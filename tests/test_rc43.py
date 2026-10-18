"""RC4.3 contracts: stream tracking, Wi-Fi 7 bands, the headset-PC graphics limit, Game Mode and the
host microphone setup. Physical behaviour still needs a headset, Bazzite and Windows."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
import subprocess

from unittest.mock import Mock

from launcher import connection_kind

ROOT = Path(__file__).resolve().parents[1]


def completed(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


# A Wi-Fi 7 multi-link connection like the RC4.2 test's USB adapter: links on all three bands.
MLO3 = """Connected to 3c:84:6a:aa:bb:cc (on wlp5s0f3u2)
\tSSID: Home:Net
\tLink 0 BSSID 3c:84:6a:aa:bb:c0
\t\tfreq: 2437.0
\t\tsignal: -30 dBm
\tLink 1 BSSID 3c:84:6a:aa:bb:c1
\t\tfreq: 5500.0
\t\tsignal: -28 dBm
\tLink 2 BSSID 3c:84:6a:aa:bb:c2
\t\tfreq: 6115.0
\t\tsignal: -26 dBm
"""


class WifiBandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.net = Path(self.temp.name)
        (self.net / 'wlx0' / 'wireless').mkdir(parents=True)

    def describe(self, text):
        return connection_kind.describe('wlx0', self.net, runner=lambda *a: completed(0, text), iw='/usr/sbin/iw')

    def test_multi_link_names_every_band_and_does_not_warn(self):
        info = self.describe(MLO3)
        self.assertEqual(info['details']['bands'], ['2.4 GHz', '5 GHz', '6 GHz'])
        self.assertEqual(info['details']['band'], '6 GHz')
        self.assertIn('multi-link 2.4 + 5 + 6 GHz', info['summary'])
        self.assertEqual(info['warnings'], [], 'RC4.2 warned "2.4 GHz" because the first link was 2.4 GHz')

    def test_only_2_4_ghz_still_warns(self):
        only = MLO3.replace('5500.0', '2462.0').replace('6115.0', '2412.0')
        info = self.describe(only)
        self.assertEqual(info['details']['bands'], ['2.4 GHz'])
        self.assertTrue(any('2.4 GHz' in text for text in info['warnings']))
        single = 'Connected to 3c:84:6a:12:34:56 (on wlx0)\n\tSSID: Home\n\tfreq: 2437.0\n\tsignal: -50 dBm\n'
        info = self.describe(single)
        self.assertEqual((info['details']['band'], info['details']['multi_link']), ('2.4 GHz', 0))
        self.assertTrue(any('2.4 GHz' in text for text in info['warnings']))

    def test_multi_link_without_frequencies_has_no_dangling_text(self):
        bare = '\n'.join(line for line in MLO3.splitlines() if 'freq' not in line) + '\n'
        info = self.describe(bare)
        self.assertIn('Wi-Fi · multi-link · ', info['summary'])
        self.assertEqual(info['details']['bands'], [])


class StreamTrackingTests(unittest.TestCase):
    """Video counts as arriving while frames decode, even when the reported state lags."""

    def setUp(self):
        from test_session_lifecycle import SessionLifecycleTests
        self.case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.app = self.case.app()
        self.app.running = lambda: True

    def feed(self, now, **metrics):
        self.app.metrics = metrics
        self.app.last_metrics = now
        return self.app.video_arriving(now)

    def test_decoded_frames_count_while_tracking_is_lost(self):
        now = 1000.0
        self.assertFalse(self.feed(now, state='recovery', decoded='0'))
        self.assertTrue(self.feed(now + 1, state='recovery', decoded='75'), 'frames decode: video arrives')
        self.assertTrue(self.feed(now + 2, state='recovery', decoded='75'), 'a second without a new frame is fine')
        self.assertFalse(self.feed(now + 4, state='recovery', decoded='75'), 'no new frame for 3 s')
        self.assertTrue(self.feed(now + 5, state='streaming', decoded='75'))

    def test_the_first_sample_of_a_running_stream_counts(self):
        self.assertTrue(self.feed(50.0, state='recovery', decoded='12438'))

    def test_a_restarted_client_process_counts_from_its_own_counter(self):
        now = 1000.0
        self.feed(now, state='streaming', decoded='13868')
        self.assertTrue(self.feed(now + 1, state='recovery', decoded='5'), 'counter restarted with new frames')

    def test_stale_metrics_or_a_stopped_client_are_not_video(self):
        now = 1000.0
        self.app.metrics = {'state': 'streaming', 'decoded': '10'}
        self.app.last_metrics = now - 3.5
        self.assertFalse(self.app.video_arriving(now))
        self.app.last_metrics = now
        self.app.running = lambda: False
        self.assertFalse(self.app.video_arriving(now))

    def test_start_clears_old_downtime(self):
        app = self.app
        app.stream_up, app.stream_ever_up, app.stream_up_at = False, True, time.monotonic() - 100
        app.client_identity.connection_state = 'searching'
        self.assertGreaterEqual(app.stream_report()['down_s'], 99)
        app.decoded_seen, app.decoded_at = 500, time.monotonic()
        app.reset_stream_tracking()
        self.assertEqual(app.stream_report()['down_s'], 0.0)
        self.assertEqual((app.decoded_seen, app.stream_ever_up), (None, False))

    def test_a_second_start_while_running_changes_nothing(self):
        app = self.app
        app.stream_up, app.stream_ever_up, app.stream_up_at = True, True, 123.0
        app.decoded_seen, app.start_clock, app.milestones = 900, 55.0, {'video': 4.2}
        app.session_cancelled.set()
        with self.assertRaisesRegex(ValueError, 'Already running'):
            app.action({'action': 'start'})
        self.assertEqual((app.stream_up, app.stream_ever_up, app.stream_up_at), (True, True, 123.0))
        self.assertEqual((app.decoded_seen, app.start_clock, app.milestones), (900, 55.0, {'video': 4.2}))
        self.assertTrue(app.session_cancelled.is_set())

    def test_track_stream_reports_up_from_decoded_frames(self):
        app = self.app
        app.client_identity.connection_state = 'searching'
        app.metrics = {'state': 'recovery', 'decoded': '0'}
        app.last_metrics = time.monotonic()
        app.track_stream()
        self.assertFalse(app.stream_report()['up'])
        app.metrics = {'state': 'recovery', 'decoded': '40'}
        app.last_metrics = time.monotonic()
        app.track_stream()
        self.assertTrue(app.stream_report()['up'])


class GraphicsLimitTests(unittest.TestCase):
    """The RC4.2 physical test: 2560 per eye on integrated Radeon graphics kept falling to half rate."""

    def test_counts_only_streaming_seconds_and_needs_eight_of_thirty(self):
        from launcher.graphics_limit import GraphicsLimit, WARMUP_S
        limit = GraphicsLimit()
        for _ in range(20):
            self.assertIsNone(limit.observe('idle'))
        self.assertIsNone(limit.observe('bogus'))
        self.assertEqual(limit.snapshot()['streaming_s'], 0)
        # The first seconds of video (first keyframes, SteamVR settling) are not counted.
        for _ in range(WARMUP_S):
            self.assertIsNone(limit.observe('client_throttled'))
        self.assertEqual(limit.snapshot()['streaming_s'], 0)
        events = [limit.observe('client_throttled') for _ in range(7)]
        self.assertEqual(events, [None] * 7)
        self.assertFalse(limit.snapshot()['limited'])
        self.assertEqual(limit.observe('late_or_paired'), None)
        self.assertEqual(limit.observe('client_throttled'), 'limited')
        snapshot = limit.snapshot()
        self.assertEqual((snapshot['limited'], snapshot['recent_throttled_s'], snapshot['episodes']), (True, 8, 1))
        self.assertEqual(snapshot['share'], round(8 / 9, 3))

    def test_a_network_gap_or_lost_tracking_is_not_the_graphics(self):
        from launcher.graphics_limit import GraphicsLimit, WARMUP_S, frames_arriving
        limit = GraphicsLimit()
        for _ in range(WARMUP_S):
            limit.observe('full')
        for _ in range(20):
            self.assertIsNone(limit.observe('client_throttled', frames_arriving=False))
            self.assertIsNone(limit.observe('client_throttled', tracked=False))
        snapshot = limit.snapshot()
        self.assertEqual((snapshot['streaming_s'], snapshot['throttled_s'], snapshot['limited']), (40, 0, False))
        self.assertTrue(frames_arriving({'packet_fps': '74.000', 'refresh_hz': '75.000'}))
        self.assertTrue(frames_arriving({'packet_fps': '64.000', 'refresh_hz': '75.000'}))
        self.assertFalse(frames_arriving({'packet_fps': '50.000', 'refresh_hz': '75.000'}))
        self.assertFalse(frames_arriving({'packet_fps': '74.000'}))
        self.assertFalse(frames_arriving({'packet_fps': 'x', 'refresh_hz': '75'}))
        self.assertFalse(frames_arriving({}))

    def test_a_limit_clears_only_after_a_whole_clean_window(self):
        from launcher.graphics_limit import GraphicsLimit, WARMUP_S
        limit = GraphicsLimit(window=10, limit=3)
        for _ in range(WARMUP_S):
            limit.observe('full')
        for _ in range(3):
            limit.observe('client_throttled')
        events = []
        for index in range(20):  # alternating keeps it limited
            events.append(limit.observe('client_throttled' if index % 4 == 0 else 'full'))
        self.assertNotIn('recovered', events)
        events = [limit.observe('full') for _ in range(10)]
        # The last throttled second (alternation index 16) leaves the 10 s window with the 7th clean one.
        self.assertEqual(events.index('recovered'), 6)
        self.assertEqual(events.count('recovered'), 1)
        self.assertFalse(limit.snapshot()['limited'])
        self.assertEqual(limit.snapshot()['episodes'], 1, 'history is kept for the page')
        limit.reset()
        self.assertEqual(limit.snapshot()['episodes'], 0)

    def test_advice_follows_the_stream_size_and_reprojection(self):
        from launcher.graphics_limit import advice
        quiet = advice({'episodes': 0})
        self.assertEqual((quiet['message'], quiet['actions']), ('', []))
        limited = {'limited': True, 'recent_throttled_s': 14, 'window_s': 30, 'episodes': 1, 'share': .4}
        high = advice(limited, 2560, 75.0, reprojection_repairable=True, reaim_paused=True)
        self.assertIn('14 of the last 30 seconds', high['message'])
        self.assertIn('Choose Standard (2016 x 2016 per eye) for a steady 75 fps', high['message'])
        self.assertIn('re-aim is paused', high['message'])
        self.assertIn('Background reprojection is off', high['message'])
        self.assertEqual(high['actions'], ['use-standard', 'repair-reprojection'])
        self.assertEqual(high['short'], 'PC graphics at their limit · re-aim paused · Standard 2016 for 75 fps')
        standard = advice(limited, 2016, 75.0)
        self.assertEqual(standard['actions'], [])
        self.assertNotIn('Choose Standard', standard['message'])
        self.assertIn('overheating', standard['message'])
        earlier = advice(dict(limited, limited=False), 2560, None)
        self.assertEqual(earlier['state'], 'recovered')
        self.assertIn('40% of the time', earlier['message'])
        self.assertIn('steady full fps', earlier['message'])
        saved = advice(limited, 2560, 75.0, chosen=True)  # Standard already saved during the session
        self.assertEqual(saved['actions'], [])
        self.assertIn('Standard (2016 x 2016 per eye) is saved and gives a steady 75 fps from the next Start.',
                      saved['message'])
        self.assertNotIn('overheating', saved['message'])
        self.assertEqual(saved['short'], 'PC graphics at their limit · Standard 2016 next Start')
        # The headset's own 'sharp' preset: High on an Index, offered only when the stream is wider.
        index = advice(limited, 2784, 120.0, smaller={'label': 'High', 'width': 2208, 'height': 2448})
        self.assertIn('Choose High (2208 x 2448 per eye) for a steady 120 fps', index['message'])
        self.assertEqual(index['smaller'], {'label': 'High', 'width': 2208, 'height': 2448})
        self.assertEqual(index['short'], 'PC graphics at their limit · High 2208 for 120 fps')
        self.assertIsNone(advice(limited, 2208, 120.0, smaller={'label': 'High', 'width': 2208, 'height': 2448})['smaller'])


class GraphicsLimitAppTests(unittest.TestCase):
    def setUp(self):
        from test_session_lifecycle import SessionLifecycleTests
        self.case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        from unittest.mock import Mock
        self.app = self.case.app()
        self.app.running = lambda: True
        self.app.native_command = Mock(return_value=True)
        self.app.session_link.snapshot.return_value = {}
        self.app.async_reprojection.status.return_value = {'state': 'disabled', 'can_repair': True}
        from headset_fixture import HEADSET
        self.app.client_runtime.headset = dict(HEADSET, model='Bigscreen Beyond 2e', manufacturer='Bigscreen',
                                               width=3560, height=3560, refresh_hz=75)
        self.app.log('VIDEO {"width": 2560, "height": 2560, "codec": "Hevc", "refresh_hz": 75}')
        from launcher.graphics_limit import WARMUP_S
        self.stream('full', WARMUP_S)

    def stream(self, cadence, seconds, packet_fps=75.0, tracked=1):
        for _ in range(seconds):
            self.app.log('METRICS state=streaming decoded=100 tracked=' + str(tracked) + ' input_focus=1')
            self.app.log('TIMING refresh_hz=75.000 unique_fps=37.5 packet_fps=' + format(packet_fps, '.3f')
                         + ' cadence=' + cadence)

    def test_the_headsets_own_smaller_size_is_offered(self):
        app = self.app
        # 1.0.0.13: a Beyond streams the mode it is in: nothing to choose here. While it streams its 75 Hz mode's
        # 2560, the advice names the lighter 90 Hz mode, which Bigscreen's Beyond Utility switches.
        from launcher.headset import BEYOND_LIGHTER_MODE
        self.assertEqual(app.sharp_preset(), {'id': None, 'hint': BEYOND_LIGHTER_MODE})
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        status = app.graphics_status()
        self.assertIsNone(status['smaller'])
        self.assertNotIn('use-standard', status['actions'])
        self.assertIn("Bigscreen's Beyond Utility on a Windows PC", status['message'])
        from headset_fixture import HEADSET
        app.client_runtime.headset = dict(HEADSET)  # a Valve Index at 90 Hz: its next lower rate
        self.assertEqual(app.sharp_preset(), {'id': 'index-80', 'label': '80 Hz', 'width': 2016, 'height': 2240,
                                              'refresh_hz': 80.0})
        app.client_runtime.headset = dict(HEADSET, model='Generic HMD', manufacturer='Example')  # its 'sharp': High
        self.assertEqual(app.sharp_preset()['label'], 'High')
        app.client_runtime.headset = None
        app.settings.pop('cached_headset', None)
        self.assertIsNone(app.sharp_preset())

    def test_a_wifi_gap_or_lost_tracking_does_not_blame_the_graphics(self):
        app = self.app
        self.stream('client_throttled', 20, packet_fps=40.0)
        self.stream('client_throttled', 20, tracked=0)
        app.check_graphics_limit()
        self.assertFalse(app.graphics_limit.snapshot()['limited'])
        app.native_command.assert_not_called()
        app.metrics = {}
        app.last_metrics = 0  # no fresh METRICS: tracking unknown, not counted
        for _ in range(10):
            app.log('TIMING refresh_hz=75.000 unique_fps=37.5 packet_fps=75.000 cadence=client_throttled')
        self.assertEqual(app.graphics_limit.snapshot()['throttled_s'], 0)

    def test_the_headset_line_shows_once_per_session(self):
        app = self.app
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        self.assertGreater(app.graphics_notice_until, time.monotonic())
        app.graphics_notice_until = 0
        self.stream('full', 30)
        app.check_graphics_limit()
        self.assertFalse(app.graphics_limit.snapshot()['limited'])
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        self.assertEqual(app.graphics_limit.snapshot()['episodes'], 2)
        self.assertEqual(app.graphics_notice_until, 0, 'the second episode is logged and on the pages only')
        self.assertEqual(sum(line.startswith('GRAPHICS This PC') for line in app.lines), 2)

    def test_limited_pauses_reaim_once_and_tells_the_headset(self):
        app = self.app
        self.stream('full', 5)
        app.check_graphics_limit()
        app.native_command.assert_not_called()
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        app.native_command.assert_called_once_with('reaim 0')
        self.assertTrue(app.reaim_paused)
        self.assertTrue(any(line.startswith('GRAPHICS ') and 're-aim is paused' in line for line in app.lines))
        status = app.graphics_status()
        self.assertEqual((status['state'], status['stream_width']), ('limited', 2560))
        self.assertEqual(status['actions'], ['repair-reprojection'])  # 1.0.0.13: a Beyond's size follows its mode
        self.assertEqual(app.settings['reaim'], True, 'the pause is for this session only')
        app.update_headset_status()
        app.native_command.assert_called_with('hud PC graphics at their limit|re-aim paused')
        app.check_graphics_limit()
        self.assertEqual(app.native_command.call_count, 2, 'nothing more until the state changes')
        app.graphics_notice_until = 0
        app.update_headset_status()
        app.native_command.assert_called_with('hud')
        self.assertEqual(app.hud_text, '')
        self.assertIn('graphics', app.status())

    def test_the_fused_pass_keeps_reaim(self):
        app = self.app
        for _ in range(8):
            app.log('METRICS state=streaming decoded=100 tracked=1 input_focus=1')
            app.log('TIMING refresh_hz=75.000 unique_fps=37.5 packet_fps=75.000 frame_pass=fused cadence=client_throttled')
        app.check_graphics_limit()
        self.assertTrue(app.graphics_limit.snapshot()['limited'])
        self.assertNotIn('reaim 0', [call.args[0] for call in app.native_command.call_args_list])
        self.assertFalse(app.reaim_paused, 'one pass runs either way: pausing re-aim would save nothing')

    def test_reaim_off_by_choice_is_not_touched(self):
        app = self.app
        app.settings['reaim'] = False
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        self.assertNotIn('reaim 0', [call.args[0] for call in app.native_command.call_args_list])
        self.assertFalse(app.reaim_paused)

    def test_the_users_reaim_choice_ends_the_pause_and_a_new_client_resets(self):
        app = self.app
        self.stream('client_throttled', 8)
        app.check_graphics_limit()
        app.action({'action': 'set-reaim', 'enabled': True})
        self.assertFalse(app.reaim_paused)
        app.native_command.assert_called_with('reaim 1')
        app.process = None
        app.running = lambda: False
        from unittest.mock import patch, Mock
        with patch('launcher.app.subprocess.Popen', return_value=Mock(stdout=[])), \
                patch('launcher.app.threading.Thread'):
            app.launch(['VROSTBridgeClient'])
        self.assertFalse(app.reaim_paused)
        app.process = None
        self.assertEqual(app.graphics_limit.snapshot()['episodes'], 0)

    def test_use_standard_is_saved_during_a_session(self):
        app = self.app
        app.settings.update(codec='Auto', resolution='automatic')
        # 1.0.0.13: nothing to save on a Beyond, whose mode Bigscreen's Beyond Utility switches.
        with self.assertRaisesRegex(ValueError, 'Beyond Utility'):
            app.action({'action': 'use-standard'})
        self.assertEqual(app.settings['resolution'], 'automatic')
        from headset_fixture import HEADSET
        app.client_runtime.headset = dict(HEADSET, refresh_hz=120.0)  # an Index at 120 Hz: 90 Hz is lighter
        app.action({'action': 'use-standard'})
        self.assertEqual(app.settings['resolution'], 'index-90')
        import json
        self.assertEqual(json.loads(app.settings_path.read_text())['resolution'], 'index-90')
        self.assertTrue(any(line.startswith('Refresh rate set to 90 Hz') and 'applies at the next Start' in line
                            for line in app.lines))


def edid(maker='VLV', product=0x91a8, extensions=()):
    """An EDID base block plus extension blocks, each with its checksum (as tests/headset_edid_tests.cpp)."""
    code = sum((ord(letter) - 64) << shift for letter, shift in zip(maker, (10, 5, 0)))
    base = bytearray(128)
    base[0:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'
    base[8:10] = code.to_bytes(2, 'big')
    base[10:12] = product.to_bytes(2, 'little')
    base[18:20] = b'\x01\x04'
    base[126] = len(extensions)
    blocks = [base] + [bytearray(block) for block in extensions]
    for block in blocks:
        block[127] = -sum(block[:127]) % 256
    return bytes(b''.join(blocks))


def cta_microsoft(version, desktop, payload=21):
    block = bytearray(128)
    block[0], block[1] = 0x02, 3
    block[4] = 3 << 5 | payload
    block[5:8] = b'\x5c\x12\xca'
    block[8] = version
    block[9] = 0x40 if desktop else 0
    block[2] = 4 + 1 + payload
    return block


def displayid(version, use, valid=True, with_block=True):
    """A DisplayID section with one 4-byte data block and a valid checksum (as tests/headset_edid_tests.cpp)."""
    block = bytearray(128)
    size = 4 if with_block else 0
    block[0:5] = bytes([0x70, version, size, use, 0])
    if with_block:
        block[5:9] = b'\x7e\x00\x01\x00'
    block[5 + size] = (-sum(block[1:5 + size]) + (0 if valid else 1)) % 256
    return block


class HeadsetEdidTests(unittest.TestCase):
    """The same cases as tests/headset_edid_tests.cpp (the lease helper's check)."""

    def test_models(self):
        from launcher.game_mode import identity
        cases = [
            (edid('VLV', 0x91a8), 'Valve Index'), (edid('VLV', 0x91b5), 'Valve Index'), (edid('VLV', 0x91c0), None),
            (edid('BIG', 0x1234), 'Bigscreen Beyond'),
            (edid('BIG', 0x4a21, [cta_microsoft(3, False)]), 'Bigscreen Beyond'),
            (edid('BIG', 0x4a21), None),
            (edid('ABC', 0x0001, [cta_microsoft(1, False)]), 'VR headset'),
            (edid('ABC', 0x0001, [cta_microsoft(2, True)]), 'VR headset'),
            (edid('DEL', 0x4321, [cta_microsoft(3, True)]), None),
            (edid('DEL', 0x4321, [cta_microsoft(3, False, 20)]), None),
            (edid('ABC', 0x0002, [displayid(0x20, 7)]), 'VR headset'),
            (edid('ABC', 0x0002, [displayid(0x20, 8)]), 'VR headset'),
            (edid('ABC', 0x0002, [displayid(0x12, 7)]), None),
            (edid('ABC', 0x0002, [displayid(0x20, 4)]), None),
            (edid('BIG', 0x4a21, [cta_microsoft(3, True), displayid(0x20, 7)]), 'Bigscreen Beyond'),
            (edid('ABC', 0x0002, [displayid(0x20, 7, valid=False)]), None),
            (edid('ABC', 0x0002, [displayid(0x20, 7, with_block=False)]), None),
            (edid('ABC', 0x0002, [displayid(0x20, 4), displayid(0x20, 7)]), None),
            (edid('ABC', 0x0002, [displayid(0x20, 7, valid=False), displayid(0x20, 7)]), 'VR headset'),
        ]
        old = cta_microsoft(1, False)
        old[1] = 2
        cases.append((edid('ABC', 0x0001, [old]), None))
        for value, expected in cases:
            self.assertEqual(identity(value), expected)
        bad = bytearray(edid('VLV', 0x91a8))
        bad[20] ^= 1
        self.assertIsNone(identity(bytes(bad)))
        self.assertIsNone(identity(edid('BIG', 0x4a21, [cta_microsoft(3, False)])[:128]), 'missing extension')
        overrun = cta_microsoft(3, False)
        overrun[2] = 10
        self.assertIsNone(identity(edid('BIG', 0x4a21, [overrun])))
        self.assertIsNone(identity(b''))

    def test_native_check_has_the_same_rules(self):
        text = (ROOT / 'core/headset_edid.hpp').read_text()
        lease = (ROOT / 'client/platform/linux/lease.cpp').read_text()
        self.assertIn('vrost::headsetModel(edid.data(),edid.size())', lease)
        self.assertNotIn("product==0x1234)))throw", lease, 'the RC4.2 two-product check is gone')
        for rule in ('ext[at + 1] == 0x5c && ext[at + 2] == 0x12 && ext[at + 3] == 0xca', 'length == 21',
                     'version == 1 || version == 2 || (version == 3 && !desktop)', 'ext[3] == 7 || ext[3] == 8'):
            self.assertIn(rule, text)


class GameModeStartTests(unittest.TestCase):
    """Game Mode: a recognised Beyond 2e is reserved, and the pages say why when it is not."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / 'drm'
        runtime = self.root / 'run'
        runtime.mkdir(mode=0o700)
        from unittest import mock
        patcher = mock.patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'config'),
                                                 'XDG_RUNTIME_DIR': str(runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def output(self, name, value, status='connected', connector_id=88):
        path = self.sysfs / name
        path.mkdir(parents=True)
        (path / 'status').write_text(status + '\n')
        (path / 'edid').write_bytes(value)
        (path / 'connector_id').write_text(str(connector_id) + '\n')

    def run_wrapper(self):
        from unittest import mock
        from launcher.game_mode import wrapper, WRAPPER_RECORD, runtime_dir
        calls = []
        with mock.patch('launcher.game_mode.process_identity', return_value={'started': 123, 'path': '/usr/bin/gamescope'}):
            wrapper(['--prefer-output', '*,eDP-1', '--steam'], sysfs=self.sysfs,
                    execve=lambda path, args, env: calls.append((args, env)))
        import json
        return calls[-1], json.loads((runtime_dir() / WRAPPER_RECORD).read_text())

    def test_a_beyond_2e_is_found_and_reserved(self):
        from launcher.game_mode import connectors
        self.output('card1-eDP-1', edid('BOE', 0x0bca))
        self.output('card1-DP-1', edid('BIG', 0x4a21, [cta_microsoft(3, False)]))
        rows = connectors(self.sysfs)
        self.assertEqual([(row['name'], row['model']) for row in rows], [('DP-1', 'Bigscreen Beyond')])
        (args, env), record = self.run_wrapper()
        self.assertEqual(args[:3], ['/usr/bin/gamescope', '--lease-connector', 'DP-1'])
        self.assertIn('*,eDP-1,DP-1', args, 'and ranked after the built-in screen')
        self.assertTrue(env['GAMESCOPE_LEASE_SOCK'].endswith('gamescope-lease.sock'))
        self.assertEqual((record['leased'], record['reason']), ('DP-1', ''))
        self.assertIn('Microsoft HMD block', record['outputs']['DP-1'])

    def test_without_a_headset_the_record_says_why(self):
        self.output('card1-eDP-1', edid('BOE', 0x0bca))
        self.output('card1-DP-1', edid('BIG', 0x4a21))  # not marked head-mounted: not reserved
        (args, env), record = self.run_wrapper()
        self.assertNotIn('--lease-connector', args)
        self.assertNotIn('GAMESCOPE_LEASE_SOCK', env)
        self.assertEqual((record['leased'], record['code']), ('', 'unrecognised'))
        self.assertIn('DP-1: BIG 4A21', record['reason'], 'names the display that is not marked as a headset')
        self.assertEqual(record['outputs']['DP-1'], 'BIG 4A21')

    def test_only_the_built_in_screen(self):
        self.output('card1-eDP-1', edid('BOE', 0x0bca))
        (args, env), record = self.run_wrapper()
        self.assertEqual((record['code'], record['reason']), ('none', 'no supported headset was plugged in and powered'))

    def test_the_kernels_non_desktop_flag_is_enough(self):
        from launcher.game_mode import connectors
        self.output('card1-DP-1', edid('BIG', 0x4a21))
        kernel = {('/dev/dri/card1', 'DP-1'): {'card': '/dev/dri/card1', 'name': 'DP-1', 'non_desktop': 1}}
        self.assertEqual([row['model'] for row in connectors(self.sysfs, kernel)], ['Bigscreen Beyond'])
        self.assertEqual(connectors(self.sysfs, {('/dev/dri/card0', 'DP-1'): {'non_desktop': 1}}), [], 'other card')
        from unittest import mock
        with mock.patch('launcher.game_mode.process_identity', return_value={'started': 1, 'path': '/usr/bin/gamescope'}):
            calls = []
            from launcher.game_mode import wrapper
            wrapper(['--steam'], sysfs=self.sysfs, execve=lambda path, args, env: calls.append(args), kernel=kernel)
        self.assertEqual(calls[-1][:3], ['/usr/bin/gamescope', '--lease-connector', 'DP-1'])

    def test_the_kernel_is_asked_only_about_unrecognised_displays(self):
        from unittest import mock
        from launcher.game_mode import GameMode, plain_environment
        self.output('card1-DP-1', edid('BIG', 0x4a21, [cta_microsoft(3, False)]))
        setup = GameMode.__new__(GameMode)
        setup.sysfs, setup._kernel, setup._kernel_refreshing = self.sysfs, (0.0, {}), False
        setup.lease_helper = lambda: Path('/bin/true')
        with mock.patch('launcher.game_mode.kernel_connectors', return_value={'x': 1}) as probe:
            self.assertEqual(setup.kernel(), {}, 'a display recognised from its EDID needs no card opened')
            probe.assert_not_called()
            (self.sysfs / 'card1-DP-1' / 'edid').write_bytes(edid('BIG', 0x4a21))
            self.assertEqual(setup.kernel(fresh=True), {'x': 1})
            probe.assert_called_once()
        with mock.patch.dict('os.environ', {'LD_LIBRARY_PATH': '/opt/VROSTBridge/_internal', 'LD_LIBRARY_PATH_ORIG': '/usr/lib/steam',
                                            'GAMESCOPE_LEASE_SOCK': '/run/x'}):
            env = plain_environment()
        self.assertEqual(env['LD_LIBRARY_PATH'], '/usr/lib/steam')
        self.assertNotIn('GAMESCOPE_LEASE_SOCK', env)
        with mock.patch.dict('os.environ', {'LD_LIBRARY_PATH': '/opt/VROSTBridge/_internal'}, clear=False):
            import os
            os.environ.pop('LD_LIBRARY_PATH_ORIG', None)
            self.assertNotIn('LD_LIBRARY_PATH', plain_environment())

    def test_kernel_listing_is_parsed_defensively(self):
        from launcher.game_mode import kernel_connectors
        lines = '\n'.join([json.dumps({'card': '/dev/dri/card1', 'name': 'DP-1', 'connector_id': 88, 'non_desktop': 1}),
                           'not json', json.dumps({'card': '/etc/passwd', 'name': 'DP-2'}),
                           json.dumps({'card': '/dev/dri/card1', 'name': 'DP-3; rm -rf /'})])
        rows = kernel_connectors('helper', runner=lambda *a, **k: completed(0, lines))
        self.assertEqual(list(rows), [('/dev/dri/card1', 'DP-1')])
        self.assertEqual(kernel_connectors('helper', runner=Mock(side_effect=OSError('missing'))), {})

    def test_the_wrapper_always_starts_gamescope(self):
        from unittest import mock
        from launcher import game_mode
        calls = []
        with mock.patch('launcher.game_mode.wrapper_plan', side_effect=RuntimeError('boom')), \
                mock.patch('sys.stderr', new=mock.Mock(write=mock.Mock(side_effect=BrokenPipeError()))):
            game_mode.wrapper(['--steam'], sysfs=self.sysfs, execve=lambda path, args, env: calls.append((args, env)))
        self.assertEqual(calls[-1][0], ['/usr/bin/gamescope', '--steam'])
        self.assertNotIn('GAMESCOPE_LEASE_SOCK', calls[-1][1])
        import launcher.bootstrap as bootstrap
        execs = []
        with mock.patch.dict('sys.modules', {'launcher.game_mode': None}), \
                mock.patch('launcher.bootstrap.os.execve', side_effect=lambda path, args, env: execs.append(args)):
            bootstrap.gamescope_wrapper(['--steam'])
        self.assertEqual(execs, [['/usr/bin/gamescope', '--steam']], 'even when the module cannot be imported')

    def test_start_explains_instead_of_an_os_error(self):
        import threading
        from unittest import mock
        from launcher.game_mode import GameMode, LeaseRuntime
        setup = GameMode(self.root / 'app', sysfs=self.sysfs)
        lease = LeaseRuntime(self.root / 'app', setup, log=lambda line: None, on_failure=lambda *a: None)
        with mock.patch('launcher.game_mode.in_game_mode', return_value=True):
            with self.assertRaises(ValueError) as caught:
                lease.prepare(threading.Event())
        message = str(caught.exception)
        self.assertNotIn('Errno', message)
        self.assertIn("started without VROSTBridge's display reservation", message)

    def test_status_reasons(self):
        from launcher.game_mode import GameMode, WRAPPER_RECORD, runtime_dir
        import json
        setup = GameMode(self.root / 'app', sysfs=self.sysfs)
        rows = [{'model': 'Bigscreen Beyond', 'name': 'DP-1'}]
        self.assertIn('reserves the Bigscreen Beyond on DP-1', setup.lease_problem(ValueError('x'), rows, False))
        self.assertIn('Connect the headset', setup.lease_problem(ValueError('x'), [], False))
        message = setup.lease_problem(ValueError('x'), [], True)
        self.assertIn("started without VROSTBridge's display reservation", message)
        self.assertIn('Switch to Desktop and back to Game Mode', message)
        (runtime_dir() / WRAPPER_RECORD).write_text(json.dumps(
            {'version': 1, 'time': 1.0, 'leased': '', 'reason': 'no supported headset was plugged in and powered'}))
        self.assertIn('Game Mode started without the headset (no supported headset was plugged in and powered).',
                      setup.lease_problem(ValueError('x'), [], True))
        (runtime_dir() / WRAPPER_RECORD).write_text(json.dumps({'version': 1, 'time': 1.0, 'leased': 'DP-1', 'reason': ''}))
        self.assertTrue(setup.lease_problem(ValueError('The previous display lease is stale. Re-enter Game Mode.'), rows,
                                            True).startswith('The previous display lease is stale'))



class GameModeScreenTests(unittest.TestCase):
    def view(self, state=None):
        import threading
        from unittest.mock import Mock
        from launcher.game_ui import View
        view = View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.action = Mock()
        view.state = state or {'settings': {}}
        return view

    def test_b_never_exits_from_the_main_page(self):
        view = self.view()
        view.back()
        self.assertEqual(view.page, 'main')
        self.assertFalse(view.stopped.is_set())
        for page, parent in (('stream', 'main'), ('motion', 'main'), ('network', 'main'), ('pc', 'main'),
                             ('reports', 'main'), ('about', 'main'), ('host', 'network'), ('exit', 'main')):
            view.go(page)
            view.back()
            self.assertEqual(view.page, parent, page)
        view.choose('exit')
        self.assertTrue(view.stopped.is_set(), 'Exit VROSTBridge still closes the app')
        text = (ROOT / 'launcher/game_ui.py').read_text()
        self.assertNotIn("self.choose('exit' if self.page == 'main' else 'back')", text)
        self.assertIn("elif key == 27:\n                            self.back()", text, 'Escape')
        # 1.0.0.4: one press reported by two controllers goes back once (a line between the two).
        self.assertRegex(text, r"elif button == 1:  # B: up one level, never Exit \(RC4\.2 exited from the main page\)\n"
                               r"(?:.*\n){0,3} +self\.back\(\)", 'B')

    def test_settings_rows_change_their_settings(self):
        view = self.view({'settings': {'pacing': True, 'reaim': True, 'encoder': 'speed', 'keep_warm_minutes': 0},
                          'session_link': {'network': {'interface': '', 'adapters': [{'interface': 'wlan0'}]}}})
        view.choose('toggle:pacing')
        view.action.assert_called_with('client-preferences', pacing=False)
        view.choose('toggle:intra_refresh')
        view.action.assert_called_with('client-preferences', intra_refresh=True)
        view.choose('reaim')
        view.action.assert_called_with('set-reaim', enabled=False)
        view.choose('encoder')
        view.action.assert_called_with('client-preferences', encoder='balanced')
        view.choose('keep-warm')
        view.action.assert_called_with('client-preferences', keep_warm_minutes=5)
        view.choose('adapter')
        view.action.assert_called_with('select-client-network', interface='wlan0')
        view.choose('align-yes')
        view.action.assert_called_with('align-answer', jumped=True)
        calls = view.action.call_count
        view.choose('info:connection')
        view.choose('toggle:not_a_setting')
        self.assertEqual(view.action.call_count, calls)
        view.choose('network')
        self.assertEqual(view.page, 'network')

    def test_every_desktop_setting_has_a_game_mode_row(self):
        import re
        from launcher import game_ui
        from launcher.app import Application, KEEP_WARM_CHOICES
        from launcher.session_link import ENCODER_PRESETS
        self.assertEqual(game_ui.KEEP_WARM, KEEP_WARM_CHOICES)
        self.assertEqual(game_ui.ENCODERS, ENCODER_PRESETS)
        handled = set(game_ui.TOGGLES) | set(game_ui.LIVE_TOGGLES) | {'ten_bit', 'auto_bitrate', 'reaim'}
        self.assertEqual(set(Application.CLIENT_OPTIONS), handled)
        self.assertEqual(set(game_ui.LIVE_TOGGLES), set(Application.LIVE_OPTIONS))  # 1.0.0.8: applied at once
        source = (ROOT / 'launcher/game_ui.py').read_text()
        for key in game_ui.TOGGLES + game_ui.LIVE_TOGGLES:  # RC4.5: Eye tracking ('gaze') has its own page row
            self.assertTrue("self.toggle('" + key + "'" in source or "'toggle:" + key + "'" in source, key)
        page = (ROOT / 'launcher/desktop.html').read_text()
        actions = set(re.findall(r"act\('([a-z-]+)'", page))
        covered = {'start', 'stop', 'host-menu', 'network-test', 'wake-host', 'repair-async', 'wifi-powersave-off',
                   'align-check', 'align-answer', 'set-reaim', 'send-report', 'sync-playspace', 'restore-client-runtime',
                   'use-standard', 'quit', 'select-client-network', 'set-sharpening',
                   'gpu-auto',  # 1.0.0.4: This PC's row while a fixed GPU clock holds the processor back
                   # RC4.5: the Eye tracking page
                   'eye-tracking', 'eye-setup', 'eye-setup-cancel', 'eye-open', 'eye-close', 'eye-camera-access',
                   'eye-remove',
                   'eye-reach-reset',  # 1.0.0.6: the Eye tracking page's Gaze reach row
                   # 1.0.0.8: the live switches, base stations, recommended settings, centring, previous calibration
                   'client-option', 'base-stations', 'recommended-settings', 'gaze-centre-reset', 'eye-calibration'}
        host_or_desktop_only = {'host-power', 'qos', 'set-startup', 'configure', 'firewall', 'unregister', 'build-host',
                                'trust-client', 'forget-client', 'add-client', 'allow-client-network', 'camera-start',
                                'camera-stop', 'check', 'steam-add', 'steam-remove', 'microphone-setup', 'microphone-check',
                                'eye-show',  # RC4.6: Baballonia's window is a Desktop Mode window
                                'windows-startup'}  # 1.0.0.7: the Windows client's sign-in startup (no Game Mode there)
        self.assertEqual(actions - covered - host_or_desktop_only, set(), 'a client page action without a Game Mode row')

    def test_session_card_says_why_start_cannot_reserve_the_headset(self):
        view = self.view({'settings': {}, 'running': False, 'state': 'stopped',
                          'session_link': {'message': 'Ready to start a session.'},
                          'game_mode': {'supported': False, 'reason': 'Game Mode started without the headset (x).'}})
        self.assertEqual(view.session_detail(), 'Game Mode started without the headset (x).')
        view.state.update(running=True, state='streaming', game_mode={'supported': True},
                          graphics={'state': 'limited', 'actions': ['use-standard'],
                                    'smaller': {'label': 'Standard', 'width': 2016, 'height': 2016}},
                          settings={'resolution': 'beyond-balanced'})
        self.assertIn("graphics can't keep up", view.session_detail())
        self.assertIn('Use Standard', view.session_detail())
        view.state['graphics']['smaller'] = {'label': 'High', 'width': 2208, 'height': 2448}  # a Valve Index
        self.assertIn('Use High gives', view.session_detail())
        view.state['settings']['resolution'] = 'sharp'
        self.assertNotIn('Use High', view.session_detail())
        view.state['startup_error'] = 'Timed out acquiring the Game Mode display.'
        self.assertEqual(view.session_detail(), 'Timed out acquiring the Game Mode display.')

    def test_multi_link_chip(self):
        view = self.view({'connection': {'kind': 'wifi', 'details': {'bands': ['2.4 GHz', '5 GHz', '6 GHz'], 'multi_link': 3,
                                                                    'band': '6 GHz', 'signal_dbm': -26}}})
        self.assertEqual(view.connection_chip(), 'Wi-Fi 7 · 2.4+5+6 GHz · -26 dBm')
        view.state['connection']['details'] = {'band': '5 GHz', 'signal_dbm': -50, 'multi_link': 0}
        self.assertEqual(view.connection_chip(), 'Wi-Fi 5 GHz · -50 dBm')

    def drawable(self, state):
        view = self.view(state)
        view.k, view.ox, view.oy = 1.0, 0.0, 0.0
        view.paint, view.s, view.renderer = Mock(), Mock(), None
        view.ink = Mock(fit=lambda font, value, size, width: value, width=lambda *a: 10.0)
        view.paint.text.return_value = 10.0
        view.paint.paragraph.return_value = 0.0
        return view

    def test_the_focused_setting_stays_focused_when_rows_come_and_go(self):
        state = {'settings': {'resolution': 'beyond-balanced'}, 'running': False,
                 'graphics': {'state': 'limited', 'actions': ['use-standard'], 'message': 'limit',
                              'smaller': {'label': 'Standard', 'width': 2016, 'height': 2016}}}
        view = self.drawable(state)
        view.go('stream')
        view.buttons = []
        view.page_stream()
        self.assertEqual([b[0] for b in view.buttons][:3], ['use-standard', 'resolution', 'codec'])
        self.assertEqual(view.buttons[0][1], 'Use Standard · 2016 per eye')
        view.move(0, 1)  # down to Stream resolution
        self.assertEqual((view.focus, view.focus_name), (1, 'resolution'))
        state['settings']['resolution'] = 'sharp'  # the Use Standard row goes away
        view.buttons = []
        view.page_stream()
        self.assertEqual(view.buttons[view.focus][0], 'resolution', 'A must keep changing the resolution, not the codec')

    def test_rows_scrolled_out_of_view_ignore_the_pointer(self):
        view = self.view()
        view.buttons = [('a', 'A', (0, 0, 100, 50), True, True), ('b', 'B', (0, 600, 100, 50), True, False),
                        ('c', 'C', (0, 60, 100, 50), True)]
        self.assertEqual(view.hit(10, 10), 0)
        self.assertIsNone(view.hit(10, 610), 'clipped row')
        self.assertEqual(view.hit(10, 70), 2, 'four-element buttons stay clickable')

    def test_pages_render(self):
        import os
        import shutil
        import subprocess
        import sys
        libvrosttext = ROOT / 'dist/client/libvrosttext.so'
        sdl = [p for p in ('/usr/lib/x86_64-linux-gnu/libSDL2-2.0.so.0', '/usr/lib64/libSDL2-2.0.so.0') if Path(p).exists()]
        if not libvrosttext.is_file() or not sdl:
            self.skipTest('needs SDL2 and the built libvrosttext.so')
        state = {'role': 'client', 'running': True, 'state': 'streaming', 'settings': {'resolution': 'beyond-balanced'},
                 'graphics': {'state': 'limited', 'message': 'This PC is at its graphics limit.', 'actions': ['use-standard'],
                              'smaller': {'label': 'Standard', 'width': 2016, 'height': 2016}},
                 'connection': {'kind': 'wifi', 'summary': 'Wi-Fi', 'details': {'bands': ['6 GHz'], 'multi_link': 2}},
                 'alignment_check': {'state': 'ask'},
                 'system': {'async_reprojection': {'state': 'disabled'},
                            'foveation': {'preset': 'eye-tracked', 'panel': {
                                'show': True, 'region_percent': 40, 'edge_ratio': 4.0, 'encoded_eye': [1408, 1408],
                                'stream_eye': [2560, 2560], 'saving_percent': 70, 'gaze_share': .98, 'gaze_rate': 120.0,
                                'frame_pass': 'fused', 'sharpening': 'medium'}}}}
        live = {'live': True, 'region': [.4, .4], 'edge': [4, 4], 'left': [.62, .44], 'right': [.42, .44],
                'gaze_left': [.64, .42], 'gaze_right': [.44, .42], 'encoded': [1408, 1408], 'eye': [2560, 2560],
                'gaze_age_ms': 5}
        script = ("import json,sys,threading;sys.path.insert(0,%r);from launcher import game_ui;"
                  "game_ui.run(%r,lambda:json.loads(%r),lambda b:None,lambda:{},threading.Event(),lambda:json.loads(%r))") % (
                      str(ROOT), str(ROOT), json.dumps(state), json.dumps(live))
        for page in ('main', 'stream', 'motion', 'network', 'pc', 'host', 'reports', 'about', 'exit', 'eyes'):
            with tempfile.TemporaryDirectory() as directory:
                shot = Path(directory) / 'shot.ppm'
                env = dict(os.environ, SDL_VIDEODRIVER='offscreen', SDL_RENDER_DRIVER='software',
                           VROSTBRIDGE_GAME_UI_PAGE=page, VROSTBRIDGE_GAME_UI_SNAPSHOT=str(shot))
                result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, page + ': ' + result.stderr[-800:])
                data = shot.read_bytes()
                self.assertTrue(data.startswith(b'P6\n1280 800\n255\n'), page)
                self.assertGreater(len(set(data[-30000:])), 40, page + ' is not blank')



class HostMicrophoneSetupTests(unittest.TestCase):
    """One-step virtual microphone on the host: VB-CABLE by VB-Audio, downloaded and opened on request."""

    SIGNED = {'status': 'Valid',
              'subject': 'CN=BUREL VINCENT Entrepreneur individuel, O=BUREL VINCENT Entrepreneur individuel, L=Eymet, '
                         'S=Dordogne, C=FR, SERIALNUMBER=423 734 177, OID.2.5.4.15=Private Organization',
              'names': ['CN=BUREL VINCENT Entrepreneur individuel', 'O=BUREL VINCENT Entrepreneur individuel',
                        'STREET=21 avenue des Marguerites', 'L=Eymet', 'S=Dordogne', 'C=FR',
                        'OID.1.3.6.1.4.1.311.60.2.1.3=FR', 'SERIALNUMBER=423 734 177',
                        'OID.2.5.4.15=Private Organization']}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.driver, self.signed = 'absent', dict(self.SIGNED)
        self.devices = {'render': ['Speakers (Realtek)'], 'capture': ['Microphone (Realtek)']}
        self.lines = []
        self.launch_result = completed(0, 'closed 0')
        self.scripts = []

    def runner(self, arguments, timeout):
        script = arguments[-1]
        self.scripts.append((script, timeout))
        if 'Get-AuthenticodeSignature' in script:
            return completed(0, json.dumps(self.signed))
        if 'Get-PnpDevice' in script:
            return completed(0, self.driver + '\n')
        if "$info.Verb = 'runas'" in script:
            if isinstance(self.launch_result, BaseException):
                raise self.launch_result
            return self.launch_result
        return completed(1, '', 'unexpected')

    def setup(self, **kwargs):
        from host.microphone_setup import MicrophoneSetup
        values = dict(log=self.lines.append, version='1.0.0-rc4.3', runner=self.runner, endpoints=lambda: self.devices)
        values.update(kwargs)
        return MicrophoneSetup(self.root / 'probe.exe', self.root / 'microphone', **values)

    @staticmethod
    def package(names=('VBCABLE_Setup_x64.exe', 'vbMmeCable64_win10.inf', 'vbaudio_cable64_win10.sys', 'readme.txt')):
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name in names:
                archive.writestr(name, b'data for ' + name.encode())
        return buffer.getvalue()

    def checked(self, setup, data):
        folder, files = setup.unpack(data)
        return folder, files, setup.verify(folder, files, data)

    def test_states_from_the_devices(self):
        setup = self.setup()
        self.assertEqual(setup.check()['state'], 'missing')
        self.driver = 'installed'
        status = setup.check()
        self.assertEqual(status['state'], 'restart')
        self.assertIn('Restart Windows', status['message'])
        self.devices = {'render': ['CABLE Input (VB-Audio Virtual Cable)'], 'capture': ['CABLE Output (VB-Audio Virtual Cable)']}
        status = setup.check()
        self.assertEqual((status['state'], status['source']), ('ready', 'CABLE Output (VB-Audio Virtual Cable)'))
        self.assertEqual(status['home'], 'https://vb-cable.com')
        broken = self.setup(endpoints=Mock(side_effect=ValueError('no devices')))
        self.assertEqual(broken.check()['state'], 'unknown')

    def test_package_checks(self):
        from host.microphone_setup import SETUP
        setup = self.setup()
        folder, files, how = self.checked(setup, self.package())  # not the pinned release: VB-Audio's signature decides
        self.assertEqual(how, 'signed')
        self.assertTrue((folder / SETUP).is_file())
        self.assertEqual(set(files), {'VBCABLE_Setup_x64.exe', 'vbMmeCable64_win10.inf', 'vbaudio_cable64_win10.sys',
                                      'readme.txt'})
        self.assertTrue(folder.name.startswith('vb-cable-') and len(folder.name) == len('vb-cable-') + 16)
        self.assertTrue(any('newer VB-CABLE package' in line for line in self.lines))
        again, _, _ = self.checked(setup, self.package())
        self.assertNotEqual(again, folder, 'every setup extracts to a new folder')
        self.assertFalse(folder.exists(), 'an interrupted setup\'s folder is removed')
        for signed in ({'status': 'NotSigned', 'subject': '', 'names': []},
                       dict(self.SIGNED, status='HashMismatch'),
                       # The author's name somewhere in the subject is not enough: the exact signer only.
                       dict(self.SIGNED, names=['CN=BUREL VINCENT Entrepreneur individuel Ltd', 'SERIALNUMBER=423 734 177']),
                       dict(self.SIGNED, names=['CN=Someone Else', 'O=BUREL VINCENT Entrepreneur individuel',
                                                'SERIALNUMBER=423 734 177']),
                       dict(self.SIGNED, names=['CN=BUREL VINCENT Entrepreneur individuel', 'SERIALNUMBER=999 999 999']),
                       dict(self.SIGNED, names=['CN=BUREL VINCENT Entrepreneur individuel']),
                       {'status': 'Valid', 'subject': self.SIGNED['subject']}):
            self.signed = signed
            with self.assertRaisesRegex(ValueError, 'could not be verified'):
                self.checked(setup, self.package(('VBCABLE_Setup_x64.exe', 'other.inf')))
        self.signed = dict(self.SIGNED, names=['CN="BUREL VINCENT Entrepreneur individuel"', 'SERIALNUMBER=423 734 177'])
        self.assertEqual(self.checked(setup, self.package())[2], 'signed', 'a quoted value is read as Windows writes it')
        for names in (('../VBCABLE_Setup_x64.exe',), ('dir/VBCABLE_Setup_x64.exe',), ('VBCABLE_Setup_x64.exe', 'run.bat')):
            with self.assertRaisesRegex(ValueError, 'unexpected file'):
                setup.unpack(self.package(names))
        with self.assertRaisesRegex(ValueError, 'no VBCABLE_Setup_x64.exe'):
            setup.unpack(self.package(('readme.txt',)))
        with self.assertRaisesRegex(ValueError, 'not a valid package'):
            setup.unpack(b'not a zip')

    def test_files_changed_after_the_download_are_not_used(self):
        setup = self.setup()
        data = self.package()
        folder, files = setup.unpack(data)
        (folder / 'vbMmeCable64_win10.inf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed after the download'):
            setup.verify(folder, files, data)

    def test_the_setup_program_itself_asks_for_administrator_permission(self):
        from host.microphone_setup import launch_command
        command = launch_command(Path("C:/Users/O'Brien/AppData/Local/VROSTBridge/microphone/vb-cable-1/VBCABLE_Setup_x64.exe"))
        self.assertIn("$info.FileName = 'C:", command)
        self.assertIn('VBCABLE_Setup_x64.exe', command)
        self.assertIn("O''Brien", command)
        self.assertIn("$info.Verb = 'runas'", command)
        self.assertIn('WaitForExit()', command)
        self.assertNotIn('"', command, 'one command-line argument without double quotes')
        for silent in ('Arguments', "-i'", "-h'", 'Hidden', 'WindowStyle', 'powershell.exe', '-File'):
            self.assertNotIn(silent, command, 'never installed silently (VB-CABLE licence), never a script elevated')
        setup = self.setup()
        self.assertEqual(setup.launch(Path('C:/x/VBCABLE_Setup_x64.exe'))['ok'], True)
        self.assertEqual(self.scripts[-1][1], 900)
        self.launch_result = completed(1223, '')
        self.assertEqual(setup.launch(Path('C:/x/VBCABLE_Setup_x64.exe'))['declined'], True)
        self.launch_result = completed(1, '', 'The file is not a valid application')
        self.assertIn('not a valid application', setup.launch(Path('C:/x/VBCABLE_Setup_x64.exe'))['message'])
        self.launch_result = subprocess.TimeoutExpired('powershell', 900)
        self.assertIn('stayed open for 15 minutes', setup.launch(Path('C:/x/VBCABLE_Setup_x64.exe'))['message'])

    def wait(self, setup, states, idle=True):
        deadline = time.monotonic() + 5
        while (setup.status()['state'] not in states or (idle and setup.status()['busy'])) and time.monotonic() < deadline:
            time.sleep(.01)
        return setup.status()

    def response(self, data):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        return Response(data)

    def test_setup_runs_in_the_background_to_ready(self):
        import threading
        release = threading.Event()
        launched = []

        def launcher(setup_path):
            launched.append((setup_path, setup_path.is_file()))
            release.wait(5)
            self.devices = {'render': ['CABLE Input (VB-Audio Virtual Cable)'], 'capture': ['CABLE Output (VB-Audio Virtual Cable)']}
            return {'ok': True, 'declined': False}

        opened = []

        def opener(request, timeout):
            opened.append((request.full_url, request.get_header('User-agent'), timeout))
            return self.response(self.package())
        setup = self.setup(opener=opener, launcher=launcher)
        setup.start()
        with self.assertRaisesRegex(ValueError, 'already running'):
            setup.start()
        installing = self.wait(setup, ('installing',), idle=False)
        self.assertEqual((installing['state'], installing['busy']), ('installing', True))
        self.assertIn('verified publisher: BUREL VINCENT Entrepreneur individuel', installing['message'])
        # A check from the page meanwhile leaves the setup's progress alone.
        self.assertEqual(setup.check()['state'], 'installing')
        release.set()
        status = self.wait(setup, ('ready',))
        self.assertEqual(status['state'], 'ready')
        self.assertEqual(opened[0][0], 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip')
        self.assertIn('VROSTBridge-Host/1.0.0-rc4.3', opened[0][1])
        self.assertEqual(launched[0][0].name, 'VBCABLE_Setup_x64.exe')
        self.assertTrue(launched[0][1])
        self.assertFalse(launched[0][0].parent.exists(), 'the extracted files are deleted afterwards')
        with self.assertRaisesRegex(ValueError, 'already set up'):
            setup.start()

    def test_declined_closed_and_failed_downloads_end_readably(self):
        opener = lambda request, timeout: (_ for _ in ()).throw(OSError('getaddrinfo failed'))
        setup = self.setup(opener=opener)
        setup.start()
        status = self.wait(setup, ('error',))
        self.assertIn('could not be downloaded', status['message'])
        self.assertIn('vb-cable.com', status['message'])
        package = self.package()
        declined = self.setup(opener=lambda request, timeout: self.response(package),
                              launcher=lambda setup_path: {'ok': False, 'declined': True})
        declined.start()
        self.assertIn('Administrator permission was not given', self.wait(declined, ('missing',))['message'])
        closed = self.setup(opener=lambda request, timeout: self.response(package),
                            launcher=lambda setup_path: {'ok': True, 'declined': False})
        closed.start()
        self.assertIn('closed without installing', self.wait(closed, ('missing',))['message'])
        self.signed = {'status': 'NotSigned', 'names': []}
        unverified = self.setup(opener=lambda request, timeout: self.response(package),
                                launcher=Mock(side_effect=AssertionError('never opened')))
        unverified.start()
        self.assertIn('could not be verified', self.wait(unverified, ('error',))['message'])
        self.assertEqual(list((self.root / 'microphone').glob('vb-cable-*')), [], 'nothing is left behind')

    def test_one_disabled_side_is_not_a_restart(self):
        self.driver = 'installed'
        self.devices = {'render': ['CABLE Input (VB-Audio Virtual Cable)'], 'capture': ['Microphone (Realtek)']}
        setup = self.setup()
        status = setup.check()
        self.assertEqual(status['state'], 'disabled')
        self.assertIn('Enable both CABLE Input', status['message'])
        with self.assertRaisesRegex(ValueError, 'enable CABLE Input'):
            setup.start()
        self.devices = {'render': [], 'capture': []}
        self.assertEqual(setup.check()['state'], 'restart')
        with self.assertRaisesRegex(ValueError, 'restart Windows'):
            setup.start()
        self.assertFalse(setup.status()['busy'])

    def test_an_open_setup_window_or_an_added_file_stops_it(self):
        self.scripts = []
        open_windows = ['1']

        def runner(arguments, timeout):
            if 'Get-Process' in arguments[-1]:
                return completed(0, open_windows[0] + '\n')
            return self.runner(arguments, timeout)
        setup = self.setup(runner=runner)
        with self.assertRaisesRegex(ValueError, 'already open'):
            setup.start()
        open_windows[0] = '0'
        data = self.package()
        folder, files = setup.unpack(data)
        (folder / 'version.dll').write_bytes(b'planted')
        with self.assertRaisesRegex(ValueError, 'folder changed'):
            setup.verify(folder, files, data)

    def test_a_setup_still_open_after_15_minutes_keeps_its_files(self):
        package = self.package()
        setup = self.setup(opener=lambda request, timeout: self.response(package),
                           launcher=lambda path: {'ok': False, 'declined': False, 'still_open': True,
                                                  'message': 'The VB-CABLE setup stayed open for 15 minutes.'})
        setup.start()
        status = self.wait(setup, ('error',))
        self.assertIn('stayed open', status['message'])
        self.assertEqual(len(list((self.root / 'microphone').glob('vb-cable-*'))), 1, 'the open setup keeps its files')

    def test_a_trickling_download_ends(self):
        from unittest.mock import patch
        import host.microphone_setup as microphone

        class Slow(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                return b'x' * 10
        clock = iter(range(0, 10000, 30))
        setup = self.setup(opener=lambda request, timeout: Slow())
        with patch.object(microphone.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(ValueError, 'more than 3 minutes'):
                setup.download()

    def test_the_session_warning_says_what_is_missing(self):
        from host import audio
        from test_rc41 import default_document
        status = audio.microphone_status(True, default_document(), Path('probe'), lambda helper: {'render': [], 'capture': []})
        self.assertIn('Set up headset microphone', status['warning'])
        status = audio.microphone_status(True, default_document(), Path('probe'), lambda helper: {'render': [], 'capture': []},
                                         setup_state='restart')
        self.assertIn('Windows needs a restart', status['warning'])
        half = {'render': ['CABLE Input (VB-Audio Virtual Cable)'], 'capture': ['Microphone (Realtek)']}
        status = audio.microphone_status(True, default_document(), Path('probe'), lambda helper: half, setup_state='restart')
        self.assertIn('Enable both CABLE Input', status['warning'], 'a disabled side, not a restart')
        self.assertIn('“CABLE Input (VB-Audio Virtual Cable)”', status['warning'])

    def test_pinned_package_when_available(self):
        from host.microphone_setup import PACKAGE_SHA256
        import hashlib
        local = Path('/tmp/vbcable45.zip')
        if not local.is_file():
            self.skipTest('the VB-CABLE package is not on this machine')
        data = local.read_bytes()
        self.assertEqual((hashlib.sha256(data).hexdigest(), len(data)), (PACKAGE_SHA256, 1318877))
        setup = self.setup(runner=Mock(side_effect=AssertionError('no signature check for the pinned release')))
        folder, files, how = self.checked(setup, data)
        self.assertEqual(how, 'pinned')
        self.assertTrue((folder / 'VBCABLE_Setup_x64.exe').is_file())


class FoveaPanelTests(unittest.TestCase):
    """RC4.3 eye-tracked foveation panel: live in memory only, tuned eye-tracked parameters, sharpening."""

    LINE = ('FOVEA region=0.400,0.400 edge=4.00,4.00 left=0.620,0.440 right=0.420,0.440 encoded=1408x1408 '
            'eye=2560x2560 gaze_left=0.640,0.420 gaze_right=0.440,0.420 gaze_age_ms=6')

    def test_lines_parse_and_validate(self):
        from launcher import fovea
        view = fovea.parse_line(self.LINE)
        self.assertEqual(view['left'], [0.62, 0.44])
        self.assertEqual(view['gaze_right'], [0.44, 0.42])
        self.assertEqual((view['encoded'], view['eye'], view['gaze_age_ms']), ([1408, 1408], [2560, 2560], 6))
        plain = fovea.parse_line(self.LINE.split(' gaze_left')[0])
        self.assertNotIn('gaze_left', plain)
        for bad in ('FOVEA', 'FOVEA region=1.5,0.4 edge=4,4 left=.5,.5 right=.5,.5 encoded=1408x1408 eye=2560x2560',
                    'FOVEA region=0.4,0.4 edge=9,4 left=.5,.5 right=.5,.5 encoded=1408x1408 eye=2560x2560',
                    'FOVEA region=0.4,0.4 edge=4,4 left=.5,.5 right=.5,.5 encoded=1408x1408',
                    'TIMING x=1', self.LINE.replace('gaze_right=0.440,0.420 ', '')):
            self.assertIsNone(fovea.parse_line(bad), bad)
        with self.assertRaises(ValueError):
            fovea.view(dict(view, frame_pass='three-pass'))
        with self.assertRaises(ValueError):
            fovea.view(dict(view, extra=1))
        self.assertEqual(fovea.view(dict(view, gaze_share=.98, saving_percent=70, frame_pass='fused',
                                         sharpening='medium', gaze_rate=120))['saving_percent'], 70)

    def test_live_view_ages_out(self):
        from launcher.fovea import LiveView, parse_line
        now = [100.0]
        live = LiveView(clock=lambda: now[0])
        self.assertEqual(live.current(), {'live': False})
        live.update(parse_line(self.LINE))
        self.assertTrue(live.current()['live'])
        now[0] += 2
        self.assertEqual(live.current(), {'live': False})

    def app(self):
        from test_session_lifecycle import SessionLifecycleTests
        case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case.app()

    def test_where_you_look_is_never_logged_or_reported(self):
        app = self.app()
        app.log(self.LINE + '\n')
        app.log('STATUS something else')
        self.assertTrue(app.fovea_live()['live'])
        self.assertFalse(any('FOVEA' in line for line in app.lines))
        self.assertNotIn('FOVEA', app.log_path.read_text())
        self.assertFalse(any('FOVEA' in line or 'gaze_left' in line for line in app.journal.lines))
        self.assertNotIn('gaze_left', app.journal.path.read_text())
        app.process = None
        from unittest.mock import patch, Mock
        app.running = lambda: False
        with patch('launcher.app.subprocess.Popen', return_value=Mock(stdout=[])), patch('launcher.app.threading.Thread'):
            app.launch(['VROSTBridgeClient'])
        app.process = None
        self.assertEqual(app.fovea_live(), {'live': False}, 'a new native client starts without the old view')

    def test_eye_tracked_tuning(self):
        from launcher.headset import foveation_parameters, foveated_size
        tuned = foveation_parameters('eye-tracked', True, ('gaze-foveation',), 'small', 'light')
        self.assertEqual((tuned['center_size'], tuned['edge_ratio'], tuned['gaze']), ([.3, .3], [5.0, 5.0], True))
        default = foveation_parameters('eye-tracked', True, ('gaze-foveation',))
        self.assertEqual((default['center_size'], default['edge_ratio']), ([.4, .4], [4.0, 4.0]), 'the RC4 preset')
        self.assertEqual(foveation_parameters('balanced', False, (), 'small', 'light')['center_size'], [.55, .5],
                         'only the eye-tracked preset is tuned')
        self.assertLess(foveated_size(2560, 2560, tuned)[0], foveated_size(2560, 2560, default)[0])
        from launcher.session_link import foveation_settings
        foveation_settings(tuned)  # an RC4.2 host accepts these values
        app = self.app()
        app.apply_client_options({'fov_region': 'large', 'fov_edge': 'detail', 'sharpening': 'high'})
        self.assertEqual(app.foveation_settings('eye-tracked', ('gaze-foveation',))['center_size'], [.55, .55])
        for bad in ({'fov_region': 'huge'}, {'fov_edge': 2}, {'sharpening': 'max'}):
            with self.assertRaises(ValueError):
                app.apply_client_options(bad)

    def test_sharpening_applies_at_once(self):
        from unittest.mock import Mock
        app = self.app()
        app.native_command = Mock(return_value=True)
        app.running = lambda: False
        app.action({'action': 'set-sharpening', 'level': 'high'})
        app.native_command.assert_not_called()
        self.assertEqual(json.loads(app.settings_path.read_text())['sharpening'], 'high')
        app.running = lambda: True
        app.action({'action': 'set-sharpening', 'level': 'off'})
        app.native_command.assert_called_once_with('sharpen 0')
        with self.assertRaisesRegex(ValueError, 'sharpening level'):
            app.action({'action': 'set-sharpening', 'level': 'extreme'})

    def test_the_host_page_sees_the_streaming_clients_view(self):
        import secrets
        from unittest.mock import Mock
        from launcher.fovea import LiveView, parse_line
        from launcher.session_link import SessionHost, SessionError
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        received = LiveView()
        host = SessionHost(Path(temp.name) / 'host', Mock(), Mock(), lambda: {'runtime_state': 'ready', 'available': True},
                           Mock(return_value=True), lambda *_: None, port=0, address_provider=lambda: [],
                           test_peer=lambda peer: peer == '127.0.0.1', fovea=received.update)
        self.addCleanup(host.close)
        self.assertIn('fovea-view', host.hello()['features'])
        credentials = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        host.dispatch('/pair', dict(credentials), '127.0.0.1')
        view = parse_line(self.LINE)
        with self.assertRaisesRegex(SessionError, 'lease'):
            host.dispatch('/fovea', dict(credentials, lease_id='x' * 32, view=view), '127.0.0.1')
        lease = host.dispatch('/start', dict(credentials, request_id=secrets.token_hex(16), settings={'codec': 'Hevc', 'bitrate': 100}),
                              '127.0.0.1')['lease_id']
        reply = host.dispatch('/fovea', dict(credentials, lease_id=lease, view=dict(view, frame_pass='fused')), '127.0.0.1')
        self.assertTrue(reply['accepted'])
        self.assertEqual(received.current()['frame_pass'], 'fused')
        self.assertFalse(host.dispatch('/fovea', dict(credentials, lease_id=lease, view=view), '127.0.0.1')['accepted'],
                         'about ten a second at most')
        with self.assertRaisesRegex(SessionError, 'Invalid eye-tracking view'):
            host.dispatch('/fovea', dict(credentials, lease_id=lease, view=dict(view, left=[2, 0])), '127.0.0.1')
        with self.assertRaisesRegex(SessionError, 'Unsupported'):
            host.dispatch('/fovea', dict(credentials, lease_id=lease, view=view, extra=1), '127.0.0.1')
        old = SessionHost(Path(temp.name) / 'old', Mock(), Mock(), lambda: {}, Mock(return_value=True), lambda *_: None,
                          port=0, address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(old.close)
        self.assertNotIn('fovea-view', old.hello()['features'])

    def test_the_client_shares_only_with_a_host_that_shows_it(self):
        from unittest.mock import Mock
        from launcher.session_link import SessionClient
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = SessionClient(Path(temp.name), lambda *_: None, address_provider=lambda: [],
                               test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(client.close)
        self.addCleanup(setattr, client, 'lease_id', '')  # runs first: nothing to stop at close
        client._request = Mock(return_value={})
        client.lease_id, client.host_ip, client.host_features = 'a' * 32, '127.0.0.1', ['report-upload']
        self.assertFalse(client.send_fovea({'left': [.5, .5]}))
        client._request.assert_not_called()
        client.host_features = ['fovea-view']
        self.assertTrue(client.send_fovea({'left': [.5, .5]}))
        self.assertEqual(client._request.call_args.args[1], '/fovea')
        self.assertEqual(client._request.call_args.args[2]['lease_id'], 'a' * 32)

    def test_the_client_shares_only_while_the_region_follows_the_eyes(self):
        from unittest.mock import Mock
        app = self.app()
        app.running = lambda: True
        app.session_link.host_features = ['fovea-view', 'gaze-foveation']
        app.session_link.send_fovea = Mock(return_value=True)
        app.log(self.LINE)
        app.settings.update(gaze=False, foveation='eye-tracked')
        self.assertFalse(app.share_fovea_once(), 'no eye tracking')
        app.settings.update(gaze=True, foveation='balanced')
        self.assertFalse(app.share_fovea_once(), 'the region does not follow the eyes')
        app.settings.update(foveation='eye-tracked', sharpening='low')
        self.assertFalse(app.share_fovea_once(), 'RC4.5: Baballonia not set up and calibrated')
        app.eye.manager.installed = lambda: True
        app.eye.manager.calibration = lambda: {'calibrated': True, 'date': '2026-10-05'}
        app.timings = {'frame_pass': 'fused'}
        app.session_link.snapshot.return_value = {'host': {'pipeline': {'stream': {'gaze_foveation': 0}}}}
        self.assertFalse(app.share_fovea_once(), 'the host does not follow the gaze this session')
        app.session_link.snapshot.return_value = {'host': {'pipeline': {'stream': {'gaze_foveation': 1}}}}
        self.assertTrue(app.share_fovea_once())
        sent = app.session_link.send_fovea.call_args.args[0]
        self.assertEqual((sent['left'], sent['gaze_left'], sent['frame_pass'], sent['sharpening']),
                         ([0.62, 0.44], [0.64, 0.42], 'fused', 'low'))
        from launcher.fovea import view
        view(sent)  # what the host accepts
        app.session_link.host_features = ['gaze-foveation']
        self.assertFalse(app.share_fovea_once(), 'an RC4.2 host has no eye-tracking view')

    def test_fragments_and_damaged_values_never_leak_or_crash(self):
        app = self.app()
        app.log('gaze_left=0.640,0.420 gaze_right=0.440,0.420 gaze_age_ms=6')  # the tail of a split line
        app.log('[hevc @ 0x1] FOVEA region=0.4')
        self.assertFalse(any('gaze_' in line or 'FOVEA' in line for line in app.lines))
        from launcher import fovea
        line = self.LINE
        for bad in ({'left': [10 ** 400, 0.5]}, {'region': [0.0, 0.0]}, {'gaze_share': 10 ** 400}, {'frame_pass': 7}):
            with self.assertRaises(ValueError):
                fovea.view(dict(fovea.parse_line(line), **bad))
        for bad in ({'sharpening': ['high']}, {'fov_region': {'a': 1}}):
            with self.assertRaises(ValueError):
                app.apply_client_options(bad)
        with self.assertRaisesRegex(ValueError, 'sharpening level'):
            app.action({'action': 'set-sharpening', 'level': ['high']})
        # A damaged settings file never keeps VROSTBridge closed.
        app.settings_path.write_text(json.dumps({'sharpening': ['high'], 'fov_region': {'x': 1}, 'fov_edge': 'light'}))
        from launcher.app import Application
        again = Application('client')
        self.assertEqual((again.settings['sharpening'], again.settings['fov_edge']), ('medium', 'light'))

    def test_host_reports_keep_counts_not_centres(self):
        from host.events import HostEvents
        from unittest.mock import Mock
        events = HostEvents.__new__(HostEvents)
        events.lock = __import__('threading').Lock()
        events.latest, events.last_record, events.journal = {}, {}, Mock()
        events.repeats, events.native_hands = {}, {}
        events.accept(json.dumps({'event_type': {'id': 'Log', 'data': {'content':
            'VROST_GAZE_FOVEATION gaze=1 optical_centre=1 gaze_frames=500 fallback_frames=10 moves=40 left=0.210,-0.100 right=0.180,-0.100'}}}))
        recorded = events.journal.record.call_args.args[1]
        self.assertEqual(recorded['gaze_frames'], 500)
        self.assertNotIn('left', recorded)
        self.assertNotIn('right', recorded)
        self.assertEqual(events.latest['GazeFoveation']['values']['left'], [0.21, -0.1], 'the live status keeps them')
        # The driver's own log, as copied into a host report.
        from launcher.reporting import filtered_driver_log
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        log = Path(temp.name) / 'session_log.txt'
        log.write_text('[INFO] VROST_GAZE_FOVEATION gaze=1 optical_centre=1 gaze_frames=500 fallback_frames=10 moves=40 '
                       'left=0.210,-0.100 right=0.180,-0.100\n[INFO] other line left=kept\n')
        text = filtered_driver_log(log)
        self.assertIn('VROST_GAZE_FOVEATION gaze=1 optical_centre=1 gaze_frames=500 fallback_frames=10 moves=40', text)
        self.assertNotIn('0.210', text)
        self.assertIn('other line left=kept', text)


class OnePassFramePathTests(unittest.TestCase):
    """RC4.3 troubleshooting switch: One-pass frame path off keeps RC4.2's two passes."""

    def app(self):
        from test_session_lifecycle import SessionLifecycleTests
        case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case, case.app()

    def test_on_by_default_and_off_passes_two_pass_to_the_native_client(self):
        case, app = self.app()
        self.assertIs(app.settings['one_pass'], True)
        app.launch = Mock()
        app.action({'action': 'start', 'codec': 'Hevc', 'bitrate': 120, 'resolution': 'compatibility', 'host_ip': '',
                    'one_pass': False})
        args, _ = app.launch.call_args.args
        self.assertEqual(args, [str(app.client), '--two-pass', '--sharpening', '70'])
        self.assertIs(json.loads(app.settings_path.read_text())['one_pass'], False)
        with self.assertRaisesRegex(ValueError, 'one_pass'):
            app.apply_client_options({'one_pass': 'no'})

    def test_the_panel_says_sharpening_is_off_without_the_fused_pass(self):
        case, app = self.app()
        app.running = lambda: True
        app.settings['sharpening'] = 'high'
        app.timings = {'frame_pass': 'fused'}
        self.assertEqual(app.applied_sharpening(), 'high')
        for frame_pass in ('two-pass', 'cpu'):
            app.timings = {'frame_pass': frame_pass}
            self.assertEqual(app.applied_sharpening(), 'off', frame_pass)
        app.running = lambda: False
        self.assertEqual(app.applied_sharpening(), 'high', 'the saved level while stopped')

    def test_game_mode_has_the_switch_and_explains_sharpening_without_it(self):
        import threading
        from launcher import game_ui
        view = game_ui.View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.action = Mock()
        self.assertIn('one_pass', game_ui.TOGGLES)
        self.assertIs(game_ui.DEFAULTS['one_pass'], True)
        view.list_page = Mock()
        view.state = {'settings': {}, 'running': False}
        view.page_pc()
        rows = {row[0]: row for row in view.list_page.call_args.args[1]}
        self.assertEqual(rows['toggle:one_pass'][1:4], ('One-pass frame path', 'On', True))
        view.state = {'settings': {'one_pass': False}, 'running': True}
        view.page_pc()
        rows = {row[0]: row for row in view.list_page.call_args.args[1]}
        self.assertEqual(rows['toggle:one_pass'][2:4], ('Off', False), 'locked during a session')
        view.page_stream()
        rows = {row[0]: row for row in view.list_page.call_args.args[1]}
        self.assertIn('one-pass frame path', rows['sharpening'][4])
        view.choose('toggle:one_pass')
        view.action.assert_called_with('client-preferences', one_pass=True)

    def test_the_native_client_skips_the_fused_pass_when_asked(self):
        main = (ROOT / 'client/platform/linux/main.cpp').read_text()
        self.assertIn('else if (arg == "--two-pass")\n      twoPass = true;', main)
        self.assertIn('graphics.allowFused = !twoPass && !compatibility;\n    graphics.open(', main)
        self.assertIn('--two-pass  Convert frames when decoded', main)
        vulkan = (ROOT / 'client/platform/linux/vulkan.cpp').read_text()
        self.assertIn('if (!allowFused)\n      diagnosticLine("STATUS Fused frame pass off', vulkan)
        self.assertIn('else if (reaimPool_) openFused(', vulkan)


if __name__ == '__main__':
    unittest.main()
