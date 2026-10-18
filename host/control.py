"""VROSTBridge-owned SteamVR lifecycle and pinned ALVR control protocol.

Protocol audited against ALVR f5023f8e78fa14ea57fb1038548d7820289a8524:
  alvr/server_core/src/web_server.rs (routes and X-ALVR header)
  alvr/packets/src/lib.rs (ClientConnectionsAction JSON representation)
  alvr/server_io/src/lib.rs (client changes synchronously persist session.json)
  alvr/server_core/src/connection.rs (negotiation changes restart_settings_hash)
  alvr/dashboard/src/steamvr_launcher/mod.rs (direct vrstartup.exe launch)

GET /api/session/ emits a websocket event, NOT a JSON HTTP response. We read the
owned session file, with bounded polling and stale-state handling, instead.
"""
import math
import copy
import csv
import datetime
import io
import ipaddress
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

from host.profile import manual_client
from host.stream_options import init_outcome
from host.processes import identify, terminate


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The local host service attempted an unexpected redirect.')


class AlvrApi:
    def __init__(self, port, timeout=1.5):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Invalid host control port in the session profile.')
        self.base = 'http://127.0.0.1:' + str(port)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, endpoint, payload=None, post=False):
        if endpoint not in ('ping', 'version', 'session/client-connections', 'steamvr/shutdown', 'session/values', 'buttons'):
            raise ValueError('Unsupported local host command')
        data = json.dumps(payload).encode('utf-8') if payload is not None else (b'' if post else None)
        request = urllib.request.Request(self.base + '/api/' + endpoint, data=data,
                                         headers={'X-ALVR': 'true', 'Content-Type': 'application/json'},
                                         method='POST' if post else 'GET')
        with self.opener.open(request, timeout=self.timeout) as response:
            return response.read(4097).decode('utf-8')

    def client(self, hostname, action):
        return self.request('session/client-connections', [hostname, action], post=True)


def windows_process_ids(name='vrserver.exe'):
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + name, '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, timeout=5,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError('Windows could not check whether SteamVR is running.')
    return {int(row[1]) for row in csv.reader(io.StringIO(result.stdout))
            if len(row) > 1 and row[0].lower() == name.lower() and row[1].isdigit()}


def windows_runtime_process_ids():
    """One process snapshot for the runtime family, independent of PID order."""
    names=('vrserver.exe','vrcompositor.exe','vrmonitor.exe','vrstartup.exe')
    result=subprocess.run(['tasklist','/FI','IMAGENAME eq vr*','/FO','CSV','/NH'],capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise OSError('Windows could not inspect the SteamVR runtime family.')
    rows={name:set() for name in names}
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row)>1 and row[0].lower() in rows and row[1].isdigit():rows[row[0].lower()].add(int(row[1]))
    return rows


def require_home_network(local_address=None):
    """Read whether this PC has a network a headset PC can reach, without modifying anything.

    Without an address: at least one network must be connected. Windows' own category for it (Private or Public)
    does not matter: until 1.0.0.14 a network Windows called Public was refused, which stopped sessions whenever
    Windows decided a home network was new, and asked people to change a Windows setting they never otherwise need.
    The firewall rules admit only the local subnet, on any network, and a headset PC still has to be paired before
    it can start anything (launcher/session_link.py).
    With the local address a client actually reached: it must be a home-network (private IPv4) address of this PC.
    """
    if local_address is not None:
        address=ipaddress.IPv4Address(local_address)
        if address.is_loopback or address.is_unspecified or address.is_multicast or not address.is_private:
            raise ValueError('The headset client did not arrive on a home-network address.')
        return [{'address':str(address)}]
    command = "@(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne 'Disconnected' -or $_.IPv6Connectivity -ne 'Disconnected' } | Select-Object Name,NetworkCategory) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell','-NoProfile','-Command',command], capture_output=True,
                            text=True, timeout=12, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Windows could not say whether this PC is connected to a network. Save a report or turn automatic connection off.')
    try:
        profiles=json.loads(result.stdout or '[]')
        if isinstance(profiles,dict):profiles=[profiles]
        rows=[{'name':str(profile.get('Name','')), 'category':profile['NetworkCategory']} for profile in profiles]
    except (ValueError,KeyError,TypeError,AttributeError):
        raise ValueError('Windows returned an unreadable network profile. Turn automatic connection off to use manual pairing.')
    if not rows:
        raise ValueError('This PC is not connected to a network yet.')
    return rows


def pairing_network_private(local_address):
    """1.0.0.14: automatic pairing of a NEW headset PC still needs the adapter it arrived on to be a network Windows
    classes as Private (or a domain network). Paired headset PCs start sessions on any network type; this only keeps a
    stranger on a cafe or hotel network from pairing itself while automatic connection is on. Raises ValueError."""
    address=ipaddress.IPv4Address(local_address)
    command=("$index=@(Get-NetIPAddress -AddressFamily IPv4 -IPAddress '"+str(address)+"' -ErrorAction Stop | "
             "Select-Object -ExpandProperty InterfaceIndex); @(Get-NetConnectionProfile -InterfaceIndex $index | "
             "Select-Object Name,NetworkCategory) | ConvertTo-Json -Compress")
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,timeout=12,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Windows network type could not be checked for a new headset PC.')
    try:
        profiles=json.loads(result.stdout or '[]')
        if isinstance(profiles,dict):profiles=[profiles]
        categories=[profile['NetworkCategory'] for profile in profiles]
    except (ValueError,KeyError,TypeError,AttributeError):
        raise ValueError('Windows returned an unreadable network profile.')
    # PowerShell serializes NetworkCategory as its enum number: Public=0, Private=1, DomainAuthenticated=2.
    if not categories or not all(value in (1,2,'Private','DomainAuthenticated') for value in categories):
        name=next((str(p.get('Name','')) for p in profiles if p.get('NetworkCategory') not in (1,2,'Private','DomainAuthenticated')),'')
        raise ValueError('A new headset PC pairs automatically only on a network Windows classes as Private'+
                         (' ("'+name[:80]+'" is Public)' if name else '')+'. Set it to Private once to pair; '
                         'headset PCs already paired work on any network.')
    return True


