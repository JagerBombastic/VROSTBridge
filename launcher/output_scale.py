"""Show the picture a little smaller while this PC's graphics hold the stream below the headset's refresh (1.0.0.11).

When the frame pass and SteamVR's compositor on the headset PC miss the refresh, SteamVR shows the stream at
half rate and reprojects it, which judders far more than a slightly softer picture. The native client can show
each eye at a percentage of its size from the next frame on ('scale N'); the frame pass's work falls with the
pixel count (90 %: 81 %, 80 %: 64 %), the stream itself does not change. This keeps a short window of the
client's TIMING seconds:

- a second is behind when SteamVR showed the stream below refresh while frames arrived at refresh and the
  headset was tracked with input focus (as GraphicsLimit counts it) and the GPU had little room: SteamVR's GPU
  time for its frame was at least BEHIND_GPU_SHARE of the refresh interval. Behind seconds with GPU room to
  spare, or without SteamVR's GPU time, are not known to be the GPU's, and a smaller picture might not help;
- DOWN_SECONDS behind seconds within DOWN_WINDOW_S: one step smaller (100 -> 90 -> 80 %), then SETTLE_S to
  settle; the seconds while it settles (the size still changing hands) do not count towards the next step;
- UP_AFTER_S seconds without one while SteamVR's GPU time stays under UP_GPU_SHARE of the interval: one step
  back up. So a PC at its limit does not alternate, a step up that falls behind again within RETRY_WINDOW_S
  steps back down for the rest of the session, and one that falls behind within ROUND_TRIP_S doubles the wait
  before the next step up (up to UP_WAIT_MAX_S); after ROUND_TRIPS of those the size stays down too.
"""
from collections import deque

LEVELS = (100, 90, 80)
WARMUP_S = 5            # streaming seconds ignored at the start (first keyframes, SteamVR settling)
DOWN_WINDOW_S = 10
DOWN_SECONDS = 3
SETTLE_S = 10
UP_AFTER_S = 60
UP_GPU_SHARE = .6
BEHIND_GPU_SHARE = .75
RETRY_WINDOW_S = 30
ROUND_TRIP_S = 300      # a step up that falls behind within this was not worth it: wait longer next time
ROUND_TRIPS = 3
UP_WAIT_MAX_S = 480
CADENCES = ('idle', 'full', 'client_throttled', 'source_limited', 'late_or_paired')


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) < 1e9 else None


def gpu_share(timings):
    """SteamVR's GPU time for its last frame (the client's pass and its compositor) as a share of the refresh
    interval, or None where it is not reported."""
    gpu, refresh = _number(timings.get('gpu_ms')), _number(timings.get('refresh_hz'))
    if gpu is None or gpu <= 0 or refresh is None or refresh < 30:
        return None
    return gpu / (1000.0 / refresh)


class OutputScale:
    """The shown size's percentage for this session; observe() one TIMING second at a time."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.level = LEVELS[0]
        self.window = deque(maxlen=DOWN_WINDOW_S)
        self.warmup = WARMUP_S
        self.settle = 0
        self.clean = []           # GPU shares of the seconds since the last behind second (None where unknown)
        self.since_up = None      # seconds since the last step up
        self.up_wait = UP_AFTER_S  # steady seconds before a step back up (doubled after each round trip)
        self.round_trips = 0
        self.pinned = False
        self.steps = []           # {'from', 'to', 'reason'} in order
        self.reason = ''

    def snapshot(self):
        return {'level': self.level, 'steps': len(self.steps), 'pinned': self.pinned, 'reason': self.reason,
                'up_wait_s': self.up_wait, 'round_trips': self.round_trips, 'history': [dict(step) for step in self.steps[-6:]]}

    def _step(self, to, reason):
        self.steps.append({'from': self.level, 'to': to, 'reason': reason})
        self.level, self.reason = to, reason
        self.window.clear()
        self.clean = []
        self.settle = SETTLE_S
        return to

    def observe(self, timings, frames_arriving=True, tracked=True):
        """One TIMING second; returns the new percentage when it changes, else None."""
        cadence = timings.get('cadence')
        if cadence not in CADENCES or cadence == 'idle':
            return None
        if self.warmup > 0:
            self.warmup -= 1
            return None
        share = gpu_share(timings)
        behind = (cadence == 'client_throttled' and bool(frames_arriving) and bool(tracked)
                  and share is not None and share >= BEHIND_GPU_SHARE)
        if self.since_up is not None:
            self.since_up += 1
        if behind:
            self.clean = []
        else:
            self.clean.append(share)
            if len(self.clean) > UP_WAIT_MAX_S:  # the longest wait is all that is ever looked at
                del self.clean[0]
        if self.settle > 0:
            self.settle -= 1
            return None
        self.window.append(behind)
        index = LEVELS.index(self.level)
        if sum(self.window) >= DOWN_SECONDS and index + 1 < len(LEVELS):
            if self.since_up is not None:
                if self.since_up <= RETRY_WINDOW_S:
                    self.pinned = True  # the step up did not hold: stay smaller for this session
                elif self.since_up <= ROUND_TRIP_S:  # it held a while: wait longer before the next try
                    self.round_trips += 1
                    self.up_wait = min(self.up_wait * 2, UP_WAIT_MAX_S)
                    self.pinned = self.round_trips >= ROUND_TRIPS
            self.since_up = None
            busy = (' (GPU ' + str(round(share * 100)) + '% of each refresh)') if share is not None else ''
            return self._step(LEVELS[index + 1], 'SteamVR showed the stream below refresh for ' + str(sum(self.window))
                              + ' of the last ' + str(len(self.window)) + ' s' + busy)
        if index > 0 and not self.pinned and len(self.clean) >= self.up_wait:
            known = sorted(value for value in self.clean[-self.up_wait:] if value is not None)
            if known and len(known) >= self.up_wait // 2 and known[len(known) // 2] <= UP_GPU_SHARE:
                self.since_up = 0
                return self._step(LEVELS[index - 1], 'steady for ' + str(self.up_wait) + ' s with GPU room ('
                                  + str(round(known[len(known) // 2] * 100)) + '% of each refresh)')
        return None
