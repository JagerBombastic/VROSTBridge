# Beyond 2 / 2e display setup

Updated 2026-10-12 for VROSTBridge 1.0.0.11 (first written for RC1.3). Local Beyond display and bridged video have been observed; the RC1.3 projection correction still needs physical confirmation.

Before the first Beyond test, stop the stream and select **Auto** for both codec
and stream resolution in the client. Existing manual Index preferences survive
upgrades; they are not silently replaced when a different headset arrives.

VROSTBridge starts the local runtime and reads the actual headset identity,
application render recommendation and effective physical refresh rate before configuring
the host. Detection repeats at each Start. Stop before swapping headsets. A driver
that identifies only “Beyond” is shown by that name; the bridge does not guess its
model revision.

The desktop and Game Mode controls share the same choices and show expected
**per-eye width × height** after detection. Before detection they show Auto plus
any saved manual preference, without inventing dimensions.

| Beyond choice | Requested width per eye | Codec |
| --- | --- | --- |
| Auto | 2016 (Standard; since RC4.3, so a handheld headset PC keeps up) | Auto selects HEVC |
| Standard | 2016 | Selected codec |
| Native (until 1.0.0.10 *High*) | 2560, the panels' own resolution | HEVC or AV1 |
| Ultra | 3200 | HEVC or AV1 |
| SteamVR app size | Current application recommendation, capped at 2048 for H.264 or 4096 for HEVC | Selected codec |

Native and Ultra retain the explicit stream budget; the client GPU's smaller local
application recommendation does **not** cap the stream (from 1.0.0.5 a larger stream is
shown at that recommended size). Width aligns down to 32 pixels,
and the runtime aspect ratio determines aligned height. Status shows actual
negotiated video dimensions once streaming. Per-eye stream size, side-by-side
encoded frame size, application recommendation and physical panel size are
separate quantities.

These are stream-resolution controls. VROSTBridge follows the effective physical refresh
validated from SteamVR and advancing display timing; it does not change headset refresh, brightness or firmware.
Use the available supported headset/runtime controls before starting the bridge.
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

RC1.2 adds a guarded Beyond-only check for a runtime property reporting 90 Hz while physical timing advances at 75 Hz. It negotiates the measured effective rate after a stable observation; it does not force a hardware mode or rewrite SteamVR refresh preferences. Keep the local working adapter/mode. See [refresh notes](history/BEYOND_REFRESH_1.0.0-rc1.2.md).

RC1.3 corrects the OpenVR raw vertical tangent conversion, preserving asymmetric optical centres. Full per-eye transforms and live IPD handling remain intact. It adds static `view_geometry` to reports so further faults can be investigated. Test eye alignment, scale and head-motion stability before performance tuning. Correct left/right eye placement alone does not prove correct projection. See [projection notes](history/PROJECTION_1.0.0-rc1.3.md).

Nominal panel pixels, the local SteamVR application recommendation and the remote encoded stream size are different values. Neither a preset name nor a 2560-wide stream is a lens calibration. Physical brightness/firmware/display-mode controls remain with the local runtime/vendor tools.
