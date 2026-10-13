"""1.0.0.3: fixes from the 1.0.0.2 test reports, on both programs (both are 1.0.0.3).

- Game Mode: the headset display reservation was always called stale. Bazzite's gamescope has a file capability
  (cap_sys_nice), so the process is non-dumpable and Linux refuses even its own user the link to its program
  (/proc/PID/exe); the check needed that link. It now identifies gamescope by what the user can read.
- Host: SteamVR started another vrserver during a session (after a 61.9 s start); the session ended with "A different
  SteamVR process appeared" and Stop refused to close SteamVR ("ownership changed"), so it had to be closed on the
  host. A new vrserver of this session's SteamVR now joins the session.
- The reduced edges of the foveated stream sparkled: the host decimated them without filtering (patch 0016: a mip
  chain and anisotropic sampling), and the client enlarged them with Catmull-Rom's overshoot (now a B-spline there).
- Eye tracking followed quick eye movements late: the eye-camera helper sent at most one frame per 50 ms (about 17 a
  second), and Baballonia's own filter smoothed heavily; the helper now sends up to about 66 a second and VROSTBridge
  sets Baballonia's eye filter once, unless it was changed.
- The eye-tracking view shows the eyes moving (twenty updates a second, gliding, with a trail) and the sharp region
  against the reduced area (fine and coarse squares).
- Baballonia set up by an earlier VROSTBridge keeps working when a later one pins another release, and the installer
  says the eye tracking set up before was found and kept.
- The headers (both pages and Game Mode) show the name in the logo's lettering on one line: VROST, then Bridge with
  a capital B; the line under it is unchanged.
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
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
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import baballonia, game_mode  # noqa: E402
from launcher.baballonia import BaballoniaManager  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_3_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1004.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 3))
        import build_distributions
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-%s.zip' % CLIENT_VERSION)
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-%s.exe' % HOST_VERSION)


# ---------------------------------------------------------------------------------------------------- Game Mode
def fake_process(proc, pid, started=4242, state='S', uids=None, argv=None):
    """A /proc/PID with the files gamescope_alive reads (stat, status, cmdline)."""
    directory = Path(proc) / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    fields = [state] + ['0'] * 18 + [str(started)] + ['0'] * 5
    (directory / 'stat').write_text('%d (gamescope) %s\n' % (pid, ' '.join(fields)))
    real, effective = uids or (os.getuid(), os.getuid())
    (directory / 'status').write_text('Name:\tgamescope\nState:\tS\nUid:\t%d\t%d\t%d\t%d\n' % (real, effective, effective, effective))
    argv = argv if argv is not None else ['/usr/bin/gamescope', '--lease-connector', 'DP-1', '--prefer-output', '*,DP-1',
                                          '--', 'steam']
    (directory / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
    return directory


def denied(path):
    raise PermissionError(13, 'Permission denied', str(path))


class GamescopeIdentityTests(unittest.TestCase):
    RECORD = {'pid': 321, 'started': 4242, 'connector': {'name': 'DP-1', 'id': 'card1-DP-1'}}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)

    def alive(self, record=None, readlink=denied):
        return game_mode.gamescope_alive(self.RECORD if record is None else record, proc=self.proc, readlink=readlink)

    def test_a_gamescope_with_its_file_capability_is_recognised(self):
        fake_process(self.proc, 321)
        self.assertTrue(self.alive(), "Bazzite's gamescope: its program link cannot be read")

    def test_a_readable_program_link_must_lead_to_gamescope(self):
        fake_process(self.proc, 321)
        self.assertTrue(self.alive(readlink=lambda path: '/usr/bin/gamescope'))
        self.assertTrue(self.alive(readlink=lambda path: '/usr/bin/gamescope (deleted)'), 'updated on disk while running')
        self.assertFalse(self.alive(readlink=lambda path: '/usr/bin/python3.13'))

    def test_another_process_is_not_the_lease_owner(self):
        fake_process(self.proc, 321, started=999)
        self.assertFalse(self.alive(), 'another start time: the PID was reused')
        fake_process(self.proc, 321, state='Z')
        self.assertFalse(self.alive(), 'a zombie')
        fake_process(self.proc, 321, uids=(os.getuid() + 1, os.getuid() + 1))
        self.assertFalse(self.alive(), "another user's process")
        fake_process(self.proc, 321, argv=['/usr/bin/gamescope', '--prefer-output', '*', '--', 'steam'])
        self.assertFalse(self.alive(), 'a gamescope without the headset lease')
        fake_process(self.proc, 321, argv=['/usr/bin/gamescope', '--lease-connector', 'DP-2', '--', 'steam'])
        self.assertFalse(self.alive(), 'another connector')
        fake_process(self.proc, 321, argv=['/usr/bin/kwin_wayland', '--lease-connector', 'DP-1'])
        self.assertFalse(self.alive(), 'not gamescope')

    def test_missing_or_broken_records(self):
        fake_process(self.proc, 321)
        for record in ({}, {'pid': 0}, {'pid': 321}, {'pid': 321, 'started': 4242}, {'pid': 'x', 'started': 4242},
                       dict(self.RECORD, connector='DP-1'), dict(self.RECORD, pid=322)):
            self.assertFalse(self.alive(record), record)

    def test_details_accepts_the_running_gamescope_and_names_a_stopped_one(self):
        runtime = self.proc / 'runtime'
        runtime.mkdir(mode=0o700)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.bind(str(runtime / 'gamescope-lease.sock'))
        row = {'id': 'card1-DP-1', 'name': 'DP-1', 'card': '/dev/dri/card1', 'connector_id': 135, 'edid_hex': '00' * 128}
        (runtime / 'gamescope-lease.json').write_text(json.dumps({'pid': 321, 'started': 4242, 'connector': row}))
        setup = game_mode.GameMode.__new__(game_mode.GameMode)
        setup.sysfs = self.proc
        setup.kernel = lambda fresh=False: {}
        with patch('launcher.game_mode.runtime_dir', return_value=runtime), \
                patch('launcher.game_mode.connectors', return_value=[dict(row, model='Bigscreen Beyond')]), \
                patch('launcher.game_mode.gamescope_alive', return_value=True) as alive:
            details = setup.details()
            self.assertEqual(details['lease_socket'], str(runtime / 'gamescope-lease.sock'))
            self.assertEqual(alive.call_args.args[0]['pid'], 321)
            alive.return_value = False
            with self.assertRaisesRegex(ValueError, 'gamescope that reserved the headset display is no longer running'):
                setup.details()

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which('setcap') and shutil.which('setpriv') and shutil.which('cc'),
                         'needs root, setcap, setpriv and a C compiler to start a program with a file capability')
    def test_a_real_program_with_a_file_capability(self):
        """As on Bazzite: a program with cap_sys_nice=eip, started by a user who then checks it. 1.0.0.2's check
        (process_identity) cannot read its program link; 1.0.0.3's recognises it."""
        work = self.proc / 'cap'
        work.mkdir()
        (work / 'fake.c').write_text('#include <unistd.h>\nint main(void){sleep(20);return 0;}\n')
        subprocess.run(['cc', '-O2', '-o', str(work / 'gamescope'), str(work / 'fake.c')], check=True)
        if subprocess.run(['setcap', 'cap_sys_nice=eip', str(work / 'gamescope')], capture_output=True).returncode:
            self.skipTest('this file system keeps no file capabilities')
        shutil.copytree(ROOT / 'launcher', work / 'repo' / 'launcher', ignore=shutil.ignore_patterns('__pycache__'))
        check = work / 'check.py'
        check.write_text('''import json, os, sys, time
sys.path.insert(0, sys.argv[2])
from launcher.client_runtime import process_identity
from launcher.game_mode import gamescope_alive
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    os.write(w, json.dumps({'pid': os.getpid(), 'started': process_identity(os.getpid())['started']}).encode())
    os.close(w)
    os.execv(sys.argv[1], ['/usr/bin/gamescope', '--lease-connector', 'DP-1', '--', 'steam'])
os.close(w)
record = json.loads(os.read(r, 4096))
record['connector'] = {'name': 'DP-1'}
time.sleep(.5)
result = {'old': process_identity(pid) is not None, 'new': gamescope_alive(record)}
try:
    os.readlink('/proc/%d/exe' % pid)
    result['link'] = 'readable'
except PermissionError:
    result['link'] = 'denied'
os.kill(pid, 9)
os.waitpid(pid, 0)
result['after'] = gamescope_alive(record)
print(json.dumps(result))
''')
        Path(self.temp.name).chmod(0o755)  # the other user must reach the program and the check
        for path in [self.proc, work, *work.rglob('*')]:
            path.chmod(path.stat().st_mode | 0o055)
        result = subprocess.run(['setpriv', '--reuid=65534', '--regid=65534', '--clear-groups', sys.executable, '-I',
                                 str(check), str(work / 'gamescope'), str(work / 'repo')],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            self.skipTest('the check could not run as another user: ' + result.stderr[-300:])
        value = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(value['link'], 'denied', 'a capability program is non-dumpable')
        self.assertFalse(value['old'], "1.0.0.2's check: every Game Mode reservation looked stale")
        self.assertTrue(value['new'])
        self.assertFalse(value['after'])

    def test_the_wrapper_still_records_its_own_start(self):
        source = read('launcher/game_mode.py')
        self.assertIn("proc=process_identity(os.getpid());atomic(d/'gamescope-lease.json'", source)
        self.assertIn("leased=['/usr/bin/gamescope','--lease-connector',row['name'],*arguments]", source)
        self.assertNotIn("owner['path']!=str(Path('/usr/bin/gamescope').resolve())", source)


# ---------------------------------------------------------------------------------------------------- host
from host import control as host_control  # noqa: E402
from test_host_control import DriverFixture  # noqa: E402


class HostRestartTests(unittest.TestCase):
    """A vrserver that this session's SteamVR starts joins the session; a foreign one still does not."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.path = directory / 'session.json'
        self.driver = DriverFixture(self.path)
        self.addCleanup(self.driver.close)
        self.document = {'server_version': '21.0.0-dev14', 'restart_settings_hash': 1,
                         'session_settings': {'connection': {'web_server_port': self.driver.server.server_port}},
                         'client_connections': {}}
        self.path.write_text(json.dumps(self.document))
        self.logs = []
        self.runtime = directory / 'SteamVR/bin/win64'
        self.runtime.mkdir(parents=True)
        self.pids = set()
        self.control = host_control.HostControl(directory, self.logs.append, self.launch, lambda: set(self.pids))
        self.identities = {}
        self.family = {'vrcompositor.exe': set(), 'vrmonitor.exe': set(), 'vrstartup.exe': set()}
        for name, value in (('windows', lambda: True), ('steamvr_startup', lambda: self.runtime / 'vrstartup.exe'),
                            ('windows_runtime_process_ids', lambda: dict(self.family, **{'vrserver.exe': set(self.pids)})),
                            ('identify', lambda pid: self.identities.get(pid)), ('terminate', self.terminate)):
            patcher = patch.object(host_control, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.stop)

    def stop(self):
        if self.control.active:
            try:
                self.control.stop()
            except ValueError:
                pass

    def created(self):
        return int((time.time() + 11644473600) * 10000000)

    def process(self, pid, name, path=None):
        self.identities[pid] = {'pid': pid, 'path': str(path or self.runtime / name), 'created': self.created()}
        if name == 'vrserver.exe':
            self.pids.add(pid)
        else:
            self.family[name].add(pid)

    def launch(self):
        self.driver.launch()
        self.process(101, 'vrserver.exe')
        self.process(102, 'vrcompositor.exe')

    def terminate(self, identity):
        self.pids.discard(identity['pid'])
        for pids in self.family.values():
            pids.discard(identity['pid'])
        self.identities.pop(identity['pid'], None)

    def wait_for(self, condition, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.02)
        self.fail('Timed out: ' + json.dumps(self.control.snapshot(), default=str)[:600] + ' ' + str(self.logs[-5:]))

    def ready(self):
        return self.control.snapshot()['runtime_state'] == 'ready'

    def test_a_second_vrserver_of_this_steamvr_joins_the_session_and_stop_closes_it(self):
        self.control.start()
        self.wait_for(self.ready)
        self.process(103, 'vrserver.exe')  # as in the 1.0.0.2 report, 3 s after a slow start
        self.wait_for(lambda: any('PID 103' in line for line in self.logs))
        time.sleep(1.0)
        self.assertEqual(self.control.snapshot()['runtime_state'], 'ready', self.logs[-3:])
        self.assertIn(103, self.control.identities)

        def shutdown(*args, **kwargs):  # SteamVR's graceful exit ends all its processes
            for pid in list(self.identities):
                self.terminate(self.identities[pid])
        with patch.object(self.control, 'SHUTDOWN_TIMEOUT', 3):
            original = self.control.api.request

            def request(endpoint, payload=None, post=False):
                result = original(endpoint, payload, post)
                if endpoint == 'steamvr/shutdown':
                    shutdown()
                return result
            self.control.api.request = request
            self.control.stop()
        self.assertEqual(self.control.snapshot()['runtime_state'], 'stopped')
        self.assertFalse(self.pids)
        self.assertFalse(any('ownership changed' in line or 'different SteamVR' in line for line in self.logs), self.logs)

    def test_a_foreign_vrserver_still_ends_the_session_and_is_not_stopped(self):
        self.control.start()
        self.wait_for(self.ready)
        self.process(104, 'vrserver.exe', path=Path(self.temporary.name) / 'Other/bin/win64/vrserver.exe')
        self.wait_for(lambda: self.control.snapshot()['runtime_state'] == 'error')
        self.assertIn('did not start', self.control.snapshot()['error'])
        with self.assertRaisesRegex(ValueError, 'ownership changed'):
            self.control.stop()
        self.assertIn(104, self.pids, 'never stopped')

    def test_a_vrserver_after_this_steamvr_ended_is_not_adopted(self):
        self.control.owned_pids = {101}
        self.control.runtime_observed = True
        self.control.launch_created = self.created() - 10
        self.process(105, 'vrserver.exe')
        self.assertEqual(self.control._adopt_new_servers({105}), {105}, 'none of this session runs, no loss pending')
        self.control.loss_pending = True
        self.assertEqual(self.control._adopt_new_servers({105}), set(), 'SteamVR restarting itself')

    def test_starting_is_not_called_an_interruption_and_a_slow_start_is_explained(self):
        original = self.launch

        def slow_launch():  # vrserver runs at once, the driver answers later (61.9 s in the 1.0.0.2 report)
            original()
            self.driver.active = False
        self.control.launch = slow_launch
        self.control.SLOW_START_S = .3
        self.control.start()
        self.wait_for(lambda: any('has been starting for' in line for line in self.logs))
        self.driver.active = True
        self.wait_for(self.ready)
        self.assertFalse(any('interrupted' in line for line in self.logs), self.logs)
        self.driver.active = False  # now it is an interruption
        self.wait_for(lambda: any('Host runtime interrupted' in line for line in self.logs))
        self.driver.active = True
        self.wait_for(self.ready)


# ---------------------------------------------------------------------------------------------------- reports
class ReportTests(unittest.TestCase):
    def test_steamvr_logs_include_the_previous_start_and_the_monitor(self):
        from launcher import diagnostics
        with tempfile.TemporaryDirectory() as home:
            logs = Path(home) / '.local/share/Steam/logs'
            logs.mkdir(parents=True)
            for name in ('vrserver.txt', 'vrserver.previous.txt', 'vrmonitor.txt', 'vrcompositor.txt'):
                (logs / name).write_text(name + ' line\n')
            (logs / 'vrserver.previous.txt').write_text('x' * 200000)
            with patch('launcher.diagnostics.Path.home', return_value=Path(home)):
                result = diagnostics.steamvr_logs()
        self.assertEqual(set(result), {'vrserver.txt', 'vrserver.previous.txt', 'vrmonitor.txt', 'vrcompositor.txt'})
        self.assertLessEqual(len(result['vrserver.previous.txt']), 64 * 1024)

    def test_a_refused_client_report_is_said_once(self):
        from launcher import diagnostics
        import urllib.error
        remote = diagnostics.RemoteDiagnostics.__new__(diagnostics.RemoteDiagnostics)
        remote.lock = threading.Lock()
        remote.capture_lock = threading.Lock()
        remote.log = Mock()
        remote.directory = Path(tempfile.gettempdir())
        remote.connection_key, remote.full_key, remote.report_received, remote.refused_key = 'lease', None, 0, None
        remote.last_ip, remote.summary, remote.full, remote.message, remote.last_seen = '192.168.1.5', {}, {}, '', 0
        remote.candidate = lambda: (None, None)
        remote.fetch = Mock(side_effect=urllib.error.HTTPError('http://x/report', 403, 'Forbidden', {}, io.BytesIO()))
        for _ in range(3):
            remote.capture()
        self.assertEqual(remote.log.call_count, 1)
        self.assertIn('only during a session and for a minute after', remote.log.call_args.args[0])
        remote.fetch.side_effect = OSError('timed out')
        remote.capture()
        self.assertIn('Client report collection: timed out', remote.log.call_args.args[0])


# ---------------------------------------------------------------------------------------------------- Baballonia
RELEASE = {'version': '9.9.9', 'url': 'https://example.invalid/b.tar.xz', 'size': 10, 'sha256': 'a' * 64,
           'extracted_mb': 1, 'capture': 'IpCameraCapture'}


class BaballoniaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data/vrostbridge/baballonia'
        self.config = Path(self.temp.name) / 'config/ProjectBabble'

    def manager(self, release=RELEASE):
        manager = BaballoniaManager(log=Mock(), root=self.root, config=self.config, state=Path(self.temp.name) / 'setup.json',
                                    release=release, vulkan_layer=None)
        manager.running = lambda *args, **kwargs: False
        return manager

    def install(self, version, sha256, capture=None):
        folder = self.root / version
        folder.mkdir(parents=True, exist_ok=True)
        (folder / baballonia.EXECUTABLE).write_text('#!/bin/sh\n')
        receipt = {'version': version, 'url': 'https://example.invalid/' + version, 'sha256': sha256}
        if capture:
            receipt['capture'] = capture
        (folder / 'vrostbridge-receipt.json').write_text(json.dumps(receipt))
        (folder / 'LocalSettings.json').write_text(json.dumps(dict(baballonia.BABALLONIA_EYE_FILTER)))
        return folder

    def test_versions_sort_with_release_candidates_first(self):
        key = baballonia.version_key
        self.assertLess(key('1.1.1.0rc6'), key('1.1.1.0'))
        self.assertLess(key('1.1.1.0rc6'), key('1.1.1.0rc10'))
        self.assertLess(key('1.1.1.0'), key('1.2.0.0'))
        self.assertGreater(key('1.1.1.0rc6'), key('1.1.0.9'))

    def test_the_pinned_release_set_up_is_used(self):
        self.install('9.9.9', 'a' * 64)
        manager = self.manager()
        self.assertTrue(manager.installed())
        self.assertFalse(manager.update_available())
        self.assertEqual(manager.release['version'], '9.9.9')
        self.assertIsNone(manager.status(running=False)['update'])

    def test_an_earlier_vrostbridges_baballonia_keeps_working_until_it_is_updated(self):
        self.install('9.9.8', 'b' * 64, capture='OldCapture')
        manager = self.manager()
        self.assertTrue(manager.installed(), 'an update of VROSTBridge does not ask to set eye tracking up again')
        self.assertTrue(manager.update_available())
        self.assertEqual((manager.release['version'], manager.release['capture']), ('9.9.8', 'OldCapture'))
        self.assertEqual(manager.install_dir, self.root / '9.9.8')
        status = manager.status(running=False)
        self.assertEqual((status['state'], status['update']), ('installed', '9.9.9'))
        self.assertIn('Baballonia 9.9.8 is set up', status['message'])
        self.assertIn('9.9.9 is available', status['message'])
        # The update downloads the pinned release; afterwards it is the one in use.
        manager.install = Mock(side_effect=lambda: self.install('9.9.9', 'a' * 64))
        self.assertTrue(manager.install_async())
        manager.job.join(5)
        self.assertEqual(manager.release['version'], '9.9.9')
        self.assertFalse(manager.update_available())
        self.assertIn('9.9.9 set up in', manager.log.call_args_list[-1].args[0])

    def test_an_update_waits_for_baballonia_to_close(self):
        self.install('9.9.8', 'b' * 64)
        manager = self.manager()
        manager.running = lambda *args, **kwargs: True
        with self.assertRaisesRegex(ValueError, 'Close Baballonia'):
            manager.install_async()

    def test_unrecognised_folders_are_not_used(self):
        folder = self.install('9.9.7', 'c' * 64)
        (folder / 'vrostbridge-receipt.json').write_text(json.dumps({'version': 'other', 'sha256': 'c' * 64}))
        broken = self.install('9.9.6', 'short')
        self.assertTrue(broken.is_dir())
        (self.root / '.unpack-x').mkdir()
        manager = self.manager()
        self.assertIsNone(manager.earlier())
        self.assertFalse(manager.installed())
        self.assertEqual(manager.status(running=False)['state'], 'missing')

    def configure(self, manager, settings=None):
        if settings is not None:
            manager.settings_path.parent.mkdir(parents=True, exist_ok=True)
            manager.settings_path.write_text(json.dumps(settings))
        manager.configure()
        return json.loads(manager.settings_path.read_text())

    def test_the_eye_filter_is_set_once_while_it_is_baballonias_own(self):
        self.install('9.9.9', 'a' * 64)
        manager = self.manager()
        settings = self.configure(manager)
        for key, value in baballonia.EYE_FILTER.items():
            self.assertEqual(settings[key], value, key)
        self.assertTrue(any('eye filter set to follow quick eye movements' in call.args[0] for call in manager.log.call_args_list))
        # Changed in Baballonia afterwards: kept.
        settings['AppSettings_OneEuroSpeedCutoff'] = 2.0
        settings = self.configure(manager, settings)
        self.assertEqual(settings['AppSettings_OneEuroSpeedCutoff'], 2.0)

    def test_a_filter_someone_chose_is_kept(self):
        self.install('9.9.9', 'a' * 64)
        manager = self.manager()
        chosen = dict(baballonia.BABALLONIA_EYE_FILTER, AppSettings_OneEuroMinFreqCutoff=2.0)
        settings = self.configure(manager, chosen)
        self.assertEqual(settings['AppSettings_OneEuroMinFreqCutoff'], 2.0)
        self.assertEqual(settings['AppSettings_OneEuroSpeedCutoff'], 3.0)
        self.assertTrue(json.loads((Path(self.temp.name) / 'setup.json').read_text())['eye_filter_set'])

    def test_the_filter_follows_quick_movements_and_stays_steady(self):
        """Baballonia's One Euro filter, simulated as Baballonia computes it."""
        import math
        import random

        def filtered(samples, rate, low, speed):
            dt, x, dx, out = 1 / rate, samples[0], 0.0, []
            for value in samples:
                r_d = 2 * math.pi * dt
                dx += (r_d / (r_d + 1)) * ((value - x) / dt - dx)
                r = 2 * math.pi * (low + speed * abs(dx)) * dt
                x += (r / (r + 1)) * (value - x)
                out.append(x)
            return out

        def lag(degrees, rate, low, speed):
            step = degrees / 90  # Baballonia's raw units: 0..1 for its +-45 degrees
            samples = [0.0] * 10 + [step] * int(rate)
            out = filtered(samples, rate, low, speed)
            return next(i - 9 for i in range(10, len(out)) if out[i] >= .9 * step) / rate * 1000

        def jitter(rate, low, speed):  # one degree of noise per sample, while the eyes hold still
            values = []
            for seed in range(5):
                random.seed(seed)
                out = filtered([random.gauss(0, 1 / 90) for _ in range(int(60 * rate))], rate, low, speed)[int(2 * rate):]
                values.append(90 * math.sqrt(sum(v * v for v in out) / len(out)))
            return sum(values) / len(values)

        old = baballonia.BABALLONIA_EYE_FILTER
        new = baballonia.EYE_FILTER
        before = [lag(d, 17, old['AppSettings_OneEuroMinFreqCutoff'], old['AppSettings_OneEuroSpeedCutoff']) for d in (5, 10)]
        after = [lag(d, 60, new['AppSettings_OneEuroMinFreqCutoff'], new['AppSettings_OneEuroSpeedCutoff']) for d in (5, 10)]
        self.assertGreater(min(before), 200)
        self.assertLess(max(after), 100, after)
        # 1.0.0.5 (filter 2): two camera frames for 5 and 10 degrees, against 67-83 ms with 1.0.0.3's 1 Hz and 10.
        self.assertLessEqual(max(after), 34, after)
        self.assertGreater(min(lag(d, 60, 1.0, 10.0) for d in (5, 10)), 60)
        # More of the tracker's noise passes while the eyes rest (the host's comfort zone absorbs it), within bounds.
        self.assertLess(jitter(60, new['AppSettings_OneEuroMinFreqCutoff'], new['AppSettings_OneEuroSpeedCutoff']), .65)
        self.assertGreater(jitter(60, new['AppSettings_OneEuroMinFreqCutoff'], new['AppSettings_OneEuroSpeedCutoff']),
                           jitter(60, 1.0, 10.0))

    def test_the_installer_says_the_eye_tracking_was_kept(self):
        from launcher import install_maintenance
        data = Path(self.temp.name) / 'data'  # self.root is data/vrostbridge/baballonia
        self.install(baballonia.RELEASE['version'], baballonia.RELEASE['sha256'])
        model = self.config / 'Models/tuned_temporal_eye_tracking_20261006_200454.onnx'
        model.parent.mkdir(parents=True)
        model.write_text('model')
        (self.config / 'ApplicationData').mkdir(parents=True)
        (self.config / 'ApplicationData/LocalSettings.json').write_text(json.dumps({'EyeHome_EyeModel': str(model)}))
        environment = {'XDG_DATA_HOME': str(data), 'XDG_CONFIG_HOME': str(self.config.parent)}
        with patch.dict(os.environ, environment), patch('sys.stdout', new_callable=io.StringIO) as out:
            install_maintenance.main(['eye-status', '--target', str(data)])
        self.assertEqual(out.getvalue().strip(), 'Eye tracking: Baballonia ' + baballonia.RELEASE['version'] +
                         ' found and kept, calibrated 2026-10-06.')
        # A copy an earlier installation left under another folder name is moved into place; the message does not name it.
        shutil.move(str(data / 'vrostbridge/baballonia'), str(data / 'earlier-name'))
        (data / 'earlier-name/baballonia').mkdir()
        shutil.move(str(data / 'earlier-name' / baballonia.RELEASE['version']), str(data / 'earlier-name/baballonia'))
        with patch.dict(os.environ, environment), patch('sys.stdout', new_callable=io.StringIO) as out:
            install_maintenance.main(['eye-status', '--target', str(data)])
        self.assertEqual(out.getvalue().strip(), 'Eye tracking: Baballonia ' + baballonia.RELEASE['version'] +
                         ' found where an earlier installation left it and moved into place, calibrated 2026-10-06.')
        self.assertTrue((data / 'vrostbridge/baballonia' / baballonia.RELEASE['version']).is_dir())
        shutil.rmtree(data / 'vrostbridge/baballonia')
        with patch.dict(os.environ, environment), patch('sys.stdout', new_callable=io.StringIO) as out:
            install_maintenance.main(['eye-status', '--target', str(data)])
        self.assertEqual(out.getvalue().strip(), 'Eye tracking: not set up yet (Eye tracking card).')
        script = read('packaging/install-bazzite.sh')
        self.assertIn('--installer-maintenance eye-status --target "$release"', script)
        self.assertIn('${eye:+ $eye}', script)

    def test_a_copy_an_earlier_installation_left_elsewhere_is_moved_here(self):
        data = Path(self.temp.name) / 'data'
        other = data / 'earlier' / 'baballonia' / RELEASE['version']
        other.mkdir(parents=True)
        (other / baballonia.EXECUTABLE).write_text('#!/bin/sh\n')
        (other / 'Calibration').mkdir()
        (other / 'earlier-receipt.json').write_text(json.dumps({'version': RELEASE['version'], 'sha256': RELEASE['sha256'],
                                                                'url': RELEASE['url']}))
        manager = self.manager()
        self.assertFalse(manager.installed())
        self.assertEqual(manager.adopt_earlier_copy(), other)
        self.assertTrue(manager.installed())
        target = self.root / RELEASE['version']
        self.assertTrue((target / 'Calibration').is_dir())
        self.assertFalse(other.exists(), 'moved, not copied')
        self.assertEqual(sorted(path.name for path in target.glob('*-receipt.json')), ['vrostbridge-receipt.json'])
        receipt = json.loads((target / 'vrostbridge-receipt.json').read_text())
        self.assertEqual((receipt['sha256'], receipt['capture']), (RELEASE['sha256'], RELEASE['capture']))
        self.assertIn('nothing was downloaded', manager.log.call_args.args[0])
        self.assertNotIn('earlier/', manager.log.call_args.args[0], 'the folder (with its earlier name) is not named')
        self.assertIsNone(manager.adopt_earlier_copy(), 'once set up, nothing more is looked for')

    def test_only_a_copy_of_the_pinned_release_with_its_receipt_is_taken(self):
        data = Path(self.temp.name) / 'data'
        for name, receipt in (('a', {'version': RELEASE['version'], 'sha256': 'f' * 64}),
                              ('b', {'version': '0.1', 'sha256': RELEASE['sha256']}), ('c', None)):
            folder = data / name / 'baballonia' / RELEASE['version']
            folder.mkdir(parents=True)
            (folder / baballonia.EXECUTABLE).write_text('#!/bin/sh\n')
            if receipt:
                (folder / (name + '-receipt.json')).write_text(json.dumps(receipt))
        real = data / 'd' / 'baballonia' / RELEASE['version']
        real.mkdir(parents=True)
        (real / 'd-receipt.json').write_text(json.dumps({'version': RELEASE['version'], 'sha256': RELEASE['sha256']}))
        (data / 'e' / 'baballonia').mkdir(parents=True)
        (data / 'e' / 'baballonia' / RELEASE['version']).symlink_to(real)
        manager = self.manager()
        self.assertIsNone(manager.adopt_earlier_copy(), 'other release, other version, no receipt, no program, a link')
        self.assertFalse(manager.installed())

    def test_the_eye_tracking_is_kept_on_uninstall_unless_removing_it_is_chosen(self):
        from launcher import install_maintenance
        with patch.object(install_maintenance, 'uninstall_client') as uninstall:
            install_maintenance.main(['uninstall-client', '--target', '/tmp/x', '--keep-eye-tracking'])
            install_maintenance.main(['uninstall-client', '--target', '/tmp/x'])
        self.assertEqual([call.kwargs for call in uninstall.call_args_list],
                         [{'keep_eye_tracking': True}, {'keep_eye_tracking': False}])
        source = read('launcher/install_maintenance.py')
        self.assertIn("        if keep_eye_tracking:\n", source)
        script = read('packaging/uninstall-bazzite.sh')
        self.assertIn('keep=(--keep-eye-tracking)', script)
        self.assertIn('--yes-label Keep --no-label Remove --cancel-label Cancel --yesnocancel "$question" || rc=$?', script)
        self.assertIn('case "$rc" in 0) choice=keep ;; 1) choice=remove ;; *) exit 0 ;; esac', script)
        self.assertIn('--ok-label Keep --cancel-label Cancel --extra-button Remove --text "$question") || rc=$?', script)
        self.assertIn('${keep[@]+"${keep[@]}"}', script)

    def test_the_uninstaller_keeps_baballonia_when_nothing_is_asked(self):
        """The script itself, run without a dialog program: kept by default, removed when asked to."""
        if not shutil.which('bash'):
            self.skipTest('bash is missing')
        work = Path(self.temp.name) / 'uninstall'
        work.mkdir()
        shutil.copy(ROOT / 'packaging/uninstall-bazzite.sh', work / 'uninstall.sh')
        (work / 'VROSTBridge-Client').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/arguments"\n')
        (work / 'VROSTBridge-Client').chmod(0o755)
        data = Path(self.temp.name) / 'data'
        (data / 'vrostbridge/baballonia/1.1.1.0rc6').mkdir(parents=True)
        path = '/usr/bin:/bin'  # no kdialog or zenity here
        for value, expected in (('1', True), ('0', False)):
            environment = {'PATH': path, 'HOME': self.temp.name, 'XDG_DATA_HOME': str(data),
                           'VROSTBRIDGE_UNINSTALL_CONFIRMED': '1', 'VROSTBRIDGE_KEEP_EYE_TRACKING': value}
            if any((Path(directory) / tool).exists() for directory in path.split(':') for tool in ('kdialog', 'zenity')):
                self.skipTest('a dialog program is installed here')
            result = subprocess.run(['bash', str(work / 'uninstall.sh')], env=environment, capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = (work / 'arguments').read_text().split()
            self.assertEqual('--keep-eye-tracking' in arguments, expected, arguments)
            self.assertEqual('was kept for the next installation' in result.stdout, expected, result.stdout)

    def test_the_uninstallers_question_with_each_dialog_program(self):
        """kdialog: Keep (0) keeps, Remove (1) removes, Cancel (2) stops the uninstall; zenity likewise."""
        work = Path(self.temp.name) / 'dialogs'
        bin_dir = work / 'bin'
        bin_dir.mkdir(parents=True)
        shutil.copy(ROOT / 'packaging/uninstall-bazzite.sh', work / 'uninstall.sh')
        (work / 'VROSTBridge-Client').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/arguments"\n')
        (work / 'VROSTBridge-Client').chmod(0o755)
        data = Path(self.temp.name) / 'data'
        (data / 'vrostbridge/baballonia/1.1.1.0rc6').mkdir(parents=True)
        (bin_dir / 'kdialog').write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = --yesnocancel ] && exit "$FAKE_RC"; done\nexit 0\n')
        (bin_dir / 'zenity').write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = --extra-button ] && { printf "%s" "$FAKE_OUT"; '
                                        'exit "$FAKE_RC"; }; done\nexit 0\n')
        for name in ('kdialog', 'zenity'):
            (bin_dir / name).chmod(0o755)
        cases = [('kdialog', '0', '', 'keep'), ('kdialog', '1', '', 'remove'), ('kdialog', '2', '', 'cancel'),
                 ('zenity', '0', '', 'keep'), ('zenity', '1', 'Remove', 'remove'), ('zenity', '1', '', 'cancel')]
        for program, rc, out, expected in cases:
            other = bin_dir / ('zenity' if program == 'kdialog' else 'kdialog')
            hidden = other.with_suffix('.off')
            other.rename(hidden)
            try:
                (work / 'arguments').unlink(missing_ok=True)
                environment = {'PATH': str(bin_dir) + ':/usr/bin:/bin', 'HOME': self.temp.name, 'XDG_DATA_HOME': str(data),
                               'FAKE_RC': rc, 'FAKE_OUT': out}
                result = subprocess.run(['bash', str(work / 'uninstall.sh')], env=environment, capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, (program, rc, result.stderr))
                if expected == 'cancel':
                    self.assertFalse((work / 'arguments').exists(), (program, rc, 'nothing is uninstalled'))
                    continue
                arguments = (work / 'arguments').read_text().split()
                self.assertEqual('--keep-eye-tracking' in arguments, expected == 'keep', (program, rc, arguments))
            finally:
                hidden.rename(other)

    def test_the_pages_offer_the_update(self):
        page = read('launcher/desktop.html')
        self.assertIn("const update=b.state==='installed'&&!!b.update;", page)
        self.assertIn('Accept licence and update to ${b.update}', page)
        game = read('launcher/game_ui.py')
        self.assertIn("'Update Baballonia'", game)
        app = read('launcher/app.py')
        self.assertIn("'updating Baballonia to ' if update else 'setting up Baballonia '", app)


