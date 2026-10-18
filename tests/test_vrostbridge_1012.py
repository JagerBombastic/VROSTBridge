"""VROSTBridge 1.0.0.12: what the first real session of 1.0.0.11 showed.

1.0.0.11's first session on hardware (Beat Saber at Native 2560 per eye, 75 Hz, eye-tracked foveation, a handheld
headset PC on Bazzite) streamed well, with three things to put right:
- the automatic bitrate went from 65 to 30 Mbit/s in the first 80 seconds, while the headset PC's GPU sat at 800 MHz
  and the game was starting, and stayed at 30 for the eight minutes after the clock was held at 1300 MHz: three steps
  down asked for 160 clean seconds before a step up, and the rendering PC's own stalls (40-80 ms with no frame sent)
  broke every run of them. A raised GPU clock now gives the bitrate a fresh start, and a second in which frames never
  arrived is not counted against the bitrate (launcher/stream_budget.py);
- the headset PC found its GPU clock afresh four and a half minutes in; the clock a session needed is now held from
  the next session's start (launcher/session_tuning.py);
- the host page listed SteamVR's own drivers as third-party add-ons and suggested turning them off (host/addons.py).
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from host.addons import detect  # noqa: E402
from launcher import stream_budget  # noqa: E402
from launcher.stream_budget import BitrateGovernor, frames_missing  # noqa: E402
from launcher.session_tuning import MEMORY_NAME, SessionTuning  # noqa: E402
from launcher.power_balance import GPU_INTERFACE  # noqa: E402
from test_session_lifecycle import SessionLifecycleTests  # noqa: E402
from test_vrostbridge_1008 import FakeSteamOSManager, sample, timing  # noqa: E402


def second(fps=75.0, timeouts=0, arrival=13.3, lost=0, cadence='full', decode=4.7, refresh=75.0, skipped=0):
    """One TIMING line's fields (the governor reads cumulative counters, so timeouts and lost are running totals)."""
    value = timing(cadence=cadence, decode=decode, arrival=arrival, lost=lost, timeouts=timeouts, skipped=skipped,
                   fps=fps)
    value['refresh_hz'] = '%.3f' % refresh
    return value


# ------------------------------------------------------------------------------------------------ version
def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_12_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1013.py and later.
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 12))
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "' + CLIENT_VERSION + '"', read('core/version.hpp'))
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.12'))


# ------------------------------------------------------------------------------------------------ add-ons
class AddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'Steam/steamapps/common/SteamVR'

    def driver(self, path, name):
        path.mkdir(parents=True)
        (path / 'driver.vrdrivermanifest').write_text(json.dumps({'name': name}))
        return path

    def registry(self, external, settings=None):
        config = self.root / 'config'
        config.mkdir(exist_ok=True)
        (config / 'steamvr.vrsettings').write_text(json.dumps(settings or {}))
        path = self.root / 'openvrpaths.vrpath'
        path.write_text(json.dumps({'runtime': [str(self.runtime)], 'config': [str(config)],
                                    'external_drivers': [str(item) for item in external]}))
        return path

    def test_steamvrs_own_drivers_are_never_listed(self):
        # The 1.0.0.11 test's host: SteamVR's drivers folder as SteamVR ships it, a year of new hardware later.
        for name in ('indexcontroller', 'frame_hmd', 'frame_controller', 'prism', 'roy', 'tundra_labs', 'lighthouse',
                     'gamepad', 'some_future_driver'):
            self.driver(self.runtime / 'drivers' / name, name)
        self.assertEqual(detect(registry=self.registry([])), [])
        # Registered from inside SteamVR's own folder is still SteamVR's.
        registered = self.registry([self.runtime / 'drivers' / 'lighthouse'])
        self.assertEqual(detect(registry=registered), [])

    def test_add_ons_registered_from_outside_steamvr_are_listed(self):
        self.driver(self.runtime / 'drivers' / 'indexcontroller', 'indexcontroller')
        beyond = self.driver(self.root / 'Steam/steamapps/common/Bigscreen Beyond Driver', 'bigscreenbeyond')
        helper = self.driver(beyond / 'bin/steamvr/BeyondSteamVR', 'BeyondSteamVR')
        vendor = self.driver(self.root / 'Vendor/driver', 'vendor_tracker')
        own = self.driver(self.root / 'VROSTBridge/dist/host', 'alvr_server')
        off = self.driver(self.root / 'Old/driver', 'old_helper')
        found = detect(registry=self.registry([own, beyond, helper, vendor, off], {'driver_old_helper': {'enable': False}}),
                       own_driver='alvr_server', own_directory=own)
        self.assertEqual([item['name'] for item in found], ['BeyondSteamVR', 'bigscreenbeyond', 'vendor_tracker'])
        self.assertEqual({item['source'] for item in found}, {'registered'})
        # Bigscreen's helper is named BeyondSteamVR: its folder says whose it is.
        self.assertIn('client PC', found[0]['advice'])
        self.assertIn('client PC', found[1]['advice'])
        self.assertEqual(found[2]['advice'], 'Third-party SteamVR add-on loaded on the host.')

    def test_the_name_list_is_gone(self):
        source = (ROOT / 'host/addons.py').read_text(encoding='utf-8')
        self.assertNotIn('STEAMVR_BUILTIN', source)
        self.assertNotIn("'drivers'", source, 'SteamVR\'s own drivers folder is not searched')


