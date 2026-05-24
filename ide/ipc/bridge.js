/**
 * nexql/ide/ipc/bridge.js
 * ───────────────────────
 * IPC bridge between the Electron main process and the Piql Python runtime.
 *
 * WHY THIS IS ITS OWN MODULE:
 *   In the original main.js, IPC handlers were mixed with:
 *     • window creation
 *     • application menu building
 *     • file I/O helpers
 *     • snippet defaults
 *     • the Python bridge function itself
 *
 *   This made it impossible to test IPC logic without spinning up an Electron
 *   window, and impossible to swap the Python bridge for a socket connection
 *   (e.g. to the transport/server.py server) without touching UI code.
 *
 * COMMUNICATION FLOW:
 *   Renderer → contextBridge.api.invoke(channel, args)
 *     → ipcRenderer.invoke
 *       → ipcMain.handle (THIS FILE)
 *         → callRuntime() or callPython()
 *           → Python process / HTTP server
 *             → result JSON
 *           ← result JSON
 *         ← ipcMain handler returns JSON
 *       ← ipcRenderer resolves promise
 *     ← api.invoke resolves
 *   Renderer receives result
 *
 * SCALABILITY HOOK:
 *   Replace callPython() with callRuntimeServer() to switch from
 *   exec()-based Python bridge to the HTTP transport server.
 */

'use strict';

const { ipcMain, dialog, BrowserWindow } = require('electron');
const { execFileSync } = require('child_process');
const fs   = require('fs');
const path = require('path');
const os   = require('os');

// ─── Data directory ──────────────────────────────────────────────────────────

const DATA_DIR      = path.join(os.homedir(), '.piql-workbench');
const LEGACY_DATA_DIR = path.join(os.homedir(), '.nexql-workbench');
const DB_FILE       = path.join(DATA_DIR, 'databases.json');
const HISTORY_FILE  = path.join(DATA_DIR, 'history.json');
const SNIPPETS_FILE = path.join(DATA_DIR, 'snippets.json');
const LEGACY_DB_FILE       = path.join(LEGACY_DATA_DIR, 'databases.json');
const LEGACY_HISTORY_FILE  = path.join(LEGACY_DATA_DIR, 'history.json');
const LEGACY_SNIPPETS_FILE = path.join(LEGACY_DATA_DIR, 'snippets.json');

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

function loadJSON(file, fallback) {
  try   { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return fallback; }
}
function isStarterDatabaseSet(data) {
  return Array.isArray(data)
    && data.length === 1
    && data[0]
    && data[0].id === 'db_default'
    && data[0].name === 'default';
}
function loadJSONWithLegacy(file, legacyFile, fallback) {
  const current = loadJSON(file, null);
  if (file === DB_FILE && isStarterDatabaseSet(current)) {
    const legacy = loadJSON(legacyFile, null);
    if (legacy != null && Array.isArray(legacy) && legacy.length > 0 && !isStarterDatabaseSet(legacy)) {
      try {
        fs.writeFileSync(file, JSON.stringify(legacy, null, 2));
      } catch {
        // Best-effort migration only; still return the legacy data.
      }
      return legacy;
    }
  }
  if (current != null && (!(Array.isArray(current)) || current.length || Object.keys(current).length)) {
    return current;
  }
  const legacy = loadJSON(legacyFile, null);
  if (legacy != null && (!(Array.isArray(legacy)) || legacy.length || Object.keys(legacy).length)) {
    try {
      fs.writeFileSync(file, JSON.stringify(legacy, null, 2));
    } catch {
      // Best-effort migration only; still return the legacy data.
    }
    return legacy;
  }
  return fallback;
}
function saveJSON(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
}

// ─── Python bridge ───────────────────────────────────────────────────────────
// Allowed modules whitelist prevents arbitrary code execution via IPC.

const ALLOWED_MODULES = new Set([
  'nexql_workbench',         // legacy monolith (compatibility layer)
  'nexql.runtime.executor',  // new modular runtime
  'ai_helpers',
  'security_features',
  'sdk_integration_features',
  'team_enterprise_features',
  'observability_features',
  'visualization_features',
]);

/**
 * Execute a Python function via stdin JSON bridge.
 * Returns the parsed result object.
 */
