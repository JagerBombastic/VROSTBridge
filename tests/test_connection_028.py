"""Regressions for the supplied missing-microphone handshake and tray UX."""
import ctypes,json,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from host.audio import microphone_status,select_pair,endpoints
from host.profile import configure
from launcher.session_link import session_settings
from launcher.windows_tray import WindowsTray,tray_view,WM_TRAY
from launcher.diagnostics import DiagnosticServer,RemoteDiagnostics
from test_session_lifecycle import SessionLifecycleTests

ROOT=Path(__file__).resolve().parents[1]
def profile():return json.loads((ROOT/'host/default-session.json').read_text())

class MicrophoneTests(unittest.TestCase):
    def test_default_profile_does_not_require_virtual_microphone(self):
        audio=profile()['session_settings']['audio']
        self.assertFalse(audio['microphone']['enabled']);self.assertTrue(audio['game_audio']['enabled'])
    def test_legacy_host_microphone_cannot_override_client_off(self):
        doc=profile();doc['session_settings']['audio']['microphone']['enabled']=True;probe=Mock()
        status=microphone_status(False,doc,Path('probe'),probe)
        configured=configure(doc,status['microphone_enabled'])
        self.assertFalse(configured['session_settings']['audio']['microphone']['enabled']);probe.assert_not_called()
        self.assertTrue(configured['session_settings']['audio']['game_audio']['enabled'])
    def test_physical_microphone_is_not_a_virtual_route(self):
        status=microphone_status(True,profile(),Path('probe'),lambda _:dict(render=['Speakers'],capture=['Index Microphone']))
        self.assertFalse(status['microphone_enabled']);self.assertTrue(status['microphone_requested']);self.assertIn('continuing',status['warning'])
    def test_both_active_cable_directions_are_required(self):
        cfg={'variant':'Automatic'}
        self.assertIsNone(select_pair(cfg,{'render':['CABLE Input'], 'capture':['Physical microphone']}))
        self.assertEqual(select_pair(cfg,{'render':['CABLE Input (VB-Audio)'],'capture':['CABLE Output (VB-Audio)']}),('CABLE Input (VB-Audio)','CABLE Output (VB-Audio)'))
    def test_valid_route_enables_requested_microphone(self):
        status=microphone_status(True,profile(),Path('probe'),lambda _:dict(render=['VoiceMeeter Aux Input'],capture=['VoiceMeeter Aux Output']))
        self.assertTrue(status['microphone_enabled']);self.assertEqual(status['warning'],'')
    def test_unavailable_probe_falls_back_without_a_handshake_failure(self):
        for error in (FileNotFoundError(),subprocess.TimeoutExpired('probe',8),ValueError('Invalid JSON')):
            status=microphone_status(True,profile(),Path('probe'),Mock(side_effect=error))
            self.assertFalse(status['microphone_enabled']);self.assertTrue(status['warning'])
    def test_custom_names_match_pinned_case_insensitive_rules(self):
        cfg={'variant':'Custom','Custom':{'sink':{'variant':'NameSubstring','NameSubstring':'custom output'},'source':{'variant':'NameSubstring','NameSubstring':'custom input'}}}
        self.assertEqual(select_pair(cfg,dict(render=['CUSTOM OUTPUT'],capture=['CUSTOM INPUT'])),('CUSTOM OUTPUT','CUSTOM INPUT'))
        cfg['Custom']['sink']={'variant':'Index','Index':0}
        self.assertIsNone(select_pair(cfg,dict(render=['Speaker'],capture=['CUSTOM INPUT'])))
    def test_request_boolean_is_strict_and_legacy_request_defaults_off(self):
        self.assertFalse(session_settings({'codec':'H264'})['microphone'])
        self.assertTrue(session_settings({'microphone':True})['microphone'])
        for bad in (1,'false',None):
            with self.assertRaises(ValueError):session_settings({'microphone':bad})
    def test_probe_output_is_validated(self):
        for raw in ('[]','{"render":"Speaker","capture":[]}','{"render":[1],"capture":[]}'):
            with patch('host.audio.subprocess.run',return_value=subprocess.CompletedProcess([],0,raw,'')),self.assertRaises(ValueError):endpoints('probe')

