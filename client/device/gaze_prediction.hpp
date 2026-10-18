#pragma once
// VROSTBridge 1.0.0.14: where the eyes will be looking, in the head's frame, when the frame being requested is shown.
//
// The sharp region of an eye-tracked stream is placed where the gaze sample says the eyes are. That sample is already
// 30 to 50 ms old when it reaches the native client (the cameras, Baballonia's model and its filter, the relay), and
// the frame it steers is shown another 30 to 40 ms later (the host's prediction step). While the head is still that
// hardly matters: the eyes rest on something, the sample is right for a while yet. While the head turns, it matters a
// lot: eyes fixed on a point in the world counter-rotate in the head (the vestibulo-ocular reflex), so a sample taken
// 70 ms ago points 70 ms of head turn away from where the eyes are now, and the region lands on the wrong part of
// the picture exactly when the picture is moving. The 1.0.0.12 session and the first external testers both noticed a
// blur during head movement.
//
// The counter-rotation is predictable from the head's own angular velocity, which every tracking sample carries: a
// gaze direction g in the head frame becomes R(-w_local x seconds) g, with w_local the head's angular velocity brought
// into the head frame. Saccades are not predicted (nothing can), only this. The turn is bounded so a glitch in the
// angular velocity cannot swing the region across the view.
#include "client/device/skeletal.hpp"
#include "core/stream.hpp"
#include <cmath>

namespace vrost {

constexpr float kGazePredictionMaxRadians = 0.35f; // about 20 degrees: one prediction step at a brisk head turn

// gaze: head-relative orientation whose -Z is the gaze direction. head: the head's orientation in the tracking space.
// angular: the head's angular velocity in the tracking space (rad/s, as OpenVR reports it). seconds: how far ahead of
// the gaze sample the frame is shown (the sample's age plus the prediction step). Returns the gaze rotated against
// the head's turn over that time; the input unchanged when nothing can be predicted.
inline VrostQuat predictGaze(VrostQuat gaze, VrostQuat head, const float angular[3], double seconds) {
  if (!(seconds > 0) || !std::isfinite(seconds) || !std::isfinite(angular[0]) || !std::isfinite(angular[1]) ||
      !std::isfinite(angular[2]))
    return gaze;
  // The head's angular velocity in its own frame: conj(head) * w * head.
  const VrostQuat w{angular[0], angular[1], angular[2], 0};
  const VrostQuat local = skeletal::multiply(skeletal::multiply(skeletal::conjugate(head), w), head);
  const float speed = std::sqrt(local.x * local.x + local.y * local.y + local.z * local.z);
  if (!(speed > 1e-4f))
    return gaze;
  float angle = float(speed * seconds);
  if (!(angle < kGazePredictionMaxRadians))
    angle = kGazePredictionMaxRadians;
  // Rotate the gaze the other way: the eyes stay on the world while the head turns.
  const float half = -0.5f * angle, s = std::sin(half) / speed, c = std::cos(half);
  const VrostQuat delta{local.x * s, local.y * s, local.z * s, c};
  VrostQuat result = skeletal::multiply(delta, gaze);
  const float n = std::sqrt(result.x * result.x + result.y * result.y + result.z * result.z + result.w * result.w);
  if (!(n > 1e-6f) || !std::isfinite(n))
    return gaze;
  return {result.x / n, result.y / n, result.z / n, result.w / n};
}

} // namespace vrost
