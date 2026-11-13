"""VROSTBridge 1.0.0.15: SteamVR's dashboard volume, and what the 1.0.0.14 sessions and reports turned up."""
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from host import steamvr_driver  # noqa: E402
from host.control import HostControl, SteamShutdownObserver  # noqa: E402
from host.events import HostEvents  # noqa: E402
from launcher import headset_hardware  # noqa: E402
from launcher.diagnostics import Journal  # noqa: E402
from launcher.headset_audio import match_headset_devices  # noqa: E402
from launcher.network_settings import NetworkSettings  # noqa: E402
from launcher.power_balance import (LOWER_SHARE, SMOOTH_MIN_ROWS, lower_power, recent_power, smooth_session,  # noqa: E402
                                    valid_power_record)
from launcher.session_link import SessionClient, SessionError, SessionHost  # noqa: E402
from launcher.stream_budget import (MEMORY_ENTRIES, SETTLE_MIN_S, WARMUP_S, BitrateGovernor, memory_key,  # noqa: E402
                                    remember, valid_memory)
from launcher.wifi_others import OtherWifiPause  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('Timed out')
        time.sleep(.01)


class VersionTests(unittest.TestCase):
    def test_every_program_is_1_0_0_15(self):
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual((CLIENT_VERSION, HOST_VERSION), ('1.0.0.15', '1.0.0.15'))
        version = read('core/version.hpp')
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "1.0.0.15"', version)
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "1.0.0.15"', version)
        self.assertIn('project(VROSTBridge VERSION 1.0.0.15 ', read('CMakeLists.txt'))
        self.assertIn('set(VROSTBRIDGE_VERSION "1.0.0.15")', read('CMakeLists.txt'))
        self.assertIn('version=1.0.0.15\n', read('packaging/install-bazzite.sh'))
        nsi = read('packaging/windows-installer.nsi')
        for line in ('Name "VROSTBridge Host 1.0.0.15"', 'VIProductVersion "1.0.0.15"', '"DisplayVersion" "1.0.0.15"',
                     'InstallDir "$LOCALAPPDATA\\VROSTBridge\\Host\\1.0.0.15"'):
            self.assertIn(line, nsi)
        self.assertNotIn('1.0.0.14', nsi)
        resource = read('packaging/windows-icon.rc')
        self.assertIn('FILEVERSION 1,0,0,15', resource)
        self.assertIn('"ProductVersion", "1.0.0.15"', resource)
        self.assertIn('native launcher 1.0.0.15 ---', read('packaging/windows-launcher.c'))
        self.assertIn("VERSION = '1.0.0.15'", read('launcher/install_maintenance.py'))
        self.assertIn("'VROSTBridge 1.0.0.15 / ALVR'", read('host/profile.py'))
        self.assertIn("default='1.0.0.15'", read('packaging/collect_rust_notices.py'))
        sys.path.insert(0, str(ROOT / 'packaging'))
        import build_distributions
        import build_windows_client
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-1.0.0.15.zip')
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-1.0.0.15.exe')
        self.assertEqual(build_windows_client.setup_name(), 'VROSTBridge-Client-Setup-1.0.0.15.exe')
        self.assertEqual(build_windows_client.portable_name(), 'VROSTBridge-Client-Portable-1.0.0.15.zip')


class DashboardVolumeTests(unittest.TestCase):
    """SteamVR's dashboard slider sets the volume of the headset's audio device on the rendering PC (the server names
    the captured device as the headset's), and the WASAPI loopback is that device's mix before its volume: the slider
    changed nothing. The capture applies the device volume now (patch 0023; its Rust tests run with cargo test)."""

    def test_patch_0023_applies_the_device_volume_before_the_limit(self):
        patch_text = read('patches/0023-host-game-audio-capture.patch')
        for needle in ('pub fn endpoint_gain(level_db: f32, scalar: f32) -> f32',
                       'pub struct GainRamp', 'pub fn convert_to_stereo_i16_with_gain(',
                       'Endpoints::IAudioEndpointVolume', 'volume.GetMasterVolumeLevel()',
                       'volume.GetMasterVolumeLevelScalar()', 'ramp.next(),',
                       'VROST_AUDIO_CAPTURE volume_db={db:.1} gain={gain:.3}',
                       'fn the_device_volume_is_applied_before_the_limit_and_ramped()'):
            self.assertIn(needle, patch_text)
        # Mute stays the server's own: the device is muted while it streams so the PC's speakers stay quiet.
        self.assertNotIn('GetMute', patch_text)
        self.assertIn('for value in [left * g, right * g] {', patch_text)
        # The unchanged entry point is the same conversion at full volume.
        self.assertIn('convert_to_stereo_i16_with_gain(data, frames, stride, channels, format, weights, silent, [1.0, 1.0], out);',
                      patch_text)

    def test_the_server_names_the_captured_device_as_the_headsets_audio_output(self):
        # Upstream code, unchanged: the HMD's AudioDefaultPlaybackDeviceIdString is the captured device, so SteamVR's
        # slider acts on the very device the capture reads.
        patch_text = read('patches/0023-host-game-audio-capture.patch')
        self.assertIn('alvr_audio::LoopbackCapture::open(&device, game_audio_sample_rate)', patch_text)


