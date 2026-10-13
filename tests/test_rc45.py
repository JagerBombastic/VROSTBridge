"""RC4.5: Beyond 2e eye tracking set up and run by VROSTBridge (Baballonia, the eye cameras, the relay)."""
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import socket
import shutil
import socketserver
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import baballonia, eye_tracking
from launcher.baballonia import BaballoniaManager, eye_crops, jpeg_size, launch_environment, probe_frame_size
from launcher.eye_tracking import CAMERA_RULE, EyeCameras, EyeTracking, allow_camera_access
from launcher.session_link import SessionError, session_settings


def jpeg(width, height):
    """A minimal JPEG header with a baseline frame (enough for jpeg_size)."""
    sof = b'\xff\xc0\x00\x11\x08' + struct.pack('>HH', height, width) + b'\x03' + b'\x01\x22\x00\x02\x11\x01\x03\x11\x01'
    return b'\xff\xd8' + b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + sof + b'\xff\xd9'


def release_tarball(directory, extra=None, executable=True):
    """A stand-in Baballonia release: its program, default settings and calibration tools."""
    path = Path(directory) / 'release.tar.xz'
    defaults = {'OSCOutPort': 8888, 'OSCAddress': '127.0.0.1', 'VRC_UseNativeTracking': False,
                'ShowOnboardingOnStartup': True, 'EyeHome_EyeModel': 'eyeModel.onnx',
                'LeftCamera': {'Camera': 0, 'Roi': {'X': 0, 'Y': 0, 'Width': 256, 'Height': 256}, 'Gamma': 1},
                'RightCamera': {'Camera': 1, 'Roi': {'X': 0, 'Y': 0, 'Width': 256, 'Height': 256}, 'Gamma': 1}}
    with tarfile.open(path, 'w:xz') as archive:
        def add(name, data, mode=0o644, kind=tarfile.REGTYPE, target=''):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.type, info.linkname = len(data), mode, kind, target
            archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        if executable:
            add('Baballonia.Desktop', b'#!/bin/sh\nsleep 30\n', 0o755)
        add('LocalSettings.json', json.dumps(defaults).encode())
        add('Calibration/Linux/Trainer/BabbleTrainer', b'trainer', 0o644)
        for item in extra or ():
            add(*item)
    data = path.read_bytes()
    return path, {'version': '9.9.9', 'url': 'https://example.invalid/Baballonia.tar.xz', 'size': len(data),
                  'sha256': hashlib.sha256(data).hexdigest(), 'extracted_mb': 1, 'capture': 'IpCameraCapture'}


def fake_curl(directory, source):
    """A curl stand-in that copies the release from disk to --output."""
    script = Path(directory) / 'curl'
    script.write_text('#!/bin/sh\nout=""; prev=""\nfor a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done\n'
                      'cp "' + str(source) + '" "$out"\n')
    script.chmod(0o755)
    return script


class Directory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manager(self, release, curl=None, **options):
        logs = []
        manager = BaballoniaManager(log=logs.append, root=self.root / 'data/baballonia', config=self.root / 'config',
                                    state=self.root / 'state/baballonia-setup.json', curl=str(curl) if curl else None,
                                    systemd_run=options.pop('systemd_run', None), systemctl=options.pop('systemctl', None),
                                    release=release, **options)
        manager.logs = logs
        return manager

    def install(self, manager):
        self.assertTrue(manager.install_async())
        manager.job.join(30)
        return manager.status(running=False)


class BaballoniaSetupTests(Directory):
    def test_download_is_checked_unpacked_and_receipted(self):
        source, release = release_tarball(self.root)
        manager = self.manager(release, fake_curl(self.root, source))
        status = self.install(manager)
        self.assertEqual(status['state'], 'installed', status)
        self.assertTrue(manager.installed())
        receipt = json.loads((manager.install_dir / 'vrostbridge-receipt.json').read_text())
        self.assertEqual((receipt['sha256'], receipt['files']), (release['sha256'], 3))
        trainer = manager.install_dir / 'Calibration/Linux/Trainer/BabbleTrainer'
        self.assertTrue(os.access(trainer, os.X_OK), 'the release ships its trainer without the execute bit')
        self.assertFalse((manager.root / 'download.part').exists())
        self.assertIn('Baballonia 9.9.9 is set up. Calibrate it once in the headset.', status['message'])
        self.assertEqual(status['licence'], 'Babble Software Distribution License 1.0 (non-commercial)')
        self.assertFalse(manager.install_async(), 'nothing to do once installed')

    def test_the_download_honours_the_desktops_proxy(self):
        source, release = release_tarball(self.root)
        curl = self.root / 'curl-env'
        curl.write_text('#!/bin/sh\nenv > "' + str(self.root / 'curl.env') + '"\nout=""; prev=""\n'
                        'for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done\n'
                        'cp "' + str(source) + '" "$out"\n')
        curl.chmod(0o755)
        with patch.dict('os.environ', {'https_proxy': 'http://proxy.home:3128'}):
            status = self.install(self.manager(release, curl))
        self.assertEqual(status['state'], 'installed', status)
        self.assertIn('https_proxy=http://proxy.home:3128', (self.root / 'curl.env').read_text())

    def test_a_download_that_does_not_match_keeps_nothing(self):
        source, release = release_tarball(self.root)
        for change, words in (({'size': release['size'] + 1}, 'not the release'), ({'sha256': '0' * 64}, 'SHA-256')):
            manager = self.manager(dict(release, **change), fake_curl(self.root, source))
            status = self.install(manager)
            self.assertEqual(status['state'], 'error')
            self.assertIn(words, status['message'])
            self.assertFalse(manager.install_dir.exists())
            self.assertEqual([p.name for p in manager.root.iterdir()], [])

    def test_unsafe_archives_are_refused(self):
        for extra, words in (([('../escape', b'x')], 'unsafe path'),
                             ([('link', b'', 0o777, tarfile.SYMTYPE, '/etc/passwd')], 'unexpected entry')):
            directory = self.root / words.replace(' ', '-')
            directory.mkdir()
            source, release = release_tarball(directory, extra)
            manager = self.manager(release, fake_curl(directory, source))
            manager.root = directory / 'data'
            status = self.install(manager)
            self.assertEqual(status['state'], 'error', words)
            self.assertIn(words, status['message'])
            self.assertFalse((directory / 'escape').exists())
            self.assertFalse(manager.install_dir.exists())
        source, release = release_tarball(self.root, executable=False)
        status = self.install(self.manager(release, fake_curl(self.root, source)))
        self.assertIn('no Baballonia.Desktop', status['message'])

    def test_too_little_disk_space_and_cancel(self):
        source, release = release_tarball(self.root)
        manager = self.manager(release, fake_curl(self.root, source))
        with patch('launcher.baballonia.shutil.disk_usage', return_value=Mock(free=10_000)):
            status = self.install(manager)
        self.assertEqual(status['state'], 'error')
        self.assertIn('GB free', status['message'])
        slow = self.root / 'slow-curl'
        slow.write_text('#!/bin/sh\nsleep 20\n')
        slow.chmod(0o755)
        manager = self.manager(release, slow)
        self.assertTrue(manager.install_async())
        time.sleep(.3)
        self.assertEqual(manager.status(running=False)['state'], 'downloading')
        manager.cancel_install()
        manager.job.join(10)
        status = manager.status(running=False)
        self.assertEqual((status['state'], status['message']), ('missing', 'Set-up cancelled. Nothing was kept.'))

    def test_remove_keeps_the_users_settings(self):
        source, release = release_tarball(self.root)
        manager = self.manager(release, fake_curl(self.root, source))
        self.install(manager)
        manager.configure((800, 400))
        manager.remove()
        self.assertFalse(manager.root.exists())
        self.assertTrue(manager.settings_path.is_file(), "Baballonia's own settings and calibration stay")
        self.assertEqual(manager.status(running=False)['state'], 'missing')


