"""Installer lifecycle operations; only marked per-user VROSTBridge releases are owned.

Invoked by the staged frozen client or the Windows installers' temporary Python (the host's, and from 1.0.0.7
the Windows client's *-windows-client actions, packaging/windows-client-installer.nsi).
Mutable state is backed up outside release directories before removing old binaries.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

PIN = 'f5023f8e78fa14ea57fb1038548d7820289a8524'
VERSION = '1.0.0.11'  # mirrors launcher/version.HOST_VERSION (standalone script)
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeHost'
# The Windows client (packaging/windows-client-installer.nsi) sits beside the host on one PC: its own folder,
# uninstall key, sign-in value and state files (client-*), never the host's.
CLIENT_UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeClient'
# A release identity may contain a SemVer prerelease suffix. Retain the fourth
# numeric component used by historical hotfixes, but never treat '-' as a
# generic delimiter: it is part of versions such as 1.0.0-rc1.
_NUMBER = r'(?:0|[1-9][0-9]*)'
_PRERELEASE_ID = r'(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
_RELEASE_VERSION = re.compile(
    _NUMBER + r'(?:\.' + _NUMBER + r'){2,3}'
    + r'(?:-' + _PRERELEASE_ID + r'(?:\.' + _PRERELEASE_ID + r')*)?'
    + r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?')


def state_directory():
    return Path(os.environ.get('LOCALAPPDATA', os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))))/'VROSTBridge'


def releases_directory(role):
    if role == 'host':
        return Path(os.environ['LOCALAPPDATA'])/'VROSTBridge'/'Host'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'vrostbridge'/'versions'


def owned_release(path, role):
    """Never follow an installation-directory symlink or accept an arbitrary ancestor."""
    if role not in ('host', 'client'):
        return False
    path = Path(path).absolute()
    base = releases_directory(role).absolute()
    if path.is_symlink() or path.parent.resolve() != base.resolve() or not path.is_dir():
        return False
    try:
        marker = path/'PACKAGED.json'
        if marker.is_symlink():
            return False
        identity = json.loads(marker.read_text(encoding='utf-8'))
        version = identity.get('version')
        if (not isinstance(version, str) or len(version) > 128 or
                not _RELEASE_VERSION.fullmatch(version)):
            return False
        # Windows owns the exact version directory. Linux also owns the
        # content-addressed layout used by its installer. Compare against the
        # complete validated metadata version, including prerelease/build tags.
        expected_name = path.name == version
        if role == 'client' and not expected_name:
            expected_name = bool(re.fullmatch(re.escape(version) + r'-[0-9a-f]{12}', path.name))
        return (identity.get('role') == role and identity.get('alvr_commit') == PIN
                and expected_name)
    except (OSError, ValueError, AttributeError):
        return False


def releases(role):
    base = releases_directory(role)
    return [p for p in base.iterdir() if owned_release(p, role)] if base.is_dir() else []


def acquire_lock(role):
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True)
    handle = (state/(role+'-desktop.lock')).open('a+b')
    if handle.tell() == 0:
        handle.write(b'\0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return None
        raise
    return handle


def stop_desktop(role):
    """Ask only the authenticated matching local companion to quit, never kill by name."""
    lock = acquire_lock(role)
    if lock:
        lock.close(); return
    try:
        record = json.loads((state_directory()/(role+'-desktop.json')).read_text(encoding='utf-8'))
        port, token = record['port'], record['token']
        if (record.get('role') != role or type(record.get('pid')) is not int or
                type(port) is not int or not 1 <= port <= 65535 or
                not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', token)):
            raise ValueError('Invalid local desktop identity')
        base = 'http://127.0.0.1:'+str(port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {'X-VROSTBridge-Token': token}
        with opener.open(urllib.request.Request(base+'/status', headers=headers), timeout=3) as response:
            status = json.load(response)
        if status.get('pid') != record['pid'] or status.get('role') != role:
            raise ValueError('The running desktop identity does not match')
        headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base+'/action', data=b'{"action":"quit"}', headers=headers)
        with opener.open(request, timeout=75) as response:
            if not json.load(response).get('ok'):
                raise ValueError('The desktop did not accept shutdown')
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            lock = acquire_lock(role)
            if lock:
                lock.close(); return
            time.sleep(.2)
        raise TimeoutError('The desktop has not finished stopping')
    except Exception as error:
        raise RuntimeError('Close VROSTBridge from its web page and retry. The existing installation has been retained. '+str(error)) from error


def windows_reg_value(key, name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None


def registered_drivers():
    path = Path(os.environ['LOCALAPPDATA'])/'openvr'/'openvrpaths.vrpath'
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    return [Path(p).resolve() for p in document.get('external_drivers', []) or [] if p]


def preserve_host_state(candidates, target):
    state = state_directory()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = state/'upgrade-backups'/stamp
    saved_sessions = {}
    for release in candidates:
        runtime = release/'dist'/'host'
        for filename in ('session.json', 'session_log.txt', 'crash_log.txt', 'vrostbridge-registration.json'):
            source = runtime/filename
            if source.is_file() and not source.is_symlink():
                destination = backup/release.name/filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if filename == 'session.json':
                    document = json.loads(destination.read_text(encoding='utf-8-sig'))
                    if not isinstance(document, dict) or not isinstance(document.get('client_connections', {}), dict):
                        raise RuntimeError('Existing host session is invalid. It was backed up; repair it before upgrading.')
                    saved_sessions[release.resolve()] = destination
    drivers = registered_drivers()
    active = [p for p in candidates if (p/'dist'/'host').resolve() in drivers]
    if len(active) > 1:
        raise RuntimeError('Multiple VROSTBridge host versions are registered. Unregister the unused version before upgrading.')
    # A registered release is the authoritative active profile. Otherwise repair
    # the target profile or use the most recently used owned installation.
    preferred = active or [p for p in candidates if p.resolve() == target.resolve()]
    remaining = sorted(candidates, key=lambda p: (p/'dist/host/session.json').stat().st_mtime if (p/'dist/host/session.json').exists() else 0, reverse=True)
    session = next((str(saved_sessions[p.resolve()]) for p in preferred+remaining if p.resolve() in saved_sessions), None)
    retained = state/'host-retained-session.json'
    if session:
        shutil.copy2(session, retained)
    elif retained.is_file():
        # Uninstall keeps the last profile for a later reinstall as well.
        destination = backup/'retained'/'session.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = json.loads(retained.read_text(encoding='utf-8-sig'))
        if not isinstance(document, dict) or not isinstance(document.get('client_connections', {}), dict):
            raise RuntimeError('The retained host profile is invalid; it has not been changed.')
        shutil.copy2(retained, destination)
        session = str(destination)
    pending = {'target': str(target.resolve()), 'session': session,
               'registered_previous': [str(p.resolve()) for p in active]}
    path = state/'host-upgrade.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, indent=2), encoding='utf-8')


def run_host_setup(target, action):
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(target/'host'/'Host-Setup.ps1'), '-Action', action,
               '-HostDirectory', str(target/'dist'/'host')]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run(command, capture_output=True, text=True, **options)
    if result.returncode:
        advice = ('Firewall cleanup needs administrator approval. Retry uninstall and approve the request.'
                  if action == 'RemoveFirewall' else 'Close SteamVR and retry the installation.')
        raise RuntimeError(advice+' Existing host files were retained. '+result.stdout+' '+result.stderr)
    print(result.stdout)


def delete_legacy_registry(release):
    import winreg
    # 0.2.2 and earlier used a key per release. Remove only keys proven to point
    # at this exact owned directory, including the old key without InstallLocation.
    key = UNINSTALL_KEY+release.name.replace('.', '')
    location = windows_reg_value(key, 'InstallLocation')
    uninstall = windows_reg_value(key, 'UninstallString')
    if ((location and Path(location).resolve() == release.resolve()) or
            (uninstall and Path(uninstall.strip('"')).resolve() == (release/'Uninstall.exe').resolve())):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def prepare_host(target):
    if target.parent.resolve() != releases_directory('host').resolve() or target.name != VERSION:
        raise RuntimeError('The host installation target is not an owned release location')
    if target.exists() and not owned_release(target, 'host'):
        raise RuntimeError('The target contains files that are not a VROSTBridge installation')
    stop_desktop('host')
    preserve_host_state(releases('host'), target)



def commit_host(target, staged):
    """Copy from the verified temporary payload with rollback for same-version repair."""
    pending_path = state_directory()/'host-upgrade.json'
    pending = json.loads(pending_path.read_text(encoding='utf-8'))
    if Path(pending['target']).resolve() != target.resolve():
        raise RuntimeError('The prepared installation target changed')
    identity = json.loads((staged/'PACKAGED.json').read_text(encoding='utf-8'))
    if identity.get('version') != VERSION or identity.get('role') != 'host' or identity.get('alvr_commit') != PIN:
        raise RuntimeError('Invalid staged package identity')
    # Validate without relying on a pre-existing installation's libraries.
    seen = set()
    for line in (staged/'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        relative = Path(name)
        source = staged/relative
        if (not re.fullmatch('[0-9a-f]{64}', digest) or not name or name in seen or
                relative.is_absolute() or '..' in relative.parts or
                not source.resolve().is_relative_to(staged.resolve()) or
                hashlib.sha256(source.read_bytes()).hexdigest() != digest):
            raise RuntimeError('Staged package integrity check failed')
        seen.add(name)
    if not seen:
        raise RuntimeError('The package manifest is empty')
    backup = None
    if target.exists():
        if not owned_release(target, 'host'):
            raise RuntimeError('The target is not an owned installation')
        backup = target.with_name('.repair-'+VERSION+'-'+str(time.time_ns()))
        deadline = time.monotonic()+8
        while True:
            try:
                target.rename(backup); break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('The old installation is still in use. Close VROSTBridge and SteamVR, then retry.')
                time.sleep(.2)
    try:
        shutil.copytree(staged, target)
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if backup:
            backup.rename(target)
        raise
    pending['repair_backup'] = str(backup) if backup else None
    pending_path.write_text(json.dumps(pending, indent=2), encoding='utf-8')


def finish_host(target):
    if not owned_release(target, 'host'):
        raise RuntimeError('The new host identity is invalid; old installations were retained')
    path = state_directory()/'host-upgrade.json'
    pending = json.loads(path.read_text(encoding='utf-8'))
    if Path(pending['target']).resolve() != target.resolve():
        raise RuntimeError('The prepared installation target changed')
    if pending.get('session'):
        source = Path(pending['session']).resolve()
        if not source.is_relative_to((state_directory()/'upgrade-backups').resolve()):
            raise RuntimeError('Invalid profile backup path')
        shutil.copy2(source, target/'dist'/'host'/'session.json')
    old = [p for p in releases('host') if p.resolve() != target.resolve()]
    if any((p/'dist/host').resolve() in registered_drivers() for p in old):
        run_host_setup(target, 'Register')
    still_registered = registered_drivers()
    for release in old:
        if (release/'dist/host').resolve() in still_registered:
            raise RuntimeError('The old host driver is still registered; its files were retained')
    for release in old:
        shutil.rmtree(release)
        delete_legacy_registry(release)
    delete_legacy_registry(target)
    for repair in releases_directory('host').glob('.repair-*'):
        if repair.is_symlink() or not re.fullmatch(r'\.repair-'+re.escape(VERSION)+r'-[0-9]+', repair.name):
            continue
        try:
            identity = json.loads((repair/'PACKAGED.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if identity != {'version': VERSION, 'role': 'host', 'alvr_commit': PIN}:
            continue
        if (repair/'dist/host').resolve() in still_registered:
            raise RuntimeError('A repair backup is still registered; it was retained')
        shutil.rmtree(repair)
    path.unlink()


def finish_client(target):
    if not owned_release(target, 'client'):
        raise RuntimeError('The new client identity is invalid; old versions were retained')
    current = releases_directory('client').parent/'current'
    if not current.is_symlink() or current.resolve() != target.resolve():
        raise RuntimeError('The current client does not point to the verified new version')
    for release in releases('client'):
        if release.resolve() != target.resolve():
            shutil.rmtree(release)



def remove_client_firewall():
    """Remove only verified, recorded rules created by VROSTBridge itself."""
    from launcher.client_network import rich_rules, system_run
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'VROSTBridge/client-firewall.json'
    if not state.is_file():
        return
    document = json.loads(state.read_text(encoding='utf-8'))
    if document.get('schema') != 1 or not isinstance(document.get('rules'), list):
        raise RuntimeError('The saved firewall ownership record is invalid. Application files were retained.')
    if document.get('pending'):
        raise RuntimeError('An earlier firewall change has an unresolved result. Open VROSTBridge and resolve its network setup before uninstalling. Application files and the ownership record were retained.')
    groups = {}
    for entry in document['rules']:
        if not isinstance(entry, dict):
            raise RuntimeError('Invalid firewall ownership entry')
        zone, scope, rule = entry.get('zone'), entry.get('scope'), entry.get('rule')
        match = re.fullmatch(r'rule family="ipv4" source address="([^"\n]+)" port port="(?:9943|9944|5353|9945|9946)" protocol="(?:tcp|udp)" accept', rule or '')
        if (not isinstance(zone, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', zone) or
                scope not in ('runtime', 'permanent') or not match or rule not in rich_rules(match.group(1))):
            raise RuntimeError('The saved firewall rule is not a VROSTBridge home-network rule. It was not changed.')
        groups.setdefault((zone, scope), []).append(entry)
    remaining = document['rules'][:]
    for (zone, scope), entries in groups.items():
        options = ['--zone='+zone]+(['--permanent'] if scope == 'permanent' else [])
        response = system_run(['/usr/bin/firewall-cmd', *options, '--list-rich-rules'], 4)
        if response.returncode:
            raise RuntimeError('Could not inspect owned firewall rules. Start firewalld or remove these rules manually before retrying. Application files were retained.')
        present = set(response.stdout.splitlines())
        active = [entry for entry in entries if entry['rule'] in present]
        failure = None
        if active:
            try:
                command = ['/usr/bin/pkexec', '/usr/bin/firewall-cmd', *options]
                command.extend('--remove-rich-rule='+entry['rule'] for entry in active)
                response = system_run(command, 50)
                if response.returncode:
                    failure = (response.stderr or response.stdout or 'Authentication was declined').strip()
            except (OSError, subprocess.SubprocessError) as error:
                failure = str(error)
        # Commands can partly succeed even with an error exit code. Persist only
        # rules whose absence was checked, keeping ambiguous ownership for retry.
        response = system_run(['/usr/bin/firewall-cmd', *options, '--list-rich-rules'], 4)
        if response.returncode:
            raise RuntimeError('Could not verify firewall cleanup. Application files and ownership records were retained.')
        present = set(response.stdout.splitlines())
        for entry in entries:
            if entry['rule'] not in present and entry in remaining:
                remaining.remove(entry)
        document['rules'] = remaining
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(document, indent=2), encoding='utf-8')
        temporary.replace(state)
        if any(entry in remaining for entry in entries):
            raise RuntimeError('Firewall cleanup needs administrator approval. Application files were retained; rerun uninstall and approve the request. '+(failure or 'Some rules remain.'))
    state.unlink()


def remove_eye_tracking():
    """RC4.5: the Baballonia VROSTBridge downloaded (841 MB unpacked) goes with VROSTBridge; its settings and
    calibration in ~/.config/ProjectBabble are the user's and stay."""
    try:
        from launcher.baballonia import BaballoniaManager, data_root
        manager = BaballoniaManager(log=print)
        if manager.root.exists():
            manager.remove()
            print('The Baballonia VROSTBridge downloaded was removed; its settings and calibration stay in ~/.config/ProjectBabble.')
        shutil.rmtree(data_root().parent/'cameras', ignore_errors=True)
    except Exception as error:
        print('The downloaded Baballonia was left in place ('+str(error)[:200]+'): ~/.local/share/vrostbridge/baballonia.')


