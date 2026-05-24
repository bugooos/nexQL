"""
nexql/schema/registry.py
────────────────────────
Schema registry: the single source of truth for type definitions.

WHY THIS IS ITS OWN MODULE:
  In the monolith, schema logic was spread across:
    • nexql_workbench.py  (_builtin_schema, _schema_for_collection,
                           _infer_scalar_type, infer_schema_from_collections,
                           analyze_schema_relationships, analyze_field_usage,
                           generate_api_docs, schema_diff_text,
                           track_deprecations, visualize_permissions,
                           smart_search_schema, explain_schema_ai_style,
                           save_schema_cache, load_schema_cache)
    • foundation_features.py  (resolve_query target canonicalisation)
    • sdk_integration_features.py  (schema-to-SDK generation)

  All of that belongs here.  The registry is injected into the validator
  and planner; it is NEVER imported by the parser or lexer.

SCALABILITY HOOK:
  The registry interface is designed to support schema federation in the future:
  multiple remote sub-registries can be registered and merged transparently.

PUBLIC API:
  SchemaRegistry()
    .load_builtin()           → self
    .load_from_collections()  → self     (infer from live data)
    .load_from_list()         → self     (from serialised list[dict])
    .get_type(name)           → TypeDef | None
    .get_field_map(target)    → dict[str, FieldDef] | None
    .all_types()              → list[TypeDef]
    .relationships()          → list[RelationshipEdge]
    .diff(other)              → str
    .search(term)             → list[SearchHit]
    .to_list()                → list[dict]
"""

from __future__ import annotations
import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Domain types ─────────────────────────────────────────────────────────────

@dataclass
class FieldDef:
    name:              str
    type:              str           = "any"
    nullable:          bool          = True
    deprecated:        bool          = False
    deprecation_reason: Optional[str] = None
    directives:        list[dict]    = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "FieldDef":
        return cls(
            name               = d.get("name", ""),
            type               = d.get("type", "any"),
            nullable           = d.get("nullable", True),
            deprecated         = bool(d.get("deprecated", False)),
            deprecation_reason = d.get("deprecationReason"),
            directives         = d.get("directives", []),
        )

    def to_dict(self) -> dict:
        return {
            "name":              self.name,
            "type":              self.type,
            "nullable":          self.nullable,
            "deprecated":        self.deprecated,
            "deprecationReason": self.deprecation_reason,
            "directives":        self.directives,
        }


@dataclass
class TypeDef:
    name:   str
    fields: list[FieldDef] = field(default_factory=list)

    def field_map(self) -> dict[str, FieldDef]:
        return {f.name: f for f in self.fields}

    @classmethod
    def from_dict(cls, d: dict) -> "TypeDef":
        return cls(
            name   = d.get("name", ""),
            fields = [FieldDef.from_dict(f) for f in d.get("fields", [])],
        )

    def to_dict(self) -> dict:
        return {"name": self.name, "fields": [f.to_dict() for f in self.fields]}


@dataclass(frozen=True)
class RelationshipEdge:
    src_type:  str
    src_field: str
    dst_type:  str
    field_type: str


@dataclass(frozen=True)
class SearchHit:
    kind:  str    # "type" | "field"
    name:  str    # "Post" or "Post.title"
    score: int


# ─── Registry ─────────────────────────────────────────────────────────────────

