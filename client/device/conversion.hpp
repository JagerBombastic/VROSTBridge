#pragma once
// Colour-conversion and foveation maths shared by the GPU conversion setup and the
// CPU reference used in tests. The compute shader (client/platform/linux/convert.comp)
// implements the same formulas; tests run that shader on a software Vulkan device and
// compare it with these functions.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace vrost {

// YCbCr range constants for normalised texture samples. A plane sampled as UNORM8
// gives code/255; a P010 plane sampled as UNORM16 keeps the 10-bit code in its top
// bits, so it gives code*64/65535.
struct RangeConstants {
  float lumaOffset, lumaScale, chromaCentre, chromaScale;
};
inline RangeConstants rangeConstants(int bits, bool fullRange, bool sixteenBitContainer) {
  const double unit = sixteenBitContainer ? double(1 << (16 - bits)) / 65535.0 : 1.0 / ((1 << bits) - 1);
  const double step = double(1 << (bits - 8));
  if (fullRange) {
    const double max = (1 << bits) - 1;
    return {0.f, float(1.0 / (max * unit)), float((1 << (bits - 1)) * unit), float(1.0 / (max * unit))};
  }
  return {float(16 * step * unit), float(1.0 / (219 * step * unit)), float(128 * step * unit),
          float(1.0 / (224 * step * unit))};
}

// Rows of the Y'CbCr -> R'G'B' matrix for luma coefficients kr, kb (BT.709 or BT.601).
struct ConversionMatrix {
  float row[3][3];
};
inline ConversionMatrix conversionMatrix(float kr, float kb) {
  const float kg = 1 - kr - kb;
  return {{{1, 0, 2 * (1 - kr)},
           {1, -2 * kb * (1 - kb) / kg, -2 * kr * (1 - kr) / kg},
           {1, 2 * (1 - kb), 0}}};
}

// Chroma sample position as an offset in luma pixels (FFmpeg AVChromaLocation values).
// Chroma coordinate = (luma coordinate + offset) / 2, with pixel-edge coordinates.
// H.264/HEVC/AV1 streams without explicit siting use MPEG-2 "left" siting.
struct ChromaSiting {
  float x, y;
};
inline ChromaSiting chromaSiting(int location) {
  switch (location) {
  case 2: return {0.f, 0.f};     // centre
  case 3: return {.5f, .5f};     // top-left
  case 4: return {0.f, .5f};     // top
  case 5: return {.5f, -.5f};    // bottom-left
  case 6: return {0.f, -.5f};    // bottom
  default: return {.5f, 0.f};    // left, or unspecified
  }
}

// Interleaved gradient noise in [-0.5, 0.5): ordered dither for 8-bit output.
inline float ditherNoise(int x, int y) {
  const float v = 52.9829189f * std::fmod(0.06711056f * float(x) + 0.00583715f * float(y), 1.0f);
  return v - std::floor(v) - .5f;
}

// ALVR foveated encoding, one axis. Host (CompressAxisAlignedPixelShader.hlsl):
// compressed coordinate -> original coordinate. Both in 0..1 eye coordinates; the right
// eye uses horizontally mirrored coordinates.
template <class T> inline T foveationCompressAxis(T eyeUV, T centerSize, T centerShift, T edgeRatio) {
  const T c0 = (1 - centerSize) / 2;
  const T c1 = (edgeRatio - 1) * c0 * (centerShift + 1) / edgeRatio;
  const T c2 = (edgeRatio - 1) * centerSize + 1;
  const T loBound = c0 * (centerShift + 1) / c2;
  const T hiBound = c0 * (centerShift - 1) / c2 + 1;
  const T center = eyeUV * c2 / edgeRatio + c1;
  if (eyeUV < loBound) {
    const T d2 = eyeUV * c2;
    const T g1 = eyeUV / std::max(loBound, T(1e-6));
    return g1 * center + (1 - g1) * d2;
  }
  if (eyeUV > hiBound) {
    const T d3 = (eyeUV - 1) * c2 + 1;
    const T g2 = (1 - eyeUV) / std::max(1 - hiBound, T(1e-6));
    return g2 * center + (1 - g2) * d3;
  }
  return center;
}

