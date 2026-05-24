"""
nexql/transport/server.py
─────────────────────────
Transport adapter: exposes the NexQL runtime over HTTP (and optionally WebSocket).

WHY THIS EXISTS SEPARATELY:
  In the original project, the runtime was only accessible through the
  Tkinter GUI.  The transport layer breaks that coupling: the runtime can
  now be started as a standalone HTTP/WebSocket server, enabling:
    • the Electron IDE to use socket-based IPC instead of exec()-ing Python
    • CI/CD integration testing against a live server
    • future CLI clients and SDKs
    • distributed execution workers

ARCHITECTURE:
  This file contains ZERO UI logic.  It knows only:
    • how to deserialise an incoming request
    • how to call Executor.execute_query()
    • how to serialise and send the response

STARTUP:
  python -m nexql.transport.server --port 7433

PUBLIC API (HTTP):
  POST /execute         { query, db_id, user_role, variables }
  GET  /schema          { db_id }
  POST /parse           { query }
  POST /validate        { query }
  POST /plan            { query }
  GET  /health
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from pathlib import Path


# ─── Runtime imports (no IDE, no UI) ─────────────────────────────────────────
# These are the ONLY imports allowed in the transport layer.
# The runtime must remain completely isolated from the IDE.

def _build_runtime(data_dir: Optional[Path] = None):
    """Lazy-initialise the full runtime stack."""
    from ..storage.store import DataStore
    from ..schema.registry import SchemaRegistry
    from ..runtime.executor import Executor

    store = DataStore(data_dir)
    dbs   = store.load_databases()
    db    = dbs[0] if dbs else store.default_databases()[0]

    registry = SchemaRegistry()
    cached   = store.load_schema_cache(db.get("id", "db_default"))
    if cached:
        registry.load_from_list(cached)
    elif db.get("schema"):
        registry.load_from_list(db["schema"])
    else:
        registry.load_builtin()

    return Executor(db=db, registry=registry), store, dbs


# ─── HTTP handler ─────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler.  Registered globals: _executor, _store, _dbs."""

    log_message = lambda self, *a: None   # silence default access log

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._send_json(200, {"ok": True, "uptime": time.time() - _START_TIME,
                                   "runtime": "nexql-runtime/0.2.0"})
        elif path == "/schema":
            self._send_json(200, {"ok": True, "schema": _executor._registry.to_list()})
        else:
            self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        if path == "/execute":
            query      = body.get("query", "")
            user_role  = body.get("user_role", "user")
            variables  = body.get("variables") or {}
            op_name    = body.get("operation_name")
            result = _executor.execute_query(query, user_role, variables, op_name)
            self._send_json(200, result.to_dict())

        elif path == "/parse":
            from ..parser.parser import Parser
            query  = body.get("query", "")
            parsed = Parser().parse(query)
            from ..nexql_ast.nodes import ParseError
            if isinstance(parsed, ParseError):
                self._send_json(200, parsed.to_dict())
            else:
                self._send_json(200, parsed.to_dict())

        elif path == "/validate":
            from ..parser.parser import Parser
            from ..validator.validator import Validator
            query   = body.get("query", "")
            parsed  = Parser().parse(query)
            from ..nexql_ast.nodes import ParseError
            if isinstance(parsed, ParseError):
                self._send_json(200, {"ok": False,
                                       "errors": [{"code": "PARSE_ERROR",
                                                   "message": parsed.message}]})
                return
            vresult = Validator(_executor._registry.to_validator_map()).validate(parsed)
            self._send_json(200, vresult.to_dict())

        elif path == "/plan":
            from ..parser.parser import Parser
            from ..planner.planner import Planner
            query   = body.get("query", "")
            parsed  = Parser().parse(query)
            from ..nexql_ast.nodes import ParseError
            if isinstance(parsed, ParseError):
                self._send_json(200, {"ok": False, "error": parsed.message})
                return
            plan = Planner(_executor._registry).plan(parsed)
            self._send_json(200, {"ok": True, "plan": plan.to_dict()})

        elif path == "/tokens":
            from ..parser.parser import Parser
            query  = body.get("query", "")
            tokens = Parser().tokenize(query)
            self._send_json(200, {"ok": True, "tokens": tokens})

        else:
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})


# ─── Server bootstrap ─────────────────────────────────────────────────────────

_executor   = None
_store      = None
_dbs        = None
_START_TIME = time.time()


def start_server(port: int = 7433, data_dir: Optional[Path] = None) -> None:
    global _executor, _store, _dbs, _START_TIME
    _executor, _store, _dbs = _build_runtime(data_dir)
    _START_TIME = time.time()

    server = HTTPServer(("127.0.0.1", port), _Handler)
    print(f"NexQL Runtime Server listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.", flush=True)


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="NexQL Runtime Server")
    ap.add_argument("--port", type=int, default=7433)
    ap.add_argument("--data-dir", type=str, default=None)
    args = ap.parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else None
    start_server(port=args.port, data_dir=data_dir)


if __name__ == "__main__":
    main()
