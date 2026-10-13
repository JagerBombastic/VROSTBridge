"""Host PC power: stay awake during a session, sleep after Stop, Wake-on-LAN readiness.

The host PC sits in another room and is used only through VROSTBridge, so
Windows sees nobody at it:

* During a session Windows could go to sleep mid-game: the player's
  controllers reach SteamVR as driver data, which Windows does not count as
  user activity. KeepAwake asks Windows to keep the system (not the display)
  on for the session only. Windows ties that request to the calling thread
  and drops it when the thread ends, so a dedicated thread holds it until
  release(); setting it from a short-lived worker would have no lasting
  effect.
* After Stop the PC can be left running all night. SleepAfterStop (opt-in)
  puts it to sleep a set time later, counting down only while nothing needs
  it: no session, SteamVR closed and nobody using the host PC itself. It uses
  sleep, not hibernation or shutdown, because waking from sleep is the most
  reliable Wake-on-LAN case and needs no sign-in afterwards.
* For launcher/wake_host.py to wake the host, its wired adapter must have
  "Wake on Magic Packet" turned on. wake_readiness() reads that and related
  settings without changing anything, so the host page can say what to
  change.

Everything Windows-specific is injectable so the logic is tested on Linux.
"""
import ctypes
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from launcher.wake_host import normalise_mac

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
CHECK_SECONDS = 15.0
INPUT_TOLERANCE_SECONDS = 2.0


