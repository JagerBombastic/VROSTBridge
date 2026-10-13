# VROSTBridge 1.0.0.11

1.0.0.11 is about what the picture itself costs on the headset PC, so that a headset's full panel resolution (2560 × 2560 per eye on a Beyond) fits lighter headset PCs, and leaves room on the ones that already run it.

In the 1.0.0.8 test a Beyond streamed 2560 per eye at 75 fps with the headset PC's GPU about half busy. Of each 13.3 ms frame, SteamVR measured about 7 ms of GPU work: some 4.4 ms of it the client's own frame pass and plane copy, the rest SteamVR's compositor. With eye-tracked foveation the stream at 2560 is about as big to send and decode as Standard's was in the 1.0.0.9 test with fixed foveation (1408 × 1408 per eye after foveation, against 1408 × 1344), so the frame pass was the part left to make lighter. It writes every pixel the headset shows, once per refresh, and its cost grows with the shown size.

So 1.0.0.11:
- does the frame pass with about a third less work per pixel and the same picture;
- skips the pixels the headset's lenses never show;
- shows each eye a little smaller (90 %, then 80 %) while the headset PC's graphics hold the stream below the headset's refresh, instead of SteamVR halving the frame rate, and at full size again once there is room;
- measures the frame pass and the plane copy on the GPU, so the reports say what the picture costs;
- calls the Beyond's 2560 size **Native** (it was *High*).

