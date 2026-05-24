"""
nexql/runtime/ai_helpers.py
────────────────────────────
Offline, deterministic AI-style query intelligence helpers.

WHY THIS IS IN runtime/ AND NOT ide/:
  These helpers operate on ASTs, schemas, and query strings — all runtime
  data structures.  They do not render UI.  The IDE calls them via IPC.

  In the monolith they lived in ai_helpers.py which was imported by both
  nexql_workbench.py (UI) and main.js (IPC bridge) — a coupling that made
  testing and extraction impossible without loading Tkinter.

WHAT LIVES HERE:
  • Autocomplete suggestions (schema-aware)
  • Query optimiser (deduplication, limit injection)
  • Natural-language → NexQL heuristic generator
  • Error explanation
  • Response summariser
  • Test-case generator
  • Schema docs generator
  • Resolver stub generator

WHAT DOES NOT LIVE HERE:
  • Any UI widget or Tkinter call
  • Any IPC channel setup
  • Any Electron-specific code
"""

from __future__ import annotations

import re
from typing import Optional

from nexql.parser import Parser
from nexql.nexql_ast import Method


# ─── Autocomplete ─────────────────────────────────────────────────────────────

_COMMON_OPERATORS = ["?", "+", "~", "!", ">>", "<<"]
_COMMON_DIRECTIVES = ["@auth", "@cache", "@cost", "@cols", "@skip", "@include", "@rate"]
_COMMON_KEYWORDS = ["true", "false", "null", "$limit", "$offset", "$after", "$sort", "$fields"]


def query_autocomplete(prefix: str, schema: Optional[list] = None,
                       max_results: int = 8) -> list[str]:
    """Return completion candidates for *prefix* given the current schema."""
    p = (prefix or "").strip().lower()
    candidates: set[str] = set()

    for token in _COMMON_OPERATORS + _COMMON_DIRECTIVES + _COMMON_KEYWORDS:
        if not p or p in token.lower() or token.lower().startswith(p):
            candidates.add(token)

    for t in schema or []:
        name = t.get("name", "")
        if not name:
            continue
        if not p or p in name.lower() or name.lower().startswith(p):
            candidates.add(name)
        for f in t.get("fields", []):
            fn = f.get("name", "")
            if fn and (not p or p in fn.lower() or fn.lower().startswith(p)):
                candidates.add(fn)

    sorted_candidates = sorted(candidates,
                                key=lambda s: (not s.lower().startswith(p), s))
    return sorted_candidates[:max_results]


# ─── Query optimiser ─────────────────────────────────────────────────────────

def optimize_query(query: str, schema: Optional[list] = None) -> str:
    """
    Deterministic query optimizer:
      1. Deduplicates field names within each block.
      2. Injects `$limit 20` on unbounded reads.
      3. Trims whitespace.
    """
    if not query:
        return query

    def _dedupe_block(block: str) -> str:
        seen: list[str] = []
        out: list[str] = []
        for line in block.splitlines():
            ln = line.strip()
            if not ln or ln in seen:
                continue
            seen.append(ln)
            out.append("  " + ln)
        return "\n".join(out)

    res = query
    for m in re.finditer(r"\{([^}]*)\}", query, re.DOTALL):
        blk = m.group(1)
        ded = _dedupe_block(blk)
        res = res.replace("{" + blk + "}", "{\n" + ded + "\n}", 1)

    res = "\n".join(ln.rstrip() for ln in res.splitlines()).strip() + "\n"

    if (res.lstrip().startswith("?")
            and "$limit" not in res
            and "limit" not in res):
        res = re.sub(r"^(\?\s*[A-Za-z0-9_.]+)", r"\1 ($limit 20)", res, count=1)

    return res


# ─── Natural-language → NexQL heuristic ──────────────────────────────────────

def nl_to_nexql(prompt: str, schema: Optional[list] = None) -> str:
    """
    Heuristic, offline NL → NexQL generator.
    No ML model required.  Matches intent keywords, schema entity names,
    field names mentioned in the prompt, and common argument patterns.
    """
    text = (prompt or "").strip()
    if not text:
        return '? user ($limit 10) { id name }'

    low = text.lower()

    # Resolve method intent
    if re.search(r"\b(create|add|new|insert)\b", low):
        method = "+"
    elif re.search(r"\b(update|edit|change|modify|patch)\b", low):
        method = "~"
    elif re.search(r"\b(delete|remove|destroy)\b", low):
        method = "!"
    elif re.search(r"\b(subscribe|watch|stream|listen)\b", low):
        method = ">>"
    else:
        method = "?"

    # Build entity candidates from schema
    schema = schema or []
    candidates: list[str] = []
    for t in schema:
        tname = str(t.get("name", "")).lower()
        if not tname:
            continue
        candidates.append(tname)
        candidates.append(tname + "s")

    target = "user"
    for cand in sorted(set(candidates), key=len, reverse=True):
        if re.search(rf"\b{re.escape(cand)}\b", low):
            target = cand
            break

    # Normalise plural → singular for mutations
    if target.endswith("s") and method in ("+", "~", "!"):
        target = target[:-1]

    # Infer field list from schema + prompt
    type_match = next(
        (t for t in schema if t.get("name", "").lower() == target.lower()), None
    )
    known_fields: list[str] = [
        f.get("name") for f in (type_match or {}).get("fields", []) if f.get("name")
    ]
    fields = [f for f in known_fields
              if re.search(rf"\b{re.escape(str(f).lower())}\b", low)]
    if not fields:
        if "name" in known_fields:
            fields = ["id", "name"]
        elif known_fields:
            fields = known_fields[:3]
        else:
            fields = ["id"]

    # Common argument extraction
    args: list[str] = []
    m_id = re.search(r"\b([a-z]_[0-9]{3,}|[a-z]_[a-z0-9]{4,})\b", low)
    if m_id:
        args.append(f'id "{m_id.group(1)}"')
    m_limit = re.search(r"\b(?:top|first|limit)\s+(\d{1,4})\b", low)
    if m_limit and method == "?":
        args.append(f"$limit {int(m_limit.group(1))}")

    arg_str   = f" ({' '.join(args)})" if args else ""
    field_str = " ".join(fields)

    if method == "+":
        return f'+ {target} {{ name "New {target.capitalize()}" }} {{ id createdAt }}'
    if method == "~":
        id_part = " ".join(args) or 'id "replace_id"'
        return f'~ {target} ({id_part}) {{ name "Updated" }} {{ id updatedAt }}'
    if method == "!":
        id_part = " ".join(args) or 'id "replace_id"'
        return f'! {target} ({id_part}) {{ id }}'
    if method == ">>":
        return f'>> {target}{arg_str} {{ {field_str} }}'
    return f'? {target}{arg_str} {{ {field_str} }}'


