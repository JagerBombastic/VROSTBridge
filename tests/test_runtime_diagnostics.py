from headset_fixture import HEADSET_OUTPUT
"""Recovery, diagnostic sharing and host control regression checks for 0.2.4."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock, patch

from launcher.client_runtime import ClientRuntime, probe_diagnostic
from launcher.diagnostics import Journal, RemoteDiagnostics, DiagnosticServer, control_peers
from host.control import HostControl


class DiagnosticTests(unittest.TestCase):
    def test_startup_milestones_survive_long_video_telemetry(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=Journal(temp,'client')
            journal.record('log','STATUS Requesting host codec configuration and first keyframe')
            journal.record('log','STATUS Decoder configured; requesting a fresh keyframe')
            for _ in range(1600):journal.record('log','TIMING packets=0 decode_output_samples=0')
            report=journal.report()
            self.assertNotIn('Decoder configured',json.dumps(report['events']))
            self.assertIn('Decoder configured',json.dumps(report['milestones']))
            self.assertLess(len(json.dumps(report)),400*1024)

    def test_socket_ownership_and_established_state_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'71/fd').mkdir(parents=True);(root/'71/net').mkdir()
            (root/'71/fd/4').symlink_to('socket:[123]')
            def line(inode,state='01',local='26D7'):
                return f' 0: 00000000:{local} 0701A8C0:BE00 {state} 0 0 0 0 0 {inode}\n'
            # 9943=0x26d7. A different process, a listening socket and another
            # local service must never authorize diagnostic access.
            (root/'71/net/tcp').write_text('header\n'+line(999)+line(123,'0A')+line(123,local='26D9'))
            self.assertEqual(control_peers(71,root),set())
            (root/'71/net/tcp').write_text('header\n'+line(123))
            self.assertEqual(control_peers(71,root),{'192.168.1.7'})
            (root/'71/fd/4').unlink()
            self.assertEqual(control_peers(71,root),set())

    def test_readonly_endpoint_rejects_browser_and_unrelated_peer(self):
        with patch('launcher.diagnostics.control_peers',return_value={'127.0.0.1'}):
            service=DiagnosticServer(lambda:71,lambda:{'role':'client'},lambda:{'role':'client','report':True},lambda _:None,port=0)
            self.addCleanup(service.close)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url=f'http://127.0.0.1:{service.server.server_port}'
            def request(path,headers=None,data=None):return opener.open(urllib.request.Request(url+path,headers=headers or {},data=data),timeout=2)
            headers={'X-VROSTBridge-Diagnostics':'1'}
            with request('/summary',headers) as response:self.assertEqual(json.load(response),{'role':'client'})
            for path,h,data,code in [('/report',{},None,403),('/report',dict(headers,Origin='http://evil.test'),None,403),('/action',headers,None,404),('/report',headers,b'{}',501)]:
                with self.assertRaises(urllib.error.HTTPError) as context:request(path,h,data)
                self.assertEqual(context.exception.code,code);context.exception.close()
            service.peers.clear()
            with patch('launcher.diagnostics.control_peers',return_value=set()):
                with self.assertRaises(urllib.error.HTTPError) as context:request('/report',headers)
                self.assertEqual(context.exception.code,403);context.exception.close()

    def test_disconnect_grace_expires(self):
        with patch('launcher.diagnostics.control_peers',return_value={'192.168.1.7'}), patch('launcher.diagnostics.time.monotonic',return_value=10):
            service=DiagnosticServer(lambda:71,dict,dict,lambda _:None,port=0);self.addCleanup(service.close)
            self.assertTrue(service.allowed('192.168.1.7'));self.assertFalse(service.allowed('192.168.1.8'))
        with patch('launcher.diagnostics.control_peers',return_value=set()),patch('launcher.diagnostics.time.monotonic',return_value=71):
            self.assertFalse(service.allowed('192.168.1.7'))

    def test_journal_rotates_bounds_reports_and_removes_sensitive_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=Journal(temp,'client')
            journal.record('log','password=abc123\n{"Tracking": [1,2,3]}\nnormal')
            self.assertNotIn('abc123',json.dumps(journal.report()))
            self.assertNotIn('Tracking',json.dumps(journal.report()))
            for _ in range(5):
                journal.path.write_bytes(b'x'*(2*1024*1024+1));journal.record('log','rotate')
            self.assertTrue(Path(str(journal.path)+'.3').exists())
            self.assertFalse(Path(str(journal.path)+'.4').exists())
            for _ in range(200):journal.record('log','x'*8192)
            self.assertLess(len(json.dumps(journal.report())),400*1024)

    def test_concurrent_collection_is_serial_and_reopened_report_is_retained(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(RemoteDiagnostics,'poll'):
            remote=RemoteDiagnostics(temp,list,lambda _:None,lambda _:None);self.addCleanup(remote.close)
            remote.last_ip='192.168.1.7';active=0;maximum=0;guard=threading.Lock()
            def fetch(ip,path):
                nonlocal active,maximum
                with guard:active+=1;maximum=max(maximum,active)
                time.sleep(.03)
                with guard:active-=1
                return {'role':'client','version':'0.2.4','debug':{'session':'retained'}}
            remote.fetch=fetch
            threads=[threading.Thread(target=remote.capture) for _ in range(3)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            self.assertEqual(maximum,1)
            reopened=RemoteDiagnostics(temp,list,lambda _:None,lambda _:None);self.addCleanup(reopened.close)
            self.assertEqual(reopened.report()['client_report']['debug']['session'],'retained')
            self.assertFalse((Path(temp)/'last-client-report.tmp').exists())


class ClientRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.runtime=ClientRuntime(Path(self.temp.name)/'VROSTBridgeClient',self.temp.name,lambda _:None)
        setting=patch('launcher.client_runtime.settings_path',return_value=Path(self.temp.name)/'steamvr.vrsettings');setting.start();self.addCleanup(setting.stop)
    def result(self,code):return subprocess.CompletedProcess([],code,HEADSET_OUTPUT if code==0 else 'RUNTIME_WAIT 108','')
    def test_missing_runtime_launches_steam_once_then_prepares(self):
        self.runtime.helper=Mock(side_effect=[self.result(2),self.result(2),self.result(0)])
        with patch('launcher.client_runtime.shutil.which',side_effect=lambda _: '/usr/bin/steam'),patch('launcher.client_runtime.subprocess.Popen') as launch,patch.object(self.runtime.cancelled,'wait'):
            self.runtime.ensure()
        self.assertEqual(launch.call_count,1)
        self.assertEqual(launch.call_args.args[0],['/usr/bin/steam','-applaunch','250820'])
        self.assertEqual(self.runtime.state,'ready')
    def test_pending_settings_restored_before_new_session(self):
        self.runtime.receipt.write_text('VROSTBridgeRuntime2 1 1 0 1\n')
        (Path(self.temp.name)/'steamvr.vrsettings').write_text('{"steamvr":{"enableHomeApp":false,"sendSystemButtonToAllApps":true},"dashboard":{"enableDashboard":false}}')
        self.runtime.helper=Mock(return_value=self.result(0));self.runtime.ensure()
        self.runtime.helper.assert_called_once_with(timeout=8)
        self.assertTrue(self.runtime.restore())
        self.assertTrue(json.loads((Path(self.temp.name)/'steamvr.vrsettings').read_text())['steamvr']['enableHomeApp'])
    def test_cancelled_start_does_not_launch_after_cancel(self):
        def helper(**kwargs):self.runtime.cancel();return self.result(2)
        self.runtime.helper=helper
        with patch('launcher.client_runtime.shutil.which',return_value='/usr/bin/steam'),patch('launcher.client_runtime.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'cancelled'):self.runtime.ensure()
        # Cancellation during the helper must be checked before starting Steam.
        self.assertEqual(launch.call_count,0)
    def test_failed_restore_keeps_receipt_for_recovery(self):
        self.runtime.receipt.write_text('receipt');self.runtime.helper=Mock(side_effect=subprocess.TimeoutExpired('helper',8))
        self.runtime.restore();self.assertTrue(self.runtime.receipt.exists())

    def test_probe_stdout_survives_wrapper_stderr(self):
        output='RUNTIME_SAMPLE reported_hz=90 vsync_advances=0\nRUNTIME_WAIT display_progress\n'
        error='vrenv.sh[42]: exec /path/client --verify-runtime\nvrenv.sh[42]: VRCOMPOSITOR_LD_LIBRARY_PATH=/path\nERROR actual IPC problem'
        diagnostic=probe_diagnostic(output.encode(),error.encode())
        self.assertIn('reported_hz=90',diagnostic)
        self.assertIn('RUNTIME_WAIT display_progress',diagnostic)
        self.assertIn('ERROR actual IPC problem',diagnostic)
        self.assertNotIn('vrenv.sh',diagnostic)

    def test_slow_probe_retries_without_starting_steam_twice(self):
        self.runtime.helper=Mock(side_effect=[self.result(2),
            subprocess.TimeoutExpired('probe',8,output=b'RUNTIME_STAGE background_init',stderr=b'IPC slow'),self.result(0)])
        with patch('launcher.client_runtime.shutil.which',return_value='/usr/bin/steam'),patch('launcher.client_runtime.subprocess.Popen') as launch,patch.object(self.runtime.cancelled,'wait'):
            self.runtime.ensure()
        self.assertEqual(launch.call_count,1)
        self.assertEqual(self.runtime.state,'ready')
        self.assertEqual(self.runtime.probe_attempts,3)
        self.assertEqual(self.runtime.probe_timeouts,1)

    def test_probe_timeouts_respect_overall_deadline_and_keep_partial_output(self):
        clock=[0.0]
        def slow_probe(**kwargs):
            clock[0]+=kwargs['timeout']
            raise subprocess.TimeoutExpired('probe',kwargs['timeout'],output=b'RUNTIME_SAMPLE reported_hz=90',stderr=b'IPC timeout detail')
        self.runtime.helper=Mock(side_effect=slow_probe)
        with patch('launcher.client_runtime.time.monotonic',side_effect=lambda:clock[0]),patch('launcher.client_runtime.shutil.which',return_value='/usr/bin/steam'),patch('launcher.client_runtime.subprocess.Popen') as launch,patch.object(self.runtime.cancelled,'wait',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            with self.assertRaisesRegex(ValueError,'could not verify headset display timing'):
                self.runtime.ensure()
        self.assertEqual(clock[0],60)
        self.assertEqual(launch.call_count,1)
        self.assertEqual(self.runtime.probe_timeouts,7)
        self.assertIn('reported_hz=90',self.runtime.last_probe_diagnostic)
        self.assertIn('IPC timeout detail',self.runtime.last_probe_diagnostic)
        self.assertIn('probe_timeout',self.runtime.last_probe_diagnostic)

    def test_cancel_during_slow_probe_never_launches_steam(self):
        def cancel(**kwargs):
            self.runtime.cancel()
            raise subprocess.TimeoutExpired('probe',8)
        self.runtime.helper=Mock(side_effect=cancel)
        with patch('launcher.client_runtime.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'cancelled'):self.runtime.ensure()
        launch.assert_not_called()

    def test_fatal_probe_reports_actual_error_without_wrapper_banner(self):
        self.runtime.helper=Mock(return_value=subprocess.CompletedProcess([],1,'ERROR missing interface','vrenv.sh[1]: exec /probe'))
        with self.assertRaisesRegex(ValueError,'ERROR missing interface'):self.runtime.ensure()

    def test_packaged_probe_uses_steamvr_environment_and_bounded_timeout(self):
        probe=self.runtime.executable.with_name('VROSTBridgeRuntimeProbe');probe.touch()
        with patch('launcher.client_runtime.runtime_environment_wrapper',return_value=Path('/runtime/bin/vrenv.sh')),patch('launcher.client_runtime.subprocess.run') as run:
            self.runtime.helper(timeout=2.5)
        self.assertEqual(run.call_args.args[0],['/runtime/bin/vrenv.sh',str(probe),'--verify-runtime'])
        self.assertEqual(run.call_args.kwargs['timeout'],2.5)
        self.assertEqual(self.runtime.status()['probe_executable'],str(probe))


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.control=HostControl(self.root/'dist/host',lambda _:None,lambda:None,lambda:set())
    def test_force_shutdown_only_terminates_matching_identity(self):
        identity={'pid':12,'path':'C:/SteamVR/bin/win64/vrserver.exe','created':100}
        self.control.identities={12:identity,13:{'pid':13,'path':'other','created':200}}
        self.control.owned_pids={12}
        with patch('host.control.identify',side_effect=lambda pid:identity if pid==12 else None),patch('host.control.terminate') as stop:
            self.control._force_owned()
        stop.assert_called_once_with(identity)
        self.assertEqual(self.control.owned_pids,set())
    def test_changed_identity_refuses_force_shutdown(self):
        self.control.identities={12:{'pid':12,'path':'vrserver.exe','created':100}}
        with patch('host.control.identify',return_value={'pid':12,'path':'vrserver.exe','created':200}),patch('host.control.terminate') as stop:
            with self.assertRaisesRegex(ValueError,'no verified'):self.control._force_owned()
        stop.assert_not_called()
    def test_menu_press_and_release_use_pinned_button_api(self):
        self.control._ready=lambda:{'client':{'connection_state':'Streaming'}}
        self.control.active=True;self.control.api=Mock();callback=[]
        def timer(delay,fn):callback.append(fn);return Mock()
        with patch('host.control.threading.Timer',side_effect=timer):self.control.menu()
        callback[0]()
        calls=self.control.api.request.call_args_list
        self.assertEqual([c.args[0] for c in calls],['buttons','buttons'])
        self.assertEqual([c.args[1][0]['value'] for c in calls],[{'Binary':True},{'Binary':False}])
        self.assertEqual(calls[0].args[1][0]['path'],'/user/hand/left/input/system/click')
    def test_play_area_validates_dimensions_and_requires_helper_confirmation(self):
        self.control._ready=lambda:{}
        self.control.active=True
        helper=self.root/'host/VROSTBridgeVRHelper.exe';helper.parent.mkdir();helper.touch()
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root)}),patch('host.control.subprocess.run') as run:
            for width in (float('nan'),0,101,True,'2'):
                with self.assertRaises(ValueError):self.control.apply_playspace({'width':width,'depth':2})
            run.assert_not_called()
            run.return_value=SimpleNamespace(returncode=0,stdout='{"applied":true,"changed":false,"width":2,"depth":3,"boundary":"rectangle"}',stderr='')
            self.control.apply_playspace({'width':2,'depth':3})
            self.assertTrue(self.control.snapshot()['playspace']['applied'])
            self.assertEqual(run.call_args.args[0][2:4],['2','3'])
            run.return_value=SimpleNamespace(returncode=0,stdout='{"applied":false}',stderr='')
            with self.assertRaisesRegex(ValueError,'not confirmed'):self.control.apply_playspace({'width':2,'depth':3})

if __name__=='__main__':unittest.main()
