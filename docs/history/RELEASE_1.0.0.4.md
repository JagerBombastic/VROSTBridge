# VROSTBridge 1.0.0.4

1.0.0.4 fixes what the 1.0.0.3 test and its reports showed, on both programs: the sharp region leaving your eyes, the little lags every few seconds, the grey flashes, a processor starved of power, and the Game Mode controls. Everything in the [1.0.0.3](RELEASE_1.0.0.3.md), [1.0.0.2](RELEASE_1.0.0.2.md) and [1.0.0.1](RELEASE_1.0.0.1.md) notes still applies.

**Install both:** `VROSTBridge-Host-Setup-1.0.0.4.exe` on Windows and `VROSTBridge-Bazzite-Installer-1.0.0.4.zip` on Bazzite. Both install over 1.0.0.3 and keep its settings, pairing, Baballonia and calibration. Install over 1.0.0.3; do not uninstall it first.

The fixes are general: every threshold comes from the PC's own reported clocks, the network path's own measurements, or the stream's own timing. Your reports are the evidence; nothing is tuned to one handheld, graphics chip or network adapter.

## What was wrong, and what changed

### 1. The sharp region left your eyes (host)
You could look straight at the coarse, reduced area at the edge of the view. The host's foveation counters show why: in both sessions of the test, **39 % of all frames** had the sharp region back in the middle of the view instead of where you were looking.

The host moves the sharp region to the latest gaze sample. When no new sample had arrived for 150 ms (a blink, or the eye tracker skipping pictures), it put the region back in the middle at once, so wherever you were looking turned coarse. Pauses like that happen many times a minute, and one in your session lasted 20 seconds.

