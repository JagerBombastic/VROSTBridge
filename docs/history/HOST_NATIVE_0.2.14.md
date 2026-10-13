# Host runtime changes in 0.2.14

These changes are applied to ALVR commit `f5023f8e78fa14ea57fb1038548d7820289a8524`.
They retain ALVR's AMF encoder, existing codec and bitrate selection, and explicit
serialization of its shared D3D11 immediate context. They do not change image
quality settings or remove synchronization to claim a latency gain.

## Capture synchronization

The previous capture path called `ID3D11DeviceContext::Flush` before waiting for
the encoder worker, even though both workers use the same immediate context.
It also held SteamVR's shared-texture keyed mutex while waiting for the previous
encode to finish. Capture now waits for the encoder first, before issuing any
context command or acquiring the compositor texture. This removes that concurrent
context access and reduces how long the compositor's texture lock is held.

The encoder-complete event is now a manual-reset readiness condition: a failed
capture can wait again without consuming the only completion notification.
`NewFrameReady` resets it; successful or failed encoder work signals it. Capture
releases an acquired mutex on composition failure, and releases it only following
an exact `S_OK` acquisition, because DXGI timeout codes are positive HRESULTs.
The encoder exit flag is atomic.

## AMF admission, ownership and bounded recovery

Every source `SubmitInput` result is checked. `AMF_INPUT_FULL` drops only the
unaccepted raw image; pending encoded reference packets are not discarded.
If that image carried an IDR request, the request is rescheduled. Converted AMF
intermediate output is retained across downstream backpressure instead of being
silently dropped.

At most three accepted source surfaces are retained by VROSTBridge. Matching
output carries a distinct surface token; receiving it releases VROSTBridge's
reference. AMF's own reference-counted allocator decides when its cached surface
can be reused. The bridge never overwrites an in-flight texture based solely on
a guessed timeout or counter. Its D3D context still uses `WaitForEncode`.

The query wait ceiling is one refresh interval rounded up plus two milliseconds,
clamped to 3–20 ms; at 120 Hz it is 11 ms. This replaces the previous one-second
maximum, not an assumed one-second delay on every frame. When an output is ready,
AMF returns immediately as before. The setting is checked during initialization.
A pipeline with pending input and no output progress for 250 ms attempts a flush,
then requests an IDR. Failed flushes retain all owned references and retry at a
bounded rate; the code does not pretend that cancellation succeeded.

## Diagnostics

Once-per-second monotonic timing windows are exported as structured host events:

- `HostCapture`: encoder wait, keyed-mutex acquire, composition and lock-hold
  maxima; cumulative acquisition/release failures and captured frames.
- `HostAMF`: allocation, submission, output-query and output-age maxima;
  accepted/output counts, admission/pool limits, failures and in-flight inputs.
- `HostEncoder`: complete transmit maximum, transmitted images and failures.

These are CPU stage and synchronization measurements. Output age includes work
from the surface submission timestamp; none is a motion-to-photon measurement.
They can identify whether an observed hitch starts in capture, AMF or elsewhere.

## Normal Steam shutdown

The host now recognizes a new, explicit `steam has initiated a quit all` entry in
its registered `vrserver.txt`. Each owned launch resets a bounded log cursor;
file identity, preceding bytes and timestamps guard against stale records and
log rotation. A negotiated restart record takes precedence over a shutdown entry.
Generic lost-process warnings are not treated as evidence of a normal quit.

A current Steam request publishes `shutdown_reason: steam-quit` and stops the
owned runtime normally. Unexplained runtime loss still follows the failure path.
The broker must carry this normal reason through client cleanup without showing
it as a crash. No process outside the existing verified ownership checks is killed.

## Verification limits

The Python loopback lifecycle tests exercise a real monitor, normal external quit,
old-log crash classification and graceful owned shutdown. Log cursor tests cover
split writes, replacement/truncation, missing files and bounded reads. Host event
tests verify structured metric sampling and warning-table preservation.
`tests/host_encoder_completion_win32.cpp` exercises the actual ALVR Windows event
wrapper and the failed-capture/second-wait scenario, including an auto-reset
negative control.

A successful Windows cross-build checks ABI/compiler integration. Actual GPU
encoding, SteamVR capture and physical headset smoothness still require the
user's hardware run; no offline test establishes a measured latency improvement.
