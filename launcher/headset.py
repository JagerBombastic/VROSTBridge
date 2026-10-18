"""Bounded physical-headset metadata and explicit stream-size selection."""
import json
import math
import re
import struct

TEXT_FIELDS = ('model', 'manufacturer', 'tracking_system', 'serial', 'render_model',
               'registered_type', 'input_profile', 'icon_ready', 'icon_off',
               'icon_searching', 'icon_alert')
RESOLUTIONS = ('compatibility', 'sharp', 'recommended', 'beyond-balanced', 'beyond-detail',
               'beyond-90', 'index-80', 'index-90', 'index-120', 'index-144')
CLIENT_RESOLUTIONS = ('automatic',) + RESOLUTIONS
# 1.0.0.13: the stream follows the display mode of the headsets VROSTBridge knows, and the choices are offered before
# a session as well as after. The Beyond 2 and 2e show 2560 x 2560 per eye at 75 Hz and take 1920 x 1920 at 90 Hz,
# which the headset upscales; only Bigscreen's Beyond Utility on a Windows PC switches the mode, so the Beyond offers
# no choice: it streams the mode it is in ('beyond-balanced' or 'beyond-90' on the wire, automatic_mode()). The Index
# shows its panels at 80, 90, 120 or, experimentally, 144 Hz, each a choice set in SteamVR for the session (MODES), and
# streams 2016 x 2240 per eye, the size SteamVR renders for its lenses (the old High). Other headsets keep the sizes
# (Custom or Standard, High, SteamVR app size). A saved choice the connected headset does not offer follows its mode,
# as Auto does.
MODES = {
    'index-80': {'family': 'index', 'width': 2016, 'refresh_hz': 80, 'label': 'Native · 80 Hz'},
    'index-90': {'family': 'index', 'width': 2016, 'refresh_hz': 90, 'label': 'Native · 90 Hz'},
    'index-120': {'family': 'index', 'width': 2016, 'refresh_hz': 120, 'label': 'Native · 120 Hz'},
    'index-144': {'family': 'index', 'width': 2016, 'refresh_hz': 144, 'label': 'Native · 144 Hz (experimental)',
                  'experimental': True},
}
# The choices besides Auto of each headset whose stream follows its display mode: none on a Beyond.
FAMILY_MODES = {'beyond': (), 'index': tuple(MODES)}
# Every choice some headset offers (offered_resolutions()); the other RESOLUTIONS are only sent to the host.
CHOICES = ('automatic', 'compatibility', 'sharp', 'recommended') + tuple(MODES)
FAMILY_NAMES = {'beyond': 'Bigscreen Beyond', 'index': 'Valve Index'}
# A known headset seen only by its display (Linux EDID, before SteamVR has described it): its maker and model as
# SteamVR names them, the render size's aspect and the rate shown until the session measures it.
DISPLAY_ONLY = {'beyond': ('Bigscreen', 'Beyond', 2560, 2560, 75.0), 'index': ('Valve', 'Index', 2016, 2240, 90.0)}
CLIENT_CODECS = ('Auto', 'H264', 'Hevc', 'AV1')
# Foveated encoding: the host sends the edges of each eye at reduced resolution and
# this client restores the full image, so the same bitrate and decoder budget go to
# the part of the image the user actually sees sharply. Sizes are per axis, as a
# fraction of the eye image kept at full resolution; edge ratio is how much the rest
# is reduced. Eye-tracked moves the sharp region with the user's gaze (Beyond 2e).
FOVEATION_PRESETS = {
    'subtle': {'center_size': [0.70, 0.65], 'edge_ratio': [2.0, 2.0]},
    'balanced': {'center_size': [0.55, 0.50], 'edge_ratio': [3.0, 3.0]},
    'eye-tracked': {'center_size': [0.40, 0.40], 'edge_ratio': [4.0, 4.0]},
}
FOVEATION_CHOICES = ('auto', 'off', 'subtle', 'balanced', 'eye-tracked')
FOVEATION_LABELS = {'auto': 'Auto', 'off': 'Off', 'subtle': 'Subtle', 'balanced': 'Balanced',
                    'eye-tracked': 'Eye-tracked'}
