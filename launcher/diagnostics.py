"""Bounded local diagnostics and read-only sharing with the connected ALVR host.

The LAN endpoint has no control actions. It authorizes the IP of the native
client's established ALVR control socket, with 60 seconds to finish collecting
after disconnect. No browser-origin request or proxy forwarding is accepted.
"""
from collections import deque
from datetime import datetime, timezone
import copy
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from launcher.version import role_version

PORT=9945
LIMIT=2*1024*1024

def clean(text):
    text=str(text)
    text=re.sub(r'(?i)((?:token|authorization|password|secret)[\"\s:=]+)[^\s,\"}]+',r'\1[redacted]',text)
    return '\n'.join(line[:8192] for line in text.splitlines() if not any(k in line for k in ('\"Tracking\"','eyes_social','eyes_combined','hand_skeletons')))

class Journal:
    def __init__(self, directory, role):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/(role+'-debug.jsonl');self.session=uuid.uuid4().hex
        self.lock=threading.Lock();self.lines=deque(maxlen=1500);self.milestones=deque(maxlen=128)
        self.incidents=[];self._incident_saved=0
        self.incident_path=self.directory/(role+'-incidents.json')
        try:
            if self.incident_path.stat().st_size <= 512*1024:
                saved=json.loads(self.incident_path.read_text(encoding='utf-8')).get('incidents',[])
                if isinstance(saved,list):
                    for item in saved[-7:]:
                        if (isinstance(item,dict) and item.get('kind') in ('transport','presentation','controller')
                                and isinstance(item.get('before'),list) and isinstance(item.get('after'),list)):
                            item['capture_until']=0
                            self.incidents.append(item)
        except (OSError,ValueError,TypeError):pass
        self.record('session-open',{'role':role,'version':role_version(role),'pid':os.getpid()})
    def record(self,event,data):
        entry={'utc':datetime.now(timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),'session':self.session,'event':event,'data':clean(data) if isinstance(data,str) else data}
        raw=json.dumps(entry,ensure_ascii=True)
        trigger=self._incident_reason(event,entry['data'])
        with self.lock:
            now=time.monotonic()
            kind='controller' if trigger and 'controller' in trigger else 'presentation' if trigger and 'presentation' in trigger else 'transport'
            latest=next((item for item in reversed(self.incidents) if item['kind']==kind),None)
            new_trigger=trigger and (latest is None or latest.get('session')!=self.session or now-latest.get('started',0)> (12 if kind=='presentation' else 2))
            if new_trigger:
                before=self._bounded(list(self.lines),(48 if kind=='transport' else 16)*1024)
                self.incidents.append({'kind':kind,'reason':trigger,'session':self.session,'utc':entry['utc'],
                                       'monotonic_ns':entry['monotonic_ns'],'started':now,'capture_until':now+(30 if kind=='controller' else 12),
                                       'trigger':entry,'before':before,'after':[]})
                # Rendering hitches cannot evict a connection failure, and a
                # connection failure starts its own window during a hitch.
                for category,maximum in (('transport',3),('presentation',2),('controller',2)):
                    matching=[item for item in self.incidents if item['kind']==category]
                    for old in matching[:-maximum]:self.incidents.remove(old)
            active=[item for item in self.incidents if item.get('capture_until',0)>=now and item.get('session')==self.session]
            for incident in active:
                incident['after']=self._bounded(incident['after']+[entry],24*1024)
                if incident['kind']=='controller' and event=='log' and isinstance(data,str) and data.startswith('CONTROLLER_STATE '):
                    incident['controller_events']=self._bounded((incident.get('controller_events',[])+[entry])[-32:],8*1024)
            if active and (trigger or now-self._incident_saved>=1):
                # Independent file survives debug rotation and app restart.
                temporary=self.incident_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'session':self.session,'incidents':self.incidents}),encoding='utf-8')
                temporary.replace(self.incident_path);self._incident_saved=now
            self.lines.append(entry)
            # Preserve startup/errors apart from high-rate frame/input metrics.
            # A report after several minutes must still show handshake progress.
            if event=='session-open' or (event=='log' and isinstance(data,str) and
                not data.startswith(('METRICS ','TIMING ','INPUT ','DEBUG '))):
                self.milestones.append(entry)
            if self.path.exists() and self.path.stat().st_size>2*1024*1024:
                for i in (2,1):
                    p=Path(str(self.path)+'.'+str(i))
                    if p.exists():p.replace(str(self.path)+'.'+str(i+1))
                self.path.replace(str(self.path)+'.1')
            with self.path.open('a',encoding='utf-8') as f:f.write(raw+'\n')
    @staticmethod
    def _bounded(entries,limit):
        while entries and len(json.dumps(entries))>limit:
            entries=entries[max(1,len(entries)//8):]
        return entries

    @staticmethod
    def _incident_reason(event,data):
        if event in ('network-path-change','presentation-incident','transport-incident'):
            return event
        if event in ('log','alvr-log','alvr-log-repeated'):
            if isinstance(data,dict):data=data.get('message','')
            if not isinstance(data,str):return None
            lower=data.lower()
            if lower.startswith('controller_state ') and re.search(r'\b(?:connected|tracked)=0\b',lower):
                return 'controller-tracking-or-connection-loss'
            if 'presentation_incident ' in lower:return 'presentation-incident'
            # Sustained cadence degradation (for example half-rate) never trips
            # the per-frame stall threshold; capture its context too.
            if lower.startswith('presentation_cadence ') and '"to":"full"' not in lower and '"to":"idle"' not in lower:
                return 'presentation-cadence'
            if 'transport_incident ' in lower:return 'transport-incident'
            if any(value in lower for value in ('connection timeout','disconnected. timeout','control stalled',
                                               'control send failed','failed to initialize compositor')):
                return 'transport-or-runtime-failure'
        return None

    def report(self):
        with self.lock:events=list(self.lines);milestones=list(self.milestones);incidents=copy.deepcopy(list(self.incidents))
        while events and len(json.dumps(events))>288*1024:events=events[max(1,len(events)//8):]
        while milestones and len(json.dumps(milestones))>80*1024:milestones=milestones[max(1,len(milestones)//8):]
        return {'session':self.session,'events':events,'milestones':milestones,'incidents':incidents,'file':str(self.path),'retention':'current file plus three 2 MiB rotations; three transport, two presentation and two controller incident windows retained separately across app restarts'}

def steamvr_logs():
    bases=[Path.home()/'.local/share/Steam/logs',Path.home()/'.steam/steam/logs']
    registry=Path(os.environ.get('LOCALAPPDATA',''))/'openvr/openvrpaths.vrpath'
    if sys.platform=='win32':
        # The host and (1.0.0.7) the Windows headset client: the log folders OpenVR registered.
        try:bases=[Path(value) for value in json.loads(registry.read_text(encoding='utf-8-sig')).get('log') or [] if isinstance(value,str) and value][:4]
        except (OSError,ValueError,TypeError,AttributeError):bases=[]
    result={}
    for base in bases:
        # 1.0.0.3: also the previous SteamVR start's server log and the monitor's log: a report saved after
        # SteamVR was restarted otherwise holds only the newest start (a slow start went unexplained in 1.0.0.2).
        for name,limit in (('vrserver.txt',128),('vrcompositor.txt',128),('vrclient_VROSTBridgeClient.txt',128),
                           ('vrclient_VROSTBridgeRuntimeProbe.txt',128),('vrserver.previous.txt',64),('vrmonitor.txt',32)):
            p=base/name
            if name in result or not p.is_file():continue
            try:
                with p.open('rb') as f:
                    f.seek(max(0,p.stat().st_size-limit*1024));result[name]=clean(f.read().decode('utf-8',errors='replace'))
            except OSError:pass
    return result

def windows_control_peers(pid):
    """Windows: the peers of established TCP 9943 connections owned by OUR native PID (IP Helper's
    per-process TCP table, launcher/windows_network.py). Empty, without an error, when Windows refuses."""
    from launcher import windows_network
    try:tcp,_=windows_network.owned_sockets(pid)
    except (OSError,ValueError,TypeError):return set()
    peers=set()
    for row in tcp:
        # TCP_STATES maps Windows' MIB_TCP_STATE to /proc/net/tcp's codes: '01' is ESTABLISHED.
        if row.get('pid')!=pid or row.get('local_port')!=9943 or windows_network.TCP_STATES.get(row.get('state'))!='01':continue
        try:address=ipaddress.ip_address(row.get('peer_ip',''))
        except ValueError:continue
        if address.is_private and not address.is_loopback and address.version==4:peers.add(str(address))
    return peers

def control_peers(pid, proc=Path('/proc')):
    """Match established TCP9943 sockets to descriptors held by OUR native PID."""
    if type(pid) is not int or pid<=0:return set()
    if sys.platform=='win32':return windows_control_peers(pid)
    try:
        owned=set()
        for fd in (proc/str(pid)/'fd').iterdir():
            try:
                link=os.readlink(fd)
                if link.startswith('socket:['):owned.add(link[8:-1])
            except OSError:pass
        peers=set()
        for filename in ('tcp','tcp6'):
            path=proc/str(pid)/'net'/filename
            if not path.exists():continue
            for line in path.read_text().splitlines()[1:]:
                fields=line.split()
                if len(fields)<10 or fields[3]!='01' or fields[9] not in owned:continue
                if int(fields[1].split(':')[1],16)!=9943:continue
                raw=bytes.fromhex(fields[2].split(':')[0])
                if len(raw)==4:address=ipaddress.ip_address(raw[::-1])
                else:
                    address=ipaddress.ip_address(b''.join(raw[i:i+4][::-1] for i in range(0,16,4)))
                    address=address.ipv4_mapped or address
                if address.is_private and not address.is_loopback and address.version==4:peers.add(str(address))
        return peers
    except (OSError,ValueError):return set()

class DiagnosticServer:
    def __init__(self, pid, summary, report, log, port=PORT, session_peer=lambda:{}):
        self.pid=pid;self.summary=summary;self.report=report;self.log=log;self.session_peer=session_peer
        self.peers={};self.lock=threading.Lock();self.server=None
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.headers.get('Origin') or self.headers.get('X-VROSTBridge-Diagnostics')!='1' or not owner.allowed(self.client_address[0]):self.send_error(403);return
                if self.path not in ('/summary','/report'):self.send_error(404);return
                raw=json.dumps(owner.summary() if self.path=='/summary' else owner.report()).encode()
                if len(raw)>LIMIT:self.send_error(413);return
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        try:
            self.server=http.server.ThreadingHTTPServer(('0.0.0.0',port),Handler)
            threading.Thread(target=self.server.serve_forever,daemon=True).start()
        except OSError as e:log('Client diagnostic sharing unavailable: '+str(e))
    def allowed(self,address):
        now=time.monotonic();current=set(control_peers(self.pid()))
        peer=self.session_peer()
        if peer.get('active') and peer.get('state') in ('starting','running','stopping','disconnected'):
            try:
                ip=ipaddress.ip_address(peer.get('host_ip',''))
                if ip.version==4 and ip.is_private and not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast:current.add(str(ip))
            except ValueError:pass
        with self.lock:
            for ip in current:self.peers[ip]=now+60
            self.peers={ip:until for ip,until in self.peers.items() if until>now}
            return address in self.peers
    def close(self):
        if self.server:self.server.shutdown();self.server.server_close();self.server=None

class RemoteDiagnostics:
    def __init__(self, directory, clients, apply_space, log, session_peer=lambda:{}):
        self.directory=Path(directory);self.clients=clients;self.apply_space=apply_space;self.log=log;self.session_peer=session_peer
        self.connection_key=None;self.full_key=None;self.report_received=0;self.refused_key=None
        self.lock=threading.Lock();self.capture_lock=threading.Lock();self.stop_event=threading.Event();self.summary={};self.full={};self.last_ip=None;self.space_key=None
        self.message='Waiting for the connected client.';self.last_seen=0
        try:
            saved=self.directory/'last-client-report.json'
            if saved.stat().st_size<=LIMIT:
                value=json.loads(saved.read_text(encoding='utf-8'))
                if isinstance(value,dict) and value.get('role')=='client':
                    self.full=value;self.message='Previous client report retained; waiting for a current connection.'
        except (OSError,ValueError):pass
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.thread=threading.Thread(target=self.poll,daemon=True);self.thread.start()
    def fetch(self,ip,path):
        address=ipaddress.ip_address(ip)
        if address.version!=4 or not address.is_private or address.is_loopback:raise ValueError('Not a client LAN address')
        request=urllib.request.Request('http://'+str(address)+':'+str(PORT)+path,headers={'X-VROSTBridge-Diagnostics':'1'})
        with self.opener.open(request,timeout=3) as response:raw=response.read(LIMIT+1)
        if len(raw)>LIMIT:raise ValueError('Client report exceeds limit')
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('role')!='client':raise ValueError('Unexpected diagnostic peer')
        return value
    def candidate(self):
        peer=self.session_peer()
        if peer.get('lease_id') and peer.get('client_ip'):
            return peer['client_ip'],peer['lease_id']
        rows=[r for r in self.clients() if r.get('trusted') and r.get('connection_state') in ('Streaming','Connected') and r.get('current_ip')]
        return (rows[0]['current_ip'],'transport:'+rows[0]['current_ip']) if len(rows)==1 else (None,None)
    def use_peer(self,ip,key):
        with self.lock:
            first=key!=self.connection_key or ip!=self.last_ip
            if first:
                self.last_ip=ip;self.connection_key=key;self.summary={};self.last_seen=0;self.space_key=None
            return first
    def capture(self):
        with self.capture_lock:return self._capture()
    def _capture(self):
        ip,key=self.candidate()
        if ip:self.use_peer(ip,key)
        with self.lock:ip,key=self.last_ip,self.connection_key
        if ip:
            try:
                value=self.fetch(ip,'/report')
                with self.lock:
                    if (ip,key)!=(self.last_ip,self.connection_key):return {}
                    self.full=value;self.full_key=key;self.report_received=time.time()
                p=self.directory/'last-client-report.json';temp=p.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(p)
            except (OSError,ValueError) as e:
                # 1.0.0.3: the client shares its report only during a session and for a minute after (403 later);
                # say that once instead of an HTTP error at every retry.
                if getattr(e,'code',None)==403:
                    if self.refused_key!=key:
                        self.refused_key=key
                        self.log('Client report not collected: the headset client shares it only during a session and for a minute after. The last report collected is kept.')
                else:self.log('Client report collection: '+str(e))
        return self.report()
    def report(self):
        with self.lock:return {'message':self.message,'last_received_utc':self.last_seen,'summary':copy.deepcopy(self.summary),'client_report':copy.deepcopy(self.full),'client_report_current':bool(self.connection_key and self.full_key==self.connection_key),'client_report_received_utc':self.report_received}
    def status(self):
        with self.lock:return {'message':self.message,'last_received_utc':self.last_seen,'summary':copy.deepcopy(self.summary)}
    def poll(self):
        while not self.stop_event.is_set():
            ip,key=self.candidate()
            try:
                if ip:
                    first=self.use_peer(ip,key)
                    value=self.fetch(ip,'/summary')
                    with self.lock:self.last_ip=ip;self.summary=value;self.last_seen=time.time();self.message='Client diagnostics available; full report collected on Save report or Stop.'
                    if first or self.full_key!=key:self.capture()
                    area=value.get('playspace',{})
                    if not isinstance(area,dict):raise ValueError('Invalid client play-area summary')
                    key=(value.get('session'),area.get('sequence'))
                    if area.get('sent') and any(r.get('connection_state') in ('Streaming','Connected') for r in self.clients()) and key!=self.space_key and not self.stop_event.is_set():
                        self.apply_space(area);self.space_key=key
                elif self.last_ip:
                    self.capture()
                    with self.lock:self.last_ip=None;self.connection_key=None;self.message='Connection ended; last client report retained.'
            except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as e:
                with self.lock:self.message='Client diagnostics not yet available: '+str(e)
            self.stop_event.wait(5)
    def close(self):self.stop_event.set();self.thread.join(5)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError("Diagnostic redirects are not accepted")
