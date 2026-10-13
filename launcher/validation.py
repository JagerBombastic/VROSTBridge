"""Read-only readiness checks. These never certify a physical VR session."""
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import sys


def host_loader_check(executable):
    """Ask a disposable Windows child to load DLLs, without starting SteamVR.

    This establishes DLL dependency availability only, never GPU/VR operation.
    A failed native initializer cannot crash the desktop launcher itself.
    """
    if sys.platform != 'win32':return {'ok':False,'detail':'Windows DLL loading has not been run on this operating system.'}
    directory=Path(executable).parent/'bin/win64'
    names=('libvpl.dll','openvr_api.dll','driver_alvr_server.dll')
    missing=[name for name in names if not (directory/name).is_file()]
    if missing:return {'ok':False,'detail':'Missing packaged runtime files: '+', '.join(missing)+'. Reinstall VROSTBridge Host.'}
    probe="""import ctypes, os, pathlib, sys
directory=pathlib.Path(sys.argv[1])
with os.add_dll_directory(str(directory)):
    for name in ('libvpl.dll','openvr_api.dll','driver_alvr_server.dll'):
        try: ctypes.WinDLL(str(directory/name), winmode=0x1100)
        except OSError as error:
            print(name+': '+str(error), flush=True)
            sys.exit(1)
print('Host DLL dependencies loaded; no headset or GPU session was tested.')
"""
    try:
        result=subprocess.run([sys.executable,'-I','-c',probe,str(directory)],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=20,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        detail=(result.stdout+'\n'+result.stderr).strip()
        if result.returncode:
            detail=(detail or 'Native loader exited with code '+str(result.returncode))+'. Repair the Microsoft Visual C++ x64 runtime using the VROSTBridge installer, then retry.'
        return {'ok':result.returncode==0,'detail':detail}
    except (OSError,subprocess.TimeoutExpired) as error:return {'ok':False,'detail':'Host loader check failed: '+str(error)}


WINDOWS_PROBE='VROSTBridgeRuntimeProbe.exe'
# Windows start failures that leave no output (NTSTATUS exit codes).
WINDOWS_EXIT={0xC0000135:'A DLL it needs is missing (0xC0000135). Repair or reinstall VROSTBridge Client; it may also need the Microsoft Visual C++ x64 runtime.',
              0xC000007B:'A DLL it loads is damaged or not 64-bit (0xC000007B). Repair or reinstall VROSTBridge Client.',
              0xC0000139:'A DLL it loads is the wrong version (0xC0000139). Repair or reinstall VROSTBridge Client.',
              0xC0000142:'A DLL it loads failed to start (0xC0000142). Restart Windows, then repair VROSTBridge Client if it persists.'}


def windows_os():
    """(ok, detail): Windows 10 or 11 on x64, as the Windows client needs."""
    version=platform.version()
    parts=version.split('.')
    major=int(parts[0]) if parts and parts[0].isdigit() else 0
    build=int(parts[2]) if len(parts)>2 and parts[2].isdigit() else 0
    machine=platform.machine()
    name='Windows 11' if major==10 and build>=22000 else 'Windows 10' if major==10 else (platform.system()+' '+platform.release()).strip()
    return platform.system()=='Windows' and major>=10 and machine.upper() in ('AMD64','X86_64'),name+' ('+version+', '+machine+')'


def windows_program_check(program):
    """(ok, detail): run a native program's --help without a window, as a loader check (no SteamVR, no GPU)."""
    try:
        result=subprocess.run([str(program),'--help'],capture_output=True,text=True,encoding='utf-8',errors='replace',
                              timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))
    except (OSError,subprocess.TimeoutExpired) as error:return False,str(error)[:300]
    detail=(result.stderr or result.stdout or '')[-1500:]
    if result.returncode and not detail.strip():
        code=result.returncode&0xFFFFFFFF
        detail=WINDOWS_EXIT.get(code,'It exited with code 0x%08X.'%code)
    return result.returncode==0,detail