# ------------------------------------------------------------------------------------------------ the bitrate
class SourceGapTests(unittest.TestCase):
    def governor(self, start=65, ceiling=65):
        governor = BitrateGovernor()
        governor.reset(start, ceiling, 75)
        governor.warmup = 0
        return governor

    def test_frames_missing_is_the_refresh_rate_less_the_frames_received(self):
        self.assertAlmostEqual(frames_missing({'refresh_hz': '75.000', 'packet_fps': '72.996'}), 2.004, places=3)
        self.assertEqual(frames_missing({'refresh_hz': '75.000', 'packet_fps': '75.958'}), 0.0)
        self.assertAlmostEqual(frames_missing({'packet_fps': '70'}, 75.0), 5.0)
        self.assertEqual(frames_missing({'refresh_hz': '75.000'}), 0.0)
        self.assertEqual(frames_missing({}), 0.0)

    def test_a_second_the_rendering_pc_sent_nothing_in_is_not_held_against_the_bitrate(self):
        # The 1.0.0.11 test's stall at 19:27:48: 73 frames, 13 frames given up on, a 41 ms gap.
        governor = self.governor()
        governor.classify(second(timeouts=505, arrival=20.2))
        stall = governor.classify(second(fps=72.996, timeouts=518, arrival=21.667))
        self.assertTrue(stall['source_gap'])
        self.assertFalse(stall['late'])
        self.assertFalse(stall['uneven'])
        self.assertFalse(stall['trouble'])
        self.assertEqual(stall['arrival_ms'], 0.0, 'nor counted in the arrival history')

    def test_frames_that_arrive_late_still_count(self):
        governor = self.governor()
        governor.classify(second(timeouts=505, arrival=20.2))
        late = governor.classify(second(fps=75.0, timeouts=518, arrival=21.667))
        self.assertFalse(late['source_gap'])
        self.assertTrue(late['late'] and late['uneven'] and late['trouble'])

    def test_frames_lost_on_the_network_are_not_a_source_gap(self):
        governor = self.governor()
        governor.classify(second(timeouts=505, lost=5))
        lost = governor.classify(second(fps=73.0, timeouts=518, lost=6, arrival=21.0))
        self.assertFalse(lost['source_gap'])
        self.assertTrue(lost['trouble'])

    def test_stalls_from_the_rendering_pc_no_longer_hold_the_bitrate_down(self):
        governor = self.governor()
        lost = timeouts = 0
        # Three steps down for lost frames: 65 -> 50 -> 40 -> 30, then the patience of three steps (160 s).
        while governor.steps_down < 3:
            lost += 1
            governor.observe(second(lost=lost, timeouts=timeouts))
        self.assertEqual(governor.mbps, 30)
        self.assertEqual(governor.up_after, stream_budget.CLEAN_UP_MAX_S)
        # Then 170 s of a clean stream with a stall from the rendering PC every 30 s, as in the 1.0.0.11 test.
        changes = []
        for number in range(170):
            stall = number % 30 == 29
            timeouts += 13 if stall else 0
            value = governor.observe(second(fps=72.996 if stall else 75.0, lost=lost, timeouts=timeouts,
                                            arrival=21.7 if stall else 20.3))
            if value is not None:
                changes.append(value)
        self.assertEqual(changes, [35], 'a step up after 160 clean seconds, stalls and all')


