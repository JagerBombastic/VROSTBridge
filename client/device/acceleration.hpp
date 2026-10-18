#pragma once
// VROSTBridge 1.0.0.14: second-order motion prediction over a first-order wire.
//
// The host renders each frame for poses it extrapolates from the client's sample by the measured motion-to-photon
// latency, about 30 to 40 ms on a wired stream: position + velocity x latency, orientation turned by angular velocity
// x latency (ALVR's DeviceMotion::predict). A hand that is speeding up or slowing down, which is most of what a hand
// does in a rhythm game, lands short or long by half the acceleration times the latency squared: at 30 m/s^2 (a brisk
// saber swing) and 35 ms, 18 mm, and the client's own check measured maxima of 170 to 280 mm per ten seconds in the
// first testers' sessions. The testers called it "missing notes I clearly swung at".
//
// The tracking packet carries pose and velocities only, and changing it would strand older hosts. So the client folds
// the acceleration into what it sends: with horizon h (the host's prediction step, which the client knows from the
// host's statistics),
//     position sent  = p - a h^2 / 2        velocity sent         = v + a h
//     orientation    = R(-alpha h^2 / 2) q  angular velocity sent = w + alpha h
// and the host's p' + v' h comes to p + v h + a h^2 / 2, the second-order prediction, exactly when its step equals h
// and within a term of a x h x (step - h) otherwise. The orientation follows the same algebra for small turns.
//
// Acceleration is the velocity's change between consecutive samples (3.3 ms apart at the client's cadence), smoothed
// over about 12 ms so tracker noise does not become a jitter in the prediction, and bounded so a glitch cannot throw a
// hand across the room. A sample after a gap starts afresh. SteamVR's own tracking already reports velocity filtered
// from its IMUs, which is what makes differentiating it workable.
#include "client/device/skeletal.hpp"
#include "core/stream.hpp"
#include <cmath>
#include <cstdint>

namespace vrost {

struct MotionAcceleration {
  static constexpr double kSmoothingS = 0.012;         // time constant of the acceleration estimate
  static constexpr double kMinStepS = 0.0005, kMaxStepS = 0.02;  // sample spacing the estimate accepts
  static constexpr float kMaxLinear = 60.f;             // m/s^2: anything above is a glitch, not a hand
  static constexpr float kMaxAngular = 400.f;           // rad/s^2
  static constexpr double kMaxHorizonS = 0.1;

  bool valid = false;
  uint64_t at = 0;
  float velocity[3]{}, angular[3]{};
  float linear[3]{}, alpha[3]{};  // the smoothed accelerations

  void reset() { valid = false; at = 0; for (int i = 0; i < 3; ++i) linear[i] = alpha[i] = 0; }

  // One tracking sample: velocities in the tracking space at monotonic time `now` (ns).
  void observe(uint64_t now, const float v[3], const float w[3]) {
    const bool finite = std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]) &&
                        std::isfinite(w[0]) && std::isfinite(w[1]) && std::isfinite(w[2]);
    if (!finite) { reset(); return; }
    const double step = valid && now > at ? double(now - at) * 1e-9 : 0;
    if (valid && step >= kMinStepS && step <= kMaxStepS) {
      const double k = step / (kSmoothingS + step);
      for (int i = 0; i < 3; ++i) {
        linear[i] = float(linear[i] + k * ((v[i] - velocity[i]) / step - linear[i]));
        alpha[i] = float(alpha[i] + k * ((w[i] - angular[i]) / step - alpha[i]));
      }
      clamp(linear, kMaxLinear);
      clamp(alpha, kMaxAngular);
    } else {
      for (int i = 0; i < 3; ++i) linear[i] = alpha[i] = 0;
    }
    for (int i = 0; i < 3; ++i) { velocity[i] = v[i]; angular[i] = w[i]; }
    at = now;
    valid = true;
  }

  // Fold the acceleration into the motion the host extrapolates by `horizonS`. Nothing changes without an estimate
  // or a horizon.
  void apply(VrostMotion &m, double horizonS) const {
    if (!valid || !(horizonS > 0) || horizonS > kMaxHorizonS) return;
    const double h = horizonS, half = 0.5 * h * h;
    for (int i = 0; i < 3; ++i) {
      m.pose.position[i] = float(m.pose.position[i] - linear[i] * half);
      m.linear[i] = float(m.linear[i] + linear[i] * h);
      m.angular[i] = float(m.angular[i] + alpha[i] * h);
    }
    const float ax = float(-alpha[0] * half), ay = float(-alpha[1] * half), az = float(-alpha[2] * half);
    const float angle = std::sqrt(ax * ax + ay * ay + az * az);
    if (angle > 1e-6f) {
      const float s = std::sin(angle / 2) / angle, c = std::cos(angle / 2);
      m.pose.orientation = skeletal::multiply(VrostQuat{ax * s, ay * s, az * s, c}, m.pose.orientation);
    }
  }

  // The largest acceleration estimated now (m/s^2), for the diagnostics line.
  float magnitude() const { return std::sqrt(linear[0] * linear[0] + linear[1] * linear[1] + linear[2] * linear[2]); }

private:
  static void clamp(float (&value)[3], float limit) {
    const float n = std::sqrt(value[0] * value[0] + value[1] * value[1] + value[2] * value[2]);
    if (n > limit) for (float &axis : value) axis = axis * (limit / n);
  }
};

} // namespace vrost
