"""RC4 integration: stream options, foveation presets, network test, quick resume, wake and telemetry."""
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from headset_fixture import HEADSET
from test_session_lifecycle import SessionLifecycleTests
from test_session_link import SessionTests, eventually
from host.events import HostEvents
from host.profile import configure
from host.stream_options import (driver_features, link_test_plan, parse_ib_values, run_link_test, stream_config,
                                 write_stream_config)
from launcher.headset import (FOVEATION_PRESETS, client_stream_preferences, foveated_size, foveation_choice,
                              foveation_parameters, resolve_stream_preferences, stream_width)
from launcher.network_test import NetworkTest, link_info, pacing_mbps, path_key, remember, saved_for
from launcher.session_link import (STREAM_CAPABILITY, QUALITY_CAPABILITY, SessionError, foveation_settings,
                                   host_settings, session_settings)
from launcher.wifi_session import WifiSession

ROOT = Path(__file__).resolve().parents[1]
BEYOND = dict(HEADSET, model='Beyond', manufacturer='Bigscreen', width=2560, height=2560, refresh_hz=90.0)
DEFAULT_SESSION = json.loads((ROOT/'host/default-session.json').read_text())


class StreamSettingsTests(unittest.TestCase):
    def test_rc4_fields_validate(self):
        settings = session_settings({'codec': 'AV1', 'bitrate': 150, 'ten_bit': True, 'intra_refresh': True,
                                     'pacing_mbps': 2300, 'keep_warm_minutes': 15,
                                     'foveation': {'center_size': [.4, .4], 'edge_ratio': [4, 4], 'gaze': True,
                                                   'optical_centre': True}})
        self.assertEqual(settings['foveation'], {'center_size': [.4, .4], 'center_shift': [0.0, 0.0],
                                                 'edge_ratio': [4.0, 4.0], 'gaze': True, 'optical_centre': True})
        self.assertEqual(session_settings({'pacing_mbps': 0})['pacing_mbps'], 0)
        for bad in ({'pacing_mbps': 49}, {'pacing_mbps': 50000}, {'pacing_mbps': True}, {'ten_bit': 1},
                    {'keep_warm_minutes': -1}, {'foveation': {'center_size': [.4, .4]}},
                    {'foveation': {'center_size': [.4, 2], 'edge_ratio': [4, 4]}},
                    {'foveation': {'center_size': [.4, .4], 'edge_ratio': [4, 4], 'gaze': 'yes'}},
                    {'foveation': [1, 2]}, {'codec': 'VP9'}):
            with self.subTest(bad=bad), self.assertRaises(SessionError):
                session_settings(bad)
        self.assertIsNone(foveation_settings(None))

    def test_older_hosts_never_receive_rc4_fields_or_av1(self):
        settings = {'codec': 'AV1', 'bitrate': 100, 'microphone': False, 'encoder': 'speed', 'ten_bit': True,
                    'foveation': None, 'pacing_mbps': 900, 'intra_refresh': False, 'keep_warm_minutes': 5}
        rc3 = host_settings(settings, [QUALITY_CAPABILITY])
        self.assertEqual(rc3, {'codec': 'Hevc', 'bitrate': 100, 'microphone': False, 'encoder': 'speed'})
        rc4 = host_settings(settings, [QUALITY_CAPABILITY, STREAM_CAPABILITY])
        self.assertEqual(rc4, settings)
        # The RC3 host's validation accepts the stripped request.
        self.assertEqual(session_settings(rc3)['codec'], 'Hevc')


