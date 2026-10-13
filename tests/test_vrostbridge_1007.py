"""VROSTBridge 1.0.0.7: the headset client on Windows 10 and 11, beside the Bazzite one.

The companion runs the client role on Windows (no Game Mode, Steam shortcut or display lease there; the tray icon
starts and stops sessions and says how a tray-started one went; sign-in startup; its own log beside the host's),
the native client stops on its command pipe instead of a signal, gaze reaches it over loopback UDP, SteamVR's
programs are recognised by their Windows names and Windows' crash records go into reports. FFmpeg for Windows is
built from the shipped source by a script in the repository, and every program shares one version.
"""
from pathlib import Path
import re
import socket
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from launcher.windows_tray import ClientNotices  # noqa: E402
from test_session_lifecycle import SessionLifecycleTests  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_every_program_shares_one_version_from_1_0_0_7_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1008.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 7))
        import build_windows_client
        # The Windows client takes the same number (the maintainer's rule: all packages on one version).
        self.assertEqual(build_windows_client.client_version(), CLIENT_VERSION)
        self.assertEqual(build_windows_client.numeric_version('1.0.0.7'), '1.0.0.7')


class FFmpegForWindowsTests(unittest.TestCase):
    def test_the_build_script_matches_its_notes_and_stays_lgpl(self):
        script = read('scripts/Build-FFmpeg-Windows.sh')
        notes = read('third_party/ffmpeg/BUILD-Windows.txt')
        self.assertIn('735b0713aa8f7218b1f77bf0407d6db6c9c4dde77d75f2e9e2d19f9b5c46ef55', script)
        self.assertIn('735b0713aa8f7218b1f77bf0407d6db6c9c4dde77d75f2e9e2d19f9b5c46ef55', notes)
        configure = re.search(r'\./configure (.*?)\n(?!\s+--)', script, re.S).group(1)
        arguments = ' '.join(configure.replace('\\\n', ' ').split())
        for argument in arguments.split():
            if argument.startswith('--extra-'):continue
            self.assertIn(argument.split('=')[0], notes, argument)
        self.assertNotIn('--enable-gpl', script)
        self.assertNotIn('--enable-nonfree', script)
        # No folder of the build machine is recorded in the libraries' configure line.
        self.assertIn('--prefix=/ffmpeg ', script)
        self.assertIn("--extra-cflags='-Ivulkan-headers -O2'", script)
        for decoder in ('h264_d3d11va', 'hevc_d3d11va', 'av1_d3d11va'):
            self.assertIn(decoder, script)


class ClientNoticeTests(unittest.TestCase):
    def test_a_tray_started_session_says_when_the_picture_arrives_once(self):
        notices = ClientNotices()
        self.assertIsNone(notices.update({'running': True, 'state': 'streaming'}))  # not started from the tray
        notices.arm()
        self.assertIsNone(notices.update({'running': False, 'state': 'starting', 'busy': True}))
        self.assertIsNone(notices.update({'running': True, 'state': 'connecting'}))
        title, text, error = notices.update({'running': True, 'state': 'streaming',
                                             'session_link': {'host_name': 'OFFICE-PC'}})
        self.assertEqual((title, error), ('Streaming', False))
        self.assertIn('OFFICE-PC', text)
        self.assertIsNone(notices.update({'running': True, 'state': 'recovery'}))
        self.assertIsNone(notices.update({'running': True, 'state': 'streaming'}))  # once per session
        self.assertIsNone(notices.update({'running': False, 'state': 'stopped'}))
        self.assertIsNone(notices.update({'running': True, 'state': 'streaming'}))  # a later page Start is quiet

    def test_a_tray_started_session_that_fails_says_why(self):
        notices = ClientNotices()
        notices.arm()
        title, text, error = notices.update({'running': False, 'state': 'error', 'startup_error': 'SteamVR is not set up.'})
        self.assertEqual((title, text, error), ('Session did not start', 'SteamVR is not set up.', True))
        self.assertIsNone(notices.update({'running': False, 'state': 'error', 'startup_error': 'SteamVR is not set up.'}))
        notices.arm()
        notices.update({'running': True, 'state': 'streaming'})
        title, _, error = notices.update({'running': False, 'state': 'error', 'startup_error': 'Host ended the session.'})
        self.assertEqual((title, error), ('Session stopped', True))


