"""RC4 client system helpers: session sleep block and power profile, SteamVR
asynchronous reprojection permission, headset audio routing, and Wi-Fi radio
diagnostics with the opt-in access-point lock. No real system command runs:
every runner, process and extended attribute is a fake."""
import errno
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from launcher import steamvr_async
from launcher.headset_audio import HeadsetAudio, match_headset_devices
from launcher.session_power import SessionPower, parse_bus_profiles, parse_profile_list
from launcher.steamvr_async import AsyncReprojectionCheck, has_cap_sys_nice, parse_vfs_cap, steamvr_runtime_dirs
from launcher.wifi_diagnostics import WifiDiagnostics, parse_station_dump, valid_bssid


def completed(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


# Session power ------------------------------------------------------------

class FakeProcess:
    def __init__(self, exit_code=None, stubborn=False):
        self.exit_code, self.stubborn, self.returncode = exit_code, stubborn, None
        self.stdin = io.BytesIO()
        self.signals = []

    def poll(self):
        if self.exit_code is not None:
            self.returncode = self.exit_code
        return self.returncode

    def wait(self, timeout=None):
        if self.poll() is None:
            raise subprocess.TimeoutExpired('systemd-inhibit', timeout)
        return self.returncode

    def terminate(self):
        self.signals.append('TERM')
        if not self.stubborn:
            self.exit_code = -15

    def kill(self):
        self.signals.append('KILL')
        self.exit_code = -9


class FakePopen:
    def __init__(self, exit_code=None, stderr_text='', stubborn=False):
        self.exit_code, self.stderr_text, self.stubborn = exit_code, stderr_text, stubborn
        self.calls, self.processes = [], []

    def __call__(self, arguments, **options):
        self.calls.append((list(arguments), options))
        if self.stderr_text:
            options['stderr'].write(self.stderr_text.encode())
        process = FakeProcess(self.exit_code, self.stubborn)
        self.processes.append(process)
        return process


class FakeProfiles:
    """powerprofilesctl: get / list / set."""
    def __init__(self, current='balanced', offered=('performance', 'balanced', 'power-saver'), refuse=False):
        self.current, self.offered, self.refuse, self.calls = current, list(offered), refuse, []

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        assert arguments[0] == '/usr/bin/powerprofilesctl', arguments
        command = arguments[1:]
        if command == ['get']:
            return completed(0, self.current + '\n')
        if command == ['list']:
            blocks = [('* ' if name == self.current else '  ') + name + ':\n    CpuDriver:\tamd_pstate\n'
                      '    PlatformDriver:\tplatform_profile\n' + ('    Degraded:   no\n' if name == 'performance' else '')
                      for name in self.offered]
            return completed(0, '\n'.join(blocks))
        if command[0] == 'set' and len(command) == 2:
            if self.refuse:
                return completed(1, '', 'Failed to communicate with power-profiles-daemon: Access denied')
            self.current = command[1]
            return completed(0)
        raise AssertionError(arguments)

    def sets(self):
        return [call[2] for call in self.calls if call[1] == 'set']


class FakeBus:
    """busctl against the power profiles D-Bus API (tuned-ppd or power-profiles-daemon)."""
    def __init__(self, service='org.freedesktop.UPower.PowerProfiles', current='balanced'):
        self.service, self.current, self.calls = service, current, []

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        assert arguments[:2] == ['/usr/bin/busctl', '--system'], arguments
        rest = arguments[2:]
        if rest[:2] == ['--json=short', 'get-property']:
            if rest[2] != self.service:
                return completed(1, '', 'Failed to get property ActiveProfile: The name is not activatable')
            if rest[5] == 'ActiveProfile':
                return completed(0, json.dumps({'type': 's', 'data': self.current}) + '\n')
            if rest[5] == 'Profiles':
                profiles = [{'Profile': {'type': 's', 'data': name}, 'Driver': {'type': 's', 'data': 'multiple'}}
                            for name in ('power-saver', 'balanced', 'performance')]
                return completed(0, json.dumps({'type': 'aa{sv}', 'data': profiles}) + '\n')
        if rest[0] == 'set-property' and rest[1] == self.service and rest[4:6] == ['ActiveProfile', 's']:
            self.current = rest[6]
            return completed(0)
        raise AssertionError(arguments)


class SessionPowerTests(unittest.TestCase):
    def make(self, runner=None, popen=None, powerprofilesctl='/usr/bin/powerprofilesctl', busctl='',
             systemd_inhibit='/usr/bin/systemd-inhibit'):
        self.lines = []
        self.popen = popen or FakePopen()
        self.profiles = runner or FakeProfiles()
        return SessionPower(runner=self.profiles, log=self.lines.append, systemd_inhibit=systemd_inhibit,
                            powerprofilesctl=powerprofilesctl, popen=self.popen, busctl=busctl, cat='/usr/bin/cat')

    def test_inhibitor_blocks_only_suspend_and_stops_with_the_session(self):
        power = self.make()
        status = power.begin()
        self.assertEqual(status['state'], 'active')
        self.assertTrue(status['sleep_blocked'])
        arguments, options = self.popen.calls[0]
        # No 'idle': KDE turns a logind idle block into "keep the screen on".
        self.assertEqual(arguments, ['/usr/bin/systemd-inhibit', '--what=sleep:handle-lid-switch',
                                     '--who=VROSTBridge', '--why=VR streaming session', '--mode=block', '/usr/bin/cat'])
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['stdout'], subprocess.DEVNULL)
        self.assertEqual(options['stdin'], subprocess.PIPE)  # the child ends when this process goes away
        self.assertIn('screen can still turn off', status['message'])
        process = self.popen.processes[0]
        ended = power.end()
        self.assertEqual(process.signals, ['TERM'])
        self.assertTrue(process.stdin.closed)
        self.assertEqual(ended['state'], 'idle')
        self.assertFalse(ended['sleep_blocked'])
        power.end()  # idempotent
        self.assertEqual(process.signals, ['TERM'])
        self.assertEqual(len(self.popen.calls), 1)

    def test_inhibitor_that_exits_at_once_is_reported(self):
        power = self.make(popen=FakePopen(exit_code=1, stderr_text='Failed to inhibit: Access denied\n'),
                          powerprofilesctl='')
        status = power.begin()
        self.assertFalse(status['sleep_blocked'])
        self.assertEqual(status['state'], 'unavailable')
        self.assertIn('Access denied', status['message'])
        self.assertEqual(power.end()['state'], 'unavailable')  # nothing to undo
        self.assertEqual(self.popen.processes[0].signals, [])

    def test_missing_systemd_inhibit_is_reported_without_starting_anything(self):
        power = self.make(systemd_inhibit='')
        status = power.begin()
        self.assertEqual(status['state'], 'partial')  # the power profile still changed
        self.assertIn('systemd-inhibit', status['message'])
        self.assertEqual(self.popen.calls, [])

    def test_stubborn_inhibitor_is_killed(self):
        power = self.make(popen=FakePopen(stubborn=True))
        power.begin()
        power.end()
        self.assertEqual(self.popen.processes[0].signals, ['TERM', 'KILL'])

    def test_inhibitor_environment_drops_the_frozen_library_path(self):
        with mock.patch.dict(os.environ, {'LD_LIBRARY_PATH': '/tmp/_MEI1234', 'LD_LIBRARY_PATH_ORIG': '/usr/lib64'}):
            power = self.make()
            power.begin()
            power.end()
            with mock.patch.dict(os.environ):
                os.environ.pop('LD_LIBRARY_PATH_ORIG')
                power.begin()
                power.end()
        self.assertEqual(self.popen.calls[0][1]['env']['LD_LIBRARY_PATH'], '/usr/lib64')
        self.assertNotIn('LD_LIBRARY_PATH', self.popen.calls[1][1]['env'])

    def test_begin_twice_ends_the_previous_inhibitor_first(self):
        power = self.make()
        power.begin()
        power.begin()
        self.assertEqual(self.popen.processes[0].signals, ['TERM'])
        self.assertEqual(self.popen.processes[1].signals, [])
        self.assertEqual(self.profiles.sets(), ['performance', 'balanced', 'performance'])
        power.end()
        self.assertEqual(self.profiles.current, 'balanced')

    def test_status_notices_a_lost_inhibitor(self):
        power = self.make()
        power.begin()
        self.popen.processes[0].exit_code = 1
        status = power.status()
        self.assertFalse(status['sleep_blocked'])
        self.assertEqual(status['state'], 'partial')
        self.assertIn('ended unexpectedly', status['message'])
        power.end()
        self.assertEqual(self.popen.processes[0].signals, [])  # already gone; nothing to signal
        self.assertEqual(self.profiles.current, 'balanced')

    def test_performance_profile_is_set_and_restored(self):
        power = self.make()
        status = power.begin()
        self.assertEqual((status['power_profile'], status['original_profile']), ('performance', 'balanced'))
        self.assertEqual(self.profiles.sets(), ['performance'])
        status = power.end()
        self.assertEqual(self.profiles.sets(), ['performance', 'balanced'])
        self.assertEqual((status['power_profile'], status['original_profile']), ('balanced', None))
        self.assertIn('back to balanced', status['message'])

    def test_profile_changed_by_the_user_is_left_alone(self):
        power = self.make()
        power.begin()
        self.profiles.current = 'power-saver'
        status = power.end()
        self.assertEqual(self.profiles.sets(), ['performance'])
        self.assertEqual(status['power_profile'], 'power-saver')
        self.assertIn('changed during the session', status['message'])

    def test_profile_already_performance_or_not_offered_is_never_changed(self):
        for profiles in (FakeProfiles(current='performance'), FakeProfiles(offered=('balanced', 'power-saver'))):
            power = self.make(runner=profiles)
            status = power.begin()
            self.assertEqual(status['state'], 'active')
            self.assertIsNone(status['original_profile'])
            power.end()
            self.assertEqual(profiles.sets(), [])
        self.assertIn('no performance power mode', status['message'])

    def test_refused_profile_change_is_partial_and_never_restored(self):
        power = self.make(runner=FakeProfiles(refuse=True))
        status = power.begin()
        self.assertEqual(status['state'], 'partial')
        self.assertIn('could not be turned on', status['message'])
        self.assertIn('Access denied', status['message'])
        power.end()
        self.assertEqual(self.profiles.sets(), ['performance'])

    def test_no_tools_at_all_is_unavailable(self):
        power = self.make(systemd_inhibit='', powerprofilesctl='')
        status = power.begin()
        self.assertEqual(status['state'], 'unavailable')
        self.assertIn('not available', status['message'])

    def test_dbus_fallback_when_powerprofilesctl_is_absent(self):
        for service in ('org.freedesktop.UPower.PowerProfiles', 'net.hadess.PowerProfiles'):
            bus = FakeBus(service)
            power = self.make(runner=bus, powerprofilesctl='', busctl='/usr/bin/busctl')
            status = power.begin()
            self.assertEqual((status['power_profile'], status['original_profile']), ('performance', 'balanced'))
            self.assertEqual(bus.current, 'performance')
            power.end()
            self.assertEqual(bus.current, 'balanced')
            writes = [call for call in bus.calls if call[2] == 'set-property']
            self.assertEqual([call[3] for call in writes], [service, service])
            self.assertEqual([call[-1] for call in writes], ['performance', 'balanced'])

    def test_profile_list_parsers(self):
        listing = ('  performance:\n    CpuDriver:\tamd_pstate\n    Degraded:   no\n\n* balanced:\n'
                   '    CpuDriver:\tamd_pstate\n\n  power-saver:\n    PlatformDriver:\tplatform_profile\n')
        self.assertEqual(parse_profile_list(listing), (['performance', 'balanced', 'power-saver'], 'balanced'))
        self.assertEqual(parse_profile_list(''), ([], None))
        self.assertEqual(parse_bus_profiles([{'Profile': {'type': 's', 'data': 'balanced'}}, {'Profile': 'performance'},
                                             {'Profile': {'data': 'Bad Name'}}, 'junk']), ['balanced', 'performance'])
        self.assertEqual(parse_bus_profiles(None), [])

    def test_runner_errors_never_escape(self):
        def broken(arguments, timeout):
            raise subprocess.TimeoutExpired(arguments, timeout)
        power = self.make(runner=broken)
        self.assertEqual(power.begin()['state'], 'active')  # sleep blocked; profile service unreadable
        self.assertEqual(power.end()['state'], 'idle')


