# VROSTBridge 1.0.0.5

1.0.0.5 fixes what the 1.0.0.4 test and its reports showed, on both programs: grey blacks with 10-bit colour, eye tracking that lagged and paused, a sharp region you could see moving, no picture while the headset was not tracked, Ultra on the headset PC, and an automatic bitrate stuck at 30 Mbit/s. Everything in the [1.0.0.4](RELEASE_1.0.0.4.md), [1.0.0.3](RELEASE_1.0.0.3.md), [1.0.0.2](RELEASE_1.0.0.2.md) and [1.0.0.1](RELEASE_1.0.0.1.md) notes still applies.

**Install both:** `VROSTBridge-Host-Setup-1.0.0.5.exe` on Windows and `VROSTBridge-Bazzite-Installer-1.0.0.5.zip` on Bazzite. Both install over 1.0.0.4 and keep its settings, pairing, Baballonia and calibration. Install over 1.0.0.4; do not uninstall it first. **Then turn 10-bit colour back on** (client page, *Stream*; or Game Mode, *Stream & headset*): the host now checks its 10-bit colour itself.

The fixes are general. Every limit comes from what the PC, the cameras, the encoder or the network path report about themselves, or from the stream's own timing; nothing is tuned to one headset, handheld, graphics chip or network adapter.

## What was wrong, and what changed

### 1. Blacks were grey with 10-bit colour (host)
With 10-bit colour on, black areas showed as dark grey on the Beyond's OLED panels; with it off they were black, as on the PC itself. On AMD graphics the host's encoder path turned the game's picture into 10-bit RGB and left the conversion to Y′CbCr to the encoder. That conversion produced studio-range samples (black at 64 of 1023), while the stream said full range (black at 0). The headset PC trusted the label, so black came out as dark grey.

- **The host now converts to 10-bit Y′CbCr itself**, with the matrix (BT.709) and range (full) stated, and labels the stream with exactly that (new driver patch `0018-host-exact-colour-and-pose-drops.patch`).
- **It checks the result at the start of every stream.** A small test picture with black, white, red and mid-grey stripes goes through the same converter. The host reads back the samples it produced and works out their range and matrix from them. The stream is labelled with what the samples really are.
- **When the check fails, the host streams 8-bit** (as it did with 10-bit off), rather than a picture with the wrong blacks. If the encoder refuses the 10-bit settings, it retries in 8-bit too. The client page's video details then say *10-bit off: the host could not make exact 10-bit colour, so it streams 8-bit*.
- The host log says what it did: `VROST_COLOR path=… bits=… range=… matrix=… verified=…`, with the codes measured for black, white, red and grey. `VROST_COLOR_FALLBACK` says why it chose 8-bit.
- The 8-bit stream is labelled exactly as well: H.264's and HEVC's full-range flags and AV1's colour description follow the same setting.
- The client page's video details show the stream's colour as the headset PC's decoder reports it, for example *10-bit full range*. The client log writes it whenever it changes.
- AV1's bitrate changes during a session now use AV1's own encoder settings.

### 2. Eye tracking lagged and paused (client)
Your report shows the eye-tracking samples swinging between 10 and 55 a second, and 11 pauses over a second (5 over 5 seconds, the longest 10 seconds). The eye-camera helper used a whole processor core.

