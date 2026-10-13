"""The session in 10-second steps, for reports (1.0.0.4).

The 1.0.0.3 reports kept only the last minute or two of detail, so a session that was
"good, then bad, then good" could not be read back: when the stutters came, whether the
eye tracking paused then, what the processor and GPU clocks were. The headset PC's
companion now keeps one compact row per 10 seconds of the session (an hour at most;
beyond that the first half hour and the latest are kept) from what it already reads:
the native client's TIMING and METRICS lines, the performance samples, the gaze relay's
counter, the eye-camera helper's frame rate (1.0.0.5) and the host's foveation counters. Counts
only: never where the user looks. 1.0.0.11: the frame pass's GPU time, SteamVR's GPU time per frame and
the shown size when the picture was shown smaller to keep the frame rate.
"""
import threading
import time

BUCKET_S = 10
MAX_ROWS = 360
KEEP_FIRST = 180
CADENCES = ('full', 'client_throttled', 'source_limited', 'late_or_paired')


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) < 1e12 else default


class SessionTimeline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.started = self.clock()
            self.rows = []
            self.row = None
            self.dropped = 0
            self.previous = {}

    # ------------------------------------------------------------------ rows
    def _current(self, now):
        index = max(0, int((now - self.started) // BUCKET_S))
        if self.row is None or self.row['index'] != index:
            if self.row is not None:
                self._keep(self.row)
            self.row = {'index': index, 'fps': [], 'cadence': {}}
        return self.row

    def _keep(self, row):
        self.rows.append(row)
        if len(self.rows) > MAX_ROWS:  # keep the start of the session and the latest
            del self.rows[KEEP_FIRST]
            self.dropped += 1

    def _delta(self, key, total):
        """The increase of a cumulative counter since its last reading (a restart counts from zero)."""
        before = self.previous.get(key)
        self.previous[key] = total
        if before is None:
            return 0
        return total - before if total >= before else total

    # ------------------------------------------------------------------ inputs
    def timing(self, values, now=None):
        """A native TIMING line (key=value strings), once a second."""
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            if values.get('cadence') == 'idle':
                return
            row['fps'].append(_number(values.get('unique_fps')))
            # 1.0.0.9: frames rebuilt from parity, and game-audio dropouts (ran dry) and lost packets.
            for key, name in (('lost', 'net_lost_frames'), ('keyframes', 'net_keyframe_requests'),
                              ('skipped', 'net_skipped_until_keyframe'), ('repaired', 'net_fec_video'),
                              ('audio_dropouts', 'audio_underruns'), ('audio_lost', 'audio_losses')):
                row[key] = row.get(key, 0) + int(_number(values.get(name)))
            incidents = self._delta('incidents', int(_number(values.get('presentation_incidents'))))
            row['incidents'] = row.get('incidents', 0) + incidents
            for key, name in (('handoff_max_ms', 'handoff_cpu_max_ms'), ('interval_max_ms', 'packet_interval_max_ms'),
                              ('convert_max_ms', 'convert_cpu_max_ms'), ('pass_gpu_max_ms', 'frame_pass_gpu_max_ms')):
                row[key] = max(row.get(key, 0.0), _number(values.get(name)))
            # 1.0.0.11: the frame pass on the GPU (its mean for the second), SteamVR's GPU time for its frame, and the
            # picture's shown size when it was smaller than the stream's.
            if _number(values.get('frame_pass_gpu_samples')) > 0:
                row.setdefault('pass_gpu', []).append(_number(values.get('frame_pass_gpu_mean_ms')))
            if _number(values.get('gpu_ms')) > 0:
                row.setdefault('steamvr_gpu', []).append(_number(values.get('gpu_ms')))
            scale = _number(values.get('shown_scale'), 100.0)
            if 0 < scale < 100:
                row['shown_scale'] = min(row.get('shown_scale', 100.0), scale)
            cadence = values.get('cadence')
            if cadence in CADENCES:
                row['cadence'][cadence] = row['cadence'].get(cadence, 0) + 1

    def metrics(self, values, now=None):
        """A native METRICS line: grey (neutral) frames and the oldest frame shown."""
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            row['grey_frames'] = row.get('grey_frames', 0) + self._delta('fallback', int(_number(values.get('fallback'))))
            row['frame_age_max_ms'] = max(row.get('frame_age_max_ms', 0), int(_number(values.get('frame_age_ms'))))
            # 1.0.0.5: tracking ticks without a head pose (the headset not tracked here; no video comes back).
            row['head_missing'] = row.get('head_missing', 0) + int(_number(values.get('head_poses_missing')))

    def performance(self, sample, now=None):
        """A performance-monitor sample (every 5 s): clocks, load, power and the busiest VR programs."""
        if not isinstance(sample, dict) or not sample.get('active'):
            return
        now = self.clock() if now is None else now
        clocks = sorted(p['reported_frequency_mhz'] for p in sample.get('cpu_policies') or []
                        if isinstance(p, dict) and isinstance(p.get('reported_frequency_mhz'), (int, float)))
        gpus = [g for g in sample.get('gpus') or [] if isinstance(g, dict) and isinstance(g.get('gpu_busy_percent'), (int, float))]
        with self.lock:
            row = self._current(now)
            # 1.0.0.7: Windows reports the processor's base clock as its current one; its effective clock (from the
            # '% Processor Performance' counter) is the one that moves.
            effective = sample.get('cpu_effective_mhz')
            if isinstance(effective, (int, float)) and effective > 0:
                row.setdefault('cpu_mhz', []).append(effective)
            elif clocks:
                row.setdefault('cpu_mhz', []).append(clocks[len(clocks) // 2])
            if gpus:
                gpu = max(gpus, key=lambda g: g['gpu_busy_percent'])
                sensors = [s for s in gpu.get('sensors') or [] if isinstance(s, dict)]
                row.setdefault('gpu_busy', []).append(gpu['gpu_busy_percent'])
                clock = next((s['clock_mhz'] for s in sensors if isinstance(s.get('clock_mhz'), (int, float))),
                             gpu.get('selected_sclk_mhz'))
                if isinstance(clock, (int, float)):
                    row.setdefault('gpu_mhz', []).append(clock)
                for key, name in (('power_w', 'average_power_w'), ('temp_c', 'temperature_c')):
                    value = next((s[name] for s in sensors if isinstance(s.get(name), (int, float))), None)
                    if value is not None:
                        row.setdefault(key, []).append(value)
            processes = sample.get('processes')
            if isinstance(processes, dict) and processes:
                row['processes'] = {name: round(_number(value), 1) for name, value in list(processes.items())[:12]}
            busy = sample.get('cpu_busy_percent')
            if isinstance(busy, (int, float)):
                row.setdefault('cpu_busy', []).append(busy)

    def gaze(self, received, now=None):
        """The gaze relay's cumulative sample count (once a second while eye tracking runs)."""
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            row['gaze_samples'] = row.get('gaze_samples', 0) + self._delta('gaze', int(_number(received)))

    def camera(self, feed, now=None):
        """1.0.0.5: the eye-camera helper's STATS values (refreshed every 5 s): its frame rate and losses."""
        if not isinstance(feed, dict):
            return
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            if 'fps' in feed:
                row.setdefault('camera_fps', []).append(_number(feed.get('fps')))
            for key, name in (('camera_lost', 'damaged_frames'), ('camera_reopened', 'reopened')):
                if name in feed:
                    row[key] = row.get(key, 0) + self._delta(key, int(_number(feed.get(name))))

    def controllers(self, inputs, now=None):
        """1.0.0.6: the native client's controller prediction check (INPUT lines, once a second): the largest
        distance between where the stream's prediction put a controller and where it was, per hand."""
        if not isinstance(inputs, dict):
            return
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            for hand in ('left', 'right'):
                check = (inputs.get(hand) or {}).get('prediction') if isinstance(inputs.get(hand), dict) else None
                if isinstance(check, dict) and _number(check.get('samples')) > 0:
                    key = 'predict_max_mm_' + hand[0]
                    row[key] = max(row.get(key, 0.0), _number(check.get('max_mm')))

    def host_gaze(self, gaze_frames, held_frames, fallback_frames, now=None):
        """The host's foveation counters (cumulative per connection): frames on the gaze, held, centred."""
        now = self.clock() if now is None else now
        with self.lock:
            row = self._current(now)
            for key, total in (('host_gaze', gaze_frames), ('host_held', held_frames), ('host_centred', fallback_frames)):
                row[key] = row.get(key, 0) + self._delta(key, int(_number(total)))

    # ------------------------------------------------------------------ output
    @staticmethod
    def _compact(row):
        result = {'t_s': row['index'] * BUCKET_S}
        fps = row.get('fps') or []
        if fps:
            result.update(fps_avg=round(sum(fps) / len(fps), 1), fps_min=round(min(fps), 1))
        for key in ('lost', 'keyframes', 'skipped', 'repaired', 'audio_dropouts', 'audio_lost', 'incidents', 'grey_frames',
                    'frame_age_max_ms', 'gaze_samples', 'host_gaze', 'host_held', 'host_centred', 'head_missing',
                    'camera_lost', 'camera_reopened'):
            if row.get(key):
                result[key] = row[key]
        if row.get('gaze_samples'):
            result['gaze_hz'] = round(row['gaze_samples'] / BUCKET_S, 1)
        for key in ('handoff_max_ms', 'interval_max_ms', 'convert_max_ms', 'predict_max_mm_l', 'predict_max_mm_r',
                    'pass_gpu_max_ms'):
            if row.get(key):
                result[key] = round(row[key], 1)
        for key, name in (('pass_gpu', 'pass_gpu_ms'), ('steamvr_gpu', 'steamvr_gpu_ms')):
            values = row.get(key)
            if values:
                result[name] = round(sum(values) / len(values), 2)
        if row.get('shown_scale'):
            result['shown_scale'] = int(row['shown_scale'])
        for key in ('cpu_mhz', 'gpu_mhz', 'gpu_busy', 'power_w', 'temp_c', 'cpu_busy', 'camera_fps'):
            values = row.get(key)
            if values:
                result[key] = round(sum(values) / len(values), 1)
        if row.get('cadence'):
            result['cadence'] = dict(row['cadence'])
        if row.get('processes'):
            result['processes'] = dict(row['processes'])
        return result

    def snapshot(self):
        with self.lock:
            rows = list(self.rows) + ([self.row] if self.row is not None else [])
            return {'step_s': BUCKET_S, 'rows_dropped': self.dropped, 'rows': [self._compact(row) for row in rows]}
