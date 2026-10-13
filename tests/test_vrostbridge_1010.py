"""VROSTBridge 1.0.0.10: the menu button, Game Mode's headset display, the bitrate on Wi-Fi, the base stations.

From the 1.0.0.9 test:
- The system (menu) button opened the rendering PC's menu only sometimes. While it is held, the headset PC's own
  SteamVR takes input focus from the client (its long-press recentre), and without focus every action reads
  released: the host saw a press of a few milliseconds or none. The client now forwards one clean press, also read
  from SteamVR's own button event, and keeps the other controls through a short loss of focus.
- Game Mode started SteamVR, but its compositor reached a display without leasing and failed with error 496. SteamVR
  now gets an environment without gamescope's display marker and a headset display under a plain name; a failure is
  explained at once, says which display SteamVR's server was given, and the start's log is kept for the reports.
- The automatic bitrate stepped from 40 to 20 Mbit/s for frames arriving unevenly over Wi-Fi, which arrived no more
  evenly at 20. Such a step is now checked and undone when it did not help.
- One base station refused both tries again; it now gets three, each after a fresh search, with its signal reported.

The C++ parts (the system-button latch, the focus grace, SteamVR's button events) are tested in tests/input_tests.cpp.
"""
import json
import os
from pathlib import Path
import stat
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

from headset_fixture import HEADSET_OUTPUT  # noqa: E402
from launcher import base_stations, client_runtime  # noqa: E402
from launcher.base_stations import BaseStations  # noqa: E402
from launcher.client_runtime import (ClientRuntime, compositor_findings, lease_failure_message,  # noqa: E402
                                     runtime_launch_environment, server_display_environment, server_display_note)
from launcher.game_mode import GameModeStartLog, LeaseRuntime, display_name  # noqa: E402
from launcher.stream_budget import BitrateGovernor  # noqa: E402
from test_vrostbridge_1008 import STATION, FakeBlueZ, done  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


# What SteamVR's compositor logged in the 1.0.0.9 test: in Game Mode, and then in Desktop Mode.
GAME_MODE_COMPOSITOR = '''Sun Oct 11 2026 19:25:00.703549 [Info] - Creating CHmdWindowSDL!
Sun Oct 11 2026 19:25:00.703629 [Info] - CHmdWindowSDL: Using Wayland
Sun Oct 11 2026 19:25:00.746203 [Error] - CHmdWindowSDL: No support for wl_drm_lease. This is needed for VR under Wayland.
Sun Oct 11 2026 19:25:00.746260 [Error] - CHmdWindowSDL: Unable to obtain drm lease FD from wayland compositor
Sun Oct 11 2026 19:25:00.746447 [Error] - Error making window!
Sun Oct 11 2026 19:25:00.777516 [Info] - Failed to initialize compositor
Sun Oct 11 2026 19:25:00.777564 [Info] - Failed to start compositor: VRInitError_Compositor_CannotDRMLeaseDisplay
'''
DESKTOP_COMPOSITOR = '''Sun Oct 11 2026 19:26:56.853731 [Info] - CHmdWindowSDL: Using Wayland
Sun Oct 11 2026 19:26:56.900376 [Info] - WaylandHMDState: Got interface: wp_drm_lease_device_v1
Sun Oct 11 2026 19:26:56.901025 [Info] - WaylandHMDLeaseDevice::LeaseDevice_DrmFD: /dev/dri/card1
Sun Oct 11 2026 19:26:57.258721 [Info] - Startup Complete (0.408820 seconds)
'''


# ------------------------------------------------------------------------------------------------ version
class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_10_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1011.py and later.
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 10))
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "' + CLIENT_VERSION + '"', read('core/version.hpp'))
        # A two-digit last part is a release version to the installers' maintenance too.
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.10'))


