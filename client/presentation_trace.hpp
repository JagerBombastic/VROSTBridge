#pragma once
#include "client/device/runtime_observation.hpp"
#include <array>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string>
#include <utility>

namespace vrost {
struct PresentationFrame {
  uint64_t at=0, source=0, packets=0, freshGapNs=0, intervalNs=0;
  uint64_t tickNs=0, submitNs=0, handoffNs=0;
  RuntimeTickTimings runtime;
  bool freshSubmission=false;
};
inline uint64_t presentationStallThreshold(float refresh) {
  return std::isfinite(refresh) && refresh>=30 ?
      uint64_t(std::max(25000000.0,3e9/double(refresh))) : 50000000ULL;
}
class PresentationTrace {
public:
  struct Incident {
    std::array<PresentationFrame,13> frames{}; // Eight preceding, trigger, four following.
    size_t size=0, trigger=0;
    uint64_t number=0, additionalStalls=0;
  };
  uint64_t incidents=0, suppressed=0;
private:
  std::array<PresentationFrame,8> history_{};
  size_t count_=0, cursor_=0;
  std::optional<Incident> pending_;
  uint64_t lastIncident_=0;
public:
  std::optional<Incident> record(const PresentationFrame &frame,float refresh) {
    const auto threshold=presentationStallThreshold(refresh);
    const bool stalled=frame.tickNs>threshold || frame.submitNs>threshold ||
        frame.handoffNs>threshold || frame.freshGapNs>threshold || frame.intervalNs>threshold;
    std::optional<Incident> ready;
    if(pending_) {
      pending_->frames[pending_->size++]=frame;
      if(stalled) ++pending_->additionalStalls;
      if(pending_->size-pending_->trigger==5) { ready=std::move(pending_);pending_.reset(); }
    } else if(stalled) {
      if(lastIncident_ && frame.at>=lastIncident_ && frame.at-lastIncident_<1000000000ULL) ++suppressed;
      else {
        Incident capture;
        for(size_t i=0;i<count_;++i) capture.frames[capture.size++]=history_[(cursor_+history_.size()-count_+i)%history_.size()];
        capture.trigger=capture.size;
        capture.frames[capture.size++]=frame;
        capture.number=++incidents;
        pending_=capture;lastIncident_=frame.at;
      }
    }
    history_[cursor_]=frame;cursor_=(cursor_+1)%history_.size();count_=std::min(count_+1,history_.size());
    return ready;
  }
  std::optional<Incident> finish() { auto result=std::move(pending_);pending_.reset();return result; }
};
// Steady half-rate presentation never crosses the stall threshold above (26.7 ms
// frames at 75 Hz), so classify each one-second window instead. The classes
// separate the three causes seen as "37 unique frames/s on a 75 Hz host":
//  - client_throttled: local SteamVR runs this app below refresh (its throttle
//    count, or our own submissions are low) — a client compositor/GPU issue;
//  - source_limited:   fewer complete encoded frames arrive — host or network;
//  - late_or_paired:   frames arrive at full rate but miss the compositor's
//    deadline or arrive in pairs, so some are superseded.
enum class Cadence { Idle, Full, ClientThrottled, SourceLimited, LateOrPaired };
struct CadenceSample {
  float refresh = 0;
  double uniqueFps = 0, submittedFps = 0, packetFps = 0;
  uint32_t throttledFrames = 0;
  bool streaming = false;
};
inline Cadence classifyCadence(const CadenceSample &s) {
  if (!s.streaming || !std::isfinite(s.refresh) || s.refresh < 30) return Cadence::Idle;
  const double full = s.refresh * .85, low = s.refresh * .7;
  if (s.uniqueFps >= full) return Cadence::Full;
  if (s.throttledFrames > 0 || s.submittedFps < low) return Cadence::ClientThrottled;
  if (s.packetFps < low) return Cadence::SourceLimited;
  return Cadence::LateOrPaired;
}
inline const char *cadenceName(Cadence c) {
  switch (c) {
  case Cadence::Full: return "full";
  case Cadence::ClientThrottled: return "client_throttled";
  case Cadence::SourceLimited: return "source_limited";
  case Cadence::LateOrPaired: return "late_or_paired";
  default: return "idle";
  }
}
// Report a class change only after it holds for two consecutive windows, so a
// single hitch does not flap the log or the UI.
class CadenceTracker {
  Cadence reported_ = Cadence::Idle, candidate_ = Cadence::Idle;
  int count_ = 0;
public:
  std::optional<std::pair<Cadence, Cadence>> update(Cadence current) {
    if (current == reported_) { count_ = 0; return {}; }
    if (current != candidate_) { candidate_ = current; count_ = 0; }
    if (++count_ < 2) return {};
    const auto previous = reported_;
    reported_ = current; count_ = 0;
    return std::make_pair(previous, current);
  }
  Cadence current() const { return reported_; }
};
inline std::string presentationIncidentJson(const PresentationTrace::Incident &incident) {
  std::ostringstream out;out<<std::fixed<<std::setprecision(3);
  out<<"PRESENTATION_INCIDENT {\"number\":"<<incident.number<<",\"trigger\":"<<incident.trigger
     <<",\"additional_stalls\":"<<incident.additionalStalls<<",\"frames\":[";
  for(size_t i=0;i<incident.size;++i) {
    const auto &f=incident.frames[i];const auto &r=f.runtime;
    if(i) out<<',';
    out<<"{\"monotonic_ns\":"<<f.at<<",\"source_ns\":"<<f.source<<",\"packets\":"<<f.packets
       <<",\"frame_valid\":"<<r.frameTimingValid<<",\"frame\":"<<r.frameIndex
       <<",\"compositor_time\":"<<r.compositorTime<<",\"dropped\":"<<r.dropped
       <<",\"mispresented\":"<<r.mispresented<<",\"reprojection\":"<<r.reprojectionFlags
       <<",\"events\":"<<r.events<<",\"event_budget\":"<<r.eventBudgetExhausted
       <<",\"event_ms\":"<<r.eventPollNs/1e6<<",\"geometry_ms\":"<<r.geometryNs/1e6
       <<",\"wait_poses_ms\":"<<r.waitPosesNs/1e6<<",\"focus_ms\":"<<r.inputFocusNs/1e6
       <<",\"frame_query_ms\":"<<r.frameQueryNs/1e6<<",\"tick_ms\":"<<f.tickNs/1e6
       <<",\"submit_ms\":"<<f.submitNs/1e6<<",\"handoff_ms\":"<<f.handoffNs/1e6
       <<",\"interval_ms\":"<<f.intervalNs/1e6<<",\"fresh_gap_ms\":"<<f.freshGapNs/1e6
       <<",\"fresh_submission\":"<<f.freshSubmission<<'}';
  }
  out<<"]}";return out.str();
}
}
