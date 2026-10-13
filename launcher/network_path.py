"""Read-only, bounded evidence for the path selected by this client session.

Windows: the native client's sockets from GetExtendedTcpTable/GetExtendedUdpTable
(by process id; Windows reports no queue sizes), the route from GetBestRoute2, the
adapter's operational state and its Wi-Fi radio from the Native Wifi API
(launcher/windows_network.py).
"""
from copy import deepcopy
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

from launcher.client_network import system_run
from launcher import windows_network

TRANSPORT_PORTS = (9943, 9944)


def _windows_sockets(pid):
    try:
        tcp, udp = windows_network.owned_sockets(pid)
    except windows_network.WindowsNetworkError:
        return []
    result = []
    for protocol, rows in (('tcp', tcp), ('udp', udp)):
        for row in rows:
            local = {'ip': row.get('local_ip', ''), 'port': row.get('local_port', 0)}
            peer = {'ip': row.get('peer_ip', '0.0.0.0'), 'port': row.get('peer_port', 0)}
            if local['port'] not in TRANSPORT_PORTS and peer['port'] not in TRANSPORT_PORTS:
                continue
            result.append({'protocol': protocol, 'local': local, 'peer': peer,
                           'state': windows_network.TCP_STATES.get(row.get('state'), '') if protocol == 'tcp' else '07',
                           'send_queue_bytes': None, 'receive_queue_bytes': None})
    return sorted(result, key=lambda item: (item['protocol'], item['local']['port'], item['peer']['ip']))


def transport_sockets(pid, proc=Path('/proc')):
    if type(pid) is not int or pid <= 0:
        return []
    if sys.platform == 'win32':
        return _windows_sockets(pid)
    try:
        owned=set()
        for fd in (proc/str(pid)/'fd').iterdir():
            try:
                target=os.readlink(fd)
                if target.startswith('socket:['):owned.add(target[8:-1])
            except OSError:pass
        result=[]
        for protocol in ('tcp','udp'):
            path=proc/str(pid)/'net'/protocol
            if not path.is_file():continue
            for line in path.read_text().splitlines()[1:]:
                row=line.split()
                if len(row)<10 or row[9] not in owned:continue
                def endpoint(raw):
                    ip,port=raw.split(':');return {'ip':str(ipaddress.IPv4Address(bytes.fromhex(ip)[::-1])),'port':int(port,16)}
                local,peer=endpoint(row[1]),endpoint(row[2])
                if local['port'] not in (9943,9944) and peer['port'] not in (9943,9944):continue
                tx,rx=(int(item,16) for item in row[4].split(':'))
                result.append({'protocol':protocol,'local':local,'peer':peer,'state':row[3],
                               'send_queue_bytes':tx,'receive_queue_bytes':rx})
        return sorted(result,key=lambda item:(item['protocol'],item['local']['port'],item['peer']['ip']))
    except (OSError,ValueError):return []


