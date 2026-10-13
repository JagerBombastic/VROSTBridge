"""Beyond 2e eye tracking run by VROSTBridge on the Bazzite client (RC4.5).

Three parts run together, started and stopped here:
- the eye-camera helper (go-bsb-cams), serving the Beyond 2e's two eye cameras as one side-by-side
  MJPEG stream on 127.0.0.1:8880 (this PC only);
- Baballonia (launcher/baballonia.py), which turns those pictures into where each eye looks and sends
  it as VRChat-style OSC to 127.0.0.1:9005;
- the gaze relay (launcher/gaze_provider.py) on 127.0.0.1:9005, which passes each sample to the running
  native client's gaze socket.

Who wants it ('owners'): the session (Start with Eye tracking on, until Stop) and the person (Open
Baballonia, until Close Baballonia or until Baballonia's own window is closed). One worker thread
reconciles what runs with what is wanted, every 2 seconds or when told, so a Start or Stop never
waits for it and a Start right after a Stop finds everything still running (a Start during the
3-second stop of Baballonia keeps its gaze socket and has everything started again).

What the sandbox runs of Baballonia 1.1.1.0rc6 showed, and what follows from it:
- its OSC sender stops for good after one refused send: the relay opens before Baballonia starts and
  closes only after it stops;
- its MJPEG reader does not reconnect, and once the stream ends it spins a CPU core: when the camera
  helper ends, Baballonia is stopped at once and started again after the cameras;
- it ignores SIGTERM (only its background services stop), so it is killed after 3 seconds;
- a Baballonia started before this companion's relay may have lost its sender: it is restarted;
- with no camera frames it sends nothing, so 25 seconds without a sample while the cameras run means
  something is stuck: the cameras and Baballonia are restarted, at most once every 5 minutes, never
  during a calibration or the 15 seconds after one (it saves the trained model's path 2 s later).
Both programs run under a watcher that ends them if this companion is gone (crashed or killed).
Live gaze is never logged or saved.

Windows (1.0.0.7): there is no camera helper. Windows lists the Beyond 2e's eye cameras as a webcam (BigEye) and
Baballonia reads them itself; EyeCameras then only says whether Windows lists them (its device list, WMI), watches
that they stay connected while eye tracking runs (Baballonia's reader also spins once they are gone), and gives
their name and picture size to Baballonia's settings. No camera frame rate is known there: the page shows
Baballonia's own samples a second. The relay sends to the native client's UDP port on 127.0.0.1 (the target
('127.0.0.1', port)). Baballonia's window is minimised and shown through Win32 (launcher/baballonia.py).
"""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from launcher.baballonia import (EYE_CAMERA_SIZE, OSC_PORT, WINDOWS_CAMERA_NAME, launch_environment, probe_frame_size,
                                 watched, windows_api, windows_eye_camera, xr_runtime_running)
from launcher.gaze_provider import GazeRelay, gaze_target
from launcher import x11_window

CAMERA_PORT = 8880
# Windows: how often a running camera check asks whether the cameras are still connected (a cheap Configuration
# Manager call; Windows' device list (WMI) at most every WINDOWS_LIST_S when that call is not available).
WINDOWS_CHECK_S = 3.0
WINDOWS_LIST_S = 15.0


def _windows():
    """Decided at call time (the tests change sys.platform)."""
    return sys.platform == 'win32'
# 1.0.0.5: the camera helper's own counts (its STATS line every 5 s) are current for this long, and at
# most this many of its CAMERA lines (one each time it opens the cameras) are logged per run.
FEED_FRESH_S = 15
CAMERA_LINES = 12
_HELPER_LINE = re.compile(r'\b(STATS|CAMERA) (.*)$')


def helper_values(text):
    """('STATS'|'CAMERA', values) for one of the camera helper's lines, else None. Numbers become int or
    float, 'WxH' a [w, h] pair; other words stay short strings. The first word of a CAMERA line is its format."""
    match = _HELPER_LINE.search(text)
    if not match:
        return None
    kind, rest = match.group(1), match.group(2)
    values = {}
    words = rest.split()
    if kind == 'CAMERA' and words and '=' not in words[0]:
        values['format'] = words[0][:16]
        if len(words) > 1 and re.fullmatch(r'\d{1,5}x\d{1,5}', words[1]):
            values['size'] = [int(v) for v in words[1].split('x')]
    for word in words[:32]:
        key, sep, raw = word.partition('=')
        if not sep or not re.fullmatch(r'[a-z_]{1,32}', key):
            continue
        if re.fullmatch(r'\d{1,5}x\d{1,5}', raw):
            values[key] = [int(v) for v in raw.split('x')]
        elif re.fullmatch(r'-?\d{1,12}', raw):
            values[key] = int(raw)
        elif re.fullmatch(r'-?\d{1,9}\.\d{1,6}', raw):
            values[key] = float(raw)
        else:
            values[key] = raw[:48]
    return kind, values
CAMERA_RULE_PATH = '/etc/udev/rules.d/70-vrostbridge-bigeye.rules'
# The same rule as config/70-vrostbridge-bigeye.rules: the signed-in desktop user may open the eye
# cameras (USB 35bd:0202). Numbered below 73 so systemd's uaccess step sees the tag.
CAMERA_RULE = ('# VROSTBridge: the Beyond 2e eye cameras for the signed-in desktop user.\n'
               'SUBSYSTEM=="usb", ATTR{idVendor}=="35bd", ATTR{idProduct}=="0202", TAG+="uaccess"\n')
CAMERA_ACCESS_SCRIPT = (
    'umask 022 && printf "%s" "$1" > ' + CAMERA_RULE_PATH + '.vrostbridge && '
    'mv -f ' + CAMERA_RULE_PATH + '.vrostbridge ' + CAMERA_RULE_PATH + ' && '
    'udevadm control --reload-rules && '
    'udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=35bd --attr-match=idProduct=0202')


