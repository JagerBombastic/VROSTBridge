#pragma once
#include "client/device/conversion.hpp"
#include "client/device/openvr_device.hpp"
#include "core/presentation.hpp"
#include "core/reaim.hpp"
#include <vulkan/vulkan.h>
#ifdef _WIN32
#include "client/platform/windows/d3d11_video.hpp"
#endif
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>
#include <libswscale/swscale.h>
}
#include <map>
#include <memory>
namespace vrost {
inline void vkcheck(VkResult v, const char *where) {
  if (v != VK_SUCCESS)
    throw std::runtime_error(std::string(where) + ": Vulkan error " +
                             std::to_string(v));
}
inline void avcheck(int v, const char *where) {
  if (v < 0) {
    char msg[256];
    av_strerror(v, msg, sizeof(msg));
    throw std::runtime_error(std::string(where) + ": " + msg);
  }
}
struct Surface {
  VkImage image = VK_NULL_HANDLE;
  VkDeviceMemory memory = VK_NULL_HANDLE;
  VkImageView view = VK_NULL_HANDLE;
  VkBuffer staging = VK_NULL_HANDLE;
  VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
  void *mapped = nullptr;
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE, retired = VK_NULL_HANDLE;
  VkDescriptorSet descriptor = VK_NULL_HANDLE;
  VkImageView planes[2]{};
  AVFrame *imported = nullptr;
  VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
  uint32_t width = 0, height = 0;
  uint64_t timestamp = 0, readyAt = 0;
  // Local tracking acquisition time behind `timestamp`. With client prediction
  // the timestamp is a future target; ages and hold windows use this instead.
  uint64_t sampledAt = 0;
  uint64_t receivedAt = 0, decodeStartAt = 0, decodeOutputAt = 0;
  uint64_t conversionQueuedAt = 0, firstReadyObservedAt = 0;
  uint64_t mapWaitNs = 0, conversionCpuNs = 0;
  Pose source{};
  bool uploaded = false;
  bool tenBit = false;      // decoded from a 10-bit (P010) surface
  bool gazeCentred = false; // foveation centres differed from the negotiated defaults
  // RC4.3 fused path: the decoded planes are copied into images of our own (at the decoded,
  // possibly foveated size) and converted, expanded, re-aimed and sharpened in one pass when
  // the frame is shown (fused.comp). Such a surface has no RGBA image (`image` stays null):
  // width/height are the decoded size and outputWidth/outputHeight what the pass writes.
  bool planar = false;
  VkImage planeImages[2]{};
  VkDeviceMemory planeMemory[2]{};
  VkImageView planeViews[2]{};
  VkImageLayout planeLayout = VK_IMAGE_LAYOUT_UNDEFINED;
  uint32_t outputWidth = 0, outputHeight = 0;
  ConversionPushConstants conversion{}; // this frame's colour, siting and foveation centres
  // 1.0.0.7 (Windows): the decoded planes reach planeImages through this persistently mapped,
  // host-visible buffer (client/device/planar_upload.hpp). There a two-pass (RGBA) surface has
  // plane images of its own too: planesWidth x planesHeight, the decoded size.
  VkBuffer planeStaging = VK_NULL_HANDLE;
  VkDeviceMemory planeStagingMemory = VK_NULL_HANDLE;
  void *planeMapped = nullptr;
  VkDeviceSize planeStagingSize = 0;
  bool planeStagingCoherent = true;
  uint32_t planesWidth = 0, planesHeight = 0;
  bool planesTenBit = false;
  // 1.0.0.11: GPU time stamps of the plane copy (a pair in VulkanVideo's copy query pool; -1: none).
  int copySlot = -1;
  bool copyTimed = false;
};
// What the conversion produces: output stereo size (the negotiated view resolution, which
// differs from the decoded size when the stream is foveated) and the foveation in use.
struct ConversionTarget {
  uint32_t width = 0, height = 0; // stereo output (2 x eye width) x eye height
  FoveationParams foveation;      // shifts: the latest centres received with a frame
  float defaultShifts[2][2]{};    // negotiated (fixed) centres
  float dither = 1.f;             // amplitude in 8-bit steps; 0 disables
};
// Per-frame metadata delivered when hardware decoding has completed.
struct FrameMetadata {
  Pose source{};
  float shifts[4]{};
  bool hasShifts = false;
};
// An image the re-aim pass writes and SteamVR then reads (RC4.2).
struct ReaimTarget {
  VkImage image = VK_NULL_HANDLE;
  VkDeviceMemory memory = VK_NULL_HANDLE;
  VkImageView view = VK_NULL_HANDLE;
  VkDescriptorSet descriptor = VK_NULL_HANDLE;
  VkDescriptorSet fusedDescriptor = VK_NULL_HANDLE; // RC4.3: planes in, this image out
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  VkFence retired = VK_NULL_HANDLE; // signalled after SteamVR's use of the image
  VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
  uint32_t width = 0, height = 0;
  // 1.0.0.11: the fused pass's constants (FusedFrame, client/device/conversion.hpp) in a host-visible, coherent
  // uniform buffer written for each frame while the target is free (its retirement fence signalled), and the
  // visible span of each output row and eye (client/device/visible_area.hpp), written when the image is made.
  VkBuffer frame = VK_NULL_HANDLE;
  VkDeviceMemory frameMemory = VK_NULL_HANDLE;
  void *frameMapped = nullptr;
  VkBuffer spans = VK_NULL_HANDLE;
  VkDeviceMemory spansMemory = VK_NULL_HANDLE;
  void *spansMapped = nullptr;
  double visibleShare = 1;  // of this image's pixels the fused pass computes
  uint32_t spansGeneration = 0;
  bool needsClear = true;   // black outside the spans only after a clear (a new image, or one the re-aim pass filled)
  bool timed = false;       // its last frame pass carries GPU time stamps
};
class VulkanVideo {
  // 1.0.0.11: tests/video_pass_tests.cpp fills a planar surface as the decode-time copy does (no hardware decoder there).
  friend struct VideoTestAccess;
  AVBufferRef *deviceRef_ = nullptr;
  AVBufferRef *mapFrames_ = nullptr;
  AVHWDeviceContext *hw_ = nullptr;
  AVVulkanDeviceContext *vk_ = nullptr;
  VkCommandPool pool_ = VK_NULL_HANDLE;
  VkDescriptorPool descriptors_ = VK_NULL_HANDLE;
  VkDescriptorSetLayout descriptorLayout_ = VK_NULL_HANDLE;
  VkPipelineLayout pipelineLayout_ = VK_NULL_HANDLE;
  VkPipeline pipeline_ = VK_NULL_HANDLE;
  VkSampler sampler_ = VK_NULL_HANDLE;
  std::vector<std::shared_ptr<Surface>> surfaces_;
  // The descriptor pool has no FREE bit: recycle sets from destroyed surfaces.
  std::vector<VkDescriptorSet> spareDescriptors_;
  std::mutex surfacesMutex_;
  void destroySurface(Surface &);
  void pruneOtherSizes(uint32_t width, uint32_t height, bool planar, bool tenBit);
  std::unique_ptr<PresentationQueue> presentation_;
  std::shared_ptr<Surface> submittedSurface_;
  // Re-aim pass (optional: unavailable when its shader is missing).
  VkDescriptorSetLayout reaimSetLayout_ = VK_NULL_HANDLE;
  VkPipelineLayout reaimLayout_ = VK_NULL_HANDLE;
  VkPipeline reaimPipeline_ = VK_NULL_HANDLE;
  VkDescriptorPool reaimDescriptors_ = VK_NULL_HANDLE;
  // Its own command pool: re-aim records on the render thread while conversion records
  // from pool_ on the decoder threads, and a pool must not be used by two threads at once.
  VkCommandPool reaimPool_ = VK_NULL_HANDLE;
  std::array<ReaimTarget, 4> reaimTargets_{};
  ReaimTarget *submittedTarget_ = nullptr;
  void openReaim(const std::filesystem::path &shader);
  bool createReaimTarget(ReaimTarget &, uint32_t width, uint32_t height);
  void destroyReaimTarget(ReaimTarget &);
  ReaimTarget *freeTarget(uint32_t width, uint32_t height);
  // RC4.3 fused pass (optional: needs fused.spv and the re-aim targets). 1.0.0.11: its constants in a uniform
  // buffer per target and the visible spans in a storage buffer per target, so any GPU runs the one shader.
  VkPipelineLayout fusedLayout_ = VK_NULL_HANDLE;
  VkPipeline fusedPipeline_ = VK_NULL_HANDLE;
  VkDescriptorSetLayout fusedSetLayout_ = VK_NULL_HANDLE;
  bool fusedSets_ = false; // every output image has its fused descriptor set and buffers
  void openFused(const std::filesystem::path &shader);
  void writeSpans(ReaimTarget &);
  // 1.0.0.11: what the lenses show, and the spans per output size (both eyes, row by row) with their share.
  VisibleArea visible_;
  bool skipHidden_ = false;
  struct Spans {
    std::vector<uint32_t> rows;
    double share = 1;
  };
  std::map<uint64_t, Spans> spanCache_;
  const Spans &spansFor(uint32_t eyeWidth, uint32_t height);
  double shownShare_ = 1;
  uint32_t spansGeneration_ = 1;
  // 1.0.0.11: GPU time stamps: two per output image (the fused pass) and two per copy slot (the plane copy).
  VkQueryPool passQueries_ = VK_NULL_HANDLE, copyQueries_ = VK_NULL_HANDLE;
  double timestampNs_ = 0;  // nanoseconds per tick
  uint64_t timestampMask_ = 0;
  std::vector<int> freeCopySlots_;
  TimingWindow passGpu_;
  std::mutex copyGpuMutex_;
  TimingWindow copyGpu_;
  bool queryDuration(VkQueryPool pool, uint32_t first, uint64_t &ns) const;
  void openTimestamps(const VkQueueFamilyProperties &family, const VkPhysicalDeviceLimits &limits);
  void beginCopyTiming(Surface &, VkCommandBuffer);
  void endCopyTiming(Surface &, VkCommandBuffer);
  std::shared_ptr<Surface> allocatePlanar(uint32_t width, uint32_t height, bool tenBit);
  void createPlanes(Surface &, uint32_t width, uint32_t height, bool tenBit);
  void destroyPlanes(Surface &);
  AVVkFrame *importFrame(AVFrame *frame, Surface &s);
  ConversionPushConstants conversionFor(const AVFrame *frame, bool tenBit, const ConversionTarget *target,
                                        bool &gazeCentred) const;
  // 1.0.0.5: the stream's colour description last logged (depth, range, matrix, siting...).
  mutable std::atomic<int64_t> colourKey_{-1};
  std::shared_ptr<Surface> copyPlanes(AVFrame *, uint64_t, const Pose &,
                                      const std::function<bool(FrameMetadata &)> &onDecoded,
                                      const ConversionTarget &target, uint64_t started);
  int submitFused(OpenVRDevice &, const std::shared_ptr<Surface> &, const Pose &source, const Pose &target,
                  const std::array<VrostView, 2> &eyes);
  int submitTarget(OpenVRDevice &, ReaimTarget &, const std::shared_ptr<Surface> &, const Pose &label);
  VkShaderModule loadShader(const std::filesystem::path &path);
  uint32_t memoryType(uint32_t, VkMemoryPropertyFlags);
  void cleanupInput(Surface &);
  std::shared_ptr<Surface> allocate(uint32_t, uint32_t);
#ifdef _WIN32
  // 1.0.0.7: the Windows decode-time step (client/platform/windows/vulkan_upload.cpp): the decoded
  // frame (a D3D11VA download, or CPU-decoded) is written to the surface's staging buffer and copied
  // into its plane images, then shown by the fused pass, or converted at once (two passes).
  std::shared_ptr<Surface> uploadPlanes(AVFrame *, uint64_t, const Pose &,
                                        const std::function<bool(FrameMetadata &)> &onDecoded,
                                        const ConversionTarget &target, uint64_t started);
  void openWindows(); // SteamVR's GPU for D3D11VA (LUID), and what Vulkan Video could decode there
  uint8_t luid_[8]{};
#endif

public:
  VkQueue queue = VK_NULL_HANDLE;
  std::atomic<uint64_t> timingMarkerErrors{0};
  // Set before open(): false keeps RC4.2's two passes (--two-pass, or CPU decoding).
  bool allowFused = true;
  // Sharpening of the fused pass (unsharp amount on luma, 0 = off); applies to the next frame shown.
  std::atomic<float> sharpening{0.f};
  // Whether hardware-decoded frames take the fused path (one pass when shown).
  bool fusedAvailable() const { return fusedPipeline_ != VK_NULL_HANDLE && reaimPool_ != VK_NULL_HANDLE && fusedSets_; }
  // 1.0.0.11: skip the pixels the lenses never show in the fused pass (call before frames are shown; an empty
  // description, or enabled false, computes every pixel).
  void setVisibleArea(VisibleArea area, bool enabled);
  // The share of the last shown image's pixels the fused pass computed (1 when it computes all of them).
  double visibleShare() const { return shownShare_; }
  // 1.0.0.11: GPU time of the fused pass and of the decode-time plane copy, from GPU time stamps (no samples where
  // the GPU's queue has none). The pass is read on the render thread; the copy is collected on the decoder's.
  bool gpuTimestamps() const { return passQueries_ != VK_NULL_HANDLE; }
  TimingSummary framePassGpu() { return passGpu_.take(); }
  TimingSummary planeCopyGpu() {
    std::lock_guard<std::mutex> lock(copyGpuMutex_);
    return copyGpu_.take();
  }
  // The RC4.2 re-aim pass for RGBA frames (CPU decoding, two-pass fallback).
  bool reaimPassAvailable() const { return reaimPipeline_ != VK_NULL_HANDLE; }
  uint32_t family = 0;
  std::string renderNode, gpu;
#ifdef _WIN32
  // 1.0.0.7: set before open(): decode on the CPU instead of with D3D11VA (--decoder cpu).
  bool cpuDecode = false;
  windows::D3D11Adapter adapter; // SteamVR's GPU as DXGI sees it, and its D3D11 video decoder
  AVBufferRef *createDecodeDevice() const { return windows::createDecodeDevice(adapter, luid_); }
#endif
  void open(OpenVRDevice &, const std::filesystem::path &shader);
  AVBufferRef *deviceRef() { return deviceRef_; }
  std::shared_ptr<Surface> convert(AVFrame *, uint64_t, const Pose &,
                                   bool compatibility,
                                   const std::function<bool(FrameMetadata &)> &onDecoded = {},
                                   const ConversionTarget *target = nullptr);
  std::shared_ptr<Surface> neutral();
  bool ready(const std::shared_ptr<Surface> &s) {
    return s && vkGetFenceStatus(vk_->act_dev, s->fence) == VK_SUCCESS;
  }
  bool submit(OpenVRDevice &, const std::shared_ptr<Surface> &, const Pose &);
  // Re-aims the frame from `source` (the pose the host rendered for) to `target` (the
  // pose SteamVR will display with) and submits it labelled `target`.
  // Returns -1 when the pass is unavailable or busy and nothing was submitted (the caller
  // then submits the frame unchanged), 0 when it was submitted but SteamVR did not accept
  // it (no focus), and 1 when SteamVR accepted it.
  bool reaimAvailable() const { return reaimPipeline_ != VK_NULL_HANDLE || fusedAvailable(); }
  int submitReaimed(OpenVRDevice &, const std::shared_ptr<Surface> &, const Pose &source,
                    const Pose &target, const std::array<VrostView, 2> &eyes);
  void beginFrame() { presentation_->beginFrame(); }
  void finishFrame();
  void waitReady(const std::shared_ptr<Surface> &s, uint64_t budget) {
    auto result=vkWaitForFences(vk_->act_dev, 1, &s->fence, VK_TRUE, budget);
    if (result!=VK_TIMEOUT) vkcheck(result,"Wait for late conversion");
  }
  ~VulkanVideo();
};
class Decoder {
  void *transport_;
  VulkanVideo &gpu_;
  ViewGeometry &geometry_;
  bool compatibility_;
  PacketQueue queue_;
  std::thread thread_, conversionThread_;
  std::atomic<bool> stopping_{false};
  struct DecodedFrame {
    std::shared_ptr<AVFrame> frame;
    uint64_t timestamp = 0, received = 0, started = 0, output = 0;
  };
  DecodedMailbox<DecodedFrame> decodedFrames_;
  std::mutex latestMutex_, errorMutex_, statisticsMutex_;
  std::condition_variable frameAvailable_;
  ReadyFrames<std::shared_ptr<Surface>> pending_;
  TimingWindow queueTiming_, decodeTiming_, conversionTiming_, arrivalTiming_;
  TimingWindow mapTiming_, conversionCpuTiming_;
  TimingWindow decodedQueueTiming_, decodeCompleteTiming_;
  uint64_t previousArrival_ = 0;
  size_t queueHighWater_ = 0;
  std::string error_;
  uint32_t codec_ = 0, width_ = 0, height_ = 0;
  ConversionTarget target_;
  // 1.0.0.11: the size each eye is shown at before scaling (start()), and the scale the companion asks for when
  // this PC's graphics fall behind ('scale N'): the conversion worker applies it to target_ frame by frame.
  EyeSize baseShown_{};
  std::atomic<uint32_t> scalePercent_{100};
  void applyScale();
  std::vector<uint8_t> config_;
  void worker();
  void conversionWorker();
  void fail(const std::string &message) {
    { std::lock_guard<std::mutex> lock(errorMutex_); if (error_.empty()) error_ = message; }
    stopping_ = true;
    queue_.stop();
    decodedFrames_.stop();
    frameAvailable_.notify_all();
  }

public:
  std::atomic<uint64_t> decoded{0}, dropped{0}, decodeOutputs{0};
  std::atomic<uint64_t> packets{0}, rejected{0}, missingTiming{0};
  // The size each eye is shown at, before any scale (per eye).
  uint32_t shownWidth() const { return baseShown_.width; }
  uint32_t shownHeight() const { return baseShown_.height; }
  // 1.0.0.11: show each eye at percent (50..100) of that size from the next decoded frame on.
  void setShownScale(uint32_t percent) { scalePercent_ = std::clamp<uint32_t>(percent, 50, 100); }
  uint32_t shownScale() const { return scalePercent_; }
  static EyeSize scaledEye(EyeSize shown, uint32_t percent) { return scaledEyeSize(shown, percent); }
  // Decoded surface size (stereo width x height): the encoded size when foveated.
  uint32_t decodedWidth() const { return target_.foveation.enabled ? target_.foveation.encoded[0] * 2 : width_ * 2; }
  uint32_t decodedHeight() const { return target_.foveation.enabled ? target_.foveation.encoded[1] : height_; }
  bool foveated() const { return target_.foveation.enabled; }
  std::atomic<uint64_t> tenBitFrames{0}, foveatedFrames{0}, gazeCentredFrames{0};
  struct Statistics {
    TimingSummary queue, decodeOutput, conversionEnqueue, arrivalInterval, mapWait, conversionCpu;
    TimingSummary decodedQueue, decodeComplete;
    size_t encodedQueued = 0, pendingFrames = 0, queueHighWater = 0;
    size_t decodedQueued = 0, decodedQueueHighWater = 0;
    uint64_t superseded = 0, beforeConversionSuperseded = 0;
  };
  Decoder(void *t, VulkanVideo &gpu, ViewGeometry &geometry,
          bool compatibility)
      : transport_(t), gpu_(gpu), geometry_(geometry), compatibility_(compatibility) {
  }
  // w/h: negotiated view resolution per eye. A foveated stream decodes at the smaller
  // encoded resolution and is expanded back to w x h by the conversion. shown: the size each
  // eye is shown at (shownEyeSize: at most SteamVR's recommended size; the fused pass only).
  void start(uint32_t codec, uint32_t w, uint32_t h,
             std::vector<uint8_t> config, const FoveationParams &foveation = {}, EyeSize shown = {}) {
    codec_ = codec;
    width_ = w;
    height_ = h;
    if (!shown.width || !shown.height) shown = {w, h};
    baseShown_ = shown;
    target_.width = shown.width * 2;
    target_.height = shown.height;
    applyScale();
    target_.foveation = foveation;
    for (int eye = 0; eye < 2; ++eye)
      for (int axis = 0; axis < 2; ++axis)
        target_.defaultShifts[eye][axis] = foveation.shifts[eye][axis];
    config_ = std::move(config);
    conversionThread_ = std::thread([this] { conversionWorker(); });
    thread_ = std::thread([this] { worker(); });
  }
  static int callback(void *c, uint64_t t, const uint8_t *b, uint64_t n) {
    auto self = static_cast<Decoder *>(c);
    try {
      const auto now = monotonicNs();
      {
        std::lock_guard<std::mutex> lock(self->statisticsMutex_);
        if (self->previousArrival_) self->arrivalTiming_.add(now-self->previousArrival_);
        self->previousArrival_=now;
      }
      if (!self->queue_.push(t, b, n)) {
        ++self->rejected;
        return 0; // ALVR owns reference recovery when a packet is rejected.
      }
      ++self->packets;
      const auto queued=self->queue_.size();
      {
        std::lock_guard<std::mutex> lock(self->statisticsMutex_);
        self->queueHighWater_=std::max(self->queueHighWater_,queued);
      }
      return 1;
    } catch (...) {
      ++self->rejected;
      return 0;
    }
  }
  std::shared_ptr<Surface> latestReady(uint64_t after, uint64_t budget=0) {
    std::unique_lock<std::mutex> l(latestMutex_);
    return selectUntilReady(budget, monotonicNs, [&] {
      return pending_.takeLatest(after, [&](const auto &surface) {
        if (!gpu_.ready(surface)) return false;
        if (!surface->firstReadyObservedAt) surface->firstReadyObservedAt=monotonicNs();
        return true;
      });
    }, [&](uint64_t remaining) {
      // Wait for an existing conversion fence or the worker publishing a new
      // surface. No busy loop and no lock held that could block the producer.
      auto pending=pending_.firstAfter(after);
      if (pending) {
        l.unlock();
        gpu_.waitReady(pending,remaining);
        l.lock();
      } else frameAvailable_.wait_for(l,std::chrono::nanoseconds(remaining));
    });
  }
  Statistics statistics() {
    Statistics out;
    out.encodedQueued=queue_.size();
    {
      std::lock_guard<std::mutex> lock(statisticsMutex_);
      out.queue=queueTiming_.take();
      out.decodeOutput=decodeTiming_.take();
      out.conversionEnqueue=conversionTiming_.take();
      out.arrivalInterval=arrivalTiming_.take();
      out.mapWait=mapTiming_.take();
      out.conversionCpu=conversionCpuTiming_.take();
      out.decodedQueue=decodedQueueTiming_.take();
      out.decodeComplete=decodeCompleteTiming_.take();
      out.queueHighWater=queueHighWater_;
      queueHighWater_=0;
    }
    {
      std::lock_guard<std::mutex> lock(latestMutex_);
      out.pendingFrames=pending_.size();
      out.superseded=pending_.superseded;
    }
    const auto decodeQueue = decodedFrames_.statistics();
    out.decodedQueued = decodeQueue.queued;
    out.decodedQueueHighWater = decodeQueue.highWater;
    out.beforeConversionSuperseded = decodeQueue.superseded;
    return out;
  }
  std::string error() {
    std::lock_guard<std::mutex> l(errorMutex_);
    return error_;
  }
  void stop() {
    stopping_ = true;
    queue_.stop();
    decodedFrames_.stop();
    frameAvailable_.notify_all();
    if (thread_.joinable())
      thread_.join();
    if (conversionThread_.joinable())
      conversionThread_.join();
    std::lock_guard<std::mutex> l(latestMutex_);
    pending_.clear();
  }
  ~Decoder() { stop(); }
};
// Lives for the whole ALVR context, including receive-thread teardown after a
// stop event.
class DecoderRouter {
  std::mutex mutex_;
  Decoder *target_ = nullptr;

public:
  void target(Decoder *decoder) {
    std::lock_guard<std::mutex> l(mutex_);
    target_ = decoder;
  }
  static int callback(void *context, uint64_t timestamp, const uint8_t *bytes,
                      uint64_t count) {
    auto &self = *static_cast<DecoderRouter *>(context);
    std::lock_guard<std::mutex> l(self.mutex_);
    return self.target_
               ? Decoder::callback(self.target_, timestamp, bytes, count)
               : 0;
  }
};
} // namespace vrost
