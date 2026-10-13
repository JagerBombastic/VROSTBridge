"""RC2 regression checks: versions, prediction setting, Wi-Fi session power
saving, adapter-scoped host gate policy, cadence journal capture, DSCP and
native source contracts that have no separate unit seam."""
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_session_lifecycle import SessionLifecycleTests

ROOT = Path(__file__).resolve().parents[1]


class VersionConsistency(unittest.TestCase):
    def test_every_version_copy_matches(self):
        from launcher.version import CLIENT_VERSION, HOST_VERSION
        header = (ROOT/'core/version.hpp').read_text()
        self.assertIn('VROSTBRIDGE_CLIENT_VERSION "%s"' % CLIENT_VERSION, header)
        self.assertIn('VROSTBRIDGE_HOST_VERSION "%s"' % HOST_VERSION, header)
        self.assertIn('set(VROSTBRIDGE_VERSION "%s")' % CLIENT_VERSION, (ROOT/'CMakeLists.txt').read_text())
        self.assertIn('version=%s\n' % CLIENT_VERSION, (ROOT/'packaging/install-bazzite.sh').read_text())
        build = (ROOT/'packaging/build_distributions.py').read_text()
        self.assertIn("VERSION='%s'" % HOST_VERSION, build)
        self.assertIn("CLIENT_VERSION='%s'" % CLIENT_VERSION, build)
        nsi = (ROOT/'packaging/windows-installer.nsi').read_text()
        # Every version-like string in the installer is the host version (four parts from 1.0.0.1).
        self.assertEqual(set(re.findall(r'\d+\.\d+\.\d+(?:\.\d+)?(?:-rc[0-9.]*[0-9])?', nsi)), {HOST_VERSION})
        self.assertIn('InstallDir "$LOCALAPPDATA\\VROSTBridge\\Host\\%s"' % HOST_VERSION, nsi)
        self.assertIn('"DisplayVersion" "%s"' % HOST_VERSION, nsi)
        # 1.0.0: the launcher's version resource and its log banner carry the host version too.
        parts = re.match(r'\d+(?:\.\d+){2,3}', HOST_VERSION).group().split('.')
        numeric = ','.join(parts + ['0'] * (4 - len(parts)))
        self.assertIn('VIProductVersion "%s"' % '.'.join(numeric.split(',')), nsi)
        resource = (ROOT/'packaging/windows-icon.rc').read_text()
        for line in ('FILEVERSION ' + numeric, 'PRODUCTVERSION ' + numeric, 'VALUE "FileVersion", "%s"' % HOST_VERSION,
                     'VALUE "ProductVersion", "%s"' % HOST_VERSION):
            self.assertIn(line, resource)
        self.assertIn('native launcher %s ---' % HOST_VERSION, (ROOT/'packaging/windows-launcher.c').read_text())
        self.assertIn("VERSION = '%s'" % HOST_VERSION, (ROOT/'launcher/install_maintenance.py').read_text())
        self.assertIn('VROSTBridge %s / ALVR' % HOST_VERSION, (ROOT/'host/profile.py').read_text())
        # No stale hard-coded labels remain in runtime code.
        for path in list((ROOT/'launcher').glob('*.py')) + list((ROOT/'launcher').glob('*.html')) + \
                list((ROOT/'client').rglob('*.cpp')) + list((ROOT/'client').rglob('*.hpp')):
            self.assertNotRegex(path.read_text(errors='ignore'), r'''['"`/ ]1\.0\.0-rc1(?:\.3)?['"`< ]''', str(path))


class WifiSessionTests(unittest.TestCase):
    def make(self, responses, wireless=True):
        from launcher.wifi_session import WifiSession
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        net = Path(directory.name)
        (net/'wlan0').mkdir(); (net/'eth0').mkdir()
        if wireless: (net/'wlan0'/'wireless').mkdir()
        calls = []
        def runner(arguments, timeout):
            calls.append(list(arguments))
            for match, result in responses:
                if all(part in arguments for part in match):
                    value = result.pop(0) if isinstance(result, list) else result
                    return SimpleNamespace(returncode=value[0], stdout=value[1], stderr=value[2] if len(value) > 2 else '')
            return SimpleNamespace(returncode=1, stdout='', stderr='unexpected')
        session = WifiSession(runner=runner, log=lambda line: None, sys_class_net=net,
                              nmcli='/usr/bin/nmcli', iw='/usr/sbin/iw', ip='/usr/sbin/ip')
        return session, calls

    def test_wired_adapter_is_untouched(self):
        session, calls = self.make([])
        self.assertEqual(session.begin('eth0')['state'], 'wired')
        self.assertEqual(calls, [])

    def test_power_save_off_already(self):
        session, calls = self.make([(('get', 'power_save'), (0, 'Power save: off\n'))])
        self.assertEqual(session.begin('wlan0')['state'], 'off')
        self.assertFalse(any('modify' in call for call in calls))

    def test_disabled_for_session_then_restored_to_profile_value(self):
        session, calls = self.make([
            (('get', 'power_save'), [(0, 'Power save: on\n'), (0, 'Power save: off\n')]),
            (('GENERAL.CONNECTION',), (0, 'Home WiFi\n')),
            (('802-11-wireless.powersave', 'connection'), (0, '0 (default)\n')),
            (('device', 'modify'), (0, '')),
        ])
        status = session.begin('wlan0')
        self.assertEqual(status['state'], 'disabled_for_session')
        modify = [call for call in calls if 'modify' in call]
        self.assertEqual(modify[0][-2:], ['802-11-wireless.powersave', '2'])
        self.assertNotIn('connection', modify[0][1:3])  # runtime device change, never the profile
        session.end()
        modify = [call for call in calls if 'modify' in call]
        self.assertEqual(modify[-1][-2:], ['802-11-wireless.powersave', '0'])
        self.assertEqual(session.status()['state'], 'restored')
        session.end()  # idempotent
        self.assertEqual(len([call for call in calls if 'modify' in call]), 2)

    def test_refused_change_is_reported_and_never_restored(self):
        session, calls = self.make([
            (('get', 'power_save'), (0, 'Power save: on\n')),
            (('GENERAL.CONNECTION',), (0, 'Home\n')),
            (('802-11-wireless.powersave', 'connection'), (0, 'enable\n')),
            (('device', 'modify'), (1, '', "Error: Can't reapply changes")),
        ])
        status = session.begin('wlan0')
        self.assertEqual(status['state'], 'on')
        self.assertIn('Turn off Wi-Fi power saving', status['message'])
        self.assertTrue(status['permanent_fix'])
        session.end()
        self.assertEqual(len([call for call in calls if 'modify' in call]), 1)

    def test_route_lookup_and_interface_validation(self):
        session, calls = self.make([(('route', 'get'), (0, json.dumps([{'dev': 'eth0'}])))])
        self.assertEqual(session.begin('', '192.168.1.10', '192.168.1.20')['state'], 'wired')
        self.assertIn(['/usr/sbin/ip', '-j', 'route', 'get', '192.168.1.10', 'from', '192.168.1.20'], calls)
        self.assertEqual(session.begin('wlan0; rm -rf /')['state'], 'unknown')
        self.assertEqual(session.begin('', 'not-an-ip')['state'], 'unknown')


