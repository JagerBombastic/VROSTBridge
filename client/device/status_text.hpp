#pragma once
// In-headset status panel: a CPU rasteriser that turns up to six short UTF-8
// status lines ("Waking the host PC…", "Reconnecting — weak Wi-Fi") into an
// RGBA8 image for an OpenVR overlay:
//
//   vrost::StatusImage image = vrost::renderStatus({"VROSTBridge", "Waking the host PC…"}, {});
//   overlays->SetOverlayRaw(handle, image.rgba.data(), image.width, image.height, 4);
//
// - Pixels are straight (non-premultiplied) alpha, OpenVR's default: leave
//   VROverlayFlags_IsPremultiplied unset.
// - The image size follows the text. Keep metres per pixel constant
//   (SetOverlayWidthInMeters(handle, image.width * metresPerPixel)) so the text
//   keeps its size, and re-render only when the text changes.
// - OpenVR documents an unspecified upper bound on SetOverlayRaw data; the
//   1024x512 maximum here is 2 MiB. Check the returned EVROverlayError and
//   VREvent_ImageFailed.
//
// Header-only, C++17 standard library only, no global state (thread-safe).
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace vrost {

// font8x8 "basic" (U+0000-U+007F) by Daniel Hepper, from
// https://github.com/dhepper/font8x8 (font8x8_basic.h, revision 8e279d2d864e).
// Its author dedicates it to the public domain ("License: Public Domain"); it
// derives from Marcel Sondaar's font8_8.asm and IBM's public-domain VGA fonts.
// Copied unchanged; tests/status_text_tests.cpp checks its checksum. Each glyph
// is 8 rows, top first; bit n (least significant first) is the nth pixel from
// the left. Control codes are blank.
inline constexpr uint8_t font8x8Basic[128][8] = {
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0000 (nul)
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0001
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0002
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0003
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0004
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0005
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0006
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0007
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0008
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0009
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000A
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000B
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000C
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000D
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000E
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+000F
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0010
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0011
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0012
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0013
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0014
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0015
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0016
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0017
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0018
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0019
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001A
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001B
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001C
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001D
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001E
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+001F
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0020 (space)
    {0x18, 0x3C, 0x3C, 0x18, 0x18, 0x00, 0x18, 0x00},  // U+0021 (!)
    {0x36, 0x36, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0022 (")
    {0x36, 0x36, 0x7F, 0x36, 0x7F, 0x36, 0x36, 0x00},  // U+0023 (#)
    {0x0C, 0x3E, 0x03, 0x1E, 0x30, 0x1F, 0x0C, 0x00},  // U+0024 ($)
    {0x00, 0x63, 0x33, 0x18, 0x0C, 0x66, 0x63, 0x00},  // U+0025 (%)
    {0x1C, 0x36, 0x1C, 0x6E, 0x3B, 0x33, 0x6E, 0x00},  // U+0026 (&)
    {0x06, 0x06, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0027 (')
    {0x18, 0x0C, 0x06, 0x06, 0x06, 0x0C, 0x18, 0x00},  // U+0028 (()
    {0x06, 0x0C, 0x18, 0x18, 0x18, 0x0C, 0x06, 0x00},  // U+0029 ())
    {0x00, 0x66, 0x3C, 0xFF, 0x3C, 0x66, 0x00, 0x00},  // U+002A (*)
    {0x00, 0x0C, 0x0C, 0x3F, 0x0C, 0x0C, 0x00, 0x00},  // U+002B (+)
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C, 0x06},  // U+002C (,)
    {0x00, 0x00, 0x00, 0x3F, 0x00, 0x00, 0x00, 0x00},  // U+002D (-)
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C, 0x00},  // U+002E (.)
    {0x60, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x01, 0x00},  // U+002F (/)
    {0x3E, 0x63, 0x73, 0x7B, 0x6F, 0x67, 0x3E, 0x00},  // U+0030 (0)
    {0x0C, 0x0E, 0x0C, 0x0C, 0x0C, 0x0C, 0x3F, 0x00},  // U+0031 (1)
    {0x1E, 0x33, 0x30, 0x1C, 0x06, 0x33, 0x3F, 0x00},  // U+0032 (2)
    {0x1E, 0x33, 0x30, 0x1C, 0x30, 0x33, 0x1E, 0x00},  // U+0033 (3)
    {0x38, 0x3C, 0x36, 0x33, 0x7F, 0x30, 0x78, 0x00},  // U+0034 (4)
    {0x3F, 0x03, 0x1F, 0x30, 0x30, 0x33, 0x1E, 0x00},  // U+0035 (5)
    {0x1C, 0x06, 0x03, 0x1F, 0x33, 0x33, 0x1E, 0x00},  // U+0036 (6)
    {0x3F, 0x33, 0x30, 0x18, 0x0C, 0x0C, 0x0C, 0x00},  // U+0037 (7)
    {0x1E, 0x33, 0x33, 0x1E, 0x33, 0x33, 0x1E, 0x00},  // U+0038 (8)
    {0x1E, 0x33, 0x33, 0x3E, 0x30, 0x18, 0x0E, 0x00},  // U+0039 (9)
    {0x00, 0x0C, 0x0C, 0x00, 0x00, 0x0C, 0x0C, 0x00},  // U+003A (:)
    {0x00, 0x0C, 0x0C, 0x00, 0x00, 0x0C, 0x0C, 0x06},  // U+003B (;)
    {0x18, 0x0C, 0x06, 0x03, 0x06, 0x0C, 0x18, 0x00},  // U+003C (<)
    {0x00, 0x00, 0x3F, 0x00, 0x00, 0x3F, 0x00, 0x00},  // U+003D (=)
    {0x06, 0x0C, 0x18, 0x30, 0x18, 0x0C, 0x06, 0x00},  // U+003E (>)
    {0x1E, 0x33, 0x30, 0x18, 0x0C, 0x00, 0x0C, 0x00},  // U+003F (?)
    {0x3E, 0x63, 0x7B, 0x7B, 0x7B, 0x03, 0x1E, 0x00},  // U+0040 (@)
    {0x0C, 0x1E, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x00},  // U+0041 (A)
    {0x3F, 0x66, 0x66, 0x3E, 0x66, 0x66, 0x3F, 0x00},  // U+0042 (B)
    {0x3C, 0x66, 0x03, 0x03, 0x03, 0x66, 0x3C, 0x00},  // U+0043 (C)
    {0x1F, 0x36, 0x66, 0x66, 0x66, 0x36, 0x1F, 0x00},  // U+0044 (D)
    {0x7F, 0x46, 0x16, 0x1E, 0x16, 0x46, 0x7F, 0x00},  // U+0045 (E)
    {0x7F, 0x46, 0x16, 0x1E, 0x16, 0x06, 0x0F, 0x00},  // U+0046 (F)
    {0x3C, 0x66, 0x03, 0x03, 0x73, 0x66, 0x7C, 0x00},  // U+0047 (G)
    {0x33, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x33, 0x00},  // U+0048 (H)
    {0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0x00},  // U+0049 (I)
    {0x78, 0x30, 0x30, 0x30, 0x33, 0x33, 0x1E, 0x00},  // U+004A (J)
    {0x67, 0x66, 0x36, 0x1E, 0x36, 0x66, 0x67, 0x00},  // U+004B (K)
    {0x0F, 0x06, 0x06, 0x06, 0x46, 0x66, 0x7F, 0x00},  // U+004C (L)
    {0x63, 0x77, 0x7F, 0x7F, 0x6B, 0x63, 0x63, 0x00},  // U+004D (M)
    {0x63, 0x67, 0x6F, 0x7B, 0x73, 0x63, 0x63, 0x00},  // U+004E (N)
    {0x1C, 0x36, 0x63, 0x63, 0x63, 0x36, 0x1C, 0x00},  // U+004F (O)
    {0x3F, 0x66, 0x66, 0x3E, 0x06, 0x06, 0x0F, 0x00},  // U+0050 (P)
    {0x1E, 0x33, 0x33, 0x33, 0x3B, 0x1E, 0x38, 0x00},  // U+0051 (Q)
    {0x3F, 0x66, 0x66, 0x3E, 0x36, 0x66, 0x67, 0x00},  // U+0052 (R)
    {0x1E, 0x33, 0x07, 0x0E, 0x38, 0x33, 0x1E, 0x00},  // U+0053 (S)
    {0x3F, 0x2D, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0x00},  // U+0054 (T)
    {0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x3F, 0x00},  // U+0055 (U)
    {0x33, 0x33, 0x33, 0x33, 0x33, 0x1E, 0x0C, 0x00},  // U+0056 (V)
    {0x63, 0x63, 0x63, 0x6B, 0x7F, 0x77, 0x63, 0x00},  // U+0057 (W)
    {0x63, 0x63, 0x36, 0x1C, 0x1C, 0x36, 0x63, 0x00},  // U+0058 (X)
    {0x33, 0x33, 0x33, 0x1E, 0x0C, 0x0C, 0x1E, 0x00},  // U+0059 (Y)
    {0x7F, 0x63, 0x31, 0x18, 0x4C, 0x66, 0x7F, 0x00},  // U+005A (Z)
    {0x1E, 0x06, 0x06, 0x06, 0x06, 0x06, 0x1E, 0x00},  // U+005B ([)
    {0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x40, 0x00},  // U+005C (\)
    {0x1E, 0x18, 0x18, 0x18, 0x18, 0x18, 0x1E, 0x00},  // U+005D (])
    {0x08, 0x1C, 0x36, 0x63, 0x00, 0x00, 0x00, 0x00},  // U+005E (^)
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF},  // U+005F (_)
    {0x0C, 0x0C, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+0060 (`)
    {0x00, 0x00, 0x1E, 0x30, 0x3E, 0x33, 0x6E, 0x00},  // U+0061 (a)
    {0x07, 0x06, 0x06, 0x3E, 0x66, 0x66, 0x3B, 0x00},  // U+0062 (b)
    {0x00, 0x00, 0x1E, 0x33, 0x03, 0x33, 0x1E, 0x00},  // U+0063 (c)
    {0x38, 0x30, 0x30, 0x3E, 0x33, 0x33, 0x6E, 0x00},  // U+0064 (d)
    {0x00, 0x00, 0x1E, 0x33, 0x3F, 0x03, 0x1E, 0x00},  // U+0065 (e)
    {0x1C, 0x36, 0x06, 0x0F, 0x06, 0x06, 0x0F, 0x00},  // U+0066 (f)
    {0x00, 0x00, 0x6E, 0x33, 0x33, 0x3E, 0x30, 0x1F},  // U+0067 (g)
    {0x07, 0x06, 0x36, 0x6E, 0x66, 0x66, 0x67, 0x00},  // U+0068 (h)
    {0x0C, 0x00, 0x0E, 0x0C, 0x0C, 0x0C, 0x1E, 0x00},  // U+0069 (i)
    {0x30, 0x00, 0x30, 0x30, 0x30, 0x33, 0x33, 0x1E},  // U+006A (j)
    {0x07, 0x06, 0x66, 0x36, 0x1E, 0x36, 0x67, 0x00},  // U+006B (k)
    {0x0E, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0x00},  // U+006C (l)
    {0x00, 0x00, 0x33, 0x7F, 0x7F, 0x6B, 0x63, 0x00},  // U+006D (m)
    {0x00, 0x00, 0x1F, 0x33, 0x33, 0x33, 0x33, 0x00},  // U+006E (n)
    {0x00, 0x00, 0x1E, 0x33, 0x33, 0x33, 0x1E, 0x00},  // U+006F (o)
    {0x00, 0x00, 0x3B, 0x66, 0x66, 0x3E, 0x06, 0x0F},  // U+0070 (p)
    {0x00, 0x00, 0x6E, 0x33, 0x33, 0x3E, 0x30, 0x78},  // U+0071 (q)
    {0x00, 0x00, 0x3B, 0x6E, 0x66, 0x06, 0x0F, 0x00},  // U+0072 (r)
    {0x00, 0x00, 0x3E, 0x03, 0x1E, 0x30, 0x1F, 0x00},  // U+0073 (s)
    {0x08, 0x0C, 0x3E, 0x0C, 0x0C, 0x2C, 0x18, 0x00},  // U+0074 (t)
    {0x00, 0x00, 0x33, 0x33, 0x33, 0x33, 0x6E, 0x00},  // U+0075 (u)
    {0x00, 0x00, 0x33, 0x33, 0x33, 0x1E, 0x0C, 0x00},  // U+0076 (v)
    {0x00, 0x00, 0x63, 0x6B, 0x7F, 0x7F, 0x36, 0x00},  // U+0077 (w)
    {0x00, 0x00, 0x63, 0x36, 0x1C, 0x36, 0x63, 0x00},  // U+0078 (x)
    {0x00, 0x00, 0x33, 0x33, 0x33, 0x3E, 0x30, 0x1F},  // U+0079 (y)
    {0x00, 0x00, 0x3F, 0x19, 0x0C, 0x26, 0x3F, 0x00},  // U+007A (z)
    {0x38, 0x0C, 0x0C, 0x07, 0x0C, 0x0C, 0x38, 0x00},  // U+007B ({)
    {0x18, 0x18, 0x18, 0x00, 0x18, 0x18, 0x18, 0x00},  // U+007C (|)
    {0x07, 0x0C, 0x0C, 0x38, 0x0C, 0x0C, 0x07, 0x00},  // U+007D (})
    {0x6E, 0x3B, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // U+007E (~)
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}   // U+007F
};

struct StatusColor { uint8_t r = 0, g = 0, b = 0, a = 255; };

struct StatusStyle {
  uint32_t scale = 3;              // integer glyph magnification for body lines (1-16)
  uint32_t headingScale = 4;       // magnification for the heading line (1-16)
  bool firstLineIsHeading = true;  // false: every line uses scale and text
  uint32_t padding = 16;           // px between the border and the text (0-64)
  uint32_t lineGap = 10;           // px between lines (0-64)
  uint32_t borderWidth = 2;        // px (0-4)
  uint32_t cornerRadius = 14;      // px (0-64, at most half the panel)
  StatusColor background{12, 14, 18, 200};
  StatusColor border{52, 57, 68, 230};     // desktop UI --line
  StatusColor text{255, 255, 255, 255};
  StatusColor heading{53, 194, 246, 255};  // desktop UI --accent
};

// Straight (non-premultiplied) RGBA8, row-major, top-left origin, no row padding.
struct StatusImage {
  uint32_t width = 0, height = 0;
  std::vector<uint8_t> rgba;  // width * height * 4 bytes
};

inline constexpr std::size_t statusMaxLines = 6, statusMaxChars = 48;
inline constexpr uint32_t statusMaxWidth = 1024, statusMaxHeight = 512;

namespace status_detail {
// U+00A0-U+00FF: Latin-1 letters lose their accents; symbols get ASCII spellings.
inline constexpr const char* latin1Fold[96] = {
    " ", "!", "c", "GBP", "?", "JPY", "|", "?", "\"", "(C)", "a", "\"", "?", "", "(R)", "-",     // A0-AF
    "deg", "+/-", "2", "3", "'", "u", "?", "|", ",", "1", "o", "\"", "1/4", "1/2", "3/4", "?", // B0-BF
    "A", "A", "A", "A", "A", "A", "AE", "C", "E", "E", "E", "E", "I", "I", "I", "I",           // C0-CF
    "D", "N", "O", "O", "O", "O", "O", "x", "O", "U", "U", "U", "U", "Y", "Th", "ss",          // D0-DF
    "a", "a", "a", "a", "a", "a", "ae", "c", "e", "e", "e", "e", "i", "i", "i", "i",           // E0-EF
    "d", "n", "o", "o", "o", "o", "o", "/", "o", "u", "u", "u", "u", "y", "th", "y"};          // F0-FF

inline const char* foldCodePoint(uint32_t cp) {
  if (cp >= 0xA0 && cp <= 0xFF) return latin1Fold[cp - 0xA0];
  if ((cp >= 0x300 && cp <= 0x36F) || (cp >= 0xFE00 && cp <= 0xFE0F)) return "";  // combining accents, variation selectors
  if ((cp >= 0x2000 && cp <= 0x200A) || cp == 0x202F || cp == 0x205F || cp == 0x3000) return " ";
  switch (cp) {
  case 0x200B: case 0x200C: case 0x200D: case 0x2060: case 0xFEFF: return "";  // zero-width
  case 0x2010: case 0x2011: case 0x2012: case 0x2013: case 0x2014: case 0x2015: case 0x2212: return "-";
  case 0x2018: case 0x2019: case 0x201A: case 0x201B: case 0x2032: return "'";
  case 0x201C: case 0x201D: case 0x201E: case 0x201F: case 0x2033: return "\"";
  case 0x2039: return "<";
  case 0x203A: return ">";
  case 0x2022: return "*";
  case 0x2026: return "...";
  case 0x2122: return "TM";
  case 0x2190: return "<-";
  case 0x2192: return "->";
  case 0x2194: return "<->";
  case 0x21D2: return "=>";
  case 0x2248: return "~";
  case 0x2260: return "!=";
  case 0x2264: return "<=";
  case 0x2265: return ">=";
  default: return "?";
  }
}
}  // namespace status_detail

// Folds UTF-8 to the printable ASCII the embedded font draws:
// - printable ASCII is kept; tab, LF, VT, FF and CR become spaces and other
//   control characters '?';
// - punctuation gets ASCII spellings: … -> "...", dashes and minus -> '-',
//   curly quotes -> ' and ", « » -> ", • -> '*', · -> '|', arrows -> "->" etc.,
//   × -> 'x', ± -> "+/-", ° -> "deg", µ -> 'u', ≤ -> "<=";
// - Latin-1 letters lose their accents (é -> e, ß -> "ss", Æ -> "AE");
//   combining accents, zero-width characters, soft hyphens and variation
//   selectors are dropped, and Unicode spaces become ' ';
// - anything else, and each maximal ill-formed UTF-8 subsequence, becomes one '?'.
inline std::string asciiFold(const std::string& utf8) {
  std::string out;
  out.reserve(utf8.size());
  const std::size_t n = utf8.size();
  std::size_t i = 0;
  while (i < n) {
    const auto lead = static_cast<unsigned char>(utf8[i]);
    if (lead < 0x80) {
      if (lead >= 0x20 && lead < 0x7F) out += char(lead);
      else out += (lead >= '\t' && lead <= '\r') ? ' ' : '?';
      ++i;
      continue;
    }
    // Well-formed sequences per the Unicode Standard, table 3-7.
    std::size_t length = 0;
    unsigned char low = 0x80, high = 0xBF;
    if (lead >= 0xC2 && lead <= 0xDF) {
      length = 2;
    } else if (lead >= 0xE0 && lead <= 0xEF) {
      length = 3;
      if (lead == 0xE0) low = 0xA0;
      if (lead == 0xED) high = 0x9F;  // no surrogates
    } else if (lead >= 0xF0 && lead <= 0xF4) {
      length = 4;
      if (lead == 0xF0) low = 0x90;
      if (lead == 0xF4) high = 0x8F;  // nothing above U+10FFFF
    }
    uint32_t cp = lead & (0x7Fu >> length);
    std::size_t used = 1;
    for (; used < length && i + used < n; ++used) {
      const auto next = static_cast<unsigned char>(utf8[i + used]);
      if (next < (used == 1 ? low : 0x80) || next > (used == 1 ? high : 0xBF)) break;
      cp = (cp << 6) | (next & 0x3Fu);
    }
    i += used;
    out += used == length ? status_detail::foldCodePoint(cp) : "?";
  }
  return out;
}

// Shortens text to at most maxChars characters (UTF-8 code points; a multi-byte
// sequence is never split). Longer text keeps its first maxChars - 3
// characters, minus trailing spaces, followed by "..."; below 3 it is all dots.
inline std::string ellipsize(const std::string& text, std::size_t maxChars) {
  const auto starts = [](char c) { return (static_cast<unsigned char>(c) & 0xC0) != 0x80; };
  const auto count = std::size_t(std::count_if(text.begin(), text.end(), starts));
  if (count <= maxChars) return text;
  if (maxChars < 3) return std::string(maxChars, '.');
  std::size_t end = 0;
  for (std::size_t seen = 0; end < text.size(); ++end)
    if (starts(text[end]) && seen++ == maxChars - 3) break;
  std::string out = text.substr(0, end);
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out + "...";
}

namespace status_detail {
using Rgba = std::array<uint8_t, 4>;
inline constexpr uint32_t coverageLevel[5] = {0, 64, 128, 191, 255};  // n of 4 subsamples -> 0-255

inline uint8_t inkColumns(unsigned char c) {
  uint8_t mask = 0;
  for (const uint8_t row : font8x8Basic[c & 0x7F]) mask = uint8_t(mask | row);
  return mask;
}
inline uint32_t inkLeft(unsigned char c) {
  const uint8_t mask = inkColumns(c);
  for (uint32_t x = 0; x < 8; ++x) if ((mask >> x) & 1) return x;
  return 0;
}
inline uint32_t inkRight(unsigned char c) {
  const uint8_t mask = inkColumns(c);
  for (uint32_t x = 8; x-- > 0;) if ((mask >> x) & 1) return x;
  return 7;
}
inline uint32_t inkTop(unsigned char c) {
  for (uint32_t y = 0; y < 8; ++y) if (font8x8Basic[c & 0x7F][y]) return y;
  return 0;
}
inline uint32_t inkBottom(unsigned char c) {
  for (uint32_t y = 8; y-- > 0;) if (font8x8Basic[c & 0x7F][y]) return y;
  return 7;
}

// Bit i of v moved to bit 2i.
inline uint32_t spreadBits(uint32_t v) {
  v = (v | (v << 4)) & 0x0F0Fu;
  v = (v | (v << 2)) & 0x3333u;
  return (v | (v << 1)) & 0x5555u;
}

// The glyph doubled to 16x16 with the EPX (Scale2x) rule: each pixel becomes
// 2x2, and a quarter takes its two neighbours' value where they meet across a
// diagonal step. One-pixel staircases become half-pixel ones; straight strokes
// and isolated pixels are unchanged and the ink never grows. Bit x of row y is
// sub-pixel (x, y).
inline std::array<uint16_t, 16> smoothGlyph(unsigned char c) {
  const uint8_t* g = font8x8Basic[c & 0x7F];
  std::array<uint16_t, 16> rows{};
  for (int y = 0; y < 8; ++y) {
    // Bit x of each mask: pixel (x, y) and its neighbours (outside the glyph is blank).
    const uint32_t p = g[y], up = y > 0 ? g[y - 1] : 0u, down = y < 7 ? g[y + 1] : 0u;
    const uint32_t right = p >> 1, left = (p << 1) & 0xFFu;
    const uint32_t tlRule = ~(left ^ up) & (left ^ down) & (up ^ right);
    const uint32_t trRule = ~(up ^ right) & (up ^ left) & (right ^ down);
    const uint32_t blRule = ~(down ^ left) & (down ^ right) & (left ^ up);
    const uint32_t brRule = ~(right ^ down) & (right ^ up) & (down ^ left);
    const uint32_t topLeft = (tlRule & up) | (~tlRule & p), topRight = (trRule & right) | (~trRule & p);
    const uint32_t bottomLeft = (blRule & left) | (~blRule & p), bottomRight = (brRule & down) | (~brRule & p);
    rows[2 * y] = uint16_t(spreadBits(topLeft) | (spreadBits(topRight) << 1));
    rows[2 * y + 1] = uint16_t(spreadBits(bottomLeft) | (spreadBits(bottomRight) << 1));
  }
  return rows;
}

// Per-pixel coverage (0-4 subsamples) of a glyph at an integer scale (1-16):
// the smoothed glyph is drawn at twice the output size, then 2x2 box-filtered.
// At odd scales the half-pixel steps become 50 % pixels; at even scales they
// fall on whole pixels, giving a crisp but finer staircase.
inline std::vector<uint8_t> glyphCoverage(unsigned char c, uint32_t scale) {
  const auto smooth = smoothGlyph(c);
  const uint32_t size = 8 * scale;
  std::array<uint8_t, 2 * 8 * 16> source{};  // smoothed row/column of each subsample
  for (uint32_t s = 0; s < 2 * size; ++s) source[s] = uint8_t(s / scale);
  std::array<std::array<uint8_t, 8 * 16>, 16> half{};  // [smoothed row][x]: lit subsamples of one subsample row
  for (uint32_t r = 0; r < 16; ++r)
    for (uint32_t x = 0; x < size; ++x)
      half[r][x] = uint8_t(((smooth[r] >> source[2 * x]) & 1) + ((smooth[r] >> source[2 * x + 1]) & 1));
  std::vector<uint8_t> coverage(std::size_t(size) * size);
  for (uint32_t y = 0; y < size; ++y) {
    const auto& top = half[source[2 * y]];
    const auto& bottom = half[source[2 * y + 1]];
    uint8_t* out = &coverage[std::size_t(y) * size];
    for (uint32_t x = 0; x < size; ++x) out[x] = uint8_t(top[x] + bottom[x]);
  }
  return coverage;
}

// Straight-alpha source-over of colour c at coverage 0-255 onto pixel d.
inline void blendOver(uint8_t* d, const StatusColor& c, uint32_t coverage) {
  const uint32_t sa = (c.a * coverage + 127) / 255;
  if (sa == 0) return;
  if (sa == 255) { d[0] = c.r; d[1] = c.g; d[2] = c.b; d[3] = 255; return; }
  const uint32_t dw = d[3] * (255 - sa);  // destination weight, scaled by 255^2
  const uint32_t aw = sa * 255 + dw;      // output alpha, scaled by 255
  d[0] = uint8_t((c.r * sa * 255 + d[0] * dw + aw / 2) / aw);
  d[1] = uint8_t((c.g * sa * 255 + d[1] * dw + aw / 2) / aw);
  d[2] = uint8_t((c.b * sa * 255 + d[2] * dw + aw / 2) / aw);
  d[3] = uint8_t((aw + 127) / 255);
}

// Signed distance (px, negative inside) from pixel (x, y)'s centre to the edge
// of a width x height rectangle with corner radius r.
inline float roundedRectDistance(uint32_t x, uint32_t y, uint32_t width, uint32_t height, float r) {
  const float qx = std::fabs(float(x) + .5f - float(width) * .5f) - (float(width) * .5f - r);
  const float qy = std::fabs(float(y) + .5f - float(height) * .5f) - (float(height) * .5f - r);
  const float ox = std::max(qx, 0.f), oy = std::max(qy, 0.f);
  return std::sqrt(ox * ox + oy * oy) + std::min(std::max(qx, qy), 0.f) - r;
}

// Panel colour at distance d: background inside, border over the outer
// borderWidth px, both anti-aliased. Fully transparent pixels keep the rim
// colour so texture filtering cannot pull dark fringes into the edge.
inline Rgba panelPixel(float d, float borderWidth, const StatusColor& fill, const StatusColor& edge) {
  const float coverage = std::clamp(.5f - d, 0.f, 1.f);
  const float mix = borderWidth > 0 ? std::clamp(d + borderWidth + .5f, 0.f, 1.f) : 0.f;
  const float fillWeight = fill.a * (1 - mix), edgeWeight = edge.a * mix, alpha = fillWeight + edgeWeight;
  if (alpha <= 0) return {0, 0, 0, 0};
  const auto channel = [&](uint8_t f, uint8_t e) { return uint8_t(std::lround((f * fillWeight + e * edgeWeight) / alpha)); };
  return {channel(fill.r, edge.r), channel(fill.g, edge.g), channel(fill.b, edge.b), uint8_t(std::lround(alpha * coverage))};
}
}  // namespace status_detail

// Renders status lines onto a rounded, semi-transparent panel.
// - Each line is folded to ASCII (asciiFold), trimmed of spaces and shortened
//   to statusMaxChars characters (ellipsize). Empty lines are skipped, so an
//   optional line can be passed as ""; at most statusMaxLines are drawn.
// - lines[0] is the heading (style.heading colour at style.headingScale) unless
//   style.firstLineIsHeading is false or it is empty.
// - Lines are centred on their ink. A line too wide for statusMaxWidth at its
//   scale is drawn at the largest integer scale that fits (default style: lines
//   over about 41 characters drop from scale 3 to 2); if the lines are too tall
//   for statusMaxHeight, every scale shrinks.
// - The image is the panel: the text plus padding and border, each side
//   rounded up to a multiple of 4, at most statusMaxWidth x statusMaxHeight.
// - No lines, or only empty ones: a 4x4 fully transparent image.
inline StatusImage renderStatus(const std::vector<std::string>& lines, const StatusStyle& style = StatusStyle{}) {
  using namespace status_detail;
  struct Line { std::string text; bool heading; uint32_t units, left, scale; };  // units: ink width in font px
  std::vector<Line> layout;
  for (std::size_t i = 0; i < lines.size() && layout.size() < statusMaxLines; ++i) {
    std::string text = asciiFold(lines[i]);
    const std::size_t first = text.find_first_not_of(' ');
    if (first == std::string::npos) continue;
    text = ellipsize(text.substr(first, text.find_last_not_of(' ') - first + 1), statusMaxChars);
    const auto head = static_cast<unsigned char>(text.front()), tail = static_cast<unsigned char>(text.back());
    const uint32_t left = inkLeft(head), units = uint32_t(text.size() - 1) * 8 + inkRight(tail) + 1 - left;
    layout.push_back({std::move(text), i == 0 && style.firstLineIsHeading, units, left, 0});
  }
  if (layout.empty()) return {4, 4, std::vector<uint8_t>(64, 0)};

  // Fit: per-line width limit, then shrink the scale caps until the block fits.
  const uint32_t borderWidth = std::min(style.borderWidth, 4u);
  const uint32_t inset = std::min(style.padding, 64u) + borderWidth, gap = std::min(style.lineGap, 64u);
  const uint32_t innerWidth = statusMaxWidth - 2 * inset, innerHeight = statusMaxHeight - 2 * inset;
  uint32_t bodyCap = std::clamp(style.scale, 1u, 16u), headingCap = std::clamp(style.headingScale, 1u, 16u);
  const auto scaleOf = [&](const Line& line) {
    return std::max(1u, std::min(line.heading ? headingCap : bodyCap, innerWidth / line.units));
  };
  const auto contentHeight = [&] {
    uint32_t height = gap * uint32_t(layout.size() - 1);
    for (const auto& line : layout) height += 8 * scaleOf(line);
    return height;
  };
  while (contentHeight() > innerHeight && (bodyCap > 1 || headingCap > 1)) {
    if (headingCap > bodyCap) --headingCap;
    else if (bodyCap > headingCap) --bodyCap;
    else { --bodyCap; --headingCap; }
  }
  uint32_t contentWidth = 0;
  for (auto& line : layout) {
    line.scale = scaleOf(line);
    contentWidth = std::max(contentWidth, line.units * line.scale);
  }
  const uint32_t textHeight = contentHeight();
  const uint32_t width = (contentWidth + 2 * inset + 3) & ~3u, height = (textHeight + 2 * inset + 3) & ~3u;

  // Panel. Rows between the corner bands are identical; corners are mirrored.
  const uint32_t radius = std::min({style.cornerRadius, 64u, width / 2, height / 2});
  const uint32_t band = std::min(std::max(radius, borderWidth + 1), std::min(width, height) / 2);
  const auto panelAt = [&](uint32_t x, uint32_t y) {
    return panelPixel(roundedRectDistance(x, y, width, height, float(radius)), float(borderWidth), style.background, style.border);
  };
  const auto put = [](uint8_t* row, uint32_t x, const Rgba& c) { std::memcpy(row + 4 * std::size_t(x), c.data(), 4); };
  StatusImage image{width, height, std::vector<uint8_t>(std::size_t(width) * height * 4)};
  std::vector<uint8_t> row(std::size_t(width) * 4);
  const auto fillRow = [&](uint32_t y) {
    for (uint32_t x = 0; x < band; ++x) {
      const Rgba c = panelAt(x, y);
      put(row.data(), x, c);
      put(row.data(), width - 1 - x, c);
    }
    const Rgba middle = panelAt(band, y);
    for (uint32_t x = band; x < width - band; ++x) put(row.data(), x, middle);
  };
  fillRow(band);
  for (uint32_t y = band; y < height - band; ++y) std::memcpy(&image.rgba[std::size_t(y) * width * 4], row.data(), row.size());
  for (uint32_t y = 0; y < band; ++y) {
    fillRow(y);
    std::memcpy(&image.rgba[std::size_t(y) * width * 4], row.data(), row.size());
    std::memcpy(&image.rgba[std::size_t(height - 1 - y) * width * 4], row.data(), row.size());
  }

  // Text, centred on its ink; glyph coverage is cached per scale.
  const Rgba interior = panelAt(band, band);
  struct GlyphCache { uint32_t scale; std::array<std::vector<uint8_t>, 128> glyphs; };
  std::vector<GlyphCache> caches;
  caches.reserve(statusMaxLines);
  uint32_t top = (height - textHeight) / 2;
  for (const auto& line : layout) {
    const uint32_t scale = line.scale, size = 8 * scale;
    auto cache = std::find_if(caches.begin(), caches.end(), [&](const GlyphCache& c) { return c.scale == scale; });
    if (cache == caches.end()) cache = caches.insert(caches.end(), GlyphCache{scale, {}});
    const StatusColor& color = line.heading ? style.heading : style.text;
    std::array<Rgba, 5> overInterior{};  // the common case: text over plain background
    for (std::size_t n = 0; n < 5; ++n) { overInterior[n] = interior; blendOver(overInterior[n].data(), color, coverageLevel[n]); }
    int x = int(width - line.units * scale) / 2 - int(line.left * scale);
    for (const char ch : line.text) {
      const auto code = static_cast<unsigned char>(ch & 0x7F);
      if (code != ' ') {
        auto& glyph = cache->glyphs[code];
        if (glyph.empty()) glyph = glyphCoverage(code, scale);
        // Smoothing never grows the ink, so only the font glyph's ink box is visited.
        const int from = std::max(int(inkLeft(code) * scale), -x);
        const int to = std::min(int((inkRight(code) + 1) * scale), int(width) - x);
        for (uint32_t gy = inkTop(code) * scale; gy < (inkBottom(code) + 1) * scale; ++gy) {
          uint8_t* out = image.rgba.data() + (std::size_t(top + gy) * width + std::size_t(x + from)) * 4;
          const uint8_t* coverage = glyph.data() + std::size_t(gy) * size;
          for (int gx = from; gx < to; ++gx, out += 4) {
            const uint8_t n = coverage[gx];
            if (std::memcmp(out, interior.data(), 4) == 0) std::memcpy(out, overInterior[n].data(), 4);
            else if (n) blendOver(out, color, coverageLevel[n]);
          }
        }
      }
      x += int(size);
    }
    top += size + gap;
  }
  return image;
}

}  // namespace vrost
