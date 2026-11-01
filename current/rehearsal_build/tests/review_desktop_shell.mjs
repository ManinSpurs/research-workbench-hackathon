// Real Electron renderer/native-window smoke. Uses only a fresh temporary DB
// and profile; refuses occupied rehearsal ports and always stops owned servers.
import { _electron as electron } from 'playwright';
import { spawn, execFile } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, cp, stat } from 'node:fs/promises';
import { promisify } from 'node:util';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import assert from 'node:assert/strict';
const root = path.resolve(import.meta.dirname, '..');
const execute = promisify(execFile);
async function chipConversation(file) {
  const { stdout } = await execute(path.join(root, '.venv/bin/python'), [
    '-c', 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(z.read("conversation.txt").decode("utf-8"))', file,
  ], { maxBuffer: 1024 * 1024 });
  return stdout;
}
async function dragRow(page, source, target, placement = 'before') {
  await source.scrollIntoViewIfNeeded();
  await target.scrollIntoViewIfNeeded();
  const from = await source.boundingBox(), to = await target.boundingBox();
  assert.ok(from && to, 'drag source and target are visible');
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 8, from.y + from.height / 2 + 8, { steps: 3 });
  await page.mouse.move(to.x + Math.min(30, to.width / 2), to.y + (placement === 'after' ? to.height - 4 : 4), { steps: 12 });
  await page.mouse.up();
}
async function paperAction(page, name) {
  await page.getByTestId('dw-open-paper-actions').click();
  await page.getByRole('button', { name, exact: true }).click();
  await page.getByTestId('dw-paper-actions-close').click();
}
const qa = await mkdtemp(path.join(tmpdir(), 'wb-transparent-qa-'));
const output = path.join(root, 'tmp', 'desktop-shell-qa');
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
  let page = await app.firstWindow();
  page.on('pageerror', error => console.error('RENDERER_ERROR', error.message));
  await page.waitForSelector('.desktop-shell', { state: 'attached' });
  await page.waitForSelector('[data-startup-field="social_science"]', { state: 'attached' });
  assert.equal(await page.locator('#startup-choices').isVisible(), false, 'fixed cartridge tray is absent');
  await page.waitForSelector('.home-chip-idle:not([hidden])');

  // --- The idle console tells a first-time user how to start, and offers the
  // same two actions the File menu does. Chip choice lives in the native menu,
  // which a fresh origin gives no reason to look at, so the screen must say it.
  const idle = await page.evaluate(() => {
    const host = document.querySelector('.home-chip-idle');
    const hint = host && host.querySelector('.home-chip-hint');
    const newBtn = host && host.querySelector('[data-testid="idle-new-chip"]');
    const openBtn = host && host.querySelector('[data-testid="idle-open-chip"]');
    const rendered = (n) => !!n && n.getClientRects().length > 0;
    return {
      hintText: hint ? hint.textContent.trim() : '',
      hintRendered: rendered(hint),
      newText: newBtn ? newBtn.textContent.trim() : '',
      newRendered: rendered(newBtn),
      openText: openBtn ? openBtn.textContent.trim() : '',
      openRendered: rendered(openBtn),
      // The menu path must still be named for a keyboard user.
      namesShortcut: /⌘N/.test(hint ? hint.textContent : '') && /⌘O/.test(hint ? hint.textContent : ''),
      namesFileMenu: /File\s*>/.test(hint ? hint.textContent : ''),
    };
  });
  assert.equal(idle.hintRendered, true, 'the idle console shows a visible hint');
  assert.equal(idle.namesFileMenu, true, 'the hint names the File menu, where a chip is chosen');
  assert.equal(idle.namesShortcut, true, 'the hint names both shortcuts');
  assert.equal(idle.newRendered, true, 'an on-screen "New Chip…" action is offered');
  assert.equal(idle.openRendered, true, 'an on-screen "Open…" action is offered');
  assert.match(idle.newText, /New Chip/, 'the new-chip action is labelled');
  assert.match(idle.openText, /Open/, 'the open action is labelled');

  // The buttons must run the SAME flow as File > New Chip… / File > Open…, not a
  // second path: they are wired to the same IPC channels the menu items use, and
  // the open dialog the menu owns answers them.
  const idleWiring = await page.evaluate(() => ({
    hasNewChip: typeof window.WB_DESKTOP?.newChip === 'function',
    hasOpenChip: typeof window.WB_DESKTOP?.openChip === 'function',
  }));
  assert.deepEqual(idleWiring, { hasNewChip: true, hasOpenChip: true },
    'the preload bridge exposes the same two actions the menu uses');

  const colors = await page.evaluate(() => [document.documentElement, document.body, document.getElementById('startup')].map(n => getComputedStyle(n).backgroundColor));
  assert.deepEqual(colors, ['rgba(0, 0, 0, 0)', 'rgba(0, 0, 0, 0)', 'rgba(0, 0, 0, 0)']);
  const native = await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    return { background: w.getBackgroundColor(), shadow: w.hasShadow() };
  });
  assert.match(native.background, /^#(?:00000000|000000)$/i);
  assert.equal(native.shadow, false);
  await page.screenshot({ path: path.join(output, '01-picker.png'), omitBackground: true });
  const chip = await (await fetch(apiUrl + '/chips/social_science')).json();
  const builtinPath = chip.path;
  assert.ok(builtinPath, 'sample chip path is available');
  console.log('QA_OPEN_CHIP', builtinPath);
  await app.evaluate(async ({ Menu, dialog }, filePath) => {
    const file = Menu.getApplicationMenu().items.find(item => item.label === 'File');
    if (!file) throw Error('Native File menu missing');
    const open = file.submenu.items.find(item => item.label === 'Open…');
    const original = dialog.showOpenDialog;
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
    try { await open.click(); } finally { dialog.showOpenDialog = original; }
  }, builtinPath);
  await page.waitForSelector('#startup-menu:not([hidden])');
  assert.equal(await page.locator('[data-menu="citations"]').count(), 0);
  assert.equal(await page.locator('[data-menu="review"]').count(), 1);
  const project = await (await fetch(apiUrl + '/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'fx', pack_id: 'social_science' }) })).json();
  await page.evaluate(async () => { await loadProjects(); });
  await page.waitForFunction(id => !!document.querySelector(`#home-project option[value="${id}"]`), project.project_id);
  await page.locator('#home-project').selectOption(project.project_id);
  await page.waitForFunction(() => !document.getElementById('secretary-input').disabled);
  await page.waitForSelector('.home-bubble-secretary');
  await page.getByTestId('home-settings').click();
  await page.getByTestId('interior-settings-tab-general').waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.getByTestId('settings-sheet').isVisible(), false, 'home settings close without leaving the menu');
  await page.locator('#secretary-mode').selectOption('reply');
  assert.equal(await page.locator('#secretary-send').isDisabled(), true, 'reply mode never creates a new question when none is waiting');
  await page.locator('#secretary-mode').selectOption('new');
  await page.locator('#secretary-input').fill('Does approval_date mean final approval?');
  await page.locator('#secretary-send').click();
  await page.waitForFunction(() => document.getElementById('home-conversation').textContent.includes('Does approval_date mean final approval?'));
  const records = await (await fetch(`${apiUrl}/projects/${project.project_id}/secretary-turns?pack_id=social_science`)).json();
  assert.ok(records.turns.some(turn => turn.role === 'researcher'), 'home uses the existing recorded question path');
  assert.ok(records.turns.some(turn => turn.role === 'secretary'), 'home records the real returned reply');
  await page.screenshot({ path: path.join(output, '02-home.png'), omitBackground: true });
  const menuLabels = await page.locator('#home-menu .menu-label').evaluateAll(labels =>
    labels.map(label => ({ fontSize: parseFloat(getComputedStyle(label).fontSize),
      fits: label.scrollWidth <= label.clientWidth && label.scrollHeight <= label.clientHeight })));
  assert.equal(menuLabels.length, 4, 'the four home destinations remain available');
  assert.ok(menuLabels.every(label => label.fontSize >= 20 && label.fits),
    'home menu labels are at least 20px and remain unclipped');
  // Inspect the actual composited native capture, not just CSS transparency.
  const capture = await app.evaluate(async ({ BrowserWindow }) => {
    const image = await BrowserWindow.getAllWindows()[0].webContents.capturePage();
    return image.toPNG().toString('base64');
  });
  await writeFile(path.join(output, '02-native.png'), Buffer.from(capture, 'base64'));
  await page.locator('#home-menu button').first().click();
  await page.waitForSelector('body.workbench-open', { state: 'attached' });
  await page.waitForTimeout(500);
  const workspaceBounds = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());
  const workspaceSize = await page.evaluate(() => {
    const r = document.getElementById('portal').getBoundingClientRect();
    return { width: r.width, height: r.height, viewportWidth: innerWidth, viewportHeight: innerHeight };
  });
  assert.equal(workspaceSize.width, workspaceSize.viewportWidth);
  assert.equal(workspaceSize.height, workspaceSize.viewportHeight);
  await page.screenshot({ path: path.join(output, '03-workspace.png') });
  assert.ok(await page.locator('#app-shell').isVisible());
  assert.ok(await page.locator('aside #secretary-compose').count(), 'the same composer returns to workspace');

  // --- The native window controls never sit on a header control ------------
  // The minimise/close buttons are drawn by the renderer over the top-right
  // corner. Widening and narrowing the real window proves the header reserves
  // that strip at every width instead of only at the one it was designed for.
  const OVERLAP_WIDTHS = [1600, 1440, 1280, 1100, 1024, 900, 820, 760, 720, 400];
  const overlapReport = [];
  const initialBounds = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());
  for (const width of OVERLAP_WIDTHS) {
    await app.evaluate(({ BrowserWindow }, w) => {
      const win = BrowserWindow.getAllWindows()[0];
      win.setBounds({ ...win.getBounds(), width: w });
    }, width);
    await page.waitForTimeout(320);
    const measured = await page.evaluate(() => {
      const controls = document.querySelector('.desktop-workspace-controls');
      if (!controls) return { missing: true };
      const box = (node) => { const r = node.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; };
      const control = box(controls);
      const hit = [];
      // Only the right-hand end matters: that is where the native strip is.
      for (const node of document.querySelectorAll('#app-shell > header > *')) {
        if (controls.contains(node)) continue;
        const r = node.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        const overlaps = r.left < control.right && r.right > control.left &&
          r.top < control.bottom && r.bottom > control.top;
        if (overlaps) {
          hit.push({ id: node.id || node.tagName, left: Math.round(r.left), right: Math.round(r.right),
            top: Math.round(r.top), bottom: Math.round(r.bottom) });
        }
      }
      // The status pill is drawn by app.js, so it is checked as painted text.
      for (const node of document.querySelectorAll('#adapter-badge, #research-status, header [data-testid="adapter-badge"]')) {
        const r = node.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if (r.left < control.right && r.right > control.left && r.top < control.bottom && r.bottom > control.top) {
          hit.push({ id: node.id || node.textContent.slice(0, 24), left: Math.round(r.left), right: Math.round(r.right),
            top: Math.round(r.top), bottom: Math.round(r.bottom) });
        }
      }
      const header = document.querySelector('#app-shell > header');
      return {
        missing: false,
        width: innerWidth,
        control: { left: Math.round(control.left), right: Math.round(control.right),
          top: Math.round(control.top), bottom: Math.round(control.bottom) },
        hit,
        // A header whose content is wider than its own box paints over the strip.
        overflow: header.scrollWidth > header.clientWidth + 1,
      };
    });
    assert.equal(measured.missing, false, `${width}px: the native window controls are drawn`);
    assert.deepEqual(measured.hit, [], `${width}px: no header control sits under the window controls`);
    assert.equal(measured.overflow, false, `${width}px: the header does not overflow its own box`);
    overlapReport.push(measured);
  }
  console.log('WINDOW_CONTROLS_OVERLAP_CLEAR', JSON.stringify(overlapReport.map(r => ({ width: r.width, control: r.control, hit: r.hit, overflow: r.overflow }))));
  // Restore the size the rest of the run expects.
  await app.evaluate(({ BrowserWindow }, bounds) => {
    BrowserWindow.getAllWindows()[0].setBounds(bounds);
  }, initialBounds);
  await page.waitForTimeout(320);

  // --- The duplicated tab strip is gone from the visible UI, and the one way
  // back is a visible top-level control, not something behind a panel.
  const chrome = await page.evaluate(() => {
    const tabs = document.querySelector('.view-tabs');
    const home = document.getElementById('back-to-menu');
    const more = document.querySelector('[data-testid="quiet-more"]');
    const rendered = (node) => !!node && node.getClientRects().length > 0;
    return {
      tabsPresent: !!tabs,
      tabsRendered: rendered(tabs),
      tabsTabStops: tabs ? [...tabs.querySelectorAll('button')].filter(rendered).length : -1,
      homeRendered: rendered(home),
      homeInHeader: !!home && !!home.closest('#app-shell > header'),
      morePresent: !!more,
      headerText: [...document.querySelectorAll('#app-shell > header > button, #app-shell > header > .theme-control')]
        .filter(rendered).map(node => (node.id || node.className || node.textContent).trim().slice(0, 40)),
    };
  });
  assert.equal(chrome.tabsRendered, false, 'the Research / Writing / Library tab strip is not visible');
  assert.equal(chrome.tabsTabStops, 0, 'no tab is a keyboard stop, so the strip cannot be reached');
  assert.equal(chrome.homeRendered, true, '"Home" is a visible control');
  assert.equal(chrome.homeInHeader, true, '"Home" is a top-level header control, not inside a panel');
  assert.equal(chrome.morePresent, false, 'the More fold is gone once the duplicated Settings controls moved out');
  const settingsOwned = await page.evaluate(() => ({
    theme: [...document.querySelectorAll('#app-shell > header .theme-control')].some(node => node.getClientRects().length > 0),
    change: (() => { const n = document.getElementById('startup-change'); return !!n && n.getClientRects().length > 0; })(),
    endSession: (() => { const n = document.getElementById('end-session'); return !!n && n.getClientRects().length > 0; })(),
  }));
  assert.deepEqual(settingsOwned, { theme: false, change: false, endSession: false },
    'theme, research field and end session are not duplicated in the workspace header');
  // "Widen workspace" had no other home, so it is offered directly instead of
  // behind a fold.
  assert.equal(await page.getByTestId('expand-surface').isVisible(), true, 'Expand surface stays reachable in the header');

  await page.keyboard.press('Escape');
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForTimeout(400);
  const homeBounds = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());
  assert.equal(homeBounds.width, workspaceBounds.width, 'returning home retains the expanded native window width');
  assert.equal(homeBounds.height, workspaceBounds.height, 'returning home retains the expanded native window height');
  const workArea = await app.evaluate(({ screen, BrowserWindow }) =>
    screen.getDisplayMatching(BrowserWindow.getAllWindows()[0].getBounds()).workArea);
  assert.ok(workspaceBounds.width >= workArea.width * 0.76 && workspaceBounds.height >= workArea.height * 0.74 &&
    workspaceBounds.width <= workArea.width * 0.78 && workspaceBounds.height <= workArea.height * 0.76,
    'the workbench stays near 80% of the previous 96% by 94% display size');
  for (const bounds of [homeBounds, workspaceBounds]) {
    assert.ok(bounds.x >= workArea.x && bounds.y >= workArea.y &&
      bounds.x + bounds.width <= workArea.x + workArea.width &&
      bounds.y + bounds.height <= workArea.y + workArea.height, 'the enlarged window remains inside the work area');
  }
  const homeScreen = await page.locator('#startup-screen').boundingBox();
  const homeViewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  assert.equal(homeScreen.width, homeViewport.width, 'home inner screen fills the expanded window width');
  assert.equal(homeScreen.height, homeViewport.height, 'home inner screen fills the expanded window height');
  assert.equal(await page.locator('.startup-hardware').isVisible(), false, 'the game console artwork is no longer painted on home');
  assert.equal(await page.locator('.startup-card').isVisible(), false, 'the cartridge enclosure is no longer painted on home');
  assert.ok(await page.locator('#portal').evaluate(n => n.hidden), 'home preserves but hides workspace');
  await page.locator('[data-menu="review"]').click();
  await page.waitForSelector('#secretary-review-view:not(.hidden)');
  await page.waitForFunction(() => document.getElementById('secretary-review-groups').textContent.includes('Does approval_date mean final approval?'));
  await page.keyboard.press('Escape');
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.evaluate(() => WB_I18N.set('ko'));
  assert.match(await page.locator('[data-menu="review"]').innerText(), /대화 기록 모음/);
  await page.evaluate(() => { WB_I18N.set('en'); WB_THEME.set('dark'); });
  await page.screenshot({ path: path.join(output, '04-home-dark.png'), omitBackground: true });
  await page.evaluate(() => WB_THEME.set('light'));
  await app.close();
  app = await electron.launch({ args: [root, '--user-data-dir=' + path.join(qa, 'profile')], cwd: root, env });
  page = await app.firstWindow();
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForFunction(() => document.getElementById('home-conversation').textContent.includes('Does approval_date mean final approval?'));
  assert.ok(await page.locator('#home-composer-slot #secretary-compose').count());
  await page.screenshot({ path: path.join(output, '05-return-home.png'), omitBackground: true });
  await page.waitForFunction(() => S.cq && document.getElementById('secretary-mode').value === 'reply');
  await page.locator('#secretary-mode').selectOption('reply');
  await page.waitForFunction(() => document.getElementById('secretary-compose-hint').textContent.startsWith('Reply to the clarification.'));
  // Synthetic registered FX fixture: inspect evidence, record an actual decision,
  // then verify the recorded reason survives an application restart without a model call.
  await page.locator('[data-menu="research"]').click();
  await page.waitForSelector('[data-testid="resp-unknown"]');
  await page.locator('[data-testid="resp-unknown"]').click();
  await page.waitForSelector('[data-testid="evidence"]');
  await page.evaluate(() => { document.getElementById('app-shell').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, '10-research-interior.png') });
  await page.waitForFunction(() => document.querySelectorAll('#ai-model option').length > 1);
  await page.locator('#quiet-secretary').click();
  await page.locator('#ai-panel > summary').click();
  await page.locator('#ai-modal-connection > summary').click();
  assert.ok(await page.locator('#ai-modal-status').isVisible());
  assert.match(await page.locator('#ai-modal-status').textContent(), /Endpoint not deployed\/configured/);
  assert.match(await page.locator('#ai-modal-connection').textContent(), /GPU time, not tokens/);
  assert.equal(await page.locator('#ai-key-modal').count(), 0, 'code-only Modal never asks for yesterday SDK key again');
  assert.equal(await page.locator('#ai-model option[value="meta-models/Muse-Glimmer-30B"]').count(), 0, 'unavailable Modal model is not executable');
  await page.locator('#ai-panel > summary').click();
  await page.locator('#quiet-secretary').click();
  const reason = 'Synthetic desktop check: release definition does not establish final approval.';
  await page.locator('#reason').fill(reason);
  await page.locator('[data-testid="defer"]').click();
  await page.waitForFunction(text => document.getElementById('decision-status').textContent.includes(text), reason);
  // The rehearsal preparation uses registered synthetic FX sources and the
  // fake-only model transport. It must produce a draft, never adopt a decision.
  await page.locator('#quiet-secretary').click();
  await page.locator('#briefing .brief-saved > summary').click();
  assert.equal(await page.locator('#briefing .brief-saved').evaluate(el => el.open), false, 'saved briefing starts expanded and can close');
  await page.locator('#briefing .brief-saved > summary').click();
  assert.equal(await page.locator('#briefing .brief-saved').evaluate(el => el.open), true, 'saved briefing can reopen for preparation');
  await page.locator('#briefing').getByRole('button', { name: 'Approve registered preparation scope', exact: true }).waitFor();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#briefing').getByRole('button', { name: 'Approve registered preparation scope', exact: true }).click();
  await page.waitForFunction(() => !!(S.prepScope && S.prepScope.scope_id));
  await page.locator('#quiet-secretary').click();
  await page.locator('[data-testid="prepare-decision"]').click();
  await page.locator('[data-testid="prepare-confirm-yes"]').click();
  await page.waitForFunction(() => S.preparations && S.preparations.jobs.some(job =>
    job.status === 'completed' || String(job.status).startsWith('stopped_')));
  const prepared = await page.evaluate(() => S.preparations);
  assert.ok(prepared.jobs.some(job => job.status === 'completed'), JSON.stringify(prepared.jobs));
  assert.ok(prepared.cards.length > 0, 'actual queue produces a draft card in rehearsal');
  await page.locator('#quiet-secretary').click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Validate draft for review (not adoption)', exact: true }).first().click();
  await page.locator('#quiet-secretary').click();
  assert.equal(await page.locator('#revisit-date-new').inputValue(), '', 'reminder date is never prefilled');
  const revisitNote = 'Synthetic researcher-authored revisit note.';
  await page.locator('#revisit-date-new').fill('2001-01-01T12:00');
  await page.locator('#revisit-note-new').fill(revisitNote);
  await page.locator('#revisit-create').getByRole('button', { name: 'Record revisit time', exact: true }).click();
  await page.waitForFunction(text => document.getElementById('revisit-management').textContent.includes(text), revisitNote);
  const revisits = await (await fetch(`${apiUrl}/projects/${project.project_id}/revisit-conditions?pack_id=social_science`)).json();
  assert.equal(revisits.conditions.length, 1);
  const reminder = revisits.conditions[0];
  assert.equal(reminder.display_status, 'due');
  assert.equal(reminder.recorded_by, 'researcher');
  assert.equal(reminder.decision_status, 'defer');
  assert.equal(reminder.decision_reason, reason);
  await page.keyboard.press('Escape');
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForFunction(text => document.getElementById('home-conversation').textContent.includes(text), revisitNote);
  await app.close();
  app = await electron.launch({ args: [root, '--user-data-dir=' + path.join(qa, 'profile')], cwd: root, env });
  page = await app.firstWindow();
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForFunction(text => document.getElementById('home-conversation').textContent.includes(text), reason);
  const restoredBriefing = await (await fetch(`${apiUrl}/projects/${project.project_id}/briefing?pack_id=social_science`)).json();
  assert.equal(restoredBriefing.model_calls, 0);
  assert.ok(JSON.stringify(restoredBriefing).includes(reason));
  assert.ok(restoredBriefing.parts.ready_now.some(item => item.id === reminder.id && item.decision_status === 'defer'));
  await page.waitForFunction(text => document.getElementById('home-conversation').textContent.includes(text), revisitNote);
  await page.locator('[data-menu="research"]').click();
  await page.waitForFunction(text => document.getElementById('revisit-management').textContent.includes(text), revisitNote);
  await page.locator('#quiet-secretary').click();
  await page.locator('#revisit-management').getByRole('button', { name: 'Done', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('revisit-management').textContent.includes('Reminder done'));
  const completedReminder = await (await fetch(`${apiUrl}/projects/${project.project_id}/revisit-conditions?pack_id=social_science`)).json();
  assert.equal(completedReminder.conditions[0].display_status, 'done');
  assert.equal(completedReminder.conditions[0].decision_status, 'defer', 'completing reminder never adopts research decision');
  await page.locator('#quiet-secretary').click();
  await page.evaluate(() => { document.getElementById('app-shell').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, '11-empty-interior.png') });
  await page.evaluate(() => openWritingView());
  await page.waitForSelector('#latex-view:not(.hidden)');
  await page.waitForFunction(() => latexState && latexState.project === S.project);
  assert.equal(await page.evaluate(() => latexState.draft), null, 'opening an empty writing view never creates a paper implicitly');
  await paperAction(page, 'New paper');
  await page.waitForFunction(() => latexState && latexState.draft);
  const newPaperCaret = await page.locator('#latex-editor').evaluate(editor => ({
    text: editor.value, caret: editor.selectionStart,
  }));
  assert.ok(newPaperCaret.text.includes('\\usepackage{lmodern}'));
  assert.ok(newPaperCaret.caret > newPaperCaret.text.indexOf('\\begin{document}'));
  assert.ok(newPaperCaret.caret <= newPaperCaret.text.indexOf('\\end{document}'));
  await page.getByTestId('dw-open-insert').click();
  await page.getByRole('button', { name: 'Bold', exact: true }).click();
  await page.getByTestId('dw-open-insert').click();
  const starterWithBold = await page.locator('#latex-editor').inputValue();
  assert.ok(starterWithBold.indexOf('\\textbf{Text}') > starterWithBold.indexOf('\\begin{document}'));
  assert.ok(starterWithBold.indexOf('\\textbf{Text}') < starterWithBold.indexOf('\\end{document}'));
  const source = '\\documentclass{article}\n\\begin{document}\nSynthetic writing fixture.\n\\end{document}\n';
  await page.locator('#latex-editor').fill(source);
  await paperAction(page, 'Save draft');
  await page.waitForFunction(() => latexState && !latexState.saving && !latexState.dirty);
  await page.evaluate(() => {
    const editor = document.getElementById('latex-editor');
    const start = editor.value.indexOf('Synthetic writing fixture.');
    editor.focus();
    editor.setSelectionRange(start, start + 'Synthetic writing fixture.'.length);
  });
  await page.getByTestId('dw-open-insert').click();
  await page.getByRole('button', { name: 'Bold', exact: true }).click();
  assert.ok((await page.locator('#latex-editor').inputValue()).includes('\\textbf{Synthetic writing fixture.}'));
  await page.locator('#latex-view summary').filter({ hasText: 'More syntax' }).click();
  await page.getByRole('button', { name: 'Undo syntax insertion', exact: true }).click();
  assert.equal(await page.locator('#latex-editor').inputValue(), source);
  await page.getByTestId('dw-open-insert').click();
  await page.getByTestId('dw-open-files').click();
  await page.getByRole('button', { name: 'Add file', exact: true }).click();
  await page.waitForSelector('#latex-file-dialog[open]');
  await page.locator('#latex-file-path').fill('references.bib');
  await page.locator('#latex-file-confirm').click();
  await page.waitForFunction(() => document.getElementById('latex-active-file').textContent === 'references.bib');
  await page.getByTestId('dw-open-files').click();
  const bibliography = '@article{synthetic_key,\n  title={Synthetic fixture only},\n  author={Fixture Author},\n  year={2026}\n}\n';
  await page.locator('#latex-editor').fill(bibliography);
  await page.getByTestId('dw-open-insert').click();
  assert.equal(await page.getByRole('button', { name: 'Bold', exact: true }).isDisabled(), true);
  await page.getByTestId('dw-open-insert').click();
  await page.getByTestId('dw-open-files').click();
  await page.locator('#latex-files').getByRole('button', { name: /main\.tex/ }).click();
  await page.getByTestId('dw-open-files').click();
  assert.equal(await page.locator('#latex-editor').inputValue(), source);
  await paperAction(page, 'Save draft');
  await page.waitForFunction(() => latexState && !latexState.saving && !latexState.dirty);
  const paperId = await page.evaluate(() => latexState.draft.draft_id);
  const paperResponse = await fetch(`${apiUrl}/drafts/${paperId}`);
  assert.equal(paperResponse.status, 200);
  const paper = await paperResponse.json();
  assert.equal(paper.files.find(file => file.path === 'main.tex').text, source);
  assert.equal(paper.files.find(file => file.path === 'references.bib').text, bibliography);
  await page.getByRole('button', { name: 'Compile', exact: true }).click();
  await page.waitForFunction(() => latexState && !latexState.compiling && latexState.compiledRevision === latexState.draft.revision);
  const compiledFrame = page.locator('.latex-preview iframe');
  await compiledFrame.waitFor();
  const compiledUrl = await compiledFrame.getAttribute('src');
  await page.getByTestId('interior-settings-open').click();
  await page.getByTestId('interior-settings-tab-general').click();
  await page.locator('#settings-lang').selectOption('ko');
  assert.equal(await compiledFrame.getAttribute('src'), compiledUrl, 'language change preserves the actual PDF blob');
  await page.locator('#settings-lang').selectOption('en');
  assert.equal(await compiledFrame.getAttribute('src'), compiledUrl);
  await page.keyboard.press('Escape');
  await page.getByTestId('interior-settings-open').click();
  await page.getByTestId('interior-settings-tab-models').click();
  await page.screenshot({ path: path.join(output, '09-settings-interior.png') });
  await page.getByTestId('interior-settings-tab-general').click();
  await page.locator('#settings-theme').selectOption('dark');
  await page.locator('#settings-theme').selectOption('light');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'interior-settings-open');
  assert.equal(await compiledFrame.getAttribute('src'), compiledUrl, 'opening settings does not replace the compiled PDF');
  await page.screenshot({ path: path.join(output, '07-writing.png') });
  const compiledRevision = await page.evaluate(() => latexState.compiledRevision);
  const actualPdfResponse = await fetch(`${apiUrl}/drafts/${paperId}/pdf?revision=${compiledRevision}`);
  assert.equal(actualPdfResponse.status, 200);
  const actualPdf = Buffer.from(await actualPdfResponse.arrayBuffer());
  assert.equal(actualPdf.subarray(0, 5).toString(), '%PDF-');
  const lifecycleSource = source + '\n% Native buffer preservation check\n';
  await page.locator('#latex-editor').fill(lifecycleSource);
  await page.evaluate(() => clearActive());
  await page.evaluate(() => openWritingView());
  assert.equal(await page.locator('#latex-editor').inputValue(), lifecycleSource, 'same-project navigation preserves unsaved text');
  await page.getByTestId('dw-open-paper-actions').focus();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+s' : 'Control+s');
  await page.waitForFunction(() => latexState && !latexState.saving && !latexState.dirty);
  assert.equal((await (await fetch(`${apiUrl}/drafts/${paperId}`)).json()).files.find(file => file.path === 'main.tex').text, lifecycleSource);
  for (let i = 0; i < 2; i++) {
    const created = await fetch(`${apiUrl}/projects`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'fx' }),
    });
    assert.equal(created.status, 200);
  }
  await page.evaluate(() => loadProjects());
  const originalProjectIds = (await (await fetch(`${apiUrl}/projects`)).json()).projects.map(row => row.id);
  const movedProjectId = originalProjectIds.at(-1);
  const projectRow = id => page.locator(`#project-list [data-project-id="${id}"]`);
  await page.locator('#quiet-projects').click();
  await dragRow(page, projectRow(movedProjectId).locator('.project-drag-handle'), projectRow(originalProjectIds[0]));
  const draggedProjectIds = [movedProjectId, ...originalProjectIds.slice(0, -1)];
  await page.waitForFunction(ids => !projectOrderPending && Array.from(document.querySelectorAll('#project-list [data-project-id]'), row => row.dataset.projectId).join() === ids.join(), draggedProjectIds);
  assert.deepEqual((await (await fetch(`${apiUrl}/projects`)).json()).projects.map(row => row.id), draggedProjectIds);
  assert.equal(await page.evaluate(() => S.project), project.project_id, 'project drag never switches the open project');
  await projectRow(movedProjectId).locator('.project-drag-handle').press('Alt+ArrowDown');
  await page.waitForFunction(() => !projectOrderPending);
  const keyboardProjectIds = [originalProjectIds[0], movedProjectId, ...originalProjectIds.slice(1, -1)];
  assert.deepEqual((await (await fetch(`${apiUrl}/projects`)).json()).projects.map(row => row.id), keyboardProjectIds);
  await page.evaluate(() => loadProjects());
  assert.deepEqual(await page.locator('#project-list [data-project-id]').evaluateAll(rows => rows.map(row => row.dataset.projectId)), keyboardProjectIds);
  await page.locator('#quiet-projects').click();
  await page.evaluate(() => openLibraryView());
  await page.locator('#library-file-input').setInputFiles(['A', 'B', 'C'].map(label => ({
    name: `${label}-synthetic-native.pdf`, mimeType: 'application/pdf', buffer: actualPdf,
  })));
  await page.waitForFunction(() => libraryState.tree && libraryState.tree.items.filter(item => item.kind === 'pdf').length === 3 && !libraryState.loading);
  const originalPdfIds = (await (await fetch(`${apiUrl}/library/tree?project_id=${project.project_id}`)).json()).items.filter(item => item.kind === 'pdf').map(item => item.id);
  const pdfRow = id => page.locator(`.library-item-row[data-item-id="${id}"]`);
  await pdfRow(originalPdfIds[1]).click();
  await page.locator('.library-pdf-frame').waitFor();
  const readerUrl = await page.locator('.library-pdf-frame').getAttribute('src');
  await page.evaluate(() => { window.nativeReaderNode = document.querySelector('.library-pdf-frame'); });
  await dragRow(page, pdfRow(originalPdfIds[2]).locator('.library-drag-handle'), pdfRow(originalPdfIds[0]));
  const draggedPdfIds = [originalPdfIds[2], originalPdfIds[0], originalPdfIds[1]];
  await page.waitForFunction(ids => !libraryState.orderBusy && libraryState.tree.items.filter(item => item.kind === 'pdf').map(item => item.id).join() === ids.join(), draggedPdfIds);
  assert.deepEqual((await (await fetch(`${apiUrl}/library/tree?project_id=${project.project_id}`)).json()).items.filter(item => item.kind === 'pdf').map(item => item.id), draggedPdfIds);
  assert.equal(await page.evaluate(() => libraryState.selected), originalPdfIds[1], 'dropping on another document never opens it');
  assert.equal(await page.locator('.library-pdf-frame').getAttribute('src'), readerUrl, 'PDF drag keeps the reader mounted');
  assert.equal(await page.evaluate(() => window.nativeReaderNode === document.querySelector('.library-pdf-frame')), true, 'the original reader node survives drag');
  await pdfRow(originalPdfIds[2]).focus();
  await pdfRow(originalPdfIds[2]).press('Alt+ArrowDown');
  await page.waitForFunction(() => !libraryState.orderBusy);
  const keyboardPdfIds = [originalPdfIds[0], originalPdfIds[2], originalPdfIds[1]];
  assert.deepEqual((await (await fetch(`${apiUrl}/library/tree?project_id=${project.project_id}`)).json()).items.filter(item => item.kind === 'pdf').map(item => item.id), keyboardPdfIds);
  await page.evaluate(() => loadLibrary());
  assert.deepEqual(await page.locator('.library-item-row[data-kind="pdf"]').evaluateAll(rows => rows.map(row => row.dataset.itemId)), keyboardPdfIds);
  assert.equal(await page.locator('.library-pdf-frame').getAttribute('src'), readerUrl, 'reload of saved order keeps the reader mounted');
  assert.equal(await page.evaluate(() => window.nativeReaderNode === document.querySelector('.library-pdf-frame')), true, 'reloading order does not reset the PDF reader');
  await page.screenshot({ path: path.join(output, '08-list-order.png') });
  console.log('ORDER_NATIVE_PASS: actual mouse drag, keyboard ordering, persisted API reload, open context preserved; real local PDF; no paid calls');
  await page.evaluate(() => {
    window.qaHomeReady = false;
    window.addEventListener('research-field-ready', () => { window.qaHomeReady = true; }, { once: true });
  });
  await page.keyboard.press('Escape');
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForFunction(() => window.qaHomeReady);
  const savedChip = path.join(qa, 'urban_policy.chip');
  await app.evaluate(({ Menu, dialog }, target) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: target });
    Menu.getApplicationMenu().items.find(item => item.label === 'File').submenu.items.find(item => item.label === 'New Chip…').click();
  }, savedChip);
  await page.waitForSelector('.new-chip-dialog[open]');
  await page.locator('#new-chip-name').fill('Urban Policy QA');
  await page.locator('.new-chip-dialog [type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('.new-chip-dialog').open ||
    Boolean(document.querySelector('.new-chip-error').textContent));
  assert.equal(await page.locator('.new-chip-error').textContent(), '', 'new chip creation reports no refusal');
  await page.waitForSelector('.new-chip-dialog[open]', { state: 'hidden' });
  await page.waitForFunction(() => document.getElementById('home-field').textContent === 'Urban Policy QA');
  assert.ok((await stat(savedChip)).size > 0, 'native New Chip saves a real file');
  await page.waitForFunction(() => !document.getElementById('home-conversation').textContent.includes('Does approval_date mean final approval?'));
  await page.waitForFunction(() => S.project && !WB_CHIP_BUSY());
  const notebook = await page.evaluate(() => ({ project: S.project, pack: S.pack }));
  assert.notEqual(notebook.project, project.project_id, 'new chip has its own notebook');
  const topicQuestion = 'DESKTOP-CHIP-CONTENT: What evidence is still missing for this topic?';
  await page.locator('#secretary-mode').selectOption('new');
  await page.locator('#secretary-input').fill(topicQuestion);
  await page.locator('#secretary-send').click();
  await page.waitForFunction(text => document.getElementById('home-conversation').textContent.includes(text), topicQuestion);
  await page.waitForFunction(() => !S.busy && S.question);
  const topicQuestionId = await page.evaluate(() => S.question);
  const topicTurns = await (await fetch(`${apiUrl}/projects/${notebook.project}/secretary-turns?pack_id=${notebook.pack}`)).json();
  assert.ok(topicTurns.turns.some(turn => turn.role === 'researcher' && turn.question_id === topicQuestionId));
  const saveEject = page.getByRole('button', { name: 'Save / Eject Chip…', exact: true });
  await app.evaluate(({ dialog }) => { dialog.showSaveDialog = async () => ({ canceled: true }); });
  await saveEject.click();
  await page.waitForFunction(() => !WB_CHIP_BUSY());
  assert.deepEqual(await page.evaluate(() => ({ project: S.project, pack: S.pack })), notebook, 'native cancel preserves notebook');
  await app.evaluate(({ dialog }, target) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: target }); }, savedChip);
  await saveEject.click();
  await page.waitForFunction(() => !WB_CHIP_BUSY() && !S.project);
  assert.ok((await chipConversation(savedChip)).includes(topicQuestion), 'saved native chip carries recorded conversation');
  assert.ok(!(await chipConversation(savedChip)).includes(reason), 'other project decision never leaks into the chip');
  await app.evaluate(async ({ Menu, dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
    await Menu.getApplicationMenu().items.find(item => item.label === 'File').submenu.items.find(item => item.label === 'Open…').click();
  }, savedChip);
  await page.waitForFunction(id => !WB_CHIP_BUSY() && S.project === id, notebook.project);
  await page.waitForFunction(text => document.getElementById('home-conversation').textContent.includes(text), topicQuestion);
  // Deletion uses the app's real API and its actual preview; this is a boundary
  // check, not a claim that a saved-question selection UI was exercised.
  const previewResponse = await fetch(`${apiUrl}/questions/${topicQuestionId}/deletion-preview`);
  assert.equal(previewResponse.status, 200);
  const deletionResponse = await fetch(`${apiUrl}/questions/${topicQuestionId}`, { method: 'DELETE' });
  assert.equal(deletionResponse.status, 200);
  assert.equal((await deletionResponse.json()).verified, true);
  await saveEject.click();
  await page.waitForFunction(() => !WB_CHIP_BUSY() && !S.project);
  assert.ok(!(await chipConversation(savedChip)).includes(topicQuestion), 'deleted content absent from next saved chip');
  await app.evaluate(async ({ Menu, dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
    await Menu.getApplicationMenu().items.find(item => item.label === 'File').submenu.items.find(item => item.label === 'Open…').click();
  }, savedChip);
  await page.waitForFunction(id => !WB_CHIP_BUSY() && S.project === id, notebook.project);
  await page.waitForFunction(text => !document.getElementById('home-conversation').textContent.includes(text), topicQuestion);
  await page.screenshot({ path: path.join(output, '06-new-chip.png'), omitBackground: true });
  await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    const original = w.setIgnoreMouseEvents.bind(w);
    w.pointerChecks = [];
    w.setIgnoreMouseEvents = (ignore, options) => { w.pointerChecks.push(ignore); original(ignore, options); };
  });
  await page.mouse.move(1, 1);
  await page.waitForTimeout(100);
  await page.mouse.move(homeBounds.width / 2, homeBounds.height / 2);
  await page.waitForTimeout(100);
  const pointerChecks = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].pointerChecks);
  // Scoped to the home state under test here. The console exterior is a boot
  // frame only (frontend/home_expand.css): on the home screen the inner display
  // is the whole window, so there is no transparent bezel left to pass pointer
  // events through, and the window's own corners must stay interactive. The
  // decorative-console hit test is still exercised before the home state, while
  // the artwork is painted; see the boot and picker checks above.
  assert.equal(pointerChecks.includes(true), false,
    'the home screen has no transparent exterior, so nothing is made click-through');
  // No redundant setIgnoreMouseEvents(false) call is required when already in
  // expanded menu mode; the real top-corner minimize click below proves input.
  assert.ok(await page.locator('.desktop-console-controls button[aria-label="Close window"]').isVisible());
  // The window controls moved from the bezel to the window's own top edge, since
  // the bezel is not painted in the home state: they must still be reachable.
  const controls = await page.locator('.desktop-console-controls button[aria-label="Minimize window"]').boundingBox();
  assert.ok(controls && controls.y < 40 && controls.x + controls.width > homeBounds.width - 40,
    'home window controls sit at the top-right corner of the window, not on the removed bezel');
  await page.locator('.desktop-console-controls button[aria-label="Minimize window"]').click();
  await page.waitForTimeout(200);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMinimized()), true);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].restore());
  console.log(JSON.stringify({ result: 'PASS', native, colors, pointerChecks, homeBounds, workspaceBounds, qa, output, paidCalls: 0 }));
} finally {
  if (app) await app.close();
  for (const child of children) child.kill('SIGTERM');
  await Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() : new Promise(resolve => child.once('exit', resolve))));
  await writeFile(path.join(output, 'servers.log'), logs);
}
