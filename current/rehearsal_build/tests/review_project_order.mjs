// Project list reorder: loads the real frontend (index.html, app.js, style.css,
// i18n.js, i18n.ko.js, the new project_order.css) in headless Chromium against an
// isolated in-memory mock of GET /projects and PUT /projects/order. No real
// backend, no database, no server of the user's, no paid call.
//
// Contract under test, as the backend worker delivers it:
//   GET  /projects        -> {projects: [public rows in saved order, new ones appended]}
//   PUT  /projects/order  -> {project_ids: [every current project id]} -> {projects: [rows in persisted order]}
//                            a list that is not the complete current set is refused 409.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const FILES = ['index.html', 'app.js', 'style.css', 'theme.js', 'i18n.js', 'i18n.ko.js', 'startup.js',
  'home_hub.js', 'canvas.js', 'latex.js', 'library.js', 'project_order.css', 'desktop_shell.css',
  'startup.css', 'home_hub.css', 'console_motion.css', 'writing.css'];

// index.html does not link project_order.css yet: the Lead adds that one line.
// The test injects the same link so the real stylesheet is what the checks read,
// and the missing link is reported separately for the Lead.
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  try {
    const file = path === '/' ? 'index.html' : path.slice(1);
    if (path.startsWith('/assets/')) {
      const asset = await readFile(root + file);
      res.setHeader('Content-Type', 'image/png'); res.end(asset); return;
    }
    if (!FILES.includes(file)) throw new Error('not served');
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript'
      : file.endsWith('.css') ? 'text/css' : 'text/html');
    if (file === 'index.html') {
      const html = await readFile(root + file, 'utf8');
      res.end(html.replace('</head>', '<link rel="stylesheet" href="project_order.css">\n</head>')); return;
    }
    res.end(await readFile(root + file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });

// --- Mock backend: the order lives here, like the real store does.
let projects = [
  { id: 'p1', name: 'Permit study', kind: 'nyc', pack_id: null },
  { id: 'p2', name: 'Housing study', kind: 'nyc', pack_id: null },
  { id: 'p3', name: 'Methods study', kind: 'nyc', pack_id: null },
];
const puts = [];
let failPut = null;      // 'error' -> 500, 'stale' -> 409
let appendOnSave = null; // a project registered while the save is in flight
let release = null;      // gate for an in-flight save
let gate = Promise.resolve();
function holdSave() { gate = new Promise(resolve => { release = resolve; }); }
// A project list read can also be held: it answers from the list as it was when
// the request arrived, and only then is released, which is exactly how a GET
// issued before a save can land after it.
let getHold = null, releaseGet = null, getHeld = 0;
function holdProjectsGet() { getHold = new Promise(resolve => { releaseGet = resolve; }); }

const rowsOf = list => list.map(p => ({ id: p.id, name: p.name, kind: p.kind, pack_id: p.pack_id }));