class BaballoniaSettingsTests(Directory):
    def installed(self):
        source, release = release_tarball(self.root)
        manager = self.manager(release, fake_curl(self.root, source))
        self.install(manager)
        manager.running = lambda max_age=0: False
        return manager

    def test_first_settings_point_baballonia_at_the_cameras_and_the_relay(self):
        manager = self.installed()
        changed = manager.configure((800, 400))
        settings = json.loads(manager.settings_path.read_text())
        self.assertEqual((settings['OSCAddress'], settings['OSCOutPort'], settings['VRC_UseNativeTracking']),
                         ('127.0.0.1', 9005, True))
        self.assertIs(settings['UseOSCQuery'], False)
        self.assertIs(settings['AppSettings_UseOSCQuery'], False)
        self.assertIs(settings['AppSettings_SteamVRAutoStart'], False)
        self.assertIs(settings['ShowOnboardingOnStartup'], False)
        for side in ('Left', 'Right'):
            self.assertEqual(settings['LastOpened' + side + 'Camera'], 'http://127.0.0.1:8880/stream')
            self.assertEqual(settings['LastOpenedPreferredCapture' + side + 'Camera'], 'IpCameraCapture')
            self.assertIs(settings['ShouldAutostart' + side + 'Camera'], True)
        self.assertEqual(settings['LeftCamera']['Roi'], {'X': 0, 'Y': 0, 'Width': 399, 'Height': 399})
        self.assertEqual(settings['RightCamera']['Roi'], {'X': 400, 'Y': 0, 'Width': 399, 'Height': 399})
        self.assertEqual(settings['LeftCamera']['Gamma'], 1, 'other camera settings are kept')
        self.assertIn('LeftCamera.Roi', changed)
        self.assertEqual(oct(manager.settings_path.stat().st_mode & 0o777), '0o600')
        self.assertFalse(manager.settings_path.with_name('LocalSettings.json.before-vrostbridge').exists(),
                         'nothing to back up on a first start')

    def test_later_settings_keep_the_users_crops_and_back_up_once(self):
        manager = self.installed()
        manager.configure((800, 400))
        settings = json.loads(manager.settings_path.read_text())
        settings['LeftCamera']['Roi'] = {'X': 20, 'Y': 10, 'Width': 300, 'Height': 300}  # set by hand in Baballonia
        settings['OSCOutPort'] = 9000
        settings['EyeHome_EyeModel'] = '/somewhere/tuned_temporal_eye_tracking_20261005_101010.onnx'
        manager.settings_path.write_text('﻿' + json.dumps(settings), encoding='utf-8')  # a BOM is fine
        self.assertEqual(manager.configure((800, 400)), ['OSCOutPort'])
        again = json.loads(manager.settings_path.read_text())
        self.assertEqual(again['LeftCamera']['Roi'], {'X': 20, 'Y': 10, 'Width': 300, 'Height': 300})
        self.assertEqual(again['EyeHome_EyeModel'], settings['EyeHome_EyeModel'])
        self.assertTrue(manager.settings_path.with_name('LocalSettings.json.before-vrostbridge').is_file())
        # A camera picture of another size: crops that no longer fit are set again.
        manager.configure((400, 200))
        self.assertEqual(json.loads(manager.settings_path.read_text())['LeftCamera']['Roi'],
                         {'X': 0, 'Y': 0, 'Width': 199, 'Height': 199})

    def test_the_first_set_up_keeps_crops_a_person_already_chose(self):
        manager = self.installed()
        manager.settings_path.parent.mkdir(parents=True)
        mine = {'LeftCamera': {'Camera': 0, 'Roi': {'X': 30, 'Y': 20, 'Width': 340, 'Height': 350}},
                'RightCamera': {'Camera': 1, 'Roi': {'X': 420, 'Y': 25, 'Width': 340, 'Height': 350}},
                'EyeHome_EyeModel': '/home/u/.config/ProjectBabble/Models/tuned_temporal_eye_tracking_20261001_101010.onnx'}
        manager.settings_path.write_text(json.dumps(mine))
        changed = manager.configure((800, 400))
        settings = json.loads(manager.settings_path.read_text())
        self.assertEqual((settings['LeftCamera']['Roi'], settings['RightCamera']['Roi']),
                         (mine['LeftCamera']['Roi'], mine['RightCamera']['Roi']))
        self.assertNotIn('LeftCamera.Roi', changed)
        self.assertEqual(settings['EyeHome_EyeModel'], mine['EyeHome_EyeModel'])
        # Baballonia's own starting areas are replaced by the Beyond's halves, and say so in the log.
        logs = []
        other = BaballoniaManager(log=logs.append, root=manager.root, config=self.root / 'fresh',
                                  state=self.root / 'fresh-state.json', systemd_run=None, systemctl=None,
                                  release=manager.release)
        other.logs = logs
        other.running = lambda max_age=0: False
        other.configure((800, 400))
        self.assertIn("Eye tracking: Baballonia's left and right eye area set to the halves of the 800 x 400 camera "
                      "picture.", other.logs)

    def test_unreadable_settings_are_kept_aside_and_running_baballonia_is_left_alone(self):
        manager = self.installed()
        manager.settings_path.parent.mkdir(parents=True)
        manager.settings_path.write_text('{broken')
        manager.configure(None)
        self.assertEqual(manager.settings_path.with_name('LocalSettings.json.unreadable').read_text(), '{broken')
        self.assertEqual(json.loads(manager.settings_path.read_text())['OSCOutPort'], 9005)
        manager.running = lambda max_age=0: True
        manager.settings_path.write_text('{}')
        self.assertEqual(manager.configure((800, 400)), [], 'it keeps its settings in memory and writes them back')
        self.assertEqual(manager.settings_path.read_text(), '{}')

    def test_calibration_is_baballonias_trained_model(self):
        manager = self.installed()
        self.assertEqual(manager.calibration(), {'calibrated': False, 'date': None})
        manager.configure((800, 400))
        model = self.root / 'config/Models/tuned_temporal_eye_tracking_20261005_213040.onnx'
        model.parent.mkdir(parents=True)
        settings = json.loads(manager.settings_path.read_text())
        for value in ('eyeModel.onnx', 'tuned_temporal_eye_tracking_20261005_213040.onnx', str(model)):
            settings['EyeHome_EyeModel'] = value
            manager.settings_path.write_text(json.dumps(settings))
            self.assertFalse(manager.calibration()['calibrated'], value)
        model.write_bytes(b'onnx')
        self.assertEqual(manager.calibration(), {'calibrated': True, 'date': '2026-10-05'})
        self.assertIn('calibrated 2026-10-05', manager.status(running=False)['message'])

    def test_eye_crops_follow_baballonias_own_choice(self):
        self.assertEqual(eye_crops((800, 400))[1], {'X': 400, 'Y': 0, 'Width': 399, 'Height': 399})
        self.assertEqual(eye_crops((640, 480)), ({'X': 0, 'Y': 0, 'Width': 640, 'Height': 480},) * 2)
        self.assertIsNone(eye_crops(None))
        self.assertIsNone(eye_crops((20, 8)))


