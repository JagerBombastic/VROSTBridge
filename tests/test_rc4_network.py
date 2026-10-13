"""RC4 network checks: Wake-on-LAN for the host PC and the link test that suggests a bitrate.

No real network is used except the 127.0.0.1 loopback link test; external commands are fakes.
"""
import ipaddress
import math
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import link_test, wake_host
from launcher.link_test import (FLAG_STEP_END, HEADER, HEADER_SIZE, LinkTestReceiver, LinkTestSender,
                                expected_duration, merge_sender, pack_datagram, pack_header, plan_steps,
                                recommend, unpack_header)
from launcher.wake_host import (HostWaker, LIMITED_BROADCAST, MESSAGE_CANCELLED, MESSAGE_NO_MAC,
                                MESSAGE_NO_RESPONSE, MESSAGE_SEND_FAILED, learn_mac, local_interfaces,
                                magic_packet, normalise_mac, send_wake, wake_targets)

MAC = 'aa:bb:cc:dd:ee:0f'


def completed(stdout='', returncode=0, arguments=()):
    return subprocess.CompletedProcess(list(arguments), returncode, stdout, '')


class MacAddressTests(unittest.TestCase):
    def test_common_formats_normalise_to_lower_case_colons(self):
        for text in ('AA:BB:CC:DD:EE:0F', 'aa-bb-cc-dd-ee-0f', 'aabbccddee0f', ' AaBbCcDdEe0F\n'):
            self.assertEqual(normalise_mac(text), MAC, text)

    def test_unusable_addresses_are_rejected(self):
        for text in ('ff:ff:ff:ff:ff:ff', '01:00:5e:00:00:fb', '33:33:00:00:00:01', '00:00:00:00:00:00',
                     'aa:bb:cc:dd:ee', 'aa:bb-cc:dd:ee:ff', 'gg:bb:cc:dd:ee:ff', 'aa:bb:cc:dd:ee:ff:00',
                     'aabb.ccdd.eeff', '', None, 0xAABBCCDDEEFF, b'aa:bb:cc:dd:ee:ff'):
            self.assertIsNone(normalise_mac(text), text)

    def test_magic_packet_is_six_ff_then_sixteen_copies(self):
        packet = magic_packet('AA-BB-CC-DD-EE-0F')
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b'\xff' * 6)
        self.assertEqual(packet[6:], bytes.fromhex('aabbccddee0f') * 16)
        with self.assertRaises(ValueError):
            magic_packet('ff:ff:ff:ff:ff:ff')


class LearnMacTests(unittest.TestCase):
    def runner(self, stdout, returncode=0):
        calls = []

        def run(arguments, timeout):
            calls.append((arguments, timeout))
            return completed(stdout, returncode, arguments)
        return run, calls

    def test_reads_confirmed_neighbour_entry(self):
        run, calls = self.runner('[{"dst":"192.168.1.10","dev":"enp3s0","lladdr":"AA:BB:CC:DD:EE:0F",'
                                 '"state":["REACHABLE"]}]')
        self.assertEqual(learn_mac('192.168.1.10', runner=run, ip_tool='/usr/sbin/ip'), MAC)
        self.assertEqual(calls, [(['/usr/sbin/ip', '-j', 'neigh', 'show', '192.168.1.10'], 2)])

    def test_stale_and_permanent_entries_are_usable_but_failed_are_not(self):
        for state, expected in ((['STALE'], MAC), ('DELAY', MAC), (['PERMANENT'], MAC), (['PROBE'], MAC),
                                (['FAILED'], None), (['INCOMPLETE'], None), (['NOARP'], None), ([], None)):
            entry = '{"dst":"192.168.1.10","lladdr":"aa:bb:cc:dd:ee:0f","state":%s}' % (
                '"' + state + '"' if isinstance(state, str) else str(state).replace("'", '"'))
            run, _ = self.runner('[' + entry + ']')
            self.assertEqual(learn_mac('192.168.1.10', runner=run, ip_tool='ip'), expected, state)

    def test_prefers_reachable_entry_and_ignores_other_destinations(self):
        run, _ = self.runner('[{"dst":"192.168.1.11","lladdr":"02:00:00:00:00:01","state":["REACHABLE"]},'
                             '{"dst":"192.168.1.10","lladdr":"02:00:00:00:00:02","state":["STALE"]},'
                             '{"dst":"192.168.1.10","lladdr":"02:00:00:00:00:03","state":["REACHABLE"]},'
                             '{"dst":"192.168.1.10","lladdr":"01:00:5e:00:00:01","state":["REACHABLE"]}]')
        self.assertEqual(learn_mac('192.168.1.10', runner=run, ip_tool='ip'), '02:00:00:00:00:03')

    def test_invalid_host_addresses_never_reach_the_command(self):
        run, calls = self.runner('[]')
        for value in ('not-an-ip', '127.0.0.1', '224.0.0.251', '0.0.0.0', '255.255.255.255', '192.168.1.10; id',
                      None, 3232235786):
            self.assertIsNone(learn_mac(value, runner=run, ip_tool='ip'), value)
        self.assertEqual(calls, [])

    def test_command_failures_return_none(self):
        def raises(error):
            def run(arguments, timeout):
                raise error
            return run
        for run in (self.runner('', 1)[0], self.runner('not json')[0], self.runner('{"dst":"x"}')[0],
                    raises(OSError('missing')), raises(subprocess.TimeoutExpired(['ip'], 2))):
            self.assertIsNone(learn_mac('192.168.1.10', runner=run, ip_tool='ip'))

    def test_missing_ip_tool_returns_none(self):
        run, calls = self.runner('[]')
        with mock.patch.object(wake_host, '_tool', return_value=None):
            self.assertIsNone(learn_mac('192.168.1.10', runner=run))
        self.assertEqual(calls, [])


