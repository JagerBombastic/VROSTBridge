#include "client/ui/status_card.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iterator>

namespace vrost {
namespace ui {

namespace {
std::string trim(std::string s) {
  const auto first = s.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) return {};
  const auto last = s.find_last_not_of(" \t\r\n");
  return s.substr(first, last - first + 1);
}

std::string seconds(uint64_t ms) { return std::to_string((ms + 999) / 1000) + " s"; }

std::vector<std::string> split(const std::string &text, const std::string &separator) {
  std::vector<std::string> parts;
  size_t start = 0;
  while (true) {
    const size_t at = text.find(separator, start);
    parts.push_back(trim(text.substr(start, at == std::string::npos ? std::string::npos : at - start)));
    if (at == std::string::npos) break;
    start = at + separator.size();
  }
  parts.erase(std::remove(parts.begin(), parts.end(), std::string()), parts.end());
  return parts;
}

const std::string dot = "\xC2\xB7";  // U+00B7 middle dot

// A soft shadow under a rounded rectangle; inset: how far inside the edge it is darkest (4 px at
// scale 1). Only the rows and columns it can reach are visited.
void shadow(Image &image, const Rect &rect, float radius, float spread, float strength, float inset) {
  const int x0 = std::max(0, int(std::floor(rect.x - spread))), x1 = std::min(image.width(), int(std::ceil(rect.x + rect.w + spread)));
  const int y0 = std::max(0, int(std::floor(rect.y - spread))), y1 = std::min(image.height(), int(std::ceil(rect.y + rect.h + spread)));
  for (int y = y0; y < y1; ++y)
    for (int x = x0; x < x1; ++x) {
      const float d = roundedRectDistance(float(x) + .5f, float(y) + .5f, rect, radius);
      if (d > spread) continue;
      const float t = std::clamp(1.f - (d + inset) / (spread + inset), 0.f, 1.f);
      image.blend(x, y, rgb(0x000000), strength * t * t);
    }
}
}  // namespace

CardContent statusCard(const StatusState &s) {
  CardContent c;
  const std::string companion = trim(s.companion);
  if (s.fatal) {
    c.visible = true;
    c.tone = CardContent::Tone::Error;
    c.headline = "Video stopped";
    const std::string error = trim(s.error);
    c.detail = error.empty() ? "See the VROSTBridge window on this PC." : error;
  } else if (s.reconnecting) {
    c.visible = true;
    c.headline = "Reconnecting to the host";
    c.detail = companion.empty() ? "The picture comes back on its own when the host is reachable again." : companion;
  } else if (!s.streaming) {
    c.visible = true;
    c.headline = "Waiting for the host";
    c.detail = companion.empty() ? "Starting the stream on the host PC." : companion;
  } else if (s.untrackedMs >= kUntrackedCardMs && (!s.configured || (!s.fresh && s.staleMs >= 500))) {
    // 1.0.0.5: without a head pose the host has nothing to render for, so no video comes.
    c.visible = true;
    c.headline = "Headset not tracked";
    c.corner = std::to_string(s.untrackedMs / 1000) + " s";
    c.detail = "SteamVR on this PC can't see the headset. Check that the base stations are on and the headset is awake.";
  } else if (!s.configured) {
    c.visible = true;
    c.headline = "Starting video";
    c.detail = companion.empty() ? "Waiting for the first picture from the host." : companion;
  } else if (!s.fresh && s.staleMs >= 500) {
    c.visible = true;
    c.headline = "Waiting for video";
    c.corner = std::to_string(s.staleMs / 1000) + " s";
    c.detail = companion.empty() ? "No new frames are arriving. The last picture is held until they do." : companion;
  } else if (s.check) {
    c.visible = true;
    c.placement = CardContent::Placement::Low;
    c.eyebrow = "FRAME ALIGNMENT CHECK";
    c.headline = "Watch something that stays still";
    c.detail = "Does the picture jump sideways once a second? Answer on the VROSTBridge page.";
    const uint64_t remaining = std::min<uint64_t>(s.checkRemainingMs, 10000);
    c.corner = seconds(remaining);
    // Quantised so the card is redrawn a few times a second, not every frame.
    c.progress = std::round((1.f - float(remaining) / 10000.f) * 40.f) / 40.f;
  }
  return c;
}

std::string hudText(const StatusState &s) {
  std::string out;
  for (const auto &part : split(s.hud, "|")) out += (out.empty() ? "" : " " + dot + " ") + part;
  return out;
}

bool StatusCardRenderer::load(const std::string &fonts, const std::string &logoPath) {
  ready_ = false;
  error_.clear();
  const auto font = [&](Font &f, const char *name) {
    if (!f.load(fonts + "/" + name)) error_ += std::string(error_.empty() ? "" : ", ") + name;
  };
  font(eyebrow_, "Outfit-SemiBold.ttf");
  font(headline_, "Inter-SemiBold.ttf");
  font(body_, "Inter-Regular.ttf");
  font(medium_, "Inter-Medium.ttf");
  std::ifstream stream(logoPath, std::ios::binary);
  std::vector<uint8_t> logo((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
  if (logo.size() != 192u * 192u * 4u) error_ += std::string(error_.empty() ? "" : ", ") + "logo";
  if (!error_.empty()) {
    error_ = "missing or unreadable: " + error_;
    return false;
  }
  logo_ = Image::fromStraightRgba8(logo.data(), 192, 192);
  // Light text on a dark card: a touch more weight than linear coverage gives.
  for (Font *f : {&eyebrow_, &headline_, &body_, &medium_}) f->setCoverageGamma(.86f);
  ready_ = true;
  return true;
}

Image StatusCardRenderer::card(const CardContent &content, float scale) const {
  const float k = std::clamp(scale, 1.f, 4.f);  // pixels per layout unit
  const int logoPixels = int(std::lround(100 * k));
  const Image logo = logo_.resized(logoPixels, logoPixels);
  const float pad = 36 * k, logoSize = float(logoPixels), gap = 30 * k, radius = 28 * k;
  const float eyebrowSize = 22 * k, headlineSize = 42 * k, bodySize = 30 * k, bodyLine = 40 * k;
  const float width = std::round(cardWidth * k), edge = std::round(margin * k);
  const float column = pad + logoSize + gap, columnWidth = width - column - pad;
  const bool error = content.tone == CardContent::Tone::Error;

  // Layout first (the text decides the height).
  const auto eyebrowMetrics = eyebrow_.metrics(eyebrowSize), headlineMetrics = headline_.metrics(headlineSize);
  const float top = pad + 6 * k;
  const float eyebrowBaseline = top + eyebrowMetrics.capHeight;
  const float headlineBaseline = eyebrowBaseline + 22 * k + headlineMetrics.capHeight;
  const auto details = wrapText(body_, content.detail, bodySize, columnWidth, 3);
  float last = headlineBaseline;
  std::vector<float> detailBaselines;
  for (size_t i = 0; i < details.size(); ++i) {
    last = headlineBaseline + 48 * k + float(i) * bodyLine;
    detailBaselines.push_back(last);
  }
  float bottom = last + 10 * k;
  const float barTop = last + 32 * k;
  if (content.progress >= 0) bottom = barTop + 8 * k;
  const float height = std::max(bottom, top + logoSize) + pad;

  Image image(int(width + 2 * edge), int(std::ceil(height) + 2 * edge));
  const Rect card{edge, edge, width, std::ceil(height)};
  shadow(image, {card.x, card.y + 6 * k, card.w, card.h}, radius, 18 * k, .38f, 4 * k);
  fillRoundedRect(image, card, radius, {{0, rgb(0x1A1D2A, .97f)}, {1, rgb(0x12141C, .97f)}}, true);
  strokeRoundedRect(image, card, radius, 2 * k, rgb(0x2B3044, .95f));
  const std::vector<GradientStop> accent =
      error ? std::vector<GradientStop>{{0, rgb(palette::magenta)}, {1, rgb(palette::purple)}}
            : std::vector<GradientStop>{{0, rgb(palette::cyan)}, {.33f, rgb(palette::blue)}, {.66f, rgb(palette::purple)},
                                        {1, rgb(palette::magenta)}};
  fillRoundedRect(image, card, radius, accent, false, {card.x, card.y, card.w, 6 * k});

  // The logo sits level with the middle of the text.
  const float textMiddle = (top - 2 * k + bottom) / 2;
  drawImage(image, logo, card.x + pad, card.y + std::max(top, textMiddle - logoSize / 2));
  const float x = card.x + column, right = card.x + width - pad;
  float eyebrowWidth = columnWidth;
  if (!content.corner.empty()) {
    const float w = medium_.measure(content.corner, 24 * k);
    medium_.draw(image, content.corner, right - w, card.y + eyebrowBaseline, 24 * k, rgb(palette::muted));
    eyebrowWidth -= w + 24 * k;
  }
  const std::string eyebrow = ellipsize(eyebrow_, content.eyebrow, eyebrowSize, eyebrowWidth, 2.6f * k);
  eyebrow_.draw(image, eyebrow, x, card.y + eyebrowBaseline, eyebrowSize, rgb(error ? palette::magenta : palette::cyan), 2.6f * k);
  headline_.draw(image, ellipsize(headline_, content.headline, headlineSize, columnWidth), x, card.y + headlineBaseline,
                 headlineSize, rgb(palette::text));
  for (size_t i = 0; i < details.size(); ++i)
    body_.draw(image, details[i], x, card.y + detailBaselines[i], bodySize, rgb(palette::muted));
  if (content.progress >= 0) {
    const Rect track{x, card.y + barTop, columnWidth, 8 * k};
    fillRoundedRect(image, track, 4 * k, rgb(palette::line));
    const float filled = std::clamp(content.progress, 0.f, 1.f) * columnWidth;
    if (filled >= 1)
      fillRoundedRect(image, {x, track.y, std::max(filled, 8 * k), 8 * k}, 4 * k,
                      std::vector<GradientStop>{{0, rgb(palette::cyan)}, {1, rgb(palette::magenta)}});
  }
  return image;
}

Image StatusCardRenderer::pill(const std::string &textLine, float scale) const {
  const float k = std::clamp(scale, 1.f, 4.f);  // pixels per layout unit
  const int logoPixels = int(std::lround(34 * k));
  const Image logoSmall = logo_.resized(logoPixels, logoPixels);
  const float height = 62 * k, pad = 16 * k, logo = float(logoPixels), afterLogo = 14 * k, endPad = 28 * k,
              size = 26 * k, spacing = 14 * k;
  const int pillMargin = int(std::lround(14 * k));
  const auto parts = split(textLine, dot);
  const float dotWidth = medium_.measure(dot, size);
  float textWidth = 0;
  for (size_t i = 0; i < parts.size(); ++i)
    textWidth += medium_.measure(parts[i], size) + (i ? 2 * spacing + dotWidth : 0);
  textWidth = std::min(textWidth, 1400 * k);
  const float width = std::ceil(pad + logo + afterLogo + textWidth + endPad);
  Image image(int(width) + 2 * pillMargin, int(height) + 2 * pillMargin);
  const Rect pill{float(pillMargin), float(pillMargin), width, height};
  shadow(image, {pill.x, pill.y + 4 * k, pill.w, pill.h}, height / 2, 10 * k, .32f, 4 * k);
  fillRoundedRect(image, pill, height / 2, rgb(0x0E1019, .9f));
  strokeRoundedRect(image, pill, height / 2, 1.5f * k, rgb(0x2B3044, .95f));
  drawImage(image, logoSmall, pill.x + pad, pill.y + (height - logo) / 2);
  const auto m = medium_.metrics(size);
  const float baseline = pill.y + height / 2 + m.capHeight / 2;
  float x = pill.x + pad + logo + afterLogo;
  const float limit = pill.x + width - endPad;
  for (size_t i = 0; i < parts.size() && x < limit; ++i) {
    if (i) {
      x += spacing;
      x = medium_.draw(image, dot, x, baseline, size, rgb(palette::muted)) + spacing;
    }
    const std::string part = ellipsize(medium_, parts[i], size, limit - x);
    x = medium_.draw(image, part, x, baseline, size, rgb(palette::text));
  }
  return image;
}

}  // namespace ui
}  // namespace vrost