class FreshStartTests(unittest.TestCase):
    def stepped_down(self):
        governor = BitrateGovernor()
        governor.reset(65, 65, 75)
        governor.warmup = 0
        lost = 0
        while governor.steps_down < 3:
            lost += 1
            governor.observe(second(lost=lost))
        self.assertEqual(governor.mbps, 30)
        return governor, lost

    def test_a_raised_gpu_clock_takes_the_bitrate_back_to_the_start(self):
        governor, lost = self.stepped_down()
        self.assertEqual(governor.fresh_start('back to the start: the GPU clock was raised'), 65)
        snapshot = governor.snapshot()
        self.assertEqual((snapshot['mbps'], snapshot['steps_down'], snapshot['steps_up'], snapshot['fresh_starts']),
                         (65, 3, 1, 1))
        self.assertEqual(snapshot['history'][-1], {'from': 30, 'to': 65, 'reason': 'back to the start: the GPU clock was raised'})
        self.assertEqual(governor.up_after, stream_budget.CLEAN_UP_S, 'the first step\'s patience again')
        # The warm-up: the change and the new clock settle before anything is judged.
        for _ in range(stream_budget.WARMUP_S):
            lost += 1
            self.assertIsNone(governor.observe(second(lost=lost)))
        self.assertEqual(len(governor.window), 0)
        # A step down after it counts as the first again.
        for _ in range(3):
            lost += 1
            governor.observe(second(lost=lost))
        self.assertEqual(governor.mbps, 50)
        self.assertEqual(governor.up_after, stream_budget.CLEAN_UP_S)

    def test_nothing_to_undo_without_a_step_down(self):
        governor = BitrateGovernor()
        governor.reset(65, 65, 75)
        self.assertIsNone(governor.fresh_start('back to the start'))
        self.assertEqual(governor.snapshot()['fresh_starts'], 0)
        inactive = BitrateGovernor()
        inactive.reset(None, 65, 75)
        self.assertIsNone(inactive.fresh_start('back to the start'))

    def test_a_pending_check_is_dropped(self):
        governor, _ = self.stepped_down()
        governor.check = {'from': 40, 'before': 5.0, 'seconds': 0, 'after': []}
        governor.arrival_check = {'from': 40, 'before': 20.0, 'seconds': 0, 'after': []}
        governor.fresh_start('back to the start')
        self.assertIsNone(governor.check)
        self.assertIsNone(governor.arrival_check)


class CompanionTests(SessionLifecycleTests):
    def test_a_raised_gpu_clock_gives_the_bitrate_a_fresh_start(self):
        app = self.app()

        class Tuning:
            active, raises = True, 0

            def observe(self, sample, gaze_rate=None):
                self.raises += 1

        app.session_tuning = Tuning()
        app.tuning_started = True
        app.bitrate_governor.reset(65, 65, 75)
        app.bitrate_governor.mbps = 30
        app.bitrate_governor.steps_down = app.bitrate_governor.streak_down = 3
        with patch.object(app, 'running', return_value=True):
            app.balance_performance({'active': True, 'observed_monotonic': 2})
        self.assertEqual(app.bitrate_governor.mbps, 65)
        self.assertEqual(app.bitrate_note, 'BITRATE 30 -> 65 Mbit/s (back to the start: the GPU clock was raised)')
        # No raise, no change.
        app.bitrate_governor.mbps = 30
        app.session_tuning.observe = lambda sample, gaze_rate=None: None
        with patch.object(app, 'running', return_value=True):
            app.balance_performance({'active': True, 'observed_monotonic': 3})
        self.assertEqual(app.bitrate_governor.mbps, 30)


