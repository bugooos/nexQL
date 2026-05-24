"""
nexql.schema — Schema registry and type system.
"""
from .registry import SchemaRegistry, TypeDef, FieldDef, RelationshipEdge

__all__ = ["SchemaRegistry", "TypeDef", "FieldDef", "RelationshipEdge"]