# SteamVR asynchronous reprojection ----------------------------------------

def cap_bytes(revision=2, permitted=1 << 23, inheritable=0, effective=True, rootid=0):
    magic = (revision << 24) | (1 if effective else 0)
    if revision == 1:
        return struct.pack('<III', magic, permitted, inheritable)
    raw = struct.pack('<IIIII', magic, permitted & 0xFFFFFFFF, inheritable & 0xFFFFFFFF,
                      permitted >> 32, inheritable >> 32)
    return raw + (struct.pack('<I', rootid) if revision == 3 else b'')


def xattr_error(code):
    def getxattr(path, name):
        raise OSError(code, os.strerror(code))
    return getxattr


class CapabilityParserTests(unittest.TestCase):
    def test_bytes_written_by_setcap_cap_sys_nice_eip(self):
        # `setcap cap_sys_nice=eip file` then os.getxattr(file, 'security.capability').
        caps = parse_vfs_cap(bytes.fromhex('0100000200008000000080000000000000000000'))
        self.assertEqual((caps['revision'], caps['sys_nice'], caps['effective']), (2, True, True))
        self.assertEqual(caps['inheritable'], 1 << 23)
        self.assertIsNone(caps['rootid'])

    def test_version_2_with_and_without_cap_sys_nice_and_effective_flag(self):
        self.assertTrue(parse_vfs_cap(cap_bytes())['sys_nice'])
        self.assertFalse(parse_vfs_cap(cap_bytes(permitted=1 << 13))['sys_nice'])  # cap_net_raw only
        self.assertFalse(parse_vfs_cap(cap_bytes(permitted=1 << (32 + 23)))['sys_nice'])  # capability 55, not 23
        self.assertFalse(parse_vfs_cap(cap_bytes(effective=False))['effective'])
        self.assertTrue(parse_vfs_cap(cap_bytes(permitted=(1 << 23) | (1 << 40)))['sys_nice'])

    def test_version_3_carries_root_id_and_version_1_has_one_pair(self):
        caps = parse_vfs_cap(cap_bytes(revision=3, rootid=100000))
        self.assertEqual((caps['revision'], caps['rootid'], caps['sys_nice']), (3, 100000, True))
        self.assertFalse(parse_vfs_cap(cap_bytes(revision=3, permitted=0))['sys_nice'])
        self.assertFalse(parse_vfs_cap(cap_bytes(revision=3, effective=False))['effective'])
        caps = parse_vfs_cap(cap_bytes(revision=1))
        self.assertEqual((caps['revision'], caps['sys_nice']), (1, True))

    def test_malformed_data_is_rejected_like_the_kernel_does(self):
        for raw in (b'', b'\x01\x00', cap_bytes() + b'\x00' * 4, cap_bytes(revision=3)[:20],
                    struct.pack('<IIIII', 0x04000001, 1 << 23, 0, 0, 0), 'text'):
            with self.assertRaises(ValueError, msg=repr(raw)):
                parse_vfs_cap(raw)

    def test_has_cap_sys_nice_reads_the_attribute_directly(self):
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes()):
            self.assertEqual(has_cap_sys_nice('/x'), {'present': True, 'effective': True, 'error': '', 'supported': True})
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes(effective=False)):
            result = has_cap_sys_nice('/x')
            self.assertEqual((result['present'], result['effective']), (True, False))
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes(revision=3, rootid=100000)):
            self.assertFalse(has_cap_sys_nice('/x')['present'])  # written inside a container's namespace
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes(revision=3, rootid=0)):
            self.assertTrue(has_cap_sys_nice('/x')['present'])
        with mock.patch.object(steamvr_async.os, 'getxattr', xattr_error(errno.ENODATA)):
            self.assertEqual(has_cap_sys_nice('/x'), {'present': False, 'effective': False, 'error': '', 'supported': True})
        with mock.patch.object(steamvr_async.os, 'getxattr', xattr_error(errno.EOPNOTSUPP)):
            self.assertEqual(has_cap_sys_nice('/x'), {'present': False, 'effective': False, 'error': '', 'supported': False})
        with mock.patch.object(steamvr_async.os, 'getxattr', xattr_error(errno.EACCES)):
            result = has_cap_sys_nice('/x')
            self.assertFalse(result['present'])
            self.assertIn('Permission denied', result['error'])
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: b'\x00\x00\x00\x02'):
            self.assertTrue(has_cap_sys_nice('/x')['error'])


class SteamVRRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.runtime = self.root / 'Steam/steamapps/common/SteamVR'
        self.linux64 = self.runtime / 'bin/linux64'
        self.linux64.mkdir(parents=True)
        self.launcher = self.linux64 / 'vrcompositor-launcher'
        self.launcher.write_bytes(b'\x7fELF')
        self.registry = self.root / 'openvrpaths.vrpath'
        self.write_registry([str(self.runtime)])
        tools = self.root / 'tools'
        tools.mkdir()
        self.pkexec, self.setcap = tools / 'pkexec', tools / 'setcap'
        self.pkexec.touch()
        self.setcap.touch()
        self.setup_log = self.root / 'SteamVRLauncherSetup.log'
        self.calls, self.granted, self.answer = [], False, completed(0)
        # Temporary folders are often mounted nosuid (Fedora's /tmp is); only the
        # nosuid test below should see that.
        patcher = mock.patch.object(steamvr_async.os, 'statvfs', lambda path: SimpleNamespace(f_flag=0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_registry(self, runtimes, bom=True):
        text = json.dumps({'config': [str(self.root / 'config')], 'runtime': runtimes, 'version': 1})
        self.registry.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode())

    def runner(self, arguments, timeout):
        self.calls.append((list(arguments), timeout))
        if self.answer.returncode == 0:
            self.granted = True
        return self.answer

    def getxattr(self, path, name):
        self.assertEqual(name, 'security.capability')
        if not self.granted:
            raise OSError(errno.ENODATA, 'No data available')
        return cap_bytes()

    def check(self, pkexec=None):
        return AsyncReprojectionCheck(registry=self.registry, setup_log=str(self.setup_log), runner=self.runner,
                                      pkexec=str(self.pkexec) if pkexec is None else pkexec, setcap=str(self.setcap),
                                      log=lambda line: None)

    def test_runtime_dirs_tolerate_bom_and_skip_missing_relative_and_extra_entries(self):
        extra = [self.root / ('extra%d' % index) for index in range(5)]
        for path in extra:
            path.mkdir()
        self.write_registry([str(self.runtime), str(self.root / 'gone'), 'relative/SteamVR', 7, str(self.runtime)]
                            + [str(path) for path in extra])
        self.assertEqual(steamvr_runtime_dirs(self.registry), [str(self.runtime)] + [str(path) for path in extra[:3]])
        self.registry.write_text('{not json')
        self.assertEqual(steamvr_runtime_dirs(self.registry), [])
        self.registry.write_text('{"runtime": {"a": 1}}')
        self.assertEqual(steamvr_runtime_dirs(self.registry), [])
        self.assertEqual(steamvr_runtime_dirs(self.root / 'missing.vrpath'), [])

    def test_ready_when_the_capability_is_present_and_effective(self):
        self.granted = True
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            result = self.check().check()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['path'], str(self.launcher))
        self.assertFalse(result['can_repair'])

    def test_missing_capability_reports_setup_failure_and_offers_repair(self):
        self.setup_log.write_text('Setting up SteamVR\nError: setcap of vrcompositor-launcher failed.\n')
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            result = self.check().check()
            self.assertEqual(result['state'], 'missing')
            self.assertTrue(result['setup_log_error'])
            self.assertTrue(result['can_repair'])
            self.assertIn('cannot use background reprojection', result['message'])
            self.assertIn('Select Repair (asks for your password)', result['message'])
            result = self.check(pkexec=str(self.root / 'no-pkexec')).check()
            self.assertFalse(result['can_repair'])
            self.assertNotIn('Select Repair', result['message'])
        self.assertEqual(self.calls, [])  # checking never runs a command

    def test_incomplete_drive_without_support_and_nosuid_drive_are_explained(self):
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes(effective=False)):
            result = self.check().check()
            self.assertEqual((result['state'], result['can_repair']), ('missing', True))
            self.assertIn('incomplete', result['message'])
        with mock.patch.object(steamvr_async.os, 'getxattr', xattr_error(errno.EOPNOTSUPP)):
            result = self.check().check()
            self.assertEqual((result['state'], result['can_repair']), ('missing', False))
            self.assertIn('cannot store', result['message'])
        with mock.patch.object(steamvr_async.os, 'getxattr', lambda path, name: cap_bytes()), \
                mock.patch.object(steamvr_async.os, 'statvfs', lambda path: SimpleNamespace(f_flag=os.ST_NOSUID)):
            result = self.check().check()
            self.assertEqual((result['state'], result['can_repair']), ('missing', False))
            self.assertIn('nosuid', result['message'])
        with mock.patch.object(steamvr_async.os, 'getxattr', xattr_error(errno.EACCES)):
            self.assertEqual(self.check().check()['state'], 'unknown')

    def test_not_installed(self):
        self.registry.unlink()
        self.assertEqual(self.check().check()['state'], 'not_installed')
        self.write_registry([str(self.runtime)])
        self.launcher.unlink()
        self.assertEqual(self.check().check()['state'], 'not_installed')

    def test_repair_runs_setcap_through_pkexec_then_checks_again(self):
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            checker = self.check()
            result = checker.repair()
        self.assertEqual(self.calls, [([str(self.pkexec), str(self.setcap), 'cap_sys_nice=eip', str(self.launcher)], 120)])
        self.assertEqual(result['state'], 'ready')
        self.assertTrue(result['repaired'])
        self.assertTrue(result['restart_required'])
        self.assertIn('next time it starts', result['message'])
        self.assertEqual(checker.status()['state'], 'ready')

    def test_repair_is_not_attempted_when_ready_or_without_pkexec(self):
        self.granted = True
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            self.assertFalse(self.check().repair()['repaired'])
            self.granted = False
            self.assertFalse(self.check(pkexec=str(self.root / 'no-pkexec')).repair()['repaired'])
            result = self.check(pkexec='').repair(str(self.launcher))
        self.assertFalse(result['repaired'])
        self.assertIn('pkexec', result['message'])
        self.assertEqual(self.calls, [])

    def test_repair_rejects_paths_outside_the_runtime_symlinks_and_other_files(self):
        outside = self.root / 'Elsewhere/bin/linux64'
        outside.mkdir(parents=True)
        (outside / 'vrcompositor-launcher').write_bytes(b'\x7fELF')
        second = self.root / 'SecondRuntime/bin/linux64'
        second.mkdir(parents=True)
        (second / 'vrcompositor-launcher').symlink_to(outside / 'vrcompositor-launcher')
        (self.linux64 / 'vrserver').write_bytes(b'\x7fELF')
        self.write_registry([str(self.runtime), str(self.root / 'SecondRuntime')])
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            checker = self.check()
            for path in (outside / 'vrcompositor-launcher', second / 'vrcompositor-launcher',
                         self.linux64 / 'vrserver', 'Steam/steamapps/common/SteamVR/bin/linux64/vrcompositor-launcher',
                         str(self.launcher) + '\n', self.linux64 / 'missing' / 'vrcompositor-launcher'):
                result = checker.repair(path)
                self.assertFalse(result['repaired'], path)
                self.assertIn('Nothing was changed', result['message'])
        self.assertEqual(self.calls, [])

    def test_dismissed_or_refused_password_prompt(self):
        cases = ((completed(126, '', 'Error executing command as another user: Request dismissed'), 'closed'),
                 (completed(127, '', 'Error executing command as another user: Not authorized'), 'not granted'),
                 (completed(127, '', 'Error executing command as another user: No authentication agent found.'),
                  'Desktop Mode'),
                 (completed(1, '', 'Failed to set capabilities on file: Operation not supported'), 'did not complete'))
        with mock.patch.object(steamvr_async.os, 'getxattr', self.getxattr):
            for answer, expected in cases:
                self.answer = answer
                result = self.check().repair()
                self.assertFalse(result['repaired'])
                self.assertEqual(result['state'], 'missing')
                self.assertIn(expected, result['message'])
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call[0][0] == str(self.pkexec) for call in self.calls))


