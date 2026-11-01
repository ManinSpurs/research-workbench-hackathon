// Native unsaved-writing close contract, exercised against a real Electron host.
//
// The renderer keeps owning "is there unsaved work?" (frontend/latex.js only calls
// beforeunload.preventDefault() while a draft is dirty or saving). This test proves
// the Electron host now answers that one question with a native prompt instead of
// silently cancelling the close:
//   * clean draft          → 0 prompts, window closes
//   * cancel ("Keep editing")   → window stays open, buffer and dirty flag untouched
//   * explicit discard          → window really closes on that same attempt
//   * quit (the role CmdQ runs) → same prompt, and a cancel leaves quit unlocked
//
// Only the native dialog RESPONSE is stubbed (dialog.showMessageBoxSync in the main
// process); every close travels the real path: frameless X → preload →
// wb:window-control, or BrowserWindow.close()/app.quit() as the menu roles do.
// Uses a fresh temporary DB and profile; never touches research files or servers.
import { _electron as electron } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, cp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const qa = await mkdtemp(path.join(tmpdir(), 'wb-unsaved-close-qa-'));
const output = path.join(root, 'tmp', 'desktop-unsaved-close-qa');
await mkdir(output, { recursive: true });
const qaData = path.join(qa, 'data');
await mkdir(qaData);
await cp(path.join(root, 'data/fx'), path.join(qaData, 'fx'), { recursive: true });

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

const fakePort = await freePort(), apiPort = await freePort(), webPort = await freePort();
const apiUrl = `http://127.0.0.1:${apiPort}`;
const webUrl = `http://127.0.0.1:${webPort}`;
const env = { ...process.env, WB_DB: path.join(qa, 'isolated.db'),
  WB_APP_URL: `${webUrl}/?api=${apiUrl}`,
  WB_ALLOW_REAL_MUSE: '0', FAKE_BASE_URL: `http://127.0.0.1:${fakePort}/v1`,
  NO_PROXY: 'localhost,127.0.0.1', no_proxy: 'localhost,127.0.0.1' };
delete env.META_API_KEY;
delete env.WB_MODAL_BASE_URL;
delete env.WB_MODAL_PROXY_TOKEN_ID;
delete env.WB_MODAL_PROXY_TOKEN_SECRET;

const children = [];
let app;
let logs = '';
let exited = false;

const UNSAVED_TEXT = 'Unsaved native buffer text.';

async function readyPage(opened) {
  const page = opened || await app.firstWindow();
  page.on('pageerror', error => console.error('RENDERER_ERROR', error.message));
  await page.waitForSelector('.desktop-shell', { state: 'attached' });
  await page.waitForSelector('.desktop-console-controls button[aria-label="Close window"]', { state: 'attached' });
  return page;
}

// The one stubbed seam: the native answer. Everything else stays real.
async function scriptAnswers(answers) {
  await app.evaluate(({ dialog }, queued) => {
    globalThis.__closePrompts = globalThis.__closePrompts || [];
    globalThis.__closeAnswers = queued.slice();
    dialog.showMessageBoxSync = (win, options) => {
      globalThis.__closePrompts.push({ options, hasWindow: !!win });
      return globalThis.__closeAnswers.length ? globalThis.__closeAnswers.shift() : 0;
    };
  }, answers);
}

async function prompts() {
  return app.evaluate(() => globalThis.__closePrompts || []);
}

async function makeDirty(page) {
  await page.evaluate((text) => {
    latexState = { draft: { draft_id: 'review-draft', main_file: 'main.tex', revision: 1, files: [{ path: 'main.tex', text }] },
      activeFile: 'main.tex', dirty: true, saving: false };
    document.getElementById('latex-editor').value = text;
  }, UNSAVED_TEXT);
}

async function bufferState(page) {
  return page.evaluate(() => ({
    text: document.getElementById('latex-editor').value,
    dirty: !!(latexState && latexState.dirty),
    revision: latexState && latexState.draft ? latexState.draft.revision : null,
  }));
}

async function clickFramelessClose(page) {
  await page.evaluate(() => document.querySelector('.desktop-console-controls button[aria-label="Close window"]').click());
}

async function newWindow() {
  const next = app.waitForEvent('window');
  await app.evaluate(({ app: electronApp }) => electronApp.emit('activate'));
  return readyPage(await next);
}

async function exists() {
  return app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length);
}

