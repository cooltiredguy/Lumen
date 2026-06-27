# Per-Frame Host Instrumentation + Trace Schema + Reporter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-frame JSONL trace emission to the Lumen C++ pipeline (4 stage timestamps keyed by `frame_index`), a Python schema parser, and a reporter that computes p50/p95/p99 per stage with run-over-run deltas — giving real latency numbers every harness loop run.

**Architecture:** A new `src/trace.h/cpp` module (lazily initialised from `LUMEN_TRACE_FILE` env var, zero-cost when unset) emits one JSONL line per frame per stage into a file on the mini. The Python harness fetches that file via SSH after each run, then `harness/trace/report.py` joins events by `frame_index`, computes percentile tables, and writes `report.md` + `report.json` into the run directory.

**Tech Stack:** C++23 (`<chrono>`, `<mutex>`, `<fstream>`, `<cstdlib>`, `std::call_once`); Python 3 (`statistics`, `json`, `dataclasses`, `pathlib`); existing harness pytest suite; existing build pipeline (cmake on the mini via SSH).

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/trace.h` | Create | C++ trace API: `lumen::trace::emit()`, `ns_now()` inline |
| `src/trace.cpp` | Create | Lazy-init JSONL sink gated by `LUMEN_TRACE_FILE` env var |
| `cmake/compile_definitions/common.cmake` | Modify (line 97–98) | Register `trace.h` + `trace.cpp` in `SUNSHINE_TARGET_FILES` |
| `src/video.cpp` | Modify (lines 2030, 2040, 1497, 1522) | Emit `capture`, `encode_submit`, `encode_done` events |
| `src/stream.cpp` | Modify (line 1576) | Emit `send_last` event |
| `harness/trace/__init__.py` | Create | Makes `harness.trace` importable |
| `harness/trace/schema.py` | Create | `TraceEvent` dataclass + `parse_trace()` |
| `harness/trace/report.py` | Create | `compute_stages()`, `generate_report()` with percentiles + deltas |
| `harness/runner/session.py` | Modify | Pass `LUMEN_TRACE_FILE`, `LUMEN_TRACE_RUN_ID`, `LUMEN_TRACE_TOPOLOGY` to LaunchAgent plist |
| `harness/config.toml` | Modify | Add `trace_dir` under `[run]` |
| `harness/runner/loop.py` | Modify | Pass trace args to `session.launch`, fetch trace file, call reporter |
| `harness/tests/test_trace.py` | Create | Unit tests for `schema.py` |
| `harness/tests/test_report.py` | Create | Unit tests for `report.py` |
| `harness/tests/test_session.py` | Modify | Add test that plist includes trace env keys |

---

## Task 1: C++ trace sink (`src/trace.h` + `src/trace.cpp` + cmake)

**Files:**
- Create: `src/trace.h`
- Create: `src/trace.cpp`
- Modify: `cmake/compile_definitions/common.cmake:97`

- [ ] **Step 1: Write `src/trace.h`**

```cpp
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
// t_ns:        nanoseconds (use ns_now() or frame_timestamp->time_since_epoch().count())
void emit(int64_t frame_index, const char *stage, uint64_t t_ns);

}  // namespace lumen::trace
```

- [ ] **Step 2: Write `src/trace.cpp`**

```cpp
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
```

- [ ] **Step 3: Register in `cmake/compile_definitions/common.cmake`**

In [cmake/compile_definitions/common.cmake](cmake/compile_definitions/common.cmake), after line 97 (`"${CMAKE_SOURCE_DIR}/src/video.cpp"`), add two lines:

```cmake
        "${CMAKE_SOURCE_DIR}/src/trace.h"
        "${CMAKE_SOURCE_DIR}/src/trace.cpp"
