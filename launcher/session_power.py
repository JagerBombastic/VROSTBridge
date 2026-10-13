"""Keep the client PC awake and steady for the length of a VR session.

Headset and controller movement is not keyboard or mouse activity, so desktop
idle timers (KDE or Steam Game Mode) and a closed handheld lid can suspend the
PC mid-game. For each session only, a logind block inhibitor covers sleep and
the lid switch. Screen dimming and locking are left alone: the screen may still
turn off. The inhibitor's child waits on a pipe from this process, so the block
also ends if VROSTBridge crashes instead of leaving the PC unable to sleep.
The performance power profile gives steadier video decode timing; the previous
profile returns at the end unless the user changed it meanwhile.

Windows (1.0.0.7): a thread of its own holds SetThreadExecutionState with the system
and the display required for the session (Windows drops the request when that thread
or the companion ends), and Windows' power mode is Best performance until the session
stops. The power mode is a lasting setting, so a small receipt in the state folder
records what to set back: recover() (at startup, and at every begin()) sets back a
power mode that a companion which did not stop normally left changed. Windows keeps
one power mode on battery and one plugged in; one changed on the other power source
is set back when the PC is on that source again. What closing the lid does is a
machine-wide power plan setting, not a per-user or per-process one, and a PC left
unable to sleep with its lid closed (in a bag) could overheat, so it is not changed
on Windows: the status says so.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from launcher.client_network import system_run
from launcher import windows_power

# No 'idle': KDE PowerDevil turns a logind idle block into its "change screen
# settings" inhibition, which stops the screen dimming and turning off. Its idle
# suspend already honours the 'sleep' block, as does logind's own IdleAction.
INHIBIT_OPTIONS = ('--what=sleep:handle-lid-switch', '--who=VROSTBridge',
                   '--why=VR streaming session', '--mode=block')
_PROFILE = re.compile(r'[a-z][a-z0-9-]{0,31}')
_LIST_ENTRY = re.compile(r'\s*(\*)?\s*([a-z][a-z0-9-]{0,31}):\s*')
# power-profiles-daemon and tuned-ppd publish the same D-Bus API. Fedora's
# tuned-ppd does not ship powerprofilesctl, so systemd's busctl is the fallback.
_BUS_NAMES = (('org.freedesktop.UPower.PowerProfiles', '/org/freedesktop/UPower/PowerProfiles',
               'org.freedesktop.UPower.PowerProfiles'),
              ('net.hadess.PowerProfiles', '/net/hadess/PowerProfiles', 'net.hadess.PowerProfiles'))
_PROFILE_TEXT = {
    'set': 'The performance power mode is on until the session stops.',
    'already': 'The performance power mode was already on.',
    'not_offered': 'This PC has no performance power mode.',
    'unavailable': 'Power modes are not available on this PC.',
}
# Windows (1.0.0.7): the receipt of the power modes a session changed, until they are set back.
RECEIPT_NAME = 'session-power.json'
_GUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_SOURCES = ('ac', 'dc', 'unknown')
_SOURCE_TEXT = {'ac': 'plugged in', 'dc': 'on battery'}
_BEST = windows_power.OVERLAY_BEST_PERFORMANCE
LID_TEXT = ('Closing the lid still does what Windows is set to do (Control Panel > Power Options > Choose what '
            'closing the lid does).')


def default_state_directory():
    """The companion's state folder, as launcher/app.py's state_directory() finds it."""
    return Path(os.environ.get('LOCALAPPDATA', os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))) \
        / 'VROSTBridge'


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def system_environment():
    """As system_run: system tools must not load the frozen bundle's libraries."""
    environment = os.environ.copy()
    if 'LD_LIBRARY_PATH_ORIG' in environment:
        environment['LD_LIBRARY_PATH'] = environment['LD_LIBRARY_PATH_ORIG']
    else:
        environment.pop('LD_LIBRARY_PATH', None)
    environment['LC_ALL'] = 'C'
    return environment


