"""Real frozen Bazzite package install/upgrade/HTTP lifecycle smoke checks.

Run only after build-tools025/release/bazzite/VROSTBridge-Bazzite-Installer
has been completely built. Uses real packaged bytes and temporary XDG roots.
No headset, SteamVR, firewall privileges, or Windows validation is claimed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path('/workspace/scratch/647ea6e3ae16')
INSTALLER = ROOT/'build-tools025/release/bazzite/VROSTBridge-Bazzite-Installer'
OLD_INSTALLER = ROOT/'client-input025/VROSTBridge-Bazzite-Installer'
SOURCE = ROOT/'work/VROSTBridge-0.2.5'
RESULT_PATH = ROOT/'build-tools025/release-smoke-0.2.5.json'
RESULT = {'release': '0.2.5', 'platform': 'Linux container; no physical headset',
          'checks': {}, 'limits': ['No physical headset or live stream test',
                                  'No Windows host execution',
                                  'No privileged firewall mutation or graphical rendering test']}
PROCESSES = []


def passed(name):
    RESULT['checks'][name] = 'pass'
    print('PASS: '+name, flush=True)


def environment(base):
    env = dict(os.environ)
    # Do not reuse the caller's settings, protocol identity, or runtime socket.
    env.pop('LOCALAPPDATA', None)
    for key, suffix in (('XDG_DATA_HOME', 'data files'), ('XDG_STATE_HOME', 'state files'),
                        ('XDG_CONFIG_HOME', 'config files'), ('XDG_RUNTIME_DIR', 'runtime')):
        directory = base/suffix
        directory.mkdir(parents=True, exist_ok=True)
        if key == 'XDG_RUNTIME_DIR':
            directory.chmod(0o700)
        env[key] = str(directory)
    env.update(VROSTBRIDGE_INSTALL_NO_LAUNCH='1', VROSTBRIDGE_UNINSTALL_CONFIRMED='1',
               BROWSER='/bin/true')
    # Record launch attempts without opening Steam or a browser in this test.
    # The current package must remain entirely idle when opened in background.
    launchers = base/'fake launchers'
    launchers.mkdir()
    env['VROSTBRIDGE_SMOKE_LAUNCH_LOG'] = str(base/'external-launches.txt')
    for name in ('steam', 'xdg-open', 'browser'):
        path = launchers/name
        path.write_text('#!/bin/sh\nprintf "%s\\n" "'+name+' $*" >> "$VROSTBRIDGE_SMOKE_LAUNCH_LOG"\nexit 0\n')
        path.chmod(0o755)
    env['PATH'] = str(launchers)+os.pathsep+env.get('PATH','')
    env['BROWSER'] = shlex.quote(str(launchers/'browser'))+' %s'
    return env


def run(command, env, timeout=60):
    result = subprocess.run([str(part) for part in command], env=env,
                            capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, (command, result.returncode, result.stdout[-6000:], result.stderr[-6000:])
    return result


def install(installer, env):
    return run(['bash', installer/'install.sh'], env)


def paths(env):
    data = Path(env['XDG_DATA_HOME'])
    state = Path(env['XDG_STATE_HOME'])/'VROSTBridge'
    return data, state, data/'vrostbridge/current'


def package_identity(current, expected='0.2.5'):
    assert current.is_symlink(), 'Current installation is not a symlink'
    identity = json.loads((current/'PACKAGED.json').read_text())
    assert identity['role'] == 'client' and identity['version'] == expected, identity
    return current.resolve()


def verify_installed(current, env):
    outcome = run([current/'VROSTBridge-Client', '--role', 'client', '--self-test'], env, timeout=30)
    assert 'Verified ' in outcome.stdout and 'packaged resources OK' in outcome.stdout, outcome.stdout


def start_app(current, env, base, label, *, background=True):
    output = base/(label+'-output.txt')
    stream = output.open('w')
    command = [str(current/'VROSTBridge-Client'), '--role', 'client']
    if background:
        command.append('--background')
    process = subprocess.Popen(command, env=env,
                               stdout=stream, stderr=subprocess.STDOUT)
    stream.close()
    PROCESSES.append(process)
    _, state, _ = paths(env)
    record_path = state/'client-desktop.json'
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        assert process.poll() is None, output.read_text()
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            if record.get('pid') == process.pid:
                return process, record, record_path
        time.sleep(.1)
    raise AssertionError('Desktop did not publish its local endpoint: '+output.read_text())


def request(record, path, data=None, token=True, origin=None):
    headers = {'X-VROSTBridge-Token': record['token']} if token else {}
    if origin is not None:
        headers['Origin'] = origin
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    url = 'http://127.0.0.1:'+str(record['port'])+path
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=40)


def get_json(record, path):
    with request(record, path) as response:
        return json.load(response)


def action(record, value):
    with request(record, '/action', value) as response:
        assert json.load(response)['ok'] is True


def denied(record, path, data=None, **options):
    try:
        with request(record, path, data, **options) as response:
            raise AssertionError('Unauthorised request accepted: '+path)
    except urllib.error.HTTPError as error:
        assert error.code == 403, error.code
        error.close()


def check_desktop(process, record, current, env):
    with request(record, '/') as response:
        page = response.read()
        assert b'VROSTBridge 0.2.5' in page and b'Development build' in page
        assert b'Start session' in page and b'Allow home-network clients' in page
        assert b'Local by design' not in page and b'local by design' not in page
        assert b'ALVR device list' not in page
        assert b'Open host menu' in page and b'Lightweight client' in page
        assert b'Restore local menus' in page
    with request(record, '/brand/logo.png') as response:
        assert response.read() == (SOURCE/'launcher/brand/logo.png').read_bytes()
    for name in ('icon-256.png','icon.svg','vrostbridge.ico'):
        with request(record, '/brand/'+name) as response:
            assert response.read() == (SOURCE/'launcher/brand'/name).read_bytes()
    status = get_json(record, '/status')
    assert status['role'] == 'client' and status['packaged'] and status['available']
    assert isinstance(status['client_identity'], dict)
    assert isinstance(status['client_network'], dict)
    assert 'can_allow' in status['client_network']
    assert status['running'] is False
    assert status['client_runtime']['state'] == 'idle'
    assert status['session_link']['active'] is False
    report = get_json(record, '/report')
    assert report['role'] == 'client' and 'client_identity' in report and 'client_network' in report
    assert report['version']=='0.2.5' and 'debug' in report and 'steamvr_logs' in report
    assert 'inputs' in report and 'session_link' in report
    assert record['token'] not in json.dumps(report), 'A report leaked the desktop access token'
    denied(record, '/status', token=False)
    denied(record, '/action', {'action': 'stop'}, token=False)
    denied(record, '/status', origin='https://example.invalid')
    reopened = run([current/'VROSTBridge-Client', '--role', 'client', '--background'], env, timeout=20)
    assert process.poll() is None, reopened.stdout+reopened.stderr
    _, state, _ = paths(env)
    assert json.loads((state/'client-desktop.json').read_text())['pid'] == process.pid
    passed('frozen_http_page_logo_identity_network_report_auth_and_reopen')
    # Give readiness checks time to finish. They cannot launch SteamVR.
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        status = get_json(record, '/status')
        assert not status['running'] and status['client_runtime']['state'] == 'idle'
        if status['readiness'] is not None and not status['busy']:
            break
        time.sleep(.1)
    assert status['readiness'] is not None, 'Readiness check did not finish'
    attempts = Path(env['VROSTBRIDGE_SMOKE_LAUNCH_LOG'])
    assert not attempts.exists() or not attempts.read_text(), 'Background companion opened Steam/browser: '+attempts.read_text()
    passed('background_and_reopen_do_not_start_steamvr_or_browser')
    # Stop remains stable with no active session or runtime.
    action(record, {'action': 'stop'})
    for _ in range(3):
        time.sleep(.2)
        stopped = get_json(record, '/status')
        assert not stopped['running'] and stopped['state'] == 'stopped', stopped['state']
    passed('explicit_stop_stays_stopped')


def retained_files(env):
    data, state, current = paths(env)
    state.mkdir(parents=True, exist_ok=True)
    config = Path(env['XDG_CONFIG_HOME'])/'ALVR Client/session.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    files = {
        state/'client-settings.json': b'{"microphone":false,"gaze":false,"software":false}\n',
        state/'retained-diagnostic.log': b'user diagnostic evidence\n',
        config: b'{"hostname":"0317.client.local.","protocol_id":"smoke-retained-identity"}\n',
    }
    for path, content in files.items():
        path.write_bytes(content)
    return files


def assert_retained(files):
    for path, expected in files.items():
        assert path.read_bytes() == expected, 'Mutable state changed or vanished: '+str(path)


def seed_foreign(env, base):
    data, _, current = paths(env)
    versions = current.parent/'versions'
    variants = [versions/'notes', versions/'0.2.1-333333333333', versions/'0.2.2-ffffffffffff']
    markers = []
    for directory in variants:
        directory.mkdir(parents=True)
        marker = directory/'keep.txt'; marker.write_text('unrelated content')
        markers.append(marker)
    (variants[-1]/'PACKAGED.json').write_text(json.dumps({'version': '0.2.2', 'role': 'client', 'alvr_commit': 'foreign'}))
    outside = base/'foreign target'
    outside.mkdir(); marker = outside/'keep.txt'; marker.write_text('outside installation'); markers.append(marker)
    symlink = versions/'0.2.0-444444444444'; symlink.symlink_to(outside, target_is_directory=True)
    foreign_menu = data/'applications/unrelated.desktop'; foreign_menu.write_text('[Desktop Entry]\nName=Other App\n')
    return markers+[foreign_menu], symlink


def assert_foreign(markers, symlink):
    assert all(path.is_file() for path in markers), 'Installer removed an unowned file'
    assert symlink.is_symlink(), 'Installer removed an unowned symlink'


def main():
    assert (INSTALLER/'payload/PACKAGED.json').is_file(), 'Build the 0.2.5 package before running this smoke test'
    assert json.loads((INSTALLER/'payload/PACKAGED.json').read_text())['version'] == '0.2.5'
    assert (OLD_INSTALLER/'payload/PACKAGED.json').is_file(), 'The actual 0.2.4 package is needed for the upgrade gate'
    assert json.loads((OLD_INSTALLER/'payload/PACKAGED.json').read_text())['version'] == '0.2.4'
    RESULT['time_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    RESULT['tested_inputs_sha256'] = {
        'new_payload_manifest': hashlib.sha256((INSTALLER/'payload/SHA256SUMS').read_bytes()).hexdigest(),
        'new_client_companion': hashlib.sha256((INSTALLER/'payload/VROSTBridge-Client').read_bytes()).hexdigest(),
        'new_native_client': hashlib.sha256((INSTALLER/'payload/VROSTBridgeClient').read_bytes()).hexdigest(),
        'old_payload_manifest': hashlib.sha256((OLD_INSTALLER/'payload/SHA256SUMS').read_bytes()).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix='VROSTBridge release smoke Ü ') as temporary:
        base = Path(temporary)
        fresh = base/'fresh'; fresh.mkdir()
        env = environment(fresh)
        install(INSTALLER, env)
        data, state, current = paths(env)
        release = package_identity(current)
        verify_installed(current, env)
        assert (data/'applications/io.vrostbridge.Client.desktop').is_file()
        assert (data/'applications/io.vrostbridge.Uninstall.desktop').is_file()
        play = data/'applications/io.vrostbridge.Play.desktop'
        assert play.is_file()
        assert 'X-VROSTBridge-Managed=true' in play.read_text()
        assert '--role client --play' in play.read_text()
        assert 'Icon=io.vrostbridge.Client' in play.read_text()
        assert (data/'icons/hicolor/256x256/apps/io.vrostbridge.Client.png').read_bytes() == (SOURCE/'launcher/brand/icon-256.png').read_bytes()
        passed('fresh_install_spaces_unicode_manifest_and_shortcuts')
        process, record, record_path = start_app(current, env, fresh, 'new')
        check_desktop(process, record, current, env)
        # A real reinstall must stop the authenticated owned launcher before repair.
        install(INSTALLER, env)
        assert process.wait(timeout=20) == 0
        assert not record_path.exists()
        assert package_identity(current) == release
        verify_installed(current, env)
        passed('same_version_reinstall_stops_running_launcher_and_repairs')
        # Deliberate corruption is limited to the temporary installed copy.
        (current/'brand/logo.png').write_bytes(b'corrupt smoke fixture')
        broken = subprocess.run([str(current/'VROSTBridge-Client'), '--role', 'client', '--self-test'],
                                env=env, capture_output=True, text=True, timeout=30)
        assert broken.returncode != 0
        assert 'damaged or changed' in (broken.stdout+broken.stderr).lower(), broken.stdout+broken.stderr
        assert (state/'client-startup.log').is_file()
        passed('corrupt_install_rejected_and_logged')
        install(INSTALLER, env)
        verify_installed(current, env)
        assert (current/'brand/logo.png').read_bytes() == (SOURCE/'launcher/brand/logo.png').read_bytes()
        assert not list((data/'vrostbridge/versions').glob('.replaced-*'))
        assert not list((data/'vrostbridge/versions').glob('.install-*'))
        passed('same_version_corruption_repaired_atomically')
        process, record, record_path = start_app(current, env, fresh, 'repaired')
        action(record, {'action': 'quit'})
        assert process.wait(timeout=20) == 0 and not record_path.exists()
        passed('web_quit_releases_single_instance')
        process, record, record_path = start_app(current, env, fresh, 'signal-stop')
        process.terminate()
        assert process.wait(timeout=25) == 0 and not record_path.exists()
        # Starting another actual frozen process proves the OS single-instance
        # lock was released, not merely that the metadata file was removed.
        process, record, record_path = start_app(current, env, fresh, 'after-signal')
        assert get_json(record, '/status')['running'] is False
        action(record, {'action':'quit'})
        assert process.wait(timeout=25) == 0 and not record_path.exists()
        passed('graceful_sigterm_removes_endpoint_and_releases_single_instance')

        upgrade = base/'upgrade'; upgrade.mkdir()
        old_env = environment(upgrade)
        install(OLD_INSTALLER, old_env)
        data, state, current = paths(old_env)
        old_release = package_identity(current, '0.2.4')
        retained = retained_files(old_env)
        foreign, symlink = seed_foreign(old_env, upgrade)
        old_process, old_record, old_record_path = start_app(current, old_env, upgrade, 'old', background=False)
        assert get_json(old_record, '/status')['role'] == 'client'
        install(INSTALLER, old_env)
        assert old_process.wait(timeout=20) == 0
        assert not old_record_path.exists()
        assert not old_release.exists(), 'Previous managed 0.2.4 payload was left behind'
        new_release = package_identity(current)
        verify_installed(current, old_env)
        assert_retained(retained); assert_foreign(foreign, symlink)
        passed('real_0_2_4_upgrade_stops_old_app_cleans_payload_and_retains_state')
        # Exercise the shipped uninstaller wrapper, not the maintenance function directly.
        run(['bash', current/'uninstall.sh'], old_env)
        assert not current.exists() and not current.is_symlink()
        assert not new_release.exists()
        for name in ('io.vrostbridge.Client.desktop', 'io.vrostbridge.Play.desktop', 'io.vrostbridge.Uninstall.desktop'):
            assert not (data/'applications'/name).exists()
        assert not (data/'icons/hicolor/256x256/apps/io.vrostbridge.Client.png').exists()
        assert_retained(retained); assert_foreign(foreign, symlink)
        passed('real_uninstall_preserves_settings_identity_logs_and_unowned_files')
        RESULT['outcome'] = 'pass'


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        RESULT['outcome'] = 'fail'
        RESULT['error'] = str(error)
        RESULT['traceback'] = traceback.format_exc()
        raise
    finally:
        for process in PROCESSES:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        RESULT_PATH.write_text(json.dumps(RESULT, indent=2)+'\n')
        print('Result: '+str(RESULT_PATH), flush=True)
