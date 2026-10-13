"""Physical-device driven display selection without controlling vendor hardware modes."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock

from headset_fixture import HEADSET
from host.profile import configure
from launcher.headset import (client_stream_preferences, display_status, headset_family,
                              resolve_stream_preferences, stream_width)
from launcher.session_link import session_settings
from launcher.game_ui import View

ROOT = Path(__file__).resolve().parents[1]
BEYOND = dict(HEADSET, model='Bigscreen Beyond 2e', manufacturer='Bigscreen',
              width=2688, height=2688, refresh_hz=90)
AUTO = {'codec':'Auto', 'bitrate':80, 'resolution':'automatic'}


class BeyondDisplayTests(unittest.TestCase):
    def test_same_auto_preferences_follow_fresh_physical_device(self):
        index = session_settings(resolve_stream_preferences(AUTO, HEADSET))
        beyond = session_settings(resolve_stream_preferences(AUTO, BEYOND))
        self.assertEqual((index['codec'], index['resolution']), ('H264','sharp'))
        # RC4.3: Auto starts the Beyond at Standard (2016). A headset PC with integrated Radeon graphics held 75 fps there
        # but kept falling to half rate at High (2560), which stays an explicit choice.
        self.assertEqual((beyond['codec'], beyond['resolution']), ('Hevc','sharp'))
        self.assertEqual(AUTO, {'codec':'Auto','bitrate':80,'resolution':'automatic'})
        self.assertEqual(stream_width(beyond['codec'], beyond['resolution'], BEYOND), 2016)
        self.assertEqual(stream_width('Hevc', 'beyond-balanced', BEYOND), 2560)
        # No auto token or unknown property reaches the fixed upstream host API.
        self.assertEqual(set(beyond), {'codec','bitrate','resolution','microphone','headset'})
        with self.assertRaises(ValueError):session_settings(AUTO)

    def test_recommended_resolution_and_active_refresh_come_from_runtime(self):
        mode75 = dict(BEYOND, width=3560, height=3560, refresh_hz=75)
        display = display_status(AUTO, mode75)
        self.assertEqual(display['recommended_width'], 3560)
        self.assertEqual(display['refresh_hz'], 75)
        self.assertEqual(display['stream_width'], 2016)
        self.assertEqual(display_status(dict(AUTO,resolution='beyond-balanced'), mode75)['stream_width'], 2560)
        self.assertEqual(stream_width('Hevc','beyond-detail',mode75), 3200)
        self.assertEqual(stream_width('Hevc','beyond-detail',BEYOND), 3200)
        self.assertEqual(stream_width('Hevc','recommended',mode75), 3552)
        doc=json.loads((ROOT/'host/default-session.json').read_text())
        host=configure(doc,codec='Hevc',resolution='beyond-balanced',headset=mode75)['session_settings']
        self.assertEqual(host['video']['transcoding_view_resolution']['Absolute']['width'],2560)
        self.assertFalse(host['video']['transcoding_view_resolution']['Absolute']['height']['set'])

    def test_beyond_stream_budget_does_not_inherit_client_gpu_auto_scale(self):
        scaled = dict(BEYOND, width=1344, height=1344)
        for preferences, expected in ((AUTO, 2016), (dict(AUTO,resolution='beyond-balanced'),2560),
                                      (dict(AUTO,resolution='beyond-detail'),3200)):
            selected=resolve_stream_preferences(preferences,scaled)
            self.assertEqual(stream_width(selected['codec'],selected['resolution'],scaled),expected)
            doc=json.loads((ROOT/'host/default-session.json').read_text())
            settings=configure(doc,codec=selected['codec'],resolution=selected['resolution'],headset=scaled)['session_settings']
            self.assertEqual(settings['video']['transcoding_view_resolution']['Absolute']['width'],expected)
            self.assertFalse(settings['video']['transcoding_view_resolution']['Absolute']['height']['set'])
        # An explicit application-recommendation choice remains exactly that.
        self.assertEqual(stream_width('Hevc','recommended',scaled),1344)
        self.assertEqual(display_status(AUTO,scaled)['recommended_width'],1344)

    def test_explicit_choices_are_not_overridden_by_auto_codec_or_resolution(self):
        value={'codec':'H264','bitrate':120,'resolution':'recommended'}
        result=resolve_stream_preferences(value,BEYOND)
        self.assertEqual((result['codec'],result['resolution'],result['bitrate']),('H264','recommended',120))
        self.assertEqual(stream_width(result['codec'],result['resolution'],BEYOND),2048)
        result=resolve_stream_preferences(dict(AUTO,codec='H264'),BEYOND)
        self.assertEqual((result['codec'],result['resolution']),('H264','sharp'))
        for preset in ('beyond-balanced','beyond-detail'):
            with self.assertRaisesRegex(ValueError,'HEVC'):
                client_stream_preferences(dict(AUTO,codec='H264',resolution=preset))
            with self.assertRaisesRegex(ValueError,'HEVC'):
                stream_width('H264',preset,BEYOND)
            with self.assertRaisesRegex(ValueError,'detected Index'):
                resolve_stream_preferences(dict(AUTO,resolution=preset),HEADSET)

    def test_auto_does_not_guess_device_version_or_missing_hardware(self):
        generic=dict(BEYOND,model='Beyond')
        self.assertEqual(headset_family(generic),'beyond')
        self.assertEqual(display_status(AUTO,generic)['model'],'Beyond')
        self.assertFalse(display_status(AUTO,None)['detected'])
        with self.assertRaises(ValueError):resolve_stream_preferences(AUTO,None)
        unknown=dict(HEADSET,model='Other headset',manufacturer='Other')
        result=resolve_stream_preferences(AUTO,unknown)
        self.assertEqual((result['codec'],result['resolution']),('H264','compatibility'))

    def test_game_mode_uses_same_options_and_does_not_trap_codec_cycle(self):
        view=View(ROOT,lambda:{},Mock(),lambda:{},threading.Event())
        view.action=Mock();view.state={'settings':dict(AUTO,resolution='beyond-balanced')}
        view.choose('codec');view.action.assert_called_with('client-preferences',codec='Hevc')
        view.state['settings']['codec']='Hevc'
        view.choose('codec');view.action.assert_called_with('client-preferences',codec='AV1')
        view.state['settings']['codec']='AV1'
        view.choose('codec');view.action.assert_called_with('client-preferences',codec='Auto')
        view.state['client_runtime']={'headset':BEYOND}
        view.state['settings']={'codec':'H264','resolution':'sharp'}
        view.choose('resolution');view.action.assert_called_with('client-preferences',resolution='beyond-balanced',codec='Auto')
        view.choose('stream');self.assertEqual(view.page,'stream')
