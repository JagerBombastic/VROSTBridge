"""Write native build provenance after a successful matching ALVR build."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = "f5023f8e78fa14ea57fb1038548d7820289a8524"
PROTOCOL = "21-dev14-vrostbridge-native-1"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--alvr", type=Path, default=ROOT / "upstream" / "ALVR")
    parser.add_argument("--include", action="append", default=[])
    args = parser.parse_args()
    base = args.dist.resolve()
    names = args.include or ["bin/win64/driver_alvr_server.dll"]
    if "bin/win64/driver_alvr_server.dll" not in names:
        parser.error("The built native host driver must be included.")
    driver = base / "bin/win64/driver_alvr_server.dll"
    if not driver.is_file() or b"vrostbridge-native-1" not in driver.read_bytes():
        parser.error("The host DLL does not contain the required native protocol marker (vrostbridge-native-1).")
    workspace = args.alvr / "Cargo.toml"
    api_version = tomllib.loads(workspace.read_text(encoding="utf-8"))["workspace"]["package"]["version"]
    if not isinstance(api_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", api_version):
        parser.error("The built ALVR workspace has an invalid API version.")
    if api_version.encode("ascii") not in driver.read_bytes():
        parser.error("The built driver does not contain its declared ALVR API version.")
    files = []
    for name in names:
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            parser.error(f"Missing or invalid native build artifact: {name}")
        files.append({"name": name, "sha256": sha256(path)})
    patches = {p.name: sha256(p) for p in sorted((ROOT / "patches").glob("*.patch"))}
    if len(patches) < 2:
        parser.error("The VROSTBridge native patches are missing.")
    metadata = {
        "schema": 1,
        "platform": "Windows",
        "alvr_commit": PIN,
        "native_protocol": "vrostbridge-native-1",
        "api_version": api_version,
        "api_version_source": "ALVR workspace.package.version / CARGO_PKG_VERSION",
        "alvr_workspace_sha256": sha256(workspace),
        "protocol": PROTOCOL,
        "build_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "compiler": args.compiler,
        "source_patch_sha256": patches,
        "physical_session_tested": False,
        "files": files,
    }
    (base / "vrostbridge-build.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Native build manifest saved: {base / 'vrostbridge-build.json'}")

if __name__ == "__main__":
    main()