```

The block after the edit (lines 95–101) looks like:

```cmake
        "${CMAKE_SOURCE_DIR}/src/stream.cpp"
        "${CMAKE_SOURCE_DIR}/src/stream.h"
        "${CMAKE_SOURCE_DIR}/src/video.cpp"
        "${CMAKE_SOURCE_DIR}/src/video.h"
        "${CMAKE_SOURCE_DIR}/src/trace.h"
        "${CMAKE_SOURCE_DIR}/src/trace.cpp"
        "${CMAKE_SOURCE_DIR}/src/video_colorspace.cpp"
```

- [ ] **Step 4: Verify the cmake file parses cleanly (local check)**

```bash
grep -n "trace" /Users/hazemeissa/Projects/lumen/cmake/compile_definitions/common.cmake
```

Expected: two matching lines — `trace.h` and `trace.cpp`.

- [ ] **Step 5: Commit**

```bash
cd /Users/hazemeissa/Projects/lumen
git checkout -b per-frame-instrumentation
git add src/trace.h src/trace.cpp cmake/compile_definitions/common.cmake
git commit -m "feat: add lumen::trace C++ JSONL sink, gated by LUMEN_TRACE_FILE env var

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Instrument `src/video.cpp` (capture, encode\_submit, encode\_done)

**Files:**
- Modify: `src/video.cpp:1497` (encode_done in avcodec path)
- Modify: `src/video.cpp:1522` (encode_done in nvenc path)
- Modify: `src/video.cpp:2030` (capture)
- Modify: `src/video.cpp:2040` (encode_submit)

- [ ] **Step 1: Add include at the top of video.cpp**

Find the existing includes block (around line 1–30 of video.cpp). Add after the last local `#include`:

```cpp
#include "trace.h"
```

- [ ] **Step 2: Emit `capture` event (video.cpp ~line 2030)**

Current code at lines 2028–2038:
```cpp
      if (!requested_idr_frame || images->peek()) {
        if (auto img = images->pop(max_frametime)) {
          frame_timestamp = img->frame_timestamp;
          if (session->convert(*img)) {
            BOOST_LOG(error) << "Could not convert image"sv;
            return;
          }
        } else if (!images->running()) {
          break;
        }
      }
```

Replace with (add the emit line after `frame_timestamp = img->frame_timestamp`):
```cpp
      if (!requested_idr_frame || images->peek()) {
        if (auto img = images->pop(max_frametime)) {
          frame_timestamp = img->frame_timestamp;
          lumen::trace::emit(frame_nr, "capture", lumen::trace::ns_now());
          if (session->convert(*img)) {
            BOOST_LOG(error) << "Could not convert image"sv;
            return;
          }
        } else if (!images->running()) {
          break;
        }
      }
```

Note: `img->frame_timestamp` is never populated by the macOS SCK backend, so `ns_now()` is used here. This measures encode-thread arrival time (when the frame was dequeued), not the SCK delivery instant. It is the same `steady_clock` domain as all other events, so all inter-stage deltas are valid.

- [ ] **Step 3: Emit `encode_submit` event (video.cpp ~line 2040)**

Current line 2040:
```cpp
      if (encode(frame_nr++, *session, packets, channel_data, frame_timestamp)) {
```

Replace with:
```cpp
      lumen::trace::emit(frame_nr, "encode_submit", lumen::trace::ns_now());
      if (encode(frame_nr++, *session, packets, channel_data, frame_timestamp)) {
```

`frame_nr` is read before the post-increment, so this correctly records the index being submitted.

- [ ] **Step 4: Emit `encode_done` in avcodec path (video.cpp ~line 1497)**

Current lines 1496–1502:
```cpp
      if (av_packet && av_packet->pts == frame_nr) {
        packet->frame_timestamp = frame_timestamp;
      }

      packet->replacements = &session.replacements;
      packet->channel_data = channel_data;
      packets->raise(std::move(packet));
```

Replace with:
```cpp
      if (av_packet && av_packet->pts == frame_nr) {
        packet->frame_timestamp = frame_timestamp;
        lumen::trace::emit(frame_nr, "encode_done", lumen::trace::ns_now());
      }

      packet->replacements = &session.replacements;
      packet->channel_data = channel_data;
      packets->raise(std::move(packet));
```