def system_run(arguments, timeout):
    """A system tool with the system's libraries (not the frozen companion's)."""
    environment = launch_environment()
    environment['LC_ALL'] = 'C'
    return subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, env=environment, check=False)


def allow_camera_access(runner=system_run, pkexec='/usr/bin/pkexec', shell='/bin/sh'):
    """Install the camera rule as root after the system's own password prompt (pkexec): one fixed
    script, the rule passed as its argument, nothing from the user's files run as root. Linux only: Windows lets
    the signed-in user's programs open the cameras."""
    if _windows():
        raise ValueError('Windows needs no camera access setting: programs of the signed-in user can open the eye '
                         'cameras.')
    if not pkexec or not Path(pkexec).is_file():
        raise ValueError('This PC has no administrator prompt (pkexec), so VROSTBridge cannot allow camera '
                         'access itself. Copy config/70-vrostbridge-bigeye.rules from the VROSTBridge folder to '
                         '/etc/udev/rules.d/ as administrator, then reconnect the headset.')
    result = runner([pkexec, shell, '-c', CAMERA_ACCESS_SCRIPT, 'vrostbridge-camera-access', CAMERA_RULE], 180)
    if result.returncode == 126:
        raise ValueError('Camera access was not allowed: the password prompt was closed. Try again.')
    if result.returncode == 127:
        raise ValueError('Camera access was not allowed: the password was refused, or no password prompt could '
                         'be shown here (in Game Mode, use the VROSTBridge page in Desktop Mode).')
    if result.returncode != 0:
        raise ValueError('Allowing camera access did not finish: ' +
                         ((result.stderr or result.stdout or '').strip()[-240:] or 'exit ' + str(result.returncode)))