class AppMicrophoneTests(SessionLifecycleTests):
    def test_host_start_migrates_legacy_true_without_probing_when_client_is_off(self):
        app=self.app('host');app.host=self.root/'host';app.host.mkdir();(app.host/'ALVR Dashboard.exe').touch()
        doc=profile();doc['session_settings']['audio']['microphone']['enabled']=True
        app.session=Mock(return_value=doc);app.settings['microphone']=True
        app.host_prepared=True;app.ensure_host_stopped=Mock();app.setup_host=Mock()
        with patch('launcher.app.require_private_home_network'),patch('launcher.app.host_loader_check',return_value={'ok':True}),patch('host.audio.subprocess.run') as probe:
            app.start_host_session({'codec':'H264','bitrate':80,'microphone':False})
        saved=json.loads((app.host/'session.json').read_text())['session_settings']['audio']
        self.assertFalse(saved['microphone']['enabled']);self.assertTrue(saved['game_audio']['enabled']);probe.assert_not_called();app.host_control.start.assert_called_once()
    def test_client_transmits_microphone_selection(self):
        app=self.app();app.launch=Mock();app.action({'action':'start','microphone':True})
        self.assertTrue(app.session_link.start.call_args.args[0]['microphone'])
    def test_client_failure_is_logged_before_remote_cleanup_collects_report(self):
        app=self.app();app.client_runtime.ensure.side_effect=ValueError('Local display startup failed')
        seen=[];app.session_link.stop.side_effect=lambda:seen.append(app.startup_error)
        with self.assertRaisesRegex(ValueError,'Local display'):app.action({'action':'start'})
        self.assertEqual(seen,['Local display startup failed'])