def eye_status(target):
    """1.0.0.3: one line for the installer's last message: whether the eye tracking set up before is still set up.
    VROSTBridge keeps Baballonia outside the version folders, so an update leaves it, its settings and its
    calibration as they were; this says so (or that eye tracking is not set up yet)."""
    try:
        from launcher.baballonia import BaballoniaManager
        manager = BaballoniaManager(log=lambda line: None, vulkan_layer=None)
        moved = manager.adopt_earlier_copy()  # one an installation before 1.0.0.1 left under another folder name
        if not manager.installed():
            print('Eye tracking: not set up yet (Eye tracking card).')
            return
        calibration = manager.calibration()
        line = 'Eye tracking: Baballonia ' + manager.release['version'] + (
            ' found where an earlier installation left it and moved into place' if moved else ' found and kept')
        if calibration.get('calibrated'):
            line += ', calibrated' + (' ' + calibration['date'] if calibration.get('date') else '')
        line += '.' if not manager.update_available() else '; Baballonia ' + manager.pinned['version'] + \
            ' can be downloaded on the Eye tracking card.'
        print(line)
    except Exception:
        pass


def uninstall_client(target, keep_eye_tracking=False):
    if not owned_release(target, 'client'):
        raise RuntimeError('Refusing to remove a directory not owned by this installer')
    base = releases_directory('client').parent
    current = base/'current'
    if current.is_symlink() and current.resolve() == target.resolve():
        stop_desktop('client')
        receipt=state_directory()/'client-runtime-settings.txt'
        if receipt.exists():
            from launcher.client_runtime import ClientRuntime
            if not ClientRuntime(target/'VROSTBridgeClient',state_directory(),print).restore():
                raise RuntimeError('Close SteamVR before uninstalling. Saved settings could not be restored; application files were retained.')
        from launcher.game_mode import GameMode
        setup=GameMode(target)
        setup.disable()
        # The Steam library entry would point at removed files (Steam restarts if it is open).
        try:
            from launcher.steam_shortcut import SteamShortcut
            shortcut=SteamShortcut(target,setup.executable,target/'brand'/'steam',setup.directory/'steam-shortcut.json',log=print)
            if shortcut.receipt.exists() or shortcut.status().get('added'):shortcut.remove()
        except Exception as error:
            print('The VROSTBridge entry in Steam was left in place ('+str(error)[:200]+'). Remove it in Steam.')
        remove_client_firewall()
        if keep_eye_tracking:
            # 1.0.0.3: kept for the next installation, which uses it without downloading it again.
            print('The Baballonia VROSTBridge downloaded was kept for the next installation: ~/.local/share/vrostbridge/baballonia.')
        else:
            remove_eye_tracking()
        data = base.parent
        for name in ('io.vrostbridge.Client.desktop', 'io.vrostbridge.GameMode.desktop', 'io.vrostbridge.Uninstall.desktop'):
            path = data/'applications'/name
            # Old uninstallers must never remove shortcuts for a newer install.
            if path.is_file() and ('X-VROSTBridge-Managed=true' in path.read_text(encoding='utf-8') or str(base).replace('\\', '\\\\') in path.read_text(encoding='utf-8')):
                path.unlink()
        for icon in (data/'icons/hicolor').glob('*/apps/vrostbridge.*'):
            icon.unlink(missing_ok=True)
        current.unlink()
        for release in releases('client'):
            shutil.rmtree(release)
    else:
        shutil.rmtree(target)
    print('VROSTBridge Client removed. Settings, client identity and logs were retained.')


