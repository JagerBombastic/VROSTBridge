"""Check (and on request repair) what SteamVR needs for asynchronous reprojection on Linux.

SteamVR's Linux setup gives <SteamVR>/bin/linux64/vrcompositor-launcher the
CAP_SYS_NICE file capability, after a password prompt, so its compositor can
get a high-priority GPU queue. Without it there is no asynchronous reprojection;
on a client that also decodes video on the same GPU that shows up as missed
frames, judder or SteamVR dropping to half rate. The prompt can be dismissed or
fail, and a SteamVR update replaces the file and drops the capability. The check
reads the extended attribute directly (no external tool). Repair runs setcap only
through pkexec, only on that exact file, and applies the next time SteamVR starts.

With the permission in place, SteamVR can still turn asynchronous reprojection
off: its "steamvr.enableLinuxVulkanAsync" setting, or a direct-mode display
driver. Its compositor then logs "Async support disabled by user setting or a
direct mode driver" at every start. The check reads both (RC4.1). Repair turns
the setting on while SteamVR is closed (SteamVR rewrites its settings when it
exits); if SteamVR still reports async off after that, the headset's display
driver disables it and nothing on this PC can change it.

Windows (1.0.0.7): SteamVR's compositor gets its high-priority GPU queue without
any file permission, so there is nothing to check or repair. check() reports the
state 'not_needed' and repair() changes nothing.
"""
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run

CAP_SYS_NICE = 23
VFS_CAP_REVISION_MASK = 0xFF000000
VFS_CAP_FLAGS_EFFECTIVE = 0x000001
VFS_CAP_REVISION_1 = 0x01000000
VFS_CAP_REVISION_2 = 0x02000000
VFS_CAP_REVISION_3 = 0x03000000
# revision -> (exact xattr size, number of 32-bit permitted/inheritable pairs)
_LAYOUTS = {VFS_CAP_REVISION_1: (12, 1), VFS_CAP_REVISION_2: (20, 2), VFS_CAP_REVISION_3: (24, 2)}
LAUNCHER = 'vrcompositor-launcher'
SETUP_FAILURE = 'setcap of vrcompositor-launcher failed'
ASYNC_KEY = 'enableLinuxVulkanAsync'
ASYNC_DISABLED = 'Async support disabled by user setting or a direct mode driver'
COMPOSITOR_LOGS = ('.local/share/Steam/logs/vrcompositor.txt', '.steam/steam/logs/vrcompositor.txt')
_JUDDER = 'so head movement may judder when frames are late.'
NOT_NEEDED = 'not_needed'
_WINDOWS_REASON = 'SteamVR can use background (asynchronous) reprojection without an extra permission.'
WINDOWS_MESSAGE = 'Not needed on Windows: ' + _WINDOWS_REASON


