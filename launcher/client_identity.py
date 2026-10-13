"""Read the native ALVR client's identity and connection messages.

The pinned ALVR client stores UserConfig/ALVR Client/session.json using
app_dirs2 2.5.5. On Linux this is $XDG_CONFIG_HOME/ALVR Client/session.json,
or ~/.config/ALVR Client/session.json. On Windows app_dirs2 adds the author
(AppInfo author "ALVR") under the roaming application data folder:
%APPDATA%\\ALVR\\ALVR Client\\session.json. It is independent of the install
directory, so upgrades retain the identity. Only ALVR writes that file.
"""

import ctypes
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

from launcher import windows_network


def config_path():
    """Match the path used by pinned app_dirs2: XDG on Linux, %APPDATA%\\ALVR\\ALVR Client on Windows."""
    if sys.platform == 'win32':
        configured = os.environ.get('APPDATA', '')
        base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / 'AppData' / 'Roaming'
        return base / 'ALVR' / 'ALVR Client' / 'session.json'
    configured = os.environ.get('XDG_CONFIG_HOME', '')
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.config'
    return base / 'ALVR Client' / 'session.json'


def valid_hostname(value):
    """Keep the actual protocol identity, including its trailing dot."""
    if not isinstance(value, str) or len(value) > 253:
        return False
    return bool(re.fullmatch(r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+local\.', value))


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        return not (address.is_loopback or address.is_unspecified or address.is_multicast
                    or address.is_link_local or address.is_reserved)
    except (ValueError, TypeError):
        return False


def lan_addresses():
    """Enumerate assigned IPv4 addresses, without DNS or an Internet probe.

    getifaddrs includes secondary addresses as well as the primary address.
    Interface names remain visible so a user can choose Wi-Fi/Ethernet when
    more than one network is active. Physical interfaces sort first.
    Windows: the same rows from GetAdaptersAddresses (launcher/windows_network.py),
    with the adapter alias ("Wi-Fi 2") as the interface name.
    """
    if sys.platform == 'win32':
        try:
            return windows_network.lan_addresses()
        except windows_network.WindowsNetworkError:
            return []
    if not sys.platform.startswith('linux'):
        return []

    class Sockaddr(ctypes.Structure):
        _fields_ = [('family', ctypes.c_ushort), ('data', ctypes.c_ubyte * 14)]

    class SockaddrIn(ctypes.Structure):
        _fields_ = [('family', ctypes.c_ushort), ('port', ctypes.c_ushort),
                    ('address', ctypes.c_ubyte * 4), ('padding', ctypes.c_ubyte * 8)]

    class Ifaddrs(ctypes.Structure):
        pass

    pointer = ctypes.POINTER(Ifaddrs)
    Ifaddrs._fields_ = [('next', pointer), ('name', ctypes.c_char_p), ('flags', ctypes.c_uint),
                       ('address', ctypes.POINTER(Sockaddr)), ('netmask', ctypes.POINTER(Sockaddr)),
                       ('destination', ctypes.POINTER(Sockaddr)), ('data', ctypes.c_void_p)]
    first = pointer()
    records = []
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.getifaddrs.argtypes = [ctypes.POINTER(pointer)]
        libc.getifaddrs.restype = ctypes.c_int
        libc.freeifaddrs.argtypes = [pointer]
        libc.freeifaddrs.restype = None
        if libc.getifaddrs(ctypes.byref(first)) != 0:
            return []
        current = first
        while current:
            item = current.contents
            # IFF_UP and not IFF_LOOPBACK. Do not invent an address if absent.
            if item.flags & 1 and not item.flags & 8 and item.address and item.address.contents.family == socket.AF_INET:
                raw = ctypes.cast(item.address, ctypes.POINTER(SockaddrIn)).contents
                address = socket.inet_ntop(socket.AF_INET, bytes(raw.address))
                interface = (item.name or b'').decode('utf-8', errors='replace')
                if usable_ipv4(address) and interface:
                    prefix = None
                    if item.netmask and item.netmask.contents.family == socket.AF_INET:
                        mask = ctypes.cast(item.netmask, ctypes.POINTER(SockaddrIn)).contents
                        mask_text = socket.inet_ntop(socket.AF_INET, bytes(mask.address))
                        try:
                            prefix = ipaddress.IPv4Network('0.0.0.0/' + mask_text).prefixlen
                        except ValueError:
                            pass
                    record = {'address': address, 'interface': interface, 'prefix': prefix,
                              'physical': (Path('/sys/class/net') / interface / 'device').exists(),
                              'connected': bool(item.flags & 0x10000)}
                    if record not in records:
                        records.append(record)
            current = item.next
    except (AttributeError, OSError, ValueError):
        return []
    finally:
        if first:
            libc.freeifaddrs(first)
    records.sort(key=lambda value: (not (Path('/sys/class/net') / value['interface'] / 'device').exists(),
                                    value['interface'], ipaddress.IPv4Address(value['address'])))
    return records


class ClientIdentity:
    def __init__(self, path=None, address_provider=None):
        self.path = Path(path) if path is not None else config_path()
        self.address_provider = address_provider or lan_addresses
        self.hostname = ''
        self.connection_state = 'stopped'
        self.message = 'Start the client to look for your rendering host.'
        self._error_detail = False
        self._addresses = []
        self._address_time = None

    def reset(self):
        """Call before each native client launch; retain ALVR's stored identity."""
        self.hostname = ''
        self.connection_state = 'starting'
        self.message = 'Starting the local headset runtime.'
        self._error_detail = False

    def observe(self, line):
        """Consume native stdout one line at a time, including multiline HUDs."""
        value = line.strip()
        if value.startswith('STATUS '):
            value = value[7:]
        if value.startswith('hostname: '):
            hostname = value.partition(':')[2].strip()
            if valid_hostname(hostname):
                self.hostname = hostname
            return
        # HUD IP is only its chosen default route; the interface list is more
        # useful on a multihomed client, and never includes an invented address.
        if not value or value.startswith(('IP: ', 'ALVR v', 'METRICS ')):
            return
        if value.startswith('ERROR '):
            self.connection_state = 'error'
            self.message = value[6:][:1024]
            self._error_detail = False
        elif value.startswith('Connection error:'):
            self.connection_state = 'error'
            self.message = value[:1024]
            self._error_detail = True
        elif value.startswith('Searching for streamer'):
            self.connection_state = 'searching'
            self.message = 'Looking for the host. Use the host page to pair this client.'
            self._error_detail = False
        elif value.startswith('Successful connection!'):
            self.connection_state = 'connecting'
            self.message = 'Host reached. Negotiating the stream.'
            self._error_detail = False
        elif value.startswith(('The stream will begin soon', 'Stream negotiated ')):
            self.connection_state = 'connected'
            self.message = 'Host connected. Waiting for decoded video.'
            self._error_detail = False
        elif value.startswith('The streamer is restarting'):
            self.connection_state = 'restarting'
            self.message = 'The host is restarting its streaming runtime.'
            self._error_detail = False
        elif value.startswith(('The streamer has disconnected', 'Disconnected; waiting for host')):
            self.connection_state = 'disconnected'
            self.message = 'Host disconnected. Waiting to reconnect.'
            self._error_detail = False
        elif value.startswith(('Connection timeout.', 'Unexpected packet')):
            self.connection_state = 'error'
            self.message = value[:1024]
            self._error_detail = False
        elif value.startswith('Check the PC for more details'):
            self._error_detail = False
        elif self._error_detail:
            self.message = (self.message + ' ' + value)[:1024]

    def status(self):
        stored = {}
        try:
            if self.path.stat().st_size <= 65536:
                document = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(document, dict) and valid_hostname(document.get('hostname')):
                    stored = document
        except (OSError, ValueError, UnicodeError):
            pass
        now = time.monotonic()
        if self._address_time is None or now - self._address_time >= 5:
            self._addresses = self.address_provider()
            self._address_time = now
        return {'hostname': self.hostname or stored.get('hostname', ''),
                'protocol_id': stored.get('protocol_id', '') if isinstance(stored.get('protocol_id', ''), str) else '',
                'addresses': list(self._addresses),
                'connection_state': self.connection_state, 'message': self.message}
