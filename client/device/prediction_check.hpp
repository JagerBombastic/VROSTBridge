#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <vector>

namespace vrost {
// VROSTBridge 1.0.0.6: how far the stream's motion prediction puts a controller from where it
// really was.
//
// The host renders each frame for poses it extrapolates by the measured motion-to-photon
// latency (prediction.hpp): position + velocity x latency, about 40-45 ms on a wired stream at
// 75 Hz (ALVR's estimate in the 1.0.0.5 test). A controller that stops suddenly (two controllers
// tapped together, a hand hitting a wall) therefore overshoots for a moment by its speed times that
// latency, then settles. A headset connected to its own PC predicts only to its display, roughly
// half of that. The
// 1.0.0.5 test reported controller models clipping into each other by about 10 mm on the
// stream but not on the headset's own PC.
//
// This check repeats the host's extrapolation on the client's own samples: for every new
// sample it takes the sample one prediction step earlier, extrapolates it to now exactly as
// the host does, and measures the distance to where the controller really is now. `still`
// collects the same while the controller is held still (both samples slower than
// kStillMetresPerSecond): a steady offset there would not be prediction. Only distances are
// kept, never positions.
struct PredictionSummary {
  uint32_t samples = 0;     // extrapolations measured in this window
  float horizonMs = 0;      // the prediction step the last of them used
  float meanMm = 0, p95Mm = 0, maxMm = 0;
  uint32_t stillSamples = 0;
  float stillMaxMm = 0;     // largest distance while held still
};

class PredictionCheck {
public:
  static constexpr float kStillMetresPerSecond = 0.03f;
  static constexpr size_t kHistory = 256;  // about 0.85 s at the 300 Hz tracking cadence
  static constexpr size_t kWindow = 2048;  // distances kept per window (oldest replaced)

  // One controller sample (monotonic ns, metres, metres per second) and the stream's current
  // prediction step. A sample within half a tracking interval of now - horizon is used.
  // 1.0.0.14: `sentPosition` and `sentVelocity`, when given, are what the host receives for this sample (the
  // acceleration folded in, acceleration.hpp); the extrapolation starts from them and is still measured against
  // where the controller really is.
  void add(uint64_t now, const float position[3], const float velocity[3], uint64_t horizonNs,
           const float *sentPosition = nullptr, const float *sentVelocity = nullptr) {
    if (!finite3(position) || !finite3(velocity)) return;
    const float *from = sentPosition && finite3(sentPosition) ? sentPosition : position;
    const float *rate = sentVelocity && finite3(sentVelocity) ? sentVelocity : velocity;
    history_[next_] = {now, {from[0], from[1], from[2]}, {rate[0], rate[1], rate[2]}};
    next_ = (next_ + 1) % kHistory;
    if (count_ < kHistory) ++count_;
    if (!horizonNs || horizonNs > 200000000ULL || now <= horizonNs) return;
    const uint64_t wanted = now - horizonNs;
    const Sample *best = nullptr;
    uint64_t bestGap = UINT64_MAX;
    for (size_t i = 1; i < count_; ++i) {
      const Sample &old = history_[(next_ + kHistory - 1 - i) % kHistory];
      if (old.at > now) continue;
      const uint64_t gap = old.at > wanted ? old.at - wanted : wanted - old.at;
      if (gap < bestGap) { bestGap = gap; best = &old; }
      if (old.at < wanted) break;  // older still only drifts further away
    }
    if (!best || bestGap > maxGapNs_) return;
    const float seconds = float(double(now - best->at) / 1e9);
    float distance2 = 0, speedOld2 = 0, speedNew2 = 0;
    for (int axis = 0; axis < 3; ++axis) {
      const float predicted = best->position[axis] + best->velocity[axis] * seconds;
      const float error = predicted - position[axis];
      distance2 += error * error;
      speedOld2 += best->velocity[axis] * best->velocity[axis];
      speedNew2 += velocity[axis] * velocity[axis];
    }
    const float millimetres = std::sqrt(distance2) * 1000.f;
    if (window_.size() < kWindow) window_.push_back(millimetres);
    else window_[replace_++ % kWindow] = millimetres;
    ++samples_;
    sum_ += millimetres;
    max_ = std::max(max_, millimetres);
    horizonMs_ = float(double(horizonNs) / 1e6);
    const float still2 = kStillMetresPerSecond * kStillMetresPerSecond;
    if (speedOld2 < still2 && speedNew2 < still2) {
      ++stillSamples_;
      stillMax_ = std::max(stillMax_, millimetres);
    }
  }