def parse_vfs_cap(raw):
    """Decode struct vfs_cap_data (little endian) from 'security.capability'.

    Raises ValueError for data the kernel would also reject (unknown revision or
    a size that does not match it).
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        raise ValueError('The capability data is too short.')
    magic = struct.unpack_from('<I', raw, 0)[0]
    revision = magic & VFS_CAP_REVISION_MASK
    if revision not in _LAYOUTS:
        raise ValueError('Unknown capability format 0x%08x.' % magic)
    size, pairs = _LAYOUTS[revision]
    if len(raw) != size:
        raise ValueError('Capability data has %d bytes; %d expected.' % (len(raw), size))
    permitted = inheritable = 0
    for index in range(pairs):
        low_permitted, low_inheritable = struct.unpack_from('<II', raw, 4 + 8 * index)
        permitted |= low_permitted << (32 * index)
        inheritable |= low_inheritable << (32 * index)
    return {'revision': revision >> 24, 'effective': bool(magic & VFS_CAP_FLAGS_EFFECTIVE),
            'permitted': permitted, 'inheritable': inheritable,
            'rootid': struct.unpack_from('<I', raw, 20)[0] if revision == VFS_CAP_REVISION_3 else None,
            'sys_nice': bool(permitted >> CAP_SYS_NICE & 1)}


def has_cap_sys_nice(path):
    """{'present', 'effective', 'error', 'supported'} for CAP_SYS_NICE on one file.

    'present' requires CAP_SYS_NICE in the permitted set of an entry that applies
    to programs started normally: a version 3 entry with a non-zero root id was
    written inside another user namespace (a container) and the kernel ignores it
    here. 'effective' is the VFS_CAP_FLAGS_EFFECTIVE flag, which raises the
    capability at start without the program asking for it.
    """
    result = {'present': False, 'effective': False, 'error': '', 'supported': True}
    getxattr = getattr(os, 'getxattr', None)
    if getxattr is None:
        return dict(result, error='file capabilities cannot be read on this system')
    try:
        raw = getxattr(os.fspath(path), 'security.capability')
    except OSError as error:
        if error.errno == errno.ENODATA:
            return result
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return dict(result, supported=False)
        return dict(result, error=(error.strerror or str(error))[:160])
    except (TypeError, ValueError) as error:
        return dict(result, error=str(error)[:160])
    try:
        caps = parse_vfs_cap(raw)
    except ValueError as error:
        return dict(result, error=str(error))
    applies = caps['rootid'] in (None, 0)
    return dict(result, present=caps['sys_nice'] and applies, effective=caps['effective'])


def _registry_path():
    configured = os.environ.get('XDG_CONFIG_HOME', '')
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.config'
    return base / 'openvr' / 'openvrpaths.vrpath'


def steamvr_runtime_dirs(registry=None):
    """Existing SteamVR runtime directories from the OpenVR registry, in order (at most 4)."""
    try:
        with open(registry or _registry_path(), 'rb') as handle:
            data = json.loads(handle.read(1 << 20).decode('utf-8-sig', 'replace'))
    except (OSError, ValueError):
        return []
    runtimes = data.get('runtime') if isinstance(data, dict) else None
    if isinstance(runtimes, str):
        runtimes = [runtimes]
    result = []
    for entry in runtimes if isinstance(runtimes, list) else ():
        if not isinstance(entry, str) or not entry or '\x00' in entry:
            continue
        try:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                continue
            path = path.resolve()
            if path.is_dir() and str(path) not in result:
                result.append(str(path))
        except (OSError, RuntimeError, ValueError):
            continue
        if len(result) == 4:
            break
    return result


def steamvr_settings_path(registry=None):
    """steamvr.vrsettings in SteamVR's registered config folder, or None."""
    try:
        with open(registry or _registry_path(), 'rb') as handle:
            data = json.loads(handle.read(1 << 20).decode('utf-8-sig', 'replace'))
        config = data.get('config') if isinstance(data, dict) else None
        first = config[0] if isinstance(config, list) and config else None
        if not isinstance(first, str) or not first or '\x00' in first or not Path(first).expanduser().is_absolute():
            return None
        return Path(first).expanduser() / 'steamvr.vrsettings'
    except (OSError, ValueError):
        return None


