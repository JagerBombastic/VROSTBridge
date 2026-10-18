"""VROSTBridge 1.0.0.14: what the first external testers and 1.0.0.13's first session asked for."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from launcher.adapter_counters import AdapterCounters, linux_udp_sockets  # noqa: E402
from launcher.last_session import FILE_NAME as LAST_SESSION_FILE, LastSessionRecord, driver_statistics  # noqa: E402
from launcher.headset import automatic_codec, parse_decoder_profiles, resolution_options, resolve_stream_preferences  # noqa: E402
from launcher.network_settings import DROP_IN, NEEDED_BYTES, WANTED_BYTES, NetworkSettings, drop_in_text, shared_network  # noqa: E402
from launcher.adapter_counters import elsewhere  # noqa: E402
from launcher.wifi_others import OtherWifiPause  # noqa: E402
from headset_fixture import HEADSET  # noqa: E402
from host.profile import RENDER_OVERSAMPLE_MAX, configure, render_width  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


UDP_TABLE = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  100: 00000000:26D8 00000000:0000 07 00000000:00000000 00:00000000 00000000  1000        0 41234 2 0000000000000000 9321
  101: 0100007F:232D 00000000:0000 07 00000000:00000000 00:00000000 00000000  1000        0 41235 2 0000000000000000 28
  102: 00000000:14E9 00000000:0000 07 00000000:00000000 00:00000000 00000000   995        0 20111 2 0000000000000000 0
"""


class VersionTests(unittest.TestCase):
    def test_every_program_is_1_0_0_14(self):
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        self.assertEqual((CLIENT_VERSION, HOST_VERSION), ('1.0.0.14', '1.0.0.14'))
        version = read('core/version.hpp')
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "1.0.0.14"', version)
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "1.0.0.14"', version)
        self.assertIn('project(VROSTBridge VERSION 1.0.0.14 ', read('CMakeLists.txt'))
        self.assertIn('version=1.0.0.14\n', read('packaging/install-bazzite.sh'))
        nsi = read('packaging/windows-installer.nsi')
        for line in ('Name "VROSTBridge Host 1.0.0.14"', 'VIProductVersion "1.0.0.14"', '"DisplayVersion" "1.0.0.14"',
                     'InstallDir "$LOCALAPPDATA\\VROSTBridge\\Host\\1.0.0.14"'):
            self.assertIn(line, nsi)
        self.assertNotIn('1.0.0.13', nsi)
        resource = read('packaging/windows-icon.rc')
        self.assertIn('FILEVERSION 1,0,0,14', resource)
        self.assertIn('PRODUCTVERSION 1,0,0,14', resource)
        self.assertIn('native launcher 1.0.0.14 ---', read('packaging/windows-launcher.c'))
        self.assertIn("VERSION = '1.0.0.14'", read('launcher/install_maintenance.py'))
        self.assertIn("'VROSTBridge 1.0.0.14 / ALVR'", read('host/profile.py'))
        sys.path.insert(0, str(ROOT / 'packaging'))
        import build_distributions
        import build_windows_client
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-1.0.0.14.zip')
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-1.0.0.14.exe')
        self.assertEqual(build_windows_client.client_version(), '1.0.0.14')
        self.assertEqual(build_windows_client.setup_name(), 'VROSTBridge-Client-Setup-1.0.0.14.exe')
        self.assertEqual(build_windows_client.portable_name(), 'VROSTBridge-Client-Portable-1.0.0.14.zip')
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.14'))


