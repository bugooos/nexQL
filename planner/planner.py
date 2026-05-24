"""
nexql/planner/planner.py
────────────────────────
Query planner: converts a validated AST into an execution plan.

WHY THIS IS SEPARATE FROM THE EXECUTOR:
  PostgreSQL, SQLite, and every serious database engine have a distinct
  planner/optimizer phase that sits between parsing and execution.

  In the original monolith, _build_query_plan() was a ~60-line function
  inside the same file as execute_nexql().  This creates several problems:
    • Cannot unit-test planning without running execution
    • Cannot swap in an AI-assisted planner (planned feature) without
      touching the executor
    • Cannot cache plans independently of results
    • Cannot explain plans to the user without executing first

  The planner's job is to decide *how* to execute a query, not to run it.

PIPELINE POSITION:
  Validator → ValidationResult + QueryDocument → Planner → ExecutionPlan → Executor

SCALABILITY HOOKS:
  • AI-assisted plan selection: inject an AIPlanAdvisor into Planner.__init__
  • Distributed execution: plan can carry shard routing info
  • Plan caching: hash(source) → CachedPlan with TTL

PUBLIC API:
  Planner(registry).plan(doc: QueryDocument) -> ExecutionPlan
"""

from __future__ import annotations
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any

from ..nexql_ast.nodes import QueryDocument, Method, VariableRef
from ..schema.registry import SchemaRegistry


# ─── Execution strategy enum ──────────────────────────────────────────────────

class ExecutionStrategy(Enum):
    IN_MEMORY_SCAN      = auto()   # default: scan in-memory collection
    IN_MEMORY_POINT_GET = auto()   # direct id lookup (fast path)
    SUBSCRIBE_STREAM    = auto()   # streaming subscription
    PUBLISH_EVENT       = auto()   # publish/broadcast
    MOCK                = auto()   # no data source – return mock data


# ─── Plan node ────────────────────────────────────────────────────────────────

@dataclass
class FilterSpec:
    field:    str
    operator: str    # "=", ">", ">=", "<", "<=", "!=", "__any__"
    value:    Any
    any_filters: list['FilterSpec'] = field(default_factory=list)  # for __any__ operator

    @property
    def is_any(self) -> bool:
        return self.operator == "__any__"

@dataclass
class SortSpec:
    field:     str
    direction: str = "asc"   # "asc" | "desc"

@dataclass
class PaginationSpec:
    limit:  Optional[int] = None
    offset: Optional[int] = None
    after:  Optional[str] = None


@dataclass
class ExecutionPlan:
    """
    A complete, self-contained description of how to execute a NexQL query.

    The executor reads this struct; it never touches the original AST directly.
    This decoupling means the planner can be swapped or extended without
    changing executor logic.
    """
    # Identity
    plan_id:    str
    query_hash: str
    created_at: float = field(default_factory=time.time)

    # What to do
    method:    Method              = Method.READ
    target:    str                 = ""
    strategy:  ExecutionStrategy  = ExecutionStrategy.IN_MEMORY_SCAN

    # How to filter / page
    filters:    list[FilterSpec]  = field(default_factory=list)
    or_groups:  list[list[FilterSpec]] = field(default_factory=list)  # OR semantics
    pagination: PaginationSpec    = field(default_factory=PaginationSpec)
    sort:       list[SortSpec]    = field(default_factory=list)

    # What to return
    projected_fields: list[dict]  = field(default_factory=list)  # raw FieldSelection dicts
    columnar_mode:    bool         = False

    # Mutation payload (create/update)
    payload: dict                 = field(default_factory=dict)

    # Cache policy
    cache_ttl: Optional[int]      = None   # seconds; None = no cache

    # Cost estimate (from validator)
    estimated_cost: int           = 0

    # Directives forwarded to executor
    directives: list[dict]        = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "plan_id":      self.plan_id,
            "method":       self.method.value,
            "target":       self.target,
            "strategy":     self.strategy.name,
            "filters":      [{"field": f.field, "op": f.operator, "value": f.value}
                             for f in self.filters],
            "pagination":   {"limit": self.pagination.limit,
                             "offset": self.pagination.offset,
                             "after": self.pagination.after},
            "sort":         [{"field": s.field, "direction": s.direction}
                             for s in self.sort],
            "columnar_mode": self.columnar_mode,
            "cache_ttl":    self.cache_ttl,
            "estimated_cost": self.estimated_cost,
        }


