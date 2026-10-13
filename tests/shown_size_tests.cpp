// 1.0.0.5: the size each eye is shown at (client/device/conversion.hpp shownEyeSize). A stream
// larger than SteamVR's recommended render size is shown at that size, keeping its aspect
// ratio; smaller streams are never enlarged.
#include "client/device/conversion.hpp"

#include <cstdio>

namespace {
int failures = 0;
void check(bool ok, const char *what) {
  if (!ok) {
    ++failures;
    std::printf("FAIL %s\n", what);
  }
}
bool is(vrost::EyeSize size, uint32_t width, uint32_t height) { return size.width == width && size.height == height; }
}  // namespace

int main() {
  using vrost::shownEyeSize;
  // Ultra on a Beyond 2e whose SteamVR recommends 2712 x 2712: shown at 2712.
  check(is(shownEyeSize(3200, 3200, 2712, 2712), 2712, 2712), "Ultra is shown at the recommended size");
  // High (2560) and the recommended size itself are shown as streamed.
  check(is(shownEyeSize(2560, 2560, 2712, 2712), 2560, 2560), "a smaller stream is not enlarged");
  check(is(shownEyeSize(2712, 2712, 2712, 2712), 2712, 2712), "the recommended size is unchanged");
  // Other headsets: the aspect ratio is kept and the tighter axis decides.
  check(is(shownEyeSize(3200, 3000, 2712, 2712), 2712, 2543), "aspect kept, width limits");
  check(is(shownEyeSize(2400, 2800, 2468, 2740), 2349, 2740), "aspect kept, height limits");
  const auto other = shownEyeSize(2144, 2368, 2016, 2240);
  check(is(other, 2016, 2227) && other.height <= 2240, "never above the cap on either axis");
  // No cap (unknown recommendation) or no stream size leaves it alone.
  check(is(shownEyeSize(3200, 3200, 0, 0), 3200, 3200), "no cap");
  check(is(shownEyeSize(0, 0, 2712, 2712), 0, 0), "no stream");
  // A tiny recommendation still gives a usable image.
  check(is(shownEyeSize(3200, 3200, 8, 8), 8, 8), "capped at the recommendation even when tiny");
  // 1.0.0.11: the companion's scale while this PC's graphics fall behind (90 % and 80 % of 2560: 2304, 2048).
  using vrost::scaledEyeSize;
  check(is(scaledEyeSize({2560, 2560}, 90), 2304, 2304) && is(scaledEyeSize({2560, 2560}, 80), 2048, 2048),
        "90 and 80 percent of 2560");
  check(is(scaledEyeSize({2016, 2240}, 90), 1814, 2016), "both sides scaled alike, rounded");
  check(is(scaledEyeSize({2560, 2560}, 100), 2560, 2560) && is(scaledEyeSize({2560, 2560}, 120), 2560, 2560),
        "100 percent or more leaves the size");
  check(is(scaledEyeSize({2560, 2560}, 10), 1280, 1280), "never below half");
  check(is(scaledEyeSize({20, 20}, 50), 16, 16) && is(scaledEyeSize({0, 0}, 80), 0, 0), "small and empty sizes");
  if (failures) return 1;
  std::printf("shown eye size: 14 checks passed\n");
  return 0;
}
