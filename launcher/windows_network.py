"""Windows network facts for the headset-PC companion: IP Helper and Native Wifi through ctypes.

Stdlib only: the Windows companion runs on the embedded Python in runtime\\python. Importing this module
never touches a Windows DLL, so it imports (and its parsers are tested) on Linux; every DLL is loaded on
first use. Structures use fixed-size ctypes types (c_uint32 for ULONG/DWORD, c_uint16 for WCHAR, c_void_p
for pointers), so their layouts are the Win64 ones whatever platform builds them.

Every call into Windows goes through the module-level `_api` (NativeApi); tests replace it with a fake.
Helpers fail soft: they raise WindowsNetworkError (an OSError) with a short reason, or return a dict
with a 'reason', which callers turn into their own 'unavailable' status.

Windows APIs used (Microsoft documentation names):
- GetAdaptersAddresses, IP_ADAPTER_ADDRESSES_LH, IP_ADAPTER_UNICAST_ADDRESS_LH, IP_ADAPTER_GATEWAY_ADDRESS_LH
  (iphlpapi.h, iptypes.h)
- GetIfEntry2, MIB_IF_ROW2 (netioapi.h): ConnectorPresent ("TRUE if this is a physical adapter", the
  property `Get-NetAdapter -Physical` reports), MediaConnectState, PhysicalMediumType
- GetBestRoute2, MIB_IPFORWARD_ROW2, SOCKADDR_INET (netioapi.h)
- GetIpNetTable2, MIB_IPNET_TABLE2, MIB_IPNET_ROW2, FreeMibTable (netioapi.h)
- GetExtendedTcpTable / GetExtendedUdpTable, MIB_TCPROW_OWNER_PID, MIB_UDPROW_OWNER_PID (iphlpapi.h)
- WlanOpenHandle, WlanCloseHandle, WlanEnumInterfaces, WlanQueryInterface, WlanSetInterface,
  WlanGetNetworkBssList, WlanDisconnect, WlanConnect, WlanFreeMemory (wlanapi.h)
- PowerGetActiveScheme, PowerReadACValueIndex, PowerReadDCValueIndex (powersetting.h), LocalFree,
  GetSystemPowerStatus (winbase.h)
- The registry value HKLM\\SYSTEM\\CurrentControlSet\\Control\\Network\\{4D36E972-...}\\{adapter}\\Connection
  PnPInstanceId tells a USB adapter (USB\\VID_...) from a PCI one.

Windows 11 24H2 and later ask for location consent (a one-time prompt per program, then Settings > Privacy &
security > Location) before WlanQueryInterface(wlan_intf_opcode_current_connection), WlanGetNetworkBssList,
WlanGetAvailableNetworkList and WlanScan answer; without it they return ERROR_ACCESS_DENIED ("Wi-Fi access
location changes"). wlan_intf_opcode_realtime_connection_quality, made for streaming apps, gives rates,
signal and each link's frequency without that consent where Windows offers it; the RSSI, channel and
statistics opcodes are not gated. wifi_link() therefore uses the gated query only for the network name
and access point (BSSID), and reports 'location_blocked' when Windows refuses it.
"""
import copy
import ctypes
from ctypes import POINTER, Structure, Union, c_int32, c_uint8, c_uint16, c_uint32, c_uint64, c_void_p, sizeof
import ipaddress
import os
import socket
import struct
import threading
import time
import unicodedata
import uuid


