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
