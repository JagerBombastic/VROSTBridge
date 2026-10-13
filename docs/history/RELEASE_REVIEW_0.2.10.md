# VROSTBridge 0.2.10 — connected, but no video

## Findings from the supplied 0.2.9 reports

The host started its runtime, connected to the trusted client and applied the
3.0 × 2.25 metre rectangular playspace. Client tracking continued at about
247–249 samples per second, but its decoder reported zero packets and zero
frames. The host's retained debug ring contained 1,496 copies of
`Dropping video packet. Reason: Waiting for IDR frame`. This identifies a video
startup/recovery problem after connection, not another headset DisplayPort
initialization failure. A decoder packet count is not a network-interface counter.

Code review confirmed a circular dependency in VROSTBridge's use of the pinned
ALVR protocol:

1. The host sends decoder configuration when it receives `RequestIdr`, provided
   its encoder has produced that configuration.
2. ALVR's `set_decoder_input_callback` installs the callback and sends `RequestIdr`.
3. VROSTBridge 0.2.9 called that API only after receiving decoder configuration.
4. The host may discard video while waiting for an IDR, so client recovery driven
   by incoming video packets cannot reliably break the deadlock.

Earlier successful sessions do not make this ordering safe. Whether an initial
IDR reaches the client can affect whether packet-driven recovery starts. The
reports lost the earliest encoder events to repeated warnings, so the exact
first-frame timing in this physical run cannot be reconstructed.

Primary implementation references at ALVR commit
`f5023f8e78fa14ea57fb1038548d7820289a8524`:

- [Server control handling](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/connection.rs): `ClientControlPacket::RequestIdr`.
- [Client callback registration](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/client_core/src/lib.rs): `set_decoder_input_callback`.
- [Server video gate](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/lib.rs): `send_video_nal`.
- [Client packet recovery](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/client_core/src/connection.rs): video receive loop.

## Changes

- Register the lifetime-stable video router at `StreamingStarted`, before waiting
  for codec configuration. This sends the initial configuration/keyframe request.
- Reject video safely until the decoder input queue exists. Once it exists,
  request another keyframe so an early IDR cannot strand the decoder.
- Retry no more than once per second during video silence. This handles a request
  sent before the encoder's codec configuration exists, as well as later silence.
  Healthy streaming sends no periodic keyframe requests. The configuration-ready
  transition can make one immediate extra request.
- Reset recovery state on every stop/reconnect; no requests in idle state.
- Keep the existing bitrate, resolution, codec and corruption protection.
- Report `video_phase`, `video_packets`, `keyframe_requests` and
  `video_silence_ms`. Log decoder configuration, decoder open and first decoded
  frame separately so the next failure, if any, can be localized.
- Show **Starting video** / **Video not arriving** on the desktop UI. Zero
  decoded frames show an unavailable frame age, not a misleading zero latency.
- Coalesce duplicate host warnings, preserve their counts and first/last times,
  and retain bounded startup milestones apart from frame telemetry on both ends.

The Windows tray fix and live IPD geometry handling remain intact. The neutral
client stream-only view, client-owned startup/shutdown, room transfer, audio,
Game Mode and desktop UI are retained. This change does not fall back to local
SteamVR Home. ALVR, FFmpeg, OpenVR and other third-party credits remain included.

## Install and test

1. Stop the session from the client and exit both VROSTBridge companions. Run
   `VROSTBridge-Host-Setup-0.2.10.exe` on Windows. On Bazzite, extract the complete
   `VROSTBridge-Bazzite-Installer-0.2.10.zip` and open
   `Install VROSTBridge.desktop`. Use the installers over the existing installs;
   they replace owned old program files and retain settings/connection identity.
2. Confirm both UIs show **0.2.10**. Leave the host idle in its tray. Start from
   the client only, initially in Bazzite desktop mode, using the current stream
   settings.
3. Watch for decoded frames increasing and an actual host image. Normal progress
   is: stream negotiated → configuration/keyframe requested → decoder configured
   → decoder opened → first frame decoded. Startup can briefly reject an early
   frame before configuration; it should recover automatically.
4. If the image stays blank for about 15 seconds, save the **host report while
   still connected**, then save the client report too. Do not change bitrate or
   reinstall SteamVR just to troubleshoot this handshake. Both reports now keep
   the missing startup evidence.
5. If video works, stop and restart from the client, then test IPD adjustment
   during streaming. Verify that a healthy stream's keyframe-request counter
   stops increasing unless video reception stalls.

## Validation and limits

The native Linux executable and frozen client companion are rebuilt, as are the
Windows launcher/helper executables and installer. Pinned ALVR host/transport,
FFmpeg libraries and the Game Mode lease helper are reused without modification.
Validation evidence is under `docs/evidence`; the release validation record
identifies the executed tests and package integrity checks.

Automated coverage includes the server's request-before-config behavior, late
encoder configuration, rejection of an early IDR, decoder-ready retry, silent
video recovery, healthy-stream request suppression, and reconnect/stop behavior.
It also covers log-spam retention and the desktop no-video state.

The build environment cannot execute the Windows host or drive the physical
Index. A real end-to-end video test is still required; this release fixes the
confirmed handshake defect and makes any remaining decoder/GPU/network issue
visible. No successful physical 0.2.10 stream is claimed.
