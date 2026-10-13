// Standalone checks for client/device/status_text.hpp (no framework; exits non-zero on failure).
//   g++ -std=c++17 -O2 -Wall -Wextra -I. tests/status_text_tests.cpp -o /tmp/status_text_tests && /tmp/status_text_tests
// Also writes a sample panel composited over mid-grey to /tmp/status_text_sample.ppm (or argv[1]).
#include "client/device/status_text.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <string>
#include <vector>

// The 1 ms budget is asserted only in optimised builds without heavy instrumentation.
#if defined(__OPTIMIZE__) && !defined(__SANITIZE_ADDRESS__) && !defined(__SANITIZE_THREAD__)
#define STATUS_TEXT_CHECK_TIMING 1
#if defined(__has_feature)
#if __has_feature(address_sanitizer) || __has_feature(thread_sanitizer) || __has_feature(memory_sanitizer)
#undef STATUS_TEXT_CHECK_TIMING
#endif
#endif
#endif

namespace {
int checks = 0, failures = 0;
volatile unsigned sink = 0;

#define CHECK(condition)                                                                  \
  do {                                                                                    \
    ++checks;                                                                             \
    if (!(condition)) {                                                                   \
      ++failures;                                                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);           \
    }                                                                                     \
  } while (0)

void checkText(const std::string& actual, const std::string& expected, int line) {
  ++checks;
  if (actual == expected) return;
  ++failures;
  std::fprintf(stderr, "FAIL %s:%d: got \"%s\", expected \"%s\"\n", __FILE__, line, actual.c_str(), expected.c_str());
}
#define CHECK_TEXT(actual, expected) checkText((actual), (expected), __LINE__)

const uint8_t* pixel(const vrost::StatusImage& image, uint32_t x, uint32_t y) {
  return &image.rgba[(std::size_t(y) * image.width + x) * 4];
}
bool sameColor(const uint8_t* p, const vrost::StatusColor& c) {
  return p[0] == c.r && p[1] == c.g && p[2] == c.b && p[3] == c.a;
}
bool validShape(const vrost::StatusImage& image) {
  return image.width >= 4 && image.height >= 4 && image.width % 4 == 0 && image.height % 4 == 0 &&
         image.width <= vrost::statusMaxWidth && image.height <= vrost::statusMaxHeight &&
         image.rgba.size() == std::size_t(image.width) * image.height * 4;
}
bool cornersTransparent(const vrost::StatusImage& image) {
  const uint32_t r = image.width - 1, b = image.height - 1;
  return pixel(image, 0, 0)[3] == 0 && pixel(image, r, 0)[3] == 0 && pixel(image, 0, b)[3] == 0 && pixel(image, r, b)[3] == 0;
}
struct Box { uint32_t left = ~0u, top = ~0u, right = 0, bottom = 0, count = 0; };
Box boxOf(const vrost::StatusImage& image, const vrost::StatusColor& color) {
  Box box;
  for (uint32_t y = 0; y < image.height; ++y)
    for (uint32_t x = 0; x < image.width; ++x)
      if (sameColor(pixel(image, x, y), color)) {
        box.left = std::min(box.left, x); box.right = std::max(box.right, x);
        box.top = std::min(box.top, y); box.bottom = std::max(box.bottom, y);
        ++box.count;
      }
  return box;
}
bool centred(const vrost::StatusImage& image, const Box& box) {
  const uint32_t leftMargin = box.left, rightMargin = image.width - 1 - box.right;
  return box.count && (leftMargin > rightMargin ? leftMargin - rightMargin : rightMargin - leftMargin) <= 1;
}

const std::vector<std::string> sample = {"VROSTBridge", "Starting SteamVR on the host (12 s)", "Reconnecting - weak Wi-Fi"};

void testFont() {
  const uint8_t a[8] = {0x0C, 0x1E, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x00};  // the upstream README's example
  CHECK(std::equal(a, a + 8, vrost::font8x8Basic['A']));
  // FNV-1a and byte sum of all 1024 bytes, computed from upstream font8x8_basic.h.
  uint32_t hash = 0x811C9DC5u, sum = 0;
  int inked = 0;
  bool inkOnlyOnPrintable = true;
  for (int c = 0; c < 128; ++c) {
    bool any = false;
    for (int row = 0; row < 8; ++row) {
      const uint8_t b = vrost::font8x8Basic[c][row];
      hash = (hash ^ b) * 0x01000193u;
      sum += b;
      any = any || b != 0;
    }
    inked += any;
    inkOnlyOnPrintable = inkOnlyOnPrintable && any == (c > 0x20 && c < 0x7F);
  }
  CHECK(hash == 0x4540520Du);
  CHECK(sum == 27688u);
  CHECK(inked == 94);
  CHECK(inkOnlyOnPrintable);  // control codes, space and DEL are blank
  CHECK(vrost::font8x8Basic['g'][7] == 0x1F && vrost::font8x8Basic['_'][7] == 0xFF);
  CHECK(vrost::font8x8Basic['~'][0] == 0x6E && vrost::font8x8Basic['~'][1] == 0x3B);
}