class BaballoniaProcessTests(Directory):
    def installed(self, **options):
        source, release = release_tarball(self.root)
        manager = self.manager(release, fake_curl(self.root, source), **options)
        self.install(manager)
        return manager

    def test_it_runs_as_its_own_user_service_outside_steams_scope(self):
        tools = self.root / 'tools'
        tools.mkdir()
        for name in ('systemd-run', 'systemctl'):
            (tools / name).write_text('#!/bin/sh\n')
            (tools / name).chmod(0o755)
        calls = []

        def runner(command, **options):
            calls.append((command, options))
            return subprocess.CompletedProcess(command, 3 if 'is-active' in command else 0, '', '')
        manager = self.installed(systemd_run=str(tools / 'systemd-run'), systemctl=str(tools / 'systemctl'), runner=runner)
        environment = {'HOME': '/home/u', 'DISPLAY': ':0', 'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': '/run/user/1000',
                       'LD_LIBRARY_PATH': '/tmp/_MEI/lib', 'LD_LIBRARY_PATH_ORIG': '/usr/lib64', 'LD_PRELOAD': 'overlay.so',
                       'SteamAppId': '123', 'SteamGameId': '123', 'STEAM_COMPAT_APP_ID': '123', 'DBUS_SESSION_BUS_ADDRESS': 'x'}
        with patch.dict('os.environ', environment, clear=True):
            self.assertEqual(manager.start(), 'started')
        command = next(c for c, _ in calls if c[0].endswith('systemd-run'))
        self.assertEqual(command[1:6], ['--user', '--quiet', '--collect', '--unit', 'vrostbridge-baballonia'])
        self.assertIn('KillMode=mixed', command)
        self.assertIn('TimeoutStopSec=6', command, "longer than the watcher's own 3-second grace")
        self.assertIn('CPUWeight=25', command)  # 1.0.0.9: its cgroup yields to the stream under contention
        program = command[command.index('--') + 1:]
        self.assertEqual(program[:3], ['/bin/sh', '-c', baballonia.WATCH_SCRIPT])
        # 1.0.0.4: one step down in priority through nice, which replaces itself with Baballonia.
        lower = [baballonia.NICE, '-n', str(baballonia.NICENESS)] if Path(baballonia.NICE).is_file() else []
        self.assertEqual(program[4:], [str(os.getpid()), '3', *lower, str(manager.executable)], 'watching this companion')
        settings = [item for item in command if item.startswith('--setenv=')]
        self.assertIn('--setenv=DISPLAY=:0', settings)
        self.assertFalse([item for item in settings if 'LD_' in item or 'Steam' in item or 'STEAM' in item], settings)
        manager.stop()
        self.assertIn('stop', next(c for c, _ in reversed(calls) if c[0].endswith('systemctl') and 'stop' in c))

    def test_without_systemd_it_starts_in_its_own_session_and_is_killed_after_the_grace(self):
        manager = self.installed()
        manager.executable.write_text('#!/bin/sh\ntrap "" TERM\nwhile :; do sleep 1; done\n')  # ignores SIGTERM, as it does
        self.assertEqual(manager.start(), 'started')
        self.assertTrue(manager.running())
        self.assertEqual(manager.start(), 'running')
        process = manager.process
        time.sleep(.5)  # past the script's trap
        self.assertNotEqual(os.getpgid(process.pid), os.getpgid(0))
        began = time.monotonic()
        self.assertTrue(manager.stop())
        self.assertGreaterEqual(time.monotonic() - began, 2.5, 'SIGTERM first, then SIGKILL after the grace')
        self.assertIsNotNone(process.poll())
        self.assertFalse(manager.running())

    def test_the_launch_environment_is_the_desktops_not_the_frozen_companions(self):
        result = launch_environment({'HOME': '/h', 'LD_LIBRARY_PATH': '/bundle', 'LD_LIBRARY_PATH_ORIG': '/usr/lib',
                                     'SteamAppId': '1', 'WAYLAND_DISPLAY': 'gamescope-0', 'PYTHONPATH': '/bundle',
                                     'https_proxy': 'http://proxy:3128'})
        self.assertEqual(result, {'HOME': '/h', 'WAYLAND_DISPLAY': 'gamescope-0', 'PATH': '/usr/local/bin:/usr/bin:/bin'},
                         'no proxy for Baballonia: its camera reader would send 127.0.0.1 through it')

    def test_the_watcher_survives_systemds_own_expansion(self):
        # systemd-run (254+) expands ${...} in a service's command line, and % is a unit specifier.
        self.assertNotIn('${', baballonia.WATCH_SCRIPT)
        self.assertNotIn('%', baballonia.WATCH_SCRIPT)
        for shell in ('/bin/sh', '/bin/bash', '/bin/dash'):
            if Path(shell).exists():
                check = subprocess.run([shell, '-n', '-c', baballonia.WATCH_SCRIPT], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, (shell, check.stderr))

    def test_the_watcher_ends_the_program_when_the_companion_is_gone(self):
        companion = subprocess.Popen(['sleep', '30'])
        self.addCleanup(companion.wait)
        marker = self.root / 'child.pid'
        program = ['/bin/sh', '-c', 'echo $$ > "$1"; trap "" TERM; while :; do sleep 1; done', 'child', str(marker)]
        watcher = subprocess.Popen(baballonia.watched(program, 1, companion=companion.pid), start_new_session=True)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        child = int(marker.read_text())
        companion.kill()  # the companion crashed or was killed (left unreaped: a zombie still counts as gone)
        self.assertEqual(watcher.wait(10), 0)
        time.sleep(.2)
        self.assertFalse(Path('/proc/%d' % child).exists() and
                         Path('/proc/%d/stat' % child).read_text().split()[2] != 'Z', 'ended with the companion')
        # A program that ends by itself: the watcher passes its exit status on.
        alive = subprocess.Popen(['sleep', '30'])
        self.addCleanup(alive.wait)
        self.addCleanup(alive.kill)
        quick = subprocess.Popen(baballonia.watched(['/bin/sh', '-c', 'exit 7'], 1, companion=alive.pid))
        self.assertEqual(quick.wait(10), 7)

    def test_processes_are_matched_by_resolved_path(self):
        manager = self.installed()
        link = self.root / 'linked'
        link.symlink_to(manager.root)  # e.g. ~/.local/share moved to an SD card and linked
        linked = BaballoniaManager(log=lambda line: None, root=link, config=self.root / 'config',
                                   state=self.root / 'state.json', systemd_run=None, systemctl=None,
                                   release=manager.release)
        shutil.copyfile('/bin/sleep', linked.executable)  # a real program: a script's /proc exe is its shell
        linked.executable.chmod(0o755)
        process = subprocess.Popen([str(linked.executable), '30'])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 5
        while process.pid not in linked._own_processes() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertIn(process.pid, linked._own_processes(), 'the kernel reports /proc/<pid>/exe resolved')
        self.assertTrue(linked.running())
        self.assertEqual(linked.other_instances(), [])
        linked.stop()
        self.assertIsNotNone(process.poll())


