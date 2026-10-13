"""VROSTBridge in Steam Game Mode: controller-friendly controls drawn with SDL2.

RC4.2: the screen is drawn at the display's own resolution (no fixed 1280 x 800 canvas
scaled up), with the Inter and Outfit fonts rasterised by libvrosttext.so (stb_truetype),
the brand background and logo, rounded cards and a cyan-to-magenta accent, like
the desktop pages. Every element is rendered once into a texture and cached, so
navigation stays instant. D-pad, stick, keyboard, mouse and touch all work; focus
moves to the nearest control in the pressed direction.

RC4.3: every setting of the desktop page, on scrolling pages (Stream & headset, Motion &
timing, Network & host, This PC) whose focused row explains itself; B goes up one level
and never closes the app (Exit VROSTBridge does); Start stays available and the session
card says why the headset display is not reserved instead of greying Start out.

1.0.0.4, controller movement: on a settings list, up and down step through the rows and
left and right change the focused row's value (switches: left off, right on) instead of
jumping to Back; the D-pad, arrow keys and the stick repeat while held (after 0.38 s,
then about 8 steps a second); one press seen twice (Steam's virtual controller and the
physical one, or a button and a key) moves once; rows scrolled out of view are reached
only by stepping through the list; lists stop at their ends instead of wrapping to Back.
"""
import ctypes as c
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from launcher.game_mode import in_game_mode
from launcher.headset import CLIENT_CODECS, FOVEATION_CHOICES, FOVEATION_LABELS, resolution_options
from launcher.headset import (FOVEATION_EDGES, FOVEATION_EDGE_LABELS, FOVEATION_REGIONS, FOVEATION_REGION_LABELS,
                              SHARPENING_LABELS, SHARPENING_LEVELS)
from launcher.version import CLIENT_VERSION

CODEC_LABELS = {'Auto': 'Auto', 'H264': 'H.264', 'Hevc': 'HEVC', 'AV1': 'AV1'}
BITRATES = (80, 120, 160, 200, 250)
# RC4.3: every setting of the desktop page (launcher/app.py validates the same values).
PAGES = ('stream', 'motion', 'network', 'pc', 'host', 'reports', 'about', 'eyes', 'eyetrack')
# RC4.3: sharpening (applies at once) and the eye-tracked region and outside reduction.
SHARPENING = tuple(SHARPENING_LEVELS)
REGIONS = tuple(FOVEATION_REGIONS)
EDGES = tuple(FOVEATION_EDGES)
FRACTIONS = {2: '½', 3: '⅓', 4: '¼', 5: '⅕', 6: '⅙', 8: '⅛'}
# 1.0.0.4: the bundled fonts carry ½ and ¼ (Latin-1) but not the other vulgar fractions, which the screen drew as
# '?' ("Lightest · ? resolution"): those are written out instead, wherever text is drawn.
GLYPH_FALLBACK = str.maketrans({'⅓': '1/3', '⅔': '2/3', '⅕': '1/5', '⅙': '1/6', '⅛': '1/8'})
ENCODERS = ('speed', 'balanced')
ENCODER_LABELS = {'speed': 'Lowest latency', 'balanced': 'Balanced quality'}
KEEP_WARM = (0, 5, 15, 30, 60)
KEEP_WARM_LABELS = {0: 'Off', 5: '5 min', 15: '15 min', 30: '30 min', 60: '60 min'}
# 1.0.0.4: held D-pad, arrow keys and stick repeat; a second report of the same press is ignored.
REPEAT_DELAY_S, REPEAT_S, STICK_REPEAT_S, DUPLICATE_S = .38, .11, .14, .07
STICK_ON, STICK_OFF = 18000, 11000  # stick travel (of 32767) that moves, and below which it is released
DPAD = {11: (0, -1), 12: (0, 1), 13: (-1, 0), 14: (1, 0)}
ARROWS = {1073741906: (0, -1), 1073741905: (0, 1), 1073741904: (-1, 0), 1073741903: (1, 0)}
# Rows whose value left and right step through (choose(name, step)); switches are set by direction.
CYCLED = ('quality', 'foveation', 'codec', 'resolution', 'encoder', 'keep-warm', 'sharpening', 'fov-region', 'fov-edge',
          'adapter')
SWITCHES = {'ten-bit': 'ten_bit', 'reaim': 'reaim'}
SLOW_GAZE_RATE = 30  # 1.0.0.4: fewer eye-tracking samples a second than this and the sharp region lags the eyes


def gaze_reach_text(reach):
    """1.0.0.6: the eye-tracking view's 'Gaze reach' row: how far the gaze went each way this session, in degrees."""
    if not isinstance(reach, dict) or not all(isinstance(reach.get(way), int) for way in ('left', 'right', 'up', 'down')):
        return '—'
    return '%d° ← · %d° → · %d° ↑ · %d° ↓' % (reach['left'], reach['right'], reach['up'], reach['down'])


def _side(value, positive, negative):
    return '%d° %s' % (abs(int(round(value))), positive if value >= 0 else negative)


def centre_text(centre, running):
    """1.0.0.8: the gaze centring in words, as on the desktop page's Eye tracking card ('' when there is nothing)."""
    if not isinstance(centre, dict):
        return ''
    parts = []
    middle = centre.get('middle')
    if running and isinstance(middle, (list, tuple)) and len(middle) == 2:
        parts.append('Your gaze rests ' + _side(middle[0], 'right', 'left') + ' and ' + _side(middle[1], 'up', 'down') +
                     ' of straight ahead this session.')
    yaw, pitch = centre.get('yaw') or 0, centre.get('pitch') or 0
    if abs(yaw) >= 0.5 or abs(pitch) >= 0.5:
        parts.append(('Centring is off; it would take ' if centre.get('enabled') is False else 'Centring takes ') +
                     _side(yaw, 'right', 'left') + ' and ' + _side(pitch, 'up', 'down') + ' off this calibration.')
    if centre.get('off_centre'):
        parts.append('This calibration looks off-centre: calibrate again, or go back to your previous calibration.')
    return ' '.join(parts)


def bitrate_note(rate, running):
    """1.0.0.8: the running stream's bitrate as this PC steers it ('' when not streaming on Auto)."""
    if not running or not isinstance(rate, dict) or not rate.get('active') or not rate.get('mbps'):
        return ''
    text = ('Now ' + str(rate['mbps']) + ' Mbit/s; the most for this picture is ' + str(rate.get('ceiling')) + ' Mbit/s.')
    if rate.get('reason'):
        text += ' Last change: ' + str(rate['reason']) + '.'
    return text


# RC4.4/RC4.5: why Eye-tracked does not follow the eyes (system.foveation.eye_state from the companion):
# (title, value, description). The row opens the Eye tracking page.
EYE_NOTES = {
    'off-here': ('Eye tracking is off', 'Balanced in use',
                 'Eye-tracked needs Eye tracking on (This PC → Eye tracking). Until then the fixed Balanced region is '
                 'used.'),
    'setup': ('Eye tracking is not set up', 'Balanced in use',
              'Set up eye tracking once (This PC → Eye tracking): it downloads Baballonia. Until then the fixed '
              'Balanced region is used.'),
    'calibrate': ('Eye tracking needs calibrating', 'Balanced in use',
                  'Calibrate Baballonia once in Desktop Mode, during a session (its window: the Eye calibration). '
                  'Until then the fixed Balanced region is used.'),
    'restart': ('Start again for eye tracking', 'region centred',
                'Eye tracking was not ready when this session started: stop and start the session for the sharp '
                'region to follow your eyes.'),
    'no-cameras': ('No eye cameras on this headset', 'Balanced in use',
                   'Eye-tracked needs a Bigscreen Beyond 2e. With this headset the fixed Balanced region is used.'),
    'starting': ('Eye tracking is starting', 'region centred',
                 'The eye cameras, then Baballonia. The sharp region follows your eyes once they run.'),
    'cameras': ('The eye cameras need attention', 'region centred', ''),  # the companion's message
    'closed': ('Baballonia was closed', 'region centred',
               'Open Baballonia again on the Eye tracking page (This PC → Eye tracking).'),
    'eye-error': ('Eye tracking stopped working', 'region centred', ''),
    'host-off': ('The host keeps the region centred', 'update host',
                 'Update VROSTBridge on the host PC: RC4 and later move the sharp region with your eyes.'),
    'no-gaze': ('No eye tracking reaching the host', 'region centred',
                'The host follows your eyes, but no eye tracking arrives. Check the Eye tracking page.'),
    'cpu': ('Foveated encoding is off', 'CPU decoding',
            'CPU decoding cannot undo foveated encoding, so it is off. Turn off CPU decoding (This PC) to use '
            'Eye-tracked.'),
}

TOGGLES = ('microphone', 'gaze', 'software', 'late_frame_catchup', 'motion_prediction', 'timing_lock', 'intra_refresh',
           'pacing', 'keep_awake', 'headset_audio', 'wake_host', 'headset_hud', 'pause_other_wifi', 'stay_on_access_point',
           'one_pass', 'lens_area')
DEFAULTS = {'microphone': False, 'gaze': False, 'software': False, 'late_frame_catchup': True, 'motion_prediction': True,
            'timing_lock': True, 'intra_refresh': False, 'pacing': True, 'keep_awake': True, 'headset_audio': True,
            'wake_host': True, 'headset_hud': False, 'pause_other_wifi': True, 'stay_on_access_point': True, 'reaim': True,
            'one_pass': True, 'balance_performance': True, 'base_stations_off': True, 'gaze_centre': True,
            'lens_area': True, 'keep_frame_rate': True}
# 1.0.0.8: switches saved and applied at once (the companion's client-option action), also during a session.
LIVE_TOGGLES = ('balance_performance', 'base_stations_off', 'gaze_centre', 'keep_frame_rate')
# Palette (straight RGBA, 0xRRGGBBAA): the desktop pages' tokens.
TEXT, MUTED, FAINT = 0xF3F5FBFF, 0xA3ABBFFF, 0x727A92FF
# 1.0.0.3: the one-line wordmark (launcher/brand/vrostbridge-name.png): VROST's capital height and top as fractions of
# the picture's height (brand/tools/vrostbridge_symbol.py inline_metrics(); a test keeps them equal).
NAME_CAP, NAME_CAP_TOP = 0.7441, 0.0447
# 1.0.0: VROSTBridge's colours (brand/brand.json): cyan, blue, purple, magenta; BUTTON_* keep white text readable.
CYAN, BLUE, PURPLE, MAGENTA = 0x00E5FFFF, 0x0085FFFF, 0xA855FFFF, 0xFF2DCBFF
BUTTON_FOCUSED, BUTTON_IDLE = (0x0085FFFF, 0xA855FFFF), (0x0062BDF0, 0x7C3FBDF0)
OK, BAD = 0x62D6B4FF, 0xFF8FB6FF
# Under the name, as on the pages: VR Rendering OutSide The Box – Bridge. V, R (of Rendering), O, S, T and B (of Box)
# are picked out in the palette's colours, and every letter of Bridge follows the brand gradient (cyan to magenta).
BRIDGE = (0x00E5FFFF, 0x00ABFFFF, 0x227BFFFF, 0x865FFFFF, 0xCB45EAFF, 0xFF2DCBFF)
TAGLINE = (('V', CYAN, 'strong'), ('R ', MUTED, 'body'), ('R', CYAN, 'strong'), ('endering ', MUTED, 'body'),
           ('O', BLUE, 'strong'), ('ut', MUTED, 'body'), ('S', PURPLE, 'strong'), ('ide ', MUTED, 'body'),
           ('T', MAGENTA, 'strong'), ('he ', MUTED, 'body'), ('B', MAGENTA, 'strong'), ('ox \u2013 ', MUTED, 'body'),
           *((letter, colour, 'strong') for letter, colour in zip('Bridge', BRIDGE)))
REFERENCE = (1280, 800)  # layout units; scaled to the screen


class Rect(c.Structure):
    _fields_ = [('x', c.c_int), ('y', c.c_int), ('w', c.c_int), ('h', c.c_int)]