def parse_profile_list(text):
    """`powerprofilesctl list` output -> (profile names in order, current profile or None)."""
    names, current = [], None
    for line in (text or '').splitlines():
        match = _LIST_ENTRY.fullmatch(line)
        if match and match.group(2) not in names:
            names.append(match.group(2))
            if match.group(1):
                current = match.group(2)
    return names, current


def parse_bus_profiles(data):
    """Profile names from the D-Bus 'Profiles' property (aa{sv}) as `busctl --json` prints it."""
    names = []
    for entry in data if isinstance(data, list) else ():
        value = entry.get('Profile') if isinstance(entry, dict) else None
        if isinstance(value, dict):
            value = value.get('data')
        if isinstance(value, str) and _PROFILE.fullmatch(value) and value not in names:
            names.append(value)
    return names


def _short(error):
    lines = str(error).strip().splitlines()
    return (lines[-1] if lines else type(error).__name__)[:160]


def _detail(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:160] if lines else ''


def _label(profile):
    return (profile or '').replace('-', ' ')


def _close(handle):
    try:
        if handle is not None:
            handle.close()
    except OSError:
        pass


def _read_tail(handle):
    if handle is None:
        return ''
    try:
        handle.seek(0)
        lines = handle.read(4096).decode('utf-8', 'replace').strip().splitlines()
    except (OSError, ValueError):
        return ''
    return lines[-1].strip()[:160] if lines else ''


