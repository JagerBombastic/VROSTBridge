#include "client/ui/raster.hpp"

#include "client/device/status_text.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iterator>
#include <mutex>
#include <unordered_map>

#define STBTT_STATIC
#define STB_TRUETYPE_IMPLEMENTATION
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wcast-qual"
#endif
#include "third_party/stb/stb_truetype.h"
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

namespace vrost::ui {

namespace {
float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
}  // namespace

// ------------------------------------------------------------------ image
Image::Image(int width, int height)
    : width_(std::max(0, width)), height_(std::max(0, height)), data_(size_t(width_) * size_t(height_) * 4, 0.f) {}

void Image::blend(int x, int y, const Color &color, float coverage) {
  if (x < 0 || y < 0 || x >= width_ || y >= height_) return;
  const float a = color.a * clamp01(coverage);
  if (a <= 0) return;
  float *d = pixel(x, y);
  const float keep = 1 - a;
  d[0] = color.r * a + d[0] * keep;
  d[1] = color.g * a + d[1] * keep;
  d[2] = color.b * a + d[2] * keep;
  d[3] = a + d[3] * keep;
}

void Image::blendPremultiplied(int x, int y, const float *source, float opacity) {
  if (x < 0 || y < 0 || x >= width_ || y >= height_) return;
  const float a = source[3] * opacity;
  if (a <= 0) return;
  float *d = pixel(x, y);
  const float keep = 1 - a;
  for (int c = 0; c < 3; ++c) d[c] = source[c] * opacity + d[c] * keep;
  d[3] = a + d[3] * keep;
}

void Image::clear(const Color &color) {
  for (size_t i = 0; i < data_.size(); i += 4) {
    data_[i] = color.r * color.a;
    data_[i + 1] = color.g * color.a;
    data_[i + 2] = color.b * color.a;
    data_[i + 3] = color.a;
  }
}

std::vector<uint8_t> Image::straightRgba8() const {
  const size_t count = size_t(width_) * size_t(height_);
  std::vector<uint8_t> out(count * 4, 0);
  std::vector<uint8_t> known(count, 0);
  const auto byte = [](float v) { return uint8_t(std::lround(clamp01(v) * 255.f)); };
  for (size_t i = 0; i < count; ++i) {
    const float *p = &data_[i * 4];
    const uint8_t alpha = byte(p[3]);
    out[i * 4 + 3] = alpha;
    if (alpha) {
      for (int c = 0; c < 3; ++c) out[i * 4 + size_t(c)] = byte(p[c] / p[3]);
      known[i] = 1;
    }
  }
  // Transparent pixels take the colour of the nearest visible pixel (row, then column).
  std::vector<uint8_t> rowKnown(size_t(height_), 0);
  for (int y = 0; y < height_; ++y) {
    const size_t row = size_t(y) * size_t(width_);
    int last = -1;
    for (int x = 0; x < width_; ++x) {
      if (known[row + size_t(x)]) {
        if (last < 0)
          for (int fill = 0; fill < x; ++fill) std::memcpy(&out[(row + size_t(fill)) * 4], &out[(row + size_t(x)) * 4], 3);
        last = x;
      } else if (last >= 0) {
        std::memcpy(&out[(row + size_t(x)) * 4], &out[(row + size_t(last)) * 4], 3);
      }
    }
    rowKnown[size_t(y)] = last >= 0;
  }
  int lastRow = -1;
  for (int y = 0; y < height_; ++y) {
    if (rowKnown[size_t(y)]) {
      if (lastRow < 0)
        for (int fill = 0; fill < y; ++fill)
          for (int x = 0; x < width_; ++x)
            std::memcpy(&out[(size_t(fill) * size_t(width_) + size_t(x)) * 4], &out[(size_t(y) * size_t(width_) + size_t(x)) * 4], 3);
      lastRow = y;
    } else if (lastRow >= 0) {
      for (int x = 0; x < width_; ++x)
        std::memcpy(&out[(size_t(y) * size_t(width_) + size_t(x)) * 4], &out[(size_t(lastRow) * size_t(width_) + size_t(x)) * 4], 3);
    }
  }
  return out;
}

Image Image::fromStraightRgba8(const uint8_t *data, int width, int height) {
  Image image(width, height);
  for (size_t i = 0; i < size_t(width) * size_t(height); ++i) {
    const float a = data[i * 4 + 3] / 255.f;
    float *d = &image.data_[i * 4];
    for (int c = 0; c < 3; ++c) d[c] = data[i * 4 + size_t(c)] / 255.f * a;
    d[3] = a;
  }
  return image;
}

namespace {
// Weights of the source samples covering each destination sample (exact box overlap).
struct Taps {
  int first = 0;
  std::vector<float> weights;
};
std::vector<Taps> areaTaps(int source, int target) {
  std::vector<Taps> taps(size_t(std::max(target, 0)));
  const double scale = double(source) / double(target);
  for (int i = 0; i < target; ++i) {
    const double begin = i * scale, end = (i + 1) * scale;
    int first = int(std::floor(begin));
    int last = std::min(source - 1, int(std::ceil(end)) - 1);
    first = std::clamp(first, 0, source - 1);
    double total = 0;
    for (int s = first; s <= last; ++s) {
      const double w = std::max(0.0, std::min(end, double(s + 1)) - std::max(begin, double(s)));
      taps[size_t(i)].weights.push_back(float(w));
      total += w;
    }
    if (taps[size_t(i)].weights.empty()) {
      taps[size_t(i)].weights.push_back(1);
      total = 1;
    }
    for (auto &w : taps[size_t(i)].weights) w = float(w / total);
    taps[size_t(i)].first = first;
  }
  return taps;
}
}  // namespace

Image Image::resized(int width, int height) const {
  Image out(width, height);
  if (empty() || out.empty()) return out;
  const auto xs = areaTaps(width_, width), ys = areaTaps(height_, height);
  Image horizontal(width, height_);
  for (int y = 0; y < height_; ++y)
    for (int x = 0; x < width; ++x) {
      float sum[4] = {0, 0, 0, 0};
      const auto &t = xs[size_t(x)];
      for (size_t k = 0; k < t.weights.size(); ++k) {
        const float *p = pixel(t.first + int(k), y);
        for (int c = 0; c < 4; ++c) sum[c] += p[c] * t.weights[k];
      }
      std::memcpy(horizontal.pixel(x, y), sum, sizeof(sum));
    }
  for (int y = 0; y < height; ++y)
    for (int x = 0; x < width; ++x) {
      float sum[4] = {0, 0, 0, 0};
      const auto &t = ys[size_t(y)];
      for (size_t k = 0; k < t.weights.size(); ++k) {
        const float *p = horizontal.pixel(x, t.first + int(k));
        for (int c = 0; c < 4; ++c) sum[c] += p[c] * t.weights[k];
      }
      std::memcpy(out.pixel(x, y), sum, sizeof(sum));
    }
  return out;
}

// ------------------------------------------------------------------ shapes
float roundedRectDistance(float px, float py, const Rect &rect, float radius) {
  const float hx = rect.w * .5f, hy = rect.h * .5f;
  const float r = std::max(0.f, std::min(radius, std::min(hx, hy)));
  const float qx = std::fabs(px - (rect.x + hx)) - (hx - r);
  const float qy = std::fabs(py - (rect.y + hy)) - (hy - r);
  const float ox = std::max(qx, 0.f), oy = std::max(qy, 0.f);
  return std::sqrt(ox * ox + oy * oy) + std::min(std::max(qx, qy), 0.f) - r;
}

Color mix(const Color &a, const Color &b, float t) {
  t = clamp01(t);
  return {a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, a.a + (b.a - a.a) * t};
}

Color gradientAt(const std::vector<GradientStop> &stops, float t) {
  if (stops.empty()) return {};
  if (t <= stops.front().at) return stops.front().color;
  for (size_t i = 1; i < stops.size(); ++i)
    if (t <= stops[i].at) {
      const float span = stops[i].at - stops[i - 1].at;
      return mix(stops[i - 1].color, stops[i].color, span > 0 ? (t - stops[i - 1].at) / span : 1);
    }
  return stops.back().color;
}

namespace {
struct Span {
  int x0, y0, x1, y1;  // inclusive-exclusive pixel bounds
};
Span bounds(const Image &image, float x0, float y0, float x1, float y1, const Rect &clip = {}) {
  Span s{int(std::floor(x0)) - 1, int(std::floor(y0)) - 1, int(std::ceil(x1)) + 1, int(std::ceil(y1)) + 1};
  if (clip.w > 0 && clip.h > 0) {
    s.x0 = std::max(s.x0, int(std::floor(clip.x)));
    s.y0 = std::max(s.y0, int(std::floor(clip.y)));
    s.x1 = std::min(s.x1, int(std::ceil(clip.x + clip.w)));
    s.y1 = std::min(s.y1, int(std::ceil(clip.y + clip.h)));
  }
  s.x0 = std::max(s.x0, 0);
  s.y0 = std::max(s.y0, 0);
  s.x1 = std::min(s.x1, image.width());
  s.y1 = std::min(s.y1, image.height());
  return s;
}
float clipCoverage(const Rect &clip, int x, int y) {
  if (clip.w <= 0 || clip.h <= 0) return 1;
  const float cx = clamp01(std::min(float(x) + 1, clip.x + clip.w) - std::max(float(x), clip.x));
  const float cy = clamp01(std::min(float(y) + 1, clip.y + clip.h) - std::max(float(y), clip.y));
  return cx * cy;
}
}  // namespace

void fillRoundedRect(Image &image, const Rect &rect, float radius, const Color &color) {
  const Span s = bounds(image, rect.x, rect.y, rect.x + rect.w, rect.y + rect.h);
  for (int y = s.y0; y < s.y1; ++y)
    for (int x = s.x0; x < s.x1; ++x)
      image.blend(x, y, color, clamp01(.5f - roundedRectDistance(float(x) + .5f, float(y) + .5f, rect, radius)));
}

void fillRoundedRect(Image &image, const Rect &rect, float radius, const std::vector<GradientStop> &stops,
                     bool vertical, const Rect &clip) {
  const Span s = bounds(image, rect.x, rect.y, rect.x + rect.w, rect.y + rect.h, clip);
  for (int y = s.y0; y < s.y1; ++y)
    for (int x = s.x0; x < s.x1; ++x) {
      const float cover = clamp01(.5f - roundedRectDistance(float(x) + .5f, float(y) + .5f, rect, radius)) *
                          clipCoverage(clip, x, y);
      if (cover <= 0) continue;
      const float t = vertical ? (float(y) + .5f - rect.y) / std::max(1.f, rect.h)
                               : (float(x) + .5f - rect.x) / std::max(1.f, rect.w);
      image.blend(x, y, gradientAt(stops, t), cover);
    }
}

void strokeRoundedRect(Image &image, const Rect &rect, float radius, float width, const Color &color) {
  const Span s = bounds(image, rect.x, rect.y, rect.x + rect.w, rect.y + rect.h);
  for (int y = s.y0; y < s.y1; ++y)
    for (int x = s.x0; x < s.x1; ++x) {
      const float d = roundedRectDistance(float(x) + .5f, float(y) + .5f, rect, radius);
      const float cover = clamp01(.5f - d) - clamp01(.5f - (d + width));
      if (cover > 0) image.blend(x, y, color, cover);
    }
}

void fillCircle(Image &image, float cx, float cy, float radius, const Color &color) {
  const Span s = bounds(image, cx - radius, cy - radius, cx + radius, cy + radius);
  for (int y = s.y0; y < s.y1; ++y)
    for (int x = s.x0; x < s.x1; ++x) {
      const float dx = float(x) + .5f - cx, dy = float(y) + .5f - cy;
      image.blend(x, y, color, clamp01(.5f - (std::sqrt(dx * dx + dy * dy) - radius)));
    }
}

void glow(Image &image, float cx, float cy, float radius, const Color &color) {
  const float reach = radius * 1.6f, sigma2 = 2 * (radius / 2.4f) * (radius / 2.4f);
  const Span s = bounds(image, cx - reach, cy - reach, cx + reach, cy + reach);
  for (int y = s.y0; y < s.y1; ++y)
    for (int x = s.x0; x < s.x1; ++x) {
      const float dx = float(x) + .5f - cx, dy = float(y) + .5f - cy;
      image.blend(x, y, color, std::exp(-(dx * dx + dy * dy) / sigma2));
    }
}

void drawImage(Image &target, const Image &source, float x, float y, float opacity) {
  const int ox = int(std::lround(x)), oy = int(std::lround(y));
  for (int sy = 0; sy < source.height(); ++sy)
    for (int sx = 0; sx < source.width(); ++sx) target.blendPremultiplied(ox + sx, oy + sy, source.pixel(sx, sy), opacity);
}

// ------------------------------------------------------------------ UTF-8
std::vector<uint32_t> decodeUtf8(const std::string &text) {
  std::vector<uint32_t> out;
  out.reserve(text.size());
  const size_t n = text.size();
  size_t i = 0;
  while (i < n) {
    const auto lead = static_cast<unsigned char>(text[i]);
    if (lead < 0x80) {
      out.push_back(lead);
      ++i;
      continue;
    }
    size_t length = 0;
    unsigned char low = 0x80, high = 0xBF;
    if (lead >= 0xC2 && lead <= 0xDF) length = 2;
    else if (lead >= 0xE0 && lead <= 0xEF) {
      length = 3;
      if (lead == 0xE0) low = 0xA0;
      if (lead == 0xED) high = 0x9F;
    } else if (lead >= 0xF0 && lead <= 0xF4) {
      length = 4;
      if (lead == 0xF0) low = 0x90;
      if (lead == 0xF4) high = 0x8F;
    }
    uint32_t cp = lead & (0x7Fu >> length);
    size_t used = 1;
    for (; used < length && i + used < n; ++used) {
      const auto next = static_cast<unsigned char>(text[i + used]);
      if (next < (used == 1 ? low : 0x80) || next > (used == 1 ? high : 0xBF)) break;
      cp = (cp << 6) | (next & 0x3Fu);
    }
    i += used;
    out.push_back(length && used == length ? cp : 0xFFFD);
  }
  return out;
}

namespace {
std::string encodeUtf8(const std::vector<uint32_t> &points, size_t begin, size_t end) {
  std::string out;
  for (size_t i = begin; i < end && i < points.size(); ++i) {
    const uint32_t cp = points[i];
    if (cp < 0x80) out += char(cp);
    else if (cp < 0x800) {
      out += char(0xC0 | (cp >> 6));
      out += char(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      out += char(0xE0 | (cp >> 12));
      out += char(0x80 | ((cp >> 6) & 0x3F));
      out += char(0x80 | (cp & 0x3F));
    } else {
      out += char(0xF0 | (cp >> 18));
      out += char(0x80 | ((cp >> 12) & 0x3F));
      out += char(0x80 | ((cp >> 6) & 0x3F));
      out += char(0x80 | (cp & 0x3F));
    }
  }
  return out;
}
}  // namespace

// ------------------------------------------------------------------ fonts
struct Font::Impl {
  std::vector<uint8_t> data;
  stbtt_fontinfo info{};
  bool ok = false;
  float gamma = 1;
  struct Bitmap {
    int x0 = 0, y0 = 0, w = 0, h = 0;
    std::vector<uint8_t> coverage;
  };
  mutable std::mutex mutex;
  mutable std::unordered_map<uint64_t, std::shared_ptr<const Bitmap>> cache;

