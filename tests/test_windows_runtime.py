"""1.0.0.7 Windows headset client: local SteamVR, the native client and its probe, readiness and diagnostics.

Every Windows facility is faked (sys.platform, OpenVR's registry in a temporary folder with SteamVR's
bin\\win64 programs, the process list and identities, subprocesses, wevtutil, IP Helper), so these run on
Linux. The Linux behaviour of the same functions is covered by the existing tests and checked here too
where a function now has two branches.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from headset_fixture import HEADSET, HEADSET_OUTPUT
from launcher import client_runtime
from launcher.client_runtime import (ClientRuntime, crash_details, describe_event, parse_events, process_identity,
                                     runtime_name, runtime_processes, steam_environment)
from launcher import diagnostics
from launcher.steamvr_async import AsyncReprojectionCheck
from launcher.validation import check_machine

NO_WINDOW = 0x08000000
ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = ('vrserver', 'vrcompositor', 'vrmonitor', 'vrstartup', 'vrdashboard')


class WindowsTree(unittest.TestCase):
    """A Windows headset PC: %LOCALAPPDATA%\\openvr\\openvrpaths.vrpath naming SteamVR's runtime, config and logs."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.local = self.root / 'AppData' / 'Local'
        self.steamvr = self.root / 'Steam' / 'steamapps' / 'common' / 'SteamVR'
        self.bin = self.steamvr / 'bin' / 'win64'
        self.bin.mkdir(parents=True)
        for name in PROGRAMS:
            (self.bin / (name + '.exe')).touch()
        self.config = self.root / 'Steam' / 'config'
        self.config.mkdir(parents=True)
        self.logs = self.root / 'Steam' / 'logs'
        self.logs.mkdir()
        (self.local / 'openvr').mkdir(parents=True)
        self.registry = self.local / 'openvr' / 'openvrpaths.vrpath'
        self.registry.write_text(json.dumps({'runtime': [str(self.steamvr)], 'config': [str(self.config)],
                                             'log': [str(self.logs)], 'external_drivers': None}), encoding='utf-8-sig')
        self.client_dir = self.root / 'VROSTBridge Client'
        self.client_dir.mkdir()
        self.client = self.client_dir / 'VROSTBridgeClient.exe'
        self.client.touch()
        self.probe = self.client_dir / 'VROSTBridgeRuntimeProbe.exe'
        self.probe.touch()
        for patcher in (patch('sys.platform', 'win32'),
                        patch.dict(os.environ, {'LOCALAPPDATA': str(self.local), 'LD_LIBRARY_PATH': '/kept/as/is'})):
            patcher.start()
            self.addCleanup(patcher.stop)
        # The processes running now: pid -> (image name, identity). Identities are host/processes.identify's shape.
        self.live = {}
        self.terminated = []
        self.listed = []
        for target, value in (('_toolhelp_processes', self.process_list), ('windows_identity', self.identify),
                              ('terminate_identity', self.terminate)):
            patcher = patch('launcher.client_runtime.' + target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = Mock()

    def run_process(self, pid, name, path=None, created=None):
        identity = {'pid': pid, 'path': str(path or self.bin / (name + '.exe')), 'created': created or 1000 + pid}
        self.live[pid] = (Path(identity['path']).name if path is None else name + '.exe', identity)
        return identity

    def process_list(self):
        return [(pid, image) for pid, (image, _) in self.live.items()] + [(4, 'System'), (77, 'explorer.exe')]

    def identify(self, pid):
        self.listed.append(pid)
        return dict(self.live[pid][1]) if pid in self.live else None

    def terminate(self, identity):
        current = self.live.get(identity['pid'])
        if current is None or current[1] != identity:
            raise ValueError('Runtime process identity changed; it was not terminated.')
        self.terminated.append(identity['pid'])
        del self.live[identity['pid']]

    def runtime(self, executable=None):
        return ClientRuntime(executable or self.client, self.root / 'state', self.log)


class WindowsPathTests(WindowsTree):
    def test_openvr_registry_runtime_config_logs_and_startup_come_from_localappdata(self):
        self.assertEqual(client_runtime.openvr_registry(), self.registry)
        self.assertEqual(client_runtime.runtime_roots(), [self.steamvr.resolve()])
        self.assertEqual(client_runtime.log_roots(), [self.logs])
        self.assertEqual(client_runtime.settings_path(), self.config.resolve() / 'steamvr.vrsettings')
        self.assertEqual(client_runtime.windows_startup(), self.steamvr.resolve() / 'bin' / 'win64' / 'vrstartup.exe')
        self.assertEqual(self.runtime().compositor_logs, [self.logs / 'vrcompositor.txt'])
        self.assertIsNone(client_runtime.runtime_environment_wrapper())

    def test_compositor_log_place_is_read_again_once_steamvr_registered_it(self):
        self.registry.unlink()
        runtime = self.runtime()
        self.assertEqual(runtime.compositor_logs, [])
        self.registry.write_text(json.dumps({'runtime': [str(self.steamvr)], 'log': [str(self.logs)]}))
        (self.logs / 'vrcompositor.txt').write_text('earlier start\n')
        runtime.mark_compositor_log()
        self.assertEqual(runtime.compositor_mark[0], self.logs / 'vrcompositor.txt')
        with (self.logs / 'vrcompositor.txt').open('a') as handle:
            handle.write('Failed Watchdog timeout in thread Render in RenderDistort after 5.9 seconds. Aborting.\n')
        self.assertTrue(runtime.compositor_aborted())

    def test_names_compare_like_linux(self):
        self.assertEqual(runtime_name({'pid': 1, 'path': 'C:\\SteamVR\\bin\\win64\\VRServer.EXE'}), 'vrserver')
        self.assertEqual(runtime_name(str(self.bin / 'vrcompositor.exe')), 'vrcompositor')
        with patch('sys.platform', 'linux'):
            self.assertEqual(runtime_name({'pid': 1, 'path': '/steamvr/bin/linux64/vrcompositor'}), 'vrcompositor')

    def test_steam_environment_is_this_environment_without_loader_path_handling(self):
        environment = steam_environment({'EXTRA': '1'})
        self.assertEqual(environment['LD_LIBRARY_PATH'], '/kept/as/is')
        self.assertEqual(environment['EXTRA'], '1')
        with patch('sys.platform', 'linux'):
            self.assertNotIn('LD_LIBRARY_PATH', steam_environment())

    def test_settings_are_written_without_posix_modes_and_retried_while_held(self):
        with patch('launcher.client_runtime.os.fchmod', side_effect=AssertionError('no fchmod on Windows'), create=True):
            client_runtime.atomic_json(self.config / 'steamvr.vrsettings', {'steamvr': {'enableHomeApp': False}})
        self.assertFalse(json.loads((self.config / 'steamvr.vrsettings').read_text())['steamvr']['enableHomeApp'])
        replace = Mock(side_effect=[PermissionError('held by a scanner'), None])
        with patch('launcher.client_runtime.os.replace', replace), patch('launcher.client_runtime.time.sleep') as sleep:
            client_runtime._replace('a', 'b')
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once()
        with patch('sys.platform', 'linux'), patch('launcher.client_runtime.os.replace', side_effect=PermissionError('x')):
            with self.assertRaises(PermissionError):
                client_runtime._replace('a', 'b')


class WindowsProcessTests(WindowsTree):
    def test_only_the_registered_runtime_programs_are_managed(self):
        server = self.run_process(101, 'vrserver')
        compositor = self.run_process(102, 'vrcompositor', path=str(self.bin / 'VRCompositor.EXE'))
        monitor = self.run_process(103, 'vrmonitor')
        self.run_process(104, 'vrserver', path=str(self.root / 'Other SteamVR' / 'bin' / 'win64' / 'vrserver.exe'))
        self.live[105] = ('notepad.exe', {'pid': 105, 'path': 'C:\\Windows\\notepad.exe', 'created': 1})
        self.assertEqual(runtime_processes(), {101: server, 102: compositor, 103: monitor})
        self.assertNotIn(105, self.listed, 'other programs are never opened')
        self.assertNotIn(4, self.listed)
        self.assertEqual(process_identity(101), server)

    def test_processes_reports_any_running_steamvr_for_the_app_checks(self):
        runtime = self.runtime()
        self.assertEqual(runtime.processes(), {})
        self.assertFalse(runtime.processes())
        server = self.run_process(101, 'vrserver')
        # Not started by this session: still SteamVR running on this PC.
        self.assertEqual(runtime.identities, {})
        self.assertEqual(runtime.processes(), {101: server})

    def test_tasklist_answers_when_the_snapshot_fails_and_errors_fail_closed(self):
        server = self.run_process(101, 'vrserver')
        with patch('launcher.client_runtime._toolhelp_processes', side_effect=OSError('snapshot')), \
             patch('host.control.windows_runtime_process_ids', return_value={'vrserver.exe': {101}, 'vrcompositor.exe': set()}):
            self.assertEqual(runtime_processes(), {101: server})
        runtime = self.runtime()
        runtime.session_active = True
        with patch('launcher.client_runtime._toolhelp_processes', side_effect=OSError('snapshot')), \
             patch('host.control.windows_runtime_process_ids', side_effect=OSError('tasklist')):
            with self.assertRaisesRegex(OSError, 'could not list running programs'):
                runtime.processes()
            runtime.capture()  # a start keeps going; the next check looks again
            runtime.capture()
        self.assertEqual(sum('process check skipped' in call.args[0] for call in self.log.call_args_list), 1)
        with patch('sys.platform', 'linux'), patch('launcher.client_runtime.runtime_processes', side_effect=OSError('proc')):
            with self.assertRaises(OSError):
                runtime.capture()


class LinuxProcessesTests(unittest.TestCase):
    def test_processes_is_the_registered_runtime_on_linux_too(self):
        root = Path('/opt/steam/SteamVR')
        good = {'pid': 1, 'path': str(root / 'bin/linux64/vrserver'), 'uid': os.getuid(), 'started': 12}
        wrong = dict(good, pid=2, path='/somewhere/vrserver')
        with patch('sys.platform', 'linux'), patch('launcher.client_runtime.runtime_roots', return_value=[root]), \
             patch('launcher.client_runtime.Path.iterdir', return_value=[Path('/proc/1'), Path('/proc/2')]), \
             patch('launcher.client_runtime.process_identity', side_effect=lambda pid: good if pid == 1 else wrong):
            self.assertEqual(ClientRuntime(Path('/x/VROSTBridgeClient'), '/tmp', print).processes(), {1: good})

    def test_the_app_actions_that_need_steamvr_closed_have_their_method(self):
        # launcher/app.py (enable-game-mode, disable-game-mode, repair-async) calls client_runtime.processes().
        self.assertIn('self.client_runtime.processes()', (ROOT / 'launcher/app.py').read_text(encoding='utf-8'))
        self.assertTrue(callable(getattr(ClientRuntime, 'processes', None)))


class WindowsStartTests(WindowsTree):
    def setUp(self):
        super().setUp()
        self.runs = []
        self.results = [subprocess.CompletedProcess([], 2, 'RUNTIME_WAIT display_progress\n', ''),
                        subprocess.CompletedProcess([], 0, HEADSET_OUTPUT, '')]

    def fake_run(self, command, **options):
        self.runs.append((command, options))
        return self.results.pop(0)

    def test_probe_runs_hidden_in_utf8_then_vrstartup_starts_steamvr(self):
        runtime = self.runtime()
        with patch('launcher.client_runtime.subprocess.run', side_effect=self.fake_run), \
             patch('launcher.client_runtime.subprocess.Popen') as launch, \
             patch('launcher.client_runtime.open_url') as url, \
             patch.object(runtime.cancelled, 'wait'):
            runtime.ensure(True, extra_env={})
        command, options = self.runs[0]
        self.assertEqual(command, [str(self.probe.resolve()), '--verify-runtime'])
        self.assertEqual(options['creationflags'], NO_WINDOW)
        self.assertEqual((options['encoding'], options['errors'], options['text'], options['capture_output']),
                         ('utf-8', 'replace', True, True))
        self.assertLessEqual(options['timeout'], 8)
        self.assertEqual(options['env']['LD_LIBRARY_PATH'], '/kept/as/is')
        startup = self.steamvr.resolve() / 'bin' / 'win64' / 'vrstartup.exe'
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], [str(startup)])
        self.assertEqual(launch.call_args.kwargs['cwd'], str(startup.parent))
        self.assertEqual(launch.call_args.kwargs['creationflags'], NO_WINDOW)
        url.assert_not_called()
        self.assertEqual(runtime.state, 'ready')
        self.assertEqual(runtime.headset, HEADSET)
        self.assertEqual(runtime.status()['probe_executable'], str(self.probe.resolve()))
        self.assertEqual(runtime.status()['native_environment_setup'], 'direct')
        settings = json.loads((self.config / 'steamvr.vrsettings').read_text(encoding='utf-8'))
        self.assertEqual((settings['steamvr']['enableHomeApp'], settings['dashboard']['enableDashboard'],
                          settings['steamvr']['sendSystemButtonToAllApps']), (False, False, True))
        self.assertTrue(runtime.receipt.exists())

    def test_steam_starts_steamvr_when_vrstartup_is_missing(self):
        (self.bin / 'vrstartup.exe').unlink()
        runtime = self.runtime()
        with patch('launcher.client_runtime.subprocess.run', side_effect=self.fake_run), \
             patch('launcher.client_runtime.subprocess.Popen') as launch, \
             patch('launcher.client_runtime.open_url') as url, \
             patch.object(runtime.cancelled, 'wait'):
            runtime.ensure()
        url.assert_called_once_with('steam://rungameid/250820')
        launch.assert_not_called()
        self.assertEqual(runtime.state, 'ready')

    def test_no_way_to_start_steamvr_is_a_clear_error(self):
        (self.bin / 'vrstartup.exe').unlink()
        runtime = self.runtime()
        with patch('launcher.client_runtime.subprocess.run', side_effect=self.fake_run), \
             patch('launcher.client_runtime.open_url', side_effect=OSError('No application is associated')), \
             patch.object(runtime.cancelled, 'wait'):
            with self.assertRaisesRegex(ValueError, 'SteamVR could not be started'):
                runtime.ensure()

    def test_client_exe_verifies_the_runtime_when_the_probe_is_missing(self):
        self.probe.unlink()
        runtime = self.runtime(self.client_dir / 'VROSTBridgeClient')  # a path without its suffix
        with patch('launcher.client_runtime.subprocess.run', return_value=self.results[1]) as run:
            runtime.helper(timeout=2.5)
        self.assertEqual(run.call_args.args[0], [str(self.client.resolve()), '--verify-runtime'])
        self.assertEqual(run.call_args.kwargs['timeout'], 2.5)
        self.assertEqual(run.call_args.kwargs['creationflags'], NO_WINDOW)

    def test_native_command_is_the_program_and_its_arguments(self):
        runtime = self.runtime(self.client_dir / 'VROSTBridgeClient')
        arguments = ['--gaze-socket', str(self.root / 'socket with spaces'), '--sharpening', '0']
        with patch('launcher.client_runtime.runtime_roots', side_effect=AssertionError('no wrapper lookup')):
            self.assertEqual(runtime.native_command(arguments), [str(self.client.resolve()), *arguments])
            self.assertEqual(runtime.native_command(['--help']), [str(self.client.resolve()), '--help'])
        self.assertEqual(runtime.status()['native_environment_setup'], 'direct')
        self.assertEqual(self.log.call_count, 1)

    def test_an_existing_steamvr_is_closed_before_the_stream_only_start(self):
        self.run_process(101, 'vrserver')
        runtime = self.runtime()
        quit_sent = []

        def popen(command, **options):
            if command[-1] == 'vrmonitor://quit':
                quit_sent.append((command, options))
                self.live.clear()  # SteamVR closes itself
            return Mock()
        with patch('launcher.client_runtime.subprocess.run', side_effect=self.fake_run), \
             patch('launcher.client_runtime.subprocess.Popen', side_effect=popen), \
             patch.object(runtime.cancelled, 'wait'):
            runtime.ensure()
        self.assertEqual(len(quit_sent), 1)
        self.assertEqual(self.terminated, [])
        self.assertEqual(runtime.state, 'ready')


