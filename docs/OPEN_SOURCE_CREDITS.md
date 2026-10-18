# VROSTBridge — open-source credits

Reviewed for VROSTBridge 1.0.0.14 (the host, the Bazzite client and the Windows client), documentation revision 2026-10-18. VROSTBridge's own code is MIT-licensed (`LICENSE`, copyright JagerBombastic); [THIRD_PARTY.md](../THIRD_PARTY.md) is the short list of what each package ships, and `NOTICE` the key attributions. Paths under `dist/host/licenses/` refer to the packaged/built Windows host, which is separate from this source archive. Since RC4 the Windows host contains no FFmpeg (see below); the Windows client carries its own LGPL FFmpeg build, with its source.

VROSTBridge builds on the work of the following projects and their
contributors. The original licence texts and copyright notices remain the
applicable terms for each component. This overview does not replace them.

VROSTBridge is an independent open-source project. It is not affiliated with,
sponsored by, or endorsed by Valve Corporation, Bigscreen, or any other headset,
tracking-system or platform manufacturer, nor by the ALVR project, Project Babble,
VB-Audio or Universal Blue (Bazzite). Third-party product names and trademarks
belong to their respective owners; references to them identify compatibility or
dependencies and do not imply endorsement or certification. SteamVR and the
headset vendor's software are separate products, installed by the user. OpenVR's
open-source licence does not describe the licence of the SteamVR runtime itself.

## Streaming and the native headset client

