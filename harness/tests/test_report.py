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


# ─── New helpers and tests for Task 8 ─────────────────────────────────────────

def _ev(node, frame_index, stage, t_ns, topology="loopback", extra=None):
    return TraceEvent(
        run_id="r1", topology=topology, node=node,
        frame_index=frame_index, stage=stage, t_ns=t_ns,
        clock="steady", extra=extra or {}
    )


def test_join_frames_pairs_host_and_client():
    from harness.trace.report import join_frames
    events = [
        _ev("host",   1, "capture",       1000),
        _ev("host",   1, "encode_submit",  2000),
        _ev("host",   1, "encode_done",    3000),
        _ev("host",   1, "send_last",      4000),
        _ev("client", 1, "recv",           5000),
        _ev("client", 1, "decode_submit",  6000),
        _ev("client", 1, "decode_done",    7000),
        _ev("client", 1, "present",        8000),
    ]
    joined = join_frames(events, topology="loopback")
    assert ("loopback", 1) in joined
    row = joined[("loopback", 1)]
    assert row["host"]["capture"] == 1000
    assert row["client"]["recv"] == 5000
    assert row["client"]["present"] == 8000


def test_compute_client_stages_durations():
    from harness.trace.report import join_frames, compute_client_stages
    events = [
        _ev("client", 1, "recv",          5000),
        _ev("client", 1, "decode_submit", 6000),
        _ev("client", 1, "decode_done",   7000),
        _ev("client", 1, "present",       8000),
    ]
    joined = join_frames(events, topology="loopback")
    durations = compute_client_stages(joined)
    assert durations["client_recv_to_decode_submit_ms"] is not None
    assert durations["client_decode_ms"] is not None
    assert durations["client_present_ms"] is not None
    assert durations["client_pipeline_ms"] is not None


def test_network_span_loopback_exact():
    """On loopback (shared clock), network = recv(client) - send_last(host)."""
    from harness.trace.report import join_frames, compute_network_span
    events = [
        _ev("host",   1, "send_last", 4000),
        _ev("client", 1, "recv",      5000),
    ]
    joined = join_frames(events, topology="loopback")
    spans = compute_network_span(joined, topology="loopback")
    assert spans[("loopback", 1)] == pytest.approx(1000, abs=1)


def test_network_span_wifi_returns_empty():
    """Wi-Fi: cross-machine clocks not subtractable; returns empty dict."""
    from harness.trace.report import join_frames, compute_network_span
    events = [
        _ev("host",   1, "send_last", 4000, topology="wifi"),
        _ev("client", 1, "recv",      5000, topology="wifi"),
    ]
    joined = join_frames(events, topology="wifi")
    spans = compute_network_span(joined, topology="wifi")
    assert spans == {}


def test_frame_drop_counted():
    """Frames present in host but missing client rows are counted as drops."""
    from harness.trace.report import count_frame_drops
    events = [
        _ev("host",   1, "capture",  1000),
        _ev("host",   1, "send_last", 4000),
        _ev("host",   2, "capture",  5000),
        _ev("host",   2, "send_last", 8000),
        # No client events for frame 2 → drop
        _ev("client", 1, "recv",     5000),
        _ev("client", 1, "present",  9000),
    ]
    drops = count_frame_drops(events, topology="loopback")
    assert drops["client_drops"] == 1
    assert drops["host_frames"] == 2


# ─── Task 19: glass-to-glass join and consistency check ──────────────────────

def test_glass_to_glass_join():
    """id→t_paint joined with id→t_observe gives g2g duration."""
    from harness.trace.report import compute_g2g
    paint_events  = [{"id": 5, "t_paint_ns": 1000}]
    observe_events = [{"id": 5, "t_observe_ns": 5000}]
    g2g = compute_g2g(paint_events, observe_events)
    # 5000 - 1000 = 4000 ns
    assert g2g[5] == pytest.approx(4000, abs=1)


def test_g2g_only_includes_matched_ids():
    """Only ids present in BOTH paint and observe are included."""
    from harness.trace.report import compute_g2g
    paint_events   = [{"id": 1, "t_paint_ns": 100}, {"id": 2, "t_paint_ns": 200}]
    observe_events = [{"id": 1, "t_observe_ns": 500}]
    g2g = compute_g2g(paint_events, observe_events)
    assert 1 in g2g
    assert 2 not in g2g


def test_consistency_check_g2g_gte_pipeline():
    """Loopback: G2G >= summed pipeline means the measurement is self-consistent."""
    from harness.trace.report import consistency_check
    ok, msg = consistency_check(g2g_ns=10_000_000, pipeline_ns=8_000_000)
    assert ok is True
    assert "OK" in msg


def test_consistency_check_g2g_less_than_pipeline_is_bug():
    """G2G < pipeline is physically impossible (join or clock bug)."""
    from harness.trace.report import consistency_check
    ok, msg = consistency_check(g2g_ns=5_000_000, pipeline_ns=8_000_000)
    assert ok is False
    assert "BUG" in msg.upper() or "join" in msg.lower()
