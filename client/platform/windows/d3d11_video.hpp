#pragma once
// Windows hardware decoding (1.0.0.7): FFmpeg's D3D11VA decoder on the GPU SteamVR renders with.
// SteamVR's Vulkan device names its GPU by LUID (VkPhysicalDeviceIDProperties::deviceLUID); DXGI lists
// the same GPU under that LUID. The adapter's D3D11 video decoder profiles decide what the client
// offers the host (AV1, 10-bit). No Direct3D header is needed to use this one.
#include <cstdint>
#include <string>
extern "C" {
struct AVBufferRef;
}

namespace vrost::windows {

struct D3D11Adapter {
  bool found = false;             // a DXGI adapter has SteamVR's LUID
  bool video = false;             // and a D3D11 video device could be created on it
  int index = -1;                 // its DXGI adapter index (FFmpeg's D3D11VA device string)
  std::string name;               // DXGI_ADAPTER_DESC1::Description
  uint32_t vendor = 0, device = 0;
  // D3D11 video decoder profiles (with their output formats): H.264, HEVC Main (NV12),
  // HEVC Main10 (P010), AV1 Main (NV12, and P010 for 10-bit streams).
  bool h264 = false, hevc = false, hevcMain10 = false, av1 = false, av1TenBit = false;
  std::string problem;            // why the adapter or its video decoder is unavailable
};

// The DXGI adapter with this LUID and its decoder profiles. `luidValid` false (a Vulkan driver
// that does not report one) leaves `found` false with the reason in `problem`.
D3D11Adapter findAdapter(const uint8_t (&luid)[8], bool luidValid);

// FFmpeg's D3D11VA device on that adapter, checked to be on the same GPU (its LUID). Throws with a
// message naming D3D11VA and the adapter when it cannot be created.
AVBufferRef *createDecodeDevice(const D3D11Adapter &adapter, const uint8_t (&luid)[8]);

} // namespace vrost::windows