def uninstall_host(target):
    if not owned_release(target, 'host'):
        raise RuntimeError('Refusing to remove a directory not owned by this installer')
    location = windows_reg_value(UNINSTALL_KEY, 'InstallLocation')
    current = bool(location and Path(location).resolve() == target.resolve())
    if current:
        stop_desktop('host')
    preserve_host_state([target], target)
    if (target/'dist/host').resolve() in registered_drivers():
        run_host_setup(target, 'Unregister')
    if (target/'dist/host').resolve() in registered_drivers():
        raise RuntimeError('SteamVR still references this installation. Its files were retained.')
    if current:
        run_host_setup(target, 'RemoveFirewall')
    (state_directory()/'host-upgrade.json').unlink(missing_ok=True)
    # NSIS removes files only after this returns and guards shared menu/registry
    # entries against a newer installation replacing this one.


# ---------------------------------------------------------------- Windows client (1.0.0.7)
# packaging/windows-client-installer.nsi runs these with the payload's own Python, as the host installer runs its
# steps: prepare (stop the running companion), commit (verified copy, same-version repair with rollback), finish
# (only the new version folder remains) and uninstall. Settings, pairing and logs live in %LOCALAPPDATA%\VROSTBridge,
# outside the version folders, so an update or an uninstall keeps them.

def windows_client_directory():
    return Path(os.environ['LOCALAPPDATA'])/'VROSTBridge'/'Client'


def windows_client_version():
    """The client version of the package this script belongs to (its launcher/version.py)."""
    from launcher.version import CLIENT_VERSION
    return CLIENT_VERSION


def windows_client_identity(version):
    """PACKAGED.json of a Windows client package (packaging/build_windows_client.py writes it)."""
    return {'version': version, 'role': 'client', 'platform': 'windows', 'alvr_commit': PIN}


def is_windows_client(identity, version):
    """Whether an installed folder's identity is the Windows client's for that version. Other keys do not matter, so
    a later release that pins another ALVR revision or adds a key still recognises (and replaces) this one."""
    return (isinstance(identity, dict) and identity.get('version') == version and identity.get('role') == 'client' and
            identity.get('platform') == 'windows')


def owned_windows_client(path):
    """Exactly <LOCALAPPDATA>\\VROSTBridge\\Client\\<version> with that client identity; never a link or junction."""
    path = Path(path).absolute()
    base = windows_client_directory().absolute()
    if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or
            path.parent.resolve() != base.resolve() or not path.is_dir()):
        return False
    try:
        marker = path/'PACKAGED.json'
        if marker.is_symlink():
            return False
        identity = json.loads(marker.read_text(encoding='utf-8'))
        version = identity.get('version')
        if (not isinstance(version, str) or len(version) > 128 or
                not _RELEASE_VERSION.fullmatch(version)):
            return False
        return is_windows_client(identity, version) and path.name == version
    except (OSError, ValueError, AttributeError):
        return False