class FrameProbeTests(unittest.TestCase):
    def test_the_frame_size_comes_from_the_streams_first_picture(self):
        self.assertEqual(jpeg_size(b'junk' + jpeg(800, 400)), (800, 400))
        self.assertIsNone(jpeg_size(b'\xff\xd8\xff\xe0\x00'))

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace;boundary=b')
                self.end_headers()
                self.wfile.write(b'\r\n--b\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg(800, 400))
        server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(probe_frame_size('http://127.0.0.1:%d/stream' % server.server_address[1], 3), (800, 400))
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            port = unused.getsockname()[1]
        self.assertIsNone(probe_frame_size('http://127.0.0.1:%d/stream' % port, 1))


class RelayTests(unittest.TestCase):
    def test_eyelids_and_expressions_are_not_invalid_and_samples_wait_for_a_session(self):
        from launcher.gaze_provider import GazeRelay
        from test_gaze_integration import packet
        relay = GazeRelay(None, 0).start()
        self.addCleanup(relay.close)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            for data in (packet('/tracking/eye/EyesClosedAmount', .2), b'garbage',
                         packet('/tracking/eye/LeftRightPitchYaw', 1, 2, 1, 2)):
                source.sendto(data, relay.incoming.getsockname())
            deadline = time.monotonic() + 2
            while relay.status()['received'] < 1 and time.monotonic() < deadline:
                time.sleep(.01)
        status = relay.status()
        self.assertEqual((status['other'], status['invalid'], status['received'], status['unsent'], status['forwarded']),
                         (1, 1, 1, 1, 0))
        self.assertEqual(status['state'], 'receiving')


class CameraTests(Directory):
    def helper(self, body):
        script = self.root / 'go-bsb-cams'
        script.write_text('#!/bin/sh\n' + body)
        script.chmod(0o755)
        return script

    def test_states_from_the_helpers_own_words(self):
        for body, state, words in (
                ('echo "2026/10/04 It looks like the cameras cannot be accessed, udev file being created"\n', 'access',
                 'Allow eye camera access'),
                ('echo "2026/10/04 Could Not Find Device, Please Make Sure It Is On And Plugged In !"; exit 1\n',
                 'no-device', 'connected and on'),
                ('echo "listen tcp 127.0.0.1:8880: bind: address already in use"; exit 1\n', 'failed',
                 'address already in use')):
            logs = []
            cameras = EyeCameras(self.helper(body), self.root / 'cams', log=logs.append)
            cameras.start()
            self.assertIsNone(cameras.wait_frames(lambda: False, 5))
            deadline = time.monotonic() + 3
            while cameras.status()['state'] == 'starting' and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(cameras.status()['state'], state, body)
            self.assertIn(words, cameras.status()['message'])
            count = len(logs)
            cameras.start()  # the same failure again: not logged again
            cameras.wait_frames(lambda: False, 3)
            time.sleep(.3)
            self.assertEqual(len(logs), count, logs)

    def test_a_running_helper_and_stop(self):
        cameras = EyeCameras(self.helper('echo "Server Is Running And Can Be Accessed At: http://localhost:8880/stream"\n'
                                         'exec sleep 30\n'), self.root / 'cams', log=lambda line: None)
        self.assertEqual(cameras.start(), 'started')
        self.assertEqual(cameras.start(), 'running')
        deadline = time.monotonic() + 3
        while cameras.status()['state'] != 'running' and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(cameras.status()['state'], 'running')
        with patch('launcher.eye_tracking.probe_frame_size', return_value=(800, 400)):
            self.assertEqual(cameras.wait_frames(lambda: False, 3), (800, 400))
        self.assertTrue(cameras.stop())
        self.assertFalse(cameras.running())
        self.assertEqual(cameras.status()['state'], 'off')

    def test_logging_never_holds_the_camera_lock(self):
        helper = self.root / 'go-bsb-cams'
        shutil.copyfile('/bin/sleep', helper)  # a real program, so /proc names it
        helper.chmod(0o755)
        orphan = subprocess.Popen([str(helper), '30'])  # one an earlier companion left running
        self.addCleanup(orphan.wait)
        deadline = time.monotonic() + 5
        cameras = None
        lines = []

        class OwnedLock:  # tells whether the thread that logs holds it (any holder would make locked() true)
            def __init__(self):
                self.lock, self.owner = threading.Lock(), None

            def __enter__(self):
                self.lock.acquire()
                self.owner = threading.get_ident()
                return self

            def __exit__(self, *details):
                self.owner = None
                self.lock.release()
        cameras = EyeCameras(helper, self.root / 'cams',
                             log=lambda line: lines.append((line, cameras.lock.owner == threading.get_ident())))
        cameras.lock = OwnedLock()
        while orphan.pid not in cameras.orphans() and time.monotonic() < deadline:
            time.sleep(.05)
        cameras.start()  # ends the orphan, logs it, then runs the "helper" (sleep refuses -port and exits)
        self.assertIsNotNone(orphan.wait(5))
        cameras.wait_frames(lambda: False, 3)
        time.sleep(.3)
        self.assertTrue(any('an earlier VROSTBridge left running' in line for line, _ in lines), lines)
        self.assertEqual([line for line, locked in lines if locked], [], 'the companion log takes its own lock')

    def test_a_missing_helper(self):
        cameras = EyeCameras(self.root / 'missing', self.root / 'cams', log=lambda line: None)
        with self.assertRaisesRegex(ValueError, 'repair or reinstall'):
            cameras.start()
        self.assertEqual(cameras.status()['state'], 'failed')


class CameraAccessTests(unittest.TestCase):
    def test_one_fixed_script_installs_the_rule_and_applies_it(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.NamedTemporaryFile() as pkexec:
            allow_camera_access(runner, pkexec.name)
            command = calls[0]
            self.assertEqual(command[:3], [pkexec.name, '/bin/sh', '-c'])
            self.assertEqual(command[4:], ['vrostbridge-camera-access', CAMERA_RULE])
            self.assertIn('/etc/udev/rules.d/70-vrostbridge-bigeye.rules', command[3])
            self.assertIn('udevadm trigger --action=change', command[3])
            self.assertNotIn(CAMERA_RULE, command[3], 'the rule is an argument, not part of the script')
            shipped = (ROOT / 'config/70-vrostbridge-bigeye.rules').read_text().splitlines()[-1]
            self.assertEqual(CAMERA_RULE.splitlines()[-1], shipped, 'the same rule as config/')
            with self.assertRaisesRegex(ValueError, 'not allowed'):
                allow_camera_access(lambda command, timeout: subprocess.CompletedProcess(command, 126, '', ''),
                                    pkexec.name)
        with self.assertRaisesRegex(ValueError, '70-vrostbridge-bigeye.rules'):
            allow_camera_access(runner, '/nonexistent/pkexec')


class FakeManager:
    def __init__(self, installed=True, calibrated=True):
        self.release = {'size': 517_000_000, 'version': '1.1.1.0rc6'}
        self.is_installed, self.calibrated, self.alive, self.is_calibrating = installed, calibrated, False, False
        self.events = []
        self.on_installed = None
        self.stop_delay = 0
        self.stopping = threading.Event()
        self.others = []
        self.job = None

    def other_instances(self):
        return list(self.others)

    def window_pids(self):
        return [4242] if self.alive else []

    def cancel_install(self):
        self.events.append('cancel-install')

    def installed(self):
        return self.is_installed

    def calibration(self):
        return {'calibrated': self.calibrated, 'date': '2026-10-05' if self.calibrated else None}

    def running(self, max_age=0):
        return self.alive

    def calibrating(self):
        return self.is_calibrating

    def configure(self, size):
        self.events.append(('configure', size))

    def start(self):
        self.events.append('start')
        self.alive = True
        return 'started'

    def stop(self):
        self.events.append('stop')
        self.stopping.set()
        time.sleep(self.stop_delay)  # the real one waits up to 3 s for Baballonia, which ignores SIGTERM
        self.alive = False
        self.stopping.clear()
        return True

    def status(self, running=None):
        return {'state': 'installed' if self.is_installed else 'missing', 'running': self.alive,
                'calibrated': self.calibrated}


class FakeCameras:
    def __init__(self, size=(800, 400)):
        self.alive, self.size, self.events = False, size, []
        self.state = {'state': 'off', 'message': ''}

    def running(self):
        return self.alive

    def start(self):
        self.events.append('start')
        self.alive = self.size is not None or self.state['state'] != 'access'
        self.state = {'state': 'running', 'message': ''} if self.size else self.state
        return 'started'

    def wait_frames(self, cancelled, timeout):
        if self.size is None:
            self.alive = False
        return self.size

    def stop(self):
        self.events.append('stop')
        self.alive = False

    def status(self):
        return dict(self.state)

    def path(self):
        return Path('/nonexistent/go-bsb-cams')

    def end_orphans(self, path=None):
        return 0


class FakeRelay:
    def __init__(self):
        self.target, self.closed, self.receiving, self.received = 'unset', False, False, 0

    def set_target(self, path):
        self.target = path

    def status(self):
        return {'state': 'receiving' if self.receiving else 'waiting', 'received': self.received,
                'idle_s': 0.1 if self.receiving else None}

    def close(self):
        self.closed = True


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.manager, self.cameras, self.relays, self.logs = FakeManager(), FakeCameras(), [], []

        def relay():
            self.relays.append(FakeRelay())
            return self.relays[-1]
        self.game_mode = False
        self.eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=relay,
                               game_mode=lambda: self.game_mode)
        self.eye.CHECK_S = .05
        self.addCleanup(self.eye.shutdown, 3)

    def wait(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(condition(), (self.eye.phase, self.eye.message, self.manager.events, self.cameras.events))

    def test_a_session_brings_up_cameras_then_baballonia_and_the_relay_first(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.assertEqual(self.relays[0].target, '/run/gaze.sock', 'the relay listens before Baballonia can start')
        self.wait(lambda: self.manager.alive)
        self.assertEqual(self.manager.events[:2], [('configure', (800, 400)), 'start'])
        self.assertEqual(self.cameras.events, ['start'])
        self.wait(lambda: self.eye.phase == 'waiting')
        self.relays[0].receiving = True
        self.wait(lambda: self.eye.phase == 'running')
        self.eye.end('session')
        self.wait(lambda: self.eye.thread is None)
        self.assertEqual(self.manager.events[-1], 'stop')
        self.assertEqual(self.cameras.events[-1], 'stop')
        self.assertTrue(self.relays[0].closed)
        self.assertIsNone(self.eye.relay)
        self.assertEqual(self.eye.phase, 'off')

    def test_nothing_starts_until_baballonia_is_set_up_and_installation_wakes_it(self):
        self.manager.is_installed = False
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'setup')
        self.assertIn('517 MB', self.eye.message)
        self.assertEqual((self.cameras.events, self.manager.events), ([], []))
        self.manager.is_installed = True
        self.manager.on_installed()
        self.wait(lambda: self.manager.alive)

    def test_game_mode_waits_for_a_calibration_and_open_runs_it_anyway(self):
        self.game_mode, self.manager.calibrated = True, False
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'calibrate')
        self.assertIn('Desktop Mode', self.eye.message)
        self.assertEqual(self.manager.events, [], 'an uncalibrated Baballonia in Game Mode would only cost a CPU core')
        self.eye.begin('user')
        self.wait(lambda: self.manager.alive)

    def test_when_the_cameras_end_baballonia_is_stopped_first_then_both_start_again(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.cameras.alive = False  # the helper died: Baballonia's reader would spin and never reconnect
        self.wait(lambda: self.manager.events.count('start') == 2)
        self.assertEqual(self.manager.events[2:4], ['stop', ('configure', (800, 400))])
        self.assertEqual(self.cameras.events, ['start', 'start'])

    def test_a_baballonia_closed_by_hand_is_not_restarted_until_open(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.manager.alive = False  # its window was closed
        self.wait(lambda: self.eye.phase == 'closed')
        self.assertIn('Open Baballonia', self.eye.message)
        self.wait(lambda: not self.cameras.alive)
        time.sleep(.2)
        self.assertEqual(self.manager.events.count('start'), 1)
        self.eye.begin('user')
        self.wait(lambda: self.manager.alive)
        self.eye.end('session')
        time.sleep(.2)
        self.assertTrue(self.manager.alive, 'opened by hand: it outlives the session')
        self.eye.close_baballonia()
        self.wait(lambda: self.eye.thread is None)
        self.assertFalse(self.manager.alive)

    def test_close_during_a_session_keeps_it_closed(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.eye.close_baballonia()
        self.wait(lambda: self.eye.phase == 'closed' and not self.manager.alive)

    def test_a_baballonia_from_before_this_companion_is_restarted(self):
        self.manager.alive = True  # its OSC sender may have stopped while no relay listened
        self.cameras.alive = True
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.events[:2] == ['stop', ('configure', None)] or 'start' in self.manager.events)
        self.assertEqual(self.manager.events[0], 'stop')
        self.wait(lambda: self.manager.alive)

    def test_silence_restarts_baballonia_and_the_cameras_once_and_never_during_calibration(self):
        self.eye.SILENCE_S = .3
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.wait(lambda: self.manager.events.count('start') == 2, 4)
        self.assertEqual(self.cameras.events[:3], ['start', 'stop', 'start'], 'the cameras restart too')
        time.sleep(.8)
        self.assertEqual(self.manager.events.count('start'), 2, 'at most once in five minutes')
        self.assertIn('restarting it and the eye cameras', ' '.join(self.logs))
        self.wait(lambda: self.eye.phase == 'silent')
        self.assertIn('even after a restart', self.eye.message)
        self.eye.last_restart = -1e9
        self.manager.is_calibrating = True
        self.wait(lambda: self.eye.phase == 'calibrating')
        time.sleep(.8)
        self.assertEqual(self.manager.events.count('start'), 2)

    def test_a_calibration_may_finish_after_the_session_and_its_model_is_awaited(self):
        self.manager.calibrated = False
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.manager.is_calibrating = True
        self.wait(lambda: self.eye.phase == 'calibrating')
        self.eye.end('session')
        self.wait(lambda: self.eye.phase == 'finishing')
        time.sleep(.2)
        self.assertTrue(self.manager.alive)
        self.manager.is_calibrating = False  # the overlay and trainer ended...
        time.sleep(.3)
        self.assertTrue(self.manager.alive, '...but Baballonia saves the trained model 2 s later')
        self.manager.calibrated = True  # ...now it has
        self.wait(lambda: self.eye.thread is None)
        self.assertFalse(self.manager.alive)
        self.assertIn('Eye tracking: Baballonia saved its calibration.', self.logs)

    def test_cameras_lost_during_a_calibration_restart_baballonia_once_it_is_saved(self):
        self.manager.calibrated = False
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.manager.is_calibrating = True
        self.wait(lambda: self.eye.phase == 'calibrating')
        self.cameras.alive = False  # the helper died mid-calibration: Baballonia is kept for its calibration...
        self.wait(lambda: self.cameras.events.count('start') == 2)
        self.assertEqual(self.manager.events.count('start'), 1)
        self.manager.is_calibrating = False
        self.manager.calibrated = True  # ...saved: now its dead camera reader is replaced
        self.wait(lambda: self.manager.events.count('start') == 2)
        self.assertIn('restarting Baballonia after its calibration', ' '.join(self.logs))

    def test_a_calibration_that_saves_nothing_is_not_waited_for_long(self):
        self.eye.CALIBRATION_SETTLE_S = .4
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.manager.is_calibrating = True
        self.wait(lambda: self.eye.phase == 'calibrating')
        self.eye.end('session')
        self.manager.is_calibrating = False
        self.wait(lambda: self.eye.thread is None)
        self.assertFalse(self.manager.alive)

    def test_a_start_during_the_stop_keeps_its_gaze_socket_and_restarts_what_it_needs(self):
        self.manager.stop_delay = .6
        self.eye.begin('session', '/run/first.sock')
        self.wait(lambda: self.manager.alive)
        self.eye.end('session')  # Stop...
        self.assertTrue(self.manager.stopping.wait(3))
        self.eye.begin('session', '/run/second.sock')  # ...and Start again while Baballonia stops
        self.wait(lambda: self.manager.events.count('start') == 2)
        self.wait(lambda: self.cameras.alive and self.manager.alive)
        self.assertEqual(self.relays[-1].target, '/run/second.sock')
        self.assertEqual(self.eye.target, '/run/second.sock')
        self.assertFalse(self.relays[-1].closed)
        self.assertEqual(self.eye.owners, {'session'})

    def test_a_new_start_tries_the_cameras_again_after_an_access_failure(self):
        self.cameras.size = None
        self.cameras.state = {'state': 'access', 'message': 'Allow eye camera access'}
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'cameras')
        self.eye.end('session')
        self.wait(lambda: self.eye.thread is None)
        self.cameras.size = (800, 400)  # the rule was installed by hand and the headset reconnected
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.assertEqual(self.cameras.events.count('start'), 2)

    def test_another_baballonia_is_named_and_nothing_starts(self):
        self.manager.others = ['/home/u/.local/share/Steam/steamapps/common/Baballonia/Baballonia.Desktop']
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'error')
        self.assertIn('Another Baballonia is open', self.eye.message)
        self.assertNotIn('start', self.manager.events)
        self.manager.others = []
        self.wait(lambda: self.manager.alive)

    def test_camera_access_is_asked_in_the_background(self):
        release = threading.Event()

        def runner(command, timeout):
            release.wait(5)
            return subprocess.CompletedProcess(command, 126, '', '')
        with tempfile.NamedTemporaryFile() as pkexec:
            self.assertTrue(self.eye.camera_access_async(runner, pkexec.name))
            self.assertEqual(self.eye.status()['access']['state'], 'asking')
            self.assertFalse(self.eye.camera_access_async(runner, pkexec.name), 'one prompt at a time')
            release.set()
            self.wait(lambda: self.eye.status()['access']['state'] == 'error')
        self.assertIn('prompt was closed', self.eye.status()['access']['message'])

    def test_shutdown_stops_everything_even_a_calibration(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        self.manager.is_calibrating = True
        self.manager.job = threading.Thread(target=time.sleep, args=(.2,))
        self.manager.job.start()
        self.eye.shutdown(3)
        self.assertIn('cancel-install', self.manager.events, 'a download stops with VROSTBridge')
        self.assertFalse(self.manager.alive)
        self.assertFalse(self.cameras.alive)
        self.assertTrue(self.relays[0].closed)
        self.eye.begin('session', '/run/gaze.sock')
        self.assertEqual(len(self.relays), 1, 'nothing starts while closing')

    def test_cameras_that_send_nothing_are_retried_and_explained(self):
        self.cameras.size = None
        self.cameras.state = {'state': 'no-device', 'message': "The Beyond 2e's eye cameras were not found."}
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'cameras')
        self.assertIn('not found', self.eye.message)
        self.assertEqual(self.manager.events, [])
        time.sleep(.2)
        self.assertEqual(self.cameras.events, ['start'], 'retried every 15 s, not every check')
        self.cameras.size = (800, 400)
        self.eye.begin('user')  # Open Baballonia tries again at once
        self.wait(lambda: self.manager.alive)

    def test_camera_access_waits_for_the_person(self):
        self.cameras.size = None
        self.cameras.state = {'state': 'access', 'message': 'Allow eye camera access'}
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.eye.phase == 'cameras')
        self.eye.CAMERA_RETRY_S = 0
        time.sleep(.3)
        self.assertEqual(self.cameras.events, ['start'])
        self.cameras.size = (800, 400)
        with patch('launcher.eye_tracking.allow_camera_access') as allow:
            self.eye.camera_access()
        allow.assert_called_once()
        self.wait(lambda: self.manager.alive)

    def test_a_taken_port_is_reported(self):
        def taken():
            raise OSError(98, 'Address already in use')
        eye = EyeTracking(self.manager, self.cameras, log=self.logs.append, relay_factory=taken)
        self.addCleanup(eye.shutdown, 3)
        with self.assertRaises(OSError):
            eye.begin('session', '/run/gaze.sock')
        self.assertEqual(eye.phase, 'error')
        self.assertIn('Port 9005', eye.message)
        self.assertEqual(eye.owners, {'session'}, 'a session keeps trying')
        with self.assertRaises(OSError):
            eye.begin('user')
        self.assertEqual(eye.owners, {'session'}, 'a failed Open leaves nothing to cancel')

    def test_status_counts_but_never_carries_gaze(self):
        self.eye.begin('session', '/run/gaze.sock')
        self.wait(lambda: self.manager.alive)
        status = self.eye.status()
        self.assertEqual(status['owners'], ['session'])
        self.assertEqual(status['baballonia']['state'], 'installed')
        text = json.dumps(status)
        for word in ('pitch', 'yaw', 'quaternion', 'VRG1'):
            self.assertNotIn(word, text)