class WakeTargetTests(unittest.TestCase):
    def test_directed_broadcast_limited_broadcast_and_unicast(self):
        local = [ipaddress.IPv4Interface('10.8.0.2/24'), ipaddress.IPv4Interface('192.168.1.20/24')]
        self.assertEqual(wake_targets('192.168.1.10', local), ['192.168.1.255', LIMITED_BROADCAST, '192.168.1.10'])

    def test_host_outside_local_networks_gets_no_directed_broadcast(self):
        local = [ipaddress.IPv4Interface('192.168.1.20/24')]
        self.assertEqual(wake_targets('192.168.5.10', local), [LIMITED_BROADCAST, '192.168.5.10'])

    def test_strings_duplicates_and_point_to_point_networks(self):
        local = ['192.168.1.20/24', '192.168.1.21/24', '192.168.1.10/32', '192.168.1.9/31', 'junk', None]
        self.assertEqual(wake_targets('192.168.1.10', local), ['192.168.1.255', LIMITED_BROADCAST, '192.168.1.10'])

    def test_unknown_host_uses_private_network_broadcasts(self):
        local = ['192.168.1.20/24', '10.1.2.3/16', '100.64.0.5/10']
        self.assertEqual(wake_targets(None, local), ['192.168.1.255', '10.1.255.255', LIMITED_BROADCAST])
        self.assertEqual(wake_targets('224.0.0.1', local), ['192.168.1.255', '10.1.255.255', LIMITED_BROADCAST])

    def test_local_interfaces_from_lan_address_records(self):
        records = [{'address': '192.168.1.20', 'prefix': 24, 'interface': 'enp3s0', 'connected': True},
                   {'address': '192.168.1.20', 'prefix': 24, 'interface': 'enp3s0', 'connected': True},
                   {'address': '10.0.0.2', 'prefix': None, 'interface': 'wg0'},
                   {'address': '172.16.0.2', 'prefix': 16, 'interface': 'wlan0', 'connected': False},
                   {'address': 'bad', 'prefix': 24}, 'junk']
        self.assertEqual(local_interfaces(records), [ipaddress.IPv4Interface('192.168.1.20/24')])


class FakeSocket:
    def __init__(self, failing=()):
        self.failing, self.options, self.sent, self.closed = set(failing), [], [], False

    def setsockopt(self, *arguments):
        self.options.append(arguments)

    def sendto(self, data, destination):
        if destination[0] in self.failing:
            raise OSError('Network is unreachable')
        self.sent.append((bytes(data), destination))

    def close(self):
        self.closed = True


class SendWakeTests(unittest.TestCase):
    def setUp(self):
        self.sleeps = []

    def send(self, targets, sock, **options):
        return send_wake(MAC, targets, sock_factory=lambda family, kind: sock, sleep=self.sleeps.append, **options)

    def test_every_target_and_port_repeated(self):
        sock = FakeSocket()
        targets = ['192.168.1.255', LIMITED_BROADCAST, '192.168.1.10']
        self.assertEqual(self.send(targets, sock), 18)
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), sock.options)
        self.assertEqual({data for data, _ in sock.sent}, {magic_packet(MAC)})
        self.assertEqual(sock.sent[:6], [(magic_packet(MAC), (address, port))
                                         for address in targets for port in (9, 7)])
        self.assertEqual(self.sleeps, [0.2, 0.2])
        self.assertTrue(sock.closed)

    def test_one_failing_target_does_not_stop_the_others(self):
        sock = FakeSocket(failing={LIMITED_BROADCAST})
        self.assertEqual(self.send(['192.168.1.255', LIMITED_BROADCAST, '192.168.1.10'], sock, repeats=2), 8)
        self.assertNotIn(LIMITED_BROADCAST, {destination[0] for _, destination in sock.sent})

    def test_invalid_targets_are_skipped(self):
        sock = FakeSocket()
        self.assertEqual(self.send(['bogus', '127.0.0.1', '224.0.0.251', '0.0.0.0', '192.168.1.10'], sock,
                                   repeats=1), 2)
        self.assertEqual(self.send([], FakeSocket()), 0)

    def test_socket_failure_returns_zero_and_bad_mac_raises(self):
        def broken(family, kind):
            raise OSError('no sockets')
        self.assertEqual(send_wake(MAC, ['192.168.1.10'], sock_factory=broken), 0)
        with self.assertRaises(ValueError):
            send_wake('ff:ff:ff:ff:ff:ff', ['192.168.1.10'], sock_factory=lambda *a: FakeSocket())


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


