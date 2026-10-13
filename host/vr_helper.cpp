#define OPENVR_API_NODLL
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "chaperone_geometry.hpp"
#undef __WIN32 // Avoid generated legacy C bool typedef in C++; retain _WIN32 ABI.
#include "openvr_capi.h"

static bool matches(VR_IVRChaperoneSetup_FnTable* setup,float w,float d) {
  setup->RevertWorkingCopy();
  HmdMatrix34_t standing{},seated{};HmdQuad_t walls[4]{};uint32_t count=4;
  return vrostbridge::matchingChaperone(setup,w,d)&&
    setup->GetWorkingStandingZeroPoseToRawTrackingPose(&standing)&&
    setup->GetWorkingSeatedZeroPoseToRawTrackingPose(&seated)&&
    setup->GetWorkingCollisionBoundsInfo(walls,&count)&&
    vrostbridge::matchingChaperoneGeometry(w,d,walls,count,standing,seated);
}
static bool visible(VR_IVRSettings_FnTable* settings) {
  EVRSettingsError error=EVRSettingsError_VRSettingsError_None;
  float fade=settings->GetFloat((char*)"collisionBounds",(char*)"CollisionBoundsFadeDistance",&error);
  if(error!=EVRSettingsError_VRSettingsError_None||!isfinite(fade)||fade<=0) {
    settings->SetFloat((char*)"collisionBounds",(char*)"CollisionBoundsFadeDistance",0.7f,&error);
    if(error!=EVRSettingsError_VRSettingsError_None)return false;
    fade=settings->GetFloat((char*)"collisionBounds",(char*)"CollisionBoundsFadeDistance",&error);
  }
  return error==EVRSettingsError_VRSettingsError_None&&isfinite(fade)&&fade>0;
}
// Use the generated C function tables: no compiler-specific C++ vtable ABI.
int wmain(int argc,wchar_t** argv) {
  if(argc!=5) return 2;
  wchar_t *end1,*end2;double w=wcstod(argv[2],&end1),d=wcstod(argv[3],&end2);
  if(*end1||*end2||!isfinite(w)||!isfinite(d)||w<=0||d<=0||w>100||d>100) return 2;
  HMODULE lib=LoadLibraryExW(argv[1],NULL,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
  if(!lib) {fprintf(stderr,"OpenVR DLL load failed: %lu\n",GetLastError());return 1;}
  typedef uint32_t (__cdecl *Init)(EVRInitError*,EVRApplicationType,const char*);
  typedef intptr_t (__cdecl *Get)(const char*,EVRInitError*);
  typedef void (__cdecl *End)();
  auto init=(Init)GetProcAddress(lib,"VR_InitInternal2");auto get=(Get)GetProcAddress(lib,"VR_GetGenericInterface");auto shutdown=(End)GetProcAddress(lib,"VR_ShutdownInternal");
  if(!init||!get||!shutdown) {FreeLibrary(lib);return 1;}
  EVRInitError error=EVRInitError_VRInitError_None;
  init(&error,EVRApplicationType_VRApplication_Background,NULL);
  if(error) {fprintf(stderr,"Host OpenVR background init failed: %d\n",int(error));FreeLibrary(lib);return 1;}
  auto setup=(VR_IVRChaperoneSetup_FnTable*)get("FnTable:IVRChaperoneSetup_006",&error);
  auto settings=(VR_IVRSettings_FnTable*)get("FnTable:IVRSettings_003",&error);
  int result=1;bool changed=false;
  if(setup&&settings) {
    if(matches(setup,float(w),float(d))) {
      // Inspect live data every time: the native driver may have reconnected.
      // No cached acceptance, extra backup, or universe commit for unchanged geometry.
      if(visible(settings))result=0;
    } else {
      uint32_t size=0;setup->ExportLiveToBuffer(NULL,&size);
      if(size>0&&size<=1024*1024) {
        char* data=(char*)malloc(size);
        if(data&&setup->ExportLiveToBuffer(data,&size)) {
          FILE* backup=_wfopen(argv[4],L"wb");
          bool saved=backup&&fwrite(data,1,size,backup)==size;
          if(backup)fclose(backup);
          if(saved) {
            setup->RevertWorkingCopy();
            HmdMatrix34_t identity={{{1,0,0,0},{0,1,0,0},{0,0,1,0}}};
            HmdVector2_t perimeter[4]={{{float(-w/2),float(-d/2)}},{{float(-w/2),float(d/2)}},{{float(w/2),float(d/2)}},{{float(w/2),float(-d/2)}}};
            setup->SetWorkingStandingZeroPoseToRawTrackingPose(&identity);
            setup->SetWorkingSeatedZeroPoseToRawTrackingPose(&identity);
            setup->SetWorkingPlayAreaSize(float(w),float(d));setup->SetWorkingPerimeter(perimeter,4);
            changed=setup->CommitWorkingCopy(EChaperoneConfigFile_Live);
            if(changed&&matches(setup,float(w),float(d))&&visible(settings))result=0;
          }
        }
        free(data);
      }
    }
  }
  if(result)fprintf(stderr,"Host play-area apply/verification failed; previous chaperone backup retained if available.\n");
  else printf("{\"applied\":true,\"changed\":%s,\"width\":%.4f,\"depth\":%.4f,\"boundary\":\"rectangle\"}\n",changed?"true":"false",w,d);
  shutdown();FreeLibrary(lib);return result;
}
