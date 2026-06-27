# Instrumented Client + Synthetic Workload + Glass-to-Glass + Dual Topology — Design Spec

- **Date:** 2026-06-26
- **Status:** Draft — awaiting user review
- **Sub-project:** Foundation (workstream ① of 5). This is the **Plan 3** design, refining sections §5.5, §5.6, §5.7, §5.8 of the foundation spec (`2026-06-26-measurement-harness-design.md`) to implementation depth.
- **Repo:** `trollzem/Lumen` (fork of LizardByte/Sunshine), `~/Projects/lumen`. Dev box: M5 Max MacBook. Host: M4 Mac mini (`ssh mac-mini`).
- **Depends on:** Plan 1 (build/deploy/launch harness, merged) and Plan 2 (per-frame host trace, merged).

---

## 1. Context & Goal

Plan 2 added a C++ JSONL trace sink (`src/trace.{h,cpp}`) that emits four per-frame host stages keyed by `frame_index` — `capture`, `encode_submit`, `encode_done`, `send_last` — and a Python reporter that computes p50/p95/p99 per stage with run-over-run deltas.

**Plan 2's execution outcome is the motivation for Plan 3:** the smoke run captured only 3 `encode_done` events because **no Moonlight client was connected**. The single-client encode path (with all four stage emits) only runs while a client actively requests frames. During the harness's 20s idle window nothing connects, so the pipeline produces no usable data.

**Goal of Plan 3:** drive real frames through the pipeline and measure the *whole* path end to end. Concretely:
1. An **instrumented Moonlight client** (moonlight-qt from source) that emits matching per-frame client stages (`recv`, `decode_submit`, `decode_done`, `present`) keyed by `DECODE_UNIT.frameNumber`, so host and client traces join on one index with no clock sync.
2. A **synthetic timestamp workload** painting a machine-readable frame-ID onto the streamed virtual display, plus a **software glass-to-glass readback** on the client that reads that ID back — an *independent* end-to-end latency truth check.
3. **Dual-topology** runs every loop: loopback on the mini (low-variance host signal, exact timing) and Wi-Fi to the M5 Max (realistic network).
4. A **reporter** that joins host + client by `frame_index` per topology, reports per-stage percentiles side by side, and validates the instrumentation against the readback.

---

## 2. Scope

**In scope:**
1. Vendor + patch moonlight-qt (per-frame client trace), built for Apple Silicon on **both** the M5 Max and the mini.
2. CLI-driven, non-interactive pair/stream/quit; pairing automated once and cached.
3. Synthetic Metal/CoreAnimation workload on the virtual display (binary-block frame counter + motion/stress region), logging `id→t_paint`.
4. ScreenCaptureKit readback tool that decodes the block from the client window, logging `id→t_observe`.
5. Dual topology (loopback on the mini; Wi-Fi to the M5 Max) wired into the harness loop.
6. Trace-schema additions for client rows; reporter extended to join host+client, report per-topology, compute network span and glass-to-glass, and run the consistency check.

**Out of scope (deferred to milestone ③):**
- Wi-Fi *absolute* glass-to-glass (requires cross-machine clock-offset estimation).
- Hardware glass-to-glass (capture card / high-speed camera).
- Custom headless client on `moonlight-common-c`; any pipeline rewrite.
- Multi-client / multi-session measurement.

---

## 3. Decisions Locked (with user, during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Plan packaging | **One plan**, all four pieces (client + workload + readback + dual topology), executed in internal phases (§9). |
| D2 | moonlight-qt vendoring | **Pinned-clone script + patch**: `harness/client/setup.sh` clones a pinned commit, applies `trace.patch`, builds. Repo stays lean; patch is small and reviewable; reproducible on both machines. |
| D3 | On-screen frame-ID codec | **High-contrast luma binary blocks** (a row of large black/white squares = a monotonic counter) + fixed corner calibration markers. Luma-only, big flat regions survive H.264/4:2:0 cleanly; no decode library; thresholded sampling. |
| D4 | What the screen carries | **Only a monotonic counter** (the painted ID). The workload logs `id→t_paint`; readback logs `id→t_observe`; the two join by ID. The screen never carries a timestamp (fewer bits, more robust). |
| D5 | Clock domain | The moonlight-qt patch uses the **identical** nanosecond steady-clock-since-boot expression as `src/trace.cpp`, so on a shared machine (loopback) host and client timestamps are directly subtractable. |

---

## 4. Architecture & Data Flow

