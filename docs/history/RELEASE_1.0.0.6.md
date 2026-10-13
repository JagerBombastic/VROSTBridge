# VROSTBridge 1.0.0.6

1.0.0.6 follows up the 1.0.0.5 test and its reports, on both programs: a sharp region that followed the eyes only near the middle of the view, 10-bit colour refused at every start, an automatic bitrate of 50 Mbit/s on a path that carried 300, and controller models that overlapped about 10 mm more than on the headset's own PC. Everything in the [1.0.0.5](RELEASE_1.0.0.5.md), [1.0.0.4](RELEASE_1.0.0.4.md), [1.0.0.3](RELEASE_1.0.0.3.md), [1.0.0.2](RELEASE_1.0.0.2.md) and [1.0.0.1](RELEASE_1.0.0.1.md) notes still applies.

**Install both:** `VROSTBridge-Host-Setup-1.0.0.6.exe` on Windows and `VROSTBridge-Bazzite-Installer-1.0.0.6.zip` on Bazzite, over 1.0.0.5 (do not uninstall it first). Settings, pairing, Baballonia and its calibration stay.

The fixes stay general: the reach is measured against Baballonia's own range, the colour against a test picture, the network against the sender's own timing, and the prediction against the headset PC's own tracking.

## What the reports showed, and what changed

### 1. The sharp region followed your eyes only near the middle (Baballonia's calibration; both programs)
The relay, the client and the host pass the angles Baballonia sends through unchanged, and the host places the region anywhere in each eye's picture. The limit is upstream of all three. Baballonia's eye model reports only as far as its calibration taught it: the calibration's dot stays still while you turn your head, so your eyes only went as far as your head turned. Beyond that its answers fall short, and the region stops short of where you look. Baballonia also never reports more than **45°** from straight ahead (its trainer's range); the Beyond's view reaches about 55° to the sides.

- **Calibrate again, turning your head further.** While the dot shows, keep your eyes on it and turn your head slowly each way, corners too, until the dot nears the edge of your view. The page's *Calibrate once* steps now say so.
- **Gaze reach** shows how far the gaze went each way this session, in whole degrees: on the client page's Eye tracking card (with **Measure again**) and as a row on Game Mode's Eye tracking page. Look to the far left, right, up and down, then read it. If it says about 20° where you looked 40°, the calibration did not go that far.
- The host logs the same for each 5-second window, with where the left eye's region went (`gaze_yaw`, `gaze_pitch`, `region_x`, `region_y` on the `VROST_GAZE_FOVEATION` line; driver patch `0020-host-colour-variants-and-gaze-reach.patch`). The same line no longer carries the region's current position while it follows the gaze (only for a fixed or optical centre), so the host's log never says where you were looking.
- Reports carry the session's reach and the host's windows: four numbers, never where you looked at any moment.

### 2. 10-bit colour was refused at every start (host)
Your host report's `VROST_COLOR` line says `refused=p010:black-or-white-level`: the test picture came back with black at 0 but white at 297 of 1023, mid grey at 100 and red at under a third of its level. AMD's converter dimmed everything, the way it would when mapping SDR into an HDR range. The check did its job: the stream fell back to 8-bit, which is why your blacks were black.

- The converter is now asked for plain SDR first: the same transfer (BT.709) and primaries in and out, no tone mapping and no HDR metadata, so it only changes the matrix and range. The same test picture checks it; then 1.0.0.5's settings are tried; then 8-bit, as before.
- Each attempt is logged with what it measured (`VROST_COLOR_PROBE path=p010 variant=plain …`), and `VROST_COLOR` names the settings that worked (`variant=plain` or `auto`).

### 3. Automatic bitrate chose 50 Mbit/s (client)
The network test lost nothing up to 300 Mbit/s, but called the 150 Mbit/s step late: its frames arrived spread over 9.5 ms. The network had nothing to do with it. The host sends the test from a Python loop that needs about 50 µs per datagram, so writing a 150 Mbit/s frame took 9.5 ms. (The video itself is sent by the driver, much faster.)