def powershell_path():
    """Windows PowerShell 5.1 by full path, so a program named powershell.exe elsewhere is never run."""
    root = os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    if root:
        candidate = Path(root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        if candidate.is_file():
            return str(candidate)
    return 'powershell.exe'


def windows_run(arguments, timeout):
    """Run a console program without a window; text output decoded as UTF-8."""
    if (not isinstance(arguments, list) or not arguments
            or not all(isinstance(argument, str) and '\0' not in argument for argument in arguments)):
        raise ValueError('Invalid command.')
    return subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def run_powershell(script, runner=None, timeout=30):
    """Run a fixed, read-only PowerShell script (no double quotes, so command-line quoting is simple)."""
    return (runner or windows_run)([powershell_path(), '-NoProfile', '-NonInteractive', '-Command', script], timeout)


def _load_kernel32():
    if os.name != 'nt':
        return None
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
        return kernel32
    except (AttributeError, OSError):
        return None


class KeepAwake:
    """Keep Windows from sleeping while a session runs (the display may still turn off)."""

    def __init__(self, kernel32=None, log=print):
        self.kernel32 = kernel32 if kernel32 is not None else _load_kernel32()
        self.log = log
        self.lock = threading.Lock()
        self.thread = self.release_event = None
        self.generation = 0
        if self.kernel32 is None:
            self.value = {'state': 'unsupported', 'message': 'Keeping the PC awake is only available on Windows.'}
        else:
            self.value = {'state': 'idle', 'message': ''}

    def status(self):
        with self.lock:
            return dict(self.value)

    def _set(self, generation, state, message):
        with self.lock:
            if generation != self.generation:
                return
            self.value = {'state': state, 'message': message}
        if message:
            self.log('POWER ' + state + ': ' + message)

    def _hold(self, generation, ready, release):
        try:
            accepted = self.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception as error:  # A missing or broken API must not stop a session.
            accepted = 0
            self.log('POWER error: ' + str(error)[:200])
        if not accepted:
            self._set(generation, 'failed', 'Windows did not accept the request to stay awake during the session; '
                      'it may go to sleep if left idle.')
            ready.set()
            return
        self._set(generation, 'active', 'The host PC stays awake during the session.')
        ready.set()
        release.wait()
        try:
            self.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass  # The request lapses when this thread ends anyway.

    def acquire(self):
        """Start holding the request; idempotent. Returns status()."""
        with self.lock:
            if self.kernel32 is None or (self.thread is not None and self.thread.is_alive()):
                return dict(self.value)
            self.generation += 1
            ready, release = threading.Event(), threading.Event()
            self.thread = threading.Thread(target=self._hold, args=(self.generation, ready, release), daemon=True,
                                           name='VROSTBridgeKeepAwake')
            self.release_event = release
            self.thread.start()
        ready.wait(2)
        return self.status()

    def release(self):
        """Clear the request on the thread that set it; idempotent."""
        with self.lock:
            thread, release = self.thread, self.release_event
            self.thread = self.release_event = None
            if thread is None:
                return dict(self.value)
            self.generation += 1
            self.value = {'state': 'idle', 'message': ''}
        release.set()
        thread.join(2)
        return self.status()


def windows_input_idle_seconds():
    """Seconds since the last keyboard or mouse input in this Windows session, or None."""
    if os.name != 'nt':
        return None

    class LastInputInfo(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint32), ('dwTime', ctypes.c_uint32)]
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInputInfo)]
        user32.GetLastInputInfo.restype = ctypes.c_int
        kernel32.GetTickCount.argtypes = []
        kernel32.GetTickCount.restype = ctypes.c_uint32
        info = LastInputInfo(ctypes.sizeof(LastInputInfo), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Both are 32-bit millisecond tick counts that wrap after 49.7 days.
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except (AttributeError, OSError):
        return None


def suspend_windows():
    """Sleep (not hibernate), wake events enabled so Wake-on-LAN can wake the PC.

    Called directly rather than through rundll32, which passes its arguments
    wrongly and can hibernate instead. Returns after the PC wakes again.
    """
    powrprof = ctypes.WinDLL('powrprof', use_last_error=True)
    powrprof.SetSuspendState.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
    powrprof.SetSuspendState.restype = ctypes.c_ubyte
    if not powrprof.SetSuspendState(0, 0, 0):
        raise OSError(ctypes.get_last_error(), 'Windows did not go to sleep.')
    return True


def _minutes(value):
    if value is None or value is False or value == 0:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 < value <= 1440:
        raise ValueError('Sleep after Stop needs a delay from 1 to 1440 minutes, or 0 for off.')
    return value


class SleepAfterStop:
    """Put the host PC to sleep `minutes` after a session ends, if it stays unused.

    Call arm() once Stop has finished (SteamVR closed) and cancel() when a
    session starts. is_idle() must return True only while there is no
    session and SteamVR is not running; a False answer cancels the countdown
    rather than pausing it, because SteamVR running after Stop means someone
    is using it. User activity on the host PC after arm() also cancels it;
    input_idle_seconds() returns seconds since the last input
    (GetLastInputInfo by default on Windows, skipped elsewhere). Conditions
    are re-checked every 15 s.
    """

    def __init__(self, minutes, is_idle, suspend=None, clock=time.monotonic, log=print, input_idle_seconds=None,
                 check_seconds=CHECK_SECONDS):
        self.is_idle = is_idle
        self.suspend = suspend if suspend is not None else suspend_windows
        self.clock, self.log, self.check_seconds = clock, log, check_seconds
        if input_idle_seconds is None and os.name == 'nt':
            input_idle_seconds = windows_input_idle_seconds
        self.input_idle_seconds = input_idle_seconds
        self.lock = threading.Lock()
        self.generation = 0
        self.stop_event = None
        self.armed_at = self.deadline = None
        self.minutes = _minutes(minutes)
        self.state, self.message = ('off' if self.minutes is None else 'idle'), ''

    def configure(self, minutes):
        """Change the delay for the next arm(); 0 or None turns the feature off (cancelling a countdown)."""
        value = _minutes(minutes)
        with self.lock:
            self.minutes = value
        if value is None:
            self._stop('off', '')
        else:
            with self.lock:
                if self.state == 'off':
                    self.state, self.message = 'idle', ''
        return self.status()

    def status(self):
        with self.lock:
            value = {'state': self.state, 'message': self.message, 'minutes': self.minutes, 'remaining_seconds': None}
            if self.state == 'armed' and self.deadline is not None:
                value['remaining_seconds'] = max(0, int(math.ceil(self.deadline - self.clock())))
        return value

    def arm(self):
        """Start (or restart) the countdown; no-op when the feature is off."""
        with self.lock:
            if self.minutes is None:
                self.state, self.message = 'off', ''
                return self._snapshot()
            self.generation += 1
            if self.stop_event is not None:
                self.stop_event.set()
            stop = self.stop_event = threading.Event()
            self.armed_at = self.clock()
            self.deadline = self.armed_at + self.minutes * 60
            minutes = self.minutes
            self.state = 'armed'
            self.message = ('The host PC will go to sleep in ' + _duration(minutes) +
                            ' unless a session starts or someone uses it.')
            thread = threading.Thread(target=self._countdown, args=(self.generation, stop), daemon=True,
                                      name='VROSTBridgeSleepAfterStop')
        self.log('SLEEP armed: ' + self.message)
        thread.start()
        return self.status()

    def cancel(self, reason='Sleep after Stop cancelled.'):
        """Stop a countdown (call when a session starts)."""
        return self._stop('cancelled', reason)

    def close(self):
        self._stop('idle', '')

    def _snapshot(self):
        return {'state': self.state, 'message': self.message, 'minutes': self.minutes, 'remaining_seconds': None}

    def _stop(self, state, message):
        with self.lock:
            armed = self.state == 'armed'
            self.generation += 1
            if self.stop_event is not None:
                self.stop_event.set()
                self.stop_event = None
            self.deadline = None
            if armed or state == 'off':
                self.state, self.message = state, message
        if armed and message:
            self.log('SLEEP ' + state + ': ' + message)
        return self.status()

    def _countdown(self, generation, stop):
        while True:
            with self.lock:
                if generation != self.generation or self.deadline is None:
                    return
                remaining = self.deadline - self.clock()
            if stop.wait(max(0.0, min(self.check_seconds, remaining))):
                return
            if self.poll(generation) != 'waiting':
                return

    def _busy(self, armed_at):
        """Why the PC is in use, or '' when it is idle."""
        try:
            idle = self.is_idle()
        except Exception as error:  # Unknown means do not sleep.
            return ('Sleep after Stop cancelled: VROSTBridge could not check that the host PC is idle ('
                    + str(error)[:120] + ').')
        if not idle:
            return 'Sleep after Stop cancelled: a session started or SteamVR is running.'
        if self.input_idle_seconds is not None:
            try:
                seconds = self.input_idle_seconds()
            except Exception:
                seconds = None
            if seconds is not None and seconds + INPUT_TOLERANCE_SECONDS < self.clock() - armed_at:
                return 'Sleep after Stop cancelled because someone is using the host PC.'
        return ''

    def poll(self, generation=None):
        """One check: cancel if the PC is in use, sleep when the time is up.

        Returns 'waiting', 'cancelled', 'suspended', 'failed' or 'stale'. The
        countdown thread calls this every 15 s; tests can call it directly.
        """
        with self.lock:
            generation = self.generation if generation is None else generation
            if generation != self.generation or self.state != 'armed':
                return 'stale'
            armed_at, deadline = self.armed_at, self.deadline
        reason = self._busy(armed_at)
        with self.lock:
            if generation != self.generation or self.state != 'armed':
                return 'stale'
            if not reason and self.clock() < deadline:
                return 'waiting'
            self.generation += 1
            mine = self.generation
            if self.stop_event is not None:
                self.stop_event.set()
                self.stop_event = None
            self.deadline = None
            if reason:
                self.state, self.message = 'cancelled', reason
            else:
                self.state, self.message = 'suspending', 'Putting the host PC to sleep.'
        if reason:
            self.log('SLEEP cancelled: ' + reason)
            return 'cancelled'
        self.log('SLEEP suspending: Putting the host PC to sleep after Stop.')
        try:
            self.suspend()
        except Exception as error:
            self._finish(mine, 'failed', 'The host PC could not be put to sleep: ' + str(error)[:160])
            return 'failed'
        self._finish(mine, 'idle', 'The host PC went to sleep after Stop.')
        return 'suspended'

    def _finish(self, generation, state, message):
        with self.lock:
            if generation != self.generation:
                return
            self.state, self.message = state, message
        self.log('SLEEP ' + state + ': ' + message)


def _duration(minutes):
    if minutes >= 1 and float(minutes).is_integer():
        return str(int(minutes)) + (' minute' if minutes == 1 else ' minutes')
    return str(int(round(minutes * 60))) + ' seconds'


# Physical adapters that are up, with their Wake-on-LAN settings; `powercfg /a`
# (sleep states; it needs no administrator rights) and the Fast Startup value.
# No double quotes: the script travels as one command-line argument.
# wake_armed: devices with Allow this device to wake the computer ticked. Many drivers (USB and 2.5G
# adapters) report Wake on Magic Packet only as the standard *WakeOnMagicPacket advanced property.
WAKE_READINESS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$states = (& powercfg.exe /a 2>$null) -join [string][char]10
$armed = @(& powercfg.exe /devicequery wake_armed 2>$null | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -and $_ -ne 'NONE' })
$armedKnown = ($LASTEXITCODE -eq 0)
$adapters = @(Get-NetAdapter -Physical | Where-Object { [string]$_.Status -eq 'Up' } | ForEach-Object {
  $power = Get-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue
  $advanced = Get-NetAdapterAdvancedProperty -Name $_.Name -RegistryKeyword '*WakeOnMagicPacket' -ErrorAction SilentlyContinue
  [pscustomobject]@{
    Name = [string]$_.Name
    InterfaceDescription = [string]$_.InterfaceDescription
    MacAddress = [string]$_.MacAddress
    LinkSpeed = [string]$_.LinkSpeed
    TransmitLinkSpeed = $_.TransmitLinkSpeed
    MediaType = [string]$_.MediaType
    PhysicalMediaType = [string]$_.PhysicalMediaType
    WakeOnMagicPacket = $(if ($power) { [string]$power.WakeOnMagicPacket } else { $null })
    DeviceSleepOnDisconnect = $(if ($power) { [string]$power.DeviceSleepOnDisconnect } else { $null })
    WakeOnMagicPacketAdvanced = $(if ($advanced) { [string](@($advanced.RegistryValue)[0]) } else { $null })
    WakeArmed = $(if ($armedKnown) { $armed -contains [string]$_.InterfaceDescription } else { $null })
  }
})
$hiberboot = $null
$powerKey = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
if ($powerKey) { $hiberboot = [int]$powerKey.HiberbootEnabled }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
ConvertTo-Json -Compress -Depth 4 -InputObject ([pscustomobject]@{ adapters = $adapters; sleep_states = $states; hiberboot = $hiberboot })
"""

# Every IPv4 address with the adapter that owns it (including virtual adapters).
ADAPTER_ADDRESSES_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$adapters = @{}
foreach ($adapter in @(Get-NetAdapter)) { $adapters[[int]$adapter.ifIndex] = $adapter }
$rows = @(Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object {
  $adapter = $adapters[[int]$_.InterfaceIndex]
  if ($adapter) {
    [pscustomobject]@{
      IPAddress = [string]$_.IPAddress
      PrefixLength = [int]$_.PrefixLength
      InterfaceIndex = [int]$_.InterfaceIndex
      Name = [string]$adapter.Name
      InterfaceDescription = [string]$adapter.InterfaceDescription
      MacAddress = [string]$adapter.MacAddress
      LinkSpeed = [string]$adapter.LinkSpeed
      TransmitLinkSpeed = $adapter.TransmitLinkSpeed
      MediaType = [string]$adapter.MediaType
      PhysicalMediaType = [string]$adapter.PhysicalMediaType
      Status = [string]$adapter.Status
      Hardware = [bool]$adapter.HardwareInterface
    }
  }
})
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
ConvertTo-Json -Compress -Depth 3 -InputObject $rows
"""

