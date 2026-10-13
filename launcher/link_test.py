"""Short network test that imitates the video stream, used to suggest a bitrate.

The right video bitrate depends on the whole path between the two PCs, not on
the adapters' link speed: gigabit Ethernet through a cheap switch, powerline
adapters and Wi-Fi can all report a high speed and still drop frames. Nor is
the stream a smooth flow. The encoder produces one compressed frame per
refresh and the sender writes it to the socket as a burst of datagrams, so
switch, access point and socket buffers that cope with the average rate can
still overflow on the bursts. This test therefore sends what the stream sends:
one burst of fixed-size UDP datagrams per frame interval, at rising rates. The
receiver measures packet and frame loss, how long each frame's burst takes to
arrive and how regularly frames complete, and recommend() turns that into a
bitrate with headroom for encoder spikes and other traffic.

Roles: the host sends, as the video does; the client receives and decides.
The client opens its receiver first, then asks the host (over the session
link) for a test with a random 64-bit test id, the frame rate and the rate
ladder. The host must send only to the address of the client that asked,
never to an address named in a request; LinkTestSender validates the plan so
a request cannot ask for an unbounded flood.

Wire format, little endian, one datagram per packet:
    magic b'VRLT' | version u8 (1) | flags u8 | step u16 | test id u64 |
    frame u32 | packet u16 | packets in frame u16 | send time u64 (ns)
followed by zero padding to the packet size. Frames are numbered from 0 in
each step, and every frame of a step has the same number of packets. The
send time comes from the sender's monotonic clock; only differences between
datagrams from the same sender mean anything. After each step the sender
sends a header-only end-of-step datagram three times (flags bit 0; the frame
field holds the number of frames sent and the packets field the packets per
frame), so the receiver can also count frames lost at the end of a step.

1.0.0.5: the test measures the network rather than how busy the receiving companion is.
The receiver is a thread of the companion, which runs other threads, and Python runs one
thread at a time: datagrams could wait in the socket until the receiver's turn came, so a
frame's arrival looked spread over most of a frame interval (judged "late") and, when the
socket's buffer filled meanwhile, datagrams that had reached the PC were dropped there and
counted as lost on the network. On Linux the receiver now takes each datagram's arrival
time from the kernel (SO_TIMESTAMPNS) and counts the datagrams its own buffer dropped
(SO_RXQ_OVFL), which do not count against the network. The client also sends one extra
step first, at the lowest rate, that wakes the path (an adapter leaving power saving, the
first packets of a flow) and is not judged. And the rate the stream is paced at is set by
loss that grows with the rate (CAPACITY_LOSS_PCT), not by one stray lost packet.
"""
import ipaddress
import math
import socket
import struct
import sys
import time

MAGIC = b'VRLT'
VERSION = 1
FLAG_STEP_END = 0x01
HEADER = struct.Struct('<4sBBHQIHHQ')
HEADER_SIZE = HEADER.size
# frame, packet, packets in frame and send time: the fields that change per datagram.
_TAIL = struct.Struct('<IHHQ')
_TAIL_OFFSET = 16

# Finer steps where home Wi-Fi tops out, so the clean rate (bitrate and pacing) is closer to the truth.
LADDER_MBPS = (80, 150, 200, 250, 300, 400, 500, 600, 750, 900)
LINK_KINDS = ('wired', 'wifi', 'unknown')
DEFAULT_PACKET_SIZE = 1400
DEFAULT_MAX_MBPS = 600
STEP_GAP_SECONDS = 0.3
END_MARKER_COPIES = 3
MAX_STEPS = 16
MAX_FRAMES_PER_STEP = 4096
MAX_RATE_MBPS = 10000

# recommend() thresholds.
CLEAN_PACKET_LOSS_PCT = 0.05
CLEAN_FRAME_LOSS_PCT = 0.2
CLEAN_SPREAD_FRACTION = 0.6
SENDER_FRACTION = 0.9
MIN_MBPS = 20
FALLBACK_MBPS = 30
# 1.0.0.5: a step losing this share of its packets on the way is past what the path carries (steady loss, as
# opposed to the odd lost packet, which happens at any rate); the stream is paced below it.
CAPACITY_LOSS_PCT = 1.0
# 1.0.0.5: on a wired link the automatic bitrate is at most this share of the rate its frames are paced at, so
# an average frame takes at most this share of a frame interval to arrive.
SEND_FRACTION = 0.4
# 1.0.0.8: the share of the most a path carried without loss that video is paced at. The 1.0.0.7 test paced at
# exactly that rate (500 Mbit/s on a USB Ethernet adapter whose test lost data from 600, and from 400 the evening
# before): any other traffic or a busier receiver then tipped frames into loss. 0.7 leaves room for both.
PACING_HEADROOM = 0.7
# 1.0.0.5: the client plans this many extra steps first, at the lowest rate; they wake the path and are not judged.
WARMUP_STEPS = 1

# 1.0.0.5: Linux socket options (socket(7); these values hold on x86, ARM and most other architectures).
_SO_TIMESTAMPNS = 35   # each datagram's arrival time, stamped by the kernel
_SO_RXQ_OVFL = 40      # how many datagrams the socket's own buffer has dropped
_TIMESPEC = struct.Struct('@ll')
_DROP_COUNT = struct.Struct('@I')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pack_header(step, test_id, frame, packet, packets_in_frame, send_ns, flags=0):
    """Pack and validate one header (HEADER_SIZE bytes)."""
    values = (('flags', flags, 0xFF), ('step', step, 0xFFFF), ('test id', test_id, 2 ** 64 - 1),
              ('frame', frame, 2 ** 32 - 1), ('packet', packet, 0xFFFF),
              ('packets in frame', packets_in_frame, 0xFFFF), ('send time', send_ns, 2 ** 64 - 1))
    for name, value, limit in values:
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError('Invalid link test ' + name + '.')
    if flags & ~FLAG_STEP_END:
        raise ValueError('Unknown link test flags.')
    if not flags and (packets_in_frame < 1 or packet >= packets_in_frame):
        raise ValueError('Link test packet number is outside its frame.')
    return HEADER.pack(MAGIC, VERSION, flags, step, test_id, frame, packet, packets_in_frame, send_ns)


