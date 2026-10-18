# Release packaging — VROSTBridge 1.0.0.13, host, Bazzite client and Windows client

Updated 2026-10-08. Normal users install release packages; maintainers use this guide after [native builds](../docs/BUILDING.md). Earlier notes are retained in [packaging history](../docs/history/PACKAGING_HISTORY_0.2.md).

The packages are `VROSTBridge-Host-Setup-<version>.exe`, `VROSTBridge-Host-Portable-<version>.zip` and `VROSTBridge-Bazzite-Installer-<version>.zip`, and from 1.0.0.7 the Windows client's `VROSTBridge-Client-Setup-<version>.exe` and `VROSTBridge-Client-Portable-<version>.zip` ([Windows client](#windows-client-from-1007)). Every package of a release carries the same version: for 1.0.0.7 they are `VROSTBridge-Host-Setup-1.0.0.7.exe`, `VROSTBridge-Host-Portable-1.0.0.7.zip`, `VROSTBridge-Bazzite-Installer-1.0.0.7.zip`, `VROSTBridge-Client-Setup-1.0.0.7.exe` and `VROSTBridge-Client-Portable-1.0.0.7.zip`, all listed in one `SHA256SUMS.txt` ([Release checksums](#release-checksums)). From 1.0.0.1 everything inside them carries the VROSTBridge name: the Linux programs (`VROSTBridge-Client` and the native files), the install and state folders, the Windows uninstall key and sign-in value, and the build manifest `vrostbridge-build.json`.

## Inputs and limits

The source tree has native Linux RC1.3 payloads and build inputs, not a complete prebuilt Windows host. Linux release assembly requires a frozen launcher plus the matching native client, transport, decoder libraries, shader, probe, lease helper and optional camera helper. Windows assembly requires a **separately verified host payload** with `SHA256SUMS`, `PACKAGED.json`, `HOST_PROVENANCE.txt`, embedded `runtime/` and `dist/host/`, plus a matching current native overlay with `vrostbridge-build.json` and its hashed files. Do not use an installed user's mutable runtime directory as the seed.

