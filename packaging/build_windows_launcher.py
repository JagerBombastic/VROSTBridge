"""Build a VROSTBridge native launcher from packaging/windows-launcher.c (no C runtime, entry WinMainCRTStartup,
kernel32/user32/shell32 only), with its icon and version resource:

- host (the default): "VROSTBridge Host.exe" with packaging/windows-icon.rc. Its version is the one written in
  windows-launcher.c and windows-icon.rc; the build refuses them when they differ from launcher/version.py.
- client (--role client): "VROSTBridge Client.exe", the same source built with -DVROST_CLIENT and
  -DVROST_VERSION=<CLIENT_VERSION of launcher/version.py>, and windows-icon.rc made the client's (its name and
  version; the same icon). Its PE timestamp is a content hash (/Brepro), so the same source gives the same file.

Either toolchain gives the same program:
- Zig:  python3 packaging/build_windows_launcher.py --zig /path/to/zig --output DIR [--role client]
- LLVM: python3 packaging/build_windows_launcher.py --sdk /path/to/xwin-sdk --output DIR [--role client]
  with clang, lld-link and llvm-rc on PATH and an xwin-style Windows SDK (sdk/include/{um,shared,ucrt},
  sdk/lib/um/x86_64; crt/include). That is how the 1.0.0, 1.0.0.1, 1.0.0.2, 1.0.0.3, 1.0.0.4, 1.0.0.5, 1.0.0.6, 1.0.0.7, 1.0.0.8, 1.0.0.9, 1.0.0.10 and 1.0.0.11 launchers were built (zig was not available).
The host's PE timestamp is the link time; --timestamp N (LLVM) writes N instead, so the host launcher rebuilt
with an earlier release's timestamp can be compared with that release's launcher byte for byte.
"""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging'
NAMES = {'host': 'VROSTBridge Host.exe', 'client': 'VROSTBridge Client.exe'}
NAME = NAMES['host']


def release_version(role):
    """launcher/version.py's version for the role, which core/version.hpp must agree with."""
    key = 'CLIENT_VERSION' if role == 'client' else 'HOST_VERSION'
    found = re.search(r"^%s = '([^']+)'$" % key, (ROOT / 'launcher/version.py').read_text(encoding='utf-8'), re.M)
    header = re.search(r'#define VROSTBRIDGE_%s "([^"]+)"' % key, (ROOT / 'core/version.hpp').read_text(encoding='utf-8'))
    if not found or not header or found.group(1) != header.group(1):
        raise RuntimeError('launcher/version.py and core/version.hpp disagree on ' + key)
    return found.group(1)


def numeric_version(version):
    """The four numbers of a Windows version resource: 1.0.0.7 -> 1,0,0,7; 1.0.0-rc1 -> 1,0,0,0."""
    match = re.match(r'(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?', version)
    if not match:
        raise ValueError('Not a release version: ' + version)
    return ','.join(part or '0' for part in match.groups())


def check_host_sources(version):
    """The host's version lives in its source files; never build a launcher that names another release."""
    source = (PACKAGING / 'windows-launcher.c').read_text(encoding='utf-8')
    resource = (PACKAGING / 'windows-icon.rc').read_text(encoding='utf-8')
    expected = ('--- VROSTBridge Host native launcher %s ---' % version, 'FILEVERSION ' + numeric_version(version),
                'PRODUCTVERSION ' + numeric_version(version), '"FileVersion", "%s"' % version,
                '"ProductVersion", "%s"' % version)
    missing = [text for text in expected if text not in source + resource]
    if missing:
        raise RuntimeError('packaging/windows-launcher.c or windows-icon.rc does not carry the host version %s: %s'
                           % (version, '; '.join(missing)))


def client_resource(version):
    """packaging/windows-icon.rc with the client's name and version: the same icon, layout and copyright."""
    text = (PACKAGING / 'windows-icon.rc').read_text(encoding='utf-8')
    host = re.search(r'VALUE "FileVersion", "([^"]+)"', text).group(1)
    replacements = (
        ('The VROSTBridge Host launcher', 'The VROSTBridge Client launcher'),
        ('FILEVERSION ' + numeric_version(host), 'FILEVERSION ' + numeric_version(version)),
        ('PRODUCTVERSION ' + numeric_version(host), 'PRODUCTVERSION ' + numeric_version(version)),
        ('VALUE "FileDescription", "VROSTBridge Host"', 'VALUE "FileDescription", "VROSTBridge Client"'),
        ('VALUE "FileVersion", "%s"' % host, 'VALUE "FileVersion", "%s"' % version),
        ('VALUE "InternalName", "VROSTBridge Host"', 'VALUE "InternalName", "VROSTBridge Client"'),
        ('VALUE "OriginalFilename", "VROSTBridge Host.exe"', 'VALUE "OriginalFilename", "VROSTBridge Client.exe"'),
        ('VALUE "ProductVersion", "%s"' % host, 'VALUE "ProductVersion", "%s"' % version))
    for old, new in replacements:
        if text.count(old) != 1:
            raise RuntimeError('packaging/windows-icon.rc no longer has the expected line: ' + old)
        text = text.replace(old, new)
    if 'Host' in text:
        raise RuntimeError('The client resource would still name the host')
    return text


