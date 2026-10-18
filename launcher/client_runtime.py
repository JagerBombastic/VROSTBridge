"""Session-owned local SteamVR startup, settings and bounded shutdown.

Linux (Bazzite): the registered SteamVR's bin/linux64 programs are identified through /proc (exact
executable, owner, start ticks) and signalled through pidfd. Windows 10/11: the same programs in
bin\\win64 are identified by exact image path and creation time while they run, SteamVR is started
with its own vrstartup.exe as the host does, asked to quit with vrmonitor.exe vrmonitor://quit, and a
process that lingers is ended by host/processes.terminate only while its identity still matches (a
reused PID is never touched). The platform is decided at call time.
"""
import datetime
import json
import math
import ntpath
import os
from pathlib import Path
import re
import shutil
import tempfile
import stat
import signal
import subprocess
import sys
import threading
import time
from launcher.headset import parse_headset

RUNTIME_NAMES = ('vrserver', 'vrcompositor', 'vrmonitor', 'vrstartup')
PROBE_NAME = 'VROSTBridgeRuntimeProbe'
STEAMVR_URL = 'steam://rungameid/250820'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)


def probe_diagnostic(stdout, stderr):
    """Keep the probe result even when vrenv.sh writes its banner to stderr."""
    def decode(value):
        return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
    output = decode(stdout)
    error = decode(stderr)
    protocol = '\n'.join(line for line in output.splitlines() if line.startswith(('RUNTIME_', 'ERROR ', 'FATAL ')))
    # Wrapper PIDs change on every attempt; retaining those banners both hid
    # the useful stdout and filled the retained journal with repeated paths.
    error = '\n'.join(line for line in error.splitlines()
                      if not (line.startswith('vrenv.sh[') and
                              (']: exec ' in line or ']: VRCOMPOSITOR_LD_LIBRARY_PATH=' in line)))
    return '\n'.join(part for part in (protocol[-3072:], error.strip()[-1024:]) if part) or output.strip()[-4096:]


def desktop_environment():
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '')
    gamescope = bool(os.environ.get('GAMESCOPE_WAYLAND_DISPLAY') or
                     'gamescope' in desktop.lower() or
                     os.environ.get('XDG_SESSION_DESKTOP', '').lower() == 'gamescope')
    return {'desktop': desktop, 'session_type': os.environ.get('XDG_SESSION_TYPE', ''),
            'game_mode_detected': gamescope,
            'game_mode_vr_support': 'requires-headset-display-validation' if gamescope else 'not-tested'}


def steam_environment(extra=None):
    """Do not pass PyInstaller's private loader path to Steam/SteamVR (Linux).

    Windows has no loader path to undo: SteamVR gets this environment as it is."""
    env = os.environ.copy()
    if sys.platform != 'win32':
        if 'LD_LIBRARY_PATH_ORIG' in env:
            env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH_ORIG']
        else:
            env.pop('LD_LIBRARY_PATH', None)
    env.update(extra or {})
    return env


def openvr_registry():
    """OpenVR's registry of installed runtimes: %LOCALAPPDATA%\\openvr on Windows, $XDG_CONFIG_HOME/openvr on Linux."""
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or str(Path.home()/'AppData'/'Local'))/'openvr'/'openvrpaths.vrpath'
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'openvr/openvrpaths.vrpath'


def runtime_roots():
    registry = openvr_registry()
    try:
        paths = json.loads(registry.read_text(encoding='utf-8-sig')).get('runtime') or []
        return [Path(path).expanduser().resolve() for path in paths if isinstance(path, str) and path]
    except (OSError, ValueError, TypeError):
        return []


def log_roots():
    """SteamVR's registered log folders (the registry's "log" list), [] when none is registered."""
    try:
        paths = json.loads(openvr_registry().read_text(encoding='utf-8-sig')).get('log') or []
        return [Path(path) for path in paths if isinstance(path, str) and path][:4]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def runtime_environment_wrapper():
    """Use the active registered installation's own helper environment setup.

    Do not copy library paths from another process or manufacture SteamVR's
    internal environment variables. The installed script owns their contents.
    Windows SteamVR has no such wrapper: the native client starts directly.
    """
    if sys.platform == 'win32':
        return None
    roots = runtime_roots()
    if not roots:
        return None
    wrapper = roots[0]/'bin/vrenv.sh'
    return wrapper if wrapper.is_file() and os.access(wrapper, os.X_OK) else None


def windows_executable(path):
    """The .exe a Windows program path names (a path may be given without its suffix)."""
    path = Path(path)
    return path if path.suffix.lower() == '.exe' else path.with_name(path.name + '.exe')


def windows_startup():
    """vrstartup.exe of the first registered SteamVR runtime that has one (as the host finds it), or None."""
    return next((root/'bin'/'win64'/'vrstartup.exe' for root in runtime_roots()
                 if (root/'bin'/'win64'/'vrstartup.exe').is_file()), None)


def open_url(url):
    """Windows: hand a steam:// address to its registered handler (Steam)."""
    os.startfile(url)