- The receiver now takes each frame's send time from the sender's own time stamps and judges lateness only by what the way here added (`network_spread_p95_ms`; the sender's own is `send_spread_p95_ms`). Both are kept with the saved steps.
- A result whose limit was lateness measured the old way is tested again before the next session.
- Replayed through the new rule, with the sender's time estimated from your test's own spreads, it gives 120 Mbit/s: clean up to 300 Mbit/s, paced at 300, with 40 % of the pace used so each frame arrives quickly.

### 4. Controller models overlapped more than on the headset's own PC (both)
Two different things can do this, and 1.0.0.6 measures both.

- **Prediction, while moving.** On the stream, about 40 to 45 ms pass between the headset PC sampling your controllers and the frame drawn for them reaching the displays (ALVR's estimate at full frame rate in the 1.0.0.5 test; about twice that while the frame rate is halved), so the host draws each controller where it will be by then: position plus velocity times that time. Controllers that stop suddenly overshoot for that moment (at 20 cm/s and 40 ms, 8 mm), then settle. A headset on its own PC predicts only about 20 ms ahead (an estimate). The headset PC now repeats the host's extrapolation on its own tracking samples and measures how far it lands from where the controller really was: per second in the client's `INPUT` line (`prediction`: the step, mean, 95th percentile and largest miss, and the largest while held still), per 10 seconds in the timeline (`predict_max_mm_l`, `predict_max_mm_r`) and for the session in reports (`controller_prediction`).
- **An offset, also when held still.** That is what you saw: held still against each other, they still overlapped more than on the headset's own PC. Nothing in the stream should move a controller that is held still: the host applies no controller offsets (all zero in its profile), its prediction moves a still controller by well under a millimetre, and both PCs use the same Index controller model. So 1.0.0.6 measures the distance between the two controllers, while both are held still, at the three places it passes: as SteamVR on the headset PC tracks them (`pair` in the client's `INPUT` line and in `controller_prediction`), as the host received them, and as the host handed them to its SteamVR (`VROST_CONTROLLER_PAIR`, driver patch 0020: `received_mm`, `submitted_mm`, and the largest move and turn the host gave either controller). Three equal distances mean the poses reach the host's SteamVR exactly as tracked, and the difference is in how the picture is drawn or shown; a difference between them shows where the poses change.

**Try this:** touch the controllers together and hold them still for about five seconds, then tap them together a few times, and send reports from both PCs. Please also say whether what overlapped was the controllers themselves or the hands holding them, and in which app (SteamVR Home, the dashboard or a game).

### 5. Also in the reports
- **The eye cameras:** 90 pictures a second for the whole session, none damaged, the camera never opened again (1.0.0.5's helper at work). Baballonia turned 50 to 57 of them a second into gaze, its own limit, using about 1.7 processor cores; the page now says one to two.
- **The stream:** about 74 of 75 frames a second at High with eye-tracked foveation, the integrated GPU at 900 to 1000 MHz and 20 W. It dropped to half rate only in the last seconds, after the headset was taken off.
- **Controller firmware:** SteamVR on the headset PC says both controllers have an update waiting (*Device update required*). Update them there when convenient (SteamVR → Devices → Update Device).

### 6. Version 1.0.0.6 on both programs.

## Settings worth checking
- **Eye calibration:** do it again as above, then check *Gaze reach*.
- **Automatic bitrate:** leave it on. The first 1.0.0.6 session tests the network again (about 15 s).
- **10-bit colour:** leave it on. The video details say *10-bit full range* when the plain SDR conversion checks out, and *10-bit off …* (8-bit, still correct blacks) when it does not.
- **Motion prediction:** leave it on. Without it everything trails behind your movements by the whole latency.

## How to test (please report each)
1. **Install** both over 1.0.0.5. The pages show *VROSTBridge Host 1.0.0.6* and *VROSTBridge Client 1.0.0.6*.
2. **Network:** the first session tests again. On Ethernet the automatic bitrate should be well above 50 Mbit/s. Watch *lost frames* on the session card for a few minutes.
3. **Gaze reach:** in a session, look to the far left, right, up and down, then read *Gaze reach* (page or Game Mode). Then calibrate again turning your head further, choose **Measure again**, and compare.
4. **The sharp region:** after the new calibration, look toward the edges of the view. The region should follow until about 45° from straight ahead.
5. **10-bit:** start a dark scene. Check the video details (*10-bit full range* or *10-bit off*) and that blacks stay black.
6. **Controllers:** touch them together and hold still for about five seconds; then tap them together. Say what you see each way, and whether the controllers or the hands overlapped.
7. **Send reports from both PCs** after a session of several minutes.

## Known limits
- Baballonia reports at most 45° from straight ahead, and only as far as its calibration went. The edges of a Beyond's view lie beyond that.
- The plain SDR conversion is checked by the host itself at every start, but has not met an AMD driver yet; `VROST_COLOR_PROBE` in the next host report says what it measured.
- The controller checks measure poses: the prediction repeated on the headset PC's own samples, and the distance between the controllers as tracked, received and submitted. If all three distances agree while the overlap remains, the next place to look is how the picture is drawn and shown, which reports cannot measure.
- The host driver was rebuilt with the same cross-build as 1.0.0.5 (patches 0001 to 0020). Nothing in this release has been tried on real hardware yet.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.6.json](../evidence/vrostbridge-build-1.0.0.6.json).
