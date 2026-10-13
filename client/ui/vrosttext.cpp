// libvrosttext.so: the C ABI the Game Mode screen (launcher/game_ui.py, through ctypes)
// uses to draw crisp interface images at the screen's own resolution: TrueType text,
// anti-aliased rounded panels, the logo, and JPEG/PNG artwork. Every function draws
// into or returns a straight (non-premultiplied) RGBA8 buffer that the caller turns
// into an SDL texture. All sizes are pixels; colours are 0xRRGGBBAA.
//
// Handles are opaque; a font is safe to use from one thread at a time per image.
#include "client/ui/raster.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#define STBI_ONLY_PNG
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wimplicit-fallthrough"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#endif
#include "third_party/stb/stb_image.h"
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#define VROSTTEXT_API extern "C" __attribute__((visibility("default")))

using vrost::ui::Color;
using vrost::ui::Font;
using vrost::ui::Image;

namespace {
Color color(uint32_t rgba) {
  return {float((rgba >> 24) & 255) / 255.f, float((rgba >> 16) & 255) / 255.f, float((rgba >> 8) & 255) / 255.f,
          float(rgba & 255) / 255.f};
}

// A drawing surface owned by the library; exported as straight RGBA8 on demand.
struct Canvas {
  Image image;
  std::vector<uint8_t> exported;
};
}  // namespace

VROSTTEXT_API int vrosttext_version() { return 1; }

VROSTTEXT_API void *vrosttext_font_open(const char *path) {
  auto font = std::make_unique<Font>();
  if (!path || !font->load(path)) return nullptr;
  font->setCoverageGamma(.9f);
  return font.release();
}

VROSTTEXT_API void vrosttext_font_close(void *font) { delete static_cast<Font *>(font); }

VROSTTEXT_API float vrosttext_measure(void *font, const char *utf8, float size, float tracking) {
  if (!font || !utf8) return 0;
  return static_cast<Font *>(font)->measure(utf8, size, tracking);
}

// ascent, descent (negative), cap height, line gap in px.
VROSTTEXT_API void vrosttext_metrics(void *font, float size, float *out4) {
  if (!font || !out4) return;
  const auto m = static_cast<Font *>(font)->metrics(size);
  out4[0] = m.ascent;
  out4[1] = m.descent;
  out4[2] = m.capHeight;
  out4[3] = m.lineGap;
}

// Shortens to fit `max_width` with an ellipsis. Writes a NUL-terminated string.
VROSTTEXT_API int vrosttext_ellipsize(void *font, const char *utf8, float size, float max_width, char *out, int capacity) {
  if (!font || !utf8 || !out || capacity <= 0) return 0;
  const std::string fitted = vrost::ui::ellipsize(*static_cast<Font *>(font), utf8, size, max_width);
  const int n = std::min<int>(int(fitted.size()), capacity - 1);
  std::memcpy(out, fitted.data(), size_t(n));
  out[n] = 0;
  return n;
}

// Word-wraps into lines separated by '\n' (at most max_lines, the last ellipsised).
VROSTTEXT_API int vrosttext_wrap(void *font, const char *utf8, float size, float max_width, int max_lines, char *out,
                           int capacity) {
  if (!font || !utf8 || !out || capacity <= 0) return 0;
  std::string joined;
  for (const auto &line : vrost::ui::wrapText(*static_cast<Font *>(font), utf8, size, max_width, max_lines))
    joined += (joined.empty() ? "" : "\n") + line;
  const int n = std::min<int>(int(joined.size()), capacity - 1);
  std::memcpy(out, joined.data(), size_t(n));
  out[n] = 0;
  return n;
}

VROSTTEXT_API void *vrosttext_canvas(int width, int height) {
  if (width <= 0 || height <= 0 || width > 16384 || height > 16384) return nullptr;
  auto canvas = std::make_unique<Canvas>();
  canvas->image = Image(width, height);
  return canvas.release();
}

VROSTTEXT_API void vrosttext_canvas_free(void *canvas) { delete static_cast<Canvas *>(canvas); }

VROSTTEXT_API void vrosttext_clear(void *canvas, uint32_t rgba) {
  if (canvas) static_cast<Canvas *>(canvas)->image.clear(color(rgba));
}

VROSTTEXT_API float vrosttext_draw(void *canvas, void *font, const char *utf8, float x, float baseline, float size,
                             uint32_t rgba, float tracking) {
  if (!canvas || !font || !utf8) return x;
  return static_cast<Font *>(font)->draw(static_cast<Canvas *>(canvas)->image, utf8, x, baseline, size, color(rgba),
                                         tracking);
}

