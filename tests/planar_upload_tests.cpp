// The Windows client's frame upload (client/device/planar_upload.hpp, 1.0.0.7) on any Vulkan device —
// normally Mesa's software rasteriser (lavapipe), also through Wine's winevulkan. Synthetic NV12, P010,
// yuv420p and yuv420p10le frames, with padded rows as FFmpeg's frames have them, are written into a
// host-visible staging buffer by writePlanar, copied into R8 + R8G8 (R16 + R16G16) plane images by
// recordPlanarCopy, and then
//  1. read back: the plane images hold exactly the frame's codes (10-bit codes in the top bits);
//  2. shown through the real fused pass (fused.spv) at an unchanged pose, compared with a CPU model
//     (luma at texel centres, chroma bilinear at its siting, then range and matrix), as
//     tests/fused_shader_tests.cpp checks the pass for frames copied from VA-API;
//  3. converted by convert.spv (the two-pass path), compared with the same model;
//  4. the same picture as NV12 and yuv420p (P010 and yuv420p10le) gives identical images.
// Usage: planar_upload_tests <fused.spv> <convert.spv>. Exit 77 (skip) if no Vulkan device exists.
#include "client/device/conversion.hpp"
#include "client/device/planar_upload.hpp"
#include "core/reaim.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL line %d: %s ", __LINE__, #cond);                                           \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

void vk(VkResult r, const char *what) {
  if (r != VK_SUCCESS) throw std::runtime_error(std::string(what) + " failed: " + std::to_string(r));
}

struct Pass {
  VkPipelineLayout layout = VK_NULL_HANDLE;
  VkPipeline pipeline = VK_NULL_HANDLE;
};

struct Gpu {
  VkInstance instance = VK_NULL_HANDLE;
  VkPhysicalDevice physical = VK_NULL_HANDLE;
  VkDevice device = VK_NULL_HANDLE;
  VkQueue queue = VK_NULL_HANDLE;
  uint32_t family = 0;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkDescriptorSetLayout setLayout = VK_NULL_HANDLE;
  VkDescriptorSetLayout fusedSetLayout = VK_NULL_HANDLE; // 1.0.0.11: also the frame's constants and visible spans
  VkSampler sampler = VK_NULL_HANDLE;
  VkDescriptorPool descriptorPool = VK_NULL_HANDLE;
  Pass fused, convert;
  std::string name;

