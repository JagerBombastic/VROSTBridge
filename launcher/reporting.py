"""Session reports: naming, the host's copy of client reports, and remote session data.

RC4.2: every report carries `report_info` (which end saved it, version, local time)
and a file name built from the same values, for example
VROSTBridge-Client-report_1.0.0.3_2026-10-06_19-45-12.json. A client can send
its report to the host companion over the paired session link; the host keeps the
latest ones under reports/received for download from the host page.
"""
import copy
import gzip
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

REPORT_NAME = re.compile(r'VROSTBridge-(Client|Host)-report_[0-9A-Za-z.+-]{1,40}_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}'
                         r'(?:-\d{1,3})?\.json')
# The name a client sends: no "-N" suffix, which only the host adds to keep two reports apart.
SENT_NAME = re.compile(r'VROSTBridge-Client-report_[0-9A-Za-z.+-]{1,40}_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json')
NAME_PREFIX = 'VROSTBridge-'
# 1.0.0.9: the newest three only (the host page listed twenty, most of them out of date).
KEEP_RECEIVED = 3
# A client report is about 1.5 MB of JSON (about 140 KB compressed); the caps leave
# ample room while bounding what a paired peer can make the host decode and keep.
DECODED_LIMIT = 16 * 1024 * 1024
KEEP_BYTES = 96 * 1024 * 1024


def report_name(role, version, when=None):
    """File name for a report saved now (or at `when`, epoch seconds) on this end."""
    stamp = time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(time.time() if when is None else when))
    end = 'Client' if role == 'client' else 'Host'
    safe = re.sub(r'[^0-9A-Za-z.+-]', '-', str(version))[:40] or 'unknown'
    return NAME_PREFIX + end + '-report_' + safe + '_' + stamp + '.json'


def report_info(role, version, when=None):
    when = time.time() if when is None else when
    local = time.localtime(when)
    offset = time.strftime('%z', local)
    return {'end': 'client' if role == 'client' else 'host', 'version': str(version),
            'saved_local': time.strftime('%Y-%m-%d %H:%M:%S', local) + (' ' + offset if offset else ''),
            'saved_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(when)),
            'file_name': report_name(role, version, when)}


def stamped(report, role, version, when=None):
    """The report with report_info first (readers see which end saved it at once)."""
    info = report_info(role, version, when)
    result = {'report_info': info}
    result.update({key: value for key, value in report.items() if key != 'report_info'})
    return result


def encode(report):
    """gzip-compressed JSON (for sending a report over the session link)."""
    return gzip.compress(json.dumps(report, separators=(',', ':')).encode('utf-8'), 6)


