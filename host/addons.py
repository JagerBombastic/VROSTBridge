"""Read-only detection of other SteamVR add-ons (drivers) that load on the host.

On the rendering PC, SteamVR only needs VROSTBridge's virtual headset driver. A headset
vendor's add-on installed here (for example a Bigscreen Beyond helper) still loads and can
act on the virtual headset, as one report showed with IPD requests. This never edits
SteamVR settings or registrations; it lists enabled third-party drivers so the user can
disable them in SteamVR's add-on manager.
"""
import json
import os
from pathlib import Path

# Drivers shipped inside SteamVR itself. Absent physical hardware they stay idle.
STEAMVR_BUILTIN = {'lighthouse', 'null', 'gamepad', 'htc', 'indexhmd', 'oculus', 'oculus_legacy',
                   'holographic', 'vrlink', 'vive_tracker', 'vive_hand_tracking', 'vrwebhelper'}
KNOWN_ADVICE = {
    'bigscreen': 'Bigscreen Beyond add-on: the headset is on the client PC, so it is not needed on the host.',
}


def _json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest_name(directory):
    name = _json(Path(directory) / 'driver.vrdrivermanifest').get('name')
    return name if isinstance(name, str) and 0 < len(name) <= 64 else None


def detect(registry=None, own_driver=None, own_directory=None):
    """Return enabled third-party SteamVR drivers visible to the host's SteamVR."""
    if registry is None:
        if os.name != 'nt':
            return []
        registry = Path(os.environ.get('LOCALAPPDATA', '')) / 'openvr/openvrpaths.vrpath'
    paths = _json(registry)
    settings = {}
    for root in paths.get('config', [])[:1]:
        if isinstance(root, str):
            settings = _json(Path(root) / 'steamvr.vrsettings')
    own = {own_driver} if own_driver else set()
    own_path = str(Path(own_directory).resolve()).lower() if own_directory else None
    found = {}
    candidates = []
    for entry in paths.get('external_drivers', []) or []:
        if isinstance(entry, str) and entry:
            candidates.append((Path(entry), 'registered'))
    for runtime in paths.get('runtime', [])[:2]:
        if isinstance(runtime, str):
            drivers = Path(runtime) / 'drivers'
            try:
                candidates += [(item, 'steamvr') for item in sorted(drivers.iterdir())[:64] if item.is_dir()]
            except OSError:
                pass
    for directory, source in candidates[:96]:
        try:
            resolved = str(directory.resolve()).lower()
        except OSError:
            continue
        if own_path and resolved == own_path:
            continue
        name = _manifest_name(directory)
        if not name or name in own or name in found or (source == 'steamvr' and name in STEAMVR_BUILTIN):
            continue
        section = settings.get('driver_' + name, {})
        enabled = not (isinstance(section, dict) and section.get('enable') is False)
        if enabled:
            key = next((k for k in KNOWN_ADVICE if k in name.lower()), None)
            found[name] = {'name': name, 'source': source, 'path': str(directory)[:260],
                           'advice': KNOWN_ADVICE.get(key, 'Third-party SteamVR add-on loaded on the host.')}
    return sorted(found.values(), key=lambda item: item['name'])[:16]
