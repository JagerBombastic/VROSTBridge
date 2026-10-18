"""Optional, source-scoped firewalld allowances for the Linux client.

All status queries are read-only. Only allow(), explicitly invoked by the
desktop button, requests OS authentication and adds runtime/permanent rules.
No reload, zone reassignment, service enablement or global port opening.

Windows: launcher/Client-Firewall.ps1 (modelled on host/Host-Setup.ps1
-Action Firewall). Its Check action reads the rules without elevation; it is
slow, so the result is cached and refreshed every minute. allow() runs its
Firewall action, which asks for administrator permission once (UAC) and
creates inbound Allow rules named VROSTBridge.Client.* (never the host's
VROSTBridge.* names), any network type (1.0.0.14; Private only before), RemoteAddress LocalSubnet, each limited
to its program and ports and carrying an ownership marker in its description.
remove_windows_rules() is the uninstaller's RemoveFirewall action.
"""

from copy import deepcopy
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

from launcher.client_identity import lan_addresses
from launcher import windows_network


PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PORTS = ((9943, 'tcp'), (9944, 'udp'), (5353, 'udp'), (9945, 'tcp'), (9946, 'udp'))
# Windows (launcher/Client-Firewall.ps1): the same ports, per program: the native client's ALVR control
# (TCP 9943), stream (UDP 9944) and mDNS, and the companion's diagnostics (TCP 9945), session-discovery
# replies (UDP 9946) and network-test receiver (UDP 9944, launcher/network_test.py STREAM_PORT).
FIREWALL_SCRIPT = Path(__file__).resolve().with_name('Client-Firewall.ps1')
WINDOWS_RULES = ('VROSTBridge.Client.VR.TCP', 'VROSTBridge.Client.VR.UDP', 'VROSTBridge.Client.VR.mDNS',
                 'VROSTBridge.Client.Companion.TCP', 'VROSTBridge.Client.Companion.UDP')
WINDOWS_RULE_STATES = ('ready', 'missing', 'different', 'foreign')
WINDOWS_CHECK_SECONDS = 60
ERROR_CANCELLED = 1223  # the user declined the UAC prompt


def _valid_interface(interface):
    if sys.platform == 'win32':
        return windows_network.valid_alias(interface)
    return bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,63}', interface))


def private_targets(addresses):
    """Require an observed, physical interface and its real private netmask."""
    targets = []
    for item in addresses:
        interface, prefix = item.get('interface'), item.get('prefix')
        if item.get('physical') is not True or item.get('connected') is not True or not isinstance(interface, str):
            continue
        if not _valid_interface(interface) or not isinstance(prefix, int) or isinstance(prefix, bool):
            continue
        try:
            network = ipaddress.IPv4Network(f"{item['address']}/{prefix}", strict=False)
        except (ValueError, KeyError):
            continue
        if not any(network.subnet_of(private) for private in PRIVATE_NETWORKS):
            continue
        target = {'interface': interface, 'subnet': str(network)}
        if target not in targets:
            targets.append(target)
    return targets


def rich_rules(subnet):
    network = ipaddress.IPv4Network(subnet)
    if not any(network.subnet_of(private) for private in PRIVATE_NETWORKS):
        raise ValueError('Only a connected private IPv4 subnet can be allowed.')
    return [f'rule family="ipv4" source address="{network}" port port="{port}" protocol="{protocol}" accept'
            for port, protocol in PORTS]


def system_run(arguments, timeout):
    if sys.platform == 'win32':
        # A console program started from the windowless companion would open its own console window.
        return subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, check=False,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
    environment = os.environ.copy()
    # PyInstaller modifies LD_LIBRARY_PATH for its child processes. Use system
    # libraries for system utilities and never pass the frozen bundle to pkexec.
    if 'LD_LIBRARY_PATH_ORIG' in environment:
        environment['LD_LIBRARY_PATH'] = environment['LD_LIBRARY_PATH_ORIG']
    else:
        environment.pop('LD_LIBRARY_PATH', None)
    environment['LC_ALL'] = 'C'
    return subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, env=environment, check=False)


def _default_client_executable():
    """Windows: the native client next to the companion, as launcher/app.py finds it (with .exe)."""
    root = Path(__file__).resolve().parents[1]
    for candidate in (root / 'VROSTBridgeClient.exe', root / 'dist' / 'client' / 'VROSTBridgeClient.exe'):
        if candidate.is_file():
            return candidate
    return root / 'VROSTBridgeClient.exe'


