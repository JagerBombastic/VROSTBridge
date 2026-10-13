// CPU decoding as the Windows client does it (1.0.0.7; client/platform/linux/decoder.cpp, decoded_planes.hpp,
// planar_upload.hpp), with the FFmpeg the client ships: small H.264 streams (8-bit, 8-bit full range
// and 10-bit; tests/fixtures/software_decode_*.h264) decode with the client's settings (low delay, slice
// threads), give the frame formats the planar upload accepts (yuv420p, yuvj420p, yuv420p10le), and
// writePlanar lays each decoded frame's planes out exactly as the plane images expect them (NV12 or
// P010: Cb and Cr interleaved, 10-bit codes in the top bits), its padded rows included.
// Usage: software_decode_tests <8-bit stream> <8-bit full-range stream> <10-bit stream>.
#include "client/device/decoded_planes.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL line %d: %s ", __LINE__, #cond);                                           \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

struct Expected {
  const char *name;
  vrost::PlanarFormat format;
  bool fullRange;
};

// Decodes every frame of an Annex B stream and checks each one's upload.
int decodeAndCheck(const std::string &path, const Expected &expected) {
  std::ifstream file(path, std::ios::binary);
  const std::vector<uint8_t> stream((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
  CHECK(!stream.empty(), "%s is missing", path.c_str());
  const AVCodec *implementation = avcodec_find_decoder(AV_CODEC_ID_H264);
  AVCodecContext *codec = avcodec_alloc_context3(implementation);
  AVCodecParserContext *parser = av_parser_init(AV_CODEC_ID_H264);
  AVPacket *packet = av_packet_alloc();
  AVFrame *frame = av_frame_alloc();
  if (!implementation || !codec || !parser || !packet || !frame) {
    std::printf("FAIL: FFmpeg's H.264 decoder or parser is unavailable\n");
    return 0;
  }
  // The client's CPU decoding settings on Windows.
  codec->thread_count = 0;
  codec->flags |= AV_CODEC_FLAG_LOW_DELAY;
  if (avcodec_open2(codec, implementation, nullptr) < 0) {
    std::printf("FAIL: cannot open the H.264 decoder\n");
    return 0;
  }
  int frames = 0;
  auto receive = [&] {
    while (avcodec_receive_frame(codec, frame) == 0) {
      ++frames;
      AVPixelFormat download = AV_PIX_FMT_NONE;
      vrost::PlanarFormat format{};
      try {
        format = vrost::planarFormat(frame, download);
      } catch (const std::exception &e) {
        CHECK(false, "%s: %s", expected.name, e.what());
        av_frame_unref(frame);
        continue;
      }
      CHECK(format == expected.format && download == AV_PIX_FMT_NONE, "%s: %s frames", expected.name,
            vrost::pixelFormatName(frame->format));
      CHECK((frame->color_range == AVCOL_RANGE_JPEG) == expected.fullRange, "%s: colour range %d", expected.name,
            int(frame->color_range));
      const bool ten = vrost::planarTenBit(format);
      const auto layout = vrost::planarLayout(uint32_t(frame->width), uint32_t(frame->height), ten);
      std::vector<uint8_t> staging(layout.size, 0x5A);
      vrost::writePlanar(layout, staging.data(), format, frame->data, frame->linesize);
      // Every sample where the plane images expect it.
      size_t wrong = 0;
      auto sample = [&](const uint8_t *row, uint32_t x) { return ten ? uint32_t(row[2 * x] | row[2 * x + 1] << 8) : row[x]; };
      auto stored = [&](size_t at) { return ten ? uint32_t(staging[at] | staging[at + 1] << 8) : staging[at]; };
      const uint32_t bytes = ten ? 2 : 1;
      for (uint32_t y = 0; y < layout.height; ++y)
        for (uint32_t x = 0; x < layout.width; ++x) {
          const uint32_t code = sample(frame->data[0] + y * frame->linesize[0], x);
          if (stored(y * layout.lumaPitch + x * bytes) != (ten ? code << 6 : code)) ++wrong;
        }
      for (uint32_t y = 0; y < layout.chromaHeight; ++y)
        for (uint32_t x = 0; x < layout.chromaWidth; ++x)
          for (int c = 0; c < 2; ++c) {
            const uint32_t code = sample(frame->data[1 + c] + y * frame->linesize[1 + c], x);
            if (stored(layout.chromaOffset + y * layout.chromaPitch + (2 * x + c) * bytes) != (ten ? code << 6 : code))
              ++wrong;
          }
      CHECK(wrong == 0, "%s: %zu samples misplaced", expected.name, wrong);
      std::printf("%-26s %dx%d %-11s rows %d/%d bytes, threads %d (%s): %s\n", expected.name, frame->width, frame->height,
                  vrost::pixelFormatName(frame->format), frame->linesize[0], frame->linesize[1], codec->thread_count,
                  codec->active_thread_type == FF_THREAD_SLICE ? "slices" : codec->active_thread_type ? "frames" : "one",
                  wrong ? "MISPLACED" : "exact");
      av_frame_unref(frame);
    }
  };
  const uint8_t *data = stream.data();
  size_t left = stream.size();
  while (left) {
    uint8_t *out = nullptr;
    int outSize = 0;
    const int used = av_parser_parse2(parser, codec, &out, &outSize, data, int(left), AV_NOPTS_VALUE, AV_NOPTS_VALUE, 0);
    if (used < 0) break;
    data += used;
    left -= size_t(used);
    if (outSize) {
      av_new_packet(packet, outSize);
      std::memcpy(packet->data, out, size_t(outSize));
      CHECK(avcodec_send_packet(codec, packet) >= 0, "%s: packet refused", expected.name);
      av_packet_unref(packet);
      receive();
    }
  }
  uint8_t *out = nullptr;
  int outSize = 0;
  av_parser_parse2(parser, codec, &out, &outSize, nullptr, 0, AV_NOPTS_VALUE, AV_NOPTS_VALUE, 0);
  if (outSize) {
    av_new_packet(packet, outSize);
    std::memcpy(packet->data, out, size_t(outSize));
    avcodec_send_packet(codec, packet);
    av_packet_unref(packet);
    receive();
  }
  avcodec_send_packet(codec, nullptr);
  receive();
  av_frame_free(&frame);
  av_packet_free(&packet);
  av_parser_close(parser);
  avcodec_free_context(&codec);
  return frames;
}
} // namespace

int main(int argc, char **argv) {
  if (argc < 4) {
    std::printf("usage: software_decode_tests 8bit.h264 8bit_full_range.h264 10bit.h264\n");
    return 2;
  }
  std::printf("FFmpeg libavcodec %s\n", AV_STRINGIFY(LIBAVCODEC_VERSION));
  const Expected cases[] = {{"H.264 8-bit", vrost::PlanarFormat::Yuv420p, false},
                            {"H.264 8-bit full range", vrost::PlanarFormat::Yuv420p, true},
                            {"H.264 10-bit", vrost::PlanarFormat::Yuv420p10, false}};
  for (int i = 0; i < 3; ++i) {
    const int frames = decodeAndCheck(argv[1 + i], cases[i]);
    CHECK(frames == 2, "%s: %d frames decoded", cases[i].name, frames);
  }
  if (failures) {
    std::printf("%d software decoding check(s) failed\n", failures);
    return 1;
  }
  std::printf("Software decoding contracts passed\n");
  return 0;
}
