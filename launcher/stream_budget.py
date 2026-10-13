"""How many bits a stream needs, and keeping it there while it runs (1.0.0.8).

The 1.0.0.7 test streamed about 195 Mbit/s, four times what the evening before had used
(about 50 Mbit/s), after the 1.0.0.6 network test passed a wired path up to 500 Mbit/s.
On the same headset PC the bigger frames doubled the hardware decoding time at the same
picture size (4.6 -> 7.8 ms), arrived later and less evenly (the host's frame timing lock
stopped locking: 5 of 48 updates against 34 of 34), lost a frame every 12 s rather than
every 27 s, and repeated 2-3 refreshes a second. A link that carries a bitrate is not a
reason to use it: past a few tenths of a bit per pixel the picture barely improves while
every frame costs more to send and to decode.

quality_ceiling_mbps() is the most the automatic bitrate asks for a stream of this size:
bits per pixel per frame by codec, times the encoded pixels and the frame rate.

BitrateGovernor follows the session second by second from the native client's TIMING
lines and steps the bitrate down when the stream shows the cost of its size (decoding
behind, frames bunching up on arrival, frames lost), then back up slowly while it runs
clean, never above the ceiling it started with. Each step down makes the next step up
wait longer, so a stream that struggles at one bitrate does not keep returning to it.
Decoding time depends on the bitrate on some decoders and only on the picture size on
others, so a step taken for decoding alone is checked: when decoding got no faster, the
bitrate goes back up and decoding time no longer counts for this session. A host that
offers 'bitrate-control' applies each change at once (launcher/session_link.py
heartbeats, host/control.py).

1.0.0.10: uneven arrival is checked the same way. Big frames bunch up on arrival on some
links (the 1.0.0.7 test's wired 195 Mbit/s), but on Wi-Fi the radio's own pauses and
jitter make frames arrive just as unevenly at any bitrate: the 1.0.0.9 test stepped from
40 to 20 Mbit/s for it and the frames arrived no more evenly at 20. A step taken for
uneven arrival alone is now compared with how evenly frames arrived over the seconds at
the bitrate before it; when they arrive no more evenly, the bitrate goes back up and
uneven arrival no longer counts for this session. Seconds in which too few frames arrived
at all (cadence source_limited: the host or the network, as while a game loads) do not
count as uneven arrival.
"""
from collections import deque
import math
import statistics

# Bits per pixel per frame the automatic bitrate stops at. HEVC at 0.22 gave the 1.0.0.5 test's clean
# picture (2432 x 1216 at 75 Hz: 49 Mbit/s); H.264 needs about 45 % more for the same picture and AV1 a
# little less than HEVC.
BITS_PER_PIXEL = {'H264': 0.32, 'Hevc': 0.22, 'AV1': 0.18}
MIN_MBPS = 20
MAX_MBPS = 300
FLOOR_SHARE = 0.35       # the governor never goes below this share of the ceiling (or MIN_MBPS)

# Governor timing (streaming seconds, one TIMING line each).
WARMUP_S = 8             # the first keyframes and SteamVR settling are not judged
WINDOW_S = 10            # seconds a step-down decision looks back over
TROUBLE_S = 5            # troubled seconds in the window that step the bitrate down
LOSSES = 2               # frames lost in the window that step it down
HOLD_DOWN_S = 12         # after a step down, the next decision waits this long
CLEAN_UP_S = 40          # clean seconds before a step up (after the first step down)...
CLEAN_UP_MAX_S = 160     # ...doubling with each further step down, up to this
HOLD_UP_S = 20           # after a step up, the next decision waits this long
STEP_DOWN = 0.8
STEP_UP = 1.1
CHECK_SETTLE_S = 6       # a step taken for decoding alone: seconds for the host to apply it...
CHECK_S = 8              # ...then seconds of decoding time compared with the window before it
CHECK_GAIN = 0.93        # decoding at least 7 % faster means the bitrate was what slowed it
ARRIVAL_HISTORY_S = 60   # 1.0.0.10: seconds of arrival spread kept at the current bitrate...
ARRIVAL_CHECK_MIN_S = 20 # ...of which a step for uneven arrival needs this many to be checked against
ARRIVAL_CHECK_S = 12     # seconds of arrival spread compared after a step for uneven arrival

# What a troubled second is (shares of the headset's frame interval).
DECODE_MEAN_SHARE = 0.42  # the decoder's mean time per frame
DECODE_P95_SHARE = 0.65   # its slowest frames
ARRIVAL_P95_SHARE = 1.45  # gaps between arriving frames (1.0 is perfectly even)
LATE_TIMEOUTS = 3         # frames given up on in one second
STREAMING = ('full', 'late_or_paired', 'client_throttled', 'source_limited')
LATE = ('late_or_paired', 'client_throttled')