class HostWakerTests(unittest.TestCase):
    def setUp(self):
        self.time = FakeTime()
        self.sends = []
        self.logs = []

    def sender(self, mac, targets):
        self.sends.append((self.time.now, mac, list(targets)))
        return 6

    def waker(self, discover, sender=None):
        return HostWaker(sender=sender or self.sender, discover=discover, clock=self.time.clock,
                         sleep=self.time.sleep, log=self.logs.append)

    def test_woken_when_the_companion_answers(self):
        answers = iter([False, False, False, True])
        progress = []
        waker = self.waker(lambda: next(answers))
        result = waker.wake_and_wait('AA-BB-CC-DD-EE-0F', ['192.168.1.255'], progress=progress.append)
        self.assertEqual(result['woken'], True)
        self.assertEqual(result['seconds'], 6.0)
        self.assertEqual(progress, [0.0, 2.0, 4.0, 6.0])
        self.assertEqual(self.sends, [(0.0, MAC, ['192.168.1.255'])])
        self.assertEqual(waker.status()['state'], 'awake')
        self.assertEqual(self.logs[0], 'WAKE waking: Waking the host PC…')

    def test_resends_every_ten_seconds_until_timeout(self):
        waker = self.waker(lambda: False)
        result = waker.wake_and_wait(MAC, ['192.168.1.255'], timeout=35)
        self.assertEqual(result['woken'], False)
        self.assertEqual(result['message'], MESSAGE_NO_RESPONSE)
        self.assertEqual(result['seconds'], 35.0)
        self.assertEqual([moment for moment, _, _ in self.sends], [0.0, 10.0, 20.0, 30.0])
        self.assertEqual(result['packets'], 24)
        self.assertIn('BIOS', waker.status()['message'])

    def test_cancel_stops_waiting(self):
        cancel = threading.Event()

        def discover():
            if self.time.now >= 4:
                cancel.set()
            return False
        result = self.waker(discover).wake_and_wait(MAC, ['192.168.1.255'], cancel=cancel)
        self.assertEqual((result['woken'], result['state'], result['message']), (False, 'cancelled', MESSAGE_CANCELLED))
        self.assertEqual(result['seconds'], 4.0)

    def test_unknown_mac_and_send_failure_are_reported(self):
        result = self.waker(lambda: True).wake_and_wait('', ['192.168.1.255'])
        self.assertEqual((result['woken'], result['message']), (False, MESSAGE_NO_MAC))
        self.assertEqual(self.sends, [])
        result = self.waker(lambda: True, sender=lambda mac, targets: 0).wake_and_wait(MAC, ['192.168.1.255'])
        self.assertEqual((result['woken'], result['message']), (False, MESSAGE_SEND_FAILED))

    def test_failing_probe_and_progress_callback_do_not_end_the_wait(self):
        calls = []

        def discover():
            calls.append(self.time.now)
            if len(calls) < 3:
                raise OSError('host unreachable')
            return {'hostname': 'host'}

        def progress(seconds):
            raise RuntimeError('UI closed')
        result = self.waker(discover).wake_and_wait(MAC, ['192.168.1.255'], progress=progress)
        self.assertTrue(result['woken'])
        self.assertEqual(calls, [0.0, 2.0, 4.0])
        self.assertTrue(any('host unreachable' in line for line in self.logs))

    def test_real_sender_with_fake_socket(self):
        sock = FakeSocket()
        sender = lambda mac, targets: send_wake(mac, targets, sock_factory=lambda *a: sock, sleep=self.time.sleep)
        result = self.waker(lambda: True, sender=sender).wake_and_wait(MAC, ['192.168.1.255', '192.168.1.10'])
        self.assertTrue(result['woken'])
        self.assertEqual(result['packets'], 12)


