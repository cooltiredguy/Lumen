import pytest
from harness.trace.schema import TraceEvent, parse_trace
import json

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
