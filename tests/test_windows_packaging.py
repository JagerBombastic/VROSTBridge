"""1.0.0.7: the Windows client package.

packaging/build_windows_client.py (the payload against a stand-in native folder), packaging/windows-client-installer.nsi
(its identity beside the host's), packaging/windows-launcher.c and build_windows_launcher.py (both roles; built only
when clang, lld-link, llvm-rc and a Windows SDK named by VROSTBRIDGE_WINDOWS_SDK are present) and
launcher/install_maintenance.py's *-windows-client steps.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

import build_windows_client as client_build  # noqa: E402
import build_windows_launcher as launcher_build  # noqa: E402
from launcher import install_maintenance as m  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def fake_pe(text=b'', imports=(), subsystem=3, machine=0x8664, plus=True):
    """A minimal Windows image: headers, one section with an import table naming `imports`, then `text`."""
    names = b''
    offsets = []
    for dll in imports:
        offsets.append(len(names))
        names += dll.encode() + b'\0'
    table = 20 * (len(imports) + 1)
    section = b''.join(struct.pack('<IIIII', 0, 0, 0, 0x1000 + table + offset, 0) for offset in offsets)
    section += b'\0' * 20 + names
    section += b'\0' * (-len(section) % 0x200)
    optional = bytearray(240)
    struct.pack_into('<H', optional, 0, 0x20B if plus else 0x10B)
    struct.pack_into('<H', optional, 68, subsystem)
    struct.pack_into('<I', optional, 108, 16)
    if imports:
        struct.pack_into('<II', optional, 112 + 8, 0x1000, table)
    header = bytearray(0x40)
    header[:2] = b'MZ'
    struct.pack_into('<I', header, 0x3C, 0x40)
    coff = b'PE\0\0' + struct.pack('<HHIIIHH', machine, 1, 0, 0, 0, len(optional), 0x22)
    section_header = struct.pack('<8sIIIIIIHHI', b'.idata', len(section), 0x1000, len(section), 0x200, 0, 0, 0, 0, 0xC0000040)
    image = bytes(header) + coff + bytes(optional) + section_header
    image += b'\0' * (0x200 - len(image)) + section
    return image + text


def version_resource(*pairs):
    """UTF-16 version strings as a resource holds them ("FileVersion", "3.13.12", ...)."""
    return b''.join(key.encode('utf-16-le') + b'\0\0' + value.encode('utf-16-le') + b'\0\0' for key, value in pairs)


def stand_in_native(folder, version=CLIENT_VERSION):
    """The Windows client build's folder, with stand-ins for its programs."""
    folder.mkdir(parents=True)
    label = ('VROSTBridge %s native Windows client (stand-in)' % version).encode()
    (folder / 'VROSTBridgeClient.exe').write_bytes(fake_pe(label, ('KERNEL32.dll', 'vrostbridge_transport.dll', 'avcodec-60.dll')))
    (folder / 'VROSTBridgeRuntimeProbe.exe').write_bytes(fake_pe(('VROSTBridge %s runtime probe' % version).encode(), ('KERNEL32.dll',)))
    for name in ('vrostbridge_transport.dll', 'avcodec-60.dll', 'avutil-58.dll', 'swscale-7.dll', 'openvr_api.dll'):
        (folder / name).write_bytes(fake_pe(b'stand-in ' + name.encode(), ('KERNEL32.dll', 'api-ms-win-crt-runtime-l1-1-0.dll')))
    (folder / 'avutil-58.dll').write_bytes(fake_pe(b'stand-in avutil-58.dll\0' + client_build.FFMPEG_RELEASE.encode() + b'\0'))
    for name in client_build.NATIVE_ASSETS:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b'stand-in ' + name.encode())
    (folder / 'VROSTBridgeClient.pdb').write_bytes(b'debug information')
    (folder / 'launcher').mkdir()
    (folder / 'launcher' / 'app.py').write_text('# a build folder copy, not packaged\n')
    (folder / 'third_party' / 'ffmpeg').mkdir(parents=True)
    (folder / 'third_party' / 'ffmpeg' / 'BUILD-windows.txt').write_text('FFmpeg n6.1.4 for Windows: configure ...\n')
    return folder


