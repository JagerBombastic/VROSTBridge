# Building host and client from source

Current code baseline: **VROSTBridge 1.0.0.13, Windows host, Bazzite client and Windows client**. Documentation revision: 2026-10-15. End users normally install packages and do not need compilers; see [INSTALL_PACKAGES.md](INSTALL_PACKAGES.md).

## Source and pinned inputs

The archive includes VROSTBridge source, tests, build scripts, notices, selected source snapshots and the native Linux payload. It excludes the full ALVR checkout and build caches. Native builds fetch upstream dependencies over the Internet. Pins are recorded in [PROJECT_SPEC.md](PROJECT_SPEC.md), `scripts/fetch_upstream.py`, `rust-toolchain.toml` and `client/transport/Cargo.lock`.

ALVR is pinned to `f5023f8e78fa14ea57fb1038548d7820289a8524`; OpenVR to `0924064316de3effbcd1acf1e309182a2deb1c05`; Rust to 1.97.1. The fetch script initializes the matching submodule and applies all twenty-three files in `patches/` in order. It refuses to reset an existing checkout at another revision. Do not replace it with arbitrary current ALVR releases.

## Windows rendering host

Use Windows x64 with Visual Studio 2022 Build Tools (Desktop development with C++ and Windows SDK), Git, Python 3, Rust/rustup and CMake on PATH. ALVR dependency preparation also needs its platform SDK/tool packages; the standard script uses Chocolatey. Its package-manager step may require elevation. Install those tools in the development environment, not on an end user's machine.

