#pragma once
#include "openvr.h"
#include "client/device/runtime_readiness.hpp"
#include <cmath>
#include <ostream>
#include <string>

namespace vrost {
inline std::string jsonString(const std::string &text) {
  std::string out="\"";
  const char *hex="0123456789abcdef";
  for (unsigned char c:text) {
    if(c=='"' || c=='\\') {out+='\\';out+=char(c);}
    else if(c<32) {out+="\\u00";out+=hex[c>>4];out+=hex[c&15];}
    else out+=char(c);
  }
  return out+'"';
}
inline bool printHeadsetMetadata(vr::IVRSystem *system,std::ostream &out,const VerifiedDisplayTiming* timing=nullptr) {
  if(!system || !system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd)) return false;
  uint32_t width=0,height=0;
  system->GetRecommendedRenderTargetSize(&width,&height);
  vr::ETrackedPropertyError error{};
  float hz=system->GetFloatTrackedDeviceProperty(0,vr::Prop_DisplayFrequency_Float,&error);
  if(error || !std::isfinite(hz) || hz<30 || hz>240 || width<32 || width>8192 || height<32 || height>8192) return false;
  auto property=[&](vr::ETrackedDeviceProperty key) {
    char text[193]{};vr::ETrackedPropertyError pe{};
    system->GetStringTrackedDeviceProperty(0,key,text,sizeof(text),&pe);
    return pe==vr::TrackedProp_Success ? std::string(text) : std::string{};
  };
  const auto model=property(vr::Prop_ModelNumber_String);
  if(model.empty()) return false;
  if(timing) {
    if(!timing->matches(model,hz)) return false;
    hz=timing->effectiveHz;
  }
  out<<"HEADSET {\"model\":"<<jsonString(model)
     <<",\"manufacturer\":"<<jsonString(property(vr::Prop_ManufacturerName_String))
     <<",\"tracking_system\":"<<jsonString(property(vr::Prop_TrackingSystemName_String))
     <<",\"serial\":"<<jsonString(property(vr::Prop_SerialNumber_String))
     <<",\"render_model\":"<<jsonString(property(vr::Prop_RenderModelName_String))
     <<",\"registered_type\":"<<jsonString(property(vr::Prop_RegisteredDeviceType_String))
     <<",\"input_profile\":"<<jsonString(property(vr::Prop_InputProfilePath_String))
     <<",\"icon_ready\":"<<jsonString(property(vr::Prop_NamedIconPathDeviceReady_String))
     <<",\"icon_off\":"<<jsonString(property(vr::Prop_NamedIconPathDeviceOff_String))
     <<",\"icon_searching\":"<<jsonString(property(vr::Prop_NamedIconPathDeviceSearching_String))
     <<",\"icon_alert\":"<<jsonString(property(vr::Prop_NamedIconPathDeviceReadyAlert_String))
     <<",\"width\":"<<width<<",\"height\":"<<height<<",\"refresh_hz\":"<<hz<<"}\n";
  return true;
}
}