# RC4.3: with eye tracking the sharp region follows the gaze, so its size (per axis, as a
# fraction of each eye's image) and how much the rest is reduced can be chosen. Medium and
# Balanced are the RC4 eye-tracked preset. A smaller region or a lighter outside sends fewer
# pixels, which lightens the host's encoder, the network and this PC's decoder, and leaves
# room for a higher stream resolution.
FOVEATION_REGIONS = {'small': 0.30, 'medium': 0.40, 'large': 0.55}
FOVEATION_REGION_LABELS = {'small': 'Small', 'medium': 'Medium', 'large': 'Large'}
FOVEATION_EDGES = {'detail': 3.0, 'balanced': 4.0, 'light': 5.0}
FOVEATION_EDGE_LABELS = {'detail': 'More detail · ⅓', 'balanced': 'Balanced · ¼', 'light': 'Lightest · ⅕'}
# RC4.3: sharpening of the picture's detail when it is shown (fused frame pass), as the
# percentage of the unsharp amount the native client takes (--sharpening, 'sharpen N').
SHARPENING_LEVELS = {'off': 0, 'low': 35, 'medium': 70, 'high': 120}
SHARPENING_LABELS = {'off': 'Off', 'low': 'Low', 'medium': 'Medium', 'high': 'High'}


def headset_family(headset):
    """Classify only the detected runtime identity; do not guess a model revision."""
    name = (headset['manufacturer'] + ' ' + headset['model']).casefold()
    if 'beyond' in name:
        return 'beyond'
    if 'index' in name and ('valve' in name or headset['model'].casefold() == 'index'):
        return 'index'
    return 'other'


def client_stream_preferences(value):
    """Validate persistent UI preferences before the physical headset is awake."""
    if not isinstance(value, dict) or set(value) - {'codec', 'bitrate', 'resolution'}:
        raise ValueError('Unsupported client stream setting.')
    codec = value.get('codec', 'Auto')
    bitrate = value.get('bitrate', 80)
    resolution = value.get('resolution', 'automatic')
    if codec not in CLIENT_CODECS or type(bitrate) is not int or not 10 <= bitrate <= 300:
        raise ValueError('Choose Auto, H.264, HEVC or AV1 and a bitrate from 10 to 300 Mbit/s.')
    if resolution not in CLIENT_RESOLUTIONS:
        raise ValueError('Choose a supported stream resolution.')
    # 1.0.0.13: no choice needs HEVC any more. The Beyond's 2560 and 3200 sizes were the ones wider than H.264's 2048
    # pixels per eye; saved, they follow the Beyond's mode, which keeps H.264 within its limit (automatic_mode()).
    return {'codec': codec, 'bitrate': bitrate, 'resolution': resolution}


DECODER_PROFILES = ('h264', 'hevc', 'hevc_main10', 'av1', 'av1_10bit')


def parse_decoder_profiles(line):
    """1.0.0.14: the native client's decoder-profile line (Windows: 'STATUS SteamVR's GPU for D3D11VA: … decoder
    profiles h264=1 hevc=1 …'; Linux: 'STATUS VA-API decoder profiles on /dev/dri/renderD128: h264=1 …') as
    {'backend', 'device', 'h264', …}, or None for any other line."""
    if not isinstance(line, str) or 'decoder profiles' not in line or not line.startswith('STATUS '):
        return None
    head, _, tail = line.partition('decoder profiles')
    found = dict(re.findall(r'\b(h264|hevc|hevc_main10|av1|av1_10bit)=([01])\b', tail))
    if 'hevc' not in found:
        return None
    result = {key: found.get(key) == '1' for key in DECODER_PROFILES}
    result['backend'] = 'D3D11VA' if 'D3D11VA' in head else 'VA-API' if 'VA-API' in head else 'unknown'
    device = re.search(r'D3D11VA: (.+?) \(DXGI adapter', head) or re.search(r' on (/dev/dri/\S+):', tail)
    result['device'] = device.group(1)[:96] if device else ''
    return result


def automatic_codec(decoders=None):
    """1.0.0.14: the codec Auto means: HEVC (10-bit where the decoder has it), for every headset. Until 1.0.0.13 Auto
    was H.264 on everything but a Beyond, a choice from the days of weak decoders that left Index users with the
    weakest codec and no 10-bit. H.264 only where the headset PC's decoder was found to lack HEVC (the native
    client's decoder profiles, remembered by the companion)."""
    if isinstance(decoders, dict) and decoders.get('hevc') is False and decoders.get('h264') is not False:
        return 'H264'
    return 'Hevc'


