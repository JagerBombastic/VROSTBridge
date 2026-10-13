"""The headset PC's shared power limit during a session (1.0.0.4).

Where the processor and the GPU are one chip (handhelds, most laptops, mini PCs with integrated
graphics), they share one power limit (TDP). In the 1.0.0.3 test the GPU
clock was fixed at its maximum (2700 MHz on the tested integrated Radeon graphics) with the limit at 28 W: the
GPU then took nearly all of the power and every processor core was held at about
540 MHz, below its own 1100 MHz minimum, for the whole session. Eye tracking (Baballonia
runs its model on the processor) fell from 54 to 12 samples a second, so the sharp
region kept losing the gaze, and the stream's own work stalled for 40-60 ms every few
seconds. The session before, with the GPU clock automatic but a lower limit, the
opposite happened: the GPU sat at 800 MHz while busy (graphics_limit.GpuClocks.starved).

CpuClocks keeps the last performance samples (launcher/performance_monitor.py, every
5 s) and says when the processor is held far below its minimum, and whether a fixed GPU
clock is the likely cause. SteamOSPower reads, and on request changes, the GPU clock
setting through steamos-manager's D-Bus interface on the session bus, where a PC has it
(SteamOS and the distributions that use it for Steam's Performance menu and their desktop
TDP controls). Nothing is changed without the person choosing it, and every threshold is
relative to the PC's own reported clocks.

1.0.0.7, Windows: Windows reports no minimum clock. There the processor counts as held
back when, while it is busy (WINDOWS_BUSY, all processors), its clock while working
('cpu_effective_mhz' from the performance monitor, else Windows' CurrentMhz) or Windows'
own limit on it (MhzLimit) is at or below WINDOWS_HELD_SHARE of its base clock (MaxMhz):
again relative to the PC's own figures. A fixed GPU clock cannot be seen on Windows.
steamos-manager does not exist there, so SteamOSPower reports unavailable and the advice
names Windows' power mode, the charger and the maker's own tool instead of Game Mode.
"""
from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run
from launcher.windows_power import POWER_MODE_STEP, VENDOR_TOOL

# Held back: the processor's median clock below this share of its own minimum clock (scaling_min_freq). The
# system never asks for less than that minimum, so running below it means the firmware is holding the processor
# back (its power or thermal limit), on any processor; a processor that is merely idle stays at or above it.
STARVED_SHARE = .75
STARVED_SAMPLES = 2     # in this many consecutive samples (5 s apart)
FIXED_SHARE = .9        # a GPU clock at this share of its maximum or more while not busy looks fixed
FIXED_BUSY = 60
SAMPLES = 6
# Windows (1.0.0.7): held back at or below this share of the processor's base clock (MaxMhz)...
WINDOWS_HELD_SHARE = .4
WINDOWS_BUSY = 20       # ...while the processors are at least this busy (percent of all of them)

SERVICE = 'com.steampowered.SteamOSManager1'
OBJECT = '/com/steampowered/SteamOSManager1'
GPU_INTERFACE = SERVICE + '.GpuPerformanceLevel1'
TDP_INTERFACE = SERVICE + '.TdpLimit1'
PROPERTIES = ((GPU_INTERFACE, 'GpuPerformanceLevel', 'gpu_level'), (GPU_INTERFACE, 'ManualGpuClock', 'manual_clock_mhz'),
              (GPU_INTERFACE, 'ManualGpuClockMin', 'manual_clock_min_mhz'),
              (GPU_INTERFACE, 'ManualGpuClockMax', 'manual_clock_max_mhz'),
              (TDP_INTERFACE, 'TdpLimit', 'tdp_w'), (TDP_INTERFACE, 'TdpLimitMin', 'tdp_min_w'),
              (TDP_INTERFACE, 'TdpLimitMax', 'tdp_max_w'))
_LEVEL = re.compile(r'[a-z][a-z_]{0,31}')
READ_EVERY_S = 30.0


def _positive(value, high=100000):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < high


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