# ------------------------------------------------------------------------------------------------ the GPU clock
class RememberedClockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.logs = []

    def tuning(self, manager):
        return SessionTuning(runner=manager, log=self.logs.append, busctl='busctl', state_directory=self.state)

    def late(self, tuning, seconds=10):
        for _ in range(seconds):
            tuning.note_timing(second(cadence='late_or_paired'))

    def raised(self, manager):
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        self.late(tuning)
        tuning.observe(sample(2))
        self.assertEqual(tuning.observe(sample(3)), 1300)
        self.assertEqual(tuning.raises, 1)
        return tuning

    def memory(self):
        path = self.state / MEMORY_NAME
        return json.loads(path.read_text()) if path.exists() else None

    def test_the_clock_a_session_needed_is_held_from_the_next_start(self):
        manager = FakeSteamOSManager()
        self.raised(manager).end()
        self.assertEqual(self.memory(), {'version': 1, 'clock_mhz': 1300, 'clock_max_mhz': 2700, 'charger': True})
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto', 'set back at the stop')
        tuning = self.tuning(manager)
        status = tuning.begin(sample(10))
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'manual')
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1300)
        self.assertIn('GPU held at 1300 MHz from the start, as the last session needed', status['message'])
        self.assertEqual(tuning.raises, 0, 'not a raise: nothing for the bitrate to undo')
        tuning.end()
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto')
        self.assertIsNotNone(self.memory(), 'kept for the next session')

    def test_a_gpu_set_by_hand_is_left_alone(self):
        self.raised(FakeSteamOSManager()).end()
        manager = FakeSteamOSManager(level='manual', clock=1800)
        status = self.tuning(manager).begin(sample(10))
        self.assertEqual(manager.props[(GPU_INTERFACE, 'ManualGpuClock')], 1800)
        self.assertNotIn('from the start', status['message'])

    def test_only_for_the_same_gpu_on_the_same_power_source(self):
        self.raised(FakeSteamOSManager()).end()
        other = FakeSteamOSManager(clock_max=2200)
        self.assertNotIn('from the start', self.tuning(other).begin(sample(10))['message'])
        self.assertEqual([name for name, _ in other.sets if name == 'ManualGpuClock'], [])
        battery = FakeSteamOSManager()
        self.assertNotIn('from the start', self.tuning(battery).begin(sample(10, mains=False))['message'])
        self.assertEqual(battery.sets, [])

    def test_a_session_that_has_to_set_it_back_down_forgets_it(self):
        self.raised(FakeSteamOSManager()).end()
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(10))
        tuning.observe(sample(11, cpu=900, gpu=1300))
        self.assertEqual(tuning.observe(sample(12, cpu=900, gpu=1300)), 0, 'below the clock it started at: automatic')
        self.assertEqual(manager.props[(GPU_INTERFACE, 'GpuPerformanceLevel')], 'auto')
        self.assertIsNone(self.memory())

    def test_a_clock_changed_elsewhere_is_forgotten(self):
        manager = FakeSteamOSManager()
        tuning = self.raised(manager)
        manager.props[(GPU_INTERFACE, 'ManualGpuClock')] = 1800  # Steam's Performance menu
        tuning.observe(sample(4, gpu=1800))
        self.assertIsNone(self.memory())

    def test_frames_that_never_arrived_do_not_raise_the_clock(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        tuning.begin(sample(1))
        timeouts = 0
        for _ in range(10):
            timeouts += 4
            tuning.note_timing(second(fps=72.0, timeouts=timeouts))
        tuning.observe(sample(2))
        self.assertIsNone(tuning.observe(sample(3)))
        self.assertEqual([name for name, _ in manager.sets], ['TdpLimit'])
        # A frame this PC could not show still counts.
        skipped = 0
        for _ in range(10):
            skipped += 1
            tuning.note_timing(second(fps=72.0, timeouts=timeouts, skipped=skipped))
        tuning.observe(sample(4))
        self.assertEqual(tuning.observe(sample(5)), 1300)


if __name__ == '__main__':
    unittest.main()
