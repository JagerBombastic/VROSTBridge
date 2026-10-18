"""Per-session stream options for the patched host driver, and the host side of the network test.

The driver reads `vrostbridge-stream.json` next to session.json each time a client
connects (patches 0009-0012). It holds options that must not change ALVR's session
schema, so mixed VROSTBridge versions keep working:

* pacing_mbps    spread each video frame at the client's link rate, so a 10 Gbit/s
                 host does not overflow a slower link's buffers with frame bursts;
* intra_refresh  AMF gradual refresh (NVENC reads it from the session settings);
* gaze_foveation move the full-resolution region with the user's eye gaze;
* optical_centre without gaze, centre it on each eye's straight-ahead direction;
* host_prediction false only when the client turned motion prediction off
                 (patch 0014); the host otherwise predicts every pose once;
* gaze_hold_ms   1.0.0.4 (patch 0017): when the gaze pauses (a blink, the eye tracker
                 skipping pictures) the region stays where the user last looked for this
                 long before it returns to the fixed centre.
* gaze_comfort   1.0.0.5 (patch 0019): the region stays put while the gaze stays within
                 this fraction of the region's half-size from its middle, and moves,
                 centred on the gaze, once the gaze leaves that zone. Small eye movements
                 and tracker noise no longer move it, so its border changes far less often
                 (each move is also a cost for the encoder).
* fec_video_group, fec_audio_group  1.0.0.9 (patch 0021): one parity shard for every this
                 many data shards of a video frame or game-audio packet. A home network loses
                 a single packet now and then; without parity that costs a whole frame (a
                 keyframe request and a short freeze) or a piece of sound. With it the client
                 rebuilds the lost piece. Clients without patch 0021 ignore parity.

`vrostbridge-init.json` (patch 0015) is read once when SteamVR loads the driver: the
client display (view size, refresh rate) the headset client is about to advertise, so
the virtual headset starts with the negotiated configuration and the first connection
does not restart SteamVR. The driver records the outcome, with the nonce written here,
in `vrostbridge-init.applied.json` before it changes session.json.

Features are read from the installed driver's build manifest, so a host with an
older driver never advertises what it cannot do.
"""
import json
import os
from pathlib import Path
import secrets

from launcher.link_test import LinkTestSender, expected_duration, open_sender

CONFIG_FILE = 'vrostbridge-stream.json'
INIT_FILE = 'vrostbridge-init.json'
INIT_APPLIED_FILE = 'vrostbridge-init.applied.json'
# Patch prefix -> features it provides.
PATCH_FEATURES = {
    '0009-host-stream-config-and-pacing': ('pacing',),
    '0010-host-gaze-foveated-encoding': ('gaze-foveation',),
    '0011-host-amf-intra-refresh': ('intra-refresh',),
    '0012-host-foveation-optical-centre': ('optical-centre',),
    '0014-host-prediction-switch': ('prediction-switch',),
    '0015-host-init-config': ('init-config',),
    '0017-host-gaze-hold': ('gaze-hold',),
    '0018-host-exact-colour-and-pose-drops': ('exact-colour', 'pose-drops'),
    '0019-host-gaze-comfort': ('gaze-comfort',),
    # 1.0.0.6: the colour converter tried as plain SDR first, and how far the gaze and region reached.
    '0020-host-colour-variants-and-gaze-reach': ('colour-variants', 'gaze-reach'),
    # 1.0.0.9: parity for video and game audio, and the host's own game-audio capture.
    '0021-transport-forward-error-correction': ('parity',),
    '0023-host-game-audio-capture': ('audio-capture',),
}
# 1.0.0.4: how long the sharp region stays where the user last looked when the gaze pauses.
GAZE_HOLD_MS = 1000
# 1.0.0.5: the comfort zone, as a fraction of the sharp region's half-size.
GAZE_COMFORT = 0.5
# 1.0.0.9: data shards per parity shard. Video: 10 % more packets, and any one lost shard in each
# interleaved group comes back (a burst of up to one shard per group too). Game audio is small,
# so every shard is sent twice.
FEC_VIDEO_GROUP = 10
FEC_AUDIO_GROUP = 1
# 1.0.0.13: a wired path can lose packets in clumps, not one at a time (a network test on a wired link lost six at
# once, and 12 frames of a session were lost although every group got its parity). One parity shard per 5 data
# shards there: twice the groups, so a run of lost shards up to a fifth of the frame comes back, for 20 % more
# packets. Wi-Fi resends lost packets itself, and keeps one per 10. Older clients read the group count from
# each parity datagram, so they repair either.
FEC_VIDEO_GROUP_WIRED = 5
# Companion features that need no driver patch (upstream ALVR or companion only).
COMPANION_FEATURES = ('ten-bit', 'av1', 'foveation', 'link-test', 'quick-resume', 'host-power', 'stream-recovery')
EF_TOS = 46 << 2  # DSCP Expedited Forwarding, as the stream marks its packets
MAX_TEST_SECONDS = 30


def driver_patches(directory):
    try:
        manifest = json.loads((Path(directory) / 'vrostbridge-build.json').read_text(encoding='utf-8-sig'))
        return [str(name) for name in (manifest.get('source_patch_sha256') or {})]
    except (OSError, ValueError, AttributeError):
        return []


def driver_features(directory):
    """Features of the installed driver plus the companion's own."""
    names = driver_patches(directory)
    features = set(COMPANION_FEATURES)
    for prefix, provided in PATCH_FEATURES.items():
        if any(name.startswith(prefix) for name in names):
            features.update(provided)
    return sorted(features)