try {
  for (const [args, cwd] of [
    [['-m', 'app.fake_meta', String(fakePort)], root],
    [['-c', `import app.api as api; from pathlib import Path; import uvicorn; api.ORIGINS.append(${JSON.stringify(webUrl)}); uvicorn.run(api.create_app(db_path=${JSON.stringify(path.join(qa, 'isolated.db'))},data_dir=Path(${JSON.stringify(qaData)})),host='127.0.0.1',port=${apiPort})`], root],
    [['-m', 'http.server', String(webPort), '--bind', '127.0.0.1'], path.join(root, 'frontend')],
  ]) {
    const child = spawn(path.join(root, '.venv/bin/python'), args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', data => { logs += data; });
    child.stderr.on('data', data => { logs += data; });
    children.push(child);
  }
  for (const url of [webUrl, apiUrl + '/projects']) {
    let ready = false;
    for (let i = 0; i < 80; i++) {
      try { if ((await fetch(url)).ok) { ready = true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(ready, 'QA server did not become ready: ' + url);
  }

  app = await electron.launch({ args: [root, '--user-data-dir=' + path.join(qa, 'profile')], cwd: root, env });
  let page = await readyPage();

  // The transparent frameless shell is unchanged by this contract.
  const shell = await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    return { background: w.getBackgroundColor(), shadow: w.hasShadow(), visible: w.isVisible() };
  });
  assert.match(shell.background, /^#(?:00000000|000000)$/i, 'console background stays transparent');
  assert.equal(shell.shadow, false, 'console shadow stays off');
  assert.equal(shell.visible, true, 'window is shown');
  assert.equal(await page.locator('#startup-choices').isVisible(), false, 'startup choices stay hidden');
  assert.equal(await page.evaluate(() => typeof latexOnBeforeUnload), 'function', 'the renderer close guard is the real one');

  // (1) A clean draft closes with no prompt at all.
  await scriptAnswers([]);
  assert.equal((await bufferState(page)).dirty, false, 'no unsaved work before the clean close');
  await clickFramelessClose(page);
  for (let i = 0; i < 100 && await exists(); i++) await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await exists(), 0, 'a clean window closes immediately');
  assert.deepEqual(await prompts(), [], 'a clean close never asks');
  console.log('PASS clean close: 0 prompts, window closed');

  // (2) Cancel keeps the window, the buffer and the dirty flag.
  page = await newWindow();
  await makeDirty(page);
  await scriptAnswers([0]); // "Keep editing" (the default button)
  await clickFramelessClose(page);
  await page.waitForTimeout(600);
  assert.equal(await exists(), 1, 'cancelling the native prompt keeps the window open');
  let asked = await prompts();
  assert.equal(asked.length, 1, 'exactly one prompt per close attempt');
  const options = asked[0].options;
  assert.equal(asked[0].hasWindow, true, 'the prompt is window-modal on its own window');
  assert.equal(options.type, 'warning');
  assert.equal(options.defaultId, 0, 'the non-destructive answer is the default');
  assert.equal(options.cancelId, 0, 'Escape and window-close map to Keep editing');
  assert.deepEqual(options.buttons, ['Keep editing', 'Close without saving']);
  assert.match(options.message, /unsaved changes/i);
  assert.match(options.detail, /discard|cannot be undone/i, 'the discard alternative carries a clear warning');
  assert.deepEqual(await bufferState(page), { text: UNSAVED_TEXT, dirty: true, revision: 1 }, 'the renderer buffer is untouched');
  console.log('PASS cancel: window open, buffer and dirty flag untouched, default = Keep editing');

  // (3) The explicit discard answer closes on that same attempt — no re-prompt loop.
  await scriptAnswers([1]);
  await clickFramelessClose(page);
  for (let i = 0; i < 100 && await exists(); i++) await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await exists(), 0, 'Close without saving really closes the window');
  asked = await prompts();
  assert.equal(asked.length, 2, 'the discard attempt asked once, not in a loop');
  console.log('PASS discard: window closed, one prompt, no re-prompt loop');

  // (4) The menu roles that the accelerators run reach the same host prompt.
  const roles = await app.evaluate(({ Menu }) => {
    const menu = Menu.getApplicationMenu();
    const quit = menu.items.find(item => item.label === 'Research Workbench').submenu.items.find(item => item.role === 'quit');
    const close = menu.items.find(item => item.label === 'File').submenu.items.find(item => item.role === 'close');
    return { quit: quit && quit.accelerator, close: close && close.accelerator };
  });
  assert.equal(roles.quit, 'CmdOrCtrl+Q', '⌘Q runs the quit role');
  assert.equal(roles.close, 'CmdOrCtrl+W', '⌘W runs the close role');

  page = await newWindow();
  await makeDirty(page);
  await scriptAnswers([0, 1]);
  // ⌘W / File → 닫기: BrowserWindow.close() is exactly what the close role runs.
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  await page.waitForTimeout(600);
  assert.equal(await exists(), 1, 'menu close keeps the window on cancel');
  assert.equal((await prompts()).length, 3, 'menu close asks the same prompt once');
  assert.deepEqual(await bufferState(page), { text: UNSAVED_TEXT, dirty: true, revision: 1 }, 'menu close cancel keeps the buffer');
  // ⌘W accepted.
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  for (let i = 0; i < 100 && await exists(); i++) await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await exists(), 0, 'menu close accepted closes the window');
  assert.equal((await prompts()).length, 4, 'menu close accepted asked exactly once');
  console.log('PASS menu close: same prompt for cancel and accept');

  // (5) ⌘Q / app menu 종료: cancel does not lock quit, accept quits the app.
  page = await newWindow();
  await makeDirty(page);
  await scriptAnswers([0]);
  await app.evaluate(({ app: electronApp }) => electronApp.quit());
  await page.waitForTimeout(800);
  assert.equal(await exists(), 1, 'quit is cancelled while the prompt is declined');
  assert.equal((await prompts()).length, 5, 'quit asks once');
  assert.deepEqual(await bufferState(page), { text: UNSAVED_TEXT, dirty: true, revision: 1 }, 'quit cancel keeps the buffer');
  const quitClosed = new Promise(resolve => app.on('close', resolve));
  await scriptAnswers([1]);
  await app.evaluate(({ app: electronApp }) => electronApp.quit());
  await Promise.race([quitClosed, new Promise((_, reject) => setTimeout(() => reject(new Error('app did not quit after Close without saving')), 15000))]);
  exited = true;
  await assert.rejects(async () => prompts(), 'the main process is unavailable after confirmed quit');
  console.log('PASS quit: cancel keeps the app and buffer, accept quits, no permanent lock');

  console.log(JSON.stringify({ result: 'PASS', shell, prompts: 6, qa, paidCalls: 0 }));
} finally {
  if (app && !exited) { try { await app.close(); } catch {} }
  exited = exited || !app;
  for (const child of children) child.kill('SIGTERM');
  await Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() : new Promise(resolve => {
    child.once('exit', resolve); setTimeout(resolve, 3000);
  })));
  await writeFile(path.join(output, 'servers.log'), logs);
}
