"""Session ownership, LAN authorization, recovery and discovery regression tests."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from launcher.session_link import (SessionHost, SessionClient, SessionError, PROTOCOL, NATIVE_PROTOCOL,
                                   LEASE_SECONDS, _interfaces, session_settings)


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('Timed out waiting for session state')
        time.sleep(.01)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.started, self.stopped, self.logs = Mock(), Mock(), []
        self.policy = Mock(return_value=True)
        self.host = SessionHost(self.root/'host', self.started, self.stopped, lambda:{'runtime_state':'ready','available':True},
                                self.policy, self.logs.append, port=0,
                                address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(self.host.close)
        self.client = SessionClient(self.root/'client', self.logs.append, port=self.host.port,
                                    address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(self.client.close)

    def credentials(self, pair=True):
        value = {'client_id': secrets.token_hex(16), 'token': secrets.token_hex(32)}
        if pair:
            self.host.dispatch('/pair', value, '127.0.0.1')
        return value

    def test_client_start_owns_host_stop_and_retries_are_idempotent(self):
        state = self.client.start({'codec': 'H264', 'bitrate': 120}, host_ip='127.0.0.1')
        self.assertTrue(state['active'])
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.started.assert_called_once_with({'codec': 'H264', 'bitrate': 120, 'microphone':False})
        first_lease = self.host.snapshot()['lease_id']
        self.client.start(host_ip='127.0.0.1')
        self.assertEqual(self.host.snapshot()['lease_id'], first_lease)
        self.assertEqual(self.started.call_count, 1)
        self.client.heartbeat()
        self.client.stop()
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.client.stop()
        self.stopped.assert_called_once()
        self.assertFalse(self.client.snapshot()['active'])
        self.assertNotIn(self.client.token, json.dumps(self.client.snapshot()))
        self.assertNotIn(self.client.token, json.dumps(self.host.snapshot()))

    def test_delayed_start_retry_cannot_resurrect_stopped_session(self):
        owner = self.credentials()
        first, retry = secrets.token_hex(16), secrets.token_hex(16)
        result = self.host.dispatch('/start', dict(owner, request_id=first), '127.0.0.1')
        self.host.dispatch('/start', dict(owner, request_id=retry), '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.host.dispatch('/stop', dict(owner, lease_id=result['lease_id']), '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        for identity in (first, retry):
            with self.assertRaisesRegex(SessionError, 'has ended'):
                self.host.dispatch('/start', dict(owner, request_id=identity), '127.0.0.1')
        self.started.assert_called_once()

    def test_another_paired_client_cannot_start_or_stop_active_lease(self):
        self.client.start(host_ip='127.0.0.1')
        other = self.credentials()
        with self.assertRaisesRegex(SessionError, 'Another headset'):
            self.host.dispatch('/start', dict(other, request_id=secrets.token_hex(16)), '127.0.0.1')
        with self.assertRaisesRegex(SessionError, 'does not own'):
            self.host.dispatch('/stop', dict(other, lease_id=self.host.snapshot()['lease_id']), '127.0.0.1')
        self.assertNotIn('lease_id', self.host.dispatch('/status', other, '127.0.0.1'))

    def test_wrong_token_cannot_replace_saved_pairing(self):
        credentials = self.credentials()
        credentials['token'] = secrets.token_hex(32)
        with self.assertRaisesRegex(SessionError, 'not paired'):
            self.host.dispatch('/pair', credentials, '127.0.0.1')

    def test_private_network_policy_required_for_pair_and_new_start(self):
        self.policy.return_value = False
        with self.assertRaisesRegex(SessionError, 'pair from a network Windows classes as Private'):
            self.host.dispatch('/pair', self.credentials(pair=False), '127.0.0.1')
        self.policy.return_value = True
        value = self.credentials()
        self.policy.return_value = False
        with self.assertRaisesRegex(SessionError, 'unavailable'):
            self.host.dispatch('/start', dict(value, request_id=secrets.token_hex(16)), '127.0.0.1')
        self.started.assert_not_called()

    def test_client_close_during_discovery_does_not_start_host(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        failure = []
        original = self.client.discover
        def discover(ip, **kwargs):
            entered.set()
            release.wait(2)
            return original(ip)
        self.client.discover = discover
        def start():
            try:
                self.client.start(host_ip='127.0.0.1')
            except SessionError as error:
                failure.append(str(error))
        starter = threading.Thread(target=start)
        starter.start()
        self.assertTrue(entered.wait(1))
        closer = threading.Thread(target=self.client.close)
        closer.start()
        self.assertTrue(self.client.closed.wait(1))
        release.set()
        starter.join(2)
        closer.join(2)
        self.assertTrue(failure)
        self.started.assert_not_called()

    def test_close_during_policy_check_cannot_enqueue_start_after_shutdown(self):
        owner = self.credentials()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        result = []
        def policy():
            entered.set()
            release.wait(2)
            return True
        self.host.allow_start = policy
        def request():
            try:
                self.host.dispatch('/start', dict(owner, request_id=secrets.token_hex(16)), '127.0.0.1')
            except SessionError as error:
                result.append(str(error))
        worker = threading.Thread(target=request)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.host.close()
        release.set()
        worker.join(2)
        self.assertTrue(result)
        self.started.assert_not_called()
        self.assertNotIn('lease_id', self.host.snapshot())

    def test_disabling_new_pairing_keeps_existing_pair_allowed_to_start(self):
        owner = self.credentials()
        self.policy.return_value = False
        self.host.allow_start = lambda: True
        result = self.host.dispatch('/start', dict(owner, request_id=secrets.token_hex(16)), '127.0.0.1')
        self.assertEqual(result['state'], 'starting')
        with self.assertRaisesRegex(SessionError, 'pair from a network Windows classes as Private'):
            self.host.dispatch('/pair', self.credentials(pair=False), '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')

    def test_stop_during_blocked_start_is_serial_and_status_does_not_block(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def start(settings):
            entered.set()
            release.wait(3)
        self.host.start_callback = start
        self.client.start(host_ip='127.0.0.1')
        self.assertTrue(entered.wait(1))
        self.client.heartbeat()
        self.assertEqual(self.host.snapshot()['state'], 'starting')
        stopped = threading.Event()
        stop_thread = threading.Thread(target=lambda: (self.client.stop(), stopped.set()))
        stop_thread.start()
        eventually(lambda: self.host.snapshot()['state'] == 'stopping')
        self.stopped.assert_not_called()
        self.assertFalse(stopped.is_set())
        release.set()
        stop_thread.join(2)
        self.assertTrue(stopped.is_set())
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.stopped.assert_called_once()

    def test_heartbeat_deadline_stops_abandoned_session(self):
        now = [10.0]
        self.host.clock = lambda: now[0]
        owner = self.credentials()
        result = self.host.dispatch('/start', dict(owner, request_id=secrets.token_hex(16)), '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        now[0] += LEASE_SECONDS - 1
        self.host.dispatch('/heartbeat', dict(owner, lease_id=result['lease_id']), '127.0.0.1')
        now[0] += 2
        self.assertGreater(self.host.snapshot()['expires_in'], 20)
        now[0] += LEASE_SECONDS
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.stopped.assert_called_once()

    def test_host_emergency_stop_ends_lease_on_client_heartbeat(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.host.request_stop()
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.assertEqual(self.client.heartbeat()['state'], 'waiting')
        self.assertFalse(self.client.snapshot()['active'])

    def test_runtime_failure_is_observed_and_cleans_owned_session(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.host.status_callback = lambda: {'runtime_state':'error','error':'Compositor exited'}
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        result = self.client.heartbeat()
        self.assertIn('Compositor exited', result['message'])
        self.stopped.assert_called_once()

    def test_steam_quit_closes_lease_without_claiming_a_host_failure(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.host.status_callback = lambda: {'runtime_state':'stopped','shutdown_reason':'steam-quit','message':'Steam closed the host VR session.'}
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        result=self.client.heartbeat()
        self.assertEqual(result['state'],'waiting')
        self.assertFalse(result['active'])
        self.assertFalse(self.host.snapshot()['last_error'])
        self.assertNotIn('failure',result['message'].lower())
        self.stopped.assert_called_once()

    def test_stale_runtime_failure_cannot_stop_a_newer_lease(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed_status():
            entered.set()
            release.wait(4)
            return {'runtime_state':'error','error':'Old lease failed'}
        self.host.status_callback = delayed_status
        self.client.start(host_ip='127.0.0.1')
        self.assertTrue(entered.wait(2))
        self.client.stop()
        self.client.start(host_ip='127.0.0.1')
        # Launch accepted is deliberately still 'starting' while the prior
        # status read is blocked; only a fresh readiness sample can run it.
        eventually(lambda: self.started.call_count == 2)
        self.assertEqual(self.host.snapshot()['state'], 'starting')
        new_lease = self.host.snapshot()['lease_id']
        self.host.status_callback = lambda: {'runtime_state':'ready','available':True,'message':'New lease ready'}
        release.set()
        eventually(lambda: self.host.snapshot()['runtime'].get('message') == 'New lease ready')
        self.assertEqual(self.host.snapshot()['lease_id'], new_lease)
        self.assertEqual(self.host.snapshot()['state'], 'running')
        self.assertEqual(self.stopped.call_count, 1)

    def test_host_close_stops_active_lease(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        # Avoid cleanup client HTTP wait against a closed server.
        self.client.lease_id = ''
        self.host.close()
        self.stopped.assert_called_once()

    def test_start_failure_runs_cleanup_before_allowing_new_session(self):
        self.started.side_effect = ValueError('Runtime startup failed')
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.stopped.call_count == 1)
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.assertTrue(any('Runtime startup failed' in line for line in self.logs))
        self.started.side_effect = None
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')

    def test_cleanup_failure_retains_ownership_and_retries_stop(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        owner = self.client._credentials(lease=True)
        self.stopped.side_effect = ValueError('SteamVR still stopping')
        self.host.dispatch('/stop', owner, '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'error')
        self.assertIn('lease_id', self.host.snapshot())
        self.stopped.side_effect = None
        self.host.dispatch('/stop', owner, '127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'waiting')
        self.assertEqual(self.stopped.call_count, 2)

    def test_stop_waits_for_callback_to_collect_final_diagnostics(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        captured = threading.Event()
        def stop():
            time.sleep(.15)
            captured.set()
        self.host.stop_callback = stop
        result = self.client.stop()
        self.assertTrue(captured.is_set())
        self.assertEqual(result['state'], 'waiting')

    def test_stop_recovers_lease_when_start_response_was_lost(self):
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda: self.host.snapshot()['state'] == 'running')
        self.client.lease_id = ''  # host accepted Start, HTTP response was lost
        self.client.stop()
        self.assertEqual(self.host.snapshot()['state'], 'waiting')
        self.stopped.assert_called_once()

    def test_credentials_survive_restart_and_host_only_saves_hash(self):
        self.client.start(host_ip='127.0.0.1')
        saved = json.loads((self.root/'host/session-host.json').read_text())
        self.assertEqual(saved['peers'][self.client.client_id], hashlib.sha256(self.client.token.encode()).hexdigest())
        self.assertNotIn(self.client.token, json.dumps(saved))
        original = self.client.client_id, self.client.token
        self.client.stop()
        self.client.close()
        reopened = SessionClient(self.root/'client', self.logs.append, port=self.host.port,
                                 address_provider=lambda: [], test_peer=lambda peer: peer == '127.0.0.1')
        self.addCleanup(reopened.close)
        self.assertEqual((reopened.client_id, reopened.token), original)
        if os.name != 'nt':
            self.assertEqual((self.root/'client/session-client.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root/'host/session-host.json').stat().st_mode & 0o777, 0o600)

    def test_http_rejects_browser_unknown_action_and_unbounded_payload(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{self.host.port}'
        for path, headers, body, expected in [
            ('/hello', {}, None, 403),
            ('/hello', {'X-VROSTBridge-Session':'1', 'Origin':''}, None, 403),
            ('/hello', {'X-VROSTBridge-Session':'1', 'Origin':'http://evil.test'}, None, 403),
            ('/command', {'X-VROSTBridge-Session':'1', 'Content-Type':'application/json'}, b'{}', 404),
            ('/pair', {'X-VROSTBridge-Session':'1', 'Content-Type':'application/json'}, b'x'*8193, 400),
            ('/pair', {'X-VROSTBridge-Session':'1', 'Content-Type':'text/plain'}, b'{}', 400),
        ]:
            with self.subTest(path=path, headers=headers):
                with self.assertRaises(urllib.error.HTTPError) as result:
                    opener.open(urllib.request.Request(base+path, headers=headers, data=body), timeout=2)
                self.assertEqual(result.exception.code, expected)
                self.assertIsNone(result.exception.headers.get('Access-Control-Allow-Origin'))
                result.exception.close()

    def test_discovery_is_readonly_and_echoes_random_nonce(self):
        nonce = secrets.token_hex(16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(1)
            probe.sendto(json.dumps({'protocol': PROTOCOL, 'nonce': nonce}).encode(), ('127.0.0.1', self.host.port))
            value = json.loads(probe.recv(1024))
        self.assertEqual(value['nonce'], nonce)
        self.assertEqual(value['host_id'], self.host.host_id)
        self.started.assert_not_called()

    def test_malformed_nested_udp_json_does_not_kill_discovery(self):
        nonce = secrets.token_hex(16)
        original = json.loads
        def parse(value, *args, **kwargs):
            # Nesting thresholds differ between the Linux test interpreter and
            # the Windows packaged interpreter. Exercise the parser exception
            # explicitly instead of relying on one interpreter's exact limit.
            if value == b'['*1024:
                raise RecursionError('JSON nesting limit')
            return original(value, *args, **kwargs)
        with patch('launcher.session_link.json.loads', side_effect=parse):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(1)
                probe.sendto(b'['*1024, ('127.0.0.1', self.host.port))
                probe.sendto(json.dumps({'protocol':PROTOCOL,'nonce':nonce}).encode(), ('127.0.0.1', self.host.port))
                self.assertEqual(json.loads(probe.recv(1024))['nonce'], nonce)
        self.assertTrue(self.host.discovery.is_alive())

    def test_unknown_fields_and_settings_do_not_reach_callback(self):
        owner = self.credentials()
        for fields in ({'url':'http://evil'}, {'settings': {'codec':'H264','bitrate':True}},
                       {'settings': {'codec':'VP9'}}, {'settings': {'bitrate':301}},
                       {'settings': {'pacing_mbps':20}}, {'settings': {'ten_bit':'yes'}},
                       {'settings': {'keep_warm_minutes':500}},
                       {'settings': {'foveation':{'center_size':[.1,.5],'edge_ratio':[2,2]}}},
                       {'settings': {'foveation':{'center_size':[.5,.5],'edge_ratio':[2,2],'shader':'x'}}},
                       {'settings': {'command':'shutdown'}}):
            with self.assertRaises(SessionError):
                self.host.dispatch('/start', dict(owner, request_id=secrets.token_hex(16), **fields), '127.0.0.1')
        self.started.assert_not_called()
        self.assertEqual(session_settings(), {'codec':'H264','bitrate':80,'microphone':False})

    def test_unreachable_stop_clears_heartbeat_and_relies_on_host_deadline(self):
        self.client.start(host_ip='127.0.0.1')
        with patch.object(self.client, '_request', side_effect=OSError('offline')):
            status = self.client.stop()
        self.assertFalse(status['active'])
        self.assertIn('expires', status['message'])


class AddressTests(unittest.TestCase):
    def test_discovery_broadcasts_only_from_actual_physical_interfaces(self):
        rows = [{'address':'192.168.1.7','prefix':24,'physical':True,'connected':True,'interface':'wlan0'},
                {'address':'10.4.3.2','prefix':24,'physical':False,'connected':True}]
        probe = Mock()
        def receive(limit):
            packet = json.loads(probe.sendto.call_args.args[0])
            return json.dumps({'protocol':PROTOCOL,'native_protocol':NATIVE_PROTOCOL,'nonce':packet['nonce'], 'host_id':'a'*32}).encode(), ('192.168.1.8',9946)
        probe.recvfrom.side_effect = receive
        with tempfile.TemporaryDirectory() as directory:
            client = SessionClient(directory, lambda _:None, address_provider=lambda:rows)
            try:
                with patch('launcher.session_link.socket.socket', return_value=probe), patch('launcher.session_link.socket.if_nametoindex',return_value=7), patch('launcher.session_link.select.select', return_value=([probe],[],[])), patch('launcher.session_link.time.monotonic', side_effect=[0,0,.1,2]):
                    host = client.discover()
                probe.bind.assert_called_once_with(('192.168.1.7',9946))
                self.assertEqual(probe.sendto.call_args.args[1],('192.168.1.255',9946))
                self.assertEqual(host['host_ip'],'192.168.1.8')
                probe.close.assert_called_once()
            finally:
                client.close()

    def test_only_private_connected_physical_subnets_are_authorized(self):
        rows = [dict(address=ip, prefix=24, physical=physical, connected=connected)
                for ip, physical, connected in [('192.168.1.7',True,True),('10.4.3.2',True,True),
                                                ('172.25.1.9',False,True),('192.168.8.2',True,False),
                                                ('127.0.0.1',True,True),('8.8.8.8',True,True),
                                                ('169.254.1.5',True,True)]]
        self.assertEqual([row[0] for row in _interfaces(lambda: rows)], ['192.168.1.7','10.4.3.2'])
        with tempfile.TemporaryDirectory() as directory:
            client = SessionClient(directory, lambda _:None, address_provider=lambda:rows)
            self.addCleanup(client.close)
            self.assertTrue(client._allowed('192.168.1.8'))
            for ip in ('192.168.2.8','172.25.1.2','127.0.0.1','192.168.1.255','192.168.1.0','example.com','8.8.8.8'):
                self.assertFalse(client._allowed(ip), ip)


if __name__ == '__main__':
    unittest.main()
