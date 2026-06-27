#include "trace.h"
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <string>

namespace lumen::trace {
namespace {

std::once_flag g_init_flag;
std::ofstream  g_file;
std::string    g_run_id;
std::string    g_topology;
std::mutex     g_mutex;
bool           g_enabled = false;

void do_init() {
  const char *path     = std::getenv("LUMEN_TRACE_FILE");
  if (!path) return;
  const char *run_id   = std::getenv("LUMEN_TRACE_RUN_ID");
  const char *topology = std::getenv("LUMEN_TRACE_TOPOLOGY");
  g_run_id   = run_id   ? run_id   : "unknown";
  g_topology = topology ? topology : "loopback";
  g_file.open(path, std::ios::out | std::ios::app);
  g_enabled = g_file.is_open();
}

}  // namespace

void emit(int64_t frame_index, const char *stage, uint64_t t_ns) {
  std::call_once(g_init_flag, do_init);
  if (!g_enabled) return;
  std::lock_guard<std::mutex> lk(g_mutex);
  g_file << "{\"run_id\":\"" << g_run_id
         << "\",\"topology\":\"" << g_topology
         << "\",\"node\":\"host\""
         << ",\"frame_index\":" << frame_index
         << ",\"stage\":\"" << stage << "\""
         << ",\"t_ns\":" << t_ns
         << ",\"clock\":\"steady\"}\n";
  g_file.flush();
}

}  // namespace lumen::trace
