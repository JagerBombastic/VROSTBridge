"""Client side of the network test, link facts and the stream pacing rate.

The test (launcher/link_test.py) imitates the video: the host sends frame-sized
bursts at rising rates and this client measures what arrives. It runs only
between sessions, takes about fifteen seconds, and its result is kept per host and
adapter, so automatic bitrate needs one test per network path, not one per start.

Pacing: the host may sit on a 10 Gbit/s link while this PC uses 1-2.5 Gbit/s
Ethernet or Wi-Fi. Each video frame then leaves the host as a burst ten times
faster than the last link can carry, and a switch or router with small buffers
drops the excess. The host can spread each frame at the client's link rate
instead (patch 0009), which costs no latency because the slower link would take
that long anyway. pacing_mbps() picks that rate; 0 leaves the stream unpaced.

1.0.0.4: an adapter can receive less than its link speed. In the 1.0.0.3 test a 1 Gbit/s
USB Ethernet adapter lost no data up to 500 Mbit/s but lost 12 % at 750 and 27 % at
900 (receiving at most about 650), while frames were paced at 920 Mbit/s: one frame was
lost every 3 s or so, and each loss cost three or four frames while a keyframe was
fetched. Wired pacing now stays at or below the highest rate the network test carried
without loss on that path (any adapter); only without a test result on that path is a
USB adapter, which often receives well below its link speed, paced at 60 % of it.

1.0.0.5: a test that finds no rate the path carries cleanly is not kept for a month. In
the 1.0.0.4 test a USB Ethernet adapter that had carried 500 Mbit/s without loss lost a
few packets at 80 Mbit/s and frames seemed late at 150, so every session after it ran at
30 Mbit/s. Such a result is tested again before the next session, up to three tests in a
row, and an earlier result that did find a clean rate is used meanwhile. The test itself
now measures the network rather than how busy the companion was (launcher/link_test.py):
a warm-up step, kernel arrival times, and the receiver's own dropped datagrams counted
apart; while it runs, the companion's other threads hand over to the receiving thread
every half millisecond rather than every five.
"""
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time

from launcher.link_test import (DEFAULT_PACKET_SIZE, WARMUP_STEPS, LinkTestReceiver, expected_duration,
                                merge_sender, open_receiver, paced_below, plan_steps, recommend)
from launcher import windows_network

STREAM_PORT = 9944          # UDP port the client firewall already allows from the host's subnet
WIRED_PACING_FRACTION = 0.92
USB_PACING_FRACTION = 0.6   # 1.0.0.4: USB Ethernet adapters often receive well below their link speed
MAX_SAVED_RESULTS = 8
MAX_AGE_S = 30 * 24 * 3600  # a saved result is used for this long (launcher/app.py path_test)
RETEST_LIMIT = 3            # 1.0.0.5: tests in a row that may find no clean rate before such a result is kept
RECEIVE_SWITCH_INTERVAL = 0.0005  # 1.0.0.5: seconds between thread switches while the test receives
_INTERFACE = re.compile(r'(?!-)[A-Za-z0-9_.:-]{1,15}')


def _wifi_rates(result, interface, wifi_sample):
    sample = {}
    if wifi_sample is not None:
        try:
            sample = wifi_sample(interface) or {}
        except Exception:
            sample = {}
    for key, name in (('rx_mbps', 'rx_bitrate_mbps'), ('tx_mbps', 'tx_bitrate_mbps')):
        value = sample.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            result[key] = round(float(value), 1)
    return result


def _link_info_windows(result, interface, wifi_sample):
    """Windows: the adapter (by alias) from GetAdaptersAddresses; Wi-Fi rates from the Wi-Fi sample, else the
    adapter's own receive/transmit link speeds."""
    if not interface or not windows_network.valid_alias(interface):
        return result
    try:
        adapter = windows_network.adapter_for(interface)
    except windows_network.WindowsNetworkError:
        return result
    if adapter is None:
        return result
    if adapter.get('kind') == 'wifi':
        result['kind'] = 'wifi'
        _wifi_rates(result, interface, wifi_sample)
        for key, name in (('rx_mbps', 'receive_mbps'), ('tx_mbps', 'transmit_mbps')):
            value = adapter.get(name)
            if result[key] is None and isinstance(value, (int, float)) and 0 < value <= 100000:
                result[key] = round(float(value), 1)
        return result
    result['kind'] = 'wired'
    speed = adapter.get('transmit_mbps')
    if isinstance(speed, (int, float)) and 10 <= speed <= 400000:
        result['speed_mbps'] = int(speed)
    result['usb_adapter'] = bool(adapter.get('usb'))
    return result


