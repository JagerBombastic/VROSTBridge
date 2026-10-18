"""1.0.0.6: from the 1.0.0.5 test reports, on both programs (both are 1.0.0.6).

- The sharp region followed the eyes only near the middle of the view. Baballonia's model reports only the range of
  eye movement its calibration covered (and at most 45 degrees, the range its trainer uses), and nothing showed how
  far the gaze reached. The relay now measures it each session (whole degrees, never a trace), the eye-tracking
  card and Game Mode show it with a Measure again button, the host logs how far the gaze and the region went
  (driver patch 0020), and the calibration steps say to turn the head far enough each way.
- 10-bit colour was refused at every start: AMF's P010 output came back dimmed (white at 297 of 1023), as if
  mapped for HDR. The host now first asks the converter for plain SDR (transfer and primaries kept, no tone
  mapping) and checks that, then 1.0.0.5's settings, and logs every attempt (patch 0020).
- Automatic bitrate chose 50 Mbit/s on a wired path that carried 300 Mbit/s without loss: the network test judged
  150 Mbit/s late because the host's sender, a Python loop, took 9.5 ms to write each frame. Lateness is now what
  the way here added to each frame (arrival spread less the sender's own), and such a saved result is tested again.
- Controller models clipped into each other by about 10 mm on the stream (not on the headset's own PC), also when
  held still. The host extrapolates poses by the whole motion-to-photon latency, so a controller that stops suddenly
  overshoots for a moment: the client now repeats that extrapolation on its own samples and reports how far it lands
  from where the controller really was. For the still case the distance between the controllers is measured where
  the headset PC tracks it, where the host receives it and where the host submits it (patch 0020).
- The page checks (tests/test_desktop_ui.js) had fallen behind the 1.0.0.3 eye view; they run here again.
"""
import json
import math
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from host.stream_options import parse_ib_values  # noqa: E402
from launcher import game_ui, gaze_provider, link_test, network_test  # noqa: E402
from launcher.gaze_provider import GazeReach, GazeRelay, angles_quaternion, gaze_angles  # noqa: E402
from launcher.timeline import SessionTimeline  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

PATCH = 'patches/0020-host-colour-variants-and-gaze-reach.patch'


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_6_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1007.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 6))

    def test_the_release_notes_are_listed(self):
        self.assertIn('RELEASE_1.0.0.6.md', read('docs/DOCUMENTATION_INDEX.md'))
        notes = read('docs/history/RELEASE_1.0.0.6.md')
        for words in ('0020-host-colour-variants-and-gaze-reach.patch', 'Gaze reach', 'Measure again', '45°',
                      'VROST_COLOR_PROBE', 'network_spread_p95_ms', 'prediction', 'calibrat'):
            self.assertIn(words, notes)


