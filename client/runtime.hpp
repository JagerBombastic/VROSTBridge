#pragma once
#include "client/common.hpp"
#include "openvr.h"
#include <map>
#include <vector>
namespace vrost {
struct Controller {
    bool valid=false; Pose pose{};
    std::map<std::string,float> values;
};
class Runtime {
    HMODULE dll_=nullptr;
    bool initialised_=false;
    using Shutdown=void(__cdecl*)();
    Shutdown shutdown_=nullptr;
    std::map<std::string,vr::VRActionHandle_t> actions_;
    vr::VRActionSetHandle_t actionSet_=0;
public:
    vr::IVRSystem* system=nullptr;
    vr::IVRCompositor* compositor=nullptr;
    vr::IVRInput* input=nullptr;
    vr::IVRChaperone* chaperone=nullptr;
    std::array<Fov,2> fov;
    std::array<Pose,2> eyeToHead;
    std::array<Controller,2> controllers;
    std::string inputStatus="Not started";
    std::string model,runtimeVersion;
    std::uint32_t recommendedW=0,recommendedH=0;
    float refresh=0;
    int adapter=-1;
    Runtime()=default;~Runtime(){stop();}
    void start();void stop();
    bool sample(Pose& head,bool& focus,bool& spaceChanged,bool& quit);
    void haptic(int hand);
    std::string report()const;
    std::string property(vr::TrackedDeviceIndex_t device,vr::ETrackedDeviceProperty property)const;
};
}
