'use strict';

// Electron entry point for the packaged REVIEW build.
//
// The approved window behaviour (console art, 80% home/workspace sizing, 28px
// menu labels, close/unsaved guards) is the shipped one: this file keeps the
// original geometry functions and reuses the original menu and preload modules
// unchanged. What differs is only who starts the servers.
//
// The original main.js deliberately does not start Python: a browser-tab
// rehearsal expects the researcher to run run_mock.py. A packaged .app has no
// project folder and no terminal, so this shell starts the bundled backend as
// its own child on free loopback ports and stops exactly that child on exit.
// It never reuses an already running server and never assumes one exists.

const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { app, BrowserWindow, ipcMain, screen, shell, dialog } = require('electron');
const existingUserData = app.getPath('userData');
app.setName('Research Workbench Review');
app.setPath('userData', existingUserData);
const { buildMenu, openChipDialog, deliverChip, deliverNewChip } = require('./menu');

const SMOKE = process.argv.includes('--smoke');
const BUNDLE_ROOT = path.join(path.dirname(app.getAppPath()), 'wb');
const PYTHON = path.join(BUNDLE_ROOT, 'runtime', 'python', 'bin', 'python3.14');
const BACKEND = path.join(BUNDLE_ROOT, 'packaging', 'backend_server.py');
const READY_TIMEOUT_MS = 90000;

function loopbackUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password) {
    throw new Error('Workbench launch URLs must use HTTP loopback without credentials.');
  }
  return url;
}

function windowSize(mode, area) {
  if (mode === 'menu') {
    return { width: Math.round(area.width * 0.768), height: Math.round(area.height * 0.752) };
  }
  if (mode !== 'boot' && mode !== 'home') return null;
  const width = Math.round(Math.min(area.width * 0.72, (area.height * 0.72 - 20) * 1488 / 656 + 20));
  return { width, height: Math.round((width - 20) * 656 / 1488 + 20) };
}

const TWEEN_MS = 220;
const TWEEN_STEP_MS = 16;

let mainWindow = null;
let tweenToken = 0;
let smokeStarted = false;
let windowMode = 'boot';
let closePromptOpen = false;
let backend = null;
let backendExit = null;

const UNSAVED_CLOSE = {
  message: 'Your writing has unsaved changes.',
  detail: 'Choose “Keep editing” to save them first. “Close without saving” discards the unsaved text in this window, and that cannot be undone.',
  keepEditing: 'Keep editing',
  discard: 'Close without saving',
};

function log(...args) {
  console.log('[electron]', ...args);
}

// -- bundled backend lifecycle ----------------------------------------------

function startBackend() {
  if (!fs.existsSync(PYTHON) || !fs.existsSync(BACKEND)) {
    throw new Error('The bundled review backend is missing from this app.');
  }
  // A fresh session directory per launch: a new sample database, never a
  // previous session's file and never the source tree's local_rehearsal.db.
  const session = new Date().toISOString().replace(/[:.]/g, '-');
  const stateDir = path.join(app.getPath('userData'), 'review-sessions', session);
  fs.mkdirSync(stateDir, { recursive: true });
  const readyFile = path.join(stateDir, 'ready.json');

  const child = spawn(PYTHON, [BACKEND,
    '--state-dir', stateDir,
    '--ready-file', readyFile,
    '--parent-pid', String(process.pid),
  ], {
    cwd: path.dirname(BACKEND),
    env: { ...process.env, PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONHOME: '' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (chunk) => process.stdout.write(`[backend] ${chunk}`));
  child.stderr.on('data', (chunk) => process.stderr.write(`[backend] ${chunk}`));
  child.on('exit', (code, signal) => {
    backendExit = { code, signal };
    log('backend exited', code, signal);
  });
  backend = child;
  return { child, readyFile, stateDir };
}

function stopBackend() {
  const child = backend;
  backend = null;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  try {
    child.kill('SIGTERM');
  } catch (err) {
    log('backend stop failed', String(err && err.message || err));
  }
}

function waitForReady(readyFile) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    const tick = () => {
      if (backendExit) {
        reject(new Error(`The local review backend stopped before it was ready (exit ${backendExit.code}).`));
        return;
      }
      if (fs.existsSync(readyFile)) {
        try {
          const info = JSON.parse(fs.readFileSync(readyFile, 'utf8'));
          if (info && info.app_url) { resolve(info); return; }
        } catch (err) {
          // Mid-write is impossible (the file is renamed into place), so any
          // parse failure is retried only until the deadline.
        }
      }
      if (Date.now() > deadline) {
        reject(new Error('The local review backend did not become ready in time.'));
        return;
      }
      setTimeout(tick, 150);
    };
    tick();
  });
}

