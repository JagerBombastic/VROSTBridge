"""Build the VROSTBridge Client for Windows 10/11 x64: VROSTBridge-Client-Setup-<version>.exe (a per-user NSIS
installer, packaging/windows-client-installer.nsi) and VROSTBridge-Client-Portable-<version>.zip, from

  --native    the native client folder from the Windows client build (default dist/client-windows):
              VROSTBridgeClient.exe, VROSTBridgeRuntimeProbe.exe, vrostbridge_transport.dll, avcodec-60.dll,
              avutil-58.dll, swscale-7.dll, openvr_api.dll and assets/ (shaders, HUD, fonts); anything else in it
              (a Vulkan layer, its licenses/ or third_party/ notes) is carried too, build by-products are not;
  --python    the embedded CPython for Windows x64, copied byte for byte: the host payload's runtime/python
              (its files are then checked against that payload's SHA256SUMS and its provenance is recorded);
  --launcher  a prebuilt "VROSTBridge Client.exe" (packaging/build_windows_launcher.py --role client), or
  --sdk/--zig to build it here;
  --makensis/--nsisdir  NSIS 3.09 (makensis, and NSISDIR when it is not built in);
  --output    where the setup EXE and the portable ZIP are written; --work keeps the payload (work/payload).

The version is launcher/version.py's CLIENT_VERSION (core/version.hpp must agree); the native programs must carry
it, and avutil-58.dll the FFmpeg release whose source the package carries. The payload (the install folder %LOCALAPPDATA%\\VROSTBridge\\Client\\<version>) holds the launcher, the native
files, runtime/python, the companion (launcher/ without Game Mode's artwork, and host/'s Python modules and default
session, which the companion imports for both roles), docs/, licenses/, LICENSE, NOTICE, THIRD_PARTY.md, brand/,
third_party/ (FFmpeg n6.1.4's source with its licence, as the LGPL asks, and OpenVR's licence),
CLIENT_PROVENANCE.txt, PACKAGED.json and SHA256SUMS (checked by --self-test). No Microsoft C++ runtime installer:
the native programs link the C runtime statically; the build refuses one that imports a Visual C++ or MinGW runtime.

Every file gets the same time (SOURCE_DATE_EPOCH, else the last commit's, else 2026-01-01) and mode, the ZIP is
written in UTC and the launcher's PE timestamp is a content hash, so the same inputs give the same packages.
"""
import argparse
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
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
from build_distributions import PIN, archive, manifest  # noqa: E402
import build_windows_launcher  # noqa: E402

LAUNCHER = build_windows_launcher.NAMES['client']
FOLDER = 'VROSTBridge-Client'
NATIVE_FILES = ('VROSTBridgeClient.exe', 'VROSTBridgeRuntimeProbe.exe', 'vrostbridge_transport.dll', 'avcodec-60.dll',
                'avutil-58.dll', 'swscale-7.dll', 'openvr_api.dll')
NATIVE_ASSETS = ('assets/convert.spv', 'assets/reaim.spv', 'assets/fused.spv', 'assets/hud/logo-192.rgba',
                 'assets/fonts/Inter-Regular.ttf')
