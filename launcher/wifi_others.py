"""Pause the client's other Wi-Fi adapters on the stream's network during a session (RC4.1).

A second Wi-Fi adapter joined to the same home network competes with the stream
adapter for airtime on the same channel, and the client then answers on two
addresses: ALVR's discovery can see the address the native client is not listening
on, and a reconnection after a Wi-Fi outage fails. During a session only, VROSTBridge
asks NetworkManager to disconnect other Wi-Fi adapters that have an address in the
stream adapter's subnet (`nmcli device disconnect`), and reconnects them when the
session stops (`nmcli device connect`). The stream adapter, wired adapters and
adapters on other networks are never touched.

1.0.0.14: a wired stream leaves Wi-Fi alone where the system already keeps the stream on the cable: on Windows, which
answers ARP only on the adapter that has the address, and on Linux once arp_ignore/arp_announce are set (launcher/
network_settings.py). Pausing remains for a Wi-Fi stream (airtime) and for a Linux PC without those settings, where
the host could otherwise send the video to the Wi-Fi card. The caller checks the host is still reachable over the
stream adapter after a pause and undoes it at once if not (resume_now): on one PC disconnecting Wi-Fi left the
cable without a route, and every Start failed.

A receipt file records what was paused, so a companion that ends unexpectedly
reconnects those adapters the next time it starts. Every failure is reported and
none blocks a session.

Windows (no administrator rights needed): the other adapter's saved Wi-Fi profile is
read from its connection (WlanQueryInterface, which needs Windows' location consent
from Windows 11 24H2), then it is disconnected (WlanDisconnect) and later reconnected
with that profile (WlanConnect, in the background, skipped if Windows reconnected it
already). The receipt also records each adapter's GUID and profile. Without the
profile an adapter is not paused, and the reason is reported.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from launcher.client_identity import lan_addresses
from launcher.client_network import system_run
from launcher import windows_network

_INTERFACE = re.compile(r'[A-Za-z0-9_.:-]{1,15}')
RECEIPT = 'paused-wifi.json'


def _valid_name(name):
    if sys.platform == 'win32':
        return windows_network.valid_alias(name)
    return isinstance(name, str) and bool(_INTERFACE.fullmatch(name))


class OtherWifiPause:
    def __init__(self, state_directory, runner=system_run, log=print, sys_class_net='/sys/class/net',
                 nmcli=None, address_provider=lan_addresses):
        self.receipt = Path(state_directory) / RECEIPT
        self.runner, self.log = runner, log
        self.sys_class_net = Path(sys_class_net)
        self.nmcli = nmcli if nmcli is not None else (
            '/usr/bin/nmcli' if Path('/usr/bin/nmcli').is_file() else shutil.which('nmcli'))
        self.address_provider = address_provider
        self.lock = threading.Lock()
        # Serialises begin/end/recover: the start-up recovery can overlap the first session.
        self.operation = threading.RLock()
        self.paused = []
        self.unrestored = []  # adapters an earlier run paused that could not be reconnected yet
        self.windows_records = {}  # Windows: adapter alias -> {'interface', 'guid', 'profile'} to reconnect it
        self.value = {'state': 'idle', 'message': '', 'paused': []}

    def status(self):
        with self.lock:
            return dict(self.value, paused=list(self.value.get('paused', [])))

    def _set(self, state, message, paused=()):
        with self.lock:
            self.value = {'state': state, 'message': message, 'paused': list(paused)}
        if message:
            self.log('WIFI ' + message)

    def _wireless(self, interface):
        if sys.platform == 'win32':
            try:
                adapter = windows_network.adapter_for(interface)
            except windows_network.WindowsNetworkError:
                return False
            return bool(adapter) and adapter.get('kind') == 'wifi'
        return (self.sys_class_net / interface / 'wireless').is_dir()

    def others(self, interface, source_ip):
        """Other Wi-Fi adapters with an address in the stream address's subnet."""
        try:
            records = list(self.address_provider() or ())
        except Exception:
            return []
        network = None
        for record in records:
            if record.get('address') == source_ip and isinstance(record.get('prefix'), int):
                network = ipaddress.IPv4Network(source_ip + '/' + str(record['prefix']), strict=False)
        if network is None:
            return []
        found = []
        for record in records:
            name = record.get('interface')
            if (_valid_name(name) and name != interface and name not in found
                    and record.get('physical') and self._wireless(name)):
                try:
                    if ipaddress.IPv4Address(record.get('address')) in network:
                        found.append(name)
                except (ValueError, TypeError):
                    continue
        return found

    def _write_receipt(self, paused):
        paused = list(dict.fromkeys(list(paused) + list(self.unrestored)))
        try:
            if paused:
                temporary = self.receipt.with_suffix('.tmp')
                value = {'paused': paused}
                if sys.platform == 'win32':
                    value['windows'] = {name: self.windows_records[name] for name in paused if name in self.windows_records}
                temporary.write_text(json.dumps(value) + '\n', encoding='utf-8')
                os.replace(temporary, self.receipt)
            else:
                self.receipt.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            self.log('WIFI Could not record the paused adapters: ' + str(error)[:160])

    def begin(self, interface, source_ip, enabled=True, wired=False, separated=False):
        """Pause other same-network Wi-Fi adapters for this session (enabled) or only warn. A wired stream leaves them
        connected when the system keeps the stream on the cable anyway (`separated`, always on Windows)."""
        with self.operation:
            return self._begin(interface, source_ip, enabled, wired, separated or sys.platform == 'win32')

    def resume_now(self, reason):
        """Reconnect what this session paused, at once (the pause cut the stream adapter off)."""
        with self.operation:
            with self.lock:
                paused, self.paused = self.paused, []
            if not paused:
                return self.status()
            failed = self._reconnect(paused, reason)
            self.unrestored = failed
            self._write_receipt(failed)
            self._set('resumed', 'Reconnected ' + ', '.join(paused) + ': ' + reason + '.')
            return self.status()

    def _begin(self, interface, source_ip, enabled, wired=False, separated=False):
        self._end(retry=False)  # never delay a start for an adapter an earlier run left down
        if not interface or not source_ip:
            return self.status()
        others = self.others(interface, source_ip)
        # An adapter with an address on this network is connected again.
        self.unrestored = [name for name in self.unrestored if name not in others]
        if not others:
            self._set('none', '')
            return self.status()
        names = ', '.join(others)
        if wired and separated:
            self._set('kept', 'Wi-Fi (' + names + ') stays connected: the cable carries the stream.')
            return self.status()
        if not enabled:
            self._set('warning', 'Another Wi-Fi adapter (' + names + ') is on the same network as the stream adapter ' +
                      interface + '. It shares the radio channel and can stop a reconnection; turn it off, or let '
                      'VROSTBridge pause it during sessions.')
            return self.status()
        if sys.platform == 'win32':
            return self._report(*self._pause_windows(others))
        if not self.nmcli:
            self._set('warning', 'Another Wi-Fi adapter (' + names + ') is on the same network, and NetworkManager is '
                      'unavailable to pause it. Turn it off for smoother streaming.')
            return self.status()
        paused, failed = [], []
        for name in others:
            try:
                result = self.runner([self.nmcli, '-w', '5', 'device', 'disconnect', name], 10)
                ok = result.returncode == 0
            except (OSError, ValueError, subprocess.SubprocessError):
                ok = False
            (paused if ok else failed).append(name)
            if ok:
                # Record each pause at once so a crash cannot strand an adapter.
                with self.lock:
                    self.paused = list(paused)
                self._write_receipt(paused)
        return self._report(paused, failed)

    def _report(self, paused, failed, detail=''):
        message = ''
        if paused:
            message = ('Paused the other Wi-Fi adapter' + ('s ' if len(paused) > 1 else ' ') + ', '.join(paused) +
                       ' on this network for the session; ' + ('they reconnect' if len(paused) > 1 else 'it reconnects') +
                       ' when the session stops.')
        if failed:
            message += (' ' if message else '') + ('Could not pause ' + ', '.join(failed) + ' on the same network' +
                                                    (' (' + detail + ')' if detail else '') +
                                                    '; turn it off for smoother streaming.')
        self._set('paused' if paused else 'warning', message, paused)
        return self.status()

    def _pause_windows(self, others):
        """Windows: disconnect each adapter, remembering its profile first. (paused, failed, first reason)."""
        paused, failed, reasons = [], [], []
        for name in others:
            try:
                record = windows_network.wifi_pause(name)
            except OSError as error:  # windows_network.WindowsNetworkError
                failed.append(name)
                reasons.append(str(error)[:160])
                continue
            paused.append(name)
            # Record each pause at once so a crash cannot strand an adapter.
            with self.lock:
                self.windows_records[name] = record
                self.paused = list(paused)
            self._write_receipt(paused)
        return paused, failed, reasons[0] if reasons else ''

    def _connect(self, name):
        if sys.platform == 'win32':
            try:
                return bool(windows_network.wifi_resume(self.windows_records.get(name)))
            except OSError:
                return False
        try:
            # Queue the activation and return (-w 0): NetworkManager reconnects in the
            # background, so Stop never waits for Wi-Fi association or DHCP.
            result = self.runner([self.nmcli, '-w', '0', 'device', 'connect', name], 10) if self.nmcli else None
            return result is not None and result.returncode == 0
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def _reconnect(self, names, reason):
        restored, failed = [], []
        for name in names:
            if not _valid_name(name):
                continue
            ok = self._connect(name)
            (restored if ok else failed).append(name)
        if restored:
            self.log('WIFI Reconnected ' + ', '.join(restored) + ' ' + reason + '.')
        if failed:
            self.log('WIFI Could not reconnect ' + ', '.join(failed) + '; reconnect it from the network menu.')
        return failed

    def end(self):
        with self.operation:
            self._end()

    def _end(self, retry=True):
        with self.lock:
            paused, self.paused = self.paused, []
        # Adapters an earlier attempt could not reconnect are tried again at Stop.
        retry = [name for name in self.unrestored if name not in paused] if retry else []
        if not paused and not retry:
            return
        failed = self._reconnect(paused + retry, 'after the session')
        self.unrestored = failed
        self.windows_records = {name: record for name, record in self.windows_records.items() if name in failed}
        self._write_receipt([])
        if paused:
            self._set('restored' if not failed else 'restore_failed',
                      '' if not failed else 'Reconnect ' + ', '.join(failed) + ' from the network menu.')

    def recover(self):
        """Reconnect adapters a previous companion paused and could not restore (call at start-up)."""
        with self.operation:
            if self.paused:
                return []  # a session is running; its receipt already lists everything
            try:
                value = json.loads(self.receipt.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return []
            except (OSError, ValueError):
                value = {}
            names = [name for name in (value.get('paused') if isinstance(value, dict) else None) or []
                     if _valid_name(name)][:8]
            if sys.platform == 'win32':
                records = value.get('windows') if isinstance(value, dict) and isinstance(value.get('windows'), dict) else {}
                for name in names:
                    record = records.get(name)
                    if isinstance(record, dict):
                        self.windows_records[name] = {'interface': name, 'guid': str(record.get('guid', ''))[:64],
                                                      'profile': str(record.get('profile', ''))[:256]}
            failed = self._reconnect(names, 'that an earlier session paused') if names else []
            # Keep what could not be reconnected so the next Stop or start-up tries again.
            self.unrestored = failed
            self.windows_records = {name: record for name, record in self.windows_records.items() if name in failed}
            self._write_receipt([])
            return failed


__all__ = ['OtherWifiPause', 'RECEIPT']