# ------------------------------------------------------------------------------------------------ the menu button
class MenuButtonTests(unittest.TestCase):
    def test_the_system_button_is_one_clean_press(self):
        protocol = read('client/device/input_protocol.hpp')
        self.assertIn('class SystemButtonLatch', protocol)
        self.assertIn('static constexpr uint64_t minHoldNs = 120000000ULL;', protocol)
        self.assertIn('inline constexpr uint64_t focusGraceNs = 500000000ULL;', protocol)
        self.assertIn('struct SystemButtonEvents', protocol)
        device = read('client/device/openvr_device.cpp')
        # SteamVR's own event for the system button, kept per hand with its time.
        self.assertIn('case vr::VREvent_ButtonPress:', device)
        self.assertIn('ev.data.controller.button == vr::k_EButton_System', device)
        self.assertIn('diagnostics.systemLatch.update(sampleNow, known, pressed)', device)
        # A short loss of focus keeps the other controls; the system button is never held this way.
        self.assertIn('holdThroughFocusLoss(connected, focusedNow, focusLostAt, sampleNow)', device)
        self.assertIn('if (hold && !systemButton && std::isfinite(previous[hand][i]))', device)
        # Each press is logged: whether focus was lost meanwhile, and whether the button went unread
        # (no focus and no event), and the INPUT line counts what happened.
        self.assertIn('"SYSTEM_BUTTON hand="', device)
        self.assertIn('" focus=" + (diagnostics.systemFocusLost ? "lost" : "kept")', device)
        self.assertIn('" unread=" + (diagnostics.systemLatch.lastUnseen() ? "1" : "0")', device)
        for field in ('focus_losses', 'focus_brief', 'system_events', 'system_focus_lost', 'system_unread'):
            self.assertIn('\\"' + field + '\\":', device)
        self.assertIn('system-button latch, focus grace', read('tests/input_tests.cpp'))


# ------------------------------------------------------------------------------------------------ Game Mode
class LeaseHelperSourceTests(unittest.TestCase):
    def test_the_headset_display_has_a_plain_name_and_says_who_reached_it(self):
        lease = read('client/platform/linux/lease.cpp')
        self.assertIn('const std::string name="vrostbridge-headset-"+std::to_string(getpid());', lease)
        self.assertNotIn('vrostbridge-wayland-', lease)
        self.assertIn('LEASE_CLIENT connected pid=', lease)
        self.assertIn('LEASE_CLIENT bound pid=', lease)
        self.assertIn('wl_display_add_client_created_listener(server.display,&clientListener);', lease)
        # Only a socket of this user's is replaced, and only the one the helper made is removed.
        self.assertIn('if(!S_ISSOCK(old.st_mode)||old.st_uid!=getuid())throw std::runtime_error("Wayland name already taken");', lease)
        self.assertIn('path=where;  // ours from here on: removed at exit', lease)
        self.assertEqual(display_name(1234), 'vrostbridge-headset-1234')


FAKE_HELPER = '''#!{python}
import json, os, socket, sys, time
name = 'vrostbridge-headset-%d' % os.getpid()
path = os.path.join(os.environ['XDG_RUNTIME_DIR'], name)
server = socket.socket(socket.AF_UNIX)
server.bind(path)
os.chmod(path, 0o600)
server.listen(1)
print('LEASE_READY ' + json.dumps({{'wayland_display': {shown} or name, 'path': path, 'connector_id': 135}}), flush=True)
try:
    time.sleep(30)
finally:
    os.unlink(path)
'''


class LeaseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'app'
        self.root.mkdir()
        self.runtime_dir = Path(self.temp.name) / 'run'
        self.runtime_dir.mkdir(mode=0o700)

    def runtime(self, shown='None'):
        helper = self.root / 'VROSTBridgeLease'
        helper.write_text(FAKE_HELPER.format(python=sys.executable, shown=shown))
        helper.chmod(0o700)
        setup = Mock()
        setup.details.return_value = {'name': 'DP-1', 'card': '/dev/dri/card1', 'connector_id': 135, 'edid_hex': '00',
                                      'lease_socket': str(self.runtime_dir / 'gamescope-lease.sock')}
        lease = LeaseRuntime(self.root, setup, log=lambda line: None, on_failure=lambda *a: None)
        self.addCleanup(lease.close)
        return lease

    def test_steamvr_gets_the_plain_display_name(self):
        lease = self.runtime()
        with patch('launcher.game_mode.in_game_mode', return_value=True), \
                patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.runtime_dir), 'DISPLAY': ':1'}):
            environment = lease.prepare(threading.Event())
        name = environment['WAYLAND_DISPLAY']
        self.assertTrue(name.startswith('vrostbridge-headset-'), name)
        self.assertNotIn('/', name)
        self.assertTrue(stat.S_ISSOCK((self.runtime_dir / name).lstat().st_mode))
        self.assertEqual({key: environment[key] for key in ('DISPLAY', 'SDL_VIDEODRIVER', 'QT_QPA_PLATFORM')},
                         {'DISPLAY': ':1', 'SDL_VIDEODRIVER': 'x11', 'QT_QPA_PLATFORM': 'xcb'})
        self.assertEqual(lease.display, name)

    def test_another_name_is_refused(self):
        lease = self.runtime(shown="'gamescope-0'")
        with patch('launcher.game_mode.in_game_mode', return_value=True), \
                patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.runtime_dir), 'DISPLAY': ':1'}):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                lease.prepare(threading.Event())


