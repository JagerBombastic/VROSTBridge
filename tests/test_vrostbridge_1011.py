"""VROSTBridge 1.0.0.11: the full-resolution picture on lighter headset PCs.

The 1.0.0.8 test streamed the Beyond's own 2560 per eye at 75 fps with the headset PC's GPU about half busy; the
client's own share of it (the fused frame pass) was the part left to make lighter, so that the panels' full
resolution also fits smaller GPUs:
- the fused pass does the same work with about a third less arithmetic per pixel (its foveation constants worked
  out once per frame, each eye in its own workgroup layer) and skips the pixels the headset's lenses never show;
- the GPU times the pass and the decode-time plane copy, so a report says what the picture costs;
- while the headset PC's graphics hold the stream below refresh, each eye is shown a little smaller (90 %, then
  80 %) instead of SteamVR halving the frame rate, and at full size again once there is room;
- the Beyond's 2560 size is called Native.

The C++ parts are tested by tests/fused_shader_tests.cpp (the pass against its CPU model and the visible spans),
tests/video_pass_tests.cpp (the client's own frame path with SteamVR stood in for), tests/visible_area_tests.cpp and
tests/shown_size_tests.cpp.
"""
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from launcher.output_scale import (BEHIND_GPU_SHARE, DOWN_SECONDS, LEVELS, OutputScale, RETRY_WINDOW_S,  # noqa: E402
                                   ROUND_TRIP_S, ROUND_TRIPS, SETTLE_S, UP_AFTER_S, UP_WAIT_MAX_S, WARMUP_S,
                                   gpu_share)
from launcher.timeline import SessionTimeline  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def second(cadence='full', gpu_ms=8.0, refresh=75.0):
    return {'cadence': cadence, 'gpu_ms': str(gpu_ms), 'refresh_hz': str(refresh)}


# ------------------------------------------------------------------------------------------------ version
class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_11_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1012.py and later.
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 11))
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "' + CLIENT_VERSION + '"', read('core/version.hpp'))
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.11'))