# ------------------------------------------------------------------------------------------------ the gaze reach
class GazeReachTests(unittest.TestCase):
    def test_angles_follow_the_hosts_convention(self):
        # VRChat's OSC: pitch positive looks down, yaw positive looks right. The host's gaze_angles (patch 0020):
        # yaw positive right, pitch positive up.
        for pitch, yaw, expected in ((0, 0, (0, 0)), (0, 30, (30, 0)), (20, 0, (0, -20)), (-10, 0, (0, 10)),
                                     (0, -40, (-40, 0))):
            got = gaze_angles(angles_quaternion(pitch, yaw))
            self.assertAlmostEqual(got[0], expected[0], places=3)
            self.assertAlmostEqual(got[1], expected[1], places=3)
        # The host's own formula on the same quaternion (vrostbridge_foveation.rs gaze_angles).
        patch = read(PATCH)
        self.assertIn('direction.x.atan2(-direction.z).to_degrees()', patch)
        self.assertIn('.atan2((direction.x * direction.x + direction.z * direction.z).sqrt())', patch)

    def test_the_reach_is_each_ways_percentile_in_whole_degrees(self):
        reach = GazeReach()
        for _ in range(gaze_provider.REACH_MIN_SAMPLES - 1):
            reach.note(angles_quaternion(0, 0))
        self.assertIsNone(reach.reach(), 'too few samples to say')
        reach.reset()
        # A session of looking about: yaw -20..25, pitch (VRChat) -12..18, and one wild sample (a blink).
        for index in range(1000):
            yaw = -20 + 45 * (index % 101) / 100
            pitch = -12 + 30 * (index % 37) / 36
            reach.note(angles_quaternion(pitch, yaw))
        reach.note(angles_quaternion(80, -85))
        value = reach.reach()
        self.assertEqual(value['samples'], 1001)
        self.assertTrue(18 <= value['left'] <= 21 and 23 <= value['right'] <= 26, value)
        self.assertTrue(10 <= value['up'] <= 13 and 16 <= value['down'] <= 19, value)
        self.assertTrue(all(type(value[way]) is int for way in ('left', 'right', 'up', 'down')))
        # Only counts per degree are kept, never a sample.
        self.assertEqual(set(vars(reach)), {'yaw', 'pitch', 'samples'})
        self.assertEqual(len(reach.yaw), 2 * gaze_provider.REACH_LIMIT_DEG + 1)
        reach.note((math.nan, 0, 0, 1))
        self.assertEqual(reach.reach()['samples'], 1001, 'invalid samples are not counted')

    def test_the_relay_counts_the_reach_per_session_and_can_start_again(self):
        relay = GazeRelay.__new__(GazeRelay)  # no sockets: only the counters
        relay.gaps = {}
        relay.longest_gap = 0.0
        relay.reach = GazeReach()
        for _ in range(200):
            relay.reach.note(angles_quaternion(0, 25))
        self.assertEqual(relay.reach.reach()['right'], 25)
        relay.reset_reach()
        self.assertIsNone(relay.reach.reach())
        for _ in range(200):
            relay.reach.note(angles_quaternion(0, 10))
        relay.reset_gaps()  # each session starts afresh
        self.assertIsNone(relay.reach.reach())
        source = read('launcher/gaze_provider.py')
        self.assertIn("'reach': self.reach.reach(),", source)  # 1.0.0.8: then the gaze centring
        self.assertIn('self.reach.note(q)', source)
        self.assertIn("'unsent', 'other', 'idle_s', 'gaps', 'reach', 'centre')", read('launcher/eye_tracking.py'))

    def test_the_pages_show_it_with_measure_again(self):
        page = read('launcher/desktop.html')
        self.assertIn('id="eye-reach-reset" onclick="act(\'eye-reach-reset\')">Measure again</button>', page)
        self.assertIn('Gaze reach this session: ${reach.left}° left · ${reach.right}° right · ${reach.up}° up · '
                      '${reach.down}° down (Baballonia reports up to 45°).', page)
        self.assertIn('calibrate again and turn your head further while you follow the dot', page)
        self.assertIn('far enough each way (corners too) that the dot nears the edge of your view', page)
        self.assertIn('Reports include only how far the gaze reached each way.', page)
        app = read('launcher/app.py')
        self.assertIn("if action=='eye-reach-reset' and self.role=='client':", app)
        self.assertIn("'gaze_reach':self.gaze_reach()}", app)
        self.assertEqual(game_ui.gaze_reach_text({'left': 18, 'right': 21, 'up': 14, 'down': 22, 'samples': 500}),
                         '18° ← · 21° → · 14° ↑ · 22° ↓')
        self.assertEqual(game_ui.gaze_reach_text(None), '—')
        self.assertEqual(game_ui.gaze_reach_text({'left': 'x'}), '—')
        self.assertIn("('Gaze reach', gaze_reach_text(panel.get('gaze_reach'))),", read('launcher/game_ui.py'))

    def test_the_hosts_line_carries_the_window_reach(self):
        patch = read(PATCH)
        self.assertIn(' gaze_yaw={:.0},{:.0} gaze_pitch={:.0},{:.0} region_x={:.2},{:.2} region_y={:.2},{:.2}', patch)
        self.assertIn('fn reach_keeps_each_windows_extremes_then_starts_again()', patch)
        line = ('VROST_GAZE_FOVEATION gaze=1 optical_centre=1 gaze_frames=10 held_frames=0 fallback_frames=0 moves=2 '
                'pauses=0 longest_pause_ms=0 returns=0 left=0.100,0.000 right=-0.100,0.000 gaze_yaw=-18,21 '
                'gaze_pitch=-9,12 region_x=0.38,0.81 region_y=0.40,0.62')
        values = parse_ib_values('VROST_GAZE_FOVEATION', line)
        self.assertEqual(values['gaze_yaw'], [-18.0, 21.0])
        self.assertEqual(values['region_x'], [0.38, 0.81])
        self.assertEqual(values['moves'], 2)

    def test_the_hosts_log_never_says_where_the_eyes_looked(self):
        # The current region centres are logged only while they do not follow the gaze (fixed or optical centre).
        patch = read(PATCH)
        self.assertIn('let centres = if config.gaze_foveation {', patch)
        self.assertRegex(patch, r'(?m)^\+\s+"VROST_GAZE_FOVEATION gaze=\{\} .*returns=\{\}\{\}\{\}",$')
        self.assertNotRegex(patch, r'(?m)^\+\s+"VROST_GAZE_FOVEATION .*left=')


