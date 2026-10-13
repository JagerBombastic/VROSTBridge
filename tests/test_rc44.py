"""RC4.4: from the RC4.3 test of 2026-10-04 17:27-17:35.

- The integrated Radeon GPU ran at 800 of its 2700 MHz for the whole session while 45-64 % busy (and in
  the RC4.2 tests): its power limit held SteamVR at half rate, so latency and prediction doubled
  and tracking jittered. The graphics advice now names the power limit first.
- SteamVR's compositor on the client crashed at 17:35:12; the native client died of SIGPIPE 9 s
  later and the session simply ended. VROSTBridge now says so and restarts the session once.
- Eye-tracked foveation's settings stayed hidden while eye tracking was off.
- The in-headset quality pill was magnified by the compositor (pixelated).
- The fused pass was cheaper to run (five-tap Catmull-Rom, analytic texel density).
"""
import json
import signal
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from launcher import graphics_limit
from launcher.graphics_limit import GpuClocks, advice

ROOT = Path(__file__).resolve().parents[1]


def sample(clock, busy=58, maximum=2700, power=15.3, at=None, active=True):
    return {'observed_monotonic': at if at is not None else time.monotonic(), 'active': active, 'available': True,
            'gpus': [{'card': 'card1', 'gpu_busy_percent': busy, 'selected_sclk_mhz': clock, 'max_sclk_mhz': maximum,
                      'sensors': [{'clock_mhz': clock, 'average_power_w': power}]}]}


