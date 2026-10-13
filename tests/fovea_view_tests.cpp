// RC4.3 eye-tracking panel geometry (client/device/fovea_view.hpp): the gaze point in each
// eye's image follows the host's projection (patch 0010), and FOVEA lines carry the region
// centres of the frame being shown.
#include "client/device/fovea_view.hpp"
#include <cmath>
#include <cstdio>
#include <string>

static int failures = 0;
#define CHECK(c, ...) do { if (!(c)) { std::printf("FAIL %s:%d %s ", __FILE__, __LINE__, #c); std::printf(__VA_ARGS__); std::printf("\n"); ++failures; } } while (0)

static VrostQuat yawPitch(float yawDegrees, float pitchDegrees) {
  // Yaw about +Y (positive turns left), then pitch about +X (positive looks up).
  const float y = yawDegrees * 0.00872664626f, p = pitchDegrees * 0.00872664626f;
  const float cy = std::cos(y), sy = std::sin(y), cp = std::cos(p), sp = std::sin(p);
  return {cy * sp, sy * cp, -sy * sp, cy * cp};
}

int main() {
  VrostView eye{};
  eye.pose.orientation = {0, 0, 0, 1};
  eye.left = -0.87f; eye.right = 0.79f; eye.up = 0.84f; eye.down = -0.92f; // an asymmetric Beyond-like eye
  float u = 0, v = 0;
  CHECK(vrost::gazeInEye({0, 0, 0, 1}, eye, u, v), "straight ahead is in view");
  const float expectedU = -std::tan(eye.left) / (std::tan(eye.right) - std::tan(eye.left));
  const float expectedV = std::tan(eye.up) / (std::tan(eye.up) - std::tan(eye.down));
  CHECK(std::fabs(u - expectedU) < 1e-5f && std::fabs(v - expectedV) < 1e-5f, "straight ahead at %f,%f (expected %f,%f)", u, v,
        expectedU, expectedV);
  float ur = 0, vr = 0;
  CHECK(vrost::gazeInEye(yawPitch(-10, 0), eye, ur, vr) && ur > u + .05f && std::fabs(vr - v) < 1e-4f, "looking right moves right: %f", ur);
  float uu = 0, vu = 0;
  CHECK(vrost::gazeInEye(yawPitch(0, 10), eye, uu, vu) && vu < v - .05f, "looking up moves up: %f", vu);
  CHECK(!vrost::gazeInEye(yawPitch(180, 0), eye, u, v), "looking backwards is not in view");
  float ue = 0, ve = 0;
  CHECK(vrost::gazeInEye(yawPitch(-70, 0), eye, ue, ve) && ue == 1.f, "outside the view clamps to the edge: %f", ue);
  // A canted eye (rotated 5 degrees outwards to the left): the same gaze lands further right in it.
  VrostView canted = eye;
  canted.pose.orientation = {0, std::sin(2.5f * 0.0174533f), 0, std::cos(2.5f * 0.0174533f)};
  float uc = 0, vc = 0;
  CHECK(vrost::gazeInEye({0, 0, 0, 1}, canted, uc, vc) && uc > expectedU + .02f, "canted eye: %f", uc);
  // Region centres: ALVR's region spans [c0 (1 + s), 1 - c0 (1 - s)], c0 = (1 - size) / 2.
  CHECK(std::fabs(vrost::foveationRegionCentre(.4f, 0.f, false) - .5f) < 1e-6f, "centred");
  CHECK(std::fabs(vrost::foveationRegionCentre(.4f, 1.f, false) - .8f) < 1e-6f, "far right");
  CHECK(std::fabs(vrost::foveationRegionCentre(.4f, 1.f, true) - .2f) < 1e-6f, "mirrored right eye");
  vrost::ConversionPushConstants c{};
  c.fovSize[0] = .4f; c.fovSize[1] = .35f; c.fovSize[2] = 4.f; c.fovSize[3] = 4.f;
  c.fovShifts[0] = .5f; c.fovShifts[1] = -.2f; c.fovShifts[2] = -.5f; c.fovShifts[3] = -.2f;
  std::array<VrostView, 2> views{eye, eye};
  const std::string plain = vrost::foveaLine(c, 1408, 1408, 2560, 2560, nullptr, views, 0);
  CHECK(plain == "FOVEA region=0.400,0.350 edge=4.00,4.00 left=0.650,0.435 right=0.650,0.435 encoded=1408x1408 eye=2560x2560",
        "%s", plain.c_str());
  const VrostQuat ahead{0, 0, 0, 1};
  const std::string gazed = vrost::foveaLine(c, 1408, 1408, 2560, 2560, &ahead, views, 7);
  CHECK(gazed.rfind(plain + " gaze_left=0.540,0.459 gaze_right=0.540,0.459 gaze_age_ms=7", 0) == 0, "%s", gazed.c_str());
  if (failures) return 1;
  std::printf("eye-tracking panel geometry contracts passed\n");
  return 0;
}
