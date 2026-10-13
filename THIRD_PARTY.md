# Third-party software and notices

For VROSTBridge 1.0.0.11: the host, the Bazzite client and the Windows client. No third-party
component has been added or changed since 1.0.0.7, which added the Windows client's package
(1.0.0.9's patches 0021 to 0023 are VROSTBridge's own changes to ALVR, under ALVR's MIT terms).
Reviewed 2026-10-12. In plain words: [docs/LICENSING.md](docs/LICENSING.md).

VROSTBridge's own code is MIT-licensed (see [LICENSE](LICENSE)). It stands on other people's
work, which keeps its own licences; this file lists what is used, what each package ships and
where the notices are. Every package carries `LICENSE`, `NOTICE`, this file, `licenses/` and
`docs/`. Fuller history and roles: [docs/OPEN_SOURCE_CREDITS.md](docs/OPEN_SOURCE_CREDITS.md).

## Windows host package

- **ALVR** (MIT, `licenses/ALVR-MIT.txt`): the streaming engine. The host driver is ALVR's,
  built from revision `f5023f8e78fa14ea57fb1038548d7820289a8524` with VROSTBridge's twenty-three
  patches in `patches/` (VROSTBridge's changes, under ALVR's MIT terms). The unmodified rest of
  ALVR's host distribution keeps its notices in `dist/host/licenses/` (`ALVR.txt`, `Valve.txt`,
  `libvpl.txt`, `dependencies.html`). Compiled into the driver:
  - **NVIDIA Video Codec SDK sample code** (NvEncoder, NvEncoderD3D11, NvCodecUtils), object code
    only. *This software contains source code provided by NVIDIA Corporation.* NVIDIA's Video
    Codec SDK licence applies to it, not MIT: `licenses/NVIDIA-Video-Codec-SDK-samples.txt`.
    The `nvEncodeAPI.h` interface is MIT: `licenses/NVIDIA-nvEncodeAPI-MIT.txt`.
  - **AMD AMF** headers (MIT, `licenses/AMD-AMF-MIT.txt`), **backward-cpp** (MIT,
    `licenses/backward-cpp-MIT.txt`) and the **Intel libvpl** dispatcher (MIT,
    `dist/host/licenses/libvpl.txt`).
  - **Rust crates**: each crate's licence in `licenses/rust/`, indexed with versions and sources
    in `licenses/rust/INDEX.json`.
- **ALVR Dashboard** (unmodified) embeds the Hack, Ubuntu, Noto Emoji and emoji-icon-font fonts:
  `licenses/ALVR-Dashboard-fonts.txt`.
- **OpenVR** (BSD 3-Clause, Valve Corporation): `third_party/openvr/LICENSE`.
- **Python** embeddable runtime (PSF licence): `runtime/python/LICENSE.txt`.
- **websockets 16.0** (BSD 3-Clause, `licenses/websockets-BSD.txt`), unmodified in
  `host/vendor/websockets/`; its `async_timeout.py` is Apache-2.0:
  `licenses/websockets-async-timeout-Apache-2.0.txt`.
- **Inter** and **Outfit** typefaces (SIL Open Font License 1.1): `licenses/Inter-OFL.txt`,
  `licenses/Outfit-OFL.txt`.
- **NSIS** installer engine (zlib licence and component notices): `licenses/NSIS.txt`.
- **Zig**, **LLVM** and **MinGW-w64** runtime parts in the two helper programs
  (`host/VROSTBridgeVRHelper.exe`, `host/VROSTBridgeAudioProbe.exe`), built with `zig cc`:
  `licenses/Zig.txt`, `licenses/LLVM-*.txt`, `licenses/MinGW-w64.txt`. The launcher
  (`VROSTBridge Host.exe`) has no C runtime and contains only VROSTBridge's own code, its icon
  and version information.
- **Microsoft Visual C++ Redistributable** (`runtime/vc_redist.x64.exe`): Microsoft's own
  runtime under Microsoft's terms, not open source.

No FFmpeg is included on Windows since 1.0.0-rc4: the driver is built without ALVR's optional
`gpl` feature (a CPU x264 fallback). Host packages RC1 to RC3 did include a GPL FFmpeg build
and must not be republished.

## Bazzite client package

- **ALVR** client core (MIT) and its Rust crates (`licenses/rust/`).
- **FFmpeg** n6.1.4, a minimal shared build for VA-API decoding (LGPL 2.1 or later):
  `licenses/FFmpeg-LGPL-2.1.txt`; exact source and build arguments in `third_party/ffmpeg/`.
- **SDL2** 2.30.0 (zlib licence): `licenses/SDL2.txt`.
- **Wayland** 1.22.0 (MIT): `licenses/Wayland.txt`; the DRM lease protocol (MIT):
  `licenses/wayland-protocols-drm-lease-MIT.txt`.
