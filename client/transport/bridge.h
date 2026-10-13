#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* VROSTBridge-owned ABI. ALVR's Rust types never cross this boundary. */
typedef struct {
  float x, y, z, w;
} VrostQuat;
typedef struct {
  VrostQuat orientation;
  float position[3];
} VrostPose;
typedef struct {
  VrostPose pose;
  float left, right, up, down;
} VrostView;
typedef struct {
  uint64_t id;
  VrostPose pose;
  float linear[3], angular[3];
} VrostMotion;
typedef struct {
  uint32_t kind, width, height, codec;
  float refresh;
  uint64_t device;
  float duration, frequency, amplitude;
} VrostEvent;
typedef int (*VrostVideoCallback)(void *, uint64_t, const uint8_t *, uint64_t);
/* Context is owned by the caller. Destroy only after all calling workers join.
   Encoded callback data is borrowed for that call only. Return 0 on overflow.
 */
void *vrost_create(uint32_t width, uint32_t height, float refresh);
/* Capabilities for vrost_create2: Main10 decoding, AV1 decoding, foveated decoding.
   They allow the host to use a feature; the host still decides. */
#define VROST_CAP_10_BIT 1u
#define VROST_CAP_AV1 2u
#define VROST_CAP_FOVEATED 4u
void *vrost_create2(uint32_t width, uint32_t height, float refresh, uint32_t caps);
void vrost_destroy(void *);
void vrost_resume(void *);
void vrost_pause(void *);
int vrost_poll(void *, VrostEvent *); /* 1 HUD, 2 start, 3 stop, 4 haptics, 5 config,
                                   6 unsupported stream */
uint64_t vrost_message(void *, char *, uint64_t);
uint64_t vrost_config(void *, uint8_t *, uint64_t);
/* Register a stable router AND request codec configuration / an IDR. Call at
   StreamingStarted before waiting for config, then when the decoder is ready.
   Repeating with the same router is safe for rate-limited no-video recovery. */
void vrost_decoder(void *, void *, VrostVideoCallback);
void vrost_decoded(void *, uint64_t);
int vrost_frame_views(void *, uint64_t, VrostView *); /* exact timestamp only */
/* Bit 0: views valid. Bit 1: shifts[4] (left X/Y, right X/Y foveation centres) written. */
int vrost_frame_views2(void *, uint64_t, VrostView *, float *shifts);
/* Negotiated foveated encoding (ALVR AlvrFoveatedEncodingParams layout). */
typedef struct {
  uint32_t encoded[2];
  float view_ratio[2];
  float center_size[2];
  float center_shifts[2][2];
  float edge_ratio[2];
} VrostFoveation;
int vrost_foveation(void *, VrostFoveation *); /* 1 when the current stream is foveated */
/* fec_*: video frames and game-audio packets rebuilt from the host's parity shards (1.0.0.9). */
typedef struct {
  uint64_t frames, network_losses, idr_requests, decoder_saturation, skipped_until_idr;
  uint64_t fec_video_recovered, fec_audio_recovered;
} VrostStreamCounters;
void vrost_stream_counters(void *, VrostStreamCounters *);
/* Game-audio playback (1.0.0.9): packets, losses bridged, underruns (the buffer ran dry while
   packets were late), pauses (the host sent no sound), trims (a burst skipped back to the target)
   since the client started; whether playback runs, its rates, channels and device buffer, and the
   jitter buffer's level, target and speed correction (parts per million) now. */
typedef struct {
  uint64_t packets, losses, underruns, pauses, trims;
  uint32_t active, stream_rate, device_rate, device_channels, device_buffer_frames;
  float buffer_ms, target_ms, correction_ppm;
} VrostAudioStats;
void vrost_audio_stats(void *, VrostAudioStats *);
void vrost_set_gradual_recovery(void *, int);
void vrost_submit(void *, uint64_t, uint64_t);
void vrost_fail(void *, const char *);
uint64_t vrost_path(const char *);
void vrost_views(void *, const VrostView *);
void vrost_tracking(void *, uint64_t, const VrostMotion *, uint32_t, const VrostQuat *,
                 const VrostPose *, const VrostPose *);
/* Native OpenVR parent-space bones: [0] WithController, [1] WithoutController.
   Curl order is thumb, index, middle, ring, little. sampled_at uses the SAME
   client monotonic clock as tracking. Null means unavailable, never a hold. */
typedef struct {
  uint64_t sampled_at;
  uint32_t curls_valid;
  float curls[5];
  VrostPose ranges[2][31];
} VrostNativeHand;
void vrost_tracking_native(void *, uint64_t, const VrostMotion *, uint32_t,
                        const VrostQuat *, const VrostPose *, const VrostPose *,
                        const VrostNativeHand *, const VrostNativeHand *);
typedef struct {
  uint64_t id;
  uint32_t kind;
  float value;
} VrostButton;
void vrost_buttons(void *, const VrostButton *, uint32_t);
void vrost_button(void *, uint64_t, uint32_t, float);
void vrost_profile(void *, uint64_t, const uint64_t *, uint32_t);
void vrost_playspace(void *, float, float);
void vrost_battery(void *, uint64_t, float, int);
void vrost_proximity(void *, int);
uint64_t vrost_prediction_ns(void *);
#ifdef __cplusplus
}
#endif