# ------------------------------------------------------------------------------------------------ the frame pass
class FramePassSourceTests(unittest.TestCase):
    def test_one_shader_for_every_gpu(self):
        shader = read('client/platform/linux/fused.comp')
        self.assertIn('layout(binding=3,std140) uniform Frame', shader)
        self.assertIn('readonly buffer Visible', shader)
        self.assertIn('int eye=int(gl_WorkGroupID.z);', shader)
        self.assertNotIn('push_constant', shader)
        self.assertNotIn('FUSED_UNIFORM_ROWS', shader)
        # The inverse's terms are no longer worked out per pixel.
        self.assertNotIn('decompressAxis', shader)
        self.assertIn('vec2 axisTexel(float u,Axis a)', shader)
        for path in ('CMakeLists.txt', 'packaging/build_distributions.py', 'packaging/build_windows_client.py'):
            self.assertFalse('fused-uniform' in read(path), path)

    def test_the_client_writes_the_frame_constants_and_dispatches_each_eye(self):
        vulkan = read('client/platform/linux/vulkan.cpp')
        self.assertIn('fusedFrameConstants(fusedParameters(s->conversion, rows.rows, sharpening.load())', vulkan)
        self.assertIn('vkCmdDispatch(t->cmd, (t->width / 2 + 15) / 16, (t->height + 15) / 16, 2);', vulkan)
        # Black where the pass does not write, once per image.
        self.assertIn('vkCmdClearColorImage(t->cmd, t->image, VK_IMAGE_LAYOUT_GENERAL, &black', vulkan)
        self.assertIn('t->needsClear = true; // 1.0.0.11: filled to the edges', vulkan)
        # A description leaving under half of an eye is taken for a fault.
        self.assertIn('const bool plausible = share[0] >= .5 && share[1] >= .5;', vulkan)

    def test_the_lens_area_comes_from_steamvr(self):
        device = read('client/device/openvr_device.cpp')
        self.assertIn('GetHiddenAreaMesh(eye, vr::k_eHiddenAreaMesh_Inverse)', device)
        self.assertIn('system->ComputeDistortion(eye, u, v, &coordinates)', device)
        # The mesh's way up is not documented: checked against the outline (tests/visible_area_tests.cpp).
        self.assertIn('alignMeshes(area);', device)
        main = read('client/platform/linux/main.cpp')
        self.assertIn('graphics.setVisibleArea(runtime.visibleArea(), !fullFrame);', main)
        self.assertIn('arg == "--full-frame"', main)

    def test_the_gpu_times_the_pass_and_the_copy(self):
        vulkan = read('client/platform/linux/vulkan.cpp')
        # Both stamps once the work before them is complete: a top-of-pipe stamp may be written while earlier
        # work still runs, and the time would include its tail.
        self.assertIn('vkCmdWriteTimestamp(t->cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, passQueries_, index * 2);', vulkan)
        self.assertIn('vkCmdWriteTimestamp(t->cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, passQueries_, index * 2 + 1);',
                      vulkan)
        self.assertIn('vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, copyQueries_, uint32_t(s.copySlot * 2));',
                      vulkan)
        self.assertNotIn('TOP_OF_PIPE', vulkan)
        self.assertIn('beginCopyTiming(*s, s->cmd);', vulkan)
        self.assertIn('beginCopyTiming(*s, s->cmd);', read('client/platform/windows/vulkan_upload.cpp'))
        self.assertIn('VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WITH_AVAILABILITY_BIT', vulkan)
        main = read('client/platform/linux/main.cpp')
        for field in ('"frame_pass_gpu"', '"plane_copy_gpu"', '" shown_scale="', '" visible_share="',
                      '" shown_eye_width="'):
            self.assertIn(field, main)

    def test_the_client_frame_path_is_run_with_steamvr_stood_in_for(self):
        # tests/video_pass_tests.cpp runs VulkanVideo itself (open, the frame pass, the lens area, the clears, the
        # shown size, the GPU time stamps) against a stand-in SteamVR generated from the OpenVR headers.
        cmake = read('CMakeLists.txt')
        self.assertIn('add_executable(video_pass_tests tests/video_pass_tests.cpp client/platform/linux/vulkan.cpp)', cmake)
        self.assertIn('add_test(NAME video_pass_contracts COMMAND video_pass_tests "${CMAKE_CURRENT_BINARY_DIR}")', cmake)
        self.assertIn('friend struct VideoTestAccess;', read('client/platform/linux/video.hpp'))
        import subprocess
        checked = subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_openvr_stand_in.py'), '--check'],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_the_scale_command(self):
        main = read('client/platform/linux/main.cpp')
        self.assertIn('value.rfind("scale ",0)==0', main)
        self.assertIn('decoder->setShownScale(shownScale);', main)
        self.assertIn('arg == "--scale"', main)
        decoder = read('client/platform/linux/decoder.cpp')
        self.assertIn('if (!compatibility_) applyScale();', decoder)