- [ ] **Step 5: Emit `encode_done` in nvenc path (video.cpp ~line 1522)**

Current lines 1519–1523:
```cpp
    auto packet = std::make_unique<packet_raw_generic>(std::move(encoded_frame.data), encoded_frame.frame_index, encoded_frame.idr);
    packet->channel_data = channel_data;
    packet->after_ref_frame_invalidation = encoded_frame.after_ref_frame_invalidation;
    packet->frame_timestamp = frame_timestamp;
    packets->raise(std::move(packet));
```

Replace with:
```cpp
    auto packet = std::make_unique<packet_raw_generic>(std::move(encoded_frame.data), encoded_frame.frame_index, encoded_frame.idr);
    packet->channel_data = channel_data;
    packet->after_ref_frame_invalidation = encoded_frame.after_ref_frame_invalidation;
    packet->frame_timestamp = frame_timestamp;
    lumen::trace::emit(frame_nr, "encode_done", lumen::trace::ns_now());
    packets->raise(std::move(packet));
```

- [ ] **Step 6: Commit**

```bash
git add src/video.cpp
git commit -m "feat: emit trace events at capture, encode_submit, encode_done in video.cpp

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Instrument `src/stream.cpp` (send\_last)

**Files:**
- Modify: `src/stream.cpp:1576`

- [ ] **Step 1: Add include at the top of stream.cpp**

Find the existing includes block in `src/stream.cpp`. Add:
```cpp
#include "trace.h"
```

- [ ] **Step 2: Emit `send_last` after the "Sent Frame seq" log (stream.cpp ~line 1576)**

Current lines 1574–1580:
```cpp
          frame_network_latency_logger.second_point_now_and_log();

          BOOST_LOG(verbose) << "Sent Frame seq ["sv << packet->frame_index() << "] pts ["sv << timestamp
                             << "] shards ["sv << shards.size() << "/"sv << shards.percentage << "%]"sv
                             << (frame_is_dupe ? " Dupe" : "")
                             << (packet->is_idr() ? " Key" : "")
                             << (packet->after_ref_frame_invalidation ? " RFI" : "");
```

Replace with (add one line after the BOOST_LOG):
```cpp
          frame_network_latency_logger.second_point_now_and_log();

          BOOST_LOG(verbose) << "Sent Frame seq ["sv << packet->frame_index() << "] pts ["sv << timestamp
                             << "] shards ["sv << shards.size() << "/"sv << shards.percentage << "%]"sv
                             << (frame_is_dupe ? " Dupe" : "")
                             << (packet->is_idr() ? " Key" : "")
                             << (packet->after_ref_frame_invalidation ? " RFI" : "");
          lumen::trace::emit(packet->frame_index(), "send_last", lumen::trace::ns_now());
```

- [ ] **Step 3: Commit**

```bash
git add src/stream.cpp
git commit -m "feat: emit trace event at send_last in stream.cpp

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Python trace schema + tests

**Files:**
- Create: `harness/trace/__init__.py`
- Create: `harness/trace/schema.py`
- Create: `harness/tests/test_trace.py`

- [ ] **Step 1: Write failing tests in `harness/tests/test_trace.py`**

```python
import tempfile
import os
from harness.trace.schema import TraceEvent, parse_trace


def test_parse_single_event():
    line = ('{"run_id":"r1","topology":"loopback","node":"host",'
            '"frame_index":42,"stage":"capture","t_ns":123456789,"clock":"steady"}')
    e = TraceEvent.from_line(line)
    assert e.frame_index == 42
    assert e.stage == "capture"
    assert e.t_ns == 123456789
    assert e.topology == "loopback"
    assert e.node == "host"


def test_parse_trace_file():
    lines = [
        '{"run_id":"r1","topology":"loopback","node":"host","frame_index":1,"stage":"capture","t_ns":100,"clock":"steady"}',
        '{"run_id":"r1","topology":"loopback","node":"host","frame_index":1,"stage":"encode_submit","t_ns":200,"clock":"steady"}',
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write('\n'.join(lines) + '\n')
        fname = f.name
    try:
        events = parse_trace(fname)
        assert len(events) == 2
        assert events[0].stage == "capture"
        assert events[1].stage == "encode_submit"
    finally:
        os.unlink(fname)


def test_parse_trace_skips_blank_lines():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write('\n')
        f.write('{"run_id":"r1","topology":"loopback","node":"host","frame_index":1,"stage":"send_last","t_ns":300,"clock":"steady"}\n')
        fname = f.name
    try:
        events = parse_trace(fname)
        assert len(events) == 1
        assert events[0].stage == "send_last"
    finally:
        os.unlink(fname)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/hazemeissa/Projects/lumen
harness/.venv/bin/python -m pytest harness/tests/test_trace.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'harness.trace'`