- **The camera helper no longer decodes and re-encodes every camera frame.** That took 10 to 16 ms of processor time per frame on a handheld, and frames queued up whenever it was slower than the cameras. Frames are now checked and passed on as the cameras sent them. Huffman tables are added where a camera leaves them out, and a frame is decoded in full now and then to check it.
- **The cameras run at their fastest frame rate.** The helper asked each camera for its longest frame interval, which is its slowest frame rate.
- **Lost USB data costs one frame, not a pause.** The helper used to open the camera again after any damaged USB packet, without closing what it had opened, and eye tracking paused each time. Now the frame is skipped. The camera is opened again only if no frame arrives for 2 seconds (also while it sends USB data that never makes a frame), after everything is closed properly, with increasing waits. The picture stream to Baballonia stays connected meanwhile, and a malformed USB packet no longer stops the helper.
- The helper reads the USB data in smaller pieces, so the end of a frame arrives sooner. It passes on up to 125 frames a second (66 in 1.0.0.4).
- **Baballonia's eye filter follows within two camera frames.** In a simulation of Baballonia's own filter at 60 camera frames a second, 1.0.0.4's setting (1 Hz, speed 10) reached 90 % of a 5 or 10 degree eye movement after 67–83 ms. The new setting (2 Hz, speed 60) takes 33 ms. It is applied once, and only where the filter is still as Baballonia or an earlier VROSTBridge set it; a filter you chose yourself is kept.
- **The camera helper reports what it does.** Every 5 seconds it writes a `STATS` line: frames sent and received, frames and packets lost, frames it rejected, re-openings, the longest gap, the frame interval and the picture size. It also writes a `CAMERA` line whenever it opens a camera. The client report carries them, the session timeline adds camera frames a second, and the eye-tracking card says *… samples a second from … camera frames a second*.

### 3. The sharp region moved where you could see it (host)
The host moved the sharp region whenever the gaze moved by more than 2.5 % of the view. The tracker's own noise and small eye movements crossed that every few frames, so the region's edge kept shifting where you could see it.

