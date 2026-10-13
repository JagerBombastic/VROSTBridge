import unittest,sys,json,threading,urllib.request,urllib.error,struct,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher.app import server_for
from launcher.gaze_provider import parse_osc
class FakeApp:
 role='client';token='test-secret';lock=threading.RLock();lines=[];calls=[]
 def running(self):return False
 def action(self,body):self.calls.append(body)
 def status(self):
  import os
  return {'pid':os.getpid(),'role':self.role}
class LauncherTests(unittest.TestCase):
 def setUp(self):
  self.app=FakeApp();self.app.calls=[];self.server=server_for(self.app);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
 def request(self,token,origin=None):
  headers={'Content-Type':'application/json','X-VROSTBridge-Token':token}
  if origin:headers['Origin']=origin
  return urllib.request.urlopen(urllib.request.Request(self.url+'/action',data=b'{"action":"stop"}',headers=headers))
 def test_local_token_required(self):
  for token,origin in [('',None),('test-secret','https://example.com')]:
   with self.assertRaises(urllib.error.HTTPError) as e:self.request(token,origin)
   self.assertEqual(e.exception.code,403)
  self.assertEqual(self.app.calls,[])
  with self.request('test-secret',self.url) as response:self.assertEqual(response.status,200)
  self.assertEqual(self.app.calls,[{'action':'stop'}])
 def test_status_requires_authentication(self):
  with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(self.url+'/status')
 def test_logo_route_serves_png(self):
  with urllib.request.urlopen(self.url+'/brand/vrostbridge-256.png') as response:
   self.assertEqual(response.headers['Content-Type'],'image/png')
   self.assertEqual(response.read(8),b'\x89PNG\r\n\x1a\n')
  # Only the brand files the pages use are served; earlier artwork addresses are refused.
  for old in ('logo.png','icon.svg','icon-256.png','../app.py'):
   with self.assertRaises(urllib.error.HTTPError,msg=old):urllib.request.urlopen(self.url+'/brand/'+old)
 def test_existing_instance_authenticates_before_reopen(self):
  import tempfile
  from launcher.app import DesktopInstance
  self.app.token='reopen-token-with-sufficient-length'
  with tempfile.TemporaryDirectory() as directory:
   owner=DesktopInstance('client',directory)
   self.assertTrue(owner.acquire())
   owner.publish(self.server.server_port,self.app.token)
   second=DesktopInstance('client',directory)
   try:
    self.assertFalse(second.acquire())
    self.assertEqual(second.existing_url(),self.url+'/#'+self.app.token)
    record=json.loads(owner.record_path.read_text());record['pid']+=1
    owner.record_path.write_text(json.dumps(record))
    with self.assertRaises(ValueError):second.existing_url()
   finally:owner.close()
   self.assertTrue(second.acquire());second.close()
 def test_play_reuses_active_session_without_duplicate_start(self):
  from launcher.app import existing_play
  from unittest.mock import Mock
  states=iter([{'running':True,'busy':False},{'running':False,'busy':False}])
  self.app.status=lambda:next(states)
  instance=Mock();instance.existing_url.return_value=self.url+'/#'+self.app.token
  self.assertEqual(existing_play(instance),0)
  self.assertEqual(self.app.calls,[{'action':'stop'}])
 def test_play_does_not_interrupt_busy_companion(self):
  from launcher.app import existing_play
  from unittest.mock import Mock
  self.app.status=lambda:{'running':False,'busy':True}
  instance=Mock();instance.existing_url.return_value=self.url+'/#'+self.app.token
  with self.assertRaises(ValueError):existing_play(instance)
  self.assertEqual(self.app.calls,[])

class GazeTests(unittest.TestCase):
 def packet(self,pitch,yaw):
  def s(v):v=v.encode()+b'\0';return v+b'\0'*((-len(v))%4)
  return s('/tracking/eye/CenterPitchYaw')+s(',ff')+struct.pack('>ff',pitch,yaw)
 def test_neutral(self):self.assertEqual(parse_osc(self.packet(0,0)),(0,0,0,1))
 def test_quaternion_normalized(self):self.assertAlmostEqual(sum(v*v for v in parse_osc(self.packet(20,-30))),1)
 def test_reject_invalid(self):
  for data in (self.packet(float('nan'),0),self.packet(0,0)+b'bad'):
   with self.assertRaises(ValueError):parse_osc(data)
