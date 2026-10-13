"""Rolling summary of ALVR's per-frame latency breakdown (GraphStatistics events).

The pinned host emits one GraphStatistics event per presented frame. Each event splits
the client-measured total (tracking sample to the headset display slot) into host render,
encode, network, client decode and client display wait. This keeps a short window and
reduces it to a small, bounded summary that the session heartbeat carries to the client.

`client_compositor_s` is the time from a decoded frame entering the client compositor
path to its submission at the headset's frame pickup. Modulo the refresh interval it is
where host frames land in the headset's frame, so its circular mean and spread show
whether frames arrive just in time (small, steady) or wait for most of a frame.
"""
import math
import threading
import time
from collections import deque

FIELDS = ('total_pipeline_latency_s', 'game_time_s', 'server_compositor_s', 'encoder_s', 'network_s',
          'decoder_s', 'decoder_queue_s', 'client_compositor_s', 'vsync_queue_s', 'client_fps',
          'server_fps', 'bitrate_bps')


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


class PipelineStats:
    def __init__(self, window_s=5.0, max_samples=1024, clock=time.monotonic):
        self.window_s, self.clock = window_s, clock
        self.samples = deque(maxlen=max_samples)
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.samples.clear()

    def add(self, values):
        if not isinstance(values, dict):
            return False
        row = {}
        for key in FIELDS:
            value = values.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return False
            row[key] = float(value)
        if row['total_pipeline_latency_s'] > 2 or row['client_fps'] > 1000:
            return False
        with self.lock:
            self.samples.append((self.clock(), row))
        return True

    def summary(self, minimum=10):
        now = self.clock()
        with self.lock:
            rows = [row for stamp, row in self.samples if now - stamp <= self.window_s]
        if len(rows) < minimum:
            return None
        def ms(values):
            return round(1000 * sum(values) / len(values), 2)
        total = [r['total_pipeline_latency_s'] for r in rows]
        fps = sorted(r['client_fps'] for r in rows if r['client_fps'] > 0)
        interval = 1 / fps[len(fps) // 2] if fps else 0
        arrival = None
        if 0.004 < interval < 0.05:
            angles = [2 * math.pi * (r['client_compositor_s'] % interval) / interval for r in rows]
            c = sum(math.cos(a) for a in angles) / len(angles)
            s = sum(math.sin(a) for a in angles) / len(angles)
            concentration = min(1.0, math.hypot(c, s))
            mean = (math.atan2(s, c) % (2 * math.pi)) * interval / (2 * math.pi)
            spread = math.sqrt(max(0.0, -2 * math.log(concentration))) * interval / (2 * math.pi) if concentration > 1e-6 else interval
            arrival = {'wait_ms': round(1000 * mean, 2), 'spread_ms': round(1000 * spread, 2),
                       'interval_ms': round(1000 * interval, 3), 'concentration': round(concentration, 3)}
        return {
            'window_s': self.window_s,
            'samples': len(rows),
            'total_ms': {'mean': ms(total), 'p95': round(1000 * _percentile(total, .95), 2)},
            'stages_ms': {
                'render': ms([r['game_time_s'] + r['server_compositor_s'] for r in rows]),
                'encode': ms([r['encoder_s'] for r in rows]),
                'network': ms([r['network_s'] for r in rows]),
                'decode': ms([r['decoder_s'] + r['decoder_queue_s'] for r in rows]),
                'display_wait': ms([r['client_compositor_s'] + r['vsync_queue_s'] for r in rows]),
            },
            'host_fps': round(sum(r['server_fps'] for r in rows) / len(rows), 1),
            'client_fps': round(sum(r['client_fps'] for r in rows) / len(rows), 1),
            'bitrate_mbps': round(sum(r['bitrate_bps'] for r in rows) / len(rows) / 1e6, 1),
            'arrival': arrival,
        }