def role_inputs(role, version, folder):
    """The resource script, extra compiler definitions and the program name for a role."""
    if role == 'host':
        if version is not None and version != release_version('host'):
            raise ValueError('The host launcher carries the version written in its sources; build it without --version')
        check_host_sources(release_version('host'))
        return PACKAGING / 'windows-icon.rc', [], NAMES['host']
    version = version or release_version('client')
    if not re.fullmatch(r'[0-9A-Za-z.+-]{1,64}', version):
        raise ValueError('Invalid client version: ' + version)
    script = Path(folder) / 'windows-client-icon.rc'
    script.write_text(client_resource(version), encoding='utf-8')
    return script, ['-DVROST_CLIENT', '-DVROST_VERSION="%s"' % version], NAMES['client']


def build_with_zig(zig, output, role='host', version=None):
    with tempfile.TemporaryDirectory() as folder:
        script, defines, name = role_inputs(role, version, folder)
        resource = Path(folder) / 'windows-icon.res'
        subprocess.run([str(zig), 'rc', '/fo', str(resource), '--', str(script)], cwd=PACKAGING, check=True)
        subprocess.run([str(zig), 'cc', '-target', 'x86_64-windows-gnu', '-Os', '-fno-stack-protector', '-nostdlib',
                        '-I', str(Path(zig).parent / 'lib/libc/include/any-windows-any'), '-Wl,--subsystem,windows',
                        '-Wl,--entry,WinMainCRTStartup', *defines, str(PACKAGING / 'windows-launcher.c'), str(resource),
                        '-o', str(output / name), '-lkernel32', '-luser32', '-lshell32'], check=True)
    return output / name


def build_with_llvm(sdk, output, role='host', version=None, timestamp=None):
    sdk = Path(sdk)
    include = sdk / 'sdk' / 'include'
    with tempfile.TemporaryDirectory() as folder:
        script, defines, name = role_inputs(role, version, folder)
        folder = Path(folder)
        resource, program = folder / 'windows-icon.res', folder / 'windows-launcher.obj'
        subprocess.run(['llvm-rc', '/nologo', '/fo', str(resource), str(script)], cwd=PACKAGING, check=True)
        subprocess.run(['clang', '--target=x86_64-pc-windows-msvc', '-Os', '-fno-stack-protector', '-fno-builtin',
                        '-mno-stack-arg-probe', '-D_WIN32_WINNT=0x0A00', '-DWIN32_LEAN_AND_MEAN', *defines,
                        *('-isystem' + str(path) for path in (include / 'um', include / 'shared', include / 'ucrt',
                                                               sdk / 'crt' / 'include')),
                        '-c', str(PACKAGING / 'windows-launcher.c'), '-o', str(program)], check=True)
        stamp = ['/timestamp:%d' % timestamp] if timestamp is not None else (['/Brepro'] if role == 'client' else [])
        subprocess.run(['lld-link', '/nologo', '/subsystem:windows', '/entry:WinMainCRTStartup', '/nodefaultlib',
                        '/machine:x64', '/opt:ref', '/release', *stamp, str(program), str(resource),
                        '/libpath:' + str(sdk / 'sdk' / 'lib' / 'um' / 'x86_64'), 'kernel32.lib', 'user32.lib',
                        'shell32.lib', '/out:' + str(output / name)], check=True)
    return output / name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--role', choices=tuple(NAMES), default='host')
    parser.add_argument('--version', help='client only: the version to build (default: CLIENT_VERSION in launcher/version.py)')
    parser.add_argument('--timestamp', type=int, help='LLVM only: the PE timestamp to write (seconds since 1970)')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--zig', type=Path)
    group.add_argument('--sdk', type=Path)
    arguments = parser.parse_args()
    if arguments.version and arguments.role != 'client':
        parser.error('--version is for the client launcher; the host version is written in its sources')
    if arguments.zig and arguments.timestamp is not None:
        parser.error('--timestamp needs the LLVM build (--sdk)')
    arguments.output.mkdir(parents=True, exist_ok=True)
    if arguments.zig:
        program = build_with_zig(arguments.zig.resolve(), arguments.output.resolve(), arguments.role, arguments.version)
    else:
        program = build_with_llvm(arguments.sdk.resolve(), arguments.output.resolve(), arguments.role, arguments.version,
                                  arguments.timestamp)
    print(program)


if __name__ == '__main__':
    main()