class SteamVREnvironmentTests(unittest.TestCase):
    def test_gamescope_display_marker_is_removed_only_with_the_headset_display(self):
        with patch.dict(os.environ, {'GAMESCOPE_WAYLAND_DISPLAY': 'gamescope-0', 'WAYLAND_DISPLAY': 'gamescope-0'}):
            game = runtime_launch_environment({'WAYLAND_DISPLAY': 'vrostbridge-headset-7', 'DISPLAY': ':1'})
            desktop = runtime_launch_environment({})
        self.assertEqual(game['WAYLAND_DISPLAY'], 'vrostbridge-headset-7')
        self.assertNotIn('GAMESCOPE_WAYLAND_DISPLAY', game)
        self.assertEqual(desktop['GAMESCOPE_WAYLAND_DISPLAY'], 'gamescope-0')

    def test_the_compositor_log_is_read_for_the_lease(self):
        found = compositor_findings(GAME_MODE_COMPOSITOR)
        self.assertTrue(found['lease_failed'])
        self.assertFalse(found['aborted'])
        self.assertIn('CHmdWindowSDL: No support for wl_drm_lease. This is needed for VR under Wayland.', found['notes'])
        self.assertIn('Failed to start compositor: VRInitError_Compositor_CannotDRMLeaseDisplay', found['notes'])
        good = compositor_findings(DESKTOP_COMPOSITOR)
        self.assertFalse(good['lease_failed'])
        self.assertEqual(good['notes'], ['CHmdWindowSDL: Using Wayland', 'WaylandHMDState: Got interface: wp_drm_lease_device_v1'])

    def test_which_display_steamvr_was_given(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / '40').mkdir()
            (proc / '40' / 'environ').write_bytes(b'HOME=/home/x\0WAYLAND_DISPLAY=gamescope-0\0GAMESCOPE_WAYLAND_DISPLAY=gamescope-0\0')
            identities = {40: {'pid': 40, 'path': '/s/bin/linux64/vrserver'}, 41: {'pid': 41, 'path': '/s/bin/linux64/vrmonitor'}}
            server = server_display_environment(identities, proc)
            self.assertEqual(server, {'readable': True, 'wayland_display': 'gamescope-0',
                                      'gamescope_wayland_display': 'gamescope-0', 'display': None, 'sdl_videodriver': None})
            self.assertIsNone(server_display_environment({41: identities[41]}, proc))
            self.assertFalse(server_display_environment({42: {'pid': 42, 'path': '/s/bin/linux64/vrserver'}}, proc)['readable'])
        wanted = {'WAYLAND_DISPLAY': 'vrostbridge-headset-7'}
        self.assertIn("started with WAYLAND_DISPLAY=gamescope-0, not VROSTBridge's headset display (vrostbridge-headset-7); "
                      "gamescope's display marker was still set.", server_display_note(server, wanted))
        self.assertIn("started with VROSTBridge's headset display",
                      server_display_note({'readable': True, 'wayland_display': 'vrostbridge-headset-7'}, wanted))
        message = lease_failure_message(wanted, server)
        self.assertIn('Game Mode (SteamVR error 496', message)
        self.assertIn('SteamVR was started with WAYLAND_DISPLAY=gamescope-0', message)
        self.assertIn('Play from Desktop Mode for now, and save a report', message)
        self.assertIn('did not use it', lease_failure_message(wanted, {'readable': True, 'wayland_display': 'vrostbridge-headset-7'}))
        self.assertIn('from the desktop', lease_failure_message({}))


class FastLeaseFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'steamvr/bin').mkdir(parents=True)
        (root / 'steamvr/bin/vrstartup.sh').write_text('#!/bin/sh\n')
        self.log = root / 'vrcompositor.txt'
        self.log.write_text('an earlier run\n')
        self.lines = []
        self.runtime = ClientRuntime(root / 'VROSTBridgeClient', self.temp.name, self.lines.append, compositor_logs=[self.log])
        for target, value in (('launcher.client_runtime.settings_path', root / 'steamvr.vrsettings'),
                              ('launcher.client_runtime.runtime_roots', [root / 'steamvr'])):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_game_mode_says_why_at_once(self):
        waiting = subprocess.CompletedProcess([], 2, 'RUNTIME_WAIT 108', '')
        self.runtime.helper = Mock(return_value=waiting)
        def start(*arguments, **options):
            with self.log.open('a') as handle:
                handle.write(GAME_MODE_COMPOSITOR)
            return Mock(wait=lambda: 0)
        environment = {'WAYLAND_DISPLAY': 'vrostbridge-headset-7', 'DISPLAY': ':1', 'SDL_VIDEODRIVER': 'x11',
                       'QT_QPA_PLATFORM': 'xcb'}
        given = {'readable': True, 'wayland_display': 'vrostbridge-headset-7', 'gamescope_wayland_display': None}
        started = time.monotonic()
        with patch('launcher.client_runtime.subprocess.Popen', side_effect=start) as launch, \
                patch('launcher.client_runtime.server_display_environment', return_value=given), \
                patch.dict(os.environ, {'GAMESCOPE_WAYLAND_DISPLAY': 'gamescope-0'}), \
                patch.object(self.runtime.cancelled, 'wait'):
            with self.assertRaises(ValueError) as caught:
                self.runtime.ensure(extra_env=environment)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(launch.call_args.args[0][0].endswith('vrstartup.sh'))
        self.assertNotIn('GAMESCOPE_WAYLAND_DISPLAY', launch.call_args.kwargs['env'])
        self.assertEqual(launch.call_args.kwargs['env']['WAYLAND_DISPLAY'], 'vrostbridge-headset-7')
        self.assertIn('SteamVR error 496', str(caught.exception))
        self.assertIn('did not use it', str(caught.exception))
        self.assertEqual(self.runtime.state, 'error')
        self.assertIn("Game Mode: SteamVR's server was started with VROSTBridge's headset display", '\n'.join(self.lines))
        self.assertIn('SteamVR compositor: CHmdWindowSDL: No support for wl_drm_lease. This is needed for VR under Wayland.',
                      self.lines)
        self.assertEqual(self.runtime.status()['steamvr_display'], given)

    def test_a_compositor_that_gets_the_display_carries_on(self):
        results = [subprocess.CompletedProcess([], 2, 'RUNTIME_WAIT 108', ''),
                   subprocess.CompletedProcess([], 0, HEADSET_OUTPUT, '')]
        self.runtime.helper = Mock(side_effect=results)
        def start(*arguments, **options):
            with self.log.open('a') as handle:
                handle.write(DESKTOP_COMPOSITOR)
            return Mock(wait=lambda: 0)
        with patch('launcher.client_runtime.subprocess.Popen', side_effect=start), \
                patch('launcher.client_runtime.shutil.which', return_value='/usr/bin/steam'), \
                patch.object(self.runtime.cancelled, 'wait'):
            self.runtime.ensure()
        self.assertEqual(self.runtime.state, 'ready')
        self.assertIsNone(self.runtime.server_display, 'only looked for in Game Mode')


class GameModeStartLogTests(unittest.TestCase):
    def test_the_last_game_mode_start_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [100.0]
            log = GameModeStartLog(Path(directory) / 'game-mode-last-start.json', clock=lambda: clock[0])
            log.line('before any Game Mode start')
            self.assertIsNone(log.load())
            log.begin('1.0.0.10')
            clock[0] = 101.5
            log.line('Starting local SteamVR for the bridge.')
            log.line('Game Mode: LEASE_CLIENT connected pid=40 program=vrcompositor')
            log.end('failed: SteamVR could not take the headset display in Game Mode')
            log.line('TIMING after the outcome')                         # not kept...
            log.line('Game Mode: Headset display adapter exited (0).')  # ...the adapter's own lines are
            record = log.load()
            self.assertEqual(record['outcome'], 'failed: SteamVR could not take the headset display in Game Mode')
            self.assertEqual([line for _, line in record['lines']],
                             ['Starting local SteamVR for the bridge.', 'Game Mode: LEASE_CLIENT connected pid=40 program=vrcompositor',
                              'Game Mode: Headset display adapter exited (0).'])
            self.assertEqual(record['lines'][0][0], 1.5)
            log.close()
            log.line('Game Mode: a later session')
            self.assertEqual(len(log.load()['lines']), 3, 'closed: nothing more is added')
            log.end('started')
            self.assertTrue(log.load()['outcome'].startswith('failed'))
            # A Start that was stopped before its outcome says so.
            log.begin('1.0.0.10')
            log.close()
            self.assertEqual(log.load()['outcome'], 'stopped while starting')

    def test_the_companion_keeps_it_and_reports_it(self):
        app = read('launcher/app.py')
        self.assertIn("self.game_mode_log=GameModeStartLog(self.log_path.parent/'game-mode-last-start.json') if game_mode else None", app)
        self.assertIn('if self.game_mode_log and in_game_mode():self.game_mode_log.begin(role_version(self.role))', app)
        self.assertEqual(app.count("if self.game_mode_log:self.game_mode_log.end('failed: '+str(error))"), 3)
        self.assertIn("if self.game_mode_log:self.game_mode_log.end('started')", app)
        self.assertIn('game_mode_last_start=self.game_mode_log.load() if self.game_mode_log else None', app)


