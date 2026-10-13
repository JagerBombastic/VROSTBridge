"""VROSTBridge 1.0.0.9: sound without breakups, and fewer stutters.

The 1.0.0.8 test streamed High (2560) with eye-tracked foveation smoothly apart from a few short stutters, but the game
audio lagged, broke up and crackled. The client played it through rodio at 44.1 kHz (PipeWire's default through ALSA)
while the host sent 48 kHz, converted by dropping samples; its jitter buffer emptied on every lost packet and drifted
between a long delay and a skip. 1.0.0.9 plays at the stream's own rate with an adaptive jitter buffer and drift
correction (patch 0022), captures the host's sound straight from WASAPI (silent packets as silence, a surround mix
folded by speaker position; patch 0023), and sends parity with video and game audio so a lost packet is rebuilt
instead of costing a frame (a keyframe request and a freeze of 70-100 ms) or a piece of sound (patch 0021). Base
stations are reached one at a time, with one more try; Baballonia's service gets a low CPU weight; the host keeps the
newest three client reports.

The Rust parts have their own tests in the patched ALVR crates (alvr_sockets, alvr_audio).
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'packaging'))

from host import stream_options  # noqa: E402
from host.stream_options import driver_features, stream_config  # noqa: E402
from launcher import base_stations, reporting  # noqa: E402
from launcher.app import Application, timing_number  # noqa: E402
from launcher.base_stations import BaseStations  # noqa: E402
from launcher.reporting import ReceivedReports  # noqa: E402
from launcher.timeline import SessionTimeline  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from test_vrostbridge_1008 import STATION, FakeBlueZ, done  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


# ------------------------------------------------------------------------------------------------ version
class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_9_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1010.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 9))
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "' + HOST_VERSION + '"', read('core/version.hpp'))
        self.assertIn("'VROSTBridge " + HOST_VERSION + " / ALVR'", read('host/profile.py'))
        import build_windows_client
        self.assertEqual(build_windows_client.client_version(), CLIENT_VERSION)


# ------------------------------------------------------------------------------------------------ patches and driver options
class DriverOptionTests(unittest.TestCase):
    def test_the_new_patches_follow_the_series(self):
        names = sorted(path.name for path in (ROOT / 'patches').glob('*.patch'))
        self.assertEqual(len(names), 23)
        self.assertEqual(names[-3:], ['0021-transport-forward-error-correction.patch', '0022-client-audio-playback.patch',
                                      '0023-host-game-audio-capture.patch'])
        fec = read('patches/0021-transport-forward-error-correction.patch')
        self.assertIn('FEC_STREAM_FLAG: u16 = 0x8000', fec, 'parity on its own stream ID, so older clients drop it')
        playback = read('patches/0022-client-audio-playback.patch')
        for text in ('fn choose(device: &Device, stream_rate: u32)', 'MAX_CORRECTION: f64 = 0.005', 'pub const DEVICE_BUFFER_MS: u32 = 40',
                     'VROST_AUDIO_OUTPUT'):
            self.assertIn(text, playback)
        capture = read('patches/0023-host-game-audio-capture.patch')
        for text in ('AUDCLNT_BUFFERFLAGS_SILENT', 'AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY', 'w!("Pro Audio")',
                     'VROST_AUDIO_CAPTURE fallback=cpal', '+                AudioChannel::FrontLeft,\n'):
            self.assertIn(text, capture)

    def test_parity_is_sent_only_by_a_driver_that_has_it(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'vrostbridge-build.json'
            names = ['0020-host-colour-variants-and-gaze-reach.patch']
            manifest.write_text(json.dumps({'source_patch_sha256': {name: '0' * 64 for name in names}}))
            older = driver_features(directory)
            self.assertNotIn('parity', older)
            names += ['0021-transport-forward-error-correction.patch', '0022-client-audio-playback.patch',
                      '0023-host-game-audio-capture.patch']
            manifest.write_text(json.dumps({'source_patch_sha256': {name: '0' * 64 for name in names}}))
            newer = driver_features(directory)
        self.assertIn('parity', newer)
        self.assertIn('audio-capture', newer)
        preferences = {'pacing_mbps': 420, 'foveation': {'gaze': True}}
        config = stream_config(preferences, newer)
        self.assertEqual((config['fec_video_group'], config['fec_audio_group']), (10, 1))
        self.assertEqual((stream_options.FEC_VIDEO_GROUP, stream_options.FEC_AUDIO_GROUP), (10, 1))
        self.assertNotIn('fec_video_group', stream_config(preferences, older), 'an older driver is not told about parity')


# ------------------------------------------------------------------------------------------------ the host's received reports
class ReceivedReportTests(unittest.TestCase):
    def test_only_the_newest_three_are_kept_and_listed(self):
        self.assertEqual(reporting.KEEP_RECEIVED, 3)
        with tempfile.TemporaryDirectory() as directory:
            store = ReceivedReports(directory)
            for minute in range(5):
                store.save('VROSTBridge-Client-report_1.0.0.9_2026-10-09_12-0%d-00.json' % minute, {'minute': minute})
                time.sleep(0.02)
            self.assertEqual([row['name'][-13:-5] for row in store.list()], ['12-04-00', '12-03-00', '12-02-00'])
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 3)

    def test_a_folder_from_an_earlier_version_shrinks_when_the_host_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            for minute in range(8):
                path = Path(directory) / ('VROSTBridge-Client-report_1.0.0.8_2026-10-08_20-0%d-00.json' % minute)
                path.write_text('{}')
                stamp = 1790000000 + minute
                import os
                os.utime(path, (stamp, stamp))
            (Path(directory) / 'notes.txt').write_text('not a report')
            ReceivedReports(directory).prune()
            left = sorted(path.name for path in Path(directory).iterdir())
            self.assertEqual(left, ['VROSTBridge-Client-report_1.0.0.8_2026-10-08_20-05-00.json',
                                    'VROSTBridge-Client-report_1.0.0.8_2026-10-08_20-06-00.json',
                                    'VROSTBridge-Client-report_1.0.0.8_2026-10-08_20-07-00.json', 'notes.txt'])
            ReceivedReports(Path(directory) / 'missing').prune()  # nothing there yet: nothing to do
        self.assertIn('target=self.received_reports.prune', read('launcher/app.py'))


# ------------------------------------------------------------------------------------------------ base stations
class FlakyBlueZ(FakeBlueZ):
    """The first `failures` connections are aborted, as BlueZ did when two stations were connected at once."""
    def __init__(self, failures, **options):
        super().__init__(**options)
        self.failures = failures

    def __call__(self, arguments, timeout):
        if 'Connect' in arguments and self.failures > 0:
            self.calls.append(list(arguments))
            self.failures -= 1
            return done('', 1, 'Call failed: le-connection-abort-by-local')
        return super().__call__(arguments, timeout)


class BaseStationRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def stations(self, bluez):
        return BaseStations(self.state, log=lambda line: None, runner=bluez, busctl='busctl', bluetoothctl='bluetoothctl',
                            clock=lambda: 1790000000)

    @patch('launcher.base_stations.time.sleep', lambda seconds: None)
    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_a_station_that_did_not_answer_is_tried_once_more(self):
        bluez = FlakyBlueZ(1)
        outcome = self.stations(bluez).set_power('on')
        self.assertEqual(outcome['results'], [{'name': 'LHB-1A2B3C4D', 'result': 'sent', 'state': 'on'}])
        self.assertEqual(sum(1 for call in bluez.calls if 'Connect' in call), 2)
        self.assertEqual(bluez.written, [0x01])

    @patch('launcher.base_stations.time.sleep', lambda seconds: None)
    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_a_station_that_never_answers_is_reported_after_two_tries(self):
        bluez = FlakyBlueZ(5)
        outcome = self.stations(bluez).set_power('on')
        self.assertEqual(outcome['results'][0]['result'], 'failed')
        self.assertEqual(outcome['results'][0]['error'], 'Call failed: le-connection-abort-by-local')
        self.assertEqual(sum(1 for call in bluez.calls if 'Connect' in call), base_stations.ATTEMPTS)
        self.assertEqual(outcome['message'], '0 of 1 base stations turned on. Not reached: LHB-1A2B3C4D '
                                             '(Call failed: le-connection-abort-by-local).')

    def test_stations_are_reached_one_at_a_time(self):
        source = read('launcher/base_stations.py')
        self.assertNotIn('ThreadPoolExecutor', source)
        self.assertGreaterEqual(base_stations.ATTEMPTS, 2)  # 1.0.0.10: three tries
        self.assertGreaterEqual(base_stations.RETRY_S, 1.0)
        script = read('launcher/BaseStations.ps1')
        self.assertRegex(script, r"for \(\$attempt=1; \$attempt -le [23] -and \$row.result -ne 'sent'; \$attempt\+\+\) \{")
        import os
        import shutil
        pwsh = os.environ.get('PWSH') or shutil.which('pwsh')
        if not pwsh:
            self.skipTest('PowerShell is not installed here')
        check = ('$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("' +
                 str(ROOT / 'launcher/BaseStations.ps1') + '",[ref]$t,[ref]$e);if($e.Count){$e;exit 1}')
        result = subprocess.run([pwsh, '-NoProfile', '-Command', check], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


# ------------------------------------------------------------------------------------------------ Baballonia's share of the processor
class BaballoniaWeightTests(unittest.TestCase):
    def test_its_service_has_a_low_cpu_weight(self):
        from launcher import baballonia
        self.assertEqual(baballonia.CPU_WEIGHT, 25)
        self.assertIn("'-p', 'CPUWeight=' + str(CPU_WEIGHT)", read('launcher/baballonia.py'))
        self.assertIn("self.assertIn('CPUWeight=25', command)", read('tests/test_rc45.py'), 'the service test checks it')


# ------------------------------------------------------------------------------------------------ what the client shows
def timing_line(**values):
    base = {'cadence': 'full', 'unique_fps': '75.0', 'net_lost_frames': '0', 'net_keyframe_requests': '0',
            'net_skipped_until_keyframe': '0', 'presentation_incidents': '0', 'net_fec_video': '0', 'net_fec_video_total': '0',
            'audio_active': '1', 'audio_stream_hz': '48000', 'audio_device_hz': '48000', 'audio_channels': '2',
            'audio_buffer_ms': '52.37', 'audio_target_ms': '50', 'audio_correction_ppm': '-120', 'audio_underruns': '0',
            'audio_losses': '0', 'audio_underruns_total': '0'}
    base.update({key: str(value) for key, value in values.items()})
    return base


class StatusTests(unittest.TestCase):
    def test_game_audio_from_the_timing_line(self):
        app = types.SimpleNamespace(timings=timing_line(audio_underruns_total=2, audio_device_hz=44100))
        self.assertEqual(Application.game_audio_status(app), {'active': True, 'stream_hz': 48000, 'device_hz': 44100,
                                                              'channels': 2, 'buffer_ms': 52.4, 'target_ms': 50.0,
                                                              'dropouts': 2})
        self.assertEqual(Application.game_audio_status(types.SimpleNamespace(timings={})), {}, 'an older native client')
        self.assertEqual(Application.game_audio_status(types.SimpleNamespace(timings=None)), {})
        self.assertIsNone(timing_number({'x': 'nan'}, 'x'))
        self.assertIsNone(timing_number({'x': 'oops'}, 'x'))
        self.assertEqual(timing_number({'x': '3.0'}, 'x', int), 3)

    def test_the_bitrate_line_counts_frames_rebuilt_from_parity(self):
        governor = types.SimpleNamespace(snapshot=lambda: {'active': True, 'mbps': 55})
        app = types.SimpleNamespace(bitrate_governor=governor, host_feature_list=lambda: ['bitrate-control', 'parity'],
                                    timings=timing_line(net_fec_video_total=12))
        self.assertEqual(Application.bitrate_status(app), {'active': True, 'mbps': 55, 'host_applies': True,
                                                           'repaired_frames': 12})
        app.timings = {}
        self.assertNotIn('repaired_frames', Application.bitrate_status(app))

    def test_the_timeline_keeps_repairs_and_audio_dropouts(self):
        clock = [1000.0]
        timeline = SessionTimeline(clock=lambda: clock[0])
        timeline.timing(timing_line(net_fec_video=3, audio_underruns=1, audio_losses=2))
        clock[0] += 1
        timeline.timing(timing_line(net_fec_video=1))
        rows = timeline.snapshot()['rows']
        self.assertEqual((rows[-1]['repaired'], rows[-1]['audio_dropouts'], rows[-1]['audio_lost']), (4, 1, 2))

    def test_the_native_client_reports_playback_and_repairs(self):
        bridge = read('client/transport/bridge.h')
        self.assertIn('void vrost_audio_stats(void *, VrostAudioStats *);', bridge)
        self.assertIn('uint64_t fec_video_recovered, fec_audio_recovered;', bridge)
        self.assertIn('pub unsafe extern "C" fn vrost_audio_stats(', read('client/transport/lib.rs'))
        main = read('client/platform/linux/main.cpp')
        for field in ('" net_fec_video="', '" net_fec_video_total="', '" audio_buffer_ms="', '" audio_underruns="',
                      '" audio_underruns_total="', '" audio_correction_ppm="'):
            self.assertIn(field, main)


if __name__ == '__main__':
    unittest.main()
