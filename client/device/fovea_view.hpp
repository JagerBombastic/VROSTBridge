#pragma once
// RC4.3 eye-tracking panel (local only): where each eye looks in its own image and where the
// full-resolution region of the frame being shown lies. The native client prints these as
// FOVEA lines ten times a second while a foveated stream plays; the companion keeps only the
// latest in memory for its pages and never logs or reports them.
//
// The gaze projection mirrors the host's eye_image_position (patch 0010): the head-relative
// gaze orientation turns -Z into a direction, the eye's view rotation brings it into the eye,
// and the eye's field-of-view tangents give the image position (u right, v down, 0..1).
#include "client/device/conversion.hpp"
#include "core/stream.hpp"
#include <cmath>
#include <cstdio>
#include <string>

namespace vrost {

inline bool gazeInEye(const VrostQuat &gaze, const VrostView &eye, float &u, float &v) {
  const Pose g = matrix(VrostPose{gaze, {0, 0, 0}});
  const float d[3] = {-g.m[0][2], -g.m[1][2], -g.m[2][2]}; // gaze * -Z
  const Pose e = matrix(VrostPose{eye.pose.orientation, {0, 0, 0}});
  // Inverse (transpose) of the eye rotation.
  const float x = e.m[0][0] * d[0] + e.m[1][0] * d[1] + e.m[2][0] * d[2];
  const float y = e.m[0][1] * d[0] + e.m[1][1] * d[1] + e.m[2][1] * d[2];
  const float z = e.m[0][2] * d[0] + e.m[1][2] * d[1] + e.m[2][2] * d[2];
  if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) || z > -1e-3f) return false;
  const float tx = x / -z, ty = y / -z;
  const float left = std::tan(eye.left), right = std::tan(eye.right), up = std::tan(eye.up), down = std::tan(eye.down);
  if (!(right > left && up > down)) return false;
  u = (tx - left) / (right - left);
  v = (up - ty) / (up - down);
  if (!std::isfinite(u) || !std::isfinite(v)) return false;
  u = std::clamp(u, 0.f, 1.f);
  v = std::clamp(v, 0.f, 1.f);
  return true;
}

// One FOVEA line: the frame's full-resolution region (size and centre per eye, from its
// conversion constants), the edge reduction, the stream sizes and, when a fresh gaze sample
// exists, the gaze point in each eye.
inline std::string foveaLine(const ConversionPushConstants &c, uint32_t encodedEyeWidth, uint32_t encodedHeight,
                             uint32_t eyeWidth, uint32_t eyeHeight, const VrostQuat *gaze,
                             const std::array<VrostView, 2> &views, uint64_t gazeAgeMs) {
  char buffer[512];
  const float lx = foveationRegionCentre(c.fovSize[0], c.fovShifts[0], false);
  const float ly = foveationRegionCentre(c.fovSize[1], c.fovShifts[1], false);
  const float rx = foveationRegionCentre(c.fovSize[0], c.fovShifts[2], true);
  const float ry = foveationRegionCentre(c.fovSize[1], c.fovShifts[3], false);
  int n = std::snprintf(buffer, sizeof(buffer),
                        "FOVEA region=%.3f,%.3f edge=%.2f,%.2f left=%.3f,%.3f right=%.3f,%.3f encoded=%ux%u eye=%ux%u",
                        c.fovSize[0], c.fovSize[1], c.fovSize[2], c.fovSize[3], lx, ly, rx, ry, encodedEyeWidth,
                        encodedHeight, eyeWidth, eyeHeight);
  std::string line(buffer, size_t(std::max(0, std::min(n, int(sizeof(buffer)) - 1))));
  float u[2], v[2];
  if (gaze && gazeInEye(*gaze, views[0], u[0], v[0]) && gazeInEye(*gaze, views[1], u[1], v[1])) {
    n = std::snprintf(buffer, sizeof(buffer), " gaze_left=%.3f,%.3f gaze_right=%.3f,%.3f gaze_age_ms=%llu", u[0], v[0],
                      u[1], v[1], static_cast<unsigned long long>(gazeAgeMs));
    line.append(buffer, size_t(std::max(0, std::min(n, int(sizeof(buffer)) - 1))));
  }
  return line;
}

} // namespace vrost