import test_rc4_integration  # noqa: E402  (its test classes are reused here, not imported by name: they would run twice)
import test_session_lifecycle  # noqa: E402


class AppEyeTrackingTests(unittest.TestCase):
    def app(self, role='client'):
        if not hasattr(self, 'harness'):
            self.harness = test_session_lifecycle.SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
            self.harness.setUp()
            self.addCleanup(self.harness.doCleanups)
        return self.harness.app(role)

    def eye_app(self, installed=True, calibrated=True, headset=None):
        app = self.app()
        app.launch = Mock()
        app.client_runtime.headset = dict(headset or test_rc4_integration.BEYOND)
        app.eye.manager.installed = lambda: installed
        app.eye.manager.calibration = lambda: {'calibrated': calibrated, 'date': '2026-10-05' if calibrated else None}
        app.eye.begin = Mock(side_effect=lambda owner, target=None: app.eye.owners.add(owner))
        app.eye.end = Mock(side_effect=lambda owner: app.eye.owners.discard(owner))
        app.session_link.host_features = ['foveation', 'gaze-foveation', 'optical-centre', 'session-gaze']
        return app

    def test_start_runs_eye_tracking_and_follows_the_eyes_once_calibrated(self):
        app = self.eye_app()
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        socket_path = app.eye.begin.call_args.args[1]
        self.assertEqual(app.eye.begin.call_args.args[0], 'session')
        self.assertEqual(socket_path.name, 'gaze.sock')
        args = app.launch.call_args.args[0]
        self.assertEqual(args[args.index('--gaze-socket') + 1], str(socket_path))
        sent = app.session_link.start.call_args.args[0]
        self.assertTrue(sent['foveation']['gaze'])
        self.assertIs(sent['gaze'], True, 'an RC4.5 host turns its eye tracking on for this session')
        self.assertTrue(app.session_gaze)
        self.assertIn('the sharp region follows your eyes', ' '.join(app.lines))
        app.process = Mock(poll=Mock(return_value=0))
        app.action({'action': 'stop'})
        app.eye.end.assert_called_with('session')
        self.assertIsNone(app.runtime_directory)

    def test_until_calibrated_baballonia_runs_but_balanced_is_used(self):
        app = self.eye_app(calibrated=False)
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        app.eye.begin.assert_called_once()
        sent = app.session_link.start.call_args.args[0]
        self.assertFalse(sent['foveation']['gaze'])
        self.assertEqual(sent['foveation']['center_size'], [.55, .5], 'Balanced')
        self.assertIs(sent['gaze'], False)
        self.assertIn('not calibrated yet', ' '.join(app.lines))
        # Calibrated during the session: the next Start follows the eyes.
        app.running = lambda: True
        app.eye.manager.calibration = lambda: {'calibrated': True, 'date': '2026-10-05'}
        self.assertEqual(app.eye_state('balanced', None), 'restart')
        self.assertFalse(app.gaze_planned(), 'the running session keeps its choice')

    def test_older_hosts_never_see_the_field_and_eye_tracking_off_starts_nothing(self):
        app = self.eye_app()
        app.session_link.host_features = ['foveation', 'gaze-foveation']
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        self.assertNotIn('gaze', app.session_link.start.call_args.args[0])
        app = self.eye_app()
        app.action({'action': 'start', 'gaze': False})
        app.eye.begin.assert_not_called()
        self.assertNotIn('--gaze-socket', app.launch.call_args.args[0])
        self.assertIs(app.session_link.start.call_args.args[0]['gaze'], False)

    def test_eye_actions(self):
        app = self.eye_app(installed=False)
        app.action({'action': 'eye-tracking', 'enabled': True})
        self.assertTrue(json.loads(app.settings_path.read_text())['gaze'])
        with self.assertRaisesRegex(ValueError, 'Choose whether'):
            app.action({'action': 'eye-tracking', 'enabled': 'yes'})
        with self.assertRaisesRegex(ValueError, 'non-commercial'):
            app.action({'action': 'eye-setup'})
        app.eye.manager.install_async = Mock(return_value=True)
        app.action({'action': 'eye-setup', 'accept_licence': True})
        app.eye.manager.install_async.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'Set up eye tracking first'):
            app.action({'action': 'eye-open'})
        with self.assertRaisesRegex(ValueError, 'Set up eye tracking first'):
            app.action({'action': 'camera-start'})  # an RC4.4 page's Start eye cameras
        app.eye.manager.installed = lambda: True
        app.action({'action': 'eye-open'})
        app.eye.begin.assert_called_with('user')
        app.eye.close_baballonia = Mock()
        app.action({'action': 'eye-close'})
        app.eye.close_baballonia.assert_called_once()
        app.eye.status = Mock(return_value={'owners': ['user']})
        app.eye.manager.remove = Mock()
        with self.assertRaisesRegex(ValueError, 'Close Baballonia'):
            app.action({'action': 'eye-remove'})
        app.eye.status = Mock(return_value={'owners': []})
        app.action({'action': 'eye-remove'})
        app.eye.manager.remove.assert_called_once()
        app.running = lambda: True
        app.action({'action': 'eye-tracking', 'enabled': False})
        self.assertIn('applies at the next Start', app.lines[-1])

    def test_an_index_gets_no_eye_tracking(self):
        from headset_fixture import HEADSET
        app = self.eye_app(headset=HEADSET)
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        app.eye.end.assert_called_with('session')  # begun for an unknown headset, ended once it was an Index
        sent = app.session_link.start.call_args.args[0]
        self.assertIs(sent['gaze'], False)
        self.assertFalse(sent['foveation']['gaze'])
        self.assertIn('this headset has no eye cameras', ' '.join(app.lines))
        app.process = Mock(poll=Mock(return_value=None))
        self.assertEqual(app.eye_state('balanced', None), 'no-cameras')
        app.process = Mock(poll=Mock(return_value=0))
        app.action({'action': 'stop'})
        app.eye.begin.reset_mock()
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        app.eye.begin.assert_not_called()  # the last headset was an Index: not even begun

    def test_the_session_decides_for_the_headset_it_finds(self):
        from headset_fixture import HEADSET
        app = self.eye_app()  # a Beyond is connected...
        app.settings['cached_headset'] = dict(HEADSET)  # ...but the last session was with an Index
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        self.assertTrue(app.session_gaze)
        sent = app.session_link.start.call_args.args[0]
        self.assertTrue(sent['gaze'] and sent['foveation']['gaze'])
        app.process = Mock(poll=Mock(return_value=None))
        self.assertTrue(app.gaze_planned())
        self.assertNotEqual(app.eye_state('eye-tracked', None), 'restart')
        other = self.eye_app(headset=HEADSET)  # the last session was with a Beyond, an Index is connected
        other.settings['cached_headset'] = dict(test_rc4_integration.BEYOND)
        other.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        self.assertFalse(other.session_gaze)
        self.assertIs(other.session_link.start.call_args.args[0]['gaze'], False)
        other.process = Mock(poll=Mock(return_value=None))
        self.assertEqual(other.eye_state('balanced', None), 'no-cameras')

    def test_a_calibration_saved_while_the_session_starts_waits_for_the_next_start(self):
        # The host starts early from the last headset (the same Beyond) and is told the Start's choice; a
        # calibration finishing meanwhile (in the grace after the last session) must not make this PC claim
        # the sharp region follows the eyes while the host keeps it fixed.
        app = self.eye_app(calibrated=False)
        app.settings['cached_headset'] = dict(test_rc4_integration.BEYOND)
        calibrated = {'value': False}
        app.eye.manager.calibration = lambda: {'calibrated': calibrated['value'], 'date': None}
        host_started = threading.Event()
        sent = []
        app.session_link.start.side_effect = lambda preferences, **kwargs: (sent.append(preferences), host_started.set())

        def local_steamvr(*args, **kwargs):
            host_started.wait(5)
            calibrated['value'] = True  # saved while local SteamVR starts
        app.client_runtime.ensure.side_effect = local_steamvr
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        self.assertEqual(len(sent), 1, 'the same headset: the early host start stands')
        self.assertIs(sent[0]['gaze'], False)
        self.assertFalse(sent[0]['foveation']['gaze'])
        self.assertIs(app.session_gaze, False, 'this PC keeps the choice the host was told')
        app.process = Mock(poll=Mock(return_value=None))
        app.eye.phase = 'running'
        self.assertFalse(app.gaze_planned())
        link = {'host_features': app.session_link.host_features}
        family, preset, features, estimate, display = app.foveation_plan(link)
        self.assertFalse(app.foveation_status(link, family, preset, features, estimate, display)['gaze_follow'])
        self.assertEqual(app.eye_state(preset, {'mode': 'centred', 'gaze_known': True}), 'restart')

    def test_the_note_follows_the_switch_this_session_started_with(self):
        app = self.eye_app()
        app.action({'action': 'start', 'gaze': True, 'foveation': 'eye-tracked'})
        app.process = Mock(poll=Mock(return_value=None))
        app.eye.phase = 'running'
        app.settings['gaze'] = False  # turned off during the session: this session keeps its eye tracking
        self.assertEqual(app.eye_state('eye-tracked', {'mode': 'eyes', 'gaze_known': True}), 'following')
        other = self.eye_app()
        other.action({'action': 'start', 'gaze': False, 'foveation': 'eye-tracked'})
        other.process = Mock(poll=Mock(return_value=None))
        other.settings['gaze'] = True  # turned on during the session: from the next Start
        self.assertEqual(other.eye_state('balanced', None), 'restart')

    def test_status_reads_eye_tracking_outside_the_companion_lock(self):
        app = self.app()
        free = []

        def probe():
            def other():
                got = app.lock.acquire(timeout=1)
                free.append(got)
                if got:
                    app.lock.release()
            thread = threading.Thread(target=other)
            thread.start()
            thread.join()
            return {'phase': 'off'}
        app.eye_status = probe
        app.status()
        self.assertEqual(free, [True], 'eye tracking takes its own locks; its camera helper logs')

    def test_status_and_report_carry_eye_tracking_without_gaze(self):
        app = self.app()
        status = app.status()
        self.assertEqual(status['eye_tracking']['phase'], 'off')
        self.assertIn('licence', status['eye_tracking']['baballonia'])
        self.assertFalse(status['camera_running'])
        with patch('launcher.app.steamvr_logs', return_value={}):
            report = app.local_report()
        self.assertIn('eye_tracking', report)
        self.assertIn('baballonia_log', report)