class WindowsStopTests(WindowsTree):
    def setUp(self):
        super().setUp()
        self.server = self.run_process(101, 'vrserver')
        self.compositor = self.run_process(102, 'vrcompositor')
        self.monitor = self.run_process(103, 'vrmonitor')
        self.runtime_ = self.runtime()
        self.runtime_.identities = runtime_processes()
        self.runtime_.session_active = True
        self.waits = []
        self.runtime_.wait_stopped = Mock(side_effect=self.wait)
        self.signals = patch('launcher.client_runtime.signal_identity', side_effect=AssertionError('no POSIX signals'))
        self.signals.start()
        self.addCleanup(self.signals.stop)

    def wait(self, seconds):
        self.waits.append(seconds)
        return not self.runtime_.remaining()

    def test_graceful_quit_uses_the_servers_own_vrmonitor(self):
        def popen(command, **options):
            self.live.clear()
            return Mock()
        with patch('launcher.client_runtime.subprocess.Popen', side_effect=popen) as launch:
            self.runtime_.stop()
        self.assertEqual(launch.call_args.args[0], [str(self.bin / 'vrmonitor.exe'), 'vrmonitor://quit'])
        self.assertEqual(launch.call_args.kwargs['creationflags'], NO_WINDOW)
        self.assertEqual(launch.call_args.kwargs['cwd'], str(self.bin))
        self.assertEqual(self.waits, [8])
        self.assertEqual(self.terminated, [])
        self.assertEqual((self.runtime_.state, self.runtime_.session_active, self.runtime_.identities), ('idle', False, {}))

    def test_quit_goes_to_the_registered_link_handler_only_inside_steamvr(self):
        handler = self.bin / 'vrurlhandler.exe'
        handler.touch()
        elsewhere = self.root / 'Downloads' / 'vrurlhandler.exe'
        elsewhere.parent.mkdir()
        elsewhere.touch()
        server = self.bin / 'vrserver.exe'
        for command, expected in (('"' + str(handler) + '" "%1"', handler), (str(handler) + ' %1', handler),
                                  ('"' + str(elsewhere) + '" "%1"', self.bin / 'vrmonitor.exe'),
                                  ('', self.bin / 'vrmonitor.exe'), ('"unterminated', self.bin / 'vrmonitor.exe')):
            with patch('launcher.client_runtime.registered_url_command', return_value=command):
                self.assertEqual(client_runtime.windows_quit_program(server), expected, command)
        with patch('launcher.client_runtime.registered_url_command', return_value='"' + str(handler) + '" "%1"'), \
             patch('launcher.client_runtime.subprocess.Popen') as launch:
            self.assertTrue(self.runtime_.request_quit())
        self.assertEqual(launch.call_args.args[0], [str(handler), 'vrmonitor://quit'])

    def test_lingering_processes_are_ended_server_first_after_the_quit_window(self):
        with patch('launcher.client_runtime.subprocess.Popen'):
            self.runtime_.stop()
        self.assertEqual(self.waits, [8, 5, 3])
        self.assertEqual(self.terminated, [101, 102, 103])
        self.assertEqual(self.runtime_.state, 'idle')

    def test_a_reused_pid_is_never_ended(self):
        # The compositor exited and Windows gave its PID to another program; the server exits by itself.
        self.live[102] = ('notepad.exe', {'pid': 102, 'path': 'C:\\Windows\\notepad.exe', 'created': 99999})
        with patch('launcher.client_runtime.subprocess.Popen'):
            self.runtime_.stop()
        self.assertNotIn(102, self.terminated)
        self.assertIn(102, self.live)
        self.assertEqual(self.terminated, [101, 103])
        # The identity check happens again on the process handle: a race is refused, not ended.
        self.live[201] = ('vrserver.exe', {'pid': 201, 'path': str(self.bin / 'vrserver.exe'), 'created': 5})
        runtime = self.runtime()
        runtime.identities = {201: dict(self.live[201][1])}
        with patch('launcher.client_runtime.process_identity', side_effect=lambda pid: runtime.identities.get(pid)):
            self.live[201] = ('vrserver.exe', dict(self.live[201][1], created=6))
            self.assertEqual(runtime.terminate_remaining(), 0)
        self.assertIn(201, self.live)

    def test_a_process_windows_refuses_to_end_leaves_stop_retryable(self):
        with patch('launcher.client_runtime.subprocess.Popen'), \
             patch('launcher.client_runtime.terminate_identity', side_effect=OSError('Access is denied')):
            with self.assertRaisesRegex(ValueError, 'still stopping'):
                self.runtime_.stop()
        self.assertEqual(self.runtime_.state, 'error')
        self.assertTrue(any('could not be closed' in call.args[0] for call in self.log.call_args_list))

    def test_without_a_server_to_ask_the_others_are_ended_at_once(self):
        del self.live[101]
        with patch('launcher.client_runtime.subprocess.Popen') as launch:
            self.runtime_.stop()
        launch.assert_not_called()
        self.assertEqual(self.waits, [0, 0, 3])
        self.assertEqual(self.terminated, [102, 103])

    def test_watchdog_recovery_ends_only_the_compositor(self):
        self.assertEqual(self.runtime_.recover_compositor(), 1)
        self.assertEqual(self.terminated, [102])
        self.assertNotIn(102, self.runtime_.identities)
        self.assertIn(101, self.runtime_.identities)
        with patch('launcher.client_runtime.terminate_identity', side_effect=ValueError('gone')):
            self.runtime_.identities[103] = self.monitor
            self.runtime_.identities[104] = self.run_process(104, 'vrcompositor')
            self.assertEqual(self.runtime_.recover_compositor(), 0)