class WindowsNetworkError(OSError):
    """A Windows network API is unavailable or refused; str() is a short reason, .code the Windows error."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- constants
AF_INET = 2
ERROR_SUCCESS = 0
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_HANDLE = 6
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_PARAMETER = 87
ERROR_BUFFER_OVERFLOW = 111
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_DATA = 232
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_NOT_FOUND = 1168
ERROR_NETWORK_UNREACHABLE = 1231
ERROR_HOST_UNREACHABLE = 1232
ERROR_INVALID_STATE = 5023

IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_IEEE80211 = 71
IF_TYPE_TUNNEL = 131
# NDIS_PHYSICAL_MEDIUM values that mean Wi-Fi (an adapter may still report IfType 6 for them).
WIRELESS_MEDIA = (1, 9)  # NdisPhysicalMediumWirelessLan, NdisPhysicalMediumNative802_11
# IF_OPER_STATUS, named like Linux operstate (both follow RFC 2863).
OPER_STATUS = {1: 'up', 2: 'down', 3: 'testing', 4: 'unknown', 5: 'dormant', 6: 'notpresent', 7: 'lowerlayerdown'}
IF_OPER_STATUS_UP = 1
# IP_DAD_STATE: IpDadStateInvalid 0 and IpDadStateDuplicate 2 are not assigned addresses.
DAD_ASSIGNED = (1, 3, 4)  # tentative, deprecated, preferred

# GetAdaptersAddresses flags (iptypes.h).
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
GAA_FLAG_INCLUDE_GATEWAYS = 0x0080

# NL_NEIGHBOR_STATE (nldef.h) as the Linux neighbour-state names launcher/wake_host.py ranks.
NEIGHBOUR_STATES = {0: 'FAILED', 1: 'INCOMPLETE', 2: 'PROBE', 3: 'DELAY', 4: 'STALE', 5: 'REACHABLE',
                    6: 'PERMANENT'}

TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1
# MIB_TCP_STATE (tcpmib.h) as the hexadecimal state codes of Linux /proc/net/tcp, so reports read the same.
TCP_STATES = {1: '07', 2: '0A', 3: '02', 4: '03', 5: '01', 6: '04', 7: '05', 8: '08', 9: '0B', 10: '09',
              11: '06', 12: '07'}

# WLAN_INTF_OPCODE (wlanapi.h). background_scan_enabled: "disabled if at least one client disables it ...
# enabled automatically" when the interface disconnects; media_streaming_mode: "enabled if at least one
# client enables it ... disabled automatically on disconnect". Both end with the client handle.
OPCODE_BACKGROUND_SCAN = 2
OPCODE_MEDIA_STREAMING = 3
OPCODE_INTERFACE_STATE = 6
OPCODE_CURRENT_CONNECTION = 7
OPCODE_CHANNEL_NUMBER = 8
OPCODE_REALTIME_QUALITY = 19   # wlan_intf_opcode_realtime_connection_quality: newer Windows 11 only
OPCODE_STATISTICS = 0x10000101
OPCODE_RSSI = 0x10000102
WLAN_CLIENT_VERSION = 2        # Windows Vista and later
WLAN_STATES = {0: 'not_ready', 1: 'connected', 2: 'ad_hoc', 3: 'disconnecting', 4: 'disconnected',
               5: 'associating', 6: 'discovering', 7: 'authenticating'}
WLAN_CONNECTED = 1
DOT11_BSS_TYPE_INFRASTRUCTURE = 1
WLAN_CONNECTION_MODE_PROFILE = 0
PHY_NAMES = {1: 'FHSS', 2: 'DSSS', 3: 'IR', 4: '802.11a', 5: '802.11b', 6: '802.11g', 7: '802.11n',
             8: '802.11ac', 9: '802.11ad', 10: '802.11ax', 11: '802.11be'}
WIDE_PHYS = ('802.11ax', '802.11be')  # the only PHY types that can use 6 GHz

# Power plan: Wireless Adapter Settings > Power Saving Mode (powercfg /q; 0 Maximum Performance .. 3 Maximum
# Power Saving). Windows uses it to ask the Wi-Fi adapter for 802.11 power saving.
WIFI_POWER_SUBGROUP = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'
WIFI_POWER_SETTING = '12bbebe6-58d6-4636-95bb-3217ef867c1a'
WIFI_POWER_MODES = ('Maximum Performance', 'Low Power Saving', 'Medium Power Saving', 'Maximum Power Saving')

NETWORK_CLASS_KEY = r'SYSTEM\CurrentControlSet\Control\Network\{4D36E972-E325-11CE-BFC1-08002BE10318}'
ADAPTER_CLASS_KEY = r'SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}'
ADAPTER_CACHE_SECONDS = 3.0

_REASONS = {
    ERROR_ACCESS_DENIED: 'Windows denied access',
    ERROR_INVALID_HANDLE: 'the Wi-Fi service handle is no longer valid',
    ERROR_NOT_SUPPORTED: 'the adapter driver does not support it',
    ERROR_INVALID_PARAMETER: 'this Windows version or adapter does not support it',
    ERROR_SERVICE_NOT_ACTIVE: 'the WLAN AutoConfig service is not running',
    ERROR_NOT_FOUND: 'not found',
    ERROR_INVALID_STATE: 'Wi-Fi is not connected',
}


def reason(code):
    """A short, human reason for a Windows error code."""
    return _REASONS.get(code, 'Windows error ' + str(code))


# --------------------------------------------------------------------------- structures
class GUID(Structure):
    _fields_ = [('Data1', c_uint32), ('Data2', c_uint16), ('Data3', c_uint16), ('Data4', c_uint8 * 8)]

    @classmethod
    def from_text(cls, text):
        return cls.from_buffer_copy(uuid.UUID(str(text)).bytes_le)

    def text(self):
        return '{' + str(uuid.UUID(bytes_le=bytes(self))).upper() + '}'


class SOCKADDR_IN(Structure):
    _fields_ = [('sin_family', c_uint16), ('sin_port', c_uint16), ('sin_addr', c_uint8 * 4),
                ('sin_zero', c_uint8 * 8)]


class SOCKADDR_IN6(Structure):
    _fields_ = [('sin6_family', c_uint16), ('sin6_port', c_uint16), ('sin6_flowinfo', c_uint32),
                ('sin6_addr', c_uint8 * 16), ('sin6_scope_id', c_uint32)]


class SOCKADDR_INET(Union):
    _fields_ = [('Ipv4', SOCKADDR_IN), ('Ipv6', SOCKADDR_IN6), ('si_family', c_uint16)]


class SOCKET_ADDRESS(Structure):
    _fields_ = [('lpSockaddr', c_void_p), ('iSockaddrLength', c_int32)]


class IP_ADAPTER_UNICAST_ADDRESS(Structure):
    """IP_ADAPTER_UNICAST_ADDRESS_LH; the leading ULONGLONG Alignment union is Length + Flags."""
    _fields_ = [('Length', c_uint32), ('Flags', c_uint32), ('Next', c_void_p), ('Address', SOCKET_ADDRESS),
                ('PrefixOrigin', c_int32), ('SuffixOrigin', c_int32), ('DadState', c_int32),
                ('ValidLifetime', c_uint32), ('PreferredLifetime', c_uint32), ('LeaseLifetime', c_uint32),
                ('OnLinkPrefixLength', c_uint8)]


class IP_ADAPTER_GATEWAY_ADDRESS(Structure):
    """IP_ADAPTER_GATEWAY_ADDRESS_LH."""
    _fields_ = [('Length', c_uint32), ('Reserved', c_uint32), ('Next', c_void_p), ('Address', SOCKET_ADDRESS)]


class IP_ADAPTER_ADDRESSES(Structure):
    """IP_ADAPTER_ADDRESSES_LH (Windows Vista and later). The Flags bit-field union is one ULONG."""
    _fields_ = [('Length', c_uint32), ('IfIndex', c_uint32), ('Next', c_void_p), ('AdapterName', c_void_p),
                ('FirstUnicastAddress', c_void_p), ('FirstAnycastAddress', c_void_p),
                ('FirstMulticastAddress', c_void_p), ('FirstDnsServerAddress', c_void_p), ('DnsSuffix', c_void_p),
                ('Description', c_void_p), ('FriendlyName', c_void_p), ('PhysicalAddress', c_uint8 * 8),
                ('PhysicalAddressLength', c_uint32), ('Flags', c_uint32), ('Mtu', c_uint32), ('IfType', c_uint32),
                ('OperStatus', c_int32), ('Ipv6IfIndex', c_uint32), ('ZoneIndices', c_uint32 * 16),
                ('FirstPrefix', c_void_p), ('TransmitLinkSpeed', c_uint64), ('ReceiveLinkSpeed', c_uint64),
                ('FirstWinsServerAddress', c_void_p), ('FirstGatewayAddress', c_void_p), ('Ipv4Metric', c_uint32),
                ('Ipv6Metric', c_uint32), ('Luid', c_uint64), ('Dhcpv4Server', SOCKET_ADDRESS),
                ('CompartmentId', c_uint32), ('NetworkGuid', GUID), ('ConnectionType', c_int32),
                ('TunnelType', c_int32), ('Dhcpv6Server', SOCKET_ADDRESS), ('Dhcpv6ClientDuid', c_uint8 * 130),
                ('Dhcpv6ClientDuidLength', c_uint32), ('Dhcpv6Iaid', c_uint32), ('FirstDnsSuffix', c_void_p)]


class MIB_IF_ROW2(Structure):
    """MIB_IF_ROW2. InterfaceAndOperStatusFlags is a byte of BOOLEAN bit fields, lowest bit first (MSVC):
    HardwareInterface, FilterInterface, ConnectorPresent, NotAuthenticated, NotMediaConnected, Paused,
    LowPower, EndPointInterface."""
    _fields_ = [('InterfaceLuid', c_uint64), ('InterfaceIndex', c_uint32), ('InterfaceGuid', GUID),
                ('Alias', c_uint16 * 257), ('Description', c_uint16 * 257), ('PhysicalAddressLength', c_uint32),
                ('PhysicalAddress', c_uint8 * 32), ('PermanentPhysicalAddress', c_uint8 * 32), ('Mtu', c_uint32),
                ('Type', c_uint32), ('TunnelType', c_int32), ('MediaType', c_int32), ('PhysicalMediumType', c_int32),
                ('AccessType', c_int32), ('DirectionType', c_int32), ('InterfaceAndOperStatusFlags', c_uint8),
                ('OperStatus', c_int32), ('AdminStatus', c_int32), ('MediaConnectState', c_int32),
                ('NetworkGuid', GUID), ('ConnectionType', c_int32), ('TransmitLinkSpeed', c_uint64),
                ('ReceiveLinkSpeed', c_uint64)] + [(name, c_uint64) for name in (
                    'InOctets', 'InUcastPkts', 'InNUcastPkts', 'InDiscards', 'InErrors', 'InUnknownProtos',
                    'InUcastOctets', 'InMulticastOctets', 'InBroadcastOctets', 'OutOctets', 'OutUcastPkts',
                    'OutNUcastPkts', 'OutDiscards', 'OutErrors', 'OutUcastOctets', 'OutMulticastOctets',
                    'OutBroadcastOctets', 'OutQLen')]


IF_FLAG_HARDWARE, IF_FLAG_CONNECTOR_PRESENT, IF_FLAG_NOT_MEDIA_CONNECTED = 0x01, 0x04, 0x10


class IP_ADDRESS_PREFIX(Structure):
    _fields_ = [('Prefix', SOCKADDR_INET), ('PrefixLength', c_uint8)]


class MIB_IPFORWARD_ROW2(Structure):
    _fields_ = [('InterfaceLuid', c_uint64), ('InterfaceIndex', c_uint32), ('DestinationPrefix', IP_ADDRESS_PREFIX),
                ('NextHop', SOCKADDR_INET), ('SitePrefixLength', c_uint8), ('ValidLifetime', c_uint32),
                ('PreferredLifetime', c_uint32), ('Metric', c_uint32), ('Protocol', c_int32), ('Loopback', c_uint8),
                ('AutoconfigureAddress', c_uint8), ('Publish', c_uint8), ('Immortal', c_uint8), ('Age', c_uint32),
                ('Origin', c_int32)]


class MIB_IPNET_ROW2(Structure):
    """MIB_IPNET_ROW2; Flags is the IsRouter (bit 0) / IsUnreachable (bit 1) byte."""
    _fields_ = [('Address', SOCKADDR_INET), ('InterfaceIndex', c_uint32), ('InterfaceLuid', c_uint64),
                ('PhysicalAddress', c_uint8 * 32), ('PhysicalAddressLength', c_uint32), ('State', c_int32),
                ('Flags', c_uint8), ('ReachabilityTime', c_uint32)]


class MIB_IPNET_TABLE2(Structure):
    _fields_ = [('NumEntries', c_uint32), ('Table', MIB_IPNET_ROW2 * 1)]


class MIB_TCPROW_OWNER_PID(Structure):
    _fields_ = [('dwState', c_uint32), ('dwLocalAddr', c_uint32), ('dwLocalPort', c_uint32),
                ('dwRemoteAddr', c_uint32), ('dwRemotePort', c_uint32), ('dwOwningPid', c_uint32)]


class MIB_TCPTABLE_OWNER_PID(Structure):
    _fields_ = [('dwNumEntries', c_uint32), ('table', MIB_TCPROW_OWNER_PID * 1)]


class MIB_UDPROW_OWNER_PID(Structure):
    _fields_ = [('dwLocalAddr', c_uint32), ('dwLocalPort', c_uint32), ('dwOwningPid', c_uint32)]


class MIB_UDPTABLE_OWNER_PID(Structure):
    _fields_ = [('dwNumEntries', c_uint32), ('table', MIB_UDPROW_OWNER_PID * 1)]


class WLAN_INTERFACE_INFO(Structure):
    _fields_ = [('InterfaceGuid', GUID), ('strInterfaceDescription', c_uint16 * 256), ('isState', c_int32)]


class WLAN_INTERFACE_INFO_LIST(Structure):
    _fields_ = [('dwNumberOfItems', c_uint32), ('dwIndex', c_uint32), ('InterfaceInfo', WLAN_INTERFACE_INFO * 1)]


class DOT11_SSID(Structure):
    _fields_ = [('uSSIDLength', c_uint32), ('ucSSID', c_uint8 * 32)]


class WLAN_ASSOCIATION_ATTRIBUTES(Structure):
    """ulRxRate / ulTxRate are in kbit/s; wlanSignalQuality is 0-100 (-100 to -50 dBm)."""
    _fields_ = [('dot11Ssid', DOT11_SSID), ('dot11BssType', c_int32), ('dot11Bssid', c_uint8 * 6),
                ('dot11PhyType', c_uint32), ('uDot11PhyIndex', c_uint32), ('wlanSignalQuality', c_uint32),
                ('ulRxRate', c_uint32), ('ulTxRate', c_uint32)]


class WLAN_SECURITY_ATTRIBUTES(Structure):
    _fields_ = [('bSecurityEnabled', c_int32), ('bOneXEnabled', c_int32), ('dot11AuthAlgorithm', c_int32),
                ('dot11CipherAlgorithm', c_int32)]


class WLAN_CONNECTION_ATTRIBUTES(Structure):
    _fields_ = [('isState', c_int32), ('wlanConnectionMode', c_int32), ('strProfileName', c_uint16 * 256),
                ('wlanAssociationAttributes', WLAN_ASSOCIATION_ATTRIBUTES),
                ('wlanSecurityAttributes', WLAN_SECURITY_ATTRIBUTES)]


class WLAN_MAC_FRAME_STATISTICS(Structure):
    _fields_ = [(name, c_uint64) for name in (
        'ullTransmittedFrameCount', 'ullReceivedFrameCount', 'ullWEPExcludedCount', 'ullTKIPLocalMICFailures',
        'ullTKIPReplays', 'ullTKIPICVErrorCount', 'ullCCMPReplays', 'ullCCMPDecryptErrors',
        'ullWEPUndecryptableCount', 'ullWEPICVErrorCount', 'ullDecryptSuccessCount', 'ullDecryptFailureCount')]


class WLAN_PHY_FRAME_STATISTICS(Structure):
    _fields_ = [(name, c_uint64) for name in (
        'ullTransmittedFrameCount', 'ullMulticastTransmittedFrameCount', 'ullFailedCount', 'ullRetryCount',
        'ullMultipleRetryCount', 'ullMaxTXLifetimeExceededCount', 'ullTransmittedFragmentCount',
        'ullRTSSuccessCount', 'ullRTSFailureCount', 'ullACKFailureCount', 'ullReceivedFrameCount',
        'ullMulticastReceivedFrameCount', 'ullPromiscuousReceivedFrameCount', 'ullMaxRXLifetimeExceededCount',
        'ullFrameDuplicateCount', 'ullReceivedFragmentCount', 'ullPromiscuousReceivedFragmentCount',
        'ullFCSErrorCount')]


class WLAN_STATISTICS(Structure):
    _fields_ = [('ullFourWayHandshakeFailures', c_uint64), ('ullTKIPCounterMeasuresInvoked', c_uint64),
                ('ullReserved', c_uint64), ('MacUcastCounters', WLAN_MAC_FRAME_STATISTICS),
                ('MacMcastCounters', WLAN_MAC_FRAME_STATISTICS), ('dwNumberOfPhys', c_uint32),
                ('PhyCounters', WLAN_PHY_FRAME_STATISTICS * 1)]


class WLAN_RATE_SET(Structure):
    _fields_ = [('uRateSetLength', c_uint32), ('usRateSet', c_uint16 * 126)]


class WLAN_BSS_ENTRY(Structure):
    """ulChCenterFrequency is in kHz."""
    _fields_ = [('dot11Ssid', DOT11_SSID), ('uPhyId', c_uint32), ('dot11Bssid', c_uint8 * 6),
                ('dot11BssType', c_int32), ('dot11BssPhyType', c_uint32), ('lRssi', c_int32),
                ('uLinkQuality', c_uint32), ('bInRegDomain', c_uint8), ('usBeaconPeriod', c_uint16),
                ('ullTimestamp', c_uint64), ('ullHostTimestamp', c_uint64), ('usCapabilityInformation', c_uint16),
                ('ulChCenterFrequency', c_uint32), ('wlanRateSet', WLAN_RATE_SET), ('ulIeOffset', c_uint32),
                ('ulIeSize', c_uint32)]


class WLAN_BSS_LIST(Structure):
    _fields_ = [('dwTotalSize', c_uint32), ('dwNumberOfItems', c_uint32), ('wlanBssEntries', WLAN_BSS_ENTRY * 1)]


class WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO(Structure):
    """The documentation calls ulChannelCenterFrequencyMhz kHz in its text; frequency_mhz() accepts either."""
    _fields_ = [('ucLinkID', c_uint8), ('ulChannelCenterFrequencyMhz', c_uint32), ('ulBandwidth', c_uint32),
                ('lRssi', c_int32), ('wlanRateSet', WLAN_RATE_SET)]


class WLAN_REALTIME_CONNECTION_QUALITY(Structure):
    _fields_ = [('dot11PhyType', c_uint32), ('ulLinkQuality', c_uint32), ('ulRxRate', c_uint32),
                ('ulTxRate', c_uint32), ('bIsMLOConnection', c_int32), ('ulNumLinks', c_uint32),
                ('linksInfo', WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO * 1)]


class WLAN_CONNECTION_PARAMETERS(Structure):
    _fields_ = [('wlanConnectionMode', c_int32), ('strProfile', c_void_p), ('pDot11Ssid', c_void_p),
                ('pDesiredBssidList', c_void_p), ('dot11BssType', c_int32), ('dwFlags', c_uint32)]


class SYSTEM_POWER_STATUS(Structure):
    _fields_ = [('ACLineStatus', c_uint8), ('BatteryFlag', c_uint8), ('BatteryLifePercent', c_uint8),
                ('SystemStatusFlag', c_uint8), ('BatteryLifeTime', c_uint32), ('BatteryFullLifeTime', c_uint32)]


# (restype, argtypes) per exported function; set when the function is first used.
_PROTOTYPES = {
    ('iphlpapi', 'GetAdaptersAddresses'): (c_uint32, [c_uint32, c_uint32, c_void_p, c_void_p, POINTER(c_uint32)]),
    ('iphlpapi', 'GetIfEntry2'): (c_uint32, [POINTER(MIB_IF_ROW2)]),
    ('iphlpapi', 'GetBestRoute2'): (c_uint32, [POINTER(c_uint64), c_uint32, POINTER(SOCKADDR_INET),
                                               POINTER(SOCKADDR_INET), c_uint32, POINTER(MIB_IPFORWARD_ROW2),
                                               POINTER(SOCKADDR_INET)]),
    ('iphlpapi', 'GetIpNetTable2'): (c_uint32, [c_uint16, POINTER(c_void_p)]),
    ('iphlpapi', 'FreeMibTable'): (None, [c_void_p]),
    ('iphlpapi', 'GetExtendedTcpTable'): (c_uint32, [c_void_p, POINTER(c_uint32), c_int32, c_uint32, c_uint32,
                                                     c_uint32]),
    ('iphlpapi', 'GetExtendedUdpTable'): (c_uint32, [c_void_p, POINTER(c_uint32), c_int32, c_uint32, c_uint32,
                                                     c_uint32]),
    ('wlanapi', 'WlanOpenHandle'): (c_uint32, [c_uint32, c_void_p, POINTER(c_uint32), POINTER(c_void_p)]),
    ('wlanapi', 'WlanCloseHandle'): (c_uint32, [c_void_p, c_void_p]),
    ('wlanapi', 'WlanEnumInterfaces'): (c_uint32, [c_void_p, c_void_p, POINTER(c_void_p)]),
    ('wlanapi', 'WlanQueryInterface'): (c_uint32, [c_void_p, POINTER(GUID), c_uint32, c_void_p, POINTER(c_uint32),
                                                   POINTER(c_void_p), POINTER(c_uint32)]),
    ('wlanapi', 'WlanSetInterface'): (c_uint32, [c_void_p, POINTER(GUID), c_uint32, c_uint32, c_void_p, c_void_p]),
    ('wlanapi', 'WlanGetNetworkBssList'): (c_uint32, [c_void_p, POINTER(GUID), c_void_p, c_uint32, c_int32,
                                                      c_void_p, POINTER(c_void_p)]),
    ('wlanapi', 'WlanDisconnect'): (c_uint32, [c_void_p, POINTER(GUID), c_void_p]),
    ('wlanapi', 'WlanConnect'): (c_uint32, [c_void_p, POINTER(GUID), POINTER(WLAN_CONNECTION_PARAMETERS), c_void_p]),
    ('wlanapi', 'WlanFreeMemory'): (None, [c_void_p]),
    ('powrprof', 'PowerGetActiveScheme'): (c_uint32, [c_void_p, POINTER(POINTER(GUID))]),
    ('powrprof', 'PowerReadACValueIndex'): (c_uint32, [c_void_p, POINTER(GUID), POINTER(GUID), POINTER(GUID),
                                                       POINTER(c_uint32)]),
    ('powrprof', 'PowerReadDCValueIndex'): (c_uint32, [c_void_p, POINTER(GUID), POINTER(GUID), POINTER(GUID),
                                                       POINTER(c_uint32)]),
    ('kernel32', 'LocalFree'): (c_void_p, [c_void_p]),
    ('kernel32', 'GetSystemPowerStatus'): (c_int32, [POINTER(SYSTEM_POWER_STATUS)]),
}


# --------------------------------------------------------------------------- small conversions
def _wide_string(address, limit=1024):
    """A NUL-terminated UTF-16 string at address (PWCHAR), without reading past its terminator."""
    if not address:
        return ''
    if sizeof(ctypes.c_wchar) == 2:  # Windows: wchar_t is UTF-16
        return ctypes.wstring_at(address)[:limit]
    units = []
    for index in range(limit):
        unit = c_uint16.from_address(address + 2 * index).value
        if not unit:
            break
        units.append(unit)
    return struct.pack('<%dH' % len(units), *units).decode('utf-16-le', 'replace')


def _wide_array(array):
    """A fixed WCHAR[] field as text, up to its first NUL."""
    return bytes(array).decode('utf-16-le', 'replace').split('\0', 1)[0]


def _mac(raw, length=6):
    data = bytes(raw)[:length]
    return ':'.join('%02x' % value for value in data) if len(data) == 6 else ''


def _ipv4_of(value):
    """Text of a SOCKADDR_INET holding IPv4, else ''."""
    if value.si_family != AF_INET:
        return ''
    return socket.inet_ntoa(bytes(value.Ipv4.sin_addr))


def _sockaddr_inet(text):
    value = SOCKADDR_INET()
    value.Ipv4.sin_family = AF_INET
    value.Ipv4.sin_addr[:] = list(socket.inet_aton(str(ipaddress.IPv4Address(text))))
    return value


def _dword_ipv4(value):
    """A DWORD holding an IPv4 address in network byte order (as the tables store it)."""
    return socket.inet_ntoa(struct.pack('<I', value & 0xFFFFFFFF))


def _dword_port(value):
    """The low 16 bits of a DWORD holding a port in network byte order."""
    return struct.unpack('>H', struct.pack('<H', value & 0xFFFF))[0]


def _usable_ipv4(value):
    """launcher.client_identity.usable_ipv4 (not imported: that module imports this one)."""
    try:
        address = ipaddress.IPv4Address(value)
        return not (address.is_loopback or address.is_unspecified or address.is_multicast
                    or address.is_link_local or address.is_reserved)
    except (ValueError, TypeError):
        return False


def frequency_mhz(value):
    """MHz from a channel centre frequency given in kHz (WLAN_BSS_ENTRY) or MHz; None if implausible."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    mhz = value / 1000.0 if value >= 1000000 else float(value)
    return int(round(mhz)) if 2000 <= mhz <= 8000 else None