class SDL:
    def __init__(self, root):
        paths = [root / 'lib/libSDL2-2.0.so.0', Path('/usr/lib64/libSDL2-2.0.so.0'),
                 Path('/usr/lib/x86_64-linux-gnu/libSDL2-2.0.so.0'), Path('/usr/lib/libSDL2-2.0.so.0')]
        self.lib = c.CDLL(str(next((p for p in paths if p.exists()), paths[1])))
        signatures = {
            'Init': (c.c_int, [c.c_uint32]), 'Quit': (None, []), 'GetError': (c.c_char_p, []),
            'SetHint': (c.c_int, [c.c_char_p, c.c_char_p]),
            'CreateWindow': (c.c_void_p, [c.c_char_p, c.c_int, c.c_int, c.c_int, c.c_int, c.c_uint32]),
            'DestroyWindow': (None, [c.c_void_p]),
            'CreateRenderer': (c.c_void_p, [c.c_void_p, c.c_int, c.c_uint32]), 'DestroyRenderer': (None, [c.c_void_p]),
            'GetRendererOutputSize': (c.c_int, [c.c_void_p, c.POINTER(c.c_int), c.POINTER(c.c_int)]),
            'GetWindowSize': (None, [c.c_void_p, c.POINTER(c.c_int), c.POINTER(c.c_int)]),
            'SetRenderDrawColor': (c.c_int, [c.c_void_p, c.c_ubyte, c.c_ubyte, c.c_ubyte, c.c_ubyte]),
            'SetRenderDrawBlendMode': (c.c_int, [c.c_void_p, c.c_int]),
            'RenderClear': (c.c_int, [c.c_void_p]), 'RenderFillRect': (c.c_int, [c.c_void_p, c.POINTER(Rect)]),
            'RenderFillRects': (c.c_int, [c.c_void_p, c.POINTER(Rect), c.c_int]),
            'RenderPresent': (None, [c.c_void_p]),
            'CreateTexture': (c.c_void_p, [c.c_void_p, c.c_uint32, c.c_int, c.c_int, c.c_int]),
            'UpdateTexture': (c.c_int, [c.c_void_p, c.POINTER(Rect), c.c_void_p, c.c_int]),
            'SetTextureBlendMode': (c.c_int, [c.c_void_p, c.c_int]),
            'RenderCopy': (c.c_int, [c.c_void_p, c.c_void_p, c.POINTER(Rect), c.POINTER(Rect)]),
            'DestroyTexture': (None, [c.c_void_p]), 'PollEvent': (c.c_int, [c.c_void_p]),
            'RenderReadPixels': (c.c_int, [c.c_void_p, c.POINTER(Rect), c.c_uint32, c.c_void_p, c.c_int]),
            'RenderSetClipRect': (c.c_int, [c.c_void_p, c.POINTER(Rect)]),
            'NumJoysticks': (c.c_int, []), 'IsGameController': (c.c_int, [c.c_int]),
            'GameControllerOpen': (c.c_void_p, [c.c_int]), 'GameControllerClose': (None, [c.c_void_p]),
            'GameControllerGetAxis': (c.c_int16, [c.c_void_p, c.c_int]), 'Delay': (None, [c.c_uint32]),
            'JoystickGetDeviceInstanceID': (c.c_int32, [c.c_int]),
            'GameControllerGetAttached': (c.c_int, [c.c_void_p]),
            'GameControllerGetButton': (c.c_uint8, [c.c_void_p, c.c_int])}
        for name, (ret, args) in signatures.items():
            function = getattr(self.lib, 'SDL_' + name)
            function.restype, function.argtypes = ret, args
            setattr(self, name, function)

    def error(self):
        return (self.GetError() or b'').decode(errors='replace')


class Ink:
    """libvrosttext.so: fonts, rounded shapes and images rendered to straight RGBA."""

    def __init__(self, root):
        candidates = [root / 'libvrosttext.so', root / 'lib/libvrosttext.so', root / 'dist/client/libvrosttext.so']
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise RuntimeError('The VROSTBridge text library (libvrosttext.so) is missing. Repair or reinstall VROSTBridge.')
        lib = self.lib = c.CDLL(str(path))
        f, i, u, p, s = c.c_float, c.c_int, c.c_uint32, c.c_void_p, c.c_char_p
        signatures = {
            'version': (i, []), 'font_open': (p, [s]), 'font_close': (None, [p]),
            'measure': (f, [p, s, f, f]), 'metrics': (None, [p, f, c.POINTER(f)]),
            'wrap': (i, [p, s, f, f, i, c.c_char_p, i]), 'ellipsize': (i, [p, s, f, f, c.c_char_p, i]),
            'canvas': (p, [i, i]), 'canvas_free': (None, [p]), 'clear': (None, [p, u]),
            'draw': (f, [p, p, s, f, f, f, u, f]), 'rounded_rect': (None, [p, f, f, f, f, f, u]),
            'gradient_rect': (None, [p, f, f, f, f, f, u, u, i]), 'accent_rect': (None, [p, f, f, f, f, f, f, f]),
            'stroke_rect': (None, [p, f, f, f, f, f, f, u]), 'circle': (None, [p, f, f, f, u]),
            'image': (None, [p, c.c_char_p, i, i, f, f, i, i, f]), 'pixels': (c.POINTER(c.c_ubyte), [p]),
            'load_image': (c.POINTER(c.c_ubyte), [s, i, i, c.POINTER(i), c.POINTER(i)]),
            'free_pixels': (None, [c.POINTER(c.c_ubyte)])}
        for name, (ret, args) in signatures.items():
            function = getattr(lib, 'vrosttext_' + name)
            function.restype, function.argtypes = ret, args
            setattr(self, name, function)
        fonts = next((d for d in (root / 'assets/fonts', root / 'launcher/fonts', Path(__file__).with_name('fonts'))
                      if (d / 'Inter-Regular.ttf').is_file()), None)
        if fonts is None:
            raise RuntimeError('The VROSTBridge fonts are missing. Repair or reinstall VROSTBridge.')
        self.fonts = {}
        for key, name in (('heading', 'Outfit-SemiBold.ttf'), ('body', 'Inter-Regular.ttf'),
                          ('medium', 'Inter-Medium.ttf'), ('strong', 'Inter-SemiBold.ttf')):
            handle = self.font_open(str(fonts / name).encode())
            if not handle:
                raise RuntimeError('Font could not be loaded: ' + name)
            self.fonts[key] = handle

    def close(self):
        for handle in self.fonts.values():
            self.font_close(handle)
        self.fonts = {}

    def width(self, font, text, size, tracking=0.0):
        return self.measure(self.fonts[font], text.encode(), size, tracking)

    def lines(self, font, text, size, width, count):
        out = c.create_string_buffer(8192)
        self.wrap(self.fonts[font], text.encode(), size, width, count, out, len(out))
        return out.value.decode(errors='replace').split('\n') if out.value else []

    def fit(self, font, text, size, width):
        out = c.create_string_buffer(4096)
        self.ellipsize(self.fonts[font], text.encode(), size, width, out, len(out))
        return out.value.decode(errors='replace')

    def ascent(self, font, size):
        values = (c.c_float * 4)()
        self.metrics(self.fonts[font], size, values)
        return values[0], values[1], values[2]