LABELLED = ('VROSTBridgeClient.exe', 'VROSTBridgeRuntimeProbe.exe')  # they print "VROSTBridge <version> ..."
FFMPEG_SOURCE = 'ffmpeg-n6.1.4-source.tar.xz'
FFMPEG_RELEASE = re.search(r'-n(\d+\.\d+\.\d+)-', FFMPEG_SOURCE).group(1)  # what av_version_info() says
# The companion's own names in a native build folder (as a CMake install leaves them): the payload's come from the
# source tree. licenses/ and third_party/ are merged instead.
COMPANION = {'launcher', 'host', 'docs', 'Start-Client.sh', 'README.md', 'START_HERE.html', 'PACKAGED.json', 'SHA256SUMS'}
MERGED = {'licenses', 'third_party'}
RESERVED = {'runtime', 'brand', 'LICENSE', 'NOTICE', 'THIRD_PARTY.md', 'CLIENT_PROVENANCE.txt', LAUNCHER, 'Uninstall.exe'}
BY_PRODUCTS = {'.pdb', '.lib', '.exp', '.ilk', '.obj', '.iobj', '.ipdb', '.map'}
JUNK = re.compile(r'(?:.*\.(?:pyc|pyo|orig|rej|swp|bak)|.*~|\.DS_Store|Thumbs\.db|desktop\.ini)$', re.I)
# Runtimes Windows 10/11 does not provide: the Visual C++ redistributable and MinGW's. The Universal CRT
# (ucrtbase, api-ms-win-crt-*) is part of Windows 10 and later.
REDISTRIBUTABLE = re.compile(r'(?:vcruntime|msvcp|msvcr\d|concrt|vccorlib|vcomp|libgcc|libstdc\+\+|libwinpthread|libc\+\+)', re.I)
DEFAULT_EPOCH = 1767225600  # 2026-01-01T00:00:00Z


def client_version():
    return build_windows_launcher.release_version('client')


def numeric_version(version):
    return build_windows_launcher.numeric_version(version).replace(',', '.')


def setup_name(version=None):
    return 'VROSTBridge-Client-Setup-%s.exe' % (version or client_version())


def portable_name(version=None):
    return 'VROSTBridge-Client-Portable-%s.zip' % (version or client_version())


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def pe_image(data):
    """(machine, PE32+, subsystem, imported and delay-loaded DLL names) of a Windows program or library."""
    if data[:2] != b'MZ' or len(data) < 0x40:
        raise ValueError('not a Windows program')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('not a Windows program')
    machine, sections = struct.unpack_from('<HH', data, pe + 4)
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from('<H', data, optional)[0] != 0x20B:
        return machine, False, None, []
    subsystem = struct.unpack_from('<H', data, optional + 68)[0]
    headers = range(optional + optional_size, optional + optional_size + 40 * sections, 40)

    def offset(rva):
        for header in headers:
            size, address, raw_size, raw = struct.unpack_from('<IIII', data, header + 8)
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw
        raise ValueError('damaged import table')

    def name(rva):
        start = offset(rva)
        return data[start:data.index(b'\0', start)].decode('ascii', 'replace')

    imports = []
    directories = optional + 112
    for index, size, field in ((1, 20, 12), (13, 32, 4)):  # import and delay-import directories
        rva = struct.unpack_from('<I', data, directories + 8 * index)[0]
        entry = offset(rva) if rva else None
        while entry is not None and struct.unpack_from('<I', data, entry + field)[0]:
            imports.append(name(struct.unpack_from('<I', data, entry + field)[0]))
            entry += size
    return machine, True, subsystem, imports