class AudioStallTests(unittest.TestCase):
    """Wired 1.0.0.14 sessions ran the client's audio dry almost only in stalls of the whole stream (video stalled in
    the same seconds, 70-220 ms), and each raised the buffer target: one session ended with the sound 125 ms late."""

    def test_patch_0022_raises_the_target_only_for_gaps_one_step_would_bridge(self):
        patch_text = read('patches/0022-client-audio-playback.patch')
        self.assertIn('+const STALL_MARGIN_MS: f64 = 30.0;', patch_text)
        self.assertIn('+                    if gap.as_secs_f64() * 1000.0 <= self.target_ms + STALL_MARGIN_MS {', patch_text)
        self.assertIn('fn late_packets_raise_the_target_a_stall_and_a_pause_do_not()', patch_text)
        self.assertNotIn('fn a_stall_counts_as_an_underrun_and_raises_the_target_and_a_pause_does_not()', patch_text)


class PatchSetTests(unittest.TestCase):
    def test_a_second_build_in_the_same_checkout_goes_ahead(self):
        """Later patches change lines of earlier ones, so one patch alone no longer reverses cleanly; the applied set
        is recorded in the checkout's git folder instead."""
        source = read('scripts/fetch_upstream.py')
        self.assertIn("'vrostbridge-patches.json'", source)
        self.assertIn("'--absolute-git-dir'", source)
        self.assertIn('already applied', source)
        self.assertIn('carries another VROSTBridge patch set', source)
        sys.path.insert(0, str(ROOT / 'scripts'))
        import fetch_upstream
        names = [name for name, _ in fetch_upstream.patch_set()]
        self.assertEqual(len(names), 23)
        self.assertEqual(names[0], '0001-local-microphone-consent.patch')
        self.assertEqual(names[-1], '0023-host-game-audio-capture.patch')


VRSETTINGS = {
    'steamvr': {'activateMultipleDrivers': True, 'mirrorViewGeometry': '0 0 1080 600'},
    'driver_alvr_server': {'blocked_by_safe_mode': True, 'trackingFrameOffset': 0},
    'driver_lighthouse': {'enable': True},
}
SAFE_MODE_LINE = ('Fri Nov 13 2026 00:24:41.885 [Warning] - Not loading driver alvr_server because it was blocked by a '
                  'previous safe mode event\n')