# ------------------------------------------------------------------------------------------------ the bitrate
def second(p95, late=True, cadence=None, timeouts=[0]):
    """One TIMING line's worth for the governor: frame arrival spread p95 (ms), late frames or not."""
    timeouts[0] += 4 if late else 0
    return {'cadence': cadence or ('late_or_paired' if late else 'full'), 'packet_fps': '75',
            'net_lost_frames_total': '0', 'late_frame_timeouts': str(timeouts[0]), 'fused_skipped': '0',
            'decode_sync_complete_mean_ms': '4.5', 'decode_sync_complete_p95_ms': '4.8',
            'packet_interval_p95_ms': str(p95)}


class ArrivalCheckTests(unittest.TestCase):
    def governor(self):
        governor = BitrateGovernor()
        governor.reset(40, 40, 75.0)
        changes = []
        for _ in range(8):                      # warm-up
            governor.observe(second(17.8, late=False))
        for _ in range(24):                     # this bitrate's usual spread on Wi-Fi
            changes.append(governor.observe(second(17.8, late=False)))
        self.assertEqual([c for c in changes if c is not None], [])
        return governor

    def test_a_step_that_did_not_help_is_undone(self):
        governor = self.governor()
        changes = [governor.observe(second(21.0)) for _ in range(5)]
        self.assertEqual([c for c in changes if c is not None], [30])
        self.assertEqual(governor.reason, 'frames arriving unevenly')
        self.assertIsNotNone(governor.arrival_check)
        # The same spread at 30 Mbit/s: the network's timing, not the bitrate's.
        changes = [governor.observe(second(17.8 if i % 3 else 21.0, late=bool(i % 3 == 0))) for i in range(18)]
        self.assertEqual([c for c in changes if c is not None], [40])
        self.assertTrue(governor.arrival_bound)
        self.assertIn('frames arrived no more evenly at a lower bitrate', governor.reason)
        # From now on uneven arrival does not step it down again.
        changes = [governor.observe(second(25.0)) for _ in range(40)]
        self.assertEqual([c for c in changes if c is not None], [])
        self.assertEqual(governor.mbps, 40)
        self.assertTrue(governor.snapshot()['arrival_bound'])

    def test_a_step_that_helped_stays(self):
        governor = self.governor()
        [governor.observe(second(21.0)) for _ in range(5)]
        self.assertEqual(governor.mbps, 30)
        changes = [governor.observe(second(14.0, late=False)) for _ in range(18)]
        self.assertEqual([c for c in changes if c is not None], [])
        self.assertEqual(governor.mbps, 30)
        self.assertFalse(governor.arrival_bound)
        self.assertIsNone(governor.arrival_check)

    def test_the_rendering_pc_being_late_is_not_uneven_arrival(self):
        governor = self.governor()
        changes = [governor.observe(second(60.0, cadence='source_limited')) for _ in range(12)]
        self.assertEqual([c for c in changes if c is not None], [])
        self.assertEqual(governor.mbps, 40)

    def test_lost_frames_still_step_it_down(self):
        governor = self.governor()
        [governor.observe(second(21.0)) for _ in range(5)]
        [governor.observe(second(17.8 if i % 3 else 21.0, late=bool(i % 3 == 0))) for i in range(18)]
        self.assertTrue(governor.arrival_bound)
        lost = [0]
        changes = []
        for _ in range(40):
            timing = second(17.8, late=False)
            lost[0] += 1
            timing['net_lost_frames_total'] = str(lost[0])
            changes.append(governor.observe(timing))
        self.assertIn(30, [c for c in changes if c is not None])