  float scale(float size) const { return stbtt_ScaleForMappingEmToPixels(&info, size); }

  // Glyphs for a code point: the font's own, or the ASCII spelling of a missing one.
  void glyphs(uint32_t cp, std::vector<int> &out) const {
    out.clear();
    if (cp == '\t' || cp == 0xA0 || (cp >= 0x2000 && cp <= 0x200A)) cp = ' ';
    if (cp < 0x20 || cp == 0x7F) return;
    if (const int g = stbtt_FindGlyphIndex(&info, int(cp))) {
      out.push_back(g);
      return;
    }
    const char *spelling = cp < 0x80 ? "?" : vrost::status_detail::foldCodePoint(cp);
    for (const char *c = spelling; *c; ++c)
      if (const int g = stbtt_FindGlyphIndex(&info, static_cast<unsigned char>(*c))) out.push_back(g);
  }

  std::shared_ptr<const Bitmap> bitmap(int glyph, float size, int subpixel) const {
    const uint64_t key = (uint64_t(uint32_t(glyph)) << 32) | (uint64_t(uint32_t(std::lround(size * 64))) << 2) |
                         uint64_t(subpixel & 3);
    std::lock_guard<std::mutex> lock(mutex);
    auto found = cache.find(key);
    if (found != cache.end()) return found->second;
    if (cache.size() > 4096) cache.clear();
    auto made = std::make_shared<Bitmap>();
    Bitmap &bitmap = *made;
    const float s = scale(size), shift = float(subpixel) * .25f;
    int x0, y0, x1, y1;
    stbtt_GetGlyphBitmapBoxSubpixel(&info, glyph, s, s, shift, 0, &x0, &y0, &x1, &y1);
    bitmap.x0 = x0;
    bitmap.y0 = y0;
    bitmap.w = std::max(0, x1 - x0);
    bitmap.h = std::max(0, y1 - y0);
    if (bitmap.w && bitmap.h) {
      bitmap.coverage.resize(size_t(bitmap.w) * size_t(bitmap.h));
      stbtt_MakeGlyphBitmapSubpixel(&info, bitmap.coverage.data(), bitmap.w, bitmap.h, bitmap.w, s, s, shift, 0, glyph);
    }
    cache.emplace(key, made);
    return made;
  }