APPLICATION_EVENTS = (
    # Windows 11: named fields.
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error' "
    "Guid='{a0e9b465-b939-57d7-b27d-95d8e925ff57}'/><EventID>1000</EventID><Version>0</Version><Level>2</Level>"
    "<Task>100</Task><TimeCreated SystemTime='2026-10-08T17:35:12.3456789Z'/><EventRecordID>4242</EventRecordID>"
    "<Channel>Application</Channel><Computer>HEADSET-PC</Computer></System><EventData>"
    "<Data Name='AppName'>vrcompositor.exe</Data><Data Name='AppVersion'>2.7.4.0</Data>"
    "<Data Name='AppTimeStamp'>66d8a123</Data><Data Name='ModuleName'>amdvlk64.dll</Data>"
    "<Data Name='ModuleVersion'>2.0.302.0</Data><Data Name='ModuleTimeStamp'>65f0aa01</Data>"
    "<Data Name='ExceptionCode'>c0000005</Data><Data Name='FaultingOffset'>00000000001a2b3c</Data>"
    "<Data Name='ProcessId'>0x1c60</Data><Data Name='ProcessCreationTime'>0x1db1a2b3c4d5e6f</Data>"
    "<Data Name='AppPath'>C:\\Program Files (x86)\\Steam &amp; Games\\SteamVR\\bin\\win64\\vrcompositor.exe</Data>"
    "<Data Name='ModulePath'>C:\\WINDOWS\\System32\\amdvlk64.dll</Data><Data Name='IntegratorReportId'>0b2f</Data>"
    "<Data Name='PackageFullName'></Data><Data Name='PackageRelativeAppId'></Data></EventData></Event>\r\n"
    # Another program's crash: not ours.
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error'/>"
    "<EventID>1000</EventID><TimeCreated SystemTime='2026-10-08T17:36:00.0000000Z'/></System><EventData>"
    "<Data Name='AppName'>chrome.exe</Data><Data Name='ExceptionCode'>c0000005</Data></EventData></Event>\r\n"
    # Windows 10: positional fields; the native client.
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error'/>"
    "<EventID Qualifiers='0'>1000</EventID><TimeCreated SystemTime='2026-10-08T17:35:20.0000000Z'/></System><EventData>"
    "<Data>VROSTBridgeClient.exe</Data><Data>1.0.0.7</Data><Data>67000000</Data><Data>ucrtbase.dll</Data>"
    "<Data>10.0.22621.3593</Data><Data>10c46e71</Data><Data>c0000409</Data><Data>000000000007f61e</Data>"
    "<Data>0x2a10</Data><Data>0x1db1</Data><Data>C:\\VROSTBridge\\VROSTBridgeClient.exe</Data></EventData></Event>\r\n"
    # A hang report for SteamVR's server.
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Hang'/>"
    "<EventID Qualifiers='0'>1002</EventID><TimeCreated SystemTime='2026-10-08T17:34:00.0000000Z'/></System><EventData>"
    "<Data>vrserver.exe</Data><Data>2.7.4.0</Data><Data>1c60</Data><Data>01db</Data><Data>4294967295</Data>"
    "<Data>C:\\SteamVR\\bin\\win64\\vrserver.exe</Data></EventData><Binary>55006E006B006E006F0077006E0000000000</Binary></Event>\r\n"
    "<Event><broken")