Every package carries **1.0.0.11**. Everything in the [1.0.0.10](history/RELEASE_1.0.0.10.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.11.exe` over 1.0.0.10 or 1.0.0.9. Its driver is 1.0.0.9's, unchanged.
- **Headset PC:** install `VROSTBridge-Bazzite-Installer-1.0.0.11.zip` or `VROSTBridge-Client-Setup-1.0.0.11.exe` over the earlier one. Settings, pairing, Baballonia and its calibration stay.

Nothing on the network changed, so either PC still works with the other at 1.0.0.10. Installing both keeps them on one version.

## A lighter frame pass (both clients)
Per pixel the pass re-aims the frame to your head, undoes the foveated encoding, scales the picture (Catmull-Rom where it is sharp, a smooth B-spline where it was reduced), sharpens it and converts its colour. On the integrated GPUs typical of headset PCs it was bounded by arithmetic, not by reading the picture: the driver's own compiler statistics showed it. Most of that arithmetic was the foveation inverse's terms, about ten divisions per axis for every pixel, from numbers that are the same for the whole frame.
- **Worked out once per frame.** The client now computes those terms on the processor for each eye, axis and region of the axis (the reduced low edge, the sharp centre, the reduced high edge), with the texture size, the right eye's mirrored axis and its offset folded in. A pixel then needs one square root and one reciprocal per axis.
- **Each eye in its own layer of workgroups,** so everything that depends on the eye is the same across a group of pixels.
- **About a third less work per pixel**, in the reduced periphery and in the sharp centre alike, by the same compiler statistics for the integrated Radeon GPUs of handheld PCs (the GPU timing below will show the real figure).
- **The same picture.** The contract test compares the pass with its CPU model as before (its results match 1.0.0.10's to a hundredth of a level), and the new per-frame constants with ALVR's formula itself, to a thousandth of a texel.
- **One shader for every GPU.** Its constants now come from a small uniform buffer for each output image, so drivers with only the minimum push-constant space run the same shader; 1.0.0.7 needed a second one for them.

## Skipping what the lenses never show (both clients)
SteamVR says which part of each eye's image reaches the eye: the headset driver's visible-area mesh (the one games use to skip those pixels; not every headset has one), and its lens distortion of the panel's edge (the compositor never reads beyond it). The pass now computes only the pixels inside both, plus a margin of 3 % of the eye's size: when a frame is late, SteamVR shows the previous one turned to a newer head pose, which brings pixels near the border into view. Each output image is made black once, so what is skipped stays black. SteamVR does not say which way up the mesh is given, so the client checks it against the lens outline and turns it if that matches better, or leaves it out if it matches neither way; with no outline to check it against, it uses the mesh both ways up, so nothing it shows is skipped.

How much this saves depends on the headset: mostly the image's corners. The client log's `VISIBLE_AREA` line gives each eye's share of computed pixels and where the outline came from. A description that would leave less than half of an eye is ignored as faulty. *Skip what the lenses never show* (Preferences, on) turns it off, should the picture's edges ever look wrong.

## Keeping the frame rate (both clients)
When the frame pass and SteamVR's compositor miss the headset's refresh, SteamVR on the headset PC shows the stream at half rate and reprojects it. That judders much more than a slightly softer picture would.
- **One step smaller.** After 3 seconds within 10 in which SteamVR showed the stream below refresh while frames arrived on time and its GPU time per frame was at least 75 % of the refresh interval, each eye is shown at 90 % of its size, and at 80 % if that happens again. The frame pass then handles 81 % or 64 % of the pixels. The stream from the rendering PC does not change, so the detail still arrives and the headset PC resamples it once.
- **Back when there is room.** After a minute without such a second and with SteamVR's GPU time under 60 % of the interval, it goes back up a step. A step up that falls behind again within half a minute stays down for the rest of the session; one that falls behind within five minutes makes the next try wait twice as long, and after three of those it stays down too. So a PC at its limit does not swing back and forth.
- **Only when the graphics are the cause.** Seconds that fall below refresh with GPU time to spare (a busy processor, frames not arriving), or without SteamVR's GPU time to tell, are not counted, since a smaller picture might not help them.
- **Where to see it:** *Shown at 90 %* on the page beside the frame rate, the reason under *Keep the frame rate when this PC falls behind* (This PC, on, applies at once), a `SCALE` line in the log, and the size in the report's session timeline. If the stream keeps falling below refresh, the page gives the graphics-limit advice as before.

## What the picture costs, measured (both clients)
GPU time stamps around the frame pass and around the copy of each decoded frame into the pass's images give their GPU time each second (`frame_pass_gpu` and `plane_copy_gpu` on the client's `TIMING` line). The page shows *Frame pass … ms on the GPU* beside the frame rate, and the report's session timeline keeps it with SteamVR's own GPU time per frame. These figures say how much room a headset PC has left at a given size, and what 1.0.0.11's changes gained on real hardware.

## Native (Beyond)
The Beyond's 2560 size is now called **Native**: the panels' own resolution. With eye-tracked foveation it is about as light to send and decode as Standard with fixed foveation; without eye tracking (Subtle foveation on both) it carries about 60 % more pixels than Standard, so *Auto* still starts a Beyond at Standard and Native is one choice away.

SteamVR's render resolution on the headset PC still sets the largest size each eye is shown at (1.0.0.5); when it asks for less than the stream, the page now says so beside the stream size.

## How to test
Install 1.0.0.11 on both PCs. Choose **Native** with eye-tracked foveation (or Standard, for a comparison with earlier reports), play as usual, and watch the page's *Fresh video* line: *Frame pass … ms on the GPU*, and *Shown at …* if the picture was made smaller. Please look at the edges of the picture as well (turn your head quickly too): if anything looks cut off or dark there, turn off *Skip what the lenses never show* and tell me. Send reports from both PCs, also when everything works. If you have not tested 1.0.0.10 yet, test 1.0.0.11 instead: it contains all of it.

## Known limits
- **Nothing new here has run on hardware yet.** The frame pass matches its CPU model on Mesa's software Vulkan driver; its gain on real GPUs is estimated from the driver's compiler statistics until the reports' GPU times show it. The client's frame path from a decoded picture to the image SteamVR is given runs in the tests with SteamVR stood in for, on both clients (the Windows one under Wine, from CPU-decoded frames; the hardware decoders' hand-over is not part of it), and Vulkan's validation layers find nothing wrong with it. The lens areas are tested with stand-in meshes and outlines; showing smaller, with made-up TIMING seconds.
- **Not every headset driver gives SteamVR a visible-area mesh;** the lens distortion's outline is used where it does not.
- **The plane copy stays.** Sampling the decoder's own surfaces directly would save the copy but tie up the decoder's surfaces for as long as a frame is shown, and those surfaces are in a layout the pass reads more slowly; the copy's GPU time is now reported, to judge it.
- **AV1** is unchanged: where both PCs have it, AV1 needs fewer bits than HEVC for the same picture; choose it in the stream settings.
- The Windows client has still not run on real hardware (see 1.0.0.7).

Build and test evidence: [evidence/vrostbridge-build-1.0.0.11.json](evidence/vrostbridge-build-1.0.0.11.json).
