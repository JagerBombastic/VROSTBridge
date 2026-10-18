#pragma once
// 1.0.0.14: which codecs this GPU's VA-API decoder handles, reported once at client start as the Windows client
// reports its D3D11 decoder profiles, so the companion can choose Auto codec for the next session from what the
// decoder really has (HEVC 10-bit for everyone with it, H.264 only without) and the report can say.
// Linux only; opens the render node SteamVR's GPU exposes, asks libva, closes it again. Never throws.
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <vector>
#include <va/va.h>
#include <va/va_drm.h>

namespace vrost {

struct VaapiProfiles {
  bool queried = false;
  bool h264 = false, hevc = false, hevcMain10 = false, av1 = false, av1TenBit = false;
  std::string problem;
};

inline VaapiProfiles vaapiDecoderProfiles(const std::string &renderNode) {
  VaapiProfiles result;
  if (renderNode.empty()) {
    result.problem = "no render node";
    return result;
  }
  const int fd = ::open(renderNode.c_str(), O_RDWR | O_CLOEXEC);
  if (fd < 0) {
    result.problem = "cannot open " + renderNode;
    return result;
  }
  VADisplay display = vaGetDisplayDRM(fd);
  int major = 0, minor = 0;
  if (!display || vaInitialize(display, &major, &minor) != VA_STATUS_SUCCESS) {
    result.problem = "VA-API did not initialise on " + renderNode;
    ::close(fd);
    return result;
  }
  int count = vaMaxNumProfiles(display);
  std::vector<VAProfile> profiles(count > 0 ? count : 0);
  if (count > 0 && vaQueryConfigProfiles(display, profiles.data(), &count) == VA_STATUS_SUCCESS) {
    profiles.resize(count);
    auto decodes = [&](VAProfile profile) {
      int entries = vaMaxNumEntrypoints(display);
      std::vector<VAEntrypoint> entrypoints(entries > 0 ? entries : 0);
      if (entries <= 0 || vaQueryConfigEntrypoints(display, profile, entrypoints.data(), &entries) != VA_STATUS_SUCCESS)
        return false;
      for (int i = 0; i < entries; ++i)
        if (entrypoints[i] == VAEntrypointVLD) return true;
      return false;
    };
    auto tenBit = [&](VAProfile profile) {
      VAConfigAttrib attribute{};
      attribute.type = VAConfigAttribRTFormat;
      if (vaGetConfigAttributes(display, profile, VAEntrypointVLD, &attribute, 1) != VA_STATUS_SUCCESS) return false;
      return (attribute.value & VA_RT_FORMAT_YUV420_10) != 0;
    };
    for (VAProfile profile : profiles) {
      switch (profile) {
      case VAProfileH264Main:
      case VAProfileH264High:
      case VAProfileH264ConstrainedBaseline:
        result.h264 = result.h264 || decodes(profile);
        break;
      case VAProfileHEVCMain:
        result.hevc = result.hevc || decodes(profile);
        break;
      case VAProfileHEVCMain10:
        result.hevcMain10 = result.hevcMain10 || decodes(profile);
        break;
#ifdef VA_CHECK_VERSION
#if VA_CHECK_VERSION(1, 8, 0)
      case VAProfileAV1Profile0:
        if (decodes(profile)) {
          result.av1 = true;
          result.av1TenBit = result.av1TenBit || tenBit(profile);
        }
        break;
#endif
#endif
      default:
        break;
      }
    }
    result.queried = true;
  } else {
    result.problem = "VA-API lists no profiles on " + renderNode;
  }
  vaTerminate(display);
  ::close(fd);
  return result;
}

} // namespace vrost
