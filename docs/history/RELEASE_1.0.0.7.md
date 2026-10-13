# VROSTBridge 1.0.0.7

1.0.0.7 adds a **Windows client**: the headset PC can now run Windows 10 or 11 (64-bit) as well as Bazzite. It is the same client, built for Windows: the same page and session flow, the same stream and re-aiming, eye-tracked foveation with Baballonia, reports and network test, with a tray icon and Windows' own settings handled for you. Game Mode stays with Bazzite; Windows doesn't need it. The host's driver is unchanged; every VROSTBridge package now carries **1.0.0.7**, so the versions always match. Everything in the [1.0.0.6](RELEASE_1.0.0.6.md), [1.0.0.5](RELEASE_1.0.0.5.md), [1.0.0.4](RELEASE_1.0.0.4.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** `VROSTBridge-Host-Setup-1.0.0.7.exe` over 1.0.0.6 (do not uninstall it first).
- **Headset PC with Windows:** `VROSTBridge-Client-Setup-1.0.0.7.exe`. It installs for your Windows account only and needs no administrator rights. `VROSTBridge-Client-Portable-1.0.0.7.zip` holds the same files without an installer.
- **Headset PC with Bazzite:** `VROSTBridge-Bazzite-Installer-1.0.0.7.zip` over 1.0.0.6, as before. Settings, pairing, Baballonia and its calibration stay.

A Windows headset PC is a new client to the host, so allow it once, just like a new Bazzite client (the host does this by itself while *Allow home-network clients* is on).

## The Windows client