class SessionPower:
    def __init__(self, runner=system_run, log=print, systemd_inhibit=None, powerprofilesctl=None,
                 popen=subprocess.Popen, busctl=None, cat=None, state_directory=None, windows=None):
        """Tool paths: None locates the tool; an empty string disables it.

        Windows only: state_directory holds the power mode receipt (None: the companion's state folder);
        windows is the launcher.windows_power API (None: the real one, on first use; tests pass a fake)."""
        self.runner, self.log, self.popen = runner, log, popen
        self.state_directory = Path(state_directory) if state_directory is not None else None
        self.windows = windows
        self._hold = None     # Windows: the ExecutionStateHold keeping the PC and its screen awake
        self._overlay = None  # Windows: (power source, previous power mode) this session changed
        self.systemd_inhibit = (_tool('systemd-inhibit', ('/usr/bin/systemd-inhibit', '/bin/systemd-inhibit'))
                                if systemd_inhibit is None else systemd_inhibit)
        self.powerprofilesctl = (_tool('powerprofilesctl', ('/usr/bin/powerprofilesctl',))
                                 if powerprofilesctl is None else powerprofilesctl)
        self.busctl = _tool('busctl', ('/usr/bin/busctl', '/bin/busctl')) if busctl is None else busctl
        self.cat = _tool('cat', ('/usr/bin/cat', '/bin/cat')) if cat is None else cat
        self._lock = threading.Lock()          # status snapshot
        self._operation = threading.RLock()    # begin()/end()
        self._process = self._stderr = None    # running inhibitor and its error output
        self._original = None                  # (profile, tool) to restore, if this session changed it
        self._bus_name = None
        self._value = {'state': 'idle', 'sleep_blocked': False, 'power_profile': None,
                       'original_profile': None, 'message': ''}

    def status(self):
        process = self._process
        if process is not None:
            try:
                ended = process.poll() is not None
            except (OSError, subprocess.SubprocessError):
                ended = False
            if ended:
                self._inhibitor_lost(process)
        with self._lock:
            return dict(self._value)

    def _set(self, state, message, sleep_blocked=False, power_profile=None, original_profile=None, **extra):
        with self._lock:
            self._value = {'state': state, 'sleep_blocked': sleep_blocked, 'power_profile': power_profile,
                           'original_profile': original_profile, 'message': message, **extra}
            value = dict(self._value)
        if message:
            self.log('POWER ' + state + ': ' + message)
        return value

    def _run(self, arguments, timeout=5):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', _short(error))

    def begin(self):
        """Block suspend and select the performance profile for this session only."""
        with self._operation:
            if sys.platform == 'win32':
                return self._begin_windows()
            self.end()  # a previous begin() is always undone first
            try:
                blocked, sleep_detail = self._start_inhibitor()
            except Exception as error:  # a session must never be blocked by this helper
                blocked, sleep_detail = False, _short(error)
            try:
                outcome, profile, profile_detail = self._raise_profile()
            except Exception as error:
                outcome, profile, profile_detail = 'failed', None, _short(error)
            if blocked:
                sleep_text = 'This PC will stay awake during the session; the screen can still turn off.'
            elif sleep_detail is None:
                sleep_text = ('This PC may go to sleep during the session because its sleep blocker '
                              '(systemd-inhibit) is not available.')
            else:
                sleep_text = ('This PC may go to sleep during the session: the system did not allow keeping '
                              'it awake (' + sleep_detail + ').')
            profile_text = _PROFILE_TEXT.get(outcome) or (
                'The performance power mode could not be turned on (' + profile_detail + ').')
            if blocked and outcome != 'failed':
                state = 'active'
            elif blocked or outcome in ('set', 'already'):
                state = 'partial'
            else:
                state = 'unavailable'
            return self._set(state, sleep_text + ' ' + profile_text, blocked, profile,
                             self._original[0] if self._original else None)

    def end(self):
        """Release the sleep block and restore the profile only if this session changed it."""
        with self._operation:
            if sys.platform == 'win32':
                return self._end_windows()
            process, stderr, original = self._process, self._stderr, self._original
            self._process = self._stderr = self._original = None
            if process is None and original is None:
                return self.status()
            profile, profile_text = self.status().get('power_profile'), ''
            try:
                if process is not None:
                    self._stop_inhibitor(process, stderr)
                if original:
                    profile, profile_text = self._restore_profile(*original)
            except Exception as error:  # never raise out of end()
                profile_text = 'Power settings could not be fully restored (' + _short(error) + ').'
            sleep_text = 'This PC can sleep normally again.' if process is not None else ''
            return self._set('idle', ' '.join(part for part in (sleep_text, profile_text) if part),
                             power_profile=profile)

    def recover(self):
        """Windows: set back a power mode that a companion which did not stop normally left changed (the receipt
        in the state folder). Call once at startup; begin() does it too. Nothing to do on Linux: the sleep block
        ends with this process. Never raises; returns status()."""
        if sys.platform != 'win32':
            return self.status()
        with self._operation:
            if self._overlay is None:  # never while this process's own session holds the power mode
                try:
                    texts = self._recover_windows(self._windows_api())
                except Exception as error:
                    texts = []
                    self.log('POWER: an earlier power mode could not be checked (' + _short(error) + ').')
                if texts:
                    with self._lock:
                        self._value = dict(self._value, message=' '.join(texts))
                    self.log('POWER recovered: ' + ' '.join(texts))
            return self.status()

    # Sleep inhibitor -----------------------------------------------------

    def _start_inhibitor(self):
        """(running, detail); detail None means the tool is not installed."""
        if not self.systemd_inhibit or not self.cat:
            return False, None
        try:
            stderr = tempfile.TemporaryFile()
        except OSError:
            stderr = None
        # `cat` instead of `sleep infinity`: it ends when this process closes the
        # pipe or exits, so a crash cannot leave the PC unable to sleep.
        arguments = [self.systemd_inhibit, *INHIBIT_OPTIONS, self.cat]
        try:
            process = self.popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=stderr if stderr is not None else subprocess.DEVNULL,
                                 start_new_session=True, env=system_environment())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _close(stderr)
            return False, _short(error)
        self._process, self._stderr = process, stderr  # end() can always clean it up from here on
        try:
            code = process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            return True, ''
        self._process = self._stderr = None
        detail = _read_tail(stderr)
        _close(stderr)
        _close(getattr(process, 'stdin', None))
        return False, detail or 'it stopped straight away with exit code %s' % code

    def _stop_inhibitor(self, process, stderr):
        _close(getattr(process, 'stdin', None))  # end of input ends the inhibitor's child
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError) as error:
            self.log('POWER: the sleep blocker did not stop cleanly: ' + _short(error))
        finally:
            _close(stderr)

    def _inhibitor_lost(self, process):
        with self._lock:
            if self._process is not process or not self._value.get('sleep_blocked'):
                return
            message = 'The sleep block ended unexpectedly, so this PC may go to sleep during the session.'
            state = 'partial' if self._value.get('power_profile') == 'performance' else 'unavailable'
            self._value = dict(self._value, state=state, sleep_blocked=False, message=message)
        self.log('POWER ' + state + ': ' + message)

    # Power profile -------------------------------------------------------

    def _raise_profile(self):
        """Returns (outcome, current profile, detail)."""
        for tool, available in (('powerprofilesctl', self.powerprofilesctl), ('busctl', self.busctl)):
            current = self._get(tool) if available else None
            if current:
                break
        else:
            return 'unavailable', None, ''
        if current == 'performance':
            return 'already', current, ''
        if 'performance' not in self._names(tool):
            return 'not_offered', current, ''
        problem = self._put(tool, 'performance')
        if problem:
            return 'failed', current, problem
        self._original = (current, tool)
        return 'set', 'performance', ''

    def _restore_profile(self, original, tool):
        current = self._get(tool)
        if current is None:
            return None, 'The power mode could not be checked, so it was left as it is.'
        if current != 'performance':
            return current, 'The power mode was changed during the session, so it was left as it is.'
        problem = self._put(tool, original)
        if problem:
            return current, 'The power mode could not be set back to ' + _label(original) + ' (' + problem + ').'
        return original, 'The power mode is back to ' + _label(original) + '.'

    def _get(self, tool):
        if tool == 'powerprofilesctl':
            result = self._run([self.powerprofilesctl, 'get'])
            value = (result.stdout or '').strip() if result.returncode == 0 else ''
        else:
            value = self._bus_get('ActiveProfile')
        return value if isinstance(value, str) and _PROFILE.fullmatch(value) else None

    def _names(self, tool):
        if tool == 'powerprofilesctl':
            result = self._run([self.powerprofilesctl, 'list'])
            return parse_profile_list(result.stdout)[0] if result.returncode == 0 else []
        return parse_bus_profiles(self._bus_get('Profiles'))

    def _put(self, tool, profile):
        if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
            return 'not a valid power mode name'
        if tool == 'powerprofilesctl':
            result = self._run([self.powerprofilesctl, 'set', profile])
        elif self._bus_name:
            result = self._run([self.busctl, '--system', 'set-property', *self._bus_name, 'ActiveProfile', 's', profile])
        else:
            return 'the power mode service is not available'
        return '' if result.returncode == 0 else (_detail(result) or 'exit code %s' % result.returncode)

    def _bus_get(self, name):
        for bus_name in (self._bus_name,) if self._bus_name else _BUS_NAMES:
            result = self._run([self.busctl, '--system', '--json=short', 'get-property', *bus_name, name])
            if result.returncode != 0:
                continue
            try:
                document = json.loads(result.stdout or '')
            except ValueError:
                continue
            self._bus_name = bus_name
            return document.get('data') if isinstance(document, dict) else None
        return None

    # Windows (1.0.0.7) ---------------------------------------------------

    def _windows_api(self):
        return self.windows if self.windows is not None else windows_power.api()

    def _begin_windows(self):
        self._end_windows()  # a previous begin() is always undone first
        native = self._windows_api()
        try:
            notes = self._recover_windows(native)
            if notes:
                self.log('POWER recovered: ' + ' '.join(notes))
        except Exception as error:
            self.log('POWER: an earlier power mode could not be checked (' + _short(error) + ').')
        source, battery = self._power_source(native)
        hold = windows_power.ExecutionStateHold(windows_power.SESSION_AWAKE, api=native)
        try:
            awake = hold.acquire()
        except Exception as error:  # a session must never be blocked by this helper
            awake, hold.detail = False, _short(error)
        if awake:
            self._hold = hold
        else:
            hold.release()
        try:
            outcome, mode, previous, detail, plan = self._raise_overlay(native, source)
        except Exception as error:
            outcome, mode, previous, detail, plan = 'failed', None, None, _short(error), None
        if awake:
            sleep_text = 'This PC and its screen stay awake during the session.'
        else:
            sleep_text = ('This PC may go to sleep during the session: Windows did not accept the request to keep '
                          'it awake' + (' (' + hold.detail + ')' if hold.detail else '') + '.')
        mode_text = {
            'set': "Windows' power mode is Best performance until the session stops.",
            'already': "Windows' power mode was already Best performance.",
            'plan': 'The ' + str(plan) + " power plan is in use, so Windows' power mode was left as it is.",
            'not_offered': "This PC's power plan does not offer the Best performance power mode.",
            'unavailable': "Windows' power mode is not available on this PC" + (' (' + detail + ')' if detail else '')
                           + '.',
        }.get(outcome) or ("Windows' power mode could not be set to Best performance (" + detail + ').')
        if awake and outcome != 'failed':
            state = 'active'
        elif awake or outcome in ('set', 'already', 'plan'):
            state = 'partial'
        else:
            state = 'unavailable'
        texts = (sleep_text, mode_text, LID_TEXT if battery else '')
        return self._set(state, ' '.join(text for text in texts if text), awake, windows_power.overlay_slug(mode),
                         windows_power.overlay_slug(previous) if outcome == 'set' else None, display_awake=awake,
                         power_mode=windows_power.overlay_label(mode) if mode else None, power_plan=plan,
                         lid='unchanged')

    def _end_windows(self):
        hold, overlay = self._hold, self._overlay
        self._hold = self._overlay = None
        if hold is None and overlay is None:
            return self.status()
        current = self.status()
        profile, mode, texts = current.get('power_profile'), current.get('power_mode'), []
        try:
            if hold is not None:
                hold.release()
                texts.append('This PC and its screen can sleep normally again.')
            if overlay is not None:
                guid, text = self._restore_overlay(self._windows_api(), *overlay)
                profile, mode = windows_power.overlay_slug(guid), windows_power.overlay_label(guid) if guid else None
                texts.append(text)
        except Exception as error:  # never raise out of end()
            texts.append('Power settings could not be fully restored (' + _short(error) + ').')
        return self._set('idle', ' '.join(texts), power_profile=profile, display_awake=False, power_mode=mode,
                         power_plan=None, lid='unchanged')

    @staticmethod
    def _power_source(native):
        """('ac' | 'dc' | 'unknown', whether the PC has a battery: True, False or None)."""
        try:
            status = native.power_status()
        except (OSError, ValueError):
            return 'unknown', None
        source = {1: 'ac', 0: 'dc'}.get(status.get('ac_line'), 'unknown')
        flag = status.get('battery_flag')
        battery = None if flag == 255 or not isinstance(flag, int) else not flag & 128
        return source, battery

    def _raise_overlay(self, native, source):
        """(outcome, power mode now, power mode before, detail, high-performance plan name)."""
        try:
            plan = windows_power.PERFORMANCE_PLANS.get(native.active_scheme())
        except (OSError, ValueError):
            plan = None
        if plan:
            return 'plan', None, None, '', plan
        try:
            current = native.overlay()
        except (OSError, ValueError) as error:
            return 'unavailable', None, None, _short(error), None
        if current == _BEST:
            return 'already', current, None, '', None
        modes = self._read_receipt()
        modes[source] = {'previous': current, 'set': _BEST}
        self._write_receipt(modes)  # first: a crash right after the change can still be undone
        try:
            native.set_overlay(_BEST)
        except (OSError, ValueError) as error:
            self._forget(source)
            return 'failed', current, None, _short(error), None
        try:
            now = native.overlay()
        except (OSError, ValueError):
            now = _BEST  # Windows accepted the change
        if now != _BEST:
            self._forget(source)
            return 'not_offered', now, None, '', None
        self._overlay = (source, current)
        return 'set', _BEST, current, '', None

    def _restore_overlay(self, native, source, previous):
        """Set the power mode back unless the user changed it: (power mode now or None, message)."""
        label = windows_power.overlay_label(previous)
        now, _ = self._power_source(native)
        try:
            current = native.overlay()
        except (OSError, ValueError) as error:
            return None, ("Windows' power mode could not be checked (" + _short(error) + '); VROSTBridge checks it '
                          'again when it next starts.')
        if source in _SOURCE_TEXT and now in _SOURCE_TEXT and now != source:
            # The receipt keeps it: recover() sets it back once the PC is on that power source again.
            return current, ("Windows' power mode " + ('plugged in' if source == 'ac' else 'on battery') + ' goes back '
                             'to ' + label + ' when VROSTBridge next starts while this PC is ' + _SOURCE_TEXT[source]
                             + '.')
        if current != _BEST:
            self._forget(source)
            return current, "Windows' power mode was changed during the session, so it was left as it is."
        try:
            native.set_overlay(previous)
        except (OSError, ValueError) as error:
            return current, ("Windows' power mode could not be set back to " + label + ' (' + _short(error) + '); '
                             'VROSTBridge tries again when it next starts.')
        self._forget(source)
        return previous, "Windows' power mode is back to " + label + '.'

    def _recover_windows(self, native):
        """Set back the receipt's power modes for the current power source; returns what was done."""
        modes = self._read_receipt()
        if not modes:
            return []
        source, _ = self._power_source(native)
        texts = []
        for key in list(modes):
            entry = modes[key]
            if key in _SOURCE_TEXT and source in _SOURCE_TEXT and key != source:
                continue  # Windows keeps that power mode for the other power source
            try:
                current = native.overlay()
            except (OSError, ValueError):
                continue
            if current != entry['set']:
                del modes[key]  # changed since: the user's choice stays
                continue
            try:
                native.set_overlay(entry['previous'])
            except (OSError, ValueError) as error:
                self.log("POWER: Windows' power mode could not be set back (" + _short(error) + ').')
                continue
            del modes[key]
            texts.append("Windows' power mode is back to " + windows_power.overlay_label(entry['previous']) +
                         ' (a session that did not stop normally had changed it).')
        self._write_receipt(modes)
        return texts

    def _receipt_path(self):
        return (self.state_directory or default_state_directory()) / RECEIPT_NAME

    def _read_receipt(self):
        """{power source: {'previous': guid, 'set': guid}}; {} when there is none or it is unreadable."""
        try:
            document = json.loads(self._receipt_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        modes = document.get('power_modes') if isinstance(document, dict) else None
        result = {}
        for key, entry in (modes.items() if isinstance(modes, dict) else ()):
            if key in _SOURCES and isinstance(entry, dict) and all(
                    isinstance(entry.get(name), str) and _GUID.fullmatch(entry[name]) for name in ('previous', 'set')):
                result[key] = {'previous': entry['previous'], 'set': entry['set']}
        return result

    def _write_receipt(self, modes):
        path = self._receipt_path()
        try:
            if not modes:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'version': 1, 'power_modes': modes}) + '\n', encoding='utf-8')
            os.replace(temporary, path)
        except OSError as error:
            self.log("POWER: the note of Windows' power mode could not be saved (" + _short(error) + ').')

    def _forget(self, source):
        modes = self._read_receipt()
        if modes.pop(source, None) is not None:
            self._write_receipt(modes)
