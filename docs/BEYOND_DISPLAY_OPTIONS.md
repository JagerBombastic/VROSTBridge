# Beyond 2 / 2e display setup

Updated 2026-10-15 for VROSTBridge 1.0.0.13, in which the stream follows the Beyond's display mode (first written for RC1.3). Local Beyond display and bridged video have been observed; the RC1.3 projection correction still needs physical confirmation.

Before the first Beyond test, stop the stream and select **Auto** for the codec in
the client; the stream resolution has nothing to choose on a Beyond (below). Choices
saved for an Index survive upgrades and a change of headset: they wait for the Index
while the Beyond follows its mode.

VROSTBridge starts the local runtime and reads the actual headset identity,
application render recommendation and effective physical refresh rate before configuring
the host. Detection repeats at each Start. Stop before swapping headsets. A driver
that identifies only “Beyond” is shown by that name; the bridge does not guess its
model revision.

The desktop and Game Mode controls show the expected **per-eye width × height**.
From 1.0.0.13 they do so before a session too: from the headset of the last verified
session, or on Bazzite from the display of a Beyond plugged in now (its EDID), in which
case the size is known on Start.

The Beyond 2 and 2e have two display modes: 75 Hz with 2560 × 2560 per eye, and 90 Hz,
in which the panels take 1920 × 1920 and the headset upscales it. From 1.0.0.13 the stream
follows the mode the headset is in, with nothing to choose, on Bazzite and Windows alike:
a stream larger than the mode shows only costs decoding time, and a smaller one loses
detail the panels could show.

| Beyond mode | Stream width per eye | Codec |
| --- | --- | --- |
| 75 Hz | 2560, the panels' own resolution (*Native* since 1.0.0.11) | HEVC or AV1; 1920 with H.264, whose limit is 2048 |
| 90 Hz | 1920, what the panels take at 90 Hz | Any |

Only Bigscreen's Beyond Utility, on a Windows PC, switches the mode: VROSTBridge never
does, and the stream resolution shows the mode the headset is in (*Set by the headset*).
The other sizes (Standard 2016, Ultra 3200, SteamVR app size) went in 1.0.0.13, and one
saved earlier is ignored. Until 1.0.0.12 Auto was Standard, chosen in RC4.3 when a handheld
headset PC could not hold 2560; 1.0.0.11's lighter frame pass holds it. If the headset PC's
graphics still fall behind at 2560, its advice names the 90 Hz mode, about a third fewer
pixels a second. A rendering PC still on 1.0.0.12 does not know the 90 Hz mode's 1920 and
gets 2016, as 1.0.0.12 streamed. The client GPU's smaller local application
recommendation does **not** cap the stream (from 1.0.0.5 a larger stream is shown at that
recommended size). Width aligns down to 32 pixels,
and the runtime aspect ratio determines aligned height. Status shows actual
negotiated video dimensions once streaming. Per-eye stream size, side-by-side
encoded frame size, application recommendation and physical panel size are
separate quantities.

**The refresh rate.** VROSTBridge never asks SteamVR for a Beyond refresh rate (it does only
for a rate chosen for an Index). On Bazzite the headset keeps the mode the Beyond Utility last
set, and Linux sees only that mode; on Windows the mode is left to the Beyond Utility as well.
VROSTBridge streams at the effective rate it measures from SteamVR's advancing display timing,
and it never changes brightness or firmware.
Higher resolutions increase rendering, encoding and decoding work and are not
promises that a given dock, decoder or mode will work. The stream is not reduced
mid-session; from 1.0.0.11 the headset PC may show each eye a step smaller (90 %,
then 80 %) while its graphics hold the stream below the refresh rate, with *Keep the
frame rate when this PC falls behind* on, and the page says so.

For the first test use Auto, retain the existing bitrate, and run the same scene
used for the Index comparison. Check identity, active Hz, codec and actual video
size. Then Stop, change one quality setting, restart and save a report. Keep gaze
off until display, tracking and audio work.

Beyond 2e eye cameras and calibrated gaze are a separate path. Display detection
does not enable or calibrate gaze. See [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md).
Beyond display/stream connection has since been observed. RC1.3 projection correctness, Linux gaze calibration and Game Mode display leasing still need physical acceptance. Software tests do not qualify those hardware paths.

Vendor references retained for display/runtime setup:
[displays](https://bigscreenvr.com/displays/),
[performance and render resolution](https://bigscreen-inc.frontkb.com/en/articles/2740032),
[Beyond Utility](https://bigscreen-inc.frontkb.com/en/articles/2700928).

## Refresh and projection corrections

RC1.2 adds a guarded Beyond-only check for a runtime property reporting 90 Hz while physical timing advances at 75 Hz. It negotiates the measured effective rate after a stable observation; it does not force a hardware mode (from 1.0.0.13 VROSTBridge sets SteamVR's preferred rate only for a rate chosen for an Index). Keep the local working adapter/mode. See [refresh notes](history/BEYOND_REFRESH_1.0.0-rc1.2.md).

RC1.3 corrects the OpenVR raw vertical tangent conversion, preserving asymmetric optical centres. Full per-eye transforms and live IPD handling remain intact. It adds static `view_geometry` to reports so further faults can be investigated. Test eye alignment, scale and head-motion stability before performance tuning. Correct left/right eye placement alone does not prove correct projection. See [projection notes](history/PROJECTION_1.0.0-rc1.3.md).

Nominal panel pixels, the local SteamVR application recommendation and the remote encoded stream size are different values. Neither a preset name nor a 2560-wide stream is a lens calibration. Brightness, firmware and the display mode stay with Bigscreen's tools.
