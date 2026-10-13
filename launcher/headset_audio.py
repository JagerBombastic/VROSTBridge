"""Use the headset's own speakers and microphone for a session, then put sound back.

The streaming client plays game sound on the default playback device and records
voice from the default recording device. On the client PC those defaults are
usually the PC's own speakers and microphone, not the Index's DisplayPort audio
and USB microphone or a Beyond's USB audio. For each session only, this picks the
headset's devices by name (it never guesses: no match means no change) and makes
them the defaults with pactl, which works with PipeWire and PulseAudio. At the
end the previous defaults return, unless the user picked other devices meanwhile.

Windows (1.0.0.7): SteamVR itself makes the headset the default playback and recording
device while it runs (SteamVR Settings > Audio), so nothing is changed here. The status
says so and names Windows' current default devices (Core Audio through COM, read on a
thread of its own and never while a page waits) and SteamVR's own audio settings from
steamvr.vrsettings.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run
from launcher import windows_power

_DEVICE = re.compile(r'(?!-)[\x21-\x7e]{1,255}')  # printable, no whitespace, never an option
_PROPERTIES = ('device.description', 'device.product.name', 'device.vendor.name', 'device.nick',
               'alsa.card_name', 'alsa.long_card_name', 'alsa.name', 'api.alsa.pcm.name',
               'api.alsa.card.name', 'node.nick', 'node.description', 'device.profile.description')
# (phrase, weight, identifies the headset on its own). Supporting words never
# select a device alone: a Steam Deck's own sound card is called "Valve-Jupiter".
# The Index's speakers are DisplayPort audio whose monitor name is "Index HMD";
# its microphone is the USB "Valve VR Radio & HMD Mic". Beyond audio is USB.
_KEYWORDS = {
    ('index', 'sink'): (('valve index', 10, True), ('index hmd', 10, True),
                        ('index', 2, False), ('valve', 1, False)),
    ('index', 'source'): (('hmd mic', 10, True), ('valve vr radio', 10, True), ('valve index', 8, True),
                          ('index hmd', 6, True), ('valve vr', 6, True), ('index', 2, False), ('valve', 1, False)),
    ('beyond', 'sink'): (('bigscreen beyond', 12, True), ('bigscreen', 10, True), ('beyond', 6, True),
                         ('audio strap', 2, False)),
    ('beyond', 'source'): (('bigscreen beyond', 12, True), ('bigscreen', 10, True), ('beyond', 6, True),
                           ('audio strap', 2, False)),
}
_GENERIC = ('hmd', 'vr headset', 'virtual reality')
_LABELS = {'sink': 'sound output', 'source': 'microphone'}
# Windows (1.0.0.7): SteamVR's audio settings (openvr.h k_pch_audio_*) worth reporting; device ids are left out.
STEAMVR_AUDIO_KEYS = ('setOsDefaultPlaybackDevice', 'enablePlaybackDeviceOverride', 'playbackDeviceOverrideName',
                      'setOsDefaultRecordingDevice', 'enableRecordingDeviceOverride', 'recordingDeviceOverrideName',
                      'enablePlaybackMirror', 'playbackMirrorDeviceName')
READ_ENDPOINT_NAMES = True   # Windows: name the default devices (Core Audio through COM); False skips it
ENDPOINT_REFRESH_S = 10      # Windows: how old the names may get during a session before they are read again
ENDPOINT_WAIT_S = 2.0        # Windows: how long begin() and devices() wait for them
WINDOWS_TEXT = ("On Windows, SteamVR switches sound to the headset itself (SteamVR Settings > Audio), so VROSTBridge "
                "leaves Windows' sound devices as they are.")


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _short(error):
    lines = str(error).strip().splitlines()
    return (lines[-1] if lines else type(error).__name__)[:160]


def _detail(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:160] if lines else ''


def _words(text):
    return ' '.join(re.sub(r'[^0-9a-z]+', ' ', text.casefold()).split()) if isinstance(text, str) else ''


def _family(headset_model):
    words = _words(headset_model).split()
    if 'beyond' in words or 'bigscreen' in words:
        return 'beyond'
    if 'index' in words or 'valve' in words:
        return 'index'
    return 'other'


def _keywords(family, kind, headset_model):
    if family != 'other':
        return _KEYWORDS[(family, kind)] + tuple((word, 1, False) for word in _GENERIC)
    model = _words(headset_model)
    own = ((model, 8, True),) if len(model) >= 3 and model not in _GENERIC else ()
    return own + tuple((word, 3, True) for word in _GENERIC)


def _haystack(device):
    parts = [device.get('name'), device.get('description')]
    properties = device.get('properties')
    if isinstance(properties, dict):
        parts.extend(properties.get(key) for key in _PROPERTIES)
    ports = device.get('ports')
    for port in ports if isinstance(ports, list) else ():
        if isinstance(port, dict):
            parts.append(port.get('description'))  # may carry the HDMI/DP monitor name
    # '|' keeps a phrase from matching across two separate fields.
    return ' ' + ' | '.join(_words(part) for part in parts if isinstance(part, str)) + ' '


def _is_monitor(source):
    name = source.get('name')
    if isinstance(name, str) and name.endswith('.monitor'):
        return True
    # pactl's JSON names this 'monitor_source' for sources (the monitored sink).
    for key in ('monitor_of_sink', 'monitor_source'):
        value = source.get(key)
        if isinstance(value, str) and value.strip() not in ('', 'n/a'):
            return True
    properties = source.get('properties')
    return isinstance(properties, dict) and properties.get('device.class') == 'monitor'


def _is_hdmi(device):
    properties = device.get('properties') if isinstance(device.get('properties'), dict) else {}
    words = ' '.join(_words(value) for value in (device.get('name'), device.get('active_port'),
                                                  properties.get('device.profile.name'))).split()
    ports = device.get('ports') if isinstance(device.get('ports'), list) else []
    return ('hdmi' in words or 'displayport' in words or
            any(isinstance(port, dict) and port.get('type') in ('HDMI', 'DisplayPort') for port in ports))


def _is_usb(device):
    properties = device.get('properties') if isinstance(device.get('properties'), dict) else {}
    return properties.get('device.bus') == 'usb' or '.usb-' in device.get('name', '')


def _plugged(device):
    """False only when the active port is known to be unplugged."""
    ports = device.get('ports') if isinstance(device.get('ports'), list) else []
    for port in ports:
        if isinstance(port, dict) and port.get('name') == device.get('active_port'):
            return port.get('availability') != 'not available'
    return True


def _describe(device):
    description = device.get('description')
    return (description if isinstance(description, str) and description.strip() else device['name'])[:120]


def _usable(devices, kind):
    """Well-formed devices with safe names; monitors are never recording devices."""
    return [device for device in (devices if isinstance(devices, list) else ())
            if isinstance(device, dict) and isinstance(device.get('name'), str) and _DEVICE.fullmatch(device['name'])
            and not (kind == 'source' and _is_monitor(device))]


def _choose(devices, kind, family, headset_model, override):
    label = 'Speakers' if kind == 'sink' else 'Microphone'
    note = ''
    if override:
        if isinstance(override, str) and _DEVICE.fullmatch(override):
            chosen = next((device for device in devices if device['name'] == override), None)
            if chosen is not None:
                return chosen, label + ': ' + _describe(chosen) + ' (chosen in settings)'
            note = ' (the chosen device is not connected)'
        else:
            note = ' (the chosen device name is not valid)'
    candidates = []
    keywords = _keywords(family, kind, headset_model)
    for device in devices:
        if family == 'beyond' and kind == 'sink' and _is_hdmi(device):
            continue  # the Beyond has no speakers on its display connection
        haystack = _haystack(device)
        matched = [entry for entry in keywords if ' ' + entry[0] + ' ' in haystack]
        if not any(identifying for _, _, identifying in matched):
            continue
        score = sum(weight for _, weight, _ in matched)
        if family == 'index' and kind == 'sink' and _is_hdmi(device):
            score += 2
        if _is_usb(device) and (family == 'beyond' or kind == 'source'):
            score += 1
        candidates.append((-score, not _plugged(device), device['name'], device, [entry[0] for entry in matched]))
    if not candidates:
        return None, label + ': no headset device found' + note
    _, _, _, device, phrases = min(candidates, key=lambda item: item[:3])
    return device, label + ': ' + _describe(device) + ' (matched ' + ', '.join(phrases) + ')' + note


def _openvr_registry():
    base = os.environ.get('LOCALAPPDATA', '')
    return Path(base) / 'openvr' / 'openvrpaths.vrpath' if base and Path(base).is_absolute() else None


def steamvr_audio_settings(registry=None):
    """SteamVR's audio settings (the "audio" section of steamvr.vrsettings in its registered config folder):
    the STEAMVR_AUDIO_KEYS present, as booleans or short names; {} when unreadable."""
    try:
        registry = registry or _openvr_registry()
        if registry is None:
            return {}
        with open(registry, 'rb') as handle:
            paths = json.loads(handle.read(1 << 20).decode('utf-8-sig', 'replace'))
        config = paths.get('config') if isinstance(paths, dict) else None
        first = config[0] if isinstance(config, list) and config else None
        if not isinstance(first, str) or not first or '\x00' in first or not Path(first).is_absolute():
            return {}
        with open(Path(first) / 'steamvr.vrsettings', 'rb') as handle:
            settings = json.loads(handle.read(4 << 20).decode('utf-8-sig', 'replace'))
    except (OSError, ValueError):
        return {}
    audio = settings.get('audio') if isinstance(settings, dict) else None
    result = {}
    for key in STEAMVR_AUDIO_KEYS:
        value = audio.get(key) if isinstance(audio, dict) else None
        if key.endswith('Name'):  # a device's name; the others are switches
            name = _endpoint_name(value)
            if name:
                result[key] = name
        elif isinstance(value, bool):
            result[key] = value
    return result


def match_headset_devices(sinks, sources, headset_model, overrides=None):
    """Pick the headset's playback sink and recording source from pactl JSON lists.

    An override name ({'sink': name, 'source': name}) wins when that device is
    present. Otherwise devices are scored on headset keywords found in their name,
    description, descriptive properties and port descriptions; a device needs at
    least one identifying keyword. Returns (sink name or None, source name or None,
    one-line explanation). None means "leave this direction alone", never a guess.
    """
    overrides = overrides if isinstance(overrides, dict) else {}
    family = _family(headset_model)
    sink, sink_text = _choose(_usable(sinks, 'sink'), 'sink', family, headset_model, overrides.get('sink'))
    source, source_text = _choose(_usable(sources, 'source'), 'source', family, headset_model, overrides.get('source'))
    return (sink['name'] if sink else None, source['name'] if source else None, sink_text + '; ' + source_text)


def _endpoint_name(value):
    if not isinstance(value, str):
        return None
    return ''.join(character for character in value if character.isprintable()).strip()[:120] or None


class HeadsetAudio:
    def __init__(self, runner=system_run, log=print, pactl=None, endpoints=None, steamvr_settings=None):
        """pactl: None locates the tool; an empty string disables it.

        Windows only: endpoints() returns Windows' default devices as {'playback', 'recording'} names (None: Core
        Audio through launcher.windows_power); steamvr_settings() SteamVR's audio settings (None:
        steamvr_audio_settings)."""
        self.runner, self.log = runner, log
        self.pactl = _tool('pactl', ('/usr/bin/pactl',)) if pactl is None else pactl
        self.endpoints = endpoints
        self.steamvr_settings = steamvr_settings if steamvr_settings is not None else steamvr_audio_settings
        self._lock = threading.Lock()
        self._operation = threading.RLock()
        self._changed = {}  # kind -> (previous default, default this session selected)
        self._value = {'state': 'idle', 'sink': None, 'source': None, 'message': '', 'explanation': ''}
        self._windows_session = False  # Windows: between begin() and end(), the device names are kept fresh
        self._endpoint_at = None
        self._endpoint_thread = None

    def status(self):
        if sys.platform == 'win32':
            self._refresh_endpoints()
            with self._lock:
                return dict(self._value, managed_by='steamvr')
        with self._lock:
            return dict(self._value)

    def _set(self, state, message, sink=None, source=None, explanation='', **extra):
        with self._lock:
            self._value = {'state': state, 'sink': sink, 'source': source, 'message': message,
                           'explanation': explanation, **extra}
            value = dict(self._value)
        if message:
            self.log('AUDIO ' + state + ': ' + message)
        return value

    # Windows (1.0.0.7) ---------------------------------------------------

    def _query_endpoints(self):
        """({'playback', 'recording'} names or None, problem). Runs Core Audio on the calling thread."""
        try:
            names = (self.endpoints or windows_power.api().default_audio_endpoints)()
        except Exception as error:  # COM refused or unavailable: say so, never fail the session
            return None, _short(error)
        if not isinstance(names, dict):
            return None, 'Windows gave no answer'
        return {key: _endpoint_name(names.get(key)) for key in ('playback', 'recording')}, ''

    def _read_endpoints(self, wait=ENDPOINT_WAIT_S):
        """The default device names within `wait` seconds, read on a thread of their own (COM needs one)."""
        if not READ_ENDPOINT_NAMES:
            return None, ''
        box = {}
        thread = threading.Thread(target=lambda: box.setdefault('value', self._query_endpoints()), daemon=True,
                                  name='VROSTBridge-audio-endpoints')
        thread.start()
        thread.join(wait)
        return box.get('value') or (None, 'Windows did not answer in time')

    def _refresh_endpoints(self):
        """During a session, read the names again in the background once they are ENDPOINT_REFRESH_S old."""
        if not READ_ENDPOINT_NAMES:
            return
        with self._lock:
            if not self._windows_session or (self._endpoint_thread is not None and self._endpoint_thread.is_alive()):
                return
            if self._endpoint_at is not None and time.monotonic() - self._endpoint_at < ENDPOINT_REFRESH_S:
                return
            self._endpoint_at = time.monotonic()
            thread = self._endpoint_thread = threading.Thread(target=self._refreshed, daemon=True,
                                                              name='VROSTBridge-audio-endpoints')
        thread.start()

    def _refreshed(self):
        endpoints, _ = self._query_endpoints()
        with self._lock:
            self._endpoint_at = time.monotonic()
            if endpoints is None or not self._windows_session or self._value.get('state') != 'steamvr':
                return
            self._value = dict(self._value, playback=endpoints['playback'], recording=endpoints['recording'],
                               message=self._windows_message(endpoints, '', self._value.get('steamvr_audio') or {}))

    @staticmethod
    def _windows_message(endpoints, problem, settings):
        parts = [WINDOWS_TEXT]
        if endpoints is not None:
            parts.append('Windows plays sound through ' + (endpoints.get('playback') or 'no device') +
                         ' and records from ' + (endpoints.get('recording') or 'no device') + '.')
        elif problem:
            parts.append("Windows' current sound devices could not be read (" + problem + ').')
        if settings.get('setOsDefaultPlaybackDevice') is False:
            parts.append('SteamVR is set not to switch the sound output to the headset; change that in SteamVR '
                         'Settings > Audio for sound in the headset.')
        if settings.get('setOsDefaultRecordingDevice') is False:
            parts.append("SteamVR is set not to switch the microphone to the headset's; change that in SteamVR "
                         'Settings > Audio to use it.')
        return ' '.join(parts)

    def _begin_windows(self):
        endpoints, problem = self._read_endpoints()
        try:
            settings = self.steamvr_settings() or {}
        except Exception:
            settings = {}
        settings = dict(settings) if isinstance(settings, dict) else {}
        with self._lock:
            self._windows_session, self._endpoint_at = True, time.monotonic()
        return self._set('steamvr', self._windows_message(endpoints, problem, settings),
                         explanation='SteamVR chooses the sound devices on Windows; VROSTBridge does not change them.',
                         playback=(endpoints or {}).get('playback'), recording=(endpoints or {}).get('recording'),
                         steamvr_audio=settings, managed_by='steamvr')

    def _run(self, arguments, timeout=5):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', _short(error))

    def _list(self, kind):
        """Raw pactl JSON objects for 'sinks' or 'sources'; ValueError when unreadable."""
        result = self._run([self.pactl, '-f', 'json', 'list', kind])
        if result.returncode != 0:
            raise ValueError(_detail(result) or 'pactl could not list sound devices')
        data = json.loads(result.stdout or '[]')
        if not isinstance(data, list):
            raise ValueError('pactl returned an unexpected device list')
        return [item for item in data if isinstance(item, dict) and isinstance(item.get('name'), str)]

    def _default(self, kind):
        result = self._run([self.pactl, 'get-default-' + kind])
        value = (result.stdout or '').strip() if result.returncode == 0 else ''
        return value if _DEVICE.fullmatch(value) else None

    def _make_default(self, kind, name):
        if not isinstance(name, str) or not _DEVICE.fullmatch(name):
            return 'the device name is not valid'
        result = self._run([self.pactl, 'set-default-' + kind, name])
        return '' if result.returncode == 0 else (_detail(result) or 'exit code %s' % result.returncode)

    def devices(self):
        """Sound devices for the settings UI (monitors excluded). Never raises."""
        result = {'available': False, 'sinks': [], 'sources': [], 'default_sink': None, 'default_source': None,
                  'message': ''}
        if sys.platform == 'win32':  # 1.0.0.7: SteamVR chooses them; only Windows' current defaults are named
            endpoints, problem = self._read_endpoints()
            return dict(result, default_playback=(endpoints or {}).get('playback'),
                        default_recording=(endpoints or {}).get('recording'),
                        message="On Windows, SteamVR chooses the headset's sound devices (SteamVR Settings > Audio)." +
                        (" Windows' current sound devices could not be read (" + problem + ').' if problem else ''))
        if not self.pactl:
            return dict(result, message='Sound devices cannot be listed because pactl is not available.')
        try:
            sinks, sources = _usable(self._list('sinks'), 'sink'), _usable(self._list('sources'), 'source')
        except ValueError as error:
            return dict(result, message='Sound devices could not be read (' + _short(error) + ').')
        return dict(result, available=True,
                    sinks=[{'name': device['name'], 'description': _describe(device)} for device in sinks],
                    sources=[{'name': device['name'], 'description': _describe(device)} for device in sources],
                    default_sink=self._default('sink'), default_source=self._default('source'))

    def begin(self, headset_model, overrides=None, enabled=True):
        """Make the headset's devices the defaults for this session only (Windows: SteamVR does; nothing changes)."""
        with self._operation:
            self.end()  # a previous begin() is always undone first
            try:
                if sys.platform == 'win32':
                    return self._begin_windows()
                return self._begin(headset_model, overrides, enabled)
            except Exception as error:  # a session must never be blocked by this helper
                return self._set('unchanged', 'Sound devices were left as they are (' + _short(error) + ').')

    def _begin(self, headset_model, overrides, enabled):
        if not enabled:
            return self._set('unchanged', 'Using the headset for sound is turned off, so sound devices were left '
                             'as they are.')
        if not self.pactl:
            return self._set('unavailable', 'Sound devices could not be changed because pactl is not available.')
        try:
            sinks, sources = self._list('sinks'), self._list('sources')
        except ValueError as error:
            return self._set('unavailable', 'Sound devices could not be read (' + _short(error) +
                             '), so they were left as they are.')
        sink, source, explanation = match_headset_devices(sinks, sources, headset_model, overrides)
        self.log('AUDIO match: ' + explanation)
        routed, notes = {}, []
        for kind, target in (('sink', sink), ('source', source)):
            if target is None:
                continue
            previous = self._default(kind)
            if previous is None:
                notes.append('The current ' + _LABELS[kind] + ' could not be read, so it was left as it is.')
                continue
            if previous != target:
                problem = self._make_default(kind, target)
                if problem:
                    notes.append('The headset ' + _LABELS[kind] + ' could not be selected (' + problem + ').')
                    continue
                self._changed[kind] = (previous, target)
            routed[kind] = target
        if len(routed) == 2:
            state, text = 'routed', ('Sound plays through the headset and its microphone is used for this session; '
                                     'your previous sound devices return when the session stops.')
        elif 'sink' in routed:
            state, text = 'partial', 'Sound plays through the headset for this session.'
        elif 'source' in routed:
            state, text = 'partial', "The headset's microphone is used for this session."
        else:
            state, text = 'unchanged', 'Sound devices were left as they are.'
        missing = [_LABELS[kind] for kind, target in (('sink', sink), ('source', source)) if target is None]
        if missing:
            notes.append("The headset's " + ' and '.join(missing) + (' were' if len(missing) > 1 else ' was') +
                         ' not found; you can choose the ' + ('devices' if len(missing) > 1 else 'device') +
                         ' by name instead.')
        return self._set(state, ' '.join([text] + notes), routed.get('sink'), routed.get('source'), explanation)

    def end(self):
        """Restore each previous default only if it is still the one this session set."""
        with self._operation:
            with self._lock:
                self._windows_session = False
            changed, self._changed = self._changed, {}
            if not changed:
                return self.status()
            try:
                notes = self._restore(changed)
            except Exception as error:  # never raise out of end()
                notes = ['Sound devices could not be fully restored (' + _short(error) + ').']
            return self._set('idle', ' '.join(notes))

    def _restore(self, changed):
        restored, notes = [], []
        for kind in ('sink', 'source'):
            if kind not in changed:
                continue
            previous, ours = changed[kind]
            label = _LABELS[kind]
            current = self._default(kind)
            if current != ours:
                notes.append('The ' + label + (' could not be checked' if current is None else
                                               ' was changed during the session') + ', so it was left as it is.')
                continue
            try:
                present = {device['name'] for device in self._list(kind + 's')}
            except ValueError:
                notes.append('The previous ' + label + ' could not be checked, so the headset stays selected.')
                continue
            if previous not in present:
                notes.append('The previous ' + label + ' is no longer available, so the headset stays selected.')
                continue
            problem = self._make_default(kind, previous)
            if problem:
                notes.append('The previous ' + label + ' could not be selected again (' + problem + ').')
            else:
                restored.append(label)
        if restored:
            notes.insert(0, 'Your previous ' + ' and '.join(restored) + (' are' if len(restored) > 1 else ' is') +
                         ' selected again.')
        return notes
