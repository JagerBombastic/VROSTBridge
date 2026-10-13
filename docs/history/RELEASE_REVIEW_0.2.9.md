# VROSTBridge 0.2.9 — startup, live IPD and tray menu

The supplied 27 September host and client reports identify a client-side startup
failure. The host reached driver-ready state on all four attempts. Two attempts
then ended with `FATAL IPD changed` on the client, followed by the client's normal
request to stop the host. The initial attempt was cancelled while the headset
was not yet detected. The last attempt reached the transport handshake and then
the native client exited with code 0, without a recorded reason.

## Changes

- IPD and lens-geometry events refresh the local eye transforms and projections
  and send them through ALVR's existing live view-parameter channel. They do not
  terminate or restart the stream. Duplicate startup events are harmless.
- The decoder retains a bounded, synchronized history of valid eye geometry and
  matches both rendered eyes when recovering the source head pose. This handles
  old frames still in flight during an adjustment, including small IPD changes.
  Invalid transient updates retain the last valid geometry and are retried.
- The native client no longer treats `VREvent_ProcessQuit` (another process's
  lifecycle notification) as a request to terminate itself. It still acknowledges
  and follows `VREvent_Quit`. The previous behavior is consistent with the final
  silent exit, but the old report did not record the event, so its exact cause
  cannot be proved retrospectively. Process notifications, real quit requests
  and termination signals now have distinct diagnostic messages.
- Unexpected native exits remain visible in the UI and report before cleanup.
- SteamVR processes launched through the native client's OpenVR calls no longer
  inherit PyInstaller's private library directory. The native executable uses
  its own relative ELF library search paths. This removes the observed private
  library contamination; a separate missing SteamVR Qt dependency may still
  need investigation if it remains in the next report.
- The tray menu temporarily uses a visible, zero-area tool window in the topmost
  window band as its foreground owner. It is hidden and its topmost state removed
  when the menu closes. Settings and Exit are retained. Idle status no longer
  says a microphone-disabled stream is continuing after the session has stopped.

The native Linux client is rebuilt as 0.2.9. ALVR transport, Linux decoder shared
libraries, shader, display-lease helper and upstream Windows ALVR runtime are
unchanged. No codec, bitrate, resolution or frame-pacing defaults change.
Open-source attribution and source/build references remain included.

## Install and test

1. Stop any bridge session and exit the old companion on both computers. Install
   both 0.2.9 packages over the existing versions. The normal installers retain
   settings and pairing and remove previous managed payloads.
2. Confirm both web UIs show 0.2.9. For this regression test use Bazzite desktop
   mode, leave microphone forwarding off, and retain your working video settings.
3. Leave the host waiting and start from the client. Allow startup to finish.
4. Once video is running, move the Index IPD slider in both directions. The
   connection should remain running. Then Stop, wait for host shutdown to finish,
   and Start again.
5. Right-click the host icon inside Windows' hidden-icons panel. Check that the
   complete menu, including Exit, is visible; also check Settings and dismissing
   the menu by clicking outside it.
6. If it fails, save both reports immediately. The new native version banner,
   eye-geometry revisions and explicit exit messages distinguish the next cause.

## Validation and limits

The native client compiles against the included pinned FFmpeg 6.1.4 headers and
existing matching shared libraries. Five C++ contract groups pass, covering live
IPD, old/new frame pose recovery, invalid updates, concurrent geometry access and
quit-event semantics. Python: 180 tests, 178 passing and 2 environment-dependent
skips. Desktop JavaScript checks pass. Win32 menu ordering is exercised with a
simulated API, not an actual Explorer session. Final package lifecycle and archive
verification results are recorded separately in `docs/evidence` in the source.

Windows execution, the tray menu's real Windows 11 appearance, end-to-end headset
streaming and on-headset IPD adjustment still require physical acceptance testing.

## Implementation references

- Pinned OpenVR declarations and loader: `third_party/openvr`; Valve copyright
  and license included. Event API: https://github.com/ValveSoftware/openvr
- Pinned ALVR `send_view_params` / `ClientControlPacket::LocalViewParams`:
  https://github.com/alvr-org/ALVR/tree/f5023f8e78fa14ea57fb1038548d7820289a8524
- Microsoft notification-menu owner requirements:
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-trackpopupmenu
- Microsoft window ordering and owned-window behavior:
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos
