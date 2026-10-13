// Contracts for the in-headset status card and quality pill (client/ui/status_card.hpp)
// and the raster underneath them (client/ui/raster.hpp).
//
//   status_card_tests <fonts directory> <logo-192.rgba> [sample output directory]
//
// With an output directory, writes PPM samples composited over a mid-grey scene.
#include "client/ui/status_card.hpp"

#include <cmath>
#include <cstdlib>
#include <cstdio>
#include <fstream>
#include <string>

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

using namespace vrost;
using namespace vrost::ui;

void content() {
  StatusState s;
  s.streaming = s.configured = s.fresh = true;
  CHECK(!statusCard(s).visible, "fresh video shows no card");
  s.hud = "89.9 fps | latency 42.5 ms | lost 2";
  CHECK(hudText(s) == "89.9 fps \xC2\xB7 latency 42.5 ms \xC2\xB7 lost 2", "hud text: %s", hudText(s).c_str());
  s.hud = " | ";
  CHECK(hudText(s).empty(), "empty segments hide the pill");

  StatusState r;
  r.streaming = true;
  r.reconnecting = true;
  auto c = statusCard(r);
  CHECK(c.visible && c.headline == "Reconnecting to the host" && c.tone == CardContent::Tone::Info, "reconnecting");
  r.companion = "  Waking the host PC\xE2\x80\xA6  ";
  CHECK(statusCard(r).detail == "Waking the host PC\xE2\x80\xA6", "companion line is the detail: %s",
        statusCard(r).detail.c_str());

  StatusState f;
  f.fatal = true;
  f.error = "Decoder failed";
  c = statusCard(f);
  CHECK(c.visible && c.tone == CardContent::Tone::Error && c.detail == "Decoder failed", "fatal");

  StatusState w;
  w.streaming = w.configured = true;
  w.staleMs = 2300;
  c = statusCard(w);
  CHECK(c.visible && c.headline == "Waiting for video" && c.corner == "2 s", "stale video: %s", c.corner.c_str());
  w.staleMs = 200;
  CHECK(!statusCard(w).visible, "a short gap shows nothing");

  StatusState k;
  k.streaming = k.configured = k.fresh = true;
  k.check = true;
  k.checkRemainingMs = 7400;
  c = statusCard(k);
  CHECK(c.visible && c.placement == CardContent::Placement::Low && c.corner == "8 s", "check card: %s", c.corner.c_str());
  CHECK(std::fabs(c.progress - .25f) < .03f, "check progress %f", c.progress);
  k.checkRemainingMs = 7390;
  CHECK(statusCard(k) == c, "progress is quantised so the card is not redrawn every frame");
  // Problems outrank the check.
  k.fresh = false;
  k.staleMs = 3000;
  CHECK(statusCard(k).headline == "Waiting for video", "stale video outranks the check");

  // 1.0.0.5: SteamVR here has no head pose, so the host has nothing to render for. Said once
  // it has lasted kUntrackedCardMs, before the first picture or when the video has stopped.
  StatusState u;
  u.streaming = true;
  u.untrackedMs = 1500;
  CHECK(statusCard(u).headline == "Starting video", "a short loss of tracking is still starting video");
  u.untrackedMs = 4200;
  c = statusCard(u);
  CHECK(c.visible && c.headline == "Headset not tracked" && c.corner == "4 s" &&
            c.detail.find("base stations") != std::string::npos,
        "untracked before the first picture: %s / %s", c.headline.c_str(), c.corner.c_str());
  u.configured = true;
  u.staleMs = 3000;
  CHECK(statusCard(u).headline == "Headset not tracked", "untracked while the video has stopped");
  u.fresh = true;
  u.staleMs = 0;
  CHECK(!statusCard(u).visible, "fresh video shows no card even if a pose was briefly missing");
  u.fresh = false;
  u.streaming = false;
  CHECK(statusCard(u).headline == "Waiting for the host", "no stream: waiting for the host");
  u.streaming = true;
  u.reconnecting = true;
  CHECK(statusCard(u).headline == "Reconnecting to the host", "reconnecting outranks tracking");
}

