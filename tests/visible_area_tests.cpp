// 1.0.0.11: the pixels the fused frame pass computes (client/device/visible_area.hpp): each eye's
// span per output row from SteamVR's visible-area mesh and the outline of its lens distortion of
// the panel, inside both, with a margin around them; the mesh's way up checked against the outline
// (or kept both ways up without one). tests/fused_shader_tests.cpp checks that the pass leaves the
// rest black and the computed pixels unchanged.
#include "client/device/visible_area.hpp"

#include <cmath>
#include <cstdio>

namespace {
int failures = 0, checks = 0;
void check(bool ok, const char *what) {
  ++checks;
  if (!ok) {
    ++failures;
    std::printf("FAIL %s\n", what);
  }
}
uint32_t first(const std::vector<uint32_t> &spans, uint32_t row) { return vrost::spanFirst(spans[row]); }
uint32_t end(const std::vector<uint32_t> &spans, uint32_t row) { return vrost::spanEnd(spans[row]); }
// A square [lo, hi] x [lo, hi] as two triangles.
void square(vrost::VisibleArea &area, int eye, float lo, float hi) {
  area.triangles[eye].push_back({lo, lo, hi, lo, hi, hi});
  area.triangles[eye].push_back({lo, lo, hi, hi, lo, hi});
}
void circle(vrost::VisibleArea &area, int eye, float cx, float cy, float r, int colours = 3) {
  for (int c = 0; c < colours; ++c)
    for (int i = 0; i < 256; ++i) {
      const float a = 6.2831853f * float(i) / 256.f;
      area.outlines[eye][c].push_back({cx + r * std::cos(a), cy + r * std::sin(a)});
    }
}
}  // namespace

