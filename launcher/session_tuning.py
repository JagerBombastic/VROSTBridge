"""Balance the headset PC's performance while a session streams (1.0.0.8, Linux).

In the 1.0.0.7 test the Bazzite headset PC's GPU stayed at 800 of its 2700 MHz for the
whole session while 54 % busy, with the chip held at a flat 20 W: each frame's decode and
frame pass took twice as long as the evening before, when the GPU had a manual clock
setting and ran at 880-1060 MHz. The opposite extreme is as bad: in the 1.0.0.3 test a
GPU fixed at its maximum took the whole power limit and held every processor core at
540 MHz, and eye tracking fell from 54 to 12 samples a second (launcher/power_balance.py).

SessionTuning steers between the two, only while a session streams video, and only
through steamos-manager's D-Bus interface on the session bus (Bazzite, SteamOS and the
systems that use it for Steam's Performance menu); nothing else is touched:

- On the charger, the power limit (TDP) goes to its maximum for the session.
- When frames run late while the GPU sits at a low clock (at most half its maximum) and
  is busy, the GPU clock is held higher (steamos-manager's manual GPU clock), first at
  45 % of its maximum and then 200 MHz at a time, up to 75 %. It never rises while the
  processor is being held below its own minimum clock or eye tracking has slowed, and
  steps back down when either happens: the processor keeps its share of the power.
- Everything changed is set back when the session stops, and at the next start after a
  crash (a receipt in the state folder), unless it was changed elsewhere meanwhile (Steam's
  Performance menu, the desktop's TDP control): a setting changed elsewhere during the
  session is left alone from then on.

A PC without steamos-manager, or one that does not offer a setting, simply streams as
before. The only inputs are the PC's own reported clocks, power and the stream's timing.

1.0.0.12: each session used to find the clock afresh. In the 1.0.0.11 test the GPU sat at
800 MHz for the first four and a half minutes, while frames ran late and the bitrate
stepped down, before it was held at 1300 MHz. The clock a session ends up holding is now
remembered for this PC (MEMORY_NAME in the state folder, with the GPU's maximum clock and
whether it was on the charger) and held as soon as the next session's video is up, when
the GPU's level is automatic. A session that has to set it back down (the processor or
eye tracking squeezed), or in which it is changed elsewhere, forgets it. Frames that never
arrived (the rendering PC sent nothing for a moment) no longer count as frames running
late here: no GPU clock on this PC would have shown them.
"""
from collections import deque
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run
from launcher.power_balance import CpuClocks, GPU_INTERFACE, OBJECT, SERVICE, TDP_INTERFACE
from launcher.stream_budget import SOURCE_MISSING, frames_missing