def stream_config(preferences, features):
    """The driver options for these validated session preferences."""
    foveation = preferences.get('foveation') or {}
    pacing = preferences.get('pacing_mbps', 0) if 'pacing' in features else 0
    config = {'pacing_mbps': int(pacing or 0),
              'intra_refresh': bool(preferences.get('intra_refresh')) and 'intra-refresh' in features,
              'gaze_foveation': bool(foveation.get('gaze')) and 'gaze-foveation' in features,
              'optical_centre': bool(foveation.get('optical_centre')) and 'optical-centre' in features,
              'gaze_deadband': 0.025, 'gaze_stale_ms': 150,
              # The client samples current poses; the host's extrapolation is the only prediction.
              'host_prediction': not (preferences.get('host_prediction') is False and 'prediction-switch' in features)}
    if 'gaze-hold' in features:  # patch 0017 (a driver without it returns the region 150 ms into a pause)
        config['gaze_hold_ms'] = GAZE_HOLD_MS
    if 'gaze-comfort' in features:  # patch 0019 (a driver without it moves with every 0.025 change)
        config['gaze_comfort'] = GAZE_COMFORT
    if 'parity' in features:  # patch 0021 (older clients ignore the parity datagrams)
        wired = preferences.get('network') == 'wired'
        config['fec_video_group'] = FEC_VIDEO_GROUP_WIRED if wired else FEC_VIDEO_GROUP
        config['fec_audio_group'] = FEC_AUDIO_GROUP
    return config


def _write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, separators=(',', ':')) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    return path


def write_stream_config(directory, config):
    return _write_json(Path(directory) / CONFIG_FILE, config)


def init_hint(headset):
    """What the client will advertise: its SteamVR render size per eye and verified refresh rate."""
    if not isinstance(headset, dict):
        return None
    width, height, hz = headset.get('width'), headset.get('height'), headset.get('refresh_hz')
    if (type(width) is not int or type(height) is not int or not 64 <= width <= 8192 or not 64 <= height <= 8192
            or not _number(hz) or not 30 <= hz <= 240):
        return None
    return {'default_view_width': width, 'default_view_height': height, 'refresh_rate': float(hz)}


def write_init_hint(directory, headset):
    """Write the startup display hint for the next driver load; returns its nonce ('' if none).

    A missing or unusable headset description removes any stale hint, so the driver
    never starts with an older session's display.
    """
    directory = Path(directory)
    for name in (INIT_FILE, INIT_APPLIED_FILE):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
    hint = init_hint(headset)
    if hint is None:
        return ''
    nonce = secrets.token_hex(8)
    _write_json(directory / INIT_FILE, dict(hint, nonce=nonce))
    return nonce


def init_outcome(directory, nonce):
    """The driver's record for this launch's hint, or None (old driver, not loaded yet, other launch)."""
    if not nonce:
        return None
    try:
        value = json.loads((Path(directory) / INIT_APPLIED_FILE).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get('nonce') != nonce:
        return None
    return value


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def link_test_plan(request):
    """Validate a client's test request; the target address is never taken from it."""
    if not isinstance(request, dict) or set(request) - {'test_id', 'port', 'fps', 'steps', 'step_seconds',
                                                         'packet_size', 'pace_mbps'}:
        raise ValueError('Invalid network test request.')
    port = request.get('port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid network test port.')
    steps = request.get('steps')
    step_seconds = request.get('step_seconds', 1.0)
    if not isinstance(steps, list) or not _number(step_seconds):
        raise ValueError('Invalid network test plan.')
    if expected_duration(len(steps), step_seconds) > MAX_TEST_SECONDS:
        raise ValueError('The network test would take too long.')
    pace = request.get('pace_mbps', 0)
    if not _number(pace) or not 0 <= pace <= 40000:
        raise ValueError('Invalid network test pacing.')
    return {'test_id': request.get('test_id'), 'port': port, 'fps': request.get('fps'), 'steps': steps,
            'step_seconds': step_seconds, 'packet_size': request.get('packet_size', 1400), 'pace_mbps': pace}


def run_link_test(request, peer, cancel=None, sender_socket=open_sender):
    """Send the planned test traffic to the client that asked (peer), then summarise it."""
    plan = link_test_plan(request)
    sock = sender_socket(tos=EF_TOS)
    try:
        sender = LinkTestSender(sock, (peer, plan['port']), plan['test_id'], plan['fps'], plan['steps'],
                                plan['step_seconds'], plan['packet_size'], plan['pace_mbps'])
        return sender.run(cancel)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def parse_ib_values(prefix, content):
    """key=value pairs of a driver diagnostic line (numbers, number pairs or short words)."""
    text = content.strip()
    if not text.startswith(prefix + ' '):
        return None
    values = {}
    for item in text[len(prefix) + 1:].split()[:24]:
        key, separator, raw = item.partition('=')
        if not separator or not key.replace('_', '').isalnum() or len(key) > 32:
            continue
        try:
            if ',' in raw:
                values[key] = [float(part) for part in raw.split(',')[:4]]
            else:
                number = float(raw)
                values[key] = int(number) if number.is_integer() and 'e' not in raw.lower() else number
        except ValueError:
            values[key] = raw[:64]  # 1.0.0.6: room for two colour attempts' reasons (patch 0020)
    return values or None


__all__ = ['CONFIG_FILE', 'INIT_FILE', 'INIT_APPLIED_FILE', 'driver_features', 'stream_config', 'write_stream_config',
           'init_hint', 'write_init_hint', 'init_outcome', 'run_link_test', 'link_test_plan', 'parse_ib_values']
