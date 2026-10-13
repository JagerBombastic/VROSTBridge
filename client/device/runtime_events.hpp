#pragma once
#include "openvr.h"
namespace vrost {
// ProcessQuit is a broadcast lifecycle notification (for example vrstartup
// exiting after launch). Only Quit asks this application to terminate.
inline bool runtimeRequestsExit(uint32_t event) {
  return event == vr::VREvent_Quit;
}
inline bool runtimeChangesGeometry(uint32_t event) {
  return event == vr::VREvent_IpdChanged || event == vr::VREvent_LensDistortionChanged;
}
}
