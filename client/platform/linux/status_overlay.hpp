#pragma once
// In-headset status: head-locked OpenVR overlays that show what is happening while
// there is no fresh video (waking the host, starting SteamVR, reconnecting, errors),
// the frame alignment check, and, when switched on, a live quality pill.
//
// RC4.2: the status is a card in the style of SteamVR's pop-up cards, drawn with the
// VROSTBridge logo and the desktop pages' fonts and colours (client/ui/status_card.hpp),
// and the quality line is a separate pill near the bottom of the view. Drawing a card
// takes several milliseconds, so it happens on a worker thread; only the finished
// image is handed to SteamVR on the render thread. If the fonts or logo are missing,
// the RC4 bitmap panel (client/device/status_text.hpp) is used instead.
#include "client/device/status_text.hpp"
#include "client/ui/status_card.hpp"
#include "core/stream.hpp"
#include "openvr.h"

#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace vrost {

// One head-locked overlay showing an RGBA image at a constant size per pixel.
class HeadLockedPanel {
  vr::IVROverlay *overlay_ = nullptr;
  vr::VROverlayHandle_t handle_ = vr::k_ulOverlayHandleInvalid;
  bool visible_ = false;
  float lower_ = -1;

public:
  static constexpr float depth = 1.6f;            // metres in front of the eyes
  static constexpr float metresPerPixel = 0.0008f;  // per layout pixel: a 900-unit card is about 25 degrees wide

  bool open(vr::IVROverlay *overlay, const char *key, const char *name, uint32_t sortOrder) {
    overlay_ = overlay;
    if (!overlay_) return false;
    const auto error = overlay_->CreateOverlay(key, name, &handle_);
    if (error != vr::VROverlayError_None) {
      diagnosticLine(std::string("DEBUG In-headset ") + name + " unavailable: overlay error " + std::to_string(int(error)));
      overlay_ = nullptr;
      handle_ = vr::k_ulOverlayHandleInvalid;
      return false;
    }
    overlay_->SetOverlaySortOrder(handle_, sortOrder);
    return true;
  }

  bool available() const { return overlay_ && handle_ != vr::k_ulOverlayHandleInvalid; }
  bool visible() const { return visible_; }

  // `lower`: metres below eye level at the panel's depth.
  void place(float lower) {
    if (!available() || lower == lower_) return;
    vr::HmdMatrix34_t pose{{{1, 0, 0, 0}, {0, 1, 0, -lower}, {0, 0, 1, -depth}}};
    overlay_->SetOverlayTransformTrackedDeviceRelative(handle_, vr::k_unTrackedDeviceIndex_Hmd, &pose);
    lower_ = lower;
  }

  // Straight (non-premultiplied) RGBA8. `scale`: image pixels per layout pixel (RC4.4: the
  // cards and the quality pill are drawn at 2, so SteamVR shrinks them instead of enlarging).
  bool show(const std::vector<uint8_t> &rgba, uint32_t width, uint32_t height, float lower, float scale = 1) {
    if (!available() || rgba.size() != size_t(width) * height * 4) return false;
    const auto error = overlay_->SetOverlayRaw(handle_, const_cast<uint8_t *>(rgba.data()), width, height, 4);
    if (error != vr::VROverlayError_None) {
      diagnosticLine("DEBUG In-headset status image rejected: overlay error " + std::to_string(int(error)));
      return false;
    }
    overlay_->SetOverlayWidthInMeters(handle_, float(width) * metresPerPixel / std::max(scale, 1.f));
    place(lower);
    if (!visible_) overlay_->ShowOverlay(handle_);
    visible_ = true;
    return true;
  }

  void hide() {
    if (available() && visible_) overlay_->HideOverlay(handle_);
    visible_ = false;
  }

  void close() {
    if (available()) overlay_->DestroyOverlay(handle_);
    handle_ = vr::k_ulOverlayHandleInvalid;
    overlay_ = nullptr;
    visible_ = false;
    lower_ = -1;
  }

  ~HeadLockedPanel() { close(); }
};

// The RC4 bitmap panel's lines (used only when the card fonts are unavailable).
inline std::vector<std::string> statusLines(const StatusState &s) {
  std::vector<std::string> lines;
  std::string detail;
  if (s.fatal)
    detail = s.error.empty() ? "Video stopped. See the VROSTBridge window." : "Video stopped: " + s.error;
  else if (s.reconnecting)
    detail = "Reconnecting to the host...";
  else if (!s.streaming)
    detail = "Waiting for the host stream...";
  else if (!s.configured)
    detail = "Starting video...";
  else if (!s.fresh && s.staleMs >= 500)
    detail = "Waiting for video (" + std::to_string(s.staleMs / 1000) + " s)";
  if (!detail.empty()) {
    lines.push_back("VROSTBridge");
    if (!s.companion.empty()) lines.push_back(s.companion);
    lines.push_back(detail);
  } else if (s.check) {
    lines.push_back("Frame alignment check");
    lines.push_back("Does the picture jump once a second?");
    lines.push_back(std::to_string((s.checkRemainingMs + 999) / 1000) + " s left");
  }
  if (!s.hud.empty()) {
    if (lines.empty()) lines.push_back("");
    lines.push_back(s.hud);
  }
  return lines;
}

class StatusOverlay {
public:
  static constexpr float centreLower = .10f, lowLower = .30f, pillLower = .50f, bitmapLower = .18f;

