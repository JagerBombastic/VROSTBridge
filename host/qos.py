"""Optional Windows QoS policy that marks the host's video packets for Wi-Fi priority.

Wi-Fi access points and adapters queue packets in four priority classes
(WMM) chosen from each packet's DSCP mark; packets marked 46 (Expedited
Forwarding) go in the voice class, which waits least for airtime. Since RC2
the session profile asks ALVR to mark its packets, and the Linux client's
uplink is marked that way, but Windows ignores marks that applications set
themselves: only a QoS policy can mark packets. This module manages one
policy, VROSTBridgeVideo, that marks UDP packets sent by vrserver.exe, the
SteamVR process the streaming driver runs in. It helps only where the Wi-Fi
equipment honours WMM; wired networks and most routers ignore the mark, and
some access points aggregate voice-class traffic less, so it stays optional
and is worth comparing with the network test.

On a PC that is not in a Windows domain, Windows applies QoS policies only
when HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\QoS "Do not use NLA"
(REG_SZ) is "1", and it reads that value at start-up, so a restart is
needed. The value affects every QoS policy on the PC, so VROSTBridge sets it
only if it was not already "1", records that it did (VROSTBridgeSet = "1" and
the time under HKLM\\SOFTWARE\\VROSTBridge), and on disable removes it only if
VROSTBridge set it and no other QoS policy remains.

Changing either needs administrator rights, so the scripts run through one
UAC prompt. They are written to a .ps1 file and run with -File rather than
as an encoded command, so the UAC details show a readable path and security
software does not see an obfuscated command. Both scripts are idempotent.
"""
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

from host.power import powershell_path, run_powershell, windows_run

POLICY_NAME = 'VROSTBridgeVideo'
APP_NAME = 'vrserver.exe'
DSCP = 46
QOS_KEY = r'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\QoS'
NLA_VALUE = 'Do not use NLA'
MARKER_KEY = r'HKLM:\SOFTWARE\VROSTBridge'
MARKER_VALUE = 'VROSTBridgeSet'
MARKER_TIME_VALUE = 'VROSTBridgeSetUtc'
ERROR_CANCELLED = 1223  # The user declined the UAC prompt.
UNKNOWN_RESULT = 3