def family_headset(family):
    """1.0.0.13: a known headset seen only by its display, described as SteamVR would before it has (DISPLAY_ONLY)."""
    manufacturer, model, width, height, hz = DISPLAY_ONLY[family]
    return headset_metadata(dict({key: '' for key in TEXT_FIELDS}, manufacturer=manufacturer, model=model,
                                 width=width, height=height, refresh_hz=hz))


def automatic_mode(family, refresh_hz, codec='Hevc'):
    """1.0.0.13: the stream for the mode a Beyond or an Index runs: a Beyond at 90 Hz is in its 1920 mode, at 75 Hz in
    its 2560 one; an Index streams 2016 at any rate. H.264 keeps a Beyond within its 2048-pixel limit."""
    if family == 'beyond':
        return 'beyond-90' if refresh_hz >= 82.5 or codec == 'H264' else 'beyond-balanced'
    if family == 'index':
        return min(FAMILY_MODES['index'], key=lambda key: abs(MODES[key]['refresh_hz'] - refresh_hz))
    return None


def wire_resolution(resolution):
    """The stream size as hosts before 1.0.0.13 name it: an Index mode is 'sharp' (2016), its old High."""
    mode = MODES.get(resolution)
    return 'sharp' if mode and mode['family'] == 'index' else resolution


def requested_refresh(resolution, family):
    """1.0.0.13: the refresh rate (Hz) to ask SteamVR on this PC for: a chosen Index rate, on Bazzite and Windows alike
    (steamvr.preferredRefreshRate). None leaves the headset as it is set: Auto, other sizes and headsets, and a Beyond
    always, whose mode only Bigscreen's Beyond Utility on a Windows PC switches."""
    mode = MODES.get(resolution)
    return float(mode['refresh_hz']) if mode and mode['family'] == family else None


def refresh_text(family):
    """1.0.0.13: how the stream and the headset's refresh rate are chosen for this headset, in plain words."""
    if family == 'index':
        return ('A rate chosen here is set in SteamVR on this PC for the session, and your own setting comes back after '
                'it. Auto keeps the rate SteamVR is set to. 144 Hz is experimental: not every graphics card drives it.')
    if family == 'beyond':
        return ("The stream follows the Beyond's display mode: 2560 x 2560 per eye at 75 Hz, or 1920 x 1920 at 90 Hz, "
                "which the headset upscales. VROSTBridge does not switch the mode: set it with Bigscreen's Beyond Utility "
                'on a Windows PC.')
    return "The headset's refresh rate is used as it is set on the headset."


# 1.0.0.13: the lighter choice when the headset PC's graphics fall behind a Beyond streaming 2560 at 75 Hz is its
# other mode, which VROSTBridge cannot switch (1920^2 x 90 is about two thirds of 2560^2 x 75 pixels a second).
BEYOND_LIGHTER_MODE = ("The Beyond's 90 Hz mode is lighter for this PC: 1920 x 1920 per eye, about a third fewer "
                       "pixels a second. Switch it with Bigscreen's Beyond Utility on a Windows PC.")


def offered_resolutions(family):
    """1.0.0.13: the stream choices for a kind of headset: Auto only on a Beyond (it follows the headset's mode), Auto
    and the rates on an Index, Auto and the sizes on others."""
    if family in FAMILY_MODES:
        return ('automatic',) + FAMILY_MODES[family]
    return ('automatic', 'compatibility', 'sharp', 'recommended')


def resolve_stream_preferences(value, headset):
    """Resolve Auto once from fresh metadata; never save guesses over preferences."""
    physical = headset_metadata(headset)
    result = client_stream_preferences({key: value[key] for key in ('codec', 'bitrate', 'resolution') if key in value})
    family = headset_family(physical)
    # 1.0.0.13: a choice this headset does not offer (another headset's, or a size gone in 1.0.0.13) follows its mode,
    # as Auto does, and stays saved for the headset it was made for.
    chosen = result['resolution'] if result['resolution'] in offered_resolutions(family) else 'automatic'
    if result['codec'] == 'Auto':
        result['codec'] = automatic_codec(value.get('decoder_profiles'))
    if chosen == 'automatic':
        # Conservative for other headsets; the full runtime recommendation stays selectable. Never equate the render
        # recommendation with panel pixels. 1.0.0.13: a Beyond or an Index streams the mode it runs (until 1.0.0.12
        # Auto was 2016 on both: since 1.0.0.11's lighter frame pass a handheld headset PC holds the Beyond's 2560 at
        # 75 Hz).
        chosen = automatic_mode(family, physical['refresh_hz'], result['codec']) or 'compatibility'
    result['resolution'] = wire_resolution(chosen)
    result['headset'] = physical
    result['microphone'] = value.get('microphone', False)
    return result