# Headset audio ------------------------------------------------------------

def port(name, description, kind, priority, availability='available'):
    return {'name': name, 'description': description, 'type': kind, 'priority': priority,
            'availability_group': '', 'availability': availability}


def pactl_device(index, name, description, properties, ports=(), active_port=None, monitor_source=None):
    """The object shape `pactl -f json list sinks|sources` prints (PulseAudio 16+ client)."""
    return {'index': index, 'state': 'SUSPENDED', 'name': name, 'description': description, 'driver': 'PipeWire',
            'sample_specification': 's32le 2ch 48000Hz', 'channel_map': 'front-left,front-right',
            'owner_module': 4294967295, 'mute': False,
            'volume': {'front-left': {'value': 39321, 'value_percent': '60%', 'db': '-13.31 dB'},
                       'front-right': {'value': 39321, 'value_percent': '60%', 'db': '-13.31 dB'}},
            'balance': 0.0, 'base_volume': {'value': 65536, 'value_percent': '100%', 'db': '0.00 dB'},
            'monitor_source': monitor_source, 'latency': {'actual': 0.0, 'configured': 0.0},
            'flags': ['HARDWARE', 'HW_MUTE_CTRL', 'HW_VOLUME_CTRL', 'DECIBEL_VOLUME', 'LATENCY'],
            'properties': dict(properties, **{'node.name': name}), 'ports': list(ports), 'active_port': active_port,
            'formats': ['pcm']}


LAPTOP_SINK = pactl_device(
    45, 'alsa_output.pci-0000_c4_00.6.analog-stereo', 'Family 17h/19h/1ah HD Audio Controller Speaker',
    {'alsa.card_name': 'HD-Audio Generic', 'alsa.long_card_name': 'HD-Audio Generic at 0x90680000 irq 92',
     'alsa.name': 'ALC269VB Analog', 'device.description': 'Family 17h/19h/1ah HD Audio Controller',
     'device.product.name': 'Family 17h/19h/1ah HD Audio Controller',
     'device.vendor.name': 'Advanced Micro Devices, Inc. [AMD]', 'device.bus': 'pci', 'node.nick': 'ALC269VB Analog',
     'device.profile.name': 'analog-stereo', 'device.profile.description': 'Analog Stereo', 'media.class': 'Audio/Sink'},
    [port('analog-output-speaker', 'Speakers', 'Speaker', 10000),
     port('analog-output-headphones', 'Headphones', 'Headphones', 9900, 'not available')],
    'analog-output-speaker', 'alsa_output.pci-0000_c4_00.6.analog-stereo.monitor')
INDEX_SINK = pactl_device(
    52, 'alsa_output.pci-0000_c4_00.1.hdmi-stereo-extra1',
    'Rembrandt Radeon High Definition Audio Controller Digital Stereo (HDMI 2)',
    {'alsa.card_name': 'HD-Audio Generic', 'alsa.long_card_name': 'HD-Audio Generic at 0x90688000 irq 91',
     'alsa.name': 'HDMI 1', 'device.description': 'Rembrandt Radeon High Definition Audio Controller',
     'device.vendor.name': 'Advanced Micro Devices, Inc. [AMD/ATI]', 'device.bus': 'pci', 'node.nick': 'Index HMD',
     'device.profile.name': 'hdmi-stereo-extra1', 'device.profile.description': 'Digital Stereo (HDMI 2)',
     'media.class': 'Audio/Sink'},
    [port('hdmi-output-1', 'HDMI / DisplayPort 2', 'HDMI', 5800)], 'hdmi-output-1',
    'alsa_output.pci-0000_c4_00.1.hdmi-stereo-extra1.monitor')