# Get-NetAdapterPowerManagement settings, as names or their documented numbers.
_POWER_SETTINGS = {'enabled': True, '2': True, 'disabled': False, 'inactive': False, '1': False,
                   'unsupported': None, '0': None}
_SPEED_UNITS = {'': 1e-6, 'k': 1e-3, 'm': 1.0, 'g': 1e3, 't': 1e6}


def _json_value(text):
    if not isinstance(text, str):
        raise ValueError('No output from PowerShell.')
    text = text.lstrip('﻿').strip()
    if not text or len(text) > 1_000_000:
        raise ValueError('No output from PowerShell.')
    return json.loads(text)


def _rows(value):
    if isinstance(value, dict):
        return [value]
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value, limit=128):
    return value.strip()[:limit] if isinstance(value, str) else ''


def link_speed_mbps(value):
    """Mbit/s from bits per second (number) or Windows text such as '2.5 Gbps'; None if unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value / 1e6)) if math.isfinite(value) and value >= 1e6 else None
    match = re.fullmatch(r'\s*([0-9]+(?:[.,][0-9]+)?)\s*([kKmMgGtT]?)\s*(?:bps|bit/s)\s*', value or '') \
        if isinstance(value, str) else None
    if not match:
        return None
    speed = float(match.group(1).replace(',', '.')) * _SPEED_UNITS[match.group(2).lower()]
    return int(round(speed)) if speed >= 1 else None


def _media_kind(*values):
    text = ' '.join(value for value in values if isinstance(value, str)).lower()
    if '802.11' in text or 'wireless' in text:
        return 'wifi'
    if '802.3' in text or 'ethernet' in text:
        return 'wired'
    return 'other'


def _power_setting(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        value = str(value)
    return _POWER_SETTINGS.get(value.strip().lower()) if isinstance(value, str) else None


def _advanced_setting(value):
    """The standard *WakeOnMagicPacket advanced property: 1 enabled, 0 disabled."""
    value = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''
    return {'1': True, '0': False}.get(value)


def _adapter(row):
    speed = link_speed_mbps(row.get('TransmitLinkSpeed'))
    return {'name': _text(row.get('Name')), 'description': _text(row.get('InterfaceDescription'), 256),
            'mac': normalise_mac(row.get('MacAddress')),
            'link_mbps': speed if speed is not None else link_speed_mbps(row.get('LinkSpeed')),
            'kind': _media_kind(row.get('MediaType'), row.get('PhysicalMediaType'))}


def parse_sleep_states(text):
    """Availability of S3 sleep and S0 Low Power Idle (Modern Standby) from `powercfg /a`.

    The output lists available states under a first heading and unavailable
    ones under a second, each state indented beneath its heading and its
    reasons indented further. Headings and names are translated, but the
    ACPI codes such as (S3) are not, so only those codes are matched.
    """
    result = {'s3': None, 's0_low_power_idle': None}
    if not isinstance(text, str):
        return result
    section, item_indent = 0, None
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if not indent:
            section, item_indent = section + 1, None
            continue
        if section not in (1, 2):
            continue
        if item_indent is None:
            item_indent = indent
        if indent != item_indent:
            continue  # A reason line beneath a state.
        upper = line.upper()
        for key, code in (('s3', 'S3'), ('s0_low_power_idle', 'S0')):
            if result[key] is None and re.search(r'\b' + code + r'\b', upper):
                result[key] = section == 1
    return result


def _device(adapter):
    name = adapter.get('name') or 'the wired adapter'
    description = adapter.get('description') or ''
    return name + (' (' + description + ')' if description and description != name else '')


def wake_message(adapters, fast_startup, preferred=''):
    """Guidance for waking this PC; preferred names the adapter the session uses, when known."""
    wired = [adapter for adapter in adapters if adapter['kind'] == 'wired']
    chosen = [adapter for adapter in wired if preferred and adapter.get('name') == preferred]
    wired = chosen or wired
    enabled = [adapter for adapter in wired if adapter['wake_on_magic_packet'] is True]
    disabled = [adapter for adapter in wired if adapter['wake_on_magic_packet'] is False]
    if enabled and enabled[0].get('wake_armed') is False:
        first = ('"Wake on Magic Packet" is on for ' + _device(enabled[0]) + ', but Windows does not let it wake the '
                 'PC: in Device Manager open the adapter\'s Properties and, on the Power Management tab, tick "Allow '
                 'this device to wake the computer" (and "Only allow a magic packet to wake the computer").')
    elif enabled:
        first = 'Wake from sleep is ready: "Wake on Magic Packet" is on for ' + (enabled[0]['name'] or 'the wired adapter') + '.'
    elif disabled:
        first = ('To wake this PC from sleep, turn on "Wake on Magic Packet" for ' + (disabled[0]['name'] or 'the wired adapter')
                 + ': in Device Manager open the adapter\'s Properties, set "Wake on Magic Packet" to Enabled on the '
                 'Advanced tab and tick "Allow this device to wake the computer" on the Power Management tab.')
    elif wired:
        first = (_device(wired[0]) + ' does not report Wake-on-LAN support to Windows, so waking this PC from sleep '
                 'may not work. If its driver supports it, open Device Manager, Network adapters, the adapter\'s '
                 'Properties: on the Advanced tab set "Wake on Magic Packet" (or "Wake on LAN") to Enabled, and on '
                 'the Power Management tab tick "Allow this device to wake the computer". Installing the network '
                 'driver from the PC or adapter maker often adds these settings; USB Ethernet adapters usually cannot '
                 'wake a PC, a motherboard Ethernet port usually can.')
    else:
        first = ('Waking this PC from sleep needs a wired (Ethernet) connection with "Wake on Magic Packet" turned on; '
                 'Wi-Fi adapters rarely support it.')
    if fast_startup is True:
        second = ('Waking from a full shutdown also needs Wake-on-LAN enabled in the BIOS (UEFI) settings, and Fast '
                  'Startup is on, which usually stops it: turn it off in Control Panel, Power Options, "Choose what '
                  'the power buttons do".')
    elif fast_startup is False:
        second = ('Waking from a full shutdown also needs Wake-on-LAN enabled in the BIOS (UEFI) settings; Fast '
                  'Startup is already off.')
    else:
        second = ('Waking from a full shutdown also needs Wake-on-LAN enabled in the BIOS (UEFI) settings and usually '
                  'Fast Startup turned off.')
    third = ('After a full shutdown Windows stops at the sign-in screen unless automatic sign-in is set up, and '
             'VROSTBridge only starts after sign-in.')
    return ' '.join((first, second, third))


def parse_wake_readiness(text):
    """Turn WAKE_READINESS_SCRIPT output into a readiness report (raises ValueError if unreadable)."""
    value = _json_value(text)
    if not isinstance(value, dict):
        raise ValueError('Unexpected Wake-on-LAN settings output.')
    adapters = []
    for row in _rows(value.get('adapters'))[:16]:
        adapter = _adapter(row)
        adapter['wake_on_magic_packet'] = _power_setting(row.get('WakeOnMagicPacket'))
        if adapter['wake_on_magic_packet'] is None:
            adapter['wake_on_magic_packet'] = _advanced_setting(row.get('WakeOnMagicPacketAdvanced'))
        adapter['device_sleep_on_disconnect'] = _power_setting(row.get('DeviceSleepOnDisconnect'))
        armed = row.get('WakeArmed')
        adapter['wake_armed'] = armed if type(armed) is bool else None
        adapters.append(adapter)
    hiberboot = value.get('hiberboot')
    fast_startup = hiberboot == 1 if type(hiberboot) is int else None
    ready = any(adapter['kind'] == 'wired' and adapter['wake_on_magic_packet'] is True and adapter['wake_armed'] is not False
                for adapter in adapters)
    return {'adapters': adapters, 'sleep_states': parse_sleep_states(value.get('sleep_states')),
            'fast_startup': fast_startup, 'ready': ready, 'error': False,
            'message': wake_message(adapters, fast_startup)}


def wake_readiness(runner=None):
    """Read-only Wake-on-LAN readiness report for the host page; never raises."""
    try:
        result = run_powershell(WAKE_READINESS_SCRIPT, runner, 30)
        if result.returncode != 0:
            raise ValueError((result.stderr or '').strip()[:200] or 'PowerShell reported an error.')
        return parse_wake_readiness(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'adapters': [], 'sleep_states': {'s3': None, 's0_low_power_idle': None}, 'fast_startup': None,
                'ready': False, 'error': True,
                'message': 'Wake-on-LAN settings could not be read: ' + str(error)[:200]}


def parse_adapter_addresses(text):
    """Turn ADAPTER_ADDRESSES_SCRIPT output into one dict per IPv4 address (raises ValueError if unreadable)."""
    result = []
    for row in _rows(_json_value(text))[:256]:
        try:
            address = str(ipaddress.IPv4Address(row.get('IPAddress')))
        except (ValueError, TypeError):
            continue
        prefix = row.get('PrefixLength')
        entry = _adapter(row)
        entry.update(ip=address, prefix=prefix if type(prefix) is int and 0 <= prefix <= 32 else None,
                     index=row.get('InterfaceIndex') if type(row.get('InterfaceIndex')) is int else None,
                     status=_text(row.get('Status'), 32), physical=row.get('Hardware') is True)
        result.append(entry)
    return result


def adapter_addresses(runner=None):
    """IPv4 addresses with their adapters; [] when Windows cannot be queried."""
    try:
        result = run_powershell(ADAPTER_ADDRESSES_SCRIPT, runner, 30)
        return parse_adapter_addresses(result.stdout) if result.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def adapter_for_ip(local_ip, adapters):
    """The adapter owning local_ip: {'name', 'mac', 'link_mbps', 'kind', 'description', 'physical'} or None.

    adapters is the list from parse_adapter_addresses()/adapter_addresses();
    raw PowerShell rows (with IPAddress/MacAddress keys) are accepted too.
    """
    try:
        wanted = str(ipaddress.IPv4Address(local_ip)) if isinstance(local_ip, str) else None
    except ValueError:
        wanted = None
    if wanted is None:
        return None
    for row in adapters or ():
        if not isinstance(row, dict):
            continue
        if 'IPAddress' in row:
            parsed = parse_adapter_addresses(json.dumps([row]))
            row = parsed[0] if parsed else {}
        if row.get('ip') == wanted:
            return {'name': row.get('name', ''), 'mac': row.get('mac'), 'link_mbps': row.get('link_mbps'),
                    'kind': row.get('kind', 'other'), 'description': row.get('description', ''),
                    'physical': row.get('physical')}
    return None