def native_files(native, version=None):
    """The native folder's files to ship, as (relative path, source), after checking the release's needs."""
    version = version or client_version()
    if not native.is_dir():
        raise SystemExit('The native client folder %s does not exist: build the Windows client first.' % native)
    missing = [name for name in NATIVE_FILES + NATIVE_ASSETS if not (native / name).is_file()]
    if missing:
        raise SystemExit('The native client folder lacks ' + ', '.join(missing) + '.')
    label = 'VROSTBridge ' + version + ' '
    for name in LABELLED:
        data = (native / name).read_bytes()
        if label.encode() not in data and label.encode('utf-16-le') not in data:
            raise SystemExit('%s is not the %s build (it does not say "%s"): rebuild the Windows client.' % (name, version, label.strip()))
    # The package carries FFmpeg's source as the LGPL asks: the DLLs must be built from that release.
    if FFMPEG_RELEASE.encode() not in (native / 'avutil-58.dll').read_bytes():
        raise SystemExit('avutil-58.dll does not say FFmpeg %s; the package carries %s as the libraries\' source, so '
                         'they must be built from it.' % (FFMPEG_RELEASE, FFMPEG_SOURCE))
    files, skipped = [], []
    for item in sorted(native.iterdir()):
        if item.name in COMPANION:
            skipped.append(item.name)
            continue
        if item.name in RESERVED or item.name.lower() in {name.lower() for name in RESERVED}:
            raise SystemExit('The native client folder has %s, which the package makes itself.' % item.name)
        for path in sorted([item] if item.is_file() or item.is_symlink() else item.rglob('*')):
            relative = path.relative_to(native).as_posix()
            if path.is_symlink():
                raise SystemExit('The native client folder has a link: ' + relative)
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if path.suffix.lower() in BY_PRODUCTS or JUNK.fullmatch(path.name):
                skipped.append(relative)
                continue
            files.append((relative, path))
    shipped = {Path(relative).name.lower() for relative, _ in files if '/' not in relative}
    for relative, path in files:
        if path.suffix.lower() not in ('.exe', '.dll'):
            continue
        try:
            machine, plus, _, imports = pe_image(path.read_bytes())
        except (ValueError, struct.error) as error:
            raise SystemExit('%s: %s' % (relative, error))
        if machine != 0x8664 or not plus:
            raise SystemExit(relative + ' is not a 64-bit Windows (x64) file.')
        needed = [dll for dll in imports if REDISTRIBUTABLE.match(dll) and dll.lower() not in shipped]
        if needed:
            raise SystemExit('%s needs %s, which Windows does not provide and the package does not carry: link the '
                             'C runtime statically (the client ships no Visual C++ redistributable).'
                             % (relative, ', '.join(needed)))
    return files, skipped


