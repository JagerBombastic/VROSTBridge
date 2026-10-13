// VROSTBridge 1.0.0.6: the controller prediction check (client/device/prediction_check.hpp).
#include "client/device/prediction_check.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace {
int failures = 0;
void check(bool ok, const char *what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL %s\n", what);
    ++failures;
  }
}
constexpr uint64_t kTick = 3333333ULL;     // 300 Hz tracking
constexpr uint64_t kHorizon = 50000000ULL; // 50 ms prediction step
}  // namespace

int main() {
  using vrost::PredictionCheck;
  {
    // Steady motion: the host's straight-line extrapolation lands where the controller is.
    PredictionCheck check_;
    for (int i = 0; i < 600; ++i) {
      const float t = float(i) * float(kTick) / 1e9f;
      const float position[3] = {0.2f * t, 1.0f, -0.3f};
      const float velocity[3] = {0.2f, 0.f, 0.f};
      check_.add(1000000000ULL + uint64_t(i) * kTick, position, velocity, kHorizon);
    }
    const auto s = check_.take();
    check(s.samples > 500, "steady motion is measured");
    check(s.maxMm < 0.5f, "steady motion predicts within half a millimetre");
    check(std::fabs(s.horizonMs - 50.f) < 0.01f, "the prediction step is reported");
    check(s.stillSamples == 0, "a moving controller is not still");
  }
  {
    // Two controllers tapped together: 0.2 m/s, then a sudden stop. For one prediction step
    // after the stop the extrapolation overshoots by up to speed x step = 10 mm, then settles.
    PredictionCheck check_;
    float x = 0;
    for (int i = 0; i < 600; ++i) {
      const bool moving = i < 300;
      const float speed = moving ? 0.2f : 0.f;
      if (i) x += speed * float(kTick) / 1e9f;
      const float position[3] = {x, 1.0f, -0.3f};
      const float velocity[3] = {speed, 0.f, 0.f};
      check_.add(1000000000ULL + uint64_t(i) * kTick, position, velocity, kHorizon);
    }
    const auto s = check_.take();
    check(s.maxMm > 9.f && s.maxMm < 10.5f, "a sudden stop overshoots by speed x prediction step");
    // The overshoot lasts one prediction step (15 of 600 samples): the peak shows it, the 95th
    // percentile of the window does not.
    check(s.p95Mm < 1.f && s.p95Mm <= s.maxMm, "the overshoot is brief: the 95th percentile stays low");
    check(s.stillSamples > 200, "the settled controller is still");
    check(s.stillMaxMm < 0.01f, "held still, there is no offset");
    check(s.meanMm < s.maxMm, "the mean is below the peak");
    const auto empty = check_.take();
    check(empty.samples == 0 && empty.maxMm == 0 && empty.stillSamples == 0, "take starts a new window");
  }
  {
    // No prediction step, an implausible one, a gap in the samples or non-finite values: nothing measured.
    PredictionCheck check_;
    const float position[3] = {0, 1, 0}, velocity[3] = {0, 0, 0};
    for (int i = 0; i < 100; ++i) check_.add(1000000000ULL + uint64_t(i) * kTick, position, velocity, 0);
    check(check_.take().samples == 0, "without a prediction step nothing is measured");
    for (int i = 0; i < 100; ++i) check_.add(2000000000ULL + uint64_t(i) * kTick, position, velocity, 500000000ULL);
    check(check_.take().samples == 0, "an implausible prediction step is ignored");
    check_.clear();
    check_.add(3000000000ULL, position, velocity, kHorizon);
    check_.add(3000000000ULL + 4 * kHorizon, position, velocity, kHorizon);
    check(check_.take().samples == 0, "no sample near one step earlier, nothing measured");
    const float bad[3] = {NAN, 0, 0};
    check_.add(3000000000ULL + 5 * kHorizon, bad, velocity, kHorizon);
    check(check_.take().samples == 0, "non-finite samples are ignored");
  }
  {
    // The controllers' distance while both are held still; moving or non-finite samples are left out.
    vrost::PairCheck pair;
    const float still[3] = {0.01f, 0.f, 0.f}, moving[3] = {0.2f, 0.f, 0.f};
    for (int i = 0; i < 100; ++i) {
      const float left[3] = {-0.06f, 1.f, -0.3f}, right[3] = {0.06f + 0.0001f * float(i % 3), 1.f, -0.3f};
      pair.add(left, still, right, still);
    }
    const float left[3] = {-0.2f, 1.f, -0.3f}, right[3] = {0.2f, 1.f, -0.3f};
    pair.add(left, moving, right, still);
    const float bad[3] = {NAN, 0.f, 0.f};
    pair.add(bad, still, right, still);
    const auto summary = pair.take();
    check(summary.stillSamples == 100, "only still, finite pairs count");
    check(std::fabs(summary.meanMm - 120.1f) < 0.1f, "the mean distance between the controllers");
    check(std::fabs(summary.minMm - 120.f) < 0.01f && std::fabs(summary.maxMm - 120.2f) < 0.01f, "its range");
    const auto empty = pair.take();
    check(empty.stillSamples == 0 && empty.meanMm == 0, "take starts a new window");
  }
  if (failures) return EXIT_FAILURE;
  std::puts("prediction check contracts passed");
  return EXIT_SUCCESS;
}
