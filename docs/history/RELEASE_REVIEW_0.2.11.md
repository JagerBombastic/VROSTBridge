# VROSTBridge 0.2.11 — frame consistency and graphics synchronization

The Index is now user-confirmed working end to end on 0.2.10. This update keeps
that video-startup fix, the tray menu, live IPD, client-controlled sessions,
stream-only local runtime, audio and Game Mode. Stream resolution, codec and
bitrate are unchanged by the performance changes.

## What the working-session report shows

The two uploaded reports contain the same stream session; they are not two
independent measurements. The host report includes the client report, so a
separate client upload was not needed for this analysis.

| Measurement | Observed baseline on 0.2.10 |
| --- | --- |
| Stream | Index, 90 Hz, H.264, 80 Mbit/s, 1440 × 1568 per eye |
| Fresh image cadence | Usually near 90 fps; occasional 33–56 ms intervals |
| Client source-frame age | Reported p95 27 ms, sampled once per second |
| ALVR pipeline estimate | Median 37.5 ms across retained graph samples |
| Host encoding estimate | Median 4.2 ms |
| Network estimate | Median 3.4 ms; sampled maximum 12.1 ms |
| Client arrival-to-submit | Median of active one-second means: 11.3 ms |
| Decoder output call | Median of active one-second means: 0.37 ms; not complete hardware decode time |
| Conversion enqueue path | Median of active one-second means: 4.0 ms, including hardware synchronization/import |
| Connection interruptions | Two timeouts/reconnects before the final requested stop |

These are retained, sampled diagnostics. They do not measure motion-to-photon
latency. Host graph samples, client windows and the session frame-age summary
cover different retained windows and cannot be added as a single exact budget.
The client timing table excludes low-FPS startup/shutdown windows; the source
selection and exact aggregates are in `docs/evidence/baseline-0.2.10.json`.
There are also host rendering outliers and uneven packet arrivals. A client fix
cannot eliminate a genuine host or network interruption.

## Changes made

### Coordinate the Vulkan queue correctly

The decoder submits conversion work asynchronously. SteamVR can also use the
same queue during Submit, WaitGetPoses and PostPresentHandoff. The old code
locked Submit and conversion but left the other runtime accesses uncoordinated.
This is a concrete concurrency defect; the report alone cannot prove it caused
all of the visible artifacts.

The client now enables explicit timing with application-managed handoff, which
allows WaitGetPoses to run without touching the queue. Timing markers, conversion
submissions, eye submissions and handoff share FFmpeg's queue lock. The queue
lock is not held during WaitGetPoses. Presentation retirement fences are inserted
AFTER handoff, including cleanup of a partially submitted frame. Timing marker
errors are counted for diagnosis; a missing initial timing marker does not
replace or terminate an otherwise valid image stream.

