#include "client/runtime.hpp"
#include <cstring>
#include <iomanip>
namespace vrost {
static Pose copyPose(const vr::HmdMatrix34_t& m){Pose p;std::memcpy(p.m,m.m,sizeof(p.m));return p;}
template<class T>T load(HMODULE d,const char* name){auto p=GetProcAddress(d,name);if(!p)throw std::runtime_error(std::string("OpenVR export missing: ")+name);return reinterpret_cast<T>(p);}
std::string Runtime::property(vr::TrackedDeviceIndex_t device,vr::ETrackedDeviceProperty prop)const {
    char value[1024]{};vr::ETrackedPropertyError error;
    system->GetStringTrackedDeviceProperty(device,prop,value,sizeof(value),&error);
    return error==vr::TrackedProp_Success?value:"unavailable";
}
void Runtime::start(){
    stop();
    auto path=executableDir()/L"openvr_api.dll";
    dll_=LoadLibraryExW(path.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if(!dll_)throw std::runtime_error("Cannot load bundled openvr_api.dll. Extract the whole ZIP before launching.");
    try {
        auto init=load<uint32_t(__cdecl*)(vr::EVRInitError*,vr::EVRApplicationType,const char*)>(dll_,"VR_InitInternal2");
        shutdown_=load<Shutdown>(dll_,"VR_ShutdownInternal");
        auto describe=load<const char*(__cdecl*)(vr::EVRInitError)>(dll_,"VR_GetVRInitErrorAsEnglishDescription");
        auto iface=load<void*(__cdecl*)(const char*,vr::EVRInitError*)>(dll_,"VR_GetGenericInterface");
        vr::EVRInitError error=vr::VRInitError_None;
        init(&error,vr::VRApplication_Scene,nullptr);
        if(error!=vr::VRInitError_None)throw std::runtime_error(std::string("SteamVR: ")+describe(error)+". Start SteamVR and check the headset's DP/USB connections.");
        initialised_=true;
        system=static_cast<vr::IVRSystem*>(iface(vr::IVRSystem_Version,&error));
        compositor=static_cast<vr::IVRCompositor*>(iface(vr::IVRCompositor_Version,&error));
        input=static_cast<vr::IVRInput*>(iface(vr::IVRInput_Version,&error));
        chaperone=static_cast<vr::IVRChaperone*>(iface(vr::IVRChaperone_Version,&error));
        if(!system||!compositor)throw std::runtime_error("SteamVR lacks the required OpenVR interfaces. Update SteamVR and retry.");
        compositor->SetTrackingSpace(vr::TrackingUniverseStanding);
        system->GetRecommendedRenderTargetSize(&recommendedW,&recommendedH);
        system->GetDXGIOutputInfo(&adapter);
        if(adapter<0)throw std::runtime_error("SteamVR did not provide a valid headset GPU adapter.");
        model=property(0,vr::Prop_ModelNumber_String);
        runtimeVersion=system->GetRuntimeVersion();
        vr::ETrackedPropertyError propError;
        refresh=system->GetFloatTrackedDeviceProperty(0,vr::Prop_DisplayFrequency_Float,&propError);
        if(propError!=vr::TrackedProp_Success)refresh=0;
        for(int e=0;e<2;++e){
            auto eye=static_cast<vr::EVREye>(e);
            auto& f=fov[e];system->GetProjectionRaw(eye,&f.left,&f.right,&f.top,&f.bottom);
            eyeToHead[e]=copyPose(system->GetEyeToHeadTransform(eye));
        }
        inputStatus="Input interface unavailable";
        if(input){
            auto manifest=utf8((executableDir()/L"assets"/L"actions.json").wstring());
            auto result=input->SetActionManifestPath(manifest.c_str());
            if(result==vr::VRInputError_None&&input->GetActionSetHandle("/actions/test",&actionSet_)==vr::VRInputError_None){
                const char* names[]={"pose","trigger","grip","stick","pad","a","b","trigger_touch","trigger_click","grip_touch","stick_click","stick_touch","pad_touch","a_touch","b_touch","skeleton","haptic"};
                unsigned failures=0;
                for(const char* side:{"left","right"})for(const char* name:names){
                    std::string key=std::string(side)+"_"+name;
                    auto path=std::string("/actions/test/")+(std::string(name)=="haptic"?"out/":"in/")+key;
                    vr::VRActionHandle_t handle=0;
                    if(input->GetActionHandle(path.c_str(),&handle)==vr::VRInputError_None)actions_[key]=handle;else ++failures;
                }
                inputStatus=failures?"Some action handles unavailable":"Action manifest loaded; awaiting bindings";
            }else inputStatus="Action manifest error "+std::to_string(int(result));
        }
    }catch(...){stop();throw;}
}
void Runtime::stop(){
    if(initialised_&&shutdown_)shutdown_();
    initialised_=false;system=nullptr;compositor=nullptr;input=nullptr;chaperone=nullptr;
    actions_.clear();actionSet_=0;controllers={};
    if(dll_)FreeLibrary(dll_);dll_=nullptr;shutdown_=nullptr;
}
bool Runtime::sample(Pose& head,bool& focus,bool& spaceChanged,bool& quit){
    vr::VREvent_t event{};
    while(system->PollNextEvent(&event,sizeof(event))){
        switch(event.eventType){
        case vr::VREvent_Quit:system->AcknowledgeQuit_Exiting();quit=true;break;
        case vr::VREvent_ChaperoneUniverseHasChanged:case vr::VREvent_ChaperoneDataHasChanged:
        case vr::VREvent_IpdChanged:spaceChanged=true;break;
        default:break;
        }
    }
    std::array<vr::TrackedDevicePose_t,vr::k_unMaxTrackedDeviceCount> poses{};
    auto error=compositor->WaitGetPoses(poses.data(),static_cast<uint32_t>(poses.size()),nullptr,0);
    focus=system->IsInputAvailable()&&!system->ShouldApplicationPause()&&compositor->CanRenderScene();
    // Do not log controller poses or gaze samples. Live display is local only.
    controllers={};
    unsigned active=0;
    if(input&&actionSet_&&focus){
        vr::VRActiveActionSet_t set{};set.ulActionSet=actionSet_;
        const auto status=input->UpdateActionState(&set,sizeof(set),1);
        if(status==vr::VRInputError_None){
            for(int hand=0;hand<2;++hand){
                std::string prefix=hand==0?"left_":"right_";
                auto handle=[&](const std::string& n){auto it=actions_.find(prefix+n);return it==actions_.end()?vr::k_ulInvalidActionHandle:it->second;};
                vr::InputPoseActionData_t pose{};
                if(input->GetPoseActionDataForNextFrame(handle("pose"),vr::TrackingUniverseStanding,&pose,sizeof(pose),vr::k_ulInvalidInputValueHandle)==vr::VRInputError_None&&pose.bActive&&pose.pose.bPoseIsValid){controllers[hand].valid=true;controllers[hand].pose=copyPose(pose.pose.mDeviceToAbsoluteTracking);++active;}
                for(const char* name:{"trigger","grip","stick","pad"}){
                    vr::InputAnalogActionData_t value{};
                    if(input->GetAnalogActionData(handle(name),&value,sizeof(value),vr::k_ulInvalidInputValueHandle)==vr::VRInputError_None&&value.bActive){controllers[hand].values[name]=value.x;if(std::string(name)=="stick"||std::string(name)=="pad")controllers[hand].values[std::string(name)+"_y"]=value.y;}
                }
                for(const char* name:{"a","b","trigger_touch","trigger_click","grip_touch","stick_click","stick_touch","pad_touch","a_touch","b_touch"}){
                    vr::InputDigitalActionData_t value{};
                    if(input->GetDigitalActionData(handle(name),&value,sizeof(value),vr::k_ulInvalidInputValueHandle)==vr::VRInputError_None&&value.bActive)controllers[hand].values[name]=value.bState?1.f:0.f;
                }
                vr::InputSkeletalActionData_t skeletal{};
                vr::VRSkeletalSummaryData_t summary{};
                if(input->GetSkeletalActionData(handle("skeleton"),&skeletal,sizeof(skeletal))==vr::VRInputError_None&&skeletal.bActive&&input->GetSkeletalSummaryData(handle("skeleton"),vr::VRSummaryType_FromDevice,&summary)==vr::VRInputError_None)
                    for(int finger=0;finger<5;++finger)controllers[hand].values["curl"+std::to_string(finger)]=summary.flFingerCurl[finger];
            }
            inputStatus="Action poses active: "+std::to_string(active)+" / 2";
        }else inputStatus="UpdateActionState error "+std::to_string(int(status));
    }
    bool valid=error==vr::VRCompositorError_None&&poses[0].bDeviceIsConnected&&poses[0].bPoseIsValid&&poses[0].eTrackingResult==vr::TrackingResult_Running_OK;
    if(valid)head=copyPose(poses[0].mDeviceToAbsoluteTracking);
    return valid;
}
void Runtime::haptic(int hand){
    if(!input||hand<0||hand>1||!controllers[hand].valid)return;
    auto it=actions_.find(hand==0?"left_haptic":"right_haptic");
    if(it!=actions_.end())input->TriggerHapticVibrationAction(it->second,0,.08f,100,.25f,vr::k_ulInvalidInputValueHandle);
}
std::string Runtime::report()const{
    std::ostringstream s;s<<std::fixed<<std::setprecision(4);
    s<<"Headset: "<<model<<"\nSteamVR runtime: "<<runtimeVersion<<"\nOpenVR interface: "<<vr::IVRSystem_Version<<" / "<<vr::IVRCompositor_Version;
    s<<"\nRecommended eye target: "<<recommendedW<<" x "<<recommendedH<<"\nReported refresh Hz: "<<refresh<<"\nRuntime DXGI adapter index: "<<adapter<<"\n";
    for(int e=0;e<2;++e){const auto& f=fov[e];s<<"Eye "<<e<<" raw L/R/T/B: "<<f.left<<", "<<f.right<<", "<<f.top<<", "<<f.bottom<<"\nEye-to-head:\n";for(auto& row:eyeToHead[e].m)s<<row[0]<<" "<<row[1]<<" "<<row[2]<<" "<<row[3]<<"\n";}
    if(chaperone){float x=0,z=0;s<<"Chaperone calibration enum: "<<int(chaperone->GetCalibrationState())<<"\n";if(chaperone->GetPlayAreaSize(&x,&z))s<<"Play area metres: "<<x<<" x "<<z<<"\n";}
    if(system)for(uint32_t i=0;i<vr::k_unMaxTrackedDeviceCount;++i)if(system->IsTrackedDeviceConnected(i))s<<"Device slot "<<i<<" class "<<int(system->GetTrackedDeviceClass(i))<<" model "<<property(i,vr::Prop_ModelNumber_String)<<"\n";
    s<<"Serial numbers, usernames, IPs, raw tracking and eye data omitted.\n";
    return s.str();
}
}