# ------------------------------------------------------------------------------------------------ keeping the frame rate
class OutputScaleTests(unittest.TestCase):
    def run_seconds(self, scale, n, **kwargs):
        frames = kwargs.pop('frames_arriving', True)
        tracked = kwargs.pop('tracked', True)
        return [scale.observe(second(**kwargs), frames, tracked) for _ in range(n)]

    def test_gpu_share(self):
        self.assertAlmostEqual(gpu_share(second(gpu_ms=10.0, refresh=75.0)), .75)
        self.assertIsNone(gpu_share({'cadence': 'full'}))
        self.assertIsNone(gpu_share(second(gpu_ms=0)))

    def test_steps_down_after_three_behind_seconds_then_settles(self):
        scale = OutputScale()
        self.assertEqual(self.run_seconds(scale, 30, cadence='idle'), [None] * 30)
        self.assertEqual(self.run_seconds(scale, WARMUP_S, cadence='client_throttled', gpu_ms=12.5), [None] * WARMUP_S)
        events = self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual(events, [None] * (DOWN_SECONDS - 1) + [90])
        self.assertEqual(scale.level, 90)
        self.assertIn('below refresh for 3 of the last', scale.reason)
        self.assertIn('GPU 94% of each refresh', scale.reason)
        # It settles before another step, then goes on to 80 % and no further.
        events = self.run_seconds(scale, SETTLE_S + DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual([e for e in events if e], [80])
        events = self.run_seconds(scale, 60, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual([e for e in events if e], [])
        self.assertEqual(scale.level, LEVELS[-1])

    def test_only_the_gpu_falling_behind_counts(self):
        # SteamVR below refresh with GPU room to spare: a smaller picture would not help.
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.assertEqual([e for e in self.run_seconds(scale, 20, cadence='client_throttled', gpu_ms=6.0) if e], [])
        # Frames not arriving at refresh (Wi-Fi), or the headset not tracked: not the graphics either.
        self.assertEqual([e for e in self.run_seconds(scale, 20, cadence='client_throttled', gpu_ms=12.5,
                                                      frames_arriving=False) if e], [])
        self.assertEqual([e for e in self.run_seconds(scale, 20, cadence='client_throttled', gpu_ms=12.5,
                                                      tracked=False) if e], [])
        self.assertEqual(scale.level, 100)
        # Without SteamVR's GPU time a throttled second is not known to be the GPU's: it does not count.
        unknown = OutputScale()
        self.run_seconds(unknown, WARMUP_S)
        events = [unknown.observe({'cadence': 'client_throttled', 'refresh_hz': '75'}) for _ in range(20)]
        self.assertEqual([e for e in events if e], [])
        self.assertGreaterEqual(BEHIND_GPU_SHARE, .7)
        # Nor does it step back up without it: at least half of the steady seconds must show the room.
        smaller = OutputScale()
        self.run_seconds(smaller, WARMUP_S)
        self.run_seconds(smaller, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        events = [smaller.observe({'cadence': 'full', 'refresh_hz': '75'}) for _ in range(3 * UP_AFTER_S)]
        self.assertEqual(([e for e in events if e], smaller.level), ([], 90))

    def test_the_seconds_it_settles_do_not_count(self):
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        # Behind all through the settling and for two seconds more: not yet the next step; the third is.
        events = self.run_seconds(scale, SETTLE_S + DOWN_SECONDS - 1, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual([e for e in events if e], [])
        self.assertEqual(self.run_seconds(scale, 1, cadence='client_throttled', gpu_ms=12.5), [80])

    def test_back_to_full_size_when_there_is_room(self):
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual(scale.level, 90)
        # Steady but busy: it stays smaller.
        self.assertEqual([e for e in self.run_seconds(scale, UP_AFTER_S + SETTLE_S, gpu_ms=9.5) if e], [])
        # Steady with room: one step back up.
        events = self.run_seconds(scale, UP_AFTER_S + 1, gpu_ms=6.0)
        self.assertEqual([e for e in events if e], [100])
        self.assertIn('steady for 60 s', scale.reason)
        self.assertEqual(scale.snapshot()['steps'], 2)

    def test_a_step_up_that_falls_behind_again_stays_down(self):
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.run_seconds(scale, SETTLE_S + UP_AFTER_S + 1, gpu_ms=6.0)
        self.assertEqual(scale.level, 100)
        self.run_seconds(scale, SETTLE_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual(scale.level, 90)
        self.assertTrue(scale.snapshot()['pinned'])
        self.assertEqual([e for e in self.run_seconds(scale, 3 * UP_AFTER_S, gpu_ms=5.0) if e], [])
        self.assertLessEqual(SETTLE_S + DOWN_SECONDS, RETRY_WINDOW_S)

    def seconds_until_a_step(self, scale, limit, **kwargs):
        for n in range(1, limit + 1):
            if self.run_seconds(scale, 1, **kwargs)[0]:
                return n
        return None

    def test_a_step_up_that_holds_a_while_then_falls_behind_waits_longer_each_time(self):
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        waits = []
        for _ in range(ROUND_TRIPS):
            waits.append(self.seconds_until_a_step(scale, 2 * UP_WAIT_MAX_S, gpu_ms=6.0))
            self.assertEqual(scale.level, 100)
            self.run_seconds(scale, RETRY_WINDOW_S + 20, gpu_ms=6.0)  # held past the retry window
            self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
            self.assertEqual(scale.level, 90)
        self.assertEqual(waits, [UP_AFTER_S, 2 * UP_AFTER_S, 4 * UP_AFTER_S])
        snapshot = scale.snapshot()
        self.assertEqual((snapshot['round_trips'], snapshot['pinned'], snapshot['up_wait_s']),
                         (ROUND_TRIPS, True, UP_WAIT_MAX_S))
        self.assertIsNone(self.seconds_until_a_step(scale, 2 * UP_WAIT_MAX_S, gpu_ms=5.0))
        # Falling behind long after a step up is not held against the next one.
        later = OutputScale()
        self.run_seconds(later, WARMUP_S)
        self.run_seconds(later, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual(self.seconds_until_a_step(later, 2 * UP_WAIT_MAX_S, gpu_ms=6.0), UP_AFTER_S)
        self.run_seconds(later, ROUND_TRIP_S + 1, gpu_ms=6.0)
        self.run_seconds(later, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        self.assertEqual((later.level, later.round_trips, later.up_wait, later.pinned), (90, 0, UP_AFTER_S, False))

    def test_reset_starts_at_full_size(self):
        scale = OutputScale()
        self.run_seconds(scale, WARMUP_S)
        self.run_seconds(scale, DOWN_SECONDS, cadence='client_throttled', gpu_ms=12.5)
        scale.reset()
        self.assertEqual((scale.level, scale.snapshot()['steps']), (100, 0))


class OutputScaleAppTests(unittest.TestCase):
    def setUp(self):
        from test_session_lifecycle import SessionLifecycleTests
        self.case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.app = self.case.app()
        self.app.running = lambda: True
        self.app.native_command = Mock(return_value=True)
        self.app.session_link.snapshot.return_value = {}
        self.app.async_reprojection.status.return_value = {}

    def stream(self, cadence, seconds, gpu_ms=12.5, frame_pass='fused'):
        for _ in range(seconds):
            self.app.log('METRICS state=streaming decoded=100 tracked=1 input_focus=1')
            self.app.log('TIMING refresh_hz=75.000 unique_fps=37.5 packet_fps=75.000 cadence=' + cadence
                         + ' frame_pass=' + frame_pass + ' gpu_ms=' + str(gpu_ms) + ' frame_pass_gpu_samples=37'
                         + ' frame_pass_gpu_mean_ms=4.4 frame_pass_gpu_max_ms=5.1 shown_scale=100')

    def test_the_client_is_told_and_the_page_says_why(self):
        app = self.app
        self.assertTrue(app.settings['keep_frame_rate'] and app.settings['lens_area'])
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS)
        app.check_graphics_limit()
        app.native_command.assert_any_call('scale 90')
        status = app.graphics_status()['output_scale']
        self.assertEqual((status['level'], status['steps']), (90, 1))
        self.assertIn('SCALE Each eye shown at 90% of its size (was 100%: SteamVR showed the stream below refresh',
                      app.log_path.read_text(encoding='utf-8'))

    def test_off_means_full_size_at_once(self):
        app = self.app
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS)
        app.check_graphics_limit()
        app.native_command.reset_mock()
        app.action({'action': 'client-option', 'name': 'keep_frame_rate', 'enabled': False})
        app.native_command.assert_called_with('scale 100')
        self.assertEqual(app.output_scale.level, 100)
        self.stream('client_throttled', 20)
        app.check_graphics_limit()
        self.assertNotIn('scale 90', [c.args[0] for c in app.native_command.call_args_list])

    def test_a_size_chosen_just_before_the_switch_goes_off_is_not_sent(self):
        app = self.app
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS)  # 90 % chosen, not yet sent
        app.action({'action': 'client-option', 'name': 'keep_frame_rate', 'enabled': False})
        app.check_graphics_limit()
        sent = [c.args[0] for c in app.native_command.call_args_list if c.args[0].startswith('scale ')]
        self.assertEqual(sent, ['scale 100'])

    def test_a_size_on_its_way_when_the_switch_goes_off_is_followed_by_full_size(self):
        app = self.app
        delivered, switch = [], []

        def command(text):
            if text == 'scale 90':  # the switch is turned off while this size is being sent
                thread = threading.Thread(target=app.action, args=({'action': 'client-option', 'name': 'keep_frame_rate',
                                                                     'enabled': False},))
                thread.start()
                switch.append(thread)
                thread.join(.3)
            delivered.append(text)
            return True
        app.native_command = Mock(side_effect=command)
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS)
        app.check_graphics_limit()
        switch[0].join(5)
        self.assertEqual([text for text in delivered if text.startswith('scale ')], ['scale 90', 'scale 100'])
        self.assertEqual(app.output_scale.level, 100)

    def test_a_size_that_could_not_be_sent_is_tried_again(self):
        app = self.app
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS)
        app.native_command.return_value = False
        app.check_graphics_limit()
        self.assertEqual(app.scale_event, 90)
        app.native_command.return_value = True
        app.check_graphics_limit()
        self.assertIsNone(app.scale_event)
        self.assertIn('SCALE Each eye shown at 90% of its size', app.log_path.read_text(encoding='utf-8'))

    def test_not_on_cpu_decoding(self):
        self.stream('client_throttled', WARMUP_S + DOWN_SECONDS + 5, frame_pass='cpu')
        self.app.check_graphics_limit()
        self.assertEqual(self.app.output_scale.level, 100)

    def test_the_settings(self):
        app = self.app
        self.assertIn('lens_area', app.CLIENT_OPTIONS)
        self.assertIn('keep_frame_rate', app.CLIENT_OPTIONS)
        self.assertIn('keep_frame_rate', app.LIVE_OPTIONS)
        self.assertTrue(app.RECOMMENDED['lens_area'] and app.RECOMMENDED['keep_frame_rate'])
        source = read('launcher/app.py')
        self.assertIn("if not self.settings.get('lens_area',True):args.append('--full-frame')", source)


# ------------------------------------------------------------------------------------------------ reports and pages
class TimelineTests(unittest.TestCase):
    def test_the_pass_and_the_shown_size_are_kept(self):
        now = [0.0]
        timeline = SessionTimeline(clock=lambda: now[0])
        for i in range(10):
            now[0] = float(i)
            timeline.timing({'cadence': 'full', 'unique_fps': '75', 'frame_pass_gpu_samples': '75',
                             'frame_pass_gpu_mean_ms': str(2.0 + i * .1), 'frame_pass_gpu_max_ms': str(3 + i),
                             'gpu_ms': '7.5', 'shown_scale': '90' if i > 5 else '100'})
        now[0] = 10.0
        timeline.timing({'cadence': 'full', 'unique_fps': '75'})
        row = timeline.snapshot()['rows'][0]
        self.assertAlmostEqual(row['pass_gpu_ms'], 2.45, places=2)
        self.assertEqual(row['pass_gpu_max_ms'], 12.0)
        self.assertEqual(row['steamvr_gpu_ms'], 7.5)
        self.assertEqual(row['shown_scale'], 90)
        self.assertNotIn('shown_scale', timeline.snapshot()['rows'][1])


class PageTests(unittest.TestCase):
    def test_the_desktop_page(self):
        page = read('launcher/desktop.html')
        for text in ('id="lens_area"', 'data.lens_area=$(\'lens_area\').checked', 'id="keep_frame_rate"',
                     "act('client-option',{name:'keep_frame_rate',enabled:this.checked})", 'id="frame-rate-status"',
                     'Frame pass ${Number(pacing.frame_pass_gpu_mean_ms).toFixed(1)} ms on the GPU'):
            self.assertIn(text, page)

    def test_game_mode(self):
        from launcher.game_ui import DEFAULTS, LIVE_TOGGLES, TOGGLES
        self.assertIn('lens_area', TOGGLES)
        self.assertIn('keep_frame_rate', LIVE_TOGGLES)
        self.assertTrue(DEFAULTS['lens_area'] and DEFAULTS['keep_frame_rate'])

    def test_the_beyond_streams_its_panels_own_size(self):
        from headset_fixture import HEADSET
        from launcher.headset import resolution_options
        beyond = dict(HEADSET, model='Bigscreen Beyond 2e', manufacturer='Bigscreen', width=2712, height=2712,
                      refresh_hz=75.0)
        options = resolution_options({'codec': 'Hevc', 'resolution': 'beyond-balanced'}, beyond)
        # 1.0.0.13: the Beyond offers no choice; at 75 Hz it streams 2560, its panels' own size (1.0.0.11's Native).
        self.assertEqual([(o['id'], o['label']) for o in options],
                         [('automatic', 'Set by the headset · 75 Hz - 2560 x 2560 per eye')])


if __name__ == '__main__':
    unittest.main()
