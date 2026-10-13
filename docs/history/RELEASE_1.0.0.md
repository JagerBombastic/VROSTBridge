# VROSTBridge 1.0.0: the first release build

**VR outside the box.** *Think outside the box. Render there too.*

1.0.0 was the first release build of VROSTBridge, a test build that [1.0.0.1](RELEASE_1.0.0.1.md) replaces and keeps everything of. It brought everything since the last test build (1.0.0-rc4.5): Baballonia's calibration over the stream, Baballonia kept out of the way, one page tab, the licence and notices for publishing the source, and the logo.

It installed over 1.0.0-rc4.5 on both PCs, keeping settings, pairing, calibration, logs and the downloaded Baballonia.

## The logo and the look
- **The logo:** a cube in front of a dome, in colours running from cyan to magenta. The pages, the Game Mode screen, the headset cards and the Steam artwork use its colours.
- **What the name means:** under *VROSTBridge* at the top of both pages and the Game Mode screen: *VR Rendering OutSide The Box*, with the letters V-R-O-S-T picked out (1.0.0.1 adds *Bridge*). The badge on the right still says which PC you are on.
- **Icons that stay clear when small:** the 16 to 32 pixel icons are fitted to the pixel grid. The Windows tray icon is a one-colour symbol, white on a dark taskbar and dark on a light one, and follows the taskbar if you change it.
- **Windows:** `VROSTBridge Host.exe` has its own icon and version details.
- **Bazzite:** the menu entries (*VROSTBridge Client*, *VROSTBridge Game Mode* and *Uninstall VROSTBridge Client*) have the icon in every size the desktop uses.
- **Steam Game Mode:** an update with new artwork brings the library entry's pictures up to date, unless you chose your own.

## Since 1.0.0-rc4.5

These follow the first RC4.5 test with the Beyond 2e: Baballonia's calibration showed nothing in the headset at first; with SteamVR started by hand it showed, but with each eye given the other eye's picture; during a session it was the right way round, but after a moment the session closed. Also, Baballonia's window opened over the page at the start of a session, and opening the page from the tray icon or the taskbar pin opened another browser tab each time.

### 1. The calibration shows over the stream, and the session keeps running (client)
**Why the session closed:** on Linux, Baballonia starts its calibration program through OpenXR, as a new SteamVR application. SteamVR then asks the application showing the stream (VROSTBridge's client) to quit. The log said *Local SteamVR requested this client to quit*: the session stopped, SteamVR closed, and Baballonia with it.

**The fix:** VROSTBridge now starts the calibration as a **SteamVR overlay**, the way Baballonia calibrates with SteamVR on Windows. It is drawn over the stream, which keeps running.
- Baballonia itself is unchanged. VROSTBridge puts a small launcher of its own where Baballonia looks for its calibration program (`Calibration/Linux/Overlay/BabbleCalibration.x86_64`) and keeps Baballonia's program beside it as `BabbleCalibration.program`.
- While SteamVR runs, the launcher starts that program with Baballonia's own `--use-openvr` option instead of OpenXR. Without SteamVR it passes everything on unchanged, so a calibration started with no session still opens on the screen.
- The swapped eyes came from the OpenXR way with SteamVR started by hand. The overlay is placed in the scene by SteamVR's compositor, which draws each eye from its own position. This needs your next test (see *How to test*).
- If the overlay way ever needs to be bypassed: `VROSTBRIDGE_CALIBRATION=openxr` in the companion's environment keeps Baballonia's own way, and only then does VROSTBridge point Baballonia at SteamVR's OpenXR runtime (`XR_RUNTIME_JSON` = SteamVR's `steamxr_linux64.json`).

### 2. Baballonia keeps out of the way (client)
- For a session in Desktop Mode, Baballonia starts **minimised**, so its window no longer covers the page.
- **Show Baballonia** on the *Eye tracking* card brings its window back (for calibrating). *Open Baballonia* still shows it straight away. Once you have shown it, it is not minimised again during that session.