class PowerStarvedGpuTests(unittest.TestCase):
    def test_the_rc43_session_reads_as_power_starved(self):
        clocks = GpuClocks()
        for i, (clock, busy) in enumerate(((800, 59), (800, 59), (800, 58), (1012, 64), (800, 59))):
            clocks.observe(sample(clock, busy, at=float(i)))
        self.assertEqual(clocks.starved(), {'clock_mhz': 800, 'max_mhz': 2700, 'power_w': 15})
        clocks.observe(sample(800, at=4.0))  # the same sample again is not counted twice
        self.assertEqual(len(clocks.samples), 5)

    def test_a_busy_gpu_at_speed_or_an_idle_one_is_not_starved(self):
        fast = GpuClocks()
        for i in range(4):
            fast.observe(sample(2400, 70, at=float(i)))
        self.assertIsNone(fast.starved(), 'clocked up: the GPU itself is the limit')
        idle = GpuClocks()
        for i in range(4):
            idle.observe(sample(800, 12, at=float(i)))
        self.assertIsNone(idle.starved(), 'a low clock at low load is normal')
        few = GpuClocks()
        for i in range(2):
            few.observe(sample(800, 60, at=float(i)))
        self.assertIsNone(few.starved(), 'two samples are not enough to say')
        inactive = GpuClocks()
        for i in range(4):
            inactive.observe(sample(800, 60, at=float(i), active=False))
        self.assertIsNone(inactive.starved())

    def test_the_busiest_gpu_is_the_one_judged(self):
        clocks = GpuClocks()
        for i in range(3):
            value = sample(800, 60, at=float(i))
            value['gpus'].insert(0, {'card': 'card0', 'gpu_busy_percent': 2, 'selected_sclk_mhz': 300,
                                     'max_sclk_mhz': 2000, 'sensors': [{'clock_mhz': 300}]})
            clocks.observe(value)
        self.assertEqual(clocks.starved()['max_mhz'], 2700)

    def test_the_advice_puts_the_power_limit_first(self):
        snapshot = {'limited': True, 'recent_throttled_s': 30, 'window_s': 30, 'episodes': 1, 'share': .99}
        result = advice(snapshot, 2560, 75, True, False, {'label': 'Standard', 'width': 2016, 'height': 2016}, False,
                        {'clock_mhz': 800, 'max_mhz': 2700, 'power_w': 15})
        self.assertIn('Its GPU ran at 800 of its 2700 MHz while busy, at about 15 W: a power limit (TDP) or a fixed GPU '
                      'clock is holding it back', result['message'])
        self.assertIn('leave the GPU clock on automatic', result['message'])
        self.assertIn('raise the TDP limit', result['message'])
        self.assertLess(result['message'].index('TDP'), result['message'].index('Or choose Standard'))
        self.assertEqual(result['short'], 'GPU held at 800 MHz · raise the TDP limit')
        self.assertEqual(result['starved']['clock_mhz'], 800)
        self.assertNotIn('overheating', result['message'])
        plain = advice(snapshot, 2560, 75, False, False, None, False)
        self.assertIsNone(plain['starved'])
        self.assertTrue(plain['short'].startswith('PC graphics at their limit'))
        self.assertIsNone(advice({'episodes': 0}, starved={'clock_mhz': 800, 'max_mhz': 2700})['starved'])

    def test_the_eye_state_says_why_the_region_does_not_follow(self):
        from test_session_lifecycle import SessionLifecycleTests
        case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        app = case.app()
        app.settings.update(foveation='eye-tracked', gaze=False)
        self.assertEqual(app.eye_state('balanced', None), 'off-here')
        self.assertEqual(app.eye_state('off', None), 'cpu')
        app.settings['gaze'] = True
        # RC4.5: VROSTBridge's Baballonia is set up, then calibrated, before the region follows the eyes.
        self.assertEqual(app.eye_state('balanced', None), 'setup')
        app.eye.manager.installed = lambda: True
        app.eye.manager.calibration = lambda: {'calibrated': False, 'date': None}
        self.assertEqual(app.eye_state('balanced', None), 'calibrate')
        app.eye.manager.calibration = lambda: {'calibrated': True, 'date': '2026-10-05'}
        self.assertEqual(app.eye_state('eye-tracked', None), '', 'not known before a session')
        self.assertEqual(app.eye_state('eye-tracked', {'mode': 'centred'}), '', 'the host has not said yet')
        self.assertEqual(app.eye_state('eye-tracked', {'mode': 'centred', 'gaze_known': True}), 'host-off')
        self.assertEqual(app.eye_state('eye-tracked', {'mode': 'eyes-waiting', 'gaze_known': True}), 'no-gaze')
        self.assertEqual(app.eye_state('eye-tracked', {'mode': 'eyes', 'gaze_known': True}), 'following')
        app.settings['foveation'] = 'balanced'
        self.assertEqual(app.eye_state('balanced', {'mode': 'centred', 'gaze_known': True}), '')

    def test_the_latest_sample_is_the_live_readout(self):
        clocks = GpuClocks()
        self.assertIsNone(clocks.latest())
        clocks.observe({'active': True, 'observed_monotonic': 1, 'gpus': [
            {'gpu_busy_percent': 58, 'selected_sclk_mhz': 800, 'max_sclk_mhz': 2700,
             'sensors': [{'clock_mhz': 812, 'average_power_w': 15.3}]}]})
        self.assertEqual(clocks.latest(), {'busy_percent': 58, 'clock_mhz': 812, 'max_mhz': 2700, 'power_w': 15})

    def test_the_maximum_clock_is_read_from_the_dpm_table(self):
        from launcher.performance_monitor import read_performance
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        sysfs = Path(temp.name)
        device = sysfs / 'class/drm/card1/device'
        (device / 'hwmon/hwmon3').mkdir(parents=True)
        (device / 'pp_dpm_sclk').write_text('0: 800Mhz *\n1: 1610Mhz\n2: 2700Mhz\n')
        (device / 'gpu_busy_percent').write_text('58\n')
        (device / 'hwmon/hwmon3/freq1_input').write_text('800000000\n')
        (device / 'hwmon/hwmon3/power1_average').write_text('15267000\n')
        value = read_performance(sysfs)
        gpu = value['gpus'][0]
        self.assertEqual((gpu['selected_sclk_mhz'], gpu['max_sclk_mhz'], gpu['sensors'][0]['clock_mhz']), (800, 2700, 800))

    def test_the_companion_feeds_samples_and_reports_the_starvation(self):
        from test_session_lifecycle import SessionLifecycleTests
        case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        app = case.app()
        samples = iter([sample(800, 59, at=1.0), sample(800, 58, at=2.0), sample(800, 60, at=3.0)])
        app.performance_monitor = Mock()
        app.performance_monitor.snapshot.side_effect = lambda: next(samples)
        for _ in range(3):
            app.check_graphics_limit()
        self.assertEqual(app.gpu_clocks.starved()['clock_mhz'], 800)
        with app.lock:
            app.graphics_limit.limited = True
            app.graphics_limit.episodes = 1
        status = app.graphics_status()
        self.assertEqual(status['starved']['max_mhz'], 2700)
        self.assertIn('TDP', status['message'])
        self.assertIsNone(status['gpu'], 'no live readout outside a session')
        app.running = lambda: True
        self.assertEqual(app.graphics_status()['gpu']['clock_mhz'], 800)


