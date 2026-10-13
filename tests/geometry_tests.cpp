#include "client/device/view_geometry.hpp"
#include "client/device/runtime_events.hpp"
#include "client/device/openvr_projection.hpp"
#include <iostream>
#include <stdexcept>
#include <thread>
static void check(bool condition) { if (!condition) throw std::runtime_error("live geometry regression"); }
static vrost::ViewGeometry::Views local(float ipd) {
  vrost::ViewGeometry::Views v{};
  for(int e=0;e<2;++e)
    v[e]={{{0,0,0,1},{e?ipd/2:-ipd/2,0,0}},-.8f,.9f,.85f,-.75f};
  return v;
}
static vrost::ViewGeometry::Views rendered(vrost::ViewGeometry::Views v) {
  VrostPose head{{0,.5f,0,.8660254f},{1,2,3}};
  for(auto &eye:v) eye.pose=vrost::quaternion(vrost::compose(vrost::matrix(head),vrost::matrix(eye.pose)));
  return v;
}
static void recovers(const vrost::ViewGeometry &geometry,const vrost::ViewGeometry::Views &views) {
  auto source=rendered(views);vrost::Pose head;
  check(geometry.sourcePose(source.data(),head));
  for(int i=0;i<3;++i) check(std::abs(head.m[i][3]-(i+1))<1e-5f);
}
int main() {
  // Round-trip through the pinned ALVR host's fov_to_tangents convention.
  // Unequal top/bottom values expose the old sign-and-swap bug, which was
  // invisible with a vertically symmetric projection. Fixtures are synthetic,
  // not calibration values inferred from any headset model.
  const std::array<vrost::RawProjection,3> projections{{
      {{-1.1f,.9f,-1.3f,.7f}},{{-.85f,1.2f,-.65f,1.25f}},{{-1.f,1.f,-1.f,1.f}}
  }};
  VrostPose optical{{0,.08715574f,0,.9961947f},{-.032f,.003f,.016f}};
  const auto eyeTransform=vrost::matrix(optical);
  for(const auto& raw:projections) {
    const auto v=vrost::openvrView(eyeTransform,raw);
    const vrost::RawProjection host{{std::tan(v.left),std::tan(v.right),
                                  std::tan(v.down),std::tan(v.up)}};
    for(int k=0;k<4;++k)check(std::abs(host[k]-raw[k])<1e-5f);
    // Preserve optical-centre offset and scale, not just the total FOV.
    check(std::abs((host[3]+host[2])/(host[3]-host[2])-
                   (raw[3]+raw[2])/(raw[3]-raw[2]))<1e-5f);
    const auto retained=vrost::matrix(v.pose);
    for(int i=0;i<3;++i)for(int j=0;j<4;++j)
      check(std::abs(retained.m[i][j]-eyeTransform.m[i][j])<1e-5f);
  }
  {
    const auto& raw=projections[0];
    const float oldTop=std::tan(std::atan(-raw[3]));
    const float oldBottom=std::tan(std::atan(-raw[2]));
    check(std::abs((oldBottom+oldTop)/(oldBottom-oldTop)-
                   (raw[3]+raw[2])/(raw[3]-raw[2]))>.5f);
  }
  vrost::ViewGeometry geometry;
  auto old=local(.064f), next=local(.065f); // small enough for old loose tolerance
  check(geometry.update(old));check(geometry.snapshot().revision==1);
  check(!geometry.update(old)); // Initial/duplicate IPD events are harmless.
  check(geometry.update(next));check(geometry.snapshot().revision==2);
  recovers(geometry,old);recovers(geometry,next); // In-flight old AND new frames.
  auto bad=next;bad[0].pose.position[0]=std::numeric_limits<float>::quiet_NaN();
  check(!geometry.update(bad));check(geometry.snapshot().revision==2);
  bad=next;bad[0].up=bad[0].down;check(!geometry.update(bad));
  auto fov=next;fov[0].left-=.04f;fov[1].right+=.04f;
  check(geometry.update(fov));recovers(geometry,fov);recovers(geometry,old);
  auto source=rendered(next);source[1].pose.position[2]+=.1f;vrost::Pose head;
  check(!geometry.sourcePose(source.data(),head));
  // The tracker, rendering loop and decoder access geometry concurrently.
  std::thread writer([&]{for(int i=0;i<2000;++i)geometry.update(local(.05f+float(i%200)*.0001f));});
  for(int i=0;i<2000;++i) check(vrost::ViewGeometry::valid(geometry.snapshot().views));
  writer.join();recovers(geometry,geometry.snapshot().views);
  check(vrost::runtimeRequestsExit(vr::VREvent_Quit));
  check(!vrost::runtimeRequestsExit(vr::VREvent_ProcessQuit));
  check(!vrost::runtimeRequestsExit(vr::VREvent_IpdChanged));
  check(vrost::runtimeChangesGeometry(vr::VREvent_IpdChanged));
  check(vrost::runtimeChangesGeometry(vr::VREvent_LensDistortionChanged));
  std::cout<<"Live IPD, old/new frame provenance, invalid updates, concurrent snapshots and runtime events passed\n";
}