class FoveationPresetTests(unittest.TestCase):
    def test_auto_choice_follows_headset_and_eye_tracking(self):
        self.assertEqual(foveation_choice('auto', 'beyond', True), 'eye-tracked')
        self.assertEqual(foveation_choice('auto', 'beyond', False), 'subtle')
        self.assertEqual(foveation_choice('auto', 'index', True), 'subtle')
        self.assertEqual(foveation_choice('auto', 'other', False), 'off')
        self.assertEqual(foveation_choice('eye-tracked', 'beyond', False), 'balanced')
        self.assertEqual(foveation_choice('balanced', 'index', False), 'balanced')
        self.assertEqual(foveation_choice('unknown', 'index', False), 'subtle')

    def test_parameters_need_host_features(self):
        self.assertIsNone(foveation_parameters('off'))
        full = foveation_parameters('eye-tracked', True, ('gaze-foveation', 'optical-centre'))
        self.assertTrue(full['gaze'] and full['optical_centre'])
        self.assertEqual(full['center_size'], FOVEATION_PRESETS['eye-tracked']['center_size'])
        self.assertFalse(foveation_parameters('eye-tracked', True, ())['gaze'])
        self.assertFalse(foveation_parameters('balanced', True, ('gaze-foveation',))['gaze'])
        # Every preset validates as session settings.
        for name in FOVEATION_PRESETS:
            session_settings({'foveation': foveation_parameters(name, True, ('gaze-foveation', 'optical-centre'))})

    def test_encoded_size_matches_alvr_negotiation(self):
        # Same alignment as alvr/server_core/src/connection.rs (and tests/convert_shader_tests.cpp).
        self.assertEqual(foveated_size(2560, 2560, foveation_parameters('eye-tracked')), (1408, 1408))
        self.assertEqual(foveated_size(2016, 2240, foveation_parameters('subtle')), (1728, 1856))
        self.assertEqual(foveated_size(2016, 2240, None), (2016, 2240))

    def test_av1_uses_hevc_limits_and_beyond_sizes(self):
        self.assertEqual(stream_width('AV1', 'recommended', dict(HEADSET, width=3500)), 3488)
        self.assertEqual(stream_width('AV1', 'beyond-detail', BEYOND), 3200)
        self.assertEqual(client_stream_preferences({'codec': 'AV1', 'resolution': 'beyond-balanced'})['codec'], 'AV1')
        # 1.0.0.13: the Beyond's sizes are no longer choices: saved with H.264 they follow its mode, within H.264's limit.
        self.assertEqual(client_stream_preferences({'codec': 'H264', 'resolution': 'beyond-balanced'})['codec'], 'H264')
        with self.assertRaisesRegex(ValueError, 'HEVC or AV1'):
            stream_width('H264', 'beyond-balanced', BEYOND)
        self.assertEqual(resolve_stream_preferences({'codec': 'AV1', 'bitrate': 80, 'resolution': 'automatic'}, BEYOND)['codec'], 'AV1')


class HostProfileTests(unittest.TestCase):
    def test_profile_applies_codec_10bit_foveation_and_intra_refresh(self):
        foveation = foveation_parameters('balanced', False, ('optical-centre',))
        doc = configure(DEFAULT_SESSION, codec='AV1', bitrate=150, ten_bit=True, foveation=foveation,
                        intra_refresh=True, headset=BEYOND, resolution='beyond-balanced')
        video = doc['session_settings']['video']
        self.assertEqual(video['preferred_codec']['variant'], 'AV1')
        self.assertEqual(video['transcoding_view_resolution']['Absolute']['width'], 2560)
        self.assertTrue(video['encoder_config']['use_10bit']['content'])
        fov = video['foveated_encoding']
        self.assertTrue(fov['enabled'])
        self.assertFalse(fov['content']['force_enable'])
        self.assertEqual(fov['content']['center_size']['content'], [.55, .5])
        self.assertEqual(fov['content']['edge_ratio']['content'], [3.0, 3.0])
        nvenc = video['encoder_config']['nvenc']
        self.assertTrue(nvenc['enable_intra_refresh'])
        self.assertLess(nvenc['intra_refresh_count'], nvenc['intra_refresh_period'])
        self.assertEqual(nvenc['intra_refresh_period'], 45)
        self.assertFalse(doc['session_settings']['connection']['avoid_video_glitching'])

    def test_defaults_keep_rc3_behaviour(self):
        doc = configure(DEFAULT_SESSION, codec='H264', bitrate=80, ten_bit=True)
        video = doc['session_settings']['video']
        self.assertFalse(video['encoder_config']['use_10bit']['content'], 'H.264 stays 8-bit')
        self.assertFalse(video['foveated_encoding']['enabled'])
        self.assertFalse(video['encoder_config']['nvenc']['enable_intra_refresh'])
        self.assertTrue(doc['session_settings']['connection']['avoid_video_glitching'])
        with self.assertRaises(ValueError):
            configure(DEFAULT_SESSION, codec='VP9')


