"""
nexql/nexql_ast/nodes.py
────────────────────────
Typed AST node definitions for NexQL (moved from `ast/nodes.py`).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional, Union


# ─── Method enum ─────────────────────────────────────────────────────────────

class Method(Enum):
    READ      = "read"
    CREATE    = "create"
    UPDATE    = "update"
    DELETE    = "delete"
    SUBSCRIBE = "subscribe"
    PUBLISH   = "publish"

    @classmethod
    def from_operator(cls, op: str) -> "Method":
        _MAP = {"?": cls.READ, "+": cls.CREATE, "~": cls.UPDATE,
                "!": cls.DELETE, ">>": cls.SUBSCRIBE, "<<": cls.PUBLISH}
        try:
            return _MAP[op]
        except KeyError:
            raise ValueError(f"Unknown NexQL operator: {op!r}")

    @property
    def is_mutation(self) -> bool:
        return self in (Method.CREATE, Method.UPDATE, Method.DELETE)

    @property
    def is_subscription(self) -> bool:
        return self in (Method.SUBSCRIBE, Method.PUBLISH)


# ─── Literal value union ──────────────────────────────────────────────────────

ScalarValue = Union[str, int, float, bool, None]
NqlValue    = Union[ScalarValue, list, dict, "VariableRef", "DeleteMarker"]


@dataclass(frozen=True, slots=True)
class VariableRef:
    """Represents a $variableName reference in args or payload."""
    name: str   # without the leading $

    def __repr__(self) -> str:
        return f"${self.name}"


@dataclass(frozen=True, slots=True)
class DeleteMarker:
    """Represents an explicit field-removal request in an update payload."""

    def __repr__(self) -> str:
        return "<delete>"


# ─── Directive ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Directive:
    name: str
    args: dict[str, NqlValue] = field(default_factory=dict)


# ─── Field selection ──────────────────────────────────────────────────────────

@dataclass
class FieldSelection:
    """A single field in a projection block.

    - Leaf field:   name="email", fields=[]
    - Nested:       name="posts", fields=[FieldSelection(...), ...]
    - Wildcard:     name="*"
    - Type cond.:   type_condition="User", name="", fields=[...]
    - Field filter: name="subject", filters={"done": true}  (from `subject (done true)`)
    """
    name:           str
    alias:          Optional[str]        = None
    directives:     list[Directive]      = field(default_factory=list)
    filters:        dict[str, NqlValue]  = field(default_factory=dict)  # from ()
    fields:         list["FieldSelection"] = field(default_factory=list)
    type_condition: Optional[str]        = None   # for "... on Type"

    @property
    def is_wildcard(self) -> bool:
        return self.name == "*"

    @property
    def is_leaf(self) -> bool:
        return not self.fields and not self.type_condition

    @property
    def output_name(self) -> str:
        return self.alias or self.name

    def to_dict(self) -> dict:
        node: dict = {}
        if self.type_condition:
            node["type_condition"] = self.type_condition
        else:
            node["name"] = self.name
            if self.alias:
                node["alias"] = self.alias
        if self.directives:
            node["directives"] = [{"name": d.name, "args": dict(d.args)}
                                   for d in self.directives]
        if self.filters:
            node["filters"] = dict(self.filters)
        if self.fields:
            node["fields"] = [child.to_dict() for child in self.fields]
        return node


# ─── Top-level statement ──────────────────────────────────────────────────────

@dataclass
class QueryDocument:
    """The root AST node produced by the parser for every valid NexQL document.

    Args:
        method:         The query method (read/create/update/delete/subscribe/publish).
        target:         Collection or resource name.
        args:           Filter/pagination arguments parsed from the (...) block.
        payload:        For create/update: the input data block.
        fields:         Projection field selections.
        directives:     Top-level directives on the statement.
        operation_name: Optional named operation (PascalCase before target).
        warnings:       Non-fatal parser warnings (e.g. ignored fragment spreads).
        source:         Original raw query string (for diagnostics/diff).
    """
    method:         Method
    target:         str
    args:           dict[str, NqlValue]    = field(default_factory=dict)
    payload:        dict[str, NqlValue]    = field(default_factory=dict)
    fields:         list[FieldSelection]   = field(default_factory=list)
    directives:     list[Directive]        = field(default_factory=list)
    operation_name: Optional[str]          = None
    warnings:       list[str]             = field(default_factory=list)
    source:         str                    = ""

    # ── convenience ──────────────────────────────────────────────────────────

    def has_directive(self, name: str) -> bool:
        return any(d.name == name for d in self.directives)

    def get_directive(self, name: str) -> Optional[Directive]:
        return next((d for d in self.directives if d.name == name), None)

    def to_dict(self) -> dict:
        """Serialise to the plain-dict format expected by the runtime and IDE."""
        def _val(v: NqlValue) -> Any:
            if isinstance(v, VariableRef):
                return f"${v.name}"
            if isinstance(v, DeleteMarker):
                return {"__delete__": True}
            if isinstance(v, dict):
                return {k: _val(vv) for k, vv in v.items()}
            if isinstance(v, list):
                return [_val(i) for i in v]
            return v

        def _field(f: FieldSelection) -> dict:
            node: dict = {}
            if f.type_condition:
                node["type_condition"] = f.type_condition
            else:
                node["name"] = f.name
                if f.alias:
                    node["alias"] = f.alias
            if f.directives:
                node["directives"] = [{"name": d.name, "args": d.args} for d in f.directives]
            if f.fields:
                node["fields"] = [_field(c) for c in f.fields]
            return node

        return {
            "type":           "statement",
            "method":         self.method.value,
            "operation_name": self.operation_name,
            "target":         self.target,
            "args":           {k: _val(v) for k, v in self.args.items()},
            "input_fields":   {k: _val(v) for k, v in self.payload.items()},
            "fields":         [_field(f) for f in self.fields],
            "directives":     [{"name": d.name, "args": d.args} for d in self.directives],
            "warnings":       self.warnings,
        }


# ─── Error node ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParseError:
    """Returned by the parser when the document cannot be parsed at all."""
    message:  str
    position: int   = 0   # byte offset in source
    line:     int   = 0
    column:   int   = 0

    def to_dict(self) -> dict:
        return {"error": self.message, "position": self.position,
                "line": self.line, "column": self.column}


# ─── Union type used throughout ───────────────────────────────────────────────

ParseResult = Union[QueryDocument, ParseError]
