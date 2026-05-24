"""
nexql/runtime/observability.py
──────────────────────────────
Execution metrics, distributed tracing stubs, and latency statistics.

WHY SEPARATE FROM THE EXECUTOR:
  The original observability_features.py was a feature file imported by
  the monolith but also called directly from the Electron IPC bridge.
  It conflated:
    • trace recording (runtime concern)
    • latency histograms (runtime concern)
    • UI panel data formatters (IDE concern)

  This module owns the runtime half.  IDE panels call it via IPC.
  It has NO UI imports.

INSTRUMENTATION APPROACH:
  Every ExecutionContext carries a Trace object.  The executor appends
  TraceStep records at each pipeline stage.  When the response is
  serialised the trace is optionally embedded in the response envelope.
  This gives us query-level "explain" for free.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ─── Trace step ──────────────────────────────────────────────────────────────

@dataclass
class TraceStep:
    stage:      str           # "parse", "validate", "plan", "execute", "serialize"
    elapsed_ms: float
    detail:     Optional[str] = None


@dataclass
class Trace:
    """Per-query execution trace.  Created fresh for every execute() call."""
    query_id:  str
    start_ns:  int = field(default_factory=lambda: time.perf_counter_ns())
    steps:     list[TraceStep] = field(default_factory=list)

    def checkpoint(self, stage: str, detail: Optional[str] = None) -> None:
        elapsed = (time.perf_counter_ns() - self.start_ns) / 1_000_000
        self.steps.append(TraceStep(stage=stage, elapsed_ms=round(elapsed, 3),
                                    detail=detail))

    def total_ms(self) -> float:
        elapsed = (time.perf_counter_ns() - self.start_ns) / 1_000_000
        return round(elapsed, 3)

    def to_list(self) -> list[dict]:
        return [{"stage": s.stage, "elapsed_ms": s.elapsed_ms,
                 "detail": s.detail} for s in self.steps]


# ─── Latency ring-buffer ─────────────────────────────────────────────────────

@dataclass
class LatencySample:
    timestamp_ms: float
    duration_ms:  float
    method:       str
    target:       str
    ok:           bool


class LatencyRecorder:
    """Thread-safe sliding-window latency recorder (last N samples)."""

    def __init__(self, window: int = 1_000) -> None:
        self._samples: deque[LatencySample] = deque(maxlen=window)
        self._lock = threading.Lock()

    def record(self, sample: LatencySample) -> None:
        with self._lock:
            self._samples.append(sample)

    def stats(self, method: Optional[str] = None) -> dict:
        with self._lock:
            samples = list(self._samples)
        if method:
            samples = [s for s in samples if s.method == method]
        if not samples:
            return {"count": 0}
        durations = [s.duration_ms for s in samples]
        durations.sort()
        n = len(durations)
        return {
            "count":   n,
            "min_ms":  round(durations[0], 3),
            "max_ms":  round(durations[-1], 3),
            "avg_ms":  round(statistics.mean(durations), 3),
            "p50_ms":  round(durations[int(n * 0.50)], 3),
            "p95_ms":  round(durations[min(int(n * 0.95), n - 1)], 3),
            "p99_ms":  round(durations[min(int(n * 0.99), n - 1)], 3),
            "ok_rate": round(sum(1 for s in samples if s.ok) / n, 4),
        }

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            samples = list(self._samples)[-n:]
        return [{"ts": s.timestamp_ms, "ms": s.duration_ms,
                 "method": s.method, "target": s.target, "ok": s.ok}
                for s in reversed(samples)]


_DEFAULT_RECORDER: Optional[LatencyRecorder] = None


def get_recorder() -> LatencyRecorder:
    global _DEFAULT_RECORDER
    if _DEFAULT_RECORDER is None:
        _DEFAULT_RECORDER = LatencyRecorder()
    return _DEFAULT_RECORDER


# ─── Execution graph builder ─────────────────────────────────────────────────

def build_execution_graph(history: list[dict]) -> dict:
    """
    Build a lightweight call-graph from query history entries.
    Returns nodes (targets) and edges (target → subfield target links).

    Used by the IDE's Execution Graph panel via IPC.
    """
    nodes: dict[str, dict] = {}   # target → {count, methods}
    edges: list[dict] = []

    from nexql.parser import Parser
    parser = Parser()

    for entry in history or []:
        query = entry.get("query", "")
        if not query:
            continue
        result = parser.parse(query)
        if hasattr(result, "error"):
            continue
        target = result.target
        method = result.method.value

        if target not in nodes:
            nodes[target] = {"target": target, "count": 0, "methods": set()}
        nodes[target]["count"] += 1
        nodes[target]["methods"].add(method)

        # Detect nested sub-selects as implicit joins
        def _scan(fields, parent):
            for f in fields or []:
                children = f.fields if hasattr(f, "fields") else []
                if children:
                    child_name = f.name if hasattr(f, "name") else f.get("name", "")
                    if child_name and child_name != parent:
                        edges.append({"from": parent, "to": child_name,
                                      "field": child_name})
                    _scan(children, child_name)

        _scan(result.fields, target)

    serialized_nodes = [
        {"target": v["target"], "count": v["count"],
         "methods": list(v["methods"])}
        for v in nodes.values()
    ]
    return {"nodes": serialized_nodes, "edges": edges}


# ─── Prometheus-compatible metrics text ──────────────────────────────────────

def export_metrics_text(recorder: Optional[LatencyRecorder] = None) -> str:
    """Export basic metrics in Prometheus text format."""
    recorder = recorder or get_recorder()
    stats = recorder.stats()
    lines = [
        "# HELP nexql_query_duration_ms Query latency",
        "# TYPE nexql_query_duration_ms summary",
    ]
    for quantile, key in [(0.5, "p50_ms"), (0.95, "p95_ms"), (0.99, "p99_ms")]:
        if key in stats:
            lines.append(
                f'nexql_query_duration_ms{{quantile="{quantile}"}} {stats[key]}'
            )
    if "count" in stats:
        lines.append(f"nexql_query_total {stats['count']}")
    return "\n".join(lines)