GAMING_HEADSET_SINK = pactl_device(
    61, 'alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo', 'Arctis Nova 7 Analog Stereo',
    {'device.product.name': 'Arctis Nova 7', 'device.vendor.name': 'SteelSeries ApS', 'device.bus': 'usb',
     'alsa.card_name': 'Arctis Nova 7', 'device.description': 'Arctis Nova 7'},
    [port('analog-output', 'Headset', 'Headset', 9900)], 'analog-output',
    'alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo.monitor')
STEAM_DECK_SINK = pactl_device(
    70, 'alsa_output.pci-0000_04_00.5-platform-acp5x_mach.0.HiFi__hw_acp5x_1__sink', 'acp5x Speaker',
    {'alsa.card_name': 'acp5x', 'alsa.long_card_name': 'Valve-Jupiter-1-A1-Jupiter', 'device.bus': 'pci',
     'device.description': 'Speaker'},
    [port('[Out] Speaker', 'Speaker', 'Speaker', 100)], '[Out] Speaker')


def monitor_of(sink):
    return pactl_device(sink['index'] + 1, sink['name'] + '.monitor', 'Monitor of ' + sink['description'],
                        {'device.class': 'monitor', 'node.nick': sink['properties'].get('node.nick', '')},
                        monitor_source=sink['name'])


LAPTOP_MIC = pactl_device(
    47, 'alsa_input.pci-0000_c4_00.6.analog-stereo', 'Family 17h/19h/1ah HD Audio Controller Digital Microphone',
    {'alsa.card_name': 'HD-Audio Generic', 'device.bus': 'pci', 'device.profile.name': 'analog-stereo'},
    [port('analog-input-internal-mic', 'Digital Microphone', 'Mic', 8900)], 'analog-input-internal-mic')
INDEX_MIC = pactl_device(
    58, 'alsa_input.usb-Valve_Corporation_Valve_VR_Radio___HMD_Mic_0000000000-01.mono-fallback',
    'Valve VR Radio & HMD Mic Mono',
    {'alsa.card_name': 'Valve VR Radio & HMD Mic',
     'alsa.long_card_name': 'Valve Corporation Valve VR Radio & HMD Mic at usb-0000:c6:00.4-1.1, full speed',
     'device.product.name': 'Valve VR Radio & HMD Mic', 'device.vendor.name': 'Valve Software', 'device.bus': 'usb',
     'device.vendor.id': '0x28de', 'device.product.id': '0x2102', 'node.nick': 'Valve VR Radio & HMD Mic',
     'device.profile.name': 'mono-fallback', 'device.profile.description': 'Mono'},
    [port('analog-input-mic', 'Microphone', 'Mic', 8700)], 'analog-input-mic')
GAMING_HEADSET_MIC = pactl_device(
    63, 'alsa_input.usb-SteelSeries_Arctis_Nova_7-00.mono-fallback', 'Arctis Nova 7 Mono',
    {'device.product.name': 'Arctis Nova 7', 'device.bus': 'usb'}, [port('analog-input', 'Headset Microphone', 'Headset', 8700)],
    'analog-input')
BEYOND_DISPLAY_SINK = pactl_device(  # DisplayPort audio endpoint of a display without speakers
    80, 'alsa_output.pci-0000_03_00.1.hdmi-stereo', 'Navi 31 HDMI/DP Audio Digital Stereo (HDMI)',
    {'device.description': 'Navi 31 HDMI/DP Audio', 'device.bus': 'pci', 'node.nick': 'Beyond',
     'device.profile.name': 'hdmi-stereo'},
    [port('hdmi-output-0', 'HDMI / DisplayPort', 'HDMI', 5900)], 'hdmi-output-0', 'alsa_output.pci-0000_03_00.1.hdmi-stereo.monitor')
BEYOND_STRAP_SINK = pactl_device(
    82, 'alsa_output.usb-Bigscreen_Bigscreen_Beyond_Audio_Strap_0001-00.analog-stereo',
    'Bigscreen Beyond Audio Strap Analog Stereo',
    {'device.product.name': 'Bigscreen Beyond Audio Strap', 'device.vendor.name': 'Bigscreen', 'device.bus': 'usb',
     'alsa.card_name': 'Bigscreen Beyond Audio Strap'},
    [port('analog-output', 'Analog Output', 'Analog', 9900)], 'analog-output',
    'alsa_output.usb-Bigscreen_Bigscreen_Beyond_Audio_Strap_0001-00.analog-stereo.monitor')
BEYOND_STRAP_MIC = pactl_device(
    84, 'alsa_input.usb-Bigscreen_Bigscreen_Beyond_Audio_Strap_0001-00.mono-fallback',
    'Bigscreen Beyond Audio Strap Mono',
    {'device.product.name': 'Bigscreen Beyond Audio Strap', 'device.vendor.name': 'Bigscreen', 'device.bus': 'usb'},
    [port('analog-input-mic', 'Microphone', 'Mic', 8700)], 'analog-input-mic')

INDEX_SINKS = [LAPTOP_SINK, INDEX_SINK, GAMING_HEADSET_SINK, STEAM_DECK_SINK]
INDEX_SOURCES = [monitor_of(LAPTOP_SINK), monitor_of(INDEX_SINK), LAPTOP_MIC, INDEX_MIC, GAMING_HEADSET_MIC]
BEYOND_SINKS = [LAPTOP_SINK, BEYOND_DISPLAY_SINK, BEYOND_STRAP_SINK]
BEYOND_SOURCES = [monitor_of(LAPTOP_SINK), monitor_of(BEYOND_STRAP_SINK), LAPTOP_MIC, BEYOND_STRAP_MIC]


class FakePactl:
    def __init__(self, sinks, sources, default_sink, default_source):
        self.sinks, self.sources = list(sinks), list(sources)
        self.defaults = {'sink': default_sink, 'source': default_source}
        self.calls, self.fail = [], False

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        assert arguments[0] == '/usr/bin/pactl', arguments
        if self.fail:
            return completed(1, '', 'Connection failure: Connection refused')
        command = arguments[1:]
        if command[:3] == ['-f', 'json', 'list']:
            return completed(0, json.dumps(self.sinks if command[3] == 'sinks' else self.sources))
        kind = command[0][len('get-default-'):]
        if command[0].startswith('get-default-'):
            return completed(0, self.defaults[kind] + '\n')
        if command[0].startswith('set-default-') and len(command) == 2:
            if command[1] not in [device['name'] for device in (self.sinks if kind == 'sink' else self.sources)]:
                return completed(1, '', 'Failure: No such entity')
            self.defaults[kind] = command[1]
            return completed(0)
        raise AssertionError(arguments)

    def sets(self):
        return [call[1:] for call in self.calls if call[1].startswith('set-default-')]


