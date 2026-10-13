"""Quality UI retains legacy stream budgets and labels the negotiated target."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from headset_fixture import HEADSET
from launcher.headset import display_status, resolution_options, resolve_stream_preferences, stream_size, stream_width
from launcher.game_ui import View

ROOT = Path(__file__).resolve().parents[1]
BEYOND = dict(HEADSET, model='Bigscreen Beyond 2e', manufacturer='Bigscreen', width=1344, height=1344)


class ResolutionPresentationTests(unittest.TestCase):
    def test_index_high_preserves_sharper_pixels_and_wire_id(self):
        prefs = {'codec':'H264', 'resolution':'sharp', 'bitrate':80}
        info = display_status(prefs, HEADSET)
        options = info['resolution_options']['H264']
        self.assertEqual([o['id'] for o in options], ['automatic','compatibility','sharp','recommended'])
        high = next(o for o in options if o['id'] == 'sharp')
        self.assertEqual(high['label'], 'High - 2016 x 2240 per eye')
        self.assertEqual((high['encoded_width'],high['encoded_height']), (4032,2240))
        self.assertEqual((info['stream_width'],info['stream_height']), (2016,2240))
        self.assertEqual(resolve_stream_preferences(prefs, HEADSET)['resolution'], 'sharp')
        self.assertEqual(prefs, {'codec':'H264','resolution':'sharp','bitrate':80})

    def test_same_catalog_used_by_game_mode_and_desktop_status(self):
        for hmd in (HEADSET, BEYOND):
            for codec in ('Auto','H264','Hevc'):
                prefs = {'codec':codec,'resolution':'automatic','bitrate':80}
                display = display_status(prefs,hmd)
                view = View(ROOT,lambda:{},Mock(),lambda:{},threading.Event())
                view.state = {'settings':prefs,'display':display}
                self.assertEqual(view.stream_options(),display['resolution_options'][codec])
                view.action=Mock();view.choose('resolution')
                expected=view.stream_options()[1]['id']
                view.action.assert_called_with('client-preferences',resolution=expected)

    def test_legacy_hevc_size_is_not_mislabelled_standard_below_high(self):
        prefs = {'codec':'Hevc','resolution':'compatibility','bitrate':120}
        options=resolution_options(prefs,HEADSET)
        high=next(o for o in options if o['id']=='sharp')
        custom=next(o for o in options if o['id']=='compatibility')
        self.assertEqual(custom['width'],2144)
        self.assertEqual(high['width'],2016)
        self.assertTrue(custom['label'].startswith('Custom'))
        self.assertEqual(stream_width('Hevc','compatibility',HEADSET),2144)

    def test_beyond_sizes_are_model_aware_and_ignore_client_gpu_scaling(self):
        options=resolution_options({'codec':'H264','resolution':'automatic'},BEYOND)
        self.assertEqual([o['id'] for o in options],['automatic','sharp','beyond-balanced','beyond-detail','recommended'])
        high=next(o for o in options if o['id']=='beyond-balanced')
        self.assertEqual((high['width'],high['height'],high['requires_codec']),(2560,2560,'Hevc'))
        self.assertTrue(high['label'].startswith('Native'))  # 1.0.0.11: the panels' own size (High before)
        self.assertEqual(next(o for o in options if o['id']=='recommended')['width'],1344)

    def test_no_guessed_dimensions_before_headset_detection(self):
        for headset in (None,{},dict(HEADSET,width=0)):
            info=display_status({'codec':'H264','resolution':'sharp'},headset)
            self.assertFalse(info['detected'])
            opts=info['resolution_options']['H264']
            self.assertEqual([o['id'] for o in opts],['automatic','sharp'])
            self.assertTrue(all('width' not in o for o in opts))
            self.assertTrue(all('2016' not in o['label'] for o in opts))

    def test_model_change_keeps_incompatible_saved_choice_explicit(self):
        info=display_status({'codec':'Auto','resolution':'beyond-detail'},HEADSET)
        saved=next(o for o in info['resolution_options']['Auto'] if o['id']=='beyond-detail')
        self.assertFalse(saved['available'])
        self.assertIn('different headset',saved['label'])
        self.assertIn('detected Index',info['message'])

    def test_requested_height_keeps_runtime_aspect_and_alignment(self):
        self.assertEqual(stream_size('H264','compatibility',HEADSET),(1440,1600))
        self.assertEqual(stream_size('Hevc','compatibility',HEADSET),(2144,2368))
        scaled=dict(HEADSET,width=3600,height=4000)
        self.assertEqual(stream_size('H264','recommended',scaled),(2048,2272))
        self.assertEqual(stream_size('Hevc','recommended',scaled),(3584,3968))