class WindowsClientAppTests(SessionLifecycleTests):
    """The client role as it runs on Windows (sys.platform patched; the helpers are mocks as in the Linux tests)."""
    # launcher.app.on_windows() decides the client's Windows behaviour (sys.platform itself is left alone: patching it
    # would make the standard library take its Windows paths on this Linux test machine).
    def windows(self):
        return patch('launcher.app.on_windows', return_value=True)

    def windows_app(self):
        with self.windows(), patch('launcher.app.PerformanceMonitor'):
            return self.app()

    def test_windows_has_no_game_mode_and_its_own_programs_and_log(self):
        app = self.windows_app()
        self.assertIsNone(app.game_mode_setup)
        self.assertIsNone(app.steam_shortcut)
        self.assertIsNone(app.lease_runtime)
        self.assertEqual(app.windows_startup.role, 'client')
        self.assertEqual(app.windows_startup.executable.name, 'VROSTBridge Client.exe')
        self.assertEqual(app.log_path.name, 'client-latest.log')
        with self.windows():
            self.assertEqual(app.native_client_path().name, 'VROSTBridgeClient.exe')
            status = app.status()
        self.assertEqual(status['platform'], 'windows')
        self.assertIsNone(status['game_mode'])
        self.assertIsNone(status['steam_shortcut'])
        # The Bazzite client keeps its names.
        linux = self.app()
        self.assertEqual(linux.log_path.name, 'latest.log')
        self.assertEqual(linux.native_client_path().name, 'VROSTBridgeClient')
        self.assertEqual(linux.status()['platform'], 'linux')

    def test_game_mode_actions_are_refused_and_sign_in_startup_is_offered(self):
        app = self.windows_app()
        for action in ('steam-add', 'enable-game-mode'):
            with self.assertRaisesRegex(ValueError, 'Bazzite client'):
                app.action({'action': action})
        app.windows_startup = Mock()
        app.action({'action': 'windows-startup', 'enabled': True})
        app.windows_startup.set_enabled.assert_called_once_with(True)
        linux = self.app()
        with self.assertRaisesRegex(ValueError, 'Windows client'):
            linux.action({'action': 'windows-startup', 'enabled': True})

    def test_a_windows_session_sends_gaze_to_the_native_client_over_loopback_udp(self):
        app = self.windows_app()
        app.launch = Mock()
        app.eye = Mock()
        app.eye.owners = set()
        app.eye.manager.installed.return_value = True
        app.eye.manager.calibration.return_value = {'calibrated': True}
        app.settings['gaze'] = True
        with self.windows():
            app.action({'action': 'start'})
        host, port = app.gaze_socket
        self.assertEqual(host, '127.0.0.1')
        arguments = app.launch.call_args.args[0]
        self.assertIn('--gaze-udp', arguments)
        self.assertEqual(arguments[arguments.index('--gaze-udp') + 1], str(port))
        self.assertNotIn('--gaze-socket', arguments)
        app.eye.begin.assert_called_with('session', ('127.0.0.1', port))
        self.assertIsNone(app.runtime_directory)  # no socket folder on Windows

    def test_stopping_on_windows_asks_the_native_client_to_quit(self):
        app = self.windows_app()
        process = Mock()
        process.poll.return_value = None
        app.process = process
        with self.windows():
            app.stop()
        process.stdin.write.assert_called_with('quit\n')
        process.terminate.assert_not_called()
        # One that does not quit has its command pipe closed (which stops it too), then the usual last resort.
        app = self.windows_app()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('client', 8), 0]
        app.process = process
        with self.windows():
            app.stop()
        process.stdin.close.assert_called_once()
        process.kill.assert_not_called()

    def test_windows_programs_and_crash_records(self):
        app = self.windows_app()
        identities = {1: {'pid': 1, 'path': 'C:\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\vrserver.exe'},
                      2: {'pid': 2, 'path': 'C:\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\VRCompositor.exe'},
                      3: {'pid': 3, 'path': 'C:\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\vrmonitor.exe'}}
        # runtime_name() reads sys.platform itself: patched only around this pure call.
        with patch.object(app, 'local_runtime_processes', return_value=identities), patch('sys.platform', 'win32'):
            found = app.local_runtime_state()
        self.assertEqual(sorted(found), ['vrcompositor', 'vrserver'])
        lines = []
        app.log = lines.append
        with self.windows(), \
                patch('launcher.app.windows_crash_details', return_value=['vrcompositor.exe stopped working (0xc0000005)']):
            app.log_compositor_crash_details()
        self.assertEqual(lines, ['STEAMVR crash detail: vrcompositor.exe stopped working (0xc0000005)'])

    def test_the_windows_report_says_which_platform(self):
        app = self.windows_app()
        with self.windows(), patch('launcher.app.steamvr_logs', return_value={}):
            report = app.local_report()
        self.assertEqual(report['platform'], 'windows')
        self.assertIsNone(report['game_mode'])
        self.assertIsNone(report['steam_shortcut'])


class GazeOverUdpTests(unittest.TestCase):
    def test_the_relay_sends_only_to_loopback(self):
        from launcher import gaze_provider
        port = gaze_provider.free_udp_port()
        self.assertEqual(gaze_provider.gaze_target(('127.0.0.1', port)), ('127.0.0.1', port))
        for target in (('192.168.1.5', port), ('0.0.0.0', port), ('localhost', 0)):
            with self.assertRaises((ValueError, OSError)):
                gaze_provider.gaze_target(target)


class MissingSteamVRTests(unittest.TestCase):
    def test_a_pc_without_steamvr_is_told_plainly(self):
        from launcher import client_runtime
        with patch.object(client_runtime, 'openvr_registry', return_value=ROOT / 'no-such-openvrpaths.vrpath'):
            with self.assertRaisesRegex(ValueError, 'SteamVR is not set up on this PC yet'):
                client_runtime.settings_path()


class PagePathTests(unittest.TestCase):
    def test_long_paths_in_messages_wrap(self):
        self.assertIn('#hint,.small-note,.warning-note,.status-lines p{overflow-wrap:anywhere}', read('launcher/desktop.html'))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (VersionTests, FFmpegForWindowsTests, ClientNoticeTests, WindowsClientAppTests, GazeOverUdpTests,
                MissingSteamVRTests, PagePathTests):
        for name in sorted(cls.__dict__):
            if name.startswith('test_'):
                suite.addTest(cls(name))
    return suite


if __name__ == '__main__':
    unittest.main()
