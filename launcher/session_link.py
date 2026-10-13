"""Client-owned session lifecycle on a trusted, private home LAN.

TCP/UDP 9946 is a small companion broker, not the ALVR streaming transport.
UDP only discovers hosts. The authenticated HTTP action allowlist is start,
heartbeat, stop and status; there are no URLs, paths or commands in requests.
Pairing also requires the caller's private, directly attached subnet and the
host's explicit Windows Private-network/automatic-connection policy callback.
Tokens are bearer credentials for this trusted LAN, not Internet transport.

Integration: construct SessionHost while the host page is open. Its
start(settings)/stop() callbacks run on one worker, outside the state lock,
using validated client codec/bitrate and the host's remaining configuration.
Start requests return immediately with a lease and state=starting. SessionClient.start() automatically maintains that lease; call
stop() if local runtime startup fails, on Stop and when native streaming exits.
Call close() before closing either companion. Keep the host's firewall scoped
to Private/LocalSubnet and include TCP + UDP 9946. Status never exposes tokens.
"""
import base64
import hashlib
import inspect
import http.server
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib

from launcher.client_identity import lan_addresses
from launcher.headset import headset_metadata, RESOLUTIONS
from launcher import windows_network

PORT = 9946
PROTOCOL = 'vrostbridge-session-2'
NATIVE_PROTOCOL = 'vrostbridge-native-1'
LIMIT = 8192
HEARTBEAT_SECONDS = 3
LEASE_SECONDS = 30
STOP_GRACE_SECONDS = 5
_PRIVATE = tuple(ipaddress.IPv4Network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
_ID = re.compile(r'^[0-9a-f]{32}$')
_TOKEN = re.compile(r'^[0-9a-f]{64}$')


class SessionError(ValueError):
    pass


class SessionVersionError(SessionError):
    """A reachable companion needs the matching release, not rediscovery."""


def _private(value):
    try:
        address = ipaddress.IPv4Address(value)
        return any(address in network for network in _PRIVATE)
    except (ValueError, TypeError):
        return False


def network_addresses():
    """Assigned addresses of adapters, with physical/connected flags; no DNS/Internet probe.

    Windows: launcher/windows_network.py (GetAdaptersAddresses, cached for a few seconds, as status
    reads this about once a second): the same rows as Linux, the adapter alias ("Wi-Fi 2") as
    'interface', physical = a connector is present (what Get-NetAdapter -Physical reports) and
    connected = operational status up. Only if that API cannot answer, the PowerShell query below.
    """
    if sys.platform != 'win32':
        return lan_addresses()
    try:
        return windows_network.lan_addresses()
    except windows_network.WindowsNetworkError:
        return _powershell_addresses()


def _powershell_addresses():
    """The pre-1.0.0.7 Windows query (physical adapters that are up), now with the alias as 'interface'."""
    command = ("[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
               "$physical=@(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | "
               "Select-Object -ExpandProperty ifIndex); @(Get-NetIPAddress -AddressFamily IPv4 | "
               "Where-Object { $physical -contains $_.InterfaceIndex } | "
               "Select-Object @{n='address';e={$_.IPAddress}},@{n='interface';e={[string]$_.InterfaceAlias}},"
               "@{n='prefix';e={[int]$_.PrefixLength}},"
               "@{n='physical';e={$true}},@{n='connected';e={$true}}) | ConvertTo-Json -Compress")
    try:
        from host.power import powershell_path
        result = subprocess.run([powershell_path(), '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        if result.returncode:
            return []
        value = json.loads((result.stdout or '').lstrip('\ufeff') or '[]')
        return value if isinstance(value, list) else [value]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


_INTERFACE_NAME = re.compile(r'[A-Za-z0-9_.:-]{1,63}')


def _valid_interface_name(value):
    """A Linux interface name, or on Windows an adapter alias (which may contain spaces: "Wi-Fi 2")."""
    if not isinstance(value, str):
        return False
    if sys.platform == 'win32':
        return windows_network.valid_alias(value)
    return bool(_INTERFACE_NAME.fullmatch(value))


def _interfaces(provider):
    result = []
    for record in provider():
        if not isinstance(record, dict) or not record.get('physical') or not record.get('connected'):
            continue
        prefix, address = record.get('prefix'), record.get('address')
        if type(prefix) is not int or not 1 <= prefix <= 30 or not _private(address):
            continue
        network = ipaddress.IPv4Network(f'{address}/{prefix}', strict=False)
        if not any(network.subnet_of(private) for private in _PRIVATE):
            continue
        if address not in (str(network.network_address), str(network.broadcast_address)):
            result.append((address, network))
    return result


# Fields a host accepts only when it advertises QUALITY_CAPABILITY. Older hosts
# reject unknown fields, so clients strip these unless the capability is present.
QUALITY_CAPABILITY = 'quality-2'
PIPELINE_CAPABILITY = 'pipeline-1'
TIMING_LOCK_CAPABILITY = 'vsync-lock-1'
QUALITY_FIELDS = ('encoder', 'network', 'timing_lock')
ENCODER_PRESETS = ('speed', 'balanced')
NETWORK_KINDS = ('wired', 'wifi', 'unknown')
# RC4 stream fields (10-bit, AV1, foveated encoding, pacing, intra refresh, quick
# resume) need STREAM_CAPABILITY. One capability covers them so the hello list stays
# short: RC3 clients read only the first eight sorted capabilities. The host lists
# what its driver and companion can actually do in the separate 'features' field.
STREAM_CAPABILITY = 'quality-3'
STREAM_FIELDS = ('ten_bit', 'foveation', 'pacing_mbps', 'intra_refresh', 'keep_warm_minutes')
# RC4.1: the motion-prediction switch, the client's ALVR hostname (so the host can
# reconnect to its known address) and the stream state in heartbeats (stream recovery).
RECOVERY_CAPABILITY = 'quality-4'
RECOVERY_FIELDS = ('host_prediction', 'client_hostname')
FEATURES = ('ten-bit', 'av1', 'foveation', 'gaze-foveation', 'optical-centre', 'pacing', 'intra-refresh',
            'link-test', 'quick-resume', 'host-power', 'prediction-switch', 'init-config', 'stream-recovery',
            'report-upload', 'fovea-view', 'session-gaze', 'bitrate-control')
# 1.0.0.8: 'bitrate-control' hosts accept a 'bitrate' (whole Mbit/s) in heartbeats and set the running stream to
# it at once: the headset client's bitrate governor (launcher/stream_budget.py) steps it down when decoding falls
# behind or frames are lost, and back up while the stream runs clean.
BITRATE_SPACING_S = 2.0   # a host applies at most one bitrate change this often
BITRATE_ATTEMPTS = 3      # failed changes in one session before a host stops trying
# RC4.5: 'session-gaze' hosts accept a 'gaze' session field: the client's eye tracking follows the eyes this
# session, so the host turns its eye tracking (VRChat's OSC eye input on the host PC) on for the session.
# RC4.3: the client shares its live eye-tracking view (launcher/fovea.py) with the host page,
# at most about ten times a second, during its own session. Memory only on the host.
FOVEA_INTERVAL_S = 0.08
# RC4.2: a paired client may send its report to the host (gzip JSON, base64 in the body).
REPORT_LIMIT = 6 * 1024 * 1024
REPORT_INTERVAL_S = 3
CLIENT_HOSTNAME = re.compile(r'^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,4}client\.local\.$')
STREAM_STATES = ('stopped', 'starting', 'searching', 'connecting', 'connected', 'restarting', 'disconnected',
                 'error', 'streaming', 'recovery')
CODECS = ('H264', 'Hevc', 'AV1')
FOVEATION_KEYS = {'center_size', 'center_shift', 'edge_ratio', 'gaze', 'optical_centre'}
MIN_PACING_MBPS, MAX_PACING_MBPS = 50, 40000


def host_settings(settings, capabilities):
    """Drop fields a peer host would reject (RC1/RC2 hosts accept only the base set)."""
    settings = dict(settings)
    capabilities = capabilities or ()
    if QUALITY_CAPABILITY not in capabilities:
        for field in QUALITY_FIELDS:
            settings.pop(field, None)
    if STREAM_CAPABILITY not in capabilities:
        for field in STREAM_FIELDS:
            settings.pop(field, None)
        if settings.get('codec') == 'AV1':
            settings['codec'] = 'Hevc'  # earlier hosts offer H.264 and HEVC only
    if RECOVERY_CAPABILITY not in capabilities:
        for field in RECOVERY_FIELDS:
            settings.pop(field, None)
    return settings


def stream_report(value):
    """The headset client's stream state carried by heartbeats (RC4.1 hosts).

    up: fresh video is being shown; down_s: seconds without it since the stream was
    last up in this session (0 before the first frame, so a slow start never counts);
    state: the client's ALVR connection or video state.
    """
    if not isinstance(value, dict) or set(value) - {'up', 'down_s', 'state'}:
        raise SessionError('Invalid stream report.')
    up, down, state = value.get('up', False), value.get('down_s', 0), value.get('state', '')
    if (type(up) is not bool or type(down) not in (int, float) or not math.isfinite(down) or not 0 <= down <= 86400
            or not isinstance(state, str) or (state and state not in STREAM_STATES)):
        raise SessionError('Invalid stream report.')
    return {'up': up, 'down_s': round(float(down), 1), 'state': state}


def _pair(value, low, high):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            not all(type(item) in (int, float) and math.isfinite(item) and low <= item <= high for item in value)):
        raise SessionError('Invalid foveated encoding setting.')
    return [float(item) for item in value]


def foveation_settings(value):
    """Validated foveated-encoding parameters (ALVR's per-axis centre size, shift and edge ratio)."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - FOVEATION_KEYS or not {'center_size', 'edge_ratio'} <= set(value):
        raise SessionError('Invalid foveated encoding setting.')
    result = {'center_size': _pair(value['center_size'], .2, .95),
              'center_shift': _pair(value.get('center_shift', [0.0, 0.0]), -1.0, 1.0),
              'edge_ratio': _pair(value['edge_ratio'], 1.0, 8.0)}
    for key in ('gaze', 'optical_centre'):
        if type(value.get(key, False)) is not bool:
            raise SessionError('Invalid foveated encoding setting.')
        result[key] = value.get(key, False)
    return result


def session_settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'codec', 'bitrate', 'microphone', 'resolution', 'headset', 'gaze', *QUALITY_FIELDS, *STREAM_FIELDS, *RECOVERY_FIELDS}:
        raise SessionError('Unsupported host stream setting.')
    codec, bitrate = value.get('codec', 'H264'), value.get('bitrate', 80)
    if codec not in CODECS or type(bitrate) is not int or not 10 <= bitrate <= 300:
        raise SessionError('Choose H264, Hevc or AV1 and a bitrate from 10 to 300 Mbit/s.')
    microphone=value.get('microphone',False)
    if type(microphone) is not bool:raise SessionError('Microphone selection must be true or false.')
    result={'codec': codec, 'bitrate': bitrate, 'microphone':microphone}
    if 'gaze' in value:
        if type(value['gaze']) is not bool:raise SessionError('Eye tracking must be true or false.')
        result['gaze']=value['gaze']
    if 'resolution' in value:
        if value['resolution'] not in RESOLUTIONS:raise SessionError('Choose a supported stream resolution.')
        result['resolution']=value['resolution']
    if 'headset' in value:
        try:result['headset']=headset_metadata(value['headset'])
        except ValueError as error:raise SessionError(str(error)) from error
    if 'encoder' in value:
        if value['encoder'] not in ENCODER_PRESETS:raise SessionError('Choose a supported encoder priority.')
        result['encoder']=value['encoder']
    if 'network' in value:
        if value['network'] not in NETWORK_KINDS:raise SessionError('Invalid client network type.')
        result['network']=value['network']
    if 'timing_lock' in value:
        if type(value['timing_lock']) is not bool:raise SessionError('Frame timing lock must be true or false.')
        result['timing_lock']=value['timing_lock']
    for key in ('ten_bit', 'intra_refresh'):
        if key in value:
            if type(value[key]) is not bool:raise SessionError('Invalid stream setting: '+key+'.')
            result[key]=value[key]
    if 'foveation' in value:
        result['foveation']=foveation_settings(value['foveation'])
    if 'pacing_mbps' in value:
        pacing=value['pacing_mbps']
        if type(pacing) is not int or not (pacing==0 or MIN_PACING_MBPS<=pacing<=MAX_PACING_MBPS):
            raise SessionError('Video pacing must be off (0) or 50 to 40000 Mbit/s.')
        result['pacing_mbps']=pacing
    if 'keep_warm_minutes' in value:
        minutes=value['keep_warm_minutes']
        if type(minutes) is not int or not 0<=minutes<=120:
            raise SessionError('Keep host SteamVR open for 0 to 120 minutes.')
        result['keep_warm_minutes']=minutes
    if 'host_prediction' in value:
        if type(value['host_prediction']) is not bool:raise SessionError('Motion prediction must be true or false.')
        result['host_prediction']=value['host_prediction']
    if 'client_hostname' in value:
        hostname=value['client_hostname']
        if not isinstance(hostname,str) or len(hostname)>128 or not CLIENT_HOSTNAME.fullmatch(hostname):
            raise SessionError('Invalid headset client hostname.')
        result['client_hostname']=hostname
    return result


def _read(path):
    try:
        if path.stat().st_size <= 65536:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                return value
    except (OSError, ValueError, RecursionError):
        pass
    return {}


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SessionError('Session redirects are not accepted.')


class _LimitedHTTP(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(*args)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class SessionHost:
    def __init__(self, directory, start_callback, stop_callback, status_callback,
                 allow_pairing, log, port=PORT, *, allow_start=None, address_provider=network_addresses,
                 test_peer=None, clock=time.monotonic, capabilities=(QUALITY_CAPABILITY, PIPELINE_CAPABILITY),
                 telemetry=None, features=(), link_test=None, host_info=None, report=None, fovea=None,
                 bitrate=None):
        # test_peer is an explicit dependency injection for loopback tests only.
        # features: callable or list of FEATURES this host offers (driver dependent).
        # link_test(request, peer_ip, cancel) runs a network test towards the asking client only.
        # host_info(local_address) returns a small dict (power, wake, quick resume) for status.
        self.features, self.link_test_callback, self.host_info = features, link_test, host_info
        self.link_test_lock = threading.Lock()
        self.last_link_test = -1e9
        # report(name, report_bytes, peer) stores a client's report (the decoded JSON bytes of a
        # validated client report) and returns its saved name.
        self.report_callback = report
        # fovea(view) keeps the client's live eye-tracking view for the host page (RC4.3).
        self.fovea_callback = fovea
        self.last_fovea = -1e9
        # bitrate(mbps) sets the running stream's bitrate (1.0.0.8, 'bitrate-control'); applied on a thread of its
        # own, the latest request only, at most every BITRATE_SPACING_S.
        self.bitrate_callback = bitrate
        self.bitrate_wanted = None
        self.bitrate_signal = threading.Event()
        self.report_lock = threading.Lock()
        self.report_read = threading.Lock()  # one report body read at a time
        self.last_report = -1e9
        self.directory = Path(directory)
        self.path = self.directory / 'session-host.json'
        self.start_callback, self.stop_callback = start_callback, stop_callback
        self.status_callback, self.allow_pairing, self.log = status_callback, allow_pairing, log
        self.allow_start = allow_start or allow_pairing
        self.address_provider, self.test_peer, self.clock = address_provider, test_peer, clock
        # capabilities may be a callable so driver support is re-read after repair/upgrade.
        self.capabilities, self.telemetry = capabilities, telemetry
        self.lock = threading.RLock()
        self.operations = queue.Queue()
        self.closed = threading.Event()
        saved = _read(self.path)
        self.host_id = saved.get('host_id')
        if not isinstance(self.host_id, str) or not _ID.fullmatch(self.host_id):
            self.host_id = secrets.token_hex(16)
        peers = saved.get('peers', {})
        self.peers = {key: value for key, value in peers.items()
                      if isinstance(key, str) and _ID.fullmatch(key) and isinstance(value, str) and _TOKEN.fullmatch(value)} if isinstance(peers, dict) else {}
        self.peers = dict(list(self.peers.items())[:16])
        self._persist()
        self.lease = None
        self.state, self.message = 'waiting', 'Ready. Start a session from the headset client.'
        self.completed = []
        self.runtime = {}
        self.last_error = ''
        self.interfaces = _interfaces(self.address_provider)
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)
            def log_message(self, *args):
                pass
            def reply(self, code, value):
                raw = json.dumps(value, separators=(',', ':')).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (OSError, TimeoutError):
                    pass
            def guard(self):
                return (self.headers.get('Origin') is None and self.headers.get('X-VROSTBridge-Session') == '1'
                        and owner.allowed(self.client_address[0]) and not owner.closed.is_set())
            def do_GET(self):
                if not self.guard():
                    self.reply(403, {'error': 'Private LAN companion access required.'})
                elif self.path == '/hello':
                    self.reply(200, owner.hello())
                else:
                    self.reply(404, {'error': 'Unknown endpoint.'})
            def do_POST(self):
                if not self.guard():
                    self.reply(403, {'error': 'Private LAN companion access required.'})
                    return
                if self.path not in ('/pair', '/start', '/heartbeat', '/stop', '/status', '/linktest', '/report', '/fovea'):
                    self.reply(404, {'error': 'Unknown endpoint.'})
                    return
                if self.path != '/report':
                    self.handle_post(LIMIT)
                    return
                # A report body may be megabytes: it is read only for a paired client (credentials
                # also in the headers) and one at a time.
                self.connection.settimeout(20)  # a report is larger than a control message
                if not owner.report_callback:
                    self.refuse(409, 'This host does not accept reports. Install this release on the host PC.')
                    return
                if not owner.paired(self.headers.get('X-VROSTBridge-Client', ''), self.headers.get('X-VROSTBridge-Token', '')):
                    self.refuse(403, 'Companion is not paired with this host.')
                    return
                if not owner.report_read.acquire(blocking=False):
                    self.refuse(409, 'Another report is being received; try again in a moment.')
                    return
                try:
                    self.handle_post(REPORT_LIMIT)
                finally:
                    owner.report_read.release()
            def refuse(self, status, message):
                """Reply after discarding the body unparsed (in small pieces), so the sender sees the reply
                rather than a reset connection."""
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) <= REPORT_LIMIT:
                    remaining = int(lengths[0])
                    try:
                        while remaining > 0:
                            chunk = self.rfile.read(min(remaining, 65536))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                    except (OSError, TimeoutError):
                        return
                self.reply(status, {'error': message})
            def handle_post(self, limit):
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > limit
                        or self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json'):
                    self.reply(400, {'error': 'A bounded JSON body is required.'})
                    return
                try:
                    value = json.loads(self.rfile.read(int(lengths[0])))
                    if not isinstance(value, dict):
                        raise SessionError('Invalid request.')
                    try:
                        local = self.connection.getsockname()[0]
                    except (OSError, IndexError, TypeError):
                        local = None
                    result = owner.dispatch(self.path, value, self.client_address[0], local=local)
                    self.reply(200, result)
                except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
                    self.reply(409 if isinstance(error, SessionError) else 400, {'error': str(error)[:300]})
                except (OSError, TimeoutError):
                    self.reply(503, {'error': 'Session broker could not save its state.'})

        self.server = _LimitedHTTP(('0.0.0.0', port), Handler)
        self.port = self.server.server_port
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp.bind(('0.0.0.0', self.port))
            self.udp.settimeout(.5)
        except OSError:
            self.udp.close()
            self.server.server_close()
            raise
        self.worker = threading.Thread(target=self._work, daemon=True, name='VROSTBridge-session-actions')
        self.worker.start()
        self.web = threading.Thread(target=self.server.serve_forever, daemon=True, name='VROSTBridge-session-http')
        self.web.start()
        self.discovery = threading.Thread(target=self._discover, daemon=True, name='VROSTBridge-session-discovery')
        self.discovery.start()
        self.monitor = threading.Thread(target=self._monitor, daemon=True, name='VROSTBridge-session-lease')
        self.monitor.start()
        if self.bitrate_callback:
            self.bitrate_worker = threading.Thread(target=self._bitrates, daemon=True, name='VROSTBridge-session-bitrate')
            self.bitrate_worker.start()

    def _persist(self):
        _save(self.path, {'host_id': self.host_id, 'peers': self.peers})

    def allowed(self, peer):
        if self.test_peer is not None:
            return bool(self.test_peer(peer))
        if not _private(peer):
            return False
        address = ipaddress.IPv4Address(peer)
        with self.lock:
            return any(address in net and address not in (net.network_address, net.broadcast_address)
                       for _, net in self.interfaces)

    def paired(self, client_id, token):
        """Whether these are a paired client's credentials (checked before a large body is read)."""
        if (not isinstance(client_id, str) or not _ID.fullmatch(client_id) or not isinstance(token, str)
                or not _TOKEN.fullmatch(token)):
            return False
        with self.lock:
            known = self.peers.get(client_id)
        return known is not None and secrets.compare_digest(known, hashlib.sha256(token.encode()).hexdigest())

    def host_capabilities(self):
        try:
            value = self.capabilities() if callable(self.capabilities) else self.capabilities
            return sorted({str(item)[:32] for item in value or () if isinstance(item, str)})[:8]
        except Exception:
            return [QUALITY_CAPABILITY]

    def host_features(self):
        try:
            value = list(self.features() if callable(self.features) else self.features or ())
        except Exception:
            value = []
        if self.report_callback:
            value.append('report-upload')
        if self.fovea_callback:
            value.append('fovea-view')
        value.append('session-gaze')  # RC4.5: session_settings() accepts 'gaze'
        if self.bitrate_callback:
            value.append('bitrate-control')  # 1.0.0.8: heartbeats may carry 'bitrate'
        return sorted({item for item in value if item in FEATURES})

    def hello(self):
        return {'protocol': PROTOCOL, 'native_protocol': NATIVE_PROTOCOL, 'host_id': self.host_id,
                'name': socket.gethostname()[:80], 'capabilities': self.host_capabilities(),
                'features': self.host_features()}

    def _telemetry(self):
        if not self.telemetry:
            return None
        try:
            value = self.telemetry()
        except Exception:
            return None
        # Bounded: a heartbeat reply must stay small.
        return value if isinstance(value, dict) and len(json.dumps(value)) <= 2048 else None

    def _host_info(self, local=None):
        if not self.host_info:
            return None
        try:
            value = self.host_info(local)
        except Exception:
            return None
        return value if isinstance(value, dict) and len(json.dumps(value)) <= 2048 else None

    def _snapshot_locked(self, local=None):
        result = dict(self.hello(), state=self.state, message=self.message, runtime=dict(self.runtime), last_error=self.last_error)
        pipeline = self._telemetry() if self.lease else None
        if pipeline:
            result['pipeline'] = pipeline
        info = self._host_info(local)
        if info:
            result['host_info'] = info
        if self.lease:
            result.update(lease_id=self.lease['id'], client_id=self.lease['client_id'],
                          client_ip=self.lease['ip'], expires_in=max(0, round(self.lease['deadline'] - self.clock(), 1)))
        return result

    def snapshot(self):
        with self.lock:
            return self._snapshot_locked()

    def lease_details(self):
        """The running lease's client address, ALVR hostname and latest stream report (None if no lease).

        The stream report's down_s is advanced by the time since it arrived.
        """
        with self.lock:
            if not self.lease:
                return None
            result = {'lease_id': self.lease['id'], 'ip': self.lease['ip'],
                      'client_hostname': (self.lease.get('settings') or {}).get('client_hostname', ''),
                      'started': bool(self.lease.get('started')), 'stream': None}
            stream = self.lease.get('stream')
            if stream:
                age = max(0.0, self.clock() - stream['received'])
                result['stream'] = {'up': stream['up'], 'state': stream['state'], 'age_s': round(age, 1),
                                    'down_s': round(stream['down_s'] + (age if stream['down_s'] > 0 else 0), 1)}
            return result

    @staticmethod
    def _policy(policy, local):
        """Network policies may accept the local address a request arrived on."""
        try:
            accepts = bool(inspect.signature(policy).parameters)
        except (TypeError, ValueError):
            accepts = False
        return policy(local) if accepts and local is not None else policy()

    def dispatch(self, action, value, peer, local=None):
        if not self.allowed(peer) or self.closed.is_set():
            raise SessionError('Private LAN companion access required.')
        permitted = {'client_id', 'token'} | ({'request_id', 'settings'} if action == '/start'
                                               else {'lease_id', 'stream', *(('bitrate',) if self.bitrate_callback else ())}
                                               if action == '/heartbeat'
                                               else {'lease_id'} if action == '/stop' else {'test'} if action == '/linktest'
                                               else {'name', 'data'} if action == '/report'
                                               else {'lease_id', 'view'} if action == '/fovea' else set())
        if set(value) - permitted:
            raise SessionError('Unsupported session request field.')
        client_id, token = value.get('client_id'), value.get('token')
        if not isinstance(client_id, str) or not _ID.fullmatch(client_id) or not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise SessionError('Invalid companion credentials.')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            known = self.peers.get(client_id)
        if action == '/pair' and known is None:
            # The policy may run PowerShell; do not hold the status mutex.
            if not self._policy(self.allow_pairing, local):
                raise SessionError('Enable automatic connection on the host Private home network first.')
            with self.lock:
                known = self.peers.get(client_id)
                if known is None:
                    if len(self.peers) >= 16:
                        raise SessionError('Host companion pairing list is full.')
                    self.peers[client_id] = digest
                    try:
                        self._persist()
                    except OSError:
                        del self.peers[client_id]
                        raise
                    known = digest
            self.log('Headset companion paired on the private LAN.')
        if known is None or not secrets.compare_digest(known, digest):
            raise SessionError('Companion is not paired with this host.')
        # Check the private-network policy on every new start too, so saved
        # credentials do not bypass a later Public-network change.
        if action in ('/start', '/linktest', '/report') and not self._policy(self.allow_start, local):
            raise SessionError('Host automatic connection is unavailable on this network.')
        settings = session_settings(value.get('settings')) if action == '/start' else None
        stream = None
        if action == '/heartbeat' and 'stream' in value:
            try:
                stream = stream_report(value['stream'])
            except SessionError:
                stream = None  # an optional report never fails the heartbeat (newer clients may add states)
        bitrate = None
        if action == '/heartbeat' and 'bitrate' in value:
            bitrate = value['bitrate']
            if type(bitrate) is not int or not 10 <= bitrate <= 300:
                raise SessionError('A stream bitrate is a whole number from 10 to 300 Mbit/s.')
        if action == '/linktest':
            return self._link_test(client_id, value.get('test'), peer, local)
        if action == '/report':
            return self._receive_report(value, peer)
        if action == '/fovea':
            return self._receive_fovea(client_id, value)
        with self.lock:
            if self.closed.is_set():
                raise SessionError('The host companion is closing.')
            if action == '/pair':
                return self.hello()
            if action == '/status':
                result = self._snapshot_locked(local)
                if self.lease and self.lease['client_id'] != client_id:
                    result.pop('lease_id', None)
                return result
            if action == '/start':
                request_id = value.get('request_id')
                if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
                    raise SessionError('Invalid start request identity.')
                if (client_id, request_id) in self.completed:
                    raise SessionError('This session has ended; start a new session.')
                if self.lease:
                    if self.lease['client_id'] != client_id:
                        raise SessionError('Another headset client already owns this session.')
                    if self.state in ('stopping', 'error'):
                        raise SessionError('The previous host session must finish stopping first.')
                    if request_id not in self.lease['requests']:
                        self.lease['requests'].append(request_id)
                        self.lease['requests'] = self.lease['requests'][-32:]
                    self.lease['ip'] = peer
                    self.lease['deadline'] = self.clock() + LEASE_SECONDS
                    return self._snapshot_locked(local)
                # 'bitrate': what the stream runs at (1.0.0.8): it starts with the session's own setting.
                self.lease = {'id': secrets.token_hex(16), 'client_id': client_id, 'ip': peer,
                              'request_id': request_id, 'requests': [request_id], 'settings': settings, 'started':False, 'deadline': self.clock() + LEASE_SECONDS,
                              'bitrate': (settings or {}).get('bitrate'), 'bitrate_failures': 0}
                self.last_error = ''
                self.runtime = {}
                self.state, self.message = 'starting', 'Starting host SteamVR for the headset client.'
                self.operations.put(('start', self.lease['id']))
                return self._snapshot_locked(local)
            if action not in ('/heartbeat', '/stop'):
                raise SessionError('Unknown session action.')
            if not self.lease:
                return self._snapshot_locked(local)  # stop/heartbeat acknowledges a completed lease
            if self.lease['client_id'] != client_id or not secrets.compare_digest(str(value.get('lease_id', '')), self.lease['id']):
                raise SessionError('This companion does not own the session lease.')
            if action == '/heartbeat':
                self.lease['ip'] = peer
                self.lease['deadline'] = self.clock() + LEASE_SECONDS
                if stream is not None:
                    self.lease['stream'] = dict(stream, received=self.clock())
                if bitrate is not None and self.lease.get('started') and self.lease.get('bitrate') != bitrate:
                    self.lease['bitrate'] = bitrate
                    self.bitrate_wanted = (self.lease['id'], bitrate)
                    self.bitrate_signal.set()
            else:
                self._queue_stop_locked('Stopping host SteamVR at the headset client’s request.')
            return self._snapshot_locked(local)

    def _link_test(self, client_id, request, peer, local):
        """Send network test traffic to the paired client that asked, never to another address.

        Only between sessions, one test at a time and at most one every five seconds:
        the test briefly fills the link and must not disturb a running stream.
        """
        if not self.link_test_callback or 'link-test' not in self.host_features():
            raise SessionError('This host cannot run the network test. Install this release on the host PC.')
        if not isinstance(request, dict):
            raise SessionError('Invalid network test request.')
        with self.lock:
            if self.lease and (self.lease['client_id'] != client_id or self.lease.get('started')):
                raise SessionError('Stop the running session before testing the network.')
        if not self.link_test_lock.acquire(blocking=False):
            raise SessionError('A network test is already running.')
        try:
            if self.clock() - self.last_link_test < 5:
                raise SessionError('Wait a few seconds before testing the network again.')
            self.last_link_test = self.clock()
            summary = self.link_test_callback(dict(request), peer, self.closed)
            self.log('Network test sent to the headset client at ' + str(peer) + '.')
        finally:
            self.link_test_lock.release()
        result = self.hello()
        result['sender'] = summary if isinstance(summary, dict) else {}
        if len(json.dumps(result)) > LIMIT:
            result['sender'] = {'steps': [], 'error': 'Network test summary too large.'}
        return result

    def _receive_fovea(self, client_id, value):
        """Keep the leasing client's live eye-tracking view for the host page (never stored or logged)."""
        if not self.fovea_callback:
            raise SessionError('This host does not show the eye-tracking view.')
        from launcher.fovea import view as fovea_view
        try:
            view = fovea_view(value.get('view'))
        except ValueError:
            raise SessionError('Invalid eye-tracking view.') from None
        with self.lock:
            if (not self.lease or self.lease['client_id'] != client_id
                    or not secrets.compare_digest(str(value.get('lease_id', '')), self.lease['id'])):
                raise SessionError('This companion does not own the session lease.')
            now = self.clock()
            accepted = now - self.last_fovea >= FOVEA_INTERVAL_S
            if accepted:
                self.last_fovea = now
        if accepted:
            self.fovea_callback(view)
        return {'protocol': PROTOCOL, 'native_protocol': NATIVE_PROTOCOL, 'host_id': self.host_id, 'accepted': accepted}

    def _receive_report(self, value, peer):
        """Store a paired client's report for download on the host page (one at a time)."""
        if not self.report_callback:
            raise SessionError('This host does not accept reports. Install this release on the host PC.')
        name, data = value.get('name'), value.get('data')
        if not isinstance(name, str) or not isinstance(data, str) or len(name) > 120:
            raise SessionError('Invalid report upload.')
        if not self.report_lock.acquire(blocking=False):
            raise SessionError('Another report is being received; try again in a moment.')
        try:
            if self.clock() - self.last_report < REPORT_INTERVAL_S:
                raise SessionError('Wait a few seconds before sending another report.')
            self.last_report = self.clock()
            try:
                from launcher.reporting import inflate, parse
                raw = inflate(base64.b64decode(data, validate=True))
                report = parse(raw)
            except (ValueError, OSError, EOFError, zlib.error, RecursionError) as error:
                # zlib.error: corrupt compressed data; EOFError: truncated; BadGzipFile is an OSError.
                raise SessionError('The report could not be read (' + str(error)[:120] + ').') from None
            if report.get('role') != 'client':
                raise SessionError('Only client reports are accepted.')
            try:
                saved = self.report_callback(name, raw, peer)
            except OSError as error:
                raise SessionError('The host could not save the report (' + str(error)[:120] + ').') from None
        finally:
            self.report_lock.release()
        return dict(self.hello(), saved=saved)

    def request_stop(self):
        """Trusted local emergency Stop; clients observe the ended lease."""
        with self.lock:
            self._queue_stop_locked('Host user stopped the session.')
            return self._snapshot_locked()

    def _queue_stop_locked(self, message):
        if self.lease and self.state != 'stopping':
            self.state, self.message = 'stopping', message
            self.operations.put(('stop', self.lease['id']))
            self.log(message)

    def _work(self):
        while True:
            task = self.operations.get()
            if task is None:
                return
            action, lease_id = task
            with self.lock:
                if not self.lease or self.lease['id'] != lease_id:
                    continue
                skip = action == 'start' and self.state == 'stopping'
            if skip:
                continue
            try:
                if action == 'start':
                    with self.lock:
                        settings = dict(self.lease['settings'])
                    self.start_callback(settings)
                else:
                    self.stop_callback()
                with self.lock:
                    if not self.lease or self.lease['id'] != lease_id:
                        continue
                    if action == 'start':
                        self.lease['started']=True
                        if self.state=='starting':self.message='SteamVR launch requested; waiting for the host driver.'
                    else:
                        self.completed.extend((self.lease['client_id'], request_id) for request_id in self.lease['requests'])
                        self.completed = self.completed[-32:]
                        self.lease = None
                        self.state = 'waiting'
                        self.message = ('Previous host session ended: ' + self.last_error) if self.last_error else 'Ready. Start a session from the headset client.'
                self.log('Host session '+('launch requested.' if action=='start' else 'stopped.'))
            except Exception as error:
                self.log('Host session ' + action + ' failed: ' + str(error))
                with self.lock:
                    if self.lease and self.lease['id'] == lease_id:
                        self.last_error = str(error)[:500]
                        self.state, self.message = 'error', self.last_error
                        self.lease['deadline'] = self.clock() + LEASE_SECONDS
                        if action == 'start':
                            self._queue_stop_locked('Host start failed; cleaning up its SteamVR session.')

    def _bitrates(self):
        """1.0.0.8: apply the latest bitrate a client asked for, one at a time and at most every
        BITRATE_SPACING_S, only while its lease runs."""
        applied = -1e9
        while not self.closed.is_set():
            if not self.bitrate_signal.wait(1):
                continue
            wait = BITRATE_SPACING_S - (self.clock() - applied)
            if wait > 0 and self.closed.wait(wait):
                return
            with self.lock:
                self.bitrate_signal.clear()
                wanted, self.bitrate_wanted = self.bitrate_wanted, None
                current = self.lease['id'] if self.lease and self.state in ('starting', 'running') else None
            if not wanted or wanted[0] != current:
                continue
            try:
                self.bitrate_callback(wanted[1])
                applied = self.clock()
            except Exception as error:
                with self.lock:
                    lease = self.lease if self.lease and self.lease['id'] == wanted[0] else None
                    if lease is not None:
                        lease['bitrate_failures'] = lease.get('bitrate_failures', 0) + 1
                        retry = lease['bitrate_failures'] < BITRATE_ATTEMPTS
                        if retry and lease.get('bitrate') == wanted[1]:
                            lease['bitrate'] = None  # the next heartbeat asks again
                if lease is not None and lease['bitrate_failures'] <= BITRATE_ATTEMPTS:
                    self.log('Stream bitrate change to ' + str(wanted[1]) + ' Mbit/s failed: ' + str(error)[:200])

    def _monitor(self):
        refreshed = self.clock()
        while not self.closed.wait(1):
            now = self.clock()
            with self.lock:
                if self.lease and self.lease['deadline'] <= now:
                    self._queue_stop_locked('Client heartbeat ended; stopping host SteamVR.')
            with self.lock:
                lease_id = self.lease['id'] if self.lease is not None and self.lease.get('started') and self.state in ('starting','running') else None
            if lease_id:
                try:
                    runtime = self.status_callback()
                    if isinstance(runtime, dict):
                        summary = {key: str(runtime.get(key) or '')[:500] for key in ('runtime_state', 'message', 'error')}
                        summary['available'] = bool(runtime.get('available'))
                        summary['audio']=dict(runtime.get('audio') or {})
                        with self.lock:
                            if not self.lease or self.lease['id'] != lease_id:
                                continue
                            self.runtime = summary
                            if self.state in ('starting','running') and runtime.get('runtime_state') in ('error', 'stopped'):
                                normal_close = runtime.get('shutdown_reason') == 'steam-quit'
                                self.last_error = '' if normal_close else str(runtime.get('error') or runtime.get('message') or 'Host SteamVR ended.')[:500]
                                self._queue_stop_locked('SteamVR was closed by Steam; ending the session.' if normal_close else 'Host runtime ended; cleaning up its session.')
                            elif self.state in ('starting','running'):
                                ready=runtime.get('runtime_state')=='ready' and bool(runtime.get('available'))
                                self.state='running' if ready else 'starting'
                                self.message=summary['message'] or ('Host ready for the headset stream.' if ready else 'Waiting for SteamVR startup or headset negotiation.')
                except Exception as error:
                    self.log('Host runtime status could not be read: ' + str(error))
            if now - refreshed >= 15:
                interfaces = _interfaces(self.address_provider)
                with self.lock:
                    self.interfaces = interfaces
                refreshed = now

    def _discover(self):
        while not self.closed.is_set():
            try:
                raw, (peer, port) = self.udp.recvfrom(1025)
                if len(raw) > 1024 or not self.allowed(peer):
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict) or set(value) != {'protocol', 'nonce'} or value['protocol'] != PROTOCOL or not isinstance(value['nonce'], str) or not _ID.fullmatch(value['nonce']):
                    continue
                self.udp.sendto(json.dumps(dict(self.hello(), nonce=value['nonce'])).encode(), (peer, port))
            except (OSError, ValueError, RecursionError):
                continue

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        with self.lock:
            self._queue_stop_locked('Host companion is closing; stopping its session.')
        self.server.shutdown()
        self.server.server_close()
        self.udp.close()
        self.operations.put(None)
        self.worker.join(45)
        if self.worker.is_alive():
            self.log('Host session cleanup is still finishing.')


WINDOWS_IP_UNICAST_IF = 31  # ws2ipdef.h; Linux uses 50


def pin_ipv4_interface(sock, interface):
    """Choose egress per socket without root or changing the user's routes.

    Binding only an IPv4 source does not choose the outgoing device on Linux
    when two physical adapters share a subnet. IP_UNICAST_IF uses an interface
    index in network byte order and covers that separate routing decision.
    Windows has the same option (IPPROTO_IP socket options: "an interface
    index in network byte order"); its index comes from the adapter alias.
    """
    if not interface:
        return
    if sys.platform == 'win32':
        index = windows_network.interface_index(interface)
        if not index:
            raise OSError('The network adapter ' + str(interface)[:64] + ' is not available.')
        sock.setsockopt(socket.IPPROTO_IP, WINDOWS_IP_UNICAST_IF, struct.pack('!I', index))
        return
    if not sys.platform.startswith('linux'):
        raise SessionError('Explicit headset-client adapter selection requires Linux or Windows.')
    sock.setsockopt(socket.IPPROTO_IP, getattr(socket,'IP_UNICAST_IF',50),
                    struct.pack('!I',socket.if_nametoindex(interface)))


def _source_connection(address, timeout, source_address, interface):
    # The caller already requires a numeric LAN IPv4 address. Avoid DNS and
    # address-family fallbacks that could bypass the chosen source/device.
    ipaddress.IPv4Address(address[0])
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:sock.settimeout(timeout)
        pin_ipv4_interface(sock,interface)
        sock.bind(source_address)
        sock.connect(address)
        return sock
    except BaseException:
        sock.close();raise


class _SourceHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, source, interface=''):
        super().__init__()
        self.source,self.interface = source,interface

    def http_open(self, request):
        def connection(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            result=http.client.HTTPConnection(host, timeout=timeout, source_address=(self.source, 0), **kwargs)
            result._create_connection=lambda address,wait,source:_source_connection(address,wait,source,self.interface)
            return result
        return self.do_open(connection, request)


class SessionClient:
    def __init__(self, directory, log, port=PORT, *, address_provider=network_addresses,
                 test_peer=None):
        self.path = Path(directory) / 'session-client.json'
        self.log, self.port = log, port
        self.address_provider, self.test_peer = address_provider, test_peer
        self.lock, self.actions = threading.RLock(), threading.Lock()
        self.closed = threading.Event()
        saved = _read(self.path)
        self.client_id = saved.get('client_id')
        self.token = saved.get('token')
        if not isinstance(self.client_id, str) or not _ID.fullmatch(self.client_id):
            self.client_id = secrets.token_hex(16)
        if not isinstance(self.token, str) or not _TOKEN.fullmatch(self.token):
            self.token = secrets.token_hex(32)
        self.interface = saved.get('interface', '')
        if not isinstance(self.interface,str) or (self.interface and not _valid_interface_name(self.interface)):
            self.interface = ''
        self.source_ip = ''
        self.source_interface = ''
        self.host_ip = saved.get('host_ip', '')
        self.host_id = saved.get('host_id', '')
        self.lease_id = saved.get('lease_id', '')
        self.request_id = saved.get('request_id', '')
        self.state, self.message = 'waiting', 'Ready to find your rendering host.'
        self.last_reply = {}
        self.host_capabilities = []
        self.host_features = []
        self.last_seen = time.monotonic()
        # Callable returning stream_report() fields for RC4.1 hosts (set by the companion).
        self.stream_status = None
        self.heartbeat_failed_since = None
        self._persist()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.heartbeat_thread = threading.Thread(target=self._heartbeats, daemon=True, name='VROSTBridge-session-client')
        # A companion restart does not silently adopt a stream whose native
        # client process is gone. Recover any saved host lease before accepting
        # a fresh Start. A lost Stop is covered by the host's own deadline.
        if self.lease_id:
            self.stop()
        self.heartbeat_thread.start()

    def _persist(self):
        _save(self.path, {key: getattr(self, key) for key in ('client_id', 'token', 'host_ip', 'host_id', 'lease_id', 'request_id', 'interface')})

    def select_interface(self, interface=''):
        """Persist the app's adapter preference; never change system routes."""
        if not isinstance(interface, str) or (interface and not _valid_interface_name(interface)):
            raise SessionError('Choose an available client network adapter.')
        with self.actions, self.lock:
            if self.lease_id:
                raise SessionError('Stop the session before changing its network adapter.')
            if interface and not any(item.get('interface') == interface and item.get('physical') and item.get('connected')
                                     for item in self.address_provider()):
                raise SessionError('That client network adapter is not connected.')
            self.interface, self.source_ip, self.source_interface = interface, '', ''
            self._persist()
        return self.network_status()

    def _selected_interfaces(self):
        records = self.address_provider()
        if self.interface:
            records = [record for record in records if record.get('interface') == self.interface]
        return _interfaces(lambda: records)

    def _source_for(self, ip):
        if self.source_ip:
            return self.source_ip
        choices = self._selected_interfaces()
        if self.interface:
            peer = ipaddress.IPv4Address(ip)
            for address, network in choices:
                if peer in network:
                    return address
            raise SessionError('The selected adapter has no address on the host home network.')
        # UDP connect only asks the kernel which local route it would use; no
        # datagram is sent and no Internet or DNS lookup is made.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((ip, self.port))
            address=probe.getsockname()[0]
        if self.test_peer is None and address not in {source for source,_ in choices}:
            raise SessionError('The automatic route does not use a connected physical home-network adapter. Choose the client adapter in settings.')
        return address

    def _interface_for_source(self, source):
        if self.test_peer is not None and self.test_peer(source):return ''
        allowed={address for address,_ in self._selected_interfaces()}
        for record in self.address_provider():
            if not isinstance(record,dict):continue
            interface=record.get('interface','')
            if (record.get('address')==source and source in allowed and
                    record.get('physical') and record.get('connected') and
                    (not self.interface or interface==self.interface) and
                    isinstance(interface,str) and _valid_interface_name(interface)):
                return interface
        raise SessionError('The chosen client address is no longer assigned to its physical adapter. Check the network connection and start again.')

    def prepare_network(self, host_ip='', cancel=None):
        """Choose and pin one source before launching the native transport."""
        with self.actions:
            if self.closed.is_set():raise SessionError('Client companion is closing.')
            if self.lease_id:
                raise SessionError('Stop the existing session before preparing a new network path.')
            with self.lock:self.source_ip = '';self.source_interface = ''
            host = self.discover(host_ip, cancel=cancel)
            if cancel is not None and cancel.is_set():raise SessionError('Session start cancelled.')
            source = host.get('source_ip') or self._source_for(host['host_ip'])
            interface = self._interface_for_source(source)
            with self.lock:
                self.source_ip,self.source_interface = source,interface
                self.host_ip, self.host_id = host['host_ip'], host['host_id']
                self._remember_host(host)
                self._persist()
            self.log('NETWORK selected source=' + self.source_ip + ' host=' + self.host_ip + ' adapter=' + (self.source_interface or 'Automatic'))
            return {'VROSTBRIDGE_BIND_IP': self.source_ip, 'VROSTBRIDGE_INTERFACE':self.source_interface}

    def network_status(self):
        options = []
        for record in self.address_provider():
            if (record.get('physical') and record.get('connected') and _private(record.get('address'))
                    and isinstance(record.get('interface'), str)):
                options.append({key: record.get(key) for key in ('interface','address','prefix')})
        with self.lock:
            return {'interface': self.interface, 'source_ip': self.source_ip,
                    'source_interface':self.source_interface,
                    'host_ip': self.host_ip, 'adapters': options, 'pinned': bool(self.source_ip)}

    def _allowed(self, ip):
        if self.test_peer is not None:
            return bool(self.test_peer(ip))
        if not _private(ip):
            return False
        address = ipaddress.IPv4Address(ip)
        return any(address in net and address not in (net.network_address, net.broadcast_address)
                   for _, net in self._selected_interfaces())

    def _request(self, ip, path, value=None, timeout=5, headers=None):
        if not self._allowed(ip):
            raise SessionError('Choose a host IPv4 address on your directly attached private home network.')
        headers = dict(headers or {}, **{'X-VROSTBridge-Session': '1'})
        data = None
        if value is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(value, separators=(',', ':')).encode()
        request = urllib.request.Request(f'http://{ip}:{self.port}{path}', data=data, headers=headers)
        try:
            # Each request uses the same pinned source as native control/video.
            # Existing test peers intentionally permit loopback only.
            source = self._source_for(ip)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), _SourceHTTPHandler(source,self._interface_for_source(source)))
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(LIMIT + 1)
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(LIMIT + 1)
                message = json.loads(raw).get('error', 'Host rejected the session request.') if len(raw) <= LIMIT else 'Host response is too large.'
            except (ValueError, AttributeError, RecursionError):
                message = 'Host rejected the session request.'
            finally:
                error.close()
            raise SessionError(message) from None
        if len(raw) > LIMIT:
            raise SessionError('Host response is too large.')
        try:
            result = json.loads(raw)
        except (ValueError, RecursionError):
            raise SessionError('Invalid host companion response.') from None
        if isinstance(result,dict) and str(result.get('protocol','')).startswith('vrostbridge-session-') and (result.get('protocol') != PROTOCOL or result.get('native_protocol') != NATIVE_PROTOCOL):
            raise SessionVersionError('The host uses a different VROSTBridge transport. Install this release on both PCs before starting.')
        if not isinstance(result, dict) or result.get('protocol') != PROTOCOL or not isinstance(result.get('host_id'), str) or not _ID.fullmatch(result['host_id']):
            raise SessionError('This is not a VROSTBridge session host.')
        return result

    def discover(self, host_ip='', cancel=None):
        """Return exactly one host; never silently select among different PCs."""
        if cancel is not None and cancel.is_set():raise SessionError('Session start cancelled.')
        preferred = host_ip or self.host_ip
        if preferred:
            try:
                return dict(self._request(preferred, '/hello', timeout=2), host_ip=preferred, source_ip=self._source_for(preferred))
            except SessionVersionError:
                raise
            except (OSError, ValueError):
                if host_ip:
                    raise
        interfaces = self._selected_interfaces()
        sockets, found, sources = [], {}, {}
        incompatible = False
        nonce = secrets.token_hex(16)
        try:
            raw = json.dumps({'protocol': PROTOCOL, 'nonce': nonce}).encode()
            for address, network in interfaces:
                interface=self._interface_for_source(address)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    pin_ipv4_interface(sock,interface)
                    # Broadcast requests and unicast replies do not form the
                    # ordinary reversed conntrack tuple. Use the dedicated
                    # destination port covered by our source-scoped UDP rule.
                    sock.bind((address, self.port))
                    sock.sendto(raw, (str(network.broadcast_address), self.port))
                    sock.sendto(json.dumps({'protocol':'vrostbridge-session-1','nonce':nonce}).encode(), (str(network.broadcast_address), self.port))
                    sockets.append(sock)
                    sources[sock] = address
                except OSError:
                    sock.close()
            deadline = time.monotonic() + 1.5
            while sockets and time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():raise SessionError('Session start cancelled.')
                readable, _, _ = select.select(sockets, [], [], min(.1,max(0, deadline-time.monotonic())))
                for sock in readable:
                    raw, (peer, port) = sock.recvfrom(1025)
                    try:
                        value = json.loads(raw)
                        valid=(len(raw)<=1024 and port==self.port and self._allowed(peer) and isinstance(value,dict)
                               and value.get('nonce')==nonce and isinstance(value.get('host_id'),str)
                               and _ID.fullmatch(value['host_id']))
                        if (valid and str(value.get('protocol','')).startswith('vrostbridge-session-')
                                and (value.get('protocol') != PROTOCOL or value.get('native_protocol') != NATIVE_PROTOCOL)):
                            incompatible = True
                        if valid and value.get('protocol')==PROTOCOL and value.get('native_protocol')==NATIVE_PROTOCOL:
                            found.setdefault(value['host_id'], dict(value, host_ip=peer, source_ip=sources[sock]))
                    except (ValueError, TypeError, RecursionError):
                        pass
        finally:
            for sock in sockets:
                sock.close()
        if not found and incompatible:
            raise SessionVersionError('The host uses a different VROSTBridge transport. Install this release on both PCs before starting.')
        if not found:
            raise SessionError('No VROSTBridge host found. Open the host companion and allow the client home-network connection if prompted, or enter the host IPv4 address.')
        if len(found) > 1:
            raise SessionError('More than one rendering host was found. Enter the host IPv4 address to choose one.')
        return next(iter(found.values()))

    def _credentials(self, lease=False):
        value = {'client_id': self.client_id, 'token': self.token}
        if lease:
            value['lease_id'] = self.lease_id
        return value

    def start(self, settings=None, host_ip='', cancel=None):
        settings = session_settings(settings)
        def check_cancelled():
            if self.closed.is_set() or (cancel is not None and cancel.is_set()):
                raise SessionError('Session start cancelled.')
        with self.actions:
            check_cancelled()
            with self.lock:
                self.state, self.message = 'discovering', 'Finding the rendering host.'
            try:
                # prepare_network handed these exact endpoints to the native
                # process. Never rediscover onto another adapter/host after that
                # handoff; a changed path requires a new explicit Start.
                host = self.discover(host_ip or (self.host_ip if self.source_ip else ''),cancel=cancel)
                check_cancelled()
                with self.lock:
                    if self.lease_id and self.host_ip and self.host_ip != host['host_ip']:
                        raise SessionError('Stop the existing host session before choosing another host.')
                    self.host_ip, self.host_id = host['host_ip'], host['host_id']
                    self.source_ip = host.get('source_ip') or self._source_for(self.host_ip)
                    self.source_interface=self._interface_for_source(self.source_ip)
                    self.request_id = secrets.token_hex(16)
                    self._remember_host(host)
                    self._persist()  # persist credentials before pairing, for loss/retry safety
                    ip, credentials = self.host_ip, self._credentials()
                    request = dict(credentials, request_id=self.request_id, settings=host_settings(settings, self.host_capabilities))
                self._request(ip, '/pair', credentials, timeout=15)
                check_cancelled()
                result = self._request(ip, '/start', request, timeout=15)
                lease = result.get('lease_id')
                if not isinstance(lease, str) or not _ID.fullmatch(lease):
                    raise SessionError('Host did not return a session lease.')
                with self.lock:
                    self.lease_id = lease
                    self._accept(result)
                    self._persist()
                check_cancelled()
                return self.snapshot()
            except (OSError, ValueError) as error:
                with self.lock:
                    self.state, self.message = 'error', str(error)[:500]
                raise

    def source_path(self, host_ip=''):
        """(host, local source address, adapter) this client would use for host_ip; no session state changes."""
        with self.actions:
            if self.closed.is_set():raise SessionError('Client companion is closing.')
            host = self.discover(host_ip or self.host_ip)
            source = host.get('source_ip') or self._source_for(host['host_ip'])
            return host, source, self._interface_for_source(source)

    def link_test(self, test, timeout=40, host_ip=''):
        """Ask the host to send a network test to this client (between sessions only).

        Returns (hello, sender summary). The caller opened its receiver first and
        passed its port in test['port'].
        """
        with self.actions:
            if self.closed.is_set():raise SessionError('Client companion is closing.')
            if self.lease_id:raise SessionError('Stop the session before testing the network.')
            host = self.discover(host_ip or self.host_ip)
            with self.lock:
                self.host_ip, self.host_id = host['host_ip'], host['host_id']
                self._remember_host(host)
                self._persist()
                ip, credentials = self.host_ip, self._credentials()
            if 'link-test' not in self.host_features:
                raise SessionError('Update the host PC for the network test (it needs 1.0.0-rc4 or later).')
            self._request(ip, '/pair', credentials, timeout=15)
            result = self._request(ip, '/linktest', dict(credentials, test=test), timeout=timeout)
            sender = result.get('sender') if isinstance(result.get('sender'), dict) else {}
            return host, sender

    def send_report(self, name, report, host_ip='', pair_timeout=15, upload_timeout=30):
        """Send this client's report to the paired host companion; returns the name it saved.

        Only discovery holds the action lock: the upload itself never delays Stop or
        Start (it uses no session state).
        """
        from launcher.reporting import encode
        data = base64.b64encode(encode(report)).decode('ascii')
        if len(data) + 512 > REPORT_LIMIT:
            raise SessionError('The report is too large to send; save it on this PC instead.')
        with self.actions:
            if self.closed.is_set():raise SessionError('Client companion is closing.')
            host = self.discover(host_ip or self.host_ip)
            with self.lock:
                if not self.lease_id:
                    self.host_ip, self.host_id = host['host_ip'], host['host_id']
                    self._remember_host(host)
                    self._persist()
                ip, credentials = host['host_ip'], self._credentials()
        if 'report-upload' not in (host.get('features') or []):
            raise SessionError('Update the host PC to receive reports (it needs 1.0.0-rc4.2 or later).')
        self._request(ip, '/pair', credentials, timeout=pair_timeout)
        if self.closed.is_set():raise SessionError('Client companion is closing.')
        # The host checks the credentials in the headers before it reads the (large) body.
        result = self._request(ip, '/report', dict(credentials, name=name, data=data), timeout=upload_timeout,
                               headers={'X-VROSTBridge-Client': credentials['client_id'],
                                        'X-VROSTBridge-Token': credentials['token']})
        saved = result.get('saved')
        if not isinstance(saved, str):
            raise SessionError('The host did not confirm the report.')
        return saved

    def _remember_host(self, host):
        """Capabilities and features from the host's hello (lock held)."""
        capabilities, features = host.get('capabilities'), host.get('features')
        self.host_capabilities = [item for item in capabilities if isinstance(item, str)][:16] if isinstance(capabilities, list) else []
        self.host_features = [item for item in features if item in FEATURES] if isinstance(features, list) else []

    def _accept(self, result):
        if result['host_id'] != self.host_id:
            raise SessionError('The host companion identity changed; start again to pair it.')
        self.last_reply = result
        self.last_seen = time.monotonic()
        self.state = result.get('state', 'error')
        self.message = result.get('message', '')

    def send_fovea(self, view):
        """Share the live eye-tracking view with the host page (RC4.3 hosts list 'fovea-view')."""
        with self.lock:
            if not self.lease_id or 'fovea-view' not in self.host_features:
                return False
            ip = self.host_ip
            body = dict(self._credentials(lease=True), view=view)
        self._request(ip, '/fovea', body, timeout=1)
        return True

    def heartbeat(self):
        with self.lock:
            if not self.lease_id:
                return self.snapshot()
            ip, lease = self.host_ip, self.lease_id
            credentials = self._credentials(lease=True)
            report = (RECOVERY_CAPABILITY in getattr(self, 'host_capabilities', ())
                      and getattr(self, 'stream_status', None) is not None)
        if report:
            try:
                credentials['stream'] = stream_report(self.stream_status())
            except Exception:
                pass  # a heartbeat never fails because of its optional report
        # 1.0.0.8: the bitrate the headset client's governor wants now ('bitrate-control' hosts).
        wanted = getattr(self, 'bitrate_request', None)
        if callable(wanted) and 'bitrate-control' in (getattr(self, 'host_features', None) or ()):
            try:
                value = wanted()
            except Exception:
                value = None
            if type(value) is int and 10 <= value <= 300:
                credentials['bitrate'] = value
        result = self._request(ip, '/heartbeat', credentials)
        with self.lock:
            if self.lease_id == lease:
                self._accept(result)
                if result.get('state') == 'waiting' and not result.get('lease_id'):
                    self.lease_id = self.request_id = ''
                    self._persist()
            return self.snapshot()

    def _heartbeats(self):
        while not self.closed.wait(HEARTBEAT_SECONDS):
            with self.lock:
                attempt=(self.host_ip,self.host_id,self.lease_id)
                active = bool(self.lease_id)
            if not active:
                continue
            try:
                self.heartbeat()
                failed_since = getattr(self, 'heartbeat_failed_since', None)
                if failed_since is not None:
                    self.log('Host heartbeat restored after ' + str(round(time.monotonic() - failed_since, 1)) + ' s.')
                    self.heartbeat_failed_since = None
            except (OSError, ValueError) as error:
                if getattr(self, 'heartbeat_failed_since', None) is None:
                    self.heartbeat_failed_since = time.monotonic()
                    log = getattr(self, 'log', None)
                    if log:
                        log('Host heartbeat failed: ' + str(error)[:200])
                with self.lock:
                    if self.lease_id and attempt==(self.host_ip,self.host_id,self.lease_id):
                        expired = time.monotonic() - self.last_seen >= LEASE_SECONDS
                        self.state = 'error' if isinstance(error, SessionError) or expired else 'disconnected'
                        self.message = 'Host heartbeat: ' + str(error)[:400]
                # The lease is retained for an explicit Stop/retry after a brief
                # connection interruption. The client never restarts host SteamVR;
                # an RC4.1 host may, for a stream that stays down (stream recovery).

    def stop(self):
        with self.actions:
            with self.lock:
                ip = self.host_ip
                # A Start response can be lost after the host accepted it. Its
                # authenticated status can recover OUR lease so Stop remains
                # immediate instead of waiting for lease expiry.
                uncertain = not self.lease_id and bool(self.request_id) and bool(ip)
                basic = self._credentials()
            if uncertain:
                try:
                    status = self._request(ip, '/status', basic)
                    if status.get('client_id') == self.client_id and _ID.fullmatch(str(status.get('lease_id', ''))):
                        with self.lock:
                            self.lease_id = status['lease_id']
                except (OSError, ValueError):
                    pass
            with self.lock:
                if not self.lease_id:
                    self.request_id = ''
                    self._persist()
                    self.state, self.message = 'waiting', 'Ready to start a session.'
                    return self.snapshot()
                credentials = self._credentials(lease=True)
            accepted = False
            try:
                result = self._request(ip, '/stop', credentials)
                accepted = True
                with self.lock:
                    self._accept(result)
                    self.lease_id = self.request_id = ''
                    self._persist()
                # The callback first collects the client's diagnostics. Retain
                # the native client's established control socket briefly while
                # that collection starts, without maintaining the host lease.
                deadline = time.monotonic() + STOP_GRACE_SECONDS
                while result.get('state') == 'stopping' and time.monotonic() < deadline:
                    time.sleep(.1)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    result = self._request(ip, '/status', basic, timeout=min(2, remaining))
                    if result.get('client_id') not in (None, self.client_id):
                        break  # our lease ended and a different client started
                    with self.lock:
                        self._accept(result)
                return self.snapshot()
            except (OSError, ValueError) as error:
                # Stop heartbeats even if the host is unreachable. Its own
                # deadline then shuts down SteamVR without a stranded session.
                with self.lock:
                    self.lease_id = self.request_id = ''
                    self.state = 'waiting'
                    self.message = ('Host accepted Stop; waiting for SteamVR to finish closing.' if accepted
                                    else 'Host unavailable; its session expires after the last heartbeat.')
                    self._persist()
                self.log(('Host Stop confirmation unavailable: ' if accepted else 'Host Stop could not be delivered: ') + str(error))
                return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'message': self.message, 'host_ip': self.host_ip,
                    'host_id': self.host_id, 'active': bool(self.lease_id), 'host': dict(self.last_reply),
                    'network': self.network_status(), 'host_features': list(self.host_features)}

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.stop()
        self.heartbeat_thread.join(6)
