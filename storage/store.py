"""
nexql/storage/store.py
──────────────────────
Storage layer: persistence, seed data, and schema cache.

WHY THIS EXISTS SEPARATELY:
  In the monolith, file I/O was done with load_json/save_json helpers
  defined at the top level of nexql_workbench.py, then called from:
    • the PiqlWorkbench Tkinter class
    • the execution engine
    • the schema cache functions
    • the foundation_features module

  This created tight coupling between the UI state management and
  persistence.  The storage layer owns all disk I/O; nothing else does.

PUBLIC API:
  DataStore(data_dir)
    .load_databases()       → list[dict]
    .save_databases(dbs)    → bool
    .load_history()         → list[dict]
    .save_history(hist)     → bool
    .load_snippets()        → list[dict]
    .save_snippets(snips)   → bool
    .load_env()             → dict
    .save_env(env)          → bool
    .load_schema_cache(id)  → dict | None
    .save_schema_cache(id, schema) → bool
    .default_databases()    → list[dict]
    .default_snippets()     → list[dict]
"""

from __future__ import annotations
import copy
import json
import random
import string
import time
from pathlib import Path
from typing import Any, Optional


class DataStore:
    """All file-system operations for the Piql runtime and IDE."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or Path.home() / ".piql-workbench"
        self._legacy_data_dir = Path.home() / ".nexql-workbench"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._schema_cache_dir = self.data_dir / "schema-cache"
        self._schema_cache_dir.mkdir(exist_ok=True)

    # ── Generic helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_starter_database_set(data: Any) -> bool:
        return (
            isinstance(data, list)
            and len(data) == 1
            and isinstance(data[0], dict)
            and data[0].get("id") == "db_default"
            and data[0].get("name") == "default"
        )

    def _read(self, filename: str, fallback: Any) -> Any:
        path = self.data_dir / filename
        if filename == "databases.json":
            try:
                current = json.loads(path.read_text())
            except Exception:
                current = None

            if self._is_starter_database_set(current):
                legacy_path = self._legacy_data_dir / filename
                try:
                    legacy = json.loads(legacy_path.read_text())
                    if isinstance(legacy, list) and legacy and not self._is_starter_database_set(legacy):
                        try:
                            path.write_text(json.dumps(legacy, indent=2))
                        except Exception:
                            pass
                        return legacy
                except Exception:
                    pass

        try:
            return json.loads(path.read_text())
        except Exception:
            legacy_path = self._legacy_data_dir / filename
            try:
                data = json.loads(legacy_path.read_text())
                try:
                    path.write_text(json.dumps(data, indent=2))
                except Exception:
                    pass
                return data
            except Exception:
                return copy.deepcopy(fallback) if isinstance(fallback, (dict, list)) else fallback

    def _write(self, filename: str, data: Any) -> bool:
        try:
            (self.data_dir / filename).write_text(json.dumps(data, indent=2))
            return True
        except Exception:
            return False

    # ── Databases ─────────────────────────────────────────────────────────────

    def load_databases(self) -> list[dict]:
        dbs = self._read("databases.json", [])
        if not dbs:
            dbs = self.default_databases()
        return dbs

    def save_databases(self, databases: list[dict]) -> bool:
        return self._write("databases.json", databases)

    # ── History ───────────────────────────────────────────────────────────────

    def load_history(self) -> list[dict]:
        return self._read("history.json", [])

    def save_history(self, history: list[dict]) -> bool:
        return self._write("history.json", history)

    # ── Snippets ──────────────────────────────────────────────────────────────

    def load_snippets(self) -> list[dict]:
        snips = self._read("snippets.json", [])
        return snips if snips else self.default_snippets()

    def save_snippets(self, snippets: list[dict]) -> bool:
        return self._write("snippets.json", snippets)

    # ── Environment variables ─────────────────────────────────────────────────

    def load_env(self) -> dict:
        return self._read("env.json", {})

    def save_env(self, env: dict) -> bool:
        return self._write("env.json", env)

    # ── Schema cache ──────────────────────────────────────────────────────────

    def _schema_cache_path(self, db_id: str) -> Path:
        import re
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", db_id or "default")
        return self._schema_cache_dir / f"{safe}.json"

    def load_schema_cache(self, db_id: str) -> Optional[list]:
        try:
            data = json.loads(self._schema_cache_path(db_id).read_text())
            return data
        except Exception:
            return None

    def save_schema_cache(self, db_id: str, schema: list) -> bool:
        try:
            self._schema_cache_path(db_id).write_text(json.dumps(schema, indent=2))
            return True
        except Exception:
            return False

    # ── Defaults ──────────────────────────────────────────────────────────────

    def default_databases(self) -> list[dict]:
        return [{
            "id":          "db_default",
            "name":        "default",
            "description": "Default empty database",
            "collections": {"users": [], "posts": [], "messages": [], "orgs": []},
            "schema":      None,
            "createdAt":   int(time.time()),
        }]

    @staticmethod
    def default_snippets() -> list[dict]:
        return [
            {"id": "s01", "name": "Simple Fetch", "category": "Query",
             "code": '? user (id "u_0001") { name email createdAt }'},
            {"id": "s02", "name": "List with Limit", "category": "Query",
             "code": '? posts ($limit 10) { id title score status createdAt }'},
            {"id": "s03", "name": "Nested Fetch", "category": "Query",
             "code": '? user (id "u_0001") { name email posts ($limit 5) { title score } }'},
            {"id": "s04", "name": "Cursor Pagination", "category": "Query",
             "code": '? posts ($after "cursor_abc" $limit 25 $sort createdAt desc) { id title score }'},
            {"id": "s05", "name": "Filter + Sort", "category": "Query",
             "code": '? users (age >= 18 $sort age desc $limit 5) { id name age }'},
            {"id": "s06", "name": "OR Filter", "category": "Query",
             "code": '? users (role admin) or (role moderator) { id name role }'},
            {"id": "s07", "name": "Create Record", "category": "Mutation",
             'code': '+ post {\n  title "New Post"\n  body  "Content here..."\n  tags  ["piql" "api"]\n  authorId "u_0001"\n} { id createdAt }'},
            {"id": "s08", "name": "Update Record", "category": "Mutation",
             "code": '~ post (id "p_0001") {\n  title "Updated Title"\n  status published\n} { id updatedAt }'},
            {"id": "s09", "name": "Delete Record", "category": "Mutation",
             "code": '! post (id "p_0001") { id }'},
              {"id": "s10", "name": "Delete Field", "category": "Mutation",
               "code": '~ user (id "u_0001") {\n  !settings\n  profile { !bio }\n} { id }'},
              {"id": "s11", "name": "Subscribe", "category": "Subscription",
             "code": '>> messages { id body authorId createdAt } @rate(max 10 per second)'},
              {"id": "s12", "name": "Columnar Mode", "category": "Advanced",
             "code": '? posts ($limit 20) @cols {\n  id title score createdAt\n}'},
              {"id": "s13", "name": "Field-level Auth", "category": "Advanced",
             "code": '? user (id "u_0001") {\n  name\n  secret @auth(role admin)\n  email @cache(ttl 300)\n}'},
              {"id": "s14", "name": "Wildcard", "category": "Query",
             "code": '? user (id "u_0001") { * }'},
              {"id": "s15", "name": "Inline Type Condition", "category": "Advanced",
             "code": '? node (id "n_001") {\n  id\n  ... on User { name email }\n  ... on Post { title score }\n}'},
        ]
