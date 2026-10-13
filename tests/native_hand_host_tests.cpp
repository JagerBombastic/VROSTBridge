// Compiles the SAME native driver helper used by Controller::OnPoseUpdate.
#include "VROSTBridgeHand.h"
#include "VROSTBridgeButtonInput.h"
#include <map>
#include <vector>
#include <iostream>
#include <limits>
#include <stdexcept>
void check(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
int main() {
    FfiNativeOpenvrHand sample{};
    sample.sampledAtNs=100000000;
    sample.curlsValid=true;
    const float curls[]={0.07f,0.13f,0.37f,0.61f,0.89f};
    std::copy_n(curls,5,sample.curls);
    // Deliberately conflicting bone rotations catch a silent estimate override.
    for (auto& range:sample.ranges) for (auto& q:range.jointRotations) q.z=-1.0f;
    auto host=VROSTBridgeNativeFingerCurls(sample);
    for(int i=0;i<4;++i) check(host[i]==curls[i+1],"source curl changed or finger order shifted");
    check(VROSTBridgeNativeHandIsFresh(&sample,150000000),"fresh boundary rejected");
    check(!VROSTBridgeNativeHandIsFresh(&sample,150000001),"stale source accepted");
    check(!VROSTBridgeNativeHandIsFresh(&sample,99999999),"future source accepted");
    check(!VROSTBridgeNativeHandIsFresh(nullptr,100000000),"missing source accepted");
    sample.curlsValid=false;
    host=VROSTBridgeNativeFingerCurls(sample);
    for(float v:host) check(v==0.0f,"negative fallback was not bounded");
    sample.curlsValid=true; sample.curls[3]=std::numeric_limits<float>::quiet_NaN();
    host=VROSTBridgeNativeFingerCurls(sample);
    for(float v:host) check(v==0.0f,"invalid measured input bypassed fallback");
    const std::map<uint64_t,std::vector<uint64_t>> mappings{{10,{1,2,3}}};
    const std::map<uint64_t,uint64_t> handles{{1,100},{2,0}};
    FfiButtonValue button{}; button.type=BUTTON_TYPE_BINARY; button.binary=1;
    int updates=0; float gameValue=0;
    auto publish=[&](uint64_t handle,FfiButtonValue v) {
        check(handle==100,"invalid or unknown component published");
        ++updates; gameValue=v.type==BUTTON_TYPE_BINARY ? v.binary : v.scalar;
    };
    check(!VROSTBridgePublishControllerButton(false,10,button,mappings,handles,publish),"input before activation");
    check(VROSTBridgePublishControllerButton(true,10,button,mappings,handles,publish),"valid press lost");
    check(gameValue==1 && updates==1,"press not applied");
    // Device stays activated during loss of its optical pose. Its release must
    // reach the game without waiting for another controller movement.
    button.binary=0;
    check(VROSTBridgePublishControllerButton(true,10,button,mappings,handles,publish),"invalid-pose release lost");
    check(gameValue==0 && updates==2,"stuck pressed value after optical loss");
    check(!VROSTBridgePublishControllerButton(true,99,button,mappings,handles,publish),"unknown mapping accepted");
    button.type=BUTTON_TYPE_SCALAR; button.scalar=std::numeric_limits<float>::quiet_NaN();
    check(!VROSTBridgePublishControllerButton(true,10,button,mappings,handles,publish),"NaN scalar accepted");
    check(updates==2,"invalid input changed game state");
    std::cout << "Native host exact scalar, expiry, activated-component and optical-loss input tests passed\n";
}
