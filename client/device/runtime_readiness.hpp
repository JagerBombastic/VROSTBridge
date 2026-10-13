#pragma once
#include <cmath>
#include <cstdint>
#include <algorithm>
#include <cctype>
#include <string>

namespace vrost {
// A plausible property can be stale during compositor startup (e.g. Index's
// default 90 Hz before the configured 120 Hz mode is active). Readiness requires
// actual display progress at the reported cadence, not a fixed sleep.
class DisplayReadiness {
  uint64_t firstFrame_ = 0, previousFrame_ = 0;
  double firstVsync_ = 0, previousVsync_ = 0;
  float refresh_ = 0;
  uint32_t width_ = 0, height_ = 0;
  unsigned samples_ = 0;
public:
  uint64_t frames = 0;
  double measuredHz = 0;
  double observedSeconds() const { return samples_ ? previousVsync_ - firstVsync_ : 0; }
  void reset() { *this = {}; }
  bool observe(double nowSeconds, bool vsyncValid, float sinceVsync,
               uint64_t frame, float hz, uint32_t width, uint32_t height) {
    if (!vsyncValid || !std::isfinite(nowSeconds) || !std::isfinite(sinceVsync) ||
        !std::isfinite(hz) || hz < 30 || hz > 240 || sinceVsync < 0 ||
        sinceVsync > 2.0 / hz || width < 32 || width > 8192 ||
        height < 32 || height > 8192) { reset(); return false; }
    const double vsync = nowSeconds - sinceVsync;
    if (!samples_ || std::abs(hz - refresh_) > .1f || width != width_ ||
        height != height_ || frame < previousFrame_ || vsync < previousVsync_ - .001 ||
        vsync - previousVsync_ > .5) {
      firstFrame_ = previousFrame_ = frame;
      firstVsync_ = previousVsync_ = vsync;
      refresh_ = hz; width_ = width; height_ = height;
      samples_ = 1; frames = 0; measuredHz = 0;
      return false;
    }
    if (frame == previousFrame_) return false;
    previousFrame_ = frame; previousVsync_ = vsync; ++samples_;
    frames = frame - firstFrame_;
    const auto elapsed = vsync - firstVsync_;
    if (elapsed < .050 || frames < 8 || samples_ < 4) return false;
    measuredHz = frames / elapsed;
    if (std::abs(measuredHz - hz) > hz * .03) {
      // Keep a rolling baseline while a physical mode is changing.
      firstFrame_ = frame; firstVsync_ = vsync; samples_ = 1;
      return false;
    }
    return true;
  }
};

// Observed on Beyond/Linux: the compositor selects the 75 Hz display mode but
// leaves Prop_DisplayFrequency_Float at 90. Never generalise this exception to
// Index's transient 90/120 startup race or to an arbitrary measured cadence.
inline bool beyond75Mismatch(const std::string& model,float reported,float effective) {
  auto lower=model;
  std::transform(lower.begin(),lower.end(),lower.begin(),[](unsigned char c){return std::tolower(c);});
  return lower.find("beyond")!=std::string::npos &&
         std::isfinite(reported) && std::abs(reported-90.f)<.1f && effective==75.f;
}

struct VerifiedDisplayTiming {
  std::string model;
  float reportedHz=0, effectiveHz=0;
  double measuredHz=0;
  bool matches(const std::string& currentModel,float currentReported) const {
    return !model.empty() && model==currentModel && std::isfinite(currentReported) &&
        std::abs(currentReported-reportedHz)<.1f &&
        std::isfinite(effectiveHz) && effectiveHz>=30 && effectiveHz<=240 &&
        std::isfinite(measuredHz) && std::abs(measuredHz-effectiveHz)<=effectiveHz*.03 &&
        (effectiveHz==reportedHz || beyond75Mismatch(model,reportedHz,effectiveHz));
  }
};

class BeyondDisplayReadiness {
  DisplayReadiness measured75_;
public:
  bool observe(const std::string& model,double now,bool valid,float since,
               uint64_t frame,float reported,uint32_t width,uint32_t height) {
    if(!beyond75Mismatch(model,reported,75)) {measured75_.reset();return false;}
    const bool ready=measured75_.observe(now,valid,since,frame,75,width,height);
    // Require sustained fresh display progress (roughly 120 frames). A short
    // startup transition, frozen counter, reset or invalid timer cannot qualify.
    return ready && measured75_.observedSeconds()>=1.5 && measured75_.frames>=110;
  }
  const DisplayReadiness& measurement() const {return measured75_;}
};
}