class EyeCameras:
    """The go-bsb-cams helper on 127.0.0.1:8880 (a separate GPL-2.0 program, started as a process).

    Windows (1.0.0.7): no helper (Baballonia opens the cameras itself). start() checks that Windows lists the
    cameras (the 'no-device' state and a ValueError when it does not), running() stays true while they stay
    connected, wait_frames() gives their known picture size, and stop() ends the check."""
    MESSAGES = {
        'access': 'This PC does not let VROSTBridge open the eye cameras yet: choose Allow eye camera access '
                  '(it asks for your password once).',
        'no-device': "The Beyond 2e's eye cameras were not found: check that the headset is connected and on.",
    }

    def __init__(self, executable, directory, log=print, popen=subprocess.Popen, detect=None, win32=None):
        self.executable = executable  # a path, or a function returning one (the install layout varies)
        self.directory = Path(directory)
        self.log, self.popen = log, popen
        self.lock = threading.Lock()  # never held while logging (the companion's log takes its own lock)
        self.process = None  # the watcher running the helper
        self.stopping = False
        self.failure = None  # (state, last line) of the last run that ended by itself
        self._state, self._message, self.last_line = 'off', '', ''
        # 1.0.0.5: the helper's STATS line (every 5 s) and its CAMERA line (each time it opens the cameras).
        self.feed, self.feed_at, self.camera, self.camera_lines = None, 0.0, None, 0
        # Windows (1.0.0.7): Windows' answer about the cameras (launcher/baballonia.py windows_eye_camera), and the
        # check that runs while eye tracking runs.
        self.detect = detect or windows_eye_camera
        self._win32 = win32
        self.windows_camera, self.windows_checked, self._refreshing = None, 0.0, False
        self._windows_running, self._windows_generation, self._windows_stop = False, 0, threading.Event()
        self._windows_note = None  # the last note logged (generic name, no answer): logged once

    @property
    def win32(self):
        if self._win32 is None:
            self._win32 = windows_api()
        return self._win32

    def path(self):
        return Path(self.executable() if callable(self.executable) else self.executable)

    def running(self):
        if _windows():
            return self._windows_running
        process = self.process
        return process is not None and process.poll() is None

    def status(self):
        if _windows():
            return self._windows_status()
        with self.lock:
            value = {'state': self._state, 'message': self._message}
            if self.feed and time.monotonic() - self.feed_at <= FEED_FRESH_S:
                value['feed'] = dict(self.feed)
            if self.camera:
                value['camera'] = dict(self.camera)
            return value

    def note_line(self, text):
        """1.0.0.5: keep the helper's STATS and CAMERA lines as values; True when the line was one of them."""
        values = helper_values(text)
        if values is None:
            return False
        kind, parsed = values
        with self.lock:
            if kind == 'STATS':
                self.feed, self.feed_at = parsed, time.monotonic()
            elif 'size' in parsed or 'negotiated_us' in parsed:
                self.camera = parsed
        return True

    # ------------------------------------------------------------------ Windows (1.0.0.7)
    def _windows_status(self):
        """State and message as on Linux, with 'presence' (Windows' answer: 'present', 'generic-name', 'absent' or
        'unknown'; None before the first answer), 'camera' (their name and picture size) and no frame rate."""
        camera = self.presence()
        with self.lock:
            value = {'state': self._state, 'message': self._message, 'feed': None,
                     'presence': camera.get('state') if camera else None}
            name = (self.camera or {}).get('name') or (camera or {}).get('name')
        if name:
            value['camera'] = {'name': name, 'size': list(EYE_CAMERA_SIZE)}
        if value['state'] == 'off' and camera and camera.get('state') in ('absent', 'generic-name') and camera.get('message'):
            value['message'] = camera['message']
        return value

    def presence(self, max_age=30.0):
        """Windows: Windows' last answer about the cameras (see windows_eye_camera), asked again in the background
        when older than max_age: the pages ask every second and never wait for it."""
        with self.lock:
            camera, when = self.windows_camera, self.windows_checked
            refresh = (camera is None or time.monotonic() - when > max_age) and not self._refreshing
            if refresh:
                self._refreshing = True
        if refresh:
            threading.Thread(target=self._refresh, daemon=True, name='VROSTBridge-eye-camera-check').start()
        return dict(camera) if camera else None

    def _refresh(self):
        try:
            self.check()
        finally:
            with self.lock:
                self._refreshing = False

    def check(self):
        """Windows: ask Windows now whether the cameras are connected (about a second at most)."""
        try:
            camera = dict(self.detect())
        except Exception as error:  # never let the check take eye tracking down
            camera = {'state': 'unknown', 'name': None, 'device_id': None,
                      'message': "Windows did not say whether the Beyond 2e's eye cameras are connected (" +
                                 str(error)[:120] + '); eye tracking tries them anyway.'}
        with self.lock:
            self.windows_camera, self.windows_checked = camera, time.monotonic()
        return dict(camera)

    def camera_name(self):
        """Windows: the cameras' name for Baballonia's settings (BigEye, or the generic name Windows gave them)."""
        with self.lock:
            camera = self.camera or {}
            known = self.windows_camera or {}
        return camera.get('name') or (known.get('name') if known.get('state') in ('present', 'generic-name') else None)

    def _start_windows(self):
        with self.lock:
            if self._windows_running:
                return 'running'
        camera = self.check()
        state = camera.get('state')
        if state == 'absent':
            with self.lock:
                self._state, self._message = 'no-device', self.MESSAGES['no-device']
                self.camera = None
            raise ValueError(self.MESSAGES['no-device'])
        note = camera.get('message') if state in ('generic-name', 'unknown') else None
        with self.lock:
            self._windows_generation += 1
            generation = self._windows_generation
            self._windows_stop = stop = threading.Event()
            self._windows_running, self.stopping = True, False
            self._state = 'running'
            self._message = note or 'The eye cameras are connected; Baballonia reads them.'
            self.camera = {'name': camera.get('name') or WINDOWS_CAMERA_NAME, 'size': list(EYE_CAMERA_SIZE)}
            repeated = note == self._windows_note
            self._windows_note = note
        if note and not repeated:
            self.log('Eye cameras: ' + note)
        threading.Thread(target=self._watch_windows, args=(generation, stop, camera.get('device_id')), daemon=True,
                         name='VROSTBridge-eye-cameras').start()
        return 'started'

    def _watch_windows(self, generation, stop, device_id):
        """While eye tracking runs: whether the cameras are still connected (the headset turned off or unplugged).
        Gone, running() turns false, as when the Linux helper ends: Baballonia is stopped, and both start again once
        Windows lists the cameras again."""
        listed = time.monotonic()
        while not stop.wait(WINDOWS_CHECK_S):
            present = None
            if device_id:
                try:
                    present = self.win32.device_present(device_id)
                except OSError:
                    present = None
            if present is None and time.monotonic() - listed >= WINDOWS_LIST_S:
                listed = time.monotonic()
                present = {'present': True, 'generic-name': True, 'absent': False}.get(self.check().get('state'))
            if present is False:
                with self.lock:
                    if generation != self._windows_generation:
                        return
                    self._windows_running = False
                    self._state, self._message = 'no-device', self.MESSAGES['no-device']
                    self.windows_camera = {'state': 'absent', 'name': None, 'device_id': None,
                                           'message': self.MESSAGES['no-device']}
                    self.windows_checked = time.monotonic()
                self.log("Eye cameras: the Beyond 2e's eye cameras were disconnected.")
                return

    def _stop_windows(self):
        with self.lock:
            was = self._windows_running
            self._windows_generation += 1
            self._windows_stop.set()
            self._windows_running, self.stopping = False, True
            if self._state == 'running':
                self._state, self._message = 'off', ''
        return was

    def orphans(self, path=None):
        """Helpers from this install an earlier companion left running (they would hold the port)."""
        if _windows():
            return []
        try:
            target = os.path.realpath(path or self.path())
            entries = [entry for entry in Path('/proc').iterdir() if entry.name.isdecimal()]
        except OSError:
            return []
        found = []
        for entry in entries:
            try:
                if os.readlink(entry/'exe') == target and entry.stat().st_uid == os.getuid():
                    found.append(int(entry.name))
            except OSError:
                continue
        process = self.process
        child_of_ours = set()
        if process is not None:
            for pid in found:  # the helper this object runs is the watcher's child, not an orphan
                try:
                    if int(Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[1]) == process.pid:
                        child_of_ours.add(pid)
                except (OSError, ValueError, IndexError):
                    continue
        return [pid for pid in found if pid not in child_of_ours]

    def end_orphans(self, path=None):
        ended = 0
        for pid in self.orphans(path):
            try:
                os.kill(pid, signal.SIGKILL)
                ended += 1
            except OSError:
                pass
        if ended:
            self.log('Eye cameras: ended a camera helper an earlier VROSTBridge left running.')
        return ended

    def start(self):
        """Start the helper unless it runs. Returns 'running' or 'started'; raises if it cannot start."""
        if _windows():
            return self._start_windows()
        with self.lock:
            if self.running():
                return 'running'
            path = self.path()
            if not path.is_file():
                self._state, self._message = 'failed', 'The eye-camera helper is missing: repair or reinstall VROSTBridge.'
                missing = self._message
            else:
                missing = None
        if missing:
            raise ValueError(missing)
        self.end_orphans(path)
        with self.lock:
            if self.running():
                return 'running'
            self.directory.mkdir(parents=True, exist_ok=True)  # it writes a udev rule here when access fails
            # Under the watcher: it ends the helper if this companion is gone.
            self.process = self.popen(watched([path, '-port', CAMERA_PORT], 1), cwd=str(self.directory),
                                      env=launch_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                      start_new_session=True)
            self.stopping = False
            self._state, self._message, self.last_line = 'starting', 'Starting the eye cameras…', ''
            self.feed, self.feed_at, self.camera, self.camera_lines = None, 0.0, None, 0
            process = self.process
        threading.Thread(target=self._read, args=(process,), daemon=True, name='VROSTBridge-eye-cameras').start()
        return 'started'

    def _read(self, process):
        # After a failed run, the next run's lines are held back until it works or fails differently:
        # retries every 15 s with the headset off must not fill the log with the same lines.
        held = [] if self.failure else None
        shown = 0
        try:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                lower = text.lower()
                state = ('access' if 'cannot be accessed' in lower else 'no-device' if 'could not find device' in lower
                         else 'running' if 'server is running' in lower else None)
                with self.lock:
                    if self.process is process:
                        if 'STATS ' not in text:
                            self.last_line = text[-200:]
                        if state:
                            self._state, self._message = state, self.MESSAGES.get(state, 'The eye cameras are running.')
                if self.note_line(text):
                    # 1.0.0.5: STATS lines are kept as values, not logged; CAMERA lines (rare) are logged.
                    if 'STATS ' in text or self.camera_lines >= CAMERA_LINES:
                        continue
                    self.camera_lines += 1
                    self.log('Eye cameras: ' + text[:240])
                    continue
                if shown >= 30:  # its start-up lines; frame errors only appear with -verbose
                    continue
                shown += 1
                if held is not None and state == 'running':
                    for earlier in held:
                        self.log('Eye cameras: ' + earlier)
                    held = None
                if held is None:
                    self.log('Eye cameras: ' + text[:240])
                else:
                    held.append(text[:240])
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdout.close()
            except (OSError, AttributeError):
                pass
        code = process.wait()
        with self.lock:
            if self.process is not process:
                return
            stopping = self.stopping
            if stopping:
                self._state, self._message = 'off', ''
            elif self._state not in ('access', 'no-device'):
                self._state = 'failed'
                self._message = ('The eye-camera helper stopped (' + (self.last_line or 'exit ' + str(code)) + ').')
            failure = None if stopping else (self._state, self.last_line)
            repeated = failure is not None and failure == self.failure
            self.failure = failure
        if held and not repeated:
            for earlier in held:
                self.log('Eye cameras: ' + earlier)
        if not stopping and not repeated:
            self.log('Eye cameras: the helper ended (exit ' + str(code) + ').')

    def wait_frames(self, cancelled, timeout=12.0):
        """The cameras' picture size once they send pictures; None if the helper ended or sent none. Windows: their
        known size while they are connected (Baballonia opens them, not VROSTBridge)."""
        if _windows():
            return tuple(EYE_CAMERA_SIZE) if self._windows_running else None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not cancelled():
            if not self.running():
                time.sleep(.2)  # let the reader record why it ended
                return None
            if self.status()['state'] == 'running':
                size = probe_frame_size(timeout=max(.5, min(4.0, deadline - time.monotonic())))
                if size:
                    return size
            time.sleep(.25)
        return None

    def stop(self):
        if _windows():
            return self._stop_windows()
        with self.lock:
            process = self.process
            self.stopping = True
        if process is not None and process.poll() is None:
            process.terminate()  # the watcher ends the helper
            try:
                process.wait(4)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
                process.wait(3)
        with self.lock:
            if self.process is process:
                self._state, self._message = 'off', ''
        return process is not None