# ---------------------------------------------------------------------------------------------------- foveation
class SharperPeripheryTests(unittest.TestCase):
    def test_the_host_filters_the_reduced_edges(self):
        patch_text = read('patches/0016-host-foveation-prefilter.patch')
        for needle in ('compositionTextureDesc.MipLevels = 0;', 'D3D11_RESOURCE_MISC_GENERATE_MIPS',
                       'GenerateMips(m_compositionResourceView.Get())', 'sampler.Filter = D3D11_FILTER_ANISOTROPIC;',
                       'sampler.AddressU = D3D11_TEXTURE_ADDRESS_CLAMP;', 'PSGetSamplers(0, 1',
                       'VROST_FOVEATION_PREFILTER composition_mips=%d', 'Settings_Instance()->m_enableFoveatedEncoding\n'):
            self.assertIn(needle, patch_text)
        names = sorted(path.name for path in (ROOT / 'patches').glob('00*.patch'))
        self.assertEqual(names[15], '0016-host-foveation-prefilter.patch')  # later patches follow (1.0.0.4: 0017)

    def test_the_client_smooths_the_enlarged_edges(self):
        shader = read('client/platform/linux/fused.comp')
        # 1.0.0.11: the texture size comes from the frame's constants (f.sizes), not a parameter.
        self.assertIn('float lumaBSpline(vec2 pixel,vec2 lo,vec2 hi){', shader)
        self.assertIn('float y=detail>=1.?lumaCatmullRom(pixel,lo,hi):detail<=0.?lumaBSpline(pixel,lo,hi):', shader)
        model = read('tests/fused_shader_tests.cpp')
        self.assertIn('float bSpline(const Plane &luma', model)
        self.assertIn('"reduced edges: moving detail and a step"', model)