def inflate(data, limit=DECODED_LIMIT):
    """The JSON bytes inside encode()'s output; bounded so a peer cannot exhaust memory."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('The report is too large.')
    return raw


def parse(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('The report is not a JSON object.')
    return value


def decode(data, limit=DECODED_LIMIT):
    """Inverse of encode()."""
    return parse(inflate(data, limit))


class ReceivedReports:
    """Client reports the host companion received, newest first.

    At most KEEP_RECEIVED files and KEEP_BYTES in total (the newest is always kept).
    A received report is stored exactly as decoded (compact JSON, so never more than
    DECODED_LIMIT); the host page indents a report when it is downloaded.
    """

    def __init__(self, directory, keep=KEEP_RECEIVED, keep_bytes=KEEP_BYTES):
        self.directory = Path(directory)
        self.keep, self.keep_bytes = keep, keep_bytes
        self.lock = threading.Lock()

    def save(self, name, report):
        """report: the decoded JSON bytes as received (stored unchanged), or a dict."""
        if not isinstance(name, str) or not SENT_NAME.fullmatch(name):
            raise ValueError('Invalid report name.')
        raw = bytes(report) if isinstance(report, (bytes, bytearray)) else json.dumps(report, separators=(',', ':')).encode('utf-8')
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / name
            counter = 1
            while target.exists() and counter < 100:  # never overwrite an earlier report
                target = self.directory / (name[:-5] + '-' + str(counter) + '.json')
                counter += 1
            # Every stored name is one the list, the pruning and downloads recognise.
            if target.exists() or not REPORT_NAME.fullmatch(target.name):
                raise ValueError('Too many reports with this name; try again in a moment.')
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.receiving-')
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            self._prune(target)
            return target.name

    def prune(self):
        """Drops what is beyond the limits now (1.0.0.9: the host companion does this when it starts, so a
        folder kept from an earlier version shrinks at once rather than at the next report)."""
        with self.lock:
            files = self._files()
            if files:
                self._prune(files[0])

    def _prune(self, newest):
        total = 0
        for index, path in enumerate(self._files()):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            if path != newest and (index >= self.keep or total > self.keep_bytes):
                path.unlink(missing_ok=True)
                total -= size

    def _files(self):
        try:
            files = [path for path in self.directory.iterdir() if path.is_file() and REPORT_NAME.fullmatch(path.name)]
        except OSError:
            return []
        return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)

    def list(self):
        with self.lock:
            rows = []
            for path in self._files()[:self.keep]:
                try:
                    info = path.stat()
                except OSError:
                    continue
                rows.append({'name': path.name, 'bytes': info.st_size, 'received': int(info.st_mtime)})
            return rows

    def read(self, name):
        if not REPORT_NAME.fullmatch(name or ''):
            raise ValueError('Invalid report name.')
        with self.lock:
            path = self.directory / name
            if not path.is_file():
                raise ValueError('That report is no longer available.')
            return path.read_bytes()


GAZE_CENTRES = re.compile(r' (?:left|right)=[^ ]*')


def filtered_driver_log(path, keep_bytes=131072, window=4 * 1024 * 1024, drop=(), keep_stats=120):
    """The end of the host driver's session_log.txt without its per-frame noise.

    ALVR writes a "[GRAPH]" line per frame; in RC4.1 reports they were 92% of the
    log's last 128 KiB and pushed every other line out. Those lines are dropped,
    only the latest `keep_stats` "[STATS]" lines are kept, and the last `keep_bytes`
    of what remains is returned.
    """
    path = Path(path)
    with path.open('rb') as source:
        source.seek(max(0, path.stat().st_size - window))
        text = source.read().decode('utf-8', errors='replace')
    lines = [line for line in text.splitlines() if '[GRAPH]' not in line and not any(marker in line for marker in drop)]
    # RC4.3: the gaze-following foveation centres say where the user looked: reports keep the counts only.
    lines = [GAZE_CENTRES.sub('', line) if 'VROST_GAZE_FOVEATION' in line else line for line in lines]
    stats = [index for index, line in enumerate(lines) if '[STATS]' in line]
    if len(stats) > keep_stats:
        removed = set(stats[:-keep_stats])
        lines = [line for index, line in enumerate(lines) if index not in removed]
    result = '\n'.join(lines)
    return result[-keep_bytes:] if len(result) > keep_bytes else result


def attach_remote_session(report, remote):
    result = copy.deepcopy(report)
    result['remote_diagnostics'] = remote
    result['host_session'] = result.get('session', {})
    client = remote.get('client_report', {})
    session = client.get('session') if isinstance(client, dict) else None
    if isinstance(session, dict) and client.get('role') == 'client':
        result['session'] = copy.deepcopy(session)
        result['session'].update(
            available=True, source='remote_client',
            report_scope=('current_connection' if remote.get('client_report_current')
                          else 'last_captured_client_connection'),
            client_report_received_utc=remote.get('client_report_received_utc'),
        )
    else:
        result['session'] = {
            'available': False, 'source': 'remote_client',
            'reason': 'No client stream report has been captured. Host runtime lifetime is recorded separately.',
        }
    return result