  uint32_t memoryType(uint32_t bits, VkMemoryPropertyFlags flags) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(physical, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
      if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & flags) == flags) return i;
    throw std::runtime_error("memory type");
  }
  bool open() {
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    if (vkCreateInstance(&ici, nullptr, &instance) != VK_SUCCESS) return false;
    uint32_t count = 0;
    vkEnumeratePhysicalDevices(instance, &count, nullptr);
    if (!count) return false;
    std::vector<VkPhysicalDevice> devices(count);
    vkEnumeratePhysicalDevices(instance, &count, devices.data());
    physical = devices[0];
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(physical, &props);
    name = props.deviceName;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
    std::vector<VkQueueFamilyProperties> families(count);
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, families.data());
    family = UINT32_MAX;
    for (uint32_t i = 0; i < count; ++i)
      if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
        family = i;
        break;
      }
    if (family == UINT32_MAX) return false;
    float priority = 1;
    VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = family;
    qci.queueCount = 1;
    qci.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qci;
    vk(vkCreateDevice(physical, &dci, nullptr, &device), "device");
    vkGetDeviceQueue(device, family, 0, &queue);
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    pci.queueFamilyIndex = family;
    vk(vkCreateCommandPool(device, &pci, nullptr, &pool), "pool");
    // The client's layout: planes 0 and 1 sampled, the output image 2 (both passes).
    VkDescriptorSetLayoutBinding bindings[3]{};
    for (int i = 0; i < 3; ++i) {
      bindings[i].binding = i;
      bindings[i].descriptorCount = 1;
      bindings[i].descriptorType = i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dl.bindingCount = 3;
    dl.pBindings = bindings;
    vk(vkCreateDescriptorSetLayout(device, &dl, nullptr, &setLayout), "set layout");
    // 1.0.0.11: the fused pass's layout (client/platform/linux/vulkan.cpp openFused).
    VkDescriptorSetLayoutBinding fusedBindings[5]{};
    for (int i = 0; i < 5; ++i) {
      fusedBindings[i].binding = i;
      fusedBindings[i].descriptorCount = 1;
      fusedBindings[i].descriptorType = i == 4   ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER
                                        : i == 3 ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER
                                        : i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                                                 : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      fusedBindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    dl.bindingCount = 5;
    dl.pBindings = fusedBindings;
    vk(vkCreateDescriptorSetLayout(device, &dl, nullptr, &fusedSetLayout), "fused set layout");
    VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    vk(vkCreateSampler(device, &si, nullptr, &sampler), "sampler");
    VkDescriptorPoolSize sizes[] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 2}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1},
                                    {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 1}, {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1}};
    VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dp.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
    dp.maxSets = 1;
    dp.poolSizeCount = 4;
    dp.pPoolSizes = sizes;
    vk(vkCreateDescriptorPool(device, &dp, nullptr, &descriptorPool), "descriptor pool");
    return true;
  }
  // pushBytes 0: the fused pass (1.0.0.11: no push constants, its own set layout).
  Pass pass(const std::string &spirvPath, uint32_t pushBytes) {
    Pass p;
    VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, pushBytes};
    VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pl.setLayoutCount = 1;
    pl.pSetLayouts = pushBytes ? &setLayout : &fusedSetLayout;
    pl.pushConstantRangeCount = pushBytes ? 1 : 0;
    pl.pPushConstantRanges = &range;
    vk(vkCreatePipelineLayout(device, &pl, nullptr, &p.layout), "pipeline layout");
    std::ifstream file(spirvPath, std::ios::binary | std::ios::ate);
    if (!file) throw std::runtime_error("shader missing: " + spirvPath);
    const auto bytes = size_t(file.tellg());
    std::vector<uint32_t> code(bytes / 4);
    file.seekg(0);
    file.read(reinterpret_cast<char *>(code.data()), std::streamsize(bytes));
    VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    sm.codeSize = bytes;
    sm.pCode = code.data();
    VkShaderModule module;
    vk(vkCreateShaderModule(device, &sm, nullptr, &module), "shader");
    VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cp.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
    cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    cp.stage.module = module;
    cp.stage.pName = "main";
    cp.layout = p.layout;
    vk(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cp, nullptr, &p.pipeline), "pipeline");
    vkDestroyShaderModule(device, module, nullptr);
    return p;
  }
  struct Buffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void *mapped = nullptr;
    bool coherent = true;
  };
  // A staging buffer chosen as the client chooses it (stagingMemoryType).
  Buffer buffer(VkDeviceSize size, VkBufferUsageFlags usage) {
    Buffer b;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = size;
    bi.usage = usage;
    vk(vkCreateBuffer(device, &bi, nullptr, &b.buffer), "buffer");
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, b.buffer, &req);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = vrost::stagingMemoryType(physical, req.memoryTypeBits, b.coherent);
    vk(vkAllocateMemory(device, &ai, nullptr, &b.memory), "buffer memory");
    vk(vkBindBufferMemory(device, b.buffer, b.memory, 0), "bind buffer");
    vk(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0, &b.mapped), "map");
    return b;
  }
  void flush(const Buffer &b) {
    if (b.coherent) return;
    VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
    range.memory = b.memory;
    range.size = VK_WHOLE_SIZE;
    vk(vkFlushMappedMemoryRanges(device, 1, &range), "flush");
  }
  void invalidate(const Buffer &b) {
    if (b.coherent) return;
    VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
    range.memory = b.memory;
    range.size = VK_WHOLE_SIZE;
    vk(vkInvalidateMappedMemoryRanges(device, 1, &range), "invalidate");
  }
  void free(Buffer &b) {
    vkUnmapMemory(device, b.memory);
    vkDestroyBuffer(device, b.buffer, nullptr);
    vkFreeMemory(device, b.memory, nullptr);
  }
  struct Image {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
  };
  Image image(uint32_t w, uint32_t h, VkFormat format, VkImageUsageFlags usage) {
    Image im;
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D;
    ci.format = format;
    ci.extent = {w, h, 1};
    ci.mipLevels = ci.arrayLayers = 1;
    ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = usage;
    vk(vkCreateImage(device, &ci, nullptr, &im.image), "image");
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(device, im.image, &req);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memoryType(req.memoryTypeBits, 0);
    vk(vkAllocateMemory(device, &ai, nullptr, &im.memory), "image memory");
    vk(vkBindImageMemory(device, im.image, im.memory, 0), "bind image");
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = im.image;
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vk(vkCreateImageView(device, &vi, nullptr, &im.view), "view");
    return im;
  }
  void free(Image &im) {
    vkDestroyImageView(device, im.view, nullptr);
    vkDestroyImage(device, im.image, nullptr);
    vkFreeMemory(device, im.memory, nullptr);
  }
  static void barrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr,
                         0, nullptr, 1, &b);
  }
  template <class Record> void submit(Record record) {
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool;
    ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vk(vkAllocateCommandBuffers(device, &ai, &cmd), "command buffer");
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vk(vkBeginCommandBuffer(cmd, &bi), "begin");
    record(cmd);
    vk(vkEndCommandBuffer(cmd), "end");
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "submit");
    vk(vkQueueWaitIdle(queue), "wait");
    vkFreeCommandBuffers(device, pool, 1, &cmd);
  }
};

