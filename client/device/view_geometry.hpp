#pragma once
#include "core/stream.hpp"
#include <array>
#include <limits>

namespace vrost {
// The transport includes the rendered eye poses with each frame. During an IPD
// adjustment frames using the old and new eye transforms can both be in flight.
// Match both eyes against a bounded history, rather than combining an old frame
// with the newest IPD (which would introduce a translation error).
class ViewGeometry {
public:
  using Views = std::array<VrostView, 2>;
  struct Snapshot { Views views{}; uint64_t revision = 0; };
private:
  mutable std::mutex mutex_;
  std::deque<Views> history_;
  uint64_t revision_ = 0;
public:
  static bool valid(const Views &views) {
    for (const auto &v : views) {
      if (!finitePose(v.pose)) return false;
      for (float p : v.pose.position)
        if (std::abs(p) > .5f) return false;
      for (float angle : {v.left, v.right, v.up, v.down})
        if (!std::isfinite(angle) || std::abs(angle) >= 1.57f) return false;
      if (v.left >= v.right || v.down >= v.up) return false;
    }
    float distance = 0;
    for (int axis=0; axis<3; ++axis) {
      float d=views[0].pose.position[axis]-views[1].pose.position[axis];
      distance+=d*d;
    }
    return distance>.001f*.001f && distance<.2f*.2f;
  }
  bool update(const Views &views) {
    if (!valid(views)) return false;
    std::lock_guard<std::mutex> lock(mutex_);
    if (!history_.empty() && std::memcmp(history_.front().data(),views.data(),sizeof(Views))==0)
      return false;
    history_.push_front(views);
    if (history_.size()>256) history_.pop_back();
    ++revision_;
    return true;
  }
  Snapshot snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return {history_.empty() ? Views{} : history_.front(),revision_};
  }
  bool sourcePose(const VrostView *source, Pose &head) const {
    std::lock_guard<std::mutex> lock(mutex_);
    float best=std::numeric_limits<float>::infinity();
    bool found=false;
    for (const auto &views : history_) {
      Pose candidate; float score=0;
      if (sourceHead(source,views.data(),candidate,&score) && score<best) {
        head=candidate;best=score;found=true;
        if (score<1e-12f) break;
      }
    }
    return found;
  }
};
} // namespace vrost