# ─── Error explanation ────────────────────────────────────────────────────────

_ERROR_EXPLANATIONS: dict[str, str] = {
    "PARSE_ERROR":         "Syntax issue — check operator placement and balanced braces.",
    "UNKNOWN_COLLECTION":  "Unknown collection — verify the target name or sync the schema.",
    "NOT_FOUND":           "Record not found — check the id argument.",
    "UNAUTHORIZED":        "Access denied — check @auth directives and your user role.",
    "SCHEMA_VIOLATION":    "Schema validation failed — check field types and required fields.",
    "RATE_LIMITED":        "Rate limit exceeded — reduce query cost or retry later.",
    "QUERY_TOO_COMPLEX":   "Query is too complex — reduce nesting depth or field count.",
    "MISSING_REQUIRED":    "A required field is missing from the payload.",
    "TYPE_MISMATCH":       "A payload field has the wrong type.",
}


def explain_error(error: dict) -> str:
    if not isinstance(error, dict):
        return "No error provided."
    code = error.get("code", "")
    return _ERROR_EXPLANATIONS.get(code, error.get("message", "Unknown error"))


def debug_assistant(query: str, result: dict) -> str:
    if not isinstance(result, dict):
        return "No result available to analyse."
    if result.get("ok") is True:
        return "Query succeeded with no errors detected."
    errors = result.get("errors") or []
    if not errors:
        return "Query failed with an unknown error."
    lines = []
    for e in errors[:3]:
        lines.append(f"[{e.get('code', '?')}] {e.get('message', '')} "
                     f"→ {e.get('suggestion', explain_error(e))}")
    return "\n".join(lines)


# ─── Response summariser ──────────────────────────────────────────────────────

def summarize_response(result: dict, max_chars: int = 300) -> str:
    if not isinstance(result, dict):
        return ""
    ok   = result.get("ok")
    cost = result.get("#cost")
    took = result.get("#took")
    keys = [k for k in result if not str(k).startswith("#")]
    summary = f"ok={ok}  cost={cost}  took={took}  keys={keys[:6]}"
    return summary[:max_chars]


# ─── Test-case generator ──────────────────────────────────────────────────────

def generate_test_case(query: str, schema: Optional[list] = None) -> dict:
    parser = Parser()
    result = parser.parse(query)
    target = result.target if hasattr(result, "target") else "unknown"
    method = result.method.value if hasattr(result, "method") else "?"
    return {
        "query":          query,
        "target":         target,
        "method":         method,
        "expected_shape": "non-empty",
        "notes":          "Auto-generated test case — verify manually.",
    }


# ─── Schema docs generator ───────────────────────────────────────────────────

def generate_schema_docs(schema: list) -> str:
    lines = ["# NexQL Schema API Reference", ""]
    for t in schema or []:
        lines.append(f"## {t.get('name')}")
        lines.append("")
        lines.append("| Field | Type | Required |")
        lines.append("|---|---|---|")
        for f in t.get("fields", []):
            req = "Yes" if not f.get("nullable", True) else "No"
            lines.append(f"| {f.get('name')} | {f.get('type')} | {req} |")
        lines.append("")
    return "\n".join(lines)


# ─── Resolver stub generator ─────────────────────────────────────────────────

def generate_resolver_stub(type_name: str, schema: Optional[list] = None) -> str:
    """Emit a Python resolver function stub for *type_name*."""
    fields: list[str] = []
    for t in schema or []:
        if t.get("name", "").lower() == type_name.lower():
            fields = [f.get("name", "") for f in t.get("fields", []) if f.get("name")]
            break
    field_comments = "\n    ".join(f"# {f}" for f in fields) if fields else "# no fields"
    return (
        f"def resolve_{type_name.lower()}(source, args, context):\n"
        f"    \"\"\"\n"
        f"    Resolver for {type_name}.\n"
        f"    Fields:\n"
        f"    {field_comments}\n"
        f"    \"\"\"\n"
        f"    # TODO: implement\n"
        f"    return None\n"
    )
