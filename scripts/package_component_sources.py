"""Make the source split by part, for a release page: one archive each for the rendering PC (host), the Windows
headset PC (Windows client) and the Bazzite headset PC (Bazzite client).

    python3 scripts/package_component_sources.py [--output DIRECTORY]

Each archive holds what its part is built from: its own code, build scripts and installer, what every part shares
(the notices and licences, the documentation, the artwork, the companion and VROSTBridge's patches to ALVR) and a
SOURCE.md that says what is in it, how to build it and what was left out. The files come from the same list as the
public source archive (scripts/package_public_source.py: what git tracks plus new files it does not ignore), with the
same checks, and every file in that list must belong to a part or be named in WHOLE_ONLY, so a new file can't be
left out of every archive by accident.

The whole source, the repository, stays the reference: these archives are for reading or building one part. The
Python tests, the page test and the GitHub workflow check every part together, so they are in the whole source only;
the client archives keep the native tests their builds compile.
"""
import argparse
import functools
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from package_public_source import LIMIT, NAME, private_patterns, public_files  # noqa: E402

# Patterns are repository paths: `*` and `?` stay within one name, `**` crosses folders.
# Every part needs these: the notices, licences and documentation, the artwork and the scripts that make it, the
# companion (one Python program for every role, with the host modules it imports), VROSTBridge's patches to ALVR
# (the host's driver and both clients' transport are built from ALVR with them) and the scripts that fetch the pinned
# ALVR revision or vendor its crates.
SHARED = ['LICENSE', 'NOTICE', 'THIRD_PARTY.md', 'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
          'START_HERE.html', '.gitignore', 'rust-toolchain.toml', 'docs/**', 'licenses/**', 'brand/**', 'launcher/**',
          'host/*.py', 'host/default-session.json', 'host/vendor/**', 'patches/**', 'scripts/fetch_upstream.py',
          'scripts/vendor_crates_from_lock.py', 'scripts/build_brand_assets.py', 'scripts/build_fonts.py',
          'packaging/README.md', 'packaging/collect_rust_notices.py']
# The native client: one source for both headset PCs (the Windows sections are in client/platform/linux/), its tests
# (the host's hand-protocol test builds in it when the patched ALVR is present), shaders, fonts, action manifest and
# the third-party code it compiles or ships.
CLIENT = ['CMakeLists.txt', 'core/**', 'client/**', 'assets/**', 'host/chaperone_geometry.hpp', 'tests/*.cpp',
          'tests/*.hpp', 'tests/fixtures/*.h264', 'tests/make_openvr_stand_in.py', 'tests/transport_smoke.py',
          'tests/check_assets.py', 'scripts/generate_bindings.py', 'third_party/openvr/**', 'third_party/stb/**',
          'third_party/ffmpeg/**']
# Tests of the rendering PC's driver, which only it builds.
HOST_TESTS = ['tests/host_*.cpp']
# In the whole source only: the tests of every part together, the workflow, the release scripts and earlier tests.
WHOLE_ONLY = ['.github/**', 'tests/test_*.py', 'tests/test_*.js', 'tests/headset_fixture.py', 'tests/fixtures/*.json',
              'tests/native-hand-rust/**', 'tests/host_encoder_completion_win32.cpp', 'scripts/package.py',
              'scripts/package_public_source.py', 'scripts/package_component_sources.py',
              'scripts/package_windows_diagnostic.py', 'scripts/render_spec.py', 'scripts/render_reference.py']
LEFT_OUT_EVERYWHERE = ('the Python tests, the page test, the GitHub workflow and the release scripts, which work on '
                       'every part together')
