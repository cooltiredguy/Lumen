# Lumen Foundation: Measurement Harness + Living Documentation — Design Spec

- **Date:** 2026-06-26
- **Status:** Draft — awaiting user review
- **Sub-project:** Foundation (workstream ① of 5). Delivers milestone #1 (the iteration loop) and the measurement baseline that milestone #3 (latency-floor research) depends on, while producing the pillar documentation as a byproduct.
- **Repo:** `trollzem/Lumen` (fork of LizardByte/Sunshine), at `~/Projects/lumen` on the M5 Max MacBook (dev box) and to be deployed to the M4 Mac mini (host).

---

## 1. Context & Goal

Lumen is a macOS/Apple-Silicon fork of Sunshine — a Moonlight-compatible game-streaming host. We are giving it a major upgrade across five workstreams: ① this foundation, ② gut the web UI/tray → native menubar app, ③ find the absolute latency floor (its own R&D track, open to rewrite), ④ remove the intrusive controller/AMFI requirement, ⑤ (implicit) cleanup.

**Goal of this sub-project:** stand up a single-command, reproducible **measure → diagnose → fix → re-measure** loop that Claude Code can drive autonomously, giving full visibility into Lumen's behavior: per-stage latency, full structured logs, and an independent end-to-end latency truth check. Simultaneously, document each pillar of Lumen as we trace it to instrument it, and create a maintainable documentation system that grows with the code.

**Why this first:** you cannot optimize (③), safely gut (②), or confidently change (④) a system you cannot run, measure, and read. This foundation makes every other workstream tractable.

---

## 2. Scope

**In scope:**
1. Reproducible build + deploy of the *current* repo to the M4 mini.
2. Correct launch into the console GUI (Aqua) session with a precondition gate.
3. Per-frame host instrumentation (extending existing plumbing) emitting structured traces keyed by frame index.
4. An instrumentable Moonlight client (moonlight-qt from source) emitting matching per-frame client traces.
5. A synthetic timestamp workload + software glass-to-glass readback.
6. Dual-topology runs (loopback on the mini; M5 Max over Wi-Fi).
7. A reporter producing per-stage percentiles, topology comparison, run-over-run deltas, and full raw logs.
8. Pillar documentation: a repo-root `CLAUDE.md` doc-index, per-pillar docs with a mandatory "keep updated" directive, deepest treatment for the latency-path pillars.

**Out of scope (deferred):**
- True hardware glass-to-glass (capture card / high-speed camera) → milestone ③.
- Custom headless client on `moonlight-common-c` → milestone ③.
- Any rewrite of the streaming pipeline → milestone ③.
- The menubar app → milestone ②. (The harness must NOT depend on the web UI; it drives Lumen headlessly.)
- LAN/wired topology, Apple TV automation → revisit only if proven necessary.

---

## 3. Verified Constraints (de-risking results)

These were verified against the code and current macOS behavior before this spec. They are load-bearing.

### 3.1 SSH-launched Lumen will NOT capture — must inject into the Aqua session *(verified: refuted the naive assumption, high confidence, independently cross-checked)*
- `sshd`'s `ssh.plist` has no `SessionCreate`, so SSH processes run in the **Background** launchd domain, not **Aqua**. In Background: `SCShareableContent` errors → `sc_capture.m:92` returns `nil` (no capture); `vd_helper`'s WindowServer/SkyLight calls fail (`vd_helper.m:56-59,264-278`; `virtual_display.m:102` `posix_spawn` inherits the wrong session) → `virtual_display_create` returns 0.
- **Silent-failure trap:** `sc_capture.m:303-306` re-serves the last cached sample buffer when frames stop, so a broken capture looks "alive but frozen." The harness must not be fooled by this.
- **Required approach:**
  - Launch via `launchctl asuser <consoleUID> …` (quick runs) or a per-user LaunchAgent bootstrapped into `gui/<consoleUID>` (persistent runs). Console UID is currently 501 (hazemeissa, logged in).
  - **Precondition gate:** abort a run unless `launchctl asuser <uid> launchctl managername` prints `Aqua`, and a console user is actually logged in (`scutil` `State:/Users/ConsoleUser` Name non-empty).
  - **Keep awake/unlocked:** `caffeinate -dimsu` + `pmset displaysleep 0 sleep 0 disablesleep 1`; disable screen lock/screensaver. Screen lock or sleep stalls SCK frame delivery.

