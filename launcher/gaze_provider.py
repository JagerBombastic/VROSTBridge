#!/usr/bin/env python3
"""Managed, local OSC gaze adapter. Provider owns camera inference/calibration.

VRG1 timestamps are receipt times from time.monotonic_ns(), not camera exposure times: CLOCK_MONOTONIC on
Linux, QueryPerformanceCounter on Windows (Python 3.13), the clocks of the native client's monotonicNs()
(std::chrono::steady_clock) on each.
Binocular OSC is reduced to a combined orientation; no vergence is preserved.

1.0.0.7: the native client's gaze input is an AF_UNIX datagram socket at a path on Linux (--gaze-socket) and a
UDP port on 127.0.0.1 on Windows (--gaze-udp PORT), whose Python has no AF_UNIX. A target is either: a path (str or
os.PathLike) or a (host, port) pair on this PC (gaze_target). Gaze never leaves this PC.
"""
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import struct
import threading
import time


def angles_quaternion(pitch, yaw):
    if not all(math.isfinite(v) and abs(v) <= 180 for v in (pitch, yaw)):
        raise ValueError('Invalid gaze angles')
    x, y = math.radians(-pitch)/2, math.radians(-yaw)/2
    return (math.sin(x)*math.cos(y), math.cos(x)*math.sin(y),
            math.sin(x)*math.sin(y), math.cos(x)*math.cos(y))


def messages(data, depth=0):
    if len(data) > 16384 or depth > 4:
        raise ValueError('Oversized OSC packet')
    if data.startswith(b'#bundle\0'):
        # Only immediate bundles: never stamp scheduled/stale samples as fresh.
        if len(data) < 16 or data[8:16] != struct.pack('>Q', 1):
            raise ValueError('Expected immediate OSC bundle')
        offset = 16
        while offset < len(data):
            if offset+4 > len(data): raise ValueError('Truncated OSC bundle')
            size = struct.unpack_from('>I', data, offset)[0]; offset += 4
            if not size or offset+size > len(data): raise ValueError('Invalid bundle element')
            yield from messages(data[offset:offset+size], depth+1)
            offset += size
        return
    def string(offset):
        end = data.index(b'\0', offset)
        return data[offset:end].decode('ascii'), (end+4)&~3
    address, offset = string(0); types, offset = string(offset)
    if address == '/tracking/eye/CenterPitchYaw': count = 2
    elif address == '/tracking/eye/LeftRightPitchYaw': count = 4
    else: return  # Providers also emit eyelids/expression messages.
    if types != ','+'f'*count or len(data) != offset+4*count:
        raise ValueError('Invalid OSC gaze payload')
    values = struct.unpack_from('>'+'f'*count, data, offset)
    if not all(math.isfinite(v) and abs(v) <= 180 for v in values):
        raise ValueError('Invalid gaze angles')
    if count == 2: yield angles_quaternion(*values)
    else:
        left, right = angles_quaternion(*values[:2]), angles_quaternion(*values[2:])
        sign = 1 if sum(a*b for a,b in zip(left,right)) >= 0 else -1
        combined = tuple(a+sign*b for a,b in zip(left,right))
        norm = math.sqrt(sum(v*v for v in combined))
        if norm < 1e-6: raise ValueError('Inconsistent binocular gaze')
        yield tuple(v/norm for v in combined)


class NoGaze(ValueError):
    """A valid OSC packet without a gaze sample (Baballonia also sends eyelids and expressions)."""


def parse_osc(data):
    parsed = list(messages(data))
    if not parsed: raise NoGaze('No gaze sample')
    return parsed[-1]


def gaze_angles(q):
    """(yaw, pitch) in degrees of a head-relative gaze quaternion (x, y, z, w): yaw positive to the right, pitch
    positive up, as the host measures them (patch 0020, vrostbridge_foveation.rs gaze_angles)."""
    x, y, z, w = q
    # The rotated forward vector (0, 0, -1).
    dx = -(2 * (x * z + y * w))
    dy = -(2 * (y * z - x * w))
    dz = -(1 - 2 * (x * x + y * y))
    return math.degrees(math.atan2(dx, -dz)), math.degrees(math.atan2(dy, math.hypot(dx, dz)))