class HostPolicyTests(unittest.TestCase):
    def test_policy_receives_local_address_when_it_accepts_one(self):
        from launcher.session_link import SessionHost
        seen = []
        self.assertTrue(SessionHost._policy(lambda: True, '192.168.1.5'))
        self.assertTrue(SessionHost._policy(lambda local: seen.append(local) or True, '192.168.1.5'))
        self.assertEqual(seen, ['192.168.1.5'])
        self.assertTrue(SessionHost._policy(lambda local=None: seen.append(local) or True, None))
        self.assertEqual(seen, ['192.168.1.5', None])  # no address: called without an argument

    def test_dscp_marking_enabled(self):
        from host.profile import configure
        document = json.loads((ROOT/'host/default-session.json').read_text())
        dscp = configure(document)['session_settings']['connection']['dscp']
        self.assertTrue(dscp['set']); self.assertEqual(dscp['content']['variant'], 'ExpeditedForwarding')


class CadenceJournalTests(unittest.TestCase):
    def test_degraded_cadence_opens_an_incident_and_recovery_does_not(self):
        from launcher.diagnostics import Journal
        reason = Journal._incident_reason
        self.assertEqual(reason('log', 'PRESENTATION_CADENCE {"from":"full","to":"client_throttled"}'), 'presentation-cadence')
        self.assertIsNone(reason('log', 'PRESENTATION_CADENCE {"from":"client_throttled","to":"full"}'))
        self.assertIsNone(reason('log', 'PRESENTATION_CADENCE {"from":"full","to":"idle"}'))


class NativeSourceContracts(unittest.TestCase):
    """Guards for loop-level changes that have no separate unit seam."""
    def test_controls_follow_session_not_head_pose_or_compositor(self):
        device = (ROOT/'client/device/openvr_device.cpp').read_text()
        self.assertIn('focused_ = system->IsInputAvailable();', device)
        self.assertNotIn('good && system->IsInputAvailable()', device)
        self.assertIn('const bool active = online && hmdConnected;', device)
        self.assertNotIn('online && tracked_', device)
        # Freshness/age use acquisition time, ALVR receives the target timestamp.
        self.assertIn('gaze_.sample(sampleNow)', device)
        self.assertIn('vrost_tracking_native(t, timestamp,', device)
        self.assertIn('d.native.setTransportTime(prediction_ ? timestamp : boneNow)', device)

    def test_presentation_uses_acquisition_time_and_never_blocks_on_stdout(self):
        main = (ROOT/'client/platform/linux/main.cpp').read_text()
        self.assertIn('displayed ? displayed->sampledAt : 0', main)
        self.assertIn('--no-prediction', main)
        loop = main[main.index('while (!quit && !interrupted)'):main.index('presentationTrace.finish()')]
        self.assertNotIn('std::cout', loop)
        self.assertNotIn('std::cerr', loop)


class PredictionSettingTests(SessionLifecycleTests):
    def test_prediction_defaults_on_persists_and_reaches_native(self):
        app = self.app(); app.launch = Mock()
        self.assertTrue(app.settings['motion_prediction'])
        app.action({'action': 'start', 'motion_prediction': False})
        self.assertIn('--no-prediction', app.launch.call_args.args[0])
        self.assertFalse(self.app().settings['motion_prediction'])

    def test_status_reports_wifi_session_for_the_ui(self):
        app = self.app()
        self.assertEqual(app.status()['wifi']['state'], 'idle')
        self.assertIn('wifi', app.local_report())

    def test_prediction_setting_rejects_non_boolean(self):
        app = self.app()
        with self.assertRaisesRegex(ValueError, 'motion_prediction'):
            app.action({'action': 'start', 'motion_prediction': 'off'})


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (VersionConsistency, WifiSessionTests, HostPolicyTests, CadenceJournalTests, NativeSourceContracts):
        suite.addTests(loader.loadTestsFromTestCase(case))
    suite.addTests(PredictionSettingTests(name) for name in PredictionSettingTests.__dict__ if name.startswith('test_'))
    return suite
