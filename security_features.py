"""Legacy security feature compatibility module."""

from __future__ import annotations

from nexql.runtime.security import check_depth, get_audit_log as _get_audit_log


def query_depth_limiter(ast: dict, max_depth: int = 5) -> dict:
    fields = []
    if isinstance(ast, dict):
        fields = ast.get("fields") or []
    dr = check_depth(fields, max_depth=max_depth)
    return {"ok": dr.ok, "depth": dr.depth, "reason": dr.reason}


def get_audit_log(limit: int = 100) -> list[dict]:
    return _get_audit_log().recent(limit)
