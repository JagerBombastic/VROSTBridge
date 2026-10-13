"""Keep the stream's Wi-Fi on one access point during a session (RC4.2).

NetworkManager asks wpa_supplicant to scan in the background ("bgscan") so Wi-Fi can
roam between access points. While the signal is good it scans every 300 seconds when
more than one access point broadcasts the network or the network uses enterprise
(802.1X) security, and once a day otherwise; with a weak signal it scans every 30
seconds. Each scan takes the radio off its channel for a moment. RC4.1 sessions over
Wi-Fi 7 showed a video stall about every five minutes: these scans.

NetworkManager sets up no background scanning for a connection locked to one access
point (802-11-wireless.bssid). It cannot apply that setting to a running connection,
so for a session VROSTBridge:

1. writes a receipt (connection UUID, interface, the BSSID it sets);
2. sets 802-11-wireless.bssid on the connection profile to the access point Wi-Fi
   is using now and reconnects that connection: a two-to-five-second interruption
   while the session starts, before any video. The change is made in memory only
   (`nmcli connection modify --temporary`), so a restart of the PC or of
   NetworkManager also undoes it; only an nmcli that rejects --temporary saves it
   instead (a timed-out command is never retried that way);
3. at Stop, removes the BSSID from the profile again (only if it is still the one
   VROSTBridge set) with a saved change, which also clears a lock that was saved
   (by such an nmcli, or by the user saving the connection during the session); a
   temporary change only if the profile cannot be saved. The running connection
   stays on the access point until Wi-Fi next reconnects, when normal roaming
   returns. Nothing else in the profile changes. The receipt is deleted only once
   the lock is known to be gone, and no new lock is set while one is left over.

Skipped (nothing changed) when the adapter is not Wi-Fi, the profile already names an
access point, only the current access point is known to broadcast the network, with
a good signal and personal security (NetworkManager then scans once a day; an empty
or out-of-date access point list counts as unknown, and Wi-Fi is locked), the connection is Wi-Fi 7
multi-link (the address there belongs to the access point's MLD, not one radio, so a
BSSID lock could stop multi-link association), or NetworkManager is unavailable or
refuses. A companion that ends during a session undoes the lock the next time it
starts. Every failure is reported and none blocks a session.

Windows keeps a PC on its access point by itself unless it scans in the background,
so there the session turns Native Wifi background scanning off for the stream's
adapter (WlanSetInterface wlan_intf_opcode_background_scan_enabled = FALSE). Nothing
reconnects and no profile changes: the setting lasts while VROSTBridge's WLAN client
handle is open, ends at Stop (end()) or when the companion exits, and Windows
re-enables scanning when Wi-Fi disconnects, after which it is turned off again
(windows_network.WlanSessionOption). No receipt is needed.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from launcher.client_network import system_run
from launcher.wifi_diagnostics import parse_station_dump, valid_bssid
from launcher import windows_network

RECEIPT = 'wifi-ap-lock.json'
_INTERFACE = re.compile(r'(?!-)[A-Za-z0-9_.-]{1,15}')
_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
_SSID = re.compile(r'^\s*SSID:\s?(.*)$', re.M)
_SIGNAL = re.compile(r'^\s*signal:\s*(-?\d+)', re.M)
# NetworkManager's bgscan threshold for personal networks: below it, scans every 30 s.
GOOD_SIGNAL_DBM = -68
TIMED_OUT = 124  # _run's return code for a command that did not answer in time


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _detail(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:160] if lines else ''


def _terse_fields(line):
    """Split one `nmcli -t` line on unescaped colons and unescape the values."""
    fields, current, escaped = [], '', False
    for character in line:
        if escaped:
            current += character
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == ':':
            fields.append(current)
            current = ''
        else:
            current += character
    fields.append(current)
    return fields


class AccessPointLock:
    def __init__(self, state_directory, runner=system_run, log=print, sys_class_net='/sys/class/net',
                 iw=None, nmcli=None, sleep=time.sleep):
        """Tool paths: None locates the tool; an empty string disables it."""
        self.receipt = Path(state_directory) / RECEIPT
        self.runner, self.log, self.sleep = runner, log, sleep
        self.sys_class_net = Path(sys_class_net)
        self.iw = _tool('iw', ('/usr/sbin/iw', '/usr/bin/iw')) if iw is None else iw
        self.nmcli = _tool('nmcli', ('/usr/bin/nmcli',)) if nmcli is None else nmcli
        self.lock = threading.Lock()
        self.operation = threading.RLock()
        self.scans_off = None  # Windows: (interface, windows_network.WlanSessionOption) held for the session
        self.value = {'state': 'idle', 'interface': '', 'locked': False, 'message': ''}

    # ------------------------------------------------------------------ helpers
    def status(self):
        with self.lock:
            return dict(self.value)

    def _set(self, state, message, interface='', **extra):
        with self.lock:
            self.value = {'state': state, 'interface': interface, 'locked': state == 'locked', 'message': message,
                          **extra}
            value = dict(self.value)
        if message:
            self.log('WIFI ROAMING ' + state + ': ' + message)
        return value

    def _run(self, arguments, timeout):
        try:
            return self.runner(arguments, timeout)
        except subprocess.TimeoutExpired:
            # Without the command line in the message: it must never look like a rejected option.
            return subprocess.CompletedProcess(arguments, TIMED_OUT, '', 'no answer within ' + str(timeout) + ' s')
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', str(error)[:160])

    def _write_receipt(self, value):
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.receipt.parent, prefix='.vrostbridge-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream)
            os.replace(name, self.receipt)
        finally:
            Path(name).unlink(missing_ok=True)

    def _read_receipt(self):
        try:
            value = json.loads(self.receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if (isinstance(value, dict) and isinstance(value.get('uuid'), str) and _UUID.fullmatch(value['uuid'])
                and valid_bssid(value.get('bssid')) and isinstance(value.get('interface'), str)
                and _INTERFACE.fullmatch(value['interface'])):
            return value
        return None

    def _profile(self, interface):
        """(uuid, name) of the adapter's active connection, or None."""
        result = self._run([self.nmcli, '-g', 'GENERAL.CON-UUID,GENERAL.CONNECTION', 'device', 'show', interface], 5)
        lines = (result.stdout or '').strip().splitlines() if result.returncode == 0 else []
        uuid = lines[0].strip() if lines else ''
        name = re.sub(r'\\(.)', r'\1', lines[1].strip()) if len(lines) > 1 else ''
        return (uuid, name[:64]) if _UUID.fullmatch(uuid) else None

    def _get(self, uuid, field):
        result = self._run([self.nmcli, '-g', field, 'connection', 'show', 'uuid', uuid], 5)
        return re.sub(r'\\(.)', r'\1', (result.stdout or '').strip()) if result.returncode == 0 else None

    def _access_points(self, interface, ssid):
        """BSSIDs NetworkManager last saw broadcasting `ssid` (no new scan), or None."""
        result = self._run([self.nmcli, '-t', '-f', 'SSID,BSSID', 'device', 'wifi', 'list', 'ifname', interface,
                            '--rescan', 'no'], 5)
        if result.returncode != 0:
            return None
        seen = set()
        for line in (result.stdout or '').splitlines():
            fields = _terse_fields(line)
            if len(fields) >= 2 and fields[0] == ssid and valid_bssid(fields[1].lower()):
                seen.add(fields[1].lower())
        return seen

    def _activate(self, uuid, interface):
        return self._run([self.nmcli, '-w', '25', 'connection', 'up', 'uuid', uuid, 'ifname', interface], 30)

    def _modify(self, uuid, bssid, temporary):
        return self._run([self.nmcli, 'connection', 'modify', *(['--temporary'] if temporary else []), 'uuid', uuid,
                          '802-11-wireless.bssid', bssid], 30)

    def _lock(self, uuid, bssid):
        """Set the profile's BSSID in memory only; a saved change only when nmcli rejects --temporary."""
        result = self._modify(uuid, bssid, True)
        if (result.returncode not in (0, TIMED_OUT)
                and 'temporary' in ((result.stderr or '') + (result.stdout or '')).lower()):
            result = self._modify(uuid, bssid, False)
        return result

    def _clear(self, uuid):
        """Clear the BSSID in memory and on disk; in memory only if the profile cannot be saved."""
        result = self._modify(uuid, '', False)
        if result.returncode not in (0, TIMED_OUT):
            result = self._modify(uuid, '', True)
        return result

    def _undo(self, uuid, bssid):
        """Remove the lock; the receipt goes only once the profile is known to be clear."""
        if self._restore(uuid, bssid):
            self.receipt.unlink(missing_ok=True)
            return True
        return False

    # ------------------------------------------------------------------ session
    def begin(self, interface):
        """Lock the session's Wi-Fi to its current access point. Returns the status;
        'reconnected' is True when the connection was re-activated (addresses may change)."""
        with self.operation:
            try:
                return self._begin_windows(interface) if sys.platform == 'win32' else self._begin(interface)
            except Exception as error:  # a session must never be blocked by this helper
                return self._set('unavailable', 'Wi-Fi roaming was left as it is (' + str(error)[:160] + ').',
                                 interface if isinstance(interface, str) else '')

    def _begin_windows(self, interface):
        self.end()
        if not isinstance(interface, str) or not windows_network.valid_alias(interface):
            return self._set('unavailable', '')
        try:
            adapter = windows_network.adapter_for(interface)
        except windows_network.WindowsNetworkError as error:
            return self._set('unavailable', 'Wi-Fi roaming was left as it is (' + str(error)[:160] + ').', interface)
        if adapter is None:
            return self._set('unavailable', 'The network adapter ' + interface + ' was not found; Wi-Fi roaming was '
                             'left as it is.', interface)
        if adapter.get('kind') != 'wifi':
            return self._set('wired', '', interface)
        try:
            option = windows_network.WlanSessionOption(adapter['guid'], windows_network.OPCODE_BACKGROUND_SCAN, False)
        except windows_network.WindowsNetworkError as error:
            if error.code == windows_network.ERROR_INVALID_STATE:
                return self._set('unavailable', 'Wi-Fi is not connected on ' + interface + '; roaming was left as it is.',
                                 interface)
            return self._set('refused', 'Windows did not allow turning off Wi-Fi background scans (' + str(error)[:160] +
                             '); Wi-Fi works as usual.', interface)
        with self.lock:
            self.scans_off = (interface, option)
        bssid = windows_network.wifi_link(interface).get('bssid') or ''
        return self._set('locked', 'Wi-Fi background scans are off for this session, so Windows keeps ' + interface +
                         ' on ' + ('access point ' + bssid if bssid else 'its current access point') + '; scanning '
                         'returns when the session stops.', interface, reconnected=False, background_scan=False,
                         **({'bssid': bssid} if valid_bssid(bssid) else {}))

    def _begin(self, interface):
        self.end()
        if self.receipt.exists():
            # One receipt records one lock: never replace a lock that could not be removed yet.
            return self._set('unavailable', 'Wi-Fi was left as it is, because an earlier access-point lock could not '
                             'be removed yet; VROSTBridge retries when it next starts.',
                             interface if isinstance(interface, str) else '')
        if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface):
            return self._set('unavailable', '')
        if not (self.sys_class_net / interface / 'wireless').is_dir():
            return self._set('wired', '', interface)
        if not self.iw or not self.nmcli:
            return self._set('unavailable', 'Staying on one access point needs iw and NetworkManager; Wi-Fi roaming '
                             'was left as it is.', interface)
        result = self._run([self.iw, 'dev', interface, 'link'], 3)
        text = result.stdout if result.returncode == 0 else ''
        connection = next((item for item in parse_station_dump(text) if item['kind'] == 'connection'), None)
        bssid = connection['address'] if connection else ''
        if not valid_bssid(bssid):
            return self._set('unavailable', 'Wi-Fi is not connected on ' + interface + '; roaming was left as it is.',
                             interface)
        if connection['links']:
            return self._set('multi_link', 'This is a Wi-Fi 7 multi-link connection, which VROSTBridge does not lock '
                             'to one access point; background scans may still cause a short stall now and then.',
                             interface)
        profile = self._profile(interface)
        if not profile:
            return self._set('unavailable', 'The Wi-Fi connection could not be identified; roaming was left as it is.',
                             interface)
        uuid, name = profile
        saved = self._get(uuid, '802-11-wireless.bssid')
        if saved is None:
            return self._set('unavailable', 'The Wi-Fi connection settings could not be read; roaming was left as '
                             'it is.', interface)
        if saved:
            return self._set('already_locked', '', interface)
        ssid_match = _SSID.search(text)
        signal_match = _SIGNAL.search(text)
        ssid = ssid_match.group(1) if ssid_match else ''
        signal = int(signal_match.group(1)) if signal_match else None
        security = self._get(uuid, '802-11-wireless-security.key-mgmt') or ''
        aps = self._access_points(interface, ssid) if ssid else None
        # Only "this access point and no other" is a known single one: an empty list or
        # one without the current access point is out of date, and Wi-Fi is locked anyway.
        if (aps == {bssid.lower()} and 'eap' not in security.lower() and signal is not None
                and signal >= GOOD_SIGNAL_DBM):
            return self._set('not_needed', 'One access point with a good signal: NetworkManager rarely scans, so '
                             'Wi-Fi was left as it is.', interface)
        self._write_receipt({'uuid': uuid, 'interface': interface, 'bssid': bssid, 'name': name,
                             'time': int(time.time())})
        result = self._lock(uuid, bssid.upper())
        if result.returncode != 0:
            # A timed-out change may still have been applied: undo it before reporting.
            undone = self._undo(uuid, bssid)
            detail = _detail(result)
            return self._set('refused', 'NetworkManager did not allow keeping Wi-Fi on one access point' +
                             (' (' + detail + ')' if detail else '') + '; Wi-Fi works as usual.' +
                             ('' if undone else ' VROSTBridge removes any partial change when it next starts.'),
                             interface)
        self.log('WIFI ROAMING keeping "' + name + '" on access point ' + bssid + ' for this session; reconnecting '
                 'Wi-Fi once to apply it.')
        started = time.monotonic()
        result = self._activate(uuid, interface)
        if result.returncode != 0:
            detail = _detail(result)
            undone = self._undo(uuid, bssid)
            self._activate(uuid, interface)
            return self._set('failed', 'Wi-Fi could not reconnect locked to the access point' +
                             (' (' + detail + ')' if detail else '') +
                             ('; the lock was removed again.' if undone else
                              '; the lock could not be removed yet, and VROSTBridge retries when it next starts.'),
                             interface, reconnected=True)
        return self._set('locked', 'Wi-Fi stays on access point ' + bssid + ' for this session, so NetworkManager '
                         'does not scan in the background (reconnected in ' + str(round(time.monotonic() - started, 1)) +
                         ' s).', interface, reconnected=True, bssid=bssid)

    def _restore(self, uuid, bssid):
        """Remove the BSSID from the profile if it is still the one VROSTBridge set."""
        current = self._get(uuid, '802-11-wireless.bssid')
        if current is None:
            return False
        if current.lower() != bssid.lower():
            return True  # changed or cleared by someone else: leave it
        return self._clear(uuid).returncode == 0

    def end(self):
        """Remove the lock this session (or an earlier, interrupted one) set."""
        with self.operation:
            with self.lock:
                scans_off, self.scans_off = self.scans_off, None
            if scans_off:  # Windows: closing the WLAN handle turns background scanning back on
                interface, option = scans_off
                option.close()
                return self._set('released', 'Wi-Fi background scans are back on for ' + interface + '.', interface)
            if sys.platform == 'win32':
                return self.status()
            receipt = self._read_receipt()
            if receipt is None:
                self.receipt.unlink(missing_ok=True)
                return self.status()
            if not self.nmcli:
                return self._set('restore_failed', 'NetworkManager is unavailable, so the access-point lock on "' +
                                 receipt.get('name', '') + '" could not be removed.', receipt['interface'])
            if self._undo(receipt['uuid'], receipt['bssid']):
                return self._set('released', 'Wi-Fi roaming returns to normal the next time Wi-Fi reconnects.',
                                 receipt['interface'])
            return self._set('restore_failed', 'The access-point lock on "' + receipt.get('name', '') + '" could not '
                             'be removed yet; VROSTBridge retries when it next starts.', receipt['interface'])

    def recover(self):
        """At companion start: undo a lock left by a session that did not stop normally.
        Windows: nothing outlives the companion (its WLAN handle closed with it)."""
        if sys.platform == 'win32':
            return
        if self.receipt.exists():
            self.end()
