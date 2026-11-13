"""SteamVR's own switch for VROSTBridge's driver on the rendering PC (1.0.0.15).

After SteamVR crashes it can start the next time in safe mode, which turns every add-on
driver off and records that in steamvr.vrsettings, in the driver's own section
("blocked_by_safe_mode"). The block outlasts safe mode: SteamVR keeps the driver off until
someone turns it on again in its add-on manager. VROSTBridge starts SteamVR on the
rendering PC without a window, so nobody sees SteamVR's notice; the driver never loads, and
the headset PC keeps looking for a host that never appears (a 1.0.0.14 report). A driver
turned off by hand in the add-on manager ("enable": false) has the same effect.

The host reads the setting before it starts SteamVR and stops at once with the reason, and
recognises the same refusal in SteamVR's server log if it appears anyway. Turning the
driver back on is offered (on the host page, and from the headset PC) and done only on
request, and only while SteamVR is closed: SteamVR writes the file again when it exits.
Only that section of the file changes; everything else is written back as it was read.
"""
import json
import os
from pathlib import Path
import re
import tempfile

DRIVER = 'alvr_server'
SECTION = 'driver_' + DRIVER

SAFE_MODE_TEXT = ("SteamVR on the rendering PC turned VROSTBridge's driver off after an earlier crash (safe mode). "
                  "Choose Turn the driver back on, then start again.")
DISABLED_TEXT = ("VROSTBridge's driver is turned off in SteamVR's add-ons on the rendering PC. "
                 "Choose Turn the driver back on, then start again.")
REASONS = {'safe-mode': SAFE_MODE_TEXT, 'disabled': DISABLED_TEXT}

# SteamVR's server log when it skips the driver (vrserver.txt, 2026):
#   "Not loading driver alvr_server because it was blocked by a previous safe mode event"
#   "Not loading driver alvr_server because it is disabled in settings"
LOG_LINE = re.compile(r'Not loading driver ' + DRIVER + r' because (it was blocked by a previous safe mode event|'
                      r'it is disabled in settings)', re.I)


def settings_path(registry=None):
    """steamvr.vrsettings in the configuration folder the installed OpenVR runtime names (None if unknown)."""
    if registry is None:
        if os.name != 'nt':
            return None
        registry = Path(os.environ.get('LOCALAPPDATA', '')) / 'openvr/openvrpaths.vrpath'
    try:
        roots = json.loads(Path(registry).read_text(encoding='utf-8-sig')).get('config') or []
    except (OSError, ValueError, AttributeError):
        return None
    for root in roots[:1]:
        if isinstance(root, str) and root:
            return Path(root) / 'steamvr.vrsettings'
    return None


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def block_reason(settings):
    """'safe-mode', 'disabled' or None for a parsed steamvr.vrsettings document."""
    section = settings.get(SECTION) if isinstance(settings, dict) else None
    if not isinstance(section, dict):
        return None
    if section.get('blocked_by_safe_mode') is True:
        return 'safe-mode'
    if section.get('enable') is False:
        return 'disabled'
    return None


def driver_block(path=None):
    """{'reason', 'message'} when SteamVR will not load VROSTBridge's driver, else None."""
    path = path or settings_path()
    if path is None:
        return None
    reason = block_reason(_read(path))
    return {'reason': reason, 'message': REASONS[reason]} if reason else None


def log_block(line):
    """The reason in one line of SteamVR's server log, or None."""
    match = LOG_LINE.search(line or '')
    if not match:
        return None
    return 'safe-mode' if 'safe mode' in match.group(1).lower() else 'disabled'


def clear_driver_block(path=None, steamvr_running=False):
    """Turn the driver back on in steamvr.vrsettings. Returns {'changed', 'message'}; raises ValueError when it
    can't (SteamVR open, no settings file, a file it can't read or write)."""
    if steamvr_running:
        raise ValueError('Close SteamVR on the rendering PC first: it writes its settings again when it closes.')
    path = path or settings_path()
    if path is None:
        raise ValueError("SteamVR's settings were not found on this PC. Open SteamVR once, then try again.")
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
        settings = json.loads(text)
    except (OSError, ValueError) as error:
        raise ValueError("SteamVR's settings could not be read: " + str(error)[:160]) from None
    if not isinstance(settings, dict):
        raise ValueError("SteamVR's settings file is not in the expected form; it was left unchanged.")
    reason = block_reason(settings)
    if not reason:
        return {'changed': False, 'message': "VROSTBridge's driver is already on in SteamVR."}
    section = dict(settings[SECTION])
    section['blocked_by_safe_mode'] = False  # what SteamVR itself writes when the add-on is turned on again
    if section.get('enable') is False:
        section['enable'] = True
    settings[SECTION] = section
    # SteamVR's own layout (three spaces, " : "), written beside the file and moved over it in one step.
    data = json.dumps(settings, indent=3, separators=(',', ' : '), ensure_ascii=False) + '\n'
    try:
        handle, temporary = tempfile.mkstemp(prefix='.steamvr.vrsettings.', dir=str(path.parent))
        try:
            with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as file:
                file.write(data)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as error:
        raise ValueError("SteamVR's settings could not be written: " + str(error)[:160]) from None
    return {'changed': True, 'reason': reason,
            'message': "VROSTBridge's driver is on again in SteamVR. Start the session again."}