// Client inverse (alvr/graphics/resources/stream.wgsl): original -> compressed coordinate.
// Templated so tests can check the algebra in double precision; the shader uses float.
template <class T> inline T foveationDecompressAxis(T u, T centerSize, T shift, T edgeRatio) {
  const T c0 = (1 - centerSize) * T(.5);
  const T c2 = (edgeRatio - 1) * centerSize + 1;
  const T c1 = (edgeRatio - 1) * c0 * (shift + 1) / edgeRatio;
  const T loBound = c0 * (shift + 1);
  const T hiBound = c0 * (shift - 1) + 1;
  const T loBoundC = c0 * (shift + 1) / c2;
  const T hiBoundC = c0 * (shift - 1) / c2 + 1;
  if (u < loBound) {
    const T a = c2 * (1 - edgeRatio) / (edgeRatio * loBoundC);
    const T b = (c1 + c2 * loBoundC) / loBoundC;
    return (-b + std::sqrt(std::max(b * b + 4 * a * u, T(0)))) / (2 * a);
  }
  if (u > hiBound) {
    const T a = c2 * (edgeRatio - 1) / (edgeRatio * (1 - hiBoundC));
    const T b = (c2 - edgeRatio * c1 - 2 * edgeRatio * c2 + c2 * edgeRatio * (1 - hiBoundC) + edgeRatio) /
                (edgeRatio * (1 - hiBoundC));
    const T c = (c2 * edgeRatio - c2) * (c1 - hiBoundC + c2 * hiBoundC) / (edgeRatio * (1 - hiBoundC) * (1 - hiBoundC));
    return (-b + std::sqrt(std::max(b * b - 4 * (c - a * u), T(0)))) / (2 * a);
  }
  return (u - c1) * edgeRatio / c2;
}

// d(compressed)/d(original) of foveationDecompressAxis at u (RC4.4): 1/sqrt(discriminant) in the
// reduced edges (the derivative of the quadratic's root), the constant slope in the centre.
// Encoded texels per view pixel along the axis = slope * encoded size / view size: 1 in the
// full-resolution centre, falling to 1/(2 x edgeRatio - 1) at the outer edge. fused.comp uses it to
// fade sharpening out where the picture was sent reduced.
template <class T> inline T foveationDecompressSlope(T u, T centerSize, T shift, T edgeRatio) {
  const T c0 = (1 - centerSize) * T(.5);
  const T c2 = (edgeRatio - 1) * centerSize + 1;
  const T c1 = (edgeRatio - 1) * c0 * (shift + 1) / edgeRatio;
  const T loBound = c0 * (shift + 1);
  const T hiBound = c0 * (shift - 1) + 1;
  const T loBoundC = c0 * (shift + 1) / c2;
  const T hiBoundC = c0 * (shift - 1) / c2 + 1;
  if (u < loBound) {
    const T a = c2 * (1 - edgeRatio) / (edgeRatio * loBoundC);
    const T b = (c1 + c2 * loBoundC) / loBoundC;
    return 1 / std::max(std::sqrt(std::max(b * b + 4 * a * u, T(0))), T(1e-6));
  }
  if (u > hiBound) {
    const T a = c2 * (edgeRatio - 1) / (edgeRatio * (1 - hiBoundC));
    const T b = (c2 - edgeRatio * c1 - 2 * edgeRatio * c2 + c2 * edgeRatio * (1 - hiBoundC) + edgeRatio) /
                (edgeRatio * (1 - hiBoundC));
    const T c = (c2 * edgeRatio - c2) * (c1 - hiBoundC + c2 * hiBoundC) / (edgeRatio * (1 - hiBoundC) * (1 - hiBoundC));
    return 1 / std::max(std::sqrt(std::max(b * b - 4 * (c - a * u), T(0))), T(1e-6));
  }
  return edgeRatio / c2;
}

// Negotiated foveated encoding plus this frame's centres.
struct FoveationParams {
  bool enabled = false;
  uint32_t encoded[2]{};
  float viewRatio[2]{1, 1}, centerSize[2]{1, 1}, edgeRatio[2]{1, 1};
  float shifts[2][2]{};
};

// Output pixel (eye, local u/v in 0..1, unmirrored) -> location in the encoded eye image
// (local 0..1 of the encoded eye half, unmirrored).
inline void foveatedSource(int eye, float u, float v, const FoveationParams &f, float &outU, float &outV) {
  if (!f.enabled) {
    outU = u;
    outV = v;
    return;
  }
  const float x = eye ? 1.f - u : u;
  float cx = foveationDecompressAxis(x, f.centerSize[0], f.shifts[eye][0], f.edgeRatio[0]) * f.viewRatio[0];
  const float cy = foveationDecompressAxis(v, f.centerSize[1], f.shifts[eye][1], f.edgeRatio[1]) * f.viewRatio[1];
  if (eye) cx = 1.f - cx;
  outU = cx;
  outV = cy;
}

