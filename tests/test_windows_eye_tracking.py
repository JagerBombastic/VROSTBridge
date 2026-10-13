"""1.0.0.7: Beyond 2e eye tracking on the Windows client (launcher/baballonia.py, launcher/eye_tracking.py,
launcher/gaze_provider.py), run on Linux with sys.platform set to win32 and stand-ins for Win32, the registry,
WMI and Baballonia's installer. The relay's UDP path uses real sockets on 127.0.0.1."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from launcher import baballonia, eye_tracking, gaze_provider  # noqa: E402
from launcher.baballonia import BaballoniaManager  # noqa: E402
from launcher.eye_tracking import EyeCameras, EyeTracking  # noqa: E402
from launcher.gaze_provider import GazeRelay, gaze_target  # noqa: E402
import test_rc45  # noqa: E402  (its relay fake is reused)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def windows(test):
    """sys.platform is win32 for the rest of the test (undone last, after its threads stopped)."""
    patcher = patch.object(sys, 'platform', 'win32')
    patcher.start()
    test.addCleanup(patcher.stop)


PROGRAM = {'Baballonia.Desktop.exe': b'MZ Baballonia program', 'Baballonia.Desktop.dll': b'MZ desktop library',
           'Baballonia.dll': b'MZ main library'}
TEMPLATE = {'VRC_UseNativeTracking': False, 'ShowOnboardingOnStartup': True, 'OSCInPort': 8889, 'OSCOutPort': 8888,
            'OSCAddress': '127.0.0.1',
            'LeftCamera': {'Camera': 0, 'Roi': {'X': 0, 'Y': 0, 'Width': 256, 'Height': 256}, 'RotationRadians': 0,
                           'Gamma': 1, 'UseRedChannel': False, 'UseHorizontalFlip': False, 'UseVerticalFlip': False},
            'RightCamera': {'Camera': 1, 'Roi': {'X': 0, 'Y': 0, 'Width': 256, 'Height': 256}, 'RotationRadians': 0,
                            'Gamma': 1, 'UseRedChannel': False, 'UseHorizontalFlip': False, 'UseVerticalFlip': False},
            'LastOpenedLeftCamera': '', 'LastOpenedRightCamera': '', 'EyeHome_EyeModel': 'eyeModel.onnx',
            'AppSettings_UseOSCQuery': False, 'AppSettings_OneEuroEnabled': True,
            'AppSettings_OneEuroMinFreqCutoff': 0.5, 'AppSettings_OneEuroSpeedCutoff': 3,
            'AppSettings_SteamVRAutoStart': False, 'AppSettings_UseGPU': True, 'AppSettings_CheckForUpdates': True,
            'AppSettings_LogLevel': 'Information'}
INSTALLER = b'NSIS stand-in installer ' * 4000


def test_release(**changes):
    return dict({'version': '9.9.9',
                 'url': 'https://github.com/Project-Babble/Baballonia/releases/download/v9.9.9/Baballonia.Setup.v9.9.9.exe',
                 'size': len(INSTALLER), 'sha256': sha(INSTALLER), 'extracted_mb': 1, 'capture': 'OpenCvCapture',
                 'display_version': 'v9.9.9', 'files': {name: sha(data) for name, data in PROGRAM.items()}}, **changes)


def put_program(folder, **changed):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in dict(PROGRAM, **changed).items():
        (folder / name).write_bytes(data)
    (folder / 'LocalSettings.json').write_text(json.dumps(TEMPLATE, indent=2), encoding='utf-8')
    (folder / 'Uninstall.exe').write_bytes(b'MZ uninstaller')
    return folder


class FakeProcess:
    def __init__(self, pid, code=0, runs=False, on_kill=None):
        self.pid, self.returncode, self._runs, self.on_kill, self.killed = pid, None, runs, on_kill, False
        self._code = code

    def poll(self):
        if self.returncode is None and not self._runs:
            self.returncode = self._code
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None and self._runs:
            raise subprocess.TimeoutExpired('fake', timeout)
        return self.poll()

    def end(self, code=0):
        self._runs, self._code = False, code

    def kill(self):
        self.killed = True
        if self.on_kill:
            self.on_kill(self)
        self.end(1)


class FakeWin32:
    """Win32 stand-in: a table of processes (pid -> path), their windows, and job objects."""

    def __init__(self):
        self.calls, self.table, self.jobs, self.windows_of, self.minimised = [], {}, {}, {}, set()
        self.next_handle, self.fail_assign, self.close_ends, self.present = 100, False, True, True
        self.lock = threading.Lock()

    def spawn(self, pid, path, window=None):
        with self.lock:
            self.table[pid] = str(path)
            if window is not None:
                self.windows_of[pid] = window

    def end(self, pid):
        with self.lock:
            self.table.pop(pid, None)
            self.windows_of.pop(pid, None)

    def create_job(self, single_process=False):
        self.next_handle += 1
        self.calls.append(('create_job', single_process))
        self.jobs[self.next_handle] = set()
        return self.next_handle

    def assign(self, job, pid):
        self.calls.append(('assign', job, pid))
        if self.fail_assign:
            raise OSError(5, 'AssignProcessToJobObject failed')
        self.jobs[job].add(pid)

    def terminate_job(self, job, code=1):
        self.calls.append(('terminate_job', job))
        for pid in self.jobs.get(job, ()):
            self.end(pid)
        return True

    def close_handle(self, handle):
        self.calls.append(('close', handle))
        return True

    def processes(self):
        with self.lock:
            return [(pid, Path(path).name) for pid, path in self.table.items()] + [(4, 'System'), (77, 'vrserver.exe')]

    def image_path(self, pid):
        with self.lock:
            return self.table.get(pid)

    def terminate(self, pid, path=None, wait_ms=3000):
        self.calls.append(('terminate', pid, path))
        with self.lock:
            if pid not in self.table or (path is not None and self.table[pid] != str(path)):
                return False
        self.end(pid)
        return True

    def windows(self, pids):
        with self.lock:
            return [self.windows_of[pid] for pid in pids if pid in self.windows_of]

    def post_close(self, hwnd):
        self.calls.append(('post_close', hwnd))
        if self.close_ends:
            for pid, window in list(self.windows_of.items()):
                if window == hwnd:
                    self.end(pid)
        return True

    def show(self, hwnd, command):
        self.calls.append(('show', hwnd, command))
        if command in (baballonia.SW_RESTORE, baballonia.SW_SHOW, baballonia.SW_SHOWNORMAL):
            self.minimised.discard(hwnd)
        elif command == baballonia.SW_SHOWMINNOACTIVE:
            self.minimised.add(hwnd)
        return True

    def is_minimised(self, hwnd):
        return hwnd in self.minimised

    def bring_to_front(self, hwnd):
        self.calls.append(('front', hwnd))
        return True

    def device_present(self, instance_id):
        self.calls.append(('present', instance_id))
        return self.present


class FakeRegistry:
    def __init__(self, folders):
        self.keys, self.entries, self.folders, self.subkey_names = {}, [], folders, {}

    def subkeys(self, hive, path):
        return list(self.subkey_names.get((hive, path), []))

    def read_values(self, hive, path):
        values = self.keys.get((hive, path))
        return dict(values) if values is not None else None

    def restore_values(self, hive, path, values):
        if values is None:
            self.keys.pop((hive, path), None)
        else:
            self.keys[(hive, path)] = dict(values)

    def delete_key(self, hive, path):
        self.keys.pop((hive, path), None)

    def uninstall_entries(self):
        return [dict(entry) for entry in self.entries]

    def user_folder(self, name):
        return str(self.folders[name]) if name in self.folders else None


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # A user name with spaces and an apostrophe, as /D= must survive.
        self.home = Path(self.temp.name) / "Anne O'Brien"
        self.local = self.home / 'AppData/Local'
        self.roaming = self.home / 'AppData/Roaming'
        folders = {'Desktop': self.home / 'Desktop', 'Programs': self.roaming / 'Microsoft/Windows/Start Menu/Programs',
                   'Personal': self.home / 'Documents'}
        for folder in folders.values():
            folder.mkdir(parents=True)
        self.win32, self.registry = FakeWin32(), FakeRegistry(folders)
        self.folders = folders
        environment = patch.dict('os.environ', {'LOCALAPPDATA': str(self.local), 'APPDATA': str(self.roaming),
                                                'SystemRoot': 'C:\\Windows', 'USERPROFILE': str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        windows(self)
        self.popen_calls, self.logs = [], []
        self.camera = {'state': 'present', 'name': 'BigEye', 'device_id': 'USB\\VID_35BD&PID_0202&MI_00\\7&1&0&0000',
                       'message': ''}

    def manager(self, release=None, popen=None, **options):
        manager = BaballoniaManager(log=self.logs.append, root=self.local / 'VROSTBridge/Baballonia',
                                    config=self.roaming / 'ProjectBabble', state=self.local / 'VROSTBridge/state.json',
                                    popen=popen or self.installer, release=release or test_release(),
                                    win32=self.win32, registry=self.registry, camera_check=lambda: dict(self.camera),
                                    curl='/bin/sh', systemd_run=None, systemctl=None, **options)
        return manager

    # Baballonia's NSIS installer: /S /D=<folder>, its program there, its uninstall entry and three shortcuts.
    def installer(self, command, **options):
        self.popen_calls.append((command, options))
        target = Path(command.split(' /D=', 1)[1])
        put_program(target)
        self.registry.keys[('HKCU', baballonia.UNINSTALL_KEY)] = {
            'DisplayName': ('Baballonia', 1), 'DisplayVersion': ('v9.9.9', 1), 'InstallLocation': (str(target), 1),
            'UninstallString': (str(target / 'Uninstall.exe'), 1)}
        for folder, name in baballonia.INSTALLER_SHORTCUTS:
            (self.folders[folder] / name).write_bytes(b'L\0\0\0' + str(target).encode('utf-16-le'))
        return FakeProcess(5000)

    def install(self, manager, download=INSTALLER):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k: Response(download)) as urlopen, \
                patch('ssl.create_default_context', return_value=Mock()):
            started = manager.install_async()
            if started:
                manager.job.join(20)
        self.urlopen = urlopen
        return manager.status(running=False)


class PinTests(unittest.TestCase):
    def test_the_windows_release_is_project_babbles_installer_checked_by_size_and_sha256(self):
        pin = baballonia.WINDOWS_RELEASE
        self.assertEqual(pin['version'], baballonia.RELEASE['version'])
        self.assertEqual(pin['url'], 'https://github.com/Project-Babble/Baballonia/releases/download/v1.1.1.0rc6/'
                                     'Baballonia.Setup.v1.1.1.0rc6.exe')
        self.assertEqual(pin['size'], 423324292)
        self.assertEqual(pin['sha256'], 'dec2e8fd1e5c844662f99b47680d772a393f6eb2fdf4e4dd3c62853928b2c470')
        self.assertEqual((pin['capture'], pin['display_version'], pin['extracted_mb']), ('OpenCvCapture', 'v1.1.1.0rc6', 877))
        self.assertEqual(sorted(pin['files']), ['Baballonia.Desktop.dll', 'Baballonia.Desktop.exe', 'Baballonia.dll'])
        self.assertTrue(all(len(digest) == 64 for digest in pin['files'].values()))

    def test_the_platform_is_decided_when_asked(self):
        self.assertIs(baballonia.default_release(), baballonia.RELEASE)
        with patch.object(sys, 'platform', 'win32'):
            self.assertIs(baballonia.default_release(), baballonia.WINDOWS_RELEASE)
            self.assertEqual(baballonia.program_name(), 'Baballonia.Desktop.exe')
            with patch.dict('os.environ', {'LOCALAPPDATA': 'C:\\Users\\a\\AppData\\Local',
                                           'APPDATA': 'C:\\Users\\a\\AppData\\Roaming'}):
                self.assertEqual(baballonia.data_root(), Path('C:\\Users\\a\\AppData\\Local') / 'VROSTBridge' / 'Baballonia')
                self.assertEqual(baballonia.config_root(), Path('C:\\Users\\a\\AppData\\Roaming') / 'ProjectBabble')
                self.assertEqual(BaballoniaManager(log=lambda line: None, vulkan_layer=None).pinned['sha256'],
                                 baballonia.WINDOWS_RELEASE['sha256'])
        self.assertEqual(baballonia.program_name(), 'Baballonia.Desktop')

    def test_windows_keeps_the_whole_environment(self):
        base = {'SystemRoot': 'C:\\Windows', 'APPDATA': 'x', 'LOCALAPPDATA': 'y', 'TEMP': 'z', 'PATH': 'p',
                'https_proxy': 'http://proxy:3128'}
        with patch.object(sys, 'platform', 'win32'):
            self.assertEqual(baballonia.launch_environment(base), base)
        self.assertEqual(baballonia.launch_environment(base), {'PATH': 'p'}, 'Linux: unchanged')

    def test_the_job_structures_have_windows_sizes(self):
        extended, entry = baballonia.job_structures()
        import ctypes
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(extended), 144)
            if ctypes.sizeof(ctypes.c_wchar) == 2:  # Windows: wchar_t is 16-bit
                self.assertEqual(ctypes.sizeof(entry), 568)

    @unittest.skipIf(sys.platform == 'win32', 'off Windows only')
    def test_the_win32_layer_fails_soft_off_windows(self):
        api = baballonia.WindowsApi()
        with self.assertRaises(OSError):
            api.processes()
        self.assertIsNone(api.device_present('USB\\VID_35BD&PID_0202\\1'))
        with self.assertRaises(OSError):
            baballonia.WindowsRegistry().read_values('HKCU', baballonia.UNINSTALL_KEY)


class WindowsSetupTests(Fixture):
    def test_download_checked_then_installed_silently_into_vrostbridges_folder(self):
        manager = self.manager()
        status = self.install(manager)
        self.assertEqual(status['state'], 'installed', status)
        self.assertEqual(status['installation'], 'vrostbridge')
        self.urlopen.assert_called_once()  # urllib (Windows' certificates), never curl
        target = manager.root / '9.9.9'
        self.assertEqual(manager.install_dir, target)
        self.assertEqual(manager.executable, target / 'Baballonia.Desktop.exe')
        (command, options), = self.popen_calls
        installer = manager.root / 'Baballonia.Setup.v9.9.9.exe'
        self.assertEqual(command, subprocess.list2cmdline([str(installer), '/S']) + ' /D=' + str(target))
        self.assertTrue(command.endswith(' /D=' + str(target)), 'last')
        self.assertNotIn('"', command.split(' /D=')[1], 'unquoted, spaces and all')
        self.assertIn(' ', str(target))
        self.assertEqual(options['creationflags'], baballonia.CREATE_NO_WINDOW | baballonia.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertNotIn('shell', options)
        self.assertIn(('create_job', True), self.win32.calls, 'one process: the installer cannot start Baballonia')
        job = next(call[1] for call in self.win32.calls if call[0] == 'assign')
        self.assertIn(('assign', job, 5000), self.win32.calls)
        self.assertIn(('terminate_job', job), self.win32.calls)
        self.assertIn(('close', job), self.win32.calls)
        receipt = json.loads((target / 'vrostbridge-receipt.json').read_text())
        self.assertEqual((receipt['sha256'], receipt['platform'], receipt['capture']),
                         (sha(INSTALLER), 'windows', 'OpenCvCapture'))
        self.assertEqual(sorted(path.name for path in manager.root.iterdir()), ['9.9.9'], 'the download is gone')
        # What the installer wrote outside its folder is as it was: no entry, no shortcuts for VROSTBridge's copy.
        self.assertNotIn(('HKCU', baballonia.UNINSTALL_KEY), self.registry.keys)
        for folder, name in baballonia.INSTALLER_SHORTCUTS:
            self.assertFalse((self.folders[folder] / name).exists(), name)
        self.assertIn('set up in ' + str(target), self.logs[-1])
        self.assertFalse(manager.install_async(), 'nothing to do once installed')

    def test_a_baballonia_the_person_installed_keeps_its_entry_and_shortcuts(self):
        theirs = {'DisplayName': ('Baballonia', 1), 'DisplayVersion': ('v1.1.0.9', 1),
                  'InstallLocation': (str(self.local / 'Baballonia'), 1)}
        self.registry.keys[('HKCU', baballonia.UNINSTALL_KEY)] = dict(theirs)
        shortcut = self.folders['Programs'] / 'Baballonia.lnk'
        shortcut.write_bytes(b'their shortcut')
        status = self.install(self.manager())
        self.assertEqual(status['state'], 'installed', status)
        self.assertEqual(self.registry.keys[('HKCU', baballonia.UNINSTALL_KEY)], theirs)
        self.assertEqual(shortcut.read_bytes(), b'their shortcut')
        self.assertFalse((self.folders['Desktop'] / 'Baballonia.lnk').exists())

    def test_a_download_that_does_not_match_is_never_run(self):
        for release, words in ((test_release(size=len(INSTALLER) + 1), 'not the release'),
                               (test_release(sha256='0' * 64), 'SHA-256')):
            self.popen_calls.clear()
            manager = self.manager(release)
            status = self.install(manager)
            self.assertEqual(status['state'], 'error')
            self.assertIn(words, status['message'])
            self.assertEqual(self.popen_calls, [])
            self.assertEqual(list(manager.root.iterdir()), [])

    def test_a_failed_installer_keeps_nothing_and_puts_registrations_back(self):
        def failing(command, **options):
            self.installer(command, **options)
            return FakeProcess(5000, code=2)
        manager = self.manager(popen=failing)
        status = self.install(manager)
        self.assertEqual(status['state'], 'error')
        self.assertIn('exit code 2', status['message'])
        self.assertFalse((manager.root / '9.9.9').exists())
        self.assertNotIn(('HKCU', baballonia.UNINSTALL_KEY), self.registry.keys)
        self.assertFalse((self.folders['Desktop'] / 'Baballonia.lnk').exists())

    def test_installed_files_must_be_the_pinned_releases(self):
        def other(command, **options):
            self.installer(command, **options)
            (Path(command.split(' /D=', 1)[1]) / 'Baballonia.dll').write_bytes(b'another release')
            return FakeProcess(5000)
        status = self.install(self.manager(popen=other))
        self.assertEqual(status['state'], 'error')
        self.assertIn("Baballonia.dll is not the pinned release's", status['message'])

    def test_a_baballonia_its_installer_started_anyway_is_closed(self):
        self.win32.fail_assign = True

        def launching(command, **options):
            process = self.installer(command, **options)
            self.win32.spawn(6000, Path(command.split(' /D=', 1)[1]) / 'Baballonia.Desktop.exe')
            return process
        manager = self.manager(popen=launching)
        status = self.install(manager)
        self.assertEqual(status['state'], 'installed', status)
        self.assertIn(('terminate', 6000, str(manager.root / '9.9.9' / 'Baballonia.Desktop.exe')), self.win32.calls)
        self.assertNotIn(6000, self.win32.table)
        self.assertTrue(any('could not be put in its job object' in line for line in self.logs))

    def test_cancel_ends_the_installer_and_keeps_nothing(self):
        holder = {}

        def slow(command, **options):
            self.installer(command, **options)
            holder['process'] = FakeProcess(5000, runs=True)
            return holder['process']
        manager = self.manager(popen=slow)

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k: Response(INSTALLER)), \
                patch('ssl.create_default_context', return_value=Mock()):
            self.assertTrue(manager.install_async())
            deadline = time.monotonic() + 10
            while 'process' not in holder and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(manager.status(running=False)['state'], 'installing')
            manager.cancel_install()
            manager.job.join(10)
        status = manager.status(running=False)
        self.assertEqual((status['state'], status['message']), ('missing', 'Set-up cancelled. Nothing was kept.'))
        self.assertTrue(holder['process'].killed)
        self.assertEqual(sum(1 for call in self.win32.calls if call[0] == 'terminate_job'), 1)
        self.assertFalse((manager.root / '9.9.9').exists())

    def test_the_command_line_refuses_a_relative_folder(self):
        with self.assertRaises(ValueError):
            BaballoniaManager.installer_command('C:\\x\\setup.exe', 'relative\\folder')


class AdoptionTests(Fixture):
    def entry(self, folder, version='v9.9.9'):
        self.registry.entries.append({'hive': 'HKCU', 'key': 'Baballonia', 'DisplayName': 'Baballonia',
                                      'DisplayVersion': version, 'InstallLocation': str(folder),
                                      'UninstallString': str(Path(folder) / 'Uninstall.exe')})

    def test_the_persons_own_installation_of_the_pinned_release_is_used_without_a_download(self):
        theirs = put_program(self.local / 'Baballonia')
        self.entry(theirs)
        manager = self.manager()
        before = manager.status(running=False)
        self.assertEqual(before['installed_here'], {'version': 'v9.9.9', 'usable': True})
        self.assertIn('Set up eye tracking uses it, with nothing to download', before['message'])
        status = self.install(manager)
        self.assertEqual(status['state'], 'installed', status)
        self.assertEqual(status['installation'], 'yours')
        self.assertIn('the one installed on this PC', status['message'])
        self.urlopen.assert_not_called()
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(manager.install_dir, theirs)
        self.assertEqual(manager.executable, theirs / 'Baballonia.Desktop.exe')
        self.assertIn('nothing was downloaded', self.logs[-1])
        # Remove only stops using it.
        manager.remove()
        self.assertTrue((theirs / 'Baballonia.Desktop.exe').is_file())
        self.assertFalse(manager.installed())

    def test_another_version_or_other_files_are_left_alone_and_ours_is_installed(self):
        theirs = put_program(self.local / 'Baballonia')
        self.entry(theirs, 'v1.1.0.9')
        manager = self.manager()
        found = manager.status(running=False)
        self.assertEqual(found['installed_here'], {'version': 'v1.1.0.9', 'usable': False})
        self.assertIn('(v1.1.0.9) stays as it is', found['message'])
        status = self.install(manager)
        self.assertEqual((status['state'], status['installation']), ('installed', 'vrostbridge'))
        self.urlopen.assert_called_once()
        # Without its entry, only its files tell: these are another release's.
        self.registry.entries.clear()
        (theirs / 'Baballonia.dll').write_bytes(b'MZ main library of another release')
        self.assertIsNone(self.manager().adopt_installation())
        changed = put_program(self.home / 'Programs/Baballonia', **{'Baballonia.dll': b'modified'})
        self.entry(changed)
        self.assertIsNone(self.manager().adopt_installation())

    def test_a_changed_installation_is_no_longer_used(self):
        theirs = put_program(self.local / 'Baballonia')  # found by its default folder, without an entry
        manager = self.manager()
        self.assertEqual(manager.adopt_installation(), theirs)
        self.assertTrue(manager.installed())
        (theirs / 'Baballonia.dll').write_bytes(b'MZ main library, updated by its own installer')
        self.assertFalse(manager.installed())
        self.assertEqual(manager.status(running=False)['state'], 'missing')


class WindowsSettingsTests(Fixture):
    def installed(self):
        manager = self.manager()
        self.assertEqual(self.install(manager)['state'], 'installed')
        return manager

    def test_both_eyes_read_bigeye_through_opencv_and_send_to_the_relay(self):
        manager = self.installed()
        self.assertEqual(manager.settings_path, self.roaming / 'ProjectBabble/ApplicationData/LocalSettings.json')
        mine = dict(TEMPLATE, AppSettings_LogLevel='Debug', EyeHome_EyeModel='C:\\Users\\a\\model.onnx',
                    RightCamera=dict(TEMPLATE['RightCamera'], Gamma=1.4))
        manager.settings_path.parent.mkdir(parents=True)
        manager.settings_path.write_text(json.dumps(mine), encoding='utf-8')
        changed = manager.configure((800, 400), camera='BigEye')
        settings = json.loads(manager.settings_path.read_text(encoding='utf-8'))
        for side in ('Left', 'Right'):
            self.assertEqual(settings['LastOpened' + side + 'Camera'], 'BigEye')
            self.assertEqual(settings['LastOpenedPreferredCapture' + side + 'Camera'], 'OpenCvCapture')
            self.assertIs(settings['ShouldAutostart' + side + 'Camera'], True)
        self.assertEqual((settings['OSCAddress'], settings['OSCOutPort'], settings['VRC_UseNativeTracking']),
                         ('127.0.0.1', 9005, True))
        for key in ('UseOSCQuery', 'AppSettings_UseOSCQuery', 'AppSettings_SteamVRAutoStart', 'AppSettings_CheckForUpdates',
                    'ShowOnboardingOnStartup', 'AppSettings_UseGPU'):
            self.assertIs(settings[key], False, key)
        self.assertEqual((settings['AppSettings_OneEuroMinFreqCutoff'], settings['AppSettings_OneEuroSpeedCutoff']),
                         (2.0, 60.0), "Linux's filter and its versioning")
        self.assertEqual(settings['LeftCamera']['Roi'], {'X': 0, 'Y': 0, 'Width': 399, 'Height': 399})
        self.assertEqual(settings['RightCamera']['Roi'], {'X': 400, 'Y': 0, 'Width': 399, 'Height': 399})
        self.assertEqual((settings['AppSettings_LogLevel'], settings['EyeHome_EyeModel'], settings['RightCamera']['Gamma']),
                         ('Debug', 'C:\\Users\\a\\model.onnx', 1.4), 'the person\'s own settings stay')
        self.assertIn('LastOpenedLeftCamera', changed)
        self.assertTrue(manager.settings_path.with_name('LocalSettings.json.before-vrostbridge').is_file())
        self.assertTrue(any('read the BigEye camera (both eyes, OpenCvCapture) and send to 127.0.0.1:9005' in line
                            for line in self.logs))
        self.assertTrue(any('on the processor' in line for line in self.logs))
        # The GPU once: a later choice in Baballonia's settings stays.
        settings['AppSettings_UseGPU'] = True
        manager.settings_path.write_text(json.dumps(settings), encoding='utf-8')
        self.assertNotIn('AppSettings_UseGPU', manager.configure((800, 400), camera='BigEye'))
        self.assertIs(json.loads(manager.settings_path.read_text(encoding='utf-8'))['AppSettings_UseGPU'], True)

    def test_first_settings_come_from_the_installed_template_and_the_found_camera(self):
        manager = self.installed()
        self.camera = {'state': 'generic-name', 'name': 'USB Video Device', 'device_id': 'x', 'message': 'advice'}
        manager.configure((800, 400))
        settings = json.loads(manager.settings_path.read_text(encoding='utf-8'))
        self.assertEqual(settings['LastOpenedLeftCamera'], 'USB Video Device')
        self.assertEqual(settings['AppSettings_LogLevel'], 'Information')
        self.camera = {'state': 'absent', 'name': None, 'device_id': None, 'message': ''}
        self.assertEqual(manager.camera_name(), 'BigEye')

    def test_linux_only_parts_do_nothing_on_windows(self):
        manager = self.installed()
        with patch.dict('os.environ', {'VROSTBRIDGE_CALIBRATION': 'openxr'}):
            self.assertIsNone(manager.prepare_calibration())
            self.assertIsNone(manager.openxr_found())
            self.assertIsNone(baballonia.steamvr_openxr_runtime())
        self.assertIsNone(manager.prepare_vulkan_layer(manager.install_dir))
        self.assertIsNone(manager.adopt_earlier_copy())
        self.assertFalse(manager.unit_active())
        with self.assertRaisesRegex(ValueError, 'Windows needs no camera access'):
            eye_tracking.allow_camera_access()


class WindowsProcessTests(Fixture):
    def started(self, show='minimised'):
        manager = self.manager()
        self.assertEqual(self.install(manager)['state'], 'installed')
        self.runs = []

        def popen(args, **options):
            self.runs.append((args, options))
            process = FakeProcess(4321, runs=True)
            self.win32.spawn(4321, args[0], window=0x5001)
            self.win32.minimised.add(0x5001)
            return process
        manager.popen = popen
        self.assertEqual(manager.start(show=show), 'started')
        return manager

    def test_baballonia_starts_minimised_below_normal_priority_in_a_job(self):
        manager = self.started()
        (args, options), = self.runs
        self.assertEqual(args, [str(manager.executable)])
        self.assertEqual(options['cwd'], str(manager.install_dir))
        self.assertEqual(options['creationflags'] & baballonia.BELOW_NORMAL_PRIORITY_CLASS,
                         baballonia.BELOW_NORMAL_PRIORITY_CLASS)
        info = options['startupinfo']
        self.assertTrue(info.dwFlags & baballonia.STARTF_USESHOWWINDOW)
        self.assertEqual(info.wShowWindow, baballonia.SW_SHOWMINNOACTIVE)
        self.assertEqual(options['env']['SystemRoot'], 'C:\\Windows', 'the whole environment')
        self.assertNotIn('shell', options)
        job = manager.process_job
        self.assertIn(('create_job', False), self.win32.calls)
        self.assertIn(('assign', job, 4321), self.win32.calls)
        self.assertTrue(manager.running())
        self.assertEqual(manager.start(), 'running')
        self.assertEqual(manager.window_pids(), [4321])

    def test_open_baballonia_shows_it_and_a_job_failure_still_runs_it(self):
        self.win32.fail_assign = True
        manager = self.started(show='normal')
        self.assertEqual(self.runs[0][1]['startupinfo'].wShowWindow, baballonia.SW_SHOWNORMAL)
        self.assertIsNone(manager.process_job)
        self.assertIn('could not be tied to VROSTBridge', self.logs[-1])

    def test_stop_closes_its_window_then_ends_what_is_left(self):
        manager = self.started()
        job = manager.process_job
        # An orphan from this installation (started by hand) and its calibration overlay; another Baballonia.
        self.win32.spawn(4400, manager.executable)
        self.win32.spawn(4500, manager.install_dir / 'Calibration/Windows/Overlay/BabbleCalibration.x86_64.exe')
        other = self.local / 'Baballonia/Baballonia.Desktop.exe'
        self.win32.spawn(4600, other)
        self.assertTrue(manager.calibrating())
        self.assertEqual(manager.other_instances(), [str(other)])
        process = manager.process
        original = self.win32.post_close

        def close(hwnd):
            process.end()
            return original(hwnd)
        self.win32.post_close = close
        began = time.monotonic()
        self.assertTrue(manager.stop())
        self.assertLess(time.monotonic() - began, 3, 'it closed when asked')
        self.assertIn(('post_close', 0x5001), self.win32.calls)
        self.assertNotIn(('terminate', 4321, str(manager.executable)), self.win32.calls, 'closed, not ended')
        self.assertIn(('terminate_job', job), self.win32.calls)
        self.assertIn(('close', job), self.win32.calls)
        self.assertIn(('terminate', 4400, str(manager.executable)), self.win32.calls)
        self.assertIn(('terminate', 4500, str(manager.install_dir / 'Calibration/Windows/Overlay/'
                                              'BabbleCalibration.x86_64.exe')), self.win32.calls)
        self.assertIn(4600, self.win32.table, 'another Baballonia is not ours to end')
        self.assertFalse(manager.running())
        self.assertIsNone(manager.process_job)

    def test_one_that_does_not_close_is_ended_after_the_grace(self):
        manager = self.started()
        self.win32.close_ends = False
        with patch.object(baballonia, 'STOP_GRACE_WINDOWS_S', .3):
            self.assertTrue(manager.stop())
        self.assertIn(('terminate_job', next(c[1] for c in self.win32.calls if c[0] == 'assign' and c[2] == 4321)),
                      self.win32.calls)
        self.assertNotIn(4321, self.win32.table)

    def test_show_and_minimise_its_window(self):
        manager = self.started()
        manager.show_window()
        self.assertEqual(self.win32.calls[-2:], [('show', 0x5001, baballonia.SW_RESTORE), ('front', 0x5001)])
        self.assertEqual(manager.minimise_windows(lambda: False), 0, 'Open Baballonia: left alone')
        self.assertEqual(manager.minimise_windows(), 1)
        self.assertEqual(self.win32.calls[-1], ('show', 0x5001, baballonia.SW_SHOWMINNOACTIVE))
        self.assertEqual(manager.minimise_windows(), 0, 'already minimised')
        self.win32.windows_of.clear()
        self.assertIsNone(manager.minimise_windows())
        with self.assertRaisesRegex(ValueError, 'was not found'):
            manager.show_window()
        self.win32.end(4321)
        manager.process.end()
        with self.assertRaisesRegex(ValueError, 'not running'):
            manager.show_window()

    def test_steamvr_is_found_by_its_program_name(self):
        self.assertTrue(baballonia.xr_runtime_running(names=['explorer.exe', 'vrserver.exe']))
        self.assertFalse(baballonia.xr_runtime_running(names=['explorer.exe']))
        with patch.object(baballonia, 'windows_api', lambda: self.win32):
            self.assertTrue(baballonia.xr_runtime_running())

    def test_reports_carry_baballonias_own_log(self):
        manager = self.manager()
        self.assertEqual(manager.log_path(), manager.root / 'baballonia.log')
        logs = self.folders['Personal'] / 'ProjectBabble'
        logs.mkdir()
        for name, when in (('baballonia_desktop.2026-10-07.log', 100), ('baballonia_desktop.2026-10-08.log', 200)):
            (logs / name).write_text('log')
            os.utime(logs / name, (when, when))
        self.assertEqual(manager.log_path(), logs / 'baballonia_desktop.2026-10-08.log')


class RemovalTests(Fixture):
    def test_remove_keeps_settings_and_clears_what_a_cut_off_set_up_left(self):
        manager = self.manager()
        self.assertEqual(self.install(manager)['state'], 'installed')
        manager.configure((800, 400), camera='BigEye')
        target = manager.install_dir
        # A set-up cut off before it put things back: entry and shortcut of VROSTBridge's copy.
        self.registry.keys[('HKCU', baballonia.UNINSTALL_KEY)] = {'InstallLocation': (str(target), 1)}
        (self.folders['Desktop'] / 'Baballonia.lnk').write_bytes(b'L' + str(target).encode('utf-16-le'))
        theirs = self.folders['Programs'] / 'Baballonia.lnk'
        theirs.write_bytes(b'L' + str(self.local / 'Baballonia').encode('utf-16-le'))
        manager.remove()
        self.assertFalse(manager.root.exists())
        self.assertTrue(manager.settings_path.is_file(), '%APPDATA%\\ProjectBabble stays')
        self.assertNotIn(('HKCU', baballonia.UNINSTALL_KEY), self.registry.keys)
        self.assertFalse((self.folders['Desktop'] / 'Baballonia.lnk').exists())
        self.assertTrue(theirs.exists(), "the person's own shortcut stays")
        self.assertEqual(manager.status(running=False)['state'], 'missing')

    def test_files_in_use_are_reported(self):
        manager = self.manager()
        self.assertEqual(self.install(manager)['state'], 'installed')
        with patch.object(baballonia, '_rmtree', lambda path: None):
            with self.assertRaisesRegex(OSError, 'in use'):
                manager.remove()

    def test_the_uninstallers_helper(self):
        manager = self.manager()
        self.assertEqual(self.install(manager)['state'], 'installed')
        with patch.object(baballonia, 'windows_api', lambda: self.win32), \
                patch.object(baballonia, 'WindowsRegistry', lambda: self.registry):
            self.assertTrue(baballonia.remove_downloaded(log=self.logs.append, root=manager.root))
            self.assertFalse(manager.root.exists())
            self.assertFalse(baballonia.remove_downloaded(log=self.logs.append, root=manager.root))


WMI_ROWS = [
    {'Name': 'USB Composite Device', 'PNPDeviceID': 'USB\\VID_35BD&PID_0202\\0123', 'PNPClass': 'USB', 'Present': 'TRUE',
     'ConfigManagerErrorCode': '0'},
    {'Name': 'BigEye', 'PNPDeviceID': 'USB\\VID_35BD&PID_0202&MI_00\\7&2F6A3B1&0&0000', 'PNPClass': 'Camera',
     'Present': 'TRUE', 'ConfigManagerErrorCode': '0'},
    {'Name': 'USB Input Device', 'PNPDeviceID': 'USB\\VID_35BD&PID_0202&MI_02\\7&2F6A3B1&0&0002', 'PNPClass': 'HIDClass',
     'Present': 'TRUE', 'ConfigManagerErrorCode': '0'},
]


def wmi_text(rows):
    """As CPython's _wmi answers: 'Name=value' fields, NUL after each, an extra NUL between rows, the last removed."""
    return '\0\0'.join('\0'.join(key + '=' + value for key, value in row.items()) for row in rows)


