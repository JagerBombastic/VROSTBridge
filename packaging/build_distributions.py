"""Build offline NSIS host + per-user Bazzite installers from verified inputs.

--host-input is an extracted previously verified host payload with SHA256SUMS,
PACKAGED.json and HOST_PROVENANCE.txt. Reuse only its runtime/ and dist/host/.
The matching native driver overlay is required; it is verified against the
shipped patch set. No downloads or compilers are needed on either user's PC.
"""
import argparse,hashlib,json,os,re,shutil,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='1.0.0.12'
CLIENT_VERSION='1.0.0.12'
PRODUCT='VROSTBridge'
PIN='f5023f8e78fa14ea57fb1038548d7820289a8524'

def copy(src,dst):
    if src.is_dir():shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pdb','*.pyc'))
    else:dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
def archive(source,destination,prefix=''):
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6,strict_timestamps=False) as z:
        for path in sorted(source.rglob('*')):
            if path.is_file():z.write(path,prefix+path.relative_to(source).as_posix())
def marker(path,role):
    (path/'PACKAGED.json').write_text(json.dumps({'version':CLIENT_VERSION if role=='client' else VERSION,'role':role,'alvr_commit':PIN},indent=2))
def manifest(path):
    files=[f for f in sorted(path.rglob('*')) if f.is_file() and f.name!='SHA256SUMS']
    (path/'SHA256SUMS').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.relative_to(path).as_posix()+'\n' for f in files))
