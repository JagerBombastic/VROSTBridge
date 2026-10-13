"""VROSTBridge 1.0.0.8: steadier streams.

The 1.0.0.7 test streamed about 195 Mbit/s after the 1.0.0.6 network test passed a wired path up to 500 Mbit/s:
decoding took twice as long, frames arrived late and bunched, the host's frame timing lock stopped locking and a
frame was lost every 12 s. 1.0.0.8 asks only for what the picture needs (launcher/stream_budget.py), follows the
stream second by second and changes the host's bitrate while it runs ('bitrate-control'), and paces video below
the rate the network test carried without loss. On the Bazzite client the GPU clock and power limit are balanced
during sessions only (steamos-manager); both clients turn SteamVR 2.0 base stations on as a session starts and off
when VROSTBridge closes; the eye tracker's straight ahead is kept centred per calibration, and an earlier
calibration can be chosen again.
"""
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher import stream_budget  # noqa: E402
from launcher.stream_budget import BitrateGovernor, floor_mbps, quality_ceiling_mbps  # noqa: E402
from launcher import link_test  # noqa: E402
from launcher.network_test import pacing_mbps  # noqa: E402
from launcher.session_tuning import SessionTuning, on_charger  # noqa: E402
from launcher.power_balance import GPU_INTERFACE, TDP_INTERFACE  # noqa: E402
from launcher import base_stations  # noqa: E402
from launcher.base_stations import BaseStations, POWER_UUID  # noqa: E402
from launcher.gaze_provider import GazeCentre, GazeRelay, gaze_angles, gaze_quaternion  # noqa: E402
from launcher.session_link import SessionClient, SessionError, SessionHost  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from host.control import HostControl  # noqa: E402
from test_session_lifecycle import SessionLifecycleTests  # noqa: E402
from test_session_link import eventually  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def done(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def timing(cadence='full', decode=4.5, decode_p95=None, arrival=13.3, lost=0, timeouts=0, skipped=0, fps=75):
    """One TIMING line's fields as the companion parses them (strings)."""
    return {'cadence': cadence, 'packet_fps': str(fps), 'decode_sync_complete_mean_ms': str(decode),
            'decode_sync_complete_p95_ms': str(decode if decode_p95 is None else decode_p95),
            'packet_interval_p95_ms': str(arrival), 'net_lost_frames_total': str(lost),
            'late_frame_timeouts': str(timeouts), 'fused_skipped': str(skipped)}


# ------------------------------------------------------------------------------------------------ version
class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_8_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1009.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 8))
        version = read('core/version.hpp')
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "' + CLIENT_VERSION + '"', version)
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "' + HOST_VERSION + '"', version)
        nsi = read('packaging/windows-installer.nsi')
        self.assertIn('Name "VROSTBridge Host ' + HOST_VERSION + '"', nsi)
        self.assertNotIn('1.0.0.7', nsi)
        import build_windows_client
        self.assertEqual(build_windows_client.client_version(), CLIENT_VERSION)


# ------------------------------------------------------------------------------------------------ the picture's bitrate
class QualityCeilingTests(unittest.TestCase):
    def test_the_ceiling_follows_codec_size_and_rate(self):
        # 1.0.0.5's clean session: 2432 x 1216 HEVC at 75 Hz streamed at about 50 Mbit/s.
        self.assertEqual(quality_ceiling_mbps('Hevc', 2432, 1216, 75), 49)
        self.assertEqual(quality_ceiling_mbps('Hevc', 4032, 2016, 75), 134)
        self.assertGreater(quality_ceiling_mbps('H264', 2432, 1216, 75), quality_ceiling_mbps('Hevc', 2432, 1216, 75))
        self.assertLess(quality_ceiling_mbps('AV1', 2432, 1216, 75), quality_ceiling_mbps('Hevc', 2432, 1216, 75))
        self.assertEqual(quality_ceiling_mbps('Hevc', 640, 480, 60), stream_budget.MIN_MBPS)
        self.assertEqual(quality_ceiling_mbps('H264', 8192, 4096, 144), stream_budget.MAX_MBPS)
        for bad in ((0, 1216, 75), (2432, None, 75), (2432, 1216, 'x'), (2432, 1216, math.nan)):
            self.assertIsNone(quality_ceiling_mbps('Hevc', *bad), bad)
        self.assertEqual(quality_ceiling_mbps('unknown', 2432, 1216, 75), quality_ceiling_mbps('H264', 2432, 1216, 75))

    def test_the_floor_is_a_share_of_the_ceiling(self):
        self.assertEqual(floor_mbps(134), 47)
        self.assertEqual(floor_mbps(49), stream_budget.MIN_MBPS)
        self.assertEqual(floor_mbps(15), 15, 'never above the ceiling itself')


