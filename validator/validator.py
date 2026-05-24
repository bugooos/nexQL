"""
nexql/validator/validator.py
────────────────────────────
Semantic validator for NexQL ASTs.

WHY SEPARATE FROM THE PARSER:
  The parser only enforces syntax (grammar).  The validator enforces *semantics*:
    • target exists in the schema
    • required fields are present for create/update
    • field names exist on the target type
    • directive argument types are correct
    • query complexity/depth is within limits
    • @auth roles are recognised
    • $limit/$offset values are positive

  Keeping these concerns separate means:
    • The parser never needs to know about schemas
    • The validator can be run independently (e.g. by a lint tool)
    • Rules can be added/removed without touching the parser

PIPELINE POSITION:
  Parser → AST → Validator → ValidationResult → Planner

PUBLIC API:
  Validator(schema_registry).validate(doc: QueryDocument) -> ValidationResult
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ..parser.lexer import Lexer
from ..parser.tokens import TokenType
from ..nexql_ast.nodes import (
    Method, VariableRef, DeleteMarker, Directive, FieldSelection,
    QueryDocument, ParseError, ParseResult,
)

# ─── Limits (configurable at validator construction) ─────────────────────────
DEFAULT_MAX_DEPTH      = 10
DEFAULT_MAX_FIELDS     = 200
DEFAULT_MAX_COMPLEXITY = 1000


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ValidationError:
    code:    str
    message: str
    path:    str = ""          # e.g. "fields.posts.author"
    hint:    str = ""


@dataclass
class ValidationResult:
    ok:       bool
    errors:   list[ValidationError] = field(default_factory=list)
    warnings: list[str]             = field(default_factory=list)

    def add_error(self, code: str, message: str, path: str = "", hint: str = "") -> None:
        self.errors.append(ValidationError(code, message, path, hint))
        self.ok = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "ok":       self.ok,
            "errors":   [{"code": e.code, "message": e.message,
                          "path": e.path, "hint": e.hint}
                         for e in self.errors],
            "warnings": self.warnings,
        }


# ─── Validator ────────────────────────────────────────────────────────────────

class Validator:
    """
    Stateless semantic validator.

    Args:
        schema_registry: dict mapping type_name → {field_name → field_meta}
                         If None, schema validation is skipped (permissive mode).
        max_depth:       Maximum field nesting depth allowed.
        max_fields:      Maximum total field nodes in projection.
        max_complexity:  Maximum total estimated complexity score.
    """

    def __init__(
        self,
        schema_registry = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_fields: int = DEFAULT_MAX_FIELDS,
        max_complexity: int = DEFAULT_MAX_COMPLEXITY,
    ) -> None:
        # Accept either a plain dict or a SchemaRegistry object
        if schema_registry is None:
            self._schema = {}
        elif isinstance(schema_registry, dict):
            self._schema = schema_registry
        else:
            # Assume SchemaRegistry — call to_validator_map()
            self._schema = schema_registry.to_validator_map()
        self._max_depth   = max_depth
        self._max_fields  = max_fields
        self._max_complex = max_complexity

    def validate(self, doc: QueryDocument) -> ValidationResult:
        result = ValidationResult(ok=True)

        # Forward parser warnings
        for w in doc.warnings:
            result.add_warning(w)

        self._check_target(doc, result)
        self._check_args(doc, result)
        self._check_payload(doc, result)
        self._check_fields(doc.fields, result, depth=0, path="", field_count=[0])
        self._check_complexity(doc, result)
        self._check_string_escapes(doc, result)

        return result

    def validate_grammar_only(self, source: str) -> ValidationResult:
        """Quick grammar check via the parser without schema lookup."""
        from ..parser.parser import Parser
        parsed = Parser().parse(source)
        result = ValidationResult(ok=True)
        from ..nexql_ast.nodes import ParseError
        if isinstance(parsed, ParseError):
            result.add_error("PARSE_ERROR", parsed.message)
        return result

    # ── Rules ─────────────────────────────────────────────────────────────────

    def _check_target(self, doc: QueryDocument, r: ValidationResult) -> None:
        if not doc.target:
            r.add_error("MISSING_TARGET", "No target collection specified")
            return
        if self._schema:
            type_name = self._canonical_type(doc.target)
            if type_name and type_name not in self._schema:
                r.add_error(
                    "UNKNOWN_COLLECTION",
                    f"Collection '{doc.target}' not found in schema",
                    hint=f"Available types: {', '.join(sorted(self._schema)[:8])}",
                )

    def _check_args(self, doc: QueryDocument, r: ValidationResult) -> None:
        args = doc.args
        limit = args.get("limit")
        if limit is not None and not isinstance(limit, (int, VariableRef)):
            r.add_error("TYPE_ERROR", "$limit must be an integer", path="args.$limit")
        if isinstance(limit, int) and limit <= 0:
            r.add_error("VALUE_ERROR", "$limit must be positive", path="args.$limit")

        offset = args.get("offset")
        if isinstance(offset, int) and offset < 0:
            r.add_error("VALUE_ERROR", "$offset must be non-negative", path="args.$offset")
        if "after" in args and "offset" in args:
            r.add_error("CONFLICT", "$after and $offset cannot be used together", path="args")

        after = args.get("after")
        if after is not None and not isinstance(after, (str, VariableRef)):
            r.add_error("TYPE_ERROR", "$after must be a string", path="args.$after")

        # --- New: scan filters for NULL comparisons and emit helpful warnings
        try:
            type_name = self._canonical_type(doc.target)
            type_fields = self._schema.get(type_name, {}) if type_name else {}
        except Exception:
            type_fields = {}

        def _check_arg_key(k, v):
            skip = {"limit", "offset", "after", "sort", "fields", "__or__"}
            if k in skip:
                return

            fmeta = type_fields.get(k, {}) if isinstance(type_fields, dict) else {}

            # v == {op: val}
            if isinstance(v, dict) and "__any__" in v:
                return
            if isinstance(v, dict) and len(v) == 1:
                op, val = next(iter(v.items()))
                if val is None:
                    if op in (">", "<", ">=", "<="):
                        r.add_warning(
                            f"Comparison '{k} {op} null' is always false; use '=' or '<>' for null checks"
                        )
                    if not fmeta.get("nullable", True) and op in ("=", "=="):
                        r.add_warning(f"Field '{k}' is non-nullable; comparing to null will always be false")
                return

            # v is a bare value → equality implied
            if v is None:
                if not fmeta.get("nullable", True):
                    r.add_warning(f"Field '{k}' is non-nullable; comparing to null will always be false")

        # Handle top-level OR-groups
        if "__or__" in doc.args and isinstance(doc.args.get("__or__"), list):
            for group in doc.args.get("__or__"):
                if isinstance(group, dict):
                    for kk, vv in group.items():
                        _check_arg_key(kk, vv)
        else:
            for kk, vv in doc.args.items():
                _check_arg_key(kk, vv)

    def _check_payload(self, doc: QueryDocument, r: ValidationResult) -> None:
        if doc.method not in (Method.CREATE, Method.UPDATE):
            return
        if not doc.payload and doc.method is Method.CREATE:
            r.add_warning("CREATE statement has an empty payload block")
            return

        if doc.method is Method.CREATE and _contains_delete_marker(doc.payload):
            r.add_error(
                "INVALID_PAYLOAD",
                "Field deletion markers are only allowed in update statements",
                path="payload",
            )
            return

        if not self._schema:
            return

        type_name = self._canonical_type(doc.target)
        if not type_name or type_name not in self._schema:
            return

        type_fields = self._schema[type_name]

        # For create: check required fields are present
        if doc.method is Method.CREATE:
            for fname, fmeta in type_fields.items():
                if fname in ("id", "createdAt", "updatedAt"):
                    continue
                if not fmeta.get("nullable", True) and fname not in doc.payload:
                    r.add_error(
                        "MISSING_REQUIRED_FIELD",
                        f"Required field '{fname}' missing for create on '{doc.target}'",
                        path=f"payload.{fname}",
                    )

        # Type-check provided fields
        for fname, fvalue in doc.payload.items():
            if _contains_delete_marker(fvalue):
                continue
            if isinstance(fvalue, VariableRef):
                continue   # runtime resolves variables
            if fname in type_fields:
                fmeta = type_fields[fname]
                if not self._type_matches(fvalue, fmeta.get("type", "any")):
                    r.add_error(
                        "TYPE_MISMATCH",
                        f"Field '{fname}' expects type '{fmeta.get('type')}' "
                        f"but received {type(fvalue).__name__}",
                        path=f"payload.{fname}",
                    )

    def _check_fields(
        self,
        fields:       list[FieldSelection],
        r:            ValidationResult,
        depth:        int,
        path:         str,
        field_count:  list[int],   # mutable counter
    ) -> None:
        if depth > self._max_depth:
            r.add_error("QUERY_TOO_DEEP",
                        f"Field nesting exceeds max depth of {self._max_depth}",
                        path=path)
            return

        for f in fields:
            if f.type_condition:
                self._check_fields(f.fields, r, depth + 1, path + ".(inline)", field_count)
                continue

            field_count[0] += 1
            if field_count[0] > self._max_fields:
                r.add_error("TOO_MANY_FIELDS",
                            f"Query exceeds max field count of {self._max_fields}")
                return

            node_path = f"{path}.{f.name}" if path else f.name
            self._check_field_directives(f.directives, r, node_path)

            if f.fields:
                self._check_fields(f.fields, r, depth + 1, node_path, field_count)

    def _check_field_directives(
        self,
        directives: list[Directive],
        r:          ValidationResult,
        path:       str,
    ) -> None:
        known = {"auth", "cache", "cost", "skip", "include", "rate", "cols"}
        for d in directives:
            if d.name not in known:
                r.add_warning(f"Unknown directive '@{d.name}' at {path}")
            if d.name == "auth":
                if "role" not in d.args:
                    r.add_error("INVALID_DIRECTIVE",
                                f"@auth at '{path}' requires a 'role' argument",
                                path=path)

    def _check_complexity(self, doc: QueryDocument, r: ValidationResult) -> None:
        cost = _estimate_complexity(doc)
        if cost > self._max_complex:
            r.add_error(
                "QUERY_TOO_COMPLEX",
                f"Estimated complexity {cost} exceeds limit {self._max_complex}",
                hint="Reduce field depth, add $limit, or split the query",
            )

    def _check_string_escapes(self, doc: QueryDocument, r: ValidationResult) -> None:
        """Validate string literals for invalid escape sequences (warning-only)."""
        source = doc.source or ""
        if not source:
            return

        lexer = Lexer()
        seen: set[str] = set(r.warnings)
        for tok in lexer.tokenize(source):
            if tok.type is not TokenType.STRING:
                continue

            warnings: list[str] = []
            Lexer.unescape_string(
                tok.value,
                warnings=warnings,
                context=f"line {tok.line}, column {tok.column}",
            )
            for msg in warnings:
                if msg not in seen:
                    r.add_warning(msg)
                    seen.add(msg)

    # ── Schema helpers ────────────────────────────────────────────────────────

    def _canonical_type(self, target: str) -> Optional[str]:
        """Convert collection name to type name (e.g. 'posts' → 'Post')."""
        singular = target[:-1] if target.endswith("s") else target
        return singular.capitalize() or None

    @staticmethod
    def _type_matches(value, typ: str) -> bool:
        if not typ or typ == "any":
            return True
        if typ.startswith("["):
            return isinstance(value, list)
        if typ == "str" or typ.startswith("uid"):
            return isinstance(value, str)
        if typ == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if typ == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if typ == "bool":
            return isinstance(value, bool)
        if typ == "ts":
            return isinstance(value, (int, float))
        if typ.startswith("enum"):
            return isinstance(value, str)
        return True


# ─── Complexity estimator ─────────────────────────────────────────────────────

def _estimate_complexity(doc: QueryDocument) -> int:
    base = {"read": 5, "create": 15, "update": 12,
            "delete": 10, "subscribe": 8, "publish": 8}.get(doc.method.value, 5)

    def count(fields: list[FieldSelection], depth: int = 1) -> int:
        total = 0
        for f in fields:
            total += depth * 2
            if f.fields:
                total += count(f.fields, depth + 1)
        return total

    limit = doc.args.get("limit", 50)
    if isinstance(limit, int):
        scale = max(1, min(limit, 100)) // 10
    else:
        scale = 5

    field_cost = count(doc.fields)
    return max(1, min(base + field_cost + scale, 2000))


def _contains_delete_marker(value) -> bool:
    if isinstance(value, DeleteMarker):
        return True
    if isinstance(value, dict):
        return any(_contains_delete_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_delete_marker(v) for v in value)
    return False
