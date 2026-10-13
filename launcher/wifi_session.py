"""Session-scoped Wi-Fi power saving for the Linux client.

Linux Wi-Fi power saving (NetworkManager's usual default) lets the radio doze
between beacons. That delays uplink tracking packets and downlink video bursts
and causes periodic stutter. During a streaming session only, ask
NetworkManager to turn power saving off on the adapter that carries the host
route, using `nmcli device modify`: a runtime change to the active connection
that never edits the saved profile. Stop restores the profile's value; a Wi-Fi
reconnect or reboot restores it too. Wired adapters are untouched. Every
failure is reported and none blocks a session.

Windows: during a session the Wi-Fi adapter runs in the Native Wifi "media
streaming mode" (WlanSetInterface wlan_intf_opcode_media_streaming_mode: the
driver must not do anything that delays the connection, such as scanning). It
is held through a WLAN client handle for the session only: Windows drops it
when the handle closes (end()) or the companion exits, and when Wi-Fi
disconnects, after which it is applied again (windows_network.WlanSessionOption).
Wi-Fi power saving itself is a power-plan setting there (Wireless Adapter
Settings > Power Saving Mode); status reports it, and disable_permanently()
sets it to Maximum Performance in the active plan, on battery and plugged in
(powercfg; Windows normally allows plan changes without administrator rights,
and a refusal is reported).
"""
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from launcher.client_network import system_run
from launcher import windows_network

_INTERFACE = re.compile(r'[A-Za-z0-9_.:-]{1,15}')
_NM_VALUES = {'default': '0', 'ignore': '1', 'disable': '2', 'enable': '3'}


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file():
            return path
    return shutil.which(name)


