// Runs the real conversion shader (client/platform/linux/convert.comp) on a Vulkan
// device — normally Mesa's software rasteriser (lavapipe) in CI — and compares it with
//  1. the CPU reference in client/device/conversion.hpp, pixel by pixel, and
//  2. for foveated streams, the original full-resolution image before ALVR's host
//     compression (CompressAxisAlignedPixelShader.hlsl, ported in conversion.hpp).
// Usage: convert_shader_tests <convert.spv>. Exit 77 (skip) if no Vulkan device exists.
// The 10-bit cases are the strict ones: lavapipe filters 8-bit textures in fixed point.
#include "client/device/conversion.hpp"
#include <vulkan/vulkan.h>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL %s:%d: %s ", __FILE__, __LINE__, #cond);                                   \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

void vk(VkResult r, const char *what) {
  if (r != VK_SUCCESS) throw std::runtime_error(std::string(what) + " failed: " + std::to_string(r));
}

struct Gpu {
  VkInstance instance = VK_NULL_HANDLE;
  VkPhysicalDevice physical = VK_NULL_HANDLE;
  VkDevice device = VK_NULL_HANDLE;
  VkQueue queue = VK_NULL_HANDLE;
  uint32_t family = 0;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkDescriptorSetLayout setLayout = VK_NULL_HANDLE;
  VkPipelineLayout pipelineLayout = VK_NULL_HANDLE;
  VkPipeline pipeline = VK_NULL_HANDLE;
  VkSampler sampler = VK_NULL_HANDLE;
  VkDescriptorPool descriptorPool = VK_NULL_HANDLE;
  std::string name;