try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  await page.route('http://wb.test/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { body = null; }
    const reply = data => route.fulfill({ json: data });
    if (path === '/domain-packs') return reply({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (path === '/chips') return reply({ chips: [] });
    if (path === '/chips/social_science') return reply({ source: 'builtin', pack_id: 'social_science' });
    if (path === '/projects' && req.method() === 'GET') {
      const snapshot = rowsOf(projects);
      const held = getHold;
      if (held) { getHold = null; getHeld++; await held; }
      return reply({ projects: snapshot });
    }
    if (path === '/projects' && req.method() === 'POST') {
      const created = { id: 'new' + (projects.length + 1), name: 'Created during save', kind: 'nyc', pack_id: null };
      projects = [...projects, created];
      return reply({ project_id: created.id, name: created.name });
    }
    if (path === '/projects/order' && req.method() === 'PUT') {
      puts.push({ body, keys: Object.keys(body || {}) });
      // The membership this save places is the one read when the request arrived:
      // a project registered after that is not part of it and is appended, which
      // is what the backend does with new rows.
      const atArrival = projects.map(p => p.id);
      await gate;
      if (failPut === 'error') {
        failPut = null;
        return route.fulfill({ status: 500, json: { message: 'The local program refused the change.' } });
      }
      const asked = Array.isArray(body && body.project_ids) ? body.project_ids : null;
      if (!asked || asked.length !== atArrival.length || atArrival.some(id => !asked.includes(id)) || failPut === 'stale') {
        failPut = null;
        return route.fulfill({ status: 409, json: { message: 'The project list changed. Reorder again.' } });
      }
      projects = [...asked.map(id => projects.find(p => p.id === id)).filter(Boolean),
        ...projects.filter(p => !asked.includes(p.id))];
      if (appendOnSave) { projects = [...projects, appendOnSave]; appendOnSave = null; }
      return reply({ projects: rowsOf(projects) });
    }
    if (/^\/projects\/[^/]+\/briefing$/.test(path))
      return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (/^\/projects\/[^/]+\/clarifications$/.test(path)) return reply({ clarifications: [] });
    if (/^\/projects\/[^/]+\/profile$/.test(path)) return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (/^\/projects\/[^/]+\/pack-memory$/.test(path)) return reply({ questions: [] });
    if (/^\/projects\/[^/]+\/secretary-turns/.test(path)) return reply({ turns: [] });
    if (/^\/projects\/[^/]+\/drafts$/.test(path)) return reply({ project_id: 'p1', drafts: [] });
    if (/^\/projects\/[^/]+\/revisit-conditions$/.test(path)) return reply({ conditions: [] });
    if (path === '/capabilities/latex') return reply({ available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null }, note: 'No TeX engine found on this machine.' });
    if (path === '/briefing') return reply({ rows: [] });
    return reply({});
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await page.goto(`${origin}/?api=http://wb.test`);
  await page.getByTestId('home-menu-research').click();
  await page.waitForSelector('[data-testid="open-nyc"]');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length === 3);

  const names = () => page.$$eval('#project-list li.project-row', lis => lis.map(li => li.querySelector('a').textContent));
  const ids = () => page.$$eval('#project-list li.project-row', lis => lis.map(li => li.dataset.projectId));
  const open = () => page.evaluate(() => S.project);
  const row = name => page.locator('#project-list li.project-row', { has: page.locator(`a:text-is("${name}")`) });
  const handleOf = name => row(name).locator('[data-testid="project-handle"]');
  // Row geometry is read inside the page, and only once the layout has settled:
  // the home hub redraws the shared composer just after the workspace appears, so
  // a rect taken too early describes the previous layout. Each point is confirmed
  // by elementFromPoint, and a point that is not yet the handle is retried.
  const pointOf = (name, part) => page.evaluate(([n, p]) => {
    const li = [...document.querySelectorAll('#project-list li.project-row')]
      .find(el => el.querySelector('a').textContent === n);
    if (!li) return null;
    const el = p === 'handle' ? li.querySelector('[data-testid="project-handle"]') : li;
    const r = el.getBoundingClientRect();
    const x = r.x + r.width / 2;
    const y = p === 'handle' ? r.y + r.height / 2 : r.y + (p === 'below' ? r.height * 0.75 : r.height * 0.2);
    const at = document.elementFromPoint(x, y);
    return {x, y, rect: {x: r.x, y: r.y, width: r.width, height: r.height},
      hit: p !== 'handle' || at === el};
  }, [name, part]);
  async function stablePoint(name, part) {
    for (let i = 0; i < 40; i++) {
      const point = await pointOf(name, part);
      if (point && point.hit) return point;
      if (process.env.WB_DEBUG_DRAG) console.log('NOHIT', name, part, JSON.stringify(point), JSON.stringify(await page.evaluate(([n, p]) => {
        const li = [...document.querySelectorAll('#project-list li.project-row')]
          .find(el => el.querySelector('a').textContent === n);
        const el = p === 'handle' ? li && li.querySelector('[data-testid="project-handle"]') : li;
        const portal = document.querySelector('.display-portal');
        return {missing: !el, portalHidden: portal && portal.hidden, portalCls: portal && portal.className,
          startupCls: document.getElementById('startup').className,
          menuHidden: document.getElementById('startup-menu').hidden,
          navRect: document.querySelector('main > nav').getBoundingClientRect().toJSON(),
          rows: [...document.querySelectorAll('#project-list li.project-row')].map(l => [l.querySelector('a').textContent, Math.round(l.getBoundingClientRect().y)])};
      }, [name, part])));
      await page.waitForTimeout(25);
    }
    throw new Error('the pointer never reached ' + part + ' of ' + name);
  }
  // The workspace keeps settling for a moment after it appears (the home hub moves
  // its shared composer in), so a press is confirmed against the app's own state
  // and retried at a fresh point rather than trusted blindly.
  async function pressHandle(name) {
    for (let i = 0; i < 20; i++) {
      const point = await stablePoint(name, 'handle');
      await page.mouse.move(point.x, point.y);
      await page.mouse.down();
      if (await page.evaluate(() => !!projectDrag)) return point;
      await page.mouse.up();
      await page.waitForTimeout(50);
    }
    throw new Error('the drag did not start on ' + name);
  }
  const rowRect = name => pointOf(name, 'row');

  // Drag the handle of `from` until the insertion line sits before (`above`) or
  // after (`below`) the row named `to`, then drop. The point is re-aimed from live
  // geometry each time: the workspace settles for a moment after it appears, and
  // the assertion is about where the line lands relative to the pointer, which is
  // exactly what the feature promises.
  async function drag(from, to, where, midflight) {
    const handle = await pressHandle(from);
    const expected = await page.evaluate(([f, t, w]) => {
      const rows = [...document.querySelectorAll('#project-list li.project-row')]
        .filter(el => el.querySelector('a').textContent !== f);
      return rows.findIndex(el => el.querySelector('a').textContent === t) + (w === 'below' ? 1 : 0);
    }, [from, to, where]);
    let state = null;
    for (let i = 0; i < 12; i++) {
      const y = await page.evaluate(([t, w]) => {
        const li = [...document.querySelectorAll('#project-list li.project-row')]
          .find(el => el.querySelector('a').textContent === t);
        const r = li.getBoundingClientRect();
        return r.y + (w === 'below' ? r.height * 0.8 : r.height * 0.2);
      }, [to, where]);
      await page.mouse.move(handle.x, y, { steps: 6 });
      state = await page.evaluate(() => ({active: !!projectDrag && projectDrag.active,
        index: projectDrag ? projectDrag.index : null,
        marker: !!document.querySelector('li.project-drop-marker')}));
      if (state.active && state.marker && state.index === expected) break;
      await page.waitForTimeout(30);
    }
    assert.equal(state.index, expected,
      'the insertion line sits where the pointer is, before or after ' + to);
    await page.mouse.up();
    if (!midflight) await settle();
  }
  const settle = () => page.waitForFunction(() => !projectOrderPending);

  // --- (1) The dedicated handle is a real control; the row still opens.
  assert.equal(await page.getByTestId('project-handle').count(), 3, 'one handle per project');
  assert.equal(await page.locator('#project-list li.project-row a').first().getAttribute('href'), '#', 'the name is still the open link');
  assert.match(await page.getByTestId('project-handle').first().getAttribute('aria-label'), /Reorder Permit study/);
  assert.equal(await page.getByTestId('project-handle').first().evaluate(el => getComputedStyle(el).cursor),
    'grab', 'the new stylesheet gives the handle a grab cursor');
  assert.equal(await page.locator('#project-list li.project-row').first().evaluate(el => getComputedStyle(el).display),
    'grid', 'the row lays the handle out beside the name');

  // --- (2) Open a project, then reorder around it: the selection must not move.
  await page.getByTestId('open-nyc').nth(1).click();
  await page.waitForFunction(() => S.project === 'p2');
  assert.equal(await page.locator('#project-list a[aria-current="true"]').innerText(), 'Housing study');

  await drag('Permit study', 'Methods study', 'below');   // p1 -> last
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[2].dataset.projectId === 'p1');
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study'], 'drag moves the row downward');
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].keys, ['project_ids'], 'the save sends exactly one field');
  assert.deepEqual(puts[0].body.project_ids, ['p2', 'p3', 'p1'], '...the complete current id list');
  assert.equal(await open(), 'p2', 'the open project did not change');
  assert.equal(await page.locator('#project-list a[aria-current="true"]').innerText(), 'Housing study',
    'the selection mark followed the project, not the position');
  assert.equal(await page.locator('#project-list li.project-row.dragging').count(), 0, 'the dragged row is cleaned up');
  assert.equal(await page.locator('li.project-drop-marker').count(), 0, 'the insertion line is removed after the drop');
  await page.waitForFunction(() => /Order saved/.test(document.querySelector('[data-testid="project-order-status"]').textContent));
  assert.equal(await page.getByTestId('project-order-status').innerText(),
    'Order saved. Permit study is now number 3 of 3.', 'the save is announced in words');

  // --- (3) ...and upward.
  await drag('Permit study', 'Housing study', 'above');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[0].dataset.projectId === 'p1');
  assert.deepEqual(await names(), ['Permit study', 'Housing study', 'Methods study'], 'drag moves the row upward');
  assert.deepEqual(puts.at(-1).body.project_ids, ['p1', 'p2', 'p3']);

  // --- (4) The saved order is the backend's: a reload shows the server order.
  projects = [projects[1], projects[2], projects[0]];  // server holds p2,p3,p1
  await page.reload();
  await page.getByTestId('home-menu-research').click();
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length === 3);
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study'], 'a fresh GET paints the persisted order');
  await page.evaluate(() => loadProjects());
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[0].dataset.projectId === 'p2');
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study'], 'refreshing keeps the saved order');

  // --- (5) Cancelling a drag changes nothing and sends nothing.
  const putsBeforeCancel = puts.length;
  const handle = await pressHandle('Housing study');
  const aimBelow = async () => page.evaluate(() => {
    const li = [...document.querySelectorAll('#project-list li.project-row')]
      .find(el => el.querySelector('a').textContent === 'Permit study');
    const r = li.getBoundingClientRect();
    return r.y + r.height * 0.8;
  });
  await page.mouse.move(handle.x, await aimBelow(), { steps: 8 });
  assert.equal(await page.locator('li.project-drop-marker').count(), 1, 'the insertion line is visible while dragging');
  assert.equal(await page.locator('#project-list li.project-row.dragging').count(), 1, 'the dragged row is dimmed in place');
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await page.waitForFunction(() => !document.querySelector('li.project-drop-marker'));
  assert.equal(await page.locator('#project-list li.project-row.dragging').count(), 0, 'Escape also clears the dimmed row');
  assert.equal(puts.length, putsBeforeCancel, 'a cancelled drag saves nothing');
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study'], 'a cancelled drag keeps the order');
  assert.match(await page.getByTestId('project-order-status').innerText(), /Move cancelled/);
  // Cancelling is a workspace event, not a shell one: the capture-phase Escape
  // must not also return to the start menu or disturb the open project.
  assert.equal(await page.evaluate(() => document.getElementById('startup-menu').hidden), true,
    'Escape during a drag did not open the start menu');
  assert.equal(await page.evaluate(() => S.project), 'p2', 'Escape during a drag kept the open project');
  assert.equal(await page.evaluate(() => S.pack), 'social_science', 'Escape during a drag kept the chip');
  assert.equal(await page.locator('#project-list').isVisible(), true, 'the workspace and its list are still on screen');
  // The shell's Escape does not finish synchronously: backToMenu() only starts the
  // 540 ms collapse, so the checks above would still see a hidden menu if the key
  // had leaked. Read the same state once that motion would have completed.
  await page.waitForTimeout(700);
  const afterShellEscape = await page.evaluate(() => ({
    menuHidden: document.getElementById('startup-menu').hidden,
    portalHidden: document.getElementById('portal').hidden,
    bodyOpen: document.body.classList.contains('workbench-open'),
    researchPressed: document.getElementById('view-research').getAttribute('aria-pressed'),
    project: S.project, pack: S.pack,
  }));
  assert.deepEqual(afterShellEscape, {menuHidden: true, portalHidden: false, bodyOpen: true,
    researchPressed: 'true', project: 'p2', pack: 'social_science'},
    'the Escape that cancelled the drag never started the collapse back to the home menu');

  // --- (6) A failed save rolls back to the previous order with a plain sentence.
  failPut = 'error';
  const putsBeforeFail = puts.length;
  await drag('Housing study', 'Permit study', 'below');
  await page.waitForFunction(() => /Order not changed/.test(document.querySelector('[data-testid="project-order-status"]').textContent));
  assert.equal(puts.length, putsBeforeFail + 1, 'the drag did reach the save');
  assert.deepEqual(puts.at(-1).body.project_ids, ['p3', 'p1', 'p2'], '...with the moved order');
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study'], 'a refused save restores the previous order');
  assert.match(await page.locator('#error').innerText(), /^Error: The local program refused the change\.$/);
  assert.match(await page.getByTestId('project-order-status').innerText(), /Order not changed/);

  // --- (7) A stale list (409) is refused, and the project it hid is not lost.
  projects = [...projects, { id: 'p4', name: 'New permit study', kind: 'nyc', pack_id: null }];
  await drag('Permit study', 'Housing study', 'above');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length === 4);
  assert.deepEqual(await ids(), ['p2', 'p3', 'p1', 'p4'], 'the refused move did not reorder anything');
  assert.deepEqual(await names(), ['Housing study', 'Methods study', 'Permit study', 'New permit study'],
    'after the 409 the list is re-read, so the unseen project appears');
  assert.match(await page.locator('#error').innerText(), /The project list changed\. Reorder again\./);

  // --- (8) A project registered while the save is in flight is kept, not dropped.
  holdSave();
  const saving = drag('New permit study', 'Housing study', 'above', true);
  await page.waitForFunction(() => document.querySelector('#project-list').getAttribute('aria-busy') === 'true');
  appendOnSave = { id: 'p5', name: 'Added during save', kind: 'nyc', pack_id: null };
  release();
  await saving;
  await settle();
  assert.deepEqual(await ids(), ['p4', 'p2', 'p3', 'p1', 'p5'], 'the save answer carries the new project at the end');

  // --- (9) A GET that races the save cannot paint the pre-save order back.
  holdSave();
  const racing = drag('Added during save', 'Permit study', 'above', true);
  await page.waitForFunction(() => document.querySelector('#project-list').getAttribute('aria-busy') === 'true');
  await page.evaluate(() => loadProjects());
  assert.deepEqual(await ids(), ['p4', 'p2', 'p3', 'p5', 'p1'], 'the optimistic order survives a GET issued mid-save');
  release();
  await racing;
  await settle();
  assert.deepEqual(await ids(), ['p4', 'p2', 'p3', 'p5', 'p1'], 'the saved answer matches the optimistic order');

  // --- (10) Keyboard: Alt + Arrow moves the row and keeps focus on it.
  await handleOf('Housing study').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[2].dataset.projectId === 'p2');
  await settle();
  assert.deepEqual(puts.at(-1).body.project_ids, ['p4', 'p3', 'p2', 'p5', 'p1'], 'the keyboard path saves the same contract');
  assert.equal(await page.evaluate(() => {
    const el = document.activeElement;
    const li = el && el.closest && el.closest('li.project-row');
    return li ? li.dataset.projectId : null;
  }), 'p2', 'focus stays on the moved project handle');
  assert.match(await page.getByTestId('project-order-status').innerText(), /Order saved/);
  await page.keyboard.press('Alt+ArrowUp');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[1].dataset.projectId === 'p2');
  await settle();
  assert.deepEqual(puts.at(-1).body.project_ids, ['p4', 'p2', 'p3', 'p5', 'p1']);
  const putsAtBoundary = puts.length;
  await handleOf('New permit study').focus();
  await page.keyboard.press('Alt+ArrowUp');
  assert.match(await page.getByTestId('project-order-status').innerText(), /Already the first project/);
  assert.equal(puts.length, putsAtBoundary, 'a boundary move saves nothing');

  // --- (11) Dragging never opens or attaches: no accidental project switch.
  const openedBefore = await open();
  const attachesBefore = await page.locator('#project-list li.project-row > .project-row-body > button').count();
  await drag('Permit study', 'Housing study', 'above');
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row')[1].dataset.projectId === 'p1');
  assert.deepEqual(await ids(), ['p4', 'p1', 'p2', 'p3', 'p5'], 'the row landed where the pointer was, not at the end');
  assert.equal(await open(), openedBefore, 'dropping a row does not open it');
  assert.equal(await page.locator('#project-list li.project-row > .project-row-body > button').count(), attachesBefore,
    'the Attach to active chip action is still on the unbound rows');

  // --- (12) No second copy of the order is kept in the browser.
  const stored = await page.evaluate(() => Object.keys(localStorage).sort());
  assert.deepEqual(stored.filter(k => !['wb.pack', 'wb.project', 'wb.panels', 'wb.theme', 'wb.lang'].includes(k)),
    [], 'no key besides the app\u2019s existing preferences: no order copy');

  // --- (13) A long list still scrolls: the drag only takes over on the handle,
  // so the rest of the row, and the panel around it, scroll as before.
  const many = Array.from({ length: 30 }, (_, i) => ({ id: 'm' + i, name: 'Bulk study ' + i, kind: 'nyc', pack_id: null }));
  projects = [...projects, ...many];
  await page.evaluate(() => loadProjects());
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length === 35);
  const scrolling = await page.evaluate(() => {
    const shell = document.querySelector('.app-shell');
    const before = shell.scrollTop;
    shell.scrollTop = before + 300;
    const handle = document.querySelector('[data-testid="project-handle"]');
    const link = document.querySelector('#project-list li.project-row a');
    const out = { before, after: shell.scrollTop, scrollable: shell.scrollHeight > shell.clientHeight,
      handleTouch: getComputedStyle(handle).touchAction, linkTouch: getComputedStyle(link).touchAction };
    shell.scrollTop = before;
    return out;
  });
  assert.equal(scrolling.scrollable, true, 'a long project list makes the workspace scroll');
  assert.ok(scrolling.after > scrolling.before, 'the long project list scrolls normally');
  assert.equal(scrolling.handleTouch, 'none', 'only the handle suppresses the touch scroll');
  assert.notEqual(scrolling.linkTouch, 'none', 'the row itself still scrolls the list');

  // --- (14) The writing flush hook: a failed flush keeps the project and editor.
  const flush = outcome => page.evaluate(o => {
    window.__flushCalls = 0;
    window.latexBeforeProjectSwitch = async () => { window.__flushCalls++; return o; };
  }, outcome);
  const before = await open();
  assert.equal(before, 'p2', 'the open project before the flush checks');
  await flush(false);
  await page.evaluate(() => { window.__switched = openProject('p3', 'Methods study'); });
  await page.waitForFunction(() => window.__flushCalls === 1);
  await page.evaluate(() => window.__switched);
  assert.equal(await open(), before, 'a failed flush leaves the current project open');
  await flush(true);
  const pending = page.evaluate(() => { window.__switched = openProject('p3', 'Methods study'); return window.__switched; });
  await page.waitForFunction(() => window.__flushCalls === 1);
  // The flush resolves on the next microtask; give the switch its one await.
  await page.evaluate(() => Promise.resolve());
  assert.equal(await open(), 'p3', 'the switch happens once the flush reports success');
  await pending;
  await page.waitForFunction(() => S.project === 'p3');
  assert.equal(await page.evaluate(() => window.__flushCalls), 1, 'one flush per switch');
  // A refused flush is not silent: the writing view can be hidden, so its own
  // status line is off screen. The project and the editor context stay put.
  await page.evaluate(() => { document.getElementById('latex-view').classList.add('hidden'); });
  await flush(false);
  const editorContext = await page.evaluate(() => ({project: S.project,
    text: document.getElementById('latex-editor').value,
    label: document.getElementById('latex-draft-label').textContent}));
  await page.evaluate(() => { window.__switched = openProject('p1', 'Permit study'); });
  await page.waitForFunction(() => window.__flushCalls === 1);
  await page.evaluate(() => window.__switched);
  assert.equal(await page.evaluate(() => S.project), editorContext.project, 'a refused flush leaves the project alone');
  assert.deepEqual(await page.evaluate(() => ({text: document.getElementById('latex-editor').value,
    label: document.getElementById('latex-draft-label').textContent})),
    {text: editorContext.text, label: editorContext.label},
    '...and leaves the editor text and its label exactly as they were');
  assert.match(await page.locator('#error').innerText(),
    /^Error: Not opened: the previous paper has unsaved changes that could not be saved\.$/,
    'a refused flush while the writing view is hidden is reported on screen');
  // A stale switch is dropped: a second open while the flush is pending wins.
  await page.evaluate(() => { delete window.latexBeforeProjectSwitch; });

  // --- (15) A refresh that lands mid-drag does not abort the gesture: the rows
  // are rebuilt, the drag is re-pointed at its own row, and the drop still lands
  // where the pointer is — computed from the rows that are on screen.
  const putsBeforeRefresh = puts.length;
  // A move that really changes the order (p2 sits directly above p3 here, so it
  // has to cross the row below before the drop is a save at all).
  const aimBelowMethods = () => page.evaluate(() => {
    const li = [...document.querySelectorAll('#project-list li.project-row')]
      .find(el => el.querySelector('a').textContent === 'Methods study');
    const r = li.getBoundingClientRect();
    return r.y + r.height * 0.8;
  });
  const refreshHandle = await pressHandle('Housing study');
  await page.mouse.move(refreshHandle.x, await aimBelowMethods(), { steps: 6 });
  assert.equal(await page.locator('li.project-drop-marker').count(), 1, 'the line is up before the refresh');
  await page.evaluate(() => loadProjects());
  await page.waitForTimeout(60);
  assert.equal(await page.evaluate(() => !!projectDrag), true, 'the drag survived the refresh');
  assert.equal(await page.evaluate(() => projectDrag && projectDrag.id), 'p2', '...still moving the row it started on');
  assert.equal(await page.locator('#project-list li.project-row.dragging a').innerText(), 'Housing study',
    '...and that row is still the one shown as dragged');
  // The re-pointed drag still drops where the pointer is.
  await page.mouse.move(refreshHandle.x, await aimBelowMethods(), { steps: 6 });
  assert.equal(await page.locator('li.project-drop-marker').count(), 1, 'the line comes back under the pointer');
  await page.mouse.up();
  await settle();
  assert.equal(puts.length, putsBeforeRefresh + 1, 'the drop after a refresh is a normal save');
  assert.equal(await page.locator('#project-list li.project-row.dragging').count(), 0, 'no row is left dimmed');
  assert.equal(await page.evaluate(() => !!projectDrag), false, 'the drag is finished');

  // --- (15b) Context safety of keeping the drag alive: a refresh that lands
  // mid-drag may bring a membership change. The drop must then save the list that
  // is on screen, not the one the gesture started on — a stale list would be a
  // 409 at best and would silently drop the new project at worst.
  const idsBeforeAdded = await ids();
  const putsBeforeAdded = puts.length;
  const addedHandle = await pressHandle(await page.evaluate(() => projectRowName(projectListRows[0].id)));
  await page.mouse.move(addedHandle.x, await aimBelowMethods(), { steps: 6 });
  assert.equal(await page.evaluate(() => !!projectDrag), true, 'a drag is in progress before the surprise refresh');
  projects = [...projects, { id: 'p9', name: 'Added elsewhere', kind: 'nyc', pack_id: null }];
  await page.evaluate(() => loadProjects());
  await page.waitForFunction(count => document.querySelectorAll('#project-list li.project-row').length === count,
    idsBeforeAdded.length + 1);
  assert.equal(await page.evaluate(() => !!projectDrag), true, 'the drag is still the same gesture after the membership change');
  await page.mouse.move(addedHandle.x, await aimBelowMethods(), { steps: 6 });
  await page.mouse.up();
  await settle();
  assert.equal(puts.length, putsBeforeAdded + 1, 'the drop saved once');
  const sent = puts.at(-1).body.project_ids;
  assert.equal(sent.includes('p9'), true, 'the save names the project the mid-drag refresh brought in');
  assert.deepEqual([...sent].sort(), [...(await ids())].sort(), '...and it is exactly the complete list on screen, nothing stale');
  assert.notDeepEqual(sent, idsBeforeAdded, '...which is not the list the gesture started on');

  // --- (16) Alt+Arrow during a drag is ignored: one move at a time.
  const orderBeforeAlt = await ids();
  const putsAfterRefresh = puts.length;
  const altHandle = await pressHandle('Housing study');
  await page.mouse.move(altHandle.x, await aimBelow(), { steps: 6 });
  await handleOf('Housing study').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await page.waitForTimeout(50);
  assert.deepEqual(await ids(), orderBeforeAlt, 'the keyboard move did not touch the list mid-drag');
  assert.equal(puts.length, putsAfterRefresh, '...and saved nothing');
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await page.waitForFunction(() => !projectDrag);

  // --- (17) A busy workspace refuses a new reorder instead of racing it.
  await page.evaluate(() => { S.busy = true; });
  const busyPoint = await stablePoint('Permit study', 'handle');
  await page.mouse.move(busyPoint.x, busyPoint.y);
  await page.mouse.down();
  await page.mouse.move(busyPoint.x, busyPoint.y + 40, { steps: 4 });
  assert.equal(await page.evaluate(() => !!projectDrag), false, 'no drag starts while the workspace is busy');
  await page.mouse.up();
  await handleOf('Permit study').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await page.waitForTimeout(50);
  assert.deepEqual(await ids(), orderBeforeAlt, 'no keyboard move while busy either');
  await page.evaluate(() => { S.busy = false; });
  assert.ok(busyPoint.hit, 'the handle is still reachable while busy: the list stays usable');

  // --- (18) A double click on the handle moves nothing and opens nothing.
  const openedBeforeDouble = await open();
  await handleOf('Permit study').dblclick();
  await page.waitForTimeout(80);
  assert.deepEqual(await ids(), orderBeforeAlt, 'a double click does not reorder');
  assert.equal(await open(), openedBeforeDouble, 'a double click on the handle does not open the project');

  // --- (19) A GET that started before a save and lands *after* the save has
  // completed must not repaint the pre-save order. This is the deferred half of
  // (9): there `pending` was still set, here it is already cleared.
  await settle();
  const idsBeforeDeferred = await ids();
  holdProjectsGet();
  const deferredGet = page.evaluate(() => { window.__staleGet = loadProjects(); return true; });
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length > 0);
  while (!getHeld) await page.waitForTimeout(10);
  // The GET holds a snapshot of the pre-save order; the save completes and paints
  // the persisted order; only then is the stale answer released.
  await drag('Housing study', 'Methods study', 'above');
  await settle();
  const savedIds = await ids();
  assert.notDeepEqual(savedIds, idsBeforeDeferred, 'the save really changed the order');
  releaseGet();
  await deferredGet;
  await page.waitForTimeout(60);
  assert.deepEqual(await ids(), savedIds, 'a GET released after the save did not paint the pre-save order back');
  assert.equal(puts.length > 0, true);
  // A refresh that repaints the list must not pull the caret out of the editor:
  // only a control that was inside the list is put back.
  await page.evaluate(() => {
    document.getElementById('latex-view').classList.remove('hidden');
    document.getElementById('latex-editor').focus();
  });
  assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), 'latex-editor',
    'the editor really holds the focus before the refresh');
  await page.evaluate(() => loadProjects());
  assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), 'latex-editor',
    'a late refresh leaves the focus in the editor');

  // --- (20) A project registered while a save is held, whose own refresh lands
  // during the save, is listed and marked open after the save settles.
  await settle();
  holdSave();
  // The flush gate is section (14)'s subject; here the writing side is allowed to
  // settle so the scenario is the list, not the refusal.
  await page.evaluate(() => { window.latexBeforeProjectSwitch = async () => true; });
  // A move that certainly changes the order, whatever the list looks like now:
  // the first row goes below the last one.
  const listedNames = await names();
  const heldDrag = drag(listedNames[0], listedNames[2], 'below', true);
  await page.waitForFunction(() => document.querySelector('#project-list').getAttribute('aria-busy') === 'true');
  holdProjectsGet();
  const creating = page.evaluate(async () => {
    const created = await call('POST', '/projects', {kind: 'nyc'});
    await loadProjects();                      // dropped: a save owns the list
    await openProject(created.project_id, created.name);
    return created.project_id;
  });
  while (!getHeld) await page.waitForTimeout(10);   // its GET is now held too
  release();                                   // the save answers first
  await heldDrag;
  await settle();
  releaseGet();
  const newId = await creating;
  await page.waitForFunction(id => !!document.querySelector(`#project-list li.project-row[data-project-id="${id}"]`), newId);
  await page.waitForFunction(() => !projectListRefreshQueued);
  const listed = await page.evaluate(id => {
    const li = document.querySelector(`#project-list li.project-row[data-project-id="${id}"]`);
    return {text: li && li.querySelector('a').textContent,
      current: li && li.querySelector('a').getAttribute('aria-current'),
      open: S.project};
  }, newId);
  assert.equal(listed.text, 'Created during save', 'the project registered during the save is in the list');
  assert.equal(listed.open, newId, '...and it is the open project');
  assert.equal(listed.current, 'true', '...and it carries the open mark');
  // The placed rows keep the saved order and the new project is appended after
  // them, which is what the backend does with a row a save did not name.
  const afterCreate = await ids();
  assert.equal(afterCreate.at(-1), newId, 'the queued refresh appends the new project after the placed rows');
  assert.deepEqual([...afterCreate].sort(), [...savedIds, newId].sort(),
    '...and every row the save answered with is still there, with nothing lost or duplicated');

  // --- (21) Two switches with a deferred flush: the second wins, and the first
  // cannot override it when its flush finally reports success.
  await page.evaluate(() => {
    window.__releases = [];
    window.latexBeforeProjectSwitch = () => new Promise(resolve => { window.__releases.push(resolve); });
  });
  const firstIds = await page.evaluate(ids => ids, await ids());
  assert.ok(firstIds.length >= 2 && firstIds[0] !== firstIds[1], 'two distinct projects to switch between');
  await page.evaluate(id => { window.__first = openProject(id, 'first'); }, firstIds[0]);
  await page.waitForFunction(() => window.__releases.length === 1);
  await page.evaluate(id => { window.__second = openProject(id, 'second'); }, firstIds[1]);
  await page.waitForFunction(() => window.__releases.length === 2);
  await page.evaluate(() => { window.__releases[1](true); });   // the second switch flushes
  await page.waitForFunction(id => S.project === id, firstIds[1]);
  await page.evaluate(() => { window.__releases[0](true); });   // the first, now stale, flushes
  await page.evaluate(() => Promise.all([window.__first, window.__second]));
  assert.equal(await page.evaluate(() => S.project), firstIds[1], 'the first switch cannot override the second after the epoch recheck');
  // The stale switch must be dropped as a whole, not just in S.project: everything
  // it writes after the recheck (the title, the remembered project) would otherwise
  // describe a project that is not the open one.
  assert.equal(await page.evaluate(() => document.getElementById('project-title').textContent), 'second',
    'the stale switch did not write its name over the winning project title');
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('wb.project') || 'null')),
    {id: firstIds[1], name: 'second'}, 'the stale switch did not save itself as the open project');
  await page.evaluate(() => { delete window.latexBeforeProjectSwitch; });

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('PROJECT_ORDER_PASS: drag both directions with a visible insertion line and Escape cancel (which does not close the workspace, the menu hidden and project/chip kept), persisted server order on GET/reload, selection kept, failure/stale rollback, project added mid-save kept, GET mid-save ignored, GET released after the save ignored, Alt+Arrow keyboard move with focus and live status, no accidental open or attach, no localStorage order, long list still scrolls, writing-flush-gated project switch with a visible error when the view is hidden, a refresh landing mid-drag keeps the drag and its drop and saves the fresh membership, Alt during drag and busy/double-click no-ops, refresh queued across a save lists and marks a project registered meanwhile, second concurrent switch wins');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
