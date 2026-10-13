"""Bounded physical-headset metadata and explicit stream-size selection."""
import json
import math
import struct

TEXT_FIELDS = ('model', 'manufacturer', 'tracking_system', 'serial', 'render_model',
               'registered_type', 'input_profile', 'icon_ready', 'icon_off',
               'icon_searching', 'icon_alert')
RESOLUTIONS = ('compatibility', 'sharp', 'recommended', 'beyond-balanced', 'beyond-detail')
CLIENT_RESOLUTIONS = ('automatic',) + RESOLUTIONS
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
    if resolution.startswith('beyond-') and codec == 'H264':
        raise ValueError('Beyond 2560 and 3200 stream sizes require HEVC or AV1. Select Auto, HEVC or AV1 for the video codec.')
    return {'codec': codec, 'bitrate': bitrate, 'resolution': resolution}


def resolve_stream_preferences(value, headset):
    """Resolve Auto once from fresh metadata; never save guesses over preferences."""
    physical = headset_metadata(headset)
    result = client_stream_preferences({key: value[key] for key in ('codec', 'bitrate', 'resolution') if key in value})
    family = headset_family(physical)
    if result['resolution'].startswith('beyond-') and family != 'beyond':
        raise ValueError('A Beyond stream preset is selected, but SteamVR detected ' + physical['model'] + '. Select Automatic headset or another stream size.')
    if result['codec'] == 'Auto':
        result['codec'] = 'Hevc' if family == 'beyond' else 'H264'
    if result['resolution'] == 'automatic':
        # Conservative starting budgets; the full runtime recommendation stays
        # selectable. Never equate the render recommendation with panel pixels.
        # RC4.3: the Beyond starts at Standard (2016) too. In the RC4.2 physical test a
        # headset PC with integrated Radeon graphics held 75 fps at 2016 but kept falling to half rate at
        # 2560 (Native since 1.0.0.11, High before), which stays one choice away: without eye tracking its
        # foveated stream carries about 60 % more pixels to decode than Standard's.
        result['resolution'] = 'sharp' if family in ('index', 'beyond') else 'compatibility'
    result['headset'] = physical
    result['microphone'] = value.get('microphone', False)
    return result


def display_status(preferences, headset):
    """Read-only display information for both desktop and Game Mode controls."""
    catalog = {'resolution_options': {codec: resolution_options(dict(preferences, codec=codec), headset)
                                      for codec in CLIENT_CODECS}}
    if not headset:
        return dict(catalog, detected=False, message='Headset detected automatically on Start. Saved stream quality is retained.')
    try:
        physical = headset_metadata(headset)
    except ValueError:
        return dict(catalog, detected=False, message='Waiting for a valid headset description from SteamVR.')
    result = dict(catalog, **{'detected': True, 'model': physical['model'], 'family': headset_family(physical),
              'refresh_hz': physical['refresh_hz'], 'recommended_width': physical['width'],
              'recommended_height': physical['height']})
    try:
        selected = resolve_stream_preferences(preferences, physical)
        width, height = stream_size(selected['codec'], selected['resolution'], physical)
        result.update(codec=selected['codec'], resolution=selected['resolution'],
                      stream_width=width, stream_height=height, encoded_width=width * 2,
                      encoded_height=height)
    except ValueError as error:
        result['message'] = str(error)
    return result


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


def resolution_options(preferences, headset=None):
    """Shared, model-aware UI labels; legacy IDs keep their exact saved budget.

    Do not replace a saved legacy preset with a differently sized 'equivalent'.
    Before detection, only Auto and the saved manual choice are meaningful.
    """
    try:
        physical = headset_metadata(headset)
    except ValueError:
        physical = None
    family = headset_family(physical) if physical else None
    codec = preferences.get('codec', 'Auto')
    effective_codec = ('Hevc' if family == 'beyond' else 'H264') if codec == 'Auto' else codec
    codec_label = {'H264': 'H.264', 'Hevc': 'HEVC', 'AV1': 'AV1'}
    current = preferences.get('resolution', 'automatic')
    # 1.0.0.11: the Beyond's 2560 is its panels' own resolution, so it is called Native (until 1.0.0.10: High).
    labels = {'automatic': 'Auto', 'sharp': 'Standard' if family == 'beyond' else 'High',
              'compatibility': 'Standard' if effective_codec == 'H264' and family != 'beyond' else 'Custom',
              'beyond-balanced': 'Native', 'beyond-detail': 'Ultra', 'recommended': 'SteamVR app size'}
    if not physical:
        ids = ['automatic']
    elif family == 'beyond':
        ids = ['automatic', 'sharp', 'beyond-balanced', 'beyond-detail', 'recommended']
    else:
        ids = (['automatic', 'compatibility', 'sharp', 'recommended'] if effective_codec == 'H264'
               else ['automatic', 'sharp', 'compatibility', 'recommended'])
    if current in CLIENT_RESOLUTIONS and current not in ids:
        ids.insert(-1 if len(ids) > 1 else len(ids), current)
    options = []
    for preset in ids:
        option = {'id': preset, 'label': labels[preset], 'available': True}
        if not physical:
            option['label'] = ('Auto - detect on Start' if preset == 'automatic'
                               else labels[preset] + ' - saved; size checked on Start')
        elif preset.startswith('beyond-') and family != 'beyond':
            option.update(label='Saved custom - different headset; choose Auto', available=False)
        else:
            required_hevc = preset.startswith('beyond-')
            candidate = dict(preferences, resolution=preset)
            if required_hevc and effective_codec == 'H264':
                candidate['codec'] = 'Hevc'
            try:
                selected = resolve_stream_preferences(candidate, physical)
                width, height = stream_size(selected['codec'], selected['resolution'], physical)
                option.update(width=width, height=height, encoded_width=width * 2,
                              encoded_height=height, codec=selected['codec'])
                option['label'] += f' - {width} x {height} per eye'
                if preset == 'recommended':
                    option['label'] += ' (capped)'
                elif required_hevc and effective_codec == 'H264':
                    option['label'] += ' (HEVC)'
                if required_hevc:
                    option['requires_codec'] = 'Hevc'
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
    elif resolution in ('beyond-balanced', 'beyond-detail'):
        if codec == 'H264':
            raise ValueError('Beyond 2560 and 3200 stream sizes require HEVC or AV1.')
        if headset is None or headset_family(headset_metadata(headset)) != 'beyond':
            raise ValueError('The physical Beyond headset must be ready before choosing its stream preset.')
        # This application recommendation is scaled for the client GPU, which
        # only decodes the stream. It must not cap the remote rendering PC's
        # chosen budget. ALVR retains the measured runtime aspect ratio; these
        # widths describe stream targets, not physical panel/display modes.
        width = 2560 if resolution == 'beyond-balanced' else 3200
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
