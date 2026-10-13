"""Verify final archives, NSIS CRC, embedded icon, and native PE imports.
This does not execute Windows or validate the physical VR path.
"""
import hashlib,json,struct,sys,zipfile,zlib,subprocess,shutil
from pathlib import Path
BASE=Path('/workspace/scratch/647ea6e3ae16');OUT=BASE/'release-0.2.10'
import pefile
result={'version':'0.2.10','checks':{},'limits':['Windows execution and physical VR not tested']}
for path,prefix in [(OUT/'VROSTBridge-Host-Portable-0.2.10.zip','VROSTBridge-Host/'),(OUT/'VROSTBridge-Bazzite-Installer-0.2.10.zip','VROSTBridge-Bazzite-Installer/payload/')]:
 with zipfile.ZipFile(path) as z:
  assert z.testzip() is None
  count=0
  for line in z.read(prefix+'SHA256SUMS').decode().splitlines():
   digest,name=line.split('  ',1);assert hashlib.sha256(z.read(prefix+name)).hexdigest()==digest,name;count+=1
  result['checks'][path.name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'verified_files':count,'crc':'pass'}
exe=OUT/'VROSTBridge-Host-Setup-0.2.10.exe';raw=exe.read_bytes();expected=struct.unpack('<I',raw[-4:])[0];assert zlib.crc32(raw[512:-4])==expected,'NSIS executable CRC mismatch'
extract=BASE/'package030/verified-setup'
if extract.exists():shutil.rmtree(extract)
seven=BASE/'deps029/root/usr/lib/7zip/7z'
r=subprocess.run([str(seven),'x','-y','-o'+str(extract),str(exe)],capture_output=True,text=True);assert r.returncode==0,r.stdout+r.stderr
payload=extract/'$PLUGINSDIR/payload';assert payload.is_dir(),[str(p) for p in extract.iterdir()]
count=0
for line in (payload/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1);assert hashlib.sha256((payload/name).read_bytes()).hexdigest()==digest,name;count+=1
for rel in ['VROSTBridge Host.exe','host/VROSTBridgeVRHelper.exe','host/VROSTBridgeAudioProbe.exe']:
 pe=pefile.PE(str(payload/rel));assert pe.FILE_HEADER.Machine==0x8664
 imports=[r.dll.decode() for r in pe.DIRECTORY_ENTRY_IMPORT]
 if rel=='VROSTBridge Host.exe':
  assert pe.OPTIONAL_HEADER.Subsystem==2
  assert set(imports)=={'KERNEL32.dll','USER32.dll','SHELL32.dll'}
  assert {3,14}.issubset({r.id for r in pe.DIRECTORY_ENTRY_RESOURCE.entries})
 result['checks'][rel]={'machine':'AMD64','subsystem':pe.OPTIONAL_HEADER.Subsystem,'imports':imports,'sha256':hashlib.sha256((payload/rel).read_bytes()).hexdigest()}
result['checks'][exe.name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'nsis_crc':hex(expected),'verified_extracted_files':count}
path=BASE/'work030/VROSTBridge-0.2.10/docs/evidence/package-verification-0.2.10.json';path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
