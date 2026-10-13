// 1.0.0.11: the client's own frame path — VulkanVideo in client/platform/linux/vulkan.cpp, as both clients run
// it — on any Vulkan device (normally Mesa's lavapipe; for the Windows client under Wine), with SteamVR replaced by
// a stand-in runtime whose compositor reads back every image it is given (tests/openvr_stand_in.hpp).
// tests/fused_shader_tests.cpp checks the frame pass's shader against its CPU model; this checks the client's code
// around it, which before 1.0.0.11 only ran on hardware:
//  1. open(): the frame pass with its descriptor sets, its uniform and storage buffer per output image, GPU time
//     stamps;
//  2. decoded frames shown through submitReaimed: a flat colour, a pattern that must arrive pixel for pixel in both
//     eyes, and a ramp through foveated encoding (each output pixel where ALVR's inverse puts it), at the size
//     SteamVR is given. On Windows they are CPU-decoded frames taken by the client's own upload (convert ->
//     uploadPlanes, client/platform/windows/vulkan_upload.cpp); on Linux, without a VA-API decoder, their planes
//     are copied in as copyPlanes copies them. Either copy is timed on the GPU;
//  3. the lens area (setVisibleArea): pixels outside each row's span black, the rest unchanged; a description that
//     would leave under half an eye ignored;
//  4. an output image the two-pass path filled, then taken by the frame pass: cleared first;
//  5. a smaller shown size (the companion's 'scale'): an image and spans of its own;
//  6. the GPU time of the pass and of the plane copy reported.
// Usage: video_pass_tests <directory with convert.spv, reaim.spv and fused.spv>. Exit 77 (skip) without Vulkan.
#include "client/platform/linux/video.hpp"
#include "tests/openvr_stand_in.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