// A synthetic decoded frame in one of the four formats, rows padded like FFmpeg's (and the padding
// filled with a marker that must never reach the planes).
struct Frame {
  vrost::PlanarFormat format;
  uint32_t width = 0, height = 0;
  std::vector<std::vector<uint8_t>> planes;
  std::vector<int> strides;
  const uint8_t *data[3]{};
  int linesize[3]{};
};

// Codes of the test picture: smooth and sharp detail in both eyes, every code range used.
uint32_t lumaCode(uint32_t x, uint32_t y, uint32_t maxCode) {
  const double v = .5 + .35 * std::sin(x * .37 + y * .11) * std::cos(y * .23) + .14 * (((x * 7 + y * 13) % 17) / 16.0 - .5);
  return uint32_t(std::lround(std::clamp(v, 0.0, 1.0) * maxCode));
}
uint32_t chromaCode(uint32_t x, uint32_t y, int plane, uint32_t maxCode) {
  const double v = .5 + .4 * std::sin(x * (plane ? .29 : .41) + y * (plane ? .37 : .19) + plane);
  return uint32_t(std::lround(std::clamp(v, 0.0, 1.0) * maxCode));
}

Frame makeFrame(vrost::PlanarFormat format, uint32_t width, uint32_t height) {
  Frame f;
  f.format = format;
  f.width = width;
  f.height = height;
  const bool ten = vrost::planarTenBit(format);
  const uint32_t maxCode = ten ? 1023 : 255, bytes = ten ? 2 : 1;
  const uint32_t cw = (width + 1) / 2, ch = (height + 1) / 2;
  const bool semi = format == vrost::PlanarFormat::Nv12 || format == vrost::PlanarFormat::P010;
  auto store = [&](std::vector<uint8_t> &plane, size_t at, uint32_t code) {
    // P010 keeps the code in the top bits, yuv420p10le in the low bits (both little-endian).
    const uint32_t value = format == vrost::PlanarFormat::P010 ? code << 6 : code;
    if (bytes == 2) {
      plane[at] = uint8_t(value);
      plane[at + 1] = uint8_t(value >> 8);
    } else
      plane[at] = uint8_t(value);
  };
  const int lumaStride = int(width * bytes + 64);
  f.strides.push_back(lumaStride);
  f.planes.emplace_back(size_t(lumaStride) * height, uint8_t(0xAB));
  for (uint32_t y = 0; y < height; ++y)
    for (uint32_t x = 0; x < width; ++x) store(f.planes[0], size_t(y) * lumaStride + x * bytes, lumaCode(x, y, maxCode));
  if (semi) {
    const int stride = int(cw * 2 * bytes + 32);
    f.strides.push_back(stride);
    f.planes.emplace_back(size_t(stride) * ch, uint8_t(0xCD));
    for (uint32_t y = 0; y < ch; ++y)
      for (uint32_t x = 0; x < cw; ++x)
        for (int c = 0; c < 2; ++c) store(f.planes[1], size_t(y) * stride + (x * 2 + c) * bytes, chromaCode(x, y, c, maxCode));
  } else
    for (int c = 0; c < 2; ++c) {
      const int stride = int(cw * bytes + 48);
      f.strides.push_back(stride);
      f.planes.emplace_back(size_t(stride) * ch, uint8_t(0xEF));
      for (uint32_t y = 0; y < ch; ++y)
        for (uint32_t x = 0; x < cw; ++x) store(f.planes.back(), size_t(y) * stride + x * bytes, chromaCode(x, y, c, maxCode));
    }
  for (size_t i = 0; i < f.planes.size(); ++i) {
    f.data[i] = f.planes[i].data();
    f.linesize[i] = f.strides[i];
  }
  return f;
}

