"""RC4 host checks: keep-awake, sleep after Stop, Wake-on-LAN readiness and the optional QoS policy.

Windows APIs and PowerShell are replaced by fakes; nothing here runs a system command.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host import power, qos
from host.power import (ES_CONTINUOUS, ES_SYSTEM_REQUIRED, KeepAwake, SleepAfterStop, adapter_addresses,
                        adapter_for_ip, link_speed_mbps, parse_adapter_addresses, parse_sleep_states,
                        parse_wake_readiness, wake_readiness)


def completed(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeKernel32:
    def __init__(self, result=0x80000000, error=None):
        self.result, self.error = result, error
        self.calls = []

    def SetThreadExecutionState(self, flags):
        self.calls.append((threading.get_ident(), flags))
        if self.error:
            raise self.error
        return self.result


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def test_request_is_set_and_cleared_on_one_dedicated_thread(self):
        kernel32 = FakeKernel32()
        keeper = KeepAwake(kernel32=kernel32, log=self.logs.append)
        self.assertEqual(keeper.status()['state'], 'idle')
        self.assertEqual(keeper.acquire()['state'], 'active')
        self.assertEqual(keeper.acquire()['state'], 'active')  # idempotent
        thread = keeper.thread
        self.assertEqual(len(kernel32.calls), 1)
        holder, flags = kernel32.calls[0]
        self.assertEqual(flags, ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        self.assertEqual(flags, 0x80000001)
        self.assertNotEqual(holder, threading.get_ident())
        self.assertEqual(keeper.release()['state'], 'idle')
        self.assertEqual(kernel32.calls[1], (holder, ES_CONTINUOUS))
        self.assertFalse(thread.is_alive())
        self.assertEqual(keeper.release()['state'], 'idle')  # idempotent
        self.assertEqual(len(kernel32.calls), 2)

    def test_reacquire_after_release_uses_a_new_thread(self):
        kernel32 = FakeKernel32()
        keeper = KeepAwake(kernel32=kernel32, log=self.logs.append)
        keeper.acquire()
        keeper.release()
        keeper.acquire()
        self.assertEqual(keeper.status()['state'], 'active')
        keeper.release()
        self.assertEqual([flags for _, flags in kernel32.calls], [0x80000001, 0x80000000, 0x80000001, 0x80000000])
        self.assertEqual(kernel32.calls[2][0], kernel32.calls[3][0])

    def test_refused_or_broken_api_is_reported_not_raised(self):
        for kernel32 in (FakeKernel32(result=0), FakeKernel32(error=OSError('access denied'))):
            keeper = KeepAwake(kernel32=kernel32, log=self.logs.append)
            status = keeper.acquire()
            self.assertEqual(status['state'], 'failed')
            self.assertIn('may go to sleep', status['message'])
            self.assertEqual(keeper.release()['state'], 'idle')

    def test_unsupported_without_windows(self):
        with mock.patch.object(power.os, 'name', 'posix'):
            keeper = KeepAwake(log=self.logs.append)
        self.assertEqual(keeper.acquire()['state'], 'unsupported')
        self.assertIsNone(keeper.thread)
        self.assertEqual(keeper.release()['state'], 'unsupported')


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SleepAfterStopTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.idle = True
        self.input_idle = None
        self.suspended = threading.Event()
        self.suspend_calls = 0
        self.logs = []
        self.timers = []

    def tearDown(self):
        for timer in self.timers:
            timer.close()

    def suspend(self):
        self.suspend_calls += 1
        self.suspended.set()

    def is_idle(self):
        if isinstance(self.idle, Exception):
            raise self.idle
        return self.idle

    def timer(self, minutes=10, check_seconds=60.0, suspend=None):
        timer = SleepAfterStop(minutes, self.is_idle, suspend=suspend or self.suspend, clock=self.clock,
                               log=self.logs.append, input_idle_seconds=lambda: self.input_idle,
                               check_seconds=check_seconds)
        self.timers.append(timer)
        return timer

    def test_counts_down_and_sleeps_when_time_is_up(self):
        timer = self.timer()
        status = timer.arm()
        self.assertEqual((status['state'], status['remaining_seconds']), ('armed', 600))
        self.assertIn('10 minutes', status['message'])
        self.clock.now += 100
        self.assertEqual(timer.status()['remaining_seconds'], 500)
        self.assertEqual(timer.poll(), 'waiting')
        self.clock.now += 500
        self.assertEqual(timer.poll(), 'suspended')
        self.assertEqual(self.suspend_calls, 1)
        status = timer.status()
        self.assertEqual((status['state'], status['remaining_seconds']), ('idle', None))
        self.assertEqual(timer.poll(), 'stale')
        self.assertEqual(self.suspend_calls, 1)

    def test_new_session_cancels(self):
        timer = self.timer()
        timer.arm()
        self.assertEqual(timer.cancel()['state'], 'cancelled')
        self.clock.now += 3600
        self.assertEqual(timer.poll(), 'stale')
        self.assertEqual(self.suspend_calls, 0)

    def test_session_or_steamvr_or_failed_check_cancels(self):
        for idle, words in ((False, 'SteamVR is running'), (RuntimeError('tasklist failed'), 'could not check')):
            self.idle = idle
            timer = self.timer()
            timer.arm()
            self.assertEqual(timer.poll(), 'cancelled')
            self.assertIn(words, timer.status()['message'])
        self.assertEqual(self.suspend_calls, 0)

    def test_user_activity_after_arming_cancels(self):
        timer = self.timer()
        timer.arm()
        self.clock.now += 100
        self.input_idle = 150.0  # last input before the countdown started
        self.assertEqual(timer.poll(), 'waiting')
        self.input_idle = 30.0  # someone used the host PC 70 s after Stop
        self.assertEqual(timer.poll(), 'cancelled')
        self.assertIn('someone is using the host PC', timer.status()['message'])
        self.clock.now += 1000
        self.assertEqual(timer.poll(), 'stale')
        self.assertEqual(self.suspend_calls, 0)

    def test_rearming_restarts_the_countdown(self):
        timer = self.timer()
        timer.arm()
        self.clock.now += 500
        timer.arm()
        self.clock.now += 200
        self.assertEqual(timer.poll(), 'waiting')
        self.assertEqual(timer.status()['remaining_seconds'], 400)

    def test_suspend_failure_is_reported(self):
        def refuse():
            raise OSError(5, 'Access is denied')
        timer = self.timer(suspend=refuse)
        timer.arm()
        self.clock.now += 601
        self.assertEqual(timer.poll(), 'failed')
        self.assertIn('could not be put to sleep', timer.status()['message'])

    def test_off_and_configuration(self):
        for value in (0, None, False):
            timer = self.timer(minutes=value)
            self.assertEqual(timer.arm()['state'], 'off')
        self.assertEqual(timer.configure(15)['state'], 'idle')
        self.assertEqual(timer.arm()['remaining_seconds'], 900)
        self.assertEqual(timer.configure(0)['state'], 'off')
        self.clock.now += 3600
        self.assertEqual(timer.poll(), 'stale')
        self.assertEqual(self.suspend_calls, 0)
        for value in (-5, 2000, 'ten', True, float('nan')):
            with self.assertRaises(ValueError):
                self.timer(minutes=value)

    def test_background_thread_sleeps_and_can_be_cancelled(self):
        timer = self.timer(check_seconds=0.01)
        timer.arm()
        time.sleep(0.05)
        self.assertFalse(self.suspended.is_set())
        self.clock.now += 601
        self.assertTrue(self.suspended.wait(2))
        self.assertEqual(self.suspend_calls, 1)
        self.assertEqual(timer.status()['state'], 'idle')

        cancelled = self.timer(check_seconds=0.01)
        self.suspended.clear()
        cancelled.arm()
        cancelled.cancel()
        self.clock.now += 3600
        time.sleep(0.1)
        self.assertFalse(self.suspended.is_set())

    def test_windows_helpers_are_inert_elsewhere(self):
        with mock.patch.object(power.os, 'name', 'posix'):
            self.assertIsNone(power.windows_input_idle_seconds())
            timer = SleepAfterStop(5, lambda: True, suspend=self.suspend, clock=self.clock, log=self.logs.append)
        self.timers.append(timer)
        self.assertIsNone(timer.input_idle_seconds)


READINESS = {
    'adapters': [
        {'Name': 'Ethernet', 'InterfaceDescription': 'Intel(R) Ethernet Controller I225-V', 'MacAddress': 'AA-BB-CC-DD-EE-01',
         'LinkSpeed': '2.5 Gbps', 'TransmitLinkSpeed': 2500000000, 'MediaType': '802.3', 'PhysicalMediaType': '802.3',
         'WakeOnMagicPacket': 'Enabled', 'DeviceSleepOnDisconnect': 'Disabled'},
        {'Name': 'Wi-Fi', 'InterfaceDescription': 'Intel(R) Wi-Fi 6E AX211', 'MacAddress': 'AA-BB-CC-DD-EE-02',
         'LinkSpeed': '1.2 Gbps', 'TransmitLinkSpeed': 1201000000, 'MediaType': 'Native 802.11',
         'PhysicalMediaType': 'Native 802.11', 'WakeOnMagicPacket': 'Disabled', 'DeviceSleepOnDisconnect': None}],
    'sleep_states': ('The following sleep states are available on this system:\n    Standby (S3)\n    Hibernate\n'
                     '    Fast Startup\n\nThe following sleep states are not available on this system:\n'
                     '    Standby (S1)\n        The system firmware does not support this standby state.\n\n'
                     '    Standby (S0 Low Power Idle)\n        The system firmware does not support this standby state.\n'),
    'hiberboot': 1}

MODERN_STANDBY = """The following sleep states are available on this system:
    Standby (S0 Low Power Idle) Network Connected
    Hibernate

