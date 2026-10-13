"""Which kind of link the client streams over, for the page, Game Mode and reports (RC4.2).

The page shows Wi-Fi options (staying on one access point, power saving, other
adapters) only on Wi-Fi and Ethernet details only on Ethernet. Ethernet checks:
negotiated speed (a damaged cable, a 100 Mbit/s port or a slow USB adapter caps the
stream), duplex, and Energy-Efficient Ethernet, which lets the link doze between
packets and can add small delays (reported, not changed). Wi-Fi details come from
`iw dev <adapter> link`: band, signal and link rate. Never raises; reads only.

Windows: the adapter alias ("Wi-Fi 2") names the adapter; Wi-Fi details come from the
Native Wifi API (band from each link's frequency, else the channel; signal, link rate),
Ethernet from GetAdaptersAddresses (speed; duplex and Energy-Efficient Ethernet are not
reported there) and the adapter's PnP id (USB). See launcher/windows_network.py.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from launcher.client_network import system_run
from launcher.wifi_diagnostics import parse_station_dump
from launcher import windows_network

_INTERFACE = re.compile(r'(?!-)[A-Za-z0-9_.-]{1,15}')
_SSID = re.compile(r'^\s*SSID:\s?(.*)$', re.M)
WEAK_SIGNAL_DBM = -67


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return ''


BANDS = ('2.4 GHz', '5 GHz', '6 GHz')


def band(freq_mhz):
    if not isinstance(freq_mhz, (int, float)) or freq_mhz <= 0:
        return ''
    if freq_mhz < 3000:
        return '2.4 GHz'
    if freq_mhz < 5925:
        return '5 GHz'
    return '6 GHz'


def speed_text(mbps):
    if not mbps:
        return ''
    if mbps >= 1000:
        return ('%.1f' % (mbps / 1000)).rstrip('0').rstrip('.') + ' Gbit/s'
    return str(int(mbps)) + ' Mbit/s'


def _run(runner, arguments, timeout):
    try:
        return runner(arguments, timeout)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(arguments, 1, '', '')


def describe(interface, sys_class_net='/sys/class/net', runner=system_run, iw=None, ethtool=None):
    """{'interface', 'kind': 'wifi'|'ethernet'|'unknown', 'summary', 'details': {...}, 'warnings': [...],
    'notes': [...]} for the adapter that carries (or will carry) the stream."""
    result = {'interface': interface or '', 'kind': 'unknown', 'summary': '', 'details': {}, 'warnings': [], 'notes': []}
    if sys.platform == 'win32':
        return _describe_windows(result, interface)
    if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface):
        result['summary'] = 'No network adapter is selected yet.'
        return result
    base = Path(sys_class_net) / interface
    if not base.exists():
        result['summary'] = interface + ' is not available.'
        return result
    if (base / 'wireless').is_dir() or (base / 'phy80211').exists():
        return _wifi(result, interface, runner, _tool('iw', ('/usr/sbin/iw', '/usr/bin/iw')) if iw is None else iw)
    return _ethernet(result, interface, base, runner,
                     _tool('ethtool', ('/usr/sbin/ethtool', '/usr/bin/ethtool')) if ethtool is None else ethtool)


def _wifi(result, interface, runner, iw):
    result['kind'] = 'wifi'
    details = {}
    if iw:
        output = _run(runner, [iw, 'dev', interface, 'link'], 3)
        text = output.stdout if output.returncode == 0 else ''
        connection = next((item for item in parse_station_dump(text) if item['kind'] == 'connection'), None)
        if connection:
            links = connection.get('links') or []
            # Wi-Fi 7 multi-link: every link's band (RC4.2 named only the first link's,
            # and warned about "2.4 GHz" for a 2.4 + 5 + 6 GHz connection).
            freqs = [link['freq_mhz'] for link in links if link.get('freq_mhz')] or \
                ([connection['freq_mhz']] if connection.get('freq_mhz') else [])
            bands = sorted({band(freq) for freq in freqs if band(freq)}, key=BANDS.index)
            signal = connection.get('signal_dbm')
            if signal is None and links:
                signal = max((link['signal_dbm'] for link in links if 'signal_dbm' in link), default=None)
            rate = connection.get('tx_bitrate_mbps')
            if rate is None and links:
                rate = max((link['tx_bitrate_mbps'] for link in links if 'tx_bitrate_mbps' in link), default=None)
            ssid = _SSID.search(text)
            details = {'ssid': ssid.group(1)[:64] if ssid else '', 'band': bands[-1] if bands else '', 'bands': bands,
                       'signal_dbm': signal, 'link_mbps': rate, 'multi_link': len(links)}
        elif output.returncode == 0:
            result['summary'] = 'Wi-Fi (' + interface + ') is not connected.'
            result['details'] = {'connected': False}
            return result
    return _wifi_report(result, interface, details)


def _wifi_report(result, interface, details):
    """Summary and warnings from Wi-Fi details (band(s), signal_dbm, link_mbps, multi_link)."""
    result['details'] = details
    parts = ['Wi-Fi']
    bands = details.get('bands') or ([details['band']] if details.get('band') else [])
    if details.get('multi_link'):
        parts.append('multi-link ' + ' + '.join(name.replace(' GHz', '') for name in bands) + ' GHz' if bands
                     else 'multi-link')
    elif bands:
        parts.append(bands[-1])
    if details.get('signal_dbm') is not None:
        parts.append(str(details['signal_dbm']) + ' dBm')
    if details.get('link_mbps'):
        parts.append(speed_text(int(details['link_mbps'])) + ' link')
    result['summary'] = ' · '.join(parts) + ' (' + interface + ')'
    if bands == ['2.4 GHz']:  # a multi-link connection that also has 5 or 6 GHz links is fine
        result['warnings'].append('Wi-Fi is on 2.4 GHz, which is too slow and crowded for PC VR. Connect to your '
                                  "router's 5 or 6 GHz network.")
    if isinstance(details.get('signal_dbm'), int) and details['signal_dbm'] < WEAK_SIGNAL_DBM:
        result['warnings'].append('The Wi-Fi signal is weak (' + str(details['signal_dbm']) + ' dBm). Move closer to '
                                  'the access point or keep a clear line of sight to it.')
    return result


def _ethernet(result, interface, base, runner, ethtool):
    result['kind'] = 'ethernet'
    carrier = _read(base / 'carrier')
    try:
        speed = int(_read(base / 'speed'))
    except ValueError:
        speed = None
    speed = speed if speed and 10 <= speed <= 400000 else None
    duplex = _read(base / 'duplex').lower()
    try:
        usb = '/usb' in os.path.realpath(base / 'device')
    except OSError:
        usb = False
    details = {'link_mbps': speed, 'duplex': duplex if duplex in ('full', 'half') else '', 'usb_adapter': usb,
               'connected': carrier == '1'}
    if carrier == '0':
        return _no_cable(result, interface, details)
    if ethtool:
        output = _run(runner, [ethtool, '--show-eee', interface], 3)
        text = (output.stdout or '').lower() if output.returncode == 0 else ''
        status = re.search(r'eee status:\s*([a-z -]+)', text)
        if status:
            details['eee'] = status.group(1).strip()
            if 'enabled' in details['eee'] and 'active' in details['eee'] and 'inactive' not in details['eee']:
                result['notes'].append('Energy-Efficient Ethernet is active on ' + interface + '. It lets the link doze '
                                       'between packets; if you see small stutters on Ethernet, turn it off in the '
                                       "adapter's or router's settings.")
    return _ethernet_report(result, interface, details)


def _no_cable(result, interface, details):
    result['summary'] = 'Ethernet (' + interface + ') has no cable link.'
    result['details'] = details
    result['warnings'].append('The Ethernet adapter ' + interface + ' reports no cable link.')
    return result


def _ethernet_report(result, interface, details):
    """Summary and warnings from Ethernet details (link_mbps, duplex, usb_adapter)."""
    speed, usb = details['link_mbps'], details['usb_adapter']
    result['details'] = details
    parts = ['Ethernet']
    if speed:
        parts.append(speed_text(speed))
    if details['duplex'] == 'half':
        parts.append('half duplex')
    if usb:
        parts.append('USB adapter')
    result['summary'] = ' · '.join(parts) + ' (' + interface + ')'
    if speed and speed < 1000:
        result['warnings'].append('Ethernet is connected at only ' + speed_text(speed) + '. A cable, port or adapter '
                                  'is limiting it; VROSTBridge needs 1 Gbit/s for high bitrates. Try another cable '
                                  'or router port.')
    if details['duplex'] == 'half':
        result['warnings'].append('The Ethernet link runs at half duplex, which adds delay under load. Check the cable '
                                  'and the router port.')
    return result


def _describe_windows(result, interface):
    """Windows: the same result for the adapter with this alias (launcher/windows_network.py)."""
    if not isinstance(interface, str) or not windows_network.valid_alias(interface):
        result['summary'] = 'No network adapter is selected yet.'
        return result
    try:
        adapter = windows_network.adapter_for(interface)
    except windows_network.WindowsNetworkError as error:
        adapter = None
        result['notes'].append('The network adapters could not be read: ' + str(error)[:160])
    if adapter is None:
        result['summary'] = interface + ' is not available.'
        return result
    if adapter.get('kind') == 'wifi':
        return _wifi_windows(result, interface, adapter)
    return _ethernet_windows(result, interface, adapter)


def _wifi_windows(result, interface, adapter):
    result['kind'] = 'wifi'
    # Without the network name and access point: those need Windows' location consent (windows_network).
    link = windows_network.wifi_link(interface, identity=False)
    if link.get('wifi') and not link.get('connected'):
        result['summary'] = 'Wi-Fi (' + interface + ') is not connected.'
        result['details'] = {'connected': False}
        return result
    details = {}
    if link.get('connected'):
        links = link.get('links') or []
        details = {'ssid': str(link.get('ssid') or '')[:64], 'band': link.get('band') or '',
                   'bands': list(link.get('bands') or []), 'signal_dbm': link.get('signal_dbm'),
                   'link_mbps': link.get('tx_mbps'), 'multi_link': len(links) if link.get('multi_link') else 0,
                   'rx_mbps': link.get('rx_mbps'), 'channel': link.get('channel'), 'phy': link.get('phy') or '',
                   'signal_quality': link.get('signal_quality'), 'usb_adapter': bool(adapter.get('usb'))}
    elif link.get('reason'):
        result['notes'].append('Wi-Fi details are unavailable: ' + str(link['reason'])[:160])
    return _wifi_report(result, interface, details)


def _ethernet_windows(result, interface, adapter):
    result['kind'] = 'ethernet'
    speed = adapter.get('transmit_mbps')
    speed = int(speed) if isinstance(speed, (int, float)) and 10 <= speed <= 400000 else None
    media = adapter.get('media_connected')  # MediaConnectState (GetIfEntry2): the cable link itself
    details = {'link_mbps': speed, 'duplex': '', 'usb_adapter': bool(adapter.get('usb')),
               'connected': media if isinstance(media, bool) else bool(adapter.get('connected'))}
    if not details['connected']:
        return _no_cable(result, interface, details)
    return _ethernet_report(result, interface, details)