class HostSessionGazeTests(unittest.TestCase):
    def setUp(self):
        self.harness = test_rc4_integration.HostIntegrationTests('test_host_writes_stream_options_and_keeps_the_pc_awake')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def host_app(self):
        return self.harness.host_app()

    def start(self, app, **extra):
        self.harness.start(app, **extra)

    def test_session_settings_take_a_gaze_switch(self):
        self.assertIs(session_settings({'gaze': True})['gaze'], True)
        self.assertNotIn('gaze', session_settings({}))
        with self.assertRaises(SessionError):
            session_settings({'gaze': 'yes'})

    def test_the_client_turns_host_eye_tracking_on_for_its_session_only(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['optical-centre'])
        self.start(app, gaze=True)
        saved = json.loads((app.host / 'session.json').read_text())['session_settings']
        self.assertTrue(saved['headset']['face_tracking']['enabled'])
        self.assertTrue(app.session_gaze_mark().is_file())
        app._host_settings_stamp = None
        app.refresh_host_settings()
        self.assertFalse(app.settings['gaze'], "not read back as this PC's own preference")
        self.assertIn('VRChat on this PC receives where you look', ' '.join(app.lines))
        app.stop_host_session()
        app.unpark()
        app.host_control.active = False
        self.start(app, gaze=False, keep_warm_minutes=0)
        saved = json.loads((app.host / 'session.json').read_text())['session_settings']
        self.assertFalse(saved['headset']['face_tracking']['enabled'])
        self.assertFalse(app.session_gaze_mark().exists())

    def test_quick_resume_restarts_steamvr_when_the_sessions_eye_tracking_changes(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['optical-centre'])
        self.start(app, gaze=False)  # kept open 15 minutes after Stop
        app.stop_host_session()
        self.assertIsNotNone(app.parked)
        self.start(app, gaze=True)  # calibrated meanwhile: VRChat's eye input needs SteamVR started again
        self.assertEqual(app.host_control.start.call_count, 2)
        saved = json.loads((app.host / 'session.json').read_text())['session_settings']
        self.assertTrue(saved['headset']['face_tracking']['enabled'])

    def test_the_session_only_note_outlives_an_upgrade(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['optical-centre'])
        self.start(app, gaze=True)
        self.assertEqual(app.session_gaze_mark().parent, app.log_path.parent, 'kept in the state folder')
        upgraded = self.harness.app('host')  # a new release reads the session.json carried over
        upgraded.host = app.host
        upgraded._host_settings_stamp = None
        upgraded.refresh_host_settings()
        self.assertFalse(upgraded.settings['gaze'])

    def test_the_hosts_own_switch_still_wins(self):
        app = self.host_app()
        app._features_cache = (time.monotonic(), ['optical-centre'])
        app.settings['gaze'] = True
        self.start(app, gaze=False)
        saved = json.loads((app.host / 'session.json').read_text())['session_settings']
        self.assertTrue(saved['headset']['face_tracking']['enabled'])
        self.assertFalse(app.session_gaze_mark().exists())
        app._host_settings_stamp = None
        app.refresh_host_settings()
        self.assertTrue(app.settings['gaze'])


