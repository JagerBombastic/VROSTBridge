"""GUI session coordination; no real SteamVR, broker sockets or firewall changes."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from launcher.app import Application
from headset_fixture import HEADSET


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # RC4.5: eye tracking keeps Baballonia under XDG_DATA_HOME and reads XDG_CONFIG_HOME: never this
        # machine's own, and never its systemd user units (the real Baballonia may be running).
        env = patch.dict('os.environ', {'XDG_STATE_HOME':str(self.root), 'LOCALAPPDATA':str(self.root),
                                        'XDG_DATA_HOME':str(self.root/'data'), 'XDG_CONFIG_HOME':str(self.root/'config')})
        env.start(); self.addCleanup(env.stop)
        from launcher.baballonia import BaballoniaManager
        manager = patch('launcher.app.BaballoniaManager',
                        new=lambda **options: BaballoniaManager(systemd_run=None, systemctl=None, **options))
        manager.start(); self.addCleanup(manager.stop)
        # ...nor the eye-camera helper of this checkout (a running one would be taken for an orphan).
        helper = patch('launcher.app.Application.camera_executable', new=staticmethod(lambda: self.root/'no-camera-helper'))
        helper.start(); self.addCleanup(helper.stop)
        self.objects = {}
        for name in ('ClientIdentity','ClientNetwork','NetworkMonitor','ClientRuntime','DiagnosticServer',
                     'RemoteDiagnostics','HostEvents','SessionClient','SessionHost','HostControl',
                     # RC4 system helpers: tested in their own suites, never run against this machine here.
                     'SessionPower','AsyncReprojectionCheck','HeadsetAudio','WifiDiagnostics','HostWaker',
                     'NetworkTest','KeepAwake','SleepAfterStop','OtherWifiPause',
                     # 1.0.0.8: Bluetooth and steamos-manager are never touched from these tests either.
                     'BaseStations','SessionTuning'):
            mock = Mock(name=name)
            patcher = patch('launcher.app.'+name,return_value=mock)
            patcher.start(); self.addCleanup(patcher.stop)
            self.objects[name] = mock
        monitor = patch.object(Application,'monitor_session')
        monitor.start(); self.addCleanup(monitor.stop)
        self.objects['ClientRuntime'].state = 'idle'
        self.objects['ClientRuntime'].headset = dict(HEADSET)
        self.objects['HostControl'].active = False
        self.objects['HostControl'].owned_pids = set()
        self.objects['SessionClient'].snapshot.return_value = {'state':'waiting','active':False}
        self.objects['SessionClient'].prepare_network.return_value = {'VROSTBRIDGE_BIND_IP':'192.168.1.8'}
        self.objects['SessionHost'].snapshot.return_value = {'state':'waiting','active':False}
        self.apps = []
        self.addCleanup(self.close_apps)

    def close_apps(self):
        for app in self.apps:
            app.process = None
            # Tests intentionally make actions fail; cleanup should still close
            # the mocked companion handles and set their lifetime events.
            app.session_link.stop.side_effect = None
            if app.client_runtime:
                app.client_runtime.stop.side_effect = None
            app.shutdown()

    def test_optical_geometry_survives_log_rotation_and_reaches_saved_report(self):
        app=self.app()
        geometry={'schema':1,'revision':2,'convention':'openvr_raw_to_alvr_v2',
                  'eyes':[{'eye':'left','raw_lrtb':[-1.1,.9,-1.3,.7]},
                          {'eye':'right','raw_lrtb':[-.9,1.1,-1.2,.8]}]}
        app.log('VIEW_GEOMETRY '+json.dumps(geometry))
        # Reports after a game must retain the setup even after startup lines
        # have fallen out of the bounded scrolling log.
        for i in range(130):app.log('STATUS frame '+str(i))
        self.assertNotIn('VIEW_GEOMETRY',str(list(app.lines)))
        app.log('VIEW_GEOMETRY []')
        app.log('VIEW_GEOMETRY malformed')
        with patch('launcher.app.steamvr_logs',return_value={}):
            self.assertEqual(app.local_report()['view_geometry'],geometry)
        with patch('launcher.app.subprocess.Popen') as start,patch('launcher.app.threading.Thread'):
            start.return_value.poll.return_value=None
            app.launch(['fixture-client'])
        self.assertEqual(app.view_geometry,{})

    def app(self, role='client'):
        app = Application(role)
        self.apps.append(app)
        if role == 'client':
            app.client = self.root/'VROSTBridgeClient'
            app.client.touch()
            app.client_runtime.native_command.side_effect = lambda args:[str(app.client),*args]
        return app

    def test_client_start_reads_physical_headset_before_host_then_native(self):
        app = self.app()
        events = []
        app.session_link.start.side_effect = lambda *args,**kwargs:events.append('host.start')
        app.session_link.prepare_network.side_effect = lambda **kw:events.append('network.prepare') or {'VROSTBRIDGE_BIND_IP':'192.168.1.8'}
        app.client_runtime.ensure.side_effect = lambda *_,**kw:events.append('runtime.ensure')
        app.launch = Mock(side_effect=lambda *args:events.append('native.launch'))
        app.action({'action':'start','codec':'Hevc','bitrate':120,'resolution':'compatibility','host_ip':' 192.168.1.40 '})
        self.assertEqual(events,['runtime.ensure','network.prepare','host.start','native.launch'])
        # 1.0.0.13: on an Index the saved Custom size follows its mode: Native, 'sharp' on the wire.
        app.session_link.start.assert_called_once_with({'codec':'Hevc','bitrate':120,'microphone':False,'resolution':'sharp','headset':HEADSET,
                                                         'encoder':'speed','network':'unknown','timing_lock':True,
                                                     # RC4 fields; the session link strips them for older hosts.
                                                     'foveation':None,'ten_bit':True,'intra_refresh':False,'pacing_mbps':0,
                                                     'keep_warm_minutes':0,
                                                     # RC4.1: one prediction step, on the host (stripped for older hosts).
                                                     'host_prediction':True},host_ip='192.168.1.40',cancel=app.session_cancelled)
        app.client_runtime.ensure.assert_called_once_with(True,extra_env={})
        args, env = app.launch.call_args.args
        # RC4.3: the fused frame pass sharpens at the saved level (Medium by default).
        self.assertEqual(args,[str(app.client),'--sharpening','70'])
        self.assertEqual(env['VROSTBRIDGE_MICROPHONE'],'0')
        self.assertEqual(env['VROSTBRIDGE_BIND_IP'],'192.168.1.8')
        app.session_link.prepare_network.assert_called_once_with(host_ip='192.168.1.40',cancel=app.session_cancelled)
        self.assertEqual(json.loads(app.settings_path.read_text())['bitrate'],120)

    def test_new_install_auto_resolves_after_runtime_probe_without_saving_result(self):
        app=self.app();app.launch=Mock()
        self.assertEqual((app.settings['codec'],app.settings['resolution']),('Auto','automatic'))
        beyond=dict(HEADSET,model='Bigscreen Beyond 2',manufacturer='Bigscreen',width=3560,height=3560,refresh_hz=75)
        app.client_runtime.ensure.side_effect=lambda *_,**kw:setattr(app.client_runtime,'headset',beyond)
        app.action({'action':'start'})
        sent=app.session_link.start.call_args.args[0]
        # 1.0.0.13: Auto follows the Beyond's mode: Native (2560) at 75 Hz. Nothing asks SteamVR for a rate.
        self.assertEqual((sent['codec'],sent['resolution']),('Hevc','beyond-balanced'))
        self.assertNotIn('refresh',app.client_runtime.ensure.call_args.kwargs)
        self.assertEqual(sent['headset']['refresh_hz'],75)
        saved=json.loads(app.settings_path.read_text())
        self.assertEqual((saved['codec'],saved['resolution']),('Auto','automatic'))
        self.assertNotIn('headset',saved)

    def test_upgrade_retains_existing_manual_quality(self):
        settings=self.root/'VROSTBridge'/'client-settings.json'
        settings.parent.mkdir(parents=True,exist_ok=True)
        settings.write_text(json.dumps({'codec':'H264','resolution':'recommended','bitrate':120}))
        app=self.app()
        self.assertEqual((app.settings['codec'],app.settings['resolution'],app.settings['bitrate']),('H264','recommended',120))

    def test_older_settings_without_resolution_keep_legacy_budget(self):
        settings=self.root/'VROSTBridge'/'client-settings.json'
        settings.parent.mkdir(parents=True,exist_ok=True)
        settings.write_text(json.dumps({'codec':'Hevc','bitrate':80}))
        app=self.app()
        self.assertEqual((app.settings['codec'],app.settings['resolution']),('Hevc','compatibility'))

    def test_runtime_start_failure_rolls_back_remote_and_local(self):
        app = self.app(); app.launch = Mock()
        app.client_runtime.ensure.side_effect = ValueError('headset display not ready')
        with self.assertRaisesRegex(ValueError,'headset display not ready'):
            app.action({'action':'start'})
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()
        app.launch.assert_not_called()
        app.session_link.start.assert_not_called()

    def test_missing_headset_description_never_starts_a_guessed_host(self):
        app=self.app();app.launch=Mock();app.client_runtime.headset=None
        with self.assertRaisesRegex(ValueError,'headset description'):
            app.action({'action':'start'})
        app.session_link.start.assert_not_called()
        app.client_runtime.stop.assert_called_once_with()

    def test_resolution_is_saved_and_headset_metadata_is_session_scoped(self):
        app=self.app();app.launch=Mock()
        app.action({'action':'start','resolution':'index-120'})
        sent=app.session_link.start.call_args.args[0]
        self.assertEqual(sent['headset'],HEADSET)
        # 1.0.0.13: an Index mode is its Native size on the wire ('sharp', 2016), and its rate is asked of SteamVR.
        self.assertEqual(sent['resolution'],'sharp')
        self.assertEqual(app.client_runtime.ensure.call_args.kwargs.get('refresh'),120.0)
        saved=json.loads(app.settings_path.read_text())
        self.assertEqual(saved['resolution'],'index-120')
        self.assertNotIn('headset',saved)

    def test_native_start_failure_rolls_back_remote_and_local(self):
        app = self.app(); app.launch = Mock(side_effect=OSError('native loader failed'))
        with self.assertRaisesRegex(OSError,'native loader failed'):
            app.action({'action':'start'})
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()
        self.assertIsNone(app.runtime_directory)

    def test_gaze_directory_failure_rolls_back_remote_and_local(self):
        app = self.app(); app.launch = Mock()
        with patch('launcher.app.tempfile.mkdtemp',side_effect=OSError('runtime directory unavailable')):
            with self.assertRaisesRegex((OSError,ValueError),'runtime directory unavailable'):
                app.action({'action':'start','gaze':True})
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()
        app.launch.assert_not_called()

    def test_broker_failure_rolls_back_prepared_local_runtime(self):
        app = self.app(); app.launch = Mock()
        app.session_link.start.side_effect = ValueError('host unavailable')
        with self.assertRaisesRegex(ValueError,'host unavailable'):
            app.action({'action':'start'})
        app.client_runtime.ensure.assert_called_once_with(True,extra_env={})
        app.launch.assert_not_called()
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()

    def test_network_selection_failure_cleans_up_without_starting_host(self):
        app=self.app();app.launch=Mock()
        app.session_link.prepare_network.side_effect=ValueError('Selected interface disconnected')
        with self.assertRaisesRegex(ValueError,'Selected interface disconnected'):
            app.action({'action':'start'})
        app.session_link.start.assert_not_called()
        app.launch.assert_not_called()
        app.client_runtime.stop.assert_called_once_with()

    def test_stop_during_network_discovery_prevents_remote_start(self):
        app=self.app();app.launch=Mock()
        app.session_link.prepare_network.side_effect=lambda **kw:app.session_cancelled.set() or {}
        with self.assertRaisesRegex(ValueError,'cancelled'):
            app.action({'action':'start'})
        app.session_link.start.assert_not_called()
        app.launch.assert_not_called()
        app.client_runtime.stop.assert_called_once_with()

    def test_stop_cancels_network_discovery_before_waiting_for_action_lock(self):
        app=self.app();app.launch=Mock();entered=threading.Event();errors=[]
        def prepare(**kwargs):
            entered.set()
            if not kwargs['cancel'].wait(2):raise AssertionError('Stop could not cancel discovery')
            raise ValueError('Session start cancelled.')
        def start():
            try:app.action({'action':'start'})
            except ValueError as error:errors.append(str(error))
        app.session_link.prepare_network.side_effect=prepare
        worker=threading.Thread(target=start);worker.start()
        self.assertTrue(entered.wait(1))
        app.action({'action':'stop'})
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,['Session start cancelled.'])
        app.session_link.start.assert_not_called()
        app.launch.assert_not_called()

    def test_network_adapter_selection_is_explicit_and_stopped_only(self):
        app=self.app();app.session_link.select_interface.return_value={'interface':'wlan7'}
        app.action({'action':'select-client-network','interface':'wlan7'})
        app.session_link.select_interface.assert_called_once_with('wlan7')
        app.network_monitor.refresh.assert_called_once_with()
        app.process=Mock();app.process.poll.return_value=None
        with self.assertRaisesRegex(ValueError,'Stop the session'):
            app.action({'action':'select-client-network','interface':''})
        self.assertEqual(app.session_link.select_interface.call_count,1)

    def test_stop_during_host_start_rolls_back_local_runtime(self):
        app = self.app(); app.launch = Mock()
        app.session_link.start.side_effect = lambda *args,**kwargs:app.session_cancelled.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            app.action({'action':'start'})
        app.client_runtime.ensure.assert_called_once_with(True,extra_env={})
        app.launch.assert_not_called()
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()

    def test_stop_during_local_runtime_prevents_native_launch(self):
        app = self.app(); app.launch = Mock()
        app.client_runtime.ensure.side_effect = lambda *_,**kw:app.session_cancelled.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            app.action({'action':'start'})
        app.launch.assert_not_called()
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()

    def test_invalid_typed_client_settings_never_start_host(self):
        app = self.app(); app.launch = Mock()
        for key,value in (('microphone','false'),('gaze',1),('software',None),
                          ('lightweight','true'),('bitrate',True),('bitrate','80'),
                          ('bitrate',301),('codec','VP9'),('resolution','invalid'),('host_ip',123)):
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                app.action({'action':'start',key:value})
        app.session_link.start.assert_not_called()
        app.client_runtime.ensure.assert_not_called()
        app.launch.assert_not_called()

    def test_stop_keeps_native_scene_until_compositor_closed(self):
        app = self.app(); events = []; alive = [True]
        process = Mock(); process.poll.side_effect = lambda:None if alive[0] else 0
        process.terminate.side_effect = lambda:events.append('native.terminate')
        def wait(timeout=None):
            events.append('native.wait'); alive[0] = False; return 0
        process.wait.side_effect = wait
        app.process = process
        app.session_link.stop.side_effect = lambda:events.append('host.stop')
        app.client_runtime.stop.side_effect = lambda:events.append('runtime.stop')
        app.action({'action':'stop'})
        self.assertEqual(events,['host.stop','runtime.stop','native.terminate','native.wait'])
        app.client_runtime.cancel.assert_called_once_with()
        self.assertTrue(app.session_cancelled.is_set())

    def test_native_exit_ends_remote_and_local_session(self):
        app = self.app()
        process = Mock(); process.stdout = ['STATUS native ended\n']; process.wait.return_value = 0
        process.poll.return_value = 0; app.process = process
        app.watch(process)
        app.session_link.stop.assert_called_once_with()
        app.client_runtime.stop.assert_called_once_with()
        self.assertEqual(app.last_exit,0)

    def test_display_preflight_fails_before_host_start(self):
        app=self.app();app.lease_runtime.prepare=Mock(side_effect=ValueError('No headset lease'))
        with self.assertRaisesRegex(ValueError,'No headset lease'):app.action({'action':'start'})
        app.session_link.start.assert_not_called();app.client_runtime.ensure.assert_not_called()

    def test_failed_compositor_stop_keeps_display_adapter_alive(self):
        app=self.app();app.client_runtime.stop.side_effect=ValueError('Still stopping');app.lease_runtime.close=Mock()
        with self.assertRaisesRegex(ValueError,'Still stopping'):app.stop()
        app.lease_runtime.close.assert_not_called()

    def test_host_page_cannot_start_runtime(self):
        app = self.app('host')
        with self.assertRaisesRegex(ValueError,'headset client'):
            app.action({'action':'start'})
        app.host_control.start.assert_not_called()

    def test_opening_client_ui_performs_readiness_only(self):
        app = self.app(); app.launch = Mock()
        with patch('launcher.app.check_machine',return_value={'checks':[]}):
            app.autostart(); app.autostart()
        app.session_link.start.assert_not_called()
        app.client_runtime.ensure.assert_not_called()
        app.launch.assert_not_called()

    def test_opening_host_ui_prepares_listener_without_starting_steamvr(self):
        app = self.app('host')
        with patch('launcher.app.check_machine',return_value={'checks':[]}), \
             patch('launcher.app.require_private_home_network'), \
             patch.object(app,'setup_host') as setup:
            app.autostart()
        setup.assert_called_once_with('Firewall')
        app.host_control.start.assert_not_called()
        self.assertTrue(app.host_prepared)


if __name__ == '__main__':
    unittest.main()
