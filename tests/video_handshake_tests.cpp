#include "client/video_handshake.hpp"
#include <iostream>
#include <stdexcept>
#include <string>

void check(bool value) { if (!value) throw std::runtime_error("video handshake regression"); }
struct Decoder { bool ready = false; uint64_t packets = 0; };
int route(void *context, uint64_t, const uint8_t *, uint64_t) {
  auto &decoder = *static_cast<Decoder *>(context);
  if (!decoder.ready) return 0;
  ++decoder.packets;
  return 1;
}
// Protocol fixture mirrors the pinned server: no unsolicited codec config;
// config is returned on RequestIdr if available. Video waits for a keyframe.
struct Server {
  bool configAvailable = false, configEvent = false, keyframe = false;
  uint64_t requests = 0;
  void *context = nullptr;
  VrostVideoCallback callback = nullptr;
  void deliver() {
    if (keyframe && callback) {
      const uint8_t sample = 1;
      callback(context, 0, &sample, 1);
      keyframe = false;
    }
  }
};
extern "C" void vrost_decoder(void *transport, void *context, VrostVideoCallback callback) {
  auto &server = *static_cast<Server *>(transport);
  server.context = context; server.callback = callback;
  ++server.requests;
  if (server.configAvailable) server.configEvent = server.keyframe = true;
}
int main() {
  constexpr uint64_t second = 1000000000ULL;
  Server server;
  Decoder decoder;
  vrost::VideoHandshake handshake(&server, &decoder, route);
  check(!handshake.update(second, 0) && server.requests == 0);
  handshake.start(second);
  check(server.requests == 1 && !server.configEvent);
  check(std::string(handshake.phase()) == "waiting_config");
  // First request precedes encoder initialization. No video can drive ALVR's
  // packet-loss recovery; the paced control request must recover this case.
  for (uint64_t t = second; t < 2 * second; t += second / 90)
    check(!handshake.update(t, 0));
  server.configAvailable = true;
  check(handshake.update(2 * second, 0));
  check(server.requests == 2 && server.configEvent);
  server.deliver(); // Early IDR cannot be queued into an unconfigured decoder.
  check(decoder.packets == 0);
  decoder.ready = true;
  handshake.configured(2 * second);
  check(server.requests == 3);
  handshake.configured(2 * second); // Duplicate config events are harmless.
  check(server.requests == 3);
  server.deliver();
  check(decoder.packets == 1 && !handshake.update(2 * second, decoder.packets));
  check(std::string(handshake.phase()) == "receiving");
  // Healthy video does not request periodic keyframes or alter bitrate.
  for (uint64_t i = 1; i <= 900; ++i)
    check(!handshake.update(2 * second + i * second / 90, ++decoder.packets));
  check(server.requests == 3);
  check(!handshake.update(13 * second - 1, decoder.packets));
  check(handshake.update(13 * second, decoder.packets));
  check(!handshake.update(13 * second + 1, decoder.packets));
  server.deliver();
  check(!handshake.update(13 * second + 2, decoder.packets));
  // A stopped session never requests recovery and reconnect starts afresh.
  handshake.stop();
  check(!handshake.update(20 * second, 0));
  check(std::string(handshake.phase()) == "idle");
  decoder.ready = false; decoder.packets = 0; server.configEvent = false;
  handshake.start(21 * second);
  check(server.configEvent && handshake.requests() == 1);
  server.deliver(); check(decoder.packets == 0);
  decoder.ready = true; handshake.configured(21 * second);
  server.deliver(); check(decoder.packets == 1);
  check(!handshake.update(21 * second, decoder.packets));
  std::cout << "Request-before-config, late encoder, early IDR rejection, decoder-ready "
               "IDR, silent-video recovery, healthy streaming and reconnect passed\n";
}