PARTS = {
    'host': {
        'title': 'the rendering PC (VROSTBridge Host, Windows)',
        'include': SHARED + [
            'Build-Host-Windows.cmd', 'Start-Host.cmd', 'host/**', 'core/version.hpp', 'third_party/openvr/**',
            'scripts/Build-Host-Cross.py', 'scripts/Build-Host.ps1', 'scripts/cross/**', 'scripts/native_build_manifest.py',
            'scripts/host_module_tests.py', 'tests/host_color_tests.cpp', 'packaging/build_distributions.py',
            'packaging/repack_host_payload.py', 'packaging/INPUT_SHA256.json', 'packaging/windows-installer.nsi',
            'packaging/build_windows_launcher.py', 'packaging/windows-launcher.c', 'packaging/windows-icon.rc'],
        'exclude': ['third_party/openvr/*.dll', 'third_party/openvr/*.so'],
        'what': [
            'the companion (`launcher/`, `host/`): the tray program and the page on the rendering PC;',
            "the SteamVR driver's build: ALVR with VROSTBridge's patches, cross-built on Linux "
            '(`scripts/Build-Host-Cross.py`) or built on Windows (`Build-Host-Windows.cmd`), and the driver\'s own '
            'tests (`scripts/host_module_tests.py`, `tests/host_color_tests.cpp`);',
            'the helper programs (`host/vr_helper.cpp`, `host/audio_probe.cpp`), the launcher '
            '(`packaging/windows-launcher.c`) and the installer (`packaging/windows-installer.nsi`, '
            '`packaging/build_distributions.py`);'],
        'build': ('[docs/BUILDING.md](docs/BUILDING.md#windows-rendering-host) builds the driver, cross-built on Linux or '
                  'built on Windows. [packaging/README.md](packaging/README.md#both-packages-or-host-only) assembles the '
                  'setup and the portable ZIP; that step also takes a verified host payload (the embedded Python and '
                  'the other runtime parts), which is not source code and is not in this archive.'),
        'left_out': ('the headset PCs\' native client (`client/`, `core/` apart from its version header, the shaders, '
                     'FFmpeg and go-bsb-cams) with its build scripts and installers, and OpenVR\'s prebuilt libraries '
                     '(the helpers use its headers only)'),
    },
    'windows-client': {
        'title': 'the Windows headset PC (VROSTBridge Client for Windows)',
        'include': SHARED + CLIENT + [
            'Build-Windows.cmd', 'cmake/**', 'scripts/Build-Client-Windows.py', 'scripts/Build-Client-Transport-Windows.py',
            'scripts/Build-FFmpeg-Windows.sh', 'packaging/build_windows_client.py', 'packaging/windows-client-installer.nsi',
            'packaging/build_windows_launcher.py', 'packaging/windows-launcher.c', 'packaging/windows-icon.rc'],
        'exclude': HOST_TESTS + [
            'client/platform/linux/lease.cpp', 'client/platform/linux/lease_protocol/**',
            'client/platform/linux/openvr_texture_layer.c', 'client/platform/linux/VkLayer_*',
            'tests/lease_protocol_tests.cpp', 'tests/vulkan_layer_tests.cpp', 'third_party/openvr/*.so',
            'third_party/ffmpeg/BUILD.txt'],
        'what': [
            'the native client (`client/`, `core/`): decoding with Direct3D 11 (`client/platform/windows/`), the frame '
            'pass and presentation through SteamVR, shared with the Bazzite client (`client/platform/linux/`, with '
            "Windows sections), the Rust adapter to ALVR's client core (`client/transport/`) and the native tests "
            '(`tests/`);',
            'the companion (`launcher/` and the modules it uses from `host/`): the page and the tray program;',
            "FFmpeg's exact source (`third_party/ffmpeg/`), the cross-build scripts for it, the transport and the "
            'client (`scripts/Build-FFmpeg-Windows.sh`, `scripts/Build-Client-Transport-Windows.py`, '
            '`scripts/Build-Client-Windows.py`), the launcher and the installer (`packaging/build_windows_client.py`, '
            '`packaging/windows-client-installer.nsi`);'],
        'build': ('[docs/BUILDING.md](docs/BUILDING.md#windows-headset-client-1007) cross-builds it on Linux with '
                  'clang-cl and runs its tests under Wine. [packaging/README.md](packaging/README.md#windows-client-from-1007) '
                  'makes the setup and the portable ZIP; that step also takes an embedded Python for Windows.'),
        'left_out': ('the Bazzite-only parts (the Game Mode display lease helper, the Vulkan layer for the calibration '
                     'overlay, go-bsb-cams, the Bazzite build scripts and installer) and the rendering PC\'s driver '
                     'build, helpers and installer'),
    },
    'bazzite-client': {
        'title': 'the Bazzite headset PC (VROSTBridge Client for Bazzite)',
        'include': SHARED + CLIENT + [
            'Build-Client-Linux.sh', 'Build-Eye-Camera-Linux.sh', 'Start-Client.sh', 'Install-Client.desktop',
            'VROSTBridge-Client.desktop', 'config/**', 'third_party/go-bsb-cams/**', 'third_party/wayland/**',
            'scripts/Build-Client-Linux.sh', 'scripts/Build-FFmpeg-Linux.sh', 'packaging/build_client.py',
            'packaging/build_distributions.py', 'packaging/VROSTBridge-Client.spec', 'packaging/install-bazzite.sh',
            'packaging/uninstall-bazzite.sh'],
        'exclude': HOST_TESTS + ['client/platform/windows/**', 'tests/windows_process_tests.cpp',
                                 'third_party/openvr/*.dll', 'third_party/ffmpeg/BUILD-Windows.txt'],
        'what': [
            'the native client (`client/`, `core/`): VA-API decoding, the frame pass and presentation through SteamVR '
            "(`client/platform/linux/`), the Game Mode display lease helper, the Vulkan layer for Baballonia's "
            "calibration overlay, the Rust adapter to ALVR's client core (`client/transport/`) and the native tests "
            '(`tests/`);',
            'the companion (`launcher/` and the modules it uses from `host/`): the page and the Game Mode screen;',
            "FFmpeg's exact source (`third_party/ffmpeg/`), go-bsb-cams with VROSTBridge's changes (the Beyond 2e "
            'eye-camera helper, `third_party/go-bsb-cams/`), the build scripts and the installer '
            '(`packaging/install-bazzite.sh`, `packaging/build_client.py`);'],
        'build': ('[docs/BUILDING.md](docs/BUILDING.md#linux-native-client): `Build-Client-Linux.sh` builds and tests '
                  'the client, and `Start-Client.sh` runs it from this folder. '
                  '[packaging/README.md](packaging/README.md#client-only-update) makes the installer ZIP '
                  '(`packaging/build_client.py`, after freezing the companion with PyInstaller).'),
        'left_out': ('the Windows-only parts (`client/platform/windows/`, the Windows cross-build scripts, launcher and '
                     'installers) and the rendering PC\'s driver build, helpers and installer'),
    },
}


