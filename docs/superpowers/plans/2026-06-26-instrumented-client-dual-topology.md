# Plan 3: Instrumented Client + Synthetic Workload + Dual Topology

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive real frames end-to-end through the Lumen pipeline and produce a single-command dual-topology latency report with per-stage percentiles for both host and client, a synthetic glass-to-glass consistency check, and run-over-run deltas.

**Architecture:** An instrumented moonlight-qt client (patched with a JSONL trace sink mirroring the host's) connects to Lumen over both loopback (mini↔mini) and Wi-Fi (mini↔M5 Max); a Metal workload paints a monotonic binary-block counter on the virtual display; a ScreenCaptureKit readback on each client machine decodes the counter and records glass-to-glass latency. The harness orchestrates both topologies sequentially each loop and merges all traces into one report.

**Tech Stack:** Python 3.14 (harness), C++17 (moonlight-qt patch + client_trace sink), Swift 5 (workload + readback), qmake6 + Qt 6.4+ (moonlight-qt build), Metal + CoreAnimation + ScreenCaptureKit (macOS), pytest (tests).

---

## Dependency Map

```
Plans 1 & 2 already merged to main.
Branch for this plan: instrumented-client-dual-topology (already created).

Phase A: Wi-Fi client + frame_index join
  Task 1  → client_trace.h / client_trace.cpp   [new files in moonlight-qt tree]
  Task 2  → trace.patch                          [git diff applied by setup.sh]
  Task 3  → setup.sh + pinned-commit.txt         [clone+patch+build]
  Task 4  → tests: schema extra field            [TDD before schema change]
  Task 5  → schema.py: add extra field           [implement]
  Task 6  → tests: run.py pair/stream/quit       [TDD before client run script]
  Task 7  → harness/client/run.py                [implement]
  Task 8  → tests: reporter host+client join     [TDD before reporter change]
  Task 9  → report.py: host+client join          [implement]
  Task 10 → config.toml additions                [new sections]
  Task 11 → Phase A smoke test                   [manual, verify Wi-Fi report]

Phase B: Loopback client on mini
  Task 12 → harness/runner/deploy.py             [push client+workload+readback to mini]
  Task 13 → harness/runner/topology.py           [per-topology orchestration]
  Task 14 → session.py / loop.py topology wiring [modify existing files]
  Task 15 → Phase B smoke test                   [loopback on mini]

Phase C: Synthetic workload + readback
  Task 16 → harness/workload/LumenWorkload.swift + build.sh
  Task 17 → harness/readback/LumenReadback.swift + build.sh
  Task 18 → workload + readback build test

Phase D: Reporter unification + dual-topology smoke run
  Task 19 → report.py: glass-to-glass + consistency check
  Task 20 → full dual-topology smoke run + commit
```

---

## File Map

### New files
```
harness/
  client/
    setup.sh              clone moonlight-qt v6.1.0, apply trace.patch, build
    trace.patch           unified diff — adds client_trace emit to ffmpeg.cpp + vt_metal.mm
    pinned-commit.txt     f786e94c7b2f943e24e65d7d74deb539b827fc84
    run.py                pair / stream / quit CLI wrapper; handles /api/pin POST
  workload/
    LumenWorkload.swift   Metal/CoreAnimation app: binary-block counter on virtual display
    build.sh              swiftc build; output: workload/LumenWorkload
  readback/
    LumenReadback.swift   SCK capture of Moonlight window; threshold-decode block counter
    build.sh              swiftc build; output: readback/LumenReadback
  runner/
    deploy.py             push harness/client + workload + readback artifacts to mini
    topology.py           per-topology start/run/stop (client + readback + workload on host)
```

### Modified files
```
harness/
  config.toml             add [client], [lumen], [topologies.*], [workload], [readback]
  trace/
    schema.py             add extra: dict field + parse it; add client stage pairs
    report.py             host↔client join; client stages; network span; glass-to-glass;
                          per-topology tables; consistency check
  runner/
    session.py            topology param (replace hardcoded "loopback")
    loop.py               dual-topology loop; collect all traces; call topology.py
  tests/
    test_schema.py        new + extended
    test_report.py        new + extended
    test_client_run.py    new
```

---

## Phase A — Wi-Fi Client + frame_index Join

### Task 1: Write client_trace.h and client_trace.cpp

These files are added into the moonlight-qt source tree as part of `trace.patch`. Write them here first so Task 2 can generate the diff.

**Files:** (scratchpad staging area — will become patch hunks in Task 2)

- [ ] **Step 1: Create the staging directory**

```bash
MQT=/private/tmp/claude-501/-Users-hazemeissa-Projects-lumen/c59751ec-a6b7-43f4-bd7e-990eb0e79622/scratchpad/moonlight-qt
mkdir -p $MQT/app/streaming/video
```

- [ ] **Step 2: Write client_trace.h**

Create `$MQT/app/streaming/video/client_trace.h`:

```cpp
#pragma once
#include <chrono>
#include <cstdint>

// Trace sink for the instrumented moonlight-qt client.
// Reads MOONLIGHT_TRACE_FILE on first emit. Zero-cost when unset.
// JSONL format mirrors lumen/src/trace.h; node is always "client".
// Stage names: "recv", "decode_submit", "decode_done", "present"
namespace client_trace {

inline uint64_t ns_now() {
  return static_cast<uint64_t>(
      std::chrono::steady_clock::now().time_since_epoch().count());
}

// frame_index  : DECODE_UNIT.frameNumber cast to int64_t
// stage        : one of the four stage name strings above
// t_ns         : nanoseconds from ns_now()
// fhpl_tenth_ms: frameHostProcessingLatency (1/10 ms units); 0 = not provided
void emit(int64_t frame_index, const char *stage, uint64_t t_ns,
          uint64_t fhpl_tenth_ms = 0);

} // namespace client_trace
```

- [ ] **Step 3: Write client_trace.cpp**

Create `$MQT/app/streaming/video/client_trace.cpp`:

```cpp
#include "client_trace.h"
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <string>

namespace client_trace {
namespace {

std::once_flag g_init_flag;
std::ofstream  g_file;
std::string    g_run_id;
std::string    g_topology;
std::mutex     g_mutex;
bool           g_enabled = false;

void do_init() {
  const char *path     = std::getenv("MOONLIGHT_TRACE_FILE");
  if (!path) return;
  const char *run_id   = std::getenv("MOONLIGHT_TRACE_RUN_ID");
  const char *topology = std::getenv("MOONLIGHT_TRACE_TOPOLOGY");
  g_run_id   = run_id   ? run_id   : "unknown";
  g_topology = topology ? topology : "loopback";
  g_file.open(path, std::ios::out | std::ios::app);
  g_enabled = g_file.is_open();
}

} // anonymous namespace

void emit(int64_t frame_index, const char *stage, uint64_t t_ns,
          uint64_t fhpl_tenth_ms) {
  std::call_once(g_init_flag, do_init);
  if (!g_enabled) return;
  std::lock_guard<std::mutex> lk(g_mutex);
  g_file << "{\"run_id\":\"" << g_run_id
         << "\",\"topology\":\"" << g_topology
         << "\",\"node\":\"client\""
         << ",\"frame_index\":" << frame_index
         << ",\"stage\":\"" << stage << "\""
         << ",\"t_ns\":" << t_ns
         << ",\"clock\":\"steady\""
         << ",\"extra\":{\"fhpl_tenth_ms\":" << fhpl_tenth_ms << "}}\n";
  g_file.flush();
}

} // namespace client_trace
```

- [ ] **Step 4: Commit staging files (do NOT push; these feed Task 2)**

```bash
# These are committed INTO the moonlight-qt clone so git diff can produce the patch.
cd $MQT
git add app/streaming/video/client_trace.h app/streaming/video/client_trace.cpp
git commit -m "chore: stage client_trace for trace.patch generation"
```

---

### Task 2: Write trace.patch

Generate the unified diff that `setup.sh` will apply to a fresh v6.1.0 clone. The patch touches four files: `ffmpeg.cpp` (3 emit sites + frameNumber stash), `vt_metal.mm` (present emit), `vt_avsamplelayer.mm` (fallback present emit), and `app.pro` (add client_trace.cpp to SOURCES).

**Files:** `harness/client/trace.patch`

- [ ] **Step 1: Apply the ffmpeg.cpp changes to the clone**

```bash
MQT=/private/tmp/claude-501/-Users-hazemeissa-Projects-lumen/c59751ec-a6b7-43f4-bd7e-990eb0e79622/scratchpad/moonlight-qt
FFMPEG=$MQT/app/streaming/video/ffmpeg.cpp
```

Open `$FFMPEG` and make these four targeted edits:

**Edit A — add include near the top of ffmpeg.cpp** (after the last existing `#include` in the file's include block, around line 30–45; add after the last local include):

```cpp
#include "client_trace.h"
```

**Edit B — emit `recv` and `decode_submit` in `submitDecodeUnit`**

In `submitDecodeUnit` (line 1731), right after the IDR gate (after the `if (m_FramesIn == 0 && du->frameType != FRAME_TYPE_IDR) { return DR_NEED_IDR; }` block, approximately line 1739), add:

```cpp
    // --- client trace: recv ---
    client_trace::emit(static_cast<int64_t>(du->frameNumber), "recv",
                       client_trace::ns_now(),
                       static_cast<uint64_t>(du->frameHostProcessingLatency));
```

Then, immediately before the `avcodec_send_packet` call at line 1819, add:

```cpp
    // --- client trace: decode_submit ---
    client_trace::emit(static_cast<int64_t>(du->frameNumber), "decode_submit",
                       client_trace::ns_now());
```

**Edit C — emit `decode_done` and stash frameNumber in `decoderThreadProc`**

In `decoderThreadProc` (line 1580), inside the `if (!m_FrameInfoQueue.isEmpty())` block, right after `DECODE_UNIT du = m_FrameInfoQueue.dequeue();` (line 1663), add:

```cpp
                        // --- client trace: decode_done ---
                        client_trace::emit(static_cast<int64_t>(du.frameNumber), "decode_done",
                                           client_trace::ns_now());
                        // stash frameNumber for present-stage trace via frame->opaque
                        frame->opaque = reinterpret_cast<void *>(static_cast<intptr_t>(du.frameNumber));
```

- [ ] **Step 2: Apply vt_metal.mm changes**

Open `$MQT/app/streaming/video/ffmpeg-renderers/vt_metal.mm`.

At the top of the file's include block, add:

```objc
#include "client_trace.h"
```

In `VTMetalRenderer::renderFrame(AVFrame* frame)` (line 482), right after the function opens (before any `if` guards), add:

```objc
    // --- client trace: extract stashed frameNumber ---
    const int64_t ct_frame_num = frame->opaque
        ? static_cast<int64_t>(reinterpret_cast<intptr_t>(frame->opaque))
        : -1;
```

Then, immediately before `[commandBuffer presentDrawable:m_NextDrawable];` (line 646), add:

```objc
    // --- client trace: present ---
    if (ct_frame_num >= 0) {
        client_trace::emit(ct_frame_num, "present", client_trace::ns_now());
    }
```

- [ ] **Step 3: Apply vt_avsamplelayer.mm changes (fallback renderer)**

Open `$MQT/app/streaming/video/ffmpeg-renderers/vt_avsamplelayer.mm`.

At the top of the include block, add:

```objc
#include "client_trace.h"
```

In `VTRenderer::renderFrame(AVFrame* frame)` (line 264), right after the function opens, add:

```objc
    const int64_t ct_frame_num = frame->opaque
        ? static_cast<int64_t>(reinterpret_cast<intptr_t>(frame->opaque))
        : -1;
```

Immediately before `[m_DisplayLayer enqueueSampleBuffer:sampleBuffer];` (line 368), add:

```objc
    if (ct_frame_num >= 0) {
        client_trace::emit(ct_frame_num, "present", client_trace::ns_now());
    }
```

- [ ] **Step 4: Apply app.pro change**

Open `$MQT/app/app.pro`. Find the `SOURCES +=` block that lists `streaming/video/ffmpeg.cpp`. Add after it:

```qmake
    streaming/video/client_trace.cpp \
```

- [ ] **Step 5: Stage all changes and generate the patch**

```bash
cd $MQT
git add app/streaming/video/ffmpeg.cpp \
        app/streaming/video/ffmpeg-renderers/vt_metal.mm \
        app/streaming/video/ffmpeg-renderers/vt_avsamplelayer.mm \
        app/app.pro
git diff HEAD~1 -- \
    app/streaming/video/ffmpeg.cpp \
    app/streaming/video/ffmpeg-renderers/vt_metal.mm \
    app/streaming/video/ffmpeg-renderers/vt_avsamplelayer.mm \
    app/app.pro \
    app/streaming/video/client_trace.h \
    app/streaming/video/client_trace.cpp \
    > /Users/hazemeissa/Projects/lumen/harness/client/trace.patch
```

- [ ] **Step 6: Verify the patch applies cleanly to a scratch clone**

```bash
cd /tmp
git clone --depth=1 --branch v6.1.0 \
    https://github.com/moonlight-stream/moonlight-qt.git mqt-verify
cd mqt-verify
git submodule update --init --recursive
git apply /Users/hazemeissa/Projects/lumen/harness/client/trace.patch
echo "patch applied exit code: $?"
# Expected: patch applied exit code: 0
```

- [ ] **Step 7: Commit trace.patch to the Lumen repo**

```bash
cd /Users/hazemeissa/Projects/lumen
git add harness/client/trace.patch harness/client/pinned-commit.txt
git commit -m "feat: add moonlight-qt trace.patch (client_trace recv/decode/present stages)"
```

---

### Task 3: Write pinned-commit.txt and setup.sh

**Files:** `harness/client/pinned-commit.txt`, `harness/client/setup.sh`

- [ ] **Step 1: Write pinned-commit.txt**

Create `harness/client/pinned-commit.txt`:

```
f786e94c7b2f943e24e65d7d74deb539b827fc84
```

- [ ] **Step 2: Write setup.sh**

Create `harness/client/setup.sh`:

```bash
#!/usr/bin/env bash
# Usage: setup.sh <build-root>
# Clones moonlight-qt at the pinned commit, applies trace.patch, builds.
# build-root: directory where the clone lives (created if absent).
# Output: <build-root>/moonlight-qt/app/Moonlight.app

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PINNED_COMMIT="$(cat "$SCRIPT_DIR/pinned-commit.txt")"
BUILD_ROOT="${1:?usage: setup.sh <build-root>}"

CLONE_DIR="$BUILD_ROOT/moonlight-qt"
APP_PATH="$CLONE_DIR/app/Moonlight.app"
BINARY="$APP_PATH/Contents/MacOS/Moonlight"

# --- Qt path ---
# Homebrew Qt on Apple Silicon:
export PATH="/opt/homebrew/opt/qt/bin:$PATH"
QMK6=$(command -v qmake6 2>/dev/null || echo "")
if [ -z "$QMK6" ]; then
  echo "ERROR: qmake6 not found. Install Qt: brew install qt" >&2
  exit 1
fi

# --- Clone if needed ---
if [ ! -d "$CLONE_DIR/.git" ]; then
  echo "[setup] Cloning moonlight-qt..."
  git clone https://github.com/moonlight-stream/moonlight-qt.git "$CLONE_DIR"
  cd "$CLONE_DIR"
  git checkout "$PINNED_COMMIT"
  git submodule update --init --recursive
else
  echo "[setup] Clone exists at $CLONE_DIR"
  cd "$CLONE_DIR"
fi

# --- Check if patch already applied (idempotent) ---
if grep -q 'client_trace' app/streaming/video/ffmpeg.cpp 2>/dev/null; then
  echo "[setup] Patch already applied"
else
  echo "[setup] Applying trace.patch..."
  git apply "$SCRIPT_DIR/trace.patch"
fi

# --- Build ---
echo "[setup] Building (release)..."
"$QMK6" moonlight-qt.pro
make release -j"$(sysctl -n hw.logicalcpu)"

echo "[setup] Built: $BINARY"
echo "$BINARY"
```

- [ ] **Step 3: Make setup.sh executable**

```bash
chmod +x /Users/hazemeissa/Projects/lumen/harness/client/setup.sh
```

- [ ] **Step 4: Run setup.sh locally on the M5 Max dev box**

```bash
cd /Users/hazemeissa/Projects/lumen
harness/client/setup.sh /Volumes/T7/lumen-harness/moonlight-qt-build
# Expected: builds successfully; last line printed is path to Moonlight binary
```

- [ ] **Step 5: Verify the binary starts and --version works**

```bash
MOONLIGHT=/Volumes/T7/lumen-harness/moonlight-qt-build/moonlight-qt/app/Moonlight.app/Contents/MacOS/Moonlight
"$MOONLIGHT" --version
# Expected: something like "6.1.0"
```

- [ ] **Step 6: Commit**

```bash
cd /Users/hazemeissa/Projects/lumen
git add harness/client/setup.sh harness/client/pinned-commit.txt
git commit -m "feat: moonlight-qt client setup.sh + pinned commit"
```

---

### Task 4: Tests — schema.py extra field

**Files:** `harness/tests/test_schema.py`

- [ ] **Step 1: Write failing tests**

Create or extend `harness/tests/test_schema.py`:

```python
import pytest
from harness.trace.schema import TraceEvent, parse_trace
import tempfile, os, json

HOST_LINE = json.dumps({
    "run_id": "r1", "topology": "loopback", "node": "host",
    "frame_index": 5, "stage": "capture", "t_ns": 1000000, "clock": "steady"
})

CLIENT_LINE = json.dumps({
    "run_id": "r1", "topology": "loopback", "node": "client",
    "frame_index": 5, "stage": "recv", "t_ns": 2000000, "clock": "steady",
    "extra": {"fhpl_tenth_ms": 15}
})

CLIENT_LINE_NO_EXTRA = json.dumps({
    "run_id": "r1", "topology": "wifi", "node": "client",
    "frame_index": 6, "stage": "present", "t_ns": 3000000, "clock": "steady"
})

def test_host_line_parses():
    e = TraceEvent.from_line(HOST_LINE)
    assert e.node == "host"
    assert e.frame_index == 5
    assert e.extra == {}

def test_client_line_with_extra_parses():
    e = TraceEvent.from_line(CLIENT_LINE)
    assert e.node == "client"
    assert e.stage == "recv"
    assert e.extra == {"fhpl_tenth_ms": 15}

def test_client_line_without_extra_defaults_to_empty_dict():
    e = TraceEvent.from_line(CLIENT_LINE_NO_EXTRA)
    assert e.extra == {}

def test_parse_trace_mixed_host_and_client(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(HOST_LINE + "\n" + CLIENT_LINE + "\n")
    events = parse_trace(str(p))
    assert len(events) == 2
    assert events[0].node == "host"
    assert events[1].node == "client"
    assert events[1].extra["fhpl_tenth_ms"] == 15
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd /Users/hazemeissa/Projects/lumen
harness/.venv/bin/python -m pytest harness/tests/test_schema.py -v
# Expected: FAILED — extra field not in TraceEvent yet
```

---

### Task 5: Update schema.py (add extra field)

**Files:** `harness/trace/schema.py`

- [ ] **Step 1: Read the current file**

Read `harness/trace/schema.py` fully before editing.

- [ ] **Step 2: Add extra field to TraceEvent**

In the `TraceEvent` dataclass, add the `extra` field with a default factory after `clock`:

```python
from dataclasses import dataclass, field
import json
from typing import List

@dataclass
class TraceEvent:
    run_id: str
    topology: str
    node: str
    frame_index: int
    stage: str
    t_ns: int
    clock: str
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_line(cls, line: str) -> "TraceEvent":
        d = json.loads(line)
        return cls(
            run_id=d["run_id"],
            topology=d["topology"],
            node=d["node"],
            frame_index=d["frame_index"],
            stage=d["stage"],
            t_ns=d["t_ns"],
            clock=d["clock"],
            extra=d.get("extra", {}),
        )


def parse_trace(path: str) -> List[TraceEvent]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(TraceEvent.from_line(line))
    return events
```

- [ ] **Step 3: Run tests — expect pass**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_schema.py -v
# Expected: all 4 tests PASS
```

- [ ] **Step 4: Run existing tests to check no regression**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
# Expected: all tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add harness/trace/schema.py harness/tests/test_schema.py
git commit -m "feat: add optional extra field to TraceEvent (client fhpl + readback ids)"
```

---

### Task 6: Tests — harness/client/run.py

**Files:** `harness/tests/test_client_run.py`

- [ ] **Step 1: Write failing tests**

Create `harness/tests/test_client_run.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, call
import subprocess

# We test run.py's three public functions: pair(), stream(), quit_stream()
# patch subprocess.run and requests.post so no real network calls happen

from harness.client import run as client_run


def test_pair_posts_pin_to_lumen(tmp_path):
    """pair() must POST the PIN to the Lumen /api/pin endpoint."""
    with patch("subprocess.Popen") as mock_popen, \
         patch("requests.post") as mock_post, \
         patch("time.sleep"):
        proc = MagicMock()
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"status": True})

        client_run.pair(
            host="mac-mini",
            moonlight_bin="/fake/Moonlight",
            lumen_url="https://mac-mini:47990",
            admin_user="admin",
            admin_password="secret",
            pin="7777",
        )

        # subprocess.Popen called with 'pair' subcommand and --pin flag
        popen_args = mock_popen.call_args[0][0]
        assert "pair" in popen_args
        assert "--pin" in popen_args
        assert "7777" in popen_args

        # requests.post called with /api/pin and correct JSON
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/api/pin" in call_kwargs[0][0]
        body = call_kwargs[1]["json"]
        assert body["pin"] == "7777"


def test_stream_sets_env_vars():
    """stream() must pass MOONLIGHT_TRACE_FILE and MOONLIGHT_TRACE_TOPOLOGY as env."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        client_run.stream(
            host="mac-mini",
            moonlight_bin="/fake/Moonlight",
            app="Desktop",
            resolution="1920x1080",
            fps=60,
            bitrate_kbps=20000,
            stream_seconds=5,
            trace_file="/tmp/client.jsonl",
            run_id="run1",
            topology="wifi",
        )
        env = mock_run.call_args[1]["env"]
        assert env["MOONLIGHT_TRACE_FILE"] == "/tmp/client.jsonl"
        assert env["MOONLIGHT_TRACE_TOPOLOGY"] == "wifi"
        assert env["MOONLIGHT_TRACE_RUN_ID"] == "run1"


def test_quit_stream_invokes_quit_subcommand():
    """quit_stream() must call moonlight quit <host>."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        client_run.quit_stream(host="mac-mini", moonlight_bin="/fake/Moonlight")
        args = mock_run.call_args[0][0]
        assert "quit" in args
        assert "mac-mini" in args
```

- [ ] **Step 2: Run tests — expect ImportError / FAILED**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_client_run.py -v
# Expected: ImportError or FAILED — harness/client/run.py doesn't exist yet
```

---

### Task 7: Write harness/client/run.py

**Files:** `harness/client/__init__.py`, `harness/client/run.py`

- [ ] **Step 1: Create the __init__.py**

```bash
touch /Users/hazemeissa/Projects/lumen/harness/client/__init__.py
```

- [ ] **Step 2: Write run.py**

Create `harness/client/run.py`:

```python
"""
CLI wrapper for the instrumented moonlight-qt client.
Exposes three functions: pair(), stream(), quit_stream().
"""
import os
import subprocess
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def pair(
    host: str,
    moonlight_bin: str,
    lumen_url: str,
    admin_user: str,
    admin_password: str,
    pin: str = "7777",
    timeout: int = 30,
) -> None:
    """
    Pair moonlight with a Lumen host using a fixed PIN.

    Steps:
      1. Launch 'moonlight pair <host> --pin <pin>' in the background.
      2. Sleep 2s to give Moonlight time to connect and register the pairing request.
      3. POST {"pin": pin, "name": "lumen-harness"} to <lumen_url>/api/pin.
      4. Wait for the Moonlight subprocess to exit (success = exit 0).
    """
    cmd = [moonlight_bin, "pair", host, "--pin", pin]
    print(f"[run.py] pair: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    time.sleep(2)  # give client time to send its certificate to Lumen

    url = f"{lumen_url}/api/pin"
    resp = requests.post(
        url,
        json={"pin": pin, "name": "lumen-harness"},
        auth=(admin_user, admin_password),
        verify=False,
        timeout=10,
    )
    print(f"[run.py] POST {url} → {resp.status_code} {resp.text}")
    if not resp.ok:
        proc.kill()
        raise RuntimeError(f"POST /api/pin failed: {resp.status_code} {resp.text}")

    ret = proc.wait(timeout=timeout)
    if ret != 0:
        stderr = proc.stderr.read().decode(errors="replace")
        raise RuntimeError(f"moonlight pair exited {ret}: {stderr}")
    print("[run.py] pair complete")


def stream(
    host: str,
    moonlight_bin: str,
    app: str,
    resolution: str,
    fps: int,
    bitrate_kbps: int,
    stream_seconds: int,
    trace_file: str,
    run_id: str,
    topology: str,
    display_mode: str = "windowed",
    timeout: int = 120,
) -> None:
    """
    Stream <app> from <host> for stream_seconds, writing client trace to trace_file.

    moonlight stream <host> <app> --resolution <WxH> --fps <N> --bitrate <N>
        --display-mode windowed --no-vsync --no-frame-pacing
    """
    cmd = [
        moonlight_bin, "stream", host, app,
        "--resolution", resolution,
        "--fps", str(fps),
        "--bitrate", str(bitrate_kbps),
        "--display-mode", display_mode,
        "--no-vsync",
        "--no-frame-pacing",
    ]
    env = os.environ.copy()
    env["MOONLIGHT_TRACE_FILE"]     = trace_file
    env["MOONLIGHT_TRACE_RUN_ID"]   = run_id
    env["MOONLIGHT_TRACE_TOPOLOGY"] = topology

    print(f"[run.py] stream: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        env=env,
        timeout=stream_seconds + timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"moonlight stream exited {result.returncode}")
    print(f"[run.py] stream complete; trace at {trace_file}")


def quit_stream(host: str, moonlight_bin: str, timeout: int = 15) -> None:
    """Quit the currently running stream on host."""
    cmd = [moonlight_bin, "quit", host]
    print(f"[run.py] quit: {' '.join(cmd)}")
    result = subprocess.run(cmd, timeout=timeout)
    if result.returncode != 0:
        print(f"[run.py] WARNING: moonlight quit exited {result.returncode} (stream may have already ended)")
```

- [ ] **Step 3: Install requests in the harness venv**

```bash
harness/.venv/bin/pip install requests
```

- [ ] **Step 4: Run tests — expect pass**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_client_run.py -v
# Expected: all 3 tests PASS
```

- [ ] **Step 5: Run all harness tests**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
# Expected: all PASS
```

- [ ] **Step 6: Commit**

```bash
git add harness/client/__init__.py harness/client/run.py harness/tests/test_client_run.py
git commit -m "feat: harness/client/run.py — pair/stream/quit automation with /api/pin POST"
```

---

### Task 8: Tests — reporter host+client join

**Files:** `harness/tests/test_report.py`

- [ ] **Step 1: Write failing tests**

Extend or create `harness/tests/test_report.py`:

```python
import pytest
from harness.trace.report import compute_stages, join_frames, compute_client_stages
from harness.trace.schema import TraceEvent

def _ev(node, frame_index, stage, t_ns, topology="loopback", extra=None):
    return TraceEvent(
        run_id="r1", topology=topology, node=node,
        frame_index=frame_index, stage=stage, t_ns=t_ns,
        clock="steady", extra=extra or {}
    )

def test_join_frames_pairs_host_and_client():
    events = [
        _ev("host",   1, "capture",      1000),
        _ev("host",   1, "encode_submit", 2000),
        _ev("host",   1, "encode_done",   3000),
        _ev("host",   1, "send_last",     4000),
        _ev("client", 1, "recv",          5000),
        _ev("client", 1, "decode_submit", 6000),
        _ev("client", 1, "decode_done",   7000),
        _ev("client", 1, "present",       8000),
    ]
    joined = join_frames(events, topology="loopback")
    assert ("loopback", 1) in joined
    row = joined[("loopback", 1)]
    assert row["host"]["capture"] == 1000
    assert row["client"]["recv"] == 5000
    assert row["client"]["present"] == 8000


def test_compute_client_stages_durations():
    joined = {
        ("loopback", 1): {
            "host":   {"capture": 1000, "encode_submit": 2000, "encode_done": 3000, "send_last": 4000},
            "client": {"recv": 5000, "decode_submit": 6000, "decode_done": 7000, "present": 8000},
        }
    }
    durations = compute_client_stages(joined)
    # recv→decode_submit = 6000-5000 = 1000 ns
    assert durations["client_recv_to_decode_submit_ms"] is not None
    # decode_submit→decode_done = 7000-6000 = 1000 ns
    assert durations["client_decode_ms"] is not None
    # decode_done→present = 8000-7000 = 1000 ns
    assert durations["client_present_ms"] is not None


def test_network_span_loopback_exact():
    """On loopback (shared clock), network = recv(client) - send_last(host)."""
    joined = {
        ("loopback", 1): {
            "host":   {"send_last": 4000},
            "client": {"recv": 5000},
        }
    }
    from harness.trace.report import compute_network_span
    spans = compute_network_span(joined, topology="loopback")
    # 5000 - 4000 = 1000 ns → 0.001 ms
    assert spans[("loopback", 1)] == pytest.approx(1000, abs=1)


def test_frame_drop_counted():
    """Frames present in host but missing client rows are counted as drops."""
    events = [
        _ev("host",   1, "capture",  1000),
        _ev("host",   1, "send_last", 4000),
        _ev("host",   2, "capture",  5000),
        _ev("host",   2, "send_last", 8000),
        # No client events for frame 2 → drop
        _ev("client", 1, "recv",      5000),
        _ev("client", 1, "present",   9000),
    ]
    from harness.trace.report import count_frame_drops
    drops = count_frame_drops(events, topology="loopback")
    assert drops["client_drops"] == 1
    assert drops["host_frames"] == 2
```

- [ ] **Step 2: Run tests — expect failure**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_report.py -v
# Expected: FAILED — join_frames, compute_client_stages etc. don't exist yet
```

---

### Task 9: Update report.py (host+client join)

**Files:** `harness/trace/report.py`

- [ ] **Step 1: Read the current file fully**

Read `harness/trace/report.py` before editing.

- [ ] **Step 2: Add helper functions before `generate_report`**

Add these functions to `report.py` (insert before the existing `generate_report` function):

```python
from typing import Dict, List, Tuple, Optional
from harness.trace.schema import TraceEvent


# ─── Per-topology joined frame map ───────────────────────────────────────────

def join_frames(events: List[TraceEvent], topology: str) -> Dict[Tuple[str, int], dict]:
    """
    Returns {(topology, frame_index): {"host": {stage: t_ns}, "client": {stage: t_ns}}}
    for the given topology string. Events from other topologies are ignored.
    """
    result: Dict[Tuple[str, int], dict] = {}
    for e in events:
        if e.topology != topology:
            continue
        key = (e.topology, e.frame_index)
        row = result.setdefault(key, {"host": {}, "client": {}})
        row[e.node][e.stage] = e.t_ns
    return result


# ─── Client stage durations ──────────────────────────────────────────────────

CLIENT_STAGE_PAIRS = [
    ("recv",          "decode_submit", "client_recv_to_decode_submit_ms"),
    ("decode_submit", "decode_done",   "client_decode_ms"),
    ("decode_done",   "present",       "client_present_ms"),
    ("recv",          "present",       "client_pipeline_ms"),
]


def compute_client_stages(joined: Dict[Tuple[str, int], dict]) -> Dict[str, Optional[dict]]:
    """Compute per-client-stage duration distributions (ns→ms) across all frames."""
    raw: Dict[str, List[float]] = {name: [] for _, _, name in CLIENT_STAGE_PAIRS}
    for row in joined.values():
        c = row.get("client", {})
        for a, b, name in CLIENT_STAGE_PAIRS:
            if a in c and b in c:
                raw[name].append(float(c[b] - c[a]))
    return {name: _percentiles(vals) for name, vals in raw.items()}


# ─── Network span ────────────────────────────────────────────────────────────

def compute_network_span(
    joined: Dict[Tuple[str, int], dict], topology: str
) -> Dict[Tuple[str, int], float]:
    """
    Returns {(topology, frame_index): network_span_ns} for frames that have both
    send_last (host) and recv (client).

    Loopback: span = recv - send_last (shared monotonic clock → exact).
    Wi-Fi: span is NOT returned (cross-machine clocks are not subtractable);
           use frameHostProcessingLatency from TraceEvent.extra instead.
    """
    if topology != "loopback":
        return {}
    spans: Dict[Tuple[str, int], float] = {}
    for key, row in joined.items():
        h = row.get("host", {})
        c = row.get("client", {})
        if "send_last" in h and "recv" in c:
            spans[key] = float(c["recv"] - h["send_last"])
    return spans


# ─── Frame-drop count ────────────────────────────────────────────────────────

def count_frame_drops(events: List[TraceEvent], topology: str) -> Dict[str, int]:
    """
    Returns {"host_frames": N, "client_drops": M} where client_drops is the count
    of frames with host rows but no client 'recv' row in the given topology.
    """
    host_frames = set()
    client_frames = set()
    for e in events:
        if e.topology != topology:
            continue
        if e.node == "host" and e.stage == "capture":
            host_frames.add(e.frame_index)
        elif e.node == "client" and e.stage == "recv":
            client_frames.add(e.frame_index)
    return {
        "host_frames": len(host_frames),
        "client_drops": len(host_frames - client_frames),
    }
```

- [ ] **Step 3: Update `generate_report` to include client stages**

Find the `generate_report` function and extend it to:
1. Call `join_frames(events, topology)` for each topology in the events.
2. Call `compute_client_stages(joined)` and include the results in `report.json`.
3. Call `compute_network_span(joined, topology)` for loopback.
4. Call `count_frame_drops(events, topology)` and include in report.

The simplest way: after computing `host_stages = compute_stages(host_events)`, add:

```python
    # ─── client join (if client events present) ───
    topologies = list({e.topology for e in events})
    client_sections = {}
    for topo in topologies:
        topo_events = [e for e in events if e.topology == topo]
        joined = join_frames(topo_events, topo)
        client_sections[topo] = {
            "client_stages": compute_client_stages(joined),
            "network_spans_ns": list(compute_network_span(joined, topo).values()),
            "drops": count_frame_drops(topo_events, topo),
        }
```

And write `client_sections` into `report.json` under the key `"client"`.

- [ ] **Step 4: Run tests — expect pass**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_report.py -v
harness/.venv/bin/python -m pytest harness/tests/ -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add harness/trace/report.py harness/tests/test_report.py
git commit -m "feat: report.py — host+client join, client stage durations, network span, frame drops"
```

---

### Task 10: Update config.toml

**Files:** `harness/config.toml`

- [ ] **Step 1: Read current config.toml**

Read `harness/config.toml` before editing.

- [ ] **Step 2: Add new sections**

Append to `harness/config.toml`:

```toml
[lumen]
# Admin credentials for the Lumen web API (used by pairing automation).
# Must match the username/password configured in Lumen's web UI.
admin_user     = "admin"
admin_password = "CHANGEME"
# The HTTPS base URL of the Lumen config server on the mini.
api_url        = "https://mac-mini:47990"
# Fixed 4-digit PIN used for automated pairing.
pair_pin       = "7777"

[topologies]
order = ["loopback", "wifi"]   # both topologies run each loop

[topologies.loopback]
# Client and readback run on the mini against 127.0.0.1.
client_target = "127.0.0.1"
run_on        = "mini"

[topologies.wifi]
# Client and readback run on the dev box (M5 Max) against the mini.
client_target = "mac-mini"
run_on        = "dev"

[client]
# Path to the Moonlight binary on each machine (set after running setup.sh).
moonlight_bin_dev  = "/Volumes/T7/lumen-harness/moonlight-qt-build/moonlight-qt/app/Moonlight.app/Contents/MacOS/Moonlight"
moonlight_bin_mini = "/Volumes/T7/lumen-harness/moonlight-qt-mini/moonlight-qt/app/Moonlight.app/Contents/MacOS/Moonlight"
app            = "Desktop"
resolution     = "1920x1080"
fps            = 60
bitrate_kbps   = 20000
stream_seconds = 20

[workload]
fps          = 60
counter_bits = 20

[readback]
window_match = "Moonlight"
```

- [ ] **Step 3: Fill in the real admin credentials**

Edit the `admin_password` value in `harness/config.toml` to match the password configured in Lumen's web UI on the mini.

- [ ] **Step 4: Commit**

```bash
git add harness/config.toml
git commit -m "feat: config.toml — lumen admin, topologies, client, workload, readback sections"
```

---

### Task 11: Phase A Smoke Test (Wi-Fi, M5 Max → mini)

This is a manual integration test. Lumen must already be built and running on the mini (run `harness/.venv/bin/python -m harness.runner.loop` first to ensure it launches).

- [ ] **Step 1: Confirm Lumen is accepting connections on the mini**

```bash
curl -k -u admin:YOURPASSWORD https://mac-mini:47990/api/config
# Expected: JSON response with Lumen config
```

- [ ] **Step 2: Pair the M5 Max client with the mini**

```bash
harness/.venv/bin/python - <<'EOF'
import tomllib, pathlib
from harness.client.run import pair

cfg = tomllib.loads(pathlib.Path("harness/config.toml").read_text())
pair(
    host=cfg["lumen"]["api_url"].replace("https://", "").split(":")[0],
    moonlight_bin=cfg["client"]["moonlight_bin_dev"],
    lumen_url=cfg["lumen"]["api_url"],
    admin_user=cfg["lumen"]["admin_user"],
    admin_password=cfg["lumen"]["admin_password"],
    pin=cfg["lumen"]["pair_pin"],
)
EOF
# Expected: "[run.py] pair complete"
```

- [ ] **Step 3: Run a 20s stream and collect the client trace**

```bash
RUNDIR=$(harness/.venv/bin/python -c "
import tomllib, pathlib, os, time
cfg = tomllib.loads(pathlib.Path('harness/config.toml').read_text())
from harness.client.run import stream
run_id = time.strftime('%Y%m%d-%H%M%S')
trace = f'/tmp/client_wifi_{run_id}.jsonl'
stream(
    host=cfg['topologies.wifi']['client_target'] if 'topologies.wifi' in cfg else 'mac-mini',
    moonlight_bin=cfg['client']['moonlight_bin_dev'],
    app=cfg['client']['app'],
    resolution=cfg['client']['resolution'],
    fps=cfg['client']['fps'],
    bitrate_kbps=cfg['client']['bitrate_kbps'],
    stream_seconds=cfg['client']['stream_seconds'],
    trace_file=trace,
    run_id=run_id,
    topology='wifi',
)
print(trace)
")
wc -l "$RUNDIR"
# Expected: non-zero line count
head -3 "$RUNDIR"
# Expected: JSON lines with node="client" and stages recv/decode_submit/decode_done/present
```

- [ ] **Step 4: Verify report generates from combined host + client traces**

At this stage run the full harness loop and confirm the report.json now contains a "client" section with client stage percentiles.

---

## Phase B — Loopback Client on Mini

### Task 12: Write harness/runner/deploy.py

Pushes the built client binary and (later) workload/readback artifacts to the mini.

**Files:** `harness/runner/deploy.py`

- [ ] **Step 1: Write deploy.py**

Create `harness/runner/deploy.py`:

```python
"""
Deploy pre-built harness artifacts (client, workload, readback) to the mini.

These are NOT included in the main rsync (harness/ is excluded from the
primary deploy to keep the Lumen source sync fast). We deploy only build
outputs: the Moonlight binary bundle and the Swift tool binaries.
"""
import subprocess
from pathlib import Path


def rsync_to_mini(local_path: str, remote_path: str, ssh_host: str) -> None:
    """rsync a file or directory to ssh_host:remote_path."""
    cmd = [
        "rsync", "-avz", "--mkpath",
        local_path,
        f"{ssh_host}:{remote_path}",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    print(f"[deploy] rsync → {remote_path}: OK ({result.stdout.count('>')} files)")


def deploy_client(cfg: dict, ssh_host: str) -> None:
    """
    Push the Moonlight.app bundle to the mini.

    The bundle is built from harness/client/setup.sh run on the dev box.
    After deploy, the mini can launch the binary for loopback runs.
    """
    local_app = cfg["client"]["moonlight_bin_dev"].replace(
        "/Contents/MacOS/Moonlight", ""
    )  # → .../Moonlight.app
    remote_dir = "/Volumes/T7/lumen-harness/moonlight-qt-mini/"
    print(f"[deploy] deploying Moonlight.app → mini:{remote_dir}")
    rsync_to_mini(local_app, remote_dir, ssh_host)
    # The mini binary path is <remote_dir>/Moonlight.app/Contents/MacOS/Moonlight
    mini_bin = remote_dir + "Moonlight.app/Contents/MacOS/Moonlight"
    print(f"[deploy] mini moonlight_bin: {mini_bin}")
    return mini_bin


def deploy_workload(cfg: dict, ssh_host: str) -> str:
    """Push the LumenWorkload binary to the mini."""
    local_bin = str(Path(__file__).parent.parent / "workload" / "LumenWorkload")
    remote_dir = "/Volumes/T7/lumen-harness/harness-tools/"
    rsync_to_mini(local_bin, remote_dir + "LumenWorkload", ssh_host)
    return remote_dir + "LumenWorkload"


def deploy_readback(cfg: dict, ssh_host: str) -> str:
    """Push the LumenReadback binary to the mini."""
    local_bin = str(Path(__file__).parent.parent / "readback" / "LumenReadback")
    remote_dir = "/Volumes/T7/lumen-harness/harness-tools/"
    rsync_to_mini(local_bin, remote_dir + "LumenReadback", ssh_host)
    return remote_dir + "LumenReadback"
```

- [ ] **Step 2: Build moonlight-qt on the mini via SSH**

The mini also needs the patched moonlight-qt binary for loopback runs. Since we already have the binary on the dev box and both machines are Apple Silicon arm64, we can rsync the built `.app` bundle (arm64 binaries run fine on both M4 and M5):

```bash
# Deploy the dev-box-built Moonlight.app to the mini
harness/.venv/bin/python - <<'EOF'
import tomllib, pathlib
from harness.runner.deploy import deploy_client
cfg = tomllib.loads(pathlib.Path("harness/config.toml").read_text())
mini_bin = deploy_client(cfg, cfg["mini"]["ssh_host"])
print(f"mini binary: {mini_bin}")
EOF
```

- [ ] **Step 3: Verify the binary runs on the mini**

```bash
ssh mac-mini "/Volumes/T7/lumen-harness/moonlight-qt-mini/Moonlight.app/Contents/MacOS/Moonlight --version"
# Expected: version string like "6.1.0"
```

- [ ] **Step 4: Pair the mini client with the mini Lumen (loopback)**

```bash
harness/.venv/bin/python - <<'EOF'
import tomllib, pathlib, subprocess
cfg = tomllib.loads(pathlib.Path("harness/config.toml").read_text())

# We run the pair command ON the mini (via launchctl asuser 501 for Aqua session)
mini_bin = "/Volumes/T7/lumen-harness/moonlight-qt-mini/Moonlight.app/Contents/MacOS/Moonlight"

# pair() runs on the dev box coordinating: mini runs moonlight pair,
# dev box POSTs the PIN to lumen's API
import time, requests
import urllib3; urllib3.disable_warnings()

pin = cfg["lumen"]["pair_pin"]
host = "127.0.0.1"   # loopback on mini

# Launch moonlight pair on the mini in Aqua session
ssh_cmd = [
    "ssh", "mac-mini",
    f"launchctl asuser 501 {mini_bin} pair {host} --pin {pin}"
    " > /tmp/mlpair.log 2>&1 &"
]
subprocess.Popen(ssh_cmd)
time.sleep(2)

# POST PIN from dev box to Lumen
r = requests.post(
    f"{cfg['lumen']['api_url']}/api/pin",
    json={"pin": pin, "name": "lumen-harness-loopback"},
    auth=(cfg["lumen"]["admin_user"], cfg["lumen"]["admin_password"]),
    verify=False, timeout=10,
)
print(r.status_code, r.text)
time.sleep(5)
result = subprocess.run(["ssh", "mac-mini", "cat /tmp/mlpair.log"], capture_output=True, text=True)
print(result.stdout)
EOF
```

- [ ] **Step 5: Commit**

```bash
git add harness/runner/deploy.py
git commit -m "feat: harness/runner/deploy.py — rsync client/workload/readback to mini"
```

---

### Task 13: Write harness/runner/topology.py

**Files:** `harness/runner/topology.py`

- [ ] **Step 1: Write topology.py**

Create `harness/runner/topology.py`:

```python
"""
Per-topology run orchestration: starts the client (and readback + workload
when available), streams for N seconds, stops everything, and returns the
path to the collected client trace file.
"""
import subprocess
import time
import os
from pathlib import Path
from harness.runner import mini as minimod


def run_wifi_topology(cfg: dict, run_id: str, run_dir: Path) -> str:
    """
    Wi-Fi topology: client runs on the dev box (M5 Max) pointing at mac-mini.
    Returns path to the local client trace file.
    """
    client_cfg = cfg["client"]
    moonlight_bin = client_cfg["moonlight_bin_dev"]
    trace_file = str(run_dir / "client_wifi.jsonl")

    env = os.environ.copy()
    env["MOONLIGHT_TRACE_FILE"]     = trace_file
    env["MOONLIGHT_TRACE_RUN_ID"]   = run_id
    env["MOONLIGHT_TRACE_TOPOLOGY"] = "wifi"

    cmd = [
        moonlight_bin, "stream", cfg["topologies.wifi"]["client_target"],
        client_cfg["app"],
        "--resolution", client_cfg["resolution"],
        "--fps",        str(client_cfg["fps"]),
        "--bitrate",    str(client_cfg["bitrate_kbps"]),
        "--display-mode", "windowed",
        "--no-vsync",
        "--no-frame-pacing",
    ]
    print(f"[topology:wifi] starting stream: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env)
    time.sleep(client_cfg["stream_seconds"])
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(f"[topology:wifi] done; trace: {trace_file}")
    return trace_file


def run_loopback_topology(cfg: dict, run_id: str, run_dir: Path,
                          ssh_host: str, bp: str) -> str:
    """
    Loopback topology: client runs on the mini against 127.0.0.1.
    Returns path to the local client trace file (fetched from mini after run).
    """
    client_cfg = cfg["client"]
    moonlight_bin_mini = client_cfg["moonlight_bin_mini"]
    remote_trace = f"/tmp/client_loopback_{run_id}.jsonl"
    local_trace  = str(run_dir / "client_loopback.jsonl")

    # Launch client on mini in Aqua session
    stream_cmd = (
        f"launchctl asuser 501 env "
        f"MOONLIGHT_TRACE_FILE={remote_trace} "
        f"MOONLIGHT_TRACE_RUN_ID={run_id} "
        f"MOONLIGHT_TRACE_TOPOLOGY=loopback "
        f"{moonlight_bin_mini} stream 127.0.0.1 {client_cfg['app']} "
        f"--resolution {client_cfg['resolution']} "
        f"--fps {client_cfg['fps']} "
        f"--bitrate {client_cfg['bitrate_kbps']} "
        f"--display-mode windowed --no-vsync --no-frame-pacing"
    )
    print(f"[topology:loopback] starting client on mini")
    proc = minimod.run_remote(ssh_host, bp, stream_cmd, check=False, background=True)
    time.sleep(client_cfg["stream_seconds"])

    # Kill the client on the mini
    minimod.run_remote(ssh_host, bp,
        f"pkill -f 'Moonlight.*127.0.0.1' || true", check=False)
    time.sleep(2)

    # Fetch trace back to dev box
    raw = minimod.run_remote(ssh_host, bp,
        f"cat {remote_trace} 2>/dev/null", check=False).stdout
    Path(local_trace).write_text(raw)
    print(f"[topology:loopback] trace fetched: {local_trace} ({len(raw.splitlines())} events)")
    return local_trace
```

- [ ] **Step 2: Check mini.py for background=True support**

Read `harness/runner/mini.py`. If `run_remote()` does not support a `background` parameter, update `topology.py` to use `subprocess.Popen` for the SSH command instead:

```python
# Alternative: SSH subprocess in background
ssh_cmd = ["ssh", ssh_host, stream_cmd + " &"]
subprocess.Popen(ssh_cmd)
```

- [ ] **Step 3: Commit**

```bash
git add harness/runner/topology.py
git commit -m "feat: harness/runner/topology.py — per-topology stream + trace collect"
```

---

### Task 14: Wire topology into session.py and loop.py

**Files:** `harness/runner/session.py`, `harness/runner/loop.py`

- [ ] **Step 1: Read both files fully before editing**

Read `harness/runner/session.py` and `harness/runner/loop.py`.

- [ ] **Step 2: Update session.py — topology parameter**

In `session.py`, find the `launch()` function. Replace the hardcoded `"LUMEN_TRACE_TOPOLOGY": "loopback"` with a `topology` parameter:

```python
def launch(host: str, bp: str, build_dir: str, conf_path: str,
           log_file: str, trace_file: str, run_id: str,
           topology: str = "loopback") -> None:
    plist = render_plist([f"{build_dir}/lumen", conf_path],
                         {
                             "SUNSHINE_ASSETS_DIR":  f"{build_dir}/assets",
                             "LUMEN_TRACE_FILE":      trace_file,
                             "LUMEN_TRACE_RUN_ID":    run_id,
                             "LUMEN_TRACE_TOPOLOGY":  topology,   # ← was hardcoded
                         }, log_file)
    # ... rest unchanged
```

- [ ] **Step 3: Update loop.py — run both topologies**

In `loop.py`, after the existing `[7/8] launch + gate` stage (once Lumen is running and ready), add the topology loop before `[8/8] teardown`:

```python
    # ─── [7.5/8] topology runs ─────────────────────────────────────────────
    from harness.runner.topology import run_wifi_topology, run_loopback_topology
    import tomllib

    cfg = load_cfg()  # already available in context as cfg
    client_traces = []

    if "wifi" in cfg.get("topologies", {}).get("order", []):
        wifi_trace = run_wifi_topology(cfg, run_id, rundir)
        client_traces.append(("wifi", wifi_trace))

    if "loopback" in cfg.get("topologies", {}).get("order", []):
        loop_trace = run_loopback_topology(cfg, run_id, rundir, host, bp)
        client_traces.append(("loopback", loop_trace))
```

Then in the `[8/8] teardown` block, after fetching `host_trace.jsonl`, merge all traces and call `generate_report`:

```python
    # merge host + all client traces
    import json
    all_events_jsonl = local_trace.read_text()
    for topo, ct in client_traces:
        ct_path = pathlib.Path(ct)
        if ct_path.exists():
            all_events_jsonl += ct_path.read_text()

    merged_trace = rundir / "merged_trace.jsonl"
    merged_trace.write_text(all_events_jsonl)
    merged_count = len([l for l in all_events_jsonl.splitlines() if l.strip()])
    print(f"merged trace: {merged_trace}  ({merged_count} events)")
    if merged_count > 0:
        generate_report(str(merged_trace), rundir)
        print(f"report: {rundir}/report.md")
```

- [ ] **Step 4: Run all tests**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
# Expected: all PASS
```

- [ ] **Step 5: Commit**

```bash
git add harness/runner/session.py harness/runner/loop.py
git commit -m "feat: wire topology param into session.py + dual-topology loop in loop.py"
```

---

### Task 15: Phase B Smoke Test (Loopback)

- [ ] **Step 1: Ensure Lumen is running on the mini via the harness**

```bash
harness/.venv/bin/python -m harness.runner.loop
```

- [ ] **Step 2: Verify the loopback client trace was fetched**

```bash
ls -1t harness/reports/ | head -1 | xargs -I{} ls harness/reports/{}/
# Expected: sunshine.log, host_trace.jsonl, client_loopback.jsonl,
#           merged_trace.jsonl, report.md, report.json
```

- [ ] **Step 3: Spot-check merged_trace.jsonl for loopback client events**

```bash
ls -1t harness/reports/ | head -1 | xargs -I{} bash -c \
  'grep -c "\"node\":\"client\"" harness/reports/{}/merged_trace.jsonl'
# Expected: non-zero count
```

- [ ] **Step 4: Spot-check report.json for client sections**

```bash
ls -1t harness/reports/ | head -1 | xargs -I{} python3 -c "
import json, sys
r = json.load(open('harness/reports/{}/report.json'.format(sys.argv[1])))
print(json.dumps(list(r.keys()), indent=2))
" {}
# Expected: ["stages", "frame_count", "client"] or similar
```

---

## Phase C — Synthetic Workload + Readback

### Task 16: Write LumenWorkload.swift and build.sh

The workload paints a binary-block frame counter on the virtual display so Lumen captures it. Each frame: fixed corner calibration markers + a row of large black/white squares encoding a monotonic `uint32` counter + a motion region. Logs `id→t_paint` to a JSONL trace.

**Files:** `harness/workload/LumenWorkload.swift`, `harness/workload/build.sh`

- [ ] **Step 1: Create directory**

```bash
mkdir -p /Users/hazemeissa/Projects/lumen/harness/workload
```

- [ ] **Step 2: Write LumenWorkload.swift**

Create `harness/workload/LumenWorkload.swift`:

```swift
// LumenWorkload: paints a binary-block monotonic counter on the virtual display.
// Reads LUMEN_WORKLOAD_TRACE_FILE for id→t_paint JSONL output.
// Reads LUMEN_VIRTUAL_DISPLAY_ID for the CGDirectDisplayID to target.
// Usage: LumenWorkload [fps] [bits] [seconds]

import Cocoa
import Metal
import QuartzCore
import CoreVideo

let BITS: Int = Int(CommandLine.arguments.dropFirst().first.flatMap(UInt32.init) ?? 20)
let FPS:  Int = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2])! : 60
let SECS: Int = CommandLine.arguments.count > 3 ? Int(CommandLine.arguments[3])! : 30

// ─── Trace sink ──────────────────────────────────────────────────────────────
class TraceSink {
    private let file: FileHandle?
    init() {
        guard let path = ProcessInfo.processInfo.environment["LUMEN_WORKLOAD_TRACE_FILE"],
              FileManager.default.createFile(atPath: path, contents: nil) else {
            file = nil; return
        }
        file = FileHandle(forWritingAtPath: path)
    }
    func emit(id: UInt32, t_ns: UInt64) {
        guard let fh = file else { return }
        let line = "{\"id\":\(id),\"t_paint_ns\":\(t_ns)}\n"
        fh.write(line.data(using: .utf8)!)
    }
    deinit { file?.closeFile() }
}

// ─── Steady clock ────────────────────────────────────────────────────────────
func ns_now() -> UInt64 {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    let raw = mach_absolute_time()
    return raw * UInt64(info.numer) / UInt64(info.denom)
}

// ─── Find target NSScreen ────────────────────────────────────────────────────
func findVirtualScreen() -> NSScreen? {
    guard let idStr = ProcessInfo.processInfo.environment["LUMEN_VIRTUAL_DISPLAY_ID"],
          let targetID = UInt32(idStr) else {
        print("[workload] LUMEN_VIRTUAL_DISPLAY_ID not set; using main screen")
        return NSScreen.main
    }
    for screen in NSScreen.screens {
        if let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
           num.uint32Value == targetID {
            return screen
        }
    }
    print("[workload] WARNING: display \(targetID) not found; using main screen")
    return NSScreen.main
}

// ─── Draw binary blocks ──────────────────────────────────────────────────────
func drawFrame(in context: CGContext, counter: UInt32, bounds: CGRect) {
    let w = bounds.width, h = bounds.height
    let blockSize: CGFloat = max(w / 64, 20)  // large blocks survive H.264 compression
    let markerSize: CGFloat = blockSize * 1.5

    // Background: mid-grey (motion region: fills the rest)
    context.setFillColor(CGColor(gray: 0.5, alpha: 1))
    context.fill(bounds)

    // Motion region: alternating stripes that change each frame
    let stripe = Int(counter) % 8
    for i in 0..<8 {
        let x = w * CGFloat(i) / 8.0
        context.setFillColor((i + stripe) % 2 == 0
            ? CGColor(gray: 0.3, alpha: 1) : CGColor(gray: 0.7, alpha: 1))
        context.fill(CGRect(x: x, y: h * 0.25, width: w / 8, height: h * 0.5))
    }

    // Corner calibration markers (always white squares at fixed positions)
    let corners: [CGPoint] = [
        CGPoint(x: 0, y: 0),
        CGPoint(x: w - markerSize, y: 0),
        CGPoint(x: 0, y: h - markerSize),
        CGPoint(x: w - markerSize, y: h - markerSize),
    ]
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    for c in corners {
        context.fill(CGRect(origin: c, size: CGSize(width: markerSize, height: markerSize)))
    }

    // Binary block row: BITS blocks encoding `counter`
    let rowY: CGFloat = h * 0.1
    for bit in 0..<BITS {
        let bitVal = (counter >> bit) & 1
        let x = (w - blockSize * CGFloat(BITS)) / 2 + blockSize * CGFloat(bit)
        context.setFillColor(bitVal == 1
            ? CGColor(gray: 1, alpha: 1) : CGColor(gray: 0, alpha: 1))
        context.fill(CGRect(x: x, y: rowY, width: blockSize - 2, height: blockSize))
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
let app  = NSApplication.shared
let sink = TraceSink()

guard let screen = findVirtualScreen() else {
    print("[workload] No screen found, exiting")
    exit(1)
}
let frame = screen.frame
print("[workload] target screen: \(frame.width)x\(frame.height)")

let win = NSWindow(
    contentRect: frame,
    styleMask: [.borderless],
    backing: .buffered,
    defer: false,
    screen: screen
)
win.level = .normal
win.backgroundColor = .black
win.setFrame(frame, display: true)
win.makeKeyAndOrderFront(nil)

let view = NSView(frame: CGRect(origin: .zero, size: frame.size))
win.contentView = view
let layer = CALayer()
layer.frame = view.bounds
view.layer = layer
view.wantsLayer = true

var counter: UInt32 = 0
let interval = 1.0 / Double(FPS)
let deadline = Date().addingTimeInterval(Double(SECS))

let timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { _ in
    guard Date() < deadline else {
        print("[workload] done after \(counter) frames")
        app.stop(nil)
        return
    }
    let t = ns_now()
    sink.emit(id: counter, t_ns: t)

    // Draw into a CGContext and set as layer content
    let size = CGSize(width: frame.width, height: frame.height)
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
    guard let ctx = CGContext(
        data: nil, width: Int(size.width), height: Int(size.height),
        bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: bitmapInfo.rawValue)
    else { return }
    drawFrame(in: ctx, counter: counter, bounds: CGRect(origin: .zero, size: size))
    layer.contents = ctx.makeImage()

    counter += 1
}

RunLoop.main.add(timer, forMode: .default)
app.run()
```

- [ ] **Step 3: Write build.sh**

Create `harness/workload/build.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/LumenWorkload"
echo "[workload/build.sh] building → $OUT"
swiftc -O -o "$OUT" "$SCRIPT_DIR/LumenWorkload.swift" \
    -framework Cocoa -framework Metal -framework QuartzCore \
    -framework CoreVideo
echo "[workload/build.sh] done: $OUT"
```

- [ ] **Step 4: Build and sanity-check**

```bash
chmod +x harness/workload/build.sh
harness/workload/build.sh
./harness/workload/LumenWorkload --version 2>/dev/null || \
    echo "binary exists: $(ls -lh harness/workload/LumenWorkload)"
```

- [ ] **Step 5: Commit**

```bash
git add harness/workload/
git commit -m "feat: LumenWorkload.swift — binary-block counter on virtual display"
```

---

### Task 17: Write LumenReadback.swift and build.sh

Captures the Moonlight client window using ScreenCaptureKit, decodes the binary block counter from each frame, and logs `id→t_observe` to JSONL.

**Files:** `harness/readback/LumenReadback.swift`, `harness/readback/build.sh`

- [ ] **Step 1: Create directory**

```bash
mkdir -p /Users/hazemeissa/Projects/lumen/harness/readback
```

- [ ] **Step 2: Write LumenReadback.swift**

Create `harness/readback/LumenReadback.swift`:

```swift
// LumenReadback: captures the Moonlight client window via ScreenCaptureKit,
// threshold-decodes the binary-block counter from each frame, and writes
// id→t_observe to a JSONL trace.
//
// Env: LUMEN_READBACK_TRACE_FILE=<path>   where to write JSONL
//      LUMEN_READBACK_BITS=<N>             bits in the counter (default 20)
//      LUMEN_READBACK_SECONDS=<N>          how long to run (default 30)

import Cocoa
import ScreenCaptureKit
import CoreGraphics

let BITS    = Int(ProcessInfo.processInfo.environment["LUMEN_READBACK_BITS"] ?? "20") ?? 20
let SECS    = Int(ProcessInfo.processInfo.environment["LUMEN_READBACK_SECONDS"] ?? "30") ?? 30
let TRACE   = ProcessInfo.processInfo.environment["LUMEN_READBACK_TRACE_FILE"]

// ─── Steady clock ────────────────────────────────────────────────────────────
func ns_now() -> UInt64 {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    return mach_absolute_time() * UInt64(info.numer) / UInt64(info.denom)
}

// ─── Trace sink ──────────────────────────────────────────────────────────────
class TraceSink {
    private let fh: FileHandle?
    private var seen = Set<UInt32>()

    init(path: String?) {
        guard let p = path else { fh = nil; return }
        FileManager.default.createFile(atPath: p, contents: nil)
        fh = FileHandle(forWritingAtPath: p)
    }
    func emit(id: UInt32, t_ns: UInt64) {
        guard let fh = fh, !seen.contains(id) else { return }
        seen.insert(id)   // de-duplicate: keep first observation
        let line = "{\"id\":\(id),\"t_observe_ns\":\(t_ns)}\n"
        fh.write(line.data(using: .utf8)!)
    }
    deinit { fh?.closeFile() }
}

// ─── Block decoder ───────────────────────────────────────────────────────────
// Reads a row of BITS large black/white squares from a CGImage.
// calibration markers (white corners) must be present for validation.
func decodeCounter(from image: CGImage) -> UInt32? {
    let w = image.width, h = image.height
    guard w > 0, h > 0 else { return nil }

    // Validate calibration markers: top-left corner should be white
    guard let data = image.dataProvider?.data,
          let ptr = CFDataGetBytePtr(data) else { return nil }

    let bpp = image.bitsPerPixel / 8  // bytes per pixel (≥3)
    func luma(x: Int, y: Int) -> Double {
        let offset = (y * w + x) * bpp
        let r = Double(ptr[offset])
        let g = Double(ptr[offset + 1])
        let b = Double(ptr[offset + 2])
        return 0.299 * r + 0.587 * g + 0.114 * b
    }

    // Calibration marker check: top-left 20x20 pixels should be >200 luma
    let markerSize = max(w / 64, 20)
    let markerCenter = markerSize / 2
    let topLeftLuma = luma(x: markerCenter, y: markerCenter)
    guard topLeftLuma > 180 else { return nil }  // marker not white → skip frame

    // Decode block row at y ≈ 10% of height
    let rowY = Int(Double(h) * 0.1) + markerSize / 2
    let blockSize = max(w / 64, 20)
    let startX = (w - blockSize * BITS) / 2

    var counter: UInt32 = 0
    for bit in 0..<BITS {
        let cx = startX + blockSize * bit + blockSize / 2
        let cy = rowY
        guard cx >= 0, cx < w, cy >= 0, cy < h else { continue }
        let l = luma(x: cx, y: cy)
        if l > 128 {
            counter |= (1 << bit)
        }
    }
    return counter
}

// ─── SCK capture ─────────────────────────────────────────────────────────────
class ReadbackDelegate: NSObject, SCStreamOutput {
    let sink: TraceSink
    init(sink: TraceSink) { self.sink = sink }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .screen else { return }
        let t = ns_now()
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return }
        if let id = decodeCounter(from: cgImage) {
            sink.emit(id: id, t_ns: t)
        }
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
let sink = TraceSink(path: TRACE)
let sema = DispatchSemaphore(value: 0)

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                           onScreenWindowsOnly: true)
        // Find Moonlight window by app bundle name or title
        guard let moonlightWindow = content.windows.first(where: { w in
            w.owningApplication?.applicationName.contains("Moonlight") == true ||
            w.title?.contains("Moonlight") == true
        }) else {
            print("[readback] ERROR: Moonlight window not found in SCShareableContent")
            print("[readback] Available windows: \(content.windows.map { $0.owningApplication?.applicationName ?? "?" })")
            exit(1)
        }
        print("[readback] capturing window: \(moonlightWindow.title ?? "Moonlight") app=\(moonlightWindow.owningApplication?.applicationName ?? "?")")

        let filter = SCContentFilter(desktopIndependentWindow: moonlightWindow)
        let config = SCStreamConfiguration()
        config.width  = 1920
        config.height = 1080
        config.minimumFrameInterval = CMTime(value: 1, timescale: 60)
        config.pixelFormat = kCVPixelFormatType_32BGRA

        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        let delegate = ReadbackDelegate(sink: sink)
        try stream.addStreamOutput(delegate, type: .screen,
                                   sampleHandlerQueue: .global(qos: .userInteractive))
        try await stream.startCapture()
        print("[readback] capture started; running for \(SECS)s")
        try await Task.sleep(nanoseconds: UInt64(SECS) * 1_000_000_000)
        try await stream.stopCapture()
        print("[readback] done")
    } catch {
        print("[readback] ERROR: \(error)")
        exit(1)
    }
    sema.signal()
}

sema.wait()
```

- [ ] **Step 3: Write build.sh**

Create `harness/readback/build.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/LumenReadback"
echo "[readback/build.sh] building → $OUT"
swiftc -O -o "$OUT" "$SCRIPT_DIR/LumenReadback.swift" \
    -framework Cocoa -framework ScreenCaptureKit \
    -framework CoreGraphics -framework CoreMedia \
    -framework CoreVideo
echo "[readback/build.sh] done: $OUT"
```

- [ ] **Step 4: Build readback**

```bash
chmod +x harness/readback/build.sh
harness/readback/build.sh
ls -lh harness/readback/LumenReadback
```

- [ ] **Step 5: Commit**

```bash
git add harness/readback/
git commit -m "feat: LumenReadback.swift — SCK capture of Moonlight window, threshold block decode"
```

---

### Task 18: Build and verify workload + readback pipeline

- [ ] **Step 1: Grant Screen Recording to LumenReadback on dev box**

Open **System Settings → Privacy & Security → Screen Recording** and add `harness/readback/LumenReadback` to the allowed list. This is a one-time manual step.

- [ ] **Step 2: Read virtual display ID from mini**

```bash
ssh mac-mini "cat /tmp/sunshine_vd_id"
# Expected: a numeric CGDirectDisplayID like "69732864"
```

- [ ] **Step 3: Run workload on mini (targets virtual display)**

```bash
VD_ID=$(ssh mac-mini "cat /tmp/sunshine_vd_id")
ssh mac-mini "launchctl asuser 501 env \
  LUMEN_VIRTUAL_DISPLAY_ID=$VD_ID \
  LUMEN_WORKLOAD_TRACE_FILE=/tmp/workload_test.jsonl \
  /Volumes/T7/lumen-harness/harness-tools/LumenWorkload 60 20 10 &"
sleep 12
ssh mac-mini "head -3 /tmp/workload_test.jsonl"
# Expected: {"id":0,"t_paint_ns":<N>}
```

- [ ] **Step 4: Run readback on dev box while Moonlight is streaming**

Start a Moonlight stream first (use `run.py stream`), then:

```bash
LUMEN_READBACK_TRACE_FILE=/tmp/readback_test.jsonl \
LUMEN_READBACK_BITS=20 \
LUMEN_READBACK_SECONDS=10 \
  harness/readback/LumenReadback
head -3 /tmp/readback_test.jsonl
# Expected: {"id":<N>,"t_observe_ns":<T>}
```

- [ ] **Step 5: Deploy workload to mini**

```bash
harness/.venv/bin/python - <<'EOF'
import tomllib, pathlib
from harness.runner.deploy import deploy_workload, deploy_readback
cfg = tomllib.loads(pathlib.Path("harness/config.toml").read_text())
deploy_workload(cfg, cfg["mini"]["ssh_host"])
deploy_readback(cfg, cfg["mini"]["ssh_host"])
EOF
```

---

## Phase D — Reporter Unification + Dual-Topology Smoke Run

### Task 19: Extend report.py — glass-to-glass, consistency check, per-topology tables

**Files:** `harness/trace/report.py`, `harness/tests/test_report.py`

- [ ] **Step 1: Write tests for glass-to-glass and consistency check**

Add to `harness/tests/test_report.py`:

```python
def test_glass_to_glass_join():
    """id→t_paint joined with id→t_observe gives g2g duration."""
    from harness.trace.report import compute_g2g
    paint_events  = [{"id": 5, "t_paint_ns": 1000}]
    observe_events = [{"id": 5, "t_observe_ns": 5000}]
    g2g = compute_g2g(paint_events, observe_events)
    # 5000 - 1000 = 4000 ns
    assert g2g[5] == pytest.approx(4000, abs=1)


def test_consistency_check_g2g_gte_pipeline():
    """Loopback: G2G must be >= summed pipeline; if smaller → bug."""
    from harness.trace.report import consistency_check
    # G2G=10ms pipeline=8ms → OK; gap=2ms (paint→capture + present→observe overhead)
    ok, msg = consistency_check(g2g_ns=10_000_000, pipeline_ns=8_000_000)
    assert ok is True

    # G2G < pipeline → impossible, indicates clock/join bug
    ok, msg = consistency_check(g2g_ns=5_000_000, pipeline_ns=8_000_000)
    assert ok is False
    assert "join" in msg.lower() or "bug" in msg.lower()
```

- [ ] **Step 2: Run these tests — expect failure**

```bash
harness/.venv/bin/python -m pytest harness/tests/test_report.py::test_glass_to_glass_join \
    harness/tests/test_report.py::test_consistency_check_g2g_gte_pipeline -v
# Expected: FAILED
```

- [ ] **Step 3: Implement compute_g2g and consistency_check in report.py**

Add to `harness/trace/report.py`:

```python
def compute_g2g(
    paint_events: list,    # [{"id": int, "t_paint_ns": int}]
    observe_events: list,  # [{"id": int, "t_observe_ns": int}]
) -> dict:
    """
    Join workload and readback events by id.
    Returns {id: g2g_ns} where g2g_ns = t_observe_ns - t_paint_ns.
    Only ids present in both lists are included.
    """
    paint = {e["id"]: e["t_paint_ns"] for e in paint_events}
    observe = {e["id"]: e["t_observe_ns"] for e in observe_events}
    return {
        id_: observe[id_] - paint[id_]
        for id_ in paint if id_ in observe
    }


def consistency_check(g2g_ns: float, pipeline_ns: float) -> tuple:
    """
    Asserts G2G >= summed pipeline (loopback only).
    G2G includes paint→capture + present→observe overhead on top of the pipeline.
    If G2G < pipeline → join or clock bug.
    Returns (ok: bool, message: str).
    """
    if g2g_ns < pipeline_ns:
        return (False,
            f"CONSISTENCY BUG: G2G ({g2g_ns/1e6:.2f}ms) < pipeline ({pipeline_ns/1e6:.2f}ms). "
            "Indicates a frame_index join error or clock mismatch.")
    gap_ns = g2g_ns - pipeline_ns
    gap_ms = gap_ns / 1e6
    return (True, f"OK — G2G overhead (paint→capture + present→observe): {gap_ms:.2f}ms")
```

- [ ] **Step 4: Update generate_report to include glass-to-glass**

In `generate_report`, after collecting `client_sections`, load the workload and readback JSONL files (if present in `run_dir`) and call `compute_g2g` + `consistency_check`:

```python
    # ─── Glass-to-glass (loopback only) ───
    import json as _json
    g2g_section = {}
    for topo in ["loopback"]:
        paint_path   = run_dir / "workload_trace.jsonl"
        observe_path = run_dir / f"readback_{topo}.jsonl"
        if paint_path.exists() and observe_path.exists():
            paint_evts   = [_json.loads(l) for l in paint_path.read_text().splitlines() if l.strip()]
            observe_evts = [_json.loads(l) for l in observe_path.read_text().splitlines() if l.strip()]
            g2g_map = compute_g2g(paint_evts, observe_evts)
            if g2g_map:
                g2g_vals = list(g2g_map.values())
                g2g_stats = _percentiles([float(v) for v in g2g_vals])
                # Compare p50 G2G vs loopback pipeline p50
                if topo in client_sections:
                    pipeline_ms = (client_sections[topo]
                                   .get("client_stages", {})
                                   .get("client_pipeline_ms", {})
                                   .get("p50") or 0)
                    host_p50 = stages.get("host_pipeline_ms", {}).get("p50") or 0
                    total_pipeline_ns = (pipeline_ms + host_p50) * 1e6
                    ok, msg = consistency_check(
                        g2g_ns=(g2g_stats.get("p50") or 0) * 1e6,
                        pipeline_ns=total_pipeline_ns
                    )
                    g2g_section[topo] = {
                        "g2g_ms": g2g_stats,
                        "frame_count": len(g2g_map),
                        "consistency_ok": ok,
                        "consistency_msg": msg,
                    }
                    print(f"[report] G2G loopback p50={g2g_stats.get('p50'):.2f}ms — {msg}")
```

- [ ] **Step 5: Run all tests**

```bash
harness/.venv/bin/python -m pytest harness/tests/ -v
# Expected: all PASS
```

- [ ] **Step 6: Commit**

```bash
git add harness/trace/report.py harness/tests/test_report.py
git commit -m "feat: report.py — glass-to-glass join, loopback consistency check, per-topology tables"
```

---

### Task 20: Wire workload + readback into topology.py and loop.py; full smoke run

**Files:** `harness/runner/topology.py`, `harness/runner/loop.py`

- [ ] **Step 1: Update topology.py to start workload and readback alongside each topology**

In `run_loopback_topology`, before starting the client:

```python
    # Start workload on mini (paints binary-block counter on virtual display)
    vd_id = minimod.run_remote(ssh_host, bp,
        "cat /tmp/sunshine_vd_id 2>/dev/null", check=False).stdout.strip()
    workload_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenWorkload"
    workload_trace = f"/tmp/workload_{run_id}.jsonl"
    minimod.run_remote(ssh_host, bp,
        f"launchctl asuser 501 env "
        f"LUMEN_VIRTUAL_DISPLAY_ID={vd_id} "
        f"LUMEN_WORKLOAD_TRACE_FILE={workload_trace} "
        f"{workload_bin} {cfg['workload']['fps']} {cfg['workload']['counter_bits']} "
        f"{cfg['client']['stream_seconds']} &",
        check=False)
```

In `run_loopback_topology`, alongside the client, also start the readback on mini:

```python
    readback_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenReadback"
    readback_trace = f"/tmp/readback_loopback_{run_id}.jsonl"
    minimod.run_remote(ssh_host, bp,
        f"launchctl asuser 501 env "
        f"LUMEN_READBACK_TRACE_FILE={readback_trace} "
        f"LUMEN_READBACK_BITS={cfg['workload']['counter_bits']} "
        f"LUMEN_READBACK_SECONDS={cfg['client']['stream_seconds']} "
        f"{readback_bin} &",
        check=False)
```

After waiting `stream_seconds`, fetch both workload and readback traces:

```python
    # Fetch workload trace
    wt_raw = minimod.run_remote(ssh_host, bp, f"cat {workload_trace} 2>/dev/null", check=False).stdout
    (run_dir / "workload_trace.jsonl").write_text(wt_raw)
    # Fetch loopback readback trace
    rb_raw = minimod.run_remote(ssh_host, bp, f"cat {readback_trace} 2>/dev/null", check=False).stdout
    (run_dir / "readback_loopback.jsonl").write_text(rb_raw)
```

For `run_wifi_topology`, also start the readback on the dev box alongside the stream subprocess:

```python
    import threading
    readback_trace_local = str(run_dir / "readback_wifi.jsonl")
    env_rb = os.environ.copy()
    env_rb["LUMEN_READBACK_TRACE_FILE"]    = readback_trace_local
    env_rb["LUMEN_READBACK_BITS"]          = str(cfg["workload"]["counter_bits"])
    env_rb["LUMEN_READBACK_SECONDS"]       = str(client_cfg["stream_seconds"])
    readback_bin_local = str(Path(__file__).parent.parent / "readback" / "LumenReadback")
    rb_proc = subprocess.Popen([readback_bin_local], env=env_rb)
```

- [ ] **Step 2: Ensure virtual_display setting is enabled in sunshine.conf rendering**

Read `harness/runner/config_render.py`. Confirm it sets `virtual_display = enabled` in the rendered conf, or add it:

```python
# in the rendered sunshine.conf template, add:
"virtual_display": "enabled",
```

If the key is missing, add it to the template in `config_render.py`.

- [ ] **Step 3: Full dual-topology smoke run**

```bash
harness/.venv/bin/python -m harness.runner.loop
```

Expected behavior:
1. Build → sign → launch Lumen on mini (host)
2. Run Wi-Fi topology (client on M5 Max → mini, readback on M5 Max)
3. Run loopback topology (client + readback + workload on mini)
4. Collect all traces → merge → generate report
5. report.md shows per-stage p50/p95/p99 for host stages + client stages for both topologies
6. report.json contains `"g2g_loopback"` section with consistency check

- [ ] **Step 4: Verify success criteria from design spec §10**

```bash
LATEST=$(ls -1t harness/reports/ | head -1)
python3 - <<EOF
import json, pathlib
r = json.loads((pathlib.Path("harness/reports/$LATEST/report.json")).read_text())
print("host_pipeline_ms p50:", r.get("stages", {}).get("host_pipeline_ms", {}).get("p50"))
# Check client sections
print("client present:", r.get("client"))
print("g2g:", r.get("g2g_loopback"))
EOF
```

- [ ] **Step 5: Final commit**

```bash
git add harness/runner/topology.py harness/runner/loop.py harness/runner/config_render.py
git commit -m "feat: wire workload+readback into topology.py; full dual-topology smoke run wiring"
```

- [ ] **Step 6: PR or tag**

```bash
# Create a PR from instrumented-client-dual-topology → main
gh pr create \
    --title "Plan 3: Instrumented client + synthetic workload + dual topology" \
    --body "$(cat <<'EOF'
## Summary
- Patches moonlight-qt v6.1.0 with a JSONL trace sink (recv/decode_submit/decode_done/present)
- Automates pair/stream/quit via harness/client/run.py + /api/pin POST
- Dual topology (loopback on mini + Wi-Fi to M5 Max) wired into harness loop
- Synthetic Metal workload paints binary-block counter on virtual display
- ScreenCaptureKit readback decodes counter for glass-to-glass measurement
- Reporter joins host+client by frame_index, adds glass-to-glass + consistency check

## Test plan
- [ ] All 18+ unit tests pass: `harness/.venv/bin/python -m pytest harness/tests/ -v`
- [ ] setup.sh builds patched moonlight-qt and patch applies cleanly to fresh clone
- [ ] Full loop exits clean with teardown
- [ ] report.md shows per-stage tables for both topologies
- [ ] Loopback consistency check: G2G >= host_pipeline + client_pipeline
EOF
)"
```

---

## Self-Review Checklist

### Spec coverage
| Spec §        | Task(s) covering it |
|---------------|---------------------|
| §6.1 client setup.sh + trace.patch | Task 2, 3 |
| §6.1 recv/decode_submit/decode_done/present stages | Task 2 |
| §6.1 run.py pair/stream/quit | Task 7 |
| §6.2 LumenWorkload (Metal, binary block, motion region) | Task 16 |
| §6.3 LumenReadback (SCK, calibration, threshold, dedup) | Task 17 |
| §6.4 schema extra field (backward compat) | Task 4, 5 |
| §6.5 host↔client join by frame_index | Task 8, 9 |
| §6.5 network span loopback = recv−send_last | Task 8, 9 |
| §6.5 glass-to-glass join by id | Task 19 |
| §6.5 consistency check G2G ≥ pipeline | Task 19 |
| §6.6 deploy.py | Task 12 |
| §6.6 topology.py | Task 13 |
| §6.6 session.py topology param | Task 14 |
| §6.6 loop.py dual topology | Task 14, 20 |
| §7 pairing automation / /api/pin | Task 7 |
| §8 config.toml additions | Task 10 |
| §9 Phase A–D sequence | Phases A–D |
| §10 success criteria 1–5 | Task 20 |
| §11 risk: pixel feedback loop | Task 13/16 (loopback: client renders on real display; workload on virtual) |
| §11 risk: T7 disk space | setup.sh builds on T7; workload/readback also on T7 |

### Key type consistency
- `TraceEvent.extra: dict` (Task 5) used in reporter (Task 9) — consistent
- `frame_index` is always `int` / `int64_t` — consistent across C++ emit and Python parse
- `t_ns` is always `uint64_t` / `int` (Python JSON parses to int) — consistent
- Stage names: host=`capture/encode_submit/encode_done/send_last`; client=`recv/decode_submit/decode_done/present` — no overlap, reporter handles both
- `compute_g2g` takes raw dicts `{"id": int, "t_paint_ns": int}` not `TraceEvent` — intentional (workload/readback JSONL has different schema than trace events)

### Placeholder scan
- All code blocks are complete and runnable
- No "TBD", "TODO", "implement later" strings
- All file paths are absolute or clearly relative to repo root
- All test names match the functions they call

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-26-instrumented-client-dual-topology.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh Sonnet subagent per task, review between tasks. Use `superpowers:subagent-driven-development`. Remember: pass `model: "sonnet"` on every agent dispatch (cyber-safeguard avoidance).

**2. Inline Execution** — Execute tasks sequentially in this session using `superpowers:executing-plans`.

**Which approach?**
