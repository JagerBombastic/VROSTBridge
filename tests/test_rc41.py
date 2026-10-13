"""RC4.1: single motion prediction, AMF drain (driver), stream recovery, Wi-Fi robustness,
faster startup, diagnostics and foveation status. No SteamVR, NetworkManager or Windows."""
import errno
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock, patch

from headset_fixture import HEADSET
from host import audio, power
from host.control import HostControl
from host.profile import configure, session_client_address
from host.stream_options import (INIT_APPLIED_FILE, INIT_FILE, PATCH_FEATURES, driver_features, init_hint,
                                 init_outcome, stream_config, write_init_hint)
from host.stream_recovery import StreamRecovery
from launcher import steamvr_async
from launcher.client_runtime import ClientRuntime, watchdog_aborted
from launcher.session_link import (FEATURES, RECOVERY_CAPABILITY, STREAM_CAPABILITY, QUALITY_CAPABILITY,
                                   SessionClient, SessionError, SessionHost, host_settings, session_settings,
                                   stream_report)
from launcher.steamvr_async import AsyncReprojectionCheck, last_start_async_disabled
from launcher.wifi_others import OtherWifiPause

ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = '7f3a.client.local.'


def completed(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def default_document():
    return json.loads((ROOT / 'host/default-session.json').read_text())


class SinglePredictionTests(unittest.TestCase):
    def test_client_samples_current_poses_and_only_labels_the_target(self):
        device = (ROOT / 'client/device/openvr_device.cpp').read_text()
        self.assertIn('planPoseSample(clock_, sampleNow, offset)', device)
        self.assertIn('sample.secondsFromNow, raw.data()', device)
        self.assertNotIn('float(double(applied) / 1e9)', device, 'RC4 sampled SteamVR at the target time')
        header = (ROOT / 'client/device/prediction.hpp').read_text()
        self.assertIn('return {0.f, clock.target(now, offset)};', header)
        self.assertNotIn('it does not extrapolate the head', header)

    def test_prediction_switch_reaches_the_driver_only_when_supported(self):
        prefs = session_settings({'host_prediction': False})
        self.assertFalse(stream_config(prefs, ['prediction-switch'])['host_prediction'])
        self.assertTrue(stream_config(prefs, [])['host_prediction'], 'an older driver always predicts')
        self.assertTrue(stream_config(session_settings({}), ['prediction-switch'])['host_prediction'])
        self.assertTrue(stream_config(session_settings({'host_prediction': True}), ['prediction-switch'])['host_prediction'])

    def test_driver_patch_features(self):
        self.assertEqual(PATCH_FEATURES['0014-host-prediction-switch'], ('prediction-switch',))
        self.assertEqual(PATCH_FEATURES['0015-host-init-config'], ('init-config',))
        with tempfile.TemporaryDirectory() as directory:
            manifest = {'source_patch_sha256': {'0014-host-prediction-switch.patch': 'a', '0015-host-init-config.patch': 'b'}}
            (Path(directory) / 'vrostbridge-build.json').write_text(json.dumps(manifest))
            features = driver_features(directory)
        self.assertTrue({'prediction-switch', 'init-config', 'stream-recovery'} <= set(features))
        self.assertTrue(set(features) <= set(FEATURES), 'every host feature survives the client filter')

    def test_patches_are_present_and_ordered(self):
        names = sorted(path.name for path in (ROOT / 'patches').glob('00*.patch'))
        first = names.index('0013-host-amf-output-drain.patch')  # later patches follow them (1.0.0.3: 0016)
        self.assertEqual(names[first:first + 3], ['0013-host-amf-output-drain.patch', '0014-host-prediction-switch.patch',
                                                  '0015-host-init-config.patch'])
        drain = (ROOT / 'patches/0013-host-amf-output-drain.patch').read_text()
        self.assertIn('CollectOutputs', drain)
        self.assertIn('backlog_cleared', drain)
        switch = (ROOT / 'patches/0014-host-prediction-switch.patch').read_text()
        self.assertIn('host_prediction', switch)
        init = (ROOT / 'patches/0015-host-init-config.patch').read_text()
        self.assertIn('vrostbridge-init.applied.json', init)
        self.assertIn('restart_settings_hash', init)


class SessionFieldTests(unittest.TestCase):
    def test_rc41_fields_validate_and_are_stripped_for_older_hosts(self):
        value = session_settings({'host_prediction': False, 'client_hostname': HOSTNAME})
        self.assertEqual((value['host_prediction'], value['client_hostname']), (False, HOSTNAME))
        for bad in ({'host_prediction': 0}, {'client_hostname': 'x.local.'}, {'client_hostname': 'a b.client.local.'},
                    {'client_hostname': 7}, {'client_hostname': 'a' * 120 + '.client.local.'}):
            with self.assertRaises(SessionError):
                session_settings(bad)
        rc4_host = [QUALITY_CAPABILITY, STREAM_CAPABILITY]
        self.assertNotIn('host_prediction', host_settings(value, rc4_host))
        self.assertNotIn('client_hostname', host_settings(value, rc4_host))
        kept = host_settings(value, rc4_host + [RECOVERY_CAPABILITY])
        self.assertEqual((kept['host_prediction'], kept['client_hostname']), (False, HOSTNAME))

    def test_stream_report_is_validated(self):
        self.assertEqual(stream_report({'up': False, 'down_s': 12.34, 'state': 'searching'}),
                         {'up': False, 'down_s': 12.3, 'state': 'searching'})
        for bad in ({'up': 1}, {'down_s': -1}, {'down_s': float('nan')}, {'state': 'hacked'}, {'extra': 1}, []):
            with self.assertRaises(SessionError):
                stream_report(bad)


class SessionHostRecoveryLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = [100.0]
        self.started = Mock()
        self.host = SessionHost(root / 'host', self.started, Mock(), lambda: {'runtime_state': 'ready', 'available': True},
                                Mock(return_value=True), lambda line: None, port=0, address_provider=lambda: [],
                                test_peer=lambda peer: peer == '127.0.0.1',
                                capabilities=(QUALITY_CAPABILITY, STREAM_CAPABILITY, RECOVERY_CAPABILITY))
        self.addCleanup(self.host.close)

    def test_heartbeat_stream_report_reaches_the_lease(self):
        owner = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        self.host.dispatch('/pair', owner, '127.0.0.1')
        result = self.host.dispatch('/start', dict(owner, request_id=secrets.token_hex(16),
                                                   settings={'client_hostname': HOSTNAME}), '127.0.0.1')
        lease = result['lease_id']
        details = self.host.lease_details()
        self.assertEqual((details['client_hostname'], details['ip'], details['stream']), (HOSTNAME, '127.0.0.1', None))
        self.host.dispatch('/heartbeat', dict(owner, lease_id=lease, stream={'up': False, 'down_s': 5.0, 'state': 'searching'}),
                           '127.0.0.1')
        details = self.host.lease_details()
        self.assertEqual(details['stream']['state'], 'searching')
        self.assertGreaterEqual(details['stream']['down_s'], 5.0)
        # An invalid optional report is ignored, never fatal to the heartbeat.
        self.host.dispatch('/heartbeat', dict(owner, lease_id=lease, stream={'up': True, 'state': 'future-state'}), '127.0.0.1')
        self.assertEqual(self.host.lease_details()['stream']['state'], 'searching')
        with self.assertRaises(SessionError):
            self.host.dispatch('/stop', dict(owner, lease_id=lease, stream={'up': True}), '127.0.0.1')

    def test_client_adds_the_report_only_for_rc41_hosts(self):
        client = SessionClient.__new__(SessionClient)
        client.lock = threading.RLock()
        client.lease_id, client.host_ip, client.client_id, client.token = 'b' * 32, '192.168.1.2', 'a' * 32, 'c' * 64
        client.stream_status = lambda: {'up': True, 'down_s': 0, 'state': 'streaming'}
        sent = []
        client._request = lambda ip, path, value=None, timeout=5: sent.append(dict(value)) or {}
        client._accept = lambda result: None
        client.snapshot = lambda: {}
        client._persist = lambda: None
        client.host_capabilities = [QUALITY_CAPABILITY, STREAM_CAPABILITY]
        client.heartbeat()
        self.assertNotIn('stream', sent[-1])
        client.host_capabilities.append(RECOVERY_CAPABILITY)
        client.heartbeat()
        self.assertEqual(sent[-1]['stream'], {'up': True, 'down_s': 0.0, 'state': 'streaming'})
        client.stream_status = lambda: {'up': 'bad'}
        client.heartbeat()
        self.assertNotIn('stream', sent[-1], 'an invalid report never blocks the heartbeat')


class StreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.recovery = StreamRecovery(clock=lambda: self.now[0])
        self.now[0] += 600  # the session has been running for ten minutes

    def lease(self, down, up=False, age=1.0, started=True):
        return {'client_hostname': HOSTNAME, 'ip': '192.168.1.8', 'started': started,
                'stream': {'up': up, 'down_s': down, 'state': 'searching', 'age_s': age}}

    def rows(self, state):
        return [{'hostname': HOSTNAME, 'connection_state': state}]

    def test_stalled_streaming_state_restarts_after_20_s(self):
        self.assertEqual(self.recovery.check(self.lease(19), self.rows('Streaming')), '')
        reason = self.recovery.check(self.lease(21), self.rows('Streaming'))
        self.assertIn('still showed it Streaming', reason)
        self.assertEqual(self.recovery.restarts, 1)

    def test_disconnected_waits_45_s_and_disconnecting_8_s(self):
        self.assertEqual(self.recovery.check(self.lease(30), self.rows('Disconnected')), '')
        self.assertIn('did not reconnect', self.recovery.check(self.lease(46), self.rows('Disconnected')))
        other = StreamRecovery(clock=lambda: self.now[0])
        self.now[0] += 60
        self.assertEqual(other.check(self.lease(3), self.rows('Disconnecting')), '')
        self.now[0] += 8.5
        self.assertIn('Disconnecting', other.check(self.lease(11), self.rows('Disconnecting')))

    def test_limits_and_preconditions(self):
        self.assertEqual(self.recovery.check(self.lease(60, started=False), self.rows('Disconnected')), '')
        self.assertEqual(self.recovery.check(self.lease(60, age=12), self.rows('Disconnected')), '', 'stale report')
        self.assertEqual(self.recovery.check(self.lease(0), self.rows('Disconnected')), '', 'never up yet')
        self.assertEqual(self.recovery.check(self.lease(60, up=True), self.rows('Disconnected')), '')
        self.assertEqual(self.recovery.check({'started': True}, self.rows('Disconnected')), '', 'old client: no report')
        self.assertTrue(self.recovery.check(self.lease(60), self.rows('Disconnected')))
        self.now[0] += 60
        self.assertEqual(self.recovery.check(self.lease(120), self.rows('Disconnected')), '', 'three minutes apart')
        self.now[0] += 200
        self.assertTrue(self.recovery.check(self.lease(320), self.rows('Disconnected')))
        self.now[0] += 500
        self.assertEqual(self.recovery.check(self.lease(900), self.rows('Disconnected')), '', 'two per session')
        self.recovery.reset()
        self.now[0] += 100
        self.assertTrue(self.recovery.check(self.lease(60), self.rows('Disconnected')))

    def test_downtime_from_before_the_session_never_counts(self):
        fresh = StreamRecovery(clock=lambda: self.now[0])  # Start was just accepted
        self.assertEqual(fresh.check(self.lease(96), self.rows('Disconnected')), '', 'the last session\'s downtime')
        self.now[0] += 30
        self.assertEqual(fresh.check(self.lease(126), self.rows('Disconnected')), '', 'only 30 s of this session')
        self.now[0] += 20
        self.assertIn('did not reconnect', fresh.check(self.lease(146), self.rows('Disconnected')))


class InitHintTests(unittest.TestCase):
    def test_hint_from_headset_and_stale_files_are_removed(self):
        self.assertEqual(init_hint(HEADSET), {'default_view_width': 2016, 'default_view_height': 2240, 'refresh_rate': 90.0})
        self.assertIsNone(init_hint(dict(HEADSET, refresh_hz=500)))
        self.assertIsNone(init_hint(None))
        with tempfile.TemporaryDirectory() as directory:
            nonce = write_init_hint(directory, HEADSET)
            self.assertRegex(nonce, r'^[0-9a-f]{16}$')
            written = json.loads((Path(directory) / INIT_FILE).read_text())
            self.assertEqual(written['nonce'], nonce)
            (Path(directory) / INIT_APPLIED_FILE).write_text(json.dumps({'nonce': nonce, 'applied': True}))
            self.assertEqual(init_outcome(directory, nonce)['applied'], True)
            self.assertIsNone(init_outcome(directory, 'ffff'))
            self.assertIsNone(init_outcome(directory, ''))
            self.assertEqual(write_init_hint(directory, None), '')
            self.assertFalse((Path(directory) / INIT_FILE).exists())
            self.assertFalse((Path(directory) / INIT_APPLIED_FILE).exists())


class HostControlStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lines = []
        self.control = HostControl(self.root, self.lines.append, launch=Mock(), process_ids=lambda: set())

    def record(self, hash_value, applied=True, nonce='ab12'):
        (self.root / INIT_APPLIED_FILE).write_text(json.dumps({
            'nonce': nonce, 'applied': applied, 'changed': True, 'restart_settings_hash': hash_value,
            'eye_resolution': [2016, 2240], 'refresh_rate': 90}))

    def session(self, hash_value):
        (self.root / 'session.json').write_text(json.dumps({'restart_settings_hash': hash_value, 'client_connections': {}}))
        return {'restart_settings_hash': hash_value}

    def test_without_a_hint_any_change_is_a_restart(self):
        self.control.restart_hash = 111
        self.assertFalse(self.control._restart_requested(self.session(111)))
        self.assertTrue(self.control._restart_requested(self.session(222)))

    def test_driver_startup_update_is_adopted_and_later_changes_restart(self):
        self.control.init_nonce, self.control.init_record_active, self.control.restart_hash = 'ab12', True, 111
        self.record(222)
        self.assertFalse(self.control._restart_requested(self.session(222)))
        self.assertEqual(self.control.restart_hash, 222)
        self.assertTrue(any('no restart needed' in line for line in self.lines))
        self.assertFalse(self.control._restart_requested(self.session(222)))
        self.assertTrue(self.control._restart_requested(self.session(333)), 'negotiation changed it')
        # A negotiation that restores the value from before this startup is a restart too.
        self.assertTrue(self.control._restart_requested(self.session(111)))
        self.control._report_startup_config()
        self.assertEqual(self.control.snapshot()['startup_config']['eye_resolution'], [2016, 2240])

    def test_change_before_its_record_waits_then_counts(self):
        self.control.init_nonce, self.control.init_record_active, self.control.restart_hash = 'ab12', True, 111
        now = [50.0]
        with patch('host.control.time.monotonic', side_effect=lambda: now[0]):
            self.assertFalse(self.control._restart_requested(self.session(222)), 'record not written yet')
            now[0] += 1
            self.record(222)
            self.assertFalse(self.control._restart_requested(self.session(222)))
        other = HostControl(self.root, self.lines.append, launch=Mock(), process_ids=lambda: set())
        other.init_nonce, other.init_record_active, other.restart_hash = 'ffff', True, 111
        with patch('host.control.time.monotonic', side_effect=lambda: now[0]):
            self.assertFalse(other._restart_requested(self.session(222)))
            now[0] += HostControl.INIT_RECORD_GRACE + 1
            self.assertTrue(other._restart_requested(self.session(222)), 'no record for this launch: restart')

    def test_a_document_read_before_the_driver_saved_it_is_read_again(self):
        self.control.init_nonce, self.control.init_record_active, self.control.restart_hash = 'ab12', True, 111
        self.record(222)
        self.session(222)  # the driver saved session.json, then wrote the record
        self.assertFalse(self.control._restart_requested({'restart_settings_hash': 111}))
        self.assertEqual(self.control.restart_hash, 222)

    def test_relaunch_ignores_the_first_launch_record(self):
        self.control.init_nonce, self.control.init_record_active, self.control.restart_hash = 'ab12', True, 111
        self.record(222)
        self.control._shutdown_runtime = lambda timeout=None: None
        self.control._launch_runtime = Mock()
        self.session(444)
        self.assertTrue(self.control._relaunch(1))
        self.assertFalse(self.control.init_record_active)
        self.assertEqual(self.control.restart_hash, 444)
        self.assertTrue(self.control._restart_requested(self.session(222)), 'the old record no longer excuses a change')
        self.assertFalse(self.control._restart_requested(self.session(444)))

    def test_invalid_record_keeps_the_baseline_rule(self):
        self.control.init_nonce, self.control.init_record_active, self.control.restart_hash = 'ab12', True, 111
        self.record(None, applied=False)
        self.assertTrue(self.control._restart_requested(self.session(222)))

    def test_negotiated_restart_and_recovery_use_short_shutdown_windows(self):
        calls = []
        self.control._shutdown_runtime = lambda timeout=None: calls.append(timeout)
        self.session(5)
        self.control._launch_runtime = Mock()
        self.assertTrue(self.control._restart_negotiated())
        self.assertTrue(self.control._restart_for_recovery('test reason'))
        self.assertEqual(calls, [HostControl.RESTART_SHUTDOWN_TIMEOUT, HostControl.RECOVERY_SHUTDOWN_TIMEOUT])
        self.assertLess(HostControl.RESTART_SHUTDOWN_TIMEOUT, HostControl.SHUTDOWN_TIMEOUT)
        self.assertEqual(self.control.snapshot()['recovery_restarts'], 1)
        self.assertTrue(any('Stream recovery 1: test reason' in line for line in self.lines))

    def test_session_log_is_kept_across_driver_loads(self):
        (self.root / 'session_log.txt').write_text('first load\n')
        self.control._rotate_runtime_log()
        self.assertEqual((self.root / 'session_log.previous.txt').read_text(), 'first load\n')
        self.assertFalse((self.root / 'session_log.txt').exists())

    def test_client_state_changes_are_journaled(self):
        self.control._journal_client_states([{'hostname': HOSTNAME, 'connection_state': 'Disconnected'}])
        self.control._journal_client_states([{'hostname': HOSTNAME, 'connection_state': 'Streaming'}])
        self.control._journal_client_states([{'hostname': HOSTNAME, 'connection_state': 'Streaming'}])
        self.control._journal_client_states([{'hostname': HOSTNAME, 'connection_state': 'Disconnecting'}])
        self.assertEqual(self.lines, ['ALVR client ' + HOSTNAME + ': Disconnected -> Streaming',
                                      'ALVR client ' + HOSTNAME + ': Streaming -> Disconnecting'])

    def test_recovery_hook_errors_are_contained(self):
        self.control.recovery = Mock(side_effect=RuntimeError('boom'))
        self.assertEqual(self.control._recovery_reason([]), '')
        self.assertIn('Stream recovery check failed', self.lines[-1])

    def test_running_alvr_gets_the_session_address_first(self):
        self.control._ready = lambda: {HOSTNAME: {'hostname': HOSTNAME, 'manual_ips': ['192.168.1.20', '192.168.1.99']}}
        self.control.api = Mock()
        ips = self.control.set_client_address(HOSTNAME, '192.168.1.8', previous='192.168.1.99')
        self.assertEqual(ips, ['192.168.1.8', '192.168.1.20'])
        self.control.api.client.assert_called_once_with(HOSTNAME, {'SetManualIps': ['192.168.1.8', '192.168.1.20']})
        self.control._ready = lambda: {}
        self.control.api = Mock()
        self.control.set_client_address(HOSTNAME, '192.168.1.8', trust=True)
        self.assertEqual(self.control.api.client.call_args_list[0].args,
                         (HOSTNAME, {'AddIfMissing': {'trusted': True, 'manual_ips': ['192.168.1.8']}}))


class ProfileTests(unittest.TestCase):
    def test_driver_log_and_version(self):
        document = configure(default_document())
        self.assertTrue(document['session_settings']['extra']['logging']['log_to_disk'])
        self.assertFalse(document['session_settings']['extra']['logging']['log_tracking'])
        from launcher.version import HOST_VERSION
        self.assertIn("'VROSTBridge " + HOST_VERSION + " / ALVR'", (ROOT / 'host/profile.py').read_text())

    def test_session_client_address(self):
        document = {'client_connections': {HOSTNAME: {'display_name': 'x', 'current_ip': None, 'trusted': True,
                                                      'manual_ips': ['192.168.1.50', '192.168.1.60'],
                                                      'connection_state': 'Disconnected'}}}
        result = session_client_address(document, HOSTNAME, '192.168.1.8', previous='192.168.1.60')
        self.assertEqual(result['client_connections'][HOSTNAME]['manual_ips'], ['192.168.1.8', '192.168.1.50'])
        self.assertTrue(result['client_connections'][HOSTNAME]['trusted'])
        new = session_client_address({'client_connections': {}}, HOSTNAME, '192.168.1.8', auto_connect=True)
        self.assertTrue(new['client_connections'][HOSTNAME]['trusted'])
        untrusted = session_client_address({'client_connections': {}}, HOSTNAME, '192.168.1.8')
        self.assertFalse(untrusted['client_connections'][HOSTNAME]['trusted'])
        with self.assertRaises(ValueError):
            session_client_address({'client_connections': {}}, HOSTNAME, '8.8.8.8')


class OtherWifiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        net = self.root / 'net'
        for name, wireless in (('wlan0', True), ('wlan1', True), ('eth0', False), ('wlan2', True)):
            (net / name).mkdir(parents=True)
            if wireless:
                (net / name / 'wireless').mkdir()
        self.records = [
            {'interface': 'wlan0', 'address': '192.168.77.8', 'prefix': 24, 'physical': True},
            {'interface': 'wlan1', 'address': '192.168.77.233', 'prefix': 24, 'physical': True},
            {'interface': 'eth0', 'address': '192.168.77.9', 'prefix': 24, 'physical': True},
            {'interface': 'wlan2', 'address': '10.0.0.5', 'prefix': 24, 'physical': True}]
        self.calls, self.lines = [], []
        self.code = 0

    def pause(self):
        def runner(arguments, timeout):
            self.calls.append(arguments[1:])
            return completed(self.code)
        return OtherWifiPause(self.root, runner=runner, log=self.lines.append, sys_class_net=self.root / 'net',
                              nmcli='/usr/bin/nmcli', address_provider=lambda: self.records)

    def test_only_other_wifi_on_the_same_network_is_paused_and_restored(self):
        pause = self.pause()
        self.assertEqual(pause.others('wlan0', '192.168.77.8'), ['wlan1'])
        status = pause.begin('wlan0', '192.168.77.8')
        self.assertEqual((status['state'], status['paused']), ('paused', ['wlan1']))
        self.assertEqual(self.calls, [['-w', '5', 'device', 'disconnect', 'wlan1']])
        self.assertTrue((self.root / 'paused-wifi.json').exists(), 'receipt written for crash recovery')
        pause.end()
        self.assertEqual(self.calls[-1], ['-w', '0', 'device', 'connect', 'wlan1'])
        self.assertFalse((self.root / 'paused-wifi.json').exists())

    def test_warn_only_and_failures(self):
        status = self.pause().begin('wlan0', '192.168.77.8', enabled=False)
        self.assertEqual(status['state'], 'warning')
        self.assertEqual(self.calls, [])
        self.code = 1
        status = self.pause().begin('wlan0', '192.168.77.8')
        self.assertEqual(status['state'], 'warning')
        self.assertIn('Could not pause wlan1', status['message'])

    def test_crash_receipt_is_restored_at_start(self):
        (self.root / 'paused-wifi.json').write_text(json.dumps({'paused': ['wlan1', '../bad']}))
        self.assertEqual(self.pause().recover(), [])
        self.assertEqual(self.calls, [['-w', '0', 'device', 'connect', 'wlan1']])
        self.assertFalse((self.root / 'paused-wifi.json').exists())

    def test_failed_restores_are_kept_and_retried(self):
        (self.root / 'paused-wifi.json').write_text(json.dumps({'paused': ['wlan1']}))
        self.code = 1
        pause = self.pause()
        self.assertEqual(pause.recover(), ['wlan1'])
        self.assertEqual(json.loads((self.root / 'paused-wifi.json').read_text())['paused'], ['wlan1'])
        self.code = 0
        pause.end()
        self.assertEqual(self.calls[-1], ['-w', '0', 'device', 'connect', 'wlan1'])
        self.assertFalse((self.root / 'paused-wifi.json').exists())

    def test_timeouts_count_as_failures_and_starts_do_not_retry(self):
        pause = self.pause()
        def slow(arguments, timeout):
            self.calls.append(arguments[1:])
            raise subprocess.TimeoutExpired(arguments, timeout)
        pause.runner = slow
        status = pause.begin('wlan0', '192.168.77.8')
        self.assertEqual(status['state'], 'warning')
        pause.unrestored = ['wlan9']
        calls = len(self.calls)
        pause.begin('wlan0', '192.168.77.8')
        self.assertNotIn(['-w', '0', 'device', 'connect', 'wlan9'], self.calls[calls:], 'a start never waits for old adapters')
        pause.paused = ['wlan1']
        pause.end()
        self.assertIn('wlan1', pause.unrestored, 'a timed-out reconnect is kept for the next attempt')

    def test_recovery_never_touches_a_running_session(self):
        pause = self.pause()
        pause.begin('wlan0', '192.168.77.8')
        calls = len(self.calls)
        self.assertEqual(pause.recover(), [])
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(json.loads((self.root / 'paused-wifi.json').read_text())['paused'], ['wlan1'])


class AsyncSettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / 'config'
        self.config.mkdir()
        runtime = root / 'SteamVR'
        (runtime / 'bin/linux64').mkdir(parents=True)
        self.launcher = runtime / 'bin/linux64/vrcompositor-launcher'
        self.launcher.write_text('')
        self.registry = root / 'openvrpaths.vrpath'
        self.registry.write_text(json.dumps({'config': [str(self.config)], 'runtime': [str(runtime)]}))
        self.log = root / 'vrcompositor.txt'
        self.running = False
        patches = [patch.object(steamvr_async, 'has_cap_sys_nice', lambda path: {'present': True, 'effective': True,
                                                                                 'error': '', 'supported': True}),
                   patch.object(steamvr_async.os, 'statvfs', lambda path: SimpleNamespace(f_flag=0))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def check(self):
        return AsyncReprojectionCheck(registry=self.registry, setup_log=str(self.config / 'none.log'),
                                      compositor_logs=[self.log], running=lambda: self.running, log=lambda line: None,
                                      state_directory=self.config)

    def settings(self, value=None):
        data = {'steamvr': {}} if value is None else {'steamvr': {'enableLinuxVulkanAsync': value}}
        (self.config / 'steamvr.vrsettings').write_text(json.dumps(data))

    def compositor(self, disabled):
        lines = ['Tue [Info] - Global priority query enabled: 1']
        if disabled:
            lines.append('Tue [Info] - Async support disabled by user setting or a direct mode driver')
        lines.append('Tue [Info] - Vulkan fillModeNonSolid: yes')
        self.log.write_text('\n'.join(lines) + '\n')

    def test_log_parsing_reads_the_last_start(self):
        text = ('Global priority query enabled: 1\nAsync support disabled by user setting or a direct mode driver\n'
                'Global priority query enabled: 1\nVulkan fillModeNonSolid: yes\n')
        self.assertFalse(last_start_async_disabled(text))
        self.assertTrue(last_start_async_disabled(text + 'Global priority query enabled: 1\n'
                                                  'Async support disabled by user setting or a direct mode driver\n'))
        self.assertIsNone(last_start_async_disabled('nothing'))

    def test_setting_off_is_reported_and_repaired_while_steamvr_is_closed(self):
        self.settings(False)
        self.compositor(True)
        os.utime(self.config / 'steamvr.vrsettings', (1, 1))
        result = self.check().check()
        self.assertEqual((result['state'], result['can_repair'], result['fix']), ('disabled', True, 'setting'))
        self.running = True
        refused = self.check().repair()
        self.assertFalse(refused['repaired'])
        self.assertIn('Close SteamVR', refused['message'])
        self.running = False
        os.utime(self.log, (1, 1))  # SteamVR last wrote its log before the repair
        repaired = self.check().repair()
        self.assertTrue(repaired['repaired'])
        data = json.loads((self.config / 'steamvr.vrsettings').read_text())
        self.assertIs(data['steamvr']['enableLinuxVulkanAsync'], True)
        self.assertEqual(self.check().check()['state'], 'ready', 'the older log no longer counts')

    def test_log_without_setting_offers_repair_and_driver_case_has_none(self):
        self.settings(None)
        os.utime(self.config / 'steamvr.vrsettings', (1, 1))
        self.compositor(True)
        self.assertEqual(self.check().check()['state'], 'disabled')
        self.settings(True)
        os.utime(self.config / 'steamvr.vrsettings', (1, 1))
        result = self.check().check()
        self.assertEqual((result['state'], result['can_repair']), ('disabled_driver', False))
        self.compositor(False)
        self.assertEqual(self.check().check()['state'], 'ready')


class MicrophoneFallbackTests(unittest.TestCase):
    def document(self):
        return default_document()

    def test_blocked_helper_falls_back_to_the_device_list(self):
        blocked = Mock(side_effect=PermissionError(13, 'Access is denied'))
        devices = lambda: {'render': ['CABLE Input (VB-Audio Virtual Cable)'], 'capture': ['CABLE Output (VB-Audio Virtual Cable)']}
        status = audio.microphone_status(True, self.document(), Path('probe'), blocked, devices)
        self.assertTrue(status['microphone_enabled'])
        self.assertIn('Access is denied', status['detail'])
        none = audio.microphone_status(True, self.document(), Path('probe'), blocked, lambda: {'render': [], 'capture': []})
        self.assertFalse(none['microphone_enabled'])
        self.assertIn('no virtual microphone yet', none['warning'])  # RC4.3: points to the host page's setup
        self.assertIn('Set up headset microphone', none['warning'])
        failed = audio.microphone_status(True, self.document(), Path('probe'), blocked, Mock(side_effect=OSError('no shell')))
        self.assertIn('could not list its audio devices', failed['warning'])
        self.assertIn('Windows blocked the audio check helper', failed['warning'])

    def test_pnp_rows(self):
        rows = [{'id': 'SWD\\MMDEVAPI\\{0.0.0.00000000}.{a}', 'name': 'Speakers'},
                {'id': 'SWD\\MMDEVAPI\\{0.0.1.00000000}.{b}', 'name': 'Microphone'},
                {'id': 'OTHER', 'name': 'Ignored'}, 'junk']
        self.assertEqual(audio.endpoints_from_pnp(rows), {'render': ['Speakers'], 'capture': ['Microphone']})
        self.assertEqual(audio.endpoints_from_pnp(rows[0]), {'render': ['Speakers'], 'capture': []})
        with patch.object(audio.os, 'name', 'nt'):
            runner = Mock(return_value=completed(0, json.dumps(rows[:2])))
            self.assertEqual(audio.pnp_endpoints(runner), {'render': ['Speakers'], 'capture': ['Microphone']})
            self.assertNotIn('"', audio.PNP_SCRIPT)


class WakeTests(unittest.TestCase):
    def report(self, **row):
        adapter = {'Name': 'Ethernet 2', 'InterfaceDescription': 'Realtek USB GbE Family Controller',
                   'MacAddress': 'AA-BB-CC-DD-EE-02', 'LinkSpeed': '1 Gbps', 'MediaType': '802.3',
                   'PhysicalMediaType': '802.3', 'WakeOnMagicPacket': None, 'DeviceSleepOnDisconnect': None}
        adapter.update(row)
        return power.parse_wake_readiness(json.dumps({'adapters': [adapter], 'sleep_states': '', 'hiberboot': 0}))

    def test_advanced_property_and_wake_armed(self):
        report = self.report(WakeOnMagicPacketAdvanced='1', WakeArmed=True)
        self.assertTrue(report['ready'])
        self.assertTrue(report['adapters'][0]['wake_on_magic_packet'])
        report = self.report(WakeOnMagicPacketAdvanced='1', WakeArmed=False)
        self.assertFalse(report['ready'])
        self.assertIn('Allow this device to wake the computer', report['message'])
        report = self.report()
        self.assertIn('Realtek USB GbE Family Controller', report['message'])
        self.assertIn('Advanced tab', report['message'])
        self.assertNotIn('"', power.WAKE_READINESS_SCRIPT)

    def test_message_follows_the_session_adapter(self):
        rows = [dict(name='Ethernet', description='Intel', kind='wired', wake_on_magic_packet=True, wake_armed=True),
                dict(name='Ethernet 2', description='Realtek USB', kind='wired', wake_on_magic_packet=None, wake_armed=None)]
        self.assertIn('ready', power.wake_message(rows, False))
        self.assertIn('Ethernet 2 (Realtek USB) does not report', power.wake_message(rows, False, preferred='Ethernet 2'))


class CompositorRecoveryTests(unittest.TestCase):
    def test_watchdog_abort_is_detected_once_and_only_the_compositor_is_stopped(self):
        self.assertTrue(watchdog_aborted('[Info] - Failed Watchdog timeout in thread Render in RenderDistort after 5.9 seconds. Aborting.'))
        self.assertFalse(watchdog_aborted('Startup Complete'))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'vrcompositor.txt'
            log.write_text('old run: Failed Watchdog timeout in thread Render. Aborting.\n')
            runtime = ClientRuntime(Path(directory) / 'client', directory, lambda line: None, compositor_logs=[log])
            runtime.mark_compositor_log()
            self.assertFalse(runtime.compositor_aborted(), 'only lines after the mark count')
            with log.open('a') as handle:
                handle.write('Failed Watchdog timeout in thread Render in RenderDistort after 5.9 seconds. Aborting.\n')
            self.assertTrue(runtime.compositor_aborted())
            self.assertFalse(runtime.compositor_aborted(), 'reported once')
            # A replaced log (new file) is read from its start.
            log.unlink()
            log.write_text('Failed Watchdog timeout in thread Render in RenderDistort after 6.0 seconds. Aborting.\n')
            self.assertTrue(runtime.compositor_aborted())
            # No log at the mark: a log that appears later is read from its start.
            fresh = ClientRuntime(Path(directory) / 'client', directory, lambda line: None,
                                  compositor_logs=[Path(directory) / 'later.txt'])
            fresh.mark_compositor_log()
            (Path(directory) / 'later.txt').write_text('Failed Watchdog timeout in thread Render. Aborting.\n')
            self.assertTrue(fresh.compositor_aborted())
            compositor = {'pid': 11, 'path': '/x/bin/linux64/vrcompositor'}
            server = {'pid': 12, 'path': '/x/bin/linux64/vrserver'}
            runtime.identities = {11: compositor, 12: server}
            with patch('launcher.client_runtime.process_identity', side_effect=lambda pid: runtime.identities.get(pid)), \
                    patch('launcher.client_runtime.signal_identity', return_value=True) as kill:
                self.assertEqual(runtime.recover_compositor(), 1)
            kill.assert_called_once_with(compositor, signal.SIGKILL)
            self.assertNotIn(11, runtime.identities)
            self.assertIn(12, runtime.identities)


class LateFrameTests(unittest.TestCase):
    def test_budget_is_wider_but_keeps_the_submission_margin(self):
        text = (ROOT / 'core/presentation.hpp').read_text()
        self.assertIn('std::min({.003, .25 / double(refresh)', text)
        self.assertIn('double(remainingSeconds) - .002', text)


class AppTests(unittest.TestCase):
    """The RC4.1 helpers on the companion, with every system helper replaced."""

    def setUp(self):
        from test_session_lifecycle import SessionLifecycleTests
        self.case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_client_reports_downtime_only_after_the_first_frame(self):
        app = self.case.app()
        app.running = lambda: True
        app.client_identity.connection_state = 'searching'
        self.assertEqual(app.stream_report(), {'up': False, 'down_s': 0.0, 'state': 'searching'})
        app.metrics = {'state': 'streaming'}
        app.last_metrics = time.monotonic()
        app.track_stream()
        self.assertEqual(app.stream_report()['up'], True)
        app.metrics = {'state': 'recovery'}
        app.track_stream()
        with patch('launcher.app.time.monotonic', return_value=time.monotonic() + 12):
            report = app.stream_report()
        self.assertFalse(report['up'])
        self.assertGreaterEqual(report['down_s'], 11)

    def test_host_preferences_carry_prediction_and_hostname(self):
        app = self.case.app()
        app.client_identity.status.return_value = {'hostname': HOSTNAME}
        app.path_test = lambda *a: None
        app.stream_interface = lambda: ''
        with patch('launcher.app.link_info', return_value={'kind': 'wired'}):
            prefs = app.host_preferences({'codec': 'Hevc', 'bitrate': 80, 'headset': HEADSET})
        self.assertEqual((prefs['host_prediction'], prefs['client_hostname']), (True, HOSTNAME))
        app.settings['motion_prediction'] = False
        app.client_identity.status.return_value = {'hostname': 'not-a-client.local.'}
        with patch('launcher.app.link_info', return_value={'kind': 'wired'}):
            prefs = app.host_preferences({'codec': 'Hevc', 'bitrate': 80, 'headset': HEADSET})
        self.assertFalse(prefs['host_prediction'])
        self.assertNotIn('client_hostname', prefs)

    def test_foveation_status_tells_gaze_from_centred(self):
        app = self.case.app()
        app.running = lambda: True
        app.timings = {'foveated_frames': '75', 'gaze_centred_frames': '75'}
        link = {'host': {'pipeline': {'stream': {'gaze_foveation': 0, 'optical_centre': 1}}}}
        live = app.foveation_live(link, 'balanced', (1408, 1408), {'stream_width': 2016, 'stream_height': 2240})
        self.assertEqual(live['mode'], 'centred')
        self.assertNotIn('eyes', live['short'])
        self.assertEqual(live['saving_percent'], 56)
        link['host']['pipeline']['stream'] = {'gaze_foveation': 1, 'gaze_gaze_frames': 90, 'gaze_fallback_frames': 10}
        live = app.foveation_live(link, 'eye-tracked', None, {})
        self.assertEqual((live['mode'], live['gaze_share']), ('eyes', 0.9))
        link['host']['pipeline']['stream'] = {'gaze_foveation': 1, 'gaze_gaze_frames': 90, 'gaze_fallback_frames': 110}
        self.assertEqual(app.foveation_live(link, 'eye-tracked', None, {})['mode'], 'eyes-waiting', 'recent frames fall back')
        app.timings = {'foveated_frames': '0'}
        self.assertFalse(app.foveation_live(link, 'off', None, {})['active'])

    def test_host_follows_the_lease_address_and_checks_recovery(self):
        app = self.case.app('host')
        app.host_control.set_client_address = Mock(return_value=['192.168.1.8'])
        app.session_link.lease_details.return_value = {'client_hostname': HOSTNAME, 'ip': '192.168.1.8', 'started': True,
                                                       'stream': None}
        self.assertEqual(app.stream_recovery_tick([]), '')
        app.host_control.set_client_address.assert_called_once_with(HOSTNAME, '192.168.1.8', None, trust=True)
        self.assertEqual(app.settings['session_client_addresses'], {HOSTNAME: '192.168.1.8'})
        app.stream_recovery_tick([])
        app.host_control.set_client_address.assert_called_once()
        app.session_link.lease_details.return_value = {'client_hostname': HOSTNAME, 'ip': '192.168.1.9', 'started': True,
                                                       'stream': None}
        app.session_address_attempt = 0
        app.stream_recovery_tick([])
        self.assertEqual(app.host_control.set_client_address.call_args.args, (HOSTNAME, '192.168.1.9', '192.168.1.8'))

    def test_address_waits_for_approval_when_automatic_connection_is_off(self):
        app = self.case.app('host')
        app.settings['auto_connect'] = False
        app.host_control.set_client_address = Mock(return_value=['192.168.1.8'])
        app.session_link.lease_details.return_value = {'client_hostname': HOSTNAME, 'ip': '192.168.1.8', 'started': True,
                                                       'stream': None}
        rows = [{'hostname': HOSTNAME, 'trusted': False, 'manual_ips': []}]
        app.stream_recovery_tick(rows)
        app.host_control.set_client_address.assert_not_called()
        rows[0]['trusted'] = True
        app.stream_recovery_tick(rows)
        app.host_control.set_client_address.assert_called_once_with(HOSTNAME, '192.168.1.8', None, trust=False)
        rows[0]['manual_ips'] = ['192.168.1.8']
        app.session_address_attempt = 0
        app.stream_recovery_tick(rows)
        app.host_control.set_client_address.assert_called_once()
        document = {'client_connections': {}}
        self.assertIs(app.session_client(document, {'client_hostname': HOSTNAME, 'ip': '192.168.1.8'}), document)

    def test_fresh_host_start_adds_address_hint_and_nonce(self):
        app = self.case.app('host')
        app.host = self.case.root / 'host'
        app.host.mkdir()
        (app.host / 'ALVR Dashboard.exe').touch()
        app.host_prepared = True
        app.ensure_host_stopped = Mock()
        app.setup_host = Mock()
        app.session_features = lambda: ['init-config', 'prediction-switch']
        app.session_link.lease_details.return_value = {'client_hostname': HOSTNAME, 'ip': '192.168.1.8', 'started': False}
        with patch('launcher.app.require_private_home_network'), \
                patch('launcher.app.host_loader_check', return_value={'ok': True}):
            app.start_host_session({'codec': 'Hevc', 'bitrate': 80, 'headset': HEADSET, 'host_prediction': False})
        session = json.loads((app.host / 'session.json').read_text())
        self.assertEqual(session['client_connections'][HOSTNAME]['manual_ips'], ['192.168.1.8'])
        hint = json.loads((app.host / INIT_FILE).read_text())
        self.assertEqual((hint['default_view_width'], hint['default_view_height'], hint['refresh_rate']), (2016, 2240, 90.0))
        app.host_control.start.assert_called_once_with(init_nonce=hint['nonce'])
        options = json.loads((app.host / 'vrostbridge-stream.json').read_text())
        self.assertFalse(options['host_prediction'])


if __name__ == '__main__':
    unittest.main()