class PacketFormatTests(unittest.TestCase):
    def test_header_layout_round_trips(self):
        self.assertEqual(HEADER_SIZE, 32)
        header = pack_header(3, 2 ** 64 - 1, 77, 5, 6, 123456789)
        self.assertEqual(header[:4], b'VRLT')
        self.assertEqual(unpack_header(header), {'flags': 0, 'step': 3, 'test_id': 2 ** 64 - 1, 'frame': 77,
                                                 'packet': 5, 'packets_in_frame': 6, 'send_ns': 123456789,
                                                 'end_of_step': False})
        datagram = pack_datagram(0, 1, 0, 0, 1, 0)
        self.assertEqual(len(datagram), 1400)
        self.assertEqual(datagram[HEADER_SIZE:], bytes(1400 - HEADER_SIZE))
        marker = pack_header(1, 9, 90, 0, 12, 5, flags=FLAG_STEP_END)
        self.assertTrue(unpack_header(marker)['end_of_step'])

    def test_unpack_rejects_invalid_datagrams(self):
        good = bytearray(pack_header(0, 1, 0, 0, 2, 0))
        bad = [good[:31], b'XBLT' + good[4:], good[:4] + b'\x02' + good[5:], good[:5] + b'\x02' + good[6:],
               HEADER.pack(b'VRLT', 1, 0, 0, 1, 0, 2, 2, 0), HEADER.pack(b'VRLT', 1, 0, 0, 1, 0, 0, 0, 0),
               HEADER.pack(b'VRLT', 1, FLAG_STEP_END, 0, 1, 9, 0, 0, 0), 'text']
        for data in bad:
            with self.assertRaises(ValueError):
                unpack_header(data)

    def test_pack_validates_ranges(self):
        for arguments in ((70000, 1, 0, 0, 1, 0), (0, -1, 0, 0, 1, 0), (0, 2 ** 64, 0, 0, 1, 0),
                          (0, 1, 2 ** 32, 0, 1, 0), (0, 1, 0, 1, 1, 0), (0, 1, 0, 0, 0, 0), (True, 1, 0, 0, 1, 0),
                          (0, 1, 0, 0, 1, 1.5)):
            with self.assertRaises(ValueError):
                pack_header(*arguments)
        with self.assertRaises(ValueError):
            pack_header(0, 1, 0, 0, 1, 0, flags=0x80)
        with self.assertRaises(ValueError):
            pack_datagram(0, 1, 0, 0, 1, 0, packet_size=16)


class PlanTests(unittest.TestCase):
    def test_ladder_is_truncated_at_the_link_limit(self):
        full = [80, 150, 200, 250, 300, 400, 500, 600, 750, 900]
        self.assertEqual(plan_steps(950, 'wired'), full)
        self.assertEqual(plan_steps(2375, 'wired'), full)
        self.assertEqual(plan_steps(500, 'wifi'), [80, 150, 200, 250, 300, 400, 500])
        self.assertEqual(plan_steps(None, 'unknown'), full[:8])
        self.assertEqual(plan_steps('fast', 'other'), full[:8])
        self.assertEqual(plan_steps(True, 'wired'), full[:8])
        self.assertEqual(plan_steps(2375, 'unknown'), full[:8])
        self.assertEqual(plan_steps(2375, 'wifi'), full)
        # The full ladder still fits the host's 30-second limit.
        self.assertLess(expected_duration(len(full)), 30)

    def test_slow_links_get_one_step(self):
        self.assertEqual(plan_steps(95 * 0.7, 'wifi'), [60])
        self.assertEqual(plan_steps(12, 'wired'), [20])

    def test_expected_duration(self):
        self.assertAlmostEqual(expected_duration(2, 0.3), 1.0)
        self.assertAlmostEqual(expected_duration(6), 7.6)


class TickClock:
    """Fake nanosecond clock: every reading advances 1 µs; sleep() advances by its argument."""

    def __init__(self):
        self.now = 1_000_000_000

    def __call__(self):
        self.now += 1000
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += int(seconds * 1e9)