def link_info(interface, sys_class_net='/sys/class/net', wifi_sample=None):
    """{'interface', 'kind': wired|wifi|unknown, 'speed_mbps', 'rx_mbps', 'tx_mbps'} for an adapter
    (wired adds 'usb_adapter'). Windows: interface is the adapter alias ("Ethernet 3")."""
    result = {'interface': interface or '', 'kind': 'unknown', 'speed_mbps': None, 'rx_mbps': None, 'tx_mbps': None}
    if sys.platform == 'win32':
        return _link_info_windows(result, interface, wifi_sample)
    if not interface or not _INTERFACE.fullmatch(interface):
        return result
    base = Path(sys_class_net) / interface
    if (base / 'wireless').is_dir() or (base / 'phy80211').exists():
        result['kind'] = 'wifi'
        return _wifi_rates(result, interface, wifi_sample)
    if base.is_dir():
        result['kind'] = 'wired'
        try:
            speed = int((base / 'speed').read_text().strip())
            if 10 <= speed <= 400000:
                result['speed_mbps'] = speed
        except (OSError, ValueError):
            pass
        try:
            result['usb_adapter'] = '/usb' in os.path.realpath(base / 'device')
        except (OSError, ValueError):
            result['usb_adapter'] = False
    return result


def lossless_rate(test):
    """The most a network test saw this path carry before it lost packets or frames (1.0.0.4), or None
    (no test, or no loss in it: no limit found)."""
    test = test if isinstance(test, dict) else {}
    value = test.get('lossless_mbps', (test.get('recommendation') or {}).get('lossless_mbps'))
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 50 <= value <= 40000 else None


def path_tested(test):
    """Whether a network test from 1.0.0.4 or later measured this path (it records lossless_mbps, even as None)."""
    test = test if isinstance(test, dict) else {}
    return 'lossless_mbps' in test or 'lossless_mbps' in (test.get('recommendation') or {})


def pacing_mbps(link, test=None, bitrate=None, measuring=False):
    """Video pacing rate for this link, or 0 (unpaced).

    Wired: just below the adapter's link speed (10 Gbit/s clients need none) and, from
    1.0.0.4, never above the most the network test saw this path carry before it lost
    packets or frames (1.0.0.8: PACING_HEADROOM of it, so other traffic and a busy moment
    on this PC do not tip frames into loss); without such a test a USB adapter is paced at
    60 % of its link speed.
    Wi-Fi: the last clean network-test rate on this path, which is what the air
    link actually carried. The radio's PHY rate is not used: real Wi-Fi throughput
    is often under half of it, so pacing to it (RC4) let frame bursts still flood
    the access point. Without a test result Wi-Fi stays unpaced.
    Pacing is skipped when it would be under 1.25 x the bitrate: frames would then
    wait for the pacer longer than the link itself needs.
    measuring: the pace of the network test itself, which must be able to reach every
    step's rate (the link fraction only).
    """
    rate = 0
    kind = (link or {}).get('kind')
    if kind == 'wired':
        speed = link.get('speed_mbps')
        if speed and speed < 10000:
            rate = int(speed * WIRED_PACING_FRACTION)
            lossless = None if measuring else lossless_rate(test)
            if lossless:  # what this path carried without loss, whatever the adapter (1.0.0.8: with headroom)
                rate = min(rate, paced_below(lossless))
            elif link.get('usb_adapter') and not measuring and not path_tested(test):
                rate = int(speed * USB_PACING_FRACTION)
    elif kind == 'wifi':
        test = test or {}
        clean = test.get('clean_mbps') or (test.get('recommendation') or {}).get('clean_mbps') or 0
        rate = int(clean) if isinstance(clean, (int, float)) and not isinstance(clean, bool) else 0
    if bitrate and rate and rate < 1.25 * bitrate:
        return 0
    return rate if 50 <= rate <= 40000 else 0


def path_key(host_id, interface):
    # Linux interface names have at most 15 characters; Windows aliases are longer ("Беспроводная сеть 2"),
    # and two of them must not share a key.
    return str(host_id or '')[:32] + '|' + str(interface or '')[:64 if sys.platform == 'win32' else 15]


