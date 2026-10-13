#pragma once
#include "client/device/gaze.hpp"
#include "core/stream.hpp"
#include "client/device/view_geometry.hpp"
#include "client/device/runtime_observation.hpp"
#include "client/device/runtime_readiness.hpp"
#include "client/device/haptic_scheduler.hpp"
#include "client/device/prediction.hpp"
#include "client/device/tracking_schedule.hpp"
#include "client/device/visible_area.hpp"
#include "openvr.h"
#include <array>
#include <atomic>
#include <filesystem>
#include <iostream>
#include <map>
#include <thread>
namespace vrost {
class OpenVRDevice {
  void *loader_ = nullptr;
  void (*shutdown_)() = nullptr;
  std::atomic<bool> run_{false}, focused_{false}, tracked_{false}, streaming_{false};
  std::atomic<uint64_t> streamEpoch_{0};
  std::atomic<bool> spaceDirty_{true}, menuRequested_{false};
  // 1.0.0.10: SteamVR's system-button events per hand (left, right), read by the tracking loop:
  // (time << 1) | pressed, 0 before the first. One word, so time and state are always a pair.
  std::array<std::atomic<uint64_t>, 2> systemEvent_{};
  std::atomic<uint64_t> systemEvents_{0};
  std::thread tracker_;
  std::mutex hapticMutex_;
  bool geometryPending_ = false;
  uint64_t geometryRetryAt_ = 0;
  bool refreshViews();
  HapticScheduler haptics_;
  vr::VRActionSetHandle_t actionSet_ = 0;
  std::array<vr::VRInputValueHandle_t, 2> handSources_{};
  std::map<std::string, vr::VRActionHandle_t> actions_;
  GazeReceiver gaze_;
  PredictionClock clock_;
  TrackingSchedule schedule_;
  bool prediction_ = true;
  void trackingLoop(void *transport);

public:
  ViewGeometry geometry;
  vr::IVRSystem *system = nullptr;
  vr::IVRCompositor *compositor = nullptr;
  vr::IVRInput *input = nullptr;
  vr::IVRChaperone *chaperone = nullptr;
  vr::IVROverlay *overlay = nullptr; // optional: in-headset status
  uint32_t width = 0, height = 0;
  float refresh = 90;
  std::string model;
  void open(const std::filesystem::path &directory,const VerifiedDisplayTiming& timing);
  void requestMenu() { menuRequested_ = true; }
  void requestPlayspace() { spaceDirty_ = true; }
  bool inputFocus() const { return focused_; }
  std::atomic<uint64_t> trackingSamples{0}, trackingGapMaxUs{0}, predictionNs{0};
  // 1.0.0.5: the headset's own position while a stream is negotiated. The host sends a frame
  // only for a head pose this client sent, so without one no video comes back: the headset is
  // not tracked here (base stations not seen, headset asleep), not a video or network fault.
  std::atomic<uint64_t> headPoses{0}, headPosesMissing{0}, headUntrackedSince{0};
  std::atomic<int> headTrackingResult{vr::TrackingResult_Uninitialized};
  // How long the head pose has been missing (0 while tracked or not streaming).
  uint64_t headUntrackedMs(uint64_t now) const {
    const uint64_t since = headUntrackedSince.load();
    return since && now > since ? (now - since) / 1000000 : 0;
  }
  // Call before startTracking. Off sends unpredicted poses (RC1.3 behaviour).
  void setPrediction(bool enabled) { prediction_ = enabled; }
  bool prediction() const { return prediction_; }
  // Local acquisition time of the tracking sample behind a frame timestamp.
  uint64_t sampleTime(uint64_t frameTimestamp, uint64_t now) const {
    return clock_.acquiredAt(frameTimestamp, now);
  }
  // Even pose timing: a displayed frame used the pose sampled at `sampledAt`.
  void observePick(uint64_t sampledAt, uint64_t now) { schedule_.observePick(sampledAt, now); }
  TrackingSchedule::Stats trackingSchedule() const { return schedule_.stats(); }
#ifndef _WIN32
  void gaze(const std::string &path) { gaze_.open(path); }
#endif
  void gazeUdp(uint16_t port) { gaze_.openUdp(port); } // 1.0.0.7: Windows (loopback UDP), also Linux
  bool shownGaze(VrostQuat &q, uint64_t now, uint64_t &ageMs) const { return gaze_.shown(q, now, ageMs); }
  void startTracking(void *transport) {
    run_ = true;
    tracker_ = std::thread([this, transport] { trackingLoop(transport); });
  }
  void stopTracking() {
    run_ = false;
    if (tracker_.joinable())
      tracker_.join();
  }
  void stream(bool enabled) { if(enabled)++streamEpoch_; streaming_ = enabled; }
  void haptic(VrostEvent e) {
    std::lock_guard<std::mutex> l(hapticMutex_);
    const int hand = e.device == vrost_path("/user/hand/left") ? 0 :
                     e.device == vrost_path("/user/hand/right") ? 1 : -1;
    haptics_.add(hand, {e.duration, e.frequency, e.amplitude}, monotonicNs());
  }
  std::array<VrostView, 2> views() {
    return geometry.snapshot().views;
  }
  // 1.0.0.11: the part of each eye's image the lenses show (call on the main thread, before tracking starts).
  VisibleArea visibleArea();
  bool tick(Pose &head, bool &geometryChanged, bool &quit, RuntimeTickTimings &timings);
  void close();
  ~OpenVRDevice();
};
} // namespace vrost
