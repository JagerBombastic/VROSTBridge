# VROSTBridge 0.2.4 — first-stream follow-up

24 September 2026. Matching Windows host and Bazzite client development release.

## What the first test establishes

The user reports a real remote Index image, an average source-frame age around
20 ms, intermittent floor flashes, a local dashboard when pressing the physical
system button, and tracking judder. This is first end-to-end evidence for the
previous build. It is not a measured motion-to-photon result, and it does not
validate this new revision. The host log also shows a negotiated-settings restart
that timed out waiting for SteamVR, followed by an unexpected runtime exit.

The architecture remains appropriate: Windows SteamVR renders through the pinned
ALVR virtual headset; the native Bazzite client decodes, converts and submits the
remote stereo image to local SteamVR. Local SteamVR supplies Lighthouse tracking,
physical Index/Beyond display support, lens correction and reprojection. Removing
that compositor would require replacing those functions. Rendering SteamVR Home
on the client is not required.

## Changes in this release

| Area | Implementation and limits |
|---|---|
| Client runtime | The page's Start session, including automatic startup, probes SteamVR with a short-lived background OpenVR helper and starts SteamVR through Steam when needed. It waits up to 60 seconds and reports a display/runtime failure instead of claiming a connection. Steam and SteamVR must already be installed and have completed their first-run setup. |
| Lightweight mode | Enabled by default. Temporarily sets local SteamVR Home and dashboard off through IVRSettings. A saved receipt retains previous preferences; Stop restores them. If SteamVR closes or the app crashes, the receipt survives and is recovered at the next start. A Restore local menus button is available for pending restoration while stopped. This does not remove the local compositor or physical boundary. Live response to these settings still needs the Index test. |
| Floor flashes | Input focus no longer gates tracking/video. A valid remote image is reprojected through short gaps, up to 250 ms source age; it stops counting as fresh at 100 ms. Longer gaps or lost tracking use a flat dark recovery frame. This addresses our own focus/fallback path, but cannot prove that every observed flash came from it. |
| Host menu | Both pages have Open host menu. The client also maps left B + left thumbstick click to the host system action and consumes those two clicks while the chord is held. SteamVR reserves the physical Index system button; direct system-button forwarding is not implemented. The shortcut can briefly send the first button before the full chord forms, so try the desktop button first. |
| Frame pacing evidence | Reports include local input focus, valid tracking, unique/reused submissions, submit failures, fallback frames, tracking sample count/max gap, and SteamVR GPU/frame/reprojection timing. ALVR receives a bounded compositor time-remaining estimate instead of an unconditional zero. This estimate is not a measured scanout timestamp. Judder remains a target-machine investigation. |
| Room transfer | The client sends its rectangular standing play-area dimensions on connection and chaperone changes; Resend play area retries manually. The host receives the same dimensions through the small diagnostic summary, applies them through a background OpenVR helper, and verifies them. The host page distinguishes waiting from applied and verified. |
| Host chaperone | The pinned ALVR updater deliberately hides the host chaperone and builds a perimeter at ±width/depth rather than half extents. The helper corrects the rectangle to ±width/2 and ±depth/2 and enables a nonzero fade distance when previously disabled. It saves the prior host chaperone before applying. The physical client's boundary remains primary. This transfers a rectangle, not an exact irregular boundary polygon. |
| Host restart recovery | The host first requests graceful SteamVR shutdown. If that times out or its service disappears, recovery can terminate only processes recorded after this launch, in the registered SteamVR directory, with matching PID, executable path and creation time. It rechecks creation time on the opened process handle. Unknown/reused processes are refused. Quit events are acknowledged by the native client. |
| Desktop/install | Role-correct headlines, supplied artwork and open-source credits remain. Both installers upgrade managed previous versions, retain settings/trust/identity/reports, and remove owned old payloads. Bazzite uninstall retains the application if it cannot restore pending runtime preferences, with instructions to open SteamVR and retry. |

## Diagnostic behaviour

The host also subscribes to ALVR's existing loopback `/api/events` stream using
websockets 16.0. It retains encoder/network/FPS summaries and samples graph timing
at most once per second. Informational/error logs are rate-limited; raw session,
tracking, button and gaze event payloads are ignored. This works without enabling
ALVR's unbounded disk logging. Host event status and client timing are both visible
under Timing details and are included in the combined report. ALVR's reported
pipeline estimate is not an independently measured motion-to-photon result.

Use **Save report on the host while the session is still connected**. It collects
both sides into one JSON report. Host Stop also collects a detailed client report
before stopping its runtime. When the client stops but its companion remains
open, the host attempts another collection after detecting disconnect. A previous
full report remains on disk and is loaded when the host companion reopens.

Every five seconds the host requests only a small summary: client metrics,
compositor timing and room dimensions. Full reports are fetched on first
connection, Save report, host Stop and observed disconnect. They are not sent
for every video frame. Abrupt power loss, closing the client companion before
collection, a changed IP, or blocked diagnostics can leave an older cached
report; the report identifies its session and received time. The host UI exposes
collection failure instead of treating stale evidence as current.