- [ ] **Step 3: Create `harness/trace/__init__.py`**

```python
```

(Empty file — makes `harness.trace` importable.)

- [ ] **Step 4: Create `harness/trace/schema.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass


@dataclass
class TraceEvent:
    run_id: str
    topology: str
    node: str
    frame_index: int
    stage: str
    t_ns: int
    clock: str

    @classmethod
    def from_line(cls, line: str) -> TraceEvent:
        d = json.loads(line)
        return cls(
            run_id=d["run_id"],
            topology=d["topology"],
            node=d["node"],
            frame_index=d["frame_index"],
            stage=d["stage"],
            t_ns=d["t_ns"],
            clock=d["clock"],
        )


def parse_trace(path: str) -> list[TraceEvent]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(TraceEvent.from_line(line))
    return events
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_trace.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add harness/trace/__init__.py harness/trace/schema.py harness/tests/test_trace.py
git commit -m "feat: add TraceEvent schema and JSONL parser

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Python reporter + tests

**Files:**
- Create: `harness/trace/report.py`
- Create: `harness/tests/test_report.py`

- [ ] **Step 1: Write failing tests in `harness/tests/test_report.py`**

```python
import pytest
import json
import tempfile
import os
from pathlib import Path
from harness.trace.schema import TraceEvent
from harness.trace.report import compute_stages, _percentiles, generate_report


def _event(frame_index, stage, t_ns):
    return TraceEvent(run_id="r1", topology="loopback", node="host",
                      frame_index=frame_index, stage=stage, t_ns=t_ns, clock="steady")


def test_percentiles_empty():
    r = _percentiles([])
    assert r["count"] == 0
    assert r["p50"] is None
    assert r["mean"] is None


def test_percentiles_values():
    # 3 values: 1ms, 2ms, 3ms (in nanoseconds)
    r = _percentiles([1_000_000.0, 2_000_000.0, 3_000_000.0])
    assert r["count"] == 3
    assert r["p50"] == pytest.approx(2.0, abs=0.1)   # middle value
    assert r["mean"] == pytest.approx(2.0, abs=0.1)
    assert r["p99"] == pytest.approx(3.0, abs=0.1)


def test_compute_stages_single_frame():
    events = [
        _event(1, "capture",       0),
        _event(1, "encode_submit", 1_000_000),    # +1ms
        _event(1, "encode_done",   6_000_000),    # +5ms from submit
        _event(1, "send_last",     8_000_000),    # +2ms from encode_done
    ]
    stages = compute_stages(events)
    assert stages["encode_ms"]["count"] == 1
    assert stages["encode_ms"]["p50"] == pytest.approx(5.0, abs=0.1)
    assert stages["host_pipeline_ms"]["p50"] == pytest.approx(8.0, abs=0.1)
    assert stages["wait_encode_ms"]["p50"] == pytest.approx(1.0, abs=0.1)
    assert stages["packetize_send_ms"]["p50"] == pytest.approx(2.0, abs=0.1)


def test_compute_stages_multiple_frames():
    events = []
    for i in range(10):
        base = i * 100_000_000
        events += [
            _event(i, "capture",       base),
            _event(i, "encode_submit", base + 1_000_000),
            _event(i, "encode_done",   base + 6_000_000),
            _event(i, "send_last",     base + 8_000_000),
        ]
    stages = compute_stages(events)
    assert stages["encode_ms"]["count"] == 10
    assert stages["host_pipeline_ms"]["mean"] == pytest.approx(8.0, abs=0.1)


