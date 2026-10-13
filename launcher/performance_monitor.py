"""Cached, read-only Linux power/GPU evidence; never sampled by the VR frame loop.

The values are driver observations, not inferred throttling diagnoses. Unsupported
or inaccessible attributes are omitted, and no clock, governor or power cap is set.

1.0.0.4: also how busy the processor is and how much of it the programs of a VR session
use (SteamVR, Steam, the desktop's compositor, eye tracking, VROSTBridge), by program,
so a report shows what competes with the stream. Other programs are only summed as
'other', never named.

1.0.0.7, Windows: WindowsSampler fills the same keys from Windows' own counters
(launcher/windows_power.py): processor busy from GetSystemTimes, program use by group
(Toolhelp32 and GetProcessTimes, programs of this Windows session only, as Linux counts
this user's), GPU use from the GPU Engine counters per adapter ('gpu_busy_percent' is the
3D engine; 'compute_busy_percent' and 'video_decode_percent' where those engines exist)
with 'vram_used_bytes', the processor's clocks from CallNtPowerInformation per logical
processor ('reported_frequency_mhz' is Windows' CurrentMhz, plus 'maximum_mhz' and
'limit_mhz', Windows' current limit on the clock) and the battery and charger from
GetSystemPowerStatus. Windows' CurrentMhz often stays at the base clock, so the sample
also carries 'cpu_performance_percent' (Processor Information '% Processor Performance':
the processors' speed while they work, against their base clock) and 'cpu_effective_mhz'.
GPU clocks, GPU power and temperatures have no generic Windows source: they are absent.
"""
from copy import deepcopy
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

from launcher import windows_power

# 1.0.0.4: program groups by process name (the kernel's 15-character name), first match wins.
PROCESS_GROUPS = (
    ('Baballonia', 'eye tracking (Baballonia)'), ('go-bsb-cams', 'eye cameras'),
    ('VROSTBridgeClie', 'VROSTBridge headset client'), ('VROSTBridgeLeas', 'VROSTBridge display lease'),
    ('VROSTBridge', 'VROSTBridge'), ('vrcompositor', 'vrcompositor'), ('vrserver', 'vrserver'),
    ('vrmonitor', 'vrmonitor'), ('vrdashboard', 'vrdashboard'), ('vrwebhelper', 'vrwebhelper'),
    ('vrstartup', 'vrstartup'), ('steamwebhelper', 'steamwebhelper'), ('steam', 'steam'),
    ('gamescope', 'gamescope'), ('kwin_', 'kwin'), ('Xwayland', 'Xwayland'), ('plasmashell', 'plasmashell'),
    ('gnome-shell', 'gnome-shell'), ('pipewire', 'pipewire'), ('wireplumber', 'pipewire'),
)
CLOCK_TICKS = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') and 'SC_CLK_TCK' in getattr(os, 'sysconf_names', {}) else 100


def process_group(name):
    return next((group for prefix, group in PROCESS_GROUPS if name.startswith(prefix)), 'other')


def process_ticks(proc=Path('/proc'), uid=None):
    """{pid: (group, processor ticks used so far)} for this user's processes; {} where /proc is missing."""
    uid = os.getuid() if uid is None and hasattr(os, 'getuid') else uid
    result = {}
    try:
        entries = [entry for entry in Path(proc).iterdir() if entry.name.isdecimal()]
    except OSError:
        return result
    for entry in entries:
        try:
            if uid is not None and entry.stat().st_uid != uid:
                continue
            text = (entry / 'stat').read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        start, end = text.find('('), text.rfind(')')
        fields = text[end + 2:].split()
        if start < 0 or end < start or len(fields) < 13:
            continue
        try:
            result[int(entry.name)] = (process_group(text[start + 1:end]), int(fields[11]) + int(fields[12]))
        except ValueError:
            continue
    return result


