# VROSTBridge 0.2.12 — physical headset identity and image detail

The Index is working. This update addresses the host identifying it as an Oculus
headset and the conservative streaming resolution. It retains 0.2.11's graphics
queue coordination and late-frame option. It does not claim that the left-eye
artifacts or network stalls have been fixed.

## Findings from the latest report

`VROSTBridge-session-report(1).json` is a 0.2.11 host report captured after stopping,
with the client report retained. The streaming data before shutdown is usable;
the final zero decoder counters describe shutdown, not a failed whole session.
Only late-frame catchup enabled is present, so this is not the requested on/off
comparison. Retained windows also cover different scenes and periods between
builds. The raw reports are not redistributed in the source package.

| Retained measurement | Earlier 0.2.10 | Latest 0.2.11 |
| --- | ---: | ---: |
| Stream | H.264, 80 Mbit/s, 90 Hz | H.264, 80 Mbit/s, 90 Hz |
| Video size per eye | 1440 × 1568 | 1440 × 1568 |
| ALVR pipeline estimate, median | 37.54 ms | 35.98 ms |
| ALVR network component, p95 | 4.99 ms | 46.86 ms |
| ALVR network component, maximum | 12.08 ms | 175.51 ms |
| Packet-arrival gap, largest retained client window maximum | 32.21 ms | 197.72 ms |
| Fresh-image gap, largest retained client window maximum | 55.59 ms | 266.85 ms |
| Source frame age, session p95 | 27 ms | 50 ms |

Both host comparisons use 233 retained graph samples. Client timing comparisons
use windows with packets present (101 old, 82 new), including slow streaming
windows; they do not use only the healthiest periods. These quantities are not
motion-to-photon latency and cannot be summed into an exact end-to-end budget.
The network component can include transport/receiver scheduling effects; it is
not a direct Wi-Fi signal measurement. The latest run has a timeout/reconnect
around 20:34:13 UTC. Its normal median is slightly better but the stalls are
worse, so a clear smoothness improvement is not established.

The latest client reports no explicit-timing errors, rejected decoder packets or
unmatched timing records in the retained streaming windows. This does not prove
that every displayed pixel is correct. Median map/synchronization/import time is
about 3.66 ms, while other conversion CPU work is about 0.24 ms. Rendering or
decoder optimizations alone will not hide a roughly 198 ms packet-arrival gap.

The Windows vrserver log identifies the virtual HMD as `oculus_hmd`. The shipped
profile leaves headset emulation at Quest 2, even though its controllers already
emulate Index. That mismatch is corrected below.

## What changes

### Physical headset identity on the host

The existing short-lived OpenVR background verifier now reads the connected
headset's model, manufacturer, tracking system, render model, input profile,
registered type, selected status icons, recommended render size and refresh rate.
The client starts its local stream-only runtime first, validates that description,
then starts the host with it. A missing description fails with an actionable
message instead of launching a guessed HMD. Cancellation and startup failure
still close the owned runtimes and restore local preferences.

The host uses ALVR's supported Custom HMD profile and extra OpenVR properties.
Index metadata therefore comes from the actual Index; a connected Beyond is not
automatically mislabeled as an Index. The virtual serial is stable and derived
from the physical serial, with a VROSTBridge prefix to avoid colliding with a
locally attached device. Existing unrelated custom properties are retained.
String sizes are bounded before reaching the pinned native property bridge.
Resources use symbolic driver paths, not Linux filesystem paths or URLs.

This improves device identification and appropriate input/profile selection. It
does **not** turn the network into USB passthrough or guarantee every proprietary
SteamVR menu. Firmware updates, cameras, base-station Bluetooth and physical
brightness/refresh controls are not forwarded. Unsupported capabilities are not
advertised as functional. Those controls need real client-side operations and
remote commands before they can work from the host. Live IPD/eye geometry,
tracking, controllers, audio and rectangular play-area transfer are retained.
The active refresh rate continues to be negotiated from the physical runtime.

The corresponding-source checks used the pinned ALVR
[property bridge](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_openvr/src/props.rs),
[HMD implementation](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_openvr/cpp/alvr_server/HMD.cpp),
and [property-key generator](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/session/build.rs).
No upstream binary or licence is replaced.

### Explicit streaming resolution

The client web UI now has a separate Stream resolution setting; Game Mode has a
Resolution button cycling through the same saved choices.

| Choice | Requested width per eye |
| --- | --- |
| Compatibility / previous | 1440 for H.264; 2144 for HEVC, retaining the previous behavior |
| Sharper | 2016 for either codec |
| Headset recommendation | The local SteamVR recommended render width, bounded by the limits below |

Height follows the physical headset's recommended aspect ratio. The host aligns
dimensions to 32 pixels. H.264 is capped at 2048 pixels per eye (4096 side by
side); HEVC is capped at 4096 per eye (8192 side by side). These limits avoid an
unbounded request and are not a guarantee that every GPU supports every size.
The negotiated dimensions, codec, headset model and physical refresh rate are
visible on both web UIs and included in reports. Game Mode also shows dimensions.