def windows():
    """Process identities can be verified (Windows); tests replace this."""
    return os.name == 'nt'


def steamvr_startup():
    registry = Path(os.environ['LOCALAPPDATA']) / 'openvr/openvrpaths.vrpath'
    data = json.loads(registry.read_text(encoding='utf-8-sig'))
    for runtime in data.get('runtime', []):
        executable = Path(runtime) / 'bin/win64/vrstartup.exe'
        if executable.is_file():
            return executable
    raise ValueError('SteamVR was not found. Install and open SteamVR once on this PC, then retry.')


def launch_steamvr():
    executable = steamvr_startup()
    child = subprocess.Popen([str(executable)], cwd=str(executable.parent),
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # vrstartup is a short-lived launcher; its lifetime is not the session lifetime.
    threading.Thread(target=child.wait, daemon=True).start()
    return child


def quit_steamvr():
    """1.0.0.13: SteamVR's own Exit VR (vrmonitor://quit) for the runtime this host launches.

    ALVR's shutdown request asks SteamVR to quit through the driver's headset device, and that device exists only
    once a headset client has streamed: before that, the request is accepted and nothing happens. The URI goes to
    the vrmonitor.exe beside the vrstartup.exe this host runs, as the headset PC's Windows client does."""
    program = steamvr_startup().parent / 'vrmonitor.exe'
    if not program.is_file():
        raise ValueError('vrmonitor.exe was not found beside vrstartup.exe.')
    child = subprocess.Popen([str(program), 'vrmonitor://quit'], cwd=str(program.parent),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    threading.Thread(target=child.wait, daemon=True).start()


def client_rows(document, live=True):
    rows = []
    for hostname, client in document.get('client_connections', {}).items():
        if not isinstance(hostname, str) or not isinstance(client, dict):
            continue
        rows.append({'hostname': hostname, 'display_name': str(client.get('display_name', hostname)),
                     'current_ip': client.get('current_ip'),
                     'manual_ips': list(client.get('manual_ips', [])),
                     'trusted': client.get('trusted') is True,
                     'connection_state': str(client.get('connection_state', 'Disconnected')) if live else 'Unknown'})
    return sorted(rows, key=lambda row: (row['trusted'], row['hostname']))


def installed_api_version(directory):
    """Identify the installed driver, never the mutable/restored ALVR profile.

    ALVR rewrites session.server_version, and the installer preserves that file
    across upgrades. A prior nightly's build suffix therefore cannot identify
    the DLL shipped with this release. Keep exact API equality against build
    metadata, including prerelease/build suffixes, for startup and shutdown.
    """
    directory = Path(directory)
    manifest = directory / 'vrostbridge-build.json'
    try:
        document = json.loads(manifest.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        # Unbuilt source trees and native-free test fixtures use the checked-in
        # defaults. A packaged/built DLL must always have matching metadata.
        if (directory / 'bin/win64/driver_alvr_server.dll').exists():
            raise ValueError('Host driver build metadata is missing. Repair or reinstall VROSTBridge Host.')
        try:
            defaults = json.loads(Path(__file__).with_name('default-session.json').read_text(encoding='utf-8-sig'))
            version = defaults.get('server_version') if isinstance(defaults, dict) else None
        except (OSError, ValueError):
            raise ValueError('The source host API version is unavailable. Rebuild or repair VROSTBridge Host.') from None
    except (OSError, ValueError):
        raise ValueError('Host driver build metadata is unreadable. Repair or reinstall VROSTBridge Host.') from None
    else:
        version = document.get('api_version') if isinstance(document, dict) else None
    if (not isinstance(version, str) or len(version) > 128 or
            not re.fullmatch(r'(?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)'
                             r'(?:-[0-9A-Za-z-]+(?:[.][0-9A-Za-z-]+)*)?'
                             r'(?:[+][0-9A-Za-z-]+(?:[.][0-9A-Za-z-]+)*)?', version)):
        raise ValueError('Host driver API version metadata is invalid. Repair or reinstall VROSTBridge Host.')
    return version


def steamvr_server_logs():
    """Read only the log directories registered by the installed OpenVR runtime."""
    if os.name != 'nt':
        return []
    registry = Path(os.environ.get('LOCALAPPDATA', '')) / 'openvr/openvrpaths.vrpath'
    try:
        roots = json.loads(registry.read_text(encoding='utf-8-sig')).get('log', [])
        return [Path(root) / 'vrserver.txt' for root in roots[:4] if isinstance(root, str)]
    except (OSError, ValueError, TypeError):
        return []


class SteamShutdownObserver:
    """A bounded, current-launch cursor; old quit messages never classify a crash.

    SteamVR can rotate/truncate its log while starting. File identity plus the
    bytes immediately before the cursor detect replacement and truncation even
    when the new file has already grown past the old offset. Timestamps are a
    second guard when a rotated/recreated file is scanned from its beginning.
    """
    MAX_READ = 64 * 1024
    _line = re.compile(r'^\w{3} ([A-Za-z]{3}) (\d{1,2}) (\d{4}) (\d{2}):(\d{2}):(\d{2})(?:[.](\d+))?.*?Process \d+: steam has initiated a quit all(?:\s|$)', re.I)
    _months = {name: index + 1 for index, name in enumerate(
        ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}

    def __init__(self, paths=steamvr_server_logs, wall_clock=time.time):
        self.paths = paths
        self.wall_clock = wall_clock
        self.cursors = {}
        self.started_at = 0
        self.request = None

    @staticmethod
    def _guard(file, offset):
        file.seek(max(0, offset - 64))
        return file.read(min(offset, 64))

    def reset(self):
        self.started_at = self.wall_clock()
        self.request = None
        self.cursors = {}
        for path in self.paths():
            try:
                with path.open('rb') as file:
                    stat = os.fstat(file.fileno())
                    self.cursors[path] = ((stat.st_dev, stat.st_ino), stat.st_size,
                                          self._guard(file, stat.st_size), b'')
            except OSError:
                self.cursors[path] = (None, 0, b'', b'')

    def poll(self):
        for path, cursor in list(self.cursors.items()):
            try:
                with path.open('rb') as file:
                    stat = os.fstat(file.fileno())
                    identity, offset, guard, pending = cursor
                    current = (stat.st_dev, stat.st_ino)
                    if identity != current or stat.st_size < offset or self._guard(file, offset) != guard:
                        offset, pending = 0, b''
                    file.seek(offset)
                    raw = file.read(self.MAX_READ)
                    offset += len(raw)
                    # Split only complete lines, including a message appended in
                    # two writes. Bound malformed partial records as well.
                    lines = (pending + raw).split(b'\n')
                    pending = lines.pop()[-4096:]
                    self.cursors[path] = (current, offset, self._guard(file, offset), pending)
                for raw_line in lines:
                    line = raw_line.decode('utf-8', errors='replace')
                    match = self._line.match(line)
                    if not match:
                        continue
                    month, day, year, hour, minute, second, fraction = match.groups()
                    stamp = datetime.datetime(int(year), self._months[month.lower()], int(day),
                                              int(hour), int(minute), int(second),
                                              int(((fraction or '') + '000000')[:6])).timestamp()
                    now = self.wall_clock()
                    if self.started_at - .002 <= stamp <= now + 5:
                        self.request = {'reason': 'steam-quit', 'requested_utc': stamp,
                                        'observed_utc': now, 'source': 'vrserver.txt',
                                        'message': 'Steam requested that the host VR session close.'}
            except (OSError, ValueError, KeyError, OverflowError):
                continue
        return copy.deepcopy(self.request)


class HostControl:
    STARTUP_TIMEOUT = 90
    SHUTDOWN_TIMEOUT = 20
    # A negotiated restart: ALVR has saved the new headset configuration and asked to
    # exit, and its API may already be gone. Recover owned processes after this long.
    RESTART_SHUTDOWN_TIMEOUT = 6
    # Stream recovery: SteamVR is asked to close gracefully first.
    RECOVERY_SHUTDOWN_TIMEOUT = 10
    RUNTIME_LOSS_GRACE = 10
    SLOW_START_S = 30  # 1.0.0.3: say once why SteamVR's start is taking this long
    SHUTDOWN_QUIET_SECONDS = 1.0
    # 1.0.0.13: SteamVR still running this long after ALVR accepted the shutdown (or after its server appeared, when
    # the driver's API did not answer) is asked to exit as its own Exit VR does (quit_steamvr).
    QUIT_FALLBACK_S = 5.0
    MAX_NEGOTIATION_RESTARTS = 3
    # Patch 0015 saves session.json just before its startup record; wait this long for the
    # record before treating a changed hash as a restart request.
    INIT_RECORD_GRACE = 5.0

    def __init__(self, directory, log, launch=launch_steamvr, process_ids=windows_process_ids, quit=quit_steamvr):
        self.directory = Path(directory)
        self.log = log
        self.launch = launch
        self.process_ids = process_ids
        self.quit = quit
        self.lock = threading.RLock()
        self.playspace_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.thread = None
        self.api = None
        self.active = False
        self.owned_pids = set()
        self.runtime_observed=False
        self.loss_pending=False  # the monitor is waiting out a runtime loss (a new server then joins the session)
        self.identities={}
        self.launch_created=0
        self.launch_deadline=0
        self.launch_process=None
        self.launch_generation=0
        self.launch_started=0
        self.restart_pending=False
        self.shutdown_observer=SteamShutdownObserver()
        # recovery(rows) -> reason or '': stream recovery, called while the runtime is ready.
        self.recovery=None
        self.init_nonce='';self.init_record_active=False;self.init_adopted=False;self.hash_changed_at=None;self.client_states={}
        self.last_good_session = {}
        self.status_data = {'available': False, 'runtime_state': 'stopped',
                            'message': 'Start the host to find and approve a headset client.',
                            'error': '', 'clients': [], 'api_version': None, 'session_stale': False,
                            'negotiation_restarts': 0, 'last_api_ok': None, 'auto_connect': False, 'playspace': None,
                            'runtime_phase':'idle', 'launch_generation':0, 'launch_elapsed_seconds':0,
                            'shutdown_reason':'', 'shutdown_request':None, 'recovery_restarts':0, 'startup_config':None}

    def snapshot(self):
        with self.lock:
            result=copy.deepcopy(self.status_data)
            result['launch_elapsed_seconds']=round(max(0,time.monotonic()-self.launch_started),1) if self.launch_started else 0
            return result

    def _set(self, **values):
        with self.lock:
            self.status_data.update(values)

    def _read(self):
        document = json.loads((self.directory / 'session.json').read_text(encoding='utf-8-sig'))
        if not isinstance(document,dict) or not isinstance(document.get('client_connections'), dict):
            raise ValueError('The host client list is invalid.')
        self.last_good_session = document
        return document

    def start(self, init_nonce=''):
        """Launch SteamVR. init_nonce identifies this launch's display hint (patch 0015), if written."""
        if self.active:
            raise ValueError('The host session is already running.')
        if self.thread and self.thread.is_alive():
            raise ValueError('The previous host session is still stopping.')
        if self.process_ids():
            raise ValueError('Close SteamVR on this host PC before starting VROSTBridge.')
        if os.name=='nt':
            expected=steamvr_startup().parent
            for name,pids in windows_runtime_process_ids().items():
                for pid in pids:
                    item=identify(pid)
                    if item and Path(item['path'])==expected/name:
                        raise ValueError('A previous SteamVR '+name+' is still open on the host. Close SteamVR fully before starting a new session.')
        self.restart_pending=False;self.launch_process=None
        document = self._read()
        self.api = AlvrApi(document['session_settings']['connection']['web_server_port'])
        try:
            self.api.request('ping')
        except (OSError, ValueError):
            pass
        else:
            raise ValueError('Another host service is using this control port. Close its SteamVR session first.')
        self.expected_version = installed_api_version(self.directory)
        self.restart_hash = document.get('restart_settings_hash')
        self.init_nonce=init_nonce if isinstance(init_nonce,str) else ''
        # The startup record describes only this first launch; relaunches carry no hint.
        self.init_record_active=bool(self.init_nonce);self.init_adopted=False;self.hash_changed_at=None;self.client_states={}
        self._set(auto_connect=bool(document['session_settings']['connection'].get('client_discovery',{}).get('content',{}).get('auto_trust_clients',False)))
        self.owned_pids = set()
        self.runtime_observed=False
        self.launch_deadline=time.monotonic()+self.STARTUP_TIMEOUT
        self.launch_created=int((time.time()+11644473600)*10000000)
        self.identities={}
        self.stop_requested.clear()
        self.active = True
        self._set(available=False, runtime_state='starting', message='Starting SteamVR and the host connection service.',
                  error='', clients=client_rows(document, False), api_version=None, session_stale=False,
                  negotiation_restarts=0, last_api_ok=None, playspace=None, shutdown_reason='', shutdown_request=None,
                  recovery_restarts=0, startup_config=None)
        try:
            self._launch_runtime()
        except BaseException:
            self.active = False
            self._set(runtime_state='error', error='SteamVR could not be launched.', message='SteamVR could not be launched.')
            raise
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def _rotate_runtime_log(self):
        """The driver rewrites session_log.txt at each load; keep the previous load's log for reports."""
        source=self.directory/'session_log.txt'
        try:
            if source.is_file() and source.stat().st_size:os.replace(source,self.directory/'session_log.previous.txt')
        except OSError:pass

    def _launch_runtime(self):
        self._rotate_runtime_log()
        self.launch_generation+=1
        self.launch_started=time.monotonic()
        self.launch_deadline=self.launch_started+self.STARTUP_TIMEOUT
        self.launch_created=int((time.time()+11644473600)*10000000)
        self.shutdown_observer.reset()
        self._set(runtime_phase='launching',launch_generation=self.launch_generation, shutdown_reason='', shutdown_request=None)
        self.log('Host runtime launch '+str(self.launch_generation)+': waiting up to '+str(self.STARTUP_TIMEOUT)+' seconds for the SteamVR driver.')
        self.launch_process=self.launch()

    def _launcher_alive(self):
        return self.launch_process is not None and self.launch_process.poll() is None

    def _failure(self, message):
        self._set(available=False, runtime_state='error', runtime_phase='error', message=message, error=message,
                  clients=client_rows(self.last_good_session, False), shutdown_reason='failure')
        self.log(message)

    def _capture_identities(self,pids):
        if not windows():return
        expected=steamvr_startup().parent
        family=windows_runtime_process_ids()
        for name in ('vrserver.exe','vrcompositor.exe','vrmonitor.exe','vrstartup.exe'):
            candidates=pids if name=='vrserver.exe' else family.get(name,set())
            for pid in candidates:
                item=identify(pid)
                previous=self.identities.get(pid)
                if previous is not None and item is not None and item!=previous:
                    raise ValueError('SteamVR process identity changed. The replacement runtime was not stopped.')
                if item and Path(item['path'])==expected/name and item['created']>=self.launch_created-10000000:
                    self.identities.setdefault(pid,item)

    def _adopt_new_servers(self,pids):
        """New vrserver processes of this session's SteamVR join the session (1.0.0.3). Returns those that cannot.

        SteamVR can start another vrserver while its first one is still running or restarting: in a 1.0.0.2 report a
        slow start (61.9 s) was followed 3 s after ready by a new vrserver. Up to 1.0.0.2 any new vrserver ended the
        session with "A different SteamVR process appeared", and Stop then refused to close SteamVR ("ownership
        changed"), so it had to be closed on the host. A new one is ours when it is SteamVR's own vrserver.exe (the
        runtime this session launched), started after this launch, while a process of this session's SteamVR still
        runs or while this session waits out a runtime loss. A SteamVR started after ours had fully ended is not."""
        new=pids-self.owned_pids
        if not new or not windows():return new
        try:expected=steamvr_startup().parent/'vrserver.exe'
        except (OSError,ValueError,KeyError):return new
        if not self._remaining_owned() and not self.loss_pending:return new
        foreign=set()
        for pid in sorted(new):
            item=identify(pid)
            if item and Path(item['path'])==expected and item['created']>=self.launch_created-10000000:
                self.identities.setdefault(pid,item);self.owned_pids.add(pid)
                self.log('SteamVR started another server process (PID '+str(pid)+') while this session\'s SteamVR was running; it belongs to this session.')
            else:foreign.add(pid)
        return foreign

    def _remaining_owned(self):
        # vrserver can exit before vrcompositor. Keep the latter in the session
        # until its exact process identity exits, otherwise the next vrstartup
        # can collide with the previous compositor and its IPC resources.
        return [item for item in self.identities.values() if identify(item['pid'])==item]

    def _force_owned(self):
        targets=self._remaining_owned()
        if not targets:raise ValueError('SteamVR did not finish stopping; no verified process identity was available for recovery.')
        self.log('Graceful shutdown timed out; stopping only verified SteamVR processes from this VROSTBridge session: '+str([i['pid'] for i in targets]))
        for item in sorted(targets,key=lambda i:Path(i['path']).name!='vrserver.exe'):
            if identify(item['pid'])==item:terminate(item)
        if self.process_ids():raise ValueError('Another SteamVR process remains. It was not stopped.')
        self.owned_pids=set();self.identities={}

    def _shutdown_runtime(self, timeout=None):
        # An accepted shutdown HTTP response is not process termination. Track
        # server, compositor, monitor and bootstrap, then require a quiet window.
        # 1.0.0.13: a stop while SteamVR is still starting no longer waits out the rest of the launch budget
        # (STARTUP_TIMEOUT; a stop 0.75 s into a start waited 88 s): the window counts from now, and again from when
        # the server appears. ALVR's request ends SteamVR only once a headset has streamed (quit_steamvr), so SteamVR's
        # own Exit VR follows when it still runs QUIT_FALLBACK_S later. A negotiated restart is left to ALVR.
        window=timeout or self.SHUTDOWN_TIMEOUT
        deadline=time.monotonic()+window
        awaiting_server=not self.runtime_observed and not self.restart_pending
        requested=False;quiet_since=None;forced=False;can_finish=self.runtime_observed or self.restart_pending
        quit_after=None;quit_sent=self.restart_pending
        self._set(runtime_phase='stopping-runtime')
        while True:
            now=time.monotonic();pids=self.process_ids()
            if self.runtime_observed and self._adopt_new_servers(pids):
                raise ValueError('SteamVR ownership changed. Close SteamVR on the host PC before retrying.')
            if pids:
                if awaiting_server:deadline=max(deadline,now+window);awaiting_server=False
                if quit_after is None:quit_after=now+self.QUIT_FALLBACK_S
                self.owned_pids=pids;self.runtime_observed=True;can_finish=True
            self._capture_identities(pids)
            remaining=self._remaining_owned()
            empty=not pids and not remaining and not self._launcher_alive()
            if empty and (can_finish or now>=deadline):
                quiet_since=now if quiet_since is None else quiet_since
                if now-quiet_since>=self.SHUTDOWN_QUIET_SECONDS:
                    self.owned_pids=set();self.identities={};self.launch_process=None
                    self.log('Host runtime fully stopped; startup, server, compositor and monitor are quiet.')
                    return
            else:quiet_since=None
            if not requested and not empty:
                try:
                    version=self.api.request('version').strip()
                    if version!=self.expected_version:raise ValueError('The running host version changed. Its runtime was not stopped.')
                    self.api.request('steamvr/shutdown',post=True);requested=True
                    quit_after=max(quit_after or now,now+self.QUIT_FALLBACK_S)
                    self.log('Host graceful shutdown accepted; waiting for process exit.')
                except OSError:pass  # A negotiated restart can remove the API first.
            if pids and not quit_sent and quit_after is not None and now>=quit_after:
                quit_sent=True
                try:
                    self.quit()
                    self.log('SteamVR was still running; asking it to exit as its own Exit VR does.')
                except (OSError,ValueError,KeyError) as error:
                    self.log('SteamVR could not be asked to exit: '+str(error)[:160])
            if now>=deadline and not empty:
                if forced:raise ValueError('SteamVR did not become quiet after owned-process recovery. Save a report before retrying.')
                self._force_owned();forced=True
                # Keep observing: a late monitor/compositor must not overlap the
                # next launch even after we recovered an unresponsive process.
                deadline=time.monotonic()+self.SHUTDOWN_TIMEOUT
                can_finish=True;quiet_since=None
            time.sleep(.25)

    def _restart_negotiated(self):
        count=self.snapshot()['negotiation_restarts']+1
        if count>self.MAX_NEGOTIATION_RESTARTS:
            raise ValueError('Repeated headset configuration restarts. Save a report before retrying.')
        self._set(available=False,runtime_state='starting',runtime_phase='negotiation-restart',negotiation_restarts=count,
                  message='Applying the client display settings; SteamVR is restarting automatically.')
        self.log('Applying negotiated headset configuration; restarting owned SteamVR session.')
        return self._relaunch(self.RESTART_SHUTDOWN_TIMEOUT)

    def _restart_for_recovery(self, reason):
        count=self.snapshot().get('recovery_restarts',0)+1
        self._set(available=False,runtime_state='starting',runtime_phase='stream-recovery',recovery_restarts=count,
                  message='The stream stopped and did not come back; restarting host SteamVR to reconnect the headset.')
        self.log('Stream recovery '+str(count)+': '+reason+'. Restarting the owned host SteamVR session.')
        return self._relaunch(self.RECOVERY_SHUTDOWN_TIMEOUT)

    def _relaunch(self, shutdown_timeout):
        # A relaunched driver finds no hint; the earlier startup record no longer applies.
        self.init_record_active=False;self.hash_changed_at=None
        self.restart_pending=True
        self._shutdown_runtime(shutdown_timeout)
        if self.stop_requested.is_set():return False
        deadline=time.monotonic()+3
        while True:
            try:self.restart_hash=self._read().get('restart_settings_hash');break
            except (OSError,ValueError):
                if time.monotonic()>=deadline:raise ValueError('The negotiated headset settings could not be read after SteamVR stopped. Save a report before retrying.')
                if self.stop_requested.wait(.1):return False
        self.restart_pending=False
        self.runtime_observed=False
        self.launch_deadline=time.monotonic()+self.STARTUP_TIMEOUT
        self.launch_created=int((time.time()+11644473600)*10000000)
        self.identities={}
        self._launch_runtime()
        return True

    def _monitor(self):
        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        ready_once = False
        failure_since = None
        lost_since = None  # 1.0.0.3: when this session's runtime was lost (apart from waiting for it to start)
        slow_logged = False
        self.loss_pending = False
        try:
            while not self.stop_requested.is_set():
                pids = self.process_ids()
                if pids:
                    if self.owned_pids and self._adopt_new_servers(pids):
                        raise ValueError('A SteamVR server this session did not start appeared. Close SteamVR on the host PC, then start again.')
                    self.owned_pids = pids
                    self.runtime_observed=True
                self._capture_identities(pids)
                # ALVR can persist the negotiated hash and request driver exit
                # before the next poll. Inspect that durable restart signal
                # before classifying a missing API/server as a crash.
                document=None
                try:document=self._read()
                except (OSError,ValueError,TypeError):pass
                if (document is not None and not self.stop_requested.is_set() and
                        self._restart_requested(document)):
                    if not self._restart_negotiated():break
                    ready_once=False;failure_since=None;lost_since=None;slow_logged=False;self.loss_pending=False
                    deadline=self.launch_deadline
                    self.stop_requested.wait(.75)
                    continue
                # The negotiated restart record takes precedence. The observer
                # is rebased at each launch and can only recognize a new Steam
                # quit-all request, never a generic process-exit warning.
                shutdown_request=self.shutdown_observer.poll()
                if shutdown_request:
                    self._set(shutdown_reason='steam-quit', shutdown_request=shutdown_request,
                              available=False, runtime_state='stopping', runtime_phase='external-shutdown',
                              message='Steam closed the host VR session; stopping the bridge.', error='')
                    self.log('Steam requested host VR shutdown. Ending the bridge session normally.')
                    self.stop_requested.set()
                    break
                try:
                    version = self.api.request('version').strip()
                    if version != self.expected_version:
                        raise ValueError('Host driver version mismatch: expected ' + self.expected_version + ', received ' + version)
                    self.api.request('ping')
                    if not pids:
                        raise OSError('Waiting for the SteamVR process.')
                except OSError as error:
                    now=time.monotonic()
                    if failure_since is None:failure_since=now
                    # 1.0.0.3: a runtime that is still starting is not "interrupted" (1.0.0.2 said so as soon as
                    # vrserver appeared, before the driver had loaded). Lost: it was ready, or its server exited.
                    lost=ready_once or (self.runtime_observed and not pids)
                    if lost and lost_since is None:
                        lost_since=now;self.loss_pending=True
                        self.log('Host runtime interrupted'+('' if ready_once else ' while starting')+'; allowing '+str(self.RUNTIME_LOSS_GRACE)+
                                 ' seconds for the negotiated restart record or service recovery.')
                    elif not lost and lost_since is not None:
                        lost_since=None;self.loss_pending=False  # its server is back; waiting for the driver again
                    if not ready_once and not lost and not slow_logged and now-self.launch_started>=self.SLOW_START_S:
                        slow_logged=True
                        self.log('SteamVR has been starting for '+str(round(now-self.launch_started))+' seconds without loading the VROSTBridge driver yet. '
                                 'SteamVR loads its other add-ons first; one that waits for its own headset can hold it up. Waiting up to '+str(self.STARTUP_TIMEOUT)+' seconds.')
                    self._set(available=False, runtime_state='starting', runtime_phase='recovering' if lost else 'launching', session_stale=True,
                              message='Waiting for SteamVR startup or its negotiated headset restart to finish.',
                              clients=client_rows(self.last_good_session, False))
                    if now >= deadline and not ready_once:
                        raise ValueError('SteamVR started without a responding VROSTBridge driver. Check SteamVR add-ons and save a report. ' + str(error))
                    if lost and now-lost_since>=self.RUNTIME_LOSS_GRACE:
                        reason='SteamVR exited without a negotiated restart record.' if not pids else 'The host connection service did not recover.'
                        raise ValueError(reason+' Save a report to inspect the host failure, then retry.')
                    self.stop_requested.wait(.75)
                    continue
                if not ready_once or failure_since is not None:
                    self.log('Host runtime launch '+str(self.launch_generation)+' ready after '+str(round(time.monotonic()-self.launch_started,2))+' seconds.')
                    if not ready_once:self._report_startup_config()
                failure_since = None;lost_since = None;self.loss_pending = False
                ready_once = True
                try:
                    if document is None:document = self._read()
                except (OSError, ValueError, TypeError) as error:
                    self._set(available=True, session_stale=True, message='Refreshing the host client list.',
                              clients=client_rows(self.last_good_session, False), last_api_ok=time.time())
                    self.stop_requested.wait(.5)
                    continue
                rows = client_rows(document)
                self._journal_client_states(rows)
                self._set(available=True, runtime_state='ready', runtime_phase='ready', error='', api_version=version,
                          message=('Client connected. Check the client page for decoded frames.' if any(r['connection_state'] in ('Streaming', 'Connected') for r in rows)
                                   else 'Connecting automatically to the headset client.' if self.snapshot()['auto_connect'] and any(not r['trusted'] for r in rows)
                                   else 'Approve your headset client below.' if any(not r['trusted'] for r in rows)
                                   else 'Waiting for the headset client to connect.'),
                          clients=rows, session_stale=False, last_api_ok=time.time())
                reason = self._recovery_reason(rows)
                if reason and not self.stop_requested.is_set():
                    if not self._restart_for_recovery(reason):break
                    ready_once=False;failure_since=None;lost_since=None;slow_logged=False;self.loss_pending=False
                    deadline=self.launch_deadline
                self.stop_requested.wait(.75)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            self._failure(str(error))
        finally:
            if self.stop_requested.is_set():
                try:
                    self._set(available=False, runtime_state='stopping', message='Stopping the host SteamVR session.')
                    self._shutdown_runtime()
                    self._set(runtime_state='stopped', runtime_phase='idle',
                              message=('Steam closed the host VR session. Start again from the headset client.'
                                       if self.snapshot().get('shutdown_reason')=='steam-quit' else 'Host session stopped.'), error='',
                              clients=client_rows(self.last_good_session, False))
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    self._failure(str(error))
            self.active = False;self.loss_pending = False
            try:self.owned_pids.intersection_update(self.process_ids())
            except (OSError,subprocess.SubprocessError):pass

    def _restart_requested(self, document):
        """True when session.json's restart hash is ALVR's request to restart for the negotiated headset.

        Without a startup display hint any change from the hash read before launch is that
        request. With one (patch 0015) the driver itself sets a hash at startup, saves
        session.json and then records that hash with this launch's nonce: from the record on,
        every other value is a restart request, including the value before this startup (a
        negotiation that reverts the hint). A change seen before its record gets a short wait.
        """
        current=document.get('restart_settings_hash')
        outcome=init_outcome(self.directory,self.init_nonce) if self.init_record_active else None
        if outcome and outcome.get('applied') is True:
            expected=outcome.get('restart_settings_hash')
            if current!=expected:
                # The document may have been read just before the driver saved it and wrote
                # the record; read it again now that the record exists.
                try:current=self._read().get('restart_settings_hash')
                except (OSError,ValueError,TypeError,AttributeError):return False
                if current!=expected:return True
            if not self.init_adopted:
                self.init_adopted=True;self.hash_changed_at=None
                if current!=self.restart_hash:
                    self.log('Host headset configuration prepared for the client display at SteamVR start; no restart needed.')
                self.restart_hash=current
            return False
        if current==self.restart_hash:
            self.hash_changed_at=None
            return False
        if self.init_record_active and outcome is None:
            now=time.monotonic()
            if self.hash_changed_at is None:self.hash_changed_at=now
            if now-self.hash_changed_at<self.INIT_RECORD_GRACE:return False
        return True

    def _report_startup_config(self):
        if not self.init_record_active or self.snapshot().get('startup_config') is not None:return  # first launch, once
        outcome=init_outcome(self.directory,self.init_nonce)
        if not outcome:return
        summary={key:outcome.get(key) for key in ('applied','changed','eye_resolution','refresh_rate')}
        self._set(startup_config=summary)
        if outcome.get('applied') is True:
            eye=outcome.get('eye_resolution') or ['?','?']
            self.log('Startup headset configuration: '+str(eye[0])+' x '+str(eye[1])+' per eye at '+str(outcome.get('refresh_rate'))+' Hz ('+
                     ('updated for this client' if outcome.get('changed') else 'unchanged since the last session')+').')
        else:
            self.log('Startup headset configuration hint was not usable; ALVR negotiates and may restart SteamVR.')

    def _journal_client_states(self, rows):
        """Log each ALVR client connection-state change (Connecting, Streaming, Disconnecting, ...)."""
        for row in rows:
            hostname,state=row.get('hostname'),row.get('connection_state')
            previous=self.client_states.get(hostname)
            if previous!=state:
                self.client_states[hostname]=state
                if previous is not None or state not in (None,'Disconnected'):
                    self.log('ALVR client '+str(hostname)+': '+str(previous or 'not listed')+' -> '+str(state))

    def _recovery_reason(self, rows):
        if not self.recovery:return ''
        try:return str(self.recovery(rows) or '')
        except Exception as error:
            self.log('Stream recovery check failed: '+str(error)[:200])
            return ''

    def set_client_address(self, hostname, address, previous=None, trust=False):
        """Add the session client's address to the running ALVR's reconnection list, replacing the one set before."""
        rows = self._ready()
        validated = manual_client({'client_connections': {}}, hostname, address)
        address = validated['client_connections'][hostname]['manual_ips'][0]
        existing = rows.get(hostname, {}).get('manual_ips', [])
        ips = [address] + sorted(set(existing) - {address, previous})
        if hostname not in rows:
            self.api.client(hostname, {'AddIfMissing': {'trusted': bool(trust), 'manual_ips': ips}})
        self.api.client(hostname, {'SetManualIps': ips})
        return ips

    def stop(self):
        if not self.active:
            # A failed monitor can leave our SteamVR alive. Retry a graceful shutdown only
            # when it is still the exact process observed during this session.
            # Preserve compositor recovery even after the server has exited and
            # its PID was removed from owned_pids by the monitor's finalizer.
            if self.api is not None and (self.owned_pids or self.identities):
                self._shutdown_runtime()
                self._set(available=False, runtime_state='stopped', runtime_phase='idle', error='', message='Host session stopped.')
            return
        self.stop_requested.set()
        if self.thread:
            self.thread.join(self.STARTUP_TIMEOUT + self.SHUTDOWN_TIMEOUT + 5)
        if self.thread and self.thread.is_alive():
            raise ValueError('Host shutdown is still in progress. Wait a few seconds before retrying.')
        if self.snapshot()['runtime_state'] == 'error':
            raise ValueError(self.snapshot()['error'])

    def _ready(self):
        status = self.snapshot()
        if not self.active or not status['available'] or status['runtime_state'] != 'ready' or status['session_stale']:
            raise ValueError('Wait until the host connection service is ready.')
        return {row['hostname']: row for row in status['clients']}

    def trust(self, hostname):
        rows = self._ready()
        if not isinstance(hostname, str) or hostname not in rows:
            raise ValueError('That client is no longer in the discovered client list.')
        self.api.client(hostname, 'Trust')
        self.log('Client access approved: ' + hostname)

    def add(self, hostname, address):
        rows = self._ready()
        # Reuse the strict hostname and LAN-address validation before any API request.
        validated = manual_client({'client_connections': {}}, hostname, address)
        address = validated['client_connections'][hostname]['manual_ips'][0]
        self.api.client(hostname, {'AddIfMissing': {'trusted': False, 'manual_ips': [address]}})
        manual_ips = sorted(set(rows.get(hostname, {}).get('manual_ips', [])) | {address})
        self.api.client(hostname, {'SetManualIps': manual_ips})
        self.api.client(hostname, 'Trust')
        self.log('Manual client access approved: ' + hostname + ' at ' + address)

    def forget(self, hostname):
        rows = self._ready()
        if not isinstance(hostname, str) or hostname not in rows:
            raise ValueError('That client is no longer in the client list.')
        if self.snapshot()['auto_connect']:
            self.api.request('session/values', [{'path':[{'Name':part} for part in ('session_settings','connection','client_discovery','content','auto_trust_clients')], 'value':False}], post=True)
            self._set(auto_connect=False)
        self.api.client(hostname, 'RemoveEntry')
        self.log('Client access removed: ' + hostname)

    BITRATE_PATH = ('session_settings', 'video', 'bitrate', 'mode', 'ConstantMbps')

    def set_bitrate(self, mbps):
        """1.0.0.8: set the running stream's bitrate (whole Mbit/s, 10-300) for the headset client's bitrate
        governor. ALVR reads its bitrate settings for every frame and the encoder takes a new target at once;
        the session file keeps the value only until the next session's profile is written."""
        if type(mbps) is not int or not 10 <= mbps <= 300:
            raise ValueError('A stream bitrate is a whole number from 10 to 300 Mbit/s.')
        status = self.snapshot()
        if not self.active or status['runtime_state'] != 'ready':
            raise ValueError('The host stream is not running.')
        self.api.request('session/values', [{'path': [{'Name': part} for part in self.BITRATE_PATH], 'value': mbps}],
                         post=True)
        self.log('VROST_BITRATE set=' + str(mbps) + ' Mbit/s (the headset client asked)')
        return mbps

    def menu(self):
        rows=self._ready()
        if not any(r['connection_state'] in ('Streaming','Connected') for r in rows.values()):raise ValueError('Connect the headset client first.')
        path='/user/hand/left/input/system/click'
        self.api.request('buttons',[{'path':path,'value':{'Binary':True}}],post=True)
        def release():
            try:
                if self.active:self.api.request('buttons',[{'path':path,'value':{'Binary':False}}],post=True)
            except (OSError,ValueError) as error:self.log('Host menu release: '+str(error))
        timer=threading.Timer(.15,release);timer.daemon=True;timer.start()
        self.log('Host system-menu button requested.')

    def apply_playspace(self,area):
        if not isinstance(area,dict):raise ValueError('Client play-area dimensions are invalid.')
        w,d=area.get('width'),area.get('depth')
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0<v<=100 for v in (w,d)):
            raise ValueError('Client play-area dimensions are invalid.')
        # Reconnects can change native chaperone state. Let the helper verify the
        # live rectangle rather than trusting a cached success in this process.
        with self.playspace_lock:
            self._ready()
            generation=self.launch_generation
            previous=self.snapshot().get('playspace') or {}
            helper=self.directory.parent.parent/'host/VROSTBridgeVRHelper.exe'
            if not helper.is_file():raise ValueError('Host play-area helper missing; repair the host package.')
            state=Path(os.environ['LOCALAPPDATA'])/'VROSTBridge';state.mkdir(parents=True,exist_ok=True)
            backup=state/('host-chaperone-before-'+str(time.time_ns())+'.json')
            result=subprocess.run([str(helper),str(self.directory/'bin/win64/openvr_api.dll'),str(w),str(d),str(backup)],capture_output=True,text=True,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ValueError('Host play-area sync: '+(result.stderr or result.stdout).strip())
            value=json.loads(result.stdout)
            if (value.get('applied') is not True or not isinstance(value.get('changed'),bool) or
                value.get('boundary')!='rectangle' or
                not all(isinstance(value.get(key),(int,float)) and not isinstance(value.get(key),bool) and
                        math.isfinite(value[key]) and abs(value[key]-expected)<0.01
                        for key,expected in (('width',w),('depth',d)))):
                raise ValueError('Host play area was not confirmed.')
            if not self.active or generation!=self.launch_generation:
                raise ValueError('Host runtime changed during play-area verification; retry after it is ready.')
            saved=str(backup) if backup.is_file() else previous.get('backup') if previous.get('runtime_generation')==generation else None
            self._set(playspace=dict(value,received_at=time.time(),backup=saved,runtime_generation=generation))
            if value['changed'] or previous.get('runtime_generation')!=generation:
                action='applied and verified' if value['changed'] else 'verified unchanged'
                self.log('Client rectangular play area '+action+' on host: '+str(w)+' x '+str(d)+' metres. Local physical boundary stays active.')