def display_status(preferences, headset, source='detected'):
    """Read-only display information for both desktop and Game Mode controls.

    source (1.0.0.13) says where the headset description comes from: 'detected' (SteamVR, this run), 'last' (the last
    verified session) or 'display' (a known headset seen only by its display, family_headset()). The choices are
    offered from any of them, so a stream size can be chosen before a session starts."""
    catalog = {'resolution_options': {codec: resolution_options(dict(preferences, codec=codec), headset, source)
                                      for codec in CLIENT_CODECS},
               'selected': selected_resolution(preferences, headset)}
    if not headset:
        return dict(catalog, detected=False, source=None, refresh_text=refresh_text(None),
                    message='Headset detected automatically on Start. Saved stream quality is retained.')
    try:
        physical = headset_metadata(headset)
    except ValueError:
        return dict(catalog, detected=False, source=None, refresh_text=refresh_text(None),
                    message='Waiting for a valid headset description from SteamVR.')
    family = headset_family(physical)
    result = dict(catalog, **{'detected': source != 'display', 'source': source, 'model': physical['model'],
              'family': family, 'refresh_hz': physical['refresh_hz'], 'recommended_width': physical['width'],
              'recommended_height': physical['height'], 'refresh_text': refresh_text(family),
              # 1.0.0.13: no choice to make: the stream follows the headset's mode (a Beyond).
              'resolution_fixed': offered_resolutions(family) == ('automatic',)})
    if source == 'display':
        result['message'] = (FAMILY_NAMES[family] + ' connected (known from its display). SteamVR describes it on '
                             'Start' + ('; the stream follows the mode it is in.' if family == 'beyond'
                                        else '; sizes are checked then.'))
    try:
        selected = resolve_stream_preferences(preferences, physical)
        width, height = stream_size(selected['codec'], selected['resolution'], physical)
        result.update(codec=selected['codec'], resolution=selected['resolution'],
                      stream_width=width, stream_height=height, encoded_width=width * 2,
                      encoded_height=height)
    except ValueError as error:
        result['message'] = str(error)
    return result


def selected_resolution(preferences, headset):
    """1.0.0.13: the choice the controls show: the saved one, or Auto when this headset does not offer it (it follows
    the headset's mode, as Auto does; on a Beyond always)."""
    current = preferences.get('resolution', 'automatic')
    if current not in CHOICES:
        return 'automatic'  # the Beyond's sizes until 1.0.0.12: it follows its mode now
    try:
        family = headset_family(headset_metadata(headset))
    except ValueError:
        return current
    return current if current in offered_resolutions(family) else 'automatic'


def stream_size(codec, resolution, headset):
    """Expected negotiated eye size, following ALVR's f32 aspect/alignment path.

    The decoded video dimensions remain authoritative during a session. These
    are requested render/encode pixels, never a report of physical panel pixels.
    """
    physical = headset_metadata(headset)
    width = stream_width(codec, resolution, physical)
    f32 = lambda value: struct.unpack('f', struct.pack('f', value))[0]
    height = math.floor(f32(f32(width * physical['height']) / physical['width']) / 32) * 32
    if height > 8192:
        ratio = f32(f32(width) / f32(height))
        width = math.floor(f32(8192 * ratio) / 32) * 32
        height = 8192
    return width, height