namespace vrost {
// Only the runtime's interface pointers are used here; SteamVR's loader and tracking
// (client/device/openvr_device.cpp) are not part of this test.
OpenVRDevice::~OpenVRDevice() {}

// Linux: the decode-time copy of a decoded frame's planes (copyPlanes) without a VA-API decoder: 8-bit planes
// written to a staging buffer and copied into a planar surface, timed as copyPlanes times its copy.
struct VideoTestAccess {
  static VkDevice device(VulkanVideo &v) { return v.vk_->act_dev; }
  static std::shared_ptr<Surface> frame(VulkanVideo &v, std::shared_ptr<Surface> reuse, uint32_t width, uint32_t height,
                                        const std::vector<uint8_t> &luma, const std::vector<uint8_t> &chroma,
                                        const ConversionPushConstants &conversion, uint32_t outputWidth,
                                        uint32_t outputHeight) {
    const VkDevice dev = v.vk_->act_dev;
    std::shared_ptr<Surface> s = reuse;
    if (!s) {
      std::lock_guard<std::mutex> guard(v.surfacesMutex_);
      s = v.allocatePlanar(width, height, false);
    }
    const uint32_t cw = (width + 1) / 2, ch = (height + 1) / 2;
    const VkDeviceSize chromaOffset = (VkDeviceSize(width) * height + 3) & ~VkDeviceSize(3);
    VkBuffer staging = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void *mapped = nullptr;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = chromaOffset + VkDeviceSize(cw) * ch * 2;
    bi.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    vkcheck(vkCreateBuffer(dev, &bi, nullptr, &staging), "Test staging buffer");
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(dev, staging, &requirements);
    VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    a.allocationSize = requirements.size;
    a.memoryTypeIndex = v.memoryType(requirements.memoryTypeBits,
                                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    vkcheck(vkAllocateMemory(dev, &a, nullptr, &memory), "Test staging memory");
    vkcheck(vkBindBufferMemory(dev, staging, memory, 0), "Bind test staging");
    vkcheck(vkMapMemory(dev, memory, 0, VK_WHOLE_SIZE, 0, &mapped), "Map test staging");
    std::memcpy(mapped, luma.data(), luma.size());
    std::memcpy(static_cast<uint8_t *>(mapped) + chromaOffset, chroma.data(), chroma.size());
    vkcheck(vkResetCommandBuffer(s->cmd, 0), "Reset test copy");
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vkcheck(vkBeginCommandBuffer(s->cmd, &begin), "Begin test copy");
    v.beginCopyTiming(*s, s->cmd);
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
      vkCmdPipelineBarrier(s->cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0,
                           nullptr, 0, nullptr, 1, &b);
    };
    for (int i = 0; i < 2; ++i) {
      barrier(s->planeImages[i], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0,
              VK_ACCESS_TRANSFER_WRITE_BIT);
      VkBufferImageCopy region{};
      region.bufferOffset = i ? chromaOffset : 0;
      region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
      region.imageExtent = {i ? cw : width, i ? ch : height, 1};
      vkCmdCopyBufferToImage(s->cmd, staging, s->planeImages[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
      barrier(s->planeImages[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
              VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT);
    }
    v.endCopyTiming(*s, s->cmd);
    vkcheck(vkEndCommandBuffer(s->cmd), "End test copy");
    VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1;
    submit.pCommandBuffers = &s->cmd;
    vkcheck(vkResetFences(dev, 1, &s->fence), "Reset test copy fence");
    vkcheck(v.presentation_->submit([&] { return vkQueueSubmit(v.queue, 1, &submit, s->fence); }), "Queue test copy");
    vkcheck(vkWaitForFences(dev, 1, &s->fence, VK_TRUE, UINT64_MAX), "Wait for test copy");
    vkUnmapMemory(dev, memory);
    vkDestroyBuffer(dev, staging, nullptr);
    vkFreeMemory(dev, memory, nullptr);
    s->planeLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    s->conversion = conversion;
    s->outputWidth = outputWidth;
    s->outputHeight = outputHeight;
    s->uploaded = true;
    return s;
  }
};
} // namespace vrost

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                                              \
  do {                                                                                                                \
    if (!(cond)) {                                                                                                    \
      ++failures;                                                                                                     \
      std::printf("FAIL line %d: %s ", __LINE__, #cond);                                                              \
      std::printf(__VA_ARGS__);                                                                                       \
      std::printf("\n");                                                                                              \
    }                                                                                                                 \
  } while (0)

struct StandInSystem : vr::stand_in::System {
  // SteamVR's GPU: the first Vulkan device of the instance (the client opens FFmpeg's device by the same index).
  void GetOutputDevice(uint64_t *device, vr::ETextureType type, VkInstance_T *instance) override {
    *device = 0;
    if (type != vr::TextureType_Vulkan || !instance) return;
    uint32_t count = 0;
    vkEnumeratePhysicalDevices(instance, &count, nullptr);
    std::vector<VkPhysicalDevice> devices(count);
    vkEnumeratePhysicalDevices(instance, &count, devices.data());
    if (count) *device = reinterpret_cast<uint64_t>(devices[0]);
  }
};

// SteamVR's compositor as the client uses it: explicit timing, one image submitted for both eyes (each its half),
// the handoff. The image of each left-eye Submit is read back, after the client's pass already on the queue.
struct StandInCompositor : vr::stand_in::Compositor {
  int timing = 0, handoffs = 0, submits = 0, malformed = 0;
  uint32_t width = 0, height = 0;
  std::vector<uint8_t> pixels; // RGBA
  VkDevice device = VK_NULL_HANDLE;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  VkBuffer buffer = VK_NULL_HANDLE;
  VkDeviceMemory memory = VK_NULL_HANDLE;
  void *mapped = nullptr;
  VkDeviceSize capacity = 0;

  void SetExplicitTimingMode(vr::EVRCompositorTimingMode) override {}
  vr::EVRCompositorError SubmitExplicitTimingData() override {
    ++timing;
    return vr::VRCompositorError_None;
  }
  void PostPresentHandoff() override { ++handoffs; }
  vr::EVRCompositorError Submit(vr::EVREye eye, const vr::Texture_t *texture, const vr::VRTextureBounds_t *bounds,
                                vr::EVRSubmitFlags flags) override {
    ++submits;
    const float u0 = eye == vr::Eye_Left ? 0.f : .5f;
    if (!texture || texture->eType != vr::TextureType_Vulkan || !(flags & vr::Submit_TextureWithPose) || !bounds ||
        bounds->uMin != u0 || bounds->uMax != u0 + .5f || bounds->vMin != 0.f || bounds->vMax != 1.f) {
      ++malformed;
      return vr::VRCompositorError_InvalidTexture;
    }
    if (eye == vr::Eye_Left) read(*static_cast<const vr::VRVulkanTextureData_t *>(texture->handle));
    return vr::VRCompositorError_None;
  }
  void read(const vr::VRVulkanTextureData_t &d) {
    device = d.m_pDevice;
    if (!pool) {
      VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
      pc.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
      pc.queueFamilyIndex = d.m_nQueueFamilyIndex;
      vrost::vkcheck(vkCreateCommandPool(device, &pc, nullptr, &pool), "Stand-in command pool");
      VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
      ca.commandPool = pool;
      ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
      ca.commandBufferCount = 1;
      vrost::vkcheck(vkAllocateCommandBuffers(device, &ca, &cmd), "Stand-in commands");
      VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
      vrost::vkcheck(vkCreateFence(device, &fc, nullptr, &fence), "Stand-in fence");
    }
    const VkDeviceSize bytes = VkDeviceSize(d.m_nWidth) * d.m_nHeight * 4;
    if (bytes > capacity) {
      freeBuffer();
      VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
      bi.size = bytes;
      bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
      vrost::vkcheck(vkCreateBuffer(device, &bi, nullptr, &buffer), "Stand-in buffer");
      VkMemoryRequirements requirements;
      vkGetBufferMemoryRequirements(device, buffer, &requirements);
      VkPhysicalDeviceMemoryProperties properties;
      vkGetPhysicalDeviceMemoryProperties(d.m_pPhysicalDevice, &properties);
      const VkMemoryPropertyFlags wanted = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
      uint32_t type = 0;
      while (type < properties.memoryTypeCount && (!(requirements.memoryTypeBits & (1u << type)) ||
                                                   (properties.memoryTypes[type].propertyFlags & wanted) != wanted))
        ++type;
      VkMemoryAllocateInfo a{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
      a.allocationSize = requirements.size;
      a.memoryTypeIndex = type;
      vrost::vkcheck(vkAllocateMemory(device, &a, nullptr, &memory), "Stand-in memory");
      vrost::vkcheck(vkBindBufferMemory(device, buffer, memory, 0), "Bind stand-in buffer");
      vrost::vkcheck(vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped), "Map stand-in buffer");
      capacity = bytes;
    }
    vrost::vkcheck(vkResetCommandBuffer(cmd, 0), "Reset stand-in commands");
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vrost::vkcheck(vkBeginCommandBuffer(cmd, &begin), "Begin stand-in read");
    // The client hands SteamVR the image in TRANSFER_SRC_OPTIMAL, its writes made visible to transfers.
    VkBufferImageCopy region{};
    region.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    region.imageExtent = {d.m_nWidth, d.m_nHeight, 1};
    vkCmdCopyImageToBuffer(cmd, reinterpret_cast<VkImage>(uintptr_t(d.m_nImage)), VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                           buffer, 1, &region);
    VkBufferMemoryBarrier host{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
    host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    host.srcQueueFamilyIndex = host.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    host.buffer = buffer;
    host.size = VK_WHOLE_SIZE;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr, 1, &host, 0,
                         nullptr);
    vrost::vkcheck(vkEndCommandBuffer(cmd), "End stand-in read");
    VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1;
    submit.pCommandBuffers = &cmd;
    vrost::vkcheck(vkResetFences(device, 1, &fence), "Reset stand-in fence");
    vrost::vkcheck(vkQueueSubmit(d.m_pQueue, 1, &submit, fence), "Stand-in read");
    vrost::vkcheck(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX), "Wait for stand-in read");
    width = d.m_nWidth;
    height = d.m_nHeight;
    pixels.assign(static_cast<const uint8_t *>(mapped), static_cast<const uint8_t *>(mapped) + bytes);
  }
  void freeBuffer() {
    if (mapped) vkUnmapMemory(device, memory);
    if (buffer) vkDestroyBuffer(device, buffer, nullptr);
    if (memory) vkFreeMemory(device, memory, nullptr);
    mapped = nullptr;
    buffer = VK_NULL_HANDLE;
    memory = VK_NULL_HANDLE;
    capacity = 0;
  }
  // Before the client's device goes (VulkanVideo's destructor).
  void release() {
    if (!device) return;
    vkDeviceWaitIdle(device);
    freeBuffer();
    if (fence) vkDestroyFence(device, fence, nullptr);
    if (pool) vkDestroyCommandPool(device, pool, nullptr);
    fence = VK_NULL_HANDLE;
    pool = VK_NULL_HANDLE;
    device = VK_NULL_HANDLE;
  }
  const uint8_t *at(uint32_t x, uint32_t y) const { return pixels.data() + (size_t(y) * width + x) * 4; }
};

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

// ALVR's foveated encoding as the host sets it up (as in tests/fused_shader_tests.cpp).
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

vrost::ConversionPushConstants conversion(const vrost::FoveationParams &f = {}) {
  // Full-range BT.709, chroma sited left, no dither: each output value is exact to the rounding.
  return vrost::conversionPushConstants(vrost::conversionMatrix(.2126f, .0722f), vrost::rangeConstants(8, true, false),
                                        vrost::chromaSiting(1), 0.f, f);
}

// The colour a flat Y'CbCr frame must arrive as (full range, BT.709).
std::array<double, 3> colour(int y, int cb, int cr) {
  const auto m = vrost::conversionMatrix(.2126f, .0722f);
  const double v[3] = {y / 255.0, (cb - 128) / 255.0, (cr - 128) / 255.0};
  std::array<double, 3> out{};
  for (int i = 0; i < 3; ++i)
    out[i] = std::clamp(m.row[i][0] * v[0] + m.row[i][1] * v[1] + m.row[i][2] * v[2], 0.0, 1.0) * 255.0;
  return out;
}

struct Planes {
  uint32_t width = 0, height = 0;
  std::vector<uint8_t> luma, chroma;
};
Planes flat(uint32_t width, uint32_t height, uint8_t y, uint8_t cb, uint8_t cr) {
  Planes p{width, height};
  p.luma.assign(size_t(width) * height, y);
  const size_t cw = (width + 1) / 2, ch = (height + 1) / 2;
  p.chroma.resize(cw * ch * 2);
  for (size_t i = 0; i < cw * ch; ++i) {
    p.chroma[i * 2] = cb;
    p.chroma[i * 2 + 1] = cr;
  }
  return p;
}
// Neutral chroma and luma from f(eye, x within the eye, y); both eyes side by side.
template <class F> Planes pattern(uint32_t eyeWidth, uint32_t height, F f) {
  Planes p = flat(eyeWidth * 2, height, 0, 128, 128);
  for (uint32_t y = 0; y < height; ++y)
    for (uint32_t x = 0; x < eyeWidth * 2; ++x)
      p.luma[size_t(y) * eyeWidth * 2 + x] = uint8_t(f(int(x / eyeWidth), int(x % eyeWidth), int(y)));
  return p;
}

// A decoded frame on its way to the frame pass, as this platform's decode-time step takes it. The surface of an
// earlier frame is given back (`reuse`) so its copy's GPU time is collected when the surface is used again.
std::shared_ptr<vrost::Surface> decoded(vrost::VulkanVideo &video, std::shared_ptr<vrost::Surface> reuse,
                                        const Planes &p, const vrost::FoveationParams &f, uint32_t outputWidth,
                                        uint32_t outputHeight) {
#ifdef _WIN32
  // The client's own upload (a CPU-decoded yuv420p frame): it takes a planar surface nothing else holds.
  reuse.reset();
  AVFrame *frame = av_frame_alloc();
  frame->format = AV_PIX_FMT_YUV420P;
  frame->width = int(p.width);
  frame->height = int(p.height);
  frame->color_range = AVCOL_RANGE_JPEG;
  frame->colorspace = AVCOL_SPC_BT709;
  frame->chroma_location = AVCHROMA_LOC_LEFT;
  vrost::avcheck(av_frame_get_buffer(frame, 0), "Test frame");
  const uint32_t cw = (p.width + 1) / 2, ch = (p.height + 1) / 2;
  for (uint32_t y = 0; y < p.height; ++y)
    std::memcpy(frame->data[0] + size_t(y) * frame->linesize[0], p.luma.data() + size_t(y) * p.width, p.width);
  for (uint32_t y = 0; y < ch; ++y)
    for (uint32_t x = 0; x < cw; ++x) {
      frame->data[1][size_t(y) * frame->linesize[1] + x] = p.chroma[(size_t(y) * cw + x) * 2];
      frame->data[2][size_t(y) * frame->linesize[2] + x] = p.chroma[(size_t(y) * cw + x) * 2 + 1];
    }
  vrost::ConversionTarget target;
  target.width = outputWidth;
  target.height = outputHeight;
  target.foveation = f;
  target.dither = 0.f;
  for (int eye = 0; eye < 2; ++eye)
    for (int axis = 0; axis < 2; ++axis) target.defaultShifts[eye][axis] = f.shifts[eye][axis];
  auto s = video.convert(frame, 0, vrost::Pose{}, false, {}, &target);
  av_frame_free(&frame);
  if (s) video.waitReady(s, UINT64_MAX);
  return s;
#else
  return vrost::VideoTestAccess::frame(video, reuse, p.width, p.height, p.luma, p.chroma, conversion(f), outputWidth,
                                       outputHeight);
#endif
}

struct Session {
  vrost::VulkanVideo &video;
  vrost::OpenVRDevice &runtime;
  StandInCompositor &compositor;
  int frames = 0;
  // One refresh as the client's render loop does it: the frame shown (re-aimed to the same pose), the handoff.
  int show(const std::shared_ptr<vrost::Surface> &s, const vrost::Pose &source = {}, const vrost::Pose &target = {}) {
    video.beginFrame();
    const int shown = video.submitReaimed(runtime, s, source, target, eyes());
    video.finishFrame();
    vrost::vkcheck(vkDeviceWaitIdle(vrost::VideoTestAccess::device(video)), "Wait for the refresh");
    ++frames;
    return shown;
  }
};

bool black(const uint8_t *p) { return p[0] == 0 && p[1] == 0 && p[2] == 0 && p[3] == 255; }

// Whether output pixel x of row y (stereo image, eye width w) lies inside its eye's visible span.
bool inside(const std::vector<uint32_t> spans[2], uint32_t w, uint32_t x, uint32_t y) {
  const uint32_t eye = x / w, local = x % w, span = spans[eye][y];
  return local >= vrost::spanFirst(span) && local < vrost::spanEnd(span);
}

vrost::VisibleArea squareArea(float lo, float hi) {
  vrost::VisibleArea area;
  for (int eye = 0; eye < 2; ++eye) {
    area.triangles[eye].push_back({lo, lo, hi, lo, hi, hi});
    area.triangles[eye].push_back({lo, lo, hi, hi, lo, hi});
  }
  return area;
}
} // namespace

int main(int argc, char **argv) {
  if (argc < 2) {
    std::printf("usage: video_pass_tests <directory with convert.spv, reaim.spv and fused.spv>\n");
    return 2;
  }
  {
    VkInstance instance = VK_NULL_HANDLE;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    uint32_t count = 0;
    if (vkCreateInstance(&ici, nullptr, &instance) == VK_SUCCESS) {
      vkEnumeratePhysicalDevices(instance, &count, nullptr);
      vkDestroyInstance(instance, nullptr);
    }
    if (!count) {
      std::printf("No Vulkan device: video pass tests skipped\n");
      return 77;
    }
  }
  const std::filesystem::path shaders = argv[1];
  StandInSystem system;
  StandInCompositor compositor;
  vrost::OpenVRDevice runtime;
  runtime.system = &system;
  runtime.compositor = &compositor;
  try {
    vrost::VulkanVideo video;
    // The stand-in's own Vulkan objects go before the client's device does, also when a check throws.
    struct Release {
      StandInCompositor &compositor;
      ~Release() { compositor.release(); }
    } release{compositor};
    video.sharpening = 0.f;
    video.open(runtime, shaders / "convert.spv");
    std::printf("Vulkan device: %s\n", video.gpu.c_str());
    Session session{video, runtime, compositor};
    CHECK(video.fusedAvailable(), "the frame pass opened");
    CHECK(video.reaimPassAvailable(), "the re-aim pass opened");
    const bool timestamps = video.gpuTimestamps();
    std::printf("GPU time stamps: %s\n", timestamps ? "on" : "unavailable on this device");

    // 1. A flat colour, 2 x 160 x 120 decoded and shown at the same size.
    const uint32_t eyeW = 160, eyeH = 120, W = eyeW * 2;
    auto planes = flat(W, eyeH, 150, 100, 180);
    auto first = decoded(video, nullptr, planes, {}, W, eyeH);
    CHECK(first && first->planar, "a planar surface for the frame pass");
    CHECK(session.show(first) == 1, "flat frame shown");
    CHECK(compositor.width == W && compositor.height == eyeH, "SteamVR is given %u x %u", compositor.width,
          compositor.height);
    const auto expected = colour(150, 100, 180);
    double worst = 0;
    for (uint32_t y = 0; y < compositor.height; ++y)
      for (uint32_t x = 0; x < compositor.width; ++x)
        for (int c = 0; c < 3; ++c) worst = std::max(worst, std::fabs(compositor.at(x, y)[c] - expected[c]));
    CHECK(worst <= 1.0, "flat colour off by %.2f levels", worst);
    std::printf("flat colour                     worst %.2f levels (expected %.1f %.1f %.1f)\n", worst, expected[0],
                expected[1], expected[2]);

    // 2. A pattern that must arrive pixel for pixel in both eyes (Catmull-Rom at texel centres): rows, columns
    // and the eyes in their places. The first frame's surface again: its earlier copy's GPU time is collected.
    auto pixelPattern = [](int eye, int x, int y) { return 20 + (x * 7 + y * 13 + eye * 50) % 200; };
    planes = pattern(eyeW, eyeH, pixelPattern);
    auto patterned = decoded(video, std::move(first), planes, {}, W, eyeH);
    CHECK(session.show(patterned) == 1, "pattern shown");
    int wrong = 0;
    for (uint32_t y = 0; y < eyeH; ++y)
      for (uint32_t x = 0; x < W; ++x) {
        const int want = pixelPattern(int(x / eyeW), int(x % eyeW), int(y));
        const uint8_t *p = compositor.at(x, y);
        if (std::abs(p[0] - want) > 1 || std::abs(p[1] - want) > 1 || std::abs(p[2] - want) > 1 || p[3] != 255) ++wrong;
      }
    CHECK(wrong == 0, "%d pixels differ from the decoded picture", wrong);
    std::printf("pattern, both eyes              %d of %u pixels differ\n", wrong, W * eyeH);
    const std::vector<uint8_t> full = compositor.pixels;

    // 3. The lens area: a square in each eye (the visible-area mesh). Pixels outside the spans black, the rest as
    // before. The output image is the one shown before (same size): it takes the new spans and a clear.
    video.setVisibleArea(squareArea(.15f, .85f), true);
    std::vector<uint32_t> spans[2];
    const auto area = squareArea(.15f, .85f);
    for (int eye = 0; eye < 2; ++eye)
      spans[eye] = vrost::visibleSpans(area, eye, eyeW, eyeH, vrost::visibleMargin(eyeW, eyeH));
    CHECK(session.show(patterned) == 1, "pattern shown inside the lens area");
    int outsideNotBlack = 0, insideChanged = 0, outside = 0;
    for (uint32_t y = 0; y < eyeH; ++y)
      for (uint32_t x = 0; x < W; ++x) {
        const uint8_t *p = compositor.at(x, y);
        if (inside(spans, eyeW, x, y)) {
          if (std::memcmp(p, full.data() + (size_t(y) * W + x) * 4, 4) != 0) ++insideChanged;
        } else {
          ++outside;
          if (!black(p)) ++outsideNotBlack;
        }
      }
    CHECK(outside > 0 && outsideNotBlack == 0, "%d of %d pixels outside the spans not black", outsideNotBlack, outside);
    CHECK(insideChanged == 0, "%d pixels inside the spans changed", insideChanged);
    const double share = video.visibleShare();
    CHECK(share > .5 && share < .95 && std::fabs(share - (1.0 - double(outside) / (W * eyeH))) < 1e-6,
          "visible share %.4f", share);
    std::printf("lens area                       %d pixels skipped (share %.3f), %d not black, %d changed\n", outside,
                share, outsideNotBlack, insideChanged);

    // 4. An output image the frame pass made black outside the spans, then filled by the two-pass path (the
    // recovery picture, re-aimed), then taken by the frame pass again: cleared again, so what the pass skips is
    // black, not the recovery picture.
    planes = flat(128, 64, 150, 100, 180);
    auto small = decoded(video, nullptr, planes, {}, 128, 64);
    CHECK(session.show(small) == 1, "frame shown at 2 x 64 x 64");
    auto recovery = video.neutral();
    CHECK(recovery && !recovery->planar, "recovery picture");
    if (recovery) video.waitReady(recovery, UINT64_MAX); // as the render loop waits for a frame's conversion
    CHECK(session.show(recovery) == 1, "recovery picture shown through the re-aim pass");
    CHECK(compositor.width == 128 && compositor.height == 64 && compositor.at(0, 0)[0] == 4 &&
              compositor.at(127, 63)[2] == 5,
          "re-aim output %u x %u", compositor.width, compositor.height);
    CHECK(session.show(small) == 1, "frame shown after the re-aim pass");
    for (int eye = 0; eye < 2; ++eye)
      spans[eye] = vrost::visibleSpans(area, eye, 64, 64, vrost::visibleMargin(64, 64));
    int stale = 0;
    outside = 0;
    for (uint32_t y = 0; y < 64; ++y)
      for (uint32_t x = 0; x < 128; ++x)
        if (!inside(spans, 64, x, y)) {
          ++outside;
          if (!black(compositor.at(x, y))) ++stale;
        }
    CHECK(outside > 0 && stale == 0, "%d of %d skipped pixels kept the earlier picture", stale, outside);
    std::printf("after the re-aim pass           %d of %d skipped pixels not black\n", stale, outside);

    // 5. Shown smaller (90 %, the companion's first step): the same decoded size, a smaller output image with its own
    // spans; the picture inside them is the flat colour.
    const auto scaled = vrost::scaledEyeSize({eyeW, eyeH}, 90);
    planes = flat(W, eyeH, 150, 100, 180);
    auto smaller = decoded(video, nullptr, planes, {}, scaled.width * 2, scaled.height);
    CHECK(session.show(smaller) == 1, "smaller frame shown");
    CHECK(compositor.width == scaled.width * 2 && compositor.height == scaled.height, "SteamVR is given %u x %u",
          compositor.width, compositor.height);
    for (int eye = 0; eye < 2; ++eye)
      spans[eye] = vrost::visibleSpans(area, eye, scaled.width, scaled.height,
                                       vrost::visibleMargin(scaled.width, scaled.height));
    int smallWrong = 0;
    outside = 0;
    worst = 0;
    for (uint32_t y = 0; y < scaled.height; ++y)
      for (uint32_t x = 0; x < scaled.width * 2; ++x) {
        const uint8_t *p = compositor.at(x, y);
        if (!inside(spans, scaled.width, x, y)) {
          ++outside;
          if (!black(p)) ++smallWrong;
        } else {
          for (int c = 0; c < 3; ++c) worst = std::max(worst, std::fabs(p[c] - expected[c]));
        }
      }
    CHECK(outside > 0 && smallWrong == 0 && worst <= 1.0, "smaller: %d skipped not black, colour off by %.2f",
          smallWrong, worst);
    std::printf("shown at 90 %% (%u x %u)        %d skipped, colour within %.2f levels\n", scaled.width, scaled.height,
                outside, worst);

    // 6. A description that would leave under half an eye (a sliver) is ignored: every pixel computed.
    vrost::VisibleArea sliver = squareArea(.49f, .51f);
    video.setVisibleArea(sliver, true);
    CHECK(session.show(smaller) == 1, "frame shown with a faulty lens area");
    int blackPixels = 0;
    for (uint32_t y = 0; y < compositor.height; ++y)
      for (uint32_t x = 0; x < compositor.width; ++x)
        if (black(compositor.at(x, y))) ++blackPixels;
    CHECK(blackPixels == 0 && video.visibleShare() == 1.0, "faulty description: %d black pixels, share %.3f",
          blackPixels, video.visibleShare());
    std::printf("faulty lens area ignored        %d black pixels\n", blackPixels);
    video.setVisibleArea({}, true); // none: every pixel

    // 7. Foveated encoding: a ramp in both axes of each encoded eye arrives where ALVR's inverse puts each output
    // pixel (Catmull-Rom and the B-spline both reproduce a linear ramp), with sharpening on (it leaves a ramp alone).
    const uint32_t viewW = 96, viewH = 80;
    const auto f = foveation(viewW, viewH, .5f, 3.f, .25f, -.2f, -.3f, .15f);
    const uint32_t encW = f.encoded[0], encH = f.encoded[1];
    auto ramp = [](int, int x, int y) { return 20 + x + y; };
    planes = pattern(encW, encH, ramp);
    video.sharpening = .7f;
    auto foveated = decoded(video, nullptr, planes, f, viewW * 2, viewH);
    CHECK(session.show(foveated) == 1, "foveated frame shown");
    CHECK(compositor.width == viewW * 2 && compositor.height == viewH, "SteamVR is given %u x %u", compositor.width,
          compositor.height);
    int checked = 0, off = 0;
    double rampWorst = 0;
    for (uint32_t y = 0; y < viewH; ++y)
      for (uint32_t x = 0; x < viewW * 2; ++x) {
        const int eye = int(x / viewW);
        float su = 0, sv = 0;
        vrost::foveatedSource(eye, (float(x % viewW) + .5f) / float(viewW), (float(y) + .5f) / float(viewH), f, su, sv);
        const double tx = double(su) * encW, ty = double(sv) * encH; // texels within the encoded eye
        // Away from the eye's edges, where the taps are held inside the eye.
        if (tx < 3 || ty < 3 || tx > f.viewRatio[0] * encW - 3 || ty > f.viewRatio[1] * encH - 3) continue;
        const double want = 20 + (tx - .5) + (ty - .5);
        const double error = std::fabs(compositor.at(x, y)[0] - want);
        rampWorst = std::max(rampWorst, error);
        ++checked;
        if (error > 1.0) ++off;
      }
    CHECK(checked > int(viewW * viewH) / 2 && off == 0, "foveated ramp: %d of %d pixels off by more than a level", off,
          checked);
    std::printf("foveated ramp (%u x %u from %u x %u) %d pixels checked, worst %.2f levels\n", viewW, viewH, encW, encH,
                checked, rampWorst);

    // The runtime saw what SteamVR expects: explicit timing once per refresh, both eyes, the handoff.
    CHECK(compositor.malformed == 0 && compositor.submits == 2 * session.frames &&
              compositor.handoffs == session.frames && compositor.timing >= session.frames,
          "submits %d, handoffs %d, timing %d for %d refreshes", compositor.submits, compositor.handoffs,
          compositor.timing, session.frames);

    // 8. GPU time: the pass's from the second use of an output image, the copy's from the second copy into a surface.
    const auto pass = video.framePassGpu();
    const auto copy = video.planeCopyGpu();
    if (timestamps) {
      CHECK(pass.samples >= 1 && pass.meanMs > 0 && pass.maxMs < 10000, "frame pass GPU time: %zu samples, %.3f ms",
            pass.samples, pass.meanMs);
      CHECK(copy.samples >= 1 && copy.meanMs >= 0, "plane copy GPU time: %zu samples", copy.samples);
    } else {
      CHECK(pass.samples == 0 && copy.samples == 0, "no time stamps, no samples");
    }
    std::printf("GPU time                        frame pass %zu samples, mean %.3f ms; plane copy %zu samples, mean %.3f ms\n",
                pass.samples, pass.meanMs, copy.samples, copy.meanMs);
  } catch (const std::exception &error) {
    std::printf("FAIL: %s\n", error.what());
    return 1;
  }
  if (failures) {
    std::printf("Video pass tests: %d failures\n", failures);
    return 1;
  }
  std::printf("Video pass tests passed\n");
  return 0;
}