class SocketDropTests(unittest.TestCase):
    """1.0.0.13's first session: the adapters dropped nothing and the kernel's RcvbufErrors rose 9,349 times. The
    report now names the socket (the stream's UDP 9944, the gaze relay's 9005) from /proc/net/udp's drops column."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'udp').write_text(UDP_TABLE)
        (self.root / 'udp6').write_text(UDP_TABLE.splitlines()[0] + '\n'
                                        '  5: 00000000000000000000000000000000:26D8 00000000000000000000000000000000:0000 07 '
                                        '00000000:00000000 00:00000000 00000000  1000        0 41236 2 0000000000000000 7\n')

    def test_drops_by_local_port_over_both_tables(self):
        found = linux_udp_sockets((9944, 9005, 5353), paths=(self.root / 'udp', self.root / 'udp6'))
        self.assertEqual(found, {9944: 9321 + 7, 9005: 28, 5353: 0})
        self.assertEqual(linux_udp_sockets((9944,), paths=(self.root / 'missing',)), {})

    def test_the_session_reports_what_each_socket_dropped(self):
        readings = iter([{'adapters': {'eth0': {'rx_packets': 10}}, 'udp': {'RcvbufErrors': 100}, 'sockets': {'stream': 50, 'gaze': 1}},
                         {'adapters': {'eth0': {'rx_packets': 900}}, 'udp': {'RcvbufErrors': 160}, 'sockets': {'stream': 110, 'gaze': 1}}])
        counters = AdapterCounters(read=lambda: next(readings), driver=lambda interface: {})
        counters.begin('eth0')
        counters.end()
        snapshot = counters.snapshot()
        self.assertEqual(snapshot['sockets'], {'stream': 60})  # the gaze relay dropped nothing more
        self.assertEqual(snapshot['udp'], {'RcvbufErrors': 60})
        self.assertEqual(snapshot['stream_drops'], 0, 'the adapter itself dropped nothing')


CABLE = {'interface': 'enp1', 'address': '192.168.1.116', 'prefix': 24, 'physical': True}
WIFI = {'interface': 'wlp1', 'address': '192.168.1.233', 'prefix': 24, 'physical': True}
OTHER = {'interface': 'wlp2', 'address': '10.0.0.5', 'prefix': 24, 'physical': True}


class NetworkSettingsTests(unittest.TestCase):
    """The first 1.0.0.14 session with Wi-Fi connected received the video on the Wi-Fi card (7.5 million packets
    against 77 thousand on the cable): Linux answered the host's ARP for the cable's address there. And 1.0.0.14's
    first build took rmem_default for the receive buffer limit and offered a fix to a PC whose stream had 8 MiB."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'net'
        for folder in ('core', 'ipv4/conf/all', 'ipv4/conf/enp1'):
            (self.proc / folder).mkdir(parents=True)
        self.drop_in = self.root / 'etc/sysctl.d/70-vrostbridge-network.conf'
        self.logs, self.commands = [], []
        self.records = [CABLE, WIFI]
        self.values(4194304, arp_ignore=0, arp_announce=0)

    def values(self, rmem_max, rmem_default=212992, arp_ignore=0, arp_announce=0):
        (self.proc / 'core/rmem_max').write_text(str(rmem_max) + '\n')
        (self.proc / 'core/rmem_default').write_text(str(rmem_default) + '\n')
        (self.proc / 'ipv4/conf/all/arp_ignore').write_text(str(arp_ignore) + '\n')
        (self.proc / 'ipv4/conf/all/arp_announce').write_text(str(arp_announce) + '\n')

    def runner(self, arguments, timeout):
        self.commands.append(arguments)
        if 'install' in arguments[1]:
            self.drop_in.parent.mkdir(parents=True, exist_ok=True)
            self.drop_in.write_text(Path(arguments[-2]).read_text())
        elif 'sysctl' in arguments[1]:
            self.values(WANTED_BYTES, WANTED_BYTES, 1, 2)
        return Mock(returncode=0, stdout='', stderr='')

    def settings(self, platform='linux'):
        return NetworkSettings(runner=self.runner, log=self.logs.append, proc=self.proc, drop_in=self.drop_in,
                               platform=platform, addresses=lambda: self.records)

    def test_one_network_twice_is_found_and_a_large_enough_buffer_is_left_alone(self):
        with patch('launcher.network_settings._tool', side_effect=lambda name: '/usr/bin/' + name):
            status = self.settings().check('enp1')
        self.assertEqual((status['state'], status['needs'], status['shared']), ('needed', ['arp'], ['enp1', 'wlp1']))
        self.assertEqual(status['stream_buffer_bytes'], 8388608, 'the transport asks for the most and Linux doubles it')
        self.assertIn('more than once (enp1, wlp1)', status['message'])
        self.assertIn('Wi-Fi can stay connected', status['message'])
        self.records = [CABLE, OTHER]
        self.assertEqual(self.settings().check('enp1')['state'], 'ok', 'another network is no concern')
        self.records = [CABLE, dict(WIFI, physical=False)]
        self.assertEqual(self.settings().check('enp1')['state'], 'ok', 'a virtual adapter is not a second connection')
        self.assertEqual(self.settings(platform='win32').check('enp1')['state'], 'not_needed')

    def test_a_stock_kernel_buffer_is_too_small_and_the_settings_once_applied_are_enough(self):
        self.values(212992, arp_ignore=1, arp_announce=2)
        status = self.settings().check('enp1')
        self.assertEqual((status['state'], status['needs']), ('needed', ['buffer']))
        self.assertIn('416 KiB', status['message'])
        self.assertNotIn('Wi-Fi can stay connected', status['message'])
        self.values(NEEDED_BYTES // 2, arp_ignore=1, arp_announce=2)
        self.assertEqual(self.settings().check('enp1')['state'], 'ok')
        self.assertTrue(self.settings().separated('enp1'))
        self.values(4194304, arp_ignore=0, arp_announce=0)
        (self.proc / 'ipv4/conf/enp1/arp_ignore').write_text('1\n')
        (self.proc / 'ipv4/conf/enp1/arp_announce').write_text('2\n')
        self.assertTrue(self.settings().separated('enp1'), 'the larger of all and the adapter\'s own applies')
        self.assertFalse(self.settings().separated(''))
        self.assertTrue(self.settings(platform='win32').separated('enp1'), 'Windows answers ARP only on its own adapter')

    def test_repair_writes_one_drop_in_and_applies_it_through_pkexec_without_a_shell(self):
        with patch('launcher.network_settings._tool', side_effect=lambda name: '/usr/bin/' + name):
            settings = self.settings()
            settings.check('enp1')
            after = settings.repair()
        self.assertEqual(after['state'], 'ok')
        self.assertEqual([c[:2] for c in self.commands], [['/usr/bin/pkexec', '/usr/bin/install'], ['/usr/bin/pkexec', '/usr/bin/sysctl']])
        self.assertEqual(self.commands[1][-2:], ['-p', str(self.drop_in)])
        self.assertEqual(self.drop_in.read_text(), drop_in_text())
        for line in ('net.ipv4.conf.all.arp_ignore = 1\n', 'net.ipv4.conf.all.arp_announce = 2\n',
                     'net.core.rmem_max = %d\n' % WANTED_BYTES):
            self.assertIn(line, drop_in_text())
        self.assertTrue(any('arp_ignore 1, arp_announce 2' in line for line in self.logs))
        self.assertEqual(str(DROP_IN), '/etc/sysctl.d/70-vrostbridge-network.conf')

    def test_a_cancelled_prompt_or_missing_tool_changes_nothing_and_says_so(self):
        def cancelled(arguments, timeout):
            self.commands.append(arguments)
            return Mock(returncode=126, stdout='', stderr='Request dismissed')
        with patch('launcher.network_settings._tool', side_effect=lambda name: '/usr/bin/' + name):
            result = NetworkSettings(runner=cancelled, log=self.logs.append, proc=self.proc, drop_in=self.drop_in,
                                     platform='linux', addresses=lambda: self.records).repair()
        self.assertEqual(result['state'], 'needed')
        self.assertIn('Request dismissed', result['message'])
        self.assertEqual(len(self.commands), 1, 'sysctl is not run after a failed install')
        with patch('launcher.network_settings._tool', return_value=None):
            result = self.settings().repair()
        self.assertIn('/etc/sysctl.d yourself', result['message'])
        self.assertIn('net.ipv4.conf.all.arp_ignore = 1', result['message'])

    def test_shared_networks(self):
        self.assertEqual(shared_network([CABLE, WIFI, OTHER]), ['enp1', 'wlp1'])
        self.assertEqual(shared_network([CABLE, WIFI, OTHER], 'wlp2'), [])
        self.assertEqual(shared_network([CABLE, dict(CABLE, address='192.168.1.117')]), [], 'two addresses on one adapter')

    def test_the_companion_offers_it_on_the_page_and_in_game_mode(self):
        app = read('launcher/app.py')
        self.assertIn("if action=='fix-network' and self.role=='client':", app)
        self.assertIn("network_settings=self.network_settings.status()", app)
        page = read('launcher/desktop.html')
        self.assertIn('id="fix-network"', page)
        self.assertIn("network.state!=='needed'", page)
        self.assertNotIn('socket-buffer', page)
        self.assertIn("'fix-network', 'Fix this PC\\'s network settings'", read('launcher/game_ui.py'))
        self.assertFalse((ROOT / 'launcher/socket_buffers.py').exists())


class WifiPauseTests(unittest.TestCase):
    """The pause cut one PC off from the host (every Start: 'Network is unreachable'); a wired stream needs it only
    where the system would otherwise answer for the cable's address on the Wi-Fi card."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls, self.logs = [], []

    def pause(self):
        def runner(arguments, timeout):
            self.calls.append(arguments[-2:])
            return Mock(returncode=0, stdout='', stderr='')
        pause = OtherWifiPause(self.temp.name, runner=runner, log=self.logs.append)
        pause.nmcli = '/usr/bin/nmcli'
        pause.others = lambda interface, source_ip: ['wlp1']
        return pause

    def test_a_wired_stream_with_the_settings_leaves_wifi_connected(self):
        with patch('launcher.wifi_others.sys.platform', 'linux'):
            status = self.pause().begin('enp1', '192.168.1.116', True, wired=True, separated=True)
        self.assertEqual(status['state'], 'kept')
        self.assertEqual(self.calls, [])
        self.assertIn('stays connected: the cable carries the stream', status['message'])

    def test_without_them_it_pauses_and_a_pause_that_cut_the_host_off_is_undone_at_once(self):
        with patch('launcher.wifi_others.sys.platform', 'linux'):
            pause = self.pause()
            status = pause.begin('enp1', '192.168.1.116', True, wired=True, separated=False)
            self.assertEqual((status['state'], self.calls), ('paused', [['disconnect', 'wlp1']]))
            status = pause.resume_now('pausing it cut this PC off from the host over enp1')
        self.assertEqual(status['state'], 'resumed')
        self.assertEqual(self.calls[-1], ['connect', 'wlp1'])
        self.assertEqual(pause.status()['paused'], [])
        self.assertFalse((Path(self.temp.name) / 'paused-wifi.json').exists(), 'nothing left to restore later')

    def test_a_wifi_stream_still_pauses_the_other_wifi(self):
        with patch('launcher.wifi_others.sys.platform', 'linux'):
            status = self.pause().begin('wlp9', '192.168.1.9', True, wired=False, separated=True)
        self.assertEqual(status['state'], 'paused')

    def test_the_companion_checks_the_host_after_a_pause(self):
        app = read('launcher/app.py')
        self.assertIn("if other.get('state')=='paused' and not self.host_reachable(interface,source_ip,cancel):", app)
        self.assertIn("self.other_wifi.resume_now(", app)
        self.assertIn("['ip','-4','route','get',host_ip,'from',source_ip,'oif',interface]", app)


class VideoPathTests(unittest.TestCase):
    def test_the_video_on_another_adapter_is_named(self):
        session = {'stream_interface': 'enp1', 'adapters': {'enp1': {'rx_packets': 77653}, 'wlp1': {'rx_packets': 7545476}}}
        self.assertEqual(elsewhere(session), {'adapter': 'wlp1', 'packets': 7545476, 'stream_adapter': 'enp1', 'stream_packets': 77653})
        self.assertIsNone(elsewhere({'stream_interface': 'enp1', 'adapters': {'enp1': {'rx_packets': 23115400}, 'wlp1': {'rx_packets': 1}}}))
        self.assertIsNone(elsewhere({'stream_interface': 'enp1', 'adapters': {'enp1': {'rx_packets': 10}, 'wlp1': {'rx_packets': 2000}}}),
                          'too few packets to say')
        self.assertIsNone(elsewhere({'state': 'idle'}))

    def test_the_companion_says_so_once_per_session(self):
        app = read('launcher/app.py')
        self.assertIn('self.check_video_path()', app)
        self.assertIn("self.log('VIDEO_PATH The video is arriving over '", app)
        self.assertIn("'video_path':self.video_path", app)


class HomeNetworkTests(unittest.TestCase):
    """The host refused every session until Windows' network was switched to Private, which Windows undoes whenever it
    decides a home network is new. Paired headset PCs now start on any network type; the firewall still admits only
    the local subnet, and a new headset PC pairs itself only from a Private network."""

    def test_the_firewall_rules_apply_on_any_network_type_but_only_to_the_local_subnet(self):
        for path in ('host/Host-Setup.ps1', 'launcher/Client-Firewall.ps1'):
            script = read(path)
            self.assertIn('-Profile Any -RemoteAddress LocalSubnet', script, path)
            self.assertIn("$rule.Profile -ne 'Any'", script, path)
            self.assertNotIn('-Profile Private', script, path)
        self.assertIn("'LocalSubnet'", read('host/Host-Setup.ps1'))

    def test_pairing_a_new_headset_pc_still_needs_a_private_network_but_starting_does_not(self):
        from types import SimpleNamespace
        from launcher.app import Application
        logs = []
        app = SimpleNamespace(settings={'auto_connect': True}, private_session_allowed=lambda local=None: True, log=logs.append)
        with patch('launcher.app.pairing_network_private', return_value=True):
            self.assertTrue(Application.allow_session_pairing(app, '192.168.1.20'))
        with patch('launcher.app.pairing_network_private', side_effect=ValueError('A new headset PC pairs automatically only on a network Windows classes as Private')):
            self.assertFalse(Application.allow_session_pairing(app, '192.168.1.20'))
        self.assertIn('Pairing refused: A new headset PC pairs automatically only', logs[-1])
        app.settings['auto_connect'] = False
        self.assertFalse(Application.allow_session_pairing(app, '192.168.1.20'))
        app = read('launcher/app.py')
        self.assertIn('allow_start=self.private_session_allowed', app)
        self.assertNotIn('pairing_network_private', app[app.index('    def private_session_allowed'):app.index('    def allow_session_pairing')])


class AutoCodecTests(unittest.TestCase):
    """Until 1.0.0.13 Auto codec was H.264 on everything but a Beyond, which left Index testers on the weakest codec
    without 10-bit. Auto is HEVC for every headset now; H.264 only where the native client found no HEVC decoder."""

    WINDOWS = "STATUS SteamVR's GPU for D3D11VA: Example Graphics (DXGI adapter 0); decoder profiles h264=1 hevc=0 hevc_main10=0 av1=0 av1_10bit=0"
    LINUX = 'STATUS VA-API decoder profiles on /dev/dri/renderD128: h264=1 hevc=1 hevc_main10=1 av1=1 av1_10bit=1'

    def test_auto_is_hevc_unless_the_decoder_lacks_it(self):
        auto = {'codec': 'Auto', 'bitrate': 80, 'resolution': 'automatic'}
        self.assertEqual(resolve_stream_preferences(auto, HEADSET)['codec'], 'Hevc')
        other = dict(HEADSET, model='Generic HMD', manufacturer='Example')
        self.assertEqual(resolve_stream_preferences(auto, other)['codec'], 'Hevc')
        without = parse_decoder_profiles(self.WINDOWS)
        self.assertEqual(resolve_stream_preferences(dict(auto, decoder_profiles=without), HEADSET)['codec'], 'H264')
        self.assertEqual(automatic_codec(None), 'Hevc')
        self.assertEqual(automatic_codec({'hevc': False, 'h264': False}), 'Hevc', 'nothing known to decode: HEVC, the common case')
        # The choices' sizes follow the codec Auto means: High before Custom on other headsets, as with HEVC chosen.
        self.assertEqual([o['id'] for o in resolution_options(auto, other)], ['automatic', 'sharp', 'compatibility', 'recommended'])
        self.assertEqual([o['id'] for o in resolution_options(dict(auto, decoder_profiles=without), other)],
                         ['automatic', 'compatibility', 'sharp', 'recommended'])

    def test_the_native_clients_profile_lines_are_read_on_both_systems(self):
        windows = parse_decoder_profiles(self.WINDOWS)
        self.assertEqual(windows, {'h264': True, 'hevc': False, 'hevc_main10': False, 'av1': False, 'av1_10bit': False,
                                   'backend': 'D3D11VA', 'device': 'Example Graphics'})
        linux = parse_decoder_profiles(self.LINUX)
        self.assertEqual((linux['backend'], linux['device'], linux['hevc'], linux['av1_10bit']), ('VA-API', '/dev/dri/renderD128', True, True))
        self.assertIsNone(parse_decoder_profiles('STATUS VA-API decoder profiles not read: no render node'))
        self.assertIsNone(parse_decoder_profiles('METRICS decoded=1'))
        # The Linux client prints the line at start, from libva on SteamVR's render node, as the Windows client prints its D3D11 profiles.
        self.assertIn('vaapiDecoderProfiles(renderNode)', read('client/platform/linux/vulkan.cpp'))
        self.assertIn('VAProfileHEVCMain10', read('client/platform/linux/vaapi_profiles.hpp'))
        self.assertIn('PkgConfig::VA', read('CMakeLists.txt'))

    def test_the_companion_remembers_the_profiles_for_the_next_start(self):
        import test_session_lifecycle
        harness = test_session_lifecycle.SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        app = harness.app('client')
        app.remember_decoders(parse_decoder_profiles(self.WINDOWS))
        self.assertFalse(app.settings['decoder_profiles']['hevc'])
        self.assertEqual(json.loads(app.settings_path.read_text())['decoder_profiles']['backend'], 'D3D11VA')
        self.assertTrue(any(line.startswith('DECODERS D3D11VA on Example Graphics') and 'no HEVC' in line for line in app.lines))
        app.remember_decoders(parse_decoder_profiles(self.WINDOWS))
        self.assertEqual(sum(line.startswith('DECODERS') for line in app.lines), 1, 'logged only when it changes')
        app.remember_decoders(None)
        self.assertIn("'decoder profiles' in line:self.remember_decoders(parse_decoder_profiles(line))", read('launcher/app.py'))


class RenderSizeTests(unittest.TestCase):
    """A headset on its own PC renders at SteamVR's recommended size, larger than the panels; until 1.0.0.13 the host
    rendered at exactly the stream width. It renders at the headset PC's recommended size now, up to 1.5 x the
    stream, and the encoder's input is scaled down from it. The stream itself does not change."""

    def test_the_render_size_follows_the_recommendation_within_bounds(self):
        beyond = dict(HEADSET, model='Beyond', manufacturer='Bigscreen', width=2712, height=2712, refresh_hz=75.0)
        self.assertEqual(render_width(2560, beyond), 2688)  # 2712 aligned to 32
        self.assertEqual(render_width(2560, dict(beyond, width=3560)), 3552)  # a 150 % setting on the headset PC
        self.assertEqual(render_width(2560, dict(beyond, width=4200)), 3840)  # capped at 1.5 x
        self.assertEqual(render_width(2016, HEADSET), 2016)  # the Index at 100 %: already the recommended size
        self.assertEqual(render_width(2016, dict(HEADSET, width=2468, height=2740)), 2464)
        self.assertEqual(render_width(2016, dict(HEADSET, width=1500)), 2016, 'never below the stream')
        self.assertEqual(render_width(1440), 1440)
        self.assertEqual(RENDER_OVERSAMPLE_MAX, 1.5)

    def test_the_host_profile_renders_larger_and_streams_the_same(self):
        doc = json.loads(read('host/default-session.json'))
        beyond = dict(HEADSET, model='Beyond', manufacturer='Bigscreen', width=2712, height=2712, refresh_hz=75.0)
        video = configure(doc, codec='Hevc', resolution='beyond-balanced', headset=beyond)['session_settings']['video']
        self.assertEqual(video['transcoding_view_resolution']['Absolute']['width'], 2560)
        self.assertEqual(video['emulated_headset_view_resolution']['Absolute']['width'], 2688)
        self.assertFalse(video['emulated_headset_view_resolution']['Absolute']['height']['set'])
        video = configure(doc, codec='Hevc', resolution='recommended', headset=beyond)['session_settings']['video']
        self.assertEqual((video['transcoding_view_resolution']['Absolute']['width'], video['emulated_headset_view_resolution']['Absolute']['width']), (2688, 2688))
        self.assertIn('rendered at ', read('launcher/app.py'))
        self.assertIn('scales it down for the stream', read('launcher/desktop.html'))


class HostLastSessionTests(unittest.TestCase):
    """The 1.0.0.13 report from real hardware arrived with the host side empty: its companion had been restarted. The
    host now keeps its last session's figures in a file, written every minute and at Stop, and a report made before
    the next session carries them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_record_is_written_on_time_and_read_back(self):
        clock = [1000.0]
        record = LastSessionRecord(self.root, clock=lambda: clock[0])
        self.assertTrue(record.due())
        self.assertTrue(record.keep({'host_events': {'statistics': {'VsyncLock': {'values': {'spread_ms': 0.7}}}}, 'driver_statistics': ['a']}))
        self.assertFalse(record.due())
        clock[0] += 61
        self.assertTrue(record.due())
        loaded = record.load()
        self.assertEqual(loaded['host_events']['statistics']['VsyncLock']['values']['spread_ms'], 0.7)
        self.assertIn('saved_utc', loaded)
        self.assertEqual((self.root / LAST_SESSION_FILE).name, 'host-last-session.json')
        (self.root / LAST_SESSION_FILE).write_text('{"broken"')
        self.assertIsNone(record.load())
        # An outsized record keeps its statistics and drops the log lines.
        self.assertTrue(record.keep({'host_events': {}, 'driver_statistics': ['x' * 1000] * 3000}))
        self.assertNotIn('driver_statistics', record.load())

    def test_the_drivers_statistics_lines_are_kept_without_tracking_data(self):
        log = self.root / 'session_log.txt'
        log.write_text('18:16:25.044 [INFO] VROST_HOST_AMF accepted=8045 output=8045\n'
                       '18:16:25.169 [INFO] VROST_NATIVE_HAND {"hand":"left"}\n'
                       '18:16:25.300 [INFO] [GRAPH] frame\n'
                       '18:16:25.342 [INFO] VROST_VSYNC_LOCK phase_ms=6.860\n'
                       '18:16:26.000 [INFO] Tracking "Tracking" VROST_X y\n')
        self.assertEqual(driver_statistics(log), ['18:16:25.044 [INFO] VROST_HOST_AMF accepted=8045 output=8045',
                                                  '18:16:25.342 [INFO] VROST_VSYNC_LOCK phase_ms=6.860'])
        self.assertEqual(driver_statistics(log, limit=1), ['18:16:25.342 [INFO] VROST_VSYNC_LOCK phase_ms=6.860'])
        self.assertEqual(driver_statistics(self.root / 'none'), [])

    def test_the_host_report_carries_the_record_until_a_session_streams(self):
        import test_session_lifecycle
        harness = test_session_lifecycle.SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        app = harness.app('host')
        self.assertIsNotNone(app.last_session)
        self.assertIsNone(app.local_report().get('host_last_session'), 'nothing kept yet')
        app.last_session.keep({'host_events': {'statistics': {'HostAMF': {'values': {'frames': 1}}}}})
        report = app.local_report()
        self.assertEqual(report['host_last_session']['host_events']['statistics']['HostAMF']['values']['frames'], 1)
        app.host_streamed = True  # a session has streamed since the companion started: its own figures are current
        self.assertNotIn('host_last_session', app.local_report())
        app.keep_last_session()
        kept = app.last_session.load()
        self.assertEqual(set(kept) >= {'version', 'host_events', 'session', 'adapter_counters', 'driver_statistics', 'saved_utc'}, True, sorted(kept))
        self.assertIn('if self.host_streamed:self.keep_last_session()', read('launcher/app.py'))


class MotionPredictionTests(unittest.TestCase):
    """The testers' "missing notes I clearly swung at": hands predicted 30 to 40 ms ahead at constant speed. The client
    now folds the measured acceleration into the pose and velocity it sends, so the host's first-order step lands on
    the second-order prediction without a change to the host or the wire (client/device/acceleration.hpp)."""

    def test_the_native_fold_in_is_built_tested_and_switchable(self):
        device = read('client/device/openvr_device.cpp')
        self.assertIn('accelerations[i].observe(sampleNow, m.linear, m.angular);', device)
        self.assertIn('accelerations[i].apply(m, double(predictionNs.load()) * 1e-9);', device)
        self.assertIn('if (acceleration_ && prediction_ && predictionNs.load())', device, 'nothing is folded in without host prediction')
        header = read('client/device/acceleration.hpp')
        self.assertIn('m.pose.position[i] = float(m.pose.position[i] - linear[i] * half);', header)
        self.assertIn('m.linear[i] = float(m.linear[i] + linear[i] * h);', header)
        main = read('client/platform/linux/main.cpp')
        self.assertIn('"--no-acceleration"', main)
        self.assertIn('acceleration_samples=', main)
        cmake = read('CMakeLists.txt')
        for name in ('acceleration_contracts', 'gaze_prediction_contracts'):
            self.assertIn('add_test(NAME ' + name, cmake)
        self.assertIn('with their acceleration folded in', device)

    def test_the_companion_keeps_it_on_and_can_turn_it_off_from_the_settings_file(self):
        import test_session_lifecycle
        harness = test_session_lifecycle.SessionLifecycleTests('test_client_start_reads_physical_headset_before_host_then_native')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        app = harness.app()
        app.launch = Mock()
        self.assertTrue(app.settings['prediction_acceleration'])
        app.action({'action': 'start'})
        self.assertNotIn('--no-acceleration', app.launch.call_args.args[0])
        app.settings['prediction_acceleration'] = False
        app.save_settings()
        app.action({'action': 'stop'})
        again = harness.app()
        again.launch = Mock()
        self.assertFalse(again.settings['prediction_acceleration'], 'kept across restarts, from the settings file')
        again.action({'action': 'start'})
        self.assertIn('--no-acceleration', again.launch.call_args.args[0])
        self.assertNotIn('prediction_acceleration', app.CLIENT_OPTIONS, 'a comparison switch, not a page setting')


if __name__ == '__main__':
    unittest.main()