def _windows_command(script, action, client_executable=None, python_executable=None):
    from host.power import powershell_path  # host.power imports this module through launcher.wake_host
    command = [powershell_path(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
               '-Action', action]
    if action != 'RemoveFirewall':
        command += ['-ClientExecutable', str(client_executable), '-PythonExecutable', str(python_executable)]
    return command


def _last_line(result):
    lines = ((getattr(result, 'stderr', '') or '') or (getattr(result, 'stdout', '') or '')).strip().splitlines()
    return lines[-1].strip()[:200] if lines else ''


def remove_windows_rules(script=None, runner=None, timeout=600):
    """Uninstaller: remove the VROSTBridge.Client.* rules that carry VROSTBridge's ownership marker (one UAC
    prompt, only if any exist; a rule without the marker is left alone and reported).
    Returns {'ok', 'declined', 'returncode', 'message'}; never raises."""
    try:
        result = (runner or system_run)(_windows_command(script or FIREWALL_SCRIPT, 'RemoveFirewall'), timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'declined': False, 'returncode': None,
                'message': 'Timed out waiting for administrator permission; the client firewall rules were kept.'}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'ok': False, 'declined': False, 'returncode': None,
                'message': 'The client firewall cleanup could not be started: ' + str(error)[:200]}
    if result.returncode == 0:
        return {'ok': True, 'declined': False, 'returncode': 0,
                'message': (result.stdout or '').strip()[:300] or 'VROSTBridge client firewall rules removed.'}
    if result.returncode == ERROR_CANCELLED:
        return {'ok': False, 'declined': True, 'returncode': result.returncode,
                'message': 'Administrator permission was not given, so the client firewall rules were kept.'}
    detail = _last_line(result)
    return {'ok': False, 'declined': False, 'returncode': result.returncode,
            'message': 'The client firewall cleanup failed' + (': ' + detail.rstrip('.') if detail else '') + '.'}