- **The region now stays still while your gaze stays inside a comfort zone around its middle.** The zone reaches half way from the middle to the region's edge, and is never smaller than the old 2.5 %. Look further than that and the region moves to where you are looking, as quickly as before (new driver patch `0019-host-gaze-comfort.patch`; the companion writes `gaze_comfort` = 0.5 to the driver's stream options).
- In a simulated minute of eye movements (pauses of 150–450 ms, jumps of 2–15 degrees, the tracker's noise) with the region 40 % of the picture wide, the region moved 0.85 times a second instead of 3.17. The gaze always stayed at least 9 % of the picture inside the region's edge.
- With the faster tracking in section 2, a smaller sharp region should hold up better than before. That saves bitrate for the rest of the picture.

### 4. No picture while the headset was not tracked (both)
The host renders a frame only for the head position the headset PC sends with it. When SteamVR on the headset PC had no position for the headset, for example before the base stations saw it or while it was still calibrating, every frame was dropped without a word. The pages just said that no video was arriving.

- **The client says *Headset not tracked*** once SteamVR on the headset PC has had no position for the headset for 2 seconds: on its page and in Game Mode, and on the card in the headset while no new picture arrives. The page says what SteamVR reported, for example that it *cannot see the base stations*, and what to check.
- **The host counts the frames it could not send** and writes `VROST_NO_HEAD_POSE dropped=… sent=… window_s=5` once every 5 seconds while it happens (patch 0018). The host page then says *Waiting for the headset’s position*.
- The client's metrics and reports count head positions sent and missing.

### 5. Ultra on the headset PC (client)
Ultra is 3200 × 3200 per eye on a Beyond, but SteamVR on your headset PC asks for 2712 × 2712. The client drew every frame at 3200, which cost GPU time the handheld does not have, and SteamVR scaled it down again.

- **A stream larger than SteamVR's recommended size is now shown at that size.** The decoded picture is scaled in the same GPU pass that already converts it. The client page's video details say *shown at 2712 × 2712*. The stream itself, and so its detail, is unchanged.
- When the graphics warning finds the GPU held back while its **GPU clock** is fixed, it offers *Set the GPU clock to automatic* there too, in one step.

### 6. Automatic bitrate was stuck at 30 Mbit/s (client)
Every session since the first 1.0.0.4 start ran at 30 Mbit/s, which leaves little detail for a stream this size. The 1.0.0.4 network test on your USB Ethernet adapter found no rate it could call clean: a few packets went missing at 80 Mbit/s, and frames seemed to arrive late from 150 Mbit/s. The same adapter had carried 500 Mbit/s without loss the day before. The result was then kept for a month.

The test was measuring the companion more than the network. Its receiver is one thread of the companion, and Python runs one thread at a time, so datagrams waited in the socket until the receiver's turn came. Frames therefore looked spread out (late), and datagrams that had reached the PC were dropped from a full socket buffer and counted as network loss.

- **On Linux the test now takes each datagram's arrival time from the kernel**, and counts the datagrams this PC's own buffer dropped apart from those lost on the way. While it runs, the companion's other threads hand over every half millisecond instead of every five.
- **A first warm-up step** at the lowest rate wakes the path, for example an adapter leaving power saving, and is not judged. The test takes about 15 seconds.
- Frames lost at the end of a step together with its end markers now count, from the host's own count of the frames it sent; before, the step could look clean.
- **A test that finds no clean rate is tested again before the next session**, up to three tests in a row, and an earlier result that did find one is used meanwhile. A test that received nothing counts the same. Your saved result is such a test, so the first 1.0.0.5 session tests the network again.
- **Wired pacing follows steady loss**: 1 % of the packets or more at a step. One stray lost packet no longer sets the pace, or, at the first step, leaves the stream paced at the link's full speed.
- **On a wired link the bitrate leaves room in the pace.** Automatic bitrate is at most 40 % of the rate the video is paced at on that path, so an average frame arrives within 40 % of a frame's time. The message says so: *… the video is paced at 600 Mbit/s on this path, so using 240 Mbit/s keeps each frame quick to arrive.*
- Saved results keep the test's steps and how the receiver timed them, for reports.

Replayed through the new rules, with frames timed when they arrived, your three earlier tests on that adapter give 100, 120 and 240 Mbit/s. They gave 100, 100 and 30.

### 7. Version 1.0.0.5 on both programs.

## Settings worth checking (from your reports)
- **10-bit colour:** turn it back on. Check that blacks stay black and that the video details say *10-bit full range*.
- **Automatic bitrate:** leave it on. The first session tests the network again; the client page shows the result under *Test network now*, with the rate automatic bitrate uses.
- **Sharp region:** with tracking faster and the region steadier, try one size smaller (or *Medium* with *Balanced* outside) and see whether you notice it. A smaller region leaves more of the bitrate for the rest of the view.
- **Ultra:** try it again once the network test has run. If the graphics warning still shows, *High* (2560) is the next step down.

## How to test (please report each)
1. **Install** both over 1.0.0.4. The pages show *VROSTBridge Host 1.0.0.5* and *VROSTBridge Client 1.0.0.5*.
2. **Colour:** turn 10-bit colour on and start a dark scene. Blacks should look as black as on the PC. The client page's video details should say *10-bit full range*; the host log's `VROST_COLOR` line should say `bits=10 range=full … verified=1`.
3. **Network:** the first session tests the network again (about 15 s). On Ethernet the automatic bitrate should now be well above 30 Mbit/s. Watch *lost frames* on the session card for a few minutes.
4. **Eye tracking:** the eye-tracking card should show about as many samples a second as camera frames, steadily. In the session timeline in the report, camera frames should no longer drop to a few a second. The eye-camera helper should use far less processor time.
5. **The sharp region:** look at fine detail, then let your eyes wander a little around it. The edge of the sharp region should stay still. Then glance at something across the view: the region should follow at once.
6. **Headset not tracked:** with a session running, cover the headset or block the base stations for a few seconds. The card should say *Headset not tracked*, and the picture should come back by itself.
7. **Ultra:** the video details should say *shown at 2712 × 2712* (SteamVR's size on your headset PC).
8. **Send reports from both PCs** after a session of several minutes.

## Known limits
- The colour check is in the host's AMD encoder path (AMF), the one these reports used. The NVIDIA and Intel paths are unchanged.
- The camera helper's changes were tested with recorded camera frames, synthetic USB data and unit tests, not yet with the cameras themselves. The `STATS` lines in your next report will show how the cameras behave.
- The comfort zone is a host setting with a default of half the region; it is not on the pages yet.
- The network test's kernel arrival times and drop counts need Linux, which the client always runs on.
- The host driver was rebuilt with the same cross-build as 1.0.0.4 (patches 0001 to 0019). The colour rules and the comfort zone were checked with their own tests on Linux, but none of this release has been tried on real hardware yet.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.5.json](../evidence/vrostbridge-build-1.0.0.5.json).
