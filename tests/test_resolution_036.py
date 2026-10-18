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
OTHER = dict(HEADSET, model='Generic HMD', manufacturer='Example')


class ResolutionPresentationTests(unittest.TestCase):
    def test_index_native_modes_keep_the_sharper_pixels_and_wire_id(self):
        # 1.0.0.13: the Index offers Native (2016 x 2240, its old High) at each of its refresh rates; on the wire it is
        # still 'sharp', so hosts before 1.0.0.13 stream it unchanged. A saved High shows as Auto, at the same size.
        prefs = {'codec':'H264', 'resolution':'sharp', 'bitrate':80}
        info = display_status(prefs, HEADSET)
        options = info['resolution_options']['H264']
        self.assertEqual([o['id'] for o in options], ['automatic','index-80','index-90','index-120','index-144'])
        self.assertEqual(info['selected'], 'automatic')
        native = next(o for o in options if o['id'] == 'index-120')
        self.assertEqual(native['label'], 'Native · 120 Hz - 2016 x 2240 per eye')
        self.assertEqual((native['encoded_width'],native['encoded_height'],native['refresh_hz']), (4032,2240,120))
        self.assertTrue(next(o for o in options if o['id'] == 'index-144')['experimental'])
        self.assertEqual((info['stream_width'],info['stream_height']), (2016,2240))
        for choice in ('sharp', 'automatic', 'index-80', 'index-144'):
            self.assertEqual(resolve_stream_preferences(dict(prefs, resolution=choice), HEADSET)['resolution'], 'sharp')
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
                if hmd is BEYOND:  # 1.0.0.13: one entry, the mode it is in: nothing to cycle
                    self.assertEqual(len(view.stream_options()),1)
                    view.action.assert_not_called()
                else:
                    view.action.assert_called_with('client-preferences',resolution=view.stream_options()[1]['id'])

    def test_legacy_hevc_size_is_not_mislabelled_standard_below_high(self):
        prefs = {'codec':'Hevc','resolution':'compatibility','bitrate':120}
        options=resolution_options(prefs,OTHER)
        high=next(o for o in options if o['id']=='sharp')
        custom=next(o for o in options if o['id']=='compatibility')
        self.assertEqual(custom['width'],2144)
        self.assertEqual(high['width'],2016)
        self.assertTrue(custom['label'].startswith('Custom'))
        self.assertEqual(stream_width('Hevc','compatibility',HEADSET),2144)

    def test_beyond_sizes_are_model_aware_and_ignore_client_gpu_scaling(self):
        # 1.0.0.13: the Beyond offers no choice: one entry, the mode it is in (2560 at 75 Hz; 1920 at 90 Hz or with
        # H.264), whatever SteamVR recommends rendering on this PC.
        for codec,hz,size in (('Hevc',75.0,2560),('Hevc',90.0,1920),('H264',75.0,1920)):
            options=resolution_options({'codec':codec,'resolution':'beyond-balanced'},dict(BEYOND,refresh_hz=hz))
            self.assertEqual([o['id'] for o in options],['automatic'])
            self.assertEqual((options[0]['width'],options[0]['height']),(size,size))
            self.assertEqual(options[0]['label'],'Set by the headset · %d Hz - %d x %d per eye'%(hz,size,size))
        self.assertTrue(display_status({'codec':'Auto','resolution':'automatic'},BEYOND)['resolution_fixed'])
        self.assertFalse(display_status({'codec':'Auto','resolution':'automatic'},HEADSET)['resolution_fixed'])

    def test_no_guessed_dimensions_before_headset_detection(self):
        # 1.0.0.13: with no headset known at all, the sizes can still be chosen; their dimensions come on Start.
        for headset in (None,{},dict(HEADSET,width=0)):
            info=display_status({'codec':'H264','resolution':'sharp'},headset)
            self.assertFalse(info['detected'])
            opts=info['resolution_options']['H264']
            self.assertEqual([o['id'] for o in opts],['automatic','compatibility','sharp','recommended'])
            self.assertTrue(all(o['available'] for o in opts))
            self.assertTrue(all('width' not in o for o in opts))
            self.assertTrue(all('2016' not in o['label'] for o in opts))

    def test_model_change_keeps_the_saved_choice_for_its_headset(self):
        # 1.0.0.13: a choice the connected headset does not offer follows its mode, as Auto does, and stays saved for
        # the headset it was made for (until 1.0.0.12 a Start was refused until another was chosen).
        prefs={'codec':'Auto','resolution':'index-120'}
        for hmd in (BEYOND,OTHER):
            info=display_status(prefs,hmd)
            self.assertEqual(info['selected'],'automatic')
            self.assertNotIn('message',info)
            self.assertNotIn('index-120',[o['id'] for o in info['resolution_options']['Auto']])
        self.assertEqual(prefs['resolution'],'index-120')
        self.assertEqual(display_status(prefs,HEADSET)['selected'],'index-120')
        info=display_status({'codec':'Auto','resolution':'beyond-detail'},HEADSET)
        self.assertEqual((info['selected'],info['stream_width']),('automatic',2016))

    def test_requested_height_keeps_runtime_aspect_and_alignment(self):
        self.assertEqual(stream_size('H264','compatibility',HEADSET),(1440,1600))
        self.assertEqual(stream_size('Hevc','compatibility',HEADSET),(2144,2368))
        scaled=dict(HEADSET,width=3600,height=4000)
        self.assertEqual(stream_size('H264','recommended',scaled),(2048,2272))
        self.assertEqual(stream_size('Hevc','recommended',scaled),(3584,3968))
