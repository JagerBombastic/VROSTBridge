#!/usr/bin/env python3
"""Loopback desktop UI with a restricted, paired private-LAN session broker."""
import argparse
import atexit
import errno
import hashlib
from collections import deque
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.error
import urllib.parse

ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
PACKAGED=(ROOT/'PACKAGED.json').is_file()
sys.path.insert(0,str(ROOT))
from host.profile import configure,manual_client,write_session,session_client_address
from host.control import HostControl, require_home_network, pairing_network_private
from launcher.client_identity import ClientIdentity
from launcher.client_network import ClientNetwork
from launcher.network_path import NetworkMonitor
from launcher.performance_monitor import PerformanceMonitor
from launcher.timeline import SessionTimeline
from launcher.client_runtime import ClientRuntime, runtime_processes, steam_environment, process_identity, runtime_name
from launcher.client_runtime import crash_details as windows_crash_details
from launcher.session_link import SessionHost, SessionClient, SessionError, SessionVersionError, session_settings, QUALITY_CAPABILITY, PIPELINE_CAPABILITY, TIMING_LOCK_CAPABILITY, STREAM_CAPABILITY, RECOVERY_CAPABILITY, CLIENT_HOSTNAME, STREAM_STATES, ENCODER_PRESETS
from launcher.session_link import PORT as SESSION_PORT, _source_connection as session_link_connect
from launcher.headset import CLIENT_CODECS, CLIENT_RESOLUTIONS, FOVEATION_CHOICES, client_stream_preferences, resolve_stream_preferences, display_status, headset_metadata, headset_family, foveation_choice, foveation_parameters, foveated_size, stream_size
from launcher.stream_budget import BitrateGovernor, quality_ceiling_mbps
from launcher.session_tuning import SessionTuning
from launcher.gaze_provider import GazeCentre
from launcher.base_stations import BaseStations
from launcher.link_test import MIN_MBPS as LINK_MIN_MBPS, SEND_FRACTION as LINK_SEND_FRACTION
from launcher.headset import FOVEATION_REGIONS, FOVEATION_EDGES, FOVEATION_REGION_LABELS, FOVEATION_EDGE_LABELS, SHARPENING_LEVELS
from launcher.headset import BEYOND_LIGHTER_MODE, FAMILY_MODES, MODES, family_headset, parse_decoder_profiles, requested_refresh
from launcher.fovea import LiveView as FoveaView, parse_line as parse_fovea, view as fovea_view_checked
from launcher.session_power import SessionPower, system_environment
from launcher.steamvr_async import AsyncReprojectionCheck
from launcher.network_settings import NetworkSettings
from launcher.headset_audio import HeadsetAudio
from launcher.wifi_diagnostics import WifiDiagnostics
from launcher.adapter_counters import AdapterCounters, elsewhere as adapter_elsewhere
from launcher.wake_host import HostWaker, learn_mac, local_interfaces, normalise_mac, wake_targets
from launcher.network_test import NetworkTest, link_info, lossless_rate, pacing_mbps, path_tested, remember as remember_network_test, saved_for as saved_network_test
from launcher.network_test import MAX_AGE_S as NETWORK_TEST_MAX_AGE_S, failed_tests as network_test_failures, needs_retest as needs_network_retest, sender_timed as network_test_sender_timed
from launcher.client_identity import lan_addresses
from host.power import KeepAwake, SleepAfterStop, wake_readiness, wake_message, adapter_addresses, adapter_for_ip
from host import qos as host_qos
from host.stream_options import driver_features, stream_config, write_stream_config, write_init_hint, run_link_test
from host.stream_recovery import StreamRecovery
from host.audio import microphone_status
from host.events import HostEvents
from launcher.last_session import LastSessionRecord, driver_statistics
from launcher.diagnostics import Journal, DiagnosticServer, RemoteDiagnostics, steamvr_logs, clean
from launcher.baballonia import BaballoniaManager, data_root as baballonia_data_root
from launcher.eye_tracking import EyeCameras, EyeTracking
from launcher.validation import check_machine, SessionEvidence, host_loader_check
from launcher.reporting import attach_remote_session, stamped as stamp_report, report_name, ReceivedReports, filtered_driver_log, REPORT_NAME
from launcher.wifi_roaming import AccessPointLock
from launcher.connection_kind import describe as describe_connection
from launcher.graphics_limit import GpuClocks, GraphicsLimit, NOTICE_S as GRAPHICS_NOTICE_S, advice as graphics_advice, frames_arriving as graphics_frames_arriving
from launcher.output_scale import OutputScale
from launcher.power_balance import CpuClocks, SteamOSPower, advice as power_advice, gpu_clock_hint
from host.microphone_setup import MicrophoneSetup
from launcher.steam_shortcut import SteamShortcut
from launcher.windows_startup import WindowsStartup
from launcher.game_mode import GameMode, GameModeStartLog, LeaseRuntime, in_game_mode
from launcher.game_mode import connectors as headset_displays
from launcher.version import role_version
from launcher.wifi_session import WifiSession
from launcher.wifi_others import OtherWifiPause
from launcher.gaze_provider import free_udp_port
from host.addons import detect as detect_addons


# Client stream settings remembered per headset family (an Index and a Beyond differ).
PROFILE_KEYS = ('codec', 'bitrate', 'resolution', 'foveation', 'ten_bit', 'intra_refresh', 'auto_bitrate')
KEEP_WARM_CHOICES = (0, 5, 15, 30, 60)
SLEEP_AFTER_CHOICES = (0, 15, 30, 60, 120)
NETWORK_TEST_MAX_AGE = NETWORK_TEST_MAX_AGE_S  # a saved test is used for 30 days (launcher/network_test.py)


def state_directory():
    return Path(os.environ.get('LOCALAPPDATA', os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))))/'VROSTBridge'


def subprocess_options():
    """The desktop launcher must not create console windows for helper commands."""
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


def on_windows():
    """1.0.0.7: the headset client also runs on Windows. Decided when asked, never at import (tests patch it)."""
    return sys.platform == 'win32'


def platform_name():
    """'windows' or 'linux', for the pages (desktop.html shows the Windows client's own controls and wording)."""
    return 'windows' if on_windows() else 'linux'


# The installed launcher of each program (packaging/windows-launcher.c), for Windows sign-in startup.
WINDOWS_LAUNCHERS={'host':'VROSTBridge Host.exe','client':'VROSTBridge Client.exe'}


class RestartSuperseded(ValueError):
    """An automatic session restart (RC4.4) gave way to a Stop or Start by hand."""


BRAND_FILES={'vrostbridge.svg':'image/svg+xml','vrostbridge-mark.svg':'image/svg+xml','vrostbridge-name.svg':'image/svg+xml',
             'vrostbridge-logo.svg':'image/svg+xml','vrostbridge-256.png':'image/png','vrostbridge-512.png':'image/png',
             'favicon-32.png':'image/png','favicon.ico':'image/x-icon','vrostbridge.ico':'image/x-icon',
             'vrostbridge-tray-light.ico':'image/x-icon','vrostbridge-tray-dark.ico':'image/x-icon'}

def logo_resource(name='vrostbridge-256.png'):
    if name not in BRAND_FILES:return None
    candidates=[Path(__file__).with_name('brand')/name,ROOT/'brand'/name,Path(getattr(sys,'_MEIPASS',ROOT))/'brand'/name]
    return next((path for path in candidates if path.is_file()),None)


def brand_resource(name):
    """A file or folder under brand/ (next to the launcher, the package root or the frozen app)."""
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9]+)?',name or ''):return None
    candidates=[Path(__file__).with_name('brand')/name,ROOT/'brand'/name,Path(getattr(sys,'_MEIPASS',ROOT))/'launcher'/'brand'/name]
    return next((path for path in candidates if path.exists()),None)


FONT_FILES=('Inter-Variable.ttf','Outfit-Variable.ttf','Inter-OFL.txt','Outfit-OFL.txt')
def font_resource(name):
    """The desktop page's fonts (Inter, Outfit; SIL OFL 1.1)."""
    if name not in FONT_FILES:return None
    candidates=[Path(__file__).with_name('fonts')/name,ROOT/'assets'/'fonts'/name,ROOT/'launcher'/'fonts'/name,
                Path(getattr(sys,'_MEIPASS',ROOT))/'launcher'/'fonts'/name]
    return next((path for path in candidates if path.is_file()),None)


def verify_package():
    """Verify installed bytes against the release manifest, without changing files."""
    manifest=ROOT/'SHA256SUMS'
    if not manifest.is_file():raise RuntimeError('The package integrity manifest is missing. Repair or reinstall VROSTBridge.')
    seen=set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        digest, separator, name=line.partition('  ')
        if not separator or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise RuntimeError('The package integrity manifest is invalid. Repair or reinstall VROSTBridge.')
        relative=Path(name)
        if not name or '\\' in name or relative.is_absolute() or '..' in relative.parts or name in seen:
            raise RuntimeError('The package integrity manifest contains an invalid filename.')
        seen.add(name)
        path=ROOT/relative
        if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
            raise RuntimeError('A packaged file is missing: '+name+'. Repair or reinstall VROSTBridge.')
        calculated=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):calculated.update(chunk)
        if calculated.hexdigest() != digest:
            raise RuntimeError('A packaged file is damaged or changed: '+name+'. Repair or reinstall VROSTBridge.')
    if not seen:raise RuntimeError('The package integrity manifest is empty.')
    return len(seen)


def notify_desktop(message, error=False):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'VROSTBridge', 0x10 if error else 0x40)
        return
    for name, arguments in [('kdialog', ['--error' if error else '--msgbox', message]),
                            ('zenity', ['--error' if error else '--info', '--text', message])]:
        executable=shutil.which(name)
        if executable:
            subprocess.Popen([executable]+arguments, **subprocess_options())
            return
    print(message, file=sys.stderr)


def open_desktop(url):
    # Frozen Python libraries must not override the browser's system libraries.
    library_path=os.environ.get('LD_LIBRARY_PATH')
    if sys.platform.startswith('linux') and getattr(sys, 'frozen', False):
        if 'LD_LIBRARY_PATH_ORIG' in os.environ:os.environ['LD_LIBRARY_PATH']=os.environ['LD_LIBRARY_PATH_ORIG']
        else:os.environ.pop('LD_LIBRARY_PATH', None)
    try:
        try:opened=webbrowser.open(url)
        except OSError:opened=False
        if not opened:
            notify_desktop('VROSTBridge is running, but your default browser could not be opened.\n\n'
                           'Copy this address into a browser on this computer:\n'+url+
                           '\n\nOpening VROSTBridge again will retry.')
        return opened
    finally:
        if library_path is not None:os.environ['LD_LIBRARY_PATH']=library_path
        else:os.environ.pop('LD_LIBRARY_PATH', None)



def companion_command():
    """How to start this companion again (1.0.0.8: the base stations' off command at closing): the frozen program,
    or Python with the source tree's entry point."""
    if getattr(sys,'frozen',False):return [sys.executable]
    return [sys.executable,str(Path(__file__).resolve().parent/'bootstrap.py')]


class _AnyEvent:
    """Cancellation that follows the user's cancel event plus a local cancel."""
    def __init__(self, parent):
        self.parent, self.local = parent, threading.Event()
    def cancel(self):
        self.local.set()
    def is_set(self):
        return self.parent.is_set() or self.local.is_set()
    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self.parent.wait(.05 if remaining is None else min(.05, remaining))
        return True

class DesktopInstance:
    """OS-held per-user lock, with an authenticated record for reopening the UI.

    The lock, rather than the PID file, determines ownership. The OS releases it
    on a crash. The token is stored only in the user's local state directory.
    """
    def __init__(self, role, directory=None):
        self.role=role
        self.directory=Path(directory) if directory is not None else state_directory()
        self.record_path=self.directory/(role+'-desktop.json')
        self.handle=None

    def acquire(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path=self.directory/(self.role+'-desktop.lock')
        handle=path.open('a+b')
        if path.stat().st_size == 0:handle.write(b'\0');handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):return False
            raise
        self.handle=handle
        return True

    def publish(self, port, token):
        temporary=self.record_path.with_suffix('.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as f:
            json.dump({'schema':1,'role':self.role,'pid':os.getpid(),'port':port,'token':token}, f)
        temporary.replace(self.record_path)

    def existing_url(self):
        record=json.loads(self.record_path.read_text(encoding='utf-8'))
        if record.get('role') != self.role or type(record.get('pid')) is not int:raise ValueError('Invalid desktop record')
        port=record.get('port');token=record.get('token')
        if type(port) is not int or not 1 <= port <= 65535:raise ValueError('Invalid desktop port')
        if not isinstance(token, str) or not 16 <= len(token) <= 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in token):
            raise ValueError('Invalid desktop token')
        base='http://127.0.0.1:'+str(port)
        request=urllib.request.Request(base+'/status', headers={'X-VROSTBridge-Token':token})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:status=json.load(response)
        if status.get('pid') != record['pid'] or status.get('role') != self.role:raise ValueError('Desktop process did not match')
        return base+'/#'+token

    def reopen(self):
        deadline=time.monotonic()+4
        while True:
            try:return open_desktop(self.existing_url())
            except (OSError, ValueError, KeyError):
                if time.monotonic() >= deadline:break
                time.sleep(.1)
        raise RuntimeError('VROSTBridge is already starting or stopping. Wait a few seconds and open it again. If it remains unavailable, close VROSTBridge in Task Manager. Your settings are preserved.')

    def close(self):
        if self.handle is None:return
        try:
            self.record_path.unlink(missing_ok=True)
        finally:
            # Closing the descriptor releases the OS lock on both platforms.
            self.handle.close();self.handle=None

class HostNetworkNotReady(ValueError):
    """Only a read-only network check may be retried at sign-in."""


def timing_number(timings,key,kind=float):
    """A number from a native TIMING line's key=value fields, or None (1.0.0.9)."""
    try:
        value=float((timings or {}).get(key))
    except (TypeError,ValueError):
        return None
    if value!=value or value in (float('inf'),float('-inf')):return None
    return int(value) if kind is int else value


