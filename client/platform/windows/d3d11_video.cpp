// Windows hardware decoding (1.0.0.7): SteamVR's GPU as DXGI and Direct3D 11 see it. See d3d11_video.hpp.
#include "client/platform/windows/d3d11_video.hpp"
#include "client/platform/windows/dynamic_library.hpp"
#include <d3d11.h>
#include <dxgi.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>
extern "C" {
#include <libavutil/error.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_d3d11va.h>
}
#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dxguid.lib")

namespace vrost::windows {
namespace {

// Releases a COM interface when it goes out of scope.
template <class T> struct Com {
  T *p = nullptr;
  Com() = default;
  Com(const Com &) = delete;
  Com &operator=(const Com &) = delete;
  ~Com() {
    if (p) p->Release();
  }
  T **put() {
    if (p) p->Release();
    p = nullptr;
    return &p;
  }
  void **putVoid() { return reinterpret_cast<void **>(put()); }
  T *operator->() const { return p; }
};

std::string utf8(const wchar_t *text) {
  const int bytes = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
  if (bytes <= 1) return {};
  std::string result(size_t(bytes - 1), '\0');
  WideCharToMultiByte(CP_UTF8, 0, text, -1, result.data(), bytes, nullptr, nullptr);
  return result;
}

std::string hresult(HRESULT value) {
  char text[16];
  std::snprintf(text, sizeof(text), "0x%08lX", static_cast<unsigned long>(value));
  return text;
}

bool sameLuid(const LUID &a, const uint8_t (&b)[8]) {
  LUID other{};
  std::memcpy(&other, b, sizeof(other));
  return a.LowPart == other.LowPart && a.HighPart == other.HighPart;
}

} // namespace

D3D11Adapter findAdapter(const uint8_t (&luid)[8], bool luidValid) {
  D3D11Adapter result;
  if (!luidValid) {
    result.problem = "the Vulkan driver does not report SteamVR's GPU LUID";
    return result;
  }
  Com<IDXGIFactory1> factory;
  HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), factory.putVoid());
  if (FAILED(hr)) {
    result.problem = "DXGI is unavailable (" + hresult(hr) + ")";
    return result;
  }
  for (UINT i = 0;; ++i) {
    Com<IDXGIAdapter1> adapter;
    if (factory->EnumAdapters1(i, adapter.put()) == DXGI_ERROR_NOT_FOUND) break;
    DXGI_ADAPTER_DESC1 desc{};
    if (FAILED(adapter->GetDesc1(&desc)) || !sameLuid(desc.AdapterLuid, luid)) continue;
    result.found = true;
    result.index = int(i);
    result.name = utf8(desc.Description);
    result.vendor = desc.VendorId;
    result.device = desc.DeviceId;
    Com<ID3D11Device> device;
    hr = D3D11CreateDevice(adapter.p, D3D_DRIVER_TYPE_UNKNOWN, nullptr, D3D11_CREATE_DEVICE_VIDEO_SUPPORT, nullptr, 0,
                           D3D11_SDK_VERSION, device.put(), nullptr, nullptr);
    if (FAILED(hr)) {
      result.problem = "Direct3D 11 with video support is unavailable on " + result.name + " (" + hresult(hr) + ")";
      return result;
    }
    Com<ID3D11VideoDevice> video;
    hr = device->QueryInterface(__uuidof(ID3D11VideoDevice), video.putVoid());
    if (FAILED(hr)) {
      result.problem = result.name + " has no Direct3D 11 video decoder (" + hresult(hr) + ")";
      return result;
    }
    result.video = true;
    auto outputs = [&](const GUID &profile, DXGI_FORMAT format) {
      BOOL supported = FALSE;
      return SUCCEEDED(video->CheckVideoDecoderFormat(&profile, format, &supported)) && supported;
    };
    const UINT count = video->GetVideoDecoderProfileCount();
    for (UINT p = 0; p < count; ++p) {
      GUID profile{};
      if (FAILED(video->GetVideoDecoderProfile(p, &profile))) continue;
      if (profile == D3D11_DECODER_PROFILE_H264_VLD_NOFGT) result.h264 = outputs(profile, DXGI_FORMAT_NV12);
      else if (profile == D3D11_DECODER_PROFILE_HEVC_VLD_MAIN) result.hevc = outputs(profile, DXGI_FORMAT_NV12);
      else if (profile == D3D11_DECODER_PROFILE_HEVC_VLD_MAIN10) result.hevcMain10 = outputs(profile, DXGI_FORMAT_P010);
      else if (profile == D3D11_DECODER_PROFILE_AV1_VLD_PROFILE0) {
        result.av1 = outputs(profile, DXGI_FORMAT_NV12);
        result.av1TenBit = outputs(profile, DXGI_FORMAT_P010);
      }
    }
    return result;
  }
  result.problem = "no DXGI adapter has SteamVR's GPU LUID";
  return result;
}

AVBufferRef *createDecodeDevice(const D3D11Adapter &adapter, const uint8_t (&luid)[8]) {
  if (!adapter.found || !adapter.video)
    throw std::runtime_error("D3D11VA hardware decoding is unavailable: " + adapter.problem +
                             ". Choose CPU decoding in VROSTBridge's settings");
  AVBufferRef *device = nullptr;
  const std::string index = std::to_string(adapter.index);
  const int created = av_hwdevice_ctx_create(&device, AV_HWDEVICE_TYPE_D3D11VA, index.c_str(), nullptr, 0);
  if (created < 0) {
    char message[AV_ERROR_MAX_STRING_SIZE]{};
    av_strerror(created, message, sizeof(message));
    throw std::runtime_error("D3D11VA hardware decoding could not start on " + adapter.name + ": " + message +
                             ". Choose CPU decoding in VROSTBridge's settings");
  }
  // FFmpeg silently takes the default adapter when an index is not found: confirm SteamVR's GPU.
  auto *context = static_cast<AVD3D11VADeviceContext *>(reinterpret_cast<AVHWDeviceContext *>(device->data)->hwctx);
  Com<IDXGIDevice> dxgi;
  Com<IDXGIAdapter> used;
  DXGI_ADAPTER_DESC desc{};
  if (!context->device || FAILED(context->device->QueryInterface(__uuidof(IDXGIDevice), dxgi.putVoid())) ||
      FAILED(dxgi->GetAdapter(used.put())) || FAILED(used->GetDesc(&desc)) || !sameLuid(desc.AdapterLuid, luid)) {
    av_buffer_unref(&device);
    throw std::runtime_error("D3D11VA opened another GPU than SteamVR's " + adapter.name);
  }
  return device;
}

} // namespace vrost::windows
