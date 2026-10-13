#include "client/platform/linux/video.hpp"
namespace vrost {
#ifdef _WIN32
// 1.0.0.7: D3D11VA surfaces on SteamVR's GPU. When the driver cannot decode the stream (its codec,
// profile or size), FFmpeg asks again without D3D11: that refusal is remembered for the error.
struct HardwareFormatChoice {
  bool offered = false, refused = false;
};
static AVPixelFormat hardwareFormat(AVCodecContext *codec, const AVPixelFormat *formats) {
  auto *choice = static_cast<HardwareFormatChoice *>(codec->opaque);
  for (auto p = formats; *p != AV_PIX_FMT_NONE; ++p)
    if (*p == AV_PIX_FMT_D3D11) {
      if (choice) choice->offered = true;
      return *p;
    }
  if (choice && choice->offered) choice->refused = true;
  return AV_PIX_FMT_NONE;
}
#else
static AVPixelFormat hardwareFormat(AVCodecContext *,
                                    const AVPixelFormat *formats) {
  for (auto p = formats; *p != AV_PIX_FMT_NONE; ++p)
    if (*p == AV_PIX_FMT_VAAPI)
      return *p;
  return AV_PIX_FMT_NONE;
}
#endif
void Decoder::worker() {
  AVCodecContext *codec = nullptr;
  AVBufferRef *device = nullptr;
  AVFrame *frame = av_frame_alloc();
  AVPacket *packet = av_packet_alloc();
#ifdef _WIN32
  HardwareFormatChoice hardwareChoice;
  const bool hardware = !compatibility_ && !gpu_.cpuDecode;
#endif
  try {
    if (width_ == 0 || height_ == 0 || width_ > 8192 || height_ > 8192 ||
        codec_ > 2 || decodedWidth() == 0 || decodedHeight() == 0 ||
        decodedWidth() > 16384 || decodedHeight() > 8192)
      throw std::runtime_error(
          "Unsupported negotiated video dimensions or codec");
    const AVCodecID codecId = codec_ == 0 ? AV_CODEC_ID_H264 : codec_ == 1 ? AV_CODEC_ID_HEVC : AV_CODEC_ID_AV1;
    auto implementation = avcodec_find_decoder(codecId);
    if (!implementation)
      throw std::runtime_error(codec_ == 2 ? "This client's FFmpeg has no AV1 decoder; choose HEVC"
                                           : "FFmpeg codec not installed");
    codec = avcodec_alloc_context3(implementation);
    if (!codec || !frame || !packet)
      throw std::bad_alloc();
    codec->thread_count = 1;
#ifdef _WIN32
    // Windows CPU decoding (the fallback for a GPU without a usable D3D11 video decoder) may use one
    // thread per slice. Frame threads stay off with AV_CODEC_FLAG_LOW_DELAY: no frames of delay.
    if (!hardware) codec->thread_count = 0;
#endif
    codec->flags |= AV_CODEC_FLAG_LOW_DELAY;
    codec->pkt_timebase = {1, 1000000000};
    if (config_.size() > 1024 * 1024)
      throw std::runtime_error("Codec configuration too large");
    codec->extradata = static_cast<uint8_t *>(
        av_mallocz(config_.size() + AV_INPUT_BUFFER_PADDING_SIZE));
    if (!codec->extradata)
      throw std::bad_alloc();
    std::memcpy(codec->extradata, config_.data(), config_.size());
    codec->extradata_size = config_.size();
#ifdef _WIN32
    if (hardware) {
      device = gpu_.createDecodeDevice(); // D3D11VA on SteamVR's GPU, or a clear error
      codec->hw_device_ctx = av_buffer_ref(device);
      codec->opaque = &hardwareChoice;
      codec->get_format = hardwareFormat;
    }
#else
    if (!compatibility_) {
      if (gpu_.renderNode.empty())
        throw std::runtime_error(
            "Cannot identify SteamVR GPU render node for VA-API");
      avcheck(av_hwdevice_ctx_create(&device, AV_HWDEVICE_TYPE_VAAPI,
                                     gpu_.renderNode.c_str(), nullptr, 0),
              "VA-API device");
      codec->hw_device_ctx = av_buffer_ref(device);
      codec->get_format = hardwareFormat;
    }
#endif
    avcheck(avcodec_open2(codec, implementation, nullptr),
            "Open video decoder");
#ifdef _WIN32
    diagnosticLine(std::string("STATUS Video decoder opened successfully: backend=") +
                   (hardware ? "D3D11VA" : "software") +
                   " codec=" + implementation->name +
                   " adapter=" + (hardware ? gpu_.adapter.name : std::string("none")) +
#else
    diagnosticLine(std::string("STATUS Video decoder opened successfully: backend=") +
                   (compatibility_ ? "software" : "VAAPI") +
                   " codec=" + implementation->name +
                   " render_node=" + (compatibility_ ? "none" : gpu_.renderNode) +
#endif
                   " decoded=" + std::to_string(decodedWidth()) + "x" + std::to_string(decodedHeight()) +
                   " output=" + std::to_string(width_ * 2) + "x" + std::to_string(height_) +
                   " foveated=" + (foveated() ? "1" : "0") +
                   " scheduling=overlapped_decode_conversion decoded_mailbox=2");
    struct PacketTiming { uint64_t timestamp, received, started; };
    std::deque<PacketTiming> packetTimings;
    auto receive = [&] {
      while (!stopping_) {
        int r = avcodec_receive_frame(codec, frame);
        if (r == AVERROR(EAGAIN) || r == AVERROR_EOF)
          return;
        avcheck(r, "Decode frame");
        if (decodeOutputs.fetch_add(1) == 0)
          diagnosticLine("STATUS First video frame decoded");
        if (frame->width != int(decodedWidth()) || frame->height != int(decodedHeight()))
          throw std::runtime_error(
              "Decoded stereo layout does not match negotiated dimensions");
        if (frame->pts == AV_NOPTS_VALUE || frame->pts < 0) {
          ++dropped;
          av_frame_unref(frame);
          continue;
        }
        uint64_t timestamp = frame->pts;
        const auto decodeOutputAt=monotonicNs();
        PacketTiming timing{};
        auto recorded=std::find_if(packetTimings.begin(),packetTimings.end(),
            [&](const auto &value) { return value.timestamp==timestamp; });
        if (recorded!=packetTimings.end()) {
          timing=*recorded;
          packetTimings.erase(recorded);
          std::lock_guard<std::mutex> lock(statisticsMutex_);
          decodeTiming_.add(decodeOutputAt-timing.started);
        } else ++missingTiming;
        // receive_frame does not wait for the VA-API GPU. Transfer an AVFrame
        // reference, not its raw surface ID, so the decoder pool cannot reuse
        // memory until conversion has finished reading it. Keep feeding the
        // decoder while another thread waits for hardware completion.
        AVFrame *owned = av_frame_alloc();
        if (!owned) throw std::bad_alloc();
        av_frame_move_ref(owned, frame);
        DecodedFrame output{
            std::shared_ptr<AVFrame>(owned, [](AVFrame *value) { av_frame_free(&value); }),
            timestamp, timing.received, timing.started, decodeOutputAt};
        decodedFrames_.push(std::move(output));
        av_frame_unref(frame);
      }
    };
    Encoded encoded;
    while (queue_.pop(encoded)) {
      const auto decodeStart=monotonicNs();
      {
        std::lock_guard<std::mutex> lock(statisticsMutex_);
        queueTiming_.add(decodeStart-encoded.receivedAt);
      }
      if (encoded.timestamp > uint64_t(INT64_MAX))
        throw std::runtime_error("Invalid frame timestamp");
      avcheck(av_new_packet(packet, encoded.bytes.size()),
              "Allocate video packet");
      std::memcpy(packet->data, encoded.bytes.data(), encoded.bytes.size());
      packet->pts = packet->dts = encoded.timestamp;
      // Correlate delayed decoder output without changing compressed packet
      // ordering. This metadata is bounded independently of decoder surfaces.
      if (packetTimings.size()==64) packetTimings.pop_front();
      packetTimings.push_back({encoded.timestamp,encoded.receivedAt,decodeStart});
      int r = avcodec_send_packet(codec, packet);
      if (r == AVERROR(EAGAIN)) {
        receive();
        r = avcodec_send_packet(codec, packet);
      }
      av_packet_unref(packet);
      avcheck(r, "Submit encoded packet");
      receive();
    }
  } catch (const std::exception &e) {
#ifdef _WIN32
    // Name the decoder that failed; there is no silent fallback (the companion offers CPU decoding).
    std::string message = e.what();
    if (hardware && message.rfind("D3D11VA", 0) != 0)
      message = "D3D11VA hardware decoding on " + gpu_.adapter.name + " failed" +
                (hardwareChoice.refused ? " (the GPU's video decoder does not accept this stream)" : "") + ": " +
                message + ". Choose CPU decoding in VROSTBridge's settings";
    else if (!hardware)
      message = "CPU video decoding failed: " + message;
    fail(message);
#else
    fail(e.what());
#endif
  }
  av_packet_free(&packet);
  av_frame_free(&frame);
  avcodec_free_context(&codec);
  av_buffer_unref(&device);
}
// 1.0.0.11: the output size for the next frame: the shown size at the companion's scale. Only the
// conversion worker (and start(), before it runs) writes target_; each surface keeps the size it got.
void Decoder::applyScale() {
  const EyeSize eye = scaledEye(baseShown_, scalePercent_.load());
  target_.width = eye.width * 2;
  target_.height = eye.height;
}
void Decoder::conversionWorker() {
  try {
    DecodedFrame output;
    while (decodedFrames_.popLatest(output)) {
      if (stopping_) break;
      if (!compatibility_) applyScale();
      const auto conversionStart = monotonicNs();
      {
        std::lock_guard<std::mutex> lock(statisticsMutex_);
        decodedQueueTiming_.add(conversionStart - output.output);
      }
      auto surface = gpu_.convert(output.frame.get(), output.timestamp, Pose{},
          compatibility_, [&](FrameMetadata &metadata) {
            // Hardware decoding is complete only after the VA-API map has
            // synchronized. Correct ALVR's stage boundary before reporting
            // compositor start, which also supplies this frame's source pose
            // and the foveation centres the host used for this frame.
            const auto completedAt = monotonicNs();
            if (output.started) {
              std::lock_guard<std::mutex> lock(statisticsMutex_);
              decodeCompleteTiming_.add(completedAt - output.started);
            }
            if (stopping_) return false;
            vrost_decoded(transport_, output.timestamp);
            VrostView views[2]{};
            const int found = vrost_frame_views2(transport_, output.timestamp, views, metadata.shifts);
            metadata.hasShifts = (found & 2) != 0;
            // Frames without centres reuse the previous ones (as the ALVR client does).
            if (metadata.hasShifts && target_.foveation.enabled)
              for (int i = 0; i < 4; ++i) target_.foveation.shifts[i / 2][i % 2] = metadata.shifts[i];
            return (found & 1) && geometry_.sourcePose(views, metadata.source);
          }, compatibility_ ? nullptr : &target_);
      output.frame.reset();
      {
        std::lock_guard<std::mutex> lock(statisticsMutex_);
        conversionTiming_.add(monotonicNs() - conversionStart);
        if (surface) {
          mapTiming_.add(surface->mapWaitNs);
          conversionCpuTiming_.add(surface->conversionCpuNs);
        }
      }
      if (surface) {
        surface->receivedAt = output.received;
        surface->decodeStartAt = output.started;
        surface->decodeOutputAt = output.output;
        if (surface->tenBit) ++tenBitFrames;
        if (foveated()) ++foveatedFrames;
        if (surface->gazeCentred) ++gazeCentredFrames;
        {
          std::lock_guard<std::mutex> lock(latestMutex_);
          // Stop/reconfiguration joins both workers before replacing Decoder.
          // Never publish an old-session frame once teardown has begun.
          if (stopping_) break;
          pending_.push(std::move(surface));
        }
        frameAvailable_.notify_one();
        ++decoded;
      } else if (!stopping_) ++dropped;
    }
  } catch (const std::exception &e) {
    fail(e.what());
  }
}
} // namespace vrost