@functools.lru_cache(maxsize=None)
def pattern_regex(pattern):
    """A repository-path glob as a regular expression: `**` crosses folders, `*` and `?` stay within one name."""
    parts = []
    index = 0
    while index < len(pattern):
        if pattern.startswith('**', index):
            parts.append('.*')
            index += 2
        elif pattern[index] == '*':
            parts.append('[^/]*')
            index += 1
        elif pattern[index] == '?':
            parts.append('[^/]')
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile(''.join(parts) + r'\Z')


def matches(path, patterns):
    return any(pattern_regex(pattern).match(path) for pattern in patterns)


def relative_paths(files):
    return [path.relative_to(ROOT).as_posix() if isinstance(path, Path) else path for path in files]


def part_files(part, files=None):
    """The repository-relative paths of one part's archive, sorted."""
    rule = PARTS[part]
    chosen = [relative for relative in relative_paths(public_files() if files is None else files)
              if matches(relative, rule['include']) and not matches(relative, rule['exclude'])]
    return sorted(chosen)


def unassigned(files=None):
    """Files of the whole source that no part takes and WHOLE_ONLY doesn't name: each needs a decision."""
    relatives = relative_paths(public_files() if files is None else files)
    taken = set()
    for part in PARTS:
        taken.update(part_files(part, relatives))
    return sorted(relative for relative in relatives if relative not in taken and not matches(relative, WHOLE_ONLY))