The existing assembler is a verified-input reuse path, not a clean all-dependencies bootstrap. A new maintainer without that host seed must recreate and qualify the dependency/runtime package first. Preserve dependency source/notice records. Since RC4 the Windows host carries no FFmpeg. The Windows client (1.0.0.7) needs the native folder of its cross-build ([BUILDING.md](../docs/BUILDING.md#windows-headset-client-1007)) and, from a verified host payload, only the embedded Python.

## Freeze the Linux companion

Use Python 3.12 and PyInstaller 6.22.3 (1.0.0.1 to 1.0.0.6 used Python 3.12.3). From the source root:

```sh
python3.12 -m PyInstaller --noconfirm --distpath /tmp/vrostbridge-frozen --workpath /tmp/pyi-work \
  packaging/VROSTBridge-Client.spec
```

The spec (since 1.0.0; written for the unreleased RC4.6) has the options of the earlier command line (`--onedir`, the source root on the path, the page, brand and Game Mode assets as data) and leaves the X11 libraries out of the bundle: `launcher/x11_window.py` loads libX11 with ctypes, and a plain PyInstaller command line would bundle the build machine's libX11 and libxcb instead of using the desktop's own.

Run `scripts/Build-Client-Linux.sh` (or `cmake --build` and `cmake --install --prefix dist/client`) first when native sources change. The client assembler refuses a `dist/client/VROSTBridgeClient` or `VROSTBridgeRuntimeProbe` whose version label does not match the release, or a payload without `reaim.spv`, `fused.spv` (RC4.3), the fonts, the headset logo or `libvrosttext.so`: RC4.1 shipped a client that still called itself rc4. Keep matching shared libraries together; ELF NEEDED entries must be basename-only and runtime lookup must not depend on a private build-machine LD_LIBRARY_PATH. Game Mode requires `VROSTBridgeLease`, SDL2, Wayland libraries, `libvrosttext.so` and the frozen UI assets. The fonts are not frozen: the pages, the Game Mode screen and the headset cards read them from the native payload's `assets/fonts/` (installed by CMake from `launcher/fonts/`).

## Client-only update

```sh
python3 packaging/build_client.py \
  --work /tmp/vrostbridge-client-package \
  --output /tmp/vrostbridge-release \
  --frozen /tmp/vrostbridge-frozen/VROSTBridge-Client
```

This emits the Bazzite installer ZIP and does not modify or require host binaries. It replaces the staging directory under `--work`; use a dedicated temporary directory. Generate release checksums after all files are final.

For a client-only update, `CLIENT_VERSION` (`launcher/version.py`, `core/version.hpp`, `CMakeLists.txt`, `packaging/install-bazzite.sh`, `packaging/build_distributions.py`) moves while every host version stays (`tests/test_rc2_updates.py` checks both sets); the native client and runtime probe are rebuilt for their version label. 1.0.0.1 updates both: the names, the rebuilt driver and transport library, and the line under the name. From 1.0.0.2 CMake also builds and installs the calibration overlay's Vulkan layer into `dist/client/vulkan/` (`libVkLayer_vrostbridge_openvr.so` and its manifest), and the client assembler refuses a payload without it. 1.0.0.2 moves both versions: the host is repacked over the same inputs as 1.0.0.1 with the launcher rebuilt for its version resource.

Every package carries `LICENSE`, `NOTICE`, `THIRD_PARTY.md`, `licenses/` and `docs/` (since 1.0.0; the Windows client's too). The clean public source archive (tracked and new files, no history or build outputs) is `python3 scripts/package_public_source.py`, and the same source split by part for the release page (rendering PC, Windows client, Bazzite client) is `python3 scripts/package_component_sources.py`; see [docs/BUILDING.md](../docs/BUILDING.md#installable-releases-and-source-archives) and [docs/PUBLISHING.md](../docs/PUBLISHING.md). Never publish host packages RC1 to RC3: they carried a GPL FFmpeg build without its source.

## Both packages, or host only

The current assembler requires Zig and NSIS paths, and the native overlay in addition to the retained host payload:

```sh
python3 packaging/build_distributions.py \
  --work /tmp/vrostbridge-package --output /tmp/vrostbridge-release \
  --host-input /path/to/verified-host-payload \
  --native-host-overlay /path/to/current/native-host-overlay \
  --frozen /tmp/vrostbridge-frozen/VROSTBridge-Client \
  --zig /path/to/zig --makensis /path/to/makensis \
  --nsisdir /path/to/nsis/share
```

Use `--host-only` and omit `--frozen` for host-only assembly. It verifies input hashes, upstream identity and the complete patch-set manifest, overlays the current native driver and shared companion/docs, builds the native Windows launcher (`VROSTBridge Host.exe`, through `packaging/build_windows_launcher.py`) and the helper executables (`VROSTBridgeVRHelper.exe`, `VROSTBridgeAudioProbe.exe`), and writes the host setup EXE, portable ZIP and checksums. The Windows helper uses pinned OpenVR C function tables; do not substitute compiler-specific C++ ABI interfaces. Packaging copies the complete docs, so the new specification is included in future installer builds.

The retained Windows packaging lineage uses NSIS 3.09 and embedded Python 3.13.12. Their runtimes and notice files come from the verified seed. The assembler does not download/reconstruct them on behalf of a new maintainer. Preserve `licenses/NSIS.txt` and Windows host dependency notices. The host carries no FFmpeg; the Windows client's FFmpeg DLLs are built from the same source as the Bazzite client's, and its package carries that source with the Windows build's notes.

## Host update over the previous payload

When the embedded Python runtime and helper EXEs are unchanged, reuse the previous released installer's verified payload instead of the full assembler. The companion (Python, HTML/JSON, profile code, artwork, docs) always comes from the source; the native launcher is rebuilt and passed in when its source, icon or version changes (it carries the version in its resource and log line, so normally every release); the driver is passed in when it is rebuilt:

```sh
7zz x VROSTBridge-Host-Setup-1.0.0.3.exe -o/tmp/previous-host   # the previous release; yields $PLUGINSDIR/payload
python3 packaging/build_windows_launcher.py --sdk /path/to/xwin-sdk --output /tmp/launcher   # or --zig /path/to/zig
python3 scripts/Build-Host-Cross.py --sdk-root /path/to/xwin-sdk --clang-cl … --llvm-lib … --lld-link … \
  --overlay /tmp/native-overlay   # only when the driver changes
python3 packaging/repack_host_payload.py --previous-payload '/tmp/previous-host/$PLUGINSDIR/payload' \
  --output /tmp/host/payload --launcher '/tmp/launcher/VROSTBridge Host.exe' [--native-overlay /tmp/native-overlay]
makensis -DPAYLOAD=/tmp/host/payload -DOUTPUT=/tmp/VROSTBridge-Host-Setup-<version>.exe packaging/windows-installer.nsi
```

The repack verifies the input manifest and ALVR pin, never replaces compiled files except the launcher given with `--launcher` (it checks that it is a 64-bit Windows program with the VROSTBridge version resource) and the driver given with `--native-overlay`, replaces `brand/` (the installer's icon) with `launcher/brand`, and rewrites `PACKAGED.json`, `HOST_PROVENANCE.txt` (with the launcher's and its source's SHA-256, and the driver's patch set) and `SHA256SUMS`. The `launcher/` tree is pruned to the source's, so no earlier artwork stays.

Without `--native-overlay` the previous native driver is reused, so the host does not include newer driver patches. RC4.2 to 1.0.0 were built that way over the payload before them (the RC4.1 driver); the overlay also carries the page fonts, the brand images, `licenses/`, `LICENSE`, `NOTICE` and `THIRD_PARTY.md`. The driver was rebuilt for RC3, RC4, RC4.1, 1.0.0.1 (where only its names changed), 1.0.0.3, 1.0.0.4, 1.0.0.5, 1.0.0.6 and 1.0.0.9 (and 1.0.0.10 to 1.0.0.13 passed 1.0.0.9's overlay again), each passed with `--native-overlay <dir>`, the output of `scripts/Build-Host-Cross.py`, whose manifest must record the complete current patch set. 1.0.0.1's previous payload was 1.0.0's, with the file names inside it made VROSTBridge's first and its build manifest left out (a one-off step outside the project, recorded in the build evidence). RC4 also passed `--drop-ffmpeg`, which removes the Windows FFmpeg DLLs and notice after checking that the rebuilt driver (built without ALVR's `gpl` feature) does not import them. RC2 was produced this way over the RC1 payload. A Linux `makensis` built from NSIS 3.09 source works with the official NSIS 3.09 `Include`, `Contrib`, `Stubs` and `Plugins` directories as `NSISDIR`.

The installer's identity is the install folder `$LOCALAPPDATA\VROSTBridge\Host\<version>`, the uninstall key `VROSTBridgeHost` and the Run value `VROSTBridgeHost`: a later version upgrades it in place and rewrites the sign-in command only when it was VROSTBridge's own.

`packaging/windows-launcher.c` builds both launchers (`--role host`, the default, or `--role client`). The host's strings and version stay written in the source and `windows-icon.rc` (the build refuses them when they differ from `launcher/version.py`), so its launcher is the same program as before: built from the 1.0.0.6 sources with that launcher's PE timestamp (`--timestamp 1791424578`), it is the released 1.0.0.6 file byte for byte (sha256 `e7f94404…6d51e5`). Without `--timestamp` the host's PE header carries the link time (6 bytes differ between builds); the client's carries a content hash (`/Brepro`).

## Windows client (from 1.0.0.7)

The headset PC's client for Windows 10 and 11 (x64), beside the Bazzite one. Inputs: the native client folder from the Windows client build ([BUILDING.md](../docs/BUILDING.md#windows-headset-client-1007): `scripts/Build-Client-Windows.py` installs into `dist/client-windows` `VROSTBridgeClient.exe`, `VROSTBridgeRuntimeProbe.exe`, `vrostbridge_transport.dll`, `avcodec-60.dll`, `avutil-58.dll`, `swscale-7.dll`, `openvr_api.dll` and `assets/`: the three shaders, the action manifest and bindings, the headset logo and the fonts), a verified host payload's embedded Python (`runtime/python`), clang/lld-link/llvm-rc with an xwin SDK (or Zig) for the launcher, and NSIS 3.09:

```sh
python3 packaging/build_windows_client.py --native dist/client-windows \
  --python /path/to/verified-host-payload/runtime/python \
  --sdk /path/to/xwin-sdk --makensis /path/to/makensis --nsisdir /path/to/nsis-3.09 \
  --output /tmp/vrostbridge-release [--work /tmp/vrostbridge-client-package]
```

`--launcher '/tmp/launcher/VROSTBridge Client.exe'` uses a launcher built beforehand (`build_windows_launcher.py --role client`) instead of `--sdk`/`--zig`. The version is `CLIENT_VERSION` in `launcher/version.py` (`core/version.hpp` must agree): the launcher, the installer and `PACKAGED.json` take it from there, and the build refuses native programs that do not say `VROSTBridge <version>`, a missing native file or asset, a non-x64 file, a program that imports a Visual C++ or MinGW runtime DLL the package does not carry (the client ships no `vc_redist`: its native programs link the C runtime statically), and FFmpeg DLLs not built from the source the package carries (`avutil-58.dll` must say 6.1.4). It refuses a `runtime/python` that differs from what the host payload's `SHA256SUMS` lists. It prints the SHA-256 of the setup EXE and the ZIP. Every payload file gets one time (`SOURCE_DATE_EPOCH`, else the last commit's) and mode, and the ZIP is written in UTC, so the same inputs give byte-identical packages.

The payload, which is the install folder `%LOCALAPPDATA%\VROSTBridge\Client\<version>` and the portable ZIP's `VROSTBridge-Client/` folder:

| Path | From |
| --- | --- |
| `VROSTBridge Client.exe` | the native launcher: `runtime\python\python.exe -u launcher\bootstrap.py --role client [--background\|--self-test]`, logging to `%LOCALAPPDATA%\VROSTBridge\client-native.log` |
| native programs and `assets/` | the native folder; its `licenses/` and `third_party/` notes are merged in, build by-products (`.pdb`, `.lib`) and companion copies are left out |
| `runtime/python/` | the host payload's embedded CPython, byte for byte |
| `launcher/` | the source's, without `game_ui_assets/` (only Game Mode, a Linux client feature, reads it) |
| `host/` | its Python modules (with the vendored websockets) and `default-session.json`, which the companion imports for both roles; no host programs or scripts, as in the Bazzite package |
| `docs/`, `licenses/`, `brand/`, `LICENSE`, `NOTICE`, `THIRD_PARTY.md` | the source (`brand/` is `launcher/brand`, the installer's icon) |
| `third_party/ffmpeg/` | FFmpeg n6.1.4's source tarball (`ffmpeg-n6.1.4-source.tar.xz`, the same file as the Bazzite package's) and `LICENSE.md`: the LGPL source travels with the DLLs, as in the Bazzite package. With them the Windows build's notes: the source tree's `third_party/ffmpeg/BUILD-Windows.txt` (any file there whose name contains *windows*), and any `third_party/` notes in the native folder; not the Linux build's `BUILD.txt` |
| `third_party/openvr/LICENSE` | OpenVR's licence, for `openvr_api.dll` |
| `CLIENT_PROVENANCE.txt` | where the runtime, the launcher and the native files came from |
| `PACKAGED.json`, `SHA256SUMS` | `{version, role: client, platform: windows, alvr_commit}`; every file's SHA-256, checked by `--self-test` |

`packaging/windows-client-installer.nsi` mirrors the host's installer and sits beside it on one PC: the uninstall key `VROSTBridgeClient` (*VROSTBridge Client* in *Settings → Apps*), the Run value `VROSTBridgeClient` (`"…\VROSTBridge Client.exe" --background`), and in the shared *VROSTBridge* Start menu folder *VROSTBridge Client*, *Client startup logs*, *Client open-source credits* and *Uninstall VROSTBridge Client* (only an empty folder is removed). Setup runs the staged launcher's `--self-test`, then `launcher/install_maintenance.py` with the payload's Python: `prepare-windows-client` (closes an open client companion), `commit-windows-client` (verified copy; a same-version repair is restored if it fails) and `finish-windows-client` (only the new version folder remains). *Start when I sign in* is off unless chosen (`/STARTUP` when silent); a repair or update keeps it on only when the entry is the client's own command and never replaces another program's. The uninstaller (`uninstall-windows-client`) closes the companion, restores SteamVR settings a session changed, removes the client's own firewall rules (`launcher/Client-Firewall.ps1 -Action RemoveFirewall`: one administrator prompt if any exist; if it is declined, the rules and the client's files stay and the uninstall stops) and asks whether to keep Baballonia (`%LOCALAPPDATA%\VROSTBridge\Baballonia`, about 0.9 GB once installed; Yes keeps it and is the default, No removes it through `launcher/baballonia.py`'s `remove_downloaded`, Cancel stops the uninstall; when silent it is kept unless `/REMOVEEYETRACKING` is given). It then removes the program folder, its Start menu shortcuts and uninstall entry, and the Run value only when it is the client's own command (a stale uninstaller of another version touches only its own files). Settings, pairing and logs in `%LOCALAPPDATA%\VROSTBridge`, Baballonia's own settings in `%APPDATA%\ProjectBabble` and a Baballonia the user installed stay.

`tests/test_windows_packaging.py` checks the payload against a stand-in native folder, the installer's identity beside the host's, the maintenance steps and, with `VROSTBRIDGE_WINDOWS_SDK` set to an xwin SDK and clang/lld-link/llvm-rc on the path, builds both launchers. Wine runs of the installer are no substitute for the Windows gate below.

## Release checksums

One `SHA256SUMS.txt` lists every file of a release, one `<sha256>  <file name>` line each: for 1.0.0.13 `VROSTBridge-Host-Setup-1.0.0.13.exe`, `VROSTBridge-Host-Portable-1.0.0.13.zip`, `VROSTBridge-Bazzite-Installer-1.0.0.13.zip`, `VROSTBridge-Client-Setup-1.0.0.13.exe` and `VROSTBridge-Client-Portable-1.0.0.13.zip`. `packaging/build_distributions.py` writes it only for the host and Bazzite products it builds, and `packaging/build_windows_client.py` only prints the SHA-256 of its two, so write the file once all five are final, in the folder that holds them:

```sh
cd /tmp/vrostbridge-release && sha256sum VROSTBridge-*-1.0.0.13.* > SHA256SUMS.txt
```

Each package also checks its own files against the `SHA256SUMS` inside it (its installer, or the launcher's `--self-test`); that manifest is separate from the release's `SHA256SUMS.txt`.

## Baballonia is never packaged

From RC4.5 the client downloads Baballonia itself, on the user's request, from Project Babble's GitHub release (`launcher/baballonia.py` pins the URL, size and SHA-256: the Linux archive, and from 1.0.0.7 for the Windows client the Windows installer of the same release). Do not add Baballonia, its models, its tarball or its installer to any package or to the source ZIP: its licence (Babble Software Distribution License 1.0) is non-commercial and copyleft, while VROSTBridge's packages are MIT; downloading it from Project Babble on the user's request, after the user accepts that licence, keeps the two separate. A new pin needs the new release's size and SHA-256 computed from Project Babble's own download, its `capture` provider name checked against that release's source, and the sandbox run described in `docs/BEYOND_EYE_TRACKING.md`. The Windows pin (`WINDOWS_RELEASE`) also needs the installed size, the SHA-256 of the three program files by which a Baballonia the user installed is recognised, and the version its uninstall entry shows.

## Required package gates

Run the exact frozen client's `--role client --self-test`; test managed upgrade, same-version repair, retained state, owned cleanup, a second launch, authenticated status/report/quit and damaged-package detection. Verify installed native executable loading outside the build environment. Retain archive CRC and SHA-256 checks.

On actual Windows, test the distributed setup EXE, native installed launcher and bundled Python, clean install/upgrade/uninstall, tray menu, quiet sign-in startup, missing default browser, spaced/non-ASCII paths, runtime dependencies and SteamVR/GPU execution. For the Windows client (1.0.0.7) also: the host and the client installed on one PC and each uninstalled alone, the uninstaller's firewall prompt (approved and declined) and its Baballonia question (Yes, No, Cancel), and the native client's programs and DLLs loading on a PC without the Visual C++ runtime. For 1.0.0.1 also: a fresh installation (earlier test builds removed first), the *VROSTBridge* Start menu folder, one *Settings → Apps* entry named *VROSTBridge Host*, sign-in startup, the firewall rules and the tray icon on light and dark taskbars. Static PE checks, cross-compilation or Wine do not replace this gate.

On Bazzite, for 1.0.0.1: a fresh installation (earlier test builds removed first), the menu entries with the `vrostbridge` icons, pairing, *Add to Steam*, then an upgrade over 1.0.0.1 keeping settings and pairing; for 1.0.0.2 Baballonia's *Eye* calibration in the headset over the stream (the layer's line in Baballonia's log); for 1.0.0.3 an upgrade over 1.0.0.2 whose last message says Baballonia was found and kept, Game Mode past the display reservation, and the uninstaller's Keep, Remove and Cancel; for 1.0.0.4 the network test run once more before the first session and the lower pacing it sets, the processor warning and *Set the GPU clock to automatic*, and Game Mode's D-pad and stick; for 1.0.0.5 the eye-camera helper's `STATS` lines in the client report, the network test repeated after a result without a clean rate, and on Windows the host's `VROST_COLOR` line with 10-bit colour on; for 1.0.0.6 *Gaze reach* on the Eye tracking card, the network test repeated once and the bitrate it sets, `controller_prediction` in the client report, and on Windows the host's `VROST_COLOR_PROBE` lines.

Create the source ZIP with `python3 scripts/package.py`. The historical root `VROSTBridge.exe` diagnostic is no longer built (its CMake target is gone in 1.0.0.7); never relabel an old copy as the Windows host or the Windows client. The project's licence is MIT (`LICENSE`); third-party components keep their own ([THIRD_PARTY.md](../THIRD_PARTY.md)). For the public source archive see [docs/PUBLISHING.md](../docs/PUBLISHING.md).
