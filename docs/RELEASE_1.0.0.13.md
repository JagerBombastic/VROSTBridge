# VROSTBridge 1.0.0.13

Follow-ups from 1.0.0.12's first session on real hardware, and the stream chosen before a session: an Index's refresh rate, set in SteamVR for it, and a Beyond's stream following the mode it is in. Every package carries **1.0.0.13**, and the [1.0.0.12 notes](history/RELEASE_1.0.0.12.md) still apply.

**Install** over 1.0.0.12 on each PC: `VROSTBridge-Host-Setup-1.0.0.13.exe` on the rendering PC, and `VROSTBridge-Bazzite-Installer-1.0.0.13.zip` or `VROSTBridge-Client-Setup-1.0.0.13.exe` on the headset PC. Settings, pairing, the network test, Baballonia and its calibration stay. Either PC still works with the other at 1.0.0.12; a Beyond in its 90 Hz mode then streams 2016 per eye, as 1.0.0.12 did.

## What changed
- **The stream, chosen before a session** (headset PC). The choices come from the last headset seen (on Bazzite also the one plugged in), so they no longer wait for Start. Index: *Native* (2016 × 2240) at 80, 90, 120 or 144 Hz (experimental); VROSTBridge sets the rate in SteamVR for the session and puts yours back. Beyond: no choice. The stream follows the mode the headset is in, 2560 per eye at 75 Hz or 1920 at 90 Hz (upscaled in the headset), because a larger stream only costs decoding and a smaller one loses detail; the mode is switched with Bigscreen's Beyond Utility on a Windows PC. Until 1.0.0.12 a Beyond started at 2016, a size its panels never show. *Auto* follows the headset as it is set. When this PC's graphics fall behind, an Index is offered its next lower rate, and a Beyond at 2560 is pointed at its 90 Hz mode.
- **More parity on a wired link** (rendering PC). The session lost 12 frames although each had its parity: a wired path can drop packets in clumps, and one parity packet per ten rebuilds a run of lost packets only up to a tenth of a frame. With a wired headset PC it is now one per five: a run up to a fifth of a frame comes back, for 20 % more packets. Wi-Fi resends lost packets itself and keeps one per ten.
- **Stopping while SteamVR starts** (rendering PC). ALVR asks SteamVR to quit only once a headset has streamed, so a stop before that waited out the whole 90 s start. SteamVR's own *Exit VR* now follows, and the wait counts from when SteamVR's server appears.
- **The network adapters' drop counters** in both reports: packets in and out and every drop and error counter that moved during the session, on both PCs, and on Linux also the system's UDP receive errors and the driver's own counters. They show where packets are lost: on the rendering PC, on the way, or on the headset PC.
- **The remembered GPU clock** in the report (Bazzite): what is remembered, and whether the session held it from the start.
- **Game Mode** still fails. The report now says which process started SteamVR's server, whether it runs in a container, and which display its own name for it reaches, so the next attempt says why.

## How to test
Play as before, wired, with *Use recommended settings*, and send both reports. They should show fewer lost frames, `adapter_counters` on both PCs and, from the second session, `session_tuning.last_session.held_from_start`. A stop while the rendering PC's SteamVR is still starting should end within about ten seconds. Before Start, choose 120 Hz and then 144 Hz on an Index: the page says when it runs another rate than the one chosen (the log's `DISPLAY_MODE` line), and SteamVR's own setting is back after Stop. On a Beyond the stream resolution shows the mode it is in and offers nothing to choose; switch the mode with the Beyond Utility and the stream follows (2560 at 75 Hz, 1920 at 90 Hz).

## Known limits
- The rendering PC's occasional stalls are not fixed; they just no longer cost bitrate.
- The menu button reaches the rendering PC as a short press, because the headset PC's SteamVR takes the button while it is held. The 1.0.0.12 session's host log suggests that opens the dashboard; not confirmed yet.
- VROSTBridge doesn't switch a Beyond between its 75 and 90 Hz modes: set the mode with Bigscreen's Beyond Utility on a Windows PC. 144 Hz on an Index is experimental: if SteamVR won't start at it, choose 120 Hz.
- Game Mode on Bazzite doesn't start sessions yet, and the Windows client hasn't run on real hardware.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.13.json](evidence/vrostbridge-build-1.0.0.13.json).