def test_generate_report_writes_files(tmp_path):
    lines = []
    for i in range(5):
        base = i * 100_000_000
        for stage, t in [("capture", base), ("encode_submit", base+1_000_000),
                         ("encode_done", base+6_000_000), ("send_last", base+8_000_000)]:
            lines.append(json.dumps({
                "run_id": "test", "topology": "loopback", "node": "host",
                "frame_index": i, "stage": stage, "t_ns": t, "clock": "steady"
            }))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write('\n'.join(lines) + '\n')
        trace_path = f.name

    run_dir = tmp_path / "20260626-120000"
    run_dir.mkdir()

    try:
        result = generate_report(trace_path, run_dir)
        assert (run_dir / "report.json").exists()
        assert (run_dir / "report.md").exists()
        data = json.loads((run_dir / "report.json").read_text())
        assert data["frame_count"] == 5
        assert "stages" in data
        assert data["stages"]["encode_ms"]["count"] == 5
    finally:
        os.unlink(trace_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_report.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'harness.trace.report'`

- [ ] **Step 3: Create `harness/trace/report.py`**

```python
from __future__ import annotations
import json
import statistics
from pathlib import Path
from typing import Optional
from .schema import TraceEvent, parse_trace


def _percentiles(values: list) -> dict:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "mean": None, "count": 0}
    s = sorted(values)
    n = len(s)

    def pct(p):
        idx = int(n * p / 100)
        return s[min(idx, n - 1)]

    return {
        "p50":  pct(50) / 1e6,
        "p95":  pct(95) / 1e6,
        "p99":  pct(99) / 1e6,
        "mean": statistics.mean(values) / 1e6,
        "count": n,
    }


def compute_stages(events: list) -> dict:
    by_frame: dict = {}
    for e in events:
        key = (e.topology, e.frame_index)
        by_frame.setdefault(key, {})[e.stage] = e.t_ns

    stage_pairs = [
        ("capture",      "encode_submit", "wait_encode_ms"),
        ("encode_submit","encode_done",   "encode_ms"),
        ("encode_done",  "send_last",     "packetize_send_ms"),
        ("capture",      "send_last",     "host_pipeline_ms"),
    ]

    durations: dict = {name: [] for _, _, name in stage_pairs}
    for stages in by_frame.values():
        for a, b, name in stage_pairs:
            if a in stages and b in stages:
                durations[name].append(float(stages[b] - stages[a]))

    return {name: _percentiles(v) for name, v in durations.items()}


def _load_prev_report(reports_dir: Path) -> Optional[dict]:
    dirs = sorted(p for p in reports_dir.iterdir() if p.is_dir())
    if len(dirs) < 2:
        return None
    prev = dirs[-2] / "report.json"
    if prev.exists():
        return json.loads(prev.read_text())
    return None


def _delta_str(curr: Optional[float], prev: Optional[float]) -> str:
    if curr is None or prev is None:
        return ""
    d = curr - prev
    sign = "+" if d >= 0 else ""
    return f" ({sign}{d:.2f}ms)"


def generate_report(trace_path: str, run_dir: Path) -> dict:
    events = parse_trace(trace_path)
    stages = compute_stages(events)
    frame_count = len(set((e.topology, e.frame_index) for e in events))

    result = {"stages": stages, "frame_count": frame_count}
    (run_dir / "report.json").write_text(json.dumps(result, indent=2))

    prev = _load_prev_report(run_dir.parent)
    prev_stages = (prev or {}).get("stages", {})

    lines = [
        f"# Lumen Trace Report\n\n**Run:** {run_dir.name}  "
        f"**Frames:** {frame_count}\n\n",
        "## Host Pipeline Latencies\n\n",
        "| Stage | p50 | p95 | p99 | mean | n | vs prev |\n",
        "|---|---|---|---|---|---|---|\n",
    ]
    for name, stats in stages.items():
        fmt = lambda v: f"{v:.2f}ms" if v is not None else "—"
        delta = _delta_str(stats["p50"], (prev_stages.get(name) or {}).get("p50"))
        lines.append(
            f"| {name} | {fmt(stats['p50'])} | {fmt(stats['p95'])} | "
            f"{fmt(stats['p99'])} | {fmt(stats['mean'])} | {stats['count']} | {delta} |\n"
        )

    md = "".join(lines)
    (run_dir / "report.md").write_text(md)
    print(md)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_report.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add harness/trace/report.py harness/tests/test_report.py