function callPython(moduleName, functionName, args = [], kwargs = {}) {
  if (!ALLOWED_MODULES.has(String(moduleName))) {
    throw new Error(`Module '${moduleName}' is not in the allowed list`);
  }

  // __dirname = <repo>/ide/ipc, so repo root is two levels up.
  const projectRoot = path.resolve(__dirname, '../..');
  const packageParent = path.dirname(projectRoot);
  const pyEntries = [projectRoot, packageParent];
  if (process.env.PYTHONPATH) pyEntries.push(process.env.PYTHONPATH);
  const pyPath = pyEntries.join(':');

  const bridge = [
    'import importlib, json, sys, traceback',
    'payload = json.loads(sys.stdin.read() or "{}")',
    'module = importlib.import_module(payload["module"])',
    'fn = getattr(module, payload["function"])',
    'result = fn(*payload.get("args", []), **payload.get("kwargs", {}))',
    'if isinstance(result, (bytes, bytearray)):',
    '    result = {"bytes": len(result), "preview_hex": result[:64].hex()}',
    'print(json.dumps({"ok": True, "result": result}, default=str))',
  ].join('\n');

  const payload = JSON.stringify({ module: moduleName, function: functionName, args, kwargs });
  const raw = execFileSync('python3', ['-c', bridge], {
    input:     payload,
    encoding:  'utf8',
    cwd:       projectRoot,
    env:       { ...process.env, PYTHONPATH: pyPath },
    maxBuffer: 10 * 1024 * 1024,
  });

  const parsed = JSON.parse(raw);
  if (!parsed.ok) throw new Error(parsed.error || 'Python bridge failed');
  return parsed.result;
}

/**
 * Future: replace callPython() with this when transport server is running.
 */