class StreamOptionsTests(unittest.TestCase):
    def test_features_come_from_the_driver_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            base = set(driver_features(directory))
            self.assertIn('link-test', base)
            self.assertNotIn('pacing', base)
            manifest = {'source_patch_sha256': {name: '0' * 64 for name in (
                '0009-host-stream-config-and-pacing.patch', '0010-host-gaze-foveated-encoding.patch',
                '0011-host-amf-intra-refresh.patch', '0012-host-foveation-optical-centre.patch')}}
            (Path(directory)/'vrostbridge-build.json').write_text(json.dumps(manifest))
            features = set(driver_features(directory))
            self.assertTrue({'pacing', 'gaze-foveation', 'intra-refresh', 'optical-centre'} <= features)

    def test_stream_config_only_enables_supported_options(self):
        prefs = {'pacing_mbps': 2300, 'intra_refresh': True,
                 'foveation': {'center_size': [.4, .4], 'edge_ratio': [4, 4], 'gaze': True, 'optical_centre': True}}
        everything = stream_config(prefs, ['pacing', 'intra-refresh', 'gaze-foveation', 'optical-centre'])
        self.assertEqual((everything['pacing_mbps'], everything['intra_refresh'], everything['gaze_foveation'],
                          everything['optical_centre']), (2300, True, True, True))
        nothing = stream_config(prefs, [])
        self.assertEqual((nothing['pacing_mbps'], nothing['intra_refresh'], nothing['gaze_foveation'],
                          nothing['optical_centre']), (0, False, False, False))
        with tempfile.TemporaryDirectory() as directory:
            path = write_stream_config(directory, everything)
            self.assertEqual(json.loads(path.read_text()), everything)

    def test_link_test_plan_is_bounded_and_never_names_a_target(self):
        good = {'test_id': 5, 'port': 9944, 'fps': 90, 'steps': [80, 150], 'step_seconds': 1.0,
                'packet_size': 1400, 'pace_mbps': 920}
        self.assertEqual(link_test_plan(good)['port'], 9944)
        for bad in (dict(good, target='10.0.0.9'), dict(good, port=80), dict(good, steps=[80] * 30),
                    dict(good, pace_mbps=-1), dict(good, port='9944'), 'x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                link_test_plan(bad)

    def test_link_test_sends_only_to_the_requesting_peer(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0));receiver.settimeout(2)
        self.addCleanup(receiver.close)
        summary = run_link_test({'test_id': 7, 'port': receiver.getsockname()[1], 'fps': 60, 'steps': [10],
                                 'step_seconds': 0.2, 'packet_size': 512}, '127.0.0.1')
        self.assertEqual(summary['steps'][0]['frames_sent'], 12)
        self.assertEqual(len(receiver.recv(2048)), 512)

    def test_driver_lines_parse(self):
        self.assertEqual(parse_ib_values('VROST_GAZE_FOVEATION', 'VROST_GAZE_FOVEATION gaze=1 moves=4 left=0.1,-0.2'),
                         {'gaze': 1, 'moves': 4, 'left': [0.1, -0.2]})
        self.assertEqual(parse_ib_values('VROST_INTRA_REFRESH', 'VROST_INTRA_REFRESH backend=AMF accepted=1'),
                         {'backend': 'AMF', 'accepted': 1})
        self.assertIsNone(parse_ib_values('VROST_STREAM_CONFIG', 'something else'))


class HostEventStreamTests(unittest.TestCase):
    def test_stream_diagnostics_are_kept_as_latest_values(self):
        with patch.object(HostEvents, 'run'):
            events = HostEvents(lambda: None, Mock())
        def log(content):
            events.accept(json.dumps({'event_type': {'id': 'Log', 'data': {'severity': 'Info', 'content': content}}}))
        log('VROST_STREAM_CONFIG pacing_mbps=2300 paced=1 intra_refresh=1 gaze_foveation=1 optical_centre=1')
        log('VROST_GAZE_FOVEATION gaze=1 optical_centre=1 gaze_frames=10 fallback_frames=2 moves=3 left=0.1,0.0 right=-0.1,0.0')
        log('VROST_HOST_ENCODER selected backend=AMF width=2816 height=1408')
        log('VROST_INTRA_REFRESH backend=AMF codec=1 period_frames=45 units=12 accepted=1')
        stats = events.status()['statistics']
        self.assertEqual(stats['StreamConfig']['values']['pacing_mbps'], 2300)
        self.assertEqual(stats['GazeFoveation']['values']['moves'], 3)
        self.assertEqual(stats['EncoderBackend']['values']['backend'], 'AMF')
        self.assertEqual(stats['IntraRefresh']['values']['accepted'], 1)