class GazeSpeedTests(unittest.TestCase):
    def test_the_camera_helper_sends_up_to_66_frames_a_second(self):
        # 1.0.0.3 sent up to 66 a second (15 ms); 1.0.0.5 up to 125 (8 ms): tests/test_vrostbridge_1005.py.
        source = read('third_party/go-bsb-cams/mjpeg_stream.go')
        interval = re.search(r'const frameInterval = (\d+) \* time\.Millisecond', source)
        self.assertIsNotNone(interval)
        self.assertLessEqual(int(interval.group(1)), 15)
        self.assertIn('if sequence == sent || part == nil {', source)
        self.assertIn('1.0.0.3', read('third_party/go-bsb-cams/VROSTBRIDGE_CHANGES.md'))
        tests = read('third_party/go-bsb-cams/mjpeg_stream_test.go')
        for name in ('TestMJPEGEveryFrameOnceInOrder', 'TestMJPEGTwoClientsAndDisconnect',
                     'TestMJPEGFrameReachesClientWithoutNextFrame'):
            self.assertIn('func ' + name + '(', tests)

    @unittest.skipUnless(shutil.which('go') or Path('/usr/local/go/bin/go').is_file(), 'Go is not installed')
    def test_the_camera_helpers_stream_tests(self):
        go = shutil.which('go') or '/usr/local/go/bin/go'
        if subprocess.run(['pkg-config', '--exists', 'libusb-1.0']).returncode:
            self.skipTest('libusb-1.0 development files are missing')
        result = subprocess.run([go, 'test', '-mod=vendor', '-vet=off', '-count=1', '-run', 'MJPEG', '.'],
                                cwd=ROOT / 'third_party/go-bsb-cams', capture_output=True, text=True, timeout=600,
                                env=dict(os.environ, CGO_ENABLED='1'))
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])


