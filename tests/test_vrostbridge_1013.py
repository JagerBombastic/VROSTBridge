"""VROSTBridge 1.0.0.13: follow-ups from 1.0.0.12's first session on hardware."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from host.control import HostControl  # noqa: E402
from host.stream_options import FEC_AUDIO_GROUP, FEC_VIDEO_GROUP, FEC_VIDEO_GROUP_WIRED, stream_config  # noqa: E402
from launcher import adapter_counters  # noqa: E402
from launcher.adapter_counters import AdapterCounters, linux_adapters, linux_driver, linux_udp  # noqa: E402
from launcher.client_runtime import server_origin, server_origin_note  # noqa: E402
from launcher.session_tuning import MEMORY_NAME, SessionTuning  # noqa: E402
from launcher.windows_network import MIB_IF_ROW2, parse_if_counters  # noqa: E402
from test_vrostbridge_1008 import FakeSteamOSManager, sample  # noqa: E402
from test_vrostbridge_1012 import second  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_every_program_is_1_0_0_13(self):
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual((CLIENT_VERSION, HOST_VERSION), ('1.0.0.13', '1.0.0.13'))
        version = read('core/version.hpp')
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "1.0.0.13"', version)
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "1.0.0.13"', version)
        self.assertIn('project(VROSTBridge VERSION 1.0.0.13 ', read('CMakeLists.txt'))
        self.assertIn('version=1.0.0.13\n', read('packaging/install-bazzite.sh'))
        nsi = read('packaging/windows-installer.nsi')
        for line in ('Name "VROSTBridge Host 1.0.0.13"', 'VIProductVersion "1.0.0.13"', '"DisplayVersion" "1.0.0.13"',
                     'InstallDir "$LOCALAPPDATA\\VROSTBridge\\Host\\1.0.0.13"'):
            self.assertIn(line, nsi)
        self.assertNotIn('1.0.0.12', nsi)
        resource = read('packaging/windows-icon.rc')
        self.assertIn('FILEVERSION 1,0,0,13', resource)
        self.assertIn('PRODUCTVERSION 1,0,0,13', resource)
        self.assertIn('native launcher 1.0.0.13 ---', read('packaging/windows-launcher.c'))
        self.assertIn("VERSION = '1.0.0.13'", read('launcher/install_maintenance.py'))
        self.assertIn("'VROSTBridge 1.0.0.13 / ALVR'", read('host/profile.py'))
        sys.path.insert(0, str(ROOT / 'packaging'))
        import build_distributions
        import build_windows_client
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-1.0.0.13.zip')
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-1.0.0.13.exe')
        self.assertEqual(build_windows_client.client_version(), '1.0.0.13')
        self.assertEqual(build_windows_client.setup_name(), 'VROSTBridge-Client-Setup-1.0.0.13.exe')
        self.assertEqual(build_windows_client.portable_name(), 'VROSTBridge-Client-Portable-1.0.0.13.zip')
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.13'))


class HostStopDuringStartupTests(unittest.TestCase):
    """A stop 0.75 s into a start waited 88 s: the window ran to the end of the 90 s launch budget, and ALVR's
    shutdown request, accepted before any headset streamed, did nothing."""

    VERSION = '21.0.0-dev14'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 0.0
        self.quits = []
        self.messages = []
        self.control = HostControl(Path(self.temp.name), self.messages.append, lambda: None, lambda: set(),
                                   quit=lambda: self.quits.append(self.now))
        self.control.expected_version = self.VERSION
        self.server = {'pid': 12, 'path': str(Path(self.temp.name) / 'vrserver.exe'), 'created': 100}
        self.live = {}
        self.control.process_ids = lambda: {12} if 12 in self.live else set()
        self.control.api = Mock()
        self.control.api.request.side_effect = self.request
        self.api_up_at = 2.0
        self.server_at = 1.0
        self.exits_after_quit = 1.0
        self.ignores_quit = False
        self.shutdown_works = False

    def request(self, endpoint, **kwargs):
        if self.now < self.api_up_at:
            raise OSError('The driver API is not up yet.')
        if endpoint == 'steamvr/shutdown' and self.shutdown_works:
            self.gone(12)
        return self.VERSION

    def gone(self, pid):
        self.live.pop(pid, None)
        self.server_gone = True

    def sleep(self, seconds):
        self.now += seconds
        if self.server_at is not None and self.now >= self.server_at and not self.server_gone:
            self.live.setdefault(12, self.server)
        if self.quits and not self.ignores_quit and self.now >= self.quits[0] + self.exits_after_quit:
            self.gone(12)

    def stop(self, starting=True):
        self.server_gone = False
        if starting:
            self.control.runtime_observed = False
            self.control.launch_started = -0.75
            self.control.launch_deadline = self.control.launch_started + self.control.STARTUP_TIMEOUT
            launcher = Mock()
            launcher.poll.side_effect = lambda: None if self.now < 1.5 else 0
            self.control.launch_process = launcher
        capture = lambda pids: self.control.identities.update({12: self.server}) if 12 in pids else None  # noqa: E731
        with patch('host.control.identify', side_effect=self.live.get), \
             patch('host.control.time.monotonic', side_effect=lambda: self.now), \
             patch('host.control.time.sleep', side_effect=self.sleep), \
             patch.object(self.control, '_capture_identities', side_effect=capture), \
             patch('host.control.terminate', side_effect=lambda item: self.gone(item['pid'])) as terminate:
            self.control._shutdown_runtime()
        return terminate

    def test_a_stop_during_startup_asks_steamvr_to_exit_instead_of_waiting_out_the_launch(self):
        terminate = self.stop()
        self.assertEqual(len(self.quits), 1)
        self.assertGreaterEqual(self.quits[0], self.api_up_at + HostControl.QUIT_FALLBACK_S - 0.01)
        self.assertLess(self.quits[0], self.api_up_at + HostControl.QUIT_FALLBACK_S + 0.5)
        self.assertLess(self.now, 12)
        terminate.assert_not_called()
        self.assertIn('SteamVR was still running; asking it to exit as its own Exit VR does.', self.messages)
        self.assertEqual(self.control.identities, {})

    def test_a_runtime_that_ignores_both_is_ended_a_window_after_its_server_appeared(self):
        self.ignores_quit = True
        terminate = self.stop()
        terminate.assert_called_once_with(self.server)
        forced = self.server_at + HostControl.SHUTDOWN_TIMEOUT
        self.assertGreaterEqual(self.now, forced)
        self.assertLess(self.now, forced + HostControl.SHUTDOWN_QUIET_SECONDS + 1)
        self.assertLess(self.now, HostControl.STARTUP_TIMEOUT / 2)

    def test_a_streaming_session_stops_through_alvr_without_the_fallback(self):
        self.control.runtime_observed = True
        self.live[12] = self.server
        self.control.identities = {12: self.server}
        self.control.owned_pids = {12}
        self.api_up_at = 0
        self.shutdown_works = True
        terminate = self.stop(starting=False)
        self.assertEqual(self.quits, [])
        terminate.assert_not_called()
        self.assertLess(self.now, 3)

    def test_a_negotiated_restart_is_left_to_alvr(self):
        self.control.runtime_observed = True
        self.control.restart_pending = True
        self.live[12] = self.server
        self.control.identities = {12: self.server}
        self.control.owned_pids = {12}
        self.api_up_at = 0
        self.server_at = None
        terminate = self.stop(starting=False)
        self.assertEqual(self.quits, [])
        terminate.assert_called_once_with(self.server)


def longest_repairable_run(shards, group):
    """The longest run of datagrams lost in a row, anywhere in a frame as it is sent (its data shards, then one parity
    datagram per group, group 0 first), that XOR parity over interleaved groups still repairs: shard i is in group
    i % groups, groups = ceil(shards / group), and a group comes back while it lost at most one of its members."""
    groups = math.ceil(shards / group)
    sent = [('data', index % groups) for index in range(shards)] + [('parity', number) for number in range(groups)]
    best = 0
    for length in range(1, len(sent) + 1):
        for start in range(len(sent) - length + 1):
            lost = {}
            for _, number in sent[start:start + length]:
                lost[number] = lost.get(number, 0) + 1
            if max(lost.values()) > 1:
                return best
        best = length
    return best


class WiredParityTests(unittest.TestCase):
    FEATURES = ['parity', 'pacing']

    def test_a_wired_client_gets_twice_the_parity(self):
        config = stream_config({'network': 'wired'}, self.FEATURES)
        self.assertEqual((config['fec_video_group'], config['fec_audio_group']), (FEC_VIDEO_GROUP_WIRED, FEC_AUDIO_GROUP))
        self.assertEqual(FEC_VIDEO_GROUP_WIRED, 5)

    def test_wifi_and_an_unreported_network_keep_one_in_ten(self):
        for preferences in ({'network': 'wifi'}, {'network': 'unknown'}, {}):
            self.assertEqual(stream_config(preferences, self.FEATURES)['fec_video_group'], FEC_VIDEO_GROUP)
        self.assertNotIn('fec_video_group', stream_config({'network': 'wired'}, ['pacing']), 'not without patch 0021')

    def test_a_clump_twice_as_long_comes_back(self):
        # About 60 shards a frame at 50 Mbit/s and 75 frames a second (1386 bytes of a 1400-byte datagram each).
        self.assertEqual(longest_repairable_run(60, FEC_VIDEO_GROUP), 6)
        self.assertEqual(longest_repairable_run(60, FEC_VIDEO_GROUP_WIRED), 12)
        self.assertEqual(longest_repairable_run(8, FEC_VIDEO_GROUP), 1, 'a small frame had a single group')
        self.assertEqual(longest_repairable_run(8, FEC_VIDEO_GROUP_WIRED), 2)


class LinuxAdapterCounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.net = self.root / 'sys/class/net'
        self.adapter('enp1s0u1', usb=True, rx_packets=1000, rx_dropped=2, rx_missed_errors=0, tx_packets=50)
        self.adapter('wlan0', usb=False, rx_packets=10, tx_packets=10)
        (self.net / 'lo/statistics').mkdir(parents=True)
        (self.net / 'lo/statistics/rx_packets').write_text('5\n')  # no device behind it

    def adapter(self, name, usb, **counters):
        folder = self.net / name
        device = self.root / ('devices/usb1/1-1' if usb else 'devices/pci0000/0000:01:00.0') / name
        device.mkdir(parents=True, exist_ok=True)
        if not (folder / 'device').exists():
            (folder / 'statistics').mkdir(parents=True)
            os.symlink(device, folder / 'device')
        for key, value in counters.items():
            (folder / 'statistics' / key).write_text(str(value) + '\n')

    def test_only_adapters_with_a_device_and_their_usb_link(self):
        adapters = linux_adapters(self.net)
        self.assertEqual(sorted(adapters), ['enp1s0u1', 'wlan0'])
        self.assertTrue(adapters['enp1s0u1']['usb'])
        self.assertFalse(adapters['wlan0']['usb'])
        self.assertEqual(adapters['enp1s0u1']['rx_dropped'], 2)

    def test_the_systems_udp_receive_errors(self):
        snmp = self.root / 'snmp'
        snmp.write_text('Ip: Forwarding DefaultTTL\nIp: 2 64\n'
                        'Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti\n'
                        'Udp: 900 1 3 40 3 0 0 7\n')
        self.assertEqual(linux_udp(snmp), {'NoPorts': 1, 'InErrors': 3, 'RcvbufErrors': 3, 'SndbufErrors': 0, 'InCsumErrors': 0})
        self.assertEqual(linux_udp(self.root / 'missing'), {})

    def test_the_drivers_own_drop_counters(self):
        output = Mock(returncode=0, stdout='NIC statistics:\n     tx_packets: 50\n     rx_missed: 4\n     rx_errors: 1\n'
                                         '     align_errors: 0\n     rx_unicast: 900\n')
        runner = Mock(return_value=output)
        self.assertEqual(linux_driver('enp1s0u1', runner=runner, ethtool='/usr/sbin/ethtool'),
                         {'rx_missed': 4, 'rx_errors': 1, 'align_errors': 0})
        runner.assert_called_once_with(['/usr/sbin/ethtool', '-S', 'enp1s0u1'], 3)
        self.assertEqual(linux_driver('enp1s0u1', runner=Mock(side_effect=OSError('gone')), ethtool='ethtool'), {})
        self.assertEqual(linux_driver('', runner=runner, ethtool='ethtool'), {})

    def test_a_session_reports_what_the_counters_gained(self):
        driver = {'rx_missed': 4}
        clock = [100.0]
        counters = AdapterCounters(read=lambda: {'adapters': linux_adapters(self.net), 'udp': {'RcvbufErrors': 3}},
                                   driver=lambda interface: dict(driver), clock=lambda: clock[0])
        self.assertEqual(counters.snapshot(), {'state': 'idle'})
        counters.begin('enp1s0u1')
        self.adapter('enp1s0u1', usb=True, rx_packets=91000, rx_dropped=2, rx_missed_errors=12, tx_packets=950)
        self.adapter('wlan0', usb=False, rx_packets=10, tx_packets=10)
        driver['rx_missed'] = 16
        clock[0] = 160.0
        live = counters.snapshot()
        self.assertEqual(live['state'], 'session')
        self.assertEqual(live['stream_interface'], 'enp1s0u1')
        self.assertEqual(live['seconds'], 60.0)
        self.assertEqual(live['adapters']['enp1s0u1'], {'rx_packets': 90000, 'rx_missed_errors': 12, 'tx_packets': 900, 'usb': True})
        self.assertEqual(live['adapters']['wlan0'], {'rx_packets': 0, 'tx_packets': 0, 'usb': False})
        self.assertEqual(live['driver'], {'rx_missed': 12})
        self.assertEqual(live['udp'], {})
        self.assertEqual(live['stream_drops'], 12)
        counters.end()
        self.adapter('enp1s0u1', usb=True, rx_dropped=500)
        clock[0] = 999.0
        final = counters.snapshot()
        self.assertEqual(final['state'], 'ended')
        self.assertEqual(final['seconds'], 60.0)
        self.assertNotIn('rx_dropped', final['adapters']['enp1s0u1'], 'kept as it was when the session ended')
        counters.begin('enp1s0u1')
        self.assertEqual(counters.snapshot()['state'], 'session', 'the next session starts afresh')

    def test_counters_that_go_backwards_or_cannot_be_read_are_left_out(self):
        values = {'adapters': {'eth0': {'rx_packets': 500, 'rx_dropped': 9}}, 'udp': {}}
        counters = AdapterCounters(read=lambda: values, driver=lambda interface: {})
        counters.begin('eth0')
        values = {'adapters': {'eth0': {'rx_packets': 20, 'rx_dropped': 9}, 'usb0': {'rx_packets': 1}}, 'udp': {}}
        snapshot = counters.snapshot()
        self.assertEqual(snapshot['adapters'], {'eth0': {}, 'usb0': {'appeared': True}})
        broken = AdapterCounters(read=Mock(side_effect=OSError('no sysfs')), driver=lambda interface: {})
        broken.begin('eth0')
        self.assertEqual(broken.snapshot()['adapters'], {})


class WindowsAdapterCounterTests(unittest.TestCase):
    def test_get_if_entry2_counters(self):
        row = MIB_IF_ROW2()
        row.InUcastPkts, row.InNUcastPkts, row.InDiscards, row.InErrors, row.InUnknownProtos = 900, 12, 3, 1, 4
        row.OutUcastPkts, row.OutNUcastPkts, row.OutDiscards, row.OutErrors = 50, 2, 0, 0
        self.assertEqual(parse_if_counters(row), {'rx_packets': 912, 'rx_discards': 3, 'rx_errors': 1,
                                                  'rx_unknown_protocols': 4, 'tx_packets': 52, 'tx_discards': 0,
                                                  'tx_errors': 0})

    def test_physical_adapters_by_alias(self):
        from launcher import windows_network
        rows = [{'alias': 'Ethernet 2', 'luid': 7, 'physical': True, 'kind': 'ethernet', 'usb': False},
                {'alias': 'vEthernet (WSL)', 'luid': 8, 'physical': False, 'kind': 'ethernet', 'usb': False}]
        api = Mock()
        api.interface_counters.return_value = {'rx_packets': 5, 'tx_discards': 1}
        with patch.object(windows_network, 'adapters', return_value=rows), patch.object(windows_network, '_api', api):
            self.assertEqual(windows_network.adapter_counters(),
                             {'Ethernet 2': {'rx_packets': 5, 'tx_discards': 1, 'kind': 'ethernet', 'usb': False}})
        api.interface_counters.assert_called_once_with(7)


class CompanionCounterTests(unittest.TestCase):
    def test_both_reports_carry_them_and_both_ends_note_them(self):
        source = (ROOT / 'launcher/app.py').read_text(encoding='utf-8')
        self.assertIn("'adapter_counters':self.adapter_counters.snapshot() if getattr(self,'adapter_counters',None) else None", source)
        self.assertIn("self.adapter_counters.begin(interface)", source, 'the headset PC, with the stream adapter')
        self.assertIn("('Adapter counters',getattr(self,'adapter_counters',None))", source, 'ended with the session')
        self.assertIn("self.evidence=SessionEvidence();self.last_exit=None;self.adapter_counters.begin()", source, 'the host')
        self.assertIn("    def stop_host_session(self):\n        with self.action_lock:\n            self.adapter_counters.end()", source)


class RememberedClockReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def tuning(self, manager):
        return SessionTuning(runner=manager, log=lambda line: None, busctl='busctl', state_directory=self.state)

    def test_the_report_says_what_is_remembered_and_what_the_session_did(self):
        manager = FakeSteamOSManager()
        first = self.tuning(manager)
        self.assertEqual((first.status()['remembered'], first.status()['last_session']), (None, None))
        first.begin(sample(1))
        for _ in range(10):
            first.note_timing(second(cadence='late_or_paired'))
        first.observe(sample(2))
        self.assertEqual(first.observe(sample(3)), 1300)
        first.end()
        status = first.status()
        self.assertEqual(status['remembered'], {'clock_mhz': 1300, 'clock_max_mhz': 2700, 'charger': True})
        self.assertEqual(status['last_session'], {'held_from_start': False, 'clock_mhz': 1300, 'raises': 1, 'set_back': False},
                         'kept after the stop, when reports are made')
        second_session = self.tuning(manager)
        second_session.begin(sample(10))
        self.assertEqual(second_session.status()['last_session'],
                         {'held_from_start': True, 'clock_mhz': 1300, 'raises': 0, 'set_back': False})
        (self.state / MEMORY_NAME).write_text('not json')
        self.assertIsNone(second_session.status()['remembered'])

    def test_no_steamos_manager_no_record(self):
        manager = FakeSteamOSManager()
        tuning = self.tuning(manager)
        with patch.object(tuning, 'read', return_value={'available': False}):
            tuning.begin(sample(1))
        self.assertIsNone(tuning.status()['last_session'])



class GameModeOriginTests(unittest.TestCase):
    """1.0.0.12's Game Mode start: SteamVR's server had WAYLAND_DISPLAY=wayland-0, a name VROSTBridge never gives."""

    def setUp(self):
        import socket
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.runtime = base / 'r'
        self.runtime.mkdir()
        self.sockets = []
        for name in ('vrostbridge-headset-7', 'gamescope-0', 'pipewire-0'):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(self.runtime / name))
            self.addCleanup(listener.close)
        self.proc = base / 'proc'
        for pid, name, parent in ((40, 'vrserver', 30), (30, 'pv-adverb', 20), (20, 'reaper', 10), (10, 'steam', 1)):
            (self.proc / str(pid)).mkdir(parents=True)
            (self.proc / str(pid) / 'stat').write_text(f'{pid} ({name}) S {parent} 1 1 0\n')
        # The server's own file system: its wayland-0 is a name for one of this session's sockets.
        inner = base / 'inner' / 'run/user/1000'
        inner.mkdir(parents=True)
        os.symlink(base / 'inner', self.proc / '40' / 'root')
        self.inner = inner
        self.identities = {40: {'pid': 40, 'path': '/s/bin/linux64/vrserver'}}

    def environ(self, **values):
        data = b''.join(f'{key}={value}'.encode() + b'\0' for key, value in values.items())
        (self.proc / '40' / 'environ').write_bytes(data)

    def origin(self):
        return server_origin(self.identities, 'vrostbridge-headset-7', self.proc, str(self.runtime), 'gamescope-0')

    def test_a_container_that_renamed_gamescopes_display(self):
        self.environ(WAYLAND_DISPLAY='wayland-0', XDG_RUNTIME_DIR='/run/user/1000', PRESSURE_VESSEL_RUNTIME='x',
                     SteamAppId='250820', HOME='/home/x')
        os.symlink(self.runtime / 'gamescope-0', self.inner / 'wayland-0')
        origin = self.origin()
        self.assertEqual(origin['parents'], ['pv-adverb', 'reaper', 'steam'])
        self.assertEqual(origin['markers'], ['PRESSURE_VESSEL_RUNTIME', 'SteamAppId'])
        self.assertEqual(origin['display'], {'name': 'wayland-0', 'reaches': 'gamescope', 'socket': 'gamescope-0'})
        note = server_origin_note(origin)
        self.assertIn('started by pv-adverb < reaper < steam', note)
        self.assertIn("its display name wayland-0 reaches gamescope's own display", note)
        self.assertTrue(note.startswith('Game Mode: '), 'kept with the Game Mode start')

    def test_the_headset_display_under_another_name_is_recognised(self):
        self.environ(WAYLAND_DISPLAY='wayland-0', XDG_RUNTIME_DIR='/run/user/1000')
        os.symlink(self.runtime / 'vrostbridge-headset-7', self.inner / 'wayland-0')
        self.assertEqual(self.origin()['display']['reaches'], 'vrostbridge')
        self.assertIn("reaches VROSTBridge's headset display", server_origin_note(self.origin()))

    def test_a_name_with_no_socket_and_an_unreadable_server(self):
        self.environ(WAYLAND_DISPLAY='wayland-0', XDG_RUNTIME_DIR='/run/user/1000')
        self.assertEqual(self.origin()['display'], {'name': 'wayland-0', 'reaches': 'missing', 'socket': None})
        (self.proc / '40' / 'environ').unlink()
        self.assertFalse(self.origin()['readable'])
        self.assertIn('could not be read', server_origin_note(self.origin()))
        self.assertIsNone(server_origin({41: {'pid': 41, 'path': '/s/bin/linux64/vrmonitor'}}, None, self.proc))


# ---------------------------------------------------------------------------------------------------------------
# Display modes: the choices offered before a session; an Index's rate set in SteamVR, a Beyond's mode followed.
from types import SimpleNamespace  # noqa: E402

from headset_fixture import HEADSET  # noqa: E402
from host.profile import configure  # noqa: E402
from launcher.app import Application  # noqa: E402
from launcher.client_runtime import REFRESH, ClientRuntime  # noqa: E402
from launcher.graphics_limit import advice  # noqa: E402
from launcher.headset import (BEYOND_LIGHTER_MODE, FAMILY_MODES, MODES, display_status, family_headset,  # noqa: E402
                              refresh_text, requested_refresh, resolution_options, resolve_stream_preferences,
                              selected_resolution, stream_size, wire_resolution)
from launcher.session_link import session_settings  # noqa: E402

BEYOND = dict(HEADSET, model='Beyond', manufacturer='Bigscreen', width=2712, height=2712, refresh_hz=75.0)
OTHER = dict(HEADSET, model='Generic HMD', manufacturer='Example', width=2000, height=2000)
AUTO = {'codec': 'Auto', 'bitrate': 80, 'resolution': 'automatic'}


def ids(options):
    return [option['id'] for option in options]


class DisplayModeTests(unittest.TestCase):
    """1.0.0.13: the stream choices could only be made once a session had detected the headset. They are offered
    before a session too, from the last verified headset or a known headset's display: an Index offers its rates
    (Native at 80, 90, 120 and 144 Hz, set in SteamVR for the session), and a Beyond none: it streams the mode it is
    in (2560 at 75 Hz, 1920 at 90 Hz), which only Bigscreen's Beyond Utility on a Windows PC switches."""

    def test_each_known_headset_offers_its_choices_before_a_session(self):
        for headset, family, expected in ((BEYOND, 'beyond', ['automatic']),
                                          (HEADSET, 'index', ['automatic', 'index-80', 'index-90', 'index-120', 'index-144'])):
            for source in ('last', 'display'):
                shown = family_headset(family) if source == 'display' else headset
                display = display_status(AUTO, shown, source)
                self.assertEqual(ids(display['resolution_options']['Auto']), expected)
                self.assertTrue(all(option['available'] for option in display['resolution_options']['Auto']))
                self.assertEqual((display['source'], display['detected']), (source, source != 'display'))
                self.assertEqual(display['resolution_fixed'], family == 'beyond')
        # Known only by its display, the Beyond's mode is known once SteamVR describes it.
        self.assertEqual(resolution_options(AUTO, family_headset('beyond'), 'display')[0]['label'],
                         'Set by the headset - size checked on Start')
        self.assertIn('the stream follows the mode it is in', display_status(AUTO, family_headset('beyond'), 'display')['message'])
        self.assertIn('known from its display', display_status(AUTO, family_headset('index'), 'display')['message'])
        # Nothing known at all: the sizes can be chosen, their dimensions come on Start.
        self.assertEqual(ids(display_status(AUTO, None)['resolution_options']['H264']),
                         ['automatic', 'compatibility', 'sharp', 'recommended'])

    def test_the_beyond_streams_the_mode_it_is_in(self):
        for hz, wire, size in ((75.0, 'beyond-balanced', 2560), (90.0, 'beyond-90', 1920)):
            beyond = dict(BEYOND, refresh_hz=hz)
            (only,) = resolution_options(AUTO, beyond)
            self.assertEqual(only['label'], 'Set by the headset · %d Hz - %d x %d per eye' % (hz, size, size))
            # Whatever is saved: Auto, the Beyond's sizes until 1.0.0.12, another headset's choice.
            for saved in ('automatic', 'beyond-balanced', 'beyond-90', 'beyond-detail', 'sharp', 'recommended', 'index-120'):
                self.assertEqual(resolve_stream_preferences(dict(AUTO, resolution=saved), beyond)['resolution'], wire)
                self.assertEqual(selected_resolution({'resolution': saved}, beyond), 'automatic')
            self.assertEqual(stream_size('Hevc', wire, beyond), (size, size))
        # H.264 keeps it within 2048 pixels: the 90 Hz mode's 1920, at 75 Hz too.
        self.assertEqual(resolve_stream_preferences(dict(AUTO, codec='H264'), BEYOND)['resolution'], 'beyond-90')
        self.assertIn("set it with Bigscreen's Beyond Utility on a Windows PC", refresh_text('beyond'))

    def test_the_index_streams_native_at_every_rate(self):
        for mode in FAMILY_MODES['index']:
            resolved = resolve_stream_preferences(dict(AUTO, resolution=mode), HEADSET)
            self.assertEqual((resolved['codec'], resolved['resolution']), ('H264', 'sharp'))
            self.assertEqual(stream_size(resolved['codec'], resolved['resolution'], HEADSET), (2016, 2240))
        self.assertEqual([MODES[mode]['refresh_hz'] for mode in FAMILY_MODES['index']], [80, 90, 120, 144])
        self.assertTrue(MODES['index-144']['experimental'])
        self.assertEqual(resolve_stream_preferences(AUTO, HEADSET)['resolution'], 'sharp')

    def test_a_choice_the_headset_does_not_offer_follows_its_mode(self):
        for saved in ('sharp', 'compatibility', 'recommended', 'beyond-balanced', 'beyond-detail'):
            self.assertEqual(selected_resolution({'resolution': saved}, HEADSET), 'automatic')
            self.assertEqual(resolve_stream_preferences(dict(AUTO, resolution=saved), HEADSET)['resolution'], 'sharp')
        # An Index rate on another headset too (until 1.0.0.12 another headset's choice refused the Start), and it stays
        # saved for the Index.
        for headset in (BEYOND, OTHER):
            self.assertEqual(selected_resolution({'resolution': 'index-120'}, headset), 'automatic')
            self.assertNotIn('index-120', ids(resolution_options({'resolution': 'index-120'}, headset)))
        self.assertEqual(resolve_stream_preferences(dict(AUTO, resolution='index-120'), OTHER)['resolution'], 'compatibility')
        # Other headsets keep their sizes, unchanged; the Beyond's old sizes are not among them.
        self.assertEqual(ids(resolution_options(dict(AUTO, codec='H264'), OTHER)), ['automatic', 'compatibility', 'sharp', 'recommended'])
        self.assertEqual(selected_resolution({'resolution': 'sharp'}, OTHER), 'sharp')
        self.assertEqual(selected_resolution({'resolution': 'beyond-detail'}, OTHER), 'automatic')
        # With no headset known, a saved rate shows as saved, the Beyond's old sizes as Auto.
        self.assertEqual(selected_resolution({'resolution': 'index-120'}, None), 'index-120')
        self.assertEqual(selected_resolution({'resolution': 'beyond-balanced'}, None), 'automatic')

    def test_the_refresh_rate_is_asked_for_an_index_only(self):
        self.assertEqual(requested_refresh('index-144', 'index'), 144.0)
        self.assertIsNone(requested_refresh('automatic', 'index'))
        self.assertIsNone(requested_refresh('sharp', 'index'))
        self.assertIsNone(requested_refresh('index-120', 'beyond'))
        for saved in ('beyond-90', 'beyond-balanced', 'automatic'):  # the Beyond's mode is never asked for
            self.assertIsNone(requested_refresh(saved, 'beyond'))

    def test_hosts_before_1_0_0_13_get_their_old_sizes(self):
        self.assertEqual([wire_resolution(mode) for mode in FAMILY_MODES['index']], ['sharp'] * 4)
        self.assertEqual(wire_resolution('beyond-90'), 'beyond-90')
        sent = session_settings(resolve_stream_preferences(AUTO, dict(BEYOND, refresh_hz=90.0)))
        doc = json.loads(read('host/default-session.json'))
        width = configure(doc, codec='Hevc', resolution=sent['resolution'], headset=BEYOND)['session_settings']['video']
        self.assertEqual(width['transcoding_view_resolution']['Absolute']['width'], 1920)
        self.assertIn("'display-modes'", read('launcher/session_link.py'))
        # A host before 1.0.0.13 does not know the Beyond's 90 Hz stream: it gets 2016, as 1.0.0.12 sent a Beyond.
        import test_session_lifecycle
        harness = test_session_lifecycle.SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        app = harness.app('client')
        for features, expected in ((['foveation', 'display-modes'], 'beyond-90'), (['foveation'], 'sharp')):
            app.session_link.host_features = features
            self.assertEqual(app.host_preferences(sent)['resolution'], expected)
        self.assertTrue(any('older than 1.0.0.13' in line for line in app.lines))

    def companion(self, live=None, cached=None, connected=None, resolution='automatic'):
        fake = SimpleNamespace(client_runtime=SimpleNamespace(headset=live), settings={'cached_headset': cached,
                               'resolution': resolution, 'codec': 'Auto'}, connected_family=lambda: connected)
        fake.known_headset = lambda: Application.known_headset(fake)
        return fake

    def test_the_companion_knows_the_headset_before_a_session(self):
        self.assertEqual(Application.known_headset(self.companion(live=HEADSET, cached=BEYOND)), (HEADSET, 'detected'))
        self.assertEqual(Application.known_headset(self.companion(cached=BEYOND))[1], 'last')
        self.assertEqual(Application.known_headset(self.companion(cached=BEYOND, connected='beyond'))[1], 'last')
        headset, source = Application.known_headset(self.companion(cached=BEYOND, connected='index'))
        self.assertEqual((headset['model'], source), ('Index', 'display'))  # another headset is plugged in now
        self.assertEqual(Application.known_headset(self.companion()), (None, None))
        for platform in ('linux', 'win32'):  # the same on both systems
            with patch('launcher.app.sys.platform', platform):
                self.assertEqual(Application.session_refresh(self.companion(cached=HEADSET, resolution='index-120')), 120.0)
                self.assertIsNone(Application.session_refresh(self.companion(cached=BEYOND, resolution='beyond-90')))
                self.assertIsNone(Application.session_refresh(self.companion(cached=BEYOND, resolution='index-120')))
                self.assertIsNone(Application.session_refresh(self.companion(resolution='index-120')))

    def test_the_connected_headset_comes_from_its_display(self):
        fake = SimpleNamespace(role='client')
        rows = {'one': [{'model': 'Bigscreen Beyond'}], 'two': [{'model': 'Bigscreen Beyond'}, {'model': 'Valve Index'}],
                'unknown': [{'model': 'VR headset'}], 'none': []}
        expected = {'one': 'beyond', 'two': None, 'unknown': None, 'none': None}
        for name, found in rows.items():
            fake._connected_family = None
            with patch('launcher.app.headset_displays', return_value=found), patch('launcher.app.sys.platform', 'linux'):
                self.assertEqual(Application.connected_family(fake), expected[name], name)
        with patch('launcher.app.sys.platform', 'win32'):
            fake._connected_family = None
            self.assertIsNone(Application.connected_family(fake))

    def test_an_index_rate_the_headset_did_not_take_is_said_plainly(self):
        fake = self.companion(resolution='index-144')
        note = Application.refresh_outcome(fake, dict(HEADSET, refresh_hz=120.0), {'stream_width': 2016, 'stream_height': 2240})
        self.assertIn('runs the Index at 120 Hz, not the 144 Hz chosen: 144 Hz is experimental', note)
        self.assertIn('2016 x 2240 per eye at 120 Hz', note)
        self.assertIsNone(Application.refresh_outcome(fake, dict(HEADSET, refresh_hz=144.0)))
        for saved in ('beyond-90', 'index-120', 'automatic'):  # a Beyond has nothing chosen to miss
            fake.settings['resolution'] = saved
            self.assertIsNone(Application.refresh_outcome(fake, BEYOND))

    def test_the_lighter_choice_when_the_graphics_fall_behind(self):
        def lighter(headset, resolution, codec='Auto'):
            fake = self.companion(live=headset, resolution=resolution)
            fake.settings['codec'] = codec
            return Application.sharp_preset(fake)
        index = lighter(dict(HEADSET, refresh_hz=144.0), 'index-144')
        self.assertEqual(index, {'id': 'index-120', 'label': '120 Hz', 'width': 2016, 'height': 2240, 'refresh_hz': 120.0})
        self.assertEqual(lighter(dict(HEADSET, refresh_hz=80.0), 'index-80'), {'id': None})  # its lowest rate
        # A Beyond: nothing to choose here. Streaming its 75 Hz mode's 2560, the advice names the 90 Hz mode.
        self.assertEqual(lighter(BEYOND, 'automatic'), {'id': None, 'hint': BEYOND_LIGHTER_MODE})
        self.assertEqual(lighter(dict(BEYOND, refresh_hz=90.0), 'automatic'), {'id': None})
        self.assertEqual(lighter(BEYOND, 'automatic', 'H264'), {'id': None})  # 1920 already
        self.assertEqual(lighter(OTHER, 'recommended')['id'], 'sharp')
        snapshot = {'episodes': 2, 'limited': True, 'recent_throttled_s': 12, 'window_s': 30}
        offered = advice(snapshot, 2016, 144, smaller=index)
        self.assertIn('Choose 120 Hz (2016 x 2240 per eye) for a steady 120 fps', offered['message'])
        self.assertIn('use-standard', offered['actions'])
        self.assertEqual(offered['short'], 'PC graphics at their limit · 120 Hz for 120 fps')
        hinted = advice(snapshot, 2560, 75, smaller={'id': None, 'hint': BEYOND_LIGHTER_MODE})
        self.assertIn("Switch it with Bigscreen's Beyond Utility on a Windows PC.", hinted['message'])
        self.assertNotIn('use-standard', hinted['actions'])
        self.assertIsNone(hinted['smaller'])
        self.assertEqual(hinted['short'], 'PC graphics at their limit')