async function callRuntimeServer(endpoint, body) {
  const resp = await fetch(`http://127.0.0.1:7433${endpoint}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  return resp.json();
}

// ─── IPC handler registration ─────────────────────────────────────────────────

function registerHandlers() {

  // ── Persistence ─────────────────────────────────────────────────────────

  ipcMain.handle('db:load', () => loadJSONWithLegacy(DB_FILE, LEGACY_DB_FILE, []));
  ipcMain.handle('db:save', (_, data) => { saveJSON(DB_FILE, data); return true; });
  ipcMain.handle('db:create', (_, { name, description }) => {
    if (!name?.trim()) throw new Error('Database name is required');
    const all = loadJSON(DB_FILE, []);
    if (all.some(d => d.name === name.trim()))
      throw new Error('A database with that name already exists');
    const db = {
      id: 'db_' + Date.now(), name: name.trim(), description: description || '',
      collections: { users: [], posts: [], messages: [] },
      schema: null, createdAt: Date.now(),
    };
    all.push(db);
    saveJSON(DB_FILE, all);
    return db;
  });

  ipcMain.handle('history:load',  () => loadJSONWithLegacy(HISTORY_FILE, LEGACY_HISTORY_FILE, []));
  ipcMain.handle('history:save',  (_, data) => { saveJSON(HISTORY_FILE, data); return true; });
  ipcMain.handle('snippets:load', () => loadJSONWithLegacy(SNIPPETS_FILE, LEGACY_SNIPPETS_FILE, []));
  ipcMain.handle('snippets:save', (_, data) => { saveJSON(SNIPPETS_FILE, data); return true; });

  // ── File dialogs ──────────────────────────────────────────────────────────

  ipcMain.handle('file:save', async (_, { content }) => {
    const win    = BrowserWindow.getFocusedWindow();
    const result = await dialog.showSaveDialog(win, {
      defaultPath: 'query.piql',
      filters: [{ name: 'Piql', extensions: ['nexql', 'nql', 'txt'] }],
    });
    if (!result.canceled) {
      fs.writeFileSync(result.filePath, content);
      return result.filePath;
    }
    return null;
  });

  ipcMain.handle('file:open', async (_) => {
    const win    = BrowserWindow.getFocusedWindow();
    const result = await dialog.showOpenDialog(win, {
      filters: [{ name: 'Piql', extensions: ['nexql', 'nql', 'txt'] }],
    });
    if (!result.canceled && result.filePaths.length) {
      return { path: result.filePaths[0], content: fs.readFileSync(result.filePaths[0], 'utf8') };
    }
    return null;
  });

  // ── Core runtime calls ───────────────────────────────────────────────────
  // All execution goes through execute_nexql_with_state so the updated
  // db snapshot is returned to the renderer on every mutation.

  ipcMain.handle('nexql:execute', async (_, { query, db }) => {
    return callPython('nexql_workbench', 'execute_nexql_with_state', [
      String(query || ''), db || {},
    ]);
  });

  ipcMain.handle('nexql:parse', (_, { query }) =>
    callPython('nexql_workbench', '_cli_parse_entry', [String(query || '')]));

  ipcMain.handle('nexql:validate', (_, { query }) =>
    callPython('nexql_workbench', '_cli_validate_entry', [String(query || '')]));

  ipcMain.handle('nexql:tokens', (_, { query }) =>
    callPython('nexql_workbench', '_cli_tokens_entry', [String(query || '')]));

  ipcMain.handle('nexql:plan', (_, { query }) =>
    callPython('nexql_workbench', '_cli_plan_entry', [String(query || '')]));

  // ── AI helpers ───────────────────────────────────────────────────────────

  ipcMain.handle('ai:debug',     (_, { input }) =>
    callPython('ai_helpers', 'ai_debug_assistant',
      [String(input || ''), { ok: false, errors: [{ code: 'USER_INPUT', message: String(input || '') }] }]));

  ipcMain.handle('ai:nl-query',  (_, { prompt, schema }) =>
    callPython('nexql_workbench', 'nl_to_nexql_query', [String(prompt || ''), schema || []]));

  ipcMain.handle('ai:optimize',  (_, { query, schema }) =>
    callPython('ai_helpers', 'ai_optimize_query', [String(query || ''), schema || []]));

  ipcMain.handle('ai:autocomplete', (_, { prefix, schema }) =>
    callPython('ai_helpers', 'ai_query_autocomplete', [String(prefix || ''), schema || []]));

  // ── Schema analysis ───────────────────────────────────────────────────────

  ipcMain.handle('schema:relationships', (_, { schema }) =>
    callPython('nexql_workbench', 'analyze_schema_relationships', [schema || []]));

  ipcMain.handle('schema:diff',     (_, { old: o, current: c }) =>
    callPython('nexql_workbench', 'schema_diff_text', [o || [], c || []]));

  ipcMain.handle('schema:search',   (_, { schema, term }) =>
    callPython('nexql_workbench', 'smart_search_schema', [schema || [], String(term || '')]));

  ipcMain.handle('schema:infer',    (_, { collections }) =>
    callPython('nexql_workbench', 'infer_schema_from_collections', [collections || {}]));

  ipcMain.handle('schema:explain',  (_, { schema, focus }) =>
    callPython('nexql_workbench', 'explain_schema_ai_style', [schema || [], String(focus || '')]));

  ipcMain.handle('schema:api-docs', (_, { schema }) =>
    callPython('nexql_workbench', 'generate_api_docs', [schema || []]));

  ipcMain.handle('schema:deprecations', (_, { schema }) =>
    callPython('nexql_workbench', 'track_deprecations', [schema || []]));

  ipcMain.handle('schema:permissions', (_, { schema }) =>
    callPython('nexql_workbench', 'visualize_permissions', [schema || []]));

  // ── Security ──────────────────────────────────────────────────────────────

  ipcMain.handle('security:depth-check', (_, { ast, maxDepth }) =>
    callPython('security_features', 'query_depth_limiter', [ast || {}, maxDepth || 5]));

  ipcMain.handle('security:audit-log', (_) =>
    callPython('security_features', 'get_audit_log', []));

  // ── Observability ─────────────────────────────────────────────────────────

  ipcMain.handle('obs:latency',      (_, { history }) =>
    callPython('observability_features', 'calculate_latency_stats', [history || []]));

  ipcMain.handle('obs:execution-graph', (_, { history }) =>
    callPython('observability_features', 'get_execution_graph', [history || []]));

  // ── SDK generation ────────────────────────────────────────────────────────

  ipcMain.handle('sdk:generate', (_, { language, schema, config }) =>
    callPython('sdk_integration_features', 'generate_sdk',
      [String(language || 'python'), schema || {}, config || {}]));

  ipcMain.handle('sdk:webhook-create', (_, { name, url, events }) =>
    callPython('sdk_integration_features', 'create_webhook',
      [String(name || ''), String(url || ''), events || []]));

  // ── Team / Enterprise ─────────────────────────────────────────────────────

  ipcMain.handle('team:create',    (_, { name, members }) =>
    callPython('team_enterprise_features', 'create_team', [String(name || ''), members || []]));

  ipcMain.handle('team:analytics', (_, { teamId }) =>
    callPython('team_enterprise_features', 'get_team_analytics', [String(teamId || '')]));

  // ── Visualization ─────────────────────────────────────────────────────────

  ipcMain.handle('viz:schema-graph', (_, { schema }) =>
    callPython('visualization_features', 'build_schema_graph', [schema || []]));

  ipcMain.handle('viz:erd', (_, { schema }) =>
    callPython('visualization_features', 'build_erd', [schema || []]));

  // ── Benchmarking ─────────────────────────────────────────────────────────

  ipcMain.handle('benchmark:run', (_, { query, db, runs }) =>
    callPython('nexql_workbench', 'benchmark_query_runs',
      [String(query || ''), db || {}, Number(runs) || 10]));

  ipcMain.handle('benchmark:mock', (_, { query, db }) =>
    callPython('nexql_workbench', 'generate_mock_query_response',
      [String(query || ''), db || {}]));
}

module.exports = { registerHandlers };