The previous choice stays selected on upgrade. For the Index clarity test,
explicitly choose **Sharper — 2016 pixels per eye**, leaving H.264, 80 Mbit/s and
90 Hz unchanged initially. This increases the pixel count by approximately 96%
at the same aspect ratio, before alignment. It adds encoding/decoding load and
can expose compression limits at the same bitrate. Increasing SteamVR rendering
resolution alone cannot remove a lower-resolution streaming bottleneck.

## Install and test

1. Stop the session, exit both companions and install both 0.2.12 packages over
   the previous installation. On Bazzite extract the whole ZIP and open
   **Install VROSTBridge.desktop**. Existing identity/settings are retained.
2. Start once with Compatibility selected. Confirm the client identifies the
   physical Index and the host no longer identifies its HMD as Oculus/Miramar.
   Test the host dashboard and both controllers. If a specific menu is still
   missing, record its exact name or take a screenshot; identity alone may not
   supply that operation.
3. Stop, select Sharper, restart and compare the same scene. Confirm the displayed
   negotiated width is 2016 per eye. Keep bitrate/codec/refresh unchanged for
   this test and save the host report while connected.
4. Separately, try a temporary Ethernet connection for the handheld (and wired host),
   with Wi-Fi disconnected during the comparison so the route is unambiguous.
   This is a diagnostic for the large arrival gaps, not a change to the Wi-Fi
   project goal. If the pauses improve, investigate the wireless path before
   adding more buffering or reducing image quality.

## Left-eye fault: local reproduction confirmed

The user subsequently confirmed that the left-eye display errors also occur
when running SteamVR locally on the handheld without a host connection. The host
encoder and network video are therefore not required for this fault. This does
not identify Bazzite alone: the local SteamVR/graphics path and the handheld-to-dock-to-
headset display path remain candidates. It is separate evidence from the large
packet-arrival gaps responsible for some streamed-session stalls.

**Next comparison:** local SteamVR under Windows on the same handheld, keeping the
same dock, headset, cables and refresh rate. A clean Windows result would strongly
implicate the Linux graphics/runtime path; corruption in both would make bypassing
the dock the next useful isolation step. Do not replace hardware or reinstall the
OS solely on the current evidence.

Local reproduction without a host connection is confirmed. The report does not
establish whether the companion was fully exited during that test. The remaining
checks below change one variable at a time:

1. **Local baseline — host connection already excluded:** if VROSTBridge was
   still open during the confirmed test, exit it fully before further comparisons.
   Use local SteamVR, the same dock/cables and 90 Hz. This also separates any
   still-running companion behavior from the local SteamVR baseline. The bridge
   itself still never falls back to local Home during a session.
2. **Compare the image:** during an affected run, look at the left-eye image in
   SteamVR's desktop VR View as well as the headset. If the artifact appears in
   the software view it is already in that captured image path. A clean mirror
   with a faulty headset points further downstream, but does not by itself
   distinguish compositor scanout, the driver, DisplayPort, cable or panel.
   A short phone video through the affected lens is useful for distinguishing
   speckles/lines from block-shaped compression damage.
3. **Display link:** if it also happens locally, power down the headset and dock,
   reseat the DisplayPort/USB-C connections and repeat. If available, test a
   suitable direct USB-C-to-DisplayPort adapter to bypass the dock. Do not buy a
   replacement cable or dock solely on this log's evidence.
4. **OS comparison:** if local Bazzite still shows it, repeat local SteamVR on
   the same handheld/dock under its Windows installation, if SteamVR is available.
   This keeps the hardware path much more comparable than moving to the tower.
   A Bazzite-only result would make its graphics/runtime path a stronger suspect.

The report and local reproduction do not identify the remaining local cause conclusively. One-eye-only
corruption is possible in software as well as in the display path; a side-by-side
compressed frame can also be damaged in only one region. Valve's tracker has
older, closed [left-eye](https://github.com/ValveSoftware/SteamVR-for-Linux/issues/299)
and [right-eye](https://github.com/ValveSoftware/SteamVR-for-Linux/issues/480)
corruption reports, but they do not establish this as the same bug. Do not apply
old RADV workaround flags or reinstall Bazzite on that basis.

## Validation and limits

Automated checks cover metadata parsing and size limits, host identity properties,
native controller retention, custom property preservation across headset changes,
stereo-resolution bounds, GUI/Game Mode settings, cancellation and cleanup with
the new startup order. Native C++ and frozen Python companions are rebuilt;
installer upgrade and archive integrity results are included under docs/evidence
in the source archive.

Windows execution, physical Index/Beyond menu behavior, sharper-stream performance
and the left-eye fault still require hardware testing. No measured 0.2.12 speedup,
full Index USB feature parity, or artifact elimination is claimed.