class RecordingSocket:
    def __init__(self, clock, fail_every=0):
        self.clock, self.fail_every = clock, fail_every
        self.datagrams, self.attempts, self.connected, self.blocking = [], 0, None, None

    def setblocking(self, value):
        self.blocking = value

    def connect(self, target):
        self.connected = target

    def send(self, data):
        self.attempts += 1
        if self.fail_every and self.attempts % self.fail_every == 0:
            raise OSError('No buffer space available')
        self.datagrams.append((self.clock.now, bytes(data)))
        return len(data)


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.clock = TickClock()
        self.test_id = 0x1122334455667788

    def sender(self, sock, steps=(8,), fps=10, **options):
        return LinkTestSender(sock, ('192.168.1.20', 9950), self.test_id, fps, list(steps), clock=self.clock,
                              sleep=self.clock.sleep, **options)

    def test_frames_packets_markers_and_schedule(self):
        sock = RecordingSocket(self.clock)
        summary = self.sender(sock).run()
        self.assertEqual((sock.connected, sock.blocking), (('192.168.1.20', 9950), True))
        data = [(moment, unpack_header(raw), len(raw)) for moment, raw in sock.datagrams]
        frames = [header for _, header, size in data if not header['end_of_step']]
        markers = [header for _, header, size in data if header['end_of_step']]
        # 8 Mbit/s at 10 fps is 100,000 bytes a frame: 72 datagrams of 1400 bytes.
        self.assertEqual(len(frames), 720)
        self.assertTrue(all(size == 1400 for _, header, size in data if not header['end_of_step']))
        self.assertEqual([(h['frame'], h['packet'], h['packets_in_frame']) for h in frames[:2]], [(0, 0, 72), (0, 1, 72)])
        self.assertEqual(frames[-1]['frame'], 9)
        self.assertEqual({h['test_id'] for h in frames + markers}, {self.test_id})
        self.assertEqual([(m['step'], m['frame'], m['packets_in_frame']) for m in markers], [(0, 10, 72)] * 3)
        starts = [moment for moment, header, _ in data if not header['end_of_step'] and header['packet'] == 0]
        for frame, moment in enumerate(starts):
            self.assertGreaterEqual(moment - starts[0], frame * 100_000_000 - 1000)
            self.assertLess(moment - starts[0], frame * 100_000_000 + 50_000)
        step = summary['steps'][0]
        self.assertEqual((step['frames_sent'], step['packets_sent'], step['bytes_sent']), (10, 720, 1_008_000))
        self.assertTrue(step['keeping_up'])
        self.assertAlmostEqual(step['achieved_mbps'], 8.06, places=1)
        self.assertFalse(summary['cancelled'])

    def test_pacing_spreads_a_frame(self):
        sock = RecordingSocket(self.clock)
        self.sender(sock, steps=(8,), pace_mbps=100).run()
        first = [moment for moment, raw in sock.datagrams if unpack_header(raw)['frame'] == 0
                 and not unpack_header(raw)['end_of_step']]
        gaps = [later - earlier for earlier, later in zip(first, first[1:])]
        self.assertGreaterEqual(min(gaps), 112_000 - 2000)  # 1400 bytes at 100 Mbit/s = 112 µs
        self.assertLess(max(gaps), 112_000 + 10_000)

    def test_steps_are_separated_and_send_errors_are_counted(self):
        sock = RecordingSocket(self.clock, fail_every=10)
        summary = self.sender(sock, steps=(8, 16), step_seconds=0.5).run()
        self.assertEqual([step['step'] for step in summary['steps']], [0, 1])
        self.assertGreater(sum(step['send_errors'] for step in summary['steps']), 0)
        first_of_second = next(moment for moment, raw in sock.datagrams if raw[6] == 1)
        last_of_first = max(moment for moment, raw in sock.datagrams if raw[6] == 0 and raw[5] == 0)
        self.assertGreaterEqual(first_of_second - last_of_first, 300_000_000)

    def test_cancel_and_connection_failure(self):
        cancel = threading.Event()
        cancel.set()
        summary = self.sender(RecordingSocket(self.clock), steps=(8, 16)).run(cancel=cancel)
        self.assertTrue(summary['cancelled'])
        self.assertEqual(summary['steps'], [])

        class Unreachable(RecordingSocket):
            def connect(self, target):
                raise OSError('Network is unreachable')
        summary = self.sender(Unreachable(self.clock)).run()
        self.assertIn('could not reach', summary['error'])

    def test_requests_outside_the_plan_limits_are_refused(self):
        sock = RecordingSocket(self.clock)
        for options in ({'fps': 5}, {'steps': []}, {'steps': [0]}, {'steps': [20000]}, {'steps': [80] * 17},
                        {'step_seconds': 30}, {'packet_size': 100}, {'pace_mbps': -1}, {'steps': ['80']}):
            arguments = {'steps': (8,), 'fps': 10, **options}
            with self.assertRaises(ValueError, msg=options):
                self.sender(sock, **arguments)
        for target in (('224.0.0.1', 9950), ('0.0.0.0', 9950), ('255.255.255.255', 9950), ('host', 9950),
                       ('192.168.1.20', 0), ('192.168.1.20', '9950'), '192.168.1.20:9950'):
            with self.assertRaises(ValueError, msg=target):
                LinkTestSender(sock, target, self.test_id, 30, [80])
        with self.assertRaises(ValueError):
            LinkTestSender(sock, ('192.168.1.20', 9950), -1, 30, [80])


INTERVAL = 10_000_000  # 100 fps


def feed(receiver, step=0, frames=10, packets=5, drop=(), late=None, send_interval=INTERVAL, test_id=None,
         base=5_000_000_000):
    """Feed synthetic datagrams: packet p of frame f arrives at base + f*10 ms + p*10 µs (+ late[(f, p)])."""
    late = late or {}
    for frame in range(frames):
        for packet in range(packets):
            if frame in drop or (frame, packet) in drop:
                continue
            data = pack_datagram(step, receiver.test_id if test_id is None else test_id, frame, packet, packets,
                                 frame * send_interval + packet * 2000)
            arrival = base + frame * INTERVAL + packet * 10_000 + late.get((frame, packet), 0) + late.get(frame, 0)
            receiver._accept(data, arrival)