RECEIPT_NAME = 'session-tuning.json'
MEMORY_NAME = 'gpu-clock.json'  # 1.0.0.12: the clock the last session needed held, for the next session's start
LOW_CLOCK_SHARE = 0.5     # the GPU counts as held low at or below this share of its maximum clock...
BUSY_MIN = 35             # ...while at least this busy (percent)
FIRST_CLOCK_SHARE = 0.45  # the first held clock, as a share of the maximum (rounded up to 100 MHz)
STEP_MHZ = 200
MAX_CLOCK_SHARE = 0.75    # never held above this share of the maximum
RAISE_AFTER = 2           # performance samples (5 s apart) in a row with late frames and a low GPU
LOWER_AFTER = 2           # samples in a row with the processor held back, or eye tracking slowed
LATE_WINDOW_S = 10        # seconds of TIMING considered for "frames run late"
LATE_SECONDS = 3          # late seconds in that window that count
GAZE_DROP_SHARE = 0.7     # eye tracking below this share of its rate when the session settled has slowed
LATE_CADENCES = ('late_or_paired', 'client_throttled')


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _round_up(value, step=100):
    return int(-(-int(value) // step) * step)


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def on_charger(sample):
    """True on external power, False on battery, None when the performance sample does not say."""
    supplies = [row for row in (sample or {}).get('power_supplies') or [] if isinstance(row, dict)]
    mains = [row.get('online') for row in supplies if row.get('type') in ('Mains', 'USB', 'USB_C', 'USB_PD')]
    if any(value for value in mains if isinstance(value, (int, float))):
        return True
    if any(row.get('type') == 'Battery' for row in supplies):
        return False
    return None


class SessionTuning:
    """steamos-manager's GPU clock and power limit during a streaming session (see the module notes)."""

    def __init__(self, runner=system_run, log=print, busctl=None, state_directory=None, clock=time.monotonic):
        self.runner, self.log, self.clock = runner, log, clock
        self.busctl = _tool('busctl', ('/usr/bin/busctl', '/bin/busctl')) if busctl is None else busctl
        self.state_directory = Path(state_directory) if state_directory is not None else None
        self.lock = threading.RLock()
        self.cpu = CpuClocks()
        self.late = deque(maxlen=LATE_WINDOW_S)
        self.previous_counters = None
        self._reset_session()
        self.value = {'state': 'idle', 'message': ''}
        # 1.0.0.13: what the last session did with the GPU clock, kept after it ends, for the reports.
        self.last_session = None

    def _reset_session(self):
        self.active = False
        self.original = {}      # what the session found: gpu_level, manual_clock_mhz, tdp_w
        self.applied = {}       # what this session set and still owns
        self.limits = {}
        self.raise_count = self.lower_count = 0
        self.raises = 0         # 1.0.0.12: clocks raised this session (the bitrate's fresh start follows each)
        self.charger = None
        self.first_clock = None
        self.gaze_baseline = None
        self.late.clear()
        self.previous_counters = None
        self.cpu.reset()

    # ------------------------------------------------------------------ steamos-manager
    def _run(self, arguments, timeout=5):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', str(error)[:160])

    def _get(self, interface, name):
        if not self.busctl:
            return None
        result = self._run([self.busctl, '--user', '--json=short', 'get-property', SERVICE, OBJECT, interface, name])
        if result.returncode != 0:
            return None
        try:
            document = json.loads(result.stdout or '')
        except ValueError:
            return None
        return document.get('data') if isinstance(document, dict) else None

    def _put(self, interface, name, signature, value):
        """Set one property; '' when it worked, else what went wrong."""
        if not self.busctl:
            return 'busctl is not available on this PC'
        result = self._run([self.busctl, '--user', 'set-property', SERVICE, OBJECT, interface, name, signature,
                            str(value)], timeout=8)
        if result.returncode != 0:
            lines = ((result.stderr or '') or (result.stdout or '')).strip().splitlines()
            return (lines[-1].strip() if lines else 'exit code %s' % result.returncode)[:160]
        return ''

    def read(self):
        """steamos-manager's settings now: {'available', 'gpu_level', 'manual_clock_mhz', 'clock_min_mhz',
        'clock_max_mhz', 'tdp_w', 'tdp_min_w', 'tdp_max_w'} (missing keys: not offered)."""
        if sys.platform == 'win32':
            return {'available': False}
        level = self._get(GPU_INTERFACE, 'GpuPerformanceLevel')
        value = {'available': False}
        if isinstance(level, str) and level:
            value.update(available=True, gpu_level=level)
            for name, key in (('ManualGpuClock', 'manual_clock_mhz'), ('ManualGpuClockMin', 'clock_min_mhz'),
                              ('ManualGpuClockMax', 'clock_max_mhz')):
                number = _int(self._get(GPU_INTERFACE, name))
                if number is not None and 0 < number < 100000:
                    value[key] = number
        tdp = _int(self._get(TDP_INTERFACE, 'TdpLimit'))
        if tdp is not None and 0 < tdp < 1000:
            value.update(available=True, tdp_w=tdp)
            for name, key in (('TdpLimitMin', 'tdp_min_w'), ('TdpLimitMax', 'tdp_max_w')):
                number = _int(self._get(TDP_INTERFACE, name))
                if number is not None and 0 < number < 1000:
                    value[key] = number
        return value

    # ------------------------------------------------------------------ receipt (crash recovery)
    def _receipt_path(self):
        return (self.state_directory / RECEIPT_NAME) if self.state_directory else None

    def _write_receipt(self):
        path = self._receipt_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name('.' + path.name + '.tmp')
            temporary.write_text(json.dumps({'version': 1, 'original': self.original, 'applied': self.applied}) + '\n',
                                 encoding='utf-8')
            os.replace(temporary, path)
        except OSError as error:
            self.log('PERFORMANCE receipt not saved: ' + str(error)[:160])

    def _forget_receipt(self):
        path = self._receipt_path()
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                self.log('PERFORMANCE receipt not removed: ' + str(error)[:160])

    def _read_receipt(self):
        path = self._receipt_path()
        try:
            value = json.loads(path.read_text(encoding='utf-8')) if path and path.is_file() else None
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get('original'), dict) \
                or not isinstance(value.get('applied'), dict):
            return None
        return value

    # ------------------------------------------------------------------ the clock the last session needed (1.0.0.12)
    def _memory_path(self):
        return (self.state_directory / MEMORY_NAME) if self.state_directory else None

    def _remembered(self):
        """The clock to hold from the start: the last session's, found on this GPU (the same maximum clock) on the
        same power source, and within what a session may hold now; else None."""
        path = self._memory_path()
        try:
            value = json.loads(path.read_text(encoding='utf-8')) if path and path.is_file() else None
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        clock, maximum = _int(value.get('clock_mhz')), _int(value.get('clock_max_mhz'))
        if not clock or not maximum or maximum != self.limits.get('clock_max_mhz') or value.get('charger') != self.charger:
            return None
        if clock > int(MAX_CLOCK_SHARE * maximum) or clock < (self.limits.get('clock_min_mhz') or 0):
            return None
        return clock

    def _remember(self, clock):
        path = self._memory_path()
        if path is None or not self.limits.get('clock_max_mhz'):
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name('.' + path.name + '.tmp')
            temporary.write_text(json.dumps({'version': 1, 'clock_mhz': int(clock),
                                             'clock_max_mhz': self.limits['clock_max_mhz'],
                                             'charger': self.charger}) + '\n', encoding='utf-8')
            os.replace(temporary, path)
        except OSError as error:
            self.log('PERFORMANCE GPU clock not remembered: ' + str(error)[:160])

    def _forget_clock(self):
        path = self._memory_path()
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                self.log('PERFORMANCE remembered GPU clock not removed: ' + str(error)[:160])

    def _hold(self, target):
        """Hold the GPU at target MHz (steamos-manager's manual level); '' when it worked, else what went wrong."""
        if self.original.get('gpu_level') != 'manual' and 'gpu_level' not in self.applied:
            problem = self._put(GPU_INTERFACE, 'GpuPerformanceLevel', 's', 'manual')
            if problem:
                return problem
            self.applied['gpu_level'] = 'manual'
        problem = self._put(GPU_INTERFACE, 'ManualGpuClock', 'u', target)
        if problem:
            self._restore_level()
            return problem
        self.applied['manual_clock_mhz'] = target
        if self.first_clock is None:
            self.first_clock = target
        self._write_receipt()
        return ''

    # ------------------------------------------------------------------ session
    def status(self):
        with self.lock:
            return dict(self.value, applied=dict(self.applied), active=self.active,
                        remembered=self.remembered_record(),
                        last_session=dict(self.last_session) if self.last_session else None)

    def remembered_record(self):
        """1.0.0.13: the clock saved for the next session's start (MEMORY_NAME), as saved; None without one."""
        path = self._memory_path()
        try:
            value = json.loads(path.read_text(encoding='utf-8')) if path and path.is_file() else None
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        return {key: value.get(key) for key in ('clock_mhz', 'clock_max_mhz', 'charger')}

    def _note_clock(self, clock, set_back=False):
        if self.last_session is not None:
            self.last_session.update(clock_mhz=clock, raises=self.raises)
            if set_back:
                self.last_session['set_back'] = True

    def _say(self, state, message):
        self.value = {'state': state, 'message': message}
        if message:
            self.log('PERFORMANCE ' + state + ': ' + message)

    def begin(self, sample=None):
        """The session's video is up: take note of the settings and raise the power limit on the charger."""
        with self.lock:
            if self.active:
                return self.status()
            self._reset_session()
            self.last_session = None
            current = self.read()
            if not current.get('available'):
                self.value = {'state': 'unavailable', 'message': 'This PC has no steamos-manager performance settings, so '
                              'the session uses the system\'s own GPU clock and power limit.'}
                return self.status()
            self.active = True
            self.original = {key: current[key] for key in ('gpu_level', 'manual_clock_mhz', 'tdp_w') if key in current}
            self.limits = {key: current[key] for key in ('clock_min_mhz', 'clock_max_mhz', 'tdp_min_w', 'tdp_max_w')
                           if key in current}
            notes = []
            charger = self.charger = on_charger(sample)
            tdp, highest = current.get('tdp_w'), current.get('tdp_max_w')
            if tdp is not None and highest and tdp < highest:
                if charger:
                    problem = self._put(TDP_INTERFACE, 'TdpLimit', 'u', highest)
                    if problem:
                        notes.append('the power limit could not be raised (' + problem + ')')
                    else:
                        self.applied['tdp_w'] = highest
                        notes.append('power limit ' + str(tdp) + ' -> ' + str(highest) + ' W while on the charger')
                elif charger is False:
                    notes.append('on battery the power limit stays at ' + str(tdp) + ' W')
            elif tdp is not None:
                notes.append('power limit ' + str(tdp) + ' W (its maximum)')
            # 1.0.0.12: the clock the last session needed, at once, unless the GPU's level was set by hand.
            remembered = self._remembered() if current.get('gpu_level') == 'auto' else None
            if remembered:
                problem = self._hold(remembered)
                notes.append(('GPU held at ' + str(remembered) + ' MHz from the start, as the last session needed')
                             if not problem else 'the GPU clock could not be held (' + problem + ')')
            self.last_session = {'held_from_start': bool(remembered) and 'manual_clock_mhz' in self.applied,
                                 'clock_mhz': self.applied.get('manual_clock_mhz'), 'raises': 0, 'set_back': False}
            if self.applied:
                self._write_receipt()
            self._say('active', 'Balancing this PC for the session: ' + ('; '.join(notes) if notes else
                                 'watching the GPU clock') + '.')
            return self.status()

    def note_timing(self, timings):
        """One TIMING line (each streaming second): whether frames ran late this second."""
        if not isinstance(timings, dict):
            return
        cadence = timings.get('cadence')
        if cadence not in ('full',) + LATE_CADENCES + ('source_limited',):
            self.previous_counters = None
            return
        counters = {}
        for name in ('late_frame_timeouts', 'fused_skipped'):
            try:
                counters[name] = int(float(timings.get(name)))
            except (TypeError, ValueError):
                counters[name] = None
        previous = self.previous_counters or {}

        def delta(name):
            now, before = counters.get(name), previous.get(name)
            return now - before if now is not None and before is not None and now >= before else 0
        # 1.0.0.12: frames that never arrived explain that second's late frames, and no clock here shows them; a
        # throttled compositor or a frame this PC could not show still counts.
        missing = frames_missing(timings) >= SOURCE_MISSING
        late = (cadence == 'client_throttled' or delta('fused_skipped') >= 1 or
                (not missing and (cadence in LATE_CADENCES or delta('late_frame_timeouts') >= 3)))
        self.previous_counters = counters
        with self.lock:
            self.late.append(bool(late))

    def observe(self, sample, gaze_rate=None):
        """One performance sample (every 5 s while the session runs): raise or lower the held GPU clock."""
        with self.lock:
            if not self.active or not self.cpu.observe(sample):
                return None
            row = self.cpu.samples[-1]
            if gaze_rate and gaze_rate > 0 and self.gaze_baseline is None and len(self.cpu.samples) >= 3:
                self.gaze_baseline = float(gaze_rate)  # eye tracking's own rate once the session settled
            changed_elsewhere = self._check_elsewhere()
            if changed_elsewhere:
                return None
            gpu, maximum, busy = row.get('gpu_mhz'), row.get('gpu_max_mhz') or self.limits.get('clock_max_mhz'), \
                row.get('gpu_busy')
            held_cpu = CpuClocks.held(row)
            gaze_slow = bool(gaze_rate and self.gaze_baseline and gaze_rate < GAZE_DROP_SHARE * self.gaze_baseline)
            late = sum(self.late) >= LATE_SECONDS
            clock = self.applied.get('manual_clock_mhz')
            # Step back: the processor is being squeezed (or eye tracking slowed) while this session holds the GPU up.
            if clock and (held_cpu or gaze_slow):
                self.lower_count += 1
                self.raise_count = 0
                if self.lower_count >= LOWER_AFTER:
                    self.lower_count = 0
                    return self._lower(clock, 'the processor is held back' if held_cpu else 'eye tracking slowed down')
                return None
            self.lower_count = 0
            low = bool(gpu and maximum and gpu <= LOW_CLOCK_SHARE * maximum and busy is not None and busy >= BUSY_MIN)
            if late and (low or (clock and busy is not None and busy >= BUSY_MIN)) and not held_cpu and not gaze_slow:
                self.raise_count += 1
                if self.raise_count >= RAISE_AFTER:
                    self.raise_count = 0
                    return self._raise(gpu, maximum)
            else:
                self.raise_count = 0
            return None

    def _check_elsewhere(self):
        """A setting this session owns that was changed elsewhere is left alone from then on."""
        if not self.applied:
            return False
        current = self.read()
        dropped = []
        if 'manual_clock_mhz' in self.applied and (current.get('gpu_level') != 'manual'
                                                   or current.get('manual_clock_mhz') != self.applied['manual_clock_mhz']):
            self.applied.pop('manual_clock_mhz', None)
            self.applied.pop('gpu_level', None)
            self._forget_clock()  # 1.0.0.12: set by hand now; not ours to bring back next time
            dropped.append('GPU clock')
        if 'tdp_w' in self.applied and current.get('tdp_w') != self.applied['tdp_w']:
            self.applied.pop('tdp_w', None)
            dropped.append('power limit')
        if dropped:
            self._write_receipt() if self.applied else self._forget_receipt()
            self._say('active', 'The ' + ' and '.join(dropped) + ' changed elsewhere; VROSTBridge leaves '
                      + ('it' if len(dropped) == 1 else 'them') + ' as set there for this session.')
            return True
        return False

    def _raise(self, gpu, maximum):
        maximum = maximum or self.limits.get('clock_max_mhz')
        if not maximum:
            return None
        cap = int(MAX_CLOCK_SHARE * maximum) // 100 * 100
        current = self.applied.get('manual_clock_mhz')
        target = (_round_up(max(FIRST_CLOCK_SHARE * maximum, (gpu or 0) + STEP_MHZ)) if current is None
                  else current + STEP_MHZ)
        low = self.limits.get('clock_min_mhz')
        target = min(cap, max(target, low or 0))
        if current is not None and target <= current:
            return None
        problem = self._hold(target)
        if problem:
            self._say('active', 'The GPU clock could not be held higher (' + problem + ').')
            return None
        self.raises += 1
        self._remember(target)
        self._note_clock(target)
        self._say('active', 'Frames were running late with the GPU at ' + (str(int(gpu)) + ' MHz' if gpu else 'a low clock')
                  + ': GPU held at ' + str(target) + ' MHz for the rest of the session.')
        return target

    def _lower(self, clock, reason):
        target = clock - STEP_MHZ
        if self.first_clock is None or target < self.first_clock:
            self._restore_clock()
            self._forget_clock()  # 1.0.0.12: this PC needed it lower after all; the next session finds it afresh
            self._note_clock(None, set_back=True)
            self._say('active', 'GPU clock set back to ' + str(self.original.get('gpu_level', 'automatic')) + ' (' +
                      reason + ').')
            return 0
        problem = self._put(GPU_INTERFACE, 'ManualGpuClock', 'u', target)
        if problem:
            return None
        self.applied['manual_clock_mhz'] = target
        self._remember(target)
        self._note_clock(target)
        self._write_receipt()
        self._say('active', 'GPU held at ' + str(target) + ' MHz (' + reason + ').')
        return target

    def _restore_level(self):
        level = self.original.get('gpu_level')
        if 'gpu_level' in self.applied and level:
            self._put(GPU_INTERFACE, 'GpuPerformanceLevel', 's', level)
        self.applied.pop('gpu_level', None)

    def _restore_clock(self):
        if 'manual_clock_mhz' in self.applied:
            if self.original.get('gpu_level') == 'manual' and self.original.get('manual_clock_mhz'):
                self._put(GPU_INTERFACE, 'ManualGpuClock', 'u', self.original['manual_clock_mhz'])
            self.applied.pop('manual_clock_mhz', None)
        self._restore_level()
        self._write_receipt() if self.applied else self._forget_receipt()

    def end(self):
        """The session stopped: set back what it changed (unless changed elsewhere meanwhile)."""
        with self.lock:
            if not self.active:
                return self.status()
            restored = self._restore(self.original, self.applied)
            self._forget_receipt()
            self._reset_session()
            self._say('idle', ('Performance settings set back: ' + ', '.join(restored) + '.') if restored else '')
            return self.status()

    def _restore(self, original, applied):
        current = self.read() if applied else {}
        restored = []
        if 'manual_clock_mhz' in applied and current.get('gpu_level') == 'manual' \
                and current.get('manual_clock_mhz') == applied['manual_clock_mhz']:
            if original.get('gpu_level') == 'manual' and original.get('manual_clock_mhz'):
                if not self._put(GPU_INTERFACE, 'ManualGpuClock', 'u', original['manual_clock_mhz']):
                    restored.append('GPU clock ' + str(original['manual_clock_mhz']) + ' MHz')
            elif original.get('gpu_level') and not self._put(GPU_INTERFACE, 'GpuPerformanceLevel', 's',
                                                             original['gpu_level']):
                restored.append('GPU clock ' + ('automatic' if original['gpu_level'] == 'auto' else original['gpu_level']))
        if 'tdp_w' in applied and current.get('tdp_w') == applied['tdp_w'] and original.get('tdp_w'):
            if not self._put(TDP_INTERFACE, 'TdpLimit', 'u', original['tdp_w']):
                restored.append('power limit ' + str(original['tdp_w']) + ' W')
        return restored

    def recover(self):
        """At startup: set back what a session left changed when the companion did not stop normally."""
        with self.lock:
            if self.active:
                return self.status()
            receipt = self._read_receipt()
            if not receipt:
                return self.status()
            restored = self._restore(receipt['original'], receipt['applied'])
            self._forget_receipt()
            if restored:
                self._say('idle', 'Set back after an unexpected stop: ' + ', '.join(restored) + '.')
            return self.status()