SYSTEM_EVENTS = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Display'/>"
                 "<EventID Qualifiers='0'>4101</EventID><TimeCreated SystemTime='2026-10-08T17:35:11.5000000Z'/>"
                 "</System><EventData><Data>amdkmdag</Data><Data></Data></EventData></Event>\r\n")


class CrashDetailTests(unittest.TestCase):
    def runner(self, command, timeout):
        self.commands.append((command, timeout))
        text = APPLICATION_EVENTS if command[2] == 'Application' else SYSTEM_EVENTS
        # wevtutil's redirected output may come back as UTF-16; NULs are dropped before parsing.
        return subprocess.CompletedProcess(command, 0, text if command[2] == 'Application' else '\x00'.join(text), '')

    def setUp(self):
        self.commands = []

    def test_crashes_hangs_and_driver_resets_of_steamvr_and_the_client_oldest_first(self):
        with patch('sys.platform', 'win32'):
            lines = crash_details(runner=self.runner)
        self.assertEqual(len(lines), 4)
        self.assertIn('vrserver.exe 2.7.4.0 stopped responding (Windows hang report).', lines[0])
        self.assertIn('display driver amdkmdag stopped responding and was recovered by Windows (TDR).', lines[1])
        self.assertIn('vrcompositor.exe 2.7.4.0 crashed: exception 0xc0000005 (access violation) in amdvlk64.dll '
                      '2.0.302.0 at offset 0x00000000001a2b3c.', lines[2])
        self.assertIn('VROSTBridgeClient.exe 1.0.0.7 crashed: exception 0xc0000409 (fail-fast or stack buffer overrun) '
                      'in ucrtbase.dll 10.0.22621.3593', lines[3])
        self.assertFalse(any('chrome' in line for line in lines))
        self.assertTrue(all(len(line) <= 300 for line in lines))
        (application, timeout), (system, _) = self.commands
        self.assertEqual(timeout, 8)
        self.assertTrue(application[0].lower().endswith('wevtutil.exe'))
        self.assertEqual(application[1:3], ['qe', 'Application'])
        self.assertIn("/q:*[System[Provider[@Name='Application Error' or @Name='Application Hang'] and "
                      'TimeCreated[timediff(@SystemTime) <= 600000]]]', application)
        self.assertIn('/rd:true', application)
        self.assertIn('/f:xml', application)
        self.assertEqual(system[2], 'System')
        self.assertIn("/q:*[System[Provider[@Name='Display'] and EventID=4101 and TimeCreated[timediff(@SystemTime) <= 300000]]]", system)

    def test_limit_and_unreadable_logs(self):
        with patch('sys.platform', 'win32'):
            self.assertEqual(len(crash_details(runner=self.runner, limit=2)), 2)
            self.assertEqual(crash_details(runner=Mock(side_effect=OSError('missing'))), [])
            self.assertEqual(crash_details(runner=Mock(return_value=subprocess.CompletedProcess([], 15007, '', 'denied'))), [])
            self.assertEqual(crash_details(runner=Mock(side_effect=subprocess.TimeoutExpired('wevtutil', 8))), [])
        before = len(self.commands)
        self.assertEqual(crash_details(runner=self.runner), [], 'only on Windows')
        self.assertEqual(len(self.commands), before)

    def test_event_parsing_keeps_names_positions_and_entities(self):
        events = parse_events(APPLICATION_EVENTS)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]['provider'], 'Application Error')
        self.assertEqual(events[0]['id'], 1000)
        self.assertEqual(events[0]['time'], '2026-10-08T17:35:12.3456789Z')
        self.assertIn('Steam & Games', events[0]['data']['AppPath'])
        self.assertEqual(events[2]['data']['AppName'], 'VROSTBridgeClient.exe')
        self.assertEqual(events[2]['data']['ExceptionCode'], 'c0000409')
        self.assertEqual(events[3]['data']['AppPath'], 'C:\\SteamVR\\bin\\win64\\vrserver.exe')
        self.assertEqual(describe_event(events[1]), '')
        self.assertEqual(describe_event({'provider': 'Application Error', 'id': 1001, 'data': {'AppName': 'vrserver.exe'}}), '')