git commit -m "feat: add trace reporter with per-stage p50/p95/p99 and run-over-run deltas

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Wire trace into the harness runner

**Files:**
- Modify: `harness/runner/session.py` (function signature + env dict in `launch()`)
- Modify: `harness/config.toml` (add `trace_dir`)
- Modify: `harness/runner/loop.py` (pass trace args, fetch file, call reporter)
- Modify: `harness/tests/test_session.py` (add plist env-vars assertion)

- [ ] **Step 1: Add a test for trace env vars in the plist**

In `harness/tests/test_session.py`, add this test:

```python
def test_launch_plist_includes_trace_env_vars():
    from harness.runner.launch_agent import render_plist
    plist = render_plist(
        ["/b/lumen", "/c/harness.conf"],
        {
            "SUNSHINE_ASSETS_DIR": "/b/assets",
            "LUMEN_TRACE_FILE": "/t/trace.jsonl",
            "LUMEN_TRACE_RUN_ID": "20260626-120000",
            "LUMEN_TRACE_TOPOLOGY": "loopback",
        },
        "/c/run.log"
    )
    assert "LUMEN_TRACE_FILE" in plist
    assert "/t/trace.jsonl" in plist
    assert "LUMEN_TRACE_RUN_ID" in plist
    assert "20260626-120000" in plist
    assert "LUMEN_TRACE_TOPOLOGY" in plist
```

- [ ] **Step 2: Run test to verify it passes (it tests render_plist directly, no code change needed)**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_session.py::test_launch_plist_includes_trace_env_vars -v
```

Expected: PASS (render_plist already accepts any env dict)

- [ ] **Step 3: Update `harness/config.toml`**

Add `trace_dir` to the `[run]` section. Full `[run]` section after edit:

```toml
[run]
config_dir   = "/Users/hazemeissa/.config/sunshine"
min_log_level = 0
idle_seconds  = 20
trace_dir     = "/Volumes/T7/lumen-harness/traces"
```

- [ ] **Step 4: Update `session.launch()` to accept and pass trace env vars**

In `harness/runner/session.py`, change the `launch()` signature and the `render_plist` call:

Current `launch()` signature:
```python
def launch(ssh_host: str, brew_prefix: str, uid: int, user: str, build_dir: str,
           conf_path: str, log_file: str) -> None:
```

New signature (add `trace_file` and `run_id`):
```python
def launch(ssh_host: str, brew_prefix: str, uid: int, user: str, build_dir: str,
           conf_path: str, log_file: str, trace_file: str = "", run_id: str = "") -> None:
```

Current `render_plist` call inside `launch()`:
```python
    plist = render_plist([f"{build_dir}/lumen", conf_path],
                         {"SUNSHINE_ASSETS_DIR": f"{build_dir}/assets"}, log_file)
```

New call:
```python
    plist = render_plist([f"{build_dir}/lumen", conf_path],
                         {
                             "SUNSHINE_ASSETS_DIR": f"{build_dir}/assets",
                             "LUMEN_TRACE_FILE":     trace_file,
                             "LUMEN_TRACE_RUN_ID":   run_id,
                             "LUMEN_TRACE_TOPOLOGY":  "loopback",
                         }, log_file)
```

- [ ] **Step 5: Run existing session tests to verify nothing broke**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_session.py -v
```

Expected: all 6 PASSED (including the new test)

