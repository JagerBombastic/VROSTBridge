#pragma once
#include "core/stream.hpp"
#include <cstdio>
#include <mutex>
#include <stdexcept>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#endif
namespace vrost {
// Local calibrated-provider interface. Sensor processing remains
// provider-owned. The companion sends "VRG1 <stamp> <valid> <x> <y> <z> <w>"
// datagrams; the stamp is its time.monotonic_ns(), the clock of monotonicNs().
// Linux: an AF_UNIX datagram socket at a path (--gaze-socket). Windows has no
// AF_UNIX datagrams: a UDP socket bound to 127.0.0.1 (--gaze-udp PORT, which
// also works on Linux). Only this machine can reach either endpoint.
class GazeReceiver {
#ifdef _WIN32
  SOCKET fd_ = INVALID_SOCKET;
  bool winsock_ = false;
#else
  int fd_ = -1;
#endif
  std::string path_;
  uint64_t timestamp_ = 0;
  VrostQuat latest_{};
  bool valid_ = false;
  // A copy of the latest valid sample for the eye-tracking panel (read on the render thread).
  mutable std::mutex shownMutex_;
  VrostQuat shown_{};
  uint64_t shownAt_ = 0;
  bool listening() const {
#ifdef _WIN32
    return fd_ != INVALID_SOCKET;
#else
    return fd_ >= 0;
#endif
  }

public:
#ifndef _WIN32
  void open(const std::string &path) {
    if (path.empty())
      return;
    if (path.size() >= sizeof(sockaddr_un::sun_path))
      throw std::runtime_error("Gaze socket path too long");
    fd_ = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd_ < 0)
      throw std::runtime_error("Cannot create gaze socket");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    // Never unlink an existing endpoint belonging to another running client.
    if (bind(fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0)
      throw std::runtime_error("Gaze socket already exists or is inaccessible");
    path_ = path;
  }
#endif
  // UDP on the loopback interface only; port 0 leaves gaze input off.
  void openUdp(uint16_t port) {
    if (!port || listening())
      return;
#ifdef _WIN32
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0)
      throw std::runtime_error("Cannot start Windows sockets for gaze input");
    winsock_ = true;
    fd_ = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd_ == INVALID_SOCKET)
      throw std::runtime_error("Cannot create gaze socket");
    u_long nonBlocking = 1;
    // Exclusive use: another program cannot bind the same port and read or inject the samples.
    BOOL exclusive = TRUE;
    if (ioctlsocket(fd_, FIONBIO, &nonBlocking) != 0 ||
        setsockopt(fd_, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char *>(&exclusive),
                   sizeof(exclusive)) != 0)
      throw std::runtime_error("Cannot configure gaze socket");
#else
    fd_ = ::socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd_ < 0)
      throw std::runtime_error("Cannot create gaze socket");
#endif
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (::bind(fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0)
      throw std::runtime_error("Gaze UDP port " + std::to_string(port) + " is in use or inaccessible");
  }
  const VrostQuat *sample(uint64_t now) {
    if (!listening())
      return nullptr;
    char packet[256];
    for (int i = 0; i < 32; ++i) {
#ifdef _WIN32
      const int n = ::recv(fd_, packet, int(sizeof(packet) - 1), 0);
#else
      auto n = recv(fd_, packet, sizeof(packet) - 1, MSG_DONTWAIT);
#endif
      if (n < 0)
        break;
      packet[n] = 0;
      unsigned long long stamp;
      int valid;
      VrostQuat q{};
      char tail;
      if (std::sscanf(packet, "VRG1 %llu %d %f %f %f %f %c", &stamp, &valid,
                      &q.x, &q.y, &q.z, &q.w, &tail) != 6) {
        valid_ = false;
        continue;
      }
      VrostPose pose{q, {0, 0, 0}};
      if (stamp <= timestamp_ || stamp > now || now - stamp >= 50000000ULL ||
          !finitePose(pose) || valid != 1) {
        valid_ = false;
        continue;
      }
      timestamp_ = stamp;
      latest_ = q;
      valid_ = true;
      std::lock_guard<std::mutex> lock(shownMutex_);
      shown_ = q;
      shownAt_ = stamp;
    }
    return valid_ && now >= timestamp_ && now - timestamp_ < 50000000ULL
               ? &latest_
               : nullptr;
  }
  // The latest valid sample if it is under 100 ms old (local display only; never logged).
  bool shown(VrostQuat &q, uint64_t now, uint64_t &ageMs) const {
    std::lock_guard<std::mutex> lock(shownMutex_);
    if (!shownAt_ || now < shownAt_ || now - shownAt_ >= 100000000ULL) return false;
    q = shown_;
    ageMs = (now - shownAt_) / 1000000ULL;
    return true;
  }
  ~GazeReceiver() {
#ifdef _WIN32
    if (fd_ != INVALID_SOCKET)
      closesocket(fd_);
    if (winsock_)
      WSACleanup();
#else
    if (fd_ >= 0)
      close(fd_);
    if (!path_.empty())
      unlink(path_.c_str());
#endif
  }
};
} // namespace vrost