def _number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def quality_ceiling_mbps(codec, encoded_width, encoded_height, fps):
    """The most the automatic bitrate asks for: codec bits per pixel x encoded pixels x frame rate, in
    whole Mbit/s within [MIN_MBPS, MAX_MBPS]. None when the size or rate is unknown."""
    bpp = BITS_PER_PIXEL.get(codec, BITS_PER_PIXEL['H264'])
    width, height, rate = (_number(value) for value in (encoded_width, encoded_height, fps))
    if not width or not height or not rate or width <= 0 or height <= 0 or rate <= 0:
        return None
    value = bpp * width * height * rate / 1e6
    return int(max(MIN_MBPS, min(MAX_MBPS, round(value))))


def floor_mbps(ceiling):
    """The lowest bitrate the governor steps down to for a ceiling."""
    return int(min(int(ceiling), max(MIN_MBPS, round(FLOOR_SHARE * int(ceiling)))))


def _round5(value):
    return int(5 * round(value / 5.0))


class BitrateGovernor:
    """Second-by-second bitrate decisions for one session (see the module notes).

    reset() at each session start; observe() with each TIMING line (a dict of strings, as the
    native client prints them); the returned value is the new bitrate when it changed, else None.
    """

    def __init__(self):
        self.reset(None, None, 75.0)

    def reset(self, start_mbps, ceiling_mbps, refresh_hz):
        start = int(start_mbps) if start_mbps else None
        ceiling = int(ceiling_mbps) if ceiling_mbps else start
        if start is not None and ceiling is not None:
            start = min(start, ceiling)
        self.active = start is not None
        self.mbps, self.ceiling = start, ceiling
        self.floor = floor_mbps(ceiling) if ceiling else None
        if self.active and self.floor > self.mbps:
            self.floor = self.mbps
        rate = _number(refresh_hz, 75.0) or 75.0
        self.interval_ms = 1000.0 / max(30.0, min(240.0, rate))
        self.warmup = WARMUP_S
        self.hold = 0
        self.clean = 0
        self.window = deque(maxlen=WINDOW_S)
        self.previous = None
        self.steps_down = self.steps_up = 0
        self.reason = ''
        self.history = deque(maxlen=12)
        self.up_after = CLEAN_UP_S
        self.check = None          # a step down for decoding alone, until it is known whether it helped
        self.decode_bound = False  # decoding time follows the picture size here, not the bitrate
        # 1.0.0.10: the same for uneven arrival (the network's timing here, not the bitrate's).
        self.arrival = deque(maxlen=ARRIVAL_HISTORY_S)
        self.arrival_check = None
        self.arrival_bound = False

    def _counter(self, values, name):
        value = _number(values.get(name))
        return int(value) if value is not None and value >= 0 else None

    def _delta(self, now, name):
        before = (self.previous or {}).get(name)
        value = now.get(name)
        if value is None or before is None or value < before:
            return 0
        return value - before

    def classify(self, timings):
        """One second's state: None when not streaming, else a dict of what was seen."""
        cadence = timings.get('cadence')
        packet_fps = _number(timings.get('packet_fps'), 0.0)
        if cadence not in STREAMING or packet_fps < 1:
            return None
        counters = {name: self._counter(timings, name)
                    for name in ('net_lost_frames_total', 'late_frame_timeouts', 'fused_skipped')}
        lost = self._delta(counters, 'net_lost_frames_total')
        timeouts = self._delta(counters, 'late_frame_timeouts')
        self.previous = counters
        decode_mean = _number(timings.get('decode_sync_complete_mean_ms'), 0.0)
        decode_p95 = _number(timings.get('decode_sync_complete_p95_ms'), 0.0)
        arrival_p95 = _number(timings.get('packet_interval_p95_ms'), 0.0)
        decode_slow = (not self.decode_bound and (decode_mean > DECODE_MEAN_SHARE * self.interval_ms
                                                  or decode_p95 > DECODE_P95_SHARE * self.interval_ms))
        # 1.0.0.10: not while it is known to be the network's timing, nor in a second with too few frames.
        uneven = (not self.arrival_bound and cadence != 'source_limited'
                  and arrival_p95 > ARRIVAL_P95_SHARE * self.interval_ms)
        late = cadence in LATE or timeouts >= LATE_TIMEOUTS
        return {'lost': lost, 'decode_slow': decode_slow, 'uneven': uneven, 'late': late,
                'decode_ms': decode_mean, 'arrival_ms': arrival_p95 if cadence != 'source_limited' else 0.0,
                'trouble': bool(lost) or decode_slow or (late and uneven)}

    def observe(self, timings):
        if not self.active or not isinstance(timings, dict):
            return None
        second = self.classify(timings)
        if second is None:
            self.previous = None  # counters restart with the next stream
            return None
        if self.warmup > 0:
            self.warmup -= 1
            return None
        self.window.append(second)
        if second['arrival_ms'] > 0:
            self.arrival.append(second['arrival_ms'])
        self.clean = 0 if second['trouble'] else self.clean + 1
        if self.check is not None:
            return self._follow_check(second)
        if self.arrival_check is not None:
            return self._follow_arrival_check(second)
        if self.hold > 0:
            self.hold -= 1
            return None
        losses = sum(item['lost'] for item in self.window)
        troubled = sum(item['trouble'] for item in self.window)
        if (losses >= LOSSES or troubled >= TROUBLE_S) and self.mbps > self.floor:
            decoding = sum(item['decode_slow'] for item in self.window)
            uneven = sum(item['late'] and item['uneven'] for item in self.window)
            reasons = [name for name, count in (('frames lost', losses), ('decoding behind', decoding),
                                                ('frames arriving unevenly', uneven)) if count]
            # Decoding alone: no losses to speak of, and too few troubled seconds without it.
            alone = losses < LOSSES and sum(bool(item['lost']) or (item['late'] and item['uneven'])
                                            for item in self.window) < TROUBLE_S
            before = [item['decode_ms'] for item in self.window if item['decode_ms'] > 0]
            arrival = list(self.arrival)  # this bitrate's seconds (_change starts them afresh)
            previous = self.mbps
            changed = self._change(max(self.floor, _round5(self.mbps * STEP_DOWN)), 'down', ', '.join(reasons))
            if changed is not None and alone and decoding and before:
                self.check = {'from': previous, 'before': sum(before) / len(before), 'seconds': 0, 'after': []}
            elif changed is not None and uneven and not losses and not decoding and len(arrival) >= ARRIVAL_CHECK_MIN_S:
                # 1.0.0.10: for uneven arrival alone; checked against this bitrate's usual spread, not just the
                # troubled seconds that triggered the step (those would look better afterwards in any case).
                self.arrival_check = {'from': previous, 'before': statistics.median(arrival), 'seconds': 0, 'after': []}
            return changed
        if self.clean >= self.up_after and self.mbps < self.ceiling:
            return self._change(min(self.ceiling, max(self.mbps + 5, _round5(self.mbps * STEP_UP))), 'up',
                                'running clean')
        return None

    def _follow_check(self, second):
        """After a step down for decoding alone: did decoding get faster? If not, the bitrate goes back."""
        check = self.check
        check['seconds'] += 1
        self.hold = max(0, self.hold - 1)
        if check['seconds'] > CHECK_SETTLE_S and second['decode_ms'] > 0:
            check['after'].append(second['decode_ms'])
        if len(check['after']) < CHECK_S:
            if check['seconds'] > CHECK_SETTLE_S + 3 * CHECK_S:  # no decoding times: leave it as it is
                self.check = None
            return None
        self.check = None
        after = sum(check['after']) / len(check['after'])
        if after <= CHECK_GAIN * check['before']:
            return None
        self.decode_bound = True
        self.window.clear()
        return self._change(check['from'], 'up', 'decoding was no faster at a lower bitrate (%.1f against %.1f ms), '
                            'so it is left to the picture size' % (after, check['before']))

    def _follow_arrival_check(self, second):
        """1.0.0.10: after a step down for uneven arrival alone: do frames arrive more evenly? If not, the
        bitrate goes back up and uneven arrival no longer counts for this session."""
        check = self.arrival_check
        check['seconds'] += 1
        self.hold = max(0, self.hold - 1)
        if check['seconds'] > CHECK_SETTLE_S and second['arrival_ms'] > 0:
            check['after'].append(second['arrival_ms'])
        if len(check['after']) < ARRIVAL_CHECK_S:
            if check['seconds'] > CHECK_SETTLE_S + 3 * ARRIVAL_CHECK_S:  # too few streaming seconds: leave it
                self.arrival_check = None
            return None
        self.arrival_check = None
        after = statistics.median(check['after'])
        if after <= CHECK_GAIN * check['before']:
            return None
        self.arrival_bound = True
        self.window.clear()
        return self._change(check['from'], 'up', 'frames arrived no more evenly at a lower bitrate (gaps of %.1f against '
                            '%.1f ms), so uneven arrival is left to the network' % (after, check['before']))

    def _change(self, mbps, direction, reason):
        mbps = int(mbps)
        if mbps == self.mbps:
            return None
        self.history.append({'from': self.mbps, 'to': mbps, 'reason': reason})
        self.mbps, self.reason = mbps, reason
        self.window.clear()
        self.arrival.clear()
        self.clean = 0
        if direction == 'down':
            self.steps_down += 1
            self.hold = HOLD_DOWN_S
            self.up_after = min(CLEAN_UP_MAX_S, CLEAN_UP_S * 2 ** max(0, self.steps_down - 1))
        else:
            self.steps_up += 1
            self.hold = HOLD_UP_S
        return mbps

    def snapshot(self):
        return {'active': self.active, 'mbps': self.mbps, 'ceiling': self.ceiling, 'floor': self.floor,
                'steps_down': self.steps_down, 'steps_up': self.steps_up, 'reason': self.reason,
                'decode_bound': self.decode_bound, 'arrival_bound': self.arrival_bound, 'history': list(self.history)}