class GameModeEyeTrackingTests(unittest.TestCase):
    def view(self, state):
        from launcher.game_ui import View
        view = View(ROOT, lambda: state, Mock(), Mock(), threading.Event())
        view.action = Mock()
        view.list_page = Mock()
        view.state = state
        return view

    def rows(self, view):
        view.page_eyetrack()
        return {row[0]: row for row in view.list_page.call_args.args[1]}

    def state(self, **baballonia):
        value = {'state': 'missing', 'version': '1.1.1.0rc6', 'size_mb': 517, 'running': False, 'calibrated': False,
                 'message': 'Not set up.'}
        value.update(baballonia)
        return {'settings': {'gaze': True}, 'running': False,
                'eye_tracking': {'phase': 'off', 'message': '', 'owners': [], 'camera_access': False,
                                 'relay': {'state': 'off'}, 'baballonia': value}}

    def test_set_up_with_the_licence_then_open_and_close(self):
        view = self.view(self.state())
        rows = self.rows(view)
        self.assertEqual(rows['toggle:gaze'][2], 'On')
        self.assertEqual(rows['eye-setup'][2], '517 MB download')
        self.assertIn('non-commercial', rows['eye-setup'][4])
        view.choose('eye-setup')
        self.assertEqual(view.action.call_args.kwargs, {'accept_licence': True})
        view.choose('toggle:gaze')
        self.assertEqual(view.action.call_args.args[0], 'eye-tracking')
        self.assertEqual(view.action.call_args.kwargs, {'enabled': False})
        rows = self.rows(self.view(self.state(state='downloading', progress=.42)))
        self.assertEqual(rows['eye-setup-cancel'][2], '42%')
        self.assertNotIn('eye-setup', rows)
        rows = self.rows(self.view(self.state(state='installed')))
        self.assertIn('eye-open', rows)
        self.assertEqual(rows['info:eye-calibration'][2], 'in Desktop Mode')
        self.assertIn('eye-remove', rows)
        state = self.state(state='installed', running=True, calibrated=True, date='2026-10-05')
        state['eye_tracking'].update(phase='running', relay={'state': 'receiving', 'rate': 29.6}, owners=['session'],
                                     camera_access=True)
        rows = self.rows(self.view(state))
        self.assertEqual(rows['info:eye-status'][2], 'running · 30 samples/s')
        self.assertEqual(rows['info:eye-calibration'][2], 'done 2026-10-05')
        self.assertIn('eye-close', rows)
        self.assertIn('eye-camera-access', rows)
        self.assertNotIn('eye-remove', rows)

    def test_a_finishing_calibration_and_a_password_prompt(self):
        state = self.state(state='installed', running=True)
        state['eye_tracking'].update(phase='finishing', owners=[])
        rows = self.rows(self.view(state))
        self.assertNotIn('eye-close', rows, 'it closes by itself once its calibration is saved')
        self.assertNotIn('eye-open', rows)
        self.assertEqual(rows['info:eye-status'][2], 'finishing calibration')
        state['eye_tracking'].update(phase='cameras', camera_access=True,
                                     access={'state': 'asking', 'message': 'Waiting for your password…'})
        rows = self.rows(self.view(state))
        self.assertEqual(rows['eye-camera-access'][2:4], ('asking…', False))
        state['eye_tracking']['access'] = {'state': 'error', 'message': 'Camera access was not allowed: the prompt was closed.'}
        rows = self.rows(self.view(state))
        self.assertTrue(rows['eye-camera-access'][4].startswith('Camera access was not allowed'))

    def test_the_page_opens_from_this_pc_and_from_the_stream_note(self):
        view = self.view(self.state(state='installed'))
        view.page = 'pc'
        view.choose('eyetrack')
        self.assertEqual(view.page, 'eyetrack')
        view.back()
        self.assertEqual(view.page, 'pc')
        view.page = 'stream'
        view.choose('eyetrack')
        view.back()
        self.assertEqual(view.page, 'stream')
        self.assertEqual(view.eye_summary(), 'On · not calibrated')
        view.state['settings']['gaze'] = False
        self.assertEqual(view.eye_summary(), 'Off')


class UninstallTests(unittest.TestCase):
    def test_the_downloaded_baballonia_goes_with_vrostbridge(self):
        from launcher import install_maintenance
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            (data / 'vrostbridge/baballonia/1.1.1.0rc6').mkdir(parents=True)
            (data / 'vrostbridge/cameras').mkdir()
            config = Path(directory) / 'config/ProjectBabble'
            config.mkdir(parents=True)
            with patch.dict('os.environ', {'XDG_DATA_HOME': str(data), 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                    patch('launcher.baballonia.BaballoniaManager.stop', return_value=False):
                install_maintenance.remove_eye_tracking()
            self.assertFalse((data / 'vrostbridge/baballonia').exists())
            self.assertFalse((data / 'vrostbridge/cameras').exists())
            self.assertTrue(config.is_dir(), 'its settings and calibration are the user\'s')


if __name__ == '__main__':
    unittest.main()