def python_runtime(python):
    """Check the embedded CPython and describe where it came from (lines for CLIENT_PROVENANCE.txt)."""
    python = python.resolve()
    paths = sorted(path for path in python.rglob('*') if path.is_file())
    configuration = [path for path in paths if path.parent == python and re.fullmatch(r'python3\d+\._pth', path.name)]
    if not (python / 'python.exe').is_file() or len(configuration) != 1:
        raise SystemExit('%s is not an embedded CPython for Windows (python.exe and python3XX._pth).' % python)
    if any('__pycache__' in path.parts for path in paths):
        raise SystemExit(str(python) + ' has __pycache__ folders: use the unmodified embedded package.')
    try:
        machine, plus, _, _ = pe_image((python / 'python.exe').read_bytes())
    except (ValueError, struct.error):
        machine, plus = None, False
    if machine != 0x8664 or not plus:
        raise SystemExit(str(python / 'python.exe') + ' is not the x64 build.')
    data = (python / 'python.exe').read_bytes()
    key = 'FileVersion\0'.encode('utf-16-le')
    start = data.find(key) + len(key)
    while data[start:start + 2] == b'\0\0':
        start += 2
    end = start
    while end < len(data) and data[end:end + 2] != b'\0\0':
        end += 2
    release = data[start:end].decode('utf-16-le', 'replace')
    if not re.fullmatch(r'3\.\d+\.\d+', release):
        raise SystemExit('Could not read the Python version of ' + str(python / 'python.exe'))
    lines = ['Embedded Python: CPython %s for Windows x64 (embeddable package), %d files in runtime/python.'
             % (release, len(paths))]
    host = python.parent.parent
    if python.parent.name == 'runtime' and (host / 'SHA256SUMS').is_file() and (host / 'PACKAGED.json').is_file():
        listed = {}
        for line in (host / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            digest, _, name = line.partition('  ')
            if name.startswith('runtime/python/'):
                listed[name[len('runtime/python/'):]] = digest
        found = {path.relative_to(python).as_posix(): path for path in paths}
        if set(found) != set(listed) or any(sha256(path) != listed[name] for name, path in found.items()):
            raise SystemExit('%s differs from what %s lists: use an unmodified verified host payload.' % (python, host / 'SHA256SUMS'))
        identity = json.loads((host / 'PACKAGED.json').read_text(encoding='utf-8'))
        if identity.get('role') != 'host' or identity.get('alvr_commit') != PIN:
            raise SystemExit(str(host) + ' is not a VROSTBridge host payload for the pinned ALVR revision.')
        source = re.search(r'^Python: (\S+)$', (host / 'HOST_PROVENANCE.txt').read_text(encoding='utf-8'), re.M) \
            if (host / 'HOST_PROVENANCE.txt').is_file() else None
        if source:
            lines.append('Source: ' + source.group(1))
        lines.append('runtime/python is byte for byte the runtime/python of the verified VROSTBridge Host %s payload '
                     '(every file checked against its SHA256SUMS; manifest sha256 %s).'
                     % (identity.get('version'), sha256(host / 'SHA256SUMS')))
    else:
        lines.append('Not taken from a VROSTBridge host payload; its origin was not recorded by this build.')
    return lines


def check_launcher(path, version):
    data = Path(path).read_bytes()
    try:
        machine, plus, subsystem, imports = pe_image(data)
    except (ValueError, struct.error):
        machine, plus, subsystem, imports = None, False, None, []
    if machine != 0x8664 or not plus or subsystem != 2:
        raise SystemExit(str(path) + ' is not a 64-bit Windows GUI program.')
    for text in ('VROSTBridge Client', LAUNCHER, version):
        if text.encode('utf-16-le') not in data:
            raise SystemExit('%s has no VROSTBridge Client %s version resource: build it with '
                             'packaging/build_windows_launcher.py --role client.' % (path, version))
    if ('--- VROSTBridge Client native launcher %s ---' % version).encode() not in data:
        raise SystemExit('%s is not the %s client launcher.' % (path, version))
    if sorted(name.lower() for name in imports) != ['kernel32.dll', 'shell32.dll', 'user32.dll']:
        raise SystemExit('%s imports %s; the launcher needs only kernel32, user32 and shell32.' % (path, imports))


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def copy_tree(source, destination, skip=lambda path: False):
    for path in sorted(source.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and not JUNK.fullmatch(path.name) and not skip(path):
            copy_file(path, destination / path.relative_to(source))


def merge_tree(source, destination, origin):
    """Add a native folder's notes (licenses/, third_party/); a name already there must have the same bytes."""
    for path in sorted(source.rglob('*')):
        if not path.is_file() or JUNK.fullmatch(path.name):
            continue
        target = destination / path.relative_to(source)
        if target.exists():
            if target.read_bytes() != path.read_bytes():
                raise SystemExit('%s/%s differs from the file of the same name in the package.' % (origin, path.relative_to(source.parent).as_posix()))
            continue
        copy_file(path, target)


def assemble(payload, native, python, launcher, version=None, launcher_note=None):
    """Make the install folder's contents (payload is replaced). Returns the shipped native names."""
    version = version or client_version()
    files, skipped = native_files(native, version)
    runtime = python_runtime(python)
    check_launcher(launcher, version)
    for name in ('Client-Firewall.ps1', 'BaseStations.ps1', 'desktop.html', 'bootstrap.py', 'app.py', 'install_maintenance.py'):
        if not (ROOT / 'launcher' / name).is_file():
            raise SystemExit('launcher/' + name + ' is missing from the source tree.')
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    copy_file(launcher, payload / LAUNCHER)
    for relative, source in files:
        if relative.split('/', 1)[0] not in MERGED:
            copy_file(source, payload / relative)
    for path in sorted(python.resolve().rglob('*')):  # byte for byte, every file
        if path.is_file():
            copy_file(path, payload / 'runtime' / 'python' / path.relative_to(python.resolve()))
    # Game Mode (its artwork) is the Linux client's (gamescope); nothing else reads game_ui_assets.
    copy_tree(ROOT / 'launcher', payload / 'launcher', skip=lambda path: 'game_ui_assets' in path.relative_to(ROOT / 'launcher').parts)
    # As the Bazzite client: host/'s Python (with the vendored websockets) and the default session, no host programs.
    copy_tree(ROOT / 'host', payload / 'host', skip=lambda path: not (path.suffix in ('.py', '.json') or path.name == 'VENDORED.md'))
    copy_tree(ROOT / 'docs', payload / 'docs')
    copy_tree(ROOT / 'licenses', payload / 'licenses')
    copy_tree(ROOT / 'launcher' / 'brand', payload / 'brand')
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY.md'):
        copy_file(ROOT / name, payload / name)
    # FFmpeg's libraries are LGPL 2.1+: their unmodified source and licence travel with them (as in the Bazzite package).
    for name in (FFMPEG_SOURCE, 'LICENSE.md'):
        copy_file(ROOT / 'third_party' / 'ffmpeg' / name, payload / 'third_party' / 'ffmpeg' / name)
    for path in sorted((ROOT / 'third_party' / 'ffmpeg').iterdir()):  # Windows build notes kept in the source tree
        if path.is_file() and 'windows' in path.name.lower():
            copy_file(path, payload / 'third_party' / 'ffmpeg' / path.name)
    copy_file(ROOT / 'third_party' / 'openvr' / 'LICENSE', payload / 'third_party' / 'openvr' / 'LICENSE')
    for name in sorted(MERGED):
        if (native / name).is_dir():
            merge_tree(native / name, payload / name, 'native ' + name)
    names = [relative for relative, _ in files]
    notes = sorted(path.relative_to(payload).as_posix() for path in (payload / 'third_party' / 'ffmpeg').iterdir()
                   if path.name not in (FFMPEG_SOURCE, 'LICENSE.md'))
    lines = ['VROSTBridge Client %s for Windows 10 and 11 (x64): what this package was built from.' % version, '']
    lines += runtime + ['No Microsoft Visual C++ runtime installer is included: the native programs import no Visual '
                        'C++ or MinGW runtime DLL (checked at build time; they link the C runtime statically), and '
                        'runtime/python carries the vcruntime DLLs of the Python package.', '']
    lines += [launcher_note or 'Native launcher: %s, supplied prebuilt.' % LAUNCHER,
              'Its sha256: ' + sha256(payload / LAUNCHER), '']
    shipped = [n for n in names if '/' not in n] + sorted({n.split('/')[0] + '/' for n in names
                                                          if '/' in n and n.split('/')[0] not in MERGED})
    lines += ['Native client (the Windows client build): ' + ', '.join(shipped) + '.']
    lines += ['FFmpeg: avcodec-60.dll, avutil-58.dll and swscale-7.dll are LGPL 2.1+ libraries from FFmpeg n6.1.4; '
              'its unmodified source is third_party/ffmpeg/%s (sha256 %s), with its licence (LICENSE.md, '
              'licenses/FFmpeg-LGPL-2.1.txt); avutil-58.dll says %s.' % (FFMPEG_SOURCE, sha256(payload / 'third_party/ffmpeg' / FFMPEG_SOURCE), FFMPEG_RELEASE)]
    lines += ['Its Windows build notes: ' + ', '.join(notes) + '.' if notes else
              'No Windows build notes for FFmpeg were supplied (third_party/ffmpeg/ in the native folder or the source).', '']
    lines += ['Companion: launcher/ (without Game Mode artwork), host/ (Python modules and default session), docs/, '
              'licenses/, brand/, LICENSE, NOTICE and THIRD_PARTY.md from the VROSTBridge source; ALVR pin %s.' % PIN]
    (payload / 'CLIENT_PROVENANCE.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (payload / 'PACKAGED.json').write_text(json.dumps({'version': version, 'role': 'client', 'platform': 'windows',
                                                       'alvr_commit': PIN}, indent=2), encoding='utf-8')
    manifest(payload)
    for item in skipped:
        print('not packaged from the native folder:', item)
    return names