```
┌─ M4 Mac mini (host, ssh mac-mini) ───────────────────────────────────────────┐
│  lumen (sunshine)  ── captures the virtual display, encodes, streams ──┐      │
│    ▲ host trace JSONL (capture→encode_submit→encode_done→send_last)    │      │
│  synthetic workload ── paints binary-block counter on virtual display  │      │
│    ▲ workload trace JSONL (id → t_paint)                               │      │
│                                                                        │ loopback (127.0.0.1)
│  ── LOOPBACK topology (all on the mini, ONE monotonic clock) ──        ▼      │
│  moonlight-qt(localhost) ─► client trace (recv→decode→present)                │
│  readback(SCK) ─► reads block off client window ─► id → t_observe             │
└───────────────────────────────────────────────────────────────────────│──────┘
                                                                          │ Wi-Fi
┌─ M5 Max MacBook (dev box; also the Wi-Fi client) ───────────────────────▼──────┐
│  harness/runner  ── orchestrates the whole loop (one command) ──               │
│  ── WIFI topology (mini host clock ≠ M5 Max client clock) ──                   │
│  moonlight-qt(mac-mini) ─► client trace (recv→decode→present)                  │
│  readback(SCK) ─► reads block off client window ─► id → t_observe              │
│                                                                                │
│  collect all traces ─► harness/trace/report.py ─► report.{md,json}             │
└────────────────────────────────────────────────────────────────────────────────┘
```

Each loop runs **both** topologies sequentially and merges the data. Loopback is the exact, low-variance signal; Wi-Fi is the realistic-network signal.

---

## 5. Clock & Correlation Model (the correctness crux)

Two independent joins, by two different keys:

- **Stage join — by `frame_index`.** Host sets `packet.frameIndex = frame_index()`; the wire carries it; the client exposes it as `DECODE_UNIT.frameNumber`. Host rows (`node=host`) and client rows (`node=client`) for the same `(topology, frame_index)` are stitched into one frame's timeline.
- **Glass-to-glass join — by painted `id`.** Workload `id→t_paint` and readback `id→t_observe` join independently of `frame_index` (the workload's paint counter is its own counter, not the encoder's). This measures display-to-display latency directly.

**Subtracting timestamps across nodes is only valid on a shared machine:**

| Quantity | Loopback (one machine, one clock) | Wi-Fi (two machines, two clocks) |
|---|---|---|
| Host stage durations (capture→…→send_last) | exact (host clock) | exact (host clock) |
| Client stage durations (recv→…→present) | exact (client clock) | exact (client clock) |
| Network span `send_last(host)→recv(client)` | **exact** (subtractable) | **not subtractable** — estimate via RTT/2; cross-check with `frameHostProcessingLatency` (a *duration*, clock-agnostic, already delivered host→client per Limelight.h:155) |
| Glass-to-glass `t_observe−t_paint` | **exact** (truth check) | **relative only** (liveness/jitter); absolute deferred to ③ |
| Authoritative end-to-end | summed durations **and** readback agree (consistency check) | host_pipeline (dur) + network (RTT/2) + client_pipeline (dur) |