class SchemaRegistry:
    """Holds all type definitions and provides query/analysis APIs."""

    def __init__(self, schema: list = None) -> None:
        self._types: dict[str, TypeDef] = {}
        if schema:
            self.load_from_list(schema)

    # ── Loaders ──────────────────────────────────────────────────────────────

    def load_builtin(self) -> "SchemaRegistry":
        """Load the built-in NexQL demo schema."""
        return self.load_from_list(_BUILTIN_SCHEMA)

    def load_from_list(self, schema_list: list[dict]) -> "SchemaRegistry":
        """Load from a serialised list[dict] (from file or API)."""
        for entry in schema_list or []:
            td = TypeDef.from_dict(entry)
            if td.name:
                self._types[td.name] = td
        return self

    def load_from_collections(self, collections: dict) -> "SchemaRegistry":
        """Infer schema from live in-memory collection data."""
        for col_name, rows in (collections or {}).items():
            type_name = (col_name[:-1] if col_name.endswith("s") else col_name).capitalize()
            if not type_name:
                continue
            fields_map: dict[str, FieldDef] = {}
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                for k, v in row.items():
                    if k not in fields_map:
                        fields_map[k] = FieldDef(
                            name=k,
                            type=_infer_scalar_type(v),
                            nullable=(v is None),
                        )
                    elif v is None:
                        fields_map[k].nullable = True
            if fields_map:
                self._types[type_name] = TypeDef(
                    name=type_name, fields=list(fields_map.values())
                )
        return self

    def merge(self, other: "SchemaRegistry") -> "SchemaRegistry":
        """Merge another registry into this one (other wins on conflict)."""
        self._types.update(other._types)
        return self

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_type(self, name: str) -> Optional[TypeDef]:
        return self._types.get(name)

    def get_field_map(self, target: str) -> Optional[dict[str, FieldDef]]:
        """Return field map for the type corresponding to *target* collection."""
        type_name = self._canonical_type(target)
        td = self._types.get(type_name) if type_name else None
        return td.field_map() if td else None

    def all_types(self) -> list[TypeDef]:
        return list(self._types.values())

    def to_list(self) -> list[dict]:
        return [td.to_dict() for td in self._types.values()]

    def to_validator_map(self) -> dict[str, dict]:
        """Return {TypeName: {field_name: field_meta_dict}} for the Validator."""
        return {
            name: {f.name: f.to_dict() for f in td.fields}
            for name, td in self._types.items()
        }

    # ── Analysis ─────────────────────────────────────────────────────────────

    def relationships(self) -> list[RelationshipEdge]:
        type_names = set(self._types)
        edges: list[RelationshipEdge] = []
        for td in self._types.values():
            for f in td.fields:
                base = f.type.strip("[]!")
                if base in type_names and base != td.name:
                    edges.append(RelationshipEdge(
                        src_type=td.name, src_field=f.name,
                        dst_type=base, field_type=f.type
                    ))
        return edges

    def search(self, term: str) -> list[SearchHit]:
        q = (term or "").strip().lower()
        if not q:
            return []
        hits: list[SearchHit] = []
        for td in self._types.values():
            if q in td.name.lower():
                hits.append(SearchHit("type", td.name, 2))
            for f in td.fields:
                if q in f.name.lower() or q in f.type.lower():
                    hits.append(SearchHit("field", f"{td.name}.{f.name}", 1))
        hits.sort(key=lambda h: (-h.score, h.name))
        return hits

    def diff(self, other) -> str:
        """Accept either a SchemaRegistry or a raw list[dict].

        When given a raw list, it is first normalised through a temporary
        registry so both sides use the same serialisation schema.
        """
        if isinstance(other, list):
            tmp = SchemaRegistry(other)
            other_list = tmp.to_list()
        else:
            other_list = other.to_list()
        old = json.dumps(self.to_list(), indent=2, sort_keys=True).splitlines()
        new = json.dumps(other_list, indent=2, sort_keys=True).splitlines()
        lines = list(difflib.unified_diff(
            old, new, fromfile="previous-schema", tofile="current-schema", lineterm=""
        ))
        return "\n".join(lines) or "No schema differences detected."

    def deprecations(self) -> list[dict]:
        items = []
        for td in self._types.values():
            for f in td.fields:
                if f.deprecated or f.deprecation_reason or f.name.startswith("legacy"):
                    items.append({
                        "type": td.name, "field": f.name,
                        "reason": f.deprecation_reason or "Marked deprecated",
                    })
        return items

    def permissions(self) -> list[dict]:
        perms = []
        for td in self._types.values():
            for f in td.fields:
                for d in f.directives:
                    if d.get("name") == "auth":
                        perms.append({
                            "type": td.name, "field": f.name,
                            "role": d.get("args", {}).get("role", "unknown"),
                        })
        return perms

    def explain(self, focus: str = "") -> str:
        types   = list(self._types.keys())
        edges   = self.relationships()
        parts   = [f"Schema has {len(types)} types and {len(edges)} cross-type relationships."]
        if focus:
            td = self._types.get(focus) or self._types.get(focus.capitalize())
            if td:
                req = sum(1 for f in td.fields if not f.nullable)
                parts.append(f"Type {td.name} has {len(td.fields)} fields ({req} required).")
                linked = sorted({e.dst_type for e in edges if e.src_type == td.name})
                if linked:
                    parts.append(f"It links to: {', '.join(linked)}.")
            else:
                parts.append(f"Focus '{focus}' not found. Available: {', '.join(types[:6])}.")
        elif types:
            parts.append(f"Primary entities: {', '.join(types[:6])}{'...' if len(types) > 6 else ''}.")
        return " ".join(parts)

    def generate_api_docs(self) -> str:
        lines = ["# NexQL Schema API Docs", ""]
        for td in self._types.values():
            lines += [f"## {td.name}", "", "| Field | Type | Required |", "|---|---|---|"]
            for f in td.fields:
                req = "Yes" if not f.nullable else "No"
                lines.append(f"| {f.name} | {f.type} | {req} |")
            lines.append("")
        return "\n".join(lines)

    # ── Persistence helpers ───────────────────────────────────────────────────

    def save_to_file(self, path: Path) -> bool:
        try:
            path.write_text(json.dumps(self.to_list(), indent=2))
            return True
        except Exception:
            return False

    def load_from_file(self, path: Path) -> "SchemaRegistry":
        try:
            data = json.loads(path.read_text())
            return self.load_from_list(data)
        except Exception:
            return self

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _canonical_type(target: str) -> Optional[str]:
        singular = target[:-1] if target.endswith("s") else target
        return singular.capitalize() or None


