# Historical packaging notes

Superseded by [the current packaging guide](../../packaging/README.md). Versioned instructions below describe earlier releases and are not current commands.

# Release packaging

Normal users install the release packages; these steps are for maintainers.
Version 0.2.8 uses NSIS 3.09's offline per-user installer, a native x64 Win32 entry
point and bundled Python 3.13.12 on Windows. It no longer appends a ZIP to a custom
EXE or runs an encoded PowerShell command at startup.

Freeze the client launcher with Python 3.12.14 and PyInstaller 6.22.3:

```sh
python -m PyInstaller --noconfirm --onedir --name VROSTBridge-Client \
  --paths "$PWD" \
  --add-data "$PWD/launcher/desktop.html:launcher" \
  --add-data "$PWD/launcher/brand:launcher/brand" \
  --add-data "$PWD/launcher/game_ui_assets:launcher/game_ui_assets" \
  --distpath /tmp/vrostbridge-frozen launcher/bootstrap.py
```

Build `dist/client/VROSTBridgeClient` from the current source first. Keep its
matching `libvrostbridge_transport.so`, decoder libraries and shader together.
Then run:

```sh
python packaging/build_distributions.py \
  --work /tmp/vrostbridge-package --output /tmp/vrostbridge-release \
  --host-input /path/to/verified-host-payload \
  --frozen /tmp/vrostbridge-frozen/VROSTBridge-Client \
  --zig /path/to/zig --makensis /path/to/makensis \
  --nsisdir /path/to/nsis/share
```

The host input is the exact previously verified Windows payload, including its
SHA256SUMS, PACKAGED.json and HOST_PROVENANCE.txt. The assembler checks every input
file and ALVR source revision before reusing only runtime/, dist/host/ and the
provenance record. It overlays current launcher, setup and documentation files and cross-compiles
`host/vr_helper.cpp` with Zig against the pinned OpenVR C function tables. Do not
substitute compiler-specific C++ OpenVR interfaces on Windows.
Do not use an installed or modified user's runtime directory as this input.
Original downloaded archive digests are retained in INPUT_SHA256.json.

The output also includes a portable host ZIP using the same native launcher,
SHA256SUMS.txt, and a Bazzite installer ZIP. Run the exact frozen client package's
`--role client --self-test`, the installer, a second launch, token-protected HTTP
checks and damaged-package detection. On Windows, run the distributed setup EXE,
its installed native launcher and bundled Python; check fresh install, repair,
missing default browser, spaced/non-ASCII paths and repeat launch. That Windows
gate must be performed on Windows. Static PE and archive checks do not replace it.

The full NSIS notice must remain in licenses/NSIS.txt. Preserve the upstream host
licences under dist/host/licenses, which include the Windows FFmpeg GPL build's
notices; the separate Linux FFmpeg runtime has different build and licence terms.

Create the reviewed source archive with `python scripts/package.py` after adding
release evidence. Never publish a historical root `VROSTBridge.exe` diagnostic
as the current host launcher.

Game Mode adds VROSTBridgeLease, libwayland-server and SDL2 to the Linux payload. Native UI assets must be included in the frozen launcher. The Windows tray uses the Win32 API and shared .ico; no extra Python tray dependency is needed.

0.2.8 is a companion/lifecycle fix. Its native Linux streaming and display-lease binaries are reused byte-for-byte from verified 0.2.6; the native core diagnostic banner remains 0.2.6. The companion, installer and package identities are 0.2.8. Rebuild the native core only when changing its source.

0.2.8 also builds `host/audio_probe.cpp` into `VROSTBridgeAudioProbe.exe`, using read-only Windows Core Audio enumeration. The packaged host must include this helper for opt-in microphone preflight. No virtual audio driver is bundled or installed.

## 0.2.9

The native Linux client must be rebuilt for live eye geometry and runtime-event handling. Reuse only the unchanged ALVR transport, FFmpeg libraries, shader and lease helper. The executable must have basename-only ELF NEEDED entries and RUNPATH `$ORIGIN:$ORIGIN/lib`, and run without a private LD_LIBRARY_PATH. Rebuild the frozen companion for the environment and diagnostic fixes. The Windows payload includes the updated tray module.

## 0.2.10

Native client rebuilt with request-before-config video startup and paced recovery.
Host/client companions include bounded repeat summaries, retained startup
milestones and video-startup UI states. Pinned ALVR host/transport, FFmpeg and
Game Mode lease helper remain unchanged. Use the 0.2.9 verified host payload
as the assembler input; run the 0.2.9-to-0.2.10 upgrade smoke check.

## 0.2.11

Native Vulkan synchronization and bounded late-frame selection rebuilt. Host
profile adds native Index passthrough mappings, preserving explicit custom maps.
Use the verified 0.2.10 host payload as input and run the real 0.2.10 upgrade
smoke gate. Late-frame selection can be compared through the desktop UI.

## 0.2.12

Both companions and the native client are rebuilt for physical HMD metadata,
explicit resolution choices and negotiated-video diagnostics. Existing managed
upgrade behavior, settings retention, pinned upstream binaries and credits remain.
See docs/RELEASE_REVIEW_0.2.12.md for limits and physical comparison steps.

## 0.2.13

Rebuild the native Linux client for skeletal forwarding and the split decode/
conversion pipeline. Rebuild both companions for automatic Beyond display
profiles. ALVR host and transport stay on the verified source pin; the new client
uses its existing 26-joint skeleton protocol. Reuse the verified 0.2.12 Windows
host payload, run all contracts and real 0.2.12-to-0.2.13 install/upgrade/cleanup
checks, and verify the final package bytes. Physical validation remains required.
