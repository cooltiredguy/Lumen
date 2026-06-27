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
