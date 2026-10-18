// VROSTBridge 1.0.0.14: the gaze predicted through the head's turn (client/device/gaze_prediction.hpp).
#include "client/device/gaze_prediction.hpp"
#include <cmath>
#include <cstdio>

namespace {
int failures = 0;
void check(bool ok, const char *what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL %s\n", what);
    ++failures;
  }
}
// The gaze direction: the head-relative orientation applied to -Z.
void direction(const VrostQuat &q, float out[3]) {
  out[0] = -(2 * (q.x * q.z + q.y * q.w));
  out[1] = -(2 * (q.y * q.z - q.x * q.w));
  out[2] = -(1 - 2 * (q.x * q.x + q.y * q.y));
}
VrostQuat aboutY(float radians) { return {0, std::sin(radians / 2), 0, std::cos(radians / 2)}; }
bool near(float a, float b, float tolerance = 2e-3f) { return std::fabs(a - b) < tolerance; }
constexpr float kHalfPi = 1.57079632679f;  // M_PI is not in MSVC's <cmath> without _USE_MATH_DEFINES
}  // namespace

int main() {
  using vrost::predictGaze;
  const VrostQuat ahead{0, 0, 0, 1};
  {
    // The head turns left at 1 rad/s and the eyes stay on a point straight ahead: 100 ms later that point is
    // 0.1 rad to the right of the nose, so the predicted gaze points right by 0.1 rad.
    const float angular[3] = {0, 1, 0};
    float d[3];
    direction(predictGaze(ahead, ahead, angular, 0.1), d);
    check(near(d[0], std::sin(0.1f)) && near(d[2], -std::cos(0.1f)) && near(d[1], 0), "a left turn moves the gaze right");
    direction(predictGaze(ahead, ahead, angular, 0.0), d);
    check(near(d[0], 0) && near(d[2], -1), "no time ahead, no change");
    const float still[3] = {0, 0, 0};
    direction(predictGaze(ahead, ahead, still, 0.1), d);
    check(near(d[0], 0) && near(d[2], -1), "a still head leaves the gaze alone");
  }
  {
    // The head's own orientation does not matter when the angular velocity is about its own up axis: the same turn
    // from a head already yawed 90 degrees gives the same head-relative result.
    const float angular[3] = {0, 1, 0};
    float d[3];
    direction(predictGaze(ahead, aboutY(kHalfPi), angular, 0.1), d);
    check(near(d[0], std::sin(0.1f)) && near(d[2], -std::cos(0.1f)), "the turn is taken in the head's frame");
    // But a world-frame angular velocity about X seen from a head yawed 90 degrees is a roll for that head, which
    // leaves a straight-ahead gaze pointing straight ahead.
    const float aboutX[3] = {1, 0, 0};
    direction(predictGaze(ahead, aboutY(kHalfPi), aboutX, 0.1), d);
    check(near(d[0], 0, 5e-3f) && near(d[2], -1, 5e-3f), "a roll about the gaze axis does not move it");
    // The same world-frame X rotation on an unturned head is a pitch: looking up then means the point drops.
    direction(predictGaze(ahead, ahead, aboutX, 0.1), d);
    check(near(d[1], -std::sin(0.1f)) && near(d[2], -std::cos(0.1f)), "a pitch up moves the gaze down");
  }
  {
    // A gaze already off to the side is turned the same amount further.
    const float angular[3] = {0, 2, 0};
    const VrostQuat right = aboutY(-0.2f); // looking 0.2 rad to the right
    float before[3], after[3];
    direction(right, before);
    direction(predictGaze(right, ahead, angular, 0.05), after);
    check(near(after[0], std::sin(0.3f)) && near(after[2], -std::cos(0.3f)), "the turn adds to an off-centre gaze");
    check(before[0] > 0, "the fixture looks right");
  }
  {
    // Bounded: a glitching angular velocity cannot swing the region across the view.
    const float wild[3] = {0, 200, 0};
    float d[3];
    direction(predictGaze(ahead, ahead, wild, 0.1), d);
    check(near(d[0], std::sin(vrost::kGazePredictionMaxRadians)) && near(d[2], -std::cos(vrost::kGazePredictionMaxRadians)),
          "the turn is capped at about 20 degrees");
    const float broken[3] = {NAN, 0, 0};
    direction(predictGaze(ahead, ahead, broken, 0.1), d);
    check(near(d[2], -1), "a broken angular velocity changes nothing");
  }
  if (failures) {
    std::fprintf(stderr, "%d failure(s)\n", failures);
    return 1;
  }
  std::printf("gaze prediction contracts passed\n");
  return 0;
}