// -- window ----------------------------------------------------------------

function clampToWorkArea(win, width, height) {
  const area = screen.getDisplayMatching(win.getBounds()).workArea;
  const w = Math.max(320, Math.min(width, area.width));
  const h = Math.max(240, Math.min(height, area.height));

  const bounds = win.getBounds();
  const x = Math.max(area.x, Math.min(bounds.x, area.x + area.width - w));
  const y = Math.max(area.y, Math.min(bounds.y, area.y + area.height - h));
  return { x, y, width: w, height: h };
}

function animateBounds(win, target) {
  const token = ++tweenToken;
  const start = win.getBounds();
  const startAt = Date.now();

  if (start.width === target.width && start.height === target.height &&
      start.x === target.x && start.y === target.y) {
    return;
  }

  const tick = () => {
    if (token !== tweenToken || win.isDestroyed()) return;
    const t = Math.min(1, (Date.now() - startAt) / TWEEN_MS);
    const e = 1 - Math.pow(1 - t, 3);
    const lerp = (a, b) => Math.round(a + (b - a) * e);
    win.setBounds({
      x: lerp(start.x, target.x),
      y: lerp(start.y, target.y),
      width: lerp(start.width, target.width),
      height: lerp(start.height, target.height),
    });
    if (t < 1) setTimeout(tick, TWEEN_STEP_MS);
  };
  tick();
}

function setWindowSize(mode) {
  const win = mainWindow;
  if (!win || win.isDestroyed()) return;
  const area = screen.getDisplayMatching(win.getBounds()).workArea;
  const size = windowSize(mode, area);
  if (!size) {
    log('ignored unknown window size mode:', mode);
    return;
  }
  windowMode = mode;
  win.setIgnoreMouseEvents(false);
  win.setHasShadow(mode === 'menu');
  win.setMinimumSize(720, mode === 'menu' ? 480 : 350);
  const bounds = win.getBounds();
  if (bounds.width === size.width && bounds.height === size.height) return;
  animateBounds(win, clampToWorkArea(win, size.width, size.height));
}

