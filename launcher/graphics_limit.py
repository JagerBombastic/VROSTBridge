"""Notice when this PC's graphics hold the headset stream below its refresh rate (RC4.3).

In the RC4.2 physical test a Bigscreen Beyond streamed 2560 x 2560 per eye (HEVC 10-bit)
to integrated Radeon graphics: SteamVR on the headset PC kept switching the stream to half rate
(37.5 of 75 fps) for seconds at a time and latency rose from about 42 ms to 70-107 ms,
while 2016 x 2016 held 75 fps on the same evening. Frames kept arriving from the host
at 75 per second, so the network was not the cause.

The native client classifies each second of presentation in its TIMING line
('cadence'); 'client_throttled' means SteamVR on this PC shows the stream below the
headset refresh (its own throttle, or too few submissions) while video arrives. This
module keeps a short window of those samples while video streams and decides when the
PC is at its graphics limit, plus the advice the pages show.

1.0.0.7, Windows: Windows has no generic GPU clock, power or temperature reading, so a
power-starved GPU cannot be told from one at its real limit. There the frame rate
(GraphicsLimit, as on Linux) and how busy the GPU is (Windows' GPU Engine counters)
decide: a busy GPU while the stream is held below refresh is at its limit, which on a
handheld or laptop is usually its power limit; the advice names Windows' Best
performance power mode, the charger and the maker's own tool for the power (TDP) limit,
never Steam's Game Mode.
"""
from collections import deque
import sys

from launcher.windows_power import POWER_MODE_STEP, VENDOR_TOOL

CADENCES = ('idle', 'full', 'client_throttled', 'source_limited', 'late_or_paired')
WINDOW_S = 30   # streaming seconds considered
LIMIT_S = 8     # throttled seconds within the window that mean "at the limit"
WARMUP_S = 5    # streaming seconds ignored at the start (first keyframes, SteamVR settling)
STANDARD = {'label': 'Standard', 'width': 2016, 'height': 2016}  # 'sharp' while the headset is not known
NOTICE_S = 12   # how long the in-headset line shows the notice (first episode of a session)
# RC4.4: a power-starved GPU. Both RC4.2 and RC4.3 tests ran the integrated GPU at 800 of its
# 2700 MHz for whole sessions while 45-64 % busy, with the chip drawing about 15 W: its power
# limit (TDP), not the GPU itself, held the stream at half rate.
STARVED_RATIO = .5    # median clock at or below this share of the GPU's maximum...
STARVED_BUSY = 40     # ...while the GPU is at least this busy (percent)
CLOCK_SAMPLES = 6     # performance samples (5 s apart) considered
# 1.0.0.7, Windows (no GPU clocks): the median use of the busiest GPU's 3D (or compute) engine at or above this
# share means the GPU is at its limit. SteamVR rendering at half rate also halves the GPU's work, so this is
# lower than a GPU running flat out would show.
WINDOWS_GPU_BUSY = 70
WINDOWS_GPU_IDLE = 50  # below this, the GPU is not what holds the stream back


class GraphicsLimit:
    """Sliding window of streaming seconds; 'limited' once enough are throttled.

    Idle seconds (no fresh video) and the first WARMUP_S streaming seconds are not counted.
    A second counts as throttled only while frames arrive at the headset refresh and the
    headset is tracked with SteamVR's focus: a Wi-Fi gap or lost tracking also lowers the
    submission rate, which the native classifier reports as client_throttled. A limit clears
    only after a whole window without throttling, so a stream that alternates does not flap.
    """

    def __init__(self, window=WINDOW_S, limit=LIMIT_S):
        self.samples = deque(maxlen=window)
        self.limit = limit
        self.reset()

    def reset(self):
        self.samples.clear()
        self.limited = False
        self.streaming_s = 0
        self.throttled_s = 0
        self.episodes = 0
        self.warmup = WARMUP_S

    def observe(self, cadence, frames_arriving=True, tracked=True):
        """Count one TIMING sample; returns 'limited' or 'recovered' when the state changes."""
        if cadence not in CADENCES or cadence == 'idle':
            return None
        if self.warmup > 0:
            self.warmup -= 1
            return None
        throttled = cadence == 'client_throttled' and bool(frames_arriving) and bool(tracked)
        self.samples.append(throttled)
        self.streaming_s += 1
        self.throttled_s += throttled
        recent = sum(self.samples)
        if not self.limited and recent >= self.limit:
            self.limited = True
            self.episodes += 1
            return 'limited'
        if self.limited and recent == 0 and len(self.samples) == self.samples.maxlen:
            self.limited = False
            return 'recovered'
        return None

    def snapshot(self):
        return {'limited': self.limited, 'recent_throttled_s': sum(self.samples), 'window_s': len(self.samples),
                'streaming_s': self.streaming_s, 'throttled_s': self.throttled_s, 'episodes': self.episodes,
                'share': round(self.throttled_s / self.streaming_s, 3) if self.streaming_s else 0.0}