def processor_ticks(proc=Path('/proc')):
    """(busy, total) ticks of all processors since boot from /proc/stat, or None."""
    try:
        with (Path(proc) / 'stat').open('r', encoding='utf-8') as stream:
            fields = stream.readline().split()
    except OSError:
        return None
    if len(fields) < 5 or fields[0] != 'cpu':
        return None
    try:
        values = [int(value) for value in fields[1:9]]
    except ValueError:
        return None
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total - idle, total


class ProcessUse:
    """Processor use between two samples: by program group (percent of one core) and in all (percent)."""

    def __init__(self, proc=Path('/proc'), clock=time.monotonic):
        self.proc, self.clock = Path(proc), clock
        self.previous = self.stat = self.at = None

    def sample(self):
        now, ticks, stat = self.clock(), process_ticks(self.proc), processor_ticks(self.proc)
        previous, before, at = self.previous, self.stat, self.at
        self.previous, self.stat, self.at = ticks, stat, now
        result = {}
        if previous is not None and at is not None and now > at and ticks:
            used = {}
            for pid, (group, total) in ticks.items():
                earlier = previous.get(pid)
                delta = total - earlier[1] if earlier and earlier[0] == group and total >= earlier[1] else total
                used[group] = used.get(group, 0) + delta
            scale = 100.0 / (CLOCK_TICKS * (now - at))
            shares = {group: round(value * scale, 1) for group, value in used.items() if value * scale >= .5}
            result['processes'] = dict(sorted(shares.items(), key=lambda item: -item[1]))
        if stat and before and stat[1] > before[1]:
            result['cpu_busy_percent'] = round(100.0 * (stat[0] - before[0]) / (stat[1] - before[1]), 1)
        return result


def _read(path, limit=4096):
    try:
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            return stream.read(limit).strip()
    except (OSError, ValueError):
        return None


def _number(path, scale=1, minimum=0, maximum=None):
    text = _read(path, 64)
    try:
        value = int(text) / scale
        if value < minimum or (maximum is not None and value > maximum): return None
        return value
    except (TypeError, ValueError, OverflowError): return None


def _entries(path, pattern, limit):
    try: return sorted((p for p in path.iterdir() if re.fullmatch(pattern, p.name)), key=lambda p:p.name)[:limit]
    except OSError: return []


def _observed(row, name, value):
    if value is not None: row[name] = value


def _selected_clock(path):
    text = _read(path)
    if text is None: return None
    # pp_dpm_* marks the selected DPM level, not an independently measured clock.
    for line in text.splitlines():
        match = re.fullmatch(r'\s*\d+:\s*(\d+)\s*Mhz\s*\*\s*', line, re.IGNORECASE)
        if match: return int(match[1])
    return None


def _highest_clock(path):
    """The highest DPM level in pp_dpm_sclk (RC4.4): the GPU's maximum clock, for telling a
    power-starved GPU (held near its minimum while busy) from one that is simply too slow."""
    text = _read(path)
    if text is None: return None
    levels = [int(match[1]) for match in (re.fullmatch(r'\s*\d+:\s*(\d+)\s*Mhz\s*\*?\s*', line, re.IGNORECASE)
                                          for line in text.splitlines()[:32]) if match]
    return max(levels) if levels and 0 < max(levels) <= 10000 else None