if __name__=='__main__':unittest.main()

class SessionStateTests(unittest.TestCase):
 def setUp(self):
  import tempfile
  from unittest.mock import patch
  from launcher.app import Application
  self.directory=tempfile.TemporaryDirectory()
  self.env=patch.dict('os.environ',{'XDG_STATE_HOME':self.directory.name,'LOCALAPPDATA':self.directory.name})
  self.env.start();self.app=Application('client')
 def tearDown(self):
  # These state tests use mocked native processes; close companion threads
  # while their state directory still exists rather than leaking them onward.
  self.app.process=None
  self.app.shutdown()
  self.env.stop();self.directory.cleanup()
 def test_metrics_expire_instead_of_claiming_streaming(self):
  from unittest.mock import Mock
  self.app.process=Mock();self.app.process.poll.return_value=None
  self.app.log('METRICS state=streaming decoded=12 dropped=2 frame_age_ms=18')
  self.assertEqual(self.app.status()['state'],'streaming')
  self.app.last_metrics-=4
  self.assertEqual(self.app.status()['state'],'unresponsive')
 def test_preferences_survive_restart(self):
  from launcher.app import Application
  self.app.settings['microphone']=True;self.app.save_settings()
  reopened=Application('client')
  try:self.assertTrue(reopened.settings['microphone'])
  finally:reopened.shutdown()
 def test_stop_does_not_block_status_or_output(self):
  import time
  from unittest.mock import Mock
  process=Mock();process.poll.return_value=None
  entered=threading.Event();release=threading.Event()
  def wait(timeout=None):entered.set();release.wait(2);return 0
  process.wait.side_effect=wait;self.app.process=process
  worker=threading.Thread(target=self.app.action,args=({'action':'stop'},));worker.start()
  self.assertTrue(entered.wait(1))
  try:
   start=time.monotonic();self.app.log('Stopping');self.assertTrue(self.app.status()['busy'])
   self.assertLess(time.monotonic()-start,.5)
  finally:release.set();worker.join()


class StartupTests(unittest.TestCase):
 def test_browser_failure_has_visible_recovery(self):
  from unittest.mock import patch
  from launcher.app import open_desktop
  url='http://127.0.0.1:12345/#private-token'
  with patch('launcher.app.webbrowser.open',return_value=False),patch('launcher.app.notify_desktop') as notify:
   self.assertFalse(open_desktop(url))
   self.assertIn(url,notify.call_args.args[0])
 def test_bootstrap_records_import_failure(self):
  import os,shutil,subprocess,tempfile
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);(base/'launcher').mkdir()
   shutil.copy2(root/'launcher/bootstrap.py',base/'launcher/bootstrap.py')
   env=dict(os.environ,LOCALAPPDATA=str(base/'state'),XDG_STATE_HOME=str(base/'state'),VROSTBRIDGE_NATIVE_LAUNCHER='1')
   result=subprocess.run([sys.executable,'-I',str(base/'launcher/bootstrap.py'),'--role','host'],env=env,cwd=base,capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,1)
   log=(base/'state/VROSTBridge/host-startup.log').read_text()
   self.assertIn('Traceback',log);self.assertIn('launcher.app',log)
 def test_lock_released_when_process_exits(self):
  import subprocess,tempfile
  from launcher.app import DesktopInstance
  root=Path(__file__).resolve().parents[1]
  script='from launcher.app import DesktopInstance; import sys; app=DesktopInstance("host",sys.argv[1]); assert app.acquire()'
  with tempfile.TemporaryDirectory() as directory:
   result=subprocess.run([sys.executable,'-c',script,directory],cwd=root,capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr)
   instance=DesktopInstance('host',directory)
   self.assertTrue(instance.acquire());instance.close()