void text(const std::string &fonts) {
  Font font;
  CHECK(!font.load(fonts + "/missing.ttf"), "missing font");
  CHECK(font.load(fonts + "/Inter-Regular.ttf"), "Inter loads");
  if (!font.loaded()) return;
  const auto m = font.metrics(28);
  CHECK(m.ascent > 20 && m.ascent < 32 && m.descent < 0 && m.capHeight > 17 && m.capHeight < 23,
        "metrics %f %f %f", m.ascent, m.descent, m.capHeight);
  const float a = font.measure("VROSTBridge", 28), b = font.measure("VROSTBridge", 56);
  CHECK(a > 100 && std::fabs(b - 2 * a) < 2, "measure scales with size: %f %f", a, b);
  CHECK(font.measure("AV", 40) < font.measure("A", 40) + font.measure("V", 40) - .5f, "kerning applies");
  CHECK(font.measure("ab", 28, 3) > font.measure("ab", 28) + 2.5f, "tracking adds space");
  // A code point the font lacks falls back to its ASCII spelling or '?'.
  CHECK(std::fabs(font.measure("\xE4\xB8\xAD", 28) - font.measure("?", 28)) < .01f, "missing glyph -> ?");
  const auto lines = wrapText(font, "The picture comes back on its own when the host is reachable again.", 28, 300, 2);
  CHECK(lines.size() == 2, "wrapped into %zu lines", lines.size());
  for (const auto &line : lines) CHECK(font.measure(line, 28) <= 300.5f, "line fits: %s", line.c_str());
  CHECK(!lines.empty() && lines.back().size() >= 3 && lines.back().substr(lines.back().size() - 3) == "\xE2\x80\xA6",
        "overflow ends with an ellipsis: %s", lines.empty() ? "" : lines.back().c_str());
  CHECK(wrapText(font, "Short", 28, 300, 3).size() == 1, "short text stays on one line");
  CHECK(wrapText(font, "Supercalifragilisticexpialidocious", 28, 120, 4).size() >= 3, "long words break");
  CHECK(ellipsize(font, "Fits", 28, 300) == "Fits", "no ellipsis when it fits");

  Image image(200, 60);
  font.draw(image, "Hg", 10, 40, 28, rgb(0xFFFFFF));
  float ink = 0;
  for (int y = 0; y < 60; ++y)
    for (int x = 0; x < 200; ++x) ink += image.pixel(x, y)[3];
  CHECK(ink > 150 && ink < 1500, "glyph ink %f", ink);
}

void raster() {
  Image image(8, 1);
  image.blend(4, 0, rgb(0xFF0000), 1);
  const auto rgba = image.straightRgba8();
  CHECK(rgba[4 * 4] == 255 && rgba[4 * 4 + 3] == 255, "opaque red");
  CHECK(rgba[0] == 255 && rgba[3] == 0 && rgba[7 * 4] == 255 && rgba[7 * 4 + 3] == 0,
        "transparent pixels keep the neighbour colour (no dark fringes)");
  Image box(20, 20);
  fillRoundedRect(box, {2, 2, 16, 16}, 6, rgb(0x00FF00));
  CHECK(box.pixel(10, 10)[3] > .99f && box.pixel(2, 2)[3] < .2f && box.pixel(0, 10)[3] == 0, "rounded rect");
  const Image half = box.resized(10, 10);
  CHECK(half.width() == 10 && half.pixel(5, 5)[3] > .99f, "resize keeps the interior");
}

void writePpm(const std::string &path, const Image &image) {
  std::ofstream out(path, std::ios::binary);
  out << "P6\n" << image.width() << " " << image.height() << "\n255\n";
  const auto rgba = image.straightRgba8();
  for (int y = 0; y < image.height(); ++y)
    for (int x = 0; x < image.width(); ++x) {
      const uint8_t *p = &rgba[(size_t(y) * size_t(image.width()) + size_t(x)) * 4];
      // Over a checker of two mid greys, like a busy VR scene.
      const float scene = ((x / 24 + y / 24) % 2) ? 96.f : 140.f;
      for (int c = 0; c < 3; ++c) out.put(char(std::lround(p[c] * (p[3] / 255.f) + scene * (1 - p[3] / 255.f))));
    }
}

