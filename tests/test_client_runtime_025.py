"""Lifecycle ownership and reversible System-button runtime settings."""
import os
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from headset_fixture import HEADSET_OUTPUT
from unittest.mock import Mock, patch

from launcher.client_runtime import (ClientRuntime, desktop_environment, process_identity,
                                     runtime_processes, signal_identity, steam_environment)


class ClientRuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings_path=self.root/'steamvr.vrsettings'
        settings=patch('launcher.client_runtime.settings_path',return_value=self.settings_path);settings.start();self.addCleanup(settings.stop)
        self.log = Mock()
        self.runtime = ClientRuntime(self.root/'VROSTBridgeClient', self.root, self.log)
        self.identity = {'pid': 4102, 'path': str(self.root/'SteamVR/bin/linux64/vrserver'),
                         'uid': os.getuid(), 'started': 8300}

    def test_legacy_full_mode_is_migrated_to_stream_only(self):
        self.runtime.helper = Mock(return_value=subprocess.CompletedProcess([], 0, HEADSET_OUTPUT, ''))
        with patch('launcher.client_runtime.runtime_processes', return_value={}):
            self.runtime.ensure(lightweight=False)
        self.runtime.helper.assert_called_once_with(timeout=8)
        data=json.loads(self.settings_path.read_text());self.assertFalse(data['steamvr']['enableHomeApp']);self.assertFalse(data['dashboard']['enableDashboard']);self.assertTrue(data['steamvr']['sendSystemButtonToAllApps'])

    def test_existing_runtime_restarts_before_offline_preparation(self):
        events=[];self.runtime.helper=Mock(return_value=subprocess.CompletedProcess([],0,HEADSET_OUTPUT,''))
        def stop():events.append('stop');self.runtime.identities={}
        self.runtime._stop_runtime=stop
        self.runtime.prepare_settings=lambda:events.append('prepare')
        with patch('launcher.client_runtime.runtime_processes',side_effect=[{4102:self.identity},{},{}]):self.runtime.ensure()
        self.assertEqual(events,['stop','prepare'])

    def test_stop_restores_only_after_runtime_quit(self):
        actions = []
        self.runtime.session_active = True
        self.runtime.identities = {4102:self.identity}
        self.runtime.restore = lambda: actions.append('restore') or True
        self.runtime.request_quit = lambda: actions.append('quit') or True
        self.runtime.wait_stopped = Mock(return_value=True)
        with patch('launcher.client_runtime.process_identity', return_value=self.identity), \
             patch('launcher.client_runtime.signal_identity') as send:
            self.runtime.stop()
        self.assertEqual(actions, ['quit', 'restore'])
        self.runtime.wait_stopped.assert_called_once_with(8)
        send.assert_not_called()
        self.assertFalse(self.runtime.session_active)

    def test_stop_without_session_does_not_close_existing_runtime(self):
        self.runtime.restore = Mock(return_value=True)
        with patch('launcher.client_runtime.runtime_processes', return_value={4102:self.identity}), \
             patch('launcher.client_runtime.signal_identity') as send:
            self.runtime.stop()
        send.assert_not_called()

    def test_stop_escalates_only_captured_identity(self):
        self.runtime.session_active = True
        self.runtime.identities = {4102:self.identity}
        self.runtime.restore = Mock(return_value=True)
        self.runtime.request_quit = Mock(return_value=True)
        self.runtime.wait_stopped = Mock(side_effect=[False,False,True])
        with patch('launcher.client_runtime.process_identity', return_value=self.identity), \
             patch('launcher.client_runtime.signal_identity') as send:
            self.runtime.stop()
        self.assertEqual([call.args for call in send.call_args_list],
                         [(self.identity,signal.SIGTERM),(self.identity,signal.SIGKILL)])
        self.assertEqual([call.args[0] for call in self.runtime.wait_stopped.call_args_list], [8,5,3])

    def test_reused_pid_never_receives_shutdown(self):
        self.runtime.session_active = True
        self.runtime.identities = {4102:self.identity}
        changed = dict(self.identity,started=8400)
        with patch('launcher.client_runtime.process_identity', return_value=changed), \
             patch('launcher.client_runtime.signal_identity') as send, \
             patch.object(self.runtime,'request_quit') as request:
            self.runtime.stop()
        send.assert_not_called()
        request.assert_not_called()

    def test_failed_preference_restore_retains_receipt_and_still_stops(self):
        self.runtime.receipt.write_text('VROSTBridgeRuntime2 1 1 0 1\n')
        self.runtime.session_active = True
        self.runtime.helper = Mock(return_value=subprocess.CompletedProcess([], 2, 'runtime unavailable', ''))
        with patch('launcher.client_runtime.atomic_json',side_effect=OSError('disk full')):self.runtime.stop()
        self.assertTrue(self.runtime.receipt.exists())
        self.assertIn('next start', self.runtime.message)

    def test_quit_uses_registered_wrapper_and_clean_environment(self):
        wrapper = self.root/'SteamVR/bin/vrmonitor.sh'
        wrapper.parent.mkdir(parents=True)
        wrapper.touch()
        self.runtime.identities = {4102:self.identity}
        with patch('launcher.client_runtime.process_identity', return_value=self.identity), \
             patch('launcher.client_runtime.subprocess.Popen') as launch, \
             patch.dict(os.environ, {'LD_LIBRARY_PATH':'/frozen/private','LD_LIBRARY_PATH_ORIG':'/system/lib'}):
            self.assertTrue(self.runtime.request_quit())
        self.assertEqual(launch.call_args.args[0], [str(wrapper),'vrmonitor://quit'])
        self.assertEqual(launch.call_args.kwargs['env']['LD_LIBRARY_PATH'],'/system/lib')