const char *formatName(vrost::PlanarFormat format) {
  switch (format) {
  case vrost::PlanarFormat::Nv12: return "NV12";
  case vrost::PlanarFormat::P010: return "P010";
  case vrost::PlanarFormat::Yuv420p: return "yuv420p";
  default: return "yuv420p10le";
  }
}

struct Uploaded {
  Gpu::Image planes[2];
  std::vector<uint8_t> luma, chroma; // read back
};

// writePlanar + recordPlanarCopy, exactly as the client's upload, then the planes read back.
Uploaded upload(Gpu &gpu, const Frame &frame) {
  const bool ten = vrost::planarTenBit(frame.format);
  const auto layout = vrost::planarLayout(frame.width, frame.height, ten);
  Uploaded u;
  for (int i = 0; i < 2; ++i)
    u.planes[i] = gpu.image(i ? layout.chromaWidth : layout.width, i ? layout.chromaHeight : layout.height,
                            vrost::planeFormat(i, ten),
                            VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
  auto staging = gpu.buffer(layout.size, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
  std::memset(staging.mapped, 0x5A, layout.size); // stale contents the upload must overwrite
  vrost::writePlanar(layout, static_cast<uint8_t *>(staging.mapped), frame.format, frame.data, frame.linesize);
  gpu.flush(staging);
  const size_t lumaBytes = layout.lumaPitch * layout.height, chromaBytes = layout.chromaPitch * layout.chromaHeight;
  // The chroma plane read back at a multiple of its texel size (4 bytes covers R8G8 and R16G16), as Vulkan requires.
  const size_t chromaOffset = (lumaBytes + 3) & ~size_t(3);
  auto readback = gpu.buffer(chromaOffset + chromaBytes, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
  const VkImage images[2] = {u.planes[0].image, u.planes[1].image};
  gpu.submit([&](VkCommandBuffer cmd) {
    vrost::recordPlanarCopy(cmd, staging.buffer, layout, images);
    for (int i = 0; i < 2; ++i) {
      Gpu::barrier(cmd, images[i], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
      VkBufferImageCopy region{};
      region.bufferOffset = i ? chromaOffset : 0;
      region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
      region.imageExtent = {i ? layout.chromaWidth : layout.width, i ? layout.chromaHeight : layout.height, 1};
      vkCmdCopyImageToBuffer(cmd, images[i], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &region);
      Gpu::barrier(cmd, images[i], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    }
  });
  gpu.invalidate(readback);
  const auto *bytes = static_cast<const uint8_t *>(readback.mapped);
  u.luma.assign(bytes, bytes + lumaBytes);
  u.chroma.assign(bytes + chromaOffset, bytes + chromaOffset + chromaBytes);
  gpu.free(readback);
  gpu.free(staging);
  return u;
}

// The planes' expected contents: the frame's codes, P010-style for 10-bit.
void expectedPlanes(const Frame &frame, std::vector<uint8_t> &luma, std::vector<uint8_t> &chroma) {
  const bool ten = vrost::planarTenBit(frame.format);
  const uint32_t maxCode = ten ? 1023 : 255, bytes = ten ? 2 : 1;
  const uint32_t cw = (frame.width + 1) / 2, ch = (frame.height + 1) / 2;
  auto put = [&](std::vector<uint8_t> &out, size_t at, uint32_t code) {
    const uint32_t value = ten ? code << 6 : code;
    out[at] = uint8_t(value);
    if (ten) out[at + 1] = uint8_t(value >> 8);
  };
  luma.assign(size_t(frame.width) * frame.height * bytes, 0);
  chroma.assign(size_t(cw) * ch * 2 * bytes, 0);
  for (uint32_t y = 0; y < frame.height; ++y)
    for (uint32_t x = 0; x < frame.width; ++x) put(luma, (size_t(y) * frame.width + x) * bytes, lumaCode(x, y, maxCode));
  for (uint32_t y = 0; y < ch; ++y)
    for (uint32_t x = 0; x < cw; ++x)
      for (int c = 0; c < 2; ++c) put(chroma, ((size_t(y) * cw + x) * 2 + c) * bytes, chromaCode(x, y, c, maxCode));
}

// Runs one frame pass on the uploaded planes into an RGBA8 image of the decoded size: convert with its push
// constants, or (fused set) the fused pass with its frame constants and every pixel visible, as the client runs it.
std::vector<uint8_t> runPass(Gpu &gpu, const Pass &pass, const Uploaded &u, uint32_t w, uint32_t h, const void *constants,
                             uint32_t constantBytes, const vrost::FusedParameters *fused = nullptr) {
  auto output = gpu.image(w, h, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
  auto readback = gpu.buffer(VkDeviceSize(w) * h * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
  VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
  dai.descriptorPool = gpu.descriptorPool;
  dai.descriptorSetCount = 1;
  dai.pSetLayouts = fused ? &gpu.fusedSetLayout : &gpu.setLayout;
  VkDescriptorSet set;
  vk(vkAllocateDescriptorSets(gpu.device, &dai, &set), "descriptor set");
  Gpu::Buffer frameBuffer, spansBuffer;
  if (fused) {
    const auto frame = vrost::fusedFrameConstants(*fused, w, h, w / 2, h);
    frameBuffer = gpu.buffer(sizeof(frame), VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
    std::memcpy(frameBuffer.mapped, &frame, sizeof(frame));
    gpu.flush(frameBuffer);
    const std::vector<uint32_t> spans(size_t(h) * 2, (w / 2) << 16); // every row whole
    spansBuffer = gpu.buffer(spans.size() * sizeof(uint32_t), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    std::memcpy(spansBuffer.mapped, spans.data(), spans.size() * sizeof(uint32_t));
    gpu.flush(spansBuffer);
    VkDescriptorBufferInfo bufferInfos[2] = {{frameBuffer.buffer, 0, sizeof(frame)},
                                             {spansBuffer.buffer, 0, spans.size() * sizeof(uint32_t)}};
    VkWriteDescriptorSet bufferWrites[2]{};
    for (int i = 0; i < 2; ++i) {
      bufferWrites[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      bufferWrites[i].dstSet = set;
      bufferWrites[i].dstBinding = uint32_t(3 + i);
      bufferWrites[i].descriptorCount = 1;
      bufferWrites[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      bufferWrites[i].pBufferInfo = &bufferInfos[i];
    }
    vkUpdateDescriptorSets(gpu.device, 2, bufferWrites, 0, nullptr);
  }
  VkDescriptorImageInfo infos[3] = {{gpu.sampler, u.planes[0].view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                    {gpu.sampler, u.planes[1].view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                    {VK_NULL_HANDLE, output.view, VK_IMAGE_LAYOUT_GENERAL}};
  VkWriteDescriptorSet writes[3]{};
  for (int i = 0; i < 3; ++i) {
    writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    writes[i].dstSet = set;
    writes[i].dstBinding = i;
    writes[i].descriptorCount = 1;
    writes[i].descriptorType = i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    writes[i].pImageInfo = &infos[i];
  }
  vkUpdateDescriptorSets(gpu.device, 3, writes, 0, nullptr);
  gpu.submit([&](VkCommandBuffer cmd) {
    Gpu::barrier(cmd, output.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pass.pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pass.layout, 0, 1, &set, 0, nullptr);
    if (fused) {
      vkCmdDispatch(cmd, (w / 2 + 15) / 16, (h + 15) / 16, 2); // z: the eye
    } else {
      vkCmdPushConstants(cmd, pass.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, constantBytes, constants);
      vkCmdDispatch(cmd, (w + 15) / 16, (h + 15) / 16, 1);
    }
    Gpu::barrier(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
    VkBufferImageCopy out{};
    out.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    out.imageExtent = {w, h, 1};
    vkCmdCopyImageToBuffer(cmd, output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &out);
  });
  gpu.invalidate(readback);
  std::vector<uint8_t> result(size_t(w) * h * 4);
  std::memcpy(result.data(), readback.mapped, result.size());
  vkFreeDescriptorSets(gpu.device, gpu.descriptorPool, 1, &set);
  if (fused) {
    gpu.free(frameBuffer);
    gpu.free(spansBuffer);
  }
  gpu.free(readback);
  gpu.free(output);
  return result;
}

// CPU model of both passes for an unforeshortened, unfoveated frame shown at its decoded size and
// unchanged pose: luma is the texel itself, chroma is filtered bilinearly at its siting within the
// pixel's eye (as fused.comp and convert.comp clamp it), then range and matrix as conversion.hpp.
std::array<float, 3> model(const Frame &frame, const vrost::ConversionPushConstants &pc, uint32_t px, uint32_t py) {
  const bool ten = vrost::planarTenBit(frame.format);
  const uint32_t maxCode = ten ? 1023 : 255;
  auto normalised = [&](uint32_t code) { return ten ? float(code << 6) / 65535.f : float(code) / 255.f; };
  const uint32_t eyeWidth = frame.width / 2, eye = px >= eyeWidth ? 1 : 0;
  const float y = (normalised(lumaCode(px, py, maxCode)) - pc.adjustment[0]) * pc.adjustment[1];
  const float chromaEye = float(frame.width / 2) * .5f, chromaHeight = float((frame.height + 1) / 2);
  const float ax = float(px - eye * eyeWidth) + .5f, ay = float(py) + .5f;
  const float cx = std::clamp((ax + pc.chromaSiting[0]) * .5f, .5f, chromaEye - .5f) + float(eye) * chromaEye;
  const float cy = std::clamp((ay + pc.chromaSiting[1]) * .5f, .5f, chromaHeight - .5f);
  auto chroma = [&](int c) {
    const float fx = cx - .5f, fy = cy - .5f;
    const int x0 = int(std::floor(fx)), y0 = int(std::floor(fy));
    const float wx = fx - float(x0), wy = fy - float(y0);
    auto at = [&](int x, int yy) {
      x = std::clamp(x, 0, int((frame.width + 1) / 2) - 1);
      yy = std::clamp(yy, 0, int(chromaHeight) - 1);
      return normalised(chromaCode(uint32_t(x), uint32_t(yy), c, maxCode));
    };
    return (at(x0, y0) * (1 - wx) + at(x0 + 1, y0) * wx) * (1 - wy) + (at(x0, y0 + 1) * (1 - wx) + at(x0 + 1, y0 + 1) * wx) * wy;
  };
  const float cb = (chroma(0) - pc.row0[3]) * pc.adjustment[2], cr = (chroma(1) - pc.row0[3]) * pc.adjustment[2];
  const float *rows[3] = {pc.row0, pc.row1, pc.row2};
  std::array<float, 3> rgb{};
  for (int i = 0; i < 3; ++i) rgb[i] = std::clamp(rows[i][0] * y + rows[i][1] * cb + rows[i][2] * cr, 0.f, 1.f);
  return rgb;
}

double compareModel(const Frame &frame, const vrost::ConversionPushConstants &pc, const std::vector<uint8_t> &image) {
  double worst = 0;
  for (uint32_t y = 0; y < frame.height; ++y)
    for (uint32_t x = 0; x < frame.width; ++x) {
      const auto rgb = model(frame, pc, x, y);
      for (int c = 0; c < 3; ++c)
        worst = std::max(worst, std::fabs(double(image[(size_t(y) * frame.width + x) * 4 + c]) - rgb[c] * 255.0));
    }
  return worst;
}

std::array<VrostView, 2> eyes() {
  std::array<VrostView, 2> v{};
  for (auto &e : v) {
    e.pose.orientation = {0, 0, 0, 1};
    e.left = -.785398f;
    e.right = .785398f;
    e.up = .785398f;
    e.down = -.785398f;
  }
  v[0].pose.position[0] = -.032f;
  v[1].pose.position[0] = .032f;
  return v;
}
} // namespace

int main(int argc, char **argv) {
  if (argc < 3) {
    std::printf("usage: planar_upload_tests fused.spv convert.spv\n");
    return 2;
  }
  // The layout without a GPU: aligned chroma, odd sizes rounded up, 16-bit samples for P010.
  {
    const auto l = vrost::planarLayout(4032, 2240, false);
    CHECK(l.lumaPitch == 4032 && l.chromaPitch == 4032 && l.chromaOffset == 4032u * 2240 && l.chromaOffset % 256 == 0 &&
              l.size == l.chromaOffset + 4032u * 1120,
          "NV12 layout");
    const auto t = vrost::planarLayout(1921, 1081, true);
    CHECK(t.chromaWidth == 961 && t.chromaHeight == 541 && t.lumaPitch == 3842 && t.chromaPitch == 3844 &&
              t.chromaOffset % 256 == 0 && t.chromaOffset >= t.lumaPitch * 1081,
          "odd P010 layout");
    bool rejected = false;
    try {
      vrost::planarLayout(0, 16, false);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    CHECK(rejected, "an empty frame is refused");
    rejected = false;
    std::vector<uint8_t> buffer(t.size);
    const auto frame = makeFrame(vrost::PlanarFormat::Yuv420p, 1921, 1081);
    try {
      vrost::writePlanar(t, buffer.data(), frame.format, frame.data, frame.linesize);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    CHECK(rejected, "an 8-bit frame is refused for a 10-bit layout");
  }
  Gpu gpu;
  try {
    if (!gpu.open()) {
      std::printf("SKIP: no Vulkan device\n");
      return 77;
    }
  } catch (const std::exception &e) {
    std::printf("SKIP: Vulkan unavailable (%s)\n", e.what());
    return 77;
  }
  std::printf("Vulkan device: %s\n", gpu.name.c_str());
  try {
    const bool fusedPass = true; // 1.0.0.11: one fused shader for every GPU
    gpu.fused = gpu.pass(argv[1], 0);
    gpu.convert = gpu.pass(argv[2], sizeof(vrost::ConversionPushConstants));
    // 1. Plane contents, including odd sizes (chroma rounded up) and padded source rows.
    for (const auto format : {vrost::PlanarFormat::Nv12, vrost::PlanarFormat::P010, vrost::PlanarFormat::Yuv420p,
                              vrost::PlanarFormat::Yuv420p10})
      for (const auto size : {std::array<uint32_t, 2>{192, 80}, std::array<uint32_t, 2>{191, 77}}) {
        const Frame frame = makeFrame(format, size[0], size[1]);
        Uploaded u = upload(gpu, frame);
        std::vector<uint8_t> luma, chroma;
        expectedPlanes(frame, luma, chroma);
        CHECK(u.luma == luma, "%s %ux%u luma plane differs", formatName(format), size[0], size[1]);
        CHECK(u.chroma == chroma, "%s %ux%u chroma plane differs", formatName(format), size[0], size[1]);
        std::printf("%-12s %ux%u planes %s\n", formatName(format), size[0], size[1],
                    u.luma == luma && u.chroma == chroma ? "exact" : "DIFFERENT");
        for (auto &plane : u.planes) gpu.free(plane);
      }
    // 2-4. Both frame passes on the uploaded planes against the model; semi-planar = planar.
    const vrost::Pose still{};
    for (const bool ten : {false, true})
      for (const bool fullRange : {true, false}) {
        const auto semi = ten ? vrost::PlanarFormat::P010 : vrost::PlanarFormat::Nv12;
        const auto planar = ten ? vrost::PlanarFormat::Yuv420p10 : vrost::PlanarFormat::Yuv420p;
        const uint32_t w = 192, h = 80;
        const auto conversion = vrost::conversionPushConstants(
            fullRange ? vrost::conversionMatrix(.2126f, .0722f) : vrost::conversionMatrix(.299f, .114f),
            vrost::rangeConstants(ten ? 10 : 8, fullRange, ten), vrost::chromaSiting(fullRange ? 0 : 2), 0.f,
            vrost::FoveationParams{});
        const auto fusedConstants =
            vrost::fusedParameters(conversion, vrost::reaimConstants(still, still, eyes()).rows, 0.f);
        std::vector<uint8_t> images[2][2]; // [semi, planar][fused, convert]
        for (int source = 0; source < 2; ++source) {
          const Frame frame = makeFrame(source ? planar : semi, w, h);
          Uploaded u = upload(gpu, frame);
          if (fusedPass) images[source][0] = runPass(gpu, gpu.fused, u, w, h, nullptr, 0, &fusedConstants);
          images[source][1] = runPass(gpu, gpu.convert, u, w, h, &conversion, sizeof(conversion));
          const double tolerance = ten ? 1.0 : 4.5; // as fused_shader_tests' identity case
          for (int passIndex = fusedPass ? 0 : 1; passIndex < 2; ++passIndex) {
            const double worst = compareModel(frame, conversion, images[source][passIndex]);
            std::printf("%-12s %s range, %-7s pass: model max %.2f\n", formatName(frame.format),
                        fullRange ? "full" : "limited", passIndex ? "convert" : "fused", worst);
            CHECK(worst <= tolerance, "%s %s pass differs from the model by %.2f", formatName(frame.format),
                  passIndex ? "convert" : "fused", worst);
          }
          for (auto &plane : u.planes) gpu.free(plane);
        }
        for (int passIndex = fusedPass ? 0 : 1; passIndex < 2; ++passIndex)
          CHECK(images[0][passIndex] == images[1][passIndex], "%s and %s give different %s images", formatName(semi),
                formatName(planar), passIndex ? "convert" : "fused");
      }
  } catch (const std::exception &e) {
    std::printf("FAIL: %s\n", e.what());
    return 1;
  }
  if (failures) {
    std::printf("%d planar upload check(s) failed\n", failures);
    return 1;
  }
  std::printf("Planar upload contracts passed\n");
  return 0;
}
