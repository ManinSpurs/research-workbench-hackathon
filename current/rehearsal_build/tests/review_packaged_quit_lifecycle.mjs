// Packaged-app quit lifecycle: a cancelled quit must not stop the backend.
//
// Defect under regression: the packaged shell registered stopBackend on
// 'before-quit'. Electron emits 'before-quit' BEFORE any window close, so the
// unsaved-writing prompt's "Keep editing" (which prevents the unload and so
// cancels the quit) used to leave a live editor window with a dead backend: the
// next save, compile or API call failed. The fix stops the owned child only in
// the non-cancellable phase ('quit') and still on main-process exit; the
// child's own --parent-pid supervision covers a hard kill of this process.
//
// Part 1 (always runs, no Electron, no window, no network, no server): loads the
// real packaging/electron_main.js and the real electron/menu.js in a node:vm
// context whose electron / child_process / fs seams are recorded doubles, and
// drives the documented Electron quit protocol:
//   * renderer veto ("Keep editing")      -> quit cancelled, backend never signalled
//   * explicit discard                   -> window closes, backend stopped exactly once
//   * clean quit (no veto)                -> no prompt, backend stopped exactly once
//   * main-process exit only              -> backend stopped exactly once
// It also pins the shipped geometry, window options and menu accelerators, so a
// change of those cannot ride along with this fix.
//
// Part 2 (opt-in, real packaged app, run by the Lead after a rebuild):
//   WB_PACKAGED_APP="/path/Research Workbench Review.app" node tests/review_packaged_quit_lifecycle.mjs
// It launches that packaged executable with an isolated --user-data-dir, makes
// the writing buffer dirty, cancels a quit, proves the owned backend still
// answers a real API save, then accepts the quit and proves the backend pid and
// its ports are gone. It opens a real window, so it is never run implicitly.
//
// Part 3 (section 7, always runs, no Electron, no window, no network): failure-
// safe teardown. Every real resource the packaged leg creates — the packaged app
// Playwright launched, the backend pid that app's own isolated session reported,
// the mkdtemp profile — is registered in an owned-resource set at the moment it
// exists, and a try/catch/finally around the whole leg always tears that set
// down. A failed assertion or a timeout inside the leg therefore leaves no
// window, no backend and no occupied port behind, and teardown only ever signals
// a pid it recorded itself (never a discovered, named or port-derived process)
// and only ever removes a directory it created with mkdtemp under the OS temp
// root. The dirty buffer it discards is this test's own fixture, answered
// through the shipped unsaved-close prompt with its explicit discard button; the
// product's unsaved-content protection is untouched. Section 7a regresses that
// teardown against a deliberately failing leg using stand-in processes, so the
// regression itself needs no GUI.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
// Aliased: the packaged-leg assertions below already use the name `spawn` for
// the recorded child_process.spawn call of the shipped shell.
import { spawn as spawnProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const SOURCE = path.resolve(import.meta.dirname, '..');
const MAIN = path.join(SOURCE, 'packaging', 'electron_main.js');
const MENU = path.join(SOURCE, 'electron', 'menu.js');
const mainSource = readFileSync(MAIN, 'utf8');

const APP_ROOT = '/opt/WB Test.app/Contents/Resources/app';
const BUNDLE = path.join(path.dirname(APP_ROOT), 'wb');
const PYTHON = path.join(BUNDLE, 'runtime', 'python', 'bin', 'python3.14');
const BACKEND = path.join(BUNDLE, 'packaging', 'backend_server.py');
const PROFILE = '/tmp/wb-quit-profile';
const WORK_AREA = { x: 0, y: 0, width: 1512, height: 982 };
const APP_URL = 'http://127.0.0.1:54321/?api=http://127.0.0.1:54322&preview=1';
const API_URL = 'http://127.0.0.1:54322';
const UNSAVED = {
  message: 'Your writing has unsaved changes.',
  detail: 'Choose “Keep editing” to save them first. “Close without saving” discards the unsaved text in this window, and that cannot be undone.',
  buttons: ['Keep editing', 'Close without saving'],
};

function preventable() {
  return {
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  };
}

// One harness = one fresh load of the two real modules and one quit protocol.
function harness() {
  const spawned = [];
  const prompts = [];
  const openedUrls = [];
  const events = { willQuit: 0, quit: 0, beforeQuit: 0 };
  const exitListeners = [];
  let answers = [];
  let menuBuilt = 0;
  const windows = [];

  class FakeWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.destroyed = false;
      // The real renderer (frontend/latex.js latexOnBeforeUnload) reports unsaved
      // work by preventing the unload; only then does Electron ask the host.
      this.vetoes = false;
      this.bounds = { x: 40, y: 40, width: options.width, height: options.height };
      this.webContents = Object.assign(new EventEmitter(), {
        send() {},
        setWindowOpenHandler() {},
        getURL() { return ''; },
        executeJavaScript: async () => '{}',
      });
      windows.push(this);
    }
    isDestroyed() { return this.destroyed; }
    show() { this.shown = true; }
    getBounds() { return { ...this.bounds }; }
    setBounds(next) { this.bounds = { ...next }; }
    setMinimumSize(width, height) { this.minimumSize = [width, height]; }
    setIgnoreMouseEvents() {}
    setHasShadow(value) { this.hasShadow = value; }
    loadURL(url) { openedUrls.push(url); }
    close() { return requestClose(this); }
  }

  function destroy(win) {
    win.destroyed = true;
    const at = windows.indexOf(win);
    if (at !== -1) windows.splice(at, 1);
    win.emit('closed');
  }

  // Real order for one window: the renderer may prevent the unload
  // (frontend/latex.js latexOnBeforeUnload), Electron then emits
  // 'will-prevent-unload', and calling preventDefault THERE overrides the
  // renderer and lets the close proceed. Without that override the unload stays
  // prevented and the window does not close.
  function requestClose(win) {
    if (win.destroyed) return true;
    if (win.vetoes) {
      const event = preventable();
      win.webContents.emit('will-prevent-unload', event);
      if (!event.defaultPrevented) return false;
    }
    destroy(win);
    return true;
  }

  const app = new EventEmitter();
  app.getPath = () => PROFILE;
  app.setName = () => {};
  app.setPath = () => {};
  app.getAppPath = () => APP_ROOT;
  app.whenReady = () => Promise.resolve();
  app.isReady = () => true;
  app.quit = () => {
    const before = preventable();
    events.beforeQuit += 1;
    app.emit('before-quit', before);
    if (before.defaultPrevented) return 'cancelled:before-quit';
    for (const win of windows.slice()) {
      if (!requestClose(win)) return 'cancelled:window-close';
    }
    const will = preventable();
    events.willQuit += 1;
    app.emit('will-quit', will);
    if (will.defaultPrevented) return 'cancelled:will-quit';
    events.quit += 1;
    app.emit('quit');
    // Normal Electron termination runs the Node 'exit' hook as well.
    for (const listener of exitListeners) listener(0);
    return 'quit';
  };

  const electronMock = {
    app,
    BrowserWindow: Object.assign(FakeWindow, {
      getAllWindows: () => windows.slice(),
      getFocusedWindow: () => windows[windows.length - 1] || null,
      fromWebContents: (contents) => windows.find((win) => win.webContents === contents) || null,
    }),
    ipcMain: { handle() {}, on() {} },
    screen: {
      getPrimaryDisplay: () => ({ workArea: WORK_AREA }),
      getDisplayMatching: () => ({ workArea: WORK_AREA }),
    },
    shell: { openExternal() {} },
    dialog: {
      showMessageBoxSync(win, options) {
        prompts.push({ options, hasWindow: !!win });
        return answers.length ? answers.shift() : 0;
      },
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
      showSaveDialog: async () => ({ canceled: true }),
      showErrorBox() {},
    },
    Menu: {
      buildFromTemplate(template) { return { template, items: template }; },
      setApplicationMenu(menu) { menuBuilt += 1; this.menu = menu; },
      getApplicationMenu() { return this.menu; },
    },
  };

  const child = Object.assign(new EventEmitter(), {
    pid: 9001,
    exitCode: null,
    signalCode: null,
    kills: [],
    stdout: new EventEmitter(),
    stderr: new EventEmitter(),
    kill(signal) {
      this.kills.push(signal);
      this.signalCode = signal;
      return true;
    },
  });

  const fsMock = {
    existsSync: (target) => target === PYTHON || target === BACKEND || /ready\.json$/.test(target),
    readFileSync: () => JSON.stringify({ app_url: APP_URL, api: API_URL }),
    mkdirSync() {},
    writeFileSync() {},
  };

  const processStub = {
    pid: 4242,
    platform: 'darwin',
    argv: ['/opt/Electron', path.join(APP_ROOT, 'main.js')],
    env: { PATH: '/usr/bin' },
    stdout: { write() {} },
    stderr: { write() {} },
    on(name, listener) { if (name === 'exit') exitListeners.push(listener); return this; },
    off() {},
  };

  const context = vm.createContext({
    console: { log() {}, error: console.error, warn() {} },
    process: processStub,
    setTimeout,
    clearTimeout,
    URL,
    Buffer,
    Date,
  });

  const cache = new Map();
  function loadModule(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} };
    cache.set(file, module);
    const dirname = path.dirname(file);
    const require = (spec) => {
      if (spec === 'node:path') return path;
      if (spec === 'node:fs') return fsMock;
      if (spec === 'node:fs/promises') return { readFile: async () => '', writeFile: async () => {}, stat: async () => ({}) };
      if (spec === 'node:child_process') {
        return { spawn: (command, args, options) => {
          spawned.push({ command, args, options, child });
          return child;
        } };
      }
      if (spec === 'electron') return electronMock;
      // The bundle copies electron/menu.js next to main.js; the source tree keeps
      // it in electron/, so the shipped module is loaded from there unchanged.
      if (spec === './menu') return loadModule(MENU);
      throw new Error('unexpected require: ' + spec);
    };
    const wrapper = vm.runInContext(
      '(function (module, exports, require, __filename, __dirname) {\n' + readFileSync(file, 'utf8') + '\n})',
      context, { filename: file });
    wrapper(module, module.exports, require, file, dirname);
    return module.exports;
  }

  return {
    windows, spawned, prompts, openedUrls, events, child, app,
    get menuBuilt() { return menuBuilt; },
    get menu() { return electronMock.Menu.menu; },
    answer(next) { answers = next.slice(); },
    load() { loadModule(MAIN); },
    runExitListeners() { for (const listener of exitListeners) listener(0); },
    settle: () => new Promise((resolve) => setTimeout(resolve, 25)),
  };
}