void testAsciiFold() {
  using vrost::asciiFold;
  CHECK_TEXT(asciiFold("\xE2\x80\xA6"), "...");  // U+2026 horizontal ellipsis
  CHECK_TEXT(asciiFold("\xE2\x80\x94"), "-");    // U+2014 em dash
  CHECK_TEXT(asciiFold("\xE2\x80\x93"), "-");    // U+2013 en dash
  CHECK_TEXT(asciiFold("\xE2\x80\x98"), "'");    // U+2018 left single quote
  CHECK_TEXT(asciiFold("\xE2\x80\x99"), "'");    // U+2019 right single quote
  CHECK_TEXT(asciiFold("\xE2\x80\x9C"), "\"");   // U+201C left double quote
  CHECK_TEXT(asciiFold("\xE2\x80\x9D"), "\"");   // U+201D right double quote
  CHECK_TEXT(asciiFold("\xC2\xB0"), "deg");      // U+00B0 degree sign
  CHECK_TEXT(asciiFold("\xC3\xA9"), "e");        // U+00E9 e acute
  CHECK_TEXT(asciiFold("Waking the host PC\xE2\x80\xA6"), "Waking the host PC...");
  CHECK_TEXT(asciiFold("Reconnecting \xE2\x80\x94 weak Wi-Fi"), "Reconnecting - weak Wi-Fi");
  CHECK_TEXT(asciiFold("1832\xC3\x97" "1920 \xC2\xB7 \xC2\xB1" "0.6 ms \xC2\xB7 12 \xC2\xB5s"), "1832x1920 | +/-0.6 ms | 12 us");
  CHECK_TEXT(asciiFold("SteamVR \xE2\x86\x92 Settings \xE2\x80\xA2 \xE2\x89\xA4 20 ms"), "SteamVR -> Settings * <= 20 ms");
  CHECK_TEXT(asciiFold("Ren\xC3\xA9" "e's Stra\xC3\x9F" "e \xC3\x86"), "Renee's Strasse AE");
  CHECK_TEXT(asciiFold("e\xCC\x81"), "e");  // decomposed: e + U+0301 combining acute
  CHECK_TEXT(asciiFold("a\xC2\xA0" "b\xE2\x80\xAF" "c\xE2\x80\x8B" "d\xC2\xAD" "e"), "a b cde");  // NBSP, narrow NBSP, ZWSP, soft hyphen
  CHECK_TEXT(asciiFold("tab\there\r\nnext"), "tab here  next");
  CHECK_TEXT(asciiFold(std::string("nul\0bell\x07", 9)), "nul?bell?");
  CHECK_TEXT(asciiFold("\xE6\x97\xA5\xE6\x9C\xAC"), "??");             // two CJK characters
  CHECK_TEXT(asciiFold("\xF0\x9F\x8E\xAE"), "?");                      // emoji (4-byte sequence)
  CHECK_TEXT(asciiFold("\xE2\x9A\xA0\xEF\xB8\x8F ok"), "? ok");       // warning sign + VS16
  // Ill-formed UTF-8: one '?' per maximal ill-formed subpart; nothing is decoded from it.
  CHECK_TEXT(asciiFold("\x80"), "?");                  // stray continuation byte
  CHECK_TEXT(asciiFold("\xC0\xAF"), "??");             // overlong '/'
  CHECK_TEXT(asciiFold("\xE2\x80"), "?");              // truncated sequence
  CHECK_TEXT(asciiFold("\xE2\x80" "A"), "?A");
  CHECK_TEXT(asciiFold("\xED\xA0\x80"), "???");        // UTF-16 surrogate
  CHECK_TEXT(asciiFold("\xF4\x90\x80\x80"), "????");   // above U+10FFFF
  CHECK_TEXT(asciiFold("\xFF" "x"), "?x");
  CHECK_TEXT(asciiFold(""), "");
  // Every code point, and every single byte, folds to at most 3 printable ASCII characters.
  bool printable = true;
  const auto encode = [](uint32_t cp) {
    std::string s;
    if (cp < 0x80) s += char(cp);
    else if (cp < 0x800) { s += char(0xC0 | (cp >> 6)); s += char(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { s += char(0xE0 | (cp >> 12)); s += char(0x80 | ((cp >> 6) & 0x3F)); s += char(0x80 | (cp & 0x3F)); }
    else { s += char(0xF0 | (cp >> 18)); s += char(0x80 | ((cp >> 12) & 0x3F)); s += char(0x80 | ((cp >> 6) & 0x3F)); s += char(0x80 | (cp & 0x3F)); }
    return s;
  };
  for (uint32_t cp = 0; cp <= 0x10FFFF; ++cp) {
    if (cp >= 0xD800 && cp <= 0xDFFF) continue;
    const std::string folded = asciiFold(encode(cp));
    printable = printable && folded.size() <= 3 && std::all_of(folded.begin(), folded.end(), [](char c) { return c >= 0x20 && c < 0x7F; });
  }
  for (int b = 0; b < 256; ++b) printable = printable && asciiFold(std::string(1, char(b))).size() == 1;
  CHECK(printable);
}

void testEllipsize() {
  using vrost::ellipsize;
  CHECK_TEXT(ellipsize("abc", 5), "abc");
  CHECK_TEXT(ellipsize("abcdef", 6), "abcdef");
  CHECK_TEXT(ellipsize("abcdefg", 6), "abc...");
  CHECK_TEXT(ellipsize("abcdefg", 3), "...");
  CHECK_TEXT(ellipsize("abcdefg", 2), "..");
  CHECK_TEXT(ellipsize("abcdefg", 0), "");
  CHECK_TEXT(ellipsize("", 0), "");
  CHECK_TEXT(ellipsize("abc defgh", 7), "abc...");  // no space before the dots
  CHECK_TEXT(ellipsize("h\xC3\xA9llo w\xC3\xB6rld", 8), "h\xC3\xA9llo...");  // counts code points, never splits one
  CHECK_TEXT(ellipsize(std::string(60, 'x'), 48), std::string(45, 'x') + "...");
}

void testEmpty() {
  for (const auto& lines : {std::vector<std::string>{}, std::vector<std::string>{"", "   ", "\xE2\x80\x8B"}}) {
    const auto image = vrost::renderStatus(lines, {});
    CHECK(image.width == 4 && image.height == 4 && image.rgba.size() == 64);
    CHECK(std::all_of(image.rgba.begin(), image.rgba.end(), [](uint8_t v) { return v == 0; }));
  }
}

void testPanel(const vrost::StatusImage& image) {
  const vrost::StatusStyle style;
  const uint32_t inset = style.padding + style.borderWidth;
  CHECK(validShape(image));
  // Widest line: 35 characters, ink 34*8+5 = 277 font px at scale 3 = 831 px, + 2*18 -> 868.
  // Height: heading 32 + two lines of 24 + two 10 px gaps = 100, + 2*18 = 136.
  CHECK(image.width == 868 && image.height == 136);
  CHECK(cornersTransparent(image));
  // Background between border and text: exact colour, alpha 200.
  CHECK(sameColor(pixel(image, image.width / 2, style.borderWidth + style.padding / 2), style.background));
  CHECK(sameColor(pixel(image, style.borderWidth + style.padding / 2, image.height / 2), style.background));
  // A full row of background between lines, in the middle of the panel.
  bool gapRow = false;
  for (uint32_t y = image.height / 4; y < image.height * 3 / 4 && !gapRow; ++y) {
    bool all = true;
    for (uint32_t x = inset; x < image.width - inset && all; ++x) all = sameColor(pixel(image, x, y), style.background);
    gapRow = all;
  }
  CHECK(gapRow);
  // Inside the panel nothing is more transparent than the background.
  bool solidInside = true;
  for (uint32_t y = inset; y < image.height - inset; ++y)
    for (uint32_t x = inset; x < image.width - inset; ++x) solidInside = solidInside && pixel(image, x, y)[3] >= style.background.a;
  CHECK(solidInside);
  // 2 px border, lighter than the background, on all four sides.
  CHECK(style.border.r > style.background.r && style.border.g > style.background.g && style.border.b > style.background.b);
  for (uint32_t d = 0; d < 2; ++d) {
    CHECK(sameColor(pixel(image, image.width / 2, d), style.border));
    CHECK(sameColor(pixel(image, image.width / 2, image.height - 1 - d), style.border));
    CHECK(sameColor(pixel(image, d, image.height / 2), style.border));
    CHECK(sameColor(pixel(image, image.width - 1 - d, image.height / 2), style.border));
  }
  CHECK(sameColor(pixel(image, image.width / 2, 2), style.background));
  // Rounded corner: transparent at the corner, partially covered on the arc, opaque inside.
  const uint8_t arc = pixel(image, 4, 3)[3];  // centre (4.5, 3.5) is 14.16 px from the arc centre (14, 14): on the edge
  CHECK(arc > 0 && arc < style.border.a);
  CHECK(pixel(image, 8, 8)[3] >= style.background.a);
  // Heading in the heading colour, the other lines white; each line is centred.
  const Box heading = boxOf(image, style.heading), body = boxOf(image, style.text);
  CHECK(heading.count > 100 && body.count > 1000);
  CHECK(heading.bottom < body.top);
  CHECK(heading.bottom - heading.top + 1 == 32);  // "VROSTBridge": V..g span all 8 rows at scale 4
  CHECK(centred(image, heading) && centred(image, body));
  CHECK(vrost::renderStatus(sample, {}).rgba == image.rgba);  // deterministic
}

void testGlyphA() {
  vrost::StatusStyle style;
  style.firstLineIsHeading = false;
  const auto image = vrost::renderStatus({"A"}, style);
  CHECK(validShape(image));
  CHECK(image.width == 56 && image.height == 60);  // ink 6x7 font px at scale 3, + 2*18, rounded up to 4
  const Box white = boxOf(image, style.text);      // pixels exactly (255,255,255,255)
  CHECK(white.count > 150);
  CHECK(white.right - white.left + 1 == 18 && white.bottom - white.top + 1 == 21);
  CHECK(centred(image, white));
  CHECK(sameColor(pixel(image, white.left + 7, white.top + 13), style.text));  // crossbar, font pixel (2,4)
  CHECK(sameColor(pixel(image, white.left + 8, white.top + 8), style.background));  // centre of the counter (hole)
  // Supersampling softened the diagonals: partially covered pixels lie between background and text colour.
  int partial = 0;
  bool between = true;
  for (uint32_t y = white.top - 1; y <= white.bottom + 1; ++y)
    for (uint32_t x = white.left - 1; x <= white.right + 1; ++x) {
      const uint8_t* p = pixel(image, x, y);
      if (sameColor(p, style.background) || sameColor(p, style.text)) continue;
      ++partial;
      between = between && p[3] > style.background.a && p[3] < 255 && p[0] > style.background.r && p[0] < 255 &&
                p[1] > style.background.g && p[2] > style.background.b;
    }
  CHECK(partial >= 8 && between);
  // Spaces and folding: "A" with padding spaces and a zero-width space renders identically.
  CHECK(vrost::renderStatus({"  A\xE2\x80\x8B  "}, style).rgba == image.rgba);
}

void testFitting() {
  vrost::StatusStyle plain;
  plain.firstLineIsHeading = false;
  // 41 x 'H' at scale 3: ink 40*8+6 = 326 font px = 978 px, within 1024 - 2*18.
  auto image = vrost::renderStatus({std::string(41, 'H')}, plain);
  CHECK(image.width == 1016 && image.height == 60);
  // 42 x 'H' would be 1002 px wide: drawn at scale 2 instead (668 px).
  image = vrost::renderStatus({std::string(42, 'H')}, plain);
  CHECK(image.width == 704 && image.height == 52);
  // 100 characters: cut to 48 (45 x 'H' + "...", ink 47*8+4 = 380 font px), drawn at scale 2.
  image = vrost::renderStatus({std::string(100, 'H')}, plain);
  CHECK(image.width == 796 && image.height == 52);
  // At most six lines; empty lines take no space; an empty lines[0] means no heading.
  const std::vector<std::string> eight = {"VROSTBridge", "one", "two", "three", "four", "five", "six", "seven"};
  CHECK(vrost::renderStatus(eight, {}).rgba == vrost::renderStatus(std::vector<std::string>(eight.begin(), eight.begin() + 6), {}).rgba);
  CHECK(vrost::renderStatus({"VROSTBridge", "", "Ready"}, {}).rgba == vrost::renderStatus({"VROSTBridge", "Ready"}, {}).rgba);
  CHECK(vrost::renderStatus({"", "Ready"}, {}).rgba == vrost::renderStatus({"Ready"}, plain).rgba);
  // Six maximal lines, with the default style and with out-of-range style values.
  vrost::StatusStyle huge;
  huge.scale = huge.headingScale = 99;
  huge.padding = huge.lineGap = huge.borderWidth = huge.cornerRadius = 999;
  const std::vector<std::string> full(6, std::string(60, 'W'));
  for (const auto& style : {vrost::StatusStyle{}, huge}) {
    image = vrost::renderStatus(full, style);
    CHECK(validShape(image) && cornersTransparent(image));
  }
}

struct Timing { double median, best; uint32_t width, height; };
Timing timeRender(const std::vector<std::string>& lines) {
  std::vector<double> micros;
  vrost::StatusImage image;
  for (int i = 0; i < 20; ++i) image = vrost::renderStatus(lines, {});  // warm-up
  for (int i = 0; i < 300; ++i) {
    const auto start = std::chrono::steady_clock::now();
    image = vrost::renderStatus(lines, {});
    const auto stop = std::chrono::steady_clock::now();
    sink = sink + image.rgba[image.rgba.size() / 2];
    micros.push_back(std::chrono::duration<double, std::micro>(stop - start).count());
  }
  std::sort(micros.begin(), micros.end());
  return {micros[micros.size() / 2], micros.front(), image.width, image.height};
}

void testTiming() {
  const std::vector<std::string> realistic = {
      "VROSTBridge", "Starting SteamVR on the host (12 s)", "Reconnecting \xE2\x80\x94 weak Wi-Fi",
      "Waking the host PC\xE2\x80\xA6", "90 Hz \xC2\xB7 HEVC \xC2\xB7 150 Mbps \xC2\xB7 0.2% loss",
      "Pipeline 38.0 ms (p95 44.0) \xC2\xB7 wait 3.7 ms"};
  const std::vector<std::string> dense(6, "WM@#&%$8B0WM@#&%$8B0WM@#&%$8B0WM@#&%$8B0W");  // 41 heavy glyphs: all at scale 3
  const std::vector<std::string> longest(6, std::string(48, 'W'));                       // 48 characters: scale 2
  const struct { const char* name; const std::vector<std::string>* lines; } cases[] = {
      {"6 status lines, scale 3 (heading 4)", &realistic}, {"6 x 41 dense glyphs, scale 3", &dense}, {"6 x 48 characters (fits at scale 2)", &longest}};
  for (const auto& c : cases) {
    const Timing t = timeRender(*c.lines);
    std::printf("timing  %-38s %4ux%-4u median %7.1f us, best %7.1f us\n", c.name, t.width, t.height, t.median, t.best);
#ifdef STATUS_TEXT_CHECK_TIMING
    CHECK(t.best < 1000.0);  // best of 300: insensitive to a busy machine
#endif
  }
}

bool writePpm(const std::string& path, const vrost::StatusImage& image) {
  std::vector<uint8_t> rgb(std::size_t(image.width) * image.height * 3);
  for (std::size_t i = 0; i < std::size_t(image.width) * image.height; ++i) {
    const uint8_t* p = &image.rgba[i * 4];
    for (int c = 0; c < 3; ++c) rgb[i * 3 + c] = uint8_t((p[c] * p[3] + 128 * (255 - p[3]) + 127) / 255);  // over mid-grey
  }
  std::FILE* file = std::fopen(path.c_str(), "wb");
  if (!file) return false;
  std::fprintf(file, "P6\n%u %u\n255\n", image.width, image.height);
  const bool written = std::fwrite(rgb.data(), 1, rgb.size(), file) == rgb.size();
  return std::fclose(file) == 0 && written;
}
}  // namespace

int main(int argc, char** argv) {
  testFont();
  testAsciiFold();
  testEllipsize();
  testEmpty();
  const vrost::StatusImage image = vrost::renderStatus(sample, {});
  testPanel(image);
  testGlyphA();
  testFitting();
  testTiming();
  const std::string path = argc > 1 ? argv[1] : "/tmp/status_text_sample.ppm";
  if (writePpm(path, image)) std::printf("sample  %ux%u panel over mid-grey: %s\n", image.width, image.height, path.c_str());
  else std::fprintf(stderr, "note: could not write the sample image to %s (not a test failure)\n", path.c_str());
  std::printf("status_text_tests: %d checks, %d failed\n", checks, failures);
  return failures ? 1 : 0;
}