def windows_client_releases():
    base = windows_client_directory()
    return [p for p in base.iterdir() if owned_windows_client(p)] if base.is_dir() else []


def prepare_windows_client(target):
    if target.parent.resolve() != windows_client_directory().resolve() or target.name != windows_client_version():
        raise RuntimeError('The client installation target is not an owned release location')
    if (target.exists() or target.is_symlink()) and not owned_windows_client(target):
        raise RuntimeError('The target contains files that are not a VROSTBridge installation')
    stop_desktop('client')
    path = state_directory()/'client-upgrade.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'target': str(target.resolve())}, indent=2), encoding='utf-8')


def commit_windows_client(target, staged):
    """Copy from the verified temporary payload; a same-version repair is restored if the copy fails."""
    pending_path = state_directory()/'client-upgrade.json'
    pending = json.loads(pending_path.read_text(encoding='utf-8'))
    if Path(pending['target']).resolve() != target.resolve():
        raise RuntimeError('The prepared installation target changed')
    version = windows_client_version()
    if json.loads((staged/'PACKAGED.json').read_text(encoding='utf-8')) != windows_client_identity(version):
        raise RuntimeError('Invalid staged package identity')
    seen = set()
    for line in (staged/'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, _, name = line.partition('  ')
        relative = Path(name)
        source = staged/relative
        if (not re.fullmatch('[0-9a-f]{64}', digest) or not name or name in seen or '\\' in name or
                relative.is_absolute() or '..' in relative.parts or
                not source.resolve().is_relative_to(staged.resolve()) or
                hashlib.sha256(source.read_bytes()).hexdigest() != digest):
            raise RuntimeError('Staged package integrity check failed')
        seen.add(name)
    if not seen:
        raise RuntimeError('The package manifest is empty')
    backup = None
    if target.exists():
        if not owned_windows_client(target):
            raise RuntimeError('The target is not an owned installation')
        backup = target.with_name('.repair-'+version+'-'+str(time.time_ns()))
        deadline = time.monotonic()+8
        while True:
            try:
                target.rename(backup); break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('The old installation is still in use. Close VROSTBridge Client and SteamVR, then retry.')
                time.sleep(.2)
    try:
        # copyfile, not copy2: the payload's bytes are what the manifest checks (and Wine lacks CopyFile2).
        shutil.copytree(staged, target, copy_function=shutil.copyfile)
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if backup:
            backup.rename(target)
        raise
    pending['repair_backup'] = str(backup) if backup else None
    pending_path.write_text(json.dumps(pending, indent=2), encoding='utf-8')


def finish_windows_client(target):
    if not owned_windows_client(target):
        raise RuntimeError('The new client identity is invalid; old versions were retained')
    path = state_directory()/'client-upgrade.json'
    pending = json.loads(path.read_text(encoding='utf-8'))
    if Path(pending['target']).resolve() != target.resolve():
        raise RuntimeError('The prepared installation target changed')
    for release in windows_client_releases():
        if release.resolve() != target.resolve():
            shutil.rmtree(release)
    for repair in windows_client_directory().glob('.repair-*'):
        if repair.is_symlink() or not re.fullmatch(r'\.repair-'+re.escape(target.name)+r'-[0-9]+', repair.name):
            continue
        try:
            identity = json.loads((repair/'PACKAGED.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if is_windows_client(identity, target.name):
            shutil.rmtree(repair)
    path.unlink()


def remove_windows_client_firewall():
    """Only the rules the client created (VROSTBridge.Client.*, with VROSTBridge's ownership marker), through
    launcher/Client-Firewall.ps1 -Action RemoveFirewall: one administrator prompt, and only if any exist."""
    from launcher.client_network import remove_windows_rules
    result = remove_windows_rules()
    if not result.get('ok'):
        advice = ('Firewall cleanup needs administrator approval. Retry uninstall and approve the request.'
                  if result.get('declined') else 'Firewall cleanup failed.')
        raise RuntimeError(advice+' Existing client files were retained. '+str(result.get('message') or ''))
    print(result.get('message') or 'VROSTBridge client firewall rules removed.')


def remove_windows_eye_tracking():
    """The Baballonia VROSTBridge downloaded (%LOCALAPPDATA%\\VROSTBridge\\Baballonia, about 880 MB installed) goes with
    VROSTBridge on request; its own settings and calibration stay. launcher/baballonia.py's
    remove_downloaded(log) stops a Baballonia VROSTBridge started and removes the download, returning whether there
    was one, and raises when it could not. A failure leaves it in place and is reported, as on Linux."""
    folder = state_directory()/'Baballonia'
    try:
        from launcher import baballonia
        remove = getattr(baballonia, 'remove_downloaded', None)
        if remove is None:
            raise RuntimeError('this VROSTBridge cannot remove it on Windows')
        if remove(log=print):
            print('The Baballonia VROSTBridge downloaded was removed; its settings and calibration stay.')
    except Exception as error:
        print('The downloaded Baballonia was left in place ('+str(error)[:200]+'): '+str(folder))


def restore_windows_power_mode():
    """A session that ended in a crash can leave Windows' power mode at Best performance until the companion next
    starts (launcher/session_power.py keeps a receipt); uninstalling puts it back now. Never stops the uninstall."""
    try:
        from launcher.session_power import SessionPower
        SessionPower(log=print, state_directory=state_directory()).recover()
    except Exception as error:
        print('The Windows power mode could not be checked ('+str(error)[:200]+').')


def uninstall_windows_client(target, keep_eye_tracking=False):
    if not owned_windows_client(target):
        raise RuntimeError('Refusing to remove a directory not owned by this installer')
    location = windows_reg_value(CLIENT_UNINSTALL_KEY, 'InstallLocation')
    # A stale uninstaller (another version's folder) removes only its own files.
    if location and Path(location).resolve() == target.resolve():
        stop_desktop('client')
        restore_windows_power_mode()
        receipt = state_directory()/'client-runtime-settings.txt'
        if receipt.exists():
            from launcher.client_runtime import ClientRuntime
            if not ClientRuntime(target/'VROSTBridgeClient.exe', state_directory(), print).restore():
                raise RuntimeError('Close SteamVR before uninstalling. Saved settings could not be restored; application files were retained.')
        # No Steam entry to remove: Add to Steam belongs to the Bazzite client's Game Mode.
        remove_windows_client_firewall()
        if keep_eye_tracking:
            print('The Baballonia VROSTBridge downloaded was kept for the next installation: '+str(state_directory()/'Baballonia'))
        else:
            remove_windows_eye_tracking()
    (state_directory()/'client-upgrade.json').unlink(missing_ok=True)
    print('VROSTBridge Client removed. Settings, client identity and logs were retained.')
    # NSIS removes the files after this returns, and the shortcuts and registry entries only for the current install.


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare-client', 'finish-client', 'uninstall-client', 'prepare-host', 'commit-host', 'finish-host', 'uninstall-host', 'eye-status',
                                           'prepare-windows-client', 'commit-windows-client', 'finish-windows-client', 'uninstall-windows-client'))
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--staged-payload', type=Path)
    parser.add_argument('--keep-eye-tracking', action='store_true', help='uninstall-client, uninstall-windows-client: keep the downloaded Baballonia')
    args = parser.parse_args(argv)
    target = args.target.absolute()
    if args.action == 'prepare-client':
        if target.parent.resolve() != releases_directory('client').resolve():
            raise RuntimeError('Invalid client installation location')
        current = releases_directory('client').parent/'current'
        if current.exists() and not current.is_symlink():
            raise RuntimeError('The current client path is not an installer-owned link')
        if (target.exists() or target.is_symlink()) and not owned_release(target, 'client'):
            raise RuntimeError('The target contains files not owned by VROSTBridge')
        stop_desktop('client')
    elif args.action == 'uninstall-client':
        uninstall_client(target, keep_eye_tracking=args.keep_eye_tracking)
    elif args.action == 'uninstall-windows-client':
        uninstall_windows_client(target, keep_eye_tracking=args.keep_eye_tracking)
    elif args.action in ('commit-host', 'commit-windows-client'):
        if not args.staged_payload:
            raise RuntimeError('The staged payload is missing')
        (commit_host if args.action == 'commit-host' else commit_windows_client)(target, args.staged_payload.absolute())
    else:
        globals()[args.action.replace('-', '_')](target)
    return 0


if __name__ == '__main__':
    # An installer runs this file with the payload's embedded Python, which does not put the package root on its
    # path (its ._pth entry resolves to runtime\ under Wine); the Windows client's steps import the companion.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        sys.exit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