class CameraTests(unittest.TestCase):
    def test_the_query_is_the_beyonds_usb_device(self):
        self.assertIn("WHERE PNPDeviceID LIKE 'USB\\\\VID_35BD&PID_0202%'", baballonia.EYE_CAMERA_QUERY)
        self.assertTrue(baballonia.EYE_CAMERA_QUERY.startswith('SELECT Name, PNPDeviceID'))
        self.assertTrue(baballonia.EYE_CAMERA_QUERY_BASIC.startswith('SELECT Name, PNPDeviceID FROM Win32_PnPEntity'))

    def test_wmis_answer_is_parsed_into_rows(self):
        self.assertEqual(baballonia.parse_wmi(wmi_text(WMI_ROWS)), WMI_ROWS)
        self.assertEqual(baballonia.parse_wmi(''), [])

    def test_the_camera_function_is_found_by_its_class(self):
        camera = baballonia.windows_eye_camera(lambda query: wmi_text(WMI_ROWS))
        self.assertEqual((camera['state'], camera['name'], camera['device_id']),
                         ('present', 'BigEye', 'USB\\VID_35BD&PID_0202&MI_00\\7&2F6A3B1&0&0000'))

    def test_a_remembered_but_disconnected_headset_is_absent(self):
        rows = [dict(row, Present='FALSE', ConfigManagerErrorCode='45') for row in WMI_ROWS]
        camera = baballonia.windows_eye_camera(lambda query: wmi_text(rows))
        self.assertEqual(camera['state'], 'absent')
        self.assertIn('connected and on', camera['message'])
        self.assertEqual(baballonia.windows_eye_camera(lambda query: '')['state'], 'absent')

    def test_the_generic_name_comes_with_bigscreens_advice(self):
        rows = [dict(row, Name='USB Video Device') if row['PNPClass'] == 'Camera' else row for row in WMI_ROWS]
        camera = baballonia.windows_eye_camera(lambda query: wmi_text(rows))
        self.assertEqual((camera['state'], camera['name']), ('generic-name', 'USB Video Device'))
        self.assertIn('Device Manager', camera['message'])
        self.assertIn('BigEye', camera['message'])

    def test_baballonias_list_shows_directshows_name(self):
        interface = '##?#USB#VID_35BD&PID_0202&MI_00#7&2f6a3b1&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}'
        registry = FakeRegistry({})
        registry.subkey_names[('HKLM', baballonia.CAPTURE_INTERFACES)] = [
            '##?#USB#VID_046D&PID_0825&MI_00#6&1b4e23d4&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}', interface]
        parameters = ('HKLM', baballonia.CAPTURE_INTERFACES + '\\' + interface + '\\#GLOBAL\\Device Parameters')
        registry.keys[parameters] = {'FriendlyName': ('USB Video Device', 1)}
        camera = baballonia.windows_eye_camera(lambda query: wmi_text(WMI_ROWS), registry)
        self.assertEqual((camera['state'], camera['name']), ('generic-name', 'USB Video Device'))
        registry.keys[parameters] = {'FriendlyName': ('BigEye', 1)}
        rows = [dict(row, Name='USB Video Device') if row['PNPClass'] == 'Camera' else row for row in WMI_ROWS]
        camera = baballonia.windows_eye_camera(lambda query: wmi_text(rows), registry)
        self.assertEqual((camera['state'], camera['name'], camera['message']), ('present', 'BigEye', ''))
        self.assertIsNone(baballonia.directshow_name('USB\\VID_35BD&PID_0202&MI_00\\9&9', registry))
        self.assertIsNone(baballonia.directshow_name('USB\\VID_35BD&PID_0202&MI_00\\9&9', FakeRegistry({})))

    def test_the_basic_query_when_the_full_one_fails_then_unknown(self):
        asked = []

        def query(text):
            asked.append(text)
            if 'PNPClass' in text:
                raise OSError('[WinError -2147217385] invalid query')
            return wmi_text([{key: row[key] for key in ('Name', 'PNPDeviceID')} for row in WMI_ROWS])
        camera = baballonia.windows_eye_camera(query)
        self.assertEqual(asked, [baballonia.EYE_CAMERA_QUERY, baballonia.EYE_CAMERA_QUERY_BASIC])
        self.assertEqual((camera['state'], camera['name']), ('present', 'BigEye'))

        def broken(text):
            raise OSError('[WinError 258] The wait operation timed out')
        camera = baballonia.windows_eye_camera(broken)
        self.assertEqual(camera['state'], 'unknown')
        self.assertIn('tries them anyway', camera['message'])


