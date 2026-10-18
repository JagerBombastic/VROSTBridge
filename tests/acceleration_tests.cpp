// VROSTBridge 1.0.0.14: the acceleration folded into the poses the host extrapolates (client/device/acceleration.hpp).
#include "client/device/acceleration.hpp"
#include "client/device/prediction_check.hpp"
#include <cmath>
#include <cstdio>

namespace {
int failures = 0;
void check(bool ok, const char *what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL %s\n", what);
    ++failures;
  }
}
constexpr uint64_t kTick = 3333333ULL;     // 300 Hz tracking
constexpr double kHorizon = 0.035;         // 35 ms prediction step
constexpr uint64_t kHorizonNs = 35000000ULL;

// What the host does with a motion: position + velocity x step.
void hostExtrapolate(const VrostMotion &sent, double step, float out[3]) {
  for (int i = 0; i < 3; ++i) out[i] = float(sent.pose.position[i] + sent.linear[i] * step);
}
}  // namespace

int main() {
  using vrost::MotionAcceleration;
  {
    // A hand accelerating steadily at 20 m/s^2 along x from rest. After the estimate settles, the host's
    // first-order extrapolation of what the client sends lands where the hand will be, not short of it.
    MotionAcceleration estimate;
    const float a = 20.f;
    float worstFirstOrder = 0, worstSecondOrder = 0;
    for (int i = 0; i < 300; ++i) {
      const double t = double(i) * double(kTick) * 1e-9;
      const uint64_t now = 1000000000ULL + uint64_t(i) * kTick;
      VrostMotion m{};
      m.pose.orientation = {0, 0, 0, 1};
      m.pose.position[0] = float(0.5 * a * t * t);
      m.linear[0] = float(a * t);
      estimate.observe(now, m.linear, m.angular);
      VrostMotion sent = m;
      estimate.apply(sent, kHorizon);
      if (i < 60) continue;  // the estimate settles within a few time constants
      const double later = t + kHorizon;
      const float truth = float(0.5 * a * later * later);
      float plain[3], folded[3];
      hostExtrapolate(m, kHorizon, plain);
      hostExtrapolate(sent, kHorizon, folded);
      worstFirstOrder = std::max(worstFirstOrder, std::fabs(plain[0] - truth));
      worstSecondOrder = std::max(worstSecondOrder, std::fabs(folded[0] - truth));
    }
    check(worstFirstOrder > 0.011f && worstFirstOrder < 0.013f, "first order misses by a h^2 / 2 = 12 mm");
    check(worstSecondOrder < 0.001f, "with the acceleration folded in the host lands within a millimetre");
    check(std::fabs(estimate.magnitude() - a) < 0.5f, "the acceleration is estimated");
  }
  {
    // A sudden stop: the estimate reports a large deceleration for a moment (bounded), then zero.
    MotionAcceleration estimate;
    const float moving[3] = {1.f, 0, 0}, still[3] = {0, 0, 0}, none[3] = {0, 0, 0};
    uint64_t now = 1000000000ULL;
    for (int i = 0; i < 30; ++i, now += kTick) estimate.observe(now, moving, none);
    check(estimate.magnitude() < 0.01f, "steady speed: no acceleration");
    estimate.observe(now, still, none);
    check(estimate.magnitude() > 1.f && estimate.magnitude() <= MotionAcceleration::kMaxLinear + 1e-3f,
          "a stop within one sample is a bounded deceleration");
    for (int i = 0; i < 60; ++i) { now += kTick; estimate.observe(now, still, none); }
    check(estimate.magnitude() < 0.01f, "then it settles to nothing");
  }
  {
    // A gap in the samples (a tracking dropout) starts the estimate afresh instead of dividing by a long interval.
    MotionAcceleration estimate;
    const float v1[3] = {1.f, 0, 0}, v2[3] = {2.f, 0, 0}, none[3] = {0, 0, 0};
    estimate.observe(1000000000ULL, v1, none);
    estimate.observe(1000000000ULL + 500000000ULL, v2, none);  // half a second later
    check(estimate.magnitude() == 0.f, "a long gap gives no estimate");
    VrostMotion m{};
    m.pose.orientation = {0, 0, 0, 1};
    m.linear[0] = 2.f;
    estimate.apply(m, kHorizon);
    check(m.linear[0] == 2.f && m.pose.position[0] == 0.f, "nothing is folded in without an estimate");
    const float broken[3] = {NAN, 0, 0};
    estimate.observe(1000000000ULL + 503333333ULL, broken, none);
    check(!estimate.valid, "a broken sample resets the estimate");
  }
  {
    // Angular: a controller spinning up about y folds half the angular acceleration into the orientation sent and
    // the rest into the angular velocity, so the host's turn by w' x step reaches the second-order angle.
    MotionAcceleration estimate;
    const float alpha = 50.f;  // rad/s^2
    uint64_t now = 1000000000ULL;
    float w[3] = {0, 0, 0}, none[3] = {0, 0, 0};
    for (int i = 0; i < 200; ++i, now += kTick) {
      w[1] = float(alpha * double(i) * double(kTick) * 1e-9);
      estimate.observe(now, none, w);
    }
    VrostMotion m{};
    m.pose.orientation = {0, 0, 0, 1};
    m.angular[1] = w[1];
    estimate.apply(m, kHorizon);
    check(std::fabs(m.angular[1] - (w[1] + alpha * float(kHorizon))) < 0.05f, "angular velocity gains alpha x h");
    const float halfTurn = float(0.5 * alpha * kHorizon * kHorizon);  // radians, about 1.75 degrees
    check(std::fabs(2 * std::asin(std::fabs(m.pose.orientation.y)) - halfTurn) < 0.002f && m.pose.orientation.y < 0,
          "the orientation is turned back by alpha h^2 / 2");
  }
  {
    // The prediction check measures the host's result from what was sent, against the real position.
    vrost::PredictionCheck check_;
    const float a = 20.f;
    MotionAcceleration estimate;
    for (int i = 0; i < 600; ++i) {
      const double t = double(i) * double(kTick) * 1e-9;
      const uint64_t now = 1000000000ULL + uint64_t(i) * kTick;
      VrostMotion m{};
      m.pose.orientation = {0, 0, 0, 1};
      m.pose.position[0] = float(0.5 * a * t * t);
      m.linear[0] = float(a * t);
      estimate.observe(now, m.linear, m.angular);
      VrostMotion sent = m;
      estimate.apply(sent, kHorizon);
      check_.add(now, m.pose.position, m.linear, kHorizonNs, sent.pose.position, sent.linear);
    }
    const auto s = check_.take();
    check(s.samples > 500, "the check runs on the folded samples");
    check(s.p95Mm < 1.5f, "and finds the host within about a millimetre");
  }
  if (failures) {
    std::fprintf(stderr, "%d failure(s)\n", failures);
    return 1;
  }
  std::printf("acceleration contracts passed\n");
  return 0;
}