class Painter:
    """Cached textures for text runs, panels and images, drawn at screen pixels."""

    def __init__(self, sdl, renderer, ink):
        self.sdl, self.renderer, self.ink = sdl, renderer, ink
        self.cache = OrderedDict()

    def _texture(self, pixels, w, h):
        texture = self.sdl.CreateTexture(self.renderer, 0x16762004, 0, w, h)  # ABGR8888 = R,G,B,A bytes
        if not texture:
            raise RuntimeError(self.sdl.error())
        self.sdl.UpdateTexture(texture, None, pixels, w * 4)
        self.sdl.SetTextureBlendMode(texture, 1)
        return texture

    def _cached(self, key, make):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        value = make()
        self.cache[key] = value
        while len(self.cache) > 500:
            _, (texture, *_rest) = self.cache.popitem(last=False)
            if texture:
                self.sdl.DestroyTexture(texture)
        return value

    def _canvas(self, w, h, draw):
        canvas = self.ink.canvas(w, h)
        try:
            draw(canvas)
            data = c.string_at(self.ink.pixels(canvas), w * h * 4)
        finally:
            self.ink.canvas_free(canvas)
        return self._texture(data, w, h)

    def blit(self, texture, x, y, w, h, src=None):
        self.sdl.RenderCopy(self.renderer, texture, c.byref(src) if src else None, c.byref(Rect(int(x), int(y), int(w), int(h))))

    def text(self, value, font, size, color, x, baseline, align='left', tracking=0.0):
        """Draws one line with its baseline at y; returns its width."""
        value = str(value).translate(GLYPH_FALLBACK)
        if not value:
            return 0

        def make():
            width = self.ink.width(font, value, size, tracking)
            ascent, descent, _ = self.ink.ascent(font, size)
            pad = 3
            w, h = max(1, int(width + 2 * pad + 1)), max(1, int(ascent - descent + 2 * pad + 1))
            top = pad + int(ascent + .999)
            texture = self._canvas(w, h, lambda canvas: self.ink.draw(canvas, self.ink.fonts[font], value.encode(),
                                                                      float(pad), float(top), size, color, tracking))
            return texture, w, h, top, pad, width
        texture, w, h, top, pad, width = self._cached(('text', value, font, round(size, 2), color, tracking), make)
        left = x - (width if align == 'right' else width / 2 if align == 'center' else 0)
        self.blit(texture, round(left - pad), round(baseline - top), w, h)
        return width

    def paragraph(self, value, font, size, color, x, baseline, width, count=3, line=1.45):
        y = baseline
        for row in self.ink.lines(font, str(value).translate(GLYPH_FALLBACK), size, width, count):
            self.text(row, font, size, color, x, y)
            y += size * line
        return y

    def panel(self, x, y, w, h, radius, fill=(0x1A1D2AE8, 0x12141CE8), border=0x2B3044FF, border_width=1.5,
              accent=False, glow=0, horizontal=False):
        w, h = int(w), int(h)
        margin = int(glow)

        def make():
            cw, ch = w + 2 * margin, h + 2 * margin

            def draw(canvas):
                if glow:
                    for step in range(margin, 0, -max(1, margin // 6)):
                        alpha = int(30 * (1 - step / (margin + 1)))
                        self.ink.rounded_rect(canvas, margin - step, margin - step, w + 2 * step, h + 2 * step,
                                              radius + step, (CYAN & 0xFFFFFF00) | alpha)
                self.ink.gradient_rect(canvas, margin, margin, w, h, radius, fill[0], fill[1], 0 if horizontal else 1)
                if accent:
                    self.ink.accent_rect(canvas, margin, margin, w, h, radius, margin, max(3.0, h * .02))
                if border_width:
                    self.ink.stroke_rect(canvas, margin, margin, w, h, radius, border_width, border)
            return self._canvas(cw, ch, draw), cw, ch
        texture, cw, ch = self._cached(('panel', w, h, radius, fill, border, border_width, accent, margin, horizontal), make)
        self.blit(texture, x - margin, y - margin, cw, ch)

    def image(self, path, x, y, w, h, cover=False):
        w, h = int(w), int(h)

        def make():
            iw, ih = c.c_int(), c.c_int()
            limit = (0, 0) if cover else (w, h)
            data = self.ink.load_image(str(path).encode(), limit[0], limit[1], c.byref(iw), c.byref(ih))
            if not data:
                return None, 0, 0
            try:
                pixels = c.string_at(data, iw.value * ih.value * 4)
            finally:
                self.ink.free_pixels(data)
            return self._texture(pixels, iw.value, ih.value), iw.value, ih.value
        texture, iw, ih = self._cached(('image', str(path), w if not cover else 0, h if not cover else 0), make)
        if not texture:
            return False
        if cover:  # crop to the target aspect, centred
            scale = max(w / iw, h / ih)
            sw, sh = int(w / scale), int(h / scale)
            self.blit(texture, x, y, w, h, Rect((iw - sw) // 2, (ih - sh) // 2, sw, sh))
        else:
            self.blit(texture, x, y, w, h)
        return True

    def circle(self, cx, cy, radius, color):
        size = int(radius * 2 + 4)

        def make():
            return self._canvas(size, size, lambda canvas: self.ink.circle(canvas, size / 2, size / 2, radius, color)), size, size
        texture, _, _ = self._cached(('circle', round(radius, 1), color), make)
        self.blit(texture, round(cx - size / 2), round(cy - size / 2), size, size)

    def clear(self):
        for texture, *_rest in self.cache.values():
            if texture:
                self.sdl.DestroyTexture(texture)
        self.cache.clear()


class View:
    # Settings pages scroll inside this band (layout units); the title, Back and help sit above it.
    LIST_TOP, LIST_HEIGHT, ROW, ROW_STEP = 236, 420, 60, 70
    # Where B / Escape go from each page. On the main page they do nothing: Exit VROSTBridge
    # closes the app (RC4.2 closed it on B, from the screen people return to most).
    PARENT = {'host': 'network', 'exit': 'main', 'eyes': 'stream'}

    def __init__(self, root, status, action, report, stopped, fovea=None):
        self.root = Path(root)
        self.status_fn, self.action_fn, self.report_fn, self.stopped = status, action, report, stopped
        self.fovea_fn = fovea or (lambda: {'live': False})  # RC4.3 live eye-tracking view (memory only)
        self.state, self.error, self.notice = {}, '', ''
        self.focus, self.page, self.edit, self.pending = 0, os.environ.get('VROSTBRIDGE_GAME_UI_PAGE', 'main'), '', 0
        self.buttons = []
        self.focus_name = None  # the focused control by name: rows that appear or vanish must not move it
        self.scroll = {}
        self.eye_parent = 'pc'
        self.eye_motion = {}  # 1.0.0.3: the eye view's gliding points and gaze trails (memory only)
        self.eye_cells = {}  # squares drawn for the eye view, by geometry
        self.name_art, self.name_aspect = None, 10.09  # 1.0.0.3: the name in the logo's lettering (set in run)
        self.holds = {}  # 1.0.0.4: held directions -> {'sources': set, 'next': time}
        self.last_move = {}  # direction -> when it last moved (one press reported twice moves once)
        self.stick_direction, self.stick_next = None, 0.0
        self.controllers = {}  # instance id -> SDL game controller
        self.refresh = threading.Event()
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ actions
    def work(self, fn, interrupt=False):
        if self.pending and not interrupt:
            return
        self.pending += 1
        self.error = ''
        self.notice = ''

        def run():
            try:
                fn()
            except Exception as error:
                self.error = str(error)
            finally:
                self.pending -= 1
                self.refresh.set()
        threading.Thread(target=run, daemon=True).start()

    def action(self, action, notice='', **kwargs):
        def run():
            self.action_fn(dict(kwargs, action=action))
            if notice:
                self.notice = notice
        self.work(run, interrupt=action == 'stop')

    def stream_options(self):
        settings = self.state.get('settings', {})
        return (self.state.get('display', {}).get('resolution_options', {}).get(settings.get('codec', 'Auto'))
                or resolution_options(settings, self.state.get('client_runtime', {}).get('headset')))

    def save_report(self):
        from launcher.app import state_directory
        report = self.report_fn()
        name = (report.get('report_info') or {}).get('file_name') or 'VROSTBridge-Client-report.json'
        path = state_directory() / 'reports' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        self.notice = 'Saved ' + name + ' in ' + str(path.parent)

    def go(self, page):
        self.page, self.focus, self.focus_name = page, 0, None

    def back(self):
        """B / Escape: up one level; nothing on the main page."""
        if self.page == 'eyetrack':  # opened from This PC or from Stream & headset's eye-tracking note
            self.go(self.eye_parent if self.eye_parent in ('pc', 'stream') else 'pc')
        elif self.page != 'main':
            self.go(self.PARENT.get(self.page, 'main'))

    def setting(self, key):
        return self.state.get('settings', {}).get(key, DEFAULTS.get(key, False))

    def choose(self, name, step=1):
        """A for the focused control; step -1 (left on a row) takes the previous value of a cycled setting."""
        settings = self.state.get('settings', {})
        step = -1 if step < 0 else 1
        if name == 'exit':
            if self.state.get('running') or self.state.get('busy'):
                self.go('exit')
            else:
                self.stopped.set()
        elif name == 'exit-confirm':
            self.stopped.set()
        elif name == 'back':
            self.back()
        elif name in PAGES:
            if name == 'host':
                self.edit = settings.get('host_ip', '')
            if name == 'eyetrack':
                self.eye_parent = self.page
            self.go(name)
        elif name == 'report':
            self.work(self.save_report)
        elif name == 'send-report':
            # Sent in the background; report_delivery shows progress and the outcome below the buttons.
            self.action('send-report')
        elif name == 'host-save':
            self.action('client-preferences', 'Host address saved.', host_ip=self.edit)
            self.go('network')
        elif name == 'host-auto':
            self.edit = ''
        elif name == 'adapter':
            network = self.state.get('session_link', {}).get('network', {})
            values = [''] + list(dict.fromkeys(a['interface'] for a in network.get('adapters', []) if a.get('interface')))
            current = network.get('interface', '')
            self.action('select-client-network', interface=values[(values.index(current) + step) % len(values)]
                        if current in values else '')
        elif name == 'erase':
            self.edit = self.edit[:-1]
        elif name.startswith('key:'):
            if len(self.edit) < 15:
                self.edit += name[4:]
        elif name == 'quality':  # Auto, then each bitrate, then Auto again (left: the other way round)
            values = ('auto',) + BITRATES
            current = 'auto' if settings.get('auto_bitrate') else settings.get('bitrate', 80)
            if current not in values:  # a bitrate set on the desktop page: the next listed one up or down
                following = (next((v for v in BITRATES if v > current), 'auto') if step > 0 else
                             next((v for v in reversed(BITRATES) if v < current), 'auto'))
            else:
                following = values[(values.index(current) + step) % len(values)]
            self.action('client-preferences', **({'auto_bitrate': True} if following == 'auto' else
                                                 {'auto_bitrate': False, 'bitrate': following}))
        elif name == 'foveation':
            current, values = settings.get('foveation', 'auto'), FOVEATION_CHOICES
            self.action('client-preferences', foveation=values[(values.index(current) + step) % len(values)]
                        if current in values else 'auto')
        elif name == 'ten-bit':
            self.action('client-preferences', ten_bit=not settings.get('ten_bit', True))
        elif name == 'codec':
            current = settings.get('codec', 'Auto')
            values = ('Auto', 'Hevc', 'AV1') if settings.get('resolution', '').startswith('beyond-') else CLIENT_CODECS
            self.action('client-preferences', codec=values[(values.index(current) + step) % len(values)]
                        if current in values else 'Auto')
        elif name == 'resolution':
            values = [option['id'] for option in self.stream_options() if option.get('available', True)]
            current = settings.get('resolution', 'automatic')
            following = values[(values.index(current) + step) % len(values)] if current in values else 'automatic'
            changes = {'resolution': following}
            if following.startswith('beyond-') and settings.get('codec') == 'H264':
                changes['codec'] = 'Auto'
            self.action('client-preferences', **changes)
        elif name == 'encoder':
            current = settings.get('encoder', 'speed')
            self.action('client-preferences', encoder=ENCODERS[(ENCODERS.index(current) + step) % len(ENCODERS)]
                        if current in ENCODERS else 'speed')
        elif name == 'keep-warm':
            current = settings.get('keep_warm_minutes', 0)
            self.action('client-preferences', keep_warm_minutes=KEEP_WARM[(KEEP_WARM.index(current) + step) % len(KEEP_WARM)]
                        if current in KEEP_WARM else 0)
        elif name == 'reaim':
            self.action('set-reaim', enabled=not self.setting('reaim'))
        elif name == 'sharpening':
            current = settings.get('sharpening', 'medium')
            self.action('set-sharpening', level=SHARPENING[(SHARPENING.index(current) + step) % len(SHARPENING)]
                        if current in SHARPENING else 'medium')
        elif name == 'fov-region':
            current = settings.get('fov_region', 'medium')
            self.action('client-preferences', fov_region=REGIONS[(REGIONS.index(current) + step) % len(REGIONS)]
                        if current in REGIONS else 'medium')
        elif name == 'fov-edge':
            current = settings.get('fov_edge', 'balanced')
            self.action('client-preferences', fov_edge=EDGES[(EDGES.index(current) + step) % len(EDGES)]
                        if current in EDGES else 'balanced')
        elif name in ('align-yes', 'align-no'):
            self.action('align-answer', jumped=name == 'align-yes')
        elif name == 'align-check':
            self.action('align-check', 'Check running: watch something that stays still.')
        elif name == 'use-standard':
            smaller = (self.state.get('graphics') or {}).get('smaller') or {}
            self.action('use-standard', (str(smaller['label']) + ' (' + str(smaller['width']) + ' per eye)'
                                         if smaller.get('label') and smaller.get('width') else 'The smaller size')
                        + ' is used from the next Start.')
        elif name == 'network-test':
            self.action('network-test', 'Network test finished.')
        elif name == 'gpu-auto':  # 1.0.0.4: the processor held back by a GPU clock fixed high
            self.action('gpu-auto', 'The GPU clock is automatic again.')
        elif name == 'wake-host':
            self.action('wake-host', 'Wake-up sent.')
        elif name == 'repair-async':
            self.action('repair-async', 'SteamVR reprojection repaired; it applies when SteamVR next starts.')
        elif name == 'wifi-powersave-off':
            self.action('wifi-powersave-off', 'Wi-Fi power saving is off for your home network.')
        elif name == 'sync-playspace':
            self.action('sync-playspace', 'Play area sent to the host.')
        elif name.startswith('info:'):  # a row that only explains (its text shows while it has focus)
            pass
        elif name == 'toggle:gaze':  # RC4.5: saved at once, also during a session (applies at the next Start)
            self.action('eye-tracking', enabled=not self.setting('gaze'))
        elif name == 'eye-setup':
            self.action('eye-setup', 'Setting up Baballonia: downloading it from Project Babble.', accept_licence=True)
        elif name == 'eye-reach-reset':  # 1.0.0.6
            self.action('eye-reach-reset', 'Measuring again: look to the far left, right, up and down in the headset.')
        elif name.startswith('toggle:') and name[7:] in LIVE_TOGGLES:  # 1.0.0.8: applies at once
            self.action('client-option', name=name[7:], enabled=not self.setting(name[7:]))
        elif name in ('base-stations-on', 'base-stations-off'):  # 1.0.0.8: in the background; the row shows the result
            self.action('base-stations', power='on' if name == 'base-stations-on' else 'off')
        elif name == 'recommended-settings':
            self.action('recommended-settings', 'Recommended settings chosen. They apply at your next Start.')
        elif name == 'gaze-centre-reset':
            self.action('gaze-centre-reset', 'Gaze centring starts again for this calibration.')
        elif name == 'eye-previous':
            self.action('eye-calibration', 'The previous calibration is chosen. It applies when eye tracking next starts.',
                        name='previous')
        elif name.startswith('toggle:'):
            key = name[7:]
            if key in TOGGLES:
                self.action('client-preferences', **{key: not self.setting(key)})
        else:
            self.action(name)

    # ------------------------------------------------------------------ focus
    def move(self, dx, dy):
        self._move(dx, dy)
        if self.focus < len(self.buttons):
            self.focus_name = self.buttons[self.focus][0]

    @staticmethod
    def _row(button):
        return len(button) > 5 and bool(button[5])

    @staticmethod
    def _visible(button):
        return button[4] if len(button) > 4 else True

    def _move(self, dx, dy):
        enabled = [i for i, b in enumerate(self.buttons) if b[3]]
        if not enabled:
            return
        if self.focus not in enabled:
            self.focus = enabled[0]
            return
        current = self.buttons[self.focus]
        on_row = self._row(current)
        if dx and not dy and on_row:
            # 1.0.0.4: left and right change a settings row's value; they never jump to Back.
            self.adjust(current[0], dx)
            return
        x, y, w, h = current[2]
        cx, cy = x + w / 2, y + h / 2
        best, score = None, None
        for i in enabled:
            if i == self.focus:
                continue
            button = self.buttons[i]
            # Rows scrolled out of view only by stepping up or down the list (it scrolls to them).
            if not self._visible(button) and not (on_row and dy and self._row(button)):
                continue
            bx, by, bw, bh = button[2]
            ox, oy = bx + bw / 2 - cx, by + bh / 2 - cy
            along = ox * dx + oy * dy
            if along <= 1:
                continue
            across = abs(ox * dy) + abs(oy * dx)
            value = along + across * 2.2
            if score is None or value < score:
                best, score = i, value
        if best is not None:
            self.focus = best
        elif dy and not on_row:  # wrap vertically through the page (a list stops at its ends)
            ordered = sorted((i for i in enabled if self._visible(self.buttons[i])),
                             key=lambda i: (self.buttons[i][2][1], self.buttons[i][2][0]))
            if self.focus in ordered:
                position = ordered.index(self.focus)
                self.focus = ordered[(position + (1 if dy > 0 else -1)) % len(ordered)]

    def adjust(self, name, step):
        """Left or right on a settings row (1.0.0.4): the previous or next value; switches off or on."""
        if name.startswith('toggle:'):
            key = name[7:]
            if (key == 'gaze' or key in TOGGLES or key in LIVE_TOGGLES) and bool(self.setting(key)) != (step > 0):
                self.choose(name)
        elif name in SWITCHES:
            if bool(self.state.get('settings', {}).get(SWITCHES[name], DEFAULTS.get(SWITCHES[name], True))) != (step > 0):
                self.choose(name)
        elif name in CYCLED:
            self.choose(name, step)

    # ------------------------------------------------------------------ controller input (1.0.0.4)
    def press(self, direction, source, now):
        """A D-pad button or arrow key went down: move at once, then repeat while it is held. The same
        press reported twice (Steam's virtual controller and the physical one, or a button and a key)
        moves once."""
        entry = self.holds.get(direction)
        if entry:
            entry['sources'].add(source)
            return
        self.holds[direction] = {'sources': {source}, 'next': now + REPEAT_DELAY_S}
        if now - self.last_move.get(direction, -1.0) >= DUPLICATE_S:
            self.step(direction, now)

    def release(self, direction, source):
        entry = self.holds.get(direction)
        if entry:
            entry['sources'].discard(source)
            if not entry['sources']:
                del self.holds[direction]

    def step(self, direction, now):
        self.last_move[direction] = now
        self.move(*direction)

    def repeat(self, now, held=lambda source: True):
        """Repeat held directions; held(source) tells whether a source is still down. True when one moved."""
        moved = False
        for direction, entry in list(self.holds.items()):
            entry['sources'] = {source for source in entry['sources'] if held(source)}
            if not entry['sources']:
                del self.holds[direction]
            elif now >= entry['next']:
                self.step(direction, now)
                entry['next'] = now + REPEAT_S
                moved = True
        return moved

    def stick(self, x, y, now):
        """The left stick: moves once when pushed past STICK_ON, repeats while held, releases below STICK_OFF.
        A diagonal keeps its first direction until the other axis clearly takes over. True when it moved."""
        along = {(1, 0): x, (-1, 0): -x, (0, 1): y, (0, -1): -y}
        current = self.stick_direction
        if current is not None:
            other = abs(y) if current[0] else abs(x)
            if along[current] < STICK_OFF:
                self.stick_direction = current = None
            elif other > 1.3 * along[current]:
                current = None  # the other axis took over: a new direction
        if current is None:
            if max(abs(x), abs(y)) < STICK_ON:
                self.stick_direction = None
                return False
            direction = ((1 if x > 0 else -1), 0) if abs(x) > abs(y) else (0, (1 if y > 0 else -1))
            self.stick_direction, self.stick_next = direction, now + REPEAT_DELAY_S
            if now - self.last_move.get(direction, -1.0) < DUPLICATE_S:
                return False
            self.step(direction, now)
            return True
        if now >= self.stick_next:
            self.step(current, now)
            self.stick_next = now + STICK_REPEAT_S
            return True
        return False

    def activate(self):
        if self.buttons and self.focus < len(self.buttons) and self.buttons[self.focus][3]:
            self.choose(self.buttons[self.focus][0])

    def hit(self, x, y):
        """Mouse and touch: the visible, enabled control under the pointer (rows scrolled out are skipped)."""
        for i, button in enumerate(self.buttons):
            bx, by, bw, bh = button[2]
            visible = button[4] if len(button) > 4 else True
            if button[3] and visible and bx <= x < bx + bw and by <= y < by + bh:
                return i
        return None

    # ------------------------------------------------------------------ drawing
    def u(self, value):
        return value * self.k

    def setup_scale(self):
        w, h = c.c_int(), c.c_int()
        if self.s.GetRendererOutputSize(self.renderer, c.byref(w), c.byref(h)) or w.value <= 0:
            self.s.GetWindowSize(self.window, c.byref(w), c.byref(h))
        if (w.value, h.value) != getattr(self, 'size', None):
            self.size = (w.value, h.value)
            self.k = min(w.value / REFERENCE[0], h.value / REFERENCE[1])
            self.ox = (w.value - REFERENCE[0] * self.k) / 2
            self.oy = (h.value - REFERENCE[1] * self.k) / 2
            self.paint.clear()

    def X(self, value):
        return self.ox + value * self.k

    def Y(self, value):
        return self.oy + value * self.k

    def label(self, value, x, baseline, size, color=TEXT, font='body', align='left', width=None, tracking=0.0):
        value = str(value)
        if width:
            value = self.ink.fit(font, value, self.u(size), self.u(width))
        return self.paint.text(value, font, self.u(size), color, self.X(x), self.Y(baseline), align, self.u(tracking))

    def button(self, name, title, x, y, w, h, enabled=True, subtitle='', primary=False, value='', center=False,
               visible=True, row=False):
        index = len(self.buttons)
        focused = index == self.focus and enabled
        self.buttons.append((name, title, (self.X(x), self.Y(y), self.u(w), self.u(h)), enabled, visible, row))
        radius = self.u(16)
        if primary and enabled:
            fill = BUTTON_FOCUSED if focused else BUTTON_IDLE
            self.paint.panel(self.X(x), self.Y(y), self.u(w), self.u(h), radius, fill=fill,
                             border=0xFFFFFFFF if focused else 0x00000000, border_width=self.u(2.5) if focused else 0,
                             glow=self.u(14) if focused else 0, horizontal=True)
        elif focused:
            self.paint.panel(self.X(x), self.Y(y), self.u(w), self.u(h), radius, fill=(0x232842F4, 0x1A1E31F4),
                             border=CYAN, border_width=self.u(2.5), glow=self.u(14))
        else:
            self.paint.panel(self.X(x), self.Y(y), self.u(w), self.u(h), radius,
                             fill=(0x191C29E0, 0x141621E0) if enabled else (0x141621A0, 0x121420A0),
                             border=0x2B3044FF if enabled else 0x22263399, border_width=self.u(1.5))
        color = TEXT if enabled else FAINT
        if subtitle:
            self.label(title, x + 24, y + h / 2 - 4, 22, color, 'strong', width=w - 48 - (180 if value else 0))
            self.label(subtitle, x + 24, y + h / 2 + 22, 15.5, (0xE8ECF7FF if primary and enabled else MUTED)
                       if enabled else FAINT, 'body', width=w - 48 - (180 if value else 0))
        elif center:
            self.label(title, x + w / 2, y + h / 2 + 8, 22, color, 'strong', 'center', width=w - 16)
        else:
            self.label(title, x + 24, y + h / 2 + 8, 21, color, 'strong', width=w - 48 - (420 if value else 0))
        if value:
            self.label(value, x + w - 24, y + h / 2 + 8, 19, CYAN if enabled else FAINT, 'medium', 'right', width=400)

    def glyph(self, letter, x, y, color):
        self.paint.circle(self.X(x), self.Y(y), self.u(13), color)
        self.paint.text(letter, 'strong', self.u(14), 0x0B0C12FF, self.X(x), self.Y(y + 5), 'center')

    def connection_chip(self):
        connection = self.state.get('connection') or {}
        details = connection.get('details') or {}
        kind = connection.get('kind')
        if kind == 'wifi':
            bands = details.get('bands') or ([details['band']] if details.get('band') else [])
            if details.get('multi_link'):
                # Wi-Fi 7 multi-link: every band (RC4.2 showed only the first link's, e.g. "2.4 GHz").
                chip = 'Wi-Fi 7' + (' · ' + '+'.join(b.replace(' GHz', '') for b in bands) + ' GHz' if bands else '')
            else:
                chip = 'Wi-Fi' + (' ' + bands[-1] if bands else '')
            if details.get('signal_dbm') is not None:
                chip += ' · %s dBm' % details['signal_dbm']
            return chip
        if kind == 'ethernet':
            chip, speed = 'Ethernet', details.get('link_mbps')
            if speed:
                chip += ' · ' + (('%.1f' % (speed / 1000)).rstrip('0').rstrip('.') + ' Gbit/s' if speed >= 1000 else '%d Mbit/s' % speed)
            return chip
        return ''

    def header(self):
        if not self.paint.image(self.logo, self.X(48), self.Y(34), self.u(58), self.u(58)):
            self.paint.circle(self.X(77), self.Y(63), self.u(26), CYAN)
        # 1.0.0.3: the name in the logo's lettering on one line, VROST then Bridge; VROST's capitals are as tall as
        # the heading text's were (24 of its 34 units, their top at 46), so the line under it stays where it was.
        height = 24 / NAME_CAP
        if not (self.name_art and self.paint.image(self.name_art, self.X(120), self.Y(46 - NAME_CAP_TOP * height),
                                                   self.u(height * self.name_aspect), self.u(height))):
            self.label('VROSTBridge', 120, 70, 34, TEXT, 'heading')
        x = 121  # what the name stands for, its letters in the brand's colours (as on the pages)
        for text, colour, font in TAGLINE:
            self.label(text, x, 94, 15.5, colour, font)
            x += self.ink.width(font, text, self.u(15.5)) / self.k
        chip = self.connection_chip()
        right = 1232
        for text in (CLIENT_VERSION, chip):
            if not text:
                continue
            width = self.ink.width('medium', text, self.u(14)) / self.k + 32
            self.paint.panel(self.X(right - width), self.Y(46), self.u(width), self.u(34), self.u(17),
                             fill=(0xFFFFFF10, 0xFFFFFF0A), border=0x353B52FF, border_width=self.u(1.2))
            self.label(text, right - width / 2, 68, 14, TEXT if text == chip else MUTED, 'medium', 'center')
            right -= width + 12

    def session_detail(self):
        """The status card's explanation, most important first."""
        s = self.state
        game = s.get('game_mode') or {}
        wake = (s.get('system') or {}).get('wake') or {}
        graphics = s.get('graphics') or {}
        state = s.get('state')
        if s.get('startup_error'):
            return s['startup_error']
        if (s.get('runtime_restart') or {}).get('message'):  # RC4.4: SteamVR's compositor here closed
            return s['runtime_restart']['message']
        if wake.get('state') == 'waking' and wake.get('message'):
            return wake['message']
        if self.head_untracked():
            # 1.0.0.5: without the headset's position the host has nothing to render for.
            return ("SteamVR on this PC can't see the headset, so no picture can come. Check that the base "
                    'stations are on and can see it, and that the headset is awake.')
        if not s.get('running') and game.get('supported') is False and game.get('reason'):
            # RC4.2 greyed out Start here and showed "Ready to start a session." instead of why.
            return game['reason']
        power = s.get('power_balance') or {}
        held = power.get('held') or {}
        if s.get('running') and power.get('state') == 'starved' and held.get('cpu_mhz'):
            # 1.0.0.4: the processor held back by the shared power limit comes before the graphics.
            if held.get('cause') == 'fixed-gpu-clock':  # two lines on the card: the cause, then the one step
                return ("This PC's processor is held at " + str(held['cpu_mhz']) + ' MHz because the GPU clock is fixed'
                        + (' at ' + str(held['gpu_mhz']) + ' MHz' if held.get('gpu_mhz') else '') + '. '
                        + ('Choose This PC → Set the GPU clock to automatic.' if 'gpu-auto' in (power.get('actions') or [])
                           else 'Set it to automatic: Quick Access menu → Performance.'))
            return ("This PC's processor is held at " + str(held['cpu_mhz']) + ' MHz: raise the TDP limit and leave the '
                    'GPU clock automatic (Quick Access menu → Performance).')
        starved = graphics.get('starved') or {}
        if s.get('running') and graphics.get('state') == 'limited' and starved.get('clock_mhz'):
            # RC4.4: the power limit, not the stream size, is what to change first.
            return ("This PC's GPU is held at " + str(starved['clock_mhz']) + ' of its ' + str(starved.get('max_mhz')) +
                    ' MHz. Raise the TDP limit and leave the GPU clock automatic (Quick Access menu → Performance), '
                    'then start again.')
        if s.get('running') and graphics.get('state') == 'limited':
            smaller = graphics.get('smaller') or {}
            return ("This PC's graphics can't keep up with the stream, so SteamVR shows fewer frames."
                    + (' Stream & headset → Use ' + str(smaller['label']) + ' gives a steady rate from the next Start.'
                       if 'use-standard' in (graphics.get('actions') or []) and smaller.get('label')
                       and s.get('settings', {}).get('resolution') != 'sharp' else ''))
        return ((s.get('session_link') or {}).get('message') or
                ('Fresh video is reaching the headset.' if state == 'streaming' else game.get('reason') or
                 'Start opens SteamVR on both PCs and connects them.'))

    def head_untracked(self):
        """1.0.0.5: SteamVR here has had no head pose for 2 s or more during a session."""
        s = self.state
        try:
            return bool(s.get('running')) and float((s.get('metrics') or {}).get('head_untracked_ms', 0)) >= 2000
        except (TypeError, ValueError):
            return False

    def status_card(self):
        s = self.state
        settings = s.get('settings', {})
        busy = s.get('busy') or self.pending
        running = s.get('running')
        state = s.get('state')
        title = ('Starting or stopping…' if busy else {'streaming': 'Streaming', 'stopped': 'Ready to start',
                                                         'error': 'Session needs attention', 'recovery': 'Recovering video',
                                                         'connecting': 'Connecting to your host'}.get(
            state, str(state or 'Checking').replace('-', ' ').capitalize()))
        game = s.get('game_mode') or {}
        if not running and not busy and state in ('stopped', None) and game.get('supported') is False:
            title = 'Headset display not reserved'
        if not busy and self.head_untracked():
            title = 'Headset not tracked'
        detail = self.session_detail()
        self.paint.panel(self.X(48), self.Y(122), self.u(1184), self.u(190), self.u(22), accent=True)
        self.label('SESSION', 76, 160, 13, CYAN, 'heading', tracking=2.2)
        warn = (state == 'error' or s.get('startup_error') or (not running and game.get('supported') is False)
                or self.head_untracked())
        # 1.0.0.5: a session whose headset is not tracked is streaming nothing: the warning colour.
        dot = (MAGENTA if self.head_untracked() else CYAN if state == 'streaming' else MAGENTA if warn
               else PURPLE if busy or running else FAINT)
        self.paint.circle(self.X(84), self.Y(196), self.u(7), dot)
        self.label(title, 102, 210, 38, TEXT, 'heading', width=700)
        bottom = self.paint.paragraph(detail, 'body', self.u(17), BAD if warn else MUTED, self.X(76),
                                      self.Y(246), self.u(740), 2)
        video = s.get('video', {})
        if video.get('width'):
            line = '%s × %s per eye · %s%s' % (video['width'], video['height'], CODEC_LABELS.get(video.get('codec'), video.get('codec', '')),
                                               '' if running else ' · last session')
            shown = (video.get('shown_width'), video.get('shown_height'))
            if running and shown[0] and shown[0] != video['width']:  # 1.0.0.5: shown at SteamVR's recommended size
                line += ' · shown at %s × %s' % shown
            gpu = (s.get('graphics') or {}).get('gpu') or {}
            if running and gpu.get('clock_mhz') and gpu.get('max_mhz'):  # RC4.4: watch a TDP change take effect
                line += ' · GPU %s of %s MHz' % (gpu['clock_mhz'], gpu['max_mhz'])
            self.label(line, 76, min(290, (bottom - self.oy) / self.k + 4), 15, FAINT, 'medium', width=700)
        metrics, timings = s.get('metrics', {}), s.get('timings', {})
        tiles = []
        if running:
            tiles = [(str(round(float(timings['unique_fps']))) if timings.get('unique_fps') else '--', 'fps'),
                     (str(metrics.get('frame_age_ms', '--')), 'ms frame age'),
                     (str(timings.get('net_lost_frames_total', '0')), 'lost frames')]
        else:
            display = s.get('display') or {}
            if display.get('detected'):
                tiles = [(str(display.get('refresh_hz', '--')), 'Hz'),
                         (('Auto' if settings.get('auto_bitrate') else str(settings.get('bitrate', 80))), 'Mbit/s'),
                         (CODEC_LABELS.get(settings.get('codec', 'Auto'), 'Auto'), 'codec')]
        x = 848
        for value, caption in tiles:
            self.paint.panel(self.X(x), self.Y(172), self.u(118), self.u(104), self.u(14), fill=(0x0F111AE0, 0x0F111AE0),
                             border=0x262A39FF, border_width=self.u(1.2))
            self.label(value, x + 59, 228, 30, TEXT, 'heading', 'center', width=104)
            self.label(caption, x + 59, 256, 13, MUTED, 'body', 'center', width=104)
            x += 128

    def page_main(self):
        s = self.state
        settings = s.get('settings', {})
        busy = s.get('busy') or self.pending
        running = s.get('running')
        self.status_card()
        live = running or busy
        # Start stays available in Game Mode: without a reserved headset display it explains why
        # (RC4.2 greyed it out without a reason).
        self.button('stop' if live else 'start', 'Stop session' if live else 'Start session', 48, 336, 580, 104,
                    primary=True, subtitle='Closes SteamVR on both PCs' if live else 'Opens SteamVR on both PCs and connects them')
        self.button('host-menu', 'Open host menu', 652, 336, 580, 104, enabled=bool(running and not busy),
                    subtitle='The host\'s SteamVR dashboard, in the headset')
        options = self.stream_options()
        chosen = next((o for o in options if o.get('id') == settings.get('resolution', 'automatic')), None)
        stream = (chosen or {}).get('label', 'Auto').replace(' - ', ' · ').replace(' x ', ' × ')
        self.button('stream', 'Stream & headset', 48, 462, 380, 92, subtitle=stream)
        self.button('motion', 'Motion & timing', 450, 462, 380, 92,
                    subtitle='Re-aim ' + ('paused' if (s.get('graphics') or {}).get('reaim_paused') else
                                          'on' if self.setting('reaim') else 'off') +
                    ' · prediction ' + ('on' if self.setting('motion_prediction') else 'off'))
        host = settings.get('host_ip') or (s.get('session_link') or {}).get('host_ip') or 'found automatically'
        self.button('network', 'Network & host', 852, 462, 380, 92, subtitle='Host ' + host)
        self.button('pc', 'This PC', 48, 576, 281, 72)
        self.button('reports', 'Reports', 349, 576, 281, 72)
        self.button('about', 'About', 650, 576, 281, 72)
        self.button('exit', 'Exit VROSTBridge', 951, 576, 281, 72)

    # ------------------------------------------------------------------ settings lists
    def list_page(self, title, rows, help_text=''):
        """A scrolling list of rows: (name, title, value, enabled, description). Back sits beside the title.

        The focused row's description replaces the page help, so a controller user reads what
        a setting does before changing it.
        """
        self.label(title, 48, 158, 34, TEXT, 'heading', width=900)
        count = len(rows)
        names = [entry[0] for entry in rows]
        if self.focus_name in names:  # the same setting stays focused when rows above it come or go
            self.focus = names.index(self.focus_name)
        top, height, row, step = self.LIST_TOP, self.LIST_HEIGHT, self.ROW, self.ROW_STEP
        content = count * step - (step - row) if count else 0
        offset = self.scroll.get(self.page, 0)
        if self.focus < count:  # keep the focused row inside the band
            y = self.focus * step
            if y < offset:
                offset = y
            elif y + row > offset + height:
                offset = y + row - height
        offset = max(0, min(offset, max(0, content - height)))
        self.scroll[self.page] = offset
        described = rows[self.focus][4] if self.focus < count and rows[self.focus][4] else help_text
        self.paint.paragraph(described, 'body', self.u(16.5), MUTED, self.X(48), self.Y(192), self.u(1184), 2)
        # Room for the focus glow above the first row, none below: no sliver of the next row shows.
        lead = 8 if offset == 0 else 0  # glow room above the first row, but no sliver of a row scrolled away
        clip = Rect(int(self.X(0)), int(self.Y(top - lead)), int(self.u(1280)), int(self.u(height + lead - 1)))
        self.s.RenderSetClipRect(self.renderer, c.byref(clip))
        try:
            for index, (name, label, value, enabled, _) in enumerate(rows):
                y = top + index * step - offset
                self.button(name, label, 48, y, 1176, row, enabled=enabled, value=value,
                            visible=top <= y and y + row <= top + height, row=True)
        finally:
            self.s.RenderSetClipRect(self.renderer, None)
        if content > height:  # scroll bar
            self.paint.panel(self.X(1236), self.Y(top), self.u(4), self.u(height), self.u(2),
                             fill=(0xFFFFFF14, 0xFFFFFF14), border_width=0)
            thumb = max(40, height * height / content)
            self.paint.panel(self.X(1236), self.Y(top + (height - thumb) * offset / (content - height)), self.u(4),
                             self.u(thumb), self.u(2), fill=(CYAN, MAGENTA), border_width=0)
        self.button('back', 'Back', 1052, 122, 180, 52, center=True)

    def toggle(self, key, title, description, locked):
        return ('toggle:' + key, title, 'On' if self.setting(key) else 'Off', not locked, description)

    def scale_note(self):
        """1.0.0.11: the shown size this session, when it changed (launcher/output_scale.py)."""
        s = self.state
        scale = ((s.get('graphics') or {}).get('output_scale') or {}) if s.get('running') else {}
        if not scale.get('steps'):
            return ''
        if scale.get('level', 100) < 100:
            return ' Now ' + str(scale['level']) + ' %: ' + str(scale.get('reason', '')) + '.'
        return ' Back at full size.'

    def page_stream(self):
        s = self.state
        settings, display = s.get('settings', {}), s.get('display', {})
        busy, running = s.get('busy') or self.pending, s.get('running')
        locked = bool(busy or running)
        chosen = next((o for o in self.stream_options() if o.get('id') == settings.get('resolution', 'automatic')),
                      {'label': 'Auto - detect on Start'})
        fov = (s.get('system') or {}).get('foveation') or {}
        choice = FOVEATION_LABELS.get(settings.get('foveation', 'auto'), 'Auto')
        resolved = FOVEATION_LABELS.get(fov.get('preset', 'off'), 'Off')
        system = s.get('system') or {}
        tested = system.get('path_test') or {}
        bitrate = ('Auto' + (' · ' + str(tested['recommended_mbps']) + ' Mbit/s' if tested.get('recommended_mbps') else '')
                   if settings.get('auto_bitrate') else str(settings.get('bitrate', 80)) + ' Mbit/s')
        rate = system.get('bitrate') or {}
        if running and rate.get('active') and rate.get('mbps'):  # 1.0.0.8: as this PC steers it during the session
            bitrate = 'Auto · ' + str(rate['mbps']) + ' Mbit/s now'
        graphics = s.get('graphics') or {}
        rows = []
        if (fov.get('panel') or {}).get('show'):
            rows.append(('eyes', 'Eye-tracking view', 'live', True,
                         'Where each eye looks and the sharp region that follows it. Live only: never saved.'))
        smaller = graphics.get('smaller') or {}
        if ('use-standard' in (graphics.get('actions') or []) and settings.get('resolution') != 'sharp'
                and smaller.get('label') and smaller.get('width')):
            rows.append(('use-standard', 'Use ' + str(smaller['label']) + ' · ' + str(smaller['width']) + ' per eye',
                         'steady frame rate', not busy, graphics.get('message') or ''))
        # RC4.4: Eye-tracked shows its settings whenever it is chosen, and says what is missing while
        # the region does not follow the eyes (RC4.3 hid them until it did, which looked broken).
        eye_choice = settings.get('foveation') == 'eye-tracked' or fov.get('preset') == 'eye-tracked'
        eye_note = EYE_NOTES.get(fov.get('eye_state', '')) if eye_choice else None
        eye_message = (s.get('eye_tracking') or {}).get('message') or ''
        rows += [
            ('resolution', 'Stream resolution', chosen['label'].replace(' - ', ' · ').replace(' x ', ' × '), not locked,
             'The picture the host sends per eye. More pixels need more time on both PCs; Standard keeps a steady '
             'frame rate on integrated graphics.'),
            ('codec', 'Video codec', CODEC_LABELS.get(settings.get('codec', 'Auto'), 'Auto'), not locked,
             'Auto picks HEVC for the Beyond and H.264 for the Index. AV1 needs AV1 on both PCs.'),
            ('quality', 'Bitrate', bitrate, not locked,
             bitrate_note(rate, running) or
             'Auto tests the network to your host once per adapter, asks only for what the picture needs, and steps '
             'down during a session when decoding falls behind or frames go missing, then back up as it runs clean.'),
            ('foveation', 'Foveated encoding', choice + ('' if choice == resolved else ' (' + resolved + ')'), not locked,
             'Sends the edges of each eye at reduced resolution, so detail goes where you look.'),
        ]
        if eye_note:
            rows.append(('eyetrack', eye_note[0], eye_note[1], True, eye_note[2] or eye_message))
        if eye_choice:  # RC4.3: the region follows the gaze; its size and the outside can be chosen
            region = settings.get('fov_region', 'medium')
            later = ' Used once eye tracking runs.' if eye_note else ''
            rows += [
                ('fov-region', 'Sharp region', FOVEATION_REGION_LABELS.get(region, 'Medium') + ' · ' +
                 str(round(100 * FOVEATION_REGIONS.get(region, .4))) + '% of each eye', not locked,
                 'How much of each eye stays at full resolution around where you look. Smaller sends fewer pixels.' + later),
                ('fov-edge', 'Outside the sharp region', FOVEATION_EDGE_LABELS.get(settings.get('fov_edge', 'balanced'),
                                                                                  'Balanced · ¼') + ' resolution', not locked,
                 'How much the rest of each eye is reduced. Lighter sends fewer pixels: less work for both PCs and the '
                 'network, and room for a higher stream resolution.' + later)]
        rows += [
            ('sharpening', 'Sharpening', SHARPENING_LABELS.get(settings.get('sharpening', 'medium'), 'Medium'), not busy,
             'Brings back fine detail when the picture is shown in the headset (separate from foveated encoding, '
             'which decides how much is sent). Applies immediately.'
             if settings.get('one_pass', True) else
             'Needs the one-pass frame path, which is off on the This PC page.'),
            ('ten-bit', '10-bit colour', 'On' if settings.get('ten_bit', True) else 'Off', not locked,
             'Smooth dark gradients without banding. HEVC and AV1; H.264 stays 8-bit.'),
            ('encoder', 'Encoder priority', ENCODER_LABELS.get(settings.get('encoder', 'speed'), 'Lowest latency'),
             not locked, 'Lowest latency, or a little more quality per bit for a little more delay.'),
            ('keep-warm', 'Quick resume', KEEP_WARM_LABELS.get(settings.get('keep_warm_minutes', 0), 'Off'), not locked,
             'Keeps SteamVR open on the host after Stop, so starting again skips its startup.'),
            ('network-test', 'Test network now', '', not locked,
             (system.get('network_test') or {}).get('message') or tested.get('message') or
             'Measures how much video the network to your host carries cleanly (about 15 s).'),
            ('recommended-settings', 'Use recommended settings', '', not locked,
             'Codec, stream resolution, foveated encoding and bitrate on Auto, with the frame timing lock, re-aim, '
             'late-frame catch-up and performance balancing on. Applies at the next Start.'),
        ]
        model = ((str(display.get('model', '')) + ' · ' + str(display.get('refresh_hz', '')) + ' Hz' +
                  ('' if running else ' · last detected')) if display.get('detected') else
                 'The connected headset is detected when you start.')
        self.list_page('Stream & headset', rows, model + ('. Stop the session to change these.' if running else
                                                          '. Changes apply to the next session.'))

    def page_motion(self):
        s = self.state
        busy, running = s.get('busy') or self.pending, s.get('running')
        locked = bool(busy or running)
        check = s.get('alignment_check') or {}
        graphics = s.get('graphics') or {}
        reaim = 'Paused' if graphics.get('reaim_paused') else 'On' if self.setting('reaim') else 'Off'
        align_value = {'running': str(check.get('remaining_s', 10)) + ' s left', 'ask': 'answer below',
                       'answered': 'done'}.get(check.get('state'), '')
        rows = [('reaim', 'Re-aim frames to your head', reaim, not busy,
                 'Turns each frame to where your head is when the headset shows it, so text and held objects stay '
                 'still. Applies immediately.' + (' Paused for this session: this PC\'s graphics were at their limit.'
                                                  if graphics.get('reaim_paused') else '')),
                ('align-check', 'Check alignment', align_value,
                 bool(running and not busy and s.get('state') == 'streaming' and check.get('state') != 'running'),
                 check.get('message') if check.get('state') == 'answered' else
                 'Tells you whether SteamVR on this PC already re-aims frames. Needs a running session.')]
        if check.get('state') == 'ask':
            rows += [('align-yes', 'Yes, the picture jumped once a second', '', not busy,
                      'SteamVR already re-aims: VROSTBridge turns its own re-aim off.'),
                     ('align-no', 'No, it stayed still', '', not busy, 'VROSTBridge keeps re-aiming frames.')]
        rows += [self.toggle('motion_prediction', 'Motion prediction',
                             'Renders each frame for where your head and hands will be when it is shown.', locked),
                 self.toggle('timing_lock', 'Frame timing lock',
                             "Times the host's frames to this headset's display so each arrives just in time.", locked),
                 self.toggle('late_frame_catchup', 'Catch late frames',
                             'Waits up to 3 ms for a nearly ready frame instead of showing the previous one again.', locked),
                 self.toggle('pacing', 'Pace video to the link',
                             'The host spreads each frame at the rate your link carries, so bursts cannot overflow it.',
                             locked),
                 self.toggle('intra_refresh', 'Gradual loss recovery',
                             'A lost packet heals within half a second without a large keyframe. Recommended on Wi-Fi.',
                             locked)]
        self.list_page('Motion & timing', rows, 'How frames are timed and aimed so the picture stays steady.')

    def page_network(self):
        s = self.state
        settings = s.get('settings', {})
        busy, running = s.get('busy') or self.pending, s.get('running')
        locked = bool(busy or running)
        network = (s.get('session_link') or {}).get('network', {})
        connection = s.get('connection') or {}
        wired = connection.get('kind') == 'ethernet'
        system = s.get('system') or {}
        wake = system.get('wake') or {}
        summary = connection.get('summary') or 'Checking how this PC connects.'
        warnings = ' '.join(connection.get('warnings') or [])
        rows = [('info:connection', "This PC's connection", self.connection_chip() or 'Checking', True,
                 summary + ('. ' + warnings if warnings else '')),
                ('host', 'Host address', settings.get('host_ip') or 'Automatic', not locked,
                 'Your rendering host is found automatically on the same home network. Enter its address if not.'),
                ('adapter', 'Network adapter', network.get('interface') or 'Automatic', not locked,
                 'VROSTBridge uses this adapter only; your other connections stay available.')]
        if not wired:
            rows.append(self.toggle('stay_on_access_point', 'Stay on one access point',
                                    'Stops the Wi-Fi background scans that pause the stream every few minutes.', locked))
        rows.append(self.toggle('pause_other_wifi', 'Pause Wi-Fi during sessions' if wired else 'Pause other Wi-Fi adapters',
                                'Another Wi-Fi adapter on your home network can stop the stream from reconnecting. '
                                'It is reconnected when the session stops.', locked))
        if (s.get('wifi') or {}).get('permanent_fix'):
            rows.append(('wifi-powersave-off', 'Turn off Wi-Fi power saving', '', not locked,
                         (s.get('wifi') or {}).get('message') or 'Keeps the Wi-Fi adapter fully awake on your home network.'))
        rows += [self.toggle('wake_host', 'Wake the host PC when it is asleep',
                             'Sends Wake-on-LAN on Start. Learnt after one session; the host needs Wake on Magic Packet.',
                             locked),
                 ('wake-host', 'Wake host now', '', bool(not locked and wake.get('mac_known')),
                  wake.get('message') or 'Available after one session with the host PC.')]
        self.list_page('Network & host', rows, summary)

    def page_pc(self):
        s = self.state
        busy, running = s.get('busy') or self.pending, s.get('running')
        locked = bool(busy or running)
        system = s.get('system') or {}
        repair = system.get('async_reprojection') or {}
        power = s.get('power_balance') or {}
        rows = []
        if 'gpu-auto' in (power.get('actions') or []):  # 1.0.0.4: the processor held back by a fixed GPU clock
            held = power.get('held') or {}
            tdp = (power.get('steamos') or {}).get('tdp_w')
            explain = ('The GPU clock is fixed' + (' at ' + str(held['gpu_mhz']) + ' MHz' if held.get('gpu_mhz') else '')
                       + ' and takes nearly all of the power limit' + (' (' + str(tdp) + ' W)' if isinstance(tdp, int) and tdp > 0 else '')
                       + ', so the processor is held at ' + str(held.get('cpu_mhz', '?')) + ' MHz'
                       + (' (its minimum is ' + str(held['cpu_min_mhz']) + ' MHz)' if held.get('cpu_min_mhz') else '')
                       + '. Choose this to set the GPU clock to automatic; it applies at once.')
            rows.append(('gpu-auto', 'Set the GPU clock to automatic', 'processor at ' + str(held.get('cpu_mhz', '?')) + ' MHz',
                         not busy, explain))
        elif 'gpu-auto' in ((s.get('graphics') or {}).get('actions') or []):
            # 1.0.0.5: the GPU itself held low by a fixed clock while the stream needs more.
            starved = (s.get('graphics') or {}).get('starved') or {}
            explain = ('The GPU clock is fixed' + (' at ' + str(starved['clock_mhz']) + ' MHz' if starved.get('clock_mhz') else '')
                       + ", so this PC's graphics can't keep up with the stream. Choose this to set the GPU clock to"
                       ' automatic; it applies at once.')
            rows.append(('gpu-auto', 'Set the GPU clock to automatic',
                         'GPU at ' + str(starved.get('clock_mhz', '?')) + ' MHz', not busy, explain))
        tuning = system.get('tuning') or {}
        bases = system.get('base_stations') or {}
        bases_working = bases.get('state') == 'working'
        base_note = bases.get('message') or (bases.get('last') or {}).get('message') or ''
        rows += [self.toggle('keep_awake', 'Keep this PC awake during sessions',
                            'Blocks sleep and the lid switch and uses the performance power mode until Stop.', locked),
                 # 1.0.0.8: applied at once, also during a session.
                 self.toggle('balance_performance', 'Balance performance during sessions',
                             'Only while video streams: the power limit at its maximum on the charger, and the GPU '
                             'clock held higher when frames run late with the GPU at a low clock, never so high that '
                             'the processor is squeezed. Everything is set back at Stop.' +
                             (' ' + tuning['message'] if tuning.get('message') else ''), busy),
                 # 1.0.0.11: applied at once, also during a session.
                 self.toggle('keep_frame_rate', 'Keep the frame rate when this PC falls behind',
                             "While this PC's graphics hold the stream below the headset's refresh, each eye is shown a "
                             'little smaller (90 %, then 80 % of its size) instead of SteamVR halving the frame rate, '
                             'and at full size again once there is room.' + self.scale_note(), busy),
                 self.toggle('base_stations_off', 'Base stations off when VROSTBridge closes',
                             "SteamVR 2.0 base stations, over this PC's Bluetooth: turned on as each session starts "
                             'and off when VROSTBridge closes.' + (' ' + base_note if base_note else ''), busy),
                 ('base-stations-on', 'Base stations on now', 'working…' if bases_working else '',
                  not busy and not bases_working, base_note or "Turns the SteamVR 2.0 base stations on over this PC's "
                                                             'Bluetooth.'),
                 ('base-stations-off', 'Base stations off now', 'working…' if bases_working else '',
                  not busy and not bases_working, base_note or "Turns the SteamVR 2.0 base stations off over this PC's "
                                                              'Bluetooth.'),
                self.toggle('headset_audio', "Use the headset's speakers and microphone",
                            "Makes the headset's audio devices the defaults for the session, then restores yours.", locked),
                self.toggle('microphone', 'Send the headset microphone to the host',
                            "The host PC needs its virtual microphone: set it up once on the host's VROSTBridge page "
                            '(Set up headset microphone).', locked),
                self.toggle('headset_hud', 'Stream quality in the headset',
                            'A small bar with frame rate, latency and lost frames.', locked),
                ('eyetrack', 'Eye tracking', self.eye_summary(), True,
                 'Beyond 2e: the sharp region follows your eyes. Set-up, Baballonia and camera access.'),
                self.toggle('software', 'CPU decoding', 'Troubleshooting only. Hardware decoding is the default.', locked),
                self.toggle('one_pass', 'One-pass frame path',
                            'Sharper and lighter on this PC. Turn off only if the headset picture looks wrong: '
                            "RC4.2's two passes, without sharpening. Applies at the next Start.", locked),
                # 1.0.0.11
                self.toggle('lens_area', 'Skip what the lenses never show',
                            "The one-pass frame path leaves out the parts of each eye's picture the headset's lenses "
                            'never show, with a margin. Turn off only if the edges of the picture look wrong. Applies at '
                            'the next Start.', locked)]
        if repair.get('state') in ('missing', 'disabled'):
            rows.append(('repair-async', 'Fix SteamVR reprojection', '', not locked,
                         repair.get('message') or 'Lets SteamVR smooth a late frame instead of halving the frame rate.'))
        if running:
            rows.append(('sync-playspace', 'Resend play area', '', not busy,
                         "Sends this PC's play area to the host again."))
        if (s.get('client_runtime') or {}).get('settings_pending_restore') and not running:
            rows.append(('restore-client-runtime', 'Restore saved SteamVR settings', '', not busy,
                         'Puts back the local SteamVR settings VROSTBridge changed for a session.'))
        self.list_page('This PC', rows, 'Power, audio and SteamVR on this PC.')

    def page_host(self):
        s = self.state
        locked = bool(self.pending or s.get('running') or s.get('busy'))
        self.label('Host address', 48, 158, 34, TEXT, 'heading')
        self.label(self.edit or 'Find the host automatically', 48, 206, 26, CYAN, 'medium', width=700)
        for i, ch in enumerate('123456789.0'):
            self.button('key:' + ch, ch, 48 + (i % 6) * 118, 236 + (i // 6) * 82, 104, 70, center=True)
        self.button('erase', 'Delete', 760, 236, 220, 70)
        self.button('host-auto', 'Automatic', 1000, 236, 232, 70)
        self.button('host-save', 'Save address', 760, 318, 472, 70, enabled=not locked, primary=True)
        self.paint.paragraph('Enter the host PC\'s home-network address, or choose Automatic to find it on the same '
                             'network.', 'body', self.u(16), MUTED, self.X(48), self.Y(440), self.u(1184), 2)
        self.button('back', 'Back', 48, 600, 380, 64)

    EYE_TRAIL_S = 1.0

    @staticmethod
    def glide(shown, target, amount):
        """The shown point moves most of the way to the newest one at each paint (1.0.0.3)."""
        if not target:
            return None
        if not shown:
            return list(target)
        return [shown[0] + (target[0] - shown[0]) * amount, shown[1] + (target[1] - shown[1]) * amount]

    def page_eyes(self):
        """RC4.3: where each eye looks and the sharp region, drawn live. 1.0.0.3: twenty times a second,
        the points gliding to each new sample, the gaze with a trail of its last second, and the
        reduced area drawn as coarse squares against the sharp region's fine ones."""
        panel = ((self.state.get('system') or {}).get('foveation') or {}).get('panel') or {}
        try:
            view = self.fovea_fn() or {}
        except Exception:
            view = {}
        self.label('Eye-tracked foveation', 48, 158, 34, TEXT, 'heading', width=900)
        live = bool(view.get('live'))
        note = ('Following your eyes · sample ' + str(view.get('gaze_age_ms', 0)) + ' ms old' if live and view.get('gaze_left')
                else 'No current eye-tracking sample: the sharp region holds where you last looked, then returns to the '
                     'middle.' if live
                else 'Waiting for the stream…' if self.state.get('running') else 'Start a session with eye tracking.')
        self.label(note, 48, 196, 17, CYAN if live else MUTED, 'medium', width=900)
        aspect = (view['eye'][0] / view['eye'][1]) if live and view.get('eye') and view['eye'][1] else 1.0
        now = time.monotonic()
        for index, (name, key) in enumerate((('Left eye', 'left'), ('Right eye', 'right'))):
            motion = self.eye_motion.setdefault(key, {'trail': []})
            gaze = view.get('gaze_' + key) if live else None
            if gaze and motion['trail'] and motion['trail'][-1][:2] == (gaze[0], gaze[1]):
                motion['trail'][-1] = (gaze[0], gaze[1], now)  # held still: fresh until the eye moves on
            elif gaze:
                motion['trail'].append((gaze[0], gaze[1], now))
            motion['trail'] = [p for p in motion['trail'] if now - p[2] < self.EYE_TRAIL_S][-80:]
            motion['centre'] = self.glide(motion.get('centre'), view.get(key) if live else None, .55)
            motion['gaze'] = self.glide(motion.get('gaze'), gaze, .65)
            self.eye(48 + index * 324, 226, 300, aspect, view.get('region') if live else None, motion['centre'],
                     motion['gaze'], view.get('edge') if live else None, motion['trail'] if gaze else (), now)
            self.label(name, 48 + index * 324 + 150, 552, 16, MUTED, 'medium', 'center')
        eye, enc = panel.get('stream_eye'), panel.get('encoded_eye')
        edge = panel.get('edge_ratio')
        rows = [('Sharp region', (str(panel.get('region_percent', '—')) + '% of each eye') if panel.get('region_percent') else '—'),
                ('Outside', (FRACTIONS.get(round(edge), '1/' + str(edge)) + ' resolution') if edge else '—'),
                ('Sent per eye', (str(enc[0]) + ' × ' + str(enc[1]) + (' of ' + str(eye[0]) + ' × ' + str(eye[1]) if eye else ''))
                 if enc else '—'),
                ('Pixels saved', (str(panel['saving_percent']) + '% fewer') if panel.get('saving_percent') is not None else '—'),
                ('On your gaze', (str(round(100 * panel['gaze_share'])) + '% of frames') if panel.get('gaze_share') is not None else '—'),
                ('Eye tracking', (str(round(panel['gaze_rate'])) + ' samples/s') if panel.get('gaze_rate') is not None else '—'),
                # 1.0.0.6: how far the gaze reached this session (whole degrees from straight ahead).
                ('Gaze reach', gaze_reach_text(panel.get('gaze_reach'))),
                ('Frame pass', {'fused': 'One pass', 'two-pass': 'Two passes', 'cpu': 'CPU'}.get(panel.get('frame_pass'), '—')),
                ('Sharpening', SHARPENING_LABELS.get(panel.get('sharpening'), '—'))]
        y = 252
        for label, value in rows:
            self.label(label, 700, y, 16, MUTED, 'medium')
            self.label(value, 880, y, 18, TEXT, 'strong', width=350)
            y += 38
        rate = panel.get('gaze_rate')
        if isinstance(rate, (int, float)) and 0 < rate < SLOW_GAZE_RATE:  # 1.0.0.4: the region then lags the eyes
            self.label('Eye tracking sends only ' + str(round(rate)) + ' samples a second, so the sharp region reaches where '
                       'you look late. Check the session card, or choose a larger sharp region.', 48, 584, 15, BAD,
                       'body', width=1180)
        else:
            self.label('Coarse squares: reduced resolution · fine squares: the sharp region · pink: where you look, with '
                       'its last second.', 48, 584, 15, MUTED, 'body', width=1180)
        self.label('Live view only: where you look is never saved. Reports include only how far the gaze reached.', 48, 610,
                   15, FAINT, 'body')
        self.button('back', 'Back', 1052, 122, 180, 52, center=True)

    def checker(self, x, y, w, h, cell, color, clip=None):
        """Every other square of a grid of cells anchored at (x, y), in one call (cached by geometry)."""
        key = (x, y, w, h, cell)
        rects = self.eye_cells.get(key)
        if rects is None:
            cells = [Rect(x + i, y + j, cell, cell) for j in range(0, h, cell) for i in range(0, w, cell)
                     if (i // cell + j // cell) % 2 == 0]
            rects = (Rect * len(cells))(*cells), len(cells)
            if len(self.eye_cells) > 16:
                self.eye_cells.clear()
            self.eye_cells[key] = rects
        if clip is not None:
            self.s.RenderSetClipRect(self.renderer, c.byref(clip))
        self.s.SetRenderDrawColor(self.renderer, (color >> 24) & 255, (color >> 16) & 255, (color >> 8) & 255, color & 255)
        self.s.RenderFillRects(self.renderer, rects[0], rects[1])

    def eye(self, x, y, size, aspect, region, centre, gaze, edge=None, trail=(), now=0.0):
        """One eye's image: the reduced area as coarse squares, the sharp region (fine squares, blue to
        purple), and where the eye looks (pink) with its trail."""
        w, h = (size, size / aspect) if aspect >= 1 else (size * aspect, size)
        x0, y0 = x + (size - w) / 2, y + (size - h) / 2
        self.paint.panel(self.X(x0), self.Y(y0), self.u(w), self.u(h), self.u(14), fill=(0x1D2130FF, 0x161925FF),
                         border=0x353B52FF, border_width=self.u(1.5))
        ex, ey, ew, eh = int(self.X(x0)), int(self.Y(y0)), int(self.u(w)), int(self.u(h))
        clip = Rect(ex, ey, ew, eh)
        self.s.RenderSetClipRect(self.renderer, c.byref(clip))
        try:
            fine = max(3, round(ew / 64))
            try:
                ratio = max(1.0, min(8.0, float((edge or [4])[0])))
            except (TypeError, ValueError):
                ratio = 4.0
            if region and centre:
                # The reduced picture: squares as large as one of its pixels in the sharp region's squares.
                self.checker(ex, ey, ew, eh, max(fine + 1, round(fine * ratio)), 0x262B3FFF)
            if region and centre and region[0] * w >= 4 and region[1] * h >= 4:
                rw, rh = region[0] * w, region[1] * h
                rx, ry = x0 + centre[0] * w - rw / 2, y0 + centre[1] * h - rh / 2
                left, top = max(ex, int(self.X(rx))), max(ey, int(self.Y(ry)))
                right = min(ex + ew, int(self.X(rx + rw)))
                bottom = min(ey + eh, int(self.Y(ry + rh)))
                if right > left and bottom > top:
                    inside = Rect(left, top, right - left, bottom - top)
                    self.s.SetRenderDrawColor(self.renderer, 0x26, 0x30, 0x4A, 255)
                    self.s.RenderFillRect(self.renderer, c.byref(inside))
                    self.checker(ex, ey, ew, eh, fine, 0x2D3957FF, inside)
                    self.s.RenderSetClipRect(self.renderer, c.byref(clip))
                self.paint.panel(self.X(rx), self.Y(ry), self.u(rw), self.u(rh), self.u(10), fill=(0x00E5FF38, 0xFF2DCB38),
                                 border=PURPLE, border_width=self.u(2.5), horizontal=True)
                if rw > 64 and rh > 30:
                    self.label('Sharp', rx + 8, ry + 22, 14, TEXT, 'strong')
            else:
                self.s.SetRenderDrawColor(self.renderer, 0x2B, 0x30, 0x44, 255)  # quarter lines, for position
                line = max(1, int(self.u(1)))
                for f in (.25, .5, .75):
                    self.s.RenderFillRect(self.renderer, c.byref(Rect(int(self.X(x0 + w * f)), ey, line, eh)))
                    self.s.RenderFillRect(self.renderer, c.byref(Rect(ex, int(self.Y(y0 + h * f)), ew, line)))
            # The trail: a line of small dots through the samples of the last second, fading with age (each stretch
            # as old as its older end), ending at the dot as drawn (it glides to the newest sample).
            points = list(trail)
            if gaze and points:
                points[-1] = (gaze[0], gaze[1], points[-1][2])
            previous = None
            for px, py, at in points:
                level = min(4, int(4 * (now - at) / self.EYE_TRAIL_S))  # newest 0 .. oldest 4
                sx, sy = self.X(x0 + px * w), self.Y(y0 + py * h)
                if previous:
                    (qx, qy, older) = previous
                    steps = min(48, int(((sx - qx) ** 2 + (sy - qy) ** 2) ** .5 / self.u(4)))
                    for k in range(1, steps):
                        self.paint.circle(qx + (sx - qx) * k / steps, qy + (sy - qy) * k / steps, self.u(2.6 - .4 * older),
                                          0xFF2DCB00 | (0xB0 - 0x26 * older))
                self.paint.circle(sx, sy, self.u(4.5 - .7 * level), 0xFF2DCB00 | (0xC0 - 0x28 * level))
                previous = (sx, sy, level)
            if gaze:
                gx, gy = self.X(x0 + gaze[0] * w), self.Y(y0 + gaze[1] * h)
                self.paint.circle(gx, gy, self.u(20), 0xFF2DCB33)
                self.paint.circle(gx, gy, self.u(13), 0xFF2DCB55)
                self.s.SetRenderDrawColor(self.renderer, 255, 255, 255, 210)
                for dx, dy, rw_, rh_ in ((-14, -1, 6, 2), (8, -1, 6, 2), (-1, -14, 2, 6), (-1, 8, 2, 6)):
                    self.s.RenderFillRect(self.renderer, c.byref(Rect(int(gx + self.u(dx)), int(gy + self.u(dy)),
                                                                       max(1, int(self.u(rw_))), max(1, int(self.u(rh_))))))
                self.paint.circle(gx, gy, self.u(7.5), 0xFFFFFFFF)
                self.paint.circle(gx, gy, self.u(6), MAGENTA)
        finally:
            self.s.RenderSetClipRect(self.renderer, None)

    def eye_summary(self):
        """This PC's Eye tracking row: on or off, and what Baballonia is doing."""
        eye = self.state.get('eye_tracking') or {}
        baballonia = eye.get('baballonia') or {}
        if not self.setting('gaze'):
            return 'Off'
        if baballonia.get('state') in ('downloading', 'installing'):
            return 'Setting up ' + str(round(100 * (baballonia.get('progress') or 0))) + '%'
        if baballonia.get('state') != 'installed':
            return 'On · not set up'
        if eye.get('phase') == 'running':
            return 'On · running'
        return 'On · ' + ('calibrated' if baballonia.get('calibrated') else 'not calibrated')

    def page_eyetrack(self):
        """RC4.5: eye tracking set up and run by VROSTBridge (Baballonia and the Beyond 2e eye cameras)."""
        s = self.state
        eye = s.get('eye_tracking') or {}
        baballonia = eye.get('baballonia') or {}
        busy = bool(s.get('busy') or self.pending)
        state = baballonia.get('state')
        working = state in ('downloading', 'installing')
        installed = state == 'installed'
        phase = eye.get('phase') or 'off'
        rate = (eye.get('relay') or {}).get('rate')
        rows = [('toggle:gaze', 'Eye tracking', 'On' if self.setting('gaze') else 'Off', not busy,
                 'Starts the eye cameras and Baballonia with each session and sends where you look to the host, '
                 'so the sharp region follows your eyes. ' + ('Applies at the next Start.' if s.get('running') else ''))]
        if state in ('missing', 'error'):
            rows.append(('eye-setup', 'Set up eye tracking', str(baballonia.get('size_mb', '')) + ' MB download', not busy,
                         'Downloads Baballonia ' + str(baballonia.get('version', '')) + ' by Project Babble and checks it. '
                         'Its licence (Babble Software Distribution License 1.0) allows personal, non-commercial use '
                         'only; setting up accepts it.' + (' ' + baballonia['message'] if state == 'error' and
                                                           baballonia.get('message') else '')))
        if installed and baballonia.get('update'):
            # 1.0.0.3: an earlier VROSTBridge's Baballonia keeps working until it is updated.
            rows.append(('eye-setup', 'Update Baballonia', str(baballonia['update']) + ' · ' +
                         str(baballonia.get('size_mb', '')) + ' MB download', not busy and not baballonia.get('running'),
                         'Downloads Baballonia ' + str(baballonia['update']) + ' by Project Babble and checks it; your '
                         'calibration and settings stay. Its licence (Babble Software Distribution License 1.0) allows '
                         'personal, non-commercial use only; updating accepts it.'))
        if working:
            rows.append(('eye-setup-cancel', 'Cancel set-up', str(round(100 * (baballonia.get('progress') or 0))) + '%',
                         True, baballonia.get('message') or 'Setting up Baballonia…'))
        if installed:
            status = ('running · ' + (str(round(rate)) + ' samples/s' if rate is not None else 'sending') if phase == 'running'
                      else 'calibrating' if phase == 'calibrating' else 'finishing calibration' if phase == 'finishing'
                      else 'starting' if phase in ('starting', 'waiting') else 'closed' if phase == 'closed'
                      else 'needs attention' if phase in ('cameras', 'error', 'silent') else 'set up')
            rows.append(('info:eye-status', 'Baballonia ' + str(baballonia.get('version', '')), status, True,
                         eye.get('message') or baballonia.get('message') or ''))
            rows.append(('info:eye-calibration', 'Calibration', ('done ' + str(baballonia.get('date') or '')).strip()
                         if baballonia.get('calibrated') else 'in Desktop Mode', True,
                         'Calibrate once in Desktop Mode: start a session with Eye tracking on, then choose the Eye '
                         'calibration in Baballonia\'s window and follow it in the headset. It shows in the headset '
                         'only while SteamVR runs on this PC, so start the session first. Until then the fixed '
                         'Balanced region is used.'))
            # 1.0.0.8: the eye tracker's middle kept straight ahead, and the calibration before this one.
            centre = (eye.get('relay') or {}).get('centre') or {}
            rows.append(self.toggle('gaze_centre', 'Keep my gaze centred',
                                    'Your eyes rest near straight ahead most of the time. Where the eye tracker\'s '
                                    'middle sits further to one side, VROSTBridge takes the difference off, so the sharp '
                                    'region stays where you look. Remembered for each calibration.' +
                                    (' ' + centre_text(centre, phase == 'running') if centre_text(centre, phase == 'running')
                                     else ''), busy))
            if abs(centre.get('yaw') or 0) >= 0.5 or abs(centre.get('pitch') or 0) >= 0.5:
                rows.append(('gaze-centre-reset', 'Reset centring', 'now ' + str(round(abs(centre.get('yaw') or 0))) + '° · ' +
                             str(round(abs(centre.get('pitch') or 0))) + '°', not busy,
                             'Forgets the correction for this calibration; it is measured again as you use eye tracking.'))
            previous = eye.get('previous_calibration') or {}
            active = bool(baballonia.get('running') or eye.get('owners') or phase in (
                'starting', 'waiting', 'running', 'silent', 'calibrating', 'finishing', 'cameras'))
            if previous.get('name') and not active:
                rows.append(('eye-previous', 'Use previous calibration', str(previous.get('date') or ''),
                             not busy and not s.get('running'),
                             'Goes back to the calibration before the current one (Baballonia keeps both). Useful when '
                             'a new calibration puts the sharp region beside where you look.'))
            if phase == 'running':
                # 1.0.0.6: how far the gaze reached this session; choosing it measures again.
                reach = (eye.get('relay') or {}).get('reach')
                rows.append(('eye-reach-reset', 'Gaze reach', gaze_reach_text(reach) if reach else 'measuring…', not busy,
                             'How far your gaze went each way this session (Baballonia reports up to 45°). Choose it to '
                             'measure again, then look to the far left, right, up and down. If it stops short of where '
                             'you looked, calibrate again in Desktop Mode and turn your head further while you follow '
                             'the dot.'))
            if phase == 'finishing':
                pass  # it closes by itself once its calibration is saved
            elif baballonia.get('running') or 'user' in (eye.get('owners') or []) or phase in (
                    'starting', 'waiting', 'running', 'silent', 'cameras'):
                rows.append(('eye-close', 'Close Baballonia', '', not busy,
                             'Stops Baballonia and the eye cameras now. A running session keeps a centred sharp region.'))
            else:
                rows.append(('eye-open', 'Open Baballonia', '', not busy,
                             'Starts the eye cameras and Baballonia now, without a session.'))
        if eye.get('camera_access'):
            access = eye.get('access') or {}
            rows.append(('eye-camera-access', 'Allow eye camera access',
                         'asking…' if access.get('state') == 'asking' else 'password',
                         not busy and access.get('state') != 'asking',
                         (access.get('message') + ' ' if access.get('state') == 'error' and access.get('message') else '') +
                         'Lets this PC\'s signed-in user open the eye cameras (asks for your password once). If no '
                         'password prompt appears in Game Mode, do it from the VROSTBridge page in Desktop Mode.'))
        if installed and not baballonia.get('running') and not eye.get('owners'):
            rows.append(('eye-remove', 'Remove Baballonia', '', not busy,
                         'Deletes the downloaded Baballonia. Its settings and calibration stay in ~/.config/ProjectBabble.'))
        self.list_page('Eye tracking', rows, 'Bigscreen Beyond 2e. Baballonia uses one to two CPU cores while it runs.')

    def page_reports(self):
        s = self.state
        self.label('Reports', 48, 162, 34, TEXT, 'heading')
        self.paint.paragraph('A report has the connection, video and tracking details of this session. Send it to the host '
                             'PC to download and share it there, or save it on this PC.', 'body', self.u(17), MUTED,
                             self.X(48), self.Y(200), self.u(1100), 3)
        sending = (s.get('report_delivery') or {}).get('state') == 'sending'
        self.button('send-report', 'Send report to host', 48, 270, 580, 96, primary=True, enabled=not self.pending and not sending,
                    subtitle='Shows on the host\'s VROSTBridge page under Reports')
        self.button('report', 'Save report on this PC', 652, 270, 580, 96, enabled=not self.pending,
                    subtitle='In ~/.local/state/VROSTBridge/reports')
        delivery = (s.get('report_delivery') or {}).get('message')
        if delivery:
            self.paint.paragraph(delivery, 'body', self.u(16), MUTED, self.X(48), self.Y(406), self.u(1184), 2)
        self.button('back', 'Back', 48, 600, 380, 64)

    def page_about(self):
        self.label('About VROSTBridge', 48, 162, 34, TEXT, 'heading')
        self.label('VROSTBridge Client ' + CLIENT_VERSION + '  ·  An open-source remote PCVR bridge',
                   48, 196, 17, CYAN, 'medium', width=1184)
        credits = [('ALVR', 'streaming, tracking and audio (MIT)'), ('Valve OpenVR', 'headset integration (BSD 3-Clause)'),
                   ('FFmpeg', 'Linux video decoding (LGPL 2.1+)'), ('Inter and Outfit', 'typefaces (SIL OFL 1.1)'),
                   ('stb', 'font and image rendering (public domain / MIT)'), ('SDL2', 'this screen (zlib)'),
                   ('Wayland', 'display leasing (MIT); Bazzite gamescope: system compositor (BSD)'),
                   ('Python and PyInstaller', 'the companion'),
                   ('Eye tracking', 'go-bsb-cams (GPL 2.0, modified); Baballonia, downloaded on request (non-commercial licence)')]
        y = 248
        for name, what in credits:
            width = self.label(name, 48, y, 18, TEXT, 'strong')
            self.label(what, 48 + width / self.k + 12, y, 17, MUTED, 'body', width=1000)
            y += 31
        # The short independent-project notice (the brand pack's wording) and the licence, in three lines.
        self.paint.paragraph('Free software by JagerBombastic, MIT licence. Full credits and licences: THIRD_PARTY.md and '
                             'docs/OPEN_SOURCE_CREDITS.md. VROSTBridge is an independent open-source project, not '
                             'affiliated with or endorsed by Valve, Bigscreen, ALVR or other hardware or platform '
                             'manufacturers. Third-party names and marks belong to their respective owners.',
                             'body', self.u(15), FAINT, self.X(48), self.Y(y + 10), self.u(1184), 3)
        self.button('back', 'Back', 48, 600, 380, 64)

    def page_exit(self):
        self.label('End the session and close VROSTBridge?', 640, 300, 34, TEXT, 'heading', 'center')
        self.label('SteamVR closes on both PCs.', 640, 344, 18, MUTED, 'body', 'center')
        self.button('back', 'Keep playing', 240, 400, 380, 80)
        self.button('exit-confirm', 'Stop and exit', 660, 400, 380, 80, primary=True)

    def render(self):
        self.setup_scale()
        width, height = self.size
        self.s.SetRenderDrawColor(self.renderer, 0x0B, 0x0C, 0x12, 255)
        self.s.RenderClear(self.renderer)
        self.paint.image(self.background, 0, 0, width, height, cover=True)
        self.buttons = []
        self.header()
        wanted = self.focus_name
        getattr(self, 'page_' + self.page, self.page_main)()
        names = [button[0] for button in self.buttons]
        if wanted in names and (self.focus >= len(names) or names[self.focus] != wanted):
            self.focus = names.index(wanted)
        if self.focus >= len(self.buttons) or not self.buttons[self.focus][3]:
            self.focus = next((i for i, b in enumerate(self.buttons) if b[3]), 0)
        self.focus_name = self.buttons[self.focus][0] if self.focus < len(self.buttons) else None
        message = self.error or self.state.get('startup_error') or self.notice or \
            ((self.state.get('session_link') or {}).get('host') or {}).get('runtime', {}).get('audio', {}).get('warning')
        if message:
            error = bool(self.error or self.state.get('startup_error'))
            self.paint.panel(self.X(48), self.Y(676), self.u(1184), self.u(54), self.u(14),
                             fill=(0x2A1020E8, 0x2A1020E8) if error else (0x0F2230E8, 0x0F2230E8),
                             border=MAGENTA if error else CYAN, border_width=self.u(1.5))
            self.label(str(message), 72, 710, 16, BAD if error else 0xBDEBFFFF, 'medium', width=1136)
        hints = (('A', 'Select', OK),) if self.page == 'main' else (('A', 'Select', OK), ('B', 'Back', MAGENTA))
        listing = any(self._row(button) for button in self.buttons)
        x = (404 if listing else 474) if len(hints) > 1 else 540
        for letter, what, color in hints:
            self.glyph(letter, x, 760, color)
            x += 22 + self.label(what, x + 22, 766, 16, MUTED, 'medium') / self.k + 34
        x += 4 + self.label('D-pad  Move', x + 4, 766, 16, MUTED, 'medium') / self.k
        if listing:  # 1.0.0.4: left and right change the focused setting
            self.label('◀ ▶  Change', x + 34, 766, 16, MUTED, 'medium')
        snapshot = os.environ.get('VROSTBRIDGE_GAME_UI_SNAPSHOT')
        if snapshot:  # documentation screenshots and tests: one frame to a PPM file, then exit
            pixels = c.create_string_buffer(width * height * 4)
            if not self.s.RenderReadPixels(self.renderer, None, 0x16762004, pixels, width * 4):
                rgb = bytearray(width * height * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = pixels.raw[0::4], pixels.raw[1::4], pixels.raw[2::4]
                Path(snapshot).write_bytes(b'P6\n%d %d\n255\n' % (width, height) + bytes(rgb))
            self.stopped.set()
        self.s.RenderPresent(self.renderer)

    # ------------------------------------------------------------------ loop
    def open_controllers(self):
        """Open game controllers that appeared and close those that went (1.0.0.4: never all of them every few
        seconds, which could lose a button's release and so leave a direction repeating)."""
        for instance, handle in list(self.controllers.items()):
            if not self.s.GameControllerGetAttached(handle):
                self.s.GameControllerClose(handle)
                del self.controllers[instance]
        for index in range(self.s.NumJoysticks()):
            if not self.s.IsGameController(index):
                continue
            instance = self.s.JoystickGetDeviceInstanceID(index)
            if instance not in self.controllers:
                handle = self.s.GameControllerOpen(index)
                if handle:
                    self.controllers[instance] = handle

    def held(self, source):
        """Whether a press is still down: a controller button is checked (its release may have been missed)."""
        if source[0] != 'pad':
            return True
        handle = self.controllers.get(source[1])
        return bool(handle and self.s.GameControllerGetButton(handle, source[2]))

    def run(self):
        self.s = SDL(self.root)
        self.window = self.renderer = None
        self.s.SetHint(b'SDL_RENDER_SCALE_QUALITY', b'1')
        self.s.SetHint(b'SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR', b'0')
        if self.s.Init(0x20 | 0x2000):
            raise RuntimeError(self.s.error())
        self.ink = Ink(self.root)
        try:
            # In Game Mode: full screen at the resolution gamescope gives the app (the display's own
            # when the game's resolution is set to Native). On the desktop: a resizable window.
            flags = (0x1001 | 0x4) if in_game_mode() else (0x4 | 0x20)
            self.window = self.s.CreateWindow(b'VROSTBridge', 0x2fff0000, 0x2fff0000, 1280, 800, flags)
            if not self.window:
                raise RuntimeError(self.s.error())
            self.renderer = self.s.CreateRenderer(self.window, -1, 2 | 4) or self.s.CreateRenderer(self.window, -1, 1)
            if not self.renderer:
                raise RuntimeError(self.s.error())
            self.s.SetRenderDrawBlendMode(self.renderer, 1)
            self.paint = Painter(self.s, self.renderer, self.ink)
            brand = next((d for d in (self.root / 'brand', Path(__file__).with_name('brand')) if d.is_dir()),
                         self.root / 'brand')
            self.logo = brand / 'steam' / 'icon.png'
            self.name_art = brand / 'vrostbridge-name.png'
            try:  # its width for its height, from the PNG header
                head = self.name_art.read_bytes()[:24]
                self.name_aspect = int.from_bytes(head[16:20], 'big') / int.from_bytes(head[20:24], 'big')
            except (OSError, ZeroDivisionError):
                self.name_art = None
            self.background = Path(__file__).with_name('game_ui_assets') / 'background.jpg'

            def poll():
                while not self.stopped.is_set():
                    try:
                        self.state = self.status_fn()
                    except Exception as error:
                        self.error = str(error)
                    self.refresh.wait(.5)
                    self.refresh.clear()
            threading.Thread(target=poll, daemon=True).start()
            event = c.create_string_buffer(64)
            next_paint = next_controllers = 0.0
            while not self.stopped.is_set():
                now = time.monotonic()
                dirty = False
                if now >= next_controllers:
                    self.open_controllers()
                    next_controllers = now + 2
                while self.s.PollEvent(event):
                    raw = event.raw
                    kind = int.from_bytes(raw[:4], 'little')
                    dirty = True
                    if kind == 0x100:  # quit
                        self.choose('exit')
                    elif kind in (0x300, 0x301):  # key down / up
                        key = int.from_bytes(raw[20:24], 'little', signed=True)
                        if key in ARROWS:  # held arrows repeat on our own timing, not the system's
                            if kind == 0x301:
                                self.release(ARROWS[key], ('key', key))
                            elif not raw[13]:
                                self.press(ARROWS[key], ('key', key), now)
                        elif kind == 0x301 or raw[13]:
                            pass
                        elif key == 9:
                            self.move(1, 0)
                        elif key in (13, 32):
                            self.activate()
                        elif key == 27:
                            self.back()
                        elif self.page == 'host' and (48 <= key <= 57 or key == 46):
                            self.choose('key:' + chr(key))
                        elif self.page == 'host' and key == 8:
                            self.choose('erase')
                    elif kind in (0x651, 0x652):  # controller button down / up
                        instance, button = int.from_bytes(raw[8:12], 'little', signed=True), raw[12]
                        if button in DPAD:
                            if kind == 0x652:
                                self.release(DPAD[button], ('pad', instance, button))
                            else:
                                self.press(DPAD[button], ('pad', instance, button), now)
                        elif kind == 0x652:
                            pass
                        elif button == 0:
                            if now - self.last_move.get('a', -1.0) >= DUPLICATE_S:  # one press, two controllers
                                self.last_move['a'] = now
                                self.activate()
                        elif button == 1:  # B: up one level, never Exit (RC4.2 exited from the main page)
                            if now - self.last_move.get('b', -1.0) >= DUPLICATE_S:
                                self.last_move['b'] = now
                                self.back()
                    elif kind == 0x401 and raw[16] == 1:  # left mouse button (and touch)
                        x = int.from_bytes(raw[20:24], 'little', signed=True)
                        y = int.from_bytes(raw[24:28], 'little', signed=True)
                        index = self.hit(x, y)
                        if index is not None:
                            self.focus, self.focus_name = index, self.buttons[index][0]
                            self.activate()
                    elif kind == 0x200:  # window event (resize)
                        dirty = True
                if self.repeat(now, self.held):
                    dirty = True
                # The stick of whichever controller is pushed furthest.
                axes = [(self.s.GameControllerGetAxis(handle, 0), self.s.GameControllerGetAxis(handle, 1))
                        for handle in self.controllers.values()]
                x, y = max(axes, key=lambda pair: max(abs(pair[0]), abs(pair[1])), default=(0, 0))
                if self.stick(x, y, now):
                    dirty = True
                if dirty or now >= next_paint:
                    self.render()
                    next_paint = now + (.05 if self.page == 'eyes' else .5)  # 1.0.0.3: the eye view 20 times a second
                self.s.Delay(16)
        finally:
            for controller in self.controllers.values():
                self.s.GameControllerClose(controller)
            self.controllers = {}
            if getattr(self, 'paint', None):
                self.paint.clear()
            if self.renderer:
                self.s.DestroyRenderer(self.renderer)
            if self.window:
                self.s.DestroyWindow(self.window)
            self.ink.close()
            self.s.Quit()


def run(root, status, action, report, stopped, fovea=None):
    View(root, status, action, report, stopped, fovea).run()