def source_note(part, version, count):
    rule = PARTS[part]
    lines = [f'# VROSTBridge {version}: source for {rule["title"]}', '',
             'This archive holds the source this part is built from:', '']
    lines += ['- ' + item for item in rule['what']]
    lines += ['- what every part shares: the notices and licences (`LICENSE`, `NOTICE`, `THIRD_PARTY.md`, '
              '`licenses/`), the documentation (`docs/`), the artwork (`brand/`) and VROSTBridge\'s patches to ALVR '
              '(`patches/`), which the build applies to the pinned ALVR revision it fetches '
              '(`scripts/fetch_upstream.py`).', '',
              f'**Building:** {rule["build"]}', '',
              f'**Not in this archive:** {rule["left_out"]}; and {LEFT_OUT_EVERYWHERE}.', '',
              'The whole source, every part with every test, is the project\'s repository, which each release also '
              f'offers as *Source code*. This archive holds {count} of its files, unchanged.', '',
              'VROSTBridge is MIT-licensed (`LICENSE`); third-party components keep their own licences '
              '(`THIRD_PARTY.md`, and [licences in plain words](docs/LICENSING.md)).', '']
    return '\n'.join(lines)


def archive_name(part, version=CLIENT_VERSION):
    return f'{NAME}-{version}-{part}-source.zip'


def write_archive(part, output, files):
    version = CLIENT_VERSION
    prefix = f'{NAME}-{version}-{part}-source/'
    target = output / archive_name(part, version)
    note = source_note(part, version, len(files)).encode('utf-8')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=7, strict_timestamps=False) as archive:
        info = zipfile.ZipInfo(prefix + 'SOURCE.md', date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, note, compresslevel=7)
        for relative in files:
            path = ROOT / relative
            info = zipfile.ZipInfo.from_file(path, prefix + relative, strict_timestamps=False)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=7)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise SystemExit('Archive check failed: ' + target.name)
        if sorted(archive.namelist()) != sorted([prefix + 'SOURCE.md'] + [prefix + relative for relative in files]):
            raise SystemExit('Archive listing differs: ' + target.name)
        for relative in files:
            if hashlib.sha256(archive.read(prefix + relative)).digest() != hashlib.sha256((ROOT / relative).read_bytes()).digest():
                raise SystemExit('Archive content differs: ' + relative)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT.parent)
    arguments = parser.parse_args()
    if CLIENT_VERSION != HOST_VERSION:
        raise SystemExit('The source archives are made for a release of every part; versions differ.')
    files = relative_paths(public_files())
    missing = unassigned(files)
    if missing:
        raise SystemExit('In no part and not named in WHOLE_ONLY (choose where each belongs):\n' + '\n'.join(missing))
    patterns = private_patterns()
    if not patterns:
        print('VROSTBRIDGE_PRIVATE_PATTERNS is not set: only the size check runs. See docs/PUBLISHING.md.')
    problems = []
    for relative in sorted({relative for part in PARTS for relative in part_files(part, files)}):
        data = (ROOT / relative).read_bytes()
        if len(data) > LIMIT:
            problems.append(relative + ': larger than 50 MB')
        for pattern in patterns:
            if pattern.search(data):
                problems.append(relative + ': matches a private pattern (' + pattern.pattern.decode(errors='replace')[:40] + ')')
    if problems:
        raise SystemExit('Not packaged:\n' + '\n'.join(problems))
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for part in PARTS:
        chosen = part_files(part, files)
        target = write_archive(part, output, chosen)
        print(f'{target}: {len(chosen)} files and SOURCE.md; {target.stat().st_size} bytes; checked')


if __name__ == '__main__':
    main()