class WindowsAsyncReprojectionTests(unittest.TestCase):
    def test_not_needed_and_nothing_is_run_or_changed(self):
        runner, log = Mock(), Mock()
        check = AsyncReprojectionCheck(registry='/nonexistent/openvrpaths.vrpath', runner=runner, log=log,
                                       pkexec='/usr/bin/pkexec', setcap='/usr/sbin/setcap', running=Mock(side_effect=AssertionError))
        with patch('sys.platform', 'win32'), patch('launcher.steamvr_async.os.getxattr', side_effect=AssertionError, create=True):
            status = check.check()
            repaired = check.repair()
        self.assertEqual(status['state'], 'not_needed')
        self.assertFalse(status['can_repair'])
        self.assertIn('Windows', status['message'])
        self.assertEqual(repaired['state'], 'not_needed')
        self.assertFalse(repaired['repaired'])
        self.assertFalse(repaired['restart_required'])
        self.assertIn('Nothing to repair on Windows', repaired['message'])
        runner.assert_not_called()
        # The desktop and Game Mode pages offer Repair only for these states.
        self.assertNotIn(status['state'], ('missing', 'disabled'))
        self.assertEqual(sum('STEAMVR ASYNC not_needed' in call.args[0] for call in log.call_args_list), 1)


class WindowsReadinessTests(WindowsTree):
    def windows(self, version='10.0.22631', machine='AMD64'):
        for target, value in (('system', 'Windows'), ('version', version), ('machine', machine), ('release', '11')):
            patcher = patch('launcher.validation.platform.' + target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_client_checks_os_registry_programs_and_their_loaders_hidden(self):
        self.windows()
        runs = []

        def run(command, **options):
            runs.append((command, options))
            return subprocess.CompletedProcess(command, 0, 'VROSTBridge 1.0.0.7\n', '')
        with patch('launcher.validation.subprocess.run', side_effect=run):
            result = check_machine('client', self.client_dir / 'VROSTBridgeClient')
        checks = {check['name']: check for check in result['checks']}
        self.assertEqual([name for name, check in checks.items() if check['result'] != 'pass'], [])
        self.assertEqual(set(checks), {'Operating system', 'Application installed', 'Runtime probe installed', 'SteamVR runtime',
                                       'Native runtime loader', 'Runtime probe loader'})
        self.assertIn('Windows 11', checks['Operating system']['detail'])
        self.assertEqual(checks['Application installed']['detail'], 'VROSTBridgeClient.exe')
        self.assertEqual([command for command, _ in runs], [[str(self.client), '--help'], [str(self.probe), '--help']])
        for _, options in runs:
            self.assertEqual((options['creationflags'], options['encoding'], options['errors']), (NO_WINDOW, 'utf-8', 'replace'))
            self.assertEqual(options['timeout'], 8)

    def test_missing_programs_dll_and_registry_need_attention(self):
        self.windows(version='6.3.9600')
        self.probe.unlink()
        self.registry.unlink()
        missing_dll = subprocess.CompletedProcess([], 0xC0000135, '', '')
        with patch('launcher.validation.subprocess.run', return_value=missing_dll):
            result = check_machine('client', self.client)
        checks = {check['name']: check for check in result['checks']}
        self.assertEqual(checks['Operating system']['result'], 'needs-attention')
        self.assertEqual(checks['Runtime probe installed']['result'], 'needs-attention')
        self.assertEqual(checks['SteamVR runtime']['result'], 'needs-attention')
        self.assertEqual(checks['Native runtime loader']['result'], 'needs-attention')
        self.assertIn('DLL', checks['Native runtime loader']['detail'])
        self.assertNotIn('Runtime probe loader', checks)
        self.assertNotIn('Graphics render device', checks)

    def test_linux_client_keeps_its_registry_and_render_device_checks(self):
        config = self.root / 'xdg'
        (config / 'openvr').mkdir(parents=True)
        (config / 'openvr' / 'openvrpaths.vrpath').write_text(json.dumps({'runtime': [str(self.steamvr)]}))
        native = self.root / 'VROSTBridgeClient'
        with patch('sys.platform', 'linux'), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config), 'LOCALAPPDATA': ''}):
            result = check_machine('client', native)
        checks = {check['name']: check for check in result['checks']}
        self.assertEqual(checks['SteamVR runtime']['result'], 'pass')
        self.assertIn('Graphics render device', checks)
        self.assertNotIn('Runtime probe installed', checks)
        self.assertEqual(checks['Application installed']['detail'], 'VROSTBridgeClient')