class WindowsCameraTests(unittest.TestCase):
    def setUp(self):
        windows(self)
        self.answer = {'state': 'present', 'name': 'BigEye', 'device_id': 'USB\\VID_35BD&PID_0202&MI_00\\7&1',
                       'message': ''}
        self.win32, self.logs = FakeWin32(), []
        self.cameras = EyeCameras(Path('/nonexistent/go-bsb-cams'), '/nonexistent/cameras', log=self.logs.append,
                                  detect=lambda: dict(self.answer), win32=self.win32)
        self.addCleanup(self.cameras.stop)
        check = patch.object(eye_tracking, 'WINDOWS_CHECK_S', .05)
        check.start()
        self.addCleanup(check.stop)

    def test_no_helper_only_windows_answer(self):
        self.answer = {'state': 'absent', 'name': None, 'device_id': None, 'message': 'x'}
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.cameras.start()
        self.assertEqual(self.cameras.status()['state'], 'no-device')
        self.assertFalse(self.cameras.running())
        self.assertIsNone(self.cameras.wait_frames(lambda: False))
        self.answer = {'state': 'present', 'name': 'BigEye', 'device_id': 'USB\\VID_35BD&PID_0202&MI_00\\7&1',
                       'message': ''}
        self.assertEqual(self.cameras.start(), 'started')
        self.assertEqual(self.cameras.start(), 'running')
        self.assertTrue(self.cameras.running())
        self.assertEqual(self.cameras.wait_frames(lambda: False), (800, 400))
        self.assertEqual(self.cameras.camera_name(), 'BigEye')
        status = self.cameras.status()
        self.assertEqual((status['state'], status['presence'], status['feed']), ('running', 'present', None))
        self.assertEqual(status['camera'], {'name': 'BigEye', 'size': [800, 400]})
        self.assertEqual(self.cameras.orphans(), [])
        self.assertEqual(self.cameras.end_orphans(), 0)
        self.assertTrue(self.cameras.stop())
        self.assertFalse(self.cameras.running())
        self.assertEqual(self.cameras.status()['state'], 'off')

    def test_a_disconnected_headset_ends_the_check_as_the_helper_would_end(self):
        self.cameras.start()
        self.win32.present = False
        deadline = time.monotonic() + 3
        while self.cameras.running() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.cameras.running())
        self.assertEqual(self.cameras.status()['state'], 'no-device')
        self.assertIn(('present', 'USB\\VID_35BD&PID_0202&MI_00\\7&1'), self.win32.calls)
        self.assertIn('disconnected', self.logs[-1])

    def test_the_generic_name_is_used_and_explained_once(self):
        self.answer = {'state': 'generic-name', 'name': 'USB Video Device', 'device_id': 'd',
                       'message': 'Windows lists the Beyond 2e\'s eye cameras as "USB Video Device"'}
        self.cameras.start()
        self.assertEqual(self.cameras.camera_name(), 'USB Video Device')
        self.assertIn('USB Video Device', self.cameras.status()['message'])
        self.cameras.stop()
        self.cameras.start()
        self.assertEqual(sum('USB Video Device' in line for line in self.logs), 1)

    def test_the_pages_never_wait_for_windows(self):
        slow = threading.Event()

        def detect():
            slow.wait(2)
            return dict(self.answer)
        cameras = EyeCameras(Path('/x'), '/y', log=self.logs.append, detect=detect, win32=self.win32)
        began = time.monotonic()
        self.assertIsNone(cameras.status()['presence'])
        self.assertLess(time.monotonic() - began, .5)
        slow.set()
        deadline = time.monotonic() + 3
        while cameras.status()['presence'] is None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(cameras.status()['presence'], 'present')


