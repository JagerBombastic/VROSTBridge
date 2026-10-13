# Native hand contract validation — 0.2.14

The validation below exercises source code; it does not substitute for physical
controller input or Windows SteamVR execution.

- `tests/skeletal_tests.cpp`: compiled with g++17 and passed. Covers original
  independent-basis/handedness round trips plus native range, auxiliary, named
  curl, invalid-input, changed-device and 50 ms expiry checks.
- `tests/native_hand_host_tests.cpp`: compiled against the actual patched
  ALVR `VROSTBridgeHand.h` and passed. Distinct physical scalar values win over
  deliberately conflicting quaternion estimates. Checks named scalar order,
  invalid-source fallback, bounds and source timestamp expiry.
- `tests/native-hand-rust`: four tests passed using Rust 1.97.1. This harness
  includes the actual patched host `tracking/native_hands.rs`; it is not a
  duplicate cache implementation. Covers future/stale/invalid input, reordered
  packets, missing full samples, disconnects, separate left/right samples and
  serialization of measured curls, both native motion ranges and auxiliaries.
  It also verifies that unchanged hand packets do not repeat full bone payloads.
- The native client's `openvr_device.cpp` compiled with g++17, `-Wall -Wextra`.
- The Rust release transport compiled successfully with the new native ABI.

Reproduce after fetching and applying the pinned upstream patches:

```sh
cargo test --manifest-path tests/native-hand-rust/Cargo.toml --locked
g++ -std=c++17 -O2 -I. tests/skeletal_tests.cpp -o skeletal-tests
./skeletal-tests
g++ -std=c++17 -O2 -Iupstream/ALVR/alvr/server_openvr/cpp/alvr_server \
    tests/native_hand_host_tests.cpp -o native-hand-host-tests
./native-hand-host-tests
```

Independent code review checked device identity, native parent-space range
preservation, host scalar precedence and timestamp/loss handling. The remaining
performance tradeoff is explicit: full two-hand sample packets span several UDP
shards, while intervening pose packets carry a small sample reference. Physical
finger isolation and live source-versus-host accepted values remain runtime
validation work.
