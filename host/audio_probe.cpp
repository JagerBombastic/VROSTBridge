// Read-only Windows Core Audio enumeration. Never creates/installs an audio device.
#define INITGUID
#include <windows.h>
#include <mmdeviceapi.h>
#include <functiondiscoverykeys_devpkey.h>
#include <stdio.h>
#include <stdlib.h>
static bool json_name(const wchar_t* value) {
  int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value,-1,NULL,0,NULL,NULL);
  if(n<=0||n>32768)return false;
  char* utf8=(char*)malloc(n);if(!utf8)return false;
  if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value,-1,utf8,n,NULL,NULL)){free(utf8);return false;}
  putchar('"');
  for(int i=0;i<n-1;i++){unsigned char ch=utf8[i];if(ch=='"'||ch=='\\'){putchar('\\');putchar(ch);}else if(ch<32)printf("\\u%04x",ch);else putchar(ch);}
  putchar('"');free(utf8);return true;
}
static bool devices(IMMDeviceEnumerator* enumerator,EDataFlow flow) {
  IMMDeviceCollection* collection=NULL;
  HRESULT hr=enumerator->EnumAudioEndpoints(flow,DEVICE_STATE_ACTIVE,&collection);
  if(FAILED(hr)||!collection)return false;
  UINT count=0;hr=collection->GetCount(&count);bool ok=SUCCEEDED(hr)&&count<=256;
  putchar('[');bool first=true;
  for(UINT i=0;ok&&i<count;i++) {
    IMMDevice* device=NULL;IPropertyStore* props=NULL;PROPVARIANT name={};
    hr=collection->Item(i,&device);
    if(SUCCEEDED(hr))hr=device->OpenPropertyStore(STGM_READ,&props);
    if(SUCCEEDED(hr))hr=props->GetValue(PKEY_Device_FriendlyName,&name);
    if(SUCCEEDED(hr)&&name.vt==VT_LPWSTR&&name.pwszVal) {
      if(!first)putchar(',');first=false;ok=json_name(name.pwszVal);
    } else ok=false;
    PropVariantClear(&name);if(props)props->Release();if(device)device->Release();
  }
  putchar(']');collection->Release();return ok;
}
int main() {
  HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);if(FAILED(hr))return 1;
  IMMDeviceEnumerator* enumerator=NULL;
  hr=CoCreateInstance(CLSID_MMDeviceEnumerator,NULL,CLSCTX_INPROC_SERVER,IID_IMMDeviceEnumerator,(void**)&enumerator);
  bool ok=SUCCEEDED(hr)&&enumerator;
  if(ok){printf("{\"render\":");ok=devices(enumerator,eRender);printf(",\"capture\":");ok=devices(enumerator,eCapture)&&ok;printf("}\n");enumerator->Release();}
  CoUninitialize();return ok?0:1;
}