  void open(vr::IVROverlay *overlay, const std::filesystem::path &assets) {
    if (!card_.open(overlay, "vrostbridge.status", "VROSTBridge status", 100)) return;
    const auto fonts = assets / "fonts", logo = assets / "hud" / "logo-192.rgba";
    if (!renderer_.load(fonts.string(), logo.string())) {
      diagnosticLine("DEBUG In-headset status card unavailable (" + renderer_.error() + "); using the plain panel");
      return;
    }
    pill_.open(overlay, "vrostbridge.quality", "VROSTBridge quality", 101);
    worker_ = std::thread([this] { work(); });
  }

  bool available() const { return card_.available(); }
  bool styled() const { return renderer_.ready(); }

  // Called once per frame on the render thread; never draws there when styled.
  void update(const StatusState &state) {
    if (!available()) return;
    if (!styled()) {
      showBitmap(statusLines(state));
      return;
    }
    const auto card = ui::statusCard(state);
    const auto pill = ui::hudText(state);
    std::optional<Rendered> cardReady, pillReady;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!card.visible) {
        wantedCard_ = card;
        cardJob_.reset();
        cardResult_.reset();
      } else if (card != wantedCard_) {
        wantedCard_ = card;
        cardJob_ = card;
        wake_.notify_one();
      }
      if (pill != wantedPill_) {
        wantedPill_ = pill;
        pillResult_.reset();
        if (pill.empty()) pillJob_.reset();
        else {
          pillJob_ = pill;
          wake_.notify_one();
        }
      }
      if (cardResult_ && cardResult_->card == wantedCard_) cardReady = std::move(cardResult_);
      cardResult_.reset();
      if (pillResult_ && pillResult_->pill == wantedPill_) pillReady = std::move(pillResult_);
      pillResult_.reset();
    }
    if (!card.visible) card_.hide();
    else if (cardReady)
      card_.show(cardReady->rgba, cardReady->width, cardReady->height,
                 card.placement == ui::CardContent::Placement::Low ? lowLower : centreLower, cardReady->scale);
    if (pill.empty()) pill_.hide();
    else if (pillReady) pill_.show(pillReady->rgba, pillReady->width, pillReady->height, pillLower, pillReady->scale);
  }

  void close() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stopping_ = true;
      wake_.notify_all();
    }
    if (worker_.joinable()) worker_.join();
    card_.close();
    pill_.close();
  }

  ~StatusOverlay() { close(); }

private:
  struct Rendered {
    ui::CardContent card;
    std::string pill;
    std::vector<uint8_t> rgba;
    uint32_t width = 0, height = 0;
    float scale = 1;
  };

  // RC4.4: draw at twice RC4.3's pixels per degree (its pill was magnified by the compositor and
  // looked pixelated), as far as the image stays within the 2 MiB SetOverlayRaw budget the RC4
  // panels kept (OpenVR documents an upper bound without a number).
  static constexpr float preferredScale = 2.f;
  static constexpr size_t rawBudget = 2u * 1024u * 1024u;
  // start: the scale to try first, kept per image kind and updated here, so an image of the same
  // shape as the last one (a card redrawn four times a second during the alignment check) is
  // drawn once; a larger one is drawn again at the scale that fits.
  template <class Draw> static Rendered draw(Draw drawAt, float &start) {
    float scale = start;
    ui::Image image = drawAt(scale);
    const float fitted = ui::fittedScale(image.width(), image.height(), scale, rawBudget);
    if (fitted < scale) image = drawAt(scale = fitted);
    const double bytes = double(image.width()) * double(image.height()) * 4.0;
    start = bytes > 0 ? float(std::clamp(std::floor(double(scale) * std::sqrt(double(rawBudget) / bytes) * 20.0) / 20.0,
                                         1.0, double(preferredScale)))
                      : preferredScale;
    Rendered done;
    done.rgba = image.straightRgba8();
    done.width = uint32_t(image.width());
    done.height = uint32_t(image.height());
    done.scale = scale;
    return done;
  }
  float cardScale_ = preferredScale, pillScale_ = preferredScale;  // worker thread only

  void work() {
    std::unique_lock<std::mutex> lock(mutex_);
    while (!stopping_) {
      wake_.wait(lock, [this] { return stopping_ || cardJob_ || pillJob_; });
      if (stopping_) break;
      if (cardJob_) {
        const ui::CardContent content = *cardJob_;
        cardJob_.reset();
        lock.unlock();
        Rendered done = draw([&](float scale) { return renderer_.card(content, scale); }, cardScale_);
        done.card = content;
        lock.lock();
        if (wantedCard_ == content) cardResult_ = std::move(done);
      } else if (pillJob_) {
        const std::string text = *pillJob_;
        pillJob_.reset();
        lock.unlock();
        Rendered done = draw([&](float scale) { return renderer_.pill(text, scale); }, pillScale_);
        done.pill = text;
        lock.lock();
        if (wantedPill_ == text) pillResult_ = std::move(done);
      }
    }
  }

  void showBitmap(const std::vector<std::string> &lines) {
    bool any = false;
    for (const auto &line : lines) any = any || !line.empty();
    if (!any) {
      card_.hide();
      shownLines_.clear();
      return;
    }
    if (card_.visible() && lines == shownLines_) return;
    const auto image = renderStatus(lines);
    if (card_.show(image.rgba, image.width, image.height, bitmapLower)) shownLines_ = lines;
  }

  HeadLockedPanel card_, pill_;
  ui::StatusCardRenderer renderer_;
  std::vector<std::string> shownLines_;
  std::mutex mutex_;
  std::condition_variable wake_;
  std::thread worker_;
  bool stopping_ = false;
  ui::CardContent wantedCard_;
  std::string wantedPill_;
  std::optional<ui::CardContent> cardJob_;
  std::optional<std::string> pillJob_;
  std::optional<Rendered> cardResult_, pillResult_;
};

}  // namespace vrost