# ─── Planner ──────────────────────────────────────────────────────────────────

class Planner:
    """
    Converts a validated QueryDocument into an ExecutionPlan.

    Args:
        registry:       Schema registry for type/field lookups.
        enable_cache:   Whether to attach cache hints to plans.
    """

    def __init__(
        self,
        registry: Optional[SchemaRegistry] = None,
        enable_cache: bool = True,
    ) -> None:
        self._registry     = registry or SchemaRegistry()
        self._enable_cache = enable_cache

    def plan(self, doc: QueryDocument) -> ExecutionPlan:
        query_hash = _hash_query(doc.source or "")
        plan_id    = f"plan_{query_hash[:8]}_{int(time.time()*1000) % 100000}"

        strategy  = self._choose_strategy(doc)
        filters, or_groups = self._extract_filters(doc)
        pagination = self._extract_pagination(doc)
        sort       = self._extract_sort(doc)
        cache_ttl  = self._extract_cache_ttl(doc) if self._enable_cache else None

        return ExecutionPlan(
            plan_id          = plan_id,
            query_hash       = query_hash,
            method           = doc.method,
            target           = doc.target,
            strategy         = strategy,
            filters          = filters,
            or_groups        = or_groups,
            pagination       = pagination,
            sort             = sort,
            projected_fields = [f.to_dict() if hasattr(f, "to_dict") else _field_to_dict(f)
                                for f in doc.fields],
            columnar_mode    = doc.has_directive("cols"),
            payload          = {k: (_resolve_var_placeholder(v))
                                for k, v in doc.payload.items()},
            cache_ttl        = cache_ttl,
            estimated_cost   = _estimate_cost(doc),
            directives       = [{"name": d.name, "args": d.args} for d in doc.directives],
        )

    def explain(self, doc: QueryDocument) -> str:
        """Return a human-readable plan explanation (like SQL EXPLAIN)."""
        plan = self.plan(doc)
        lines = [
            f"Plan ID:   {plan.plan_id}",
            f"Method:    {plan.method.value}",
            f"Target:    {plan.target}",
            f"Strategy:  {plan.strategy.name}",
            f"Cost:      {plan.estimated_cost}",
        ]
        if plan.filters:
            lines.append("Filters:")
            for f in plan.filters:
                lines.append(f"  {f.field} {f.operator} {f.value!r}")
        if plan.or_groups:
            lines.append(f"OR-groups: {len(plan.or_groups)}")
        pg = plan.pagination
        if pg.limit:    lines.append(f"Limit:     {pg.limit}")
        if pg.offset:   lines.append(f"Offset:    {pg.offset}")
        if pg.after:    lines.append(f"After:     {pg.after!r}")
        if plan.sort:
            lines.append("Sort:      " +
                         ", ".join(f"{s.field} {s.direction}" for s in plan.sort))
        if plan.cache_ttl:
            lines.append(f"Cache TTL: {plan.cache_ttl}s")
        if plan.columnar_mode:
            lines.append("Mode:      columnar (@cols)")
        return "\n".join(lines)

    # ── Strategy selection ────────────────────────────────────────────────────

    def _choose_strategy(self, doc: QueryDocument) -> ExecutionStrategy:
        if doc.method is Method.SUBSCRIBE:
            return ExecutionStrategy.SUBSCRIBE_STREAM
        if doc.method is Method.PUBLISH:
            return ExecutionStrategy.PUBLISH_EVENT
        # Point-get fast path: single id= filter with no pagination
        args = doc.args
        if (doc.method is Method.READ
                and "id" in args
                and not isinstance(args["id"], VariableRef)
                and "limit" not in args):
            return ExecutionStrategy.IN_MEMORY_POINT_GET
        return ExecutionStrategy.IN_MEMORY_SCAN

    # ── Filter extraction ─────────────────────────────────────────────────────

    def _extract_filters(
        self, doc: QueryDocument
    ) -> tuple[list[FilterSpec], list[list[FilterSpec]]]:
        args = doc.args

        # OR semantics
        if "__or__" in args:
            or_groups = []
            for group in args["__or__"]:
                or_groups.append(self._args_to_filters(group))
            return [], or_groups

        return self._args_to_filters(args), []

    @staticmethod
    def _args_to_filters(args: dict) -> list[FilterSpec]:
        skip = {"limit", "offset", "after", "sort", "fields", "__or__"}
        filters = []
        for k, v in args.items():
            if k in skip:
                continue
            # Handle .any() conditions: {field: {"__any__": {nested_filters}}}
            if isinstance(v, dict) and "__any__" in v:
                nested_args = v["__any__"]
                nested_filters = Planner._args_to_filters(nested_args)
                filters.append(FilterSpec(field=k, operator="__any__", value=None, any_filters=nested_filters))
            elif isinstance(v, dict) and len(v) == 1:
                op, val = next(iter(v.items()))
                filters.append(FilterSpec(field=k, operator=op, value=val))
            else:
                actual = v.name if isinstance(v, VariableRef) else v
                filters.append(FilterSpec(field=k, operator="=", value=actual))
        return filters

    # ── Pagination ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_pagination(doc: QueryDocument) -> PaginationSpec:
        args   = doc.args
        limit  = args.get("limit")
        offset = args.get("offset")
        after  = args.get("after")
        return PaginationSpec(
            limit  = int(limit)  if isinstance(limit, int)   else None,
            offset = int(offset) if isinstance(offset, int)  else None,
            after  = str(after)  if isinstance(after, str)   else None,
        )

    # ── Sort ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_sort(doc: QueryDocument) -> list[SortSpec]:
        raw = doc.args.get("sort")
        if not raw:
            return []
        if isinstance(raw, dict):
            return [SortSpec(field=raw.get("field", ""), direction=raw.get("direction", "asc"))]
        if isinstance(raw, list):
            out = []
            for item in raw:
                if isinstance(item, dict):
                    out.append(SortSpec(item.get("field", ""), item.get("direction", "asc")))
            return out
        return []

    # ── Cache hints ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_cache_ttl(doc: QueryDocument) -> Optional[int]:
        if doc.method is not Method.READ:
            return None
        d = doc.get_directive("cache")
        if d and "ttl" in d.args:
            try:
                return int(d.args["ttl"])
            except (TypeError, ValueError):
                pass
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_query(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _resolve_var_placeholder(v):
    if isinstance(v, VariableRef):
        return f"${v.name}"
    return v


def _field_to_dict(f) -> dict:
    """Convert FieldSelection to dict if it has no to_dict method."""
    if hasattr(f, "to_dict"):
        return f.to_dict()
    if isinstance(f, dict):
        return f
    return {"name": str(f)}


def _estimate_cost(doc: QueryDocument) -> int:
    base = {"read": 5, "create": 15, "update": 12,
            "delete": 10, "subscribe": 8, "publish": 8}.get(doc.method.value, 5)
    limit = doc.args.get("limit", 50)
    scale = (min(int(limit), 100) // 10) if isinstance(limit, int) else 5

    def count(fields, depth=1):
        total = 0
        for f in fields:
            total += depth * 2
            sub = f.fields if hasattr(f, "fields") else (f.get("fields") or [])
            if sub:
                total += count(sub, depth + 1)
        return total

    return max(1, min(base + count(doc.fields) + scale, 2000))