def verify_input(path):
    for line in (path/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        file=(path/name).resolve()
        if not file.is_relative_to(path) or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Host input integrity failure: '+name)
    if json.loads((path/'PACKAGED.json').read_text())['alvr_commit']!=PIN:raise RuntimeError('ALVR source pin mismatch')
def verify_native_overlay(path):
    path=path.resolve()
    metadata=json.loads((path/'vrostbridge-build.json').read_text())
    if metadata.get('alvr_commit')!=PIN or metadata.get('native_protocol')!='vrostbridge-native-1':
        raise RuntimeError('Native host overlay is not the matching VROSTBridge protocol build')
    patches={item.name:hashlib.sha256(item.read_bytes()).hexdigest() for item in sorted((ROOT/'patches').glob('*.patch'))}
    if metadata.get('source_patch_sha256')!=patches:
        raise RuntimeError('Native driver was not built from the complete current patch set')
    api_version=metadata.get('api_version')
    if not isinstance(api_version,str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?',api_version):
        raise RuntimeError('Native host API version is missing or invalid')
    files=metadata.get('files')
    if not isinstance(files,list) or not files or len(files)>100:raise RuntimeError('Invalid native host build file manifest')
    names=[]
    for entry in files:
        name=entry.get('name','')
        if not isinstance(name,str) or not name or '\\' in name or ':' in name:raise RuntimeError('Invalid overlay path')
        file=(path/name).resolve()
        if not file.is_relative_to(path) or not file.is_file() or name in names:raise RuntimeError('Unsafe or missing overlay file: '+name)
        if hashlib.sha256(file.read_bytes()).hexdigest()!=entry.get('sha256','').lower():raise RuntimeError('Native host overlay integrity failure: '+name)
        names.append(name)
    if 'bin/win64/driver_alvr_server.dll' not in names:raise RuntimeError('Native host driver is absent')
    if api_version.encode('ascii') not in (path/'bin/win64/driver_alvr_server.dll').read_bytes():
        raise RuntimeError('Native driver API version disagrees with its build manifest')
    return metadata,names

def prepare_host_profile(windows, metadata):
    """Never ship a retained dependency package's mutable user/session metadata."""
    native=windows/'dist/host'
    for pattern in ('session.json*','session_log.txt*','crash_log.txt*'):
        for path in native.glob(pattern):
            if path.is_file():path.unlink()
    document=json.loads((ROOT/'host/default-session.json').read_text())
    document['server_version']=metadata['api_version']
    (native/'session.json').write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8')

def desktop_quote(argument):
    escaped=''.join('\\'+c if c in '\\"`$' else c for c in argument)
    return '"'+escaped.replace('\\','\\\\')+'"'

def verify_native_client(client):
    """Refuse a stale native build. RC4.1 shipped a client whose own label still said rc4,
    because the binary predated the version change; the label must match this release."""
    for name,what in (('VROSTBridgeClient','native Linux client'),('VROSTBridgeRuntimeProbe','runtime probe')):
        if (PRODUCT+' '+CLIENT_VERSION+' '+what).encode() not in (client/name).read_bytes():
            raise SystemExit('dist/client/'+name+' is not the '+CLIENT_VERSION+' build: run cmake --build and cmake --install first.')
    for name in ('assets/convert.spv','assets/reaim.spv','assets/fused.spv','assets/fonts/Inter-Regular.ttf','assets/hud/logo-192.rgba','libvrosttext.so',
                 'vulkan/libVkLayer_vrostbridge_openvr.so','vulkan/VkLayer_vrostbridge_openvr.json'):
        if not (client/name).is_file():raise SystemExit('dist/client/'+name+' is missing: run cmake --install first.')

INSTALLER_DESKTOP='Install VROSTBridge.desktop'
CLIENT_FOLDER='VROSTBridge-Bazzite-Installer'
HOST_FOLDER='VROSTBridge-Host'
LAUNCHER='VROSTBridge Host.exe'
# host/control.py and launcher/app.py run the helpers by these names.
VR_HELPER,AUDIO_PROBE='VROSTBridgeVRHelper.exe','VROSTBridgeAudioProbe.exe'
CLIENT_README=(f'VROSTBridge Client {CLIENT_VERSION} for Bazzite.\n\n'
    'Extract this whole folder, then open Install VROSTBridge.desktop. No Python install, compiler, root access or '
    'base-OS changes are needed. SteamVR and system GPU drivers are prerequisites. Installing over an earlier '
    'VROSTBridge client keeps its settings, pairing, calibration and logs. Start with payload/docs/QUICK_START.md; '
    'payload/docs/HARDWARE_ACCEPTANCE.md lists the hardware it has been tested with and the checks still open.\n\n'
    'VROSTBridge is distributed under the licence in payload/LICENSE (MIT). Third-party components remain subject '
    'to their respective licences and notices (payload/THIRD_PARTY.md, payload/NOTICE).\n\n'
    'VROSTBridge is an independent open-source project and is not affiliated with or endorsed by Valve Corporation, '
    'Bigscreen, the ALVR project, Project Babble or other hardware or platform manufacturers. Third-party names and '
    'marks belong to their respective owners.\n')


def client_archive_name():
    return f'{CLIENT_FOLDER}-{CLIENT_VERSION}.zip'


def host_setup_name():
    return f'VROSTBridge-Host-Setup-{VERSION}.exe'


def host_portable_name():
    return f'VROSTBridge-Host-Portable-{VERSION}.zip'


def build_client(linux, frozen, out):
    verify_native_client(ROOT/'dist/client')
    if linux.exists():shutil.rmtree(linux)
    payload=linux/'payload';copy(frozen.resolve(),payload)
    for item in ROOT.joinpath('dist/client').iterdir():
        if item.name in ('launcher','host','Start-Client.sh','docs','licenses','PACKAGED.json','SHA256SUMS'):continue
        copy(item,payload/item.name)
    copy(ROOT/'host/default-session.json',payload/'host/default-session.json')
    for name in ('docs','licenses','config','LICENSE','NOTICE','THIRD_PARTY.md'):copy(ROOT/name,payload/name)
    for name in ('ffmpeg','go-bsb-cams','openvr','wayland'):copy(ROOT/'third_party'/name,payload/'third_party'/name)
    copy(ROOT/'launcher/brand',payload/'brand')
    copy(ROOT/'packaging/uninstall-bazzite.sh',payload/'uninstall.sh');os.chmod(payload/'uninstall.sh',0o755)
    marker(payload,'client');manifest(payload)
    copy(ROOT/'packaging/install-bazzite.sh',linux/'install.sh');os.chmod(linux/'install.sh',0o755)
    command='exec bash "$(dirname "$1")/install.sh"'
    (linux/INSTALLER_DESKTOP).write_text('[Desktop Entry]\nType=Application\nName=Install VROSTBridge\nComment=Install VROSTBridge Client for this user\nExec=sh -c '+desktop_quote(command)+' sh %k\nIcon=system-software-install\nTerminal=false\n')
    os.chmod(linux/INSTALLER_DESKTOP,0o755)
    (linux/'README.txt').write_text(CLIENT_README)
    archive(linux,out/client_archive_name(),CLIENT_FOLDER+'/')

def main():
    p=argparse.ArgumentParser()
    for name in ('work','output','host-input','native-host-overlay','zig','makensis'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--frozen',type=Path)
    p.add_argument('--host-only',action='store_true')
    p.add_argument('--nsisdir',type=Path)
    a=p.parse_args()
    if not a.host_only and a.frozen is None:p.error('--frozen is required unless --host-only is selected')
    work=a.work.resolve();out=a.output.resolve();host_input=a.host_input.resolve()
    verify_input(host_input);native_metadata,native_files=verify_native_overlay(a.native_host_overlay);work.mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    linux=work/'bazzite'/CLIENT_FOLDER;windows=work/'windows-package'
    for path in ([windows] if a.host_only else [linux,windows]):
        if path.exists():shutil.rmtree(path)
    if not a.host_only:
        build_client(linux,a.frozen,out)
    for name in ('runtime','dist/host'):copy(host_input/name,windows/name)
    copy(host_input/'HOST_PROVENANCE.txt',windows/'HOST_PROVENANCE.txt')
    for name in native_files:copy(a.native_host_overlay/name,windows/'dist/host'/name)
    copy(a.native_host_overlay/'vrostbridge-build.json',windows/'dist/host/vrostbridge-build.json')
    prepare_host_profile(windows,native_metadata)
    with (windows/'HOST_PROVENANCE.txt').open('a') as stream:
        stream.write('\nVROSTBridge '+VERSION+' replaces the native driver with a matching patched build.\n')
        stream.write('Native protocol: vrostbridge-native-1. See dist/host/vrostbridge-build.json for compiler, source patches and file hashes.\n')
        stream.write('Original Python runtime and other host dependencies retain the provenance above. Physical GPU/headset execution remains a separate validation step.\n')
    for name in ('launcher','host','docs','licenses','LICENSE','NOTICE','THIRD_PARTY.md'):copy(ROOT/name,windows/name)
    copy(ROOT/'third_party/openvr/LICENSE',windows/'third_party/openvr/LICENSE')
    copy(ROOT/'launcher/brand',windows/'brand')
    zig=a.zig.resolve()
    subprocess.run([str(zig),'cc','-x','c++','-target','x86_64-windows-gnu','-Os',
                    '-fno-exceptions','-fno-rtti','-municode','-I',str(ROOT/'third_party/openvr'),
                    str(ROOT/'host/vr_helper.cpp'),'-o',str(windows/'host'/VR_HELPER)],check=True)
    subprocess.run([str(zig),'cc','-x','c++','-Wno-nullability-completeness','-target','x86_64-windows-gnu','-Os','-fno-exceptions','-fno-rtti',
                    str(ROOT/'host/audio_probe.cpp'),'-o',str(windows/'host'/AUDIO_PROBE),'-lole32','-luuid'],check=True)
    from build_windows_launcher import build_with_zig
    build_with_zig(zig,windows)  # writes VROSTBridge Host.exe with its icon and version resource
    marker(windows,'host');manifest(windows)
    env=os.environ.copy()
    if a.nsisdir:env['NSISDIR']=str(a.nsisdir.resolve())
    exe=out/host_setup_name()
    subprocess.run([str(a.makensis.resolve()),'-V3','-DPAYLOAD='+str(windows),'-DOUTPUT='+str(exe),str(ROOT/'packaging/windows-installer.nsi')],env=env,check=True)
    # Portable folder is a second direct path to the same native launcher, useful for diagnosis.
    archive(windows,out/host_portable_name(),HOST_FOLDER+'/')
    products=([out/client_archive_name()] if not a.host_only else [])+[exe,out/host_portable_name()]
    (out/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(x.read_bytes()).hexdigest()+'  '+x.name+'\n' for x in products))
    print('\n'.join(str(x) for x in products))
if __name__=='__main__':main()