def band_from_frequency(mhz):
    if not isinstance(mhz, (int, float)) or mhz <= 0:
        return ''
    if mhz < 3000:
        return '2.4 GHz'
    if mhz < 5925:
        return '5 GHz'
    return '6 GHz'


def band_from_channel(channel, phy=''):
    """Band from the channel number alone, '' when it is ambiguous.

    6 GHz 20 MHz channels are 1, 5, 9 ... 233 and need 802.11ax/be, so 1-14 is 2.4 GHz unless the PHY could
    be 6 GHz on 1/5/9/13 (or 2), and channels 149-177 (one more than a multiple of four, like 6 GHz) are
    5 GHz unless the PHY could be 6 GHz. Other channels one more than a multiple of four are 6 GHz.
    """
    if type(channel) is not int or channel <= 0:
        return ''
    wide = phy in WIDE_PHYS
    if channel <= 14:
        return '' if wide and channel in (1, 2, 5, 9, 13) else '2.4 GHz'
    if channel % 4 == 1 and channel <= 233:
        if 149 <= channel <= 177:
            return '' if wide else '5 GHz'
        return '6 GHz'
    if channel <= 177:
        return '5 GHz'
    return '6 GHz' if channel <= 233 else ''


def quality_dbm(quality):
    """The documented mapping of WLAN signal quality 0-100 onto -100 to -50 dBm."""
    if type(quality) is not int or not 0 <= quality <= 100:
        return None
    return int(round(quality / 2.0 - 100))


