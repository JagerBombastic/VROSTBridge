// The companion -> client gaze input (client/device/gaze.hpp): "VRG1 <stamp> <valid> <x> <y> <z> <w>"
// datagrams with the companion's time.monotonic_ns() stamps. 1.0.0.7: the loopback UDP endpoint
// (--gaze-udp, the Windows client's; Linux too) and, on Linux, the AF_UNIX one (--gaze-socket) accept
// fresh valid samples only, in order, and the UDP port is the client's alone.
#include "client/device/gaze.hpp"
#include <chrono>
#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <thread>
#ifndef _WIN32
#include <filesystem>
#endif

namespace {
int failures = 0;
#define CHECK(cond, what)                                                                          \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL line %d: %s (%s)\n", __LINE__, #cond, what);                               \
    }                                                                                              \
  } while (0)

#ifdef _WIN32
using Socket = SOCKET;
const Socket noSocket = INVALID_SOCKET;
void closeSocket(Socket s) { closesocket(s); }
#else
using Socket = int;
const Socket noSocket = -1;
void closeSocket(Socket s) { close(s); }
#endif

struct Sender {
  Socket fd = noSocket;
  sockaddr_in to{};
  explicit Sender(uint16_t port) {
    fd = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    to.sin_family = AF_INET;
    to.sin_port = htons(port);
    to.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  }
  void send(const std::string &text) const {
    ::sendto(fd, text.data(), int(text.size()), 0, reinterpret_cast<const sockaddr *>(&to), sizeof(to));
  }
  ~Sender() {
    if (fd != noSocket) closeSocket(fd);
  }
};

std::string packet(uint64_t stamp, int valid, const char *quaternion = "0 0 0 1") {
  return "VRG1 " + std::to_string(stamp) + " " + std::to_string(valid) + " " + quaternion;
}

// Datagrams on the loopback interface arrive at once, but give the stack a moment.
const VrostQuat *sampleSoon(vrost::GazeReceiver &receiver) {
  for (int i = 0; i < 200; ++i) {
    if (const VrostQuat *q = receiver.sample(vrost::monotonicNs())) return q;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  return nullptr;
}

// A stamp newer than any sent before (the clock moves on by at least a millisecond).
uint64_t fresh() {
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
  return vrost::monotonicNs() - 1000;
}

template <class Send> void exercise(vrost::GazeReceiver &receiver, Send send, const char *endpoint) {
  std::printf("%s\n", endpoint);
  send(packet(fresh(), 1, "0 0.70710678 0 0.70710678"));
  const VrostQuat *q = sampleSoon(receiver);
  CHECK(q && std::fabs(q->y - .70710678f) < 1e-6f && std::fabs(q->w - .70710678f) < 1e-6f, endpoint);
  VrostQuat shown{};
  uint64_t age = 99;
  CHECK(receiver.shown(shown, vrost::monotonicNs(), age) && age < 100, "the eye-tracking panel sees it");
  // Each of these is refused and leaves no valid sample (made when sent: newer than the last good one).
  const std::function<std::string()> refused[] = {
      [] { return packet(fresh(), 0); },                                // the provider says invalid
      [] {                                                              // older than 50 ms
        std::this_thread::sleep_for(std::chrono::milliseconds(60));
        return packet(vrost::monotonicNs() - 55000000, 1);
      },
      [] { return packet(fresh() + 1000000000, 1); },                   // from the future
      [] { return packet(fresh(), 1, "0 0 0 2"); },                     // not a unit quaternion
      [] { return packet(fresh(), 1, "0 0 0 1 9"); },                   // trailing data
      [] { return "VRG2 " + std::to_string(fresh()) + " 1 0 0 0 1"; }, // another format
  };
  for (const auto &make : refused) {
    send(packet(fresh(), 1)); // a good sample first...
    CHECK(sampleSoon(receiver) != nullptr, "a fresh valid sample is accepted");
    const std::string text = make();
    send(text); // ...then the bad one invalidates it
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    CHECK(receiver.sample(vrost::monotonicNs()) == nullptr, text.c_str());
  }
  // Out of order: a stamp not newer than the last accepted one is refused.
  const uint64_t older = fresh();
  send(packet(fresh(), 1));
  CHECK(sampleSoon(receiver) != nullptr, "newest sample");
  send(packet(older, 1));
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  CHECK(receiver.sample(vrost::monotonicNs()) == nullptr, "an older stamp after a newer one");
  // And a sample grows stale on its own after 50 ms.
  send(packet(fresh(), 1));
  CHECK(sampleSoon(receiver) != nullptr, "fresh again");
  std::this_thread::sleep_for(std::chrono::milliseconds(70));
  CHECK(receiver.sample(vrost::monotonicNs()) == nullptr, "stale after 50 ms");
}
} // namespace

int main() {
  try {
    // Off unless a port is given.
    {
      vrost::GazeReceiver off;
      off.openUdp(0);
      CHECK(off.sample(vrost::monotonicNs()) == nullptr, "no endpoint, no gaze");
    }
    // A free port: the first of these that binds.
    uint16_t port = 0;
    vrost::GazeReceiver receiver;
    for (uint16_t candidate = 47811; candidate < 47851 && !port; ++candidate) {
      try {
        receiver.openUdp(candidate);
        port = candidate;
      } catch (const std::runtime_error &) {
      }
    }
    CHECK(port != 0, "a loopback UDP port");
    if (port) {
      {
        Sender sender(port);
        exercise(receiver, [&](const std::string &text) { sender.send(text); }, "UDP 127.0.0.1");
      }
      // The port is the client's alone: a second endpoint cannot take it.
      vrost::GazeReceiver second;
      bool refused = false;
      try {
        second.openUdp(port);
      } catch (const std::runtime_error &) {
        refused = true;
      }
      CHECK(refused, "a second endpoint on the same port");
    }
#ifndef _WIN32
    const auto path = std::filesystem::temp_directory_path() / ("vrost-gaze-test-" + std::to_string(getpid()));
    {
      vrost::GazeReceiver local;
      local.open(path.string());
      const int fd = ::socket(AF_UNIX, SOCK_DGRAM, 0);
      sockaddr_un to{};
      to.sun_family = AF_UNIX;
      std::snprintf(to.sun_path, sizeof(to.sun_path), "%s", path.c_str());
      exercise(local, [&](const std::string &text) {
        ::sendto(fd, text.data(), text.size(), 0, reinterpret_cast<const sockaddr *>(&to), sizeof(to));
      }, "AF_UNIX datagram socket");
      close(fd);
    }
    CHECK(!std::filesystem::exists(path), "the socket file is removed with the receiver");
#endif
  } catch (const std::exception &e) {
    std::printf("FAIL: %s\n", e.what());
    return 1;
  }
  if (failures) {
    std::printf("%d gaze input check(s) failed\n", failures);
    return 1;
  }
  std::printf("Gaze input contracts passed\n");
  return 0;
}
