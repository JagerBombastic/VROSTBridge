"""RC4.2: Wi-Fi access-point lock, Wi-Fi/Ethernet detection, report naming and upload,
Steam shortcut, Game Mode display ranking and the SteamVR async check. No SteamVR,
NetworkManager, Steam or Windows needed."""
import base64
import gzip
import json
import os
from pathlib import Path
import secrets
import shlex
import struct
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from unittest.mock import Mock

from launcher import connection_kind, reporting, steam_shortcut
from launcher.game_mode import (MARKER, GameMode, connected_outputs, digest, headset_outputs, known_headsets,
                                prefer_output, rank_headsets_last, remember_headsets, wrapper as gamescope_wrapper)
from launcher.session_link import SessionClient, SessionError, SessionHost
from launcher.steamvr_async import AsyncReprojectionCheck
from launcher.wifi_roaming import AccessPointLock, _terse_fields

ROOT = Path(__file__).resolve().parents[1]


def completed(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


LINK = """Connected to 3c:84:6a:12:34:56 (on wlp1s0)
\tSSID: Home:Net
\tfreq: 6115.0
\tRX: 1834651620 bytes (1386034 packets)
\tTX: 72830411 bytes (380122 packets)
\tsignal: -41 dBm
\trx bitrate: 2401.9 MBit/s 160MHz EHT-MCS 11 EHT-NSS 2 EHT-GI 0
\ttx bitrate: 2882.3 MBit/s 320MHz EHT-MCS 11 EHT-NSS 2 EHT-GI 0
"""
MLO_LINK = """Connected to 3c:84:6a:aa:bb:cc (on wlp1s0)
\tSSID: Home:Net
\tLink 0 BSSID 3c:84:6a:aa:bb:c0
\t\tfreq: 5180.0
\t\tsignal: -50 dBm
\tLink 1 BSSID 3c:84:6a:aa:bb:c1
\t\tfreq: 6115.0
\t\tsignal: -47 dBm
"""
UUID = '0d6c79f6-2c4b-4bb5-9a4c-2f1a2b3c4d5e'


class AccessPointLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        net = self.root / 'net'
        (net / 'wlp1s0' / 'wireless').mkdir(parents=True)
        (net / 'enp2s0').mkdir()
        self.calls, self.lines = [], []
        self.saved = ''
        self.answers = {}
        self.link = LINK
        self.aps = 'Home\\:Net:3C\\:84\\:6A\\:12\\:34\\:56\nHome\\:Net:3C\\:84\\:6A\\:12\\:34\\:57\nOther:11\\:22\\:33\\:44\\:55\\:66\n'
        self.security = 'sae'
        self.modify_code = 0
        self.modify_codes = []  # per call, before modify_code
        self.modify_applies = False  # a failed change that was applied all the same
        self.modify_timeouts = 0  # changes that do not answer in time (but are applied)
        self.temporary = True  # nmcli knows --temporary
        self.disk = ''  # the saved profile's BSSID (self.saved is the one in memory)
        self.get_fails = False
        self.up_codes = [0]
        self.lock = AccessPointLock(self.root / 'state', runner=self.runner, log=self.lines.append,
                                    sys_class_net=net, iw='/usr/sbin/iw', nmcli='/usr/bin/nmcli')

    def runner(self, arguments, timeout):
        self.calls.append(list(arguments))
        if arguments[-1] == 'link':
            return completed(0, self.link)
        if 'GENERAL.CON-UUID,GENERAL.CONNECTION' in arguments:
            return completed(0, UUID + '\nHome\\:Net\n')
        if arguments[1:3] == ['-g', '802-11-wireless.bssid']:
            return completed(1, '', 'Error: no such connection') if self.get_fails else completed(0, self.saved + '\n')
        if arguments[1:3] == ['-g', '802-11-wireless-security.key-mgmt']:
            return completed(0, self.security + '\n')
        if 'wifi' in arguments and 'list' in arguments:
            return completed(0, self.aps)
        if arguments[1:3] == ['connection', 'modify']:
            temporary = '--temporary' in arguments
            if temporary and not self.temporary:
                return completed(2, '', "Error: invalid option '--temporary'.")
            value = arguments[-1].replace(':', '\\:')
            if self.modify_timeouts:
                self.modify_timeouts -= 1
                self.saved = value
                self.disk = self.disk if temporary else value
                raise subprocess.TimeoutExpired(arguments, timeout)
            code = self.modify_codes.pop(0) if self.modify_codes else self.modify_code
            if code == 0 or self.modify_applies:
                self.saved = value
                self.disk = self.disk if temporary else value
            return completed(code, '', 'Error: insufficient privileges' if code else '')
        if 'up' in arguments:
            return completed(self.up_codes.pop(0) if self.up_codes else 0, '', 'Error: activation failed')
        return completed(1, '', 'unexpected')

    def test_terse_parsing(self):
        self.assertEqual(_terse_fields('Home\\:Net:3C\\:84\\:6A\\:12\\:34\\:56'), ['Home:Net', '3C:84:6A:12:34:56'])

    def test_lock_changes_the_profile_reconnects_and_restores(self):
        status = self.lock.begin('wlp1s0')
        self.assertEqual((status['state'], status['locked'], status['reconnected']), ('locked', True, True))
        modify = [call for call in self.calls if call[1:3] == ['connection', 'modify']]
        self.assertEqual(modify, [['/usr/bin/nmcli', 'connection', 'modify', '--temporary', 'uuid', UUID,
                                   '802-11-wireless.bssid', '3C:84:6A:12:34:56']], 'in memory only: a restart undoes it')
        self.assertIn(['/usr/bin/nmcli', '-w', '25', 'connection', 'up', 'uuid', UUID, 'ifname', 'wlp1s0'], self.calls)
        receipt = json.loads((self.root / 'state' / 'wifi-ap-lock.json').read_text())
        self.assertEqual((receipt['uuid'], receipt['bssid']), (UUID, '3c:84:6a:12:34:56'))
        self.assertEqual(self.disk, '', 'nothing saved')
        self.disk = self.saved  # the user saved the connection (with the lock) during the session
        released = self.lock.end()
        self.assertEqual(released['state'], 'released')
        self.assertEqual(self.calls[-1][-2:], ['802-11-wireless.bssid', ''])
        self.assertNotIn('--temporary', self.calls[-1], 'cleared with a saved change')
        self.assertEqual((self.saved, self.disk), ('', ''))
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())
        self.assertFalse(any('up' in call for call in self.calls[-2:]), 'Stop does not reconnect Wi-Fi')

    def test_single_access_point_with_good_signal_is_left_alone(self):
        self.aps = 'Home\\:Net:3C\\:84\\:6A\\:12\\:34\\:56\n'
        self.assertEqual(self.lock.begin('wlp1s0')['state'], 'not_needed')
        self.assertFalse(any(call[1:3] == ['connection', 'modify'] for call in self.calls))
        # Enterprise networks scan every 300 s even with one access point.
        self.security = 'wpa-eap'
        self.assertEqual(self.lock.begin('wlp1s0')['state'], 'locked')

    def test_unknown_access_point_lists_lock_anyway(self):
        for aps in ('', 'Home\\:Net:3C\\:84\\:6A\\:12\\:34\\:57\n'):  # empty, or without the current one
            self.aps = aps
            self.assertEqual(self.lock.begin('wlp1s0')['state'], 'locked', repr(aps))
            self.lock.end()

    def test_nmcli_without_temporary_saves_the_change(self):
        self.temporary = False
        self.assertEqual(self.lock.begin('wlp1s0')['state'], 'locked')
        modify = [call for call in self.calls if call[1:3] == ['connection', 'modify']]
        self.assertEqual([('--temporary' in call) for call in modify], [True, False])
        self.assertEqual(self.lock.end()['state'], 'released')
        self.assertEqual(self.saved, '')

    def test_a_timeout_never_saves_the_lock(self):
        self.modify_timeouts = 1  # the in-memory change does not answer in time, but is applied
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'refused')
        self.assertIn('no answer', status['message'])
        saved_locks = [call for call in self.calls if call[1:3] == ['connection', 'modify']
                       and '--temporary' not in call and call[-1]]
        self.assertEqual(saved_locks, [], 'a timeout is not a rejected --temporary')
        self.assertEqual((self.saved, self.disk), ('', ''))
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())

    def test_no_new_lock_while_an_old_one_is_left(self):
        old = {'uuid': '11111111-2222-3333-4444-555555555555', 'interface': 'wlp1s0', 'bssid': 'aa:bb:cc:dd:ee:ff',
               'name': 'Old', 'time': 1}
        (self.root / 'state').mkdir(parents=True, exist_ok=True)
        (self.root / 'state' / 'wifi-ap-lock.json').write_text(json.dumps(old))
        self.get_fails = True  # that profile cannot be read now
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'unavailable')
        self.assertFalse(any(call[1:3] == ['connection', 'modify'] for call in self.calls))
        self.assertEqual(json.loads((self.root / 'state' / 'wifi-ap-lock.json').read_text()), old)

    def test_a_change_that_timed_out_is_undone(self):
        self.modify_codes, self.modify_applies = [1], True  # reported failed, but applied
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'refused')
        self.assertEqual(self.saved, '', 'the BSSID is removed again')
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())

    def test_a_lock_that_cannot_be_removed_keeps_its_receipt(self):
        self.up_codes = [4, 0]
        self.modify_codes = [0, 1, 1]  # the lock works; neither a saved nor a temporary removal does
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'failed')
        self.assertIn('retries', status['message'])
        self.assertTrue((self.root / 'state' / 'wifi-ap-lock.json').exists(), 'kept for the next start')
        restarted = AccessPointLock(self.root / 'state', runner=self.runner, log=self.lines.append,
                                    sys_class_net=self.root / 'net', iw='/usr/sbin/iw', nmcli='/usr/bin/nmcli')
        restarted.recover()
        self.assertEqual(self.saved, '')
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())

    def test_skips(self):
        self.assertEqual(self.lock.begin('enp2s0')['state'], 'wired')
        self.assertEqual(self.lock.begin('wlan0; reboot')['state'], 'unavailable')
        self.link = MLO_LINK
        self.assertEqual(self.lock.begin('wlp1s0')['state'], 'multi_link')
        self.link = LINK
        self.saved = 'AA\\:BB\\:CC\\:DD\\:EE\\:FF'
        self.assertEqual(self.lock.begin('wlp1s0')['state'], 'already_locked')
        self.assertFalse(any(call[1:3] == ['connection', 'modify'] for call in self.calls))
        no_tools = AccessPointLock(self.root / 'state', runner=self.runner, log=self.lines.append,
                                   sys_class_net=self.root / 'net', iw='', nmcli='')
        self.assertEqual(no_tools.begin('wlp1s0')['state'], 'unavailable')

    def test_refusal_and_failed_reconnect_leave_no_lock(self):
        self.modify_code = 1
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'refused')
        self.assertIn('insufficient privileges', status['message'])
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())
        self.modify_code = 0
        self.up_codes = [4, 0]
        status = self.lock.begin('wlp1s0')
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(self.saved, '', 'the BSSID was removed again')
        self.assertEqual(sum(1 for call in self.calls if 'up' in call), 2, 'reconnected without the lock')
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())

    def test_recovery_after_an_interrupted_session_respects_user_changes(self):
        self.lock.begin('wlp1s0')
        restarted = AccessPointLock(self.root / 'state', runner=self.runner, log=self.lines.append,
                                    sys_class_net=self.root / 'net', iw='/usr/sbin/iw', nmcli='/usr/bin/nmcli')
        self.saved = '11\\:22\\:33\\:44\\:55\\:66'  # the user locked another access point meanwhile
        restarted.recover()
        self.assertEqual(self.saved, '11\\:22\\:33\\:44\\:55\\:66')
        self.assertFalse((self.root / 'state' / 'wifi-ap-lock.json').exists())

    def test_runner_errors_never_escape(self):
        def broken(arguments, timeout):
            raise OSError('nmcli missing')
        lock = AccessPointLock(self.root / 'state', runner=broken, log=lambda line: None,
                               sys_class_net=self.root / 'net', iw='/usr/sbin/iw', nmcli='/usr/bin/nmcli')
        self.assertEqual(lock.begin('wlp1s0')['state'], 'unavailable')


class ConnectionKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.net = Path(self.temp.name)

    def adapter(self, name, wireless=False, **files):
        path = self.net / name
        path.mkdir()
        if wireless:
            (path / 'wireless').mkdir()
        for key, value in files.items():
            (path / key).write_text(value + '\n')
        return path

    def test_ethernet(self):
        self.adapter('enp1s0', carrier='1', speed='2500', duplex='full')
        info = connection_kind.describe('enp1s0', self.net, runner=lambda *a: completed(1), ethtool='')
        self.assertEqual((info['kind'], info['details']['link_mbps'], info['warnings']), ('ethernet', 2500, []))
        self.assertIn('Ethernet · 2.5 Gbit/s', info['summary'])
        self.adapter('enp2s0', carrier='1', speed='100', duplex='half')
        eee = 'EEE settings for enp2s0:\n\tEEE status: enabled - active\n'
        info = connection_kind.describe('enp2s0', self.net, runner=lambda *a: completed(0, eee), ethtool='/usr/sbin/ethtool')
        self.assertEqual(len(info['warnings']), 2)
        self.assertIn('100 Mbit/s', info['warnings'][0])
        self.assertIn('Energy-Efficient Ethernet', info['notes'][0])
        self.adapter('enp3s0', carrier='0')
        self.assertIn('no cable link', connection_kind.describe('enp3s0', self.net, ethtool='')['summary'])

    def test_wifi(self):
        self.adapter('wlp1s0', wireless=True)
        info = connection_kind.describe('wlp1s0', self.net, runner=lambda *a: completed(0, LINK), iw='/usr/sbin/iw')
        self.assertEqual((info['kind'], info['details']['band'], info['details']['signal_dbm']), ('wifi', '6 GHz', -41))
        self.assertEqual(info['details']['ssid'], 'Home:Net')
        self.assertIn('2.9 Gbit/s link', info['summary'])
        weak = LINK.replace('freq: 6115.0', 'freq: 2437.0').replace('signal: -41', 'signal: -74')
        info = connection_kind.describe('wlp1s0', self.net, runner=lambda *a: completed(0, weak), iw='/usr/sbin/iw')
        self.assertEqual(len(info['warnings']), 2)
        info = connection_kind.describe('wlp1s0', self.net, runner=lambda *a: completed(0, MLO_LINK), iw='/usr/sbin/iw')
        self.assertEqual((info['details']['multi_link'], info['details']['signal_dbm']), (2, -47))

    def test_invalid(self):
        self.assertEqual(connection_kind.describe('', self.net)['kind'], 'unknown')
        self.assertEqual(connection_kind.describe('../../etc', self.net)['kind'], 'unknown')
        self.assertEqual(connection_kind.describe('eth9', self.net)['kind'], 'unknown')