class GameModeRc44Tests(unittest.TestCase):
    def view(self, state):
        from launcher.game_ui import View
        view = View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.action = Mock()
        view.list_page = Mock()
        view.state = state
        return view

    def rows(self, view):
        view.page_stream()
        return {row[0]: row for row in view.list_page.call_args.args[1]}

    def test_eye_tracked_settings_show_with_eye_tracking_off_and_say_why(self):
        state = {'settings': {'foveation': 'eye-tracked', 'gaze': False, 'fov_region': 'small'}, 'running': False,
                 'system': {'foveation': {'choice': 'eye-tracked', 'preset': 'balanced', 'gaze_follow': False,
                                          'eye_state': 'off-here'}}}
        rows = self.rows(self.view(state))
        self.assertIn('fov-region', rows)
        self.assertIn('fov-edge', rows)
        self.assertIn('Used once eye tracking runs', rows['fov-region'][4])
        # RC4.5: the note row opens the Eye tracking page.
        self.assertEqual(rows['eyetrack'][1:3], ('Eye tracking is off', 'Balanced in use'))
        self.assertIn('This PC → Eye tracking', rows['eyetrack'][4])
        self.assertEqual(rows['foveation'][2], 'Eye-tracked (Balanced)')
        state['settings']['gaze'] = True
        state['system']['foveation'].update(preset='eye-tracked', eye_state='host-off')
        rows = self.rows(self.view(state))
        self.assertEqual(rows['eyetrack'][1], 'The host keeps the region centred')
        self.assertIn('Update VROSTBridge on the host PC', rows['eyetrack'][4])
        state['system']['foveation']['eye_state'] = 'no-gaze'
        self.assertIn('Eye tracking page', self.rows(self.view(state))['eyetrack'][4])
        state['system']['foveation']['eye_state'] = 'cpu'
        self.assertIn('CPU decoding', self.rows(self.view(state))['eyetrack'][4])
        state['system']['foveation']['eye_state'] = 'cameras'
        state['eye_tracking'] = {'message': "The Beyond 2e's eye cameras were not found."}
        self.assertIn('eye cameras were not found', self.rows(self.view(state))['eyetrack'][4])
        for following in ('following', ''):
            state['system']['foveation']['eye_state'] = following
            rows = self.rows(self.view(state))
            self.assertNotIn('eyetrack', rows)
            self.assertNotIn('Used once', rows['fov-region'][4])
        state = {'settings': {'foveation': 'balanced'}, 'system': {'foveation': {'preset': 'balanced'}}}
        rows = self.rows(self.view(state))
        self.assertNotIn('fov-region', rows)
        self.assertIn('separate from foveated encoding', rows['sharpening'][4])

    def test_the_session_card_names_the_power_limit_and_a_restart(self):
        state = {'settings': {}, 'running': True, 'state': 'streaming',
                 'graphics': {'state': 'limited', 'starved': {'clock_mhz': 800, 'max_mhz': 2700}, 'actions': []}}
        view = self.view(state)
        self.assertIn('held at 800 of its 2700 MHz. Raise the TDP limit', view.session_detail())
        self.assertIn('TDP', view.session_detail())
        state['runtime_restart'] = {'state': 'restarting', 'message': "SteamVR's compositor on this PC closed"}
        self.assertEqual(view.session_detail(), "SteamVR's compositor on this PC closed")