async function boot() {
  const h = harness();
  h.load();
  await h.settle();
  assert.equal(h.windows.length, 1, 'the shell opens exactly one window');
  return h;
}

// -- 1. Static contract: the cancellable phase never stops the backend --------
assert.ok(!/app\.on\(\s*['"]before-quit['"]/.test(mainSource),
  'no before-quit handler: that phase precedes the renderer close veto');
assert.ok(!/app\.on\(\s*['"]will-quit['"]/.test(mainSource),
  'will-quit can still be vetoed and must not stop the backend');
assert.match(mainSource, /app\.on\('quit', stopBackend\);/, 'the final quit still stops the owned child');
assert.match(mainSource, /process\.on\('exit', stopBackend\);/, 'a main-process exit still stops the owned child');
assert.equal((mainSource.match(/stopBackend\s*\)/g) || []).length, 2, 'exactly the two non-cancellable cleanup registrations');
console.log('PASS static: stopBackend is registered on quit and process exit only');

// -- 2. Boot, geometry and menu are the shipped ones ------------------------
let h = await boot();
assert.equal(h.spawned.length, 1, 'one owned backend child per launch');
const spawn = h.spawned[0];
assert.equal(spawn.command, PYTHON, 'the bundled interpreter starts the backend');
const argv = Array.from(spawn.args);
assert.equal(argv[0], BACKEND, 'the bundled supervisor script is the entry');
for (const flag of ['--state-dir', '--ready-file', '--parent-pid']) {
  assert.ok(argv.includes(flag), `launch keeps ${flag}`);
}
assert.equal(argv[argv.indexOf('--parent-pid') + 1], '4242',
  'the child still supervises its parent pid');
assert.equal(spawn.options.env.PYTHONNOUSERSITE, '1', 'no user site-packages leak into the backend');
assert.equal(spawn.options.env.PYTHONHOME, '', 'the bundle runtime is isolated from PYTHONHOME');
assert.equal(spawn.options.stdio[0], 'ignore', 'the backend inherits no stdin');
assert.deepEqual(h.openedUrls, [APP_URL], 'the window loads the loopback URL the backend reported');

const win = h.windows[0];
// The options object is built inside the vm realm, so it is copied into plain
// data before a deep comparison (its prototype is that realm's Object).
const winOptions = JSON.parse(JSON.stringify(win.options));
const { width, height, ...rest } = winOptions;
assert.deepEqual({ width, height }, { width: 1089, height: 491 }, '80%-bounded boot geometry is unchanged');
assert.equal(rest.frame, false, 'frameless shell preserved');
assert.equal(rest.transparent, true, 'transparent shell preserved');
assert.equal(rest.backgroundColor, '#00000000', 'console background preserved');
assert.equal(rest.hasShadow, false, 'console shadow stays off');
assert.deepEqual([rest.minWidth, rest.minHeight], [720, 350], 'minimum size preserved');
assert.equal(rest.show, false, 'the window still waits for ready-to-show');
assert.deepEqual(rest.webPreferences, {
  preload: path.join(path.dirname(MAIN), 'preload.js'),
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  spellcheck: true,
}, 'renderer isolation settings preserved');

assert.equal(h.menuBuilt, 1, 'the shipped menu module builds the application menu once');
const quitRole = h.menu.template[0].submenu.find((item) => item.role === 'quit');
const closeRole = h.menu.template[1].submenu.find((item) => item.role === 'close');
assert.equal(quitRole.accelerator, 'CmdOrCtrl+Q', 'quit accelerator unchanged');
assert.equal(closeRole.accelerator, 'CmdOrCtrl+W', 'close accelerator unchanged');
console.log('PASS boot: one owned child, shipped geometry, shipped menu');

// -- 3. Cancelled quit (Keep editing): the backend must stay up -------------
win.vetoes = true;                        // the renderer has unsaved writing
h.answer([0]);                            // Keep editing (the default answer)
const cancelled = h.app.quit();
assert.equal(cancelled, 'cancelled:window-close', 'the veto cancels the quit');
assert.equal(h.windows.length, 1, 'the editor window stays open after Keep editing');
assert.equal(win.destroyed, false, 'the window is not destroyed');
assert.equal(h.events.willQuit, 0, 'the non-cancellable quit phase never started');
assert.deepEqual(h.child.kills, [], 'the owned backend was NOT signalled by the cancelled quit');
assert.equal(h.child.signalCode, null, 'the backend child is still running');
assert.equal(h.spawned.length, 1, 'no second backend is started behind the live window');
assert.equal(h.prompts.length, 1, 'exactly one native prompt per quit attempt');
const prompt = { ...h.prompts[0], options: JSON.parse(JSON.stringify(h.prompts[0].options)) };
assert.equal(prompt.hasWindow, true, 'the prompt is window-modal on its own window');
assert.equal(prompt.options.type, 'warning');
assert.equal(prompt.options.defaultId, 0, 'the non-destructive answer stays the default');
assert.equal(prompt.options.cancelId, 0, 'Escape and window-close stay Keep editing');
assert.equal(prompt.options.noLink, true);
assert.deepEqual(prompt.options.buttons, UNSAVED.buttons, 'button labels unchanged');
assert.equal(prompt.options.message, UNSAVED.message, 'prompt message unchanged');
assert.equal(prompt.options.detail, UNSAVED.detail, 'prompt detail unchanged');
console.log('PASS cancelled quit: window open, backend alive, one unchanged native prompt');

// The same window and the same child still answer: a later save/compile uses them.
assert.equal(h.child.pid, 9001, 'the live session keeps its own backend child');
h.answer([0]);
assert.equal(h.app.quit(), 'cancelled:window-close', 'a second cancelled quit behaves the same');
assert.deepEqual(h.child.kills, [], 'repeat cancellation still never signals the backend');
assert.equal(h.prompts.length, 2, 'one prompt per attempt, no double prompt per attempt');
console.log('PASS repeat cancel: no backend signal, no double prompt');

// -- 4. Accepted quit (Close without saving): the backend is stopped once ----
h.answer([1]);
assert.equal(h.app.quit(), 'quit', 'the discard answer lets the quit complete');
assert.equal(h.windows.length, 0, 'the window really closes on that same attempt');
assert.equal(h.prompts.length, 3, 'the accepted attempt asked once, not in a loop');
assert.equal(h.events.quit, 1, 'the non-cancellable quit phase ran once');
assert.deepEqual(h.child.kills, ['SIGTERM'], 'the owned backend is stopped exactly once');
console.log('PASS accepted quit: window closed, owned backend stopped exactly once');

// -- 5. Clean quit: no prompt, backend stopped once -------------------------
h = await boot();
h.answer([]);
assert.equal(h.app.quit(), 'quit', 'a window with no unsaved work quits');
assert.equal(h.windows.length, 0, 'the clean window closes');
assert.deepEqual(h.prompts, [], 'a clean quit never asks');
assert.deepEqual(h.child.kills, ['SIGTERM'], 'a clean quit also stops the owned backend once');
console.log('PASS clean quit: 0 prompts, backend stopped exactly once');

// A will-quit veto happens after windows close, but the app is still alive.
h = await boot();
const vetoWillQuit = (event) => event.preventDefault();
h.app.on('will-quit', vetoWillQuit);
assert.equal(h.app.quit(), 'cancelled:will-quit');
assert.equal(h.windows.length, 0, 'will-quit runs after the windows close');
assert.deepEqual(h.child.kills, [], 'late quit cancellation keeps the backend');
h.app.removeListener('will-quit', vetoWillQuit);
assert.equal(h.app.quit(), 'quit');
assert.deepEqual(h.child.kills, ['SIGTERM'], 'the later final quit still cleans up once');
console.log('PASS will-quit veto: backend survives until the final quit');

// -- 6. Main-process exit without a quit still stops the child -------------
h = await boot();
h.runExitListeners();
assert.deepEqual(h.child.kills, ['SIGTERM'], 'process exit stops the owned backend once');
assert.equal(h.windows.length, 1, 'this leg models an exit that skips the window close');
console.log('PASS process exit: owned backend stopped once');

// -- 7. Failure-safe teardown of the packaged leg (owned resources only) ----
// The packaged leg launches a real app that starts its own backend and writes
// into a throwaway profile. A failed assertion or a timeout in the middle of
// that leg would otherwise leave a window, a backend process and an occupied
// port behind for the next run (or for the user's own copy of the app). So
// everything the leg creates is recorded HERE, at the moment it exists, and
// teardownOwned() releases exactly that recorded set:
//   * the Playwright handle of the app THIS test launched,
//   * the backend pid that app's own isolated session reported in ready.json,
//   * the mkdtemp profile directories THIS test created (discarded when the leg
//     failed; a passing run keeps them as it always did).
// It never looks a process up by name, bundle path or port, and never removes a
// path it did not create, so a concurrently running review app, another agent's
// app and the user's real profile/state are unreachable from here. Teardown is
// idempotent and per-resource fault tolerant: the accepted-quit legs already
// close their own app, and one resource that misbehaves cannot stop the rest.
//
// The unsaved buffer this leg dirties is the test's own fixture: it is answered
// through the shipped unsaved-close prompt with its explicit discard button, so
// the product's unsaved-content protection is exercised, not bypassed, and only
// a temp profile holding that fixture is ever discarded.
const APP_CLOSE_TIMEOUT_MS = 20000;
const APP_EXIT_TIMEOUT_MS = 5000;
const owned = {
  apps: new Set(),
  backendPids: new Set(),
  profiles: new Set(),
  // ChildProcess handles of apps whose shutdown could not be confirmed. Kept so
  // the process-exit sweep can still signal them.
  retainedProcesses: new Set(),
};

function alive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function ownApp(launched) {
  owned.apps.add(launched);
  return launched;
}

function ownBackend(pid) {
  assert.ok(Number.isInteger(pid) && pid > 0, 'an owned backend pid is a positive integer: ' + pid);
  owned.backendPids.add(pid);
  return pid;
}

async function ownProfile(prefix) {
  const dir = await mkdtemp(path.join(tmpdir(), prefix));
  owned.profiles.add(dir);
  return dir;
}

// Playwright's close resolves only once the app process has exited, so a
// resolved close IS the exit verification and needs no further check. A close
// that REJECTS or hangs proves nothing: it can reject because the renderer is
// gone or because the app is still alive. Both cases must therefore be verified
// against the owned ChildProcess handle before anything downstream (profiles)
// is touched:
//   * no handle -> throw: the app's liveness cannot be established at all.
//   * handle already exited (exitCode/signalCode set) -> done, already verified.
//   * handle still live -> kill it and await bounded exit; throw if it stays.
// The handle is retained in owned.retainedProcesses while a failure is being
// reported, so the exit sweep can still reach it.
async function stopOwnedApp(launched, timeoutMs = APP_CLOSE_TIMEOUT_MS, exitTimeoutMs = APP_EXIT_TIMEOUT_MS) {
  if (typeof launched?.close !== 'function') throw new Error('owned app has no close()');
  // Bound only: the timer is always cleared in the same `finally`, so it can
  // never outlive this call nor keep the process alive after teardown.
  let timer = null;
  const timedOut = new Promise((resolve) => {
    timer = setTimeout(() => resolve('timeout'), timeoutMs);
  });
  const outcome = await Promise.race([
    Promise.resolve().then(() => launched.close()).then(() => 'closed').catch(() => 'close-failed'),
    timedOut,
  ]).finally(() => clearTimeout(timer));
  // Resolved close: the library already waited for the app process to exit.
  if (outcome === 'closed') return;
  // Rejected or hung close: the app may still be running, so verify it directly.
  const handle = typeof launched.process === 'function' ? launched.process() : null;
  if (!handle || typeof handle.kill !== 'function' || typeof handle.pid !== 'number') {
    throw new Error('close ' + (outcome === 'timeout' ? 'timed out' : 'rejected')
      + ' and the owned app exposes no process handle to verify exit with');
  }
  owned.retainedProcesses.add(handle);
  if (handle.exitCode === null && handle.signalCode === null) {
    try { handle.kill('SIGKILL'); } catch { /* already gone; exit is verified below */ }
  }
  const exited = await waitForProcessExit(handle, exitTimeoutMs);
  if (!exited) throw new Error('owned app process ' + handle.pid + ' did not exit after SIGKILL');
  owned.retainedProcesses.delete(handle);
}

// Exit is read from the handle itself (exitCode/signalCode), never inferred from
// elapsed time or from the absence of an error.
async function waitForProcessExit(handle, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (handle.exitCode === null && handle.signalCode === null && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return handle.exitCode !== null || handle.signalCode !== null;
}

// Signals only a pid that was recorded as owned, and only after re-checking it
// is alive. The packaged shell stops this same child on its own quit path; this
// is the belt for a leg that failed before its quit, and the child's own
// --parent-pid supervision covers a hard kill of this test process.
async function stopOwnedBackend(pid) {
  if (!Number.isInteger(pid) || pid <= 0) throw new Error('refusing to signal pid ' + pid);
  if (!alive(pid)) return true;
  try { process.kill(pid, 'SIGTERM'); } catch { /* raced to exit */ }
  for (let i = 0; i < 120 && alive(pid); i++) await new Promise((resolve) => setTimeout(resolve, 250));
  if (alive(pid)) {
    try { process.kill(pid, 'SIGKILL'); } catch { /* raced to exit */ }
    for (let i = 0; i < 40 && alive(pid); i++) await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return !alive(pid);
}

// Deletes a profile only when it is provably this test's: a direct child of the
// OS temp root with this test's own mkdtemp prefix.
async function discardOwnedProfile(dir) {
  assert.equal(path.dirname(dir), tmpdir(), 'only a test-created temp profile is removed: ' + dir);
  assert.match(path.basename(dir), /^wb-packaged-quit-/, 'only this test\'s profile prefix is removed: ' + dir);
  await rm(dir, { recursive: true, force: true });
}

// Stops exactly the owned processes. `discardProfiles` is set by the caller when
// the packaged leg FAILED: that run's throwaway user-data directories hold only
// this test's own fixture, so they are removed rather than accumulated by repeat
// failed runs. A successful run keeps its profiles exactly as before, so its
// ready.json/session reports remain available for the Lead to inspect.
//
// Profiles are discarded ONLY when every app and backend was verifiably stopped.
// A profile is the last evidence of an app that might still be running against
// it, so an unconfirmed cleanup keeps it (and says so) instead of deleting it.
async function teardownOwned({ discardProfiles = false } = {}) {
  const failures = [];
  const discarded = [];
  const kept = [];
  // Apps first: quitting the app is what stops the backend it owns, so this
  // leaves the pid sweep with nothing to do in the normal case.
  for (const launched of [...owned.apps]) {
    try {
      await stopOwnedApp(launched);
      owned.apps.delete(launched);
    } catch (error) {
      // Not deleted: an app whose exit was not confirmed stays registered, so
      // the process-exit sweep can still reach it through its retained handle.
      failures.push('app: ' + error.message);
    }
  }
  for (const pid of [...owned.backendPids]) {
    try {
      if (!await stopOwnedBackend(pid)) failures.push('backend pid ' + pid + ' survived SIGKILL');
      else owned.backendPids.delete(pid);
    } catch (error) { failures.push('backend ' + pid + ': ' + error.message); }
  }
  if (discardProfiles) {
    if (failures.length) {
      kept.push(...owned.profiles);
      console.log('KEPT profiles of a failed run because app/backend cleanup was unconfirmed: ' + [...owned.profiles].join(' '));
    } else {
      for (const dir of [...owned.profiles]) {
        try { discarded.push(dir); await discardOwnedProfile(dir); owned.profiles.delete(dir); }
        catch (error) { failures.push('profile ' + dir + ': ' + error.message); }
      }
    }
  }
  if (discarded.length) console.log('DISCARDED profiles of a failed run: ' + discarded.join(' '));
  if (failures.length) throw new Error('owned teardown failed: ' + failures.join('; '));
}

// Every leg that owns real resources runs inside this, so a thrown assertion or
// a timeout cannot skip cleanup. A leg failure is rethrown unchanged; a teardown
// failure is reported on its own unless there is already a leg failure to report
// (then both appear, and neither is hidden).
async function withOwnedTeardown(body) {
  let legFailed = false;
  try {
    return await body();
  } catch (error) {
    legFailed = true;
    throw error;
  } finally {
    try {
      await teardownOwned({ discardProfiles: legFailed });
    } catch (error) {
      if (!legFailed) throw error;
      console.error('TEARDOWN_FAILED_AFTER_LEG_FAILURE', error.message);
    }
  }
}

// An exit that skips every await (SIGINT/abort) still signals only recorded
// processes: the backend pids this test recorded and the retained ChildProcess
// handles of apps whose shutdown was not confirmed. The child's --parent-pid
// supervision is the final backstop.
process.on('exit', () => {
  for (const pid of owned.backendPids) {
    if (!alive(pid)) continue;
    try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ }
  }
  for (const handle of owned.retainedProcesses) {
    if (handle.exitCode !== null || handle.signalCode !== null) continue;
    try { handle.kill('SIGKILL'); } catch { /* already gone */ }
  }
});

// -- 7a. Regression: teardown on deliberate failure (no Electron, no window) --
// The packaged legs only ever reach teardown after a full pass, which would let
// a broken cleanup path ship untested. These legs fail on purpose.
const sleeper = () => spawnProcess(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });

// Stand-ins carry the same shape teardown relies on in the real app: a close()
// and a process() that returns the owned ChildProcess handle.
function appStandIn({ close, handle }) {
  return { close, process: () => handle };
}

{
  const dir = await ownProfile('wb-packaged-quit-regression-');
  const foreignDir = await mkdtemp(path.join(tmpdir(), 'wb-quit-unowned-'));
  const started = Date.now();
  let closedApp = 0;
  let ownedChildPid = 0;
  let control = null;
  let legError = null;
  try {
    await withOwnedTeardown(async () => {
      // The state a packaged leg is in when one of its assertions fails: a
      // launched app, a live backend child and a profile, all recorded. The
      // stand-in app reports a real handle that it really terminates, which is
      // what the shipped library does on a successful close.
      const appHandle = sleeper();
      ownApp(appStandIn({
        close: async () => { closedApp += 1; appHandle.kill('SIGKILL'); },
        handle: appHandle,
      }));
      control = sleeper();
      const ownedChild = sleeper();
      ownedChildPid = ownBackend(ownedChild.pid);
      assert.equal(alive(ownedChildPid), true, 'the recorded backend child really runs');
      assert.equal(alive(control.pid), true, 'the unowned control process really runs');
      throw new Error('deliberate leg failure');
    }).catch((error) => { legError = error; });
    assert.equal(legError?.message, 'deliberate leg failure', 'the leg failure surfaces, it is not swallowed');
    assert.equal(closedApp, 1, 'the failure path closed the app it owned');
    assert.equal(alive(ownedChildPid), false, 'the failure path stopped the backend it owned');
    assert.equal(existsSync(dir), false, 'the failure path discarded the profile it created');
    assert.deepEqual([...owned.apps, ...owned.backendPids, ...owned.profiles, ...owned.retainedProcesses], [],
      'teardown leaves no owned resource registered');
    // Idempotent with nothing registered, and never a directory it did not create.
    await teardownOwned();
    assert.equal(existsSync(foreignDir), true, 'a temp directory this test did not create is untouched');
    assert.equal(alive(control.pid), true, 'a process never recorded as owned is never signalled');
    assert.ok(Date.now() - started < 60000, 'the failure-path teardown completes promptly');
  } finally {
    if (control && alive(control.pid)) control.kill('SIGKILL');
    await rm(foreignDir, { recursive: true, force: true });
  }
}

// A recorded backend child and an unrecorded look-alike: teardown stops only the
// recorded one, so it cannot reach another app's backend or a user process. Both
// stand-ins are released in `finally`, so this leg leaves nothing behind even if
// one of its own assertions fails.
{
  const ownedChild = sleeper();
  const lookAlike = sleeper();
  const appHandle = sleeper();
  try {
    let closed = 0;
    await withOwnedTeardown(async () => {
      ownApp(appStandIn({ close: async () => { closed += 1; appHandle.kill('SIGKILL'); }, handle: appHandle }));
      ownBackend(ownedChild.pid);
      assert.equal(alive(ownedChild.pid), true, 'the recorded backend child runs');
    });
    assert.equal(closed, 1, 'the owned app is closed exactly once');
    assert.equal(alive(ownedChild.pid), false, 'the recorded backend child is stopped');
    assert.equal(alive(lookAlike.pid), true, 'an unrecorded process of the same shape is never signalled');
  } finally {
    for (const child of [ownedChild, lookAlike, appHandle]) {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
  }
}

// Close resolutions and failures, against stand-ins whose handle really exits or
// really does not. Every leg uses short injectable timeouts, so none waits long.
{
  // A resolved close is exit-verified by the library: no extra work, and teardown
  // returns without touching the handle.
  const exitedHandle = sleeper();
  const resolved = appStandIn({ close: async () => { exitedHandle.kill('SIGKILL'); }, handle: exitedHandle });
  await stopOwnedApp(resolved, 500, 500);
  await new Promise((resolve) => exitedHandle.once('exit', resolve));
  assert.deepEqual([...owned.retainedProcesses], [], 'a resolved close needs no handle verification');
  assert.notEqual(exitedHandle.signalCode, null, 'the resolved-close handle really exited');

  // Rejected close on an app that is ALREADY gone: verification passes on the
  // handle's own exit state, and teardown still needs no kill.
  const alreadyExited = sleeper();
  alreadyExited.kill('SIGKILL');
  await new Promise((resolve) => alreadyExited.once('exit', resolve));
  assert.notEqual(alreadyExited.exitCode ?? alreadyExited.signalCode, null, 'the stand-in already exited');
  const rejectedExited = appStandIn({
    close: async () => { throw new Error('renderer gone'); }, handle: alreadyExited,
  });
  await stopOwnedApp(rejectedExited, 500, 2000);
  assert.deepEqual([...owned.retainedProcesses], [], 'a verified-exited app is not retained for the sweep');

  // Rejected close on an app that is STILL LIVE: the owned handle must be killed
  // and its exit confirmed.
  const liveHandle = sleeper();
  try {
    const rejectedLive = appStandIn({
      close: async () => { throw new Error('close failed while the app is alive'); }, handle: liveHandle,
    });
    await stopOwnedApp(rejectedLive, 500, 5000);
    assert.notEqual(liveHandle.exitCode ?? liveHandle.signalCode, null,
      'a rejected close on a live app is verified by killing and awaiting the handle');
    assert.deepEqual([...owned.retainedProcesses], [], 'a confirmed exit is not retained');
  } finally {
    if (liveHandle.exitCode === null && liveHandle.signalCode === null) liveHandle.kill('SIGKILL');
  }

  // Hung close on a live app: same requirement, bounded by the injectable timeout.
  const hungHandle = sleeper();
  try {
    const hung = { closeCalls: 0, close() { this.closeCalls += 1; return new Promise(() => {}); }, process: () => hungHandle };
    const at = Date.now();
    await stopOwnedApp(hung, 100, 5000);
    assert.equal(hung.closeCalls, 1, 'the hung app was asked to close once');
    assert.notEqual(hungHandle.exitCode ?? hungHandle.signalCode, null, 'the hung app handle was killed and verified');
    assert.ok(Date.now() - at < 10000, 'a hung app close is bounded by its timeout');
  } finally {
    if (hungHandle.exitCode === null && hungHandle.signalCode === null) hungHandle.kill('SIGKILL');
  }

  // A rejected close with NO handle: liveness cannot be established, so this must
  // throw rather than be treated as a completed close.
  await assert.rejects(
    () => stopOwnedApp({ close: async () => { throw new Error('close failed'); } }, 500, 2000),
    /no process handle/,
    'a rejected close without a handle is a failure, not a silent success');
}

// A cleanup that cannot be confirmed must fail loudly AND keep the profile, since
// that profile is the only evidence of an app that may still be using it.
{
  const dir = await ownProfile('wb-packaged-quit-regression-');
  const stubborn = {
    pid: 1,
    exitCode: null,
    signalCode: null,
    killed: 0,
    // Accepts the kill and never exits, standing in for an unkillable/hung app.
    kill() { this.killed += 1; return true; },
  };
  const failingClose = appStandIn({
    close: async () => { throw new Error('close failed while the app is alive'); }, handle: stubborn,
  });
  ownApp(failingClose);
  let teardownError = null;
  try {
    await teardownOwned({ discardProfiles: true });
  } catch (error) {
    teardownError = error;
  }
  assert.match(teardownError?.message ?? '', /did not exit after SIGKILL/,
    'an app that never exits is reported as a teardown failure');
  assert.equal(stubborn.killed, 1, 'the stubborn handle was signalled exactly once');
  assert.equal(existsSync(dir), true, 'the profile is preserved when app cleanup is unconfirmed');
  assert.equal(owned.apps.has(failingClose), true, 'the unconfirmed app stays registered for the exit sweep');
  assert.equal(owned.retainedProcesses.has(stubborn), true, 'its handle stays retained for the exit sweep');
  // Reset the fixture: it holds no real process, so nothing is left running.
  owned.apps.clear();
  owned.retainedProcesses.clear();
  owned.profiles.delete(dir);
  await rm(dir, { recursive: true, force: true });
}

// Guards: teardown only removes its own temp profiles and only signals recorded
// pids.
{
  await assert.rejects(() => discardOwnedProfile(path.join(tmpdir(), 'not-this-tests-')), /profile prefix/,
    'teardown refuses to remove a path outside its own prefix');
  await assert.rejects(() => stopOwnedBackend(0), /refusing to signal/,
    'teardown refuses to signal a pid that was never recorded as a process');
}
console.log('PASS teardown: a failed packaged leg closes its own app, backend and profile, and touches nothing else');
console.log('PASS teardown: rejected/hung closes are exit-verified, and unconfirmed cleanup keeps the profile and errors');

// -- 8. Opt-in: the real app, using an isolated copy with current source ----
const packagedApp = process.env.WB_PACKAGED_APP || '';
if (!packagedApp) {
  console.log('SKIP packaged GUI leg (no WB_PACKAGED_APP). Run with an isolated current-source app copy:');
  console.log('  WB_PACKAGED_APP="/path/to/Research Workbench Review.app" node tests/review_packaged_quit_lifecycle.mjs');
  console.log('PACKAGED_QUIT_LIFECYCLE_PASS');
  process.exit(0);
}

const { _electron: electron } = await import('playwright');
const executable = path.join(packagedApp, 'Contents', 'MacOS', 'Research Workbench Review');
assert.ok(existsSync(executable), 'the packaged executable exists: ' + executable);
assert.match(mainSource, /app\.on\('quit', stopBackend\);/, 'this source is the one to overlay as Contents/Resources/app/main.js');

async function session(profile, deadlineMs = 150000) {
  const root = path.join(profile, 'review-sessions');
  const until = Date.now() + deadlineMs;
  while (Date.now() < until) {
    try {
      for (const name of await readdir(root)) {
        try {
          const ready = JSON.parse(await readFile(path.join(root, name, 'ready.json'), 'utf8'));
          if (ready && ready.api && ready.identity && ready.identity.pid) return ready;
        } catch { /* not written yet */ }
      }
    } catch { /* session dir not created yet */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('the packaged backend never reported a session under ' + root);
}

async function api(base, route, options) {
  const response = await fetch(base + route, options);
  const text = await response.text();
  return { ok: response.ok, status: response.status, body: text ? JSON.parse(text) : null };
}

// A stopped backend must also release its listener: the port refuses connections.
async function portClosed(base, attempts = 60) {
  for (let i = 0; i < attempts; i++) {
    try {
      await fetch(base + '/projects');
    } catch {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function pidGone(pid, attempts = 60) {
  for (let i = 0; i < attempts && alive(pid); i++) await new Promise((resolve) => setTimeout(resolve, 250));
  return !alive(pid);
}

async function launchPackaged(profile) {
  const launched = await electron.launch({
    executablePath: executable,
    args: ['--user-data-dir=' + profile],
    timeout: 120000,
  });
  // Recorded before the first await that can fail: from here on, a timeout
  // while the window or the backend is coming up still tears this app down.
  ownApp(launched);
  const page = await launched.firstWindow({ timeout: 120000 });
  await page.waitForSelector('.desktop-shell', { state: 'attached', timeout: 120000 });
  const ready = await session(profile);
  // The backend this app started, from that app's own isolated session report.
  // Owned by the app: teardown quits the app first and only then sweeps the pid.
  ownBackend(ready.identity.pid);
  return { launched, page, ready };
}

async function stubPromptAnswer(launched, answers) {
  await launched.evaluate(({ dialog }, queued) => {
    globalThis.__wbQuitPrompts = globalThis.__wbQuitPrompts || [];
    globalThis.__wbQuitAnswers = queued.slice();
    dialog.showMessageBoxSync = (win, options) => {
      globalThis.__wbQuitPrompts.push(options);
      return globalThis.__wbQuitAnswers.length ? globalThis.__wbQuitAnswers.shift() : 0;
    };
  }, answers);
}

// A clean quit must ask nothing. The stub throws instead of answering, so an
// unexpected prompt cannot silently pass: the shell's own try/catch treats a
// failed prompt as "Keep editing", the quit is cancelled, and the close event
// below never arrives within its timeout.
async function refusePrompt(launched) {
  await launched.evaluate(({ dialog }) => {
    dialog.showMessageBoxSync = () => {
      throw new Error('unexpected unsaved-close prompt on a clean quit');
    };
  });
}

async function makeDirty(page, text) {
  await page.evaluate((draftText) => {
    latexState = { draft: { draft_id: 'review-draft', main_file: 'main.tex', revision: 1,
      files: [{ path: 'main.tex', text: draftText }] }, activeFile: 'main.tex', dirty: true, saving: false, serial: 0 };
    document.getElementById('latex-editor').value = draftText;
  }, text);
}

// Everything past this point owns real resources, so the whole packaged leg —
// all three sub-legs — runs under one teardown: a failed assertion or a timeout
// inside (a), (b) or (c) still closes the app this test launched, stops the
// backend it reported and removes the temp profiles it created. The legs keep
// their own explicit assertions about the quit path; teardown only guarantees
// no owned resource outlives the run.
await withOwnedTeardown(async () => {
  // (a) cancelled quit: the window and its backend survive, and the app's own
  // frontend save path still writes through that same backend.
  const profileA = await ownProfile('wb-packaged-quit-a-');
  const runA = await launchPackaged(profileA);
  const apiA = runA.ready.api;
  const backendPid = runA.ready.identity.pid;   // launchPackaged already recorded it
  assert.ok(alive(backendPid), 'the packaged app runs its own backend');
  const UNSAVED_TEXT = 'Unsaved packaged buffer text.';
  await makeDirty(runA.page, UNSAVED_TEXT);
  await stubPromptAnswer(runA.launched, [0, 1]);
  await runA.launched.evaluate(({ app }) => app.quit());
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const openAfterCancel = await runA.launched.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length);
  assert.equal(openAfterCancel, 1, 'Keep editing keeps the packaged window open');
  const promptsAfterCancel = await runA.launched.evaluate(() => globalThis.__wbQuitPrompts.map(
    (options) => ({ buttons: options.buttons, message: options.message, defaultId: options.defaultId,
      cancelId: options.cancelId, type: options.type })));
  assert.deepEqual(promptsAfterCancel, [{
    buttons: UNSAVED.buttons, message: UNSAVED.message, defaultId: 0, cancelId: 0, type: 'warning',
  }], 'one unchanged native prompt asked on the cancelled quit');
  assert.ok(alive(backendPid), 'the cancelled quit left the packaged backend running');
  assert.equal((await api(apiA, '/projects')).ok, true, 'the packaged API answers after a cancelled quit');
  const bufferAfterCancel = await runA.page.evaluate(() => ({
    text: document.getElementById('latex-editor').value,
    dirty: !!(latexState && latexState.dirty),
  }));
  assert.deepEqual(bufferAfterCancel, { text: UNSAVED_TEXT, dirty: true },
    'the unsaved buffer and its dirty flag survived the cancelled quit');

  // The later save goes through the shipped frontend (saveDraft -> PUT /drafts)
  // to the very backend this window started.
  const projectsAfterCancel = await api(apiA, '/projects');
  const projectId = projectsAfterCancel.body.projects[0].id;
  const created = await api(apiA, `/projects/${encodeURIComponent(projectId)}/drafts`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  assert.equal(created.ok, true, 'a real draft can be created after a cancelled quit');
  const draftId = created.body.draft_id;
  const savedByRenderer = await runA.page.evaluate((draft) => {
    latexState = { draft, activeFile: 'main.tex', dirty: true, saving: false, serial: 0 };
    document.getElementById('latex-editor').value = draft.files[0].text;
    return saveDraft('manual').then((result) => (result ? { revision: result.revision } : null));
  }, { ...created.body, files: [{ path: 'main.tex', text: UNSAVED_TEXT }] });
  assert.deepEqual(savedByRenderer, { revision: 1 }, 'the shipped save path succeeds after a cancelled quit');
  const reread = await api(apiA, `/drafts/${encodeURIComponent(draftId)}`);
  assert.equal(reread.ok, true, 'the saved draft can be read back');
  assert.equal(reread.body.revision, 1, 'the backend committed the new revision');
  assert.deepEqual(reread.body.files, [{ path: 'main.tex', text: UNSAVED_TEXT }],
    'the saved text is the unsaved buffer, so nothing was lost');
  console.log('PASS packaged: cancelled quit keeps the window, its buffer and its backend, and the later save commits');

  // (b) accepted quit: the unsaved prompt answered "Close without saving" closes
  // the window, and the owned backend and its port go away with it. The fixture
  // buffer is discarded here on purpose, through the product's own prompt.
  await makeDirty(runA.page, UNSAVED_TEXT + ' second draft');
  const closedA = new Promise((resolve) => runA.launched.on('close', resolve));
  await runA.launched.evaluate(({ app }) => app.quit()).catch(() => {});
  await Promise.race([closedA, new Promise((resolve, reject) =>
    setTimeout(() => reject(new Error('the packaged app did not quit after Close without saving')), 30000))]);
  assert.equal(await pidGone(backendPid), true, 'the accepted quit stopped the owned backend');
  assert.equal(await portClosed(apiA), true, 'the stopped backend released its API port');
  console.log('PASS packaged: accepted quit stopped the owned backend and closed its port');

  // (c) clean quit: no prompt, backend stopped.
  const profileC = await ownProfile('wb-packaged-quit-c-');
  const runC = await launchPackaged(profileC);
  const backendPidC = runC.ready.identity.pid;
  await refusePrompt(runC.launched);
  await runC.launched.evaluate(({ app }) => new Promise((resolve) => {
    app.once('will-quit', (event) => {
      event.preventDefault();
      resolve();
    });
    app.quit();
  }));
  assert.equal(await runC.launched.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 0);
  assert.ok(alive(backendPidC), 'a will-quit veto keeps the packaged backend running');
  assert.equal((await api(runC.ready.api, '/projects')).ok, true, 'API works after a will-quit veto');
  const reopened = runC.launched.waitForEvent('window', { timeout: 30000 });
  await runC.launched.evaluate(({ app }) => app.emit('activate'));
  const reopenedPage = await reopened;
  await reopenedPage.waitForSelector('[data-startup-field="social_science"]', { state: 'attached' });
  assert.equal((await api(runC.ready.api, '/projects')).ok, true, 'the reopened window keeps the same backend');
  console.log('PASS packaged: will-quit veto keeps the backend and permits reopening');
  const closedC = new Promise((resolve) => runC.launched.on('close', resolve));
  await runC.launched.evaluate(({ app }) => app.quit()).catch(() => {});
  await Promise.race([closedC, new Promise((resolve, reject) =>
    setTimeout(() => reject(new Error('a clean window did not quit: an unsaved-close prompt was asked')), 30000))]);
  assert.equal(await pidGone(backendPidC), true, 'a clean quit stopped the owned backend');
  assert.equal(await portClosed(runC.ready.api), true, 'a clean quit released the API port');
  console.log('PASS packaged: clean quit asked no prompt and stopped the owned backend');
});

console.log('PACKAGED_QUIT_LIFECYCLE_PASS');