// Shader push constants (128 bytes, the Vulkan guaranteed minimum).
struct ConversionPushConstants {
  float row0[4];         // matrix row 0; w: chroma centre
  float row1[4];         // matrix row 1; w: dither amplitude in 8-bit steps (0 = off)
  float row2[4];         // matrix row 2; w: 1 when foveated
  float adjustment[4];   // x: luma offset, y: luma scale, z: chroma scale
  float chromaSiting[4]; // xy: chroma sample offset in luma pixels
  float fovSize[4];      // xy: centre size, zw: edge ratio
  float fovView[4];      // xy: view ratio
  float fovShifts[4];    // xy: left eye centre shift, zw: right eye centre shift
};
static_assert(sizeof(ConversionPushConstants) == 128, "Vulkan guarantees 128 bytes of push constants");

inline ConversionPushConstants conversionPushConstants(const ConversionMatrix &m, const RangeConstants &r,
                                                       ChromaSiting siting, float dither,
                                                       const FoveationParams &f) {
  ConversionPushConstants pc{};
  for (int i = 0; i < 3; ++i) {
    pc.row0[i] = m.row[0][i];
    pc.row1[i] = m.row[1][i];
    pc.row2[i] = m.row[2][i];
  }
  pc.row0[3] = r.chromaCentre;
  pc.row1[3] = dither;
  pc.row2[3] = f.enabled ? 1.f : 0.f;
  pc.adjustment[0] = r.lumaOffset;
  pc.adjustment[1] = r.lumaScale;
  pc.adjustment[2] = r.chromaScale;
  pc.chromaSiting[0] = siting.x;
  pc.chromaSiting[1] = siting.y;
  pc.fovSize[0] = f.centerSize[0];
  pc.fovSize[1] = f.centerSize[1];
  pc.fovSize[2] = f.edgeRatio[0];
  pc.fovSize[3] = f.edgeRatio[1];
  pc.fovView[0] = f.viewRatio[0];
  pc.fovView[1] = f.viewRatio[1];
  pc.fovShifts[0] = f.shifts[0][0];
  pc.fovShifts[1] = f.shifts[0][1];
  pc.fovShifts[2] = f.shifts[1][0];
  pc.fovShifts[3] = f.shifts[1][1];
  return pc;
}

// RC4.3 fused frame pass (client/platform/linux/fused.comp): one pass per displayed frame
// from the decoded planes to the image SteamVR shows (conversion, foveation inverse, re-aim
// and sharpening). Its parameters: the conversion constants above, repacked, plus each eye's
// re-aim homography (core/reaim.hpp) and the sharpening amount. Until 1.0.0.10 these were the
// pass's push constants; 1.0.0.11 derives the pass's per-frame constants from them
// (fusedFrameConstants below) and the CPU model in tests/fused_shader_tests.cpp reads them.
struct FusedParameters {
  float row0[4];       // matrix row 0; w: chroma centre
  float row1[4];       // matrix row 1; w: dither amplitude in 8-bit steps (0 = off)
  float row2[4];       // matrix row 2; w: 1 when foveated
  float adjustment[4]; // x: luma offset, y: luma scale, z: chroma scale, w: sharpening amount (0 = off)
  float sitingView[4]; // xy: chroma sample offset in luma pixels, zw: foveation view ratio
  float fovSize[4];    // xy: centre size, zw: edge ratio
  float fovShifts[4];  // xy: left eye centre shift, zw: right eye centre shift
  float eyeRows[2][3][4]; // re-aim homography rows per eye (identity when not re-aiming)
};

// Sharpening amount for the fused pass: the companion's levels (off/low/medium/high) arrive
// as a percentage of the unsharp amount on luma (0..200).
inline float sharpeningAmount(int percent) { return float(std::clamp(percent, 0, 200)) / 100.f; }