- The host now keeps the region **where you last looked for one second** after the gaze pauses. It returns to the middle only when eye tracking has really stopped (new driver patch `0017-host-gaze-hold.patch`; the companion writes `gaze_hold_ms` to the driver's stream options).
- The host's log line now counts the frames held in place and the pauses: `VROST_GAZE_FOVEATION … gaze_frames … held_frames … fallback_frames … pauses … longest_pause_ms … returns`. A report shows how often the gaze paused and for how long.
- On the pages and in Game Mode, *Centred on your gaze* counts held frames as following your eyes.
- The eye-tracking view says *the sharp region holds where you last looked, then returns to the middle* while no sample arrives, and warns when eye tracking sends fewer than 30 samples a second: the region then reaches where you look late.

### 2. The processor was held back by the power limit (client)
In the last session **every processor core ran at about 540 MHz for the whole session**, below the processor's own 1100 MHz minimum. The GPU clock was fixed at its maximum (2700 MHz), and with the 28 W power limit the GPU took nearly all of the power. Handhelds, most laptops and mini PCs with integrated graphics share one power limit between the processor and the GPU. Because the processor was starved:

- **Eye tracking fell from 54 to 12 samples a second.** Baballonia runs its eye model on the processor.
- **The stream's own work stalled for 40–60 ms every few seconds.** SteamVR on the headset PC dropped 637 frames, 161 of them in 53 moments when the stream's frame came too late.
- The session before showed the reverse: the GPU clock was automatic, and the GPU stayed at 800 MHz while 62–69 % busy, because the power limit held it back.

What changed:

- **The client notices a processor held below its own minimum clock** for 10 seconds while streaming. The system never asks for less than that minimum, so running below it means the power or temperature limit is holding the processor back.
- **It says why.** If the GPU clock is fixed (or near its maximum while the GPU isn't busy), it says so and suggests a fixed clock worked out from this GPU's own maximum (about 60–75 % of it; 1600–2000 MHz on yours). Otherwise it says the power limit is too low for both, or the PC is too warm.
- Where it shows: the session card, the client page, Game Mode's session card and *This PC* page, and once as a short line in the headset (*CPU held at 544 MHz · GPU clock fixed*). The client log says `POWER_BALANCE …`.
- **Set the GPU clock to automatic**, on the client page and in Game Mode under *This PC*, changes the setting at once. It works where steamos-manager runs, the service behind Steam's *Quick Access → Performance* in Game Mode on SteamOS and on distributions that use it, such as Bazzite. Elsewhere the warning says where to change the setting. Nothing changes until you choose it.
- **Baballonia runs one step lower in priority** (`nice 5`). When the processor is short, the stream's own work goes first; with spare processor time, Baballonia runs as fast as before.

### 3. Little lags every few seconds (client)
The good moments were interrupted by a short hitch every few seconds. The client's counters show **one lost video frame every 3 seconds or so** (146 in 8 minutes). Each loss cost three or four frames: the headset PC asked for a keyframe and skipped frames until it arrived, a 40–60 ms gap.

The frames were sent faster than your network path could take them. The network test on your USB Ethernet adapter lost nothing up to 500 Mbit/s, but 12 % at 750 Mbit/s and 27 % at 900, and it received at most about 650 Mbit/s. Each video frame, however, was paced at 920 Mbit/s: 92 % of the adapter's 1 Gbit/s link speed.

- **Wired pacing now stays at or below the highest rate the network test carried without loss on that path**, whatever the adapter, whenever the test found a rate the path could not carry. A test without any loss leaves the pace at the link's speed, so faster links are not held back. The test records this rate, and the session's start line in the log says why the pace is lower: *pacing 500 Mbit/s, the most this network path carried without loss in its test*.
- **A path tested before 1.0.0.4 is tested once more** before its next session, which takes about 15 seconds. Without any test, a USB adapter is paced at 60 % of its link speed.
- The test itself still sends at the link's full pace, so it can measure every step.

### 4. Grey flashes (client)
The picture went grey whenever no new frame had arrived for 250 ms, for example while a lost frame waited for its keyframe on a busy PC. That added up to about 11 seconds of grey in your Beat Saber session.

- The last frame now stays up, **re-aimed to your head**, for up to **one second** before the grey recovery view appears. The *Waiting for video* card still shows after half a second.

### 5. Game Mode: the D-pad and stick go where you expect (client)
- **On a settings list, left and right change the focused setting.** They step to the previous or next value, and switches go off with left and on with right. Before, right jumped to *Back*, and left from *Back* could land on a row scrolled out of view.
- **Up and down step through the rows.** Rows scrolled out of view are reached only by stepping through the list, and lists stop at their ends instead of wrapping round to *Back*. Other pages still wrap from bottom to top.
- **Holding the D-pad, an arrow key or the stick repeats** after 0.38 s, then about nine steps a second (the stick about seven). The stick moves once when pushed past half way and lets go below a third.
- **One press moves once.** In Game Mode, Steam's virtual controller and the physical controller can both report the same press, as can a button and a key. Controllers are opened once and kept, so a lost release can't leave a direction repeating.
- The hint line shows *◀ ▶ Change* on settings lists.
- *Outside the sharp region* and the eye-tracking view showed *?* for ⅓ and ⅕ (the screen's font has no such characters). They now read 1/3 and 1/5.
- The session card says in two lines why the processor is held back and the one step that fixes it.

### 6. Reports show the whole session (both)
The reports kept only the last minute or two of detail, so a session that was good, then bad, then good again could not be read back. A client report now carries a **timeline**: one row per 10 seconds for the whole session (up to an hour). Each row has:

- frame rate (average and lowest), lost frames, keyframe requests, stalls, the longest stall and grey frames;
- eye-tracking samples a second, and the host's frames on your gaze, held, and centred;
- the processor's and GPU's clocks, how busy each is, power and temperature;
- the processor time of the session's programs: SteamVR, Steam, the desktop's compositor, the audio server, Baballonia, the eye cameras and VROSTBridge. Other programs are only summed as *other*, never named.

The eye-tracking relay also counts the pauses between samples, by length. Where you look is still never logged or reported.

### 7. Version 1.0.0.4 on both programs.

## Settings worth checking (from your reports)
- **GPU clock:** it was fixed at its maximum while the power limit was 28 W. Set it to automatic, or fix it lower (about 1600–2000 MHz on this GPU). Then check that the processor warning goes away. *Set the GPU clock to automatic* does it in one step.
- **Sharp region and outside:** *Small* with *Lightest* at Standard sends only 896 × 896 per eye, the most aggressive combination. With the region now held through pauses it should hold up better. If you still catch the coarse area after quick eye movements, *Medium* or *Balanced* hides the delay. For more resolution with eye tracking, High (2560 per eye) with *Medium* and *Balanced* sends about 1408 × 1408 per eye.
- **SteamVR background reprojection** is still off on the headset PC. Select **Repair** where the client page says so.

## How to test (please report each)
1. **Install** both over 1.0.0.3. The pages show *VROSTBridge Host 1.0.0.4* and *VROSTBridge Client 1.0.0.4*.
2. **The first session** runs the network test once more (about 15 s). The client log's *Stream:* line then says *pacing … Mbit/s, the most this network path carried without loss in its test*. Over a few minutes, *lost frames* on the session card should stay far below the 146 in 8 minutes of the last test.
3. **The processor warning:** with the GPU clock fixed at its maximum as before, the session card should say within about 15 s of streaming that the processor is held back. Choose *Set the GPU clock to automatic*: the warning should go, and *Eye-tracking samples* on the client page should rise.
4. **The sharp region:** look at fine detail near the edge of the view, hold your gaze there and blink. The detail should stay sharp. The host's log line `VROST_GAZE_FOVEATION` should show `held_frames` growing and `fallback_frames` growing far more slowly than `gaze_frames`.
5. **Beat Saber:** short hitches should no longer turn grey. A stall over a second still does.
6. **Game Mode:** step through *Stream & headset* with the D-pad, change values with left and right, hold a direction, and do the same with the stick.
7. **More resolution:** once the processor warning is gone, try High (2560) with eye-tracked foveation.
8. **Send reports from both PCs** after a session of several minutes, especially one with good and bad moments: the timeline shows when and why.

## Known limits
- The processor warning needs Linux to report the processor's minimum clock (`scaling_min_freq`), as it does on current AMD and Intel processors. Without it, no warning is shown. The one-click fix needs steamos-manager; elsewhere the warning says where to change the setting.
- The host driver was rebuilt with the same cross-build as 1.0.0.3 (patches 0001 to 0017). The hold is in the driver's Rust code and was checked with its own tests on Linux. It has not yet been tried on real hardware, and neither have the pacing, the warning or the Game Mode controls.
- Holding the last frame for up to a second turns it to follow your head (rotation only): if you walk during a long stall, the picture lags your movement until new frames arrive.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.4.json](../evidence/vrostbridge-build-1.0.0.4.json).