class ReportTests(unittest.TestCase):
    def test_names_and_info(self):
        when = time.mktime((2026, 10, 3, 19, 45, 12, 0, 0, -1))
        self.assertEqual(reporting.report_name('client', '1.0.0-rc4.2', when),
                         'VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json')
        self.assertEqual(reporting.report_name('host', '1.0.0-rc4.2', when),
                         'VROSTBridge-Host-report_1.0.0-rc4.2_2026-10-03_19-45-12.json')
        report = reporting.stamped({'role': 'client', 'logs': []}, 'client', '1.0.0-rc4.2', when)
        self.assertEqual(list(report)[0], 'report_info')
        self.assertEqual((report['report_info']['end'], report['report_info']['version']), ('client', '1.0.0-rc4.2'))
        self.assertTrue(reporting.REPORT_NAME.fullmatch(report['report_info']['file_name']))
        self.assertTrue(report['report_info']['saved_local'].startswith('2026-10-03 19:45:12'))

    def test_encoding_is_bounded(self):
        value = {'role': 'client', 'logs': ['x' * 1000] * 50}
        self.assertEqual(reporting.decode(reporting.encode(value)), value)
        with self.assertRaises(ValueError):
            reporting.decode(gzip.compress(b'{"a":"' + b'x' * 5000 + b'"}'), limit=1000)
        with self.assertRaises(ValueError):
            reporting.decode(gzip.compress(b'[1, 2]'))

    def test_received_reports_are_kept_and_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            store = reporting.ReceivedReports(directory)
            name = 'VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json'
            self.assertEqual(store.save(name, {'role': 'client'}), name)
            self.assertEqual(store.save(name, {'role': 'client', 'n': 2}), name[:-5] + '-1.json', 'never overwritten')
            for bad in ('../x.json', 'VROSTBridge-Host-report_1_2026-10-03_19-45-12.json', 'evil.json',
                        name[:-5] + '-3.json'):  # a suffix is the host's to add
                with self.assertRaises(ValueError):
                    store.save(bad, {})
            self.assertEqual(json.loads(store.read(name)), {'role': 'client'})
            with self.assertRaises(ValueError):
                store.read('../../secret.json')
            for i in range(25):
                path = Path(directory) / ('VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-%02d-00.json' % i)
                store.save(path.name, {'role': 'client'})
            self.assertEqual(len(store.list()), reporting.KEEP_RECEIVED)
            self.assertEqual(len(list(Path(directory).glob('*.json'))), reporting.KEEP_RECEIVED)

    def test_received_reports_are_compact_and_bounded_in_total(self):
        self.assertEqual(reporting.DECODED_LIMIT, 16 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            store = reporting.ReceivedReports(directory, keep_bytes=3000)
            names = ['VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-%02d-00.json' % i for i in range(5)]
            for index, name in enumerate(names):
                store.save(name, {'role': 'client', 'logs': ['x' * 1000], 'nested': {'a': [1, 2]}})
                os.utime(Path(directory) / name, (1000 + index, 1000 + index))
            raw = (Path(directory) / names[-1]).read_bytes()
            self.assertNotIn(b'\n', raw, 'stored compact: never larger than what was decoded')
            self.assertEqual(json.loads(raw)['nested'], {'a': [1, 2]})
            self.assertEqual(sorted(path.name for path in Path(directory).glob('*.json')), names[-2:])
            alone = reporting.ReceivedReports(Path(directory) / 'small', keep_bytes=10)
            self.assertEqual(alone.save(names[0], {'role': 'client'}), names[0], 'the newest is always kept')
            self.assertEqual(len(alone.list()), 1)
            raw = b'{"role":"client","n":1e9}'
            stored = alone.save(names[1], raw)
            self.assertEqual(alone.read(stored), raw, 'received bytes are stored unchanged')

    def test_driver_log_drops_graph_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session_log.txt'
            lines = []
            for i in range(4000):
                lines.append('18:44:%02d.%03d [GRAPH] ' % (i % 60, i % 1000))
                if i % 40 == 0:
                    lines.append('18:44:00 INFO step %d' % i)
                if i % 10 == 0:
                    lines.append('18:44:00 [STATS] {"n":%d}' % i)
            lines.append('18:45:00 {"Tracking":1}')
            path.write_text('\n'.join(lines))
            text = reporting.filtered_driver_log(path, keep_stats=5, drop=('"Tracking"',))
            self.assertNotIn('[GRAPH]', text)
            self.assertNotIn('Tracking', text)
            self.assertEqual(text.count('[STATS]'), 5)
            self.assertEqual(text.count('INFO step'), 100)


class ReportUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = reporting.ReceivedReports(self.root / 'received')
        self.logs = []
        self.host = SessionHost(self.root / 'host', Mock(), Mock(), lambda: {'runtime_state': 'ready', 'available': True},
                                Mock(return_value=True), self.logs.append, port=0, address_provider=lambda: [],
                                test_peer=lambda peer: peer == '127.0.0.1',
                                report=lambda name, report, peer: self.store.save(name, report))
        self.addCleanup(self.host.close)
        self.client = SessionClient(self.root / 'client', self.logs.append, port=self.host.port,
                                    address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(self.client.close)

    def test_client_report_reaches_the_host(self):
        self.assertIn('report-upload', self.host.hello()['features'])
        report = reporting.stamped({'role': 'client', 'logs': ['line'] * 2000}, 'client', '1.0.0-rc4.2')
        saved = self.client.send_report(report['report_info']['file_name'], report, host_ip='127.0.0.1')
        self.assertEqual(saved, report['report_info']['file_name'])
        self.assertEqual(json.loads(self.store.read(saved))['logs'][0], 'line')

    def test_host_validation(self):
        credentials = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        self.host.dispatch('/pair', dict(credentials), '127.0.0.1')
        good = base64.b64encode(reporting.encode({'role': 'client'})).decode()
        name = reporting.report_name('client', '1.0.0-rc4.2')
        with self.assertRaises(SessionError):
            self.host.dispatch('/report', dict(credentials, name=name, data=base64.b64encode(
                reporting.encode({'role': 'host'})).decode()), '127.0.0.1')
        self.host.last_report = -1e9
        with self.assertRaises(SessionError):
            self.host.dispatch('/report', dict(credentials, name=name, data='not base64!'), '127.0.0.1')
        self.host.last_report = -1e9
        with self.assertRaises(SessionError):
            self.host.dispatch('/report', {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32),
                                           'name': name, 'data': good}, '127.0.0.1')
        self.host.last_report = -1e9
        self.host.dispatch('/report', dict(credentials, name=name, data=good), '127.0.0.1')
        with self.assertRaises(SessionError):  # rate limited
            self.host.dispatch('/report', dict(credentials, name=name, data=good), '127.0.0.1')

    def credentials(self):
        credentials = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        self.host.dispatch('/pair', dict(credentials), '127.0.0.1')
        return credentials

    def test_damaged_uploads_are_refused_cleanly(self):
        credentials = self.credentials()
        name = reporting.report_name('client', '1.0.0-rc4.2')
        packed = gzip.compress(json.dumps({'role': 'client', 'logs': ['y' * 5000]}).encode())
        damaged = packed[:10] + b'\xff' * 8 + packed[18:]  # invalid deflate block: zlib.error
        for data in (damaged, packed[:40]):  # and a truncated stream: EOFError
            self.host.last_report = -1e9
            with self.assertRaisesRegex(SessionError, 'could not be read'):
                self.host.dispatch('/report', dict(credentials, name=name, data=base64.b64encode(data).decode()),
                                   '127.0.0.1')

    def test_reports_follow_the_host_network_policy(self):
        credentials = self.credentials()
        self.host.allow_start = lambda: False
        good = base64.b64encode(reporting.encode({'role': 'client'})).decode()
        with self.assertRaisesRegex(SessionError, 'unavailable on this network'):
            self.host.dispatch('/report', dict(credentials, name=reporting.report_name('client', 'x'), data=good),
                               '127.0.0.1')

    def post_report(self, headers, body=b'{}'):
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', self.host.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request('POST', '/report', body=body, headers=dict(
            {'X-VROSTBridge-Session': '1', 'Content-Type': 'application/json'}, **headers))
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_report_bodies_are_read_only_for_paired_clients(self):
        status, reply = self.post_report({}, b'[' + b'[],' * 200000 + b'[]]')
        self.assertEqual((status, reply['error']), (403, 'Companion is not paired with this host.'))
        credentials = self.credentials()
        headers = {'X-VROSTBridge-Client': credentials['client_id'], 'X-VROSTBridge-Token': credentials['token']}
        status, reply = self.post_report(dict(headers, **{'X-VROSTBridge-Token': secrets.token_hex(32)}))
        self.assertEqual(status, 403, 'a wrong token')
        self.host.report_read.acquire()
        try:
            status, reply = self.post_report(headers)
            self.assertEqual(status, 409, 'one report at a time')
        finally:
            self.host.report_read.release()

    def test_an_upload_never_delays_stop(self):
        entered, release = threading.Event(), threading.Event()
        original = self.client._request

        def slow(ip, path, value=None, timeout=5, headers=None):
            if path == '/report':
                entered.set()
                release.wait(5)
            return original(ip, path, value, timeout=timeout, headers=headers)
        self.client._request = slow
        report = reporting.stamped({'role': 'client'}, 'client', '1.0.0-rc4.2')
        worker = threading.Thread(target=self.client.send_report,
                                  args=(report['report_info']['file_name'], report, '127.0.0.1'))
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        self.client.stop()
        self.assertLess(time.monotonic() - started, 2)

    def test_old_host_is_reported(self):
        old = SessionHost(self.root / 'old', Mock(), Mock(), lambda: {}, Mock(return_value=True), self.logs.append,
                          port=0, address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(old.close)
        self.assertNotIn('report-upload', old.hello()['features'])
        client = SessionClient(self.root / 'client2', self.logs.append, port=old.port, address_provider=lambda: [],
                               test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(client.close)
        with self.assertRaisesRegex(SessionError, 'rc4.2 or later'):
            client.send_report(reporting.report_name('client', 'x'), {'role': 'client'}, host_ip='127.0.0.1')


def vdf_entry(index, name, exe, options=b''):
    return (steam_shortcut.MAP, str(index).encode(), [
        (steam_shortcut.INT32, b'appid', -12345),
        (steam_shortcut.STRING, b'AppName', name),
        (steam_shortcut.STRING, b'Exe', exe),
        (steam_shortcut.STRING, b'StartDir', b'"/games/"'),
        (steam_shortcut.STRING, b'LaunchOptions', options),
        (steam_shortcut.FLOAT32, b'scale', struct.pack('<f', 1.5)),
        (steam_shortcut.UINT64, b'big', struct.pack('<Q', 2 ** 40)),
        (steam_shortcut.MAP, b'tags', [(steam_shortcut.STRING, b'0', b'favorite')]),
    ])


class SteamShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        steam = self.home / '.local/share/Steam'
        self.config = steam / 'userdata/123456/config'
        self.config.mkdir(parents=True)
        (steam / 'userdata/999/config').mkdir(parents=True)
        (steam / 'config').mkdir()
        (steam / 'config/loginusers.vdf').write_text('"users"\n{\n\t"%d"\n\t{\n\t\t"AccountName"\t\t"me"\n\t\t'
                                                     '"MostRecent"\t\t"1"\n\t}\n}\n' % (steam_shortcut.STEAMID64_BASE + 123456))
        install = self.home / '.local/share/vrostbridge/current'
        install.mkdir(parents=True)
        self.exe = install / 'VROSTBridge-Client'
        self.exe.write_text('#!/bin/sh\n')
        self.art = ROOT / 'launcher/brand/steam'
        self.running = [True]
        self.commands = []
        self.lines = []

        def runner(command, **kwargs):
            self.commands.append(command)
            if command[-1] == '-shutdown':
                self.running[0] = False
            return completed()

        def spawn(command, **kwargs):
            self.commands.append(command)
            self.running[0] = True
        self.shortcut = steam_shortcut.SteamShortcut(install, self.exe, self.art, self.home / 'receipt.json',
                                                     log=self.lines.append, home=self.home, steam=['/usr/bin/steam'],
                                                     processes=lambda: [42] if self.running[0] else [],
                                                     runner=runner, spawn=spawn, sleep=lambda s: None)
        other = steam_shortcut.dump_shortcuts([vdf_entry(0, b'Some Game', b'"/games/game"')[2],
                                               vdf_entry(1, b'VROSTBridge Game Mode', b'"' + bytes(self.exe) + b'"',
                                                         b'--role client --game-mode')[2]])
        (self.config / 'shortcuts.vdf').write_bytes(other)
        self.original = other

    def test_codec_round_trip(self):
        entries = steam_shortcut.load_shortcuts(self.original)
        self.assertEqual(steam_shortcut.dump_shortcuts(entries), self.original)
        self.assertEqual(steam_shortcut.load_shortcuts(b''), [])
        self.assertEqual(steam_shortcut.load_shortcuts(b'\x00shortcuts\x00\x08\x08'), [])
        for broken in (self.original[:-1], self.original + b'\x00', b'\x00shortcuts\x00\x09x\x00\x08\x08'):
            with self.assertRaises(steam_shortcut.FormatError):
                steam_shortcut.load_shortcuts(broken)

    def test_add_replaces_old_entries_keeps_others_and_restarts_steam(self):
        status = self.shortcut.add()
        self.assertTrue(status['added'])
        self.assertEqual(self.commands, [['/usr/bin/steam', '-shutdown'], ['/usr/bin/steam', '-silent']])
        entries = steam_shortcut.load_shortcuts((self.config / 'shortcuts.vdf').read_bytes())
        self.assertEqual([steam_shortcut.field(e, 'AppName') for e in entries], [b'Some Game', b'VROSTBridge'])
        self.assertEqual(entries[0], steam_shortcut.load_shortcuts(self.original)[0], 'other shortcuts unchanged')
        ours = entries[1]
        quoted = '"' + str(self.exe) + '"'
        appid = steam_shortcut.shortcut_appid(quoted)
        self.assertEqual(steam_shortcut.field(ours, 'appid') & 0xFFFFFFFF, appid)
        self.assertEqual(steam_shortcut.field(ours, 'LaunchOptions'), b'--role client --game-mode')
        grid = self.config / 'grid'
        for name in ('%dp.jpg', '%d.jpg', '%d_hero.jpg', '%d_logo.png', '%d.json'):
            self.assertTrue((grid / (name % appid)).is_file(), name)
        self.assertEqual((self.config / 'shortcuts.vdf.vrostbridge-backup').read_bytes(), self.original)
        self.assertFalse((self.home / '.local/share/Steam/userdata/999/config/shortcuts.vdf').exists(),
                         'only the most recent account is changed')
        # Removing deletes our entry and artwork, but keeps artwork the user replaced.
        (grid / ('%d_hero.jpg' % appid)).write_bytes(b'my own picture')
        status = self.shortcut.remove()
        self.assertFalse(status['added'])
        entries = steam_shortcut.load_shortcuts((self.config / 'shortcuts.vdf').read_bytes())
        self.assertEqual([steam_shortcut.field(e, 'AppName') for e in entries], [b'Some Game'])
        self.assertFalse((grid / ('%dp.jpg' % appid)).exists())
        self.assertTrue((grid / ('%d_hero.jpg' % appid)).exists())

    def test_refusals(self):
        (self.config / 'shortcuts.vdf').write_bytes(b'\x00shortcuts\x00\x09odd\x00\x08\x08')
        with self.assertRaisesRegex(ValueError, 'format'):
            self.shortcut.add()
        self.assertEqual(self.commands, [], 'Steam is not closed for a file that cannot be changed')
        self.shortcut.in_game_mode = lambda: True
        with self.assertRaisesRegex(ValueError, 'Desktop Mode'):
            self.shortcut.add()

    def test_steam_gets_a_clean_environment(self):
        seen = []

        def runner(command, **kwargs):
            seen.append(kwargs.get('env'))
            self.running[0] = False
            return completed()
        self.shortcut.runner, self.shortcut.spawn = runner, lambda command, **kwargs: seen.append(kwargs.get('env'))
        with mock.patch.dict(os.environ, {'LD_LIBRARY_PATH': '/tmp/_MEIabc123'}):
            os.environ.pop('LD_LIBRARY_PATH_ORIG', None)
            self.shortcut.add()
        self.assertEqual(len(seen), 2, '-shutdown and -silent')
        for env in seen:
            self.assertIsInstance(env, dict)
            self.assertNotIn('LD_LIBRARY_PATH', env, "PyInstaller's library path never reaches Steam")

    def test_steam_that_does_not_close(self):
        self.shortcut.runner = lambda command, **kwargs: completed()
        clock = iter(range(0, 1000, 5))
        self.shortcut.clock = lambda: next(clock)
        with self.assertRaisesRegex(ValueError, 'did not close'):
            self.shortcut.add()
        self.assertEqual((self.config / 'shortcuts.vdf').read_bytes(), self.original)

    def test_artwork_exists(self):
        try:
            from PIL import Image  # build-time check of the shipped artwork sizes
        except ImportError:
            self.skipTest('Pillow is not installed (pip install pillow)')
        for name, size in (('capsule.jpg', (600, 900)), ('header.jpg', (920, 430)), ('hero.jpg', (3840, 1240)),
                           ('logo.png', (1280, 480)), ('icon.png', (256, 256))):
            with Image.open(self.art / name) as image:
                self.assertEqual(image.size, size, name)


class GameModeDisplayTests(unittest.TestCase):
    def test_headsets_rank_after_the_built_in_screen(self):
        self.assertEqual(rank_headsets_last('*,eDP-1', ['DP-1']), '*,eDP-1,DP-1')
        self.assertEqual(rank_headsets_last('DP-1,eDP-1', ['DP-1']), 'eDP-1,*,DP-1')
        self.assertEqual(prefer_output(['--prefer-output', '*,eDP-1', '--steam'], ['DP-2']),
                         ['--prefer-output', '*,eDP-1,DP-2', '--steam'])
        self.assertEqual(prefer_output(['-O', '*,eDP-1'], ['DP-2'])[1], '*,eDP-1,DP-2')
        self.assertEqual(prefer_output(['--prefer-output=*,eDP-1'], ['DP-2']), ['--prefer-output=*,eDP-1,DP-2'])
        self.assertEqual(prefer_output(['--steam'], ['DP-2']), ['--prefer-output', '*,DP-2', '--steam'])
        self.assertEqual(prefer_output(['--steam'], []), ['--steam'])

    @staticmethod
    def edid(maker, product):
        code = sum((ord(letter) - 64) << shift for letter, shift in zip(maker, (10, 5, 0)))
        data = bytearray(b'\x00\xff\xff\xff\xff\xff\xff\x00' + code.to_bytes(2, 'big') + product.to_bytes(2, 'little')
                         + bytes(115))
        data.append(-sum(data) % 256)
        return bytes(data)

    def test_only_real_headset_outputs_rank_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def output(name, status, edid=b''):
                (root / name).mkdir()
                (root / name / 'status').write_text(status + '\n')
                (root / name / 'edid').write_bytes(edid)
            output('card0-eDP-1', 'connected', self.edid('BOE', 0x0BCA))
            output('card0-DP-1', 'connected', self.edid('VLV', 0x91A8))  # the Index, now
            output('card0-DP-2', 'connected', self.edid('DEL', 0x4321))  # a monitor where a headset once was
            output('card0-DP-3', 'disconnected')
            outputs = connected_outputs(root)
            self.assertEqual(outputs, {'eDP-1': '', 'DP-1': 'Valve Index', 'DP-2': ''})
            ranked = headset_outputs(['DP-2', 'DP-3'], outputs)
            self.assertEqual(ranked, ['DP-1', 'DP-3'], 'a monitor on a former headset port keeps its place')
            self.assertEqual(prefer_output(['--prefer-output', '*,eDP-1'], ranked),
                             ['--prefer-output', '*,eDP-1,DP-1,DP-3'])

    def test_wrapper_always_starts_gamescope(self):
        calls = []
        row = {'id': 'card0-DP-1', 'name': 'DP-1', 'card': '/dev/dri/card0', 'connector_id': 77, 'edid_hex': '',
               'model': 'Valve Index'}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), \
                mock.patch('launcher.game_mode.os.execve', side_effect=lambda path, args, env: calls.append((args, env))):
            with mock.patch('launcher.game_mode.connectors', side_effect=KeyError('broken sysfs')):
                gamescope_wrapper(['--prefer-output', '*,eDP-1', '--steam'])
            self.assertEqual(calls[-1][0], ['/usr/bin/gamescope', '--prefer-output', '*,eDP-1', '--steam'])
            with mock.patch('launcher.game_mode.connectors', return_value=[row]), \
                    mock.patch('launcher.game_mode.connected_outputs', return_value={'eDP-1': '', 'DP-1': 'Valve Index'}), \
                    mock.patch('launcher.game_mode.unique', return_value=True), \
                    mock.patch('launcher.game_mode.runtime_dir', return_value=Path(directory)), \
                    mock.patch('launcher.game_mode.process_identity', return_value=None):  # TypeError inside
                gamescope_wrapper(['--prefer-output', '*,eDP-1'])
            args, env = calls[-1]
            self.assertEqual(args, ['/usr/bin/gamescope', '--prefer-output', '*,eDP-1,DP-1'])
            self.assertNotIn('GAMESCOPE_LEASE_SOCK', env)

    def test_wrapper_script_falls_back_and_is_updated(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
            setup = GameMode(Path(directory) / 'versions' / 'removed')  # its VROSTBridge-Client does not exist
            fake = Path(directory) / 'gamescope'
            fake.write_text('#!/bin/sh\necho plain "$@"\n')
            fake.chmod(0o755)
            script = Path(directory) / 'wrapper'
            script.write_text(setup.wrapper_text().replace('/usr/bin/gamescope', str(fake)))
            script.chmod(0o755)
            result = subprocess.run([str(script), '--steam'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), 'plain --steam')
            # A wrapper written by an earlier release is brought up to date, and stays ours.
            old = '#!/bin/sh\n' + MARKER + '\nexec ' + shlex.quote(str(setup.executable)) + ' --gamescope-wrapper "$@"\n'
            setup.wrapper.parent.mkdir(parents=True)
            setup.wrapper.write_text(old)
            setup.receipt.write_text(json.dumps({'version': 1, 'files': {str(setup.wrapper): digest(old.encode())},
                                                 'previous': {}}))
            self.assertTrue(setup.update_wrapper())
            self.assertEqual(setup.wrapper.read_text(), setup.wrapper_text())
            self.assertTrue(setup.owned(setup.wrapper, json.loads(setup.receipt.read_text())))
            self.assertFalse(setup.update_wrapper(), 'already up to date')
            setup.wrapper.write_text('#!/bin/sh\n# my own\n')
            self.assertFalse(setup.update_wrapper(), 'a wrapper changed by the user is never touched')
            self.assertEqual(setup.wrapper.read_text(), '#!/bin/sh\n# my own\n')

    def test_headset_connectors_are_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(remember_headsets(directory, [{'name': 'DP-1'}]), ['DP-1'])
            self.assertEqual(remember_headsets(directory, [{'name': 'DP-2'}, {'name': 'bad name'}]), ['DP-2', 'DP-1'])
            self.assertEqual(known_headsets(directory), ['DP-2', 'DP-1'])


class GameModeScreenTests(unittest.TestCase):
    def test_focus_moves_to_the_nearest_control_in_that_direction(self):
        import threading
        from launcher.game_ui import View
        view = View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        # A 2 x 2 grid plus a wide button below, like the main page.
        view.buttons = [('a', 'A', (0, 0, 100, 50), True), ('b', 'B', (120, 0, 100, 50), True),
                        ('c', 'C', (0, 70, 100, 50), True), ('d', 'D', (120, 70, 100, 50), False),
                        ('e', 'E', (0, 140, 220, 50), True)]
        view.focus = 0
        view.move(1, 0)
        self.assertEqual(view.focus, 1)
        view.move(0, 1)
        self.assertEqual(view.focus, 4, 'disabled controls are skipped')
        view.move(0, -1)
        self.assertIn(view.focus, (2, 1))
        view.focus = 4
        view.move(0, 1)
        self.assertEqual(view.focus, 0, 'down from the last row wraps to the top')

    def test_reports_use_the_report_file_name(self):
        import threading
        from launcher import game_ui
        from unittest import mock
        report = reporting.stamped({'role': 'client'}, 'client', '1.0.0-rc4.2')
        view = game_ui.View(ROOT, lambda: {}, Mock(), lambda: report, threading.Event())
        with tempfile.TemporaryDirectory() as directory, mock.patch('launcher.app.state_directory',
                                                                     lambda: Path(directory)):
            view.save_report()
            self.assertTrue((Path(directory) / 'reports' / report['report_info']['file_name']).is_file())


class SendReportTests(unittest.TestCase):
    def wait(self, fake, state):
        deadline = time.monotonic() + 5
        while fake.report_delivery['state'] != state and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(fake.report_delivery['state'], state)

    def test_sending_runs_in_the_background_and_reports_the_outcome(self):
        from launcher.app import Application
        release = threading.Event()
        link = Mock()
        link.send_report.side_effect = lambda name, report, host_ip: release.wait(5) and name
        fake = types.SimpleNamespace(session_link=link, report_send_lock=threading.Lock(), settings={'host_ip': ''},
                                     log=lambda line: None,
                                     report=lambda: reporting.stamped({'role': 'client'}, 'client', '1.0.0-rc4.2'))
        Application.send_report_async(fake)  # returns at once: Start and Stop are never held up
        self.assertEqual(fake.report_delivery['state'], 'sending')
        with self.assertRaisesRegex(ValueError, 'already being sent'):
            Application.send_report_async(fake)
        release.set()
        self.wait(fake, 'sent')
        self.assertIn('Download it on the host PC', fake.report_delivery['message'])
        link.send_report.side_effect = SessionError('The host PC is not reachable.')
        Application.send_report_async(fake)
        self.wait(fake, 'error')
        self.assertIn('not reachable', fake.report_delivery['message'])
        self.assertFalse(fake.report_send_lock.locked())


class AsyncLogTests(unittest.TestCase):
    def test_settings_rewritten_after_the_run_do_not_hide_the_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'vrcompositor.txt'
            log.write_text('Global priority query enabled: 1\nAsync support disabled by user setting or a direct '
                           'mode driver\n')
            os.utime(log, (100, 100))
            settings = root / 'steamvr.vrsettings'
            settings.write_text('{}')  # SteamVR rewrote it at shutdown, after the log
            check = AsyncReprojectionCheck(compositor_logs=[log], log=lambda line: None, state_directory=root)
            self.assertTrue(check._compositor_reports_disabled(settings))
            (root / 'steamvr-async-repair.json').write_text(json.dumps({'time': 200}))
            self.assertIsNone(check._compositor_reports_disabled(settings), 'a later repair hides the old run')


class HostRepackPruneTests(unittest.TestCase):
    def test_files_removed_from_the_companion_leave_the_host_payload(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('repack_host_payload', ROOT / 'packaging/repack_host_payload.py')
        repack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repack)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, payload = root / 'launcher', root / 'payload' / 'launcher'
            (source / 'game_ui_assets').mkdir(parents=True)
            (payload / 'game_ui_assets').mkdir(parents=True)
            (source / 'app.py').write_text('new')
            (payload / 'app.py').write_text('old')
            (payload / 'game_ui_assets' / 'font.rgba.z').write_bytes(b'atlas')
            removed = repack.prune(source, payload)
            self.assertEqual([path.name for path in removed], ['font.rgba.z'])
            self.assertTrue((payload / 'app.py').is_file(), 'files still in the source are kept for the overlay')


class PackagingContracts(unittest.TestCase):
    def test_fonts_and_hud_assets(self):
        for name in ('Inter-Regular.ttf', 'Inter-Medium.ttf', 'Inter-SemiBold.ttf', 'Outfit-SemiBold.ttf',
                     'Inter-Variable.ttf', 'Outfit-Variable.ttf', 'Inter-OFL.txt', 'Outfit-OFL.txt'):
            self.assertTrue((ROOT / 'launcher/fonts' / name).is_file(), name)
        self.assertEqual((ROOT / 'client/assets/hud/logo-192.rgba').stat().st_size, 192 * 192 * 4)
        self.assertTrue((ROOT / 'launcher/game_ui_assets/background.jpg').is_file())
        cmake = (ROOT / 'CMakeLists.txt').read_text()
        self.assertIn('reaim.spv', cmake)
        self.assertIn('assets/fonts', cmake)
        repack = (ROOT / 'packaging/repack_host_payload.py').read_text()
        self.assertIn('.ttf', repack, 'companion-only host updates carry the page fonts')


if __name__ == '__main__':
    unittest.main()