class FoveatedSizeTests(unittest.TestCase):
    def test_the_estimate_rounds_as_alvr_does(self):
        from launcher.headset import foveated_size
        square = lambda size, centre, edge: foveated_size(size, size, {'center_size': [centre, centre],
                                                                         'edge_ratio': [edge, edge]})[0]
        # Each case once came out a 32-pixel step too large with f64 sums.
        self.assertEqual(square(2016, .55, 3), 1408)
        self.assertEqual(square(2560, .55, 3), 1792)
        self.assertEqual(square(3200, .30, 5), 1408)
        self.assertEqual(square(3200, .40, 4), 1760)
        # What the RC4.4 host really sent for Balanced at 2560 × 2560, and the 1.0.0.5 test's Small + Balanced.
        self.assertEqual(foveated_size(2560, 2560, {'center_size': [.55, .50], 'edge_ratio': [3, 3]}), (1792, 1728))
        self.assertEqual(square(2560, .30, 4), 1216)


# ----------------------------------------------------------------------------------------------- 10-bit colour
class ColourVariantTests(unittest.TestCase):
    def test_plain_sdr_is_tried_first_then_the_earlier_settings(self):
        patch = read(PATCH)
        for words in ('AMF_VIDEO_CONVERTER_OUTPUT_TONEMAPPING, AMF_VIDEO_CONVERTER_TONEMAPPING_COPY',
                      'AMF_VIDEO_CONVERTER_INPUT_TRANSFER_CHARACTERISTIC, AMF_COLOR_TRANSFER_CHARACTERISTIC_BT709',
                      'AMF_VIDEO_CONVERTER_OUTPUT_COLOR_PRIMARIES, AMF_COLOR_PRIMARIES_BT709',
                      'AMF_VIDEO_CONVERTER_USE_DECODER_HDR_METADATA, false',
                      '{{true, "plain"}, {false, "auto"}}', 'VROST_COLOR_PROBE path=%s variant=%s measured=1',
                      'verified=%d variant=%s refused=%s'):
            self.assertIn(words, patch)
        # The test image still decides: only a verdict that is known and displayable is used.
        self.assertIn('if (verdict.known && verdict.displayable) {', patch)

    def test_the_companion_keeps_the_variant_and_a_longer_reason(self):
        line = ('VROST_COLOR path=rgb bits=8 range=full matrix=709 verified=0 variant=none '
                'refused=p010:plain-black-or-white-level/auto-black-or-white-level probe_bits=10 black=0,512,512')
        values = parse_ib_values('VROST_COLOR', line)
        self.assertEqual(values['refused'], 'p010:plain-black-or-white-level/auto-black-or-white-level')
        self.assertEqual(values['variant'], 'none')
        self.assertIn("('path','bits','range','matrix','verified','variant','refused')", read('launcher/app.py'))
        # The page's 10-bit note still recognises a refused P010 path.
        self.assertIn("String(hostStream.colour_refused||'none').startsWith('p010')", read('launcher/desktop.html'))


