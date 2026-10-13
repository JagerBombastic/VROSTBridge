"""Verify final archives, NSIS CRC, embedded icon, and native PE imports.
This does not execute Windows or validate the physical VR path.
"""
import hashlib,json,struct,sys,zipfile,zlib,subprocess,shutil
from pathlib import Path
BASE=Path('/workspace/scratch/647ea6e3ae16');OUT=BASE/'release-1.0.0-rc1'
import pefile
result={'version':'1.0.0-rc1','checks':{},'limits':['Windows execution and physical VR not tested']}
for path,prefix in [(OUT/'VROSTBridge-Host-Portable-1.0.0-rc1.zip','VROSTBridge-Host/'),(OUT/'VROSTBridge-Bazzite-Installer-1.0.0-rc1.zip','VROSTBridge-Bazzite-Installer/payload/')]:
 with zipfile.ZipFile(path) as z:
  assert z.testzip() is None
  count=0
  for line in z.read(prefix+'SHA256SUMS').decode().splitlines():
   digest,name=line.split('  ',1);assert hashlib.sha256(z.read(prefix+name)).hexdigest()==digest,name;count+=1
  result['checks'][path.name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'verified_files':count,'crc':'pass'}
exe=OUT/'VROSTBridge-Host-Setup-1.0.0-rc1.exe';raw=exe.read_bytes();expected=struct.unpack('<I',raw[-4:])[0];assert zlib.crc32(raw[512:-4])==expected,'NSIS executable CRC mismatch'
extract=BASE/'package036/verified-setup'
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
# Verify the new native driver and its imports against the exact delivered DLLs.
native=payload/'dist/host';driver=native/'bin/win64/driver_alvr_server.dll'
record=json.loads((native/'vrostbridge-build.json').read_text())
assert record['native_protocol']=='vrostbridge-native-1'
assert record['api_version']=='21.0.0-dev14'
assert json.loads((native/'session.json').read_text())['server_version']==record['api_version']
assert json.loads((native/'session.json').read_text())['client_connections']=={}
for entry in record['files']:
 assert hashlib.sha256((native/entry['name']).read_bytes()).hexdigest()==entry['sha256'].lower()
prior=BASE/'package035/windows-package/dist/host/bin/win64/driver_alvr_server.dll'
assert driver.read_bytes()!=prior.read_bytes(),'Old native driver was reused'
pe=pefile.PE(str(driver));assert pe.FILE_HEADER.Machine==0x8664
available={p.name.lower():p for p in native.rglob('*.dll')}
matched=[]
for imported in pe.DIRECTORY_ENTRY_IMPORT:
 dependency=available.get(imported.dll.decode().lower())
 if dependency:
  lib=pefile.PE(str(dependency));assert lib.FILE_HEADER.Machine==0x8664
  symbols=getattr(lib,'DIRECTORY_ENTRY_EXPORT',None)
  names={s.name for s in symbols.symbols} if symbols else set()
  ordinals={s.ordinal for s in symbols.symbols} if symbols else set()
  for symbol in imported.imports:
   assert symbol.name in names if symbol.name else symbol.ordinal in ordinals, (dependency.name,symbol.name,symbol.ordinal)
  matched.append(dependency.name)
result['checks']['native_driver']={'sha256':hashlib.sha256(driver.read_bytes()).hexdigest(),'new_binary':True,'machine':'AMD64','matched_dependency_exports':matched,'source_patch_sha256':record['source_patch_sha256'],'windows_execution':False}
result['checks'][exe.name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'nsis_crc':hex(expected),'verified_extracted_files':count}
path=BASE/'work036/VROSTBridge-1.0.0-rc1/docs/evidence/package-verification-1.0.0-rc1.json';path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