### 3.2 TCC (Screen Recording) must be granted to a stable-signed binary
- Ad-hoc signing (`codesign --sign -`, as `install.sh` does) rotates the code identity every build, revoking the Screen Recording grant → capture silently denied after a rebuild.
- **Required approach:** sign `sunshine` and `vd_helper` with a **stable self-signed identity** so the TCC grant persists across rebuilds; grant Screen Recording (and Accessibility) **to that binary** once via the GUI (Screen Sharing into the console session). Drop the Terminal-grant assumption from `install.sh`. (No TCC.db editing.)
- **Residual risk:** without `com.apple.developer.persistent-content-capture`, macOS may periodically re-prompt for Screen Recording, which can interrupt long unattended runs. Accept for now; note for ③.

### 3.3 Host↔client frame correlation exists natively *(verified, high confidence)*
- The frame index flows host → wire → client: host sets `packet.frameIndex = frame_index()` (`stream.cpp:1434,1495,1513`); the wire carries it (`moonlight-common-c/RtpVideoQueue.c:366`, `VideoDepacketizer.c:767`); the client's `DECODE_UNIT` exposes `frameNumber` (`Limelight.h:146`).
- We can join host-side and client-side per-frame timestamps by this index with no cross-machine clock sync.

### 3.4 Host instrumentation largely already exists
- A `frame_timestamp` (steady_clock) and `frame_nr` are threaded capture → `encode()` → send (`video.cpp:1528,2025`; `encode_avcodec/encode_nvenc` take both).
- Per-frame processing latency is already computed and stamped: `frame_header.frame_processing_latency = now − frame_timestamp` (`stream.cpp:1334-1341`), and reaches the client as `frameHostProcessingLatency` (`Limelight.h:155`).
- Periodic latency loggers already exist (`stream.cpp:1280-1284`: processing, send-batch, FEC, overall network). We extend these to per-frame structured emission rather than building from scratch.
- Existing per-frame log line: `stream.cpp:1576` `Sent Frame seq [N] pts […]`.

### 3.5 Client is feasible
- moonlight-qt builds on Apple-Silicon macOS (Qt 6.7+, `qmake6 moonlight-qt.pro` → `make release`, `setup-deps.py`). It has CLI stream initiation (pair/stream/quit) and a performance overlay computing network/decode/queue latency and frame loss — the numbers we want already exist in-code and need only a patch to emit per-frame JSONL keyed by `DECODE_UNIT.frameNumber`.

### 3.6 Build environment on the mini
- Present: cmake 4.2.3, pkgconf 2.5.1, openssl@3 3.6.2, opus 1.5.2, doxygen, graphviz, node 25.2.1, icu4c@78, miniupnpc, ffmpeg 8.0.1; full Xcode 26.4 SDK + clang 21.
- **Missing:** `boost`, `llvm` → `brew install boost llvm` before first build.
- Internal data volume ~91% full (~18Gi free) → **build artifacts on T7** (389Gi free). Non-interactive SSH must load Homebrew env (`eval "$(/opt/homebrew/bin/brew shellenv)"`).

---

## 4. Architecture Overview

```
┌─ M5 Max MacBook (dev box, where Claude Code runs) ───────────────────────────┐
│  edit code in ~/Projects/lumen                                               │
│  harness/runner  ── orchestrates everything, one command ──┐                 │
│  moonlight-qt (instrumented)  ◄── Wi-Fi client ──┐         │                 │
│  client trace JSONL  ─────────────────────────┐  │         │                 │
│  screen-capture readback (reads frame-ID) ──┐ │  │         │ ssh             │
└──────────────────────────────────────────────│─│──│─────────│─────────────────┘
                                               │ │  │         ▼
┌─ M4 Mac mini (host) ──────────────────────────│─│──│── gui/501 (Aqua) ────────┐
│  rsync'd working tree on T7 → incremental build (boost/llvm, T7 artifacts)    │
│  launchctl asuser 501 → lumen (sunshine)  [GATE: managername==Aqua]          │
│     ├─ synthetic timestamp workload on the virtual display                   │
│     ├─ host trace JSONL (per-frame, keyed by frame_index)                    │
│     └─ moonlight-qt loopback client (instrumented) ◄── loopback ────────────┘
│  caffeinate + no-sleep/no-lock                                                │
└──────────────────────────────────────────────────────────────────────────────┘
        │ host traces + client traces + readback ─► harness/reporter ─► report.{md,json}
```

Every run executes **both** topologies (loopback on the mini; Wi-Fi to the M5 Max) and merges the data.

---

## 5. Components

