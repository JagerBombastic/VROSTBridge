# Beyond 2 / 2e display setup

Reviewed 2026-10-18 for VROSTBridge 1.0.0.14. Beyond streaming is in regular use; a deliberate check of stereo alignment, scale and optical centre is still on the [hardware acceptance](HARDWARE_ACCEPTANCE.md) list.

For a first Beyond session, select **Auto** for the codec (or **Use recommended settings**); the stream resolution has nothing to choose on a Beyond (below). Choices saved for an Index survive upgrades and a change of headset: they wait for the Index while the Beyond follows its mode.

## What VROSTBridge reads

At each Start, VROSTBridge starts SteamVR on the headset PC and reads the headset's identity, the application render size SteamVR recommends and the refresh rate the display really runs at, before it configures the rendering PC. Stop before swapping headsets. A driver that identifies only “Beyond” is shown by that name; VROSTBridge does not guess the model revision.

The desktop and Game Mode pages show the expected **per-eye width × height** before a session too: from the headset of the last verified session, or on Bazzite from the display of a Beyond plugged in now (its EDID), in which case the size is confirmed at Start.

## The stream follows the display mode

The Beyond 2 and 2e have two display modes: 75 Hz with 2560 × 2560 per eye, and 90 Hz, in which the panels take 1920 × 1920 and the headset upscales it. The stream follows the mode the headset is in, with nothing to choose, on Bazzite and Windows alike. A stream larger than the mode shows only costs decoding time on the headset PC, and a smaller one throws away detail the panels could show, so matching the mode is the right size in both directions.

| Beyond mode | Stream width per eye | Codec |
| --- | --- | --- |
| 75 Hz | 2560, the panels' own resolution (*Native*) | HEVC or AV1; 1920 with H.264, whose limit is 2048 |
| 90 Hz | 1920, what the panels take at 90 Hz | Any |

Only Bigscreen's Beyond Utility, on a Windows PC, switches the mode: VROSTBridge never does, and the stream resolution shows the mode the headset is in (*Set by the headset*). If the headset PC's graphics fall behind at 2560, its advice names the 90 Hz mode, about a third fewer pixels a second.

The rendering PC renders larger than the stream (at the size the headset PC's SteamVR recommends, up to one and a half times the stream) and scales down for the encoder, so the lens-distortion headroom a directly connected headset gets is kept; see the [technical reference](TECHNICAL_REFERENCE.md). The headset PC's own smaller application recommendation does **not** cap the stream: a larger stream is shown at that recommended size. Width aligns down to 32 pixels, and the runtime's aspect ratio sets the aligned height. Once streaming, the status shows the negotiated video size. Per-eye stream size, the side-by-side encoded frame, the application recommendation and the physical panel size are separate quantities.

## The refresh rate

VROSTBridge never asks SteamVR for a Beyond refresh rate (it does only for a rate chosen for an Index). On Bazzite the headset keeps the mode the Beyond Utility last set, and Linux sees only that mode; on Windows the mode is left to the Beyond Utility as well. A Beyond can report 90 Hz while its display timing advances at 75 Hz, so VROSTBridge streams at the effective rate it measures from SteamVR's advancing display timing after a stable observation, rather than the reported property. It never changes brightness or firmware.

Higher resolutions increase rendering, encoding and decoding work and are not promises that a given dock, decoder or mode will keep up. The stream size does not change mid-session; with *Keep the frame rate when this PC falls behind* on, the headset PC may show each eye a step smaller (90 %, then 80 %) while its graphics hold the stream below the refresh rate, and the page says so. That keeps the full frame rate, which matters more in a headset than the last few per cent of sharpness.

## Projection

The headset PC sends each eye's raw projection to the rendering PC, preserving the Beyond's asymmetric optical centres, with full per-eye transforms and live IPD. Reports carry a static `view_geometry` block so a geometry fault can be investigated. When testing, check eye alignment, scale and head-motion stability before tuning performance: correct left/right placement alone does not prove correct projection. The background is in the [refresh notes](history/BEYOND_REFRESH_1.0.0-rc1.2.md) and [projection notes](history/PROJECTION_1.0.0-rc1.3.md).

Nominal panel pixels, the local SteamVR application recommendation and the encoded stream size are different values. Neither a preset name nor a 2560-wide stream is a lens calibration. Brightness, firmware and the display mode stay with Bigscreen's tools.

## Eye tracking

The Beyond 2e's eye cameras and calibrated gaze are a separate path: display detection does not enable or calibrate gaze. Keep eye tracking off until display, tracking and audio work, then see [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md).

Vendor references: [displays](https://bigscreenvr.com/displays/), [performance and render resolution](https://bigscreen-inc.frontkb.com/en/articles/2740032), [Beyond Utility](https://bigscreen-inc.frontkb.com/en/articles/2700928).
