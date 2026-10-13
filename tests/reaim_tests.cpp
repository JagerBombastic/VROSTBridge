// Contracts for core/reaim.hpp: the rotation-only re-aim homography maps each texture
// coordinate of the image wanted at the display pose onto the coordinate of the same
// world direction in the image the host rendered at the frame's source pose.
#include "core/reaim.hpp"
#include <cstdio>
#include <random>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL %s:%d: %s ", __FILE__, __LINE__, #cond);                                   \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

vrost::Pose fromAxisAngle(float x, float y, float z, float degrees) {
  const float n = std::sqrt(x * x + y * y + z * z);
  const float a = degrees * 0.01745329252f * .5f, s = std::sin(a) / n;
  VrostPose p{};
  p.orientation = {x * s, y * s, z * s, std::cos(a)};
  return vrost::matrix(p);
}

vrost::Pose combine(const vrost::Pose &a, const vrost::Pose &b) { return vrost::compose(a, b); }

VrostView eyeView(float left, float right, float up, float down, const vrost::Pose &eyeToHead) {
  VrostView v{};
  v.pose = vrost::quaternion(eyeToHead);
  v.left = left; v.right = right; v.up = up; v.down = down;
  return v;
}

// Projects a world direction into an eye's texture (u right, v down).
bool project(const vrost::Pose &head, const VrostView &eye, vrost::Vec3 world, float &u, float &v) {
  const vrost::Pose eyeWorld = vrost::compose(head, vrost::matrix(eye.pose));
  // eye ray = R_eye^T * world
  vrost::Vec3 d{eyeWorld.m[0][0] * world.x + eyeWorld.m[1][0] * world.y + eyeWorld.m[2][0] * world.z,
             eyeWorld.m[0][1] * world.x + eyeWorld.m[1][1] * world.y + eyeWorld.m[2][1] * world.z,
             eyeWorld.m[0][2] * world.x + eyeWorld.m[1][2] * world.y + eyeWorld.m[2][2] * world.z};
  if (d.z >= -1e-4f) return false;
  const auto t = vrost::tangents(eye);
  const float x = d.x / -d.z, y = d.y / -d.z;
  u = (x - t.left) / (t.right - t.left);
  v = (t.up - y) / (t.up - t.down);
  return true;
}

void identity() {
  const auto eye = eyeView(-.96f, .74f, .77f, -.92f, vrost::Pose{});
  const auto head = fromAxisAngle(.2f, 1, -.3f, 37);
  const auto h = vrost::reaimHomography(head, head, eye);
  for (float u : {0.f, .25f, .5f, .9f})
    for (float v : {0.f, .4f, 1.f}) {
      float su = -1, sv = -1;
      CHECK(vrost::applyHomography(h, u, v, su, sv), "identity applies");
      CHECK(std::abs(su - u) < 1e-5f && std::abs(sv - v) < 1e-5f, "u=%f v=%f -> %f %f", u, v, su, sv);
    }
}

void worldConsistency() {
  std::mt19937 rng(7);
  std::uniform_real_distribution<float> axis(-1, 1), angle(-60, 60), small(-12, 12), cant(-6, 6);
  int compared = 0;
  for (int trial = 0; trial < 400; ++trial) {
    const auto source = fromAxisAngle(axis(rng), axis(rng), axis(rng), angle(rng));
    const auto delta = fromAxisAngle(axis(rng), axis(rng), axis(rng), small(rng));
    const auto target = combine(source, delta);
    // Asymmetric fields of view like the Beyond's, and canted eye displays like the Index's.
    const auto eyeToHead = fromAxisAngle(0, 1, 0, cant(rng));
    const auto eye = eyeView(-.96f, .74f, .77f, -.92f, eyeToHead);
    const auto h = vrost::reaimHomography(source, target, eye);
    for (int sample = 0; sample < 8; ++sample) {
      vrost::Vec3 world{axis(rng), axis(rng), axis(rng)};
      float tu = 0, tv = 0, su = 0, sv = 0, hu = -1, hv = -1;
      if (!project(target, eye, world, tu, tv) || !project(source, eye, world, su, sv)) continue;
      if (tu < -.5f || tu > 1.5f || tv < -.5f || tv > 1.5f) continue;
      CHECK(vrost::applyHomography(h, tu, tv, hu, hv), "visible direction applies");
      CHECK(std::abs(hu - su) < 2e-4f && std::abs(hv - sv) < 2e-4f,
            "trial %d: target (%f, %f) -> (%f, %f), expected (%f, %f)", trial, tu, tv, hu, hv, su, sv);
      ++compared;
    }
  }
  CHECK(compared > 500, "only %d samples compared", compared);
}

void directions() {
  const auto eye = eyeView(-1, 1, 1, -1, vrost::Pose{});
  const vrost::Pose source{};
  // Turning the head left moves a fixed object to the right of the picture.
  const auto left = vrost::yawed(source, 5);
  float u, v, su, sv;
  CHECK(project(left, eye, {0, 0, -1}, u, v), "ahead visible");
  CHECK(u > .5f + .02f && std::abs(v - .5f) < 1e-4f, "object should move right: u=%f v=%f", u, v);
  CHECK(vrost::applyHomography(vrost::reaimHomography(source, left, eye), u, v, su, sv), "applies");
  CHECK(std::abs(su - .5f) < 1e-4f && std::abs(sv - .5f) < 1e-4f, "maps back to centre: %f %f", su, sv);
  // Looking up moves it down the picture (texture v grows downwards).
  const auto up = fromAxisAngle(1, 0, 0, 5);
  CHECK(project(up, eye, {0, 0, -1}, u, v), "ahead visible");
  CHECK(v > .5f + .02f && std::abs(u - .5f) < 1e-4f, "object should move down: u=%f v=%f", u, v);
  // The yawed pose used by the alignment check really is a yaw of that size, to the left.
  CHECK(std::abs(vrost::rotationDegrees(source, left) - 5) < 1e-3f, "rotation %f", vrost::rotationDegrees(source, left));
  const vrost::Vec3 forward = vrost::rotate(left, {0, 0, -1});
  CHECK(forward.x < -.05f, "a positive yaw turns left: x=%f", forward.x);
}

void behind() {
  const auto eye = eyeView(-1, 1, 1, -1, vrost::Pose{});
  const vrost::Pose source{};
  const auto around = vrost::yawed(source, 180);
  float su, sv;
  CHECK(!vrost::applyHomography(vrost::reaimHomography(source, around, eye), .5f, .5f, su, sv),
        "a view behind the source image must not sample it");
}

void constants() {
  const std::array<VrostView, 2> eyes{eyeView(-1, .8f, .9f, -1.1f, vrost::Pose{}), eyeView(-.8f, 1, .9f, -1.1f, vrost::Pose{})};
  const auto c = vrost::reaimConstants(vrost::Pose{}, vrost::Pose{}, eyes);
  static_assert(sizeof(vrost::ReaimPushConstants) == 96, "push constant block: 2 eyes x 3 vec4 rows");
  for (int e = 0; e < 2; ++e)
    for (int i = 0; i < 3; ++i)
      for (int j = 0; j < 3; ++j)
        CHECK(std::abs(c.rows[e][i][j] - (i == j ? 1.f : 0.f)) < 1e-5f, "eye %d row %d col %d = %f", e, i, j, c.rows[e][i][j]);
}
} // namespace

int main() {
  identity();
  worldConsistency();
  directions();
  behind();
  constants();
  if (failures) {
    std::printf("%d re-aim contract failures\n", failures);
    return 1;
  }
  std::printf("re-aim contracts passed\n");
  return 0;
}
