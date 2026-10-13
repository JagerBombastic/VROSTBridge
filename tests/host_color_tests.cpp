// 1.0.0.5 (patch 0018): the host's check of what its RGB to Y'CbCr converter produced
// (alvr/server_openvr/cpp/platform/win32/VROSTBridgeColor.h). The header has no Windows or AMF
// types; tests/test_vrostbridge_1005.py takes it from patches/0018-host-exact-colour-and-pose-drops.patch,
// builds this file with it and runs it. Each case encodes the test image as a converter would
// (matrix, range, depth; or with a fault) and checks what the host concludes.
#include "VROSTBridgeColor.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>

using namespace vrost_color;

namespace {
int failures = 0, checks = 0;
void check(bool ok, const char *what) {
  ++checks;
  if (!ok) {
    ++failures;
    std::printf("FAIL %s\n", what);
  }
}

// The test image as a converter with these properties would produce it. lift: black raised by this
// many 8-bit codes (the 1.0.0.4 fault); gamma: a tone curve applied first; swap: Cb and Cr swapped.
Measured convert(int bits, bool full, double kr, double kb, double gamma = 1.0, bool swap = false, int lift = 0) {
  Measured m;
  m.bits = bits;
  const double top = (1 << bits) - 1, step = 1 << (bits - 8);
  for (int i = 0; i < kColours; ++i) {
    const double r = std::pow(kTestColours[i][0] / 255.0, gamma), g = std::pow(kTestColours[i][1] / 255.0, gamma),
                 b = std::pow(kTestColours[i][2] / 255.0, gamma);
    const double y = kr * r + (1 - kr - kb) * g + kb * b;
    const double cb = (b - y) / (2 * (1 - kb)), cr = (r - y) / (2 * (1 - kr));
    double Y, U, V;
    if (full) {
      Y = y * top;
      U = (1 << (bits - 1)) + cb * top;
      V = (1 << (bits - 1)) + cr * top;
    } else {
      Y = (16 + 219 * y) * step;
      U = (128 + 224 * cb) * step;
      V = (128 + 224 * cr) * step;
    }
    Y += lift * step * (1 - y);
    const auto clip = [&](double v) { return int(std::lround(std::min(top, std::max(0.0, v)))); };
    m.code[i][0] = clip(Y);
    m.code[i][1] = clip(swap ? V : U);
    m.code[i][2] = clip(swap ? U : V);
  }
  return m;
}
}  // namespace

int main() {
  const struct {
    int matrix;
    double kr, kb;
  } matrices[] = {{709, .2126, .0722}, {601, .299, .114}, {2020, .2627, .0593}};
  for (int bits : {8, 10})
    for (bool full : {true, false})
      for (const auto &matrix : matrices) {
        const Verdict v = Classify(convert(bits, full, matrix.kr, matrix.kb));
        check(v.known && v.full == full && v.matrix == matrix.matrix, "range and matrix recognised");
        check(v.displayable == (matrix.matrix != 2020), "BT.2020 is not shown by the client");
        check(v.profile == ProfileFor(full, matrix.matrix) && IsFullRangeProfile(v.profile) == full &&
                  MatrixOfProfile(v.profile) == matrix.matrix,
              "the profile describes it");
      }
  // What the stream is labelled with when the converter ignored the requested full range: studio
  // BT.709, so the headset expands 64..940 back to black..white.
  check(Classify(convert(10, false, .2126, .0722)).profile == kProfile709, "studio BT.709 relabelled");
  check(Classify(convert(10, true, .2126, .0722)).profile == kProfileFull709, "full BT.709 kept");
  // The 1.0.0.4 fault: black at about 8 % in a full-range stream is neither range: refused (8-bit).
  check(std::string(Classify(convert(10, true, .2126, .0722, 1.0, false, 20)).reason) == "black-or-white-level",
        "lifted black refused");
  for (int bits : {8, 10}) {
    check(!Classify(convert(bits, true, .2126, .0722, 2.2)).known, "a tone curve is refused");
    check(std::string(Classify(convert(bits, true, .2126, .0722, 1.0, true)).reason) == "chroma-order",
          "swapped chroma refused");
  }
  // Rounding in the converter (a few codes at 10 bits) is accepted.
  Measured m = convert(10, true, .2126, .0722);
  m.code[0][0] += 2;
  m.code[1][0] -= 3;
  m.code[3][1] += 2;
  const Verdict rounded = Classify(m);
  check(rounded.known && rounded.full && rounded.matrix == 709, "converter rounding");
  Measured cast = convert(8, true, .2126, .0722);
  cast.code[3][1] += 6;
  check(std::string(Classify(cast).reason) == "neutral-has-colour", "a colour cast on grey refused");
  Measured unknown;
  unknown.bits = 12;
  check(std::string(Classify(unknown).reason) == "bit-depth", "unknown depth refused");
  if (failures) return 1;
  std::printf("host colour check: %d checks passed\n", checks);
  return 0;
}
