#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>

namespace vrost {
struct HapticCommand { float duration, frequency, amplitude; };

// SteamVR turns each vibration action into Lighthouse work. Replaying every
// overlapping game event can overfill that queue. Mix each hand's bounded
// envelope and submit non-overlapping slices instead, with no replay backlog.
class HapticScheduler {
  struct Pulse { uint64_t until = 0; float frequency = 0, amplitude = 0; };
  struct Hand { std::array<Pulse,16> pulses{}; uint64_t next = 0; };
  std::array<Hand,2> hands_{};
public:
  static constexpr uint64_t sliceNs = 8000000;
  // RC1.3 capped every command at 100 ms, cutting long game rumbles short.
  // Keep a bound (a lost stop must not buzz indefinitely) but honour normal
  // effect lengths. A zero/negative duration with amplitude is OpenVR/OpenXR's
  // "shortest possible pulse"; zero amplitude is an explicit stop.
  static constexpr float maxDurationSeconds = 1.f, minimumPulseSeconds = .01f;
  uint64_t received = 0, sent = 0, merged = 0, expired = 0, replaced = 0, stopped = 0;
  void clear() { hands_ = {}; }
  void add(int hand, HapticCommand command, uint64_t now) {
    if (hand < 0 || hand > 1 || !std::isfinite(command.duration) ||
        !std::isfinite(command.frequency) || !std::isfinite(command.amplitude)) return;
    ++received;
    if (command.amplitude <= 0) {
      auto& h = hands_[hand];
      for (auto& pulse : h.pulses) pulse = {};
      ++stopped;
      return;
    }
    if (command.duration <= 0) command.duration = minimumPulseSeconds;
    auto& h = hands_[hand];
    auto* slot = &h.pulses[0];
    bool pending = false;
    for (auto& pulse : h.pulses) {
      pending |= pulse.until > now;
      if (pulse.until < slot->until) slot = &pulse;
    }
    if (pending) ++merged;
    if (slot->until > now) ++replaced;
    *slot = {now + uint64_t(std::min(command.duration, maxDurationSeconds) * 1e9),
             std::clamp(command.frequency, 0.f, 320.f),
             std::clamp(command.amplitude, 0.f, 1.f)};
  }
  std::optional<HapticCommand> poll(int hand, uint64_t now, bool available) {
    if (hand < 0 || hand > 1) return {};
    auto& h = hands_[hand];
    if (!available) { h = {}; return {}; }
    for (auto& pulse : h.pulses)
      if (pulse.until && pulse.until <= now) { pulse = {}; ++expired; }
    if (now < h.next) return {};
    const Pulse* strongest = nullptr;
    for (const auto& pulse : h.pulses)
      if (pulse.until > now && (!strongest || pulse.amplitude > strongest->amplitude)) strongest = &pulse;
    if (!strongest) return {};
    const uint64_t duration = std::min(sliceNs, strongest->until - now);
    h.next = now + duration;
    ++sent;
    return HapticCommand{float(double(duration) / 1e9), strongest->frequency, strongest->amplitude};
  }
};
} // namespace vrost