From the project folder, run **scripts\Build-Host-Windows.cmd**. It discovers the Visual Studio x64 environment and invokes `scripts/Build-Host.ps1`. If dependencies have already been prepared, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build-Host.ps1 -DependenciesReady
```

That mode checks `unzip`, `clang` and `pkg-config`, then uses ALVR's CI-style dependency preparation. It is not an offline build guarantee. The script builds the patched ALVR Dashboard/driver to `dist/host/`, writes `dist/host/vrostbridge-build.json`, and builds the VR/audio helpers under `host/` (`VROSTBridgeVRHelper.exe`, `VROSTBridgeAudioProbe.exe`). Transcripts go to `build-evidence/`. After a successful source build, **scripts\Start-Host.cmd** launches the companion through your installed Python.

### Cross-building the driver on Linux (the release path of RC3 to RC4.1, 1.0.0.1, 1.0.0.3 to 1.0.0.6 and 1.0.0.9; RC4.2 to 1.0.0 reused the RC4.1 driver, 1.0.0.2 the 1.0.0.1 driver, 1.0.0.7 and 1.0.0.8 the 1.0.0.6 driver, and 1.0.0.10 to 1.0.0.13 the 1.0.0.9 driver)

`scripts/Build-Host-Cross.py` builds only the patched driver (`alvr_server_openvr`) for `x86_64-pc-windows-msvc` from a Linux workspace: Rust 1.97.1 with the Windows target, Clang/LLD 19 (`clang-cl`, `lld-link`, `llvm-lib`), a licensed Microsoft CRT/SDK in xwin layout (for example from `xwin splat`), and libvpl 2.15 built static under `deps/windows/libvpl/alvr_build` (`scripts/cross/clang-cl-msvc.cmake`). Release drivers are built **without** ALVR's `gpl` feature, so no FFmpeg is needed or shipped; pass `--gpl` only to include the x264 CPU encoder fallback (then FFmpeg 8.1 import libraries and Vulkan headers are required). The script writes `vrostbridge-build.json` with the compiler, Rust and patch hashes. `packaging/repack_host_payload.py --native-overlay … --drop-ffmpeg` places it over a verified previous host payload and removes the FFmpeg runtime after checking the driver does not import it. The Microsoft SDK/CRT are build inputs only and must not be redistributed or committed.

### The driver's own Rust modules on Linux (1.0.0.4; 1.0.0.5; 1.0.0.6)

The driver builds only for Windows, but its gaze foveation (`vrostbridge_foveation.rs`), per-session stream options (`vrostbridge_stream.rs`), pose-drop counting (`vrostbridge_pose.rs`) and still-controller check (`vrostbridge_pair.rs`, 1.0.0.6) are plain Rust with their own tests. `python3 scripts/host_module_tests.py --alvr upstream/ALVR [--vendor DIR]` builds a small crate around these four files of a patched checkout, with stand-ins for the few ALVR types they use, and runs those tests (`--vendor`: a `cargo vendor` folder with glam, parking_lot and serde_json, for an offline run).

From 1.0.0.9 three patches change ALVR crates that both sides share: packet repair in `alvr_sockets` (0021), the clients' audio player and the host's audio capture in `alvr_audio` (0022, 0023). Their tests run on Linux in the patched checkout: `cargo test --locked -p alvr_sockets -p alvr_audio`. The host's capture itself is Windows-only code and is checked by building the driver.

### The launcher, `VROSTBridge Host.exe`

`packaging/windows-launcher.c` (no C runtime; it starts the bundled Python with the companion and logs to `%LOCALAPPDATA%\VROSTBridge\host-native.log`) and `packaging/windows-icon.rc` (the app icon `launcher/brand/vrostbridge.ico` and the version resource) make the launcher. `python3 packaging/build_windows_launcher.py --output DIR` builds it with either `--zig PATH` or `--sdk XWIN_DIR` (clang, lld-link and llvm-rc with an xwin-layout Windows SDK; the 1.0.0 and 1.0.0.1 launchers were built this way). Both give a 64-bit GUI program importing only kernel32, user32 and shell32. From 1.0.0.7 the same source also builds the Windows client's launcher, `VROSTBridge Client.exe` (`--role client`, see [Windows headset client](#windows-headset-client-1007)).

The source archive does not include the complete packaged Windows host payload. `scripts\Build-Windows.cmd` builds and runs the contract tests on Windows (see [On Windows itself](#on-windows-itself)); it is not the host build. Use the host script above.

## Linux native client

Build in a development environment (for example an Ubuntu 24.04 Distrobox) rather than layering build tools into Bazzite's immutable base image. CMake requires at least 3.24. The C++17 build uses Vulkan, VAAPI, libdrm, Wayland server/client, SDL2 runtime, ALSA/PipeWire and libclang. An Ubuntu dependency example is:

```sh
sudo apt-get update
sudo apt-get install -y build-essential cmake git pkg-config patchelf python3   glslang-tools libvulkan-dev libva-dev libdrm-dev libwayland-dev libsdl2-dev   libasound2-dev libpipewire-0.3-dev libspa-0.2-dev libclang-dev
rustup toolchain install 1.97.1 --profile minimal
bash scripts/Build-Client-Linux.sh
```

Install rustup first if it is absent. The script fetches pinned ALVR, builds minimal shared FFmpeg n6.1.4 (H.264, HEVC and AV1 decoders with VA-API), builds the Rust adapter with its lockfile (with `--remap-path-prefix`, so the library carries no build-machine paths), regenerates the default host schema, builds the native client/lease helper/probe/shader, runs native CTest and installs to `dist/client/`, including `libvrosttext.so` (text and shapes for the Game Mode screen), the `convert.spv`, `reaim.spv` and `fused.spv` shaders (1.0.0.7 to 1.0.0.10 also `fused-uniform.spv`) and the fonts under `assets/fonts/`. It copies matching FFmpeg, SDL2 and Wayland runtime libraries. The conversion, re-aim and fused frame pass shader tests (`conversion_shader_contracts`, `reaim_shader_contracts`, `fused_shader_contracts`), from 1.0.0.7 the Windows client's plane upload (`planar_upload_contracts`) and from 1.0.0.11 the client's own frame path with SteamVR stood in for (`video_pass_contracts`: `VulkanVideo` with the frame pass's buffers, the lens area, the clears and the GPU time stamps; `tests/openvr_stand_in.hpp`, written by `tests/make_openvr_stand_in.py` from the OpenVR headers) run on any Vulkan device, normally Mesa's lavapipe in a container (`mesa-vulkan-drivers`), and are skipped when none exists. `software_decode_contracts` (1.0.0.7) decodes small H.264 files from `tests/fixtures/` on the processor as the Windows client's *CPU decoding* does and checks the planes it would upload. `status_card_contracts` renders the in-headset cards with the bundled fonts and writes sample images to the build directory. Do not substitute incompatible FFmpeg major versions or separate the native binary from its matching transport/shader/libraries.

Return to the Bazzite desktop and launch **scripts/Start-Client.sh** (or the *VROSTBridge Client* shortcut beside it). It uses Python 3 and the developer payload. Run outside the container and outside Proton so the client sees the physical SteamVR session, GPU, audio and devices. A clean build in a container is not evidence that physical presentation works.

The optional camera helper is separate:

```sh
bash scripts/Build-Eye-Camera-Linux.sh
```

It uses the included source/vendor tree with Go >=1.24.5, a C compiler, pkg-config and libusb development headers. Output goes to `dist/tools/go-bsb-cams`; build it before the client/install assembly when rebuilding that helper. Baballonia is not bundled. Its tests (1.0.0.5: the frame checks, the frame reader with lost packets and error bits, the `STATS` line, the MJPEG server) run with `CGO_ENABLED=1 go test -mod=vendor -vet=off -race .` in `third_party/go-bsb-cams` (`-vet=off` because of a vet complaint in upstream's unchanged code).

## Windows headset client (1.0.0.7)

The Windows client is cross-built on Linux, like the release driver, so no Windows build machine is needed, and its C++ tests run under Wine. It has not yet run on Windows hardware: a green build and test run here is not evidence that it streams on a real PC.

### Prerequisites

In a Linux development environment (an Ubuntu 24.04 container works):

- **LLVM 19 or later:** `clang-cl`, `lld-link`, `llvm-lib`, `llvm-rc` and `llvm-mt` in one folder (`--llvm-bin`). The launcher build also wants `clang`, `lld-link` and `llvm-rc` on `PATH`.
- **A Microsoft CRT and Windows SDK in xwin's layout:** `crt/include`, `crt/lib/x86_64`, `sdk/include/{ucrt,um,shared,winrt}` and `sdk/lib/{um,ucrt}/x86_64`, case-correct, for example from `xwin splat`. You obtain it yourself, under Microsoft's licence. It is a build input only: never commit or redistribute it.
- **mingw-w64 GCC with the win32 thread model** (`x86_64-w64-mingw32-gcc`; on Ubuntu `gcc-mingw-w64-x86-64-win32`), `nasm` and `make`, for FFmpeg.
- **Vulkan headers** 1.3.255 or later with `vulkan/` and `vk_video/` (Ubuntu 24.04's `libvulkan-dev` puts 1.3.275 in `/usr/include`), and **glslangValidator** (`glslang-tools`).
- **Rust 1.97.1** with the Windows target, `rustup target add x86_64-pc-windows-msvc --toolchain 1.97.1`, and the patched ALVR checkout (`python3 scripts/fetch_upstream.py`, as for the other builds).
- **CMake 3.24 or later** and **Ninja**.
- **Python 3.11 or later** to run the scripts.
- **NSIS 3.09** for the installer: `makensis`, with NSIS 3.09's `Include`, `Contrib`, `Stubs` and `Plugins` folders as `NSISDIR` when they are not built in (a Linux `makensis` built from the 3.09 source works).
- **A verified host payload** for its embedded Python (`runtime/python`, checked against the payload's `SHA256SUMS`; see [packaging/README.md](../packaging/README.md)).
- Optional, for the tests: **Wine** (64-bit; 9.0 was used) with **Mesa's lavapipe** Vulkan driver (`mesa-vulkan-drivers`), and `xvfb-run` when there is no display.

### Steps

From the source root, in this order (options in brackets show the defaults).

1. **FFmpeg for Windows:**

   ```sh
   bash scripts/Build-FFmpeg-Windows.sh [--output build-ffmpeg-windows/out] [--work build-ffmpeg-windows] \
     [--vulkan-headers /usr/include] [--jobs N]
   ```

   It checks the shipped, unmodified source (`third_party/ffmpeg/ffmpeg-n6.1.4-source.tar.xz`) against its SHA-256 and builds `avcodec-60.dll`, `avutil-58.dll` and `swscale-7.dll` with the arguments recorded in `third_party/ffmpeg/BUILD-Windows.txt`: the H.264, HEVC and AV1 decoders with D3D11VA, the Vulkan hardware context, Windows threads, no programs. It refuses a configuration that is not LGPL 2.1 or later. `--output` receives `bin/` (the DLLs), `lib/` (their `.def` files) and `include/`. The build uses the prefix `/ffmpeg`, relative paths and a copy of the Vulkan headers beside the source, and links without a time stamp, so the DLLs name no folder of the build machine (checked) and rebuild identically. libgcc is linked statically, so they import only `KERNEL32`, `msvcrt` and `bcrypt`. It prints their SHA-256.

2. **The transport DLL:**

   ```sh
   python3 scripts/Build-Client-Transport-Windows.py --sdk-root /path/to/xwin-sdk \
     --clang-cl /path/to/llvm/bin/clang-cl --llvm-lib /path/to/llvm/bin/llvm-lib --lld-link /path/to/llvm/bin/lld-link \
     [--target-dir build-transport-windows] [--no-pdb] [--vendor-config CARGO_CONFIG --offline] [--jobs N]
   ```

   It builds `client/transport` for `x86_64-pc-windows-msvc` from inside the ALVR checkout (so ALVR's pinned toolchain is used), the way `scripts/Build-Host-Cross.py` builds the driver: the C runtime linked statically (`+crt-static`), build paths written as `/src`, `/cargo`, `/rustup` and `/target`, and a content-derived PE time stamp (`/Brepro`). The result, in `<target-dir>/x86_64-pc-windows-msvc/release/`, is `vrostbridge_transport.dll`, its import library `vrostbridge_transport.dll.lib` and `vrostbridge-defaults.exe`. The script checks that the DLL exports every function `client/transport/bridge.h` declares, that neither program imports a Visual C++, UCRT or MinGW runtime DLL, and that neither contains a build-machine path. The `.pdb` written beside the DLL is for local debugging only: it holds build paths, and it gives the DLL a new build ID at every link. `--no-pdb` links without it, and the DLL is then the same bit for bit from the same source folders. `--vendor-config` names a Cargo config that replaces crates.io with vendored crates, for an offline build.

3. **The native client:**

   ```sh
   python3 scripts/Build-Client-Windows.py --sdk-root /path/to/xwin-sdk --llvm-bin /path/to/llvm/bin \
     --ffmpeg build-ffmpeg-windows/out \
     --transport build-transport-windows/x86_64-pc-windows-msvc/release/vrostbridge_transport.dll \
     [--vulkan-headers /usr/include] [--build build-windows] [--output dist/client-windows] [--test] [--jobs N]
   ```

   It makes the MSVC import libraries (FFmpeg's from its `.def` files, and `vulkan-1.lib` from the Vulkan 1.0–1.3 core commands in the headers), configures CMake with Ninja and the toolchain file `scripts/cross/windows-clang-cl.cmake` (clang-cl, lld-link, llvm-lib, llvm-rc and llvm-mt against the xwin SDK, the C runtime linked statically, `/Brepro`, and the source and build folders mapped out of `__FILE__`), builds, and installs into `--output`:
   - `VROSTBridgeClient.exe` and `VROSTBridgeRuntimeProbe.exe`, each with its version resource and manifest (UTF-8, Windows 10 and 11, no elevation);
   - beside them `vrostbridge_transport.dll`, `avcodec-60.dll`, `avutil-58.dll`, `swscale-7.dll` and `openvr_api.dll`;
   - `assets/`: the SteamVR action manifest and bindings, the headset logo, `convert.spv`, `reaim.spv` and `fused.spv`, and the fonts with their licences.

   `vulkan-1.dll` comes with the GPU driver and is loaded when first needed, so a PC without Vulkan gets the client's own message rather than Windows refusing to start it. With `--test` the C++ contract tests run under Wine before the install (`ctest` with Wine as the emulator, a Wine prefix of its own in the build folder): 27 programs in the 1.0.0.13 build, among them the fused pass, the plane upload, the client's own frame path with CPU-decoded frames through its upload (`video_pass_contracts`), CPU decoding with the FFmpeg DLLs just built, gaze over loopback UDP and the command pipe and console events. The Vulkan ones run on Mesa's lavapipe through winevulkan (skipped without it), on a virtual display through `xvfb-run` when there is none; `--wine` and `--lavapipe` choose another Wine or Vulkan driver file. Wine has no D3D11 video decoder, so the D3D11VA path is built but not run. Finally the script refuses programs that contain a build-machine path (a supplied DLL that does is only reported) and lists each program's imports. A clean rebuild gives the same programs.

4. **The launcher:**

   ```sh
   python3 packaging/build_windows_launcher.py --role client --sdk /path/to/xwin-sdk --output /tmp/launcher   # or --zig /path/to/zig
   ```

   `VROSTBridge Client.exe` is `packaging/windows-launcher.c` built with `-DVROST_CLIENT` and the client version of `launcher/version.py` (which `core/version.hpp` must match), with the host's icon and a version resource made the client's: no C runtime, only kernel32, user32 and shell32 imported, and a content-derived PE time stamp, so the same source gives the same file. `packaging/build_windows_client.py` can build it in the next step instead (`--sdk` or `--zig` there).

5. **The packages:**

   ```sh
   python3 packaging/build_windows_client.py --native dist/client-windows \
     --python /path/to/verified-host-payload/runtime/python \
     --launcher '/tmp/launcher/VROSTBridge Client.exe' \
     --makensis /path/to/makensis [--nsisdir /path/to/nsis-3.09] \
     --output /tmp/vrostbridge-release [--work /tmp/vrostbridge-client-package]
   ```

   It writes `VROSTBridge-Client-Setup-<version>.exe` (with `packaging/windows-client-installer.nsi`) and `VROSTBridge-Client-Portable-<version>.zip`, and prints their SHA-256. [packaging/README.md](../packaging/README.md) describes the payload, the installer and how its checksums join the release's `SHA256SUMS.txt`.

### What the steps check

- **No build-machine paths:** in the FFmpeg DLLs, the transport DLL and its tool, and the native programs.
- **No Visual C++ or MinGW runtime:** the transport script refuses one among its imports, and the package builder refuses any packaged program or DLL that imports a Visual C++ or MinGW runtime DLL the package does not carry. The Windows client ships no Microsoft redistributable.
- **Version labels:** the package builder refuses native programs that do not say `VROSTBridge <version>`, a launcher without the client's version resource and log line, FFmpeg DLLs that are not 6.1.4 (the release whose source the package carries), a missing native file, shader or asset, a file that is not x64, and an embedded Python that differs from what the host payload's `SHA256SUMS` lists.
- **Reproducibility:** content-derived time stamps (`/Brepro`, `--no-insert-timestamp`) and mapped-out folders in every program, the transport DLL with `--no-pdb`, and in the packages one time (`SOURCE_DATE_EPOCH`, else the last commit's) and mode for every file, with the ZIP written in UTC, so the same inputs give the same packages.

### On Windows itself

**scripts\Build-Windows.cmd** builds and runs the contract tests natively with Visual Studio 2022's CMake: it uses `cmake` and `ctest` from `PATH`, or else the ones Visual Studio installs (found with `vswhere`; install *Desktop development with C++* and its CMake tools), configures `build` for x64 with `-DVROST_BUILD_WINDOWS_CLIENT=OFF`, builds the Release configuration and runs `ctest`. It does not build the Windows client, which needs the inputs above, nor the early Windows diagnostic program (`VROSTBridge.exe`), whose CMake target is gone.

## Fonts and artwork

The fonts and brand artwork are built once and committed; rebuild them only when they change. Both scripts are build-time tools.

```sh
python3 scripts/build_fonts.py --google-fonts /path/to/google/fonts   # fontTools; Inter and Outfit (SIL OFL 1.1)
python3 scripts/build_brand_assets.py                                 # rsvg-convert (librsvg), numpy, Pillow
```

`build_fonts.py` writes static Inter/Outfit instances (for stb_truetype in the headset cards and the Game Mode screen) and variable subsets (for the pages) to `launcher/fonts/`. `build_brand_assets.py` builds every runtime image from the VROSTBridge logo in `brand/` (see [brand/README.md](../brand/README.md)): the icons, favicons, tray icons, Windows `.ico` and Linux icon theme in `launcher/brand/`, the Steam library artwork in `launcher/brand/steam/`, the headset cards' logo `client/assets/hud/logo-192.rgba` and the Game Mode background. The 16 to 32 px icons come from `brand/tools/refine_small_icons.py`. The logo itself is never redrawn by hand; `brand/tools/trace_wordmark.py` (Potrace) is only needed to trace the wordmark again, and `brand/tools/set_bridge.py` (fontTools, Pillow with raqm, and the Montserrat file it names) only to set *Bridge* of the headers' one-line wordmark again (1.0.0.3; the outlines are committed in `brand/assets/svg/vrostbridge-bridge.svg`). `third_party/stb/` holds the vendored `stb_truetype.h` and `stb_image.h` (see its `VENDORED.md`).

## Tests

Run from the source root after native prerequisites are available:

```sh
ctest --test-dir build-linux --output-on-failure
cargo test --manifest-path client/transport/Cargo.toml --release --locked
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/transport_smoke.py client/transport/target/release/libvrostbridge_transport.so
```

For core-only native contracts without the streaming build:

```sh
cmake -S . -B build-contracts -DVROST_BUILD_LINUX_CLIENT=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build-contracts --parallel
ctest --test-dir build-contracts --output-on-failure
```

The Linux runtime probe still builds in this configuration; host-hand tests appear only when the patched ALVR headers exist. Transport smoke opens local discovery sockets and does not simulate a headset. Tests needing platform tools or physical hardware remain separate gates. Do not equate a green subset with a full cross-platform acceptance result.

The Windows client (1.0.0.7) is tested the same way from Linux: the Python suite above includes its Windows modules with stand-ins for the Windows calls (`tests/test_windows_*.py`, `tests/test_vrostbridge_1007.py`), `node tests/test_desktop_ui.js` checks its page wording, and `scripts/Build-Client-Windows.py … --test` runs its C++ tests under Wine. `tests/test_windows_packaging.py` also builds both launchers when `VROSTBRIDGE_WINDOWS_SDK` names an xwin SDK and `clang`, `lld-link` and `llvm-rc` are on `PATH`. None of these replaces a run on Windows hardware.

## Installable releases and source archives

Follow [packaging/README.md](../packaging/README.md) for the frozen companion and installers. Windows assembly requires a separately verified host payload, matching current native overlay, compiler/NSIS inputs and provenance; the source ZIP alone does not supply all of them. Client-only assembly has its own entry point and does not modify host binaries. The Windows client's packages come from `packaging/build_windows_client.py` (step 5 above), which takes only the embedded Python from a host payload. Every package of a release carries one version, and one `SHA256SUMS.txt` lists them all.

The source archives come from one file list: what git tracks plus new files it does not ignore. `python3 scripts/package_public_source.py` makes the whole source, `VROSTBridge-<version>-public-source.zip`, the same files as the repository ([PUBLISHING.md](PUBLISHING.md) says how it becomes the public repository). `python3 scripts/package_component_sources.py` splits it by part for the release page: `VROSTBridge-<version>-host-source.zip`, `VROSTBridge-<version>-windows-client-source.zip` and `VROSTBridge-<version>-bazzite-client-source.zip`. Each holds what its part is built from (its code, build scripts and installer, the companion, the ALVR patches, the notices, licences and documentation) and a `SOURCE.md` that says what is in it, how to build it and what was left out. Every file has to belong to a part or be named as whole-source only (the tests that check every part together, the workflow and the release scripts), so a new file can't drop out of every archive. Both scripts check each archive after writing it and refuse a file over 50 MB or one matching the maintainer's private patterns (`VROSTBRIDGE_PRIVATE_PATTERNS`). `python3 scripts/package.py` makes the older maintainer archive, with a per-file SHA-256 manifest and the built Linux client inside. Pinned sources improve repeatability, but byte-identical builds across arbitrary systems are not promised; for 1.0.0.11, the two client archives rebuilt the released native programs byte for byte. See [BUILD_VERIFICATION.md](BUILD_VERIFICATION.md) for what was actually tested in this handoff.

## Regenerate the readable specification

`docs/PROJECT_SPEC.md` is canonical. With ReportLab and the DejaVu Sans/Mono fonts installed at `/usr/share/fonts/truetype/dejavu/` on Linux, run:

```sh
python3 scripts/render_spec.py /absolute/output/path/VROSTBridge-Project-Specification-1.0.0.13.pdf
```

The resulting PDF needs a rendered-page layout check before distribution. The render script is documentation tooling, not a runtime dependency.