class CpuClocks:
    """The processor's and the GPU's recent clocks from the performance monitor's samples."""

    def __init__(self, size=SAMPLES):
        self.samples = deque(maxlen=size)
        self.last = None

    def reset(self):
        self.samples.clear()
        self.last = None

    def observe(self, sample):
        """One performance-monitor sample (read_performance plus 'observed_monotonic'); repeats are ignored.
        True when it added a row."""
        if not isinstance(sample, dict) or not sample.get('active') or sample.get('observed_monotonic') == self.last:
            return False
        self.last = sample.get('observed_monotonic')
        policies = [p for p in sample.get('cpu_policies') or [] if isinstance(p, dict)]
        clocks = [p['reported_frequency_mhz'] for p in policies if _positive(p.get('reported_frequency_mhz'))]
        minimums = [p['minimum_mhz'] for p in policies if _positive(p.get('minimum_mhz'))]
        maximums = [p['maximum_mhz'] for p in policies if _positive(p.get('maximum_mhz'))]
        windows = sys.platform == 'win32'
        if windows and _positive(sample.get('cpu_effective_mhz')):
            clocks = [float(sample['cpu_effective_mhz'])]  # the clock while working; CurrentMhz often stays at base
        if not clocks:
            return False
        row = {'cpu_mhz': _median(clocks), 'cpu_min_mhz': _median(minimums), 'cpu_max_mhz': _median(maximums)}
        if windows:
            busy = sample.get('cpu_busy_percent')
            busy = float(busy) if isinstance(busy, (int, float)) and not isinstance(busy, bool) and 0 <= busy <= 100 \
                else None
            row.update(basis='windows', cpu_busy=busy,
                       cpu_limit_mhz=_median([p['limit_mhz'] for p in policies if _positive(p.get('limit_mhz'))]))
        best = None
        for gpu in sample.get('gpus') or []:
            if not isinstance(gpu, dict) or not isinstance(gpu.get('gpu_busy_percent'), (int, float)):
                continue
            if best is None or gpu['gpu_busy_percent'] > best['gpu_busy_percent']:
                best = gpu  # the GPU doing the work
        if best:
            sensors = [s for s in best.get('sensors') or [] if isinstance(s, dict)]
            clock = next((s.get('clock_mhz') for s in sensors if _positive(s.get('clock_mhz'))), None)
            power = next((s.get('average_power_w') for s in sensors if _positive(s.get('average_power_w'), 2000)), None)
            level = best.get('performance_level')
            row.update(gpu_busy=float(best['gpu_busy_percent']),
                       gpu_mhz=float(clock if clock is not None else best.get('selected_sclk_mhz') or 0) or None,
                       gpu_max_mhz=best.get('max_sclk_mhz') if _positive(best.get('max_sclk_mhz')) else None,
                       gpu_level=level if isinstance(level, str) and _LEVEL.fullmatch(level) else None,
                       power_w=float(power) if power is not None else None)
        self.samples.append(row)
        return True

    def latest(self):
        """The latest processor clock for the readout: {'cpu_mhz', 'cpu_min_mhz'}, or None. Windows rows add
        'cpu_max_mhz' (the base clock; Windows reports no minimum)."""
        if not self.samples:
            return None
        row = self.samples[-1]
        result = {'cpu_mhz': int(round(row['cpu_mhz'])),
                  'cpu_min_mhz': int(round(row['cpu_min_mhz'])) if row.get('cpu_min_mhz') else None}
        if row.get('basis') == 'windows':
            result['cpu_max_mhz'] = int(round(row['cpu_max_mhz'])) if row.get('cpu_max_mhz') else None
        return result

    @staticmethod
    def held(row):
        if row.get('basis') == 'windows':
            return _windows_held(row) is not None
        minimum = row.get('cpu_min_mhz')
        return bool(minimum) and row['cpu_mhz'] < STARVED_SHARE * minimum

    def starved(self):
        """While the processor is held far below its minimum: {'cpu_mhz', 'cpu_min_mhz', 'cause', 'gpu_mhz',
        'gpu_max_mhz', 'gpu_busy', 'gpu_level', 'power_w'}; cause 'fixed-gpu-clock' when the GPU runs at a fixed or
        near-maximum clock without being busy, else 'power-limit'. None otherwise."""
        recent = list(self.samples)[-STARVED_SAMPLES:]
        if len(recent) < STARVED_SAMPLES or not all(self.held(row) for row in recent):
            return None
        row = recent[-1]
        fixed = any(r.get('gpu_level') == 'manual' or (
            r.get('gpu_mhz') and r.get('gpu_max_mhz') and r['gpu_mhz'] >= FIXED_SHARE * r['gpu_max_mhz']
            and (r.get('gpu_busy') or 0) < FIXED_BUSY) for r in recent)
        result = {'cpu_mhz': int(round(_median([r['cpu_mhz'] for r in recent]))),
                  'cpu_min_mhz': int(round(row['cpu_min_mhz'])) if row.get('cpu_min_mhz') else None,
                  'cause': 'fixed-gpu-clock' if fixed else 'power-limit'}
        if row.get('basis') == 'windows':
            if not fixed and _windows_held(row) == 'limit':
                result['cause'] = 'processor-limit'  # Windows itself caps the clock (heat or a power setting)
            for key in ('cpu_max_mhz', 'cpu_limit_mhz', 'cpu_busy'):
                if row.get(key) is not None:
                    result[key] = int(round(row[key]))
        for key in ('gpu_mhz', 'gpu_max_mhz', 'gpu_busy', 'power_w'):
            if row.get(key) is not None:
                result[key] = int(round(row[key]))
        if row.get('gpu_level'):
            result['gpu_level'] = row['gpu_level']
        return result


