#!/usr/bin/env python3
"""
nexql/runtime/server_entry.py
──────────────────────────────
Standalone runtime entry point.

WHY THIS EXISTS:
  The IDE is only ONE possible client of the NexQL runtime.  The runtime
  must be usable without loading any Electron or Tkinter code.  This script
  boots the runtime server so it can be started independently:

    python -m nexql.runtime.server_entry --port 7890
    python -m nexql.runtime.server_entry --stdio       ← for IPC via pipes

  The Electron IDE connects to this via the bridge (ipc/bridge.js) through
  the stdio mode.  A future cloud deployment would use --port mode.

USAGE:
  python -m nexql.runtime.server_entry --stdio
  python -m nexql.runtime.server_entry --port 7890 [--host 0.0.0.0]
  python -m nexql.runtime.server_entry --parse "? user { id }"
  python -m nexql.runtime.server_entry --tokens "? user { id }"
  python -m nexql.runtime.server_entry --validate "? user { id }"

PROTOCOL (stdio / HTTP):
  Request:  {"fn": "execute",  "args": [...], "kwargs": {...}}
  Response: {"ok": true,  "result": {...}}
          | {"ok": false, "error": "..."}
"""

from __future__ import annotations

import json
import sys
import argparse
import traceback
from pathlib import Path


# ─── Function registry ────────────────────────────────────────────────────────
# Only whitelisted functions are callable through the bridge.

def _build_registry():
    from nexql.parser  import Parser
    from nexql.runtime import (execute, query_autocomplete, optimize_query,
                                nl_to_nexql, explain_error, debug_assistant,
                                summarize_response, generate_test_case,
                                generate_schema_docs, get_recorder,
                                build_execution_graph)
    from nexql.schema  import SchemaRegistry
    from nexql.storage import StorageEngine

    _parser = Parser()

    def _execute(query, db, user_role="user", variables=None, operation_name=None):
        result = execute(query, db, user_role=user_role,
                          variables=variables, operation_name=operation_name)
        return result.to_dict()

    def _parse(query):
        result = _parser.parse(query)
        return result.to_dict() if hasattr(result, "to_dict") else {"error": result.message}

    def _tokens(query):
        return _parser.tokenize(query)

    def _validate(query):
        result = _parser.parse(query)
        if hasattr(result, "message"):
            return {"ok": False, "errors": [{"code": "PARSE_ERROR", "message": result.message}]}
        return {"ok": True, "warnings": result.warnings}

    def _schema_infer(collections):
        registry = SchemaRegistry()
        registry.infer_from_collections(collections)
        return registry.to_list()

    def _benchmark(query, db, runs=10):
        import time
        samples, ok_count = [], 0
        for _ in range(max(1, runs)):
            t0 = time.perf_counter()
            r = execute(query, db)
            dt = (time.perf_counter() - t0) * 1000
            samples.append(dt)
            if r.ok:
                ok_count += 1
        samples.sort()
        n = len(samples)
        import statistics
        return {
            "runs": n, "ok_runs": ok_count,
            "min_ms": round(min(samples), 3),
            "max_ms": round(max(samples), 3),
            "avg_ms": round(statistics.mean(samples), 3),
            "p95_ms": round(samples[min(int(n * 0.95), n - 1)], 3),
        }

    return {
        # core execution
        "execute":               _execute,
        "execute_with_state":    lambda q, db, **kw: _execute(q, db, **kw),
        # parser tooling
        "parse":                 _parse,
        "tokenize":              _tokens,
        "validate":              _validate,
        # schema
        "schema_infer":          _schema_infer,
        "schema_relationships":  lambda schema: SchemaRegistry(schema).relationships_as_dicts(),
        "schema_search":         lambda schema, term: SchemaRegistry(schema).search_as_dicts(term),
        "schema_diff":           lambda old, new: SchemaRegistry(old).diff(new),
        "schema_docs":           lambda schema: generate_schema_docs(schema),
        "schema_deprecations":   lambda schema: SchemaRegistry(schema).deprecated_fields(),
        "schema_permissions":    lambda schema: SchemaRegistry(schema).permissions(),
        "schema_explain":        lambda schema, focus="": SchemaRegistry(schema).explain(focus),
        # AI
        "ai_autocomplete":       query_autocomplete,
        "ai_optimize":           optimize_query,
        "ai_nl_query":           nl_to_nexql,
        "ai_debug":              debug_assistant,
        "ai_explain_error":      explain_error,
        "ai_summarize":          summarize_response,
        "ai_test_case":          generate_test_case,
        # observability
        "obs_latency":           lambda: get_recorder().stats(),
        "obs_latency_recent":    lambda n=50: get_recorder().recent(n),
        "obs_graph":             lambda history: build_execution_graph(history),
        # benchmark
        "benchmark":             _benchmark,
    }


# ─── Dispatch ────────────────────────────────────────────────────────────────

def dispatch(registry: dict, fn: str, args: list, kwargs: dict) -> dict:
    func = registry.get(fn)
    if func is None:
        return {"ok": False, "error": f"Unknown function: {fn!r}"}
    try:
        result = func(*args, **kwargs)
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "traceback": traceback.format_exc(limit=5)}


# ─── Stdio mode ───────────────────────────────────────────────────────────────

def run_stdio(registry: dict) -> None:
    """Read newline-delimited JSON from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"Invalid JSON: {e}"}), flush=True)
            continue
        fn     = req.get("fn", "")
        args   = req.get("args", [])
        kwargs = req.get("kwargs", {})
        response = dispatch(registry, fn, args, kwargs)
        print(json.dumps(response, default=str), flush=True)


# ─── HTTP mode ────────────────────────────────────────────────────────────────

def run_http(registry: dict, host: str, port: int) -> None:
    """Minimal HTTP server (no external dependencies)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass   # suppress access log

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
            except Exception:
                self._send(400, {"ok": False, "error": "Invalid JSON"})
                return
            response = dispatch(registry, req.get("fn", ""),
                                req.get("args", []), req.get("kwargs", {}))
            self._send(200, response)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True, "status": "healthy"})
            elif self.path == "/metrics":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"ok": False, "error": "Not found"})

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer((host, port), Handler)
    print(f"NexQL runtime listening on http://{host}:{port}", file=sys.stderr)
    server.serve_forever()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="NexQL Runtime Server")
    ap.add_argument("--stdio",    action="store_true",  help="Stdio NDJSON mode (for IPC)")
    ap.add_argument("--port",     type=int, default=None, help="HTTP port")
    ap.add_argument("--host",     default="127.0.0.1",  help="HTTP host")
    ap.add_argument("--parse",    metavar="QUERY",       help="Parse a query and print AST")
    ap.add_argument("--tokens",   metavar="QUERY",       help="Tokenise a query")
    ap.add_argument("--validate", metavar="QUERY",       help="Validate query grammar")
    args = ap.parse_args()

    registry = _build_registry()

    if args.parse:
        print(json.dumps(dispatch(registry, "parse", [args.parse], {}), indent=2))
    elif args.tokens:
        print(json.dumps(dispatch(registry, "tokenize", [args.tokens], {}), indent=2))
    elif args.validate:
        print(json.dumps(dispatch(registry, "validate", [args.validate], {}), indent=2))
    elif args.stdio:
        run_stdio(registry)
    elif args.port:
        run_http(registry, args.host, args.port)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