# ─── Type inference ───────────────────────────────────────────────────────────

def _infer_scalar_type(value) -> str:
    if isinstance(value, bool):   return "bool"
    if isinstance(value, int):    return "int"
    if isinstance(value, float):  return "float"
    if isinstance(value, str):
        return "uid" if value[:2] in {"u_","p_","o_","m_","c_"} else "str"
    if isinstance(value, list):
        return "[" + (_infer_scalar_type(value[0]) if value else "any") + "]"
    if isinstance(value, dict):   return "obj"
    return "any"


# ─── Built-in demo schema ─────────────────────────────────────────────────────

_BUILTIN_SCHEMA: list[dict] = [
    {"name": "User", "fields": [
        {"name": "id",        "type": "uid",  "nullable": False},
        {"name": "name",      "type": "str",  "nullable": False},
        {"name": "email",     "type": "str",  "nullable": False},
        {"name": "age",       "type": "int",  "nullable": True},
        {"name": "role",      "type": "str",  "nullable": True},
        {"name": "posts",     "type": "[Post]","nullable": True},
        {"name": "settings",  "type": "UserSettings","nullable": True},
        {"name": "createdAt", "type": "ts",   "nullable": False},
    ]},
    {"name": "Post", "fields": [
        {"name": "id",        "type": "uid",  "nullable": False},
        {"name": "title",     "type": "str",  "nullable": False},
        {"name": "body",      "type": "str",  "nullable": True},
        {"name": "author",    "type": "User", "nullable": False},
        {"name": "authorId",  "type": "uid",  "nullable": False},
        {"name": "score",     "type": "float","nullable": True},
        {"name": "status",    "type": "enum(draft|published|archived)","nullable": False},
        {"name": "tags",      "type": "[str]","nullable": True},
        {"name": "createdAt", "type": "ts",   "nullable": False},
        {"name": "updatedAt", "type": "ts",   "nullable": True},
    ]},
    {"name": "UserSettings", "fields": [
        {"name": "theme",    "type": "enum(light|dark|system)","nullable": False},
        {"name": "notify",   "type": "bool","nullable": False},
        {"name": "timezone", "type": "str", "nullable": True},
    ]},
    {"name": "Org", "fields": [
        {"name": "id",          "type": "uid","nullable": False},
        {"name": "name",        "type": "str","nullable": False},
        {"name": "plan",        "type": "str","nullable": False},
        {"name": "memberCount", "type": "int","nullable": False},
    ]},
    {"name": "Message", "fields": [
        {"name": "id",        "type": "uid","nullable": False},
        {"name": "body",      "type": "str","nullable": False},
        {"name": "authorId",  "type": "uid","nullable": False},
        {"name": "channelId", "type": "uid","nullable": True},
        {"name": "createdAt", "type": "ts", "nullable": False},
    ]},
]
