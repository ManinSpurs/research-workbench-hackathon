'use strict';

// Electron entry point for the Research Workbench desktop shell.
//
// It hosts the *existing* web app in a native macOS window: the Python API on
// http://127.0.0.1:8123 and the static frontend on http://127.0.0.1:5173.
// Electron is used only because a browser tab cannot have a native menu bar.
//
// Why we do not start Python from here: the servers are started deliberately
// by the researcher (run_mock.py / Start_Mock.command) because they carry the
// mock provider, the local SQLite database and the rehearsal flags. Silent
// spawning here would make two competing backends possible, hide Python errors
// inside a GUI, and outlive or be killed by the window in surprising ways. If
// the frontend is unreachable the window shows an explicit, printable notice
// instead.
//
// Audio note for the later audio task: Electron allows audio playback without a
// preceding user gesture, unlike a browser page under the autoplay policy. No
// audio files or playback code are added here.
//
// Smoke test: `electron . --smoke` (npm run smoke) quits by itself once the
// renderer has finished loading, so a CI run cannot hang.

const path = require('node:path');
const fs = require('node:fs/promises');
const { app, BrowserWindow, ipcMain, screen, shell, dialog } = require('electron');
const existingUserData = app.getPath('userData');
app.setName('Research Workbench');
app.setPath('userData', existingUserData);
const { buildMenu, openChipDialog, deliverChip, deliverNewChip } = require('./menu');

const DEFAULT_APP_URL = 'http://127.0.0.1:5173/?api=http://127.0.0.1:8123';
function loopbackUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password) {
    throw new Error('Workbench launch URLs must use HTTP loopback without credentials.');
  }
  return url;
}
const launchUrl = loopbackUrl(process.env.WB_APP_URL || DEFAULT_APP_URL);
if (launchUrl.searchParams.has('api')) loopbackUrl(launchUrl.searchParams.get('api'));
const APP_URL = launchUrl.href;
const SMOKE = process.argv.includes('--smoke');

// Fit the console to the current display, then give the opened workbench
// additional width and height. Keep the approved 1488 x 656 art undistorted.
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

// Native text for the unsaved-writing close confirmation. The dialog only ever
// appears because the renderer's own beforeunload guard (frontend/latex.js) held
// the unload back; this file never decides by itself that something is unsaved.
const UNSAVED_CLOSE = {
  message: 'Your writing has unsaved changes.',
  detail: 'Choose “Keep editing” to save them first. “Close without saving” discards the unsaved text in this window, and that cannot be undone.',
  keepEditing: 'Keep editing',
  discard: 'Close without saving',
};

function log(...args) {
  // Console output is the smoke-test capture surface.
  console.log('[electron]', ...args);
}

function clampToWorkArea(win, width, height) {
  // Never grow off-screen: clamp against the display the window currently sits
  // on, then pull the frame back inside that display's work area.
  const area = screen.getDisplayMatching(win.getBounds()).workArea;
  const w = Math.max(320, Math.min(width, area.width));
  const h = Math.max(240, Math.min(height, area.height));

  const bounds = win.getBounds();
  const x = Math.max(area.x, Math.min(bounds.x, area.x + area.width - w));
  const y = Math.max(area.y, Math.min(bounds.y, area.y + area.height - h));
  return { x, y, width: w, height: h };
}

