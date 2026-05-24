/**
 * nexql/ide/frontend/preload.js
 * ─────────────────────────────
 * Electron context bridge: exposes a minimal, typed API to the renderer.
 *
 * SECURITY PRINCIPLES:
 *   • nodeIntegration is false — the renderer cannot require() Node modules
 *   • contextIsolation is true — renderer runs in isolated JS context
 *   • Only explicitly listed channels are forwarded (no wildcard passthrough)
 *
 * The API surface exposed here is the renderer's ONLY way to reach the
 * main process.  Keep it small and explicit.
 */

'use strict';

const { contextBridge, ipcRenderer, clipboard } = require('electron');

// Channels the renderer is allowed to INVOKE (request/response pattern)
const ALLOWED_INVOKE_CHANNELS = new Set([
  // Persistence
  'db:load', 'db:save', 'db:create',
  'history:load', 'history:save',
  'snippets:load', 'snippets:save',
  'file:save', 'file:open',

  // Runtime
  'nexql:execute', 'nexql:parse', 'nexql:validate', 'nexql:tokens', 'nexql:plan',

  // AI
  'ai:debug', 'ai:nl-query', 'ai:optimize', 'ai:autocomplete',

  // Schema
  'schema:relationships', 'schema:diff', 'schema:search',
  'schema:infer', 'schema:explain', 'schema:api-docs',
  'schema:deprecations', 'schema:permissions',

  // Security
  'security:depth-check', 'security:audit-log',

  // Observability
  'obs:latency', 'obs:execution-graph',

  // SDK
  'sdk:generate', 'sdk:webhook-create',

  // Team
  'team:create', 'team:analytics',

  // Visualization
  'viz:schema-graph', 'viz:erd',

  // Tools
  'benchmark:run', 'benchmark:mock',
]);

// Channels the renderer can LISTEN to (events pushed from main)
const ALLOWED_ON_CHANNELS = new Set([
  'new-tab', 'open-file', 'request-save',
  'execute-query', 'format-query', 'clear-editor',
  'explain-query', 'show-ast', 'show-plan', 'show-diff',
  'open-file-dialog',
  'open-panel:aiDebug', 'open-panel:aiNL', 'open-panel:aiOptimize',
  'open-panel:aiSchemaExplain', 'open-panel:schemaBrowser',
  'open-panel:schemaDiff', 'open-panel:vizGraph', 'open-panel:vizERD',
  'open-panel:fieldUsage', 'open-panel:apiDocs', 'open-panel:secDepth',
  'open-panel:secAudit', 'open-panel:secPermissions',
  'open-panel:createTeam', 'open-panel:createWorkspace',
  'open-panel:teamAnalytics', 'open-panel:orchestrate',
  'open-panel:benchmark', 'open-panel:mockQuery',
  'open-panel:sdkGenerate', 'open-panel:sdkWebhook',
  'open-panel:obsLatency', 'open-panel:obsGraph', 'open-panel:help',
]);

contextBridge.exposeInMainWorld('api', {
  /**
   * Send a request to the main process and await a response.
   * Only channels in ALLOWED_INVOKE_CHANNELS are forwarded.
   */
  invoke(channel, args) {
    if (!ALLOWED_INVOKE_CHANNELS.has(channel)) {
      return Promise.reject(new Error(`Channel '${channel}' is not allowed`));
    }
    return ipcRenderer.invoke(channel, args);
  },

  /**
   * Listen for an event pushed from the main process.
   * Returns an unsubscribe function.
   */
  on(channel, listener) {
    if (!ALLOWED_ON_CHANNELS.has(channel)) {
      console.warn(`[preload] Blocked listener for unknown channel: ${channel}`);
      return () => {};
    }
    const cb = (_event, ...args) => listener(...args);
    ipcRenderer.on(channel, cb);
    return () => ipcRenderer.removeListener(channel, cb);
  },

  /** Write text to the system clipboard. */
  copy(text) {
    try { clipboard.writeText(String(text)); return true; }
    catch { return false; }
  },
});
