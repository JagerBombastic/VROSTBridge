import unittest,json,sys,tempfile,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from host.profile import configure,manual_client,write_session
class HostTests(unittest.TestCase):
 def setUp(self): self.defaults=json.loads((Path(__file__).resolve().parents[1]/'host/default-session.json').read_text())
 def test_profile_preserves_other_fields(self):
  self.defaults['unrelated']={'keep':True};copy=configure(self.defaults)
  self.assertEqual(copy['unrelated'],{'keep':True})
  self.assertEqual(copy['session_settings']['headset']['controllers']['content']['emulation_mode']['variant'],'ValveIndex')
  self.assertFalse(copy['session_settings']['connection']['client_discovery']['content']['auto_trust_clients'])
  self.assertTrue(copy['session_settings']['connection']['avoid_video_glitching'])
  self.assertNotEqual(copy['session_settings']['video']['foveated_encoding']['enabled'],self.defaults['session_settings']['video']['foveated_encoding']['enabled'])
 def test_raw_index_pose_profile_removes_inherited_transforms(self):
  controllers=self.defaults['session_settings']['headset']['controllers']['content']
  controllers['left_controller_position_offset']['content']=[0.4,-0.2,-0.11]
  controllers['left_controller_rotation_offset']['content']=[30.0,45.0,90.0]
  controllers['linear_velocity_cutoff']=0.5
  controllers['angular_velocity_cutoff']=25.0
  controllers['hand_skeleton']['enabled']=True
  applied=configure(self.defaults)['session_settings']['headset']['controllers']['content']
  self.assertEqual(applied['left_controller_position_offset']['content'],[0.0,0.0,0.0])
  self.assertEqual(applied['left_controller_rotation_offset']['content'],[0.0,0.0,0.0])
  self.assertEqual(applied['linear_velocity_cutoff'],0.0)
  self.assertEqual(applied['angular_velocity_cutoff'],0.0)
  self.assertTrue(applied['hand_skeleton']['enabled'])
  self.assertFalse(applied['hand_skeleton']['content']['steamvr_input_2_0'])
  self.assertFalse(applied['hand_skeleton']['content']['predict'])
  self.assertFalse(applied['hand_tracking_interaction']['enabled'])
  self.assertEqual(applied['left_hand_tracking_position_offset']['content'],[0.0,0.0,0.0])
  self.assertEqual(applied['left_hand_tracking_rotation_offset']['content'],[0.0,0.0,0.0])
  self.assertTrue(applied['tracked'])
  # Applying the profile must not mutate the user's backed-up document.
  self.assertEqual(controllers['left_controller_position_offset']['content'],[0.4,-0.2,-0.11])
 def test_beyond_physical_refresh_is_preferred_without_host_display_controls(self):
  from launcher.headset import TEXT_FIELDS
  description={key:'' for key in TEXT_FIELDS}
  description.update(model='Bigscreen Beyond 2e',manufacturer='Bigscreen',
                     width=3560,height=3560,refresh_hz=75)
  applied=configure(self.defaults,codec='Hevc',headset=description)
  settings=applied['session_settings']
  self.assertEqual(settings['video']['preferred_fps'],75.0)
  props={entry['key']['variant']:entry['value'] for entry in settings['headset']['extra_openvr_props']['content']}
  self.assertEqual(props['DisplaySupportsRuntimeFramerateChangeBool'],'false')
  self.assertEqual(configure(self.defaults)['session_settings']['video']['preferred_fps'],90.0)
 def test_unknown_schema_rejected(self):
  del self.defaults['session_settings']['video']['encoder_config']
  with self.assertRaises(ValueError):configure(self.defaults)
 def test_codec_profiles_bound_stereo_width_and_preserve_headset_aspect(self):
  video=self.defaults['session_settings']['video']
  fields=('transcoding_view_resolution','emulated_headset_view_resolution')
  for field in fields:
   video[field]['variant']='Scale'
   video[field]['Scale']=2.0
   video[field]['Absolute']['height']={'set':True,'content':999}
  first=configure(self.defaults,codec='H264')
  for field in fields:
   resolution=first['session_settings']['video'][field]
   self.assertEqual(resolution['variant'],'Absolute')
   self.assertEqual(resolution['Absolute']['width']*2,2880)
   self.assertFalse(resolution['Absolute']['height']['set'])
  # A codec change must restore its own resolution, including after an upgrade
  # from a session that already saved the conservative H.264 profile.
  quality=configure(first,codec='Hevc')
  for field in fields:
   resolution=quality['session_settings']['video'][field]
   self.assertEqual(resolution['Absolute']['width'],2144)
   self.assertFalse(resolution['Absolute']['height']['set'])
  again=configure(quality,codec='H264')
  self.assertEqual(again['session_settings']['video']['transcoding_view_resolution']['Absolute']['width'],1440)
  self.assertEqual(video['transcoding_view_resolution']['variant'],'Scale')
  self.assertTrue(video['transcoding_view_resolution']['Absolute']['height']['set'])
 def test_manual_address_does_not_trust(self):
  doc=manual_client(self.defaults,'1234.client.local.','192.168.1.2')
  self.assertFalse(doc['client_connections']['1234.client.local.']['trusted'])
  for address in ('8.8.8.8','127.0.0.1','224.0.0.1','0.0.0.0'):
   with self.assertRaises(ValueError):manual_client(self.defaults,'1234.client.local.',address)
 def test_every_native_index_input_gets_unchanged_host_passthrough(self):
  mapping=configure(self.defaults)['session_settings']['headset']['controllers']['content']['button_mappings']
  self.assertTrue(mapping['set'])
  header=(Path(__file__).resolve().parents[1]/'client/device/input_protocol.hpp').read_text()
  suffixes=re.findall(r'\{"[^"]+", "([^"]+)", [01], [01]\}',header)
  rows=dict(mapping['content']['content'])
  expected={'/user/hand/'+side+'/input/'+suffix for side in ('left','right') for suffix in suffixes}
  self.assertEqual(set(rows),expected);self.assertEqual(len(rows),36)
  # Verify the dictionary -> vector -> section -> choice structure consumed
  # by pinned ALVR json_session_settings_to_settings, not a flat pseudo-profile.
  for path,vector in rows.items():
   targets=vector['content'];self.assertEqual(len(targets),1)
   self.assertEqual(targets[0]['destination'],path)
   self.assertEqual(targets[0]['mapping_type']['variant'],'Passthrough')
   self.assertEqual(targets[0]['binary_conditions']['content'],[])
  self.assertFalse(self.defaults['session_settings']['headset']['controllers']['content']['button_mappings']['set'])
 def test_explicit_custom_controller_map_is_preserved(self):
  mapping=self.defaults['session_settings']['headset']['controllers']['content']['button_mappings']
  mapping['set']=True;mapping['content']['content']=[['custom',{'content':[]}]]
  applied=configure(self.defaults)['session_settings']['headset']['controllers']['content']['button_mappings']
  self.assertEqual(applied,mapping)
 def test_backup_before_replace(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'session.json';p.write_text('original');write_session(p,configure(self.defaults));backups=list(Path(d).glob('*.bak'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'original');self.assertIn('session_settings',json.loads(p.read_text()))
if __name__=='__main__':unittest.main()