class HeadsetAudioMatchTests(unittest.TestCase):
    def test_index_speakers_are_displayport_audio_and_microphone_is_the_usb_radio(self):
        for model in ('Index', 'Valve Index', 'index'):
            sink, source, explanation = match_headset_devices(INDEX_SINKS, INDEX_SOURCES, model, {})
            self.assertEqual(sink, INDEX_SINK['name'])
            self.assertEqual(source, INDEX_MIC['name'])
        self.assertIn('index hmd', explanation)
        self.assertIn('hmd mic', explanation)

    def test_beyond_uses_its_usb_audio_and_never_the_display_connection(self):
        sink, source, _ = match_headset_devices(BEYOND_SINKS, BEYOND_SOURCES, 'Beyond', None)
        self.assertEqual((sink, source), (BEYOND_STRAP_SINK['name'], BEYOND_STRAP_MIC['name']))
        sink, source, explanation = match_headset_devices([LAPTOP_SINK, BEYOND_DISPLAY_SINK], [LAPTOP_MIC],
                                                          'Bigscreen Beyond 2e', {})
        self.assertEqual((sink, source), (None, None))
        self.assertIn('no headset device found', explanation)

    def test_no_match_means_no_change_never_a_guess(self):
        sinks, sources = [LAPTOP_SINK, GAMING_HEADSET_SINK, STEAM_DECK_SINK], [LAPTOP_MIC, GAMING_HEADSET_MIC]
        for model in ('Index', 'Beyond', 'Vive Pro 2', ''):
            self.assertEqual(match_headset_devices(sinks, sources, model, {})[:2], (None, None), model)

    def test_monitors_are_never_microphones(self):
        sources = [monitor_of(INDEX_SINK), dict(monitor_of(LAPTOP_SINK), name='index-hmd-capture',
                                                 description='Index HMD loopback')]
        self.assertIsNone(match_headset_devices([], sources, 'Index', {})[1])

    def test_overrides_win_when_present_and_fall_back_when_absent_or_invalid(self):
        sink, source, explanation = match_headset_devices(
            INDEX_SINKS, INDEX_SOURCES, 'Index', {'sink': LAPTOP_SINK['name'], 'source': GAMING_HEADSET_MIC['name']})
        self.assertEqual((sink, source), (LAPTOP_SINK['name'], GAMING_HEADSET_MIC['name']))
        self.assertIn('chosen in settings', explanation)
        sink, source, explanation = match_headset_devices(
            INDEX_SINKS, INDEX_SOURCES, 'Index', {'sink': 'alsa_output.unplugged', 'source': '-rm -rf'})
        self.assertEqual((sink, source), (INDEX_SINK['name'], INDEX_MIC['name']))
        self.assertIn('not connected', explanation)
        self.assertIn('not valid', explanation)

    def test_unknown_model_matches_its_own_name(self):
        vive = pactl_device(90, 'alsa_output.usb-HTC_Vive_Pro_2-00.analog-stereo', 'HTC Vive Pro 2 Analog Stereo',
                            {'device.bus': 'usb'})
        self.assertEqual(match_headset_devices([LAPTOP_SINK, vive], [], 'Vive Pro 2', {})[0], vive['name'])

    def test_unsafe_device_names_are_never_chosen(self):
        unsafe = [dict(INDEX_SINK, name='hdmi output; rm -rf ~'), dict(INDEX_SINK, name='--help'),
                  dict(INDEX_SINK, name='x' * 256), dict(INDEX_SINK, name=None), 'garbage', None]
        self.assertIsNone(match_headset_devices(unsafe, None, 'Index', {})[0])


class HeadsetAudioSessionTests(unittest.TestCase):
    def make(self, sinks=INDEX_SINKS, sources=INDEX_SOURCES, default_sink=LAPTOP_SINK['name'],
             default_source=LAPTOP_MIC['name'], pactl='/usr/bin/pactl'):
        self.pactl = FakePactl(sinks, sources, default_sink, default_source)
        self.lines = []
        return HeadsetAudio(runner=self.pactl, log=self.lines.append, pactl=pactl)

    def test_routes_both_directions_and_restores_them(self):
        audio = self.make()
        status = audio.begin('Index')
        self.assertEqual(status['state'], 'routed')
        self.assertEqual((status['sink'], status['source']), (INDEX_SINK['name'], INDEX_MIC['name']))
        self.assertEqual(self.pactl.defaults, {'sink': INDEX_SINK['name'], 'source': INDEX_MIC['name']})
        status = audio.end()
        self.assertEqual(status['state'], 'idle')
        self.assertEqual(self.pactl.defaults, {'sink': LAPTOP_SINK['name'], 'source': LAPTOP_MIC['name']})
        self.assertIn('sound output and microphone are selected again', status['message'])
        calls = len(self.pactl.calls)
        audio.end()
        self.assertEqual(len(self.pactl.calls), calls)

    def test_restores_only_what_the_user_did_not_change(self):
        audio = self.make()
        audio.begin('Index')
        self.pactl.defaults['sink'] = GAMING_HEADSET_SINK['name']  # user picked other speakers mid-session
        status = audio.end()
        self.assertEqual(self.pactl.defaults, {'sink': GAMING_HEADSET_SINK['name'], 'source': LAPTOP_MIC['name']})
        self.assertIn('sound output was changed during the session', status['message'])
        self.assertIn('microphone is selected again', status['message'])

    def test_previous_device_that_disappeared_is_not_restored(self):
        audio = self.make()
        audio.begin('Index')
        self.pactl.sources.remove(LAPTOP_MIC)
        status = audio.end()
        self.assertEqual(self.pactl.defaults['source'], INDEX_MIC['name'])
        self.assertEqual(self.pactl.defaults['sink'], LAPTOP_SINK['name'])
        self.assertIn('previous microphone is no longer available', status['message'])
        self.assertEqual(self.pactl.sets()[-1], ['set-default-sink', LAPTOP_SINK['name']])

    def test_device_already_default_is_not_touched_or_restored(self):
        audio = self.make(default_sink=INDEX_SINK['name'])
        self.assertEqual(audio.begin('Index')['state'], 'routed')
        audio.end()
        self.assertEqual([call for call in self.pactl.sets() if call[0] == 'set-default-sink'], [])

    def test_partial_when_only_the_microphone_is_found(self):
        audio = self.make(sinks=[LAPTOP_SINK])
        status = audio.begin('Index')
        self.assertEqual((status['state'], status['sink'], status['source']), ('partial', None, INDEX_MIC['name']))
        self.assertIn("headset's sound output was not found", status['message'])
        self.assertEqual(self.pactl.sets(), [['set-default-source', INDEX_MIC['name']]])

    def test_disabled_unavailable_and_unmatched_change_nothing(self):
        audio = self.make()
        self.assertEqual(audio.begin('Index', enabled=False)['state'], 'unchanged')
        self.assertEqual(self.pactl.calls, [])
        self.assertEqual(self.make(pactl='').begin('Index')['state'], 'unavailable')
        audio = self.make()
        self.pactl.fail = True
        self.assertEqual(audio.begin('Index')['state'], 'unavailable')
        audio = self.make(sinks=[LAPTOP_SINK], sources=[LAPTOP_MIC])
        status = audio.begin('Index')
        self.assertEqual(status['state'], 'unchanged')
        self.assertIn('choose the devices by name', status['message'])
        self.assertEqual(self.pactl.sets(), [])

    def test_beyond_session_and_overrides(self):
        audio = self.make(sinks=BEYOND_SINKS, sources=BEYOND_SOURCES)
        status = audio.begin('Beyond', overrides={'source': LAPTOP_MIC['name']})
        self.assertEqual((status['sink'], status['source']), (BEYOND_STRAP_SINK['name'], LAPTOP_MIC['name']))
        self.assertEqual(self.pactl.sets(), [['set-default-sink', BEYOND_STRAP_SINK['name']]])
        audio.end()
        self.assertEqual(self.pactl.defaults['sink'], LAPTOP_SINK['name'])

    def test_devices_for_the_settings_ui_exclude_monitors(self):
        devices = self.make().devices()
        self.assertTrue(devices['available'])
        self.assertEqual([item['name'] for item in devices['sinks']], [device['name'] for device in INDEX_SINKS])
        self.assertEqual([item['name'] for item in devices['sources']],
                         [LAPTOP_MIC['name'], INDEX_MIC['name'], GAMING_HEADSET_MIC['name']])
        self.assertEqual(devices['sources'][1]['description'], 'Valve VR Radio & HMD Mic Mono')
        self.assertEqual((devices['default_sink'], devices['default_source']), (LAPTOP_SINK['name'], LAPTOP_MIC['name']))
        self.assertFalse(self.make(pactl='').devices()['available'])