VROSTTEXT_API void vrosttext_rounded_rect(void *canvas, float x, float y, float w, float h, float radius, uint32_t rgba) {
  if (canvas) vrost::ui::fillRoundedRect(static_cast<Canvas *>(canvas)->image, {x, y, w, h}, radius, color(rgba));
}

// Horizontal (vertical = 0) or vertical gradient between two colours.
VROSTTEXT_API void vrosttext_gradient_rect(void *canvas, float x, float y, float w, float h, float radius, uint32_t from,
                                     uint32_t to, int vertical) {
  if (canvas)
    vrost::ui::fillRoundedRect(static_cast<Canvas *>(canvas)->image, {x, y, w, h}, radius,
                            std::vector<vrost::ui::GradientStop>{{0, color(from)}, {1, color(to)}}, vertical != 0);
}

// The VROSTBridge accent: cyan, blue, purple, magenta (left to right), clipped to
// the band [clip_y, clip_y + clip_h) of the rounded rectangle.
VROSTTEXT_API void vrosttext_accent_rect(void *canvas, float x, float y, float w, float h, float radius, float clip_y,
                                   float clip_h) {
  if (!canvas) return;
  using vrost::ui::rgb;
  vrost::ui::fillRoundedRect(static_cast<Canvas *>(canvas)->image, {x, y, w, h}, radius,
                          {{0, rgb(0x00E5FF)}, {.33f, rgb(0x0085FF)}, {.66f, rgb(0xA855FF)}, {1, rgb(0xFF2DCB)}}, false,
                          {x, clip_y, w, clip_h});
}

VROSTTEXT_API void vrosttext_stroke_rect(void *canvas, float x, float y, float w, float h, float radius, float width,
                                   uint32_t rgba) {
  if (canvas) vrost::ui::strokeRoundedRect(static_cast<Canvas *>(canvas)->image, {x, y, w, h}, radius, width, color(rgba));
}

VROSTTEXT_API void vrosttext_circle(void *canvas, float cx, float cy, float radius, uint32_t rgba) {
  if (canvas) vrost::ui::fillCircle(static_cast<Canvas *>(canvas)->image, cx, cy, radius, color(rgba));
}

// Draws straight RGBA8 pixels (width x height), scaled to w x h with an area filter.
VROSTTEXT_API void vrosttext_image(void *canvas, const uint8_t *rgba, int width, int height, float x, float y, int w, int h,
                             float opacity) {
  if (!canvas || !rgba || width <= 0 || height <= 0 || w <= 0 || h <= 0) return;
  Image source = Image::fromStraightRgba8(rgba, width, height);
  if (w != width || h != height) source = source.resized(w, h);
  vrost::ui::drawImage(static_cast<Canvas *>(canvas)->image, source, x, y, opacity);
}

// Straight RGBA8 of the canvas; valid until the next call for this canvas.
VROSTTEXT_API const uint8_t *vrosttext_pixels(void *canvas) {
  if (!canvas) return nullptr;
  auto *c = static_cast<Canvas *>(canvas);
  c->exported = c->image.straightRgba8();
  return c->exported.data();
}

// Decodes a JPEG or PNG file to straight RGBA8, scaled (area filter) to fit inside
// max_w x max_h when it is larger (0 = no limit). Free with vrosttext_free_pixels.
VROSTTEXT_API uint8_t *vrosttext_load_image(const char *path, int max_w, int max_h, int *out_w, int *out_h) {
  if (!path || !out_w || !out_h) return nullptr;
  int w = 0, h = 0, channels = 0;
  stbi_uc *data = stbi_load(path, &w, &h, &channels, 4);
  if (!data) return nullptr;
  int tw = w, th = h;
  if (max_w > 0 && max_h > 0 && (w > max_w || h > max_h)) {
    const double scale = std::min(double(max_w) / w, double(max_h) / h);
    tw = std::max(1, int(w * scale + .5));
    th = std::max(1, int(h * scale + .5));
  }
  std::vector<uint8_t> pixels;
  if (tw != w || th != h) pixels = Image::fromStraightRgba8(data, w, h).resized(tw, th).straightRgba8();
  else pixels.assign(data, data + size_t(w) * size_t(h) * 4);
  stbi_image_free(data);
  auto *out = static_cast<uint8_t *>(std::malloc(pixels.size()));
  if (!out) return nullptr;
  std::memcpy(out, pixels.data(), pixels.size());
  *out_w = tw;
  *out_h = th;
  return out;
}

VROSTTEXT_API void vrosttext_free_pixels(uint8_t *pixels) { std::free(pixels); }