class SafeModeTests(unittest.TestCase):
    """After a crash SteamVR started in safe mode and turned VROSTBridge's driver off; the headset PC then looked for a
    host that never appeared, with nothing to say why (a 1.0.0.14 report)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root / 'steamvr.vrsettings'
        self.settings.write_text(json.dumps(VRSETTINGS, indent=3), encoding='utf-8')

    def test_the_block_is_read_from_steamvrs_settings(self):
        block = steamvr_driver.driver_block(self.settings)
        self.assertEqual(block['reason'], 'safe-mode')
        self.assertIn('safe mode', block['message'])
        self.assertIn('Turn the driver back on', block['message'])
        value = dict(VRSETTINGS, driver_alvr_server={'enable': False})
        self.settings.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(steamvr_driver.driver_block(self.settings)['reason'], 'disabled')
        value = dict(VRSETTINGS, driver_alvr_server={'blocked_by_safe_mode': False})
        self.settings.write_text(json.dumps(value), encoding='utf-8')
        self.assertIsNone(steamvr_driver.driver_block(self.settings))
        self.settings.write_text('{ not json', encoding='utf-8')
        self.assertIsNone(steamvr_driver.driver_block(self.settings), 'an unreadable file never blocks a start')
        self.assertIsNone(steamvr_driver.driver_block(self.root / 'missing.vrsettings'))

    def test_the_settings_file_is_found_through_openvrs_registry(self):
        registry = self.root / 'openvrpaths.vrpath'
        registry.write_text(json.dumps({'config': [str(self.root)], 'runtime': ['x']}), encoding='utf-8')
        self.assertEqual(steamvr_driver.settings_path(registry), self.settings)
        registry.write_text('{}', encoding='utf-8')
        self.assertIsNone(steamvr_driver.settings_path(registry))

    def test_turning_it_back_on_changes_only_that_section_and_only_while_steamvr_is_closed(self):
        with self.assertRaisesRegex(ValueError, 'Close SteamVR'):
            steamvr_driver.clear_driver_block(self.settings, steamvr_running=True)
        self.assertEqual(json.loads(self.settings.read_text())['driver_alvr_server']['blocked_by_safe_mode'], True)
        result = steamvr_driver.clear_driver_block(self.settings)
        self.assertTrue(result['changed'])
        self.assertEqual(result['reason'], 'safe-mode')
        written = json.loads(self.settings.read_text(encoding='utf-8'))
        self.assertEqual(written['driver_alvr_server'], {'blocked_by_safe_mode': False, 'trackingFrameOffset': 0})
        self.assertEqual(written['steamvr'], VRSETTINGS['steamvr'])
        self.assertEqual(written['driver_lighthouse'], {'enable': True})
        self.assertIn('"steamvr" : {', self.settings.read_text(encoding='utf-8'), "SteamVR's own layout")
        self.assertEqual(list(self.root.glob('.steamvr.vrsettings.*')), [], 'no temporary file left behind')
        self.assertFalse(steamvr_driver.clear_driver_block(self.settings)['changed'])
        self.settings.write_text(json.dumps(dict(VRSETTINGS, driver_alvr_server={'enable': False})), encoding='utf-8')
        steamvr_driver.clear_driver_block(self.settings)
        self.assertEqual(json.loads(self.settings.read_text())['driver_alvr_server']['enable'], True)

    def test_steamvrs_server_log_line_is_recognised(self):
        self.assertEqual(steamvr_driver.log_block(SAFE_MODE_LINE), 'safe-mode')
        self.assertEqual(steamvr_driver.log_block('Fri Nov 13 2026 00:24:41.885 [Warning] - Not loading driver '
                                                  'alvr_server because it is disabled in settings'), 'disabled')
        self.assertIsNone(steamvr_driver.log_block('Not loading driver prism because it is disabled in settings'))

    def test_the_host_refuses_to_start_steamvr_with_the_reason(self):
        messages = []
        control = HostControl(self.root, messages.append, launch=Mock(), process_ids=lambda: set())
        control.driver_settings = lambda: self.settings
        with self.assertRaises(ValueError) as caught:
            control.start()
        self.assertEqual(str(caught.exception), steamvr_driver.SAFE_MODE_TEXT)
        self.assertFalse(control.active)
        control.launch.assert_not_called()
        self.assertTrue(any('safe mode' in line for line in messages))

    def test_the_server_log_ends_a_start_at_once(self):
        log = self.root / 'vrserver.txt'
        log.write_text('Thu Nov 12 2026 23:00:00.000 [Warning] - Not loading driver alvr_server because it was '
                       'blocked by a previous safe mode event\n', encoding='utf-8')
        clock = [datetime_stamp('Fri Nov 13 2026 00:24:40')]
        observer = SteamShutdownObserver(paths=lambda: [log], wall_clock=lambda: clock[0])
        observer.reset()
        observer.poll()
        self.assertIsNone(observer.blocked, 'a line from before this launch is history')
        with log.open('a', encoding='utf-8') as file:
            file.write(SAFE_MODE_LINE)
        clock[0] += 2
        observer.poll()
        self.assertEqual(observer.blocked, 'safe-mode')
        source = read('host/control.py')
        self.assertIn("blocked=getattr(self.shutdown_observer,'blocked',None)", source)
        self.assertIn('raise ValueError(steamvr_driver.REASONS[blocked])', source)


def datetime_stamp(text):
    import datetime
    return datetime.datetime.strptime(text, '%a %b %d %Y %H:%M:%S').timestamp()


class SafeModeSessionTests(unittest.TestCase):
    """The headset PC is usually in another room: it can ask the host to turn the driver back on."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = []
        self.unblock = Mock(return_value="VROSTBridge's driver is on again in SteamVR. Start the session again.")
        self.host = SessionHost(self.root / 'host', Mock(), Mock(), lambda: {'runtime_state': 'ready', 'available': True},
                                Mock(return_value=True), self.logs.append, port=0, address_provider=lambda: [],
                                test_peer=lambda peer: peer == '127.0.0.1', unblock=self.unblock)
        self.addCleanup(self.host.close)
        self.client = SessionClient(self.root / 'client', self.logs.append, port=self.host.port,
                                    address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(self.client.close)

    def credentials(self):
        value = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        self.host.dispatch('/pair', value, '127.0.0.1')
        return value

    def test_a_paired_headset_pc_turns_it_back_on_between_sessions(self):
        self.assertIn('driver-unblock', self.host.hello()['features'])
        self.client.start({'codec': 'H264', 'bitrate': 60}, host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        with self.assertRaisesRegex(SessionError, 'session is running'):
            self.client.unblock_driver()
        self.client.stop()
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        message = self.client.unblock_driver()
        self.assertIn('on again', message)
        self.unblock.assert_called_once_with()
        self.assertTrue(any('turn the driver back on' in line for line in self.logs))

    def test_strangers_and_old_hosts_cannot(self):
        stranger = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        with self.assertRaisesRegex(SessionError, 'not paired'):
            self.host.dispatch('/unblock', stranger, '127.0.0.1')
        owner = self.credentials()
        with self.assertRaisesRegex(SessionError, 'Unsupported'):
            self.host.dispatch('/unblock', dict(owner, extra=1), '127.0.0.1')
        self.unblock.side_effect = ValueError('Close SteamVR on the rendering PC first.')
        with self.assertRaisesRegex(SessionError, 'Close SteamVR'):
            self.host.dispatch('/unblock', owner, '127.0.0.1')
        client = SessionClient(self.root / 'other', self.logs.append, port=self.host.port,
                               address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(client.close)
        with self.assertRaisesRegex(SessionError, 'start a session once'):
            client.unblock_driver()
        client.host_ip, client.host_id, client.host_features = '127.0.0.1', self.host.host_id, ['link-test']
        with self.assertRaisesRegex(SessionError, 'Install this release on the host'):
            client.unblock_driver()

    def test_the_pages_and_game_mode_offer_it(self):
        app = read('launcher/app.py')
        self.assertIn("if action=='unblock-driver':", app)
        self.assertIn("if action=='host-unblock-driver' and self.role=='client':", app)
        self.assertIn("extra['steamvr_driver']=self.steamvr_driver_status()", app)
        self.assertIn("info['steamvr_driver']={'state':driver['state'],'can_fix':bool(driver.get('can_fix'))}", app)
        self.assertIn('unblock=self.remote_unblock_driver', app)
        # The host's reason stays on the headset PC's pages and card after the session stops.
        self.assertIn("if link['state']=='error' and link.get('message') and not self.startup_error:", app)
        page = read('launcher/desktop.html')
        self.assertIn('id="unblock-driver"', page)
        self.assertIn("act(lastStatus?.role==='host'?'unblock-driver':'host-unblock-driver')", page)
        self.assertIn("['safe-mode','disabled'].includes(driver?.state)", page)
        game = read('launcher/game_ui.py')
        self.assertIn("self.button('host-unblock-driver', 'Turn the driver back on'", game)
        self.assertIn("self.action('host-unblock-driver',", game)


def sysfs_device(root, name, device_class, vendor='05e3', product='0610'):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / 'bDeviceClass').write_text(device_class + '\n')
    (folder / 'idVendor').write_text(vendor + '\n')
    (folder / 'idProduct').write_text(product + '\n')
    return folder


class UsbDepthTests(unittest.TestCase):
    """A dock with two hubs of its own and a headset with three made the headset's innermost hub the sixth: Linux
    refused it ('hub nested too deep') and the controllers' radios never appeared."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'usb3').mkdir()
        (self.root / '3-0:1.0').mkdir()
        sysfs_device(self.root, '3-1', '09')
        sysfs_device(self.root, '3-1.4', '09')
        sysfs_device(self.root, '3-1.4.2', '00', '0bda', '8153')       # the dock's Ethernet
        sysfs_device(self.root, '3-1.4.1', '09')                        # the headset's link box
        sysfs_device(self.root, '3-1.4.1.1', '09')                      # its top hub
        sysfs_device(self.root, '3-1.4.1.1.3', '00', '35bd', '0101')    # the headset itself, five levels down

    def test_a_tree_within_the_limit_is_fine(self):
        self.assertEqual(headset_hardware.deep_hubs(self.root), [])
        self.assertEqual(headset_hardware.usb_depth(self.root)['state'], 'ok')

    def test_a_hub_six_levels_down_is_named(self):
        sysfs_device(self.root, '3-1.4.1.1.2', '09')        # its inner hub: level 5, still allowed
        sysfs_device(self.root, '3-1.4.1.1.2.1', '09')      # one more: level 6, refused
        sysfs_device(self.root, '3-1.4.1.1.2.4', '00', '28de', '2102')  # (a device can sit at level 6)
        (self.root / '3-1.4.1.1.2.1:1.0').mkdir()
        hubs = headset_hardware.deep_hubs(self.root)
        self.assertEqual(hubs, [{'path': '3-1.4.1.1.2.1', 'level': 6, 'id': '05e3:0610'}])
        depth = headset_hardware.usb_depth(self.root)
        self.assertEqual(depth['state'], 'deep')
        self.assertIn('nested too deep', depth['message'])
        self.assertIn('a port on the PC itself', depth['message'])
        with patch('launcher.headset_hardware.sys.platform', 'win32'):
            self.assertEqual(headset_hardware.usb_depth()['state'], 'ok', 'Windows has no such tree to read')

    def test_the_client_checks_it_at_start_and_shows_it(self):
        app = read('launcher/app.py')
        self.assertIn("if hardware.get('state')=='deep':", app)
        self.assertIn("extra['headset_hardware']=self.headset_hardware_status()", app)
        self.assertIn("headset_hardware=self.headset_hardware_status(fresh=True)", app)
        self.assertIn('id="headset-hardware"', read('launcher/desktop.html'))
        self.assertIn("hardware.get('state') in ('drop', 'deep')", read('launcher/game_ui.py'))


# The native client's lines when the headset's USB dropped out (a 1.0.0.14 report, in order).
DROP_LINES = ['CONTROLLER_STATE hand=left device=1 connected=0 tracked=0 tracking_result=1',
              'CONTROLLER_STATE hand=left device=4294967295 connected=0 tracked=0 tracking_result=1',
              'CONTROLLER_STATE hand=right device=4294967295 connected=0 tracked=0 tracking_result=1',
              'HEAD_TRACKING tracked=0 connected=0 result=101 reason=headset-not-connected']
START_LINES = ['HEAD_TRACKING tracked=1 connected=1 result=200 reason=tracked',
               'CONTROLLER_STATE hand=left device=1 connected=1 tracked=1 tracking_result=200',
               'CONTROLLER_STATE hand=right device=2 connected=1 tracked=1 tracking_result=200']


class HeadsetWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.watch = headset_hardware.HeadsetWatch(clock=lambda: self.now, wall=lambda: 1_800_000_000.0)

    def feed(self, lines, step=0.05):
        events = []
        for line in lines:
            self.now += step
            event = self.watch.observe(line)
            if event:
                events.append(event)
        return events

    def test_the_headset_and_its_controllers_dropping_together_is_the_usb(self):
        self.feed(START_LINES)
        self.now += 300
        self.assertEqual(self.feed(DROP_LINES), ['drop'])
        status = self.watch.status()
        self.assertEqual(status['state'], 'drop')
        self.assertIn("USB dropped out", status['message'])
        self.assertIn('5 V', status['message'])
        self.assertIn('2 controllers disconnected together', self.watch.log_text('drop'))
        # SteamVR then refuses the next frame: the card says what happened instead of its error number.
        self.now += 0.5
        self.assertEqual(self.watch.fatal_message('SteamVR texture submission failed: 105'), headset_hardware.DROP_TEXT)
        self.now += self.watch.FATAL_AFTER_S + 1
        self.assertEqual(self.watch.fatal_message('SteamVR texture submission failed: 105'),
                         'SteamVR texture submission failed: 105')
        self.assertEqual(self.watch.status()['state'], 'drop', 'it stays on the pages for a while')
        self.now += self.watch.NOTICE_S
        self.assertEqual(self.watch.status()['state'], 'ok')

    def test_the_other_order_and_a_controller_alone(self):
        self.feed(START_LINES)
        self.now += 60
        self.assertEqual(self.feed([DROP_LINES[3], DROP_LINES[0]]), ['drop'], 'the headset first, then a controller')
        watch = headset_hardware.HeadsetWatch(clock=lambda: self.now)
        for line in START_LINES:
            watch.observe(line)
        self.now += 60
        self.assertIsNone(watch.observe(DROP_LINES[0]), 'a controller switched off is not the USB')
        self.now += 5
        self.assertIsNone(watch.observe(DROP_LINES[3]), 'the headset five seconds later is not together')
        self.now += 1
        self.assertEqual(watch.fatal_message('SteamVR texture submission failed: 105'), headset_hardware.HEADSET_LOST_TEXT)

    def test_controllers_on_but_untracked_while_the_headset_tracks(self):
        self.feed(START_LINES)
        self.now += 20
        self.assertEqual(self.feed(['CONTROLLER_STATE hand=left device=1 connected=1 tracked=0 tracking_result=101']), [])
        self.now += self.watch.UNTRACKED_S - 1
        self.assertEqual(self.feed(['INPUT ' + json.dumps({'left': {'connected': True, 'tracked': False},
                                                             'right': {'connected': True, 'tracked': True}})]), [])
        self.now += 2
        self.assertEqual(self.feed(['INPUT ' + json.dumps({'left': {'connected': True, 'tracked': False}})]), ['untracked'])
        status = self.watch.status()
        self.assertEqual((status['state'], status['hands']), ('untracked', ['left']))
        self.assertIn("can't see the left controller", status['message'])
        self.assertIn('mist up', status['message'])
        self.assertIn('base stations', status['message'])
        self.assertTrue(self.watch.short().startswith('Left controller not tracked'))
        self.now += self.watch.HUD_S + 1
        self.assertEqual(self.watch.short(), '', 'the line in the headset goes after a while')
        self.feed(['CONTROLLER_STATE hand=left device=1 connected=1 tracked=1 tracking_result=200'])
        self.assertEqual(self.watch.status()['state'], 'ok')

    def test_both_untracked_and_a_headset_that_is_not_tracked_either(self):
        self.feed(START_LINES)
        self.now += 30
        self.feed(['CONTROLLER_STATE hand=left device=1 connected=1 tracked=0 tracking_result=101',
                   'CONTROLLER_STATE hand=right device=2 connected=1 tracked=0 tracking_result=101'])
        self.now += self.watch.UNTRACKED_S + 1
        self.feed(['INPUT {}'])
        self.assertIn("can't see the controllers: they're on", self.watch.status()['message'])
        watch = headset_hardware.HeadsetWatch(clock=lambda: self.now)
        for line in ('HEAD_TRACKING tracked=0 connected=1 result=101 reason=not-tracking',
                     'CONTROLLER_STATE hand=left device=1 connected=1 tracked=0 tracking_result=101'):
            watch.observe(line)
        self.now += 60
        self.assertIsNone(watch.observe('INPUT {}'), 'nothing tracks: the base stations, which the headset card says')

    def test_the_summary_puts_a_drop_first(self):
        deep = {'state': 'deep', 'message': headset_hardware.DEEP_TEXT, 'hubs': [{'path': '3-1.2.3.4.5.6'}]}
        self.assertEqual(headset_hardware.summary(self.watch, deep)['state'], 'deep')
        self.feed(START_LINES)
        self.feed(DROP_LINES)
        self.assertEqual(headset_hardware.summary(self.watch, deep)['state'], 'drop')
        self.assertEqual(headset_hardware.summary(None, {'state': 'ok'})['state'], 'ok')

    def test_the_companion_uses_it(self):
        app = read('launcher/app.py')
        self.assertIn("if watch is not None:message=watch.fatal_message(message)", app)
        self.assertIn("line.startswith(('HEAD_TRACKING ','CONTROLLER_STATE ','INPUT '))", app)
        self.assertIn("if hands:notice=hands", app)
        self.assertIn('if getattr(self,\'headset_watch\',None):self.headset_watch.reset()', app)


def pactl_sink(name, description, properties):
    return {'index': 7, 'state': 'SUSPENDED', 'name': name, 'description': description, 'driver': 'PipeWire',
            'properties': properties, 'ports': [], 'active_port': None, 'monitor_source': name + '.monitor'}


class BeyondSpeakerTests(unittest.TestCase):
    def test_speakers_on_the_beyonds_accessory_port_are_its_sound_output(self):
        beyondex = pactl_sink('alsa_output.usb-Generic_Beyondex_HD_Audio-00.analog-stereo', 'Beyondex HD Audio Analog Stereo',
                              {'device.product.name': 'Beyondex HD Audio', 'device.bus': 'usb'})
        laptop = pactl_sink('alsa_output.pci-0000_c4_00.6.analog-stereo', 'HD Audio Controller Speaker',
                            {'device.bus': 'pci'})
        sink, _, _ = match_headset_devices([laptop, beyondex], [], 'Bigscreen Beyond 2e', {})
        self.assertEqual(sink, beyondex['name'])
        # Not for an Index, and a word that only starts like it is not it.
        self.assertIsNone(match_headset_devices([laptop, beyondex], [], 'Valve Index', {})[0])
        lookalike = pactl_sink('alsa_output.usb-Beyondexx', 'Beyondexx', {'device.bus': 'usb'})
        self.assertIsNone(match_headset_devices([laptop, lookalike], [], 'Bigscreen Beyond 2e', {})[0])


def timings(**values):
    base = {'cadence': 'full', 'packet_fps': '75', 'refresh_hz': '75', 'net_lost_frames_total': '0',
            'late_frame_timeouts': '0', 'decode_sync_complete_mean_ms': '4.0', 'decode_sync_complete_p95_ms': '5.0',
            'packet_interval_p95_ms': '14.0'}
    base.update({key: str(value) for key, value in values.items()})
    return base


class BitrateMemoryTests(unittest.TestCase):
    """Every 1.0.0.14 session started at the ceiling and spent its first minute stepping down to where the session
    before had settled (89 to 45 Mbit/s in three steps)."""

    def test_a_session_starts_where_the_last_one_settled(self):
        governor = BitrateGovernor()
        governor.reset(89, 89, 75.0, remembered={'mbps': 50, 'decode_bound': True})
        self.assertEqual((governor.mbps, governor.start), (50, 50))
        self.assertTrue(governor.decode_bound, 'decoding time no longer lowers it')
        self.assertEqual(governor.snapshot()['remembered'], {'mbps': 50, 'decode_bound': True})
        # Never above the session's own start, never below its floor.
        governor.reset(40, 89, 75.0, remembered={'mbps': 60})
        self.assertEqual(governor.mbps, 40)
        governor.reset(89, 89, 75.0, remembered={'mbps': 20})
        self.assertEqual(governor.mbps, governor.floor)
        governor.reset(89, 89, 75.0, remembered={'mbps': '50'})
        self.assertEqual(governor.mbps, 89, 'a damaged record is ignored')
        governor.reset(None, 89, 75.0, remembered={'mbps': 50})
        self.assertFalse(governor.active)

    def test_the_settled_rate_is_where_the_session_spent_longest(self):
        governor = BitrateGovernor()
        governor.reset(89, 89, 75.0)
        for _ in range(WARMUP_S):
            governor.observe(timings())
        self.assertIsNone(governor.settled())
        governor.mbps = 55
        for _ in range(100):
            governor.observe(timings())
        governor.mbps = 45
        for _ in range(SETTLE_MIN_S - 100 - 1):
            governor.observe(timings())
        self.assertIsNone(governor.settled(), 'too short to count')
        governor.observe(timings())
        self.assertEqual(governor.settled(), {'mbps': 55, 'decode_bound': False, 'seconds': SETTLE_MIN_S})

    def test_the_memory_keeps_the_newest_streams(self):
        key = memory_key('Hevc', 5632, 1408, 75.0, 'wired')
        self.assertEqual(key, 'Hevc 5632x1408 75 wired')
        self.assertEqual(memory_key('Hevc', 5632, 1408, 75.0, 'cable'), 'Hevc 5632x1408 75 unknown')
        self.assertIsNone(memory_key('Hevc', None, 1408, 75.0, 'wired'))
        memory = {}
        for index in range(MEMORY_ENTRIES + 2):
            memory = remember(memory, 'stream %d' % index, {'mbps': 40 + index, 'decode_bound': index % 2 == 0},
                              1000.0 + index)
        self.assertEqual(len(memory), MEMORY_ENTRIES)
        self.assertNotIn('stream 0', memory)
        self.assertEqual(memory['stream 9'], {'mbps': 49, 'decode_bound': False, 'utc': 1009.0})
        self.assertEqual(remember(memory, None, {'mbps': 50}, 0), memory)
        self.assertEqual(valid_memory({'a': {'mbps': 5000}, 'b': {'mbps': 50.5}, 'c': 'x', 7: {'mbps': 50},
                                       'd': {'mbps': 50, 'decode_bound': 'yes'}}),
                         {'d': {'mbps': 50, 'decode_bound': False, 'utc': 0.0}})
        self.assertEqual(valid_memory(None), {})

    def test_the_companion_remembers_it_at_stop_and_uses_it_at_start(self):
        app = read('launcher/app.py')
        self.assertIn("remembered=(self.settings.get('bitrate_memory') or {}).get(self.bitrate_memory_key)", app)
        self.assertIn('remembered=remembered)', app)
        self.assertIn('try:self.remember_bitrate()', app)
        self.assertIn("elif key=='bitrate_memory':self.settings[key]=valid_bitrate_memory(saved.get(key))", app)


CABLE = {'interface': 'enp1', 'address': '192.168.1.116', 'prefix': 24, 'physical': True}
WIFI = {'interface': 'wlp1', 'address': '192.168.1.117', 'prefix': 24, 'physical': True}


class NetworkCheckTests(unittest.TestCase):
    """A check made while a session had Wi-Fi paused found one adapter and said all was well, and kept saying so."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name) / 'net'
        for folder in ('core', 'ipv4/conf/all'):
            (self.proc / folder).mkdir(parents=True)
        (self.proc / 'core/rmem_max').write_text('4194304\n')
        (self.proc / 'core/rmem_default').write_text('212992\n')
        (self.proc / 'ipv4/conf/all/arp_ignore').write_text('0\n')
        (self.proc / 'ipv4/conf/all/arp_announce').write_text('0\n')
        self.records, self.paused, self.now = [CABLE], ['wlp1'], 0.0

    def settings(self):
        return NetworkSettings(runner=Mock(), log=lambda *_: None, proc=self.proc, drop_in=Path(self.temp.name) / 'x',
                               platform='linux', addresses=lambda: self.records, paused=lambda: self.paused,
                               clock=lambda: self.now)

    def test_a_paused_adapter_still_shares_the_network(self):
        settings = self.settings()
        status = settings.check('enp1')
        self.assertEqual((status['state'], status['needs']), ('needed', ['arp']))
        self.assertEqual(status['shared'], ['enp1', 'wlp1 (paused)'])
        self.paused = []
        self.assertEqual(settings.check('enp1')['state'], 'ok', 'neither paused nor connected: nothing shares it')

    def test_an_old_check_is_made_again(self):
        settings = self.settings()
        self.paused = []
        self.assertEqual(settings.status()['state'], 'ok')
        self.records = [CABLE, WIFI]  # Wi-Fi came back after the session
        self.now += 30
        self.assertEqual(settings.status()['state'], 'ok', 'within a minute the last check stands')
        self.now += settings.STALE_S
        self.assertEqual(settings.status()['state'], 'needed')
        app = read('launcher/app.py')
        self.assertIn("paused=lambda:(self.other_wifi.status().get('paused') or [])", app)
        self.assertIn('recheck=threading.Timer(self.NETWORK_RECHECK_S', app)


class UnpluggedWifiTests(unittest.TestCase):
    def test_an_adapter_that_is_gone_is_forgotten_not_asked_for_again(self):
        with tempfile.TemporaryDirectory() as temp:
            net = Path(temp) / 'net'
            (net / 'wlp1').mkdir(parents=True)
            logs = []
            runner = Mock(return_value=Mock(returncode=10, stdout='', stderr='Error: Device not found'))
            with patch('launcher.wifi_others.sys.platform', 'linux'):
                pause = OtherWifiPause(temp, runner=runner, log=logs.append, sys_class_net=net)
                pause.nmcli = '/usr/bin/nmcli'
                pause.paused = ['wlx00c0ca000001', 'wlp1']
                pause.end()
            self.assertEqual(pause.unrestored, ['wlp1'], 'still here: asked for again')
            self.assertTrue(any('wlx00c0ca000001 is no longer on this PC (unplugged)' in line for line in logs))
            self.assertIn('Reconnect wlp1 from the network menu.', pause.status()['message'])
            self.assertNotIn('wlx00c0ca000001', pause.status()['message'])


def clock_rows(power, count=6):
    return [{'cpu_mhz': 3000.0, 'power_w': power} for _ in range(count)]


class LowerPowerTests(unittest.TestCase):
    def test_much_less_power_than_a_smooth_session_while_short_of_frames(self):
        record = {'power_w': 21.0, 'utc': 1.0}
        lower = lower_power(recent_power(clock_rows(12.0)), record, short_of_frames=True)
        self.assertEqual((lower['state'], lower['power_w'], lower['good_power_w']), ('lower', 12, 21))
        self.assertIn('about 12 W while streaming, against 21 W in its last smooth session', lower['message'])
        self.assertIn('TDP', lower['message'])
        self.assertEqual(lower['short'], '12 W, was 21 W · raise the TDP limit')
        self.assertIsNone(lower_power(12.0, record, short_of_frames=False), 'a stream that keeps up needs nothing')
        self.assertIsNone(lower_power(LOWER_SHARE * 21.0 + .1, record, True))
        self.assertIsNone(lower_power(12.0, None, True), 'nothing to compare with yet')
        self.assertIsNone(recent_power(clock_rows(12.0, 3)), 'too few readings')
        self.assertIsNone(recent_power([{'cpu_mhz': 1.0}] * 6))

    def test_a_smooth_session_is_remembered(self):
        rows = [{'fps_avg': 74.6, 'power_w': 20.0 + (index % 3)} for index in range(SMOOTH_MIN_ROWS)]
        self.assertEqual(smooth_session(rows, 75.0), (True, 21.0))
        self.assertEqual(smooth_session(rows[:-1], 75.0)[0], False, 'too short')
        rough = [dict(row, fps_avg=60.0) for row in rows]
        self.assertFalse(smooth_session(rough, 75.0)[0])
        self.assertFalse(smooth_session(rows, None)[0])
        self.assertEqual(valid_power_record({'power_w': 21.04, 'utc': 5}), {'power_w': 21.0, 'utc': 5.0})
        self.assertIsNone(valid_power_record({'power_w': -1}))
        app = read('launcher/app.py')
        self.assertIn('try:self.remember_smooth_power()', app)
        self.assertIn('self.check_lower_power()', app)
        self.assertIn("if result.get('state')=='ok' and lower:result=dict(lower)", app)
        self.assertIn("power.get('state') == 'lower'", read('launcher/game_ui.py'))


class CaptureStallTests(unittest.TestCase):
    """Each capture skip in the 1.0.0.14 reports (acquire_failed 0x102) came in the same second as the encoder's send
    stalling for half a second or more."""

    def native(self, events, content):
        events.accept(json.dumps({'event_type': {'id': 'Log', 'data': {'content': content, 'severity': 'Info'}}}))

    def test_a_skip_is_kept_with_the_send_time_beside_it(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(HostEvents, 'run'):
            events = HostEvents(lambda: None, Journal(temp, 'host'))
            self.addCleanup(events.close)
            capture = 'VROST_HOST_CAPTURE frames=%d acquire_failures=%d release_failures=0 encoder_wait_max_us=5 ' \
                      'acquire_max_us=10050 compose_max_us=600 mutex_held_max_us=700'
            with patch('host.events.time.monotonic', return_value=100):
                self.native(events, capture % (100, 0))
                self.native(events, 'VROST_HOST_ENCODER frames=100 failures=0 transmit_max_us=688164')
            with patch('host.events.time.monotonic', return_value=101):
                self.native(events, capture % (170, 1))
            with patch('host.events.time.monotonic', return_value=110):
                self.native(events, capture % (900, 1))
                self.native(events, capture % (975, 3))
            with patch('host.events.time.monotonic', return_value=110.5):
                self.native(events, 'VROST_HOST_ENCODER frames=975 failures=0 transmit_max_us=4000')
            stalls = events.status()['capture_stalls']
            self.assertEqual([(row['skipped'], row['cause'], row['transmit_max_us']) for row in stalls],
                             [(1, 'network-send', 688164), (2, 'unknown', 4000)])
            self.assertNotIn('_at', json.dumps(stalls))


if __name__ == '__main__':
    unittest.main()