class GovernorTests(unittest.TestCase):
    def governor(self, start=50, ceiling=50, hz=75):
        governor = BitrateGovernor()
        governor.reset(start, ceiling, hz)
        return governor

    def run_seconds(self, governor, count, **fields):
        changes = []
        for _ in range(count):
            value = governor.observe(timing(**fields))
            if value is not None:
                changes.append(value)
        return changes

    def test_inactive_without_a_starting_bitrate(self):
        governor = self.governor(None, 50)
        self.assertFalse(governor.active)
        self.assertIsNone(governor.observe(timing(decode=12)))
        self.assertIsNone(governor.observe('not a line'))

    def test_the_first_seconds_are_not_judged(self):
        governor = self.governor()
        self.assertEqual(self.run_seconds(governor, stream_budget.WARMUP_S, decode=12), [])
        self.assertEqual(len(governor.window), 0)

    def test_slow_decoding_steps_down_and_holds(self):
        governor = self.governor()
        changes = self.run_seconds(governor, stream_budget.WARMUP_S + stream_budget.TROUBLE_S, decode=7.8)
        self.assertEqual(changes, [40])
        self.assertEqual(governor.reason, 'decoding behind')
        self.assertEqual(governor.steps_down, 1)
        self.assertEqual(governor.hold, stream_budget.HOLD_DOWN_S)
        self.assertIsNotNone(governor.check, 'a step for decoding alone is checked')

    def test_a_step_that_made_decoding_faster_stays(self):
        governor = self.governor()
        self.run_seconds(governor, stream_budget.WARMUP_S + stream_budget.TROUBLE_S, decode=7.8)
        self.assertEqual(self.run_seconds(governor, stream_budget.CHECK_SETTLE_S + stream_budget.CHECK_S, decode=5.0), [])
        self.assertIsNone(governor.check)
        self.assertFalse(governor.decode_bound)
        self.assertEqual(governor.mbps, 40)

    def test_a_step_that_did_not_help_decoding_goes_back_and_decoding_no_longer_counts(self):
        governor = self.governor()
        self.run_seconds(governor, stream_budget.WARMUP_S + stream_budget.TROUBLE_S, decode=7.8)
        changes = self.run_seconds(governor, stream_budget.CHECK_SETTLE_S + stream_budget.CHECK_S, decode=7.7)
        self.assertEqual(changes, [50], 'back to where it was')
        self.assertTrue(governor.decode_bound)
        self.assertIn('decoding was no faster at a lower bitrate', governor.reason)
        self.assertEqual(self.run_seconds(governor, 60, decode=7.7), [], 'slow decoding alone no longer steps down')
        self.assertTrue(governor.snapshot()['decode_bound'])
        # Lost frames still do.
        governor.hold = 0
        changes = []
        for second in range(1, 4):
            value = governor.observe(timing(decode=7.7, lost=second))
            if value is not None:
                changes.append(value)
        self.assertEqual(changes, [40])
        self.assertEqual(governor.reason, 'frames lost')

    def test_lost_frames_step_down_without_a_decoding_check(self):
        governor = self.governor(100, 134)
        self.run_seconds(governor, stream_budget.WARMUP_S)
        changes = []
        for second in range(1, 4):  # the native client's counter is cumulative
            value = governor.observe(timing(lost=second))
            if value is not None:
                changes.append(value)
        self.assertEqual(changes, [80])
        self.assertIsNone(governor.check)
        self.assertEqual(governor.history[-1], {'from': 100, 'to': 80, 'reason': 'frames lost'})

    def test_late_bunched_frames_step_down_but_late_even_ones_do_not(self):
        governor = self.governor()
        self.run_seconds(governor, stream_budget.WARMUP_S)
        self.assertEqual(self.run_seconds(governor, 10, cadence='late_or_paired', arrival=14.0), [], 'evenly spaced')
        self.assertEqual(self.run_seconds(governor, 5, cadence='late_or_paired', arrival=25.0), [40])
        self.assertEqual(governor.reason, 'frames arriving unevenly')

    def test_clean_running_steps_back_up_to_the_ceiling_and_never_past_it(self):
        governor = self.governor(40, 50)
        self.run_seconds(governor, stream_budget.WARMUP_S)
        self.assertEqual(self.run_seconds(governor, stream_budget.CLEAN_UP_S), [45])
        self.assertEqual(self.run_seconds(governor, stream_budget.HOLD_UP_S + stream_budget.CLEAN_UP_S), [50])
        self.assertEqual(self.run_seconds(governor, 200), [], 'the ceiling holds')

    def test_each_step_down_makes_the_next_step_up_wait_longer(self):
        governor = self.governor(100, 100)
        self.run_seconds(governor, stream_budget.WARMUP_S)
        waits = []
        for _ in range(3):
            governor.hold = 0
            lost = governor.previous['net_lost_frames_total'] if governor.previous else 0
            for step in (1, 2):
                governor.observe(timing(lost=lost + step))
            waits.append(governor.up_after)
        self.assertEqual(waits, [40, 80, 160])
        governor.steps_down = 9
        governor._change(governor.mbps - 5, 'down', 'frames lost')
        self.assertEqual(governor.up_after, stream_budget.CLEAN_UP_MAX_S)

    def test_never_below_the_floor(self):
        governor = self.governor(25, 49)  # floor: MIN_MBPS (20)
        self.run_seconds(governor, stream_budget.WARMUP_S)
        lost = 0
        for _ in range(40):
            governor.hold = 0
            governor.check = None
            lost += 1
            governor.observe(timing(lost=lost))
        self.assertEqual(governor.mbps, stream_budget.MIN_MBPS)

    def test_a_pause_in_the_stream_restarts_the_counters(self):
        governor = self.governor()
        self.run_seconds(governor, stream_budget.WARMUP_S)
        governor.observe(timing(lost=40))
        governor.observe(timing(cadence='waiting'))
        self.assertIsNone(governor.previous)
        self.assertIsNone(governor.observe(timing(lost=3)), 'a new stream counts from its own start')
        self.assertEqual(sum(item['lost'] for item in governor.window), 0)


# ------------------------------------------------------------------------------------------------ pacing
class PacingHeadroomTests(unittest.TestCase):
    def test_video_is_paced_below_the_loss_free_rate(self):
        self.assertEqual(link_test.PACING_HEADROOM, 0.7)
        self.assertEqual(link_test.paced_below(500), 350)
        wired = {'kind': 'wired', 'speed_mbps': 1000}
        self.assertEqual(pacing_mbps(wired, {'lossless_mbps': 500}), 350)
        self.assertEqual(pacing_mbps(wired), 920, 'no test: the link fraction as before')
        self.assertEqual(pacing_mbps(wired, {'lossless_mbps': 500}, measuring=True), 920, 'the test itself is not held')
        # 1.0.0.7's session: paced at 500 Mbit/s on the path the test passed at 500; now 350.
        self.assertIn('rate = min(rate, paced_below(lossless))', read('launcher/network_test.py'))