class NetworkMonitor:
    def __init__(self, selection, pid, record, runner=system_run, auto_start=True):
        self.selection,self.pid,self.record,self.runner=selection,pid,record,runner
        self.lock=threading.Lock();self.closed=threading.Event();self.wake=threading.Event();self.value={};self.key=None;self.active=False
        self.thread=None
        if auto_start:
            self.thread=threading.Thread(target=self._poll,daemon=True,name='VROSTBridgePathEvidence');self.thread.start()

    def refresh(self):
        self.wake.set()

    def snapshot(self):
        with self.lock:return deepcopy(self.value)

    def sample(self):
        selected=self.selection();source,peer=selected.get('source_ip',''),selected.get('host_ip','')
        native_pid=self.pid()
        active=type(native_pid) is int and native_pid>0
        value={'observed_monotonic':time.monotonic(),'active':active,'selected':selected,'sockets':transport_sockets(native_pid)}
        if source and peer and active and sys.platform=='win32':
            self._windows_path(value,selected,source,peer)
        elif source and peer and active:
            try:
                ipaddress.IPv4Address(source);ipaddress.IPv4Address(peer)
                command=next((str(path) for path in (Path('/usr/sbin/ip'),Path('/usr/bin/ip')) if path.is_file()),None)
                if command:
                    bound_interface=selected.get('source_interface') or selected.get('bound_interface') or next((row.get('interface') for row in selected.get('adapters',[]) if row.get('address')==source),selected.get('interface',''))
                    arguments=[command,'-j','route','get',peer,'from',source]
                    if bound_interface and re.fullmatch(r'[A-Za-z0-9_.:-]{1,63}',bound_interface):arguments+=['oif',bound_interface]
                    result=self.runner(arguments,1)
                    routes=json.loads(result.stdout) if result.returncode==0 else []
                    if routes:
                        value['route']={key:routes[0][key] for key in ('dev','gateway','prefsrc','src','dst','flags') if key in routes[0]}
                interface=value.get('route',{}).get('dev')
                if interface and re.fullmatch(r'[A-Za-z0-9_.:-]{1,63}',interface):
                    link=Path('/sys/class/net')/interface
                    value['operstate']=(link/'operstate').read_text().strip()[:32]
                    command=next((str(path) for path in (Path('/usr/sbin/iw'),Path('/usr/bin/iw')) if path.is_file()),None)
                    if command and (link/'wireless').is_dir():
                        wifi={}
                        for arguments,keys in ((['link'],('freq:','signal:','tx bitrate:','rx bitrate:')),(['get','power_save'],('Power save:',))):
                            result=self.runner([command,'dev',interface,*arguments],1)
                            for line in result.stdout.splitlines():
                                text=line.strip()
                                for key in keys:
                                    if text.startswith(key):wifi[key.rstrip(':')]=text[len(key):].strip()[:120]
                        value['wifi']=wifi
            except (OSError,ValueError,TypeError):value['route_unavailable']=True
        # Compare path identity only. Queue sizes/signal change frequently and
        # are ordinary telemetry, not hundreds of synthetic incidents.
        key=(source,peer,value.get('route',{}).get('dev'),value.get('route',{}).get('gateway'),value.get('operstate'))
        with self.lock:
            previous=self.key;was_active=self.active;self.key=key;self.value=value;self.active=active
        self.record('network-path',value)
        if was_active and active and previous is not None and previous!=key and previous[0] and source:
            self.record('network-path-change',{'previous':previous,'current':key,'snapshot':value})
        return value

    @staticmethod
    def _windows_path(value, selected, source, peer):
        """The same keys as the Linux evidence: route (dev = adapter alias), operstate and, on Wi-Fi, the radio."""
        try:
            ipaddress.IPv4Address(source);ipaddress.IPv4Address(peer)
            bound=selected.get('source_interface') or selected.get('bound_interface') or next((row.get('interface') for row in selected.get('adapters',[]) if row.get('address')==source),selected.get('interface',''))
            found=windows_network.route(peer,source,bound if windows_network.valid_alias(bound) else '')
            if found:
                value['route']={key:item for key,item in (('dev',found.get('interface')),('gateway',found.get('gateway')),
                                                          ('prefsrc',found.get('source')),('dst',peer)) if item}
            interface=value.get('route',{}).get('dev')
            adapter=windows_network.adapter_for(interface) if interface else None
            if adapter:
                value['operstate']=str(adapter.get('operstate','unknown'))[:32]
                if adapter.get('kind')=='wifi':
                    link=windows_network.wifi_link(interface,identity=False,options=True)
                    wifi={}
                    for key,name,unit in (('freq','freq_mhz',''),('channel','channel',''),('band','band',''),
                                          ('signal','signal_dbm',' dBm'),('tx bitrate','tx_mbps',' MBit/s'),
                                          ('rx bitrate','rx_mbps',' MBit/s'),('phy','phy','')):
                        if link.get(name) not in (None,''):wifi[key]=(str(link[name])+unit)[:120]
                    if link.get('multi_link'):wifi['links']=str(len(link.get('links') or []))
                    for key,name in (('streaming mode','media_streaming'),('background scan','background_scan')):
                        if isinstance(link.get(name),bool):wifi[key]='on' if link[name] else 'off'
                    if link.get('reason'):wifi['unavailable']=str(link['reason'])[:120]
                    try:wifi['Power save']=windows_network.power_mode_name(windows_network.wifi_power_saving().get('current'))
                    except windows_network.WindowsNetworkError:pass
                    value['wifi']=wifi
        except (OSError,ValueError,TypeError):value['route_unavailable']=True

    def _poll(self):
        while not self.closed.is_set():
            try:self.sample()
            except Exception as error:self.record('network-inspection-error',str(error)[:300])
            self.wake.wait(5);self.wake.clear()

    def close(self):
        self.closed.set();self.wake.set()
        if self.thread:self.thread.join(5)