_ALIAS_PUNCTUATION = frozenset(" -_.()#&+,'[]@*")


def valid_alias(value):
    """A Windows adapter alias ("Wi-Fi 2", "Ethernet 3", "WLAN", "イーサネット"): letters, digits and marks of any
    script, spaces and - _ . ( ) # & + , ' [ ] @ *, at most 128 characters, no leading or trailing space.
    Control characters, quotes, slashes, backslashes, |, ;, $, %, <, > and the like are refused; an alias
    is only ever a lookup key, never part of a command line."""
    if not isinstance(value, str) or not 0 < len(value) <= 128 or value != value.strip():
        return False
    for character in value:
        if character in _ALIAS_PUNCTUATION:
            continue
        if unicodedata.category(character)[0] not in 'LNM':
            return False
    return True


def same_guid(left, right):
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (ValueError, TypeError, AttributeError):
        return False


def system32(name):
    """A Windows tool by full path (like host.power.powershell_path), so a program elsewhere is never run."""
    root = os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    if root:
        candidate = os.path.join(root, 'System32', name)
        if os.path.isfile(candidate):
            return candidate
    return name


# --------------------------------------------------------------------------- parsers (also used by tests)
def walk_adapter_addresses(address, limit=256):
    """The IP_ADAPTER_ADDRESSES linked list at address as dicts (IPv4 unicast addresses and gateways)."""
    rows = []
    while address and len(rows) < limit:
        item = IP_ADAPTER_ADDRESSES.from_address(address)
        ipv4, gateways = [], []
        entry, count = item.FirstUnicastAddress, 0
        while entry and count < 64:
            count += 1
            unicast = IP_ADAPTER_UNICAST_ADDRESS.from_address(entry)
            pointer = unicast.Address.lpSockaddr
            if pointer and unicast.Address.iSockaddrLength >= sizeof(SOCKADDR_IN):
                value = SOCKADDR_IN.from_address(pointer)
                if value.sin_family == AF_INET:
                    prefix = unicast.OnLinkPrefixLength
                    ipv4.append({'address': socket.inet_ntoa(bytes(value.sin_addr)),
                                 'prefix': prefix if 0 < prefix <= 32 else None, 'dad_state': unicast.DadState})
            entry = unicast.Next
        entry, count = item.FirstGatewayAddress, 0
        while entry and count < 16:
            count += 1
            gateway = IP_ADAPTER_GATEWAY_ADDRESS.from_address(entry)
            pointer = gateway.Address.lpSockaddr
            if pointer and gateway.Address.iSockaddrLength >= sizeof(SOCKADDR_IN):
                value = SOCKADDR_IN.from_address(pointer)
                if value.sin_family == AF_INET:
                    gateways.append(socket.inet_ntoa(bytes(value.sin_addr)))
            entry = gateway.Next
        name = ctypes.string_at(item.AdapterName).decode('ascii', 'replace') if item.AdapterName else ''
        rows.append({'index': item.IfIndex, 'luid': item.Luid, 'guid': name[:64],
                     'alias': _wide_string(item.FriendlyName, 256), 'description': _wide_string(item.Description, 256),
                     'if_type': item.IfType, 'oper_status': item.OperStatus,
                     'mac': _mac(item.PhysicalAddress, item.PhysicalAddressLength), 'mtu': item.Mtu,
                     'transmit_bps': item.TransmitLinkSpeed, 'receive_bps': item.ReceiveLinkSpeed,
                     'connection_type': item.ConnectionType, 'tunnel_type': item.TunnelType,
                     'ipv4': ipv4, 'gateways': gateways})
        address = item.Next
    return rows


def parse_if_row(row):
    """GetIfEntry2 facts: physical (ConnectorPresent), hardware, media and admin state, physical medium."""
    flags = row.InterfaceAndOperStatusFlags
    return {'physical': bool(flags & IF_FLAG_CONNECTOR_PRESENT), 'hardware': bool(flags & IF_FLAG_HARDWARE),
            'media_connected': row.MediaConnectState == 1 if row.MediaConnectState in (1, 2) else None,
            'admin_up': row.AdminStatus == 1, 'physical_medium': row.PhysicalMediumType}


def parse_if_counters(row):
    """1.0.0.13: GetIfEntry2's packet, discard and error counters, named as the reports name them."""
    return {'rx_packets': row.InUcastPkts + row.InNUcastPkts, 'rx_discards': row.InDiscards, 'rx_errors': row.InErrors,
            'rx_unknown_protocols': row.InUnknownProtos, 'tx_packets': row.OutUcastPkts + row.OutNUcastPkts,
            'tx_discards': row.OutDiscards, 'tx_errors': row.OutErrors}


def route_from_row(row, source):
    """A GetBestRoute2 result: interface index, gateway ('' when on-link) and the chosen source address."""
    gateway = _ipv4_of(row.NextHop)
    prefix = _ipv4_of(row.DestinationPrefix.Prefix)
    return {'index': row.InterfaceIndex, 'luid': row.InterfaceLuid,
            'gateway': '' if gateway in ('', '0.0.0.0') else gateway, 'source': _ipv4_of(source),
            'destination_prefix': (prefix + '/' + str(row.DestinationPrefix.PrefixLength)) if prefix else '',
            'metric': row.Metric}