function animateBounds(win, target) {
  // macOS has no native window-size animation, so tween setBounds ourselves.
  const token = ++tweenToken;
  const start = win.getBounds();
  const startAt = Date.now();

  if (start.width === target.width && start.height === target.height &&
      start.x === target.x && start.y === target.y) {
    return; // already there — no-op
  }

  const tick = () => {
    if (token !== tweenToken || win.isDestroyed()) return;
    const t = Math.min(1, (Date.now() - startAt) / TWEEN_MS);
    const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
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

function unreachablePage() {
  const html = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>로컬 서버가 필요합니다</title>
<style>
  html, body { margin: 0; height: 100%; }
  body {
    font: 15px/1.7 -apple-system, "Apple SD Gothic Neo", system-ui, sans-serif;
    color: #1c1c1e; background: #fff;
    display: flex; align-items: center; justify-content: center;
  }
  main { max-width: 34rem; padding: 2.5rem; }
  h1 { font-size: 1.15rem; margin: 0 0 1rem; }
  p { margin: 0 0 .9rem; }
  pre {
    font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #f4f4f5; border: 1px solid #e4e4e7; border-radius: 6px;
    padding: .8rem .9rem; margin: 0 0 1rem; white-space: pre-wrap;
  }
  code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; }
  @media print { body { display: block; } main { max-width: none; } }
</style>
</head>
<body>
<main>
  <h1>로컬 서버가 먼저 실행되어야 합니다</h1>
  <p>이 창은 이미 실행 중인 Research Workbench 로컬 서버
     (<code>127.0.0.1:8123</code> API, <code>127.0.0.1:5173</code> 프런트엔드)를
     표시합니다. 창이 서버를 대신 실행하지는 않습니다.</p>
  <p>터미널에서 프로젝트 폴더로 이동한 뒤 다음 중 하나를 실행하세요.</p>
  <pre>python3 run_mock.py</pre>
  <p>또는 Finder에서 <code>Start_Mock.command</code>를 더블클릭합니다.
     서버가 올라온 다음 파일 → 새로 고침 (<code>Cmd+R</code>)을 누르면 됩니다.</p>
</main>
</body>
</html>`;
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html);
}

function createWindow() {
  const size = windowSize('boot', screen.getPrimaryDisplay().workArea);
  const win = new BrowserWindow({
    width: size.width,
    height: size.height,
    minWidth: 720,
    minHeight: 350,
    title: 'Research Workbench',
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
  win.setResizable(true);


  win.once('ready-to-show', () => {
    win.show();
    log('window shown', JSON.stringify(win.getBounds()));
  });

  win.webContents.on('did-finish-load', async () => {
    log('did-finish-load', win.webContents.getURL());
    // A failed navigation may finish twice (error page, then the fallback page);
    // the smoke sequence must only run once.
    if (!SMOKE || smokeStarted) return;
    smokeStarted = true;

    // Confirm the sandboxed preload really reached the renderer through
    // contextBridge (no node globals, expected WB_DESKTOP surface).
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

    // The renderer is not calling setWindowSize yet (that hook lands later),
    // so the smoke run drives the three modes itself and prints the resulting
    // native bounds, then quits on its own.
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
    setWindowSize('boot'); // no-op: already boot-sized
    log('smoke: size boot (no-op)', JSON.stringify(win.getBounds()));
    setTimeout(next, 400);
  });

  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame) return;
    log('did-fail-load', errorCode, errorDescription, validatedURL);
    win.loadURL(unreachablePage());
  });

  // External links stay in the system browser instead of hijacking the shell.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1:') || url.startsWith('http://localhost:')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Unsaved writing close contract.
  //
  // The renderer owns the "is there unsaved work?" decision: frontend/latex.js
  // calls preventDefault() on beforeunload only while a draft is dirty or saving.
  // When that happens the unload is held back and Electron asks the host here,
  // once, instead of silently cancelling the close with no UI. Keeping it
  // synchronous means no save is promised and no second prompt can stack up.
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
    // Only the explicit discard choice lets the page unload; anything else leaves
    // the window, the renderer buffer and the draft state exactly as they were.
    if (choice === 1) event.preventDefault();
  });

  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });

  buildMenu();
  log('loading', APP_URL);
  win.loadURL(APP_URL);
  return win;
}

// Renderer-side 파일 → 열기… via preload's WB_DESKTOP.openChip().
ipcMain.handle('wb:open-chip', async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const chosen = await openChipDialog(win);
  if (chosen) deliverChip(chosen, win);
  return chosen || null;
});

// Renderer buttons can trigger the same new-chip flow as the native menu.
ipcMain.on('wb:new-chip', (event) => {
  deliverNewChip(BrowserWindow.fromWebContents(event.sender));
});

ipcMain.handle('wb:save-chip', async (event, payload) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) throw new Error('Unknown window.');
  if (!payload || typeof payload.base64 !== 'string' || payload.base64.length > 4 * Math.ceil(100 * 1024 * 1024 / 3) || !/^[A-Za-z0-9+/]*={0,2}$/.test(payload.base64)) throw new Error('Invalid chip file.');
  const bytes = Buffer.from(payload.base64, 'base64');
  if (bytes.length > 100 * 1024 * 1024 || bytes[0] !== 80 || bytes[1] !== 75) throw new Error('Invalid chip archive.');
  const filename = typeof payload.filename === 'string' ? path.basename(payload.filename) : 'research.chip';
  const chosen = await dialog.showSaveDialog(mainWindow, { title: 'Save Research Chip', defaultPath: filename, filters: [{ name: 'Research chip', extensions: ['chip'] }] });
  if (chosen.canceled || !chosen.filePath) return null;
  await fs.writeFile(chosen.filePath, bytes, { mode: 0o600 });
  return { filename: path.basename(chosen.filePath) };
});

ipcMain.on('wb:set-window-size', (event, mode) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  setWindowSize(mode);
});

// Narrow shell-only commands; no renderer access to arbitrary native APIs.
ipcMain.on('wb:window-control', (event, action) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  if (action === 'close') mainWindow.close();
  else if (action === 'minimize') mainWindow.minimize();
});
ipcMain.on('wb:ignore-pointer', (event, ignore) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return;
  mainWindow.setIgnoreMouseEvents(windowMode !== 'menu' && ignore === true, { forward: true });
});

app.whenReady().then(() => {
  log('app ready; smoke =', SMOKE);
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' || process.argv.includes('--quit-on-window-close')) app.quit();
});

// Belt and braces for the smoke test: never leave a headless CI run hanging.
if (SMOKE) {
  setTimeout(() => {
    log('smoke: timeout reached, quitting');
    app.quit();
  }, 12000);
}