### 5.1 Orchestrator (`harness/runner/`)
A single command (e.g. `harness/runner/loop.py run`) that, idempotently and with teardown on exit:
1. Asserts preconditions (mini reachable; console user logged in; deps present; disk OK).
2. `rsync` the working tree → `/Volumes/T7/lumen-harness/Lumen` on the mini (excludes `.git`, build dirs).
3. Incremental build (§5.2).
4. Stable-sign `sunshine` + `vd_helper` (§3.2).
5. Launch Lumen into Aqua (§3.3 / §5.3), gate on `managername==Aqua`, wait for "ready" in logs.
6. Pair once via the confighttp PIN API (cached in `sunshine_state.json` thereafter) and start the synthetic workload (§5.6).
7. Run topology A (loopback client on the mini) for N seconds; collect traces.
8. Run topology B (Wi-Fi client on the M5 Max) for N seconds; collect traces + screen-capture readback.
9. Tear down (stop clients, stop Lumen, restore power/lock settings if changed).
10. Invoke the reporter (§5.8); print the summary path.

Config via a single `harness/config.toml` (host, UID, durations, resolution/fps, bitrate, topologies, paths). Language: Python 3 (already on both Macs) for orchestration; small Objective-C/Swift/Metal binary for the synthetic workload.

### 5.2 Build & deploy (`harness/runner/build.sh`)
- Derive cmake flags from `install.sh` (the verified macOS toolchain fixes) but **build only** (no `--creds`, no config overwrite, no interactive prompts): targets `sunshine vd_helper` (+ `get_display_origin`). Skip the **`web-ui` (Vue) asset build** — the harness drives Lumen headlessly and pairs via the confighttp PIN API, so only the Vue front-end assets are skipped; the C++ HTTP server (`confighttp.cpp`) remains compiled into `sunshine`. (This also keeps the harness independent of the front-end we are gutting in workstream ②.)
- Build dir on T7. Incremental: keep the cmake cache; only re-`make` changed targets.
- Load Homebrew env in every SSH command.

### 5.3 Session launch & permissions (`harness/runner/session.sh`)
- Resolve `UID=$(stat -f%u /dev/console)`; assert an Aqua session exists.
- Launch: `launchctl asuser "$UID" <wrapper>` where the wrapper sets `SUNSHINE_ASSETS_DIR`, starts `caffeinate -dimsu` bound to the process, and execs the stable-signed binary with structured logging enabled.
- Gate: `launchctl asuser "$UID" launchctl managername` must equal `Aqua`.
- One-time manual step (documented, not automated): grant Screen Recording + Accessibility to the signed binary via Screen Sharing.

### 5.4 Host instrumentation (`src/` changes, behind a build flag)
- Add a lightweight **trace sink**: a lock-free per-frame event recorder writing JSONL to a file (and/or a unix socket), gated by an env var / config so it is zero-cost when off.
- Emit events keyed by `frame_index` at existing points: capture-callback / frame origination (`video.cpp` ~2025), encode-submit & encode-done (around `encode()` `video.cpp:1528`), packetize & first/last-packet-send + `frame_processing_latency` (`stream.cpp:1334-1341`, near 1576).
- Reuse `frame_timestamp` (already threaded). Prefer `mach_absolute_time` resolution; keep steady_clock where already present and record the clock domain.
- No protocol changes. This is additive and removable.

### 5.5 Client (`harness/client/`)
- Vendor a pinned moonlight-qt source + a patch that adds a per-frame JSONL trace (packet-receive, decode-submit, decode-done, render/present) keyed by `DECODE_UNIT.frameNumber`, reusing the existing overlay-stat computation points. Build script for Apple-Silicon.
- Driven via the CLI for non-interactive pair/stream/quit.

### 5.6 Synthetic workload + software glass-to-glass (`harness/workload/`)
- A small Metal/CoreAnimation app rendered onto the streamed virtual display that paints, each frame: a **machine-readable frame-ID + monotonic host timestamp** block, plus a configurable motion/bitrate-stress region.
- On the Wi-Fi client (a Mac we control), screen-capture the moonlight-qt window and decode the block → display-to-display latency in software.
  - **Loopback:** host and client share one clock → readback E2E is exact; cross-checks the summed stage timings.
  - **Wi-Fi:** clocks differ → readback is a sanity bound; the authoritative E2E is `host-side (host clock) + network (RTT-estimated) + client-side (client clock)`, which needs no cross-clock subtraction.

### 5.7 Trace schema & data model (`harness/trace/schema.*`)
One JSONL event per stage transition:
```json
{ "run_id": "...", "topology": "loopback|wifi", "node": "host|client",
  "frame_index": 12345, "stage": "capture|encode_submit|encode_done|packetize|send_first|send_last|recv|decode_submit|decode_done|present",
  "t_ns": 123456789, "clock": "mach_abs|steady|client_steady", "extra": { } }
```
The reporter joins host+client rows by `(run_id, topology, frame_index)` and derives per-stage durations.

