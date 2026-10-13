"""Wi-Fi radio health for the session report.

Wi-Fi stutter usually comes from the radio rather than the route: retransmitted
or failed frames, missed beacons, or a roam to another access point mid-game.
Each sample is one cheap `iw station dump`, turned into counters and rates, so a
report shows what the radio was doing during the session. Nothing is changed.
Keeping Wi-Fi on one access point during a session is launcher/wifi_roaming.py.

Windows: each sample comes from the Native Wifi API (launcher/windows_network.py):
signal, rates, access point (a change is a roam), channel and band, and the driver's
frame counters where it reports them. Those counters are not restarted by a roam.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run
from launcher import windows_network

_INTERFACE = re.compile(r'(?!-)[A-Za-z0-9_.-]{1,15}')
_MAC = r'[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}'
_HEADER = re.compile(r'(Station|Connected to)\s+(' + _MAC + r')(?:\s*\(on\s+([^)\s]+)\))?', re.I)
# MLO sections vary between iw versions ("Link 0:", "link 0, address ...",
# "- link ID 0 link addr ..."); lines indented below a link header belong to it.
_LINK = re.compile(r'(?:-\s*)?link(?:\s+id)?\s*(\d{1,2})(?!\d)(.*)', re.I)
_PAIR = re.compile(r'([A-Za-z][A-Za-z0-9 ./_()\[\]-]*?)\s*:\s*(.*)')
_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')
_RATE = re.compile(r'(\d+(?:\.\d+)?)\s*([KMG]?)(?:Bit/s|bps)', re.I)
_TRAFFIC = re.compile(r'(\d+)\s+bytes\s+\((\d+)\s+packets\)')
_COUNTERS = ('tx_retries', 'tx_failed', 'beacon_loss', 'tx_packets', 'rx_packets')
_RADIO = ('signal_dbm', 'signal_avg_dbm', 'tx_bitrate_mbps', 'rx_bitrate_mbps')


def _integer(value):
    match = _NUMBER.search(value)
    return int(float(match.group())) if match else None


def _decimal(value):
    match = _NUMBER.search(value)
    return float(match.group()) if match else None


def _bitrate(value):
    """Mbit/s from 'MBit/s' (iw) or 'Mbps' (expected throughput)."""
    match = _RATE.search(value)
    if not match:
        return None
    return round(float(match.group(1)) * {'': 1e-6, 'k': 1e-3, 'm': 1.0, 'g': 1e3}[match.group(2).lower()], 1)


def _address(value):
    match = re.search(_MAC, value or '')
    return match.group().lower() if match else None


_FIELDS = {
    'tx retries': ('tx_retries', _integer), 'tx failed': ('tx_failed', _integer),
    'beacon loss': ('beacon_loss', _integer), 'beacon rx': ('beacon_rx', _integer),
    'rx packets': ('rx_packets', _integer), 'tx packets': ('tx_packets', _integer),
    'rx bytes': ('rx_bytes', _integer), 'tx bytes': ('tx_bytes', _integer),
    'rx drop misc': ('rx_drop_misc', _integer), 'inactive time': ('inactive_ms', _integer),
    'signal': ('signal_dbm', _integer), 'signal avg': ('signal_avg_dbm', _integer),
    'beacon signal avg': ('beacon_signal_avg_dbm', _integer), 'last ack signal': ('last_ack_signal_dbm', _integer),
    'tx bitrate': ('tx_bitrate_mbps', _bitrate), 'rx bitrate': ('rx_bitrate_mbps', _bitrate),
    'expected throughput': ('expected_throughput_mbps', _bitrate),
    'connected time': ('connected_time_s', _integer), 'freq': ('freq_mhz', _decimal),
    'address': ('address', _address), 'mld address': ('mld_address', _address),
}


def _store(target, key, value):
    if key in ('rx', 'tx'):  # `iw link`: "RX: 1234 bytes (56 packets)"
        traffic = _TRAFFIC.search(value)
        if traffic:
            target[key + '_bytes'], target[key + '_packets'] = int(traffic.group(1)), int(traffic.group(2))
        return
    field = _FIELDS.get(key)
    if field:
        try:
            parsed = field[1](value)
        except (ValueError, OverflowError):
            parsed = None
        if parsed is not None:
            target[field[0]] = parsed


def parse_station_dump(text):
    """Parse `iw dev <if> station dump` or `iw dev <if> link` output. Never raises.

    Returns one dict per 'Station <mac>' (kind 'station') or 'Connected to <mac>'
    (kind 'connection') with 'address', 'interface', numeric fields (signal in
    dBm, bitrates in Mbit/s, counters, connected_time_s, freq_mhz) and 'links':
    per-link dicts ('link_id', optional 'address' and the same fields) for Wi-Fi 7
    multi-link (MLO) connections. Unknown lines are ignored.
    """
    stations, current, link, link_indent = [], None, None, -1
    for raw in (text if isinstance(text, str) else '').splitlines():
        body = raw.strip()
        if not body:
            continue
        indent = len(raw) - len(raw.lstrip())
        header = _HEADER.match(body)
        if header:
            current = {'kind': 'station' if header.group(1).lower() == 'station' else 'connection',
                       'address': header.group(2).lower(), 'interface': header.group(3) or '', 'links': []}
            stations.append(current)
            link = None
            continue
        if current is None:
            continue
        if link is not None and indent <= link_indent:
            link = None
        match = _LINK.match(body)
        if match:
            link = {'link_id': int(match.group(1))}
            address = _address(match.group(2))
            if address:
                link['address'] = address
            current['links'].append(link)
            link_indent = indent
            continue
        pair = _PAIR.match(body)
        if pair:
            _store(link if link is not None else current, ' '.join(pair.group(1).casefold().split()), pair.group(2))
    return stations


def _aggregate(station):
    """Station-wide values; for MLO, missing station totals come from the links."""
    links = station.get('links') or []
    values = {'links': len(links)}
    for key in _COUNTERS:
        if key in station:
            values[key] = station[key]
        elif any(key in link for link in links):
            values[key] = sum(link.get(key, 0) for link in links)
    for key in _RADIO:
        seen = [station[key]] if key in station else [link[key] for link in links if key in link]
        if seen:
            values[key] = max(seen)
    if 'connected_time_s' in station:
        values['connected_time_s'] = station['connected_time_s']
    return values


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _detail(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:160] if lines else ''


def _valid_interface(value):
    if sys.platform == 'win32':
        return windows_network.valid_alias(value)
    return isinstance(value, str) and bool(_INTERFACE.fullmatch(value)) and value not in ('.', '..')


def valid_bssid(value):
    """A unicast, non-zero MAC address written as six colon-separated hex pairs."""
    if not isinstance(value, str) or not re.fullmatch(_MAC, value):
        return False
    octets = bytes.fromhex(value.replace(':', ''))
    return not octets[0] & 1 and any(octets)


class WifiDiagnostics:
    def __init__(self, runner=system_run, iw=None, nmcli=None, log=print, clock=time.monotonic):
        """Tool paths: None locates the tool; an empty string disables it."""
        self.runner, self.log, self.clock = runner, log, clock
        self.iw = _tool('iw', ('/usr/sbin/iw', '/usr/bin/iw')) if iw is None else iw
        self.nmcli = _tool('nmcli', ('/usr/bin/nmcli',)) if nmcli is None else nmcli
        self._lock = threading.Lock()          # samples and totals
        self._interface = ''
        self._previous = None                  # last connected sample used for deltas
        self._latest = None
        self._totals = None

    def _run(self, arguments, timeout):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', str(error)[:160])

    # Radio diagnostics ---------------------------------------------------

    def begin(self, interface):
        """Start session totals for this adapter and take the first sample."""
        with self._lock:
            self._interface = interface if _valid_interface(interface) else ''
            self._previous = self._latest = None
            self._totals = {'samples': 0, 'first_time': None, 'last_time': None, 'tx_retries': 0, 'tx_failed': 0,
                            'beacon_loss': 0, 'tx_packets': 0, 'reassociations': 0, 'roams': 0,
                            'min_signal_dbm': None, 'max_signal_dbm': None}
        return self.sample()

    def end(self):
        """Nothing to undo; totals stay available to summary()."""
        return self.summary()

    def sample(self, interface=None):
        """One `iw station dump`: current values and changes since the previous sample. Never raises."""
        with self._lock:
            interface = interface or self._interface
        if not _valid_interface(interface):
            return {'interface': '', 'connected': False, 'message': 'No valid Wi-Fi adapter was given.'}
        if sys.platform == 'win32':
            return self._sample_windows(interface)
        if not self.iw:
            return {'interface': interface, 'connected': False,
                    'message': 'Wi-Fi radio details are not recorded because the iw tool is not available.'}
        result = self._run([self.iw, 'dev', interface, 'station', 'dump'], 3)
        now = self.clock()
        stations = parse_station_dump(result.stdout) if result.returncode == 0 else []
        if result.returncode != 0:
            message = 'Wi-Fi radio details could not be read (' + (_detail(result) or 'iw failed') + ').'
        else:
            message = '' if stations else 'Wi-Fi is not connected on ' + interface + '.'
        return self._record(interface, stations[0] if stations else None, now, message)

    def _sample_windows(self, interface):
        """One Native Wifi reading turned into the station fields _record() expects."""
        link = windows_network.wifi_link(interface, identity=True, statistics=True)
        now = self.clock()
        if not link.get('connected'):
            message = ('Wi-Fi is not connected on ' + interface + '.' if link.get('wifi') else
                       'Wi-Fi radio details could not be read (' + (link.get('reason') or 'unavailable').rstrip('.') + ').')
            return self._record(interface, None, now, message)
        links = []
        for item in (link.get('links') or []) if link.get('multi_link') else []:
            entry = {'link_id': item.get('link_id')}
            entry.update({key: item[key] for key in ('signal_dbm', 'freq_mhz') if item.get(key) is not None})
            links.append(entry)
        station = {'kind': 'connection', 'address': link.get('bssid') or '', 'interface': interface, 'links': links}
        for key, name in (('signal_dbm', 'signal_dbm'), ('tx_bitrate_mbps', 'tx_mbps'), ('rx_bitrate_mbps', 'rx_mbps')):
            if link.get(name) is not None:
                station[key] = link[name]
        station.update({key: value for key, value in (link.get('statistics') or {}).items() if key in _COUNTERS})
        extra = {key: link[key] for key in ('channel', 'band', 'bands', 'phy', 'signal_quality', 'freq_mhz')
                 if link.get(key) not in (None, '', [])}
        message = ''
        if link.get('location_blocked'):
            extra['location_blocked'] = True
            message = ('Windows location access is off for VROSTBridge, so the access point (and roams to another '
                       'one) cannot be identified.')
        return self._record(interface, station, now, message, extra=extra, roam_resets_counters=False)

    def _record(self, interface, station, now, message, extra=None, roam_resets_counters=True):
        """roam_resets_counters: the counters restart with each association (Linux); Windows counts per adapter."""
        sample = {'interface': interface, 'connected': station is not None, 'message': message}
        with self._lock:
            if station is None:
                self._latest = sample
                return dict(sample)
            values = _aggregate(station)
            sample.update(values, interval_s=None, tx_retries_per_s=None, tx_failed_per_s=None,
                          beacon_loss_delta=None, tx_retry_ratio=None, reassociated=False, roamed=False)
            if extra:
                sample.update(extra)
            previous = self._previous if self._previous and self._previous['interface'] == interface else None
            deltas = {}
            if previous:
                old = previous['values']
                roamed = previous['address'] != station['address']
                # Counters restart with each association; a drop means a reconnect.
                reset = roamed or any(key in values and key in old and values[key] < old[key]
                                      for key in _COUNTERS + ('connected_time_s',))
                counters_reset = reset if roam_resets_counters else any(
                    key in values and key in old and values[key] < old[key] for key in _COUNTERS)
                if counters_reset:
                    deltas = {key: values[key] for key in _COUNTERS if key in values}
                else:
                    deltas = {key: values[key] - old[key] for key in _COUNTERS if key in values and key in old}
                interval = now - previous['time']
                sample.update(interval_s=round(interval, 3), reassociated=reset, roamed=roamed,
                              beacon_loss_delta=deltas.get('beacon_loss'))
                if not counters_reset and interval > 0:
                    for key in ('tx_retries', 'tx_failed'):
                        if key in deltas:
                            sample[key + '_per_s'] = round(deltas[key] / interval, 2)
                if deltas.get('tx_packets'):
                    sample['tx_retry_ratio'] = round(deltas.get('tx_retries', 0) / deltas['tx_packets'], 4)
            totals = self._totals
            if totals is not None:
                for key in ('tx_retries', 'tx_failed', 'beacon_loss', 'tx_packets'):
                    totals[key] += deltas.get(key, 0)
                totals['reassociations'] += int(sample['reassociated'])
                totals['roams'] += int(sample['roamed'])
                totals['samples'] += 1
                totals['first_time'] = now if totals['first_time'] is None else totals['first_time']
                totals['last_time'] = now
                signal = values.get('signal_dbm')
                if signal is not None:
                    totals['min_signal_dbm'] = min(signal, totals['min_signal_dbm'] if totals['min_signal_dbm']
                                                   is not None else signal)
                    totals['max_signal_dbm'] = max(signal, totals['max_signal_dbm'] if totals['max_signal_dbm']
                                                   is not None else signal)
            self._previous = {'interface': interface, 'address': station['address'], 'time': now, 'values': values}
            self._latest = sample
            return dict(sample)

    def latest(self):
        """The latest sample without taking a new one (1.0.0.7: the page reads whether Windows refused to name the
        network), or {}."""
        with self._lock:
            return dict(self._latest) if self._latest else {}

    def summary(self):
        """Latest sample plus cumulative changes since begin(), for the session report."""
        with self._lock:
            totals = dict(self._totals) if self._totals else None
            latest = dict(self._latest) if self._latest else None
            interface = self._interface
        since = None
        if totals:
            first, last = totals.pop('first_time'), totals.pop('last_time')
            duration = (last - first) if first is not None else 0.0
            since = dict(totals, duration_s=round(duration, 1),
                         tx_retries_per_s=round(totals['tx_retries'] / duration, 2) if duration > 0 else None,
                         tx_failed_per_s=round(totals['tx_failed'] / duration, 2) if duration > 0 else None,
                         tx_retry_ratio=round(totals['tx_retries'] / totals['tx_packets'], 4)
                         if totals['tx_packets'] else None)
        return {'interface': interface, 'latest': latest, 'since_begin': since}