def last_start_async_disabled(text):
    """True/False for the last compositor start in vrcompositor.txt, None if no start is logged.

    Each start logs its Vulkan device setup ("Global priority query enabled") and,
    right after it, the async line when async is off.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if 'Global priority query enabled' in line]
    if not starts:
        return (True if any(ASYNC_DISABLED in line for line in lines) else None)
    last = starts[-1]
    return any(ASYNC_DISABLED in line for line in lines[last:last + 4])


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _detail(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:160] if lines else ''


class AsyncReprojectionCheck:
    def __init__(self, registry=None, setup_log='/tmp/SteamVRLauncherSetup.log', runner=system_run,
                 pkexec='/usr/bin/pkexec', setcap=None, log=print, compositor_logs=None, running=None,
                 state_directory=None):
        self.registry, self.setup_log = registry, setup_log
        # When VROSTBridge last repaired the setting (RC4.2): the compositor log is
        # ignored only if that happened after SteamVR last wrote it.
        self.repair_marker = Path(state_directory) / 'steamvr-async-repair.json' if state_directory else None
        self.compositor_logs = ([Path(item) for item in compositor_logs] if compositor_logs is not None
                                else [Path.home() / item for item in COMPOSITOR_LOGS])
        self.running = running
        self.runner, self.log, self.pkexec = runner, log, pkexec
        self.setcap = _tool('setcap', ('/usr/sbin/setcap', '/usr/bin/setcap')) if setcap is None else setcap
        self._lock = threading.Lock()
        self._operation = threading.Lock()
        self._value = {'state': 'unknown', 'path': '', 'message': '', 'setup_log_error': False, 'can_repair': False, 'fix': ''}

    def status(self):
        with self._lock:
            return dict(self._value)

    def _set(self, state, message, path='', setup_log_error=False, can_repair=False, fix=''):
        with self._lock:
            changed = (self._value.get('state'), self._value.get('message')) != (state, message)
            self._value = {'state': state, 'path': str(path), 'message': message,
                           'setup_log_error': setup_log_error, 'can_repair': can_repair,
                           'fix': fix if can_repair else ''}
            value = dict(self._value)
        if changed and message:
            self.log('STEAMVR ASYNC ' + state + ': ' + message)
        return value

    def _repair_tools(self):
        return bool(self.pkexec and Path(self.pkexec).is_file() and self.setcap and Path(self.setcap).is_file())

    def check(self):
        """Read-only: report whether SteamVR can use asynchronous reprojection."""
        if sys.platform == 'win32':
            return self._set(NOT_NEEDED, WINDOWS_MESSAGE)
        try:
            return self._check()
        except Exception as error:  # never block a session
            return self._set('unknown', 'Could not check whether SteamVR can use background reprojection ('
                             + str(error)[:160] + ').')

    def _check(self):
        runtimes = steamvr_runtime_dirs(self.registry)
        if not runtimes:
            return self._set('not_installed', 'SteamVR was not found on this PC. Install SteamVR in Steam and start it once.')
        launcher = next((Path(root) / 'bin' / 'linux64' / LAUNCHER for root in runtimes
                         if (Path(root) / 'bin' / 'linux64' / LAUNCHER).is_file()), None)
        if launcher is None:
            return self._set('not_installed', "SteamVR's Linux program files were not found. "
                             'Verify the SteamVR files in Steam, then start SteamVR once.')
        log_error = self._setup_log_error()
        caps = has_cap_sys_nice(launcher)
        if caps['error']:
            return self._set('unknown', 'Could not read the SteamVR compositor permission (' + caps['error'] + ').',
                             launcher, log_error)
        if not caps['supported']:
            return self._set('missing', 'SteamVR is installed on a drive that cannot store the permission it needs '
                             'for background reprojection, ' + _JUDDER + ' Install SteamVR on a Linux-formatted '
                             'drive, such as your main drive.', launcher, log_error)
        if self._nosuid(launcher):
            return self._set('missing', 'SteamVR is installed on a drive that ignores the permission it needs for '
                             'background reprojection (it is mounted with "nosuid"), ' + _JUDDER + ' Install '
                             'SteamVR on your main drive or remove that mount option.', launcher, log_error)
        if caps['present'] and caps['effective']:
            return self._enabled_state(launcher, log_error)
        can_repair = self._repair_tools()
        if caps['present']:
            # Set without the effective flag: SteamVR's own setup may not notice.
            message = ("SteamVR's permission for background reprojection is incomplete, " + _JUDDER +
                       (' Select Repair (asks for your password) to set it the way SteamVR expects.' if can_repair
                        else ' Verify the SteamVR files in Steam, then restart SteamVR and accept its setup prompt.'))
        else:
            message = ('SteamVR on this PC cannot use background reprojection, ' + _JUDDER +
                       (" SteamVR's own setup tried to set this permission and failed." if log_error else '') +
                       (' Select Repair (asks for your password) or restart SteamVR and accept its setup prompt.'
                        if can_repair else ' Restart SteamVR and accept its setup prompt.'))
        return self._set('missing', message, launcher, log_error, can_repair)

    def _setting(self):
        """(settings path, value of steamvr.enableLinuxVulkanAsync: True, False or None when unset/unreadable)."""
        path = steamvr_settings_path(self.registry)
        if path is None:
            return None, None
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return path, None
        section = data.get('steamvr') if isinstance(data, dict) else None
        value = section.get(ASYNC_KEY) if isinstance(section, dict) else None
        return path, value if type(value) is bool else None

    def _repaired_at(self):
        if self.repair_marker is None:
            return None
        try:
            value = json.loads(self.repair_marker.read_text(encoding='utf-8')).get('time')
        except (OSError, ValueError, AttributeError):
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def _compositor_reports_disabled(self, settings_path):
        """What SteamVR's compositor logged at its last start, unless VROSTBridge repaired the setting since.

        RC4.1 compared the log with the settings file's modification time, but SteamVR
        rewrites its settings whenever it closes and VROSTBridge restores them after
        each session, so the log was nearly always ignored and a real "disabled" went
        unreported. Only a repair made after SteamVR last wrote the log hides it now.
        """
        repaired = self._repaired_at()
        for log in self.compositor_logs:
            try:
                info = log.stat()
                if repaired is not None and repaired > info.st_mtime:
                    return None  # repaired after that run; it applies at the next start
                with log.open('rb') as handle:
                    handle.seek(max(0, info.st_size - (512 << 10)))
                    text = handle.read().decode('utf-8', 'replace')
            except OSError:
                continue
            return last_start_async_disabled(text)
        return None

    def _enabled_state(self, launcher, log_error):
        path, setting = self._setting()
        disabled = self._compositor_reports_disabled(path)
        if setting is False:
            return self._set('disabled', "SteamVR's asynchronous (background) reprojection is turned off in its "
                             'settings, ' + _JUDDER + ' Select Repair to turn it on; it applies the next time SteamVR '
                             'starts.', launcher, log_error, True, 'setting')
        if disabled and setting is not True:
            return self._set('disabled', 'SteamVR turned background reprojection off at its last start ("' +
                             ASYNC_DISABLED + '"), ' + _JUDDER + ' Select Repair to turn its setting on; if SteamVR '
                             "still reports it off afterwards, the headset's display driver disables it.",
                             launcher, log_error, True, 'setting')
        if disabled:
            return self._set('disabled_driver', 'SteamVR reported background reprojection off at its last start '
                             "although its setting is on, so the headset's display driver disables it; "
                             'VROSTBridge cannot change that. Keep the frame rate steady (lower stream size or game '
                             'settings) to avoid judder.', launcher, log_error)
        return self._set('ready', 'SteamVR has the permission it needs for background reprojection'
                         + (' and its last start reported it on.' if disabled is False else '.'), launcher, log_error)

    def _repair_setting(self, before):
        """Turn steamvr.enableLinuxVulkanAsync on while SteamVR is closed."""
        running = self.running
        if running is None:
            from launcher.client_runtime import runtime_processes as running
        if running():
            message = 'Close SteamVR on this PC first, then select Repair: SteamVR rewrites its settings when it closes.'
            return dict(before, message=message, repaired=False, restart_required=False)
        path, _ = self._setting()
        if path is None:
            return dict(before, message="SteamVR's settings folder is not registered. Start SteamVR once, close it, "
                        'then select Repair.', repaired=False, restart_required=False)
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
            if not isinstance(data, dict) or not isinstance(data.setdefault('steamvr', {}), dict):
                raise ValueError('unexpected settings format')
            data['steamvr'][ASYNC_KEY] = True
            temporary = path.with_name(path.name + '.vrostbridge-async')
            temporary.write_text(json.dumps(data, indent=3) + '\n', encoding='utf-8')
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        except (OSError, ValueError) as error:
            message = 'SteamVR settings could not be changed (' + str(error)[:160] + ').'
            self.log('STEAMVR ASYNC repair: ' + message)
            return dict(before, message=message, repaired=False, restart_required=False)
        message = 'Repaired. SteamVR turns background reprojection on the next time it starts.'
        self.log('STEAMVR ASYNC repair: ' + ASYNC_KEY + ' set to true in ' + str(path) + '.')
        if self.repair_marker is not None:
            try:
                self.repair_marker.parent.mkdir(parents=True, exist_ok=True)
                self.repair_marker.write_text(json.dumps({'time': time.time()}), encoding='utf-8')
            except OSError:
                pass
        after = self._set('ready', message, before.get('path', ''), before.get('setup_log_error', False))
        return dict(after, repaired=True, restart_required=True)

    def _setup_log_error(self):
        """True when SteamVR's setup log records a failed setcap (read without blocking or following links)."""
        try:
            descriptor = os.open(self.setup_log, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        except (OSError, TypeError, ValueError):
            return False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                return False
            os.lseek(descriptor, max(0, info.st_size - (256 << 10)), os.SEEK_SET)
            text = os.read(descriptor, 256 << 10).decode('utf-8', 'replace')
        except OSError:
            return False
        finally:
            os.close(descriptor)
        return SETUP_FAILURE in text

    @staticmethod
    def _nosuid(path):
        """The kernel ignores file capabilities on file systems mounted nosuid."""
        try:
            return bool(os.statvfs(path).f_flag & os.ST_NOSUID)
        except (OSError, AttributeError):
            return False

    def _validated(self, path):
        """The real path of SteamVR's own vrcompositor-launcher, or (None, reason)."""
        text = os.fspath(path) if isinstance(path, (str, os.PathLike)) else ''
        if not text or len(text) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in text):
            return None, 'the file name is not valid'
        candidate = Path(text)
        if not candidate.is_absolute() or candidate.name != LAUNCHER:
            return None, 'it is not ' + LAUNCHER
        try:
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                return None, 'it is a link or not a regular file'
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None, 'the file does not exist'
        if real.parent.parts[-2:] != ('bin', 'linux64'):
            return None, 'it is not in a bin/linux64 folder'
        for root in steamvr_runtime_dirs(self.registry):
            try:
                # Resolve the folder only: a link named vrcompositor-launcher inside
                # the runtime must not make its target acceptable.
                expected = (Path(root) / 'bin' / 'linux64').resolve(strict=True) / LAUNCHER
            except (OSError, RuntimeError):
                continue
            if real == expected:
                return real, ''
        return None, "it is not inside SteamVR's registered folder"

    def repair(self, path=None):
        """Explicit user action: set cap_sys_nice=eip through pkexec, then check again."""
        if sys.platform == 'win32':
            # Nothing is run and no file or setting is touched.
            return dict(self.check(), message='Nothing to repair on Windows: ' + _WINDOWS_REASON,
                        can_repair=False, repaired=False, restart_required=False)
        with self._operation:
            try:
                return self._repair(path)
            except Exception as error:  # never block a session
                return dict(self._set('unknown', 'The repair could not be completed (' + str(error)[:160] + ').'),
                            repaired=False, restart_required=False)

    def _repair(self, path):
        before = self.check()
        if path is None and before['state'] == 'disabled' and before.get('fix') == 'setting':
            return self._repair_setting(before)
        if path is None:
            if before['state'] != 'missing' or not before['path'] or not before['can_repair']:
                return dict(before, repaired=False, restart_required=False)
            path = before['path']
        launcher, reason = self._validated(path)
        if launcher is None:
            message = "Nothing was changed: the selected file is not SteamVR's compositor launcher (" + reason + ').'
            self.log('STEAMVR ASYNC repair refused: ' + message)
            return dict(before, message=message, can_repair=False, repaired=False, restart_required=False)
        if not self._repair_tools():
            return dict(before, message='Repair needs the system password prompt (pkexec) and setcap, which are '
                        'not available. Restart SteamVR and accept its setup prompt instead.', can_repair=False,
                        repaired=False, restart_required=False)
        # No shell; pkexec authenticates only the system setcap tool for this one file.
        try:
            result = self.runner([self.pkexec, self.setcap, 'cap_sys_nice=eip', str(launcher)], 120)
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess([], 1, '', 'the password prompt timed out')
        except (OSError, subprocess.SubprocessError) as error:
            result = subprocess.CompletedProcess([], 1, '', str(error)[:160])
        after = self.check()
        if after['state'] == 'ready':
            message = 'Repaired. SteamVR can use background reprojection the next time it starts.'
            self._set('ready', message, after['path'], after['setup_log_error'])
            return dict(after, message=message, repaired=True, restart_required=True)
        detail = _detail(result)
        if 'authentication agent' in detail.lower():
            problem = ('No password prompt could be shown here. Switch to Desktop Mode and select Repair there, '
                       'or restart SteamVR and accept its setup prompt.')
        elif result.returncode == 126:
            problem = 'The password prompt was closed, so nothing was changed. You can select Repair again.'
        elif result.returncode == 127:
            problem = 'Permission was not granted, so nothing was changed. You can select Repair again.'
        elif result.returncode != 0:
            problem = 'The repair did not complete (' + (detail or 'exit code %s' % result.returncode) + ').'
        else:
            problem = 'The repair ran, but SteamVR still does not have the permission. ' + after['message']
        self.log('STEAMVR ASYNC repair: ' + problem)
        return dict(after, message=problem, repaired=False, restart_required=False)