def _windows_held(row):
    """Windows rows: 'limit' while Windows caps the clock far below the base clock, 'clock' while the clock itself
    is that low, each only while the processors are busy; else None."""
    maximum, busy = row.get('cpu_max_mhz'), row.get('cpu_busy')
    if not maximum or busy is None or busy < WINDOWS_BUSY:
        return None
    limit = row.get('cpu_limit_mhz')
    if limit and limit <= WINDOWS_HELD_SHARE * maximum:
        return 'limit'
    if row['cpu_mhz'] <= WINDOWS_HELD_SHARE * maximum:
        return 'clock'
    return None


def gpu_clock_hint():
    """Where a fixed GPU clock is set back to automatic by hand on this platform, for messages."""
    if sys.platform == 'win32':
        return 'in ' + VENDOR_TOOL + '.'
    return "in Steam's Game Mode, Quick Access menu > Performance; on the desktop, your power or TDP control."


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


class SteamOSPower:
    """steamos-manager's GPU clock and TDP settings for this desktop user (read-only unless asked)."""

    def __init__(self, runner=system_run, busctl=None, clock=time.monotonic):
        self.runner, self.clock = runner, clock
        self.busctl = _tool('busctl', ('/usr/bin/busctl', '/bin/busctl')) if busctl is None else busctl
        self.lock = threading.Lock()
        self.value, self.read_at = {'available': False}, None

    def _run(self, arguments, timeout=3):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', str(error)[:160])

    def _get(self, interface, name):
        result = self._run([self.busctl, '--user', '--json=short', 'get-property', SERVICE, OBJECT, interface, name])
        if result.returncode != 0:
            return None
        try:
            document = json.loads(result.stdout or '')
        except ValueError:
            return None
        return document.get('data') if isinstance(document, dict) else None

    def read(self, force=False):
        """{'available', 'gpu_level', 'manual_clock_mhz', ..., 'tdp_w', ...}; cached for READ_EVERY_S.
        Windows: {'available': False, 'reason'} without running anything."""
        if sys.platform == 'win32':
            return {'available': False, 'reason': 'steamos-manager is not available on Windows'}
        with self.lock:
            if not force and self.read_at is not None and self.clock() - self.read_at < READ_EVERY_S:
                return dict(self.value)
        value = {'available': False}
        if self.busctl:
            for interface, name, key in PROPERTIES:
                data = self._get(interface, name)
                if key == 'gpu_level':
                    if not (isinstance(data, str) and _LEVEL.fullmatch(data)):
                        break  # no steamos-manager on this PC (or not for this user)
                    value.update(available=True, gpu_level=data)
                elif isinstance(data, int) and not isinstance(data, bool) and 0 <= data <= 100000:
                    value[key] = data
        with self.lock:
            self.value, self.read_at = value, self.clock()
            return dict(value)

    def set_gpu_automatic(self):
        """Set the GPU clock to automatic (the person chose it). Returns '' or what went wrong."""
        if sys.platform == 'win32':
            return 'steamos-manager is not available on Windows'
        if not self.busctl:
            return 'busctl is not available on this PC'
        result = self._run([self.busctl, '--user', 'set-property', SERVICE, OBJECT, GPU_INTERFACE,
                            'GpuPerformanceLevel', 's', 'auto'], timeout=5)
        if result.returncode != 0:
            lines = ((result.stderr or '') or (result.stdout or '')).strip().splitlines()
            return (lines[-1].strip() if lines else 'exit code %s' % result.returncode)[:160]
        level = self.read(force=True).get('gpu_level')
        return '' if level == 'auto' else 'the GPU clock setting is still ' + str(level or 'unknown')