class ProcessIdentityTests(unittest.TestCase):
    def test_self_identity_has_real_uid_and_start_ticks(self):
        identity = process_identity(os.getpid())
        if identity is None:self.skipTest('Execution container does not expose the matching UID/executable in procfs')
        self.assertEqual(identity['uid'],os.getuid())
        self.assertGreater(identity['started'],0)
        self.assertEqual(identity['path'],str(Path('/proc/self/exe').resolve()))

    def test_only_exact_registered_runtime_executable_is_managed(self):
        root = Path('/opt/steam/SteamVR')
        good = {'pid':1,'path':str(root/'bin/linux64/vrserver'),'uid':os.getuid(),'started':12}
        wrong = dict(good,pid=2,path='/somewhere/vrserver')
        with patch('launcher.client_runtime.runtime_roots',return_value=[root]), \
             patch('launcher.client_runtime.Path.iterdir',return_value=[Path('/proc/1'),Path('/proc/2')]), \
             patch('launcher.client_runtime.process_identity',side_effect=lambda pid:good if pid==1 else wrong):
            self.assertEqual(runtime_processes(),{1:good})

    def test_pidfd_is_opened_then_identity_verified_before_signal(self):
        identity = {'pid':42,'path':'/runtime/vrserver','uid':os.getuid(),'started':100}
        order = []
        with patch('launcher.client_runtime.os.pidfd_open',side_effect=lambda pid:order.append('open') or 9), \
             patch('launcher.client_runtime.process_identity',side_effect=lambda pid:order.append('verify') or identity), \
             patch('launcher.client_runtime.signal.pidfd_send_signal',side_effect=lambda fd,sig:order.append('signal')) as send, \
             patch('launcher.client_runtime.os.close') as close:
            self.assertTrue(signal_identity(identity,signal.SIGTERM))
        self.assertEqual(order,['open','verify','signal'])
        send.assert_called_once_with(9,signal.SIGTERM)
        close.assert_called_once_with(9)

    def test_pidfd_identity_mismatch_is_closed_without_signal(self):
        identity = {'pid':42,'path':'/runtime/vrserver','uid':os.getuid(),'started':100}
        with patch('launcher.client_runtime.os.pidfd_open',return_value=9), \
             patch('launcher.client_runtime.process_identity',return_value=dict(identity,started=101)), \
             patch('launcher.client_runtime.signal.pidfd_send_signal') as send, \
             patch('launcher.client_runtime.os.close') as close:
            self.assertFalse(signal_identity(identity,signal.SIGTERM))
        send.assert_not_called()
        close.assert_called_once_with(9)

    def test_game_mode_detected_without_claiming_display_support(self):
        with patch.dict(os.environ,{'XDG_CURRENT_DESKTOP':'gamescope','XDG_SESSION_TYPE':'wayland'},clear=True):
            status = desktop_environment()
        self.assertTrue(status['game_mode_detected'])
        self.assertEqual(status['game_mode_vr_support'],'requires-headset-display-validation')

    def test_steam_does_not_inherit_frozen_private_loader_path(self):
        with patch.dict(os.environ,{'LD_LIBRARY_PATH':'/frozen/private'},clear=True):
            self.assertNotIn('LD_LIBRARY_PATH',steam_environment())


if __name__ == '__main__':
    unittest.main()
