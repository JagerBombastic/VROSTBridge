> Historical 0.1 audit. Native Linux integration is now implemented; see IMPLEMENTATION_STATUS.md and BUILD_VERIFICATION.md for current evidence.

# Pinned upstream audit

Checked 21 September 2026 from source, not from an older architecture wiki.

## OpenVR

Commit `0924064316de3effbcd1acf1e309182a2deb1c05` from
[ValveSoftware/openvr](https://github.com/ValveSoftware/openvr/tree/0924064316de3effbcd1acf1e309182a2deb1c05).
The bundled header, Windows x64 loader DLL and licence come from that revision.
Runtime interfaces are obtained dynamically through the loader, with interface
version checks. The scene uses `VRTextureWithPose_t` / `Submit_TextureWithPose`
with the pose actually used to render the selected texture. Raw per-eye projection
and eye-to-head transforms are read from the connected runtime.

## ALVR

Commit `f5023f8e78fa14ea57fb1038548d7820289a8524` from
[alvr-org/ALVR](https://github.com/alvr-org/ALVR/tree/f5023f8e78fa14ea57fb1038548d7820289a8524).
The source declares Rust toolchain 1.97.1 and package rust-version 1.92.
These values describe that checkout; they are not an assertion about older releases.

Observed in `alvr/client_core/src/c_api.rs`:

| Need | Actual source interface |
| --- | --- |
| Lifecycle | `alvr_initialize`, `alvr_resume`, `alvr_pause`, `alvr_destroy` |
| Encoded video | `alvr_set_decoder_input_callback` with borrowed frame bytes and timestamp |
| Decoder configuration | DecoderConfig event and `alvr_get_decoder_config` |
| Frame completion | `alvr_report_frame_decoded` |
| Frame metadata | `alvr_report_compositor_start` yields per-eye view parameters and optional foveation centres |
| Submission timing | `alvr_report_submit` |
| Tracking and gaze | `alvr_send_tracking`, including optional combined-eye quaternion |
| View geometry | `alvr_send_view_params`, described as head-local view poses |
| Controls / play area | `alvr_send_button`, `alvr_send_active_interaction_profile`, `alvr_send_playspace` |

Important integration decisions:

- Copy or consume callback bytes while their borrowed lifetime is valid. A
  future worker queue cannot retain the raw pointer after the callback returns.
- Preserve the frame timestamp through decoding and metadata lookup. When exact
  source-pose provenance is absent, do not blindly substitute a previous frame's
  metadata even though the generic C API comment permits that fallback.
- The exposed C gaze argument is combined orientation. Transporting separate
  eyes, openness and confidence may require another API or a narrow extension.
- The module currently also imports `alvr_graphics`; a C ABI does not establish
  a clean D3D11-only Windows link. Non-Android built-in decoder support and external
  callback behaviour require a target build and runtime audit.
- Pin host and client together. No fake ALVR wire protocol or hand-invented C
  layout has been added to this first build.

No Rust/Windows build was completed in this environment. The first build audit on
a Windows development machine should check out the exact commit and run:

```powershell
git clone https://github.com/alvr-org/ALVR.git upstream/ALVR
git -C upstream/ALVR checkout f5023f8e78fa14ea57fb1038548d7820289a8524
git -C upstream/ALVR submodule update --init --recursive
Push-Location upstream/ALVR
cargo check -p alvr_client_core --target x86_64-pc-windows-msvc
cargo build -p alvr_client_core --target x86_64-pc-windows-msvc --release
Pop-Location
```

Retain the actual compiler output and resolve dependencies rather than calling
this gate passed from source inspection alone.

## Beyond eye tracking

Commit `65c78999f49dbc72f166196d12c8c7c9e38c26eb` from
[BigscreenVR/VRCFT-Beyond](https://github.com/BigscreenVR/VRCFT-Beyond/tree/65c78999f49dbc72f166196d12c8c7c9e38c26eb).
The published module opens `VRCFTMemmapData` read-only. Its sequential struct
contains left/right/combined XYZ, confidence, a 64-bit timestamp, validity and
per-eye closed amounts. Under the observed default layout, the payload is 64 bytes
with the timestamp at offset 40 and validity at offset 48.

VROSTBridge's optional probe opens that existing map; it does not create it,
start inference, install a vendor module or write gaze values. The publisher's
timestamp units, update atomicity and coordinate convention are not established
by the struct declaration. Those remain hardware acceptance items. The map's
advancing timestamp is used only as a liveness indicator, with a local 50 ms
unchanged-data timeout. There is no raw gaze logging or host integration yet.
