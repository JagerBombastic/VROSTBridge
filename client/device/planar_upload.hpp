#pragma once
// RC4.3's fused design on the Windows client (1.0.0.7): a decoded 4:2:0 frame reaches a surface's own
// plane images (R8 + R8G8, or R16 + R16G16 for P010) through that surface's persistently mapped,
// host-visible staging buffer, instead of a GPU copy from a mapped VA-API frame as on Linux:
//  - a D3D11VA frame is downloaded into the buffer by FFmpeg (av_hwframe_transfer_data writes its
//    NV12 or P010 rows straight into it, client/platform/windows/vulkan_upload.cpp);
//  - a CPU-decoded frame is written into it here (writePlanar): yuv420p becomes NV12 (Cb and Cr
//    interleaved), yuv420p10le becomes P010 (each 10-bit code shifted into the top bits);
//  - then one command buffer copies the two planes into the images (recordPlanarCopy), which the
//    fused pass (or, with two passes, convert.comp) samples exactly as it samples the Linux copies.
// Platform-neutral (no Windows API): tests/planar_upload_tests.cpp checks it on any Vulkan device.
#include <vulkan/vulkan.h>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>

namespace vrost {

// What a decoder hands over: semi-planar NV12/P010 (FFmpeg's D3D11VA download formats) or the
// planar formats of FFmpeg's software decoders (yuvj420p, the full-range label, is yuv420p here).
enum class PlanarFormat { Nv12, P010, Yuv420p, Yuv420p10 };
inline bool planarTenBit(PlanarFormat format) { return format == PlanarFormat::P010 || format == PlanarFormat::Yuv420p10; }

// Where the planes lie in a staging buffer: `height` luma rows of `width` samples, then
// `chromaHeight` rows of `chromaWidth` CbCr pairs, each plane tightly packed (bufferRowLength 0)
// and the chroma plane at an offset aligned for the copy. Samples are bytes (NV12) or 16-bit
// little-endian words with the 10-bit code in the top bits (P010), as the plane images hold them.
struct PlanarLayout {
  uint32_t width = 0, height = 0, chromaWidth = 0, chromaHeight = 0;
  uint32_t sampleBytes = 1;
  size_t lumaPitch = 0, chromaPitch = 0, chromaOffset = 0, size = 0;
  bool tenBit() const { return sampleBytes == 2; }
};

inline PlanarLayout planarLayout(uint32_t width, uint32_t height, bool tenBit) {
  if (!width || !height || width > 16384 || height > 16384)
    throw std::runtime_error("Unsupported decoded frame size for the planar upload");
  PlanarLayout layout;
  layout.width = width;
  layout.height = height;
  layout.chromaWidth = (width + 1) / 2;
  layout.chromaHeight = (height + 1) / 2;
  layout.sampleBytes = tenBit ? 2 : 1;
  layout.lumaPitch = size_t(width) * layout.sampleBytes;
  layout.chromaPitch = size_t(layout.chromaWidth) * 2 * layout.sampleBytes;
  constexpr size_t alignment = 256; // a multiple of every texel size and of optimalBufferCopyOffsetAlignment
  layout.chromaOffset = (layout.lumaPitch * height + alignment - 1) / alignment * alignment;
  layout.size = layout.chromaOffset + layout.chromaPitch * layout.chromaHeight;
  return layout;
}

// Writes one decoded frame into `staging` (at least layout.size bytes) in the layout. `planes` and
// `strides` are FFmpeg's AVFrame data/linesize: luma and interleaved CbCr for NV12/P010; Y, Cb and
// Cr for yuv420p/yuv420p10le. The frame's size and depth must be the layout's.
inline void writePlanar(const PlanarLayout &layout, uint8_t *staging, PlanarFormat format,
                        const uint8_t *const planes[], const int strides[]) {
  if (planarTenBit(format) != layout.tenBit())
    throw std::runtime_error("Decoded frame depth does not match the planar upload");
  uint8_t *chroma = staging + layout.chromaOffset;
  switch (format) {
  case PlanarFormat::Nv12:
  case PlanarFormat::P010:
    for (uint32_t y = 0; y < layout.height; ++y)
      std::memcpy(staging + y * layout.lumaPitch, planes[0] + std::ptrdiff_t(y) * strides[0], layout.lumaPitch);
    for (uint32_t y = 0; y < layout.chromaHeight; ++y)
      std::memcpy(chroma + y * layout.chromaPitch, planes[1] + std::ptrdiff_t(y) * strides[1], layout.chromaPitch);
    return;
  case PlanarFormat::Yuv420p:
    for (uint32_t y = 0; y < layout.height; ++y)
      std::memcpy(staging + y * layout.lumaPitch, planes[0] + std::ptrdiff_t(y) * strides[0], layout.lumaPitch);
    for (uint32_t y = 0; y < layout.chromaHeight; ++y) {
      const uint8_t *cb = planes[1] + std::ptrdiff_t(y) * strides[1], *cr = planes[2] + std::ptrdiff_t(y) * strides[2];
      uint8_t *row = chroma + y * layout.chromaPitch;
      for (uint32_t x = 0; x < layout.chromaWidth; ++x) {
        row[2 * x] = cb[x];
        row[2 * x + 1] = cr[x];
      }
    }
    return;
  case PlanarFormat::Yuv420p10: {
    // 10-bit codes in the low bits of little-endian words -> P010's top bits (the plane images are
    // UNORM16: a code reads as code * 64 / 65535, which rangeConstants(10, ..., true) expects).
    auto word = [](const uint8_t *at) { return uint16_t(at[0] | at[1] << 8); };
    auto put = [](uint8_t *at, uint16_t code) {
      const uint16_t value = uint16_t((code & 0x3ff) << 6);
      at[0] = uint8_t(value);
      at[1] = uint8_t(value >> 8);
    };
    for (uint32_t y = 0; y < layout.height; ++y) {
      const uint8_t *in = planes[0] + std::ptrdiff_t(y) * strides[0];
      uint8_t *row = staging + y * layout.lumaPitch;
      for (uint32_t x = 0; x < layout.width; ++x) put(row + 2 * x, word(in + 2 * x));
    }
    for (uint32_t y = 0; y < layout.chromaHeight; ++y) {
      const uint8_t *cb = planes[1] + std::ptrdiff_t(y) * strides[1], *cr = planes[2] + std::ptrdiff_t(y) * strides[2];
      uint8_t *row = chroma + y * layout.chromaPitch;
      for (uint32_t x = 0; x < layout.chromaWidth; ++x) {
        put(row + 4 * x, word(cb + 2 * x));
        put(row + 4 * x + 2, word(cr + 2 * x));
      }
    }
    return;
  }
  }
}

// The two plane images' formats for a depth.
inline VkFormat planeFormat(int plane, bool tenBit) {
  return tenBit ? (plane ? VK_FORMAT_R16G16_UNORM : VK_FORMAT_R16_UNORM) : (plane ? VK_FORMAT_R8G8_UNORM : VK_FORMAT_R8_UNORM);
}

// Records the staging buffer -> plane images copies and leaves both images ready to be sampled
// (SHADER_READ_ONLY_OPTIMAL). Their previous contents are discarded: the caller has waited for
// every earlier reader (the surface's fences). The host's writes need no barrier: submitting the
// command buffer makes them visible (coherent memory, or memory the caller has flushed).
inline void recordPlanarCopy(VkCommandBuffer cmd, VkBuffer staging, const PlanarLayout &layout, const VkImage planes[2]) {
  auto barrier = [&](VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags fromAccess, VkAccessFlags toAccess) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = fromAccess;
    b.dstAccessMask = toAccess;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0,
                         nullptr, 1, &b);
  };
  for (int i = 0; i < 2; ++i) {
    barrier(planes[i], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT);
    VkBufferImageCopy region{};
    region.bufferOffset = i ? VkDeviceSize(layout.chromaOffset) : 0;
    region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.imageExtent = {i ? layout.chromaWidth : layout.width, i ? layout.chromaHeight : layout.height, 1};
    vkCmdCopyBufferToImage(cmd, staging, planes[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
    barrier(planes[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
            VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT);
  }
}

// The staging buffer's memory type: host-visible and coherent, preferably system memory (not the
// device-local window, which is small without resizable BAR); else any host-visible type, which the
// caller then flushes after writing (`coherent` false). Throws if there is none.
inline uint32_t stagingMemoryType(VkPhysicalDevice physical, uint32_t allowed, bool &coherent) {
  VkPhysicalDeviceMemoryProperties props;
  vkGetPhysicalDeviceMemoryProperties(physical, &props);
  const VkMemoryPropertyFlags visible = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT,
                              wanted = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
  for (int pass = 0; pass < 3; ++pass)
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
      const VkMemoryPropertyFlags flags = props.memoryTypes[i].propertyFlags;
      if (!(allowed & (1u << i))) continue;
      const bool match = pass == 0 ? (flags & wanted) == wanted && !(flags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)
                         : pass == 1 ? (flags & wanted) == wanted
                                     : (flags & visible) == visible;
      if (match) {
        coherent = (flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) != 0;
        return i;
      }
    }
  throw std::runtime_error("No host-visible Vulkan memory for the frame upload");
}

} // namespace vrost
