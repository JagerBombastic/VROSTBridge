// Runs the real fused frame pass (client/platform/linux/fused.comp, RC4.3) on a Vulkan
// device — normally Mesa's software rasteriser (lavapipe) — and checks it against a CPU
// model of the same sampling, built from client/device/conversion.hpp and core/reaim.hpp:
//  1. with the head pose unchanged and no foveation, every output pixel is the decoded
//     texel converted exactly (Catmull-Rom at a texel centre returns that texel);
//  2. foveated streams (gaze-centred and fixed) match the model pixel by pixel and
//     reproduce the original scene at least as well as the RC4.2 two passes (bilinear
//     expansion, then Catmull-Rom re-aim): one resampling instead of two;
//  3. a re-aimed frame matches the model and moves the way the world appears to move;
//  4. sharpening raises local contrast in the full-resolution region, leaves flat areas and
//     the reduced foveation edges unchanged, and never mixes the eyes.
// 1.0.0.11: the pass reads its constants from a uniform buffer (FusedFrame, computed from the
// parameters by fusedFrameConstants) and each eye's visible spans from a storage buffer; the model
// below still evaluates the foveation inverse term by term (conversion.hpp), so it also checks the
// per-frame constants. Pixels outside the spans keep the black the output image was cleared to.
// Usage: fused_shader_tests <fused.spv>. Exit 77 (skip) if no Vulkan device exists.
#include "client/device/conversion.hpp"
#include "client/device/visible_area.hpp"
#include "core/reaim.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
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
    // As the client lays it out (client/platform/linux/vulkan.cpp openFused): planes, output image, the frame's
    // constants and the visible spans; no push constants.
    VkDescriptorSetLayoutBinding bindings[5]{};
    for (int i = 0; i < 5; ++i) {
      bindings[i].binding = i;
      bindings[i].descriptorCount = 1;
      bindings[i].descriptorType = i == 4   ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER
                                   : i == 3 ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER
                                   : i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                                            : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dl.bindingCount = 5;
    dl.pBindings = bindings;
    vk(vkCreateDescriptorSetLayout(device, &dl, nullptr, &setLayout), "set layout");
    VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pl.setLayoutCount = 1;
    pl.pSetLayouts = &setLayout;
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
    ai.memoryTypeIndex =
        memoryType(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
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
  // Planes (luma w x h, chroma w/2 x h/2 interleaved CbCr) -> RGBA8 output through the fused pass. spans: each
  // eye's visible span per output row (left eye's rows, then the right eye's); none: every pixel.
  std::vector<uint8_t> run(const std::vector<uint16_t> &luma, const std::vector<uint16_t> &chroma, uint32_t w, uint32_t h,
                           bool sixteen, uint32_t outW, uint32_t outH, const vrost::FusedParameters &pc,
                           double *milliseconds = nullptr, const std::vector<uint32_t> *spans = nullptr) {
    const VkFormat lumaFormat = sixteen ? VK_FORMAT_R16_UNORM : VK_FORMAT_R8_UNORM;
    const VkFormat chromaFormat = sixteen ? VK_FORMAT_R16G16_UNORM : VK_FORMAT_R8G8_UNORM;
    const size_t bytes = sixteen ? 2 : 1;
    auto lumaImage = image(w, h, lumaFormat, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    auto chromaImage = image(w / 2, h / 2, chromaFormat, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    auto output = image(outW, outH, VK_FORMAT_R8G8B8A8_UNORM,
                        VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
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
    // The frame's constants and the spans, as the client writes them (submitFused, writeSpans).
    const auto frame = vrost::fusedFrameConstants(pc, w, h, outW / 2, outH);
    Buffer constants = buffer(sizeof(frame), VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
    std::memcpy(constants.mapped, &frame, sizeof(frame));
    std::vector<uint32_t> rows(size_t(outH) * 2, vrost::packSpan(0, outW / 2));
    if (spans) rows = *spans;
    Buffer visible = buffer(rows.size() * sizeof(uint32_t), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    std::memcpy(visible.mapped, rows.data(), rows.size() * sizeof(uint32_t));
    VkDescriptorBufferInfo bufferInfos[2] = {{constants.buffer, 0, sizeof(frame)},
                                             {visible.buffer, 0, rows.size() * sizeof(uint32_t)}};
    VkWriteDescriptorSet bufferWrites[2]{};
    for (int i = 0; i < 2; ++i) {
      bufferWrites[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      bufferWrites[i].dstSet = set;
      bufferWrites[i].dstBinding = uint32_t(3 + i);
      bufferWrites[i].descriptorCount = 1;
      bufferWrites[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      bufferWrites[i].pBufferInfo = &bufferInfos[i];
    }
    vkUpdateDescriptorSets(device, 2, bufferWrites, 0, nullptr);
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
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool;
    ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vk(vkAllocateCommandBuffers(device, &ai, &cmd), "command buffer");
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    // Upload first (timed separately), then the pass alone.
    vkBeginCommandBuffer(cmd, &bi);
    barrier(cmd, lumaImage.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    barrier(cmd, chromaImage.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    barrier(cmd, output.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
    const VkClearColorValue black{{0.f, 0.f, 0.f, 1.f}}; // as the client clears a new output image
    const VkImageSubresourceRange whole{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdClearColorImage(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, &black, 1, &whole);
    barrier(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL);
    VkBufferImageCopy region{};
    region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.imageExtent = {w, h, 1};
    vkCmdCopyBufferToImage(cmd, staging.buffer, lumaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
    region.bufferOffset = lumaBytes;
    region.imageExtent = {w / 2, h / 2, 1};
    vkCmdCopyBufferToImage(cmd, staging.buffer, chromaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
    barrier(cmd, lumaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    barrier(cmd, chromaImage.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    vkEndCommandBuffer(cmd);
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "upload");
    vk(vkQueueWaitIdle(queue), "wait upload");
    vkResetCommandBuffer(cmd, 0);
    vkBeginCommandBuffer(cmd, &bi);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1, &set, 0, nullptr);
    vkCmdDispatch(cmd, (outW / 2 + 15) / 16, (outH + 15) / 16, 2); // z: the eye, as the client dispatches it
    vkEndCommandBuffer(cmd);
    const auto start = std::chrono::steady_clock::now();
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "fused pass");
    vk(vkQueueWaitIdle(queue), "wait pass");
    if (milliseconds)
      *milliseconds = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    vkResetCommandBuffer(cmd, 0);
    vkBeginCommandBuffer(cmd, &bi);
    barrier(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
    VkBufferImageCopy out{};
    out.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    out.imageExtent = {outW, outH, 1};
    vkCmdCopyImageToBuffer(cmd, output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &out);
    vkEndCommandBuffer(cmd);
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "readback");
    vk(vkQueueWaitIdle(queue), "wait readback");
    vkFreeCommandBuffers(device, pool, 1, &cmd);
    std::vector<uint8_t> result(size_t(outW) * outH * 4);
    std::memcpy(result.data(), readback.mapped, result.size());
    vkFreeDescriptorSets(device, descriptorPool, 1, &set);
    free(constants);
    free(visible);
    free(staging);
    free(readback);
    free(lumaImage);
    free(chromaImage);
    free(output);
    return result;
  }
};

// Normalised plane with the sampler's clamp-to-edge bilinear filtering.
struct Plane {
  uint32_t w = 0, h = 0, channels = 1;
  std::vector<float> values;
  float at(int x, int y, uint32_t c) const {
    x = std::clamp(x, 0, int(w) - 1);
    y = std::clamp(y, 0, int(h) - 1);
    return values[(size_t(y) * w + x) * channels + c];
  }
  float sample(float px, float py, uint32_t c) const { // texel coordinates, centres at .5
    const float fx = px - .5f, fy = py - .5f;
    const int x0 = int(std::floor(fx)), y0 = int(std::floor(fy));
    const float ax = fx - x0, ay = fy - y0;
    return (at(x0, y0, c) * (1 - ax) + at(x0 + 1, y0, c) * ax) * (1 - ay) +
           (at(x0, y0 + 1, c) * (1 - ax) + at(x0 + 1, y0 + 1, c) * ax) * ay;
  }
};

// ---- CPU model of fused.comp ----
float decompress(float u, float size, float shift, float edge) { return vrost::foveationDecompressAxis<float>(u, size, shift, edge); }

void encodedAt(const vrost::FusedParameters &pc, int eye, float sx, float sy, float &ex, float &ey, float *slopeX = nullptr,
               float *slopeY = nullptr) {
  if (pc.row2[3] < .5f) {
    ex = sx;
    ey = sy;
    if (slopeX) *slopeX = 1;
    if (slopeY) *slopeY = 1;
    return;
  }
  const float shiftX = eye ? pc.fovShifts[2] : pc.fovShifts[0], shiftY = eye ? pc.fovShifts[3] : pc.fovShifts[1];
  const float x = eye ? 1.f - sx : sx;
  ex = decompress(x, pc.fovSize[0], shiftX, pc.fovSize[2]) * pc.sitingView[2];
  ey = decompress(sy, pc.fovSize[1], shiftY, pc.fovSize[3]) * pc.sitingView[3];
  if (slopeX) *slopeX = vrost::foveationDecompressSlope<float>(x, pc.fovSize[0], shiftX, pc.fovSize[2]) * pc.sitingView[2];
  if (slopeY) *slopeY = vrost::foveationDecompressSlope<float>(sy, pc.fovSize[1], shiftY, pc.fovSize[3]) * pc.sitingView[3];
  if (eye) ex = 1.f - ex;
}

float catmullRom(const Plane &luma, float px, float py, float lox, float loy, float hix, float hiy) {
  const float p1x = std::floor(px - .5f) + .5f, p1y = std::floor(py - .5f) + .5f;
  const float fx = px - p1x, fy = py - p1y;
  auto weights = [](float f, float w[4]) {
    w[0] = f * (-.5f + f * (1 - .5f * f));
    w[1] = 1 + f * f * (-2.5f + 1.5f * f);
    w[2] = f * (.5f + f * (2 - 1.5f * f));
    w[3] = f * f * (-.5f + .5f * f);
  };
  float wx[4], wy[4];
  weights(fx, wx);
  weights(fy, wy);
  const float xs[3] = {std::clamp(p1x - 1, lox, hix), std::clamp(p1x + wx[2] / (wx[1] + wx[2]), lox, hix),
                       std::clamp(p1x + 2, lox, hix)};
  const float ys[3] = {std::clamp(p1y - 1, loy, hiy), std::clamp(p1y + wy[2] / (wy[1] + wy[2]), loy, hiy),
                       std::clamp(p1y + 2, loy, hiy)};
  const float cx[3] = {wx[0], wx[1] + wx[2], wx[3]}, cy[3] = {wy[0], wy[1] + wy[2], wy[3]};
  // RC4.4: the five-tap form (no corner taps), renormalised, as fused.comp.
  const int taps[5][2] = {{1, 1}, {1, 0}, {0, 1}, {2, 1}, {1, 2}};
  float sum = 0, weight = 0;
  for (const auto &t : taps) {
    const float w = cx[t[0]] * cy[t[1]];
    sum += luma.sample(xs[t[0]], ys[t[1]], 0) * w;
    weight += w;
  }
  return sum / weight;
}

// 1.0.0.3: the cubic B-spline from four bilinear taps, as fused.comp's lumaBSpline.
float bSpline(const Plane &luma, float px, float py, float lox, float loy, float hix, float hiy) {
  const float p1x = std::floor(px - .5f) + .5f, p1y = std::floor(py - .5f) + .5f;
  auto axis = [](float p1, float f, float lo, float hi, float &s0, float &t0, float &t1) {
    const float g = 1 - f, w0 = g * g * g / 6, w1 = (4 + f * f * (-6 + 3 * f)) / 6, w3 = f * f * f / 6;
    s0 = w0 + w1;
    t0 = std::clamp(p1 - 1 + w1 / s0, lo, hi);
    t1 = std::clamp(p1 + 1 + w3 / std::max(1 - s0, 1e-6f), lo, hi);
  };
  float sx, tx0, tx1, sy, ty0, ty1;
  axis(p1x, px - p1x, lox, hix, sx, tx0, tx1);
  axis(p1y, py - p1y, loy, hiy, sy, ty0, ty1);
  return (luma.sample(tx0, ty0, 0) * sx + luma.sample(tx1, ty0, 0) * (1 - sx)) * sy +
         (luma.sample(tx0, ty1, 0) * sx + luma.sample(tx1, ty1, 0) * (1 - sx)) * (1 - sy);
}

std::array<float, 3> model(const Plane &luma, const Plane &chroma, int px, int py, uint32_t outW, uint32_t outH,
                           const vrost::FusedParameters &pc) {
  const int eyeWidth = int(outW) / 2;
  const int eye = px >= eyeWidth ? 1 : 0;
  const float eyeW = float(eyeWidth), eyeH = float(outH);
  const float u = (float(px - eye * eyeWidth) + .5f) / eyeW, v = (float(py) + .5f) / eyeH;
  const float(*rows)[4] = pc.eyeRows[eye];
  const float hx = rows[0][0] * u + rows[0][1] * v + rows[0][2], hy = rows[1][0] * u + rows[1][1] * v + rows[1][2],
              hz = rows[2][0] * u + rows[2][1] * v + rows[2][2];
  if (hz <= 1e-6f) return {0, 0, 0};
  float sx = hx / hz, sy = hy / hz;
  const float outside = std::max(std::max(std::max(-sx, sx - 1), 0.f) * eyeW, std::max(std::max(-sy, sy - 1), 0.f) * eyeH);
  const float fade = std::clamp(1 - outside * .5f, 0.f, 1.f);
  if (fade <= 0) return {0, 0, 0};
  sx = std::clamp(sx, 0.f, 1.f);
  sy = std::clamp(sy, 0.f, 1.f);
  float ex, ey, slopeX, slopeY;
  encodedAt(pc, eye, sx, sy, ex, ey, &slopeX, &slopeY);
  const float lumaEye = luma.w * .5f, lumaH = float(luma.h);
  const float ax = ex * lumaEye, ay = ey * lumaH;
  const float pxl = ax + eye * lumaEye, pyl = ay;
  const float lox = eye * lumaEye + .5f, loy = .5f, hix = (eye + 1) * lumaEye - .5f, hiy = lumaH - .5f;
  auto full = [&](float raw) { return (raw - pc.adjustment[0]) * pc.adjustment[1]; };
  // RC4.4: the encoded texels per output pixel from the foveation slope (no extra inverses).
  const float texels = std::min(slopeX * lumaEye / eyeW, slopeY * lumaH / eyeH);
  const float detail = std::clamp(2.f * texels - 1.f, 0.f, 1.f);
  // 1.0.0.3: Catmull-Rom from one texel per pixel, the B-spline from two output pixels per texel, blended between.
  const float sharp = detail > 0 ? catmullRom(luma, pxl, pyl, lox, loy, hix, hiy) : 0;
  const float smooth = detail < 1 ? bSpline(luma, pxl, pyl, lox, loy, hix, hiy) : 0;
  float y = full(detail >= 1 ? sharp : detail <= 0 ? smooth : smooth + (sharp - smooth) * detail);
  if (pc.adjustment[3] > 0 && detail > 0) {
    auto tap = [&](float dx, float dy) {
      return full(luma.sample(std::clamp(pxl + dx, lox, hix), std::clamp(pyl + dy, loy, hiy), 0));
    };
    const float n = tap(0, -1), s = tap(0, 1), w = tap(-1, 0), e = tap(1, 0);
    const float mn = std::clamp(std::min({y, n, s, w, e}), 0.f, 1.f), mx = std::clamp(std::max({y, n, s, w, e}), 0.f, 1.f);
    const float amp = std::sqrt(std::clamp(std::min(mn, 1.f - mx) / std::max(mx, 1.f / 1024.f), 0.f, 1.f));
    y += pc.adjustment[3] * amp * detail * (y - .25f * (n + s + w + e));
  }
  const float chromaEye = chroma.w * .5f;
  const float c2x = (ax + pc.sitingView[0]) * .5f, c2y = (ay + pc.sitingView[1]) * .5f;
  const float cx = std::clamp(c2x, .5f, chromaEye - .5f) + eye * chromaEye, cy = std::clamp(c2y, .5f, chroma.h - .5f);
  const float cb = (chroma.sample(cx, cy, 0) - pc.row0[3]) * pc.adjustment[2];
  const float cr = (chroma.sample(cx, cy, 1) - pc.row0[3]) * pc.adjustment[2];
  const float noise = vrost::ditherNoise(px, py) * pc.row1[3] / 255.f;
  const float *m[3] = {pc.row0, pc.row1, pc.row2};
  std::array<float, 3> rgb{};
  for (int i = 0; i < 3; ++i) rgb[i] = std::clamp(m[i][0] * y + m[i][1] * cb + m[i][2] * cr + noise, 0.f, 1.f) * fade;
  return rgb;
}

// 1.0.0.3: the model's luma (0..1, before sharpening and conversion) at an output pixel of the left eye,
// unchanged pose.
float modelLuma(const Plane &luma, int px, int py, uint32_t outW, uint32_t outH, const vrost::FusedParameters &pc) {
  const float eyeW = float(outW / 2), eyeH = float(outH);
  float ex, ey, slopeX, slopeY;
  encodedAt(pc, 0, (float(px) + .5f) / eyeW, (float(py) + .5f) / eyeH, ex, ey, &slopeX, &slopeY);
  const float lumaEye = luma.w * .5f, lumaH = float(luma.h);
  const float pxl = ex * lumaEye, pyl = ey * lumaH;
  const float lox = .5f, loy = .5f, hix = lumaEye - .5f, hiy = lumaH - .5f;
  const float texels = std::min(slopeX * lumaEye / eyeW, slopeY * lumaH / eyeH);
  const float detail = std::clamp(2.f * texels - 1.f, 0.f, 1.f);
  const float sharp = detail > 0 ? catmullRom(luma, pxl, pyl, lox, loy, hix, hiy) : 0;
  const float smooth = detail < 1 ? bSpline(luma, pxl, pyl, lox, loy, hix, hiy) : 0;
  return detail >= 1 ? sharp : detail <= 0 ? smooth : smooth + (sharp - smooth) * detail;
}

// The RC4.2 chain on the CPU for comparison: bilinear expansion to the view size
// (convert.comp), then (unchanged pose) the converted pixel.
std::array<float, 3> twoPass(const Plane &luma, const Plane &chroma, int px, int py, uint32_t outW, uint32_t outH,
                             const vrost::FusedParameters &pc) {
  const int eyeWidth = int(outW) / 2;
  const int eye = px >= eyeWidth ? 1 : 0;
  const float u = (float(px - eye * eyeWidth) + .5f) / float(eyeWidth), v = (float(py) + .5f) / float(outH);
  float ex, ey;
  encodedAt(pc, eye, u, v, ex, ey);
  const float lumaEye = luma.w * .5f;
  const float ax = ex * lumaEye, ay = ey * luma.h;
  const float lx = std::clamp(ax, .5f, lumaEye - .5f) + eye * lumaEye, ly = std::clamp(ay, .5f, luma.h - .5f);
  const float chromaEye = chroma.w * .5f;
  const float cx = std::clamp((ax + pc.sitingView[0]) * .5f, .5f, chromaEye - .5f) + eye * chromaEye;
  const float cy = std::clamp((ay + pc.sitingView[1]) * .5f, .5f, chroma.h - .5f);
  const float y = (luma.sample(lx, ly, 0) - pc.adjustment[0]) * pc.adjustment[1];
  const float cb = (chroma.sample(cx, cy, 0) - pc.row0[3]) * pc.adjustment[2];
  const float cr = (chroma.sample(cx, cy, 1) - pc.row0[3]) * pc.adjustment[2];
  const float noise = vrost::ditherNoise(px, py) * pc.row1[3] / 255.f;
  const float *m[3] = {pc.row0, pc.row1, pc.row2};
  std::array<float, 3> rgb{};
  for (int i = 0; i < 3; ++i) rgb[i] = std::clamp(m[i][0] * y + m[i][1] * cb + m[i][2] * cr + noise, 0.f, 1.f);
  return rgb;
}

// ---- Scenes and encoding (as tests/convert_shader_tests.cpp) ----
float sceneY(int eye, float u, float v) {
  return .5f + .3f * std::sin(6.28318f * (u * (eye ? 2.6f : 1.8f) + .2f * v)) * std::cos(6.28318f * v * 1.4f) +
         .1f * (eye ? u : 1 - u);
}
float sceneCb(int eye, float u, float v) { return .5f + .2f * std::sin(6.28318f * (u + (eye ? .3f : 0) + .5f * v)); }
float sceneCr(int eye, float u, float v) { return .5f + .2f * std::cos(6.28318f * (.8f * u - v + (eye ? .2f : 0))); }

struct Stream {
  bool tenBit = true, foveated = false;
  uint32_t viewW = 192, viewH = 160;
  vrost::FoveationParams f;
  float dither = 0;
  int chromaLocation = 0;
};

struct Encoded {
  std::vector<uint16_t> luma, chroma;
  Plane lumaRef, chromaRef;
  uint32_t w = 0, h = 0;
};

Encoded encode(const Stream &c, const std::function<float(int, float, float)> &scene = sceneY) {
  Encoded out;
  const uint32_t encW = c.foveated ? c.f.encoded[0] : c.viewW, encH = c.foveated ? c.f.encoded[1] : c.viewH;
  const uint32_t w = encW * 2, h = encH;
  out.w = w;
  out.h = h;
  const uint32_t maxCode = c.tenBit ? 1023 : 255;
  auto quantise = [&](float value) {
    const uint32_t code = uint32_t(std::lround(std::clamp(value, 0.f, 1.f) * float(maxCode)));
    return uint16_t(c.tenBit ? code << 6 : code);
  };
  auto normalise = [&](uint16_t stored) { return c.tenBit ? float(stored) / 65535.f : float(stored) / 255.f; };
  auto original = [&](int eye, float localU, float localV, float &u, float &v) {
    if (!c.foveated) {
      u = localU;
      v = localV;
      return;
    }
    const float m = eye ? 1.f - localU : localU;
    const float eu = m / c.f.viewRatio[0], ev = localV / c.f.viewRatio[1];
    const float ou = vrost::foveationCompressAxis(eu, c.f.centerSize[0], c.f.shifts[eye][0], c.f.edgeRatio[0]);
    const float ov = vrost::foveationCompressAxis(ev, c.f.centerSize[1], c.f.shifts[eye][1], c.f.edgeRatio[1]);
    u = eye ? 1.f - ou : ou;
    v = ov;
  };
  out.luma.assign(size_t(w) * h, 0);
  out.lumaRef = {w, h, 1, std::vector<float>(size_t(w) * h)};
  for (uint32_t y = 0; y < h; ++y)
    for (uint32_t x = 0; x < w; ++x) {
      const int eye = x >= encW ? 1 : 0;
      float u, v;
      original(eye, (float(x - eye * encW) + .5f) / float(encW), (float(y) + .5f) / float(h), u, v);
      out.luma[size_t(y) * w + x] = quantise(scene(eye, u, v));
      out.lumaRef.values[size_t(y) * w + x] = normalise(out.luma[size_t(y) * w + x]);
    }
  const uint32_t cw = w / 2, ch = h / 2, ceye = encW / 2;
  const vrost::ChromaSiting siting = vrost::chromaSiting(c.chromaLocation);
  out.chroma.assign(size_t(cw) * ch * 2, 0);
  out.chromaRef = {cw, ch, 2, std::vector<float>(size_t(cw) * ch * 2)};
  for (uint32_t y = 0; y < ch; ++y)
    for (uint32_t x = 0; x < cw; ++x) {
      const int eye = x >= ceye ? 1 : 0;
      const float lx = 2.f * (float(x - eye * ceye) + .5f) - siting.x, ly = 2.f * (float(y) + .5f) - siting.y;
      float u, v;
      original(eye, lx / float(encW), ly / float(h), u, v);
      const size_t i = (size_t(y) * cw + x) * 2;
      out.chroma[i] = quantise(sceneCb(eye, u, v));
      out.chroma[i + 1] = quantise(sceneCr(eye, u, v));
      out.chromaRef.values[i] = normalise(out.chroma[i]);
      out.chromaRef.values[i + 1] = normalise(out.chroma[i + 1]);
    }
  return out;
}

vrost::FoveationParams foveation(uint32_t viewW, uint32_t viewH, float centerSize, float edgeRatio, float shiftLX,
                              float shiftLY, float shiftRX, float shiftRY) {
  vrost::FoveationParams f;
  f.enabled = true;
  const float res[2] = {float(viewW), float(viewH)};
  const float shifts[2][2] = {{shiftLX, shiftLY}, {shiftRX, shiftRY}};
  for (int axis = 0; axis < 2; ++axis) {
    const float resolution = res[axis];
    float edgeSize = resolution - centerSize * resolution;
    const float alignedSize =
        float(1.0 - std::ceil(double(edgeSize) / (double(edgeRatio) * 2.0)) * (double(edgeRatio) * 2.0) / double(resolution));
    edgeSize = resolution - alignedSize * resolution;
    const float step = edgeRatio * 2.f / edgeSize;
    for (int eye = 0; eye < 2; ++eye) {
      const float aligned = std::ceil(shifts[eye][axis] * edgeSize / (edgeRatio * 2.f)) * (edgeRatio * 2.f) / edgeSize;
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

// Asymmetric, mirrored and canted views like a real headset's: each eye's homography differs,
// so a pass that used the other eye's rows would not match the model.
std::array<VrostView, 2> realEyes() {
  std::array<VrostView, 2> v{};
  const float cant = .0174533f; // 2 degrees outwards
  v[0].pose.orientation = {0, std::sin(cant / 2), 0, std::cos(cant / 2)};
  v[1].pose.orientation = {0, -std::sin(cant / 2), 0, std::cos(cant / 2)};
  v[0].left = -.87f; v[0].right = .79f; v[0].up = .84f; v[0].down = -.92f;
  v[1].left = -.79f; v[1].right = .87f; v[1].up = .84f; v[1].down = -.92f;
  v[0].pose.position[0] = -.032f;
  v[1].pose.position[0] = .032f;
  return v;
}

vrost::FusedParameters constants(const Stream &c, const vrost::Pose &source, const vrost::Pose &target, float sharpening,
                                 bool fullRange = true, bool bt601 = false, bool real = false) {
  const auto conversion = vrost::conversionPushConstants(bt601 ? vrost::conversionMatrix(.299f, .114f) : vrost::conversionMatrix(.2126f, .0722f),
                                                      vrost::rangeConstants(c.tenBit ? 10 : 8, fullRange, c.tenBit),
                                                      vrost::chromaSiting(c.chromaLocation), c.dither, c.f);
  return vrost::fusedParameters(conversion, vrost::reaimConstants(source, target, real ? realEyes() : eyes()).rows, sharpening);
}

struct Comparison {
  double maxModel = 0, meanModel = 0, meanScene = 0, meanSceneTwoPass = 0, maxCentreScene = 0;
  double centreScene = 0, centreSceneTwoPass = 0; // mean errors in the full-resolution centre
};

Comparison compare(const Stream &c, const Encoded &e, const std::vector<uint8_t> &out, const vrost::FusedParameters &pc,
                   bool sceneChecks, const std::function<float(int, float, float)> &sceneLuma = sceneY) {
  Comparison r;
  const uint32_t outW = c.viewW * 2, outH = c.viewH;
  double sumModel = 0, sumScene = 0, sumTwoPass = 0, sumCentre = 0, sumCentreTwoPass = 0;
  size_t count = 0, centreCount = 0;
  const auto m = vrost::conversionMatrix(.2126f, .0722f);
  for (uint32_t y = 0; y < outH; ++y)
    for (uint32_t x = 0; x < outW; ++x) {
      const auto ref = model(e.lumaRef, e.chromaRef, int(x), int(y), outW, outH, pc);
      const auto old = twoPass(e.lumaRef, e.chromaRef, int(x), int(y), outW, outH, pc);
      const int eye = x >= c.viewW ? 1 : 0;
      const float u = (float(x - eye * c.viewW) + .5f) / float(c.viewW), v = (float(y) + .5f) / float(outH);
      const float sy = sceneLuma(eye, u, v), sb = sceneCb(eye, u, v) - .5f, sr = sceneCr(eye, u, v) - .5f;
      bool centre = true;
      if (c.foveated) {
        const float m0 = eye ? 1 - u : u;
        const float c0x = (1 - c.f.centerSize[0]) / 2, c0y = (1 - c.f.centerSize[1]) / 2;
        const float lo = c0x * (c.f.shifts[eye][0] + 1) + .02f, hi = c0x * (c.f.shifts[eye][0] - 1) + 1 - .02f;
        const float loY = c0y * (c.f.shifts[eye][1] + 1) + .02f, hiY = c0y * (c.f.shifts[eye][1] - 1) + 1 - .02f;
        centre = m0 > lo && m0 < hi && v > loY && v < hiY;
      }
      for (int i = 0; i < 3; ++i) {
        const double gpu = out[(size_t(y) * outW + x) * 4 + i];
        const double error = std::fabs(gpu - ref[i] * 255.0);
        r.maxModel = std::max(r.maxModel, error);
        sumModel += error;
        if (sceneChecks) {
          // Interpolation quality: both models in float against the scene (the GPU output adds
          // 8-bit rounding); the GPU itself against the scene in the full-resolution centre.
          const double scene = std::clamp(m.row[i][0] * sy + m.row[i][1] * sb + m.row[i][2] * sr, 0.f, 1.f) * 255.0;
          sumScene += std::fabs(ref[i] * 255.0 - scene);
          sumTwoPass += std::fabs(old[i] * 255.0 - scene);
          if (centre) {
            r.maxCentreScene = std::max(r.maxCentreScene, std::fabs(gpu - scene));
            sumCentre += std::fabs(ref[i] * 255.0 - scene);
            sumCentreTwoPass += std::fabs(old[i] * 255.0 - scene);
            ++centreCount;
          }
        }
        ++count;
      }
    }
  r.meanModel = sumModel / double(count);
  r.meanScene = sumScene / double(count);
  r.meanSceneTwoPass = sumTwoPass / double(count);
  r.centreScene = centreCount ? sumCentre / double(centreCount) : 0;
  r.centreSceneTwoPass = centreCount ? sumCentreTwoPass / double(centreCount) : 0;
  return r;
}

// Pixel standard deviation of channel 1 (green, mostly luma) over a window of one eye.
double contrast(const std::vector<uint8_t> &img, uint32_t outW, uint32_t x0, uint32_t y0, uint32_t w, uint32_t h) {
  double sum = 0, sum2 = 0;
  for (uint32_t y = y0; y < y0 + h; ++y)
    for (uint32_t x = x0; x < x0 + w; ++x) {
      const double v = img[(size_t(y) * outW + x) * 4 + 1];
      sum += v;
      sum2 += v * v;
    }
  const double n = double(w) * h, mean = sum / n;
  return std::sqrt(std::max(0.0, sum2 / n - mean * mean));
}
} // namespace

int main(int argc, char **argv) {
  if (argc < 2) {
    std::printf("usage: fused_shader_tests fused.spv\n");
    return 2;
  }
  Gpu gpu;
  try {
    if (!gpu.open(argv[1])) {
      std::printf("SKIP: no Vulkan device with a compute queue\n");
      return 77;
    }
  } catch (const std::exception &e) {
    std::printf("SKIP: Vulkan unavailable (%s)\n", e.what());
    return 77;
  }
  std::printf("Vulkan device: %s\n", gpu.name.c_str());
  const vrost::Pose still{};
  try {
    // 1. Unchanged pose, not foveated, no sharpening: each pixel is its own texel converted.
    for (bool tenBit : {true, false}) {
      Stream c;
      c.tenBit = tenBit;
      const auto e = encode(c);
      const auto pc = constants(c, still, still, 0.f);
      const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, c.tenBit, c.viewW * 2, c.viewH, pc);
      const auto r = compare(c, e, out, pc, false);
      std::printf("%-46s model max %.2f mean %.3f\n", tenBit ? "identity, 10-bit" : "identity, 8-bit", r.maxModel, r.meanModel);
      CHECK(r.maxModel <= (tenBit ? 1.0 : 4.5), "identity differs from the model by %.2f", r.maxModel);
      // Luma at texel centres is exact: green follows the stored luma code to rounding.
      int worst = 0;
      for (uint32_t y = 0; y < c.viewH; ++y)
        for (uint32_t x = 0; x < c.viewW * 2; ++x) {
          const auto ref = model(e.lumaRef, e.chromaRef, int(x), int(y), c.viewW * 2, c.viewH, pc);
          worst = std::max(worst, std::abs(int(out[(size_t(y) * c.viewW * 2 + x) * 4 + 1]) - int(std::lround(ref[1] * 255))));
        }
      CHECK(worst <= (tenBit ? 1 : 4), "identity green differs by %d", worst);
    }
    // 2. Foveated streams, unchanged pose: the model, and the scene at least as well as RC4.2.
    struct Case {
      const char *name;
      Stream stream;
    };
    std::vector<Case> cases;
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 512;
      s.f = foveation(512, 512, .4f, 4.f, 0, 0, 0, 0);
      cases.push_back({"foveated 10-bit, eye-tracked preset, centred", s});
      s.f = foveation(512, 512, .4f, 4.f, .6f, -.5f, -.2f, -.5f);
      s.dither = 1.f;
      cases.push_back({"foveated 10-bit, gaze right/up, dither", s});
      s.tenBit = false;
      s.dither = 0;
      s.f = foveation(512, 480, .45f, 4.f, .4f, .1f, .4f, .1f);
      s.viewH = 480;
      cases.push_back({"foveated 8-bit, ALVR default centre", s});
      s.tenBit = true;
      s.viewW = 480;
      s.viewH = 448;
      s.f = foveation(480, 448, .7f, 2.f, .2f, 0, .2f, 0);
      cases.push_back({"foveated 10-bit, subtle preset", s});
      s.viewW = s.viewH = 384;
      s.f = foveation(384, 384, .5f, 3.f, 1.f, -1.f, -1.f, 1.f);
      cases.push_back({"foveated 10-bit, clamped extreme centres", s});
    }
    {
      // Limited range (what encoders send), BT.601 and centre-sited chroma, 8- and 10-bit.
      for (bool tenBit : {false, true}) {
        Stream s;
        s.tenBit = tenBit;
        s.foveated = true;
        s.viewW = s.viewH = 384;
        s.f = foveation(384, 384, .4f, 4.f, .3f, -.2f, -.3f, -.2f);
        s.chromaLocation = 2;
        const auto e = encode(s);
        const auto pc = constants(s, still, vrost::yawed(still, .5f), .7f, false, true);
        const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, s.tenBit, 768, 384, pc);
        const auto r = compare(s, e, out, pc, false);
        std::printf("%-46s model max %.2f mean %.3f\n", tenBit ? "limited range 10-bit, BT.601, sharpened" :
                    "limited range 8-bit, BT.601, sharpened", r.maxModel, r.meanModel);
        CHECK(r.maxModel <= (tenBit ? 1.5 : 4.5), "limited range differs from the model by %.2f", r.maxModel);
        CHECK(r.meanModel <= (tenBit ? .3 : .7), "limited range mean model error %.3f", r.meanModel);
      }
    }
    for (const auto &k : cases) {
      const auto e = encode(k.stream);
      const auto pc = constants(k.stream, still, still, 0.f);
      double ms = 0;
      const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, k.stream.tenBit, k.stream.viewW * 2, k.stream.viewH, pc, &ms);
      const auto r = compare(k.stream, e, out, pc, true);
      std::printf("%-46s model max %.2f mean %.3f | scene mean %.3f (two passes %.3f) centre max %.2f\n", k.name,
                  r.maxModel, r.meanModel, r.meanScene, r.meanSceneTwoPass, r.maxCentreScene);
      CHECK(r.maxModel <= (k.stream.tenBit ? 1.5 : 4.5), "%s: GPU differs from the model by %.2f", k.name, r.maxModel);
      CHECK(r.meanModel <= (k.stream.tenBit ? .3 : .6), "%s: mean model error %.3f", k.name, r.meanModel);
      CHECK(r.maxCentreScene <= 6.0, "%s: centre differs from the scene by %.2f", k.name, r.maxCentreScene);
      CHECK(r.meanScene <= r.meanSceneTwoPass + .05, "%s: one resampling should reproduce the scene at least as well "
            "as two (%.3f vs %.3f)", k.name, r.meanScene, r.meanSceneTwoPass);
    }
    // Detail under re-aim: RC4.2 expanded the encoded picture bilinearly (exact only where the
    // expansion is 1:1, the aligned centre), then resampled that again to re-aim it. One
    // Catmull-Rom resampling of the decoded planes keeps as much of the detail in the full-
    // resolution centre. 1.0.0.3: the reduced edges (two or more output pixels per texel) are
    // smoothed by a B-spline on purpose (test 6; until 1.0.0.2 Catmull-Rom kept more detail
    // there, with the overshoot that made them sparkle), so they are measured, not compared.
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 384;
      s.f = foveation(384, 384, .4f, 4.f, .2f, -.1f, -.2f, -.1f);
      auto detailed = [](int eye, float u, float v) {
        return .5f + .2f * std::sin(6.28318f * u * 14.f + eye) * std::sin(6.28318f * v * 12.f) + .1f * (sceneY(eye, u, v) - .5f);
      };
      const auto e = encode(s, detailed);
      const vrost::Pose turned = vrost::yawed(still, .37f);
      const auto pc = constants(s, still, turned, 0.f);
      const uint32_t outW = 768, outH = 384, eyeW = 384;
      // RC4.2's first pass, in float.
      std::vector<std::array<float, 3>> expanded(size_t(outW) * outH);
      const auto stillPc = constants(s, still, still, 0.f);
      for (uint32_t y = 0; y < outH; ++y)
        for (uint32_t x = 0; x < outW; ++x) expanded[size_t(y) * outW + x] = twoPass(e.lumaRef, e.chromaRef, int(x), int(y), outW, outH, stillPc);
      auto tap = [&](float px, float py, int c) {
        const float fx = px - .5f, fy = py - .5f;
        const int x0 = int(std::floor(fx)), y0 = int(std::floor(fy));
        const float ax = fx - x0, ay = fy - y0;
        auto at = [&](int xi, int yi) {
          return expanded[size_t(std::clamp(yi, 0, int(outH) - 1)) * outW + std::clamp(xi, 0, int(outW) - 1)][c];
        };
        return (at(x0, y0) * (1 - ax) + at(x0 + 1, y0) * ax) * (1 - ay) + (at(x0, y0 + 1) * (1 - ax) + at(x0 + 1, y0 + 1) * ax) * ay;
      };
      const auto m = vrost::conversionMatrix(.2126f, .0722f);
      double fusedError = 0, chainError = 0, fusedEdges = 0, chainEdges = 0;
      size_t count = 0, edgeCount = 0;
      for (uint32_t y = 0; y < outH; ++y)
        for (uint32_t x = 0; x < outW; ++x) {
          const int eye = x >= eyeW ? 1 : 0;
          const float u = (float(x - eye * eyeW) + .5f) / eyeW, v = (float(y) + .5f) / outH;
          const float(*rows)[4] = pc.eyeRows[eye];
          const float hx = rows[0][0] * u + rows[0][1] * v + rows[0][2], hy = rows[1][0] * u + rows[1][1] * v + rows[1][2],
                      hz = rows[2][0] * u + rows[2][1] * v + rows[2][2];
          const float sx = hx / hz, sy = hy / hz;
          if (sx < .01f || sx > .99f || sy < .01f || sy > .99f) continue; // away from the faded border
          float ex, ey, slopeX, slopeY;
          encodedAt(pc, eye, sx, sy, ex, ey, &slopeX, &slopeY);
          const float texels = std::min(slopeX * e.lumaRef.w * .5f / float(eyeW), slopeY * e.lumaRef.h / float(outH));
          if (texels < 1.f && texels > .5f) continue; // the band between, blended
          const bool edges = texels <= .5f;
          // RC4.2's second pass: Catmull-Rom of the expanded picture (reaim.comp).
          const float px = sx * eyeW + eye * eyeW, py = sy * outH;
          const float lox = eye * eyeW + .5f, hix = (eye + 1) * eyeW - .5f, loy = .5f, hiy = outH - .5f;
          const float p1x = std::floor(px - .5f) + .5f, p1y = std::floor(py - .5f) + .5f;
          float wx[4], wy[4];
          auto weights = [](float f, float w[4]) {
            w[0] = f * (-.5f + f * (1 - .5f * f));
            w[1] = 1 + f * f * (-2.5f + 1.5f * f);
            w[2] = f * (.5f + f * (2 - 1.5f * f));
            w[3] = f * f * (-.5f + .5f * f);
          };
          weights(px - p1x, wx);
          weights(py - p1y, wy);
          const float xs[3] = {std::clamp(p1x - 1, lox, hix), std::clamp(p1x + wx[2] / (wx[1] + wx[2]), lox, hix), std::clamp(p1x + 2, lox, hix)};
          const float ys[3] = {std::clamp(p1y - 1, loy, hiy), std::clamp(p1y + wy[2] / (wy[1] + wy[2]), loy, hiy), std::clamp(p1y + 2, loy, hiy)};
          const float cx[3] = {wx[0], wx[1] + wx[2], wx[3]}, cy[3] = {wy[0], wy[1] + wy[2], wy[3]};
          const auto fused = model(e.lumaRef, e.chromaRef, int(x), int(y), outW, outH, pc);
          const float ly = detailed(eye, sx, sy), lb = sceneCb(eye, sx, sy) - .5f, lr = sceneCr(eye, sx, sy) - .5f;
          for (int c = 0; c < 3; ++c) {
            float chain = 0;
            for (int j = 0; j < 3; ++j)
              for (int i = 0; i < 3; ++i) chain += tap(xs[i], ys[j], c) * cx[i] * cy[j];
            const float truth = std::clamp(m.row[c][0] * ly + m.row[c][1] * lb + m.row[c][2] * lr, 0.f, 1.f);
            if (edges) {
              fusedEdges += std::fabs(fused[c] - truth) * 255;
              chainEdges += std::fabs(std::clamp(chain, 0.f, 1.f) - truth) * 255;
              ++edgeCount;
              continue;
            }
            fusedError += std::fabs(fused[c] - truth) * 255;
            chainError += std::fabs(std::clamp(chain, 0.f, 1.f) - truth) * 255;
            ++count;
          }
        }
      fusedError /= double(count);
      chainError /= double(count);
      fusedEdges /= double(std::max<size_t>(1, edgeCount));
      chainEdges /= double(std::max<size_t>(1, edgeCount));
      const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, true, outW, outH, pc);
      const auto r = compare(s, e, out, pc, false);
      std::printf("%-46s centre scene mean %.3f (RC4.2 two passes %.3f), smoothed edges %.3f (%.3f)\n",
                  "detail, re-aimed 0.37 degrees", fusedError, chainError, fusedEdges, chainEdges);
      CHECK(r.maxModel <= 1.5, "detail differs from the model by %.2f", r.maxModel);
      CHECK(count > 1000 && edgeCount > 1000, "both regions measured (%zu, %zu)", count, edgeCount);
      CHECK(fusedError <= chainError * 1.05 + .02, "one resampling should keep as much detail as two in the centre "
            "(%.3f vs %.3f)", fusedError, chainError);
    }
    // 3. Re-aimed: head turned 2 degrees left on a foveated stream.
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 256;
      s.f = foveation(256, 256, .4f, 4.f, .2f, .1f, -.1f, .1f);
      // A bright vertical line at each eye's view centre makes the movement measurable.
      auto lined = [](int eye, float u, float v) { return std::fabs(u - .5f) < .006f ? .95f : sceneY(eye, u, v) * .6f; };
      const auto e = encode(s, lined);
      const auto pc = constants(s, still, vrost::yawed(still, 2.f), 0.f);
      const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, s.tenBit, 512, 256, pc);
      const auto r = compare(s, e, out, pc, false);
      std::printf("%-46s model max %.2f mean %.3f\n", "re-aimed 2 degrees, foveated", r.maxModel, r.meanModel);
      CHECK(r.maxModel <= 2.0, "re-aimed frame differs from the model by %.2f", r.maxModel);
      const auto stillOut = gpu.run(e.luma, e.chroma, e.w, e.h, s.tenBit, 512, 256, constants(s, still, still, 0.f));
      auto centroid = [&](const std::vector<uint8_t> &img) {
        double sum = 0, weight = 0;
        for (int x = 100; x < 160; ++x) {
          const int g = img[(size_t(128) * 512 + x) * 4 + 1];
          if (g > 190) {
            sum += (x + .5) * (g - 190);
            weight += g - 190;
          }
        }
        return weight > 0 ? sum / weight : -1.0;
      };
      const double before = centroid(stillOut), after = centroid(out);
      // At the view centre: tan(2 deg) / (2 tan 45 deg) * 256 = 4.47 pixels to the right.
      std::printf("  centre line %.2f -> %.2f\n", before, after);
      CHECK(std::fabs(after - before - 4.47) < .5, "the centre line moved from %.2f to %.2f", before, after);
      auto rightCentroid = [&](const std::vector<uint8_t> &img) {
        double sum = 0, weight = 0;
        for (int x = 356; x < 416; ++x) {
          const int g = img[(size_t(128) * 512 + x) * 4 + 1];
          if (g > 190) {
            sum += (x + .5) * (g - 190);
            weight += g - 190;
          }
        }
        return weight > 0 ? sum / weight : -1.0;
      };
      const double rightBefore = rightCentroid(stillOut), rightAfter = rightCentroid(out);
      std::printf("  right eye centre line %.2f -> %.2f\n", rightBefore, rightAfter);
      CHECK(std::fabs(rightAfter - rightBefore - 4.47) < .5, "the right eye's line moved from %.2f to %.2f", rightBefore,
            rightAfter);
      // A real headset's asymmetric, mirrored and canted eyes: each eye uses its own homography.
      const auto realPc = constants(s, still, vrost::yawed(still, 2.f), 0.f, true, false, true);
      const auto realOut = gpu.run(e.luma, e.chroma, e.w, e.h, s.tenBit, 512, 256, realPc);
      const auto real = compare(s, e, realOut, realPc, false);
      std::printf("%-46s model max %.2f mean %.3f\n", "re-aimed, asymmetric canted eyes", real.maxModel, real.meanModel);
      CHECK(real.maxModel <= 2.0, "asymmetric eyes differ from the model by %.2f", real.maxModel);
      CHECK(std::memcmp(realPc.eyeRows[0], realPc.eyeRows[1], sizeof(realPc.eyeRows[0])) != 0, "the eyes' rows differ");
      const auto far = gpu.run(e.luma, e.chroma, e.w, e.h, s.tenBit, 512, 256, constants(s, still, vrost::yawed(still, 60.f), 0.f));
      const uint8_t *edge = &far[(size_t(128) * 512 + 2) * 4];
      CHECK(edge[0] == 0 && edge[1] == 0 && edge[2] == 0, "outside the rendered view must be black: %u %u %u", edge[0],
            edge[1], edge[2]);
    }
    // 4. Sharpening.
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 384;
      s.f = foveation(384, 384, .4f, 4.f, 0, 0, 0, 0);
      // Fine detail (an 8-pixel pattern) over the smooth scene, as in textures and text.
      auto detailed = [](int eye, float u, float v) {
        return .5f + .18f * std::sin(6.28318f * u * 48.f) * std::sin(6.28318f * v * 48.f) + .1f * (sceneY(eye, u, v) - .5f);
      };
      const auto e = encode(s, detailed);
      const auto plain = gpu.run(e.luma, e.chroma, e.w, e.h, true, 768, 384, constants(s, still, still, 0.f));
      const auto pc = constants(s, still, still, vrost::sharpeningAmount(70));
      const auto sharp = gpu.run(e.luma, e.chroma, e.w, e.h, true, 768, 384, pc);
      const auto r = compare(s, e, sharp, pc, false);
      std::printf("%-46s model max %.2f mean %.3f\n", "sharpening 0.7, foveated", r.maxModel, r.meanModel);
      CHECK(r.maxModel <= 1.5, "sharpened frame differs from the model by %.2f", r.maxModel);
      // More local contrast in the full-resolution centre of each eye...
      for (int eye = 0; eye < 2; ++eye) {
        const double before = contrast(plain, 768, eye * 384 + 172, 172, 40, 40);
        const double after = contrast(sharp, 768, eye * 384 + 172, 172, 40, 40);
        std::printf("  eye %d centre contrast %.2f -> %.2f\n", eye, before, after);
        CHECK(after > before * 1.05, "sharpening should raise the centre's local contrast (%.2f -> %.2f)", before, after);
      }
      // ...and the reduced foveation edges stay as they were (one output pixel spans a quarter texel there).
      int changed = 0;
      for (uint32_t y = 0; y < 384; ++y)
        for (uint32_t x = 0; x < 20; ++x)
          for (int i = 0; i < 3; ++i) changed += plain[(size_t(y) * 768 + x) * 4 + i] != sharp[(size_t(y) * 768 + x) * 4 + i];
      CHECK(changed == 0, "the reduced left edge changed in %d values", changed);
      // A flat picture stays flat.
      auto flat = [](int, float, float) { return .45f; };
      const auto fe = encode(s, flat);
      const auto flatSharp = gpu.run(fe.luma, fe.chroma, fe.w, fe.h, true, 768, 384, pc);
      const auto flatPlain = gpu.run(fe.luma, fe.chroma, fe.w, fe.h, true, 768, 384, constants(s, still, still, 0.f));
      int worst = 0;
      for (size_t i = 0; i < flatSharp.size(); ++i) worst = std::max(worst, std::abs(int(flatSharp[i]) - int(flatPlain[i])));
      CHECK(worst <= 1, "sharpening changed a flat picture by %d", worst);
    }
    // 6. 1.0.0.3: the reduced edges, enlarged two to five times, no longer overshoot or shimmer.
    // The hardest detail for them: encoded texels alternating dark and light, then a step. A
    // sample position swept across them in 1/16-texel steps stands for the head or the gaze
    // moving the enlarged texels under the output pixels.
    {
      Plane plane{64, 16, 1, std::vector<float>(64 * 16)};
      for (uint32_t y = 0; y < 16; ++y)
        for (uint32_t x = 0; x < 64; ++x) plane.values[y * 64 + x] = x < 32 ? (x % 2 ? .8f : .2f) : x < 48 ? .2f : .8f;
      double flickerSharp = 0, flickerSmooth = 0;
      float lastSharp = 0, lastSmooth = 0;
      for (int i = 0; i <= 256; ++i) {
        const float px = 8.f + float(i) / 16.f;
        const float sharp = catmullRom(plane, px, 8.5f, .5f, .5f, 63.5f, 15.5f);
        const float smooth = bSpline(plane, px, 8.5f, .5f, .5f, 63.5f, 15.5f);
        if (i) {
          flickerSharp += std::fabs(sharp - lastSharp);
          flickerSmooth += std::fabs(smooth - lastSmooth);
        }
        lastSharp = sharp;
        lastSmooth = smooth;
      }
      float lowSharp = 1, highSharp = 0, lowSmooth = 1, highSmooth = 0;
      for (int i = 0; i <= 256; ++i) {
        const float px = 40.f + float(i) / 16.f;
        const float sharp = catmullRom(plane, px, 8.5f, .5f, .5f, 63.5f, 15.5f);
        const float smooth = bSpline(plane, px, 8.5f, .5f, .5f, 63.5f, 15.5f);
        lowSharp = std::min(lowSharp, sharp);
        highSharp = std::max(highSharp, sharp);
        lowSmooth = std::min(lowSmooth, smooth);
        highSmooth = std::max(highSmooth, smooth);
      }
      std::printf("%-46s change %.2f (Catmull-Rom %.2f), step %.3f..%.3f (Catmull-Rom %.3f..%.3f)\n",
                  "reduced edges: moving detail and a step", flickerSmooth, flickerSharp, lowSmooth, highSmooth, lowSharp,
                  highSharp);
      CHECK(flickerSmooth < flickerSharp * .6, "enlarged detail should change less as it moves (%.2f vs %.2f)",
            flickerSmooth, flickerSharp);
      CHECK(lowSmooth >= .2f - 1e-4f && highSmooth <= .8f + 1e-4f, "the B-spline overshoots a step (%.3f..%.3f)", lowSmooth,
            highSmooth);
      CHECK(lowSharp < .18f && highSharp > .82f, "Catmull-Rom (1.0.0.2's edges) should overshoot it (%.3f..%.3f)", lowSharp,
            highSharp);
      // The pass itself: where the stream is reduced, the GPU follows the model (B-spline), and
      // a step there stays inside its two levels.
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 256;
      s.f = foveation(256, 256, .3f, 5.f, 0.f, 0.f, 0.f, 0.f);
      auto stepped = [](int, float u, float v) { return (u < .13f || (v > .1f && v < .12f)) ? .8f : .2f; };
      const auto e = encode(s, stepped);
      const auto pc = constants(s, still, still, 0.f);
      const auto out = gpu.run(e.luma, e.chroma, e.w, e.h, true, 512, 256, pc);
      const auto r = compare(s, e, out, pc, false, stepped);
      int beyond = 0;
      for (uint32_t y = 0; y < 256; ++y)
        for (uint32_t x = 0; x < 256; ++x) {
          float ex, ey, slopeX, slopeY;
          const float u = (float(x) + .5f) / 256.f, v = (float(y) + .5f) / 256.f;
          encodedAt(pc, 0, u, v, ex, ey, &slopeX, &slopeY);
          if (std::min(slopeX * e.lumaRef.w * .5f, slopeY * float(e.lumaRef.h)) / 256.f > .5f) continue;
          const float model = modelLuma(e.lumaRef, int(x), int(y), 512, 256, pc);
          beyond += model < .2f - .004f || model > .8f + .004f;
        }
      std::printf("%-46s model max %.2f, %d luma values beyond the step\n", "reduced edges on the GPU", r.maxModel, beyond);
      CHECK(r.maxModel <= 1.5, "reduced edges differ from the model by %.2f", r.maxModel);
      CHECK(beyond == 0, "%d reduced-edge values overshoot the step", beyond);
    }
    // 5. Eye boundary: constant colours per eye never bleed, re-aimed and sharpened.
    {
      Stream s;
      s.tenBit = false;
      s.foveated = true;
      s.viewW = s.viewH = 256;
      s.f = foveation(256, 256, .45f, 4.f, .4f, .1f, .4f, .1f);
      const uint32_t encW = s.f.encoded[0], encH = s.f.encoded[1];
      std::vector<uint16_t> luma(size_t(encW) * 2 * encH), chroma(size_t(encW) * encH, 128);
      for (uint32_t y = 0; y < encH; ++y)
        for (uint32_t x = 0; x < encW * 2; ++x) luma[size_t(y) * encW * 2 + x] = x < encW ? 40 : 220;
      const auto conversion = vrost::conversionPushConstants(vrost::conversionMatrix(.2126f, .0722f), vrost::rangeConstants(8, true, false),
                                                          vrost::chromaSiting(0), 0.f, s.f);
      const auto pc = vrost::fusedParameters(conversion, vrost::reaimConstants(still, vrost::yawed(still, 1.f), eyes()).rows, 1.2f);
      const auto out = gpu.run(luma, chroma, encW * 2, encH, false, 512, 256, pc);
      Plane lumaRef{encW * 2, encH, 1, {}}, chromaRef{encW, encH / 2, 2, std::vector<float>(size_t(encW) * (encH / 2) * 2, 128 / 255.f)};
      for (auto value : luma) lumaRef.values.push_back(value / 255.f);
      int bleed = 0;
      for (uint32_t y = 0; y < 256; ++y)
        for (uint32_t x = 0; x < 512; ++x) {
          const int g = out[(size_t(y) * 512 + x) * 4 + 1];
          const int expected = int(std::lround(model(lumaRef, chromaRef, int(x), int(y), 512, 256, pc)[1] * 255));
          // Only this eye's colour (faded towards black where the turned view leaves the rendered one).
          if (std::abs(g - expected) > 1 || g > (x < 256 ? 40 : 220)) {
            if (bleed < 12) std::printf("  differs at %u,%u: %d (model %d)\n", x, y, g, expected);
            ++bleed;
          }
        }
      std::printf("%-46s %d pixels differ\n", "eye boundary, re-aimed and sharpened", bleed);
      CHECK(bleed == 0, "eye colours bled across the boundary in %d pixels", bleed);
    }
    // 6. The parameters, and the frame's constants made from them, carry what the shader reads.
    {
      vrost::FoveationParams f = foveation(512, 512, .4f, 4.f, .3f, -.2f, .1f, .4f);
      const auto conversion = vrost::conversionPushConstants(vrost::conversionMatrix(.299f, .114f), vrost::rangeConstants(10, false, true),
                                                          vrost::chromaSiting(2), 1.f, f);
      const auto reaim = vrost::reaimConstants(still, vrost::yawed(still, 3.f), eyes());
      const auto &rows = reaim.rows;
      const auto pc = vrost::fusedParameters(conversion, rows, 5.f);
      CHECK(pc.adjustment[3] == 2.f, "sharpening is limited to 2 (%f)", pc.adjustment[3]);
      CHECK(pc.sitingView[2] == f.viewRatio[0] && pc.sitingView[3] == f.viewRatio[1], "view ratio");
      CHECK(pc.fovShifts[0] == f.shifts[0][0] && pc.fovShifts[3] == f.shifts[1][1], "shifts");
      CHECK(std::memcmp(pc.eyeRows, rows, sizeof(pc.eyeRows)) == 0, "homographies");
      CHECK(vrost::fusedParameters(conversion, rows, NAN).adjustment[3] == 0.f, "a broken amount means no sharpening");
      CHECK(vrost::sharpeningAmount(-5) == 0.f && vrost::sharpeningAmount(70) == .7f && vrost::sharpeningAmount(900) == 2.f, "levels");
      CHECK(std::fabs(vrost::foveationRegionCentre(.4f, .5f, false) - .65f) < 1e-6f, "region centre");
      CHECK(std::fabs(vrost::foveationRegionCentre(.4f, .5f, true) - .35f) < 1e-6f, "mirrored region centre");
      // RC4.4: the analytic slope is the inverse's derivative (one more division, no extra
      // evaluations): 1 texel per pixel in the centre, 1/(2 x edge ratio - 1) at the outer edge.
      double worstSlope = 0;
      for (float size : {.3f, .4f, .55f, .7f})
        for (float edge : {2.f, 3.f, 4.f, 5.f})
          for (float shift : {-.6f, 0.f, .2f, .6f})
            for (int i = 1; i < 400; ++i) {
              const double u = i / 400.0, h = 1e-6;
              const double fd = (vrost::foveationDecompressAxis<double>(u + h, size, shift, edge) -
                                 vrost::foveationDecompressAxis<double>(u - h, size, shift, edge)) / (2 * h);
              const double slope = vrost::foveationDecompressSlope<double>(u, size, shift, edge);
              worstSlope = std::max(worstSlope, std::fabs(fd - slope) / slope);
            }
      CHECK(worstSlope < 1e-3, "foveation slope differs from the derivative by %.2e", worstSlope);
      const double c2 = 3.0 * .4 + 1, centreSlope = vrost::foveationDecompressSlope<double>(.5, .4, 0, 4) * c2 / 4;
      const double edgeSlope = vrost::foveationDecompressSlope<double>(0, .4, 0, 4) * c2 / 4;
      CHECK(std::fabs(centreSlope - 1) < 1e-9 && std::fabs(edgeSlope - 1 / 7.0) < 1e-6, "texel density %.4f / %.4f",
            centreSlope, edgeSlope);
      // 1.0.0.11: the per-frame constants reproduce the inverse term by term (in double), the mirrored right eye
      // and its offset included, to a thousandth of a texel, and the texels per output pixel to 0.1 %.
      double worstTexel = 0, worstDensity = 0;
      for (float size : {.3f, .4f, .55f, .7f})
        for (float edge : {1.f, 2.f, 3.f, 4.f, 5.f, 8.f})
          for (float shift : {-1.f, -.6f, 0.f, .2f, .6f, 1.f}) {
            const uint32_t lumaW = 2 * 1408, lumaH = 1344, outEye = 2560, outH = 2448;
            vrost::FusedParameters q{};
            q.row2[3] = 1;
            q.fovSize[0] = q.fovSize[1] = size;
            q.fovSize[2] = q.fovSize[3] = edge;
            q.sitingView[2] = .97f;
            q.sitingView[3] = .99f;
            q.fovShifts[0] = shift;
            q.fovShifts[1] = -shift;
            q.fovShifts[2] = -shift * .5f;
            q.fovShifts[3] = shift * .5f;
            const auto frame = vrost::fusedFrameConstants(q, lumaW, lumaH, outEye, outH);
            for (int eye = 0; eye < 2; ++eye)
              for (int axis = 0; axis < 2; ++axis)
                for (int i = 0; i <= 512; ++i) {
                  const double u = i / 512.0;
                  const double shiftHere = q.fovShifts[eye * 2 + axis], vr = q.sitingView[2 + axis];
                  const double extent = axis ? lumaH : lumaW * .5, pixels = axis ? outH : outEye;
                  const bool mirrored = eye == 1 && axis == 0;
                  const double m = mirrored ? 1 - u : u;
                  const double x = vrost::foveationDecompressAxis<double>(m, size, shiftHere, edge);
                  double expected = vr * extent * x;
                  if (mirrored) expected = 2 * extent - expected;
                  const double density = vr * extent / pixels * vrost::foveationDecompressSlope<double>(m, size, shiftHere, edge);
                  float texel = 0, perPixel = 0;
                  vrost::fusedAxisTexel(frame.eye[eye].axis[axis], float(u), texel, perPixel);
                  if (!std::isfinite(expected)) continue; // the term-by-term form divides by zero at edge ratio 1
                  worstTexel = std::max(worstTexel, std::fabs(texel - expected));
                  worstDensity = std::max(worstDensity, std::fabs(perPixel - density) / density);
                }
          }
      std::printf("%-46s texel %.2e, density %.2e\n", "per-frame foveation constants vs the inverse", worstTexel,
                  worstDensity);
      CHECK(worstTexel < 2e-3, "the per-frame constants place a texel %.2e away", worstTexel);
      CHECK(worstDensity < 1e-3, "the per-frame constants' texel density differs by %.2e", worstDensity);
      // Not foveated: the identity, each eye in its half; edge ratio 1 and centres at the border stay finite.
      vrost::FusedParameters plainParameters{};
      const auto plainFrame = vrost::fusedFrameConstants(plainParameters, 2 * 1000, 900, 1000, 900);
      float texel = 0, perPixel = 0;
      vrost::fusedAxisTexel(plainFrame.eye[1].axis[0], .25f, texel, perPixel);
      CHECK(std::fabs(texel - 1250.f) < 1e-3f && std::fabs(perPixel - 1.f) < 1e-6f, "identity right eye %.4f %.4f", texel,
            perPixel);
      vrost::fusedAxisTexel(plainFrame.eye[0].axis[1], .5f, texel, perPixel);
      CHECK(std::fabs(texel - 450.f) < 1e-3f, "identity vertical %.4f", texel);
      // The std140 layout fused.comp reads (struct Frame, Eye and Axis there).
      CHECK(offsetof(vrost::FusedFrame, eye) == 96 && sizeof(vrost::FusedEye) == 224 &&
                offsetof(vrost::FusedEye, axis) == 48 && offsetof(vrost::FusedEye, lumaBounds) == 176 &&
                offsetof(vrost::FusedEye, chromaHigh) == 208,
            "uniform buffer layout");
      const auto frame = vrost::fusedFrameConstants(pc, 2 * 704, 672, 1280, 1224);
      CHECK(frame.eye[1].lumaBounds[0] == 704.5f && frame.eye[1].lumaBounds[2] == 1407.5f &&
                frame.eye[0].lumaBounds[3] == 671.5f,
            "luma bounds %.1f %.1f %.1f", frame.eye[1].lumaBounds[0], frame.eye[1].lumaBounds[2], frame.eye[0].lumaBounds[3]);
      CHECK(frame.eye[1].chromaLow[0] == 352.5f && frame.eye[1].chromaHigh[0] == 703.5f && frame.outputEye[0] == 1280.f,
            "chroma bounds and output size");
      CHECK(std::memcmp(frame.eye[1].rows, pc.eyeRows[1], sizeof(frame.eye[1].rows)) == 0, "the right eye's rows");
    }
    // 7. 1.0.0.11: pixels outside the visible spans are not computed and stay black; those inside are unchanged.
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 256;
      s.f = foveation(256, 256, .4f, 4.f, .2f, -.1f, -.2f, -.1f);
      s.dither = 1.f;
      const auto e = encode(s);
      const auto pc = constants(s, still, vrost::yawed(still, 1.5f), .7f);
      vrost::VisibleArea area;
      for (int eye = 0; eye < 2; ++eye)
        for (int c = 0; c < 3; ++c)
          for (int i = 0; i < 96; ++i) {
            const float a = 6.2831853f * float(i) / 96.f, r = .5f + .015f * float(c);
            area.outlines[eye][c].push_back({.5f + (eye ? -.04f : .04f) + r * std::cos(a), .5f + .47f * std::sin(a)});
          }
      std::vector<uint32_t> spans;
      for (int eye = 0; eye < 2; ++eye) {
        const auto rows = vrost::visibleSpans(area, eye, 256, 256, 8);
        spans.insert(spans.end(), rows.begin(), rows.end());
      }
      const auto full = gpu.run(e.luma, e.chroma, e.w, e.h, true, 512, 256, pc);
      const auto skipped = gpu.run(e.luma, e.chroma, e.w, e.h, true, 512, 256, pc, nullptr, &spans);
      int differ = 0, notBlack = 0, outside = 0;
      for (uint32_t y = 0; y < 256; ++y)
        for (uint32_t x = 0; x < 512; ++x) {
          const int eye = x >= 256 ? 1 : 0;
          const uint32_t local = x - eye * 256, span = spans[size_t(eye) * 256 + y];
          const size_t i = (size_t(y) * 512 + x) * 4;
          if (local >= vrost::spanFirst(span) && local < vrost::spanEnd(span)) {
            differ += std::memcmp(&full[i], &skipped[i], 4) != 0;
          } else {
            ++outside;
            notBlack += skipped[i] != 0 || skipped[i + 1] != 0 || skipped[i + 2] != 0 || skipped[i + 3] != 255;
          }
        }
      std::printf("%-46s %d skipped, %d inside differ, %d outside not black\n", "visible spans", outside, differ, notBlack);
      CHECK(outside > 5000, "the outline should leave the corners out (%d)", outside);
      CHECK(differ == 0, "pixels inside the spans changed in %d places", differ);
      CHECK(notBlack == 0, "%d skipped pixels are not opaque black", notBlack);
    }
    // Time on this device for the record (lavapipe runs on the CPU: relative only).
    {
      Stream s;
      s.foveated = true;
      s.viewW = s.viewH = 1024;
      s.f = foveation(1024, 1024, .4f, 4.f, 0, 0, 0, 0);
      const auto e = encode(s);
      double plainMs = 0, sharpMs = 0;
      gpu.run(e.luma, e.chroma, e.w, e.h, true, 2048, 1024, constants(s, still, vrost::yawed(still, 1.f), 0.f)); // warm-up
      gpu.run(e.luma, e.chroma, e.w, e.h, true, 2048, 1024, constants(s, still, vrost::yawed(still, 1.f), 0.f), &plainMs);
      gpu.run(e.luma, e.chroma, e.w, e.h, true, 2048, 1024, constants(s, still, vrost::yawed(still, 1.f), .7f), &sharpMs);
      std::printf("%-46s %.1f ms, with sharpening %.1f ms (2048 x 1024 on %s)\n", "fused pass time", plainMs, sharpMs,
                  gpu.name.c_str());
    }
  } catch (const std::exception &e) {
    std::printf("ERROR %s\n", e.what());
    return 1;
  }
  std::printf(failures ? "Fused shader tests FAILED (%d)\n" : "Fused shader tests passed\n", failures);
  return failures ? 1 : 0;
}
