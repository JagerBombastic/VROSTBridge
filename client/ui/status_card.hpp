#pragma once
// What the headset shows about VROSTBridge, and how it looks.
//
// The in-headset status is a card in the style of SteamVR's own pop-up cards (dark,
// rounded, an icon, a bold title and a grey message) in VROSTBridge's colours: the
// logo, a cyan-to-magenta accent and the desktop pages' fonts (Outfit, Inter).
// The optional quality line is a separate pill near the bottom of the view.
//
// statusCard() decides what to say from the native client's state and the
// companion's latest status line; StatusCardRenderer draws it. Both are independent
// of OpenVR so they are tested on their own (tests/status_card_tests.cpp).
#include "client/ui/raster.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace vrost {

// 1.0.0.5: how long the head pose must be missing before the card says the headset is not
// tracked (brief losses, such as a hand over the headset, stay "Waiting for video").
inline constexpr uint64_t kUntrackedCardMs = 2000;

struct StatusState {
  bool streaming = false, configured = false, fresh = false, fatal = false, reconnecting = false;
  uint64_t staleMs = 0;  // since the last new frame
  uint64_t untrackedMs = 0;  // 1.0.0.5: how long SteamVR here has had no head pose (0 while tracked)
  std::string companion, error, hud;
  bool check = false;  // the frame alignment check is running
  uint64_t checkRemainingMs = 0;
};

namespace ui {

// Brand palette (the same tokens as launcher/desktop.html).
namespace palette {
inline constexpr uint32_t cyan = 0x00E5FF, blue = 0x0085FF, purple = 0xA855FF, magenta = 0xFF2DCB;  // VROSTBridge colours
inline constexpr uint32_t ink = 0x0B0C12, panel = 0x141621, line = 0x262A39, text = 0xF3F5FB, muted = 0xA3ABBF;
}  // namespace palette

struct CardContent {
  enum class Tone { Info, Error };
  enum class Placement { Centre, Low };  // Low keeps the middle of the view clear
  bool visible = false;
  Tone tone = Tone::Info;
  Placement placement = Placement::Centre;
  std::string eyebrow = "VROSTBRIDGE", headline, detail, corner;
  float progress = -1;  // 0..1 draws a progress bar; negative draws none

  bool operator==(const CardContent &o) const {
    return visible == o.visible && tone == o.tone && placement == o.placement && eyebrow == o.eyebrow &&
           headline == o.headline && detail == o.detail && corner == o.corner && progress == o.progress;
  }
  bool operator!=(const CardContent &o) const { return !(*this == o); }
};

// The card for this moment; invisible while video is fresh and no check is running.
CardContent statusCard(const StatusState &state);
// The quality pill's text (empty hides it): the companion's "a | b | c" as "a · b · c".
std::string hudText(const StatusState &state);

// The scale to redraw an RGBA8 image drawn at `scale` (width x height pixels) so it fits in
// `budget` bytes: `scale` when it already fits, else the largest 1/20 step that does (never
// below 1). Image area grows with the square of the scale.
inline float fittedScale(int width, int height, float scale, size_t budget) {
  const double bytes = double(width) * double(height) * 4.0;
  if (bytes <= double(budget) || width <= 0 || height <= 0) return scale;
  const double fitted = std::floor(double(scale) * std::sqrt(double(budget) / bytes) * 20.0) / 20.0;
  return float(std::max(1.0, fitted));
}

class StatusCardRenderer {
public:
  static constexpr int cardWidth = 900;  // layout units (px at scale 1), without the shadow margin
  static constexpr int margin = 24;      // transparent border for the soft shadow

  // fonts: a directory with Inter-Regular/Medium/SemiBold.ttf and Outfit-SemiBold.ttf;
  // logo: a 192 x 192 straight RGBA8 file. False (and not ready) if any is missing.
  bool load(const std::string &fontsDirectory, const std::string &logoPath);
  bool ready() const { return ready_; }
  std::string error() const { return error_; }

  // scale: image pixels per layout unit (1..4). RC4.4 draws the in-headset images at up to 2
  // and shows them at that many times the pixels per degree, so the compositor shrinks the
  // text instead of enlarging it (RC4.3's quality pill looked pixelated).
  Image card(const CardContent &content, float scale = 1) const;
  Image pill(const std::string &text, float scale = 1) const;

private:
  bool ready_ = false;
  std::string error_;
  Font eyebrow_, headline_, body_, medium_;
  Image logo_;  // the 192 x 192 master, resized per image
};

}  // namespace ui
}  // namespace vrost
