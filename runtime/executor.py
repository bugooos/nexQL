"""
nexql/runtime/executor.py
─────────────────────────
NexQL runtime executor: the authoritative query execution engine.

WHY THIS IS THE HEART OF THE SYSTEM:
  In the monolith, execute_nexql() was ~350 lines inside the same file as:
    • the Tkinter UI class
    • the colour palette
    • snippet management
    • file I/O helpers

  That means the execution engine could never be run without loading Tkinter,
  and any UI change could silently break execution.  This module:
    • has ZERO imports from ide/, frontend, or any UI layer
    • can be run as a standalone server (see transport/)
    • can be tested in isolation
    • receives an ExecutionPlan (not a raw string) from the planner

PIPELINE POSITION:
  ExecutionPlan → Executor → ExecutionResult

COMPILER ANALOGY:
  Parser   = syntactic front-end
  Planner  = optimizer / code-gen
  Executor = runtime / interpreter
  This file IS the interpreter.

PUBLIC API:
  Executor(db).execute(plan) -> ExecutionResult
  Executor(db).execute_query(source, variables, user_role) -> ExecutionResult
"""

from __future__ import annotations
import copy
import random
import string
import time
from typing import Any, Optional, Generator

from ..nexql_ast.nodes import Method, VariableRef, DeleteMarker, QueryDocument, ParseError
from ..planner.planner import ExecutionPlan, ExecutionStrategy, FilterSpec
from ..schema.registry import SchemaRegistry


# ─── Response envelope ────────────────────────────────────────────────────────

class ExecutionResult:
    """
    The canonical result object returned by the executor.
    Always serialize with .to_dict() when sending to a client.
    """

    def __init__(
        self,
        qid:      str,
        ok:       bool,
        data:     Optional[dict]  = None,
        errors:   Optional[list]  = None,
        cost:     int             = 0,
        took_ms:  str             = "0ms",
        metadata: Optional[dict]  = None,
        warnings: Optional[list]  = None,
        trace:    Optional[list]  = None,
    ) -> None:
        self.qid      = qid
        self.ok       = ok
        self.data     = data or {}
        self.errors   = errors or []
        self.cost     = cost
        self.took_ms  = took_ms
        self.metadata = metadata or {}
        self.warnings = warnings or []
        self.trace    = trace or []

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def query_id(self) -> str:
        return self.qid

    @property
    def error_code(self) -> Optional[str]:
        """The code of the first error, or None if ok."""
        if self.errors:
            return self.errors[0].get("code")
        return None

    def to_dict(self) -> dict:
        d: dict = {"ok": self.ok, "#qid": self.qid,
                   "#cost": self.cost, "#took": self.took_ms}
        if self.data:
            d.update(self.data)
        if self.errors:
            d["errors"] = self.errors
        if self.warnings:
            d["warnings"] = self.warnings
        if self.metadata:
            d.update(self.metadata)
        if self.trace:
            d["#trace"] = self.trace
        return d

    @classmethod
    def error(cls, qid: str, code: str, message: str,
              took_ms: str = "0ms", details: Optional[dict] = None,
              hint: str = "") -> "ExecutionResult":
        err: dict = {"code": code, "message": message}
        if hint:
            err["suggestion"] = hint
        if details:
            err.update(details)
        return cls(qid=qid, ok=False, errors=[err], took_ms=took_ms)

    @classmethod
    def success(cls, qid: str, cost: int, data: dict,
                took_ms: str = "0ms", metadata: Optional[dict] = None,
                warnings: Optional[list] = None) -> "ExecutionResult":
        return cls(qid=qid, ok=True, data=data, cost=cost,
                   took_ms=took_ms, metadata=metadata, warnings=warnings)


# ─── Executor ─────────────────────────────────────────────────────────────────

MAX_RESPONSE_SIZE = 500   # hard cap on items returned per query


