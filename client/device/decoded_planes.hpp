#pragma once
// 1.0.0.7: which planar upload (client/device/planar_upload.hpp) a frame from FFmpeg takes on the Windows
// client. A D3D11VA frame is downloaded as NV12 or P010 (its surface pool's format); a CPU-decoded one
// is written as it is: NV12/P010, or the planar yuv420p (also labelled yuvj420p, full range) and
// yuv420p10le of FFmpeg's software decoders. Anything else is refused with the decoder named.
#include "client/device/planar_upload.hpp"
extern "C" {
#include <libavutil/frame.h>
#include <libavutil/hwcontext.h>
#include <libavutil/pixdesc.h>
}
#include <stdexcept>
#include <string>

namespace vrost {

inline const char *pixelFormatName(int format) {
  const char *name = av_get_pix_fmt_name(static_cast<AVPixelFormat>(format));
  return name ? name : "unknown";
}

// `download` becomes the D3D11VA surface format for a hardware frame (AV_PIX_FMT_NONE otherwise).
inline PlanarFormat planarFormat(const AVFrame *frame, AVPixelFormat &download) {
  download = AV_PIX_FMT_NONE;
  if (frame->format == AV_PIX_FMT_D3D11) {
    if (!frame->hw_frames_ctx) throw std::runtime_error("D3D11VA returned a frame without its surface pool");
    download = reinterpret_cast<const AVHWFramesContext *>(frame->hw_frames_ctx->data)->sw_format;
    if (download == AV_PIX_FMT_NV12) return PlanarFormat::Nv12;
    if (download == AV_PIX_FMT_P010) return PlanarFormat::P010;
    throw std::runtime_error(std::string("D3D11VA returned ") + pixelFormatName(download) +
                             " surfaces; the client needs NV12 or P010");
  }
  switch (frame->format) {
  case AV_PIX_FMT_NV12: return PlanarFormat::Nv12;
  case AV_PIX_FMT_P010: return PlanarFormat::P010;
  case AV_PIX_FMT_YUV420P:
  case AV_PIX_FMT_YUVJ420P: return PlanarFormat::Yuv420p; // J: full range, which color_range also says
  case AV_PIX_FMT_YUV420P10: return PlanarFormat::Yuv420p10;
  default:
    throw std::runtime_error(std::string("CPU decoding returned ") + pixelFormatName(frame->format) +
                             " frames; the client shows 8- or 10-bit 4:2:0 video");
  }
}

} // namespace vrost
