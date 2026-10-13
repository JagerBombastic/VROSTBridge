#pragma once
#include <algorithm>
#include <cstdint>

namespace vrost {
struct RuntimeTickTimings {
  uint64_t eventPollNs=0, geometryNs=0, waitPosesNs=0, inputFocusNs=0, frameQueryNs=0;
  uint32_t events=0, exitedProcesses=0, frameIndex=0, dropped=0, mispresented=0, reprojectionFlags=0;
  bool eventBudgetExhausted=false, geometryAttempted=false, frameTimingValid=false;
  double compositorTime=0;
};
// Leave remaining events queued for the next frame. A noisy overlay or a batch
// of property changes must not monopolize a presentation tick.
inline bool runtimeEventBudgetAvailable(uint32_t events, uint64_t elapsedNs) {
  return events < 64 && elapsedNs < 500000;
}
}