- [ ] **Step 6: Update `harness/runner/loop.py`**

Full updated `loop.py`:

```python
import time
from pathlib import Path
from .runctx import load_cfg, new_run_dir, REPO
from . import mini, preconditions as pre, deps, build as B, sign, config_render, session, power
from harness.trace.report import generate_report


def run():
    cfg = load_cfg()
    host = cfg["mini"]["ssh_host"]; bp = cfg["mini"]["brew_prefix"]
    deploy = cfg["mini"]["deploy_dir"]; bdir = cfg["mini"]["build_dir"]
    ident = cfg["signing"]["identity"]; cdir = cfg["run"]["config_dir"]
    mll = cfg["run"]["min_log_level"]; idle = cfg["run"]["idle_seconds"]
    trace_dir = cfg["run"]["trace_dir"]
    rundir = new_run_dir()
    run_id = rundir.name
    remote_log = f"{cdir}/harness-run.log"

    print("[1/8] preconditions")
    uid = pre.console_uid(host, bp)
    user = pre.console_user_name(host, bp)
    assert pre.console_user_present(host, bp), "no console user logged in"
    assert pre.aqua_session_ready(host, bp, uid), "no Aqua session"
    print("[2/8] deploy"); mini.rsync_deploy(REPO, host, deploy)
    print("[3/8] deps"); assert deps.ensure_deps(host, bp) == [], "deps still missing"
    print("[4/8] build"); B.build(host, bp, deploy, bdir)
    print("[5/8] sign"); sign.sign_binaries(host, bp, bdir, ident)
    print("[6/8] config")
    conf = config_render.render_sunshine_conf(mll)
    mini.run_remote(host, bp, f"cat > {cdir}/harness.conf <<'EOF'\n{conf}\nEOF")
    mini.run_remote(host, bp, f": > {remote_log}")
    mini.run_remote(host, bp, f"mkdir -p {trace_dir}")
    trace_remote = f"{trace_dir}/{run_id}.jsonl"
    try:
        power.disable_sleep_lock(host, bp)
        print("[7/8] launch + gate")
        session.launch(host, bp, uid, user, bdir, f"{cdir}/harness.conf", remote_log,
                       trace_file=trace_remote, run_id=run_id)
        session.wait_ready(host, bp, remote_log, timeout=90)
        print(f"      ready. idling {idle}s for log capture")
        time.sleep(idle)
    finally:
        print("[8/8] teardown")
        session.teardown(host, bp, uid, user)
        power.restore_sleep_lock(host, bp)
        local_log = rundir / "sunshine.log"
        out = mini.run_remote(host, bp, f"cat {remote_log}", check=False).stdout
        local_log.write_text(out)
        print(f"log saved: {local_log}  ({len(out.splitlines())} lines)")
        # fetch trace and generate report
        trace_out = mini.run_remote(host, bp, f"cat {trace_remote} 2>/dev/null", check=False).stdout
        local_trace = rundir / "host_trace.jsonl"
        local_trace.write_text(trace_out)
        event_count = len([l for l in trace_out.splitlines() if l.strip()])
        print(f"trace saved: {local_trace}  ({event_count} events)")
        if event_count > 0:
            generate_report(str(local_trace), rundir)
            print(f"report: {rundir}/report.md")
        else:
            print("WARNING: trace file is empty — check LUMEN_TRACE_FILE env var in LaunchAgent plist")


if __name__ == "__main__":
    run()
```

- [ ] **Step 7: Run all harness tests to confirm no regressions**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
```

Expected: all existing 18 tests + new 9 (test_trace x3, test_report x5, test_session x1) = 27 PASSED

- [ ] **Step 8: Commit**

```bash
git add harness/runner/session.py harness/runner/loop.py harness/config.toml harness/tests/test_session.py
git commit -m "feat: wire LUMEN_TRACE_FILE into LaunchAgent and fetch+report after each run

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 7: End-to-end smoke run + verify

**No new files — this is the integration verification step.**