### 5.8 Reporter (`harness/trace/report.py`)
- Output `harness/reports/<run_id>/report.md` + `report.json` (+ copies of raw host/client logs).
- Per-stage **p50/p95/p99** and mean, per topology, side by side; the readback E2E; frame-loss and drop counts; encoder bitrate vs configured.
- **Run-over-run deltas** vs the previous report (regression/improvement per stage).
- Full raw logs retained for Claude to read end to end.

---

## 6. Documentation Deliverable

Produced *with* the harness (we read each pillar to instrument it), plus a completeness pass for the rest.

- **`CLAUDE.md` (repo root):** a doc-index **table** — one row per pillar: *Pillar · One-line purpose · Doc path · Governing code paths*. This is the map a future Claude/Code session reads first.
- **`docs/pillars/<pillar>.md`:** how the pillar works, key types/functions with `file:line` refs, data flow, gotchas. Latency-path pillars (capture+virtual-display, video-encode, streaming-protocol, client) get the deepest treatment now; the rest (audio, input, pairing/control, config, app-launch, tray) are produced via a parallel multi-agent mapping pass for completeness.
- **Mandatory "Keep this updated" directive in every doc:** a standing instruction + checklist at the foot of each doc — *"When you change X, update this doc: update the file:line refs, the data-flow section, and the CLAUDE.md row."* Tied to that pillar's change triggers so docs grow with the code instead of rotting.
- A short `docs/pillars/_TEMPLATE.md` enforces the structure incl. the keep-updated block.

---

## 7. Repo Layout (additions)

```
CLAUDE.md                          # doc-index table (links every pillar doc)
docs/
  pillars/_TEMPLATE.md
  pillars/*.md                     # per-pillar docs, each with keep-updated directive
  superpowers/specs/2026-06-26-measurement-harness-design.md   # this spec
harness/
  config.toml
  runner/  loop.py build.sh session.sh        # the one-command loop
  workload/                                    # synthetic timestamp app (Metal/CoreAnimation)
  client/                                      # pinned moonlight-qt + instrumentation patch + build
  trace/   schema.* report.py                  # trace schema + reporter
  reports/                                     # generated (gitignored)
src/ … (additive trace sink behind a flag)
```

---

## 8. Success Criteria

1. One command on the M5 Max runs the full loop and exits clean (with teardown), no manual steps after the one-time permission grant.
2. The run produces a report with per-stage p50/p95/p99 for **both** topologies and deltas vs the prior run.
3. On loopback, the synthetic readback E2E agrees with the summed host+client stage timings within a stated tolerance (validates instrumentation).
4. The Aqua-session gate reliably refuses to "measure a frozen capture" (§3.1 trap).
5. `CLAUDE.md` index exists and links pillar docs for at least the four latency-path pillars, each with a working keep-updated directive; remaining pillars stubbed or filled by the mapping pass.
6. Rebuild-then-rerun does not require re-granting Screen Recording (stable signing works).

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Capture silently frozen (cached-frame trap) | Aqua gate + liveness check (frame_index advancing) before trusting a run |
| Per-build TCC revocation | Stable self-signed identity for sunshine + vd_helper |
| No console session after reboot | Assert ConsoleUser logged in; document enabling auto-login if true-headless is needed |
| Periodic Screen-Recording re-prompt on long runs | Accept now; flag `persistent-content-capture` for ③ |
| Wi-Fi jitter masking small wins | Dual topology — loopback gives the low-variance host-pipeline signal |
| External-drive (T7) build I/O | Acceptable on USB-SSD; revisit if it dominates iteration time |
| moonlight-qt patch points shift across versions | Pin the client version; keep the patch small and localized |

---

## 10. Out of Scope → Milestone ③ Handoff

The harness is the instrument; the floor-finding is separate. ③ will add (as needed): hardware glass-to-glass, a custom instrumented `moonlight-common-c` client, `persistent-content-capture`, and any pipeline rewrite — all measured by this harness.

---

## 11. Open Questions for the User

1. **Build/deploy:** confirm building on **T7** (vs freeing internal disk) and **rsync working tree** (vs git push/pull) — both are reversible.
2. **Stable signing identity:** OK to create a local self-signed code-signing cert on the mini for stable TCC (no paid Apple Developer account needed)?
3. **Power/lock settings:** OK for the harness to disable display-sleep / screen-lock on the mini during runs (and restore after)?
4. **Doc depth now:** produce *all* pillar docs in this sub-project, or only the latency-path pillars now and the rest as their workstreams come up?
