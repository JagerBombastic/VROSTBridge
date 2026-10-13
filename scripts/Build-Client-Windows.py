#!/usr/bin/env python3
"""Cross-build the native Windows client on Linux: VROSTBridgeClient.exe and VROSTBridgeRuntimeProbe.exe.

One command: makes the MSVC import libraries (FFmpeg's from its .def files, vulkan-1.lib from the
Vulkan 1.0-1.3 core commands the Windows loader exports), configures CMake with Ninja and
cmake/windows-clang-cl.cmake, builds, and installs into --output (default dist/client-windows):

    VROSTBridgeClient.exe, VROSTBridgeRuntimeProbe.exe, vrostbridge_transport.dll,
    avcodec-*.dll, avutil-*.dll, swscale-*.dll, openvr_api.dll,
    assets/ (actions, bindings, HUD logo, convert/reaim/fused .spv, fonts/)

It runs nothing on its own unless --test is given: then the Windows test programs run under Wine
(ctest with Wine as the emulator; the Vulkan ones on Mesa's lavapipe through winevulkan when it is
installed, else they skip). Finally the programs are checked for build-machine paths.

Inputs:
  --sdk-root    Microsoft CRT and Windows SDK in xwin's layout (crt/, sdk/), a licensed copy
  --llvm-bin    LLVM 19 or later: clang-cl, lld-link, llvm-lib, llvm-rc, llvm-mt
  --ffmpeg      FFmpeg 6.1 built for Windows as shared libraries (H.264, HEVC and AV1 decoders with
                D3D11VA, the Vulkan hardware context, swscale): include/, bin/*.dll, lib/*.def
  --transport   vrostbridge_transport.dll (client/transport built for x86_64-pc-windows-msvc); its
                import library vrostbridge_transport.dll.lib lies beside it
  --vulkan-headers  the folder holding vulkan/ and vk_video/ (Vulkan 1.3; default /usr/include)
Also needs CMake 3.24+, Ninja and glslangValidator.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = ("VROSTBridgeClient.exe", "VROSTBridgeRuntimeProbe.exe")


def run(command: list[str], **kwargs) -> None:
    print("+ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def vulkan_commands(header: Path) -> list[str]:
    """The Vulkan 1.0-1.3 core commands: the prototypes before the first extension in vulkan_core.h."""
    text = header.read_text(encoding="utf-8")
    start = text.index("#define VK_VERSION_1_0 1")
    end = text.index("#define VK_KHR_surface 1")
    names = sorted(set(re.findall(r"VKAPI_CALL (vk\w+)\(", text[start:end])))
    if len(names) < 200 or "vkCreateInstance" not in names or "vkCmdPipelineBarrier2" not in names:
        raise SystemExit(f"Unexpected Vulkan headers in {header.parent}: {len(names)} core commands")
    return names


def import_library(llvm_lib: Path, dll: str, names: list[str], output: Path) -> None:
    definition = output.with_suffix(".def")
    definition.write_text("LIBRARY " + dll + "\nEXPORTS\n" + "".join("    " + name + "\n" for name in names))
    run([llvm_lib, "/nologo", "/machine:x64", "/def:" + str(definition), "/out:" + str(output)])


def def_exports(path: Path) -> list[str]:
    names = []
    for line in path.read_text().splitlines():
        line = line.split(";")[0].strip()
        if not line or line.upper().startswith(("EXPORTS", "LIBRARY")):
            continue
        names.append(line.split()[0])
    return names


def build_paths(path: Path, needles: list[str]) -> list[str]:
    data = path.read_bytes()
    found = []
    for needle in needles:
        for encoded in (needle.encode(), needle.encode("utf-16-le")):
            if encoded in data:
                found.append(needle)
                break
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--llvm-bin", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--vulkan-headers", type=Path, default=Path("/usr/include"))
    parser.add_argument("--build", type=Path, default=ROOT / "build-windows", help="build folder")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "client-windows")
    parser.add_argument("--test", action="store_true", help="run the Windows test programs under Wine")
    parser.add_argument("--wine", default=shutil.which("wine64") or "/usr/lib/wine/wine64")
    parser.add_argument("--lavapipe", type=Path, help="Vulkan ICD for the tests under Wine (default: Mesa's lavapipe)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = parser.parse_args()

    sdk, llvm = args.sdk_root.resolve(), args.llvm_bin.resolve()
    ffmpeg, transport = args.ffmpeg.resolve(), args.transport.resolve()
    headers, build, output = args.vulkan_headers.resolve(), args.build.resolve(), args.output.resolve()
    for tool in ("clang-cl", "lld-link", "llvm-lib", "llvm-rc", "llvm-mt"):
        if not (llvm / tool).is_file():
            parser.error(f"--llvm-bin has no {tool}")
    for required in (sdk / "crt/include", sdk / "crt/lib/x86_64", sdk / "sdk/include/um", sdk / "sdk/lib/um/x86_64",
                     sdk / "sdk/lib/ucrt/x86_64", ffmpeg / "include/libavcodec", ffmpeg / "bin", ffmpeg / "lib",
                     headers / "vulkan", headers / "vk_video"):
        if not required.is_dir():
            parser.error(f"Missing input folder: {required}")
    implib = transport.with_name(transport.name + ".lib")
    if not transport.is_file() or not implib.is_file():
        parser.error(f"--transport needs {transport.name} and {implib.name}")
    for tool in ("cmake", "ninja", "glslangValidator"):
        if not shutil.which(tool):
            parser.error(f"{tool} is not installed")

    # 1. Import libraries.
    libraries = build / "import-libraries"
    libraries.mkdir(parents=True, exist_ok=True)
    for name in ("avcodec", "avutil", "swscale"):
        dlls = sorted((ffmpeg / "bin").glob(name + "-*.dll"))
        definitions = sorted((ffmpeg / "lib").glob(name + "-*.def"))
        if len(dlls) != 1 or len(definitions) != 1:
            parser.error(f"--ffmpeg needs exactly one bin/{name}-*.dll and lib/{name}-*.def")
        import_library(llvm / "llvm-lib", dlls[0].name, def_exports(definitions[0]), libraries / (name + ".lib"))
    import_library(llvm / "llvm-lib", "vulkan-1.dll", vulkan_commands(headers / "vulkan/vulkan_core.h"),
                   libraries / "vulkan-1.lib")

    # 2. Configure and build.
    configure = ["cmake", "-S", ROOT, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 "-DCMAKE_TOOLCHAIN_FILE=" + str(ROOT / "cmake/windows-clang-cl.cmake"),
                 "-DVROST_LLVM_BIN=" + str(llvm), "-DVROST_WINDOWS_SDK=" + str(sdk),
                 "-DVROST_FFMPEG_DIR=" + str(ffmpeg), "-DVROST_IMPORT_LIBRARY_DIR=" + str(libraries),
                 "-DVROST_TRANSPORT_LIBRARY=" + str(transport), "-DVROST_TRANSPORT_IMPORT_LIBRARY=" + str(implib),
                 "-DVROST_VULKAN_HEADERS=" + str(headers), "-DCMAKE_INSTALL_PREFIX=" + str(output)]
    wine = Path(args.wine)
    if args.test:
        if not wine.is_file():
            parser.error(f"--test needs Wine: {wine} not found (--wine)")
        environment = ["/usr/bin/env", "WINEPREFIX=" + str(build / "wineprefix"), "WINEDEBUG=-all",
                       "WINEDLLOVERRIDES=mscoree,mshtml="]
        icds = [args.lavapipe] if args.lavapipe else [Path("/usr/share/vulkan/icd.d/lvp_icd.x86_64.json"),
                                                      Path("/usr/share/vulkan/icd.d/lvp_icd.json")]
        icd = next((path for path in icds if path.is_file()), None)
        if icd:
            environment.append("VK_ICD_FILENAMES=" + str(icd))
        configure.append("-DCMAKE_CROSSCOMPILING_EMULATOR=" + ";".join(environment + [str(wine)]))
    run(configure)
    run(["cmake", "--build", build, "--parallel", str(max(1, args.jobs))])

    # 3. Tests under Wine (only when asked).
    if args.test:
        prefix = build / "wineprefix"
        if not (prefix / "system.reg").is_file():
            run(["/usr/bin/env", "WINEPREFIX=" + str(prefix), "WINEDEBUG=-all", "WINEDLLOVERRIDES=mscoree,mshtml=",
                 str(wine), "wineboot", "--init"])
        # Wine's Vulkan (winevulkan) needs its X11 driver: without a display, run the tests on a virtual one.
        ctest = ["ctest", "--test-dir", build, "--output-on-failure", "--timeout", "600"]
        if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            ctest = ["xvfb-run", "-a", "-s", "-screen 0 640x480x24"] + ctest
        run(ctest)

    # 4. Install.
    if output.exists():
        shutil.rmtree(output)
    run(["cmake", "--install", build])
    missing = [name for name in PROGRAMS if not (output / name).is_file()]
    if missing:
        raise SystemExit("Not installed: " + ", ".join(missing))

    # 5. No build-machine paths in the programs (third-party DLLs are reported, not changed here).
    # The input and build folders themselves (a bare "/tmp/" would also match FFmpeg's own temporary-file
    # template, "/tmp/%sXXXXXX", which is not a path of this machine).
    needles = sorted({str(ROOT), str(build), str(sdk), str(llvm), str(ffmpeg), str(transport.parent), str(Path.home())})
    problems = []
    for program in sorted(output.rglob("*")):
        if program.suffix.lower() not in (".exe", ".dll") or not program.is_file():
            continue
        found = build_paths(program, needles)
        if found:
            line = f"{program.relative_to(output)}: {', '.join(found)}"
            if program.name in PROGRAMS:
                problems.append(line)
            else:
                print("NOTE build path in a supplied library: " + line)
    if problems:
        raise SystemExit("Build-machine paths in the programs:\n  " + "\n  ".join(problems))
    readobj = llvm / "llvm-readobj"
    for name in PROGRAMS:
        if readobj.is_file():
            table = subprocess.run([readobj, "--coff-imports", output / name], capture_output=True, text=True,
                                   check=True).stdout
            loaded = re.findall(r"^Import \{\s*\n\s*Name: (\S+)", table, re.MULTILINE)
            delayed = re.findall(r"^DelayImport \{\s*\n\s*Name: (\S+)", table, re.MULTILINE)
            print(f"{name}: imports {', '.join(loaded)}" + (f"; delay-loaded {', '.join(delayed)}" if delayed else ""))
    total = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    print(f"Installed the Windows client in {output} ({total / 1048576:.1f} MiB)")


if __name__ == "__main__":
    main()