# 1.0.0.6: how far the gaze reached this session, for the eye-tracking card and reports. The 1.0.0.5 test found the
# sharp region following the eyes only near the middle of the view; Baballonia's model reports only the range of
# eye movement its calibration covered (and at most 45 degrees from straight ahead, the range its trainer uses),
# so a calibration with little head movement leaves the region short of where the user looks. Counts per whole
# degree only, never a sample or a time: the reach is the 1st and 99th percentile of each axis, so a stray value
# (a blink) does not count.
REACH_LIMIT_DEG = 90
REACH_MIN_SAMPLES = 100
REACH_PERCENTILE = 0.01


class GazeReach:
    """Per-degree counts of gaze yaw and pitch; reach() gives how far it went each way."""
    def __init__(self):
        self.reset()

    def reset(self):
        size = 2 * REACH_LIMIT_DEG + 1
        self.yaw, self.pitch, self.samples = [0] * size, [0] * size, 0

    def note(self, q):
        try:
            yaw, pitch = gaze_angles(q)
        except (ValueError, TypeError):
            return
        if not (math.isfinite(yaw) and math.isfinite(pitch)):
            return
        for counts, value in ((self.yaw, yaw), (self.pitch, pitch)):
            counts[max(-REACH_LIMIT_DEG, min(REACH_LIMIT_DEG, int(round(value)))) + REACH_LIMIT_DEG] += 1
        self.samples += 1

    @staticmethod
    def _percentile(counts, total, fraction):
        wanted, seen = fraction * total, 0
        for index, count in enumerate(counts):
            seen += count
            if seen > wanted:
                return index - REACH_LIMIT_DEG
        return REACH_LIMIT_DEG

    def reach(self):
        """{'samples', 'left', 'right', 'up', 'down'} in whole degrees (how far each way; negative when the gaze never
        crossed straight ahead that way), or None before REACH_MIN_SAMPLES samples."""
        total = self.samples
        if total < REACH_MIN_SAMPLES:
            return None
        low, high = REACH_PERCENTILE, 1 - REACH_PERCENTILE
        return {'samples': total,
                'left': -self._percentile(self.yaw, total, low), 'right': self._percentile(self.yaw, total, high),
                'down': -self._percentile(self.pitch, total, low), 'up': self._percentile(self.pitch, total, high)}


def gaze_target(target):
    """The native client's gaze input as the relay sends to it: None (between sessions); a socket path as str (an
    AF_UNIX datagram socket, Linux); or ('127.0.0.1', port) for UDP on this PC (Windows). 'localhost' and other
    IPv4 loopback addresses are accepted for the host (the native client listens on 127.0.0.1); anything else raises
    ValueError: gaze never leaves this PC."""
    if target is None:
        return None
    if isinstance(target, (tuple, list)):
        if len(target) != 2:
            raise ValueError('A gaze UDP target is (host, port).')
        host, port = target
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise ValueError('Invalid gaze UDP port: ' + repr(port)[:40])
        host = '127.0.0.1' if str(host).strip().lower() == 'localhost' else str(host).strip()
        try:
            address = ipaddress.ip_address(host)
            loopback = address.version == 4 and address.is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError('The gaze target must be on this PC (127.0.0.1), not ' + host[:64] + '.')
        return (host, port)
    if isinstance(target, (str, os.PathLike)):
        path = os.fspath(target)
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        return path or None
    raise ValueError('Invalid gaze target: ' + type(target).__name__)


