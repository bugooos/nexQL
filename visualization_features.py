"""Legacy visualization compatibility module."""

from __future__ import annotations

import importlib


def build_schema_graph(schema: list[dict]) -> dict:
    schema_mod = importlib.import_module("nexql.schema")
    viz_mod = importlib.import_module("nexql.runtime.visualization")
    rels = schema_mod.SchemaRegistry(schema).relationships()
    nodes = [{"id": t.get("name"), "label": t.get("name")} for t in schema or [] if t.get("name")]
    edges = [(r.src_type, r.dst_type) for r in rels]
    return {
        "ascii": viz_mod.generate_graph_visualization(nodes, edges, format="ascii"),
        "svg": viz_mod.generate_graph_visualization(nodes, edges, format="svg"),
        "nodes": nodes,
        "edges": [{"from": s, "to": d} for s, d in edges],
    }


def build_erd(schema: list[dict]) -> dict:
    schema_mod = importlib.import_module("nexql.schema")
    viz_mod = importlib.import_module("nexql.runtime.visualization")
    entities = {t.get("name", "Unknown"): [f.get("name", "") for f in t.get("fields", [])] for t in schema or []}
    rels = schema_mod.SchemaRegistry(schema).relationships()
    relationships = [{"from": r.src_type, "to": r.dst_type, "type": r.field_type} for r in rels]
    return viz_mod.generate_erd(entities, relationships)