def pack_datagram(step, test_id, frame, packet, packets_in_frame, send_ns, packet_size=DEFAULT_PACKET_SIZE):
    """A data datagram: header plus zero padding to packet_size bytes."""
    if type(packet_size) is not int or not HEADER_SIZE <= packet_size <= 65507:
        raise ValueError('Invalid link test packet size.')
    header = pack_header(step, test_id, frame, packet, packets_in_frame, send_ns)
    return header + bytes(packet_size - HEADER_SIZE)


def unpack_header(data):
    """Validate and decode a datagram header; raises ValueError."""
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) < HEADER_SIZE:
        raise ValueError('Link test datagram is too short.')
    magic, version, flags, step, test_id, frame, packet, count, send_ns = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError('Not a link test datagram.')
    if version != VERSION:
        raise ValueError('Unsupported link test version.')
    if flags & ~FLAG_STEP_END:
        raise ValueError('Unknown link test flags.')
    if flags & FLAG_STEP_END:
        if count < 1:
            raise ValueError('Invalid link test end-of-step datagram.')
    elif count < 1 or packet >= count:
        raise ValueError('Link test packet number is outside its frame.')
    return {'flags': flags, 'step': step, 'test_id': test_id, 'frame': frame, 'packet': packet,
            'packets_in_frame': count, 'send_ns': send_ns, 'end_of_step': bool(flags & FLAG_STEP_END)}


def packets_per_frame(rate_mbps, fps, packet_size=DEFAULT_PACKET_SIZE):
    """Datagrams needed for one frame of rate/fps bytes (at least one)."""
    return max(1, math.ceil(rate_mbps * 1e6 / 8 / fps / packet_size))


def frames_per_step(step_seconds, fps):
    return max(1, round(step_seconds * fps))