  // Walks the text, calling fn(glyph, penX) for each glyph; returns the final pen.
  template <typename Fn> float layout(const std::string &utf8, float size, float tracking, Fn &&fn) const {
    const float s = scale(size);
    float pen = 0;
    int previous = -1;
    bool first = true;
    std::vector<int> run;
    for (const uint32_t cp : decodeUtf8(utf8)) {
      glyphs(cp, run);
      for (const int g : run) {
        if (!first) pen += tracking;
        if (previous >= 0) pen += float(stbtt_GetGlyphKernAdvance(&info, previous, g)) * s;
        fn(g, pen);
        int advance = 0, bearing = 0;
        stbtt_GetGlyphHMetrics(&info, g, &advance, &bearing);
        pen += float(advance) * s;
        previous = g;
        first = false;
      }
    }
    return pen;
  }
};

Font::Font() : impl_(std::make_unique<Impl>()) {}
Font::~Font() = default;
Font::Font(Font &&) noexcept = default;
Font &Font::operator=(Font &&) noexcept = default;

bool Font::load(const std::string &path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) return false;
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
  return loadMemory(std::move(bytes));
}

bool Font::loadMemory(std::vector<uint8_t> bytes) {
  auto impl = std::make_unique<Impl>();
  impl->data = std::move(bytes);
  if (impl->data.size() < 12 || impl->data.size() > (64u << 20)) return false;
  const int offset = stbtt_GetFontOffsetForIndex(impl->data.data(), 0);
  if (offset < 0 || !stbtt_InitFont(&impl->info, impl->data.data(), offset)) return false;
  // stb_truetype draws TrueType (glyf) outlines; reject fonts whose basic glyphs are missing.
  if (!stbtt_FindGlyphIndex(&impl->info, 'A')) return false;
  impl->ok = true;
  impl->gamma = impl_ ? impl_->gamma : 1.f;
  impl_ = std::move(impl);
  return true;
}

bool Font::loaded() const { return impl_ && impl_->ok; }

void Font::setCoverageGamma(float gamma) {
  if (impl_) impl_->gamma = std::clamp(gamma, .3f, 3.f);
}

Font::Metrics Font::metrics(float size) const {
  Metrics m;
  if (!loaded()) return m;
  const float s = impl_->scale(size);
  int ascent = 0, descent = 0, gap = 0;
  if (!stbtt_GetFontVMetricsOS2(&impl_->info, &ascent, &descent, &gap)) stbtt_GetFontVMetrics(&impl_->info, &ascent, &descent, &gap);
  m.ascent = float(ascent) * s;
  m.descent = float(descent) * s;
  m.lineGap = float(gap) * s;
  int x0, y0, x1, y1;
  if (stbtt_GetCodepointBox(&impl_->info, 'H', &x0, &y0, &x1, &y1)) m.capHeight = float(y1) * s;
  return m;
}

float Font::measure(const std::string &utf8, float size, float tracking) const {
  if (!loaded()) return 0;
  return impl_->layout(utf8, size, tracking, [](int, float) {});
}

float Font::draw(Image &image, const std::string &utf8, float x, float baseline, float size, const Color &color,
                 float tracking) const {
  if (!loaded()) return x;
  const int line = int(std::lround(baseline));
  const float gamma = impl_->gamma;
  const float end = impl_->layout(utf8, size, tracking, [&](int glyph, float pen) {
    const float at = x + pen;
    int ix = int(std::floor(at));
    int sub = int(std::lround((at - float(ix)) * 4));
    if (sub == 4) {
      ++ix;
      sub = 0;
    }
    const auto bitmap = impl_->bitmap(glyph, size, sub);
    for (int gy = 0; gy < bitmap->h; ++gy)
      for (int gx = 0; gx < bitmap->w; ++gx) {
        const uint8_t c = bitmap->coverage[size_t(gy) * size_t(bitmap->w) + size_t(gx)];
        if (!c) continue;
        float cover = float(c) / 255.f;
        if (gamma != 1.f) cover = std::pow(cover, gamma);
        image.blend(ix + bitmap->x0 + gx, line + bitmap->y0 + gy, color, cover);
      }
  });
  return x + end;
}

std::string ellipsize(const Font &font, const std::string &utf8, float size, float maxWidth, float tracking) {
  if (font.measure(utf8, size, tracking) <= maxWidth) return utf8;
  const auto points = decodeUtf8(utf8);
  const std::string dots = "\xE2\x80\xA6";  // …
  size_t lo = 0, hi = points.size();
  while (lo < hi) {  // the longest prefix that fits with the ellipsis
    const size_t mid = (lo + hi + 1) / 2;
    std::string candidate = encodeUtf8(points, 0, mid);
    while (!candidate.empty() && candidate.back() == ' ') candidate.pop_back();
    if (font.measure(candidate + dots, size, tracking) <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  std::string prefix = encodeUtf8(points, 0, lo);
  while (!prefix.empty() && (prefix.back() == ' ' || prefix.back() == ',' || prefix.back() == '.')) prefix.pop_back();
  return prefix + dots;
}

std::vector<std::string> wrapText(const Font &font, const std::string &utf8, float size, float maxWidth, int maxLines,
                                  float tracking) {
  std::vector<std::string> lines;
  if (maxLines <= 0) return lines;
  const auto fits = [&](const std::string &s) { return font.measure(s, size, tracking) <= maxWidth; };
  std::vector<std::string> paragraphs;
  {
    std::string current;
    for (const char c : utf8) {
      if (c == '\n') {
        paragraphs.push_back(current);
        current.clear();
      } else current += c;
    }
    paragraphs.push_back(current);
  }
  std::vector<std::string> all;
  for (const auto &paragraph : paragraphs) {
    std::vector<std::string> words;
    std::string word;
    for (const char c : paragraph) {
      if (c == ' ') {
        if (!word.empty()) words.push_back(word);
        word.clear();
      } else word += c;
    }
    if (!word.empty()) words.push_back(word);
    std::string current;
    for (auto w : words) {
      const std::string candidate = current.empty() ? w : current + " " + w;
      if (fits(candidate)) {
        current = candidate;
        continue;
      }
      if (!current.empty()) {
        all.push_back(current);
        current.clear();
      }
      // A word wider than the line is broken between characters.
      while (!fits(w)) {
        const auto points = decodeUtf8(w);
        size_t take = 1;
        while (take < points.size() && fits(encodeUtf8(points, 0, take + 1))) ++take;
        all.push_back(encodeUtf8(points, 0, take));
        w = encodeUtf8(points, take, points.size());
        if (w.empty()) break;
      }
      current = w;
    }
    all.push_back(current);
  }
  while (!all.empty() && all.back().empty()) all.pop_back();
  if (int(all.size()) <= maxLines) return all;
  lines.assign(all.begin(), all.begin() + maxLines);
  std::string rest = lines.back();
  for (size_t i = size_t(maxLines); i < all.size(); ++i) rest += " " + all[i];
  lines.back() = ellipsize(font, rest, size, maxWidth, tracking);
  return lines;
}

}  // namespace vrost::ui
