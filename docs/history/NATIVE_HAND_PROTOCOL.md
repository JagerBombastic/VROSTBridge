# Native Index hand input in 0.2.14

The matching host and client transport preserve the Index controller's source
finger curls and native OpenVR skeletons. Both packages must be upgraded together;
this changes the transport protocol and older native drivers are incompatible.

The client reads the physical controller's `FromDevice` summary at the bone
sampling cadence, up to 120 Hz. The explicit channel order is thumb, index,
middle, ring, little. The host publishes index, middle, ring and little directly
to SteamVR's legacy finger inputs. A native skeleton update cannot overwrite
those inputs with the older quaternion-derived estimate.

Both `WithController` and `WithoutController` ranges preserve all 31 parent-space
OpenVR bones, including the five auxiliary bones. This path does not convert the
bones to OpenXR and back. Controller world poses still use the existing tracked
pose path. No hand swapping, smoothing or sensitivity remapping is added.

The client checks the skeletal action's tracked device against the physical
left/right controller, its hierarchy and tracking capability. Bone positions,
rotations and curls must be finite and within the supported bounds. Source
samples expire after 50 ms. A disconnect, loss of input, changed controller
identity or invalid sample clears the cached native data. If both native ranges
cannot be acquired, the existing validated legacy skeleton path remains usable.
If only source curls are unavailable, native bones remain valid and the host uses
bounded bone-derived curl estimates until measured curls return.

A complete native hand sample is sent when the source sample changes. The
intervening 250 Hz pose packets carry a small reference to that exact sample.
The host checks the sample timestamp, packet timestamp and local receipt age;
future, stale, reordered and missing samples do not resurrect old fingers.
A lost full sample can briefly use the normal controller animation until the
next source sample arrives. Full two-hand samples are roughly 3.6 KB before
transport framing and can span several UDP shards; the intervening packets are
smaller. This reduces repeated bone data but does not claim zero effect on pose
packet delivery. A separate hand-data stream remains a future performance test.

Client INPUT records contain source curls, sample timestamp and the selected
finger transport. The host emits `VROST_NATIVE_HAND` structured log records with the
same source timestamp, accepted source curls, applied four-channel scalars,
sample age, both-range status and whether every SteamVR update succeeded (`applied`). A source
read alone is not evidence of host acceptance. Live comparisons should only use
fresh host records that report `applied=true`.

Tests cover both hands, range/auxiliary preservation, distinct named channels,
wire serialization, source and receive expiry, future/reordered/lost data,
disconnects, invalid values and measured-scalar precedence in the actual C++
driver helper. Physical fit and isolated-finger testing remain necessary to judge
the controller's own sensing independently of the bridge.