function failurePage(message) {
  const html = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>로컬 backend를 시작하지 못했습니다</title>
<style>body{font:15px/1.7 -apple-system,"Apple SD Gothic Neo",system-ui,sans-serif;color:#1c1c1e;background:#fff;
display:flex;align-items:center;justify-content:center;height:100%;margin:0}main{max-width:34rem;padding:2.5rem}
pre{font:12px/1.5 ui-monospace,Menlo,monospace;background:#f4f4f5;border:1px solid #e4e4e7;border-radius:6px;padding:.8rem;white-space:pre-wrap}</style>
</head><body><main><h1>This review build could not start its local backend</h1>
<p>${message.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</p>
<pre>Research Workbench REVIEW build · mock adapter · sample data</pre></main></body></html>`;
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html);
}

function createWindow(appUrl) {
  const size = windowSize('boot', screen.getPrimaryDisplay().workArea);
  const win = new BrowserWindow({
    width: size.width,
    height: size.height,
    minWidth: 720,
    minHeight: 350,
    title: 'Research Workbench (review build)',
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    show: false,
    resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: true,
    },
  });

  mainWindow = win;

  win.once('ready-to-show', () => {
    win.show();
    log('window shown', JSON.stringify(win.getBounds()));
  });

  win.webContents.on('did-finish-load', async () => {
    log('did-finish-load', win.webContents.getURL());
    if (!SMOKE || smokeStarted) return;
    smokeStarted = true;
    try {
      const probe = await win.webContents.executeJavaScript(
        `JSON.stringify({
           hasDesktop: typeof window.WB_DESKTOP === 'object' && window.WB_DESKTOP !== null,
           keys: window.WB_DESKTOP ? Object.keys(window.WB_DESKTOP).sort() : [],
           isDesktop: window.WB_DESKTOP ? window.WB_DESKTOP.isDesktop : null,
           nodeLeak: typeof window.require !== 'undefined' || typeof window.process !== 'undefined'
         })`
      );
      log('smoke: renderer probe', probe);
    } catch (err) {
      log('smoke: renderer probe FAILED', String(err && err.message || err));
    }
    const steps = ['home', 'menu', 'boot'];
    let i = 0;
    const next = () => {
      if (i >= steps.length) {
        log('smoke: load complete, quitting');
        setTimeout(() => app.quit(), 200);
        return;
      }
      const mode = steps[i++];
      setWindowSize(mode);
      setTimeout(() => {
        log('smoke: size', mode, JSON.stringify(win.getBounds()));
        next();
      }, TWEEN_MS + 180);
    };
    log('smoke: size boot (no-op)', JSON.stringify(win.getBounds()));
    setTimeout(next, 400);
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1:') || url.startsWith('http://localhost:')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.webContents.on('will-prevent-unload', (event) => {
    if (win.isDestroyed() || closePromptOpen) return;
    closePromptOpen = true;
    let choice = 0;
    try {
      choice = dialog.showMessageBoxSync(win, {
        type: 'warning',
        buttons: [UNSAVED_CLOSE.keepEditing, UNSAVED_CLOSE.discard],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
        message: UNSAVED_CLOSE.message,
        detail: UNSAVED_CLOSE.detail,
      });
    } catch (err) {
      log('unsaved-close prompt failed; keeping the window open:', String(err && err.message || err));
      choice = 0;
    } finally {
      closePromptOpen = false;
    }
    log('unsaved-close prompt answered', choice === 1 ? 'close-without-saving' : 'keep-editing');
    if (choice === 1) event.preventDefault();
  });

  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });

  buildMenu();
  log('loading', appUrl);
  win.loadURL(appUrl);
  return win;
}

ipcMain.handle('wb:open-chip', async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const chosen = await openChipDialog(win);
  if (chosen) deliverChip(chosen, win);
  return chosen || null;
});

ipcMain.on('wb:new-chip', (event) => {
  deliverNewChip(BrowserWindow.fromWebContents(event.sender));
});

ipcMain.handle('wb:save-chip', async (event, payload) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) throw new Error('Unknown window.');
  const fsPromises = require('node:fs/promises');
  if (!payload || typeof payload.base64 !== 'string' || payload.base64.length > 4 * Math.ceil(100 * 1024 * 1024 / 3) || !/^[A-Za-z0-9+/]*={0,2}$/.test(payload.base64)) throw new Error('Invalid chip file.');
  const bytes = Buffer.from(payload.base64, 'base64');
  if (bytes.length > 100 * 1024 * 1024 || bytes[0] !== 80 || bytes[1] !== 75) throw new Error('Invalid chip archive.');
  const filename = typeof payload.filename === 'string' ? path.basename(payload.filename) : 'research.chip';
  const chosen = await dialog.showSaveDialog(mainWindow, { title: 'Save Research Chip', defaultPath: filename, filters: [{ name: 'Research chip', extensions: ['chip'] }] });
  if (chosen.canceled || !chosen.filePath) return null;
  await fsPromises.writeFile(chosen.filePath, bytes, { mode: 0o600 });
  return { filename: path.basename(chosen.filePath) };
});

ipcMain.on('wb:set-window-size', (event, mode) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  setWindowSize(mode);
});

ipcMain.on('wb:window-control', (event, action) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  if (action === 'close') mainWindow.close();
  else if (action === 'minimize') mainWindow.minimize();
});

ipcMain.on('wb:ignore-pointer', (event, ignore) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  mainWindow.setIgnoreMouseEvents(windowMode !== 'menu' && ignore === true, { forward: true });
});

app.whenReady().then(async () => {
  log('app ready; smoke =', SMOKE);
  let appUrl = null;
  try {
    const started = startBackend();
    const ready = await waitForReady(started.readyFile);
    loopbackUrl(ready.app_url);
    loopbackUrl(ready.api);
    log('backend ready', JSON.stringify(ready.identity));
    appUrl = ready.app_url;
  } catch (err) {
    const message = String(err && err.message || err);
    log('backend start failed:', message);
    const win = createWindow('about:blank');
    win.loadURL(failurePage(message));
    return;
  }
  createWindow(appUrl);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(appUrl);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' || process.argv.includes('--quit-on-window-close')) app.quit();
});

// The backend is this app's own child; nothing else is signalled or terminated.
//
// Stop it only once the quit can no longer be taken back. `before-quit` fires
// BEFORE any window's close, so a cancelled quit (the unsaved-writing prompt's
// "Keep editing", or any renderer that vetoes the unload) would otherwise kill
// the backend while the editor window stays open and usable: every later save,
// compile or API call would then fail. `will-quit` can also be vetoed after the
// windows close. The non-cancellable `quit` event is the first final boundary;
// `process.on('exit')` and the child's --parent-pid supervision cover exits that
// skip it. A cancelled quit keeps its backend available for a reopened window.
app.on('quit', stopBackend);
process.on('exit', stopBackend);

if (SMOKE) {
  setTimeout(() => {
    log('smoke: timeout reached, quitting');
    app.quit();
  }, 20000);
}