inline FusedParameters fusedParameters(const ConversionPushConstants &c, const float (&rows)[2][3][4],
                                       float sharpening) {
  FusedParameters f{};
  for (int i = 0; i < 4; ++i) {
    f.row0[i] = c.row0[i];
    f.row1[i] = c.row1[i];
    f.row2[i] = c.row2[i];
    f.adjustment[i] = c.adjustment[i];
    f.fovSize[i] = c.fovSize[i];
    f.fovShifts[i] = c.fovShifts[i];
  }
  f.adjustment[3] = std::isfinite(sharpening) ? std::clamp(sharpening, 0.f, 2.f) : 0.f;
  f.sitingView[0] = c.chromaSiting[0];
  f.sitingView[1] = c.chromaSiting[1];
  f.sitingView[2] = c.fovView[0];
  f.sitingView[3] = c.fovView[1];
  for (int e = 0; e < 2; ++e)
    for (int r = 0; r < 3; ++r)
      for (int k = 0; k < 4; ++k) f.eyeRows[e][r][k] = rows[e][r][k];
  return f;
}

// 1.0.0.11: what fused.comp reads, computed once per frame on the processor: its uniform
// buffer (binding 3, std140; struct Frame there). The foveation inverse of each eye and axis
// becomes a few constants per region of the axis, with the luma texture's size, the right
// eye's mirrored x and its offset folded in, so a pixel needs one square root and one
// reciprocal per axis instead of the inverse's ten divisions. The colour constants are
// FusedParameters' rows; the texture and output sizes and each eye's texel bounds (the taps
// stay inside each eye) are here so the pass reads no texture size either.
//
// Along one axis of one eye, with u the view position (0..1) and m = u * bounds[2] + bounds[3]
// (the right eye's x runs mirrored, as ALVR encodes it):
//   m < bounds[0] (reduced low edge):  r = sqrt(max(low[0] m + low[1], 0)), texel = low[2] r + low[3]
//   m > bounds[1] (reduced high edge): the same with high[]
//   otherwise (full-resolution centre): texel = centre[0] m + centre[1]
// texel: luma texel position (both eyes side by side, centres at .5); texels one output pixel
// spans: centre[2] in the centre, centre[3] / r in the edges (r is the inverse's 1 / slope).
struct FusedAxis {
  float bounds[4];
  float low[4];
  float high[4];
  float centre[4];
};
struct FusedEye {
  float rows[3][4];      // re-aim homography rows (output view position -> rendered view position)
  FusedAxis axis[2];     // x, y
  float lumaBounds[4];   // xy: first, zw: last luma texel centre of this eye
  float chromaLow[4];    // xy: first chroma texel centre of this eye; zw: chroma position = luma position / 2 + zw
  float chromaHigh[4];   // xy: last chroma texel centre of this eye
};
struct FusedFrame {
  float row0[4];       // matrix row 0; w: chroma centre
  float row1[4];       // matrix row 1; w: dither amplitude in 8-bit steps (0 = off)
  float row2[4];       // matrix row 2
  float adjustment[4]; // x: luma offset, y: luma scale, z: chroma scale, w: sharpening amount (0 = off)
  float sizes[4];      // xy: 1 / luma size, zw: 1 / chroma size (texels)
  float outputEye[4];  // xy: output size of one eye (pixels), zw: its reciprocal
  FusedEye eye[2];
};
static_assert(sizeof(FusedAxis) == 64 && sizeof(FusedEye) == 224 && sizeof(FusedFrame) == 544,
              "fused.comp's std140 uniform buffer");