# Read-only. No double quotes: the script travels as one command-line argument.
STATUS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$policy = Get-NetQosPolicy -Name 'VROSTBridgeVideo' -ErrorAction SilentlyContinue | Select-Object -First 1
$found = $null
if ($policy) {
  $found = [pscustomobject]@{
    Name = [string]$policy.Name
    AppPath = [string]$(if ($policy.AppPathNameMatchCondition) { $policy.AppPathNameMatchCondition } else { $policy.AppPathName })
    Protocol = [string]$(if ($policy.IPProtocolMatchCondition) { $policy.IPProtocolMatchCondition } else { $policy.IPProtocol })
    Dscp = $(if ($null -ne $policy.DSCPAction) { [int]$policy.DSCPAction } else { [int]$policy.DSCPValue })
    NetworkProfile = [string]$policy.NetworkProfile
  }
}
$qos = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\QoS' -ErrorAction SilentlyContinue
$marker = Get-ItemProperty -Path 'HKLM:\SOFTWARE\VROSTBridge' -ErrorAction SilentlyContinue
$system = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction SilentlyContinue
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
ConvertTo-Json -Compress -Depth 3 -InputObject ([pscustomobject]@{
  policy = $found
  nla = $(if ($qos) { [string]$qos.'Do not use NLA' } else { $null })
  marker = $(if ($marker) { [string]$marker.VROSTBridgeSet } else { $null })
  marker_utc = $(if ($marker) { [string]$marker.VROSTBridgeSetUtc } else { $null })
  boot_utc = $(if ($system) { $system.LastBootUpTime.ToUniversalTime().ToString('o') } else { $null })
})
"""

_ENABLE_SCRIPT = r"""
# VROSTBridge: mark SteamVR's video packets (UDP from vrserver.exe) with DSCP 46.
$ErrorActionPreference = 'Stop'
try {
  $name = 'VROSTBridgeVideo'
  $qosKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\QoS'
  $markerKey = 'HKLM:\SOFTWARE\VROSTBridge'
  $nlaName = 'Do not use NLA'
  # Recreate rather than edit, so the policy always ends up exactly as below.
  Get-NetQosPolicy -Name $name -ErrorAction SilentlyContinue | Remove-NetQosPolicy -Confirm:$false
  New-NetQosPolicy -Name $name -AppPathNameMatchCondition 'vrserver.exe' -IPProtocolMatchCondition UDP -DSCPAction 46 -NetworkProfile All | Out-Null
  # Windows applies policies outside a domain only with this value; leave it alone if it is already set.
  if (-not (Test-Path -Path $qosKey)) { New-Item -Path $qosKey -Force | Out-Null }
  $current = (Get-ItemProperty -Path $qosKey -Name $nlaName -ErrorAction SilentlyContinue).$nlaName
  if ([string]$current -ne '1') {
    # Record ownership before the change, so a failure part-way never leaves an unowned value behind.
    if (-not (Test-Path -Path $markerKey)) { New-Item -Path $markerKey -Force | Out-Null }
    New-ItemProperty -Path $markerKey -Name 'VROSTBridgeSet' -PropertyType String -Value '1' -Force | Out-Null
    New-ItemProperty -Path $markerKey -Name 'VROSTBridgeSetUtc' -PropertyType String -Value ((Get-Date).ToUniversalTime().ToString('o')) -Force | Out-Null
    New-ItemProperty -Path $qosKey -Name $nlaName -PropertyType String -Value '1' -Force | Out-Null
  }
  exit 0
} catch {
  exit 1
}
"""

_DISABLE_SCRIPT = r"""
# VROSTBridge: remove the video priority policy.
$ErrorActionPreference = 'Stop'
try {
  $name = 'VROSTBridgeVideo'
  $qosKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\QoS'
  $markerKey = 'HKLM:\SOFTWARE\VROSTBridge'
  $nlaName = 'Do not use NLA'
  Get-NetQosPolicy -Name $name -ErrorAction SilentlyContinue | Remove-NetQosPolicy -Confirm:$false
  # Remove the NLA value only if VROSTBridge set it and no other QoS policy may rely on it.
  $marker = (Get-ItemProperty -Path $markerKey -Name 'VROSTBridgeSet' -ErrorAction SilentlyContinue).VROSTBridgeSet
  if ([string]$marker -eq '1') {
    $others = @(Get-NetQosPolicy -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $name })
    if ($others.Count -eq 0) {
      Remove-ItemProperty -Path $qosKey -Name $nlaName -ErrorAction SilentlyContinue
      Remove-ItemProperty -Path $markerKey -Name 'VROSTBridgeSet' -ErrorAction SilentlyContinue
      Remove-ItemProperty -Path $markerKey -Name 'VROSTBridgeSetUtc' -ErrorAction SilentlyContinue
    }
  }
  exit 0
} catch {
  exit 1
}
"""

MESSAGES = {
    'on': "Video priority is on: Windows marks SteamVR's video packets (DSCP 46), so Wi-Fi equipment that honours "
          'priority can send them first.',
    'restart': 'Video priority is set up. Restart the host PC so Windows starts marking the video packets.',
    'needs_nla': 'The video priority policy exists, but Windows ignores it on a PC outside a domain until "Do not use '
                 'NLA" is set. Turn video priority on again to finish.',
    'mismatch': "The video priority policy has different settings from VROSTBridge's. Turn video priority on again "
                'to replace it.',
    'off': 'Video priority is off. Turning it on asks for administrator permission and needs a restart of the host PC.',
}


def enable_script():
    """PowerShell to run elevated: create the policy and, if needed, the NLA override."""
    return _ENABLE_SCRIPT


def disable_script():
    """PowerShell to run elevated: remove the policy and any NLA override VROSTBridge added."""
    return _DISABLE_SCRIPT


def _utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    # PowerShell writes seven fractional digits; Python 3.12 accepts at most six reliably.
    text = re.sub(r'(\.\d{6})\d+', r'\1', text)
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=datetime.timezone.utc) if moment.tzinfo is None else moment


def _matches_app(path):
    return isinstance(path, str) and re.split(r'[\\/]', path.strip())[-1].lower() == APP_NAME


def _udp(value):
    return str(value).strip().lower() in ('udp', '2')


def parse_status(text):
    """Turn STATUS_SCRIPT output into a status dict (raises ValueError if unreadable)."""
    if not isinstance(text, str):
        raise ValueError('No output from PowerShell.')
    value = json.loads(text.lstrip('﻿').strip() or 'null')
    if not isinstance(value, dict):
        raise ValueError('Unexpected video priority status output.')
    policy = value.get('policy') if isinstance(value.get('policy'), dict) else None
    policy_ok = bool(policy) and _matches_app(policy.get('AppPath')) and _udp(policy.get('Protocol')) \
        and policy.get('Dscp') == DSCP
    nla_value = value.get('nla')
    nla = (nla_value.strip() == '1') if isinstance(nla_value, str) else nla_value == 1
    owned = value.get('marker') == '1'
    set_at, boot = _utc(value.get('marker_utc')), _utc(value.get('boot_utc'))
    restart_needed = bool(nla and owned and set_at and boot and set_at > boot)
    if policy is None:
        state, message = 'off', MESSAGES['off']
    elif not policy_ok:
        state, message = 'incomplete', MESSAGES['mismatch']
    elif not nla:
        state, message = 'incomplete', MESSAGES['needs_nla']
    elif restart_needed:
        state, message = 'restart', MESSAGES['restart']
    else:
        state, message = 'on', MESSAGES['on']
    return {'state': state, 'policy': policy is not None, 'policy_ok': policy_ok, 'nla_override': nla,
            'set_by_vrostbridge': owned, 'restart_needed': restart_needed, 'error': False, 'message': message}


def status(runner=None):
    """Read-only video priority status; never raises."""
    try:
        result = run_powershell(STATUS_SCRIPT, runner, 30)
        if result.returncode != 0:
            raise ValueError((result.stderr or '').strip()[:200] or 'PowerShell reported an error.')
        return parse_status(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'state': 'unknown', 'policy': None, 'policy_ok': None, 'nla_override': None,
                'set_by_vrostbridge': None, 'restart_needed': None, 'error': True,
                'message': 'The video priority setting could not be read: ' + str(error)[:200]}


_SINGLE_QUOTES = ("'", '‘', '’', '‚', '‛')  # PowerShell treats all of these as quotes.


def _quote(text):
    for quote in _SINGLE_QUOTES:
        text = text.replace(quote, quote + quote)
    return "'" + text + "'"


def elevation_command(script_path):
    """The -Command text that runs script_path elevated and returns its exit code.

    Exit codes: the script's own, 1223 when the UAC prompt is declined, 3 when
    Windows does not report the elevated process's result. It calls
    Process.Start with the runas verb, which is what Start-Process -Verb RunAs
    does internally, because Start-Process replaces the Windows error with a
    translated message and so loses the "cancelled by the user" code (1223).
    The double quotes around the path are built with [char]34, so the command
    line itself contains none.
    """
    path = str(script_path)
    if not path or any(character in path for character in '"\r\n\0'):
        raise ValueError('The temporary script path contains unsupported characters.')
    return ('try { $info = New-Object System.Diagnostics.ProcessStartInfo; '
            '$info.FileName = ' + _quote(powershell_path()) + '; '
            "$info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + [string][char]34 + "
            + _quote(path) + ' + [string][char]34; '
            "$info.Verb = 'runas'; $info.UseShellExecute = $true; "
            '$info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden; '
            '$process = [System.Diagnostics.Process]::Start($info); '
            'if ($null -eq $process) { exit 3 }; $process.WaitForExit(); exit $process.ExitCode } '
            'catch { $failure = $_.Exception; while ($failure) { '
            'if ($failure -is [System.ComponentModel.Win32Exception] -and $failure.NativeErrorCode -eq 1223) { exit 1223 }; '
            '$failure = $failure.InnerException }; [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')


def run_elevated(script, runner=None, temp_root=None, label='task', timeout=600):
    """Run a PowerShell script with administrator rights through one UAC prompt.

    The script is written to %TEMP%\\VROSTBridge\\VROSTBridge-<label>-<random>.ps1
    (created exclusively, never over an existing file) and deleted afterwards,
    whatever happens. Returns {'ok', 'declined', 'returncode', 'message'};
    never raises. Confirm the outcome with a status query afterwards.
    """
    if not isinstance(script, str) or not script.strip() or len(script) > 65536 or '\0' in script:
        return {'ok': False, 'declined': False, 'returncode': None, 'message': 'The administrator script is invalid.'}
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,40}', label):
        label = 'task'
    root = Path(temp_root) if temp_root is not None else Path(os.environ.get('TEMP') or tempfile.gettempdir())
    path = None
    try:
        directory = root / 'VROSTBridge'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ('VROSTBridge-' + label + '-' + secrets.token_hex(4) + '.ps1')
        # BOM: Windows PowerShell 5.1 reads a .ps1 file without one in the ANSI code page.
        with open(path, 'x', encoding='utf-8-sig', newline='\r\n') as handle:
            handle.write(script)
        result = (runner or windows_run)([powershell_path(), '-NoProfile', '-NonInteractive', '-Command',
                                          elevation_command(path)], timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'declined': False, 'returncode': None,
                'message': 'Timed out waiting for administrator permission. If the prompt is still open, decline it '
                           'and try again; nothing was changed.'}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'ok': False, 'declined': False, 'returncode': None,
                'message': 'The administrator step could not be started: ' + str(error)[:200]}
    finally:
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
    code = result.returncode
    if code == 0:
        return {'ok': True, 'declined': False, 'returncode': 0, 'message': 'The administrator step finished.'}
    if code == ERROR_CANCELLED:
        return {'ok': False, 'declined': True, 'returncode': code,
                'message': 'Administrator permission was not given, so nothing was changed.'}
    if code == UNKNOWN_RESULT:
        return {'ok': False, 'declined': False, 'returncode': code,
                'message': 'Windows did not report whether the administrator step worked; check the setting again.'}
    detail = (result.stderr or '').strip().splitlines()
    return {'ok': False, 'declined': False, 'returncode': code,
            'message': 'The administrator step failed' + (': ' + detail[-1][:200].rstrip('.') if detail else '') + '.'}


def apply(enabled, runner=None, temp_root=None):
    """Turn video priority on or off (one UAC prompt), then report the resulting status."""
    script, label = (enable_script(), 'video-priority-on') if enabled else (disable_script(), 'video-priority-off')
    result = run_elevated(script, runner=runner, temp_root=temp_root, label=label)
    current = status(runner)
    result['status'] = current
    if result['ok']:
        result['message'] = current['message']
    return result