class WifiSession:
    def __init__(self, runner=system_run, log=print, sys_class_net='/sys/class/net',
                 nmcli=None, iw=None, ip=None):
        self.runner, self.log = runner, log
        self.sys_class_net = Path(sys_class_net)
        self.nmcli = nmcli or _tool('nmcli', ('/usr/bin/nmcli',))
        self.iw = iw or _tool('iw', ('/usr/sbin/iw', '/usr/bin/iw'))
        self.ip = ip or _tool('ip', ('/usr/sbin/ip', '/usr/bin/ip'))
        self.lock = threading.Lock()
        self.changed = None  # (interface, original NetworkManager value)
        self.streaming = None  # Windows: (interface, windows_network.WlanSessionOption) held for the session
        self.value = {'state': 'idle', 'message': ''}

    def status(self):
        with self.lock:
            return dict(self.value)

    def _set(self, state, message, **extra):
        with self.lock:
            self.value = {'state': state, 'message': message, **extra}
        if message:
            self.log('WIFI ' + state + ': ' + message)
        return self.status()

    def _run(self, arguments, timeout=5):
        return self.runner(arguments, timeout)

    def route_interface(self, host_ip, source_ip=''):
        if sys.platform == 'win32':
            try:
                found = windows_network.route(str(ipaddress.IPv4Address(host_ip)),
                                              str(ipaddress.IPv4Address(source_ip)) if source_ip else '')
            except (ValueError, TypeError, OSError):
                return ''
            name = (found or {}).get('interface', '')
            return name if windows_network.valid_alias(name) else ''
        if not self.ip:
            return ''
        try:
            arguments = [self.ip, '-j', 'route', 'get', str(ipaddress.IPv4Address(host_ip))]
            if source_ip:
                arguments += ['from', str(ipaddress.IPv4Address(source_ip))]
        except ValueError:
            return ''
        try:
            result = self._run(arguments, 2)
            routes = json.loads(result.stdout) if result.returncode == 0 else []
            device = routes[0].get('dev', '') if routes else ''
        except (OSError, ValueError, TypeError, AttributeError, IndexError):
            return ''
        return device if isinstance(device, str) and _INTERFACE.fullmatch(device) else ''

    def power_save(self, interface):
        """Kernel view: True (on), False (off) or None (unknown). Windows: the active power plan's Wi-Fi power
        saving for the current power source (it applies to every Wi-Fi adapter)."""
        if sys.platform == 'win32':
            current = (self._windows_power() or {}).get('current')
            return None if current is None else current > 0
        if not self.iw:
            return None
        try:
            result = self._run([self.iw, 'dev', interface, 'get', 'power_save'], 2)
        except OSError:
            return None
        text = result.stdout.lower() if result.returncode == 0 else ''
        if 'power save: on' in text:
            return True
        if 'power save: off' in text:
            return False
        return None

    def _profile_value(self, interface):
        """Saved profile value of 802-11-wireless.powersave as NM's number."""
        result = self._run([self.nmcli, '-g', 'GENERAL.CONNECTION', 'device', 'show', interface])
        name = result.stdout.strip() if result.returncode == 0 else ''
        if not name:
            return None
        result = self._run([self.nmcli, '-g', '802-11-wireless.powersave', 'connection', 'show', name])
        raw = result.stdout.strip().lower() if result.returncode == 0 else ''
        match = re.match(r'(\d)', raw)
        if match:
            return match.group(1)
        return next((number for word, number in _NM_VALUES.items() if word in raw), None)

    @staticmethod
    def _windows_power():
        try:
            return windows_network.wifi_power_saving()
        except windows_network.WindowsNetworkError:
            return None

    def _begin_windows(self, interface, host_ip, source_ip):
        self.end()
        if not interface:
            interface = self.route_interface(host_ip, source_ip)
        try:
            adapter = windows_network.adapter_for(interface) if windows_network.valid_alias(interface) else None
        except windows_network.WindowsNetworkError:
            adapter = None
        if adapter is None:
            return self._set('unknown', 'Could not identify the network adapter used for this session.')
        if adapter.get('kind') != 'wifi':
            return self._set('wired', '', interface=interface)
        streaming, note = False, ''
        try:
            option = windows_network.WlanSessionOption(adapter['guid'], windows_network.OPCODE_MEDIA_STREAMING, True)
        except windows_network.WindowsNetworkError as error:
            note = 'Windows did not turn on Wi-Fi media streaming mode (' + str(error)[:160] + ').'
        else:
            with self.lock:
                self.streaming = (interface, option)
            streaming = True
        power = self._windows_power() or {}
        current = power.get('current')
        extra = dict(interface=interface, streaming_mode=streaming, on_battery=power.get('on_battery'),
                     power_saving_ac=windows_network.power_mode_name(power.get('ac')),
                     power_saving_dc=windows_network.power_mode_name(power.get('dc')),
                     permanent_fix=any(type(power.get(key)) is int and power[key] > 0 for key in ('ac', 'dc')))
        session = ('Media streaming mode is on for this session, so the adapter avoids scans that interrupt the '
                   'stream; it ends when the session stops.' if streaming else note)
        if current is None:
            return self._set('disabled_for_session' if streaming else 'on',
                             "The Windows power plan's Wi-Fi power saving could not be read. " + session, **extra)
        if current > 0:
            return self._set('on', 'Wi-Fi power saving is set to ' + windows_network.power_mode_name(current) +
                             (' on battery' if power.get('on_battery') else '') + ' in the Windows power plan, which lets '
                             'the radio doze and can make the stream stutter. Turn it off with "Turn off Wi-Fi power '
                             'saving" in VROSTBridge (between sessions). ' + session, **extra)
        return self._set('off', 'Wi-Fi power saving is off in the Windows power plan on ' + interface + '. ' + session,
                         **extra)

    def begin(self, interface='', host_ip='', source_ip=''):
        """Disable power saving for this session's Wi-Fi adapter if it is on."""
        if sys.platform == 'win32':
            return self._begin_windows(interface, host_ip, source_ip)
        self.end()
        if not interface:
            interface = self.route_interface(host_ip, source_ip)
        if not interface or not _INTERFACE.fullmatch(interface):
            return self._set('unknown', 'Could not identify the network adapter used for this session.')
        if not (self.sys_class_net / interface / 'wireless').is_dir():
            return self._set('wired', '', interface=interface)
        state = self.power_save(interface)
        if state is False:
            return self._set('off', '', interface=interface)
        if not self.nmcli:
            return self._set('on', 'Wi-Fi power saving is on and NetworkManager is unavailable to change it. '
                             'Expect periodic stutter on Wi-Fi.', interface=interface)
        try:
            original = self._profile_value(interface)
            if original is None:
                return self._set('on' if state else 'unknown',
                                 'Wi-Fi power saving could not be read from NetworkManager; left unchanged.',
                                 interface=interface)
            result = self._run([self.nmcli, 'device', 'modify', interface, '802-11-wireless.powersave', '2'], 8)
        except OSError as error:
            return self._set('on', 'Wi-Fi power saving could not be changed: ' + str(error)[:160], interface=interface)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip().splitlines()
            return self._set('on', 'NetworkManager did not turn Wi-Fi power saving off for this session'
                             + (' (' + detail[-1][:160] + ')' if detail else '') +
                             '. For smoother Wi-Fi streaming, turn it off for your home network with '
                             '"Turn off Wi-Fi power saving" in VROSTBridge (between sessions).',
                             interface=interface, permanent_fix=True)
        with self.lock:
            self.changed = (interface, original)
        after = self.power_save(interface)
        if after is True:
            return self._set('on', 'NetworkManager accepted the change but the adapter still reports power saving on.',
                             interface=interface)
        return self._set('disabled_for_session', 'Wi-Fi power saving turned off for this session on ' + interface +
                         '; it is restored when the session stops.', interface=interface)

    def wireless_interfaces(self):
        if sys.platform == 'win32':
            try:
                return sorted(adapter['alias'] for adapter in windows_network.adapters()
                              if adapter.get('kind') == 'wifi' and windows_network.valid_alias(adapter.get('alias')))
            except windows_network.WindowsNetworkError:
                return []
        try:
            return sorted(item.name for item in self.sys_class_net.iterdir()
                          if (item / 'wireless').is_dir() and _INTERFACE.fullmatch(item.name))
        except OSError:
            return []

    def disable_permanently(self, interface=''):
        """Turn power saving off in the saved profile of this Wi-Fi connection, then reconnect.

        NetworkManager cannot change power saving on a live connection, so the
        per-session change above is often refused. This is the lasting fix for the
        home network only (the profile of the connection in use), made on request
        and applied by reconnecting Wi-Fi, which interrupts the network briefly.
        Windows: the active power plan's Wi-Fi Power Saving Mode becomes Maximum Performance, on battery and
        plugged in, for every Wi-Fi adapter (no reconnection; interface is not needed).
        """
        if sys.platform == 'win32':
            return self._disable_permanently_windows()
        if not interface or not (self.sys_class_net / interface / 'wireless').is_dir():
            candidates = self.wireless_interfaces()
            if len(candidates) != 1:
                raise ValueError('Connect to your home Wi-Fi first' if not candidates else
                                 'Choose the Wi-Fi adapter for VROSTBridge in Host connection settings first.')
            interface = candidates[0]
        if not _INTERFACE.fullmatch(interface):
            raise ValueError('Invalid Wi-Fi adapter name.')
        if not self.nmcli:
            raise ValueError('NetworkManager (nmcli) is not available on this PC.')
        result = self._run([self.nmcli, '-g', 'GENERAL.CON-UUID,GENERAL.CONNECTION', 'device', 'show', interface], 5)
        lines = result.stdout.strip().splitlines() if result.returncode == 0 else []
        uuid = lines[0].strip() if lines else ''
        name = lines[1].strip() if len(lines) > 1 else ''
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', uuid):
            raise ValueError('Wi-Fi is not connected on ' + interface + '.')
        result = self._run([self.nmcli, 'connection', 'modify', 'uuid', uuid, '802-11-wireless.powersave', '2'], 60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip().splitlines()
            raise ValueError('NetworkManager did not save the change' + (' (' + detail[-1][:160] + ')' if detail else '') + '.')
        self.log('WIFI Power saving turned off permanently for "' + name[:64] + '"; reconnecting Wi-Fi to apply it.')
        result = self._run([self.nmcli, 'connection', 'up', 'uuid', uuid], 60)
        if result.returncode != 0:
            return ('Wi-Fi power saving is now off for "' + name[:64] + '"; it applies the next time Wi-Fi '
                    'connects (reconnecting now failed).')
        state = self.power_save(interface)
        return ('Wi-Fi power saving is now off for "' + name[:64] + '"' +
                (' and the adapter confirms it.' if state is False else '; it applies from this connection on.'))

    def _disable_permanently_windows(self):
        if not self.wireless_interfaces():
            raise ValueError('No Wi-Fi adapter was found on this PC.')
        powercfg = windows_network.system32('powercfg.exe')
        setting = [windows_network.WIFI_POWER_SUBGROUP, windows_network.WIFI_POWER_SETTING, '0']
        for arguments in (['/setacvalueindex', 'SCHEME_CURRENT', *setting], ['/setdcvalueindex', 'SCHEME_CURRENT', *setting],
                          ['/setactive', 'SCHEME_CURRENT']):
            try:
                result = self._run([powercfg, *arguments], 15)
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError('Windows could not change the power plan (' + str(error)[:160] + ').') from None
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip().splitlines()
                raise ValueError('Windows did not save the power setting' + (' (' + detail[-1][:160] + ')' if detail else '') + '.')
        self.log('WIFI Power saving set to Maximum Performance in the active Windows power plan.')
        after = self._windows_power() or {}
        confirmed = after.get('ac') == 0 and after.get('dc') == 0
        if confirmed:
            with self.lock:
                self.value = dict(self.value, permanent_fix=False, power_saving_ac=windows_network.power_mode_name(0),
                                  power_saving_dc=windows_network.power_mode_name(0))
        return ('Wi-Fi power saving is now off (Maximum Performance) in the active Windows power plan, on battery and '
                'plugged in' + (', and Windows confirms it.' if confirmed else '.'))

    def end(self):
        with self.lock:
            streaming, self.streaming = self.streaming, None
        if streaming:  # Windows: release media streaming mode (closing its WLAN handle ends it)
            interface, option = streaming
            reapplied = option.status().get('reapplied', 0)
            option.close()
            self._set('restored', 'Wi-Fi media streaming mode ended on ' + interface +
                      (' (applied again after ' + str(reapplied) + ' Wi-Fi reconnection' + ('s' if reapplied != 1 else '') + ')'
                       if reapplied else '') + '.', interface=interface, streaming_mode=False)
            return
        with self.lock:
            changed, self.changed = self.changed, None
        if not changed:
            return
        interface, original = changed
        try:
            result = self._run([self.nmcli, 'device', 'modify', interface, '802-11-wireless.powersave', original], 8)
            ok = result.returncode == 0
        except OSError:
            ok = False
        self._set('restored' if ok else 'restore_failed',
                  'Wi-Fi power saving restored on ' + interface + '.' if ok else
                  'Wi-Fi power saving could not be restored on ' + interface +
                  '; it returns to normal when Wi-Fi reconnects.', interface=interface)