- **OpenVR** (BSD 3-Clause): `third_party/openvr/LICENSE`.
- **stb** (`stb_truetype.h`, `stb_image.h`; public domain or MIT): `licenses/stb.txt`.
- **font8x8** (public domain, Daniel Hepper), embedded in `client/device/status_text.hpp`.
- **Inter** and **Outfit** (SIL OFL 1.1), as above.
- **Python 3.12** and the libraries inside the frozen companion (OpenSSL, libffi, expat, zlib,
  bzip2, xz, mpdecimal): `licenses/Python-Linux.txt`, `licenses/Python-Linux-incorporated.txt`.
  **PyInstaller**'s bootloader (GPL 2.0 or later with its bootloader exception):
  `licenses/PyInstaller.txt`.
- **go-bsb-cams** (GPL version 2, LilliaElaine and contributors): the Bigscreen Beyond eye
  camera helper, a separate program (`tools/go-bsb-cams`). It is modified by VROSTBridge (it
  listens on this PC only and has its own small MJPEG server, which from 1.0.0.3 sends each reader
  every new picture, from 1.0.0.5 at most one every 8 ms; from 1.0.0.5 it passes the cameras'
  pictures on without decoding them again, asks for the cameras' fastest rate and skips damaged
  frames); the complete corresponding source, the changes and the build command are in
  `third_party/go-bsb-cams/` (`VROSTBRIDGE_CHANGES.md`). Compiled in: gousb and go-uvc
  (Apache-2.0; VROSTBridge 1.0.0.5 modifies four of go-uvc's files and adds one, each marked at its
  top), uuid (BSD 3-Clause), each with its licence under `vendor/`, and the Go runtime (BSD,
  `licenses/Go-BSD.txt`).

**MPL-2.0 crates.** The `symphonia` 0.5.5 crates and `option-ext` 0.2.0, used unmodified by
ALVR, are under the Mozilla Public License 2.0 (text in `licenses/rust/`). Their source code is
available at those exact versions from crates.io (for example
https://crates.io/crates/symphonia/0.5.5 and https://crates.io/crates/option-ext/0.2.0) and from
https://github.com/pdeljanov/Symphonia and https://github.com/soc/option-ext.

## Windows client package (1.0.0.7)

`VROSTBridge-Client-Setup-<version>.exe` and `VROSTBridge-Client-Portable-<version>.zip` hold the
same files (`packaging/build_windows_client.py`):

- **ALVR** client core (MIT, `licenses/ALVR-MIT.txt`) and its Rust crates, built into
  `vrostbridge_transport.dll` from the same source as the Bazzite client's library. Each crate's
  licence is in `licenses/rust/`, indexed in `licenses/rust/INDEX.json`, and the MPL-2.0 crates
  above are among them. From 1.0.0.7 that collection also covers the Windows client's own dependency
  graph (`windows_client` in the index): 18 crates only its build uses, all unmodified and under MIT,
  Apache-2.0 or either (winapi-util: Unlicense or MIT), among them `windows` 0.58.0 and `gpu-allocator`
  0.27.0. Two of them, `gl_generator` 0.14.0 and `khronos_api` 3.1.0, run only while building and ship
  no licence file in their crates; their Apache-2.0 licence is included from their repository at the
  crates' exact commits.
- **FFmpeg** n6.1.4 (LGPL 2.1 or later): `avcodec-60.dll`, `avutil-58.dll` and `swscale-7.dll`, a
  minimal shared build for Direct3D 11 video decoding, cross-compiled with mingw-w64 and built from
  the unmodified source. The package carries that exact source, the same file as the Bazzite
  client's (`third_party/ffmpeg/ffmpeg-n6.1.4-source.tar.xz`), FFmpeg's licence notes
  (`third_party/ffmpeg/LICENSE.md`, `licenses/FFmpeg-LGPL-2.1.txt`) and the Windows build's
  arguments (`third_party/ffmpeg/BUILD-Windows.txt`; the script is
  `scripts/Build-FFmpeg-Windows.sh` in the source). These are shared libraries and can be replaced
  with compatible rebuilt versions. Linked into them:
  - the **mingw-w64** runtime's start-up and support code: `licenses/MinGW-w64.txt` (Zope Public
    License 2.1, with parts in the public domain);
  - **libgcc** (GPL version 3 with the GCC Runtime Library Exception), linked statically. The
    exception lets code compiled by GCC be passed on under its own terms, here FFmpeg's, and asks
    for no notice; it is named here for completeness.

  Otherwise they use only Windows' own system libraries (`kernel32`, `msvcrt`, `bcrypt`). The
  **Khronos Vulkan headers** (Apache-2.0 or MIT) were used at build time for declarations only;
  nothing of them is shipped.
- **OpenVR** (BSD 3-Clause, Valve Corporation): `openvr_api.dll`; `third_party/openvr/LICENSE`.
- **Python** embeddable runtime 3.13 (PSF licence), byte for byte the host package's:
  `runtime/python/LICENSE.txt`, which also covers the libraries inside it and the Microsoft runtime
  code it is linked with.