class NetworkTest:
    """Run the test against the host companion and remember the result per path."""

    def __init__(self, session_link, log=print, wifi_sample=None, receiver_socket=open_receiver,
                 clock=time.time):
        self.session_link, self.log, self.wifi_sample = session_link, log, wifi_sample
        self.receiver_socket, self.clock = receiver_socket, clock
        self.lock = threading.Lock()
        self.running = threading.Lock()
        self.value = {'state': 'idle', 'message': ''}

    def status(self):
        with self.lock:
            return dict(self.value)

    def _set(self, state, message, **extra):
        with self.lock:
            self.value = dict({'state': state, 'message': message}, **extra)
        if message:
            self.log('NETWORK TEST ' + state + ': ' + message)

    def run(self, host_ip='', fps=90.0, codec_cap_mbps=300, cancel=None):
        """Blocking; returns a result dict (recommendation may have recommended_mbps None)."""
        if not self.running.acquire(blocking=False):
            raise ValueError('A network test is already running.')
        try:
            self._set('running', 'Testing the network to the host PC (about 15 seconds)...')
            try:
                result = self._run(host_ip, fps, codec_cap_mbps, cancel)
            except Exception as error:
                self._set('error', 'Network test failed: ' + str(error)[:300])
                raise
            recommendation = result['recommendation']
            self._set('done', recommendation.get('message', ''), result=result)
            return result
        finally:
            self.running.release()

    def _run(self, host_ip, fps, codec_cap_mbps, cancel):
        host, source, interface = self.session_link.source_path(host_ip)
        link = link_info(interface, wifi_sample=self.wifi_sample)
        limit = None
        if link['kind'] == 'wired' and link['speed_mbps']:
            limit = link['speed_mbps'] * 0.95
        elif link['kind'] == 'wifi' and link['rx_mbps']:
            limit = link['rx_mbps'] * 0.95
        steps = plan_steps(limit, link['kind'])
        # 1.0.0.5: the first step repeats the lowest rate to wake the path; it is reported but not judged.
        plan = steps[:1] * WARMUP_STEPS + steps
        fps = float(fps) if isinstance(fps, (int, float)) and 10 <= fps <= 240 else 90.0
        pace = pacing_mbps(link, measuring=True)
        try:
            sock = self.receiver_socket(source, STREAM_PORT)
        except OSError:
            sock = self.receiver_socket(source, 0)  # stream port busy; works when the firewall allows it
        switch_interval = sys.getswitchinterval()
        try:
            port = sock.getsockname()[1]
            test_id = secrets.randbits(64)
            receiver = LinkTestReceiver(sock, test_id, fps=fps, steps=plan)
            duration = expected_duration(len(plan)) + 3.0
            outcome = {}

            def receive():
                try:
                    outcome['rows'] = receiver.run(duration, cancel)
                except Exception as error:  # reported with the result
                    outcome['error'] = str(error)[:200]

            # 1.0.0.5: the receiving thread waits for its turn at most this long while other threads run.
            sys.setswitchinterval(min(switch_interval, RECEIVE_SWITCH_INTERVAL))
            thread = threading.Thread(target=receive, daemon=True, name='VROSTBridge-network-test')
            thread.start()
            request = {'test_id': test_id, 'port': port, 'fps': fps, 'steps': plan, 'step_seconds': 1.0,
                       'packet_size': DEFAULT_PACKET_SIZE, 'pace_mbps': pace}
            try:
                _, sender = self.session_link.link_test(request, timeout=duration + 20, host_ip=host['host_ip'])
            finally:
                thread.join(duration + 5)
        finally:
            sys.setswitchinterval(switch_interval)
            sock.close()
        rows = merge_sender(outcome.get('rows') or receiver.results(), sender)
        for row in rows:
            row['warmup'] = type(row.get('step')) is int and row['step'] < WARMUP_STEPS
        recommendation = recommend(rows, codec_cap_mbps, link['kind'], pace if link['kind'] == 'wired' else 0)
        compact = [{key: row.get(key) for key in ('rate_mbps', 'packet_loss_pct', 'frame_loss_pct', 'spread_p95_ms',
                                                   'send_spread_p95_ms', 'network_spread_p95_ms', 'jitter_p95_ms',
                                                   'received_mbps', 'achieved_mbps', 'local_drops', 'warmup')}
                   for row in rows][:16]
        return {'time': self.clock(), 'host_id': host.get('host_id', ''), 'host_ip': host.get('host_ip', ''),
                'source_ip': source, 'interface': interface, 'link': link, 'pace_mbps': pace, 'fps': fps,
                'steps': compact, 'sender_error': str((sender or {}).get('error') or '')[:200],
                'receiver_error': outcome.get('error', ''), 'recommendation': recommendation,
                'receiver': {'arrival_clock': receiver.arrival_clock, 'counts_local_drops': receiver.counts_local_drops,
                             'receive_buffer_bytes': receiver.receive_buffer_bytes}}


