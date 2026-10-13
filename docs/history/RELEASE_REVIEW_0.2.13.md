# VROSTBridge 0.2.13 — fingers, stream scheduling and Beyond setup

This development release builds on the user's working Index connection. It adds
real finger-bone forwarding, overlaps video decoding with GPU conversion, and
adds automatic headset profiles in the desktop and Game Mode clients. Windows
execution, new finger behavior in Alyx, the new pipeline on the handheld, and Beyond
hardware operation still require physical testing. There is no measured speedup
claim for 0.2.13.

## Install both ends

1. Stop the current session from the client and exit both VROSTBridge apps.
2. Run `VROSTBridge-Host-Setup-0.2.13.exe` on Windows.
3. Extract `VROSTBridge-Bazzite-Installer-0.2.13.zip` on Bazzite and open
   **Install VROSTBridge.desktop**. Use the whole extracted folder.
4. Open the host and leave it waiting in the tray. Open the client from the
   desktop or its existing Steam Game Mode shortcut. Start the session there.

The installers replace their managed previous program files while retaining
settings and connection identity. They do not remove unrelated files or physical
headset drivers. Windows sign-in remains silent in the tray when enabled. Client
Stop closes both owned SteamVR sessions; the local scene/dashboard does not
become a fallback view.

Existing Index codec/resolution choices are preserved. Fresh installs select
**Auto** codec and **Automatic headset** resolution at 80 Mbit/s. To use automatic
selection after an upgrade, change those two settings while stopped.

## Finger tracking: what was wrong and what changed

The active Linux client previously collected buttons, sticks and squeeze values
but supplied no skeletal joints to ALVR. The host then inferred finger animation
from trigger/grip/touch inputs. This can look approximately right while losing
individual finger movement, especially in Alyx.

The client now reads the physical Index skeletal actions for both hands. It
converts OpenVR's parent-relative wrist/finger bones into the pinned ALVR
protocol's 26 global joints. The conversion inverts the host's coordinate and
handedness transforms, so it does not reinterpret 31 OpenVR bones as 26 OpenXR
joints. Real controller poses, buttons, force inputs and haptics remain separate.
The host uses the skeleton on the controller rather than creating a separate
optical-hand device.

Bone polling is bounded separately from the high-rate pose loop. Each pose
packet carries the latest valid bones rebased on that packet's controller pose;
this is necessary because the host matches skeletons by timestamp. Invalid,
disconnected or stale hands revert to the existing input-based animation rather
than retaining an indefinitely frozen finger pose. Per-hand diagnostics report
skeletal activity and read/conversion failures. Sending both hands at 250 Hz adds
about 2.9 Mbit/s of joint payload before transport headers; the hardware test must
also check that tracking remains steady under real Wi-Fi conditions.

The conversion preserves measured wrist/finger bones 1–25. The existing host
reconstructs the five auxiliary bones, derives its scalar curls, and uses one
source motion range for both OpenVR motion ranges. This is a substantial upgrade
over inferred button-only fingers, but is not full raw OpenVR skeleton parity.
The selected source range is **WithoutController**, for unconstrained virtual
hand movement. Index finger sensing is partial controller-based tracking.

### Alyx acceptance test

Use the same scene on both builds, with both controllers charged and tracking.
Check each hand independently: open palm, fist, pointing, thumb touches, gradual
finger closure, wrist rotation, grabbing, squeeze/release and throwing. Confirm
left-hand buttons/stick still work and the host menu opens. Then briefly lose
tracking on one hand and restore it: it must recover without a stuck hand or
swapped roles. Save a host report while connected, including a period where the
fingers are visibly wrong if anything remains off.

## Streaming: a concrete pipeline improvement

The previous path received a decoded VA-API surface and immediately mapped it
into Vulkan on the same worker that feeds compressed packets to the decoder.
The recent report showed that mapping averaged about 5 ms per frame. That wait
includes real hardware decoding and synchronization; it is not all avoidable
copying. During a wait, later encoded packets could accumulate.

The new path feeds compressed packets in order on one worker and converts decoded
frames on another. A small bounded mailbox keeps only recent unconverted decoded
images during a backlog. Discarding those images does not discard compressed
reference packets, which must still reach the decoder in order. Session/config
boundaries clear queued images; resources remain referenced until their GPU work
and compositor use are safe to retire. The existing source-pose submission,
neutral recovery image and optional late-frame selection remain.

The ALVR decoded/compositor timing boundary now follows the VA-API map wait.
New queue and readiness diagnostics distinguish decoded images superseded before
conversion from packet loss. Old and new decode/compositor stage numbers are
therefore not directly comparable: some time is classified more accurately.
`decode_sync_complete` includes time waiting in the decoded mailbox as well as
decoding/synchronization; `decoded_wait` reports that mailbox residence separately.
Neither value alone is a pure hardware-decode duration.
Compare frame age, fresh frames, repeated frames and long gaps, not just one
renamed stage. This change preserves selected dimensions, bitrate and codec.

### What could produce the next large gain?

The recent host report also showed roughly 10.7 ms median in ALVR's
encoder stage. That stage includes synchronization/submission work; it is not a
pure hardware-encoder duration. The existing AMD encoder already uses AMF's
ultra-low-latency mode, a speed preset and no B-frames/preanalysis. Replacing the
engine with Apollo does not automatically remove a missing fast path.