def source_epoch():
    if os.environ.get('SOURCE_DATE_EPOCH', '').isdigit():
        return int(os.environ['SOURCE_DATE_EPOCH'])
    try:
        result = subprocess.run(['git', '-C', str(ROOT), 'log', '-1', '--format=%ct'], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return DEFAULT_EPOCH


def settle(payload, epoch):
    """One time and mode for every file, so the installer and the ZIP do not record the build machine's."""
    for path in sorted(payload.rglob('*'), reverse=True) + [payload]:
        path.chmod(0o755 if path.is_dir() else 0o644)
        os.utime(path, (epoch, epoch))


def package(payload, output, version, makensis, nsisdir=None):
    """The setup EXE and the portable ZIP from a finished payload."""
    environment = dict(os.environ)
    if nsisdir:
        environment['NSISDIR'] = str(Path(nsisdir).resolve())
    setup = output / setup_name(version)
    subprocess.run([str(makensis), '-V3', '-DPAYLOAD=' + str(payload), '-DOUTPUT=' + str(setup), '-DVERSION=' + version,
                    '-DVERSION_NUMERIC=' + numeric_version(version), str(ROOT / 'packaging/windows-client-installer.nsi')],
                   env=environment, check=True)
    portable = output / portable_name(version)
    zone = os.environ.get('TZ')
    os.environ['TZ'] = 'UTC'  # ZIP times are local times: write them in UTC
    if hasattr(time, 'tzset'):
        time.tzset()
    try:
        archive(payload, portable, FOLDER + '/')
    finally:
        if zone is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = zone
        if hasattr(time, 'tzset'):
            time.tzset()
    return setup, portable


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--native', type=Path, default=ROOT / 'dist' / 'client-windows')
    parser.add_argument('--python', type=Path, required=True, help='embedded CPython for Windows x64 (a host payload\'s runtime/python)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, help='keep the payload in WORK/payload (replaced)')
    parser.add_argument('--launcher', type=Path, help='a prebuilt "VROSTBridge Client.exe"')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sdk', type=Path, help='xwin-style Windows SDK for building the launcher with clang/lld-link/llvm-rc')
    group.add_argument('--zig', type=Path, help='Zig for building the launcher')
    parser.add_argument('--makensis', type=Path, default=Path(shutil.which('makensis') or 'makensis'))
    parser.add_argument('--nsisdir', type=Path, help='NSIS data folder (Include, Contrib, Stubs, Plugins) when makensis needs one')
    arguments = parser.parse_args()
    if not arguments.launcher and not (arguments.sdk or arguments.zig):
        parser.error('pass --launcher, or --sdk or --zig to build it')
    version = client_version()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        work = arguments.work.resolve() if arguments.work else Path(temporary)
        if arguments.launcher:
            launcher, note = arguments.launcher.resolve(), None
        else:
            built = work / 'launcher'
            built.mkdir(parents=True, exist_ok=True)
            launcher = (build_windows_launcher.build_with_llvm(arguments.sdk.resolve(), built, 'client') if arguments.sdk
                        else build_windows_launcher.build_with_zig(arguments.zig.resolve(), built, 'client'))
            note = ('Native launcher: %s, built from packaging/windows-launcher.c (sha256 %s) and packaging/windows-icon.rc '
                    '(sha256 %s) by packaging/build_windows_launcher.py --role client.'
                    % (LAUNCHER, sha256(ROOT / 'packaging/windows-launcher.c'), sha256(ROOT / 'packaging/windows-icon.rc')))
        payload = work / 'payload'
        assemble(payload, arguments.native.resolve(), arguments.python, launcher, version, note)
        epoch = source_epoch()
        settle(payload, epoch)
        products = package(payload, output, version, arguments.makensis, arguments.nsisdir)
    print('VROSTBridge Client %s for Windows (file times %s UTC)' % (version, time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(epoch))))
    for path in products:
        print(sha256(path) + '  ' + path.name + '  (%d bytes)' % path.stat().st_size)


if __name__ == '__main__':
    main()
