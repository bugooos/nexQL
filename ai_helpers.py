"""Legacy AI helper compatibility module."""

from __future__ import annotations

from nexql.runtime.ai_helpers import debug_assistant, optimize_query, query_autocomplete


def ai_debug_assistant(user_input: str, result: dict) -> str:
    return debug_assistant(user_input, result)


def ai_optimize_query(query: str, schema: list | None = None) -> str:
    return optimize_query(query, schema or [])


def ai_query_autocomplete(prefix: str, schema: list | None = None, max_results: int = 8) -> list[str]:
    return query_autocomplete(prefix, schema or [], max_results=max_results)