  uint32_t memoryType(uint32_t bits, VkMemoryPropertyFlags flags) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(physical, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
      if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & flags) == flags) return i;
    throw std::runtime_error("memory type");
  }

  bool open(const std::string &spirvPath) {
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
    if (props.limits.maxPushConstantsSize < 128) throw std::runtime_error("push constants < 128");
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
    VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(vrost::ConversionPushConstants)};
    VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pl.setLayoutCount = 1;
    pl.pSetLayouts = &setLayout;
    pl.pushConstantRangeCount = 1;
    pl.pPushConstantRanges = &range;
    vk(vkCreatePipelineLayout(device, &pl, nullptr, &pipelineLayout), "pipeline layout");
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
    cp.layout = pipelineLayout;
    vk(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cp, nullptr, &pipeline), "pipeline");
    vkDestroyShaderModule(device, module, nullptr);
    VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    vk(vkCreateSampler(device, &si, nullptr, &sampler), "sampler");
    VkDescriptorPoolSize sizes[] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 2},
                                    {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1}};
    VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dp.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
    dp.maxSets = 1;
    dp.poolSizeCount = 2;
    dp.pPoolSizes = sizes;
    vk(vkCreateDescriptorPool(device, &dp, nullptr, &descriptorPool), "descriptor pool");
    return true;
  }

  struct Buffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void *mapped = nullptr;
  };
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
    ai.memoryTypeIndex = memoryType(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                                            VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    vk(vkAllocateMemory(device, &ai, nullptr, &b.memory), "buffer memory");
    vk(vkBindBufferMemory(device, b.buffer, b.memory, 0), "bind buffer");
    vk(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0, &b.mapped), "map");
    return b;
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
  void submit(const std::function<void(VkCommandBuffer)> &record) {
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool;
    ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vk(vkAllocateCommandBuffers(device, &ai, &cmd), "command buffer");
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vkBeginCommandBuffer(cmd, &bi);
    record(cmd);
    vkEndCommandBuffer(cmd);
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "submit");
    vk(vkQueueWaitIdle(queue), "wait");
    vkFreeCommandBuffers(device, pool, 1, &cmd);
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
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0,
                         nullptr, 0, nullptr, 1, &b);
  }

  // Converts planes (luma: w x h, chroma: w/2 x h/2, interleaved CbCr) into an RGBA8 output.
  std::vector<uint8_t> convert(const std::vector<uint16_t> &luma, const std::vector<uint16_t> &chroma, uint32_t w,
                               uint32_t h, bool sixteen, uint32_t outW, uint32_t outH,
                               const vrost::ConversionPushConstants &pc) {
    const VkFormat lumaFormat = sixteen ? VK_FORMAT_R16_UNORM : VK_FORMAT_R8_UNORM;
    const VkFormat chromaFormat = sixteen ? VK_FORMAT_R16G16_UNORM : VK_FORMAT_R8G8_UNORM;
    const size_t bytes = sixteen ? 2 : 1;
    auto lumaImage = image(w, h, lumaFormat, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    auto chromaImage = image(w / 2, h / 2, chromaFormat, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    auto output = image(outW, outH, VK_FORMAT_R8G8B8A8_UNORM,
                        VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
    const VkDeviceSize lumaBytes = VkDeviceSize(w) * h * bytes, chromaBytes = VkDeviceSize(w / 2) * (h / 2) * 2 * bytes;
    auto staging = buffer(lumaBytes + chromaBytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
    auto readback = buffer(VkDeviceSize(outW) * outH * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    auto *dst = static_cast<uint8_t *>(staging.mapped);
    for (size_t i = 0; i < luma.size(); ++i) {
      if (sixteen) std::memcpy(dst + i * 2, &luma[i], 2);
      else dst[i] = uint8_t(luma[i]);
    }
    for (size_t i = 0; i < chroma.size(); ++i) {
      if (sixteen) std::memcpy(dst + lumaBytes + i * 2, &chroma[i], 2);
      else dst[lumaBytes + i] = uint8_t(chroma[i]);
    }
    VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dai.descriptorPool = descriptorPool;
    dai.descriptorSetCount = 1;
    dai.pSetLayouts = &setLayout;
    VkDescriptorSet set;
    vk(vkAllocateDescriptorSets(device, &dai, &set), "descriptor set");
    VkDescriptorImageInfo infos[3] = {{sampler, lumaImage.view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                      {sampler, chromaImage.view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
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
    vkUpdateDescriptorSets(device, 3, writes, 0, nullptr);
    submit([&](VkCommandBuffer cmd) {
      barrier(cmd, lumaImage.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
      barrier(cmd, chromaImage.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
      barrier(cmd, output.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
      VkBufferImageCopy region{};
      region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
      region.imageExtent = {w, h, 1};
      vkCmdCopyBufferToImage(cmd, staging.buffer, lumaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
      region.bufferOffset = lumaBytes;
      region.imageExtent = {w / 2, h / 2, 1};
      vkCmdCopyBufferToImage(cmd, staging.buffer, chromaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
      barrier(cmd, lumaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
      barrier(cmd, chromaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
      vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
      vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1, &set, 0, nullptr);
      vkCmdPushConstants(cmd, pipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc), &pc);
      vkCmdDispatch(cmd, (outW + 15) / 16, (outH + 15) / 16, 1);
      barrier(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
      VkBufferImageCopy out{};
      out.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
      out.imageExtent = {outW, outH, 1};
      vkCmdCopyImageToBuffer(cmd, output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &out);
    });
    std::vector<uint8_t> result(size_t(outW) * outH * 4);
    std::memcpy(result.data(), readback.mapped, result.size());
    vkFreeDescriptorSets(device, descriptorPool, 1, &set);
    free(staging);
    free(readback);
    free(lumaImage);
    free(chromaImage);
    free(output);
    return result;
  }
};

// CPU reference of the shader: bilinear sampling (texel centres at i+0.5, clamp to edge).
struct Plane {
  uint32_t w, h, channels;
  std::vector<float> values; // normalised 0..1
  float at(int x, int y, uint32_t c) const {
    x = std::clamp(x, 0, int(w) - 1);
    y = std::clamp(y, 0, int(h) - 1);
    return values[(size_t(y) * w + x) * channels + c];
  }
  float sample(float px, float py, uint32_t c) const { // pixel-edge coordinates
    const float fx = px - .5f, fy = py - .5f;
    const int x0 = int(std::floor(fx)), y0 = int(std::floor(fy));
    const float ax = fx - x0, ay = fy - y0;
    return (at(x0, y0, c) * (1 - ax) + at(x0 + 1, y0, c) * ax) * (1 - ay) +
           (at(x0, y0 + 1, c) * (1 - ax) + at(x0 + 1, y0 + 1, c) * ax) * ay;
  }
};

std::array<float, 3> referencePixel(const Plane &luma, const Plane &chroma, int px, int py, uint32_t outW,
                                    uint32_t outH, const vrost::ConversionPushConstants &pc,
                                    const vrost::FoveationParams &f) {
  const int eyeWidth = int(outW) / 2;
  const int eye = px >= eyeWidth ? 1 : 0;
  const float u = (float(px - eye * eyeWidth) + .5f) / float(eyeWidth), v = (float(py) + .5f) / float(outH);
  float su, sv;
  vrost::foveatedSource(eye, u, v, f, su, sv);
  const float lumaEye = luma.w * .5f;
  const float ax = su * lumaEye, ay = sv * luma.h;
  const float lx = std::clamp(ax, .5f, lumaEye - .5f) + eye * lumaEye, ly = std::clamp(ay, .5f, luma.h - .5f);
  const float chromaEye = chroma.w * .5f;
  const float cxl = (ax + pc.chromaSiting[0]) * .5f, cyl = (ay + pc.chromaSiting[1]) * .5f;
  const float cx = std::clamp(cxl, .5f, chromaEye - .5f) + eye * chromaEye, cy = std::clamp(cyl, .5f, chroma.h - .5f);
  const float y = luma.sample(lx, ly, 0), cb = chroma.sample(cx, cy, 0), cr = chroma.sample(cx, cy, 1);
  const float yv = (y - pc.adjustment[0]) * pc.adjustment[1];
  const float bv = (cb - pc.row0[3]) * pc.adjustment[2], rv = (cr - pc.row0[3]) * pc.adjustment[2];
  std::array<float, 3> rgb{};
  const float *rows[3] = {pc.row0, pc.row1, pc.row2};
  for (int i = 0; i < 3; ++i)
    rgb[i] = std::clamp(rows[i][0] * yv + rows[i][1] * bv + rows[i][2] * rv + vrost::ditherNoise(px, py) * pc.row1[3] / 255.f,
                        0.f, 1.f);
  return rgb;
}

// Smooth "scene" per eye in unmirrored eye coordinates; different per eye so mirroring
// mistakes show. Values are Y'CbCr in 0..1.
float sceneY(int eye, float u, float v) {
  return .5f + .3f * std::sin(6.28318f * (u * (eye ? 1.3f : .9f) + .2f * v)) * std::cos(6.28318f * v * .7f) +
         .1f * (eye ? u : 1 - u);
}
float sceneCb(int eye, float u, float v) { return .5f + .2f * std::sin(6.28318f * (u + (eye ? .3f : 0) + .5f * v)); }
float sceneCr(int eye, float u, float v) { return .5f + .2f * std::cos(6.28318f * (.8f * u - v + (eye ? .2f : 0))); }

struct Case {
  const char *name;
  bool tenBit, foveated;
  uint32_t viewW, viewH; // per eye output size
  vrost::FoveationParams f;
  float dither;
  int chromaLocation;
};

// Builds the encoded planes the host would send: for foveated streams each encoded texel
// takes the scene value at the host compression mapping of its centre.
void encodePlanes(const Case &c, uint32_t encW, uint32_t encH, std::vector<uint16_t> &luma,
                  std::vector<uint16_t> &chroma, Plane &lumaRef, Plane &chromaRef) {
  const uint32_t w = encW * 2, h = encH;
  const uint32_t maxCode = c.tenBit ? 1023 : 255;
  auto quantise = [&](float value) {
    const uint32_t code = uint32_t(std::lround(std::clamp(value, 0.f, 1.f) * float(maxCode)));
    return uint16_t(c.tenBit ? code << 6 : code);
  };
  auto normalise = [&](uint16_t stored) { return c.tenBit ? float(stored) / 65535.f : float(stored) / 255.f; };
  // Original eye coordinate of the centre of encoded texel (x, y) of eye `eye` at plane scale.
  auto original = [&](int eye, float localU, float localV, float &u, float &v) {
    if (!c.foveated) {
      u = localU;
      v = localV;
      return;
    }
    const float m = eye ? 1.f - localU : localU; // mirrored for the right eye
    const float eu = m / c.f.viewRatio[0], ev = localV / c.f.viewRatio[1];
    float ou = vrost::foveationCompressAxis(eu, c.f.centerSize[0], c.f.shifts[eye][0], c.f.edgeRatio[0]);
    const float ov = vrost::foveationCompressAxis(ev, c.f.centerSize[1], c.f.shifts[eye][1], c.f.edgeRatio[1]);
    u = eye ? 1.f - ou : ou;
    v = ov;
  };
  luma.assign(size_t(w) * h, 0);
  lumaRef = {w, h, 1, std::vector<float>(size_t(w) * h)};
  for (uint32_t y = 0; y < h; ++y)
    for (uint32_t x = 0; x < w; ++x) {
      const int eye = x >= encW ? 1 : 0;
      float u, v;
      original(eye, (float(x - eye * encW) + .5f) / float(encW), (float(y) + .5f) / float(h), u, v);
      luma[size_t(y) * w + x] = quantise(sceneY(eye, u, v));
      lumaRef.values[size_t(y) * w + x] = normalise(luma[size_t(y) * w + x]);
    }
  const uint32_t cw = w / 2, ch = h / 2, ceye = encW / 2;
  const vrost::ChromaSiting siting = vrost::chromaSiting(c.chromaLocation);
  chroma.assign(size_t(cw) * ch * 2, 0);
  chromaRef = {cw, ch, 2, std::vector<float>(size_t(cw) * ch * 2)};
  for (uint32_t y = 0; y < ch; ++y)
    for (uint32_t x = 0; x < cw; ++x) {
      const int eye = x >= ceye ? 1 : 0;
      // Chroma sample position in luma pixel-edge coordinates of the encoded eye.
      const float lx = 2.f * (float(x - eye * ceye) + .5f) - siting.x, ly = 2.f * (float(y) + .5f) - siting.y;
      float u, v;
      original(eye, lx / float(encW), ly / float(h), u, v);
      const size_t i = (size_t(y) * cw + x) * 2;
      chroma[i] = quantise(sceneCb(eye, u, v));
      chroma[i + 1] = quantise(sceneCr(eye, u, v));
      chromaRef.values[i] = normalise(chroma[i]);
      chromaRef.values[i + 1] = normalise(chroma[i + 1]);
    }
}

void runCase(Gpu &gpu, const Case &c) {
  const uint32_t encW = c.foveated ? c.f.encoded[0] : c.viewW, encH = c.foveated ? c.f.encoded[1] : c.viewH;
  std::vector<uint16_t> luma, chroma;
  Plane lumaRef, chromaRef;
  encodePlanes(c, encW, encH, luma, chroma, lumaRef, chromaRef);
  const auto pc = vrost::conversionPushConstants(vrost::conversionMatrix(.2126f, .0722f),
                                              vrost::rangeConstants(c.tenBit ? 10 : 8, true, c.tenBit),
                                              vrost::chromaSiting(c.chromaLocation), c.dither, c.f);
  const uint32_t outW = c.viewW * 2, outH = c.viewH;
  const auto out = gpu.convert(luma, chroma, encW * 2, encH, c.tenBit, outW, outH, pc);
  double maxReference = 0, sumReference = 0;
  double maxScene = 0, sumScene = 0, maxCentreScene = 0;
  size_t count = 0, centreCount = 0;
  for (uint32_t y = 0; y < outH; ++y)
    for (uint32_t x = 0; x < outW; ++x) {
      const auto ref = referencePixel(lumaRef, chromaRef, int(x), int(y), outW, outH, pc, c.f);
      const int eye = x >= c.viewW ? 1 : 0;
      const float u = (float(x - eye * c.viewW) + .5f) / float(c.viewW), v = (float(y) + .5f) / float(outH);
      // Scene colour at this output pixel (no compression, no quantisation).
      const float sy = sceneY(eye, u, v), sb = sceneCb(eye, u, v) - .5f, sr = sceneCr(eye, u, v) - .5f;
      const auto m = vrost::conversionMatrix(.2126f, .0722f);
      for (int i = 0; i < 3; ++i) {
        const double gpuValue = out[(size_t(y) * outW + x) * 4 + i];
        const double error = std::fabs(gpuValue - ref[i] * 255.0);
        maxReference = std::max(maxReference, error);
        sumReference += error;
        const double scene = std::clamp(m.row[i][0] * sy + m.row[i][1] * sb + m.row[i][2] * sr, 0.f, 1.f) * 255.0;
        const double sceneError = std::fabs(gpuValue - scene);
        maxScene = std::max(maxScene, sceneError);
        sumScene += sceneError;
        // Centre region of the eye (full resolution when foveated).
        const float m0 = eye ? 1 - u : u;
        bool centre = true;
        if (c.foveated) {
          const float c0x = (1 - c.f.centerSize[0]) / 2, c0y = (1 - c.f.centerSize[1]) / 2;
          const float lo = c0x * (c.f.shifts[eye][0] + 1) + .02f, hi = c0x * (c.f.shifts[eye][0] - 1) + 1 - .02f;
          const float loY = c0y * (c.f.shifts[eye][1] + 1) + .02f, hiY = c0y * (c.f.shifts[eye][1] - 1) + 1 - .02f;
          centre = m0 > lo && m0 < hi && v > loY && v < hiY;
        }
        if (centre) {
          maxCentreScene = std::max(maxCentreScene, sceneError);
          ++centreCount;
        }
        ++count;
      }
    }
  const double meanReference = sumReference / double(count), meanScene = sumScene / double(count);
  std::printf("%-44s ref max %.2f mean %.3f | scene max %.2f mean %.2f centre max %.2f (%zu)\n", c.name,
              maxReference, meanReference, maxScene, meanScene, maxCentreScene, centreCount / 3);
  // The shader must match the CPU reference to within filtering precision. Vulkan leaves
  // filtering precision to the implementation: lavapipe filters 16-bit textures in float
  // (so 10-bit P010 must match to output rounding) but 8-bit textures in 8.8 fixed point,
  // which can move each filtered plane by about one code.
  const double maxAllowed = c.tenBit ? 1.0 : 4.5, meanAllowed = c.tenBit ? .3 : .6;
  CHECK(maxReference <= maxAllowed, "%s: GPU differs from the CPU reference by %.2f", c.name, maxReference);
  CHECK(meanReference <= meanAllowed, "%s: mean reference error %.3f", c.name, meanReference);
  // And the full chain (host compression + client inverse) must reproduce the scene:
  // closely in the full-resolution centre, approximately in the compressed periphery.
  CHECK(maxCentreScene <= 6.0, "%s: centre differs from the scene by %.2f", c.name, maxCentreScene);
  CHECK(meanScene <= 2.0, "%s: mean scene error %.2f", c.name, meanScene);
}

vrost::FoveationParams foveation(uint32_t viewW, uint32_t viewH, float centerSize, float edgeRatio, float shiftLX,
                              float shiftLY, float shiftRX, float shiftRY) {
  // Same alignment as the negotiation in alvr/server_core/src/connection.rs.
  vrost::FoveationParams f;
  f.enabled = true;
  const float res[2] = {float(viewW), float(viewH)};
  const float shifts[2][2] = {{shiftLX, shiftLY}, {shiftRX, shiftRY}};
  for (int axis = 0; axis < 2; ++axis) {
    const float resolution = res[axis];
    float edgeSize = resolution - centerSize * resolution;
    const float alignedSize = float(1.0 - std::ceil(double(edgeSize) / (double(edgeRatio) * 2.0)) * (double(edgeRatio) * 2.0) / double(resolution));
    edgeSize = resolution - alignedSize * resolution;
    const float step = edgeRatio * 2.f / edgeSize;
    for (int eye = 0; eye < 2; ++eye) {
      float aligned = std::ceil(shifts[eye][axis] * edgeSize / (edgeRatio * 2.f)) * (edgeRatio * 2.f) / edgeSize;
      f.shifts[eye][axis] = std::clamp(aligned, -1.f + step, 1.f - step);
    }
    const float scale = float(double(alignedSize) + (1.0 - double(alignedSize)) / double(edgeRatio));
    const float optimized = scale * resolution;
    const uint32_t encoded = uint32_t(std::ceil(optimized / 32.f)) * 32;
    f.encoded[axis] = encoded;
    f.viewRatio[axis] = optimized / float(encoded);
    f.centerSize[axis] = alignedSize;
    f.edgeRatio[axis] = edgeRatio;
  }
  return f;
}

void mathTests() {
  // The client inverse is the exact algebraic inverse of the host compression: checked in
  // double precision, for many centre sizes, edge ratios and centres.
  std::mt19937 rng(7);
  std::uniform_real_distribution<double> unit(0., 1.);
  double worstAlgebra = 0, worstFloat = 0, worstFloatInner = 0;
  for (int trial = 0; trial < 1000; ++trial) {
    const double size = .25 + .5 * unit(rng), edge = 1.5 + 4. * unit(rng);
    // Centres as far out as the negotiation allows for a 1000-pixel eye (step = 2*edge/edge size).
    const double step = 2. * edge / ((1. - size) * 1000.);
    const double shift = std::clamp(-1. + 2. * unit(rng), -1. + step, 1. - step);
    const float fs = float(size), fe = float(edge), fsh = float(shift);
    for (int i = 0; i <= 1000; ++i) {
      const double u = double(i) / 1000.;
      const double exact = vrost::foveationDecompressAxis<double>(u, fs, fsh, fe);
      worstAlgebra = std::max(worstAlgebra, std::fabs(vrost::foveationCompressAxis<double>(exact, fs, fsh, fe) - u));
      // The shader evaluates the inverse in float: error in compressed-image units.
      const double error = std::fabs(double(vrost::foveationDecompressAxis<float>(float(u), fs, fsh, fe)) - exact);
      worstFloat = std::max(worstFloat, error);
      if (u >= .01 && u <= .99) worstFloatInner = std::max(worstFloatInner, error);
    }
  }
  std::printf("foveation inverse: algebra %.2e, float %.2e (%.2e inside the outer 1%%)\n", worstAlgebra, worstFloat,
              worstFloatInner);
  CHECK(worstAlgebra < 1e-9, "inverse is not exact: %.2e", worstAlgebra);
  // Float error stays far below one encoded pixel; it peaks only in the last sliver of an
  // eye when the centre is pushed to the very edge.
  CHECK(worstFloatInner < 1e-4, "float inverse error %.2e", worstFloatInner);
  CHECK(worstFloat < 1e-3, "float inverse error at the edge %.2e", worstFloat);
  // Range constants.
  const auto limited8 = vrost::rangeConstants(8, false, false);
  CHECK(std::fabs(limited8.lumaOffset - 16.f / 255.f) < 1e-7f, "8-bit offset");
  CHECK(std::fabs(limited8.lumaScale - 255.f / 219.f) < 1e-6f, "8-bit scale");
  CHECK(std::fabs(limited8.chromaCentre - 128.f / 255.f) < 1e-7f, "8-bit chroma centre");
  const auto limited10 = vrost::rangeConstants(10, false, true);
  CHECK(std::fabs(limited10.lumaOffset - 64.f * 64.f / 65535.f) < 1e-7f, "10-bit offset");
  CHECK(std::fabs((940.f * 64.f / 65535.f - limited10.lumaOffset) * limited10.lumaScale - 1.f) < 1e-5f, "10-bit white");
  CHECK(std::fabs(limited10.chromaCentre - 512.f * 64.f / 65535.f) < 1e-7f, "10-bit chroma centre");
  const auto full10 = vrost::rangeConstants(10, true, true);
  CHECK(std::fabs(1023.f * 64.f / 65535.f * full10.lumaScale - 1.f) < 1e-5f, "10-bit full white");
  // Chroma siting.
  CHECK(vrost::chromaSiting(0).x == .5f && vrost::chromaSiting(1).x == .5f && vrost::chromaSiting(2).x == 0.f, "siting");
  // Dither is bounded and roughly zero-mean.
  double mean = 0;
  for (int y = 0; y < 64; ++y)
    for (int x = 0; x < 64; ++x) {
      const float n = vrost::ditherNoise(x, y);
      CHECK(n >= -.5f && n < .5f, "dither range %f", n);
      mean += n;
    }
  CHECK(std::fabs(mean / 4096) < .05, "dither mean %f", mean / 4096);
}
} // namespace

int main(int argc, char **argv) {
  if (argc < 2) {
    std::printf("usage: convert_shader_tests convert.spv\n");
    return 2;
  }
  mathTests();
  Gpu gpu;
  try {
    if (!gpu.open(argv[1])) {
      std::printf("SKIP: no Vulkan device for shader tests\n");
      return failures ? 1 : 77;
    }
  } catch (const std::exception &e) {
    std::printf("SKIP: Vulkan unavailable (%s)\n", e.what());
    return failures ? 1 : 77;
  }
  std::printf("Vulkan device: %s\n", gpu.name.c_str());
  const Case cases[] = {
      {"8-bit, not foveated, left-sited chroma", false, false, 192, 160, {}, 0.f, 0},
      {"10-bit P010, not foveated", true, false, 192, 160, {}, 0.f, 0},
      {"8-bit, centre-sited chroma", false, false, 192, 160, {}, 0.f, 2},
      {"8-bit with dither", false, false, 192, 160, {}, 1.f, 0},
      {"10-bit, centre-sited chroma", true, false, 192, 160, {}, 0.f, 2},
      {"10-bit, top-left sited chroma", true, false, 192, 160, {}, 0.f, 3},
      {"10-bit with dither", true, false, 192, 160, {}, 1.f, 0},
      {"foveated 10-bit with dither, gaze right/up", true, true, 512, 512, foveation(512, 512, .4f, 4.f, .6f, -.5f, -.2f, -.5f), 1.f, 0},
      {"foveated, ALVR default centre", false, true, 512, 480, foveation(512, 480, .45f, 4.f, .4f, .1f, .4f, .1f), 0.f, 0},
      {"foveated 10-bit, Beyond eye-tracked preset", true, true, 512, 512, foveation(512, 512, .4f, 4.f, 0, 0, 0, 0), 0.f, 0},
      {"foveated, gaze far left/down, eyes differ", false, true, 512, 512, foveation(512, 512, .4f, 4.f, -.9f, .8f, .55f, -.6f), 0.f, 0},
      {"foveated, clamped extreme centres", false, true, 384, 384, foveation(384, 384, .5f, 3.f, 1.f, -1.f, -1.f, 1.f), 0.f, 0},
      {"foveated, subtle preset", false, true, 480, 448, foveation(480, 448, .7f, 2.f, .2f, 0, .2f, 0), 0.f, 0},
  };
  for (const auto &c : cases) runCase(gpu, c);
  {
    // Eye boundary: constant colours per eye must not bleed into each other.
    Case c{"eye boundary", false, true, 256, 256, foveation(256, 256, .45f, 4.f, .4f, .1f, .4f, .1f), 0.f, 0};
    const uint32_t encW = c.f.encoded[0], encH = c.f.encoded[1];
    std::vector<uint16_t> luma(size_t(encW) * 2 * encH), chroma(size_t(encW) * encH, 128);
    for (uint32_t y = 0; y < encH; ++y)
      for (uint32_t x = 0; x < encW * 2; ++x) luma[size_t(y) * encW * 2 + x] = x < encW ? 40 : 220;
    const auto pc = vrost::conversionPushConstants(vrost::conversionMatrix(.2126f, .0722f), vrost::rangeConstants(8, true, false),
                                                vrost::chromaSiting(0), 0.f, c.f);
    const auto out = gpu.convert(luma, chroma, encW * 2, encH, false, 512, 256, pc);
    int bleed = 0;
    for (uint32_t y = 0; y < 256; ++y)
      for (uint32_t x = 0; x < 512; ++x) {
        const int g = out[(size_t(y) * 512 + x) * 4 + 1];
        if (g != (x < 256 ? 40 : 220)) ++bleed;
      }
    std::printf("%-44s %d pixels differ\n", c.name, bleed);
    CHECK(bleed == 0, "eye colours bled across the boundary in %d pixels", bleed);
  }
  std::printf(failures ? "Conversion shader tests FAILED (%d)\n" : "Conversion shader tests passed\n", failures);
  return failures ? 1 : 0;
}