// One axis of one eye (see FusedAxis). scale: luma texels per unit of the encoded eye along the axis
// (view ratio x encoded eye size); origin: the eye's first texel (the right eye starts at half the luma
// width); pixels: the output eye's size along the axis. Without foveation the axis is the identity.
// Computed in double from the same terms as foveationDecompressAxis; an empty edge region (a centre
// shifted to the border) or an edge ratio of 1 (no reduction) leaves only the linear centre.
inline FusedAxis fusedAxis(bool foveated, double centerSize, double shift, double edgeRatio, double viewRatio,
                           double encodedEye, double origin, bool mirrored, double pixels) {
  FusedAxis a{};
  const double never = 1e30;
  const double sign = mirrored ? -1.0 : 1.0;
  const double base = origin + (mirrored ? encodedEye : 0.0);
  const double scale = (foveated ? viewRatio : 1.0) * encodedEye;
  a.bounds[0] = float(-never);
  a.bounds[1] = float(never);
  a.bounds[2] = mirrored ? -1.f : 1.f;
  a.bounds[3] = mirrored ? 1.f : 0.f;
  a.centre[3] = float(scale / pixels);
  if (!foveated || std::fabs(edgeRatio - 1.0) < 1e-6) {
    // texel = base + sign * scale * m (the inverse is the identity when nothing is reduced)
    a.centre[0] = float(sign * scale);
    a.centre[1] = float(base);
    a.centre[2] = float(scale / pixels);
    return a;
  }
  const double c0 = (1 - centerSize) * .5;
  const double c2 = (edgeRatio - 1) * centerSize + 1;
  const double c1 = (edgeRatio - 1) * c0 * (shift + 1) / edgeRatio;
  const double loBound = c0 * (shift + 1), hiBound = c0 * (shift - 1) + 1;
  const double loBoundC = c0 * (shift + 1) / c2, hiBoundC = c0 * (shift - 1) / c2 + 1;
  const double k = edgeRatio / c2; // the centre's slope
  a.centre[0] = float(sign * scale * k);
  a.centre[1] = float(base - sign * scale * k * c1);
  a.centre[2] = float(scale * k / pixels);
  if (loBound > 1e-9 && loBoundC > 1e-12) {
    const double qa = c2 * (1 - edgeRatio) / (edgeRatio * loBoundC);
    const double qb = (c1 + c2 * loBoundC) / loBoundC;
    a.bounds[0] = float(loBound);
    a.low[0] = float(4 * qa);
    a.low[1] = float(qb * qb);
    a.low[2] = float(sign * scale / (2 * qa));
    a.low[3] = float(base - sign * scale * qb / (2 * qa));
  }
  if (hiBound < 1 - 1e-9 && 1 - hiBoundC > 1e-12) {
    const double qa = c2 * (edgeRatio - 1) / (edgeRatio * (1 - hiBoundC));
    const double qb = (c2 - edgeRatio * c1 - 2 * edgeRatio * c2 + c2 * edgeRatio * (1 - hiBoundC) + edgeRatio) /
                      (edgeRatio * (1 - hiBoundC));
    const double qc = (c2 * edgeRatio - c2) * (c1 - hiBoundC + c2 * hiBoundC) / (edgeRatio * (1 - hiBoundC) * (1 - hiBoundC));
    a.bounds[1] = float(hiBound);
    a.high[0] = float(4 * qa);
    a.high[1] = float(qb * qb - 4 * qc);
    a.high[2] = float(sign * scale / (2 * qa));
    a.high[3] = float(base - sign * scale * qb / (2 * qa));
  }
  return a;
}

// fused.comp's axisTexel on the processor, in float as the GPU evaluates it (tests).
inline void fusedAxisTexel(const FusedAxis &a, float u, float &texel, float &texelsPerPixel) {
  const float m = u * a.bounds[2] + a.bounds[3];
  const float *edge = m < a.bounds[0] ? a.low : m > a.bounds[1] ? a.high : nullptr;
  if (!edge) {
    texel = a.centre[0] * m + a.centre[1];
    texelsPerPixel = a.centre[2];
    return;
  }
  const float r = std::sqrt(std::max(edge[0] * m + edge[1], 0.f));
  texel = edge[2] * r + edge[3];
  texelsPerPixel = a.centre[3] / std::max(r, 1e-6f);
}

