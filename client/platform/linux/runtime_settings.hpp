#pragma once
#include "openvr.h"
#include "client/device/headset_metadata.hpp"
#include "client/device/runtime_readiness.hpp"
#include <chrono>
#include <thread>
#ifdef _WIN32
#include "client/platform/windows/dynamic_library.hpp"
#else
#include <dlfcn.h>
#endif
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sstream>
namespace vrost {
struct RuntimePreferences {
  bool home=true, dashboard=true, system=false, quiet=true, legacy=false;
};
inline RuntimePreferences readRuntimePreferences(const std::filesystem::path& receipt) {
  std::ifstream in(receipt); std::string magic; int h=-1,d=-1,s=-1,q=-1;
  RuntimePreferences saved;
  if(!(in>>magic>>h>>d)) throw std::runtime_error("Invalid runtime settings receipt");
  if(magic=="VROSTBridgeRuntime1") { saved.legacy=true; s=0; in>>q; }
  else if(magic=="VROSTBridgeRuntime2") in>>s>>q;
  else throw std::runtime_error("Unknown runtime settings receipt");
  if(!in || (h!=0&&h!=1)||(d!=0&&d!=1)||(s!=0&&s!=1)||(q!=0&&q!=1))
    throw std::runtime_error("Invalid runtime settings receipt values");
  std::string trailing; if(in>>trailing) throw std::runtime_error("Invalid runtime settings receipt suffix");
  saved.home=h; saved.dashboard=d; saved.system=s; saved.quiet=q;
  return saved;
}
inline void writeRuntimePreferences(const std::filesystem::path& receipt,const RuntimePreferences& saved) {
  auto temp=receipt; temp+=".tmp";
  std::ofstream out(temp); out<<"VROSTBridgeRuntime2 "<<saved.home<<" "<<saved.dashboard<<" "<<saved.system<<" "<<saved.quiet<<"\n";
  out.close(); if(!out) throw std::runtime_error("Cannot save runtime settings receipt");
  std::filesystem::rename(temp,receipt);
}
// Getter/setter injection also lets regression tests check restoration without
// requiring a running headset runtime. Restore only values still set by us.
// v1 did not touch System input, and always disabled Home, even if quiet=false.
// v2 full mode changes System input only.
template<class Get,class Set>
inline void applyRuntimePreferences(const RuntimePreferences& saved,bool restore,Get get,Set set) {
  auto change=[&](const char* section,const char* key,bool original,bool applied) {
    if(restore && get(section,key)!=applied) return;
    set(section,key,restore?original:applied);
    if(get(section,key)!=(restore?original:applied)) throw std::runtime_error("SteamVR did not accept the runtime preference");
  };
  if(saved.legacy || saved.quiet)
    change(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_EnableHomeApp,saved.home,false);
  if(saved.quiet)
    change(vr::k_pch_Dashboard_Section,vr::k_pch_Dashboard_EnableDashboard_Bool,saved.dashboard,false);
  if(!saved.legacy)
    change(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_SendSystemButtonToAllApps_Bool,saved.system,true);
}
// Short-lived background helper: never becomes a scene and never edits the
// settings file behind a running SteamVR process. Receipt survives app crashes.
inline int runtimeSettings(const std::filesystem::path& dir, const std::filesystem::path& receipt, bool restore, bool quiet, bool verify=false, VerifiedDisplayTiming* verifiedTiming=nullptr) {
  if(verifiedTiming) *verifiedTiming={};
#ifdef _WIN32
  // Windows: openvr_api.dll from the program's folder, as libopenvr_api.so on Linux.
  void* lib=loadLibrary(dir/"openvr_api.dll");
  const auto dlsym=[](void* handle,const char* name){return librarySymbol(handle,name);};
  const auto dlclose=[](void* handle){closeLibrary(handle);};
#else
  void* lib=dlopen((dir/"libopenvr_api.so").c_str(),RTLD_NOW|RTLD_LOCAL);
#endif
  if(!lib) { std::cerr<<"Runtime helper loader failed\n"; return 1; }
  auto init=reinterpret_cast<uint32_t(*)(vr::EVRInitError*,vr::EVRApplicationType,const char*)>(dlsym(lib,"VR_InitInternal2"));
  auto getInterface=reinterpret_cast<void*(*)(const char*,vr::EVRInitError*)>(dlsym(lib,"VR_GetGenericInterface"));
  auto end=reinterpret_cast<void(*)()>(dlsym(lib,"VR_ShutdownInternal"));
  if(!init||!getInterface||!end) { dlclose(lib); return 1; }
  if(verify) std::cout<<"RUNTIME_STAGE background_init"<<std::endl;
  vr::EVRInitError error{}; init(&error,vr::VRApplication_Background,nullptr);
  if(error) { dlclose(lib); std::cout<<"RUNTIME_WAIT "<<int(error)<<std::endl; return 2; }
  auto settings=static_cast<vr::IVRSettings*>(getInterface(vr::IVRSettings_Version,&error));
  int result=0;
  if(!settings) result=1;
  else try {
    auto get=[&](const char* section,const char* key) {
      vr::EVRSettingsError e{}; bool value=settings->GetBool(section,key,&e);
      if(e!=vr::VRSettingsError_None) throw std::runtime_error(std::string("Cannot read runtime preference: ")+key);
      return value;
    };
    auto set=[&](const char* section,const char* key,bool value) {
      vr::EVRSettingsError e{}; settings->SetBool(section,key,value,&e);
      if(e!=vr::VRSettingsError_None) throw std::runtime_error(std::string("Cannot set runtime preference: ")+key);
    };
    if(verify) {
      std::cout<<"RUNTIME_STAGE verify_stream_only_settings"<<std::endl;
      if(get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_EnableHomeApp) ||
         get(vr::k_pch_Dashboard_Section,vr::k_pch_Dashboard_EnableDashboard_Bool) ||
         !get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_SendSystemButtonToAllApps_Bool))
        throw std::runtime_error("Stream-only settings were not accepted. Restart from VROSTBridge.");
      auto system=static_cast<vr::IVRSystem*>(getInterface(vr::IVRSystem_Version,&error));
      auto compositor=static_cast<vr::IVRCompositor*>(getInterface(vr::IVRCompositor_Version,&error));
      DisplayReadiness readiness, compositorReadiness;
      BeyondDisplayReadiness beyondReadiness;
      float effectiveHz=0;
      bool compositorSource=false;
      bool ready=false, frameTimingAvailable=false;
      uint32_t compositorFirst=0, compositorLast=0;
      uint32_t sampleWidth=0,sampleHeight=0,attempts=0,vsyncSamples=0,vsyncAdvances=0;
      uint64_t vsyncFirst=0,vsyncLast=0;
      float sampleHz=0,sampleSince=0;
      bool connected=false,validVsync=false,validTiming=false;
      vr::ETrackedPropertyError propertyError=vr::TrackedProp_Success;
      char model[193]{};
      if(system) system->GetStringTrackedDeviceProperty(0,vr::Prop_ModelNumber_String,model,sizeof(model),&propertyError);
      auto diagnostic=[&] {
        std::cout<<"RUNTIME_SAMPLE model="<<jsonString(model)
                 <<" system="<<bool(system)<<" compositor="<<bool(compositor)
                 <<" connected="<<connected<<" property_error="<<int(propertyError)
                 <<" reported_hz="<<sampleHz<<" width="<<sampleWidth<<" height="<<sampleHeight
                 <<" samples="<<attempts<<" vsync_valid="<<validVsync
                 <<" since_vsync="<<sampleSince<<" vsync_samples="<<vsyncSamples
                 <<" vsync_advances="<<vsyncAdvances<<" vsync_first="<<vsyncFirst<<" vsync_last="<<vsyncLast
                 <<" vsync_measured_hz="<<readiness.measuredHz
                 <<" beyond75_seconds="<<beyondReadiness.measurement().observedSeconds()
                 <<" compositor_sample="<<validTiming<<" compositor_first="<<compositorFirst
                 <<" compositor_last="<<compositorLast<<" compositor_measured_hz="<<compositorReadiness.measuredHz
                 <<std::endl;
      };
      std::cout<<"RUNTIME_STAGE measure_display_progress"<<std::endl;
      const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
      auto nextDiagnostic=std::chrono::steady_clock::now()+std::chrono::seconds(1);
      while(system && compositor && std::chrono::steady_clock::now()<deadline) {
        uint32_t width=0,height=0; uint64_t frame=0; float since=0;
        system->GetRecommendedRenderTargetSize(&width,&height);
        vr::ETrackedPropertyError pe{};
        const auto hz=system->GetFloatTrackedDeviceProperty(0,vr::Prop_DisplayFrequency_Float,&pe);
        connected=system->IsTrackedDeviceConnected(0);
        const bool physical=connected && pe==vr::TrackedProp_Success;
        const bool valid=physical && system->GetTimeSinceLastVsync(&since,&frame);
        ++attempts; sampleWidth=width; sampleHeight=height; sampleHz=hz; sampleSince=since;
        propertyError=pe; validVsync=valid;
        if(valid) {
          if(!vsyncSamples) vsyncFirst=frame;
          else if(frame>vsyncLast) ++vsyncAdvances;
          ++vsyncSamples;vsyncLast=frame;
        }
        const double now=std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
        vr::Compositor_FrameTiming timing{}; timing.m_nSize=sizeof(timing);
        const bool compositorSample=compositor->GetFrameTiming(&timing) &&
            std::isfinite(timing.m_flSystemTimeInSeconds) && timing.m_flSystemTimeInSeconds>0;
        validTiming=compositorSample;
        if(compositorSample) {
          if(!frameTimingAvailable) compositorFirst=timing.m_nFrameIndex;
          compositorLast=timing.m_nFrameIndex; frameTimingAvailable=true;
        }
        if(readiness.observe(now,valid,since,frame,hz,width,height)) { effectiveHz=hz;ready=true;break; }
        if(beyondReadiness.observe(model,now,valid,since,frame,hz,width,height)) {
          readiness=beyondReadiness.measurement();effectiveHz=75;ready=true;break;
        }
        // Some drivers do not expose IVRSystem's vsync timer. The compositor's
        // advancing vsync-referenced frame history is an equivalent measured
        // cadence source. Never accept stationary history or plausible values
        // alone, and never override a valid but mismatching physical timer.
        if(!valid && compositorReadiness.observe(timing.m_flSystemTimeInSeconds,
            physical && compositorSample,0,timing.m_nFrameIndex,hz,width,height)) {
          readiness=compositorReadiness;compositorSource=true;effectiveHz=hz;ready=true;break;
        }
        if(valid) compositorReadiness.reset();
        if(std::chrono::steady_clock::now()>=nextDiagnostic) {
          diagnostic();nextDiagnostic=std::chrono::steady_clock::now()+std::chrono::seconds(1);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
      }
      diagnostic();
      if(!ready) {
        std::cout<<"RUNTIME_WAIT display_progress"<<std::endl;
        end();dlclose(lib);return 2;
      }
      // Background applications need not have submitted-frame history when
      // Home is disabled. Vsync progress is the physical readiness gate; frame
      // history is supplementary evidence, never a Scene takeover.
      std::ostringstream metadata;
      const VerifiedDisplayTiming timing{model,sampleHz,effectiveHz,readiness.measuredHz};
      if(!printHeadsetMetadata(system,metadata,&timing)) {
        std::cout<<"RUNTIME_WAIT physical_headset_metadata"<<std::endl;
        end();dlclose(lib);return 2;
      }
      std::cout<<"RUNTIME_READY display_progress display_frames="<<readiness.frames
               <<" measured_hz="<<readiness.measuredHz
               <<" reported_hz="<<sampleHz<<" effective_hz="<<effectiveHz
               <<" refresh_reconciled="<<(effectiveHz!=sampleHz)
               <<" display_source="<<(compositorSource ? "compositor" : "vsync")
               <<" compositor_timing="<<frameTimingAvailable
               <<" compositor_frames="<<uint32_t(compositorLast-compositorFirst)<<'\n'
               <<metadata.str()<<"STATUS Stream-only runtime verified"<<std::endl;
      if(verifiedTiming) *verifiedTiming=timing;
      end();dlclose(lib);return 0;
    }
    RuntimePreferences saved;
    if(std::filesystem::exists(receipt)) {
      saved=readRuntimePreferences(receipt);
      if(saved.legacy && !restore) throw std::runtime_error("Restore the previous runtime preferences before starting a new session");
    } else if(restore) { end();dlclose(lib);return 0; }
    else {
      saved.home=get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_EnableHomeApp);
      saved.dashboard=get(vr::k_pch_Dashboard_Section,vr::k_pch_Dashboard_EnableDashboard_Bool);
      saved.system=get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_SendSystemButtonToAllApps_Bool);
      saved.quiet=quiet;
      writeRuntimePreferences(receipt,saved);
    }
    applyRuntimePreferences(saved,restore,get,set);
    if(restore) {
      std::filesystem::remove(receipt);
      std::cout<<"STATUS Local SteamVR preferences restored"<<std::endl;
    } else {
      std::cout<<"STATUS Runtime prepared: System button access="
               <<get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_SendSystemButtonToAllApps_Bool)
               <<"; Home="<<get(vr::k_pch_SteamVR_Section,vr::k_pch_SteamVR_EnableHomeApp)
               <<"; dashboard="<<get(vr::k_pch_Dashboard_Section,vr::k_pch_Dashboard_EnableDashboard_Bool)
               <<"; tracking and compositor retained"<<std::endl;
    }
  } catch(const std::exception& e) {std::cerr<<"ERROR "<<e.what()<<std::endl;result=1;}
  end();dlclose(lib);return result;
}
}
