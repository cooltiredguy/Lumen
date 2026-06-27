from __future__ import annotations
import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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


# ─── Per-topology joined frame map ───────────────────────────────────────────

def join_frames(events: list, topology: str) -> Dict[Tuple[str, int], dict]:
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

_CLIENT_STAGE_PAIRS = [
    ("recv",          "decode_submit", "client_recv_to_decode_submit_ms"),
    ("decode_submit", "decode_done",   "client_decode_ms"),
    ("decode_done",   "present",       "client_present_ms"),
    ("recv",          "present",       "client_pipeline_ms"),
]


def compute_client_stages(joined: Dict[Tuple[str, int], dict]) -> Dict[str, Optional[dict]]:
    """Compute per-client-stage duration distributions (ns→ms) across all frames."""
    raw: Dict[str, List[float]] = {name: [] for _, _, name in _CLIENT_STAGE_PAIRS}
    for row in joined.values():
        c = row.get("client", {})
        for a, b, name in _CLIENT_STAGE_PAIRS:
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

    Loopback only: shared monotonic clock makes recv - send_last meaningful.
    Wi-Fi: cross-machine clocks are not comparable; returns empty dict.
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

def count_frame_drops(events: list, topology: str) -> Dict[str, int]:
    """
    Returns {"host_frames": N, "client_drops": M} where client_drops is the count
    of frames with host 'capture' rows but no client 'recv' row in the given topology.
    """
    host_frames: set = set()
    client_frames: set = set()
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


# ─── Glass-to-glass join ─────────────────────────────────────────────────────

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
    Assert G2G >= summed pipeline (loopback only, shared clock).
    G2G encompasses paint→capture overhead + host pipeline + network + client pipeline + present→observe overhead.
    If G2G < pipeline → the join or clock has a bug.
    Returns (ok: bool, message: str).
    """
    if g2g_ns < pipeline_ns:
        return (False,
            f"CONSISTENCY BUG: G2G ({g2g_ns/1e6:.2f}ms) < pipeline ({pipeline_ns/1e6:.2f}ms). "
            "Indicates a frame_index join error or clock mismatch.")
    gap_ns = g2g_ns - pipeline_ns
    gap_ms = gap_ns / 1e6
    return (True, f"OK — G2G overhead (paint→capture + present→observe): {gap_ms:.2f}ms")


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

    # ─── client join (if client events present) ───────────────────────────
    topologies = list({e.topology for e in events})
    client_sections: dict = {}
    for topo in topologies:
        topo_events = [e for e in events if e.topology == topo]
        joined = join_frames(topo_events, topo)
        client_sections[topo] = {
            "client_stages": compute_client_stages(joined),
            "network_spans_ns": list(compute_network_span(joined, topo).values()),
            "drops": count_frame_drops(topo_events, topo),
        }

    # ─── Glass-to-glass (loopback only) ────────────────────────────────────
    g2g_sections: dict = {}
    paint_path   = run_dir / "workload_trace.jsonl"
    observe_path = run_dir / "readback_loopback.jsonl"
    if paint_path.exists() and observe_path.exists():
        paint_evts   = [json.loads(l) for l in paint_path.read_text().splitlines() if l.strip()]
        observe_evts = [json.loads(l) for l in observe_path.read_text().splitlines() if l.strip()]
        g2g_map = compute_g2g(paint_evts, observe_evts)
        if g2g_map:
            g2g_vals = [float(v) for v in g2g_map.values()]
            g2g_stats = _percentiles(g2g_vals)
            # Consistency check: p50 G2G >= p50 host_pipeline + p50 client_pipeline
            host_p50 = (stages.get("host_pipeline_ms") or {}).get("p50") or 0.0
            client_p50 = (client_sections.get("loopback", {})
                          .get("client_stages", {})
                          .get("client_pipeline_ms") or {}).get("p50") or 0.0
            total_pipeline_ns = (host_p50 + client_p50) * 1e6
            ok, msg = consistency_check(
                g2g_ns=(g2g_stats.get("p50") or 0.0) * 1e6,
                pipeline_ns=total_pipeline_ns,
            )
            g2g_sections["loopback"] = {
                "g2g_ms": g2g_stats,
                "frame_count": len(g2g_map),
                "consistency_ok": ok,
                "consistency_msg": msg,
            }
            print(f"[report] G2G loopback p50={g2g_stats.get('p50'):.2f}ms — {msg}")

    result = {"stages": stages, "frame_count": frame_count, "client": client_sections, "g2g": g2g_sections}
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
