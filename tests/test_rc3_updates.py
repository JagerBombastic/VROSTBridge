"""RC3 regression checks: pipeline summary, capability negotiation with older hosts,
host profile quality options, host add-on detection, timing-lock flag and driver patch,
parallel startup and native in-session reconfiguration contracts."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from headset_fixture import HEADSET
from test_session_lifecycle import SessionLifecycleTests
from test_session_link import SessionTests, eventually

ROOT = Path(__file__).resolve().parents[1]


def graph(**overrides):
    value = {'total_pipeline_latency_s': .040, 'game_time_s': .006, 'server_compositor_s': .002, 'encoder_s': .004,
             'network_s': .007, 'decoder_s': .005, 'decoder_queue_s': .001, 'client_compositor_s': .003,
             'vsync_queue_s': .010, 'client_fps': 75.0, 'server_fps': 75.0, 'bitrate_bps': 80e6,
             'bitrate_directives': {}, 'throughput_bps': 300e6}
    value.update(overrides)
    return value


class PipelineStatsTests(unittest.TestCase):
    def setUp(self):
        from host.pipeline_stats import PipelineStats
        self.now = [100.0]
        self.stats = PipelineStats(window_s=5, clock=lambda: self.now[0])

    def test_summary_breaks_down_the_client_measured_total(self):
        for _ in range(30):
            self.assertTrue(self.stats.add(graph()))
        summary = self.stats.summary()
        self.assertEqual(summary['samples'], 30)
        self.assertEqual(summary['total_ms']['mean'], 40.0)
        self.assertEqual(summary['stages_ms'], {'render': 8.0, 'encode': 4.0, 'network': 7.0, 'decode': 6.0, 'display_wait': 13.0})
        self.assertEqual(summary['bitrate_mbps'], 80.0)
        self.assertAlmostEqual(summary['arrival']['wait_ms'], 3.0, places=1)
        self.assertLess(len(json.dumps(summary)), 2048)

    def test_arrival_phase_uses_circular_statistics_across_the_frame_boundary(self):
        # Half the frames just made pickup, half just missed it: the phase is at the
        # boundary (near zero), not the arithmetic mean of 6.7 ms.
        for i in range(40):
            self.stats.add(graph(client_compositor_s=.0002 if i % 2 else .0131))
        arrival = self.stats.summary()['arrival']
        self.assertTrue(arrival['wait_ms'] < 0.5 or arrival['wait_ms'] > 12.8, arrival)
        self.assertLess(arrival['spread_ms'], 1.0)

    def test_invalid_samples_and_old_samples_are_excluded(self):
        for bad in (graph(encoder_s=float('nan')), graph(network_s=-1), graph(total_pipeline_latency_s=5), {'x': 1}, None):
            self.assertFalse(self.stats.add(bad))
        for _ in range(12):
            self.stats.add(graph())
        self.assertIsNotNone(self.stats.summary())
        self.now[0] += 6
        self.assertIsNone(self.stats.summary())
        self.stats.add(graph())
        self.assertIsNone(self.stats.summary())  # fewer than the minimum samples


class HostEventsTests(unittest.TestCase):
    def test_timing_lock_lines_are_structured(self):
        from host.events import parse_vsync_lock
        line = ('VROST_VSYNC_LOCK phase_ms=3.712 spread_ms=0.600 target_ms=3.700 error_ms=0.012 '
                'correction_ms=-0.006 integral_ms=0.0012 concentration=0.990 steering=1 updates=42')
        value = parse_vsync_lock(line)
        self.assertEqual(value['steering'], 1)
        self.assertAlmostEqual(value['correction_ms'], -0.006)
        self.assertEqual(parse_vsync_lock('VROST_VSYNC_LOCK enabled=1 interval_us=13333 margin_ms=2.50'),
                         {'enabled': 1, 'interval_us': 13333, 'margin_ms': 2.5})
        self.assertIsNone(parse_vsync_lock('VROST_HOST_AMF frames=1'))


class SessionSettingsTests(unittest.TestCase):
    def test_new_fields_validate_and_are_stripped_for_older_hosts(self):
        from launcher.session_link import (session_settings, host_settings, SessionError,
                                           QUALITY_CAPABILITY)
        value = session_settings({'codec': 'Hevc', 'bitrate': 100, 'encoder': 'balanced', 'network': 'wired', 'timing_lock': False})
        self.assertEqual((value['encoder'], value['network'], value['timing_lock']), ('balanced', 'wired', False))
        for bad in ({'encoder': 'max'}, {'network': 'lte'}, {'timing_lock': 'yes'}):
            with self.assertRaises(SessionError):
                session_settings(bad)
        self.assertNotIn('encoder', host_settings(value, []))
        self.assertNotIn('timing_lock', host_settings(value, ['pipeline-1']))
        self.assertEqual(host_settings(value, [QUALITY_CAPABILITY]), value)


class CapabilityRoundTripTests(SessionTests):
    def test_capable_host_receives_quality_fields_and_reports_pipeline(self):
        self.host.telemetry = lambda: {'total_ms': {'mean': 40.0, 'p95': 44.0}}
        settings = {'codec': 'H264', 'bitrate': 120, 'encoder': 'balanced', 'network': 'wifi', 'timing_lock': True}
        self.client.start(settings, host_ip='127.0.0.1')
        eventually(lambda: self.started.called)
        self.assertEqual(self.started.call_args.args[0],
                         {'codec': 'H264', 'bitrate': 120, 'microphone': False, 'encoder': 'balanced', 'network': 'wifi', 'timing_lock': True})
        self.assertIn('quality-2', self.client.host_capabilities)
        self.client.heartbeat()
        self.assertEqual(self.client.snapshot()['host']['pipeline']['total_ms']['mean'], 40.0)
        self.host.telemetry = lambda: {'blob': 'x' * 5000}
        self.client.heartbeat()
        self.assertNotIn('pipeline', self.client.snapshot()['host'])  # bounded
        self.client.stop()

    def test_older_host_without_capabilities_still_starts(self):
        self.host.capabilities = ()
        self.client.start({'codec': 'H264', 'bitrate': 120, 'encoder': 'balanced', 'network': 'wired', 'timing_lock': True},
                          host_ip='127.0.0.1')
        eventually(lambda: self.started.called)
        self.assertEqual(self.started.call_args.args[0], {'codec': 'H264', 'bitrate': 120, 'microphone': False})
        self.client.stop()


class HostProfileTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((ROOT/'host/default-session.json').read_text())

    def test_cabac_encoder_priority_and_network_audio_buffer(self):
        from host.profile import configure
        fast = configure(self.document)['session_settings']
        self.assertEqual(fast['video']['encoder_config']['entropy_coding']['variant'], 'Cabac')
        self.assertEqual(fast['video']['encoder_config']['quality_preset']['variant'], 'Speed')
        self.assertEqual(fast['video']['encoder_config']['nvenc']['quality_preset']['variant'], 'P1')
        self.assertEqual(fast['audio']['game_audio']['content']['buffering']['average_buffering_ms'], 50)
        balanced = configure(self.document, encoder='balanced', network='wired')['session_settings']
        self.assertEqual(balanced['video']['encoder_config']['quality_preset']['variant'], 'Balanced')
        self.assertEqual(balanced['video']['encoder_config']['nvenc']['quality_preset']['variant'], 'P4')
        self.assertEqual(balanced['audio']['game_audio']['content']['buffering']['average_buffering_ms'], 30)
        wifi = configure(self.document, network='wifi')['session_settings']
        self.assertEqual(wifi['audio']['game_audio']['content']['buffering']['average_buffering_ms'], 50)
        with self.assertRaises(ValueError):
            configure(self.document, encoder='max')


class HostAddonTests(unittest.TestCase):
    def test_lists_enabled_third_party_drivers_only(self):
        from host.addons import detect
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def driver(path, name):
                path.mkdir(parents=True)
                (path/'driver.vrdrivermanifest').write_text(json.dumps({'name': name}))
                return path
            runtime = root/'SteamVR'
            for name in ('lighthouse', 'null', 'htc'):
                driver(runtime/'drivers'/name, name)
            driver(runtime/'drivers'/'vendorx', 'vendorx')
            own = driver(root/'VROSTBridge/dist/host', 'alvr_server')
            beyond = driver(root/'Bigscreen/driver', 'bigscreen_beyond')
            disabled = driver(root/'Old/driver', 'old_helper')
            config = root/'config'; config.mkdir()
            (config/'steamvr.vrsettings').write_text(json.dumps({'driver_old_helper': {'enable': False}}))
            registry = root/'openvrpaths.vrpath'
            registry.write_text(json.dumps({'runtime': [str(runtime)], 'config': [str(config)],
                                             'external_drivers': [str(own), str(beyond), str(disabled)]}))
            found = detect(registry=registry, own_driver='alvr_server', own_directory=own)
            self.assertEqual([item['name'] for item in found], ['bigscreen_beyond', 'vendorx'])
            self.assertIn('client PC', found[0]['advice'])
        self.assertEqual(detect(registry=Path('/nonexistent/openvrpaths.vrpath')), [])


class TimingLockFlagTests(unittest.TestCase):
    def test_driver_support_comes_from_its_build_manifest_and_flag_is_written(self):
        from launcher.app import Application
        with tempfile.TemporaryDirectory() as directory:
            host = Path(directory)
            fake = SimpleNamespace(host=host)
            self.assertFalse(Application.driver_supports_timing_lock(fake))
            (host/'vrostbridge-build.json').write_text(json.dumps({'source_patch_sha256': {'0006-host-chaperone-idempotence.patch': 'a'}}))
            self.assertFalse(Application.driver_supports_timing_lock(fake))
            (host/'vrostbridge-build.json').write_text(json.dumps({'source_patch_sha256': {'0007-host-frame-timing-lock.patch': 'b'}}))
            self.assertTrue(Application.driver_supports_timing_lock(fake))
            Application.write_timing_flag(fake, True)
            self.assertEqual(json.loads((host/'vrostbridge-timing.json').read_text()), {'vsync_lock': True, 'margin_ms': 2.5})
            Application.write_timing_flag(fake, False)
            self.assertFalse(json.loads((host/'vrostbridge-timing.json').read_text())['vsync_lock'])


class HostRepackTests(unittest.TestCase):
    """A rebuilt driver replaces the reused one only when its manifest matches the full patch set."""

    @staticmethod
    def load():
        import importlib.util
        spec = importlib.util.spec_from_file_location('repack_host_payload', ROOT/'packaging/repack_host_payload.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def previous_payload(root, repack):
        payload = root/'previous'
        native = payload/'dist/host/bin/win64'
        native.mkdir(parents=True)
        (native/'driver_alvr_server.dll').write_bytes(b'old vrostbridge-native-1 21.0.0-dev14')
        (native/'avcodec-62.dll').write_bytes(b'ffmpeg')
        (payload/'dist/host/vrostbridge-build.json').write_text('{"source_patch_sha256": {}}')
        (payload/'HOST_PROVENANCE.txt').write_text('previous\n')
        (payload/'docs').mkdir()
        (payload/'PACKAGED.json').write_text(json.dumps({'version': '1.0.0-rc1', 'role': 'host', 'alvr_commit': repack.PIN}))
        repack.manifest(payload)
        return payload

    @staticmethod
    def launcher(root):
        """A stand-in for the 1.0.0 launcher: a 64-bit PE header and the VROSTBridge version resource text."""
        path = root/'VROSTBridge Host.exe'
        header = bytearray(0x40)
        header[:2] = b'MZ'
        header[0x3c:0x40] = (0x40).to_bytes(4, 'little')
        path.write_bytes(bytes(header) + b'PE\0\0' + (0x8664).to_bytes(2, 'little') + 'VROSTBridge Host'.encode('utf-16-le'))
        return path

    @staticmethod
    def overlay(root, patches, repack):
        overlay = root/'overlay'
        (overlay/'bin/win64').mkdir(parents=True)
        driver = overlay/'bin/win64/driver_alvr_server.dll'
        driver.write_bytes(b'new vrostbridge-native-1 21.0.0-dev14 VROST_VSYNC_LOCK')
        (overlay/'vrostbridge-build.json').write_text(json.dumps({
            'alvr_commit': repack.PIN, 'native_protocol': 'vrostbridge-native-1', 'api_version': '21.0.0-dev14',
            'compiler': 'test', 'source_patch_sha256': patches,
            'files': [{'name': 'bin/win64/driver_alvr_server.dll', 'sha256': hashlib.sha256(driver.read_bytes()).hexdigest()}]}))
        return overlay

    def test_native_overlay_replaces_driver_and_records_provenance(self):
        repack = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = self.previous_payload(root, repack)
            current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'patches').glob('*.patch'))}
            stale = {name: digest for name, digest in current.items() if not name.startswith('0007-')}
            with self.assertRaisesRegex(RuntimeError, 'complete current patch set'):
                repack.repack(previous, root/'stale', self.overlay(root, stale, repack), launcher=self.launcher(root))
            shutil.rmtree(root/'overlay')
            repack.repack(previous, root/'out', self.overlay(root, current, repack), launcher=self.launcher(root))
            host = root/'out/dist/host'
            self.assertIn(b'VROST_VSYNC_LOCK', (host/'bin/win64/driver_alvr_server.dll').read_bytes())
            self.assertEqual(json.loads((host/'vrostbridge-build.json').read_text())['source_patch_sha256'], current)
            self.assertEqual((host/'bin/win64/avcodec-62.dll').read_bytes(), b'ffmpeg')
            self.assertIn('native driver rebuilt', (root/'out/HOST_PROVENANCE.txt').read_text())
            repack.verify_input(root/'out')


class TimingLockPatchTests(unittest.TestCase):
    PATCH = ROOT/'patches/0007-host-frame-timing-lock.patch'

    def module_source(self):
        text = self.PATCH.read_text()
        section = text[text.index('+++ b/alvr/server_core/src/vrostbridge_vsync.rs'):]
        section = section[section.index('@@'):]
        lines = []
        for line in section.splitlines()[1:]:
            if line.startswith('diff --git'):
                break
            if line.startswith('+'):
                lines.append(line[1:])
        return '\n'.join(lines) + '\n'

    def test_patch_wires_lock_into_statistics_and_connection(self):
        text = self.PATCH.read_text()
        for marker in ('+mod vrostbridge_vsync;', 'self.vsync_lock.observe(client_stats.rendering)',
                       'self.vsync_lock.take_shift_ns()', 'statistics.configure_vsync_lock(lock_enabled, lock_margin_ms);',
                       'crate::vrostbridge_vsync::CONFIG_FILE'):
            self.assertIn(marker, text)
        self.assertIn('pub const CONFIG_FILE: &str = "vrostbridge-timing.json";', self.module_source())
        self.assertIn("'vrostbridge-timing.json'", (ROOT/'launcher/app.py').read_text())

    @unittest.skipUnless(shutil.which('rustc'), 'rustc not installed')
    def test_controller_unit_and_closed_loop_tests_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'vrostbridge_vsync.rs'
            source.write_text(self.module_source())
            binary = Path(directory)/'vsync_tests'
            build = subprocess.run(['rustc', '--edition', '2024', '--test', '-O', str(source), '-o', str(binary)],
                                   capture_output=True, text=True, cwd=directory,
                                   env={'PATH': '/usr/bin:/bin:' + str(Path.home()/'.cargo/bin'), 'RUSTUP_TOOLCHAIN': 'stable',
                                        'HOME': str(Path.home())})
            if build.returncode and 'toolchain' in build.stderr and 'not installed' in build.stderr:
                self.skipTest('Rust toolchain unavailable')
            self.assertEqual(build.returncode, 0, build.stderr[-2000:])
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:])
            self.assertIn('test result: ok. 9 passed', run.stdout)


class NativeReconfigurationContracts(unittest.TestCase):
    def test_decoder_and_dimensions_can_change_in_session(self):
        main = (ROOT/'client/platform/linux/main.cpp').read_text()
        vulkan = (ROOT/'client/platform/linux/vulkan.cpp').read_text()
        self.assertNotIn('if (streaming && !configured)', main)
        self.assertIn('config == activeConfig', main)
        self.assertIn('restarting the decoder in-session', main)
        self.assertIn('Stream resumed: first new frame', main)
        self.assertNotIn('restart client to renegotiate', vulkan)
        # The output follows the negotiated eye size (foveated streams decode smaller).
        self.assertIn('pruneOtherSizes(outWidth, outHeight, false, false);', vulkan)
        self.assertIn('spareDescriptors_', vulkan)


class ParallelStartTests(SessionLifecycleTests):
    def run_start(self, app, events, block_ensure_until_host=True):
        host_started = threading.Event()
        def start(settings, **kwargs):
            events.append(('host.start', settings['headset']['model']))
            host_started.set()
        def ensure(*args, **kwargs):
            if block_ensure_until_host:
                self.assertTrue(host_started.wait(2), 'host did not start in parallel with local SteamVR')
            events.append('runtime.ensure')
        app.session_link.start.side_effect = start
        app.session_link.prepare_network.side_effect = lambda **kw: events.append('network.prepare') or {'VROSTBRIDGE_BIND_IP': '192.168.1.8'}
        app.client_runtime.ensure.side_effect = ensure
        app.launch = Mock(side_effect=lambda *a: events.append('native.launch'))
        app.action({'action': 'start', 'codec': 'Hevc', 'bitrate': 120, 'resolution': 'compatibility'})

    def test_known_headset_starts_both_pcs_together(self):
        app = self.app()
        app.settings['cached_headset'] = dict(HEADSET)
        events = []
        self.run_start(app, events)
        self.assertEqual(events, ['network.prepare', ('host.start', 'Index'), 'runtime.ensure', 'native.launch'])
        app.session_link.stop.assert_not_called()

    def test_changed_headset_restarts_host_with_fresh_profile(self):
        app = self.app()
        cached = dict(HEADSET, model='Older headset', width=1600, height=1600)
        app.settings['cached_headset'] = cached
        events = []
        self.run_start(app, events)
        self.assertEqual(events, ['network.prepare', ('host.start', 'Older headset'), 'runtime.ensure',
                                  ('host.start', 'Index'), 'native.launch'])
        app.session_link.stop.assert_called_once_with()
        self.assertEqual(app.settings['cached_headset'], HEADSET)

    def test_first_session_saves_headset_for_next_time(self):
        app = self.app()
        events = []
        self.run_start(app, events, block_ensure_until_host=False)
        self.assertEqual(events, ['runtime.ensure', 'network.prepare', ('host.start', 'Index'), 'native.launch'])
        self.assertEqual(json.loads(app.settings_path.read_text())['cached_headset'], HEADSET)
        self.assertEqual(self.app().settings['cached_headset'], HEADSET)

    def test_local_failure_cancels_early_host_start(self):
        app = self.app()
        app.settings['cached_headset'] = dict(HEADSET)
        released = threading.Event()
        def discover(*args, **kwargs):
            # Host discovery is slow; local SteamVR fails meanwhile.
            self.assertTrue(kwargs['cancel'].wait(2))
            released.set()
            raise ValueError('Session start cancelled.')
        app.session_link.discover.side_effect = discover
        app.client_runtime.ensure.side_effect = ValueError('Headset display not ready')
        app.launch = Mock()
        with self.assertRaisesRegex(ValueError, 'Headset display not ready'):
            app.action({'action': 'start'})
        self.assertTrue(released.is_set())
        app.session_link.start.assert_not_called()
        app.launch.assert_not_called()

    def test_client_settings_persist_and_validate(self):
        app = self.app()
        app.launch = Mock()
        app.action({'action': 'start', 'encoder': 'balanced', 'timing_lock': False})
        reloaded = self.app()
        self.assertEqual((reloaded.settings['encoder'], reloaded.settings['timing_lock']), ('balanced', False))
        for bad in ({'encoder': 'max'}, {'timing_lock': 'no'}):
            with self.assertRaises(ValueError):
                self.app().action(dict({'action': 'start'}, **bad))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (PipelineStatsTests, HostEventsTests, SessionSettingsTests, HostProfileTests, HostAddonTests,
                 TimingLockFlagTests, HostRepackTests, TimingLockPatchTests, NativeReconfigurationContracts):
        suite.addTests(loader.loadTestsFromTestCase(case))
    for case in (CapabilityRoundTripTests, ParallelStartTests):
        suite.addTests(case(name) for name in case.__dict__ if name.startswith('test_'))
    return suite