class WindowsDiagnosticsTests(WindowsTree):
    def test_control_peers_come_from_the_native_process_tcp_table(self):
        rows = [{'local_ip': '192.168.1.25', 'local_port': 9943, 'peer_ip': '192.168.1.10', 'peer_port': 50211, 'state': 5, 'pid': 4242},
                {'local_ip': '192.168.1.25', 'local_port': 9943, 'peer_ip': '192.168.1.11', 'peer_port': 1, 'state': 2, 'pid': 4242},
                {'local_ip': '192.168.1.25', 'local_port': 9945, 'peer_ip': '192.168.1.12', 'peer_port': 1, 'state': 5, 'pid': 4242},
                {'local_ip': '127.0.0.1', 'local_port': 9943, 'peer_ip': '127.0.0.1', 'peer_port': 1, 'state': 5, 'pid': 4242},
                {'local_ip': '10.0.0.2', 'local_port': 9943, 'peer_ip': '8.8.8.8', 'peer_port': 1, 'state': 5, 'pid': 4242}]
        with patch('launcher.windows_network.owned_sockets', return_value=(rows, [])) as owned:
            self.assertEqual(diagnostics.control_peers(4242), {'192.168.1.10'})
        owned.assert_called_once_with(4242)
        from launcher.windows_network import WindowsNetworkError
        with patch('launcher.windows_network.owned_sockets', side_effect=WindowsNetworkError('GetExtendedTcpTable failed')):
            self.assertEqual(diagnostics.control_peers(4242), set())
        self.assertEqual(diagnostics.control_peers(None), set())
        self.assertEqual(diagnostics.control_peers(0), set())

    def test_steamvr_logs_come_from_the_registered_log_folder(self):
        for name in ('vrserver.txt', 'vrcompositor.txt', 'vrclient_VROSTBridgeClient.txt', 'vrclient_VROSTBridgeRuntimeProbe.txt'):
            (self.logs / name).write_text(name + ' line\n')
        self.assertEqual(set(diagnostics.steamvr_logs()), {'vrserver.txt', 'vrcompositor.txt', 'vrclient_VROSTBridgeClient.txt',
                                                         'vrclient_VROSTBridgeRuntimeProbe.txt'})
        self.registry.write_text(json.dumps({'runtime': [str(self.steamvr)], 'log': None}))
        self.assertEqual(diagnostics.steamvr_logs(), {})


if __name__ == '__main__':
    unittest.main()
