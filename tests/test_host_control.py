import copy
import http.server
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from host.control import AlvrApi, HostControl, installed_api_version, require_home_network


class DriverFixture:
    def __init__(self, path):
        self.path=path
        self.active=False
        self.pid=100
        self.calls=[]
        self.launches=0
        self.version='21.0.0-dev14'
        fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,code=200,data=b''):
                self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                if self.headers.get('X-ALVR')!='true':self.respond(400);return
                if not fixture.active:self.respond(503);return
                if self.path=='/api/version':self.respond(data=fixture.version.encode('utf-8'))
                elif self.path=='/api/ping':self.respond()
                else:self.respond(404)
            def do_POST(self):
                if self.headers.get('X-ALVR')!='true':self.respond(400);return
                body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                data=json.loads(body) if body else None
                fixture.calls.append((self.path,data))
                if self.path=='/api/steamvr/shutdown':fixture.active=False
                elif self.path=='/api/session/values':
                    doc=json.loads(fixture.path.read_text());doc['session_settings']['connection']['client_discovery']['content']['auto_trust_clients']=data[0]['value'];fixture.path.write_text(json.dumps(doc))
                elif self.path=='/api/session/client-connections':
                    doc=json.loads(fixture.path.read_text())
                    hostname,action=data
                    if action=='Trust':doc['client_connections'][hostname]['trusted']=True
                    elif action=='RemoveEntry':doc['client_connections'].pop(hostname,None)
                    elif 'AddIfMissing' in action:
                        p=action['AddIfMissing'];doc['client_connections'].setdefault(hostname,dict(p,current_ip=None,connection_state='Disconnected',display_name='Unknown'))
                    elif 'SetManualIps' in action:doc['client_connections'][hostname]['manual_ips']=action['SetManualIps']
                    fixture.path.write_text(json.dumps(doc))
                else:self.respond(404);return
                self.respond()
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def launch(self):self.active=True;self.launches+=1;self.pid+=1
    def pids(self):return {self.pid} if self.active else set()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()


class HostControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.path=Path(self.temporary.name)/'session.json'
        self.driver=DriverFixture(self.path)
        self.document={'server_version':'21.0.0-dev14','restart_settings_hash':9007199254740993,
                       'session_settings':{'connection':{'web_server_port':self.driver.server.server_port}},
                       'client_connections':{'1.client.local.':{'trusted':False,'current_ip':None,'manual_ips':[],
                                                              'display_name':'VROSTBridge','connection_state':'Disconnected'}}}
        self.path.write_text(json.dumps(self.document));self.logs=[]
        self.control=HostControl(self.path.parent,self.logs.append,self.driver.launch,self.driver.pids)
    def tearDown(self):
        if self.control.active:self.control.stop()
        self.driver.close();self.temporary.cleanup()
    def wait_for(self,fn):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if fn():return
            time.sleep(.02)
        self.fail('Timed out: '+str(self.control.snapshot()))
    def start(self):
        self.control.start();self.wait_for(lambda:self.control.snapshot()['available'])
    def test_wire_pairing_and_manual_address(self):
        self.start();self.control.trust('1.client.local.')
        self.control.add('2.client.local.','192.168.1.7')
        self.assertEqual(self.driver.calls,[
            ('/api/session/client-connections',['1.client.local.','Trust']),
            ('/api/session/client-connections',['2.client.local.',{'AddIfMissing':{'trusted':False,'manual_ips':['192.168.1.7']}}]),
            ('/api/session/client-connections',['2.client.local.',{'SetManualIps':['192.168.1.7']}]),
            ('/api/session/client-connections',['2.client.local.','Trust'])])
        self.assertTrue(json.loads(self.path.read_text())['client_connections']['2.client.local.']['trusted'])
    def test_upgraded_nightly_profile_uses_installed_version_for_start_and_stop(self):
        self.document['server_version']='21.0.0-dev14+nightly.2026.09.18'
        self.path.write_text(json.dumps(self.document))
        (self.path.parent/'vrostbridge-build.json').write_text(json.dumps({'api_version':'21.0.0-dev14'}))
        self.start()
        self.assertEqual(self.control.snapshot()['api_version'],'21.0.0-dev14')
        self.control.stop()
        self.assertFalse(self.driver.active)
        self.assertEqual(self.control.snapshot()['runtime_state'],'stopped')
        self.assertIn(('/api/steamvr/shutdown',None),self.driver.calls)
        # Neither the version lookup nor lifecycle rewrites retained preferences.
        self.assertEqual(json.loads(self.path.read_text()),self.document)
    def test_really_different_api_is_rejected_even_if_mutable_profile_matches(self):
        self.driver.version='21.0.0-dev14+unexpected'
        self.document['server_version']=self.driver.version
        self.path.write_text(json.dumps(self.document))
        (self.path.parent/'vrostbridge-build.json').write_text(json.dumps({'api_version':'21.0.0-dev14'}))
        self.control.start()
        self.wait_for(lambda:not self.control.active)
        self.assertEqual(self.control.snapshot()['runtime_state'],'error')
        self.assertIn('version mismatch',self.control.snapshot()['error'])
        with self.assertRaisesRegex(ValueError,'running host version changed'):
            self.control.stop()
        self.assertEqual(self.driver.calls,[])
        self.assertTrue(self.driver.active)
        self.driver.active=False
    def test_invalid_manifest_is_not_replaced_by_profile_version(self):
        manifest=self.path.parent/'vrostbridge-build.json'
        for raw in ('{', '[]', '{}', '{"api_version":null}', '{"api_version":21}',
                    '{"api_version":""}', '{"api_version":"21.0.0-dev14\\n"}',
                    '{"api_version":"21.0.0-dev14 extra"}'):
            with self.subTest(raw=raw):
                manifest.write_text(raw)
                with self.assertRaisesRegex(ValueError,'metadata'):
                    self.control.start()
                self.assertEqual(self.driver.launches,0)
                self.assertFalse(self.control.active)
    def test_missing_manifest_with_packaged_driver_fails_before_launch(self):
        dll=self.path.parent/'bin/win64/driver_alvr_server.dll'
        dll.parent.mkdir(parents=True);dll.write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError,'metadata is missing'):
            self.control.start()
        self.assertEqual(self.driver.launches,0)
    def test_source_fallback_uses_checked_defaults_not_retained_profile(self):
        self.document['server_version']='21.0.0-dev14+nightly.2026.09.18'
        self.path.write_text(json.dumps(self.document))
        self.assertEqual(installed_api_version(self.path.parent),'21.0.0-dev14')
    def test_invalid_pairing_sends_no_commands(self):
        self.start()
        for hostname,address in [('2.client.local.','8.8.8.8'),('bad/hostname','192.168.1.7'),('1.client.local.',None)]:
            with self.assertRaises(ValueError):self.control.add(hostname,address)
        with self.assertRaises(ValueError):self.control.trust('unknown.client.local.')
        self.assertEqual(self.driver.calls,[])
    def test_negotiated_config_restarts_without_losing_trust(self):
        self.start();self.control.trust('1.client.local.')
        doc=json.loads(self.path.read_text());doc['restart_settings_hash']+=1;self.path.write_text(json.dumps(doc))
        self.wait_for(lambda:self.driver.launches==2 and self.control.snapshot()['available'])
        self.assertEqual(self.control.snapshot()['negotiation_restarts'],1)
        self.assertTrue(json.loads(self.path.read_text())['client_connections']['1.client.local.']['trusted'])
        self.assertIn(('/api/steamvr/shutdown',None),self.driver.calls)
        self.control.stop();self.assertFalse(self.driver.active)
    def test_negotiated_driver_exit_is_restarted_instead_of_reported_as_crash(self):
        self.start();self.control.trust('1.client.local.')
        doc=json.loads(self.path.read_text());doc['restart_settings_hash']+=1
        self.path.write_text(json.dumps(doc))
        # Pinned ALVR can shut its driver down immediately after persisting the
        # restart hash, before our monitor gets another successful HTTP reply.
        self.driver.active=False
        self.wait_for(lambda:self.driver.launches==2 and self.control.snapshot()['available'])
        self.assertEqual(self.control.snapshot()['negotiation_restarts'],1)
        self.assertEqual(self.control.snapshot()['error'],'')
        self.assertTrue(json.loads(self.path.read_text())['client_connections']['1.client.local.']['trusted'])
    def test_partial_write_does_not_claim_live_connection(self):
        self.document['client_connections']['1.client.local.']['connection_state']='Streaming'
        self.path.write_text(json.dumps(self.document));self.start()
        self.path.write_text('{')
        self.wait_for(lambda:self.control.snapshot()['session_stale'])
        self.assertEqual(self.control.snapshot()['clients'][0]['connection_state'],'Unknown')
        with self.assertRaises(ValueError):self.control.trust('1.client.local.')
        self.path.write_text(json.dumps(self.document))
    def test_foreign_runtime_refused(self):
        self.driver.active=True
        with self.assertRaises(ValueError):self.control.start()
        self.assertEqual(self.driver.launches,0)
        self.driver.active=False
    def test_stop_during_startup_waits_for_owned_runtime_then_stops_it(self):
        self.control.STARTUP_TIMEOUT=2
        self.control.launch=lambda:threading.Timer(.25,self.driver.launch).start()
        self.control.start();self.control.stop()
        self.assertFalse(self.driver.active)
        self.assertEqual(self.driver.launches,1)
        self.assertIn(('/api/steamvr/shutdown',None),self.driver.calls)
        self.assertEqual(self.control.snapshot()['runtime_state'],'stopped')
    def test_remove_access_turns_off_automatic_trust_before_removal(self):
        self.document['session_settings']['connection']['client_discovery']={'content':{'auto_trust_clients':True}}
        self.path.write_text(json.dumps(self.document));self.start()
        self.control.forget('1.client.local.')
        self.assertEqual(self.driver.calls[0][0],'/api/session/values')
        self.assertFalse(self.driver.calls[0][1][0]['value'])
        self.assertEqual(self.driver.calls[1],('/api/session/client-connections',['1.client.local.','RemoveEntry']))
        self.assertFalse(self.control.snapshot()['auto_connect'])
    def test_the_network_guard_needs_a_network_not_a_private_one(self):
        from types import SimpleNamespace
        # 1.0.0.14: Windows' Private/Public category no longer stops the host; only having no network does.
        with patch('host.control.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='[]')):
            with self.assertRaisesRegex(ValueError,'not connected'):require_home_network()
        for stdout in ('{"Name":"Home","NetworkCategory":1}','{"Name":"Home","NetworkCategory":0}'):
            with patch('host.control.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=stdout)):
                self.assertEqual(len(require_home_network()),1)
        mixed=[{'Name':'Home','NetworkCategory':0},{'Name':'Tailscale','NetworkCategory':0}]
        with patch('host.control.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(mixed))):
            self.assertEqual(len(require_home_network()),2)
    def test_a_session_arrives_on_a_home_network_address_without_a_windows_query(self):
        with patch('host.control.subprocess.run') as query:
            self.assertEqual(require_home_network('192.168.1.20'),[{'address':'192.168.1.20'}])
            query.assert_not_called()
        for bad in ("1.2.3.4'; Remove-Item x; '",'127.0.0.1','0.0.0.0','::1','not-an-ip','8.8.8.8'):
            with patch('host.control.subprocess.run') as query:
                with self.assertRaises(ValueError):require_home_network(bad)
                query.assert_not_called()
    def test_a_new_headset_pc_pairs_itself_only_from_a_private_network(self):
        from types import SimpleNamespace
        from host.control import pairing_network_private
        def run(stdout):
            return patch('host.control.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=stdout))
        for stdout in ('{"Name":"Home","NetworkCategory":1}','{"Name":"Work","NetworkCategory":2}'):
            with run(stdout) as query:
                self.assertTrue(pairing_network_private('192.168.1.20'))
                command=query.call_args.args[0][-1]
                self.assertIn("-IPAddress '192.168.1.20'",command);self.assertIn('-InterfaceIndex $index',command)
        for stdout in ('[]','{"Name":"Cafe","NetworkCategory":0}'):
            with run(stdout):
                with self.assertRaisesRegex(ValueError,'already paired work on any network'):pairing_network_private('192.168.1.20')
        with patch('host.control.subprocess.run') as query:
            with self.assertRaises(ValueError):pairing_network_private("1.2.3.4'; Remove-Item x; '")
            query.assert_not_called()
    def test_loopback_only_and_no_unknown_command(self):
        for port in ('9943','https://evil.test',True,0):
            with self.assertRaises(ValueError):AlvrApi(port)
        api=AlvrApi(self.driver.server.server_port)
        with self.assertRaises(ValueError):api.request('../evil')
    def test_snapshot_never_waits_for_runtime_poll(self):
        self.start()
        entered=threading.Event();release=threading.Event()
        original=self.control.api.request
        def block(endpoint,*args,**kwargs):
            if endpoint=='version':entered.set();release.wait(2)
            return original(endpoint,*args,**kwargs)
        self.control.api.request=block
        self.assertTrue(entered.wait(2))
        try:
            before=time.monotonic();self.control.snapshot();self.assertLess(time.monotonic()-before,.1)
        finally:release.set()

class AutostartTests(unittest.TestCase):
    def application(self,role):
        from launcher.app import Application
        # Exercise autostart without opening real discovery listeners or leaving
        # their worker threads alive across unrelated lifecycle tests.
        app=Application.__new__(Application)
        app.role=role;app.autostart_attempted=False
        app.action=Mock();app.prepare_host=Mock();app.log=Mock()
        app.action_lock=threading.Lock();app.startup_error=''
        app.host_prepared=False;app.host_preparation_attempted=False
        app.closing=threading.Event();app.autostart_cancelled=threading.Event()
        return app
    def test_open_prepares_host_once_without_starting_steamvr(self):
        app=self.application('host')
        app.autostart();app.autostart()
        app.action.assert_called_once_with({'action':'check'})
        app.prepare_host.assert_called_once_with()
    def test_host_preparation_failure_is_visible_without_starting_session(self):
        app=self.application('host');app.prepare_host.side_effect=ValueError('Private network required')
        app.autostart()
        app.action.assert_called_once_with({'action':'check'})
        self.assertEqual(app.startup_error,'Private network required')
    def test_open_client_only_checks_readiness_without_starting_steamvr(self):
        app=self.application('client')
        app.autostart();app.autostart()
        app.action.assert_called_once_with({'action':'check'})
        app.prepare_host.assert_not_called()


class HostShutdownOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.control=HostControl(Path(self.temp.name),lambda _:None,lambda:None,lambda:set())
        self.server={'pid':12,'path':str(Path(self.temp.name)/'vrserver.exe'),'created':100}
        self.compositor={'pid':13,'path':str(Path(self.temp.name)/'vrcompositor.exe'),'created':101}
        self.control.identities={12:self.server,13:self.compositor}
        self.control.owned_pids={12};self.control.runtime_observed=True
        self.control.expected_version='21.0.0-dev14'
        self.control.SHUTDOWN_TIMEOUT=1
        self.control.api=Mock()
        self.control.api.request.return_value=self.control.expected_version
        self.now=0

    def test_restart_waits_for_compositor_after_server_exits(self):
        live={12:self.server,13:self.compositor}
        self.control.process_ids=lambda:{12} if 12 in live else set()
        def request(endpoint,**kwargs):
            if endpoint=='steamvr/shutdown':live.pop(12,None)
            return self.control.expected_version
        self.control.api.request.side_effect=request
        def sleep(seconds):
            self.now+=seconds
            if self.now>=.5:live.pop(13,None)
        with patch('host.control.identify',side_effect=live.get), \
             patch('host.control.time.monotonic',side_effect=lambda:self.now), \
             patch('host.control.time.sleep',side_effect=sleep), \
             patch('host.control.terminate') as terminate:
            self.control._shutdown_runtime()
        self.assertGreaterEqual(self.now,.5)
        self.assertEqual(live,{})
        self.assertEqual(self.control.identities,{})
        terminate.assert_not_called()

    def test_lingering_compositor_is_recovered_before_restart_returns(self):
        live={12:self.server,13:self.compositor}
        self.control.process_ids=lambda:{12} if 12 in live else set()
        def request(endpoint,**kwargs):
            if endpoint=='steamvr/shutdown':live.pop(12,None)
            return self.control.expected_version
        self.control.api.request.side_effect=request
        def sleep(seconds):self.now+=seconds
        with patch('host.control.identify',side_effect=live.get), \
             patch('host.control.time.monotonic',side_effect=lambda:self.now), \
             patch('host.control.time.sleep',side_effect=sleep), \
             patch('host.control.terminate',side_effect=lambda item:live.pop(item['pid'])) as terminate:
            self.control._shutdown_runtime()
        terminate.assert_called_once_with(self.compositor)
        self.assertEqual(live,{})
        self.assertEqual(self.control.identities,{})

    def test_explicit_stop_recovers_compositor_after_server_crash(self):
        self.control.active=False;self.control.owned_pids=set()
        self.control._set(runtime_state='error',error='SteamVR exited.')
        self.control.api.request.side_effect=OSError('Driver exited')
        live={13:self.compositor}
        def sleep(seconds):self.now+=seconds
        with patch('host.control.identify',side_effect=live.get), \
             patch('host.control.time.monotonic',side_effect=lambda:self.now), \
             patch('host.control.time.sleep',side_effect=sleep), \
             patch('host.control.terminate',side_effect=lambda item:live.pop(item['pid'])) as terminate:
            self.control.stop()
        terminate.assert_called_once_with(self.compositor)
        self.assertEqual(self.control.snapshot()['runtime_state'],'stopped')

    def test_stop_after_crash_does_not_adopt_a_new_foreign_session(self):
        self.control.active=False;self.control.owned_pids=set()
        self.control.process_ids=lambda:{888}
        with patch('host.control.terminate') as terminate:
            with self.assertRaisesRegex(ValueError,'ownership changed'):self.control.stop()
        self.control.api.request.assert_not_called();terminate.assert_not_called()

    def test_reused_pid_is_not_recaptured_as_ours(self):
        from types import SimpleNamespace
        changed=dict(self.server,created=300)
        with patch('host.control.os',SimpleNamespace(name='nt')), \
             patch('host.control.steamvr_startup',return_value=Path(self.temp.name)/'vrstartup.exe'), \
             patch('host.control.windows_runtime_process_ids',return_value={}), \
             patch('host.control.identify',return_value=changed):
            with self.assertRaisesRegex(ValueError,'identity changed'):
                self.control._capture_identities({12})
        self.assertEqual(self.control.identities[12],self.server)

if __name__=='__main__':unittest.main()