def found_rate(test):
    """Whether a test (or a saved result) found a rate the path carried cleanly."""
    test = test if isinstance(test, dict) else {}
    clean = test.get('clean_mbps', (test.get('recommendation') or {}).get('clean_mbps'))
    return isinstance(clean, (int, float)) and not isinstance(clean, bool) and clean > 0


def failed_tests(saved):
    """Tests in a row on a path that found no clean rate (1.0.0.5). A result saved before 1.0.0.5 counts as one
    when it found none (or received nothing)."""
    if not isinstance(saved, dict):
        return 0
    count = saved.get('failures')
    if type(count) is int and 0 <= count <= RETEST_LIMIT:
        return count
    return 1 if 'clean_mbps' in saved and not found_rate(saved) else 0


def sender_timed(saved):
    """1.0.0.6: a saved result whose limit was data arriving late, judged by a version that could not tell the
    host's own sending time from the network's (no step has network_spread_p95_ms). The 1.0.0.5 test called a
    loss-free 150 Mbit/s step late because the host's sender took 9.5 ms to write each frame."""
    if not isinstance(saved, dict) or 'arrived late' not in str(saved.get('message') or '') and \
            'delaying data' not in str(saved.get('message') or ''):
        return False
    steps = saved.get('steps') if isinstance(saved.get('steps'), list) else []
    return not any(isinstance(row, dict) and row.get('network_spread_p95_ms') is not None for row in steps)


def needs_retest(saved):
    """1.0.0.5: test the path again before its next session: the last test found no rate it carried cleanly,
    which is more often a passing disturbance (this PC busy, an adapter waking up, other traffic) than the
    path's real limit. After RETEST_LIMIT such tests in a row the result stands like any other.
    1.0.0.6: also once when lateness set the limit and was judged without the sender's own time
    (sender_timed)."""
    return 0 < failed_tests(saved) < RETEST_LIMIT or sender_timed(saved)


def remember(saved, result):
    """Saved results dict (path key -> compact result), newest MAX_SAVED_RESULTS kept.

    1.0.0.5: a test that found no clean rate counts as a failure on its path; until RETEST_LIMIT of them in a
    row, an earlier result that did find one, and is still young enough to be used (MAX_AGE_S), is kept (with
    the new message) and the path is tested again before its next session (needs_retest)."""
    saved = dict(saved) if isinstance(saved, dict) else {}
    key = path_key(result.get('host_id'), result.get('interface'))
    recommendation = result.get('recommendation') or {}
    entry = {'time': result.get('time'), 'recommended_mbps': recommendation.get('recommended_mbps'),
             'clean_mbps': recommendation.get('clean_mbps'), 'limited_by': recommendation.get('limited_by'),
             'message': str(recommendation.get('message', ''))[:300], 'link': result.get('link'),
             'pace_mbps': result.get('pace_mbps')}
    # 1.0.0.4: what wired pacing stays under on this path (None: no limit found, or no step was loss-free). Its
    # presence marks a 1.0.0.4 test: a wired path tested before is tested once more before its next session
    # (ensure_network_test).
    entry['lossless_mbps'] = lossless_rate(recommendation)
    entry['failures'] = 0
    # 1.0.0.5: the steps and how the receiver timed them, for reports.
    measured = {'steps': list(result.get('steps') or [])[:16] if isinstance(result.get('steps'), list) else [],
                'receiver': result.get('receiver') if isinstance(result.get('receiver'), dict) else None}
    entry.update(measured)
    if not found_rate(recommendation):
        earlier = saved.get(key) if isinstance(saved.get(key), dict) else None
        failures = min(RETEST_LIMIT, failed_tests(earlier) + 1)
        retest = failures < RETEST_LIMIT
        young = earlier is not None and (result.get('time') or 0) - (earlier.get('time') or 0) < MAX_AGE_S
        if retest and young and found_rate(earlier):
            used = earlier.get('recommended_mbps')
            entry = dict(earlier, message=(entry['message'] + ' Automatic bitrate keeps the earlier result'
                                           + (' (' + str(used) + ' Mbit/s)' if used else '')
                                           + ' and tests the network again before the next session.')[:400],
                         failed_test=dict(measured, time=result.get('time')))
        elif retest:
            entry['message'] = (entry['message'] + ' The network is tested again before the next session.')[:400]
        entry['failures'] = failures
    saved[key] = entry
    newest = sorted(saved.items(), key=lambda item: item[1].get('time') or 0, reverse=True)[:MAX_SAVED_RESULTS]
    return dict(newest)


def saved_for(saved, host_id, interface):
    value = (saved or {}).get(path_key(host_id, interface)) if isinstance(saved, dict) else None
    return value if isinstance(value, dict) else None