| Component | Contribution | Licence and included notices |
| --- | --- | --- |
| [ALVR](https://github.com/alvr-org/ALVR) | Streaming host driver, client transport, audio, input and tracking protocol. Copyright polygraphene, alvr-org and contributors. | MIT; `licenses/ALVR-MIT.txt`, plus notices in the upstream host distribution. Rust dependencies have separate notices. The Windows driver also contains, in object code, NVIDIA Video Codec SDK sample code (*This software contains source code provided by NVIDIA Corporation*; NVIDIA's licence, `licenses/NVIDIA-Video-Codec-SDK-samples.txt`) and the MIT-licensed `nvEncodeAPI.h`, AMD AMF headers and backward-cpp (`licenses/NVIDIA-nvEncodeAPI-MIT.txt`, `licenses/AMD-AMF-MIT.txt`, `licenses/backward-cpp-MIT.txt`). |
| [OpenVR](https://github.com/ValveSoftware/openvr) | Valve's API header and loader used to communicate with the local SteamVR runtime (`libopenvr_api.so` on Bazzite, `openvr_api.dll` on Windows). | BSD 3-Clause; `third_party/openvr/LICENSE`. Copyright Valve Corporation. |
| [FFmpeg — Linux client](https://ffmpeg.org/) | H.264, HEVC and AV1 decoding (VA-API) and CPU colour conversion on the Linux client. | LGPL 2.1 or later for the supplied minimal shared build; `licenses/FFmpeg-LGPL-2.1.txt` and `third_party/ffmpeg/LICENSE.md`. Exact corresponding source and build arguments are under `third_party/ffmpeg/`. |
| [FFmpeg — Windows client](https://ffmpeg.org/) | 1.0.0.7: H.264, HEVC and AV1 decoding with Direct3D 11 (D3D11VA) and CPU decoding on the Windows client (`avcodec-60.dll`, `avutil-58.dll`, `swscale-7.dll`), cross-compiled with mingw-w64 (GCC 13, win32 threads) from the same unmodified n6.1.4 source. | LGPL 2.1 or later for the supplied minimal shared build; `licenses/FFmpeg-LGPL-2.1.txt` and `third_party/ffmpeg/LICENSE.md`. The package carries the exact corresponding source (`third_party/ffmpeg/ffmpeg-n6.1.4-source.tar.xz`) and the build arguments (`third_party/ffmpeg/BUILD-Windows.txt`; `scripts/Build-FFmpeg-Windows.sh` in the source). Linked into the DLLs: mingw-w64's runtime start-up and support code (`licenses/MinGW-w64.txt`) and libgcc, statically, under the GCC Runtime Library Exception, which requires no notice. The Khronos Vulkan headers (Apache-2.0 or MIT) were used for declarations at build time only. |
| [Inter](https://github.com/rsms/inter) | Interface typeface of the pages, the Game Mode screen and the in-headset cards (RC4.2). The Inter Project Authors (Rasmus Andersson). | SIL Open Font License 1.1, no Reserved Font Name; `licenses/Inter-OFL.txt`, also next to the fonts in `launcher/fonts/` and `assets/fonts/`. Subset and instanced from the Google Fonts release by `scripts/build_fonts.py`. |
| [Outfit](https://github.com/Outfitio/Outfit-Fonts) | Heading typeface of the pages and the Game Mode screen (RC4.2). The Outfit Project Authors. | SIL Open Font License 1.1, no Reserved Font Name; `licenses/Outfit-OFL.txt` and next to the fonts. Built like Inter. |
| [Montserrat](https://github.com/JulietaUla/Montserrat) | The word *Bridge* in the headers' one-line wordmark (1.0.0.3), kept as outlines in `launcher/brand/vrostbridge-name.svg` and `.png`; no font file is included. The Montserrat Project Authors. | SIL Open Font License 1.1; the licence covers the font, not artwork made with it, so nothing more is required; listed for provenance. Set by `brand/tools/set_bridge.py` from the Google Fonts release (the file's SHA-256 is in the script). |
| [stb](https://github.com/nothings/stb) | `stb_truetype.h` (font rasterising for the in-headset cards and `libvrosttext.so`) and `stb_image.h` (JPEG/PNG loading for the Game Mode screen). Sean Barrett and contributors. | Public domain or MIT at your choice; `licenses/stb.txt`, `third_party/stb/LICENSE` and `third_party/stb/VENDORED.md` (commit `2c980bb5`, unchanged). |
| [font8x8](https://github.com/dhepper/font8x8) | 8x8 bitmap font, now the fallback in-headset panel when the fonts cannot be loaded. Daniel Hepper, from Marcel Sondaar's and IBM's VGA fonts. | Public domain; embedded unchanged in `client/device/status_text.hpp`. |
| [Intel libvpl](https://github.com/intel/libvpl) | Video-processing dispatcher included in the Windows host. | MIT, copyright Intel Corporation; `dist/host/licenses/libvpl.txt`. |
| Rust ecosystem libraries | Dependencies used by ALVR and the Rust client adapter (`libvrostbridge_transport.so`, and from 1.0.0.7 `vrostbridge_transport.dll`). | Individual copyright/licence files in `licenses/rust/`; `licenses/rust/INDEX.json` records the collected versions and declared licences. `client/transport/Cargo.lock` in the source package records the adapter's dependency resolution. The `symphonia` 0.5.5 crates and `option-ext` 0.2.0 are MPL-2.0, used unmodified; their source is available at those versions from crates.io and from https://github.com/pdeljanov/Symphonia and https://github.com/soc/option-ext. From 1.0.0.7 the collection also covers the 18 crates that only the Windows client's build uses (Microsoft's `windows` crates 0.58, `gpu-allocator` and others, each under MIT, Apache-2.0 or the Unlicense, or a choice of them); `THIRD_PARTY.md` names them with their versions. |

The ALVR source revision is
`f5023f8e78fa14ea57fb1038548d7820289a8524` (`21.0.0-dev14`). The Windows host package
uses a matching patched native build; supporting dependency provenance is recorded in
`HOST_PROVENANCE.txt` and current native hashes/patch identity in
`dist/host/vrostbridge-build.json`. All twenty ordered source patches are retained under
`patches/`, covering consent, networking, controllers, capture/AMF, native protocol/build, chaperone handling, the host frame-timing lock (0007), client stream statistics and loss policy (0008), host stream options and video pacing (0009), gaze-driven foveated encoding (0010), AMF intra refresh (0011), optical-centre foveation (0012), AMF output drain (0013), the host prediction switch (0014), startup display configuration (0015), the filtered reduced area (0016), the host gaze hold (0017), exact colour and frames without a head pose counted (0018) and the gaze comfort zone (0019), and the plain-SDR colour variant with the gaze-reach and controller diagnostics (0020). These changes are VROSTBridge's responsibility.

The 0.2.13 client skeleton converter follows the joint transforms in the pinned
ALVR server's `alvr/server_openvr/src/tracking.rs`. The converter and its tests
retain attribution to ALVR and reference Valve's documented OpenVR skeleton
hierarchy. No new streaming engine or uncredited vendor code is incorporated.

The OpenVR revision is `0924064316de3effbcd1acf1e309182a2deb1c05`.
The Linux FFmpeg runtime is based on `n6.1.4`, revision
`34277e12e80031c7f89494ba543684bc1dd0be8f`. It is dynamically linked; its shared
libraries and corresponding source remain separate files in the distribution. The
Windows client's FFmpeg runtime (1.0.0.7) is built from the same source snapshot and is
dynamically linked in the same way: its DLLs, the source and the Windows build notes are
separate files in the Windows client package.

### Windows host dependency provenance

Since 1.0.0-rc4 the Windows driver is cross-built from the pinned ALVR source and
the patches in `patches/` **without ALVR's optional `gpl` feature**. That feature only adds
an x264/FFmpeg CPU encoder fallback, far too slow for VR; AMD AMF, NVIDIA NVENC and
Intel VPL hardware encoding do not use FFmpeg. The rebuilt driver imports no FFmpeg
library, and the RC4 host package therefore no longer contains the GPL-enabled BtbN
FFmpeg 8.1 runtime (`avcodec-62.dll` and related DLLs) or its notice. Hosts from RC1
to RC3 did include it, as an unmodified part of the upstream ALVR host distribution.

The driver statically links the Intel libvpl 2.15 dispatcher (MIT), built from
source with the same toolchain; its notice is `dist/host/licenses/libvpl.txt`. ALVR's
original `dist/host/licenses/` directory is otherwise retained, including `ALVR.txt`,
`Valve.txt`, `libvpl.txt` and `dependencies.html`. ALVR's unmodified `Dashboard.exe`
embeds the Hack, Ubuntu, Noto Emoji and emoji-icon-font fonts, whose licences are in
`licenses/ALVR-Dashboard-fonts.txt`. The HTML file records the upstream
host's additional dependency acknowledgments; these are distinct from the Linux
adapter's `licenses/rust/` collection. The exact compiler, Rust toolchain and patch
hashes of each driver build are in `dist/host/vrostbridge-build.json`; the cross-build
recipe is `scripts/Build-Host-Cross.py` (see [BUILDING.md](BUILDING.md)).

The archive under `third_party/ffmpeg/` is the **n6.1.4** source of both clients'
decoders: the Bazzite build (`BUILD.txt`) and, from 1.0.0.7, the Windows build
(`BUILD-Windows.txt`). Those two are the only FFmpeg builds VROSTBridge distributes.

## Desktop launcher and packaging

| Component | Contribution | Licence and included notices |
| --- | --- | --- |
| [Python](https://www.python.org/) | Desktop launcher and setup/runtime scripts. | PSF licence and accompanying historical/third-party notices. Linux: `licenses/Python-Linux.txt`, with the notices of the libraries inside the frozen companion (OpenSSL, libffi, expat, zlib, bzip2, xz, mpdecimal) in `licenses/Python-Linux-incorporated.txt`; Windows embedded distribution (the host's, and from 1.0.0.7 byte for byte the same in the Windows client): `runtime/python/LICENSE.txt`, which also covers the libraries inside it and the Microsoft runtime code it is linked with. |
| [PyInstaller](https://pyinstaller.org/) | Packs the Linux desktop launcher and its Python runtime. | GPL 2.0 or later with the upstream bootloader exception; `licenses/PyInstaller.txt`. The same file describes the run-time hooks' separate terms. |
| [NSIS](https://nsis.sourceforge.io/) | Compiler and installation engine for the Windows setup executables (the host's and, from 1.0.0.7, the Windows client's). Copyright NSIS, Nullsoft and contributors. | Core zlib licence with component notices, including the LZMA module's CPL-1.0 linking exception; the complete compiler-package notice is retained in `licenses/NSIS.txt`. |
| [Go](https://go.dev/) | Runtime and toolchain used by the standalone camera helper. | BSD licence; `licenses/Go-BSD.txt`. |
| [Zig](https://ziglang.org/), LLVM and MinGW-w64 | Windows bootstrap and previous Windows diagnostic build tooling/runtime support; the host's two helper programs are built with `zig cc`. From 1.0.0.7 LLVM's clang-cl and lld-link also cross-build the Windows client and both launchers, and mingw-w64's runtime code is linked into the Windows client's FFmpeg DLLs. | Original notices in `licenses/Zig.txt`, `licenses/LLVM-*.txt` and `licenses/MinGW-w64.txt`, as applicable to the supplied build. |

The Windows host package also includes Microsoft's Visual C++ redistributable.
It is a Microsoft runtime with its own terms, not a VROSTBridge open-source
component. The installer does not represent it as an open-source dependency.
The Windows client package (1.0.0.7) includes no such installer: its native programs
and transport library link Microsoft's C runtime statically, from the Microsoft CRT and
Windows SDK used to build them (Microsoft's terms; build inputs only, never
redistributed as such), and its embedded Python carries the Microsoft runtime DLLs of
the Python package under the terms in `runtime/python/LICENSE.txt`.

## Optional Beyond 2e camera and gaze path

[go-bsb-cams](https://github.com/LilliaElaine/go-bsb-cams), by LilliaElaine and
contributors, supplies the separate Linux camera helper. Its pinned source is
`d0df45de2d9ce1a7f7febf6a2c1313812e5df287`. The upstream GPL version 2 text is in
`third_party/go-bsb-cams/LICENSE`. The package includes the helper's corresponding
source, its vendored dependencies and their notices under
`third_party/go-bsb-cams/`. Build instructions are retained alongside that source
and in the VROSTBridge source package.

VROSTBridge changes the helper in the ways listed in
`third_party/go-bsb-cams/VROSTBRIDGE_CHANGES.md` and marked in the source: its HTTP
listener is loopback-only, so camera images are served on this PC alone, and (from
1.0.0) its MJPEG server is a small one written for VROSTBridge
(`mjpeg_stream.go`) instead of the `github.com/hybridgroup/mjpeg` module, whose
repositories state no licence. From 1.0.0.3 that server sends each reader every new
picture as it arrives, at most one every 15 ms (the module's pacing was one every 50 ms),
from 1.0.0.5 one every 8 ms. From 1.0.0.5 the helper also passes the cameras' pictures on
without decoding and encoding them again (`jpeg_frames.go`), asks for the cameras' fastest
frame rate, skips a picture that lost USB data instead of opening the camera again, and
writes `STATS` and `CAMERA` lines (`camera_stats.go`). For that, VROSTBridge modifies four
files of the vendored **go-uvc** and adds one (`claim_interval.go`), each marked at its top
as Apache-2.0 asks, and listed in `VROSTBRIDGE_CHANGES.md`.
The helper remains a separate process; camera images are not sent to the VR
rendering host by VROSTBridge.

Compiled into the helper are Google's **gousb** (Apache-2.0) and **uuid** (BSD
3-Clause), kevmo314's **go-uvc** (Apache-2.0) and the Go runtime (BSD). LabStack's
**Echo/gommon**, mattn's **go-colorable/go-isatty**, valyala's
**bytebufferpool/fasttemplate** and the Go **x/crypto, x/net, x/sys and x/text**
libraries are listed in its `go.mod` and vendored but not compiled in. Their precise
versions are recorded in the helper's `go.mod`, `go.sum` and `vendor/modules.txt`;
each keeps its own licence and notices. The helper also uses the OS-provided libusb
library.

[Baballonia](https://github.com/Project-Babble/Baballonia), by Project Babble and
contributors, turns the eye-camera pictures into where you look, owns the models and
performs the calibration. It is **not bundled or redistributed** with VROSTBridge.
From RC4.5 the client's *Set up eye tracking* downloads its unmodified Linux release
1.1.1.0rc6 (`Baballonia.x64.v1.1.1.0rc6.tar.xz`, SHA-256
`06c9ce7ecca19a0fee3ead40ef0c705ca173916e11a3315b99df63d348ea906a`) from Project
Babble's own GitHub release page, only after the user has been shown and accepted its
licence, the **Babble Software Distribution License 1.0** (personal, non-commercial
use). VROSTBridge then starts and stops it and writes a few of its settings (its
cameras, eye areas and output); its software, models, licence and requirements remain
Project Babble's. From 1.0.0, so that the calibration shows over the stream, VROSTBridge
puts a small launcher script of its own (MIT, no Baballonia code) in front of
Baballonia's calibration program, which stays unchanged beside it as
`BabbleCalibration.program` and is started with Baballonia's own `--use-openvr`
option. From 1.0.0.7 the Windows client downloads Project Babble's unmodified Windows
installer of the same release (`Baballonia.Setup.v1.1.1.0rc6.exe`, SHA-256
`dec2e8fd1e5c844662f99b47680d772a393f6eb2fdf4e4dd3c62853928b2c470`) on the same terms
and runs it silently into VROSTBridge's own folder, putting back the uninstall entry
and shortcuts it writes; a matching Baballonia the user installed is used instead and
never removed. On Windows Baballonia reads the eye cameras and shows its calibration
over the stream by itself, so neither the camera helper nor the launcher script is used
there. VROSTBridge only uses Baballonia as a separate program, through its
command line, settings and OSC output. Integration notes also reference source
revision `5dbd332bc7f94868ec9b11a1b9ebd3c28acb91be`.

[VB-CABLE](https://vb-cable.com), by VB-Audio Software, is the virtual audio cable
that the host page's **Set up headset microphone** (RC4.3) downloads from VB-Audio's
own website when the user asks, then opens VB-Audio's own setup program, which the
user completes. It is **not included or redistributed** with VROSTBridge, and its
setup is never run silently. VB-CABLE is donationware; its own licence applies, and
contributions to its author are welcome at https://vb-cable.com.
VROSTBridge checks the download against the SHA-256 of `VBCABLE_Driver_Pack45.zip`
or, for a newer package, a valid Authenticode signature by VB-Audio's author.

VROSTBridge accepts the provider's local OSC gaze output. The provider and its
contributors are credited for producing and calibrating the gaze estimates;
VROSTBridge does not claim that work as its own. Physical calibration accuracy
and full headset interoperability still require testing.

## System components and artwork

The native client uses the user's Vulkan implementation, GPU drivers, libva,
libdrm, ALSA, libusb and platform C/C++ runtime. These come from the operating
system and their original suppliers. On Windows (1.0.0.7) it uses the GPU driver's
Vulkan loader (`vulkan-1.dll`), Direct3D 11 and DXGI, and Windows' own system
libraries instead; the companion calls Windows' own networking, Wi-Fi, power and
firewall interfaces. SteamVR must be installed separately on both machines.

From 1.0.0.4 the client companion reads, and only when the user chooses *Set the GPU
clock to automatic* changes, the GPU clock setting of steamos-manager, the system
service behind Steam's Performance settings on SteamOS and on distributions that use
it, such as Bazzite, through its D-Bus interface with the system's `busctl`. Nothing of
steamos-manager is included; on a PC without it nothing is read or changed. From 1.0.0.8
*Balance performance during sessions* also sets steamos-manager's power limit and manual GPU
clock through the same interface, only while a session streams, and sets them back afterwards.

From 1.0.0.8 the client companion also sends SteamVR 2.0 base stations their power command over
Bluetooth Low Energy, through the operating system's own Bluetooth stack: BlueZ's D-Bus
interface and its `busctl` and `bluetoothctl` tools on Bazzite, and Windows' Bluetooth API from
Windows PowerShell on Windows. The command (one byte to a published characteristic) follows the
base stations' documented behaviour as used by community tools; no code from any of them is
included.

The VROSTBridge logo is the project's own artwork, supplied by the maintainer and
kept in `brand/` (`brand/source/` retains the supplied pictures unchanged; the
SVG masters are vector reconstructions of them, with the wordmark traced, so no
font is included; from 1.0.0.3 the headers' one-line wordmark adds *Bridge* set in
Montserrat, also as outlines). The application's icons, favicons, tray icons, Steam
artwork, header wordmark and headset logo are built from those masters by `scripts/build_brand_assets.py`,
the smallest icon sizes fitted to the pixel grid by
`brand/tools/refine_small_icons.py`. The MIT licence covers the code; the artwork
is under CC BY-ND 4.0 (`brand/LICENSE.md`). No rights to upstream project names,
logos or trademarks are claimed by these credits. Until 1.0.0 the project used a
different logo, which the packages no longer contain.

## Host diagnostic event reader

websockets 16.0, by Aymeric Augustin and contributors, supplies the local ALVR
event WebSocket client. Unmodified Python source is included under
`host/vendor/websockets/`, without optional native acceleration. BSD 3-Clause
terms are in `licenses/websockets-BSD.txt`; its `asyncio/async_timeout.py`, from
aio-libs' async-timeout, is Apache-2.0 (`licenses/websockets-async-timeout-Apache-2.0.txt`).

## Borrowed ideas (no code copied)

The client's fused frame pass (`client/platform/linux/fused.comp`) reimplements the
adaptive sharpening amount of AMD FidelityFX CAS and dithers with Jorge Jimenez's
interleaved gradient noise; `client/device/conversion.hpp` and the shaders invert
ALVR's foveated encoding (`alvr/graphics/resources/stream.wgsl`), under ALVR's MIT
notice above.

The Vulkan layer for Baballonia's calibration overlay (`client/platform/linux/openvr_texture_layer.c`,
1.0.0.2) enables the extensions that Robrecht Cannoodt's
[steamvr-room-setup-on-fedora](https://github.com/rcannood/steamvr-room-setup-on-fedora) (MIT)
found SteamVR needs on Fedora's Mesa for its Room Setup; its analysis matched the calibration's
crash. The layer is VROSTBridge's own code.

## Performance research

Moonlight, Apollo and Sunshine are credited as research references for
queue/cadence/bitrate practices in `PERFORMANCE_RESEARCH_0.2.5.md`; no source from
those projects was copied into VROSTBridge. ALVR remains the actual streaming
dependency. (From 0.2.6 to 1.0.0-rc4.5 this section also described the earlier mark;
1.0.0 replaced it with the artwork above.)

## Native Game Mode additions in 0.2.6

- **SDL2 2.30.0**, Sam Lantinga and contributors: native 2D controller window. The shared library is bundled; zlib licence and component notices are in `licenses/SDL2.txt`. Source: https://github.com/libsdl-org/SDL/tree/release-2.30.0 . System window/audio dependencies are supplied by Bazzite.
- **Wayland 1.22.0**, Kristian Høgsberg and contributors: bundled shared server library and standard DRM lease protocol. `licenses/Wayland.txt` retains copyright and MIT/X11 terms. The XML and generated C bindings retain their original MIT notices under `client/platform/linux/lease_protocol/` in source. `third_party/wayland/PROVENANCE.json` records the exact XML hash and binary package version. Source: https://gitlab.freedesktop.org/wayland/wayland/-/tree/1.22.0 . The client uses the system's libdrm and libffi.
- **DejaVu Sans**, based on Bitstream Vera: from 0.2.6 to RC4.1 the Game Mode screen used an atlas of rasterized glyphs. RC4.2 replaced it with Inter and Outfit (above). Subsets of DejaVu Sans, Sans Bold and Sans Mono remain embedded in `docs/PROJECT_SPEC.pdf`, so the font copyright, permission terms and public-domain additions stay in `licenses/DejaVu.txt`. Source: https://dejavu-fonts.github.io/ .
- **OpenGamingCollective gamescope and gamescope-session**, based on Valve's gamescope and community work: existing Bazzite system components supply the startup connector lease. VROSTBridge consumes their existing socket interface; it does not bundle or modify gamescope. Upstream: https://github.com/OpenGamingCollective/gamescope and https://github.com/OpenGamingCollective/gamescope-session . Their licences remain applicable to the separately installed system software.

The native screen also includes an About / credits page; the desktop web UI retains the full attribution section. The VROSTBridge app icon is used on both ends, including the Windows launcher and installer; the Windows tray uses the one-colour symbol, fitted to the pixel grid at 16 to 32 px like the app icon.


## Native ALVR changes in 0.2.14

ALVR remains the streaming engine. The supplied matching driver and client
transport are built from the recorded ALVR pin plus the source patches in this
release. The changes add exact OpenVR controller curls and both skeletal motion
ranges, bounded control writes/media-aware recovery, interface selection, and
host capture/AMF ownership fixes. These modifications are credited as VROSTBridge
(then VROSTBridge) changes to ALVR, not presented as an independently developed streaming engine.
Valve's OpenVR structures retain their original attribution.

The native driver build record includes its compiler/toolchain and patch hashes.
Cross-compilation uses Rust, LLVM/Clang, cargo-xwin/xwin and Microsoft SDK/CRT build
inputs. The SDK has Microsoft's own terms; it is not relabelled as open source or
bundled as an end-user development SDK. Original runtime dependency licences and
the artwork credits above remain included.


The rebuilt native components' Rust dependency notices are indexed in
`licenses/rust/INDEX.json`. The inventory covers 428 third-party packages from the
actual Linux-client, Windows-host and (from 1.0.0.7) Windows-client normal/build graphs; build-time
entries are not represented as necessarily linked runtime components. Four earlier entries
remain for traceability. Each package records its source, version, licence and
notice-file hashes. All 769 notice files were verified for 1.0.0.7 and again for each release since, 1.0.0.14 included. For hexf-parse 0.2.1, whose
crate and exact repository commit omit a full licence file, the declared CC0-1.0
text is supplied from SPDX License List Data v3.27.0 with that provenance stated.
See `docs/evidence/rust-notices-0.2.14.json` for graph and coverage details.


VROSTBridge 0.2.14.1 added the local host-version migration correction, AMD codec
configuration isolation and encoder-selection diagnostics. It retained the same
third-party versions and licence inventory as 0.2.14. The supplied ALVR patch
set includes the new host changes.


## 1.0.0-rc1 source changes and notice review

This candidate retains the pinned upstream versions above. The six ALVR patches
include VROSTBridge controller input recovery and corrected, idempotent host
chaperone geometry. These remain local changes to ALVR. The new client
performance sampler uses Python and Linux sysfs and adds no dependency.

The actual unchanged Cargo graphs were checked against the current lockfiles;
`docs/evidence/rust-notices-1.0.0-rc1.json` refreshes the 410-package third-party
inventory with no missing collected source or notice text. (The separate Windows
FFmpeg build-recipe/source limitation noted at the time ended with 1.0.0-rc4, whose
host carries no FFmpeg; RC1 to RC3 host packages must not be republished.)