def plan_steps(max_mbps=None, link_kind='unknown'):
    """Rate ladder in Mbit/s, truncated at max_mbps.

    max_mbps is normally the client's link speed x 0.95. When the link speed
    or the kind of link is unknown the ladder stops at 600 Mbit/s (0.7 x 600
    is still above the usual 300 Mbit/s video limit). A link slower than the
    first rung gets a single step at its own speed (at least 20).
    """
    if link_kind not in LINK_KINDS:
        link_kind = 'unknown'
    limit = float(max_mbps) if _number(max_mbps) and max_mbps > 0 else float(DEFAULT_MAX_MBPS)
    if link_kind == 'unknown':
        limit = min(limit, float(DEFAULT_MAX_MBPS))
    steps = [rate for rate in LADDER_MBPS if rate <= limit]
    return steps or [max(MIN_MBPS, int(limit // 10 * 10))]


def expected_duration(step_count, step_seconds=1.0, gap_seconds=STEP_GAP_SECONDS):
    """Seconds a sender needs for step_count steps; add start-up margin for the receiver."""
    return step_count * step_seconds + max(0, step_count - 1) * gap_seconds + 0.1


def _target(value):
    if isinstance(value, (tuple, list)) and len(value) == 2 and type(value[1]) is int and 0 < value[1] < 65536:
        try:
            address = ipaddress.IPv4Address(value[0])
        except (ValueError, TypeError):
            address = None
        if address is not None and not (address.is_multicast or address.is_unspecified or address.is_reserved):
            return str(address), value[1]
    raise ValueError('The link test target must be a unicast IPv4 address and port.')


def _validate_plan(test_id, fps, steps, step_seconds, packet_size):
    if type(test_id) is not int or not 0 <= test_id < 2 ** 64:
        raise ValueError('Invalid link test id.')
    if not _number(fps) or not 10 <= fps <= 240:
        raise ValueError('The link test frame rate must be between 10 and 240.')
    if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError('The link test needs 1 to ' + str(MAX_STEPS) + ' steps.')
    if not _number(step_seconds) or not 0.1 <= step_seconds <= 5:
        raise ValueError('Each link test step must last 0.1 to 5 seconds.')
    if type(packet_size) is not int or not 256 <= packet_size <= 65507:
        raise ValueError('Invalid link test packet size.')
    if frames_per_step(step_seconds, fps) > MAX_FRAMES_PER_STEP:
        raise ValueError('Too many frames in a link test step.')
    rates = []
    for rate in steps:
        if not _number(rate) or not 1 <= rate <= MAX_RATE_MBPS:
            raise ValueError('Link test rates must be between 1 and ' + str(MAX_RATE_MBPS) + ' Mbit/s.')
        if packets_per_frame(rate, fps, packet_size) > 0xFFFF:
            raise ValueError('A link test frame would need too many packets.')
        rates.append(rate)
    return rates


class LinkTestSender:
    """Send the test traffic: per step, `fps` frames per second for step_seconds.

    Each frame is rate/fps bytes split into packet_size datagrams. Frames start
    on a fixed schedule. Without pacing a frame's datagrams go out back to
    back, as the video sender writes them; with pace_mbps > 0 they are spread
    so that the frame's own send rate stays at or below pace_mbps.
    run() connects the socket to the target and switches it to blocking mode.
    """

    def __init__(self, sock, target, test_id, fps, steps, step_seconds=1.0, packet_size=DEFAULT_PACKET_SIZE,
                 pace_mbps=0, clock=time.perf_counter_ns, sleep=time.sleep, gap_seconds=STEP_GAP_SECONDS):
        self.steps = _validate_plan(test_id, fps, steps, step_seconds, packet_size)
        if not _number(pace_mbps) or not 0 <= pace_mbps <= MAX_RATE_MBPS:
            raise ValueError('Invalid link test pacing rate.')
        if not _number(gap_seconds) or not 0 <= gap_seconds <= 5:
            raise ValueError('Invalid gap between link test steps.')
        self.sock, self.target = sock, _target(target)
        self.test_id, self.fps, self.step_seconds = test_id, fps, step_seconds
        self.packet_size, self.pace_mbps, self.gap_seconds = packet_size, pace_mbps, gap_seconds
        self.clock, self.sleep = clock, sleep

    def _wait_until(self, due):
        """Sleep for long waits; busy-wait the last millisecond for accuracy."""
        clock = self.clock
        remaining = due - clock()
        if remaining > 2_000_000:
            self.sleep((remaining - 1_000_000) / 1e9)
        while clock() < due:
            pass

    def _pause(self, seconds, cancel):
        """Wait between steps; True when cancelled."""
        until = self.clock() + int(seconds * 1e9)
        while True:
            if cancel is not None and cancel.is_set():
                return True
            remaining = until - self.clock()
            if remaining <= 0:
                return False
            self.sleep(min(0.05, remaining / 1e9))

    def _end_of_step(self, send, step, frames_sent, packets):
        marker = HEADER.pack(MAGIC, VERSION, FLAG_STEP_END, step, self.test_id, frames_sent, 0, packets,
                             self.clock())
        for copy in range(END_MARKER_COPIES):
            if copy:
                self.sleep(0.002)
            try:
                send(marker)
            except OSError:
                pass

    def _run_step(self, step, rate, buffer, send, cancel):
        clock, sleep, pack = self.clock, self.sleep, _TAIL.pack_into
        packet_size = self.packet_size
        packets = packets_per_frame(rate, self.fps, packet_size)
        frames = frames_per_step(self.step_seconds, self.fps)
        interval = 1e9 / self.fps
        gap = packet_size * 8000.0 / self.pace_mbps if self.pace_mbps else 0.0  # ns per datagram
        HEADER.pack_into(buffer, 0, MAGIC, VERSION, 0, step, self.test_id, 0, 0, packets, 0)
        errors = frames_sent = late_frames = 0
        max_late = 0
        cancelled = False
        start = clock()
        for frame in range(frames):
            due = start + int(frame * interval)
            now = clock()
            if now < due:
                self._wait_until(due)
            else:
                late = now - due
                if late > max_late:
                    max_late = late
                if late > interval / 4:
                    late_frames += 1
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if gap:
                begin = clock()
                for packet in range(packets):
                    slot = begin + int(packet * gap)
                    now = clock()
                    if now < slot:
                        if slot - now > 2_000_000:
                            sleep((slot - now - 1_000_000) / 1e9)
                        while clock() < slot:
                            pass
                    pack(buffer, _TAIL_OFFSET, frame, packet, packets, clock())
                    try:
                        send(buffer)
                    except OSError:
                        errors += 1
            else:
                for packet in range(packets):
                    pack(buffer, _TAIL_OFFSET, frame, packet, packets, clock())
                    try:
                        send(buffer)
                    except OSError:
                        errors += 1
            frames_sent += 1
        end = clock()
        packets_sent = frames_sent * packets - errors
        bytes_sent = packets_sent * packet_size
        elapsed = max(end - start, frames_sent * interval, 1)
        achieved = bytes_sent * 8000.0 / elapsed
        return {'step': step, 'rate_mbps': rate, 'frames': frames, 'frames_sent': frames_sent,
                'packets_per_frame': packets, 'packets_sent': packets_sent, 'bytes_sent': bytes_sent,
                'send_errors': errors, 'seconds': round(elapsed / 1e9, 4), 'achieved_mbps': round(achieved, 1),
                'keeping_up': achieved >= SENDER_FRACTION * rate, 'late_frames': late_frames,
                'max_late_ms': round(max_late / 1e6, 3), 'cancelled': cancelled}

    def run(self, cancel=None):
        """Send every step; returns a summary with one dict per step sent."""
        started = self.clock()
        summary = {'test_id': self.test_id, 'fps': self.fps, 'packet_size': self.packet_size,
                   'pace_mbps': self.pace_mbps, 'step_seconds': self.step_seconds, 'steps': [],
                   'cancelled': False, 'error': ''}
        try:
            self.sock.setblocking(True)
            self.sock.connect(self.target)
        except OSError as error:
            summary['error'] = 'The link test could not reach the client: ' + str(error)[:160]
            return summary
        send = self.sock.send
        buffer = bytearray(self.packet_size)
        for step, rate in enumerate(self.steps):
            if step and self._pause(self.gap_seconds, cancel):
                summary['cancelled'] = True
                break
            if cancel is not None and cancel.is_set():
                summary['cancelled'] = True
                break
            result = self._run_step(step, rate, buffer, send, cancel)
            self._end_of_step(send, step, result['frames_sent'], result['packets_per_frame'])
            summary['steps'].append(result)
            if result['cancelled']:
                summary['cancelled'] = True
                break
        summary['seconds'] = round((self.clock() - started) / 1e9, 3)
        return summary


class _Step:
    __slots__ = ('frames', 'packets_in_frame', 'datagram_bytes', 'bytes', 'duplicates', 'end_frames',
                 'end_packets', 'local_drops')

    def __init__(self):
        # frame number -> [packets in frame, received, first arrival ns, last arrival ns,
        #                  earliest send ns, bytearray of packets seen]
        self.frames = {}
        self.packets_in_frame = 0
        self.datagram_bytes = 0
        self.bytes = 0
        self.duplicates = 0
        self.end_frames = None
        self.end_packets = 0
        self.local_drops = 0  # 1.0.0.5: datagrams that reached this PC but its receive buffer dropped


def _percentile(ordered, fraction):
    """Nearest-rank percentile of an already sorted, non-empty list."""
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def _ms(value):
    return None if value is None else round(value / 1e6, 3)


class LinkTestReceiver:
    """Receive one test and build per-step statistics.

    Pass the frame rate and the planned rates when known (the client chose
    them): the nominal frame interval and step rates then come from the plan,
    which is also what lets the receiver notice a sender that fell behind.
    Without them both are inferred from the sender's timestamps.

    1.0.0.5: on Linux each datagram's arrival time is the kernel's (arrival_clock 'kernel'), not
    the moment this thread read it ('companion'), and datagrams the socket's own buffer dropped
    are counted per step (local_drops) apart from those lost on the way.
    """

    def __init__(self, sock, test_id, clock=time.perf_counter_ns, fps=None, steps=None, idle_seconds=1.5):
        if type(test_id) is not int or not 0 <= test_id < 2 ** 64:
            raise ValueError('Invalid link test id.')
        if fps is not None and (not _number(fps) or not 10 <= fps <= 240):
            raise ValueError('The link test frame rate must be between 10 and 240.')
        if steps is not None and (not isinstance(steps, (list, tuple)) or len(steps) > MAX_STEPS
                                  or not all(_number(rate) and rate > 0 for rate in steps)):
            raise ValueError('Invalid link test rate plan.')
        self.sock, self.test_id, self.clock = sock, test_id, clock
        self.fps = fps
        self.steps = list(steps) if steps is not None else None
        self.idle_seconds = idle_seconds
        self.ignored = 0
        self._steps = {}
        self._stop_at = math.inf
        self._drops_seen = 0
        self._last_step = None  # the step of the last datagram counted
        try:
            self.receive_buffer_bytes = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        except (OSError, AttributeError):
            self.receive_buffer_bytes = None
        self.arrival_clock, self.counts_local_drops = 'companion', False
        if sys.platform.startswith('linux') and hasattr(sock, 'recvmsg_into'):
            for option in (_SO_TIMESTAMPNS, _SO_RXQ_OVFL):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, option, 1)
                except (OSError, AttributeError, TypeError, ValueError):
                    continue
                if option == _SO_TIMESTAMPNS:
                    self.arrival_clock = 'kernel'
                else:
                    self.counts_local_drops = True

    def _accept(self, data, now_ns, length=None, arrival_ns=None, drops=None):
        """Record one datagram read at now_ns (receiver clock); True if it belongs to this test.

        arrival_ns: when it reached this PC, if known (1.0.0.5: the kernel's time stamp; only differences
        between arrivals are used). drops: the socket's count of datagrams its buffer has dropped so far
        (credited only with a datagram of this test, see _credit).
        """
        arrived = now_ns if arrival_ns is None else arrival_ns
        size = len(data) if length is None else length
        if size < HEADER_SIZE:
            self.ignored += 1
            return False
        magic, version, flags, step, test_id, frame, packet, count, send_ns = HEADER.unpack_from(data)
        if (magic != MAGIC or version != VERSION or test_id != self.test_id or step >= MAX_STEPS
                or count == 0 or frame > MAX_FRAMES_PER_STEP):
            self.ignored += 1
            return False
        state = self._steps.get(step)
        if flags:
            if flags != FLAG_STEP_END:
                self.ignored += 1
                return False
            if state is None:
                state = self._steps[step] = _Step()
            state.end_frames, state.end_packets = frame, count
            self._credit(step, frame * count, drops)  # the step's every data datagram went before its end
            if self.steps is not None and step == len(self.steps) - 1:
                # The last step has been sent; allow a moment for reordered datagrams.
                self._stop_at = min(self._stop_at, now_ns + 100_000_000)
            return True
        if packet >= count or frame >= MAX_FRAMES_PER_STEP:
            self.ignored += 1
            return False
        if state is None:
            state = self._steps[step] = _Step()
        if not state.packets_in_frame:
            state.packets_in_frame, state.datagram_bytes = count, size
        record = state.frames.get(frame)
        if record is None:
            seen = bytearray(count)
            seen[packet] = 1
            # [packets, received, first arrival, last arrival, first send, seen, last send]: 1.0.0.6 keeps the
            # last send stamp as well, so the sender's own spread can be told from the network's (_summary).
            state.frames[frame] = [count, 1, arrived, arrived, send_ns, seen, send_ns]
            state.bytes += size
            self._credit(step, frame * count + packet, drops)
            return True
        if record[0] != count:
            self.ignored += 1
            return False
        self._credit(step, frame * count + packet, drops)
        seen = record[5]
        if seen[packet]:
            state.duplicates += 1
            return True
        seen[packet] = 1
        record[1] += 1
        if arrived > record[3]:
            record[3] = arrived
        elif arrived < record[2]:
            record[2] = arrived
        if send_ns < record[4]:
            record[4] = send_ns
        elif send_ns > record[6]:
            record[6] = send_ns
        state.bytes += size
        return True

    def _credit(self, step, before, drops):
        """1.0.0.5: credit the datagrams the socket says it dropped since the last count to the steps they were
        sent in. A count comes with a datagram and covers those dropped before it was queued, that is since the
        datagram read before it. When that one was of an earlier step (the receiver stalled at a step's end, and
        its last frames and end markers were dropped), at most `before` of them, this step's datagrams sent
        before this one, are this step's; the rest are the earlier step's."""
        if type(drops) is int and drops > self._drops_seen:
            new, self._drops_seen = drops - self._drops_seen, drops
            earlier = self._steps.get(self._last_step) if self._last_step not in (None, step) else None
            here = new if earlier is None else min(new, max(0, before))
            self._steps[step].local_drops += here
            if earlier is not None:
                earlier.local_drops += new - here
        self._last_step = step

    def run(self, duration_s, cancel=None):
        """Receive for up to duration_s seconds; returns results().

        Stops early once the last planned step's end marker has arrived, or
        when no test datagram has arrived for idle_seconds after the first one.
        """
        duration = duration_s if _number(duration_s) and 0 < duration_s <= 300 else 30
        sock, clock, accept = self.sock, self.clock, self._accept
        sock.settimeout(0.2)
        buffer = bytearray(65536)
        receive = sock.recv_into
        stamped = self.arrival_clock == 'kernel'
        ancillary = stamped or self.counts_local_drops
        if ancillary:
            receive_message, buffers = sock.recvmsg_into, [buffer]
            space = socket.CMSG_SPACE(_TIMESPEC.size) + socket.CMSG_SPACE(_DROP_COUNT.size)
            # Kernel stamps are wall-clock time: a datagram that somehow lacks one gets this clock moved onto it.
            offset = time.time_ns() - clock()
        deadline = clock() + int(duration * 1e9)
        self._stop_at = deadline
        idle = int(self.idle_seconds * 1e9)
        last = None
        count = 0
        while True:
            arrival = drops = None
            try:
                if ancillary:
                    size, messages, _, _ = receive_message(buffers, space)
                    for level, kind, data in messages:
                        if level != socket.SOL_SOCKET:
                            continue
                        if kind == _SO_TIMESTAMPNS and len(data) >= _TIMESPEC.size:
                            seconds, nanoseconds = _TIMESPEC.unpack_from(data)
                            arrival = seconds * 1_000_000_000 + nanoseconds
                        elif kind == _SO_RXQ_OVFL and len(data) >= _DROP_COUNT.size:
                            drops = _DROP_COUNT.unpack_from(data)[0]
                else:
                    size = receive(buffer)
            except (TimeoutError, socket.timeout, InterruptedError, BlockingIOError):
                now = clock()
                if (now >= self._stop_at or (cancel is not None and cancel.is_set())
                        or (last is not None and now - last >= idle)):
                    break
                continue
            except (ConnectionResetError, ConnectionRefusedError):
                # Windows reports an earlier ICMP error on the next receive; keep listening.
                if clock() >= self._stop_at:
                    break
                continue
            except OSError:
                break  # The socket was closed or failed: report what arrived.
            now = clock()
            if stamped and arrival is None:
                arrival = now + offset
            if accept(buffer, now, size, arrival, drops):
                last = now
            if now >= self._stop_at:
                break
            count += 1
            if not count & 0xFF and cancel is not None and cancel.is_set():
                break
        return self.results()

    def _interval_ns(self, ordered):
        if self.fps:
            return 1e9 / self.fps
        gaps = sorted((later[4] - earlier[4]) / (b - a)
                      for (a, earlier), (b, later) in zip(ordered, ordered[1:]) if later[4] > earlier[4])
        return gaps[len(gaps) // 2] if gaps else None

    def _summary(self, step, state):
        ordered = sorted(state.frames.items())
        interval = self._interval_ns(ordered)
        typical = state.packets_in_frame or state.end_packets
        highest = ordered[-1][0] if ordered else -1
        frames_expected = max(highest + 1, state.end_frames or 0)
        missing = frames_expected - len(ordered)
        packets_expected = sum(record[0] for _, record in ordered) + missing * typical
        packets_received = sum(record[1] for _, record in ordered)
        complete = [(number, record) for number, record in ordered if record[1] >= record[0]]
        lost_frames = missing + len(ordered) - len(complete)
        spreads = sorted(record[3] - record[2] for _, record in (complete or ordered))
        # 1.0.0.6: how long the sender took over each frame (its own stamps) and what the way here added to it.
        # In the 1.0.0.5 test the host's sender, a Python loop, needed about 50 us a datagram, so a 150 Mbit/s
        # frame took 9.5 ms to send: its arrival was spread that long without any help from the network, and the
        # step was judged late. Durations on each side's own clock: no common clock is needed.
        judged = complete or ordered
        send_spreads = sorted(max(0, record[6] - record[4]) for _, record in judged)
        added = sorted(max(0, (record[3] - record[2]) - max(0, record[6] - record[4])) for _, record in judged)
        deviations = []
        if interval:
            for (a, earlier), (b, later) in zip(complete, complete[1:]):
                deviations.append(abs(later[3] - earlier[3] - (b - a) * interval))
            deviations.sort()
        if self.steps is not None and step < len(self.steps):
            rate = self.steps[step]
        elif interval and typical and state.datagram_bytes:
            rate = round(typical * state.datagram_bytes * 8000.0 / interval)
        else:
            rate = None
        sender = None
        if self.fps and rate and len(ordered) >= 2:
            (first_number, first), (last_number, last) = ordered[0], ordered[-1]
            span = (last[4] - first[4]) / (last_number - first_number)
            if span > 0:
                sender = round(rate * interval / span, 1)
        window = frames_expected * interval if interval else 0
        lost = max(0, packets_expected - packets_received)
        return {
            'step': step, 'rate_mbps': rate,
            'frame_interval_ms': _ms(interval),
            'frames_expected': frames_expected, 'frames_received': len(ordered), 'frames_complete': len(complete),
            'packets_expected': packets_expected, 'packets_received': packets_received,
            'duplicates': state.duplicates,
            'packet_loss_pct': round(100.0 * (packets_expected - packets_received) / packets_expected, 4)
            if packets_expected else 100.0,
            # 1.0.0.5: lost on the way here; the datagrams this PC's own receive buffer dropped do not count.
            'local_drops': state.local_drops,
            'network_loss_pct': round(100.0 * max(0, lost - state.local_drops) / packets_expected, 4)
            if packets_expected else 100.0,
            'frame_loss_pct': round(100.0 * lost_frames / frames_expected, 3) if frames_expected else 100.0,
            'spread_mean_ms': _ms(sum(spreads) / len(spreads)) if spreads else None,
            'spread_p95_ms': _ms(_percentile(spreads, 0.95)) if spreads else None,
            'send_spread_p95_ms': _ms(_percentile(send_spreads, 0.95)) if send_spreads else None,
            'network_spread_p95_ms': _ms(_percentile(added, 0.95)) if added else None,
            'jitter_p95_ms': _ms(_percentile(deviations, 0.95)) if deviations else None,
            'received_mbps': round(state.bytes * 8000.0 / window, 1) if window else None,
            'sender_mbps': sender,
            'end_marker': state.end_frames is not None,
            'arrival_clock': self.arrival_clock,
        }

    def results(self):
        """One dict per step seen (data or end marker), in step order. Call after run() returns."""
        return [self._summary(step, self._steps[step]) for step in sorted(self._steps)]


def open_receiver(bind_ip='0.0.0.0', port=0, buffer_bytes=8 << 20):
    """UDP socket for LinkTestReceiver with a large receive buffer (best effort).

    A small buffer overflows on frame bursts and would be measured as network
    loss. The kernel may cap the size (Linux: net.core.rmem_max); the receiver
    reports the size it got as receive_buffer_bytes.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(buffer_bytes))
        except OSError:
            pass
        sock.bind((bind_ip, port))
    except BaseException:
        sock.close()
        raise
    return sock


def open_sender(buffer_bytes=4 << 20, tos=None):
    """UDP socket for LinkTestSender with a large send buffer (best effort).

    tos is an optional IP TOS byte (DSCP << 2) to imitate the stream's own
    marking; Windows ignores it unless a QoS policy covers the process.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, int(buffer_bytes))
    except OSError:
        pass
    if tos is not None and hasattr(socket, 'IP_TOS'):
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, int(tos) & 0xFC)
        except OSError:
            pass
    return sock


def merge_sender(step_results, sender_summary):
    """Add the host sender's own figures to the receiver's step results.

    Copies achieved_mbps and send_errors by step, and adds a fully lost entry
    for any step the host sent but the receiver never saw.
    """
    sent = {}
    for row in (sender_summary or {}).get('steps', []) or []:
        if isinstance(row, dict) and type(row.get('step')) is int:
            sent[row['step']] = row
    merged = []
    for row in step_results or []:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        source = sent.pop(row.get('step'), None)
        if source:
            row['achieved_mbps'] = source.get('achieved_mbps')
            row['send_errors'] = source.get('send_errors')
            _add_unseen_tail(row, source)
        merged.append(row)
    for step, source in sent.items():
        if not source.get('packets_sent'):
            continue
        merged.append({'step': step, 'rate_mbps': source.get('rate_mbps'), 'frame_interval_ms': None,
                       'frames_expected': source.get('frames_sent', 0), 'frames_received': 0,
                       'frames_complete': 0, 'packets_expected': source.get('packets_sent', 0),
                       'packets_received': 0, 'duplicates': 0, 'packet_loss_pct': 100.0, 'frame_loss_pct': 100.0,
                       'local_drops': 0, 'network_loss_pct': 100.0,
                       'spread_mean_ms': None, 'spread_p95_ms': None, 'jitter_p95_ms': None,
                       'received_mbps': 0.0, 'sender_mbps': None, 'end_marker': False,
                       'achieved_mbps': source.get('achieved_mbps'), 'send_errors': source.get('send_errors')})
    return sorted(merged, key=lambda row: row['step'] if type(row.get('step')) is int else MAX_STEPS)


def _add_unseen_tail(row, source):
    """1.0.0.5: without an end marker the receiver cannot know how many frames a step had, so frames lost after
    the last one it saw went uncounted (and the step looked clean). The host's summary says how many it sent:
    those frames count as lost, on the network or, when this PC's buffer dropped them with the end markers,
    as local drops."""
    sent, per_frame, expected = source.get('frames_sent'), source.get('packets_per_frame'), row.get('frames_expected')
    if (row.get('end_marker') or type(sent) is not int or type(per_frame) is not int or type(expected) is not int
            or per_frame < 1 or sent <= expected):
        return
    missing = sent - expected
    packets = (row.get('packets_expected') if type(row.get('packets_expected')) is int else 0) + missing * per_frame
    received = row.get('packets_received') if type(row.get('packets_received')) is int else 0
    local = row.get('local_drops') if type(row.get('local_drops')) is int else 0
    complete = row.get('frames_complete') if type(row.get('frames_complete')) is int else 0
    lost = max(0, packets - received)
    row.update(frames_expected=sent, packets_expected=packets, unseen_tail_frames=missing,
               packet_loss_pct=round(100.0 * lost / packets, 4),
               network_loss_pct=round(100.0 * max(0, lost - local) / packets, 4),
               frame_loss_pct=round(100.0 * max(0, sent - complete) / sent, 3))


def network_loss(row):
    """Packet loss on the way to this PC, in %: from 1.0.0.5 without the datagrams this PC's own receive buffer
    dropped (they arrived; the companion was busy), which a result from before 1.0.0.5 cannot tell apart."""
    value = row.get('network_loss_pct')
    return value if _number(value) else row.get('packet_loss_pct')


def step_verdict(row):
    """(clean, reason) for one step result; reason is 'loss', 'late', 'sender', 'receiver' or ''.

    1.0.0.5: frames that are missing only because this PC dropped datagrams that had arrived do not fail a
    step whose network loss is nil, as long as at least half its frames arrived complete to judge their timing;
    with fewer the step says nothing about the network ('receiver': this PC could not read the test fast
    enough)."""
    loss, frames = network_loss(row), row.get('frame_loss_pct')
    if not (row.get('packets_expected') or 0) or not _number(loss) or not _number(frames):
        return False, 'loss'
    if loss > CLEAN_PACKET_LOSS_PCT:
        return False, 'loss'
    if frames > CLEAN_FRAME_LOSS_PCT:
        if not (_number(row.get('local_drops')) and row['local_drops'] > 0) or loss > 0:
            return False, 'loss'
        complete, expected = row.get('frames_complete'), row.get('frames_expected')
        if not (_number(complete) and _number(expected) and expected > 0 and complete >= expected / 2):
            return False, 'receiver'
    # 1.0.0.6: lateness is what the way here added to each frame's spread (network_spread_p95_ms), not the
    # sender's own time to write it; rows without it (saved by 1.0.0.5 and earlier) use the whole spread.
    spread, interval = row.get('network_spread_p95_ms'), row.get('frame_interval_ms')
    if not _number(spread):
        spread = row.get('spread_p95_ms')
    if _number(spread) and _number(interval) and interval > 0 and spread >= CLEAN_SPREAD_FRACTION * interval:
        return False, 'late'
    sender = row.get('achieved_mbps')
    if not _number(sender):
        sender = row.get('sender_mbps')
    if _number(sender) and _number(row.get('rate_mbps')) and sender < SENDER_FRACTION * row['rate_mbps']:
        return False, 'sender'
    return True, ''


def lossless_mbps(step_results):
    """The highest step rate below the first step that lost data steadily: the most this path was seen to
    carry, which wired video pacing stays under (launcher/network_test.py pacing_mbps; kept under this name,
    which saved results use, since 1.0.0.4).

    1.0.0.5: steadily means CAPACITY_LOSS_PCT of a step's packets or more lost on the way. 1.0.0.4 took the
    first step that lost anything; but a packet or two can go missing at any rate (one of a run of clean
    steps in the 1.0.0.3 and 1.0.0.4 tests, even the first), and it then set no limit at all or one far
    below what the path carried. Such a step still is not clean (step_verdict), so it never raises the
    bitrate. A warm-up step is not judged.

    None when the first step already lost steadily, and when no step did: the test then found no limit
    below its highest step, so the link's own speed decides. Lateness does not count here.
    """
    rows = sorted((row for row in step_results or [] if isinstance(row, dict) and not row.get('warmup')
                   and _number(row.get('rate_mbps')) and row['rate_mbps'] > 0),
                  key=lambda row: row['rate_mbps'])
    best = None
    for row in rows:
        loss = network_loss(row)
        if not (row.get('packets_expected') or 0) or not _number(loss) or loss >= CAPACITY_LOSS_PCT:
            return int(best) if best else None
        best = row['rate_mbps']
    return None


def paced_below(lossless):
    """1.0.0.8: the pacing rate for a path that carried `lossless` Mbit/s without loss (PACING_HEADROOM of it)."""
    return int(math.floor(PACING_HEADROOM * lossless))


def recommend(step_results, codec_cap_mbps=300, link_kind='unknown', link_pace_mbps=0):
    """_recommend over the judged steps, with the rate the pacing stays under (1.0.0.4).

    1.0.0.5: warm-up steps are left out. On a wired link, link_pace_mbps is the rate a frame is paced at on
    this link (just below its speed; 0: unpaced). The stream is paced at the lower of that and the rate the
    path carried, and the bitrate is kept to SEND_FRACTION of that pace, so an average frame takes at most
    that share of a frame interval to arrive: a path that carries only a little more than the bitrate would
    deliver each frame late (limited_by 'latency').
    """
    rows = [row for row in step_results or [] if not (isinstance(row, dict) and row.get('warmup'))]
    warmed = any(isinstance(row, dict) and row.get('warmup') and row.get('packets_received')
                 for row in step_results or [])
    if warmed and not any(isinstance(row, dict) and (row.get('packets_received') or row.get('end_marker'))
                          for row in rows):
        return {'recommended_mbps': None, 'clean_mbps': None, 'failed_mbps': None, 'limited_by': 'none',
                'lossless_mbps': None,
                'message': 'The network test ended before it measured anything, so the bitrate was not changed.'}
    result = _recommend(rows, codec_cap_mbps, link_kind)
    result['lossless_mbps'] = lossless_mbps(rows) if result.get('recommended_mbps') is not None else None
    clean = result.get('clean_mbps')
    if link_kind == 'wired' and _number(clean) and clean > 0 and _number(link_pace_mbps) and link_pace_mbps > 0:
        lossless = result['lossless_mbps']
        pace = min(link_pace_mbps, paced_below(lossless) if lossless else link_pace_mbps)
        limit = max(MIN_MBPS, int(math.floor(SEND_FRACTION * pace / 10 + 1e-9)) * 10)
        if limit < result['recommended_mbps']:
            result['recommended_mbps'], result['limited_by'] = limit, 'latency'
            result['message'] = ('Network test: clean up to ' + _rate_text(clean) + ' Mbit/s on Ethernet; the video '
                                 'is paced at ' + _rate_text(pace) + ' Mbit/s on this path, so using ' + str(limit)
                                 + ' Mbit/s keeps each frame quick to arrive.')
    return result


def _recommend(step_results, codec_cap_mbps=300, link_kind='unknown'):
    """Pick a bitrate from the step results.

    The highest step with packet loss <= 0.05 %, frame loss <= 0.2 %, p95 frame
    spread below 0.6 of a frame interval (and a sender that kept up) is the
    clean rate (step_verdict; from 1.0.0.5 the loss is the network's, without
    datagrams this PC's own buffer dropped). The bitrate is 0.7 x clean (0.6 x
    on Wi-Fi, whose capacity varies more), rounded down to 10 and kept within
    [20, codec cap]. If every step failed the result is 30 Mbit/s. If nothing
    at all arrived the test did not run: recommended_mbps is None and the
    bitrate should stay as it is.
    """
    if link_kind not in LINK_KINDS:
        link_kind = 'unknown'
    where = {'wired': 'on Ethernet', 'wifi': 'on Wi-Fi'}.get(link_kind, 'on this connection')
    cap = int(codec_cap_mbps) if _number(codec_cap_mbps) and codec_cap_mbps >= MIN_MBPS else 300
    rows = sorted((row for row in step_results or [] if isinstance(row, dict)
                   and _number(row.get('rate_mbps')) and row['rate_mbps'] > 0),
                  key=lambda row: row['rate_mbps'])
    if not any(row.get('packets_received') or row.get('end_marker') for row in rows):
        return {'recommended_mbps': None, 'clean_mbps': None, 'failed_mbps': None, 'limited_by': 'none',
                'message': 'The network test received nothing from the host PC, so the bitrate was not changed. '
                           'Check that the firewall on this PC allows VROSTBridge.'}
    verdicts = [(row, *step_verdict(row)) for row in rows]
    clean = [row for row, ok, _ in verdicts if ok]
    if not clean:
        row, _, reason = verdicts[0]
        lowest = _rate_text(row['rate_mbps'])
        recommended = max(MIN_MBPS, min(FALLBACK_MBPS, cap))
        advice = {'wired': ' Check the network cable and any switch or powerline adapter between the PCs.',
                  'wifi': ' Move closer to the Wi-Fi access point or use its 5 GHz or 6 GHz band.'}.get(
                      link_kind, ' A wired connection is the most reliable.')
        if reason == 'sender':
            message = ('Network test: the host PC could not send test data even at ' + lowest + ' Mbit/s; using '
                       + str(recommended) + ' Mbit/s. Close other programs on the host PC and test again.')
        elif reason == 'receiver':
            message = ('Network test: this PC was too busy to read the test data even at ' + lowest + ' Mbit/s, so '
                       'the network could not be judged; using ' + str(recommended) + ' Mbit/s for now.')
        elif reason == 'late':
            message = ('Network test: the connection is delaying data even at ' + lowest + ' Mbit/s ' + where
                       + '; using ' + str(recommended) + ' Mbit/s.' + advice)
        else:
            message = ('Network test: the connection is losing data even at ' + lowest + ' Mbit/s ' + where
                       + '; using ' + str(recommended) + ' Mbit/s.' + advice)
        return {'recommended_mbps': recommended, 'clean_mbps': 0, 'failed_mbps': row['rate_mbps'],
                'limited_by': reason if reason in ('sender', 'receiver') else 'network', 'message': message}
    best = clean[-1]
    failure = next(((row, reason) for row, ok, reason in verdicts
                    if not ok and row['rate_mbps'] > best['rate_mbps']), None)
    limited_by = 'none' if failure is None else (failure[1] if failure[1] in ('sender', 'receiver') else 'network')
    factor = 0.6 if link_kind == 'wifi' else 0.7
    raw = int(math.floor(factor * best['rate_mbps'] / 10 + 1e-9)) * 10
    recommended = max(MIN_MBPS, min(cap, raw))
    if raw > cap:
        limited_by = 'codec'
    clean_text, using = _rate_text(best['rate_mbps']), str(recommended)
    if limited_by == 'codec':
        message = ('Network test: clean up to ' + clean_text + ' Mbit/s ' + where + '; using ' + using
                   + ' Mbit/s, the most the video settings allow.')
    elif limited_by == 'sender':
        message = ('Network test: clean up to ' + clean_text + ' Mbit/s ' + where
                   + '; the host PC could not send test data faster. Using ' + using + ' Mbit/s.')
    elif limited_by == 'receiver':
        message = ('Network test: clean up to ' + clean_text + ' Mbit/s ' + where + '; at '
                   + _rate_text(failure[0]['rate_mbps']) + ' Mbit/s this PC was too busy to read the test data. Using '
                   + using + ' Mbit/s.')
    elif limited_by == 'network':
        what = 'data arrived late' if failure[1] == 'late' else 'data was lost'
        message = ('Network test: clean up to ' + clean_text + ' Mbit/s ' + where + ' (' + what + ' at '
                   + _rate_text(failure[0]['rate_mbps']) + ' Mbit/s); using ' + using + ' Mbit/s.')
    else:
        message = 'Network test: clean up to ' + clean_text + ' Mbit/s ' + where + '; using ' + using + ' Mbit/s.'
    return {'recommended_mbps': recommended, 'clean_mbps': best['rate_mbps'],
            'failed_mbps': failure[0]['rate_mbps'] if failure else None, 'limited_by': limited_by,
            'message': message}


def _rate_text(value):
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))
