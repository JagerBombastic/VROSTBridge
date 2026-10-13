"""Companion-only host update from a previously verified host payload.

Use when the embedded Python runtime and helper EXEs are unchanged and the shared
companion Python, HTML/JSON assets, session profile code or documentation changed.
With --native-overlay, the patched ALVR driver is replaced by a rebuilt one whose
build manifest records the complete current patch set (checked by
build_distributions.verify_native_overlay); otherwise it is reused.

--launcher replaces the native launcher with a "VROSTBridge Host.exe" built by
packaging/build_windows_launcher.py (it carries the version in its resource, so
normally every release). The payload's brand/ folder is replaced with launcher/brand.

The input is the payload extracted from a released setup EXE (7-Zip extracts
$PLUGINSDIR/payload). Its manifest and ALVR pin are verified first; compiled files
are never replaced, except the launcher given with --launcher. Build the setup EXE
afterwards with packaging/windows-installer.nsi and makensis -DPAYLOAD=... -DOUTPUT=...
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packaging'))
from build_distributions import PIN, VERSION, manifest, marker, verify_input, verify_native_overlay  # noqa: E402

OVERLAY_SUFFIXES = {'.py', '.html', '.json', '.md', '.ps1', '.ttf', '.txt', '.png', '.jpg', '.svg', '.ico', '.cpp', '.hpp'}
LAUNCHER = 'VROSTBridge Host.exe'
# The upstream Windows host shipped a GPL-enabled FFmpeg 8.1 build, used only by
# ALVR's optional x264 CPU encoder (`gpl` feature). Since 1.0.0-rc4 the driver is
# built without it, so these DLLs and their GPL notice are removed with --drop-ffmpeg.
FFMPEG_DLLS = ('avcodec-62.dll', 'avdevice-62.dll', 'avfilter-11.dll', 'avformat-62.dll', 'avutil-60.dll',
               'swresample-6.dll', 'swscale-9.dll')
FFMPEG_NOTICE = 'FFmpeg.txt'


def drop_ffmpeg(output):
    """Remove the Windows FFmpeg runtime after checking the driver no longer imports it."""
    host = output/'dist'/'host'
    driver = (host/'bin/win64/driver_alvr_server.dll').read_bytes().lower()
    for name in FFMPEG_DLLS:
        if name.encode() in driver:
            raise RuntimeError('The host driver still imports ' + name + '; rebuild it without the gpl feature.')
    removed = []
    for path in [host/'bin/win64'/name for name in FFMPEG_DLLS] + [host/'licenses'/FFMPEG_NOTICE]:
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def overlay(source, destination, every_file=False):
    """Copy the source tree's files of the known kinds (every_file: all of them, for licences, whose texts are
    often named LICENSE-MIT or COPYING with no suffix) over the payload's."""
    changed = []
    for path in sorted(source.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or (path.suffix not in OVERLAY_SUFFIXES and not every_file):
            continue
        target = destination/path.relative_to(source)
        if not target.exists() or target.read_bytes() != path.read_bytes():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            changed.append(target)
    return changed


def prune(source, destination):
    """Remove files the source tree no longer has. Only for trees the assembler copies
    whole from the source (launcher/): RC4.2 removed the Game Mode font atlas, which
    an overlay alone would have left in the host payload."""
    removed = []
    for path in sorted(destination.rglob('*')):
        if path.is_file() and not (source/path.relative_to(destination)).is_file():
            path.unlink()
            removed.append(path)
    return removed


def replace_native(native, output):
    """Copy a verified rebuilt driver and its build manifest over the reused native host."""
    metadata, names = verify_native_overlay(native)
    host = output/'dist'/'host'
    changed = []
    for name in [*names, 'vrostbridge-build.json']:
        target = host/name
        if not target.parent.is_dir():
            raise RuntimeError('Native overlay path is not part of the previous host: ' + name)
        shutil.copy2(native/name, target)
        changed.append(target)
    return metadata, changed


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_launcher(launcher):
    """A 64-bit Windows GUI program carrying the VROSTBridge version resource."""
    data = Path(launcher).read_bytes()
    if data[:2] != b'MZ':
        raise RuntimeError('The launcher is not a Windows program: ' + str(launcher))
    offset = int.from_bytes(data[0x3c:0x40], 'little')
    if data[offset:offset+4] != b'PE\0\0' or int.from_bytes(data[offset+4:offset+6], 'little') != 0x8664:
        raise RuntimeError('The launcher is not a 64-bit Windows program: ' + str(launcher))
    if 'VROSTBridge Host'.encode('utf-16-le') not in data:
        raise RuntimeError('The launcher has no VROSTBridge version resource; build it with build_windows_launcher.py')


def replace_launcher(launcher, output):
    """Swap the native launcher (VROSTBridge Host.exe)."""
    check_launcher(launcher)
    (output/LAUNCHER).unlink(missing_ok=True)
    shutil.copy2(launcher, output/LAUNCHER)
    return [output/LAUNCHER]


def replace_brand(output):
    """brand/ (the installer's icon and the page images) is replaced whole, so no old artwork stays."""
    shutil.rmtree(output/'brand', ignore_errors=True)
    shutil.copytree(ROOT/'launcher'/'brand', output/'brand', ignore=shutil.ignore_patterns('__pycache__'))
    return sorted(path for path in (output/'brand').rglob('*') if path.is_file())


def repack(previous, output, native=None, without_ffmpeg=False, launcher=None):
    verify_input(previous)
    previous_identity = json.loads((previous/'PACKAGED.json').read_text())
    if previous_identity.get('role') != 'host' or previous_identity.get('alvr_commit') != PIN:
        raise RuntimeError('The input is not a host payload for the pinned ALVR revision')
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(previous, output)
    metadata, changed = replace_native(native, output) if native else (None, [])
    removed = drop_ffmpeg(output) if without_ffmpeg else []
    if launcher:
        changed += replace_launcher(launcher, output)
    elif not (output/LAUNCHER).is_file():
        raise RuntimeError('The previous host has no ' + LAUNCHER + '; pass --launcher with one')
    changed += replace_brand(output)
    for path in prune(ROOT/'launcher', output/'launcher'):
        print('removed', path.relative_to(output))
    changed += overlay(ROOT/'launcher', output/'launcher')
    changed += overlay(ROOT/'host', output/'host')
    # 1.0.0.7: every licence file, so each notice text licenses/rust/INDEX.json lists is in the host package too.
    changed += overlay(ROOT/'licenses', output/'licenses', every_file=True)
    for item in output.joinpath('docs').glob('*'):
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    shutil.copytree(ROOT/'docs', output/'docs', dirs_exist_ok=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY.md'):  # RC4.6: the licence and notices travel with the host
        shutil.copy2(ROOT/name, output/name)
        changed.append(output/name)
    kept = ['embedded runtime'] + ([] if launcher else ['launcher']) + ['helper executables']

    def listed(items):
        return ', '.join(items[:-1]) + ' and ' + items[-1] if len(items) > 1 else items[0]
    with (output/'HOST_PROVENANCE.txt').open('a', encoding='utf-8') as stream:
        if metadata:
            stream.write('\nVROSTBridge %s host update over host %s: native driver rebuilt from ALVR %s with patches %s '
                         '(%s); %s reused byte-for-byte from the verified previous payload.\n'
                         % (VERSION, previous_identity.get('version'), PIN,
                            ', '.join(sorted(metadata['source_patch_sha256'])), metadata.get('compiler'),
                            listed(kept + ['FFmpeg/OpenVR DLLs' if any((output/'dist/host/bin/win64'/name).exists()
                                                                        for name in FFMPEG_DLLS) else 'OpenVR/libvpl DLLs'])))
            if removed:
                stream.write('The driver is built without ALVR\'s optional gpl feature (x264 CPU encoder), so the '
                             'GPL FFmpeg runtime (' + ', '.join(FFMPEG_DLLS) + ') and its notice were removed. '
                             'Hardware encoding (AMF, NVENC, VPL) does not use FFmpeg.\n')
        else:
            stream.write('\nVROSTBridge %s companion-only update over host %s: %s reused byte-for-byte from the '
                         'verified previous payload.\n' % (VERSION, previous_identity.get('version'), listed(['native driver'] + kept)))
        if launcher:
            stream.write('The native launcher %s (sha256 %s) was built from packaging/windows-launcher.c (sha256 %s) '
                         'and packaging/windows-icon.rc by packaging/build_windows_launcher.py and replaces the '
                         'previous one.\n' % (LAUNCHER, sha256(output/LAUNCHER), sha256(ROOT/'packaging/windows-launcher.c')))
    marker(output, 'host')
    manifest(output)
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-overlay', type=Path,
                        help='Directory with bin/win64/driver_alvr_server.dll and vrostbridge-build.json')
    parser.add_argument('--drop-ffmpeg', action='store_true',
                        help='Remove the Windows FFmpeg DLLs (the native overlay must not import them)')
    parser.add_argument('--launcher', type=Path,
                        help='A new "VROSTBridge Host.exe" (packaging/build_windows_launcher.py) to replace the launcher')
    arguments = parser.parse_args()
    native = arguments.native_overlay.resolve() if arguments.native_overlay else None
    launcher = arguments.launcher.resolve() if arguments.launcher else None
    for path in repack(arguments.previous_payload.resolve(), arguments.output.resolve(), native,
                       arguments.drop_ffmpeg, launcher):
        print('updated', path.relative_to(arguments.output.resolve()))
    print('SHA256SUMS', hashlib.sha256((arguments.output/'SHA256SUMS').read_bytes()).hexdigest())
