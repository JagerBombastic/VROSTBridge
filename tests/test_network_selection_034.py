"""Actual socket adapter options and stable source selection across startup."""
import json
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from launcher.session_link import (SessionClient,SessionError,SessionVersionError,PROTOCOL,NATIVE_PROTOCOL,
                                   pin_ipv4_interface,_source_connection)

ROWS=[dict(interface='wifi7',address='192.168.77.8',prefix=24,physical=True,connected=True),
      dict(interface='builtin',address='192.168.77.233',prefix=24,physical=True,connected=True)]


class NetworkSelectionTests(unittest.TestCase):
    def client(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        client=SessionClient(temp.name,lambda _:None,address_provider=lambda:ROWS)
        self.addCleanup(client.close)
        return client

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux socket option')
    def test_real_unicast_option_uses_network_byte_order_for_tcp_and_udp(self):
        index=socket.if_nametoindex('lo')
        for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
            with self.subTest(kind=kind),socket.socket(socket.AF_INET,kind) as probe:
                pin_ipv4_interface(probe,'lo')
                self.assertEqual(probe.getsockopt(socket.IPPROTO_IP,50,4),struct.pack('!I',index))

    def test_http_pins_device_and_source_before_connecting(self):
        probe=Mock();order=[]
        probe.setsockopt.side_effect=lambda *args:order.append('interface')
        probe.bind.side_effect=lambda *args:order.append('source')
        probe.connect.side_effect=lambda *args:order.append('connect')
        with patch('launcher.session_link.socket.socket',return_value=probe), \
             patch('launcher.session_link.socket.if_nametoindex',return_value=9):
            result=_source_connection(('192.168.77.29',9946),2,('192.168.77.8',0),'wifi7')
        self.assertIs(result,probe)
        self.assertEqual(order,['interface','source','connect'])
        probe.setsockopt.assert_called_once_with(socket.IPPROTO_IP,50,b'\0\0\0\x09')
        probe.bind.assert_called_once_with(('192.168.77.8',0))

    def test_unusable_adapter_never_connects_using_an_unpinned_socket(self):
        probe=Mock();probe.setsockopt.side_effect=OSError('No such device')
        with patch('launcher.session_link.socket.socket',return_value=probe), \
             patch('launcher.session_link.socket.if_nametoindex',return_value=9):
            with self.assertRaises(OSError):
                _source_connection(('192.168.77.29',9946),2,('192.168.77.8',0),'wifi7')
        probe.connect.assert_not_called();probe.close.assert_called_once_with()

    def test_automatic_route_must_use_an_eligible_physical_address(self):
        client=self.client();probe=Mock()
        probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
        probe.getsockname.return_value=('10.9.0.2',5000)
        with patch('launcher.session_link.socket.socket',return_value=probe),self.assertRaisesRegex(SessionError,'physical'):
            client._source_for('192.168.77.29')

    def test_preparation_hands_the_same_source_and_adapter_to_native(self):
        client=self.client();client.select_interface('wifi7')
        client.discover=Mock(return_value=dict(host_ip='192.168.77.29',host_id='a'*32,source_ip='192.168.77.8'))
        env=client.prepare_network()
        self.assertEqual(env,{'VROSTBRIDGE_BIND_IP':'192.168.77.8','VROSTBRIDGE_INTERFACE':'wifi7'})
        self.assertEqual(client.network_status()['source_interface'],'wifi7')
        # Preparation was handed to native: a failed hello must not silently
        # switch adapters through a second round of automatic discovery.
        client.discover=Mock(side_effect=SessionError('Selected adapter disconnected'))
        with self.assertRaisesRegex(SessionError,'disconnected'):client.start()
        client.discover.assert_called_once_with('192.168.77.29',cancel=None)

    def test_cancel_after_pairing_prevents_host_start_request(self):
        client=self.client();cancel=threading.Event();paths=[]
        client.discover=Mock(return_value=dict(host_ip='192.168.77.29',host_id='a'*32,source_ip='192.168.77.8'))
        def request(ip,path,*args,**kwargs):
            paths.append(path)
            if path=='/pair':cancel.set()
            return {'host_id':'a'*32}
        client._request=request
        with self.assertRaisesRegex(SessionError,'cancelled'):client.start(cancel=cancel)
        self.assertEqual(paths,['/pair'])

    def test_udp_native_mismatch_is_rejected_before_host_start(self):
        client=self.client();client.select_interface('wifi7');probe=Mock()
        def receive(limit):
            request=json.loads(probe.sendto.call_args.args[0])
            return json.dumps(dict(protocol=PROTOCOL,native_protocol='old',nonce=request['nonce'],host_id='a'*32)).encode(),('192.168.77.29',9946)
        probe.recvfrom.side_effect=receive
        with patch('launcher.session_link.socket.socket',return_value=probe), \
             patch('launcher.session_link.socket.if_nametoindex',return_value=9), \
             patch('launcher.session_link.select.select',return_value=([probe],[],[])), \
             patch('launcher.session_link.time.monotonic',side_effect=[0,0,.1,2]):
            with self.assertRaisesRegex(SessionError,'Install this release on both PCs'):client.discover()
        probe.close.assert_called_once_with()

    def test_cached_host_version_error_is_not_hidden_by_discovery_failure(self):
        client=self.client();client.host_ip='192.168.77.29'
        client._request=Mock(side_effect=SessionVersionError('Install this release on both PCs'))
        with patch('launcher.session_link.socket.socket') as probe:
            with self.assertRaisesRegex(SessionVersionError,'both PCs'):client.discover()
        probe.assert_not_called()