class CompositorCrashTests(unittest.TestCase):
    def app(self):
        from test_session_lifecycle import SessionLifecycleTests
        case = SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        app = case.app()
        app.running = lambda: True
        app.stream_ever_up = True
        app.log_compositor_crash_details = Mock()
        # Tests below replace app.closing with a Mock whose wait() returns at once. The companion's own threads (the
        # fovea sharer) wait on app.closing too: give them the real event back, set, so they end with the test instead
        # of spinning on the Mock for the rest of the run (1.0.0.7: their recorded calls filled the memory).
        closing = app.closing

        def end_threads():
            app.closing = closing
            closing.set()
        self.addCleanup(end_threads)
        return app

    def processes(self, *names, first=1):
        return {i: {'pid': i, 'path': '/steamvr/bin/linux64/' + name} for i, name in enumerate(names, first)}

    def use(self, app, *names, first=1, registry=True, running=None):
        """These SteamVR processes run now; registry=False: the process list cannot be read."""
        running = running if running is not None else self.processes(*names, first=first)
        app.local_runtime_processes = (lambda: running) if registry else (lambda: {})
        app.runtime_process_alive = lambda identity: bool(identity) and identity in running.values()

    def seen(self, app):
        """The session's SteamVR as the monitor records it once video arrives."""
        self.use(app, 'vrserver', 'vrcompositor')
        app.check_local_runtime()
        self.assertEqual(set(app.runtime_seen), {'vrserver', 'vrcompositor'})

    def test_two_checks_without_a_compositor_restart_the_session_once(self):
        app = self.app()
        app.local_compositor_lost = Mock()
        app.local_runtime_processes = lambda: {}  # unreadable before the session's SteamVR was seen
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        self.seen(app)
        self.use(app, 'vrserver', 'vrmonitor')
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        app.check_local_runtime()
        app.local_compositor_lost.assert_called_once_with(reason='compositor')
        app.local_compositor_lost.reset_mock()
        self.use(app, 'vrserver', 'vrcompositor')
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        self.use(app, 'vrmonitor')  # SteamVR gone as a whole
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_called_once_with(reason='server')
        app.local_compositor_lost.reset_mock()
        app.process = Mock()
        app.runtime_quit_requested = app.process  # ... but SteamVR asked the client to quit: not a crash
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        app.runtime_quit_requested = None
        app.stream_ever_up = False  # starting up: the startup path handles its own compositor
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()

    def test_a_compositor_steamvr_restarted_counts_only_with_stalled_frames(self):
        app = self.app()
        app.local_compositor_lost = Mock()
        self.seen(app)
        server = app.runtime_seen['vrserver']
        compositor = lambda pid: {'pid': pid, 'path': '/steamvr/bin/linux64/vrcompositor'}
        app.REPLACEMENT_PROBATION_S = .3
        self.use(app, running={1: server, 9: compositor(9)})  # SteamVR restarted its compositor
        app.check_local_runtime()
        self.assertEqual(app.local_runtime_lost(), 'replaced', 'on probation until frames have kept going')
        time.sleep(.35)
        for _ in range(3):
            app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        self.assertEqual(app.runtime_seen['vrcompositor']['pid'], 9, 'frames went on: the new one is watched')
        app.compositor_stalled = True  # the headset in standby later: not a crash
        for _ in range(3):
            app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        self.use(app, running={1: server, 11: compositor(11)})  # replaced while frames are stopped
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_called_once_with(reason='compositor')

    def test_a_replacement_whose_frames_never_come_is_lost(self):
        """The review's case: a quick replacement must not be adopted before the stall can show."""
        app = self.app()
        app.local_compositor_lost = Mock()
        self.seen(app)
        server = app.runtime_seen['vrserver']
        self.use(app, running={1: server, 9: {'pid': 9, 'path': '/steamvr/bin/linux64/vrcompositor'}})
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()
        app.compositor_stalled = True  # the client's 2 s stalled-frames line, within the probation
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_called_once_with(reason='compositor')
        self.assertEqual(app.runtime_seen['vrcompositor']['pid'], 2, 'never adopted')

    def test_a_process_list_that_cannot_be_read_is_not_a_crash(self):
        app = self.app()
        app.local_compositor_lost = Mock()
        self.seen(app)
        self.use(app, 'vrserver', 'vrcompositor', registry=False)  # the SteamVR registry is unreadable
        for _ in range(3):
            app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()

    def test_a_stop_asked_for_just_before_the_trigger_wins(self):
        app = self.app()
        app.action = Mock()
        app.session_cancelled.set()
        app.local_compositor_lost(native_exit=1)
        app.action.assert_not_called()
        self.assertFalse(app.runtime_restarting)
        self.assertIsNone(app.runtime_restart_status())

    def test_a_fatal_error_waits_for_steamvr_to_show_it_failed(self):
        app = self.app()
        self.seen(app)
        app.local_compositor_lost = Mock()
        app.stop = Mock()
        process = app.process = Mock()
        app.FATAL_RUNTIME_WAIT_S = 5
        worker = threading.Thread(target=app.native_failure, args=(process, 'Vulkan device lost'))
        worker.start()
        time.sleep(.3)
        self.assertEqual(app.startup_error, 'Vulkan device lost', 'the error shows at once')
        self.use(app, 'vrserver')  # the crashed compositor leaves /proc once its core is written
        worker.join(5)
        app.local_compositor_lost.assert_called_once_with(native_exit='FATAL Vulkan device lost', reason='compositor')
        app.stop.assert_not_called()
        self.assertEqual(app.startup_error, '')

    def test_nothing_is_judged_while_a_stop_runs(self):
        """The review's case: SteamVR closing during a Stop looks like a crash for seconds."""
        app = self.app()
        app.local_compositor_lost = Mock()
        self.seen(app)
        self.use(app, 'vrserver')
        app.stopping = app.process = Mock()
        app.check_local_runtime()
        app.check_local_runtime()
        app.stopping = None
        app.session_cancelled.set()  # a Stop by hand was asked for
        app.check_local_runtime()
        app.check_local_runtime()
        app.local_compositor_lost.assert_not_called()

    def test_the_restart_stops_and_starts_and_a_second_crash_stops(self):
        app = self.app()
        calls = []
        done = threading.Event()

        def action(body):
            calls.append(body['action'])
            if body['action'] == 'start':
                done.set()
        app.action = action
        app.closing = Mock()
        app.closing.wait.return_value = False
        app.local_compositor_lost()
        self.assertTrue(done.wait(5))
        for _ in range(50):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertEqual(calls, ['stop', 'start'])
        status = app.runtime_restart_status()
        self.assertEqual(status['state'], 'restarted')
        self.assertTrue(status['message'].startswith('Session restarted. '))
        self.assertIn("not even VROSTBridge's reconnecting card", status['message'])
        app.stop = Mock()
        app.local_compositor_lost()
        app.stop.assert_called_once_with()
        self.assertTrue(app.startup_error.startswith('Session stopped: it happened again within ten minutes. '))
        self.assertEqual(calls, ['stop', 'start'], 'no second automatic restart')

    def test_a_restart_waits_for_the_host_to_finish_stopping(self):
        from launcher.session_link import SessionError
        app = self.app()
        attempts = []

        def action(body):
            if body['action'] == 'start':
                attempts.append(1)
                if len(attempts) < 3:
                    raise SessionError('The previous host session must finish stopping first.')
        app.action = action
        app.closing = Mock()
        app.closing.wait.return_value = False
        app.local_compositor_lost()
        for _ in range(250):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(app.runtime_restart['state'], 'restarted')

    def test_a_native_exit_after_the_compositor_closed_restarts_instead_of_stopping(self):
        app = self.app()
        self.seen(app)
        self.use(app, 'vrserver')
        app.local_compositor_lost = Mock()
        app.stop = Mock()
        process = Mock()
        process.stdout = iter(['STATUS SteamVR compositor frames stopped advancing (2 s)\n'])
        process.wait.return_value = 1
        app.process = process
        app.stopping = None
        app.watch(process)
        app.local_compositor_lost.assert_called_once_with(native_exit=1, reason='compositor')
        app.stop.assert_not_called()
        self.assertEqual(app.startup_error, '')
        self.assertIsNone(app.last_exit, 'no error styling while the restart runs')
        self.assertTrue(app.compositor_stalled)

    def test_a_clean_exit_or_a_quit_steamvr_asked_for_just_stops(self):
        for code, lines in ((0, []), (1, ['STATUS Local SteamVR requested this client to quit; pid=4\n'])):
            app = self.app()
            self.seen(app)
            self.use(app, 'vrserver')
            app.local_compositor_lost = Mock()
            app.stop = Mock()
            process = Mock()
            process.stdout = iter(lines)
            process.wait.return_value = code
            app.process = process
            app.stopping = None
            app.watch(process)
            app.local_compositor_lost.assert_not_called()
            app.stop.assert_called_once_with()

    def test_a_fatal_error_after_steamvr_failed_restarts(self):
        app = self.app()
        self.seen(app)
        self.use(app, 'vrserver')
        app.local_compositor_lost = Mock()
        app.stop = Mock()
        process = app.process = Mock()
        app.native_failure(process, 'Vulkan device lost')
        app.local_compositor_lost.assert_called_once_with(native_exit='FATAL Vulkan device lost', reason='compositor')
        app.stop.assert_not_called()
        self.use(app, 'vrserver', 'vrcompositor')
        app.FATAL_RUNTIME_WAIT_S = .2
        app.native_failure(process, 'Vulkan device lost')  # SteamVR fine: the usual stop with the error
        app.stop.assert_called_once_with()
        self.assertEqual(app.startup_error, 'Vulkan device lost')

    def test_the_monitor_and_the_watcher_together_restart_only_once(self):
        app = self.app()
        calls = []
        release = threading.Event()

        def action(body):
            calls.append(body['action'])
            if body['action'] == 'stop':
                release.wait(5)
        app.action = action
        app.closing = Mock()
        app.closing.wait.return_value = False
        app.stop = Mock()
        app.local_compositor_lost()
        app.local_compositor_lost(native_exit=-13)  # the native client ending while the restart runs
        release.set()
        for _ in range(250):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertEqual(calls, ['stop', 'start'])
        app.stop.assert_not_called()

    def test_a_stop_or_start_by_hand_cancels_the_automatic_start(self):
        app = self.app()
        calls = []

        def action(body):
            calls.append(body['action'])
            self.assertTrue(body.get('_automatic_restart'))
        app.action = action
        app.closing = Mock()

        def user_stops_meanwhile(_seconds):
            app.manual_actions += 1
            return False
        app.closing.wait.side_effect = user_stops_meanwhile
        app.local_compositor_lost()
        for _ in range(250):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertEqual(calls, ['stop'])
        self.assertIsNone(app.runtime_restart_status())
        self.assertEqual(app.startup_error, '')

    def test_only_a_stop_or_start_by_hand_counts_and_an_old_automatic_action_is_refused(self):
        from launcher.app import RestartSuperseded
        app = self.app()
        app.stop = Mock()
        app.action({'action': 'stop', '_automatic_restart': True, '_manual_actions': 0})
        self.assertEqual(app.manual_actions, 0)
        app.stop.assert_called_once_with()
        app.runtime_restart = {'state': 'failed', 'message': 'old'}
        app.action({'action': 'stop'})
        self.assertEqual(app.manual_actions, 1)
        self.assertIsNone(app.runtime_restart, 'a Stop by hand clears the restart message')
        with self.assertRaises(RestartSuperseded):
            app.action({'action': 'start', '_automatic_restart': True, '_manual_actions': 0})

    def test_a_stop_by_hand_during_the_stop_is_not_undone(self):
        """The review's reproduction: the restart's Start after a Stop by hand must not happen."""
        app = self.app()
        calls = []

        def action(body):
            calls.append(body['action'])
            if body['action'] == 'stop':
                app.manual_actions += 1  # the user's Stop arrives while the automatic one runs
        app.action = action
        app.closing = Mock()
        app.closing.wait.return_value = False
        app.local_compositor_lost()
        for _ in range(250):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertEqual(calls, ['stop'])
        self.assertIsNone(app.runtime_restart_status())

    def test_a_steamvr_server_crash_is_named(self):
        app = self.app()
        app.action = Mock()
        app.closing = Mock()
        app.closing.wait.return_value = False
        app.local_compositor_lost(reason='server')
        for _ in range(250):
            if not app.runtime_restarting:
                break
            time.sleep(.02)
        self.assertTrue(app.runtime_restart['message'].startswith('Session restarted. SteamVR on this PC closed unexpectedly'))

    def test_a_new_session_clears_an_earlier_restart_message(self):
        app = self.app()
        app.running = lambda: False
        app.watch = lambda process: None
        app.runtime_restart = {'state': 'failed', 'message': 'old'}
        with patch('launcher.app.subprocess.Popen') as popen:
            popen.return_value.stdout = iter([])
            app.launch(['native'])
            self.assertIsNone(app.runtime_restart_status())
            app.runtime_restart = {'state': 'restarting', 'message': 'now'}
            app.runtime_restarting = True  # the automatic restart's own Start keeps its message
            app.launch(['native'])
            self.assertEqual(app.runtime_restart_status()['message'], 'now')

    def test_the_crash_is_logged_with_its_core_dump_and_kernel_graphics_lines(self):
        from launcher import app as companion
        app = self.app()
        del app.log_compositor_crash_details
        logged = []
        app.log = logged.append
        outputs = {
            'coredumpctl': 'Thu 2026-01-01 12:00:02 UTC 4242 1000 1000 SIGSEGV present /home/u/vrcompositor 12.1M\n',
            'journalctl': ('2026-01-01T12:00:01+0000 host kernel: usb 3-2: new device\n'
                           '2026-01-01T12:00:02+0000 host kernel: amdgpu 0000:03:00.0: amdgpu: ring gfx_0.0.0 timeout\n'
                           '2026-01-01T12:00:02+0000 host kernel: wlp1s0: associated\n')}

        def run(command, **kwargs):
            self.assertNotIn('_MEI', kwargs['env'].get('LD_LIBRARY_PATH', ''))
            return Mock(stdout=outputs[command[0]])
        with patch.object(companion.subprocess, 'run', side_effect=run):
            app.log_compositor_crash_details()
        self.assertEqual(len(logged), 2, logged)
        self.assertIn('SIGSEGV', logged[0])
        self.assertIn('ring gfx_0.0.0 timeout', logged[1])
        logged.clear()
        with patch.object(companion.subprocess, 'run', side_effect=OSError('missing')):
            app.log_compositor_crash_details()
        self.assertEqual(logged, ['STEAMVR crash detail: no core dump entry or kernel graphics message found (or not readable).'])

    def test_status_carries_the_restart(self):
        app = self.app()
        app.runtime_restart = {'state': 'restarted', 'message': 'm', 'until': time.monotonic() + 100}
        self.assertEqual(app.runtime_restart_status(), {'state': 'restarted', 'message': 'm'})
        app.runtime_restart['until'] = time.monotonic() - 1
        self.assertIsNone(app.runtime_restart_status())
        page = (ROOT / 'launcher/desktop.html').read_text()
        self.assertIn('if(s.runtime_restart?.message)hint=s.runtime_restart.message;', page)