class Application:
    HOST_NETWORK_RETRY_MAX = 30

    def __init__(self,role):
        self.role=role
        self.process=None
        self.eye=None  # RC4.5: Beyond 2e eye tracking (cameras, Baballonia, gaze relay), client only
        self.session_gaze=None  # whether this session's sharp region follows the eyes (decided at Start)
        self.session_eye_on=None  # whether Eye tracking was on for this session (the switch at Start)
        self.gaze_socket=None
        self.runtime_directory=None
        self.build=None
        self.readiness=None
        self.evidence=SessionEvidence()
        self.owns_host=False
        self.lock=threading.RLock()
        self.action_lock=threading.Lock()
        self.report_send_lock=threading.Lock()  # one report upload at a time, never under action_lock
        self.metrics={}
        self.last_metrics=0
        self.last_exit=None
        self.startup_error=''
        self.autostart_cancelled=threading.Event();self.session_cancelled=threading.Event()
        self.autostart_attempted=False
        self.stopping=None
        self.lines=deque(maxlen=120)
        self.token=secrets.token_urlsafe(32)
        self.host=ROOT/'dist'/'host'
        self.client=self.native_client_path()
        self.settings={'microphone':False,'gaze':False,'software':False,'codec':'H264','bitrate':80}
        self.session_link=None;self.network_monitor=None;self.performance_monitor=None;self.host_prepared=False;self.host_preparation_attempted=False;self.closing=threading.Event();self.network_guard_lock=threading.Lock();self.network_guard_time=0;self.shutdown_started=threading.Event()
        if role=='client':self.settings.update(lightweight=True,host_ip='',late_frame_catchup=True,motion_prediction=True,codec='Auto',resolution='automatic',encoder='speed',timing_lock=True,cached_headset=None,decoder_profiles=None,prediction_acceleration=True,
                                               foveation='auto',ten_bit=True,intra_refresh=False,auto_bitrate=True,pacing=True,keep_warm_minutes=0,keep_awake=True,
                                               headset_audio=True,wake_host=True,headset_hud=False,host_mac='',network_tests={},headset_profiles={},
                                               pause_other_wifi=True,stay_on_access_point=True,reaim=True,
                                               # 1.0.0.8: steer the GPU clock and power limit during sessions (Linux, steamos-manager);
                                               # the base stations off when VROSTBridge closes (and on when a session starts);
                                               # the eye tracker's straight ahead kept centred.
                                               balance_performance=True,base_stations_off=True,gaze_centre=True,
                                               # RC4.3: picture sharpening and the eye-tracked region/outside reduction.
                                               sharpening='medium',fov_region='medium',fov_edge='balanced',
                                               # RC4.3: the fused frame pass; off keeps RC4.2's two passes (troubleshooting).
                                               one_pass=True,
                                               # 1.0.0.11: the frame pass skips what the lenses never show; the picture is
                                               # shown smaller (90, then 80 %) while this PC's graphics fall behind.
                                               lens_area=True,keep_frame_rate=True)
        if role=='host':self.settings.update(auto_connect=True,sleep_after_minutes=0,session_client_addresses={})
        self._host_settings_stamp=None
        # 1.0.0.7: a Windows PC can have both programs installed; the client's log then has a name of its own.
        self.log_path=state_directory()/('client-latest.log' if role=='client' and on_windows() else 'latest.log')
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        self.log_path.write_text('',encoding='utf-8')
        self.journal=Journal(self.log_path.parent,role)
        self.playspace={};self.timings={};self.inputs={};self.audio={};self.video={};self.view_geometry={}
        self.settings_path=self.log_path.parent/(role+'-settings.json')
        try:
            saved=json.loads(self.settings_path.read_text(encoding='utf-8'))
            # Existing installs keep their exact codec and resolution choices,
            # including pre-resolution settings files. Auto is a fresh-install
            # default and an explicit user choice on upgrade.
            if role=='client' and isinstance(saved,dict):
                self.settings.update(codec='H264',resolution='compatibility')
            for key in self.settings:
                if type(self.settings[key]) is bool and type(saved.get(key)) is bool:self.settings[key]=saved[key]
                elif key=='codec' and saved.get(key) in (CLIENT_CODECS if role=='client' else ('H264','Hevc','AV1')):self.settings[key]=saved[key]
                elif key=='foveation' and saved.get(key) in FOVEATION_CHOICES:self.settings[key]=saved[key]
                elif key=='keep_warm_minutes' and saved.get(key) in KEEP_WARM_CHOICES and type(saved.get(key)) is int:self.settings[key]=saved[key]
                elif key=='sleep_after_minutes' and saved.get(key) in SLEEP_AFTER_CHOICES and type(saved.get(key)) is int:self.settings[key]=saved[key]
                elif key=='host_mac' and normalise_mac(saved.get(key)):self.settings[key]=normalise_mac(saved[key])
                elif key=='network_tests' and isinstance(saved.get(key),dict):
                    self.settings[key]={name:value for name,value in list(saved[key].items())[:8] if isinstance(name,str) and len(name)<=64 and isinstance(value,dict)}
                elif key=='headset_profiles' and isinstance(saved.get(key),dict):
                    self.settings[key]={family:self.valid_profile(value) for family,value in saved[key].items() if family in ('index','beyond','other') and self.valid_profile(value)}
                elif key=='bitrate' and type(saved.get(key)) is int and 10<=saved[key]<=300:self.settings[key]=saved[key]
                elif key=='host_ip' and isinstance(saved.get(key),str):self.settings[key]=saved[key][:64]
                elif key=='resolution' and saved.get(key) in CLIENT_RESOLUTIONS:self.settings[key]=saved[key]
                elif key=='encoder' and saved.get(key) in ENCODER_PRESETS:self.settings[key]=saved[key]
                elif key in ('sharpening','fov_region','fov_edge') and isinstance(saved.get(key),str) and saved[key] in {
                        'sharpening':SHARPENING_LEVELS,'fov_region':FOVEATION_REGIONS,'fov_edge':FOVEATION_EDGES}[key]:
                    self.settings[key]=saved[key]
                elif key=='cached_headset' and isinstance(saved.get(key),dict):
                    try:self.settings[key]=headset_metadata(saved[key])
                    except ValueError:pass
                elif key=='decoder_profiles' and isinstance(saved.get(key),dict) and isinstance(saved[key].get('hevc'),bool):  # 1.0.0.14
                    self.settings[key]={name:bool(saved[key].get(name)) for name in ('h264','hevc','hevc_main10','av1','av1_10bit')}
                    self.settings[key].update(backend=str(saved[key].get('backend',''))[:16],device=str(saved[key].get('device',''))[:96])
                elif key=='session_client_addresses' and isinstance(saved.get(key),dict):
                    self.settings[key]={name:value for name,value in list(saved[key].items())[-8:]
                                        if isinstance(name,str) and CLIENT_HOSTNAME.fullmatch(name) and isinstance(value,str) and len(value)<=15}
        except (OSError,ValueError,AttributeError,TypeError):pass  # a damaged settings file never keeps VROSTBridge closed
        if role=='client':self.settings['lightweight']=True
        # RC4 system integration. Each helper reports failures and never blocks a session.
        self.session_power=SessionPower(log=self.log,state_directory=self.log_path.parent) if role=='client' else None
        if self.session_power:
            # 1.0.0.7 (Windows): put back a power mode a session that did not stop normally left changed.
            threading.Thread(target=self.session_power.recover,daemon=True,name='VROSTBridge-power-recover').start()
        # 1.0.0.8: SteamVR 2.0 base stations off when VROSTBridge closes and on when a session starts (when chosen).
        self.base_stations=BaseStations(self.log_path.parent,log=self.log) if role=='client' else None
        # 1.0.0.8: the GPU clock and power limit during sessions (steamos-manager on Linux); set back after a crash too.
        self.session_tuning=SessionTuning(log=self.log,state_directory=self.log_path.parent) if role=='client' and not on_windows() else None
        self.tuning_started=False
        if self.session_tuning:
            threading.Thread(target=self.session_tuning.recover,daemon=True,name='VROSTBridge-tuning-recover').start()
        self.async_reprojection=AsyncReprojectionCheck(log=self.log,state_directory=self.log_path.parent) if role=='client' else None
        self.network_settings=NetworkSettings(log=self.log) if role=='client' else None  # 1.0.0.14: one network twice, the receive buffer
        self.video_path_warned=False;self.video_path_checked=0.0;self.video_path=None
        self.headset_audio=HeadsetAudio(log=self.log) if role=='client' else None
        self.wifi_diagnostics=WifiDiagnostics(log=self.log) if role=='client' else None
        self.host_waker=HostWaker(log=self.log) if role=='client' else None
        self.wifi_sampler=None;self.native_status='';self.hud_sent=0;self.hud_text=''
        self.keep_awake=KeepAwake(log=self.log) if role=='host' else None
        # RC4.3: one-step virtual microphone (VB-CABLE by VB-Audio) for the headset microphone.
        self.microphone_setup=MicrophoneSetup(ROOT/'host/VROSTBridgeAudioProbe.exe',self.log_path.parent/'microphone',
                                              log=self.log,version=role_version('host')) if role=='host' else None
        if self.microphone_setup and os.name=='nt':
            threading.Thread(target=self.microphone_setup.check,daemon=True,name='VROSTBridge-microphone-check').start()
        self.sleep_after_stop=SleepAfterStop(self.settings.get('sleep_after_minutes') or None,self.host_idle,log=self.log) if role=='host' else None
        self.wake_info={'ready':False,'message':'Checking Wake-on-LAN settings…','adapters':[],'error':False};self.host_adapters=[];self.wake_checked=0
        self.qos_status=None;self.parked=None;self.session_key=None;self.keep_warm_minutes=0;self.force_full_stop=False;self._features_cache=None
        self.session_intra_refresh=False;self.gradual_sent=False;self.client_generation=0
        self.stream_up=False;self.stream_up_at=None;self.stream_ever_up=False;self.decoded_seen=None;self.decoded_at=-1e9
        # RC4.3: this PC's graphics holding the stream below refresh (see launcher/graphics_limit.py).
        self.graphics_limit=GraphicsLimit();self.graphics_event=None;self.reaim_paused=False;self.graphics_notice_until=0
        # 1.0.0.11: the picture shown smaller while this PC's graphics fall behind (launcher/output_scale.py).
        # scale_lock keeps choosing and sending a size in one piece, so a size chosen just before the switch is
        # turned off cannot reach the client after the switch's 'scale 100'.
        self.output_scale=OutputScale();self.scale_event=None;self.scale_lock=threading.Lock()
        self.gpu_clocks=GpuClocks()  # RC4.4: tells a power-starved GPU from one at its real limit
        # 1.0.0.4: the processor held back by the shared power limit (a fixed GPU clock), and steamos-manager's settings.
        self.cpu_clocks=CpuClocks();self.steamos_power=SteamOSPower();self.steamos_settings={}
        self.timeline=SessionTimeline();self.timeline_sample=None  # 1.0.0.4: the session in 10 s steps, for reports
        self.adapter_counters=AdapterCounters()  # 1.0.0.13: the network adapters' drop counters over a session, for reports
        self.power_notice_until=0;self.power_held=None;self.power_history={'held_samples':0,'lowest_cpu_mhz':None}
        # RC4.4: SteamVR's compositor on this PC lost mid-session (see local_compositor_lost).
        self.runtime_restart=None;self.runtime_restarting=False;self.runtime_restart_at=None;self.compositor_missing=0
        self.manual_actions=0  # Stops and Starts by hand: an automatic restart gives way to them
        self.runtime_seen=None;self.compositor_stalled=False;self.compositor_replacement=None
        self.runtime_quit_requested=None  # the native client SteamVR asked to quit (a quit, not a crash)
        self._gaze_totals=None;self._gaze_recent=None
        self.start_clock=None;self.milestones={}
        if role=='host':self.windows_startup=WindowsStartup(ROOT/WINDOWS_LAUNCHERS['host'])
        # 1.0.0.7: the Windows client can start quietly in the tray at sign-in too (its own Run value).
        elif on_windows():self.windows_startup=WindowsStartup(ROOT/WINDOWS_LAUNCHERS['client'],role='client')
        else:self.windows_startup=None
        self.host_control=HostControl(self.host,self.log) if role=='host' else None
        self.stream_recovery=StreamRecovery() if role=='host' else None
        self.session_address=None;self.session_address_attempt=0
        if self.host_control:self.host_control.recovery=self.stream_recovery_tick
        self.client_identity=ClientIdentity() if role=='client' else None
        # Windows (1.0.0.7): the firewall rules admit the native client and this companion's own Python.
        self.client_network=(ClientNetwork(client_executable=self.client) if on_windows() else ClientNetwork()) if role=='client' else None
        self.wifi_session=WifiSession(log=self.log) if role=='client' else None
        self.other_wifi=OtherWifiPause(self.log_path.parent,log=self.log) if role=='client' else None
        # RC4.2: keep the session's Wi-Fi on one access point (no background scans).
        self.ap_lock=AccessPointLock(self.log_path.parent,log=self.log) if role=='client' else None
        # Reconnect adapters an earlier companion paused and could not restore, and undo
        # an access-point lock left by a session that did not stop normally.
        if self.other_wifi:threading.Thread(target=self.other_wifi.recover,daemon=True,name='VROSTBridge-wifi-recover').start()
        if self.ap_lock:threading.Thread(target=self.ap_lock.recover,daemon=True,name='VROSTBridge-roaming-recover').start()
        self.connection_cache=(0.0,'',None);self.alignment_check={'state':'idle'}
        self.received_reports=ReceivedReports(self.log_path.parent/'reports'/'received') if role=='host' else None
        if self.received_reports:  # 1.0.0.9: only the newest three are kept, also from an earlier version's folder
            threading.Thread(target=self.received_reports.prune,daemon=True,name='VROSTBridge-reports-prune').start()
        self.client_runtime=ClientRuntime(self.client,self.log_path.parent,self.log) if role=='client' else None
        # Steam's Game Mode, its library entry and the headset display lease belong to the Bazzite client; the
        # Windows client (1.0.0.7) has neither: SteamVR on Windows drives the headset itself.
        game_mode=role=='client' and not on_windows()
        self.game_mode_setup=GameMode(ROOT,self.log) if game_mode else None
        # Artwork from the installed `current` folder, so the shortcut's icon survives updates.
        installed_art=self.game_mode_setup.executable.parent/'brand'/'steam' if self.game_mode_setup else None
        self.steam_shortcut=SteamShortcut(ROOT,self.game_mode_setup.executable if self.game_mode_setup else ROOT/'VROSTBridge-Client',
                                          installed_art if installed_art and installed_art.is_dir() else (brand_resource('steam') or ROOT/'brand'/'steam'),
                                          (self.game_mode_setup.directory/'steam-shortcut.json') if self.game_mode_setup else self.log_path.parent/'steam-shortcut.json',
                                          log=self.log,in_game_mode=in_game_mode) if game_mode else None
        if self.steam_shortcut:
            # An update with new artwork brings VROSTBridge's own Steam library images up to date.
            threading.Thread(target=self.steam_shortcut.refresh_artwork,daemon=True,name='VROSTBridge-steam-artwork').start()
        self.lease_runtime=LeaseRuntime(ROOT,self.game_mode_setup,self.log,self.display_failure) if game_mode else None
        # 1.0.0.10: the last Start in Game Mode, kept for the reports (game_mode.GameModeStartLog).
        self.game_mode_log=GameModeStartLog(self.log_path.parent/'game-mode-last-start.json') if game_mode else None
        if role=='client':
            # RC4.5: VROSTBridge sets up and runs Baballonia and the eye cameras (launcher/eye_tracking.py).
            # 1.0.0.8: the eye tracker's straight ahead kept centred, per calibration (launcher/gaze_provider.py).
            self.gaze_centre=GazeCentre(self.log_path.parent/'gaze-centre.json',
                                        model=lambda:self.eye.manager.model_path() if getattr(self,'eye',None) is not None else '',
                                        enabled=lambda:bool(self.settings.get('gaze_centre',True)))
            self.eye=EyeTracking(BaballoniaManager(log=self.log,state=self.log_path.parent/'baballonia-setup.json'),
                                 EyeCameras(self.camera_executable,baballonia_data_root().parent/'cameras',log=self.log),
                                 log=self.log,game_mode=in_game_mode,centre=self.gaze_centre)
            threading.Thread(target=self.eye.recover,daemon=True,name='VROSTBridge-eye-recover').start()
        self.diagnostic_server=DiagnosticServer(lambda:self.process.pid if self.process and self.process.poll() is None else None,self.diagnostic_summary,self.local_report,self.log,session_peer=lambda:self.session_link.snapshot() if self.session_link else {}) if role=='client' else None
        self.remote_diagnostics=RemoteDiagnostics(self.log_path.parent,lambda:self.host_control.snapshot()['clients'],self.host_control.apply_playspace,self.log,session_peer=lambda:self.session_link.snapshot() if self.session_link else {}) if role=='host' else None
        self.host_events=HostEvents(lambda:self.host_control.api if self.host_control.active and self.host_control.snapshot()['available'] else None,self.journal) if role=='host' else None
        # 1.0.0.14: the host's last streamed session, kept across restarts for the report (launcher/last_session.py).
        self.last_session=LastSessionRecord(self.log_path.parent) if role=='host' else None;self.host_streamed=False
        # RC4.3 eye-tracked foveation panel: the live view, in memory only (launcher/fovea.py).
        self.fovea_view=FoveaView();self._gaze_rate=None
        if role=='host':
            self.refresh_host_settings()
            try:self.session_link=SessionHost(self.log_path.parent,self.start_host_session,self.stop_host_session,self.host_control.snapshot,self.allow_session_pairing,self.log,allow_start=self.private_session_allowed,capabilities=self.session_capabilities,telemetry=self.session_telemetry,
                                              features=self.session_features,link_test=self.host_link_test,host_info=self.session_host_info,report=self.receive_client_report,
                                              fovea=self.fovea_view.update,bitrate=self.host_control.set_bitrate)
            except OSError as error:self.startup_error='Cannot open the session listener: '+str(error);self.log(self.startup_error)
        else:
            self.session_link=SessionClient(self.log_path.parent,self.log)
            self.session_link.stream_status=self.stream_report
            # 1.0.0.8: the running stream's bitrate, stepped by launcher/stream_budget.py and sent with heartbeats.
            self.bitrate_governor=BitrateGovernor()
            self.session_link.bitrate_request=self.governed_bitrate
            threading.Thread(target=self.share_fovea,daemon=True,name='VROSTBridge-fovea').start()
            self.network_test=NetworkTest(self.session_link,self.log,wifi_sample=self.wifi_diagnostics.sample)
            self.network_monitor=NetworkMonitor(self.session_link.network_status,
                lambda:self.process.pid if self.process and self.process.poll() is None else None,
                self.journal.record)
            self.performance_monitor=PerformanceMonitor(
                lambda:{'active':self.process is not None and self.process.poll() is None,
                        'phase':str(self.metrics.get('state','starting'))},self.journal.record)
            threading.Thread(target=self.monitor_session,daemon=True).start()
    @staticmethod
    def native_client_path():
        """The native client of this installation or source tree: VROSTBridgeClient beside the package (Bazzite)
        or in dist/client; on Windows (1.0.0.7) VROSTBridgeClient.exe beside the launcher, or in
        dist/client-windows (scripts/Build-Client-Windows.py) in a source tree."""
        if on_windows():
            for candidate in (ROOT/'VROSTBridgeClient.exe',ROOT/'dist'/'client-windows'/'VROSTBridgeClient.exe'):
                if candidate.is_file():return candidate
            return ROOT/'VROSTBridgeClient.exe'
        return ROOT/'VROSTBridgeClient' if (ROOT/'VROSTBridgeClient').is_file() else ROOT/'dist'/'client'/'VROSTBridgeClient'
    def save_settings(self):
        temporary=self.settings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.settings),encoding='utf-8')
        temporary.replace(self.settings_path)

    # RC4.5 eye tracking ----------------------------------------------------------------
    @property
    def relay(self):
        """The gaze relay on 127.0.0.1:9005 while eye tracking runs (owned by self.eye)."""
        eye=getattr(self,'eye',None)
        return eye.relay if eye is not None else None
    @staticmethod
    def camera_executable():
        """The Beyond 2e eye-camera helper (go-bsb-cams) of this install or source tree."""
        for path in (ROOT/'tools/go-bsb-cams',ROOT/'dist/tools/go-bsb-cams',ROOT/'dist/client/tools/go-bsb-cams'):
            if path.is_file():return path
        return ROOT/'tools/go-bsb-cams'
    def eye_ready(self):
        """Whether the next Start's sharp region follows the eyes: Eye tracking on, a headset with eye
        cameras (the Beyond; not yet known before the first session), and VROSTBridge's Baballonia set up
        and calibrated (its general model is no guide to where you look; until then the fixed Balanced
        region is used, while Baballonia still runs so it can be calibrated)."""
        if not self.settings.get('gaze'):return False
        if self.current_family() not in (None,'beyond'):return False
        if self.eye is None:return True
        manager=self.eye.manager
        return manager.installed() and bool(manager.calibration().get('calibrated'))
    def gaze_planned(self):
        """The running session's choice (made at Start), otherwise what the next Start would use."""
        if self.session_gaze is not None and self.running():return self.session_gaze
        return self.eye_ready()
    def eye_for_headset(self,family):
        """RC4.5: once the session's headset is known, eye tracking runs only for one with eye cameras.
        The session's choice (the sharp region follows the eyes or not) was made at Start for the last
        headset (current_family() until this headset is saved) and is made again only when this one is
        of another family. A host started early was told the Start's choice, and only another headset
        restarts it (the session settings carry the headset), so a calibration saved meanwhile waits for
        the next Start instead of leaving the host and this PC disagreeing."""
        if self.eye is None or not self.session_eye_on:return
        if family!=self.current_family():
            manager=self.eye.manager
            self.session_gaze=family=='beyond' and manager.installed() and bool(manager.calibration().get('calibrated'))
        if not self.gaze_socket:return
        wanted=family=='beyond';owned='session' in self.eye.owners
        if wanted and not owned:
            try:self.eye.begin('session',self.gaze_socket)
            except OSError as error:self.log('Eye tracking unavailable for this session: '+str(error)[:200])
        elif owned and not wanted:
            self.eye.end('session');self.log('Eye tracking: this headset has no eye cameras; it stays off for this session.')
    def eye_status(self):
        """Eye tracking for the pages and Game Mode (never where you look)."""
        if self.eye is None:return None
        try:value=self.eye.status()
        except Exception as error:value={'phase':'error','message':str(error)[:200]}
        value['enabled']=bool(self.settings.get('gaze'))
        value['session_gaze']=self.session_gaze if self.running() else None
        # 1.0.0.8: the earlier calibration the card can go back to (file name and date only).
        try:previous=self.eye.manager.previous_calibration() if self.eye.manager.installed() else None
        except Exception:previous=None
        value['previous_calibration']={'name':previous['name'],'date':previous['date']} if previous else None
        return value
    def baballonia_log_tail(self,lines=60):
        """The end of Baballonia's own log for reports (its start-up, camera and errors)."""
        if self.eye is None:return None
        manager=self.eye.manager
        # 1.0.0.7: Baballonia's own log is where the manager says (its folder differs on Windows).
        path=manager.log_path() if callable(getattr(manager,'log_path',None)) else manager.root/'baballonia.log'
        try:
            with path.open('rb') as handle:
                handle.seek(max(0,path.stat().st_size-48000))
                text=handle.read().decode('utf-8','replace')
        except OSError:return None
        return [line[:300] for line in text.splitlines()[-lines:]]

    # 1.0.0.8: switches that apply at once (client-option), also during a session.
    LIVE_OPTIONS={'balance_performance':'Balance performance during sessions',
                  'base_stations_off':'Base stations off when VROSTBridge closes (on when a session starts)',
                  'gaze_centre':'Keep my gaze centred',
                  'keep_frame_rate':'Keep the frame rate when this PC falls behind'}
    # 1.0.0.8: the stream settings that keep a session smooth on most headset PCs (Use recommended settings). Picture
    # size and bitrate on Auto: this PC's headset, the network test and launcher/stream_budget.py decide them, and the
    # bitrate follows how the stream runs; foveation on Auto (eye-tracked on a Beyond 2e with eye tracking set up).
    RECOMMENDED={'codec':'Auto','resolution':'automatic','foveation':'auto','auto_bitrate':True,'pacing':True,'ten_bit':True,
                 'late_frame_catchup':True,'motion_prediction':True,'timing_lock':True,'reaim':True,'one_pass':True,
                 'encoder':'speed','keep_awake':True,'balance_performance':True,'gaze_centre':True,'fov_region':'medium',
                 'fov_edge':'balanced','lens_area':True,'keep_frame_rate':True}
    def apply_recommended_settings(self):
        values=dict(self.RECOMMENDED)
        # Gradual loss recovery is for Wi-Fi, where single packets go missing; on a cable keyframes recover faster.
        kind=link_info(self.stream_interface(),wifi_sample=self.wifi_diagnostics.sample).get('kind')
        values['intra_refresh']=kind=='wifi'
        changed=[key for key,value in values.items() if self.settings.get(key)!=value]
        self.settings.update(values);self.remember_profile();self.save_settings()
        self.log('Stream settings set to the recommended ones'+(' ('+', '.join(changed)+' changed).' if changed else ' (already in use).'))
    CLIENT_OPTIONS=('microphone','gaze','software','late_frame_catchup','motion_prediction','timing_lock','ten_bit','intra_refresh',
                    'auto_bitrate','pacing','keep_awake','headset_audio','wake_host','headset_hud','pause_other_wifi',
                    'stay_on_access_point','reaim','one_pass','balance_performance','base_stations_off','gaze_centre',
                    'lens_area','keep_frame_rate')
    def apply_client_options(self,body):
        """Validate and store the client's switches from a start or preferences request."""
        for key in self.CLIENT_OPTIONS:
            if key in body:
                if type(body[key]) is not bool:raise ValueError('Invalid client setting: '+key)
                self.settings[key]=body[key]
        if 'foveation' in body:
            if body['foveation'] not in FOVEATION_CHOICES:raise ValueError('Invalid client setting: foveation')
            self.settings['foveation']=body['foveation']
        if 'keep_warm_minutes' in body:
            if type(body['keep_warm_minutes']) is not int or body['keep_warm_minutes'] not in KEEP_WARM_CHOICES:raise ValueError('Invalid client setting: keep_warm_minutes')
            self.settings['keep_warm_minutes']=body['keep_warm_minutes']
        if 'encoder' in body:
            if body['encoder'] not in ENCODER_PRESETS:raise ValueError('Invalid client setting: encoder')
            self.settings['encoder']=body['encoder']
        for key,choices in (('fov_region',FOVEATION_REGIONS),('fov_edge',FOVEATION_EDGES),('sharpening',SHARPENING_LEVELS)):
            if key in body:
                if not isinstance(body[key],str) or body[key] not in choices:raise ValueError('Invalid client setting: '+key)
                self.settings[key]=body[key]

    # Per-headset stream settings ------------------------------------------------
    @staticmethod
    def valid_profile(value):
        """A saved per-headset profile with only valid values, or None."""
        if not isinstance(value,dict):return None
        result={}
        try:result.update(client_stream_preferences({key:value[key] for key in ('codec','bitrate','resolution') if key in value}))
        except ValueError:return None
        if value.get('foveation') in FOVEATION_CHOICES:result['foveation']=value['foveation']
        for key in ('ten_bit','intra_refresh','auto_bitrate'):
            if type(value.get(key)) is bool:result[key]=value[key]
        return result
    def current_family(self):
        cached=self.settings.get('cached_headset')
        try:return headset_family(headset_metadata(cached)) if cached else None
        except ValueError:return None
    def connected_family(self):
        """1.0.0.13 (Linux): 'index' or 'beyond' when exactly one known headset display is connected now, from its
        EDID (launcher/game_mode.py, the same reading Game Mode uses); else None. Read at most every 5 seconds."""
        if self.role!='client' or not sys.platform.startswith('linux'):return None
        now=time.monotonic();cached=getattr(self,'_connected_family',None)
        if cached and now-cached[0]<5:return cached[1]
        try:models={row.get('model') for row in headset_displays()}
        except Exception:models=set()
        known={'Valve Index':'index','Bigscreen Beyond':'beyond'}
        families={known[m] for m in models if m in known}
        value=families.pop() if len(families)==1 and len(models)==1 else None
        self._connected_family=(now,value);return value
    def known_headset(self):
        """1.0.0.13: (headset, source) to offer stream choices for, so they can be chosen before a session starts:
        this run's from SteamVR ('detected'), else the last verified one ('last') unless the headset display connected
        now is another kind (Linux), else that kind as its display describes it ('display'); (None, None) when nothing
        is known (a first start on Windows)."""
        live=self.client_runtime.headset if self.client_runtime else None
        if live:return live,'detected'
        try:cached=headset_metadata(self.settings.get('cached_headset')) if self.settings.get('cached_headset') else None
        except ValueError:cached=None
        connected=self.connected_family()
        if cached and connected in (None,headset_family(cached)):return cached,'last'
        if connected:return family_headset(connected),'display'
        return None,None
    def display_info(self):
        """The display section of the status: the choices and sizes for the known headset, and (1.0.0.13) what the
        last start asked SteamVR for and what the headset ran."""
        result=display_status(self.settings,*self.known_headset())
        note=getattr(self,'refresh_note',None)
        if note:result['refresh_note']=note
        return result
    def session_refresh(self):
        """1.0.0.13: the refresh rate to ask SteamVR on this PC for at the next Start (an Index rate chosen for the
        known headset; launcher/headset.py requested_refresh), or None to leave it as it is set."""
        headset,_=self.known_headset()
        try:family=headset_family(headset_metadata(headset)) if headset else None
        except ValueError:family=None
        return requested_refresh(self.settings.get('resolution','automatic'),family) if family else None
    def refresh_outcome(self,headset,display=None):
        """1.0.0.13: a plain note when an Index does not run the rate chosen for it, else None. A Beyond has no choice:
        its stream follows the mode it is in."""
        try:physical=headset_metadata(headset);family=headset_family(physical)
        except ValueError:return None
        mode=MODES.get(self.settings.get('resolution'))
        if not mode or mode['family']!=family:return None
        wanted,running=float(mode['refresh_hz']),float(physical['refresh_hz'])
        if abs(running-wanted)<1:return None
        size=(' The stream is '+str(display['stream_width'])+' x '+str(display['stream_height'])+' per eye at '+('%g'%running)+' Hz.'
              if display and display.get('stream_width') else '')
        return ('SteamVR runs the Index at '+('%g'%running)+' Hz, not the '+('%g'%wanted)+' Hz chosen'+
                (': 144 Hz is experimental, and this graphics card or its driver may not drive it.' if wanted>=144 else '.')+size)
    def remember_decoders(self,profiles):
        """1.0.0.14: what this PC's video decoder handles, from the native client's decoder-profile line, kept in the
        settings so Auto codec can choose from it at the next Start (launcher/headset.py automatic_codec) and the report
        can say. Logged when it changes."""
        if not isinstance(profiles,dict):return
        with self.lock:
            if self.settings.get('decoder_profiles')==profiles:return
            self.settings['decoder_profiles']=profiles
        self.save_settings()
        self.log('DECODERS '+profiles.get('backend','?')+' on '+(profiles.get('device') or 'this GPU')+': '
                 +', '.join(name for name in ('h264','hevc','hevc_main10','av1','av1_10bit') if profiles.get(name))
                 +('' if profiles.get('hevc') else '; no HEVC, so Auto codec means H.264 here from the next Start'))
    def remember_profile(self,family=None):
        family=family or self.current_family()
        if family:
            self.settings['headset_profiles']=dict(self.settings.get('headset_profiles') or {},**{family:{key:self.settings[key] for key in PROFILE_KEYS}})
    def apply_profile(self,family,quality):
        """Switch to this headset's saved stream settings; returns the updated quality dict."""
        profile=(self.settings.get('headset_profiles') or {}).get(family)
        if not profile:return quality
        self.settings.update(profile)
        self.log('Using your saved stream settings for this '+{'index':'Valve Index','beyond':'Bigscreen Beyond'}.get(family,'headset')+'.')
        return dict(quality,**{key:profile[key] for key in ('codec','bitrate','resolution') if key in profile})

    # Host power, quick resume and features -------------------------------------
    def session_features(self):
        now=time.monotonic()
        if self._features_cache and now-self._features_cache[0]<30:return self._features_cache[1]
        value=driver_features(self.host);self._features_cache=(now,value)
        return value
    def host_link_test(self,request,peer,cancel):
        """Network test traffic towards the paired client that asked (never during a session)."""
        if self.closing.is_set():raise SessionError('The host companion is closing.')
        return run_link_test(request,peer,cancel)
    def refresh_wake_info(self,force=False):
        """Wake-on-LAN readiness and adapter addresses (PowerShell; slow, cached for 10 minutes)."""
        if sys.platform!='win32':return
        if not force and time.monotonic()-self.wake_checked<600:return
        self.wake_checked=time.monotonic()
        try:self.wake_info=wake_readiness();self.host_adapters=adapter_addresses()
        except Exception as error:self.log('Wake-on-LAN check skipped: '+str(error)[:200])
        try:self.qos_status=host_qos.status()
        except Exception as error:self.qos_status={'state':'unknown','message':'Windows QoS status could not be read: '+str(error)[:160]}
    def session_host_info(self,local=None):
        if sys.platform=='win32' and time.monotonic()-self.wake_checked>=600 and not getattr(self,'_wake_refreshing',False):
            self._wake_refreshing=True
            def refresh():
                try:self.refresh_wake_info()
                finally:self._wake_refreshing=False
            threading.Thread(target=refresh,daemon=True,name='VROSTBridge-wake-info').start()
        adapter=adapter_for_ip(local,self.host_adapters) if local else None
        wired=bool(adapter and adapter.get('kind')=='wired')
        rows=self.wake_info.get('adapters',[])
        enabled={row.get('mac') for row in rows if row.get('wake_on_magic_packet') is True and row.get('wake_armed') is not False}
        message=str(self.wake_info.get('message',''))
        # Guidance for the adapter this session actually uses.
        if adapter and rows:
            try:message=wake_message(rows,self.wake_info.get('fast_startup'),preferred=adapter.get('name',''))
            except (KeyError,TypeError,ValueError):pass
        info={'wake':{'mac':adapter.get('mac') if adapter else None,'adapter':(adapter or {}).get('name','')[:64],
                      'link_mbps':(adapter or {}).get('link_mbps'),'wired':wired,
                      'ready':bool(wired and adapter.get('mac') in enabled),'message':message[:900]},
              'power':{'keep_awake':self._plain((self.keep_awake.status() if self.keep_awake else {}).get('state')),
                       'sleep_after':self._plain((self.sleep_after_stop.status() if self.sleep_after_stop else {}).get('minutes'))}}
        parked=self.parked
        if parked:info['resume']={'parked':True,'remaining_s':max(0,int(parked['deadline']-time.monotonic()))}
        return info
    @staticmethod
    def _plain(value):
        return value if isinstance(value,(str,int,float,bool,type(None))) else None
    def host_idle(self):
        """True only when no session or SteamVR is running (for Sleep after Stop)."""
        if self.closing.is_set() or self.running() or self.parked:return False
        link=self.session_link.snapshot() if self.session_link else {}
        if link.get('lease_id'):return False
        try:
            from host.control import windows_process_ids
            return not windows_process_ids()
        except Exception:return False
    def preference_key(self,preferences):
        """What decides the host SteamVR configuration; a resumed session must match it."""
        value={key:preferences.get(key) for key in preferences if key not in ('pacing_mbps','keep_warm_minutes','timing_lock')}
        # RC4.5: the eye tracking this session gets (the host's own switch, or the client's for its session).
        value.update(gaze=bool(self.settings['gaze']) or preferences.get('gaze') is True,auto_connect=self.settings['auto_connect'])
        return json.dumps(value,sort_keys=True)
    def park(self,minutes):
        token=secrets.token_hex(8)
        self.parked={'token':token,'key':self.session_key,'deadline':time.monotonic()+minutes*60}
        timer=threading.Timer(minutes*60,self.parked_timeout,args=(token,));timer.daemon=True;self.parked['timer']=timer;timer.start()
        self.log('Quick resume: host SteamVR stays open for '+str(minutes)+' minutes. Starting again within that time skips its startup; games on this PC keep running.')
    def unpark(self):
        parked,self.parked=self.parked,None
        if parked and parked.get('timer'):parked['timer'].cancel()
        return parked
    def parked_timeout(self,token):
        with self.action_lock:
            if not self.parked or self.parked['token']!=token:return
            self.unpark();self.log('Quick resume time ended; closing host SteamVR.')
            self.finish_host_stop()
    def finish_host_stop(self):
        """Full host stop (action lock held): SteamVR closed, PC may sleep again."""
        try:
            self.host_control.stop()
            if self.host_control.snapshot().get('runtime_state')=='stopped':self.evidence.finish()
        finally:
            if self.keep_awake:self.keep_awake.release()
            if self.sleep_after_stop and not self.closing.is_set():self.sleep_after_stop.arm()

    # Client: wake, network test, in-headset status -------------------------------
    def ensure_host_awake(self,cancel):
        """Discover the host; wake it with Wake-on-LAN when it does not answer and its address is known."""
        try:return self.session_link.discover(self.settings['host_ip'],cancel=cancel)
        except SessionVersionError:raise
        except (OSError,ValueError) as error:
            mac=self.settings.get('host_mac')
            # Wake only a host that is not answering at all, never for an ambiguous or rejected reply.
            silent=isinstance(error,OSError) or 'No VROSTBridge host found' in str(error) or 'timed out' in str(error).lower()
            if cancel.is_set() or not silent or not self.settings.get('wake_host') or not mac:raise
            host_ip=self.settings['host_ip'] or self.session_link.host_ip
            targets=wake_targets(host_ip,local_interfaces(lan_addresses()))
            self.log('The host PC did not answer ('+str(error)[:160]+'); sending Wake-on-LAN.')
            def answered():
                try:return self.session_link.discover(self.settings['host_ip'],cancel=cancel)
                except (OSError,ValueError):return None
            self.host_waker.discover=answered
            result=self.host_waker.wake_and_wait(mac,targets,timeout=120,cancel=cancel)
            if not result['woken']:raise SessionError(result['message'])
            return self.session_link.discover(self.settings['host_ip'],cancel=cancel)
    def learn_host_mac(self):
        """Remember the host adapter address for Wake-on-LAN after a successful contact."""
        link=self.session_link.snapshot()
        mac=normalise_mac(((link.get('host') or {}).get('host_info') or {}).get('wake',{}).get('mac') or '') or learn_mac(link.get('host_ip',''))
        if mac and mac!=self.settings.get('host_mac'):
            self.settings['host_mac']=mac;self.save_settings();self.log('Host network adapter remembered for Wake-on-LAN.')
    def stream_fps(self):
        cached=self.settings.get('cached_headset')
        try:return float(headset_metadata(cached)['refresh_hz']) if cached else 90.0
        except ValueError:return 90.0
    def host_feature_list(self):
        features=getattr(self.session_link,'host_features',None)
        return [item for item in features if isinstance(item,str)] if isinstance(features,list) else []
    def stream_interface(self,network=None):
        network=network if isinstance(network,dict) else self.session_link.network_status()
        if not isinstance(network,dict):return ''
        value=network.get('source_interface') or network.get('interface') or ''
        return value if isinstance(value,str) else ''
    def path_test(self,network=None):
        """Saved network test for the current host and adapter, if recent."""
        host_id=getattr(self.session_link,'host_id','')
        saved=saved_network_test(self.settings.get('network_tests'),host_id if isinstance(host_id,str) else '',self.stream_interface(network))
        if saved and time.time()-(saved.get('time') or 0)<NETWORK_TEST_MAX_AGE:return saved
        return None
    def run_network_test(self,cancel=None,save=True):
        result=self.network_test.run(self.settings['host_ip'],self.stream_fps(),300,cancel)
        if save:
            self.settings['network_tests']=remember_network_test(self.settings.get('network_tests'),result);self.save_settings()
        return result
    def ensure_network_test(self,cancel):
        """Automatic bitrate: one network test per host and adapter, before the first session on it.

        1.0.0.5: and again before the next session after a test that found no rate the path carried cleanly
        (needs_retest), up to three tests in a row."""
        try:
            host,source,interface=self.session_link.source_path(self.settings['host_ip'])
            saved=saved_network_test(self.settings.get('network_tests'),host.get('host_id'),interface)
            # 1.0.0.4: a wired path's test from before 1.0.0.4 lacks the loss-free rate its pacing now stays under.
            measured=path_tested(saved) or (saved.get('link') or {}).get('kind')!='wired' if saved else False
            fresh=bool(saved) and time.time()-(saved.get('time') or 0)<NETWORK_TEST_MAX_AGE
            retest=needs_network_retest(saved)
            if saved and measured and fresh and not retest:return saved
            if 'link-test' not in (host.get('features') or []):return saved if fresh else None
            if not saved:self.log('Automatic bitrate: testing this network path once before the first session on it.')
            elif retest and network_test_sender_timed(saved) and not network_test_failures(saved):
                # 1.0.0.6: its limit was lateness measured together with the host's own sending time.
                self.log('Automatic bitrate: testing this network path again; the last test counted the host PC\'s own '
                         'sending time as network delay (1.0.0.6 tells them apart).')
            elif retest:self.log('Automatic bitrate: the last network test on this path found no rate it carried cleanly; '
                                 'testing it again before this session (test '+str(network_test_failures(saved)+1)+' in a row).')
            elif not measured:self.log('Automatic bitrate: testing this network path again, for the rate it carries without loss (1.0.0.4).')
            else:self.log('Automatic bitrate: the last network test on this path is over 30 days old; testing it again.')
            result=self.run_network_test(cancel)
            return result
        except Exception as error:
            self.log('Automatic bitrate test skipped: '+str(error)[:200]+'. Using the saved bitrate.')
            return None
    def native_command(self,text):
        """Status, quality line and recovery commands for the running native client (stdin)."""
        process=self.process
        if not process or process.poll() is not None or not process.stdin:return False
        try:process.stdin.write(text.replace('\n',' ')[:300]+'\n');process.stdin.flush();return True
        except (OSError,ValueError):return False
    def update_headset_status(self):
        """Companion status and the optional quality line in the headset (native overlay)."""
        if not self.running():return
        link=self.session_link.snapshot() if self.session_link else {}
        host=link.get('host') or {}
        status=''
        if link.get('state') in ('starting','disconnected','error') or host.get('state')=='starting':
            status=str(link.get('message') or host.get('message') or '')[:120]
        if status!=self.native_status and self.native_command('status '+status):self.native_status=status
        stream=(host.get('pipeline') or {}).get('stream') or {}
        if self.settings.get('intra_refresh') and not self.gradual_sent and stream.get('intra_refresh_active')==1:
            if self.native_command('gradual 1'):
                self.gradual_sent=True;self.log('Host intra refresh confirmed ('+str(stream.get('encoder','encoder'))+'); isolated packet loss now heals without a keyframe.')
        # RC4.3: a short in-headset line when this PC's graphics hold the stream below refresh,
        # shown even with the quality line off, then cleared (or back to the quality line).
        notice=self.graphics_status().get('short','') if time.monotonic()<self.graphics_notice_until else ''
        if time.monotonic()<self.power_notice_until:  # 1.0.0.4: the processor held back comes first
            notice=self.power_status().get('short','') or notice
        notice=notice.replace(' · ','|')  # the client's own separator for the quality line
        if notice:
            if notice!=self.hud_text and self.native_command('hud '+notice):self.hud_text=notice
        elif not self.settings.get('headset_hud'):
            if self.hud_text and self.native_command('hud'):self.hud_text=''
        else:
            now=time.monotonic()
            if now-self.hud_sent>=2:
                timings=self.timings;pipeline=host.get('pipeline') or {}
                parts=[]
                if timings.get('unique_fps'):parts.append(str(round(float(timings['unique_fps']),1))+' fps')
                total=(pipeline.get('total_ms') or {}).get('mean')
                if total:parts.append('latency '+str(round(float(total),1))+' ms')
                if self.metrics.get('frame_age_ms'):parts.append('frame age '+str(self.metrics['frame_age_ms'])+' ms')
                if timings.get('net_lost_frames') not in (None,'0'):parts.append('lost '+str(timings.get('net_lost_frames')))
                gpu=self.gpu_clocks.latest()
                if gpu:parts.append('GPU '+str(gpu['clock_mhz'])+' MHz')
                try:fov=self.foveation_live(link)
                except Exception:fov=None
                if fov and fov.get('short'):parts.append(fov['short'])
                text=' | '.join(parts)
                if text!=self.hud_text and self.native_command('hud '+text):self.hud_text=text
                self.hud_sent=now
    SESSION_GAZE_MARK='host-session-gaze.json'
    def session_gaze_mark(self):
        # In the state folder, not the release's dist/host: upgrades and reinstalls carry session.json over.
        return self.log_path.parent/self.SESSION_GAZE_MARK
    def mark_session_gaze(self,on):
        """RC4.5: remember that session.json's eye tracking is on only for the client's session, so it is not
        read back as this PC's own preference."""
        path=self.session_gaze_mark()
        try:
            if on:
                temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps({'on_for_session':True})+'\n',encoding='utf-8')
                os.replace(temporary,path)
            else:path.unlink(missing_ok=True)
        except OSError as error:self.log('Session eye-tracking note not saved: '+str(error)[:160])
    def refresh_host_settings(self):
        path=self.host/'session.json'
        try:
            stamp=path.stat().st_mtime_ns
            if stamp==self._host_settings_stamp:return
            settings=json.loads(path.read_text(encoding='utf-8'))['session_settings']
            session_only=self.session_gaze_mark().is_file()
            self.settings.update(microphone=bool(settings['audio']['microphone']['enabled']),
                                 gaze=bool(settings['headset']['face_tracking']['enabled']) and not session_only,
                                 codec=settings['video']['preferred_codec']['variant'],
                                 bitrate=settings['video']['bitrate']['mode']['ConstantMbps'])
            self._host_settings_stamp=stamp
        except FileNotFoundError:pass
        except (OSError,ValueError,KeyError,TypeError) as error:
            # Do not silently replace a user's incompatible/corrupt ALVR profile.
            self.log('Host settings could not be read: '+str(error))
    def connection_status(self):
        """The link the session uses or would use (Wi-Fi or Ethernet), cached for 5 s."""
        try:network=self.session_link.network_status() if self.session_link else {}
        except Exception:network={}
        interface=network.get('source_interface') or network.get('interface') or ''
        if not interface:
            host=network.get('host_ip') or self.settings.get('host_ip') or ''
            if host and self.wifi_session:
                try:interface=self.wifi_session.route_interface(host)
                except Exception:interface=''
        if not interface:
            adapters=[row for row in network.get('adapters') or [] if isinstance(row,dict) and row.get('interface')]
            interface=adapters[0]['interface'] if adapters else ''
        now=time.monotonic();when,cached_interface,cached=self.connection_cache
        if cached is not None and cached_interface==interface and now-when<5:return cached
        try:value=describe_connection(interface)
        except Exception as error:value={'interface':interface,'kind':'unknown','summary':str(error)[:120],'details':{},'warnings':[],'notes':[]}
        self.connection_cache=(now,interface,value)
        return value
    def alignment_status(self):
        value=dict(self.alignment_check)
        if value.get('state')=='running':
            elapsed=time.time()-value.get('started',0)
            if elapsed>=10.5:value['state']='ask'
            else:value['remaining_s']=max(0,round(10-elapsed))
        return value
    def status(self):
        extra={};relay=self.relay  # read once: eye tracking may close it meanwhile
        if self.role=='client':
            extra['connection']=self.connection_status()
            extra['wifi_roaming']=self.ap_lock.status() if self.ap_lock else None
            extra['alignment_check']=self.alignment_status()
            try:extra['steam_shortcut']=self.steam_shortcut.status() if self.steam_shortcut else None
            except Exception as error:extra['steam_shortcut']={'available':False,'added':False,'message':str(error)[:160]}
            extra['report_delivery']=dict(getattr(self,'report_delivery',None) or {})
            extra['graphics']=self.graphics_status()
            extra['power_balance']=self.power_status()
            extra['runtime_restart']=self.runtime_restart_status()
            # RC4.5: outside self.lock: eye tracking has its own locks and its camera helper logs.
            extra['eye_tracking']=self.eye_status()
        else:
            extra['received_reports']=self.received_reports.list() if self.received_reports else []
            extra['host_microphone']=self.microphone_setup.status() if self.microphone_setup else None
            extra['fovea']=self.host_fovea_status()
        with self.lock:
            if self.role=='host':self.refresh_host_settings()
            running=self.running()
            state='stopped'
            if running:
                state='host-running' if self.role=='host' else 'connecting'
                if self.role=='client' and self.metrics:
                    state=self.metrics.get('state','recovery') if time.monotonic()-self.last_metrics<3 else 'unresponsive'
            elif self.last_exit not in (None,0) or self.startup_error:state='error'
            if self.client_runtime and self.client_runtime.state=='starting' and not running:state='starting'
            host_status=self.host_control.snapshot() if self.host_control else None
            if host_status:
                if self.startup_error and host_status['runtime_state']=='stopped':
                    host_status.update(runtime_state='error',error=self.startup_error,message=self.startup_error)
                runtime_state=host_status['runtime_state']
                state={'starting':'host-starting','stopping':'host-stopping','stopped':'stopped','error':'error'}.get(runtime_state,'host-ready')
                if runtime_state=='ready' and host_status['available']:
                    rows=host_status['clients']
                    if any(row['connection_state'] in ('Streaming','Connected') for row in rows):state='host-connected'
                    elif any(not row['trusted'] for row in rows):state='host-connecting' if host_status.get('auto_connect') else 'host-pairing'
                    elif any(row['trusted'] for row in rows):state='host-connecting'
            link=self.session_link.snapshot() if self.session_link else {}
            if self.role=='host' and not running and link.get('last_error'):state='error'
            if self.client_identity and running and state in ('connecting','recovery'):
                identity_state=self.client_identity.status().get('connection_state')
                if identity_state in ('starting','searching','connecting','restarting','error'):state=identity_state
            return {'pid':os.getpid(),'packaged':PACKAGED,'role':self.role,'version':role_version(self.role),'platform':platform_name(),'running':running,'state':state,'busy':self.action_lock.locked(),'startup_error':self.startup_error,
                    'logs':list(self.lines),'settings':dict(self.settings),'metrics':dict(self.metrics),'audio':dict(self.audio),'video':dict(self.video),
                    'client_runtime':self.client_runtime.status() if self.client_runtime else None,
                    'wifi':self.wifi_status(),
                    'host_addons':self.host_addons() if self.role=='host' else None,
                    'display':self.display_info() if self.client_runtime else None,
                    'windows_startup':self.windows_startup.status() if self.windows_startup else None,
                    'game_mode_setup':self.game_mode_setup.status() if self.game_mode_setup else None,
                    'game_mode':self.lease_runtime.status() if self.lease_runtime else None,
                    'remote_diagnostics':self.remote_diagnostics.status() if self.remote_diagnostics else None,
                    'playspace':dict(self.playspace),'timings':dict(self.timings),'inputs':dict(self.inputs),
                    'session_link':link or None,
                    'system':self.client_system_status(link) if self.role=='client' else None,
                    'host_power':self.host_power_status() if self.role=='host' else None,
                    'host_events':self.host_events.status() if self.host_events else None,
                    'gaze_status':relay.status() if relay else {'state':'off'},
                    'readiness':self.readiness,'evidence':self.evidence.report(),
                    'host_control':host_status,'client_identity':self.client_identity.status() if self.client_identity else None,
                    'client_network':self.client_network.status() if self.client_network else None,
                    'network_path':self.network_monitor.snapshot() if self.network_monitor else None,
                    'build_running':self.build is not None and self.build.poll() is None,
                    'camera_running':bool(self.eye and self.eye.cameras.running()),
                    'available':(self.host/'ALVR Dashboard.exe').is_file() if self.role=='host' else self.client.is_file(),**extra}

    def wifi_status(self):
        """The session's Wi-Fi preparation (launcher/wifi_session.py), with whether Windows refused to name the
        network and access point (1.0.0.7: Windows 11 asks for location access first; the page says where)."""
        session=getattr(self,'wifi_session',None)
        if not session:return None
        value=dict(session.status() or {})
        diagnostics=getattr(self,'wifi_diagnostics',None)
        try:latest=diagnostics.latest() if diagnostics and callable(getattr(diagnostics,'latest',None)) else {}
        except Exception:latest={}
        if isinstance(latest,dict) and latest.get('location_blocked'):value['location_blocked']=True
        return value

    FOVEATION_NAMES={'subtle':'Subtle','balanced':'Balanced','eye-tracked':'Eye-tracked'}
    def foveation_plan(self,link):
        """(family, preset, host features, encoded eye estimate, display) for the next or running stream."""
        family=self.current_family() or 'other'
        preset=foveation_choice(self.settings['foveation'],family,self.gaze_planned())
        features=(link or {}).get('host_features') if isinstance((link or {}).get('host_features'),list) else []
        if self.settings['software']:preset='off'
        estimate=None
        display=display_status(self.settings,*self.known_headset()) if self.client_runtime else {}
        if display.get('stream_width') and preset!='off':
            estimate=foveated_size(display['stream_width'],display['stream_height'],self.foveation_settings(preset,features))
        return family,preset,features,estimate,display

    def foveation_settings(self,preset,features,gaze=None):
        """Session foveation parameters for a preset with this client's eye-tracked tuning (RC4.3)."""
        return foveation_parameters(preset,self.gaze_planned() if gaze is None else gaze,features,self.settings.get('fov_region','medium'),
                                    self.settings.get('fov_edge','balanced'))

    def _gaze_share(self,totals):
        """Share of recent host frames centred on the measured gaze (host counters are per connection)."""
        previous,self._gaze_totals=self._gaze_totals,totals
        gaze,fallback=totals
        if previous and gaze>=previous[0] and fallback>=previous[1]:
            if (gaze-previous[0])+(fallback-previous[1])>0:
                recent=(gaze-previous[0],fallback-previous[1]);self._gaze_recent=recent[0]/(recent[0]+recent[1])
        else:
            self._gaze_recent=gaze/(gaze+fallback) if gaze+fallback else None
        return self._gaze_recent

    def foveation_live(self,link,preset=None,estimate=None,display=None):
        """What foveated encoding is doing in the running stream, from decoded frames and host statistics."""
        if not self.running() or 'foveated_frames' not in self.timings:return None
        def number(value):
            try:return int(float(value))
            except (TypeError,ValueError):return 0
        if preset is None:_,preset,_,estimate,display=self.foveation_plan(link)
        foveated=number(self.timings.get('foveated_frames'));moved=number(self.timings.get('gaze_centred_frames'))
        if foveated<=0:
            return {'active':False,'mode':'off','short':'','text':'Foveated encoding is not active in this stream: full resolution across each eye.'}
        stream=(((link or {}).get('host') or {}).get('pipeline') or {}).get('stream') or {}
        gaze_on=number(stream.get('gaze_foveation'))==1
        # 1.0.0.4 hosts: frames held where the user last looked (a pause in the gaze) still follow the eyes.
        share=self._gaze_share((number(stream.get('gaze_gaze_frames'))+number(stream.get('gaze_held_frames')),
                                number(stream.get('gaze_fallback_frames')))) if gaze_on else None
        if gaze_on and share is not None and share>=.5:
            mode,detail='eyes','the sharp region follows your eyes ('+str(round(share*100))+'% of frames)'
        elif gaze_on:
            mode,detail='eyes-waiting','eye tracking is on, but no current gaze reaches the host, so the sharp region stays centred on each eye\'s view'
        elif number(stream.get('optical_centre'))==1 or moved>0:
            mode,detail='centred','the sharp region is centred on each eye\'s straight-ahead view'
        else:
            mode,detail='fixed','the sharp region is fixed in the middle of each eye\'s image'
        saving=None
        if estimate and (display or {}).get('stream_width') and display.get('stream_height'):
            saving=max(0,round(100*(1-estimate[0]*estimate[1]/(display['stream_width']*display['stream_height']))))
        name=self.FOVEATION_NAMES.get(preset,'On')
        text='Foveated encoding active: '+name+' · '+detail+('; about '+str(saving)+'% fewer pixels to encode and send.' if saving else '.')
        short='fov '+name.lower()+(' eyes '+str(round(share*100))+'%' if mode=='eyes' else ' centred' if mode in ('centred','eyes-waiting') else '')+(' -'+str(saving)+'% px' if saving else '')
        return {'active':True,'mode':mode,'preset':preset,'gaze_share':round(share,3) if share is not None else None,'saving_percent':saving,'text':text,'short':short,
                'gaze_known':'gaze_foveation' in stream}

    def foveation_status(self,link,family,preset,features,estimate,display):
        """Foveated encoding for the pages, with the RC4.3 eye-tracking panel's statistics."""
        live=self.foveation_live(link,preset,estimate,display)
        gaze_follow=preset=='eye-tracked' and bool(self.gaze_planned()) and 'gaze-foveation' in features
        stream=[display['stream_width'],display['stream_height']] if display.get('stream_width') else None
        encoded=list(estimate) if estimate else None
        if self.running() and self.video.get('foveated') and type(self.video.get('encoded_width')) is int:
            encoded=[self.video['encoded_width'],self.video.get('encoded_height')]
            stream=[self.video.get('width'),self.video.get('height')]
        panel={'show':bool(gaze_follow and self.running() and (live or {}).get('mode') in ('eyes','eyes-waiting')),
               'region':self.settings.get('fov_region','medium'),'edge':self.settings.get('fov_edge','balanced'),
               'region_label':FOVEATION_REGION_LABELS.get(self.settings.get('fov_region'),'Medium'),
               'edge_label':FOVEATION_EDGE_LABELS.get(self.settings.get('fov_edge'),'Balanced · ¼'),
               'region_percent':round(100*FOVEATION_REGIONS.get(self.settings.get('fov_region'),.4)),
               'edge_ratio':FOVEATION_EDGES.get(self.settings.get('fov_edge'),4.0),
               'encoded_eye':encoded,'stream_eye':stream,
               'saving_percent':(live or {}).get('saving_percent'),'gaze_share':(live or {}).get('gaze_share'),
               'frame_pass':self.timings.get('frame_pass') if self.running() else None,
               'sharpening':self.applied_sharpening(),'gaze_rate':self.gaze_rate(),'gaze_reach':self.gaze_reach()}
        self._fovea_panel=panel
        return {'choice':self.settings['foveation'],'preset':preset,'family':family,'gaze_follow':gaze_follow,
                'encoded_eye':list(estimate) if estimate else None,'live':live,'panel':panel,
                'eye_state':self.eye_state(preset,live)}

    def eye_state(self,preset,live):
        """Why Eye-tracked foveation does or does not follow the eyes, for the pages and Game Mode (RC4.4,
        RC4.5 with managed eye tracking): 'cpu' (CPU decoding: no foveation), 'off-here' (Eye tracking off:
        the fixed Balanced region), 'no-cameras' (a headset without eye cameras), 'setup' (on, Baballonia not
        set up), 'calibrate' (not calibrated: Balanced until it is), 'restart' (ready only since this session
        started: the next Start follows the eyes), 'starting', 'cameras' (the eye cameras cannot be used),
        'closed' (Baballonia was closed), 'eye-error',
        'host-off' (the host keeps the region centred), 'no-gaze' (the host follows gaze but none arrives),
        'following', or '' (Eye-tracked not chosen, or nothing known yet)."""
        if self.settings.get('foveation')!='eye-tracked' and preset!='eye-tracked':return ''
        if preset=='off':return 'cpu'
        running=self.running()
        session=running and self.session_eye_on is not None  # this session's Start decided it
        if not self.settings.get('gaze') and not (session and self.session_eye_on):return 'off-here'
        if self.current_family() not in (None,'beyond'):return 'no-cameras'
        if self.eye is not None:
            manager=self.eye.manager
            if not manager.installed():return 'setup'
            if not manager.calibration().get('calibrated'):return 'calibrate'
            if session and (not self.session_eye_on or self.session_gaze is False):return 'restart'
            phase=self.eye.phase
            if running and phase=='cameras':return 'cameras'
            if running and phase=='closed':return 'closed'
            if running and phase in ('error','silent'):return 'eye-error'
            if running and phase in ('starting','waiting'):return 'starting'
        mode=(live or {}).get('mode')
        if mode in ('centred','fixed') and not (live or {}).get('gaze_known'):return ''  # the host has not said yet
        return {'centred':'host-off','fixed':'host-off','eyes-waiting':'no-gaze','eyes':'following'}.get(mode,'')

    def host_fovea_status(self):
        """The eye-tracking panel on the host page (RC4.3): shown while the streaming client shares
        its live view; host-side counts from the driver's foveation line (every 5 s)."""
        view=self.fovea_view.current()
        lease=self.session_link.lease_details() if self.session_link and hasattr(self.session_link,'lease_details') else None
        show=bool(view.get('live') and lease)
        stats={}
        try:
            statistics=self.host_events.status().get('statistics',{}) if self.host_events else {}
            gaze=(statistics.get('GazeFoveation') or {}).get('values') or {}
            following=int(gaze.get('gaze_frames',0))+int(gaze.get('held_frames',0))  # 1.0.0.4: held frames follow too
            total=following+int(gaze.get('fallback_frames',0))
            if total:stats['host_gaze_share']=round(following/total,3)
            if 'moves' in gaze:stats['host_moves']=int(gaze['moves'])
        except (TypeError,ValueError,AttributeError):pass
        panel={'show':show}
        if show:
            panel.update(region_percent=round(100*view['region'][0]),edge_ratio=view['edge'][0],encoded_eye=view['encoded'],
                         stream_eye=view['eye'],**{key:view[key] for key in ('gaze_share','saving_percent','frame_pass','sharpening','gaze_rate') if key in view})
            if panel.get('saving_percent') is None and view['eye'][0] and view['eye'][1]:
                panel['saving_percent']=max(0,round(100*(1-view['encoded'][0]*view['encoded'][1]/(view['eye'][0]*view['eye'][1]))))
            if 'host_gaze_share' in stats:panel['gaze_share']=stats['host_gaze_share']
            if 'host_moves' in stats:panel['moves']=stats['host_moves']
        return {'panel':panel}

    def note_controller_prediction(self,inputs):
        """1.0.0.6: keep the session's worst of the native client's controller prediction check (INPUT lines, one
        window a second per hand): how far the stream's prediction put a controller from where it really was, and
        the same while it was held still (a steady offset there would not be prediction)."""
        session=getattr(self,'controller_prediction',None)
        if not isinstance(session,dict):session=self.controller_prediction={}
        pair=inputs.get('pair')
        try:
            # The controllers' distance while both are held still, as SteamVR on this PC tracks them (the host logs
            # the same as received and as submitted: VROST_CONTROLLER_PAIR).
            still=int(pair.get('still_samples') or 0) if isinstance(pair,dict) else 0
            if still>0:
                row=session.setdefault('pair',{'still_samples':0,'mean_mm':0.0,'min_mm':None,'max_mm':None})
                total=row['still_samples']+still
                row['mean_mm']=round((row['mean_mm']*row['still_samples']+float(pair.get('mean_mm') or 0)*still)/total,1)
                row['still_samples']=total
                low,high=float(pair.get('min_mm') or 0),float(pair.get('max_mm') or 0)
                row['min_mm']=round(low if row['min_mm'] is None else min(row['min_mm'],low),1)
                row['max_mm']=round(high if row['max_mm'] is None else max(row['max_mm'],high),1)
        except (TypeError,ValueError,AttributeError):pass
        for hand in ('left','right'):
            check=(inputs.get(hand) or {}).get('prediction') if isinstance(inputs.get(hand),dict) else None
            if not isinstance(check,dict):continue
            try:
                samples=int(check.get('samples') or 0)
                if samples<=0:continue
                row=session.setdefault(hand,{'windows':0,'samples':0,'max_mm':0.0,'p95_max_mm':0.0,'still_samples':0,
                                             'still_max_mm':0.0,'horizon_ms':None})
                row['windows']+=1;row['samples']+=samples
                row['max_mm']=round(max(row['max_mm'],float(check.get('max_mm') or 0)),1)
                row['p95_max_mm']=round(max(row['p95_max_mm'],float(check.get('p95_mm') or 0)),1)
                row['still_samples']+=int(check.get('still_samples') or 0)
                row['still_max_mm']=round(max(row['still_max_mm'],float(check.get('still_max_mm') or 0)),1)
                row['horizon_ms']=round(float(check.get('horizon_ms') or 0),1)
            except (TypeError,ValueError):continue

    def controller_prediction_summary(self):
        """1.0.0.6: the session's controller prediction check for reports (see note_controller_prediction)."""
        session=getattr(self,'controller_prediction',None)
        return {hand:dict(row) for hand,row in session.items()} if isinstance(session,dict) else {}

    def gaze_reach(self):
        """1.0.0.6: how far the gaze reached this session in whole degrees ({'left', 'right', 'up', 'down', 'samples'};
        gaze_provider.GazeReach), or None before enough samples."""
        relay=self.relay
        if not relay:return None
        try:reach=relay.status().get('reach')
        except Exception:return None
        return reach if isinstance(reach,dict) else None

    def gaze_rate(self):
        """Eye-tracking samples per second reaching this client (from the gaze relay's counter)."""
        relay=self.relay
        if not relay:
            self._gaze_rate=None;return None
        try:received=int(relay.status().get('received',0))
        except Exception:return None
        now=time.monotonic();previous=self._gaze_rate
        if previous and now-previous[0]>=.8:
            rate=max(0.0,(received-previous[1])/(now-previous[0]))
            self._gaze_rate=(now,received,rate)
            return round(rate,1)
        if not previous:self._gaze_rate=(now,received,None)
        return previous[2] if previous else None

    def fovea_live(self):
        """The live eye-tracking view for the pages' panel (client: from the native client; host: from the client)."""
        return self.fovea_view.current()

    def share_fovea(self):
        """Send the live view to the host page ten times a second (1.0.0.3; five before) while
        eye-tracked foveation runs (RC4.3 hosts list 'fovea-view' and take one every 80 ms).
        Memory only on both PCs."""
        failures=0
        while not self.closing.wait(.1):
            try:
                self.share_fovea_once()
                failures=0
            except Exception:
                failures+=1
                if failures>=3:
                    failures=0
                    self.closing.wait(5)

    def share_fovea_once(self):
        """One send of the live view to the host; False when there is nothing to share."""
        link=self.session_link
        features=(getattr(link,'host_features',None) or ()) if link else ()
        if not link or not self.running() or 'fovea-view' not in features or 'gaze-foveation' not in features:return False
        if not self.gaze_planned() or foveation_choice(self.settings['foveation'],self.current_family() or 'other',True)!='eye-tracked':
            return False  # only while the sharp region follows the eyes
        if not self.host_follows_gaze():return False  # and the host really uses the gaze
        view=self.fovea_view.current()
        if not view.get('live'):return False
        system=dict(getattr(self,'_fovea_panel',None) or {},frame_pass=self.timings.get('frame_pass'),
                    sharpening=self.applied_sharpening(),gaze_rate=self.gaze_rate())
        payload={key:view[key] for key in ('left','right','region','edge','encoded','eye','gaze_left','gaze_right','gaze_age_ms') if key in view}
        for key in ('gaze_share','saving_percent','frame_pass','sharpening','gaze_rate'):
            if system.get(key) is not None:payload[key]=system[key]
        try:payload=fovea_view_checked(payload)
        except ValueError:return False
        return bool(link.send_fovea(payload))

    def applied_sharpening(self):
        """The sharpening the headset picture gets: only the fused frame pass sharpens (RC4.3)."""
        frame_pass=self.timings.get('frame_pass') if self.running() else None
        return 'off' if frame_pass in ('two-pass','cpu') else self.settings.get('sharpening','medium')

    def host_follows_gaze(self):
        """Whether the host centres the sharp region on the gaze this session (its stream telemetry)."""
        try:link=self.session_link.snapshot()
        except Exception:return False
        stream=(((link or {}).get('host') or {}).get('pipeline') or {}).get('stream') or {}
        try:return int(float(stream.get('gaze_foveation',0)))==1
        except (TypeError,ValueError):return False

    def client_system_status(self,link):
        """What the client's system helpers did, for the desktop and Game Mode pages."""
        network=(link or {}).get('network') or {}
        tested=self.path_test(network) if self.session_link else None
        family,preset,features,estimate,display=self.foveation_plan(link)
        def state(helper):
            try:value=helper.status() if helper else {}
            except Exception:value={}
            return dict(value) if isinstance(value,dict) else {}
        test=state(getattr(self,'network_test',None))
        return {'power':state(self.session_power),'async_reprojection':state(self.async_reprojection),
                'network_settings':state(self.network_settings),'video_path':self.video_path,  # 1.0.0.14
                'headset_audio':state(self.headset_audio),
                'wake':dict(state(self.host_waker),mac_known=bool(self.settings.get('host_mac'))),
                'network_test':{key:test.get(key) for key in ('state','message')},
                # 1.0.0.5: the saved result without its steps (they are for reports, not for each page update).
                'path_test':{key:value for key,value in tested.items() if key not in ('steps','receiver','failed_test')} if isinstance(tested,dict) else tested,
                'foveation':self.foveation_status(link,family,preset,features,estimate,display),
                'other_wifi':state(getattr(self,'other_wifi',None)),
                # 1.0.0.8: the performance balance (Linux), the base stations and the running stream's bitrate.
                'tuning':state(getattr(self,'session_tuning',None)),
                'base_stations':state(getattr(self,'base_stations',None)),
                'bitrate':self.bitrate_status(),
                'game_audio':self.game_audio_status(),  # 1.0.0.9
                'host_features':features}
    def bitrate_status(self):
        """1.0.0.8: the bitrate governor's state for the pages and reports."""
        governor=getattr(self,'bitrate_governor',None)
        if governor is None:return {}
        value=governor.snapshot()
        value['host_applies']='bitrate-control' in self.host_feature_list()
        # 1.0.0.9: video frames rebuilt from the host's parity this session (each would have been lost).
        repaired=timing_number(getattr(self,'timings',None),'net_fec_video_total',int)
        if repaired is not None:value['repaired_frames']=repaired
        return value

    def game_audio_status(self):
        """1.0.0.9: the game audio playing on this PC, from the native client's TIMING line: the rates, the
        jitter buffer's level and aim, and how often it ran dry (dropouts) this session."""
        timings=getattr(self,'timings',None) or {}
        if 'audio_active' not in timings:return {}
        value={'active':timings.get('audio_active')=='1'}
        for key,name,kind in (('stream_hz','audio_stream_hz',int),('device_hz','audio_device_hz',int),('channels','audio_channels',int),
                              ('buffer_ms','audio_buffer_ms',float),('target_ms','audio_target_ms',float),
                              ('dropouts','audio_underruns_total',int)):
            number=timing_number(timings,name,kind)
            if number is not None:value[key]=round(number,1) if kind is float else number
        return value

    def log(self,line):
        # Do not include live gaze or world poses. Static optical geometry may
        # be recorded for projection diagnosis and included in the host report.
        if line.startswith('FOVEA '):
            # Where the user looks (RC4.3 panel): memory only, never the log, journal or reports.
            view=parse_fovea(line.strip())
            if view:self.fovea_view.update(view)
            return
        if 'FOVEA' in line or 'gaze_left=' in line or 'gaze_right=' in line:return  # never a fragment of one either
        with self.lock:
            line=clean(line)
            self.journal.record('log',line)
            self.lines.append(line.rstrip())
            if getattr(self,'game_mode_log',None):self.game_mode_log.line(line.rstrip())
            if self.client_identity:self.client_identity.observe(line)
            if line.startswith('PLAYSPACE '):
                try:
                    area=json.loads(line[10:]);area['sequence']=self.playspace.get('sequence',0)+1;self.playspace=area
                except (ValueError,TypeError):pass
            if line.startswith('INPUT '):
                try:
                    value=json.loads(line[6:])
                    if isinstance(value,dict):
                        self.inputs=value
                        self.note_controller_prediction(value)  # 1.0.0.6
                        self.timeline.controllers(value)
                except (ValueError,TypeError):pass
            if line.startswith('VIDEO '):
                try:
                    value=json.loads(line[6:])
                    # 1.0.0.5: the colour description arrives with the first picture and only again when it changes.
                    if isinstance(value,dict):self.video=dict(value,**({'colour':self.video['colour']} if isinstance(self.video.get('colour'),dict) else {}))
                except (ValueError,TypeError):pass
            if line.startswith('VIDEO_COLOUR '):
                try:
                    value=json.loads(line[len('VIDEO_COLOUR '):])
                    if isinstance(value,dict):
                        self.video=dict(self.video,colour={key:value[key] for key in ('bits','range','matrix','primaries','transfer','chroma') if key in value})
                except (ValueError,TypeError):pass
            if line.startswith('VIEW_GEOMETRY '):
                try:
                    value=json.loads(line[len('VIEW_GEOMETRY '):])
                    if isinstance(value,dict):self.view_geometry=value
                except (ValueError,TypeError):pass
            if line.startswith('TIMING '):
                self.timings=dict(item.split('=',1) for item in line.split()[1:] if '=' in item)
                self.check_video_path()
                self.timeline.timing(self.timings)
                if getattr(self,'session_tuning',None) is not None:self.session_tuning.note_timing(self.timings)
                governor=getattr(self,'bitrate_governor',None)
                if governor is not None:
                    previous=governor.mbps;changed=governor.observe(self.timings)
                    if changed:self.bitrate_note='BITRATE '+str(previous)+' -> '+str(changed)+' Mbit/s ('+governor.reason+')'
                # Acted on by monitor_session (outside this lock): native commands write to the client.
                metrics=self.metrics if time.monotonic()-self.last_metrics<3 else {}
                tracked=metrics.get('tracked')=='1' and metrics.get('input_focus')=='1'
                event=self.graphics_limit.observe(self.timings.get('cadence'),graphics_frames_arriving(self.timings),tracked)
                if event:self.graphics_event=event
                # 1.0.0.11: one step smaller (or back) when the frame rate needs it; sent by monitor_session.
                if self.settings.get('keep_frame_rate',True) and self.timings.get('frame_pass') in ('fused','two-pass'):
                    level=self.output_scale.observe(self.timings,graphics_frames_arriving(self.timings),tracked)
                    if level:self.scale_event=level
            if line.startswith('METRICS '):
                values=dict(item.split('=',1) for item in line.split()[1:] if '=' in item)
                self.metrics={key:values[key] for key in ('state','decoded','dropped','frame_age_ms','tracked','input_focus','submitted','submit_failed','reused','fallback','tracking_samples','tracking_gap_max_us','new_frames','video_phase','keyframe_requests','video_silence_ms','video_packets',
                                                          'head_tracked','head_untracked_ms','head_tracking_result','head_poses','head_poses_missing') if key in values}
                self.last_metrics=time.monotonic()
                self.evidence.record(self.metrics)
                self.timeline.metrics(values)
            if self.log_path.exists() and self.log_path.stat().st_size>2*1024*1024:self.log_path.replace(self.log_path.with_suffix('.previous.log'))
            with self.log_path.open('a',encoding='utf-8') as f:f.write(line.rstrip()+'\n')
    def running(self):
        if self.host_control:return self.host_control.active or bool(self.host_control.owned_pids) or bool(self.host_control.identities)
        return self.process is not None and self.process.poll() is None
    def watch(self,process):
        fatal=False
        for line in process.stdout:
            self.log(line)
            if line.startswith('FATAL ') and not fatal:
                fatal=True
                threading.Thread(target=self.native_failure,args=(process,line[6:].strip()),daemon=True).start()
            elif line.startswith('STATUS Local SteamVR requested this client to quit'):
                self.runtime_quit_requested=process  # a quit, not a crash: no automatic restart
            elif line.startswith('STATUS SteamVR compositor frames stopped advancing'):self.compositor_stalled=True
            elif line.startswith('STATUS SteamVR compositor frames are advancing again'):self.compositor_stalled=False
            elif line.startswith('STATUS ') and 'decoder profiles' in line:self.remember_decoders(parse_decoder_profiles(line))
        code=process.wait()
        with self.lock:
            if self.process is process:
                self.evidence.finish()
                self.last_exit=0 if self.stopping is process else code
                if self.stopping is not process and not self.startup_error:
                    self.startup_error='The headset streaming process ended unexpectedly (exit '+str(code)+'). See the client log for its exit reason.'
        self.log('Process exited: '+str(code))
        if (self.role=='client' and self.stopping is not process and code not in (0,None)
                and getattr(self,'runtime_quit_requested',None) is not process and getattr(self,'stream_ever_up',False)):
            try:lost=self.local_runtime_lost()
            except Exception:lost=''
            if lost:  # SteamVR here failed first (RC4.3: SIGPIPE 9 s later): restart, do not just stop
                self.startup_error='';self.last_exit=None
                self.local_compositor_lost(native_exit=code,reason='compositor' if lost=='replaced' else lost);return
        with self.action_lock:
            if self.process is process:
                try:self.stop()
                except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Cleanup: '+str(error))
    FATAL_RUNTIME_WAIT_S=15  # a crashed compositor can stay in /proc while its core is written

    def native_failure(self,process,message):
        if self.role=='client' and getattr(self,'stream_ever_up',False):
            # RC4.4: when it failed because SteamVR on this PC did, restart instead of stopping. The
            # error shows at once; the usual stop follows if SteamVR here still runs after the wait.
            self.startup_error=message
            deadline=time.monotonic()+self.FATAL_RUNTIME_WAIT_S
            while True:
                if (self.stopping is process or self.session_cancelled.is_set() or getattr(self,'runtime_restarting',False)
                        or getattr(self,'runtime_quit_requested',None) is process or self.process is not process):
                    break
                try:lost=self.local_runtime_lost()
                except Exception:lost=''
                if lost:
                    self.startup_error=''
                    self.local_compositor_lost(native_exit='FATAL '+message[:120],reason='compositor' if lost=='replaced' else lost);return
                if time.monotonic()>=deadline or self.closing.wait(.5):break
            if getattr(self,'runtime_restarting',False):return
        with self.action_lock:
            if self.process is not process or self.stopping is process:return
            self.session_cancelled.set();self.client_runtime.cancel();self.startup_error=message
            try:self.stop()
            except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Cleanup: '+str(error))
    def display_failure(self,process,message):
        # Cancel a concurrent start immediately; clean up on its serialized path.
        with self.lease_runtime.lock:
            if self.lease_runtime.process is not process:return
            self.session_cancelled.set();self.client_runtime.cancel()
        def cleanup():
            with self.action_lock:
                if self.lease_runtime.process is not process:return
                self.startup_error=message;self.log(message)
                try:self.stop()
                except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Display cleanup: '+str(error))
        threading.Thread(target=cleanup,daemon=True).start()

    def rollback_start(self):
        try:self.stop()
        except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Startup cleanup: '+str(error))

    def launch(self,args,env=None):
        if self.running():raise ValueError('Already running')
        self.metrics={};self.last_metrics=0;self.last_exit=None;self.stopping=None;self.evidence=SessionEvidence()
        self.reset_stream_tracking()
        with self.lock:
            # A new native client starts with the saved re-aim choice; the previous one's limit is history.
            self.graphics_limit.reset();self.graphics_event=None;self.reaim_paused=False;self.graphics_notice_until=0
            self.output_scale.reset();self.scale_event=None
            self.gpu_clocks.reset()
            self.cpu_clocks.reset();self.power_notice_until=0;self.power_held=None
            self.power_history={'held_samples':0,'lowest_cpu_mhz':None}
            self.timeline.reset();self.timeline_sample=None
            # An earlier automatic restart's message belongs to that session, unless this is the restart.
            if not getattr(self,'runtime_restarting',False):self.runtime_restart=None
            self.runtime_seen=None;self.compositor_stalled=False;self.compositor_missing=0;self.compositor_replacement=None
        self.fovea_view.clear()
        self.timings={};self.inputs={};self.video={};self.view_geometry={};self.playspace={'sent':False,'sequence':self.playspace.get('sequence',0)+1,'reason':'Waiting for this session’s play area.'}
        self.controller_prediction={}  # 1.0.0.6: this session's controller prediction check
        self.process=subprocess.Popen(args,cwd=str(self.host if self.role=='host' else self.client.parent),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',**subprocess_options())
        if getattr(self,'performance_monitor',None):self.performance_monitor.refresh()
        threading.Thread(target=self.watch,args=(self.process,),daemon=True).start()
    def close_gaze(self,end_session=True):
        """The session's eye tracking ends: the relay stops forwarding to its client (it keeps listening
        while Baballonia runs, which stops in the background unless opened by hand), and the session's
        gaze socket folder goes."""
        if end_session and self.eye is not None:self.eye.end('session')
        self.gaze_socket=None
        if self.runtime_directory:
            shutil.rmtree(self.runtime_directory,ignore_errors=True);self.runtime_directory=None
    def stop(self):
        self.autostart_cancelled.set()
        if self.host_control:
            if self.remote_diagnostics:self.remote_diagnostics.capture()
            self.unpark()
            self.host_control.stop()
            if self.host_control.snapshot().get('runtime_state')=='stopped':self.evidence.finish()
            if self.keep_awake:self.keep_awake.release()
            return
        if self.session_link:
            try:self.session_link.stop()
            except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Remote stop: '+str(error))
        process=self.process
        if process is not None:self.stopping=process
        # Keep the native neutral scene present until the physical compositor closes.
        if self.client_runtime:self.client_runtime.stop()
        if self.lease_runtime:self.lease_runtime.close()
        if getattr(self,'game_mode_log',None):self.game_mode_log.close()
        if process is not None and process.poll() is None:
            if on_windows():self.quit_native(process)
            else:process.terminate()
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self.log('The VROSTBridge client did not stop within 12 seconds; closing its owned process.')
                process.kill()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:raise ValueError('The VROSTBridge client is still stopping. Retry in a few seconds.')
        if getattr(self,'wifi_session',None):
            try:self.wifi_session.end()
            except Exception as error:self.log('Wi-Fi restore: '+str(error)[:200])
        if getattr(self,'other_wifi',None):
            try:self.other_wifi.end()
            except Exception as error:self.log('Other Wi-Fi adapter restore: '+str(error)[:200])
        if getattr(self,'ap_lock',None):
            try:self.ap_lock.end()
            except Exception as error:self.log('Wi-Fi access-point lock release: '+str(error)[:200])
        if self.alignment_check.get('state')=='running':self.alignment_check={'state':'idle'}
        self.end_client_session_helpers()
        if not self.running():self.close_gaze()
    QUIT_WAIT_S=8
    def quit_native(self,process):
        """Windows has no SIGTERM, and TerminateProcess skips the native client's clean-up (SteamVR's scene,
        the transport): ask it to quit on its command pipe, then close the pipe, which stops it too (1.0.0.7).
        stop() still ends it after its own wait if neither worked."""
        try:
            process.stdin.write('quit\n');process.stdin.flush()
        except (OSError,ValueError,AttributeError):pass
        try:
            process.wait(timeout=self.QUIT_WAIT_S);return
        except subprocess.TimeoutExpired:pass
        self.log('The VROSTBridge client did not quit within '+str(self.QUIT_WAIT_S)+' seconds; closing its command pipe.')
        try:process.stdin.close()
        except (OSError,ValueError,AttributeError):pass
    def prepare_client_system(self,generation):
        """Session-only system preparation; runs beside startup and never delays or blocks it."""
        if self.settings.get('base_stations_off') and self.base_stations and generation==self.client_generation:
            # 1.0.0.8: their motors spin up while SteamVR starts on this PC (they were turned off when VROSTBridge closed).
            try:self.base_stations.set_power_background('on')
            except Exception as error:self.log('Base stations: '+str(error)[:200])
        if self.settings['keep_awake'] and generation==self.client_generation:
            try:
                self.session_power.begin()
                # A start that already failed and cleaned up must not leave sleep blocked.
                if generation!=self.client_generation:self.session_power.end()
            except Exception as error:self.log('Keep-awake skipped: '+str(error)[:200])
        try:
            status=self.async_reprojection.check()
            if isinstance(status,dict) and status.get('state') in ('missing','disabled','disabled_driver'):
                self.log('SteamVR asynchronous reprojection is unavailable on this PC: '+str(status.get('message',''))[:300])
        except Exception as error:self.log('Asynchronous reprojection check skipped: '+str(error)[:200])
        try:  # 1.0.0.14: one network twice (the video can arrive over Wi-Fi), or a receive buffer too small for the stream
            settings=self.network_settings.check() if self.network_settings else {}
            if settings.get('state')=='needed':self.log('NETWORK '+str(settings.get('message'))[:600])
        except Exception as error:self.log('Network settings check skipped: '+str(error)[:200])
    def end_client_session_helpers(self):
        """Undo the session-only system changes (sleep block, audio defaults, Wi-Fi sampling)."""
        self.client_generation+=1
        sampler,self.wifi_sampler=self.wifi_sampler,None
        if sampler:sampler.set()
        self.tuning_started=False
        for name,helper in (('Wi-Fi diagnostics',self.wifi_diagnostics),('Headset audio',self.headset_audio),('Power',self.session_power),
                            ('Performance balance',getattr(self,'session_tuning',None)),('Adapter counters',getattr(self,'adapter_counters',None))):
            if helper:
                try:helper.end()
                except Exception as error:self.log(name+' restore: '+str(error)[:200])
        self.native_status='';self.hud_text='';self.gradual_sent=False
    def shutdown(self):
        if self.shutdown_started.is_set():return
        self.shutdown_started.set();self.closing.set();self.session_cancelled.set()
        if self.client_runtime:self.client_runtime.cancel()
        if self.network_monitor:self.network_monitor.close()
        if getattr(self,'performance_monitor',None):self.performance_monitor.close()
        # Broker callbacks take action_lock; close it before acquiring that lock.
        if self.session_link:self.session_link.close()
        self.autostart_cancelled.set()
        if self.client_runtime:self.client_runtime.cancel()
        with self.action_lock:
            try:self.stop()
            finally:
                if self.client_network:self.client_network.close()
                self.close_gaze(end_session=False)
                if self.eye is not None:self.eye.shutdown()  # Baballonia and the eye cameras stop with VROSTBridge
        if self.role=='client' and self.settings.get('base_stations_off') and self.base_stations:
            # 1.0.0.8: on its own process, so closing does not wait for Bluetooth.
            self.base_stations.detached('off',companion_command())
        if self.diagnostic_server:self.diagnostic_server.close()
        if self.remote_diagnostics:self.remote_diagnostics.close()
        if self.host_events:self.host_events.close()
        if self.role=='host':
            self.unpark()
            if self.keep_awake:self.keep_awake.release()
            if self.sleep_after_stop:self.sleep_after_stop.close()
    def local_report(self):
        relay=self.relay
        result={'readiness':self.readiness,'session':self.evidence.report(),
                'gaze':relay.status() if relay else {'state':'off'},
                'eye_tracking':self.eye_status(),'baballonia_log':self.baballonia_log_tail(),
                'role':self.role,'logs':list(self.lines),'startup_error':self.startup_error,
                'host_control':self.host_control.snapshot() if self.host_control else None,'audio':dict(self.audio),
                'client_identity':self.client_identity.status() if self.client_identity else None,
                'client_network':self.client_network.status() if self.client_network else None,
                'network_path':self.network_monitor.snapshot() if self.network_monitor else None,'wifi':self.wifi_session.status() if getattr(self,'wifi_session',None) else None,
                'adapter_counters':self.adapter_counters.snapshot() if getattr(self,'adapter_counters',None) else None}
        if self.host_control:
            result['host_runtime_logs']={}
            for name in ('session_log.txt','session_log.previous.txt','crash_log.txt'):
                path=self.host/name
                if path.is_file():
                    # Without ALVR's per-frame "[GRAPH]" lines, which filled RC4.1 reports.
                    try:result['host_runtime_logs'][name]=filtered_driver_log(path,131072 if name=='session_log.txt' else 65536,
                        drop=('\"Tracking\"','\"Buttons\"','\"Haptics\"','eyes_social','eyes_combined','hand_skeletons'))
                    except OSError:pass
        result.update(version=role_version(self.role),debug=self.journal.report(),metrics=dict(self.metrics),timings=dict(self.timings),video=dict(self.video),playspace=dict(self.playspace),inputs=dict(self.inputs),session_link=self.session_link.snapshot() if self.session_link else None)
        result['view_geometry']=dict(self.view_geometry)
        result['performance']=self.performance_monitor.snapshot() if getattr(self,'performance_monitor',None) else None
        result['steamvr_logs']=steamvr_logs()
        if self.host_events:
            result['host_events']=self.host_events.status()
            # RC4.3: the driver's foveation centres follow where the user looks; the report keeps the counts.
            gaze=((result['host_events'].get('statistics') or {}).get('GazeFoveation') or {}).get('values')
            if isinstance(gaze,dict):
                for key in ('left','right'):gaze.pop(key,None)
        if self.role=='host':
            result['host_addons']=self.host_addons()
            # 1.0.0.14: no session has streamed since this companion started: the last one's record, kept across restarts.
            if self.last_session and not self.host_streamed:result['host_last_session']=self.last_session.load()
        if self.role=='client':
            result.update(client_runtime=self.client_runtime.status(),platform=platform_name(),
                          game_mode=self.lease_runtime.status() if self.lease_runtime else None,
                          game_mode_setup=self.game_mode_setup.status() if self.game_mode_setup else None,wifi=self.wifi_status(),
                          game_mode_last_start=self.game_mode_log.load() if self.game_mode_log else None,  # 1.0.0.10
                          wifi_radio=self.wifi_diagnostics.summary(),power=self.session_power.status(),headset_audio=self.headset_audio.status(),
                          async_reprojection=self.async_reprojection.status(),network_settings=self.network_settings.status(),video_path=self.video_path,wake=self.host_waker.status(),network_test=self.network_test.status(),
                          other_wifi=self.other_wifi.status(),
                          network_tests=dict(self.settings.get('network_tests') or {}),
                          connection=self.connection_status(),wifi_roaming=self.ap_lock.status(),
                          alignment_check=self.alignment_status(),graphics=self.graphics_status(),power_balance=self.power_status(),
                          timeline=self.timeline.snapshot(),controller_prediction=self.controller_prediction_summary(),
                          preferences={key:self.settings.get(key) for key in self.CLIENT_OPTIONS+('codec','bitrate','resolution','foveation','encoder','keep_warm_minutes')},
                          # 1.0.0.8: the bitrate governor, the performance balance and the base stations.
                          bitrate_control=self.bitrate_status(),
                          game_audio=self.game_audio_status(),  # 1.0.0.9
                          session_tuning=self.session_tuning.status() if self.session_tuning else None,
                          base_stations=self.base_stations.status() if self.base_stations else None)
            try:result['steam_shortcut']=self.steam_shortcut.status() if self.steam_shortcut else None
            except Exception as error:result['steam_shortcut']={'error':str(error)[:160]}
        if self.role=='host':
            result['host_power']=self.host_power_status()
            result['received_reports']=self.received_reports.list() if self.received_reports else []
            result['host_microphone']=self.microphone_setup.status() if self.microphone_setup else None
        return result
    def diagnostic_summary(self):
        with self.lock:return {'role':self.role,'version':role_version(self.role),'session':self.journal.session,'metrics':dict(self.metrics),'timings':dict(self.timings),'video':dict(self.video),'playspace':dict(self.playspace),'inputs':dict(self.inputs),'startup_error':self.startup_error,'client_runtime':self.client_runtime.status() if self.client_runtime else None,'display':display_status(self.settings,self.client_runtime.headset) if self.client_runtime else None,'performance':self.performance_monitor.snapshot() if getattr(self,'performance_monitor',None) else None,'network_path':self.network_monitor.snapshot() if self.network_monitor else None,'wifi':self.wifi_session.status() if getattr(self,'wifi_session',None) else None}
    def report(self):
        result=self.local_report()
        if self.remote_diagnostics:result=attach_remote_session(result,self.remote_diagnostics.capture())
        # report_info: which end saved it, version and local time (also the file name).
        return stamp_report(result,self.role,role_version(self.role))
    def receive_client_report(self,name,report,peer):
        """A paired client sent its report over the session link (host)."""
        saved=self.received_reports.save(name,report)
        self.log('Client report received from '+str(peer)+': '+saved+'. Download it under Reports.')
        return saved
    def send_report_async(self):
        """Send this client's report to the host in the background.

        It never holds the action lock, so Start and Stop stay immediate while a
        slow or unreachable host times out; report_delivery carries the outcome.
        """
        if not self.session_link:raise ValueError('The session link is not running. Reopen VROSTBridge.')
        if not self.report_send_lock.acquire(blocking=False):raise ValueError('A report is already being sent.')
        self.report_delivery={'state':'sending','message':'Preparing the report…'}
        def work():
            try:
                report=self.report();name=report['report_info']['file_name']
                self.report_delivery={'state':'sending','message':'Sending '+name+' to the host…'}
                saved=self.session_link.send_report(name,report,self.settings.get('host_ip',''))
            except Exception as error:  # reported on the page; a failed upload never stops the companion
                self.report_delivery={'state':'error','message':'The report was not sent: '+str(error)[:240]}
                self.log('Report not sent: '+str(error)[:240])
            else:
                self.report_delivery={'state':'sent','name':saved,'time':time.time(),
                                      'message':'Sent to the host as '+saved+'. Download it on the host PC under Reports.'}
                self.log('Report sent to the host: '+saved+'.')
            finally:self.report_send_lock.release()
        threading.Thread(target=work,daemon=True,name='VROSTBridge-send-report').start()
    def client_command(self,command):
        if command not in ('menu','playspace') or not self.running() or not self.process.stdin:raise ValueError('Start the headset client first.')
        self.process.stdin.write(command+'\n');self.process.stdin.flush()
        self.log('Client command: '+command)
    def session(self):
        path=self.host/'session.json'
        return json.loads((path if path.exists() else ROOT/'host/default-session.json').read_text(encoding='utf-8'))
    def ensure_host_stopped(self):
        if self.build is not None and self.build.poll() is None:raise ValueError('Wait for the host build to finish')
        if self.running():raise ValueError('Stop the host before changing settings')
        if sys.platform=='win32':
            tasks=subprocess.check_output(['tasklist','/FO','CSV','/NH'],text=True,**subprocess_options()).lower()
            if 'vrserver.exe' in tasks or 'alvr dashboard.exe' in tasks:raise ValueError('Close SteamVR and ALVR before changing host settings')
    def milestone(self,name):
        """Startup timeline: seconds from Start to each step, once per session (client)."""
        if self.start_clock is None or name in self.milestones:return
        elapsed=round(time.monotonic()-self.start_clock,1);self.milestones[name]=elapsed
        self.log('STARTUP '+name+' after '+str(elapsed)+' s.')

    def reset_stream_tracking(self):
        """A new session's heartbeats start without downtime. RC4.2 kept the previous session's
        last video time until the native client started, so the first heartbeats reported a
        stream down for minutes and the host restarted its SteamVR right after Start."""
        with self.lock:self.stream_up=False;self.stream_up_at=None;self.stream_ever_up=False
        self.decoded_seen=None;self.decoded_at=-1e9
    def graphics_status(self):
        """This PC's graphics against the stream (RC4.3): limit state, advice and the session's history."""
        with self.lock:
            snapshot=self.graphics_limit.snapshot();video=dict(self.video);paused=self.reaim_paused
        width=video.get('width') if type(video.get('width')) is int else None
        refresh=video.get('refresh_hz') if isinstance(video.get('refresh_hz'),(int,float)) else None
        try:reprojection=self.async_reprojection.status() if self.async_reprojection else {}
        except Exception:reprojection={}
        repairable=reprojection.get('state') in ('missing','disabled') and bool(reprojection.get('can_repair'))
        smaller=self.sharp_preset()
        return dict(snapshot,reaim_paused=paused,stream_width=width,
                    # RC4.4: the live GPU clock, so a TDP change can be watched while it is made.
                    gpu=self.gpu_clocks.latest() if self.role=='client' and self.running() else None,
                    # 1.0.0.11: the picture's shown size this session, while this PC's graphics fell behind.
                    output_scale=self.output_scale.snapshot() if self.role=='client' else None,
                    **graphics_advice(snapshot,width,refresh,repairable,paused,smaller,
                                      self.settings.get('resolution')==(smaller or {}).get('id','sharp'),self.gpu_clocks.starved(),
                                      gpu_auto=bool((self.steamos_settings or {}).get('available'))
                                      and (self.steamos_settings or {}).get('gpu_level') not in (None,'auto'),
                                      load=self.gpu_clocks.load() if self.role=='client' and self.running() else None))
    def observe_power(self,sample):
        """1.0.0.4: the processor held far below its minimum by the shared power limit. The first time in a
        session: a log line, a short line in the headset and steamos-manager's settings read in the background."""
        if not self.cpu_clocks.observe(sample):return
        row=self.cpu_clocks.samples[-1]
        if self.cpu_clocks.held(row):
            history=self.power_history;history['held_samples']+=1
            mhz=int(round(row['cpu_mhz']))
            history['lowest_cpu_mhz']=mhz if history['lowest_cpu_mhz'] is None else min(history['lowest_cpu_mhz'],mhz)
        held=self.cpu_clocks.starved()
        first=held and not self.power_held
        self.power_held=held or None
        if not first:return
        self.power_notice_until=time.monotonic()+GRAPHICS_NOTICE_S
        # Windows (1.0.0.7) reports no minimum clock: there the processor is held below its base clock.
        floor=(' (minimum '+str(held['cpu_min_mhz'])+' MHz)' if held.get('cpu_min_mhz') is not None else
               ' (base '+str(held['cpu_max_mhz'])+' MHz)' if held.get('cpu_max_mhz') else '')
        self.log('POWER_BALANCE processor held at '+str(held['cpu_mhz'])+' MHz'+floor
                 +(', GPU '+str(held['gpu_mhz'])+' of '+str(held.get('gpu_max_mhz'))+' MHz' if held.get('gpu_mhz') else '')
                 +(' ('+held['gpu_level']+')' if held.get('gpu_level') else '')+(', '+str(held['power_w'])+' W' if held.get('power_w') else '')
                 +': '+held['cause'])
        self.read_steamos_settings()
    def read_steamos_settings(self):
        """steamos-manager's power settings, read in the background (it can take a second)."""
        def read():
            try:self.steamos_settings=self.steamos_power.read(force=True)
            except Exception as error:self.log('steamos-manager settings not read: '+str(error)[:160])
        threading.Thread(target=read,daemon=True,name='VROSTBridge-steamos-power').start()
    def sample_timeline(self):
        """Once a second during a session (1.0.0.4): the performance sample, the gaze relay's and the host's counts."""
        monitor=getattr(self,'performance_monitor',None)
        if monitor:
            sample=monitor.snapshot()
            if sample.get('observed_monotonic')!=self.timeline_sample:
                self.timeline_sample=sample.get('observed_monotonic');self.timeline.performance(sample)
        relay=self.relay
        if relay:
            try:self.timeline.gaze(relay.status().get('received',0))
            except Exception:pass
        eye=getattr(self,'eye',None)
        if eye:  # 1.0.0.5: the eye cameras' own frame rate and losses
            try:self.timeline.camera(eye.cameras.status().get('feed'))
            except Exception:pass
        link=self.session_link.snapshot() if self.session_link else {}
        stream=(((link or {}).get('host') or {}).get('pipeline') or {}).get('stream') or {}
        if 'gaze_gaze_frames' in stream:
            self.timeline.host_gaze(stream.get('gaze_gaze_frames'),stream.get('gaze_held_frames',0),stream.get('gaze_fallback_frames'))
    def power_status(self):
        """The shared power limit for the pages, Game Mode, the headset and reports (1.0.0.4)."""
        held=self.cpu_clocks.starved() if self.role=='client' and self.running() else None
        result=power_advice(held,self.steamos_settings)
        result['cpu']=self.cpu_clocks.latest() if self.role=='client' and self.running() else None
        result['history']=dict(self.power_history)
        result['steamos']=dict(self.steamos_settings or {})
        return result
    def sharp_preset(self):
        """The lighter stream choice offered when this PC's graphics fall behind: High (2016) on other headsets; from
        1.0.0.13 on an Index the next lower rate (with its 'refresh_hz', set in SteamVR at the next Start), and on a
        Beyond none ('id' None): only Bigscreen's Beyond Utility switches its mode, so while it streams 2560 a 'hint'
        names the lighter 90 Hz mode instead. None while no headset is known."""
        try:
            headset,source=self.known_headset()
            if not headset or not self.client_runtime:return None
            display=display_status(self.settings,headset,source)
            family=display.get('family')
            if family=='beyond':
                return {'id':None,**({'hint':BEYOND_LIGHTER_MODE} if (display.get('stream_width') or 0)>1920 else {})}
            if family=='index':
                rate=float(display.get('refresh_hz') or 0)
                lower=[key for key in FAMILY_MODES['index'] if MODES[key]['refresh_hz']<rate-1]
                if not lower:return {'id':None}  # already at its lowest rate
                wanted=max(lower,key=lambda key:MODES[key]['refresh_hz'])
            else:wanted='sharp'
            options=(display.get('resolution_options') or {}).get(self.settings.get('codec','Auto')) or []
            option=next((o for o in options if o.get('id')==wanted and o.get('width') and o.get('available',True)),None)
        except Exception:return None
        if not option:return None
        mode=MODES.get(option['id'])
        if mode:
            return {'id':option['id'],'label':'%g Hz'%mode['refresh_hz'],'width':option['width'],'height':option['height'],
                    'refresh_hz':float(mode['refresh_hz'])}
        return {'id':option['id'],'label':str(option.get('label','High')).split(' - ')[0],'width':option['width'],'height':option['height']}
    def balance_performance(self,sample):
        """1.0.0.8: once the session's video is up, launcher/session_tuning.py steers the GPU clock and the power
        limit (Linux with steamos-manager, when chosen); never before the stream runs."""
        tuning=self.session_tuning
        if not tuning or not self.settings.get('balance_performance') or not self.running():return
        if not self.tuning_started:
            if not self.stream_ever_up:return
            self.tuning_started=True
            generation=self.client_generation
            def begin():
                if generation!=self.client_generation:return
                tuning.begin(sample)
                if generation!=self.client_generation:tuning.end()  # the session ended meanwhile
            threading.Thread(target=begin,daemon=True,name='VROSTBridge-tuning').start()
            return
        if sample and tuning.active:
            rate=self.eye.rate() if getattr(self,'eye',None) is not None else None
            raises=getattr(tuning,'raises',None)
            tuning.observe(sample,rate if isinstance(rate,(int,float)) else None)
            # 1.0.0.12: the GPU clock was just held higher, so the bitrate's steps down so far may have been the
            # graphics' (launcher/stream_budget.py, fresh_start).
            after=getattr(tuning,'raises',None)
            if isinstance(raises,int) and isinstance(after,int) and after>raises:self.bitrate_fresh_start()
    def bitrate_fresh_start(self):
        governor=getattr(self,'bitrate_governor',None)
        if governor is None:return
        with self.lock:
            previous=governor.mbps
            changed=governor.fresh_start('back to the start: the GPU clock was raised')
            if changed:self.bitrate_note='BITRATE '+str(previous)+' -> '+str(changed)+' Mbit/s ('+governor.reason+')'
    def check_graphics_limit(self):
        """Act once when this PC's graphics start holding the stream below refresh: pause re-aim for
        this session (a full-resolution GPU pass per frame), show a short line in the headset and log."""
        monitor=getattr(self,'performance_monitor',None)
        if monitor and self.role=='client':
            try:
                sample=monitor.snapshot()
                self.gpu_clocks.observe(sample)
                if self.running():self.observe_power(sample)
            except Exception as error:self.log('GPU clock sample: '+str(error)[:160]);sample=None
            try:self.balance_performance(sample)
            except Exception as error:self.log('Performance balance: '+str(error)[:160])
        with self.lock:event,self.graphics_event=self.graphics_event,None
        if self.role=='client':self.send_output_scale()
        if not event or self.role!='client':return
        snapshot=self.graphics_limit.snapshot()
        if event=='limited':
            paused=''
            # With the RC4.3 fused frame pass re-aim costs nothing extra (the one pass runs either way),
            # so it is paused only on the two-pass path.
            if (self.settings.get('reaim') and not self.reaim_paused and self.timings.get('frame_pass')!='fused'
                    and self.native_command('reaim 0')):
                with self.lock:self.reaim_paused=True
                paused=' Frame re-aim is paused for this session to save GPU time.'
            if snapshot['episodes']==1:self.graphics_notice_until=time.monotonic()+GRAPHICS_NOTICE_S  # once per session
            # 1.0.0.5: a GPU held low (a fixed clock or the power limit): read whether steamos-manager can
            # set the clock to automatic, so the page offers it in one step.
            if self.gpu_clocks.starved() and not self.steamos_settings:self.read_steamos_settings()
            self.log("GRAPHICS This PC's graphics are holding the stream below the headset refresh rate ("
                     +str(snapshot['recent_throttled_s'])+' of the last '+str(snapshot['window_s'])+' s).'+paused)
        elif event=='recovered':
            self.log('GRAPHICS The stream is back at the headset refresh rate.')
    def send_output_scale(self):
        """1.0.0.11: send the size the output scale chose, if it still stands (Keep the frame rate may have just
        been turned off); one that could not be sent is tried again on the next check."""
        with self.scale_lock:
            with self.lock:
                scale,self.scale_event=self.scale_event,None
                current=self.output_scale.level if self.settings.get('keep_frame_rate',True) else 100
                step=dict(self.output_scale.steps[-1]) if self.output_scale.steps else {}
            if not scale or scale!=current:return
            if not self.native_command('scale '+str(scale)):
                with self.lock:
                    if self.scale_event is None:self.scale_event=scale
                return
            self.log('SCALE Each eye shown at '+str(scale)+'% of its size'+(' (was '+str(step.get('from'))+'%: '+step.get('reason','')+')'
                                                                          if step else '')+'.')
    def video_arriving(self,now=None):
        """Whether the host's video reaches this PC: frames are shown, or keep being decoded.

        Showing alone is not enough: the presentation also leaves 'streaming' when the
        headset loses tracking or is taken off while video still arrives, and the host must
        not restart SteamVR (closing the game) for that.
        """
        now=time.monotonic() if now is None else now
        metrics=self.metrics
        if not self.running() or now-self.last_metrics>=3:return False
        try:decoded=int(metrics.get('decoded','0'))
        except ValueError:decoded=0
        previous=getattr(self,'decoded_seen',None)
        if previous is None or decoded!=previous:  # more frames, or a new client process
            self.decoded_seen=decoded
            if previous is not None or decoded:self.decoded_at=now
        if metrics.get('state')=='streaming':return True
        return now-getattr(self,'decoded_at',-1e9)<2.5
    def track_stream(self):
        """Whether video reaches this PC, for the host's stream recovery (RC4.1 hosts)."""
        now=time.monotonic()
        up=self.video_arriving()
        with self.lock:
            if up and not self.stream_up and self.stream_ever_up and self.stream_up_at is not None and now-self.stream_up_at>=3:
                self.log('Fresh video resumed after '+str(round(now-self.stream_up_at,1))+' s.')
            if up:self.stream_up_at=now;self.stream_ever_up=True
            self.stream_up=up
        if up:self.milestone('first fresh video in the headset')

    def stream_report(self):
        """Heartbeat stream report: no downtime is reported before the first fresh frame of a session."""
        with self.lock:
            up,since,ever=self.stream_up,self.stream_up_at,self.stream_ever_up
            metrics_state=str(self.metrics.get('state') or '')
        down=0.0 if up or not ever or since is None else min(86400.0,time.monotonic()-since)
        state=metrics_state if up else (self.client_identity.connection_state if self.client_identity else '')
        return {'up':bool(up),'down_s':round(down,1),'state':state if state in STREAM_STATES else ''}

    def monitor_session(self):
        while not self.closing.wait(1):
            if not self.running() or not self.session_link:continue
            try:self.track_stream()
            except Exception as error:self.log('Stream state: '+str(error)[:200])
            try:self.check_graphics_limit()
            except Exception as error:self.log('Graphics check: '+str(error)[:200])
            note=getattr(self,'bitrate_note',None)  # 1.0.0.8: written by the TIMING parser, logged outside its lock
            if note:
                self.bitrate_note=None;self.log(note)
            if self.role=='client':
                try:self.sample_timeline()
                except Exception as error:self.log('Session timeline: '+str(error)[:200])
            try:self.update_headset_status()
            except Exception as error:self.log('Headset status update: '+str(error)[:200])
            try:self.check_local_runtime()
            except Exception as error:self.log('Local SteamVR check: '+str(error)[:200])
            link=self.session_link.snapshot()
            if link['state'] in ('error','waiting'):
                with self.action_lock:
                    link=self.session_link.snapshot()
                    if self.running() and link['state'] in ('error','waiting'):
                        self.log('Rendering host ended this session: '+link.get('message',''))
                        try:self.stop()
                        except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Session cleanup: '+str(error))

    # RC4.4: SteamVR on this PC failing mid-session (the RC4.3 test: its compositor crashed at 17:35:12)
    # leaves the headset unable to show anything, VROSTBridge's own cards included, and RC4.3's native
    # client was then killed by a broken pipe without a word. Notice it, say so, restart once.
    COMPOSITOR_LOST_CHECKS=2   # one-second checks in a row
    RUNTIME_RESTART_SPACING_S=600  # at most one automatic restart per ten minutes

    def local_runtime_processes(self):
        return runtime_processes()

    def local_runtime_state(self):
        """This PC's SteamVR server and compositor now: {'vrserver': identity, 'vrcompositor': identity}."""
        found={}
        for identity in self.local_runtime_processes().values():
            name=runtime_name(identity)  # 1.0.0.7: 'vrserver.exe' on Windows is 'vrserver' here too
            if name in ('vrserver','vrcompositor'):found.setdefault(name,identity)
        return found

    def runtime_process_alive(self,identity):
        """The exact process recorded (same pid, executable, owner and start time) still runs."""
        return bool(identity) and process_identity(identity['pid'])==identity

    REPLACEMENT_PROBATION_S=4  # longer than the native client's 2 s stalled-frames threshold

    def local_runtime_lost(self):
        """'compositor' or 'server' when this session's SteamVR on this PC is gone, else ''. Judged by
        the exact processes recorded once video first arrived, so a SteamVR registry or process list
        that cannot be read never looks like a crash. A compositor SteamVR started in place of a lost
        one is 'replaced' until this session's frames have kept going for REPLACEMENT_PROBATION_S
        (then it becomes the one watched), and lost as soon as they stop."""
        seen=getattr(self,'runtime_seen',None)
        if not seen or self.runtime_process_alive(seen.get('vrcompositor')):return ''
        server=self.runtime_process_alive(seen.get('vrserver'))
        if server:
            replacement=self.local_runtime_state().get('vrcompositor')
            if replacement and replacement!=seen.get('vrcompositor'):
                if getattr(self,'compositor_stalled',False):return 'compositor'
                pending=getattr(self,'compositor_replacement',None) or {}
                now=time.monotonic()
                if pending.get('identity')!=replacement:
                    self.compositor_replacement=pending={'identity':replacement,'since':now}
                if now-pending['since']<self.REPLACEMENT_PROBATION_S:return 'replaced'
                self.runtime_seen=dict(seen,vrcompositor=replacement);self.compositor_replacement=None
                return ''
        return 'compositor' if server else 'server'

    def local_compositor_gone(self):
        return self.local_runtime_lost()=='compositor'

    def check_local_runtime(self):
        process=self.process
        ending=process is not None and (self.stopping is process or getattr(self,'runtime_quit_requested',None) is process)
        if (self.role!='client' or not self.running() or not getattr(self,'stream_ever_up',False) or ending
                or self.session_cancelled.is_set() or getattr(self,'runtime_restarting',False)):
            self.compositor_missing=0;return
        if not getattr(self,'runtime_seen',None):
            now=self.local_runtime_state()
            if now.get('vrserver') and now.get('vrcompositor'):self.runtime_seen=now
            self.compositor_missing=0;return
        lost=self.local_runtime_lost()
        if not lost or lost=='replaced':  # a replacement compositor is on probation: no count yet
            self.compositor_missing=0;return
        self.compositor_missing=getattr(self,'compositor_missing',0)+1
        if self.compositor_missing>=self.COMPOSITOR_LOST_CHECKS:
            self.compositor_missing=0
            self.local_compositor_lost(reason=lost)

    def local_compositor_lost(self,native_exit=None,reason='compositor'):
        """Restart the session once (local and host SteamVR) after SteamVR on this PC failed; a second
        failure within RUNTIME_RESTART_SPACING_S stops it with the explanation instead. A Stop or Start
        by hand meanwhile wins (manual_actions)."""
        now=time.monotonic();clock=time.strftime('%H:%M:%S')
        with self.lock:  # the session monitor and the native client's watcher can both get here
            # A Stop asked for (set before the count of actions by hand rises, under this lock) wins.
            if getattr(self,'runtime_restarting',False) or self.session_cancelled.is_set():return
            last=getattr(self,'runtime_restart_at',None)
            again=last is not None and now-last<self.RUNTIME_RESTART_SPACING_S
            self.runtime_restarting=True
            if not again:self.runtime_restart_at=now
            token=self.manual_actions
        # What happened comes after what VROSTBridge does about it: Game Mode's card shows two lines.
        message=(("SteamVR's compositor on this PC closed unexpectedly at "+clock+
                  ', so the headset could not show anything (not even VROSTBridge\'s reconnecting card).')
                 if reason=='compositor' else
                 ('SteamVR on this PC closed unexpectedly at '+clock+', so the headset could not show anything.'))
        self.log('STEAMVR '+message+('' if native_exit is None else ' The headset streaming process then ended ('+str(native_exit)+').'))
        threading.Thread(target=self.log_compositor_crash_details,daemon=True,name='VROSTBridge-crash-details').start()
        if again:
            try:
                with self.action_lock:
                    try:self.stop()
                    except (OSError,ValueError,subprocess.SubprocessError) as error:self.log('Session cleanup: '+str(error))
                self.runtime_restart=None
                self.startup_error=('Session stopped: it happened again within ten minutes. '+message+
                                    ' Start again when ready; if it keeps happening, send a report.')
            finally:
                self.runtime_restarting=False
            return
        self.runtime_restart={'state':'restarting','message':'Restarting the session… '+message}
        def restart():
            try:
                self.action({'action':'stop','_automatic_restart':True,'_manual_actions':token})
                deadline=time.monotonic()+60
                while True:
                    if self.closing.wait(4):return
                    if self.manual_actions!=token:raise RestartSuperseded('A Stop or Start by hand came first.')
                    try:
                        self.action({'action':'start','_automatic_restart':True,'_manual_actions':token});break
                    except SessionError as error:  # the host is still closing its SteamVR
                        if 'finish stopping' not in str(error) or time.monotonic()>=deadline:raise
                if self.manual_actions!=token:raise RestartSuperseded('A Stop by hand came during the restart.')
                self.runtime_restart={'state':'restarted','message':'Session restarted. '+message,
                                      'until':time.monotonic()+120}
                self.log('STEAMVR Session restarted after SteamVR on this PC closed.')
            except RestartSuperseded as error:
                self.runtime_restart=None;self.log('STEAMVR Automatic restart left to the user: '+str(error))
            except Exception as error:
                if self.manual_actions!=token:
                    self.runtime_restart=None;self.log('STEAMVR Automatic restart left to the user.')
                else:
                    self.runtime_restart={'state':'failed','message':'The session could not restart ('+str(error)[:200]+'). '+message}
                    self.startup_error=self.runtime_restart['message'];self.log('STEAMVR Restart failed: '+str(error)[:200])
            finally:
                self.runtime_restarting=False
        threading.Thread(target=restart,daemon=True,name='VROSTBridge-runtime-restart').start()

    CRASH_KERNEL_LINES=re.compile(r'amdgpu|i915|xe |nouveau|nvidia|drm|gpu reset|ring \S+ timeout|gpu fault|page fault|vrcompositor',re.I)

    def log_compositor_crash_details(self):
        """What this PC recorded about the compositor crash, for the report (RC4.4: the RC4.3 crash left
        nothing in SteamVR's logs): its core dump entry and the kernel's graphics messages of the
        last minutes. Best effort and bounded; journal access may be refused for this user. On Windows (1.0.0.7):
        the Application Error and Hang events of SteamVR and the client, and display driver resets."""
        if on_windows():
            found=windows_crash_details()
            for line in found:self.log('STEAMVR crash detail: '+line)
            if not found:self.log('STEAMVR crash detail: Windows recorded no crash, hang or display driver reset for SteamVR '
                                  'or the client (or its event log is not readable).')
            return
        found=[]
        for command,keep in ((['coredumpctl','--no-pager','--no-legend','list','vrcompositor','--since=-10min'],None),
                             (['journalctl','-k','--since=-5min','--no-pager','-q','-o','short-iso'],self.CRASH_KERNEL_LINES)):
            try:result=subprocess.run(command,capture_output=True,text=True,timeout=8,env=system_environment(),**subprocess_options())
            except (OSError,ValueError,subprocess.SubprocessError):continue
            found+=[line.strip()[:300] for line in (result.stdout or '').splitlines()
                    if line.strip() and (keep is None or keep.search(line))][-30:]
        for line in found[-40:]:self.log('STEAMVR crash detail: '+line)
        if not found:self.log('STEAMVR crash detail: no core dump entry or kernel graphics message found (or not readable).')

    def runtime_restart_status(self):
        value=dict(getattr(self,'runtime_restart',None) or {})
        if value.get('state')=='restarted' and time.monotonic()>value.get('until',0):return None
        value.pop('until',None)
        return value or None

    def private_session_allowed(self, local_address=None):
        if not self.host_prepared or self.closing.is_set():return False
        # The firewall independently admits only the local subnet on every packet (any network type, 1.0.0.14).
        # Cache this slow Windows query briefly, including across pair + start.
        # A request supplies the local address it arrived on; only that adapter
        # must be a home-network address (1.0.0.14: Windows' Private/Public category no longer matters).
        with self.network_guard_lock:
            if local_address is None:
                if time.monotonic()-self.network_guard_time>15:
                    require_home_network();self.network_guard_time=time.monotonic()
            else:
                checked=getattr(self,'network_guard_addresses',None)
                if checked is None:checked=self.network_guard_addresses={}
                if time.monotonic()-checked.get(local_address,-1e9)>15:
                    require_home_network(local_address);checked[local_address]=time.monotonic()
        return True

    def allow_session_pairing(self, local_address=None):
        # 1.0.0.14: paired headset PCs start on any network type; a new one pairs itself only from a Private network.
        if not (self.settings['auto_connect'] and self.private_session_allowed(local_address)):return False
        if local_address is None:return True
        try:return pairing_network_private(local_address)
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            self.log('Pairing refused: '+str(error)[:240]);return False

    def prepare_host(self):
        try:require_home_network()
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            raise HostNetworkNotReady(str(error)) from error
        if self.closing.is_set():raise ValueError('The host companion is closing.')
        # This call may request permission. Automatic preparation gets only one
        # attempt; a denied/failed firewall change requires an explicit UI retry.
        self.host_preparation_attempted=True
        self.setup_host('Firewall')
        if self.closing.is_set():return
        self.host_prepared=True;self.network_guard_time=time.monotonic()
        # Wake-on-LAN readiness and adapter addresses for clients (slow PowerShell queries).
        threading.Thread(target=self.refresh_wake_info,args=(True,),daemon=True,name='VROSTBridge-wake-info').start()
        self.log('Host companion is waiting. Start a session from the headset client.')

    def autostart(self):
        """Opening the UI never starts SteamVR; the host prepares its listener."""
        if self.autostart_attempted:return
        self.autostart_attempted=True
        try:
            self.action({'action':'check'})
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
            if not self.startup_error:self.startup_error=str(error)
            self.log('Companion preparation: '+str(error))
            return
        if self.role=='host':self.prepare_host_after_network()

    def prepare_host_after_network(self):
        """Wait quietly for a boot-time LAN, never repeat a permission prompt."""
        delay=1.0;owned_error=''
        while not self.closing.is_set():
            try:
                with self.action_lock:
                    if self.closing.is_set():return
                    if self.host_prepared:
                        if owned_error and self.startup_error==owned_error:self.startup_error=''
                        return
                    if self.host_preparation_attempted:return
                    self.prepare_host()
            except HostNetworkNotReady as error:
                if self.closing.is_set():return
                message='Waiting for a home network: '+str(error)
                if not self.startup_error or self.startup_error==owned_error:
                    if self.startup_error!=message:self.log('Companion preparation: '+message)
                    self.startup_error=owned_error=message
                # No action lock is held while waiting, and shutdown wakes this
                # immediately. No browser, SteamVR or network-profile mutation.
                # Keep checking at a capped low rate if the PC boots offline or
                # resumes before its network; each OS query has its own timeout.
                self.closing.wait(delay)
                delay=min(delay*2,self.HOST_NETWORK_RETRY_MAX)
                continue
            except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                if self.closing.is_set():return
                if not self.startup_error or self.startup_error==owned_error:self.startup_error=str(error)
                self.log('Companion preparation: '+str(error))
                return
            if owned_error and self.startup_error==owned_error:self.startup_error=''
            return

    def host_side_start(self,quality,cancel):
        """Wake the host if needed, choose the network path, prepare Wi-Fi and obtain the host session lease."""
        self.ensure_host_awake(cancel)
        if cancel.is_set():raise ValueError('Session start cancelled.')
        if self.settings['auto_bitrate']:self.ensure_network_test(cancel)
        if cancel.is_set():raise ValueError('Session start cancelled.')
        network_environment=self.session_link.prepare_network(host_ip=self.settings['host_ip'],cancel=cancel)
        if cancel.is_set():raise ValueError('Session start cancelled.')
        interface=network_environment.get('VROSTBRIDGE_INTERFACE','')
        # Stay on one Wi-Fi access point for the session (no 5-minute background scans).
        if self.settings.get('stay_on_access_point') and interface:
            try:
                roaming=self.ap_lock.begin(interface)
                if roaming.get('reconnected'):
                    self.wait_for_address(interface,cancel)
                    network_environment=self.session_link.prepare_network(host_ip=self.settings['host_ip'],cancel=cancel)
                    interface=network_environment.get('VROSTBRIDGE_INTERFACE','')
            except (OSError,ValueError) as error:
                if cancel.is_set():raise
                self.log('Wi-Fi access-point lock skipped: '+str(error)[:200])
            if cancel.is_set():raise ValueError('Session start cancelled.')
        # Session-only Wi-Fi power saving off on the stream adapter; never blocks a start.
        try:self.wifi_session.begin(interface,self.session_link.snapshot().get('host_ip',''),network_environment.get('VROSTBRIDGE_BIND_IP',''))
        except Exception as error:self.log('Wi-Fi session preparation skipped: '+str(error)[:200])
        self.adapter_counters.begin(interface)  # 1.0.0.13: the adapters' drop counters from here on
        self.video_path_warned=False;self.video_path=None;self.video_path_checked=time.monotonic()
        # A second Wi-Fi adapter on the same network shares the channel, confuses reconnection and, on Linux, can take
        # the video (one network twice). 1.0.0.14: a wired stream leaves it connected where the system keeps the stream
        # on the cable, and a pause that cuts the stream adapter off from the host is undone at once.
        source_ip=network_environment.get('VROSTBRIDGE_BIND_IP','')
        wired=self.wifi_session.status().get('state')=='wired'
        try:
            separated=self.network_settings.separated(interface) if self.network_settings else True
            if self.network_settings:self.network_settings.check(interface)
        except Exception:separated=False
        try:
            other=self.other_wifi.begin(interface,source_ip,self.settings['pause_other_wifi'],wired=wired,separated=separated)
            if other.get('state')=='paused' and not self.host_reachable(interface,source_ip,cancel):
                self.log('NETWORK '+self.route_diagnosis(interface,source_ip))
                self.other_wifi.resume_now('pausing it cut this PC off from the host over '+interface+(
                    '; the video may arrive over Wi-Fi. Fix this PC\'s network settings on the page to keep it on the cable'
                    if wired else ''))
        except Exception as error:self.log('Other Wi-Fi adapter check skipped: '+str(error)[:200])
        if self.wifi_session.status().get('state') not in ('wired','unknown') and interface:self.begin_wifi_diagnostics(interface)
        self.network_monitor.refresh()
        self.session_link.start(self.host_preferences(quality),host_ip=self.settings['host_ip'],cancel=cancel)
        if cancel.is_set():raise ValueError('Session start cancelled.')
        self.milestone('host session accepted')
        try:self.learn_host_mac()
        except Exception as error:self.log('Wake-on-LAN address not learnt: '+str(error)[:160])
        return network_environment

    def host_reachable(self,interface,source_ip,cancel,attempts=4):
        """1.0.0.14: after pausing Wi-Fi, can this PC still reach the host's session port over the stream adapter?"""
        host_ip=(self.session_link.snapshot() or {}).get('host_ip') or self.settings.get('host_ip') or ''
        if not host_ip or not source_ip:return True
        for attempt in range(attempts):
            if cancel.is_set():return True
            try:
                connection=session_link_connect((host_ip,SESSION_PORT),2.0,(source_ip,0),interface)
                connection.close();return True
            except OSError as error:
                last=error
            cancel.wait(1.0)
        self.log('NETWORK the host '+host_ip+' cannot be reached over '+interface+' from '+source_ip+': '+str(last)[:160])
        return False

    def route_diagnosis(self,interface,source_ip):
        """The kernel's route to the host as the stream adapter would use it, for the log (Linux; read-only)."""
        host_ip=(self.session_link.snapshot() or {}).get('host_ip') or ''
        if not sys.platform.startswith('linux') or not host_ip:return 'route not read'
        try:
            result=subprocess.run(['ip','-4','route','get',host_ip,'from',source_ip,'oif',interface],capture_output=True,text=True,timeout=3)
            routes=subprocess.run(['ip','-4','route','show'],capture_output=True,text=True,timeout=3)
        except (OSError,subprocess.SubprocessError) as error:return 'route not read: '+str(error)[:120]
        shown=' | '.join(line.strip() for line in (routes.stdout or '').splitlines()[:8])
        return ('route to the host over '+interface+': '+((result.stdout or result.stderr or '').strip()[:200] or 'none')+
                '; routes: '+shown[:600])

    def check_video_path(self):
        """1.0.0.14: say once per session when the video arrives on another adapter than the stream's (every 15 s)."""
        now=time.monotonic()
        if self.role!='client' or self.video_path_warned or now-self.video_path_checked<15:return
        self.video_path_checked=now
        try:found=adapter_elsewhere(self.adapter_counters.snapshot())
        except Exception:return
        if not found:return
        self.video_path_warned=True;self.video_path=found
        self.log('VIDEO_PATH The video is arriving over '+found['adapter']+' ('+str(found['packets'])+' packets), not over '
                 +found['stream_adapter']+' ('+str(found['stream_packets'])+'), which VROSTBridge chose for the stream. '
                 'This PC is on the home network more than once: Fix this PC\'s network settings on the page, or turn the '
                 'other connection off.')

    def wait_for_address(self,interface,cancel,timeout=15):
        """After a Wi-Fi reconnect, wait until the adapter has its home-network address again."""
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and not cancel.is_set():
            try:
                if any(row.get('interface')==interface and row.get('address') for row in lan_addresses()):return True
            except Exception:pass
            cancel.wait(.5)
        return False
    def begin_wifi_diagnostics(self,interface):
        """Sample the Wi-Fi radio every 5 s during the session for the report (no changes made)."""
        try:self.wifi_diagnostics.begin(interface)
        except Exception as error:self.log('Wi-Fi diagnostics skipped: '+str(error)[:160]);return
        stop=threading.Event();self.wifi_sampler=stop
        def sample():
            while not stop.wait(5):
                try:
                    value=self.wifi_diagnostics.sample()
                    if value.get('roamed'):self.log('WIFI roamed to another access point during the session; expect a short interruption.')
                except Exception:pass
        threading.Thread(target=sample,daemon=True,name='VROSTBridge-wifi-sampler').start()

    def stream_ceiling(self,result):
        """1.0.0.8: launcher/stream_budget.py's quality ceiling for this session's stream (its codec, encoded size
        with foveation, and the headset's refresh), or None before the headset is known."""
        try:
            headset=headset_metadata(result.get('headset'))
            width,height=stream_size(result['codec'],result.get('resolution','compatibility'),headset)
            if result.get('foveation'):width,height=foveated_size(width,height,result['foveation'])
            return quality_ceiling_mbps(result['codec'],2*width,height,headset['refresh_hz'])
        except (ValueError,KeyError,TypeError):return None
    def governed_bitrate(self):
        """The bitrate the governor wants now (heartbeats to 'bitrate-control' hosts), or None."""
        governor=getattr(self,'bitrate_governor',None)
        return governor.mbps if governor is not None and governor.active and self.running() else None
    def host_preferences(self,quality):
        state=(self.wifi_session.status() if getattr(self,'wifi_session',None) else {}).get('state')
        network='wired' if state=='wired' else 'wifi' if state in ('off','on','disabled_for_session','restored','restore_failed') else 'unknown'
        result=dict(quality,encoder=self.settings['encoder'],network=network,timing_lock=self.settings['timing_lock'])
        features=self.host_feature_list()
        # 1.0.0.13: hosts before 1.0.0.13 do not know the Beyond's 90 Hz stream (1920; 'display-modes' hosts do):
        # they get 2016, the size 1.0.0.12 streamed to a Beyond.
        if result.get('resolution')=='beyond-90' and features and 'display-modes' not in features:
            result['resolution']='sharp'
            self.log("The rendering PC's VROSTBridge is older than 1.0.0.13 and does not know the Beyond's 90 Hz stream "
                     '(1920 per eye), so it streams 2016 per eye, as 1.0.0.12 did. Update VROSTBridge there.')
        try:family=headset_family(headset_metadata(quality.get('headset'))) if quality.get('headset') else 'other'
        except ValueError:family='other'
        # Foveation needs the GPU conversion path, which undoes it on this PC.
        # RC4.5: eye cameras on this headset and Baballonia calibrated, as decided for this Start.
        gaze=bool(self.session_gaze if self.session_gaze is not None else self.gaze_planned()) and family=='beyond'
        preset='off' if self.settings['software'] or 'foveation' not in features else foveation_choice(self.settings['foveation'],family,gaze)
        result['foveation']=self.foveation_settings(preset,features,gaze)
        # RC4.5 hosts turn eye tracking on for this session (VRChat's eye input on the host PC) when the
        # client's follows the eyes; older hosts would refuse the field.
        if 'session-gaze' in features:result['gaze']=gaze
        result['ten_bit']=bool(self.settings['ten_bit']) and result['codec']!='H264'
        result['intra_refresh']=bool(self.settings['intra_refresh'])
        tested=self.path_test()
        ceiling=self.stream_ceiling(result)  # 1.0.0.8: the most this stream's picture needs
        if self.settings['auto_bitrate'] and tested and tested.get('recommended_mbps'):
            result['bitrate']=max(10,min(300,int(tested['recommended_mbps'])))
        if self.settings['auto_bitrate'] and ceiling:result['bitrate']=max(10,min(result['bitrate'],ceiling))
        link=link_info(self.stream_interface(),wifi_sample=self.wifi_diagnostics.sample)
        result['pacing_mbps']=pacing_mbps(link,tested,result['bitrate']) if self.settings['pacing'] else 0
        # 1.0.0.8: an average frame arrives within SEND_FRACTION of a frame interval at the pace actually used.
        if self.settings['auto_bitrate'] and result['pacing_mbps'] and link.get('kind')=='wired':
            result['bitrate']=max(10,min(result['bitrate'],max(LINK_MIN_MBPS,int(LINK_SEND_FRACTION*result['pacing_mbps']//5*5))))
        # The bitrate follows the session only where the host can change it while it streams ('bitrate-control').
        self.bitrate_governor.reset(result['bitrate'] if self.settings['auto_bitrate'] and 'bitrate-control' in features else None,
                                    result['bitrate'],self.stream_fps())
        why=''  # 1.0.0.4: why wired pacing is below the link's speed
        if result['pacing_mbps'] and link.get('kind')=='wired':
            if lossless_rate(tested)==result['pacing_mbps']:why=', the most this network path carried in its test before it lost data steadily'
            elif link.get('usb_adapter') and not path_tested(tested):why=', for a USB adapter until a network test measures this path'
        result['keep_warm_minutes']=int(self.settings['keep_warm_minutes'])
        # RC4.1 hosts: predict once on the host (or not at all), and reconnect to this client's address.
        result['host_prediction']=bool(self.settings['motion_prediction'])
        hostname=self.client_identity.status().get('hostname') if self.client_identity else ''
        if isinstance(hostname,str) and CLIENT_HOSTNAME.fullmatch(hostname):result['client_hostname']=hostname
        self.log('Stream: '+result['codec']+(' 10-bit' if result['ten_bit'] else '')+', '+str(result['bitrate'])+' Mbit/s'+(' (measured)' if self.settings['auto_bitrate'] and tested else '')+
                 (' (picture ceiling '+str(ceiling)+' Mbit/s; steps down when the stream struggles, back up when it runs clean)' if self.settings['auto_bitrate'] and ceiling else '')+
                 '; foveated encoding '+(preset+(' with eye tracking' if (result['foveation'] or {}).get('gaze') else '') if result['foveation'] else 'off')+
                 '; pacing '+(str(result['pacing_mbps'])+' Mbit/s'+why if result['pacing_mbps'] else
                              'off (no network test result for this Wi-Fi path)' if link.get('kind')=='wifi' and not tested and self.settings['pacing'] else 'off')+
                 '; loss recovery '+('gradual (intra refresh)' if result['intra_refresh'] else 'keyframes')+'.')
        return result

    def start_both_runtimes(self,quality,display_environment):
        """Start local and host SteamVR together when the last verified headset is known.

        The host profile depends on the physical headset, which the local runtime only
        reports once its display is running. A cached profile from the last verified
        session lets both PCs start at once; if the fresh headset differs, the host is
        restarted with the correct profile, which costs about what the old serial start did.
        """
        cancel=_AnyEvent(self.session_cancelled)
        early=None;outcome={}
        # 1.0.0.13: a chosen Index rate is set in SteamVR here for the session (session_refresh()).
        refresh=self.session_refresh();self.refresh_note=None
        cached=self.settings.get('cached_headset')
        if cached and refresh:
            # The host starts with the rate this PC is about to run, so it need not restart once SteamVR shows it.
            cached=dict(cached,refresh_hz=refresh)
        if cached:
            try:early=session_settings(resolve_stream_preferences(quality,cached))
            except (ValueError,SessionError):early=None
        if early:
            self.log('Starting host and local SteamVR together using the last verified headset ('+cached['model']+').')
            def worker():
                try:outcome['network']=self.host_side_start(early,cancel)
                except BaseException as error:outcome['error']=error
            thread=threading.Thread(target=worker,daemon=True,name='VROSTBridge-host-start');thread.start()
        try:
            self.client_runtime.ensure(True,extra_env=display_environment,**({'refresh':refresh} if refresh else {}))
            if self.session_cancelled.is_set():raise ValueError('Session start cancelled.')
            self.milestone('local SteamVR display verified')
        except BaseException:
            cancel.cancel()
            if early:thread.join(30)
            raise
        # The native probe observes advancing physical display frames and
        # matching refresh before reporting metadata.
        headset=self.client_runtime.headset
        try:
            family=headset_family(headset_metadata(headset))
            if family!=self.current_family() and (self.settings.get('headset_profiles') or {}).get(family):
                quality=self.apply_profile(family,quality)
            self.eye_for_headset(family)
        except ValueError:pass
        if self.headset_audio:
            try:self.headset_audio.begin(headset_metadata(headset)['model'],None,self.settings['headset_audio'])
            except Exception as error:self.log('Headset audio selection skipped: '+str(error)[:200])
        final=session_settings(resolve_stream_preferences(quality,headset))
        display=display_status(self.settings,headset)
        self.refresh_note=self.refresh_outcome(headset,display)
        if self.refresh_note:self.log('DISPLAY_MODE '+self.refresh_note)
        self.log('Display detected: '+display['model']+' at '+str(display['refresh_hz'])+' Hz; client application render recommendation '+str(display['recommended_width'])+' x '+str(display['recommended_height'])+' per eye. Stream target: '+final['codec']+', '+str(display['stream_width'])+' pixels wide per eye ('+final['resolution']+').')
        self.settings['cached_headset']=headset;self.remember_profile();self.save_settings()
        if early:
            while thread.is_alive() and not self.session_cancelled.is_set():thread.join(.2)
            if self.session_cancelled.is_set():
                thread.join(30);raise ValueError('Session start cancelled.')
            if 'network' in outcome and final==early:
                return outcome['network']
            if 'network' in outcome:
                self.log('The headset changed since the last session; restarting host SteamVR with the detected profile.')
                return self.restart_host_side(final,outcome['network'])
            self.log('Early host start did not complete ('+str(outcome.get('error'))[:200]+'); retrying after local SteamVR.')
            if self.session_link.snapshot().get('active'):self.session_link.stop()
        return self.host_side_start(final,self.session_cancelled)

    def restart_host_side(self,quality,network_environment):
        self.session_link.stop()
        deadline=time.monotonic()+45
        while True:
            if self.session_cancelled.is_set():raise ValueError('Session start cancelled.')
            try:
                self.session_link.start(self.host_preferences(quality),host_ip=self.settings['host_ip'],cancel=self.session_cancelled)
                return network_environment
            except SessionError as error:
                if 'finish stopping' not in str(error) or time.monotonic()>=deadline:raise
                self.session_cancelled.wait(1)

    def driver_supports_timing_lock(self):
        """True only for a driver built with patch 0007 (recorded in its build manifest)."""
        try:
            manifest=json.loads((self.host/'vrostbridge-build.json').read_text(encoding='utf-8-sig'))
            return any(str(name).startswith('0007-host-frame-timing-lock') for name in (manifest.get('source_patch_sha256') or {}))
        except (OSError,ValueError,AttributeError):
            return False

    def session_capabilities(self):
        capabilities=[QUALITY_CAPABILITY,PIPELINE_CAPABILITY,STREAM_CAPABILITY,RECOVERY_CAPABILITY]
        if self.driver_supports_timing_lock():capabilities.append(TIMING_LOCK_CAPABILITY)
        return capabilities

    def session_telemetry(self):
        if not self.host_events:return None
        summary=self.host_events.pipeline.summary()
        if not summary:return None
        statistics=self.host_events.status().get('statistics',{})
        lock=statistics.get('VsyncLock',{}).get('values')
        if isinstance(lock,dict):summary=dict(summary,timing_lock={key:lock[key] for key in ('phase_ms','spread_ms','target_ms','steering','updates','enabled') if key in lock})
        stream={}
        config=statistics.get('StreamConfig',{}).get('values')
        if isinstance(config,dict):stream.update({key:config[key] for key in ('pacing_mbps','paced','intra_refresh','gaze_foveation','optical_centre','host_prediction','gaze_hold_ms','gaze_comfort') if key in config})
        # 1.0.0.5 (patch 0018): the colour the encoder streams, as checked with a test image at its start.
        colour=statistics.get('VideoColour',{}).get('values')
        if isinstance(colour,dict):stream.update({'colour_'+key:colour[key] for key in ('path','bits','range','matrix','verified','variant','refused') if key in colour})
        # ...and frames the driver could not send for lack of a head pose (the last 5 s window, while recent).
        missing=statistics.get('NoHeadPose') or {}
        if isinstance(missing.get('values'),dict) and time.time()-float(missing.get('received_utc') or 0)<=12:
            stream.update({'no_pose_dropped':missing['values'].get('dropped',0),'no_pose_sent':missing['values'].get('sent',0)})
        gaze=statistics.get('GazeFoveation',{}).get('values')
        if isinstance(gaze,dict):stream.update({'gaze_'+key:gaze[key] for key in ('gaze_frames','held_frames','fallback_frames','moves','pauses',
                                                                                 'longest_pause_ms','returns') if key in gaze})
        refresh=statistics.get('IntraRefresh',{}).get('values')
        backend=(statistics.get('EncoderBackend',{}).get('values') or {}).get('backend')
        if backend:stream['encoder']=str(backend)[:16]
        if self.session_intra_refresh:
            # NVENC takes intra refresh from the session settings; AMF reports whether it accepted it.
            accepted=backend=='NVENC' or (backend=='AMF' and isinstance(refresh,dict) and refresh.get('accepted')==1)
            stream['intra_refresh_active']=1 if accepted else 0
        if stream:summary=dict(summary,stream=stream)
        if self.stream_recovery and self.stream_recovery.restarts:
            summary=dict(summary,recovery={'restarts':self.stream_recovery.restarts,'reason':self.stream_recovery.last_reason[:160]})
        self.host_streamed=True
        last_session=getattr(self,'last_session',None)
        if last_session and last_session.due():self.keep_last_session()
        return summary
    def keep_last_session(self):
        """1.0.0.14 (host): write what this session has measured so far, for a report made after a restart."""
        if not self.last_session:return
        try:
            events=self.host_events.status() if self.host_events else {}
            gaze=((events.get('statistics') or {}).get('GazeFoveation') or {}).get('values')
            if isinstance(gaze,dict):
                for key in ('left','right'):gaze.pop(key,None)
            self.last_session.keep({'version':role_version(self.role),'host_events':events,'session':self.evidence.report(),
                                    'adapter_counters':self.adapter_counters.snapshot() if getattr(self,'adapter_counters',None) else None,
                                    'driver_statistics':driver_statistics(self.host/'session_log.txt')})
        except Exception as error:self.log('Last session record not written: '+str(error)[:160])

    def host_addons(self):
        now=time.monotonic()
        cached=getattr(self,'_addons_cache',None)
        if cached and now-cached[0]<30:return cached[1]
        try:
            own=json.loads((self.host/'driver.vrdrivermanifest').read_text(encoding='utf-8-sig')).get('name')
        except (OSError,ValueError,AttributeError):
            own='alvr_server'
        try:value=detect_addons(own_driver=own,own_directory=self.host)
        except Exception as error:value=[];self.log('SteamVR add-on check skipped: '+str(error)[:200])
        self._addons_cache=(now,value)
        return value

    def write_timing_flag(self,enabled):
        """Per-session flag read by a patch-0007 driver; absent or false keeps free-running vsync."""
        path=self.host/'vrostbridge-timing.json'
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'vsync_lock':bool(enabled),'margin_ms':2.5})+'\n',encoding='utf-8')
        os.replace(temporary,path)

    def start_host_session(self,preferences):
        preferences=session_settings(preferences)
        with self.action_lock:
            self.startup_error=''
            if self.closing.is_set():raise ValueError('The host companion is closing.')
            if self.sleep_after_stop:self.sleep_after_stop.cancel('Sleep after Stop cancelled: a session started.')
            key=self.preference_key(preferences)
            parked=self.unpark()
            if parked:
                runtime=self.host_control.snapshot()
                if parked['key']==key and self.host_control.active and runtime.get('runtime_state')=='ready' and runtime.get('available'):
                    self.write_session_files(preferences)
                    # The runtime monitor applies this lease's client address through the API.
                    self.stream_recovery.reset();self.session_address=None;self.session_address_attempt=0
                    self.adapter_counters.begin()
                    if self.keep_awake:self.keep_awake.acquire()
                    self.keep_warm_minutes=preferences.get('keep_warm_minutes',0)
                    self.session_intra_refresh=bool(preferences.get('intra_refresh'))
                    self.log('Quick resume: reusing the host SteamVR that is still open.')
                    return
                self.log('Quick resume: '+('the stream settings changed; restarting host SteamVR.' if self.host_control.active else 'host SteamVR had closed; starting it again.'))
                self.host_control.stop()
            self.ensure_host_stopped()
            executable=self.host/'ALVR Dashboard.exe'
            if not executable.exists():raise ValueError('Repair or reinstall VROSTBridge Host.')
            require_home_network()
            if not self.host_prepared:self.prepare_host()
            loader=host_loader_check(executable)
            if not loader['ok']:raise ValueError('The host runtime could not load. '+loader['detail'])
            self.host.mkdir(parents=True,exist_ok=True)
            document=self.session()
            self.audio=microphone_status(preferences['microphone'],document,ROOT/'host/VROSTBridgeAudioProbe.exe',
                                         setup_state=self.microphone_setup.status().get('state') if self.microphone_setup else None)
            self.host_control._set(audio=dict(self.audio))
            self.log(self.audio['message'])
            # RC4.5: a client whose eye tracking follows the eyes turns it on here for its session.
            own_gaze=bool(self.settings['gaze']);gaze=own_gaze or preferences.get('gaze') is True
            if gaze and not own_gaze:self.log('Eye tracking: on for this session (the headset client follows your eyes); VRChat on this PC receives where you look over OSC.')
            doc=configure(document,self.audio['microphone_enabled'],gaze,preferences['codec'],preferences['bitrate'],auto_connect=self.settings['auto_connect'],resolution=preferences.get('resolution','compatibility'),headset=preferences.get('headset'),encoder=preferences.get('encoder','speed'),network=preferences.get('network'),
                          ten_bit=preferences.get('ten_bit',False),foveation=preferences.get('foveation'),intra_refresh=preferences.get('intra_refresh',False))
            lock=self.write_session_files(preferences)
            self.log('Encoder priority: '+('balanced quality' if preferences.get('encoder')=='balanced' else 'lowest latency')+
                     '; client network: '+str(preferences.get('network','not reported'))+
                     '; frame timing lock: '+('on' if lock else 'off' if self.driver_supports_timing_lock() else 'needs a host driver built with patch 0007')+'.')
            addons=self.host_addons()
            if addons:self.log('Other SteamVR add-ons load on this host and may act on the virtual headset: '+', '.join(item['name'] for item in addons)+'. Disable them in SteamVR > Settings > Startup / Shutdown > Manage Add-ons.')
            if preferences.get('headset'):
                hmd=preferences['headset'];video=doc['session_settings']['video'];width=video['transcoding_view_resolution']['Absolute']['width']
                rendered=video['emulated_headset_view_resolution']['Absolute']['width']
                self.log('Host headset identity: '+hmd['model']+' via VROSTBridge. Requested stream width: '+str(width)+' pixels per eye'
                         +(', rendered at '+str(rendered)+' (the headset PC\'s recommended size, scaled down for the encoder)' if rendered!=width else '')
                         +'; physical display '+str(hmd['refresh_hz'])+' Hz.')
            doc=self.session_client(doc,self.session_link.lease_details() if self.session_link else None)
            self.mark_session_gaze(gaze and not own_gaze)
            write_session(self.host/'session.json',doc)
            self.refresh_host_settings()
            # The driver registers the headset with what this client will negotiate (patch 0015).
            nonce=write_init_hint(self.host,preferences.get('headset') if 'init-config' in self.session_features() else None)
            self.setup_host('Register')
            self.evidence=SessionEvidence();self.last_exit=None;self.adapter_counters.begin()
            self.host_control.directory=self.host
            if self.host_events:self.host_events.reset()
            if self.keep_awake:self.keep_awake.acquire()
            self.session_key=key;self.keep_warm_minutes=preferences.get('keep_warm_minutes',0)
            self.session_intra_refresh=bool(preferences.get('intra_refresh'))
            self.stream_recovery.reset()
            try:self.host_control.start(init_nonce=nonce)
            except BaseException:
                if self.keep_awake:self.keep_awake.release()
                raise

    def session_client(self,document,lease):
        """Add the session client's address to ALVR's list so it reconnects without discovery."""
        self.session_address=None;self.session_address_attempt=0
        if not isinstance(lease,dict):return document
        hostname,address=lease.get('client_hostname'),lease.get('ip')
        if not isinstance(hostname,str) or not hostname or not isinstance(address,str) or not address:return document
        existing=(document.get('client_connections') or {}).get(hostname) if isinstance(document,dict) else None
        if not self.settings['auto_connect'] and not (isinstance(existing,dict) and existing.get('trusted') is True):
            # ALVR removes unapproved clients when it starts; the address follows approval.
            self.log('The headset client is not approved on this host yet; its address is added once you approve it.')
            return document
        known=dict(self.settings.get('session_client_addresses') or {})
        try:result=session_client_address(document,hostname,address,auto_connect=self.settings['auto_connect'],previous=known.get(hostname))
        except (ValueError,KeyError,TypeError) as error:
            self.log('Client reconnection address not added: '+str(error)[:160]);return document
        self.remember_client_address(hostname,address)
        self.log('ALVR reconnects to the headset client at '+address+' directly, without waiting for discovery.')
        return result

    def remember_client_address(self,hostname,address):
        known=dict(self.settings.get('session_client_addresses') or {});known.pop(hostname,None);known[hostname]=address
        self.settings['session_client_addresses']=dict(list(known.items())[-8:])
        self.session_address=(hostname,address)
        try:self.save_settings()
        except OSError as error:self.log('Host settings not saved: '+str(error)[:160])

    def follow_client_address(self,lease,rows=None):
        """Keep the lease's client address in ALVR's reconnection list (quick resume, approval, DHCP or adapter change)."""
        hostname,address=lease.get('client_hostname'),lease.get('ip')
        if not isinstance(hostname,str) or not hostname or not isinstance(address,str) or not address:return
        if rows is None:
            try:rows=self.host_control.snapshot().get('clients') or []
            except (AttributeError,TypeError):rows=[]
        row=next((item for item in rows if isinstance(item,dict) and item.get('hostname')==hostname),None)
        if not self.settings['auto_connect'] and not (isinstance(row,dict) and row.get('trusted') is True):return
        if isinstance(row,dict) and address in (row.get('manual_ips') or []):
            if self.session_address!=(hostname,address):self.remember_client_address(hostname,address)
            return
        now=time.monotonic()
        if now-self.session_address_attempt<5:return
        self.session_address_attempt=now
        previous=(self.settings.get('session_client_addresses') or {}).get(hostname)
        try:self.host_control.set_client_address(hostname,address,previous,trust=self.settings['auto_connect'])
        except (OSError,ValueError) as error:
            self.log('Client reconnection address not updated: '+str(error)[:160]);return
        self.remember_client_address(hostname,address)
        self.log('ALVR reconnects to the headset client at '+address+' directly.')

    def stream_recovery_tick(self,rows):
        """Called by the host runtime monitor (about every 0.75 s) while SteamVR is ready."""
        lease=self.session_link.lease_details() if self.session_link else None
        if not isinstance(lease,dict):lease=None
        if lease and lease.get('started'):self.follow_client_address(lease,rows)
        return self.stream_recovery.check(lease,rows)

    def write_session_files(self,preferences):
        """Per-session driver options; the driver reads them whenever a client connects."""
        lock=self.driver_supports_timing_lock() and preferences.get('timing_lock',True)
        self.write_timing_flag(lock)
        features=self.session_features()
        config=stream_config(preferences,features)
        write_stream_config(self.host,config)
        foveation=preferences.get('foveation')
        self.log('Stream options: '+preferences['codec']+(' 10-bit' if preferences.get('ten_bit') and preferences['codec']!='H264' else '')+
                 '; foveated encoding '+(('on, '+('following the gaze' if config['gaze_foveation'] else 'centred on each eye\'s view' if config['optical_centre'] else 'fixed centre')) if foveation else 'off')+
                 '; pacing '+(str(config['pacing_mbps'])+' Mbit/s' if config['pacing_mbps'] else 'off')+
                 '; intra refresh '+('on' if preferences.get('intra_refresh') else 'off')+
                 '; motion prediction '+('on (predicted once, on this PC)' if config['host_prediction'] else 'off')+'.')
        return lock

    def stop_host_session(self):
        with self.action_lock:
            self.adapter_counters.end()
            if self.host_streamed:self.keep_last_session()  # 1.0.0.14: the session's final figures
            if self.remote_diagnostics:self.remote_diagnostics.capture()
            forced,self.force_full_stop=self.force_full_stop,False
            runtime=self.host_control.snapshot()
            minutes=self.keep_warm_minutes
            if (minutes>0 and not forced and not self.closing.is_set() and self.host_control.active and
                    runtime.get('runtime_state')=='ready' and runtime.get('available')):
                self.park(minutes);return
            self.finish_host_stop()

    def host_power_status(self):
        parked=self.parked
        return {'keep_awake':self.keep_awake.status() if self.keep_awake else None,
                'sleep_after':self.sleep_after_stop.status() if self.sleep_after_stop else None,
                'wake':dict({key:self.wake_info.get(key) for key in ('ready','message','error','fast_startup')},
                            adapters=[{key:row.get(key) for key in ('name','description','kind','wake_on_magic_packet','wake_armed')}
                                      for row in (self.wake_info.get('adapters') or [])[:8] if isinstance(row,dict)],
                            session_adapter_known=bool(self.host_adapters)),
                'qos':self.qos_status,'features':self.session_features(),
                'resume':{'parked':True,'remaining_seconds':max(0,int(parked['deadline']-time.monotonic()))} if parked else {'parked':False}}

    def setup_host(self, action):
        if sys.platform!='win32':raise ValueError('The rendering host must run on Windows.')
        result=subprocess.run(['powershell','-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',str(ROOT/'host/Host-Setup.ps1'),'-Action',action,'-HostDirectory',str(self.host)],capture_output=True,text=True,**subprocess_options())
        self.log(result.stdout+result.stderr)
        if result.returncode:raise ValueError('Host setup failed; see the log. If Windows requested permission, it must be allowed to install the private-LAN firewall rules.')
    def action(self,body):
        action=body.get('action')
        if body.get('_automatic_restart') and body.get('_manual_actions')!=self.manual_actions:
            raise RestartSuperseded('A Stop or Start by hand came first.')
        self.journal.record('action',{'name':str(action)})
        if action=='stop':
            self.session_cancelled.set()
            if self.client_runtime:self.client_runtime.cancel()
        if action=='stop' or (action=='start' and not body.get('_automatic_start')):self.autostart_cancelled.set()
        if action in ('stop','start') and not body.get('_automatic_restart'):
            with self.lock:
                self.manual_actions+=1
                if action=='stop':self.runtime_restart=None  # its message belonged to the session being stopped
        if action=='send-report' and self.role=='client':
            self.send_report_async();return
        with self.action_lock:
            if body.get('_automatic_restart') and body.get('_manual_actions')!=self.manual_actions:
                raise RestartSuperseded('A Stop or Start by hand came first.')
            if action=='set-startup' and self.role=='host':
                self.windows_startup.set_enabled(body.get('enabled'));return
            if action=='windows-startup' and self.role=='client':
                # 1.0.0.7: the Windows client in the tray at sign-in (VROSTBridge Client.exe --background).
                if not self.windows_startup:raise ValueError('Starting at sign-in is part of the Windows client.')
                self.windows_startup.set_enabled(body.get('enabled'))
                self.log('Start at sign-in '+('on' if body.get('enabled') else 'off')+'.');return
            if action=='stop':
                if self.role=='host' and self.session_link:
                    # A local Stop always closes SteamVR, including a session kept open for quick resume.
                    self.force_full_stop=True
                    link=self.session_link.request_stop()
                    if not link.get('lease_id') and self.parked:
                        self.unpark();self.log('Host user closed the SteamVR kept open for quick resume.');self.finish_host_stop()
                    if not link.get('lease_id'):self.force_full_stop=False
                else:self.stop()
                self.startup_error='';return
            if action=='build-host' and PACKAGED:raise ValueError('This package includes a prebuilt host.')
            if action=='build-host' and self.role=='host':
                self.ensure_host_stopped()
                self.build=subprocess.Popen(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/Build-Host.ps1')],cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',**subprocess_options())
                def build_log(process):
                    for line in process.stdout:self.log('Build: '+line)
                    self.log('Host build exited: '+str(process.wait()))
                threading.Thread(target=build_log,args=(self.build,),daemon=True).start();return
            if action=='host-menu':
                if self.role=='host':self.host_control.menu()
                else:self.client_command('menu')
                return
            if action=='sync-playspace' and self.role=='client':self.client_command('playspace');return
            if action=='restore-client-runtime' and self.role=='client':
                if self.running():raise ValueError('Stop the client before restoring local menus.')
                
                if not self.client_runtime.restore():raise ValueError('Close local SteamVR before restoring saved settings. The receipt was retained.')
                return
            if action in ('enable-game-mode','disable-game-mode','steam-add','steam-remove') and self.role=='client' and not self.game_mode_setup:
                raise ValueError("Steam's Game Mode belongs to the Bazzite client; on Windows, start sessions from this page or the tray icon.")
            if action in ('enable-game-mode','disable-game-mode') and self.role=='client':
                if self.running() or self.client_runtime.processes():raise ValueError('Stop the session and close local SteamVR before changing Game Mode setup.')
                if action=='enable-game-mode':self.game_mode_setup.enable(body.get('connector','auto'))
                else:self.game_mode_setup.disable()
                return
            if action in ('steam-add','steam-remove') and self.role=='client':
                if self.running():raise ValueError('Stop the session first: Steam restarts to apply this.')
                if action=='steam-add':
                    self.steam_shortcut.add()
                    # Game Mode display handling: the headset is never the Game Mode screen,
                    # and it is reserved for SteamVR (display lease) when supported.
                    try:
                        if not self.game_mode_setup.status().get('enabled'):self.game_mode_setup.enable('auto')
                        else:self.game_mode_setup.update_wrapper()
                        self.log('Game Mode is set up: a connected headset is never used as the Game Mode screen. '
                                 'Re-enter Game Mode once for it to apply.')
                    except (OSError,ValueError) as error:
                        self.log('VROSTBridge is in Steam, but Game Mode display handling is not set up: '+str(error))
                else:
                    self.steam_shortcut.remove()
                    try:
                        if self.game_mode_setup.status().get('enabled'):self.game_mode_setup.disable()
                    except (OSError,ValueError) as error:self.log('Game Mode display handling was kept: '+str(error))
                return
            if action=='set-sharpening' and self.role=='client':
                # RC4.3: applies to the running session at once (fused frame pass).
                level=body.get('level')
                if not isinstance(level,str) or level not in SHARPENING_LEVELS:raise ValueError('Choose a sharpening level.')
                self.settings['sharpening']=level;self.save_settings()
                if self.running():self.native_command('sharpen '+str(SHARPENING_LEVELS[level]))
                self.log('Sharpening '+level+('; applied to the running session.' if self.running() else '.'));return
            if action=='set-reaim' and self.role=='client':
                if type(body.get('enabled')) is not bool:raise ValueError('Invalid frame re-aim setting.')
                self.settings['reaim']=body['enabled'];self.save_settings()
                if self.running():self.native_command('reaim '+('1' if body['enabled'] else '0'))
                with self.lock:self.reaim_paused=False  # the user's choice replaces an automatic pause
                self.log('Frame re-aim '+('on' if body['enabled'] else 'off')+('; applied to the running session.' if self.running() else '.'));return
            if action=='gpu-auto' and self.role=='client':
                # 1.0.0.4: the person chose it on a power-balance warning; steamos-manager applies it at once.
                problem=self.steamos_power.set_gpu_automatic()
                if problem:raise ValueError('The GPU clock could not be set to automatic ('+problem+'). Change it where it was set: '
                                            +gpu_clock_hint())
                self.steamos_settings=self.steamos_power.read(force=True)
                with self.lock:self.cpu_clocks.reset();self.power_held=None;self.power_notice_until=0
                self.log('POWER_BALANCE GPU clock set to automatic (steamos-manager).');return
            if action=='use-standard' and self.role=='client':
                # RC4.3 one-tap advice when this PC's graphics fall behind: saved for the next Start,
                # allowed during a session (the running stream keeps its size).
                preset=self.sharp_preset() or {'id':'sharp','label':'Standard','width':2016,'height':2016}
                if not preset.get('id'):
                    # 1.0.0.13: a Beyond streams the mode it is in, and an Index at its lowest rate has none lower.
                    raise ValueError(preset.get('hint') or 'This headset has no lighter stream choice.')
                quality=client_stream_preferences({'codec':self.settings['codec'],'bitrate':self.settings['bitrate'],'resolution':preset['id']})
                self.settings.update(quality);self.remember_profile();self.save_settings()
                self.log(('Refresh rate' if preset.get('refresh_hz') else 'Stream size')+' set to '+preset['label']+' ('+str(preset['width'])
                         +' x '+str(preset['height'])+' per eye)'+('; it applies at the next Start.' if self.running() else '.'));return
            if action=='align-check' and self.role=='client':
                if not self.running() or self.metrics.get('state')!='streaming':raise ValueError('Start a session and wait for video before the check.')
                if not self.native_command('align-check'):raise ValueError('The headset client did not accept the check.')
                self.alignment_check={'state':'running','started':time.time()}
                self.log('Frame alignment check started: watch something that stays still for 10 seconds.');return
            if action=='align-answer' and self.role=='client':
                if type(body.get('jumped')) is not bool:raise ValueError('Answer whether the picture jumped.')
                jumped=body['jumped'];self.settings['reaim']=not jumped;self.save_settings()
                if self.running():self.native_command('reaim '+('0' if jumped else '1'))
                with self.lock:self.reaim_paused=False
                message=('SteamVR on this PC already re-aims frames, so VROSTBridge\'s own re-aim is now off '
                         '(one resampling less, slightly sharper).' if jumped else
                         'SteamVR does not re-aim frames on this PC, so VROSTBridge keeps re-aiming them to where '
                         'your head is when they are shown.')
                self.alignment_check={'state':'answered','jumped':jumped,'message':message,'time':time.time()}
                self.log('Frame alignment check: picture '+('jumped' if jumped else 'stayed still')+'. '+message);return
            if action=='client-preferences' and self.role=='client':
                if self.running():raise ValueError('Stop the session before changing stream preferences.')
                quality=client_stream_preferences({key:body.get(key,self.settings[key]) for key in ('codec','bitrate','resolution')})
                self.apply_client_options({key:value for key,value in body.items() if key!='action'})
                address=body.get('host_ip',self.settings['host_ip'])
                if not isinstance(address,str) or len(address)>64:raise ValueError('Invalid host IPv4 address.')
                if address.strip():
                    import ipaddress
                    value=ipaddress.IPv4Address(address.strip())
                    if not value.is_private or value.is_loopback or value.is_unspecified or value.is_multicast:raise ValueError('Enter the host home-network IPv4 address or use Automatic.')
                self.settings.update(quality,host_ip=address.strip());self.remember_profile();self.save_settings();return
            if action=='check':
                if self.game_mode_setup:self.game_mode_setup.refresh()
                if self.async_reprojection:self.async_reprojection.check()
                if self.role=='host':threading.Thread(target=self.refresh_wake_info,args=(True,),daemon=True).start()
                executable=self.host/'ALVR Dashboard.exe' if self.role=='host' else self.client
                self.readiness=check_machine(self.role,executable);return
            if action=='allow-client-network' and self.role=='client':
                self.client_network.allow();self.log(self.client_network.status().get('message','Client network permission checked.'));return
            if action=='select-client-network' and self.role=='client':
                if self.running():raise ValueError('Stop the session before changing its network adapter.')
                selected=self.session_link.select_interface(body.get('interface',''))
                self.network_monitor.refresh()
                self.log('Client network adapter: '+(selected['interface'] or 'Automatic')+'. Applies to the next session.');return
            if action=='network-test' and self.role=='client':
                if self.running():raise ValueError('Stop the session before testing the network.')
                self.session_cancelled.clear()
                result=self.run_network_test(self.session_cancelled)
                recommended=(result.get('recommendation') or {}).get('recommended_mbps')
                if recommended and not self.settings['auto_bitrate']:self.log('Network test suggests '+str(recommended)+' Mbit/s. Turn on automatic bitrate to use it.')
                return
            if action=='wake-host' and self.role=='client':
                if not self.settings.get('host_mac'):raise ValueError("The host PC's network address is learnt after the first session with it.")
                host_ip=self.settings['host_ip'] or self.session_link.host_ip
                self.session_cancelled.clear()
                def answered():
                    try:return self.session_link.discover(self.settings['host_ip'],cancel=self.session_cancelled)
                    except (OSError,ValueError):return None
                self.host_waker.discover=answered
                result=self.host_waker.wake_and_wait(self.settings['host_mac'],wake_targets(host_ip,local_interfaces(lan_addresses())),timeout=120,cancel=self.session_cancelled)
                self.log(result['message']);return
            if action=='fix-network' and self.role=='client':  # 1.0.0.14
                if not self.network_settings:raise ValueError('Only the headset PC has these network settings.')
                result=self.network_settings.repair()
                if result.get('state')!='ok':raise ValueError(result.get('message') or 'The network settings were not changed.')
                if self.running():self.log('The network settings apply at once; a Wi-Fi adapter paused for this session reconnects at Stop.')
                return
            if action=='repair-async' and self.role=='client':
                if self.client_runtime.processes():raise ValueError('Close SteamVR on this PC first; the repair applies when it next starts.')
                result=self.async_reprojection.repair()
                if result.get('state')!='ready':raise ValueError(result.get('message') or 'The repair did not complete.')
                return
            if action=='wifi-powersave-off' and self.role=='client':
                if self.running():raise ValueError('Stop the session first; '+('Windows changes the power plan for all networks.' if on_windows()
                                                   else 'Wi-Fi reconnects briefly to apply this.'))
                self.log(self.wifi_session.disable_permanently(self.session_link.network_status().get('source_interface') or self.session_link.network_status().get('interface') or ''));return
            if action=='microphone-setup' and self.role=='host':
                # Downloads VB-CABLE from VB-Audio and opens its own setup, in the background (host/microphone_setup.py).
                self.microphone_setup.start();return
            if action=='microphone-check' and self.role=='host':
                threading.Thread(target=self.microphone_setup.check,daemon=True).start();return
            if action=='host-power' and self.role=='host':
                minutes=body.get('sleep_after_minutes')
                if type(minutes) is not int or minutes not in SLEEP_AFTER_CHOICES:raise ValueError('Choose when the host PC may sleep after a session.')
                self.settings['sleep_after_minutes']=minutes;self.save_settings()
                self.sleep_after_stop.configure(minutes or 0)
                self.log('Sleep after the session: '+('off' if not minutes else str(minutes)+' minutes')+'.');return
            if action=='qos' and self.role=='host':
                if type(body.get('enabled')) is not bool:raise ValueError('Invalid Wi-Fi priority setting.')
                self.qos_status=host_qos.apply(body['enabled'])
                self.log(str((self.qos_status or {}).get('message','Windows QoS updated.')));return
            # RC4.5: eye tracking, set up and run by VROSTBridge (launcher/eye_tracking.py, launcher/baballonia.py).
            if action=='camera-start' and self.role=='client':action='eye-open'  # RC4.4 pages' Start eye cameras
            if action=='camera-stop' and self.role=='client':action='eye-close'
            if action=='eye-tracking' and self.role=='client':
                if type(body.get('enabled')) is not bool:raise ValueError('Choose whether eye tracking is on.')
                self.settings['gaze']=body['enabled'];self.save_settings()
                self.log('Eye tracking '+('on' if body['enabled'] else 'off')+('; applies at the next Start.' if self.running() else '.'));return
            if action=='eye-setup' and self.role=='client':
                if body.get('accept_licence') is not True:
                    raise ValueError("Read Baballonia's licence first: it is free for personal, non-commercial use only.")
                manager=self.eye.manager
                update=manager.update_available()  # 1.0.0.3: an earlier VROSTBridge's Baballonia, a newer one pinned
                if not manager.install_async():
                    if manager.installed() and not manager.update_available():raise ValueError('Baballonia is already set up.')
                    raise ValueError('Baballonia is already being set up.')
                self.log('Eye tracking: '+('updating Baballonia to ' if update else 'setting up Baballonia ')+manager.pinned['version']+
                         ' (licence accepted: Babble Software Distribution License 1.0, non-commercial).');return
            if action=='eye-setup-cancel' and self.role=='client':
                self.eye.manager.cancel_install();return
            if action=='eye-open' and self.role=='client':
                if not self.eye.manager.installed():raise ValueError('Set up eye tracking first.')
                self.eye.begin('user');return
            if action=='eye-close' and self.role=='client':
                self.eye.close_baballonia();return
            if action=='client-option' and self.role=='client':
                # 1.0.0.8: switches saved and applied at once, also during a session.
                name,value=body.get('name'),body.get('enabled')
                if name not in self.LIVE_OPTIONS or type(value) is not bool:raise ValueError('Invalid client setting.')
                self.settings[name]=value;self.save_settings()
                if name=='balance_performance' and not value and self.session_tuning:self.session_tuning.end()
                if name=='keep_frame_rate' and not value:  # 1.0.0.11: back to full size at once
                    with self.scale_lock:  # after any size being sent now, never before it
                        with self.lock:self.output_scale.reset();self.scale_event=None
                        if self.running():self.native_command('scale 100')
                self.log(self.LIVE_OPTIONS[name]+(': on.' if value else ': off.'));return
            if action=='base-stations' and self.role=='client':
                # 1.0.0.8: on or off now (the This PC card), in the background; a fresh search, so new stations are learned.
                power=body.get('power')
                if power not in ('on','off'):raise ValueError('Choose whether to turn the base stations on or off.')
                self.base_stations.set_power_background(power,search=True);return
            if action=='eye-calibration' and self.role=='client':
                # 1.0.0.8: go back to an earlier calibration (Baballonia closed), and its own centring.
                name=body.get('name')
                if name=='previous':
                    previous=self.eye.manager.previous_calibration()
                    if not previous:raise ValueError('There is no earlier calibration on this PC.')
                    name=previous['name']
                if self.eye.status().get('owners') or self.running():raise ValueError('Stop the session first.')
                self.eye.manager.use_calibration(name);return
            if action=='gaze-centre-reset' and self.role=='client':
                self.gaze_centre.reset();self.log('Eye tracking: gaze centring for this calibration starts again from none.');return
            if action=='recommended-settings' and self.role=='client':
                if self.running():raise ValueError('Stop the session before changing stream settings.')
                self.apply_recommended_settings();return
            if action=='eye-reach-reset' and self.role=='client':
                # 1.0.0.6: measure how far the gaze reaches afresh (look to each edge of the view, then read it).
                relay=self.relay
                if not relay or not hasattr(relay,'reset_reach'):raise ValueError('Eye tracking is not running.')
                relay.reset_reach();self.log('Eye tracking: measuring how far the gaze reaches afresh.');return
            if action=='eye-show' and self.role=='client':
                # RC4.6: Baballonia starts minimised for a session in Desktop Mode; this brings its window back.
                self.eye.show_window();return
            if action=='eye-camera-access' and self.role=='client':
                # In the background: the password prompt may stay open (or never show in Game Mode).
                if not self.eye.camera_access_async():raise ValueError('Camera access is already being asked for.')
                return
            if action=='eye-remove' and self.role=='client':
                if self.eye.status().get('owners'):raise ValueError('Close Baballonia and stop the session first.')
                manager=self.eye.manager
                yours=callable(getattr(manager,'installation',None)) and manager.installation()=='yours'
                manager.remove()
                folder=str(getattr(manager,'config','') or '~/.config/ProjectBabble')
                self.log(('Eye tracking: VROSTBridge no longer uses your own Baballonia; it stays installed. ' if yours else
                          'Eye tracking: Baballonia removed. ')+'Its settings and calibration stay in '+folder+'.');return
            if action=='start':
                if self.closing.is_set():raise ValueError('VROSTBridge is closing.')
                # A second Start while a session runs changes nothing (RC4.3: it used to clear the
                # running session's stream tracking and startup timeline before refusing).
                if self.role=='client' and self.running():raise ValueError('Already running')
                self.session_cancelled.clear()
                if body.get('_automatic_start') and self.autostart_cancelled.is_set():return
                self.startup_error=''
                if self.role=='client':
                    self.start_clock=time.monotonic();self.milestones={}
                    self.reset_stream_tracking()
                    if self.game_mode_log and in_game_mode():self.game_mode_log.begin(role_version(self.role))
                if self.role=='host':
                    raise ValueError('Start the session from the headset client.')
                else:
                    if not self.client.exists():
                        raise ValueError('The headset streaming program is missing. Repair or reinstall VROSTBridge Client.' if on_windows() else
                                         'Build/install the native client first using Build-Client-Linux.sh')
                    if self.running():raise ValueError('Already running')
                    self.close_gaze(end_session=False)  # a folder an earlier start left; eye tracking may still run
                    self.client_identity.reset()
                    quality=client_stream_preferences({key:body.get(key,self.settings[key]) for key in ('codec','bitrate','resolution')})
                    if 'lightweight' in body and type(body['lightweight']) is not bool:raise ValueError('Invalid legacy setting.')
                    self.apply_client_options({key:value for key,value in body.items() if key!='action'})
                    address=body.get('host_ip',self.settings['host_ip'])
                    if not isinstance(address,str) or len(address)>64:raise ValueError('Invalid host IPv4 address.')
                    quality['microphone']=self.settings['microphone']
                    self.settings.update(quality,host_ip=address.strip())
                    self.remember_profile()
                    self.save_settings()
                    self.client_generation+=1
                    threading.Thread(target=self.prepare_client_system,args=(self.client_generation,),daemon=True,name='VROSTBridge-client-system').start()
                    # RC4.5: eye tracking comes up beside the start (cameras, Baballonia, relay), so gaze is
                    # there by the first frame; the sharp region follows it only when Baballonia is calibrated.
                    self.session_gaze=self.eye_ready();self.session_eye_on=bool(self.settings['gaze'])
                    gaze_socket=None;self.gaze_socket=None
                    try:
                        if self.settings['gaze'] and self.eye is not None:
                            if on_windows():
                                # 1.0.0.7: Windows has no datagram sockets in the file system; the gaze reaches
                                # the native client over loopback UDP (gaze_provider.gaze_target refuses anything else).
                                gaze_socket=self.gaze_socket=('127.0.0.1',free_udp_port())
                            else:
                                self.runtime_directory=tempfile.mkdtemp(prefix='vrostbridge-',dir=os.environ.get('XDG_RUNTIME_DIR'))
                                gaze_socket=self.gaze_socket=Path(self.runtime_directory)/'gaze.sock'
                            if self.current_family() in (None,'beyond'):  # the last headset; checked again once detected
                                try:self.eye.begin('session',gaze_socket)
                                except OSError as error:self.log('Eye tracking unavailable for this session: '+str(error)[:200])
                                self.log('Eye tracking: on for this session; '+('the sharp region follows your eyes.' if self.session_gaze else
                                         'Baballonia is not '+('calibrated' if self.eye.manager.installed() else 'set up')+' yet, so the fixed Balanced region is used.'))
                    except BaseException as error:
                        self.startup_error=str(error);self.log('Client startup failed: '+str(error))
                        if self.game_mode_log:self.game_mode_log.end('failed: '+str(error))
                        self.rollback_start();raise
                    try:
                        display_environment=self.lease_runtime.prepare(self.session_cancelled) if self.lease_runtime else {}
                        network_environment=self.start_both_runtimes(quality,display_environment)
                    except BaseException as error:
                        self.startup_error=str(error);self.log('Client startup failed: '+str(error))
                        if self.game_mode_log:self.game_mode_log.end('failed: '+str(error))
                        self.rollback_start();raise
                    try:
                        args=[str(self.client)]
                        env=steam_environment(display_environment);env.update(network_environment);env['VROSTBRIDGE_MICROPHONE']='1' if self.settings['microphone'] else '0'
                        if self.settings['software']:args.append('--software-decode')
                        if not self.settings['late_frame_catchup']:args.append('--no-late-frame-catchup')
                        if not self.settings['motion_prediction']:args.append('--no-prediction')
                        # 1.0.0.14: the hands' acceleration folded into the poses the host extrapolates; a settings-file switch for comparison.
                        if self.settings.get('prediction_acceleration') is False:args.append('--no-acceleration')
                        if not self.settings['reaim']:args.append('--no-reaim')
                        if not self.settings['one_pass']:args.append('--two-pass')
                        if not self.settings.get('lens_area',True):args.append('--full-frame')
                        args+=['--sharpening',str(SHARPENING_LEVELS.get(self.settings.get('sharpening'),0))]
                        if isinstance(gaze_socket,tuple):args+=['--gaze-udp',str(gaze_socket[1])]
                        elif gaze_socket is not None:args+=['--gaze-socket',str(gaze_socket)]
                        if self.session_cancelled.is_set():raise ValueError('Session start cancelled.')
                        self.launch(self.client_runtime.native_command(args[1:]),env)
                        self.milestone('streaming client started')
                        if self.game_mode_log:self.game_mode_log.end('started')
                    except BaseException as error:
                        self.startup_error=str(error);self.log('Client startup failed: '+str(error))
                        if self.game_mode_log:self.game_mode_log.end('failed: '+str(error))
                        self.rollback_start();raise
                return
            if self.role!='host':raise ValueError('This action belongs to the host')
            if action=='trust-client':self.host_control.trust(body.get('hostname'));return
            if action=='add-client':self.host_control.add(body.get('client_hostname',''),body.get('client_ip',''));return
            if action=='forget-client':
                self.host_control.forget(body.get('hostname'))
                self.settings['auto_connect']=False;self.save_settings();return
            if action=='firewall':self.prepare_host();self.startup_error='';return
            if action=='configure':
                self.ensure_host_stopped();self.host.mkdir(parents=True,exist_ok=True)
                self.settings['auto_connect']=body.get('auto_connect',self.settings['auto_connect']) is True
                self.save_settings()
                # Saving preferences cannot enable auto-trust until the network check at Start.
                doc=configure(self.session(),body.get('microphone',False),body.get('gaze',False),body.get('codec','H264'),int(body.get('bitrate',80)),auto_connect=False)
                if body.get('client_ip'):doc=manual_client(doc,body.get('client_hostname',''),body['client_ip'])
                self.mark_session_gaze(False)  # this PC's own choice from now on
                write_session(self.host/'session.json',doc);self.log('Host preferences saved. Start the next session from the headset client; it selects codec and bitrate.');return
            if action in ('register','unregister'):
                self.ensure_host_stopped()
                self.setup_host(action.title());return
            raise ValueError('Unknown action')

HTML=(Path(__file__).with_name('desktop.html')).read_text(encoding='utf-8')

# RC4.6: the pages are served on a stable port per role when it is free (otherwise any free port), so a page
# opened again is the same origin as the one already open, across companion restarts too: the newer page then
# tells the older one to close (desktop.html, BroadcastChannel). Loopback only, as before.
PAGE_PORTS={'client':9947,'host':9948}

class PageServer(http.server.ThreadingHTTPServer):
    allow_reuse_address=os.name!='nt'  # Windows' SO_REUSEADDR would let a second listener share the port

def page_server(role,handler,ports=None):
    preferred=(ports or PAGE_PORTS).get(role,0)
    for port in ((preferred,0) if preferred else (0,)):
        try:return PageServer(('127.0.0.1',port),handler)
        except OSError:
            if port==0:raise

def server_for(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,kind='application/json',cache=False):
            raw=body if isinstance(body,bytes) else (json.dumps(body) if kind=='application/json' else body).encode()
            try:
                self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','max-age=3600' if cache else 'no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass  # Browser closed/reloaded the tab.
        def authenticated(self):
            origin=self.headers.get('Origin')
            expected='http://127.0.0.1:'+str(self.server.server_port)
            return self.headers.get('Host')==expected.removeprefix('http://') and (origin is None or origin==expected) and secrets.compare_digest(self.headers.get('X-VROSTBridge-Token',''),app.token)
        def do_GET(self):
            if self.path=='/':self.send(200,HTML,'text/html; charset=utf-8')
            elif self.path.startswith('/brand/') and self.path[len('/brand/'):] in BRAND_FILES:
                name=self.path[len('/brand/'):]
                logo=logo_resource(name)
                if logo:self.send(200,logo.read_bytes(),BRAND_FILES[name],cache=True)
                else:self.send(404,{'error':'Logo unavailable'})
            elif self.path.startswith('/fonts/'):
                font=font_resource(self.path[len('/fonts/'):])
                if font:self.send(200,font.read_bytes(),'font/ttf' if font.suffix=='.ttf' else 'text/plain; charset=utf-8',cache=True)
                else:self.send(404,{'error':'Font unavailable'})
            elif self.path in ('/brand/steam/capsule.jpg','/brand/steam/header.jpg'):
                art=brand_resource('steam')
                art=art/self.path.rsplit('/',1)[1] if art else None
                if art and art.is_file():self.send(200,art.read_bytes(),'image/jpeg',cache=True)
                else:self.send(404,{'error':'Artwork unavailable'})
            elif self.path=='/report' and self.authenticated():
                self.send(200,app.report())
            elif self.path.startswith('/received-report?') and self.authenticated() and app.received_reports:
                name=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('name',[''])[0]
                try:self.send(200,app.received_reports.read(name))
                except ValueError as error:self.send(404,{'error':str(error)})
            elif self.path=='/status' and self.authenticated():
                self.send(200,app.status())
            elif self.path=='/fovea' and self.authenticated():
                # RC4.3 eye-tracking panel, polled several times a second while it is shown.
                self.send(200,app.fovea_live())
            else:self.send(403,{'error':'Forbidden'})
        def do_POST(self):
            if self.path!='/action' or not self.authenticated():self.send(403,{'error':'Forbidden'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<1 or length>4096:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Expected an action object')
                if body.get('action') in ('quit','quit-if-idle'):
                    if body['action']=='quit-if-idle':
                        if not app.action_lock.acquire(blocking=False):raise ValueError('The client became busy. Stop it before opening Game Mode.')
                        try:
                            if app.running():raise ValueError('The client session became active. Stop it before opening Game Mode.')
                            app.closing.set()
                        finally:app.action_lock.release()
                    app.shutdown();self.send(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:app.action(body);self.send(200,{'ok':True})
            except (ValueError,KeyError,OSError,subprocess.SubprocessError) as error:
                app.log('Action failed: '+str(error))
                self.send(400,{'error':str(error)})
    return page_server(app.role,Handler)

def existing_play(instance):
    """A Steam shortcut can control an already-open companion without a browser."""
    parsed=urllib.parse.urlsplit(instance.existing_url())
    base=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,'','',''))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    stopped=threading.Event();failure=[];started=threading.Event()
    status_request=urllib.request.Request(base+'/status',headers={'X-VROSTBridge-Token':parsed.fragment})
    with opener.open(status_request,timeout=3) as response:initial=json.load(response)
    if initial.get('busy'):raise ValueError('The client is already starting or stopping. Wait for it to finish, then open VROSTBridge Play again.')
    if initial.get('running'):started.set()
    def request(action):
        data=json.dumps({'action':action}).encode()
        req=urllib.request.Request(base+'/action',data=data,headers={'Content-Type':'application/json','X-VROSTBridge-Token':parsed.fragment})
        with opener.open(req,timeout=100) as response:return json.load(response)
    def start():
        try:
            if not started.is_set():request('start');started.set()
        except (OSError,ValueError) as error:failure.append(str(error))
    previous={sig:signal.signal(sig,lambda *_:stopped.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    worker=threading.Thread(target=start,daemon=True);worker.start()
    try:
        while not stopped.wait(.5):
            if failure:raise ValueError(failure[0])
            if not worker.is_alive():
                req=urllib.request.Request(base+'/status',headers={'X-VROSTBridge-Token':parsed.fragment})
                with opener.open(req,timeout=3) as response:status=json.load(response)
                if not status.get('running') and not status.get('busy'):break
    finally:
        try:
            if started.is_set() or worker.is_alive():request('stop')
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
    return 0


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    if arguments[:1]==['--gamescope-wrapper']:
        from launcher.bootstrap import gamescope_wrapper
        return gamescope_wrapper(arguments[1:])
    if arguments[:1]==['--base-stations']:
        from launcher.base_stations import main as base_stations
        return base_stations(arguments[1:])
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=('host','client'),required=True)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--background',action='store_true',help='Run the companion without opening a browser.')
    parser.add_argument('--play',action='store_true',help='Client: start a session without a browser; exiting stops both runtimes.')
    parser.add_argument('--game-mode',action='store_true',help='Open native controller-friendly client controls.')
    args=parser.parse_args(arguments)
    args.game_mode=args.game_mode or args.play
    args.play=False
    if args.game_mode and args.role!='client':parser.error('--game-mode belongs to the headset client')
    if args.play and args.role!='client':parser.error('--play belongs to the headset client')
    if args.game_mode and on_windows():parser.error("--game-mode belongs to the Bazzite client (Steam's Game Mode)")
    if args.self_test:
        assert HTML.startswith('<!doctype html>')
        assert (ROOT/'host/default-session.json').is_file()
        for name in ('vrostbridge-256.png','favicon-32.png'):
            resource=logo_resource(name)
            if resource is None or resource.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':raise RuntimeError('Packaged image is missing or damaged: '+name)
        if PACKAGED:print('Verified '+str(verify_package())+' packaged files.')
        if args.role=='host' and PACKAGED:
            from host.control import installed_api_version
            print('Installed host API '+installed_api_version(ROOT/'dist/host'))
        print('VROSTBridge packaged resources OK');return 0
    instance=DesktopInstance(args.role)
    if not instance.acquire():
        if args.game_mode:
            # Restart an idle service in THIS graphical session. Steam -applaunch
            # would otherwise retain the desktop's old Wayland environment.
            parsed=urllib.parse.urlsplit(instance.existing_url());base=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,'','',''))
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));headers={'X-VROSTBridge-Token':parsed.fragment}
            with opener.open(urllib.request.Request(base+'/status',headers=headers),timeout=3) as response:state=json.load(response)
            if state.get('running') or state.get('busy'):raise ValueError('A VROSTBridge session is already active. Stop it before opening Game Mode controls.')
            headers['Content-Type']='application/json'
            with opener.open(urllib.request.Request(base+'/action',data=b'{"action":"quit-if-idle"}',headers=headers),timeout=80) as response:json.load(response)
            deadline=time.monotonic()+12
            while not instance.acquire():
                if time.monotonic()>deadline:raise ValueError('The previous companion is still closing. Try again.')
                time.sleep(.1)
        else:
            if not args.background:instance.reopen()
            return 0
    app=None;server=None;thread=None;tray=None;notices=None;stopped=threading.Event();play_finished=threading.Event();play_failed=[]
    previous={sig:signal.signal(sig,lambda *_:stopped.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        app=Application(args.role);server=server_for(app)
        instance.publish(server.server_port,app.token)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)+'/#'+app.token
        if sys.platform=='win32':
            from launcher.windows_tray import WindowsTray
            icons={'light':logo_resource('vrostbridge-tray-light.ico'),'dark':logo_resource('vrostbridge-tray-dark.ico'),
                   'app':logo_resource('vrostbridge.ico')}
            try:
                if args.role=='host':tray=WindowsTray(icons,lambda:open_desktop(url),stopped.set,app.log).start()
                else:
                    # 1.0.0.7: the Windows client's tray icon starts and stops sessions too, and says how a session
                    # it started went (a Windows notification), as no page may be open.
                    from launcher.windows_tray import ClientNotices
                    notices=ClientNotices()
                    def tray_start():
                        notices.arm();app.action({'action':'start'})
                    tray=WindowsTray(icons,lambda:open_desktop(url),stopped.set,app.log,role='client',
                                     start_callback=tray_start,stop_callback=lambda:app.action({'action':'stop'})).start()
            except (OSError,RuntimeError) as error:
                app.startup_error='Tray unavailable: '+str(error);app.log(app.startup_error)
                notify_desktop('VROSTBridge is running but its tray icon failed. Reopen VROSTBridge '+('Host' if args.role=='host' else 'Client')+' for controls.',error=True)
        if not (args.background or args.game_mode):open_desktop(url)
        def prepare():
            app.autostart()
            if args.play:
                try:
                    if not stopped.is_set():app.action({'action':'start'})
                except (OSError,ValueError,subprocess.SubprocessError) as error:
                    app.startup_error=str(error);app.log('Session start: '+str(error));play_failed.append(str(error))
                finally:play_finished.set()
        threading.Thread(target=prepare,daemon=True).start()
        print('VROSTBridge companion started. Reopen the application to view its desktop controls.')
        if args.game_mode:
            from launcher.game_ui import run as game_ui
            def native_status():
                if app.closing.is_set():stopped.set()
                return app.status()
            game_ui(ROOT,native_status,app.action,app.report,stopped,app.fovea_live)
            stopped.set()
        while thread.is_alive() and not stopped.wait(.5):
            if tray:
                status=app.status();tray.update(status)
                notice=notices.update(status) if notices else None
                if notice:tray.notify(*notice)
            if args.play and play_finished.is_set() and (play_failed or not app.running()):break
    finally:
        if tray:tray.close()
        if app:
            app.session_cancelled.set()
            if app.client_runtime:app.client_runtime.cancel()
        if server:
            if thread and thread.is_alive():server.shutdown();thread.join()
            server.server_close()
        if app:app.shutdown()
        instance.close()
        for sig,handler in previous.items():signal.signal(sig,handler)
    if play_failed:
        notify_desktop('VROSTBridge could not start the session.\n\n'+play_failed[0]+'\n\nOpen VROSTBridge Client for diagnostics.',error=True)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