class EyeViewTests(unittest.TestCase):
    def test_the_view_is_updated_often(self):
        self.assertIn('now - lastFovea >= 33000000ULL', read('client/platform/linux/main.cpp'))
        self.assertIn('while not self.closing.wait(.1):', read('launcher/app.py'))
        page = read('launcher/desktop.html')
        for needle in ('foveaTimer=setInterval(foveaTick,50);foveaFrame=requestAnimationFrame(foveaDraw);',
                       'const FOVEA_TRAIL_MS=1000;', 'function foveaPattern(', 'Reduced outside (coarser squares'):
            self.assertIn(needle, page)
        self.assertIn("next_paint = now + (.05 if self.page == 'eyes' else .5)", read('launcher/game_ui.py'))

    def test_game_mode_draws_the_trail_squares_and_glides(self):
        from launcher import game_ui
        view = game_ui.View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.s, view.paint, view.renderer = Mock(), Mock(), None
        view.label, view.button = Mock(), Mock()
        view.X = view.Y = view.u = lambda value: value
        view.state = {'running': True}
        samples = [{'live': True, 'region': [.4, .4], 'edge': [4.0, 4.0], 'left': [.5, .5], 'right': [.5, .5],
                    'gaze_left': [.5 + .1 * i, .5], 'gaze_right': [.5 + .1 * i, .5], 'eye': [3200, 3200],
                    'encoded': [1408, 1408], 'gaze_age_ms': 8} for i in range(3)]
        for sample in samples:
            view.fovea_fn = lambda sample=sample: sample
            view.page_eyes()
        motion = view.eye_motion['left']
        self.assertEqual([point[:2] for point in motion['trail']], [(.5, .5), (.6, .5), (.7, .5)])
        self.assertGreater(motion['gaze'][0], .6)
        self.assertLess(motion['gaze'][0], .7, 'it glides to the newest sample')
        self.assertTrue(view.s.RenderFillRects.called, 'the squares')
        self.assertTrue(any(call.args[3] & 0xFFFFFF00 == 0xFF2DCB00 for call in view.paint.circle.call_args_list), 'the trail')
        self.assertEqual(game_ui.View.glide(None, [.2, .3], .5), [.2, .3])
        self.assertIsNone(game_ui.View.glide([.2, .3], None, .5))

    def test_a_held_gaze_stays_in_the_trail_until_the_eye_moves_on(self):
        """The eye holds still for longer than the trail lasts, then moves: the trail runs from where it held."""
        from launcher import game_ui
        view = game_ui.View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.s, view.paint, view.renderer = Mock(), Mock(), None
        view.label, view.button = Mock(), Mock()
        view.X = view.Y = view.u = lambda value: value
        view.state = {'running': True}
        clock = [100.0]

        def sample(x):
            return {'live': True, 'region': [.4, .4], 'edge': [4.0, 4.0], 'left': [.5, .5], 'right': [.5, .5],
                    'gaze_left': [x, .5], 'gaze_right': [x, .5], 'eye': [3200, 3200], 'encoded': [1408, 1408]}
        with patch.object(game_ui.time, 'monotonic', side_effect=lambda: clock[0]):
            for step in range(40):  # two seconds on one spot
                view.fovea_fn = lambda: sample(.3)
                view.page_eyes()
                clock[0] += .05
            view.fovea_fn = lambda: sample(.7)
            view.page_eyes()
        self.assertEqual([point[:2] for point in view.eye_motion['left']['trail']], [(.3, .5), (.7, .5)])
        page = read('launcher/desktop.html')
        self.assertIn('if(last&&last.x===gaze[0]&&last.y===gaze[1])last.t=now;else e.trail.push(', page)
        self.assertIn('age=Math.min(1,(now-a.t)/FOVEA_TRAIL_MS)', page)



