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
