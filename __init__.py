"""
nexql — Nexus Query Language engine.

Quick-start:
    from nexql import execute, Parser, SchemaRegistry

    registry = SchemaRegistry()
    result   = execute("? user ($limit 5) { id name }", db=my_db)
    print(result.data)

Architecture layers (inner → outer):
    spec/ → ast/ → parser/ → validator/ → planner/ → runtime/
    storage/ → schema/ → transport/ → sdk/ → plugins/
    ide/ (Electron + frontend — separate process, communicates via IPC)
"""

from nexql.parser  import Parser, Lexer, Token, TokenType
from nexql.nexql_ast import QueryDocument, ParseError, Method
from nexql.schema  import SchemaRegistry
from nexql.runtime import execute, ExecutionContext, ExecutionResult

__version__ = "0.2.0"
__all__ = [
    "Parser", "Lexer", "Token", "TokenType",
    "QueryDocument", "ParseError", "Method",
    "SchemaRegistry",
    "execute", "ExecutionContext", "ExecutionResult",
    "__version__",
]