class HeaderWordmarkTests(unittest.TestCase):
    """The name in the headers: VROST as traced from the logo, then Bridge (capital B, lower-case ridge)."""

    def symbol(self):
        sys.path.insert(0, str(ROOT / 'brand' / 'tools'))
        import vrostbridge_symbol
        return vrostbridge_symbol

    def test_the_artwork_is_what_the_brand_tools_make(self):
        symbol = self.symbol()
        self.assertEqual(read('brand/assets/svg/vrostbridge-wordmark-inline.svg'), symbol.wordmark_inline('dark'))
        self.assertEqual(read('brand/assets/svg/vrostbridge-wordmark-inline-light.svg'), symbol.wordmark_inline('light'))
        self.assertEqual((ROOT / 'launcher/brand/vrostbridge-name.svg').read_bytes(),
                         (ROOT / 'brand/assets/svg/vrostbridge-wordmark-inline.svg').read_bytes())
        text = symbol.wordmark_inline('dark')
        self.assertIn('<title>VROSTBridge</title>', text)
        self.assertIn('fill="#FFFFFF"', text)  # VROST, as in the logo
        self.assertIn('fill="#CAD0E0"', text)  # Bridge, BRIDGE's colour in the logo

    def test_bridge_has_a_capital_b_and_lower_case_letters(self):
        symbol = self.symbol()
        word = read('brand/assets/svg/vrostbridge-bridge.svg')
        self.assertIn('"Bridge" set in Montserrat (SIL OFL 1.1) weight 400', word)
        found = re.search(r'<path id="bridge" data-cap-height="(\d+)" d="([^"]+)"', word)
        cap = float(found.group(1))
        x0, y0, x1, y1 = symbol.path_bounds(found.group(2))
        # Capitals only would span exactly the capital height above the baseline: d rises above it, g descends.
        self.assertLess(y0, -cap * 1.03)
        self.assertGreater(y1, cap * .2)
        self.assertIn("TEXT, WEIGHT, TRACKING = 'Bridge', 400, 0.10", read('brand/tools/set_bridge.py'))

    def test_the_game_mode_picture_and_its_measurements(self):
        symbol = self.symbol()
        from launcher import game_ui
        metrics = symbol.inline_metrics()
        self.assertAlmostEqual(game_ui.NAME_CAP, metrics['cap'], places=3)
        self.assertAlmostEqual(game_ui.NAME_CAP_TOP, metrics['cap_top'], places=3)
        head = (ROOT / 'launcher/brand/vrostbridge-name.png').read_bytes()[:24]
        width, height = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
        self.assertEqual(height, round(96 / metrics['cap']))
        self.assertAlmostEqual(width / height, metrics['aspect'], places=2)

    def test_the_page_header(self):
        page = read('launcher/desktop.html')
        self.assertIn('<img class="brand-name" src="/brand/vrostbridge-name.svg" alt="VROSTBridge" width="258" height="26">', page)
        self.assertNotIn('<div class="brand-name">', page)
        # The line under the name, unchanged.
        self.assertIn('<small class="tagline" id="tagline"><b class="cyan">V</b>R <b class="cyan">R</b>endering <b class="blue">O</b>'
                      'ut<b class="purple">S</b>ide <b class="magenta">T</b>he <b class="magenta">B</b>ox – <b class="g1">B</b>'
                      '<b class="g2">r</b><b class="g3">i</b><b class="g4">d</b><b class="g5">g</b><b class="g6">e</b></small>', page)
        from launcher.app import BRAND_FILES
        self.assertEqual(BRAND_FILES['vrostbridge-name.svg'], 'image/svg+xml')

    def test_game_mode_draws_the_name_and_falls_back_to_text(self):
        from launcher import game_ui
        view = game_ui.View(ROOT, lambda: {}, Mock(), lambda: {}, threading.Event())
        view.s, view.paint, view.renderer, view.ink, view.k = Mock(), Mock(), None, Mock(), 1.0
        view.ink.width.return_value = 10
        view.label = Mock()
        view.X = view.Y = view.u = lambda value: value
        view.connection_chip = lambda: ''
        view.logo, view.name_art, view.name_aspect = Path('icon.png'), Path('vrostbridge-name.png'), 10.0
        view.paint.image.return_value = True
        view.header()
        name = [call for call in view.paint.image.call_args_list if call.args[0] == Path('vrostbridge-name.png')]
        self.assertEqual(len(name), 1)
        x, y, w, h = name[0].args[1:]
        self.assertEqual(x, 120)
        self.assertAlmostEqual(h * game_ui.NAME_CAP, 24)  # VROST's capitals as tall as the old heading's
        self.assertAlmostEqual(y + h * game_ui.NAME_CAP_TOP, 46)  # and where they were
        self.assertAlmostEqual(w, h * 10.0)
        self.assertNotIn('VROSTBridge', [call.args[0] for call in view.label.call_args_list])
        view.paint.image.reset_mock()
        view.label.reset_mock()
        view.name_art = None  # no picture: the name as text, as before
        view.header()
        self.assertIn(call_args('VROSTBridge', 120, 70, 34), [call.args[:4] for call in view.label.call_args_list])


def call_args(*args):
    return args


if __name__ == '__main__':
    unittest.main()
