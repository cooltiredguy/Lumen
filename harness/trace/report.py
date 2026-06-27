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