- **Inter** and **Outfit** typefaces (SIL Open Font License 1.1): `licenses/Inter-OFL.txt`,
  `licenses/Outfit-OFL.txt`, also beside the fonts in `assets/fonts/`.
- **stb** (`stb_truetype.h`; public domain or MIT, `licenses/stb.txt`) and **font8x8** (public
  domain) in the in-headset cards, as on Bazzite.
- **websockets 16.0** (BSD 3-Clause, with its Apache-2.0 `async_timeout.py`), unmodified in
  `host/vendor/websockets/`, which the companion imports: notices as for the host above.
- **NSIS** installer engine (zlib licence and component notices): `licenses/NSIS.txt`.
- The native programs (`VROSTBridgeClient.exe`, `VROSTBridgeRuntimeProbe.exe`) and the transport
  DLL link Microsoft's C runtime statically, from the Microsoft CRT and Windows SDK they are built
  against, as the host driver does: Microsoft's code under Microsoft's terms, not open source. No
  Visual C++ redistributable is included. The launcher (`VROSTBridge Client.exe`) has no C runtime
  and contains only VROSTBridge's own code, its icon and version information.

Baballonia is never included: on Windows too it is downloaded on request (see below).

## In the documentation

- **DejaVu** fonts (Bitstream Vera licence and public-domain changes), subsets embedded in
  `docs/PROJECT_SPEC.pdf` and `docs/TECHNICAL_REFERENCE.pdf`, which the source and every package
  carry: `licenses/DejaVu.txt`.

## Ideas we borrowed (no code copied)

- The adaptive sharpening amount of AMD FidelityFX CAS, reimplemented in
  `client/platform/linux/fused.comp`.
- Interleaved gradient noise (Jorge Jimenez) for dithering, in the same shader.
- ALVR's foveated encoding (`alvr/graphics/resources/stream.wgsl`), inverted in
  `client/device/conversion.hpp` and the shaders; ALVR's MIT notice applies.
- Moonlight, Apollo and Sunshine as references for queueing, pacing and bitrate practice.
- The SteamVR 2.0 base stations' Bluetooth power command (one byte to their power
  characteristic), as community tools for switching base stations on and off use it
  (`launcher/base_stations.py`, `launcher/BaseStations.ps1`, 1.0.0.8).

## Optional software you install yourself (not included)

- **Baballonia** by Project Babble does the eye-tracking maths and calibration. It is not
  included or redistributed: when you choose *Set up eye tracking*, VROSTBridge shows you its
  licence (the Babble Software Distribution License 1.0, which allows personal, non-commercial
  use only) and, if you accept, downloads its unmodified release 1.1.1.0rc6 from Project Babble's
  GitHub page (pinned SHA-256). VROSTBridge then starts and stops it and writes some of its
  settings. So that its calibration shows over the stream on Bazzite, VROSTBridge puts a small
  launcher of its own (MIT, no Baballonia code) in front of Baballonia's calibration program, which
  stays unchanged beside it as `BabbleCalibration.program` and is started with Baballonia's own
  `--use-openvr` option. On the Windows client (1.0.0.7) VROSTBridge downloads Project Babble's
  unmodified Windows installer of the same release (pinned size and SHA-256) and runs it for you,
  silently, into VROSTBridge's own folder, or uses a matching Baballonia you installed yourself;
  Baballonia's Windows release shows its calibration over the stream by itself, so nothing is put in
  front of it there.
- **VB-CABLE** by VB-Audio Software, for the headset microphone on the host. Not included or
  redistributed: when you choose *Set up headset microphone*, VROSTBridge downloads VB-Audio's
  own package from VB-Audio's website and opens VB-Audio's own setup, which you complete; its own
  licence applies. It is donationware, so if you use it, consider supporting its author at
  https://vb-cable.com.
- **SteamVR**, **Steam**, GPU drivers and the operating system's graphics and audio libraries
  are separate prerequisites with their own terms.

## The VROSTBridge name and logo

The logo, icons and Steam artwork (`brand/`, `launcher/brand/`, `assets/hud/logo-192.rgba`,
`launcher/game_ui_assets/background.jpg`) are VROSTBridge's own artwork, made from the
logo the maintainer supplied (`brand/source/`): the symbol rebuilt as vectors and the wordmark
traced, so no font is included. The MIT licence
in `LICENSE` covers the code; the artwork is © 2026 JagerBombastic under Creative Commons
Attribution-NoDerivatives 4.0 (CC BY-ND 4.0, `brand/LICENSE.md`): it may be shared unchanged, with
credit, and modified versions and forks of the software use their own name and logo.

## Names and trademarks

VROSTBridge is an independent open-source project. It is not affiliated with, sponsored by, or
endorsed by Valve Corporation, Bigscreen, or any other headset, tracking-system or platform
manufacturer, nor by the ALVR project, Project Babble, VB-Audio or Universal Blue (Bazzite).

Third-party product names and trademarks belong to their respective owners. References to
products such as Valve Index, SteamVR, Steam and Bigscreen Beyond identify compatibility or
dependencies; they do not imply endorsement or certification.