# ------------------------------------------------------------------------------------------------- network test
HEADER = link_test.HEADER


def datagram(test_id, step, frame, packet, count, send_ns, size=1200):
    data = bytearray(size)
    HEADER.pack_into(data, 0, link_test.MAGIC, link_test.VERSION, 0, step, test_id, frame, packet, count, send_ns)
    return bytes(data)


class NetworkLatenessTests(unittest.TestCase):
    def receiver(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        return link_test.LinkTestReceiver(sock, 11, fps=75, steps=[150])

    def feed(self, receiver, per_packet_send_us, per_packet_arrival_us, frames=30, packets=40):
        interval = int(1e9 / 75)
        for frame in range(frames):
            start = frame * interval
            for packet in range(packets):
                sent = start + int(packet * per_packet_send_us * 1000)
                arrived = 5_000_000 + start + int(packet * per_packet_arrival_us * 1000)
                receiver._accept(datagram(11, 0, frame, packet, packets, sent), arrived, arrival_ns=arrived)
        return receiver.results()[0]

    def test_a_slow_sender_is_not_a_late_network(self):
        # The 1.0.0.5 test: the host's sender took about 50 us a datagram; the network added nothing.
        row = self.feed(self.receiver(), per_packet_send_us=250, per_packet_arrival_us=250)
        self.assertGreater(row['spread_p95_ms'], 0.6 * row['frame_interval_ms'])
        self.assertAlmostEqual(row['send_spread_p95_ms'], row['spread_p95_ms'], delta=0.01)
        self.assertLess(row['network_spread_p95_ms'], 0.01)
        self.assertEqual(link_test.step_verdict(row), (True, ''))
        # A row saved before 1.0.0.6 (no network spread) is still judged by its whole spread.
        old = {key: value for key, value in row.items() if key not in ('network_spread_p95_ms', 'send_spread_p95_ms')}
        self.assertEqual(link_test.step_verdict(old), (False, 'late'))

    def test_delay_the_network_adds_is_late(self):
        # The sender wrote each frame in 1 ms; the arrivals spread over 10 ms.
        row = self.feed(self.receiver(), per_packet_send_us=25, per_packet_arrival_us=250)
        self.assertGreater(row['network_spread_p95_ms'], 0.6 * row['frame_interval_ms'])
        self.assertEqual(link_test.step_verdict(row), (False, 'late'))

    def test_the_saved_steps_keep_both_spreads(self):
        self.assertIn("'send_spread_p95_ms', 'network_spread_p95_ms', 'jitter_p95_ms',", read('launcher/network_test.py'))

    def test_a_result_limited_by_lateness_without_the_senders_time_is_tested_again(self):
        report = json.loads(read('tests/fixtures/network_result_1005.json'))
        self.assertTrue(network_test.sender_timed(report))
        self.assertTrue(network_test.needs_retest(report))
        measured = dict(report, steps=[dict(row, network_spread_p95_ms=0.4) for row in report['steps']])
        self.assertFalse(network_test.sender_timed(measured))
        self.assertFalse(network_test.needs_retest(measured))
        lossy = dict(report, message='Network test: clean up to 80 Mbit/s on Ethernet (data was lost at 150 Mbit/s); '
                                     'using 50 Mbit/s.')
        self.assertFalse(network_test.sender_timed(lossy))
        self.assertFalse(network_test.sender_timed(None))
        app = read('launcher/app.py')
        self.assertIn("counted the host PC\\'s own '\n                         'sending time as network delay", app)

    def test_replayed_the_1_0_0_5_steps_give_more_than_50_mbit_s(self):
        report = json.loads(read('tests/fixtures/network_result_1005.json'))
        rows = []
        for index, step in enumerate(report['steps']):
            packets = step['rate_mbps'] * 1e6 / 75 / 8 / 1400
            sender = packets * 0.050  # about 50 us a datagram, as measured from the 1.0.0.5 test's spreads
            rows.append(dict(step, step=index, frame_interval_ms=13.333, packets_expected=10000, end_marker=True,
                             packets_received=round(10000 * (1 - step['packet_loss_pct'] / 100)),
                             frames_expected=75, frames_complete=round(75 * (1 - step['frame_loss_pct'] / 100)),
                             network_loss_pct=step['packet_loss_pct'], send_spread_p95_ms=round(sender, 3),
                             network_spread_p95_ms=round(max(0.0, step['spread_p95_ms'] - sender), 3)))
        old = link_test.recommend([{k: v for k, v in row.items() if k != 'network_spread_p95_ms'} for row in rows],
                                  300, 'wired', 920)
        new = link_test.recommend(rows, 300, 'wired', 920)
        self.assertEqual(old['recommended_mbps'], 50)
        # 1.0.0.8: 80 (paced at 70 % of the loss-free rate; the picture ceiling then decides, 49 Mbit/s for 1.0.0.5's).
        self.assertEqual((new['clean_mbps'], new['recommended_mbps']), (300, 80))


# ---------------------------------------------------------------------------------------- controller prediction
class ControllerPredictionTests(unittest.TestCase):
    def test_the_host_compares_the_distance_received_and_submitted(self):
        patch = read(PATCH)
        for words in ('pub struct PairWindow', 'VROST_CONTROLLER_PAIR still_frames={} received_mm={:.1} submitted_mm={:.1} '
                      'max_shift_mm={:.1} max_turn_deg={:.2}', 'tracking::offset_controller_motion(',
                      'motion.predict(poll_timestamp, target_controller_timestamp)',
                      'fn an_offset_toward_each_other_shows_as_a_shorter_submitted_distance()'):
            self.assertIn(words, patch)
        values = parse_ib_values('VROST_CONTROLLER_PAIR', 'VROST_CONTROLLER_PAIR still_frames=812 received_mm=121.4 '
                                 'submitted_mm=121.4 max_shift_mm=0.3 max_turn_deg=0.05')
        self.assertEqual(values, {'still_frames': 812, 'received_mm': 121.4, 'submitted_mm': 121.4, 'max_shift_mm': 0.3,
                                  'max_turn_deg': 0.05})
        from host.events import STREAM_LINES
        self.assertEqual(STREAM_LINES['VROST_CONTROLLER_PAIR'], 'ControllerPair')
        self.assertIn("'vrostbridge_pair': 'alvr/server_openvr/src/vrostbridge_pair.rs'", read('scripts/host_module_tests.py'))

    def test_the_native_check_is_built_and_reported(self):
        source = read('client/device/openvr_device.cpp')
        self.assertIn('#include "client/device/prediction_check.hpp"', source)
        self.assertIn('handDiagnostics[i - 1].prediction.add(sampleNow, measured.pose.position, measured.linear, predictionNs,',  # 1.0.0.14: measured against the real pose
                      source)
        self.assertIn('",\\"prediction\\":{\\"samples\\":"', source)
        self.assertIn('",\\"pair\\":{\\"still_samples\\":" << pair.stillSamples', source)
        self.assertIn('controllerPair.add(', source)
        self.assertIn('add_test(NAME prediction_check_contracts COMMAND prediction_check_tests)', read('CMakeLists.txt'))
        check = read('client/device/prediction_check.hpp')
        self.assertIn('kStillMetresPerSecond = 0.03f', check)
        self.assertIn('Only distances are\n// kept, never positions.', check)

    def test_the_companion_keeps_the_sessions_worst_and_the_timeline_its_peaks(self):
        from launcher.app import Application as Launcher
        app = Launcher.__new__(Launcher)
        app.controller_prediction = {}
        line = {'left': {'prediction': {'samples': 300, 'horizon_ms': 47.5, 'mean_mm': 0.4, 'p95_mm': 1.2,
                                        'max_mm': 9.6, 'still_samples': 120, 'still_max_mm': 0.1}},
                'right': {'prediction': {'samples': 0}}}
        Launcher.note_controller_prediction(app, line)
        line['left']['prediction'].update(max_mm=4.0, p95_mm=2.5, still_max_mm=0.3)
        Launcher.note_controller_prediction(app, line)
        summary = Launcher.controller_prediction_summary(app)
        self.assertEqual(set(summary), {'left'})
        left = summary['left']
        self.assertEqual((left['windows'], left['samples'], left['max_mm'], left['p95_max_mm'], left['still_max_mm'],
                          left['horizon_ms']), (2, 600, 9.6, 2.5, 0.3, 47.5))
        Launcher.note_controller_prediction(app, {'left': 'nonsense', 'right': {'prediction': {'samples': 'x'}}})
        self.assertEqual(Launcher.controller_prediction_summary(app)['left']['windows'], 2)
        # The controllers' distance while held still (INPUT 'pair'), kept for the session.
        Launcher.note_controller_prediction(app, {'pair': {'still_samples': 100, 'mean_mm': 120.0, 'min_mm': 119.5,
                                                           'max_mm': 120.4}})
        Launcher.note_controller_prediction(app, {'pair': {'still_samples': 300, 'mean_mm': 122.0, 'min_mm': 121.0,
                                                           'max_mm': 123.0}})
        Launcher.note_controller_prediction(app, {'pair': {'still_samples': 0, 'mean_mm': 50.0}})
        self.assertEqual(Launcher.controller_prediction_summary(app)['pair'],
                         {'still_samples': 400, 'mean_mm': 121.5, 'min_mm': 119.5, 'max_mm': 123.0})
        clock = [100.0]
        timeline = SessionTimeline(clock=lambda: clock[0])
        timeline.controllers({'left': {'prediction': {'samples': 10, 'max_mm': 3.0}},
                              'right': {'prediction': {'samples': 10, 'max_mm': 8.25}}})
        timeline.controllers({'left': {'prediction': {'samples': 10, 'max_mm': 5.5}}})
        row = timeline.snapshot()['rows'][-1]
        self.assertEqual((row['predict_max_mm_l'], row['predict_max_mm_r']), (5.5, 8.2))
        self.assertIn('controller_prediction=self.controller_prediction_summary()', read('launcher/app.py'))


# --------------------------------------------------------------------------------------------- the driver patch
class DriverPatchTests(unittest.TestCase):
    def test_patch_0020_is_in_the_set(self):
        patch = read(PATCH)
        for path in ('alvr/server_openvr/src/vrostbridge_foveation.rs', 'alvr/server_openvr/src/lib.rs',
                     'alvr/server_openvr/cpp/platform/win32/VideoEncoderAMF.cpp',
                     'alvr/server_openvr/cpp/platform/win32/VideoEncoderAMF.h'):
            self.assertIn('diff --git a/' + path + ' b/' + path, patch)
        self.assertIn('0020-host-colour-variants-and-gaze-reach.patch', read('docs/PROJECT_SPEC.md'))
        from host import stream_options
        names = sorted(p.name for p in (ROOT / 'patches').glob('*.patch'))
        self.assertGreaterEqual(len(names), 20)  # 1.0.0.9 adds 0021 to 0023 (tests/test_vrostbridge_1009.py)
        self.assertEqual(names[19], '0020-host-colour-variants-and-gaze-reach.patch')
        self.assertEqual(stream_options.PATCH_FEATURES['0020-host-colour-variants-and-gaze-reach'],
                         ('colour-variants', 'gaze-reach'))


# --------------------------------------------------------------------------------------------- the documentation
def github_anchor(heading):
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


class TechnicalReferenceTests(unittest.TestCase):
    """docs/TECHNICAL_REFERENCE.md: how it works and where it differs, with the README's specs."""

    def setUp(self):
        self.text = read('docs/TECHNICAL_REFERENCE.md')
        self.anchors = {github_anchor(match.group(1)) for match in re.finditer(r'^#{1,6} (.+)$', self.text, re.M)}

    def test_its_links_and_section_references_resolve(self):
        for anchor in re.findall(r'\]\(#([^)]+)\)', self.text):
            self.assertIn(anchor, self.anchors)
        sections = set(re.findall(r'^## (\d+)\.', self.text, re.M))
        subsections = set(re.findall(r'^### (\d+\.\d+)', self.text, re.M))
        for reference in re.findall(r'§(\d+(?:\.\d+)?)', self.text):
            self.assertIn(reference, subsections if '.' in reference else sections, reference)
        for target in re.findall(r'\]\(((?:\.\./)?[\w./-]+\.md)(?:#[^)]*)?\)', self.text):
            self.assertTrue((ROOT / 'docs' / target).resolve().is_file(), target)

    def test_it_covers_every_patch_and_the_differences(self):
        for patch in sorted(p.name for p in (ROOT / 'patches').glob('*.patch')):
            self.assertIn('| ' + patch[:4] + ' ', self.text, patch)
        for heading in ('From ALVR', "From ALVR's usual clients", 'From a directly connected headset',
                        "From SteamVR's defaults on the headset PC", "From Baballonia's usual setup",
                        'Ease of use and setup', 'Requirements'):
            self.assertRegex(self.text, r'(?m)^## \d+\. ' + re.escape(heading) + '$')
        for port in ('9943', '9944', '9945', '9946', '9947', '9005', '5353'):
            self.assertIn('| ' + port, self.text)
        self.assertIn(HOST_VERSION, self.text.splitlines()[2])
        # The README plays the name's joke; nothing else does.
        self.assertNotIn('two jobs', self.text)

    def test_the_readme_states_minimum_and_recommended_specs(self):
        readme = read('README.md')
        self.assertIn('## Minimum and recommended specs', readme)
        for row in ('| **Rendering PC** |', '| **Headset PC** |', '| **Headset** |', '| **Network** |'):
            self.assertIn(row, readme)
        self.assertIn('| | Minimum (expected) | Recommended | Tested |', readme)
        self.assertIn('docs/TECHNICAL_REFERENCE.md#19-requirements', readme)
        self.assertIn('19-requirements', self.anchors)
        self.assertEqual(readme.count('two jobs'), 1)
        self.assertIn('TECHNICAL_REFERENCE.md', read('docs/DOCUMENTATION_INDEX.md'))

    def test_the_rendered_reference(self):
        try:
            import pypdf
        except ImportError:
            self.skipTest('pypdf is not installed')
        reader = pypdf.PdfReader(str(ROOT / 'docs/TECHNICAL_REFERENCE.pdf'))
        pages = [' '.join((page.extract_text() or '').split()) for page in reader.pages]
        self.assertIn('Windows rendering host ' + HOST_VERSION, pages[0])
        text = ' '.join(pages)
        for heading in ('Part 1 — How it works', 'Part 2 — Where VROSTBridge differs', '21. From ALVR',
                        '31. Glossary'):
            self.assertIn(heading, text)
        self.assertNotIn('**', text)


# --------------------------------------------------------------------------------------------- the page checks
class PageCheckTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the page checks')
    def test_the_desktop_page_checks_pass(self):
        result = subprocess.run(['node', str(ROOT / 'tests' / 'test_desktop_ui.js')], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertIn('smoke checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
