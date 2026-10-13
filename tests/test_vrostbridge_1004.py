"""1.0.0.4: fixes from the 1.0.0.3 test reports, on both programs (both are 1.0.0.4).

- The sharp region left the gaze: the host returned it to the fixed centre 150 ms into every pause in the eye
  tracking (39 % of frames in both sessions of the test), so the user could look straight at the coarse area. The
  host now keeps it where the user last looked for a second (driver patch 0017, `gaze_hold_ms`).
- The processor was held at about 540 MHz for a whole session (below its own 1100 MHz minimum) because the GPU clock
  was fixed at its maximum: eye tracking fell from 54 to 12 samples a second and the stream stalled every few
  seconds. The client notices a processor held below its own minimum, says why and how to fix it, and offers to set
  the GPU clock to automatic where steamos-manager runs. Every threshold is relative to the PC's own clocks.
- One frame was lost every 3 s or so: video was paced at 92 % of a USB adapter's link speed while the network test
  showed loss above 500 Mbit/s on that path. Wired pacing now stays under the path's measured loss-free rate.
- The picture went grey 250 ms into any pause in the video; the last frame is now shown, re-aimed, for up to 1 s.
- Game Mode: left and right change the focused setting instead of jumping to Back, held directions repeat, one press
  reported by two controllers moves once, rows scrolled out of view are reached only by stepping through the list.
- Reports: the whole session in 10-second steps (frame rate, losses, stalls, grey frames, eye-tracking samples, the
  host's foveation, clocks, power and the processor time of the session's programs) and the pauses in the gaze.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher import game_ui, power_balance  # noqa: E402
from launcher.gaze_provider import GazeRelay  # noqa: E402
from launcher.link_test import lossless_mbps, recommend  # noqa: E402
from launcher.network_test import USB_PACING_FRACTION, lossless_rate, pacing_mbps, path_tested, remember  # noqa: E402
from launcher.performance_monitor import ProcessUse, process_group, process_ticks, processor_ticks  # noqa: E402
from launcher.power_balance import CpuClocks, SteamOSPower, advice  # noqa: E402
from launcher.timeline import BUCKET_S, MAX_ROWS, SessionTimeline  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from host.stream_options import GAZE_HOLD_MS, driver_features, stream_config  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_4_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1005.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 4))

    def test_the_release_notes_are_listed(self):
        self.assertIn('RELEASE_1.0.0.4.md', read('docs/DOCUMENTATION_INDEX.md'))
        notes = read('docs/history/RELEASE_1.0.0.4.md')
        for words in ('gaze_hold_ms', '0017-host-gaze-hold.patch', 'GPU clock', 'pacing', 'timeline', 'Game Mode'):
            self.assertIn(words, notes)


# ------------------------------------------------------------------------------------------------ the gaze hold
class GazeHoldTests(unittest.TestCase):
    def test_the_driver_patch_holds_the_region_and_counts_it(self):
        patch_text = read('patches/0017-host-gaze-hold.patch')
        for words in ('pub gaze_hold_ms: u32', 'DEFAULT_GAZE_HOLD_MS: u32 = 1000', 'pub held_frames: u64',
                      'input.stale + input.hold', 'held_frames={}', 'longest_pause_ms={}', 'gaze_hold_ms={}',
                      'fn a_pause_in_the_gaze_holds_the_region_then_returns_it', 'fn gaze_hold_defaults_and_bounds'):
            self.assertIn(words, patch_text)
        names = sorted(p.name for p in (ROOT / 'patches').glob('*.patch'))
        self.assertEqual(names[16], '0017-host-gaze-hold.patch')  # later patches follow (1.0.0.5: 0018, 0019)
        self.assertLess(names.index('0016-host-foveation-prefilter.patch'), names.index('0017-host-gaze-hold.patch'))

    def test_the_companion_asks_for_the_hold_only_from_a_driver_that_has_it(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {'source_patch_sha256': {'0010-host-gaze-foveated-encoding.patch': 'x',
                                                '0017-host-gaze-hold.patch': 'y'}}
            (Path(directory) / 'vrostbridge-build.json').write_text(json.dumps(manifest))
            features = driver_features(directory)
        self.assertIn('gaze-hold', features)
        preferences = {'foveation': {'gaze': True, 'optical_centre': True}}
        self.assertEqual(stream_config(preferences, features)['gaze_hold_ms'], GAZE_HOLD_MS)
        self.assertEqual(GAZE_HOLD_MS, 1000)
        # An older driver ignores the key anyway; it is left out so a driver default is never overridden by mistake.
        self.assertNotIn('gaze_hold_ms', stream_config(preferences, ['gaze-foveation', 'optical-centre']))

    def test_held_frames_count_as_following_the_eyes(self):
        app = read('launcher/app.py')
        self.assertIn("number(stream.get('gaze_gaze_frames'))+number(stream.get('gaze_held_frames'))", app)
        self.assertIn("following=int(gaze.get('gaze_frames',0))+int(gaze.get('held_frames',0))", app)
        for key in ("'held_frames'", "'pauses'", "'longest_pause_ms'", "'returns'", "'gaze_hold_ms'"):
            self.assertIn(key, app)


class GazeGapTests(unittest.TestCase):
    def relay(self):
        with patch('socket.socket') as fake:
            fake.return_value.bind.return_value = None
            return GazeRelay(None, port=0)

    def test_pauses_are_counted_by_length_and_reset_for_each_session(self):
        relay = self.relay()
        for gap in (.02, .02, .12, .3, 1.5, 6.0):
            relay.note_gap(gap)
        status = relay.status()['gaps']
        self.assertEqual((status['over_100ms'], status['over_250ms'], status['over_1s'], status['over_5s']), (4, 3, 2, 1))
        self.assertEqual(status['longest_ms'], 6000)
        relay.reset_gaps()
        self.assertEqual(relay.status()['gaps'], {'over_100ms': 0, 'over_250ms': 0, 'over_1s': 0, 'over_5s': 0,
                                                  'longest_ms': 0})

    def test_a_session_start_resets_them_and_reports_carry_them(self):
        tracking = read('launcher/eye_tracking.py')
        self.assertIn("self.session_gaps = True", tracking)
        self.assertIn("reset = getattr(self.relay, 'reset_gaps', None)", tracking)
        self.assertIn("'unsent', 'other', 'idle_s', 'gaps'", tracking)  # 1.0.0.6 adds 'reach'


class BaballoniaPriorityTests(unittest.TestCase):
    def test_baballonia_runs_one_step_down_in_priority(self):
        from launcher import baballonia
        self.assertEqual((baballonia.NICE, baballonia.NICENESS), ('/usr/bin/nice', 5))
        self.assertIn("lower = [NICE, '-n', str(NICENESS)] if Path(NICE).is_file() else []", read('launcher/baballonia.py'))

    def test_the_watched_program_really_runs_one_step_down(self):
        from launcher import baballonia
        if not Path(baballonia.NICE).is_file():
            self.skipTest('no ' + baballonia.NICE + ' here')
        with tempfile.TemporaryDirectory() as directory:
            report, program = Path(directory) / 'niceness', Path(directory) / 'Baballonia'
            program.write_text('#!/bin/sh\n"%s" > "%s"\n' % (baballonia.NICE, report))  # nice alone prints the niceness
            program.chmod(0o755)
            command = baballonia.watched([baballonia.NICE, '-n', str(baballonia.NICENESS), program], 1)
            self.assertEqual(subprocess.run(command, timeout=30).returncode, 0)
            self.assertEqual(int(report.read_text()), min(19, os.nice(0) + baballonia.NICENESS))


# ------------------------------------------------------------------------------------------------ the power limit
def sample(cpu, minimum=1100.0, maximum=5100.0, gpu_clock=2700, gpu_max=2700, busy=34, level='manual', power=26.6,
           when=None):
    return {'active': True, 'observed_monotonic': when if when is not None else time.monotonic(),
            'cpu_policies': [{'policy': 'policy%d' % i, 'reported_frequency_mhz': cpu + i * .01, 'minimum_mhz': minimum,
                              'maximum_mhz': maximum, 'governor': 'powersave'} for i in range(16)],
            'gpus': [{'card': 'card1', 'gpu_busy_percent': busy, 'selected_sclk_mhz': gpu_clock, 'max_sclk_mhz': gpu_max,
                      'performance_level': level, 'sensors': [{'clock_mhz': gpu_clock, 'average_power_w': power}]}]}


class PowerBalanceTests(unittest.TestCase):
    def test_a_processor_held_below_its_own_minimum_is_noticed_with_its_cause(self):
        clocks = CpuClocks()
        clocks.observe(sample(544, when=1))
        self.assertIsNone(clocks.starved(), 'two samples in a row')
        clocks.observe(sample(544, when=2))
        held = clocks.starved()
        self.assertEqual((held['cpu_mhz'], held['cpu_min_mhz'], held['cause']), (544, 1100, 'fixed-gpu-clock'))
        self.assertEqual((held['gpu_mhz'], held['gpu_max_mhz']), (2700, 2700))
        self.assertFalse(clocks.observe(sample(544, when=2)), 'the same sample twice is one sample')

    def test_thresholds_follow_each_processors_own_minimum(self):
        for minimum, clock, starved in ((1100, 800, True), (1100, 900, False), (400, 350, False), (400, 250, True),
                                        (2000, 1400, True)):
            clocks = CpuClocks()
            for when in (1, 2):
                clocks.observe(sample(clock, minimum=minimum, level='auto', gpu_clock=800, busy=70, when=when))
            self.assertEqual(bool(clocks.starved()), starved, (minimum, clock))
        clocks = CpuClocks()
        for when in (1, 2):  # no minimum reported: never called held
            row = sample(300, when=when)
            for policy in row['cpu_policies']:
                del policy['minimum_mhz']
            clocks.observe(row)
        self.assertIsNone(clocks.starved())

    def test_a_power_limit_without_a_fixed_gpu_clock(self):
        clocks = CpuClocks()
        for when in (1, 2):
            clocks.observe(sample(500, level='auto', gpu_clock=1200, gpu_max=2200, busy=90, when=when))
        self.assertEqual(clocks.starved()['cause'], 'power-limit')
        clocks = CpuClocks()
        for when in (1, 2):  # no performance level (other drivers): a clock at its maximum while idle looks fixed
            clocks.observe(sample(500, level=None, gpu_clock=1950, gpu_max=2000, busy=20, when=when))
        self.assertEqual(clocks.starved()['cause'], 'fixed-gpu-clock')

    def test_advice_is_worked_out_from_this_pcs_numbers(self):
        held = {'cpu_mhz': 544, 'cpu_min_mhz': 1100, 'cause': 'fixed-gpu-clock', 'gpu_mhz': 2700, 'gpu_max_mhz': 2700}
        result = advice(held, {'available': True, 'gpu_level': 'manual', 'tdp_w': 28})
        self.assertEqual(result['state'], 'starved')
        self.assertIn('about 544 MHz (its normal minimum is 1100 MHz)', result['message'])
        self.assertIn('fixed at 2700 MHz', result['message'])
        self.assertIn('(28 W)', result['message'])
        self.assertIn('about 1600-2000 MHz on this GPU', result['message'])
        self.assertEqual(result['actions'], ['gpu-auto'])
        other = advice(dict(held, gpu_mhz=1800, gpu_max_mhz=1800), {})
        self.assertIn('about 1100-1400 MHz on this GPU', other['message'])
        self.assertEqual(other['actions'], [], 'no steamos-manager: no one-click fix')
        self.assertEqual(advice(held, {'available': True, 'gpu_level': 'auto'})['actions'], [])
        limit = advice({'cpu_mhz': 600, 'cpu_min_mhz': 1400, 'cause': 'power-limit'}, {'available': True})
        self.assertIn('raise the TDP limit', limit['message'])
        self.assertEqual(limit['short'], 'CPU held at 600 MHz · raise the TDP limit')
        self.assertEqual(advice(None)['state'], 'ok')
        for text in (result['message'], limit['message']):
            self.assertNotIn('Bazzite', text)
            self.assertNotRegex(text, r'\b\d{3}M\b')  # no GPU model: the advice is general

    def test_steamos_manager_is_read_and_changed_through_the_session_bus(self):
        calls = []
        values = {'GpuPerformanceLevel': {'type': 's', 'data': 'manual'}, 'ManualGpuClock': {'type': 'u', 'data': 2700},
                  'ManualGpuClockMin': {'type': 'u', 'data': 200}, 'ManualGpuClockMax': {'type': 'u', 'data': 2700},
                  'TdpLimit': {'type': 'u', 'data': 28}, 'TdpLimitMin': {'type': 'u', 'data': 4},
                  'TdpLimitMax': {'type': 'u', 'data': 30}}

        def runner(arguments, timeout):
            calls.append(arguments)
            if 'set-property' in arguments:
                values['GpuPerformanceLevel'] = {'type': 's', 'data': arguments[-1]}
                return subprocess.CompletedProcess(arguments, 0, '', '')
            return subprocess.CompletedProcess(arguments, 0, json.dumps(values[arguments[-1]]), '')
        power = SteamOSPower(runner=runner, busctl='/usr/bin/busctl')
        found = power.read()
        self.assertEqual(found, {'available': True, 'gpu_level': 'manual', 'manual_clock_mhz': 2700,
                                 'manual_clock_min_mhz': 200, 'manual_clock_max_mhz': 2700, 'tdp_w': 28, 'tdp_min_w': 4,
                                 'tdp_max_w': 30})
        self.assertEqual(calls[0][:4], ['/usr/bin/busctl', '--user', '--json=short', 'get-property'])
        self.assertIn(power_balance.SERVICE, calls[0])
        count = len(calls)
        power.read()
        self.assertEqual(len(calls), count, 'cached between reads')
        self.assertEqual(power.set_gpu_automatic(), '')
        self.assertIn(['/usr/bin/busctl', '--user', 'set-property', power_balance.SERVICE, power_balance.OBJECT,
                       power_balance.GPU_INTERFACE, 'GpuPerformanceLevel', 's', 'auto'], calls)

    def test_without_steamos_manager_nothing_is_offered(self):
        def runner(arguments, timeout):
            return subprocess.CompletedProcess(arguments, 1, '', 'The name is not activatable')
        power = SteamOSPower(runner=runner, busctl='/usr/bin/busctl')
        self.assertEqual(power.read(), {'available': False})
        self.assertIn('not activatable', power.set_gpu_automatic())
        self.assertEqual(SteamOSPower(runner=runner, busctl='').read(), {'available': False})

    def test_the_pages_game_mode_and_the_headset_show_it(self):
        app, page, game = read('launcher/app.py'), read('launcher/desktop.html'), read('launcher/game_ui.py')
        self.assertIn("extra['power_balance']=self.power_status()", app)
        self.assertIn("if action=='gpu-auto' and self.role=='client':", app)
        self.assertIn("notice=self.power_status().get('short','') or notice", app)
        self.assertIn('POWER_BALANCE processor held at', app)
        self.assertIn('id="power-balance"', page)
        self.assertIn("act('gpu-auto')", page)
        self.assertIn("rows.append(('gpu-auto', 'Set the GPU clock to automatic',", game)
        self.assertIn("power.get('state') == 'starved'", game)


# ------------------------------------------------------------------------------------------------ pacing
class PacingTests(unittest.TestCase):
    def test_wired_pacing_stays_under_the_paths_loss_free_rate(self):
        wired = {'kind': 'wired', 'speed_mbps': 1000}
        self.assertEqual(pacing_mbps(wired), 920)
        # 1.0.0.8: the pace keeps launcher/link_test.py's PACING_HEADROOM (70 %) of the loss-free rate.
        self.assertEqual(pacing_mbps(wired, {'lossless_mbps': 500}), 350)
        self.assertEqual(pacing_mbps(wired, {'lossless_mbps': 1400}), 920, 'never above the link fraction')
        self.assertEqual(pacing_mbps(wired, {'recommendation': {'lossless_mbps': 600}}), 420)
        self.assertEqual(pacing_mbps(wired, {'lossless_mbps': 100}, bitrate=100), 0, 'below 1.25 x bitrate: unpaced')
        self.assertEqual(pacing_mbps(dict(wired, speed_mbps=10000), {'lossless_mbps': 500}), 0)
        # A test that lost nothing found no limit: the link's own speed decides (a 2.5 Gbit/s link is not held
        # to the test's highest step).
        self.assertEqual(pacing_mbps(dict(wired, speed_mbps=2500), {'lossless_mbps': None}), 2300)

    def test_a_usb_adapter_without_a_test_is_paced_lower_and_a_test_decides_otherwise(self):
        usb = {'kind': 'wired', 'speed_mbps': 1000, 'usb_adapter': True}
        self.assertEqual(pacing_mbps(usb), int(1000 * USB_PACING_FRACTION))
        self.assertEqual(pacing_mbps(usb, {'lossless_mbps': 900}), 630, 'the measured path wins (1.0.0.8: 70 % of it)')
        self.assertEqual(pacing_mbps(usb, {'lossless_mbps': 500}), 350)
        self.assertEqual(pacing_mbps(usb, {'lossless_mbps': 500}, measuring=True), 920,
                         'the test itself must reach every step')
        self.assertEqual(pacing_mbps(usb, {'lossless_mbps': None}), 920, 'tested without loss: no USB guess')
        self.assertEqual(pacing_mbps(usb, {'recommended_mbps': 100}), 600, 'a test before 1.0.0.4 measured no limit')
        self.assertTrue(path_tested({'lossless_mbps': None}) and path_tested({'recommendation': {'lossless_mbps': 500}}))
        self.assertFalse(path_tested({'recommended_mbps': 100}) or path_tested(None))

    def test_the_test_records_the_loss_free_rate(self):
        def row(rate, loss=0.0, frames=0.0, spread=4.0):
            return {'rate_mbps': rate, 'packet_loss_pct': loss, 'frame_loss_pct': frames, 'packets_expected': 1000,
                    'spread_p95_ms': spread, 'frame_interval_ms': 13.3, 'achieved_mbps': rate, 'packets_received': 1000}
        # The 1.0.0.3 test's shape: late from 200 Mbit/s, loss from 600. From 1.0.0.5 the pace limit is set by
        # steady loss (1 % of packets or more, here from 750) rather than the 0.03 % at 600 (test_vrostbridge_1005).
        rows = [row(80), row(150, spread=7.1), row(200, spread=8.2), row(300, spread=11.2), row(500, spread=14.7),
                row(600, .0298, 2.667), row(750, 12.4, 76), row(900, 27.4, 97)]
        self.assertEqual(lossless_mbps(rows), 600)
        result = recommend(rows, link_kind='wired')
        self.assertEqual((result['clean_mbps'], result['lossless_mbps']), (150, 600))
        saved = remember({}, {'host_id': 'h', 'interface': 'enp1', 'time': 1, 'recommendation': result})
        self.assertEqual(lossless_rate(saved['h|enp1']), 600)
        self.assertIsNone(lossless_mbps([row(80, 5.0, 40)]))
        self.assertIsNone(lossless_rate({'lossless_mbps': True}))
        clean = [row(rate) for rate in (80, 150, 200, 250, 300, 400, 500, 600, 750, 900)]
        self.assertIsNone(lossless_mbps(clean), 'no loss at any step: no limit found')
        saved = remember({}, {'host_id': 'h', 'interface': 'enp2', 'time': 2, 'recommendation': recommend(clean, link_kind='wired')})
        self.assertTrue(path_tested(saved['h|enp2']) and lossless_rate(saved['h|enp2']) is None)

    def test_a_wired_path_tested_before_1_0_0_4_is_tested_once_more(self):
        from types import SimpleNamespace
        from launcher.app import Application
        now = time.time()
        host = {'host_id': 'h', 'features': ['link-test']}
        runs = []

        def fake(saved):
            link = SimpleNamespace(source_path=lambda ip: (host, '192.168.1.2', 'enp1'))
            return SimpleNamespace(session_link=link, settings={'host_ip': '', 'network_tests': {'h|enp1': saved}},
                                   log=lambda line: None, run_network_test=lambda cancel: runs.append(1) or {'new': True})
        old = {'time': now, 'recommended_mbps': 100, 'link': {'kind': 'wired'}}
        self.assertEqual(Application.ensure_network_test(fake(old), None), {'new': True})
        self.assertEqual(len(runs), 1)
        tested = dict(old, lossless_mbps=500)
        self.assertEqual(Application.ensure_network_test(fake(tested), None), tested)
        none_clean = dict(old, lossless_mbps=None)
        self.assertEqual(Application.ensure_network_test(fake(none_clean), None), none_clean, 'measured, none was clean')
        wifi = {'time': now, 'recommended_mbps': 40, 'link': {'kind': 'wifi'}}
        self.assertEqual(Application.ensure_network_test(fake(wifi), None), wifi, 'Wi-Fi pacing never used it')
        self.assertEqual(len(runs), 1)
        host['features'] = []  # a host that cannot test keeps using the result it has
        self.assertEqual(Application.ensure_network_test(fake(old), None), old)

    def test_the_link_says_whether_it_is_a_usb_adapter(self):
        source = read('launcher/network_test.py')
        self.assertIn("result['usb_adapter'] = '/usb' in os.path.realpath(base / 'device')", source)
        self.assertIn('pace = pacing_mbps(link, measuring=True)', source)


# ------------------------------------------------------------------------------------------------ grey screens
class HoldTests(unittest.TestCase):
    def test_the_last_frame_is_held_for_a_second(self):
        stream = read('core/stream.hpp')
        self.assertIn('constexpr uint64_t remoteFreshNs = 100000000ULL, remoteHoldNs = 1000000000ULL;', stream)
        self.assertIn('return {now-source < remoteFreshNs, now-source < remoteHoldNs};', stream)
        tests = read('tests/stream_tests.cpp')
        self.assertIn('check(vrost::remoteFrameState(true,true,true,1999999999,1000000000).hold);', tests)
        self.assertIn('check(!vrost::remoteFrameState(true,true,true,2000000000,1000000000).hold);', tests)


# ------------------------------------------------------------------------------------------------ reports
class TimelineTests(unittest.TestCase):
    def test_the_session_in_ten_second_steps(self):
        now = [100.0]
        timeline = SessionTimeline(clock=lambda: now[0])
        incidents, fallback = 0, 0
        for second in range(25):
            now[0] = 100.0 + second
            incidents += 1 if second % 5 == 0 else 0
            fallback += 30 if second == 12 else 0
            timeline.timing({'unique_fps': '75.0' if second % 4 else '60.0', 'cadence': 'full', 'net_lost_frames': '1'
                             if second == 3 else '0', 'net_keyframe_requests': '2' if second == 3 else '0',
                             'presentation_incidents': str(incidents), 'handoff_cpu_max_ms': '63.3' if second == 7 else '6'})
            timeline.metrics({'fallback': str(fallback), 'frame_age_ms': '20'})
            timeline.gaze(second * 50)
            timeline.host_gaze(second * 60, second * 10, second * 5)
            if second == 5:
                timeline.performance(sample(544, when=1))
        rows = timeline.snapshot()['rows']
        self.assertEqual([row['t_s'] for row in rows], [0, 10, 20])
        first, second = rows[0], rows[1]
        self.assertEqual((first['lost'], first['keyframes'], first['fps_min'], first['handoff_max_ms']), (1, 2, 60.0, 63.3))
        self.assertEqual(first['incidents'], 1, 'counted from the first reading')
        self.assertEqual(second['grey_frames'], 30)
        self.assertEqual(second['gaze_hz'], 50.0)
        self.assertEqual((second['host_gaze'], second['host_held'], second['host_centred']), (600, 100, 50))
        self.assertEqual(first['cpu_mhz'], 544.1)
        self.assertEqual(first['gpu_mhz'], 2700)
        self.assertEqual(timeline.snapshot()['step_s'], BUCKET_S)

    def test_a_long_session_keeps_its_start_and_the_latest(self):
        now = [0.0]
        timeline = SessionTimeline(clock=lambda: now[0])
        for step in range(MAX_ROWS + 50):
            now[0] = step * BUCKET_S
            timeline.timing({'unique_fps': '75', 'cadence': 'full'})
        snapshot = timeline.snapshot()
        self.assertLessEqual(len(snapshot['rows']), MAX_ROWS + 1)
        self.assertEqual(snapshot['rows'][0]['t_s'], 0)
        self.assertEqual(snapshot['rows'][-1]['t_s'], (MAX_ROWS + 49) * BUCKET_S)
        self.assertGreater(snapshot['rows_dropped'], 0)

    def test_the_client_report_carries_it(self):
        app = read('launcher/app.py')
        self.assertIn('timeline=self.timeline.snapshot(),', app)
        self.assertIn('self.timeline.timing(self.timings)', app)
        self.assertIn('self.timeline.metrics(values)', app)
        self.assertIn('def sample_timeline(self):', app)


class ProcessUseTests(unittest.TestCase):
    def fake_proc(self, directory, processes, busy, total):
        for pid, (name, ticks) in processes.items():
            path = Path(directory) / str(pid)
            path.mkdir(exist_ok=True)
            fields = ['S'] + ['0'] * 10 + [str(ticks), '0'] + ['0'] * 30
            (path / 'stat').write_text('%d (%s) %s\n' % (pid, name, ' '.join(fields)))
        idle = total - busy
        (Path(directory) / 'stat').write_text('cpu  %d 0 0 %d 0 0 0 0 0 0\ncpu0 0 0 0 0\n' % (busy, idle))

    def test_programs_are_grouped_and_others_are_never_named(self):
        self.assertEqual(process_group('Baballonia.Desk'), 'eye tracking (Baballonia)')
        self.assertEqual(process_group('VROSTBridgeClie'), 'VROSTBridge headset client')
        self.assertEqual(process_group('vrcompositor'), 'vrcompositor')
        self.assertEqual(process_group('kwin_wayland'), 'kwin')
        self.assertEqual(process_group('firefox'), 'other')

    def test_processor_time_between_two_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [10.0]
            use = ProcessUse(proc=directory, clock=lambda: now[0])
            self.fake_proc(directory, {10: ('Baballonia.Desk', 1000), 11: ('vrcompositor', 500), 12: ('firefox', 50)},
                           busy=10000, total=40000)
            self.assertEqual(use.sample(), {}, 'nothing to compare with yet')
            now[0] = 15.0  # 5 s at 100 ticks a second: 500 ticks are one whole core
            self.fake_proc(directory, {10: ('Baballonia.Desk', 1750), 11: ('vrcompositor', 600), 12: ('firefox', 50),
                                       13: ('steamwebhelper', 25)}, busy=12000, total=48000)
            with patch('launcher.performance_monitor.CLOCK_TICKS', 100):
                result = use.sample()
            self.assertEqual(result['processes'], {'eye tracking (Baballonia)': 150.0, 'vrcompositor': 20.0,
                                                   'steamwebhelper': 5.0})
            self.assertEqual(result['cpu_busy_percent'], 25.0)
            self.assertEqual(processor_ticks(directory), (12000, 48000))
            self.assertEqual(set(process_ticks(directory, uid=None)), {10, 11, 12, 13})

    def test_the_monitor_samples_it_during_sessions_only(self):
        source = read('launcher/performance_monitor.py')
        self.assertIn('try: value.update(self.process_use.sample())', source)
        self.assertIn('self.process_use.previous = self.process_use.stat = self.process_use.at = None', source)


# ------------------------------------------------------------------------------------------------ Game Mode
class FakeSDL:
    def __init__(self, pressed=()):
        self.pressed = set(pressed)

    def GameControllerGetButton(self, handle, button):
        return 1 if (handle, button) in self.pressed else 0


def view_with(buttons, page='stream'):
    view = game_ui.View(ROOT / 'dist' / 'client', lambda: {}, lambda body: None, lambda: {}, None)
    view.page, view.buttons, view.focus = page, list(buttons), 0
    view.state = {'settings': {'codec': 'Auto', 'ten_bit': True, 'reaim': True, 'pacing': True, 'auto_bitrate': True,
                               'bitrate': 80, 'sharpening': 'medium'}}
    view.actions = []
    view.action = lambda name, notice='', **kwargs: view.actions.append((name, kwargs))
    return view


def rows(count, top=240, step=84, offset=0, band=380):
    result = []
    for index in range(count):
        y = top + index * step - offset
        result.append(('row%d' % index, 'Row', (48, y, 1176, 74), True, top <= y and y + 74 <= top + band, True))
    return result


class GameModeTextTests(unittest.TestCase):
    def test_every_character_game_mode_draws_is_in_its_font_or_written_out(self):
        # The screen draws with the bundled fonts only (stb_truetype): a missing glyph shows as "?". 1.0.0.3 showed
        # "Lightest · ? resolution" for the 1/5 setting.
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest('fontTools is not installed')
        cmap = TTFont(str(ROOT / 'launcher/fonts/Inter-Regular.ttf')).getBestCmap()
        text = (read('launcher/game_ui.py') + read('launcher/headset.py')).translate(game_ui.GLYPH_FALLBACK)
        self.assertEqual(sorted({ch for ch in text if ord(ch) > 0x7e and ord(ch) not in cmap}), [])
        self.assertEqual('Lightest · ⅕ resolution'.translate(game_ui.GLYPH_FALLBACK), 'Lightest · 1/5 resolution')


class GameModeNavigationTests(unittest.TestCase):
    def test_left_and_right_change_a_row_instead_of_jumping_to_back(self):
        buttons = [('codec', 'Video codec', (48, 240, 1176, 74), True, True, True),
                   ('sharpening', 'Sharpening', (48, 324, 1176, 74), True, True, True),
                   ('toggle:pacing', 'Pace', (48, 408, 1176, 74), True, True, True),
                   ('back', 'Back', (1052, 122, 180, 52), True, True, False)]
        view = view_with(buttons)
        view.move(1, 0)
        self.assertEqual(view.focus, 0, 'still on the row')
        self.assertEqual(view.actions[-1], ('client-preferences', {'codec': 'H264'}))
        view.move(-1, 0)
        self.assertEqual(view.actions[-1], ('client-preferences', {'codec': 'AV1'}), 'left: the previous value')
        view.focus = 1
        view.move(-1, 0)
        self.assertEqual(view.actions[-1], ('set-sharpening', {'level': 'low'}))
        view.focus = 2
        count = len(view.actions)
        view.move(1, 0)
        self.assertEqual(len(view.actions), count, 'a switch already on stays on')
        view.move(-1, 0)
        self.assertEqual(view.actions[-1], ('client-preferences', {'pacing': False}))

    def test_bitrate_steps_both_ways_through_auto(self):
        view = view_with([('quality', 'Bitrate', (48, 240, 1176, 74), True, True, True)])
        view.choose('quality', 1)
        self.assertEqual(view.actions[-1], ('client-preferences', {'auto_bitrate': False, 'bitrate': 80}))
        view.choose('quality', -1)
        self.assertEqual(view.actions[-1], ('client-preferences', {'auto_bitrate': False, 'bitrate': 250}))
        view.state['settings'].update(auto_bitrate=False, bitrate=250)
        view.choose('quality', 1)
        self.assertEqual(view.actions[-1], ('client-preferences', {'auto_bitrate': True}))
        view.state['settings'].update(bitrate=100)  # a value set on the desktop page
        view.choose('quality', 1)
        self.assertEqual(view.actions[-1], ('client-preferences', {'auto_bitrate': False, 'bitrate': 120}))
        view.choose('quality', -1)
        self.assertEqual(view.actions[-1], ('client-preferences', {'auto_bitrate': False, 'bitrate': 80}))

    def test_rows_out_of_view_only_by_stepping_and_lists_stop_at_their_ends(self):
        buttons = rows(8, offset=168) + [('back', 'Back', (1052, 122, 180, 52), True, True, False)]
        view = view_with(buttons)
        back = len(buttons) - 1
        view.focus = back
        view.move(0, 1)
        self.assertEqual(view.buttons[view.focus][0], 'row2', 'Back down: the first row in view, not one above it')
        view.focus = back
        view.move(-1, 0)
        self.assertEqual(view.buttons[view.focus][0], 'row2')
        view.move(0, -1)
        self.assertEqual(view.buttons[view.focus][0], 'row1', 'up from the first row in view: the row above')
        view.focus = 7
        view.move(0, 1)
        self.assertEqual(view.focus, 7, 'the last row stays: no wrap to Back')

    def test_the_main_page_still_wraps_and_moves_spatially(self):
        buttons = [('start', 'Start', (48, 336, 580, 104), True, True, False),
                   ('host-menu', 'Host menu', (652, 336, 580, 104), True, True, False),
                   ('stream', 'Stream', (48, 462, 380, 92), True, True, False),
                   ('exit', 'Exit', (951, 576, 281, 72), True, True, False)]
        view = view_with(buttons, page='main')
        view.move(1, 0)
        self.assertEqual(view.buttons[view.focus][0], 'host-menu')
        view.move(0, 1)
        self.assertEqual(view.buttons[view.focus][0], 'exit')
        view.move(0, 1)
        self.assertEqual(view.buttons[view.focus][0], 'start', 'wraps to the top')

    def test_one_press_from_two_controllers_moves_once_and_held_directions_repeat(self):
        view = view_with(rows(10, band=10000))
        view.s = FakeSDL()
        view.press((0, 1), ('pad', 1, 12), 0.0)
        view.press((0, 1), ('pad', 2, 12), 0.004)  # Steam's virtual controller reports the same press
        view.press((0, 1), ('key', 1073741905), 0.006)  # and a key for it
        self.assertEqual(view.focus, 1)
        held = lambda source: True
        self.assertFalse(view.repeat(0.2, held))
        self.assertTrue(view.repeat(game_ui.REPEAT_DELAY_S + .001, held))
        self.assertEqual(view.focus, 2)
        self.assertTrue(view.repeat(game_ui.REPEAT_DELAY_S + game_ui.REPEAT_S + .002, held))
        self.assertEqual(view.focus, 3)
        view.release((0, 1), ('pad', 1, 12))
        view.release((0, 1), ('pad', 2, 12))
        view.release((0, 1), ('key', 1073741905))
        self.assertFalse(view.repeat(5.0, held))
        self.assertEqual(view.focus, 3)
        view.press((0, 1), ('pad', 1, 12), 6.0)
        self.assertEqual(view.focus, 4, 'a new press moves at once')
        # A release that never arrived: the button is checked, so the direction does not run away.
        view.controllers = {1: 'pad-one'}
        self.assertFalse(view.repeat(7.0, view.held))
        self.assertEqual(view.holds, {})

    def test_the_stick_moves_repeats_and_releases_with_hysteresis(self):
        view = view_with(rows(10, band=10000))
        self.assertFalse(view.stick(game_ui.STICK_ON - 1, 0, 0.0))
        self.assertTrue(view.stick(0, game_ui.STICK_ON + 10, 0.0))
        self.assertEqual(view.focus, 1)
        self.assertFalse(view.stick(0, game_ui.STICK_OFF + 10, 0.2), 'held, not yet repeating')
        self.assertTrue(view.stick(0, game_ui.STICK_OFF + 10, game_ui.REPEAT_DELAY_S + .01))
        self.assertEqual(view.focus, 2)
        self.assertFalse(view.stick(0, game_ui.STICK_OFF - 10, 1.0), 'released')
        self.assertTrue(view.stick(0, game_ui.STICK_ON + 10, 1.1))
        self.assertEqual(view.focus, 3)
        self.assertTrue(view.stick(0, -(game_ui.STICK_ON + 10), 1.2), 'straight to the other way: moves at once')
        self.assertEqual(view.focus, 2)

    def test_the_hint_line_and_the_loop(self):
        source = read('launcher/game_ui.py')
        self.assertIn("self.label('◀ ▶  Change'", source)
        self.assertIn('def open_controllers(self):', source)
        self.assertIn("elif kind in (0x651, 0x652):", source)
        self.assertNotIn('self.last_axis', source)


if __name__ == '__main__':
    unittest.main()
