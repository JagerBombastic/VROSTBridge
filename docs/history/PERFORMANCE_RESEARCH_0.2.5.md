# Performance review for VROSTBridge 0.2.5

Reviewed 24 September 2026. The user has confirmed working video, tracking and
audio. Image judder remains a hardware-test issue: the changes below remove a
specific presentation defect, but are not a measured latency or smoothness win
until tested on the physical Index.

## A concrete presentation defect

In 0.2.4, the decoder published one `latest_` image immediately after submitting
its asynchronous Vulkan conversion. On the compositor tick, the client checked
only that image's completion fence. If it was unfinished, the client repeated
the previous displayed image, even when an intermediate decoded image had
finished converting. That intermediate image had already been overwritten in
the single pointer slot. This is a plausible contributor to the reported
judder, not proof that it explains every stutter.

0.2.5 keeps at most four pending decoded surfaces and selects the newest completed
surface newer than the displayed frame. A newer unfinished conversion does not
hide an older completed conversion. There is no intentional extra frame delay:
the compositor still submits as soon as a completed image is available at its
normal SteamVR cadence. Superseded decoded images can be released; compressed
interframes remain ordered because later frames may depend on them.

The selected image retains its original source pose for SteamVR reprojection.
GPU fences and the existing retirement protection prevent surfaces from being
reused while work still references them. Resolution, colour conversion, codec,
encoder quality and foveation are unchanged by this fix.

## Measure cadence, not only the average age

The pinned ALVR client core forwards the supplied tracking `poll_timestamp`.
Our client samples unpredicted OpenVR poses with a local monotonic timestamp;
the bridge uses metadata associated with the returned frame to recover its
source pose. This review does not change prediction or add another pose
extrapolation stage.

The displayed source-frame age measures time since the associated tracking
sample. It is not a camera-measured motion-to-photon result, and it does not
include an independently measured panel scanout delay. A stable average can
hide missed presentation deadlines.

The client now records bounded one-second summaries:

| Field family | Meaning |
| --- | --- |
| `unique_fps`, `submitted_fps` | New remote images versus successful compositor submissions; repeats do not count as new images. |
| `packet_interval` | Spacing between complete encoded packets delivered to the decoder callback. |
| `decode_queue` | Time waiting before the decode worker begins processing a packet. |
| `decode_output` | Packet processing start to matching FFmpeg output; hardware work may still be asynchronous. |
| `convert_enqueue` | CPU time spent preparing/importing and queuing conversion. |
| `ready_observed` | Conversion queued to first observed fence completion. Includes compositor polling delay; **not pure GPU time**. |
| `ready_to_submit` | Observed readiness to successful first submission of that image. |
| `arrival_to_submit` | Complete local packet arrival to successful first submission. Excludes earlier host/network work and panel scanout. |
| `new_frame_interval`, `compositor_interval` | New remote-image cadence and the local compositor loop cadence. |
| `decoded_superseded`, `packet_rejected` | Decoded images skipped to catch up versus bounded encoded-queue rejection. These are different events. |

Timing windows include mean, p95, maximum and sample count, and are capped at 512
samples. Existing host timings, SteamVR timing counters and tracking-gap metrics
remain necessary to locate the bottleneck. These summaries do not stream raw
tracking, controller or gaze histories over the network.

## What we can borrow from Apollo, Sunshine and Moonlight