CHILD = (
    "import ctypes, signal, sys, time\n"
    "ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG, as VROSTBridgeLease does\n"
    "print('ready', flush=True)\n"
    "time.sleep(20)\n")


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux parent-death signal')
class LeaseHelperLifetimeTests(unittest.TestCase):
    """The display adapter must outlive the Start request that started it (RC4.4)."""

    def start_from_a_finishing_thread(self, start):
        started = {}

        def request():  # like a Start request: it starts the helper, keeps working, then ends
            process = start([sys.executable, '-c', CHILD], stdout=subprocess.PIPE, text=True)
            started['process'] = process
            self.assertEqual(process.stdout.readline().strip(), 'ready')
        thread = threading.Thread(target=request)
        thread.start()
        thread.join()
        process = started['process']
        def finish():
            if process.poll() is None:
                process.kill()
            process.wait(5)
            process.stdout.close()
        self.addCleanup(finish)
        try:
            process.wait(1.5)
        except subprocess.TimeoutExpired:
            pass
        return process

    def test_the_starter_thread_keeps_the_helper_alive(self):
        from launcher.game_mode import PROCESS_STARTER
        self.assertIsNone(self.start_from_a_finishing_thread(PROCESS_STARTER.popen).poll())

    def test_without_it_the_helper_is_terminated(self):
        self.assertEqual(self.start_from_a_finishing_thread(subprocess.Popen).poll(), -signal.SIGTERM)

    def test_a_start_its_caller_gave_up_on_is_ended(self):
        from launcher.game_mode import ProcessStarter
        starter = ProcessStarter()
        starter.REPLY_TIMEOUT_S = .2
        release = threading.Event()
        started = []
        real = subprocess.Popen

        def slow(args, **options):
            release.wait(5)  # a start that blocks longer than its caller waits
            process = real(args, **options)
            started.append(process)
            return process
        with patch('launcher.game_mode.subprocess.Popen', side_effect=slow):
            with self.assertRaises(OSError):
                starter.popen([sys.executable, '-c', 'import time; time.sleep(20)'])
            release.set()
            for _ in range(100):
                if started and started[0].poll() is not None:
                    break
                time.sleep(.05)
        self.assertTrue(started)
        self.assertIsNotNone(started[0].poll(), 'the abandoned process was ended')

    def test_the_lease_runtime_uses_the_starter(self):
        source = (ROOT / 'launcher/game_mode.py').read_text()
        self.assertIn('p=PROCESS_STARTER.popen(args,env=env', source)
        self.assertNotIn('p=subprocess.Popen(args,env=env', source)


