// Runs the real re-aim shader (client/platform/linux/reaim.comp) on a Vulkan device —
// normally Mesa's software rasteriser (lavapipe) — and checks it against core/reaim.hpp:
//  1. an unchanged head pose reproduces the source image exactly;
//  2. a small head turn matches a CPU reference of the same Catmull-Rom sampling, and
//     a feature moves the way the world appears to move (turning left moves it right);
//  3. directions outside the rendered view are black and taps never cross between eyes.
// Usage: reaim_shader_tests <reaim.spv>. Exit 77 (skip) if no Vulkan device exists.
#include "core/reaim.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
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
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
    std::vector<VkQueueFamilyProperties> families(count);
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, families.data());
    family = UINT32_MAX;
    for (uint32_t i = 0; i < count; ++i)
      if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { family = i; break; }
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
    VkDescriptorSetLayoutBinding bindings[2]{};
    for (int i = 0; i < 2; ++i) {
      bindings[i].binding = i;
      bindings[i].descriptorCount = 1;
      bindings[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dl.bindingCount = 2;
    dl.pBindings = bindings;
    vk(vkCreateDescriptorSetLayout(device, &dl, nullptr, &setLayout), "set layout");
    VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(vrost::ReaimPushConstants)};
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
    VkDescriptorPoolSize sizes[] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1}};
    VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dp.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
    dp.maxSets = 1;
    dp.poolSizeCount = 2;
    dp.pPoolSizes = sizes;
    vk(vkCreateDescriptorPool(device, &dp, nullptr, &descriptorPool), "descriptor pool");
    return true;
  }
  struct Buffer { VkBuffer buffer = VK_NULL_HANDLE; VkDeviceMemory memory = VK_NULL_HANDLE; void *mapped = nullptr; };
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
    ai.memoryTypeIndex = memoryType(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
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
  struct Image { VkImage image = VK_NULL_HANDLE; VkDeviceMemory memory = VK_NULL_HANDLE; VkImageView view = VK_NULL_HANDLE; };
  Image image(uint32_t w, uint32_t h, VkImageUsageFlags usage) {
    Image im;
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D;
    ci.format = VK_FORMAT_R8G8B8A8_UNORM;
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
    vi.format = VK_FORMAT_R8G8B8A8_UNORM;
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
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0,
                         nullptr, 1, &b);
  }
  std::vector<uint8_t> run(const std::vector<uint8_t> &rgba, uint32_t w, uint32_t h, const vrost::ReaimPushConstants &pc) {
    auto source = image(w, h, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    auto output = image(w, h, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
    auto staging = buffer(rgba.size(), VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
    auto readback = buffer(rgba.size(), VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    std::memcpy(staging.mapped, rgba.data(), rgba.size());
    VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dai.descriptorPool = descriptorPool;
    dai.descriptorSetCount = 1;
    dai.pSetLayouts = &setLayout;
    VkDescriptorSet set;
    vk(vkAllocateDescriptorSets(device, &dai, &set), "descriptor set");
    VkDescriptorImageInfo infos[2] = {{sampler, source.view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                      {VK_NULL_HANDLE, output.view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet writes[2]{};
    for (int i = 0; i < 2; ++i) {
      writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      writes[i].dstSet = set;
      writes[i].dstBinding = i;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      writes[i].pImageInfo = &infos[i];
    }
    vkUpdateDescriptorSets(device, 2, writes, 0, nullptr);
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool;
    ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vk(vkAllocateCommandBuffers(device, &ai, &cmd), "command buffer");
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vkBeginCommandBuffer(cmd, &bi);
    barrier(cmd, source.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    barrier(cmd, output.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
    VkBufferImageCopy region{};
    region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.imageExtent = {w, h, 1};
    vkCmdCopyBufferToImage(cmd, staging.buffer, source.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
    barrier(cmd, source.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1, &set, 0, nullptr);
    vkCmdPushConstants(cmd, pipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc), &pc);
    vkCmdDispatch(cmd, (w + 15) / 16, (h + 15) / 16, 1);
    barrier(cmd, output.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
    vkCmdCopyImageToBuffer(cmd, output.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &region);
    vkEndCommandBuffer(cmd);
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    vk(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "submit");
    vk(vkQueueWaitIdle(queue), "wait");
    vkFreeCommandBuffers(device, pool, 1, &cmd);
    std::vector<uint8_t> result(rgba.size());
    std::memcpy(result.data(), readback.mapped, result.size());
    vkFreeDescriptorSets(device, descriptorPool, 1, &set);
    free(staging);
    free(readback);
    free(source);
    free(output);
    return result;
  }
};

constexpr uint32_t EyeW = 96, H = 64, W = EyeW * 2;

// A smooth image with a few sharp features: different in each eye so mixing shows up.
std::vector<uint8_t> scene() {
  std::vector<uint8_t> rgba(size_t(W) * H * 4);
  for (uint32_t y = 0; y < H; ++y)
    for (uint32_t x = 0; x < W; ++x) {
      const uint32_t eye = x / EyeW, local = x % EyeW;
      uint8_t *p = &rgba[(size_t(y) * W + x) * 4];
      p[0] = uint8_t(40 + local * 2);
      p[1] = uint8_t(30 + y * 3);
      p[2] = uint8_t(eye ? 220 : 20);
      if (local == EyeW / 2) p[0] = p[1] = 250; // bright vertical line at the view centre
      p[3] = 255;
    }
  return rgba;
}

// CPU model of the shader: bilinear taps at the same clamped positions.
float tap(const std::vector<uint8_t> &img, float px, float py, int c) {
  const float x = px - .5f, y = py - .5f;
  const int x0 = int(std::floor(x)), y0 = int(std::floor(y));
  const float fx = x - x0, fy = y - y0;
  auto at = [&](int xi, int yi) {
    xi = std::clamp(xi, 0, int(W) - 1);
    yi = std::clamp(yi, 0, int(H) - 1);
    return img[(size_t(yi) * W + xi) * 4 + c] / 255.f;
  };
  return (at(x0, y0) * (1 - fx) + at(x0 + 1, y0) * fx) * (1 - fy) + (at(x0, y0 + 1) * (1 - fx) + at(x0 + 1, y0 + 1) * fx) * fy;
}

float reference(const std::vector<uint8_t> &img, const vrost::ReaimPushConstants &pc, uint32_t x, uint32_t y, int c) {
  const int eye = x >= EyeW ? 1 : 0;
  const float u = (float(x - eye * EyeW) + .5f) / EyeW, v = (float(y) + .5f) / H;
  vrost::Mat3 h{};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) h.m[i][j] = pc.rows[eye][i][j];
  float su, sv;
  if (!vrost::applyHomography(h, u, v, su, sv)) return 0;
  const float outsideX = std::max(std::max(-su, su - 1), 0.f) * EyeW, outsideY = std::max(std::max(-sv, sv - 1), 0.f) * H;
  const float fade = std::clamp(1 - std::max(outsideX, outsideY) * .5f, 0.f, 1.f);
  if (fade <= 0) return 0;
  const float px = std::clamp(su, 0.f, 1.f) * EyeW + eye * EyeW, py = std::clamp(sv, 0.f, 1.f) * H;
  const float lox = eye * EyeW + .5f, hix = (eye + 1) * EyeW - .5f, loy = .5f, hiy = H - .5f;
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
  const float xs[3] = {std::clamp(p1x - 1, lox, hix), std::clamp(p1x + wx[2] / (wx[1] + wx[2]), lox, hix), std::clamp(p1x + 2, lox, hix)};
  const float ys[3] = {std::clamp(p1y - 1, loy, hiy), std::clamp(p1y + wy[2] / (wy[1] + wy[2]), loy, hiy), std::clamp(p1y + 2, loy, hiy)};
  const float cx[3] = {wx[0], wx[1] + wx[2], wx[3]}, cy[3] = {wy[0], wy[1] + wy[2], wy[3]};
  float sum = 0;
  for (int j = 0; j < 3; ++j)
    for (int i = 0; i < 3; ++i) sum += tap(img, xs[i], ys[j], c) * cx[i] * cy[j];
  return std::clamp(sum, 0.f, 1.f) * fade;
}

std::array<VrostView, 2> eyes() {
  std::array<VrostView, 2> v{};
  for (auto &e : v) {
    e.pose.orientation = {0, 0, 0, 1};
    e.left = -.785398f; e.right = .785398f; e.up = .785398f; e.down = -.785398f; // 90 degree square views
  }
  v[0].pose.position[0] = -.032f;
  v[1].pose.position[0] = .032f;
  return v;
}

int brightestColumn(const std::vector<uint8_t> &img, int eye, uint32_t y) {
  int best = -1, value = -1;
  for (uint32_t x = 0; x < EyeW; ++x) {
    const uint8_t *p = &img[(size_t(y) * W + eye * EyeW + x) * 4];
    if (p[1] > value) { value = p[1]; best = int(x); }
  }
  return best;
}
} // namespace

int main(int argc, char **argv) {
  if (argc != 2) {
    std::printf("usage: reaim_shader_tests <reaim.spv>\n");
    return 2;
  }
  try {
    Gpu gpu;
    if (!gpu.open(argv[1])) {
      std::printf("SKIP: no Vulkan device\n");
      return 77;
    }
    const auto input = scene();
    const vrost::Pose source{};
    // 1. Unchanged pose: exact copy.
    {
      const auto out = gpu.run(input, W, H, vrost::reaimConstants(source, source, eyes()));
      int worst = 0;
      for (size_t i = 0; i < out.size(); ++i) worst = std::max(worst, std::abs(int(out[i]) - int(input[i])));
      CHECK(worst <= 1, "identity differs by %d", worst);
    }
    // 2. Head turned 2 degrees left: the view-centre line moves right, and every pixel
    //    matches the CPU model of the same sampling.
    {
      const auto target = vrost::yawed(source, 2);
      const auto pc = vrost::reaimConstants(source, target, eyes());
      const auto out = gpu.run(input, W, H, pc);
      int worst = 0;
      for (uint32_t y = 0; y < H; ++y)
        for (uint32_t x = 0; x < W; ++x)
          for (int c = 0; c < 3; ++c) {
            const int expected = int(std::lround(reference(input, pc, x, y, c) * 255));
            worst = std::max(worst, std::abs(int(out[(size_t(y) * W + x) * 4 + c]) - expected));
          }
      CHECK(worst <= 3, "re-aimed image differs from the CPU model by %d", worst);
      const int before = brightestColumn(input, 0, H / 2), after = brightestColumn(out, 0, H / 2);
      // tan(2 deg) / (2 tan 45 deg) * 96 = 1.68 pixels to the right.
      CHECK(after == before + 2 || after == before + 1, "centre line moved from %d to %d", before, after);
      // The right eye stays blue and the left eye red: no taps from the other eye. (The
      // top and bottom rows of a turned view fall slightly outside the rendered one.)
      for (uint32_t y = 2; y + 2 < H; ++y) {
        CHECK(out[(size_t(y) * W + EyeW - 1) * 4 + 2] < 60, "left eye edge mixed with the right eye at row %u", y);
        CHECK(out[(size_t(y) * W + EyeW + 3) * 4 + 2] > 160, "right eye mixed with the left eye at row %u", y);
      }
    }
    // 3. A 60 degree turn: most of the view lies outside the rendered image and is black.
    {
      const auto out = gpu.run(input, W, H, vrost::reaimConstants(source, vrost::yawed(source, 60), eyes()));
      const uint8_t *edge = &out[(size_t(H / 2) * W + 2) * 4];
      CHECK(edge[0] == 0 && edge[1] == 0 && edge[2] == 0, "outside the view should be black: %u %u %u", edge[0], edge[1], edge[2]);
    }
  } catch (const std::exception &e) {
    std::printf("ERROR %s\n", e.what());
    return 1;
  }
  if (failures) {
    std::printf("%d re-aim shader failures\n", failures);
    return 1;
  }
  std::printf("re-aim shader contracts passed\n");
  return 0;
}