class PacingTests(unittest.TestCase):
    def test_pacing_follows_the_client_link(self):
        self.assertEqual(pacing_mbps({'kind': 'wired', 'speed_mbps': 2500}), 2300)
        self.assertEqual(pacing_mbps({'kind': 'wired', 'speed_mbps': 1000}, bitrate=300), 920)
        self.assertEqual(pacing_mbps({'kind': 'wired', 'speed_mbps': 10000}), 0)
        self.assertEqual(pacing_mbps({'kind': 'wired', 'speed_mbps': 100}, bitrate=80), 0, 'below 1.25 x bitrate')
        # RC4.1: Wi-Fi paces at the measured clean rate only, never at a fraction of the PHY rate.
        self.assertEqual(pacing_mbps({'kind': 'wifi', 'rx_mbps': 2882.0}), 0, 'no test result: unpaced')
        self.assertEqual(pacing_mbps({'kind': 'wifi', 'rx_mbps': 2882.0}, {'clean_mbps': 400}), 400)
        self.assertEqual(pacing_mbps({'kind': 'wifi', 'rx_mbps': 300}, {'clean_mbps': 600}), 600)
        self.assertEqual(pacing_mbps({'kind': 'wifi'}, {'recommendation': {'clean_mbps': 250}}), 250)
        self.assertEqual(pacing_mbps({'kind': 'wifi'}, {'clean_mbps': 250}, bitrate=200), 250, '>= 1.25 x bitrate')
        self.assertEqual(pacing_mbps({'kind': 'wifi'}, {'clean_mbps': 240}, bitrate=200), 0, '< 1.25 x bitrate')
        self.assertEqual(pacing_mbps({'kind': 'wifi'}, {'clean_mbps': True}), 0)
        self.assertEqual(pacing_mbps({'kind': 'wifi'}), 0)
        self.assertEqual(pacing_mbps({'kind': 'unknown'}), 0)

    def test_link_info_reads_sysfs(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'eth0').mkdir();(Path(directory)/'eth0/speed').write_text('2500\n')
            (Path(directory)/'wlan0/wireless').mkdir(parents=True)
            self.assertEqual(link_info('eth0', directory)['speed_mbps'], 2500)
            wifi = link_info('wlan0', directory, wifi_sample=lambda name: {'rx_bitrate_mbps': 2400.0})
            self.assertEqual((wifi['kind'], wifi['rx_mbps']), ('wifi', 2400.0))
            self.assertEqual(link_info('../x', directory)['kind'], 'unknown')

    def test_results_are_kept_per_host_and_adapter(self):
        saved = {}
        for index in range(10):
            saved = remember(saved, {'time': index, 'host_id': 'h', 'interface': 'if' + str(index),
                                     'recommendation': {'recommended_mbps': 100 + index, 'clean_mbps': 250}})
        self.assertEqual(len(saved), 8)
        self.assertEqual(saved_for(saved, 'h', 'if9')['recommended_mbps'], 109)
        self.assertIsNone(saved_for(saved, 'h', 'if0'))
        self.assertEqual(path_key('h', 'if1'), 'h|if1')


