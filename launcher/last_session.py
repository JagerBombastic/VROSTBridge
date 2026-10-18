"""1.0.0.14: what the host measured in its last streamed session, kept across restarts for the report.

The first 1.0.0.13 report from real hardware arrived with an empty host side: the host's VROSTBridge had been restarted
between the session and the report, which emptied the in-memory event statistics (the encoder, the frame-timing lock,
the gaze region, the pipeline), and SteamVR's restart had rotated ALVR's session log away. The headset PC's report,
carried inside, had the answers that time; the host's own evidence of an hour's session was gone.

The host companion now writes a record of the session as it runs (every minute, and when the session stops): the
event statistics and the pipeline summary as HostEvents has them, the adapters' counters, the session's own summary,
and the driver's VROST_* lines from ALVR's session log, which are its per-second statistics. A report made with no
session running since the companion started carries that record as `host_last_session`, with when it was saved.
It is one JSON file in the state folder, replaced in place, never more than a couple of megabytes.
"""
import json
import os
from pathlib import Path
import re
import time

FILE_NAME = 'host-last-session.json'
INTERVAL_S = 60
MAX_LOG_LINES = 600
MAX_BYTES = 2 * 1024 * 1024
STATISTIC = re.compile(r'VROST_[A-Z_]+ ')


def driver_statistics(path, limit=MAX_LOG_LINES):
    """The driver's VROST_* lines from ALVR's session log, the last `limit` of them, without per-sample tracking data."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    lines = [line.rstrip() for line in text.splitlines() if STATISTIC.search(line)
             and not any(word in line for word in ('"Tracking"', 'eyes_combined', 'hand_skeletons', 'VROST_NATIVE_HAND'))]
    return lines[-limit:]


class LastSessionRecord:
    def __init__(self, directory, clock=time.monotonic):
        self.path = Path(directory) / FILE_NAME
        self.clock = clock
        self.written = None

    def due(self):
        return self.written is None or self.clock() - self.written >= INTERVAL_S

    def keep(self, record):
        """Write the record (a dict) atomically; a record too large to be useful is cut to its statistics."""
        payload = dict(record, saved_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), saved_time=time.time())
        text = json.dumps(payload, default=str)  # a stand-in object in a test, never anything the report needs
        if len(text) > MAX_BYTES:
            payload.pop('driver_statistics', None)
            text = json.dumps(payload, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name('.' + self.path.name + '.tmp')
            temporary.write_text(text, encoding='utf-8')
            os.replace(temporary, self.path)
            self.written = self.clock()
            return True
        except OSError:
            return False

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and 'saved_utc' in value else None