def free_udp_port(host='127.0.0.1'):
    """A UDP port on this PC that is free now (Windows: the native client's --gaze-udp; it binds the port itself, so
    another program could take it in between: the client then reports it in use)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def gaze_quaternion(yaw, pitch):
    """The head-relative gaze quaternion (x, y, z, w) looking yaw degrees right and pitch degrees up: the inverse of
    gaze_angles (a turn about Y by -yaw, then about X by pitch; no roll, which a gaze direction does not have)."""
    half_yaw, half_pitch = math.radians(-yaw) / 2, math.radians(pitch) / 2
    sy, cy, sx, cx = math.sin(half_yaw), math.cos(half_yaw), math.sin(half_pitch), math.cos(half_pitch)
    return (cy * sx, sy * cx, -sy * sx, cy * cx)


# 1.0.0.8: the eye tracker's straight ahead, kept centred. In the 1.0.0.7 test a fresh calibration put the gaze
# 15-20 degrees right of where the user looked: over a session it reached 40 degrees right but only 5 left, so the
# sharp region sat against the right edge of the view while they looked straight ahead. Eyes rest near straight
# ahead most of the time (the head does the large turns), so the middle of a session's gaze is a reliable reference:
# where it sits further from straight ahead than a resting gaze does (CENTRE_YAW_FREE, CENTRE_PITCH_FREE), that
# excess is taken off every sample, in small steps as the session goes on and remembered per calibration (by the
# trained model's file name), so the next session starts centred. A new calibration starts at no correction.
# Only the samples' middle is used (per whole degree, never a trace); nothing else about the gaze is kept.
CENTRE_MIN_SAMPLES = 600       # about 11 s of Baballonia's 55 samples a second
CENTRE_EVERY = 100             # samples between updates
CENTRE_STEP = 0.3              # share of the remaining difference applied at each update
CENTRE_YAW_FREE = 3.0          # degrees either way a resting gaze is left as it is
CENTRE_PITCH_FREE = (-12.0, 4.0)
CENTRE_MAX = (25.0, 15.0)      # the most corrected (yaw, pitch), degrees
OFF_CENTRE_YAW = 10.0          # a calibration whose middle sits this far to a side looks off-centre
OFF_CENTRE_PITCH = (-20.0, 10.0)
CENTRE_FILE_VERSION = 1


class GazeCentre:
    """The correction that keeps the eye tracker's straight ahead centred (see the notes above).

    store: a JSON file for the corrections per calibration; model(): the current calibration's model path (its
    file name is the key); enabled(): whether samples are corrected (the middle is measured either way)."""

    def __init__(self, store=None, model=None, enabled=None):
        self.store = Path(store) if store else None
        self.model = model or (lambda: '')
        self.enabled = enabled or (lambda: True)
        self.lock = threading.Lock()
        self.middle = GazeReach()
        self.key = None
        self.yaw = self.pitch = 0.0
        self.median = None
        self.since_update = 0

    @staticmethod
    def _key(model):
        name = Path(str(model or '')).name
        return name[:120] if name else 'default'

    def _load(self):
        try:
            value = json.loads(self.store.read_text(encoding='utf-8')) if self.store and self.store.is_file() else {}
        except (OSError, ValueError):
            value = {}
        return value if isinstance(value, dict) and isinstance(value.get('models'), dict) else {'models': {}}

    def begin(self):
        """A session starts: the current calibration's correction, and the middle measured afresh."""
        try:
            key = self._key(self.model())
        except Exception:
            key = 'default'
        saved = self._load()['models'].get(key) if self.store else None
        with self.lock:
            self.key = key
            self.middle.reset()
            self.median = None
            self.since_update = 0
            self.yaw = self.pitch = 0.0
            if isinstance(saved, dict):
                for name, limit in (('yaw', CENTRE_MAX[0]), ('pitch', CENTRE_MAX[1])):
                    value = saved.get(name)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        setattr(self, name, max(-limit, min(limit, float(value))))

    def note(self, yaw, pitch):
        """One raw sample's angles (degrees)."""
        with self.lock:
            self.middle.yaw[max(-REACH_LIMIT_DEG, min(REACH_LIMIT_DEG, int(round(yaw)))) + REACH_LIMIT_DEG] += 1
            self.middle.pitch[max(-REACH_LIMIT_DEG, min(REACH_LIMIT_DEG, int(round(pitch)))) + REACH_LIMIT_DEG] += 1
            self.middle.samples += 1
            self.since_update += 1
            if self.since_update < CENTRE_EVERY or self.middle.samples < CENTRE_MIN_SAMPLES:
                return
            self.since_update = 0
            total = self.middle.samples
            median_yaw = GazeReach._percentile(self.middle.yaw, total, 0.5)
            median_pitch = GazeReach._percentile(self.middle.pitch, total, 0.5)
            self.median = (median_yaw, median_pitch)
            target_yaw = median_yaw - max(-CENTRE_YAW_FREE, min(CENTRE_YAW_FREE, median_yaw))
            target_pitch = median_pitch - max(CENTRE_PITCH_FREE[0], min(CENTRE_PITCH_FREE[1], median_pitch))
            target_yaw = max(-CENTRE_MAX[0], min(CENTRE_MAX[0], target_yaw))
            target_pitch = max(-CENTRE_MAX[1], min(CENTRE_MAX[1], target_pitch))
            before = (round(self.yaw, 1), round(self.pitch, 1))
            self.yaw += CENTRE_STEP * (target_yaw - self.yaw)
            self.pitch += CENTRE_STEP * (target_pitch - self.pitch)
            changed = before != (round(self.yaw, 1), round(self.pitch, 1))
        if changed:
            self._save()

    def _save(self):
        if not self.store:
            return
        with self.lock:
            key, yaw, pitch = self.key or 'default', round(self.yaw, 2), round(self.pitch, 2)
        value = self._load()
        models = value['models']
        models[key] = {'yaw': yaw, 'pitch': pitch}
        while len(models) > 8:  # the latest calibrations only
            models.pop(next(iter(models)))
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.store.with_name('.' + self.store.name + '.tmp')
            temporary.write_text(json.dumps({'version': CENTRE_FILE_VERSION, 'models': models}) + '\n', encoding='utf-8')
            os.replace(temporary, self.store)
        except OSError:
            pass

    def apply(self, q, yaw, pitch):
        """The sample to forward: q turned by the correction (when on and noticeable), else q itself."""
        with self.lock:
            correct_yaw, correct_pitch = self.yaw, self.pitch
        if abs(correct_yaw) < 0.25 and abs(correct_pitch) < 0.25:
            return q
        try:
            if not self.enabled():
                return q
        except Exception:
            return q
        return gaze_quaternion(yaw - correct_yaw, pitch - correct_pitch)

    def reset(self):
        """Forget this calibration's correction (the eye-tracking card's Reset centring)."""
        with self.lock:
            self.yaw = self.pitch = 0.0
            self.middle.reset()
            self.median = None
            self.since_update = 0
        self._save()

    def status(self):
        """{'yaw', 'pitch'}: the correction (degrees taken off: positive = the gaze was to the right / up), 'middle':
        [yaw, pitch] of this session's samples, 'off_centre': whether the calibration itself looks off-centre."""
        with self.lock:
            median = self.median
            value = {'yaw': round(self.yaw, 1), 'pitch': round(self.pitch, 1), 'samples': self.middle.samples,
                     'middle': list(median) if median else None}
        value['off_centre'] = bool(median and (abs(median[0]) >= OFF_CENTRE_YAW or not
                                               OFF_CENTRE_PITCH[0] <= median[1] <= OFF_CENTRE_PITCH[1]))
        try:
            value['enabled'] = bool(self.enabled())
        except Exception:
            value['enabled'] = True
        return value