class LoopbackNetworkTestTests(SessionTests):
    def setUp(self):
        super().setUp()
        self.host.features = ['link-test']
        self.host.link_test_callback = run_link_test

    def test_network_test_runs_end_to_end_between_sessions(self):
        tester = NetworkTest(self.client, self.logs.append)
        with patch('launcher.network_test.plan_steps', return_value=[20, 40]):
            result = tester.run('127.0.0.1', fps=60)
        # 1.0.0.5: a warm-up step at the lowest rate comes first and is not judged.
        self.assertEqual([row['rate_mbps'] for row in result['steps']], [20, 20, 40])
        self.assertEqual([row['warmup'] for row in result['steps']], [True, False, False])
        self.assertIsNotNone(result['recommendation']['recommended_mbps'])
        self.assertEqual(tester.status()['state'], 'done')
        self.assertIn('link-test', self.client.host_features)

    def test_network_test_is_refused_during_a_session_and_without_the_feature(self):
        self.client.start({'codec': 'H264', 'bitrate': 80}, host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        with self.assertRaisesRegex(SessionError, 'Stop the session'):
            self.client.link_test({'test_id': 1, 'port': 9999, 'fps': 60, 'steps': [10]}, host_ip='127.0.0.1')
        self.client.stop()
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.host.features = []
        with self.assertRaisesRegex(SessionError, 'rc4'):
            self.client.link_test({'test_id': 1, 'port': 9999, 'fps': 60, 'steps': [10]}, host_ip='127.0.0.1')

    def test_start_reply_carries_the_wake_address_of_the_adapter_reached(self):
        seen = []
        def info(local):
            seen.append(local)
            return {'wake': {'mac': 'aa:bb:cc:dd:ee:ff' if local == '127.0.0.1' else None}}
        self.host.host_info = info
        self.client.start({'codec': 'H264', 'bitrate': 80}, host_ip='127.0.0.1')
        self.assertEqual(self.client.snapshot()['host']['host_info']['wake']['mac'], 'aa:bb:cc:dd:ee:ff')
        self.assertIn('127.0.0.1', seen)
        self.client.stop()

    def test_hello_lists_features_and_status_carries_host_info(self):
        self.host.host_info = lambda local: {'wake': {'mac': 'aa:bb:cc:dd:ee:ff', 'ready': True}}
        owner = self.credentials()
        self.assertEqual(self.host.hello()['features'], ['display-modes', 'link-test', 'session-gaze'])
        status = self.host.dispatch('/status', owner, '127.0.0.1', local='127.0.0.1')
        self.assertEqual(status['host_info']['wake']['mac'], 'aa:bb:cc:dd:ee:ff')


class WifiPermanentFixTests(unittest.TestCase):
    def test_disable_permanently_modifies_only_the_connected_profile_and_reconnects(self):
        calls = []
        def runner(arguments, timeout):
            calls.append(arguments)
            if 'show' in arguments:
                return Mock(returncode=0, stdout='0b0c0d0e-1111-2222-3333-444455556666\nHome\n', stderr='')
            if 'get' in arguments:
                return Mock(returncode=0, stdout='Power save: off\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'wlan0/wireless').mkdir(parents=True)
            session = WifiSession(runner=runner, log=lambda line: None, sys_class_net=directory, nmcli='nmcli',
                                  iw='iw', ip='ip')
            message = session.disable_permanently('')
        self.assertIn('Home', message)
        self.assertIn(['nmcli', 'connection', 'modify', 'uuid', '0b0c0d0e-1111-2222-3333-444455556666',
                       '802-11-wireless.powersave', '2'], calls)
        self.assertIn(['nmcli', 'connection', 'up', 'uuid', '0b0c0d0e-1111-2222-3333-444455556666'], calls)


class ClientIntegrationTests(SessionLifecycleTests):
    def test_host_preferences_carry_foveation_pacing_and_measured_bitrate(self):
        app = self.app()
        app.session_link.host_features = ['foveation', 'gaze-foveation', 'optical-centre', 'pacing', 'link-test']
        app.session_link.host_id = 'h'
        app.session_link.network_status.return_value = {'interface': '', 'source_interface': 'eth9'}
        app.settings.update(gaze=True, intra_refresh=True)
        # RC4.5: the region follows the eyes once VROSTBridge's Baballonia is set up and calibrated.
        app.eye.manager.installed = lambda: True
        app.eye.manager.calibration = lambda: {'calibrated': True, 'date': '2026-10-05'}
        app.settings['network_tests'] = remember({}, {'time': time.time(), 'host_id': 'h', 'interface': 'eth9',
                                                      'recommendation': {'recommended_mbps': 180, 'clean_mbps': 250}})
        with patch('launcher.app.link_info', return_value={'kind': 'wired', 'speed_mbps': 2500}):
            prefs = app.host_preferences({'codec': 'Hevc', 'bitrate': 80, 'headset': BEYOND})
        # 1.0.0.8: the measured 180 Mbit/s, held to what this stream's picture needs (launcher/stream_budget.py).
        self.assertEqual(prefs['bitrate'], 56)
        self.assertEqual(prefs['pacing_mbps'], 2300)
        self.assertTrue(prefs['foveation']['gaze'])
        self.assertNotIn('gaze', prefs, 'only hosts listing session-gaze accept the field')
        self.assertEqual(prefs['foveation']['center_size'], [.4, .4])
        self.assertTrue(prefs['ten_bit'] and prefs['intra_refresh'])
        session_settings(prefs)
        # CPU decoding cannot undo foveation; manual bitrate ignores the measurement.
        app.settings.update(software=True, auto_bitrate=False)
        with patch('launcher.app.link_info', return_value={'kind': 'wired', 'speed_mbps': 2500}):
            prefs = app.host_preferences({'codec': 'H264', 'bitrate': 80, 'headset': HEADSET})
        self.assertIsNone(prefs['foveation'])
        self.assertEqual(prefs['bitrate'], 80)
        self.assertFalse(prefs['ten_bit'])

    def test_per_headset_settings_switch_when_the_headset_changes(self):
        app = self.app()
        app.settings['cached_headset'] = dict(BEYOND)
        app.settings.update(codec='Hevc', bitrate=200, resolution='beyond-balanced', foveation='eye-tracked')
        app.remember_profile()
        app.settings['cached_headset'] = dict(HEADSET)
        app.settings.update(codec='H264', bitrate=90, resolution='sharp', foveation='subtle')
        app.remember_profile()
        quality = app.apply_profile('beyond', {'codec': 'H264', 'bitrate': 90, 'resolution': 'sharp'})
        self.assertEqual(quality, {'codec': 'Hevc', 'bitrate': 200, 'resolution': 'beyond-balanced'})
        self.assertEqual(app.settings['foveation'], 'eye-tracked')
        app.save_settings()
        reloaded = self.app()
        self.assertEqual(set(reloaded.settings['headset_profiles']), {'index', 'beyond'})
        self.assertEqual(reloaded.settings['headset_profiles']['index']['bitrate'], 90)

    def test_new_switches_persist_and_validate(self):
        app = self.app();app.launch = Mock()
        app.action({'action': 'start', 'foveation': 'balanced', 'keep_warm_minutes': 15, 'intra_refresh': True,
                    'headset_hud': True, 'auto_bitrate': False, 'codec': 'AV1'})
        reloaded = self.app()
        self.assertEqual((reloaded.settings['foveation'], reloaded.settings['keep_warm_minutes'],
                          reloaded.settings['intra_refresh'], reloaded.settings['headset_hud'],
                          reloaded.settings['auto_bitrate'], reloaded.settings['codec']),
                         ('balanced', 15, True, True, False, 'AV1'))
        for bad in ({'foveation': 'max'}, {'keep_warm_minutes': 7}, {'intra_refresh': 'yes'}, {'pacing': None}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.app().action(dict({'action': 'start'}, **bad))

    def test_gradual_recovery_waits_for_host_confirmation(self):
        app = self.app()
        app.settings['intra_refresh'] = True
        app.native_command = Mock(return_value=True)
        app.running = Mock(return_value=True)
        link = {'state': 'running', 'host': {'state': 'running', 'pipeline': {'stream': {'intra_refresh_active': 0}}}}
        app.session_link.snapshot.return_value = link
        app.update_headset_status()
        self.assertNotIn(unittest.mock.call('gradual 1'), app.native_command.call_args_list)
        link['host']['pipeline']['stream']['intra_refresh_active'] = 1
        app.update_headset_status();app.update_headset_status()
        self.assertEqual(app.native_command.call_args_list.count(unittest.mock.call('gradual 1')), 1)

    def test_status_and_hud_lines_reach_the_native_client(self):
        app = self.app()
        app.settings['headset_hud'] = True
        app.native_command = Mock(return_value=True)
        app.running = Mock(return_value=True)
        app.timings = {'unique_fps': '89.9', 'net_lost_frames': '2'}
        app.metrics = {'frame_age_ms': '31'}
        app.session_link.snapshot.return_value = {'state': 'starting', 'message': 'Host restarting SteamVR',
                                                  'host': {'pipeline': {'total_ms': {'mean': 42.5}}}}
        app.update_headset_status()
        commands = [call.args[0] for call in app.native_command.call_args_list]
        self.assertIn('status Host restarting SteamVR', commands)
        self.assertIn('hud 89.9 fps | latency 42.5 ms | frame age 31 ms | lost 2', commands)

    def test_wake_on_lan_runs_only_when_discovery_fails_and_the_host_is_known(self):
        app = self.app()
        cancel = threading.Event()
        app.session_link.discover.side_effect = [ValueError('No VROSTBridge host found.'), {'host_ip': '192.168.1.40'}]
        app.settings.update(host_mac='aa:bb:cc:dd:ee:ff', wake_host=True, host_ip='192.168.1.40')
        app.host_waker.wake_and_wait.return_value = {'woken': True, 'message': 'The host PC is awake.'}
        with patch('launcher.app.lan_addresses', return_value=[{'address': '192.168.1.8', 'prefix': 24}]):
            app.ensure_host_awake(cancel)
        mac, targets = app.host_waker.wake_and_wait.call_args.args
        self.assertEqual(mac, 'aa:bb:cc:dd:ee:ff')
        self.assertIn('192.168.1.255', targets)
        app.session_link.discover.side_effect = ValueError('No VROSTBridge host found.')
        app.settings['host_mac'] = ''
        with self.assertRaisesRegex(ValueError, 'No VROSTBridge host'):
            app.ensure_host_awake(cancel)

    def test_learnt_mac_prefers_the_host_report(self):
        app = self.app()
        app.session_link.snapshot.return_value = {'host_ip': '192.168.1.40',
                                                  'host': {'host_info': {'wake': {'mac': 'AA-BB-CC-DD-EE-01'}}}}
        with patch('launcher.app.learn_mac', return_value='aa:bb:cc:dd:ee:02'):
            app.learn_host_mac()
        self.assertEqual(app.settings['host_mac'], 'aa:bb:cc:dd:ee:01')
        self.assertEqual(self.app().settings['host_mac'], 'aa:bb:cc:dd:ee:01')


class HostIntegrationTests(SessionLifecycleTests):
    def host_app(self):
        app = self.app('host');app.host = self.root/'host';app.host.mkdir();(app.host/'ALVR Dashboard.exe').touch()
        app.session = Mock(return_value=json.loads(json.dumps(DEFAULT_SESSION)))
        app.host_prepared = True;app.setup_host = Mock()
        app.host_control.active = False;app.host_control.identities = {}
        app.keep_awake.status.return_value = {'state': 'active'}
        app.sleep_after_stop.status.return_value = {'state': 'idle', 'minutes': None}
        def started(init_nonce=''):
            app.host_control.active = True
        app.host_control.start.side_effect = started
        def stopped():
            app.host_control.active = False
        app.host_control.stop.side_effect = stopped
        app.host_control.snapshot.return_value = {'runtime_state': 'ready', 'available': True, 'clients': []}
        return app

    def start(self, app, **extra):
        prefs = dict({'codec': 'Hevc', 'bitrate': 120, 'microphone': False, 'ten_bit': True, 'pacing_mbps': 2300,
                      'keep_warm_minutes': 15,
                      'foveation': foveation_parameters('balanced', False, ('optical-centre',))}, **extra)
        with patch('launcher.app.require_private_home_network'), \
                patch('launcher.app.host_loader_check', return_value={'ok': True}), patch('host.audio.subprocess.run'):
            app.start_host_session(prefs)

    def test_host_writes_stream_options_and_keeps_the_pc_awake(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['pacing', 'optical-centre', 'intra-refresh', 'gaze-foveation'])
        self.start(app)
        config = json.loads((app.host/'vrostbridge-stream.json').read_text())
        self.assertEqual((config['pacing_mbps'], config['optical_centre'], config['gaze_foveation']), (2300, True, False))
        saved = json.loads((app.host/'session.json').read_text())['session_settings']['video']
        self.assertTrue(saved['foveated_encoding']['enabled'])
        self.assertTrue(saved['encoder_config']['use_10bit']['content'])
        app.keep_awake.acquire.assert_called_once_with()
        app.sleep_after_stop.cancel.assert_called_once()

    def test_quick_resume_reuses_matching_runtime_and_restarts_on_changes(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['pacing', 'optical-centre'])
        self.start(app)
        app.stop_host_session()
        self.assertIsNotNone(app.parked, 'SteamVR kept open for quick resume')
        app.host_control.stop.assert_not_called()
        app.keep_awake.release.assert_not_called()
        # Same settings (pacing may differ): reuse without relaunching.
        self.start(app, pacing_mbps=920)
        self.assertEqual(app.host_control.start.call_count, 1)
        self.assertIsNone(app.parked)
        self.assertEqual(json.loads((app.host/'vrostbridge-stream.json').read_text())['pacing_mbps'], 920)
        # Stop again, then a different bitrate restarts SteamVR.
        app.stop_host_session()
        self.start(app, bitrate=150)
        app.host_control.stop.assert_called_once()
        self.assertEqual(app.host_control.start.call_count, 2)
        app.unpark()

    def test_parked_runtime_closes_after_its_time_or_on_local_stop(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), [])
        self.start(app)
        app.stop_host_session()
        token = app.parked['token']
        app.parked_timeout('stale-token')
        self.assertIsNotNone(app.parked)
        app.parked_timeout(token)
        self.assertIsNone(app.parked)
        app.host_control.stop.assert_called_once()
        app.keep_awake.release.assert_called()
        app.sleep_after_stop.arm.assert_called_once()
        # A local Stop on the host closes a parked runtime immediately.
        self.start(app)
        app.stop_host_session()
        app.session_link.request_stop.return_value = {'state': 'waiting'}
        app.action({'action': 'stop'})
        self.assertIsNone(app.parked)
        self.assertEqual(app.host_control.stop.call_count, 2)

    def test_no_quick_resume_without_minutes_or_after_a_host_stop(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), [])
        self.start(app, keep_warm_minutes=0)
        app.stop_host_session()
        self.assertIsNone(app.parked)
        app.host_control.stop.assert_called_once()
        self.start(app)
        app.force_full_stop = True
        app.stop_host_session()
        self.assertIsNone(app.parked)
        self.assertEqual(app.host_control.stop.call_count, 2)

    def test_telemetry_confirms_intra_refresh_per_encoder(self):
        app = self.host_app()
        app.session_intra_refresh = True
        app.host_events.pipeline.summary.return_value = {'total_ms': {'mean': 40}}
        statistics = {'EncoderBackend': {'values': {'backend': 'AMF'}},
                      'IntraRefresh': {'values': {'accepted': 0}},
                      'StreamConfig': {'values': {'pacing_mbps': 2300, 'paced': 1}},
                      'GazeFoveation': {'values': {'gaze_frames': 5, 'moves': 2}}}
        app.host_events.status.return_value = {'statistics': statistics}
        stream = app.session_telemetry()['stream']
        self.assertEqual((stream['intra_refresh_active'], stream['pacing_mbps'], stream['gaze_moves']), (0, 2300, 2))
        statistics['IntraRefresh']['values']['accepted'] = 1
        self.assertEqual(app.session_telemetry()['stream']['intra_refresh_active'], 1)
        statistics['EncoderBackend']['values']['backend'] = 'NVENC'
        statistics.pop('IntraRefresh')
        self.assertEqual(app.session_telemetry()['stream']['intra_refresh_active'], 1)
        self.assertLessEqual(len(json.dumps(app.session_telemetry())), 2048)

    def test_host_info_reports_the_adapter_the_client_reached(self):
        app = self.host_app()
        app.host_adapters = [{'ip': '192.168.1.40', 'mac': 'aa:bb:cc:dd:ee:ff', 'kind': 'wired', 'name': '10G',
                              'link_mbps': 10000}]
        app.wake_info = {'adapters': [{'mac': 'aa:bb:cc:dd:ee:ff', 'wake_on_magic_packet': True}], 'message': 'ok'}
        info = app.session_host_info('192.168.1.40')
        self.assertEqual((info['wake']['mac'], info['wake']['ready'], info['wake']['link_mbps']),
                         ('aa:bb:cc:dd:ee:ff', True, 10000))
        self.assertFalse(app.session_host_info('192.168.1.99')['wake']['ready'])
        self.assertLessEqual(len(json.dumps(info)), 2048)

    def test_sleep_after_setting_validates_and_persists(self):
        app = self.host_app()
        app.action({'action': 'host-power', 'sleep_after_minutes': 30})
        app.sleep_after_stop.configure.assert_called_with(30)
        self.assertEqual(self.app('host').settings['sleep_after_minutes'], 30)
        with self.assertRaises(ValueError):
            app.action({'action': 'host-power', 'sleep_after_minutes': 7})


class HostRepackWithoutFFmpegTests(unittest.TestCase):
    def test_drop_ffmpeg_removes_runtime_only_when_the_driver_does_not_import_it(self):
        import hashlib, shutil
        from test_rc3_updates import HostRepackTests
        repack = HostRepackTests.load()
        current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'patches').glob('*.patch'))}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = HostRepackTests.previous_payload(root, repack)
            (previous/'dist/host/licenses').mkdir(parents=True)
            (previous/'dist/host/licenses/FFmpeg.txt').write_text('GPL notice')
            (previous/'dist/host/bin/win64/swscale-9.dll').write_bytes(b'ffmpeg')
            repack.manifest(previous)
            overlay = HostRepackTests.overlay(root, current, repack)
            driver = overlay/'bin/win64/driver_alvr_server.dll'
            driver.write_bytes(b'new vrostbridge-native-1 21.0.0-dev14 imports AVCODEC-62.DLL')
            metadata = json.loads((overlay/'vrostbridge-build.json').read_text())
            metadata['files'][0]['sha256'] = hashlib.sha256(driver.read_bytes()).hexdigest()
            (overlay/'vrostbridge-build.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(RuntimeError, 'still imports avcodec-62.dll'):
                repack.repack(previous, root/'bad', overlay, without_ffmpeg=True, launcher=HostRepackTests.launcher(root))
            shutil.rmtree(overlay)
            repack.repack(previous, root/'out', HostRepackTests.overlay(root, current, repack), without_ffmpeg=True,
                          launcher=HostRepackTests.launcher(root))
            host = root/'out/dist/host'
            self.assertFalse((host/'bin/win64/avcodec-62.dll').exists())
            self.assertFalse((host/'bin/win64/swscale-9.dll').exists())
            self.assertFalse((host/'licenses/FFmpeg.txt').exists())
            self.assertIn('GPL FFmpeg runtime', (root/'out/HOST_PROVENANCE.txt').read_text())
            repack.verify_input(root/'out')


def load_tests(loader, tests, pattern):
    """Only this module's tests; the imported fixture classes run in their own modules."""
    suite = unittest.TestSuite()
    for case in (StreamSettingsTests, FoveationPresetTests, HostProfileTests, StreamOptionsTests, HostEventStreamTests,
                 PacingTests, WifiPermanentFixTests, HostRepackWithoutFFmpegTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    for case in (LoopbackNetworkTestTests, ClientIntegrationTests, HostIntegrationTests):
        suite.addTests(case(name) for name in case.__dict__ if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