void cards(const std::string &fonts, const std::string &logo, const std::string &out) {
  StatusCardRenderer renderer;
  CHECK(!renderer.load(fonts, logo + ".missing") && !renderer.ready(), "a missing logo is reported");
  CHECK(renderer.load(fonts, logo) && renderer.ready(), "renderer loads: %s", renderer.error().c_str());
  if (!renderer.ready()) return;
  StatusState s;
  s.streaming = true;
  s.reconnecting = true;
  s.companion = "Wi-Fi signal dropped; reconnecting to 192.168.1.20 (attempt 3)";
  const Image reconnect = renderer.card(statusCard(s));
  CHECK(reconnect.width() == StatusCardRenderer::cardWidth + 2 * StatusCardRenderer::margin, "card width %d",
        reconnect.width());
  CHECK(reconnect.height() > 200 && reconnect.height() < 420, "card height %d", reconnect.height());
  CHECK(reconnect.pixel(0, 0)[3] == 0, "transparent margin");
  CHECK(reconnect.width() * reconnect.height() * 4 <= 2 * 1024 * 1024, "within the SetOverlayRaw budget");
  const int mid = reconnect.height() / 2;
  CHECK(reconnect.pixel(StatusCardRenderer::margin + 10, mid)[3] > .9f, "card body is nearly opaque");

  StatusState f;
  f.fatal = true;
  f.error = "The host stopped sending video and SteamVR on the host exited with code 1 after the "
            "encoder failed to start; see the host log for details and restart the session.";
  const Image fatal = renderer.card(statusCard(f));
  CHECK(fatal.height() > reconnect.height() - 60, "long errors wrap");

  StatusState k;
  k.streaming = k.configured = k.fresh = true;
  k.check = true;
  k.checkRemainingMs = 6200;
  const Image check = renderer.card(statusCard(k));

  StatusState u;
  u.streaming = true;
  u.untrackedMs = 6000;
  const Image untracked = renderer.card(statusCard(u));
  CHECK(untracked.height() > 200 && untracked.height() < 420, "untracked card height %d", untracked.height());

  StatusState h;
  h.hud = "89.9 fps | latency 42.5 ms | frame age 31 ms | lost 2 | Fov: gaze";
  const Image pill = renderer.pill(hudText(h));
  CHECK(pill.height() < 100 && pill.width() > 400 && pill.width() < 1500, "pill %d x %d", pill.width(), pill.height());

  // RC4.4: the headset gets twice the pixels per degree (RC4.3's pill looked pixelated): the
  // same layout at scale 2, and the card shrinks its scale only as far as the 2 MiB raw budget needs.
  const Image pill2 = renderer.pill(hudText(h), 2.f);
  CHECK(std::abs(pill2.width() - 2 * pill.width()) <= 4 && std::abs(pill2.height() - 2 * pill.height()) <= 2,
        "pill at scale 2: %d x %d (scale 1: %d x %d)", pill2.width(), pill2.height(), pill.width(), pill.height());
  CHECK(size_t(pill2.width()) * pill2.height() * 4 <= 2 * 1024 * 1024, "the pill fits the raw budget at scale 2");
  CHECK(ui::fittedScale(pill2.width(), pill2.height(), 2.f, 2 * 1024 * 1024) == 2.f, "the pill keeps scale 2");
  const Image reconnect2 = renderer.card(statusCard(s), 2.f);
  CHECK(reconnect2.width() == 2 * (StatusCardRenderer::cardWidth + 2 * StatusCardRenderer::margin), "card width %d at scale 2",
        reconnect2.width());
  const float fitted = ui::fittedScale(reconnect2.width(), reconnect2.height(), 2.f, 2 * 1024 * 1024);
  const Image reconnectFitted = renderer.card(statusCard(s), fitted);
  CHECK(fitted > 1.2f && fitted <= 2.f && size_t(reconnectFitted.width()) * reconnectFitted.height() * 4 <= 2 * 1024 * 1024,
        "card fitted at %.2f: %d x %d", fitted, reconnectFitted.width(), reconnectFitted.height());
  CHECK(ui::fittedScale(100, 100, 2.f, 1) == 1.f && ui::fittedScale(0, 0, 2.f, 1) == 2.f, "fitted scale limits");
  // Finer glyph edges: more partially covered pixels per unit of text at scale 2 than at 1.
  auto softEdges = [](const Image &img) {
    long edges = 0;
    for (int y = 0; y < img.height(); ++y)
      for (int x = 0; x < img.width(); ++x) {
        const auto *p = img.pixel(x, y);
        if (p[3] > .9f && p[0] > .25f && p[0] < .75f) ++edges;
      }
    return edges;
  };
  CHECK(softEdges(pill2) * 2 > softEdges(pill) * 3, "glyph edges are drawn at the higher resolution");
  if (!out.empty()) {
    writePpm(out + "/status_card_reconnect.ppm", reconnect);
    writePpm(out + "/status_card_error.ppm", fatal);
    writePpm(out + "/status_card_check.ppm", check);
    writePpm(out + "/status_card_untracked.ppm", untracked);
    writePpm(out + "/status_card_pill.ppm", pill);
    writePpm(out + "/status_card_pill_x2.ppm", pill2);
    writePpm(out + "/status_card_reconnect_fitted.ppm", reconnectFitted);
  }
}
}  // namespace

int main(int argc, char **argv) {
  if (argc < 3) {
    std::printf("usage: status_card_tests <fonts directory> <logo-192.rgba> [output directory]\n");
    return 2;
  }
  content();
  text(argv[1]);
  raster();
  cards(argv[1], argv[2], argc > 3 ? argv[3] : "");
  if (failures) {
    std::printf("%d status card failures\n", failures);
    return 1;
  }
  std::printf("status card contracts passed\n");
  return 0;
}
