/**
 * nexql/ide/electron/main.js
 * ──────────────────────────
 * Electron main process entry point.
 *
 * RESPONSIBILITIES (and ONLY these):
 *   • App lifecycle (ready, window-all-closed, activate)
 *   • BrowserWindow creation and configuration
 *   • Application menu setup
 *   • Delegating IPC handler registration to ide/ipc/bridge.js
 *
 * WHAT IS INTENTIONALLY NOT HERE:
 *   • Execution logic     → runtime/executor.py
 *   • File I/O helpers    → ide/ipc/bridge.js
 *   • Python bridge code  → ide/ipc/bridge.js
 *   • Snippet defaults    → storage/store.py
 *   • Schema analysis     → schema/registry.py
 *
 * COMMUNICATION MODEL:
 *   Renderer (HTML/JS) ──IPC──► bridge.js ──subprocess──► Python runtime
 *   Python runtime ──────────► bridge.js ──────────────► Renderer
 */

'use strict';

const { app, BrowserWindow, Menu, dialog } = require('electron');
const path = require('path');
const { registerHandlers } = require('../ipc/bridge');

// Disable GPU acceleration to avoid driver errors on headless/CI environments
app.disableHardwareAcceleration();

let mainWindow = null;

// ─── Window factory ───────────────────────────────────────────────────────────

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width:           1400,
    height:          900,
    minWidth:        900,
    minHeight:       600,
    title:           'Piql Workbench',
    backgroundColor: '#0d1117',
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
      preload:          path.join(__dirname, '../frontend/preload.js'),
    },
    show: false,
  });

  mainWindow.loadFile(path.join(__dirname, '../frontend/index.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
}

// ─── Application menu ─────────────────────────────────────────────────────────

function buildMenu() {
  const send = (channel) => mainWindow?.webContents.send(channel);

  const template = [
    {
      label: 'File', submenu: [
        { label: 'New Tab',         accelerator: 'CmdOrCtrl+T',     click: () => send('new-tab') },
        { label: 'Open Query...',   accelerator: 'CmdOrCtrl+O',     click: () => send('open-file-dialog') },
        { label: 'Save Query...',   accelerator: 'CmdOrCtrl+S',     click: () => send('request-save') },
        { type: 'separator' },
        { label: 'Quit',            accelerator: 'CmdOrCtrl+Q',     role: 'quit' },
      ],
    },
    {
      label: 'Edit', submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'Query', submenu: [
        { label: 'Execute Query',   accelerator: 'CmdOrCtrl+Return', click: () => send('execute-query') },
        { label: 'Format Query',    accelerator: 'CmdOrCtrl+Shift+F',click: () => send('format-query') },
        { label: 'Explain Query',                                      click: () => send('explain-query') },
        { label: 'Clear Editor',                                       click: () => send('clear-editor') },
        { type: 'separator' },
        { label: 'Show AST',                                           click: () => send('show-ast') },
        { label: 'Show Plan',                                          click: () => send('show-plan') },
        { label: 'Show Diff',                                          click: () => send('show-diff') },
      ],
    },
    {
      label: 'AI', submenu: [
        { label: 'Debug Assistant',           click: () => send('open-panel:aiDebug') },
        { label: 'Natural Language → Query',  click: () => send('open-panel:aiNL') },
        { label: 'Optimize Query',            click: () => send('open-panel:aiOptimize') },
        { label: 'Schema Explainer',          click: () => send('open-panel:aiSchemaExplain') },
      ],
    },
    {
      label: 'Schema', submenu: [
        { label: 'Schema Browser',            click: () => send('open-panel:schemaBrowser') },
        { label: 'Schema Diff',               click: () => send('open-panel:schemaDiff') },
        { label: 'Relationship Graph',        click: () => send('open-panel:vizGraph') },
        { label: 'Entity Relationship Diagram', click: () => send('open-panel:vizERD') },
        { label: 'Field Usage Analytics',     click: () => send('open-panel:fieldUsage') },
        { label: 'API Docs',                  click: () => send('open-panel:apiDocs') },
      ],
    },
    {
      label: 'Security', submenu: [
        { label: 'Query Depth Check',         click: () => send('open-panel:secDepth') },
        { label: 'Audit Log',                 click: () => send('open-panel:secAudit') },
        { label: 'Permission Map',            click: () => send('open-panel:secPermissions') },
      ],
    },
    {
      label: 'Team', submenu: [
        { label: 'Create Team',               click: () => send('open-panel:createTeam') },
        { label: 'Create Workspace',          click: () => send('open-panel:createWorkspace') },
        { label: 'Team Analytics',            click: () => send('open-panel:teamAnalytics') },
        { label: 'Orchestration',             click: () => send('open-panel:orchestrate') },
      ],
    },
    {
      label: 'Tools', submenu: [
        { label: 'Benchmark',                 click: () => send('open-panel:benchmark') },
        { label: 'Mock Query',                click: () => send('open-panel:mockQuery') },
        { label: 'SDK Generator',             click: () => send('open-panel:sdkGenerate') },
        { label: 'Webhook Manager',           click: () => send('open-panel:sdkWebhook') },
        { label: 'Latency Statistics',        click: () => send('open-panel:obsLatency') },
        { label: 'Execution Graph',           click: () => send('open-panel:obsGraph') },
      ],
    },
    {
      label: 'View', submenu: [
        { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'resetZoom' },
        { type: 'separator' },
        { label: 'Toggle DevTools', accelerator: 'F12',
          click: () => mainWindow?.webContents.toggleDevTools() },
        { role: 'reload' },
      ],
    },
    {
      label: 'Help', submenu: [
        { label: 'Piql Syntax Reference',     click: () => send('open-panel:help') },
        {
          label: 'About Piql Workbench',
          click: () => dialog.showMessageBox(mainWindow, {
            type: 'info', title: 'About Piql Workbench',
            message: 'Piql Workbench v0.2.0',
            detail: 'Professional IDE for Piql — Packed Query Language\n' +
                    'Parser · Runtime · Schema · IDE architecture\n' +
                    'Runtime is fully independent of the IDE layer.',
          }),
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  registerHandlers();   // All IPC logic lives in bridge.js
  createMainWindow();
  buildMenu();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});
