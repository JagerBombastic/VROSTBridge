#pragma once
// 1.0.0.11: the part of each eye's image a headset's lenses show, as the fused frame pass
// (client/platform/linux/fused.comp) reads it: one span of pixels per output row and eye.
// Pixels outside are never computed; the output image is black there from the start.
//
// Two sources describe the area, both in the eye's image (u right, v down, 0..1), and a pixel
// must be inside both:
//  - SteamVR's visible-area mesh (GetHiddenAreaMesh, inverse): triangles covering what the
//    headset's driver says the lenses show; headsets without one report none;
//  - the outline of what SteamVR's compositor samples: its lens distortion (ComputeDistortion)
//    of the panel's edge, for red, green and blue. Image corners beyond it never reach the panel.
// A margin of pixels around the area stays computed: SteamVR shows an earlier frame turned to
// a newer head pose when a frame is late, which brings pixels near the border into view.
// SteamVR documents ComputeDistortion's coordinates (from the upper left); the mesh's way up is
// not documented, so alignMeshes checks the mesh against the outline where an eye has both, and
// keeps it both ways up where there is no outline.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

namespace vrost {

struct VisibleArea {
  std::vector<std::array<float, 6>> triangles[2]; // per eye: x0 y0 x1 y1 x2 y2
  std::vector<std::array<float, 2>> outlines[2][3]; // per eye and colour: a closed loop of points
  std::string notes; // what alignMeshes changed, for the log
  bool hasMesh(int eye) const { return !triangles[eye].empty(); }
  bool hasOutline(int eye) const {
    for (const auto &loop : outlines[eye])
      if (loop.size() >= 3) return true;
    return false;
  }
  bool any() const { return hasMesh(0) || hasMesh(1) || hasOutline(0) || hasOutline(1); }
  std::string sources() const {
    const bool mesh = hasMesh(0) || hasMesh(1), outline = hasOutline(0) || hasOutline(1);
    return mesh && outline ? "mesh+distortion" : mesh ? "mesh" : outline ? "distortion" : "none";
  }
};

// A packed span: first visible pixel | end (exclusive) << 16; both at most 65535.
inline uint32_t packSpan(uint32_t first, uint32_t end) {
  first = std::min<uint32_t>(first, 0xffffu);
  end = std::min<uint32_t>(std::max(end, first), 0xffffu);
  return first | end << 16;
}
inline uint32_t spanFirst(uint32_t span) { return span & 0xffffu; }
inline uint32_t spanEnd(uint32_t span) { return span >> 16; }

namespace visible_detail {
// The x range (image units) of segment a-b within the horizontal band [top, bottom], or false.
inline bool segmentInBand(float ax, float ay, float bx, float by, float top, float bottom, float &lo, float &hi) {
  if (ay > by) {
    std::swap(ax, bx);
    std::swap(ay, by);
  }
  if (by < top || ay > bottom) return false;
  if (by - ay <= 1e-12f) { // level: the whole segment lies in the band
    lo = std::min(ax, bx);
    hi = std::max(ax, bx);
    return true;
  }
  auto xAt = [&](float y) { return ax + (bx - ax) * (y - ay) / (by - ay); };
  const float x0 = xAt(std::max(ay, top)), x1 = xAt(std::min(by, bottom));
  lo = std::min(x0, x1);
  hi = std::max(x0, x1);
  return true;
}
struct Extent {
  float lo = 1e30f, hi = -1e30f;
  void add(float a, float b) {
    lo = std::min(lo, a);
    hi = std::max(hi, b);
  }
  bool valid() const { return lo <= hi; }
};
} // namespace visible_detail

// Per output row of one eye (width x height pixels): the packed span of pixels to compute. No
// description for the eye gives whole rows. margin: pixels kept around the area, both ways.
inline std::vector<uint32_t> visibleSpans(const VisibleArea &area, int eye, uint32_t width, uint32_t height,
                                          uint32_t margin) {
  using visible_detail::Extent;
  std::vector<uint32_t> spans(height, packSpan(0, width));
  const bool mesh = area.hasMesh(eye), outline = area.hasOutline(eye);
  if ((!mesh && !outline) || !width || !height) return spans;
  std::vector<Extent> meshRows(height), outlineRows(height);
  // Each edge widens the rows it crosses: a band inside a triangle or a closed outline meets
  // its edges on both sides, so the edges' extents in a row are the area's extent there.
  const auto addSegment = [&](std::vector<Extent> &rows, float ax, float ay, float bx, float by) {
    const float lowY = std::max(0.f, std::min(ay, by)), highY = std::min(1.f, std::max(ay, by));
    if (!(lowY <= highY)) return;
    const uint32_t first = std::min<uint32_t>(height - 1, uint32_t(std::floor(lowY * float(height))));
    const uint32_t last = std::min<uint32_t>(height - 1, uint32_t(std::floor(highY * float(height))));
    for (uint32_t y = first; y <= last; ++y) {
      float lo, hi;
      if (visible_detail::segmentInBand(ax, ay, bx, by, float(y) / float(height), float(y + 1) / float(height), lo, hi))
        rows[y].add(lo, hi);
    }
  };
  if (mesh)
    for (const auto &t : area.triangles[eye])
      for (int i = 0; i < 3; ++i) {
        const int j = (i + 1) % 3;
        addSegment(meshRows, t[i * 2], t[i * 2 + 1], t[j * 2], t[j * 2 + 1]);
      }
  if (outline)
    for (const auto &loop : area.outlines[eye]) {
      if (loop.size() < 3) continue;
      for (size_t i = 0; i < loop.size(); ++i) {
        const auto &a = loop[i], &b = loop[(i + 1) % loop.size()];
        addSegment(outlineRows, a[0], a[1], b[0], b[1]);
      }
    }
  // Each row: inside both descriptions where both exist, in pixels; then the margin around it.
  std::vector<int64_t> first(height, 0), end(height, 0);
  for (uint32_t y = 0; y < height; ++y) {
    Extent e;
    bool empty = false;
    if (mesh) {
      if (meshRows[y].valid()) e = meshRows[y];
      else empty = true;
    }
    if (outline && !empty) {
      if (!outlineRows[y].valid()) empty = true;
      else if (mesh) {
        e.lo = std::max(e.lo, outlineRows[y].lo);
        e.hi = std::min(e.hi, outlineRows[y].hi);
        empty = e.lo > e.hi;
      } else e = outlineRows[y];
    }
    if (empty) {
      first[y] = end[y] = 0;
      continue;
    }
    first[y] = int64_t(std::floor(double(e.lo) * width)) - int64_t(margin);
    end[y] = int64_t(std::ceil(double(e.hi) * width)) + int64_t(margin);
  }
  for (uint32_t y = 0; y < height; ++y) {
    int64_t lo = INT64_MAX, hi = INT64_MIN;
    const uint32_t from = y >= margin ? y - margin : 0, to = std::min<uint32_t>(height - 1, y + margin);
    for (uint32_t r = from; r <= to; ++r)
      if (end[r] > first[r]) {
        lo = std::min(lo, first[r]);
        hi = std::max(hi, end[r]);
      }
    if (lo >= hi) spans[y] = packSpan(0, 0);
    else spans[y] = packSpan(uint32_t(std::clamp<int64_t>(lo, 0, width)), uint32_t(std::clamp<int64_t>(hi, 0, width)));
  }
  return spans;
}

// The share of an eye's pixels the spans compute (1 when every row is whole).
inline double visibleShare(const std::vector<uint32_t> &spans, uint32_t width) {
  if (spans.empty() || !width) return 1.0;
  double sum = 0;
  for (const auto span : spans) sum += double(spanEnd(span) - spanFirst(span));
  return sum / (double(width) * double(spans.size()));
}

namespace visible_detail {
// Pixels of `outline`'s rows that `mesh`'s rows leave out (both from visibleSpans on one grid).
inline double leftOut(const std::vector<uint32_t> &outline, const std::vector<uint32_t> &mesh) {
  double lost = 0;
  for (size_t y = 0; y < outline.size() && y < mesh.size(); ++y) {
    const double lo = spanFirst(outline[y]), hi = spanEnd(outline[y]);
    if (hi <= lo) continue;
    const double mlo = spanFirst(mesh[y]), mhi = spanEnd(mesh[y]);
    if (mhi <= mlo) {
      lost += hi - lo;
      continue;
    }
    lost += std::clamp(mlo - lo, 0.0, hi - lo) + std::clamp(hi - mhi, 0.0, hi - lo);
  }
  return lost;
}
} // namespace visible_detail

// Where an eye has both a mesh and an outline, the mesh is compared with the outline on a coarse
// grid, as given and upside down. Its visible area should lie inside what the outline says reaches
// the panel (less where the lens itself hides more), so it is used the way up that leaves less of
// the outline out: upside down only when that leaves out at least 2 % of the outline less. A mesh
// that leaves out more than 40 % of the outline even then describes something else, and only the
// outline is used: computing more pixels is the safe side. A mesh without an outline to check it
// against is used both ways up (each row then reaches as far as either does), so whichever way up
// it is meant, nothing it shows is left out.
inline void alignMeshes(VisibleArea &area) {
  constexpr uint32_t grid = 128;
  for (int eye = 0; eye < 2; ++eye) {
    if (!area.hasMesh(eye)) continue;
    const char *name = eye ? "right" : "left";
    if (!area.hasOutline(eye)) {
      const auto given = area.triangles[eye];
      for (auto t : given) {
        for (int v = 0; v < 3; ++v) t[v * 2 + 1] = 1.f - t[v * 2 + 1];
        area.triangles[eye].push_back(t);
      }
      area.notes += std::string(area.notes.empty() ? "" : "; ") + name +
                    " eye's mesh used both ways up (no lens outline to check it against)";
      continue;
    }
    VisibleArea outline, given, flipped;
    for (int c = 0; c < 3; ++c) outline.outlines[0][c] = area.outlines[eye][c];
    given.triangles[0] = area.triangles[eye];
    flipped.triangles[0] = area.triangles[eye];
    for (auto &t : flipped.triangles[0])
      for (int v = 0; v < 3; ++v) t[v * 2 + 1] = 1.f - t[v * 2 + 1];
    const auto o = visibleSpans(outline, 0, grid, grid, 0);
    double inside = 0;
    for (const auto span : o) inside += double(spanEnd(span) - spanFirst(span));
    if (inside < .05 * grid * grid) continue; // too little outline to judge by
    const double lostGiven = visible_detail::leftOut(o, visibleSpans(given, 0, grid, grid, 0));
    const double lostFlipped = visible_detail::leftOut(o, visibleSpans(flipped, 0, grid, grid, 0));
    if (lostGiven - lostFlipped > .02 * inside) {
      area.triangles[eye] = flipped.triangles[0];
      area.notes += std::string(area.notes.empty() ? "" : "; ") + name + " eye's mesh used upside down (it then matches the lens outline)";
    }
    if (std::min(lostGiven, lostFlipped) > .4 * inside) {
      area.triangles[eye].clear();
      area.notes += std::string(area.notes.empty() ? "" : "; ") + name + " eye's mesh ignored (it leaves out " +
                    std::to_string(int(std::lround(100 * std::min(lostGiven, lostFlipped) / inside))) +
                    " % of what the lens outline shows)";
    }
  }
}

// The margin for an eye of this size: 3 % of its larger side, at least 8 pixels.
inline uint32_t visibleMargin(uint32_t width, uint32_t height) {
  return std::max<uint32_t>(8, uint32_t(std::lround(.03 * double(std::max(width, height)))));
}

} // namespace vrost
