"""Apply only VROSTBridge-owned settings to the pinned ALVR session schema."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import time
import hashlib
from launcher.headset import headset_metadata, stream_width

# Identity can be mirrored; USB/firmware/display-control capability cannot.
HEADSET_PROPERTIES = {
    'model':'ModelNumberString', 'manufacturer':'ManufacturerNameString',
    'tracking_system':'TrackingSystemNameString', 'render_model':'RenderModelNameString',
    'registered_type':'RegisteredDeviceTypeString', 'input_profile':'InputProfilePathString',
    'icon_ready':'NamedIconPathDeviceReadyString', 'icon_off':'NamedIconPathDeviceOffString',
    'icon_searching':'NamedIconPathDeviceSearchingString', 'icon_alert':'NamedIconPathDeviceReadyAlertString',
}
UNSUPPORTED_HOST_CAPABILITIES = ('DeviceIsWirelessBool', 'DeviceProvidesBatteryStatusBool',
    'DeviceCanPowerOffBool', 'FirmwareUpdateAvailableBool', 'FirmwareManualUpdateBool',
    'FirmwareForceUpdateRequiredBool', 'HasCameraBool', 'AllowCameraToggleBool',
    'DisplaySupportsRuntimeFramerateChangeBool')

def mirror_headset(settings, description):
    hmd=headset_metadata(description)
    identity=hashlib.sha256((hmd['serial'] or hmd['model']).encode('utf-8')).hexdigest()[:16]
    settings['headset']['emulation_mode']['variant']='Custom'
    # The headset serial SteamVR keys its per-headset data on.
    settings['headset']['emulation_mode']['Custom']['serial_number']='VROSTBridge-'+identity
    values={prop:hmd[field] for field,prop in HEADSET_PROPERTIES.items()}
    # Driver resources are referenced symbolically, never as client filesystem
    # paths or arbitrary URLs. Unknown/missing resources keep a generic model.
    for field in ('input_profile','icon_ready','icon_off','icon_searching','icon_alert'):
        value=hmd[field]
        if not value.startswith('{') or '}' not in value or '..' in value or ':' in value:
            values.pop(HEADSET_PROPERTIES[field],None)
    if not hmd['render_model'] or '..' in hmd['render_model'] or ':' in hmd['render_model']:
        values['RenderModelNameString']='generic_hmd'
    values.update({name:'false' for name in UNSUPPORTED_HOST_CAPABILITIES})
    # Keep the driver attribution truthful; the physical driver still lives on
    # the client. Display frequency and live eye/IPD geometry are negotiated.
    values['DriverVersionString']='VROSTBridge 1.0.0.14 / ALVR'
    owned=set(HEADSET_PROPERTIES.values()) | set(UNSUPPORTED_HOST_CAPABILITIES) | {'DriverVersionString'}
    props=settings['headset']['extra_openvr_props']
    retained=[row for row in props['content'] if row.get('key',{}).get('variant') not in owned]
    props['content']=retained+[{'key':{'variant':key},'value':value} for key,value in values.items()]

# These inputs already use Index's native paths. The pinned automatic mapper
# targets cross-controller conversion and omits trackpad inputs, squeeze force
# and left A/B. Explicit passthrough preserves values and avoids per-input logs.
INDEX_INPUTS = ('trigger/value','squeeze/force','squeeze/value','trackpad/force',
                'thumbstick/x','thumbstick/y','trackpad/x','trackpad/y',
                'a/click','b/click','a/touch','b/touch','trigger/touch',
                'trigger/click','thumbstick/click','thumbstick/touch',
                'trackpad/touch','system/click')

def index_button_mappings(settings):
    mapping=settings['headset']['controllers']['content']['button_mappings']
    if mapping['set']:
        return # Retain an explicitly customized mapping.
    dictionary=mapping['content']
    rows=[]
    for hand in ('left','right'):
        for suffix in INDEX_INPUTS:
            path='/user/hand/'+hand+'/input/'+suffix
            vector=copy.deepcopy(dictionary['value'])
            target=copy.deepcopy(vector['element'])
            target['destination']=path
            target['mapping_type']['variant']='Passthrough'
            target['binary_conditions']['content']=[]
            vector['content']=[target]
            rows.append([path,vector])
    dictionary['content']=rows
    mapping['set']=True

def set_existing(document, path, value):
    target = document
    for part in path[:-1]:
        if part not in target:
            raise ValueError('Incompatible ALVR settings: ' + '.'.join(path))
        target = target[part]
    if path[-1] not in target:
        raise ValueError('Incompatible ALVR settings: ' + '.'.join(path))
    target[path[-1]] = value

ENCODER_PRESETS = {'speed': ('Speed', 'P1'), 'balanced': ('Balanced', 'P4')}


def foveation_edits(foveation):
    """ALVR foveated-encoding settings; None keeps it off (full resolution everywhere).

    The client undoes the compression with the centres sent in every frame, so the
    host may move them per frame (gaze, optical centre) after this negotiation.
    """
    if foveation is None:
        return {'video.foveated_encoding.enabled': False}
    return {'video.foveated_encoding.enabled': True,
            # Only when the client reports it can undo the compression.
            'video.foveated_encoding.content.force_enable': False,
            'video.foveated_encoding.content.center_size.content': [float(v) for v in foveation['center_size']],
            'video.foveated_encoding.content.center_shift.content': [float(v) for v in foveation.get('center_shift', [0.0, 0.0])],
            'video.foveated_encoding.content.edge_ratio.content': [float(v) for v in foveation['edge_ratio']]}


def intra_refresh_edits(enabled, fps):
    """Gradual (intra) refresh instead of keyframes after packet loss.

    NVENC reads these settings; AMF reads the per-session stream file (patch 0011).
    A lost packet then heals within one refresh period, so the client keeps decoding
    damaged frames instead of freezing until a keyframe arrives.
    """
    # NVENC refreshes over `count` frames and restarts every `period` frames; the
    # count must stay below the period. A wave every half second heals any loss.
    period = max(8, int(round(fps / 2)))
    return {'video.encoder_config.nvenc.enable_intra_refresh': bool(enabled),
            'video.encoder_config.nvenc.intra_refresh_period': period if enabled else -1,
            'video.encoder_config.nvenc.intra_refresh_count': period - 1 if enabled else -1,
            'connection.avoid_video_glitching': not enabled}


# 1.0.0.14: how much larger than the stream the game may render. SteamVR's recommended render size for a headset is
# larger than its panels (the lens correction needs it), and a headset on its own PC renders at it; until 1.0.0.13
# VROSTBridge made the game render at exactly the stream width, so the picture reached the headset with no
# oversampling at all. The host now renders at the headset PC's recommended size (which carries that user's own
# supersampling setting) up to this much above the stream width, and the encoder's input is scaled down from it, as
# on a headset's own PC. Nothing changes for the headset PC: the stream is the same size.
RENDER_OVERSAMPLE_MAX = 1.5


def render_width(eye_width, headset=None):
    """The width SteamVR renders per eye: the headset's recommended width within [eye_width, 1.5 x eye_width],
    a multiple of 32; the stream width itself without a headset description."""
    if headset is None:
        return eye_width
    recommended = headset_metadata(headset)['width']
    return max(eye_width, min(recommended, int(eye_width * RENDER_OVERSAMPLE_MAX)) // 32 * 32)


def configure(document, microphone=False, gaze=False, codec='Hevc', bitrate=60, auto_connect=False,
              resolution='compatibility', headset=None, encoder='speed', network=None, ten_bit=False,
              foveation=None, intra_refresh=False):
    if codec not in ('Hevc', 'H264', 'AV1') or not 10 <= int(bitrate) <= 300:
        raise ValueError('Invalid codec or bitrate')
    if encoder not in ENCODER_PRESETS or network not in (None, 'wired', 'wifi', 'unknown'):
        raise ValueError('Invalid encoder priority or network type')
    if type(ten_bit) is not bool or type(intra_refresh) is not bool:
        raise ValueError('Invalid 10-bit or intra refresh setting')
    amf_preset, nvenc_preset = ENCODER_PRESETS[encoder]
    result = copy.deepcopy(document)
    settings = result['session_settings']
    # ALVR encodes both eyes side by side. The inherited 2144-pixel eye width
    # produces a 4288-pixel H.264 frame, exceeding common 4096-wide hardware
    # decoders. Keep the first-test profile conservative at 2880 stereo width.
    # Derive height from the actual headset; never inherit another HMD's ratio.
    # AV1 shares HEVC's size limits.
    eye_width = stream_width('Hevc' if codec == 'AV1' else codec, resolution, headset)
    fps = headset_metadata(headset)['refresh_hz'] if headset is not None else 90.0
    edits = {
        'video.preferred_codec.variant': codec,
        'video.preferred_fps': fps,
        'video.transcoding_view_resolution.variant': 'Absolute',
        'video.transcoding_view_resolution.Absolute.width': eye_width,
        'video.transcoding_view_resolution.Absolute.height.set': False,
        'video.emulated_headset_view_resolution.variant': 'Absolute',
        'video.emulated_headset_view_resolution.Absolute.width': render_width(eye_width, headset),  # 1.0.0.14
        'video.emulated_headset_view_resolution.Absolute.height.set': False,
        'video.clientside_foveation.enabled': False,
        'video.clientside_post_processing.enabled': False,
        'video.upscaling.enabled': False,
        # 10-bit removes banding in dark gradients; H.264 stays 8-bit.
        'video.encoder_config.use_10bit.set': True,
        'video.encoder_config.use_10bit.content': bool(ten_bit) and codec != 'H264',
        'video.encoder_config.encoding_gamma.set': True,
        'video.encoder_config.encoding_gamma.content': 1.0,
        'video.encoder_config.hdr.enable.set': True,
        'video.encoder_config.hdr.enable.content': False,
        'video.bitrate.mode.variant': 'ConstantMbps',
        'video.bitrate.mode.ConstantMbps': int(bitrate),
        'headset.controllers.content.emulation_mode.variant': 'ValveIndex',
        'headset.controllers.enabled': True,
        # Physical Index poses are already in their correct OpenVR tracking space.
        # ALVR defaults target a different controller pose and otherwise displace
        # each forwarded controller by 11 cm.
        'headset.controllers.content.left_controller_position_offset.content': [0.0, 0.0, 0.0],
        'headset.controllers.content.left_controller_rotation_offset.content': [0.0, 0.0, 0.0],
        'headset.controllers.content.linear_velocity_cutoff': 0.0,
        'headset.controllers.content.angular_velocity_cutoff': 0.0,
        # Client converts physical Index bones into the pinned ALVR joint basis.
        # Keep controller poses/buttons, without optical-hand devices or offsets.
        'headset.controllers.content.hand_skeleton.enabled': True,
        'headset.controllers.content.hand_skeleton.content.steamvr_input_2_0': False,
        'headset.controllers.content.hand_skeleton.content.predict': False,
        'headset.controllers.content.hand_tracking_interaction.enabled': False,
        'headset.controllers.content.left_hand_tracking_position_offset.content': [0.0, 0.0, 0.0],
        'headset.controllers.content.left_hand_tracking_rotation_offset.content': [0.0, 0.0, 0.0],
        'headset.recentering_mode.variant': 'Stage',
        'headset.face_tracking.enabled': bool(gaze),
        'headset.face_tracking.content.sink.variant': 'VrchatEyeOsc',
        'audio.microphone.enabled': bool(microphone),
        'connection.client_discovery.enabled': True,
        'connection.client_discovery.content.auto_trust_clients': bool(auto_connect),
        'connection.allow_untrusted_http': False,
        'connection.allow_remote_dashboard': False,
        'connection.max_queued_server_video_frames': 3,
        # Mark stream packets Expedited Forwarding. On Linux this places the
        # client's uplink (tracking, input, statistics) in a Wi-Fi priority
        # access category ahead of bulk traffic. Windows ignores application
        # marks without a QoS policy; ALVR discards marking errors.
        'connection.dscp.set': True,
        'connection.dscp.content.variant': 'ExpeditedForwarding',
        # CABAC is H.264's more efficient entropy coder (typically ~10% fewer bits for
        # the same picture). CAVLC suits weak mobile decoders; VA-API/desktop decoders
        # handle CABAC in hardware. No effect on HEVC, which always uses CABAC.
        'video.encoder_config.entropy_coding.variant': 'Cabac',
        # Encoder priority: lowest latency (default) or a balanced quality preset.
        'video.encoder_config.quality_preset.variant': amf_preset,
        'video.encoder_config.nvenc.quality_preset.variant': nvenc_preset,
        # Client audio jitter buffer: a wired LAN needs far less than Wi-Fi.
        'audio.game_audio.content.buffering.average_buffering_ms': 30 if network == 'wired' else 50,
        # The driver's own log (session_log.txt, rewritten at each SteamVR start) goes
        # into reports. Tracking, button and haptic logging stay off.
        'extra.logging.log_to_disk': True,
    }
    edits.update(foveation_edits(foveation))
    edits.update(intra_refresh_edits(intra_refresh, fps))
    for key, value in edits.items():
        set_existing(settings, key.split('.'), value)
    index_button_mappings(settings)
    if headset is not None:mirror_headset(settings,headset)
    return result

def manual_client(document, hostname, address):
    if not isinstance(address,str) or not isinstance(hostname,str):
        raise ValueError('Enter the client ID and LAN address shown on its page')
    ip = ipaddress.ip_address(address)
    if not ip.is_private or ip.is_loopback or ip.is_multicast or ip.is_unspecified:
        raise ValueError('Enter the client LAN address')
    if not hostname.endswith('.client.local.') or len(hostname) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in hostname):
        raise ValueError('Use the client hostname shown in its connection status')
    result = copy.deepcopy(document)
    client = result['client_connections'].setdefault(hostname, {
        'display_name': 'VROSTBridge', 'current_ip': None, 'manual_ips': [],
        'trusted': False, 'connection_state': 'Disconnected'})
    client['manual_ips'] = sorted(set(client['manual_ips']) | {str(ip)})
    # Adding a saved address never grants trust; approval is a separate live action.
    return result

def session_client_address(document, hostname, address, auto_connect=False, previous=None):
    """Let ALVR reconnect to the session's client at its known address.

    ALVR's reconnection otherwise depends on multicast discovery, which Wi-Fi
    access points can delay or drop after an outage. The headset companion
    authenticated with its pairing token and reported this hostname; its address
    is the session lease's peer. The address replaces the one this function set
    for the previous session (`previous`) and keeps addresses the user added.
    A new entry is trusted only when automatic connection is on, as ALVR's own
    discovery would do.
    """
    result = manual_client(document, hostname, address)
    address = str(ipaddress.ip_address(address))
    client = result['client_connections'][hostname]
    stale = {previous} if isinstance(previous, str) and previous != address else set()
    client['manual_ips'] = [address] + sorted(set(client['manual_ips']) - stale - {address})
    if hostname not in document.get('client_connections', {}) and auto_connect:
        client['trusted'] = True
    return result

def write_session(path, updated):
    path = Path(path)
    if path.exists():
        backup = path.with_name(path.name + '.' + str(time.time_ns()) + '.bak')
        backup.write_bytes(path.read_bytes())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(updated, indent=2), encoding='utf-8')
    os.replace(temporary, path)