class NativeRc44Tests(unittest.TestCase):
    def test_the_native_programs_survive_a_closed_pipe(self):
        main = (ROOT / 'client/platform/linux/main.cpp').read_text()
        self.assertLess(main.index('std::signal(SIGPIPE, SIG_IGN);'), main.index('--verify-runtime'))
        probe = (ROOT / 'client/platform/linux/runtime_probe.cpp').read_text()
        self.assertIn('std::signal(SIGPIPE, SIG_IGN);', probe)
        self.assertIn('STATUS SteamVR compositor frames stopped advancing (2 s)', main)
        self.assertIn('STATUS SteamVR compositor frames are advancing again', main)
        # ... and stops when the companion is gone (its command pipe closes) instead of running on.
        self.assertIn('S_ISFIFO(st.st_mode)', main)
        self.assertEqual(main.count('STATUS The VROSTBridge companion closed its command pipe; stopping'), 2)
        self.assertIn('(inputPoll.revents&(POLLHUP|POLLERR|POLLNVAL))', main)

    def test_the_headset_images_are_drawn_at_twice_the_density(self):
        overlay = (ROOT / 'client/platform/linux/status_overlay.hpp').read_text()
        self.assertIn('static constexpr float preferredScale = 2.f;', overlay)
        self.assertIn('ui::fittedScale(image.width(), image.height(), scale, rawBudget)', overlay)
        self.assertIn('float(width) * metresPerPixel / std::max(scale, 1.f)', overlay)
        # Drawn once per update: the next image starts from the scale the last one of its kind allowed.
        self.assertIn('renderer_.card(content, scale); }, cardScale_);', overlay)
        self.assertIn('renderer_.pill(text, scale); }, pillScale_);', overlay)
        self.assertIn('if (fitted < scale) image = drawAt(scale = fitted);', overlay)

    def test_the_fused_pass_uses_five_taps_and_the_analytic_slope(self):
        shader = (ROOT / 'client/platform/linux/fused.comp').read_text()
        self.assertEqual(shader.count('texture(luma,'), 10,
                         'five Catmull-Rom taps, four B-spline taps for the reduced edges (1.0.0.3) and one tap helper')
        # 1.0.0.11: the same root and slope (1/r) from per-frame constants (tests/test_vrostbridge_1011.py).
        self.assertIn('return vec2(a.low.z*r+a.low.w,a.centre.w/max(r,1e-6));', shader)
        self.assertNotIn('encodedAt(clamp(s+vec2(1./eyeSize.x', shader)


if __name__ == '__main__':
    unittest.main()