# Wi-Fi diagnostics --------------------------------------------------------

STATION_DUMP = """Station 3c:84:6a:12:34:56 (on wlp1s0)
\tinactive time:\t12 ms
\trx bytes:\t1834651620
\trx packets:\t1386034
\ttx bytes:\t98113424
\ttx packets:\t512331
\ttx retries:\t20451
\ttx failed:\t17
\trx drop misc:\t312
\tbeacon loss:\t2
\tbeacon rx:\t35841
\tsignal:  \t-54 [-56, -57] dBm
\tsignal avg:\t-53 [-55, -56] dBm
\tbeacon signal avg:\t-52 dBm
\ttx bitrate:\t1200.9 MBit/s 80MHz HE-MCS 11 HE-NSS 2 HE-GI 0 HE-DCM 0
\ttx duration:\t92614103 us
\trx bitrate:\t1080.6 MBit/s 80MHz HE-MCS 10 HE-NSS 2 HE-GI 0 HE-DCM 0
\trx duration:\t0 us
\tlast ack signal:-55 dBm
\tavg ack signal:\t-54 dBm
\texpected throughput:\t540.5Mbps
\tauthorized:\tyes
\tauthenticated:\tyes
\tassociated:\tyes
\tpreamble:\tlong
\tWMM/WME:\tyes
\tMFP:\t\tyes
\tTDLS peer:\tno
\tDTIM period:\t1
\tbeacon interval:100
\tshort slot time:yes
\tconnected time:\t3671 seconds
\tassociated at [boottime]:\t1234.567s
\tassociated at:\t1759450000000 ms
\tcurrent time:\t1759453671000 ms
"""

MLO_STATION_DUMP = """Station 3c:84:6a:aa:bb:cc (on wlp192s0)
\tinactive time:\t8 ms
\trx packets:\t701234
\ttx packets:\t250001
\ttx retries:\t9100
\ttx failed:\t4
\tbeacon loss:\t0
\tsignal:  \t-49 dBm
\ttx bitrate:\t2882.4 MBit/s 320MHz EHT-MCS 13 EHT-NSS 2 EHT-GI 0
\trx bitrate:\t2594.1 MBit/s 320MHz EHT-MCS 12 EHT-NSS 2 EHT-GI 0
\tconnected time:\t120 seconds
\tMLD address:\t3c:84:6a:aa:bb:cc
\tLink 0:
\t\taddress:\t3c:84:6a:aa:bb:c0
\t\tfreq:\t5180
\t\trx packets:\t300000
\t\ttx packets:\t100000
\t\ttx retries:\t4000
\t\ttx failed:\t1
\t\tsignal:  \t-47 [-48, -50] dBm
\t\tsignal avg:\t-48 dBm
\t\ttx bitrate:\t1729.2 MBit/s 160MHz EHT-MCS 11 EHT-NSS 2 EHT-GI 0
\t\trx bitrate:\t1441.1 MBit/s 160MHz EHT-MCS 9 EHT-NSS 2 EHT-GI 0
\tLink 1:
\t\taddress:\t3c:84:6a:aa:bb:c1
\t\tfreq:\t6135
\t\trx packets:\t401234
\t\ttx packets:\t150001
\t\ttx retries:\t5100
\t\ttx failed:\t3
\t\tsignal:  \t-58 dBm
\t\ttx bitrate:\t2882.4 MBit/s 320MHz EHT-MCS 13 EHT-NSS 2 EHT-GI 0
\trx drop misc:\t9
\tauthorized:\tyes
"""

LINK_OUTPUT = """Connected to 3c:84:6a:12:34:56 (on wlp1s0)
\tSSID: HomeNetwork
\tfreq: 5180.0
\tRX: 1834651620 bytes (1386034 packets)
\tTX: 98113424 bytes (512331 packets)
\tsignal: -54 dBm
\trx bitrate: 1080.6 MBit/s 80MHz HE-MCS 10 HE-NSS 2 HE-GI 0 HE-DCM 0
\ttx bitrate: 1200.9 MBit/s 80MHz HE-MCS 11 HE-NSS 2 HE-GI 0 HE-DCM 0
\tbss flags: short-slot-time
\tdtim period: 1
\tbeacon int: 100
"""

MLO_LINK_OUTPUT = """Connected to 3c:84:6a:aa:bb:cc (on wlp192s0)
\tSSID: HomeNetwork
\tLink 0 BSSID 3c:84:6a:aa:bb:c0
\t\tfreq: 5180.0
\t\tsignal: -47 dBm
\t - link ID  1 link addr 3c:84:6a:aa:bb:c1
\t   freq: 6135.0
\t   signal: -58 dBm
\tbeacon int: 100
"""


def station(address='3c:84:6a:12:34:56', retries=0, failed=0, beacon_loss=0, tx_packets=1000, connected=60):
    return ('Station %s (on wlp1s0)\n\ttx packets:\t%d\n\ttx retries:\t%d\n\ttx failed:\t%d\n\tbeacon loss:\t%d\n'
            '\tsignal:  \t-60 dBm\n\tconnected time:\t%d seconds\n' % (address, tx_packets, retries, failed,
                                                                         beacon_loss, connected))


