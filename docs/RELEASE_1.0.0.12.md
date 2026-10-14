# VROSTBridge 1.0.0.12

1.0.0.12 puts right what 1.0.0.11's first session on real hardware showed. That session (Beat Saber at Native, 2560 × 2560 per eye at 75 Hz, eye-tracked foveation, a handheld headset PC on Bazzite, wired) streamed well: 75 new frames a second, the sharp region following the eyes for 98.5 % of frames, five frames lost in nine minutes and about 40 ms from the rendering PC's frame to the headset. Three things were not right:
- the automatic bitrate went from 65 to 30 Mbit/s in the first 80 seconds and stayed there;
- the headset PC found its GPU clock afresh, four and a half minutes in;
- the host page called SteamVR's own drivers third-party add-ons and suggested turning them off.

Every package carries **1.0.0.12**. Everything in the [1.0.0.11](history/RELEASE_1.0.0.11.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.12.exe` over 1.0.0.11. Its driver is 1.0.0.9's, unchanged.
- **Headset PC:** install `VROSTBridge-Bazzite-Installer-1.0.0.12.zip` or `VROSTBridge-Client-Setup-1.0.0.12.exe` over the earlier one. Settings, pairing, the network test, Baballonia and its calibration stay.

Nothing on the network changed, so either PC still works with the other at 1.0.0.11. Installing both keeps them on one version.

## The bitrate forgives (both clients)
In the session's first 80 seconds the headset PC's GPU sat at a low clock (800 MHz) while the game started. Frames were late, decoding fell behind, and the automatic bitrate stepped down three times: 65, 50, 40, 30 Mbit/s. At four and a half minutes the companion held the GPU at 1300 MHz and the stream ran clean. But three steps down asked for 160 clean seconds before a step up, and the rendering PC's own stalls broke every run of them: about every half minute it sent nothing for 40–80 ms. So the picture spent eight minutes at less than half the bitrate meant for it, for reasons that were not the bitrate's.
- **A fresh start when the GPU clock goes up.** When the companion holds the headset PC's GPU clock higher, the steps down so far may have been the graphics', not the bitrate's. The bitrate goes back to where the session started, nothing is judged for 8 seconds, and the next step up needs the first step's patience (40 clean seconds) again. If the stream still struggles, it steps down again as before.
- **Frames that never came don't count against it.** A second in which at least two frames never arrived at all, with none lost on the network, is not counted as late or uneven: the rendering PC sent nothing for a moment, and a lower bitrate would not have sent those frames sooner. Frames that arrive late still count, and so do lost ones.
- After a fresh start the page's bitrate line ends *Last change: back to the start: the GPU clock was raised*; the client report's `bitrate_control` keeps every step with its reason.

## The headset PC remembers its GPU clock (Bazzite)
On a handheld, the companion holds the GPU clock higher when frames run late at a low clock (1.0.0.8), and each session found that clock afresh. Now the clock a session ends up holding is remembered for that PC, with the GPU's maximum clock and whether it was on the charger, and held as soon as the next session's video is up. The page says *GPU held at … MHz from the start, as the last session needed*.
- It is only used while the GPU's level is automatic: a clock set by hand is left alone.
- Only on the same GPU and the same power source, since the charger raises the power limit.
- Forgotten when a session has to set it back down (the processor or eye tracking squeezed) or when it is changed elsewhere, so the next session finds it afresh.

Frames that never arrived no longer count as frames running late here either: no GPU clock on the headset PC would have shown them.

## The add-on warning (rendering PC)
The host page warns about other SteamVR add-ons that load on the rendering PC and may act on the virtual headset, such as a headset maker's helper. It used to check the drivers in SteamVR's own folder against a list of names, which went stale as SteamVR added drivers: it called indexcontroller, frame_hmd, frame_controller, prism, roy and tundra_labs third-party, Valve's Index controller driver among them, while the streamed controllers present themselves as Index controllers. Now only add-ons registered from outside SteamVR are listed: SteamVR's own drivers stay idle without their hardware. Bigscreen's helpers are recognised by their folder as well as their name.

## How to test
Install 1.0.0.12 on both PCs and play as before with *Use recommended settings* (automatic bitrate).
- Watch the bitrate line on the headset PC's page: after a slow start it should go back up once the GPU clock is held.
- Start a second session: the page should say the GPU was held from the start.
- On the rendering PC, the add-on warning should name only add-ons you installed yourself, or nothing.
- If you can, play the same game for five minutes with eye tracking off: whether the rendering PC's stalls follow the game or the gaze says where to look next.

Send reports from both PCs, also when everything works.

## Known limits
- **Nothing new here has run on hardware yet.** The fresh start and the frames-that-never-came rule are tested with the 1.0.0.11 session's own numbers, the remembered clock with a stand-in for steamos-manager.
- **The rendering PC's stalls are not fixed.** In that session its capture timed out waiting for SteamVR's frame about every half minute (SteamVR logged the handoff timing out 33 times in nine minutes of Beat Saber, and not at all in a longer session with no game running). The capture waits up to 10 ms for each frame, as ALVR's does; why the frames were late there is not known yet. 1.0.0.12 only stops those stalls from costing bitrate.
- The Windows client has still not run on real hardware (see 1.0.0.7).

Build and test evidence: [evidence/vrostbridge-build-1.0.0.12.json](evidence/vrostbridge-build-1.0.0.12.json).
