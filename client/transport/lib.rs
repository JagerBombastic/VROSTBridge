use alvr_client_core::{ClientCapabilities, ClientCoreContext, ClientCoreEvent};
use alvr_common::{
    DeviceMotion, Fov, Pose, ViewParams,
    glam::{Quat, UVec2, Vec2, Vec3},
};
use alvr_packets::{ButtonEntry, ButtonValue, FaceData, TrackingData, NativeOpenvrHand, NativeOpenvrHandUpdate};
use std::{
    ffi::{CStr, c_char, c_void},
    slice,
    sync::Mutex,
    time::Duration,
};
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct Q {
    x: f32,
    y: f32,
    z: f32,
    w: f32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct P {
    orientation: Q,
    position: [f32; 3],
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct V {
    pose: P,
    left: f32,
    right: f32,
    up: f32,
    down: f32,
}
#[repr(C)]
pub struct M {
    id: u64,
    pose: P,
    linear: [f32; 3],
    angular: [f32; 3],
}
#[repr(C)]
#[derive(Default)]
pub struct E {
    kind: u32,
    width: u32,
    height: u32,
    codec: u32,
    refresh: f32,
    device: u64,
    duration: f32,
    frequency: f32,
    amplitude: f32,
}
#[repr(C)]
pub struct NativeHand {
    sampled_at: u64,
    curls_valid: u32,
    curls: [f32; 5],
    ranges: [[P; 31]; 2],
}
/// Negotiated foveated encoding (same layout as ALVR's AlvrFoveatedEncodingParams).
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct Foveation {
    encoded: [u32; 2],
    view_ratio: [f32; 2],
    center_size: [f32; 2],
    center_shifts: [[f32; 2]; 2],
    edge_ratio: [f32; 2],
}
/// Cumulative video stream counters (patch 0008) and packets rebuilt from parity (patch 0021).
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct StreamCounters {
    frames: u64,
    network_losses: u64,
    idr_requests: u64,
    decoder_saturation: u64,
    skipped_until_idr: u64,
    fec_video_recovered: u64,
    fec_audio_recovered: u64,
}
pub const CAP_10_BIT: u32 = 1;
pub const CAP_AV1: u32 = 2;
pub const CAP_FOVEATED: u32 = 4;
pub struct Bridge {
    core: ClientCoreContext,
    config: Mutex<Vec<u8>>,
    message: Mutex<String>,
    native_samples: Mutex<[Option<u64>; 2]>,
    foveation: Mutex<Option<Foveation>>,
}
fn quat(q: Q) -> Quat {
    Quat::from_xyzw(q.x, q.y, q.z, q.w)
}
fn pose(p: P) -> Pose {
    Pose {
        orientation: quat(p.orientation),
        position: Vec3::from_array(p.position),
    }
}
fn view(v: V) -> ViewParams {
    ViewParams {
        pose: pose(v.pose),
        fov: Fov {
            left: v.left,
            right: v.right,
            up: v.up,
            down: v.down,
        },
    }
}
fn out(v: ViewParams) -> V {
    let q = v.pose.orientation;
    V {
        pose: P {
            orientation: Q {
                x: q.x,
                y: q.y,
                z: q.z,
                w: q.w,
            },
            position: v.pose.position.to_array(),
        },
        left: v.fov.left,
        right: v.fov.right,
        up: v.fov.up,
        down: v.fov.down,
    }
}
unsafe fn b<'a>(p: *mut Bridge) -> &'a Bridge {
    unsafe { &*p }
}
#[unsafe(no_mangle)]
pub extern "C" fn vrost_create(w: u32, h: u32, hz: f32) -> *mut Bridge {
    vrost_create2(w, h, hz, 0)
}
/// `caps`: CAP_10_BIT (HEVC/AV1 Main10 decoding), CAP_AV1, CAP_FOVEATED (the client can
/// undo ALVR's foveated encoding). The host still decides; these only allow it.
#[unsafe(no_mangle)]
pub extern "C" fn vrost_create2(w: u32, h: u32, hz: f32, caps: u32) -> *mut Bridge {
    if w == 0 || h == 0 || w > 8192 || h > 8192 || !hz.is_finite() || hz < 30. || hz > 240. {
        return std::ptr::null_mut();
    }
    alvr_client_core::init_logging();
    let core = ClientCoreContext::new(ClientCapabilities {
        platform: alvr_system_info::platform(None, None),
        default_view_resolution: UVec2::new(w, h),
        max_view_resolution: UVec2::new(8192, 8192),
        refresh_rates: vec![hz],
        foveated_encoding: caps & CAP_FOVEATED != 0,
        encoder_high_profile: true,
        encoder_10_bits: caps & CAP_10_BIT != 0,
        encoder_av1: caps & CAP_AV1 != 0,
        prefer_10bit: false,
        preferred_encoding_gamma: 1.,
        prefer_hdr: false,
    });
    Box::into_raw(Box::new(Bridge {
        core,
        config: Mutex::new(vec![]),
        message: Mutex::new(String::new()),
        native_samples: Mutex::new([None, None]),
        foveation: Mutex::new(None),
    }))
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_destroy(p: *mut Bridge) {
    if !p.is_null() {
        drop(unsafe { Box::from_raw(p) })
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_resume(p: *mut Bridge) {
    unsafe { b(p) }.core.resume()
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_pause(p: *mut Bridge) {
    unsafe { b(p) }.core.pause()
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_poll(p: *mut Bridge, e: *mut E) -> i32 {
    let b = unsafe { b(p) };
    let Some(ev) = b.core.poll_event() else {
        return 0;
    };
    let mut out = E::default();
    match ev {
        ClientCoreEvent::UpdateHudMessage(s) => {
            *b.message.lock().unwrap() = s;
            out.kind = 1
        }
        ClientCoreEvent::StreamingStarted(s) => {
            *b.native_samples.lock().unwrap() = [None, None];
            out.kind = 2;
            out.width = s.negotiated_config.view_resolution.x;
            out.height = s.negotiated_config.view_resolution.y;
            out.refresh = s.negotiated_config.refresh_rate_hint;
            *b.foveation.lock().unwrap() = s.negotiated_config.foveated_encoding.map(|f| Foveation {
                encoded: f.encoded_view_resolution,
                view_ratio: f.view_ratio,
                center_size: f.center_size,
                center_shifts: f.center_shifts,
                edge_ratio: f.edge_ratio,
            });
            if s.negotiated_config.enable_hdr || (s.negotiated_config.encoding_gamma - 1.).abs() > 0.01 {
                out.kind = 6;
                *b.message.lock().unwrap() =
                    "Disable HDR and non-unit encoding gamma on the host".into();
            }
        }
        ClientCoreEvent::StreamingStopped => {
            *b.native_samples.lock().unwrap() = [None, None];
            *b.foveation.lock().unwrap() = None;
            out.kind = 3;
            b.config.lock().unwrap().clear()
        }
        ClientCoreEvent::Haptics {
            device_id,
            duration,
            frequency,
            amplitude,
        } => {
            out.kind = 4;
            out.device = device_id;
            out.duration = duration.as_secs_f32();
            out.frequency = frequency;
            out.amplitude = amplitude
        }
        ClientCoreEvent::DecoderConfig { codec, config_nal } => {
            out.kind = 5;
            out.codec = match codec {
                alvr_session::CodecType::H264 => 0,
                alvr_session::CodecType::Hevc => 1,
                alvr_session::CodecType::AV1 => 2,
            };
            *b.config.lock().unwrap() = config_nal
        }
        ClientCoreEvent::RealTimeConfig(_) => {}
    }
    unsafe { *e = out };
    1
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_message(p: *mut Bridge, d: *mut c_char, n: u64) -> u64 {
    let b = unsafe { b(p) };
    let s = b.message.lock().unwrap();
    let bytes = s.as_bytes();
    if !d.is_null() && n > 0 {
        let len = bytes.len().min(n as usize - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), d.cast(), len);
            *d.add(len) = 0
        }
    }
    bytes.len() as u64 + 1
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_config(p: *mut Bridge, d: *mut u8, n: u64) -> u64 {
    let b = unsafe { b(p) };
    let s = b.config.lock().unwrap();
    if !d.is_null() && n >= s.len() as u64 {
        unsafe { std::ptr::copy_nonoverlapping(s.as_ptr(), d, s.len()) }
    }
    s.len() as u64
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_decoder(
    p: *mut Bridge,
    ctx: *mut c_void,
    cb: extern "C" fn(*mut c_void, u64, *const u8, u64) -> i32,
) {
    let addr = ctx as usize;
    unsafe { b(p) }
        .core
        .set_decoder_input_callback(Box::new(move |t, v| {
            cb(
                addr as *mut c_void,
                t.as_nanos() as u64,
                v.as_ptr(),
                v.len() as u64,
            ) != 0
        }));
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_decoded(p: *mut Bridge, t: u64) {
    unsafe { b(p) }
        .core
        .report_frame_decoded(Duration::from_nanos(t))
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_frame_views(p: *mut Bridge, t: u64, v: *mut V) -> i32 {
    if let Some(m) = unsafe { b(p) }
        .core
        .report_compositor_start(Duration::from_nanos(t))
    {
        unsafe {
            *v = out(m.view_params[0]);
            *v.add(1) = out(m.view_params[1]);
        }
        1
    } else {
        0
    }
}
/// Views plus this frame's foveation centres. Returns bit 0 when the views are valid and
/// bit 1 when `shifts` (left X/Y, right X/Y) was written from the frame's own metadata.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_frame_views2(p: *mut Bridge, t: u64, v: *mut V, shifts: *mut f32) -> i32 {
    let Some(m) = unsafe { b(p) }
        .core
        .report_compositor_start(Duration::from_nanos(t))
    else {
        return 0;
    };
    unsafe {
        *v = out(m.view_params[0]);
        *v.add(1) = out(m.view_params[1]);
    }
    let mut result = 1;
    if let (Some(centres), false) = (m.foveation_center_shifts, shifts.is_null()) {
        let values = [centres[0][0], centres[0][1], centres[1][0], centres[1][1]];
        if values.iter().all(|value| value.is_finite()) {
            unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), shifts, 4) };
            result |= 2;
        }
    }
    result
}
/// Negotiated foveated encoding for the current stream; 0 when it is off.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_foveation(p: *mut Bridge, f: *mut Foveation) -> i32 {
    match (*unsafe { b(p) }.foveation.lock().unwrap(), f.is_null()) {
        (Some(value), false) => {
            unsafe { *f = value };
            1
        }
        _ => 0,
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_stream_counters(p: *mut Bridge, c: *mut StreamCounters) {
    if c.is_null() {
        return;
    }
    let s = unsafe { b(p) }.core.vrostbridge_stream_counters();
    unsafe {
        *c = StreamCounters {
            frames: s.frames,
            network_losses: s.network_losses,
            idr_requests: s.idr_requests,
            decoder_saturation: s.decoder_saturation,
            skipped_until_idr: s.skipped_until_idr,
            fec_video_recovered: s.fec_video_recovered,
            fec_audio_recovered: s.fec_audio_recovered,
        }
    }
}
/// Game-audio playback (patch 0022): counters since the client started, and the current state.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct AudioStats {
    packets: u64,
    losses: u64,
    underruns: u64,
    pauses: u64,
    trims: u64,
    active: u32,
    stream_rate: u32,
    device_rate: u32,
    device_channels: u32,
    device_buffer_frames: u32,
    buffer_ms: f32,
    target_ms: f32,
    correction_ppm: f32,
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_audio_stats(p: *mut Bridge, a: *mut AudioStats) {
    if a.is_null() {
        return;
    }
    let s = unsafe { b(p) }.core.vrostbridge_audio_stats();
    unsafe {
        *a = AudioStats {
            packets: s.packets,
            losses: s.losses,
            underruns: s.underruns,
            pauses: s.pauses,
            trims: s.trims,
            active: s.active,
            stream_rate: s.stream_rate,
            device_rate: s.device_rate,
            device_channels: s.device_channels,
            device_buffer_frames: s.device_buffer_frames,
            buffer_ms: s.buffer_ms,
            target_ms: s.target_ms,
            correction_ppm: s.correction_ppm,
        }
    }
}
/// Gradual recovery: with a host that refreshes the picture in stripes, an isolated loss
/// does not ask for a keyframe (see patch 0008).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_set_gradual_recovery(p: *mut Bridge, enabled: i32) {
    unsafe { b(p) }.core.set_vrostbridge_gradual_recovery(enabled != 0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_submit(p: *mut Bridge, t: u64, vs: u64) {
    unsafe { b(p) }
        .core
        .report_submit(Duration::from_nanos(t), Duration::from_nanos(vs))
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_fail(p: *mut Bridge, s: *const c_char) {
    unsafe { b(p) }
        .core
        .report_fatal_decoder_error(&unsafe { CStr::from_ptr(s) }.to_string_lossy())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_path(s: *const c_char) -> u64 {
    alvr_common::hash_string(&unsafe { CStr::from_ptr(s) }.to_string_lossy())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_views(p: *mut Bridge, v: *const V) {
    unsafe { b(p) }
        .core
        .send_view_params([view(unsafe { *v }), view(unsafe { *v.add(1) })])
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_tracking(
    p: *mut Bridge,
    t: u64,
    m: *const M,
    n: u32,
    g: *const Q,
    left: *const P,
    right: *const P,
) {
    unsafe { send_tracking(p,t,m,n,g,left,right,[NativeOpenvrHandUpdate::Unavailable,NativeOpenvrHandUpdate::Unavailable]) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_tracking_native(
    p: *mut Bridge, t: u64, m: *const M, n: u32, g: *const Q,
    left: *const P, right: *const P,
    native_left: *const NativeHand, native_right: *const NativeHand,
) {
    let b = unsafe { b(p) };
    let mut samples = b.native_samples.lock().unwrap();
    let mut legacy = [left, right];
    let updates = std::array::from_fn(|hand| {
        let ptr=[native_left,native_right][hand];
        if ptr.is_null() { samples[hand]=None; return NativeOpenvrHandUpdate::Unavailable; }
        let data=unsafe { &*ptr };
        let value=NativeOpenvrHand {
            sampled_at: Duration::from_nanos(data.sampled_at),
            ranges: data.ranges.map(|range| range.map(pose)),
            curls: (data.curls_valid != 0).then_some(data.curls),
        };
        if !value.valid_at(Duration::from_nanos(t)) {
            samples[hand]=None; return NativeOpenvrHandUpdate::Unavailable;
        }
        legacy[hand]=std::ptr::null();
        if samples[hand]==Some(data.sampled_at) {
            NativeOpenvrHandUpdate::Unchanged(value.sampled_at)
        } else {
            samples[hand]=Some(data.sampled_at);
            NativeOpenvrHandUpdate::Sample(Box::new(value))
        }
    });
    drop(samples);
    unsafe { send_tracking(p,t,m,n,g,legacy[0],legacy[1],updates) }
}
unsafe fn send_tracking(
    p: *mut Bridge, t: u64, m: *const M, n: u32, g: *const Q,
    left: *const P, right: *const P,
    native_openvr_hands: [NativeOpenvrHandUpdate; 2],
) {
    let motions = unsafe { slice::from_raw_parts(m, n as usize) }
        .iter()
        .map(|m| {
            (
                m.id,
                DeviceMotion {
                    pose: pose(m.pose),
                    linear_velocity: Vec3::from_array(m.linear),
                    angular_velocity: Vec3::from_array(m.angular),
                },
            )
        })
        .collect();
    unsafe { b(p) }.core.send_tracking(TrackingData {
        poll_timestamp: Duration::from_nanos(t),
        native_openvr_hands,
        device_motions: motions,
        hand_skeletons: [left, right].map(|ptr| {
            if ptr.is_null() {
                None
            } else {
                Some(std::array::from_fn(|i| pose(unsafe { *ptr.add(i) })))
            }
        }),
        face: FaceData {
            eyes_combined: if g.is_null() {
                None
            } else {
                Some(quat(unsafe { *g }))
            },
            ..Default::default()
        },
        body: None,
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_button(p: *mut Bridge, id: u64, kind: u32, v: f32) {
    unsafe { b(p) }.core.send_buttons(vec![ButtonEntry {
        path_id: id,
        value: if kind == 0 {
            ButtonValue::Binary(v != 0.)
        } else {
            ButtonValue::Scalar(v)
        },
    }])
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_profile(p: *mut Bridge, id: u64, inputs: *const u64, n: u32) {
    unsafe { b(p) }.core.send_active_interaction_profile(
        id,
        alvr_common::hash_string("/interaction_profiles/valve/index_controller"),
        unsafe { slice::from_raw_parts(inputs, n as usize) }
            .iter()
            .copied()
            .collect(),
    )
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_playspace(p: *mut Bridge, w: f32, h: f32) {
    unsafe { b(p) }.core.send_playspace(Some(Vec2::new(w, h)))
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_battery(p: *mut Bridge, id: u64, v: f32, plugged: i32) {
    unsafe { b(p) }.core.send_battery(id, v, plugged != 0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_proximity(p: *mut Bridge, v: i32) {
    unsafe { b(p) }.core.send_proximity_state(v != 0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_prediction_ns(p: *mut Bridge) -> u64 {
    unsafe { b(p) }
        .core
        .get_total_prediction_offset()
        .as_nanos() as u64
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn abi() {
        assert_eq!(size_of::<Q>(), 16);
        assert_eq!(size_of::<P>(), 28);
        assert_eq!(size_of::<V>(), 44);
        assert_eq!(size_of::<M>(), 64);
        assert_eq!(size_of::<E>(), 48);
        assert_eq!(size_of::<NativeHand>(), 1768);
        assert_eq!(size_of::<Foveation>(), 48);
        assert_eq!(size_of::<StreamCounters>(), 56); // 1.0.0.13: the two parity counters of 1.0.0.9
    }
}

#[repr(C)]
pub struct Button {
    id: u64,
    kind: u32,
    value: f32,
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn vrost_buttons(p: *mut Bridge, buttons: *const Button, n: u32) {
    if n == 0 || n > 128 {
        return;
    }
    let entries = unsafe { slice::from_raw_parts(buttons, n as usize) }
        .iter()
        .map(|v| ButtonEntry {
            path_id: v.id,
            value: if v.kind == 0 {
                ButtonValue::Binary(v.value != 0.)
            } else {
                ButtonValue::Scalar(v.value)
            },
        })
        .collect();
    unsafe { b(p) }.core.send_buttons(entries);
}
