#pragma once
// Re-aiming a received frame to the head pose SteamVR will display it with.
//
// The host renders each frame for a predicted head pose (the frame's source pose).
// By the time the frame reaches this PC's display, the head has moved on, and the
// prediction is never exact: it was made from a pose sampled tens of milliseconds
// earlier. Before handing the frame to SteamVR, the client can rotate it from the
// source pose to the pose SteamVR predicted for this display refresh (the one
// WaitGetPoses returned). Because only the view direction changes, the mapping
// between the two images of each eye is a plane projective transform (homography):
//
//   source_uv ~ H * target_uv,   H = B * E^T * S^T * T * E * A
//
// A maps a target texture coordinate (u right, v down, both 0..1 across one eye)
// to a view ray in that eye; E rotates eye rays into the head frame; T and S are
// the target and source head rotations (tracking space); B projects a source eye
// ray back to texture coordinates. Eye translation (parallax) is not corrected:
// that needs depth, which the stream does not carry.
//
// Conventions (OpenVR / ALVR): +X right, +Y up, -Z forward. An eye's field of view
// is given by signed angles left < 0 < right and down < 0 < up. Texture row 0 is
// the top of the view (+Y). See client/device/openvr_projection.hpp for how OpenVR's
// reversed raw "top"/"bottom" names map onto up/down.
#include "core/stream.hpp"
#include <array>
#include <cmath>

namespace vrost {

struct Mat3 {
  float m[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
};

inline Mat3 mul(const Mat3 &a, const Mat3 &b) {
  Mat3 r{};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) {
      r.m[i][j] = 0;
      for (int k = 0; k < 3; ++k) r.m[i][j] += a.m[i][k] * b.m[k][j];
    }
  return r;
}

inline Mat3 transpose(const Mat3 &a) {
  Mat3 r{};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) r.m[i][j] = a.m[j][i];
  return r;
}

inline Mat3 rotationOf(const Pose &p) {
  Mat3 r{};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) r.m[i][j] = p.m[i][j];
  return r;
}

// Tangents of an eye's field-of-view angles.
struct EyeTangents {
  float left = -1, right = 1, up = 1, down = -1;
};

inline EyeTangents tangents(const VrostView &view) {
  return {std::tan(view.left), std::tan(view.right), std::tan(view.up), std::tan(view.down)};
}

// Texture coordinate -> eye ray (not normalised; z = -1).
inline Mat3 rayFromTexture(const EyeTangents &t) {
  Mat3 a{};
  a.m[0][0] = t.right - t.left; a.m[0][1] = 0;               a.m[0][2] = t.left;
  a.m[1][0] = 0;                a.m[1][1] = -(t.up - t.down); a.m[1][2] = t.up;
  a.m[2][0] = 0;                a.m[2][1] = 0;               a.m[2][2] = -1;
  return a;
}

// Eye ray -> homogeneous texture coordinate (w = -z, positive in front of the eye).
inline Mat3 textureFromRay(const EyeTangents &t) {
  const float width = t.right - t.left, height = t.up - t.down;
  Mat3 b{};
  b.m[0][0] = 1 / width; b.m[0][1] = 0;           b.m[0][2] = t.left / width;
  b.m[1][0] = 0;         b.m[1][1] = -1 / height; b.m[1][2] = -t.up / height;
  b.m[2][0] = 0;         b.m[2][1] = 0;           b.m[2][2] = -1;
  return b;
}

// Maps texture coordinates of the image wanted at `target` to the coordinates of
// the same view ray in the image rendered at `source`, for one eye.
inline Mat3 reaimHomography(const Pose &source, const Pose &target, const VrostView &eye) {
  const Mat3 e = rotationOf(matrix(eye.pose));
  const EyeTangents t = tangents(eye);
  const Mat3 rotation = mul(mul(transpose(e), transpose(rotationOf(source))), mul(rotationOf(target), e));
  return mul(mul(textureFromRay(t), rotation), rayFromTexture(t));
}

// Applies a homography to (u, v). False when the ray is behind the source eye.
inline bool applyHomography(const Mat3 &h, float u, float v, float &su, float &sv) {
  const float x = h.m[0][0] * u + h.m[0][1] * v + h.m[0][2];
  const float y = h.m[1][0] * u + h.m[1][1] * v + h.m[1][2];
  const float w = h.m[2][0] * u + h.m[2][1] * v + h.m[2][2];
  if (!(w > 1e-6f)) return false;
  su = x / w;
  sv = y / w;
  return std::isfinite(su) && std::isfinite(sv);
}

// Angle between two head orientations, in degrees.
inline float rotationDegrees(const Pose &a, const Pose &b) {
  const Mat3 r = mul(transpose(rotationOf(a)), rotationOf(b));
  const float c = std::max(-1.f, std::min(1.f, (r.m[0][0] + r.m[1][1] + r.m[2][2] - 1) * .5f));
  return std::acos(c) * 57.29577951f;
}

// Head pose rotated by `degrees` about its own up axis (positive turns left).
// The frame alignment check submits frames labelled with such a pose: if SteamVR
// re-aims frames itself, the picture visibly jumps by that angle.
inline Pose yawed(const Pose &head, float degrees) {
  const float a = degrees * 0.01745329252f, c = std::cos(a), s = std::sin(a);
  Pose yaw{};
  yaw.m[0][0] = c;  yaw.m[0][2] = s;
  yaw.m[2][0] = -s; yaw.m[2][2] = c;
  Pose r = compose(head, yaw);
  for (int i = 0; i < 3; ++i) r.m[i][3] = head.m[i][3];
  return r;
}

// Push constants for client/platform/linux/reaim.comp: one homography per eye, as
// three vec4 rows each (std430 keeps vec4 arrays tightly packed).
struct ReaimPushConstants {
  float rows[2][3][4];
};

inline ReaimPushConstants reaimConstants(const Pose &source, const Pose &target,
                                         const std::array<VrostView, 2> &eyes) {
  ReaimPushConstants c{};
  for (int e = 0; e < 2; ++e) {
    const Mat3 h = reaimHomography(source, target, eyes[e]);
    for (int i = 0; i < 3; ++i) {
      for (int j = 0; j < 3; ++j) c.rows[e][i][j] = h.m[i][j];
      c.rows[e][i][3] = 0;
    }
  }
  return c;
}

} // namespace vrost