def advice(starved, steamos=None):
    """Plain-language status for the pages, Game Mode and the headset; state 'ok' when nothing needs saying.

    steamos: SteamOSPower.read(); offers the 'gpu-auto' action while steamos-manager reports a manual GPU clock.
    """
    if not starved:
        return {'state': 'ok', 'message': '', 'short': '', 'actions': [], 'held': None}
    if sys.platform == 'win32':
        return _windows_advice(starved)
    steamos = steamos if isinstance(steamos, dict) else {}
    cpu, minimum = starved['cpu_mhz'], starved.get('cpu_min_mhz')
    held = ("This PC's processor is held at about " + str(cpu) + ' MHz' +
            (' (its normal minimum is ' + str(minimum) + ' MHz)' if minimum else '') + ' while streaming')
    effect = (": eye tracking and the stream's own work on this PC fall behind, which shows as short stutters and a"
              ' sharp region that lags your eyes.')
    tdp = steamos.get('tdp_w')
    limit = (' (' + str(tdp) + ' W)') if isinstance(tdp, int) and tdp > 0 else ''
    actions = []
    if starved.get('cause') == 'fixed-gpu-clock':
        clock, maximum = starved.get('gpu_mhz'), starved.get('gpu_max_mhz')
        lower = ''
        if maximum:  # a fixed clock well below the GPU's own maximum leaves the processor a share
            low, high = (int(round(maximum * share / 100.0)) * 100 for share in (.6, .75))
            lower = ' (about ' + str(low) + '-' + str(high) + ' MHz on this GPU)'
        message = (held + effect + ' The GPU clock is fixed' + (' at ' + str(clock) + ' MHz' if clock else '') +
                   ', which takes nearly all of the power limit' + limit + '. Set the GPU clock back to automatic, or fix it'
                   ' lower' + lower + ' so the processor keeps some power, wherever it was set: in Steam\'s Game Mode,'
                   ' Quick Access menu > Performance; on the desktop, your power or TDP control.')
        if steamos.get('available') and steamos.get('gpu_level') not in (None, 'auto'):
            actions.append('gpu-auto')
            message += ' Or choose Set the GPU clock to automatic here; it applies at once.'
        short = 'CPU held at ' + str(cpu) + ' MHz · GPU clock fixed'
    else:
        message = (held + effect + ' The power limit (TDP)' + limit + ' is too low for the processor and the GPU'
                   ' together, or the PC is too warm. With the charger connected, raise the TDP limit (Steam\'s Game Mode:'
                   ' Quick Access menu > Performance; on the desktop, your power or TDP control) and leave the GPU clock'
                   ' automatic.')
        short = 'CPU held at ' + str(cpu) + ' MHz · raise the TDP limit'
    return {'state': 'starved', 'message': message, 'short': short, 'actions': actions, 'held': dict(starved)}


def _windows_advice(starved):
    """advice() on Windows (1.0.0.7): Windows' power mode, the charger and the maker's own tool; no one-step fix."""
    cpu, base = starved['cpu_mhz'], starved.get('cpu_max_mhz')
    effect = (": eye tracking and the stream's own work on this PC fall behind, which shows as short stutters and a"
              ' sharp region that lags your eyes.')
    base_text = ' (its base clock is ' + str(base) + ' MHz)' if base else ''
    cause = starved.get('cause')
    if cause == 'processor-limit':
        limit = starved.get('cpu_limit_mhz') or cpu
        message = ("Windows limits this PC's processor to about " + str(limit) + ' MHz' + base_text + ' while streaming'
                   + effect + ' The PC is too warm, or a power setting caps the processor: let air reach its vents,'
                   ' connect the charger, ' + POWER_MODE_STEP + ', and check that Maximum processor state is 100% in'
                   ' Control Panel > Power Options > Change advanced power settings > Processor power management.')
        short = 'CPU limited to ' + str(limit) + ' MHz · check cooling and power mode'
    elif cause == 'fixed-gpu-clock':
        clock = starved.get('gpu_mhz')
        message = ("This PC's processor ran at about " + str(cpu) + ' MHz' + base_text + ' while streaming' + effect +
                   ' The GPU clock is fixed' + (' at ' + str(clock) + ' MHz' if clock else '') + ', which takes nearly'
                   ' all of the power limit. Set the GPU clock back to automatic in ' + VENDOR_TOOL + '.')
        short = 'CPU held at ' + str(cpu) + ' MHz · GPU clock fixed'
    else:
        message = ("This PC's processor ran at about " + str(cpu) + ' MHz' + base_text + ' while busy streaming' +
                   effect + ' The power limit (TDP) is too low for the processor and the GPU together, or the PC is too'
                   ' warm. Connect the charger, ' + POWER_MODE_STEP + ', and raise the TDP limit in ' + VENDOR_TOOL +
                   ', leaving the GPU clock automatic.')
        short = 'CPU held at ' + str(cpu) + ' MHz · raise the TDP limit'
    return {'state': 'starved', 'message': message, 'short': short, 'actions': [], 'held': dict(starved)}
