"""Vendor crates.io packages listed in a Cargo.lock from static.crates.io.

For build hosts that can reach crate downloads but not the crates.io index. Each
archive is checked against the lock file's SHA-256 before extraction. The output is
a cargo "directory" source (same layout as `cargo vendor`); point cargo at it with

    [source.crates-io]
    replace-with = "vendored"
    [source.vendored]
    directory = "<output>"

and build with --locked. Git dependencies are still fetched by cargo.
Usage: python3 scripts/vendor_crates_from_lock.py <ALVR>/Cargo.lock <output>
"""
import concurrent.futures
import hashlib
import io
import json
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


def fetch(pkg, out):
    name, version, checksum = pkg["name"], pkg["version"], pkg["checksum"]
    dest = out / f"{name}-{version}"
    marker = dest / ".cargo-checksum.json"
    if marker.exists() and json.loads(marker.read_text())["package"] == checksum:
        return name, version, "cached"
    url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            break
        except Exception:
            if attempt == 3:
                raise
    digest = hashlib.sha256(data).hexdigest()
    if digest != checksum:
        raise RuntimeError(f"checksum mismatch for {name} {version}: {digest} != {checksum}")
    prefix = f"{name}-{version}/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = []
        for member in archive.getmembers():
            if not member.name.startswith(prefix) or ".." in Path(member.name).parts:
                raise RuntimeError(f"unexpected path {member.name} in {name} {version}")
            if member.issym() or member.islnk():
                continue
            members.append(member)
        archive.extractall(out, members=members, filter="data")
    marker.write_text(json.dumps({"files": {}, "package": checksum}))
    return name, version, "fetched"


def main():
    lock, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    packages = [p for p in tomllib.loads(lock.read_text())["package"] if p.get("source") == REGISTRY]
    missing = [p for p in packages if "checksum" not in p]
    if missing:
        raise RuntimeError(f"packages without checksums: {[p['name'] for p in missing]}")
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        for name, version, state in pool.map(lambda p: fetch(p, out), packages):
            counts[state] = counts.get(state, 0) + 1
    print(json.dumps({"registry_packages": len(packages), **counts}))


if __name__ == "__main__":
    main()