  // The window since the last call, then a new window.
  PredictionSummary take() {
    PredictionSummary summary;
    summary.samples = samples_;
    summary.horizonMs = horizonMs_;
    if (samples_) {
      summary.meanMm = float(sum_ / samples_);
      summary.maxMm = max_;
      std::vector<float> sorted(window_);
      std::sort(sorted.begin(), sorted.end());
      summary.p95Mm = sorted[std::min(sorted.size() - 1, size_t(std::ceil(0.95 * double(sorted.size()))) - 1)];
    }
    summary.stillSamples = stillSamples_;
    summary.stillMaxMm = stillMax_;
    window_.clear();
    replace_ = samples_ = stillSamples_ = 0;
    sum_ = 0;
    max_ = stillMax_ = 0;
    return summary;
  }

  // A tracking interval or two: the sample nearest now - horizon is at most this far from it.
  void setMaxGap(uint64_t ns) { maxGapNs_ = ns; }

  void clear() {
    count_ = next_ = 0;
    take();
  }

private:
  struct Sample {
    uint64_t at = 0;
    float position[3] = {};
    float velocity[3] = {};
  };
  static bool finite3(const float v[3]) {
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
  }
  std::array<Sample, kHistory> history_{};
  size_t next_ = 0, count_ = 0;
  std::vector<float> window_;
  size_t replace_ = 0;
  uint32_t samples_ = 0, stillSamples_ = 0;
  double sum_ = 0;
  float max_ = 0, stillMax_ = 0, horizonMs_ = 0;
  uint64_t maxGapNs_ = 10000000ULL;  // 10 ms
};
// VROSTBridge 1.0.0.6: the distance between the two controllers as SteamVR on this PC tracks them,
// while both are held still (slower than PredictionCheck::kStillMetresPerSecond). The host logs the
// same distance as it received it and as it submitted it to its SteamVR (VROST_CONTROLLER_PAIR,
// driver patch 0020): three equal numbers mean the poses arrive and are used as tracked here. In the
// 1.0.0.5 test controller models held still against each other overlapped more on the stream than on
// the headset's own PC. Only the distance is kept.
struct PairSummary {
  uint32_t stillSamples = 0;
  float meanMm = 0, minMm = 0, maxMm = 0;
};

class PairCheck {
public:
  void add(const float left[3], const float leftVelocity[3], const float right[3], const float rightVelocity[3]) {
    float distance2 = 0, left2 = 0, right2 = 0;
    for (int axis = 0; axis < 3; ++axis) {
      if (!std::isfinite(left[axis]) || !std::isfinite(right[axis]) || !std::isfinite(leftVelocity[axis]) ||
          !std::isfinite(rightVelocity[axis]))
        return;
      const float d = left[axis] - right[axis];
      distance2 += d * d;
      left2 += leftVelocity[axis] * leftVelocity[axis];
      right2 += rightVelocity[axis] * rightVelocity[axis];
    }
    const float still2 = PredictionCheck::kStillMetresPerSecond * PredictionCheck::kStillMetresPerSecond;
    if (left2 >= still2 || right2 >= still2) return;
    const float millimetres = std::sqrt(distance2) * 1000.f;
    minMm_ = samples_ ? std::min(minMm_, millimetres) : millimetres;
    maxMm_ = samples_ ? std::max(maxMm_, millimetres) : millimetres;
    sum_ += millimetres;
    ++samples_;
  }
  // The window since the last call, then a new window.
  PairSummary take() {
    PairSummary summary;
    summary.stillSamples = samples_;
    if (samples_) {
      summary.meanMm = float(sum_ / samples_);
      summary.minMm = minMm_;
      summary.maxMm = maxMm_;
    }
    samples_ = 0;
    sum_ = 0;
    minMm_ = maxMm_ = 0;
    return summary;
  }

private:
  uint32_t samples_ = 0;
  double sum_ = 0;
  float minMm_ = 0, maxMm_ = 0;
};
} // namespace vrost