# ------------------------------------------------------------------------------------------------ base stations
class FarBlueZ(FakeBlueZ):
    """A station that is far away: its connections are aborted `failures` times. As BlueZ does, the RSSI
    property is there only while a search runs (`rssi_property`); bluetoothctl prints each new signal
    strength during its search, in the newer form or the older one."""
    def __init__(self, failures, rssi_property=False, older_bluetoothctl=False, **options):
        super().__init__(**options)
        self.failures = failures
        self.rssi_property = rssi_property
        self.older_bluetoothctl = older_bluetoothctl

    def objects(self):
        data = json.loads(super().objects())
        device = data['data'][0].get(STATION, {}).get('org.bluez.Device1')
        if device is not None and self.rssi_property:
            device['RSSI'] = {'type': 'n', 'data': -87}
        return json.dumps(data)

    def __call__(self, arguments, timeout):
        if 'Connect' in arguments and self.failures > 0:
            self.calls.append(list(arguments))
            self.failures -= 1
            return done('', 1, 'Call failed: le-connection-abort-by-local')
        if arguments[:1] == ['bluetoothctl']:
            result = super().__call__(arguments, timeout)
            strength = 'RSSI: -91' if self.older_bluetoothctl else 'RSSI: 0xffffffa5 (-91)'
            return done(result.stdout + '\x1b[0;93m[CHG]\x1b[0m Device AA:BB:CC:DD:EE:01 ' + strength + '\n')
        return super().__call__(arguments, timeout)


class BaseStationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def stations(self, bluez):
        return BaseStations(Path(self.temp.name), log=lambda line: None, runner=bluez, busctl='busctl',
                            bluetoothctl='bluetoothctl', clock=lambda: 1790000000)

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_three_tries_each_after_a_fresh_search(self):
        self.assertEqual((base_stations.ATTEMPTS, base_stations.RETRY_S), (3, 2.0))
        bluez = FarBlueZ(2)
        outcome = self.stations(bluez).set_power('on')
        self.assertEqual(outcome['results'], [{'name': 'LHB-1A2B3C4D', 'result': 'sent', 'state': 'on'}])
        kinds = [('scan' if call[:1] == ['bluetoothctl'] else 'connect') for call in bluez.calls
                 if call[:1] == ['bluetoothctl'] or 'Connect' in call]
        self.assertEqual(kinds, ['connect', 'scan', 'connect', 'scan', 'connect'])
        self.assertIn(['bluetoothctl', '--timeout', '2', 'scan', 'on'], bluez.calls)

    @patch('launcher.base_stations.sys.platform', 'linux')
    def test_a_station_not_reached_is_reported_with_its_signal(self):
        # The signal comes from bluetoothctl's lines during the searches: BlueZ drops the RSSI property
        # when a search ends, so it is gone by the time the station is given up.
        for older in (False, True):
            bluez = FarBlueZ(5, older_bluetoothctl=older)
            outcome = self.stations(bluez).set_power('on')
            self.assertEqual(outcome['results'][0]['signal_dbm'], -91)
        # Without a line for it (no search heard it), the RSSI property is used when BlueZ still has one.
        stations = self.stations(FarBlueZ(5, rssi_property=True))
        stations._scan = lambda seconds=0: []
        self.assertEqual(stations.set_power('on')['results'][0]['signal_dbm'], -87)
        bluez = FarBlueZ(5)
        outcome = self.stations(bluez).set_power('on')
        self.assertEqual(sum(1 for call in bluez.calls if 'Connect' in call), 3)
        self.assertEqual(outcome['message'], '0 of 1 base stations turned on. Not reached: LHB-1A2B3C4D '
                                             '(Call failed: le-connection-abort-by-local; signal -91 dBm).')

    def test_the_windows_script_tries_three_times(self):
        script = read('launcher/BaseStations.ps1')
        self.assertIn("for ($attempt=1; $attempt -le 3 -and $row.result -ne 'sent'; $attempt++) {", script)
        self.assertIn("if ($attempt -gt 1) { Start-Sleep -Milliseconds 2000; $row.error='' }", script)


if __name__ == '__main__':
    unittest.main()
