#pragma once
// Even pose timing (RC4.2).
//
// The host renders each frame with the newest head pose it has received and
// extrapolates every pose by the same measured delay. RC4.1 sampled poses every
// 4 ms on a free-running timer, which does not divide the 13.3 ms frame of a 75 Hz
// headset: consecutive frames got poses 12.2 or 16.2 ms apart instead of 13.3 ms,
// so each frame was predicted for a slightly wrong moment in a repeating 21 Hz
// pattern. Head-locked text and held objects jittered while the head moved.
//
// This schedule samples on a grid locked to the local display's refresh: N evenly
// spaced samples per refresh (spacing at most 3.5 ms), re-anchored to the measured
// vsync once a second. The host then picks a sample with the same age for every
// frame. When the host's pick point sits close to a sample (its frames alternate
// between two neighbouring samples), the grid shifts by half a spacing so the pick
// point falls between samples. All times are CLOCK_MONOTONIC nanoseconds.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <mutex>

namespace vrost {

class TrackingSchedule {
public:
  struct Stats {
    uint32_t perFrame = 0;
    double spacingMs = 0, phaseMs = 0;
    uint32_t shifts = 0, changes = 0, picks = 0;
    bool adapting = true, anchored = false;
  };

  static constexpr uint64_t maxSpacingNs = 3500000;
  static constexpr uint32_t windowPicks = 150;      // about two seconds of frames
  static constexpr uint64_t shiftHoldoffNs = 3000000000ULL;
  static constexpr uint32_t maxShifts = 8;

  static uint32_t samplesPerFrame(double hz) {
    if (!std::isfinite(hz) || hz < 30 || hz > 240) hz = 90;
    const double period = 1e9 / hz;
    return std::max<uint32_t>(1, std::min<uint32_t>(8, uint32_t(std::ceil(period / double(maxSpacingNs)))));
  }

  void configure(double hz, uint64_t now) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!std::isfinite(hz) || hz < 30 || hz > 240) hz = 90;
    period_ = uint64_t(std::llround(1e9 / hz));
    perFrame_ = samplesPerFrame(hz);
    spacing_ = period_ / perFrame_;
    phase_ = spacing_ / 2;
    anchor_ = now;
    anchored_ = false;
    resetWindowLocked(now);
    shifts_ = 0;
    adapting_ = true;
    lastShift_ = 0;
  }

  // The most recent display vsync (from IVRSystem::GetTimeSinceLastVsync).
  void anchor(uint64_t vsync) {
    std::lock_guard<std::mutex> lock(mutex_);
    anchor_ = vsync;
    anchored_ = true;
  }

  // The first grid time at least `minimumGap` after `after`.
  uint64_t next(uint64_t after, uint64_t minimumGap = 250000) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const uint64_t earliest = after + minimumGap;
    const int64_t base = int64_t(anchor_ + phase_);
    const int64_t delta = int64_t(earliest) - base;
    int64_t k = delta >= 0 ? (delta + int64_t(spacing_) - 1) / int64_t(spacing_)
                           : -((-delta) / int64_t(spacing_));
    const int64_t when = base + k * int64_t(spacing_);
    return uint64_t(std::max<int64_t>(when, int64_t(earliest)));
  }

  // Index (0..N-1) of a sample within its refresh, counted from the grid's first slot.
  int slot(uint64_t sampledAt) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return slotLocked(sampledAt);
  }

  // A newly displayed frame used the pose sampled at `sampledAt`.
  void observePick(uint64_t sampledAt, uint64_t now) {
    if (!sampledAt) return;
    std::lock_guard<std::mutex> lock(mutex_);
    const int current = slotLocked(sampledAt);
    if (lastSlot_ >= 0 && current != lastSlot_) ++changes_;
    lastSlot_ = current;
    ++picks_;
    if (picks_ < windowPicks) return;
    // More than 5% of frames switched sample: the pick point sits on a sample.
    const bool unstable = changes_ * 20 > picks_;
    lastChanges_ = changes_;
    if (unstable && adapting_ && perFrame_ > 1 && (lastShift_ == 0 || now - lastShift_ >= shiftHoldoffNs)) {
      phase_ = (phase_ + spacing_ / 2) % spacing_;
      ++shifts_;
      lastShift_ = now;
      if (shifts_ >= maxShifts) adapting_ = false; // the host's pick time varies too much
    }
    resetWindowLocked(now);
  }

  Stats stats() const {
    std::lock_guard<std::mutex> lock(mutex_);
    Stats s;
    s.perFrame = perFrame_;
    s.spacingMs = spacing_ / 1e6;
    s.phaseMs = phase_ / 1e6;
    s.shifts = shifts_;
    s.changes = lastChanges_;
    s.picks = picks_;
    s.adapting = adapting_;
    s.anchored = anchored_;
    return s;
  }

private:
  int slotLocked(uint64_t sampledAt) const {
    const int64_t rel = int64_t(sampledAt) - int64_t(anchor_ + phase_);
    int64_t inFrame = rel % int64_t(period_);
    if (inFrame < 0) inFrame += int64_t(period_);
    const int64_t index = (inFrame + int64_t(spacing_) / 2) / int64_t(spacing_);
    return int(index % int64_t(perFrame_));
  }
  void resetWindowLocked(uint64_t) {
    picks_ = 0;
    changes_ = 0;
    lastSlot_ = -1;
  }

  mutable std::mutex mutex_;
  uint64_t period_ = 13333333, spacing_ = 3333333, anchor_ = 0, phase_ = 1666666;
  uint32_t perFrame_ = 4;
  bool anchored_ = false, adapting_ = true;
  int lastSlot_ = -1;
  uint32_t picks_ = 0, changes_ = 0, lastChanges_ = 0, shifts_ = 0;
  uint64_t lastShift_ = 0;
};

} // namespace vrost