class Executor:
    """
    Stateful (holds a db reference) query executor.

    Args:
        db:       The in-memory database dict { id, name, collections, schema }.
        registry: Schema registry for projection and auth checks.
    """

    def __init__(
        self,
        db:           dict,
        registry:     Optional[SchemaRegistry] = None,
        rate_limiter  = None,
    ) -> None:
        self._db           = db
        self._registry     = registry or SchemaRegistry()
        self._rate_limiter = rate_limiter

    # ── High-level entry point (used by the pipeline) ─────────────────────────

    def execute(
        self,
        plan:      ExecutionPlan,
        user_role: str             = "user",
        variables: Optional[dict] = None,
    ) -> ExecutionResult:
        """Execute a pre-built plan and return an ExecutionResult."""
        t0 = time.perf_counter()
        qid = _uid("q")

        def took() -> str:
            return f"{int((time.perf_counter() - t0) * 1000)}ms"

        try:
            return self._dispatch(plan, user_role, variables or {}, qid, took)
        except Exception as exc:
            return ExecutionResult.error(qid, "INTERNAL_ERROR", str(exc), took())

    def execute_query(
        self,
        source:    str,
        user_role: str             = "user",
        variables: Optional[dict] = None,
        operation_name: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Convenience method: parse → validate → plan → execute in one call.
        Used by the transport layer and tests.
        """
        from ..parser.parser import Parser
        from ..validator.validator import Validator
        from ..planner.planner import Planner

        t0  = time.perf_counter()
        qid = _uid("q")

        def took() -> str:
            return f"{int((time.perf_counter() - t0) * 1000)}ms"

        # 1. Parse
        parsed = Parser().parse(source)
        if isinstance(parsed, ParseError):
            return ExecutionResult.error(qid, "PARSE_ERROR", parsed.message, took())

        # 2. Operation name check
        if operation_name and parsed.operation_name != operation_name:
            return ExecutionResult.error(
                qid, "PARSE_ERROR",
                f"Requested operation '{operation_name}' does not match "
                f"'{parsed.operation_name}'", took())

        # 3. Validate
        validator = Validator(
            schema_registry=self._registry.to_validator_map()
        )
        v_result = validator.validate(parsed)
        if not v_result.ok:
            first = v_result.errors[0]
            return ExecutionResult.error(
                qid, first.code, first.message, took(), hint=first.hint)

        # 4. Plan
        plan = Planner(self._registry).plan(parsed)

        # 4.5. Rate limit check (uses injected limiter or no-op)
        if self._rate_limiter is not None:
            if not self._rate_limiter.allow(user_role, plan.estimated_cost):
                return ExecutionResult.error(
                    qid, "RATE_LIMITED",
                    f"Rate limit exceeded for role '{user_role}'. "
                    f"Retry after the current window resets.", took())

        # 5. Execute
        result = self.execute(plan, user_role, variables)
        # Forward parse/validation warnings (deduplicate to avoid repetition)
        all_warnings = list(parsed.warnings) + v_result.warnings + result.warnings
        seen = set()
        deduped_warnings = []
        for w in all_warnings:
            if w not in seen:
                seen.add(w)
                deduped_warnings.append(w)
        result.warnings = deduped_warnings
        return result

    def execute_with_state(
        self, source: str, user_role: str = "user",
        variables: Optional[dict] = None,
    ) -> dict:
        """Execute and return {result: dict, db: dict}. Used by Electron bridge."""
        result = self.execute_query(source, user_role, variables)
        return {"result": result.to_dict(), "db": self._db}

    # ── Streaming / subscription ──────────────────────────────────────────────

    def stream(
        self, plan: ExecutionPlan, user_role: str = "user"
    ) -> Generator[dict, None, None]:
        """Yield incremental result chunks for subscribe queries."""
        cols = self._db.get("collections", {})
        target = _canonical_target(plan.target, cols)
        pool   = cols.get(target, [])
        for seq, item in enumerate(pool[:10], start=1):
            projected, _ = _project(item, plan.projected_fields, user_role)
            yield {"ok": True, "#seq": seq, "#qid": _uid("sub"),
                   "data": {target: projected}}
            time.sleep(0)   # yield to event loop

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(
        self,
        plan:      ExecutionPlan,
        user_role: str,
        variables: dict,
        qid:       str,
        took,
    ) -> ExecutionResult:
        method   = plan.method
        cols     = self._db.get("collections", {})
        target   = _canonical_target(plan.target, cols)
        cost     = plan.estimated_cost
        warnings: list[str] = []

        if method is Method.READ:
            return self._exec_read(plan, target, cols, user_role, variables, qid, cost, took, warnings)
        if method is Method.CREATE:
            return self._exec_create(plan, target, cols, user_role, variables, qid, cost, took)
        if method is Method.UPDATE:
            return self._exec_update(plan, target, cols, user_role, variables, qid, cost, took)
        if method is Method.DELETE:
            return self._exec_delete(plan, target, cols, user_role, variables, qid, cost, took)
        if method in (Method.SUBSCRIBE, Method.PUBLISH):
            return self._exec_subscribe(plan, target, cols, user_role, variables, qid, cost, took)
        return ExecutionResult.error(qid, "UNKNOWN_METHOD", f"Unknown method: {method}", took())

    # ── READ ──────────────────────────────────────────────────────────────────

    def _exec_read(
        self, plan, target, cols, user_role, variables, qid, cost, took, warnings
    ) -> ExecutionResult:
        if target not in cols:
            return ExecutionResult.error(
                qid, "UNKNOWN_COLLECTION",
                f"Collection '{target}' not found",
                took(), {"available": list(cols.keys())},
                hint=f"Available collections: {', '.join(cols.keys())}",
            )

        items = cols[target]

        # Apply filters, sort, pagination
        filtered, err, next_cursor = _apply_args(items, plan)
        if err:
            return ExecutionResult.error(qid, "QUERY_ERROR", err, took())

        # Columnar mode
        if plan.columnar_mode and filtered:
            headers = [f["name"] for f in plan.projected_fields if f.get("name") != "*"] \
                      or sorted(filtered[0].keys())
            return ExecutionResult.success(
                qid, cost,
                {f"{target}@cols": {"#": headers,
                                     "*": [[r.get(h) for h in headers] for r in filtered]}},
                took(), metadata={"next": next_cursor} if next_cursor else None,
                warnings=warnings,
            )

        # Normal projection
        rows, unauth = [], []
        for row in filtered:
            proj, row_unauth = _project(row, plan.projected_fields, user_role,
                                        variables=variables)
            if row_unauth:
                unauth.extend(row_unauth)
            if isinstance(plan.projected_fields, list) and \
               isinstance(plan.args if hasattr(plan, "args") else {}, dict):
                pass
            rows.append(proj)

        if unauth:
            return ExecutionResult.error(
                qid, "UNAUTHORIZED", "Access denied to one or more fields", took(),
                {"unauthorized_fields": unauth[:10]})

        meta = {"next": next_cursor} if next_cursor else None
        return ExecutionResult.success(qid, cost, {target: rows}, took(),
                                       metadata=meta, warnings=warnings)

    # ── CREATE ────────────────────────────────────────────────────────────────

    def _exec_create(self, plan, target, cols, user_role, variables, qid, cost, took) -> ExecutionResult:
        payload = _resolve_variables(plan.payload, variables)
        payload.pop("id", None)
        payload.pop("createdAt", None)

        new_item = {"id": _uid(target[:2] if len(target) >= 2 else "x"),
                    "createdAt": int(time.time())}
        new_item.update(payload)

        if target not in cols:
            cols[target] = []
        cols[target].append(copy.deepcopy(new_item))

        out_fields = plan.projected_fields or [{"name": "id"}, {"name": "createdAt"}]
        proj, unauth = _project(new_item, out_fields, user_role, variables=variables)
        if unauth:
            return ExecutionResult.error(qid, "UNAUTHORIZED", "Access denied", took(),
                                         {"unauthorized_fields": unauth})
        return ExecutionResult.success(qid, cost, {target: proj}, took())

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def _exec_update(self, plan, target, cols, user_role, variables, qid, cost, took) -> ExecutionResult:
        pool = cols.get(target, [])
        selected, err, _ = _apply_args(pool, plan)
        if err:
            return ExecutionResult.error(qid, "QUERY_ERROR", err, took())
        item = selected[0] if selected else None
        if not item:
            return ExecutionResult.error(qid, "NOT_FOUND",
                                         f"{target} matching filter not found", took())

        updates = _resolve_variables(plan.payload, variables)
        updates.pop("id", None)
        updated = _apply_update_patch(copy.deepcopy(item), updates)
        updated["updatedAt"] = int(time.time())
        pool[pool.index(item)] = updated

        out_fields = plan.projected_fields or [{"name": "id"}, {"name": "updatedAt"}]
        proj, unauth = _project(updated, out_fields, user_role, variables=variables)
        if unauth:
            return ExecutionResult.error(qid, "UNAUTHORIZED", "Access denied", took(),
                                         {"unauthorized_fields": unauth})
        return ExecutionResult.success(qid, cost, {target: proj}, took())

    # ── DELETE ────────────────────────────────────────────────────────────────

    def _exec_delete(self, plan, target, cols, user_role, variables, qid, cost, took) -> ExecutionResult:
        pool = cols.get(target, [])
        selected, err, _ = _apply_args(pool, plan)
        if err:
            return ExecutionResult.error(qid, "QUERY_ERROR", err, took())
        item = selected[0] if selected else None
        if not item:
            return ExecutionResult.error(qid, "NOT_FOUND",
                                         f"{target} matching filter not found", took())

        cols[target] = [r for r in pool if r is not item]
        out_fields = plan.projected_fields or [{"name": "id"}]
        proj, unauth = _project(item, out_fields, user_role, variables=variables)
        if unauth:
            return ExecutionResult.error(qid, "UNAUTHORIZED", "Access denied", took(),
                                         {"unauthorized_fields": unauth})
        return ExecutionResult.success(qid, cost, {target: proj}, took())

    # ── SUBSCRIBE / PUBLISH ───────────────────────────────────────────────────

    def _exec_subscribe(self, plan, target, cols, user_role, variables, qid, cost, took) -> ExecutionResult:
        pool   = cols.get(target, [])
        sample = pool[0] if pool else {}
        proj, unauth = _project(sample, plan.projected_fields, user_role, variables=variables)
        if unauth:
            return ExecutionResult.success(
                qid, cost, {target: proj}, took(),
                metadata={"#stream": _uid("sub"), "#seq": 1, "partial": True})
        return ExecutionResult.success(
            qid, cost, {target: proj}, took(),
            metadata={"#stream": _uid("sub"), "#seq": 1})


# ─── Projection engine ────────────────────────────────────────────────────────

_SKIP = object()


def _project(
    item:         Any,
    fields:       list[dict],
    user_role:    str  = "user",
    variables:    dict = None,
) -> tuple[Any, list]:
    """
    Project *fields* from *item*, enforcing @auth directives.
    Returns (projected_value, list_of_unauthorized_fields).
    """
    if not fields:
        return (_project_default(item), None)

    if any(f.get("name") == "*" for f in fields if isinstance(f, dict)):
        if isinstance(item, dict):
            fields = [{"name": k} for k in item.keys()]
        else:
            return (item, None)

    out:    dict = {}
    unauth: list = []

    for f in fields:
        if not isinstance(f, dict):
            continue

        # Inline type condition
        if f.get("type_condition"):
            if _item_matches_type(item, f["type_condition"]):
                sub_proj, sub_unauth = _project(
                    item, f.get("fields", []), user_role, variables)
                if isinstance(sub_proj, dict):
                    out.update(sub_proj)
                if sub_unauth:
                    unauth.extend(sub_unauth)
            continue

        key     = f.get("name", "")
        out_key = f.get("alias", key) or key
        if not key:
            continue

        # @skip / @include directives
        if not _directive_allows(f, variables):
            continue

        # @auth check
        allowed, reason = _check_auth(f, user_role)
        if not allowed:
            unauth.append({"field": out_key, "reason": reason})
            continue

        value = item.get(key) if isinstance(item, dict) else None

        # Apply field-level filters if specified: field (age 20) etc.
        field_filters = f.get("filters", {})
        if field_filters and isinstance(value, list) and value and isinstance(value[0], dict):
            # Filter list items based on field filters
            filtered_items = []
            for v in value:
                if all(_matches(v, FilterSpec(field=k, operator="==", value=v2)) 
                       for k, v2 in field_filters.items()):
                    filtered_items.append(v)
            value = filtered_items

        if f.get("fields") and isinstance(value, (dict, list)):
            if isinstance(value, list):
                sub_rows = []
                for v in value:
                    sp, su = _project(v, f["fields"], user_role, variables)
                    if su: unauth.extend(su)
                    sub_rows.append(sp)
                out[out_key] = sub_rows
            else:
                sp, su = _project(value, f["fields"], user_role, variables)
                if su: unauth.extend(su)
                out[out_key] = sp
        else:
            out[out_key] = value

    return out, unauth or None


def _contains_delete_marker(value: Any) -> bool:
    if isinstance(value, DeleteMarker):
        return True
    if isinstance(value, dict):
        return any(_contains_delete_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_delete_marker(v) for v in value)
    return False


def _apply_update_patch(base: Any, patch: Any) -> Any:
    """Apply an update patch while honoring delete markers recursively.

    Ordinary payloads keep the existing shallow-update behavior.
    Delete markers trigger field removal without affecting unrelated keys.
    """
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)

    if not _contains_delete_marker(patch):
        if isinstance(base, dict):
            merged = copy.deepcopy(base)
            merged.update(copy.deepcopy(patch))
            return merged
        return copy.deepcopy(patch)

    if not isinstance(base, dict):
        return copy.deepcopy(base)

    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, DeleteMarker):
            result.pop(key, None)
            continue

        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict) and _contains_delete_marker(value):
            nested = _apply_update_patch(existing, value)
            if nested:
                result[key] = nested
            else:
                result.pop(key, None)
            continue

        if isinstance(existing, list) and isinstance(value, dict) and _contains_delete_marker(value):
            result[key] = [
                _apply_update_patch(item, value) if isinstance(item, dict) else item
                for item in existing
            ]
            continue

        result[key] = copy.deepcopy(value)

    return result


def _project_default(value, depth: int = 3) -> Any:
    """Recursively include all fields up to depth."""
    if isinstance(value, dict):
        if depth <= 0:
            return {}
        return {k: _project_default(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        if depth <= 0:
            return []
        return [_project_default(v, depth - 1) for v in value]
    return value


def _check_auth(field_meta: dict, user_role: str) -> tuple[bool, str]:
    for d in field_meta.get("directives", []):
        if d.get("name") == "auth":
            required = d.get("args", {}).get("role")
            if required and user_role not in (required, "admin"):
                return False, f"Field requires role '{required}'"
    return True, ""


def _directive_allows(field_meta: dict, variables: Optional[dict]) -> bool:
    variables = variables or {}
    for d in field_meta.get("directives", []):
        name = d.get("name")
        args = d.get("args", {})
        if name == "skip":
            val = args.get("if")
            if isinstance(val, str) and val.startswith("$"):
                val = variables.get(val[1:], False)
            if val is True:
                return False
        if name == "include":
            val = args.get("if")
            if isinstance(val, str) and val.startswith("$"):
                val = variables.get(val[1:], True)
            if val is False:
                return False
    return True


def _item_matches_type(item: Any, type_name: str) -> bool:
    if not isinstance(item, dict):
        return False
    for key in ("__type", "__typename", "_type", "type"):
        v = item.get(key)
        if isinstance(v, str) and v.lower() == type_name.lower():
            return True
    # Heuristic: check if the item has fields typical of the type
    return True   # permissive fallback


# ─── Filter / sort / pagination engine ───────────────────────────────────────

def _apply_args(
    items: list[dict],
    plan:  ExecutionPlan,
) -> tuple[list, Optional[str], Optional[str]]:
    """
    Apply filters, OR-groups, sort, and pagination from plan.
    Returns (selected_items, error_message_or_None, next_cursor_or_None).
    """
    # OR-filter groups
    if plan.or_groups:
        merged, seen = [], set()
        for group in plan.or_groups:
            for row in _filter_items(items, group):
                key = row.get("id") if isinstance(row, dict) else id(row)
                if key not in seen:
                    seen.add(key)
                    merged.append(row)
        result = merged
    else:
        result = _filter_items(items, plan.filters)

    # Sort
    result = _sort_items(result, plan.sort)

    # Pagination
    offset = plan.pagination.offset or 0
    after  = plan.pagination.after
    limit  = plan.pagination.limit

    if after and offset:
        return [], "Cannot use $after with $offset simultaneously", None

    if after:
        cursor_val = after[len("cursor_"):] if after.startswith("cursor_") else after
        start = 0
        for idx, row in enumerate(result):
            rid = str(row.get("id", "")) if isinstance(row, dict) else ""
            if rid == after or rid == cursor_val:
                start = idx + 1
                break
        result = result[start:]
    elif offset:
        result = result[offset:]

    cap   = min(limit, MAX_RESPONSE_SIZE) if limit else MAX_RESPONSE_SIZE
    sliced = result[:cap]

    next_cursor = None
    if len(result) > cap and sliced:
        last = sliced[-1]
        if isinstance(last, dict) and last.get("id"):
            next_cursor = f"cursor_{last['id']}"
        else:
            next_cursor = "cursor_token"

    return sliced, None, next_cursor


def _filter_items(items: list, filters: list[FilterSpec]) -> list:
    if not filters:
        return list(items)
    out = []
    for row in items:
        if all(_matches(row, f) for f in filters):
            out.append(row)
    return out


def _matches(row: Any, flt: FilterSpec) -> bool:
    if not isinstance(row, dict):
        return False
    actual = _get_path(row, flt.field)
    expected = flt.value
    op = flt.operator

    # Handle .any() conditions: at least one array item must match all nested filters
    if op == "__any__":
        if not isinstance(actual, list):
            return False
        if not actual:
            return False
        # Check if at least one array item matches all nested filters
        for item in actual:
            if all(_matches(item, nested_flt) for nested_flt in flt.any_filters):
                return True
        return False

    # Explicit NULL semantics
    if expected is None:
        if op in ("=", "=="):
            return actual is None
        if op in ("!=", "<>"):
            return actual is not None
        # ordering comparisons with NULL are meaningless → always false
        return False

    if actual is None and expected is not None and op in ("=", "=="):
        return False

    try:
        if op in ("=", "=="):
            if isinstance(expected, str):
                return str(actual).lower() == expected.lower()
            return actual == expected
        if op in ("!=", "<>"):
            return actual != expected
        if op == ">":   return actual is not None and actual > expected
        if op == ">=":  return actual is not None and actual >= expected
        if op == "<":   return actual is not None and actual < expected
        if op == "<=":  return actual is not None and actual <= expected
    except TypeError:
        return False
    return True


def _sort_items(items: list, sorts: list) -> list:
    if not sorts:
        return list(items)
    result = list(items)
    for spec in reversed(sorts):
        desc = (spec.direction == "desc")
        result.sort(key=lambda r: _get_path(r, spec.field) or "", reverse=desc)
    return result


def _get_path(item: Any, path: str) -> Any:
    parts = path.split(".") if path else []
    current = item
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _resolve_variables(payload: dict, variables: Optional[dict]) -> dict:
    variables = variables or {}
    result = {}
    for k, v in payload.items():
        if isinstance(v, str) and v.startswith("$"):
            result[k] = variables.get(v[1:], None)
        else:
            result[k] = v
    return result


def _canonical_target(target: str, cols: dict) -> str:
    if target in cols:                    return target
    if target + "s" in cols:             return target + "s"
    if target.endswith("s") and target[:-1] in cols: return target[:-1]
    return target


def _uid(prefix: str = "id") -> str:
    chars = random.choices(string.ascii_lowercase + string.digits, k=8)
    return f"{prefix}_{''.join(chars)}"