This is why dual topology matters: loopback validates that the summed per-stage durations equal the independently measured glass-to-glass (success criterion #3), giving us confidence in the instrumentation before trusting the Wi-Fi numbers.

---

## 6. Components & New Files

```
harness/
  client/
    setup.sh            # clone pinned moonlight-qt commit, apply trace.patch, build (Apple Silicon)
    trace.patch         # small localized patch: emit recv/decode_submit/decode_done/present (JSONL)
    run.py              # CLI-drive pair/stream/quit; sets MOONLIGHT_TRACE_FILE; per-topology target
    pinned-commit.txt   # the exact upstream commit/tag we build
  workload/
    LumenWorkload.swift # Metal/CoreAnimation app: paints binary-block counter + motion region
    build.sh            # build the workload app (Apple Silicon)
    (logs id→t_paint to a JSONL trace)
  readback/
    LumenReadback.swift # SCK capture of the moonlight-qt window; threshold-decode blocks
    build.sh
    (logs id→t_observe to a JSONL trace)
  runner/
    topology.py         # NEW: per-topology launch/teardown of client + readback (+ workload on host)
    session.py          # MODIFY: topology param (drop hardcoded "loopback")
    loop.py             # MODIFY: deploy workload+client to mini; run both topologies; collect traces
    deploy.py           # NEW: push harness/{workload,client,readback} build outputs to the mini
  trace/
    schema.py           # MODIFY: optional `extra` field; client rows already representable
    report.py           # MODIFY: host↔client join; client stages; network span; glass-to-glass; per-topology + deltas; consistency check
  config.toml           # MODIFY: [topologies], [client], [workload], [readback] sections
```

### 6.1 Client (`harness/client/`)
- `setup.sh` clones the pinned moonlight-qt commit, applies `trace.patch`, runs the Apple-Silicon build (`setup-deps.py` then `qmake6 && make release`). Runs on both machines; build output lives on T7 on the mini.
- `trace.patch` adds a per-frame JSONL trace at the existing decode/render points, keyed by `DECODE_UNIT.frameNumber`, using the §3 D5 clock expression. Stages: `recv` (depacketized decode-unit ready), `decode_submit`, `decode_done`, `present`. Also records `frameHostProcessingLatency` in `extra`.
- `run.py` drives the client non-interactively (pair if needed, stream a fixed resolution/fps/bitrate for N seconds, quit), pointing `MOONLIGHT_TRACE_FILE` at a per-topology path.

### 6.2 Synthetic workload (`harness/workload/`)
- A small Metal/CoreAnimation app rendered onto the **virtual display** (the one lumen captures). Each frame it paints: (a) fixed corner calibration markers, (b) a row of large black/white luma squares encoding a monotonic counter (D3/D4), (c) a configurable motion/bitrate-stress region so the encoder does real work.
- It logs `id→t_paint` (steady-clock-since-boot ns) to a JSONL trace.

### 6.3 Readback (`harness/readback/`)
- A ScreenCaptureKit tool that captures the **moonlight-qt window** (not the whole screen), locates the calibration markers, thresholds the block squares to recover the counter, and logs `id→t_observe`. De-duplicates repeated observations of the same id (keeps first observation).
- Requires a one-time Screen-Recording TCC grant on each machine it runs on.

### 6.4 Trace schema (`harness/trace/schema.py`)
- Add an optional `extra: dict` field (default empty) for client-only data (`frameHostProcessingLatency`, etc.) and workload/readback ids. Existing fields (`node`, `topology`, `frame_index`, `stage`, `t_ns`, `clock`) already represent client rows. Backward compatible with Plan 2 host traces.

### 6.5 Reporter (`harness/trace/report.py`)
- **Host↔client stage join** by `(topology, frame_index)`; compute client stage durations (`recv→decode_submit`, `decode_submit→decode_done`, `decode_done→present`).
- **Network span**: loopback = `recv−send_last`; Wi-Fi = RTT/2 estimate + `frameHostProcessingLatency` cross-check.
- **Glass-to-glass**: join workload+readback by id; loopback = exact distribution; Wi-Fi = relative.
- **Consistency check**: on loopback, glass-to-glass is an *outer envelope* of the summed pipeline — it additionally includes `paint→capture` (input lag before the encoder sees the frame) and `present→observe` (compositor + readback lag after the decoder). The check asserts `G2G ≥ summed(host+client+network)` and that the gap is small and stable (within a tolerance set empirically per §12.5); flag if G2G is *smaller* than the summed pipeline (impossible — indicates a join/clock bug) or if the gap is unstable.
- **Output**: per-topology side-by-side tables (p50/p95/p99/mean/n) for every host and client stage, network, and glass-to-glass; run-over-run deltas vs the previous report; frame-loss / drop counts.

### 6.6 Orchestration (`harness/runner/`)
- `deploy.py` pushes the built workload + loopback client + readback to the mini (the existing rsync excludes `harness/`, so this is a separate, explicit deploy of build outputs).
- `topology.py` encapsulates, per topology: start workload (host), launch client (target host = 127.0.0.1 or mac-mini), start readback, run for N seconds, stop all, collect traces.
- `session.py` gains a `topology` parameter (replacing the hardcoded `LUMEN_TRACE_TOPOLOGY: "loopback"`).
- `loop.py` orchestrates: build → sign → launch host → for each enabled topology { run } → collect all traces → report.

---

## 7. Pairing Automation
First run pairs once: the moonlight-qt CLI initiates pairing and the harness POSTs the PIN to lumen's confighttp `/pin` endpoint (auth from the harness `sunshine.conf` / state). The client cert + host cert are cached so subsequent runs skip pairing. The exact endpoint, auth, and CLI invocation are pinned during writing-plans — **protocol/pairing research stays in the main thread** per the cyber-safeguard memory note.

---

## 8. Config additions (`harness/config.toml`)
New sections (illustrative; finalized in the plan):
```toml
[topologies]
order = ["loopback", "wifi"]      # both run each loop

[topologies.loopback]
client_target = "127.0.0.1"       # client runs on the mini
run_on        = "mini"

[topologies.wifi]
client_target = "mac-mini"        # client runs on the M5 Max, connects to the mini
run_on        = "dev"

[client]
pinned_commit = "…"               # also stored in harness/client/pinned-commit.txt
resolution    = "1920x1080"
fps           = 60
bitrate_kbps  = 20000
stream_seconds = 20

[workload]
fps           = 60
counter_bits  = 20                # ~17 min of unique ids at 60fps

[readback]
window_match  = "Moonlight"
```

---

## 9. Execution Phases (inside the single plan)
Sequenced so each phase is independently verifiable:
- **Phase A — Wi-Fi client + frame_index join.** Vendor+patch+build moonlight-qt on the M5 Max; automate pair/stream/quit against the mini; extend schema+reporter to join host+client by `frame_index`. *Deliverable: Plan 2's host trace now carries real per-frame data and a full host+client breakdown over Wi-Fi.*
- **Phase B — loopback client on the mini.** Build+run moonlight-qt on the mini against 127.0.0.1; exact network span via shared clock.
- **Phase C — synthetic workload + readback.** Workload on the virtual display; SCK readback on each client; glass-to-glass + the loopback consistency check.
- **Phase D — reporter unification + smoke run.** Dual-topology side-by-side tables + deltas; one-command end-to-end run.

---

## 10. Success Criteria
1. One command runs the full loop (both topologies) and exits clean with teardown.
2. The report shows per-stage p50/p95/p99 for **host and client** stages, for **both** topologies, with run-over-run deltas.
3. On loopback, the synthetic glass-to-glass is a stable outer envelope of the summed host+client+network durations (`G2G ≥ pipeline`, small/steady gap), validating the instrumentation; a G2G smaller than the pipeline fails the run as a join/clock bug.
4. `frame_index` join coverage is high (most frames have both host and client rows); frame-loss is reported, not silently dropped.
5. Rebuild-then-rerun needs no re-pairing and no re-granting of Screen Recording.

---

## 11. Risks & Mitigations
| Risk | Mitigation |
|---|---|
| **Loopback client window re-captured by lumen** (pixel feedback loop corrupts readback) | The client window must render on a display **distinct** from the captured virtual display. Resolve the mini's display topology in writing-plans (real display vs a second virtual display). *Stage traces are unaffected; only glass-to-glass readback needs this.* |
| Two Qt 6.7+ builds; mini internal disk ~91% full | Both build on T7 (389Gi free); load Homebrew env in every SSH command. |
| Readback TCC re-prompts on long runs | One-time Screen-Recording grant per machine; accept periodic re-prompt risk; flag `persistent-content-capture` for ③. |
| moonlight-qt patch points drift across versions | Pin the commit (`pinned-commit.txt`); keep the patch small and localized. |
| Frame-ID unreadable under heavy compression | Luma-only high-contrast blocks + calibration markers + redundancy; readback de-dups and tolerates dropped ids. |
| Wi-Fi clock skew misread as latency | Never subtract across nodes on Wi-Fi; use durations + RTT/2 + `frameHostProcessingLatency`; absolute Wi-Fi G2G deferred to ③. |

---

## 12. Open Questions → resolved during writing-plans (main thread)
1. Exact moonlight-qt pinned commit and the precise file:line patch points for `recv/decode_submit/decode_done/present`.
2. The moonlight-qt CLI pair/stream/quit invocation and the confighttp `/pin` flow.
3. The mini's display topology for loopback readback (real display vs second virtual display via `vd_helper`).
4. Targeting the workload window onto the captured virtual display (which display index `vd_helper` exposes).
5. The consistency-check tolerance (criterion #3) — set empirically from the first loopback run.

---

## 13. Keep This Updated
**When you change X, update this doc:**
- New client stage or renamed stage → update §5/§6.1/§6.5 and the reporter.
- moonlight-qt re-pinned → update `pinned-commit.txt`, §6.1, and §12.1.
- Clock expression changes in `src/trace.cpp` → update §3 D5 and §5 (loopback subtractability depends on it).
- New topology → update §4, §6.6, §8.
- Update the `CLAUDE.md` row for the Plan 3 plan when it is executed and the outcome is recorded.
