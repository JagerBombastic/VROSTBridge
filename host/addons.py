"""Read-only detection of other SteamVR add-ons (drivers) that load on the host.

On the rendering PC, SteamVR only needs VROSTBridge's virtual headset driver. A headset
vendor's add-on installed here (for example a Bigscreen Beyond helper) still loads and can
act on the virtual headset, as one report showed with IPD requests. This never edits
SteamVR settings or registrations; it lists the enabled add-ons registered from outside
SteamVR, so the user can disable them in SteamVR's add-on manager.

1.0.0.12: only drivers registered from outside SteamVR count. The drivers in SteamVR's own
drivers folder ship with SteamVR and stay idle without their hardware. They used to be
checked against a list of names, which went stale as SteamVR added drivers: the 1.0.0.11
test's host listed indexcontroller, frame_hmd, frame_controller, prism, roy and tundra_labs
as third-party and suggested turning them off, Valve's Index controller driver among them,
while the streamed controllers present themselves as Index controllers.
"""
import json
import os
from pathlib import Path

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


def _inside(path, folder):
    """Whether path is folder or inside it (compared case-insensitively, as Windows does)."""
    path, folder = str(path).lower().rstrip('\\/'), str(folder).lower().rstrip('\\/')
    return path == folder or path.startswith(folder + '\\') or path.startswith(folder + '/')


def detect(registry=None, own_driver=None, own_directory=None):
    """Return the enabled SteamVR drivers registered from outside SteamVR, other than VROSTBridge's own."""
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
    runtimes = []
    for runtime in paths.get('runtime', [])[:2]:
        if isinstance(runtime, str) and runtime:
            try:
                runtimes.append(Path(runtime).resolve())
            except OSError:
                pass
    found = {}
    for entry in (paths.get('external_drivers', []) or [])[:96]:
        if not isinstance(entry, str) or not entry:
            continue
        directory = Path(entry)
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if own_path and str(resolved).lower() == own_path:
            continue
        if any(_inside(resolved, runtime) for runtime in runtimes):
            continue  # registered from SteamVR's own folder: part of SteamVR
        name = _manifest_name(directory)
        if not name or name in own or name in found:
            continue
        section = settings.get('driver_' + name, {})
        enabled = not (isinstance(section, dict) and section.get('enable') is False)
        if enabled:
            where = (name + ' ' + str(directory)).lower()
            key = next((k for k in KNOWN_ADVICE if k in where), None)
            found[name] = {'name': name, 'source': 'registered', 'path': str(directory)[:260],
                           'advice': KNOWN_ADVICE.get(key, 'Third-party SteamVR add-on loaded on the host.')}
    return sorted(found.values(), key=lambda item: item['name'].lower())[:16]
