# VROSTBridge 1.0.0.8

1.0.0.8 is about **steady, consistent sessions**. The 1.0.0.7 test was less smooth than the evening before, and the reports showed why. The automatic bitrate had gone up to about 195 Mbit/s, four times what the picture needed, because the 1.0.0.6 network test passed the wired path at up to 500 Mbit/s. Bigger frames took twice as long to decode on the headset PC, arrived later and in bunches, and were lost more often. The headset PC's graphics sat at 800 MHz, under a third of their maximum clock, for the whole session. A fresh eye-tracking calibration put the sharp region 15–20° to the right of where you looked. And the base stations took 20 seconds to be found after SteamVR started.

So 1.0.0.8 does four things:
- it asks only for the bitrate the picture needs, and adjusts it second by second while the stream runs;
- it balances the Bazzite headset PC's power during sessions, and only during sessions;
- it keeps the gaze centred for each calibration;
- it turns SteamVR base stations on as a session starts and off when VROSTBridge closes.

**Use recommended settings** puts every stream setting where it works best. Every package carries **1.0.0.8**. Everything in the [1.0.0.7](RELEASE_1.0.0.7.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.8.exe` over 1.0.0.7. The driver is unchanged; the companion is what changed, because it now applies bitrate changes during a session.
- **Headset PC:** install `VROSTBridge-Bazzite-Installer-1.0.0.8.zip` or `VROSTBridge-Client-Setup-1.0.0.8.exe` over 1.0.0.7. Settings, pairing, Baballonia and its calibration stay.

Install both PCs together. Live bitrate changes need 1.0.0.8 on both PCs: an older host keeps the bitrate the session started with, and the page says so.

## Steadier streams

### The bitrate the picture needs
- **A ceiling from the picture.** Automatic bitrate now stops at what the stream's picture needs, whatever the network could carry. The ceiling is bits per pixel for the codec × the encoded size (foveation included) × the frame rate: 0.22 bit per pixel for HEVC, 0.32 for H.264 and 0.18 for AV1, within 20–300 Mbit/s.
  - Standard with eye-tracked foveation on a Beyond 2e (2432 × 1216 encoded) at 75 Hz in HEVC comes to **49 Mbit/s**. That is the bitrate of 1.0.0.5's clean session.
  - Larger pictures get proportionally more. For example, 4032 × 2016 encoded at 75 Hz comes to 134 Mbit/s.
- **Video paced below what the path carried without loss.** From 1.0.0.4 the video was paced at the full rate the network test carried without loss: 500 Mbit/s in the 1.0.0.7 test. It now gets **70 %** of that rate, so other traffic or a busy moment on either PC cannot tip frames into loss. The test itself is unchanged.

### Following the stream while it runs
With *Automatic bitrate* on, the headset PC reads its own frame timing every second. After an 8-second settling period, a second counts as troubled when one of these happens:
- a frame was lost;
- decoding fell behind (on average more than 42 % of a frame interval, or more than 65 % for its slowest frames);
- frames were late while arriving unevenly.

The bitrate then changes like this:
- **Down 20 %** when 2 frames were lost, or 5 seconds were troubled, within 10 seconds. It never goes below 35 % of the ceiling, or 20 Mbit/s.
- **Back up 10 %** after 40 clean seconds, never above the ceiling. Each step down doubles the clean time needed before the next step up (40, 80, then 160 seconds), so a stream that struggles at one bitrate doesn't keep returning to it.
- **Checked when only decoding was slow.** On some PCs decoding takes as long whatever the bitrate. When a step down was taken for slow decoding alone, VROSTBridge compares the decoding time before and after. If decoding got no faster (less than 7 %), the bitrate goes back up and decoding time stops counting for the rest of the session. Lost and late frames still count. The graphics-limit advice is what helps in that case.
- **Applied on the rendering PC at once.** The wanted bitrate travels with the client's heartbeat every 3 seconds. The host companion sets it through ALVR's own settings interface, at most every 2 seconds and only while the session streams. The encoder takes the new rate without restarting the stream.
- **Where to see it:** the page and Game Mode's Stream page show *Bitrate now 40 Mbit/s (most for this picture: 49 Mbit/s; …). Last change: decoding behind.* The client log has `BITRATE 50 -> 40 Mbit/s (decoding behind)`, the host log has `VROST_BITRATE set=40 Mbit/s`, and the reports list the steps.
- A manual bitrate is never changed.

### Use recommended settings
A new button on the *Headset & stream* card, and in Game Mode, sets everything at once:
- codec, stream resolution, foveated encoding and bitrate on **Auto**;
- 10-bit colour, the frame timing lock, motion prediction, re-aim, late-frame catch-up, pacing and the one-pass frame path **on**;
- **Lowest latency** encoding;
- keep-awake, performance balancing and gaze centring **on**;
- a Medium sharp region with a Balanced outside;
- gradual loss recovery on Wi-Fi only (on a cable, keyframes recover faster).

It applies at the next Start. Codec, resolution and foveation on Auto mean this on each headset:
- **Beyond 2e** with eye tracking set up: HEVC, Standard (2016 per eye), eye-tracked foveation.
- **Index:** H.264 at 2016 per eye, with Subtle foveation.

## Balance performance during sessions (Bazzite)
A new switch on the *This PC* card and in Game Mode, **on** by default. It works through steamos-manager, the same service as Steam's Performance menu, and does nothing until a session's video is streaming:
- **On the charger** the power limit (TDP) goes to its maximum for the session. On battery it stays where it is.
- **When frames run late** while the GPU sits at no more than half its maximum clock and is busy, the GPU clock is held higher. The first step is 45 % of its maximum. After that it rises 200 MHz at a time, never above 75 %.
- **The processor keeps its share.** The clock never rises while the processor is held below its own minimum or eye tracking has slowed. If either happens, it steps back down.
- **Everything is set back at Stop.** After a crash it is set back at the next start. A setting you change yourself during the session, in Steam's Performance menu say, is left as you set it.

A PC without steamos-manager streams as before, and the card says so. Windows has nothing like it, so the switch is Bazzite's.

## Base stations off when VROSTBridge closes (both clients)
- A new switch on the *This PC* card and in Game Mode, **on** by default. Closing VROSTBridge turns SteamVR 2.0 base stations off. It runs as a separate step, so VROSTBridge itself closes at once.
- Each session's Start turns them on while SteamVR starts. In the 1.0.0.7 test the headset found the base stations 19.6 s after SteamVR started; with them already spinning it takes about 4 s.
- **Base stations on** and **Base stations off** buttons send the command straight away.
- The command goes out over the headset PC's own Bluetooth: BlueZ on Bazzite, Windows' Bluetooth API on Windows. It is one byte to each station's power setting.
- Nothing is paired or changed in the PC's Bluetooth settings, and only devices named `LHB-…` are contacted. Older 1.0 (HTC) base stations use a different command and are left alone.
- The card shows the stations found and how the last command went, including the one sent at closing.

## Eye tracking
- **Keep my gaze centred** (on by default). Your eyes rest near straight ahead most of the time; the head does the big turns. When the middle of a session's gaze sits further to one side than a resting gaze does, VROSTBridge takes the difference off every sample, so the sharp region stays where you look.
  - It moves in small steps after the first 11 seconds or so, up to 25° sideways and 15° up or down.
  - It is remembered for each calibration, so the next session starts centred.
  - The card shows where your gaze rests and the correction. It also says when a calibration looks off-centre, meaning it sits 10° or more to a side.
  - **Reset centring** forgets the correction for the current calibration.
- **Use previous calibration** goes back to the calibration before the current one. Baballonia keeps every model it trains, so nothing is lost. Use it between sessions, with Baballonia closed.
- Gaze reach is still measured from Baballonia's own samples, before the correction.

## Also in 1.0.0.8
- **Pages and Game Mode** show the bitrate now, the performance balance and the base stations. The three new switches apply straight away, even during a session.
- **Version 1.0.0.8** for the host, the Bazzite client and the Windows client. The host's driver is the same build as 1.0.0.6 and 1.0.0.7 (patches 0001 to 0020).

## How to test
Install 1.0.0.8 on both PCs, choose **Use recommended settings** and **Test network now** on the headset PC, then stream for a few minutes. Please report:
1. Is the stream smooth, and do *Bitrate now* and *lost frames* stay calm?
2. **Bazzite:** does the *Balance performance* line say what it did, and does Steam's Performance menu show your own settings again after Stop?
3. Do the base stations go to sleep when VROSTBridge closes, and wake at Start?
4. **Eye tracking:** does the sharp region sit where you look, straight ahead included? If not, try **Use previous calibration**.
5. **High (2560)** with eye-tracked foveation: better or worse than Standard?

Send reports from both PCs.

## Known limits
- **None of the new parts have run on hardware yet.** They are built and tested in code, against stand-ins for steamos-manager, Bluetooth (BlueZ) and ALVR's settings interface. The Windows Bluetooth script is checked by PowerShell's parser but has not run on Windows.
- The governor's thresholds come from one headset PC's logs, so other PCs may want different ones. The reports show every step it took and why.
- Base stations: only SteamVR 2.0 base stations, and only with Bluetooth on and in range. If the PC sleeps or shuts down at the moment VROSTBridge closes, the off command may not get through; the card shows how the last one went, and **Base stations off** sends it again.
- Gaze centring assumes your gaze rests near straight ahead over a session. A game that keeps you looking to one side for minutes moves it a little, and **Reset centring** starts it again.
- The Windows client has still not run on real hardware (see 1.0.0.7).

Build and test evidence: [evidence/vrostbridge-build-1.0.0.8.json](../evidence/vrostbridge-build-1.0.0.8.json).
