/**
 * @file src/platform/macos/nv12_zero_device.cpp
 * @brief Definitions for NV12 zero copy device on macOS.
 */
// standard includes
#include <utility>

// local includes
#include "src/platform/macos/av_img_t.h"
#include "src/platform/macos/nv12_zero_device.h"
#include "src/video.h"

extern "C" {
#include "libavutil/imgutils.h"
#include "libavutil/pixfmt.h"
}

namespace platf {

  void free_frame(AVFrame *frame) {
    av_frame_free(&frame);
  }

  void free_buffer(void *opaque, uint8_t *data) {
    CVPixelBufferRelease((CVPixelBufferRef) data);
  }

  int nv12_zero_device::convert(platf::img_t &img) {
    auto *av_img = (av_img_t *) &img;

    if (!av_img->pixel_buffer || !av_img->pixel_buffer->buf) {
      return -1;  // No valid pixel buffer — caller should skip this frame
    }

    if (!this->av_frame) {
      return -1;
    }

    // Release any existing CVPixelBuffer previously retained for encoding
    av_buffer_unref(&this->av_frame->buf[0]);

    // Attach an AVBufferRef to this frame which will retain ownership of the CVPixelBuffer
    // until av_buffer_unref() is called (above) or the frame is freed with av_frame_free().
    this->av_frame->buf[0] = av_buffer_create(
        (uint8_t *) CFRetain(av_img->pixel_buffer->buf), 0, free_buffer, nullptr, 0);

    // Place a CVPixelBufferRef at data[3] as required by AV_PIX_FMT_VIDEOTOOLBOX
    this->av_frame->data[3] = (uint8_t *) av_img->pixel_buffer->buf;

    // Detect format directly from CoreVideo buffer to avoid VideoToolbox CPU/GPU conversion fallback
    OSType format_type = CVPixelBufferGetPixelFormatType((CVPixelBufferRef) av_img->pixel_buffer->buf);
    if (format_type == kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange) {
      this->av_frame->color_range = AVCOL_RANGE_MPEG;       // HDR / P010 (10-bit)
      this->av_frame->colorspace = AVCOL_SPC_BT2020_NCL;
      this->av_frame->color_primaries = AVCOL_PRI_BT2020;
      this->av_frame->color_trc = AVCOL_TRC_SMPTE2084;      // PQ curve
    } else {
      this->av_frame->color_range = AVCOL_RANGE_MPEG;       // SDR / NV12 (8-bit)
      this->av_frame->colorspace = AVCOL_SPC_BT709;
      this->av_frame->color_primaries = AVCOL_PRI_BT709;
      this->av_frame->color_trc = AVCOL_TRC_BT709;
    }

    return 0;
  }

  int nv12_zero_device::set_frame(AVFrame *frame, AVBufferRef *hw_frames_ctx) {
    this->frame = frame;

    this->av_frame.reset(frame);

    resolution_fn(this->display, frame->width, frame->height);

    return 0;
  }

  int nv12_zero_device::init(void *display, pix_fmt_e pix_fmt, resolution_fn_t resolution_fn, const pixel_format_fn_t &pixel_format_fn) {
    pixel_format_fn(display, pix_fmt == pix_fmt_e::nv12 
        ? kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange 
        : kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange);

    this->display = display;
    this->resolution_fn = std::move(resolution_fn);

    // we never use this pointer, but its existence is checked/used
    // by the platform independent code
    data = this;

    return 0;
  }

}  // namespace platf
