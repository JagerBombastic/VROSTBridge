"""RC4.6 (client): Baballonia's calibration reaches the headset.

On Linux Baballonia shows its calibration through OpenXR when SteamVR runs on this PC (it checks for vrserver
when a calibration starts) and on this screen otherwise. VROSTBridge points Baballonia at SteamVR's OpenXR
runtime (XR_RUNTIME_JSON) and says when a calibration would not reach the headset."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import baballonia, eye_tracking  # noqa: E402
from launcher.baballonia import BaballoniaManager, steamvr_openxr_runtime, xr_runtime_running  # noqa: E402
from launcher.eye_tracking import EyeTracking  # noqa: E402
import test_rc45  # noqa: E402  (its fakes are reused; its test classes are not imported by name)


class Folder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def steamvr(self, manifest=True, extra_first=False):
        """An OpenVR registry naming a SteamVR installation (with its OpenXR manifest or not)."""
        steamvr = self.root / 'Steam/steamapps/common/SteamVR'
        steamvr.mkdir(parents=True, exist_ok=True)
        if manifest:
            (steamvr / 'steamxr_linux64.json').write_text('{"runtime": {"library_path": "bin/linux64/vrclient.so"}}')
        other = self.root / 'other-runtime'
        other.mkdir(exist_ok=True)
        registry = self.root / 'config/openvr/openvrpaths.vrpath'
        registry.parent.mkdir(parents=True, exist_ok=True)
        roots = [str(other), str(steamvr)] if extra_first else [str(steamvr), str(other)]
        registry.write_text(json.dumps({'runtime': roots, 'version': 1}))
        return steamvr / 'steamxr_linux64.json'


class OpenXRRuntimeTests(Folder):
    def test_steamvrs_manifest_comes_from_the_runtime_openvr_uses(self):
        manifest = self.steamvr()
        with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'config')}):
            self.assertEqual(steamvr_openxr_runtime(), manifest)
        self.assertEqual(steamvr_openxr_runtime([manifest.parent]), manifest)
        self.assertIsNone(steamvr_openxr_runtime([self.root / 'other-runtime', manifest.parent]),
                          'only the runtime OpenVR uses, as for the native client')
        self.assertIsNone(steamvr_openxr_runtime([]))
        with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'nothing')}):
            self.assertIsNone(steamvr_openxr_runtime())

    def manager(self, **options):
        logs = []
        manager = BaballoniaManager(log=logs.append, root=self.root / 'data/baballonia', config=self.root / 'config',
                                    state=self.root / 'state/setup.json', curl=None, vulkan_layer=None, **options)
        manager.install_dir.mkdir(parents=True)
        manager.executable.write_text('#!/bin/sh\nsleep 30\n')
        manager.executable.chmod(0o755)
        (manager.install_dir / 'vrostbridge-receipt.json').write_text(json.dumps({'sha256': manager.release['sha256']}))
        manager.logs = logs
        return manager

    def systemd_start(self, manifest, openxr=True):
        tools = self.root / 'tools'
        tools.mkdir(exist_ok=True)
        for name in ('systemd-run', 'systemctl'):
            (tools / name).write_text('#!/bin/sh\n')
            (tools / name).chmod(0o755)
        calls = []

        def runner(command, **options):
            calls.append(command)
            return subprocess.CompletedProcess(command, 3 if 'is-active' in command else 0, '', '')
        manager = self.manager(systemd_run=str(tools / 'systemd-run'), systemctl=str(tools / 'systemctl'), runner=runner)
        environment = {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'), 'PATH': '/usr/bin',
                       'DISPLAY': ':0'}
        if openxr:
            environment['VROSTBRIDGE_CALIBRATION'] = 'openxr'
        with patch.dict('os.environ', environment, clear=True):
            self.assertEqual(manager.start(), 'started')
            status = manager.status(running=True)
        command = next(c for c in calls if c[0].endswith('systemd-run'))
        return manager, [item for item in command if item.startswith('--setenv=')], status

    def test_the_openxr_opt_out_is_pointed_at_steamvrs_openxr_runtime(self):
        manifest = self.steamvr()
        manager, settings, status = self.systemd_start(manifest)
        self.assertIn('--setenv=XR_RUNTIME_JSON=' + str(manifest), settings)
        self.assertIn('--setenv=VROSTBRIDGE_CALIBRATION=openxr', settings)
        self.assertIn("its OpenXR calibration uses SteamVR's OpenXR runtime", manager.logs[-1])
        self.assertIs(status['steamvr_openxr'], True)

    def test_by_default_no_openxr_runtime_is_forced(self):
        """Review finding: the overlay calibration never uses OpenXR, and with Monado or WiVRn (no SteamVR)
        Baballonia's OpenXR calibration must keep this PC's own runtime."""
        manifest = self.steamvr()
        manager, settings, status = self.systemd_start(manifest, openxr=False)
        self.assertFalse([item for item in settings if 'XR_RUNTIME_JSON' in item or 'VROSTBRIDGE_CALIBRATION' in item],
                         settings)
        self.assertNotIn('OpenXR', manager.logs[-1])
        self.assertIsNone(status['steamvr_openxr'], 'so the page never warns about a file the overlay does not use')

    def test_without_steamvrs_manifest_the_pcs_own_setting_stays_and_the_page_is_told(self):
        manifest = self.steamvr(manifest=False)
        manager, settings, status = self.systemd_start(manifest)
        self.assertFalse([item for item in settings if 'XR_RUNTIME_JSON' in item], settings)
        self.assertIn('steamxr_linux64.json) was not found', manager.logs[-1])
        self.assertIs(status['steamvr_openxr'], False)

    def test_started_directly_it_gets_the_same(self):
        manifest = self.steamvr()
        seen = {}

        def popen(command, **options):
            seen.update(options['env'])
            return Mock(pid=4321, poll=Mock(return_value=None))
        manager = self.manager(popen=popen, systemd_run=None, systemctl=None)
        with patch.dict('os.environ', {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                       'XR_RUNTIME_JSON': '/elsewhere/runtime.json', 'VROSTBRIDGE_CALIBRATION': 'openxr'},
                        clear=True):
            self.assertEqual(manager.start(), 'started')
        self.assertEqual(seen['XR_RUNTIME_JSON'], str(manifest))
        self.assertIn("SteamVR's OpenXR runtime", manager.logs[-1])

    def test_the_pages_are_not_read_from_disk_every_second(self):
        self.steamvr()
        manager = self.manager()
        with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'config'), 'VROSTBRIDGE_CALIBRATION': 'openxr'}), \
                patch.object(baballonia, 'steamvr_openxr_runtime', wraps=baballonia.steamvr_openxr_runtime) as found:
            for _ in range(5):
                self.assertTrue(manager.openxr_found())
        self.assertEqual(found.call_count, 1)
        with patch.dict('os.environ', {'VROSTBRIDGE_CALIBRATION': ''}), \
                patch.object(baballonia, 'steamvr_openxr_runtime') as never:
            self.assertIsNone(manager.openxr_found())
        never.assert_not_called()


class CalibrationLauncherTests(Folder):
    """Baballonia's calibration starts as a SteamVR overlay, so SteamVR keeps the stream (RC4.6)."""

    def manager(self, elf=True):
        # Without the 1.0.0.2 Vulkan layer (tests/test_vrostbridge_1002.py covers it), as in RC4.6.
        manager = BaballoniaManager(log=[].append, root=self.root / 'data/baballonia', config=self.root / 'config',
                                    state=self.root / 'state/setup.json', curl=None, systemd_run=None, systemctl=None,
                                    vulkan_layer=None)
        folder = manager.install_dir / baballonia.CALIBRATION_FOLDER
        folder.mkdir(parents=True)
        if elf:
            (folder / 'BabbleCalibration.x86_64').write_bytes(b'\x7fELF' + b'godot' * 10)
            (folder / 'BabbleCalibration.x86_64').chmod(0o755)
        return manager, folder

    def test_the_launcher_takes_the_programs_place_once(self):
        manager, folder = self.manager()
        self.assertEqual(manager.prepare_calibration(), 'installed')
        launcher, program = folder / 'BabbleCalibration.x86_64', folder / 'BabbleCalibration.program'
        self.assertEqual(launcher.read_text(), baballonia.CALIBRATION_LAUNCHER)
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue(program.read_bytes().startswith(b'\x7fELF'), "Baballonia's own program is kept")
        self.assertTrue(os.access(program, os.X_OK))
        self.assertEqual(manager.prepare_calibration(), 'ready')
        # A fresh copy of Baballonia's program (set up again over it) is kept in turn.
        launcher.write_bytes(b'\x7fELF' + b'newer')
        self.assertEqual(manager.prepare_calibration(), 'installed')
        self.assertEqual(program.read_bytes(), b'\x7fELF' + b'newer')
        self.assertEqual(launcher.read_text(), baballonia.CALIBRATION_LAUNCHER)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['BabbleCalibration.program', 'BabbleCalibration.x86_64'])

    def test_a_release_without_the_program_is_left_alone(self):
        manager, folder = self.manager(elf=False)
        self.assertIsNone(manager.prepare_calibration())
        self.assertEqual(list(folder.iterdir()), [])

    def test_a_failed_write_never_leaves_baballonia_without_its_program(self):
        manager, folder = self.manager()
        with patch.object(Path, 'write_text', side_effect=OSError('read-only')):
            with self.assertRaises(OSError):
                manager.prepare_calibration()
        self.assertTrue((folder / 'BabbleCalibration.x86_64').read_bytes().startswith(b'\x7fELF'))
        self.assertFalse((folder / 'BabbleCalibration.program').exists())
        self.assertEqual(manager.prepare_calibration(), 'installed')

    def run_launcher(self, *arguments, steamvr=False, environment=None):
        folder = self.root / 'Overlay'
        folder.mkdir(exist_ok=True)
        launcher = folder / 'BabbleCalibration.x86_64'
        launcher.write_text(baballonia.CALIBRATION_LAUNCHER)
        launcher.chmod(0o755)
        program = folder / 'BabbleCalibration.program'
        program.write_text('#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done\n')
        program.chmod(0o755)
        server = None
        if steamvr:
            fake = self.root / 'vrserver'
            shutil.copy('/bin/sleep', fake)
            server = subprocess.Popen([str(fake), '30'])
            self.addCleanup(lambda: (server.kill(), server.wait()))
            time.sleep(.2)
        result = subprocess.run(['/bin/sh', str(launcher), *arguments], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, **(environment or {})))
        if server is not None:
            server.kill()
            server.wait()
        return result

    def test_with_steamvr_openxr_becomes_a_steamvr_overlay(self):
        result = self.run_launcher('-l', 'en GB', '--xr-mode', 'on', steamvr=True)
        self.assertEqual(result.stdout, '[-l][en GB][--use-openvr]')
        self.assertIn('starts as a SteamVR overlay', result.stderr)
        self.assertEqual(self.run_launcher('--xr-mode=on', '-l', 'en', steamvr=True).stdout, '[-l][en][--use-openvr]')

    def test_otherwise_baballonias_arguments_pass_unchanged(self):
        self.assertEqual(self.run_launcher('-l', 'en', '--xr-mode', 'on').stdout, '[-l][en][--xr-mode][on]',
                         'Monado or WiVRn: OpenXR stays')
        self.assertEqual(self.run_launcher('-l', 'en', '--use-debug', steamvr=True).stdout, '[-l][en][--use-debug]')
        self.assertEqual(self.run_launcher('-l', 'en', '--xr-mode', 'on', steamvr=True,
                                           environment={'VROSTBRIDGE_CALIBRATION': 'openxr'}).stdout,
                         '[-l][en][--xr-mode][on]')

    def test_start_puts_it_in_place_and_says_so_once(self):
        manager, folder = self.manager()
        manager.executable.write_text('#!/bin/sh\nsleep 30\n')
        manager.executable.chmod(0o755)
        (manager.install_dir / 'vrostbridge-receipt.json').write_text(json.dumps({'sha256': manager.release['sha256']}))
        logs = []
        manager.log = logs.append
        manager.popen = lambda command, **options: Mock(pid=4321, poll=Mock(return_value=None))
        with patch.dict('os.environ', {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config')}, clear=True):
            manager.start()
        self.assertTrue(any('starts as a SteamVR overlay over the stream' in line for line in logs), logs)
        self.assertEqual((folder / 'BabbleCalibration.x86_64').read_text(), baballonia.CALIBRATION_LAUNCHER)


class XrRuntimeProcessTests(Folder):
    def proc(self, folder, *names):
        proc = self.root / folder
        for pid, name in enumerate(names, start=100):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / 'comm').write_text(name + '\n')
        (proc / 'self').mkdir(parents=True, exist_ok=True)
        (proc / '999').mkdir()  # a process that ended while being read: no comm
        return proc

    def test_as_baballonia_judges_it_when_a_calibration_starts(self):
        self.assertFalse(xr_runtime_running(self.proc('none', 'bash', 'Baballonia.Desk', 'vrmonitor')))
        for name in ('vrserver', 'monado-service', 'wivrn-server', 'VRSERVER'):
            with self.subTest(name=name):
                self.assertTrue(xr_runtime_running(self.proc(name, 'bash', name)))
        self.assertFalse(xr_runtime_running(self.root / 'missing'))


class CalibrationGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.manager, self.cameras, self.relays, self.logs = test_rc45.FakeManager(calibrated=False), test_rc45.FakeCameras(), [], []

        def relay():
            self.relays.append(test_rc45.FakeRelay())
            return self.relays[-1]
        self.xr = False
        patcher = patch.object(eye_tracking, 'xr_runtime_running', lambda: self.xr)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=relay,
                               game_mode=lambda: False)
        self.eye.CHECK_S = .05
        self.addCleanup(self.eye.shutdown, 3)

    def wait(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(condition(), (self.eye.phase, self.eye.message))

    def test_without_steamvr_the_calibration_would_show_on_this_screen(self):
        self.eye.begin('user')  # Open Baballonia, no session
        self.wait(lambda: self.manager.alive)
        self.relays[0].receiving = True
        self.wait(lambda: self.eye.phase == 'running' and 'this screen' in self.eye.message)
        self.assertIn('start a session in Desktop Mode first', self.eye.message)
        self.assertIs(self.eye.status()['steamvr'], False)
        self.xr = True  # SteamVR started by hand: the calibration reaches the headset, but a session is advised
        self.wait(lambda: 'Calibrate during a session' in self.eye.message)
        self.assertIs(self.eye.status()['steamvr'], True)
        self.eye.begin('session', '/run/gaze.sock')  # a VROSTBridge session
        self.wait(lambda: 'follow it in the headset' in self.eye.message)
        self.assertIn('choose the Eye calibration (the first one)', self.eye.message)

    def test_a_calibration_started_without_steamvr_is_named(self):
        self.eye.begin('user')
        self.wait(lambda: self.manager.alive)
        self.manager.is_calibrating = True
        self.wait(lambda: self.eye.phase == 'calibrating')
        self.assertIn('on this screen, not in the headset', self.eye.message)
        self.xr = True
        self.wait(lambda: 'shows over the stream' in self.eye.message)
        self.assertIn('Keep the session running until it has finished', self.eye.message)

    def test_steamvr_is_only_looked_for_while_baballonia_runs(self):
        calls = []
        with patch.object(eye_tracking, 'xr_runtime_running', lambda: calls.append(1) or True):
            self.manager.is_installed = False
            self.eye.begin('session', '/run/gaze.sock')
            self.wait(lambda: self.eye.phase == 'setup')
            self.assertEqual(calls, [])
            self.assertIsNone(self.eye.status()['steamvr'])


class FakeDisplay:
    """x11_window.Display stand-in: Baballonia's window appears after a few looks."""
    def __init__(self, record, appear_after=2, windows=(0x400013,)):
        self.record, self.appear_after, self.windows = record, appear_after, list(windows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def find(self, pids, class_name=None):
        self.record.append(('find', tuple(pids), class_name))
        self.appear_after -= 1
        return self.windows if self.appear_after < 0 else []

    def minimise(self, window):
        self.record.append(('minimise', window))

    def activate(self, window):
        self.record.append(('activate', window))


class BaballoniaWindowTests(unittest.TestCase):
    """RC4.6: Baballonia's window no longer opens over the page at every session start in Desktop Mode."""

    def setUp(self):
        self.manager, self.cameras, self.relays, self.logs = test_rc45.FakeManager(), test_rc45.FakeCameras(), [], []
        self.record = []
        self.game_mode = False

        def relay():
            self.relays.append(test_rc45.FakeRelay())
            return self.relays[-1]
        for patcher in (patch.object(eye_tracking, 'xr_runtime_running', lambda: True),
                        patch.object(eye_tracking.x11_window, 'Display', lambda: FakeDisplay(self.record)),
                        patch.dict('os.environ', {'DISPLAY': ':0'})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=relay,
                               game_mode=lambda: self.game_mode)
        self.eye.CHECK_S = .05
        self.addCleanup(self.eye.shutdown, 3)

    def wait(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(condition(), (self.eye.phase, self.record, self.logs[-3:]))

    def test_started_for_a_session_it_is_minimised_once_its_window_appears(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: ('minimise', 0x400013) in self.record)
        self.assertEqual([entry for entry in self.record if entry[0] == 'minimise'], [('minimise', 0x400013)])
        self.assertEqual(self.record[0], ('find', (4242,), 'Baballonia.Desktop'))
        self.assertIn('Show Baballonia', ' '.join(self.logs))

    def test_opened_by_hand_or_in_game_mode_it_is_left_alone(self):
        self.eye.begin('user')
        self.wait(lambda: self.manager.alive)
        time.sleep(.3)
        self.assertEqual(self.record, [], 'Open Baballonia: the person wants to see it')
        self.eye.end('user')
        self.wait(lambda: not self.manager.alive)
        self.game_mode = True
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        time.sleep(.3)
        self.assertEqual(self.record, [], 'gamescope never focuses it anyway')

    def test_show_baballonia_brings_it_back(self):
        self.manager.alive = True
        with patch.object(eye_tracking.x11_window, 'Display', lambda: FakeDisplay(self.record, appear_after=0)):
            self.eye.show_window()
        self.assertEqual(self.record[-1], ('activate', 0x400013))
        with patch.object(eye_tracking.x11_window, 'Display', lambda: FakeDisplay(self.record, windows=())):
            with self.assertRaisesRegex(ValueError, 'was not found'):
                self.eye.show_window()
        self.manager.alive = False
        with self.assertRaisesRegex(ValueError, 'not running'):
            self.eye.show_window()

        def broken():
            raise OSError('no X display')
        self.manager.alive = True
        with patch.object(eye_tracking.x11_window, 'Display', broken):
            with self.assertRaisesRegex(ValueError, 'taskbar'):
                self.eye.show_window()

    def test_a_session_watcher_never_minimises_a_baballonia_the_person_opened(self):
        """Review finding: a watcher left from a session's start minimised the Baballonia opened by hand."""
        appear = threading.Event()

        class LateDisplay(FakeDisplay):
            def find(display, pids, class_name=None):
                self.record.append(('find', tuple(pids), class_name))
                return list(display.windows) if appear.is_set() and pids else []
        with patch.object(eye_tracking.x11_window, 'Display', lambda: LateDisplay(self.record)):
            self.eye.begin('session', '/run/gaze.sock')
            self.wait(lambda: any(entry[0] == 'find' and entry[1] for entry in self.record))
            self.eye.end('session')
            self.wait(lambda: not self.manager.alive)
            self.eye.begin('user')  # Open Baballonia before the first window ever appeared
            self.wait(lambda: self.manager.alive)
            appear.set()
            time.sleep(1.2)
        self.assertNotIn(('minimise', 0x400013), self.record)

    def test_a_restart_leaves_one_watcher(self):
        appear = threading.Event()

        class LateDisplay(FakeDisplay):
            def find(display, pids, class_name=None):
                self.record.append(('find', tuple(pids), class_name))
                return list(display.windows) if appear.is_set() and pids else []
        with patch.object(eye_tracking.x11_window, 'Display', lambda: LateDisplay(self.record)):
            self.eye.begin('session', '/run/gaze.sock')
            self.wait(lambda: any(entry[0] == 'find' and entry[1] for entry in self.record))
            self.eye.end('session')
            self.wait(lambda: not self.manager.alive)
            self.eye.begin('session', '/run/gaze.sock')
            self.wait(lambda: self.manager.alive)
            time.sleep(.6)
            appear.set()
            self.wait(lambda: ('minimise', 0x400013) in self.record)
            time.sleep(1.2)
        self.assertEqual([entry for entry in self.record if entry[0] == 'minimise'], [('minimise', 0x400013)])

    def test_the_page_action(self):
        case = test_rc45.AppEyeTrackingTests('test_eye_actions')
        app = case.eye_app()
        self.addCleanup(case.doCleanups)
        app.eye.show_window = Mock()
        app.action({'action': 'eye-show'})
        app.eye.show_window.assert_called_once()


class PagePortTests(unittest.TestCase):
    """Review finding: with a new random port at every companion start, a page from an earlier run was another
    origin, so the newer page could not ask it to close. The pages now use a stable port per role when free."""

    def test_the_preferred_port_then_any_free_port(self):
        import socket
        from launcher import app
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        free = probe.getsockname()[1]
        probe.close()
        handler = app.http.server.BaseHTTPRequestHandler
        first = app.page_server('client', handler, ports={'client': free})
        try:
            self.assertEqual(first.server_address, ('127.0.0.1', free))
            second = app.page_server('client', handler, ports={'client': free})  # taken: any free port
            try:
                self.assertNotEqual(second.server_address[1], free)
                self.assertEqual(second.server_address[0], '127.0.0.1')
            finally:
                second.server_close()
        finally:
            first.server_close()
        self.assertEqual(app.PAGE_PORTS, {'client': 9947, 'host': 9948})
        self.assertEqual(app.PageServer.allow_reuse_address, os.name != 'nt')


class X11ErrorHandlerTests(unittest.TestCase):
    """Review finding: Xlib's error handler is per process; restoring it when one of two connections closed
    brought back the default handler, which exits the process on the next BadWindow."""

    def test_the_ignoring_handler_is_installed_once_and_never_put_back(self):
        from launcher import x11_window
        calls = []

        class Library:
            def XOpenDisplay(self, name):
                return 1

            def XSetErrorHandler(self, handler):
                calls.append(handler.value if hasattr(handler, 'value') else handler)
                return 0

            def XDefaultRootWindow(self, display):
                return 7

            def XCloseDisplay(self, display):
                calls.append('close')
        with patch.object(x11_window, '_handler_installed', False):
            first = x11_window.Display(':5', library=Library())
            second = x11_window.Display(':5', library=Library())
            first.close()
            second.close()
            third = x11_window.Display(':5', library=Library())
            third.close()
        handlers = [call for call in calls if call != 'close']
        self.assertEqual(len(handlers), 1, calls)
        self.assertEqual(calls.count('close'), 3)


class GameModeCalibrationTests(unittest.TestCase):
    def test_the_calibration_row_says_a_session_comes_first(self):
        case = test_rc45.GameModeEyeTrackingTests('test_set_up_with_the_licence_then_open_and_close')
        rows = case.rows(case.view(case.state(state='installed')))
        self.assertIn('choose the Eye calibration', rows['info:eye-calibration'][4])
        self.assertIn('only while SteamVR runs on this PC', rows['info:eye-calibration'][4])
        from launcher.game_ui import EYE_NOTES
        self.assertIn('the Eye calibration', EYE_NOTES['calibrate'][2])


class PageTests(unittest.TestCase):
    def test_the_card_names_the_calibration_and_the_session_it_needs(self):
        html = (ROOT / 'launcher/desktop.html').read_text()
        self.assertIn('choose the <b>Eye</b> calibration (the first one)', html)
        self.assertIn('only while SteamVR runs on this PC, so start the session first', html)
        self.assertIn("<i>Gaze</i> and <i>Blink</i> redo one half after a full Eye calibration", html)



class PublicReleaseTests(unittest.TestCase):
    """RC4.6: the licence, the notices and the clean-up for a public release."""

    def test_the_licence_and_notices_exist_and_agree(self):
        licence = (ROOT / 'LICENSE').read_text()
        self.assertTrue(licence.startswith('MIT License'))
        self.assertIn('Copyright (c) 2026 JagerBombastic', licence)
        notice = (ROOT / 'NOTICE').read_text()
        third_party = (ROOT / 'THIRD_PARTY.md').read_text()
        nvidia = 'contains source code provided by NVIDIA Corporation'
        for text in (notice, third_party, (ROOT / 'docs/OPEN_SOURCE_CREDITS.md').read_text()):
            self.assertIn(nvidia, text)
        for name in ('NVIDIA-Video-Codec-SDK-samples.txt', 'NVIDIA-nvEncodeAPI-MIT.txt', 'AMD-AMF-MIT.txt',
                     'backward-cpp-MIT.txt', 'ALVR-Dashboard-fonts.txt', 'Python-Linux-incorporated.txt',
                     'wayland-protocols-drm-lease-MIT.txt', 'websockets-async-timeout-Apache-2.0.txt'):
            self.assertTrue((ROOT / 'licenses' / name).is_file(), name)
            self.assertIn('licenses/' + name, third_party, name)
        self.assertIn('https://crates.io/crates/symphonia/0.5.5', third_party)

    def test_both_packages_carry_the_licence_files(self):
        build = (ROOT / 'packaging/build_distributions.py').read_text()
        self.assertEqual(build.count("'LICENSE','NOTICE','THIRD_PARTY.md'"), 2)
        repack = (ROOT / 'packaging/repack_host_payload.py').read_text()
        self.assertIn("('LICENSE', 'NOTICE', 'THIRD_PARTY.md')", repack)

    def test_a_companion_only_host_carries_the_licence_files(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('repack_host_payload', ROOT / 'packaging/repack_host_payload.py')
        repack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repack)
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder) / 'previous'
            (previous / 'dist/host/bin/win64').mkdir(parents=True)
            (previous / 'dist/host/bin/win64/driver_alvr_server.dll').write_bytes(b'driver')
            for name in ('launcher', 'host', 'licenses', 'docs'):
                (previous / name).mkdir()
            (previous / 'HOST_PROVENANCE.txt').write_text('seed\n')
            (previous / 'PACKAGED.json').write_text(json.dumps({'version': '1.0.0-rc4.5', 'role': 'host',
                                                                'alvr_commit': repack.PIN}))
            repack.manifest(previous)
            output = Path(folder) / 'out'
            from test_rc3_updates import HostRepackTests
            repack.repack(previous, output, launcher=HostRepackTests.launcher(Path(folder)))
            for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY.md'):
                self.assertEqual((output / name).read_bytes(), (ROOT / name).read_bytes(), name)
            repack.verify_input(output)

    def test_the_camera_helper_has_no_unlicensed_module(self):
        helper = ROOT / 'third_party/go-bsb-cams'
        self.assertFalse((helper / 'vendor/github.com/hybridgroup').exists())
        for name in ('go.mod', 'go.sum', 'vendor/modules.txt'):
            self.assertNotIn('mjpeg', (helper / name).read_text(), name)
        main = (helper / 'main.go').read_text()
        self.assertNotIn('"github.com/hybridgroup/mjpeg"', main)
        self.assertIn('newMJPEGStream()', main)
        stream = (helper / 'mjpeg_stream.go').read_text()
        self.assertIn('Added by the VROSTBridge project in October 2026', stream)
        self.assertIn('multipart/x-mixed-replace;boundary=', stream)
        self.assertIn('mjpeg_stream.go', (helper / 'VROSTBRIDGE_CHANGES.md').read_text())

    def test_no_private_addresses_or_ci_binaries(self):
        # The maintainer's own patterns (such as a home network's address prefix) come from the environment, as for
        # scripts/package_public_source.py, so the source itself never names them. Without them only the workflow
        # is checked.
        sys.path.insert(0, str(ROOT / 'scripts'))
        from package_public_source import private_patterns
        patterns = private_patterns()
        for folder in ('tests', 'docs', 'launcher', 'host'):
            for path in (ROOT / folder).rglob('*'):
                if patterns and path.is_file() and path.suffix in ('.py', '.md', '.json', '.js', '.html', '.txt'):
                    data = path.read_bytes()
                    self.assertEqual([p.pattern for p in patterns if p.search(data)], [], str(path))
        workflows = list((ROOT / '.github/workflows').glob('*.yml'))
        self.assertEqual([path.name for path in workflows], ['checks.yml'])
        self.assertNotIn('upload-artifact', workflows[0].read_text())

    def test_the_pages_say_the_licence_and_independence(self):
        html = (ROOT / 'launcher/desktop.html').read_text()
        self.assertIn('free software by JagerBombastic under the MIT licence', html)
        # 1.0.0: the brand pack's short independent-project notice, and tested (not claimed) hardware.
        self.assertIn('VROSTBridge is an independent open-source project and is not affiliated with or endorsed by '
                      'Valve Corporation, Bigscreen', html)
        self.assertIn('Third-party names and marks', html)
        self.assertIn('Tested with Valve Index and Bigscreen Beyond 2e', html)
        game = (ROOT / 'launcher/game_ui.py').read_text()
        self.assertIn('Free software by JagerBombastic, MIT licence', game)
        self.assertIn("'affiliated with or endorsed by Valve, Bigscreen, ALVR", game)


if __name__ == '__main__':
    unittest.main()