class DiagnosticStartupTests(unittest.TestCase):
    def test_only_current_paired_host_is_authorized_before_native_socket(self):
        peer={'active':True,'state':'starting','host_ip':'192.168.77.29'}
        with patch('launcher.diagnostics.control_peers',return_value=set()):
            service=DiagnosticServer(lambda:None,dict,dict,lambda _:None,port=0,session_peer=lambda:peer);self.addCleanup(service.close)
            self.assertTrue(service.allowed('192.168.77.29'));self.assertFalse(service.allowed('192.168.77.30'))
            peer.clear();service.peers.clear();self.assertFalse(service.allowed('192.168.77.29'))
            peer.update(active=False,state='starting',host_ip='192.168.77.29');self.assertFalse(service.allowed('192.168.77.29'))
    def test_failed_handshake_report_is_collected_without_streaming(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(RemoteDiagnostics,'poll'):
            peer={'lease_id':'abc','client_ip':'192.168.77.8','state':'starting'}
            remote=RemoteDiagnostics(directory,list,Mock(),Mock(),session_peer=lambda:peer);self.addCleanup(remote.close)
            remote.fetch=Mock(return_value={'role':'client','startup_error':'No display','version':'0.2.8'})
            result=remote.capture();remote.fetch.assert_called_once_with('192.168.77.8','/report')
            self.assertTrue(result['client_report_current']);self.assertEqual(result['client_report']['startup_error'],'No display')
            remote.use_peer('192.168.77.8','next-lease');self.assertFalse(remote.report()['client_report_current'])
    def test_cached_report_is_explicitly_not_current(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(RemoteDiagnostics,'poll'):
            (Path(directory)/'last-client-report.json').write_text('{"role":"client","version":"0.2.4"}')
            remote=RemoteDiagnostics(directory,list,Mock(),Mock());self.addCleanup(remote.close)
            self.assertFalse(remote.report()['client_report_current']);self.assertEqual(remote.report()['client_report']['version'],'0.2.4')

class NativeTrayTests(unittest.TestCase):
    def test_status_distinguishes_driver_ready_from_stream_and_retains_failure(self):
        self.assertEqual(tray_view({'state':'host-ready'})[0],'Ready for headset')
        self.assertEqual(tray_view({'running':True,'host_control':{'clients':[{'connection_state':'Streaming'}]}})[0],'Streaming')
        label,details=tray_view({'state':'stopped','session_link':{'last_error':'Handshake failed'}})
        self.assertEqual(label,'Needs attention');self.assertIn('Handshake failed',details)
    def test_real_message_callback_right_click_keyboard_and_exit(self):
        # Exercise _pump's registered WNDPROC and popup dispatch via a Win32
        # adapter; this is not claimed as a Windows/Explorer execution test.
        class Fn:
            def __init__(self,fn=lambda *args:1):self.fn=fn
            def __call__(self,*args):return self.fn(*args)
        class API:
            def __getattr__(self,name):v=Fn();setattr(self,name,v);return v
        for message,lp,choice in ((WM_TRAY,(1<<16)|0x7b,1),(WM_TRAY,0x205,2),(0x7b,0,1)):
            with self.subTest(message=message,lp=lp):
                user,shell,kernel=API(),API(),API();state={'events':1};labels=[];notifications=[];order=[]
                user.RegisterClassW=Fn(lambda wc:state.update(proc=wc._obj.proc) or 1)
                def create(ex,name,title,style,*args):
                    self.assertTrue(ex&0x80);self.assertTrue(style&0x80000000)
                    return 123
                user.CreateWindowExW=Fn(create)
                user.SetWindowPos=Fn(lambda hwnd,after,x,y,w,h,flags:order.append(('pos',after.value,flags,w,h)) or 1)
                user.SetForegroundWindow=Fn(lambda hwnd:order.append(('foreground',)) or 1)
                user.ShowWindow=Fn(lambda hwnd,show:order.append(('show',show)) or 1)
                user.RegisterWindowMessageW=Fn(lambda _:0xc123)
                user.AppendMenuW=Fn(lambda h,flags,id,text:labels.append(text) or 1)
                user.TrackPopupMenu=Fn(lambda *args:order.append(('menu',)) or choice)
                shell.Shell_NotifyIconW=Fn(lambda action,nid:notifications.append((action,nid._obj.flags)) or 1)
                def getmsg(msg,*_):
                    if not state['events']:return 0
                    state['events']=0;msg._obj.message=message;msg._obj.lParam=lp;return 1
                user.GetMessageW=Fn(getmsg)
                user.DispatchMessageW=Fn(lambda msg:state['proc'](123,msg._obj.message,0,msg._obj.lParam))
                opened,exited=threading.Event(),threading.Event();logs=[]
                tray=WindowsTray('icon.ico',opened.set,exited.set,logs.append)
                with patch.object(ctypes,'WinDLL',side_effect=lambda name,**kw:{'user32':user,'shell32':shell,'kernel32':kernel}[name],create=True),patch.object(ctypes,'WINFUNCTYPE',ctypes.CFUNCTYPE,create=True):tray._pump()
                self.assertTrue((opened if choice==1 else exited).wait(1));self.assertFalse(logs)
                self.assertIn('Settings — open web UI',labels);self.assertIn('Exit',labels)
                self.assertEqual(order[0],('pos',ctypes.c_void_p(-1).value,0x50,0,0))
                self.assertEqual(order[1:4],[('foreground',),('menu',),('show',0)])
                self.assertEqual(order[4][1],ctypes.c_void_p(-2).value)
                self.assertTrue(next(flags for action,flags in notifications if action==0)&0x80)

def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (MicrophoneTests,AppMicrophoneTests,DiagnosticStartupTests,NativeTrayTests):
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    return suite
