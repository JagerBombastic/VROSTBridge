#include "client/device/openvr_device.hpp"
#include "client/device/input_protocol.hpp"
#include "client/device/runtime_events.hpp"
#include "client/device/openvr_projection.hpp"
#include "client/device/skeletal.hpp"
#include "client/device/prediction_check.hpp"
#ifdef _WIN32
#include "client/platform/windows/dynamic_library.hpp"
#include "client/platform/windows/process.hpp"
#else
#include <dlfcn.h>
#endif
#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>
namespace vrost {
static Pose copy(const vr::HmdMatrix34_t &m) {
  Pose p;
  std::memcpy(p.m, m.m, sizeof(p.m));
  return p;
}
// 1.0.0.5: a short word for the headset's tracking state, for HEAD_TRACKING lines.
static const char *headTrackingReason(const vr::TrackedDevicePose_t &pose) {
  if (!pose.bDeviceIsConnected) return "headset-not-connected";
  switch (pose.eTrackingResult) {
  case vr::TrackingResult_Running_OK: return pose.bPoseIsValid ? "ok" : "pose-invalid";
  case vr::TrackingResult_Uninitialized: return "not-tracked-yet";
  case vr::TrackingResult_Calibrating_InProgress: return "calibrating";
  case vr::TrackingResult_Calibrating_OutOfRange: return "base-stations-not-seen";
  case vr::TrackingResult_Running_OutOfRange: return "out-of-range";
  case vr::TrackingResult_Fallback_RotationOnly: return "rotation-only";
  default: return "unknown";
  }
}
void OpenVRDevice::open(const std::filesystem::path &dir,const VerifiedDisplayTiming& timing) {
#ifdef _WIN32
  // Windows: openvr_api.dll from the client's folder, as libopenvr_api.so on Linux.
  const auto library = dir / "openvr_api.dll";
  loader_ = loadLibrary(library);
  if (!loader_)
    throw std::runtime_error(lastLibraryError(library));
  const auto dlsym = [](void *handle, const char *name) { return librarySymbol(handle, name); };
#else
  loader_ = dlopen((dir / "libopenvr_api.so").c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!loader_)
    throw std::runtime_error(dlerror());
#endif
  auto init = reinterpret_cast<uint32_t (*)(
      vr::EVRInitError *, vr::EVRApplicationType, const char *)>(
      dlsym(loader_, "VR_InitInternal2"));
  auto get = reinterpret_cast<void *(*)(const char *, vr::EVRInitError *)>(
      dlsym(loader_, "VR_GetGenericInterface"));
  auto end =
      reinterpret_cast<void (*)()>(dlsym(loader_, "VR_ShutdownInternal"));
  auto describe = reinterpret_cast<const char *(*)(vr::EVRInitError)>(
      dlsym(loader_, "VR_GetVRInitErrorAsEnglishDescription"));
  if (!init || !get || !end || !describe)
    throw std::runtime_error("OpenVR loader exports missing");
  vr::EVRInitError error{};
  init(&error, vr::VRApplication_Scene, nullptr);
  if (error)
    throw std::runtime_error(describe(error));
  shutdown_ = end;
  system = static_cast<vr::IVRSystem *>(get(vr::IVRSystem_Version, &error));
  compositor =
      static_cast<vr::IVRCompositor *>(get(vr::IVRCompositor_Version, &error));
  input = static_cast<vr::IVRInput *>(get(vr::IVRInput_Version, &error));
  chaperone =
      static_cast<vr::IVRChaperone *>(get(vr::IVRChaperone_Version, &error));
  overlay = static_cast<vr::IVROverlay *>(get(vr::IVROverlay_Version, &error));
  if (!system || !compositor || !input)
    throw std::runtime_error("Required SteamVR interfaces unavailable");
  compositor->SetTrackingSpace(vr::TrackingUniverseStanding);
  system->GetRecommendedRenderTargetSize(&width, &height);
  vr::ETrackedPropertyError pe{};
  refresh = system->GetFloatTrackedDeviceProperty(
      0, vr::Prop_DisplayFrequency_Float, &pe);
  if (pe || !std::isfinite(refresh) || refresh < 30 || refresh > 240)
    throw std::runtime_error(
        "SteamVR did not report a valid display frequency");
  char text[512]{};
  system->GetStringTrackedDeviceProperty(0, vr::Prop_ModelNumber_String, text,
                                         sizeof(text), &pe);
  model = text;
  if(!timing.matches(model,refresh))
    throw std::runtime_error("Headset display properties changed after verification. Restart the session.");
  const auto reportedRefresh=refresh;
  refresh=timing.effectiveHz;
  diagnosticLine("STATUS Verified display timing: reported_hz="+std::to_string(reportedRefresh)+
                 " effective_hz="+std::to_string(refresh)+" measured_hz="+std::to_string(timing.measuredHz));
  if (!refreshViews()) throw std::runtime_error("SteamVR eye geometry is unavailable.");
#ifdef _WIN32
  const std::string manifest = (dir / "assets" / "actions.json").u8string(); // OpenVR takes UTF-8 paths
#else
  const std::string manifest = (dir / "assets/actions.json").string();
#endif
  if (input->SetActionManifestPath(manifest.c_str()) !=
          vr::VRInputError_None ||
      input->GetActionSetHandle("/actions/test", &actionSet_) !=
          vr::VRInputError_None)
    throw std::runtime_error("Cannot load Index controller action bindings");
  for (const char *side : {"left", "right"})
    for (const char *name :
         {"pose", "trigger", "grip", "grip_value", "pad_force", "stick", "pad",
          "a", "b", "trigger_touch", "trigger_click", "stick_click",
          "stick_touch", "pad_touch", "a_touch", "b_touch", "skeleton",
          "haptic", "remote_menu"}) {
      std::string key = std::string(side) + "_" + name;
      std::string path =
          "/actions/test/" +
          std::string(std::string(name) == "haptic" ? "out/" : "in/") + key;
      vr::VRActionHandle_t handle = 0;
      if (input->GetActionHandle(path.c_str(), &handle) !=
          vr::VRInputError_None)
        throw std::runtime_error("Missing controller action: " + key);
      actions_[key] = handle;
    }
  for (int hand = 0; hand < 2; ++hand) {
    const auto path = hand ? "/user/hand/right" : "/user/hand/left";
    if (input->GetInputSourceHandle(path, &handSources_[hand]) !=
            vr::VRInputError_None ||
        handSources_[hand] == vr::k_ulInvalidInputValueHandle)
      throw std::runtime_error(std::string("Cannot resolve controller input source: ") + path);
  }
}
bool OpenVRDevice::refreshViews() {
  ViewGeometry::Views views{};
  std::array<RawProjection,2> raw{};
  for (int e = 0; e < 2; ++e) {
    system->GetProjectionRaw(static_cast<vr::EVREye>(e), &raw[e][0], &raw[e][1],
                             &raw[e][2], &raw[e][3]);
    views[e] = openvrView(copy(system->GetEyeToHeadTransform(static_cast<vr::EVREye>(e))),raw[e]);
  }
  if (!ViewGeometry::valid(views)) {
    geometryPending_ = true;
    return false; // A transient property update must not poison live geometry.
  }
  geometryPending_ = false;
  const bool changed=geometry.update(views);
  if(changed) diagnosticLine(viewGeometryDiagnostic(geometry.snapshot(),raw));
  return changed;
}
// 1.0.0.11: what the headset's lenses show of each eye's image (client/device/visible_area.hpp):
// SteamVR's visible-area mesh and its lens distortion of the panel's edge (64 points a side,
// red, green and blue). Read once at start; a headset without either gives an empty description.
VisibleArea OpenVRDevice::visibleArea() {
  VisibleArea area;
  if (!system) return area;
  const auto finite = [](float v) { return std::isfinite(v) && std::fabs(v) < 16.f; };
  for (int e = 0; e < 2; ++e) {
    const auto eye = static_cast<vr::EVREye>(e);
    const vr::HiddenAreaMesh_t mesh = system->GetHiddenAreaMesh(eye, vr::k_eHiddenAreaMesh_Inverse);
    if (mesh.pVertexData && mesh.unTriangleCount > 0 && mesh.unTriangleCount < 65536) {
      bool valid = true;
      for (uint32_t t = 0; t < mesh.unTriangleCount && valid; ++t) {
        std::array<float, 6> triangle{};
        for (int v = 0; v < 3; ++v) {
          const auto &point = mesh.pVertexData[t * 3 + v];
          valid = valid && finite(point.v[0]) && finite(point.v[1]);
          triangle[v * 2] = point.v[0];
          triangle[v * 2 + 1] = point.v[1];
        }
        area.triangles[e].push_back(triangle);
      }
      if (!valid) area.triangles[e].clear();
    }
    constexpr int perSide = 64;
    std::array<std::vector<std::array<float, 2>>, 3> loops;
    bool valid = true;
    for (int side = 0; side < 4 && valid; ++side)
      for (int i = 0; i < perSide && valid; ++i) {
        // Clockwise around the panel: top, right, bottom, left.
        const float t = float(i) / perSide;
        const float u = side == 0 ? t : side == 1 ? 1.f : side == 2 ? 1.f - t : 0.f;
        const float v = side == 0 ? 0.f : side == 1 ? t : side == 2 ? 1.f : 1.f - t;
        vr::DistortionCoordinates_t coordinates{};
        if (!system->ComputeDistortion(eye, u, v, &coordinates)) {
          valid = false;
          break;
        }
        const float *channels[3] = {coordinates.rfRed, coordinates.rfGreen, coordinates.rfBlue};
        for (int c = 0; c < 3; ++c) {
          valid = valid && finite(channels[c][0]) && finite(channels[c][1]);
          loops[c].push_back({channels[c][0], channels[c][1]});
        }
      }
    if (valid)
      for (int c = 0; c < 3; ++c) area.outlines[e][c] = std::move(loops[c]);
  }
  alignMeshes(area); // the mesh's way up checked against the outline (client/device/visible_area.hpp)
  return area;
}
bool OpenVRDevice::tick(Pose &head, bool &changed, bool &quit, RuntimeTickTimings &timings) {
  timings = {};
  const auto eventStart = monotonicNs();
  vr::VREvent_t ev{};
  while (runtimeEventBudgetAvailable(timings.events,monotonicNs()-eventStart) &&
         system->PollNextEvent(&ev, sizeof(ev))) {
    ++timings.events;
    if (runtimeRequestsExit(ev.eventType)) {
      diagnosticLine("STATUS Local SteamVR requested this client to quit; pid=" + std::to_string(ev.data.process.pid));
      system->AcknowledgeQuit_Exiting();
      quit = true;
    } else if (ev.eventType == vr::VREvent_ProcessQuit) {
      ++timings.exitedProcesses; // Summarize once per second; never log an event flood here.
    }
    if (runtimeChangesGeometry(ev.eventType)) { geometryPending_ = true; geometryRetryAt_ = 0; }
    switch (ev.eventType) {
    case vr::VREvent_ChaperoneUniverseHasChanged:
    case vr::VREvent_ChaperoneDataHasChanged:
      spaceDirty_ = true;
      break;
    case vr::VREvent_ButtonPress:
    case vr::VREvent_ButtonUnpress:
      // 1.0.0.10: the system button as SteamVR reports it (sendSystemButtonToAllApps), also while
      // its input focus hides the action from this client (input_protocol.hpp SystemButtonLatch).
      if (ev.data.controller.button == vr::k_EButton_System) {
        const auto role = system->GetControllerRoleForTrackedDeviceIndex(ev.trackedDeviceIndex);
        const int hand = role == vr::TrackedControllerRole_LeftHand ? 0 :
                         role == vr::TrackedControllerRole_RightHand ? 1 : -1;
        if (hand >= 0) {
          systemEvent_[hand] = (std::max<uint64_t>(monotonicNs(), 1) << 1) |
                               (ev.eventType == vr::VREvent_ButtonPress ? 1 : 0);
          ++systemEvents_;
        }
      }
      break;
    default:
      break;
    }
    if (quit) break;
  }
  timings.eventPollNs = monotonicNs()-eventStart;
  timings.eventBudgetExhausted = !runtimeEventBudgetAvailable(timings.events,timings.eventPollNs);
  const auto geometryStart=monotonicNs();
  changed = false;
  if (geometryPending_ && geometryStart>=geometryRetryAt_) {
    timings.geometryAttempted=true;
    changed=refreshViews();
    // Invalid intermediate IPD/projection updates retain the last valid view.
    // Retry without hammering synchronous metadata reads every display frame.
    if(geometryPending_) geometryRetryAt_=monotonicNs()+50000000ULL;
  }
  timings.geometryNs=monotonicNs()-geometryStart;
  if (changed) diagnosticLine("STATUS Headset eye geometry updated during session");
  if (quit) return false;
  std::array<vr::TrackedDevicePose_t, vr::k_unMaxTrackedDeviceCount> poses{};
  const auto waitStart=monotonicNs();
  auto status=compositor->WaitGetPoses(poses.data(), poses.size(), nullptr, 0);
  timings.waitPosesNs=monotonicNs()-waitStart;
  bool good = status == vr::VRCompositorError_None && poses[0].bPoseIsValid &&
              poses[0].bDeviceIsConnected;
  tracked_ = good;
  const auto focusStart=monotonicNs();
  // Input focus is independent of head tracking: a brief Lighthouse occlusion
  // or a late WaitGetPoses must not release both controllers (RC1.3 did).
  focused_ = system->IsInputAvailable();
  timings.inputFocusNs=monotonicNs()-focusStart;
  const auto frameStart=monotonicNs();
  vr::Compositor_FrameTiming frame{};frame.m_nSize=sizeof(frame);
  if(compositor->GetFrameTiming(&frame) && std::isfinite(frame.m_flSystemTimeInSeconds) && frame.m_flSystemTimeInSeconds>=0) {
    timings.frameTimingValid=true;
    timings.frameIndex=frame.m_nFrameIndex; timings.compositorTime=frame.m_flSystemTimeInSeconds;
    timings.dropped=frame.m_nNumDroppedFrames; timings.mispresented=frame.m_nNumMisPresented;
    timings.reprojectionFlags=frame.m_nReprojectionFlags;
  }
  timings.frameQueryNs=monotonicNs()-frameStart;
  if (good) head = copy(poses[0].mDeviceToAbsoluteTracking);
  return good; // Dashboard focus must not stop head tracking or replace video.
}
void OpenVRDevice::trackingLoop(void *t) {
  const uint64_t ids[] = {vrost_path("/user/head"), vrost_path("/user/hand/left"),
                          vrost_path("/user/hand/right")};
  const auto &mappings = indexMappings;
  std::array<std::vector<uint64_t>, 2> paths;
  for (int hand = 0; hand < 2; ++hand)
    for (auto m : mappings) {
      std::string p = std::string("/user/hand/") + (hand ? "right" : "left") +
                      "/input/" + m.path;
      paths[hand].push_back(vrost_path(p.c_str()));
    }
  bool wasStreaming = false, wasActive = false;
  bool headStateKnown = false, headWasValid = false;
  int headLastResult = vr::TrackingResult_Uninitialized;
  uint64_t observedEpoch=0, sentViewRevision=0;
  uint64_t batteryAt = 0, spaceRetryAt = 0, menuUntil = 0, previousSample = 0,
           inputReportAt = 0;
  // 1.0.0.10: when input focus was lost (0 while focused), how often, and how many of those losses
  // were short enough for the controls to be kept through them (input_protocol.hpp holdThroughFocusLoss).
  uint64_t focusLostAt = 0, focusLosses = 0, focusBrief = 0;
  struct HandDiagnostics {
    bool connected = false, tracked = false, systemActive = false;
    uint32_t device = vr::k_unTrackedDeviceIndexInvalid;
    int trackingResult = vr::TrackingResult_Uninitialized;
    uint64_t trackingLosses = 0, fullUpdates = 0, hapticErrors = 0;
    ControllerInputRefresh refresh;
    uint32_t actionsActive = 0;
    uint64_t errors = 0, changes = 0;
    // 1.0.0.10: the system button forwarded as one clean press (input_protocol.hpp).
    SystemButtonLatch systemLatch;
    SystemButtonEvents systemEvents;
    uint64_t systemEventWord = 0;
    // Whether this client lost input focus during the current press, and how many presses did.
    bool systemFocusLost = false;
    uint64_t systemFocusLostPresses = 0;
    float trigger = 0, stickX = 0, stickY = 0;
    skeletal::Cache skeleton;
    skeletal::NativeCache native;
    uint32_t schemaDevice = vr::k_unTrackedDeviceIndexInvalid;
    vr::VRInputValueHandle_t schemaOrigin = vr::k_ulInvalidInputValueHandle;
    uint64_t schemaRetryAt = 0, skeletonSamples = 0, skeletonErrors = 0;
    bool schemaValid = false, skeletonSent = false, curlsValid = false;
    int skeletonLevel = -1, skeletonError = 0;
    const char *skeletonReason = "waiting";
    std::array<float, 5> curls{};
    // 1.0.0.6: the host's extrapolation repeated on this controller's samples
    // (prediction_check.hpp); reported with each INPUT line.
    PredictionCheck prediction;
  };
  std::array<HandDiagnostics, 2> handDiagnostics{};
  PairCheck controllerPair;  // 1.0.0.6: the controllers' distance while held still (prediction_check.hpp)
  std::array<MotionAcceleration, 3> accelerations{};  // 1.0.0.14: head, left, right (acceleration.hpp)
  uint64_t skeletonAt = 0;
  const uint64_t skeletonPeriod = uint64_t(1000000000.0 / std::min(refresh,120.f));
  std::array<std::vector<float>, 2> previous;
  for (auto &values : previous)
    values.assign(std::size(mappings), std::numeric_limits<float>::quiet_NaN());
  auto release = [&] {
    std::vector<VrostButton> updates;
    for (int hand = 0; hand < 2; ++hand)
      for (size_t i = 0; i < paths[hand].size(); ++i) {
        updates.push_back({paths[hand][i], uint32_t(mappings[i].kind), 0});
        previous[hand][i] = 0;
      }
    vrost_buttons(t, updates.data(), updates.size());
  };
  diagnosticLine(std::string("STATUS Motion prediction: ") +
                 (prediction_ ? (acceleration_ ? "on (current poses with their acceleration folded in; the host predicts once by the measured latency)"
                                               : "on (current poses; the host predicts once by the measured latency)")
                              : "off (current poses, timestamped now)"));
  // Even pose timing: sample on a grid locked to this display's refresh so the host
  // gets a pose of the same age for every frame (see tracking_schedule.hpp).
  schedule_.configure(refresh, monotonicNs());
  uint64_t anchorAt = 0;
  {
    const auto s = schedule_.stats();
    diagnosticLine("STATUS Pose sampling: " + std::to_string(s.perFrame) + " per " +
                   std::to_string(int(std::lround(refresh))) + " Hz refresh, locked to the headset display");
  }
  while (run_) {
    const bool online = streaming_;
    const auto sampleNow = monotonicNs();
    if (sampleNow >= anchorAt) {
      float sinceVsync = 0;
      uint64_t frameCounter = 0;
      if (system->GetTimeSinceLastVsync(&sinceVsync, &frameCounter) && std::isfinite(sinceVsync) &&
          sinceVsync >= 0 && sinceVsync < .1f)
        schedule_.anchor(sampleNow - uint64_t(double(sinceVsync) * 1e9));
      anchorAt = sampleNow + 1000000000ULL;
    }
    // Sample current poses once per tick. The host extrapolates them by its
    // measured latency (one prediction step); with prediction on, the frame
    // timestamp is the target display time, as in ALVR's reference client.
    std::array<vr::TrackedDevicePose_t, vr::k_unMaxTrackedDeviceCount> raw{};
    uint64_t timestamp = sampleNow;
    bool hmdConnected = false;
    if (online) {
      const uint64_t offset = prediction_ ? vrost_prediction_ns(t) : 0;
      const PoseSample sample = planPoseSample(clock_, sampleNow, offset);
      timestamp = sample.timestamp;
      predictionNs = timestamp - sampleNow;
      system->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding,
          sample.secondsFromNow, raw.data(), raw.size());
      hmdConnected = raw[0].bDeviceIsConnected;
      // 1.0.0.5: watch the head pose itself; the stream needs it (see headUntrackedMs).
      const bool headValid = raw[0].bDeviceIsConnected && raw[0].bPoseIsValid;
      const int result = int(raw[0].eTrackingResult);
      headTrackingResult = result;
      if (headValid) headUntrackedSince = 0;
      else if (!headUntrackedSince) headUntrackedSince = std::max<uint64_t>(sampleNow, 1);
      if (!headStateKnown || headValid != headWasValid || result != headLastResult) {
        diagnosticLine(std::string("HEAD_TRACKING tracked=") + (headValid ? "1" : "0") +
                       " connected=" + (raw[0].bDeviceIsConnected ? "1" : "0") +
                       " result=" + std::to_string(result) + " reason=" + headTrackingReason(raw[0]));
        headStateKnown = true;
        headWasValid = headValid;
        headLastResult = result;
      }
    } else {
      headUntrackedSince = 0;
      headStateKnown = false;
    }
    // Controls and proximity follow the streaming session and a connected
    // headset, not momentary head-pose validity or compositor focus.
    const bool active = online && hmdConnected;
    if (!active) {
      previousSample=0;
      for (auto &a : accelerations) a.reset();
      for (auto &d : handDiagnostics) {
        d.skeleton.clear(); d.native.clear();
        d.prediction.clear();
        d.skeletonSent=false;
        d.skeletonReason="inactive";
        d.curlsValid=false;
        d.refresh.reset();
        d.systemLatch.reset();
      }
      skeletonAt=0;
      focusLostAt=0;
    }
    uint64_t epoch=streamEpoch_;
    if (online && (!wasStreaming||epoch!=observedEpoch)) {
      observedEpoch=epoch;
      for (auto &d : handDiagnostics) {
        d.skeleton.clear(); d.native.clear(); d.schemaValid=false; d.schemaRetryAt=0;
        d.prediction.clear();
        d.skeletonSent=false; d.skeletonReason="waiting"; d.curlsValid=false;
        d.refresh.reset();
        d.systemLatch.reset();
      }
      skeletonAt=0;
      for (auto &values : previous)
        std::fill(values.begin(), values.end(),
                  std::numeric_limits<float>::quiet_NaN());
      sentViewRevision=0;
      registerControllerProfiles(paths, {ids[1], ids[2]},
          [&](uint64_t hand, const uint64_t *inputs, uint32_t count) {
            vrost_profile(t, hand, inputs, count);
          });
      diagnosticLine("DEBUG controller profile registered with " +
                     std::to_string(paths[0].size() + paths[1].size()) +
                     " input components covering both hands");
      spaceDirty_ = true;
    }
    if (online) {
      auto snapshot=geometry.snapshot();
      if (snapshot.revision != sentViewRevision) {
        vrost_views(t, snapshot.views.data());
        sentViewRevision=snapshot.revision;
        diagnosticLine("DEBUG Eye geometry sent to host; revision=" + std::to_string(sentViewRevision));
      }
    }
    if (online && spaceDirty_ && sampleNow >= spaceRetryAt) {
      float w=0, h=0;
      if (chaperone && chaperone->GetPlayAreaSize(&w, &h) &&
          std::isfinite(w) && std::isfinite(h) && w>0 && h>0 && w<=100 && h<=100) {
        vrost_playspace(t, w, h);
        diagnosticLine("PLAYSPACE {\"width\":" + std::to_string(w) +
            ",\"depth\":" + std::to_string(h) + ",\"sent\":true}");
        spaceDirty_ = false;
      } else {
        diagnosticLine("PLAYSPACE {\"sent\":false,\"reason\":\"Local room setup unavailable\"}");
        spaceRetryAt = sampleNow + 5000000000ULL;
      }
    }
    if (wasActive && !active)
      release();
    if (active != wasActive)
      vrost_proximity(t, active);
    if (active) {
      // Poses were sampled above (predicted to `timestamp` when enabled).
      // Never forward WaitGetPoses' compositor-predicted poses.
      ++trackingSamples;
      if (previousSample) {
        uint64_t gap=(sampleNow-previousSample)/1000, old=trackingGapMaxUs.load();
        while(gap>old && !trackingGapMaxUs.compare_exchange_weak(old,gap)) {}
      }
      previousSample=sampleNow;
      std::vector<VrostMotion> motions;
      uint32_t indices[] = {0,
                            system->GetTrackedDeviceIndexForControllerRole(
                                vr::TrackedControllerRole_LeftHand),
                            system->GetTrackedDeviceIndexForControllerRole(
                                vr::TrackedControllerRole_RightHand)};
      for (int i = 0; i < 3; ++i) {
        auto d = indices[i];
        if (d >= raw.size() || !raw[d].bPoseIsValid ||
            !raw[d].bDeviceIsConnected)
          continue;
        VrostMotion m{};
        m.id = ids[i];
        m.pose = quaternion(copy(raw[d].mDeviceToAbsoluteTracking));
        std::memcpy(m.linear, raw[d].vVelocity.v, sizeof(m.linear));
        std::memcpy(m.angular, raw[d].vAngularVelocity.v, sizeof(m.angular));
        // 1.0.0.14: the acceleration, folded into the pose and velocity the host extrapolates by its prediction step.
        const VrostMotion measured = m;
        accelerations[i].observe(sampleNow, m.linear, m.angular);
        if (acceleration_ && prediction_ && predictionNs.load()) {
          accelerations[i].apply(m, double(predictionNs.load()) * 1e-9);
          if (i > 0) {
            const uint64_t mm = uint64_t(accelerations[i].magnitude() * 1000.f);
            if (mm > accelerationMaxMm.load()) accelerationMaxMm = mm;
            ++accelerationSamples;
          }
        }
        motions.push_back(m);
        if (i > 0)
          handDiagnostics[i - 1].prediction.add(sampleNow, measured.pose.position, measured.linear, predictionNs,
                                                m.pose.position, m.linear);
        if (i == 2 && motions.size() >= 2 && motions[motions.size() - 2].id == ids[1])
          controllerPair.add(motions[motions.size() - 2].pose.position, motions[motions.size() - 2].linear,
                             m.pose.position, m.linear);
        if (sampleNow > batteryAt) {
          vr::ETrackedPropertyError pe{};
          float level = system->GetFloatTrackedDeviceProperty(
              d, vr::Prop_DeviceBatteryPercentage_Float, &pe);
          if (!pe && std::isfinite(level))
            vrost_battery(t, ids[i], level,
                       system->GetBoolTrackedDeviceProperty(
                           d, vr::Prop_DeviceIsCharging_Bool));
        }
      }
      if (sampleNow > batteryAt)
        batteryAt = sampleNow + 5000000000ULL;

      // Rebase recent local finger bones on THIS packet's unpredicted device
      // pose, then send before action reads/diagnostics can age head tracking.
      // The host matches skeletons by exact timestamp, so each tracking packet
      // includes valid cached bones even though acquisition runs at display rate.
      std::array<skeletal::Joints,2> skeletons{};
      std::array<const VrostPose *,2> skeletonPointers{};
      std::array<const VrostNativeHand *,2> nativePointers{};
      std::array<VrostNativeHand,2> nativeTransport{};
      for (int hand=0;hand<2;++hand) {
        auto &d=handDiagnostics[hand];
        d.skeletonSent=false;
        const auto device=indices[hand+1];
        if (focused_ && device<raw.size() && raw[device].bDeviceIsConnected && raw[device].bPoseIsValid) {
          // Freshness uses acquisition time; bones are rebased on this packet's
          // (predicted) controller pose.
          d.skeletonSent=d.skeleton.sample(device,sampleNow,
              quaternion(copy(raw[device].mDeviceToAbsoluteTracking)),hand!=0,skeletons[hand]);
          if (d.skeletonSent) skeletonPointers[hand]=skeletons[hand].data();
          if (d.native.fresh(device,sampleNow)) {
            nativeTransport[hand]=d.native.transport();
            nativePointers[hand]=&nativeTransport[hand];
          }
          else if (d.skeleton.valid) d.skeletonReason="stale";
        } else {
          d.skeleton.clear(); d.native.clear();
          d.skeletonReason=!focused_ ? "input_unavailable" :
              device<raw.size() && raw[device].bDeviceIsConnected ? "tracking_lost" : "disconnected";
        }
      }
      if (!motions.empty() && motions[0].id == ids[0]) {
        // 1.0.0.14: the gaze turned against the head's motion over the time until the frame is shown (the sample's
        // age plus the host's prediction step), so the sharp region lands where the eyes will be, not where they were.
        const VrostQuat *gazeSample = gaze_.sample(sampleNow);
        VrostQuat predictedGaze{};
        if (gazeSample) {
          const uint64_t age = sampleNow > gaze_.timestamp() ? sampleNow - gaze_.timestamp() : 0;
          const double ahead = double(age + predictionNs.load()) * 1e-9;
          predictedGaze = predictGaze(*gazeSample, motions[0].pose.orientation, motions[0].angular, ahead);
          const float dot = std::fabs(predictedGaze.x * gazeSample->x + predictedGaze.y * gazeSample->y +
                                      predictedGaze.z * gazeSample->z + predictedGaze.w * gazeSample->w);
          const uint64_t turnMrad = uint64_t(2000.0 * std::acos(std::min(1.0f, dot)));
          if (turnMrad > gazeTurnMaxMrad.load()) gazeTurnMaxMrad = turnMrad;
          ++gazePredicted;
          gazeSample = &predictedGaze;
        }
        vrost_tracking_native(t, timestamp, motions.data(), motions.size(),
                    gazeSample, skeletonPointers[0], skeletonPointers[1],
                    nativePointers[0], nativePointers[1]);
        ++headPoses;
      } else {
        // No head pose: nothing is sent, so the host has no frame to send back.
        ++headPosesMissing;
      }

      vr::VRActiveActionSet_t set{};
      set.ulActionSet = actionSet_;
      const auto updateError = input->UpdateActionState(&set, sizeof(set), 1);
      const bool focusedNow = focused_;
      if (focusedNow) {
        if (focusLostAt && sampleNow >= focusLostAt && sampleNow - focusLostAt < focusGraceNs) ++focusBrief;
        focusLostAt = 0;
      } else if (!focusLostAt) {
        focusLostAt = std::max<uint64_t>(sampleNow, 1); ++focusLosses;
      }
      bool valid = updateError == vr::VRInputError_None && focusedNow;
      auto held = [&](const char* name) {
        vr::InputDigitalActionData_t d{};
        return valid && input->GetDigitalActionData(actions_.at(name), &d, sizeof(d),
            handSources_[0])==vr::VRInputError_None && d.bActive && d.bState;
      };
      // The runtime helper enables sendSystemButtonToAllApps and the Index
      // binding exposes the physical system button. Keep alternate controls for
      // runtime/custom-binding combinations which do not expose that action.
      bool chord = held("left_b") && held("left_stick_click");
      if (menuRequested_.exchange(false)) menuUntil=sampleNow+180000000ULL;
      std::vector<VrostButton> buttonUpdates;
      for (int hand = 0; hand < 2; ++hand) {
        std::string prefix = hand ? "right_" : "left_";
        const auto device = indices[hand + 1];
        const bool connected = device < raw.size() && raw[device].bDeviceIsConnected;
        const bool tracked = connected && raw[device].bPoseIsValid;
        auto &diagnostics = handDiagnostics[hand];
        if (diagnostics.tracked && !tracked) ++diagnostics.trackingLosses;
        if (connected != diagnostics.connected || tracked != diagnostics.tracked ||
            device != diagnostics.device) {
          diagnosticLine(std::string("CONTROLLER_STATE hand=") + (hand ? "right" : "left") +
              " device=" + std::to_string(device) + " connected=" + std::to_string(connected) +
              " tracked=" + std::to_string(tracked) + " tracking_result=" +
              std::to_string(device < raw.size() ? int(raw[device].eTrackingResult) : 1));
        }
        diagnostics.device = device;
        diagnostics.connected = connected;
        diagnostics.tracked = tracked;
        diagnostics.trackingResult = device < raw.size() ? int(raw[device].eTrackingResult) : 1;
        const bool available = controllerActionsAvailable(connected, focusedNow,
            updateError == vr::VRInputError_None);
        // 1.0.0.10: a short loss of focus keeps the last values instead of releasing them.
        const bool hold = !available && updateError == vr::VRInputError_None &&
                          holdThroughFocusLoss(connected, focusedNow, focusLostAt, sampleNow);
        const bool fullUpdate = diagnostics.refresh.due(sampleNow, device, available || hold, tracked);
        if (fullUpdate) ++diagnostics.fullUpdates;
        diagnostics.actionsActive = 0;
        diagnostics.systemActive = false;
        if (updateError != vr::VRInputError_None) ++diagnostics.errors;
        for (size_t i = 0; i < paths[hand].size(); ++i) {
          auto m = mappings[i];
          float value = 0;
          bool actionActive = false;
          if (available) {
            auto handle = actions_.at(prefix + m.action);
            if (m.kind) {
              vr::InputAnalogActionData_t d{};
              const auto error = input->GetAnalogActionData(
                  handle, &d, sizeof(d), handSources_[hand]);
              if (error != vr::VRInputError_None) ++diagnostics.errors;
              if (error == vr::VRInputError_None && d.bActive) {
                actionActive = true;
                value = m.axis ? d.y : d.x;
              }
            } else {
              vr::InputDigitalActionData_t d{};
              const auto error = input->GetDigitalActionData(
                  handle, &d, sizeof(d), handSources_[hand]);
              if (error != vr::VRInputError_None) ++diagnostics.errors;
              if (error == vr::VRInputError_None && d.bActive) {
                actionActive = true;
                value = d.bState ? 1 : 0;
              }
            }
          }
          if (actionActive) ++diagnostics.actionsActive;
          const bool systemButton = std::string(m.action)=="remote_menu";
          if (hold && !systemButton && std::isfinite(previous[hand][i]))
            value = previous[hand][i];
          if (systemButton) {
            diagnostics.systemActive = actionActive;
            // 1.0.0.10: one clean press, readable from SteamVR's event while focus hides the action.
            const uint64_t word = systemEvent_[hand].load();
            if (word != diagnostics.systemEventWord) {
              diagnostics.systemEventWord = word;
              diagnostics.systemEvents.at = word >> 1;
              diagnostics.systemEvents.down = (word & 1) != 0;
            }
            bool known = true, pressed = false;
            if (connected)
              diagnostics.systemEvents.reading(sampleNow, available, value != 0, known, pressed);
            const bool wasHeld = diagnostics.systemLatch.held();
            const bool forwarded = diagnostics.systemLatch.update(sampleNow, known, pressed);
            if (forwarded && !wasHeld) diagnostics.systemFocusLost = false;  // a new press
            if ((forwarded || wasHeld) && !focusedNow) diagnostics.systemFocusLost = true;
            if (wasHeld && !forwarded) {
              // focus: whether SteamVR here took input focus during the press; unread=1: for a moment
              // the button could be read neither with focus nor from SteamVR's events (the press may
              // then have reached the host as a tap).
              if (diagnostics.systemFocusLost) ++diagnostics.systemFocusLostPresses;
              diagnosticLine(std::string("SYSTEM_BUTTON hand=") + (hand ? "right" : "left") +
                  " held_ms=" + std::to_string(diagnostics.systemLatch.lastHeldNs() / 1000000) +
                  " focus=" + (diagnostics.systemFocusLost ? "lost" : "kept") +
                  " unread=" + (diagnostics.systemLatch.lastUnseen() ? "1" : "0") +
                  " presses=" + std::to_string(diagnostics.systemLatch.presses()));
            }
            value = remoteMenuValue(forwarded, chord, sampleNow<menuUntil, hand) ? 1.f : 0.f;
          }
          if (hand==0 && chord && (std::string(m.action)=="b" || std::string(m.action)=="stick_click")) value=0;
          if (!std::isfinite(value))
            value = 0;
          if (std::string(m.action)=="trigger") diagnostics.trigger = value;
          if (std::string(m.action)=="stick") {
            if (m.axis) diagnostics.stickY = value;
            else diagnostics.stickX = value;
          }
          if (fullUpdate || !std::isfinite(previous[hand][i]) ||
              std::abs(previous[hand][i] - value) > 1e-4f) {
            if (std::isfinite(previous[hand][i]) &&
                std::abs(previous[hand][i] - value) > 1e-4f) ++diagnostics.changes;
            buttonUpdates.push_back({paths[hand][i], uint32_t(m.kind), value});
            previous[hand][i] = value;
          }
        }
      }
      const auto boneNow=monotonicNs();
      const bool pollBones=boneNow>=skeletonAt;
      if (pollBones) {
        // Carry the fractional display cadence across 4 ms tracking ticks.
        skeletonAt = skeletonAt && boneNow-skeletonAt<skeletonPeriod
            ? skeletonAt+skeletonPeriod : boneNow+skeletonPeriod;
      }
      for (int hand=0;hand<2;++hand) {
        auto &d=handDiagnostics[hand];
        if (!valid || !d.tracked) {
          d.skeleton.clear(); d.native.clear(); d.curlsValid=false;
          d.skeletonReason=!valid ? "input_unavailable" : d.connected ? "tracking_lost" : "disconnected";
          continue;
        }
        if (!pollBones) continue;
        auto failed=[&](const char *reason,vr::EVRInputError error=vr::VRInputError_None) {
          d.skeleton.clear(); d.native.clear(); d.curlsValid=false;
          d.skeletonReason=reason; d.skeletonError=int(error); ++d.skeletonErrors;
        };
        const auto handle=actions_.at(hand ? "right_skeleton" : "left_skeleton");
        vr::InputSkeletalActionData_t action{};
        auto error=input->GetSkeletalActionData(handle,&action,sizeof(action));
        if (error!=vr::VRInputError_None) { failed("action_error",error); continue; }
        if (!action.bActive) { failed("unbound_or_inactive"); continue; }
        const auto device=indices[hand+1];
        if (d.schemaDevice!=device || d.schemaOrigin!=action.activeOrigin) {
          d.schemaDevice=device; d.schemaOrigin=action.activeOrigin;
          d.schemaValid=false; d.schemaRetryAt=0;
        }
        if (!d.schemaValid && boneNow>=d.schemaRetryAt) {
          d.schemaRetryAt=boneNow+1000000000ULL;
          uint32_t count=0;
          std::array<vr::BoneIndex_t,31> hierarchy{};
          vr::InputOriginInfo_t origin{};
          vr::EVRSkeletalTrackingLevel level=vr::VRSkeletalTracking_Estimated;
          if ((error=input->GetBoneCount(handle,&count))!=vr::VRInputError_None ||
              (error=input->GetOriginTrackedDeviceInfo(action.activeOrigin,&origin,sizeof(origin)))!=vr::VRInputError_None ||
              (error=input->GetSkeletalTrackingLevel(handle,&level))!=vr::VRInputError_None) {
            failed("capability_error",error); continue;
          }
          d.skeletonLevel=int(level);
          if (origin.trackedDeviceIndex!=device) { failed("wrong_hand_origin"); continue; }
          if (level<vr::VRSkeletalTracking_Partial || level>vr::VRSkeletalTracking_Full) {
            failed("estimated_only"); continue;
          }
          if (count!=hierarchy.size()) { failed("unsupported_bone_count"); continue; }
          error=input->GetBoneHierarchy(handle,hierarchy.data(),hierarchy.size());
          if (error!=vr::VRInputError_None) { failed("hierarchy_error",error); continue; }
          if (!skeletal::validHierarchy(hierarchy)) { failed("unsupported_hierarchy"); continue; }
          d.schemaValid=true;
        }
        if (!d.schemaValid) continue;
        std::array<vr::VRBoneTransform_t,31> bones{};
        error=input->GetSkeletalBoneData(handle,vr::VRSkeletalTransformSpace_Parent,
            vr::VRSkeletalMotionRange_WithoutController,bones.data(),bones.size());
        if (error!=vr::VRInputError_None) { failed("bone_error",error); continue; }
        skeletal::Bones local{};
        for (size_t i=0;i<bones.size();++i) {
          const auto &bone=bones[i];
          local[i]={{bone.orientation.x,bone.orientation.y,bone.orientation.z,bone.orientation.w},
                    {bone.position.v[0],bone.position.v[1],bone.position.v[2]}};
        }
        if (!d.skeleton.update(local,device,boneNow)) { failed("invalid_bones"); continue; }
        ++d.skeletonSamples; d.skeletonReason="ready"; d.skeletonError=0;
        // Read source curls at the SAME cadence as bones. These are the physical
        // controller's named channels, not a quaternion-derived approximation.
        vr::VRSkeletalSummaryData_t summary{};
        d.curlsValid=input->GetSkeletalSummaryData(handle,vr::VRSummaryType_FromDevice,&summary)==vr::VRInputError_None;
        for (size_t i=0;i<d.curls.size();++i) {
          const float curl=summary.flFingerCurl[i];
          d.curlsValid=d.curlsValid && std::isfinite(curl) && curl>=0 && curl<=1;
          d.curls[i]=std::isfinite(curl) ? std::clamp(curl,0.f,1.f) : 0;
        }
        // Native transport preserves the constrained range and all five
        // auxiliary bones. If unavailable, keep the proven legacy bones.
        std::array<vr::VRBoneTransform_t,31> constrained{};
        error=input->GetSkeletalBoneData(handle,vr::VRSkeletalTransformSpace_Parent,
            vr::VRSkeletalMotionRange_WithController,constrained.data(),constrained.size());
        d.native.clear();
        if (error==vr::VRInputError_None) {
          skeletal::Bones localConstrained{};
          for (size_t i=0;i<constrained.size();++i) {
            const auto &bone=constrained[i];
            localConstrained[i]={{bone.orientation.x,bone.orientation.y,bone.orientation.z,bone.orientation.w},
                         {bone.position.v[0],bone.position.v[1],bone.position.v[2]}};
          }
          if (d.native.update({localConstrained,local},d.curls,d.curlsValid,device,boneNow))
            d.native.setTransportTime(prediction_ ? timestamp : boneNow);
        }

      }
      if (!buttonUpdates.empty())
        vrost_buttons(t, buttonUpdates.data(), buttonUpdates.size());
      if (sampleNow >= inputReportAt) {
        std::ostringstream line;
        const auto pair = controllerPair.take();
        line << "INPUT {\"focus\":" << (focused_ ? "true" : "false")
             << ",\"focus_losses\":" << focusLosses << ",\"focus_brief\":" << focusBrief
             << ",\"system_events\":" << systemEvents_.load()
             << ",\"pair\":{\"still_samples\":" << pair.stillSamples << ",\"mean_mm\":" << pair.meanMm
             << ",\"min_mm\":" << pair.minMm << ",\"max_mm\":" << pair.maxMm << "}"
             << ",\"update_error\":" << int(updateError)
             << ",\"profile_inputs\":" << paths[0].size() + paths[1].size();
        {
          std::lock_guard<std::mutex> l(hapticMutex_);
          line << ",\"haptics\":{\"received\":" << haptics_.received
               << ",\"submitted\":" << haptics_.sent << ",\"merged\":" << haptics_.merged
               << ",\"expired\":" << haptics_.expired << ",\"replaced\":" << haptics_.replaced << "}";
        }
        for (int hand = 0; hand < 2; ++hand) {
          const auto &d = handDiagnostics[hand];
          line << (hand ? ",\"right\":{" : ",\"left\":{")
               << "\"connected\":" << (d.connected ? "true" : "false")
               << ",\"tracked\":" << (d.tracked ? "true" : "false")
               << ",\"device_index\":" << d.device
               << ",\"tracking_result\":" << d.trackingResult
               << ",\"tracking_losses\":" << d.trackingLosses
               << ",\"full_updates\":" << d.fullUpdates
               << ",\"haptic_errors\":" << d.hapticErrors
               << ",\"actions_active\":" << d.actionsActive
               << ",\"errors\":" << d.errors << ",\"changes\":" << d.changes
               << ",\"trigger\":" << d.trigger << ",\"stick_x\":" << d.stickX
               << ",\"stick_y\":" << d.stickY
               << ",\"system_active\":" << (d.systemActive ? "true" : "false")
               << ",\"system_presses\":" << d.systemLatch.presses()
               << ",\"system_focus_lost\":" << d.systemFocusLostPresses
               << ",\"system_unread\":" << d.systemLatch.unseenEnds()
               << ",\"skeleton_sent\":" << (d.skeletonSent ? "true" : "false")
               << ",\"skeleton_samples\":" << d.skeletonSamples
               << ",\"skeleton_errors\":" << d.skeletonErrors
               << ",\"skeleton_error\":" << d.skeletonError
               << ",\"skeleton_level\":" << d.skeletonLevel
               << ",\"skeleton_reason\":\"" << d.skeletonReason << "\""
               << ",\"skeleton_range\":\"" << (d.native.valid ? "both_native" : "without_controller") << "\""
               << ",\"finger_transport\":\"" << (d.native.valid ? "native_openvr_v1" : "legacy_alvr") << "\""
               << ",\"skeleton_age_ms\":" << (d.skeleton.valid ? double(monotonicNs()-d.skeleton.sampledAt)/1000000.0 : -1.0)
               << ",\"finger_sample_ns\":\"" << d.native.data.sampled_at << "\""
               << ",\"finger_curls_valid\":" << (d.curlsValid ? "true" : "false")
               << ",\"finger_curls\":[";
          for (size_t i=0;i<d.curls.size();++i) line << (i ? "," : "") << d.curls[i];
          line << "]";
          const auto check = handDiagnostics[hand].prediction.take();
          line << ",\"prediction\":{\"samples\":" << check.samples
               << ",\"horizon_ms\":" << check.horizonMs << ",\"mean_mm\":" << check.meanMm
               << ",\"p95_mm\":" << check.p95Mm << ",\"max_mm\":" << check.maxMm
               << ",\"still_samples\":" << check.stillSamples << ",\"still_max_mm\":" << check.stillMaxMm << "}}";
        }
        line << "}";
        diagnosticLine(line.str());
        inputReportAt = sampleNow + 1000000000ULL;
      }
    }
    std::array<std::optional<HapticCommand>,2> commands;
    {
      std::lock_guard<std::mutex> l(hapticMutex_);
      for (int hand=0; hand<2; ++hand)
        commands[hand] = haptics_.poll(hand, monotonicNs(),
            active && focused_ && handDiagnostics[hand].connected);
    }
    for (int hand=0; hand<2; ++hand) {
      if (!commands[hand]) continue;
      const auto& command = *commands[hand];
      if (input->TriggerHapticVibrationAction(
          actions_.at(hand ? "right_haptic" : "left_haptic"), 0,
          command.duration, command.frequency, command.amplitude,
          handSources_[hand]) != vr::VRInputError_None)
        ++handDiagnostics[hand].hapticErrors;
    }
    wasStreaming = online;
    wasActive = active;
    const uint64_t wake = schedule_.next(monotonicNs());
#ifdef _WIN32
    windows::sleepUntil(wake); // a high-resolution timer: sleep_until there wakes in whole milliseconds
#else
    std::this_thread::sleep_until(std::chrono::steady_clock::time_point(std::chrono::nanoseconds(wake)));
#endif
  }
  release();
  vrost_proximity(t, 0);
}
void OpenVRDevice::close() {
  stopTracking();
  if (shutdown_) {
    shutdown_();
    shutdown_ = nullptr;
  }
  if (loader_) {
#ifdef _WIN32
    closeLibrary(loader_);
#else
    dlclose(loader_);
#endif
    loader_ = nullptr;
  }
  system = nullptr;
  compositor = nullptr;
  input = nullptr;
  overlay = nullptr;
  chaperone = nullptr;
}
OpenVRDevice::~OpenVRDevice() { close(); }
} // namespace vrost