def registered_url_command(scheme):
    """Windows: the command registered for scheme:// links (HKEY_CLASSES_ROOT), '' when none is readable."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme + r'\shell\open\command') as key:
            value = winreg.QueryValueEx(key, '')[0]
        return value if isinstance(value, str) else ''
    except (ImportError, OSError, ValueError):
        return ''


def windows_quit_program(server):
    """Windows: the program that takes SteamVR's vrmonitor://quit for this server (path of its vrserver.exe).

    The program SteamVR registered for vrmonitor:// links when it is in the server's own bin\\win64 folder
    (never a program elsewhere), else that folder's vrmonitor.exe. It is run directly, so Windows' shell
    never asks which app should open the link."""
    folder = Path(server).parent
    match = re.match(r'\s*(?:"([^"]+)"|(\S+))', registered_url_command('vrmonitor'))
    if match:
        try:
            program = Path(match.group(1) or match.group(2)).resolve()
            if (program.suffix.lower() == '.exe' and _path_key(program.parent) == _path_key(folder.resolve())
                    and program.is_file()):
                return program
        except (OSError, ValueError, RuntimeError):
            pass
    return folder/'vrmonitor.exe'


def runtime_name(identity):
    """The SteamVR program a process identity (or path) is, named as on Linux: 'vrserver', 'vrcompositor', ...

    Windows image names are compared without '.exe' and case-insensitively, so callers compare one set of
    names on both platforms."""
    path = identity.get('path', '') if isinstance(identity, dict) else identity
    if sys.platform == 'win32':
        name = ntpath.basename(str(path or '')).lower()
        return name[:-4] if name.endswith('.exe') else name
    return Path(str(path or '')).name


def windows_identity(pid):
    """Windows: {'pid', 'path', 'created'} of a RUNNING process, by exact image path and creation time, or None.

    The shape and values of host/processes.identify, so host/processes.terminate verifies it again; unlike
    identify, a process that has exited is not running even while another program (SteamVR's server, for its
    compositor) still holds a handle to it. One handle answers all three, so a reused PID cannot mix them."""
    import ctypes
    from ctypes import wintypes
    if type(pid) is not int or pid <= 0:
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        return None
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0x102:  # WAIT_TIMEOUT: still running
            return None
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        times = [wintypes.FILETIME() for _ in range(4)]
        if (not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)) or
                not kernel.GetProcessTimes(handle, *[ctypes.byref(item) for item in times])):
            return None
        return {'pid': pid, 'path': str(Path(name.value).resolve()),
                'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def terminate_identity(identity):
    """Windows: end one process while its identity still matches (host/processes.py).

    Raises ValueError when the PID was reused or the process exited, OSError when Windows refuses."""
    from host.processes import terminate
    terminate(identity)


def _toolhelp_processes():
    """[(pid, image name)] for every process, from one Toolhelp32 snapshot."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot.restype = wintypes.HANDLE
    for function in (kernel.Process32FirstW, kernel.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError('Windows could not list running programs (error ' + str(ctypes.get_last_error()) + ').')
    rows = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found and len(rows) < 65536:
            rows.append((int(entry.th32ProcessID), entry.szExeFile))
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return rows


def windows_processes():
    """[(pid, image name)] of running programs: a Toolhelp32 snapshot, else the host's tasklist query
    (SteamVR's family only). Raises OSError when Windows answers neither."""
    try:
        return _toolhelp_processes()
    except (OSError, AttributeError, ValueError):
        pass
    try:
        from host.control import windows_runtime_process_ids
        return [(pid, name) for name, pids in windows_runtime_process_ids().items() for pid in pids]
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise OSError('Windows could not list running programs (' + str(error)[:120] + ').') from None


def _path_key(path):
    """Windows paths compare case-insensitively, with either separator."""
    return ntpath.normcase(str(path))


def windows_runtime_processes():
    """Windows: the registered runtime's own vrserver/vrcompositor/vrmonitor/vrstartup.exe, by exact image path."""
    allowed = {_path_key((root/'bin'/'win64'/(name+'.exe')).resolve()) for root in runtime_roots() for name in RUNTIME_NAMES}
    if not allowed:
        return {}
    images = {name+'.exe' for name in RUNTIME_NAMES}
    result = {}
    for pid, image in windows_processes():
        if str(image).lower() not in images:
            continue
        identity = process_identity(pid)
        if identity and _path_key(identity['path']) in allowed:
            result[identity['pid']] = identity
    return result


def process_identity(pid, proc=Path('/proc')):
    """Read exact executable, effective owner and Linux process start ticks.

    Windows: exact image path and creation time of a running process (windows_identity), the
    identity host/processes.terminate checks again on the process handle before ending it."""
    if sys.platform == 'win32':
        return windows_identity(pid)
    try:
        directory = proc/str(pid)
        fields = (directory/'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        uid = directory.stat().st_uid
        if uid != os.getuid():
            return None
        path = os.readlink(directory/'exe')
        if path.endswith(' (deleted)'):
            return None
        return {'pid': int(pid), 'path': str(Path(path).resolve()), 'uid': uid,
                'started': int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def runtime_processes():
    # Exact registered installation paths, never a global process-name match.
    if sys.platform == 'win32':
        return windows_runtime_processes()
    roots = runtime_roots()
    allowed = {str((root/'bin/linux64'/name).resolve())
               for root in roots for name in RUNTIME_NAMES}
    if not allowed:
        return {}
    result = {}
    for directory in Path('/proc').iterdir():
        if directory.name.isdecimal():
            identity = process_identity(int(directory.name))
            if identity and identity['path'] in allowed:
                result[identity['pid']] = identity
    return result


def signal_identity(identity, sig):
    """Pin a process with pidfd before checking its identity and signalling."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise OSError('Safe process handles are unavailable; close local SteamVR manually.')
    try:
        fd = os.pidfd_open(identity['pid'])
    except ProcessLookupError:
        return False
    try:
        if process_identity(identity['pid']) != identity:
            return False
        signal.pidfd_send_signal(fd, sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)


STREAM_ONLY={('steamvr','enableHomeApp'):False,('dashboard','enableDashboard'):False,('steamvr','sendSystemButtonToAllApps'):True}
# 1.0.0.13: the headset's refresh rate SteamVR uses (Hz), set for a session for which an Index rate is chosen
# (launcher/headset.py requested_refresh) and put back with the other settings afterwards.
REFRESH=('steamvr','preferredRefreshRate')

def _refresh_value(value):
    """A refresh rate SteamVR could be asked for (a real number of Hz from 30 to 240), else None."""
    return float(value) if type(value) in (int,float) and math.isfinite(value) and 30<=value<=240 else None

def _still_applied(current,applied):
    """Whether a setting still holds the value VROSTBridge applied: the same bool, or the same number of Hz."""
    if type(applied) is bool:return current is applied
    return type(current) in (int,float) and current==applied
COMPOSITOR_LOGS=('.local/share/Steam/logs/vrcompositor.txt','.steam/steam/logs/vrcompositor.txt')
WATCHDOG_ABORT='Failed Watchdog timeout'
# 1.0.0.10: SteamVR's compositor could not take the headset display (VRInitError 496). In the 1.0.0.9
# test's Game Mode start it tried six times in 34 s and VROSTBridge kept waiting for a minute.
LEASE_FAILURES=('VRInitError_Compositor_CannotDRMLeaseDisplay','No support for wl_drm_lease')
# The compositor's lines about how it reaches the headset display, logged once each.
COMPOSITOR_NOTES=('CHmdWindowSDL: Using','WaylandHMDState: Got interface','No support for wl_drm_lease',
                  'Unable to obtain drm lease','Failed to start compositor')

def watchdog_aborted(text):
    """True when SteamVR's compositor logged a watchdog abort (it then takes ~30 s to exit by itself)."""
    return any(WATCHDOG_ABORT in line and 'Aborting' in line for line in text.splitlines())

def compositor_findings(text):
    """What a stretch of SteamVR's compositor log says (1.0.0.10): a watchdog abort, a headset display it
    could not lease, and its lines about how it reaches the headset display (without the time stamp)."""
    lines=text.splitlines()
    notes=[]
    for line in lines:
        if any(note in line for note in COMPOSITOR_NOTES):
            note=line.split('] - ',1)[-1].strip()[:200]
            if note not in notes:notes.append(note)
    return {'aborted':watchdog_aborted(text),'lease_failed':any(item in line for line in lines for item in LEASE_FAILURES),
            'notes':notes[:12]}

def runtime_launch_environment(extra=None):
    """SteamVR's environment: steam_environment() with the Game Mode display (1.0.0.10).

    With VROSTBridge's headset display (WAYLAND_DISPLAY from the lease adapter) gamescope's own display
    marker is removed, so nothing in SteamVR can take gamescope's display instead: in the 1.0.0.9 test
    SteamVR's compositor reached a display without leasing and failed with error 496."""
    env=steam_environment(extra)
    if (extra or {}).get('WAYLAND_DISPLAY'):env.pop('GAMESCOPE_WAYLAND_DISPLAY',None)
    return env

def server_display_environment(identities, proc=Path('/proc')):
    """1.0.0.10 (Game Mode): the display variables SteamVR's server was started with, from /proc.

    None while no server of this session is known; {'readable': False, ...} when its environment
    cannot be read."""
    for pid, identity in sorted(identities.items()):
        if runtime_name(identity) != 'vrserver':
            continue
        try:
            data = (proc/str(pid)/'environ').read_bytes()
        except OSError as error:
            return {'readable': False, 'error': error.strerror or type(error).__name__}
        values = dict(item.split(b'=', 1) for item in data.split(b'\0') if b'=' in item)
        return {'readable': True,
                **{key.lower(): values[key.encode()].decode('utf-8', 'replace')[:200] if key.encode() in values else None
                   for key in ('WAYLAND_DISPLAY', 'GAMESCOPE_WAYLAND_DISPLAY', 'DISPLAY', 'SDL_VIDEODRIVER')}}
    return None

def server_display_note(server, environment):
    """The log line for server_display_environment() in Game Mode (1.0.0.10)."""
    wanted=(environment or {}).get('WAYLAND_DISPLAY')
    if not server.get('readable'):
        return 'Game Mode: SteamVR\'s server environment could not be read ('+str(server.get('error'))+').'
    got=server.get('wayland_display')
    if got==wanted:
        return 'Game Mode: SteamVR\'s server was started with VROSTBridge\'s headset display (WAYLAND_DISPLAY='+str(wanted)+').'
    return ('Game Mode: SteamVR\'s server was started with '+('WAYLAND_DISPLAY='+got if got else 'no WAYLAND_DISPLAY')+
            ', not VROSTBridge\'s headset display ('+str(wanted)+'); gamescope\'s display marker '+
            ('was still set' if server.get('gamescope_wayland_display') else 'was not set')+'.')

# 1.0.0.13: names of variables that a container runtime or Steam sets for what it starts (only names are reported).
ORIGIN_PREFIXES = ('PRESSURE_VESSEL', 'SRT_', 'STEAM_COMPAT_', 'FLATPAK_')
ORIGIN_NAMES = ('container', 'SteamAppId', 'SteamGameId', 'STEAM_RUNTIME', 'STEAM_RUNTIME_SCOUT', 'SteamOverlayGameId')

def _process_stat(pid, proc):
    """(name, parent pid) of a process from /proc/<pid>/stat, else None."""
    try:
        text = (proc/str(pid)/'stat').read_text()
        name = text[text.index('(')+1:text.rindex(')')]
        return name[:32], int(text[text.rindex(')')+1:].split()[1])
    except (OSError, ValueError, IndexError):
        return None

def _socket_names(folder):
    """{(device, inode): name} of the sockets directly in a folder."""
    result = {}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return result
    for entry in entries:
        try:
            info = entry.stat()
        except OSError:
            continue
        if stat.S_ISSOCK(info.st_mode):
            result.setdefault((info.st_dev, info.st_ino), entry.name)
    return result

def server_origin(identities, wanted=None, proc=Path('/proc'), runtime_dir=None, gamescope=None):
    """1.0.0.13 (Game Mode): where SteamVR's server came from, and which display its own name for it reaches.

    In 1.0.0.12's Game Mode start the server had WAYLAND_DISPLAY=wayland-0, a name VROSTBridge never gives, and no
    gamescope marker: something between vrstartup.sh and the server renamed the display (a container runtime, such
    as Steam's, can). This notes the server's parent processes, whether it has a mount namespace of its own (a
    container), the container and Steam variables it was given (names only), and which socket its WAYLAND_DISPLAY
    is inside its own file system: VROSTBridge's headset display, gamescope's, or another. None without a server."""
    runtime_dir = runtime_dir or os.environ.get('XDG_RUNTIME_DIR') or ''
    gamescope = gamescope if gamescope is not None else os.environ.get('GAMESCOPE_WAYLAND_DISPLAY')
    for pid, identity in sorted(identities.items()):
        if runtime_name(identity) != 'vrserver':
            continue
        parents, current = [], int(pid)
        for _ in range(6):
            found = _process_stat(current, proc)
            if not found or found[1] <= 1:
                break
            current = found[1]
            parent = _process_stat(current, proc)
            if not parent:
                break
            parents.append(parent[0])
        try:
            own = os.readlink(proc/str(pid)/'ns'/'mnt') != os.readlink(proc/'self'/'ns'/'mnt')
        except OSError:
            own = None
        result = {'parents': parents, 'own_namespace': own}
        try:
            data = (proc/str(pid)/'environ').read_bytes()
        except OSError as error:
            return dict(result, readable=False, error=error.strerror or type(error).__name__)
        values = {key.decode('utf-8', 'replace'): value.decode('utf-8', 'replace')
                  for key, value in (item.split(b'=', 1) for item in data.split(b'\0') if b'=' in item)}
        result.update(readable=True, markers=sorted(name for name in values if name in ORIGIN_NAMES
                                                    or name.startswith(ORIGIN_PREFIXES))[:12])
        name = values.get('WAYLAND_DISPLAY')
        if name:
            inner = name if name.startswith('/') else (values.get('XDG_RUNTIME_DIR') or runtime_dir)+'/'+name
            try:
                info = os.stat(proc/str(pid)/'root'/inner.lstrip('/'))
                match = _socket_names(runtime_dir).get((info.st_dev, info.st_ino)) if runtime_dir else None
                kind = ('vrostbridge' if match and match == wanted else 'gamescope' if match and match == gamescope
                        else 'other' if match else 'unknown')
                result['display'] = {'name': name[:64], 'reaches': kind, 'socket': match}
            except OSError:
                result['display'] = {'name': name[:64], 'reaches': 'missing', 'socket': None}
        return result
    return None

def server_origin_note(origin):
    """The log line for server_origin() (1.0.0.13)."""
    if not origin.get('readable'):
        return 'Game Mode: where SteamVR\'s server came from could not be read ('+str(origin.get('error'))+').'
    display = origin.get('display') or {}
    reaches = {'vrostbridge': 'VROSTBridge\'s headset display', 'gamescope': 'gamescope\'s own display',
               'other': 'another display ('+str(display.get('socket'))+')', 'unknown': 'a display this PC\'s session does not have',
               'missing': 'nothing (no such socket)'}.get(display.get('reaches'), 'nothing (no WAYLAND_DISPLAY)')
    return ('Game Mode: SteamVR\'s server was started by '+(' < '.join(origin.get('parents') or []) or 'an unknown process')+
            ('; it runs in a container of its own' if origin.get('own_namespace') else '')+
            ('; Steam or container variables: '+', '.join(origin['markers']) if origin.get('markers') else '')+
            '; its display name '+str(display.get('name') or '(none)')+' reaches '+reaches+'.')

def lease_failure_message(environment, server=None):
    """Why SteamVR's compositor could not take the headset display (1.0.0.10), as precisely as known."""
    wanted=(environment or {}).get('WAYLAND_DISPLAY')
    if not wanted:
        return ('SteamVR could not take the headset display from the desktop (SteamVR error 496): the desktop did not '
                'lend it to SteamVR. On Wayland this needs a desktop that lends displays to SteamVR (DRM leasing), as KDE Plasma does.')
    message='SteamVR could not take the headset display in Game Mode (SteamVR error 496: the display it reached cannot lend it).'
    if server and server.get('readable'):
        got=server.get('wayland_display')
        if got==wanted:message+=' SteamVR was given VROSTBridge\'s headset display ('+wanted+') but did not use it.'
        else:message+=' SteamVR was started with '+('WAYLAND_DISPLAY='+got if got else 'no WAYLAND_DISPLAY')+', not VROSTBridge\'s headset display.'
    return message+' Play from Desktop Mode for now, and save a report: it keeps this Game Mode start.'

def compositor_log_paths():
    """Where SteamVR writes vrcompositor.txt: its registered log folders on Windows, Steam's logs on Linux."""
    if sys.platform == 'win32':
        return [root/'vrcompositor.txt' for root in log_roots()]
    return [Path.home()/item for item in COMPOSITOR_LOGS]

# Windows crash records (1.0.0.7): what Windows Error Reporting and the display driver logged when SteamVR's
# compositor or server, or the native client, failed on this PC. The Linux client reads coredumpctl and the
# kernel log for the same purpose (launcher/app.py log_compositor_crash_details).
CRASH_PROGRAMS = ('vrcompositor.exe', 'vrserver.exe', 'VROSTBridgeClient.exe')
_CRASH_FIELDS = {1000: ('AppName', 'AppVersion', 'AppTimeStamp', 'ModuleName', 'ModuleVersion', 'ModuleTimeStamp',
                        'ExceptionCode', 'FaultingOffset', 'ProcessId', 'ProcessCreationTime', 'AppPath', 'ModulePath'),
                 1002: ('AppName', 'AppVersion', 'ProcessID', 'StartTime', 'TerminationTime', 'AppPath')}
_EXCEPTIONS = {'c0000005': 'access violation', 'c0000409': 'fail-fast or stack buffer overrun', 'c0000374': 'heap corruption',
               'c00000fd': 'stack overflow', 'c000001d': 'illegal instruction', '80000003': 'breakpoint',
               'e06d7363': 'unhandled C++ exception', 'c0000602': 'fail-fast', 'c0000142': 'DLL initialisation failed'}

def _system_tool(name):
    """A Windows system program by full path, so a program of that name elsewhere is never run."""
    root = os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    candidate = Path(root)/'System32'/name if root else None
    return str(candidate) if candidate is not None and candidate.is_file() else name

def parse_events(text):
    """Events from `wevtutil qe ... /f:xml` (language-neutral XML, one <Event> per record):
    [{'provider', 'id', 'time' (UTC ISO), 'data': {name: value}}]; unnamed <Data> keep their position."""
    import xml.etree.ElementTree as ElementTree
    events = []
    for raw in re.findall(r'<Event[\s>].*?</Event>', (text or '').replace('\x00', ''), re.S)[:64]:
        try:
            element = ElementTree.fromstring(raw)
        except (ElementTree.ParseError, ValueError):
            continue
        event = {'provider': '', 'id': None, 'time': '', 'data': {}}
        values = []
        for node in element.iter():
            tag = node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else ''
            if tag == 'Provider':
                event['provider'] = node.get('Name', '')
            elif tag == 'EventID':
                try:
                    event['id'] = int((node.text or '').strip())
                except ValueError:
                    pass
            elif tag == 'TimeCreated':
                event['time'] = node.get('SystemTime', '')
            elif tag == 'Data':
                values.append((node.get('Name'), (node.text or '').strip()))
        positional = _CRASH_FIELDS.get(event['id'], ())
        for index, (name, value) in enumerate(values):
            key = name or (positional[index] if index < len(positional) else str(index))
            event['data'][key] = value
        events.append(event)
    return events

def _event_time(stamp):
    match = re.match(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})', stamp or '')
    if not match:
        return '?'
    try:
        moment = datetime.datetime.strptime(match.group(1)+' '+match.group(2), '%Y-%m-%d %H:%M:%S')
        return moment.replace(tzinfo=datetime.timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError, OSError):
        return match.group(1)+' '+match.group(2)+' UTC'

def describe_event(event, programs=CRASH_PROGRAMS):
    """One short line for a crash, hang or display-driver reset of interest, or ''."""
    data, when = event.get('data') or {}, _event_time(event.get('time'))
    if event.get('provider') == 'Display' and event.get('id') == 4101:
        driver = data.get('0') or next(iter(data.values()), '') or 'display driver'
        return when+' display driver '+driver[:40]+' stopped responding and was recovered by Windows (TDR).'
    if event.get('id') not in (1000, 1002) or event.get('provider') not in ('Application Error', 'Application Hang'):
        return ''
    app = data.get('AppName') or ntpath.basename(data.get('AppPath', ''))
    if app.lower() not in {name.lower() for name in programs}:
        return ''
    version = (' '+data['AppVersion'][:32]) if data.get('AppVersion') else ''
    if event['id'] == 1002:
        return when+' '+app+version+' stopped responding (Windows hang report).'
    code = data.get('ExceptionCode', '').lower()
    code = code[2:] if code.startswith('0x') else code
    meaning = _EXCEPTIONS.get(code, '')
    module = data.get('ModuleName', '')
    return (when+' '+app+version+' crashed: exception 0x'+(code or '?')[:16]+(' ('+meaning+')' if meaning else '')+
            (' in '+module[:80]+((' '+data['ModuleVersion'][:32]) if data.get('ModuleVersion') else '') if module else '')+
            (' at offset 0x'+data['FaultingOffset'].lower().removeprefix('0x')[:20] if data.get('FaultingOffset') else '')+'.')

def crash_details(runner=None, programs=CRASH_PROGRAMS, minutes=10, limit=8):
    """Windows: what this PC recorded about a SteamVR or native client failure in the last minutes, oldest first:
    Application Error (crash) and Application Hang reports for these programs, and display driver resets
    (TDR, System log "Display" 4101) of the last 5 minutes. Short lines; [] when none or unreadable.

    Read with wevtutil (no window, bounded); events are read as XML, so this works in every Windows language."""
    if sys.platform != 'win32':
        return []
    if runner is None:
        from host.power import windows_run as runner
    tool = _system_tool('wevtutil.exe')
    window = max(1, int(minutes))*60000
    queries = (('Application', "*[System[Provider[@Name='Application Error' or @Name='Application Hang'] and "
                               'TimeCreated[timediff(@SystemTime) <= '+str(window)+']]]', 24),
               ('System', "*[System[Provider[@Name='Display'] and EventID=4101 and "
                          'TimeCreated[timediff(@SystemTime) <= '+str(min(window, 300000))+']]]', 6))
    found = []
    for log, query, count in queries:
        try:
            result = runner([tool, 'qe', log, '/q:'+query, '/c:'+str(count), '/rd:true', '/f:xml'], 8)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if getattr(result, 'returncode', 1) != 0:
            continue
        for event in parse_events(getattr(result, 'stdout', '') or ''):
            line = describe_event(event, programs)
            if line and (event.get('time', ''), line) not in found:
                found.append((event.get('time', ''), line))
    return [line[:300] for _, line in sorted(found)][-max(1, int(limit)):]

def settings_path():
    registry=openvr_registry()
    try:data=json.loads(registry.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        # 1.0.0.7: said plainly (it was the bare file error): SteamVR writes this list when it first runs.
        raise ValueError('SteamVR is not set up on this PC yet: install SteamVR in Steam and start it once with the '
                         'headset connected, then start the session again.') from None
    except (OSError,ValueError) as error:
        raise ValueError("SteamVR's list of installations ("+registry.name+') could not be read: '+str(error)[:160]+
                         '. Start SteamVR once, then start the session again.') from None
    if not isinstance(data,dict):raise ValueError('SteamVR configuration is not registered. Open SteamVR once before using VROSTBridge.')
    paths=data.get('config') or []
    if not paths or not isinstance(paths[0],str) or not paths[0]:raise ValueError('SteamVR configuration is not registered. Open SteamVR once before using VROSTBridge.')
    return Path(paths[0]).expanduser().resolve()/'steamvr.vrsettings'

def read_settings(path):
    data=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(data,dict):raise ValueError('Invalid SteamVR settings object.')
    for section,_ in STREAM_ONLY:
        if section in data and not isinstance(data[section],dict):raise ValueError('Invalid SteamVR settings section.')
    return data

def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.vrostbridge-',dir=path.parent)
    try:
        # Windows keeps the folder's permissions (os.fchmod would only toggle read-only there).
        if sys.platform!='win32':os.fchmod(fd,stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(data,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
        _replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

def _replace(source,target):
    """os.replace; on Windows retried briefly while another program (a virus scanner, a backup) holds the target."""
    for attempt in range(6 if sys.platform=='win32' else 1):
        try:
            os.replace(source,target);return
        except PermissionError:
            if attempt>=5 or sys.platform!='win32':raise
            time.sleep(.1*(attempt+1))

class ClientRuntime:
    def __init__(self, executable, state, log, compositor_logs=None):
        self.executable = Path(executable)
        # The default log place can change once SteamVR has run (Windows reads it from OpenVR's registry).
        self.default_compositor_logs = compositor_logs is None
        self.compositor_logs = ([Path(item) for item in compositor_logs] if compositor_logs is not None
                                else compositor_log_paths())
        self.compositor_mark = (None, 0, None)
        self.compositor_recoveries = 0
        self.receipt = Path(state)/'client-runtime-settings.txt'
        self.log = log
        self.cancelled = threading.Event()
        self.state = 'idle'
        self.message = 'SteamVR will start with the client.'
        self.identities = {}
        self.session_active = False
        self.lifecycle_lock = threading.RLock()
        self.runtime_environment = {}
        self.headset = None
        self.requested_refresh = None
        self.environment_setup = ''
        self.last_probe_diagnostic = ''
        self.probe_attempts = 0
        self.probe_timeouts = 0
        self.probe_executable = ''
        self.server_display = None  # 1.0.0.10 (Game Mode): the display SteamVR's server was given
        self.server_origin = None  # 1.0.0.13 (Game Mode): who started that server and which display it reaches

    def status(self):
        return {'state': self.state, 'message': self.message,
                'settings_pending_restore': self.receipt.exists(),
                'managed_processes': len(self.identities), 'stream_only': True,
                'requested_display_environment':dict(self.runtime_environment), 'environment': desktop_environment(),
                'native_environment_setup':self.environment_setup,
                'last_probe_diagnostic':self.last_probe_diagnostic,
                'probe_attempts':self.probe_attempts, 'probe_timeouts':self.probe_timeouts,
                'probe_executable':self.probe_executable,
                'steamvr_display':self.server_display,
                'steamvr_origin':self.server_origin,
                'requested_refresh_hz':self.requested_refresh,
                'headset':self.headset}

    def cancel(self):
        self.cancelled.set()

    def helper(self, restore=False, quiet=True, timeout=8):
        if restore:raise ValueError('Settings can only be restored offline.')
        env=runtime_launch_environment(self.runtime_environment)
        if sys.platform=='win32':
            # VROSTBridgeRuntimeProbe.exe beside VROSTBridgeClient.exe: same arguments and RUNTIME_* output as on Linux.
            client=windows_executable(self.executable)
            probe=client.with_name(PROBE_NAME+'.exe')
            executable=probe if probe.is_file() else client
            self.probe_executable=str(executable.resolve())
            return subprocess.run(self.native_command(['--verify-runtime'], executable=executable),
                                  env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
                                  timeout=timeout,creationflags=CREATE_NO_WINDOW)
        probe=self.executable.with_name('VROSTBridgeRuntimeProbe')
        executable=probe if probe.is_file() else self.executable
        self.probe_executable=str(executable.resolve())
        return subprocess.run(self.native_command(['--verify-runtime'], executable=executable),
                              env=env,capture_output=True,text=True,timeout=timeout)

    def native_command(self, arguments, executable=None):
        if sys.platform == 'win32':
            # Windows SteamVR needs no environment wrapper: the program itself, with its arguments.
            command = [str(windows_executable(executable or self.executable).resolve()), *arguments]
            if self.environment_setup != 'direct':
                self.environment_setup = 'direct'
                self.log('Native client starts directly; SteamVR on Windows needs no environment wrapper.')
            return command
        command = [str((executable or self.executable).resolve()), *arguments]
        wrapper = runtime_environment_wrapper()
        mode = str(wrapper) if wrapper else 'direct'
        if self.environment_setup != mode:
            self.environment_setup = mode
            self.log('Native client uses SteamVR environment setup: '+mode if wrapper else
                     'SteamVR environment wrapper is unavailable. Using a clean native environment; helper startup diagnostics will be retained.')
        # vrenv.sh execs its arguments, preserving stdin and process ownership.
        return [str(wrapper), *command] if wrapper else command

    def prepare_settings(self,refresh=None):
        if runtime_processes():raise ValueError('Local SteamVR must stop before stream-only preparation.')
        if self.receipt.exists() and not self.restore():raise ValueError('Previous runtime settings could not be recovered; receipt retained.')
        path=settings_path();data=read_settings(path);previous=[]
        wanted=dict(STREAM_ONLY)
        if refresh is not None:
            if _refresh_value(refresh) is None:raise ValueError('Invalid headset refresh rate.')
            wanted[REFRESH]=_refresh_value(refresh)
        for (section,key),value in wanted.items():
            values=data.setdefault(section,{})
            previous.append(dict(section=section,key=key,present=key in values,original=values.get(key),applied=value))
            values[key]=value
        # Receipt version 4 (1.0.0.13) may carry the refresh rate; version 3 had the three stream-only switches.
        atomic_json(self.receipt,dict(version=4,path=str(path),preferences=previous))
        if runtime_processes():raise ValueError('SteamVR started during preparation. Close it and retry.')
        atomic_json(path,data)
        self.log('Stream-only settings prepared before SteamVR startup. Local Home and dashboard are disabled.'+
                 (' Headset refresh rate for this session: '+('%g'%wanted[REFRESH])+' Hz.' if REFRESH in wanted else ''))

    def processes(self):
        """SteamVR's programs running on this PC now, {pid: identity}; empty when SteamVR is closed.

        Any process of the registered runtime (server, compositor, monitor, startup), not only this
        session's: actions that need SteamVR closed refuse while this is not empty."""
        return runtime_processes()

    def capture(self):
        if self.session_active:
            try:
                self.identities.update(runtime_processes())
            except OSError as error:
                if sys.platform!='win32':raise
                # Windows could not list programs this once: keep what was captured and look again next time.
                message='Local SteamVR process check skipped: '+str(error)[:160]
                if message!=getattr(self,'capture_problem',''):self.capture_problem=message;self.log(message)

    def mark_compositor_log(self):
        """Remember where SteamVR's compositor log ends, so only this start's lines are read."""
        if self.default_compositor_logs and sys.platform=='win32':self.compositor_logs=compositor_log_paths()
        path=next((item for item in self.compositor_logs if item.is_file()),None)
        try:
            info=path.stat() if path else None
            self.compositor_mark=(path,info.st_size,info.st_ino) if info else (None,0,None)
        except OSError:self.compositor_mark=(None,0,None)

    def compositor_text(self):
        """The compositor log written since the mark ('' when nothing new); the mark moves past it."""
        path,offset,inode=self.compositor_mark
        if path is None:
            # The log appeared after the mark: everything in it is from this start.
            path=next((item for item in self.compositor_logs if item.is_file()),None)
            if path is None:return ''
            offset,inode=0,None
        try:
            info=path.stat()
            if (inode is not None and info.st_ino!=inode) or info.st_size<offset:offset=0  # a new log file
            size=info.st_size
            if size==offset:
                self.compositor_mark=(path,size,info.st_ino)
                return ''
            start=max(offset,size-(256<<10))
            with path.open('rb') as handle:
                handle.seek(start)
                text=handle.read(size-start).decode('utf-8','replace')
            self.compositor_mark=(path,size,info.st_ino)
        except OSError:
            return ''
        return text

    def compositor_aborted(self):
        """True when the compositor logged a watchdog abort since the mark (each abort is reported once)."""
        return watchdog_aborted(self.compositor_text())

    def compositor_check(self):
        """compositor_findings() of the log written since the mark (1.0.0.10)."""
        return compositor_findings(self.compositor_text())

    def recover_compositor(self):
        """Stop this session's aborting compositor now; SteamVR's server starts a new one at once.

        After a watchdog abort the old compositor writes its crash report for about
        30 seconds before it exits, and the headset stays dark meanwhile.
        """
        stopped=0
        for identity in list(self.remaining().values()):
            if runtime_name(identity)=='vrcompositor':
                if sys.platform=='win32':
                    try:terminate_identity(identity);stopped+=1
                    except ValueError:pass  # it exited (or its PID was reused): nothing was stopped
                    except OSError as error:self.log('Compositor recovery: '+str(error)[:160])
                else:
                    try:stopped+=bool(signal_identity(identity,signal.SIGKILL))
                    except OSError as error:self.log('Compositor recovery: '+str(error)[:160])
                self.identities.pop(identity['pid'],None)
        self.compositor_recoveries+=1
        self.log('Local SteamVR compositor stopped responding (watchdog abort); '+
                 ('restarting it now instead of waiting for its crash report.' if stopped else 'it already exited; SteamVR restarts it.'))
        return stopped

    def ensure(self, lightweight=True, extra_env=None, refresh=None):
        """Start SteamVR on this PC as a stream-only runtime and verify the headset display. refresh (1.0.0.13): the
        refresh rate (Hz) to ask SteamVR for this session, or None to leave it as it is set."""
        with self.lifecycle_lock:
            self.cancelled.clear();self.state='starting';self.message='Preparing stream-only headset runtime.'
            self.requested_refresh=_refresh_value(refresh) if refresh is not None else None
            self.headset=None;self.last_probe_diagnostic='';self.environment_setup=''
            self.probe_attempts=0;self.probe_timeouts=0;self.probe_executable=''
            self.identities=runtime_processes();self.session_active=True
            if self.identities:
                self.log('Restarting existing local SteamVR as a stream-only session.')
                self._stop_runtime()
                if self.cancelled.is_set():raise ValueError('Session startup cancelled.')
                self.session_active=True
            if extra_env and any(k not in ('WAYLAND_DISPLAY','DISPLAY','SDL_VIDEODRIVER','QT_QPA_PLATFORM') or not isinstance(v,str) or not v or '\0' in v for k,v in extra_env.items()):raise ValueError('Invalid runtime display environment.')
            self.runtime_environment=dict(extra_env or {})
            if self.requested_refresh:self.prepare_settings(self.requested_refresh)
            else:self.prepare_settings()
            launched=False;deadline=time.monotonic()+60
            self.mark_compositor_log();self.compositor_recoveries=0
            self.server_display=None;self.server_origin=None;notes=set()
            while time.monotonic()<deadline:
                self.capture()
                if self.cancelled.is_set():raise ValueError('Session startup cancelled.')
                if launched:
                    if self.runtime_environment.get('WAYLAND_DISPLAY') and self.server_display is None:
                        # 1.0.0.10 (Game Mode): which display SteamVR's server was really given.
                        self.server_display=server_display_environment(self.identities)
                        if self.server_display is not None:
                            self.log(server_display_note(self.server_display,self.runtime_environment))
                            self.server_origin=server_origin(self.identities,self.runtime_environment.get('WAYLAND_DISPLAY'))
                            if self.server_origin is not None:self.log(server_origin_note(self.server_origin))
                    found=self.compositor_check()
                    for note in found['notes']:
                        if note not in notes:notes.add(note);self.log('SteamVR compositor: '+note)
                    if found['lease_failed']:
                        # 1.0.0.10: retrying cannot help; say why at once instead of after a minute.
                        self.state='error';self.message=lease_failure_message(self.runtime_environment,self.server_display)
                        raise ValueError(self.message)
                    if self.compositor_recoveries<2 and found['aborted']:
                        self.recover_compositor()
                        # The new compositor needs a moment; keep the original window plus that time.
                        deadline=max(deadline,time.monotonic()+20)
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                self.probe_attempts+=1
                try:
                    result=self.helper(timeout=min(8,remaining))
                except subprocess.TimeoutExpired as error:
                    # subprocess.run has killed and reaped this probe. A slow
                    # SteamVR IPC call is retryable within the original deadline.
                    self.probe_timeouts+=1
                    output=error.stdout or ''
                    if isinstance(output,bytes):output=output.decode('utf-8',errors='replace')
                    result=subprocess.CompletedProcess([],2,
                        output+'\nRUNTIME_WAIT probe_timeout', error.stderr)
                    self.log('Runtime probe timed out; retrying within the 60-second startup window.')
                finally:
                    self.capture()
                if self.cancelled.is_set():raise ValueError('Session startup cancelled.')
                diagnostic=probe_diagnostic(result.stdout,result.stderr)
                if diagnostic and diagnostic != self.last_probe_diagnostic:
                    self.last_probe_diagnostic=diagnostic
                    self.log('Runtime probe diagnostics: '+diagnostic)
                if result.returncode==0:
                    if not any(line.startswith('RUNTIME_READY display_progress ') for line in result.stdout.splitlines()):
                        raise ValueError('The native client did not verify the running headset display. Repair or update the client installation.')
                    self.headset=parse_headset(result.stdout)
                    self.state='ready';self.message='Headset display ready. Local Home and dashboard remain disabled.'
                    self.log(result.stdout.strip());return
                if result.returncode!=2:raise ValueError(diagnostic or 'Runtime verification failed.')
                if time.monotonic()>=deadline:break
                if not launched:
                    if sys.platform=='win32':
                        self.launch_windows_runtime();launched=True
                    else:
                        if self.runtime_environment:
                            wrapper=next((r/'bin/vrstartup.sh' for r in runtime_roots() if (r/'bin/vrstartup.sh').is_file()),None)
                            if not wrapper:raise ValueError('SteamVR startup script is missing. Verify SteamVR in Steam.')
                            command=[str(wrapper)]
                        else:
                            steam=shutil.which('steam');xdg=shutil.which('xdg-open')
                            if not steam and not xdg:raise ValueError('Steam is not installed.')
                            command=[steam,'-applaunch','250820'] if steam else [xdg,'steam://rungameid/250820']
                        child=subprocess.Popen(command,env=runtime_launch_environment(self.runtime_environment),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        threading.Thread(target=child.wait,daemon=True).start();launched=True
                    self.log('Starting local SteamVR for the bridge.')
                self.cancelled.wait(min(1,max(0,deadline-time.monotonic())))
            self.state='error';self.message='VROSTBridge could not verify headset display timing within 60 seconds. This does not necessarily mean SteamVR cannot see the headset. Save a report from this client; it includes the startup probe results.'
            if self.requested_refresh:
                self.message+=(' VROSTBridge asked SteamVR for '+('%g'%self.requested_refresh)+' Hz: if this headset or '
                               'graphics card cannot run that rate, choose another mode or Auto.')
            raise ValueError(self.message)

    def restore(self):
        if not self.receipt.exists():return True
        try:
            if runtime_processes():raise ValueError('SteamVR is still running; saved settings remain protected.')
            path=settings_path();text=self.receipt.read_text(encoding='utf-8')
            if text.lstrip().startswith('{'):
                saved=json.loads(text)
                if saved.get('version') not in (3,4) or saved.get('path')!=str(path):raise ValueError('Changed SteamVR configuration path or invalid receipt.')
                previous=saved.get('preferences');seen=set()
                if not isinstance(previous,list) or len(previous) not in ((3,) if saved['version']==3 else (3,4)):raise ValueError('Invalid settings receipt.')
                for entry in previous:
                    if not isinstance(entry,dict):raise ValueError('Invalid receipt values.')
                    pair=(entry.get('section'),entry.get('key'))
                    if pair in seen or type(entry.get('present')) is not bool:raise ValueError('Invalid receipt values.')
                    if pair in STREAM_ONLY:
                        if type(entry.get('applied')) is not bool or entry['applied']!=STREAM_ONLY[pair]:raise ValueError('Invalid receipt values.')
                    elif pair!=REFRESH or saved['version']!=4 or _refresh_value(entry.get('applied'))!=entry.get('applied'):
                        raise ValueError('Invalid receipt values.')
                    seen.add(pair)
                if not set(STREAM_ONLY)<=seen:raise ValueError('Invalid settings receipt.')
            else:
                parts=text.split();previous=[]
                if not parts or parts[0] not in ('VROSTBridgeRuntime1','VROSTBridgeRuntime2'):raise ValueError('Invalid legacy receipt.')
                legacy=parts[0]=='VROSTBridgeRuntime1'
                if len(parts)!=(4 if legacy else 5) or any(x not in ('0','1') for x in parts[1:]):raise ValueError('Invalid legacy settings.')
                home,dash=parts[1:3];system,quiet=('0',parts[3]) if legacy else parts[3:5]
                for pair,old,changed in [(('steamvr','enableHomeApp'),home,legacy or quiet=='1'),(('dashboard','enableDashboard'),dash,quiet=='1'),(('steamvr','sendSystemButtonToAllApps'),system,not legacy)]:
                    if changed:previous.append(dict(section=pair[0],key=pair[1],present=True,original=old=='1',applied=STREAM_ONLY[pair]))
            data=read_settings(path)
            for entry in previous:
                values=data.get(entry['section'],{});key=entry['key']
                # Only a value still as VROSTBridge set it goes back: one changed meanwhile is the user's.
                if key in values and _still_applied(values[key],entry['applied']):
                    if entry['present']:values[key]=entry['original']
                    else:values.pop(key,None)
            if runtime_processes():raise ValueError('SteamVR restarted during restoration.')
            atomic_json(path,data);self.receipt.unlink();self.log('Local SteamVR settings restored offline.');return True
        except (OSError,ValueError,TypeError,AttributeError) as e:
            self.log('Settings restoration deferred; receipt retained: '+str(e));return False

    def remaining(self):
        return {pid: identity for pid, identity in self.identities.items()
                if process_identity(pid) == identity}

    def wait_stopped(self, seconds):
        deadline = time.monotonic()+seconds
        while self.remaining() and time.monotonic() < deadline:
            time.sleep(.1)
        return not self.remaining()

    def launch_windows_runtime(self):
        """Windows: start the registered SteamVR with its own vrstartup.exe, as the host does (host/control.py
        launch_steamvr); through Steam (steam://rungameid/250820) only when that program is missing."""
        startup=windows_startup()
        if startup is not None:
            child=subprocess.Popen([str(startup)],cwd=str(startup.parent),env=steam_environment(self.runtime_environment),
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
            # vrstartup is a short-lived launcher; its lifetime is not the session's.
            threading.Thread(target=child.wait,daemon=True).start()
            return [str(startup)]
        try:
            open_url(STEAMVR_URL)
        except OSError as error:
            raise ValueError('SteamVR could not be started: its vrstartup.exe is missing and Steam did not open it ('+
                             str(error)[:120]+'). Install SteamVR in Steam and start it once.') from None
        return [STEAMVR_URL]

    def request_quit(self):
        # This is the URI used by SteamVR's own Exit VR action. Use its
        # registered runtime wrapper directly, avoiding a desktop/browser URI
        # handler fallback. Only send while our captured server still exists.
        # Windows: the same URI to the program in the server's own bin\win64 folder (windows_quit_program).
        windows = sys.platform == 'win32'
        for identity in self.remaining().values():
            path = Path(identity['path'])
            if runtime_name(identity) == 'vrserver':
                wrapper = windows_quit_program(path) if windows else path.parent.parent/'vrmonitor.sh'
                if wrapper.is_file():
                    try:
                        options = {'cwd': str(wrapper.parent), 'creationflags': CREATE_NO_WINDOW} if windows else {}
                        child = subprocess.Popen([str(wrapper), 'vrmonitor://quit'], env=steam_environment(self.runtime_environment),
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
                        threading.Thread(target=child.wait, daemon=True).start()
                        self.log('Requesting local SteamVR shutdown.')
                        return True
                    except OSError as error:
                        self.log('SteamVR quit request could not start: '+str(error))
        return False

    def terminate_remaining(self):
        """Windows: end this session's processes that are still the exact ones captured, the server first
        (so it does not restart the others). Returns how many were ended."""
        ended = 0
        for identity in sorted(self.remaining().values(), key=lambda item: runtime_name(item) != 'vrserver'):
            try:
                terminate_identity(identity)
                ended += 1
            except ValueError:
                pass  # it exited meanwhile, or its PID now belongs to another program: nothing was stopped
            except OSError as error:
                self.log('Local SteamVR process '+str(identity.get('pid'))+' could not be closed: '+str(error)[:160])
        return ended

    def stop(self):
        self.cancel()
        with self.lifecycle_lock:
            self._stop_runtime()

    def _stop_runtime(self):
            if not self.session_active:
                self.restore();return
            self.state = 'stopping'
            self.message = 'Closing local SteamVR.'
            # Do not adopt new/restarted PIDs at Stop. Only processes captured
            # during this session's startup may receive shutdown signals.
            requested = self.request_quit() if self.remaining() else False
            stopped = self.wait_stopped(8 if requested else 0)
            windows = sys.platform == 'win32'
            if not stopped:
                if windows:
                    # Windows has no SIGTERM for these programs: SteamVR's own quit gets 5 seconds more.
                    stopped = self.wait_stopped(5 if requested else 0)
                else:
                    for identity in self.remaining().values():
                        signal_identity(identity, signal.SIGTERM)
                    stopped = self.wait_stopped(5)
            if not stopped:
                self.log('Local SteamVR did not stop; closing only verified session processes.')
                if windows:
                    self.terminate_remaining()
                else:
                    for identity in self.remaining().values():
                        signal_identity(identity, signal.SIGKILL)
                stopped = self.wait_stopped(3)
            if not stopped:
                self.state = 'error'
                self.message = 'Local SteamVR is still stopping. Retry Stop in a few seconds.'
                raise ValueError(self.message)
            self.identities = {}
            self.session_active = False
            restored = self.restore()
            self.state = 'idle'
            self.message = ('Local SteamVR stopped.' if restored else
                            'Local SteamVR stopped; preferences will be restored on the next start.')
            self.log(self.message)