def stand_in_python(folder):
    """An embedded CPython's layout, inside a host payload whose SHA256SUMS lists it."""
    python = folder / 'runtime' / 'python'
    python.mkdir(parents=True)
    (python / 'python.exe').write_bytes(fake_pe(version_resource(('FileVersion', '3.13.12'), ('ProductVersion', '3.13.12'))))
    (python / 'python313.dll').write_bytes(fake_pe(b'python313'))
    (python / 'python313.zip').write_bytes(b'PK\5\6' + b'\0' * 18)
    (python / 'python313._pth').write_text('python313.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n\n../..\n')
    (python / 'vcruntime140.dll').write_bytes(fake_pe(b'vcruntime'))
    (folder / 'PACKAGED.json').write_text(json.dumps({'version': HOST_VERSION, 'role': 'host', 'alvr_commit': m.PIN}))
    (folder / 'HOST_PROVENANCE.txt').write_text('Python: https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip\n')
    client_build.manifest(folder)
    return python


def stand_in_launcher(path, version=CLIENT_VERSION):
    text = version_resource(('FileDescription', 'VROSTBridge Client'), ('FileVersion', version),
                            ('OriginalFilename', 'VROSTBridge Client.exe'))
    text += ('\r\n--- VROSTBridge Client native launcher %s ---\r\n' % version).encode()
    path.write_bytes(fake_pe(text, ('KERNEL32.dll', 'USER32.dll', 'SHELL32.dll'), subsystem=2))
    return path


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='VROSTBridge client package Ü ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.native = stand_in_native(self.base / 'native')
        self.python = stand_in_python(self.base / 'host payload')
        self.launcher = stand_in_launcher(self.base / 'VROSTBridge Client.exe')
        self.payload = self.base / 'payload'

    def assemble(self):
        with patch('builtins.print'):
            return client_build.assemble(self.payload, self.native, self.python, self.launcher, CLIENT_VERSION, 'Native launcher: test.')

    def files(self):
        return sorted(p.relative_to(self.payload).as_posix() for p in self.payload.rglob('*') if p.is_file())

    def test_the_payload_layout(self):
        self.assemble()
        files = self.files()
        for name in ('VROSTBridge Client.exe', *client_build.NATIVE_FILES, *client_build.NATIVE_ASSETS,
                     'runtime/python/python.exe', 'runtime/python/python313._pth', 'launcher/bootstrap.py', 'launcher/app.py',
                     'launcher/install_maintenance.py', 'launcher/Client-Firewall.ps1', 'launcher/desktop.html',
                     'launcher/brand/vrostbridge.ico', 'launcher/fonts/Inter-Variable.ttf', 'host/default-session.json',
                     'host/profile.py', 'host/events.py', 'host/vendor/websockets/VENDORED.md', 'docs/OPEN_SOURCE_CREDITS.md',
                     'licenses/FFmpeg-LGPL-2.1.txt', 'brand/vrostbridge.ico', 'LICENSE', 'NOTICE', 'THIRD_PARTY.md',
                     'third_party/ffmpeg/' + client_build.FFMPEG_SOURCE, 'third_party/ffmpeg/LICENSE.md',
                     'third_party/ffmpeg/BUILD-windows.txt', 'third_party/openvr/LICENSE', 'CLIENT_PROVENANCE.txt',
                     'PACKAGED.json', 'SHA256SUMS'):
            self.assertIn(name, files)
        for path in files:
            self.assertNotIn('__pycache__', path)
            self.assertNotRegex(path, r'\.(pyc|pdb)$')
            self.assertFalse(path.startswith('launcher/game_ui_assets/'), path)
        # No host programs, sources or scripts, no Microsoft C++ installer, no build-folder copies of the companion.
        self.assertEqual([p for p in files if p.startswith('host/') and not re.search(r'\.(py|json)$|/VENDORED\.md$', p)], [])
        self.assertNotIn('host/Host-Setup.ps1', files)
        self.assertEqual([p for p in files if 'vc_redist' in p.lower()], [])
        self.assertNotIn('# a build folder copy', (self.payload / 'launcher/app.py').read_text())
        self.assertNotIn('third_party/ffmpeg/BUILD.txt', files, 'the Linux build notes do not describe the Windows DLLs')
        # The companion is the source's: every launcher file except Game Mode's artwork.
        source = sorted(p.relative_to(ROOT / 'launcher').as_posix() for p in (ROOT / 'launcher').rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts and 'game_ui_assets' not in p.parts)
        self.assertEqual(sorted(p[len('launcher/'):] for p in files if p.startswith('launcher/')), source)
        self.assertEqual((self.payload / 'launcher/install_maintenance.py').read_bytes(), (ROOT / 'launcher/install_maintenance.py').read_bytes())

    def test_identity_manifest_and_runtime(self):
        self.assemble()
        identity = json.loads((self.payload / 'PACKAGED.json').read_text())
        self.assertEqual(identity, {'version': CLIENT_VERSION, 'role': 'client', 'platform': 'windows', 'alvr_commit': m.PIN})
        self.assertEqual(identity, m.windows_client_identity(CLIENT_VERSION))
        listed = {}
        for line in (self.payload / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ', 1)
            self.assertNotIn('\\', name)
            listed[name] = digest
        self.assertEqual(sorted(listed), [p for p in self.files() if p != 'SHA256SUMS'])
        for name, digest in listed.items():
            self.assertEqual(hashlib.sha256((self.payload / name).read_bytes()).hexdigest(), digest, name)
        for path in self.python.iterdir():
            self.assertEqual((self.payload / 'runtime/python' / path.name).read_bytes(), path.read_bytes())
        provenance = (self.payload / 'CLIENT_PROVENANCE.txt').read_text()
        self.assertIn('CPython 3.13.12', provenance)
        self.assertIn('python-3.13.12-embed-amd64.zip', provenance)
        self.assertIn('byte for byte the runtime/python of the verified VROSTBridge Host ' + HOST_VERSION, provenance)
        self.assertIn('third_party/ffmpeg/BUILD-windows.txt', provenance)
        self.assertIn(hashlib.sha256(self.launcher.read_bytes()).hexdigest(), provenance)
        self.assertNotIn(str(self.base), provenance, 'no build-machine paths')

    def test_a_changed_runtime_or_python_layout_is_refused(self):
        (self.python / 'python313.dll').write_bytes(b'changed')
        with self.assertRaisesRegex(SystemExit, 'differs from what'):
            self.assemble()
        (self.python.parent.parent / 'SHA256SUMS').unlink()
        (self.python / 'python313._pth').unlink()
        with self.assertRaisesRegex(SystemExit, 'not an embedded CPython'):
            self.assemble()

    def test_native_folder_checks(self):
        (self.native / 'swscale-7.dll').unlink()
        with self.assertRaisesRegex(SystemExit, 'swscale-7.dll'):
            self.assemble()
        (self.native / 'swscale-7.dll').write_bytes(fake_pe(b'', ('KERNEL32.dll', 'VCRUNTIME140.dll')))
        with self.assertRaisesRegex(SystemExit, 'VCRUNTIME140.dll'):
            self.assemble()
        (self.native / 'swscale-7.dll').write_bytes(fake_pe(b'', ('libwinpthread-1.dll',)))
        (self.native / 'libwinpthread-1.dll').write_bytes(fake_pe(b''))
        self.assemble()  # a MinGW runtime the package carries is fine
        self.assertIn('libwinpthread-1.dll', self.files())
        (self.native / 'avutil-58.dll').write_bytes(fake_pe(b'n6.1.2'))
        with self.assertRaisesRegex(SystemExit, 'does not say FFmpeg 6.1.4'):
            self.assemble()
        (self.native / 'avutil-58.dll').write_bytes(fake_pe(b'n6.1.4', machine=0x14C, plus=False))
        with self.assertRaisesRegex(SystemExit, 'not a 64-bit'):
            self.assemble()
        (self.native / 'avutil-58.dll').write_bytes(fake_pe(b'n6.1.4'))
        (self.native / 'VROSTBridgeRuntimeProbe.exe').write_bytes(fake_pe(b'VROSTBridge 1.0.0.5 runtime probe'))
        with self.assertRaisesRegex(SystemExit, 'not the %s build' % re.escape(CLIENT_VERSION)):
            self.assemble()
        (self.native / 'VROSTBridgeRuntimeProbe.exe').write_bytes(fake_pe(('VROSTBridge %s runtime probe' % CLIENT_VERSION).encode()))
        (self.native / 'runtime').mkdir()
        with self.assertRaisesRegex(SystemExit, 'runtime'):
            self.assemble()

    def test_the_launcher_is_checked(self):
        stand_in_launcher(self.launcher, '1.0.0.5')
        with self.assertRaisesRegex(SystemExit, 'VROSTBridge Client'):
            self.assemble()
        self.launcher.write_bytes(fake_pe(b'', ('KERNEL32.dll',), subsystem=3))
        with self.assertRaisesRegex(SystemExit, 'GUI program'):
            self.assemble()

    def test_settled_files_give_the_same_zip(self):
        self.assemble()
        client_build.settle(self.payload, client_build.DEFAULT_EPOCH)
        first = self.base / 'first.zip'
        with patch.object(client_build.subprocess, 'run'):
            setup, portable = client_build.package(self.payload, self.base, CLIENT_VERSION, 'makensis')
        self.assertEqual(setup.name, 'VROSTBridge-Client-Setup-%s.exe' % CLIENT_VERSION)
        self.assertEqual(portable.name, 'VROSTBridge-Client-Portable-%s.zip' % CLIENT_VERSION)
        portable.rename(first)
        with patch.object(client_build.subprocess, 'run') as run:
            client_build.package(self.payload, self.base, CLIENT_VERSION, 'makensis')
        self.assertEqual(portable.read_bytes(), first.read_bytes())
        command = run.call_args.args[0]
        self.assertIn('-DVERSION=' + CLIENT_VERSION, command)
        self.assertIn('-DVERSION_NUMERIC=' + launcher_build.numeric_version(CLIENT_VERSION).replace(',', '.'), command)
        self.assertEqual(command[-1], str(ROOT / 'packaging/windows-client-installer.nsi'))
        import zipfile
        with zipfile.ZipFile(portable) as archive:
            self.assertTrue(all(name.startswith('VROSTBridge-Client/') for name in archive.namelist()))
            self.assertIn('VROSTBridge-Client/VROSTBridge Client.exe', archive.namelist())


class InstallerScriptTests(unittest.TestCase):
    client = read('packaging/windows-client-installer.nsi')
    host = read('packaging/windows-installer.nsi')

    @staticmethod
    def defines(script):
        return dict(re.findall(r'^!define (\w+) "([^"]*)"', script, re.M))

    @staticmethod
    def shortcuts(script, command):
        return set(re.findall(r'^\s*' + command + r' "\$\{MENU\}\\([^"]+)"', script, re.M))

    def test_the_identity_differs_from_the_hosts(self):
        client, host = self.defines(self.client), self.defines(self.host)
        self.assertEqual(client['APPKEY'], r'Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeClient')
        self.assertNotEqual(client['APPKEY'], host['APPKEY'])
        self.assertEqual(client['APPKEY'], m.CLIENT_UNINSTALL_KEY)
        self.assertEqual(client['RUNKEY'], host['RUNKEY'])
        self.assertEqual(client['RUNVALUE'], 'VROSTBridgeClient')
        self.assertNotIn('"VROSTBridgeClient"', self.host)
        self.assertNotIn('"VROSTBridgeHost"', self.client)
        self.assertEqual(client['LAUNCHER'], launcher_build.NAMES['client'])
        self.assertNotEqual(client['LAUNCHER'], host['LAUNCHER'])
        self.assertIn('InstallDir "$LOCALAPPDATA\\VROSTBridge\\Client\\${VERSION}"', self.client)
        self.assertIn('WriteRegStr HKCU "${APPKEY}" "DisplayName" "VROSTBridge Client"', self.client)
        self.assertIn('WriteRegStr HKCU "${APPKEY}" "Publisher" "JagerBombastic"', self.client)
        self.assertIn('WriteRegStr HKCU "${RUNKEY}" "${RUNVALUE}" \'"$INSTDIR\\${LAUNCHER}" --background\'', self.client)

    def test_the_start_menu_shares_the_folder_but_no_name(self):
        self.assertEqual(self.defines(self.client)['MENU'], self.defines(self.host)['MENU'])
        created, deleted = self.shortcuts(self.client, 'CreateShortcut'), self.shortcuts(self.client, 'Delete')
        self.assertEqual(created, {'VROSTBridge Client.lnk', 'Client open-source credits.lnk', 'Client startup logs.lnk',
                                   'Uninstall VROSTBridge Client.lnk'})
        self.assertEqual(created, deleted)
        self.assertEqual(created & (self.shortcuts(self.host, 'CreateShortcut') | self.shortcuts(self.host, 'Delete')), set())
        self.assertIn('RMDir "${MENU}"', self.client, 'only an empty folder is removed')
        self.assertNotIn('RMDir /r "${MENU}"', self.client)
        # The version folder goes; its parent only when empty; the state folder (settings, pairing, logs) stays.
        self.assertEqual(re.findall(r'^\s*RMDir .*$', self.client, re.M),
                         ['    RMDir "${MENU}"', '  RMDir /r "$INSTDIR"', '  RMDir "$LOCALAPPDATA\\VROSTBridge\\Client"'])

    def test_sign_in_startup_follows_the_hosts_rules(self):
        self.assertIn('Section /o "Start when I sign in" SEC_STARTUP', self.client)
        # A previous owned command is replaced (and kept on); a foreign one never.
        self.assertIn("${AndIf} $0 == '\"$PreviousInstall\\${LAUNCHER}\" --background'", self.client)
        self.assertIn("${If} $0 == '\"$PreviousInstall\\${LAUNCHER}\" --background'", self.client)
        self.assertIn("${OrIf} $0 == '\"$INSTDIR\\${LAUNCHER}\" --background'", self.client)
        self.assertIn('already contains a different command and was left unchanged', self.client)
        self.assertIn('${GetOptions} $1 "/STARTUP" $2', self.client)

    def test_the_steps_and_switches(self):
        used = set(re.findall(r' ([a-z]+-windows-client) --target "\$INSTDIR"', self.client))
        self.assertEqual(used, {'prepare-windows-client', 'commit-windows-client', 'finish-windows-client', 'uninstall-windows-client'})
        self.assertEqual(self.client.count('install_maintenance.py'), 2, 'the staged copy (install) and the installed one (uninstall)')
        with patch.object(m, 'prepare_windows_client') as prepare:
            m.main(['prepare-windows-client', '--target', 'x'])
        prepare.assert_called_once()
        self.assertIn('"$PLUGINSDIR\\payload\\${LAUNCHER}" --self-test', self.client)
        self.assertIn('${un.GetOptions} $1 "/REMOVEEYETRACKING" $2', self.client)
        self.assertIn('StrCpy $2 " --keep-eye-tracking"', self.client)
        self.assertIn('!define EYE "$LOCALAPPDATA\\VROSTBridge\\Baballonia"', self.client)
        self.assertIn('/SD IDYES IDYES eye_keep IDNO eye_remove', self.client, 'keep is the default answer')

    def test_the_script_is_plain_and_silent_safe(self):
        self.assertEqual(self.client.encode('utf-8'), self.client.encode('ascii', 'strict'))
        self.assertEqual(self.client.count('!macro'), 0)
        for line in re.findall(r'^\s*MessageBox .*$', self.client, re.M):
            self.assertIn('/SD ', line)
        # The version comes from the build (launcher/version.py), never from the script.
        self.assertEqual(re.findall(r'\d+\.\d+\.\d+', self.client), [])
        self.assertIn('VIProductVersion "${VERSION_NUMERIC}"', self.client)
        self.assertNotIn('vc_redist', self.client)


class LauncherSourceTests(unittest.TestCase):
    def test_the_host_strings_are_unchanged(self):
        source = read('packaging/windows-launcher.c')
        for text in ('"\\r\\n--- VROSTBridge Host native launcher %s ---\\r\\n"' % HOST_VERSION, 'L"\\\\host-native.log"',
                     'L"VROSTBridge Host"', 'L" --role host"', 'lstrcatW(state,L"\\\\VROSTBridge")',
                     '"\\r\\n--- VROSTBridge Client native launcher " VROST_VERSION " ---\\r\\n"', 'L"\\\\client-native.log"',
                     'L" --role client"', '#ifdef VROST_CLIENT'):
            self.assertIn(text, source)
        launcher_build.check_host_sources(HOST_VERSION)
        with self.assertRaisesRegex(RuntimeError, 'does not carry the host version'):
            launcher_build.check_host_sources('9.9.9.9')

    def test_the_client_resource(self):
        resource = launcher_build.client_resource('1.0.0.7')
        host = read('packaging/windows-icon.rc')
        for line in ('1 ICON "../launcher/brand/vrostbridge.ico"', 'FILEVERSION 1,0,0,7', 'PRODUCTVERSION 1,0,0,7',
                     'VALUE "FileDescription", "VROSTBridge Client"', 'VALUE "FileVersion", "1.0.0.7"',
                     'VALUE "OriginalFilename", "VROSTBridge Client.exe"', 'VALUE "ProductName", "VROSTBridge"',
                     'JagerBombastic'):
            self.assertIn(line, resource)
        self.assertNotIn('Host', resource)
        self.assertEqual(len(resource.splitlines()), len(host.splitlines()))
        self.assertEqual(launcher_build.numeric_version('1.0.0-rc1'), '1,0,0,0')
        self.assertEqual(launcher_build.release_version('client'), CLIENT_VERSION)
        self.assertEqual(launcher_build.NAME, 'VROSTBridge Host.exe')

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('clang', 'lld-link', 'llvm-rc')) and
                         os.environ.get('VROSTBRIDGE_WINDOWS_SDK') and Path(os.environ['VROSTBRIDGE_WINDOWS_SDK']).is_dir(),
                         'clang, lld-link, llvm-rc and VROSTBRIDGE_WINDOWS_SDK (an xwin-style SDK) are needed')
    def test_both_roles_build(self):
        sdk = Path(os.environ['VROSTBRIDGE_WINDOWS_SDK'])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            host = launcher_build.build_with_llvm(sdk, folder, 'host', timestamp=1)
            client = launcher_build.build_with_llvm(sdk, folder, 'client')
            (folder / 'again').mkdir()
            again = launcher_build.build_with_llvm(sdk, folder / 'again', 'client')
            self.assertEqual(client.read_bytes(), again.read_bytes(), 'the client launcher is reproducible')
            self.assertEqual((host.name, client.name), ('VROSTBridge Host.exe', 'VROSTBridge Client.exe'))
            for path, role, other in ((host, 'Host', 'Client'), (client, 'Client', 'Host')):
                data = path.read_bytes()
                machine, plus, subsystem, imports = client_build.pe_image(data)
                self.assertEqual((machine, plus, subsystem), (0x8664, True, 2))
                self.assertEqual(sorted(name.lower() for name in imports), ['kernel32.dll', 'shell32.dll', 'user32.dll'])
                version = HOST_VERSION if role == 'Host' else CLIENT_VERSION
                self.assertIn(('--- VROSTBridge %s native launcher %s ---' % (role, version)).encode(), data)
                for text in ('VROSTBridge %s could not start' % role, '\\%s-native.log' % role.lower(),
                             'bootstrap.py" --role %s' % role.lower(), 'VROSTBridge %s.exe' % role):
                    self.assertIn(text.encode('utf-16-le'), data)
                self.assertNotIn(('VROSTBridge ' + other).encode('utf-16-le'), data)
            client_build.check_launcher(client, CLIENT_VERSION)


class WindowsClientMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='VROSTBridge client Ü ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.base / 'Local'), 'XDG_STATE_HOME': str(self.base / 'state'),
                                              'XDG_DATA_HOME': str(self.base / 'data')})
        environment.start()
        self.addCleanup(environment.stop)
        stop = patch.object(m, 'stop_desktop')
        self.stop = stop.start()
        self.addCleanup(stop.stop)
        self.target = m.windows_client_directory() / CLIENT_VERSION

    def release(self, version, **extra):
        path = m.windows_client_directory() / version
        path.mkdir(parents=True)
        (path / 'PACKAGED.json').write_text(json.dumps(dict(m.windows_client_identity(version), **extra)))
        (path / 'VROSTBridge Client.exe').write_bytes(b'old launcher ' + version.encode())
        return path

    def staged(self, version=CLIENT_VERSION):
        staged = self.base / 'staged'
        (staged / 'launcher').mkdir(parents=True)
        (staged / 'PACKAGED.json').write_text(json.dumps(m.windows_client_identity(version)))
        (staged / 'VROSTBridge Client.exe').write_bytes(b'new launcher')
        (staged / 'launcher' / 'app.py').write_text('# new\n')
        client_build.manifest(staged)
        return staged

    def install(self, staged=None):
        staged = staged or self.staged()
        m.prepare_windows_client(self.target)
        m.commit_windows_client(self.target, staged)
        m.finish_windows_client(self.target)

    def test_an_update_leaves_only_the_new_version(self):
        old = self.release('1.0.0.5')
        unowned = m.windows_client_directory() / '1.0.0.4'
        unowned.mkdir(); (unowned / 'mine.txt').write_text('keep')
        settings = m.state_directory() / 'client-settings.json'
        settings.parent.mkdir(parents=True, exist_ok=True); settings.write_text('{"bitrate": 80}')
        self.install()
        self.stop.assert_called_with('client')
        self.assertFalse(old.exists())
        self.assertTrue(unowned.exists(), 'a folder without the client identity is never removed')
        self.assertTrue(m.owned_windows_client(self.target))
        self.assertEqual((self.target / 'VROSTBridge Client.exe').read_bytes(), b'new launcher')
        self.assertEqual(settings.read_text(), '{"bitrate": 80}')
        self.assertFalse((m.state_directory() / 'client-upgrade.json').exists())
        self.assertEqual(sorted(p.name for p in m.windows_client_directory().iterdir()), sorted(['1.0.0.4', CLIENT_VERSION]))

    def test_a_repair_replaces_the_files_and_rolls_back_a_failed_copy(self):
        target = self.release(CLIENT_VERSION)
        (target / 'obsolete.txt').write_text('old')
        staged = self.staged()
        m.prepare_windows_client(target)
        with patch.object(m.shutil, 'copytree', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                m.commit_windows_client(target, staged)
        self.assertEqual((target / 'obsolete.txt').read_text(), 'old')
        self.assertEqual(list(target.parent.glob('.repair-*')), [])
        self.install(staged)
        self.assertFalse((target / 'obsolete.txt').exists())
        self.assertEqual(list(target.parent.glob('.repair-*')), [])

    def test_only_owned_locations_and_verified_payloads(self):
        with self.assertRaisesRegex(RuntimeError, 'not an owned release location'):
            m.prepare_windows_client(m.windows_client_directory() / '1.0.0.5')
        with self.assertRaisesRegex(RuntimeError, 'not an owned release location'):
            m.prepare_windows_client(self.base / 'elsewhere' / CLIENT_VERSION)
        self.target.mkdir(parents=True); (self.target / 'mine.txt').write_text('not VROSTBridge')
        with self.assertRaisesRegex(RuntimeError, 'not a VROSTBridge installation'):
            m.prepare_windows_client(self.target)
        shutil.rmtree(self.target)
        m.prepare_windows_client(self.target)
        staged = self.staged()
        (staged / 'launcher' / 'app.py').write_text('# changed after the manifest\n')
        with self.assertRaisesRegex(RuntimeError, 'integrity'):
            m.commit_windows_client(self.target, staged)
        shutil.rmtree(staged)
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            m.commit_windows_client(self.target, self.staged('1.0.0.5'))
        self.assertFalse(self.target.exists())

    def test_ownership(self):
        release = self.release('1.0.0.5')
        self.assertTrue(m.owned_windows_client(release))
        self.assertEqual(m.windows_client_releases(), [release])
        for identity in ({'platform': 'linux'}, {'role': 'host'}, {'version': '1.0.0.4'}):
            (release / 'PACKAGED.json').write_text(json.dumps(dict(m.windows_client_identity('1.0.0.5'), **identity)))
            self.assertFalse(m.owned_windows_client(release), identity)
        # A later release with another ALVR pin, or a key added, still owns (and replaces) it.
        (release / 'PACKAGED.json').write_text(json.dumps(dict(m.windows_client_identity('1.0.0.5'), alvr_commit='0' * 40, build=2)))
        self.assertTrue(m.owned_windows_client(release))
        (release / 'PACKAGED.json').write_text(json.dumps(m.windows_client_identity('1.0.0.5')))
        self.assertFalse(m.owned_release(release, 'client'), 'the Linux client rules do not own it')
        link = m.windows_client_directory() / '1.0.0.3'
        link.symlink_to(release, target_is_directory=True)
        self.assertFalse(m.owned_windows_client(link))
        self.assertFalse(m.owned_windows_client(self.base / '1.0.0.5'))

    def uninstall(self, target, location, keep=False, firewall=None):
        firewall = firewall or {'ok': True, 'declined': False, 'message': 'Removed only the firewall rules created by the VROSTBridge client.'}
        removed = []
        from launcher import baballonia
        with patch.object(m, 'windows_reg_value', return_value=str(location)) as registry, \
                patch('launcher.client_network.remove_windows_rules', return_value=firewall) as rules, \
                patch.object(baballonia, 'remove_downloaded', create=True, side_effect=lambda log: removed.append(log) or True), \
                patch.object(m, 'restore_windows_power_mode') as self.power, patch('builtins.print') as output:
            m.uninstall_windows_client(target, keep_eye_tracking=keep)
        registry.assert_called_with(m.CLIENT_UNINSTALL_KEY, 'InstallLocation')
        return rules, removed, ' '.join(str(call.args[0]) for call in output.call_args_list)

    def test_uninstall_of_the_current_installation(self):
        target = self.release(CLIENT_VERSION)
        rules, removed, said = self.uninstall(target, target)
        self.stop.assert_called_once_with('client')
        rules.assert_called_once_with()
        self.assertFalse(hasattr(m, 'remove_windows_steam_entry'), 'Windows has no Add to Steam (Game Mode is Bazzite only)')
        self.power.assert_called_once_with()  # a crashed session's power mode is put back
        self.assertNotIn('Steam', said)
        self.assertEqual(len(removed), 1, 'Baballonia removed when chosen')
        self.assertIn('Settings, client identity and logs were retained', said)
        self.assertTrue(target.exists(), 'the uninstaller removes the files after this step')
        rules, removed, said = self.uninstall(target, target, keep=True)
        self.assertEqual(removed, [])
        self.assertIn('kept for the next installation', said)
        self.assertIn(str(m.state_directory() / 'Baballonia'), said)

    def test_uninstall_stops_when_the_firewall_rules_stay(self):
        target = self.release(CLIENT_VERSION)
        declined = {'ok': False, 'declined': True, 'message': 'Administrator permission was not given, so the client firewall rules were kept.'}
        with self.assertRaisesRegex(RuntimeError, 'administrator approval'):
            self.uninstall(target, target, firewall=declined)

    def test_a_stale_uninstaller_touches_only_its_own_folder(self):
        old = self.release('1.0.0.5')
        current = self.release(CLIENT_VERSION)
        rules, removed, said = self.uninstall(old, current)
        self.stop.assert_not_called(); rules.assert_not_called(); self.power.assert_not_called()
        self.assertEqual(removed, [])
        with self.assertRaisesRegex(RuntimeError, 'not owned'):
            self.uninstall(self.base, current)

    def test_power_mode_restore_never_stops_the_uninstall(self):
        from launcher import session_power
        with patch.object(session_power.SessionPower, 'recover', side_effect=RuntimeError('no power API')), \
                patch('builtins.print') as output:
            m.restore_windows_power_mode()
        self.assertIn('could not be checked (no power API)', output.call_args.args[0])
        with patch.object(session_power.SessionPower, 'recover') as recover:
            m.restore_windows_power_mode()
        recover.assert_called_once_with()

    def test_eye_tracking_removal_failure_is_reported_not_fatal(self):
        from launcher import baballonia
        with patch.object(baballonia, 'remove_downloaded', create=True, side_effect=OSError('in use')), patch('builtins.print') as output:
            m.remove_windows_eye_tracking()
        self.assertIn('left in place (in use)', output.call_args.args[0])
        with patch.object(baballonia, 'remove_downloaded', None, create=True), patch('builtins.print') as output:
            m.remove_windows_eye_tracking()
        self.assertIn('left in place', output.call_args.args[0])

    def test_the_script_finds_the_companion(self):
        # The installers run the file with the payload's embedded Python, which does not put the package root on its
        # path (-I: no script folder, no PYTHONPATH); the Windows client's steps import launcher.version and others.
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'launcher/install_maintenance.py'), 'prepare-windows-client',
                                 '--target', str(self.base / 'elsewhere' / CLIENT_VERSION)],
                                capture_output=True, text=True, cwd=str(self.base), timeout=120)
        self.assertEqual((result.returncode, result.stderr.strip()), (1, 'The client installation target is not an owned release location'))

    def test_the_command_line(self):
        with patch.object(m, 'uninstall_windows_client') as uninstall:
            m.main(['uninstall-windows-client', '--target', 'x', '--keep-eye-tracking'])
            m.main(['uninstall-windows-client', '--target', 'x'])
        self.assertEqual([call.kwargs['keep_eye_tracking'] for call in uninstall.call_args_list], [True, False])
        with self.assertRaisesRegex(RuntimeError, 'staged payload'):
            m.main(['commit-windows-client', '--target', 'x'])
        with patch.object(m, 'commit_windows_client') as commit:
            m.main(['commit-windows-client', '--target', 'x', '--staged-payload', 'y'])
        self.assertEqual(commit.call_args.args[1], Path('y').absolute())


if __name__ == '__main__':
    unittest.main()