This follows [Valve's Vulkan integration requirements](https://github.com/ValveSoftware/openvr/wiki/Vulkan)
and the bundled pinned OpenVR header. It is not a graphics-driver workaround or a
claim that the dock is faulty.

### Catch a nearly ready frame

If no new completed image is available immediately, the client can wait up to
1.5 ms for a new surface or conversion fence. The budget shrinks at higher
refresh rates and when the runtime reports little time remaining; a 2 ms
submission margin is reserved. A ready image is selected immediately. Waiting
uses a condition variable or GPU fence, not a busy loop. The newest ready image
wins; an unfinished newer image cannot hide an older completed image.

This can avoid repeating an entire 11.1 ms refresh when a frame finishes just
after the normal selection point. It deliberately does not add a fixed jitter
buffer to every frame. It will not cure a prolonged network gap. Operating-system
scheduling can overrun a requested timeout, so the requested budget and observed
wait time are both reported rather than claiming a hard real-time deadline.

**Advanced → Frame pacing → Catch late frames** is enabled by default and is
saved when starting a session. Turn it off for an A/B comparison. The queue
correctness fix remains active in both cases. Game Mode uses the saved client
setting, with the same default enabled.

### Remove the native Index mapping detour

The pinned ALVR automatic mapper omits trackpad inputs, squeeze force and native
left A/B inputs in this source-to-target combination. The report records many
unmapped-input messages. For the default Index controller profile, the host now
uses 36 explicit passthrough paths covering both hands. Input values are
preserved; an already explicit custom mapping is retained. This avoids the
unnecessary warning/log work without suppressing valid controller inputs.

The implementation was checked against ALVR's pinned
[automatic input mapper](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/input_mapping.rs)
and its
[settings converter](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/session/src/lib.rs).
The session schema remains unchanged.

### Measure the actual wait locations

The UI now shows fresh-video FPS, repeated-frame percentage and frame-interval
p95. Detailed timing adds late-frame catches/timeouts, actual late-frame wait,
VA-API synchronization/import time, other conversion CPU time, runtime pose-wait
path, submission time, handoff time and timing-marker errors.

FFmpeg's VA-API-to-Vulkan map performs `vaSyncSurface` before importing the
surface. Therefore a quick `avcodec_receive_frame` return does not mean hardware
decoding finished in that time. The new `map_sync_import` measurement separates
that wait/import from the remaining conversion CPU work. It is not pure GPU
conversion duration. ALVR's historical decoder estimate remains comparable, but
must be interpreted with this caveat. Reference: bundled FFmpeg 6.1.4
`libavutil/hwcontext_vulkan.c`, `vulkan_map_from_vaapi`.

## Install and compare

1. Stop the session from the client and exit both companions. Install
   `VROSTBridge-Host-Setup-0.2.11.exe` on Windows. On Bazzite extract the whole
   `VROSTBridge-Bazzite-Installer-0.2.11.zip` and open
   `Install VROSTBridge.desktop`. Install over 0.2.10; settings and identity are
   retained, while old managed program files are replaced.
2. Confirm both UIs show 0.2.11. For a clean comparison, use the same game/scene,
   current H.264/80 Mbit/s settings and 90 Hz. Start from the client and leave
   **Catch late frames** enabled. After a short warm-up, run for two minutes and
   save the host report while still connected.
3. Stop, turn **Catch late frames** off under Advanced, and repeat the same scene
   and movements. Save another host report before stopping. This isolates the
   selection policy without reintroducing the graphics synchronization defect.
4. Compare repeated-frame percentage, fresh FPS, new-frame interval p95/max,
   source-frame age, arrival-to-submit time and the late-frame counters. Watch for
   the tiny artifacts as well as perceived smoothness. Use ordinary headset and
   controller movement, then check both hands' A/B, stick, pad and squeeze inputs.
5. If artifacts persist, note whether they look like coloured speckles/lines or
   block-shaped image corruption. The current logs do not distinguish a physical
   display-link fault from all possible codec/graphics problems. A photo or short
   recording of the artifact can help select the next test.

Do not raise bitrate or change several settings during the A/B run. The point is
to measure consistency with the working image quality. A later wired-network
comparison can isolate Wi-Fi contributions if arrival gaps/timeouts remain.

## Validation and limits

Automated tests cover shared queue serialization under concurrent conversion and
presentation, marker ordering, retirement after handoff, exception-safe unlock,
bounded waiting, immediate ready-frame selection and timeouts. Controller tests
cover every native path and the pinned nested session mapping structure. The GUI
comparison setting is checked through to native launch and persistence.

Release build evidence, installer upgrade checks and package integrity results
are included in the source archive under `docs/evidence`. The native client and
companions are rebuilt. Pinned ALVR host/transport, FFmpeg libraries, OpenVR,
shader, Game Mode lease helper, branding and credits are retained.

No Windows execution or physical 0.2.11 headset test was possible in the build
environment. No measured latency reduction or elimination of artifacts is claimed
before the A/B test. The 0.2.10 baseline is documented separately from these
implementation changes.
