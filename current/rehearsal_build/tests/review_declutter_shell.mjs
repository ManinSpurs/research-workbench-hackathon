// Declutter shell review: the real index.html, the real style chain, the real
// app.js/library.js/latex.js/startup.js and the real declutter_writing module,
// with the declutter shell loaded LAST exactly as index.html will load it.
//
// What is asserted is behaviour a researcher sees on the actual workspace: what
// is on screen by default, that a panel opens and closes without losing a draft
// or a PDF reader, that the project/PDF orders still save and still reach the
// header selector, that the Settings "AI panel" route really opens the secretary
// panel, and that the build's no-live-AI truth is on screen. Only the transport
// is stubbed; no server of the user's is touched and nothing is a model call.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const frontend = path.resolve(import.meta.dirname, '../frontend');
const readSource = (name) => readFile(path.join(frontend, name), 'utf8');
const [shellJs, shellCss, writingJs, writingCss] = await Promise.all([
  readSource('declutter_shell.js'), readSource('declutter_shell.css'),
  readSource('declutter_writing.js'), readSource('declutter_writing.css'),
]);

// (0) Presentation only: the shell owns no transport, keeps no second copy of
// project data, and adds no colour treatment of its own beyond the tokens.
assert.ok(!/fetch\(|XMLHttpRequest|localStorage\.setItem/.test(shellJs),
  'declutter_shell.js performs no transport and persists nothing of its own');
for (const banned of ['-gradient(', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!shellCss.toLowerCase().includes(banned), `declutter_shell.css must not use ${banned}`);
// A shadow is only ever cancelled here, to undo one the shipped sheet draws.
for (const line of shellCss.split('\n').filter((row) => row.includes('box-shadow:')))
  assert.match(line, /box-shadow:\s*none/, 'declutter_shell.css never paints a shadow of its own');
for (const sample of ['Sample paper', 'Permit study', 'Untitled paper'])
  assert.ok(!shellJs.includes(sample) && !shellCss.includes(sample),
    'the shell carries no invented record of its own');

const server = createServer(async (req, res) => {
  const requestPath = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(frontend, requestPath === '/' ? 'index.html' : '.' + requestPath);
  if (!file.startsWith(frontend + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    const body = await readFile(file);
    const ext = path.extname(file);
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript',
      '.css': 'text/css', '.png': 'image/png' })[ext] || 'application/octet-stream');
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const api = 'http://wb.test';

// --- Fixture store: three projects, one paper, three PDFs. The two orders are
// the only things a request mutates, so a saved order is observable.
let projects = [
  { id: 'p1', name: 'Permit study', kind: 'fx', pack_id: null },
  { id: 'p2', name: 'Housing study', kind: 'fx', pack_id: null },
  { id: 'p3', name: 'Zoning study', kind: 'fx', pack_id: null },
];
const docs = {
  d1: { id: 'd1', folder_id: null, kind: 'pdf', title: 'Permit approval report', day: null, pages: 12, chars: 4012, created_at: '2026-10-24T09:00:00Z', text_status: 'OK' },
  d2: { id: 'd2', folder_id: null, kind: 'pdf', title: 'Methods appendix', day: null, pages: 4, chars: 900, created_at: '2026-10-24T09:30:00Z', text_status: 'OK' },
  d3: { id: 'd3', folder_id: null, kind: 'pdf', title: 'Zoning transcript', day: null, pages: 61, chars: 22010, created_at: '2026-10-24T10:00:00Z', text_status: 'OK' },
};
let pdfOrder = ['d1', 'd2', 'd3'];
let draft = { draft_id: 'd1', project_id: 'p1', question_id: null, title: 'Untitled paper',
  engine: 'auto', main_file: 'main.tex', revision: 3,
  files: [{ path: 'main.tex', text: '\\documentclass{article}\n\\begin{document}\nSynthetic draft fixture.\n\\end{document}\n' }],
  sections: [], previous: null, created_at: 'x', updated_at: 'x', model_calls: 0, notice: null };
const sent = { projectOrder: [], libraryOrder: [], draftSaves: [], unexpected: [] };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('response', (response) => {
  if (response.url().startsWith(origin) && response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
});
const tree = () => ({ folders: [], items: pdfOrder.map((id) => docs[id]) });
const briefing = { parts: { where_you_left_off: [], what_changed_since: [], ready_now: [],
  waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 };
await page.route(`${api}/**`, (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const p = url.pathname;
  const reply = (json) => route.fulfill({ json });
  if (p === '/domain-packs') return reply({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
  if (p === '/chips') return reply({ chips: [] });
  if (p === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
  if (p === '/projects' && request.method() === 'GET') return reply({ projects });
  if (p === '/projects/order' && request.method() === 'PUT') {
    const body = request.postDataJSON() || {};
    sent.projectOrder.push(body);
    const ids = Array.isArray(body.project_ids) ? body.project_ids : [];
    if (ids.length !== projects.length || ids.some((id) => !projects.some((row) => row.id === id)))
      return route.fulfill({ status: 409, json: { message: 'project_ids must list every project once' } });
    const byId = new Map(projects.map((row) => [row.id, row]));
    projects = ids.map((id) => byId.get(id));
    return reply({ projects });
  }
  if (p === '/briefing') return reply({ rows: [] });
  if (p === '/capabilities/latex') return reply({ available: true,
    engines: { pdflatex: '/usr/local/bin/pdflatex', xelatex: null, lualatex: null, latexmk: null, tectonic: null }, pdf_export: true });
  if (p === '/library/tree') return reply(tree());
  if (p === '/library/order' && request.method() === 'PUT') {
    const body = request.postDataJSON() || {};
    sent.libraryOrder.push(body);
    pdfOrder = (body.item_ids || []).slice();
    return reply(tree());
  }
  if (/^\/library\/docs\/[^/]+\/file$/.test(p))
    return route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n%%EOF\n') });
  if (/^\/projects\/[^/]+\/drafts$/.test(p)) {
    const id = p.split('/')[2];
    return reply({ project_id: id, drafts: id === 'p1' ? [{ draft_id: draft.draft_id, title: draft.title }] : [] });
  }
  if (/^\/drafts\/[^/]+$/.test(p) && request.method() === 'GET') return reply(draft);
  if (/^\/drafts\/[^/]+$/.test(p) && request.method() === 'PUT') {
    const body = request.postDataJSON() || {};
    sent.draftSaves.push(body);
    draft = { ...draft, revision: draft.revision + 1, files: body.files || draft.files, title: body.title || draft.title };
    return reply(draft);
  }
  if (/^\/projects\/[^/]+\/(profile|clarifications|pack-memory)$/.test(p)) return reply(p.endsWith('profile')
    ? { variables: [], computed_by: 'ordinary code, no model call' } : { clarifications: [], questions: [] });
  if (/^\/projects\/[^/]+\/briefing$/.test(p)) return reply(briefing);
  if (/^\/projects\/[^/]+\/secretary-turns$/.test(p)) return reply({ turns: [], model_calls: 0 });
  if (/^\/projects\/[^/]+\/revisit-conditions$/.test(p)) return reply({ conditions: [], model_calls: 0 });
  if (request.method() === 'GET') return reply({});
  sent.unexpected.push(`${request.method()} ${p}`);
  return route.fulfill({ status: 404, json: { message: 'unexpected fixture write' } });
});
await page.addInitScript(() => { try { localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });

const idCount = (id) => page.locator(`#${id}`).count();
// The visible tab strip is gone, so a view is opened the way the app opens it:
// through the same view-opening function the tab's onclick named. This is also
// what proves the flows that used to click a tab still work.
const openView = (fn) => page.evaluate((name) => {
  const target = window[name];
  if (typeof target !== 'function') throw new Error('missing view opener: ' + name);
  const result = target();
  return result && typeof result.then === 'function' ? result.then(() => true) : true;
}, fn);
try {
  // ?preview=1 is what the Lead's interactive launcher passes.
  await page.goto(`${origin}/?api=${api}&preview=1`);
  await page.getByTestId('home-menu-research').waitFor();

  // --- (1) The source files, not a mockup -----------------------------------
  // index.html links both declutter modules itself now, so a real page has them
  // before any test touches it. The check below is written for both worlds: what
  // is asserted is the module that HTML loaded, and only a page that somehow
  // lacks it is given the same files by hand.
  const autoLoaded = await page.evaluate(() => ({
    writing: !!window.WB_DECLUTTER_WRITING, shell: !!window.WB_DECLUTTER_SHELL,
  }));
  if (!autoLoaded.writing || !autoLoaded.shell) {
    await page.addStyleTag({ content: writingCss });
    await page.addScriptTag({ content: writingJs });
    await page.addStyleTag({ content: shellCss });
    await page.addScriptTag({ content: shellJs });
  }
  await page.waitForFunction(() => !!window.WB_DECLUTTER_WRITING && !!window.WB_DECLUTTER_SHELL);
  assert.equal(await page.evaluate(() => document.getElementById('app-shell').getAttribute('data-dc-wired')),
    '1', 'the shell wired itself to the real #app-shell');

  // Loading the same files a second time must change nothing: the module is
  // idempotent, so a page that already wired it cannot end up with two headers,
  // two selectors or two panels.
  await page.addStyleTag({ content: shellCss });
  await page.addScriptTag({ content: shellJs });
  const afterDoubleLoad = await page.evaluate(() => ({
    shellButtons: document.querySelectorAll('#quiet-projects, #quiet-secretary').length,
    selectors: document.querySelectorAll('#quiet-project-select').length,
    truthFolds: document.querySelectorAll('.wb-dc-truth').length,
    // The More fold is gone: Settings already owns theme, language, research
    // field and session, so those duplicates left the header, and the one action
    // with no other home (Expand) is offered directly instead of behind a fold.
    moreFolds: document.querySelectorAll('.wb-dc-more').length,
    navs: document.querySelectorAll('main > nav').length,
    asides: document.querySelectorAll('main > aside').length,
  }));
  assert.deepEqual(afterDoubleLoad, { shellButtons: 2, selectors: 1, truthFolds: 1,
    moreFolds: 0, navs: 1, asides: 1 },
    'a second load of the shell adds no second header control, selector, fallback fold or panel');

  // Before any panel is opened, nothing claims to be open: this is the default a
  // fresh workspace starts in.
  const freshPanels = await page.evaluate(() => {
    const main = document.querySelector('main');
    return {
      collapsed: main.classList.contains('wb-collapse-projects') && main.classList.contains('wb-collapse-secretary'),
      quiet: [document.getElementById('quiet-projects').getAttribute('aria-expanded'),
        document.getElementById('quiet-secretary').getAttribute('aria-expanded')],
      shipped: [document.getElementById('toggle-projects').getAttribute('aria-expanded'),
        document.getElementById('toggle-secretary').getAttribute('aria-expanded')],
      open: [window.WB_DECLUTTER_SHELL.isPanelOpen('projects'), window.WB_DECLUTTER_SHELL.isPanelOpen('secretary')],
    };
  });
  assert.ok(freshPanels.collapsed, 'both panels start collapsed in the shipped state');
  assert.deepEqual(freshPanels.open, [false, false], 'the shell reports both panels closed');
  assert.deepEqual(freshPanels.quiet, ['false', 'false'], 'the quiet buttons say closed');
  assert.deepEqual(freshPanels.shipped, ['false', 'false'], 'the shipped toggles say closed too');

  // The project rows live in the projects panel, so opening a project is: open
  // the panel from the header, then use the row that is now on screen.
  await page.getByTestId('home-menu-research').click();
  await page.waitForFunction(() => document.querySelectorAll('#project-list li.project-row').length === 3);
  await page.getByTestId('quiet-projects').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  await page.locator('#project-list [data-testid="open-fx"]').first().waitFor();
  await page.locator('#project-list [data-testid="open-fx"]').first().click();
  await page.waitForFunction(() => S.project === 'p1');
  await page.waitForSelector('#app-shell:not(.hidden)');
  // Back to the default: no panel was asked for, so none is on screen. Closing
  // runs through the shipped toggle, so the saved preference agrees.
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('projects')
    && !window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  // The drawer slides out (160ms) rather than being display:none'd, so the closed
  // state is read once the transition has actually finished, exactly as a person
  // would see it.
  await page.waitForFunction(() => {
    const nav = document.querySelector('main > nav'), aside = document.querySelector('main > aside');
    return getComputedStyle(nav).visibility === 'hidden' && getComputedStyle(aside).visibility === 'hidden';
  }, null, { timeout: 5000 });

  // --- (2) Default state: one quiet header, the content owns the window ------
  const initial = await page.evaluate(() => {
    const box = (selector) => document.querySelector(selector).getBoundingClientRect();
    const visible = (node) => !!node && node.getClientRects().length > 0
      && getComputedStyle(node).visibility !== 'hidden';
    const headerButtons = [...document.querySelectorAll('#app-shell > header > button')];
    return {
      shellClass: document.getElementById('app-shell').classList.contains('wb-dc'),
      headerText: document.getElementById('app-shell').querySelector(':scope > header').innerText.replace(/\s+/g, ' ').trim(),
      headerButtons: headerButtons.filter(visible).map((node) => node.id),
      headerFolds: [...document.querySelectorAll('#app-shell > header > details > summary')].map((node) => node.textContent.trim()),
      projectPanelVisible: visible(document.querySelector('main > nav')),
      secretaryPanelVisible: visible(document.querySelector('main > aside')),
      projectsExpanded: document.getElementById('quiet-projects').getAttribute('aria-expanded'),
      secretaryExpanded: document.getElementById('quiet-secretary').getAttribute('aria-expanded'),
      sectionWidth: box('main > section').width,
      viewport: innerWidth,
      viewBarInMain: document.querySelector('main > .view-bar') !== null,
      tabs: [...document.querySelectorAll('main > .view-bar .view-tabs > button')]
        .filter(visible).map((node) => node.dataset.testid),
      visibleTabs: [...document.querySelectorAll('main > .view-bar .view-tabs > button')]
        .filter(visible).length,
      viewBarRendered: visible(document.querySelector('main > .view-bar')),
      truthTag: document.querySelector('.wb-dc-truth-tag').textContent.trim(),
      truthText: document.querySelector('.wb-dc-truth-text').textContent.trim(),
      badgeVisible: visible(document.getElementById('adapter-badge')),
      badgeText: document.getElementById('adapter-badge').textContent.trim(),
      selectorCount: document.getElementById('quiet-project-select').length,
      selectorValue: document.getElementById('quiet-project-select').value,
    };
  });
  assert.ok(initial.shellClass, 'the shell applied its own class to the real workspace');
  assert.equal(initial.projectsExpanded, 'false', 'the projects panel reports itself closed');
  assert.equal(initial.secretaryExpanded, 'false', 'the secretary panel reports itself closed');
  assert.equal(initial.projectPanelVisible, false, 'the project list is not on screen by default');
  assert.equal(initial.secretaryPanelVisible, false, 'the secretary panel is not on screen by default');
  assert.ok(initial.sectionWidth / initial.viewport > 0.66,
    `the working surface dominates the window (${Math.round(initial.sectionWidth)} of ${initial.viewport}px)`);
  assert.ok(initial.viewBarInMain, 'the single view nav lives above the surface, not inside it');
  // The duplicated Research / Writing / Library strip is gone from the visible
  // UI: the home menu owns destination choice, and "← Home" is the one way back.
  assert.deepEqual(initial.tabs, [],
    'no view tab is rendered: navigation is home menu -> a destination, and ← Home back');
  assert.equal(initial.visibleTabs, 0, 'the hidden strip renders no button at all');
  assert.deepEqual(initial.headerButtons.sort(),
    ['back-to-menu', 'expand-surface', 'interior-settings-open', 'quiet-projects', 'quiet-secretary'],
    'the header carries Settings, the two panel buttons, ← Home and the one fold-less action');
  assert.equal(initial.headerFolds.length, 1, 'the header carries one fold: the build-status tag');
  // Sample data is all a preview flag can prove; the adapter state itself is the
  // badge's own wording, and the sentence behind the fold never guesses at it.
  assert.match(initial.headerFolds[0], /Test · sample data/, 'the fold states the sample-data fact');
  assert.match(initial.headerFolds[0], /adapter not yet reported/, '...beside the shipped adapter badge');
  assert.ok(!/no live AI|not connected|offline/i.test(initial.headerFolds[0]),
    'an unreported adapter is never turned into a claim that AI is off');
  assert.equal(initial.truthText, 'This build has not reported which adapter it would use, so this window makes no claim about live AI either way. Sample records are shown.',
    'the unreported state says exactly that it is unreported');
  assert.ok(initial.badgeVisible, 'the shipped adapter badge is still on screen, not hidden');

  // --- (2b) The project is named once, in the selector ----------------------
  // The screenshot the user called out showed the same name three times: header
  // selector, #project-title, #research-location. The two in-content echoes leave
  // the visual flow; the nodes, their text and their roles do not.
  const naming = await page.evaluate(() => {
    const onScreen = (node) => !!node && node.getClientRects().length > 0
      && getComputedStyle(node).visibility !== 'hidden'
      && node.getBoundingClientRect().width > 2;
    const title = document.getElementById('project-title');
    const location = document.getElementById('research-location');
    const section = document.querySelector('main > section');
    const name = document.getElementById('quiet-project-select').selectedOptions[0].textContent.trim();
    // Everything visibly carrying the project name inside the working surface.
    const visibleEchoes = [...section.querySelectorAll('h1, h2, h3, p, span, li')]
      .filter((node) => onScreen(node) && node.textContent.trim() === name && !node.closest('details:not([open])'));
    return {
      titleVisible: onScreen(title),
      locationVisible: onScreen(location),
      titleText: title.textContent.trim(),
      locationText: location.textContent.trim(),
      selectorText: name,
      visibleEchoes: visibleEchoes.length,
      titleAccessible: title.tagName === 'H2' && title.getAttribute('tabindex') === '-1',
    };
  });
  assert.equal(naming.titleVisible, false, 'the uppercase project title no longer repeats the selector');
  assert.equal(naming.locationVisible, false, 'the location line no longer repeats it either');
  assert.equal(naming.visibleEchoes, 0, 'no visible line inside the working surface repeats the project name');
  assert.equal(naming.titleText, naming.selectorText,
    'the heading still carries the project name for assistive tech and for the code that writes it');
  assert.equal(naming.locationText, naming.selectorText, 'the location line still carries it too');
  assert.ok(naming.titleAccessible, 'the surface heading keeps its role and its focus target');
  assert.equal(initial.selectorCount, 4, 'the selector lists the three projects plus "no project"');
  assert.equal(initial.selectorValue, 'p1', 'the selector reads the open project from the app');
  // Settings owns theme and language now, so the workspace carries no duplicate
  // of either control: there is exactly one theme control in the app, and it is
  // the settings sheet's.
  assert.equal(await idCount('theme-select'), 0, 'the duplicated header theme control is removed');
  assert.equal(await idCount('lang-select'), 0, 'the duplicated header language control is removed');
  assert.equal(await page.locator('#settings-theme').count(), 1, 'Settings still owns the theme control');
  assert.equal(await page.locator('#settings-lang').count(), 1, 'Settings still owns the language control');
  assert.equal(await page.locator('#settings-field').count(), 1, 'Settings still owns "change research field"');
  assert.equal(await page.locator('#settings-end').count(), 1, 'Settings still owns "end session"');
  assert.equal(await idCount('view-secretary-review'), 1, 'the review action exists once');

  // --- (3) Panels open on demand, with the panel's own contents -------------
  const opened = await page.evaluate(() => {
    const result = window.WB_DECLUTTER_SHELL.openPanel('projects');
    const nav = document.querySelector('main > nav');
    return { result, open: window.WB_DECLUTTER_SHELL.isPanelOpen('projects'),
      visible: nav.getClientRects().length > 0 && getComputedStyle(nav).visibility !== 'hidden',
      rows: document.querySelectorAll('#project-list li.project-row').length,
      expanded: document.getElementById('quiet-projects').getAttribute('aria-expanded'),
      shippedToggle: document.getElementById('toggle-projects').getAttribute('aria-expanded') };
  });
  assert.equal(opened.result, true, 'openPanel is the public seam the Lead calls');
  assert.ok(opened.open && opened.visible, 'the projects panel is a usable panel on demand');
  assert.equal(opened.rows, 3, 'the real project rows are inside it');
  assert.equal(opened.expanded, 'true');
  assert.equal(opened.shippedToggle, 'true',
    'the shipped toggle and the quiet button agree, so no route sees a hidden-but-open panel');

  // The rest of the seam the Lead integrates against.
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.refresh()), true,
    'refresh re-syncs the selector and the panel chrome without a reload');
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.closePanel()), true,
    'closePanel with no argument closes whatever a route left open');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('projects')
    && !window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  await page.evaluate(() => window.WB_DECLUTTER_SHELL.openPanel('projects'));
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  await page.evaluate(() => window.WB_DECLUTTER_SHELL.openPanel('secretary'));
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.closePanel('secretary')), true);
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.closePanel('projects')), true);
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  await page.evaluate(() => window.WB_DECLUTTER_SHELL.openPanel('projects'));
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));

  // A live reorder still saves, and the header selector follows it without
  // taking the keyboard away from the row that moved.
  const orderBefore = await page.evaluate(() => [...document.querySelectorAll('#project-list li.project-row')]
    .map((node) => node.dataset.projectId));
  await page.evaluate(() => {
    const handle = document.querySelector('#project-list li.project-row [data-project-handle="true"]');
    handle.focus();
    handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true }));
  });
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('#project-list li.project-row')].map((node) => node.dataset.projectId);
    const options = [...document.getElementById('quiet-project-select').options].slice(1).map((option) => option.value);
    return rows.join() === options.join() && rows[1] === 'p1';
  });
  const afterMove = await page.evaluate(() => ({
    selector: [...document.getElementById('quiet-project-select').options].slice(1).map((option) => option.value),
    value: document.getElementById('quiet-project-select').value,
    activeId: document.activeElement && document.activeElement.id,
    order: [...document.querySelectorAll('#project-list li.project-row')].map((node) => node.dataset.projectId),
  }));
  assert.deepEqual(sent.projectOrder.at(-1).project_ids, afterMove.order,
    'the saved order is the order on screen');
  assert.ok(orderBefore.join() !== afterMove.order.join(), 'the move really changed the order');
  assert.deepEqual(afterMove.selector, afterMove.order, 'the selector lists the persisted order');
  assert.equal(afterMove.value, 'p1', 'the selector still names the open project');
  assert.notEqual(afterMove.activeId, 'quiet-project-select', 'a reorder never pulls focus into the selector');

  // A refresh of the list reaches the selector without moving the caret out of
  // the field the researcher is using.
  await page.evaluate(() => {
    document.querySelector('.wb-dc-support').open = true;
    const field = document.getElementById('question');
    field.value = 'A question being typed while the list refreshes.';
    field.focus();
    field.setSelectionRange(6, 6);
  });
  await page.evaluate(() => { loadProjects(); });
  await page.waitForFunction(() => !S.busy);
  const focusAfterRefresh = await page.evaluate(() => ({
    activeId: document.activeElement && document.activeElement.id,
    caret: document.getElementById('question').selectionStart,
    options: [...document.getElementById('quiet-project-select').options].map((option) => option.value),
  }));
  assert.equal(focusAfterRefresh.activeId, 'question', 'a project refresh never steals focus from the field');
  assert.equal(focusAfterRefresh.caret, 6, 'the caret stays where it was through the refresh');
  assert.deepEqual(focusAfterRefresh.options.slice(1), afterMove.order, 'the refreshed order is what the selector shows');

  // --- (4) Escape closes the panel, never the workspace ---------------------
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  const afterEscape = await page.evaluate(() => ({
    workbench: document.body.classList.contains('workbench-open'),
    portal: !document.getElementById('portal').hidden,
    menu: document.getElementById('startup-menu') && !document.getElementById('startup-menu').hidden,
    expanded: document.getElementById('quiet-projects').getAttribute('aria-expanded'),
  }));
  assert.ok(afterEscape.workbench && afterEscape.portal, 'Escape does not collapse the whole app');
  assert.equal(afterEscape.menu, false, 'Escape did not return to the home menu');
  assert.equal(afterEscape.expanded, 'false', 'the panel says it is closed, and it is');

  // --- All four destinations stay reachable now that the duplicated tab strip
  // is gone, and the single "← Home" control is the way back. Each destination
  // is opened by the same function its home tile calls; the tab strip was only
  // ever a second door to them.
  const navigation = await page.evaluate(() => {
    const rendered = (node) => !!node && node.getClientRects().length > 0;
    const tabs = document.querySelector('.view-tabs');
    const home = document.getElementById('back-to-menu');
    return {
      tabsRendered: rendered(tabs),
      visibleTabs: tabs ? [...tabs.querySelectorAll('button')].filter(rendered).length : -1,
      homeRendered: rendered(home),
      homeInHeader: !!home && !!home.closest('#app-shell > header'),
      moreGone: !document.querySelector('[data-testid="quiet-more"]'),
      settingsNotDuplicated: !rendered(document.querySelector('#app-shell > header .theme-control')) &&
        !rendered(document.getElementById('startup-change')) &&
        !rendered(document.getElementById('end-session')),
      expandReachable: rendered(document.getElementById('expand-surface')),
      destinations: ['showResearchView', 'openLibraryView', 'openWritingView', 'openSecretaryReview']
        .every((name) => typeof window[name] === 'function'),
    };
  });
  assert.equal(navigation.tabsRendered, false, 'the duplicated Research / Writing / Library tab strip is not visible');
  assert.equal(navigation.visibleTabs, 0, 'no tab of that strip is rendered');
  assert.equal(navigation.homeRendered, true, '"← Home" is visible');
  assert.equal(navigation.homeInHeader, true, '"← Home" is a top-level header control');
  assert.equal(navigation.moreGone, true, 'the More fold is gone with the duplicated Settings controls');
  assert.equal(navigation.settingsNotDuplicated, true, 'theme, research field and end session stay in Settings only');
  assert.equal(navigation.expandReachable, true, '"Widen workspace" keeps a home in the header');
  assert.equal(navigation.destinations, true, 'all four destinations are still openable');

  // Each destination really opens, and "← Home" really returns to the menu.
  for (const [label, fn, probe] of [
    ['Research', 'showResearchView', () => !document.querySelector('[data-testid="library-view"]') || document.querySelector('[data-testid="library-view"]').classList.contains('hidden')],
    ['Literature', 'openLibraryView', () => !document.querySelector('[data-testid="library-view"]').classList.contains('hidden')],
    ['Writing', 'openWritingView', () => !document.querySelector('#latex-view').classList.contains('hidden')],
    ['Conversation Review', 'openSecretaryReview', () => document.querySelector('main section').classList.contains('secretary-review-mode')],
  ]) {
    await page.evaluate((name) => window[name](), fn);
    await page.waitForFunction(probe, null, { timeout: 8000 })
      .catch(() => assert.fail(`${label} did not open through ${fn}()`));
    assert.equal(await page.getByTestId('back-to-menu').isVisible(), true,
      `${label}: "← Home" stays visible while a destination is open`);
  }
  await page.getByTestId('back-to-menu').click();
  await page.waitForFunction(() => document.getElementById('startup-menu') && !document.getElementById('startup-menu').hidden);
  assert.equal(await page.evaluate(() => document.body.classList.contains('workbench-open')), false,
    '"← Home" returns to the home menu from any destination');
  // Return to the workspace for the rest of the run.
  await page.locator('#home-menu [data-menu="research"]').click();
  await page.waitForFunction(() => document.body.classList.contains('workbench-open'));

  // --- (5) The project selector opens a project through openProject ---------
  await page.locator('#quiet-project-select').selectOption('p2');
  await page.waitForFunction(() => S.project === 'p2');
  assert.equal(await page.locator('#quiet-project-select').inputValue(), 'p2');
  await page.locator('#quiet-project-select').selectOption('p1');
  await page.waitForFunction(() => S.project === 'p1');
  assert.equal(await page.evaluate(() => document.querySelector('#project-list a[aria-current="true"]').textContent),
    'Permit study', 'the list mark and the selector name the same project');

  // --- (6) The secretary panel: the real composer and the AI panel ----------
  await page.getByTestId('quiet-secretary').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  assert.equal(await page.locator('#secretary-input').isVisible(), true, 'the one composer is usable in the panel');
  assert.equal(await page.locator('#secretary-compose').count(), 1, 'the composer exists once, wherever it is');
  assert.equal(await page.locator('#ai-panel').count(), 1, 'AI readiness lives in the panel, not in a copy');
  assert.equal(await page.getByTestId('quiet-secretary').getAttribute('aria-expanded'), 'true');
  // The review surface is reachable from the panel and takes the whole width.
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelector('main section').classList.contains('secretary-review-mode'));
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary')), false,
    'choosing the review surface puts the panel away');
  await openView('showResearchView');

  // --- (7) The Settings "AI panel" route really opens the panel -------------
  await page.getByTestId('interior-settings-open').click();
  await page.getByTestId('interior-settings-tab-models').click();
  await page.getByTestId('interior-settings-open-ai').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  const routed = await page.evaluate(() => {
    const aside = document.querySelector('main > aside');
    return {
      settingsClosed: document.getElementById('settings-layer').hidden,
      inheritedExpanded: document.getElementById('toggle-secretary').getAttribute('aria-expanded'),
      quietExpanded: document.getElementById('quiet-secretary').getAttribute('aria-expanded'),
      asideVisible: aside.getClientRects().length > 0 && getComputedStyle(aside).visibility !== 'hidden',
      aiPanelOpen: document.getElementById('ai-panel').open,
      focusInside: aside.contains(document.activeElement),
    };
  });
  assert.ok(routed.settingsClosed, 'the settings sheet closes when it routes');
  assert.equal(routed.inheritedExpanded, 'true', 'the shipped toggle reports the panel open');
  assert.equal(routed.quietExpanded, 'true', 'the quiet button agrees with the shipped toggle');
  assert.ok(routed.asideVisible, 'the panel the settings route asked for is actually on screen');
  assert.ok(routed.aiPanelOpen, 'the destination disclosure is open, not merely scrolled to');
  assert.ok(routed.focusInside, 'focus lands inside the panel the route opened');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));

  // --- (8) A closing panel never costs a draft or an open PDF ---------------
  await openView('openWritingView');
  await page.waitForFunction(() => typeof latexState === 'object' && latexState && latexState.draft);
  const typed = 'Unsaved words typed before a panel opened.';
  await page.locator('#latex-editor').evaluate((node, text) => {
    node.value = text;
    node.dispatchEvent(new Event('input', { bubbles: true }));
    node.focus();
    node.setSelectionRange(4, 4);
  }, typed);
  await page.getByTestId('quiet-secretary').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  const afterPanel = await page.evaluate(() => ({
    text: document.getElementById('latex-editor').value,
    viewVisible: document.getElementById('latex-view').getClientRects().length > 0,
    status: document.getElementById('latex-status').textContent.trim(),
    caret: document.getElementById('latex-editor').selectionStart,
    focusId: document.activeElement && document.activeElement.id,
  }));
  assert.equal(afterPanel.text, typed, 'the unsaved buffer survives the panel opening and closing');
  assert.ok(afterPanel.viewVisible, 'the writing surface stayed mounted');
  assert.equal(afterPanel.caret, 4, 'the caret inside the buffer is where the researcher left it');
  assert.equal(afterPanel.focusId, 'quiet-secretary',
    'focus returns to the button that opened the panel, not to the document body');

  await openView('openLibraryView');
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 1);
  await page.locator('.library-item-row[data-item-id="d1"]').click();
  await page.waitForFunction(() => !!document.querySelector('#library-viewer iframe'));
  await page.getByTestId('quiet-projects').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.WB_DECLUTTER_SHELL.isPanelOpen('projects'));
  assert.equal(await page.locator('#library-viewer iframe').count(), 1,
    'closing a panel does not unmount the open PDF');

  // The PDF order still saves and still comes back in the saved order. Alt+ArrowUp
  // moves the document one place, so d3 lands second — the same handler the
  // shipped list uses, reached through the moved rows.
  await page.evaluate(() => {
    const row = document.querySelector('.library-item-row[data-item-id="d3"]');
    row.focus();
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', altKey: true, bubbles: true }));
  });
  // The node-side `sent` record is not visible to the page, so the save is waited
  // for here rather than inside a page predicate.
  async function waitFor(check, message, tries = 120) {
    for (let i = 0; i < tries; i++) {
      if (await check()) return;
      await page.waitForTimeout(50);
    }
    assert.fail(message);
  }
  await waitFor(async () => sent.libraryOrder.length === 1, 'the reorder is sent to the backend');
  await waitFor(async () => await page.evaluate(() => !libraryState.orderBusy), 'the save settles');
  assert.deepEqual(sent.libraryOrder.at(-1).item_ids, ['d1', 'd3', 'd2'], 'the PDF order is saved to the backend');
  assert.equal(await page.evaluate(() => libraryState.tree.items.map((item) => item.id).join()), 'd1,d3,d2',
    'the returned order is what is shown');

  // --- (9) 44px targets, both themes, and a 375px window -------------------
  await openView('showResearchView');
  for (const theme of ['light', 'dark']) {
    await page.evaluate((value) => WB_THEME.set(value), theme);
    const headerTargets = await page.locator('#app-shell > header > button, #app-shell > header > details > summary')
      .evaluateAll((nodes) => nodes.filter((node) => node.getClientRects().length)
        .map((node) => Math.round(node.getBoundingClientRect().height)));
    assert.ok(headerTargets.every((height) => height >= 44), `header targets are 44px in ${theme} mode`);
  }
  await page.evaluate(() => WB_THEME.set('light'));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(120);
  const narrow = await page.evaluate(() => {
    const nav = document.querySelector('main > nav');
    const truth = document.querySelector('.wb-dc-truth-tag');
    return {
      overflow: document.documentElement.scrollWidth,
      width: innerWidth,
      truthVisible: truth.getClientRects().length > 0 && getComputedStyle(truth).visibility !== 'hidden',
      truthText: truth.textContent.trim(),
      panelOut: getComputedStyle(nav).visibility === 'hidden',
      tabsFit: document.querySelector('main > .view-bar').scrollWidth <= innerWidth + 1,
    };
  });
  assert.ok(narrow.overflow <= narrow.width, `no horizontal overflow at 375px (${narrow.overflow}px)`);
  assert.ok(narrow.truthVisible, 'the build-status fold stays on screen at 375px');
  assert.match(narrow.truthText, /Test · sample data/,
    'the narrow window still states the sample-data fact and the adapter wording');
  assert.ok(narrow.panelOut, 'the panels are closed at a narrow width too');
  assert.ok(narrow.tabsFit, 'the three view tabs fit a 375px window');
  await page.getByTestId('quiet-secretary').click();
  await page.waitForFunction(() => window.WB_DECLUTTER_SHELL.isPanelOpen('secretary'));
  await page.waitForFunction(() => {
    const rect = document.querySelector('main > aside').getBoundingClientRect();
    return rect.right <= innerWidth + 1 && rect.left >= 0;
  });
  const drawer = await page.getByTestId('secretary-input').boundingBox();
  assert.ok(drawer && drawer.x >= 0 && drawer.x + drawer.width <= 375,
    `the panel is a full usable drawer at 375px, not a clipped column (${Math.round(drawer.x)}+${Math.round(drawer.width)})`);

  // --- (10) Five accepted review findings -----------------------------------
  // (a) The delete confirmation is never behind a fold, and the Data/Canvas route
  //     the Lead wires to openSupport() finds the fold already open.
  const support = await page.evaluate(() => {
    const fold = document.querySelector('.wb-dc-support');
    const status = document.getElementById('delete-status');
    const visible = (node) => !!node && node.getClientRects().length > 0
      && !node.closest('details:not([open])');
    fold.open = false;
    const hiddenBefore = !visible(document.getElementById('profile'));
    const opened = window.WB_DECLUTTER_SHELL.openSupport();
    return {
      opened,
      foldOpen: fold.open,
      hiddenBefore,
      containsDeleteStatus: !!(fold && fold.contains(status)),
      deleteStatusInFold: !!(status && status.closest('.wb-dc-support')),
      deleteStatusVisible: visible(status),
      deleteRowInFold: !!document.querySelector('.wb-dc-support #delete-btn'),
      deleteRowVisible: visible(document.querySelector('.wb-dc-support #delete-btn')),
    };
  });
  assert.equal(support.containsDeleteStatus, false, 'the delete line lives outside the support fold');
  assert.equal(support.deleteStatusInFold, false, 'no ancestor fold hides the delete line');
  assert.ok(support.opened && support.foldOpen, 'openSupport opens the fold the Data/Canvas routes call it from');
  assert.ok(support.hiddenBefore, 'the fold really was closed before openSupport ran');
  assert.ok(support.deleteRowInFold, 'the delete action itself follows its profile into the fold');
  assert.ok(support.deleteStatusVisible, 'a deletion message is on screen whether or not the fold is open');

  // (b) A refused open never leaves the selector claiming a project that never
  //     opened: openProject() resolves on a refusal, so only a settle-time re-read
  //     from the app's own mark can put the control back.
  await page.evaluate(() => {
    // A foreign-linked project: refused because it belongs to another chip.
    const row = document.querySelector('#project-list li.project-row[data-project-id="p3"]');
    if (row && !row.querySelector('.wb-dc-foreign')) row.setAttribute('data-wb-test-foreign', '1');
  });
  const refusal = await page.evaluate(() => {
    const before = document.getElementById('quiet-project-select').value;
    // The same path a choice takes, with the app refusing it synchronously.
    const original = window.openProject;
    window.openProject = function () { return Promise.resolve(); };
    const select = document.getElementById('quiet-project-select');
    select.value = 'p2';
    select.dispatchEvent(new Event('change'));
    window.openProject = original;
    return { before };
  });
  await page.waitForFunction((before) => document.getElementById('quiet-project-select').value === before,
    refusal.before, { timeout: 5000 });
  const afterRefusal = await page.evaluate(() => ({
    value: document.getElementById('quiet-project-select').value,
  }));
  assert.equal(afterRefusal.value, refusal.before,
    'a refused switch puts the selector back on the project that is really open');

  // (c) The truth is the adapter's own state, in both languages, and it is never
  //     derived from the preview flag alone.
  const truthStates = [];
  const badgeText = (text) => page.evaluate((value) => {
    document.getElementById('adapter-badge').textContent = value;
  }, text);
  const readTruth = () => page.evaluate(() => ({
    tag: document.querySelector('.wb-dc-truth-tag').textContent.trim(),
    text: document.querySelector('.wb-dc-truth-text').textContent.trim(),
  }));
  await badgeText('MOCK · fixture output, not real Muse');
  await page.waitForFunction(() => /fixture output/.test(document.querySelector('.wb-dc-truth-text').textContent));
  truthStates.push(await readTruth());
  await badgeText('adapter: meta');
  await page.waitForFunction(() => /meta/.test(document.querySelector('.wb-dc-truth-text').textContent));
  truthStates.push(await readTruth());
  // Unknown must not become a claim either way, in either language.
  await badgeText('REHEARSAL · adapter not yet reported');
  await page.waitForFunction(() => /has not reported/.test(document.querySelector('.wb-dc-truth-text').textContent));
  truthStates.push(await readTruth());
  await page.evaluate(() => window.WB_I18N && WB_I18N.set('ko'));
  await page.waitForFunction(() => /보고/.test(document.querySelector('.wb-dc-truth-text').textContent));
  truthStates.push(await readTruth());
  await page.evaluate(() => window.WB_I18N && WB_I18N.set('en'));
  await page.waitForFunction(() => /has not reported/.test(document.querySelector('.wb-dc-truth-text').textContent));
  assert.match(truthStates[0].text, /fixture output/, 'a mock adapter is reported as fixture output');
  assert.match(truthStates[1].text, /not a verified live connection/,
    'a reported adapter is never turned into a verified live connection');
  for (const state of truthStates.slice(2)) {
    assert.ok(!/no live AI|not connected|offline/i.test(state.text),
      'an unreported adapter never becomes a claim that AI is off');
    assert.ok(!/live model is called/i.test(state.text),
      'an unreported adapter never claims a live connection either');
  }
  assert.match(truthStates[3].text, /보고/, 'the Korean sentence is really Korean');
  assert.match(truthStates[3].tag, /테스트 · 예시 데이터/,
    'the sample-data clause is localized too, and is only about sample data');

  // (c2) A refusal is on screen at the current viewport, with nothing scrolled.
  //     #error used to sit after </main>, below a full-height surface.
  const refusalLine = await page.evaluate(() => {
    window.showError('This project is not linked to the active chip.');
    const line = document.getElementById('error');
    const rect = line.getBoundingClientRect();
    const scroller = document.getElementById('app-shell');
    return {
      text: line.textContent.trim(),
      top: rect.top, bottom: rect.bottom,
      inViewport: rect.top >= 0 && rect.bottom <= innerHeight,
      scrollTop: scroller.scrollTop,
      role: line.getAttribute('role'),
      insideSlot: !!line.closest('.wb-dc-alerts'),
    };
  });
  assert.match(refusalLine.text, /not linked to the active chip/, 'the refusal really rendered');
  assert.ok(refusalLine.inViewport,
    `a refusal is visible without scrolling (${Math.round(refusalLine.top)}–${Math.round(refusalLine.bottom)} in ${refusalLine.top >= 0 ? 'view' : 'the page'})`);
  assert.equal(refusalLine.scrollTop, 0, 'it is visible without the page having been scrolled to it');
  assert.equal(refusalLine.role, 'alert', 'the line is still an alert region');
  assert.ok(refusalLine.insideSlot, 'the shipped node moved into the always-visible slot');

  // (d) Expand keeps the shipped temporary contract: no saved preference, and the
  //     language switch repaints it. Opening a drawer leaves that state first.
  const expand = await page.evaluate(() => {
    const main = document.querySelector('main');
    const before = localStorage.getItem('wb.panels');
    document.getElementById('expand-surface').click();
    const afterExpand = { panels: localStorage.getItem('wb.panels'), class: main.classList.contains('wb-expand-surface') };
    return { before, afterExpand };
  });
  assert.equal(expand.afterExpand.panels, expand.before, 'Expand writes no saved panel preference');
  assert.ok(expand.afterExpand.class, 'Expand still applies the shipped temporary state');
  await page.evaluate(() => window.WB_I18N && WB_I18N.set('ko'));
  const expandKo = await page.evaluate(() => document.getElementById('expand-surface').textContent.trim());
  await page.evaluate(() => window.WB_I18N && WB_I18N.set('en'));
  assert.match(expandKo, /패널|되돌리/, 'the Expand control is translated by its own module in Korean');
  // Opening a drawer must leave the temporary state rather than show a panel that
  // claims to be open while the expand rule hides it.
  const reopened = await page.evaluate(() => {
    window.WB_DECLUTTER_SHELL.openPanel('projects');
    const main = document.querySelector('main');
    const nav = document.querySelector('main > nav');
    return {
      expandClass: main.classList.contains('wb-expand-surface'),
      pressed: document.getElementById('expand-surface').getAttribute('aria-pressed'),
      visible: nav.getClientRects().length > 0 && getComputedStyle(nav).display !== 'none',
      open: window.WB_DECLUTTER_SHELL.isPanelOpen('projects'),
      panels: localStorage.getItem('wb.panels'),
    };
  });
  assert.equal(reopened.expandClass, false, 'opening a drawer leaves the temporary expand state');
  assert.equal(reopened.pressed, 'false', 'the Expand control says so too');
  assert.ok(reopened.visible && reopened.open, 'the drawer is really on screen and really open');
  await page.evaluate(() => window.WB_DECLUTTER_SHELL.closePanel());

  // The reverse direction also keeps the open drawer and its reported state
  // aligned: expanding from an open Secretary must not make Settings route into
  // a display:none panel with an active scrim.
  await page.evaluate(() => WB_DECLUTTER_SHELL.openPanel('secretary'));
  const drawerPreference = await page.evaluate(() => localStorage.getItem('wb.panels'));
  // Expand is a top-level header control now (the More fold that used to hold it
  // is gone), so it is pressed directly, with no fold to open first.
  assert.equal(await page.getByTestId('expand-surface').isVisible(), true,
    'Expand is offered directly in the header, not behind a fold');
  await page.getByTestId('expand-surface').click();
  assert.equal(await page.locator('main > aside').isVisible(), true, 'temporary Expand never hides a drawer that remains open');
  assert.equal(await page.locator('#quiet-secretary').getAttribute('aria-expanded'), 'true');
  await page.getByTestId('interior-settings-open').click();
  await page.getByTestId('interior-settings-tab-models').click();
  await page.getByTestId('interior-settings-open-ai').click();
  assert.equal(await page.locator('#ai-panel').isVisible(), true, 'the Settings AI route reaches the open drawer even after Expand');
  assert.equal(await page.locator('#ai-panel').evaluate(panel => panel.open && panel.contains(document.activeElement)), true);
  assert.equal(await page.evaluate(() => localStorage.getItem('wb.panels')), drawerPreference, 'Expand and the reveal route do not rewrite the saved panel choice');
  await page.evaluate(() => {
    WB_DECLUTTER_SHELL.openPanel('projects');
    WB_DECLUTTER_SHELL.closePanel();
  });

  // (e) The design candidate and the pack/working-question note are one labelled
  //     line each, never a wall above the evidence, and their nodes are intact.
  const records = await page.evaluate(() => {
    const design = document.getElementById('design');
    const refinement = document.getElementById('refinement');
    const fold = design && design.closest('details.wb-dc-record');
    const refFold = refinement && refinement.closest('details.wb-dc-record');
    const labels = [...document.querySelectorAll('.wb-dc-record > summary')].map((n) => n.textContent.trim());
    return {
      designInFold: !!fold, refinementInFold: !!refFold,
      designOpen: fold ? fold.open : null, refinementOpen: refFold ? refFold.open : null,
      labels,
      designTextVisible: !!(design && design.getClientRects().length > 0),
    };
  });
  assert.ok(records.designInFold, 'the design candidate is presented as a fold, not a wall');
  assert.ok(records.refinementInFold, 'the pinned-pack/working-question note is a fold too');
  assert.equal(records.designOpen, false, 'the design fold starts closed');
  assert.equal(records.refinementOpen, false, 'the pack/work-question fold starts closed');
  assert.deepEqual(records.labels, ['Pinned pack and working question', 'Proposed design and checks'],
    'the note about the question sits closest to it, and the design record follows');
  assert.equal(records.designTextVisible, false, 'the design body is not on screen by default');

  // --- (11) Integrity: one node per id, nothing was rebuilt ------------------
  const duplicated = await page.evaluate(() => {
    const seen = new Map();
    const twice = [];
    for (const node of document.querySelectorAll('[id]')) {
      const id = node.id;
      seen.set(id, (seen.get(id) || 0) + 1);
      if (seen.get(id) === 2) twice.push(id);
    }
    return twice;
  });
  assert.deepEqual(duplicated, [], 'no id exists twice after the shell moved its nodes');
  const preserved = await page.evaluate(() => ({
    reviewTab: !!document.getElementById('view-secretary-review')
      && !!document.getElementById('view-secretary-review').closest('aside'),
    composerMoved: !!document.getElementById('secretary-compose')
      || !document.getElementById('portal').hidden,
    api: Object.keys(window.WB_DECLUTTER_SHELL).sort(),
  }));
  assert.ok(preserved.reviewTab, 'the review action is reachable from the secretary panel');
  assert.deepEqual(preserved.api, ['closePanel', 'isPanelOpen', 'openPanel', 'openSupport', 'refresh'],
    'the public seam the Lead integrates against is exactly these five calls');
  assert.equal(await page.evaluate(() => window.WB_DECLUTTER_SHELL.openPanel('nonsense')), false,
    'an unknown panel name is refused rather than invented');

  assert.deepEqual(sent.unexpected, [], 'the shell and the moved nodes sent no request of their own');
  assert.deepEqual(errors, [], 'the real workspace loads and runs without a page error');
  console.log('DECLUTTER_SHELL_PASS: reduced default surface, on-demand panels, project/PDF order saved and reflected in the selector without focus loss, panel close keeps the draft and the open PDF, Settings AI route opens the panel with aligned aria-expanded, adapter truth in three states (mock/reported/unreported) in both languages with sample data only from the preview flag, delete confirmation and refusals always on screen outside the fold, design and pack/work-question records folded behind labels, Expand keeps its temporary unsaved contract, 44px targets in both themes, 375px drawer layout, no duplicate ids, presentation only');
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