def read_performance(sysfs=Path('/sys')):
    root = Path(sysfs)
    result = {'gpus':[], 'cpu_policies':[], 'power_supplies':[]}
    seen = set()
    for card in _entries(root/'class/drm', r'card\d+', 16):
        device = card/'device'
        identity = str(device.resolve())
        if identity in seen: continue
        seen.add(identity)
        row = {'card':card.name}
        for name, filename, scale, maximum in (
            ('gpu_busy_percent','gpu_busy_percent',1,100),
            ('memory_busy_percent','mem_busy_percent',1,100),
            ('vram_used_bytes','mem_info_vram_used',1,None),
            ('vram_total_bytes','mem_info_vram_total',1,None)):
            _observed(row,name,_number(device/filename,scale,maximum=maximum))
        for name, filename in (('selected_sclk_mhz','pp_dpm_sclk'),('selected_mclk_mhz','pp_dpm_mclk')):
            _observed(row,name,_selected_clock(device/filename))
        _observed(row,'max_sclk_mhz',_highest_clock(device/'pp_dpm_sclk'))
        level = _read(device/'power_dpm_force_performance_level',64)
        if level and re.fullmatch(r'[A-Za-z0-9_ -]{1,63}',level): row['performance_level'] = level
        sensors = []
        for hwmon in _entries(device/'hwmon',r'hwmon\d+',8):
            sensor = {}
            for name, filename, scale, low, high in (
                ('temperature_c','temp1_input',1000,-100,300),
                ('average_power_w','power1_average',1000000,0,2000),
                ('instant_power_w','power1_input',1000000,0,2000),
                ('power_cap_w','power1_cap',1000000,0,2000),
                ('clock_mhz','freq1_input',1000000,0,10000),
                ('memory_clock_mhz','freq2_input',1000000,0,10000)):
                _observed(sensor,name,_number(hwmon/filename,scale,low,high))
            if sensor: sensors.append(sensor)
        if sensors: row['sensors'] = sensors
        # The maximum clock is a capability, not an observation: it never makes a GPU row by itself.
        if set(row)-{'card','max_sclk_mhz'}: result['gpus'].append(row)
    for policy in _entries(root/'devices/system/cpu/cpufreq',r'policy\d+',32):
        row = {'policy':policy.name}
        for name, filename in (('reported_frequency_mhz','scaling_cur_freq'),('minimum_mhz','scaling_min_freq'),('maximum_mhz','scaling_max_freq')):
            _observed(row,name,_number(policy/filename,1000,maximum=20000))
        governor = _read(policy/'scaling_governor',64)
        if governor and re.fullmatch(r'[A-Za-z0-9_-]{1,63}',governor): row['governor'] = governor
        if len(row)>1: result['cpu_policies'].append(row)
    for supply in _entries(root/'class/power_supply',r'[A-Za-z0-9_.:-]{1,64}',16):
        kind = _read(supply/'type',64)
        if kind not in ('Battery','Mains','USB','USB_C','USB_PD'): continue
        row = {'type':kind}
        _observed(row,'online',_number(supply/'online',maximum=1))
        if kind=='Battery':
            _observed(row,'capacity_percent',_number(supply/'capacity',maximum=100))
            status = _read(supply/'status',64)
            if status in ('Unknown','Charging','Discharging','Not charging','Full'): row['status'] = status
        if len(row)>1: result['power_supplies'].append(row)
    result['available'] = any(result[name] for name in ('gpus','cpu_policies','power_supplies'))
    return result


# Windows (1.0.0.7) ---------------------------------------------------------------------------------------------
# Program groups by image name (case-insensitive prefix, first match wins); the companion itself (python.exe) is
# recognised by its process id.
WINDOWS_PROCESS_GROUPS = (
    ('baballonia', 'eye tracking (Baballonia)'), ('go-bsb-cams', 'eye cameras'),
    ('vrostbridgeclient', 'VROSTBridge headset client'), ('vrostbridge', 'VROSTBridge'),
    ('vrcompositor', 'vrcompositor'), ('vrserver', 'vrserver'), ('vrmonitor', 'vrmonitor'),
    ('vrdashboard', 'vrdashboard'), ('vrwebhelper', 'vrwebhelper'), ('vrstartup', 'vrstartup'),
    ('steamwebhelper', 'steamwebhelper'), ('steam', 'steam'), ('dwm.exe', 'dwm'), ('audiodg.exe', 'audiodg'),
)
WINDOWS_COUNTERS = (('engine', r'\GPU Engine(*)\Utilization Percentage'),
                    ('memory', r'\GPU Adapter Memory(*)\Dedicated Usage'),
                    ('performance', r'\Processor Information(_Total)\% Processor Performance'))
