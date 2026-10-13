"""Eye-tracked foveation panel (RC4.3): where each eye looks and where the sharp region is.

The native client prints a FOVEA line thirty times a second (1.0.0.3; ten before) while a
foveated stream plays:
the full-resolution region of the frame being shown (its size and its centre in each eye's
image, from the centres the host used for that frame), how much the rest is reduced, the
stream sizes and, while eye tracking delivers fresh samples, the gaze point in each eye.

These describe where the user is looking. They are kept in memory only, for the pages'
live view: the companion never logs, journals or reports them, and a client shares them
only with the host it is streaming from (session link /fovea, RC4.3 hosts), which keeps
them in memory as well.
"""
import math
import time

POINTS = ('left', 'right', 'gaze_left', 'gaze_right')
REQUIRED = ('left', 'right', 'region', 'edge', 'encoded', 'eye')
FRAME_PASSES = ('fused', 'two-pass', 'cpu')
SHARPENING = ('off', 'low', 'medium', 'high')
LIVE_S = 1.5  # a view older than this is not live
KEYS = set(POINTS) | {'region', 'edge', 'encoded', 'eye', 'gaze_age_ms', 'gaze_share', 'saving_percent',
                      'frame_pass', 'sharpening', 'gaze_rate'}


def _number(item):
    """A finite JSON number as float; ValueError for anything else (including huge integers)."""
    if type(item) not in (int, float):
        raise ValueError('Invalid eye-tracking view.')
    try:
        value = float(item)
    except OverflowError:
        raise ValueError('Invalid eye-tracking view.') from None
    if not math.isfinite(value):
        raise ValueError('Invalid eye-tracking view.')
    return value


def _pair(value, low, high):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('Invalid eye-tracking view.')
    numbers = [_number(item) for item in value]
    if not all(low <= item <= high for item in numbers):
        raise ValueError('Invalid eye-tracking view.')
    return [round(item, 4) for item in numbers]


def _size(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(type(item) is int and 16 <= item <= 16384 for item in value):
        raise ValueError('Invalid eye-tracking view.')
    return [int(item) for item in value]


def view(value):
    """A validated live view (raises ValueError): positions and region sizes 0..1, edge
    ratios 1..8, pixel sizes, and optional statistics."""
    if not isinstance(value, dict) or set(value) - KEYS or not set(REQUIRED) <= set(value):
        raise ValueError('Invalid eye-tracking view.')
    result = {key: _pair(value[key], 0.0, 1.0) for key in ('left', 'right')}
    result['region'] = _pair(value['region'], 0.05, 1.0)  # ALVR's centre size is at least 0.2
    result['edge'] = _pair(value['edge'], 1.0, 8.0)
    result['encoded'], result['eye'] = _size(value['encoded']), _size(value['eye'])
    for key in ('gaze_left', 'gaze_right'):
        if value.get(key) is not None:
            result[key] = _pair(value[key], 0.0, 1.0)
    if ('gaze_left' in result) != ('gaze_right' in result):
        raise ValueError('Invalid eye-tracking view.')
    age = value.get('gaze_age_ms')
    if age is not None:
        if type(age) is not int or not 0 <= age <= 10000:
            raise ValueError('Invalid eye-tracking view.')
        result['gaze_age_ms'] = age
    share = value.get('gaze_share')
    if share is not None:
        share = _number(share)
        if not 0 <= share <= 1:
            raise ValueError('Invalid eye-tracking view.')
        result['gaze_share'] = round(share, 3)
    saving = value.get('saving_percent')
    if saving is not None:
        if type(saving) is not int or not 0 <= saving <= 100:
            raise ValueError('Invalid eye-tracking view.')
        result['saving_percent'] = saving
    rate = value.get('gaze_rate')
    if rate is not None:
        rate = _number(rate)
        if not 0 <= rate <= 2000:
            raise ValueError('Invalid eye-tracking view.')
        result['gaze_rate'] = round(rate, 1)
    if value.get('frame_pass') is not None:
        if value['frame_pass'] not in FRAME_PASSES:
            raise ValueError('Invalid eye-tracking view.')
        result['frame_pass'] = value['frame_pass']
    if value.get('sharpening') is not None:
        if value['sharpening'] not in SHARPENING:
            raise ValueError('Invalid eye-tracking view.')
        result['sharpening'] = value['sharpening']
    return result


def parse_line(line):
    """The view in a native FOVEA line, or None for anything else (never raises)."""
    if not isinstance(line, str) or not line.startswith('FOVEA '):
        return None
    values = {}
    for item in line.split()[1:16]:
        key, separator, raw = item.partition('=')
        if not separator:
            continue
        try:
            if key in POINTS or key in ('region', 'edge'):
                first, second = raw.split(',', 1)
                values[key] = [float(first), float(second)]
            elif key in ('encoded', 'eye'):
                width, height = raw.split('x', 1)
                values[key] = [int(width), int(height)]
            elif key == 'gaze_age_ms':
                values[key] = int(raw)
        except ValueError:
            continue
    try:
        return view(values)
    except ValueError:
        return None


class LiveView:
    """The latest view with its arrival time (thread-safe enough: one reference swap)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.latest = None

    def update(self, value):
        self.latest = (self.clock(), dict(value))

    def clear(self):
        self.latest = None

    def current(self, extra=None):
        """{'live': bool, 'age_ms': .., **view}; live only while fresh."""
        latest = self.latest
        if not latest:
            return {'live': False}
        age = self.clock() - latest[0]
        if age > LIVE_S:
            return {'live': False}
        result = dict(latest[1], live=True, age_ms=int(age * 1000))
        if extra:
            result.update(extra)
        return result