def resolution_options(preferences, headset=None, source='detected'):
    """Shared, model-aware UI labels and sizes for the desktop page and Game Mode.

    1.0.0.13: offered before a session too, from the last verified headset or a known headset's display. An Index
    offers Auto and its rates (MODES); a Beyond only the mode it is in, which is set outside VROSTBridge; other
    headsets the sizes; with no headset known, the sizes without dimensions. A saved choice the headset does not
    offer follows its mode (selected_resolution()).
    """
    try:
        physical = headset_metadata(headset)
    except ValueError:
        physical = None
    family = headset_family(physical) if physical else None
    codec = preferences.get('codec', 'Auto')
    effective_codec = automatic_codec(preferences.get('decoder_profiles')) if codec == 'Auto' else codec
    current = preferences.get('resolution', 'automatic')
    labels = {'automatic': 'Auto', 'sharp': 'High', 'compatibility': 'Standard' if effective_codec == 'H264' else 'Custom',
              'recommended': 'SteamVR app size', **{key: mode['label'] for key, mode in MODES.items()}}
    if family == 'beyond':
        # The mode it is in, as SteamVR measured it (not yet known from its display alone).
        labels['automatic'] = 'Set by the headset' + ('' if source == 'display' else
                                                      ' · ' + str(round(physical['refresh_hz'])) + ' Hz')
    elif family == 'index':
        labels['automatic'] = 'Auto · as SteamVR is set'
    ids = list(offered_resolutions(family))
    if family not in FAMILY_MODES and effective_codec != 'H264':
        ids = ['automatic', 'sharp', 'compatibility', 'recommended']
    if current in CLIENT_RESOLUTIONS and current not in ids and selected_resolution(preferences, physical) == current:
        ids.append(current)  # only with no headset known: checked on Start
    options = []
    for preset in ids:
        option = {'id': preset, 'label': labels[preset], 'available': True}
        if MODES.get(preset, {}).get('experimental'):
            option['experimental'] = True
        if MODES.get(preset):
            option['refresh_hz'] = MODES[preset]['refresh_hz']
        if not physical:
            option['label'] = ('Auto - detect on Start' if preset == 'automatic'
                               else labels[preset] + ' - size checked on Start')
        elif preset == 'automatic' and family == 'beyond' and source == 'display':
            option['label'] += ' - size checked on Start'  # the mode it runs is known once SteamVR describes it
        else:
            try:
                selected = resolve_stream_preferences(dict(preferences, resolution=preset), physical)
                width, height = stream_size(selected['codec'], selected['resolution'], physical)
                option.update(width=width, height=height, encoded_width=width * 2,
                              encoded_height=height, codec=selected['codec'])
                option['label'] += f' - {width} x {height} per eye'
                if preset == 'recommended':
                    option['label'] += ' (capped)'
            except ValueError as error:
                option.update(available=False, message=str(error))
        options.append(option)
    return options


def headset_metadata(value):
    if not isinstance(value, dict) or set(value) != set(TEXT_FIELDS) | {'width', 'height', 'refresh_hz'}:
        raise ValueError('Invalid physical headset description. Update both VROSTBridge installations.')
    result = {}
    for key in TEXT_FIELDS:
        text = value[key]
        # The pinned host property bridge has a 256-byte string buffer.
        if not isinstance(text, str) or len(text.encode('utf-8')) > 192 or any(ord(c) < 32 for c in text):
            raise ValueError('Invalid headset property: ' + key)
        result[key] = text
    if not result['model']:
        raise ValueError('SteamVR has not identified the physical headset yet.')
    for key in ('width', 'height'):
        size = value[key]
        if type(size) is not int or not 32 <= size <= 8192:
            raise ValueError('Invalid recommended headset render size.')
        result[key] = size
    hz = value['refresh_hz']
    if type(hz) not in (int, float) or not math.isfinite(hz) or not 30 <= hz <= 240:
        raise ValueError('Invalid physical headset refresh rate.')
    result['refresh_hz'] = float(hz)
    return result


def parse_headset(output):
    lines = [line[8:] for line in output.splitlines() if line.startswith('HEADSET ')]
    if len(lines) != 1:
        raise ValueError('The native client did not report its headset. Repair or update the client installation.')
    return headset_metadata(json.loads(lines[0]))