Apollo's guidance prioritises matching the stream/display cadence and selecting
appropriate pacing. That principle applies here, but an ordinary desktop
capture stream does not replace ALVR's VR pose-to-frame association or SteamVR's
headset reprojection. Desktop-specific VSync, VRR and HDR recipes should not be
copied directly into this VR path. [Apollo best practices](https://github.com/ClassicOldSong/Apollo/wiki/Best-Practices)

Moonlight requests low-delay FFmpeg decoding and uses one decoding thread for
hardware decoders; VROSTBridge already did both. Moonlight also bounds decoded
presentation queues and distinguishes queue time from rendering time. Those
principles informed this review and the independently implemented newest-ready
selection and timings. No Moonlight or Apollo source code was copied into the
client. [Moonlight decoder](https://github.com/moonlight-stream/moonlight-qt/blob/master/app/streaming/video/ffmpeg.cpp)
· [Moonlight pacer](https://github.com/moonlight-stream/moonlight-qt/blob/master/app/streaming/video/ffmpeg-renderers/pacer/pacer.cpp)

Sunshine documents the tradeoff between encoder work, bitrate and burst size.
Its NVENC guidance uses a fast default preset and a single-frame VBV budget;
relaxing burst limits can increase packet loss when buffers lack headroom. We
should measure encoder time and transmission bursts before adding slower
compression presets, deeper buffers or a new transport. [Sunshine configuration](https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md)

ALVR itself documents the latency cost of additional frame buffering and
provides network/encoder/decoder feedback for adaptive bitrate. Increasing
buffering can smooth arrivals but adds delay; it should not conceal a client
presentation defect. [ALVR settings tutorial](https://github.com/alvr-org/ALVR/blob/master/wiki/Settings-tutorial.md)
· [ALVR bitrate implementation](https://github.com/alvr-org/ALVR/blob/master/alvr/server_core/src/bitrate.rs)

## Is more than 40 Mbit/s better?

40 Mbit/s was a conservative prototype setting, not a measured optimum or a
limit of this hardware. More bitrate can preserve detail in busy scenes and
reduce compression artefacts at the same resolution and codec. It does not
make a frame travel faster: larger frames take longer to transmit and may
increase encoder/decoder work or congestion.

At 90 Hz, the *average* video payload is about 56 kB per frame at 40 Mbit/s and
111 kB at 80 Mbit/s, before overhead and uneven frame sizes. A Wi-Fi connection's
advertised link speed is not a guarantee of that payload arriving on time.
The useful operating point is enough bitrate for the requested image quality
with headroom for the largest frames and short wireless interruptions.

Compare profiles using the same scene, resolution and refresh rate. Look at
compression quality alongside host encode time, packet-arrival p95, queue time,
new-image rate, repeated images and rejected packets. Raise bitrate when it
improves detail without worsening these timings. Do not silently lower
resolution or increase foveation to make a latency chart look better.

## Bazzite Game Mode

Bazzite identifies Gaming Mode as a gamescope session. The desktop path already
works for this user, but adding a non-Steam shortcut alone does not establish
that gamescope can lease the Index display to the SteamVR compositor.
[Bazzite Gaming Mode documentation](https://docs.bazzite.gg/Handheld_and_HTPC_edition/Steam_Gaming_Mode/)

Current upstream gamescope's inspected Wayland server and DRM backend did not
provide a DRM-lease server in those paths. Its OpenVR backend presents a VR
overlay; it is not a replacement for the headset display lease. Bazzite may
carry patches, so the installed compositor must be checked for
`wp_drm_lease_device_v1` and SteamVR must actually acquire the headset display
before we advertise Game Mode compatibility.
[gamescope Wayland server](https://github.com/ValveSoftware/gamescope/blob/master/src/wlserver.cpp)
· [DRM backend](https://github.com/ValveSoftware/gamescope/blob/master/src/Backends/DRMBackend.cpp)
· [backend selection](https://github.com/ValveSoftware/gamescope/blob/master/src/main.cpp)

The new VROSTBridge Play shortcut starts without opening a browser; ending the shortcut stops the session. Treat true Gaming Mode headset support as a separate
capability test, with a clear Desktop Mode fallback. Do not change kernel,
graphics or compositor settings on the assumption that a shortcut fixes leasing.

## Verification and remaining evidence

Unit tests cover a newer unfinished conversion hiding an older ready frame,
newest-ready catch-up, non-regressing source timestamps, bounded surface queues,
packet ownership/order and timing percentiles. The native client was rebuilt against the shipped transport and FFmpeg libraries; see docs/evidence/native-build-0.2.5.txt.

The next physical test should compare a fixed scene with both controllers,
then a moving game scene. Save the combined report while connected. Audio was
confirmed working by the user; this change does not replace the audio path.

Pinned timing source:
[ALVR client core at f5023f8](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/client_core/src/lib.rs).
