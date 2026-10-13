"""Fetch matching source without resetting or overwriting user changes."""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PIN='f5023f8e78fa14ea57fb1038548d7820289a8524'
OPENVR='0924064316de3effbcd1acf1e309182a2deb1c05'

def run(*args,cwd=None): subprocess.run(args,cwd=cwd,check=True)
def fetch():
    target=ROOT/'upstream'/'ALVR'
    if not target.exists():
        target.parent.mkdir(exist_ok=True)
        run('git','clone','https://github.com/alvr-org/ALVR.git',str(target))
        run('git','checkout','--detach',PIN,cwd=target)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
    if head!=PIN: raise RuntimeError('Existing ALVR checkout has a different revision; select a separate build folder')
    run('git','submodule','update','--init','--recursive',cwd=target)
    for patch in sorted((ROOT/'patches').glob('*.patch')):
        already=subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=target,capture_output=True)
        if already.returncode==0: continue
        run('git','apply','--check',str(patch),cwd=target)
        run('git','apply',str(patch),cwd=target)
    print('Matched ALVR source and VROSTBridge patches ready')
if __name__=='__main__':fetch()
