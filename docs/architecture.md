# Piql Architecture Reference

**Version 0.2.0 — Post-Refactor**

---

## 1. Why the Refactor Was Necessary

The original `nexql_workbench.py` was a **5,718-line monolith** containing:

| Concern | Lines (approx.) |
|---|---|
| Tkinter UI class (`PiqlWorkbench`) | ~3,000 |
| Lexer + Parser (`PiqlParser`) | ~700 |
| Execution engine (`execute_nexql`) | ~350 |
| Schema inference / analysis | ~300 |
| Storage helpers | ~150 |
| Syntax highlighter | ~100 |
| AI helpers, snippets, env expansion | ~200 |
| Colour palette, constants | ~80 |

**The execution engine could not run without loading Tkinter.**  
That single fact made testing, standalone deployment, and language tooling impossible.

---

## 2. Target Architecture

```
nexql/
├── spec/             ← Language specification (EBNF grammar, type system)
├── ast/              ← Typed AST node definitions (dataclasses)
├── parser/           ← Lexer + recursive-descent parser
├── validator/        ← Semantic AST validation (schema-aware)
├── planner/          ← Query cost estimation + execution plan
├── runtime/          ← Executor, security, observability, AI helpers
├── schema/           ← Schema registry, inference, diff, search
├── storage/          ← JSON persistence, database CRUD
├── transport/        ← HTTP + stdio runtime server
├── sdk/              ← Multi-language SDK + webhook generation
├── plugins/          ← Plugin loader + lifecycle manager
├── ide/
│   ├── electron/     ← Electron app shell (main.js, package.json)
│   ├── frontend/     ← HTML/CSS/JS UI (index.html, preload.js)
│   └── ipc/          ← IPC bridge (bridge.js) — the ONLY glue layer
├── tests/            ← Comprehensive test suite
└── docs/             ← This file + migration guide
```

---

## 3. Data Flow — Query Pipeline

```
Raw query string
      │
      ▼
┌──────────────┐
│   Lexer      │  parser/lexer.py
│              │  Input:  str
│              │  Output: List[Token]
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Parser     │  parser/parser.py
│              │  Input:  List[Token]
│              │  Output: QueryDocument | ParseError
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Validator   │  validator/validator.py
│              │  Input:  QueryDocument + SchemaRegistry
│              │  Output: ValidationResult (ok, errors, warnings)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Planner    │  planner/planner.py
│              │  Input:  QueryDocument
│              │  Output: ExecutionPlan (steps, cost, strategy)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Executor   │  runtime/executor.py
│              │  Input:  ExecutionPlan + Database + ExecutionContext
│              │  Output: ExecutionResult (data, errors, metadata)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Serialiser  │  ExecutionResult.to_dict()
│              │  Output: JSON-ready dict
└──────────────┘
```

---

## 4. Module Responsibilities

### `spec/`
- **Owns**: The canonical language grammar (EBNF), type system table, directive reference.
- **Does not own**: Any code. This is documentation only.
- **Why separate**: The spec is the contract. Parser, validator, and runtime all derive behaviour from it. Keeping it as a standalone document allows language evolution without code changes.

### `ast/`
- **Owns**: `QueryDocument`, `FieldSelection`, `Directive`, `Method`, `ParseError`, `VariableRef`.
- **Does not own**: Parser logic, execution logic, UI code.
- **Why separate**: Every layer (parser → validator → planner → executor) communicates through typed AST nodes. Using plain dicts caused silent KeyErrors and made refactoring dangerous.

### `parser/`
- **Owns**: `Lexer` (token production), `Parser` (grammar → AST).
- **Does not own**: Schema knowledge, execution logic, UI.
- **Why separate**: A lexer must be reusable by the syntax highlighter without loading the parser. The parser must be testable without an executor.

### `validator/`
- **Owns**: Semantic rules — field existence, required fields, directive argument types, depth/complexity limits.
- **Does not own**: Grammar rules (parser's job), execution (runtime's job).
- **Why separate**: Grammar and semantics are orthogonal concerns. You can parse an invalid query successfully and then reject it in the validator with rich, schema-aware error messages.