# 1.0.0.4: pauses between gaze samples, counted by length (seconds) for the reports. Baballonia sends a
# sample for each camera picture it processes (54 a second in the 1.0.0.3 test, 12 with the processor
# held at 0.5 GHz); the host keeps the sharp region in place through a pause of up to about a second.
GAP_BANDS = ((.1, 'over_100ms'), (.25, 'over_250ms'), (1.0, 'over_1s'), (5.0, 'over_5s'))


class GazeRelay:
    """Baballonia's OSC eye output (127.0.0.1:9005) to the native client's gaze socket.

    RC4.5: the relay lives as long as eye tracking runs, not one session: Baballonia's OSC sender
    stops for good after a single refused send, so the port must stay open from before Baballonia
    starts until after it stops. Between sessions there is no client socket (target None) and
    samples are only counted. 1.0.0.4: pauses between samples are counted by length from each
    session's start (reset_gaps), never the samples themselves. 1.0.0.7: the target is a socket
    path (Linux) or ('127.0.0.1', port) (Windows: UDP; see gaze_target)."""
    def __init__(self, path=None, port=9005, centre=None):
        target = gaze_target(path)  # ValueError before anything is opened
        self.centre = centre  # 1.0.0.8: GazeCentre, or None (samples forwarded as they come)
        self.path = None
        self.outgoing = self.outgoing_udp = None
        self._sockets = threading.Lock()
        self.incoming = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 1.0.0.14: room for a few hundred OSC messages when this thread is held off (the kernel caps it at
            # net.core.rmem_max, launcher/network_settings.py); a full buffer drops samples and shows as gaze gaps.
            try: self.incoming.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            except (OSError, AttributeError): pass  # a stand-in socket module in a test
            self.incoming.bind(('127.0.0.1', port))
        except BaseException: self.incoming.close(); raise
        self.incoming.settimeout(.1)
        # Linux: the native client's AF_UNIX datagram socket. Windows' Python has no AF_UNIX (an AttributeError):
        # there the relay sends UDP to 127.0.0.1, with a socket opened when a UDP target is set.
        if hasattr(socket, 'AF_UNIX'):
            try:self.outgoing = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            except BaseException:self.incoming.close();raise
            self.outgoing.setblocking(False)
        try: self.set_target(target)
        except BaseException: self._close_sockets(); raise
        self.cancel = threading.Event()
        self.received = self.forwarded = self.invalid = self.unsent = self.other = 0
        self.last_received = self.last_forwarded = 0
        self.gaps = dict.fromkeys([name for _, name in GAP_BANDS], 0)
        self.longest_gap = 0.0
        self.reach = GazeReach()
        self.thread = threading.Thread(target=self.run, daemon=True, name='VROSTBridge-gaze-relay')

    def start(self): self.thread.start(); return self
    def set_target(self, path):
        """The running native client's gaze input (a socket path, or ('127.0.0.1', port) for UDP), or None between
        sessions. ValueError for a target off this PC; OSError when this Python cannot send to it (a socket path on
        Windows) or the UDP socket cannot be opened."""
        target = gaze_target(path)
        if isinstance(target, tuple):
            self._udp_socket()  # opened before the target is used
        elif target is not None and self.outgoing is None:
            raise OSError('This Python has no Unix sockets (Windows): the gaze target must be (\'127.0.0.1\', port).')
        self.path = target

    def _udp_socket(self):
        with self._sockets:
            if self.outgoing_udp is None:
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try: udp.setblocking(False)
                except BaseException: udp.close(); raise
                self.outgoing_udp = udp
            return self.outgoing_udp

    def _close_sockets(self):
        for name in ('incoming', 'outgoing', 'outgoing_udp'):
            sock = getattr(self, name, None)
            if sock is not None:
                try: sock.close()
                except OSError: pass

    def reset_gaps(self):
        """Start counting pauses afresh (each session), and the gaze's reach (1.0.0.6) and centring (1.0.0.8)."""
        self.gaps = dict.fromkeys([name for _, name in GAP_BANDS], 0)
        self.longest_gap = 0.0
        self.reach.reset()
        if getattr(self, 'centre', None) is not None:
            self.centre.begin()

    def reset_reach(self):
        """Measure how far the gaze reaches afresh (1.0.0.6: the eye-tracking card's Measure again)."""
        self.reach.reset()

    def note_gap(self, gap):
        """Count one pause between samples (seconds)."""
        for limit, name in GAP_BANDS:
            if gap >= limit:
                self.gaps[name] += 1
        self.longest_gap = max(self.longest_gap, gap)
    def status(self):
        now = time.monotonic()
        # Baballonia sends about 30 samples a second; half a second without one is a pause.
        return {'state': 'receiving' if self.last_received and now-self.last_received < .5 else 'waiting',
                'forwarding': bool(self.last_forwarded and now-self.last_forwarded < .5),
                'received':self.received, 'forwarded':self.forwarded, 'invalid':self.invalid, 'unsent':self.unsent,
                'other':self.other,
                'idle_s': round(now-self.last_received, 1) if self.last_received else None,
                'gaps': dict(self.gaps, longest_ms=int(round(1000 * self.longest_gap))),
                'reach': self.reach.reach(),
                **({'centre': self.centre.status()} if self.centre is not None else {})}
    def run(self):
        try:
            while not self.cancel.is_set():
                try: data,_ = self.incoming.recvfrom(16385)
                except socket.timeout: continue
                except OSError:
                    if self.cancel.is_set(): break
                    raise
                try: q = parse_osc(data)
                except NoGaze: self.other += 1; continue
                except (ValueError, UnicodeError, struct.error): self.invalid += 1; continue
                stamp = time.monotonic_ns()
                arrived = time.monotonic()
                if self.last_received:
                    self.note_gap(arrived - self.last_received)
                self.received += 1; self.last_received = arrived
                self.reach.note(q)
                if self.centre is not None:
                    try:
                        yaw, pitch = gaze_angles(q)
                        if math.isfinite(yaw) and math.isfinite(pitch):
                            self.centre.note(yaw, pitch)
                            q = self.centre.apply(q, yaw, pitch)
                    except (ValueError, TypeError):
                        pass
                path = self.path
                if path is None:
                    self.unsent += 1; continue
                udp = isinstance(path, tuple)
                outgoing = self.outgoing_udp if udp else self.outgoing
                if outgoing is None:
                    self.unsent += 1; continue
                try:
                    # +0.0 normalizes negative zero so the wire text is stable.
                    outgoing.sendto(('VRG1 %d 1 %.9g %.9g %.9g %.9g'%(stamp,*(value+0.0 for value in q))).encode(), path)
                    self.forwarded += 1; self.last_forwarded = time.monotonic()
                except OSError as error:
                    self.unsent += 1  # No replay: missing consumer or full socket drops this sample.
                    if udp and isinstance(error, ConnectionError):
                        self._reopen_udp(outgoing)  # Windows reports an earlier refused datagram this way
        finally:
            self._close_sockets()

    def _reopen_udp(self, broken):
        """A fresh UDP socket after Windows reported a refused datagram on this one (the client not listening yet)."""
        with self._sockets:
            if self.outgoing_udp is not broken:
                return
            self.outgoing_udp = None
        try: broken.close()
        except OSError: pass
        try: self._udp_socket()
        except OSError: pass

    def close(self):
        self.cancel.set()
        if self.thread.is_alive(): self.thread.join(timeout=2)
        else: self._close_sockets()


def run(path, port):
    relay = GazeRelay(path, port).start()
    try:
        while relay.thread.is_alive(): relay.thread.join(.5)
    except KeyboardInterrupt: pass
    finally: relay.close()

if __name__ == '__main__':
    p=argparse.ArgumentParser();target=p.add_mutually_exclusive_group(required=True)
    target.add_argument('--socket',help='the native client\'s gaze socket path (Linux)')
    target.add_argument('--udp',type=int,help='the native client\'s gaze UDP port on 127.0.0.1 (Windows)')
    p.add_argument('--port',type=int,default=9005)
    args=p.parse_args();run(args.socket if args.socket else ('127.0.0.1',args.udp),args.port)