### What it is
- **The same native client**, built for Windows: an OpenVR scene app on the headset PC's own SteamVR, with ALVR's transport, the one-pass frame shader (re-aim, foveation, sharpening, colour) and the in-headset status cards. The C runtime is built in, so no Visual C++ package is needed.
- **Decoding on the GPU SteamVR uses**, through Direct3D 11 video decoding (D3D11VA) and FFmpeg 6.1.4 (LGPL, built for Windows from the same source as Bazzite's). Each decoded picture is then copied into the frame shader. That copy is the main difference from Bazzite, where the picture is shared without one; it is described under *Known limits*. H.264 and HEVC everywhere; 10-bit and AV1 only where the GPU's decoder offers them, and the client offers the host only what this PC can decode. *CPU decoding* remains for troubleshooting.
- **The same page**, with Windows wording where Windows differs, and the same actions: pairing, network test and automatic bitrate, stream size, foveation, sharpening, eye tracking, reports to the host, the session timeline, gaze reach and the controller checks.

### Made for Windows
- **Tray icon:** *Start session* and *Stop session* without opening the page, the session's state at a glance, and a notification when streaming starts, when a session stops on its own, and when one does not start (with the reason).
- **Start when I sign in** (in the installer and on the page): VROSTBridge Client starts quietly in the tray; no browser or SteamVR opens.
- **Firewall in one step:** *Allow home-network connection* adds Windows Firewall rules for exactly the ports the Bazzite client opens, for private networks and the local subnet only, after one administrator (UAC) prompt. The page also warns when Windows treats your network as Public, or when a declined firewall prompt left a rule that blocks VROSTBridge. Uninstalling removes the rules.
- **Wi-Fi for streaming:** during a session Windows' media streaming mode is on and its background Wi-Fi scans are off (they pause the stream every few minutes); both return at Stop. **Turn off Wi-Fi power saving** sets the power plan's wireless setting to Maximum Performance, when you choose it. On Windows 11 24H2 and later, reading the Wi-Fi network's name needs location permission for desktop apps: the page says where to allow it, and everything else works without it.
- **Power:** while a session runs, the PC and its display stay awake and Windows' power mode is *Best performance*; your own mode comes back at Stop, and at the next start if a session ended in a crash.
- **SteamVR:** found wherever Steam put it. If SteamVR has never run on this PC, Start says so in plain words. If SteamVR's compositor crashes, the session recovers as on Bazzite, and the report includes what Windows recorded about it (application errors, hangs and graphics driver resets).
- **Eye tracking (Bigscreen Beyond 2e):** Baballonia's Windows installer is downloaded only when you ask, checked against its exact size and SHA-256, and installed privately for VROSTBridge (no extra Start menu entries). If you already installed the same Baballonia version yourself, VROSTBridge uses yours and never removes it. Baballonia reads the headset's eye camera directly, so close Bigscreen's own eye-tracking app first. Calibrate once in Baballonia's window during a session; the calibration shows in the headset over the stream.
- **Stopping cleanly:** the companion asks the native client to finish (Windows has no Linux-style signal for this) and waits for SteamVR to close, as on Bazzite.

### Bazzite only
Game Mode, *Add to Steam*, the headset display reservation, the eye-camera helper, the calibration launcher and its Vulkan layer, and *Fix SteamVR reprojection* belong to Linux and Bazzite. On Windows they are hidden from the page, and their actions explain why if called.

## Also in 1.0.0.7 (both clients)
- **One-pass frame path on more GPUs.** The frame shader needs 208 bytes of fast shader parameters (push constants); some drivers offer only the minimum of 128 (some AMD drivers on Windows). Those GPUs now get a variant of the same shader that reads the re-aim part from a small buffer, instead of the older two passes without sharpening. The tests run every case through both versions and compare them byte for byte (23 runs, none different, on Linux and under Wine).
- Long messages, such as a Windows path, wrap on the page instead of widening it.
- **Licence texts:** the packages carry the full licence text of every Rust dependency, now including the Windows client's own, and 33 texts from the projects' repositories that the Bazzite package had been missing.
- **Version 1.0.0.7** for the host, the Bazzite client and the Windows client. The host's driver is the same build as 1.0.0.6 (patches 0001 to 0020).
- **The host's companion** shares its code with the Windows client, so it now lists the PC's network adapters through Windows' own interface, as the client does, instead of a PowerShell query (still used if that interface does not answer). It should behave exactly as before.

## How to test the Windows client (please report each)
1. **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.7.exe` over 1.0.0.6. The page shows *VROSTBridge Host 1.0.0.7*.
2. **Headset PC:** install Steam and SteamVR, connect the headset, run SteamVR once and finish its room setup, then close SteamVR.
3. Run `VROSTBridge-Client-Setup-1.0.0.7.exe` (if Windows says *Windows protected your PC*, choose **More info → Run anyway**; it is not code-signed yet) and open VROSTBridge Client. The page shows *VROSTBridge Client 1.0.0.7*.
4. **Allow home-network connection** and accept the administrator prompt. Check that the client finds the host (allow it on the host page if *Allow home-network clients* is off).
5. **Network test**, then **Start**. Watch the frame rate and *lost frames* on the session card for a few minutes, move your head and controllers, and note anything that looks wrong. The video details show the decoder in use.
6. **Tray:** stop the session from the tray icon, start it again from there, and check the notifications.
7. **Eye tracking (Beyond 2e):** close Bigscreen's eye-tracking app, turn on *Eye tracking*, set up Baballonia (a 423 MB download), start a session, choose **Show Baballonia** and calibrate as the page describes. Then check that the sharp region follows your eyes, and read *Gaze reach*.
8. After Stop, check that Windows' power mode is back to yours, and that Wi-Fi behaves as before.
9. **Send reports from both PCs** after a session of several minutes.

## Known limits
- **The Windows client has not run on real hardware yet.** It was built and tested here: the companion's tests, the native tests under Wine with a software Vulkan driver, and the companion and its page started under Wine. Expect first-run problems on a real PC; the reports are the quickest way to find and fix them.
- **The copy:** every decoded picture goes from the GPU to system memory and back before it is shown. A Standard stream is about 12 MB a picture in 8-bit (twice that in 10-bit), each way; eye-tracked foveation brings it under 4 MB. On a handheld that shares memory between processor and graphics this costs memory bandwidth and a little time, which the timing details report. Sharing the picture with the frame shader directly, without the copy, is the next step if reports show it matters.
- **Baballonia's Windows installer** is pinned at v1.1.1.0rc6, a release candidate. Its silent installation by VROSTBridge, and its calibration overlay on Windows, have not been seen on hardware yet.
- Windows may ask for location permission before it tells any program the Wi-Fi network's name (24H2 and later). Without it VROSTBridge cannot notice roaming between access points; streaming is unaffected.
- The host's driver is unchanged since 1.0.0.6, which has also not met hardware yet.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.7.json](../evidence/vrostbridge-build-1.0.0.7.json).
