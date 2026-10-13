#pragma once
#include "client/transport/bridge.h"
#include <cstdint>

namespace vrost {
// The pinned ALVR server sends codec configuration in reply to RequestIdr.
// vrost_decoder both installs our lifetime-stable router and sends that request.
// Start with an empty router: it rejects early video until the decoder exists.
// Waiting for DecoderConfig before registering the router deadlocks startup.
class VideoHandshake {
  void *transport_, *context_;
  VrostVideoCallback callback_;
  bool active_ = false, configured_ = false;
  uint64_t progressAt_ = 0, requestedAt_ = 0, packets_ = 0, requests_ = 0;
  void request(uint64_t now) {
    vrost_decoder(transport_, context_, callback_);
    requestedAt_ = now;
    ++requests_;
  }
public:
  static constexpr uint64_t retryNs = 1000000000ULL;
  VideoHandshake(void *transport, void *context, VrostVideoCallback callback)
      : transport_(transport), context_(context), callback_(callback) {}
  void stop() { active_ = configured_ = false; packets_ = requests_ = 0; }
  void start(uint64_t now) {
    stop();
    active_ = true;
    progressAt_ = now;
    request(now);
  }
  void configured(uint64_t now) {
    if (!active_ || configured_) return;
    configured_ = true;
    progressAt_ = now;
    // The initial IDR may have arrived before the decoder was ready. Request
    // another only after the router points at the new decoder's input queue.
    request(now);
  }
  bool update(uint64_t now, uint64_t packets) {
    if (!active_) return false;
    if (packets != packets_) { packets_ = packets; progressAt_ = now; }
    // No periodic IDRs during healthy streaming. Retry on silence because
    // ALVR's packet-driven recovery cannot recover a server sending no video.
    if (now - progressAt_ < retryNs || now - requestedAt_ < retryNs) return false;
    request(now);
    return true;
  }
  const char *phase() const {
    return !active_ ? "idle" : !configured_ ? "waiting_config" :
           packets_ == 0 ? "waiting_video" : "receiving";
  }
  uint64_t requests() const { return requests_; }
  uint64_t silenceMs(uint64_t now) const { return active_ ? (now - progressAt_) / 1000000 : 0; }
};
} // namespace vrost
