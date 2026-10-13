# VROSTBridge 1.0.0-rc2 — host and client update

Install **both** packages: Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc2.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc2.exe` (when supplied). The RC2 client also works with the existing RC1 host; the host update adds the network and DSCP changes below. Settings, pairing and logs are kept on both PCs.

The aim of this release is to make a networked session feel closer to a headset plugged into the host: lower felt latency, controls that never drop during brief tracking or focus changes, and diagnostics that name the cause of uneven frame delivery. Codec, resolution, bitrate, optics and the host video pipeline are unchanged, so comparisons with RC1.3 isolate these changes.

## What changed

### Lower felt latency: client-side motion prediction (client)
RC1.3 sent each head and controller pose as sampled *now*. The pinned ALVR server only looks poses up by timestamp; it does not extrapolate the head. *(Correction, RC4.1: this was wrong. The pinned server does extrapolate every pose by its measured latency, so RC2–RC4 predicted twice. RC4.1 samples current poses and predicts once; see [RELEASE_1.0.0-rc4.1.md](RELEASE_1.0.0-rc4.1.md).)* Games therefore rendered for where you were a full stream round-trip earlier. Local SteamVR's reprojection hid part of the head-rotation error but not head position, and in-game hands lagged by the whole pipeline.

RC2 does what the ALVR reference client does. It samples poses for `now + measured pipeline latency`, uses ALVR's own estimate (capped at the host's existing 100 ms `max_prediction_ms`) and sends that target time as the frame timestamp. Local SteamVR then corrects only the remaining prediction error. Details kept correct by tests:

- Frame freshness, the 100/250 ms hold windows and *Frame age* use the real sampling time, not the future target.
- Target timestamps are strictly increasing, so frames cannot be reordered when the latency estimate falls.
- Index finger data keeps using the native (higher-fidelity) path. Each bone sample carries a fixed transport time that satisfies the patched host's 50 ms check.
- Gaze freshness still uses receipt time.

**Toggle:** client page → *Frame pacing* → **Motion prediction** (on by default; applies on the next session). Turn it off to compare against RC1.3 behaviour. Diagnostics show `prediction_ms`.

### Controllers no longer drop with head tracking or compositor focus (client)
In RC1.3, controller input and tracking were gated on the head pose being valid *and* SteamVR's `WaitGetPoses` succeeding. A brief headset occlusion or a late compositor frame released every button on both hands and told the host the headset had been removed. Now:

- Input focus follows `IsInputAvailable()` only.
- Controls, skeletons and proximity follow *streaming with the headset connected*.
- Per-hand optical loss, radio disconnect and real focus loss behave as before.

### Haptics keep their real length (client)
RC1.3 cut every vibration to 100 ms. RC2 honours game durations up to 1 s (the bound protects against a lost stop). Zero amplitude stops a hand's vibration immediately. A zero-duration "minimal pulse" becomes a 10 ms pulse instead of being dropped. The non-overlapping 8 ms slicing that fixed Lighthouse queue overflow is retained.

### The native client never blocks on its log pipe (client)
Render and tracking threads used to write diagnostics synchronously into the companion's pipe, so a stalled reader could freeze the headset image and tracking. Diagnostics now go through a bounded queue drained by a writer thread:

- When the reader stalls, telemetry is dropped and counted (`diagnostic_lines_dropped`).
- Lifecycle and error lines are kept, in order.

### Half-rate presentation is named, not just measured (client)
A steady 37 fps on a 75 Hz headset never tripped the 40 ms stall trace. Each one-second `TIMING` window now carries:

- `cadence`:
  - `full`
  - `client_throttled`: SteamVR on the headset PC is running the stream below refresh (its own throttle count is now recorded).
  - `source_limited`: fewer frames arrive from the host or network.
  - `late_or_paired`: frames arrive but miss the headset deadline.
- `packet_fps`, `compositor_throttled_frames`, `compositor_reprojected_percent` and `prediction_ms`.

A `PRESENTATION_CADENCE` line is logged when the class changes (after two consecutive windows), and a degraded change opens a report incident with surrounding context. The client page shows a plain-language note while it applies.

### Wi-Fi power saving off for the session (client)
Linux Wi-Fi power saving causes periodic stutter for streaming. When the stream uses a Wi-Fi adapter with power saving on, RC2 asks NetworkManager to turn it off for that adapter for the session only (`nmcli device modify`). This is a runtime change that never edits your saved connection; Stop restores it, and a reconnect or reboot also restores it. Wired adapters are untouched. If NetworkManager refuses, the session continues and the client page shows the one-line command to make it permanent for your home network.

### Host accepts sessions when an unrelated network is Public (host)
RC1 refused every session if *any* active Windows network was Public: a VPN, WSL/Hyper-V adapter, second NIC or direct cable. Now the host needs at least one Private network to prepare, and each Pair/Start must arrive on an adapter Windows classes as Private. The firewall rules (Private/LocalSubnet) are unchanged.

### Stream packets marked for Wi-Fi priority (host profile)
The session profile enables ALVR's DSCP marking (Expedited Forwarding). On Linux this queues the client's uplink (tracking, input and statistics) in a Wi-Fi priority access category. Windows ignores application marks without a QoS policy, and ALVR discards marking errors, so this cannot break a host.

### Versions
Both packages are **1.0.0-rc2**. Version labels now come from `launcher/version.py` and `core/version.hpp`, and a test keeps every copy in step. Client journals previously reported `1.0.0-rc1`.

## How to test (please report each)

1. **Install** the client RC2 (and host RC2 if supplied). Confirm both pages show 1.0.0-rc2 and your quality settings are unchanged.
2. **Index baseline:** same game and scene as before. Play 5 minutes with Motion prediction **on**, then 5 with it **off**. Compare hand and controller lag, head-motion stability, black edges at the view's border during fast turns, and any swimming or overshoot. Save a report after each.
3. **Beyond:**
   - Projection: the RC1.3 fix is included unchanged.
   - Cadence: if you see the ~37 fps periods, note what the client page says next to *Fresh video*, and save a report while it is happening.
4. **Controllers:** briefly cover the headset's sensors or turn away from the base stations while holding a trigger. The trigger should stay held. Long rumbles (engines, continuous fire) should last their full length and stop when the game stops them.
5. **Wi-Fi:** on Wi-Fi, check the client page or report for the Wi-Fi line (power saving off for the session, or the reason it could not be changed). Compare stutter with Ethernet if you can.
6. **Lifecycle:** cold start, Stop, Start again. Note whether the first session is smoother or worse than the second, and save both reports.

## Known limits

- Prediction quality depends on ALVR's latency estimate and local SteamVR's own pose extrapolation. Overshoot on sudden stops is possible; the toggle exists for comparison.
- Host-side DSCP marking for video needs a Windows QoS policy and is not configured.
- A mid-session change of stream dimensions still requires restarting the session, and a mid-session encoder configuration change is not yet re-applied (both unchanged from RC1.3).
- No physical headset, controller, Wi-Fi adapter or Windows GPU was available in the build environment. Every item above needs your hardware test.

Build and test evidence: [evidence/rc2-build-1.0.0-rc2.json](../evidence/rc2-build-1.0.0-rc2.json).
