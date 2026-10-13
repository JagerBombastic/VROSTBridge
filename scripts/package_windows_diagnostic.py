"""Package the already-built Windows client and reproducible source together."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
# The historical 0.1.0 Windows diagnostic (VROSTBridge.exe) is not the VROSTBridge host.
assert (root / "VROSTBridge.exe").is_file(), "Build the Windows executable first"
shutil.copy2(root / "third_party/openvr/openvr_api.dll", root / "openvr_api.dll")
files = []
for file in root.rglob("*"):
    if not file.is_file():
        continue
    rel = file.relative_to(root)
    if any(part.startswith("build") or part in (".git", "dist", "__pycache__") for part in rel.parts):
        continue
    if file.suffix in (".pdb", ".obj", ".ilk", ".log") or file.name == "PACKAGE_SHA256.json":
        continue
    files.append(file)
hashes = {str(f.relative_to(root)).replace("\\", "/"): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
(root / "PACKAGE_SHA256.json").write_text(json.dumps(hashes, indent=2) + "\n")
files.append(root / "PACKAGE_SHA256.json")
output = root.parent / "VROSTBridge_0.1.0_Windows_x64_with_source.zip"
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for file in sorted(files):
        archive.write(file, "VROSTBridge-0.1.0/" + file.relative_to(root).as_posix())
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    for required in ("VROSTBridge.exe", "openvr_api.dll", "START_HERE.html", "assets/actions.json", "assets/bindings_index.json", "CMakeLists.txt"):
        assert "VROSTBridge-0.1.0/" + required in archive.namelist()
print(f"{output}\n{len(files)} files; {output.stat().st_size:,} bytes; archive CRC checks passed")
