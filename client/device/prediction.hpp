#pragma once
#include <array>
#include <cstdint>
#include <mutex>

namespace vrost {
// Motion prediction, matching the pinned ALVR reference client. The pinned
// host (since ALVR 4a463af5) extrapolates every received pose by its measured
// motion-to-photon latency, using the velocities sent with it. The client
// therefore samples *current* poses (0 s ahead) and only labels them with the
// target time now + measured pipeline latency (ALVR's
// get_total_prediction_offset, capped by the host's max_prediction); the host
// then predicts once. RC2-RC4 also sampled SteamVR at the target time, which
// predicted every pose twice.
//
// Frame timestamps are therefore future times. Freshness, hold windows and
// age metrics must use the local acquisition time recorded here, never the
// target. Targets are strictly increasing so frame ordering survives a
// falling latency estimate.
class PredictionClock {
  struct Entry { uint64_t target = 0, acquired = 0; };
  mutable std::mutex mutex_;
  std::array<Entry, 1024> ring_{}; // ~4 s at the 250 Hz tracking cadence
  size_t next_ = 0;
  uint64_t lastTarget_ = 0, lastOffset_ = 0;

public:
  static constexpr uint64_t maxOffsetNs = 100000000ULL;
  uint64_t target(uint64_t acquired, uint64_t offset) {
    if (offset > maxOffsetNs) offset = maxOffsetNs;
    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t value = acquired + offset;
    if (value <= lastTarget_) value = lastTarget_ + 1;
    lastTarget_ = value;
    lastOffset_ = value - acquired;
    ring_[next_] = {value, acquired};
    next_ = (next_ + 1) % ring_.size();
    return value;
  }
  // Exact lookup for a frame timestamp. Unknown targets (for example an old
  // session's frame) fall back to the latest offset and never exceed now.
  uint64_t acquiredAt(uint64_t target, uint64_t now) const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (size_t i = 0; i < ring_.size(); ++i) {
      const auto &entry = ring_[(next_ + ring_.size() - 1 - i) % ring_.size()];
      if (!entry.target || entry.target < target) break; // strictly increasing
      if (entry.target == target) return entry.acquired;
    }
    const uint64_t estimate = target > lastOffset_ ? target - lastOffset_ : target;
    return estimate < now ? estimate : now;
  }
  uint64_t lastOffset() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return lastOffset_;
  }
};

// One tracking tick: what SteamVR is asked for and how the sample is labelled.
struct PoseSample {
  float secondsFromNow = 0.f; // GetDeviceToAbsoluteTrackingPose prediction
  uint64_t timestamp = 0;     // label sent with the poses and used by frames
};
// The poses are always current; prediction (offset > 0) only moves the label.
inline PoseSample planPoseSample(PredictionClock &clock, uint64_t now, uint64_t offset) {
  return {0.f, clock.target(now, offset)};
}
} // namespace vrost
