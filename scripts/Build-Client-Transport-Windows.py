"""Cross-build the client transport library for Windows with a prepared Microsoft SDK/CRT.

Builds client/transport for x86_64-pc-windows-msvc from Linux the same way
scripts/Build-Host-Cross.py builds the driver: vrostbridge_transport.dll, its
MSVC import library vrostbridge_transport.dll.lib and vrostbridge-defaults.exe,
all in <target-dir>/x86_64-pc-windows-msvc/release.
Install Rust 1.97.1 plus x86_64-pc-windows-msvc std, LLVM/clang-cl 19 or later,
and prepare the pinned ALVR checkout with scripts/fetch_upstream.py first.
The SDK layout matches xwin: crt/include, crt/lib/x86_64, sdk/include/{um,
shared,ucrt,winrt}, sdk/lib/{um,ucrt}/x86_64. The caller supplies a licensed,
case-correct SDK. The CRT is linked statically, so the DLL needs no Visual C++
runtime. Build-machine paths are written as /src, /cargo, /rustup and /target,
and the PE timestamp is a content hash (/Brepro), not the build time. The
finished DLL is checked for every function bridge.h declares, for imports
outside Windows and for leaked build paths.
The .pdb written beside the DLL is for local debugging only: it keeps build
paths and rustc's temporary .def path, so the DLL's build id (PE and debug
timestamps, PDB GUID) changes on every link. --no-pdb links without it, and the
DLL is then bit-for-bit reproducible from the same source folders.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-msvc"
# Runtimes that would have to ship beside the client; Windows provides everything else.
REDISTRIBUTABLE = re.compile(r"vcruntime|msvcp|ucrtbase|api-ms-win-crt-|concrt|vccorlib|libgcc|libstdc|libwinpthread", re.I)


def directory_sources(configs: list[Path]) -> list[Path]:
    """Vendored-crate folders named by Cargo configs, so their paths can be remapped."""
    folders = []
    for config in configs:
        if config.is_file():
            for source in tomllib.loads(config.read_text()).get("source", {}).values():
                if "directory" in source:
                    folders.append((config.parent.parent / source["directory"]).resolve())
    return folders


def pe_tables(data: bytes) -> tuple[set[str], list[str]]:
    """Exported names and imported (including delay-loaded) DLL names of a PE32+ image."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
        raise RuntimeError("Not a 64-bit PE image")
    sections, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]

    def offset(rva: int) -> int:
        for header in range(pe + 24 + optional, pe + 24 + optional + 40 * sections, 40):
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, header + 8)
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw
        raise RuntimeError(f"RVA {rva:#x} is outside the image")

    def name(rva: int) -> str:
        start = offset(rva)
        return data[start:data.index(b"\0", start)].decode()

    directories = pe + 24 + 112
    export_rva, import_rva = struct.unpack_from("<I4xI", data, directories)
    delay_rva = struct.unpack_from("<I", data, directories + 13 * 8)[0]
    exports: set[str] = set()
    if export_rva:
        count, names = struct.unpack_from("<I4xI", data, offset(export_rva) + 24)
        exports = {name(struct.unpack_from("<I", data, offset(names) + 4 * i)[0]) for i in range(count)}
    imports = []
    for rva, size, field in ((import_rva, 20, 12), (delay_rva, 32, 4)):
        entry = offset(rva) if rva else None
        while entry is not None and (dll := struct.unpack_from("<I", data, entry + field)[0]):
            imports.append(name(dll))
            entry += size
    return exports, imports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--alvr", type=Path, default=ROOT / "upstream" / "ALVR")
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--clang-cl", required=True)
    parser.add_argument("--llvm-lib", required=True)
    parser.add_argument("--lld-link", required=True)
    parser.add_argument("--target-dir", type=Path, default=ROOT / "build-transport-windows")
    parser.add_argument("--vendor-config", type=Path, help="Cargo config whose [source] tables replace crates.io with vendored crates")
    parser.add_argument("--offline", action="store_true", help="Pass --offline to Cargo")
    parser.add_argument("--no-pdb", action="store_true", help="Link without a PDB (/DEBUG:NONE) for a reproducible DLL")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs must be between 1 and 64")
    checkout, sdk, target_dir = args.alvr.resolve(), args.sdk_root.resolve(), args.target_dir.resolve()
    manifest, header = ROOT / "client/transport/Cargo.toml", ROOT / "client/transport/bridge.h"
    includes = [sdk / d for d in ("crt/include", "sdk/include/ucrt", "sdk/include/um", "sdk/include/shared", "sdk/include/winrt")]
    libraries = [sdk / d for d in ("crt/lib/x86_64", "sdk/lib/um/x86_64", "sdk/lib/ucrt/x86_64")]
    for directory in [*includes, *libraries, checkout / "alvr/client_core"]:
        if not directory.is_dir():
            parser.error(f"Required cross-build dependency is missing: {directory}")
    if args.vendor_config and not args.vendor_config.is_file():
        parser.error(f"Vendor config not found: {args.vendor_config}")

    # Cargo runs in the ALVR checkout so its exact Rust toolchain is selected, and it then also reads
    # the Cargo configs above that folder. Every vendored source they name is remapped like the rest.
    home = Path.home()
    cargo_home = Path(os.environ.get("CARGO_HOME", home / ".cargo")).resolve()
    rustup_home = Path(os.environ.get("RUSTUP_HOME", home / ".rustup")).resolve()
    configs = [args.vendor_config.resolve()] if args.vendor_config else []
    configs += [folder / ".cargo" / name for folder in (checkout, *checkout.parents) for name in ("config.toml", "config")]
    configs += [cargo_home / "config.toml", cargo_home / "config"]
    vendored = directory_sources(configs)
    # rustc applies the last matching prefix, so the more specific folders come later.
    remaps = [(ROOT, "/src"), (args.alvr.absolute(), "/src/upstream/ALVR"), (checkout, "/src/upstream/ALVR"),
              (cargo_home, "/cargo"), (rustup_home, "/rustup"), *[(folder, "/cargo/vendor") for folder in vendored],
              (target_dir, "/target")]

    # cc-rs accepts shell-quoted flags. Avoid ambiguous SDK paths on Windows. No crate in this graph
    # compiles C today; the compiler is configured anyway, exactly as for the driver.
    flags = " ".join(["--target=" + TARGET, *[f"/imsvc {shlex.quote(str(d))}" for d in includes]])
    rustflags = ["-C", "target-feature=+crt-static", *[flag for d in libraries for flag in ("-L", "native=" + str(d))],
                 # /Brepro: content-derived PE timestamp. LNK4099: the static CRT ships without its PDB.
                 "-C", "link-arg=/Brepro", "-C", "link-arg=/IGNORE:4099",
                 # Comes after rustc's own /DEBUG, so it wins.
                 *(["-C", "link-arg=/DEBUG:NONE"] if args.no_pdb else []),
                 *[f"--remap-path-prefix={source}={destination}" for source, destination in remaps]]
    env = dict(os.environ)
    suffix = TARGET.replace("-", "_")
    env.update({
        "CC_" + suffix: args.clang_cl,
        "CXX_" + suffix: args.clang_cl,
        "AR_" + suffix: args.llvm_lib,
        "CFLAGS_" + suffix: flags,
        "CXXFLAGS_" + suffix: flags + " /EHsc",
        "CARGO_TARGET_" + suffix.upper() + "_LINKER": args.lld_link,
        "CARGO_BUILD_JOBS": str(args.jobs),
        "CARGO_ENCODED_RUSTFLAGS": "\x1f".join(rustflags),
    })
    command = ["cargo", *(["--config", str(args.vendor_config.resolve())] if args.vendor_config else []),
               "build", "--release", "--locked", *(["--offline"] if args.offline else []), "--target", TARGET,
               "--manifest-path", str(manifest), "--target-dir", str(target_dir)]
    print("+ (cd " + shlex.quote(str(checkout)) + " && " + shlex.join(command) + ")", flush=True)
    subprocess.run(command, cwd=checkout, env=env, check=True)

    release = target_dir / TARGET / "release"
    dll, import_library = release / "vrostbridge_transport.dll", release / "vrostbridge_transport.dll.lib"
    defaults = release / "vrostbridge-defaults.exe"
    for path in (dll, import_library, defaults):
        if not path.is_file():
            raise RuntimeError(f"Cargo succeeded without producing {path}")
    declared = set(re.findall(r"\b(vrost_\w+)\s*\(", header.read_text()))
    build_paths = {str(p) for p in (ROOT, checkout, args.alvr.absolute(), cargo_home, rustup_home, target_dir, sdk, *vendored)}
    build_paths.add(str(home) + os.sep)
    for binary in (dll, defaults):
        data = binary.read_bytes()
        exports, imports = pe_tables(data)
        if runtimes := [name for name in imports if REDISTRIBUTABLE.search(name)]:
            raise RuntimeError(f"{binary.name} needs non-system DLLs: {', '.join(runtimes)}")
        if leaks := sorted(p for p in build_paths if p.encode() in data or p.encode("utf-16-le") in data):
            raise RuntimeError(f"{binary.name} contains build-machine paths: {', '.join(leaks)}")
        print(f"{binary}: {len(data)} bytes, SHA-256 {hashlib.sha256(data).hexdigest()}")
        print(f"  imports: {', '.join(sorted(set(imports), key=str.lower))}")
        if binary == dll:
            if missing := sorted(declared - exports):
                raise RuntimeError("bridge.h functions the DLL does not export: " + ", ".join(missing))
            print(f"  exports: {len(exports)}, including all {len(declared)} vrost_* functions of bridge.h")
    print(f"Import library: {import_library}")


if __name__ == "__main__":
    main()