### 3. One page tab (host and client)
Opening the page again (*Open VROSTBridge* on the Windows tray icon, the Bazzite taskbar pin) still opens it in your browser, but an older tab now closes itself when a newer one opens, so you keep exactly one. (A browser cannot be told from outside to switch to a tab it already has, so the newest is the one kept.) If the browser does not let the old tab close itself, it says *VROSTBridge is open in a newer tab*. The pages now use the same local address each time (port 9947 on the client, 9948 on the host, or any free port if that one is taken), so this also works after the companion restarts.

### 4. Clearer guidance on the card (client)
- **Which calibration:** the card and the Game Mode row name Baballonia's four: **Eye** (the first one: a short tutorial, where you look, your blinks, then training; use it the first time), **Eye (no tutorial)** (the same, for later runs), and **Gaze** and **Blink** (redo one half; each needs an earlier full **Eye** run).
- **Where it would show:** while Baballonia runs without SteamVR, the card says a calibration started now would show on this screen and to start a session first. With SteamVR started by hand it says to calibrate during a session.
- **While training:** keep the session running until it has finished. Stopping the session ends SteamVR on this PC, which closes Baballonia.

### 5. Ready to be published (both)
- **Licence:** VROSTBridge's own code is under the **MIT licence** (`LICENSE`), by JagerBombastic.
- **Notices:** a `NOTICE` file and `THIRD_PARTY.md` list everything the packages include, with each licence; both installers carry `LICENSE`, `NOTICE`, `THIRD_PARTY.md` and `licenses/`. Added: NVIDIA's required statement for the encoder sample code inside ALVR's Windows driver, the MIT notices of the AMD AMF, NVENC and backward-cpp code there, the fonts inside ALVR's dashboard, the libraries inside the frozen Linux companion, the Apache-2.0 part of websockets, the DRM lease protocol, and where the source of the MPL-2.0 Rust crates is.
- **Eye camera helper:** go-bsb-cams used a small MJPEG module whose authors never stated a licence. It now has a small MJPEG server of its own (`third_party/go-bsb-cams/mjpeg_stream.go`): the same stream for Baballonia, each picture sent as soon as it is ready. Its changes are listed with its source.
- **About:** both pages and Game Mode say the version, the licence and that VROSTBridge is an independent project.
- **Privacy:** test data contains no real network addresses or device serials.

### Smaller fixes
- An X11 error from a window that closed at the wrong moment can no longer stop the companion (its error handler is installed once, for the whole process).
- Baballonia is no longer minimised after you have chosen *Show Baballonia*, even if its window appears late.

## Calibrate
1. Desktop Mode: turn on *Eye tracking* and start a session with the Beyond 2e.
2. Choose **Show Baballonia** on the *Eye tracking* card. Put the headset on.
3. In Baballonia's window choose **Eye** and follow it in the headset, where it shows over the stream.
   - Hold your gaze on the dot while you move your head slowly around it, as it asks; then close your eyes when it asks (about 20 seconds).
   - It then trains a model for your eyes: 5 to 10 minutes by Project Babble's figure, longer on a handheld's processor. **Keep the session running** until it has finished; the log says *Baballonia saved its calibration*.
4. Stop and start the session. The sharp region now follows your eyes, in Desktop Mode and Game Mode.

## Known limits
- Not tried on real hardware: the overlay calibration and its picture in each eye, the icons on Windows and in Steam. [1.0.0.1's test](RELEASE_1.0.0.1.md#how-to-test-please-report-each) covers them.
- The Windows programs are not code-signed, so Windows may say *Windows protected your PC* (choose **More info**, then **Run anyway**).
- Baballonia's calibration and training run on the processor, beside the stream, on the handheld's shared power limit.
- Baballonia's licence allows personal, non-commercial use only; VROSTBridge downloads it only when you accept that.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.json](../evidence/vrostbridge-build-1.0.0.json).