class GpuClocks:
    """The streaming GPU's recent clock, maximum, load and power from the performance monitor's
    samples (RC4.4), to tell a power-starved GPU from one that is at its real limit."""

    def __init__(self, size=CLOCK_SAMPLES):
        self.samples = deque(maxlen=size)
        self.loads = deque(maxlen=size)  # 1.0.0.7: (busy, video decode) of the busiest GPU, clocks or not
        self.last = None

    def reset(self):
        self.samples.clear()
        self.loads.clear()
        self.last = None

    def observe(self, sample):
        """One performance-monitor sample (read_performance plus 'observed_monotonic'); repeats are ignored."""
        if not isinstance(sample, dict) or not sample.get('active') or sample.get('observed_monotonic') == self.last:
            return
        self.last = sample.get('observed_monotonic')
        best = busiest = None
        for gpu in sample.get('gpus') or []:
            if not isinstance(gpu, dict):
                continue
            load = _load(gpu)
            if load is not None and (busiest is None or load[0] > busiest[0]):
                busiest = load
            sensors = [s for s in gpu.get('sensors') or [] if isinstance(s, dict)]
            clock = next((s.get('clock_mhz') for s in sensors if _positive(s.get('clock_mhz'))), None)
            clock = clock if clock is not None else gpu.get('selected_sclk_mhz')
            busy, maximum = gpu.get('gpu_busy_percent'), gpu.get('max_sclk_mhz')
            if not (_positive(clock) and _positive(maximum) and isinstance(busy, (int, float))):
                continue
            power = next((s.get('average_power_w') for s in sensors if _positive(s.get('average_power_w'))), None)
            row = (float(busy), float(clock), float(maximum), float(power) if power is not None else None)
            if best is None or row[0] > best[0]:
                best = row  # the GPU doing the work
        if best:
            self.samples.append(best)
        if busiest:
            self.loads.append(busiest)

    def load(self):
        """1.0.0.7: the busiest GPU's latest use, also where no clock is reported (Windows):
        {'busy_percent', 'video_decode_percent' (None where unknown)}, or None."""
        if not self.loads:
            return None
        busy, decode = self.loads[-1]
        return {'busy_percent': int(round(busy)), 'video_decode_percent': int(round(decode)) if decode is not None else None}

    def latest(self):
        """The latest sample, for the live readout (RC4.4): {'busy_percent', 'clock_mhz', 'max_mhz', 'power_w'}."""
        if not self.samples:
            return None
        busy, clock, maximum, power = self.samples[-1]
        return {'busy_percent': int(round(busy)), 'clock_mhz': int(round(clock)), 'max_mhz': int(round(maximum)),
                'power_w': round(power) if power is not None else None}

    def starved(self):
        """{'clock_mhz', 'max_mhz', 'power_w'} while the busy GPU is held far below its maximum, else None.

        Windows without GPU clocks (1.0.0.7): {'clock_mhz': None, 'max_mhz': None, 'power_w': None,
        'busy_percent', 'basis': 'gpu-busy'} while the GPU is busy (WINDOWS_GPU_BUSY): at its limit, often its
        power limit, which cannot be told apart there."""
        if sys.platform == 'win32' and not self.samples:
            if len(self.loads) < 3:
                return None
            busy = _median([load[0] for load in self.loads])
            if busy < WINDOWS_GPU_BUSY:
                return None
            return {'clock_mhz': None, 'max_mhz': None, 'power_w': None, 'busy_percent': int(round(busy)),
                    'basis': 'gpu-busy'}
        if len(self.samples) < 3:
            return None
        def median(values):
            values = sorted(values)
            return values[len(values) // 2]
        busy = median([s[0] for s in self.samples])
        clock = median([s[1] for s in self.samples])
        maximum = max(s[2] for s in self.samples)
        powers = [s[3] for s in self.samples if s[3] is not None]
        if busy < STARVED_BUSY or clock > STARVED_RATIO * maximum:
            return None
        return {'clock_mhz': int(round(clock)), 'max_mhz': int(round(maximum)),
                'power_w': round(median(powers)) if powers else None}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 100000


def _percent(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def _median(values):
    values = sorted(values)
    return values[len(values) // 2]


def _load(gpu):
    """(busy, video decode or None) of one GPU row: the busier of its 3D and compute engines (Windows reports
    them apart), or None when the row says nothing about its use."""
    busy = [gpu[key] for key in ('gpu_busy_percent', 'compute_busy_percent') if _percent(gpu.get(key))]
    if not busy:
        return None
    decode = gpu.get('video_decode_percent')
    return float(max(busy)), float(decode) if _percent(decode) else None


def frames_arriving(timings):
    """Whether the host's frames reached this PC at (nearly) the headset refresh in that second."""
    try:
        return float(timings.get('packet_fps', 0)) >= .85 * float(timings.get('refresh_hz', 0)) > 0
    except (TypeError, ValueError):
        return False


def _windows_starved(starved):
    """The Windows advice for a GPU at its limit (Windows has no Game Mode and no steamos-manager)."""
    clock, maximum = starved.get('clock_mhz'), starved.get('max_mhz')
    if _positive(clock) and _positive(maximum):
        lead = (' Its GPU ran at ' + str(clock) + ' of its ' + str(maximum) + ' MHz while busy: a power limit (TDP) or'
                ' a fixed GPU clock is holding it back.')
    else:
        busy = starved.get('busy_percent')
        lead = (' Its GPU was ' + (str(busy) + '% ' if isinstance(busy, int) else '') + 'busy: it is at its limit,'
                ' which on a handheld or laptop is usually its power limit (TDP).')
    return (lead + ' Connect the charger, ' + POWER_MODE_STEP + ' and raise the TDP limit in ' + VENDOR_TOOL +
            ', leaving the GPU clock on automatic, then start again.')


def advice(snapshot, stream_width=None, refresh_hz=None, reprojection_repairable=False, reaim_paused=False,
           smaller=None, chosen=False, starved=None, gpu_auto=False, load=None):
    """Plain-language status and advice for the pages; '' when nothing needs saying.

    stream_width is the running stream's eye width (None when unknown). smaller is the
    headset's lighter choice ({'label', 'width', 'height'}, App.sharp_preset(): High on other headsets, from 1.0.0.13
    on an Index a lower refresh rate, with its 'refresh_hz'), suggested only when the stream is wider or the refresh
    higher; chosen when it is already saved for the next Start. A Beyond has none ({'id': None}): only Bigscreen's
    Beyond Utility switches its mode, and its 'hint' says so. Background reprojection only when its repair is offered. starved (GpuClocks.starved())
    puts the power limit first: raising it is the fix, not a smaller stream. gpu_auto (1.0.0.5): steamos-manager
    reports a fixed GPU clock it can set back to automatic, offered in one step while the GPU is held low.
    Windows (1.0.0.7): the advice names Windows' power mode, the charger and the maker's own tool, and never
    offers gpu-auto; load (GpuClocks.load()) of a GPU that was not busy points at the processor instead.
    """
    if not snapshot.get('episodes'):
        return {'state': 'ok', 'message': '', 'actions': [], 'short': '', 'smaller': None, 'starved': None}
    windows = sys.platform == 'win32'
    smaller = smaller or STANDARD
    rate = str(int(round(refresh_hz))) if isinstance(refresh_hz, (int, float)) and refresh_hz >= 30 else 'full'
    if snapshot.get('limited'):
        state = 'limited'
        message = ("This PC's graphics can't keep up with the stream: SteamVR showed it at reduced frame rate for "
                   + str(snapshot.get('recent_throttled_s', 0)) + ' of the last ' + str(snapshot.get('window_s', 0))
                   + ' seconds.')
    else:
        state = 'recovered'
        share = round(100 * float(snapshot.get('share') or 0))
        message = ("This PC's graphics fell behind earlier in this session (reduced frame rate "
                   + str(share) + '% of the time).')
    actions = []
    if starved and windows:
        message += _windows_starved(starved)
    elif starved:
        power = (' at about ' + str(starved['power_w']) + ' W') if starved.get('power_w') else ''
        message += (" Its GPU ran at " + str(starved['clock_mhz']) + ' of its ' + str(starved['max_mhz']) + ' MHz while busy'
                    + (',' + power if power else '') + ': a power limit (TDP) or a fixed GPU clock is holding it back.'
                    ' With the charger connected, raise the TDP limit as far as it goes and leave the GPU clock on'
                    " automatic (Steam's Game Mode: Quick Access menu > Performance; on the desktop, your power or TDP"
                    ' control), then start again.')
        if gpu_auto:
            actions.append('gpu-auto')
            message += ' The GPU clock is fixed: choose Set the GPU clock to automatic here; it applies at once.'
    elif windows and isinstance(load, dict) and _percent(load.get('busy_percent')) \
            and load['busy_percent'] < WINDOWS_GPU_IDLE:
        message += (' Its GPU was only ' + str(int(load['busy_percent'])) + '% busy, so the processor or another'
                    ' program on this PC is more likely holding SteamVR back.')
    if reaim_paused:
        message += ' Frame re-aim is paused for this session to save GPU time.'
    lower_rate = (isinstance(smaller.get('refresh_hz'), (int, float)) and isinstance(refresh_hz, (int, float))
                  and refresh_hz > smaller['refresh_hz'] + 1)
    offered = lower_rate or (isinstance(stream_width, int) and isinstance(smaller.get('width'), int)
                             and stream_width > smaller['width'])
    size = str(smaller.get('label')) + ' (' + str(smaller.get('width')) + ' x ' + str(smaller.get('height')) + ' per eye)'
    if isinstance(smaller.get('refresh_hz'), (int, float)):
        rate = str(int(round(smaller['refresh_hz'])))  # a mode with its own refresh rate: steady at that rate
    if offered and chosen:
        message += ' ' + size + ' is saved and gives a steady ' + rate + ' fps from the next Start.'
    elif offered:
        message += (' Or choose ' if starved else ' Choose ') + size + ' for a steady ' + rate + ' fps; it applies at the next Start.'
        actions.append('use-standard')
    elif smaller.get('hint'):
        message += ' ' + smaller['hint']
    if reprojection_repairable:
        message += (' Background reprojection is off in SteamVR: Repair it so a late frame is smoothed instead of'
                    ' halving the frame rate.')
        actions.append('repair-reprojection')
    if not actions and not (offered and chosen) and not starved:
        message += ' Close other programs on this PC and check that it is not overheating.'
    if starved and windows and not starved.get('clock_mhz'):
        short = 'GPU ' + (str(starved['busy_percent']) + '% ' if isinstance(starved.get('busy_percent'), int) else '') \
            + 'busy · raise the TDP limit'
    elif starved:
        short = 'GPU held at ' + str(starved['clock_mhz']) + ' MHz · raise the TDP limit'
    else:
        short = 'PC graphics at their limit' + (' · re-aim paused' if reaim_paused else '')
        if offered:  # a rate keeps the size: its name says it all
            short += ' · ' + str(smaller['label']) + ('' if isinstance(smaller.get('refresh_hz'), (int, float))
                                                      else ' ' + str(smaller['width'])) + (' next Start' if chosen else ' for ' + rate + ' fps')
    return {'state': state, 'message': message, 'actions': actions, 'short': short,
            'smaller': dict(smaller) if offered else None, 'starved': dict(starved) if starved else None}