class EyeTracking:
    CHECK_S = 2.0
    FRAMES_WAIT_S = 12.0
    CAMERA_RETRY_S = 15.0
    SILENCE_S = 25.0          # no sample for this long while everything runs: something is stuck
    RESTART_SPACING_S = 300.0  # at most one automatic restart in five minutes
    CALIBRATION_GRACE_S = 900.0  # after the session ends, how long a running calibration may finish
    CALIBRATION_SETTLE_S = 15.0  # after it ends, until Baballonia has saved the trained model's path
    QUICK_EXIT_S = 15.0       # closed this soon after starting: it did not start properly

    def __init__(self, manager, cameras, log=print, relay_factory=None, game_mode=lambda: False, port=OSC_PORT,
                 centre=None):
        self.manager, self.cameras, self.log = manager, cameras, log
        # 1.0.0.8: gaze_provider.GazeCentre, which keeps the eye tracker's straight ahead centred (None: as it comes).
        self.centre = centre
        self.relay_factory = relay_factory or (lambda: GazeRelay(None, port, centre=self.centre).start())
        self.game_mode = game_mode
        self.lock = threading.RLock()  # never held while logging or waiting for a program
        self.wake = threading.Event()
        self.owners = set()
        self.target = None
        self.relay = None
        self.thread = None
        self.closing = False
        self.phase, self.message = 'off', ''
        self.frame_size = None
        self.camera_failed_at = -1e9
        self.ours = False            # Baballonia started by this companion (watched, restarted, stopped)
        self.started_at = None
        self.closed = False          # Baballonia was closed (its window, or Close Baballonia) while wanted
        self.last_restart = -1e9
        self.restarted_silent = False
        self.wind_down_since = None
        self.calibrating = False
        self.calibration_model = None  # the model setting before the calibration running now
        self.calibration_ended = None
        self.reader_stale = False  # the cameras restarted under a running Baballonia (kept for its calibration)
        self.running = False         # Baballonia, as of the last check (for the pages)
        self.xr = None               # RC4.6: SteamVR (or another VR runtime) running while Baballonia runs:
                                     # its calibration shows in the headset only then, else on this screen
        self.access = {'state': 'idle', 'message': ''}  # Allow eye camera access, run in the background
        self._rate = None
        self.minimise_generation = 0  # the Baballonia start a minimise watcher belongs to (RC4.6)
        manager.on_installed = self.kick

    # ------------------------------------------------------------------ requests
    def begin(self, owner, target=None):
        """Eye tracking wanted by 'session' (with the native client's gaze input: its socket path on Linux,
        ('127.0.0.1', port) on Windows) or 'user'. The relay opens now (OSError if port 9005 is taken, or the target
        cannot be used); cameras and Baballonia follow in the background."""
        if owner == 'session':
            try:
                target = gaze_target(target)
            except ValueError as error:
                raise OSError('Eye tracking cannot send to ' + repr(target)[:80] + ': ' + str(error)[:120]) from error
        with self.lock:
            if self.closing:
                return
            self.owners.add(owner)
            if owner == 'user':
                self.minimise_generation += 1  # Open Baballonia: the person wants to see it
            if owner == 'session':
                self.target = target  # a socket path (str), ('127.0.0.1', port) or None
                self.session_gaps = True  # 1.0.0.4: count this session's pauses in the gaze afresh
            self.closed = False  # a new Start or Open starts Baballonia again
            self.restarted_silent = False
            self.camera_failed_at = -1e9  # ...and tries the cameras again at once
            self.wind_down_since = None
            try:
                self._relay_up()
            except OSError:
                if owner == 'user':  # nothing to cancel later: the person can simply try again
                    self.owners.discard('user')
                raise
            finally:
                self.kick()

    def end(self, owner):
        with self.lock:
            self.owners.discard(owner)
            if owner == 'session':
                self.target = None
                if self.relay:
                    self.relay.set_target(None)
            self.kick()

    MINIMISE_WAIT_S = 60.0  # Baballonia loads its models before its window appears

    MINIMISE_SETTLE_S = 8.0  # Windows: once its window is there, how long it is kept minimised

    def _minimise_when_shown(self):
        """RC4.6: Baballonia's window opened over the VROSTBridge page at every session start in Desktop Mode.
        Started for a session (not by Open Baballonia), it is minimised as soon as it appears; Show Baballonia
        on the card brings it back (to calibrate). X11 through XWayland (launcher/x11_window.py). Windows: it
        starts minimised; should its window show anyway, it is minimised without taking the focus (Win32)."""
        if _windows():
            return self._minimise_when_shown_windows()
        if not os.environ.get('DISPLAY'):  # no X11 here (a Wayland-only desktop, tests)
            return
        with self.lock:
            self.minimise_generation += 1
            generation = self.minimise_generation

        def current():
            # Only for the Baballonia it was started for: a newer start, a stop, or Open Baballonia ends it.
            with self.lock:
                return generation == self.minimise_generation and not self.closing and 'user' not in self.owners

        def work():
            deadline = time.monotonic() + self.MINIMISE_WAIT_S
            try:
                with x11_window.Display() as display:
                    while time.monotonic() < deadline and current():
                        windows = display.find(self.manager.window_pids(), x11_window.BABALLONIA_CLASS)
                        if windows and not current():
                            return
                        if windows:
                            for window in windows:
                                display.minimise(window)
                            self.log('Eye tracking: Baballonia\'s window was minimised; Show Baballonia on the Eye '
                                     'tracking card opens it.')
                            return
                        time.sleep(.5)
            except OSError as error:
                self.log('Eye tracking: Baballonia\'s window cannot be minimised here (' + str(error)[:120] + ').')
        threading.Thread(target=work, daemon=True, name='VROSTBridge-baballonia-window').start()

    def _minimise_when_shown_windows(self):
        minimise = getattr(self.manager, 'minimise_windows', None)
        if minimise is None:
            return
        with self.lock:
            self.minimise_generation += 1
            generation = self.minimise_generation

        def current():
            with self.lock:
                return generation == self.minimise_generation and not self.closing and 'user' not in self.owners

        def work():
            deadline, settled = time.monotonic() + self.MINIMISE_WAIT_S, None
            try:
                while time.monotonic() < deadline and current():
                    shown = minimise(current)
                    if shown:
                        self.log('Eye tracking: Baballonia\'s window was minimised; Show Baballonia on the Eye '
                                 'tracking card opens it.')
                        return
                    if shown is not None:  # its window is there, minimised as it started
                        settled = settled or time.monotonic() + self.MINIMISE_SETTLE_S
                        if time.monotonic() >= settled:
                            return
                    time.sleep(.5)
            except (OSError, ValueError) as error:
                self.log('Eye tracking: Baballonia\'s window cannot be minimised here (' + str(error)[:120] + ').')
        threading.Thread(target=work, daemon=True, name='VROSTBridge-baballonia-window').start()

    def show_window(self):
        """Show Baballonia's window (minimised at session start) and give it focus, as the taskbar would."""
        if _windows():
            show = getattr(self.manager, 'show_window', None)
            if show is None:
                raise ValueError('Baballonia\'s window cannot be shown from here; use the taskbar.')
            return show()
        pids = self.manager.window_pids()
        if not pids:
            raise ValueError('Baballonia is not running.')
        try:
            with x11_window.Display() as display:
                windows = display.find(pids, x11_window.BABALLONIA_CLASS)
                if not windows:
                    raise ValueError('Baballonia\'s window was not found: it may still be starting.')
                display.activate(windows[0])
        except OSError as error:
            raise ValueError('Baballonia\'s window cannot be shown from here (' + str(error)[:120] + '); use the '
                             'taskbar.') from error

    def close_baballonia(self):
        """Close Baballonia now (and the cameras); a running session keeps no eye tracking until Open."""
        with self.lock:
            self.owners.discard('user')
            if self.owners:
                self.closed = True
            self.kick()

    def camera_access(self, runner=system_run, pkexec='/usr/bin/pkexec'):
        allow_camera_access(runner, pkexec)
        self.log('Eye tracking: camera access allowed for the signed-in user (' + CAMERA_RULE_PATH + ').')
        with self.lock:
            self.camera_failed_at = -1e9
            self.kick()

    def camera_access_async(self, runner=system_run, pkexec='/usr/bin/pkexec'):
        """Allow eye camera access in the background: the password prompt may stay open for minutes and
        nothing else (Stop, Start, the pages) waits for it."""
        with self.lock:
            if self.access['state'] == 'asking':
                return False
            self.access = {'state': 'asking', 'message': 'Waiting for your password in the system prompt…'}

        def ask():
            try:
                self.camera_access(runner, pkexec)
                result = {'state': 'done', 'message': 'Camera access allowed.'}
            except Exception as error:  # reported on the page
                result = {'state': 'error', 'message': str(error)[:300]}
                self.log('Eye tracking: ' + result['message'])
            with self.lock:
                self.access = result
        threading.Thread(target=ask, daemon=True, name='VROSTBridge-camera-access').start()
        return True

    def kick(self):
        with self.lock:
            self.wake.set()
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name='VROSTBridge-eye-tracking')
                self.thread.start()

    def shutdown(self, timeout=12.0):
        """Companion closing: everything stops, a calibration and a download included."""
        with self.lock:
            self.closing = True
            self.owners.clear()
            thread = self.thread
            self.wake.set()
        manager = self.manager
        job = getattr(manager, 'job', None)
        if job is not None and job.is_alive():
            manager.cancel_install()
            job.join(10)
        if thread is not None:
            thread.join(timeout)
        try:
            self._teardown(force=True)
        except Exception as error:
            self.log('Eye tracking: stopping failed: ' + str(error)[:200])

    def recover(self):
        """At companion start: a Baballonia or camera helper an earlier companion left running."""
        try:
            with self.lock:
                if self.owners or self.thread is not None:
                    return
            if self.manager.installed() and self.manager.running():
                self.log('Eye tracking: stopping the Baballonia an earlier VROSTBridge left running.')
                self.manager.stop()
            # 1.0.0.3: a Baballonia an earlier installation left under another folder name is used, not downloaded.
            adopt = getattr(self.manager, 'adopt_earlier_copy', None)
            if adopt is not None and not self.manager.installed():
                adopt()
            if self.cameras.path().is_file():
                self.cameras.end_orphans()
        except Exception as error:
            self.log('Eye tracking: start-up check failed: ' + str(error)[:200])

    # ------------------------------------------------------------------ the worker
    def _run(self):
        last_error = None
        while True:
            self.wake.clear()
            pause = self.CHECK_S
            try:
                idle = self._step()
                last_error = None
            except Exception as error:  # never let eye tracking take the companion down
                idle, pause = False, self.CAMERA_RETRY_S
                text = str(error)[:300]
                self._set('error', 'Eye tracking stopped working: ' + text[:200])
                if text != last_error:  # the same failure every retry is logged once
                    self.log('Eye tracking: ' + text)
                last_error = text
            with self.lock:
                if idle and not self.owners and not self.wake.is_set():
                    self.thread = None
                    return
            self.wake.wait(pause)

    def _wanted(self):
        with self.lock:
            return bool(self.owners) and not self.closing

    def _set(self, phase, message=''):
        with self.lock:
            self.phase, self.message = phase, message

    def _relay_up(self):
        """Open the relay (once) and point it at the session's target. Raises OSError: also for what Windows' Python
        lacks (no AF_UNIX: an AttributeError) and for a target it cannot send to."""
        if self.relay is None:
            try:
                self.relay = self.relay_factory()
            except OSError as error:
                self._set('error', 'Port ' + str(OSC_PORT) + ' on this PC is in use by another program, so eye '
                          'tracking cannot reach VROSTBridge (' + str(error)[:80] + ').')
                raise
            except (AttributeError, ValueError) as error:
                self._set('error', 'The gaze relay cannot open its sockets on this PC (' + str(error)[:120] + ').')
                raise OSError('The gaze relay cannot open its sockets: ' + str(error)[:120]) from error
        try:
            self.relay.set_target(self.target)
        except (OSError, AttributeError, ValueError) as error:
            self._set('error', 'Eye tracking cannot reach the streaming client on this PC (' + str(error)[:120] + ').')
            if isinstance(error, OSError):
                raise
            raise OSError('Eye tracking cannot reach the streaming client: ' + str(error)[:120]) from error
        if getattr(self, 'session_gaps', False):
            self.session_gaps = False
            reset = getattr(self.relay, 'reset_gaps', None)
            if reset:
                reset()

    def _track_calibration(self, running):
        """Whether a calibration runs or has just ended (Baballonia saves the trained model's path 2 s
        after it finishes): nothing is stopped or restarted meanwhile."""
        calibrating = running and self._calibrating()
        now = time.monotonic()
        if calibrating:
            if not self.calibrating:
                self.calibration_model = self._model()
                self.log('Eye tracking: Baballonia is calibrating.')
            self.calibration_ended = None
        elif self.calibrating:
            self.calibration_ended = now
        self.calibrating = calibrating
        if self.calibration_ended is not None:
            saved = self._model() != self.calibration_model and self.manager.calibration().get('calibrated')
            if saved or now - self.calibration_ended >= self.CALIBRATION_SETTLE_S or not running:
                if saved:
                    self.log('Eye tracking: Baballonia saved its calibration.')
                self.calibration_ended = None
        return calibrating or self.calibration_ended is not None

    def _step(self):
        if not self._wanted():
            return self._wind_down()
        with self.lock:
            try:
                self._relay_up()
            except OSError:
                return False
            closed, user = self.closed, 'user' in self.owners
        manager = self.manager
        if not manager.installed():
            self.ours = self.running = False
            self._set('setup', 'Baballonia is not set up: Set up eye tracking downloads it (about ' +
                      str(round(manager.release['size'] / 1e6)) + ' MB).')
            return False
        running = manager.running()
        self.xr = xr_runtime_running() if running else None
        busy_calibrating = self._track_calibration(running)
        if self.ours and not running:  # its window was closed, or it ended by itself
            self.ours = False
            quick = self.started_at is not None and time.monotonic() - self.started_at < self.QUICK_EXIT_S
            self.running = False
            self.log('Eye tracking: Baballonia ' + ('ended right after starting.' if quick else 'closed.'))
            with self.lock:
                self.owners.discard('user')
                self.closed = bool(self.owners)
                closed = self.closed
            if not self._wanted():
                return self._wind_down()
            if quick:
                self._set('closed', 'Baballonia closed right after starting. If you closed it, choose Open Baballonia to '
                          'start it again; if another Baballonia is open, close that one first; otherwise send a report '
                          '(it includes Baballonia\'s log).')
                self._stop_cameras()
                return False
        if closed:
            self._stop_baballonia()
            self._stop_cameras()
            if self.phase != 'closed' or not self.message:
                self._set('closed', 'Baballonia was closed, so eye tracking stopped. Choose Open Baballonia to start it '
                          'again.')
            self.running = False
            return False
        calibrated = manager.calibration()['calibrated']
        if not calibrated and not user and not running and self.game_mode():
            # Calibration needs Baballonia's window: Desktop Mode. Here it would only cost a CPU core.
            self._set('calibrate', 'Baballonia is not calibrated yet. Calibrate it once in Desktop Mode: start a '
                      'session there, then choose the Eye calibration in Baballonia\'s window.')
            return False
        if running and not self.ours:
            if busy_calibrating:
                self.ours, self.started_at = True, time.monotonic()  # adopt it rather than interrupt
            else:
                # Started before this companion's relay listened: its OSC sender may have stopped for good.
                self.log('Eye tracking: restarting the Baballonia started before this VROSTBridge, so it sends here.')
                self._stop_baballonia(force=True)
                running = False
        if not running:
            others = manager.other_instances() if hasattr(manager, 'other_instances') else []
            if others:
                self._set('error', 'Another Baballonia is open (' + others[0][-120:] + '). Close it: VROSTBridge runs '
                          'its own, and only one Baballonia can run.')
                return False
        if running and self.reader_stale and not busy_calibrating:
            # Its camera reader died with the cameras during the calibration: start it again now.
            self.log('Eye tracking: restarting Baballonia after its calibration: the eye cameras restarted meanwhile.')
            self._stop_baballonia(force=True)
            running = False
        if not self.cameras.running():
            if running and not busy_calibrating:  # its camera reader never reconnects and spins once the stream ends
                self._stop_baballonia(force=True)
                running = False
            elif running:
                self.reader_stale = True  # kept for the calibration; restarted once it is saved
            camera = self.cameras.status()
            if camera['state'] == 'access' and self.camera_failed_at > 0:
                # Waits for Allow eye camera access (or Open Baballonia, or a new Start).
                self._set('cameras', camera['message'])
                return False
            if time.monotonic() - self.camera_failed_at < self.CAMERA_RETRY_S:
                return False
            self._set('starting', 'Starting the eye cameras…')
            try:
                self.cameras.start()
            except (OSError, ValueError) as error:
                self.camera_failed_at = time.monotonic()
                self._set('cameras', str(error)[:300])
                return False
            size = self.cameras.wait_frames(lambda: not self._wanted(), self.FRAMES_WAIT_S)
            if not self._wanted():
                return False
            if size is None:
                self.camera_failed_at = time.monotonic()
                state = self.cameras.status()
                message = state['message'] if state['state'] in ('access', 'no-device', 'failed') else (
                    'The eye cameras started but sent no picture. Reconnect the headset (or restart it), then '
                    'start again.')
                self._stop_cameras()
                self._set('cameras', message)
                return False
            self.frame_size = size
        if not running:
            self._set('starting', 'Starting Baballonia…')
            try:
                if _windows():  # the cameras' name as Windows gives it (Baballonia lists them by name)
                    manager.configure(self.frame_size, camera=self._camera_name())
                else:
                    manager.configure(self.frame_size)
            except (OSError, ValueError) as error:
                self.log('Eye tracking: Baballonia\'s settings were not updated: ' + str(error)[:200])
            # Windows: shown for Open Baballonia, else minimised without the focus (Linux: minimised once shown).
            started = manager.start(show='normal' if user else 'minimised') if _windows() else manager.start()
            self.ours, self.started_at, running = True, time.monotonic(), True
            self.reader_stale = False
            if started == 'started' and not user and not self.game_mode():
                self._minimise_when_shown()
        self.running = running
        if busy_calibrating:
            self._set('calibrating', self._calibrating_hint())
            return False
        stats = self.relay.status() if self.relay else {}
        if stats.get('state') == 'receiving':
            self.restarted_silent = False
            self._set('running', '' if calibrated else self._calibration_hint())
            return False
        quiet = min(time.monotonic() - (self.started_at or time.monotonic()), stats.get('idle_s') or 1e9)
        if quiet > self.SILENCE_S and time.monotonic() - self.last_restart > self.RESTART_SPACING_S:
            self.last_restart = time.monotonic()
            self.restarted_silent = True
            self.log('Eye tracking: no samples from Baballonia for ' + str(int(quiet)) + ' s; restarting it and the '
                     'eye cameras.')
            self._stop_baballonia(force=True)
            self._stop_cameras()
            self.wake.set()
            return False
        if quiet <= self.SILENCE_S:
            self._set('waiting', 'Baballonia is running; waiting for its first eye-tracking samples…')
        elif _windows():
            self._set('silent', 'Baballonia is running but sends no eye tracking' +
                      (', even after a restart' if self.restarted_silent else '') +
                      '. Open it with Show Baballonia and check that both eye pictures show. If Bigscreen\'s own '
                      'eye-tracking software is open, close it: only one program can use the eye cameras.')
        else:
            self._set('silent', 'Baballonia is running but sends no eye tracking' +
                      (', even after a restart' if self.restarted_silent else '') +
                      '. Open it in Desktop Mode and check that both eye pictures show.')
        return False

    def _camera_name(self):
        getter = getattr(self.cameras, 'camera_name', None)
        try:
            return getter() if getter else None
        except Exception:
            return None

    def _model(self):
        """Baballonia's eye model setting: each calibration saves a new path (a date alone can repeat)."""
        getter = getattr(self.manager, 'model_path', None)
        try:
            return getter() if getter else self.manager.calibration()
        except Exception:
            return None

    def _calibrating(self):
        checker = getattr(self.manager, 'calibrating', None)
        try:
            return bool(checker()) if checker else False
        except Exception:
            return False

    def _stop_baballonia(self, force=False):
        """Stop the Baballonia this companion runs (force: any from this install)."""
        with self.lock:
            self.minimise_generation += 1  # a window watcher for this Baballonia has nothing left to do
        if (self.ours or force) and self.manager.running():
            self.manager.stop()
        self.ours = False
        self.running = False

    def _stop_cameras(self):
        if self.cameras.running():
            self.cameras.stop()

    def _wind_down(self):
        """Nobody wants eye tracking: stop it all (a calibration may finish first). True when idle."""
        running = self.manager.running() if not self.closing else False
        if not self.closing and self._track_calibration(running):
            now = time.monotonic()
            with self.lock:
                self.wind_down_since = self.wind_down_since or now
                since = self.wind_down_since
            if now - since < self.CALIBRATION_GRACE_S:
                self._set('finishing', 'Baballonia is finishing its calibration; it closes when that is done.')
                return False
        return self._teardown()

    def _teardown(self, force=False):
        """Stop Baballonia and the cameras and close the relay, unless eye tracking is wanted again
        meanwhile (a Start during the 3-second stop): then the next check starts what is needed and the
        relay keeps its target. True when everything stopped."""
        if not force and self._wanted():
            return False
        with self.lock:
            self.minimise_generation += 1
        if self.manager.running():
            self.manager.stop()
        self.ours = self.running = self.calibrating = self.reader_stale = False
        self.calibration_ended = None
        if not force and self._wanted():
            return False
        self._stop_cameras()
        still_running = self.manager.running()  # asked before taking the lock: it may run systemctl
        with self.lock:
            if not force and self.owners:
                return False
            if self.relay is not None and not still_running:
                self.relay.close()
                self.relay = None
            self.closed = False
            self.restarted_silent = False
            self.wind_down_since = None
            self._rate = None
            if not self.owners:
                self.phase, self.message = 'off', ''
        return True

    # ------------------------------------------------------------------ status
    def _in_session(self):
        with self.lock:
            return 'session' in self.owners

    def _calibration_hint(self):
        """What to do while Baballonia runs uncalibrated (RC4.6). Its calibration shows in the headset only while
        SteamVR runs on this PC (Baballonia checks when a calibration starts), and VROSTBridge's launcher shows it
        as a SteamVR overlay over the stream (baballonia.CALIBRATION_LAUNCHER). It is meant for a session: in the
        first test, Baballonia's own way (an OpenXR scene application) made SteamVR end the session, and with
        SteamVR started by hand it showed each eye the other eye's picture."""
        start = 'Baballonia is not calibrated yet, so VROSTBridge does not follow your eyes. '
        here = '' if _windows() else ' in Desktop Mode'  # Windows has no Game Mode
        if self.xr is False:
            return start + ('SteamVR is not running on this PC, so a calibration started now would show on this '
                            'screen, not in the headset: start a session' + here + ' first, then choose the Eye '
                            'calibration in Baballonia\'s window.')
        if not self._in_session():
            return start + ('Calibrate during a session: start one' + here + ', then choose the Eye calibration '
                            'in Baballonia\'s window.')
        return start + ('Open its window with Show Baballonia, choose the Eye calibration (the first one) and follow '
                        'it in the headset; when it has finished, stop and start the session.')

    def _calibrating_hint(self):
        if self.xr is False:
            return ('Baballonia is calibrating on this screen, not in the headset: SteamVR is not running on this PC. '
                    'Let it finish or close it, start a session' + ('' if _windows() else ' in Desktop Mode') +
                    ', then choose the Eye calibration again.')
        return ('Baballonia is calibrating: follow it in the headset, where it shows over the stream; then it trains a '
                'model for your eyes for 5 to 10 minutes. Keep the session running until it has finished (stopping '
                'it ends SteamVR on this PC, which closes Baballonia), then stop and start the session.')

    def rate(self):
        """Eye-tracking samples a second reaching the relay (counted, never kept)."""
        relay = self.relay
        if relay is None:
            return None
        try:
            received = int(relay.status().get('received', 0))
        except Exception:
            return None
        now = time.monotonic()
        with self.lock:
            previous = self._rate
            if previous and now - previous[0] >= .8:
                value = round(max(0.0, (received - previous[1]) / (now - previous[0])), 1)
                self._rate = (now, received, value)
                return value
            if not previous:
                self._rate = (now, received, None)
            return previous[2] if previous else None

    def status(self):
        with self.lock:
            relay = self.relay
            value = {'phase': self.phase, 'message': self.message, 'owners': sorted(self.owners),
                     'closed': self.closed, 'calibrating': self.calibrating,
                     'frame_size': list(self.frame_size) if self.frame_size else None,
                     'access': dict(self.access),
                     'steamvr': self.xr}  # RC4.6: None unless Baballonia runs
        stats = relay.status() if relay is not None else {'state': 'off'}
        # 1.0.0.6: 'reach', how far the gaze went this session (gaze_provider.GazeReach; whole degrees only).
        value['relay'] = {key: stats.get(key) for key in ('state', 'forwarding', 'received', 'forwarded', 'invalid',
                                                          'unsent', 'other', 'idle_s', 'gaps', 'reach', 'centre')
                          if key in stats}
        if 'centre' not in value['relay'] and self.centre is not None:
            value['relay']['centre'] = self.centre.status()  # 1.0.0.8: also between sessions (the page's card)
        value['relay']['rate'] = self.rate()
        value['cameras'] = self.cameras.status()
        value['camera_access'] = value['cameras']['state'] == 'access'
        running = self.running if self.thread is not None else self.manager.running(max_age=5)
        value['baballonia'] = self.manager.status(running=running)
        value['game_mode'] = bool(self.game_mode())
        return value