def stream_width(codec, resolution='compatibility', headset=None):
    if codec not in ('H264', 'Hevc', 'AV1') or resolution not in RESOLUTIONS:
        raise ValueError('Invalid stream quality selection.')
    if resolution == 'compatibility':
        width = 1440 if codec == 'H264' else 2144
    elif resolution == 'sharp':
        width = 2016
    elif resolution in ('beyond-balanced', 'beyond-detail', 'beyond-90'):
        if resolution in ('beyond-balanced', 'beyond-detail') and codec == 'H264':
            raise ValueError('Beyond 2560 and 3200 stream sizes require HEVC or AV1.')
        if headset is None or headset_family(headset_metadata(headset)) != 'beyond':
            raise ValueError('The physical Beyond headset must be ready before choosing its stream preset.')
        # This application recommendation is scaled for the client GPU, which
        # only decodes the stream. It must not cap the remote rendering PC's
        # chosen budget. ALVR retains the measured runtime aspect ratio; these
        # widths describe stream targets. 1.0.0.13: 1920 is what the 90 Hz mode's panels take.
        width = {'beyond-balanced': 2560, 'beyond-detail': 3200, 'beyond-90': 1920}[resolution]
    elif resolution.startswith('index-'):
        if headset is not None and headset_family(headset_metadata(headset)) != 'index':
            raise ValueError('An Index display mode is selected for another headset.')
        width = 2016  # 1.0.0.13: the Index's native stream, the size SteamVR renders for its lenses
    else:
        if headset is None:
            raise ValueError('The physical headset must be ready before selecting its recommended resolution.')
        width = headset_metadata(headset)['width']
    # H.264 stereo is side by side: stay within a 4096-wide coded frame.
    # HEVC and AV1 are limited here to an 8192-wide frame. Actual driver support
    # still requires physical validation; a limit is not a capability guarantee.
    return max(32, min(width, 2048 if codec == 'H264' else 4096) // 32 * 32)


def foveation_choice(choice, family, gaze):
    """The preset a foveation choice means for this headset ('off' or a FOVEATION_PRESETS key).

    Auto keeps a margin that is invisible in normal use: subtle for known headsets,
    and eye-tracked on a Beyond whose eye tracking is on. Eye-tracked without gaze
    falls back to balanced, which does not depend on where the user looks.
    """
    if choice not in FOVEATION_CHOICES:
        choice = 'auto'
    if choice == 'auto':
        if family == 'beyond' and gaze:
            return 'eye-tracked'
        return 'subtle' if family in ('index', 'beyond') else 'off'
    if choice == 'eye-tracked' and not gaze:
        return 'balanced'
    return choice


def foveation_parameters(preset, gaze=False, features=(), region='medium', edge='balanced'):
    """Session foveation settings for a preset, or None when off.

    With a host that centres the fixed region on each eye's straight-ahead direction
    ('optical-centre'), the negotiated shift only covers the moment before the first
    frame; without it the centre stays on the image centre. The eye-tracked preset takes
    the chosen sharp-region size and outside reduction (RC4.3).
    """
    if preset not in FOVEATION_PRESETS:
        return None
    value = FOVEATION_PRESETS[preset]
    centre, ratio = list(value['center_size']), list(value['edge_ratio'])
    if preset == 'eye-tracked':
        size, reduction = FOVEATION_REGIONS.get(region, 0.40), FOVEATION_EDGES.get(edge, 4.0)
        centre, ratio = [size, size], [reduction, reduction]
    return {'center_size': centre, 'center_shift': [0.0, 0.0],
            'edge_ratio': ratio,
            'gaze': bool(gaze) and preset == 'eye-tracked' and 'gaze-foveation' in features,
            'optical_centre': 'optical-centre' in features}


def _f32(value):
    """Round to the nearest 32-bit float, as ALVR's Rust f32 arithmetic does."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


def foveated_size(width, height, foveation):
    """Encoded eye size for a foveated stream, exactly as ALVR negotiates it.

    1.0.0.6: ALVR (alvr/server_core/src/connection.rs) works in f32 with a few f64 intermediates and rounds
    each axis up to 32 pixels. Doing the same sums in f64 here came out a hair above a multiple of 32 in some
    cases (2560 at 55 % and a third: 1792.0000000000002) and was then rounded up a whole step too far (1824,
    where the host really sends 1792, as the RC4.4 test showed).
    """
    if not foveation:
        return width, height
    sizes = []
    for resolution, centre, edge in zip((width, height), foveation['center_size'], foveation['edge_ratio']):
        resolution, centre, edge = _f32(resolution), _f32(centre), _f32(edge)
        edge_size = _f32(resolution - _f32(centre * resolution))
        aligned = _f32(1.0 - math.ceil(edge_size / (edge * 2.0)) * (edge * 2.0) / resolution)
        scale = _f32(aligned + (1.0 - aligned) / edge)
        optimized = _f32(scale * resolution)
        sizes.append(int(math.ceil(_f32(optimized / 32.0))) * 32)
    return sizes[0], sizes[1]
