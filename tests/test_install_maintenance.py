"""Upgrade ownership, profile retention and rollback boundaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch
from launcher import install_maintenance as m


class InstallMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='VROSTBridge upgrade Ü ')
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            'XDG_DATA_HOME': str(self.base/'data files'),
            'XDG_STATE_HOME': str(self.base/'state files'),
            'LOCALAPPDATA': str(self.base/'app data')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def release(self, version, role):
        path = m.releases_directory(role)/version
        path.mkdir(parents=True)
        identity_version = re.sub(r'-[0-9a-f]{12}$', '', version) if role == 'client' else version
        (path/'PACKAGED.json').write_text(json.dumps({'version': identity_version, 'role': role, 'alvr_commit': m.PIN}))
        if role == 'host':
            (path/'dist/host').mkdir(parents=True)
        return path

    def session(self, release, hostname):
        value = {'client_connections': {hostname: {'trusted': True}}, 'session_settings': {'bitrate': 40}}
        (release/'dist/host/session.json').write_text(json.dumps(value))
        return value

    def staged_host(self, version):
        staged = self.base/'stage'
        (staged/'dist/host').mkdir(parents=True)
        (staged/'PACKAGED.json').write_text(json.dumps({'version': version, 'role': 'host', 'alvr_commit': m.PIN}))
        (staged/'dist/host/vrostbridge-build.json').write_text(json.dumps({'api_version': '21.0.0-dev14'}))
        (staged/'dist/host/session.json').write_text(json.dumps({'client_connections': {}, 'server_version': '21.0.0-dev14'}))
        manifest = ''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(staged))+'\n'
                           for p in sorted(staged.rglob('*')) if p.is_file())
        (staged/'SHA256SUMS').write_text(manifest)
        return staged

    def test_release_candidate_identity_and_historical_layouts(self):
        for role, name in [('host', '0.2.14.1'), ('host', '1.0.0-rc1'),
                           ('host', '1.0.0-rc.1'), ('host', '1.0.0'),
                           ('client', '0.2.14'), ('client', '0.2.14-111111111111'),
                           ('client', '1.0.0-rc1-222222222222')]:
            with self.subTest(role=role, name=name):
                self.assertTrue(m.owned_release(self.release(name, role), role))

    def test_ownership_rejects_malformed_or_mismatched_release_identity(self):
        valid = self.release('1.0.0-rc1', 'host')
        document = json.loads((valid/'PACKAGED.json').read_text())
        for version in ['', None, 1, '1.0', '1.0.0.0.0', '01.0.0',
                        '1.0.0-01', '1.0.0-rc..1', '1.0.0/rc1', '../1.0.0-rc1',
                        '1.0.0', '1.0.0-rc2']:
            with self.subTest(version=version):
                (valid/'PACKAGED.json').write_text(json.dumps(dict(document, version=version)))
                self.assertFalse(m.owned_release(valid, 'host'))
        (valid/'PACKAGED.json').write_text(json.dumps(document))
        sibling = valid.parent/'1.0.0-rc1-111111111111'
        sibling.mkdir(); (sibling/'PACKAGED.json').write_text(json.dumps(document))
        self.assertFalse(m.owned_release(sibling, 'host'))
        self.assertFalse(m.owned_release(valid, 'client'))
        self.assertFalse(m.owned_release(valid, 'other'))
        outside = self.base/'1.0.0-rc1'
        outside.mkdir(); (outside/'PACKAGED.json').write_text(json.dumps(document))
        self.assertFalse(m.owned_release(outside, 'host'))
        valid.rename(valid.with_name('moved'))
        valid.symlink_to(outside, target_is_directory=True)
        self.assertFalse(m.owned_release(valid, 'host'))

    def test_ownership_rejects_symlink_marker_and_wrong_client_suffix(self):
        release = self.release('1.0.0-rc1-222222222222', 'client')
        marker = release/'PACKAGED.json'
        external = self.base/'external-marker.json'
        external.write_bytes(marker.read_bytes()); marker.unlink(); marker.symlink_to(external)
        self.assertFalse(m.owned_release(release, 'client'))
        marker.unlink(); marker.write_bytes(external.read_bytes())
        self.assertTrue(m.owned_release(release, 'client'))
        for name in ['1.0.0-rc1-33333333333', '1.0.0-rc1-4444444444444',
                     '1.0.0-rc1-not-a-hash', '1.0.0-rc2-555555555555']:
            sibling = release.parent/name
            sibling.mkdir(); (sibling/'PACKAGED.json').write_bytes(marker.read_bytes())
            self.assertFalse(m.owned_release(sibling, 'client'))

    def test_rc_upgrade_migrates_hotfix_host_preserving_profile_and_native_api(self):
        from host.control import installed_api_version
        old = self.release('0.2.14.1', 'host')
        expected = self.session(old, 'known.client.local.')
        expected['server_version'] = '21.0.0-dev14+nightly.2026.09.18'
        (old/'dist/host/session.json').write_text(json.dumps(expected))
        target = m.releases_directory('host')/'1.0.0-rc1'
        staged = self.staged_host('1.0.0-rc1')
        drivers = [(old/'dist/host').resolve()]
        def migrate(actual, action):
            self.assertEqual(actual, target); self.assertEqual(action, 'Register')
            self.assertTrue(old.exists())
            self.assertEqual(json.loads((target/'dist/host/session.json').read_text()), expected)
            drivers[:] = [(target/'dist/host').resolve()]
        with patch.object(m, 'VERSION', '1.0.0-rc1'), patch.object(m, 'stop_desktop'), \
             patch.object(m, 'registered_drivers', side_effect=lambda: drivers.copy()), \
             patch.object(m, 'run_host_setup', side_effect=migrate), patch.object(m, 'delete_legacy_registry'):
            m.prepare_host(target); m.commit_host(target, staged); m.finish_host(target)
        self.assertFalse(old.exists())
        self.assertTrue(m.owned_release(target, 'host'))
        self.assertEqual(json.loads((target/'dist/host/session.json').read_text()), expected)
        self.assertEqual(installed_api_version(target/'dist/host'), '21.0.0-dev14')
        self.assertEqual(len(list((m.state_directory()/'upgrade-backups').glob('*/0.2.14.1/session.json'))), 1)

    def test_rc_client_upgrade_removes_old_version_and_preserves_settings(self):
        old = self.release('0.2.14-111111111111', 'client')
        target = self.release('1.0.0-rc1-222222222222', 'client')
        (target.parent.parent/'current').symlink_to(target)
        m.state_directory().mkdir(parents=True)
        settings = m.state_directory()/'client-settings.json'
        settings.write_text('{"width":2016,"microphone":false}')
        m.finish_client(target)
        self.assertFalse(old.exists()); self.assertTrue(target.exists())
        self.assertEqual(settings.read_text(), '{"width":2016,"microphone":false}')

    def test_rc_same_version_repair_preserves_profile_and_removes_repair_backup(self):
        target = self.release('1.0.0-rc1', 'host')
        expected = self.session(target, 'known.client.local.')
        (target/'obsolete.txt').write_text('old')
        staged = self.staged_host('1.0.0-rc1')
        with patch.object(m, 'VERSION', '1.0.0-rc1'), patch.object(m, 'stop_desktop'), \
             patch.object(m, 'registered_drivers', return_value=[(target/'dist/host').resolve()]), \
             patch.object(m, 'delete_legacy_registry'):
            m.prepare_host(target); m.commit_host(target, staged); m.finish_host(target)
        self.assertTrue(m.owned_release(target, 'host'))
        self.assertFalse((target/'obsolete.txt').exists())
        self.assertEqual(json.loads((target/'dist/host/session.json').read_text()), expected)
        self.assertEqual(list(target.parent.glob('.repair-*')), [])

    def test_client_cleanup_and_uninstall_keep_unowned_and_state(self):
        old = self.release('0.2.2-111111111111', 'client')
        new = self.release('0.2.3-222222222222', 'client')
        unowned = new.parent/'0.2.1-333333333333'
        unowned.mkdir(); (unowned/'important.txt').write_text('keep')
        outside = self.base/'unrelated'; outside.mkdir()
        symlink = new.parent/'0.2.0-444444444444'; symlink.symlink_to(outside)
        current = new.parent.parent/'current'; current.symlink_to(new)
        m.state_directory().mkdir(parents=True)
        state = m.state_directory()/'client-settings.json'; state.write_text('{"microphone":false}')
        m.finish_client(new)
        self.assertFalse(old.exists()); self.assertTrue(unowned.exists()); self.assertTrue(symlink.is_symlink())
        apps = new.parent.parent.parent/'applications'; apps.mkdir()
        (apps/'io.vrostbridge.Client.desktop').write_text('Exec="'+str(new.parent.parent)+'/current/VROSTBridge-Client"')
        (apps/'io.vrostbridge.GameMode.desktop').write_text('X-VROSTBridge-Managed=true\nExec="'+str(new.parent.parent)+'/current/VROSTBridge-Client" --role client --game-mode')
        m.uninstall_client(new)
        self.assertFalse(current.exists()); self.assertFalse(new.exists())
        self.assertFalse((apps/'io.vrostbridge.Client.desktop').exists())
        self.assertFalse((apps/'io.vrostbridge.GameMode.desktop').exists())
        self.assertTrue(state.exists()); self.assertTrue(unowned.exists()); self.assertTrue(outside.exists())

    def test_stale_client_uninstall_does_not_touch_new_menu(self):
        old = self.release('0.2.2-111111111111', 'client')
        new = self.release('0.2.3-222222222222', 'client')
        current = new.parent.parent/'current'; current.symlink_to(new)
        apps = new.parent.parent.parent/'applications'; apps.mkdir()
        game = apps/'io.vrostbridge.GameMode.desktop'
        game.write_text('X-VROSTBridge-Managed=true\nExec="'+str(new.parent.parent)+'/current/VROSTBridge-Client" --role client --game-mode')
        with patch.object(m, 'stop_desktop') as stop:
            m.uninstall_client(old)
        stop.assert_not_called(); self.assertEqual(current.resolve(), new.resolve()); self.assertTrue(new.exists())
        self.assertTrue(game.is_file())

    def test_client_uninstall_preserves_an_unowned_shortcut_of_the_same_name(self):
        new = self.release('0.2.5-222222222222', 'client')
        (new.parent.parent/'current').symlink_to(new)
        apps = new.parent.parent.parent/'applications'; apps.mkdir()
        game = apps/'io.vrostbridge.GameMode.desktop'
        game.write_text('Name=My own launcher\nExec=/another/application')
        m.uninstall_client(new)
        self.assertEqual(game.read_text(), 'Name=My own launcher\nExec=/another/application')

    def test_host_migrates_trust_before_old_deletion(self):
        old = self.release('0.2.2', 'host'); new = self.release('0.2.3', 'host')
        expected = self.session(old, 'known.client.local.')
        with patch.object(m, 'registered_drivers', return_value=[(old/'dist/host').resolve()]):
            m.preserve_host_state([old, new], new)
        drivers = [(old/'dist/host').resolve()]
        def migrate(target, action):
            self.assertTrue(old.exists()); self.assertEqual(action, 'Register')
            self.assertEqual(json.loads((new/'dist/host/session.json').read_text()), expected)
            drivers[:] = [(new/'dist/host').resolve()]
        with patch.object(m, 'registered_drivers', side_effect=lambda: drivers.copy()), patch.object(m, 'run_host_setup', side_effect=migrate), patch.object(m, 'delete_legacy_registry'):
            m.finish_host(new)
        self.assertFalse(old.exists()); self.assertTrue(new.exists())
        backups = list((m.state_directory()/'upgrade-backups').glob('*/0.2.2/session.json'))
        self.assertEqual(len(backups), 1); self.assertEqual(json.loads(backups[0].read_text()), expected)

    def test_hotfix_upgrade_preserves_nightly_profile_and_uses_new_build_identity(self):
        from host.control import installed_api_version
        old=self.release('0.2.14','host');new=self.release(m.VERSION,'host')
        self.assertTrue(m.owned_release(new,'host'))
        expected=self.session(old,'known.client.local.')
        expected['server_version']='21.0.0-dev14+nightly.2026.09.18'
        (old/'dist/host/session.json').write_text(json.dumps(expected))
        (new/'dist/host/vrostbridge-build.json').write_text(json.dumps({'api_version':'21.0.0-dev14'}))
        with patch.object(m,'registered_drivers',return_value=[]),patch.object(m,'delete_legacy_registry'):
            m.preserve_host_state([old,new],new)
            m.finish_host(new)
        self.assertFalse(old.exists())
        self.assertEqual(json.loads((new/'dist/host/session.json').read_text()),expected)
        self.assertEqual(installed_api_version(new/'dist/host'),'21.0.0-dev14')

    def test_failed_driver_migration_retains_old_files(self):
        old = self.release('0.2.2', 'host'); new = self.release('0.2.3', 'host')
        self.session(old, 'trusted.client.local.')
        with patch.object(m, 'registered_drivers', return_value=[(old/'dist/host').resolve()]):
            m.preserve_host_state([old, new], new)
            with patch.object(m, 'run_host_setup', side_effect=RuntimeError('SteamVR running')):
                with self.assertRaises(RuntimeError): m.finish_host(new)
        self.assertTrue(old.exists()); self.assertTrue((m.state_directory()/'host-upgrade.json').exists())

    def test_repair_copy_failure_restores_same_version(self):
        target = self.release(m.VERSION, 'host'); self.session(target, 'trusted.client.local.')
        staged = self.base/'stage'; staged.mkdir()
        (staged/'PACKAGED.json').write_bytes((target/'PACKAGED.json').read_bytes())
        digest = hashlib.sha256((staged/'PACKAGED.json').read_bytes()).hexdigest()
        (staged/'SHA256SUMS').write_text(digest+'  PACKAGED.json\n')
        with patch.object(m, 'registered_drivers', return_value=[]): m.preserve_host_state([target], target)
        with patch.object(m.shutil, 'copytree', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError): m.commit_host(target, staged)
        self.assertTrue((target/'dist/host/session.json').is_file())
        self.assertEqual(list(target.parent.glob('.repair-*')), [])


    def test_firewall_uninstall_removes_only_owned_rules(self):
        from launcher.client_network import rich_rules
        state = Path(os.environ['XDG_STATE_HOME'])/'VROSTBridge/client-firewall.json'
        state.parent.mkdir(parents=True)
        owned = next(rule for rule in rich_rules('192.168.1.0/24') if 'port="9946" protocol="udp"' in rule)
        foreign = rich_rules('192.168.1.0/24')[1]
        state.write_text(json.dumps({'schema': 1, 'rules': [{'zone': 'public', 'scope': 'permanent', 'rule': owned}]}))
        rules = {owned, foreign}
        calls = []
        def run(args, timeout):
            calls.append(args)
            if args[0] == '/usr/bin/pkexec':
                self.assertIn('--permanent', args)
                self.assertIn('--remove-rich-rule='+owned, args)
                self.assertNotIn('--remove-rich-rule='+foreign, args)
                rules.remove(owned)
                return CompletedProcess(args, 0, '', '')
            return CompletedProcess(args, 0, '\n'.join(rules), '')
        with patch('launcher.client_network.system_run', side_effect=run):
            m.remove_client_firewall()
        self.assertFalse(state.exists()); self.assertEqual(rules, {foreign})

    def test_denied_firewall_cleanup_retains_manifest(self):
        from launcher.client_network import rich_rules
        state = Path(os.environ['XDG_STATE_HOME'])/'VROSTBridge/client-firewall.json'
        state.parent.mkdir(parents=True)
        rule = rich_rules('192.168.1.0/24')[0]
        state.write_text(json.dumps({'schema': 1, 'rules': [{'zone': 'public', 'scope': 'runtime', 'rule': rule}]}))
        def run(args, timeout):
            return CompletedProcess(args, 126, '', 'Denied') if args[0] == '/usr/bin/pkexec' else CompletedProcess(args, 0, rule, '')
        with patch('launcher.client_network.system_run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'approval'): m.remove_client_firewall()
        self.assertEqual(len(json.loads(state.read_text())['rules']), 1)


if __name__ == '__main__': unittest.main()