- [ ] **Step 1: Run all harness tests locally**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
```

Expected: 27 PASSED, 0 failures

> **Note:** There is a second encode call site at `video.cpp:2300` (the multi-session sync path) that this plan does not instrument. For single-client harness runs only one path is active at a time, so trace data is complete. If multi-session support is added later, instrument that call site identically.

- [ ] **Step 2: Run the harness loop (triggers remote build + run + report)**

```bash
harness/.venv/bin/python -m harness.runner.loop
```

Expected output sequence (abbreviated):
```
[1/8] preconditions
[2/8] deploy
[3/8] deps
[4/8] build
[5/8] sign
[6/8] config
[7/8] launch + gate
      ready. idling 20s for log capture
[8/8] teardown
log saved: harness/reports/YYYYMMDD-HHMMSS/sunshine.log  (NNN lines)
trace saved: harness/reports/YYYYMMDD-HHMMSS/host_trace.jsonl  (NNN events)
# Lumen Trace Report
...
| host_pipeline_ms | X.Xms | ... |
report: harness/reports/YYYYMMDD-HHMMSS/report.md
```

Minimum acceptance bar:
- `host_trace.jsonl` must be non-empty (>0 events). If it is empty, the LaunchAgent plist does not have the env var — re-check Task 6 Step 4.
- `report.md` must exist and contain the `host_pipeline_ms` row.
- The `host_pipeline_ms` p50 value should be in the range 1ms–50ms for a healthy stream. Values > 100ms indicate the encoder is stalling.

- [ ] **Step 3: Inspect the trace file**

```bash
head -8 harness/reports/$(ls -1t harness/reports/ | head -1)/host_trace.jsonl
```

Expected: JSONL lines with `"stage":"capture"`, `"stage":"encode_submit"`, etc., `"frame_index"` incrementing.

If only some stages appear, check:
- `capture`: emits unconditionally via `ns_now()` once a frame is dequeued — if missing, the encode loop is not reaching the image-pop path (check `images->running()`).
- `encode_done` (avcodec path): only emits when `av_packet->pts == frame_nr`. VideoToolbox in real-time mode does not reorder, so this should always match. If missing, the encoder is buffering unexpectedly.
- `encode_done` (nvenc path): emits unconditionally after `encode_frame()` returns.

- [ ] **Step 4: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to merge `per-frame-instrumentation` back to `main`.

---

## Execution Outcome

**Run:** 2026-06-26  **Branch:** `per-frame-instrumentation`

**Smoke run (no Moonlight client):**
- 27 unit tests: all PASSED
- Harness loop completed end-to-end (build → sign → launch → 20s idle → teardown)
- Trace file: 3 events captured, all `encode_done` for `frame_index=1`
- All stages reported as empty (count=0) — expected; see below

**Why only encode_done with no client:**
The 3 init events go through the multi-session sync path (`video.cpp:2300`), which is not instrumented with `encode_submit` (noted as uninstrumented in this plan). The single-client `encode_run` path at `video.cpp:2040` (with all 4 stage emits) only runs when a Moonlight client actively requests frames. Without a client during the 20s idle window, the capture loop never dequeues real images, so `capture`, `encode_submit`, and `send_last` do not fire.

**To get real pipeline data:** Connect a Moonlight client during the idle window. Plan 3 (moonlight-qt instrumentation + dual topology) will handle this by running a client on the M5 Max as part of the automated harness run.

---

## Keep This Updated

**When to update this doc:**
- If `frame_index()` API changes in `video.h` or `stream.h`, update Task 2/3 line references.
- If new encode backends are added (e.g. VideoToolbox-specific path), add a new `encode_done` emit in that path.
- If the `TraceEvent` schema gains new fields (e.g. `extra`), update `schema.py`, `report.py`, and the test fixtures.
- If `harness/config.toml` gains new `[run]` keys used by the reporter, document them here.
- Update the `CLAUDE.md` row for `docs/superpowers/plans/2026-06-26-per-frame-host-instrumentation.md` when the plan is executed and outcome is recorded.
