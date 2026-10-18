"""1.0.0.13: the network adapters' own drop and error counters over a session, for the reports.

1.0.0.12's first session lost frames in clumps on a wired link, and nothing said where: on the rendering PC's
adapter, on the way, or on the headset PC's (a USB adapter there). Each companion now notes its physical adapters'
counters when a session starts and reports what they gained since: received and sent packets (so the adapter that
carried the stream is plain to see) and every drop and error counter that moved. On Linux also the system's UDP
receive errors (a socket that overflowed) and, for the stream's adapter, its driver's own drop counters (ethtool).
A counter an adapter does not keep is left out. The host reads the headset PC's report during the session, so the
figures are measured when a report is made, and kept as they were when the session ended.
"""
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time

LINUX_COUNTERS = ('rx_packets', 'rx_dropped', 'rx_missed_errors', 'rx_fifo_errors', 'rx_over_errors', 'rx_errors',
                  'rx_crc_errors', 'rx_frame_errors', 'rx_length_errors', 'tx_packets', 'tx_dropped', 'tx_errors',
                  'tx_fifo_errors', 'tx_carrier_errors')
PACKETS = ('rx_packets', 'tx_packets')
UDP_COUNTERS = ('InErrors', 'RcvbufErrors', 'SndbufErrors', 'InCsumErrors', 'NoPorts')
DRIVER_COUNTER = re.compile(r'drop|miss|err|fifo|over|discard|fail|full|lost', re.I)
MAX_ADAPTERS = 6
MAX_DRIVER = 16


def linux_adapters(root='/sys/class/net'):
    """{name: counters} for each adapter with a device behind it (loopback, bridges and tunnels have none)."""
    base = Path(root)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}
    result = {}
    for name in names:
        folder = base / name
        if not (folder / 'device').exists():
            continue
        values = {}
        for counter in LINUX_COUNTERS:
            try:
                values[counter] = int((folder / 'statistics' / counter).read_text().strip())
            except (OSError, ValueError):
                pass
        if values:
            values['usb'] = '/usb' in os.path.realpath(folder / 'device')
            result[name[:64]] = values
            if len(result) >= MAX_ADAPTERS:
                break
    return result


def linux_udp(path='/proc/net/snmp'):
    """The system's UDP counters that mean a datagram was lost after it arrived (RcvbufErrors: a full socket)."""
    try:
        rows = [line.split() for line in Path(path).read_text().splitlines() if line.startswith('Udp:')]
    except OSError:
        return {}
    if len(rows) < 2:
        return {}
    result = {}
    for key, value in zip(rows[0][1:], rows[1][1:]):
        if key in UDP_COUNTERS:
            try:
                result[key] = int(value)
            except ValueError:
                pass
    return result


def _ethtool():
    found = shutil.which('ethtool')
    if found:
        return found
    return next((path for path in ('/usr/sbin/ethtool', '/usr/bin/ethtool') if os.path.isfile(path)), None)


def linux_driver(interface, runner=None, ethtool=None):
    """The adapter driver's own counters with a drop or error in their name (ethtool -S), else {}."""
    tool = ethtool or _ethtool()
    if not interface or not tool:
        return {}
    if runner is None:
        from launcher.client_network import system_run as runner
    try:
        output = runner([tool, '-S', interface], 3)
    except Exception:  # a missing or hanging tool must not break a report
        return {}
    if getattr(output, 'returncode', 1) != 0:
        return {}
    result = {}
    for line in (output.stdout or '').splitlines():
        name, separator, value = line.strip().partition(':')
        if separator and DRIVER_COUNTER.search(name):
            try:
                result[name.strip()[:48]] = int(value.strip())
            except ValueError:
                continue
            if len(result) >= MAX_DRIVER:
                break
    return result


def default_reader():
    if sys.platform == 'win32':
        def read():
            from launcher import windows_network
            return {'adapters': windows_network.adapter_counters(), 'udp': {}}
        return read, lambda interface: {}
    return (lambda: {'adapters': linux_adapters(), 'udp': linux_udp()}), linux_driver


def _gained(before, after, keep=()):
    """after - before for the counters both have that did not go backwards; zero gains left out but `keep`."""
    result = {}
    for key, value in after.items():
        old = before.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or isinstance(old, bool) or not isinstance(old, int):
            continue
        if value >= old and (value > old or key in keep):
            result[key] = value - old
    return result


def gained(start, now):
    adapters = {}
    for name, values in now['adapters'].items():
        before = start['adapters'].get(name)
        if before is None:
            adapters[name] = {'appeared': True}
            continue
        row = _gained(before, values, PACKETS)
        for flag in ('usb', 'kind'):
            if flag in values:
                row[flag] = values[flag]
        adapters[name] = row
    for name in start['adapters']:
        if name not in now['adapters']:
            adapters[name] = {'gone': True}
    return {'adapters': adapters, 'udp': _gained(start['udp'], now['udp']),
            'driver': _gained(start['driver'], now['driver'])}


def stream_drops(change, interface):
    """Every drop and error the stream's adapter counted (received and sent), for a one-glance figure."""
    row = change['adapters'].get(interface) or {}
    return sum(value for key, value in row.items()
               if key not in PACKETS and isinstance(value, int) and not isinstance(value, bool))


class AdapterCounters:
    def __init__(self, read=None, driver=None, clock=time.monotonic):
        default_read, default_driver = default_reader() if read is None or driver is None else (None, None)
        self.read = read or default_read
        self.driver = driver or default_driver
        self.clock = clock
        self.lock = threading.Lock()
        self.start = None
        self.final = None
        self.interface = ''
        self.started = None
        self.ended = None

    def _read(self, interface):
        try:
            value = self.read() or {}
        except Exception:  # an adapter that cannot be read leaves the report without its figures, nothing more
            value = {}
        try:
            driver = self.driver(interface) if interface else {}
        except Exception:
            driver = {}
        return {'adapters': dict(value.get('adapters') or {}), 'udp': dict(value.get('udp') or {}),
                'driver': dict(driver or {})}

    def begin(self, interface=''):
        """A session starts: note every counter now. `interface` is the adapter carrying the stream, if known."""
        interface = str(interface or '')[:64]
        start = self._read(interface)
        with self.lock:
            self.start, self.final, self.interface, self.started, self.ended = start, None, interface, self.clock(), None

    def end(self):
        """The session ended: keep what the counters gained until now."""
        with self.lock:
            start, interface, done = self.start, self.interface, self.final is not None
        if start is None or done:
            return
        change = gained(start, self._read(interface))
        with self.lock:
            if self.start is start and self.final is None:
                self.final, self.ended = change, self.clock()

    def snapshot(self):
        with self.lock:
            start, final, interface, started, ended = self.start, self.final, self.interface, self.started, self.ended
        if start is None:
            return {'state': 'idle'}
        change = final if final is not None else gained(start, self._read(interface))
        result = {'state': 'ended' if final is not None else 'session', 'stream_interface': interface,
                  'seconds': round(max(0.0, (ended if ended is not None else self.clock()) - started), 1)}
        result.update(change)
        if interface and interface in change['adapters']:
            result['stream_drops'] = stream_drops(change, interface)
        return result