_ENGINE = re.compile(r'pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_(.*)')
_ADAPTER = re.compile(r'luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)')


def windows_process_group(name, pid=None, own_pid=None):
    if own_pid is not None and pid == own_pid:
        return 'VROSTBridge'
    lowered = (name or '').casefold()
    return next((group for prefix, group in WINDOWS_PROCESS_GROUPS if lowered.startswith(prefix)), 'other')


def _finite(value, low=0.0, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low:
        return False
    return high is None or value <= high


def _engine_kind(name):
    kind = re.sub(r'[^0-9a-z]', '', name.casefold())
    if kind.startswith('3d'):
        return 'gpu_busy_percent'
    if kind.startswith('compute'):
        return 'compute_busy_percent'
    if kind.startswith('videodecode') or kind.startswith('videocodec'):
        return 'video_decode_percent'
    return None


def windows_gpu_rows(engines, memory=()):
    """GPU rows from '\\GPU Engine(*)\\Utilization Percentage' ([(instance, percent)], one instance per program and
    engine) and '\\GPU Adapter Memory(*)\\Dedicated Usage' ([(instance, bytes)]). An engine's use is the sum over
    its programs; an adapter's use of a kind is its busiest engine of that kind (as Task Manager shows it)."""
    engine_use, adapters = {}, []
    for name, value in engines or ():
        match = _ENGINE.fullmatch(str(name).strip().casefold())
        if not match or not _finite(value):
            continue
        adapter = 'luid_%s_%s_phys_%s' % (match[2], match[3], match[4])
        if adapter not in adapters:
            adapters.append(adapter)
        kind = _engine_kind(match[6])
        if kind:
            key = (adapter, match[5], kind)
            engine_use[key] = engine_use.get(key, 0.0) + float(value)
    used = {}
    for adapter, value in memory or ():
        match = _ADAPTER.fullmatch(str(adapter).strip().casefold())
        if match and _finite(value, high=2 ** 50):
            used['luid_%s_%s_phys_%s' % match.groups()] = int(value)
    rows = []
    for adapter in sorted(adapters)[:16]:
        row = {'card': adapter}
        for (owner, _, kind), value in engine_use.items():
            if owner == adapter:
                row[kind] = max(row.get(kind, 0.0), round(min(100.0, value), 1))
        if adapter in used:
            row['vram_used_bytes'] = used[adapter]
        if len(row) > 1:
            rows.append(row)
    return rows


def windows_processor_rows(entries):
    """cpu_policies rows from CallNtPowerInformation(ProcessorInformation), one per logical processor."""
    rows = []
    for entry in (entries or [])[:64]:
        if not isinstance(entry, dict) or not isinstance(entry.get('number'), int):
            continue
        row = {'policy': 'cpu%d' % entry['number']}
        for key, name in (('reported_frequency_mhz', 'current_mhz'), ('maximum_mhz', 'max_mhz'),
                          ('limit_mhz', 'limit_mhz')):
            if isinstance(entry.get(name), int) and 0 < entry[name] <= 20000:
                row[key] = float(entry[name])
        if len(row) > 1:
            rows.append(row)
    return rows


def windows_power_supplies(status):
    """power_supplies rows from GetSystemPowerStatus: the charger ('Mains') and the battery, where present."""
    rows = []
    if not isinstance(status, dict):
        return rows
    line, flag, percent = status.get('ac_line'), status.get('battery_flag'), status.get('battery_percent')
    if line in (0, 1):
        rows.append({'type': 'Mains', 'online': float(line)})
    if isinstance(flag, int) and flag != 255 and not flag & 128:  # 128: no system battery; 255: unknown
        row = {'type': 'Battery'}
        if isinstance(percent, int) and 0 <= percent <= 100:
            row['capacity_percent'] = float(percent)
        if flag & 8:
            row['status'] = 'Charging'
        elif line == 1:
            row['status'] = 'Full' if percent == 100 else 'Not charging'
        else:
            row['status'] = 'Discharging' if line == 0 else 'Unknown'
        if status.get('saver') == 1:
            row['battery_saver'] = True
        rows.append(row)
    return rows


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


class WindowsSampler:
    """One Windows performance sample with the keys read_performance() and ProcessUse use on Linux.

    Rate values (processor and program use, GPU use, processor performance) compare two samples, so a session's
    first sample has none; reset() (between sessions) starts afresh and closes the counters. Every part fails on
    its own: what Windows does not answer is left out.
    """

    def __init__(self, api=None, clock=time.monotonic, wall=time.time, own_pid=None):
        self.api, self.clock, self.wall = api, clock, wall
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.lock = threading.Lock()
        self.query = None
        self.query_problem = None
        self.previous = self.stat = self.at = self.at_wall = None
        self.session = None

    def _api(self):
        return self.api if self.api is not None else windows_power.api()

    def reset(self):
        with self.lock:
            self.previous = self.stat = self.at = self.at_wall = None
            self.query_problem = None
            self._close_query()

    def close(self):
        self.reset()

    def _close_query(self):
        query, self.query = self.query, None
        if query is not None:
            try:
                query.close()
            except Exception:
                pass

    def _counters(self, native):
        """The open PDH query, opened on first use; None (and a problem noted once) where Windows has none."""
        if self.query is None and self.query_problem is None:
            try:
                query = native.pdh_query()
            except Exception as error:
                self.query_problem = str(error)[:120]
                return None
            problems = []
            for name, path in WINDOWS_COUNTERS:
                try:
                    query.add(name, path)
                except Exception as error:
                    problems.append(str(error)[:120])
            if not query.counters:
                query.close()
                self.query_problem = '; '.join(problems)[:200] or 'no performance counters'
                return None
            if problems:
                self.query_problem = '; '.join(problems)[:200]
            self.query = query
        return self.query

    def sample(self):
        with self.lock:
            native, now = self._api(), self.clock()
            result = {'gpus': [], 'cpu_policies': [], 'power_supplies': []}
            try:
                entries = native.processor_information()
            except Exception:
                entries = []
            result['cpu_policies'] = windows_processor_rows(entries)
            try:
                result['power_supplies'] = windows_power_supplies(native.power_status())
            except Exception:
                pass
            fresh = self.query is None and self.query_problem is None
            query, performance = self._counters(native), None
            if query is not None:
                try:
                    query.collect()
                    result['gpus'] = windows_gpu_rows(query.items('engine'), query.items('memory'))
                    performance = query.value('performance')
                except Exception as error:
                    result['counter_error'] = str(error)[:120]
            if fresh and self.query_problem:
                result['counter_error'] = self.query_problem  # once per session, not in every sample
            if _finite(performance, high=1000):
                result['cpu_performance_percent'] = round(float(performance), 1)
                base = _median([entry['max_mhz'] for entry in entries
                                if isinstance(entry, dict) and isinstance(entry.get('max_mhz'), int)
                                and 0 < entry['max_mhz'] <= 20000])
                if base:
                    result['cpu_effective_mhz'] = int(round(base * performance / 100.0))
            result['available'] = any(result[name] for name in ('gpus', 'cpu_policies', 'power_supplies'))
            try:
                result.update(self._process_use(native, now))
            except Exception as error:
                result['process_error'] = str(error)[:120]
            return result

    def _process_use(self, native, now):
        """{'processes': {group: percent of one processor}, 'cpu_busy_percent'} between this sample and the last."""
        if self.session is None:
            self.session = native.session_id(self.own_pid)
        wall = int((self.wall() + windows_power.FILETIME_UNIX_OFFSET_S) * windows_power.FILETIME_TICKS)
        rows = {}
        for pid, _parent, name in native.processes():
            if not pid or (self.session is not None and native.session_id(pid) != self.session):
                continue  # the idle process, and programs of other Windows sessions (services)
            times = native.process_times(pid)
            if times:
                rows[pid] = (windows_process_group(name, pid, self.own_pid), times[0], times[1])
        try:
            system = native.system_times()
        except Exception:
            system = None
        previous, before, at, at_wall = self.previous, self.stat, self.at, self.at_wall
        self.previous, self.stat, self.at, self.at_wall = rows, system, now, wall
        result = {}
        if previous is not None and at is not None and now > at and rows:
            used = {}
            for pid, (group, created, total) in rows.items():
                earlier = previous.get(pid)
                if earlier and earlier[1] == created and total >= earlier[2]:
                    delta = total - earlier[2]
                elif at_wall is not None and created >= at_wall:
                    delta = total  # started since the last sample
                else:
                    continue  # running before but not readable then: no baseline yet
                used[group] = used.get(group, 0) + delta
            scale = 100.0 / (windows_power.FILETIME_TICKS * (now - at))
            shares = {group: round(value * scale, 1) for group, value in used.items() if value * scale >= .5}
            result['processes'] = dict(sorted(shares.items(), key=lambda item: -item[1]))
        if system and before:
            busy_now, busy_before = system[1] + system[2] - system[0], before[1] + before[2] - before[0]
            total_now, total_before = system[1] + system[2], before[1] + before[2]
            if total_now > total_before:
                share = 100.0 * (busy_now - busy_before) / (total_now - total_before)
                result['cpu_busy_percent'] = round(min(100.0, max(0.0, share)), 1)
        return result


class PerformanceMonitor:
    def __init__(self, context, record, sysfs=Path('/sys'), auto_start=True, interval=5, windows=None):
        """context returns {'active': bool, 'phase': str}; record is journal.record. windows: the Windows
        sampler (tests pass one); None makes a WindowsSampler on the first sample on Windows."""
        self.context, self.record, self.sysfs = context, record, Path(sysfs)
        self.interval = max(1,float(interval))
        self.lock = threading.Lock(); self.closed = threading.Event(); self.wake = threading.Event()
        self.value = {}; self.thread = None
        self.process_use = ProcessUse()
        self.windows = windows
        if auto_start:
            self.thread = threading.Thread(target=self._poll,daemon=True,name='VROSTBridgePerformanceEvidence')
            self.thread.start()

    def refresh(self): self.wake.set()

    def snapshot(self):
        with self.lock: return deepcopy(self.value)

    def sample(self):
        context = self.context()
        active = context.get('active') is True
        value = {'observed_monotonic':time.monotonic(), 'active':active, 'available':False}
        phase = context.get('phase')
        if isinstance(phase,str): value['phase'] = phase[:64]
        if sys.platform == 'win32':  # 1.0.0.7: Windows' own counters, with the same keys
            self._sample_windows(value, active)
        elif active:
            value.update(read_performance(self.sysfs))
            try: value.update(self.process_use.sample())
            except Exception as error: value['process_error'] = str(error)[:120]
        else:  # the next session's first sample starts afresh, not from the last session's
            self.process_use.previous = self.process_use.stat = self.process_use.at = None
        with self.lock: self.value = value
        if active: self.record('performance-sample',value)
        return value

    def _sample_windows(self, value, active):
        if not active:  # the next session's first sample starts afresh; the counters close meanwhile
            if self.windows is not None: self.windows.reset()
            return
        if self.windows is None: self.windows = WindowsSampler()
        try: value.update(self.windows.sample())
        except Exception as error: value['sample_error'] = str(error)[:120]

    def _poll(self):
        while not self.closed.is_set():
            try: self.sample()
            except Exception as error: self.record('performance-inspection-error',str(error)[:200])
            self.wake.wait(self.interval); self.wake.clear()

    def close(self):
        self.closed.set(); self.wake.set()
        if self.thread: self.thread.join(2)
        if self.windows is not None:
            try: self.windows.close()
            except Exception: pass
