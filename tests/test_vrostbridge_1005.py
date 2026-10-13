"""1.0.0.5: from the 1.0.0.4 test reports, on both programs (both are 1.0.0.5).

- Blacks were grey on the Beyond's OLED panels with 10-bit colour on (black with it off): the AMD encoder path
  turned RGB into 10-bit RGB and let the encoder convert, which gave studio-range samples in a stream labelled
  full range. The host now converts to 10-bit Y'CbCr itself with an explicit matrix and range, checks the result
  with a test image at the start of each stream, labels the stream with what the samples really are, and streams
  8-bit when the check fails (driver patch 0018).
- No picture at all when the headset PC's SteamVR had no position for the headset: the host renders only for a
  head pose the client sends, so it dropped every frame in silence. The client now says the headset is not
  tracked (page, Game Mode, headset card) and the host counts the frames it could not send (patch 0018).
- The eye tracking lagged and paused, and the sharp region moved visibly: the camera helper decoded and encoded
  every frame again (and queued them when that was slow), opened the camera again after any lost USB packet,
  and took the camera's slowest frame rate; Baballonia's filter added 45-70 ms after each eye movement; the host
  moved the region with every small movement. Now frames are passed on as the camera sent them, at its fastest
  rate, lost data costs one frame, the filter takes two camera frames, and the region keeps still while the gaze
  stays inside a comfort zone around its middle (patch 0019).
- Ultra (3200 per eye on a Beyond) fell to half rate on the headset PC: a stream larger than SteamVR's recommended
  size is now shown at that size, and a GPU held at a fixed clock gets the one-step fix.
- Every session since the 1.0.0.4 network test ran at 30 Mbit/s: that test found no clean rate on a wired path
  that had carried 500 Mbit/s, and its result was kept for a month. The test now measures the network rather than
  how busy the companion was (kernel arrival times, its own dropped datagrams apart, a warm-up step), a result
  without a clean rate is tested again before the next session, and a wired bitrate leaves room in the pace.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from host import stream_options  # noqa: E402
from host.events import HostEvents  # noqa: E402
from host.stream_options import GAZE_COMFORT, driver_features, parse_ib_values, stream_config  # noqa: E402
from launcher import baballonia, game_ui, graphics_limit  # noqa: E402
from launcher.baballonia import BaballoniaManager  # noqa: E402
from launcher.diagnostics import Journal  # noqa: E402
from launcher.eye_tracking import EyeCameras, helper_values  # noqa: E402
from launcher.timeline import SessionTimeline  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

PATCH_COLOUR = 'patches/0018-host-exact-colour-and-pose-drops.patch'
PATCH_COMFORT = 'patches/0019-host-gaze-comfort.patch'


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def new_file(patch_text, path):
    """The content of a file a patch adds."""
    lines, inside = [], False
    for line in patch_text.splitlines():
        if line.startswith('diff --git '):
            inside = line.endswith(' b/' + path)
            continue
        if inside and line.startswith('+') and not line.startswith('+++'):
            lines.append(line[1:])
    return '\n'.join(lines) + '\n'


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_5_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1006.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 5))

    def test_the_release_notes_are_listed(self):
        self.assertIn('RELEASE_1.0.0.5.md', read('docs/DOCUMENTATION_INDEX.md'))
        notes = read('docs/history/RELEASE_1.0.0.5.md')
        for words in ('10-bit', '0018-host-exact-colour-and-pose-drops.patch', '0019-host-gaze-comfort.patch',
                      'gaze_comfort', 'VROST_COLOR', 'VROST_NO_HEAD_POSE', 'Headset not tracked', 'STATS',
                      'Ultra', 'GPU clock'):
            self.assertIn(words, notes)

    def test_the_patches_are_in_order(self):
        names = sorted(p.name for p in (ROOT / 'patches').glob('*.patch'))
        self.assertEqual(names[17:19], ['0018-host-exact-colour-and-pose-drops.patch', '0019-host-gaze-comfort.patch'])
        self.assertGreaterEqual(len(names), 19)  # 1.0.0.6 adds 0020 (tests/test_vrostbridge_1006.py)
        features = stream_options.PATCH_FEATURES
        self.assertEqual(features['0018-host-exact-colour-and-pose-drops'], ('exact-colour', 'pose-drops'))
        self.assertEqual(features['0019-host-gaze-comfort'], ('gaze-comfort',))


# ------------------------------------------------------------------------------------------- exact 10-bit colour
class ColourTests(unittest.TestCase):
    def test_the_driver_converts_checks_and_labels_the_colour(self):
        text = read(PATCH_COLOUR)
        for words in (
                # RGB to 10-bit Y'CbCr in our own converter, with an explicit matrix and range...
                'AddYuvConverter(amf::AMF_SURFACE_P010, "p010", why)',
                'AMF_VIDEO_CONVERTER_COLOR_PROFILE, colorProfile',
                'AMF_VIDEO_CONVERTER_OUTPUT_COLOR_RANGE',
                # ...checked with a test image read back from the converter...
                'bool VideoEncoderAMF::ProbeConverter(', 'image->Convert(amf::AMF_MEMORY_DX11)',
                'output->Convert(amf::AMF_MEMORY_HOST)', 'vrost_color::Classify(measured)',
                # ...the stream labelled with what the samples are, and the encoder told it is given Y'CbCr...
                'AMF_VIDEO_ENCODER_HEVC_INPUT_COLOR_PROFILE, m_color.profile',
                'AMF_VIDEO_ENCODER_HEVC_NOMINAL_RANGE_STUDIO', 'AMF_VIDEO_ENCODER_AV1_INPUT_COLOR_PROFILE',
                'data->SetProperty(AMF_VIDEO_COLOR_RANGE, m_colorRange)',
                # ...8-bit when the check fails, and one line describing it.
                'm_use10bit = false;', 'VROST_COLOR_FALLBACK 10-bit colour could not be confirmed',
                '"VROST_COLOR path=%s bits=%d range=%s matrix=%d verified=%d refused=%s%s"',
                # The AV1 bitrate now follows the adaptive bitrate.
                'AMF_VIDEO_ENCODER_AV1_TARGET_BITRATE, bitRateIn);\n+            m_amfComponents'):
            self.assertIn(words, text)
        # The old route through 10-bit RGB is gone.
        self.assertIn('-        inFormat = amf::AMF_SURFACE_R10G10B10A2;', text)
        self.assertNotIn('+        inFormat = amf::AMF_SURFACE_R10G10B10A2;', text)

    def test_the_colour_check_classifies_converters(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('no C++ compiler')
        header = new_file(read(PATCH_COLOUR), 'alvr/server_openvr/cpp/platform/win32/VROSTBridgeColor.h')
        self.assertIn('inline Verdict Classify(const Measured& m)', header)
        self.assertNotIn('#include <windows.h>', header)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'VROSTBridgeColor.h').write_text(header)
            program = Path(directory) / 'host_color_tests'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', directory,
                            str(ROOT / 'tests/host_color_tests.cpp'), '-o', str(program)], check=True)
            result = subprocess.run([str(program)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('checks passed', result.stdout)

    def test_the_companion_keeps_the_colour_and_pose_lines(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(HostEvents, 'run'):
            journal = Journal(temp, 'host')
            events = HostEvents(lambda: None, journal)
            self.addCleanup(events.close)

            def log(content, severity='Info'):
                events.accept(json.dumps({'event_type': {'id': 'Log', 'data': {'content': content, 'severity': severity}}}))
            log('VROST_COLOR path=p010 bits=10 range=full matrix=709 verified=1 refused=none probe_bits=10 '
                'black=0,512,512 white=1023,512,512 red=217,395,1023 grey=514,512,512')
            log('VROST_NO_HEAD_POSE dropped=376 sent=0 window_s=5')
            log('VROST_COLOR_FALLBACK 10-bit colour could not be confirmed (black-or-white-level); streaming 8-bit',
                'Warning')
            statistics = events.status()['statistics']
            colour = statistics['VideoColour']['values']
            self.assertEqual((colour['path'], colour['bits'], colour['range'], colour['verified']), ('p010', 10, 'full', 1))
            self.assertEqual(colour['black'], [0.0, 512.0, 512.0])
            self.assertEqual(statistics['NoHeadPose']['values'], {'dropped': 376, 'sent': 0, 'window_s': 5})
            recorded = json.dumps(journal.report())
            self.assertIn('native-VideoColour', recorded)
            self.assertIn('native-NoHeadPose', recorded)
            # The fallback warning stays a warning in the log (it is not a VROST_COLOR value line).
            self.assertIn('10-bit colour could not be confirmed', recorded)

    def test_the_client_hears_the_hosts_colour_and_missing_poses(self):
        from launcher.app import Application
        fake = SimpleNamespace(host_events=Mock(), session_intra_refresh=False, stream_recovery=None)
        fake.host_events.pipeline.summary.return_value = {'total_ms': {'mean': 40}}
        statistics = {'StreamConfig': {'values': {'gaze_comfort': 0.5, 'gaze_hold_ms': 1000}},
                      'VideoColour': {'values': {'path': 'rgb', 'bits': 8, 'range': 'full', 'matrix': 709,
                                                 'verified': 0, 'refused': 'p010:black-or-white-level'}},
                      'NoHeadPose': {'received_utc': time.time(), 'values': {'dropped': 300, 'sent': 4}}}
        fake.host_events.status.return_value = {'statistics': statistics}
        stream = Application.session_telemetry(fake)['stream']
        self.assertEqual((stream['colour_bits'], stream['colour_refused'], stream['gaze_comfort']),
                         (8, 'p010:black-or-white-level', 0.5))
        self.assertEqual((stream['no_pose_dropped'], stream['no_pose_sent']), (300, 4))
        self.assertLessEqual(len(json.dumps(Application.session_telemetry(fake))), 2048)
        statistics['NoHeadPose']['received_utc'] = time.time() - 60  # an old window says nothing now
        self.assertNotIn('no_pose_dropped', Application.session_telemetry(fake)['stream'])

    def test_the_client_logs_and_shows_the_streams_colour(self):
        vulkan = read('client/platform/linux/vulkan.cpp')
        self.assertIn('static std::string colourLine(const AVFrame *frame, bool tenBit)', vulkan)
        self.assertIn('if (colourKey_.exchange(colourKey) != colourKey) diagnosticLine(colourLine(frame, tenBit));', vulkan)
        app = read('launcher/app.py')
        self.assertIn("if line.startswith('VIDEO_COLOUR '):", app)
        page = read('launcher/desktop.html')
        self.assertIn('function colourText(colour){', page)
        self.assertIn("10-bit off: the host could not make exact 10-bit colour, so it streams 8-bit", page)


# ---------------------------------------------------------------------------------- the headset's head position
class HeadTrackingTests(unittest.TestCase):
    def test_the_driver_counts_frames_without_a_pose(self):
        text = read(PATCH_COLOUR)
        module = new_file(text, 'alvr/server_openvr/src/vrostbridge_pose.rs')
        for words in ('pub struct PoselessFrames', 'pub fn note(&mut self, sent: bool, now: Instant) -> Option<(u64, u64)>',
                      'fn an_untracked_headset_drops_every_frame_and_a_reset_starts_afresh'):
            self.assertIn(words, module)
        for words in ('VROST_POSELESS.lock().note(head_pose.is_some(), Instant::now())',
                      '"VROST_NO_HEAD_POSE dropped={dropped} sent={sent} window_s={}"', 'VROST_POSELESS.lock().reset();'):
            self.assertIn(words, text)
        self.assertIn("'vrostbridge_pose': 'alvr/server_openvr/src/vrostbridge_pose.rs'", read('scripts/host_module_tests.py'))

    def test_the_client_reports_the_head_pose(self):
        device = read('client/device/openvr_device.cpp')
        for words in ('const bool headValid = raw[0].bDeviceIsConnected && raw[0].bPoseIsValid;',
                      'diagnosticLine(std::string("HEAD_TRACKING tracked=")', '++headPosesMissing;',
                      'case vr::TrackingResult_Calibrating_OutOfRange: return "base-stations-not-seen";'):
            self.assertIn(words, device)
        main = read('client/platform/linux/main.cpp')
        for words in ('<< " head_untracked_ms=" << runtime.headUntrackedMs(now)', 'statusState.untrackedMs=runtime.headUntrackedMs(now);'):
            self.assertIn(words, main)
        card = read('client/ui/status_card.cpp')
        self.assertIn('c.headline = "Headset not tracked";', card)
        self.assertIn('"Headset not tracked"', read('tests/status_card_tests.cpp'))
        self.assertIn("'head_untracked_ms'", read('launcher/app.py'))

    def test_the_pages_and_game_mode_say_it(self):
        page = read('launcher/desktop.html')
        self.assertIn("$('status').textContent='Headset not tracked';", page)
        self.assertIn("Waiting for the headset’s position", page)
        self.assertIn('function headTrackingHint(result,where){', page)
        ui = game_ui.View.__new__(game_ui.View)
        ui.state = {'running': True, 'metrics': {'head_untracked_ms': '4200'}}
        self.assertTrue(ui.head_untracked())
        ui.state = {'running': True, 'metrics': {'head_untracked_ms': '900'}}
        self.assertFalse(ui.head_untracked())
        ui.state = {'running': False, 'metrics': {'head_untracked_ms': '9000'}}
        self.assertFalse(ui.head_untracked())
        ui.state = {'running': True, 'metrics': {'head_untracked_ms': 'x'}}
        self.assertFalse(ui.head_untracked())
        game = read('launcher/game_ui.py')
        self.assertIn("dot = (MAGENTA if self.head_untracked() else CYAN if state == 'streaming' else MAGENTA if warn", game)
        self.assertIn("line += ' · shown at %s × %s' % shown", game)

    def test_reports_count_the_ticks_without_a_pose(self):
        timeline = SessionTimeline(clock=lambda: 100.0)
        timeline.metrics({'head_poses_missing': '75', 'fallback': '0', 'frame_age_ms': '0'}, now=100.0)
        timeline.metrics({'head_poses_missing': '75', 'fallback': '0', 'frame_age_ms': '0'}, now=101.0)
        self.assertEqual(timeline.snapshot()['rows'][0]['head_missing'], 150)


# ------------------------------------------------------------------------------- the sharp region's comfort zone
class ComfortZoneTests(unittest.TestCase):
    def test_the_driver_keeps_the_region_while_the_gaze_stays_in_its_zone(self):
        text = read(PATCH_COMFORT)
        for words in ('pub fn movement_threshold(deadband: f32, comfort: f32, center_size: f32) -> f32',
                      '> movement_threshold(input.deadband, input.comfort, size)', 'pub gaze_comfort: f32,',
                      'pub const DEFAULT_GAZE_COMFORT: f32 = 0.5;', 'gaze_comfort={:.2}', 'comfort: config.gaze_comfort,',
                      'fn gaze_wandering_inside_the_zone_keeps_the_region_and_leaving_it_recentres',
                      'fn gaze_comfort_defaults_and_bounds'):
            self.assertIn(words, text)

    def test_the_companion_asks_for_it_only_from_a_driver_that_has_it(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {'source_patch_sha256': {'0010-host-gaze-foveated-encoding.patch': 'x',
                                                '0017-host-gaze-hold.patch': 'y',
                                                '0019-host-gaze-comfort.patch': 'z'}}
            (Path(directory) / 'vrostbridge-build.json').write_text(json.dumps(manifest))
            features = driver_features(directory)
        self.assertIn('gaze-comfort', features)
        preferences = {'foveation': {'gaze': True, 'optical_centre': True}}
        self.assertEqual(stream_config(preferences, features)['gaze_comfort'], GAZE_COMFORT)
        self.assertEqual(GAZE_COMFORT, 0.5)
        self.assertNotIn('gaze_comfort', stream_config(preferences, ['gaze-foveation', 'gaze-hold']))
        self.assertEqual(parse_ib_values('VROST_STREAM_CONFIG', 'VROST_STREAM_CONFIG gaze_comfort=0.50 gaze_hold_ms=1000'),
                         {'gaze_comfort': 0.5, 'gaze_hold_ms': 1000})

    def test_fewer_moves_in_a_minute_of_simulated_gaze(self):
        """The host's rule, as the driver applies it (vrostbridge_foveation.rs), on a minute of gaze: fixations of
        150-450 ms, saccades of 2-15 degrees, 0.7 degrees of tracker noise, 75 frames a second, a 0.40 region."""
        import math
        import random

        def run(comfort):
            rng = random.Random(7)
            size, deadband = 0.40, 0.025
            threshold = max(deadband, min(max(comfort, 0.0), 0.9) * size / 2)
            middle, moves, yaw, pitch, next_saccade, worst = None, 0, 0.0, 0.0, 0, 1.0
            for frame in range(75 * 60):
                if frame >= next_saccade:
                    amplitude, angle = 2 + 13 * rng.random(), 2 * math.pi * rng.random()
                    yaw = max(-25.0, min(25.0, yaw + amplitude * math.cos(angle)))
                    pitch = max(-20.0, min(20.0, pitch + amplitude * math.sin(angle)))
                    next_saccade = frame + int(150 + 300 * rng.random()) * 75 // 1000
                # About 95 degrees across the eye image: one degree is about 1/95 of it.
                gaze = (0.5 + (yaw + rng.gauss(0, 0.7)) / 95, 0.5 + (pitch + rng.gauss(0, 0.7)) / 95)
                if middle is None or any(abs(g - m) > threshold for g, m in zip(gaze, middle)):
                    middle, moves = gaze, moves + 1
                truth = (0.5 + yaw / 95, 0.5 + pitch / 95)
                worst = min(worst, min(size / 2 - abs(t - m) for t, m in zip(truth, middle)))
            return moves / 60, worst
        before, _ = run(0.0)
        after, margin = run(0.5)
        self.assertGreater(before, 2.5)
        self.assertLess(after, before / 2.5, (before, after))
        self.assertGreater(margin, 0.07)  # the gaze keeps a sharp margin of several degrees


# ------------------------------------------------------------------------------------------------ eye cameras
class EyeCameraTests(unittest.TestCase):
    def test_the_helper_passes_frames_on_at_the_cameras_fastest_rate(self):
        main = read('third_party/go-bsb-cams/main.go')
        for words in ('picture, err := cleanJPEG(fr.Bytes())', 'resp, err := iface.ClaimFrameReaderAt(fd.Index(), frd.Index(), wanted)',
                      'if resp, err = iface.ClaimFrameReader(fd.Index(), frd.Index()); err != nil {',
                      'errors.Is(err, transfers.ErrDamagedFrame)', 'stallLimit   = 2 * time.Second',
                      'log.Print(stats.line(now))', 'resp.SetTimeout(readTimeout)'):
            self.assertIn(words, main)
        # No frame is decoded and encoded again on the way, except the rare one Baballonia would misread.
        loop = main[main.index('func streamFrom('):]
        self.assertNotIn('jpeg.Encode(', loop)
        self.assertIn('if picture.EarlyEnd || len(data) > maxPassOn {', loop)
        self.assertIn('const frameInterval = 8 * time.Millisecond', read('third_party/go-bsb-cams/mjpeg_stream.go'))
        vendor = 'third_party/go-bsb-cams/vendor/github.com/kevmo314/go-uvc/'
        reader = read(vendor + 'pkg/transfers/isochronous_reader.go')
        self.assertIn('// VROSTBridge change: skip the damaged packet and say so; the next Read goes on.', reader)
        self.assertIn('C.libusb_cancel_transfer(t)', reader)
        self.assertIn('func (si *StreamingInterface) ClaimFrameReaderAt(', read(vendor + 'pkg/transfers/claim_interval.go'))
        self.assertIn('C.libusb_exit(d.usbctx)', read(vendor + 'uvc.go'))
        changes = read('third_party/go-bsb-cams/VROSTBRIDGE_CHANGES.md')
        for words in ('(1.0.0.5)', 'claim_interval.go', 'STATS', 'GET_MAX'):
            self.assertIn(words, changes)

    def test_the_helpers_go_tests(self):
        go = shutil.which('go') or ('/usr/local/go/bin/go' if Path('/usr/local/go/bin/go').exists() else None)
        if not go or subprocess.run(['pkg-config', '--exists', 'libusb-1.0']).returncode != 0:
            self.skipTest('Go or libusb-1.0 development files are not installed')
        result = subprocess.run([go, 'test', '-mod=vendor', '-vet=off', '-count=1',
                                 '-run', 'Tables|Frames|Frame|Broken|Stats|Interval|CutShort', '.'],
                                cwd=ROOT / 'third_party/go-bsb-cams', capture_output=True, text=True, timeout=600,
                                env=dict(__import__('os').environ, CGO_ENABLED='1'))
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

    def test_the_companion_reads_the_helpers_counts(self):
        stats = ('2026/10/07 21:00:00 STATS fps=89.8 sent=449 received=450 window_sent=449 window_received=450 '
                 'damaged_frames=1 lost_packets=3 invalid=0 undecodable=0 tables_added=0 reencoded=0 reopened=0 '
                 'max_gap_ms=23 interval_us=11111 size=800x400')
        kind, values = helper_values(stats)
        self.assertEqual(kind, 'STATS')
        self.assertEqual((values['fps'], values['damaged_frames'], values['size']), (89.8, 1, [800, 400]))
        kind, values = helper_values('2026/10/07 21:00:00 CAMERA mjpeg 800x400 intervals_us=8333,16666 default_us=16666 '
                                     'asked_us=8333 negotiated_us=8333 chosen=fastest max_frame=640000 max_payload=3072')
        self.assertEqual((kind, values['size'], values['negotiated_us'], values['chosen']), ('CAMERA', [800, 400], 8333, 'fastest'))
        self.assertIsNone(helper_values('Server Is Running And Can Be Accessed At: http://localhost:8880/stream'))
        cameras = EyeCameras('/nonexistent', tempfile.gettempdir(), log=Mock())
        self.assertTrue(cameras.note_line(stats))
        self.assertTrue(cameras.note_line('2026/10/07 21:00:00 CAMERA mjpeg 800x400 negotiated_us=8333 chosen=fastest'))
        self.assertTrue(cameras.note_line('2026/10/07 21:00:05 CAMERA opening the camera again (no frame for 2s)'))
        self.assertFalse(cameras.note_line('Stream: 127.0.0.1:5555 connected'))
        status = cameras.status()
        self.assertEqual(status['feed']['fps'], 89.8)
        self.assertEqual(status['camera']['negotiated_us'], 8333)  # the opening line does not replace it
        cameras.feed_at -= 60
        self.assertNotIn('feed', cameras.status())  # an old count says nothing about now

    def test_stats_lines_are_kept_as_values_not_logged(self):
        log = Mock()
        cameras = EyeCameras('/nonexistent', tempfile.gettempdir(), log=log)
        lines = (['2026/10/07 21:00:00 Server Is Running And Can Be Accessed At: http://localhost:8880/stream'] +
                 ['2026/10/07 21:00:0%d STATS fps=60.0 sent=%d received=%d' % (i % 10, i, i) for i in range(40)] +
                 ['2026/10/07 21:00:00 CAMERA mjpeg 800x400 negotiated_us=16666 chosen=device'])
        process = Mock()
        process.stdout = iter(line + '\n' for line in lines)
        process.wait.return_value = 0
        cameras.process = process
        cameras._read(process)
        logged = [call.args[0] for call in log.call_args_list]
        self.assertFalse([line for line in logged if 'STATS' in line])
        self.assertTrue([line for line in logged if 'CAMERA mjpeg' in line])
        self.assertTrue([line for line in logged if 'Server Is Running' in line])

    def test_reports_carry_the_cameras_frame_rate(self):
        timeline = SessionTimeline(clock=lambda: 50.0)
        timeline.camera({'fps': 88.0, 'damaged_frames': 2, 'reopened': 0}, now=50.0)
        timeline.camera({'fps': 92.0, 'damaged_frames': 5, 'reopened': 1}, now=51.0)
        row = timeline.snapshot()['rows'][0]
        self.assertEqual((row['camera_fps'], row['camera_lost'], row['camera_reopened']), (90.0, 3, 1))
        app = read('launcher/app.py')
        self.assertIn("self.timeline.camera(eye.cameras.status().get('feed'))", app)
        self.assertIn('camera frames a second', read('launcher/desktop.html'))


class EyeFilterTests(unittest.TestCase):
    RELEASE = {'version': '9.9.9', 'url': 'https://example.invalid/b.tar.xz', 'size': 10, 'sha256': 'a' * 64,
               'extracted_mb': 1, 'capture': 'IpCameraCapture'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data/vrostbridge/baballonia'
        self.config = Path(self.temp.name) / 'config/ProjectBabble'
        folder = self.root / '9.9.9'
        folder.mkdir(parents=True)
        (folder / baballonia.EXECUTABLE).write_text('#!/bin/sh\n')
        (folder / 'vrostbridge-receipt.json').write_text(json.dumps({'version': '9.9.9', 'url': 'x', 'sha256': 'a' * 64}))
        (folder / 'LocalSettings.json').write_text(json.dumps(dict(baballonia.BABALLONIA_EYE_FILTER)))

    def manager(self):
        manager = BaballoniaManager(log=Mock(), root=self.root, config=self.config, state=Path(self.temp.name) / 'setup.json',
                                    release=self.RELEASE, vulkan_layer=None)
        manager.running = lambda *args, **kwargs: False
        return manager

    def configure(self, manager, settings=None, setup=None):
        if settings is not None:
            manager.settings_path.parent.mkdir(parents=True, exist_ok=True)
            manager.settings_path.write_text(json.dumps(settings))
        if setup is not None:
            (Path(self.temp.name) / 'setup.json').write_text(json.dumps(setup))
        manager.configure()
        return json.loads(manager.settings_path.read_text())

    def test_two_camera_frames_after_an_eye_movement(self):
        self.assertEqual((baballonia.EYE_FILTER['AppSettings_OneEuroMinFreqCutoff'],
                          baballonia.EYE_FILTER['AppSettings_OneEuroSpeedCutoff']), (2.0, 60.0))
        self.assertEqual(baballonia.EYE_FILTER_VERSION, 2)

    def test_1_0_0_3s_filter_is_replaced_once_and_a_chosen_one_kept(self):
        earlier = dict(baballonia.EARLIER_EYE_FILTERS[0])
        manager = self.manager()
        settings = self.configure(manager, earlier, {'eye_filter_set': True})
        for key, value in baballonia.EYE_FILTER.items():
            self.assertEqual(settings[key], value, key)
        self.assertEqual(json.loads((Path(self.temp.name) / 'setup.json').read_text())['eye_filter_version'], 2)
        # Changed in Baballonia afterwards (even back to 1.0.0.3's values): kept from now on.
        settings = self.configure(manager, dict(settings, **earlier))
        self.assertEqual(settings['AppSettings_OneEuroSpeedCutoff'], 10.0)
        # Someone's own filter is never replaced.
        other = self.manager()
        (Path(self.temp.name) / 'setup.json').unlink()
        chosen = dict(baballonia.BABALLONIA_EYE_FILTER, AppSettings_OneEuroMinFreqCutoff=0.8, AppSettings_OneEuroSpeedCutoff=5.0)
        settings = self.configure(other, chosen, {'eye_filter_set': True})
        self.assertEqual((settings['AppSettings_OneEuroMinFreqCutoff'], settings['AppSettings_OneEuroSpeedCutoff']), (0.8, 5.0))

    def test_a_first_setup_gets_the_new_filter(self):
        manager = self.manager()
        settings = self.configure(manager)
        self.assertEqual(settings['AppSettings_OneEuroSpeedCutoff'], 60.0)
        self.assertTrue(any('eye filter set to follow quick eye movements' in call.args[0] for call in manager.log.call_args_list))


# --------------------------------------------------------------------------------------- the headset PC's graphics
class GraphicsTests(unittest.TestCase):
    def test_a_stream_larger_than_steamvrs_size_is_shown_at_it(self):
        conversion = read('client/device/conversion.hpp')
        self.assertIn('inline EyeSize shownEyeSize(uint32_t streamWidth, uint32_t streamHeight, uint32_t capWidth, uint32_t capHeight) {',
                      conversion)
        main = read('client/platform/linux/main.cpp')
        self.assertIn('vrost::shownEyeSize(width, height, runtime.width, runtime.height);', main)
        self.assertIn('<<",\\"shown_width\\":"<<decoder->shownWidth()', main)
        self.assertIn('add_test(NAME shown_size_contracts COMMAND shown_size_tests)', read('CMakeLists.txt'))
        self.assertIn('shown at ${details.shown_width} × ${details.shown_height}', read('launcher/desktop.html'))

    def test_a_gpu_held_at_a_fixed_clock_gets_the_one_step_fix(self):
        snapshot = {'episodes': 1, 'limited': True, 'recent_throttled_s': 12, 'window_s': 30}
        starved = {'clock_mhz': 1100, 'max_mhz': 2700, 'power_w': 15}
        offered = graphics_limit.advice(snapshot, 3200, 75, starved=starved, gpu_auto=True)
        self.assertIn('gpu-auto', offered['actions'])
        self.assertIn('Set the GPU clock to automatic', offered['message'])
        self.assertNotIn('gpu-auto', graphics_limit.advice(snapshot, 3200, 75, starved=starved)['actions'])
        self.assertNotIn('gpu-auto', graphics_limit.advice(snapshot, 3200, 75, gpu_auto=True)['actions'])
        self.assertIn("id=\"graphics-gpu-auto\" onclick=\"act('gpu-auto')\"", read('launcher/desktop.html'))
        self.assertIn("elif 'gpu-auto' in ((s.get('graphics') or {}).get('actions') or []):", read('launcher/game_ui.py'))


# ------------------------------------------------------------------------------------------------ network test
def step(rate, loss=0.0, frames=0.0, spread=2.0, local=0, network=None, complete=75, warmup=False, number=0):
    """One judged step result (75 frames, 13.3 ms apart, as at 75 Hz); network: loss on the way (default all)."""
    return {'step': number, 'rate_mbps': rate, 'packet_loss_pct': loss,
            'network_loss_pct': loss if network is None else network,
            'frame_loss_pct': frames, 'spread_p95_ms': spread, 'frame_interval_ms': 13.333, 'packets_expected': 1000,
            'packets_received': 1000, 'local_drops': local, 'frames_expected': 75, 'frames_complete': complete,
            'achieved_mbps': rate, 'warmup': warmup}


class NetworkTestTests(unittest.TestCase):
    """The 1.0.0.4 test on a USB adapter: a few packets lost at 80 Mbit/s, frames read late from 150, 30 Mbit/s."""

    R1004_TEST = [step(80, .0417, 4, 7.744), step(150, 0, 0, 11.472), step(200, .0112, 1.333, 14.167),
                  step(250, .4161, 4, 21.443), step(300, .0074, 1.333, 18.244), step(400, 22.49, 42.67, 24.5),
                  step(500, 41.06, 94.67, 26.1), step(600, 39.03, 96, 28.0), step(750, 63.79, 100, 32.8),
                  step(900, 67.18, 100, 30.5)]

    def setUp(self):
        from launcher import link_test, network_test
        self.link_test, self.network_test = link_test, network_test

    def receive_socket(self, buffer_bytes=8 << 20):
        sock = self.link_test.open_receiver('127.0.0.1', 0, buffer_bytes)
        self.addCleanup(sock.close)
        return sock

    def send(self, receiver_socket, test_id, steps, fps=30, step_seconds=0.3):
        sender_socket = self.link_test.open_sender()
        self.addCleanup(sender_socket.close)
        return self.link_test.LinkTestSender(sender_socket, receiver_socket.getsockname(), test_id, fps, steps,
                                             step_seconds=step_seconds).run()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'kernel arrival times are read on Linux')
    def test_arrival_times_come_from_the_kernel_not_from_when_the_companion_read(self):
        # Every datagram is read only after the whole step was sent, as when the receiving thread waits its
        # turn: read times would put every frame at once (jitter of whole frame intervals); the kernel's
        # times keep the frames 33 ms apart as sent.
        stamped = self.receive_socket()
        receiver = self.link_test.LinkTestReceiver(stamped, 7, fps=30, steps=[5])
        self.assertEqual((receiver.arrival_clock, receiver.counts_local_drops), ('kernel', True))
        self.send(stamped, 7, [5])
        [row] = receiver.run(2)
        self.assertEqual((row['frames_complete'], row['packet_loss_pct'], row['arrival_clock']), (9, 0.0, 'kernel'))
        self.assertLess(row['jitter_p95_ms'], 10.0)
        unstamped = self.receive_socket()
        with patch.object(sys, 'platform', 'win32'):
            receiver = self.link_test.LinkTestReceiver(unstamped, 8, fps=30, steps=[5])
        self.assertEqual(receiver.arrival_clock, 'companion')
        self.send(unstamped, 8, [5])
        [row] = receiver.run(2)
        self.assertGreater(row['jitter_p95_ms'], 25.0, 'read all at once: each frame seems a whole interval off')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'the socket reports its own drops on Linux')
    def test_datagrams_the_receive_buffer_dropped_are_not_network_loss(self):
        sock = self.receive_socket(buffer_bytes=4096)
        receiver = self.link_test.LinkTestReceiver(sock, 9, fps=30, steps=[5])
        self.send(sock, 9, [5])  # nothing is read while 135 datagrams arrive: the tiny buffer drops most
        [row] = receiver.run(2)
        if row['local_drops'] == 0:
            # Some kernels (GitHub's hosted runners among them) neither overflow nor report a drop for this socket;
            # the counting itself is covered by the tests below, which feed the receiver directly.
            self.skipTest('this kernel reported no datagrams dropped by the tiny receive buffer')
        self.assertGreater(row['local_drops'], 100)
        self.assertEqual(row['local_drops'], row['packets_expected'] - row['packets_received'])
        self.assertGreater(row['packet_loss_pct'], 50.0)
        self.assertEqual(row['network_loss_pct'], 0.0)
        self.assertEqual(self.link_test.step_verdict(row), (False, 'receiver'), 'too few frames to judge their timing')

    def test_local_drops_are_counted_per_step_and_frames_they_cost_do_not_fail_the_network(self):
        receiver = self.link_test.LinkTestReceiver(self.receive_socket(), 42, fps=100, steps=[100, 200])
        pack = self.link_test.pack_datagram
        for frame in range(10):
            for packet in range(5):
                if frame == 4 and packet == 2:
                    continue  # dropped by this PC's buffer: the next datagram read says so
                drops = 1 if (frame, packet) >= (4, 3) else None
                receiver._accept(pack(0, 42, frame, packet, 5, frame * 10_000_000), 1, None,
                                 5_000_000_000 + frame * 10_000_000 + packet * 10_000, drops)
        receiver._accept(pack(1, 42, 0, 0, 5, 0), 2, None, 6_000_000_000, 1)
        receiver._accept(pack(1, 42, 0, 1, 5, 0), 2, None, 6_000_010_000, 3)  # two of step 1's own: packets 2, 3
        first, second = receiver.results()
        self.assertEqual((first['local_drops'], first['packet_loss_pct'], first['network_loss_pct']), (1, 2.0, 0.0))
        self.assertEqual(first['frame_loss_pct'], 10.0)
        self.assertEqual(self.link_test.step_verdict(first), (True, ''), 'nine of ten frames arrived on time')
        self.assertEqual(second['local_drops'], 2)
        lost_on_the_way = dict(first, network_loss_pct=1.0)
        self.assertEqual(self.link_test.step_verdict(lost_on_the_way), (False, 'loss'))
        self.assertEqual(self.link_test.step_verdict(dict(first, local_drops=0, network_loss_pct=None)), (False, 'loss'))

    def test_drops_reported_with_the_next_steps_first_datagram_belong_to_the_step_before(self):
        # The receiver stalls at the end of step 0: its last two frames and all three end markers are dropped
        # here (13 datagrams). The count comes with step 1's first datagram read, which must not take them:
        # step 1 then also loses 2 packets on the way, which would have been hidden (the 1.0.0.5 review).
        receiver = self.link_test.LinkTestReceiver(self.receive_socket(), 42, fps=100, steps=[100, 100])
        pack = self.link_test.pack_datagram
        for frame in range(8):
            for packet in range(5):
                receiver._accept(pack(0, 42, frame, packet, 5, frame * 10_000_000), 1, None, 5_000_000_000 + frame * 10_000_000)
        for frame in range(10):
            for packet in range(5):
                if (frame, packet) in ((3, 1), (6, 4)):
                    continue  # lost on the way
                receiver._accept(pack(1, 42, frame, packet, 5, frame * 10_000_000), 2, None, 7_000_000_000 + frame * 10_000_000,
                                 13 if (frame, packet) == (0, 0) else None)
        receiver._accept(self.link_test.pack_header(1, 42, 10, 0, 5, 0, flags=self.link_test.FLAG_STEP_END), 3)
        first, second = receiver.results()
        self.assertEqual((first['local_drops'], second['local_drops']), (13, 0))
        self.assertEqual(second['network_loss_pct'], 4.0)
        self.assertEqual(self.link_test.step_verdict(second), (False, 'loss'))
        # Step 0 had no end marker: the host's summary says it sent 10 frames, so its last two count as lost,
        # here (local drops), not on the way.
        sender = {'steps': [{'step': 0, 'rate_mbps': 100, 'frames_sent': 10, 'packets_per_frame': 5, 'packets_sent': 50,
                             'achieved_mbps': 100.0, 'send_errors': 0},
                            {'step': 1, 'rate_mbps': 100, 'frames_sent': 10, 'packets_per_frame': 5, 'packets_sent': 50,
                             'achieved_mbps': 100.0, 'send_errors': 0}]}
        first, second = self.link_test.merge_sender([first, second], sender)
        self.assertEqual((first['frames_expected'], first['packets_expected'], first['unseen_tail_frames']), (10, 50, 2))
        self.assertEqual((first['packet_loss_pct'], first['network_loss_pct'], first['frame_loss_pct']), (20.0, 0.0, 20.0))
        self.assertNotIn('unseen_tail_frames', second, 'its end marker arrived')
        # A count with a later datagram of the next step: at most the datagrams of that step sent before it are its.
        receiver = self.link_test.LinkTestReceiver(self.receive_socket(), 43, fps=100, steps=[100, 100])
        receiver._accept(pack(0, 43, 0, 0, 5, 0), 1, None, 1)
        receiver._accept(pack(1, 43, 1, 2, 5, 0), 2, None, 2, 10)  # step 1's datagrams before it: 7
        first, second = receiver.results()
        self.assertEqual((first['local_drops'], second['local_drops']), (3, 7))
        receiver._accept(pack(1, 43, 1, 3, 5, 0), 2, None, 3, 12)
        self.assertEqual(receiver.results()[1]['local_drops'], 9, 'within one step, all its own')
        receiver._accept(b'not a test datagram' * 3, 4, None, None, 20)  # a count on a foreign datagram waits
        receiver._accept(pack(1, 43, 1, 4, 5, 0), 2, None, 5, 20)
        self.assertEqual(receiver.results()[1]['local_drops'], 17)

    def test_the_first_step_warms_the_path_and_is_not_judged(self):
        rows = [step(80, 0.2, 4, warmup=True), step(80, number=1), step(150, number=2)]
        result = self.link_test.recommend(rows, link_kind='wired')
        self.assertEqual((result['clean_mbps'], result['recommended_mbps']), (150, 100))
        self.assertIsNone(self.link_test.lossless_mbps(rows))
        ended = self.link_test.recommend([dict(step(80, warmup=True), packets_received=500),
                                          dict(step(80, number=1), packets_received=0)], link_kind='wired')
        self.assertIsNone(ended['recommended_mbps'])
        self.assertIn('ended before it measured anything', ended['message'])
        source = read('launcher/network_test.py')
        self.assertIn('plan = steps[:1] * WARMUP_STEPS + steps', source)
        self.assertIn("row['warmup'] = type(row.get('step')) is int and row['step'] < WARMUP_STEPS", source)

    def test_the_pace_follows_steady_loss_not_a_stray_packet(self):
        self.assertEqual(self.link_test.lossless_mbps(self.R1004_TEST), 300, '400 Mbit/s lost 22 %')
        self.assertEqual(self.link_test.lossless_mbps([step(80, .097, 1.33), step(150), step(300), step(400, 1.17, 2.7)]),
                         300, 'one packet lost at 80 Mbit/s no longer hides the limit')
        self.assertIsNone(self.link_test.lossless_mbps([step(80, 5.0, 40), step(150)]))
        self.assertIsNone(self.link_test.lossless_mbps([step(rate) for rate in (80, 150, 300)]))
        self.assertEqual(self.link_test.lossless_mbps([step(80), step(150, 30.0, 50, local=900, network=0.0)]), None,
                         "this PC's own drops are not the path's limit")

    def test_the_1_0_0_4_test_gives_a_usable_bitrate_once_frames_are_timed_on_arrival(self):
        recorded = self.link_test.recommend(self.R1004_TEST, 300, 'wired', 920)
        self.assertEqual((recorded['recommended_mbps'], recorded['clean_mbps']), (30, 0), 'as it was read')
        on_time = [dict(row, spread_p95_ms=row['rate_mbps'] / 75 / 920 * 1000) for row in self.R1004_TEST]
        result = self.link_test.recommend(on_time, 300, 'wired', 920)
        # 1.0.0.8: paced at 70 % of the 300 Mbit/s that arrived without loss, so a little less per frame.
        self.assertEqual((result['clean_mbps'], result['recommended_mbps'], result['lossless_mbps']), (150, 80, 300))

    def test_a_wired_bitrate_leaves_room_in_the_pace(self):
        rows = [step(rate) for rate in (80, 150, 200, 300, 400, 500)] + [step(600, .03, 2.7), step(750, 12.4, 76)]
        result = self.link_test.recommend(rows, 300, 'wired', 920)
        self.assertEqual((result['clean_mbps'], result['lossless_mbps']), (500, 600))
        # 1.0.0.8: paced at 70 % of the loss-free 600 Mbit/s.
        self.assertEqual((result['recommended_mbps'], result['limited_by']), (160, 'latency'))
        self.assertEqual(result['message'], 'Network test: clean up to 500 Mbit/s on Ethernet; the video is paced at '
                                            '420 Mbit/s on this path, so using 160 Mbit/s keeps each frame quick to arrive.')
        clean = [step(rate) for rate in (80, 150, 200, 300, 400, 500, 600, 750, 900)]
        self.assertEqual(self.link_test.recommend(clean, 300, 'wired', 920)['recommended_mbps'], 300, 'no limit found')
        self.assertEqual(self.link_test.recommend(rows, 300, 'wired', 0)['recommended_mbps'], 300, 'unpaced link')
        self.assertEqual(self.link_test.recommend(rows, 300, 'wifi', 920)['recommended_mbps'], 300, 'Wi-Fi unchanged')
        self.assertEqual(self.link_test.SEND_FRACTION, 0.4)
        source = read('launcher/network_test.py')
        self.assertIn("recommend(rows, codec_cap_mbps, link['kind'], pace if link['kind'] == 'wired' else 0)", source)

    def test_a_test_that_found_no_clean_rate_is_tested_again_and_an_earlier_result_kept(self):
        remember, needs_retest = self.network_test.remember, self.network_test.needs_retest
        good = {'host_id': 'h', 'interface': 'enp1', 'time': 100,
                'recommendation': {'recommended_mbps': 100, 'clean_mbps': 150, 'lossless_mbps': 300, 'message': 'good'}}
        bad = {'host_id': 'h', 'interface': 'enp1', 'time': 200,
               'recommendation': self.link_test.recommend(self.R1004_TEST, 300, 'wired', 920)}
        saved = remember({}, good)
        self.assertFalse(needs_retest(saved['h|enp1']))
        saved = remember(saved, bad)
        entry = saved['h|enp1']
        self.assertEqual((entry['recommended_mbps'], entry['time'], entry['failures']), (100, 100, 1))
        self.assertIn('losing data even at 80 Mbit/s', entry['message'])
        self.assertIn('keeps the earlier result (100 Mbit/s) and tests the network again before the next session',
                      entry['message'])
        self.assertTrue(needs_retest(entry))
        saved = remember(saved, dict(bad, time=300))
        self.assertEqual((saved['h|enp1']['recommended_mbps'], saved['h|enp1']['failures']), (100, 2))
        saved = remember(saved, dict(bad, time=400))  # the third in a row stands
        entry = saved['h|enp1']
        self.assertEqual((entry['recommended_mbps'], entry['time'], entry['failures']), (30, 400, 3))
        self.assertFalse(needs_retest(entry))
        self.assertNotIn('tested again', entry['message'])
        saved = remember(saved, dict(good, time=500))
        self.assertEqual((saved['h|enp1']['recommended_mbps'], saved['h|enp1']['failures']), (100, 0))
        # Without an earlier clean result, and a test that received nothing, also count.
        first = remember({}, bad)['h|enp1']
        self.assertEqual((first['recommended_mbps'], first['failures']), (30, 1))
        self.assertIn('The network is tested again before the next session.', first['message'])
        nothing = remember({}, dict(bad, recommendation=self.link_test.recommend([])))['h|enp1']
        self.assertTrue(needs_retest(nothing))
        # The 1.0.0.4 result in the report: saved before 1.0.0.5, no clean rate.
        report = {'time': 1791395359.66, 'recommended_mbps': 30, 'clean_mbps': 0, 'limited_by': 'network',
                  'link': {'kind': 'wired', 'speed_mbps': 1000, 'usb_adapter': True}, 'pace_mbps': 920,
                  'lossless_mbps': None}
        self.assertTrue(needs_retest(report))
        self.assertFalse(needs_retest({'time': 1, 'recommended_mbps': 100, 'lossless_mbps': 500}))
        # An earlier result too old to be used any more is not kept: the new one stands (the 1.0.0.5 review).
        month = self.network_test.MAX_AGE_S
        stale = remember(remember({}, good), dict(bad, time=100 + month + 86400))['h|enp1']
        self.assertEqual((stale['recommended_mbps'], stale['failures']), (30, 1))
        self.assertIn('The network is tested again before the next session.', stale['message'])
        from launcher import app
        self.assertEqual(app.NETWORK_TEST_MAX_AGE, month)

    def test_the_session_start_tests_again_after_a_test_without_a_clean_rate(self):
        from launcher.app import Application
        host = {'host_id': 'h', 'features': ['link-test']}
        logs, runs = [], []

        def fake(saved):
            link = SimpleNamespace(source_path=lambda ip: (host, '192.168.1.2', 'enp1'))
            return SimpleNamespace(session_link=link, settings={'host_ip': '', 'network_tests': {'h|enp1': saved}},
                                   log=logs.append, run_network_test=lambda cancel: runs.append(1) or {'new': True})
        failed = {'time': time.time(), 'recommended_mbps': 30, 'clean_mbps': 0, 'lossless_mbps': None,
                  'link': {'kind': 'wired'}}
        self.assertEqual(Application.ensure_network_test(fake(failed), None), {'new': True})
        self.assertIn('found no rate it carried cleanly; testing it again before this session (test 2 in a row)', logs[-1])
        kept = dict(failed, recommended_mbps=100, clean_mbps=150, failures=2)
        self.assertEqual(Application.ensure_network_test(fake(kept), None), {'new': True})
        self.assertIn('(test 3 in a row)', logs[-1])
        stands = dict(failed, failures=3)
        self.assertEqual(Application.ensure_network_test(fake(stands), None), stands)
        good = dict(failed, recommended_mbps=100, clean_mbps=150, failures=0)
        self.assertEqual(Application.ensure_network_test(fake(good), None), good)
        self.assertEqual(len(runs), 2)
        old = dict(good, time=time.time() - 40 * 24 * 3600)
        Application.ensure_network_test(fake(old), None)
        self.assertIn('is over 30 days old', logs[-1])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'kernel arrival times are read on Linux')
    def test_a_whole_test_runs_with_a_warm_up_and_short_thread_switches(self):
        from host.stream_options import run_link_test
        from launcher.network_test import NetworkTest
        seen = {}

        class Link:
            def source_path(self, host_ip):
                return {'host_id': 'h', 'host_ip': '127.0.0.1', 'features': ['link-test']}, '127.0.0.1', 'lo'

            def link_test(self, request, timeout, host_ip):
                seen['switch'] = sys.getswitchinterval()
                seen['steps'] = list(request['steps'])
                return None, run_link_test(request, '127.0.0.1')

        before = sys.getswitchinterval()
        tester = NetworkTest(Link(), log=lambda line: None, receiver_socket=lambda ip, port: self.receive_socket())
        with patch('launcher.network_test.plan_steps', return_value=[20, 40]):
            result = tester.run('127.0.0.1', fps=60)
        self.assertEqual(seen, {'switch': 0.0005, 'steps': [20, 20, 40]})
        self.assertEqual(sys.getswitchinterval(), before)
        self.assertEqual([row['warmup'] for row in result['steps']], [True, False, False])
        self.assertEqual(result['receiver']['arrival_clock'], 'kernel')
        self.assertTrue(result['receiver']['counts_local_drops'])
        self.assertIn('local_drops', result['steps'][1])
        self.assertIn('about 15 seconds', read('launcher/network_test.py'))


if __name__ == '__main__':
    unittest.main()
