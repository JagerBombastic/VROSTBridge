#include "client/device/skeletal.hpp"
#include "core/contracts.hpp"
#include <iostream>
#include <stdexcept>
#include <limits>

namespace {
constexpr float pi=3.14159265358979323846f;
void check(bool condition,const char *message) {
  if (!condition) throw std::runtime_error(message);
}
vrost::Pose matrix(VrostPose q) {
  const auto a=q.orientation; const auto x=a.x,y=a.y,z=a.z,w=a.w;
  vrost::Pose p{{{1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),q.position[0]},
              {2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),q.position[1]},
              {2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y),q.position[2]}}};
  return p;
}
vrost::Pose axis(int axis,float angle) {
  VrostPose p{{0,0,0,std::cos(angle/2)},{0,0,0}};
  if (axis==0) p.orientation.x=std::sin(angle/2);
  if (axis==1) p.orientation.y=std::sin(angle/2);
  if (axis==2) p.orientation.z=std::sin(angle/2);
  return matrix(p);
}
bool close(vrost::Pose a,vrost::Pose b,float tolerance=0.00003f) {
  for (int r=0;r<3;++r) for (int c=0;c<4;++c)
    if (std::abs(a.m[r][c]-b.m[r][c])>tolerance) return false;
  return true;
}
// Independent matrix-form reference for the PINNED HOST'S forward conversion.
// This intentionally uses core rigid transforms and basis matrices, not the
// inverse implementation's quaternion functions/parent table. Derived from
// ALVR f5023f8e78fa14ea57fb1038548d7820289a8524 tracking.rs (MIT).
std::array<vrost::Pose,26> hostReference(const vrost::skeletal::Joints &j,bool right) {
  std::array<vrost::Pose,26> g{},out{};
  for (size_t i=0;i<g.size();++i) g[i]=matrix(j[i]);
  out[0]=g[0];
  auto rootRelative=vrost::compose(vrost::inverseRigid(g[0]),g[1]);
  auto correction=vrost::compose(axis(0,pi),axis(2,right ? pi/2 : -pi/2));
  out[1]=vrost::compose(rootRelative,correction);
  auto wrist=vrost::compose(g[1],vrost::compose(axis(1,-pi/2),axis(0,pi/2)));
  const vrost::Pose basis=right
    ? vrost::Pose{{{0,0,1,0},{0,1,0,0},{-1,0,0,0}}}
    : vrost::Pose{{{0,0,-1,0},{0,-1,0,0},{-1,0,0,0}}};
  const int starts[]={2,6,11,16,21},ends[]={6,11,16,21,26};
  for (int finger=0;finger<5;++finger) for (int i=starts[finger];i<ends[finger];++i) {
    auto parent=i==starts[finger] ? wrist : g[i-1];
    auto local=vrost::compose(vrost::inverseRigid(parent),g[i]);
    out[i]=vrost::compose(vrost::compose(basis,local),vrost::inverseRigid(basis));
  }
  return out;
}
vrost::skeletal::Bones fixture(bool right,float curl) {
  vrost::skeletal::Bones b{};
  for (auto &p:b) p.orientation.w=1;
  // Valve's OpenVR hand simulation sample explicitly identifies this as the
  // Index wrist transform. Source: ValveSoftware/openvr samples/drivers/
  // drivers/handskeletonsimulation/src/hand_simulation.cpp (BSD-3-Clause).
  b[1]={{-.078608f,right ? .920279f : -.920279f,right ? -.379296f : .379296f,-.055147f},
        {right ? .034038f : -.034038f,.036503f,.164722f}};
  const int starts[]={2,6,11,16,21},ends[]={6,11,16,21,26};
  for (int finger=0;finger<5;++finger) for (int i=starts[finger];i<ends[finger];++i) {
    // Distinct per-finger flexion and splay catch index-order/cross-hand bugs.
    const float angle=curl*(.24f+.17f*finger+.11f*(i-starts[finger]));
    b[i].orientation={0,0,std::sin(angle/2),std::cos(angle/2)};
    b[i].position[0]=(right ? -1.f : 1.f)*(.015f+.003f*finger);
    b[i].position[1]=i==starts[finger] ? .005f*(finger-2) : 0;
    b[i].position[2]=i==starts[finger] ? -.02f : .003f;
  }
  return b;
}
VrostPose device(float angle,float x,float y,float z) {
  return {{.3f*std::sin(angle/2),.4f*std::sin(angle/2),.8660254038f*std::sin(angle/2),std::cos(angle/2)},{x,y,z}};
}
}
int main() {
  using namespace vrost::skeletal;
  const std::array<VrostPose,4> devices={device(0,0,0,0),device(1.2f,2,1.4f,-3),
      device(-2.4f,-1,.4f,2),device(pi,50,-2,1)};
  for (bool right : {false,true}) for (float curl : {0.f,.4f,1.f}) for (const auto &pose:devices) {
    auto bones=fixture(right,curl);
    check(validBones(bones),"Index wrist/finger fixture rejected");
    Joints joints{};
    check(toAlvr(bones,pose,right,joints),"joint encoding failed");
    auto recovered=hostReference(joints,right);
    for (size_t i=1;i<26;++i)
      check(close(recovered[i],matrix(bones[i])),"pinned host round-trip changed wrist/finger transform");
    // Equivalent quaternion signs must not change the legacy curl components.
    auto negated=bones;
    for (auto &p:negated) { auto &q=p.orientation; q={-q.x,-q.y,-q.z,-q.w}; }
    check(validBones(negated),"equivalent signed quaternion rejected");
    Joints same{}; check(toAlvr(negated,pose,right,same),"signed encoding failed");
    for (size_t i=0;i<26;++i) {
      auto a=joints[i].orientation,b=same[i].orientation;
      check(std::abs(a.x-b.x)<1e-6 && std::abs(a.y-b.y)<1e-6 && std::abs(a.z-b.z)<1e-6 && std::abs(a.w-b.w)<1e-6,
            "quaternion sign changed scalar finger curl");
    }
  }
  // Absolute basis goldens, independent of a round-trip: identity local wrist
  // with identity device must map to the inverse host wrist correction.
  Bones identity{}; for (auto &p:identity) p.orientation.w=1;
  Joints left{},right{};
  check(toAlvr(identity,device(0,0,0,0),false,left),"left basis failed");
  check(toAlvr(identity,device(0,0,0,0),true,right),"right basis failed");
  const float h=std::sqrt(.5f);
  check(std::abs(left[1].orientation.x+h)<1e-6 && std::abs(left[1].orientation.y+h)<1e-6,"left wrist golden");
  check(std::abs(right[1].orientation.x+h)<1e-6 && std::abs(right[1].orientation.y-h)<1e-6,"right wrist golden");
  auto hierarchy=parents; check(validHierarchy(hierarchy),"Index hierarchy rejected");
  hierarchy[11]=10; check(!validHierarchy(hierarchy),"wrong finger parent accepted");
  Cache cache; auto bones=fixture(false,.6f); Joints out{};
  check(cache.update(bones,3,100000000ULL),"cache failed");
  check(cache.sample(3,104000000ULL,devices[1],false,out),"fresh rebase failed");
  check(!cache.sample(4,104000000ULL,devices[1],false,out),"opposite/new device reused bones");
  check(!cache.sample(3,99000000ULL,devices[1],false,out),"future bones reused");
  check(cache.fresh(3,150000000ULL),"freshness boundary rejected");
  check(!cache.sample(3,150000001ULL,devices[1],false,out),"stale fingers reused");
  cache.clear(); check(!cache.sample(3,104000000ULL,devices[1],false,out),"disconnected bones reused");
  for (int corruption=0;corruption<3;++corruption) {
    auto invalid=bones;
    if (corruption==0) invalid[7].position[0]=std::numeric_limits<float>::quiet_NaN();
    if (corruption==1) invalid[12].orientation={0,0,0,0};
    if (corruption==2) invalid[22].position[2]=3.f;
    check(!cache.update(invalid,3,200000000ULL) && !cache.valid,"invalid data left cached fingers active");
  }
  auto invalidDevice=devices[0]; invalidDevice.position[0]=std::numeric_limits<float>::infinity();
  check(!toAlvr(bones,invalidDevice,false,out),"nonfinite controller produced network joints");
  // Native hand data preserves every auxiliary and both distinct ranges.
  NativeCache native;
  auto constrained=fixture(false,.2f),free=fixture(false,.8f);
  constrained[30].position[0]=.125f; free[30].position[0]=.225f;
  const std::array<float,5> curls={.1f,.2f,.4f,.6f,.8f};
  check(native.update({constrained,free},curls,true,3,100000000ULL),"native ranges rejected");
  check(sizeof(VrostNativeHand)==1768,"native ABI layout changed");
  check(native.data.ranges[0][30].position[0]==.125f && native.data.ranges[1][30].position[0]==.225f,"native auxiliaries or ranges changed");
  for (size_t i=0;i<curls.size();++i) check(native.data.curls[i]==curls[i],"named curl order changed");
  check(native.fresh(3,150000000ULL) && !native.fresh(3,150000001ULL),"native timestamp expiry failed");
  check(!native.fresh(4,104000000ULL) && !native.fresh(3,99000000ULL),"wrong-hand or future native cache accepted");
  auto badCurls=curls; badCurls[2]=std::numeric_limits<float>::quiet_NaN();
  check(native.update({constrained,free},badCurls,true,3,104000000ULL) && !native.data.curls_valid,"bad curl must fall back without damaging valid bones");
  auto badAux=constrained; badAux[30].position[0]=3.f;
  check(!native.update({badAux,free},curls,true,3,108000000ULL) && !native.valid,"invalid native auxiliary accepted");
  check(native.update({fixture(true,.3f),fixture(true,.9f)},curls,false,4,112000000ULL),"right native ranges rejected");
  native.clear(); check(!native.fresh(4,112000000ULL),"native cache survived disconnect");
  {
    // RC2 prediction: one fixed transport time per bone sample; local
    // freshness keeps using acquisition time.
    vrost::skeletal::NativeCache cache;
    check(cache.update({fixture(false,.1f),fixture(false,.2f)},curls,true,3,200000000ULL),"native fixture rejected");
    check(cache.transport().sampled_at==200000000ULL,"default transport time changed");
    cache.setTransportTime(260000000ULL);
    check(cache.transport().sampled_at==260000000ULL && cache.data.sampled_at==200000000ULL,"transport time not separate");
    check(cache.fresh(3,240000000ULL),"freshness must use acquisition time");
    check(cache.update({fixture(false,.1f),fixture(false,.2f)},curls,true,3,210000000ULL) && cache.transport().sampled_at==210000000ULL,
          "new sample must not inherit an old transport time");
    cache.clear(); cache.setTransportTime(5); check(cache.transportSampledAt==0,"cleared cache accepted a transport time");
  }
  std::cout << "Index skeletal basis, both-hand pose round-trip, sign, native ranges/curls and stale-data contracts passed\n";
}
