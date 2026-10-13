// 1.0.0.7: the Windows client's decode-time step, the counterpart of VulkanVideo::copyPlanes (Linux,
// client/platform/linux/vulkan.cpp). FFmpeg has no D3D11 -> Vulkan mapping, so a decoded frame is
// written to the surface's host-visible staging buffer, a D3D11VA frame by FFmpeg's download and a
// CPU-decoded one by writePlanar (client/device/planar_upload.hpp), and copied into the surface's
// plane images on the GPU. From there everything is as on Linux: the fused pass converts, expands,
// re-aims and sharpens the planes when the frame is shown; with two passes (--two-pass) convert.comp
// converts them at once. 1.0.0.11: the copy into the plane images is timed on the GPU, as on Linux.
#include "client/platform/linux/video.hpp"
#include "client/device/decoded_planes.hpp"
#include <cstring>

namespace vrost {
namespace {

bool hasExtension(const std::vector<VkExtensionProperties> &extensions, const char *name) {
  for (const auto &extension : extensions)
    if (std::strcmp(extension.extensionName, name) == 0) return true;
  return false;
}

// FFmpeg's D3D11VA download (a GPU copy to its staging texture, a wait for it, and rows copied to
// the destination frame) straight into the staging buffer: the destination frame only borrows it.
void downloadD3D11(const AVFrame *frame, AVPixelFormat format, const PlanarLayout &layout, uint8_t *staging) {
  AVFrame *destination = av_frame_alloc();
  if (!destination) throw std::bad_alloc();
  destination->format = format;
  destination->width = frame->width;
  destination->height = frame->height;
  destination->data[0] = staging;
  destination->linesize[0] = int(layout.lumaPitch);
  destination->data[1] = staging + layout.chromaOffset;
  destination->linesize[1] = int(layout.chromaPitch);
  destination->buf[0] = av_buffer_create(staging, layout.size, [](void *, uint8_t *) {}, nullptr, 0);
  if (!destination->buf[0]) {
    av_frame_free(&destination);
    throw std::bad_alloc();
  }
  const int result = av_hwframe_transfer_data(destination, frame, 0);
  av_frame_free(&destination);
  if (result < 0) {
    char message[AV_ERROR_MAX_STRING_SIZE]{};
    av_strerror(result, message, sizeof(message));
    throw std::runtime_error(std::string("D3D11VA frame download failed: ") + message);
  }
}

} // namespace

void VulkanVideo::openWindows() {
  VkPhysicalDeviceIDProperties id{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
  VkPhysicalDeviceProperties2 properties{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
  properties.pNext = &id;
  vkGetPhysicalDeviceProperties2(vk_->phys_dev, &properties);
  std::memcpy(luid_, id.deviceLUID, sizeof(luid_));
  adapter = windows::findAdapter(luid_, id.deviceLUIDValid == VK_TRUE);
  if (adapter.found)
    diagnosticLine("STATUS SteamVR's GPU for D3D11VA: " + adapter.name + " (DXGI adapter " + std::to_string(adapter.index) +
                   "); decoder profiles h264=" + std::to_string(adapter.h264) + " hevc=" + std::to_string(adapter.hevc) +
                   " hevc_main10=" + std::to_string(adapter.hevcMain10) + " av1=" + std::to_string(adapter.av1) +
                   " av1_10bit=" + std::to_string(adapter.av1TenBit) + (adapter.problem.empty() ? "" : "; " + adapter.problem));
  else
    diagnosticLine("STATUS SteamVR's GPU for D3D11VA not found: " + adapter.problem);
  // Future zero-copy work: whether this Vulkan device could decode the stream itself.
  uint32_t count = 0;
  vkEnumerateDeviceExtensionProperties(vk_->phys_dev, nullptr, &count, nullptr);
  std::vector<VkExtensionProperties> extensions(count);
  vkEnumerateDeviceExtensionProperties(vk_->phys_dev, nullptr, &count, extensions.data());
  vkGetPhysicalDeviceQueueFamilyProperties(vk_->phys_dev, &count, nullptr);
  std::vector<VkQueueFamilyProperties> families(count);
  vkGetPhysicalDeviceQueueFamilyProperties(vk_->phys_dev, &count, families.data());
  bool decodeQueue = false;
  for (const auto &family : families)
    if (family.queueFlags & VK_QUEUE_VIDEO_DECODE_BIT_KHR) decodeQueue = true;
  const bool video = decodeQueue && hasExtension(extensions, "VK_KHR_video_queue") &&
                     hasExtension(extensions, "VK_KHR_video_decode_queue");
  auto codec = [&](const char *name) { return video && hasExtension(extensions, name) ? "1" : "0"; };
  diagnosticLine(std::string("STATUS Vulkan Video decode support: h264=") + codec("VK_KHR_video_decode_h264") +
                 " h265=" + codec("VK_KHR_video_decode_h265") + " av1=" + codec("VK_KHR_video_decode_av1") +
                 " decode_queue=" + (decodeQueue ? "1" : "0") + " (not used yet)");
}

std::shared_ptr<Surface> VulkanVideo::uploadPlanes(AVFrame *frame, uint64_t timestamp, const Pose &source,
                                                   const std::function<bool(FrameMetadata &)> &onDecoded,
                                                   const ConversionTarget &target, uint64_t started) {
  AVPixelFormat download = AV_PIX_FMT_NONE;
  const PlanarFormat format = planarFormat(frame, download);
  const bool tenBit = planarTenBit(format);
  const uint32_t width = uint32_t(frame->width), height = uint32_t(frame->height);
  const PlanarLayout layout = planarLayout(width, height, tenBit);
  const bool fused = fusedAvailable();
  FrameMetadata metadata{source};
  std::lock_guard<std::mutex> guard(surfacesMutex_);
  std::shared_ptr<Surface> s;
  if (fused) {
    // As copyPlanes: a planar surface of this decoded size and depth that nothing uses any more.
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
  } else {
    // RC4.2's two passes: an RGBA surface of the shown size, with plane images of the decoded size.
    for (auto &candidate : surfaces_)
      if (candidate.use_count() == 1 && !candidate->planar && candidate->width == target.width &&
          candidate->height == target.height && ready(candidate) &&
          vkGetFenceStatus(vk_->act_dev, candidate->retired) == VK_SUCCESS) {
        s = candidate;
        break;
      }
    if (!s) {
      if (surfaces_.size() >= 12) pruneOtherSizes(target.width, target.height, false, false);
      if (surfaces_.size() >= 12) return {};
      s = allocate(target.width, target.height);
    }
    if (s->planeImages[0] && (s->planesWidth != width || s->planesHeight != height || s->planesTenBit != tenBit))
      destroyPlanes(*s);
    if (!s->planeImages[0]) createPlanes(*s, width, height, tenBit);
  }
  if (s->planeStagingSize < layout.size) {
    if (s->planeMapped) vkUnmapMemory(vk_->act_dev, s->planeStagingMemory);
    if (s->planeStaging) vkDestroyBuffer(vk_->act_dev, s->planeStaging, nullptr);
    if (s->planeStagingMemory) vkFreeMemory(vk_->act_dev, s->planeStagingMemory, nullptr);
    s->planeMapped = nullptr;
    s->planeStaging = VK_NULL_HANDLE;
    s->planeStagingMemory = VK_NULL_HANDLE;
    s->planeStagingSize = 0;
    VkBufferCreateInfo b{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    b.size = VkDeviceSize(layout.size);
    b.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    vkcheck(vkCreateBuffer(vk_->act_dev, &b, nullptr, &s->planeStaging), "Frame upload buffer");
    VkMemoryRequirements m;
    vkGetBufferMemoryRequirements(vk_->act_dev, s->planeStaging, &m);
    VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    a.allocationSize = m.size;
    a.memoryTypeIndex = stagingMemoryType(vk_->phys_dev, m.memoryTypeBits, s->planeStagingCoherent);
    vkcheck(vkAllocateMemory(vk_->act_dev, &a, nullptr, &s->planeStagingMemory), "Frame upload memory");
    vkcheck(vkBindBufferMemory(vk_->act_dev, s->planeStaging, s->planeStagingMemory, 0), "Bind frame upload buffer");
    vkcheck(vkMapMemory(vk_->act_dev, s->planeStagingMemory, 0, VK_WHOLE_SIZE, 0, &s->planeMapped), "Map frame upload buffer");
    s->planeStagingSize = VkDeviceSize(layout.size);
  }
  s->timestamp = timestamp;
  s->sampledAt = 0;
  s->source = source;
  s->readyAt = monotonicNs();
  s->receivedAt = s->decodeStartAt = s->decodeOutputAt = 0;
  s->conversionQueuedAt = s->firstReadyObservedAt = 0;
  s->mapWaitNs = 0;
  s->tenBit = tenBit;
  s->gazeCentred = false;
  if (fused) {
    s->outputWidth = target.width;
    s->outputHeight = target.height;
  }
  // The download waits for the decoder (Linux's map/sync/import slot in the TIMING line).
  const auto transferStart = monotonicNs();
  auto *staging = static_cast<uint8_t *>(s->planeMapped);
  if (download != AV_PIX_FMT_NONE)
    downloadD3D11(frame, download, layout, staging);
  else
    writePlanar(layout, staging, format, frame->data, frame->linesize);
  if (!s->planeStagingCoherent) {
    VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
    range.memory = s->planeStagingMemory;
    range.size = VK_WHOLE_SIZE;
    vkcheck(vkFlushMappedMemoryRanges(vk_->act_dev, 1, &range), "Flush frame upload");
  }
  s->mapWaitNs = monotonicNs() - transferStart;
  if (onDecoded && !onDecoded(metadata)) return {};
  s->source = metadata.source;
  s->conversion = conversionFor(frame, tenBit, &target, s->gazeCentred);
  vkcheck(vkResetCommandBuffer(s->cmd, 0), "Reset frame upload");
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkcheck(vkBeginCommandBuffer(s->cmd, &begin), "Begin frame upload");
  beginCopyTiming(*s, s->cmd);
  recordPlanarCopy(s->cmd, s->planeStaging, layout, s->planeImages);
  endCopyTiming(*s, s->cmd);
  if (!fused) {
    // convert.comp at once, from the plane images into the surface's RGBA image (as convert() on Linux).
    VkDescriptorImageInfo infos[3] = {{sampler_, s->planeViews[0], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                      {sampler_, s->planeViews[1], VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL},
                                      {VK_NULL_HANDLE, s->view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet writes[3]{};
    for (int i = 0; i < 3; ++i) {
      writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
      writes[i].dstSet = s->descriptor;
      writes[i].dstBinding = i;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = i == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
      writes[i].pImageInfo = &infos[i];
    }
    vkUpdateDescriptorSets(vk_->act_dev, 3, writes, 0, nullptr);
    auto barrier = [&](VkImageLayout from, VkImageLayout to, VkAccessFlags fromAccess, VkAccessFlags toAccess) {
      VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
      b.srcAccessMask = fromAccess;
      b.dstAccessMask = toAccess;
      b.oldLayout = from;
      b.newLayout = to;
      b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
      b.image = s->image;
      b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
      vkCmdPipelineBarrier(s->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr,
                           0, nullptr, 1, &b);
    };
    barrier(s->layout, VK_IMAGE_LAYOUT_GENERAL, s->layout == VK_IMAGE_LAYOUT_UNDEFINED ? 0 : VK_ACCESS_MEMORY_READ_BIT,
            VK_ACCESS_SHADER_WRITE_BIT);
    vkCmdBindPipeline(s->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_);
    vkCmdBindDescriptorSets(s->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout_, 0, 1, &s->descriptor, 0, nullptr);
    vkCmdPushConstants(s->cmd, pipelineLayout_, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(s->conversion), &s->conversion);
    vkCmdDispatch(s->cmd, (s->width + 15) / 16, (s->height + 15) / 16, 1);
    barrier(VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT,
            VK_ACCESS_TRANSFER_READ_BIT);
    s->layout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  }
  vkcheck(vkEndCommandBuffer(s->cmd), "End frame upload");
  VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  submit.commandBufferCount = 1;
  submit.pCommandBuffers = &s->cmd;
  vkResetFences(vk_->act_dev, 1, &s->fence);
  const VkResult result = presentation_->submit([&] { return vkQueueSubmit(queue, 1, &submit, s->fence); });
  vkcheck(result, "Queue frame upload");
  s->planeLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
  s->conversionQueuedAt = monotonicNs();
  s->conversionCpuNs = s->conversionQueuedAt - started - s->mapWaitNs;
  s->uploaded = true;
  return s;
}

} // namespace vrost