class ReceiverStatisticsTests(unittest.TestCase):
    def receiver(self, **options):
        options.setdefault('fps', 100)
        options.setdefault('steps', [100])
        return LinkTestReceiver(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), 42, **options)

    def tearDown(self):
        for receiver in getattr(self, 'opened', []):
            receiver.sock.close()

    def make(self, **options):
        receiver = self.receiver(**options)
        self.opened = getattr(self, 'opened', []) + [receiver]
        return receiver

    def test_clean_step(self):
        receiver = self.make()
        feed(receiver)
        [row] = receiver.results()
        self.assertEqual((row['frames_expected'], row['frames_complete'], row['packets_expected'],
                          row['packets_received']), (10, 10, 50, 50))
        self.assertEqual((row['packet_loss_pct'], row['frame_loss_pct']), (0.0, 0.0))
        self.assertEqual((row['spread_mean_ms'], row['spread_p95_ms'], row['jitter_p95_ms']), (0.04, 0.04, 0.0))
        self.assertEqual((row['rate_mbps'], row['frame_interval_ms'], row['sender_mbps']), (100, 10.0, 100.0))
        self.assertFalse(row['end_marker'])
        self.assertTrue(link_test.step_verdict(row)[0])

    def test_one_lost_packet_loses_its_frame(self):
        receiver = self.make()
        feed(receiver, drop={(3, 2)})
        [row] = receiver.results()
        self.assertEqual((row['packets_received'], row['packets_expected'], row['frames_complete']), (49, 50, 9))
        self.assertEqual((row['packet_loss_pct'], row['frame_loss_pct']), (2.0, 10.0))

    def test_frames_with_no_packets_count_when_later_frames_arrived(self):
        receiver = self.make()
        feed(receiver, drop={0, 5})
        [row] = receiver.results()
        self.assertEqual((row['frames_expected'], row['frames_received'], row['packets_expected']), (10, 8, 50))
        self.assertEqual((row['packet_loss_pct'], row['frame_loss_pct']), (20.0, 20.0))

    def test_trailing_lost_frames_need_the_end_marker(self):
        receiver = self.make()
        feed(receiver, drop={8, 9})
        [row] = receiver.results()
        self.assertEqual((row['frames_expected'], row['packet_loss_pct']), (8, 0.0))
        marker = pack_header(0, 42, 10, 0, 5, 0, flags=FLAG_STEP_END)
        self.assertTrue(receiver._accept(marker, 6_000_000_000))
        [row] = receiver.results()
        self.assertEqual((row['frames_expected'], row['packets_expected'], row['packets_received']), (10, 50, 40))
        self.assertEqual((row['packet_loss_pct'], row['frame_loss_pct'], row['end_marker']), (20.0, 20.0, True))

    def test_marker_only_step_is_fully_lost(self):
        receiver = self.make(steps=[100, 200])
        feed(receiver)
        receiver._accept(pack_header(1, 42, 100, 0, 18, 0, flags=FLAG_STEP_END), 7_000_000_000)
        rows = receiver.results()
        self.assertEqual([row['step'] for row in rows], [0, 1])
        self.assertEqual((rows[1]['packets_expected'], rows[1]['packet_loss_pct'], rows[1]['rate_mbps']),
                         (1800, 100.0, 200))
        self.assertEqual(link_test.step_verdict(rows[1]), (False, 'loss'))

    def test_foreign_invalid_and_duplicate_datagrams(self):
        receiver = self.make()
        feed(receiver, test_id=43)
        for data in (b'short', b'XBLT' + pack_datagram(0, 42, 0, 0, 5, 0)[4:],
                     HEADER.pack(b'VRLT', 1, 0, 0, 42, 0, 5, 5, 0), HEADER.pack(b'VRLT', 2, 0, 0, 42, 0, 0, 5, 0),
                     HEADER.pack(b'VRLT', 1, 0x02, 0, 42, 0, 0, 5, 0), HEADER.pack(b'VRLT', 1, 0, 99, 42, 0, 0, 5, 0)):
            self.assertFalse(receiver._accept(data, 1))
        self.assertEqual(receiver.results(), [])
        self.assertEqual(receiver.ignored, 56)
        feed(receiver)
        feed(receiver)  # every datagram again
        self.assertFalse(receiver._accept(pack_datagram(0, 42, 1, 0, 6, 0), 1))  # changed packet count
        [row] = receiver.results()
        self.assertEqual((row['packets_received'], row['duplicates'], row['packet_loss_pct']), (50, 50, 0.0))

    def test_spread_and_jitter(self):
        receiver = self.make()
        feed(receiver, late={4: 3_000_000, (7, 4): 5_000_000})
        [row] = receiver.results()
        # Frame 4 completes 3 ms late: intervals 3->4 and 4->5 are each 3 ms off; frame 7's last
        # packet is 5 ms late, so its spread is 5.04 ms and intervals 6->7 and 7->8 are 5 ms off.
        self.assertEqual(row['spread_p95_ms'], 5.04)
        self.assertAlmostEqual(row['spread_mean_ms'], (9 * 0.04 + 5.04) / 10, places=3)
        self.assertEqual(row['jitter_p95_ms'], 5.0)
        self.assertEqual(row['packet_loss_pct'], 0.0)

    def test_slow_sender_is_detected_from_its_timestamps(self):
        receiver = self.make()
        feed(receiver, send_interval=2 * INTERVAL)
        [row] = receiver.results()
        self.assertEqual(row['sender_mbps'], 50.0)
        self.assertEqual(link_test.step_verdict(row), (False, 'sender'))

    def test_without_a_plan_interval_and_rate_are_inferred(self):
        receiver = self.make(fps=None, steps=None)
        feed(receiver, send_interval=INTERVAL)
        [row] = receiver.results()
        self.assertEqual(row['frame_interval_ms'], 10.0)
        self.assertEqual(row['rate_mbps'], 6)  # 5 x 1400 bytes x 100 fps = 5.6 Mbit/s
        self.assertIsNone(row['sender_mbps'])

    def test_closed_socket_ends_the_run(self):
        class Closed:
            def __init__(self):
                self.calls = 0

            def getsockopt(self, *arguments):
                return 212992

            def settimeout(self, value):
                pass

            def recv_into(self, buffer):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionResetError('earlier ICMP error')
                raise OSError(9, 'Bad file descriptor')
        sock = Closed()
        receiver = LinkTestReceiver(sock, 42, fps=100, steps=[100])
        self.assertEqual(receiver.run(30), [])
        self.assertEqual(sock.calls, 2)

    def test_receiver_validates_its_arguments(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        for arguments in ({'test_id': -1}, {'test_id': 2 ** 64}, {'test_id': 1, 'fps': 1000},
                          {'test_id': 1, 'steps': [0]}, {'test_id': 1, 'steps': 'fast'}):
            with self.assertRaises(ValueError):
                LinkTestReceiver(sock, **arguments)


def row(rate, loss=0.0, frame_loss=0.0, spread=2.0, interval=11.111, sender=None, received=1000):
    return {'step': 0, 'rate_mbps': rate, 'packet_loss_pct': loss, 'frame_loss_pct': frame_loss,
            'spread_p95_ms': spread, 'frame_interval_ms': interval, 'sender_mbps': sender,
            'packets_expected': 1000, 'packets_received': received}


class RecommendTests(unittest.TestCase):
    def test_network_limited_on_ethernet_and_wifi(self):
        rows = [row(80), row(150), row(250), row(400), row(600, loss=0.4, frame_loss=8)]
        result = recommend(rows, link_kind='wired')
        self.assertEqual((result['recommended_mbps'], result['clean_mbps'], result['failed_mbps'], result['limited_by']),
                         (280, 400, 600, 'network'))
        self.assertTrue(result['message'].startswith('Network test: clean up to 400 Mbit/s on Ethernet'))
        self.assertTrue(result['message'].endswith('using 280 Mbit/s.'))
        self.assertIn('data was lost at 600 Mbit/s', result['message'])
        self.assertEqual(recommend(rows, link_kind='wifi')['recommended_mbps'], 240)

    def test_thresholds(self):
        self.assertTrue(link_test.step_verdict(row(80, loss=0.05, frame_loss=0.2, spread=6.66))[0])
        self.assertEqual(link_test.step_verdict(row(80, loss=0.051)), (False, 'loss'))
        self.assertEqual(link_test.step_verdict(row(80, frame_loss=0.21)), (False, 'loss'))
        self.assertEqual(link_test.step_verdict(row(80, spread=6.667)), (False, 'late'))
        self.assertEqual(link_test.step_verdict(row(80, sender=71)), (False, 'sender'))
        self.assertTrue(link_test.step_verdict(row(80, sender=72))[0])
        self.assertTrue(link_test.step_verdict(dict(row(80), achieved_mbps=79, sender_mbps=10))[0])

    def test_codec_cap_limits_a_clean_fast_link(self):
        result = recommend([row(rate) for rate in (80, 150, 250, 400, 600, 900)], link_kind='wired')
        self.assertEqual((result['recommended_mbps'], result['clean_mbps'], result['limited_by']), (300, 900, 'codec'))
        self.assertIn('the most the video settings allow', result['message'])
        self.assertEqual(recommend([row(400)], codec_cap_mbps=150)['recommended_mbps'], 150)

    def test_short_clean_ladder_is_not_limited(self):
        result = recommend([row(80), row(150), row(250)], link_kind='wired')
        self.assertEqual(result['message'], 'Network test: clean up to 250 Mbit/s on Ethernet; using 170 Mbit/s.')
        self.assertEqual((result['recommended_mbps'], result['limited_by'], result['failed_mbps']), (170, 'none', None))
        result = recommend([row(20)], link_kind='unknown')
        self.assertEqual(result['recommended_mbps'], 20)
        self.assertIn('on this connection', result['message'])

    def test_late_frames_and_slow_sender_name_the_limit(self):
        result = recommend([row(80), row(150), row(250, spread=9.0)], link_kind='wired')
        self.assertEqual((result['clean_mbps'], result['limited_by']), (150, 'network'))
        self.assertIn('data arrived late at 250 Mbit/s', result['message'])
        result = recommend([row(80), row(150), row(250, sender=180)], link_kind='wired')
        self.assertEqual((result['clean_mbps'], result['limited_by'], result['recommended_mbps']), (150, 'sender', 100))
        self.assertIn('host PC could not send', result['message'])

    def test_everything_failing_falls_back_to_30(self):
        result = recommend([row(80, loss=2.0, frame_loss=30), row(150, loss=5.0, frame_loss=60)], link_kind='wifi')
        self.assertEqual((result['recommended_mbps'], result['clean_mbps'], result['failed_mbps'], result['limited_by']),
                         (30, 0, 80, 'network'))
        self.assertIn('losing data even at 80 Mbit/s', result['message'])
        self.assertIn('Wi-Fi access point', result['message'])

    def test_nothing_received_leaves_the_bitrate_alone(self):
        for rows in ([], None, [row(80, received=0)], ['junk', {'rate_mbps': 'fast'}]):
            result = recommend(rows)
            self.assertIsNone(result['recommended_mbps'], rows)
            self.assertIn('not changed', result['message'])

    def test_merge_sender_adds_host_figures_and_unseen_steps(self):
        receiver_rows = [dict(row(80), step=0), dict(row(150), step=1)]
        summary = {'steps': [{'step': 0, 'rate_mbps': 80, 'achieved_mbps': 80.4, 'send_errors': 0, 'packets_sent': 10},
                             {'step': 1, 'rate_mbps': 150, 'achieved_mbps': 120.0, 'send_errors': 3, 'packets_sent': 10},
                             {'step': 2, 'rate_mbps': 250, 'achieved_mbps': 250.0, 'send_errors': 0,
                              'frames_sent': 90, 'packets_sent': 1800}]}
        merged = merge_sender(receiver_rows, summary)
        self.assertEqual([item['step'] for item in merged], [0, 1, 2])
        self.assertEqual((merged[1]['achieved_mbps'], merged[1]['send_errors']), (120.0, 3))
        self.assertEqual((merged[2]['packets_expected'], merged[2]['packet_loss_pct']), (1800, 100.0))
        result = recommend(merged, link_kind='wired')
        self.assertEqual((result['clean_mbps'], result['limited_by'], result['recommended_mbps']), (80, 'sender', 50))
        self.assertEqual(receiver_rows[1].get('achieved_mbps'), None)


class LoopbackLinkTests(unittest.TestCase):
    """Real UDP over 127.0.0.1: a short ladder must arrive complete."""

    def run_test(self, steps, fps=30, step_seconds=0.3, pace_mbps=0):
        test_id = secrets.randbits(64)
        receive_socket = link_test.open_receiver('127.0.0.1', 0)
        send_socket = link_test.open_sender()
        self.addCleanup(receive_socket.close)
        self.addCleanup(send_socket.close)
        receiver = LinkTestReceiver(receive_socket, test_id, fps=fps, steps=steps)
        results = {}
        thread = threading.Thread(target=lambda: results.update(rows=receiver.run(10)))
        thread.start()
        sender = LinkTestSender(send_socket, receive_socket.getsockname(), test_id, fps, steps,
                                step_seconds=step_seconds, pace_mbps=pace_mbps)
        summary = sender.run()
        thread.join(15)
        self.assertFalse(thread.is_alive())
        return summary, results['rows']

    def test_short_ladder_arrives_without_loss(self):
        summary, rows = self.run_test([20, 40])
        self.assertEqual([step['frames_sent'] for step in summary['steps']], [9, 9])
        self.assertTrue(all(step['keeping_up'] and step['send_errors'] == 0 for step in summary['steps']))
        self.assertEqual([item['step'] for item in rows], [0, 1])
        for item in rows:
            self.assertEqual((item['frames_expected'], item['frames_complete']), (9, 9), item)
            self.assertEqual((item['packet_loss_pct'], item['frame_loss_pct'], item['duplicates']), (0.0, 0.0, 0), item)
            self.assertTrue(item['end_marker'])
            self.assertTrue(math.isclose(item['sender_mbps'], item['rate_mbps'], rel_tol=0.05), item)
        self.assertEqual(rows[1]['packets_received'], 9 * link_test.packets_per_frame(40, 30))
        result = recommend(merge_sender(rows, summary), link_kind='wired')
        self.assertIsInstance(result['recommended_mbps'], int)
        self.assertGreaterEqual(result['recommended_mbps'], 20)

    def test_paced_step_arrives_without_loss(self):
        summary, rows = self.run_test([20], pace_mbps=200)
        self.assertEqual(summary['steps'][0]['packets_sent'], 9 * link_test.packets_per_frame(20, 30))
        self.assertEqual((rows[0]['packet_loss_pct'], rows[0]['frame_loss_pct']), (0.0, 0.0))
        self.assertTrue(summary['steps'][0]['keeping_up'])
        # Pacing timing itself is checked with a fake clock in SenderTests; in one process the
        # receiver thread observes arrivals only when it holds the GIL.


if __name__ == '__main__':
    unittest.main()
