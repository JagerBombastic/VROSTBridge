"""Package reviewed source, native Linux runtime and Windows host build tooling (VROSTBridge maintainer archive)."""
from pathlib import Path
import hashlib,json,sys,zipfile,io
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from launcher.version import CLIENT_VERSION  # noqa: E402
required=['dist/client/VROSTBridgeClient','dist/client/libvrostbridge_transport.so','dist/client/lib/libavcodec.so.60','dist/client/lib/libavutil.so.58','dist/client/lib/libswscale.so.7','dist/client/assets/convert.spv','dist/client/assets/fused.spv','host/default-session.json','client/transport/Cargo.lock','scripts/Build-Host-Windows.cmd']
for name in required:
    if not (root/name).is_file():raise RuntimeError('Missing package component: '+name)
files=[]
for path in root.rglob('*'):
    rel=path.relative_to(root)
    if not path.is_file() or path.is_symlink():continue
    if any(part in ('upstream','target','__pycache__','.git') or part.startswith('build-') for part in rel.parts):continue
    if path.name=='PACKAGE_SHA256.json' or path.suffix in ('.pdb','.obj','.pyc','.log'):continue
    files.append(path)
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
(root/'PACKAGE_SHA256.json').write_text(json.dumps(manifest,indent=2)+'\n')
files.append(root/'PACKAGE_SHA256.json')
prefix='VROSTBridge-'+CLIENT_VERSION+'/'
output=root.parent/('VROSTBridge_'+CLIENT_VERSION+'_Host_Client_Source.zip')
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED,compresslevel=7,strict_timestamps=False) as archive:
    for path in sorted(files):archive.write(path,prefix+path.relative_to(root).as_posix())
output.write_bytes(buffer.getvalue())
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    for name,digest in manifest.items():assert hashlib.sha256(archive.read(prefix+name)).hexdigest()==digest
print(str(output))
print(str(len(files))+' files; '+str(output.stat().st_size)+' bytes; archive CRC and SHA-256 manifest verified')
