"""Compatibility adapter for legacy IPC calls.

This module preserves the legacy function names expected by Electron IPC
while delegating to the modular Piql runtime implementation.
"""

from __future__ import annotations

import copy
import difflib
import importlib
import time
from typing import Any


def _parser_cls():
    return importlib.import_module("nexql.parser").Parser


def _planner_cls():
    return importlib.import_module("nexql.planner").Planner


def _schema_registry_cls():
    return importlib.import_module("nexql.schema").SchemaRegistry


def _runtime_module():
    return importlib.import_module("nexql.runtime")


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def _to_legacy_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """Convert modular runtime envelope to legacy workbench shape.

    Runtime returns top-level data keys; legacy UI expects metadata + `#data`.
    """
    out = dict(result)

    if "#ts" not in out:
        out["#ts"] = _now_us()

    if out.get("ok") is True and "#data" not in out:
        meta_keys = {"ok", "#qid", "#cost", "#took", "#ts", "#cached", "#trace", "warnings", "errors"}
        data = {k: v for k, v in out.items() if k not in meta_keys and not k.startswith("#")}
        if data:
            for key in list(data.keys()):
                out.pop(key, None)
            out["#data"] = data
        elif "#data" not in out:
            out["#data"] = {}

    return out


def execute_nexql(
    query: str,
    db: dict,
    user_role: str = "user",
    variables: dict | None = None,
    operation_name: str | None = None,
) -> dict:
    runtime = _runtime_module()
    result = runtime.execute(
        query,
        db,
        user_role=user_role,
        variables=variables,
        operation_name=operation_name,
    ).to_dict()
    return _to_legacy_envelope(result)


def execute_nexql_with_state(
    query: str,
    db: dict,
    user_role: str = "user",
    variables: dict | None = None,
    operation_name: str | None = None,
) -> dict:
    return {
        "result": execute_nexql(query, db, user_role, variables, operation_name),
        "db": db,
    }


def _cli_parse_entry(query: str) -> dict:
    parsed = _parser_cls()().parse(query)
    if hasattr(parsed, "to_dict"):
        return parsed.to_dict()
    return {"error": getattr(parsed, "message", "Parse error")}


def _cli_validate_entry(query: str) -> dict:
    parsed = _parser_cls()().parse(query)
    if hasattr(parsed, "message"):
        return {"ok": False, "errors": [{"code": "PARSE_ERROR", "message": parsed.message}]}
    return {"ok": True, "warnings": list(getattr(parsed, "warnings", []))}


def _cli_tokens_entry(query: str) -> list[dict]:
    return _parser_cls()().tokenize(query)


def _cli_plan_entry(query: str) -> dict:
    parsed = _parser_cls()().parse(query)
    if hasattr(parsed, "message"):
        return {"ok": False, "errors": [{"code": "PARSE_ERROR", "message": parsed.message}]}
    plan = _planner_cls()().plan(parsed)
    return {"ok": True, "plan": plan.to_dict()}


def infer_schema_from_collections(collections: dict) -> list[dict]:
    return _schema_registry_cls()().load_from_collections(collections).to_list()


def analyze_schema_relationships(schema: list) -> list[dict]:
    rels = _schema_registry_cls()(schema).relationships()
    return [
        {
            "from": r.src_type,
            "to": r.dst_type,
            "field": r.src_field,
            "type": r.field_type,
        }
        for r in rels
    ]


def schema_diff_text(old_schema: list, new_schema: list) -> str:
    return _schema_registry_cls()(old_schema).diff(new_schema)


def smart_search_schema(schema: list, term: str) -> list[dict]:
    hits = _schema_registry_cls()(schema).search(term)
    return [{"kind": h.kind, "name": h.name, "score": h.score} for h in hits]


def explain_schema_ai_style(schema: list, focus: str = "") -> str:
    return _schema_registry_cls()(schema).explain(focus)


def generate_api_docs(schema: list) -> str:
    return _schema_registry_cls()(schema).generate_api_docs()


def track_deprecations(schema: list) -> list[dict]:
    return _schema_registry_cls()(schema).deprecations()


def visualize_permissions(schema: list) -> list[dict]:
    return _schema_registry_cls()(schema).permissions()


def nl_to_nexql_query(prompt: str, schema: list | None = None) -> str:
    return _runtime_module().nl_to_nexql(prompt, schema or [])


def build_query_diff(old_query: str, new_query: str) -> str:
    old_lines = (old_query or "").splitlines()
    new_lines = (new_query or "").splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, fromfile="history", tofile="current", lineterm="")
    return "\n".join(diff) or "No differences detected."


def expand_query_env(query: str, env_vars: dict) -> str:
    text = query or ""
    for key, value in (env_vars or {}).items():
        text = text.replace("{{" + str(key) + "}}", str(value))
    return text


def benchmark_query_runs(query: str, db: dict, runs: int = 10, user_role: str = "user") -> dict:
    runs = max(1, int(runs or 1))
    samples = []
    ok_runs = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        res = execute_nexql(query, db, user_role=user_role)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        samples.append(dt_ms)
        if res.get("ok") is True:
            ok_runs += 1
    samples_sorted = sorted(samples)
    p95_idx = max(0, min(len(samples_sorted) - 1, int(len(samples_sorted) * 0.95) - 1))
    return {
        "runs": len(samples),
        "ok_runs": ok_runs,
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "avg_ms": round(sum(samples) / len(samples), 3),
        "p95_ms": round(samples_sorted[p95_idx], 3),
    }


def generate_mock_query_response(query: str, db: dict, user_role: str = "user") -> dict:
    sandbox = copy.deepcopy(db)
    result = execute_nexql(query, sandbox, user_role=user_role)
    result["#mock"] = True
    return result