class RefreshSettingTests(unittest.TestCase):
    """1.0.0.13: a chosen Index rate goes into SteamVR's settings on this PC for the session
    (steamvr.preferredRefreshRate) and is put back afterwards, through the same receipt as the stream-only switches."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'steamvr.vrsettings'
        self.runtime = ClientRuntime(self.root / 'native', self.root, lambda _: None)
        for item in (patch('launcher.client_runtime.settings_path', return_value=self.path),
                     patch('launcher.client_runtime.runtime_processes', return_value={})):
            item.start()
            self.addCleanup(item.stop)

    def settings(self):
        return json.loads(self.path.read_text())

    def test_a_chosen_rate_is_set_for_the_session_and_put_back(self):
        self.path.write_text(json.dumps({'steamvr': {'preferredRefreshRate': 90.0, 'renderTargetMultiplier': 1.2}}))
        self.runtime.prepare_settings(144)
        self.assertEqual(self.settings()['steamvr']['preferredRefreshRate'], 144.0)
        receipt = json.loads(self.runtime.receipt.read_text())
        self.assertEqual((receipt['version'], len(receipt['preferences'])), (4, 4))
        self.assertTrue(self.runtime.restore())
        self.assertEqual(self.settings()['steamvr'], {'preferredRefreshRate': 90.0, 'renderTargetMultiplier': 1.2})
        self.assertFalse(self.runtime.receipt.exists())

    def test_an_absent_rate_goes_again_and_a_change_meanwhile_is_kept(self):
        self.path.write_text('{}')
        self.runtime.prepare_settings(120)
        self.assertTrue(self.runtime.restore())
        self.assertNotIn('preferredRefreshRate', self.settings()['steamvr'])
        self.runtime.prepare_settings(120)
        changed = self.settings()
        changed['steamvr']['preferredRefreshRate'] = 80.0  # the user chose another rate in SteamVR meanwhile
        self.path.write_text(json.dumps(changed))
        self.assertTrue(self.runtime.restore())
        self.assertEqual(self.settings()['steamvr']['preferredRefreshRate'], 80.0)

    def test_without_a_rate_nothing_about_it_changes(self):
        self.path.write_text(json.dumps({'steamvr': {'preferredRefreshRate': 120.0}}))
        self.runtime.prepare_settings()
        self.assertEqual(self.settings()['steamvr']['preferredRefreshRate'], 120.0)
        self.assertEqual(len(json.loads(self.runtime.receipt.read_text())['preferences']), 3)
        self.assertTrue(self.runtime.restore())

    def test_receipts_from_before_1_0_0_13_still_restore(self):
        self.path.write_text(json.dumps({'steamvr': {'enableHomeApp': False, 'sendSystemButtonToAllApps': True},
                                         'dashboard': {'enableDashboard': False}}))
        previous = [dict(section=s, key=k, present=True, original=not v, applied=v)
                    for (s, k), v in (((('steamvr', 'enableHomeApp')), False), (('dashboard', 'enableDashboard'), False),
                                      (('steamvr', 'sendSystemButtonToAllApps'), True))]
        self.runtime.receipt.write_text(json.dumps(dict(version=3, path=str(self.path), preferences=previous)))
        self.assertTrue(self.runtime.restore())
        self.assertTrue(self.settings()['steamvr']['enableHomeApp'])

    def test_a_receipt_with_an_impossible_rate_is_refused_and_kept(self):
        self.path.write_text('{}')
        self.runtime.prepare_settings(90)
        receipt = json.loads(self.runtime.receipt.read_text())
        for bad in (True, 500.0, 'fast'):
            receipt['preferences'][-1]['applied'] = bad
            self.runtime.receipt.write_text(json.dumps(receipt))
            self.assertFalse(self.runtime.restore())
            self.assertTrue(self.runtime.receipt.exists())
        with self.assertRaisesRegex(ValueError, 'refresh rate'):
            self.runtime.receipt.unlink()
            self.runtime.prepare_settings(10)
        self.assertEqual(REFRESH, ('steamvr', 'preferredRefreshRate'))

    def test_a_failed_start_names_the_rate_it_asked_for(self):
        self.assertIn("'requested_refresh_hz':self.requested_refresh", read('launcher/client_runtime.py'))
        self.assertIn('VROSTBridge asked SteamVR for', read('launcher/client_runtime.py'))


if __name__ == '__main__':
    unittest.main()