def check_machine(role, executable):
    checks=[]
    def add(name, ok, detail):checks.append({'name':name,'result':'pass' if ok else 'needs-attention','detail':detail})
    # 1.0.0.7: the headset client also runs on Windows 10/11 (decided now, so tests can choose the platform).
    windows_client=role!='host' and sys.platform=='win32'
    if windows_client:
        add('Operating system',*windows_os())
        executable=Path(executable)
        if executable.suffix.lower()!='.exe':executable=executable.with_name(executable.name+'.exe')
    else:
        add('Operating system', platform.system()==('Windows' if role=='host' else 'Linux'), platform.system())
    add('Application installed', executable.is_file(), executable.name)
    probe=executable.with_name(WINDOWS_PROBE)
    if windows_client:add('Runtime probe installed',probe.is_file(),probe.name)
    if role=='host' and sys.platform=='win32' and executable.is_file():
        loader=host_loader_check(executable)
        add('Host DLL dependencies',loader['ok'],loader['detail'])
    path=(Path(os.environ.get('LOCALAPPDATA',''))/'openvr/openvrpaths.vrpath' if role=='host' or windows_client
          else Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'openvr/openvrpaths.vrpath')
    try:
        registry=json.loads(path.read_text(encoding='utf-8-sig'))
        runtimes=registry.get('runtime') or []
        add('SteamVR runtime',any(p and Path(p).is_dir() for p in runtimes),'SteamVR runtime registered')
        if role=='host':
            registered=any(p and Path(p).resolve()==executable.parent.resolve() for p in (registry.get('external_drivers') or []))
            add('Host driver registration',registered,'Matching driver directory must be registered')
    except (OSError,ValueError,TypeError):add('SteamVR runtime',False,'Open SteamVR once on this PC')
    if windows_client:
        # No /dev/dri on Windows; both native programs must start (DLLs present) without a window.
        if executable.is_file():add('Native runtime loader',*windows_program_check(executable))
        if probe.is_file():add('Runtime probe loader',*windows_program_check(probe))
    elif role=='client':
        add('Graphics render device',bool(list(Path('/dev/dri').glob('renderD*'))),'A render device must be available to this user')
        if executable.is_file():
            try:
                result=subprocess.run([str(executable),'--help'],capture_output=True,text=True,timeout=8)
                add('Native runtime loader',result.returncode==0,(result.stderr or result.stdout)[-1500:])
            except (OSError,subprocess.TimeoutExpired) as e:add('Native runtime loader',False,str(e))
    return {'schema':1,'role':role,'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'checks':checks,'physical_validation':'not-performed-by-readiness-check'}


class SessionEvidence:
    """Count observed states; never infer controller, eye or image correctness."""
    def __init__(self):
        self.started=time.monotonic();self.started_utc=time.time()
        self.finished=None;self.ended_utc=None
        self.streaming=0;self.recovery=0;self.ages=[];self.last_streaming=None
    def finish(self):
        if self.finished is None:self.finished=time.monotonic();self.ended_utc=time.time()
    def record(self, metrics):
        state=metrics.get('state')
        if state=='streaming':
            self.streaming+=1
            self.last_streaming={'captured_utc':time.time(),'metrics':dict(metrics)}
            try:age=float(metrics['frame_age_ms'])
            except (ValueError,KeyError,TypeError):return
            if 0<=age<10000:
                self.ages.append(age)
                self.ages=self.ages[-3600:]
        elif state=='recovery':self.recovery+=1
    def report(self):
        ages=sorted(self.ages)
        return {'elapsed_seconds':round((self.finished if self.finished is not None else time.monotonic())-self.started,1),
                'started_utc':self.started_utc,
                'ended_utc':self.ended_utc,
                'last_streaming_sample':self.last_streaming,
                'streaming_samples':self.streaming,'recovery_samples':self.recovery,
                'source_frame_age_p95_ms':ages[min(len(ages)-1,int(.95*(len(ages)-1)))] if ages else None,
                'measurement':'Once-per-second client samples; source frame age is not motion-to-photon latency',
                'physical_checks':'Not automatically verified: image correctness, tracking, inputs, haptics, audio, calibrated gaze'}
