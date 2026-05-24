"""Legacy observability feature compatibility module."""

from __future__ import annotations

import importlib


def calculate_latency_stats(history: list[dict]) -> dict:
    if not history:
        return {"count": 0}
    tooks = []
    ok_count = 0
    for item in history:
        ms = item.get("took")
        if isinstance(ms, str) and ms.endswith("ms"):
            try:
                ms = float(ms[:-2])
            except ValueError:
                ms = None
        if isinstance(ms, (int, float)):
            tooks.append(float(ms))
        if item.get("result", {}).get("ok") is True:
            ok_count += 1
    if not tooks:
        return {"count": len(history), "ok_rate": round(ok_count / len(history), 4)}
    tooks.sort()
    n = len(tooks)
    return {
        "count": n,
        "min_ms": round(tooks[0], 3),
        "max_ms": round(tooks[-1], 3),
        "avg_ms": round(sum(tooks) / n, 3),
        "p50_ms": round(tooks[min(int(n * 0.50), n - 1)], 3),
        "p95_ms": round(tooks[min(int(n * 0.95), n - 1)], 3),
        "ok_rate": round(ok_count / max(1, len(history)), 4),
    }


def get_execution_graph(history: list[dict]) -> dict:
    build_execution_graph = importlib.import_module("nexql.runtime.observability").build_execution_graph
    return build_execution_graph(history)
