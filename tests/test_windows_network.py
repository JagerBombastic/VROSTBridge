"""Windows network support for the client companion (launcher/windows_network.py and the Windows
branches of the client's network modules), tested on Linux: structure layouts against the Win64
headers, the parsers against Windows' memory layouts, and every module against a fake Windows API."""
import copy
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from launcher import windows_network as wn
from launcher import (client_identity, client_network, connection_kind, network_path, network_test, session_link,
                      wake_host, wifi_diagnostics, wifi_others, wifi_roaming, wifi_session)

WIFI = '{11111111-2222-3333-4444-555555555555}'
WIFI_USB = '{66666666-7777-8888-9999-AAAAAAAAAAAA}'
ETHERNET = '{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}'
VIRTUAL = '{12345678-1234-1234-1234-123456789ABC}'
LOOPBACK = '{00000000-0000-0000-0000-000000000001}'
BSSID = '02:11:22:33:44:55'
OTHER_BSSID = '02:11:22:33:44:66'


def raw_adapter(alias, guid, index, if_type=wn.IF_TYPE_IEEE80211, oper=1, ipv4=(('192.168.1.25', 24),),
                physical=True, pnp='PCI\\VEN_8086&DEV_272B\\4&1', speed=2_882_000_000, media=True, gateways=('192.168.1.1',)):
    """One row as NativeApi.adapters() returns it."""
    return {'index': index, 'luid': index * 1000, 'guid': guid, 'alias': alias, 'description': alias + ' adapter',
            'if_type': if_type, 'oper_status': oper, 'mac': '02:00:00:00:00:%02x' % index, 'mtu': 1500,
            'transmit_bps': speed, 'receive_bps': speed, 'connection_type': 1, 'tunnel_type': 0,
            'ipv4': [{'address': address, 'prefix': prefix, 'dad_state': 4} for address, prefix in ipv4],
            'gateways': list(gateways), 'physical': physical, 'hardware': physical, 'media_connected': media,
            'admin_up': True, 'physical_medium': 9 if if_type == wn.IF_TYPE_IEEE80211 else 14, 'pnp_id': pnp}


def standard_adapters():
    return [raw_adapter('Wi-Fi 2', WIFI, 12),
            raw_adapter('Ethernet 3', ETHERNET, 7, wn.IF_TYPE_ETHERNET_CSMACD, ipv4=(('192.168.1.30', 24),),
                        pnp='USB\\VID_0BDA&PID_8156\\000001', speed=1_000_000_000),
            raw_adapter('Wi-Fi', WIFI_USB, 15, ipv4=(('192.168.1.40', 24), ('169.254.7.8', 16)),
                        pnp='USB\\VID_0E8D&PID_7961\\00e04c', speed=866_000_000),
            raw_adapter('vEthernet (Default Switch)', VIRTUAL, 30, wn.IF_TYPE_ETHERNET_CSMACD,
                        ipv4=(('172.20.0.1', 20),), physical=False, pnp='ROOT\\VMS_MP\\0000', speed=10_000_000_000),
            raw_adapter('Loopback Pseudo-Interface 1', LOOPBACK, 1, wn.IF_TYPE_SOFTWARE_LOOPBACK,
                        ipv4=(('127.0.0.1', 8),), physical=False, pnp='')]