int main() {
  using namespace vrost;
  // Packing: first | end << 16, end never before first, both at most 65535.
  check(spanFirst(packSpan(12, 3000)) == 12 && spanEnd(packSpan(12, 3000)) == 3000, "span round trip");
  check(spanEnd(packSpan(50, 10)) == 50, "an end before the first is empty, not reversed");
  check(spanEnd(packSpan(0, 70000)) == 0xffff, "spans are 16 bits each");
  check(visibleMargin(2560, 2560) == 77 && visibleMargin(2016, 2016) == 60 && visibleMargin(100, 100) == 8,
        "margin: 3 % of the larger side, at least 8 pixels");
  // No description: whole rows.
  VisibleArea none;
  auto spans = visibleSpans(none, 0, 640, 480, 10);
  check(spans.size() == 480 && first(spans, 0) == 0 && end(spans, 479) == 640 && visibleShare(spans, 640) == 1.0,
        "no description computes every pixel");
  check(none.sources() == "none" && !none.any(), "no sources");
  // A mesh square from 0.1 to 0.9: rows inside span 64..576 (+margin), rows far outside are empty.
  VisibleArea mesh;
  square(mesh, 0, .1f, .9f);
  spans = visibleSpans(mesh, 0, 640, 640, 0);
  check(first(spans, 320) == 64 && end(spans, 320) == 576, "a mesh square's rows");
  check(end(spans, 10) == first(spans, 10) && end(spans, 630) == first(spans, 630), "rows outside the square are empty");
  check(std::fabs(visibleShare(spans, 640) - .64) < .01, "the square is 64 % of the eye");
  spans = visibleSpans(mesh, 0, 640, 640, 16);
  check(first(spans, 320) == 48 && end(spans, 320) == 592, "the margin widens each row");
  check(end(spans, 50) > first(spans, 50) && end(spans, 40) == first(spans, 40),
        "the margin reaches rows above the area (row 64 - 16) and no further");
  // The other eye has no description: whole rows.
  spans = visibleSpans(mesh, 1, 640, 640, 16);
  check(first(spans, 0) == 0 && end(spans, 0) == 640, "an eye without a description computes every pixel");
  // An outline circle: each row's chord, for the widest colour.
  VisibleArea outline;
  circle(outline, 0, .5f, .5f, .45f);
  spans = visibleSpans(outline, 0, 1000, 1000, 0);
  const double chord = 2 * std::sqrt(.45 * .45 - .3 * .3) * 1000; // the row at v = 0.8
  check(std::fabs(double(end(spans, 800) - first(spans, 800)) - chord) < 6, "a circle's chord per row");
  check(std::fabs(visibleShare(spans, 1000) - 3.14159 * .45 * .45) < .01, "a circle's share");
  check(outline.sources() == "distortion", "outline source");
  // Red, green and blue differ (lateral colour): the widest wins.
  VisibleArea colours;
  for (int c = 0; c < 3; ++c)
    for (int i = 0; i < 256; ++i) {
      const float a = 6.2831853f * float(i) / 256.f, r = .40f + .02f * float(c);
      colours.outlines[0][c].push_back({.5f + r * std::cos(a), .5f + r * std::sin(a)});
    }
  spans = visibleSpans(colours, 0, 1000, 1000, 0);
  check(first(spans, 500) <= 80 + 1 && end(spans, 500) >= 920 - 1, "the widest colour decides");
  // Inside both: a mesh square and a larger circle around it, then a smaller one inside the square.
  VisibleArea both;
  square(both, 0, .1f, .9f);
  circle(both, 0, .5f, .5f, .6f);
  spans = visibleSpans(both, 0, 1000, 1000, 0);
  check(first(spans, 500) == 100 && end(spans, 500) == 900, "a larger outline leaves the mesh");
  VisibleArea inner;
  square(inner, 0, .1f, .9f);
  circle(inner, 0, .5f, .5f, .3f);
  spans = visibleSpans(inner, 0, 1000, 1000, 0);
  check(first(spans, 500) >= 199 && end(spans, 500) <= 801, "a smaller outline trims the mesh");
  check(end(spans, 150) == first(spans, 150), "rows the outline misses are empty even inside the mesh");
  check(inner.sources() == "mesh+distortion", "both sources");
  // An outline reaching past the image (the panel's corners beyond the texture) is clamped to the eye.
  VisibleArea wide;
  circle(wide, 0, .5f, .5f, .6f); // past the sides, short of the corners (0.707 from the centre)
  spans = visibleSpans(wide, 0, 200, 200, 4);
  check(first(spans, 100) == 0 && end(spans, 100) == 200, "clamped to the eye");
  check(end(spans, 0) - first(spans, 0) < 160, "the corners stay out");
  // A level edge (a triangle with a horizontal side within one row) counts its whole length.
  VisibleArea level;
  level.triangles[0].push_back({.2f, .5f, .8f, .5f, .5f, .5005f});
  spans = visibleSpans(level, 0, 1000, 1000, 0);
  check(first(spans, 500) >= 199 && first(spans, 500) <= 200 && end(spans, 500) >= 800 && end(spans, 500) <= 801,
        "a level edge spans its length");
  // A degenerate description (a sliver) gives a small share: the client then computes every pixel.
  VisibleArea sliver;
  square(sliver, 0, .49f, .51f);
  spans = visibleSpans(sliver, 0, 1000, 1000, 8);
  check(visibleShare(spans, 1000) < .5, "a sliver's share is small (the client's plausibility check)");
  // The mesh's way up (alignMeshes): checked against the lens outline, whose coordinates SteamVR documents.
  // An outline reaching more of the top (v 0.02 to 0.80) and a mesh inside it (v 0.05 to 0.75).
  auto ellipse = [](VisibleArea &area, int eye, float top, float bottom) {
    for (int c = 0; c < 3; ++c)
      for (int i = 0; i < 256; ++i) {
        const float a = 6.2831853f * float(i) / 256.f;
        area.outlines[eye][c].push_back({.5f + .48f * std::cos(a), (top + bottom) / 2 + (bottom - top) / 2 * std::sin(a)});
      }
  };
  auto rect = [](VisibleArea &area, int eye, float x0, float y0, float x1, float y1) {
    area.triangles[eye].push_back({x0, y0, x1, y0, x1, y1});
    area.triangles[eye].push_back({x0, y0, x1, y1, x0, y1});
  };
  VisibleArea upright;
  ellipse(upright, 0, .02f, .80f);
  rect(upright, 0, .1f, .05f, .9f, .75f);
  alignMeshes(upright);
  check(upright.notes.empty() && upright.triangles[0][0][1] == .05f, "a mesh the right way up is used as it is");
  VisibleArea upsideDown;
  ellipse(upsideDown, 0, .02f, .80f);
  rect(upsideDown, 0, .1f, .25f, .9f, .95f); // the same mesh, upside down
  alignMeshes(upsideDown);
  spans = visibleSpans(upsideDown, 0, 1000, 1000, 0);
  check(upsideDown.notes.find("left eye's mesh used upside down") != std::string::npos, "an upside-down mesh is turned");
  check(end(spans, 100) > first(spans, 100) && end(spans, 900) == first(spans, 900),
        "turned, the mesh's rows are the outline's (row 100 shown, row 900 not)");
  // A level mesh (the same both ways up) stays as it is.
  VisibleArea level2;
  ellipse(level2, 1, .1f, .9f);
  rect(level2, 1, .2f, .2f, .8f, .8f);
  alignMeshes(level2);
  check(level2.notes.empty() && level2.hasMesh(1), "a symmetric mesh stays");
  // A mesh that describes something else (a square in the image's corner) is ignored for the outline.
  VisibleArea other;
  ellipse(other, 0, .02f, .98f);
  rect(other, 0, 0.f, 0.f, .3f, .3f);
  alignMeshes(other);
  check(!other.hasMesh(0) && other.notes.find("ignored") != std::string::npos, "a mesh far from the outline is ignored");
  // Without an outline the mesh is kept both ways up: whichever way it is meant, nothing it shows is left out.
  VisibleArea alone;
  rect(alone, 0, .2f, .25f, .8f, .95f);
  alignMeshes(alone);
  spans = visibleSpans(alone, 0, 1000, 1000, 0);
  check(alone.notes.find("left eye's mesh used both ways up") != std::string::npos && alone.triangles[0].size() == 4 &&
            !alone.hasMesh(1),
        "without an outline the mesh is kept both ways up");
  check(end(spans, 100) > first(spans, 100) && end(spans, 900) > first(spans, 900) && end(spans, 20) == first(spans, 20),
        "both ways up: rows either way shows are computed (100 and 900), rows neither shows are not (20)");
  if (failures) {
    std::printf("visible area: %d of %d checks failed\n", failures, checks);
    return 1;
  }
  std::printf("visible area: %d checks passed\n", checks);
  return 0;
}