class ClientNetwork:
    def __init__(self, address_provider=None, runner=None, firewall_command=None, pkexec=None, auto_refresh=True, state_path=None,
                 *, client_executable=None, python_executable=None, script=None):
        self.address_provider = address_provider or lan_addresses
        self.runner = runner or system_run
        self.command = firewall_command or '/usr/bin/firewall-cmd'
        self.pkexec = pkexec or '/usr/bin/pkexec'
        self.state_path = Path(state_path) if state_path else Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'VROSTBridge/client-firewall.json'
        # Windows: the programs the rules admit (the native client, and the companion's own Python, which
        # runs the diagnostics, session and network-test sockets) and the script that checks and adds them.
        self.client_executable = Path(client_executable) if client_executable else None
        self.python_executable = Path(python_executable) if python_executable else Path(sys.executable)
        self.script = Path(script) if script else FIREWALL_SCRIPT
        self._lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._snapshot = {'state': 'checking', 'message': 'Checking the home-network firewall.',
                          'targets': [], 'missing_rules': 0, 'can_allow': False}
        if auto_refresh:
            threading.Thread(target=self._poll, daemon=True, name='VROSTBridgeNetworkCheck').start()

    def _poll(self):
        while not self._closed.is_set():
            self.refresh()
            self._wake.wait(WINDOWS_CHECK_SECONDS if sys.platform == 'win32' else 15)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()

    def status(self):
        """Pure cached snapshot; safe under the application status lock."""
        with self._lock:
            return deepcopy(self._snapshot)

    def _set(self, state, message, targets=(), missing_rules=0, can_allow=False):
        runtime_ready = bool(targets) and not any(target.get('missing_runtime') for target in targets)
        permanent_ready = bool(targets) and not any(target.get('missing_permanent') for target in targets)
        result = dict(state=state, message=message, targets=list(targets),
                      missing_rules=missing_rules, can_allow=can_allow,
                      runtime_ready=runtime_ready, permanent_ready=permanent_ready)
        with self._lock:
            self._snapshot = result
        return deepcopy(result)

    def _run(self, *arguments):
        return self.runner([self.command, *arguments], 4)

    def refresh(self):
        with self._query_lock:
            try:
                return self._inspect_windows() if sys.platform == 'win32' else self._inspect()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                return self._set('error', 'Could not inspect the firewall: ' + str(error)[:300])

    # ------------------------------------------------------------------ Windows
    def _windows_arguments(self, action):
        if self.client_executable is None:
            self.client_executable = _default_client_executable()
        return _windows_command(self.script, action, self.client_executable, self.python_executable)

    def _set_windows(self, state, message, targets=(), rules=None, missing=(), can_allow=False, blocking=()):
        complete = rules is not None and not missing
        result = dict(state=state, message=message, targets=list(targets), missing_rules=len(missing),
                      can_allow=can_allow, runtime_ready=complete, permanent_ready=complete,
                      rules=dict(rules or {}), blocking=list(blocking))
        with self._lock:
            self._snapshot = result
        return deepcopy(result)

    def _windows_targets(self, profiles):
        """Connected physical adapters with a private subnet, with Windows' network category for each."""
        categories = {}
        for item in profiles if isinstance(profiles, list) else [profiles] if isinstance(profiles, dict) else []:
            if isinstance(item, dict) and isinstance(item.get('alias'), str):
                categories[item['alias']] = str(item.get('category') or '')[:32]
        try:
            targets = private_targets(self.address_provider())
        except (OSError, ValueError, TypeError):
            targets = []
        for target in targets:
            target['category'] = categories.get(target['interface'], '')
        return targets

    def _inspect_windows(self):
        if not self.script.is_file():
            return self._set_windows('unavailable', 'The firewall check is missing from this installation. Repair or '
                                     'reinstall VROSTBridge.')
        arguments = self._windows_arguments('Check')
        if not self.client_executable.is_file():
            return self._set_windows('unavailable', 'The VROSTBridge client program (' + self.client_executable.name +
                                     ') was not found, so its firewall rules were not checked.')
        result = self.runner(arguments, 60)
        if result.returncode != 0:
            raise ValueError(_last_line(result) or 'the firewall check did not finish')
        value = json.loads((result.stdout or '').lstrip('\ufeff').strip() or 'null')
        if not isinstance(value, dict) or not isinstance(value.get('rules'), list):
            raise ValueError('the firewall check returned an unexpected result')
        rules = {row['name']: row['state'] for row in value['rules'] if isinstance(row, dict)
                 and row.get('name') in WINDOWS_RULES and row.get('state') in WINDOWS_RULE_STATES}
        if set(rules) != set(WINDOWS_RULES):
            raise ValueError('the firewall check returned an unexpected result')
        missing = [name for name in WINDOWS_RULES if rules[name] != 'ready']
        foreign = [name for name in WINDOWS_RULES if rules[name] == 'foreign']
        firewall = value.get('firewall') if isinstance(value.get('firewall'), list) else []
        private_off = any(isinstance(item, dict) and item.get('name') == 'Private' and str(item.get('enabled')) == 'False'
                          for item in firewall)
        blocking = [name[:80] for name in (value.get('blocking') if isinstance(value.get('blocking'), list) else [])
                    if isinstance(name, str) and name][:6]
        targets = self._windows_targets(value.get('profiles'))
        common = dict(targets=targets, rules=rules, missing=missing, blocking=blocking)
        if foreign:
            return self._set_windows('error', 'A Windows Firewall rule named ' + foreign[0] + ' already exists and was not '
                                     'created by VROSTBridge, so it was left unchanged. Rename or remove it in Windows '
                                     'Defender Firewall, then check again.', **common)
        if private_off:
            return self._set_windows('inactive', 'Windows Firewall is off for private networks. Other firewall software '
                                     'has not been checked.', **common)
        note = ''
        if blocking:
            note += (' Windows Firewall also has rules that block this program (' + ', '.join(blocking[:3]) + '), '
                     'usually left by a declined firewall prompt; block rules win over allow rules, so remove them in '
                     'Windows Defender Firewall > Inbound Rules.')
        if missing:
            return self._set_windows('needs-permission', 'Allow your home network to reach this PC\'s VROSTBridge client. '
                                     'Windows asks for administrator permission once; the rules admit only the local '
                                     'subnet.' + note, can_allow=True, **common)
        if blocking:
            return self._set_windows('error', 'The home-network firewall rules are in place, but connections can still '
                                     'be blocked.' + note, **common)
        return self._set_windows('ready', 'Home-network firewall rules are in place (local subnet '
                                 'only). Other firewall software has not been checked.' + note, **common)

    def _allow_windows(self):
        before = self.refresh()
        if before['state'] == 'ready':
            return before
        if before['state'] != 'needs-permission' or not before['can_allow']:
            raise ValueError(before['message'])
        failure = None
        try:
            # The script asks for administrator permission itself (one UAC prompt, the runas verb), as Host-Setup.ps1
            # does; exit code 1223 means the prompt was declined.
            result = self.runner(self._windows_arguments('Firewall'), 600)
        except subprocess.TimeoutExpired:
            failure = ('Timed out waiting for administrator permission. If the prompt is still open, decline it and '
                       'try again.')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failure = 'The firewall setup could not be started: ' + str(error)[:200]
        else:
            if result.returncode == ERROR_CANCELLED:
                failure = 'Administrator permission was not given, so no firewall rules were changed. You can try the button again.'
            elif result.returncode != 0:
                detail = _last_line(result)
                failure = 'Windows Firewall setup failed' + (': ' + detail.rstrip('.') if detail else '') + '.'
        after = self.refresh()
        if failure:
            raise ValueError(failure)
        if after['state'] != 'ready':
            raise ValueError('Some home-network allowances could not be verified. ' + after['message'])
        return after

    def _inspect(self):
        if not Path(self.command).is_file():
            return self._set('unavailable', 'firewalld is not installed. Other firewall software has not been checked.')
        running = self._run('--state')
        if running.returncode == 252 or running.stdout.strip() == 'not running' or running.stderr.strip() == 'not running':
            return self._set('inactive', 'firewalld is not running. Other firewall software has not been checked.')
        if running.returncode != 0 or running.stdout.strip() != 'running':
            raise ValueError((running.stderr or running.stdout or 'firewalld state unavailable').strip())
        targets = private_targets(self.address_provider())
        if not targets:
            return self._set('unavailable', 'No physical interface with a private IPv4 subnet was found. No firewall rules were changed.')
        if len(targets) > 8:
            return self._set('unavailable', 'Several networks are active. Configure the client firewall for your home network manually.')
        default_zone = None
        rule_cache = {}
        missing = 0
        for target in targets:
            response = self._run('--get-zone-of-interface=' + target['interface'])
            zone = response.stdout.strip()
            if zone == 'no zone' or (response.returncode == 2 and not zone):
                if default_zone is None:
                    response = self._run('--get-default-zone')
                    default_zone = response.stdout.strip() if response.returncode == 0 else ''
                zone = default_zone
            elif response.returncode != 0:
                raise ValueError((response.stderr or 'Cannot determine the interface firewall zone').strip())
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', zone or ''):
                raise ValueError('Cannot identify the current interface firewall zone.')
            target['zone'] = zone
            for scope in ('runtime', 'permanent'):
                key = (zone, scope)
                options = ['--zone=' + zone] + (['--permanent'] if scope == 'permanent' else [])
                if key not in rule_cache:
                    response = self._run(*options, '--list-rich-rules')
                    if response.returncode != 0:
                        raise ValueError((response.stderr or 'Cannot read firewall rules').strip())
                    services = self._run(*options, '--list-services')
                    if services.returncode != 0:
                        raise ValueError((services.stderr or 'Cannot read firewall services').strip())
                    rule_cache[key] = (set(response.stdout.splitlines()), set(services.stdout.split()))
                current_rules, services = rule_cache[key]
                target['missing_' + scope] = [rule for rule in rich_rules(target['subnet'])
                                             if rule not in current_rules
                                             and not ('port="5353"' in rule and 'mdns' in services)]
                missing += len(target['missing_' + scope])
        available = Path(self.pkexec).is_file()
        if missing:
            return self._set('needs-permission',
                             'Allow this home network to reach the client and remember it after restart. Existing firewall rules may already allow it.'
                             if available else 'Home-network firewall exceptions are absent, and the OS authentication helper is unavailable.',
                             targets, missing, available)
        if self._ownership().get('pending'):
            return self._set('ready', 'Finish checking the previous network permission request.', targets, can_allow=True)
        return self._set('ready', 'Home-network allowances are active and saved for restart. Other firewall software has not been checked.', targets)

    def _ownership(self):
        try:
            value = json.loads(self.state_path.read_text(encoding='utf-8'))
            if value.get('schema') != 1 or not isinstance(value.get('rules'), list):
                raise ValueError('Unrecognised saved firewall ownership record.')
            return value
        except FileNotFoundError:
            return {'schema': 1, 'rules': []}

    def _save_ownership(self, value):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.state_path)

    def _reconcile_pending(self, owned):
        """Finish bookkeeping after a previous authorised command was uncertain."""
        pending = owned.get('pending')
        if pending is None:
            return
        if not isinstance(pending, list) or len(pending) > 128:
            raise ValueError('Saved pending firewall rules need review before continuing.')
        grouped = {}
        for entry in pending:
            if not isinstance(entry, dict):
                raise ValueError('Invalid pending firewall ownership record.')
            zone, scope, rule = entry.get('zone'), entry.get('scope'), entry.get('rule')
            match = re.fullmatch(r'rule family="ipv4" source address="([0-9./]+)" port port="[0-9]+" protocol="(?:tcp|udp)" accept', rule if isinstance(rule, str) else '')
            if (not isinstance(zone, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', zone)
                    or scope not in ('runtime', 'permanent') or not match
                    or rule not in rich_rules(match.group(1))):
                raise ValueError('Pending firewall rule is outside the VROSTBridge rule policy.')
            grouped.setdefault((zone, scope), []).append(entry)
        present = []
        for (zone, scope), entries in grouped.items():
            response = self._run('--zone=' + zone, *(['--permanent'] if scope == 'permanent' else []), '--list-rich-rules')
            if response.returncode != 0:
                raise ValueError('Previous firewall changes still could not be verified. Try Allow home-network connection again when firewalld is available.')
            current = set(response.stdout.splitlines())
            present.extend(entry for entry in entries if entry['rule'] in current)
        # Each pending entry was absent immediately before its authorised
        # attempt. Resolve both applied and unapplied entries without changes
        # to the firewall itself; preserve the record if any query failed.
        for entry in present:
            if entry not in owned['rules']:
                owned['rules'].append(entry)
        owned.pop('pending', None)
        self._save_ownership(owned)

    def allow(self):
        """Explicit desktop action. Authenticate only the system firewall utility."""
        if sys.platform == 'win32':
            return self._allow_windows()
        before = self.refresh()
        owned = self._ownership()
        self._reconcile_pending(owned)
        if before['state'] == 'ready':
            return before
        if before['state'] != 'needs-permission' or not before['can_allow']:
            raise ValueError(before['message'])
        self._save_ownership(owned)  # Check bookkeeping is writable before authentication.
        grouped = {}
        for target in before['targets']:
            for scope in ('permanent', 'runtime'):
                grouped.setdefault((target['zone'], scope), set()).update(target['missing_' + scope])
        failure = None
        try:
            for (zone, scope), rules in grouped.items():
                if not rules:
                    continue
                arguments = [self.pkexec, self.command, '--zone=' + zone] + (['--permanent'] if scope == 'permanent' else [])
                arguments.extend('--add-rich-rule=' + rule for rule in sorted(rules))
                # No shell, no root execution of an app-owned helper, no blanket
                # ports, and no firewall/zone resets.
                owned['pending'] = [{'zone': zone, 'scope': scope, 'rule': rule} for rule in sorted(rules)]
                self._save_ownership(owned)
                response = self.runner(arguments, 60)
                # Record only rules absent before this request and present now.
                # The sequence command can partly succeed despite exit status.
                check = self._run('--zone=' + zone, *(['--permanent'] if scope == 'permanent' else []), '--list-rich-rules')
                if check.returncode != 0:
                    owned['pending'] = [{'zone': zone, 'scope': scope, 'rule': rule} for rule in sorted(rules)]
                    self._save_ownership(owned)
                    raise ValueError('Firewall changes could not be inspected; pending rule ownership was saved for review.')
                for rule in rules.intersection(check.stdout.splitlines()):
                    entry = {'zone': zone, 'scope': scope, 'rule': rule}
                    if entry not in owned['rules']:
                        owned['rules'].append(entry)
                owned.pop('pending', None)
                self._save_ownership(owned)
                if response.returncode != 0:
                    if response.returncode in (126, 127):
                        raise ValueError('Network permission was not authorised. You can try the button again.')
                    raise ValueError((response.stderr or response.stdout or 'The firewall rejected the request').strip()[:500])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failure = str(error)
        finally:
            # firewall-cmd sequence options can partially succeed with exit 0.
            # Always inspect the resulting rules; never report success by exit
            # status alone, and keep any partially applied change visible.
            after = self.refresh()
        if failure:
            partial = ' Runtime access is active; saved settings still need attention.' if after.get('runtime_ready') else ' Some saved rules may have been added; the firewall has not been reset.'
            self._set(after['state'], failure + partial, after['targets'], after['missing_rules'], after['can_allow'])
            raise ValueError(failure + partial)
        if after['state'] != 'ready':
            raise ValueError('Some home-network allowances could not be verified. ' + after['message'])
        return after