// The frame's constants for one output image: parameters p, the decoded luma planes' size (both eyes
// side by side; chroma is half of it, rounded up) and the size each eye is shown at.
inline FusedFrame fusedFrameConstants(const FusedParameters &p, uint32_t lumaWidth, uint32_t lumaHeight,
                                      uint32_t outputEyeWidth, uint32_t outputHeight) {
  FusedFrame f{};
  for (int i = 0; i < 4; ++i) {
    f.row0[i] = p.row0[i];
    f.row1[i] = p.row1[i];
    f.row2[i] = p.row2[i];
    f.adjustment[i] = p.adjustment[i];
  }
  f.row2[3] = 0.f;
  const uint32_t chromaWidth = (lumaWidth + 1) / 2, chromaHeight = (lumaHeight + 1) / 2;
  const double lumaEye = lumaWidth * .5, chromaEye = chromaWidth * .5;
  f.sizes[0] = float(1.0 / std::max(1u, lumaWidth));
  f.sizes[1] = float(1.0 / std::max(1u, lumaHeight));
  f.sizes[2] = float(1.0 / std::max(1u, chromaWidth));
  f.sizes[3] = float(1.0 / std::max(1u, chromaHeight));
  f.outputEye[0] = float(outputEyeWidth);
  f.outputEye[1] = float(outputHeight);
  f.outputEye[2] = float(1.0 / std::max(1u, outputEyeWidth));
  f.outputEye[3] = float(1.0 / std::max(1u, outputHeight));
  const bool foveated = p.row2[3] >= .5f;
  for (int eye = 0; eye < 2; ++eye) {
    FusedEye &e = f.eye[eye];
    for (int r = 0; r < 3; ++r)
      for (int k = 0; k < 4; ++k) e.rows[r][k] = p.eyeRows[eye][r][k];
    e.axis[0] = fusedAxis(foveated, p.fovSize[0], p.fovShifts[eye * 2], p.fovSize[2], p.sitingView[2], lumaEye,
                          eye * lumaEye, eye == 1, double(outputEyeWidth));
    e.axis[1] = fusedAxis(foveated, p.fovSize[1], p.fovShifts[eye * 2 + 1], p.fovSize[3], p.sitingView[3],
                          double(lumaHeight), 0.0, false, double(outputHeight));
    e.lumaBounds[0] = float(eye * lumaEye + .5);
    e.lumaBounds[1] = .5f;
    e.lumaBounds[2] = float((eye + 1) * lumaEye - .5);
    e.lumaBounds[3] = float(lumaHeight - .5);
    // Chroma at (luma position within the eye + siting) / 2, inside this eye's half of the chroma plane.
    e.chromaLow[0] = float(eye * chromaEye + .5);
    e.chromaLow[1] = .5f;
    e.chromaLow[2] = float(p.sitingView[0] * .5 - eye * lumaEye * .5 + eye * chromaEye);
    e.chromaLow[3] = float(p.sitingView[1] * .5);
    e.chromaHigh[0] = float((eye + 1) * chromaEye - .5);
    e.chromaHigh[1] = float(chromaHeight - .5);
  }
  return f;
}

// Centre of one eye's full-resolution region along one axis, in that eye's image (0..1),
// for a foveation centre shift (ALVR's region spans [c0 (1 + shift), 1 - c0 (1 - shift)]
// with c0 = (1 - centre size) / 2). The right eye's horizontal shift is mirrored.
inline float foveationRegionCentre(float centerSize, float shift, bool mirrored) {
  const float centre = .5f + (1.f - centerSize) * .5f * shift;
  return mirrored ? 1.f - centre : centre;
}

// 1.0.0.5: the size each eye is shown at (the frame pass's output, which SteamVR reads). A
// stream larger than SteamVR's recommended render size, the size its compositor needs for
// full quality on the panels, is shown at that size: the frame pass resamples it once on the
// way, and the pass and SteamVR handle up to a third fewer pixels per frame (Ultra on a
// Beyond: 3200 -> 2712 per eye). The stream keeps its extra detail from the host's encoder.
// Keeps the aspect ratio and never enlarges; a zero cap means no cap.
struct EyeSize {
  uint32_t width, height;
};
inline EyeSize shownEyeSize(uint32_t streamWidth, uint32_t streamHeight, uint32_t capWidth, uint32_t capHeight) {
  if (!streamWidth || !streamHeight || !capWidth || !capHeight ||
      (streamWidth <= capWidth && streamHeight <= capHeight))
    return {streamWidth, streamHeight};
  const double scale = std::min(double(capWidth) / streamWidth, double(capHeight) / streamHeight);
  const auto scaled = [scale](uint32_t size, uint32_t cap) {
    return std::min(cap, std::max<uint32_t>(16, uint32_t(std::lround(double(size) * scale))));
  };
  return {scaled(streamWidth, capWidth), scaled(streamHeight, capHeight)};
}

// 1.0.0.11: the shown size at a percentage (50..100) of it, when the companion lowers it while this PC's
// graphics fall behind: both sides scaled alike, rounded, never below 16 pixels; 100 % (or more) leaves it.
inline EyeSize scaledEyeSize(EyeSize shown, uint32_t percent) {
  if (percent >= 100 || !shown.width || !shown.height) return shown;
  percent = std::max<uint32_t>(percent, 50);
  const auto scaled = [percent](uint32_t size) { return std::max<uint32_t>(16, (size * percent + 50) / 100); };
  return {scaled(shown.width), scaled(shown.height)};
}

} // namespace vrost
