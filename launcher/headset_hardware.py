"""The headset's USB connection and the controllers' tracking, as the headset PC sees them (1.0.0.15).

Three faults from 1.0.0.14 sessions ended in messages that pointed nowhere near the cause:

- **USB hubs nested too deep (Linux).** USB allows five hubs between a PC and a device, and Linux
  refuses a hub any deeper ("hub nested too deep"), with everything on it. A headset brings hubs
  of its own (a Bigscreen Beyond three), so a dock that adds two and a PC whose own ports sit
  behind a hub are enough: the headset half-appears and its controllers' radios never show up.
  The PC's USB tree in sysfs shows such a hub plainly: a hub six levels down. No permissions are
  needed to read it.
- **The headset's USB dropping out.** When the headset's USB loses power for a moment, the
  headset and the controllers (whose radios are in the headset) disconnect at the same instant
  and come back a few seconds later. SteamVR then refuses the next frame and the session ended
  with "SteamVR texture submission failed: 105". The native client reports each device's state
  as it changes (HEAD_TRACKING and CONTROLLER_STATE lines), so the companion sees the headset
  and a controller disconnect within a second of each other, and says what happened instead.
- **Controllers that can't see the base stations.** A controller that is on and connected but
  not tracked while the headset tracks has its sensors blinded: misted up after coming in from
  the cold, covered, or with the base stations out of its view. The stream itself is fine, so
  nothing said so.

None of this changes what the session does; it only says what went wrong and what to do.
"""
import json
from pathlib import Path
import re
import sys
import time

# Linux (drivers/usb/core/hub.c): a hub at this depth (root hub 0, a device on a root port 1) is refused.
MAX_TOPO_LEVEL = 6
USB_DEVICES = '/sys/bus/usb/devices'
_DEVPATH = re.compile(r'^(\d+)-(\d+(?:\.\d+)*)$')

DEEP_TEXT = ("A USB hub on this PC is nested too deep (five hubs in a row is the most), so Linux turns it off with "
             "what's on it. Plug the headset into a port on the PC itself, or into a dock plugged straight in.")
DROP_TEXT = ("The headset's USB dropped out: the headset and its controllers disconnected at the same moment. That is "
             "usually its 5 V supply: use a port on the PC itself, or a dock or hub with a stronger power supply.")
HEADSET_LOST_TEXT = ("The headset disconnected from this PC during the session. Check its cables; if it happens again, its "
                     "USB port may be short of power: use a port on the PC itself, or a powered dock or hub.")
HANDS = {'left': 'left controller', 'right': 'right controller'}


def _untracked_text(hands):
    names = [HANDS[hand] for hand in ('left', 'right') if hand in hands]
    subject = 'the controllers' if len(names) == 2 else 'the ' + names[0]
    verb = "they're" if len(names) == 2 else "it's"
    return ("SteamVR can't see " + subject + ": " + verb + " on but not tracked. Wipe the sensor windows (they mist up "
            "when it's cold) and check that the base stations are on.")


def _read(path):
    try:
        return Path(path).read_text(encoding='ascii', errors='replace').strip()
    except OSError:
        return ''


