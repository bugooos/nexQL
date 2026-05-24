"""
nexql.runtime — The authoritative NexQL execution engine.

This package is the heart of the system.  All other packages (IDE,
transport, SDK) are clients of this package.  This package does NOT
import from ide/, transport/, or any UI layer.

Public API:
  from nexql.runtime import execute, ExecutionContext, ExecutionResult
  from nexql.runtime.security import get_rate_limiter, get_audit_log
  from nexql.runtime.observability import get_recorder
  from nexql.runtime.ai_helpers import query_autocomplete, nl_to_nexql
"""

from .executor     import Executor, ExecutionResult
from .security     import (check_field_auth, check_depth, check_complexity,
                           get_rate_limiter, get_audit_log, get_auth_provider,
                           RateLimiter, AuditLog, AuthProvider)
from .observability import get_recorder, Trace, build_execution_graph
from .ai_helpers   import (query_autocomplete, optimize_query, nl_to_nexql,
                           explain_error, debug_assistant, summarize_response,
                           generate_test_case, generate_schema_docs)


# Convenience function: execute a raw query string against a db dict
def execute(
    query:          str,
    db:             dict,
    user_role:      str  = "user",
    variables:      dict = None,
    operation_name: str  = None,
    rate_limiter:   "RateLimiter" = None,
) -> ExecutionResult:
    """Top-level convenience API: parse → validate → plan → execute."""
    from nexql.schema.registry import SchemaRegistry
    registry = SchemaRegistry(db.get("schema") or [])
    executor = Executor(db=db, registry=registry, rate_limiter=rate_limiter)
    return executor.execute_query(
        query, user_role=user_role,
        variables=variables, operation_name=operation_name,
    )


# Backwards-compatibility alias
ExecutionContext = Executor


__all__ = [
    # core execution
    "execute", "Executor", "ExecutionContext", "ExecutionResult",
    # security
    "check_field_auth", "check_depth", "check_complexity",
    "get_rate_limiter", "get_audit_log", "get_auth_provider",
    "RateLimiter", "AuditLog", "AuthProvider",
    # observability
    "get_recorder", "Trace", "build_execution_graph",
    # AI helpers
    "query_autocomplete", "optimize_query", "nl_to_nexql",
    "explain_error", "debug_assistant", "summarize_response",
    "generate_test_case", "generate_schema_docs",
]
