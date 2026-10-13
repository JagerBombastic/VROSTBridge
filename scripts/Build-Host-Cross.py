"""Cross-build the patched Windows driver with a prepared Microsoft SDK/CRT.

Install Rust 1.97.1 plus x86_64-pc-windows-msvc std, LLVM/clang-cl 19 or later,
and prepare the pinned ALVR checkout with scripts/fetch_upstream.py first.
The SDK layout matches xwin: crt/include, crt/lib/x86_64, sdk/include/{um,
shared,ucrt,winrt}, sdk/lib/{um,ucrt}/x86_64. The caller supplies a licensed,
case-correct SDK. ALVR deps/windows must contain libvpl 2.15 headers/library.
The driver is built without ALVR's `gpl` feature, which on Windows only adds the
FFmpeg/x264 CPU encoder fallback (too slow for VR); AMD, NVIDIA and Intel hardware
encoders do not use FFmpeg. Pass --gpl to build the fallback; it then also needs
FFmpeg 8.1 shared import libraries/headers and Vulkan headers under deps/windows.
No existing binary is silently reused as a rebuilt native driver.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-msvc"

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alvr", type=Path, default=ROOT / "upstream" / "ALVR")
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--clang-cl", required=True)
    parser.add_argument("--llvm-lib", required=True)
    parser.add_argument("--lld-link", required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--gpl", action="store_true", help="Include the FFmpeg/x264 CPU encoder fallback")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs must be between 1 and 64")
    checkout, sdk = args.alvr.resolve(), args.sdk_root.resolve()
    includes = [sdk / d for d in ("crt/include", "sdk/include/ucrt", "sdk/include/um", "sdk/include/shared", "sdk/include/winrt")]
    libraries = [sdk / d for d in ("crt/lib/x86_64", "sdk/lib/um/x86_64", "sdk/lib/ucrt/x86_64")]
    required = [*includes, *libraries, checkout / "openvr/headers", checkout / "deps/windows/libvpl/alvr_build/lib"]
    if args.gpl:
        required += [checkout / "deps/windows/ffmpeg/lib", checkout / "deps/windows/vulkan-headers/include"]
    for directory in required:
        if not directory.is_dir():
            parser.error(f"Required cross-build dependency is missing: {directory}")
    # cc-rs accepts shell-quoted flags. Avoid ambiguous SDK paths on Windows.
    import shlex
    flags = " ".join(["--target=" + TARGET, *[f"/imsvc {shlex.quote(str(d))}" for d in includes]])
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
        "CARGO_ENCODED_RUSTFLAGS": "\x1f".join(["-C", "target-feature=+crt-static", *[flag for d in libraries for flag in ("-L", "native=" + str(d))]]),
    })
    # An explicit target directory makes the copied DLL unambiguous even when
    # the developer has a global Cargo target-dir configuration.
    target_dir = checkout / "target-vrostbridge-windows"
    features = ["--features", "gpl"] if args.gpl else []
    subprocess.run(["cargo", "build", "--release", "--locked", "-p", "alvr_server_openvr", "--target", TARGET,
                    *features, "--target-dir", str(target_dir)], cwd=checkout, env=env, check=True)
    source = target_dir / TARGET / "release/alvr_server_openvr.dll"
    if not source.is_file():
        raise RuntimeError("Cargo succeeded without producing the expected native driver")
    destination = args.overlay.resolve() / "bin/win64/driver_alvr_server.dll"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    compiler = subprocess.check_output([args.clang_cl, "--version"], text=True).splitlines()[0]
    rust = subprocess.check_output(["rustc", "--version"], text=True).strip()
    subprocess.run([sys.executable, str(ROOT / "scripts/native_build_manifest.py"), "--dist", str(args.overlay.resolve()),
                    "--alvr", str(checkout),
                    "--compiler", compiler + "; " + rust + "; Windows MSVC cross-build"], check=True)

if __name__ == "__main__":
    main()
