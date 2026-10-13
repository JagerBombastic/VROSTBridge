#pragma once
#include "client/transport/bridge.h"
#include "core/contracts.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#ifdef _WIN32
// The two kernel32 functions the log writer needs, declared exactly as <windows.h> declares them so
// that this header does not bring that header's macros (near, far, min, max...) into every program.
struct _OVERLAPPED;
extern "C" __declspec(dllimport) void *__stdcall GetStdHandle(unsigned long);
extern "C" __declspec(dllimport) int __stdcall WriteFile(void *, const void *, unsigned long, unsigned long *,
                                                         struct _OVERLAPPED *);
#else
#include <unistd.h>
#endif
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
namespace vrost {
// Input focus intentionally is not a video-validity condition. A local
// overlay must not replace a usable remote image. Gaps in the video keep showing
// the last frame, re-aimed to the current head pose; only a gap longer than
// remoteHoldNs enters the neutral recovery view.
// 1.0.0.4: the hold was 250 ms, so every stall a little longer than that (a lost
// frame waiting for its keyframe on a busy PC, a Wi-Fi hiccup) flashed the grey
// neutral scene: about 11 s of grey in a 1.0.0.3 Beat Saber session. A re-aimed
// last frame is far less disturbing for up to a second; the "Waiting for video"
// card still appears after 500 ms.
constexpr uint64_t remoteFreshNs = 100000000ULL, remoteHoldNs = 1000000000ULL;
struct RemoteFrameState { bool fresh, hold; };
inline RemoteFrameState remoteFrameState(bool tracked, bool streaming,
                                        bool available, uint64_t now, uint64_t source) {
  if (!tracked || !streaming || !available || now < source) return {false, false};
  return {now-source < remoteFreshNs, now-source < remoteHoldNs};
}
// On Windows, MSVC's steady_clock is QueryPerformanceCounter scaled to nanoseconds exactly as
// Python 3.13's time.monotonic_ns() (the companion's gaze stamps, client/device/gaze.hpp) is, so
// both sides share one clock there as CLOCK_MONOTONIC does on Linux.
inline uint64_t monotonicNs() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}
// The render and tracking threads must never block on a pipe. Diagnostics are
// handed to a bounded queue drained by one writer thread. If the companion
// stops reading, high-rate telemetry is dropped and counted; lifecycle and
// error lines are kept within a separate hard bound. Lines stay ordered.
class DiagnosticLog {
  std::mutex mutex_;
  std::condition_variable wake_, drained_;
  std::deque<std::string> queue_;
  size_t bytes_ = 0;
  uint64_t dropped_ = 0, generation_ = 0, written_ = 0;
  bool stopping_ = false, writing_ = false;
  std::thread writer_;
  bool autoStart_ = true;
  // RC4.3: whole lines only, in writes of at most 4096 bytes, which a pipe never splits. The
  // companion reads this output and other writers (FFmpeg's messages on stderr, which share the
  // pipe) can then only come between lines, never inside one: a line split in two would let the
  // tail of a FOVEA line (where the user looks) reach the persisted log as a line of its own.
  static void writeAll(const std::string &text) {
    const char *at = text.data();
    size_t left = text.size();
#ifdef _WIN32
    // The same bytes through the stdout handle itself (a pipe from the companion): one WriteFile per
    // chunk, never the C runtime's text-mode translation, which would add CRs and split the writes.
    void *const out = GetStdHandle(static_cast<unsigned long>(-11)); // STD_OUTPUT_HANDLE
    if (out == nullptr || out == reinterpret_cast<void *>(intptr_t(-1))) return; // INVALID_HANDLE_VALUE
    while (left) {
      unsigned long done = 0;
      if (!WriteFile(out, at, static_cast<unsigned long>(std::min<size_t>(left, 1u << 30)), &done, nullptr) || done == 0)
        return; // the companion stopped reading; nothing else to do
      at += done;
      left -= size_t(done);
    }
#else
    while (left) {
      const ssize_t done = ::write(STDOUT_FILENO, at, left);
      if (done < 0) {
        if (errno == EINTR) continue;
        return; // the companion stopped reading; nothing else to do
      }
      at += done;
      left -= size_t(done);
    }
#endif
  }
  static void writeLines(const std::deque<std::string> &batch, uint64_t dropped) {
    std::cout.flush(); // anything written through std::cout before (startup, --help) goes first
    std::fflush(stdout);
    std::string chunk;
    auto add = [&](const std::string &line) {
      if (!chunk.empty() && chunk.size() + line.size() + 1 > 4096) {
        writeAll(chunk);
        chunk.clear();
      }
      chunk += line;
      chunk += '\n';
    };
    if (dropped) add("DEBUG diagnostic_lines_dropped=" + std::to_string(dropped));
    for (const auto &line : batch) add(line);
    if (!chunk.empty()) writeAll(chunk);
  }
  void run() {
    std::unique_lock<std::mutex> lock(mutex_);
    while (true) {
      wake_.wait(lock, [&] { return stopping_ || !queue_.empty(); });
      if (queue_.empty() && stopping_) break;
      std::deque<std::string> batch;
      batch.swap(queue_);
      bytes_ = 0;
      const auto dropped = dropped_;
      dropped_ = 0;
      const auto target = generation_;
      writing_ = true;
      lock.unlock();
      writeLines(batch, dropped);
      lock.lock();
      writing_ = false;
      written_ = target;
      drained_.notify_all();
    }
  }
public:
  static constexpr size_t maxBytes = 256 * 1024, maxLines = 2048, maxEssential = 8192;
  DiagnosticLog() = default;
  // Tests construct a paused log to exercise the bounded queue deterministically.
  explicit DiagnosticLog(bool autoStart) : autoStart_(autoStart) {}
  void start() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!writer_.joinable() && !stopping_) writer_ = std::thread([this] { run(); });
  }
  static bool essential(const std::string &line) {
    for (const char *prefix : {"FATAL", "ERROR", "STATUS", "VIDEO", "VIEW_GEOMETRY",
                               "CONTROLLER_STATE", "PRESENTATION_CADENCE"})
      if (line.rfind(prefix, 0) == 0) return true;
    return false;
  }
  // Returns false when the line was dropped. Exposed for regression tests.
  bool push(std::string line) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_) return false;
    const bool full = queue_.size() >= maxLines || bytes_ + line.size() > maxBytes;
    if ((full && !essential(line)) || queue_.size() >= maxEssential) {
      ++dropped_;
      return false;
    }
    if (autoStart_ && !writer_.joinable()) writer_ = std::thread([this] { run(); });
    bytes_ += line.size();
    queue_.push_back(std::move(line));
    ++generation_;
    wake_.notify_one();
    return true;
  }
  // Waits until everything queued so far is written (bounded by the caller).
  void flush(std::chrono::milliseconds limit = std::chrono::milliseconds(2000)) {
    std::unique_lock<std::mutex> lock(mutex_);
    const auto target = generation_;
    drained_.wait_for(lock, limit, [&] { return written_ >= target && !writing_; });
  }
  uint64_t droppedPending() {
    std::lock_guard<std::mutex> lock(mutex_);
    return dropped_;
  }
  ~DiagnosticLog() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stopping_ = true;
    }
    wake_.notify_all();
    if (writer_.joinable()) writer_.join();
  }
};
inline DiagnosticLog &diagnosticLog() {
  static DiagnosticLog log;
  return log;
}
inline void diagnosticLine(const std::string &line) { diagnosticLog().push(line); }
inline void diagnosticFlush() { diagnosticLog().flush(); }
// The compositor selects a completed conversion, never just the last submitted
// conversion. A newer unfinished surface must not hide an older ready frame.
// Only decoded images are superseded; compressed interframes remain in order.
template <class Frame, size_t Capacity = 4> class ReadyFrames {
  std::deque<Frame> frames_;
public:
  static_assert(Capacity > 0);
  uint64_t superseded = 0;
  void push(Frame frame) {
    if (frames_.size() == Capacity) {
      frames_.pop_front();
      ++superseded;
    }
    frames_.push_back(std::move(frame));
  }
  template <class Ready> Frame takeLatest(uint64_t after, Ready ready) {
    Frame selected{};
    uint64_t newest = after;
    for (auto &frame : frames_)
      if (frame->timestamp > newest && ready(frame)) {
        selected = frame;
        newest = frame->timestamp;
      }
    for (auto it = frames_.begin(); it != frames_.end();) {
      if ((*it)->timestamp <= newest) {
        if (*it != selected) ++superseded;
        it = frames_.erase(it);
      } else ++it;
    }
    return selected;
  }
  size_t size() const { return frames_.size(); }
  Frame firstAfter(uint64_t after) const {
    for (const auto &frame : frames_)
      if (frame->timestamp > after) return frame;
    return {};
  }
  void clear() { frames_.clear(); }
};
// Hardware decoding may return a surface before its GPU work completes.
// Keep packet submission independent of the blocking map/import stage. Only
// decoded, independently owned surfaces may be replaced here: compressed
// reference packets must always use PacketQueue and remain in order.
template <class Frame, size_t Capacity = 2> class DecodedMailbox {
  static_assert(Capacity > 0);
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<Frame> frames_;
  bool stopped_ = false;
  size_t highWater_ = 0;
  uint64_t superseded_ = 0;
public:
  struct Statistics { size_t queued = 0, highWater = 0; uint64_t superseded = 0; };
  bool push(Frame frame) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopped_) return false;
    if (frames_.size() == Capacity) { frames_.pop_front(); ++superseded_; }
    frames_.push_back(std::move(frame));
    highWater_ = std::max(highWater_, frames_.size());
    cv_.notify_one();
    return true;
  }
  bool popLatest(Frame &frame) {
    std::unique_lock<std::mutex> lock(mutex_);
    cv_.wait(lock, [&] { return stopped_ || !frames_.empty(); });
    if (stopped_) return false;
    superseded_ += frames_.size() - 1;
    frame = std::move(frames_.back());
    frames_.clear();
    return true;
  }
  Statistics statistics() {
    std::lock_guard<std::mutex> lock(mutex_);
    Statistics result{frames_.size(), highWater_, superseded_};
    highWater_ = frames_.size();
    return result;
  }
  void stop() {
    std::lock_guard<std::mutex> lock(mutex_);
    stopped_ = true;
    frames_.clear();
    cv_.notify_all();
  }
};
struct TimingSummary {
  size_t samples = 0;
  double meanMs = 0, p50Ms = 0, p95Ms = 0, maxMs = 0;
};
// A bounded window keeps timing collection cheap even during long sessions.
// Percentiles describe recorded local stage intervals, not motion-to-photon.
class TimingWindow {
  std::deque<uint64_t> values_;
public:
  void add(uint64_t ns) {
    if (values_.size() == 512) values_.pop_front();
    values_.push_back(ns);
  }
  TimingSummary take() {
    TimingSummary out;
    out.samples = values_.size();
    if (values_.empty()) return out;
    std::vector<uint64_t> sorted(values_.begin(), values_.end());
    values_.clear();
    std::sort(sorted.begin(), sorted.end());
    for (auto value : sorted) out.meanMs += double(value) / 1e6;
    out.meanMs /= sorted.size();
    out.p50Ms = sorted[(sorted.size() * 50 + 99) / 100 - 1] / 1e6;
    out.p95Ms = sorted[(sorted.size() * 95 + 99) / 100 - 1] / 1e6;
    out.maxMs = sorted.back() / 1e6;
    return out;
  }
};
inline Pose matrix(VrostPose p) {
  const auto q = p.orientation;
  Pose m;
  const float x = q.x, y = q.y, z = q.z, w = q.w;
  m.m[0][0] = 1 - 2 * (y * y + z * z);
  m.m[0][1] = 2 * (x * y - z * w);
  m.m[0][2] = 2 * (x * z + y * w);
  m.m[1][0] = 2 * (x * y + z * w);
  m.m[1][1] = 1 - 2 * (x * x + z * z);
  m.m[1][2] = 2 * (y * z - x * w);
  m.m[2][0] = 2 * (x * z - y * w);
  m.m[2][1] = 2 * (y * z + x * w);
  m.m[2][2] = 1 - 2 * (x * x + y * y);
  for (int i = 0; i < 3; ++i)
    m.m[i][3] = p.position[i];
  return m;
}
inline VrostPose quaternion(const Pose &p) {
  VrostPose r{};
  float q[4]{};
  const float tr = p.m[0][0] + p.m[1][1] + p.m[2][2];
  if (tr > 0) {
    float s = std::sqrt(tr + 1) * 2;
    q[3] = s / 4;
    q[0] = (p.m[2][1] - p.m[1][2]) / s;
    q[1] = (p.m[0][2] - p.m[2][0]) / s;
    q[2] = (p.m[1][0] - p.m[0][1]) / s;
  } else {
    int i = p.m[1][1] > p.m[0][0] ? 1 : 0;
    if (p.m[2][2] > p.m[i][i])
      i = 2;
    int j = (i + 1) % 3, k = (i + 2) % 3;
    float s =
        std::sqrt(std::max(0.f, 1 + p.m[i][i] - p.m[j][j] - p.m[k][k])) * 2;
    if (s < 1e-6f)
      return r;
    q[i] = s / 4;
    q[j] = (p.m[j][i] + p.m[i][j]) / s;
    q[k] = (p.m[k][i] + p.m[i][k]) / s;
    q[3] = (p.m[k][j] - p.m[j][k]) / s;
  }
  r.orientation = {q[0], q[1], q[2], q[3]};
  for (int i = 0; i < 3; ++i)
    r.position[i] = p.m[i][3];
  return r;
}
inline bool finitePose(const VrostPose &p) {
  const float q[] = {p.orientation.x, p.orientation.y, p.orientation.z,
                     p.orientation.w, p.position[0],   p.position[1],
                     p.position[2]};
  for (float v : q)
    if (!std::isfinite(v))
      return false;
  float l = 0;
  for (int i = 0; i < 4; ++i)
    l += q[i] * q[i];
  return std::abs(l - 1) < .02f;
}
inline bool sourceHead(const VrostView *source, const VrostView *local, Pose &head,
                       float *residual = nullptr) {
  Pose candidates[2];
  float score = 0;
  for (int e = 0; e < 2; ++e) {
    if (!finitePose(source[e].pose))
      return false;
    const float a[] = {source[e].left, source[e].right, source[e].up,
                       source[e].down},
                b[] = {local[e].left, local[e].right, local[e].up,
                       local[e].down};
    for (int k = 0; k < 4; ++k) {
      if (!std::isfinite(a[k]) || std::abs(a[k] - b[k]) > .003f)
        return false;
      score += (a[k]-b[k])*(a[k]-b[k]);
    }
    candidates[e] =
        compose(matrix(source[e].pose), inverseRigid(matrix(local[e].pose)));
  }
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 4; ++j)
      if (std::abs(candidates[0].m[i][j] - candidates[1].m[i][j]) > .005f)
        return false;
      else {
        float delta = candidates[0].m[i][j] - candidates[1].m[i][j];
        score += delta * delta;
      }
  head = candidates[0];
  if (residual) *residual = score;
  return true;
}
struct Encoded {
  uint64_t timestamp = 0, epoch = 0, receivedAt = 0;
  std::vector<uint8_t> bytes;
};
class PacketQueue {
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<Encoded> q_;
  size_t bytes_ = 0;
  bool stopped_ = false;
  uint64_t epoch_ = 0;

public:
  static constexpr size_t maxPackets = 8, maxBytes = 16 * 1024 * 1024;
  bool push(uint64_t timestamp, const uint8_t *data, size_t count) {
    std::lock_guard<std::mutex> l(mutex_);
    if (stopped_ || count == 0 || count > maxBytes || q_.size() >= maxPackets ||
        bytes_ + count > maxBytes)
      return false;
    q_.push_back({timestamp, epoch_, monotonicNs(), {data, data + count}});
    bytes_ += count;
    cv_.notify_one();
    return true;
  }
  bool pop(Encoded &p) {
    std::unique_lock<std::mutex> l(mutex_);
    cv_.wait(l, [&] { return stopped_ || !q_.empty(); });
    if (stopped_)
      return false;
    p = std::move(q_.front());
    q_.pop_front();
    bytes_ -= p.bytes.size();
    return true;
  }
  void reset() {
    std::lock_guard<std::mutex> l(mutex_);
    ++epoch_;
    q_.clear();
    bytes_ = 0;
  }
  void stop() {
    std::lock_guard<std::mutex> l(mutex_);
    stopped_ = true;
    q_.clear();
    bytes_ = 0;
    cv_.notify_all();
  }
  uint64_t epoch() {
    std::lock_guard<std::mutex> l(mutex_);
    return epoch_;
  }
  size_t size() {
    std::lock_guard<std::mutex> l(mutex_);
    return q_.size();
  }
};
} // namespace vrost