The following sleep states are not available on this system:
    Standby (S1)
        The system firmware does not support this standby state.
        This standby state is disabled when S0 low power idle is supported.

    Standby (S3)
        The system firmware does not support this standby state.
        This standby state is disabled when S0 low power idle is supported.
"""

GERMAN = """Die folgenden Standbymodi sind auf diesem System verfügbar:
    Standbymodus (S3)
    Ruhezustand

Die folgenden Standbymodi sind auf diesem System nicht verfügbar:
    Standbymodus (S0 Niedriger Energiemodus im Leerlauf)
        Die Systemfirmware unterstützt diesen Standbymodus nicht.
"""


def readiness(**changes):
    value = json.loads(json.dumps(READINESS))
    value.update(changes)
    return json.dumps(value)


class WakeReadinessTests(unittest.TestCase):
    def test_ready_wired_adapter(self):
        report = parse_wake_readiness('\ufeff' + readiness())
        self.assertEqual(report['adapters'][0], {
            'name': 'Ethernet', 'description': 'Intel(R) Ethernet Controller I225-V', 'mac': 'aa:bb:cc:dd:ee:01',
            'link_mbps': 2500, 'kind': 'wired', 'wake_on_magic_packet': True, 'device_sleep_on_disconnect': False,
            'wake_armed': None})
        self.assertEqual((report['adapters'][1]['kind'], report['adapters'][1]['wake_on_magic_packet'],
                          report['adapters'][1]['device_sleep_on_disconnect']), ('wifi', False, None))
        self.assertEqual(report['sleep_states'], {'s3': True, 's0_low_power_idle': False})
        self.assertEqual((report['fast_startup'], report['ready'], report['error']), (True, True, False))
        message = report['message']
        self.assertIn('Wake from sleep is ready', message)
        self.assertIn('BIOS', message)
        self.assertIn('Fast Startup is on', message)
        self.assertIn('sign-in screen unless automatic sign-in is set up', message)
        self.assertIn('VROSTBridge only starts after sign-in', message)

    def test_messages_say_what_to_change(self):
        disabled = READINESS['adapters'][0] | {'WakeOnMagicPacket': 'Disabled'}
        report = parse_wake_readiness(readiness(adapters=[disabled], hiberboot=0))
        self.assertFalse(report['ready'])
        self.assertIn('turn on "Wake on Magic Packet" for Ethernet', report['message'])
        self.assertIn('Fast Startup is already off', report['message'])
        report = parse_wake_readiness(readiness(adapters=[READINESS['adapters'][1]], hiberboot=None))
        self.assertIn('needs a wired (Ethernet) connection', report['message'])
        self.assertIn('usually Fast Startup turned off', report['message'])
        self.assertIsNone(report['fast_startup'])
        unsupported = READINESS['adapters'][0] | {'WakeOnMagicPacket': 'Unsupported'}
        report = parse_wake_readiness(readiness(adapters=unsupported))  # a single adapter arrives as an object
        self.assertEqual(len(report['adapters']), 1)
        self.assertIn('does not report Wake-on-LAN support', report['message'])

    def test_numeric_power_settings(self):
        rows = [READINESS['adapters'][0] | {'WakeOnMagicPacket': value} for value in (2, '1', 0, True, 'Inactive', [])]
        report = parse_wake_readiness(readiness(adapters=rows))
        self.assertEqual([adapter['wake_on_magic_packet'] for adapter in report['adapters']],
                         [True, False, None, True, False, None])

    def test_sleep_states_in_any_language(self):
        self.assertEqual(parse_sleep_states(MODERN_STANDBY), {'s3': False, 's0_low_power_idle': True})
        self.assertEqual(parse_sleep_states(GERMAN), {'s3': True, 's0_low_power_idle': False})
        self.assertEqual(parse_sleep_states('Access denied.'), {'s3': None, 's0_low_power_idle': None})
        self.assertEqual(parse_sleep_states(None), {'s3': None, 's0_low_power_idle': None})

    def test_link_speeds(self):
        for value, expected in ((1_000_000_000, 1000), ('2.5 Gbps', 2500), ('866.7 Mbps', 867), ('2,5 Gbps', 2500),
                                ('100 Mbps', 100), ('10 Gbit/s', 10000), ('10 Kbps', None), (0, None), (-5, None),
                                (True, None), (None, None), ('fast', None), (float('inf'), None)):
            self.assertEqual(link_speed_mbps(value), expected, value)

    def test_wake_readiness_runs_the_fixed_script_and_never_raises(self):
        calls = []

        def runner(arguments, timeout):
            calls.append((arguments, timeout))
            return completed(readiness())
        self.assertTrue(wake_readiness(runner)['ready'])
        arguments, timeout = calls[0]
        self.assertTrue(arguments[0].lower().endswith('powershell.exe'))
        self.assertEqual(arguments[1:], ['-NoProfile', '-NonInteractive', '-Command', power.WAKE_READINESS_SCRIPT])
        self.assertEqual(timeout, 30)

        def raises(error):
            def run(arguments, timeout):
                raise error
            return run
        for runner in (lambda a, t: completed('', 1, 'Get-NetAdapter : Access denied'), lambda a, t: completed('nonsense'),
                       lambda a, t: completed('[]'), raises(OSError('powershell missing')),
                       raises(subprocess.TimeoutExpired(['powershell'], 30))):
            report = wake_readiness(runner)
            self.assertTrue(report['error'])
            self.assertFalse(report['ready'])
            self.assertTrue(report['message'].startswith('Wake-on-LAN settings could not be read'))

    def test_command_line_scripts_contain_no_double_quotes(self):
        for script in (power.WAKE_READINESS_SCRIPT, power.ADAPTER_ADDRESSES_SCRIPT, qos.STATUS_SCRIPT):
            self.assertNotIn('"', script)
        self.assertIn('Get-NetAdapterPowerManagement', power.WAKE_READINESS_SCRIPT)
        self.assertIn('HiberbootEnabled', power.WAKE_READINESS_SCRIPT)
        self.assertIn('powercfg.exe /a', power.WAKE_READINESS_SCRIPT)

    def test_powershell_is_found_by_full_path(self):
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
            executable.parent.mkdir(parents=True)
            executable.write_text('')
            with mock.patch.dict(os.environ, {'SystemRoot': root}):
                self.assertEqual(power.powershell_path(), str(executable))
        with mock.patch.dict(os.environ, {'SystemRoot': '', 'WINDIR': ''}):
            self.assertEqual(power.powershell_path(), 'powershell.exe')

    def test_windows_run_refuses_malformed_commands(self):
        for arguments in ('powershell -Command x', [], ['powershell', None], ['powershell', 'a\0b']):
            with self.assertRaises(ValueError):
                power.windows_run(arguments, 5)


ADDRESSES = [
    {'IPAddress': '192.168.1.50', 'PrefixLength': 24, 'InterfaceIndex': 12, 'Name': 'Ethernet',
     'InterfaceDescription': 'Intel(R) Ethernet Controller I225-V', 'MacAddress': 'AA-BB-CC-DD-EE-01',
     'LinkSpeed': '2.5 Gbps', 'TransmitLinkSpeed': 2500000000, 'MediaType': '802.3', 'PhysicalMediaType': '802.3',
     'Status': 'Up', 'Hardware': True},
    {'IPAddress': '192.168.1.51', 'PrefixLength': 24, 'InterfaceIndex': 7, 'Name': 'Wi-Fi',
     'InterfaceDescription': 'Intel(R) Wi-Fi 6E AX211', 'MacAddress': 'AA-BB-CC-DD-EE-02', 'LinkSpeed': '1.2 Gbps',
     'TransmitLinkSpeed': None, 'MediaType': 'Native 802.11', 'PhysicalMediaType': 'Native 802.11', 'Status': 'Up',
     'Hardware': True},
    {'IPAddress': '172.20.0.1', 'PrefixLength': 20, 'InterfaceIndex': 30, 'Name': 'vEthernet (WSL)',
     'InterfaceDescription': 'Hyper-V Virtual Ethernet Adapter', 'MacAddress': '00-15-5D-01-02-03',
     'LinkSpeed': '10 Gbps', 'TransmitLinkSpeed': 10000000000, 'MediaType': '802.3', 'PhysicalMediaType': 'Unspecified',
     'Status': 'Up', 'Hardware': False},
    {'IPAddress': 'fe80::1', 'PrefixLength': 64, 'Name': 'bogus'}]


class AdapterAddressTests(unittest.TestCase):
    def test_adapter_for_local_address(self):
        rows = parse_adapter_addresses(json.dumps(ADDRESSES))
        self.assertEqual([row['ip'] for row in rows], ['192.168.1.50', '192.168.1.51', '172.20.0.1'])
        self.assertEqual(adapter_for_ip('192.168.1.50', rows), {
            'name': 'Ethernet', 'mac': 'aa:bb:cc:dd:ee:01', 'link_mbps': 2500, 'kind': 'wired',
            'description': 'Intel(R) Ethernet Controller I225-V', 'physical': True})
        wifi = adapter_for_ip('192.168.1.51', rows)
        self.assertEqual((wifi['link_mbps'], wifi['kind']), (1200, 'wifi'))
        self.assertFalse(adapter_for_ip('172.20.0.1', rows)['physical'])

    def test_raw_rows_unknown_and_invalid_addresses(self):
        self.assertEqual(adapter_for_ip('192.168.1.50', ADDRESSES)['mac'], 'aa:bb:cc:dd:ee:01')
        for address in ('192.168.1.99', '192.168.1.500', '', None, 3232235826):
            self.assertIsNone(adapter_for_ip(address, ADDRESSES), address)
        self.assertIsNone(adapter_for_ip('192.168.1.50', None))
        self.assertEqual(parse_adapter_addresses(json.dumps(ADDRESSES[0]))[0]['prefix'], 24)

    def test_query_failures_return_an_empty_list(self):
        calls = []

        def runner(arguments, timeout):
            calls.append(arguments)
            return completed(json.dumps(ADDRESSES))
        self.assertEqual(len(adapter_addresses(runner)), 3)
        self.assertEqual(calls[0][-1], power.ADAPTER_ADDRESSES_SCRIPT)
        self.assertEqual(adapter_addresses(lambda a, t: completed('', 1)), [])
        self.assertEqual(adapter_addresses(lambda a, t: completed('garbage')), [])


def status_output(policy=True, app='vrserver.exe', protocol='UDP', dscp=46, nla='1', marker='1',
                  marker_utc='2026-10-03T09:15:00.1234567Z', boot_utc='2026-10-01T08:00:00.0000000Z'):
    found = {'Name': 'VROSTBridgeVideo', 'AppPath': app, 'Protocol': protocol, 'Dscp': dscp,
             'NetworkProfile': 'All'} if policy else None
    return json.dumps({'policy': found, 'nla': nla, 'marker': marker, 'marker_utc': marker_utc, 'boot_utc': boot_utc})


class QosStatusTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(qos.parse_status(status_output())['state'], 'restart')
        on = qos.parse_status(status_output(boot_utc='2026-10-04T07:00:00.0000000Z'))
        self.assertEqual((on['state'], on['restart_needed'], on['policy'], on['nla_override']), ('on', False, True, True))
        self.assertIn('DSCP 46', on['message'])
        self.assertEqual(qos.parse_status(status_output(marker=None, marker_utc=None))['state'], 'on')
        off = qos.parse_status(status_output(policy=False))
        self.assertEqual((off['state'], off['policy']), ('off', False))
        self.assertIn('administrator permission', off['message'])
        needs = qos.parse_status(status_output(nla=''))
        self.assertEqual(needs['state'], 'incomplete')
        self.assertIn('Do not use NLA', needs['message'])
        for changes in ({'dscp': 34}, {'app': 'C:\\Games\\other.exe'}, {'protocol': 'TCP'}, {'dscp': -1}):
            result = qos.parse_status(status_output(**changes))
            self.assertEqual((result['state'], result['policy_ok']), ('incomplete', False), changes)
        for changes in ({'app': 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\vrserver.exe'},
                        {'protocol': '2'}, {'app': 'VRSERVER.EXE'}):
            self.assertTrue(qos.parse_status(status_output(**changes))['policy_ok'], changes)
        self.assertEqual(qos.parse_status('\ufeff' + status_output(nla=1))['nla_override'], True)

    def test_unreadable_times_never_claim_a_restart(self):
        for changes in ({'marker_utc': 'yesterday'}, {'boot_utc': None}, {'marker_utc': ''}):
            self.assertFalse(qos.parse_status(status_output(**changes))['restart_needed'], changes)
        self.assertIsNotNone(qos._utc('2026-10-03T09:15:00.1234567Z'))
        self.assertIsNotNone(qos._utc('2026-10-03T09:15:00+01:00'))

    def test_status_never_raises(self):
        calls = []

        def runner(arguments, timeout):
            calls.append(arguments)
            return completed(status_output())
        self.assertEqual(qos.status(runner)['state'], 'restart')
        self.assertEqual(calls[0][1:], ['-NoProfile', '-NonInteractive', '-Command', qos.STATUS_SCRIPT])

        def broken(arguments, timeout):
            raise subprocess.TimeoutExpired(arguments, timeout)
        for runner in (lambda a, t: completed('', 1, 'denied'), lambda a, t: completed('[1, 2]'),
                       lambda a, t: completed(''), broken):
            result = qos.status(runner)
            self.assertEqual((result['state'], result['error'], result['policy']), ('unknown', True, None))


class QosScriptTests(unittest.TestCase):
    def test_enable_script(self):
        script = qos.enable_script()
        self.assertIn("$name = 'VROSTBridgeVideo'", script)
        self.assertIn("New-NetQosPolicy -Name $name -AppPathNameMatchCondition 'vrserver.exe' -IPProtocolMatchCondition "
                      'UDP -DSCPAction 46 -NetworkProfile All', script)
        self.assertNotIn('PolicyStore', script)
        self.assertIn(r"$qosKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\QoS'", script)
        self.assertIn("$nlaName = 'Do not use NLA'", script)
        self.assertIn("-Name $nlaName -PropertyType String -Value '1'", script)
        # Idempotent: the policy is replaced, and the NLA value is set (and marked as ours) only when absent.
        self.assertLess(script.index('Remove-NetQosPolicy -Confirm:$false'), script.index('New-NetQosPolicy'))
        self.assertIn("if ([string]$current -ne '1')", script)
        self.assertLess(script.index("-Name 'VROSTBridgeSet'"), script.index('-Name $nlaName -PropertyType'))
        self.assertIn(r"$markerKey = 'HKLM:\SOFTWARE\VROSTBridge'", script)
        self.assertIn('exit 0', script)
        self.assertIn('exit 1', script)

    def test_disable_script_removes_only_what_vrostbridge_added(self):
        script = qos.disable_script()
        self.assertIn('Get-NetQosPolicy -Name $name -ErrorAction SilentlyContinue | Remove-NetQosPolicy -Confirm:$false', script)
        self.assertIn("if ([string]$marker -eq '1')", script)
        self.assertIn('Where-Object { $_.Name -ne $name }', script)
        self.assertLess(script.index("if ([string]$marker -eq '1')"), script.index('Remove-ItemProperty -Path $qosKey'))
        self.assertNotIn('New-ItemProperty', script)
        self.assertNotIn('New-NetQosPolicy', script)

    def test_elevation_command_quotes_paths(self):
        command = qos.elevation_command("C:\\Users\\Anne O'Brien\u2019s\\AppData\\Local\\Temp\\VROSTBridge\\x.ps1")
        self.assertNotIn('"', command)
        self.assertIn("'C:\\Users\\Anne O''Brien\u2019\u2019s\\AppData\\Local\\Temp\\VROSTBridge\\x.ps1'", command)
        self.assertIn("$info.Verb = 'runas'", command)
        self.assertIn("'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + [string][char]34", command)
        self.assertIn('NativeErrorCode -eq 1223) { exit 1223 }', command)
        for path in ('C:\\Temp\\a"b.ps1', 'C:\\Temp\\a\nb.ps1', ''):
            with self.assertRaises(ValueError):
                qos.elevation_command(path)


class RunElevatedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.seen = []

    def runner(self, returncode=0, stderr='', error=None):
        def run(arguments, timeout):
            command = arguments[-1]
            files = sorted((self.root / 'VROSTBridge').glob('*.ps1'))
            self.seen.append({'arguments': arguments, 'timeout': timeout, 'files': files,
                              'content': files[0].read_bytes() if files else b''})
            if error:
                raise error
            return completed('', returncode, stderr)
        return run

    def test_script_file_is_written_run_and_deleted(self):
        result = qos.run_elevated("Write-Output 'ok'\nexit 0\n", self.runner(), temp_root=self.root, label='unit')
        self.assertEqual((result['ok'], result['declined'], result['returncode']), (True, False, 0))
        [call] = self.seen
        [script_file] = call['files']
        self.assertRegex(script_file.name, r'^VROSTBridge-unit-[0-9a-f]{8}\.ps1$')
        self.assertEqual(call['content'], b"\xef\xbb\xbfWrite-Output 'ok'\r\nexit 0\r\n")
        self.assertTrue(call['arguments'][0].lower().endswith('powershell.exe'))
        self.assertEqual(call['arguments'][1:4], ['-NoProfile', '-NonInteractive', '-Command'])
        self.assertIn(qos._quote(str(script_file)), call['arguments'][4])
        self.assertEqual(call['timeout'], 600)
        self.assertFalse(script_file.exists())
        self.assertEqual(list((self.root / 'VROSTBridge').iterdir()), [])

    def test_declined_unknown_and_failed(self):
        result = qos.run_elevated('exit 0', self.runner(1223), temp_root=self.root)
        self.assertEqual((result['ok'], result['declined']), (False, True))
        self.assertEqual(result['message'], 'Administrator permission was not given, so nothing was changed.')
        result = qos.run_elevated('exit 0', self.runner(3), temp_root=self.root)
        self.assertIn('did not report', result['message'])
        result = qos.run_elevated('exit 0', self.runner(1, 'first\nThe file is blocked.'), temp_root=self.root)
        self.assertEqual((result['ok'], result['declined'], result['returncode']), (False, False, 1))
        self.assertEqual(result['message'], 'The administrator step failed: The file is blocked.')
        self.assertEqual(list((self.root / 'VROSTBridge').iterdir()), [])

    def test_runner_errors_are_reported_and_the_file_removed(self):
        for error, words in ((subprocess.TimeoutExpired(['powershell'], 600), 'Timed out'),
                             (OSError('powershell.exe not found'), 'could not be started')):
            result = qos.run_elevated('exit 0', self.runner(error=error), temp_root=self.root)
            self.assertFalse(result['ok'])
            self.assertIn(words, result['message'])
            self.assertEqual(list((self.root / 'VROSTBridge').iterdir()), [])

    def test_invalid_scripts_and_labels(self):
        for script in ('', '   ', None, 'x' * 70000, 'a\0b'):
            result = qos.run_elevated(script, self.runner(), temp_root=self.root)
            self.assertFalse(result['ok'])
        self.assertEqual(self.seen, [])
        qos.run_elevated('exit 0', self.runner(), temp_root=self.root, label='../../evil name')
        self.assertTrue(self.seen[0]['files'][0].name.startswith('VROSTBridge-task-'))

    def test_unusual_temp_paths(self):
        quoted = self.root / "Anne O'Brien"
        result = qos.run_elevated('exit 0', self.runner(), temp_root=quoted)
        self.assertTrue(result['ok'])
        self.assertIn("Anne O''Brien", self.seen[-1]['arguments'][4])
        refused = self.root / 'odd"name'
        result = qos.run_elevated('exit 0', self.runner(), temp_root=refused)
        self.assertFalse(result['ok'])
        self.assertIn('unsupported characters', result['message'])
        self.assertEqual(list((refused / 'VROSTBridge').iterdir()), [])

    def test_apply_runs_the_script_then_reports_status(self):
        calls = []

        def runner(arguments, timeout):
            calls.append(arguments[-1])
            if 'ProcessStartInfo' in arguments[-1]:
                return completed('', 0)
            return completed(status_output())
        result = qos.apply(True, runner=runner, temp_root=self.root)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status']['state'], 'restart')
        self.assertEqual(result['message'], qos.MESSAGES['restart'])
        self.assertIn('VROSTBridge-video-priority-on-', calls[0])
        result = qos.apply(False, runner=lambda a, t: completed('', 1223) if 'ProcessStartInfo' in a[-1]
                           else completed(status_output()), temp_root=self.root)
        self.assertTrue(result['declined'])
        self.assertIn('not given', result['message'])


if __name__ == '__main__':
    unittest.main()
