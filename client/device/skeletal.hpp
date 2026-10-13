#pragma once
#include "client/transport/bridge.h"
#include <array>
#include <cmath>
#include <cstdint>

namespace vrost::skeletal {
// Inverse of ALVR f5023f8e78fa14ea57fb1038548d7820289a8524
// alvr/server_openvr/src/tracking.rs::to_openvr_ffi_hand_skeleton.
// Source: https://github.com/alvr-org/ALVR (MIT; see licenses/ALVR-MIT.txt).
// A network joint is a GLOBAL OpenXR-basis pose, not an OpenVR model-space bone.
// Preserve the 25 wrist/finger parent-space bones. ALVR regenerates auxiliaries
// 26..30 and publishes the selected range to both host motion ranges.
using Bones = std::array<VrostPose, 31>;
using Joints = std::array<VrostPose, 26>;
inline constexpr std::array<int32_t, 31> parents = {
    -1, 0, 1, 2, 3, 4, 1, 6, 7, 8, 9, 1, 11, 12, 13, 14,
    1, 16, 17, 18, 19, 1, 21, 22, 23, 24, 0, 0, 0, 0, 0};
inline VrostQuat multiply(VrostQuat a, VrostQuat b) {
  return {a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
          a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
          a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,
          a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z};
}
inline VrostQuat conjugate(VrostQuat q) { return {-q.x,-q.y,-q.z,q.w}; }
inline VrostPose compose(VrostPose a, VrostPose b) {
  auto v=multiply(multiply(a.orientation,{b.position[0],b.position[1],b.position[2],0}),conjugate(a.orientation));
  return {multiply(a.orientation,b.orientation),
          {a.position[0]+v.x,a.position[1]+v.y,a.position[2]+v.z}};
}
inline bool normalize(VrostPose &p, float positionLimit) {
  for (float v : p.position)
    if (!std::isfinite(v) || std::abs(v)>positionLimit) return false;
  auto &q=p.orientation;
  const float norm=q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w;
  if (!std::isfinite(norm) || norm<0.5f || norm>1.5f) return false;
  // ALVR derives legacy curl scalars from quaternion components. Canonicalize
  // q/-q so equivalent rotations cannot flip the apparent curl sign.
  const float s=(q.w<0 ? -1.f : 1.f)/std::sqrt(norm);
  q={q.x*s,q.y*s,q.z*s,q.w*s};
  return true;
}
inline bool validBones(Bones &bones) {
  // Bone zero is ignored by the host. Auxiliary bones are regenerated there.
  for (size_t i=1;i<26;++i)
    if (!normalize(bones[i],2.f)) return false;
  return true;
}
inline bool validHierarchy(const std::array<int32_t,31> &hierarchy) {
  return hierarchy == parents;
}
inline bool toAlvr(const Bones &bones, VrostPose controller, bool right, Joints &joints) {
  if (!normalize(controller,10000.f)) return false;
  constexpr float h=0.7071067811865475244f;
  // Rust/glam XZY(pi, +/-pi/2, 0) = qX(pi)*qZ(+/-pi/2).
  const VrostQuat rootCorrection = {h,right ? -h : h,0,0};
  // Rust/glam YXZ(-pi/2, pi/2, 0), identically on BOTH hands.
  const VrostQuat fixedWristCorrection = {0.5f,-0.5f,0.5f,0.5f};
  joints[0]=controller;
  auto wrist=bones[1];
  wrist.orientation=multiply(wrist.orientation,conjugate(rootCorrection));
  joints[1]=compose(controller,wrist);
  auto fixedWrist=joints[1];
  fixedWrist.orientation=multiply(fixedWrist.orientation,fixedWristCorrection);
  for (size_t i=2;i<26;++i) {
    const auto &bone=bones[i];
    const auto q=bone.orientation;
    const auto *p=bone.position;
    const VrostPose local = right
      ? VrostPose{{-q.z,q.y,q.x,q.w},{-p[2],p[1],p[0]}}
      : VrostPose{{-q.z,-q.y,-q.x,q.w},{-p[2],-p[1],-p[0]}};
    joints[i]=compose(parents[i]==1 ? fixedWrist : joints[parents[i]],local);
  }
  return true;
}
// Bone polling can run at display rate while raw controller poses continue at
// 250 Hz. Each packet rebases the latest local bones on its OWN raw device pose.
// ALVR matches skeletons by exact packet timestamp; omitting intervening packets
// would alternate real fingers and trigger/grip animation on the host.
struct Cache {
  Bones bones{};
  uint64_t sampledAt=0;
  uint32_t device=UINT32_MAX;
  bool valid=false;
  static constexpr uint64_t maxAgeNs=50000000ULL;
  void clear() { valid=false; sampledAt=0; device=UINT32_MAX; }
  bool update(Bones value,uint32_t owner,uint64_t now) {
    clear();
    if (!validBones(value)) return false;
    bones=value; device=owner; sampledAt=now; valid=true;
    return true;
  }
  bool fresh(uint32_t owner,uint64_t now) const {
    return valid && owner==device && now>=sampledAt && now-sampledAt<=maxAgeNs;
  }
  bool sample(uint32_t owner,uint64_t now,VrostPose controller,bool right,Joints &out) const {
    return fresh(owner,now) && toAlvr(bones,controller,right,out);
  }
};
// Exact OpenVR forwarding is independent of handed coordinate conversion.
// Validate BOTH native ranges, including auxiliary bones, before publishing.
struct NativeCache {
  VrostNativeHand data{};
  uint32_t device=UINT32_MAX;
  bool valid=false;
  // The patched transport validates sampled_at against the PACKET timestamp
  // (<=50 ms, never in the future). With client prediction packet timestamps
  // are future targets, so each bone sample carries one fixed transport time:
  // the (strictly increasing) target of the tick that read it. Later packets
  // therefore never precede it. Local freshness still uses data.sampled_at
  // (acquisition time). Fixed per sample so Unchanged updates keep matching.
  uint64_t transportSampledAt=0;
  void clear() { data={}; device=UINT32_MAX; valid=false; transportSampledAt=0; }
  void setTransportTime(uint64_t value) { if (valid) transportSampledAt=value; }
  VrostNativeHand transport() const {
    VrostNativeHand value=data;
    if (transportSampledAt) value.sampled_at=transportSampledAt;
    return value;
  }
  bool update(std::array<Bones,2> ranges,const std::array<float,5> &curls,
              bool curlsValid,uint32_t owner,uint64_t now) {
    clear();
    for (auto &range:ranges)
      for (auto &bone:range)
        if (!normalize(bone,2.f)) return false;
    data.sampled_at=now;
    data.curls_valid=curlsValid;
    for (size_t i=0;i<curls.size();++i) {
      if (!std::isfinite(curls[i]) || curls[i]<0 || curls[i]>1)
        data.curls_valid=0;
      data.curls[i]=std::isfinite(curls[i]) ? curls[i] : 0;
    }
    for (size_t r=0;r<ranges.size();++r)
      for (size_t i=0;i<ranges[r].size();++i) data.ranges[r][i]=ranges[r][i];
    device=owner; valid=true; return true;
  }
  bool fresh(uint32_t owner,uint64_t now) const {
    return valid && owner==device && now>=data.sampled_at && now-data.sampled_at<=Cache::maxAgeNs;
  }
};
} // namespace vrost::skeletal
