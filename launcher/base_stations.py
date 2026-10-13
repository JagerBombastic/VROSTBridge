"""SteamVR base stations off when VROSTBridge Client closes, and on when a session starts (1.0.0.8).

SteamVR 2.0 base stations (named LHB-XXXXXXXX) take a power command over Bluetooth Low
Energy: one byte written to the power characteristic 00001525-1212-efde-1523-785feabcd124
of service 00001523-1212-efde-1523-785feabcd124 (0x00 sleep, 0x01 on, 0x02 standby; read
back, 0x0b means running). That is all this module sends, to devices named LHB- only.
1.0 base stations (HTC) need a different, per-station command and are not touched.

Waking them as a session starts lets their motors spin up while SteamVR starts on this PC:
in the 1.0.0.7 test the headset found the base stations 19.6 s after SteamVR started,
against about 4 s when they were already running.

Linux: BlueZ over D-Bus, with its own command-line tools (bluetoothctl to look for the
stations, busctl for the rest). Windows: Windows' own Bluetooth API from Windows PowerShell
5.1 (launcher/BaseStations.ps1). The stations found are remembered (base-stations.json):
Start and closing reach them directly, and on Linux search only for those BlueZ has
forgotten, 2 s at a time until they are seen (BlueZ drops a device it never paired with
soon after its last search); the page's buttons always search afresh, so a station added
later is learned. Closing VROSTBridge starts the "off" command as a separate process, so
the companion closes at once. Nothing here pairs, trusts or changes any Bluetooth setting;
a PC without Bluetooth, or with it off, just reports that.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run

SERVICE_UUID = '00001523-1212-efde-1523-785feabcd124'
POWER_UUID = '00001525-1212-efde-1523-785feabcd124'
COMMANDS = {'off': 0x00, 'on': 0x01, 'standby': 0x02}
STATES = {0x00: 'asleep', 0x02: 'standby', 0x0b: 'on', 0x01: 'starting', 0x08: 'starting', 0x09: 'starting'}
NAME_PREFIX = 'LHB-'
STATE_FILE = 'base-stations.json'
SCAN_S = 6           # a full search (nothing remembered yet, or the page's buttons)
SCAN_ROUND_S = 2     # remembered stations BlueZ has forgotten: searched for 2 s at a time, up to SCAN_S
CONNECT_S = 15
RESOLVE_S = 10
# 1.0.0.9: one station at a time, and a station that did not answer is tried once more after a
# moment. Connecting to two at once made BlueZ abort one of the connections
# ("le-connection-abort-by-local"), so only one of two stations woke.
# 1.0.0.10: the same station still refused both tries in the 1.0.0.9 test, so the cause lies with
# reaching that station. It now gets three tries, and on Linux each retry is preceded by a short
# search, so BlueZ connects from the station's latest advertisement. The message gives the signal
# strength heard from a station not reached: from bluetoothctl's own lines during those searches,
# since BlueZ forgets a device's RSSI when a search ends.
ATTEMPTS = 3
RETRY_S = 2.0
MAX_STATIONS = 8
_ADDRESS = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')
_NAME = re.compile(r'^LHB-[0-9A-Fa-f]{8}$')
_NEW_DEVICE = re.compile(r'Device ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}) (LHB-[0-9A-Fa-f]{8})\b')
# bluetoothctl's line for a new signal strength: "RSSI: -91" or, in newer versions, "RSSI: 0xffffffa5 (-91)".
_RSSI = re.compile(r'Device ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}) RSSI: (?:0x[0-9A-Fa-f]+ \()?(-?\d+)')
BUS = 'org.bluez'


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which(name)


def _short(text):
    lines = str(text or '').strip().splitlines()
    return (lines[-1].strip() if lines else '')[:160]


def _unwrap(value):
    """busctl --json values: {'type': ..., 'data': ...} -> data."""
    return value.get('data') if isinstance(value, dict) and 'data' in value else value


def parse_objects(text):
    """busctl --json=short output of ObjectManager.GetManagedObjects -> {path: {interface: {property: value}}}."""
    try:
        document = json.loads(text or '')
    except ValueError:
        return {}
    data = document.get('data') if isinstance(document, dict) else None
    data = data[0] if isinstance(data, list) and data else data
    if not isinstance(data, dict):
        return {}
    result = {}
    for path, interfaces in data.items():
        if not isinstance(path, str) or not isinstance(interfaces, dict):
            continue
        result[path] = {name: {key: _unwrap(value) for key, value in (properties or {}).items()}
                        for name, properties in interfaces.items() if isinstance(properties, dict)}
    return result


def adapters(objects):
    """[(path, powered)] of Bluetooth adapters."""
    return [(path, bool(interfaces['org.bluez.Adapter1'].get('Powered')))
            for path, interfaces in sorted(objects.items()) if 'org.bluez.Adapter1' in interfaces]


def stations(objects):
    """2.0 base stations BlueZ knows: [{'path', 'address', 'name', 'connected', 'resolved', 'rssi'}]
    (rssi: the signal strength in dBm BlueZ last saw while searching, or None)."""
    found = []
    for path, interfaces in sorted(objects.items()):
        device = interfaces.get('org.bluez.Device1')
        if not isinstance(device, dict):
            continue
        name = device.get('Name') or device.get('Alias') or ''
        address = str(device.get('Address') or '').upper()
        rssi = device.get('RSSI')
        if isinstance(name, str) and _NAME.fullmatch(name) and _ADDRESS.fullmatch(address):
            found.append({'path': path, 'address': address, 'name': 'LHB-' + name[4:].upper(),
                          'connected': bool(device.get('Connected')), 'resolved': bool(device.get('ServicesResolved')),
                          'rssi': rssi if isinstance(rssi, int) and not isinstance(rssi, bool) and -127 <= rssi <= 20 else None})
    return found[:MAX_STATIONS]


def power_characteristic(objects, device_path):
    """The power characteristic's object path under a station's device path, or None."""
    for path, interfaces in sorted(objects.items()):
        characteristic = interfaces.get('org.bluez.GattCharacteristic1')
        if (path.startswith(device_path + '/') and isinstance(characteristic, dict)
                and str(characteristic.get('UUID', '')).lower() == POWER_UUID):
            return path
    return None


class BaseStations:
    """Power commands for the 2.0 base stations near this PC (see the module notes)."""

    def __init__(self, state_directory, log=print, runner=system_run, popen=subprocess.Popen, busctl=None,
                 bluetoothctl=None, script=None, clock=time.time):
        self.state_path = Path(state_directory) / STATE_FILE
        self.log, self.runner, self.popen, self.clock = log, runner, popen, clock
        self.busctl = _tool('busctl', ('/usr/bin/busctl', '/bin/busctl')) if busctl is None else busctl
        self.bluetoothctl = (_tool('bluetoothctl', ('/usr/bin/bluetoothctl', '/bin/bluetoothctl'))
                             if bluetoothctl is None else bluetoothctl)
        self.script = Path(script) if script else Path(__file__).resolve().parent / 'BaseStations.ps1'
        self.lock = threading.Lock()
        self.working = threading.Lock()
        self.value = {'state': 'idle', 'message': ''}
        self.heard = {}  # address: the latest signal strength in dBm bluetoothctl printed during this command

    # ------------------------------------------------------------------ remembered stations and results
    def saved(self):
        try:
            value = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _remember(self, found, action, results, message):
        saved = self.saved()
        known = {row['address']: row['name'] for row in saved.get('stations', []) if isinstance(row, dict)
                 and _ADDRESS.fullmatch(str(row.get('address', ''))) and _NAME.fullmatch(str(row.get('name', '')))}
        for row in found:
            known[row['address']] = row['name']
        value = {'version': 1, 'stations': [{'address': address, 'name': name} for address, name in
                                            sorted(known.items())][:MAX_STATIONS],
                 'last': {'action': action, 'time': int(self.clock()), 'results': results, 'message': message}}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_name('.' + self.state_path.name + '.tmp')
            temporary.write_text(json.dumps(value, indent=1) + '\n', encoding='utf-8')
            os.replace(temporary, self.state_path)
        except OSError:
            pass

    def status(self):
        saved = self.saved()
        with self.lock:
            value = dict(self.value)
        value['stations'] = [row.get('name') for row in saved.get('stations', []) if isinstance(row, dict)][:MAX_STATIONS]
        value['last'] = saved.get('last') if isinstance(saved.get('last'), dict) else None
        return value

    def _set(self, state, message):
        with self.lock:
            self.value = {'state': state, 'message': message}

    # ------------------------------------------------------------------ commands
    def set_power(self, action, search=False):
        """Send 'on', 'off' or 'standby' to every 2.0 base station found; blocking (up to about 40 s).
        search: look for stations afresh (the page's buttons) instead of reaching the remembered ones.
        Returns {'action', 'results': [{'name', 'result', 'state'}], 'message'}."""
        if action not in COMMANDS:
            raise ValueError('Choose on, off or standby for the base stations.')
        if not self.working.acquire(blocking=False):
            return {'action': action, 'results': [], 'message': 'Another base station command is still running.'}
        try:
            self._set('working', 'Turning the base stations ' + action + '…')
            try:
                found, results, message = (self._windows(action, search) if sys.platform == 'win32'
                                           else self._linux(action, search))
            except Exception as error:  # a power command never takes the companion down
                found, results, message = [], [], 'Base stations: ' + _short(error)
            self._remember(found, action, results, message)
            self._set('idle', message)
            if message:
                self.log('BASE_STATIONS ' + message)
            return {'action': action, 'results': results, 'message': message}
        finally:
            self.working.release()

    def set_power_background(self, action, search=False):
        threading.Thread(target=self.set_power, args=(action, search), daemon=True,
                         name='VROSTBridge-base-stations').start()

    def detached(self, action, companion_command):
        """Start the command as its own process (closing VROSTBridge: the companion does not wait for it).
        companion_command: how to start this companion (Linux). Returns True when started."""
        if action not in COMMANDS:
            return False
        try:
            if sys.platform == 'win32':
                arguments = self._powershell(action)
                flags = (getattr(subprocess, 'DETACHED_PROCESS', 0x8) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)
                         | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
                self.popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=flags, close_fds=True)
            else:
                self.popen([*companion_command, '--base-stations', action], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            return True
        except (OSError, ValueError) as error:
            self.log('BASE_STATIONS could not start the ' + action + ' command: ' + _short(error))
            return False

    # ------------------------------------------------------------------ Linux (BlueZ)
    def _run(self, arguments, timeout):
        try:
            return self.runner(arguments, timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, '', _short(error))

    def _objects(self):
        result = self._run([self.busctl, '--system', '--json=short', 'call', BUS, '/',
                            'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects'], 10)
        return parse_objects(result.stdout) if result.returncode == 0 else None

    def _scan(self, seconds=SCAN_S):
        """Look for stations for a few seconds; [(address, name)] seen."""
        if not self.bluetoothctl:
            return []
        result = self._run([self.bluetoothctl, '--timeout', str(seconds), 'scan', 'on'], seconds + 8)
        seen = []
        for address, name in _NEW_DEVICE.findall(result.stdout or ''):
            if (address.upper(), name) not in seen:
                seen.append((address.upper(), name))
        for address, value in _RSSI.findall(result.stdout or ''):
            if -127 <= int(value) <= 20:
                self.heard[address.upper()] = int(value)
        return seen

    def _call(self, path, interface, method, *arguments, timeout=CONNECT_S):
        return self._run([self.busctl, '--system', '--timeout=' + str(timeout), 'call', BUS, path, interface, method,
                          *[str(item) for item in arguments]], timeout + 3)

    def _linux(self, action, search=False):
        if not self.busctl:
            return [], [], 'Bluetooth (BlueZ) tools are not available on this PC.'
        self.heard = {}
        objects = self._objects()
        if objects is None:
            return [], [], 'Bluetooth is not available on this PC (BlueZ is not running).'
        if not any(powered for _, powered in adapters(objects)):
            return [], [], 'Bluetooth is off on this PC, so the base stations were not changed.'
        found = stations(objects)
        known = {row.get('address') for row in self.saved().get('stations', []) if isinstance(row, dict)}
        if search or (not found and not known):
            self._scan(SCAN_S)  # a full search: the buttons, or nothing remembered yet
            objects = self._objects() or objects
            found = stations(objects)
        else:
            waited = 0
            # The remembered stations BlueZ has forgotten: short searches until each is seen again.
            while known and not known <= {row['address'] for row in found} and waited < SCAN_S:
                self._scan(SCAN_ROUND_S)
                waited += SCAN_ROUND_S
                objects = self._objects() or objects
                found = stations(objects)
        if not found:
            return [], [], 'No SteamVR 2.0 base stations were found nearby.'
        results = []
        for row in found:
            for attempt in range(ATTEMPTS):
                if attempt:
                    # 1.0.0.10: look for it again first (about RETRY_S), so the next try starts from its latest
                    # advertisement, with its current signal strength.
                    self._scan(int(RETRY_S))
                    fresh = stations(self._objects() or {})
                    row = next((item for item in fresh if item['address'] == row['address']), row)
                result = self._linux_station(row, action)
                if result['result'] == 'sent':
                    break
            strength = self.heard.get(row['address'], row.get('rssi'))  # BlueZ keeps RSSI only while searching
            if result['result'] != 'sent' and strength is not None:
                result['signal_dbm'] = strength
            results.append(result)
        return found, results, self._summary(action, results)

    def _linux_station(self, row, action):
        result = {'name': row['name'], 'result': 'failed', 'state': None}
        path = row['path']
        try:
            if not row['connected']:
                connected = self._call(path, 'org.bluez.Device1', 'Connect')
                if connected.returncode != 0:
                    result['error'] = _short(connected.stderr or connected.stdout) or 'could not connect'
                    return result
            characteristic = None
            deadline = time.monotonic() + RESOLVE_S
            while time.monotonic() < deadline:
                objects = self._objects() or {}
                device = (objects.get(path) or {}).get('org.bluez.Device1') or {}
                characteristic = power_characteristic(objects, path) if device.get('ServicesResolved') else None
                if characteristic:
                    break
                time.sleep(0.25)
            if not characteristic:
                result['error'] = 'its power setting did not appear'
                return result
            written = self._call(characteristic, 'org.bluez.GattCharacteristic1', 'WriteValue', 'aya{sv}', 1,
                                 COMMANDS[action], 0, timeout=8)
            if written.returncode != 0:
                written = self._call(characteristic, 'org.bluez.GattCharacteristic1', 'WriteValue', 'aya{sv}', 1,
                                     COMMANDS[action], 1, 'type', 's', 'command', timeout=8)
            if written.returncode != 0:
                result['error'] = _short(written.stderr or written.stdout) or 'the command was refused'
                return result
            result['result'] = 'sent'
            read = self._run([self.busctl, '--system', '--json=short', '--timeout=8', 'call', BUS, characteristic,
                              'org.bluez.GattCharacteristic1', 'ReadValue', 'a{sv}', '0'], 11)
            try:
                data = json.loads(read.stdout).get('data') if read.returncode == 0 else None
                value = data[0][0] if isinstance(data, list) and data and isinstance(data[0], list) and data[0] else None
                result['state'] = STATES.get(value) if isinstance(value, int) else None
            except (ValueError, AttributeError, IndexError, TypeError):
                pass
            return result
        finally:
            if not row['connected']:
                self._call(path, 'org.bluez.Device1', 'Disconnect', timeout=5)

    # ------------------------------------------------------------------ Windows (PowerShell 5.1, WinRT)
    def _powershell(self, action, search=False):
        system_root = os.environ.get('SystemRoot', r'C:\Windows')
        executable = str(Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
        known = [] if search else [
            str(row['address']) + '=' + str(row['name']) for row in self.saved().get('stations', [])
            if isinstance(row, dict) and _ADDRESS.fullmatch(str(row.get('address', '')))
            and _NAME.fullmatch(str(row.get('name', '')))]
        arguments = [executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                     '-File', str(self.script), '-Action', action, '-ScanSeconds', str(SCAN_S),
                     '-StatePath', str(self.state_path)]
        if known:
            arguments += ['-Known', ','.join(known)]
        return arguments

    def _windows(self, action, search=False):
        result = self._run(self._powershell(action, search) + ['-NoState'], 60)
        try:
            value = json.loads((result.stdout or '').strip().splitlines()[-1])
        except (ValueError, IndexError):
            return [], [], 'Base stations: ' + (_short(result.stderr) or 'Windows could not run the Bluetooth command.')
        if value.get('error'):
            return [], [], str(value['error'])[:300]
        found = [{'address': str(row.get('address', '')).upper(), 'name': str(row.get('name', ''))}
                 for row in value.get('stations') or [] if isinstance(row, dict)
                 and _ADDRESS.fullmatch(str(row.get('address', '')).upper()) and _NAME.fullmatch(str(row.get('name', '')))]
        results = [{'name': str(row.get('name', ''))[:16], 'result': 'sent' if row.get('result') == 'sent' else 'failed',
                    'state': row.get('state') if row.get('state') in STATES.values() else None,
                    **({'error': str(row['error'])[:160]} if row.get('error') else {})}
                   for row in value.get('stations') or [] if isinstance(row, dict)]
        if not found:
            return [], [], 'No SteamVR 2.0 base stations were found nearby.'
        return found, results, self._summary(action, results)

    @staticmethod
    def _summary(action, results):
        done = [row['name'] for row in results if row['result'] == 'sent']
        failed = [row for row in results if row['result'] != 'sent']
        verb = {'off': 'turned off', 'on': 'turned on', 'standby': 'put on standby'}[action]
        message = (str(len(done)) + ' of ' + str(len(results)) + ' base stations ' + verb +
                   (' (' + ', '.join(done) + ')' if done else '') + '.')
        if failed:
            def why(row):
                parts = ([row['error']] if row.get('error') else []) + \
                        (['signal ' + str(row['signal_dbm']) + ' dBm'] if isinstance(row.get('signal_dbm'), int) else [])
                return ' (' + '; '.join(parts) + ')' if parts else ''
            message += ' Not reached: ' + ', '.join(row['name'] + why(row) for row in failed) + '.'
        return message


def main(arguments):
    """`--base-stations on|off|standby`: the command on its own (closing the companion starts it this way)."""
    action = arguments[0] if arguments else ''
    if action not in COMMANDS:
        print('usage: --base-stations on|off|standby', file=sys.stderr)
        return 2
    if sys.platform != 'win32':
        # Started as VROSTBridge closes: the command finishes (every step has a time limit) even when what started
        # VROSTBridge ends its processes as it goes, such as Steam's Exit game in Game Mode.
        import signal
        for name in ('SIGTERM', 'SIGHUP'):
            try:
                signal.signal(getattr(signal, name), signal.SIG_IGN)
            except (AttributeError, OSError, ValueError):
                pass
    base =Path(os.environ.get('LOCALAPPDATA', os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))) \
        / 'VROSTBridge'
    outcome = BaseStations(base, log=lambda line: None).set_power(action)  # the message is printed once, below
    print(outcome['message'])
    return 0 if outcome['results'] and all(row['result'] == 'sent' for row in outcome['results']) else 1