The next targeted host experiment is to instrument and improve AMF surface
submission/output draining and frame ownership, with a real Windows driver build
and controlled comparison. It is not silently included as an untested DLL in
this release. See [STREAMING_NEXT_STEPS.md](STREAMING_NEXT_STEPS.md) for the
source-based plan, HEVC comparison, conditional loss protection and ALXR benchmark
option. No AV1, FEC, native Vulkan decoding or eye-tracked foveation is claimed.

### Fair stream comparison on the Index

First use the same saved codec, dimensions, bitrate, refresh rate, game scene and
handheld power mode as before. Let startup settle, then spend around two minutes
looking around, moving sideways and watching a moving object. Save the host
report **while connected**, before pressing Stop. Compare freshness and gap
outliers as well as perceived smoothness. The prior report had a large stall
after Alyx closed, so include a short dashboard/idle period too.

For a separate codec experiment, stop and select **Sharper** (2016-pixel eye
width) with H.264 at 80 Mbit/s, then repeat with HEVC at the same settings. Using
Compatibility would change the width between codecs and spoil that comparison.
HEVC may preserve detail with less bandwidth, but lower latency is not assumed.
Keep whichever provides better measured consistency and image quality.

## Beyond 2 / 2e: automatic detection and display choices

The physical headset remains attached to Bazzite. At session start the client
starts its local runtime, reads the actual HMD identity, recommended render size
and active refresh rate, then negotiates the host. It does not reuse the previous
Index identity when the headset changes. The display section shows those values
separately from the encoded stream dimensions reported after video starts.

| Client selection | Behavior |
| --- | --- |
| Auto codec + Automatic headset | Beyond family selects HEVC with a maximum 2560-pixel per-eye stream width; Index selects H.264 with a 2016-pixel target. Unknown devices use a conservative profile. |
| Beyond 2560 | Explicit HEVC per-eye width budget, bounded by the runtime recommendation. |
| Beyond 3200 | Higher-detail HEVC width budget; increases host/network/decoder work. |
| Headset recommendation | Uses the actual runtime recommendation, within codec size limits. This can be substantially more expensive than the automatic profile. |
| Compatibility / Sharper | Retained comparison/fallback settings. Sharper uses the same 2016-pixel width for both codecs. |

Height follows the actual headset aspect ratio. Dimensions align to encoder
requirements. H.264 is capped at 4096 pixels for the complete side-by-side frame;
HEVC is capped at 8192. These software bounds are not a guarantee of a particular
GPU driver's supported throughput. Beyond-specific profiles require HEVC or
Auto; selecting one with an Index produces a clear configuration error.

The Beyond panel is 2560 × 2560 per eye at both 75 and 90 Hz. Bigscreen documents
a different signal/upscaling path at 90 Hz, and its compositor render targets can
be larger than panel dimensions. A 2560 stream target therefore does not mean
full lens-corrected supersampling or a physical display-mode switch. See
[Bigscreen's display explanation](https://bigscreenvr.com/displays/) and
[its render-resolution guidance](https://bigscreen-inc.frontkb.com/en/articles/2740032).

VROSTBridge follows the **detected active refresh rate**. It does not write the
Beyond's firmware/display configuration or pretend a web selection changed
75/90 Hz. Physical mode changes use the vendor's supported setup path; stop the
bridge before changing that mode, then start again to detect it afresh. Bigscreen
notes that Linux operation is possible but is not officially supported; this
release still needs validation on the user's AMD/Bazzite setup. See
[the vendor setup guide](https://bigscreenvr.com/mybeyond/) and
[Linux support statement](https://bigscreen-inc.frontkb.com/en/articles/2751168).

For the first Beyond test, confirm its local SteamVR display and tracking first,
then choose Auto / Automatic headset and start the bridge. Check the detected
model, active Hz and negotiated eye size before trying higher-detail targets.
Use the same Index controllers and Lighthouse setup; ensure room setup is valid
for the physical headset. Beyond 2e camera/gaze helper support is retained, but
calibrated Linux eye tracking and dynamic foveated rendering remain separate work.

## Known separate issue and attribution

The user confirmed the left-eye corruption also occurs in local SteamVR on the handheld with
no host connection. This release does not claim to fix that local display path.
Keep its diagnosis separate from network artifacts and frame-pacing comparisons.

ALVR, OpenVR, FFmpeg, Vulkan, the launcher/runtime dependencies and optional
camera helper retain their existing licences and notices. The skeleton conversion
explicitly credits the pinned ALVR transforms. See
[OPEN_SOURCE_CREDITS.md](../OPEN_SOURCE_CREDITS.md).

## Validation

- Python: 204 cases, 202 passed and two platform-specific skips.
- C++: all eight contract groups passed, including the independent skeletal
  transform fixtures and concurrent decoded-mailbox ownership tests.
- Native Linux client compiled and its loader/help path passed without a private
  library path. The current binary and source hashes are recorded.
- Desktop workflow checks passed. Game Mode layout was inspected using the actual
  SDL renderer with synthetic Beyond metadata; this is not headset evidence.
- All 12 checks on the actual frozen Bazzite package passed: fresh install,
  idle/background launch, web controls, Game Mode lifecycle, reinstall/repair,
  damaged-package detection, upgrade from 0.2.12, cleanup and uninstall retention.
- Final archive CRCs, payload SHA-256 manifests, Windows AMD64 imports, embedded
  icon and NSIS executable CRC passed. The Windows installer payload was extracted
  and checked against its manifest.

Evidence is included under `docs/evidence` in the source archive. These checks do
not execute the Windows host or prove physical headset performance. Test the
Index/Alyx behavior first, then the new Beyond with the automatic profile.