def parse_ipnet_table(address, limit=4096):
    """MIB_IPNET_TABLE2 at address: IPv4 neighbours with their MAC and Linux-named state."""
    count = min(c_uint32.from_address(address).value, limit)
    base = address + MIB_IPNET_TABLE2.Table.offset
    rows = []
    for index in range(count):
        row = MIB_IPNET_ROW2.from_address(base + index * sizeof(MIB_IPNET_ROW2))
        ip = _ipv4_of(row.Address)
        if not ip:
            continue
        rows.append({'address': ip, 'mac': _mac(row.PhysicalAddress, row.PhysicalAddressLength),
                     'state': NEIGHBOUR_STATES.get(row.State, ''), 'index': row.InterfaceIndex,
                     'router': bool(row.Flags & 1), 'unreachable': bool(row.Flags & 2)})
    return rows


def parse_tcp_table(raw):
    """MIB_TCPTABLE_OWNER_PID bytes as dicts."""
    size = sizeof(MIB_TCPROW_OWNER_PID)
    offset = MIB_TCPTABLE_OWNER_PID.table.offset
    if len(raw) < offset:
        return []
    count = min(struct.unpack_from('<I', raw)[0], (len(raw) - offset) // size)
    rows = []
    for index in range(count):
        row = MIB_TCPROW_OWNER_PID.from_buffer_copy(raw, offset + index * size)
        rows.append({'local_ip': _dword_ipv4(row.dwLocalAddr), 'local_port': _dword_port(row.dwLocalPort),
                     'peer_ip': _dword_ipv4(row.dwRemoteAddr), 'peer_port': _dword_port(row.dwRemotePort),
                     'state': row.dwState, 'pid': row.dwOwningPid})
    return rows


def parse_udp_table(raw):
    """MIB_UDPTABLE_OWNER_PID bytes as dicts."""
    size = sizeof(MIB_UDPROW_OWNER_PID)
    offset = MIB_UDPTABLE_OWNER_PID.table.offset
    if len(raw) < offset:
        return []
    count = min(struct.unpack_from('<I', raw)[0], (len(raw) - offset) // size)
    rows = []
    for index in range(count):
        row = MIB_UDPROW_OWNER_PID.from_buffer_copy(raw, offset + index * size)
        rows.append({'local_ip': _dword_ipv4(row.dwLocalAddr), 'local_port': _dword_port(row.dwLocalPort),
                     'pid': row.dwOwningPid})
    return rows


def parse_interface_list(address, limit=64):
    """WLAN_INTERFACE_INFO_LIST at address (from WlanEnumInterfaces)."""
    header = WLAN_INTERFACE_INFO_LIST.from_address(address)
    base = address + WLAN_INTERFACE_INFO_LIST.InterfaceInfo.offset
    rows = []
    for index in range(min(header.dwNumberOfItems, limit)):
        info = WLAN_INTERFACE_INFO.from_address(base + index * sizeof(WLAN_INTERFACE_INFO))
        rows.append({'guid': info.InterfaceGuid.text(), 'description': _wide_array(info.strInterfaceDescription),
                     'state': info.isState})
    return rows


def _structure(kind, raw, what):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < sizeof(kind):
        raise WindowsNetworkError('Windows returned incomplete ' + what + '.')
    return kind.from_buffer_copy(raw[:sizeof(kind)])


def parse_connection_attributes(raw):
    """WLAN_CONNECTION_ATTRIBUTES (wlan_intf_opcode_current_connection)."""
    value = _structure(WLAN_CONNECTION_ATTRIBUTES, raw, 'Wi-Fi connection details')
    association = value.wlanAssociationAttributes
    length = min(association.dot11Ssid.uSSIDLength, 32)
    return {'state': value.isState, 'profile': _wide_array(value.strProfileName),
            'ssid': bytes(association.dot11Ssid.ucSSID[:length]).decode('utf-8', 'replace'),
            'bssid': _mac(association.dot11Bssid), 'phy': PHY_NAMES.get(association.dot11PhyType, ''),
            'signal_quality': min(association.wlanSignalQuality, 100), 'rx_kbps': association.ulRxRate,
            'tx_kbps': association.ulTxRate, 'security': bool(value.wlanSecurityAttributes.bSecurityEnabled),
            'enterprise': bool(value.wlanSecurityAttributes.bOneXEnabled)}


def parse_realtime_quality(raw, limit=16):
    """WLAN_REALTIME_CONNECTION_QUALITY with its per-link entries (Wi-Fi 7 multi-link has several)."""
    offset = WLAN_REALTIME_CONNECTION_QUALITY.linksInfo.offset
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < offset:
        raise WindowsNetworkError('Windows returned incomplete Wi-Fi quality details.')
    head = WLAN_REALTIME_CONNECTION_QUALITY.from_buffer_copy(
        bytes(raw[:sizeof(WLAN_REALTIME_CONNECTION_QUALITY)]).ljust(sizeof(WLAN_REALTIME_CONNECTION_QUALITY), b'\0'))
    size = sizeof(WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO)
    links = []
    for index in range(min(head.ulNumLinks, (len(raw) - offset) // size, limit)):
        link = WLAN_REALTIME_CONNECTION_QUALITY_LINK_INFO.from_buffer_copy(raw, offset + index * size)
        mhz = frequency_mhz(link.ulChannelCenterFrequencyMhz)
        links.append({'link_id': link.ucLinkID, 'freq_mhz': mhz, 'band': band_from_frequency(mhz),
                      'bandwidth': link.ulBandwidth, 'signal_dbm': link.lRssi if -120 < link.lRssi < 0 else None})
    return {'phy': PHY_NAMES.get(head.dot11PhyType, ''), 'signal_quality': min(head.ulLinkQuality, 100),
            'rx_kbps': head.ulRxRate, 'tx_kbps': head.ulTxRate, 'mlo': bool(head.bIsMLOConnection), 'links': links}


def parse_statistics(raw):
    """WLAN_STATISTICS summed over the adapter's PHYs; {} when the driver reports nothing."""
    head = _structure(WLAN_STATISTICS, raw, 'Wi-Fi statistics')
    offset, size = WLAN_STATISTICS.PhyCounters.offset, sizeof(WLAN_PHY_FRAME_STATISTICS)
    phys = [WLAN_PHY_FRAME_STATISTICS.from_buffer_copy(raw, offset + index * size)
            for index in range(min(head.dwNumberOfPhys, (len(raw) - offset) // size, 8))]
    totals = {'tx_packets': sum(phy.ullTransmittedFrameCount for phy in phys),
              'tx_retries': sum(phy.ullRetryCount for phy in phys),
              'tx_failed': sum(phy.ullFailedCount for phy in phys),
              'rx_packets': sum(phy.ullReceivedFrameCount for phy in phys),
              'ack_failures': sum(phy.ullACKFailureCount for phy in phys),
              'fcs_errors': sum(phy.ullFCSErrorCount for phy in phys)}
    return totals if any(totals.values()) else {}


def parse_bss_list(raw, limit=512):
    """WLAN_BSS_LIST bytes: access points with their channel centre frequency."""
    offset, size = WLAN_BSS_LIST.wlanBssEntries.offset, sizeof(WLAN_BSS_ENTRY)
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < offset:
        return []
    count = min(struct.unpack_from('<II', raw)[1], (len(raw) - offset) // size, limit)
    rows = []
    for index in range(count):
        entry = WLAN_BSS_ENTRY.from_buffer_copy(raw, offset + index * size)
        mhz = frequency_mhz(entry.ulChCenterFrequency)
        rows.append({'bssid': _mac(entry.dot11Bssid), 'freq_mhz': mhz, 'band': band_from_frequency(mhz),
                     'rssi': entry.lRssi, 'link_quality': entry.uLinkQuality,
                     'phy': PHY_NAMES.get(entry.dot11BssPhyType, '')})
    return rows


def parse_bool(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        raise WindowsNetworkError('Windows returned no value.')
    return struct.unpack_from('<i', raw)[0] != 0


def parse_ulong(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        raise WindowsNetworkError('Windows returned no value.')
    return struct.unpack_from('<I', raw)[0]


def parse_long(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        raise WindowsNetworkError('Windows returned no value.')
    return struct.unpack_from('<i', raw)[0]


# --------------------------------------------------------------------------- the Windows calls
class NativeApi:
    """Windows DLL calls. Each method raises WindowsNetworkError when Windows cannot answer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._dlls = {}
        self._functions = {}
        self._devices = {}

    def _function(self, library, name):
        with self._lock:
            function = self._functions.get((library, name))
            if function is not None:
                return function
            dll = self._dlls.get(library)
            if dll is None:
                loader = getattr(ctypes, 'WinDLL', None)
                if loader is None:
                    raise WindowsNetworkError('Windows network information is only available on Windows.')
                try:
                    dll = loader(library, use_last_error=True)
                except OSError as error:
                    raise WindowsNetworkError(library + '.dll could not be loaded (' + str(error)[:120] + ').') from None
                self._dlls[library] = dll
            try:
                function = getattr(dll, name)
            except AttributeError:
                raise WindowsNetworkError(name + ' is not available on this Windows version.') from None
            function.restype, function.argtypes = _PROTOTYPES[(library, name)]
            self._functions[(library, name)] = function
            return function

    # ---- IP Helper
    def adapters(self):
        """GetAdaptersAddresses(AF_INET), each adapter with its GetIfEntry2 facts and PnP device id."""
        function = self._function('iphlpapi', 'GetAdaptersAddresses')
        flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER | GAA_FLAG_INCLUDE_GATEWAYS
        size = c_uint32(15000)  # the documented starting size
        for _ in range(4):
            buffer = (c_uint64 * ((size.value + 7) // 8))()  # 8-byte aligned, as the API requires
            code = function(AF_INET, flags, None, buffer, ctypes.byref(size))
            if code != ERROR_BUFFER_OVERFLOW:
                break
        if code == ERROR_NO_DATA:
            return []
        if code != ERROR_SUCCESS:
            raise WindowsNetworkError('Windows did not list the network adapters (' + reason(code) + ').', code)
        rows = walk_adapter_addresses(ctypes.addressof(buffer))
        for row in rows:
            row.update(self.interface_facts(row['luid']))
            row['pnp_id'] = self.device_id(row['guid'])
        return rows

    def interface_facts(self, luid):
        """GetIfEntry2 for one adapter; {} if Windows does not answer (callers fall back to IfType)."""
        try:
            function = self._function('iphlpapi', 'GetIfEntry2')
        except WindowsNetworkError:
            return {}
        row = MIB_IF_ROW2()
        row.InterfaceLuid = luid
        if function(ctypes.byref(row)) != ERROR_SUCCESS:
            return {}
        return parse_if_row(row)

    def interface_counters(self, luid):
        """1.0.0.13: GetIfEntry2's counters for one adapter, read now; {} if Windows does not answer."""
        try:
            function = self._function('iphlpapi', 'GetIfEntry2')
        except WindowsNetworkError:
            return {}
        row = MIB_IF_ROW2()
        row.InterfaceLuid = luid
        if function(ctypes.byref(row)) != ERROR_SUCCESS:
            return {}
        return parse_if_counters(row)

    def device_id(self, guid):
        """The adapter's PnP instance id (USB\\..., PCI\\...), '' if unknown; cached per adapter."""
        if not guid:
            return ''
        if guid in self._devices:
            return self._devices[guid]
        value = ''
        try:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_CLASS_KEY + '\\' + guid + '\\Connection') as key:
                    value = winreg.QueryValueEx(key, 'PnPInstanceId')[0]
            except OSError:
                value = self._class_device_id(winreg, guid)
        except ImportError:
            value = ''
        value = value if isinstance(value, str) else ''
        self._devices[guid] = value
        return value

    @staticmethod
    def _class_device_id(winreg, guid):
        """The network adapter class key whose NetCfgInstanceId is this adapter: its device or hardware id."""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ADAPTER_CLASS_KEY) as root:
                for index in range(512):
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, name) as key:
                            if not same_guid(winreg.QueryValueEx(key, 'NetCfgInstanceId')[0], guid):
                                continue
                            for field in ('DeviceInstanceID', 'MatchingDeviceId', 'ComponentId'):
                                try:
                                    return str(winreg.QueryValueEx(key, field)[0])
                                except OSError:
                                    continue
                            return ''
                    except OSError:
                        continue
        except OSError:
            pass
        return ''

    def best_route(self, destination, source=None, interface_index=0):
        """GetBestRoute2 towards destination (optionally from source / through one interface); None: no route."""
        function = self._function('iphlpapi', 'GetBestRoute2')
        target = _sockaddr_inet(destination)
        origin = _sockaddr_inet(source) if source else None
        row, chosen = MIB_IPFORWARD_ROW2(), SOCKADDR_INET()
        code = function(None, int(interface_index or 0), ctypes.byref(origin) if origin is not None else None,
                        ctypes.byref(target), 0, ctypes.byref(row), ctypes.byref(chosen))
        if code in (ERROR_NOT_FOUND, ERROR_NETWORK_UNREACHABLE, ERROR_HOST_UNREACHABLE):
            return None
        if code != ERROR_SUCCESS:
            raise WindowsNetworkError('Windows did not report the route (' + reason(code) + ').', code)
        return route_from_row(row, chosen)

    def neighbours(self):
        """GetIpNetTable2(AF_INET)."""
        function = self._function('iphlpapi', 'GetIpNetTable2')
        free = self._function('iphlpapi', 'FreeMibTable')
        table = c_void_p()
        code = function(AF_INET, ctypes.byref(table))
        if code == ERROR_NOT_FOUND:
            return []
        if code != ERROR_SUCCESS or not table.value:
            raise WindowsNetworkError('Windows did not list its neighbours (' + reason(code) + ').', code)
        try:
            return parse_ipnet_table(table.value)
        finally:
            free(table)

    def _owner_table(self, name, table_class, parser):
        function = self._function('iphlpapi', name)
        size = c_uint32(0)
        for _ in range(6):
            buffer = (c_uint8 * (size.value + 2048))()
            size.value = len(buffer)
            code = function(buffer, ctypes.byref(size), 0, AF_INET, table_class, 0)
            if code == ERROR_INSUFFICIENT_BUFFER:
                continue
            if code != ERROR_SUCCESS:
                raise WindowsNetworkError(name + ' failed (' + reason(code) + ').', code)
            return parser(bytes(buffer))
        raise WindowsNetworkError(name + ' kept growing.')

    def tcp_table(self):
        return self._owner_table('GetExtendedTcpTable', TCP_TABLE_OWNER_PID_ALL, parse_tcp_table)

    def udp_table(self):
        return self._owner_table('GetExtendedUdpTable', UDP_TABLE_OWNER_PID, parse_udp_table)

    # ---- Native Wifi
    def wlan_open(self):
        function = self._function('wlanapi', 'WlanOpenHandle')
        version, handle = c_uint32(), c_void_p()
        code = function(WLAN_CLIENT_VERSION, None, ctypes.byref(version), ctypes.byref(handle))
        if code != ERROR_SUCCESS:
            raise WindowsNetworkError('Wi-Fi details are unavailable (' + reason(code) + ').', code)
        return handle.value

    def wlan_close(self, handle):
        if handle:
            try:
                self._function('wlanapi', 'WlanCloseHandle')(handle, None)
            except WindowsNetworkError:
                pass

    def _free(self, pointer):
        self._function('wlanapi', 'WlanFreeMemory')(pointer)

    def wlan_interfaces(self, handle):
        pointer = c_void_p()
        code = self._function('wlanapi', 'WlanEnumInterfaces')(handle, None, ctypes.byref(pointer))
        if code != ERROR_SUCCESS or not pointer.value:
            raise WindowsNetworkError('Windows did not list its Wi-Fi adapters (' + reason(code) + ').', code)
        try:
            return parse_interface_list(pointer.value)
        finally:
            self._free(pointer)

    def wlan_query(self, handle, guid, opcode):
        """The raw WlanQueryInterface result for one opcode."""
        function = self._function('wlanapi', 'WlanQueryInterface')
        interface = GUID.from_text(guid)
        size, data, kind = c_uint32(), c_void_p(), c_uint32()
        code = function(handle, ctypes.byref(interface), opcode, None, ctypes.byref(size), ctypes.byref(data),
                        ctypes.byref(kind))
        if code != ERROR_SUCCESS or not data.value:
            raise WindowsNetworkError(reason(code), code)
        try:
            return ctypes.string_at(data.value, size.value)
        finally:
            self._free(data)

    def wlan_set(self, handle, guid, opcode, value):
        """WlanSetInterface with a BOOL; returns the Windows error code (0 on success)."""
        interface = GUID.from_text(guid)
        flag = c_int32(1 if value else 0)
        return self._function('wlanapi', 'WlanSetInterface')(handle, ctypes.byref(interface), opcode, sizeof(flag),
                                                             ctypes.byref(flag), None)

    def wlan_bss_list(self, handle, guid):
        """WlanGetNetworkBssList for every network (no SSID filter); raw WLAN_BSS_LIST bytes."""
        interface = GUID.from_text(guid)
        pointer = c_void_p()
        code = self._function('wlanapi', 'WlanGetNetworkBssList')(handle, ctypes.byref(interface), None,
                                                                  DOT11_BSS_TYPE_INFRASTRUCTURE, 0, None,
                                                                  ctypes.byref(pointer))
        if code != ERROR_SUCCESS or not pointer.value:
            raise WindowsNetworkError(reason(code), code)
        try:
            total = WLAN_BSS_LIST.from_address(pointer.value).dwTotalSize
            return ctypes.string_at(pointer.value, max(total, WLAN_BSS_LIST.wlanBssEntries.offset))
        finally:
            self._free(pointer)

    def wlan_disconnect(self, handle, guid):
        interface = GUID.from_text(guid)
        return self._function('wlanapi', 'WlanDisconnect')(handle, ctypes.byref(interface), None)

    def wlan_connect(self, handle, guid, profile):
        """WlanConnect with a saved profile (asynchronous: Windows connects in the background)."""
        interface = GUID.from_text(guid)
        name = ctypes.create_unicode_buffer(profile)
        parameters = WLAN_CONNECTION_PARAMETERS(wlanConnectionMode=WLAN_CONNECTION_MODE_PROFILE,
                                                strProfile=ctypes.addressof(name),
                                                dot11BssType=DOT11_BSS_TYPE_INFRASTRUCTURE)
        return self._function('wlanapi', 'WlanConnect')(handle, ctypes.byref(interface), ctypes.byref(parameters),
                                                       None)

    # ---- power plan
    def power_values(self, subgroup, setting):
        """(AC, DC) value index of one setting in the active power plan; None where unreadable."""
        scheme = POINTER(GUID)()
        code = self._function('powrprof', 'PowerGetActiveScheme')(None, ctypes.byref(scheme))
        if code != ERROR_SUCCESS or not scheme:
            raise WindowsNetworkError('The active power plan could not be read (' + reason(code) + ').', code)
        try:
            group, key = GUID.from_text(subgroup), GUID.from_text(setting)
            values = []
            for name in ('PowerReadACValueIndex', 'PowerReadDCValueIndex'):
                value = c_uint32()
                code = self._function('powrprof', name)(None, scheme, ctypes.byref(group), ctypes.byref(key),
                                                        ctypes.byref(value))
                values.append(value.value if code == ERROR_SUCCESS else None)
            return tuple(values)
        finally:
            self._function('kernel32', 'LocalFree')(ctypes.cast(scheme, c_void_p))

    def on_battery(self):
        """True on battery, False plugged in, None unknown (GetSystemPowerStatus)."""
        status = SYSTEM_POWER_STATUS()
        if not self._function('kernel32', 'GetSystemPowerStatus')(ctypes.byref(status)):
            return None
        return {0: True, 1: False}.get(status.ACLineStatus)


_api = NativeApi()


# --------------------------------------------------------------------------- adapters (cached)
_cache_lock = threading.Lock()
_cache = {'api': None, 'time': None, 'rows': None}


def invalidate():
    """Forget the cached adapter list (the next call enumerates again)."""
    with _cache_lock:
        _cache.update(api=None, time=None, rows=None)


def _adapter(row):
    """An adapter row with kind/physical/connected/usb and link speeds in Mbit/s."""
    if_type, medium = row.get('if_type'), row.get('physical_medium')
    kind = 'wifi' if if_type == IF_TYPE_IEEE80211 or medium in WIRELESS_MEDIA else \
        'ethernet' if if_type == IF_TYPE_ETHERNET_CSMACD else 'other'
    physical = row.get('physical')
    if physical is None:  # GetIfEntry2 unavailable: the adapter types a connector belongs to
        physical = kind in ('wifi', 'ethernet')
    device = row.get('pnp_id') or ''
    result = dict(row, kind=kind, physical=bool(physical), connected=row.get('oper_status') == IF_OPER_STATUS_UP,
                  operstate=OPER_STATUS.get(row.get('oper_status'), 'unknown'),
                  usb=device.upper().startswith('USB\\') if device else None)
    for key, name in (('transmit_bps', 'transmit_mbps'), ('receive_bps', 'receive_mbps')):
        value = row.get(key)
        result[name] = round(value / 1e6, 1) if isinstance(value, int) and 0 < value < 10 ** 12 else None
    return result


def adapters(max_age=ADAPTER_CACHE_SECONDS, clock=time.monotonic):
    """Every IPv4-capable adapter (GetAdaptersAddresses), cached for max_age seconds. Raises WindowsNetworkError."""
    api = _api
    now = clock()
    with _cache_lock:
        if (_cache['api'] is api and _cache['rows'] is not None and _cache['time'] is not None
                and 0 <= now - _cache['time'] < max_age):
            return copy.deepcopy(_cache['rows'])
    try:
        rows = [_adapter(row) for row in api.adapters()]
    except WindowsNetworkError:
        raise
    except Exception as error:  # a malformed answer must not escape as an unrelated error
        raise WindowsNetworkError('The network adapters could not be read (' + str(error)[:160] + ').') from None
    with _cache_lock:
        _cache.update(api=api, time=now, rows=rows)
    return copy.deepcopy(rows)


def adapter_for(alias=None, index=None, address=None, guid=None):
    """The adapter with this alias, interface index, IPv4 address or GUID, or None."""
    for adapter in adapters():
        if alias is not None and adapter.get('alias') != alias:
            continue
        if index is not None and adapter.get('index') != index:
            continue
        if guid is not None and not same_guid(adapter.get('guid'), guid):
            continue
        if address is not None and not any(item.get('address') == address for item in adapter.get('ipv4') or ()):
            continue
        return adapter
    return None


def adapter_counters():
    """1.0.0.13: {alias: counters} for each physical adapter, the counters read now (the adapter list may be the
    cached one). Raises WindowsNetworkError when the adapters cannot be listed."""
    result = {}
    for adapter in adapters():
        if not adapter.get('physical') or adapter.get('luid') is None:
            continue
        counters = _api.interface_counters(adapter['luid'])
        if counters:
            name = (adapter.get('alias') or str(adapter.get('index')))[:64]
            result[name] = dict(counters, kind=adapter.get('kind'), usb=adapter.get('usb'))
    return result


def interface_index(alias):
    """The interface index (IF_INDEX) of the adapter with this alias, or None."""
    if not valid_alias(alias):
        return None
    try:
        adapter = adapter_for(alias)
    except WindowsNetworkError:
        return None
    index = (adapter or {}).get('index')
    return index if type(index) is int and index > 0 else None


def lan_addresses():
    """launcher.client_identity.lan_addresses() rows for Windows: assigned, usable IPv4 addresses with the
    adapter alias as 'interface', physical (a connector is present) and connected (operational status up)
    adapters first. Raises WindowsNetworkError when Windows does not answer."""
    records = []
    for adapter in adapters():
        alias = adapter.get('alias')
        if adapter.get('if_type') == IF_TYPE_SOFTWARE_LOOPBACK or not valid_alias(alias):
            continue
        for item in adapter.get('ipv4') or ():
            if item.get('dad_state') not in DAD_ASSIGNED or not _usable_ipv4(item.get('address')):
                continue
            record = {'address': item['address'], 'interface': alias, 'prefix': item.get('prefix'),
                      'physical': adapter['physical'], 'connected': adapter['connected']}
            if record not in records:
                records.append(record)
    records.sort(key=lambda value: (not value['physical'], value['interface'], ipaddress.IPv4Address(value['address'])))
    return records


def route(destination, source='', interface=''):
    """{'interface' (alias), 'index', 'gateway', 'source', ...} Windows would use for destination, or None."""
    index = interface_index(interface) if interface else 0
    if interface and not index:
        return None
    found = _api.best_route(str(ipaddress.IPv4Address(destination)),
                            str(ipaddress.IPv4Address(source)) if source else None, index or 0)
    if not found:
        return None
    adapter = adapter_for(index=found.get('index'))
    return dict(found, interface=(adapter or {}).get('alias', ''))


def neighbours(address=None):
    """IPv4 neighbour (ARP) entries, optionally for one address: {'address', 'mac', 'state', 'index'}."""
    rows = _api.neighbours()
    return [row for row in rows if address is None or row.get('address') == address]


def owned_sockets(pid):
    """TCP connections and UDP endpoints of one process: (tcp rows, udp rows)."""
    return ([row for row in _api.tcp_table() if row.get('pid') == pid],
            [row for row in _api.udp_table() if row.get('pid') == pid])


# --------------------------------------------------------------------------- Wi-Fi
def _query(api, handle, guid, opcode, parser, result):
    try:
        return parser(api.wlan_query(handle, guid, opcode))
    except WindowsNetworkError as error:
        if opcode == OPCODE_CURRENT_CONNECTION and error.code == ERROR_ACCESS_DENIED:
            result['location_blocked'] = True
        return None


def wifi_link(alias, identity=True, statistics=False, options=False):
    """What the Wi-Fi adapter with this alias is connected to. Never raises.

    Always: 'interface', 'wifi' (Windows manages it as Wi-Fi), 'connected', 'reason' ('' or why details are
    missing). When connected: 'signal_dbm', 'signal_quality', 'rx_mbps', 'tx_mbps', 'phy', 'channel',
    'freq_mhz', 'band', 'bands', 'links' (Wi-Fi 7 multi-link: one per link), 'multi_link'; with identity also
    'ssid', 'bssid' (location consent on Windows 11 24H2+; 'location_blocked' when refused); with
    statistics the adapter's frame counters; with options 'media_streaming' and 'background_scan'.
    """
    result = {'interface': alias if isinstance(alias, str) else '', 'wifi': False, 'connected': False, 'reason': ''}
    try:
        adapter = adapter_for(alias) if valid_alias(alias) else None
    except WindowsNetworkError as error:
        result['reason'] = str(error)
        return result
    if adapter is None:
        result['reason'] = 'The network adapter was not found.'
        return result
    api, guid = _api, adapter.get('guid', '')
    try:
        handle = api.wlan_open()
    except WindowsNetworkError as error:
        result['reason'] = str(error)
        return result
    realtime = connection = rssi = channel = counters = None
    bss_mhz = None
    try:
        try:
            info = next((item for item in api.wlan_interfaces(handle) if same_guid(item.get('guid'), guid)), None)
        except WindowsNetworkError as error:
            result['reason'] = str(error)
            return result
        if info is None:
            result['reason'] = 'Windows does not manage this adapter as Wi-Fi.'
            return result
        result.update(wifi=True, state=WLAN_STATES.get(info.get('state'), 'unknown'),
                      connected=info.get('state') == WLAN_CONNECTED)
        if not result['connected']:
            return result
        realtime = _query(api, handle, guid, OPCODE_REALTIME_QUALITY, parse_realtime_quality, result)
        if identity or realtime is None:
            connection = _query(api, handle, guid, OPCODE_CURRENT_CONNECTION, parse_connection_attributes, result)
        rssi = _query(api, handle, guid, OPCODE_RSSI, parse_long, result)
        channel = _query(api, handle, guid, OPCODE_CHANNEL_NUMBER, parse_ulong, result)
        if statistics:
            counters = _query(api, handle, guid, OPCODE_STATISTICS, parse_statistics, result)
        if options:
            result['media_streaming'] = _query(api, handle, guid, OPCODE_MEDIA_STREAMING, parse_bool, result)
            result['background_scan'] = _query(api, handle, guid, OPCODE_BACKGROUND_SCAN, parse_bool, result)
        links = (realtime or {}).get('links') or []
        if not any(link.get('freq_mhz') for link in links) and connection and connection.get('bssid'):
            try:  # location consent was given (the connection query answered): the access point's frequency
                bss_mhz = next((row['freq_mhz'] for row in parse_bss_list(api.wlan_bss_list(handle, guid))
                                if row.get('bssid') == connection['bssid'] and row.get('freq_mhz')), None)
            except WindowsNetworkError:
                bss_mhz = None
    finally:
        api.wlan_close(handle)
    source = connection or realtime or {}
    phy = (realtime or {}).get('phy') or (connection or {}).get('phy') or ''
    quality = source.get('signal_quality')
    links = (realtime or {}).get('links') or []
    frequencies = [link['freq_mhz'] for link in links if link.get('freq_mhz')] or ([bss_mhz] if bss_mhz else [])
    bands = sorted({band_from_frequency(value) for value in frequencies}, key=('2.4 GHz', '5 GHz', '6 GHz').index)
    if not bands and band_from_channel(channel, phy):
        bands = [band_from_channel(channel, phy)]
    signal = rssi if type(rssi) is int and -120 < rssi < 0 else None
    if signal is None:
        signal = max((link['signal_dbm'] for link in links if link.get('signal_dbm') is not None), default=None)
    if signal is None:
        signal = quality_dbm(quality)
    rates = {}
    for key in ('rx', 'tx'):
        kbps = (connection or {}).get(key + '_kbps') or (realtime or {}).get(key + '_kbps')
        rates[key + '_mbps'] = round(kbps / 1000.0, 1) if kbps else None
    result.update(phy=phy, signal_quality=quality, signal_dbm=signal, channel=channel or None,
                  freq_mhz=frequencies[0] if frequencies else None, bands=bands, band=bands[-1] if bands else '',
                  links=links, multi_link=bool((realtime or {}).get('mlo')) and len(links) > 1, **rates)
    if connection:
        result.update(ssid=connection.get('ssid', ''), bssid=connection.get('bssid', ''),
                      profile=connection.get('profile', ''), enterprise=connection.get('enterprise', False))
    if statistics:
        result['statistics'] = counters or {}
    return result


def wifi_power_saving():
    """The active power plan's Wi-Fi power saving: {'ac', 'dc' (0-3 or None), 'on_battery', 'current'}.
    Raises WindowsNetworkError when the power plan cannot be read."""
    ac, dc = _api.power_values(WIFI_POWER_SUBGROUP, WIFI_POWER_SETTING)
    try:
        battery = _api.on_battery()
    except WindowsNetworkError:
        battery = None
    current = dc if battery else ac if battery is False else None
    if current is None and ac is not None and ac == dc:
        current = ac
    return {'ac': ac, 'dc': dc, 'on_battery': battery, 'current': current}


def power_mode_name(value):
    return WIFI_POWER_MODES[value] if type(value) is int and 0 <= value < len(WIFI_POWER_MODES) else 'unknown'


class WlanSessionOption:
    """Hold one Native Wifi BOOL setting (WlanSetInterface) on one adapter until close().

    Media streaming mode and background scanning are per client: Windows keeps a setting while at least
    one client handle asks for it, and drops it when that handle closes or the process exits, so nothing
    outlives the companion. Windows also drops it when Wi-Fi disconnects; keep() (every `interval`
    seconds on a daemon thread) applies it again once Wi-Fi is back. Raises WindowsNetworkError when the
    setting cannot be applied at all (code ERROR_INVALID_STATE: Wi-Fi is not connected).
    """

    def __init__(self, guid, opcode, value, api=None, interval=5.0, start=True):
        self.api = api if api is not None else _api
        self.guid, self.opcode, self.value = guid, opcode, bool(value)
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.reapplied = 0
        self.handle = self.api.wlan_open()
        code = self.api.wlan_set(self.handle, guid, opcode, self.value)
        if code != ERROR_SUCCESS:
            self.api.wlan_close(self.handle)
            self.handle = None
            raise WindowsNetworkError(reason(code), code)
        self.active = True
        self.thread = None
        if start and interval:
            self.thread = threading.Thread(target=self._keep, args=(interval,), daemon=True,
                                           name='VROSTBridge-wifi-option')
            self.thread.start()

    def _keep(self, interval):
        while not self.closed.wait(interval):
            try:
                self.keep()
            except Exception:  # never end the session over this
                pass

    def keep(self):
        """Apply the setting again if Windows dropped it (after a Wi-Fi reconnect); True while it is in force.
        While Wi-Fi is disconnected, or when the setting cannot be read back, nothing is sent."""
        with self.lock:
            if self.handle is None:
                return False
            try:
                current = parse_bool(self.api.wlan_query(self.handle, self.guid, self.opcode))
            except WindowsNetworkError as error:
                if error.code == ERROR_INVALID_STATE:
                    self.active = False
                return self.active
            if current == self.value:
                self.active = True
                return True
            code = self.api.wlan_set(self.handle, self.guid, self.opcode, self.value)
            if code == ERROR_SUCCESS:
                self.reapplied += 1
            self.active = code == ERROR_SUCCESS
            return self.active

    def status(self):
        with self.lock:
            return {'active': self.handle is not None and self.active, 'reapplied': self.reapplied}

    def close(self):
        self.closed.set()
        with self.lock:
            handle, self.handle = self.handle, None
        if handle is not None:
            self.api.wlan_close(handle)


def wifi_pause(alias):
    """Disconnect the Wi-Fi adapter with this alias, remembering its saved profile so wifi_resume() can
    reconnect it: {'interface', 'guid', 'profile'}. Needs no administrator rights, but the profile name
    comes from the location-gated connection query. Raises WindowsNetworkError with a short reason."""
    adapter = adapter_for(alias) if valid_alias(alias) else None
    if adapter is None:
        raise WindowsNetworkError('the adapter was not found')
    api, guid = _api, adapter['guid']
    handle = api.wlan_open()
    try:
        try:
            connection = parse_connection_attributes(api.wlan_query(handle, guid, OPCODE_CURRENT_CONNECTION))
        except WindowsNetworkError as error:
            if error.code == ERROR_ACCESS_DENIED:
                raise WindowsNetworkError('Windows location access is off for VROSTBridge, so the network of '
                                          + alias + ' could not be remembered', error.code) from None
            raise
        profile = connection.get('profile') or ''
        if not profile:
            raise WindowsNetworkError(alias + ' is not connected with a saved Wi-Fi profile')
        code = api.wlan_disconnect(handle, guid)
        if code != ERROR_SUCCESS:
            raise WindowsNetworkError(reason(code), code)
        return {'interface': alias, 'guid': adapter['guid'], 'profile': profile}
    finally:
        api.wlan_close(handle)


def wifi_resume(record):
    """Reconnect an adapter wifi_pause() disconnected, with its saved profile (Windows connects in the
    background). Raises WindowsNetworkError with a short reason."""
    if not isinstance(record, dict) or not isinstance(record.get('profile'), str) or not record['profile']:
        raise WindowsNetworkError('no saved Wi-Fi profile was recorded')
    guid = record.get('guid')
    try:
        uuid.UUID(str(guid))
    except (ValueError, TypeError):
        raise WindowsNetworkError('the recorded adapter is invalid') from None
    api = _api
    handle = api.wlan_open()
    try:
        try:
            state = next((item.get('state') for item in api.wlan_interfaces(handle)
                          if same_guid(item.get('guid'), guid)), None)
        except WindowsNetworkError:
            state = None
        if state == WLAN_CONNECTED:
            return True  # Windows already reconnected it: no second reconnection
        code = api.wlan_connect(handle, guid, record['profile'][:256])
    finally:
        api.wlan_close(handle)
    if code != ERROR_SUCCESS:
        raise WindowsNetworkError(reason(code), code)
    return True