def deep_hubs(root=USB_DEVICES):
    """USB hubs Linux refuses for their depth: [{'path': '3-1.2.3.4.5.6', 'level': 6}]. Empty off Linux."""
    folder = Path(root)
    try:
        entries = sorted(folder.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    found = []
    for entry in entries[:512]:
        match = _DEVPATH.match(entry.name)
        if not match:
            continue  # root hubs (usbN) and interfaces (3-1:1.0)
        level = len(match.group(2).split('.'))
        if level >= MAX_TOPO_LEVEL and _read(entry / 'bDeviceClass') == '09':
            found.append({'path': entry.name, 'level': level,
                          'id': (_read(entry / 'idVendor') + ':' + _read(entry / 'idProduct')).strip(':')})
    return found


def usb_depth(root=None):
    """{'state': 'deep'|'ok', 'message', 'hubs'}; always 'ok' off Linux (Windows has no such tree to read)."""
    if root is None:
        if not sys.platform.startswith('linux'):
            return {'state': 'ok', 'message': '', 'hubs': []}
        root = USB_DEVICES
    hubs = deep_hubs(root)
    return {'state': 'deep', 'message': DEEP_TEXT, 'hubs': hubs[:8]} if hubs else {'state': 'ok', 'message': '', 'hubs': []}


def _values(text):
    return dict(item.split('=', 1) for item in text.split() if '=' in item)


class HeadsetWatch:
    """Follows the native client's HEAD_TRACKING, CONTROLLER_STATE and INPUT lines during a session."""
    DROP_WINDOW_S = 1.5    # the headset and a controller disconnecting this close together: the USB dropped out
    FATAL_AFTER_S = 30     # a SteamVR failure this soon after a drop or a lost headset is put down to it
    UNTRACKED_S = 8        # a controller connected but untracked this long, while the headset tracks
    NOTICE_S = 600         # how long a drop stays on the pages after it happened
    HUD_S = 30             # how long the in-headset line shows an untracked controller

    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.reset()

    def reset(self):
        self.head = {'connected': None, 'tracked': None, 'lost_at': None, 'tracked_since': None}
        self.hands = {hand: {'connected': None, 'tracked': None, 'lost_at': None, 'untracked_since': None}
                      for hand in HANDS}
        self.drop = None          # {'at', 'utc', 'controllers'}
        self.drops = 0
        self.untracked = None     # {'hands', 'since', 'raised_at'}
        self.untracked_episodes = 0

    # Lines -------------------------------------------------------------------------------
    def observe(self, line):
        """Read one native line. Returns 'drop' or 'untracked' when that is newly seen, else None."""
        now = self.clock()
        if line.startswith('HEAD_TRACKING '):
            values = _values(line[len('HEAD_TRACKING '):])
            connected, tracked = values.get('connected') == '1', values.get('tracked') == '1'
            if self.head['connected'] and not connected:
                self.head['lost_at'] = now
            if tracked and not self.head['tracked']:
                self.head['tracked_since'] = now
            elif not tracked:
                self.head['tracked_since'] = None
            self.head.update(connected=connected, tracked=tracked)
            return self._check_drop(now) if not connected else self._check_untracked(now)
        if line.startswith('CONTROLLER_STATE '):
            values = _values(line[len('CONTROLLER_STATE '):])
            hand = values.get('hand')
            if hand not in self.hands:
                return None
            self._hand(hand, values.get('connected') == '1', values.get('tracked') == '1', now)
            event = self._check_drop(now) if values.get('connected') != '1' else None
            return event or self._check_untracked(now)
        if line.startswith('INPUT '):
            # The periodic summary: the same states, for a controller whose state lines came before the companion read them.
            try:
                value = json.loads(line[len('INPUT '):])
            except (ValueError, TypeError):
                return None
            if not isinstance(value, dict):
                return None
            for hand in HANDS:
                state = value.get(hand)
                if isinstance(state, dict) and isinstance(state.get('connected'), bool) and isinstance(state.get('tracked'), bool):
                    if self.hands[hand]['connected'] is None or (state['connected'], state['tracked']) != (
                            self.hands[hand]['connected'], self.hands[hand]['tracked']):
                        self._hand(hand, state['connected'], state['tracked'], now)
            return self._check_untracked(now)
        return None

    def _hand(self, hand, connected, tracked, now):
        state = self.hands[hand]
        if state['connected'] and not connected:
            state['lost_at'] = now
        if connected and not tracked:
            if state['untracked_since'] is None:
                state['untracked_since'] = now
        else:
            state['untracked_since'] = None
        state.update(connected=connected, tracked=tracked)

    def _check_drop(self, now):
        head = self.head['lost_at']
        if head is None or now - head > self.DROP_WINDOW_S:
            return None
        lost = [hand for hand, state in self.hands.items()
                if state['lost_at'] is not None and abs(state['lost_at'] - head) <= self.DROP_WINDOW_S]
        if not lost or (self.drop and self.drop['at'] >= head - self.DROP_WINDOW_S):
            return None
        self.drop = {'at': now, 'utc': self.wall(), 'controllers': len(lost)}
        self.drops += 1
        self.untracked = None
        return 'drop'

    def _check_untracked(self, now):
        head_steady = (self.head['tracked'] and self.head['tracked_since'] is not None
                       and now - self.head['tracked_since'] >= self.UNTRACKED_S)
        hands = sorted(hand for hand, state in self.hands.items()
                       if state['untracked_since'] is not None and now - state['untracked_since'] >= self.UNTRACKED_S)
        if not head_steady or not hands:
            if self.untracked and not hands:
                self.untracked = None
            return None
        if self.untracked and self.untracked['hands'] == hands:
            return None
        new = self.untracked is None
        self.untracked = {'hands': hands, 'since': min(self.hands[hand]['untracked_since'] for hand in hands),
                          'raised_at': now}
        if new:
            self.untracked_episodes += 1
            return 'untracked'
        return None

    # What to say -------------------------------------------------------------------------
    def fatal_message(self, message):
        """The message for the native client's fatal error: the dropped USB when that came just before."""
        now = self.clock()
        if self.drop and now - self.drop['at'] <= self.FATAL_AFTER_S:
            return DROP_TEXT
        lost = self.head['lost_at']
        if lost is not None and now - lost <= self.FATAL_AFTER_S and self.head['connected'] is False:
            return HEADSET_LOST_TEXT
        return message

    def log_text(self, event):
        if event == 'drop':
            return ('HEADSET_USB dropped: the headset and ' + str(self.drop['controllers']) + ' controller'
                    + ('s' if self.drop['controllers'] != 1 else '') + ' disconnected together (USB power or cable).')
        if event == 'untracked' and self.untracked:
            return 'CONTROLLERS not tracked while the headset tracks: ' + ', '.join(self.untracked['hands']) + '.'
        return ''

    def status(self):
        now = self.clock()
        if self.drop and now - self.drop['at'] <= self.NOTICE_S:
            return {'state': 'drop', 'message': DROP_TEXT, 'drop_utc': round(self.drop['utc'], 1), 'drops': self.drops}
        if self.untracked:
            return {'state': 'untracked', 'message': _untracked_text(self.untracked['hands']),
                    'hands': list(self.untracked['hands']), 'seconds': round(now - self.untracked['since'], 1)}
        return {'state': 'ok', 'message': '', 'drops': self.drops, 'untracked_episodes': self.untracked_episodes}

    def short(self):
        """The in-headset line for an untracked controller, for its first HUD_S seconds."""
        if not self.untracked or self.clock() - self.untracked['raised_at'] > self.HUD_S:
            return ''
        hands = self.untracked['hands']
        return ('Controllers' if len(hands) == 2 else HANDS[hands[0]].capitalize()) + ' not tracked: wipe the sensors, check the base stations'


def summary(watch, depth):
    """What the pages and reports show: a dropped USB first, then a hub too deep, then an untracked controller."""
    live = watch.status() if watch else {'state': 'ok', 'message': ''}
    if live.get('state') == 'drop':
        return dict(live, usb=depth)
    if (depth or {}).get('state') == 'deep':
        return {'state': 'deep', 'message': depth['message'], 'hubs': depth.get('hubs', []), 'live': live}
    return dict(live, usb=depth or {'state': 'ok'})