def wide_units(text):
    data = text.encode('utf-16-le')
    return list(struct.unpack('<%dH' % (len(data) // 2), data))


def connection_bytes(ssid=b'Home', bssid=BSSID, phy=10, quality=80, rx_kbps=1_200_000, tx_kbps=960_000,
                     profile='Home', state=1):
    value = wn.WLAN_CONNECTION_ATTRIBUTES()
    value.isState = state
    units = wide_units(profile)
    value.strProfileName[:len(units)] = units
    association = value.wlanAssociationAttributes
    association.dot11Ssid.uSSIDLength = len(ssid)
    association.dot11Ssid.ucSSID[:len(ssid)] = list(ssid)
    association.dot11Bssid[:] = list(bytes.fromhex(bssid.replace(':', '')))
    association.dot11PhyType, association.wlanSignalQuality = phy, quality
    association.ulRxRate, association.ulTxRate = rx_kbps, tx_kbps
    value.wlanSecurityAttributes.bSecurityEnabled = 1
    return bytes(value)


def realtime_bytes(links, phy=11, quality=90, rx_kbps=2_400_000, tx_kbps=2_000_000, mlo=None):
    head = wn.WLAN_REALTIME_CONNECTION_QUALITY(dot11PhyType=phy, ulLinkQuality=quality, ulRxRate=rx_kbps,
                                               ulTxRate=tx_kbps, bIsMLOConnection=int(len(links) > 1 if mlo is None else mlo),
                                               ulNumLinks=len(links))
    raw = bytes(head)[:wn.WLAN_REALTIME_CONNECTION_QUALITY.linksInfo.offset]
    for link_id, frequency, bandwidth, rssi in links:
        raw += bytes(wn.WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO(ucLinkID=link_id, ulChannelCenterFrequencyMhz=frequency,
                                                                  ulBandwidth=bandwidth, lRssi=rssi))
    return raw


def statistics_bytes(*phys):
    head = wn.WLAN_STATISTICS(ullFourWayHandshakeFailures=1, dwNumberOfPhys=len(phys))
    raw = bytes(head)[:wn.WLAN_STATISTICS.PhyCounters.offset]
    for transmitted, retries, failed, received in phys:
        raw += bytes(wn.WLAN_PHY_FRAME_STATISTICS(ullTransmittedFrameCount=transmitted, ullRetryCount=retries,
                                                  ullFailedCount=failed, ullReceivedFrameCount=received))
    return raw


def bss_bytes(*entries):
    raw = b''
    for bssid, khz in entries:
        entry = wn.WLAN_BSS_ENTRY(ulChCenterFrequency=khz, lRssi=-60, uLinkQuality=80, dot11BssPhyType=10)
        entry.dot11Bssid[:] = list(bytes.fromhex(bssid.replace(':', '')))
        raw += bytes(entry)
    offset = wn.WLAN_BSS_LIST.wlanBssEntries.offset
    return struct.pack('<II', offset + len(raw), len(entries)) + b'\0' * (offset - 8) + raw


def long_bytes(value):
    return struct.pack('<i', value)


def ulong_bytes(value):
    return struct.pack('<I', value)


class FakeApi:
    """NativeApi's surface with a small model of Windows behind it."""

    DEFAULTS = {wn.OPCODE_MEDIA_STREAMING: False, wn.OPCODE_BACKGROUND_SCAN: True}

    def __init__(self, adapters=None):
        self.adapter_rows = standard_adapters() if adapters is None else adapters
        self.adapter_calls = 0
        self.adapter_error = None
        self.interfaces = [{'guid': WIFI, 'description': 'Intel(R) Wi-Fi 7 BE200', 'state': 1},
                           {'guid': WIFI_USB, 'description': 'MediaTek USB Wi-Fi', 'state': 1}]
        self.queries = {}         # (GUID, opcode) -> bytes, or an int Windows error code
        self.options = {}         # (GUID, opcode) -> (handle, value): who holds a session setting
        self.set_codes = {}       # (GUID, opcode) -> error code WlanSetInterface returns
        self.handles, self.closed, self.opened = set(), [], 0
        self.open_error = None
        self.route, self.route_calls = None, []
        self.neighbour_rows, self.neighbour_error = [], None
        self.tcp, self.udp = [], []
        self.bss = {}
        self.disconnects, self.connects = [], []
        self.disconnect_code = self.connect_code = 0
        self.power, self.battery = (0, 2), False

    # IP Helper
    def adapters(self):
        self.adapter_calls += 1
        if self.adapter_error:
            raise self.adapter_error
        return copy.deepcopy(self.adapter_rows)

    def best_route(self, destination, source=None, interface_index=0):
        self.route_calls.append((destination, source, interface_index))
        return dict(self.route) if self.route else None

    def neighbours(self):
        if self.neighbour_error:
            raise self.neighbour_error
        return [dict(row) for row in self.neighbour_rows]

    def tcp_table(self):
        return [dict(row) for row in self.tcp]

    def udp_table(self):
        return [dict(row) for row in self.udp]

    # Native Wifi
    def wlan_open(self):
        if self.open_error:
            raise self.open_error
        self.opened += 1
        handle = 1000 + self.opened
        self.handles.add(handle)
        return handle

    def wlan_close(self, handle):
        self.handles.discard(handle)
        self.closed.append(handle)
        for key, (holder, _) in list(self.options.items()):
            if holder == handle:  # Windows drops a client's settings with its handle
                del self.options[key]

    def wlan_interfaces(self, handle):
        assert handle in self.handles
        return [dict(item) for item in self.interfaces]

    def wlan_query(self, handle, guid, opcode):
        assert handle in self.handles
        key = (guid.upper(), opcode)
        if opcode in self.DEFAULTS:
            return long_bytes(int(self.options.get(key, (None, self.DEFAULTS[opcode]))[1]))
        value = self.queries.get(key)
        if value is None:
            raise wn.WindowsNetworkError('not supported', wn.ERROR_INVALID_PARAMETER)
        if isinstance(value, int):
            raise wn.WindowsNetworkError(wn.reason(value), value)
        return value

    def wlan_set(self, handle, guid, opcode, value):
        assert handle in self.handles
        code = self.set_codes.get((guid.upper(), opcode), 0)
        if code == 0:
            self.options[(guid.upper(), opcode)] = (handle, bool(value))
        return code

    def drop(self, guid, opcode):
        """Wi-Fi disconnected and came back: Windows forgot the session setting."""
        self.options.pop((guid.upper(), opcode), None)

    def wlan_bss_list(self, handle, guid):
        assert handle in self.handles
        if guid.upper() not in self.bss:
            raise wn.WindowsNetworkError(wn.reason(wn.ERROR_ACCESS_DENIED), wn.ERROR_ACCESS_DENIED)
        return self.bss[guid.upper()]

    def wlan_disconnect(self, handle, guid):
        assert handle in self.handles
        self.disconnects.append(guid)
        if self.disconnect_code == 0:
            for item in self.interfaces:
                if wn.same_guid(item['guid'], guid):
                    item['state'] = 4
        return self.disconnect_code

    def wlan_connect(self, handle, guid, profile):
        assert handle in self.handles
        self.connects.append((guid, profile))
        return self.connect_code

    # power plan
    def power_values(self, subgroup, setting):
        assert (subgroup, setting) == (wn.WIFI_POWER_SUBGROUP, wn.WIFI_POWER_SETTING)
        if isinstance(self.power, Exception):
            raise self.power
        return self.power

    def on_battery(self):
        return self.battery


def wifi_queries(fake, guid=WIFI, connection=True, realtime=True, rssi=-52, channel=37, statistics=None):
    if connection:
        fake.queries[(guid, wn.OPCODE_CURRENT_CONNECTION)] = connection_bytes() if connection is True else connection
    if realtime:
        fake.queries[(guid, wn.OPCODE_REALTIME_QUALITY)] = realtime_bytes([(0, 6135, 320, -52)]) if realtime is True else realtime
    if rssi is not None:
        fake.queries[(guid, wn.OPCODE_RSSI)] = long_bytes(rssi)
    if channel is not None:
        fake.queries[(guid, wn.OPCODE_CHANNEL_NUMBER)] = ulong_bytes(channel)
    if statistics is not None:
        fake.queries[(guid, wn.OPCODE_STATISTICS)] = statistics


REAL_OPTION = wn.WlanSessionOption


class QuietOption(wn.WlanSessionOption):
    """The session option without its keeper thread (tests call keep() themselves)."""

    def __init__(self, guid, opcode, value, api=None, interval=5.0, start=True):
        super().__init__(guid, opcode, value, api=api, interval=interval, start=False)


def completed(arguments, code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(arguments, code, stdout, stderr)


class WindowsCase(unittest.TestCase):
    """sys.platform is 'win32' and windows_network talks to a FakeApi."""

    def setUp(self):
        self.fake = FakeApi()
        for patcher in (mock.patch.object(sys, 'platform', 'win32'), mock.patch.object(wn, '_api', self.fake),
                        mock.patch.object(wn, 'WlanSessionOption', QuietOption)):
            patcher.start()
            self.addCleanup(patcher.stop)
        wn.invalidate()
        self.addCleanup(wn.invalidate)
        self.lines = []


# ------------------------------------------------------------------------------------------- layouts
class LayoutTests(unittest.TestCase):
    # Win64 sizes from the Windows SDK headers (fixed-size ctypes types make them the same on any host).
    SIZES = {'GUID': 16, 'SOCKADDR_IN': 16, 'SOCKADDR_IN6': 28, 'SOCKADDR_INET': 28, 'SOCKET_ADDRESS': 16,
             'IP_ADAPTER_UNICAST_ADDRESS': 64, 'IP_ADAPTER_GATEWAY_ADDRESS': 32, 'IP_ADAPTER_ADDRESSES': 448,
             'MIB_IF_ROW2': 1352, 'IP_ADDRESS_PREFIX': 32, 'MIB_IPFORWARD_ROW2': 104, 'MIB_IPNET_ROW2': 88,
             'MIB_TCPROW_OWNER_PID': 24, 'MIB_UDPROW_OWNER_PID': 12, 'WLAN_INTERFACE_INFO': 532,
             'WLAN_INTERFACE_INFO_LIST': 540, 'DOT11_SSID': 36, 'WLAN_ASSOCIATION_ATTRIBUTES': 68,
             'WLAN_SECURITY_ATTRIBUTES': 16, 'WLAN_CONNECTION_ATTRIBUTES': 604, 'WLAN_MAC_FRAME_STATISTICS': 96,
             'WLAN_PHY_FRAME_STATISTICS': 144, 'WLAN_STATISTICS': 368, 'WLAN_RATE_SET': 256, 'WLAN_BSS_ENTRY': 360,
             'WLAN_BSS_LIST': 368, 'WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO': 272,
             'WLAN_REALTIME_CONNECTION_QUALITY': 296, 'WLAN_CONNECTION_PARAMETERS': 40, 'SYSTEM_POWER_STATUS': 12}

    def test_structure_sizes_match_win64(self):
        for name, size in self.SIZES.items():
            with self.subTest(name):
                self.assertEqual(ctypes.sizeof(getattr(wn, name)), size)

    def test_field_offsets_match_the_headers(self):
        for structure, field, offset in (
                (wn.IP_ADAPTER_ADDRESSES, 'FriendlyName', 72), (wn.IP_ADAPTER_ADDRESSES, 'IfType', 100),
                (wn.IP_ADAPTER_ADDRESSES, 'OperStatus', 104), (wn.IP_ADAPTER_ADDRESSES, 'TransmitLinkSpeed', 184),
                (wn.IP_ADAPTER_ADDRESSES, 'FirstGatewayAddress', 208), (wn.IP_ADAPTER_ADDRESSES, 'Luid', 224),
                (wn.IP_ADAPTER_ADDRESSES, 'Dhcpv6Server', 280), (wn.IP_ADAPTER_ADDRESSES, 'FirstDnsSuffix', 440),
                (wn.IP_ADAPTER_UNICAST_ADDRESS, 'Address', 16), (wn.IP_ADAPTER_UNICAST_ADDRESS, 'OnLinkPrefixLength', 56),
                (wn.MIB_IF_ROW2, 'InterfaceGuid', 12), (wn.MIB_IF_ROW2, 'PhysicalAddressLength', 1056),
                (wn.MIB_IF_ROW2, 'PhysicalMediumType', 1140), (wn.MIB_IF_ROW2, 'InterfaceAndOperStatusFlags', 1152),
                (wn.MIB_IF_ROW2, 'OperStatus', 1156), (wn.MIB_IF_ROW2, 'MediaConnectState', 1164),
                (wn.MIB_IF_ROW2, 'TransmitLinkSpeed', 1192), (wn.MIB_IPFORWARD_ROW2, 'DestinationPrefix', 12),
                (wn.MIB_IPFORWARD_ROW2, 'NextHop', 44), (wn.MIB_IPFORWARD_ROW2, 'Metric', 84),
                (wn.MIB_IPNET_ROW2, 'InterfaceLuid', 32), (wn.MIB_IPNET_ROW2, 'State', 76),
                (wn.MIB_IPNET_TABLE2, 'Table', 8), (wn.MIB_TCPTABLE_OWNER_PID, 'table', 4),
                (wn.MIB_UDPTABLE_OWNER_PID, 'table', 4), (wn.WLAN_INTERFACE_INFO_LIST, 'InterfaceInfo', 8),
                (wn.WLAN_CONNECTION_ATTRIBUTES, 'wlanAssociationAttributes', 520),
                (wn.WLAN_ASSOCIATION_ATTRIBUTES, 'dot11PhyType', 48), (wn.WLAN_STATISTICS, 'PhyCounters', 224),
                (wn.WLAN_BSS_LIST, 'wlanBssEntries', 8), (wn.WLAN_BSS_ENTRY, 'ullTimestamp', 72),
                (wn.WLAN_BSS_ENTRY, 'ulChCenterFrequency', 92), (wn.WLAN_REALTIME_CONNECTION_QUALITY, 'linksInfo', 24),
                (wn.WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO, 'ulChannelCenterFrequencyMhz', 4),
                (wn.WLAN_CONNECTION_PARAMETERS, 'strProfile', 8)):
            with self.subTest(structure=structure.__name__, field=field):
                self.assertEqual(getattr(structure, field).offset, offset)

    def test_opcodes_and_power_guids(self):
        self.assertEqual((wn.OPCODE_BACKGROUND_SCAN, wn.OPCODE_MEDIA_STREAMING, wn.OPCODE_CURRENT_CONNECTION,
                          wn.OPCODE_CHANNEL_NUMBER, wn.OPCODE_REALTIME_QUALITY), (2, 3, 7, 8, 19))
        self.assertEqual((wn.OPCODE_STATISTICS, wn.OPCODE_RSSI), (0x10000101, 0x10000102))
        self.assertEqual(wn.WIFI_POWER_SUBGROUP, '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1')
        self.assertEqual(wn.WIFI_POWER_SETTING, '12bbebe6-58d6-4636-95bb-3217ef867c1a')
        self.assertEqual(session_link.WINDOWS_IP_UNICAST_IF, 31)

    def test_every_prototype_names_a_loaded_library(self):
        self.assertEqual({library for library, _ in wn._PROTOTYPES}, {'iphlpapi', 'wlanapi', 'powrprof', 'kernel32'})

    @unittest.skipIf(hasattr(ctypes, 'WinDLL'), 'a real Windows answers')
    def test_importing_never_needs_windows_and_calls_fail_soft(self):
        api = wn.NativeApi()
        for call in (api.adapters, api.neighbours, api.tcp_table, api.wlan_open, api.on_battery):
            with self.subTest(call=call.__name__), self.assertRaises(wn.WindowsNetworkError):
                call()
        with mock.patch.object(wn, '_api', api):
            wn.invalidate()
            with self.assertRaises(wn.WindowsNetworkError):
                wn.adapters()
            self.assertIsNone(wn.interface_index('Wi-Fi 2'))
            self.assertIn('only available on Windows', wn.wifi_link('Wi-Fi 2')['reason'])
        wn.invalidate()


# ------------------------------------------------------------------------------------------- parsers
class ParserTests(unittest.TestCase):
    def setUp(self):
        self.keep = []

    def wide(self, text):
        units = wide_units(text)
        array = (ctypes.c_uint16 * (len(units) + 1))(*units)
        self.keep.append(array)
        return ctypes.addressof(array)

    def sockaddr(self, ip):
        value = wn.SOCKADDR_IN(sin_family=socket.AF_INET)
        value.sin_addr[:] = list(socket.inet_aton(ip))
        self.keep.append(value)
        return ctypes.addressof(value)

    def unicast(self, pointer, prefix, following=None, length=16, dad=4):
        value = wn.IP_ADAPTER_UNICAST_ADDRESS(Length=64, OnLinkPrefixLength=prefix, DadState=dad, Next=following)
        value.Address.lpSockaddr, value.Address.iSockaddrLength = pointer, length
        self.keep.append(value)
        return ctypes.addressof(value)

    def test_walks_the_adapter_list_as_windows_lays_it_out(self):
        ipv6 = wn.SOCKADDR_IN6(sin6_family=23)
        self.keep.append(ipv6)
        link_local = self.unicast(self.sockaddr('169.254.3.4'), 16)
        lan = self.unicast(self.sockaddr('192.168.1.25'), 24, link_local)
        first_entry = self.unicast(ctypes.addressof(ipv6), 64, lan, length=28)
        gateway = wn.IP_ADAPTER_GATEWAY_ADDRESS(Length=32)
        gateway.Address.lpSockaddr, gateway.Address.iSockaddrLength = self.sockaddr('192.168.1.1'), 16
        name = ctypes.create_string_buffer(WIFI.encode())
        second = wn.IP_ADAPTER_ADDRESSES(Length=448, IfIndex=7, IfType=6, OperStatus=2,
                                         FriendlyName=self.wide('イーサネット 2'), Luid=0x6000000000007)
        first = wn.IP_ADAPTER_ADDRESSES(Length=448, IfIndex=12, Next=ctypes.addressof(second),
                                        AdapterName=ctypes.addressof(name), FirstUnicastAddress=first_entry,
                                        FirstGatewayAddress=ctypes.addressof(gateway), FriendlyName=self.wide('Wi-Fi 2'),
                                        Description=self.wide('Intel(R) Wi-Fi 7 BE200 320MHz'), PhysicalAddressLength=6,
                                        IfType=71, OperStatus=1, TransmitLinkSpeed=2_882_000_000,
                                        ReceiveLinkSpeed=2_401_000_000, Luid=0x47000000000012, Mtu=1500)
        first.PhysicalAddress[:6] = [2, 0x11, 0x22, 0x33, 0x44, 0x55]
        self.keep += [gateway, name, second, first]
        rows = wn.walk_adapter_addresses(ctypes.addressof(first))
        self.assertEqual(len(rows), 2)
        wifi, ethernet = rows
        self.assertEqual((wifi['index'], wifi['guid'], wifi['alias'], wifi['description'], wifi['mac']),
                         (12, WIFI, 'Wi-Fi 2', 'Intel(R) Wi-Fi 7 BE200 320MHz', '02:11:22:33:44:55'))
        self.assertEqual((wifi['if_type'], wifi['oper_status'], wifi['transmit_bps'], wifi['receive_bps'], wifi['luid']),
                         (71, 1, 2_882_000_000, 2_401_000_000, 0x47000000000012))
        self.assertEqual(wifi['ipv4'], [{'address': '192.168.1.25', 'prefix': 24, 'dad_state': 4},
                                        {'address': '169.254.3.4', 'prefix': 16, 'dad_state': 4}])
        self.assertEqual(wifi['gateways'], ['192.168.1.1'])
        self.assertEqual((ethernet['alias'], ethernet['guid'], ethernet['ipv4'], ethernet['mac']), ('イーサネット 2', '', [], ''))

    def test_interface_facts_come_from_the_flag_byte(self):
        row = wn.MIB_IF_ROW2(InterfaceAndOperStatusFlags=wn.IF_FLAG_HARDWARE | wn.IF_FLAG_CONNECTOR_PRESENT,
                             MediaConnectState=1, AdminStatus=1, PhysicalMediumType=9)
        self.assertEqual(wn.parse_if_row(row), {'physical': True, 'hardware': True, 'media_connected': True,
                                                'admin_up': True, 'physical_medium': 9})
        row = wn.MIB_IF_ROW2(InterfaceAndOperStatusFlags=wn.IF_FLAG_NOT_MEDIA_CONNECTED, MediaConnectState=2, AdminStatus=2)
        self.assertEqual((wn.parse_if_row(row)['physical'], wn.parse_if_row(row)['media_connected'],
                          wn.parse_if_row(row)['admin_up']), (False, False, False))
        self.assertIsNone(wn.parse_if_row(wn.MIB_IF_ROW2(MediaConnectState=0))['media_connected'])

    def test_neighbour_table_keeps_ipv4_rows_with_linux_state_names(self):
        count = 3
        size = wn.MIB_IPNET_TABLE2.Table.offset + count * ctypes.sizeof(wn.MIB_IPNET_ROW2)
        buffer = (ctypes.c_uint8 * size)()
        ctypes.c_uint32.from_address(ctypes.addressof(buffer)).value = count
        base = ctypes.addressof(buffer) + wn.MIB_IPNET_TABLE2.Table.offset
        for index, (family, ip, mac, state) in enumerate(((2, '192.168.1.10', 'aabbccddeeff', 5),
                                                          (23, '', '', 5), (2, '192.168.1.11', '010203', 1))):
            row = wn.MIB_IPNET_ROW2.from_address(base + index * ctypes.sizeof(wn.MIB_IPNET_ROW2))
            row.Address.si_family, row.State, row.InterfaceIndex = family, state, 7
            if ip:
                row.Address.Ipv4.sin_addr[:] = list(socket.inet_aton(ip))
            data = bytes.fromhex(mac)
            row.PhysicalAddress[:len(data)], row.PhysicalAddressLength = list(data), len(data)
        self.assertEqual(wn.parse_ipnet_table(ctypes.addressof(buffer)), [
            {'address': '192.168.1.10', 'mac': 'aa:bb:cc:dd:ee:ff', 'state': 'REACHABLE', 'index': 7, 'router': False,
             'unreachable': False},
            {'address': '192.168.1.11', 'mac': '', 'state': 'INCOMPLETE', 'index': 7, 'router': False, 'unreachable': False}])

    def test_owner_tables_decode_network_byte_order(self):
        def address(ip):
            return struct.unpack('<I', socket.inet_aton(ip))[0]

        def port(number):
            return struct.unpack('<H', struct.pack('>H', number))[0]
        tcp = wn.MIB_TCPROW_OWNER_PID(dwState=5, dwLocalAddr=address('192.168.1.25'), dwLocalPort=port(9943),
                                      dwRemoteAddr=address('192.168.1.10'), dwRemotePort=port(50123), dwOwningPid=4242)
        raw = struct.pack('<I', 1) + bytes(tcp)
        self.assertEqual(wn.parse_tcp_table(raw), [{'local_ip': '192.168.1.25', 'local_port': 9943, 'peer_ip': '192.168.1.10',
                                                    'peer_port': 50123, 'state': 5, 'pid': 4242}])
        udp = wn.MIB_UDPROW_OWNER_PID(dwLocalAddr=address('0.0.0.0'), dwLocalPort=port(9944), dwOwningPid=4242)
        self.assertEqual(wn.parse_udp_table(struct.pack('<I', 5) + bytes(udp)),
                         [{'local_ip': '0.0.0.0', 'local_port': 9944, 'pid': 4242}])  # count bounded by the buffer
        self.assertEqual(wn.parse_tcp_table(b''), [])

    def test_route_row(self):
        row = wn.MIB_IPFORWARD_ROW2(InterfaceIndex=12, InterfaceLuid=99, Metric=35)
        row.DestinationPrefix.Prefix.Ipv4.sin_family = 2
        row.DestinationPrefix.PrefixLength = 0
        row.NextHop.Ipv4.sin_family = 2
        row.NextHop.Ipv4.sin_addr[:] = list(socket.inet_aton('192.168.1.1'))
        source = wn._sockaddr_inet('192.168.1.25')
        self.assertEqual(wn.route_from_row(row, source), {'index': 12, 'luid': 99, 'gateway': '192.168.1.1',
                                                          'source': '192.168.1.25', 'destination_prefix': '0.0.0.0/0',
                                                          'metric': 35})
        row.NextHop.Ipv4.sin_addr[:] = [0, 0, 0, 0]
        self.assertEqual(wn.route_from_row(row, source)['gateway'], '')

    def test_wlan_interface_list(self):
        size = wn.WLAN_INTERFACE_INFO_LIST.InterfaceInfo.offset + 2 * ctypes.sizeof(wn.WLAN_INTERFACE_INFO)
        buffer = (ctypes.c_uint8 * size)()
        header = wn.WLAN_INTERFACE_INFO_LIST.from_address(ctypes.addressof(buffer))
        header.dwNumberOfItems = 2
        base = ctypes.addressof(buffer) + wn.WLAN_INTERFACE_INFO_LIST.InterfaceInfo.offset
        for index, (guid, text, state) in enumerate(((WIFI, 'Intel(R) Wi-Fi 7', 1), (WIFI_USB, 'USB Wi-Fi', 4))):
            info = wn.WLAN_INTERFACE_INFO.from_address(base + index * ctypes.sizeof(wn.WLAN_INTERFACE_INFO))
            info.InterfaceGuid = wn.GUID.from_text(guid)
            units = wide_units(text)
            info.strInterfaceDescription[:len(units)] = units
            info.isState = state
        self.assertEqual(wn.parse_interface_list(ctypes.addressof(buffer)),
                         [{'guid': WIFI, 'description': 'Intel(R) Wi-Fi 7', 'state': 1},
                          {'guid': WIFI_USB, 'description': 'USB Wi-Fi', 'state': 4}])

    def test_guid_text_round_trip_uses_the_windows_memory_layout(self):
        guid = wn.GUID.from_text(WIFI)
        self.assertEqual((guid.Data1, guid.Data2, guid.Data3, bytes(guid.Data4)),
                         (0x11111111, 0x2222, 0x3333, bytes.fromhex('4444555555555555')))
        self.assertEqual(guid.text(), WIFI)
        self.assertTrue(wn.same_guid(WIFI.lower(), WIFI.strip('{}')))
        self.assertFalse(wn.same_guid(WIFI, 'not a guid'))

    def test_connection_attributes(self):
        value = wn.parse_connection_attributes(connection_bytes(ssid='Café'.encode(), phy=11, quality=64,
                                                                rx_kbps=2_882_000, tx_kbps=2_401_000, profile='Café 6E'))
        self.assertEqual(value, {'state': 1, 'profile': 'Café 6E', 'ssid': 'Café', 'bssid': BSSID, 'phy': '802.11be',
                                 'signal_quality': 64, 'rx_kbps': 2_882_000, 'tx_kbps': 2_401_000, 'security': True,
                                 'enterprise': False})
        with self.assertRaises(wn.WindowsNetworkError):
            wn.parse_connection_attributes(b'\0' * 100)

    def test_realtime_quality_accepts_mhz_or_khz_and_several_links(self):
        value = wn.parse_realtime_quality(realtime_bytes([(0, 2437, 20, -61), (1, 5_180_000, 80, -55),
                                                          (2, 6135, 320, -49)]))
        self.assertEqual((value['phy'], value['signal_quality'], value['mlo'], value['rx_kbps']),
                         ('802.11be', 90, True, 2_400_000))
        self.assertEqual([(link['link_id'], link['freq_mhz'], link['band'], link['signal_dbm']) for link in value['links']],
                         [(0, 2437, '2.4 GHz', -61), (1, 5180, '5 GHz', -55), (2, 6135, '6 GHz', -49)])
        single = wn.parse_realtime_quality(realtime_bytes([(0, 0, 0, 0)]))
        self.assertEqual((single['mlo'], single['links'][0]['freq_mhz'], single['links'][0]['signal_dbm']), (False, None, None))
        truncated = realtime_bytes([(0, 2437, 20, -61), (1, 5180, 80, -55)])[:-10]  # the second link is cut off
        self.assertEqual(len(wn.parse_realtime_quality(truncated)['links']), 1)
        with self.assertRaises(wn.WindowsNetworkError):
            wn.parse_realtime_quality(b'\0' * 8)

    def test_statistics_sum_the_phys_and_ignore_an_empty_report(self):
        self.assertEqual(wn.parse_statistics(statistics_bytes((1000, 50, 2, 3000), (10, 1, 0, 20))),
                         {'tx_packets': 1010, 'tx_retries': 51, 'tx_failed': 2, 'rx_packets': 3020, 'ack_failures': 0,
                          'fcs_errors': 0})
        self.assertEqual(wn.parse_statistics(statistics_bytes((0, 0, 0, 0))), {})

    def test_bss_list(self):
        rows = wn.parse_bss_list(bss_bytes((BSSID, 5_955_000), (OTHER_BSSID, 2_412_000)))
        self.assertEqual([(row['bssid'], row['freq_mhz'], row['band']) for row in rows],
                         [(BSSID, 5955, '6 GHz'), (OTHER_BSSID, 2412, '2.4 GHz')])
        self.assertEqual(wn.parse_bss_list(b''), [])

    def test_scalar_values(self):
        self.assertTrue(wn.parse_bool(long_bytes(1)))
        self.assertFalse(wn.parse_bool(long_bytes(0)))
        self.assertEqual((wn.parse_long(long_bytes(-67)), wn.parse_ulong(ulong_bytes(149))), (-67, 149))
        for parser in (wn.parse_bool, wn.parse_long, wn.parse_ulong):
            with self.assertRaises(wn.WindowsNetworkError):
                parser(b'\0')

    def test_bands_from_frequency_and_channel(self):
        self.assertEqual([wn.frequency_mhz(value) for value in (2_437_000, 5180, 7_115_000, 0, 900, 9_000_000)],
                         [2437, 5180, 7115, None, None, None])
        for channel, phy, expected in ((6, '802.11n', '2.4 GHz'), (6, '802.11ax', '2.4 GHz'), (1, '802.11ax', ''),
                                       (1, '802.11g', '2.4 GHz'), (36, '802.11ax', '5 GHz'), (149, '802.11ac', '5 GHz'),
                                       (149, '802.11ax', ''), (37, '802.11ax', '6 GHz'), (181, '802.11be', '6 GHz'),
                                       (233, '802.11be', '6 GHz'), (0, '', ''), (300, '', '')):
            with self.subTest(channel=channel, phy=phy):
                self.assertEqual(wn.band_from_channel(channel, phy), expected)
        self.assertEqual([wn.quality_dbm(value) for value in (0, 50, 100, 101, None)], [-100, -75, -50, None, None])

    def test_adapter_aliases(self):
        for alias in ('Wi-Fi', 'Wi-Fi 2', 'Ethernet 3', 'WLAN', 'vEthernet (Default Switch)', 'Local Area Connection* 1',
                      'イーサネット 2', 'Беспроводная сеть', "Bob's Wi-Fi", 'wlp1s0'):
            self.assertTrue(wn.valid_alias(alias), alias)
        for alias in ('', ' Wi-Fi', 'Wi-Fi ', 'Wi-Fi\n2', 'a"b', 'a\\b', 'a/b', 'a|b', 'a;b', '$(x)', '%PATH%', 'a<b',
                      'x' * 129, None, 7):
            self.assertFalse(wn.valid_alias(alias), alias)


# ------------------------------------------------------------------------------------------- windows_network helpers
class AdapterTests(WindowsCase):
    def test_adapters_are_classified_and_cached(self):
        clock = mock.Mock(return_value=100.0)
        rows = wn.adapters(clock=clock)
        by_alias = {row['alias']: row for row in rows}
        self.assertEqual((by_alias['Wi-Fi 2']['kind'], by_alias['Wi-Fi 2']['usb'], by_alias['Wi-Fi 2']['transmit_mbps']),
                         ('wifi', False, 2882.0))
        self.assertEqual((by_alias['Ethernet 3']['kind'], by_alias['Ethernet 3']['usb']), ('ethernet', True))
        self.assertFalse(by_alias['vEthernet (Default Switch)']['physical'])
        self.assertEqual(by_alias['Loopback Pseudo-Interface 1']['kind'], 'other')
        rows[0]['alias'] = 'changed by a caller'
        clock.return_value = 102.0
        self.assertEqual(wn.adapters(clock=clock)[0]['alias'], 'Wi-Fi 2')  # cached, and handed out as a copy
        self.assertEqual(self.fake.adapter_calls, 1)
        clock.return_value = 103.5
        wn.adapters(clock=clock)
        self.assertEqual(self.fake.adapter_calls, 2)

    def test_a_new_api_or_invalidate_enumerates_again(self):
        wn.adapters()
        other = FakeApi(adapters=[raw_adapter('Ethernet', ETHERNET, 3, wn.IF_TYPE_ETHERNET_CSMACD)])
        with mock.patch.object(wn, '_api', other):
            self.assertEqual([row['alias'] for row in wn.adapters()], ['Ethernet'])
        wn.invalidate()
        self.assertEqual(len(wn.adapters()), 5)

    def test_physical_falls_back_to_the_adapter_type_and_wifi_to_the_medium(self):
        row = raw_adapter('Ethernet', ETHERNET, 3, wn.IF_TYPE_ETHERNET_CSMACD, pnp='')
        row.pop('physical')
        legacy = dict(raw_adapter('Wireless', WIFI, 4, wn.IF_TYPE_ETHERNET_CSMACD), physical_medium=1)
        self.fake.adapter_rows = [row, legacy]
        rows = {item['alias']: item for item in wn.adapters()}
        self.assertTrue(rows['Ethernet']['physical'])
        self.assertIsNone(rows['Ethernet']['usb'])
        self.assertEqual(rows['Wireless']['kind'], 'wifi')

    def test_unexpected_failures_become_windows_network_errors(self):
        self.fake.adapter_error = TypeError('bad row')
        with self.assertRaisesRegex(wn.WindowsNetworkError, 'bad row'):
            wn.adapters()

    def test_lan_addresses_have_the_linux_shape(self):
        rows = wn.lan_addresses()
        self.assertEqual(rows, [
            {'address': '192.168.1.30', 'interface': 'Ethernet 3', 'prefix': 24, 'physical': True, 'connected': True},
            {'address': '192.168.1.40', 'interface': 'Wi-Fi', 'prefix': 24, 'physical': True, 'connected': True},
            {'address': '192.168.1.25', 'interface': 'Wi-Fi 2', 'prefix': 24, 'physical': True, 'connected': True},
            {'address': '172.20.0.1', 'interface': 'vEthernet (Default Switch)', 'prefix': 20, 'physical': False,
             'connected': True}])
        self.fake.adapter_rows[0]['oper_status'] = 2
        self.fake.adapter_rows[0]['ipv4'][0]['dad_state'] = 2  # duplicate: not an assigned address
        self.fake.adapter_rows[1]['oper_status'] = 7
        wn.invalidate()
        rows = wn.lan_addresses()
        self.assertNotIn('Wi-Fi 2', [row['interface'] for row in rows])
        self.assertFalse(next(row for row in rows if row['interface'] == 'Ethernet 3')['connected'])

    def test_lookup_by_alias_index_address_and_guid(self):
        self.assertEqual(wn.adapter_for('Ethernet 3')['index'], 7)
        self.assertEqual(wn.adapter_for(index=12)['alias'], 'Wi-Fi 2')
        self.assertEqual(wn.adapter_for(address='192.168.1.40')['alias'], 'Wi-Fi')
        self.assertEqual(wn.adapter_for(guid=WIFI.lower())['alias'], 'Wi-Fi 2')
        self.assertIsNone(wn.adapter_for('Wi-Fi 9'))
        self.assertEqual((wn.interface_index('Wi-Fi 2'), wn.interface_index('Wi-Fi 9'), wn.interface_index('a;b')),
                         (12, None, None))

    def test_route_names_the_adapter(self):
        self.fake.route = {'index': 12, 'luid': 12000, 'gateway': '', 'source': '192.168.1.25',
                           'destination_prefix': '192.168.1.0/24', 'metric': 35}
        found = wn.route('192.168.1.10', '192.168.1.25', 'Wi-Fi 2')
        self.assertEqual((found['interface'], found['source']), ('Wi-Fi 2', '192.168.1.25'))
        self.assertEqual(self.fake.route_calls, [('192.168.1.10', '192.168.1.25', 12)])
        self.assertIsNone(wn.route('192.168.1.10', interface='Wi-Fi 9'))  # never another adapter's route
        with self.assertRaises(ValueError):
            wn.route('not an address')
        self.fake.route = None
        self.assertIsNone(wn.route('192.168.1.10'))

    def test_neighbours_and_sockets_filter(self):
        self.fake.neighbour_rows = [{'address': '192.168.1.10', 'mac': 'aa:bb:cc:dd:ee:ff', 'state': 'STALE', 'index': 7},
                                    {'address': '192.168.1.11', 'mac': 'aa:bb:cc:dd:ee:00', 'state': 'REACHABLE', 'index': 7}]
        self.assertEqual([row['address'] for row in wn.neighbours('192.168.1.10')], ['192.168.1.10'])
        self.fake.tcp = [{'pid': 1, 'local_port': 9943}, {'pid': 2, 'local_port': 9943}]
        self.fake.udp = [{'pid': 2, 'local_port': 9944}]
        self.assertEqual(wn.owned_sockets(2), ([{'pid': 2, 'local_port': 9943}], [{'pid': 2, 'local_port': 9944}]))


class WifiLinkTests(WindowsCase):
    def test_realtime_quality_gives_bands_without_location_consent(self):
        wifi_queries(self.fake, connection=wn.ERROR_ACCESS_DENIED, statistics=statistics_bytes((100, 5, 1, 300)),
                     realtime=realtime_bytes([(0, 2437, 20, -61), (1, 5180, 80, -55), (2, 6135, 320, -49)]))
        link = wn.wifi_link('Wi-Fi 2', statistics=True, options=True)
        self.assertTrue(link['connected'] and link['wifi'] and link['location_blocked'])
        self.assertEqual((link['bands'], link['band'], link['multi_link'], len(link['links'])),
                         (['2.4 GHz', '5 GHz', '6 GHz'], '6 GHz', True, 3))
        self.assertEqual((link['signal_dbm'], link['rx_mbps'], link['tx_mbps'], link['phy']), (-52, 2400.0, 2000.0, '802.11be'))
        self.assertNotIn('bssid', link)
        self.assertEqual(link['statistics']['tx_retries'], 5)
        self.assertEqual((link['media_streaming'], link['background_scan']), (False, True))
        self.assertEqual(self.fake.handles, set())  # every handle closed

    def test_without_realtime_quality_the_connection_and_access_point_list_answer(self):
        wifi_queries(self.fake, connection=connection_bytes(phy=7, quality=40, rx_kbps=144_000, tx_kbps=130_000),
                     realtime=False, rssi=None, channel=6)
        self.fake.bss[WIFI] = bss_bytes((OTHER_BSSID, 5_180_000), (BSSID, 2_437_000))
        link = wn.wifi_link('Wi-Fi 2', identity=False)
        self.assertEqual((link['ssid'], link['bssid'], link['profile'], link['phy']), ('Home', BSSID, 'Home', '802.11n'))
        self.assertEqual((link['freq_mhz'], link['bands'], link['channel']), (2437, ['2.4 GHz'], 6))
        self.assertEqual((link['signal_dbm'], link['tx_mbps']), (-80, 130.0))  # from the 0-100 quality
        self.assertFalse(link['multi_link'])

    def test_channel_gives_the_band_when_nothing_else_does(self):
        wifi_queries(self.fake, connection=wn.ERROR_ACCESS_DENIED,
                     realtime=realtime_bytes([(0, 0, 0, 0)], phy=10), channel=36)
        self.assertEqual(wn.wifi_link('Wi-Fi 2')['bands'], ['5 GHz'])

    def test_disconnected_unmanaged_and_unavailable(self):
        self.fake.interfaces[0]['state'] = 4
        link = wn.wifi_link('Wi-Fi 2')
        self.assertEqual((link['wifi'], link['connected'], link['state']), (True, False, 'disconnected'))
        self.assertIn('not manage', wn.wifi_link('Ethernet 3')['reason'])
        self.assertIn('not found', wn.wifi_link('Wi-Fi 9')['reason'])
        self.fake.open_error = wn.WindowsNetworkError('Wi-Fi details are unavailable (the WLAN AutoConfig service is not '
                                                      'running).', wn.ERROR_SERVICE_NOT_ACTIVE)
        self.assertIn('WLAN AutoConfig', wn.wifi_link('Wi-Fi 2')['reason'])
        self.assertEqual(self.fake.handles, set())

    def test_power_saving_for_the_current_source(self):
        self.fake.power, self.fake.battery = (0, 2), True
        self.assertEqual(wn.wifi_power_saving(), {'ac': 0, 'dc': 2, 'on_battery': True, 'current': 2})
        self.fake.battery = None
        self.assertIsNone(wn.wifi_power_saving()['current'])
        self.fake.power = (0, 0)
        self.assertEqual(wn.wifi_power_saving()['current'], 0)
        self.assertEqual((wn.power_mode_name(2), wn.power_mode_name(None)), ('Medium Power Saving', 'unknown'))
        self.fake.power = wn.WindowsNetworkError('no plan')
        with self.assertRaises(wn.WindowsNetworkError):
            wn.wifi_power_saving()


class SessionOptionTests(WindowsCase):
    def test_setting_lasts_while_the_handle_is_open(self):
        option = wn.WlanSessionOption(WIFI, wn.OPCODE_BACKGROUND_SCAN, False, start=False)
        self.assertEqual(self.fake.options[(WIFI, wn.OPCODE_BACKGROUND_SCAN)][1], False)
        self.assertTrue(option.keep())
        self.assertEqual(option.status(), {'active': True, 'reapplied': 0})
        option.close()
        self.assertNotIn((WIFI, wn.OPCODE_BACKGROUND_SCAN), self.fake.options)
        self.assertFalse(option.keep())
        option.close()  # twice is harmless

    def test_applied_again_after_windows_drops_it_on_a_reconnect(self):
        option = wn.WlanSessionOption(WIFI, wn.OPCODE_MEDIA_STREAMING, True, start=False)
        self.fake.drop(WIFI, wn.OPCODE_MEDIA_STREAMING)
        self.assertTrue(option.keep())
        self.assertEqual(option.status()['reapplied'], 1)
        self.assertTrue(self.fake.options[(WIFI, wn.OPCODE_MEDIA_STREAMING)][1])
        option.close()

    def test_refusals(self):
        self.fake.set_codes[(WIFI, wn.OPCODE_MEDIA_STREAMING)] = wn.ERROR_ACCESS_DENIED
        with self.assertRaises(wn.WindowsNetworkError) as raised:
            wn.WlanSessionOption(WIFI, wn.OPCODE_MEDIA_STREAMING, True, start=False)
        self.assertEqual(raised.exception.code, wn.ERROR_ACCESS_DENIED)
        self.assertEqual(self.fake.handles, set())

    def test_keeper_thread_stops_on_close(self):
        option = REAL_OPTION(WIFI, wn.OPCODE_MEDIA_STREAMING, True, api=self.fake, interval=0.01)
        self.fake.drop(WIFI, wn.OPCODE_MEDIA_STREAMING)
        for _ in range(200):
            if option.status()['reapplied']:
                break
            option.closed.wait(0.01)
        option.close()
        option.thread.join(2)
        self.assertFalse(option.thread.is_alive())
        self.assertEqual(option.status()['reapplied'], 1)


# ------------------------------------------------------------------------------------------- modules
class ClientIdentityWindowsTests(WindowsCase):
    def test_alvr_identity_lives_in_roaming_appdata(self):
        with mock.patch.dict(os.environ, {'APPDATA': '/users/vr/AppData/Roaming'}):
            self.assertEqual(client_identity.config_path(), Path('/users/vr/AppData/Roaming/ALVR/ALVR Client/session.json'))
        with mock.patch.dict(os.environ, {'APPDATA': ''}):
            self.assertEqual(client_identity.config_path(),
                             Path.home() / 'AppData' / 'Roaming' / 'ALVR' / 'ALVR Client' / 'session.json')

    def test_lan_addresses_use_the_windows_rows_and_fail_soft(self):
        self.assertEqual(client_identity.lan_addresses()[0]['interface'], 'Ethernet 3')
        wn.invalidate()
        self.fake.adapter_error = wn.WindowsNetworkError('no adapters')
        self.assertEqual(client_identity.lan_addresses(), [])


class SessionLinkWindowsTests(WindowsCase):
    def test_network_addresses_carry_the_alias_and_are_cached(self):
        rows = session_link.network_addresses()
        self.assertIn({'address': '192.168.1.25', 'interface': 'Wi-Fi 2', 'prefix': 24, 'physical': True,
                       'connected': True}, rows)
        for _ in range(5):
            session_link.network_addresses()
        self.assertEqual(self.fake.adapter_calls, 1)
        self.assertEqual([address for address, _ in session_link._interfaces(session_link.network_addresses)],
                         ['192.168.1.30', '192.168.1.40', '192.168.1.25'])  # no virtual or link-local address

    def test_powershell_only_when_the_api_cannot_answer(self):
        self.fake.adapter_error = wn.WindowsNetworkError('iphlpapi missing')
        output = json.dumps([{'address': '192.168.1.25', 'interface': 'Wi-Fi 2', 'prefix': 24, 'physical': True,
                              'connected': True}])
        with mock.patch.object(session_link.subprocess, 'run', return_value=completed([], 0, output)) as run:
            self.assertEqual(session_link.network_addresses()[0]['interface'], 'Wi-Fi 2')
        arguments, options = run.call_args
        self.assertTrue(arguments[0][0].lower().endswith('powershell.exe'))
        self.assertIn('InterfaceAlias', arguments[0][-1])
        self.assertEqual((options['creationflags'], options['encoding'], options['timeout']), (0x08000000, 'utf-8', 8))
        with mock.patch.object(session_link.subprocess, 'run', side_effect=OSError('missing')):
            self.assertEqual(session_link.network_addresses(), [])

    def test_aliases_with_spaces_are_valid_interface_names(self):
        self.assertTrue(session_link._valid_interface_name('Wi-Fi 2'))
        self.assertFalse(session_link._valid_interface_name('Wi-Fi;2'))
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertFalse(session_link._valid_interface_name('Wi-Fi 2'))
            self.assertTrue(session_link._valid_interface_name('wlp1s0'))

    def test_pinning_uses_ip_unicast_if_31_with_the_index_in_network_order(self):
        probe = mock.Mock()
        session_link.pin_ipv4_interface(probe, 'Wi-Fi 2')
        probe.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 31, b'\0\0\0\x0c')
        probe = mock.Mock()
        with self.assertRaises(OSError):
            session_link.pin_ipv4_interface(probe, 'Wi-Fi 9')
        probe.setsockopt.assert_not_called()
        session_link.pin_ipv4_interface(probe, '')
        probe.setsockopt.assert_not_called()

    def test_http_connection_pins_before_binding_and_connecting(self):
        probe, order = mock.Mock(), []
        probe.setsockopt.side_effect = lambda *args: order.append('interface')
        probe.bind.side_effect = lambda *args: order.append('source')
        probe.connect.side_effect = lambda *args: order.append('connect')
        with mock.patch('launcher.session_link.socket.socket', return_value=probe):
            self.assertIs(session_link._source_connection(('192.168.1.10', 9946), 2, ('192.168.1.25', 0), 'Ethernet 3'),
                          probe)
        self.assertEqual(order, ['interface', 'source', 'connect'])
        probe.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 31, b'\0\0\0\x07')

    def test_client_selects_an_alias_and_hands_it_to_native(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # With sys.platform patched, building the HTTPS-capable opener would read the Windows certificate store.
        opener = mock.patch.object(session_link.urllib.request, 'build_opener', return_value=mock.Mock())
        opener.start()
        self.addCleanup(opener.stop)
        client = session_link.SessionClient(directory.name, self.lines.append)
        self.addCleanup(client.close)
        client.select_interface('Wi-Fi 2')
        self.assertEqual(client.network_status()['interface'], 'Wi-Fi 2')
        self.assertIn('Wi-Fi 2', [row['interface'] for row in client.network_status()['adapters']])
        with self.assertRaises(session_link.SessionError):
            client.select_interface('Wi-Fi 9')
        with self.assertRaises(session_link.SessionError):
            client.select_interface('Wi-Fi";2')
        self.assertEqual(client._interface_for_source('192.168.1.25'), 'Wi-Fi 2')
        client.discover = mock.Mock(return_value={'host_ip': '192.168.1.10', 'host_id': 'a' * 32,
                                                  'source_ip': '192.168.1.25'})
        self.assertEqual(client.prepare_network(), {'VROSTBRIDGE_BIND_IP': '192.168.1.25',
                                                    'VROSTBRIDGE_INTERFACE': 'Wi-Fi 2'})
        reopened = session_link.SessionClient(directory.name, self.lines.append)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.interface, 'Wi-Fi 2')  # the saved alias survives a restart


class NetworkTestWindowsTests(WindowsCase):
    def test_link_facts_for_ethernet_and_wifi(self):
        wired = network_test.link_info('Ethernet 3')
        self.assertEqual((wired['kind'], wired['speed_mbps'], wired['usb_adapter']), ('wired', 1000, True))
        self.assertEqual(network_test.pacing_mbps(wired), 600)  # USB Ethernet without a test: 60 % of its link
        wifi = network_test.link_info('Wi-Fi 2', wifi_sample=lambda name: {'rx_bitrate_mbps': 2401.94})
        self.assertEqual((wifi['kind'], wifi['rx_mbps'], wifi['tx_mbps']), ('wifi', 2401.9, 2882.0))
        broken = network_test.link_info('Wi-Fi 2', wifi_sample=mock.Mock(side_effect=RuntimeError))
        self.assertEqual(broken['rx_mbps'], 2882.0)
        for name in ('Wi-Fi 9', 'a;b', '', None):
            self.assertEqual(network_test.link_info(name)['kind'], 'unknown')
        self.fake.adapter_error = wn.WindowsNetworkError('no adapters')
        wn.invalidate()
        self.assertEqual(network_test.link_info('Ethernet 3')['kind'], 'unknown')

    def test_saved_results_keep_long_aliases_apart(self):
        first, second = 'Беспроводная сеть 2', 'Беспроводная сеть 3'
        self.assertNotEqual(network_test.path_key('h', first), network_test.path_key('h', second))
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertEqual(network_test.path_key('h', 'x' * 20), 'h|' + 'x' * 15)


class ConnectionKindWindowsTests(WindowsCase):
    def test_wifi_7_multi_link(self):
        wifi_queries(self.fake, realtime=realtime_bytes([(0, 2437, 20, -61), (1, 5180, 80, -55), (2, 6135, 320, -49)]))
        info = connection_kind.describe('Wi-Fi 2')
        self.assertEqual(info['kind'], 'wifi')
        self.assertEqual(info['summary'], 'Wi-Fi · multi-link 2.4 + 5 + 6 GHz · -52 dBm · 2 Gbit/s link (Wi-Fi 2)')
        self.assertEqual((info['details']['multi_link'], info['details']['bands'], info['warnings']),
                         (3, ['2.4 GHz', '5 GHz', '6 GHz'], []))

    def test_page_reads_do_not_ask_for_the_location_gated_connection(self):
        wifi_queries(self.fake)
        asked = []
        original = self.fake.wlan_query
        self.fake.wlan_query = lambda handle, guid, opcode: asked.append(opcode) or original(handle, guid, opcode)
        connection_kind.describe('Wi-Fi 2')
        self.assertNotIn(wn.OPCODE_CURRENT_CONNECTION, asked)

    def test_slow_wifi_warnings(self):
        wifi_queries(self.fake, connection=connection_bytes(phy=7, rx_kbps=144_000, tx_kbps=144_000), realtime=False,
                     rssi=-74, channel=6)
        info = connection_kind.describe('Wi-Fi 2')
        self.assertEqual(info['summary'], 'Wi-Fi · 2.4 GHz · -74 dBm · 144 Mbit/s link (Wi-Fi 2)')
        self.assertEqual(len(info['warnings']), 2)
        self.assertIn('2.4 GHz', info['warnings'][0])
        self.assertIn('weak (-74 dBm)', info['warnings'][1])

    def test_wifi_not_connected_or_unreadable(self):
        self.fake.interfaces[0]['state'] = 4
        info = connection_kind.describe('Wi-Fi 2')
        self.assertEqual((info['summary'], info['details']), ('Wi-Fi (Wi-Fi 2) is not connected.', {'connected': False}))
        self.fake.open_error = wn.WindowsNetworkError('the WLAN AutoConfig service is not running')
        info = connection_kind.describe('Wi-Fi 2')
        self.assertEqual((info['kind'], info['summary']), ('wifi', 'Wi-Fi (Wi-Fi 2)'))
        self.assertIn('WLAN AutoConfig', info['notes'][0])

    def test_ethernet_speed_usb_and_cable(self):
        info = connection_kind.describe('Ethernet 3')
        self.assertEqual(info['summary'], 'Ethernet · 1 Gbit/s · USB adapter (Ethernet 3)')
        self.assertEqual(info['details'], {'link_mbps': 1000, 'duplex': '', 'usb_adapter': True, 'connected': True})
        self.fake.adapter_rows[1]['transmit_bps'] = 100_000_000
        wn.invalidate()
        self.assertIn('only 100 Mbit/s', connection_kind.describe('Ethernet 3')['warnings'][0])
        self.fake.adapter_rows[1]['media_connected'] = False
        wn.invalidate()
        info = connection_kind.describe('Ethernet 3')
        self.assertEqual(info['summary'], 'Ethernet (Ethernet 3) has no cable link.')

    def test_unknown_or_invalid_adapters(self):
        self.assertEqual(connection_kind.describe('Wi-Fi 9')['summary'], 'Wi-Fi 9 is not available.')
        for name in ('', '../../etc', 'a;b', None):
            self.assertEqual(connection_kind.describe(name)['summary'], 'No network adapter is selected yet.')


class NetworkPathWindowsTests(WindowsCase):
    def test_native_sockets_by_process(self):
        self.fake.tcp = [{'local_ip': '192.168.1.25', 'local_port': 9943, 'peer_ip': '192.168.1.10', 'peer_port': 50211,
                          'state': 5, 'pid': 4242},
                         {'local_ip': '192.168.1.25', 'local_port': 9945, 'peer_ip': '192.168.1.10', 'peer_port': 5000,
                          'state': 5, 'pid': 4242},
                         {'local_ip': '192.168.1.25', 'local_port': 9943, 'peer_ip': '192.168.1.11', 'peer_port': 1,
                          'state': 2, 'pid': 99}]
        self.fake.udp = [{'local_ip': '192.168.1.25', 'local_port': 9944, 'pid': 4242}]
        self.assertEqual(network_path.transport_sockets(4242), [
            {'protocol': 'tcp', 'local': {'ip': '192.168.1.25', 'port': 9943}, 'peer': {'ip': '192.168.1.10', 'port': 50211},
             'state': '01', 'send_queue_bytes': None, 'receive_queue_bytes': None},
            {'protocol': 'udp', 'local': {'ip': '192.168.1.25', 'port': 9944}, 'peer': {'ip': '0.0.0.0', 'port': 0},
             'state': '07', 'send_queue_bytes': None, 'receive_queue_bytes': None}])
        self.assertEqual(network_path.transport_sockets(0), [])

    def test_route_state_and_radio_evidence(self):
        wifi_queries(self.fake)
        self.fake.route = {'index': 12, 'luid': 12000, 'gateway': '', 'source': '192.168.1.25',
                           'destination_prefix': '192.168.1.0/24', 'metric': 35}
        records = []
        selection = {'interface': '', 'source_ip': '192.168.1.25', 'source_interface': 'Wi-Fi 2', 'host_ip': '192.168.1.10',
                     'adapters': [], 'pinned': True}
        monitor = network_path.NetworkMonitor(lambda: dict(selection), lambda: 4242,
                                              lambda event, value: records.append(event), auto_start=False)
        value = monitor.sample()
        self.assertEqual(value['route'], {'dev': 'Wi-Fi 2', 'prefsrc': '192.168.1.25', 'dst': '192.168.1.10'})
        self.assertEqual(self.fake.route_calls, [('192.168.1.10', '192.168.1.25', 12)])
        self.assertEqual(value['operstate'], 'up')
        self.assertEqual((value['wifi']['signal'], value['wifi']['band'], value['wifi']['streaming mode'],
                          value['wifi']['background scan'], value['wifi']['Power save']),
                         ('-52 dBm', '6 GHz', 'off', 'on', 'Maximum Performance'))
        self.assertEqual(records, ['network-path'])
        self.fake.route = dict(self.fake.route, index=7)  # the path moved to Ethernet
        selection['source_interface'] = ''
        value = monitor.sample()
        self.assertEqual(value['route']['dev'], 'Ethernet 3')
        self.assertNotIn('wifi', value)
        self.assertEqual(records, ['network-path', 'network-path', 'network-path-change'])
        self.fake.route = None
        self.assertNotIn('route', monitor.sample())
        selection['host_ip'] = 'bad'
        self.assertTrue(monitor.sample()['route_unavailable'])


class WakeHostWindowsTests(WindowsCase):
    def test_learns_the_best_confirmed_neighbour(self):
        self.fake.neighbour_rows = [
            {'address': '192.168.1.10', 'mac': '02:00:00:00:00:01', 'state': 'STALE', 'index': 7},
            {'address': '192.168.1.10', 'mac': '02:00:00:00:00:02', 'state': 'REACHABLE', 'index': 12},
            {'address': '192.168.1.10', 'mac': '', 'state': 'INCOMPLETE', 'index': 15},
            {'address': '192.168.1.11', 'mac': '02:00:00:00:00:03', 'state': 'REACHABLE', 'index': 7}]
        runner = mock.Mock()
        self.assertEqual(wake_host.learn_mac('192.168.1.10', runner=runner), '02:00:00:00:00:02')
        runner.assert_not_called()
        self.fake.neighbour_rows = [{'address': '192.168.1.10', 'mac': 'ff:ff:ff:ff:ff:ff', 'state': 'PERMANENT'},
                                    {'address': '192.168.1.10', 'mac': '02:00:00:00:00:04', 'state': 'FAILED'}]
        self.assertIsNone(wake_host.learn_mac('192.168.1.10', runner=runner))
        self.assertIsNone(wake_host.learn_mac('127.0.0.1', runner=runner))

    def test_arp_is_the_fallback(self):
        self.fake.neighbour_error = wn.WindowsNetworkError('unavailable')
        output = ('\r\nInterface: 192.168.1.25 --- 0xc\r\n  Internet Address      Physical Address      Type\r\n'
                  '  192.168.1.10          aa-bb-cc-dd-ee-01     dynamic   \r\n')
        runner = mock.Mock(return_value=completed([], 0, output))
        self.assertEqual(wake_host.learn_mac('192.168.1.10', runner=runner), 'aa:bb:cc:dd:ee:01')
        arguments, timeout = runner.call_args[0]
        self.assertEqual((Path(arguments[0]).name.upper(), arguments[1:], timeout), ('ARP.EXE', ['-a', '192.168.1.10'], 3))
        runner.side_effect = OSError('missing')
        self.assertIsNone(wake_host.learn_mac('192.168.1.10', runner=runner))
        self.assertEqual(wake_host.local_interfaces(client_identity.lan_addresses())[0].with_prefixlen, '192.168.1.30/24')


class WifiSessionWindowsTests(WindowsCase):
    def session(self, runner=None):
        return wifi_session.WifiSession(runner=runner or mock.Mock(), log=self.lines.append, nmcli='nmcli', iw='iw',
                                        ip='ip')

    def test_media_streaming_mode_for_the_session(self):
        session = self.session()
        status = session.begin('Wi-Fi 2', '192.168.1.10', '192.168.1.25')
        self.assertEqual((status['state'], status['streaming_mode'], status['permanent_fix']), ('off', True, True))
        self.assertEqual((status['power_saving_ac'], status['power_saving_dc']), ('Maximum Performance', 'Medium Power Saving'))
        self.assertTrue(self.fake.options[(WIFI, wn.OPCODE_MEDIA_STREAMING)][1])
        session.end()
        self.assertNotIn((WIFI, wn.OPCODE_MEDIA_STREAMING), self.fake.options)
        self.assertEqual(self.fake.handles, set())
        self.assertEqual(session.status()['state'], 'restored')
        session.end()  # nothing left to undo

    def test_power_saving_on_battery_is_reported_with_the_permanent_fix(self):
        self.fake.battery = True
        status = self.session().begin('Wi-Fi 2')
        self.assertEqual((status['state'], status['permanent_fix']), ('on', True))
        self.assertIn('Medium Power Saving on battery', status['message'])
        self.assertIn('Media streaming mode is on', status['message'])

    def test_unreadable_power_plan_and_refused_streaming(self):
        self.fake.power = wn.WindowsNetworkError('no plan')
        session = self.session()
        self.assertEqual(session.begin('Wi-Fi 2')['state'], 'disabled_for_session')
        session.end()
        self.fake.set_codes[(WIFI, wn.OPCODE_MEDIA_STREAMING)] = wn.ERROR_NOT_SUPPORTED
        status = session.begin('Wi-Fi 2')
        self.assertEqual((status['state'], status['streaming_mode']), ('on', False))
        self.assertIn('did not turn on Wi-Fi media streaming mode', status['message'])

    def test_wired_and_unknown_adapters_are_left_alone(self):
        session = self.session()
        self.assertEqual(session.begin('Ethernet 3')['state'], 'wired')
        self.assertEqual(session.begin('Wi-Fi 9')['state'], 'unknown')
        self.assertEqual(self.fake.opened, 0)

    def test_route_interface_is_the_alias(self):
        self.fake.route = {'index': 7, 'gateway': '', 'source': '192.168.1.30'}
        session = self.session()
        self.assertEqual(session.route_interface('192.168.1.10'), 'Ethernet 3')
        self.assertEqual(session.begin('', '192.168.1.10')['state'], 'wired')
        self.assertEqual(session.route_interface('not an address'), '')
        self.assertEqual(session.power_save('Wi-Fi 2'), False)
        self.assertEqual(session.wireless_interfaces(), ['Wi-Fi', 'Wi-Fi 2'])

    def test_disable_permanently_sets_the_power_plan(self):
        def powercfg(arguments, timeout):
            if arguments[1] == '/setactive':
                self.fake.power = (0, 0)  # Windows applies the plan
            return completed(arguments, 0)
        runner = mock.Mock(side_effect=powercfg)
        session = self.session(runner)
        session.begin('Wi-Fi 2')
        self.assertTrue(session.status()['permanent_fix'])
        session.end()
        message = session.disable_permanently('Wi-Fi 2')
        self.assertIn('Maximum Performance', message)
        self.assertIn('Windows confirms it', message)
        commands = [call[0][0] for call in runner.call_args_list]
        self.assertEqual([Path(command[0]).name.lower() for command in commands], ['powercfg.exe'] * 3)
        setting = [wn.WIFI_POWER_SUBGROUP, wn.WIFI_POWER_SETTING, '0']
        self.assertEqual([command[1:] for command in commands],
                         [['/setacvalueindex', 'SCHEME_CURRENT', *setting], ['/setdcvalueindex', 'SCHEME_CURRENT', *setting],
                          ['/setactive', 'SCHEME_CURRENT']])
        self.assertFalse(session.status()['permanent_fix'])  # the page's fix button goes away
        runner.side_effect = None
        runner.return_value = completed([], 1, '', 'Access is denied.')
        with self.assertRaisesRegex(ValueError, 'Access is denied'):
            session.disable_permanently()
        self.fake.adapter_rows = [row for row in self.fake.adapter_rows if row['if_type'] != wn.IF_TYPE_IEEE80211]
        wn.invalidate()
        with self.assertRaisesRegex(ValueError, 'No Wi-Fi adapter'):
            session.disable_permanently()


class WifiRoamingWindowsTests(WindowsCase):
    def lock(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        return wifi_roaming.AccessPointLock(self.state, runner=mock.Mock(side_effect=AssertionError('no commands')),
                                            log=self.lines.append, iw='', nmcli='')

    def test_background_scans_off_for_the_session(self):
        wifi_queries(self.fake)
        lock = self.lock()
        status = lock.begin('Wi-Fi 2')
        self.assertEqual((status['state'], status['locked'], status['reconnected'], status['bssid']),
                         ('locked', True, False, BSSID))
        self.assertIn('background scans are off', status['message'])
        self.assertFalse(self.fake.options[(WIFI, wn.OPCODE_BACKGROUND_SCAN)][1])
        self.assertEqual(len(self.fake.handles), 1)  # held for the session
        self.assertEqual(lock.end()['state'], 'released')
        self.assertNotIn((WIFI, wn.OPCODE_BACKGROUND_SCAN), self.fake.options)
        self.assertEqual(self.fake.handles, set())
        self.assertFalse(any(self.state.iterdir()))  # no receipt on Windows

    def test_location_consent_only_changes_the_message(self):
        wifi_queries(self.fake, connection=wn.ERROR_ACCESS_DENIED)
        status = self.lock().begin('Wi-Fi 2')
        self.assertEqual(status['state'], 'locked')
        self.assertNotIn('bssid', status)
        self.assertIn('its current access point', status['message'])

    def test_not_connected_refused_wired_and_invalid(self):
        lock = self.lock()
        self.fake.set_codes[(WIFI, wn.OPCODE_BACKGROUND_SCAN)] = wn.ERROR_INVALID_STATE
        self.assertIn('not connected', lock.begin('Wi-Fi 2')['message'])
        self.fake.set_codes[(WIFI, wn.OPCODE_BACKGROUND_SCAN)] = wn.ERROR_ACCESS_DENIED
        self.assertEqual(lock.begin('Wi-Fi 2')['state'], 'refused')
        self.assertEqual(lock.begin('Ethernet 3')['state'], 'wired')
        self.assertEqual(lock.begin('a;b')['state'], 'unavailable')
        self.assertEqual(lock.begin('Wi-Fi 9')['state'], 'unavailable')
        self.assertEqual(self.fake.handles, set())

    def test_recover_has_nothing_to_undo(self):
        lock = self.lock()
        (self.state / wifi_roaming.RECEIPT).write_text('{"uuid": "x"}')
        lock.recover()
        self.assertEqual(lock.status()['state'], 'idle')


class WifiDiagnosticsWindowsTests(WindowsCase):
    def diagnostics(self, clock):
        return wifi_diagnostics.WifiDiagnostics(runner=mock.Mock(side_effect=AssertionError('no commands')), iw='',
                                                nmcli='', log=self.lines.append, clock=clock)

    def test_samples_rates_counters_and_roams(self):
        times = iter([10.0, 15.0, 20.0, 25.0])
        wifi_queries(self.fake, statistics=statistics_bytes((1000, 100, 1, 5000)))
        diagnostics = self.diagnostics(lambda: next(times))
        first = diagnostics.begin('Wi-Fi 2')
        self.assertTrue(first['connected'])
        self.assertEqual((first['signal_dbm'], first['tx_bitrate_mbps'], first['rx_bitrate_mbps'], first['channel'],
                          first['band'], first['phy'], first['links']), (-52, 960.0, 1200.0, 37, '6 GHz', '802.11be', 0))
        self.fake.queries[(WIFI, wn.OPCODE_STATISTICS)] = statistics_bytes((2000, 150, 3, 9000))
        second = diagnostics.sample()
        self.assertEqual((second['tx_retries_per_s'], second['tx_failed_per_s'], second['tx_retry_ratio'], second['roamed']),
                         (10.0, 0.4, 0.05, False))
        self.fake.queries[(WIFI, wn.OPCODE_CURRENT_CONNECTION)] = connection_bytes(bssid=OTHER_BSSID)
        self.fake.queries[(WIFI, wn.OPCODE_STATISTICS)] = statistics_bytes((2500, 160, 3, 9900))
        roam = diagnostics.sample()
        self.assertEqual((roam['roamed'], roam['reassociated'], roam['tx_retries_per_s']), (True, True, 2.0))
        summary = diagnostics.end()
        self.assertEqual(summary['interface'], 'Wi-Fi 2')
        totals = summary['since_begin']
        self.assertEqual((totals['roams'], totals['tx_retries'], totals['tx_packets'], totals['samples']), (1, 60, 1500, 3))
        self.assertEqual(self.fake.handles, set())

    def test_location_blocked_disconnected_and_unavailable(self):
        wifi_queries(self.fake, connection=wn.ERROR_ACCESS_DENIED)
        diagnostics = self.diagnostics(lambda: 1.0)
        sample = diagnostics.begin('Wi-Fi 2')
        self.assertTrue(sample['connected'] and sample['location_blocked'])
        self.assertIn('location access', sample['message'])
        self.fake.interfaces[0]['state'] = 4
        self.assertEqual(diagnostics.sample(), {'interface': 'Wi-Fi 2', 'connected': False,
                                                'message': 'Wi-Fi is not connected on Wi-Fi 2.'})
        self.assertIn('could not be read', diagnostics.sample('Ethernet 3')['message'])
        self.assertEqual(diagnostics.sample('a;b')['message'], 'No valid Wi-Fi adapter was given.')


class OtherWifiWindowsTests(WindowsCase):
    def pause(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.receipt = Path(directory.name) / wifi_others.RECEIPT
        return wifi_others.OtherWifiPause(directory.name, runner=mock.Mock(side_effect=AssertionError('no commands')),
                                          log=self.lines.append, nmcli='')

    def test_pauses_and_reconnects_another_wifi_adapter_on_the_network(self):
        wifi_queries(self.fake, guid=WIFI_USB, connection=connection_bytes(profile='Home 5G'))
        pause = self.pause()
        self.assertEqual(pause.others('Wi-Fi 2', '192.168.1.25'), ['Wi-Fi'])
        status = pause.begin('Wi-Fi 2', '192.168.1.25')
        self.assertEqual((status['state'], status['paused']), ('paused', ['Wi-Fi']))
        self.assertEqual(self.fake.disconnects, [WIFI_USB])
        saved = json.loads(self.receipt.read_text())
        self.assertEqual(saved, {'paused': ['Wi-Fi'], 'windows': {'Wi-Fi': {'interface': 'Wi-Fi', 'guid': WIFI_USB,
                                                                            'profile': 'Home 5G'}}})
        pause.end()
        self.assertEqual(self.fake.connects, [(WIFI_USB, 'Home 5G')])
        self.assertFalse(self.receipt.exists())
        self.assertEqual(pause.status()['state'], 'restored')
        self.assertEqual(self.fake.handles, set())

    def test_a_wired_stream_pauses_wifi_on_the_same_network(self):
        wifi_queries(self.fake, guid=WIFI)
        wifi_queries(self.fake, guid=WIFI_USB)
        status = self.pause().begin('Ethernet 3', '192.168.1.30')
        self.assertEqual(sorted(status['paused']), ['Wi-Fi', 'Wi-Fi 2'])

    def test_no_second_reconnection_when_windows_reconnected_it(self):
        wifi_queries(self.fake, guid=WIFI_USB)
        pause = self.pause()
        pause.begin('Wi-Fi 2', '192.168.1.25')
        self.fake.interfaces[1]['state'] = 1
        pause.end()
        self.assertEqual(self.fake.connects, [])
        self.assertEqual(pause.status()['state'], 'restored')

    def test_without_location_consent_the_adapter_is_not_paused(self):
        wifi_queries(self.fake, guid=WIFI_USB, connection=wn.ERROR_ACCESS_DENIED)
        status = self.pause().begin('Wi-Fi 2', '192.168.1.25')
        self.assertEqual(status['state'], 'warning')
        self.assertIn('Could not pause Wi-Fi on the same network (Windows location access is off', status['message'])
        self.assertEqual(self.fake.disconnects, [])

    def test_only_warns_when_disabled(self):
        status = self.pause().begin('Wi-Fi 2', '192.168.1.25', enabled=False)
        self.assertEqual(status['state'], 'warning')
        self.assertEqual(self.fake.disconnects, [])

    def test_recovers_a_pause_left_by_an_earlier_companion(self):
        pause = self.pause()
        self.receipt.write_text(json.dumps({'paused': ['Wi-Fi', 'bad;name'], 'windows': {
            'Wi-Fi': {'interface': 'Wi-Fi', 'guid': WIFI_USB, 'profile': 'Home 5G'}}}))
        self.fake.interfaces[1]['state'] = 4
        self.assertEqual(pause.recover(), [])
        self.assertEqual(self.fake.connects, [(WIFI_USB, 'Home 5G')])
        self.assertFalse(self.receipt.exists())

    def test_failed_reconnections_stay_recorded(self):
        wifi_queries(self.fake, guid=WIFI_USB)
        pause = self.pause()
        pause.begin('Wi-Fi 2', '192.168.1.25')
        self.fake.connect_code = wn.ERROR_ACCESS_DENIED
        pause.end()
        self.assertEqual(pause.status()['state'], 'restore_failed')
        self.assertEqual(json.loads(self.receipt.read_text())['windows']['Wi-Fi']['profile'], 'Home')
        self.fake.connect_code = 0
        self.assertEqual(pause.recover(), [])
        self.assertFalse(self.receipt.exists())


class ClientNetworkWindowsTests(WindowsCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.script, self.client, self.python = root / 'Client-Firewall.ps1', root / 'VROSTBridgeClient.exe', root / 'python.exe'
        for path in (self.script, self.client, self.python):
            path.write_text('')
        self.states = {name: 'missing' for name in client_network.WINDOWS_RULES}
        self.blocking, self.profiles = [], [{'alias': 'Wi-Fi 2', 'category': 'Private'}]
        self.firewall = [{'name': 'Domain', 'enabled': 'True'}, {'name': 'Private', 'enabled': 'True'},
                         {'name': 'Public', 'enabled': 'True'}]
        self.calls, self.firewall_code = [], 0

    def runner(self, arguments, timeout):
        self.calls.append((arguments, timeout))
        action = arguments[arguments.index('-Action') + 1]
        if action == 'Check':
            return completed(arguments, 0, json.dumps({'schema': 1, 'blocking': self.blocking, 'profiles': self.profiles,
                                                       'firewall': self.firewall, 'rules': [
                                                           {'name': name, 'state': state} for name, state in self.states.items()]}))
        if action == 'Firewall' and self.firewall_code == 0:
            self.states = {name: 'ready' for name in self.states}
        return completed(arguments, self.firewall_code, '', 'New-NetFirewallRule : Access is denied.' if self.firewall_code == 1 else '')

    def network(self):
        return client_network.ClientNetwork(lambda: client_identity.lan_addresses(), self.runner, auto_refresh=False,
                                            client_executable=self.client, python_executable=self.python, script=self.script)

    def test_check_runs_the_script_unelevated_and_is_cached(self):
        network = self.network()
        self.assertEqual(network.status()['state'], 'checking')
        status = network.refresh()
        self.assertEqual((status['state'], status['can_allow'], status['missing_rules'], status['runtime_ready']),
                         ('needs-permission', True, 5, False))
        arguments, timeout = self.calls[0]
        self.assertTrue(arguments[0].lower().endswith('powershell.exe'))
        self.assertEqual(arguments[1:], ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(self.script),
                                         '-Action', 'Check', '-ClientExecutable', str(self.client),
                                         '-PythonExecutable', str(self.python)])
        self.assertEqual(timeout, 60)
        self.assertEqual(network.status()['state'], 'needs-permission')
        self.assertEqual(len(self.calls), 1)  # status() only reads the cached result
        self.assertEqual({target['interface']: target['category'] for target in status['targets']},
                         {'Ethernet 3': '', 'Wi-Fi': '', 'Wi-Fi 2': 'Private'})

    def test_allow_asks_windows_once_and_verifies(self):
        network = self.network()
        status = network.allow()
        self.assertEqual((status['state'], status['runtime_ready'], status['permanent_ready']), ('ready', True, True))
        actions = [arguments[arguments.index('-Action') + 1] for arguments, _ in self.calls]
        self.assertEqual(actions, ['Check', 'Firewall', 'Check'])
        self.assertEqual(self.calls[1][1], 600)
        self.assertEqual(network.allow()['state'], 'ready')  # nothing more to do
        self.assertEqual(len(self.calls), 4)

    def test_declined_and_failed_permission(self):
        self.firewall_code = client_network.ERROR_CANCELLED
        network = self.network()
        with self.assertRaisesRegex(ValueError, 'Administrator permission was not given'):
            network.allow()
        self.assertEqual(network.status()['state'], 'needs-permission')
        self.firewall_code = 1
        with self.assertRaisesRegex(ValueError, 'Windows Firewall setup failed: New-NetFirewallRule : Access is denied'):
            network.allow()

    def test_states_that_need_the_user(self):
        network = self.network()
        self.states = {name: 'ready' for name in self.states}
        self.states['VROSTBridge.Client.VR.UDP'] = 'foreign'
        status = network.refresh()
        self.assertEqual((status['state'], status['can_allow']), ('error', False))
        self.assertIn('VROSTBridge.Client.VR.UDP already exists', status['message'])
        with self.assertRaises(ValueError):
            network.allow()
        self.assertNotIn('Firewall', [arguments[arguments.index('-Action') + 1] for arguments, _ in self.calls])
        self.states['VROSTBridge.Client.VR.UDP'] = 'ready'
        self.blocking = ['python.exe']
        status = network.refresh()
        self.assertEqual(status['state'], 'error')
        self.assertIn('block this program (python.exe)', status['message'])
        self.blocking, self.profiles = [], [{'alias': 'Wi-Fi 2', 'category': 'Public'}]
        status = network.refresh()
        self.assertEqual(status['state'], 'error')
        self.assertIn('treats the network on Wi-Fi 2 as Public', status['message'])
        self.profiles.append({'alias': 'Ethernet 3', 'category': 'Private'})
        self.assertEqual(network.refresh()['state'], 'ready')
        self.firewall[1]['enabled'] = 'False'
        self.assertEqual(network.refresh()['state'], 'inactive')

    def test_missing_files_and_unreadable_output(self):
        network = self.network()
        self.client.unlink()
        self.assertEqual(network.refresh()['state'], 'unavailable')
        self.client.write_text('')
        self.script.unlink()
        self.assertEqual(network.refresh()['state'], 'unavailable')
        self.assertEqual(self.calls, [])
        self.script.write_text('')
        network.runner = lambda arguments, timeout: completed(arguments, 0, 'not json')
        self.assertEqual(network.refresh()['state'], 'error')
        network.runner = lambda arguments, timeout: completed(arguments, 0, json.dumps({'rules': [{'name': 'x', 'state': 'ready'}]}))
        self.assertIn('unexpected result', network.refresh()['message'])
        network.runner = lambda arguments, timeout: completed(arguments, 1, '', 'Get-NetFirewallRule : failed')
        self.assertIn('Get-NetFirewallRule : failed', network.refresh()['message'])

    def test_default_programs(self):
        network = client_network.ClientNetwork(auto_refresh=False, script=self.script)
        self.assertEqual(network.python_executable, Path(sys.executable))
        self.assertTrue(network._windows_arguments('Check')[-3].endswith('VROSTBridgeClient.exe'))

    def test_uninstaller_removes_only_the_owned_rules(self):
        runner = mock.Mock(return_value=completed([], 0, 'Removed only the firewall rules created by the VROSTBridge client.'))
        result = client_network.remove_windows_rules(self.script, runner)
        self.assertTrue(result['ok'])
        arguments = runner.call_args[0][0]
        self.assertEqual(arguments[-4:], ['-File', str(self.script), '-Action', 'RemoveFirewall'])
        self.assertNotIn('-ClientExecutable', arguments)
        runner.return_value = completed([], client_network.ERROR_CANCELLED)
        self.assertTrue(client_network.remove_windows_rules(self.script, runner)['declined'])
        runner.return_value = completed([], 1, '', 'Firewall rule x does not carry the VROSTBridge ownership marker.')
        self.assertIn('ownership marker', client_network.remove_windows_rules(self.script, runner)['message'])
        runner.side_effect = subprocess.TimeoutExpired('powershell', 600)
        self.assertFalse(client_network.remove_windows_rules(self.script, runner)['ok'])

    def test_system_commands_open_no_console_window(self):
        with mock.patch.object(client_network.subprocess, 'run', return_value=completed([], 0)) as run:
            client_network.system_run(['arp', '-a'], 3)
        options = run.call_args[1]
        self.assertEqual((options['creationflags'], options['encoding'], options['errors'], options['timeout']),
                         (0x08000000, 'utf-8', 'replace', 3))
        self.assertNotIn('env', options)

    def test_private_targets_accept_aliases(self):
        self.assertEqual(client_network.private_targets([{'address': '192.168.1.25', 'interface': 'Wi-Fi 2', 'prefix': 24,
                                                          'physical': True, 'connected': True},
                                                         {'address': '192.168.1.26', 'interface': 'Wi-Fi;2', 'prefix': 24,
                                                          'physical': True, 'connected': True}]),
                         [{'interface': 'Wi-Fi 2', 'subnet': '192.168.1.0/24'}])


class FirewallScriptTests(unittest.TestCase):
    TEXT = client_network.FIREWALL_SCRIPT.read_text(encoding='ascii')

    def test_ascii_for_windows_powershell(self):
        client_network.FIREWALL_SCRIPT.read_bytes().decode('ascii')

    def test_rules_match_the_python_side_and_never_the_hosts(self):
        names = "@('" + "','".join(client_network.WINDOWS_RULES) + "')"
        self.assertIn('$ruleNames=' + names, self.TEXT)
        for name in client_network.WINDOWS_RULES:
            self.assertIn("Name='" + name + "'", self.TEXT)
        self.assertNotIn("'VROSTBridge.VR.", self.TEXT)
        self.assertNotIn("'VROSTBridge.Session.", self.TEXT)
        self.assertIn("$ownership='VROSTBridge client private LAN streaming and discovery'", self.TEXT)

    def test_rules_are_private_local_subnet_inbound_allow_per_program(self):
        self.assertIn('-Program $expected.Program -Direction Inbound -Action Allow -Protocol $expected.Protocol '
                      '-LocalPort $expected.Ports -Profile Private -RemoteAddress LocalSubnet', self.TEXT)
        for line in ("Protocol='TCP';Ports=@('9943');Program=$ClientExecutable",
                     "Protocol='UDP';Ports=@('9944');Program=$ClientExecutable",
                     "Protocol='UDP';Ports=@('5353');Program=$ClientExecutable",
                     "Protocol='TCP';Ports=@('9945');Program=$PythonExecutable",
                     "Protocol='UDP';Ports=@('9944','9946');Program=$PythonExecutable"):
            self.assertIn(line, self.TEXT)
        self.assertEqual(network_test.STREAM_PORT, 9944)  # the network-test receiver the companion rule covers

    def test_rules_open_exactly_the_bazzite_clients_ports(self):
        # 1.0.0.7: no wider than the Bazzite client's firewall (client_network.PORTS), which hardware has proven.
        opened = set()
        for protocol, ports in re.findall(r"Protocol='(TCP|UDP)';Ports=@\(([^)]*)\)", self.TEXT):
            opened |= {(int(port.strip("'")), protocol.lower()) for port in ports.split(',')}
        self.assertEqual(opened, set(client_network.PORTS))

    def test_elevation_like_host_setup(self):
        for part in ("[ValidateSet('Check','Firewall','RemoveFirewall')]", "$info.Verb='runas'", '-EncodedCommand',
                     '[Text.Encoding]::Unicode.GetBytes', 'NativeErrorCode -eq 1223) {exit 1223}',
                     '[System.Diagnostics.ProcessWindowStyle]::Hidden', 'Test-Administrator',
                     'if ($rule -and $rule.Description -ne $ownership) {throw'):
            self.assertIn(part, self.TEXT)
        self.assertNotIn('Invoke-Expression', self.TEXT)


class LinuxUnchangedTests(unittest.TestCase):
    """The Linux paths do not touch windows_network."""

    def setUp(self):
        patcher = mock.patch.object(wn, '_api', mock.NonCallableMock(spec=[]))  # any Windows call fails
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sys, 'platform', 'linux')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_network_addresses_and_pinning(self):
        with mock.patch.object(session_link, 'lan_addresses', return_value=['linux rows']):
            self.assertEqual(session_link.network_addresses(), ['linux rows'])
        probe = mock.Mock()
        with mock.patch('launcher.session_link.socket.if_nametoindex', return_value=9):
            session_link.pin_ipv4_interface(probe, 'wlp1s0')
        probe.setsockopt.assert_called_once_with(socket.IPPROTO_IP, getattr(socket, 'IP_UNICAST_IF', 50), b'\0\0\0\x09')

    def test_other_platforms_still_refuse_pinning(self):
        with mock.patch.object(sys, 'platform', 'darwin'), self.assertRaises(session_link.SessionError):
            session_link.pin_ipv4_interface(mock.Mock(), 'en0')

    def test_identity_path_and_modules(self):
        with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/custom-config'}):
            self.assertEqual(client_identity.config_path(), Path('/tmp/custom-config/ALVR Client/session.json'))
        self.assertEqual(connection_kind.describe('Wi-Fi 2')['summary'], 'No network adapter is selected yet.')
        self.assertEqual(network_test.link_info('Wi-Fi 2')['kind'], 'unknown')
        self.assertEqual(network_test.path_key('h', 'enp0s31f6-long-name'), 'h|enp0s31f6-long-')
        self.assertFalse(client_network._valid_interface('Wi-Fi 2'))
        self.assertFalse(wifi_diagnostics._valid_interface('Wi-Fi 2'))
        self.assertFalse(wifi_others._valid_name('Wi-Fi 2'))


if __name__ == '__main__':
    unittest.main()
