#pragma once
#include <chrono>
#include <cstdint>

namespace lumen::trace {

inline uint64_t ns_now() {
  return static_cast<uint64_t>(
    std::chrono::steady_clock::now().time_since_epoch().count());
}

// Emit one JSONL event to the trace file.  No-op if LUMEN_TRACE_FILE is unset.
// frame_index: the frame counter (same value as packet->frame_index() in stream.cpp)
// stage:       one of "capture", "encode_submit", "encode_done", "send_last"
// t_ns:        nanoseconds (use ns_now())
void emit(int64_t frame_index, const char *stage, uint64_t t_ns);

}  // namespace lumen::trace
