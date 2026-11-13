"""Fetch matching source without resetting or overwriting user changes."""
import hashlib
import json
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PIN='f5023f8e78fa14ea57fb1038548d7820289a8524'
OPENVR='0924064316de3effbcd1acf1e309182a2deb1c05'

def run(*args,cwd=None): subprocess.run(args,cwd=cwd,check=True)
def patch_set():
    return [[patch.name,hashlib.sha256(patch.read_bytes()).hexdigest()] for patch in sorted((ROOT/'patches').glob('*.patch'))]
def fetch():
    target=ROOT/'upstream'/'ALVR'
    if not target.exists():
        target.parent.mkdir(exist_ok=True)
        run('git','clone','https://github.com/alvr-org/ALVR.git',str(target))
        run('git','checkout','--detach',PIN,cwd=target)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
    if head!=PIN: raise RuntimeError('Existing ALVR checkout has a different revision; select a separate build folder')
    run('git','submodule','update','--init','--recursive',cwd=target)
    # 1.0.0.15: the patch set applied last is recorded in the checkout's git folder, so a second build in the same
    # checkout goes ahead (later patches change lines of earlier ones, so a patch alone no longer reverses cleanly).
    marker=Path(subprocess.check_output(['git','rev-parse','--absolute-git-dir'],cwd=target,text=True).strip())/'vrostbridge-patches.json'
    wanted=patch_set()
    try:applied=json.loads(marker.read_text(encoding='utf-8'))
    except (OSError,ValueError):applied=None
    if applied==wanted:
        print('Matched ALVR source and VROSTBridge patches ready (already applied)');return
    if applied is not None:
        raise RuntimeError('This ALVR checkout carries another VROSTBridge patch set. Reset it (git checkout -f '+PIN[:8]+
                           ' and git clean -fd in upstream/ALVR) or select a separate build folder')
    for patch in sorted((ROOT/'patches').glob('*.patch')):
        already=subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=target,capture_output=True)
        if already.returncode==0: continue
        run('git','apply','--check',str(patch),cwd=target)
        run('git','apply',str(patch),cwd=target)
    marker.write_text(json.dumps(wanted),encoding='utf-8')
    print('Matched ALVR source and VROSTBridge patches ready')
if __name__=='__main__':fetch()