The client diagnostic listener is TCP 9945 and read-only. It authorizes only the
private IPv4 address of the native client's established ALVR control socket,
matched to that process's descriptors. A remembered peer has a 60-second
post-disconnect grace. Browser-origin requests, redirects, control routes and
unrelated LAN peers are rejected. Bazzite's existing **Allow home-network
connection** action adds the scoped rule for the actual private subnet. This
is trusted-LAN access control, not encryption or cryptographic peer identity.
The desktop control pages stay authenticated on loopback.

Debug journals contain UTC and monotonic timestamps, session IDs and bounded
events. Each side keeps one approximately 2 MiB JSONL journal plus three
rotations. The ordinary latest log also rotates. Reports include bounded tails
of relevant SteamVR logs and ALVR host logs; raw gaze/tracking payload lines and
obvious credential strings are filtered. Logs still contain local paths, device
names and LAN addresses useful for diagnosis.

| PC | Persistent state and evidence |
|---|---|
| Windows | `%LOCALAPPDATA%\VROSTBridge\host-debug.jsonl`, `last-client-report.json`, host startup logs, and timestamped `host-chaperone-before-*.json` backups. SteamVR log directories are resolved from its OpenVR registry. |
| Bazzite | `${XDG_STATE_HOME:-~/.local/state}/VROSTBridge/client-debug.jsonl`, `client-runtime-settings.txt` when preferences need restoration, startup/latest logs; SteamVR logs are collected from the normal Steam directories. |

## Next Index test — use the pages

1. Stop the current bridge, close host SteamVR, and install **both 0.2.4 packages**.
   No manual uninstall is needed. Keep local room setup on Bazzite; if it has not
   been completed, do it once before moving around in VR. It is not needed merely
   to test LAN discovery, but a valid local play area is needed to test transfer.
2. Connect/power the Index and controllers. Open VROSTBridge Client and Host from
   their desktop application menus. Leave Lightweight client mode enabled. The
   client now starts local SteamVR. Allow the scoped network rule if requested.
3. Wait for streaming and for **applied and verified on host** under Runtime &
   room. If it stays waiting, inspect the diagnostics status and use Resend play
   area on the client. Keep the client page open throughout the test.
4. Try **Open host menu** on the host page, then on the client page, then left B +
   left thumbstick click. Confirm the displayed menu belongs to the rendering PC.
   The physical system button is not the remote-menu control in this release.
5. Slowly rotate and translate the headset, then move one controller. Note whether
   judder affects the whole view, the hands, or only new scene frames. Record any
   flash and its approximate time. The timing report separates dropped/mispresented
   compositor frames, network recovery, submission failure and input-focus changes.
6. Save the combined report on the host **before** stopping. Stop the host, save
   again if useful, then Stop the client. Confirm local menus/preferences return.
   Repeat one connection cycle to exercise negotiated restart and receipt recovery.

A stable frame age around 20 ms alone cannot establish smoothness. At 90 Hz each
refresh is about 11.1 ms; uneven frame arrival or missed refreshes can judder even
when the average is low. Reprojection can correct head rotation but does not
replace timely new controller/scene images. Hardware decoding remains the default;
CPU decoding is a diagnostic fallback. Do not simultaneously change codec,
resolution and bitrate while comparing this revision.

## Verification and remaining gates

The final checks passed: 72 Python tests (one environment-limited skip), native
C++ contracts, desktop controls, real Bazzite install/repair/0.2.3 upgrade/uninstall,
ZIP manifests, and NSIS archive/payload matching. Windows PE dependency checks
passed. Detailed source, protocol, installer and binary evidence is recorded in
`docs/evidence/*0.2.4*`. The Linux native client is rebuilt from this source. The
Windows native helper is cross-compiled using OpenVR's C function tables to avoid
mixing MSVC and MinGW C++ vtable ABIs. The Windows ALVR runtime and bundled Python
are reused only from the manifest-verified prior payload at the same ALVR pin.
The packages contain the reviewed source and existing third-party notices.

No Windows execution or physical headset run was available in the build
workspace. The new helper's live chaperone commit, Home/dashboard suppression,
controller shortcut, graphics timing and restart fallback need this Index test.
The exact physical system button, full irregular boundary replication, calibrated
Linux Beyond 2e eye tracking and broad audio/performance acceptance remain open.
These packages are an implementation to test, not a claim of completed validation.

## Source audit and attribution

- ALVR commit `f5023f8e78fa14ea57fb1038548d7820289a8524`: client/server connection,
  `/api/buttons`, shutdown API, packet definitions and
  `alvr/server_openvr/cpp/alvr_server/ChaperoneUpdater.cpp`.
  [Pinned ALVR source](https://github.com/alvr-org/ALVR/tree/f5023f8e78fa14ea57fb1038548d7820289a8524).
- Valve OpenVR commit `0924064316de3effbcd1acf1e309182a2deb1c05`: IVRSettings,
  IVRChaperoneSetup, compositor timing and generated C API tables.
  [Pinned OpenVR headers](https://github.com/ValveSoftware/openvr/tree/0924064316de3effbcd1acf1e309182a2deb1c05/headers).
- Full component authors, licences, Linux FFmpeg source, camera-helper source,
  NSIS terms and separate Windows FFmpeg notices remain in
  `docs/OPEN_SOURCE_CREDITS.md`, `licenses/` and the host dependency notices.
