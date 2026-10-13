# Projection correction — Bazzite client 1.0.0-rc1.3

Install this client over RC1.2 and retain the existing RC1 Windows host.
The previous Beyond 75/90 Hz readiness correction is included.

## Finding

The new report confirms a Beyond session with host video at 75 Hz, H.264,
2016 × 2016 per eye and a requested bitrate of 200 Mbit/s. The user reports
incorrect projection with the eyes on their correct sides. The report does not
record actual optical frusta, so it cannot establish the size of a projection
error on this headset.

Code review found an incorrect conversion at the client OpenVR-to-ALVR boundary.
OpenVR's raw projection names are counterintuitive: `top` represents the -Y
edge and `bottom` represents +Y. We previously generated ALVR `up=atan(-top)`
and `down=atan(-bottom)`. The existing ALVR host converts `down` back to OpenVR
`top`, and `up` back to `bottom`, producing `top=-bottom` and `bottom=-top`.
That mirrors the vertical optical-centre offset for asymmetric projections.
A vertically symmetric test does not reveal the error.

The corrected mapping is `up=atan(bottom)`, `down=atan(top)`. Horizontal
values, left/right eye selection, full eye transforms and texture orientation
are retained. This is a general API conversion correction; it does not guess
the Beyond's FOV or introduce a headset-specific calibration constant.

Sources checked:

- [Valve OpenVR driver documentation](https://github.com/ValveSoftware/openvr/blob/master/docs/Driver_API_Documentation.md),
  `IVRDisplayComponent::GetProjectionRaw` and its vertical-convention note.
- [Pinned ALVR host conversion](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_openvr/cpp/alvr_server/Utils.h),
  `fov_to_tangents`.
- [Pinned ALVR HMD driver](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_openvr/cpp/alvr_server/HMD.cpp),
  `SetViewParams` and `GetProjectionRaw`. This host already preserves full
  eye-to-head rotation and translation; no host driver change is needed for
  this conversion fix.

This is a confirmed code defect and a plausible explanation for the visual
complaint. It is not yet a physically verified resolution of that complaint.

## Diagnostics

The client records static per-eye raw projection tangents, transmitted FOV
angles, eye-to-head transforms, convention and revision on startup and valid
geometry changes. The latest values remain in `view_geometry` in saved reports
even after the scrolling startup log has expired. A new native session clears
them before collecting fresh geometry. The existing host's full client-report
collection carries these fields without needing a host update.

No live world-space poses or eye-tracking samples are added. Geometry is not
repeated in the once-per-second network summary.

## Other observations kept separate

Client presentation sometimes reached about 75 unique frames/s, and sometimes
fell to roughly 37 frames/s while the host stayed near 75. This needs a separate
performance investigation after projection correctness is established. This
update preserves codec, bitrate, resolution and pacing so comparisons isolate
the projection change.

The host also loaded the installed Beyond helper, which logged unsuccessful
IPD operations against the virtual headset. The report does not prove that this
helper changed the projection. No physical driver is disabled or removed.

## Install and test

1. Stop the bridge session and quit VROSTBridge Client.
2. Extract the entire **VROSTBridge-Bazzite-Installer-1.0.0-rc1.3.zip** and open
   **Install VROSTBridge.desktop**. Existing settings and pairing are retained.
3. Confirm the client page shows **1.0.0-rc1.3**. Keep the RC1 host installed.
4. Start from the client using the same quality settings as the previous test.
   Compare a stationary scene first, then gentle head rotation. Check whether
   the views combine comfortably and whether straight edges stay stable.
5. Save the host report while connected, or immediately after Stop. It should
   contain `remote_diagnostics.client_report.view_geometry`. If the host cannot
   collect the report, save it directly from the client.

If the view remains wrong, describe whether the remaining problem is a vertical
offset between eyes, incorrect scale, rotation/skew, or warping during movement.
The new optical data will distinguish a conversion issue from other causes.

## Validation and scope

Regression checks exercise asymmetric projection round-trips, preservation of
the optical centre, unchanged symmetric projections and full eye transforms,
live IPD history, invalid geometry rejection, concurrent geometry reads, and
the RC1.2 display-timing rules. Report tests cover persistence beyond scrolling
logs and clearing old values on a new session.

Both client native binaries and the desktop companion are rebuilt. The ALVR
transport, FFmpeg decoder libraries, shader, OpenVR loader and other native
helpers remain unchanged. Credits and licences are retained. Existing
public-alpha redistribution limitations remain documented in
OPEN_SOURCE_CREDITS.md.

Exact build evidence and package checks are in
`docs/evidence/projection-1.0.0-rc1.3.json` and
`docs/evidence/client-package-1.0.0-rc1.3.json`.