# ------------------------------------------------------------------------------------------------ the host changes the bitrate
class HostBitrateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = []
        self.applied = []
        self.fail = False

        def apply(mbps):
            if self.fail:
                raise ValueError('The host stream is not running.')
            self.applied.append(mbps)
        self.host = SessionHost(self.root / 'host', Mock(), Mock(), lambda: {'runtime_state': 'ready', 'available': True},
                                Mock(return_value=True), self.logs.append, port=0, address_provider=lambda: [],
                                test_peer=lambda peer: peer == '127.0.0.1', bitrate=apply)
        self.addCleanup(self.host.close)
        self.owner = {'client_id': 'a' * 32, 'token': 'b' * 64}
        self.host.dispatch('/pair', self.owner, '127.0.0.1')

    def start(self, bitrate=50):
        result = self.host.dispatch('/start', dict(self.owner, request_id='c' * 32,
                                                   settings={'codec': 'Hevc', 'bitrate': bitrate}), '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        return result['lease_id']

    def heartbeat(self, lease, bitrate):
        return self.host.dispatch('/heartbeat', dict(self.owner, lease_id=lease, bitrate=bitrate), '127.0.0.1')

    def test_the_host_offers_bitrate_control_only_with_a_way_to_apply_it(self):
        self.assertIn('bitrate-control', self.host.host_features())
        plain = SessionHost(self.root / 'plain', Mock(), Mock(), lambda: {}, Mock(return_value=True), self.logs.append,
                            port=0, address_provider=lambda: [])
        self.addCleanup(plain.close)
        self.assertNotIn('bitrate-control', plain.host_features())
        lease = plain.dispatch('/start', dict(self.owner, request_id='d' * 32), '127.0.0.1') if False else None
        self.assertIsNone(lease)

    def test_a_heartbeat_sets_the_running_streams_bitrate_once(self):
        lease = self.start(50)
        self.heartbeat(lease, 50)
        time.sleep(0.3)
        self.assertEqual(self.applied, [], 'the bitrate it started with is not set again')
        self.heartbeat(lease, 40)
        eventually(lambda: self.applied == [40])
        self.heartbeat(lease, 40)
        time.sleep(0.3)
        self.assertEqual(self.applied, [40])
        for bad in (9, 301, 40.0, '40', True):
            with self.assertRaises(SessionError):
                self.heartbeat(lease, bad)

    def test_a_failed_change_is_asked_again_a_few_times(self):
        lease = self.start(50)
        self.fail = True
        with patch('launcher.session_link.BITRATE_SPACING_S', 0.0):
            for _ in range(5):
                self.heartbeat(lease, 40)
                time.sleep(0.15)
        failures = [line for line in self.logs if line.startswith('Stream bitrate change to 40 Mbit/s failed')]
        self.assertEqual(len(failures), 3)
        self.fail = False

    def test_the_client_sends_the_governors_bitrate_only_to_hosts_that_take_it(self):
        client = SessionClient(self.root / 'client', self.logs.append, port=self.host.port, address_provider=lambda: [],
                               test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(client.close)
        client.bitrate_request = lambda: 35
        client.start({'codec': 'Hevc', 'bitrate': 50}, host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.assertIn('bitrate-control', client.host_features)
        client.heartbeat()
        eventually(lambda: self.applied == [35])
        client.bitrate_request = lambda: 'fast'
        client.heartbeat()  # nothing invalid is sent
        client.host_features = [item for item in client.host_features if item != 'bitrate-control']
        client.bitrate_request = lambda: 30
        client.heartbeat()
        time.sleep(0.3)
        self.assertEqual(self.applied, [35])
        client.stop()

    def test_host_control_sets_alvrs_bitrate_while_the_stream_runs(self):
        logs = []
        control = HostControl(self.root / 'alvr', logs.append, launch=Mock(), process_ids=lambda: set())
        control.api = Mock()
        with self.assertRaisesRegex(ValueError, 'not running'):
            control.set_bitrate(40)
        control.active = True
        control._set(runtime_state='ready')
        self.assertEqual(control.set_bitrate(40), 40)
        control.api.request.assert_called_once_with('session/values', [{'path': [
            {'Name': 'session_settings'}, {'Name': 'video'}, {'Name': 'bitrate'}, {'Name': 'mode'},
            {'Name': 'ConstantMbps'}], 'value': 40}], post=True)
        self.assertIn('VROST_BITRATE set=40 Mbit/s (the headset client asked)', logs)
        for bad in (9, 301, 40.5, True):
            with self.assertRaises(ValueError):
                control.set_bitrate(bad)


# ------------------------------------------------------------------------------------------------ the companion
class CompanionTests(SessionLifecycleTests):
    def test_auto_bitrate_stops_at_the_pictures_ceiling_and_follows_the_stream_on_capable_hosts(self):
        from headset_fixture import HEADSET
        from launcher.network_test import remember
        app = self.app()
        app.session_link.host_id = 'h'
        app.session_link.network_status.return_value = {'interface': '', 'source_interface': 'eth9'}
        app.settings['network_tests'] = remember({}, {'time': time.time(), 'host_id': 'h', 'interface': 'eth9',
                                                      'recommendation': {'recommended_mbps': 250, 'clean_mbps': 300}})
        for features, active in ((['pacing', 'link-test'], False), (['pacing', 'link-test', 'bitrate-control'], True)):
            app.session_link.host_features = features
            with patch('launcher.app.link_info', return_value={'kind': 'wired', 'speed_mbps': 2500}):
                prefs = app.host_preferences({'codec': 'Hevc', 'bitrate': 80, 'headset': HEADSET,
                                              'resolution': 'compatibility'})
            ceiling = app.stream_ceiling(prefs)
            self.assertTrue(ceiling and ceiling < 250, ceiling)
            self.assertEqual(prefs['bitrate'], ceiling, 'the measured 250 Mbit/s held to what the picture needs')
            self.assertEqual(app.bitrate_governor.active, active, features)
        self.assertEqual(app.bitrate_governor.ceiling, ceiling)
        # Manual bitrate: the governor stays out of it.
        app.settings['auto_bitrate'] = False
        with patch('launcher.app.link_info', return_value={'kind': 'wired', 'speed_mbps': 2500}):
            prefs = app.host_preferences({'codec': 'Hevc', 'bitrate': 80, 'headset': HEADSET})
        self.assertEqual(prefs['bitrate'], 80)
        self.assertFalse(app.bitrate_governor.active)

    def test_timing_lines_drive_the_governor_and_its_note_is_logged_outside_the_lock(self):
        app = self.app()
        app.bitrate_governor.reset(50, 50, 75)
        line = 'TIMING ' + ' '.join(key + '=' + value for key, value in timing(decode=7.8).items())
        for _ in range(stream_budget.WARMUP_S + stream_budget.TROUBLE_S):
            app.log(line)
        self.assertEqual(app.bitrate_governor.mbps, 40)
        self.assertEqual(app.bitrate_note, 'BITRATE 50 -> 40 Mbit/s (decoding behind)')
        with patch.object(app, 'running', return_value=True):
            self.assertEqual(app.governed_bitrate(), 40)
        self.assertIsNone(app.governed_bitrate(), 'only while the session runs')
        self.assertIn("note=getattr(self,'bitrate_note',None)", read('launcher/app.py'))

    def test_live_switches_save_at_once_and_balance_off_sets_the_pc_back(self):
        app = self.app()
        app.action({'action': 'client-option', 'name': 'balance_performance', 'enabled': False})
        self.assertFalse(app.settings['balance_performance'])
        app.session_tuning.end.assert_called()
        saved = json.loads(app.settings_path.read_text()) if hasattr(app, 'settings_path') else None
        if saved is not None:
            self.assertFalse(saved['balance_performance'])
        for bad in ({'name': 'codec', 'enabled': True}, {'name': 'gaze_centre', 'enabled': 'yes'}):
            with self.assertRaises(ValueError):
                app.action(dict(bad, action='client-option'))
        app.action({'action': 'base-stations', 'power': 'on'})
        app.base_stations.set_power_background.assert_called_with('on', search=True)  # the buttons search afresh
        with self.assertRaises(ValueError):
            app.action({'action': 'base-stations', 'power': 'standby'})

    def test_recommended_settings_and_not_during_a_session(self):
        app = self.app()
        app.settings.update(codec='H264', resolution='sharp', auto_bitrate=False, timing_lock=False, gaze_centre=False)
        with patch('launcher.app.link_info', return_value={'kind': 'wifi'}):
            app.action({'action': 'recommended-settings'})
        for key, value in app.RECOMMENDED.items():
            self.assertEqual(app.settings[key], value, key)
        self.assertTrue(app.settings['intra_refresh'], 'gradual loss recovery on Wi-Fi')
        with patch('launcher.app.link_info', return_value={'kind': 'wired'}):
            app.action({'action': 'recommended-settings'})
        self.assertFalse(app.settings['intra_refresh'])
        with patch.object(app, 'running', return_value=True), self.assertRaisesRegex(ValueError, 'Stop the session'):
            app.action({'action': 'recommended-settings'})

    def test_performance_balance_begins_once_the_video_is_up(self):
        app = self.app()
        tuning = app.session_tuning
        tuning.active = True
        with patch.object(app, 'running', return_value=True):
            app.balance_performance({'active': True})
            tuning.begin.assert_not_called()
            app.stream_ever_up = True
            app.balance_performance({'active': True})
            eventually(lambda: tuning.begin.called)
            app.balance_performance({'active': True, 'observed_monotonic': 2})
            tuning.observe.assert_called()
            tuning.reset_mock()
            app.settings['balance_performance'] = False
            app.balance_performance({'active': True, 'observed_monotonic': 3})
            tuning.observe.assert_not_called()
        app.end_client_session_helpers()
        tuning.end.assert_called()
        self.assertFalse(app.tuning_started)

    def test_base_stations_wake_as_a_session_starts_and_sleep_when_vrostbridge_closes(self):
        app = self.app()
        app.prepare_client_system(app.client_generation)
        app.base_stations.set_power_background.assert_called_with('on')
        app.base_stations.reset_mock()
        app.settings['base_stations_off'] = False
        app.prepare_client_system(app.client_generation)
        app.base_stations.set_power_background.assert_not_called()
        app.shutdown()
        app.base_stations.detached.assert_not_called()
        other = self.app()
        other.shutdown()
        other.base_stations.detached.assert_called_once()
        self.assertEqual(other.base_stations.detached.call_args[0][0], 'off')

    def test_the_previous_calibration_only_between_sessions(self):
        app = self.app()
        app.eye.manager.previous_calibration = Mock(return_value=None)
        with self.assertRaisesRegex(ValueError, 'no earlier calibration'):
            app.action({'action': 'eye-calibration', 'name': 'previous'})
        app.eye.manager.previous_calibration.return_value = {'name': 'tuned_temporal_eye_tracking_20261001_201500.onnx',
                                                             'date': '2026-10-01 20:15', 'current': False}
        app.eye.manager.use_calibration = Mock()
        with patch.object(app, 'running', return_value=True), self.assertRaisesRegex(ValueError, 'Stop the session'):
            app.action({'action': 'eye-calibration', 'name': 'previous'})
        app.action({'action': 'eye-calibration', 'name': 'previous'})
        app.eye.manager.use_calibration.assert_called_once_with('tuned_temporal_eye_tracking_20261001_201500.onnx')
        app.gaze_centre.reset = Mock()
        app.action({'action': 'gaze-centre-reset'})
        app.gaze_centre.reset.assert_called_once()

    def test_reports_and_pages_carry_the_new_states(self):
        app = self.app()
        system = app.client_system_status({})
        for key in ('tuning', 'base_stations', 'bitrate'):
            self.assertIn(key, system)
        self.assertEqual(system['bitrate']['active'], False)
        source = read('launcher/app.py')
        self.assertIn('session_tuning=self.session_tuning.status() if self.session_tuning else None', source)
        self.assertIn('base_stations=self.base_stations.status() if self.base_stations else None', source)


# ------------------------------------------------------------------------------------------------ Bazzite performance
class FakeSteamOSManager:
    """steamos-manager's properties as busctl --user reads and sets them."""
    def __init__(self, level='auto', clock=800, tdp=20, tdp_max=25, clock_max=2700, available=True):
        self.props = {} if not available else {
            (GPU_INTERFACE, 'GpuPerformanceLevel'): level, (GPU_INTERFACE, 'ManualGpuClock'): clock,
            (GPU_INTERFACE, 'ManualGpuClockMin'): 200, (GPU_INTERFACE, 'ManualGpuClockMax'): clock_max,
            (TDP_INTERFACE, 'TdpLimit'): tdp, (TDP_INTERFACE, 'TdpLimitMin'): 3, (TDP_INTERFACE, 'TdpLimitMax'): tdp_max}
        self.sets = []

    def __call__(self, arguments, timeout):
        if 'get-property' in arguments:
            value = self.props.get((arguments[-2], arguments[-1]))
            if value is None:
                return done('', 1, 'Failed to get property')
            return done(json.dumps({'type': 's' if isinstance(value, str) else 'u', 'data': value}))
        if 'set-property' in arguments:
            interface, name, signature, value = arguments[-4:]
            self.props[(interface, name)] = value if signature == 's' else int(value)
            self.sets.append((name, self.props[(interface, name)]))
            return done()
        return done('', 1, 'unexpected')


def sample(number, cpu=2500, gpu=800, busy=60, mains=True):
    return {'active': True, 'observed_monotonic': number,
            'cpu_policies': [{'reported_frequency_mhz': cpu, 'minimum_mhz': 1400, 'maximum_mhz': 4000}],
            'gpus': [{'gpu_busy_percent': busy, 'sensors': [{'clock_mhz': gpu}], 'max_sclk_mhz': 2700,
                      'performance_level': 'auto'}],
            'power_supplies': [{'type': 'Mains', 'online': 1 if mains else 0}, {'type': 'Battery'}]}


class SessionTuningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.logs = []

    def tuning(self, manager):
        return SessionTuning(runner=manager, log=self.logs.append, busctl='busctl', state_directory=self.state)

    def late(self, tuning, seconds=10):
        for _ in range(seconds):
            tuning.note_timing(timing(cadence='late_or_paired'))

    def test_on_the_charger_the_power_limit_goes_to_its_maximum_and_comes_back_at_stop(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        status = tuning.begin(sample(1))
        self.assertTrue(status['active'])
        self.assertEqual(manager.props[(TDP_INTERFACE, 'TdpLimit')], 25)
        self.assertIn('power limit 20 -> 25 W while on the charger', status['message'])
        receipt = json.loads((self.state / 'session-tuning.json').read_text())
        self.assertEqual((receipt['original']['tdp_w'], receipt['applied']['tdp_w']), (20, 25))
        status = tuning.end()
        self.assertEqual(manager.props[(TDP_INTERFACE, 'TdpLimit')], 20)
        self.assertFalse((self.state / 'session-tuning.json').exists())
        self.assertIn('power limit 20 W', status['message'])

    def test_on_battery_nothing_is_raised(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        status = tuning.begin(sample(1, mains=False))
        self.assertEqual(manager.sets, [])
        self.assertIn('on battery the power limit stays at 20 W', status['message'])
        self.assertIs(on_charger({'power_supplies': []}), None)

    def test_a_pc_without_steamos_manager_streams_as_before(self):
        tuning = self.tuning(FakeSteamOSManager(available=False))
        status = tuning.begin(sample(1))
        self.assertEqual(status['state'], 'unavailable')
        self.assertFalse(status['active'])
        self.assertIsNone(tuning.observe(sample(2)))
        self.assertEqual(self.logs, [], 'said on the page, not logged at every start')

    def test_late_frames_with_a_low_busy_gpu_hold_its_clock_higher_in_steps(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        self.late(tuning)
        self.assertIsNone(tuning.observe(sample(2)))
        self.assertEqual(tuning.observe(sample(3)), 1300, '45 % of 2700 MHz, rounded up')
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'manual')
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1300)
        self.late(tuning)
        tuning.observe(sample(4, gpu=1300))
        self.assertEqual(tuning.observe(sample(5, gpu=1300)), 1500)
        self.assertEqual([name for name, _ in manager.sets].count('GpuPerformanceLevel'), 1, 'manual is set once')
        # Never past 75 % of the maximum.
        for number in range(6, 30):
            self.late(tuning)
            tuning.observe(sample(number, gpu=manager.props[(GPU_INTERFACE, 'ManualGpuClock')]))
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 2000)
        tuning.end()
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto')

    def test_without_late_frames_or_with_an_idle_gpu_nothing_changes(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        for number in range(2, 8):
            tuning.observe(sample(number))
        self.late(tuning)
        for number in range(8, 14):
            tuning.observe(sample(number, busy=20))
        self.assertEqual([name for name, _ in manager.sets], ['TdpLimit'])

    def test_a_squeezed_processor_brings_the_clock_back_down(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        self.late(tuning)
        tuning.observe(sample(2))
        tuning.observe(sample(3))
        self.late(tuning)
        tuning.observe(sample(4, gpu=1300))
        tuning.observe(sample(5, gpu=1300))
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1500)
        tuning.observe(sample(6, cpu=900, gpu=1500))
        self.assertEqual(tuning.observe(sample(7, cpu=900, gpu=1500)), 1300)
        tuning.observe(sample(8, cpu=900, gpu=1300))
        self.assertEqual(tuning.observe(sample(9, cpu=900, gpu=1300)), 0, 'below the first held clock: automatic')
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto')
        self.assertIn('the processor is held back', tuning.status()['message'])

    def test_slowed_eye_tracking_counts_like_a_squeezed_processor(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        for number in (2, 3, 4):
            tuning.observe(sample(number, busy=10), gaze_rate=55)
        self.late(tuning)
        tuning.observe(sample(5), gaze_rate=55)
        tuning.observe(sample(6), gaze_rate=55)
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1300)
        tuning.observe(sample(7, gpu=1300), gaze_rate=20)
        tuning.observe(sample(8, gpu=1300), gaze_rate=20)
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto')
        self.assertIn('eye tracking slowed down', tuning.status()['message'])

    def test_a_setting_changed_elsewhere_is_left_alone(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        self.late(tuning)
        tuning.observe(sample(2))
        tuning.observe(sample(3))
        manager.props[(GPU_INTERFACE, 'ManualGpuClock')] = 1800  # Steam's Performance menu
        manager.props[(TDP_INTERFACE, 'TdpLimit')] = 15
        self.assertIsNone(tuning.observe(sample(4, gpu=1800)))
        self.assertIn('changed elsewhere', tuning.status()['message'])
        tuning.end()
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1800)
        self.assertEqual(manager.props[(TDP_INTERFACE, 'TdpLimit')], 15)

    def test_after_a_crash_the_next_start_sets_things_back(self):
        manager = FakeSteamOSManager(tdp=25)
        (self.state / 'session-tuning.json').write_text(json.dumps(
            {'version': 1, 'original': {'tdp_w': 20, 'gpu_level': 'auto'}, 'applied': {'tdp_w': 25}}))
        tuning = self.tuning(manager)
        status = tuning.recover()
        self.assertEqual(manager.props[(TDP_INTERFACE, 'TdpLimit')], 20)
        self.assertFalse((self.state / 'session-tuning.json').exists())
        self.assertIn('Set back after an unexpected stop: power limit 20 W.', status['message'])
        self.assertEqual(tuning.recover()['message'], status['message'], 'once only')

    def test_only_steamos_managers_session_bus_interface_is_used(self):
        source = read('launcher/session_tuning.py')
        self.assertIn("[self.busctl, '--user', 'set-property', SERVICE, OBJECT, interface, name, signature,", source)
        self.assertNotIn('sudo', source)
        self.assertNotIn('/sys/class/drm', source)


# ------------------------------------------------------------------------------------------------ base stations
STATION = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01'


class FakeBlueZ:
    """BlueZ over busctl --system and bluetoothctl, with one 2.0 base station (and headphones nearby)."""
    def __init__(self, powered=True, known=True, connected=False):
        self.powered, self.known, self.connected = powered, known, connected
        self.resolved = connected
        self.calls = []
        self.written = []
        self.state = 0x0b

    def objects(self):
        data = {'/org/bluez/hci0': {'org.bluez.Adapter1': {'Powered': {'type': 'b', 'data': self.powered}}},
                '/org/bluez/hci0/dev_11_22_33_44_55_66': {'org.bluez.Device1': {
                    'Address': {'type': 's', 'data': '11:22:33:44:55:66'}, 'Name': {'type': 's', 'data': 'Headphones'}}}}
        if self.known:
            data[STATION] = {'org.bluez.Device1': {
                'Address': {'type': 's', 'data': 'AA:BB:CC:DD:EE:01'}, 'Name': {'type': 's', 'data': 'LHB-1a2b3c4d'},
                'Connected': {'type': 'b', 'data': self.connected},
                'ServicesResolved': {'type': 'b', 'data': self.resolved}}}
            if self.resolved:
                data[STATION + '/service000c/char000f'] = {'org.bluez.GattCharacteristic1': {
                    'UUID': {'type': 's', 'data': POWER_UUID}}}
                data[STATION + '/service000c/char0011'] = {'org.bluez.GattCharacteristic1': {
                    'UUID': {'type': 's', 'data': '00001524-1212-efde-1523-785feabcd124'}}}
        return json.dumps({'type': 'a{oa{sa{sv}}}', 'data': [data]})

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        if 'GetManagedObjects' in arguments:
            return done(self.objects())
        if arguments[:1] == ['bluetoothctl']:
            self.known = True
            return done('[NEW] Device AA:BB:CC:DD:EE:01 LHB-1A2B3C4D\n')
        if 'Connect' in arguments:
            self.connected = self.resolved = True
            return done()
        if 'WriteValue' in arguments:
            self.written.append(int(arguments[arguments.index('aya{sv}') + 2]))
            return done()
        if 'ReadValue' in arguments:
            return done(json.dumps({'type': 'ay', 'data': [[self.state]]}))
        if 'Disconnect' in arguments:
            self.connected = self.resolved = False
            return done()
        return done('', 1, 'unexpected')


class BaseStationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.logs = []

    def stations(self, bluez, **options):
        return BaseStations(self.state, log=self.logs.append, runner=bluez, busctl='busctl', bluetoothctl='bluetoothctl',
                            clock=lambda: 1790000000, **options)

    def test_bluez_objects_are_read_for_2_0_stations_only(self):
        objects = base_stations.parse_objects(FakeBlueZ(connected=True).objects())
        self.assertEqual(base_stations.adapters(objects), [('/org/bluez/hci0', True)])
        found = [{key: value for key, value in row.items() if key != 'rssi'}  # 1.0.0.10 adds the signal strength
                 for row in base_stations.stations(objects)]
        self.assertEqual(found, [{'path': STATION, 'address': 'AA:BB:CC:DD:EE:01',
                                  'name': 'LHB-1A2B3C4D', 'connected': True, 'resolved': True}])
        self.assertEqual(base_stations.power_characteristic(objects, STATION), STATION + '/service000c/char000f')
        self.assertEqual(base_stations.parse_objects('not json'), {})

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_off_reaches_each_station_writes_one_byte_and_disconnects(self):
        bluez = FakeBlueZ()
        bluez.state = 0x00
        outcome = self.stations(bluez).set_power('off')
        self.assertEqual(bluez.written, [0x00])
        self.assertEqual(outcome['results'], [{'name': 'LHB-1A2B3C4D', 'result': 'sent', 'state': 'asleep'}])
        self.assertEqual(outcome['message'], '1 of 1 base stations turned off (LHB-1A2B3C4D).')
        self.assertTrue(any('Disconnect' in call for call in bluez.calls), 'a connection VROSTBridge made is closed')
        self.assertFalse(any(call[:1] == ['bluetoothctl'] for call in bluez.calls), 'known to BlueZ: no search')
        self.assertFalse(any('Headphones' in ' '.join(call) or '11_22_33' in ' '.join(call) for call in bluez.calls[1:]
                             if 'GetManagedObjects' not in call), 'other devices are never contacted')
        saved = json.loads((self.state / 'base-stations.json').read_text())
        self.assertEqual(saved['stations'], [{'address': 'AA:BB:CC:DD:EE:01', 'name': 'LHB-1A2B3C4D'}])
        self.assertEqual(saved['last']['action'], 'off')
        self.assertEqual(saved['last']['time'], 1790000000)
        self.assertIn('BASE_STATIONS 1 of 1 base stations turned off (LHB-1A2B3C4D).', self.logs)

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_on_searches_when_bluez_does_not_know_the_station_yet(self):
        bluez = FakeBlueZ(known=False)
        outcome = self.stations(bluez).set_power('on')
        self.assertEqual(bluez.written, [0x01])
        self.assertEqual(outcome['results'][0]['state'], 'on')
        self.assertIn(['bluetoothctl', '--timeout', '6', 'scan', 'on'], bluez.calls, 'nothing remembered: a full search')

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_a_remembered_station_bluez_forgot_is_searched_for_briefly(self):
        self.stations(FakeBlueZ()).set_power('off')  # remembered now
        bluez = FakeBlueZ(known=False)  # BlueZ drops unpaired devices soon after its last search
        self.stations(bluez).set_power('on')
        searches = [call for call in bluez.calls if call[:1] == ['bluetoothctl']]
        self.assertEqual(searches, [['bluetoothctl', '--timeout', '2', 'scan', 'on']], 'stops as soon as it is seen')
        self.assertEqual(bluez.written, [0x01])

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_the_buttons_search_afresh_and_learn_new_stations(self):
        bluez = FakeBlueZ(connected=True)
        self.stations(bluez).set_power('on', search=True)
        self.assertIn(['bluetoothctl', '--timeout', '6', 'scan', 'on'], bluez.calls)
        quiet = FakeBlueZ(connected=True)
        self.stations(quiet).set_power('on')
        self.assertFalse(any(call[:1] == ['bluetoothctl'] for call in quiet.calls), 'found already: no search')
        self.assertFalse(any('Disconnect' in call for call in quiet.calls), 'a connection VROSTBridge did not make stays')

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_no_bluetooth_or_no_stations_is_said_plainly(self):
        self.assertEqual(self.stations(FakeBlueZ(powered=False)).set_power('off')['message'],
                         'Bluetooth is off on this PC, so the base stations were not changed.')
        empty = FakeBlueZ(known=False)
        empty_runner = lambda arguments, timeout: done('') if arguments[:1] == ['bluetoothctl'] else empty(arguments, timeout)
        self.assertEqual(self.stations(empty_runner).set_power('on')['message'],
                         'No SteamVR 2.0 base stations were found nearby.')
        self.assertEqual(BaseStations(self.state, runner=FakeBlueZ(), busctl='', bluetoothctl='').set_power('on')['message'],
                         'Bluetooth (BlueZ) tools are not available on this PC.')
        with self.assertRaises(ValueError):
            self.stations(FakeBlueZ()).set_power('reboot')

    def test_one_command_at_a_time(self):
        stations = self.stations(FakeBlueZ())
        stations.working.acquire()
        try:
            self.assertEqual(stations.set_power('off')['message'], 'Another base station command is still running.')
        finally:
            stations.working.release()

    def test_windows_runs_the_powershell_script_with_the_stations_it_knows(self):
        (self.state / 'base-stations.json').write_text(json.dumps(
            {'version': 1, 'stations': [{'address': 'AA:BB:CC:DD:EE:01', 'name': 'LHB-1A2B3C4D'},
                                        {'address': 'bad', 'name': 'LHB-00000000'}]}))
        output = json.dumps({'stations': [{'name': 'LHB-1A2B3C4D', 'address': 'AA:BB:CC:DD:EE:01', 'result': 'sent',
                                           'state': 'asleep', 'error': ''},
                                          {'name': 'LHB-5E6F7A8B', 'address': 'AA:BB:CC:DD:EE:02', 'result': 'failed',
                                           'state': None, 'error': 'not reached'}], 'error': ''})
        runner = Mock(return_value=done('WARNING: something\n' + output + '\n'))
        stations = self.stations(runner)
        found, results, message = stations._windows('off')
        arguments = runner.call_args[0][0]
        self.assertTrue(arguments[0].endswith('powershell.exe'))
        for item in ('-NoProfile', '-NonInteractive', '-File', str(stations.script), '-Action', 'off', '-NoState'):
            self.assertIn(item, arguments)
        self.assertEqual(arguments[arguments.index('-Known') + 1], 'AA:BB:CC:DD:EE:01=LHB-1A2B3C4D')
        self.assertEqual([row['name'] for row in found], ['LHB-1A2B3C4D', 'LHB-5E6F7A8B'])
        self.assertEqual(message, '1 of 2 base stations turned off (LHB-1A2B3C4D). Not reached: LHB-5E6F7A8B (not reached).')
        stations._windows('on', search=True)
        self.assertNotIn('-Known', runner.call_args[0][0], 'the buttons search afresh')
        runner.return_value = done(json.dumps({'stations': [], 'error': 'Bluetooth is off on this PC, so the base '
                                                                         'stations were not changed.'}))
        self.assertEqual(stations._windows('on')[2], 'Bluetooth is off on this PC, so the base stations were not changed.')

    def test_closing_starts_the_command_as_its_own_process(self):
        popen = Mock()
        stations = self.stations(FakeBlueZ(), popen=popen)
        with patch('launcher.base_stations.sys.platform', 'linux'):
            self.assertTrue(stations.detached('off', ['/opt/VROSTBridge/VROSTBridge-Client']))
        arguments, options = popen.call_args
        self.assertEqual(arguments[0], ['/opt/VROSTBridge/VROSTBridge-Client', '--base-stations', 'off'])
        self.assertTrue(options['start_new_session'])
        with patch('launcher.base_stations.sys.platform', 'win32'):
            self.assertTrue(stations.detached('off', ['unused']))
        arguments, options = popen.call_args
        self.assertTrue(arguments[0][0].endswith('powershell.exe'))
        self.assertNotIn('-NoState', arguments[0], 'the script records what it did itself')
        self.assertEqual(arguments[0][arguments[0].index('-StatePath') + 1], str(self.state / 'base-stations.json'))
        self.assertTrue(options['creationflags'])
        self.assertFalse(stations.detached('explode', ['x']))

    def test_the_command_on_its_own_and_its_status(self):
        self.assertEqual(base_stations.main([]), 2)
        self.assertEqual(base_stations.main(['dance']), 2)
        result = subprocess.run([sys.executable, str(ROOT / 'launcher/bootstrap.py'), '--base-stations', 'dance'],
                                capture_output=True, text=True, timeout=60,
                                env={'PATH': '/usr/bin:/bin', 'XDG_STATE_HOME': str(self.state), 'HOME': str(self.state)})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.state / 'VROSTBridge' / 'client-startup.log').exists(), 'no startup log for it')
        stations = self.stations(FakeBlueZ())
        self.assertEqual(stations.status(), {'state': 'idle', 'message': '', 'stations': [], 'last': None})
        companion = read('launcher/app.py')
        self.assertIn("if arguments[:1]==['--base-stations']:", companion)
        self.assertIn("if sys.argv[1:2]==['--base-stations']:", read('launcher/bootstrap.py'))

    def test_the_powershell_script(self):
        script = read('launcher/BaseStations.ps1')
        for text in ("$serviceUuid=[Guid]'00001523-1212-efde-1523-785feabcd124'",
                     "$powerUuid=[Guid]'00001525-1212-efde-1523-785feabcd124'", "$namePattern='^LHB-[0-9A-Fa-f]{8}$'",
                     'GattWriteOption]::WriteWithoutResponse', '[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()',
                     "[ValidateSet('on','off','standby')]"):
            self.assertIn(text, script)
        self.assertNotIn('Pair', script.replace('PairingKind', ''))
        self.assertIn("'BaseStations.ps1'", read('packaging/build_windows_client.py'))
        import os
        import shutil
        pwsh = os.environ.get('PWSH') or shutil.which('pwsh')
        if not pwsh:
            self.skipTest('PowerShell is not installed here')
        check = ('$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("' +
                 str(ROOT / 'launcher/BaseStations.ps1') + '",[ref]$t,[ref]$e);if($e.Count){$e;exit 1}')
        result = subprocess.run([pwsh, '-NoProfile', '-Command', check], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


# ------------------------------------------------------------------------------------------------ gaze centring
class GazeCentreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'gaze-centre.json'
        self.model = 'tuned_temporal_eye_tracking_20261007_211500.onnx'
        self.enabled = True

    def centre(self):
        return GazeCentre(self.store, model=lambda: '/home/user/.config/ProjectBabble/Models/' + self.model,
                          enabled=lambda: self.enabled)

    def test_gaze_quaternion_is_the_inverse_of_gaze_angles(self):
        for yaw, pitch in ((0, 0), (18, 0), (-25, 10), (30, -20), (5.5, 44)):
            got = gaze_angles(gaze_quaternion(yaw, pitch))
            self.assertAlmostEqual(got[0], yaw, places=6)
            self.assertAlmostEqual(got[1], pitch, places=6)

    def test_a_gaze_resting_to_one_side_is_brought_back_in_steps_and_remembered(self):
        centre = self.centre()
        centre.begin()
        for _ in range(599):
            centre.note(18.0, -1.0)
        self.assertEqual(centre.status()['yaw'], 0.0, 'not before 600 samples')
        centre.note(18.0, -1.0)
        self.assertEqual(centre.status()['yaw'], 4.5, '30 % of the 15 degrees beyond a resting 3')
        self.assertEqual(centre.status()['pitch'], 0.0, 'a slightly lowered gaze is left as it is')
        for _ in range(3000):
            centre.note(18.0, -1.0)
        status = centre.status()
        self.assertAlmostEqual(status['yaw'], 15.0, delta=0.2)
        self.assertEqual(status['middle'], [18, -1])
        self.assertTrue(status['off_centre'])
        corrected = gaze_angles(centre.apply(gaze_quaternion(18.0, -1.0), 18.0, -1.0))
        self.assertAlmostEqual(corrected[0], 3.0, delta=0.2)
        # The next session with this calibration starts where this one ended; another calibration starts at none.
        again = self.centre()
        again.begin()
        self.assertAlmostEqual(again.status()['yaw'], 15.0, delta=0.2)
        self.model = 'tuned_temporal_eye_tracking_20261008_190000.onnx'
        fresh = self.centre()
        fresh.begin()
        self.assertEqual(fresh.status()['yaw'], 0.0)
        self.assertNotIn('20261007', json.dumps(fresh.status()))
        saved = json.loads(self.store.read_text())
        self.assertEqual(set(saved), {'version', 'models'})
        self.assertEqual(set(saved['models'][again.key]), {'yaw', 'pitch'}, 'the correction only, never samples')

    def test_the_correction_is_capped_switchable_and_can_be_reset(self):
        centre = self.centre()
        centre.begin()
        for _ in range(8000):
            centre.note(60.0, 40.0)
        status = centre.status()
        self.assertLessEqual(status['yaw'], 25.0)
        self.assertLessEqual(status['pitch'], 15.0)
        q = gaze_quaternion(60.0, 40.0)
        self.enabled = False
        self.assertIs(centre.apply(q, 60.0, 40.0), q, 'off: samples pass as they come')
        self.assertFalse(centre.status()['enabled'])
        centre.reset()
        self.assertEqual((centre.status()['yaw'], centre.status()['pitch']), (0.0, 0.0))
        again = self.centre()
        again.begin()
        self.assertEqual(again.status()['yaw'], 0.0, 'the reset is saved')

    def test_the_relay_forwards_corrected_samples(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(3)
        self.addCleanup(receiver.close)
        centre = self.centre()
        centre.begin()
        centre.yaw = 15.0  # as a previous session left it
        relay = GazeRelay(receiver.getsockname(), port=0, centre=centre).start()
        self.addCleanup(relay.close)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sender.close)
        # Baballonia's centre gaze: pitch (positive down), then yaw (positive right).
        packet = b'/tracking/eye/CenterPitchYaw\0\0\0\0,ff\0' + struct.pack('>ff', 0.0, 18.0)
        sender.sendto(packet, relay.incoming.getsockname())
        line = receiver.recv(256).decode().split()
        self.assertEqual(line[:3][0], 'VRG1')
        forwarded = tuple(float(value) for value in line[3:7])
        self.assertAlmostEqual(gaze_angles(forwarded)[0], 3.0, delta=0.05)
        self.assertEqual(relay.status()['centre']['yaw'], 15.0)


# ------------------------------------------------------------------------------------------------ calibrations
class CalibrationTests(unittest.TestCase):
    def setUp(self):
        from launcher.baballonia import BaballoniaManager
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.logs = []
        self.manager = BaballoniaManager(root=root / 'data', config=root / 'config', log=self.logs.append,
                                         systemd_run=None, systemctl=None)
        self.models = root / 'config' / 'Models'
        self.models.mkdir(parents=True)
        for stamp in ('20261001_201500', '20261005_180000', '20261007_211500'):
            (self.models / ('tuned_temporal_eye_tracking_' + stamp + '.onnx')).write_bytes(b'model')
        (self.models / 'eyeModel.onnx').write_bytes(b'bundled')
        self.settings = root / 'config' / 'ApplicationData' / 'LocalSettings.json'
        self.settings.parent.mkdir(parents=True)
        self.use('20261007_211500')

    def use(self, stamp):
        self.settings.write_text(json.dumps({'EyeHome_EyeModel': str(self.models / (
            'tuned_temporal_eye_tracking_' + stamp + '.onnx')), 'Other': 1}))
        self.manager._model = None

    def test_calibrations_newest_first_and_the_one_before_the_current(self):
        rows = self.manager.calibrations()
        self.assertEqual([row['date'] for row in rows], ['2026-10-07 21:15', '2026-10-05 18:00', '2026-10-01 20:15'])
        self.assertEqual([row['current'] for row in rows], [True, False, False])
        self.assertEqual(self.manager.previous_calibration()['date'], '2026-10-05 18:00')
        self.use('20261001_201500')
        self.assertIsNone(self.manager.previous_calibration(), 'the oldest has none before it')

    def test_choosing_one_points_baballonia_at_it_and_keeps_its_other_settings(self):
        name = 'tuned_temporal_eye_tracking_20261005_180000.onnx'
        with patch.object(self.manager, 'running', return_value=False):
            self.manager.use_calibration(name)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings['EyeHome_EyeModel'], str(self.models / name))
        self.assertEqual(settings['Other'], 1)
        self.assertEqual(self.manager.calibration(), {'calibrated': True, 'date': '2026-10-05'})
        self.assertIn('Eye tracking: Baballonia now uses the calibration from 2026-10-05 18:00.', self.logs)
        with patch.object(self.manager, 'running', return_value=True), self.assertRaisesRegex(ValueError, 'close Baballonia'):
            self.manager.use_calibration('tuned_temporal_eye_tracking_20261007_211500.onnx')
        for bad in ('../../etc/passwd', 'eyeModel.onnx', 'tuned_temporal_eye_tracking_20260101_000000.onnx', None):
            with patch.object(self.manager, 'running', return_value=False), self.assertRaises(ValueError):
                self.manager.use_calibration(bad)


# ------------------------------------------------------------------------------------------------ Game Mode
class GameModeTests(unittest.TestCase):
    def view(self, state):
        from launcher.game_ui import View
        view = View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.action = Mock()
        view.list_page = Mock()
        view.state = state
        return view

    def rows(self, view, page):
        getattr(view, 'page_' + page)()
        return {row[0]: row for row in view.list_page.call_args[0][1]}

    def test_this_pc_has_the_balance_and_base_station_rows(self):
        view = self.view({'settings': {'balance_performance': True, 'base_stations_off': False},
                          'system': {'tuning': {'message': 'Balancing this PC for the session.'},
                                     'base_stations': {'state': 'idle', 'message': '1 of 1 base stations turned on.'}}})
        rows = self.rows(view, 'pc')
        self.assertEqual(rows['toggle:balance_performance'][2], 'On')
        self.assertIn('Balancing this PC for the session.', rows['toggle:balance_performance'][4])
        self.assertEqual(rows['toggle:base_stations_off'][2], 'Off')
        self.assertTrue(rows['base-stations-on'][3])
        view.choose('toggle:balance_performance')
        view.action.assert_called_with('client-option', name='balance_performance', enabled=False)
        view.adjust('toggle:base_stations_off', 1)
        view.action.assert_called_with('client-option', name='base_stations_off', enabled=True)
        view.choose('base-stations-off')
        view.action.assert_called_with('base-stations', power='off')
        view.state['system']['base_stations'] = {'state': 'working', 'message': 'Turning the base stations off…'}
        rows = self.rows(view, 'pc')
        self.assertFalse(rows['base-stations-on'][3])
        self.assertEqual(rows['base-stations-on'][2], 'working…')

    def test_the_live_switches_are_not_locked_by_a_running_session(self):
        view = self.view({'settings': {}, 'running': True, 'system': {}})
        rows = self.rows(view, 'pc')
        self.assertTrue(rows['toggle:balance_performance'][3])
        self.assertFalse(rows['toggle:keep_awake'][3], 'session settings still wait for Stop')

    def test_stream_page_shows_the_bitrate_now_and_offers_the_recommended_settings(self):
        view = self.view({'settings': {'auto_bitrate': True}, 'running': True,
                          'system': {'bitrate': {'active': True, 'mbps': 40, 'ceiling': 49, 'reason': 'frames lost'}}})
        rows = self.rows(view, 'stream')
        self.assertEqual(rows['quality'][2], 'Auto · 40 Mbit/s now')
        self.assertEqual(rows['quality'][4], 'Now 40 Mbit/s; the most for this picture is 49 Mbit/s. Last change: frames lost.')
        self.assertFalse(rows['recommended-settings'][3], 'not during a session')
        view.state['running'] = False
        self.assertTrue(self.rows(view, 'stream')['recommended-settings'][3])
        view.choose('recommended-settings')
        view.action.assert_called_with('recommended-settings', 'Recommended settings chosen. They apply at your next Start.')

    def test_eye_tracking_page_has_centring_and_the_previous_calibration(self):
        view = self.view({'settings': {'gaze': True, 'gaze_centre': True}, 'running': False, 'eye_tracking': {
            'phase': 'off', 'owners': [], 'relay': {'centre': {'yaw': 12.4, 'pitch': 0.0, 'middle': None,
                                                               'off_centre': False, 'enabled': True}},
            'baballonia': {'state': 'installed', 'version': '1.1.1.0rc6', 'calibrated': True, 'running': False},
            'previous_calibration': {'name': 'tuned_temporal_eye_tracking_20261001_201500.onnx',
                                     'date': '2026-10-01 20:15'}}})
        rows = self.rows(view, 'eyetrack')
        self.assertEqual(rows['toggle:gaze_centre'][2], 'On')
        self.assertIn('Centring takes 12° right and 0° up off this calibration.', rows['toggle:gaze_centre'][4])
        self.assertEqual(rows['gaze-centre-reset'][2], 'now 12° · 0°')
        self.assertEqual(rows['eye-previous'][2], '2026-10-01 20:15')
        view.choose('eye-previous')
        view.action.assert_called_with('eye-calibration', 'The previous calibration is chosen. It applies when eye '
                                       'tracking next starts.', name='previous')
        view.choose('gaze-centre-reset')
        view.action.assert_called_with('gaze-centre-reset', 'Gaze centring starts again for this calibration.')
        view.state['eye_tracking']['phase'] = 'running'
        self.assertNotIn('eye-previous', self.rows(view, 'eyetrack'), 'not while Baballonia runs')

    def test_an_action_may_carry_a_field_called_name(self):
        from launcher.game_ui import View
        view = View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        sent = []
        view.action_fn = sent.append
        view.work = lambda run, interrupt=False: run()
        view.action('eye-calibration', 'Chosen.', name='previous')
        self.assertEqual(sent, [{'action': 'eye-calibration', 'name': 'previous'}])
        self.assertEqual(view.notice, 'Chosen.')


if __name__ == '__main__':
    unittest.main()
