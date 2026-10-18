import copy
import json
import re
import unittest
import threading
from unittest.mock import Mock
from pathlib import Path
from headset_fixture import HEADSET, HEADSET_OUTPUT
from host.profile import configure, HEADSET_PROPERTIES, UNSUPPORTED_HOST_CAPABILITIES
from launcher.headset import headset_metadata, parse_headset, stream_width, resolve_stream_preferences
from launcher.session_link import session_settings

ROOT=Path(__file__).resolve().parents[1]

class HeadsetTests(unittest.TestCase):
    def profile(self, **kwargs):
        source=json.loads((ROOT/'host/default-session.json').read_text())
        return configure(source,headset=kwargs.pop('headset',HEADSET),**kwargs)['session_settings']

    def test_physical_probe_survives_broker_validation(self):
        self.assertEqual(parse_headset(HEADSET_OUTPUT),HEADSET)
        settings=session_settings({'codec':'H264','bitrate':80,'resolution':'sharp','headset':HEADSET})
        self.assertEqual(settings['headset'],HEADSET)
        self.assertEqual(settings['resolution'],'sharp')
        for text in ('ready',HEADSET_OUTPUT+HEADSET_OUTPUT,'HEADSET []'):
            with self.assertRaises(ValueError):parse_headset(text)

    def test_verified_beyond_refresh_reaches_host_negotiation(self):
        # Physical 75 Hz with a stale 90 Hz property is reconciled by the
        # native probe. Existing broker/profile schemas must retain 75 Hz.
        physical=dict(HEADSET,model='Beyond',manufacturer='Bigscreen',
                      width=1424,height=1424,refresh_hz=75.0)
        output=('RUNTIME_READY display_progress reported_hz=90 effective_hz=75 '
                'refresh_reconciled=1\nHEADSET '+json.dumps(physical)+'\n')
        settings=session_settings(resolve_stream_preferences({},parse_headset(output)))
        self.assertEqual(settings['headset']['refresh_hz'],75.0)
        self.assertEqual(settings['codec'],'Hevc')
        configured=self.profile(headset=settings['headset'],codec=settings['codec'],
                                resolution=settings['resolution'])
        self.assertEqual(configured['video']['preferred_fps'],75.0)

    def test_untrusted_metadata_is_bounded_before_native_property_bridge(self):
        for key,value in [('model','x'*193),('model','é'*97),('model','bad\0text'),
                          ('model','bad\ntext'),('width',True),('width',9000),
                          ('refresh_hz',float('nan')),('refresh_hz',True),('height',0)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                session_settings({'headset':dict(HEADSET,**{key:value})})
        with self.assertRaises(ValueError):headset_metadata(dict(HEADSET,extra='no'))

    def test_index_identity_and_only_implemented_capabilities(self):
        settings=self.profile(codec='H264',resolution='sharp')
        hmd=settings['headset'];props={r['key']['variant']:r['value'] for r in hmd['extra_openvr_props']['content']}
        self.assertEqual(hmd['emulation_mode']['variant'],'Custom')
        self.assertEqual(props['ModelNumberString'],'Index')
        self.assertEqual(props['ManufacturerNameString'],'Valve')
        self.assertEqual(props['TrackingSystemNameString'],'lighthouse')
        self.assertEqual(props['InputProfilePathString'],HEADSET['input_profile'])
        self.assertTrue(hmd['emulation_mode']['Custom']['serial_number'].startswith('VROSTBridge-'))
        for key in UNSUPPORTED_HOST_CAPABILITIES:self.assertEqual(props[key],'false')
        self.assertNotIn('DisplayFrequencyFloat',props) # Live negotiated property.
        self.assertNotIn('UserIpdMetersFloat',props) # Live geometry still owns IPD.
        self.assertEqual(hmd['controllers']['content']['emulation_mode']['variant'],'ValveIndex')

    def test_property_names_exist_in_pinned_openvr_header(self):
        text=(ROOT/'third_party/openvr/openvr.h').read_text()
        keys={p.replace('_','') for p in re.findall(r'\bProp_(\w+)\s*=',text)}
        for key in set(HEADSET_PROPERTIES.values())|set(UNSUPPORTED_HOST_CAPABILITIES)|{'DriverVersionString'}:
            self.assertIn(key,keys)

    def test_beyond_switch_replaces_owned_identity_and_keeps_other_user_properties(self):
        source=json.loads((ROOT/'host/default-session.json').read_text())
        source['session_settings']['headset']['extra_openvr_props']['content']=[{'key':{'variant':'UserHeadToEyeDepthMetersFloat'},'value':'0.012'}]
        index=configure(source,headset=HEADSET)
        beyond=dict(HEADSET,model='Bigscreen Beyond 2e',manufacturer='Bigscreen',serial='other',input_profile='/client/path.json')
        changed=configure(index,headset=beyond)['session_settings']['headset']
        rows=changed['extra_openvr_props']['content'];props={r['key']['variant']:r['value'] for r in rows}
        self.assertEqual(len(props),len(rows));self.assertEqual(props['ModelNumberString'],beyond['model'])
        self.assertEqual(props['UserHeadToEyeDepthMetersFloat'],'0.012')
        self.assertNotIn('InputProfilePathString',props)
        self.assertNotEqual(changed['emulation_mode']['Custom']['serial_number'],index['session_settings']['headset']['emulation_mode']['Custom']['serial_number'])
        self.assertEqual(len(source['session_settings']['headset']['extra_openvr_props']['content']),1)

    def test_resolution_selection_is_explicit_and_clamped_for_stereo(self):
        self.assertEqual(stream_width('H264'),1440)
        self.assertEqual(stream_width('Hevc'),2144)
        self.assertEqual(stream_width('H264','sharp'),2016)
        self.assertEqual(stream_width('Hevc','sharp'),2016)
        self.assertEqual(stream_width('H264','recommended',dict(HEADSET,width=3500)),2048)
        self.assertEqual(stream_width('Hevc','recommended',dict(HEADSET,width=3500)),3488)
        for field in ('transcoding_view_resolution','emulated_headset_view_resolution'):
            res=self.profile(codec='H264',resolution='sharp')['video'][field]
            self.assertEqual(res['Absolute']['width'],2016)
            self.assertFalse(res['Absolute']['height']['set'])
        with self.assertRaises(ValueError):stream_width('H264','recommended')
        with self.assertRaises(ValueError):session_settings({'resolution':'ultra'})

    def test_game_mode_cycles_the_same_saved_resolution_setting(self):
        from launcher.game_ui import View
        view=View(ROOT,lambda:{},Mock(),lambda:{},threading.Event())
        other=dict(HEADSET,model='Generic HMD',manufacturer='Example')
        view.action=Mock();view.state={'settings':{'resolution':'compatibility'},'client_runtime':{'headset':other}}
        view.choose('resolution');view.action.assert_called_with('client-preferences',resolution='sharp')
        view.state['settings']['resolution']='sharp'
        view.choose('resolution');view.action.assert_called_with('client-preferences',resolution='recommended')
        # 1.0.0.13: an Index offers its modes; a saved size it no longer offers shows as Auto, so the next is its first.
        view.state={'settings':{'resolution':'compatibility'},'client_runtime':{'headset':HEADSET}}
        view.choose('resolution');view.action.assert_called_with('client-preferences',resolution='index-80')
        view.state['settings']['resolution']='index-120'
        view.choose('resolution');view.action.assert_called_with('client-preferences',resolution='index-144')