def packet(address, *values):
    def string(value):
        data = value.encode() + b'\0'
        return data + b'\0' * ((-len(data)) % 4)
    return string(address) + string(',' + 'f' * len(values)) + struct.pack('>' + 'f' * len(values), *values)


class UdpRelayTests(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(gaze_target(('127.0.0.1', 5000)), ('127.0.0.1', 5000))
        self.assertEqual(gaze_target(['localhost', 5000]), ('127.0.0.1', 5000))
        self.assertEqual(gaze_target(Path('/run/gaze.sock')), '/run/gaze.sock')
        self.assertIsNone(gaze_target(None))
        self.assertIsNone(gaze_target(''))
        for wrong in (('192.168.1.20', 5000), ('::1', 5000), ('127.0.0.1', 0), ('127.0.0.1', '5000'), ('127.0.0.1', True),
                      (1, 2, 3), 5000):
            with self.assertRaises(ValueError, msg=repr(wrong)):
                gaze_target(wrong)
        port = gaze_provider.free_udp_port()
        self.assertTrue(0 < port < 65536)

    def test_samples_reach_the_clients_udp_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            sink.bind(('127.0.0.1', 0))
            sink.settimeout(2)
            relay = GazeRelay(('127.0.0.1', sink.getsockname()[1]), 0).start()
            try:
                before = time.monotonic_ns()
                source.sendto(packet('/tracking/eye/LeftRightPitchYaw', 0, 0, 0, 0), relay.incoming.getsockname())
                fields = sink.recv(1024).decode().split()
                self.assertEqual(fields[0], 'VRG1')
                self.assertGreaterEqual(int(fields[1]), before)
                self.assertEqual(fields[2:], ['1', '0', '0', '0', '1'])
                relay.set_target(None)
                source.sendto(packet('/tracking/eye/CenterPitchYaw', 0, 0), relay.incoming.getsockname())
                deadline = time.monotonic() + 2
                while relay.status()['received'] < 2 and time.monotonic() < deadline:
                    time.sleep(.01)
                status = relay.status()
                self.assertEqual((status['received'], status['forwarded'], status['unsent']), (2, 1, 1))
                relay.set_target(('localhost', sink.getsockname()[1]))
                self.assertEqual(relay.path, ('127.0.0.1', sink.getsockname()[1]))
                source.sendto(packet('/tracking/eye/CenterPitchYaw', 0, 0), relay.incoming.getsockname())
                self.assertEqual(sink.recv(1024).split()[0], b'VRG1')
            finally:
                relay.close()
            self.assertFalse(relay.thread.is_alive())

    def test_a_client_not_listening_yet_costs_samples_not_the_relay(self):
        port = gaze_provider.free_udp_port()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            relay = GazeRelay(('127.0.0.1', port), 0).start()
            try:
                for _ in range(3):
                    source.sendto(packet('/tracking/eye/CenterPitchYaw', 0, 0), relay.incoming.getsockname())
                deadline = time.monotonic() + 2
                while relay.status()['received'] < 3 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(relay.status()['received'], 3)
                self.assertTrue(relay.thread.is_alive())
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:  # the client starts listening
                    sink.bind(('127.0.0.1', port))
                    sink.settimeout(2)
                    source.sendto(packet('/tracking/eye/CenterPitchYaw', 0, 0), relay.incoming.getsockname())
                    self.assertEqual(sink.recv(1024).split()[0], b'VRG1')
            finally:
                relay.close()

    def test_without_unix_sockets_as_on_windows(self):
        """CPython on Windows has no socket.AF_UNIX (an AttributeError, not an OSError)."""
        windows_socket = types.SimpleNamespace(**{name: getattr(socket, name) for name in (
            'socket', 'AF_INET', 'SOCK_DGRAM', 'timeout')})
        self.assertFalse(hasattr(windows_socket, 'AF_UNIX'))
        with patch.object(gaze_provider, 'socket', windows_socket):
            relay = GazeRelay(None, 0)
            try:
                self.assertIsNone(relay.outgoing)
                with self.assertRaisesRegex(OSError, 'no Unix sockets'):
                    relay.set_target('/run/gaze.sock')
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink, \
                        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
                    sink.bind(('127.0.0.1', 0))
                    sink.settimeout(2)
                    relay.set_target(('127.0.0.1', sink.getsockname()[1]))
                    relay.start()
                    source.sendto(packet('/tracking/eye/CenterPitchYaw', 10, -5), relay.incoming.getsockname())
                    self.assertEqual(sink.recv(1024).split()[0], b'VRG1')
            finally:
                relay.close()
            with self.assertRaisesRegex(OSError, 'no Unix sockets'):
                GazeRelay('/run/gaze.sock', 0)


class WindowsManager(test_rc45.FakeManager):
    """test_rc45's manager stand-in with the Windows methods eye tracking calls."""

    def __init__(self):
        super().__init__()
        self.shown, self.minimised_calls = [], []

    def configure(self, size, camera=None):
        self.events.append(('configure', size, camera))

    def start(self, show='minimised'):
        self.events.append(('start', show))
        self.alive = True
        return 'started'

    def show_window(self):
        self.shown.append(True)

    def minimise_windows(self, still_wanted=None):
        self.minimised_calls.append(still_wanted)
        return 1 if still_wanted is None or still_wanted() else 0


class WindowsEyeTrackingTests(unittest.TestCase):
    def setUp(self):
        windows(self)
        self.manager, self.relays, self.logs, self.win32 = WindowsManager(), [], [], FakeWin32()
        self.cameras = EyeCameras(Path('/nonexistent/go-bsb-cams'), '/nonexistent/cameras', log=self.logs.append,
                                  detect=lambda: {'state': 'present', 'name': 'BigEye', 'device_id': 'd', 'message': ''},
                                  win32=self.win32)

        def relay():
            self.relays.append(test_rc45.FakeRelay())
            return self.relays[-1]
        xr = patch.object(eye_tracking, 'xr_runtime_running', lambda: True)
        xr.start()
        self.addCleanup(xr.stop)
        self.eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=relay)
        self.eye.CHECK_S = .05
        self.addCleanup(self.eye.shutdown, 3)

    def wait(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(condition(), (self.eye.phase, self.eye.message, self.manager.events))

    def test_a_session_sends_to_the_clients_udp_port_with_bigeye_for_both_eyes(self):
        self.eye.begin('session', ('127.0.0.1', 47001))
        self.assertEqual(self.relays[0].target, ('127.0.0.1', 47001))
        self.wait(lambda: self.manager.alive)
        self.assertEqual(self.manager.events[:2], [('configure', (800, 400), 'BigEye'), ('start', 'minimised')])
        self.wait(lambda: self.manager.minimised_calls)
        self.assertTrue(callable(self.manager.minimised_calls[0]))
        self.wait(lambda: any('was minimised' in line for line in self.logs))
        self.eye.show_window()
        self.assertEqual(self.manager.shown, [True])
        self.eye.end('session')
        self.wait(lambda: self.eye.thread is None)
        self.assertFalse(self.cameras.running())

    def test_open_baballonia_shows_it(self):
        self.eye.begin('user')
        self.wait(lambda: self.manager.alive)
        self.assertIn(('start', 'normal'), self.manager.events)

    def test_targets_off_this_pc_and_missing_sockets_are_os_errors(self):
        with self.assertRaises(OSError):
            self.eye.begin('session', ('192.168.1.20', 47001))
        self.assertEqual(self.eye.owners, set())

        def no_unix():
            raise AttributeError("module 'socket' has no attribute 'AF_UNIX'")
        eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=no_unix)
        self.addCleanup(eye.shutdown, 3)
        with self.assertRaisesRegex(OSError, 'AF_UNIX'):
            eye.begin('session', ('127.0.0.1', 47001))
        self.assertEqual(eye.phase, 'error')

        class PathOnlyRelay(test_rc45.FakeRelay):
            def set_target(self, path):
                if isinstance(path, str):
                    raise AttributeError("module 'socket' has no attribute 'AF_UNIX'")
                self.target = path
        eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=PathOnlyRelay)
        self.addCleanup(eye.shutdown, 3)
        with self.assertRaisesRegex(OSError, 'cannot reach the streaming client'):
            eye.begin('session', '/run/gaze.sock')
        eye.begin('session', ('127.0.0.1', 47001))  # what app.py passes on Windows
        self.assertEqual(eye.relay.target, ('127.0.0.1', 47001))

    def test_windows_texts(self):
        self.eye.xr = False
        self.assertNotIn('Desktop Mode', self.eye._calibration_hint())
        self.assertNotIn('Desktop Mode', self.eye._calibrating_hint())


if __name__ == '__main__':
    unittest.main()
