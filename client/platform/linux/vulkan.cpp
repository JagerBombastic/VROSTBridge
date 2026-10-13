#include "client/platform/linux/video.hpp"
extern "C" {
#include <libavutil/pixdesc.h>
}
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#ifndef _WIN32
#include <sys/stat.h>
#include <sys/sysmacros.h>
#endif
namespace vrost {
static std::vector<std::string> words(const std::string &s) {
  std::istringstream in(s);
  std::vector<std::string> out;
  std::string w;
  while (in >> w)
    out.push_back(w);
  return out;
}
static std::string plus(const std::string &s) {
  auto w = words(s);
  std::string out;
  for (auto v : w) {
    if (!out.empty())
      out += '+';
    out += v;
  }
  return out;
}
uint32_t VulkanVideo::memoryType(uint32_t bits, VkMemoryPropertyFlags flags) {
  VkPhysicalDeviceMemoryProperties props;
  vkGetPhysicalDeviceMemoryProperties(vk_->phys_dev, &props);
  for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
    if ((bits & (1u << i)) &&
        (props.memoryTypes[i].propertyFlags & flags) == flags)
      return i;
  throw std::runtime_error("Vulkan memory type unavailable");
}
void VulkanVideo::open(OpenVRDevice &runtime,
                       const std::filesystem::path &shader) {
  uint32_t n =
      runtime.compositor->GetVulkanInstanceExtensionsRequired(nullptr, 0);
  std::string required(n, '\0');
  if (n)
    runtime.compositor->GetVulkanInstanceExtensionsRequired(required.data(), n);
  required.resize(std::strlen(required.c_str()));
  auto extensions = words(required);
  std::vector<const char *> names;
  for (auto &s : extensions)
    names.push_back(s.c_str());
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  app.pApplicationName = "VROSTBridge";
  app.apiVersion = VK_API_VERSION_1_3;
  VkInstanceCreateInfo create{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  create.pApplicationInfo = &app;
  create.enabledExtensionCount = names.size();
  create.ppEnabledExtensionNames = names.data();
  VkInstance probe;
  vkcheck(vkCreateInstance(&create, nullptr, &probe), "Create Vulkan probe");
  uint64_t output = 0;
  runtime.system->GetOutputDevice(&output, vr::TextureType_Vulkan, probe);
  uint32_t count = 0;
  vkEnumeratePhysicalDevices(probe, &count, nullptr);
  std::vector<VkPhysicalDevice> devices(count);
  vkEnumeratePhysicalDevices(probe, &count, devices.data());
  int selected = -1;
  for (uint32_t i = 0; i < count; ++i)
    if (reinterpret_cast<uint64_t>(devices[i]) == output)
      selected = i;
  if (selected < 0) {
    vkDestroyInstance(probe, nullptr);
    throw std::runtime_error("SteamVR Vulkan adapter not found");
  }
  n = runtime.compositor->GetVulkanDeviceExtensionsRequired(devices[selected],
                                                            nullptr, 0);
  std::string deviceExtensions(n, '\0');
  if (n)
    runtime.compositor->GetVulkanDeviceExtensionsRequired(
        devices[selected], deviceExtensions.data(), n);
  deviceExtensions.resize(std::strlen(deviceExtensions.c_str()));
  vkDestroyInstance(probe, nullptr);
  AVDictionary *options = nullptr;
  av_dict_set(&options, "instance_extensions", plus(required).c_str(), 0);
  av_dict_set(&options, "device_extensions", plus(deviceExtensions).c_str(), 0);
  av_dict_set(&options, "disable_multiplane", "1", 0);
  int result =
      av_hwdevice_ctx_create(&deviceRef_, AV_HWDEVICE_TYPE_VULKAN,
                             std::to_string(selected).c_str(), options, 0);
  av_dict_free(&options);
  avcheck(result, "Create shared FFmpeg/Vulkan device");
  hw_ = reinterpret_cast<AVHWDeviceContext *>(deviceRef_->data);
  vk_ = static_cast<AVVulkanDeviceContext *>(hw_->hwctx);
  runtime.system->GetOutputDevice(&output, vr::TextureType_Vulkan, vk_->inst);
  if (output != reinterpret_cast<uint64_t>(vk_->phys_dev))
    throw std::runtime_error("Decoder and SteamVR adapters differ");
  if (vk_->queue_family_index < 0)
    throw std::runtime_error("Vulkan graphics queue unavailable");
  family = vk_->queue_family_index;
  vkGetPhysicalDeviceQueueFamilyProperties(vk_->phys_dev, &count, nullptr);
  std::vector<VkQueueFamilyProperties> qs(count);
  vkGetPhysicalDeviceQueueFamilyProperties(vk_->phys_dev, &count, qs.data());
  if (!(qs[family].queueFlags & VK_QUEUE_COMPUTE_BIT))
    throw std::runtime_error(
        "SteamVR graphics queue cannot perform conversion compute");
  vkGetDeviceQueue(vk_->act_dev, family, 0, &queue);
  VkPhysicalDeviceProperties props;
  vkGetPhysicalDeviceProperties(vk_->phys_dev, &props);
  gpu = props.deviceName;
  openTimestamps(qs[family], props.limits);
#ifdef _WIN32
  openWindows(); // 1.0.0.7: D3D11VA on this GPU instead of a VA-API render node
#else
  uint32_t extCount = 0;
  vkEnumerateDeviceExtensionProperties(vk_->phys_dev, nullptr, &extCount,
                                       nullptr);
  std::vector<VkExtensionProperties> exts(extCount);
  vkEnumerateDeviceExtensionProperties(vk_->phys_dev, nullptr, &extCount,
                                       exts.data());
  if (std::any_of(exts.begin(), exts.end(), [](auto &e) {
        return std::strcmp(e.extensionName,
                           VK_EXT_PHYSICAL_DEVICE_DRM_EXTENSION_NAME) == 0;
      })) {
    VkPhysicalDeviceDrmPropertiesEXT drm{
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRM_PROPERTIES_EXT};
    VkPhysicalDeviceProperties2 p{
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    p.pNext = &drm;
    vkGetPhysicalDeviceProperties2(vk_->phys_dev, &p);
    if (drm.hasRender && std::filesystem::exists("/dev/dri"))
      for (auto &entry : std::filesystem::directory_iterator("/dev/dri")) {
        struct stat st{};
        if (::stat(entry.path().c_str(), &st) == 0 && S_ISCHR(st.st_mode) &&
            major(st.st_rdev) == drm.renderMajor &&
            minor(st.st_rdev) == drm.renderMinor)
          renderNode = entry.path();
      }
  }
#endif
  VkCommandPoolCreateInfo pool{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
  pool.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
  pool.queueFamilyIndex = family;
  vkcheck(vkCreateCommandPool(vk_->act_dev, &pool, nullptr, &pool_),
          "Command pool");
  VkDescriptorSetLayoutBinding bindings[3]{};
  for (int i = 0; i < 3; ++i) {
    bindings[i].binding = i;
    bindings[i].descriptorCount = 1;
    bindings[i].descriptorType =
        i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
               : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
  }
  VkDescriptorSetLayoutCreateInfo dl{
      VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
  dl.bindingCount = 3;
  dl.pBindings = bindings;
  vkcheck(vkCreateDescriptorSetLayout(vk_->act_dev, &dl, nullptr,
                                      &descriptorLayout_),
          "Descriptor layout");
  VkDescriptorPoolSize sizes[] = {
      {VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 32},
      {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 16}};
  VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
  dp.maxSets = 16;
  dp.poolSizeCount = 2;
  dp.pPoolSizes = sizes;
  vkcheck(vkCreateDescriptorPool(vk_->act_dev, &dp, nullptr, &descriptors_),
          "Descriptor pool");
  VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(ConversionPushConstants)};
  VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  pl.setLayoutCount = 1;
  pl.pSetLayouts = &descriptorLayout_;
  pl.pushConstantRangeCount = 1;
  pl.pPushConstantRanges = &range;
  vkcheck(vkCreatePipelineLayout(vk_->act_dev, &pl, nullptr, &pipelineLayout_),
          "Pipeline layout");
  std::ifstream file(shader, std::ios::binary | std::ios::ate);
  if (!file)
    throw std::runtime_error("Conversion shader missing");
  auto bytes = file.tellg();
  if (bytes <= 0 || bytes % 4 != 0)
    throw std::runtime_error("Invalid conversion shader");
  std::vector<uint32_t> code(size_t(bytes) / 4);
  file.seekg(0);
  file.read(reinterpret_cast<char *>(code.data()), bytes);
  VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
  sm.codeSize = size_t(bytes);
  sm.pCode = code.data();
  VkShaderModule module;
  vkcheck(vkCreateShaderModule(vk_->act_dev, &sm, nullptr, &module),
          "Shader module");
  VkComputePipelineCreateInfo cp{
      VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
  cp.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
  cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
  cp.stage.module = module;
  cp.stage.pName = "main";
  cp.layout = pipelineLayout_;
  auto pr = vkCreateComputePipelines(vk_->act_dev, VK_NULL_HANDLE, 1, &cp,
                                     nullptr, &pipeline_);
  vkDestroyShaderModule(vk_->act_dev, module, nullptr);
  vkcheck(pr, "Conversion pipeline");
  VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
  si.magFilter = si.minFilter = VK_FILTER_LINEAR;
  si.addressModeU = si.addressModeV = si.addressModeW =
      VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
  vkcheck(vkCreateSampler(vk_->act_dev, &si, nullptr, &sampler_), "Sampler");
  runtime.compositor->SetExplicitTimingMode(
      vr::VRCompositorTimingMode_Explicit_ApplicationPerformsPostPresentHandoff);
  presentation_ = std::make_unique<PresentationQueue>(
      [this] { vk_->lock_queue(hw_, family, 0); },
      [this] { vk_->unlock_queue(hw_, family, 0); },
      [this, &runtime] {
        auto result=runtime.compositor->SubmitExplicitTimingData();
        if (result!=vr::VRCompositorError_None) {
          ++timingMarkerErrors;
          if (timingMarkerErrors==1)
            diagnosticLine("DEBUG SteamVR explicit timing marker returned " + std::to_string(result));
        }
      },
      [&runtime] { runtime.compositor->PostPresentHandoff(); });
  diagnosticLine("STATUS Vulkan queue coordinated with SteamVR explicit timing and handoff");
  try {
    openReaim(shader.parent_path() / "reaim.spv");
  } catch (const std::exception &error) {
    diagnosticLine(std::string("STATUS Frame re-aim unavailable: ") + error.what());
  }
  try {
    if (!allowFused)
      diagnosticLine("STATUS Fused frame pass off (One-pass frame path is off, or CPU decoding): frames are "
                     "converted when decoded and re-aimed when shown");
    else if (reaimPool_) openFused(shader.parent_path() / "fused.spv");
  } catch (const std::exception &error) {
    if (fusedPipeline_) vkDestroyPipeline(vk_->act_dev, fusedPipeline_, nullptr);
    fusedPipeline_ = VK_NULL_HANDLE;
    diagnosticLine(std::string("STATUS Fused frame pass unavailable (") + error.what() +
                   "); frames are converted when decoded and re-aimed when shown");
  }
}
void VulkanVideo::openFused(const std::filesystem::path &shader) {
  // 1.0.0.11: planes 0 and 1 sampled, the output image 2, the frame's constants 3 (FusedFrame) and the visible spans 4.
  VkDescriptorSetLayoutBinding bindings[5]{};
  for (int i = 0; i < 5; ++i) {
    bindings[i].binding = uint32_t(i);
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
  vkcheck(vkCreateDescriptorSetLayout(vk_->act_dev, &dl, nullptr, &fusedSetLayout_), "Fused descriptor layout");
  auto buffer = [&](VkDeviceSize size, VkBufferUsageFlags usage, VkBuffer &b, VkDeviceMemory &memory, void *&mapped) {
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = size;
    bi.usage = usage;
    vkcheck(vkCreateBuffer(vk_->act_dev, &bi, nullptr, &b), "Fused buffer");
    VkMemoryRequirements mem;
    vkGetBufferMemoryRequirements(vk_->act_dev, b, &mem);
    VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    a.allocationSize = mem.size;
    a.memoryTypeIndex =
        memoryType(mem.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &memory), "Fused buffer memory");
    vkcheck(vkBindBufferMemory(vk_->act_dev, b, memory, 0), "Bind fused buffer");
    vkcheck(vkMapMemory(vk_->act_dev, memory, 0, VK_WHOLE_SIZE, 0, &mapped), "Map fused buffer");
  };
  // The spans of the largest output: two eyes of 8192 rows (the stream limit).
  constexpr VkDeviceSize spansBytes = 2 * 8192 * sizeof(uint32_t);
  for (auto &target : reaimTargets_) {
    buffer(sizeof(FusedFrame), VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT, target.frame, target.frameMemory, target.frameMapped);
    buffer(spansBytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, target.spans, target.spansMemory, target.spansMapped);
    std::memset(target.spansMapped, 0, size_t(spansBytes));
    VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    da.descriptorPool = reaimDescriptors_;
    da.descriptorSetCount = 1;
    da.pSetLayouts = &fusedSetLayout_;
    vkcheck(vkAllocateDescriptorSets(vk_->act_dev, &da, &target.fusedDescriptor), "Fused descriptor set");
    // The buffers are written once; the planes and the output image for each frame.
    VkDescriptorBufferInfo infos[2] = {{target.frame, 0, sizeof(FusedFrame)}, {target.spans, 0, spansBytes}};
    VkWriteDescriptorSet writes[2]{};
    for (int i = 0; i < 2; ++i) {
      writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      writes[i].dstSet = target.fusedDescriptor;
      writes[i].dstBinding = uint32_t(3 + i);
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      writes[i].pBufferInfo = &infos[i];
    }
    vkUpdateDescriptorSets(vk_->act_dev, 2, writes, 0, nullptr);
    if (target.image) writeSpans(target);
  }
  VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  pl.setLayoutCount = 1;
  pl.pSetLayouts = &fusedSetLayout_;
  vkcheck(vkCreatePipelineLayout(vk_->act_dev, &pl, nullptr, &fusedLayout_), "Fused pipeline layout");
  VkShaderModule module = loadShader(shader);
  VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
  cp.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
  cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
  cp.stage.module = module;
  cp.stage.pName = "main";
  cp.layout = fusedLayout_;
  auto result = vkCreateComputePipelines(vk_->act_dev, VK_NULL_HANDLE, 1, &cp, nullptr, &fusedPipeline_);
  vkDestroyShaderModule(vk_->act_dev, module, nullptr);
  if (result != VK_SUCCESS) fusedPipeline_ = VK_NULL_HANDLE;
  vkcheck(result, "Fused pipeline");
  fusedSets_ = true;
  diagnosticLine("STATUS Fused frame pass ready: decoded planes are converted, expanded, re-aimed and sharpened in one "
                 "pass when shown, each eye in its own workgroup layer");
}
// 1.0.0.11: GPU time stamps around the fused pass and the plane copy, where the queue has them.
void VulkanVideo::openTimestamps(const VkQueueFamilyProperties &queueFamily, const VkPhysicalDeviceLimits &limits) {
  if (!queueFamily.timestampValidBits || !(limits.timestampPeriod > 0)) {
    diagnosticLine("STATUS GPU time stamps: unavailable on this GPU's queue (the frame pass is not timed)");
    return;
  }
  VkQueryPoolCreateInfo qp{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
  qp.queryType = VK_QUERY_TYPE_TIMESTAMP;
  qp.queryCount = uint32_t(reaimTargets_.size() * 2);
  if (vkCreateQueryPool(vk_->act_dev, &qp, nullptr, &passQueries_) != VK_SUCCESS) {
    passQueries_ = VK_NULL_HANDLE;
    return;
  }
  constexpr int copySlots = 16; // more than the surfaces that exist at once (12)
  qp.queryCount = copySlots * 2;
  if (vkCreateQueryPool(vk_->act_dev, &qp, nullptr, &copyQueries_) != VK_SUCCESS) copyQueries_ = VK_NULL_HANDLE;
  else
    for (int slot = copySlots - 1; slot >= 0; --slot) freeCopySlots_.push_back(slot);
  timestampNs_ = double(limits.timestampPeriod);
  timestampMask_ = queueFamily.timestampValidBits >= 64 ? ~uint64_t(0) : (uint64_t(1) << queueFamily.timestampValidBits) - 1;
  std::ostringstream line;
  line << "STATUS GPU time stamps: on (" << queueFamily.timestampValidBits << " bits, " << limits.timestampPeriod
       << " ns per tick); TIMING reports the frame pass and the plane copy";
  diagnosticLine(line.str());
}
// The duration between a pair of time stamps once both are written (the work behind them has completed).
bool VulkanVideo::queryDuration(VkQueryPool pool, uint32_t first, uint64_t &ns) const {
  uint64_t values[4]{}; // begin, availability, end, availability
  if (vkGetQueryPoolResults(vk_->act_dev, pool, first, 2, sizeof(values), values, 2 * sizeof(uint64_t),
                            VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WITH_AVAILABILITY_BIT) != VK_SUCCESS ||
      !values[1] || !values[3])
    return false;
  const uint64_t ticks = (values[2] - values[0]) & timestampMask_;
  const double duration = double(ticks) * timestampNs_;
  if (!(duration >= 0) || duration > 1e9) return false; // a wrapped or reset counter
  ns = uint64_t(duration);
  return true;
}
// Called by the decode-time copy with the surface's previous copy complete (its fence signalled).
void VulkanVideo::beginCopyTiming(Surface &s, VkCommandBuffer cmd) {
  if (!copyQueries_) return;
  if (s.copySlot < 0) {
    std::lock_guard<std::mutex> lock(copyGpuMutex_);
    if (freeCopySlots_.empty()) return;
    s.copySlot = freeCopySlots_.back();
    freeCopySlots_.pop_back();
  }
  uint64_t ns = 0;
  if (s.copyTimed && queryDuration(copyQueries_, uint32_t(s.copySlot * 2), ns)) {
    std::lock_guard<std::mutex> lock(copyGpuMutex_);
    copyGpu_.add(ns);
  }
  s.copyTimed = false;
  vkCmdResetQueryPool(cmd, copyQueries_, uint32_t(s.copySlot * 2), 2);
  // Written once all earlier work on the queue is complete (a top-of-pipe stamp may be written before it is,
  // and the time would then include the tail of that work).
  vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, copyQueries_, uint32_t(s.copySlot * 2));
}
void VulkanVideo::endCopyTiming(Surface &s, VkCommandBuffer cmd) {
  if (!copyQueries_ || s.copySlot < 0) return;
  vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, copyQueries_, uint32_t(s.copySlot * 2 + 1));
  s.copyTimed = true;
}
VkShaderModule VulkanVideo::loadShader(const std::filesystem::path &path) {
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  if (!file)
    throw std::runtime_error("shader missing: " + path.filename().string());
  auto bytes = file.tellg();
  if (bytes <= 0 || bytes % 4 != 0)
    throw std::runtime_error("invalid shader: " + path.filename().string());
  std::vector<uint32_t> code(size_t(bytes) / 4);
  file.seekg(0);
  file.read(reinterpret_cast<char *>(code.data()), bytes);
  VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
  sm.codeSize = size_t(bytes);
  sm.pCode = code.data();
  VkShaderModule module;
  vkcheck(vkCreateShaderModule(vk_->act_dev, &sm, nullptr, &module), "Shader module");
  return module;
}
void VulkanVideo::openReaim(const std::filesystem::path &shader) {
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
  vkcheck(vkCreateDescriptorSetLayout(vk_->act_dev, &dl, nullptr, &reaimSetLayout_), "Re-aim descriptor layout");
  VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(ReaimPushConstants)};
  VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  pl.setLayoutCount = 1;
  pl.pSetLayouts = &reaimSetLayout_;
  pl.pushConstantRangeCount = 1;
  pl.pPushConstantRanges = &range;
  vkcheck(vkCreatePipelineLayout(vk_->act_dev, &pl, nullptr, &reaimLayout_), "Re-aim pipeline layout");
  const uint32_t count = uint32_t(reaimTargets_.size());
  // Per target: the re-aim set (one sampled image, one storage) and the fused set (two sampled images, one
  // storage, 1.0.0.11: its frame constants and visible spans; allocated by openFused).
  VkDescriptorPoolSize sizes[] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, count * 3},
                                  {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, count * 2},
                                  {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, count},
                                  {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, count}};
  VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
  dp.maxSets = count * 2;
  dp.poolSizeCount = 4;
  dp.pPoolSizes = sizes;
  vkcheck(vkCreateDescriptorPool(vk_->act_dev, &dp, nullptr, &reaimDescriptors_), "Re-aim descriptor pool");
  VkCommandPoolCreateInfo commands{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
  commands.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
  commands.queueFamilyIndex = family;
  vkcheck(vkCreateCommandPool(vk_->act_dev, &commands, nullptr, &reaimPool_), "Re-aim command pool");
  for (auto &target : reaimTargets_) {
    VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    da.descriptorPool = reaimDescriptors_;
    da.descriptorSetCount = 1;
    da.pSetLayouts = &reaimSetLayout_;
    vkcheck(vkAllocateDescriptorSets(vk_->act_dev, &da, &target.descriptor), "Re-aim descriptor set");
  }
  VkShaderModule module = loadShader(shader);
  VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
  cp.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
  cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
  cp.stage.module = module;
  cp.stage.pName = "main";
  cp.layout = reaimLayout_;
  auto result = vkCreateComputePipelines(vk_->act_dev, VK_NULL_HANDLE, 1, &cp, nullptr, &reaimPipeline_);
  vkDestroyShaderModule(vk_->act_dev, module, nullptr);
  if (result != VK_SUCCESS) reaimPipeline_ = VK_NULL_HANDLE;
  vkcheck(result, "Re-aim pipeline");
  diagnosticLine("STATUS Frame re-aim ready (rotation-only, Catmull-Rom)");
}
bool VulkanVideo::createReaimTarget(ReaimTarget &t, uint32_t width, uint32_t height) {
  VkImageCreateInfo im{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  im.imageType = VK_IMAGE_TYPE_2D;
  im.format = VK_FORMAT_R8G8B8A8_UNORM;
  im.extent = {width, height, 1};
  im.mipLevels = im.arrayLayers = 1;
  im.samples = VK_SAMPLE_COUNT_1_BIT;
  im.tiling = VK_IMAGE_TILING_OPTIMAL;
  // 1.0.0.11: also cleared to black (TRANSFER_DST) before the fused pass skips the pixels the lenses never show.
  im.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
             VK_IMAGE_USAGE_TRANSFER_DST_BIT;
  vkcheck(vkCreateImage(vk_->act_dev, &im, nullptr, &t.image), "Re-aim image");
  VkMemoryRequirements mem;
  vkGetImageMemoryRequirements(vk_->act_dev, t.image, &mem);
  VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  a.allocationSize = mem.size;
  a.memoryTypeIndex = memoryType(mem.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &t.memory), "Re-aim memory");
  vkcheck(vkBindImageMemory(vk_->act_dev, t.image, t.memory, 0), "Bind re-aim image");
  VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
  vi.image = t.image;
  vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
  vi.format = im.format;
  vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  vkcheck(vkCreateImageView(vk_->act_dev, &vi, nullptr, &t.view), "Re-aim view");
  if (!t.cmd) {
    VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ca.commandPool = reaimPool_; // render thread only
    ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ca.commandBufferCount = 1;
    vkcheck(vkAllocateCommandBuffers(vk_->act_dev, &ca, &t.cmd), "Re-aim commands");
  }
  if (!t.retired) {
    VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    fc.flags = VK_FENCE_CREATE_SIGNALED_BIT;
    vkcheck(vkCreateFence(vk_->act_dev, &fc, nullptr, &t.retired), "Re-aim retirement fence");
  }
  t.layout = VK_IMAGE_LAYOUT_UNDEFINED;
  t.width = width;
  t.height = height;
  t.needsClear = true;
  t.timed = false;
  if (t.spansMapped) writeSpans(t);
  return true;
}
// 1.0.0.11: the visible spans of this image's size into its storage buffer (the image is free: just made).
void VulkanVideo::writeSpans(ReaimTarget &t) {
  const Spans &spans = spansFor(t.width / 2, t.height);
  // The buffer holds two eyes of 8192 rows, the tallest stream there is.
  std::memcpy(t.spansMapped, spans.rows.data(), std::min<size_t>(spans.rows.size(), 2 * 8192) * sizeof(uint32_t));
  t.visibleShare = spans.share;
  t.spansGeneration = spansGeneration_;
}
void VulkanVideo::setVisibleArea(VisibleArea area, bool enabled) {
  visible_ = std::move(area);
  skipHidden_ = enabled && visible_.any();
  spanCache_.clear();
  ++spansGeneration_; // images made earlier take the new spans (and a clear) when next free
  std::ostringstream line;
  // Only the fused pass reads the spans (the two-pass path and CPU conversion compute every pixel).
  line << "STATUS Lens area: "
       << (skipHidden_ && fusedAvailable() ? "pixels the lenses never show are skipped"
           : skipHidden_                   ? "every pixel is computed (skipping needs the fused frame pass)"
                                           : "every pixel is computed")
       << " (sources " << visible_.sources() << ", visible-area triangles " << visible_.triangles[0].size() << "/"
       << visible_.triangles[1].size() << (enabled ? "" : ", off by choice")
       << (visible_.notes.empty() ? "" : "; " + visible_.notes) << ")";
  diagnosticLine(line.str());
}
// Both eyes' spans for an output of this size, computed once per size. A description that leaves
// under half of either eye is taken for a fault and every pixel is computed.
const VulkanVideo::Spans &VulkanVideo::spansFor(uint32_t eyeWidth, uint32_t height) {
  const uint64_t key = uint64_t(eyeWidth) << 32 | height;
  const auto found = spanCache_.find(key);
  if (found != spanCache_.end()) return found->second;
  if (spanCache_.size() >= 8) spanCache_.clear();
  Spans spans;
  spans.rows.reserve(size_t(height) * 2);
  double share[2] = {1, 1};
  const uint32_t margin = visibleMargin(eyeWidth, height);
  for (int eye = 0; eye < 2; ++eye) {
    auto rows = skipHidden_ ? visibleSpans(visible_, eye, eyeWidth, height, margin)
                            : std::vector<uint32_t>(height, packSpan(0, eyeWidth));
    share[eye] = vrost::visibleShare(rows, eyeWidth);
    spans.rows.insert(spans.rows.end(), rows.begin(), rows.end());
  }
  const bool plausible = share[0] >= .5 && share[1] >= .5;
  if (!plausible) std::fill(spans.rows.begin(), spans.rows.end(), packSpan(0, eyeWidth));
  spans.share = plausible ? (share[0] + share[1]) / 2 : 1.0;
  if (skipHidden_) {
    std::ostringstream line;
    line << std::fixed << std::setprecision(3) << "VISIBLE_AREA {\"source\":\"" << visible_.sources()
         << "\",\"eye_width\":" << eyeWidth << ",\"height\":" << height << ",\"margin\":" << margin
         << ",\"left\":" << share[0] << ",\"right\":" << share[1] << ",\"used\":" << (plausible ? "true" : "false")
         << "}";
    diagnosticLine(line.str());
  }
  return spanCache_.emplace(key, std::move(spans)).first->second;
}
void VulkanVideo::destroyReaimTarget(ReaimTarget &t) {
  if (t.view) vkDestroyImageView(vk_->act_dev, t.view, nullptr);
  if (t.image) vkDestroyImage(vk_->act_dev, t.image, nullptr);
  if (t.memory) vkFreeMemory(vk_->act_dev, t.memory, nullptr);
  t.view = VK_NULL_HANDLE;
  t.image = VK_NULL_HANDLE;
  t.memory = VK_NULL_HANDLE;
  t.width = t.height = 0;
  t.layout = VK_IMAGE_LAYOUT_UNDEFINED;
}
std::shared_ptr<Surface> VulkanVideo::allocate(uint32_t width,
                                               uint32_t height) {
  auto s = std::make_shared<Surface>();
  s->width = width;
  s->height = height;
  surfaces_.push_back(s);
  VkImageCreateInfo im{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  im.imageType = VK_IMAGE_TYPE_2D;
  im.format = VK_FORMAT_R8G8B8A8_UNORM;
  im.extent = {width, height, 1};
  im.mipLevels = im.arrayLayers = 1;
  im.samples = VK_SAMPLE_COUNT_1_BIT;
  im.tiling = VK_IMAGE_TILING_OPTIMAL;
  im.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
             VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
  vkcheck(vkCreateImage(vk_->act_dev, &im, nullptr, &s->image), "Stereo image");
  VkMemoryRequirements mem;
  vkGetImageMemoryRequirements(vk_->act_dev, s->image, &mem);
  VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  a.allocationSize = mem.size;
  a.memoryTypeIndex =
      memoryType(mem.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &s->memory),
          "Image memory");
  vkcheck(vkBindImageMemory(vk_->act_dev, s->image, s->memory, 0),
          "Bind stereo image");
  VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
  vi.image = s->image;
  vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
  vi.format = im.format;
  vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  vkcheck(vkCreateImageView(vk_->act_dev, &vi, nullptr, &s->view),
          "Stereo view");
  VkCommandBufferAllocateInfo ca{
      VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
  ca.commandPool = pool_;
  ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
  ca.commandBufferCount = 1;
  vkcheck(vkAllocateCommandBuffers(vk_->act_dev, &ca, &s->cmd),
          "Command buffer");
  VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  fc.flags = VK_FENCE_CREATE_SIGNALED_BIT;
  vkcheck(vkCreateFence(vk_->act_dev, &fc, nullptr, &s->fence), "Fence");
  vkcheck(vkCreateFence(vk_->act_dev, &fc, nullptr, &s->retired),
          "Retirement fence");
  if (!spareDescriptors_.empty()) {
    s->descriptor = spareDescriptors_.back();
    spareDescriptors_.pop_back();
  } else {
    VkDescriptorSetAllocateInfo da{
        VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    da.descriptorPool = descriptors_;
    da.descriptorSetCount = 1;
    da.pSetLayouts = &descriptorLayout_;
    vkcheck(vkAllocateDescriptorSets(vk_->act_dev, &da, &s->descriptor),
            "Descriptor set");
  }
  return s;
}
// RC4.3: images of our own for the decoded planes (R8 + R8G8, or R16 + R16G16 for P010), filled by a
// copy when a frame is decoded and read by the fused pass each time the frame is shown.
std::shared_ptr<Surface> VulkanVideo::allocatePlanar(uint32_t width, uint32_t height, bool tenBit) {
  auto s = std::make_shared<Surface>();
  s->planar = true;
  s->tenBit = tenBit;
  s->width = width;
  s->height = height;
  surfaces_.push_back(s);
  createPlanes(*s, width, height, tenBit);
  VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
  ca.commandPool = pool_;
  ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
  ca.commandBufferCount = 1;
  vkcheck(vkAllocateCommandBuffers(vk_->act_dev, &ca, &s->cmd), "Command buffer");
  VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  fc.flags = VK_FENCE_CREATE_SIGNALED_BIT;
  vkcheck(vkCreateFence(vk_->act_dev, &fc, nullptr, &s->fence), "Fence");
  vkcheck(vkCreateFence(vk_->act_dev, &fc, nullptr, &s->retired), "Retirement fence");
  return s;
}
// The two plane images of a surface (a planar one, or on Windows also a two-pass one), sampled by
// the frame passes and written by a copy.
void VulkanVideo::createPlanes(Surface &surface, uint32_t width, uint32_t height, bool tenBit) {
  Surface *s = &surface;
  s->planesWidth = width;
  s->planesHeight = height;
  s->planesTenBit = tenBit;
  for (int i = 0; i < 2; ++i) {
    VkImageCreateInfo im{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    im.imageType = VK_IMAGE_TYPE_2D;
    im.format = tenBit ? (i ? VK_FORMAT_R16G16_UNORM : VK_FORMAT_R16_UNORM) : (i ? VK_FORMAT_R8G8_UNORM : VK_FORMAT_R8_UNORM);
    im.extent = {i ? (width + 1) / 2 : width, i ? (height + 1) / 2 : height, 1};
    im.mipLevels = im.arrayLayers = 1;
    im.samples = VK_SAMPLE_COUNT_1_BIT;
    im.tiling = VK_IMAGE_TILING_OPTIMAL;
    im.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    vkcheck(vkCreateImage(vk_->act_dev, &im, nullptr, &s->planeImages[i]), "Plane image");
    VkMemoryRequirements mem;
    vkGetImageMemoryRequirements(vk_->act_dev, s->planeImages[i], &mem);
    VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    a.allocationSize = mem.size;
    a.memoryTypeIndex = memoryType(mem.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &s->planeMemory[i]), "Plane memory");
    vkcheck(vkBindImageMemory(vk_->act_dev, s->planeImages[i], s->planeMemory[i], 0), "Bind plane image");
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = s->planeImages[i];
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = im.format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkcheck(vkCreateImageView(vk_->act_dev, &vi, nullptr, &s->planeViews[i]), "Plane view");
  }
}
void VulkanVideo::destroyPlanes(Surface &s) {
  for (int i = 0; i < 2; ++i) {
    if (s.planeViews[i]) vkDestroyImageView(vk_->act_dev, s.planeViews[i], nullptr);
    if (s.planeImages[i]) vkDestroyImage(vk_->act_dev, s.planeImages[i], nullptr);
    if (s.planeMemory[i]) vkFreeMemory(vk_->act_dev, s.planeMemory[i], nullptr);
    s.planeViews[i] = VK_NULL_HANDLE;
    s.planeImages[i] = VK_NULL_HANDLE;
    s.planeMemory[i] = VK_NULL_HANDLE;
  }
  s.planesWidth = s.planesHeight = 0;
  s.planeLayout = VK_IMAGE_LAYOUT_UNDEFINED;
}
void VulkanVideo::destroySurface(Surface &s) {
  if (s.copySlot >= 0) {
    std::lock_guard<std::mutex> lock(copyGpuMutex_);
    freeCopySlots_.push_back(s.copySlot);
  }
  cleanupInput(s);
  destroyPlanes(s);
  if (s.planeMapped) vkUnmapMemory(vk_->act_dev, s.planeStagingMemory);
  if (s.planeStaging) vkDestroyBuffer(vk_->act_dev, s.planeStaging, nullptr);
  if (s.planeStagingMemory) vkFreeMemory(vk_->act_dev, s.planeStagingMemory, nullptr);
  if (s.mapped) vkUnmapMemory(vk_->act_dev, s.stagingMemory);
  if (s.staging) vkDestroyBuffer(vk_->act_dev, s.staging, nullptr);
  if (s.stagingMemory) vkFreeMemory(vk_->act_dev, s.stagingMemory, nullptr);
  if (s.retired) vkDestroyFence(vk_->act_dev, s.retired, nullptr);
  if (s.fence) vkDestroyFence(vk_->act_dev, s.fence, nullptr);
  if (s.view) vkDestroyImageView(vk_->act_dev, s.view, nullptr);
  if (s.image) vkDestroyImage(vk_->act_dev, s.image, nullptr);
  if (s.memory) vkFreeMemory(vk_->act_dev, s.memory, nullptr);
  if (s.cmd) vkFreeCommandBuffers(vk_->act_dev, pool_, 1, &s.cmd);
  if (s.descriptor) spareDescriptors_.push_back(s.descriptor);
  s = Surface{};
}
// After an in-session resolution change, release idle surfaces of the old size so
// the bounded pool serves the new stream. Only surfaces nothing else references,
// with conversion and compositor retirement both complete, are destroyed.
void VulkanVideo::pruneOtherSizes(uint32_t width, uint32_t height, bool planar, bool tenBit) {
  for (auto it = surfaces_.begin(); it != surfaces_.end();) {
    auto &candidate = *it;
    if (candidate.use_count() == 1 &&
        (candidate->width != width || candidate->height != height || candidate->planar != planar ||
         (planar && candidate->tenBit != tenBit)) &&
        ready(candidate) &&
        vkGetFenceStatus(vk_->act_dev, candidate->retired) == VK_SUCCESS) {
      destroySurface(*candidate);
      it = surfaces_.erase(it);
    } else {
      ++it;
    }
  }
}
void VulkanVideo::cleanupInput(Surface &s) {
  for (auto &v : s.planes) {
    if (v)
      vkDestroyImageView(vk_->act_dev, v, nullptr);
    v = VK_NULL_HANDLE;
  }
  av_frame_free(&s.imported);
}
// Maps a decoded VA-API frame into Vulkan (no copy): two plane images and their timeline
// semaphores. Sets s.tenBit and s.mapWaitNs; s.imported keeps the frame until cleanupInput.
AVVkFrame *VulkanVideo::importFrame(AVFrame *frame, Surface &s) {
  if (frame->format != AV_PIX_FMT_VAAPI)
    throw std::runtime_error("Hardware decoder did not return VA-API surfaces");
  auto src = reinterpret_cast<AVHWFramesContext *>(frame->hw_frames_ctx->data);
  const bool tenBit = src->sw_format == AV_PIX_FMT_P010;
  if (src->sw_format != AV_PIX_FMT_NV12 && !tenBit)
    throw std::runtime_error("GPU conversion requires 8-bit NV12 or 10-bit P010 surfaces");
  s.tenBit = tenBit;
  if (mapFrames_) {
    auto current = reinterpret_cast<AVHWFramesContext *>(mapFrames_->data);
    if (current->width != frame->width || current->height != frame->height || current->sw_format != src->sw_format) {
      // New stream dimensions or bit depth in the same session. Imported frames keep
      // their own reference to the old context until their conversion completes.
      diagnosticLine("STATUS Video format changed to " + std::to_string(frame->width) + " x " +
                     std::to_string(frame->height) + (tenBit ? " 10-bit" : " 8-bit") +
                     "; rebuilding the GPU frame mapping");
      av_buffer_unref(&mapFrames_);
    }
  }
  if (!mapFrames_) {
    mapFrames_ = av_hwframe_ctx_alloc(deviceRef_);
    if (!mapFrames_) throw std::bad_alloc();
    auto fc = reinterpret_cast<AVHWFramesContext *>(mapFrames_->data);
    fc->format = AV_PIX_FMT_VULKAN;
    fc->sw_format = src->sw_format;
    fc->width = frame->width;
    fc->height = frame->height;
    auto vc = static_cast<AVVulkanFramesContext *>(fc->hwctx);
    vc->flags = AV_VK_FRAME_FLAG_DISABLE_MULTIPLANE;
    avcheck(av_hwframe_ctx_init(mapFrames_), "Vulkan map frame context");
  }
  s.imported = av_frame_alloc();
  if (!s.imported) throw std::bad_alloc();
  s.imported->format = AV_PIX_FMT_VULKAN;
  s.imported->hw_frames_ctx = av_buffer_ref(mapFrames_);
  if (!s.imported->hw_frames_ctx) throw std::bad_alloc();
  const auto mapStart = monotonicNs();
  avcheck(av_hwframe_map(s.imported, frame, AV_HWFRAME_MAP_READ | AV_HWFRAME_MAP_DIRECT), "VA-API to Vulkan direct map");
  // FFmpeg waits for VA-API completion here, then imports the DMA-BUFs.
  // The earlier avcodec_receive_frame timer alone is not hardware decode time.
  s.mapWaitNs = monotonicNs() - mapStart;
  auto input = reinterpret_cast<AVVkFrame *>(s.imported->data[0]);
  if (!input->img[0] || !input->img[1] || input->img[2])
    throw std::runtime_error("Driver did not export separate luma and chroma layers");
  return input;
}
// Colour matrix, range, chroma siting, dither and this frame's foveation centres. Call after the
// decoder's metadata callback, which stores the centres the host used for this frame.
// 1.0.0.5: a VIDEO_COLOUR line whenever the stream's colour description changes: what the
// decoder reports (depth, range, matrix, primaries, transfer, chroma siting). A stream labelled
// full range whose blacks look grey is then visibly a host conversion fault, not this client's.
static std::string colourLine(const AVFrame *frame, bool tenBit) {
  const auto name = [](const char *value) { return std::string(value ? value : "unknown"); };
  std::ostringstream line;
  line << "VIDEO_COLOUR {\"bits\":" << (tenBit ? 10 : 8) << ",\"range\":\""
       << (frame->color_range == AVCOL_RANGE_JPEG ? "full" : frame->color_range == AVCOL_RANGE_MPEG ? "limited" : "unspecified")
       << "\",\"matrix\":\"" << name(av_color_space_name(frame->colorspace))
       << "\",\"primaries\":\"" << name(av_color_primaries_name(frame->color_primaries))
       << "\",\"transfer\":\"" << name(av_color_transfer_name(frame->color_trc))
       << "\",\"chroma\":\"" << name(av_chroma_location_name(frame->chroma_location)) << "\"}";
  return line.str();
}

ConversionPushConstants VulkanVideo::conversionFor(const AVFrame *frame, bool tenBit, const ConversionTarget *target,
                                                   bool &gazeCentred) const {
  const int64_t colourKey = int64_t(tenBit) | int64_t(frame->color_range & 0xff) << 1 |
                            int64_t(frame->colorspace & 0xff) << 9 | int64_t(frame->color_primaries & 0xff) << 17 |
                            int64_t(frame->color_trc & 0xff) << 25 | int64_t(frame->chroma_location & 0xff) << 33;
  if (colourKey_.exchange(colourKey) != colourKey) diagnosticLine(colourLine(frame, tenBit));
  float kr = .2126f, kb = .0722f;
  if (frame->colorspace == AVCOL_SPC_BT470BG || frame->colorspace == AVCOL_SPC_SMPTE170M) {
    kr = .299f;
    kb = .114f;
  } else if (frame->colorspace != AVCOL_SPC_UNSPECIFIED && frame->colorspace != AVCOL_SPC_BT709) {
    throw std::runtime_error("Unsupported video colour matrix");
  }
  // Centres the host used for this frame; they move with gaze when the host enables it.
  const FoveationParams foveation = target ? target->foveation : FoveationParams{};
  gazeCentred = false;
  if (foveation.enabled)
    for (int eye = 0; eye < 2; ++eye)
      for (int axis = 0; axis < 2; ++axis)
        if (std::fabs(foveation.shifts[eye][axis] - target->defaultShifts[eye][axis]) > 1e-6f) gazeCentred = true;
  return conversionPushConstants(conversionMatrix(kr, kb),
                                 rangeConstants(tenBit ? 10 : 8, frame->color_range == AVCOL_RANGE_JPEG, tenBit),
                                 chromaSiting(int(frame->chroma_location)), target ? target->dither : 1.f, foveation);
}
// RC4.3 decode-time step of the fused path: copy the decoded planes into a planar surface (a
// transfer, about 3 bytes per decoded pixel) and release the VA-API frame once the copy is done.
std::shared_ptr<Surface> VulkanVideo::copyPlanes(AVFrame *frame, uint64_t timestamp, const Pose &source,
                                                 const std::function<bool(FrameMetadata &)> &onDecoded,
                                                 const ConversionTarget &target, uint64_t started) {
  if (frame->format != AV_PIX_FMT_VAAPI)
    throw std::runtime_error("Hardware decoder did not return VA-API surfaces");
  const bool tenBit =
      reinterpret_cast<AVHWFramesContext *>(frame->hw_frames_ctx->data)->sw_format == AV_PIX_FMT_P010;
  const uint32_t width = uint32_t(frame->width), height = uint32_t(frame->height);
  FrameMetadata metadata{source};
  std::lock_guard<std::mutex> guard(surfacesMutex_);
  for (auto &candidate : surfaces_)
    if (candidate->imported && ready(candidate)) cleanupInput(*candidate);
  std::shared_ptr<Surface> s;
  for (auto &candidate : surfaces_)
    if (candidate.use_count() == 1 && candidate->planar && candidate->tenBit == tenBit && candidate->width == width &&
        candidate->height == height && ready(candidate) &&
        vkGetFenceStatus(vk_->act_dev, candidate->retired) == VK_SUCCESS) {
      s = candidate;
      break;
    }
  if (!s) {
    if (surfaces_.size() >= 12) pruneOtherSizes(width, height, true, tenBit);
    if (surfaces_.size() >= 12) return {};
    s = allocatePlanar(width, height, tenBit);
  }
  cleanupInput(*s);
  s->timestamp = timestamp;
  s->sampledAt = 0;
  s->source = source;
  s->readyAt = monotonicNs();
  s->receivedAt = s->decodeStartAt = s->decodeOutputAt = 0;
  s->conversionQueuedAt = s->firstReadyObservedAt = 0;
  s->mapWaitNs = 0;
  s->gazeCentred = false;
  s->outputWidth = target.width;
  s->outputHeight = target.height;
  AVVkFrame *input = importFrame(frame, *s);
  if (s->tenBit != tenBit) throw std::runtime_error("Decoded surface format changed during import");
  if (onDecoded && !onDecoded(metadata)) {
    cleanupInput(*s);
    return {};
  }
  s->source = metadata.source;
  s->conversion = conversionFor(frame, tenBit, &target, s->gazeCentred);
  vkcheck(vkResetCommandBuffer(s->cmd, 0), "Reset plane copy");
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkcheck(vkBeginCommandBuffer(s->cmd, &begin), "Begin plane copy");
  beginCopyTiming(*s, s->cmd); // 1.0.0.11
  auto barrier = [&](VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags fromAccess, VkAccessFlags toAccess) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = fromAccess;
    b.dstAccessMask = toAccess;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(s->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr,
                         0, nullptr, 1, &b);
  };
  auto fc = reinterpret_cast<AVHWFramesContext *>(s->imported->hw_frames_ctx->data);
  auto frameContext = static_cast<AVVulkanFramesContext *>(fc->hwctx);
  frameContext->lock_frame(fc, input);
  VkSemaphore waits[2]{};
  uint64_t values[2]{}, signals[2]{};
  for (int i = 0; i < 2; ++i) {
    waits[i] = input->sem[i];
    values[i] = input->sem_value[i];
    signals[i] = values[i] + 1;
    // Our planes were last read by a fused pass that has retired (fence checked above): overwrite.
    barrier(input->img[i], input->layout[i], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, input->access[i],
            VK_ACCESS_TRANSFER_READ_BIT);
    barrier(s->planeImages[i], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0,
            VK_ACCESS_TRANSFER_WRITE_BIT);
    VkImageCopy region{};
    region.srcSubresource = region.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.extent = {i ? (width + 1) / 2 : width, i ? (height + 1) / 2 : height, 1};
    vkCmdCopyImage(s->cmd, input->img[i], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, s->planeImages[i],
                   VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
    barrier(s->planeImages[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
            VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT);
  }
  endCopyTiming(*s, s->cmd);
  VkResult ended = vkEndCommandBuffer(s->cmd);
  if (ended != VK_SUCCESS) {
    frameContext->unlock_frame(fc, input);
    vkcheck(ended, "End plane copy");
  }
  VkPipelineStageFlags stages[2] = {VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT};
  VkTimelineSemaphoreSubmitInfo timeline{VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO};
  timeline.waitSemaphoreValueCount = timeline.signalSemaphoreValueCount = 2;
  timeline.pWaitSemaphoreValues = values;
  timeline.pSignalSemaphoreValues = signals;
  VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  submit.pNext = &timeline;
  submit.waitSemaphoreCount = submit.signalSemaphoreCount = 2;
  submit.pWaitSemaphores = submit.pSignalSemaphores = waits;
  submit.pWaitDstStageMask = stages;
  submit.commandBufferCount = 1;
  submit.pCommandBuffers = &s->cmd;
  vkResetFences(vk_->act_dev, 1, &s->fence);
  VkResult result;
  try {
    result = presentation_->submit([&] { return vkQueueSubmit(queue, 1, &submit, s->fence); });
  } catch (...) {
    frameContext->unlock_frame(fc, input);
    throw;
  }
  if (result == VK_SUCCESS)
    for (int i = 0; i < 2; ++i) {
      input->sem_value[i] = signals[i];
      input->layout[i] = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
      input->access[i] = VK_ACCESS_TRANSFER_READ_BIT;
    }
  frameContext->unlock_frame(fc, input);
  vkcheck(result, "Queue plane copy");
  s->planeLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
  s->conversionQueuedAt = monotonicNs();
  s->conversionCpuNs = s->conversionQueuedAt - started - s->mapWaitNs;
  s->uploaded = true;
  return s;
}
std::shared_ptr<Surface> VulkanVideo::convert(AVFrame *frame,
                                              uint64_t timestamp,
                                              const Pose &source,
                                              bool compatibility,
                                              const std::function<bool(FrameMetadata &)> &onDecoded,
                                              const ConversionTarget *target) {
  const auto conversionStart=monotonicNs();
#ifdef _WIN32
  // 1.0.0.7: on Windows every decoded frame (D3D11VA or CPU) is uploaded as planes through a staging
  // buffer, then shown by the fused pass or converted at once (client/platform/windows/vulkan_upload.cpp).
  // Only the RGBA recovery image takes the compatibility path below.
  if (!compatibility && target && target->width && target->height)
    return uploadPlanes(frame, timestamp, source, onDecoded, *target, conversionStart);
#endif
  // RC4.3: hardware-decoded frames keep their planes and are converted when shown (fused.comp).
  if (!compatibility && fusedAvailable() && target && target->width && target->height)
    return copyPlanes(frame, timestamp, source, onDecoded, *target, conversionStart);
  // A foveated stream decodes smaller than it is shown; the output keeps the view size.
  const uint32_t outWidth = !compatibility && target && target->width ? target->width : uint32_t(frame->width);
  const uint32_t outHeight = !compatibility && target && target->height ? target->height : uint32_t(frame->height);
  FrameMetadata metadata{source};
  std::lock_guard<std::mutex> guard(surfacesMutex_);
  // The output remains owned by SteamVR until retirement, but the decoder
  // input is needed only by our colour conversion. Release completed imports
  // promptly so several displayed/retired RGBA frames do not pin VA-API pool
  // surfaces and prevent the overlapped decoder from making progress.
  for (auto &candidate : surfaces_)
    if (candidate->imported && ready(candidate)) cleanupInput(*candidate);
  std::shared_ptr<Surface> s;
  for (auto &candidate : surfaces_)
    if (candidate.use_count() == 1 && !candidate->planar &&
        candidate->width == outWidth &&
        candidate->height == outHeight && ready(candidate) &&
        vkGetFenceStatus(vk_->act_dev, candidate->retired) == VK_SUCCESS) {
      s = candidate;
      break;
    }
  if (!s) {
    if (surfaces_.size() >= 12)
      pruneOtherSizes(outWidth, outHeight, false, false);
    if (surfaces_.size() >= 12)
      return {};
    s = allocate(outWidth, outHeight);
  }
  cleanupInput(*s);
  s->timestamp = timestamp;
  s->sampledAt = 0;
  s->source = source;
  s->readyAt = monotonicNs();
  s->receivedAt = s->decodeStartAt = s->decodeOutputAt = 0;
  s->conversionQueuedAt = s->firstReadyObservedAt = 0;
  s->mapWaitNs=0;
  s->tenBit = false;
  s->gazeCentred = false;
  VkSemaphore waits[2]{};
  uint64_t values[2]{}, signals[2]{};
  VkPipelineStageFlags stages[2] = {VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                    VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT};
  uint32_t semaphoreCount = 0;
  AVVkFrame *inputFrame = nullptr;
  if (compatibility) {
    if (onDecoded && !onDecoded(metadata)) return {};
    s->source = metadata.source;
    if (!s->staging) {
      VkBufferCreateInfo b{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
      b.size = VkDeviceSize(s->width) * s->height * 4;
      b.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
      vkcheck(vkCreateBuffer(vk_->act_dev, &b, nullptr, &s->staging),
              "Compatibility staging buffer");
      VkMemoryRequirements m;
      vkGetBufferMemoryRequirements(vk_->act_dev, s->staging, &m);
      VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
      a.allocationSize = m.size;
      a.memoryTypeIndex = memoryType(m.memoryTypeBits,
                                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                         VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
      vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &s->stagingMemory),
              "Staging memory");
      vkcheck(vkBindBufferMemory(vk_->act_dev, s->staging, s->stagingMemory, 0),
              "Bind staging");
      vkcheck(vkMapMemory(vk_->act_dev, s->stagingMemory, 0, VK_WHOLE_SIZE, 0,
                          &s->mapped),
              "Map staging");
    }
    auto sw = sws_getContext(frame->width, frame->height,
                             static_cast<AVPixelFormat>(frame->format),
                             frame->width, frame->height, AV_PIX_FMT_RGBA,
                             SWS_BILINEAR, nullptr, nullptr, nullptr);
    if (!sw)
      throw std::runtime_error("Unsupported software colour conversion");
    int matrix = frame->colorspace == AVCOL_SPC_BT470BG ||
                         frame->colorspace == AVCOL_SPC_SMPTE170M
                     ? SWS_CS_ITU601
                     : SWS_CS_ITU709;
    auto coefficients = sws_getCoefficients(matrix);
    sws_setColorspaceDetails(sw, coefficients,
                             frame->color_range == AVCOL_RANGE_JPEG,
                             coefficients, 1, 0, 1 << 16, 1 << 16);
    uint8_t *data[] = {static_cast<uint8_t *>(s->mapped)};
    int stride[] = {int(s->width * 4)};
    int rows = sws_scale(sw, frame->data, frame->linesize, 0, frame->height,
                         data, stride);
    sws_freeContext(sw);
    if (rows != frame->height)
      throw std::runtime_error("Incomplete colour conversion");
  } else {
    inputFrame = importFrame(frame, *s);
    if (onDecoded && !onDecoded(metadata)) {
      cleanupInput(*s);
      return {};
    }
    s->source = metadata.source;
    const bool tenBit = s->tenBit;
    for (int i = 0; i < 2; ++i) {
      VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
      vi.image = inputFrame->img[i];
      vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
      vi.format = tenBit ? (i ? VK_FORMAT_R16G16_UNORM : VK_FORMAT_R16_UNORM)
                         : (i ? VK_FORMAT_R8G8_UNORM : VK_FORMAT_R8_UNORM);
      vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
      vkcheck(vkCreateImageView(vk_->act_dev, &vi, nullptr, &s->planes[i]),
              "Video plane view");
    }
    VkDescriptorImageInfo infos[3] = {
        {sampler_, s->planes[0], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
        {sampler_, s->planes[1], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
        {VK_NULL_HANDLE, s->view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet writes[3]{};
    for (int i = 0; i < 3; ++i) {
      writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      writes[i].dstSet = s->descriptor;
      writes[i].dstBinding = i;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType =
          i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                 : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      writes[i].pImageInfo = &infos[i];
    }
    vkUpdateDescriptorSets(vk_->act_dev, 3, writes, 0, nullptr);
  }
  vkcheck(vkResetCommandBuffer(s->cmd, 0), "Reset conversion commands");
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkcheck(vkBeginCommandBuffer(s->cmd, &begin), "Begin conversion");
  auto barrier = [&](VkImage image, VkImageLayout old, VkImageLayout to,
                     VkAccessFlags fromAccess, VkAccessFlags toAccess) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = fromAccess;
    b.dstAccessMask = toAccess;
    b.oldLayout = old;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(s->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                         VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0,
                         nullptr, 1, &b);
  };
  barrier(s->image, s->layout,
          compatibility ? VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL
                        : VK_IMAGE_LAYOUT_GENERAL,
          s->layout == VK_IMAGE_LAYOUT_UNDEFINED ? 0
                                                 : VK_ACCESS_MEMORY_READ_BIT,
          compatibility ? VK_ACCESS_TRANSFER_WRITE_BIT
                        : VK_ACCESS_SHADER_WRITE_BIT);
  AVVulkanFramesContext *frameContext = nullptr;
  AVHWFramesContext *fc = nullptr;
  if (compatibility) {
    VkBufferImageCopy region{};
    region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.imageExtent = {s->width, s->height, 1};
    vkCmdCopyBufferToImage(s->cmd, s->staging, s->image,
                           VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
  } else {
    fc =
        reinterpret_cast<AVHWFramesContext *>(s->imported->hw_frames_ctx->data);
    frameContext = static_cast<AVVulkanFramesContext *>(fc->hwctx);
    frameContext->lock_frame(fc, inputFrame);
    for (int i = 0; i < 2; ++i) {
      waits[i] = inputFrame->sem[i];
      values[i] = inputFrame->sem_value[i];
      signals[i] = values[i] + 1;
      barrier(inputFrame->img[i], inputFrame->layout[i],
              VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, inputFrame->access[i],
              VK_ACCESS_SHADER_READ_BIT);
    }
    semaphoreCount = 2;
    ConversionPushConstants constants;
    try {
      constants = conversionFor(frame, s->tenBit, target, s->gazeCentred);
    } catch (...) {
      frameContext->unlock_frame(fc, inputFrame);
      throw;
    }
    s->conversion = constants; // the frame's foveation centres, for the eye-tracking panel
    vkCmdBindPipeline(s->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_);
    vkCmdBindDescriptorSets(s->cmd, VK_PIPELINE_BIND_POINT_COMPUTE,
                            pipelineLayout_, 0, 1, &s->descriptor, 0, nullptr);
    vkCmdPushConstants(s->cmd, pipelineLayout_, VK_SHADER_STAGE_COMPUTE_BIT, 0,
                       sizeof(constants), &constants);
    vkCmdDispatch(s->cmd, (s->width + 15) / 16, (s->height + 15) / 16, 1);
  }
  barrier(s->image,
          compatibility ? VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL
                        : VK_IMAGE_LAYOUT_GENERAL,
          VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
          compatibility ? VK_ACCESS_TRANSFER_WRITE_BIT
                        : VK_ACCESS_SHADER_WRITE_BIT,
          VK_ACCESS_TRANSFER_READ_BIT);
  s->layout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  VkResult ended = vkEndCommandBuffer(s->cmd);
  if (ended != VK_SUCCESS) {
    if (frameContext)
      frameContext->unlock_frame(fc, inputFrame);
    vkcheck(ended, "End conversion");
  }
  VkTimelineSemaphoreSubmitInfo timeline{
      VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO};
  timeline.waitSemaphoreValueCount = timeline.signalSemaphoreValueCount =
      semaphoreCount;
  timeline.pWaitSemaphoreValues = values;
  timeline.pSignalSemaphoreValues = signals;
  VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  submit.pNext = semaphoreCount ? &timeline : nullptr;
  submit.waitSemaphoreCount = submit.signalSemaphoreCount = semaphoreCount;
  submit.pWaitSemaphores = submit.pSignalSemaphores = waits;
  submit.pWaitDstStageMask = stages;
  submit.commandBufferCount = 1;
  submit.pCommandBuffers = &s->cmd;
  vkResetFences(vk_->act_dev, 1, &s->fence);
  VkResult result;
  try {
    result=presentation_->submit([&] { return vkQueueSubmit(queue, 1, &submit, s->fence); });
  } catch (...) {
    if (frameContext) frameContext->unlock_frame(fc,inputFrame);
    throw;
  }
  if (frameContext) {
    if (result == VK_SUCCESS)
      for (int i = 0; i < 2; ++i) {
        inputFrame->sem_value[i] = signals[i];
        inputFrame->layout[i] = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        inputFrame->access[i] = VK_ACCESS_SHADER_READ_BIT;
      }
    frameContext->unlock_frame(fc, inputFrame);
  }
  vkcheck(result, "Queue conversion");
  s->conversionQueuedAt = monotonicNs();
  s->conversionCpuNs=s->conversionQueuedAt-conversionStart-s->mapWaitNs;
  s->uploaded = true;
  return s;
}
std::shared_ptr<Surface> VulkanVideo::neutral() {
  AVFrame *frame = av_frame_alloc();
  frame->format = AV_PIX_FMT_RGBA;
  frame->width = 128;
  frame->height = 64;
  avcheck(av_frame_get_buffer(frame, 0), "Recovery frame");
  for (int y = 0; y < frame->height; ++y)
    for (int x = 0; x < frame->width; ++x) {
      auto p = frame->data[0] + y * frame->linesize[0] + x * 4;
      // Neutral recovery image, not a second world-space grid.
      p[0] = 4; p[1] = 4; p[2] = 5;
      p[3] = 255;
    }
  try {
    auto s = convert(frame, 0, Pose{}, true);
    av_frame_free(&frame);
    return s;
  } catch (...) {
    av_frame_free(&frame);
    throw;
  }
}
bool VulkanVideo::submit(OpenVRDevice &runtime,
                         const std::shared_ptr<Surface> &s, const Pose &pose) {
  // A planar (fused-path) frame has no image of its own: it is shown through submitReaimed.
  if (!s || s->planar || !ready(s) || vkGetFenceStatus(vk_->act_dev, s->retired) != VK_SUCCESS)
    return false;
  submittedSurface_=s;
  vr::VRVulkanTextureData_t data{};
  data.m_nImage = reinterpret_cast<uint64_t>(s->image);
  data.m_pDevice = vk_->act_dev;
  data.m_pPhysicalDevice = vk_->phys_dev;
  data.m_pInstance = vk_->inst;
  data.m_pQueue = queue;
  data.m_nQueueFamilyIndex = family;
  data.m_nWidth = s->width;
  data.m_nHeight = s->height;
  data.m_nFormat = VK_FORMAT_R8G8B8A8_UNORM;
  data.m_nSampleCount = 1;
  vr::VRTextureWithPose_t texture{};
  texture.handle = &data;
  texture.eType = vr::TextureType_Vulkan;
  texture.eColorSpace = vr::ColorSpace_Gamma;
  std::memcpy(texture.mDeviceToAbsoluteTracking.m, pose.m, sizeof(pose.m));
  bool ok = true;
  int failure = 0;
  presentation_->submit([&] {
  for (int eye = 0; eye < 2; ++eye) {
    vr::VRTextureBounds_t bounds{eye * .5f, 0, (eye + 1) * .5f, 1};
    auto r = runtime.compositor->Submit(static_cast<vr::EVREye>(eye), &texture,
                                        &bounds, vr::Submit_TextureWithPose);
    if (r != vr::VRCompositorError_None) {
      ok = false;
      if (r != vr::VRCompositorError_DoNotHaveFocus)
        failure = static_cast<int>(r);
    }
  }
  });
  if (failure)
    throw std::runtime_error("SteamVR texture submission failed: " +
                             std::to_string(failure));
  return ok;
}
// An output image of this size that SteamVR has finished with (retirement fence), or a free slot.
ReaimTarget *VulkanVideo::freeTarget(uint32_t width, uint32_t height) {
  for (auto &candidate : reaimTargets_)
    if (candidate.image && candidate.width == width && candidate.height == height &&
        vkGetFenceStatus(vk_->act_dev, candidate.retired) == VK_SUCCESS)
      return &candidate;
  for (auto &candidate : reaimTargets_)
    if (!candidate.image || vkGetFenceStatus(vk_->act_dev, candidate.retired) == VK_SUCCESS) {
      destroyReaimTarget(candidate);
      createReaimTarget(candidate, width, height);
      return &candidate;
    }
  return nullptr;
}
// Submits the recorded pass of `t` and hands its image to SteamVR labelled with `label`.
int VulkanVideo::submitTarget(OpenVRDevice &runtime, ReaimTarget &t, const std::shared_ptr<Surface> &s, const Pose &label) {
  submittedSurface_ = s;
  submittedTarget_ = &t;
  vr::VRVulkanTextureData_t data{};
  data.m_nImage = reinterpret_cast<uint64_t>(t.image);
  data.m_pDevice = vk_->act_dev;
  data.m_pPhysicalDevice = vk_->phys_dev;
  data.m_pInstance = vk_->inst;
  data.m_pQueue = queue;
  data.m_nQueueFamilyIndex = family;
  data.m_nWidth = t.width;
  data.m_nHeight = t.height;
  data.m_nFormat = VK_FORMAT_R8G8B8A8_UNORM;
  data.m_nSampleCount = 1;
  vr::VRTextureWithPose_t texture{};
  texture.handle = &data;
  texture.eType = vr::TextureType_Vulkan;
  texture.eColorSpace = vr::ColorSpace_Gamma;
  std::memcpy(texture.mDeviceToAbsoluteTracking.m, label.m, sizeof(label.m));
  bool ok = true;
  int failure = 0;
  presentation_->submit([&] {
    VkSubmitInfo work{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    work.commandBufferCount = 1;
    work.pCommandBuffers = &t.cmd;
    vkcheck(vkQueueSubmit(queue, 1, &work, VK_NULL_HANDLE), "Submit frame pass");
    for (int eye = 0; eye < 2; ++eye) {
      vr::VRTextureBounds_t bounds{eye * .5f, 0, (eye + 1) * .5f, 1};
      auto r = runtime.compositor->Submit(static_cast<vr::EVREye>(eye), &texture, &bounds, vr::Submit_TextureWithPose);
      if (r != vr::VRCompositorError_None) {
        ok = false;
        if (r != vr::VRCompositorError_DoNotHaveFocus) failure = static_cast<int>(r);
      }
    }
  });
  if (failure)
    throw std::runtime_error("SteamVR texture submission failed: " + std::to_string(failure));
  return ok ? 1 : 0;
}
int VulkanVideo::submitReaimed(OpenVRDevice &runtime, const std::shared_ptr<Surface> &s, const Pose &source,
                               const Pose &target, const std::array<VrostView, 2> &eyes) {
  if (s && s->planar) return submitFused(runtime, s, source, target, eyes);
  if (reaimPipeline_ == VK_NULL_HANDLE || !reaimPool_ || !ready(s) ||
      vkGetFenceStatus(vk_->act_dev, s->retired) != VK_SUCCESS || s->layout != VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL)
    return -1;
  ReaimTarget *t = freeTarget(s->width, s->height);
  if (!t) return -1;
  VkDescriptorImageInfo infos[2] = {{sampler_, s->view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                    {VK_NULL_HANDLE, t->view, VK_IMAGE_LAYOUT_GENERAL}};
  VkWriteDescriptorSet writes[2]{};
  for (int i = 0; i < 2; ++i) {
    writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    writes[i].dstSet = t->descriptor;
    writes[i].dstBinding = i;
    writes[i].descriptorCount = 1;
    writes[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    writes[i].pImageInfo = &infos[i];
  }
  vkUpdateDescriptorSets(vk_->act_dev, 2, writes, 0, nullptr);
  vkcheck(vkResetCommandBuffer(t->cmd, 0), "Reset re-aim commands");
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkcheck(vkBeginCommandBuffer(t->cmd, &begin), "Begin re-aim");
  auto barrier = [&](VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags fromAccess,
                     VkAccessFlags toAccess) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = fromAccess;
    b.dstAccessMask = toAccess;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(t->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0,
                         nullptr, 0, nullptr, 1, &b);
  };
  // The source may still be read by SteamVR's copy of an earlier refresh: the barrier
  // orders the layout change after every earlier use on this queue.
  barrier(s->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
          VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT);
  barrier(t->image, t->layout, VK_IMAGE_LAYOUT_GENERAL,
          t->layout == VK_IMAGE_LAYOUT_UNDEFINED ? 0 : VK_ACCESS_MEMORY_READ_BIT, VK_ACCESS_SHADER_WRITE_BIT);
  const auto constants = reaimConstants(source, target, eyes);
  vkCmdBindPipeline(t->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, reaimPipeline_);
  vkCmdBindDescriptorSets(t->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, reaimLayout_, 0, 1, &t->descriptor, 0, nullptr);
  vkCmdPushConstants(t->cmd, reaimLayout_, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(constants), &constants);
  vkCmdDispatch(t->cmd, (t->width + 15) / 16, (t->height + 15) / 16, 1);
  barrier(t->image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT,
          VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_MEMORY_READ_BIT);
  barrier(s->image, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
          VK_ACCESS_SHADER_READ_BIT, VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_MEMORY_READ_BIT);
  vkcheck(vkEndCommandBuffer(t->cmd), "End re-aim");
  t->layout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  t->needsClear = true; // 1.0.0.11: filled to the edges; a fused pass would leave picture where it skips pixels
  t->timed = false;
  return submitTarget(runtime, *t, s, target);
}
// RC4.3: one pass from the frame's planes to the image SteamVR shows (fused.comp). `source` ->
// `target` is the re-aim (equal poses: none); the frame goes to SteamVR labelled `target`.
// 1.0.0.11: the constants go in the target's uniform buffer, each eye is a workgroup layer, pixels
// outside the visible spans keep the black of a clear, and the pass is timed on the GPU.
int VulkanVideo::submitFused(OpenVRDevice &runtime, const std::shared_ptr<Surface> &s, const Pose &source,
                             const Pose &target, const std::array<VrostView, 2> &eyes) {
  if (!fusedAvailable() || !ready(s) || vkGetFenceStatus(vk_->act_dev, s->retired) != VK_SUCCESS ||
      s->planeLayout != VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL || !s->outputWidth || !s->outputHeight ||
      !s->planesWidth || !s->planesHeight)
    return -1;
  ReaimTarget *t = freeTarget(s->outputWidth, s->outputHeight);
  if (!t) return -1;
  const uint32_t index = uint32_t(t - reaimTargets_.data());
  // The target is free (freeTarget): its previous pass has completed, so its time stamps can be read.
  uint64_t ns = 0;
  if (t->timed && queryDuration(passQueries_, index * 2, ns)) passGpu_.add(ns);
  t->timed = false;
  if (t->spansGeneration != spansGeneration_) {
    writeSpans(*t);
    t->needsClear = true;
  }
  shownShare_ = t->visibleShare;
  const auto rows = reaimConstants(source, target, eyes);
  const auto frame = fusedFrameConstants(fusedParameters(s->conversion, rows.rows, sharpening.load()), s->planesWidth,
                                         s->planesHeight, t->width / 2, t->height);
  std::memcpy(t->frameMapped, &frame, sizeof(frame));
  VkDescriptorImageInfo infos[3] = {{sampler_, s->planeViews[0], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                    {sampler_, s->planeViews[1], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                    {VK_NULL_HANDLE, t->view, VK_IMAGE_LAYOUT_GENERAL}};
  VkWriteDescriptorSet writes[3]{};
  for (int i = 0; i < 3; ++i) {
    writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    writes[i].dstSet = t->fusedDescriptor;
    writes[i].dstBinding = i;
    writes[i].descriptorCount = 1;
    writes[i].descriptorType = i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    writes[i].pImageInfo = &infos[i];
  }
  vkUpdateDescriptorSets(vk_->act_dev, 3, writes, 0, nullptr);
  vkcheck(vkResetCommandBuffer(t->cmd, 0), "Reset frame pass");
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkcheck(vkBeginCommandBuffer(t->cmd, &begin), "Begin frame pass");
  VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
  b.srcAccessMask = t->layout == VK_IMAGE_LAYOUT_UNDEFINED ? 0 : VK_ACCESS_MEMORY_READ_BIT;
  b.dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
  b.oldLayout = t->needsClear ? VK_IMAGE_LAYOUT_UNDEFINED : t->layout; // a clear discards the old picture anyway
  b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
  b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
  b.image = t->image;
  b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  vkCmdPipelineBarrier(t->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                       VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1,
                       &b);
  if (t->needsClear) {
    // Black (opaque) where the pass will not write: once per image, not per frame.
    const VkClearColorValue black{{0.f, 0.f, 0.f, 1.f}};
    vkCmdClearColorImage(t->cmd, t->image, VK_IMAGE_LAYOUT_GENERAL, &black, 1, &b.subresourceRange);
    b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    b.oldLayout = b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    vkCmdPipelineBarrier(t->cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0,
                         nullptr, 1, &b);
    t->needsClear = false;
  }
  vkCmdBindPipeline(t->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, fusedPipeline_);
  vkCmdBindDescriptorSets(t->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, fusedLayout_, 0, 1, &t->fusedDescriptor, 0, nullptr);
  if (passQueries_) {
    vkCmdResetQueryPool(t->cmd, passQueries_, index * 2, 2);
    // After the clear and any earlier work have completed, so the time is the pass's alone.
    vkCmdWriteTimestamp(t->cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, passQueries_, index * 2);
  }
  vkCmdDispatch(t->cmd, (t->width / 2 + 15) / 16, (t->height + 15) / 16, 2); // z: the eye
  if (passQueries_) {
    vkCmdWriteTimestamp(t->cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, passQueries_, index * 2 + 1);
    t->timed = true;
  }
  b.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
  b.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_MEMORY_READ_BIT;
  b.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
  b.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  vkCmdPipelineBarrier(t->cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr,
                       0, nullptr, 1, &b);
  vkcheck(vkEndCommandBuffer(t->cmd), "End frame pass");
  t->layout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  return submitTarget(runtime, *t, s, target);
}
void VulkanVideo::finishFrame() {
  auto submitted=std::move(submittedSurface_);
  auto *target=submittedTarget_;
  submittedTarget_=nullptr;
  presentation_->finishFrame([&] {
    VkSubmitInfo completion{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    if (submitted) {
      vkcheck(vkResetFences(vk_->act_dev,1,&submitted->retired),"Reset retirement fence");
      vkcheck(vkQueueSubmit(queue,1,&completion,submitted->retired),"Compositor queue retirement");
    }
    if (target) {
      vkcheck(vkResetFences(vk_->act_dev,1,&target->retired),"Reset re-aim retirement fence");
      vkcheck(vkQueueSubmit(queue,1,&completion,target->retired),"Re-aim retirement");
    }
  });
}
VulkanVideo::~VulkanVideo() {
  if (vk_) {
    vkDeviceWaitIdle(vk_->act_dev);
    for (auto &s : surfaces_)
      destroySurface(*s);
    for (auto &t : reaimTargets_) {
      destroyReaimTarget(t);
      if (t.retired) vkDestroyFence(vk_->act_dev, t.retired, nullptr);
      if (t.cmd) vkFreeCommandBuffers(vk_->act_dev, reaimPool_, 1, &t.cmd);
      if (t.frameMapped) vkUnmapMemory(vk_->act_dev, t.frameMemory);
      if (t.frame) vkDestroyBuffer(vk_->act_dev, t.frame, nullptr);
      if (t.frameMemory) vkFreeMemory(vk_->act_dev, t.frameMemory, nullptr);
      if (t.spansMapped) vkUnmapMemory(vk_->act_dev, t.spansMemory);
      if (t.spans) vkDestroyBuffer(vk_->act_dev, t.spans, nullptr);
      if (t.spansMemory) vkFreeMemory(vk_->act_dev, t.spansMemory, nullptr);
    }
    if (passQueries_) vkDestroyQueryPool(vk_->act_dev, passQueries_, nullptr);
    if (copyQueries_) vkDestroyQueryPool(vk_->act_dev, copyQueries_, nullptr);
    if (reaimPool_) vkDestroyCommandPool(vk_->act_dev, reaimPool_, nullptr);
    if (fusedPipeline_) vkDestroyPipeline(vk_->act_dev, fusedPipeline_, nullptr);
    if (fusedLayout_) vkDestroyPipelineLayout(vk_->act_dev, fusedLayout_, nullptr);
    if (fusedSetLayout_) vkDestroyDescriptorSetLayout(vk_->act_dev, fusedSetLayout_, nullptr);
    if (reaimPipeline_) vkDestroyPipeline(vk_->act_dev, reaimPipeline_, nullptr);
    if (reaimLayout_) vkDestroyPipelineLayout(vk_->act_dev, reaimLayout_, nullptr);
    if (reaimDescriptors_) vkDestroyDescriptorPool(vk_->act_dev, reaimDescriptors_, nullptr);
    if (reaimSetLayout_) vkDestroyDescriptorSetLayout(vk_->act_dev, reaimSetLayout_, nullptr);
    if (sampler_)
      vkDestroySampler(vk_->act_dev, sampler_, nullptr);
    if (pipeline_)
      vkDestroyPipeline(vk_->act_dev, pipeline_, nullptr);
    if (pipelineLayout_)
      vkDestroyPipelineLayout(vk_->act_dev, pipelineLayout_, nullptr);
    if (descriptors_)
      vkDestroyDescriptorPool(vk_->act_dev, descriptors_, nullptr);
    if (descriptorLayout_)
      vkDestroyDescriptorSetLayout(vk_->act_dev, descriptorLayout_, nullptr);
    if (pool_)
      vkDestroyCommandPool(vk_->act_dev, pool_, nullptr);
  }
  av_buffer_unref(&mapFrames_);
  av_buffer_unref(&deviceRef_);
}
} // namespace vrost
