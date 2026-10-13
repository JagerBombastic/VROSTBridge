#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <utility>

namespace vrost {
// SteamVR explicit timing permits WaitGetPoses on a different thread from
// conversion. Submit, explicit timing and PostPresentHandoff still need the
// SAME queue lock used by FFmpeg. Never hold that lock across WaitGetPoses.
class PresentationQueue {
  std::function<void()> lock_, unlock_, timing_, handoff_;
  bool timingPending_ = false; // protected by the external Vulkan queue lock
  struct Guard {
    PresentationQueue &owner;
    explicit Guard(PresentationQueue &value) : owner(value) { owner.lock_(); }
    ~Guard() { owner.unlock_(); }
  };
public:
  PresentationQueue(std::function<void()> lock, std::function<void()> unlock,
                    std::function<void()> timing, std::function<void()> handoff)
      : lock_(std::move(lock)), unlock_(std::move(unlock)),
        timing_(std::move(timing)), handoff_(std::move(handoff)) {}
  void beginFrame() { Guard guard(*this); timingPending_ = true; }
  template <class Work> auto submit(Work work) {
    Guard guard(*this);
    if (timingPending_) { timing_(); timingPending_ = false; }
    return work();
  }
  template <class Retire> void finishFrame(Retire retire) {
    Guard guard(*this);
    handoff_();
    // Retire only after ALL runtime queue use for this frame, not just Submit.
    retire();
    timingPending_ = false;
  }
};

// Catch a just-late frame without buffering all frames or waiting out a network
// interruption. Preserve a 2 ms submission margin and bound the added wait at
// 3 ms (a quarter of a frame at 75-90 Hz). RC4 sessions on Wi-Fi timed out more
// often than they caught at the former 1.5 ms bound; a frame caught here is shown
// instead of repeating the previous one for a whole refresh.
inline uint64_t lateFrameBudget(float remainingSeconds, float refresh) {
  if (!std::isfinite(remainingSeconds) || !std::isfinite(refresh) ||
      remainingSeconds <= .002f || remainingSeconds > .1f || refresh < 30.f)
    return 0;
  const double budget = std::min({.003, .25 / double(refresh),
                                 double(remainingSeconds) - .002});
  return uint64_t(budget * 1e9);
}
// The caller supplies blocking waits (a fence or condition variable), not a
// polling sleep. A ready frame always wins immediately, even with zero budget.
template <class Clock, class Take, class Wait>
auto selectUntilReady(uint64_t budget, Clock clock, Take take, Wait wait) {
  const auto deadline=clock()+budget;
  while (true) {
    auto selected=take();
    if (selected) return selected;
    const auto now=clock();
    if (now>=deadline) return selected;
    wait(deadline-now);
  }
}
} // namespace vrost
