# VROSTBridge 1.0.0-rc3 — host and client update

RC3 builds on RC2 (prediction, controller decoupling, full-length haptics, cadence diagnostics, Wi-Fi power-save control). It aims to make a networked session feel like a headset plugged into the host in another room: frames that arrive just in time, faster starts, a sharper image per megabit, and a single line that shows where every millisecond goes.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc3.zip`. Windows host `VROSTBridge-Host-Setup-1.0.0-rc3.exe`, which includes a SteamVR driver rebuilt from all seven patches (see *Host driver build* below). The RC3 client works with RC1, RC2 and RC3 hosts. Features that need the new host switch themselves on only when the host advertises them. Settings, pairing and logs are kept.

## What changed

### Frames arrive just in time: host frame-timing lock (patch 0007; needs a new host driver)
The pinned ALVR host paces the game with a virtual vsync timer that starts when the client connects and then runs freely. Its phase against the real headset panel is therefore random for every session, and it drifts with the small clock difference between the two PCs. Depending on that phase, each frame either waits most of a refresh interval before the headset can show it, or lands right at the headset's deadline, where small jitter alternates hits and misses (repeated and skipped frames). This fits the reports of inconsistent first launches and intermittent judder.

The client already reports, for every frame, how long the decoded frame waited for the headset's display slot. Patch 0007 makes the host steer its virtual vsync from that report, using circular statistics and a PI controller, so frames land a small, measured margin before the headset needs them:
- one step to the target at session start;
- then corrections of at most 0.5 ms per update;
- an integral term follows the clock drift between the PCs.

In a closed-loop simulation with 0.6 ms jitter and ±300 ppm drift, every starting phase settled at about a 3.7 ms wait with no repeated or dropped frames. Without the lock, the average wait ranged from 3.7 to 11.5 ms depending on the starting phase, and the worst phase repeated 50 frames and dropped 51 in 20 seconds.

- **Host-only.** No client or transport change is needed.
- **Included in the RC3 host installer.** The installer's driver is built from all seven patches. The host companion detects support from the driver's build manifest and writes the per-session flag `vrostbridge-timing.json`. When the flag is absent or false (or with an older driver), the virtual vsync stays free-running, so other ALVR use is unchanged.
- **Toggle:** client page → *Frame pacing* → **Frame timing lock** (on by default).
- **Diagnostics:** host logs `VROST_VSYNC_LOCK` lines, and the session quality line shows *timing lock on* while it is steering.

### Faster starts: both PCs start SteamVR together (client)
Previously the client started its own SteamVR, waited for the headset display, and only then asked the host to start. Now the client remembers the headset verified in the last good session and starts the host at the same time as its own SteamVR. If the freshly detected headset differs (another headset, display mode or render size), the host is restarted with the correct profile, which costs about what the old serial start did. The first session after installing RC3 still starts serially, then saves the headset.

The native client logs `First remote frame shown … ms after the native client started` and, after a network drop, `Stream resumed: first new frame … ms after the disconnect`, so start and recovery times can be compared across builds.

### Session quality line (host and client)
The host companion already received ALVR's per-frame latency breakdown but kept only the latest sample. It now keeps a 5-second window and sends a small summary in the session heartbeat. While streaming, both pages show:

> Pipeline 38.0 ms (p95 44.0): render 8.0 · encode 4.0 · network 6.0 · decode 5.0 · display wait 15.0 · frames wait 3.7 ms for the headset (±0.6) · timing lock on

The total is measured from the tracking sample to the headset's display slot (panel scan-out not included). It is not a camera-measured motion-to-photon figure. Reports include the same summary.

### Sharper image per megabit (host profile)
- **H.264 uses CABAC** instead of CAVLC: typically about 10% more efficient for the same picture. VA-API decoders handle it in hardware. HEVC already uses CABAC.
- **Encoder priority** (client page → *Frame pacing*): *Lowest latency* (default, unchanged) or *Balanced quality* (AMF Balanced / NVENC P4, a little more encode time). The quality line shows the encode cost.

### Lower audio delay on Ethernet (host profile)
When the client streams over a wired adapter, the game-audio buffer drops from 50 ms to 30 ms. Wi-Fi keeps 50 ms.

### Host add-on check (host)
The host page and session log list other SteamVR add-ons that load on the rendering PC and may act on the virtual headset (for example a Bigscreen Beyond helper that tried IPD changes in an earlier report), and say where to disable them. This check is read-only and never changes SteamVR settings.

### In-session video changes (client)
If the host encoder restarts with new parameters, the client replaces its decoder without ending the session. If the stream size changes after a reconnect, the GPU frame mapping is rebuilt and idle surfaces of the old size are recycled. Previously both cases needed a client restart.

### Host driver build
The RC3 host's SteamVR driver (`driver_alvr_server.dll`) is rebuilt from the pinned ALVR revision with patches 0001–0007, using the same toolchain as RC1: Rust 1.97.1, Clang/LLD 19.1.1 (MSVC ABI), MSVC CRT 14.44 and Windows SDK 10.0.26100 (static CRT), and a static oneVPL 2.15.0. Compared with the RC1 driver, its exported functions and every imported DLL symbol are identical. All its FFmpeg and OpenVR imports resolve against the FFmpeg 8.1 (n8.1.2) and OpenVR DLLs that RC1 shipped, which RC3 keeps byte-for-byte. The embedded Python runtime, launcher and helper programs are also unchanged from RC1.

## Not in RC3, and why
- **10-bit HEVC.** The pinned host refuses 10-bit unless the client advertises it, and that flag lives in the client's Rust transport. The transport can now be rebuilt here, so this is planned for the next client build, once the timing lock has been tested on hardware.
- **Windows-side DSCP marking.** On PCs that are not on a Windows domain, QoS policies take effect only after a system-wide registry change and a reboot. That is too invasive for an untested installer step.
- **Keeping host SteamVR warm after Stop.** This changes what Stop means (the game would stay open) and needs Windows lifecycle testing. Parallel start delivers most of the startup gain.
- **Intra-refresh loss recovery.** This needs per-GPU encoder testing alongside ALVR's keyframe recovery.

## How to test (please report each)
1. **Install** client RC3 and host RC3 (run the host installer over RC1). Check both pages show 1.0.0-rc3 and your settings are unchanged.
2. **Quality line:** in a fixed scene, note the pipeline line on Ethernet and on Wi-Fi. Save a report for each.
3. **Startup:** Start once (serial, saves the headset), Stop, then Start again; the second start should be quicker. Note the `First remote frame shown` times from both reports.
4. **Encoder priority:** compare *Lowest latency* and *Balanced quality* in the same scene, looking at image detail and the encode figure.
5. **Index:** with H.264 now using CABAC, check image quality at your usual bitrate.
6. **Host add-ons:** if the host page lists any, disable them in SteamVR and retest.
7. **Frame timing lock:** check that the line shows *timing lock on* and *frames wait* settles near 3–4 ms. Check that judder from frame repeats is gone across several Stop/Start cycles.

Build and test evidence: [evidence/rc3-build-1.0.0-rc3.json](../evidence/rc3-build-1.0.0-rc3.json).
