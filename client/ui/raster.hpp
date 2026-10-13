#pragma once
// A small CPU raster for VROSTBridge's own interface images: the in-headset status
// card and quality pill (client/ui/status_card.hpp) and the Game Mode screen
// (libvrosttext.so, used by launcher/game_ui.py).
//
// - Images are premultiplied float RGBA in sRGB-encoded values (0..1), composited
//   source-over. straightRgba8() exports straight (non-premultiplied) 8-bit RGBA,
//   which is what OpenVR overlays (without VROverlayFlags_IsPremultiplied) and SDL
//   blended textures expect.
// - Shapes are anti-aliased from their signed distance (one pixel wide).
// - Text is TrueType (Inter, Outfit), rasterised by stb_truetype with kerning and
//   quarter-pixel horizontal positioning. Code points a font lacks fall back to the
//   ASCII spellings of client/device/status_text.hpp ("…" -> "...").
//
// Not thread-safe per object; Font caches glyph bitmaps internally under a mutex so
// one font may be shared by threads that each draw into their own Image.
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace vrost::ui {

struct Color {
  float r = 0, g = 0, b = 0, a = 1;  // straight alpha, sRGB-encoded 0..1
};

constexpr Color rgb(uint32_t hex, float alpha = 1) {
  return {float((hex >> 16) & 255) / 255.f, float((hex >> 8) & 255) / 255.f, float(hex & 255) / 255.f, alpha};
}

struct GradientStop {
  float at = 0;  // 0..1 along the gradient
  Color color;
};

struct Rect {
  float x = 0, y = 0, w = 0, h = 0;
};

class Image {
public:
  Image() = default;
  Image(int width, int height);
  int width() const { return width_; }
  int height() const { return height_; }
  bool empty() const { return width_ <= 0 || height_ <= 0; }
  float *pixel(int x, int y) { return &data_[(size_t(y) * size_t(width_) + size_t(x)) * 4]; }
  const float *pixel(int x, int y) const { return &data_[(size_t(y) * size_t(width_) + size_t(x)) * 4]; }
  // Source-over of a straight colour at a coverage of 0..1.
  void blend(int x, int y, const Color &color, float coverage);
  // Source-over of a premultiplied pixel scaled by `opacity`.
  void blendPremultiplied(int x, int y, const float *source, float opacity);
  void clear(const Color &color);
  // Straight 8-bit RGBA, row-major, top-left origin, no padding. Transparent
  // pixels keep the colour of their nearest visible neighbour along the row so that
  // bilinear filtering never pulls dark fringes into anti-aliased edges.
  std::vector<uint8_t> straightRgba8() const;
  static Image fromStraightRgba8(const uint8_t *data, int width, int height);
  // Area-filtered resize (good for downscaling the logo).
  Image resized(int width, int height) const;

private:
  int width_ = 0, height_ = 0;
  std::vector<float> data_;
};

// Signed distance (px, negative inside) from point (px, py) to a rounded rectangle.
float roundedRectDistance(float px, float py, const Rect &rect, float radius);

void fillRoundedRect(Image &image, const Rect &rect, float radius, const Color &color);
// Linear gradient across the rectangle (left to right, or top to bottom). Pixels are
// limited to `clip` when it has a positive size.
void fillRoundedRect(Image &image, const Rect &rect, float radius, const std::vector<GradientStop> &stops,
                     bool vertical = false, const Rect &clip = {});
void strokeRoundedRect(Image &image, const Rect &rect, float radius, float width, const Color &color);
void fillCircle(Image &image, float cx, float cy, float radius, const Color &color);
// A soft round glow (Gaussian falloff) of `color` centred at (cx, cy).
void glow(Image &image, float cx, float cy, float radius, const Color &color);
// Blits `source` (premultiplied) with its top-left at the nearest pixel to (x, y).
void drawImage(Image &target, const Image &source, float x, float y, float opacity = 1);

Color mix(const Color &a, const Color &b, float t);
Color gradientAt(const std::vector<GradientStop> &stops, float t);

// UTF-8 to code points; each ill-formed sequence becomes U+FFFD.
std::vector<uint32_t> decodeUtf8(const std::string &text);

class Font {
public:
  Font();
  ~Font();
  Font(Font &&) noexcept;
  Font &operator=(Font &&) noexcept;
  Font(const Font &) = delete;
  Font &operator=(const Font &) = delete;

  bool load(const std::string &path);  // false when the file is missing or not a font
  bool loadMemory(std::vector<uint8_t> bytes);
  bool loaded() const;

  struct Metrics {
    float ascent = 0, descent = 0, lineGap = 0, capHeight = 0;  // px; descent is negative
  };
  // `size` is the em size in pixels (CSS font-size).
  Metrics metrics(float size) const;
  float measure(const std::string &utf8, float size, float tracking = 0) const;
  // Draws with the baseline at `baseline` (rounded to a whole pixel) and returns the
  // pen position after the last glyph. `tracking` is extra space between characters.
  float draw(Image &image, const std::string &utf8, float x, float baseline, float size, const Color &color,
             float tracking = 0) const;
  // Exponent applied to glyph coverage (below 1 thickens light-on-dark text slightly).
  void setCoverageGamma(float gamma);

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

// Greedy word wrap into at most `maxLines` lines of at most `maxWidth` px; text that
// does not fit ends the last line with an ellipsis. Explicit newlines start new lines.
std::vector<std::string> wrapText(const Font &font, const std::string &utf8, float size, float maxWidth,
                                  int maxLines, float tracking = 0);
// The longest prefix that fits in `maxWidth` px followed by "…" (or the text itself).
std::string ellipsize(const Font &font, const std::string &utf8, float size, float maxWidth, float tracking = 0);

}  // namespace vrost::ui