class StationDumpParserTests(unittest.TestCase):
    def test_classic_station_dump(self):
        [entry] = parse_station_dump(STATION_DUMP)
        self.assertEqual((entry['kind'], entry['address'], entry['interface']), ('station', '3c:84:6a:12:34:56', 'wlp1s0'))
        expected = {'tx_retries': 20451, 'tx_failed': 17, 'beacon_loss': 2, 'signal_dbm': -54, 'signal_avg_dbm': -53,
                    'tx_bitrate_mbps': 1200.9, 'rx_bitrate_mbps': 1080.6, 'connected_time_s': 3671,
                    'rx_packets': 1386034, 'tx_packets': 512331, 'rx_drop_misc': 312, 'last_ack_signal_dbm': -55,
                    'expected_throughput_mbps': 540.5, 'inactive_ms': 12}
        self.assertEqual({key: entry[key] for key in expected}, expected)
        self.assertEqual(entry['links'], [])

    def test_wifi7_multi_link_station(self):
        [entry] = parse_station_dump(MLO_STATION_DUMP)
        self.assertEqual((entry['tx_retries'], entry['signal_dbm'], entry['tx_bitrate_mbps']), (9100, -49, 2882.4))
        self.assertEqual(entry['mld_address'], '3c:84:6a:aa:bb:cc')
        first, second = entry['links']
        self.assertEqual((first['link_id'], first['address'], first['freq_mhz']), (0, '3c:84:6a:aa:bb:c0', 5180.0))
        self.assertEqual((first['tx_retries'], first['signal_dbm'], first['signal_avg_dbm']), (4000, -47, -48))
        self.assertEqual((second['link_id'], second['freq_mhz'], second['tx_failed']), (1, 6135.0, 3))
        self.assertEqual(entry['rx_drop_misc'], 9)  # back at station level after the link block
        self.assertNotIn('rx_drop_misc', second)

    def test_link_output_and_mlo_link_headers(self):
        [entry] = parse_station_dump(LINK_OUTPUT)
        self.assertEqual((entry['kind'], entry['address'], entry['freq_mhz']), ('connection', '3c:84:6a:12:34:56', 5180.0))
        self.assertEqual((entry['rx_packets'], entry['tx_bytes'], entry['signal_dbm']), (1386034, 98113424, -54))
        [entry] = parse_station_dump(MLO_LINK_OUTPUT)
        self.assertEqual([(link['link_id'], link['address'], link['signal_dbm']) for link in entry['links']],
                         [(0, '3c:84:6a:aa:bb:c0', -47), (1, '3c:84:6a:aa:bb:c1', -58)])

    def test_unknown_or_broken_input_never_raises(self):
        for text in (None, '', 'Not connected.', 'command failed: No such device (-19)', '\x00\xff garbage:\n:::\n',
                     'Station zz:zz (on x)\n\tsignal: unknown\n', 42):
            self.assertEqual(parse_station_dump(text), [], repr(text))
        [entry] = parse_station_dump('Station 02:00:00:00:00:01 (on wlan0)\n\tsignal: n/a\n\ttx retries: lots\n'
                                     '\tlink 7 unexpected\n\t\tnew field: 3\n\ttx bitrate: 6.0 MBit/s\n')
        self.assertNotIn('signal_dbm', entry)
        self.assertEqual(entry['links'], [{'link_id': 7}])
        self.assertEqual(entry['tx_bitrate_mbps'], 6.0)


class WifiDiagnosticsTests(unittest.TestCase):
    def make(self, outputs=(), nmcli='/usr/bin/nmcli'):
        self.now = [100.0]
        self.outputs = list(outputs)
        self.calls = []
        self.answers = {}
        self.lines = []
        return WifiDiagnostics(runner=self.runner, iw='/usr/sbin/iw', nmcli=nmcli, log=self.lines.append,
                               clock=lambda: self.now[0])

    def runner(self, arguments, timeout):
        self.calls.append(list(arguments))
        if arguments[-2:] == ['station', 'dump']:
            return completed(0, self.outputs.pop(0))
        for key, answer in self.answers.items():
            if all(part in arguments for part in key):
                return answer
        raise AssertionError(arguments)

    def test_rates_since_previous_sample_and_totals_since_begin(self):
        wifi = self.make([station(retries=100, failed=1, tx_packets=1000), station(retries=160, failed=3, tx_packets=1600,
                                                                                   beacon_loss=1, connected=65),
                          station(retries=10, failed=0, tx_packets=100, connected=2),
                          station('3c:84:6a:12:34:99', retries=5, tx_packets=50, connected=1)])
        first = wifi.begin('wlp1s0')
        self.assertTrue(first['connected'])
        self.assertIsNone(first['tx_retries_per_s'])
        self.now[0] += 5
        second = wifi.sample()
        self.assertEqual((second['interval_s'], second['tx_retries_per_s'], second['tx_failed_per_s']), (5.0, 12.0, 0.4))
        self.assertEqual((second['beacon_loss_delta'], second['tx_retry_ratio']), (1, 0.1))
        self.assertFalse(second['reassociated'])
        self.now[0] += 5
        third = wifi.sample()
        self.assertTrue(third['reassociated'])
        self.assertFalse(third['roamed'])
        self.assertIsNone(third['tx_retries_per_s'])  # no rate across a reconnect
        self.now[0] += 5
        fourth = wifi.sample()
        self.assertTrue(fourth['roamed'])
        summary = wifi.summary()['since_begin']
        self.assertEqual((summary['tx_retries'], summary['tx_failed'], summary['beacon_loss']), (75, 2, 1))
        self.assertEqual((summary['reassociations'], summary['roams'], summary['samples']), (2, 1, 4))
        self.assertEqual((summary['duration_s'], summary['tx_retries_per_s']), (15.0, 5.0))
        self.assertEqual((summary['min_signal_dbm'], summary['max_signal_dbm']), (-60, -60))
        self.assertEqual(self.calls, [['/usr/sbin/iw', 'dev', 'wlp1s0', 'station', 'dump']] * 4)  # one call each
        self.assertEqual(wifi.summary()['latest']['tx_retries'], 5)

    def test_multi_link_station_without_station_totals_sums_its_links(self):
        text = MLO_STATION_DUMP.replace('\ttx retries:\t9100\n', '').replace('\tsignal:  \t-49 dBm\n', '')
        sample = self.make([text]).sample('wlp192s0')
        self.assertEqual((sample['tx_retries'], sample['links'], sample['signal_dbm']), (9100, 2, -47))

    def test_invalid_interface_missing_iw_and_disconnected(self):
        wifi = self.make(['', ''])
        for name in ('wlan0; rm -rf /', '-wlan0', '', None, 'a' * 16, '..'):
            self.assertFalse(wifi.sample(name)['connected'])
        self.assertEqual(self.calls, [])
        self.assertIn('not connected', wifi.sample('wlp1s0')['message'])
        no_iw = WifiDiagnostics(runner=self.runner, iw='', nmcli='', log=self.lines.append)
        self.assertIn('iw tool', no_iw.sample('wlp1s0')['message'])
        self.assertIsNone(wifi.summary()['since_begin'])

    def test_valid_bssid(self):
        self.assertTrue(valid_bssid('3c:84:6a:12:34:56'))
        for value in ('3c:84:6a:12:34', '3c-84-6a-12-34-56', 'ff:ff:ff:ff:ff:ff', '3c:84:6a:12:34:56 ', None,
                      '01:00:5e:00:00:01', '00:00:00:00:00:00'):
            self.assertFalse(valid_bssid(value), value)

    def test_runner_errors_never_escape(self):
        def broken(arguments, timeout):
            raise OSError('No such file or directory')
        wifi = WifiDiagnostics(runner=broken, iw='/usr/sbin/iw', nmcli='/usr/bin/nmcli', log=lambda line: None)
        self.assertFalse(wifi.sample('wlp1s0')['connected'])
        self.assertNotIn('lock', wifi.summary())


if __name__ == '__main__':
    unittest.main()
