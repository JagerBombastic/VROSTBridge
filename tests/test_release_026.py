"""Regression checks for offline restoration, Game Mode ownership and quiet startup."""
import json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from launcher.client_runtime import ClientRuntime,STREAM_ONLY
from launcher.game_mode import GameMode,connectors,identity,unique
from launcher.windows_startup import WindowsStartup
from launcher.game_ui import View

def edid(maker='VLV',product=0x91a8):
 b=bytearray(128);b[:8]=b'\0\xff\xff\xff\xff\xff\xff\0';m=((ord(maker[0])-64)<<10)|((ord(maker[1])-64)<<5)|(ord(maker[2])-64);b[8:10]=m.to_bytes(2,'big');b[10:12]=product.to_bytes(2,'little');b[127]=(-sum(b))%256;return b
class SettingsTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.path=self.root/'steamvr.vrsettings';self.runtime=ClientRuntime(self.root/'native',self.root,lambda _:None)
  for p in (patch('launcher.client_runtime.settings_path',return_value=self.path),patch('launcher.client_runtime.runtime_processes',return_value={})):p.start();self.addCleanup(p.stop)
 def test_absent_keys_and_unrelated_preferences_round_trip(self):
  original={'steamvr':{'renderTargetMultiplier':1.2},'other':{'a':True}};self.path.write_text(json.dumps(original));self.runtime.prepare_settings();self.assertTrue(self.runtime.restore());self.assertEqual(json.loads(self.path.read_text()),dict(original,dashboard={}))
 def test_user_change_after_prepare_is_retained(self):
  self.path.write_text('{"steamvr":{"enableHomeApp":true}}');self.runtime.prepare_settings();changed=json.loads(self.path.read_text());changed['steamvr']['sendSystemButtonToAllApps']=False;self.path.write_text(json.dumps(changed));self.assertTrue(self.runtime.restore());self.assertFalse(json.loads(self.path.read_text())['steamvr']['sendSystemButtonToAllApps'])
 def test_live_runtime_never_restores_home(self):
  self.runtime.prepare_settings();before=self.path.read_bytes()
  with patch('launcher.client_runtime.runtime_processes',return_value={4:{}}):self.assertFalse(self.runtime.restore())
  self.assertTrue(self.runtime.receipt.exists());self.assertEqual(self.path.read_bytes(),before)
 def test_failed_atomic_write_leaves_recoverable_receipt(self):
  from launcher.client_runtime import atomic_json
  def write(p,data):
   if p==self.path:raise OSError('disk full')
   atomic_json(p,data)
  with patch('launcher.client_runtime.atomic_json',side_effect=write),self.assertRaises(OSError):self.runtime.prepare_settings()
  self.assertTrue(self.runtime.receipt.exists());self.assertTrue(self.runtime.restore())
class GameModeTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.sysfs=self.root/'sysfs';self.sysfs.mkdir();self.env=patch.dict(os.environ,{'HOME':str(self.root),'XDG_CONFIG_HOME':str(self.root/'config')},clear=True);self.env.start();self.addCleanup(self.env.stop);self.setup=GameMode(self.root,sysfs=self.sysfs);self.setup._capability=(True,'')
 def row(self,name='card1-DP-2',raw=None):
  p=self.sysfs/name;p.mkdir();(p/'status').write_text('connected');(p/'connector_id').write_text('123');(p/'edid').write_bytes(raw if raw is not None else edid());return p
 def test_edid_allowlist_rejects_monitor_and_corruption(self):
  self.assertEqual(identity(edid()),'Valve Index');self.assertEqual(identity(edid('BIG',0x1234)),'Bigscreen Beyond');self.assertIsNone(identity(edid('DEL',0x1234)));b=edid();b[20]=1;self.assertIsNone(identity(b))
 def test_duplicate_dp_name_on_other_gpu_rejected(self):
  self.row();r=connectors(self.sysfs)[0];self.assertTrue(unique(r,self.sysfs));self.row('card2-DP-2',edid('DEL'));self.assertFalse(unique(r,self.sysfs))
 def test_setup_reversible_and_user_edit_preserved(self):
  self.row();self.setup.enable();self.assertTrue(self.setup.status()['enabled']);self.assertIn('--gamescope-wrapper',self.setup.wrapper.read_text());self.setup.config.write_text('user change');
  with self.assertRaisesRegex(ValueError,'Modified'):self.setup.disable()
  self.assertEqual(self.setup.config.read_text(),'user change')
 def test_unmodified_setup_removed(self):
  self.setup.enable();self.setup.disable();self.assertFalse(self.setup.config.exists());self.assertFalse(self.setup.wrapper.exists())
 def test_foreign_wrapper_is_not_overwritten(self):
  self.setup.wrapper.parent.mkdir(parents=True);self.setup.wrapper.write_text('foreign')
  with self.assertRaisesRegex(ValueError,'preserved'):self.setup.enable()
  self.assertEqual(self.setup.wrapper.read_text(),'foreign')
 def test_existing_compositor_override_blocks_enable(self):
  self.setup.config.parent.mkdir(parents=True);(self.setup.config.parent/'80-other.conf').write_text('GAMESCOPE_BIN=/something\n')
  with self.assertRaisesRegex(ValueError,'override'):self.setup.enable()
class Registry:
 HKEY_CURRENT_USER=1;REG_SZ=1;KEY_SET_VALUE=2
 def __init__(self):self.value=None;self.kind=1
 def OpenKey(self,*args):
  if 'StartupApproved' in args[1]:raise FileNotFoundError()
  return self
 def CreateKeyEx(self,*args):return self
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def QueryValueEx(self,*args):
  if self.value is None:raise FileNotFoundError()
  return self.value,self.kind
 def SetValueEx(self,*args):self.value=args[-1]
 def DeleteValue(self,*args):self.value=None
class StartupTests(unittest.TestCase):
 def test_startup_uses_native_background_launcher_and_preserves_foreign_command(self):
  with tempfile.TemporaryDirectory() as d:
   exe=Path(d)/'VROSTBridge Host.exe';exe.touch();r=Registry();s=WindowsStartup(exe,r);s.set_enabled(True);self.assertEqual(r.value,'"'+str(exe)+'" --background');self.assertTrue(s.status()['enabled']);s.set_enabled(False);self.assertIsNone(r.value);r.value='foreign.exe'
   with self.assertRaises(ValueError):s.set_enabled(True)
   self.assertEqual(r.value,'foreign.exe')
class NativeControlsTests(unittest.TestCase):
 def test_stop_can_cancel_pending_start(self):
  entered=threading.Event();leave=threading.Event();calls=[]
  def action(d):
   calls.append(d['action'])
   if d['action']=='start':entered.set();leave.wait(3)
   else:leave.set()
  v=View(Path('.'),lambda:{},action,lambda:{},threading.Event());v.action('start');self.assertTrue(entered.wait(1));v.action('stop');self.assertTrue(leave.wait(1));self.assertEqual(calls,['start','stop'])
 def test_back_does_not_stop_a_session(self):
  v=View(Path('.'),lambda:{},Mock(),lambda:{},threading.Event());v.state={'running':True};v.choose('exit');self.assertEqual(v.page,'exit');self.assertFalse(v.stopped.is_set());v.choose('back');v.action_fn.assert_not_called();self.assertEqual(v.page,'main')