### `planner/`
- **Owns**: Cost estimation, execution strategy selection (index scan vs full scan), plan serialisation for `EXPLAIN`.
- **Does not own**: Actual data access (executor's job).
- **Why separate**: Mirrors PostgreSQL's planner/executor split. A plan can be cached, inspected, and explained independently of execution.

### `runtime/`
- **Owns**: Executor, SecurityContext, RateLimiter, AuditLog, Trace, LatencyRecorder, AI helpers.
- **Does not own**: Parser, UI, IPC, Electron.
- **Why the heart**: The runtime is the only layer that touches data. All other layers produce inputs for it or consume its outputs.

### `schema/`
- **Owns**: `SchemaRegistry` — type definitions, field lookups, inference, diff, relationships, search.
- **Does not own**: Storage (registry is injected with data from storage), UI.
- **Why separate**: In the monolith, schema logic was split across 12+ free functions. Centralising it in a registry makes schema-aware tools (validator, planner, IDE panels) composable.

### `storage/`
- **Owns**: JSON file persistence, database CRUD, schema cache, history, snippets.
- **Does not own**: Query execution, schema analysis, UI.
- **Why separate**: The storage layer is a future extension point. Swapping JSON files for SQLite, Redis, or a cloud API should not touch the executor.

### `transport/`
- **Owns**: HTTP server, stdio server, request/response serialisation.
- **Does not own**: Query logic (delegates to runtime).
- **Why separate**: Transport is orthogonal to execution. The same runtime should be reachable over HTTP, WebSockets, Unix sockets, or stdio.

### `sdk/`
- **Owns**: Multi-language client code generation, webhook management, OpenAPI spec generation.
- **Does not own**: Runtime, UI.
- **Why separate**: SDK generation is a build-time / developer-tooling concern, not a runtime concern.

### `plugins/`
- **Owns**: Plugin discovery, lifecycle management, hook registration.
- **Does not own**: Plugin implementations (those live in plugin packages).
- **Why separate**: Plugin isolation is critical for stability. A bad plugin should not crash the runtime.

### `ide/`
- **Owns**: Electron shell, HTML/CSS/JS frontend, IPC bridge.
- **Does not own**: Any runtime logic.
- **Why separate**: The IDE is a **client** of the runtime, not part of it. This is the most important separation in the architecture.

---

## 5. IPC Communication Flow

```
Renderer Process (index.html)
  │
  │  window.api.invoke('nexql:execute', { query, db })
  ▼
preload.js  (contextBridge — exposes window.api)
  │
  │  ipcRenderer.invoke(channel, args)
  ▼
Main Process (main.js)
  │
  │  ipcMain.handle(channel, handler)
  ▼
ipc/bridge.js  (the ONLY file that imports Python)
  │
  │  callPython('runtime.server_entry', 'execute', [query, db])
  ▼
runtime/server_entry.py  (function registry + dispatch)
  │
  │  execute(query, db) → ExecutionResult
  ▼
runtime/executor.py
```

**Rules enforced by this architecture:**
1. The renderer **never** calls Python directly.
2. `bridge.js` **only** calls whitelisted functions in `server_entry.py`.
3. `server_entry.py` **only** calls into `nexql.*` modules — no Tkinter, no Electron.
4. The runtime **never** imports from `ide/`.

---

## 6. Runtime Lifecycle

```
Process start
     │
     ├─ Load SchemaRegistry (from DB or builtin)
     ├─ Initialise RateLimiter
     ├─ Initialise AuditLog
     ├─ Initialise LatencyRecorder
     ├─ Register plugins (from plugins/ loader)
     │
     ▼
Ready to serve requests
     │
     ┌───────────────────────────────────┐
     │  Per-request lifecycle            │
     │  1. Receive query + context       │
     │  2. RateLimiter.allow()           │
     │  3. Parser.parse()  → AST         │
     │  4. Validator.validate() → ok/err │
     │  5. Planner.plan()  → plan        │
     │  6. Executor.run()  → result      │
     │  7. AuditLog.record(event)        │
     │  8. LatencyRecorder.record()      │
     │  9. Return ExecutionResult        │
     └───────────────────────────────────┘
     │
Process shutdown
     ├─ Flush AuditLog buffer
     └─ Save schema cache
```

---

## 7. Dependency Graph (Inner → Outer)

```
spec   (no deps)
  └─ ast   (no deps)
      └─ parser   (→ ast)
          └─ validator   (→ ast, schema)
              └─ planner   (→ ast, schema)
                  └─ runtime   (→ ast, parser, validator, planner, schema, storage)
                      └─ transport   (→ runtime)
                      └─ sdk         (→ schema)
                      └─ plugins     (→ runtime)
                          └─ ide     (→ transport via IPC only)
```

**Key invariant**: No inner layer imports from an outer layer. The IDE never appears in a Python import chain.

---

## 8. Extension Points

| Feature | Extension Point |
|---|---|
| New directive | `spec/language.md` + `validator/` rule + `runtime/executor.py` handler |
| New storage backend | Implement `StorageEngine` interface in `storage/` |
| AI-assisted planner | Swap `Planner` implementation in `planner/` |
| Streaming execution | `transport/server.py` SSE endpoint + executor generator |
| Plugin | Drop into `plugins/installed/`, implement `PiqlPlugin` |
| New transport | Add server variant to `transport/` (WebSocket, gRPC, etc.) |
| New SDK language | Add generator to `sdk/generator.py` |
| Schema federation | Add federation resolver to `schema/registry.py` |

---

## 9. Migration Guide (From Monolith)

| Old location | New location | Notes |
|---|---|---|
| `PiqlParser` | `nexql/parser/parser.py` | Lexer split into `lexer.py` |
| `PiqlParser.tokenize` | `nexql/parser/lexer.py` | Standalone `Lexer` class |
| `execute_nexql` | `nexql/runtime/executor.py` | Now takes `ExecutionPlan` |
| `_infer_scalar_type` | `nexql/schema/registry.py` | Inside `SchemaRegistry` |
| `infer_schema_from_collections` | `nexql/schema/registry.py` | `registry.infer_from_collections()` |
| `analyze_schema_relationships` | `nexql/schema/registry.py` | `registry.relationships()` |
| `analyze_field_usage` | `nexql/schema/registry.py` | `registry.field_usage()` |
| `generate_api_docs` | `nexql/runtime/ai_helpers.py` | `generate_schema_docs()` |
| `schema_diff_text` | `nexql/schema/registry.py` | `registry.diff()` |
| `track_deprecations` | `nexql/schema/registry.py` | `registry.deprecated_fields()` |
| `smart_search_schema` | `nexql/schema/registry.py` | `registry.search()` |
| `nl_to_nexql_query` | `nexql/runtime/ai_helpers.py` | `nl_to_nexql()` |
| `benchmark_query_runs` | `nexql/runtime/server_entry.py` | `benchmark()` |
| `save_schema_cache` | `nexql/storage/store.py` | `StorageEngine.save_schema_cache()` |
| `foundation_features.py` | Split across `runtime/` and `storage/` | No single file; RateLimiter → `security.py`, cache → `storage/` |
| `ai_helpers.py` | `nexql/runtime/ai_helpers.py` | No UI imports |
| `security_features.py` | `nexql/runtime/security.py` | AuthProvider + AuditLog + guards |
| `observability_features.py` | `nexql/runtime/observability.py` | LatencyRecorder + trace |
| `sdk_integration_features.py` | `nexql/sdk/generator.py` | SDKGenerator + WebhookManager |
| `PiqlWorkbench` (Tkinter) | `nexql/ide/frontend/index.html` | Electron frontend |
| `main.js` | `nexql/ide/electron/main.js` | Electron shell only |
| `preload.js` | `nexql/ide/frontend/preload.js` | Context bridge |
| IPC handlers (inline in main.js) | `nexql/ide/ipc/bridge.js` | All IPC in one place |
| `_build_query_plan` | `nexql/planner/planner.py` | `Planner.plan()` |
| `_estimate_query_cost` | `nexql/planner/planner.py` | `Planner.estimate_cost()` |
| `highlight_rules` | `nexql/ide/frontend/index.html` | Client-side JS only |
| `_lint_query` | `nexql/validator/validator.py` | `Validator.lint()` |
| `C = dict(...)` colour palette | `nexql/ide/frontend/index.html` | CSS variables |
| `MOCK_NAMES`, `_mock()` | `nexql/storage/store.py` | Seed data in `StorageEngine` |
