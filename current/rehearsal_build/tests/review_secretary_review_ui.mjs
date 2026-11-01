// Secretary review UI: the gathered secretary exchanges for the open project.
// Serves the real frontend files and scripts the secretary-turn contract with
// fixtures (the parallel backend worker owns the real routes). No model call.
//
// Covered: newest-first order, chip grouping only when two chips are present,
// Keep through PATCH, deleting the last turn leaving the honest empty state, and
// "Delete all discarded" asking first and then reporting the server's count.
// On top of that the review-list defects: a project switch drops and closes the
// list, one write per double-click, the consent count matching the deletion
// scope beyond the route's default page, a kept row asking before it dies, a
// failed read never reading as "nothing was ever recorded", and Research getting
// its tab back.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const FILES = ['index.html', 'app.js', 'style.css', 'theme.js', 'i18n.js', 'i18n.ko.js', 'startup.js',
  'startup.css', 'canvas.js', 'latex.js', 'library.js', 'citations.js', 'secretary_review.js'];
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
    res.end(await readFile(root + file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });

try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  // --- Fixture state for the secretary-turn contract.
  // Two chips' turns, so the first render carries the group headings. Newest is
  // first here on purpose: the route answers newest first and the UI must keep it.
  // The store is per project: the review list holds the rows of ONE project and
  // its bulk delete acts on the open project, so the two must never mix.
  const store = {
    p1: [
      { id: 't1', project_id: 'p1', pack_id: 'social_science', question_id: 'q1', role: 'researcher',
        body_json: { text: 'Is the approval date the permit date?' }, status: 'recorded', created_at: '2026-10-27T18:00:00Z' },
      { id: 't2', project_id: 'p1', pack_id: 'social_science', question_id: 'q1', role: 'secretary',
        body_json: { text: 'No: the approval date is recorded separately in row 4.' }, status: 'recorded', created_at: '2026-10-27T17:00:00Z' },
      { id: 't3', project_id: 'p1', pack_id: 'engineering_measurement', question_id: 'q2', role: 'secretary',
        body_json: { text: 'The tolerance column is unverified.' }, status: 'recorded', created_at: '2026-10-26T09:00:00Z' },
      { id: 't4', project_id: 'p1', pack_id: 'engineering_measurement', question_id: 'q2', role: 'researcher',
        body_json: { text: 'Which rows carry the tolerance?' }, status: 'recorded', created_at: '2026-10-25T09:00:00Z' }
    ],
    p2: []
  };
  const sent = { patch: [], clear: [], deletes: [], listLimits: [], compile: [], chipLists: 0 };
  let clearCalls = 0;
  // The chip list is the only route that reports a chip's `source`. One chip ships
  // with the app (the store refuses to write into it) and one was installed by the
  // researcher (writing into it is the whole feature).
  const CHIPS = [
    { pack_id: 'social_science', title: 'Social Science', version: '1.0.0', source: 'builtin' },
    { pack_id: 'engineering_measurement', title: 'Engineering: Measurement', version: '1.0.0', source: 'file' }
  ];
  // What each chip's conversation.txt holds: a compile writes it, the read route
  // reports it, so "the answer was lost but the write landed" is reachable.
  const conversationOf = {};
  let failNextChipLists = 0;
  let compileGate = null;          // while set, the next compile waits on it
  let releaseCompile = null;
  let compileRefusal = null;       // { pack, message }: a 400, nothing written
  let loseCompileAnswerOf = null;  // the write lands, the answer does not
  // The clear route answers 2 the first time and 0 afterwards: the count shown is
  // the server's, not a local guess.
  // Failure knobs, so the honest-failure paths can be reached without a payload:
  //   failList.failNext  - fail this many upcoming list GETs
  //   failList.loseDeleteOf - answer DELETE with an error while the row really goes
  let failNextListGets = 0;
  let loseDeleteOf = null;
  const findTurn = (tid) => {
    for (const pid of Object.keys(store)) {
      const t = store[pid].find(x => x.id === tid);
      if (t) return { pid, turn: t };
    }
    return null;
  };
  await page.route('http://wb.test/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { body = null; }
    const reply = data => route.fulfill({ json: data });
    const fail = (status, message) => route.fulfill({ status, json: { message } });
    const listMatch = path.match(/^\/projects\/([^/]+)\/secretary-turns$/);
    if (listMatch && req.method() === 'GET') {
      const pid = decodeURIComponent(listMatch[1]);
      // The real route answers DEFAULT_LIMIT (50) rows unless a limit is asked
      // for, and caps at 200. The fixture repeats that: a caller that forgets the
      // limit gets a page it can mistake for the whole log.
      const asked = url.searchParams.get('limit');
      sent.listLimits.push(asked);
      if (failNextListGets > 0) { failNextListGets--; return fail(500, 'the turn store is locked'); }
      const limit = asked == null ? 50 : Math.min(Number(asked), 200);
      const rows = (store[pid] || []).slice(0, limit);
      return reply({ project_id: pid, turns: rows.map(t => ({ ...t })), model_calls: 0 });
    }
    if (/^\/secretary-turns\/[^/]+$/.test(path) && req.method() === 'PATCH') {
      const tid = decodeURIComponent(path.split('/').pop());
      sent.patch.push({ tid, body });
      const found = findTurn(tid);
      if (found) found.turn.status = body.status;
      const updated = found ? found.turn : null;
      return reply(updated ? { ...updated, model_calls: 0 } : {});
    }
    if (/^\/secretary-turns\/[^/]+$/.test(path) && req.method() === 'DELETE') {
      const tid = decodeURIComponent(path.split('/').pop());
      sent.deletes.push(tid);
      const found = findTurn(tid);
      if (found) store[found.pid] = store[found.pid].filter(t => t.id !== tid);
      // A lost answer: the row really went, the client only sees an error.
      if (loseDeleteOf === tid) { loseDeleteOf = null; return fail(500, 'the connection dropped'); }
      return reply({ deleted: true, turn_id: tid, model_calls: 0 });
    }
    const clearMatch = path.match(/^\/projects\/([^/]+)\/secretary-turns\/clear-discarded$/);
    if (clearMatch && req.method() === 'POST') {
      const pid = decodeURIComponent(clearMatch[1]);
      clearCalls++;
      sent.clear.push({ call: clearCalls, project: pid, body });
      const doomed = (store[pid] || []).filter(t => t.status === 'discarded');
      store[pid] = (store[pid] || []).filter(t => t.status !== 'discarded');
      return reply({ deleted: clearCalls === 1 ? 2 : doomed.length, project_id: pid, status: 'discarded', model_calls: 0 });
    }
    // --- The rest of the workspace, enough to open a project.
    if (/^\/projects\/[^/]+\/revisit-conditions$/.test(path) && req.method() === 'GET')
      return reply({ conditions: [], verified_at: '2026-10-28T12:00:00Z', model_calls: 0 });
    if (path === '/projects' && req.method() === 'GET')
      return reply({ projects: [{ id: 'p1', name: 'Permit study', kind: 'nyc' },
        { id: 'p2', name: 'Second study', kind: 'nyc' }] });
    if (path === '/domain-packs') return reply({ packs: [
      { pack_id: 'social_science', title: 'Social Science', version: '1.0.0' },
      { pack_id: 'engineering_measurement', title: 'Engineering: Measurement', version: '1.0.0' }] });
    const pidMatch = path.match(/^\/projects\/([^/]+)\/(citations|profile|briefing|clarifications|pack-memory|drafts)$/);
    if (pidMatch) {
      const what = pidMatch[2];
      if (what === 'citations') return reply({ citations: [] });
      if (what === 'profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
      if (what === 'briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
      if (what === 'clarifications') return reply({ clarifications: [] });
      if (what === 'pack-memory') return reply({ questions: [] });
      if (what === 'drafts') return reply({ project_id: pidMatch[1], drafts: [] });
    }
    if (path === '/chips') {
      sent.chipLists++;
      if (failNextChipLists > 0) { failNextChipLists--; return fail(500, 'the chip folder is locked'); }
      return reply({ chips: CHIPS.map(chip => ({ ...chip })) });
    }
    if (path === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
    const compileMatch = path.match(/^\/chips\/([^/]+)\/conversation\/compile$/);
    if (compileMatch && req.method() === 'POST') {
      const pack = decodeURIComponent(compileMatch[1]);
      sent.compile.push({ pack, body });
      if (compileGate) {
        await compileGate;
      }
      if (compileRefusal && compileRefusal.pack === pack) {
        return fail(400, compileRefusal.message);
      }
      // The store refuses a shipped chip, exactly as the real route does.
      const known = CHIPS.find(chip => chip.pack_id === pack);
      if (!known) return fail(404, `${pack} is not installed`);
      if (known.source === 'builtin') return fail(400, `${pack} is a built-in chip; built-in chips cannot be modified`);
      // Composed out of the project's own records, so the write is real even when
      // the answer never arrives.
      conversationOf[pack] = `Secretary conversation record\nChip: ${pack}\nProject: ${body && body.project_id}\n`;
      if (loseCompileAnswerOf === pack) { loseCompileAnswerOf = null; return fail(500, 'the connection dropped'); }
      return reply({ pack_id: pack, project_id: body && body.project_id, text: conversationOf[pack],
        bytes: conversationOf[pack].length, stored: true, model_calls: 0 });
    }
    const conversationMatch = path.match(/^\/chips\/([^/]+)\/conversation$/);
    if (conversationMatch && req.method() === 'GET') {
      const pack = decodeURIComponent(conversationMatch[1]);
      const text = conversationOf[pack] || '';
      return reply({ pack_id: pack, text, bytes: text.length });
    }
    if (path === '/library/tree') return reply({ folders: [], items: [] });
    if (path === '/briefing') return reply({ rows: [] });
    if (path === '/capabilities/latex') return reply({ available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null }, note: 'No TeX engine found on this machine.' });
    throw new Error('Unexpected endpoint ' + path);
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await page.goto(`${origin}/?api=http://wb.test`);
  // The app boots to the home menu; enter the workspace the way a researcher would.
  await page.getByTestId('home-menu-research').click();
  // Both fixture projects carry the same kind, so every row shares one testid;
  // the rows read in the order the route lists them (p1 first).
  await page.getByTestId('open-nyc').first().click();
  await page.waitForFunction(() => S.project === 'p1');

  // --- (1) The review list is reachable from the workspace tab bar, and it is
  // NOT a fifth home tile.
  assert.equal(await page.getByTestId('home-menu-secretary-review').count(), 0,
    'the review list is not added to the four home tiles');
  assert.equal(await page.getByTestId('secretary-review-view').isVisible(), false,
    'the review surface is hidden by default');
  await page.evaluate(() => openSecretaryReview());
  await page.getByTestId('secretary-review-item').first().waitFor();
  // The list asks the route for its maximum page: the default page (50) is
  // smaller than the scope of a bulk delete, so it may never be the basis for a
  // consent count.
  assert.ok(sent.listLimits.length > 0 && sent.listLimits.every(limit => limit === '200'),
    'every list read asks for the route maximum limit, got ' + JSON.stringify(sent.listLimits));
  assert.equal(await page.getByTestId('view-secretary-review').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByTestId('view-research').getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#question').isVisible(), false, 'the review list replaces the research surface');
  assert.equal(await page.getByTestId('secretary-review-view').isVisible(), true);

  // --- (2) Newest first: the row order is the route's stored order.
  const ids = await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId));
  assert.deepEqual(ids, ['t1', 't2', 't3', 't4'], 'turns are listed newest first');
  const whens = await page.getByTestId('secretary-review-when').allInnerTexts();
  assert.deepEqual(whens, ['2026-10-27T18:00:00Z', '2026-10-27T17:00:00Z', '2026-10-26T09:00:00Z', '2026-10-25T09:00:00Z'],
    'each row shows its stored time');

  // --- (3) Two chips present: a group heading per chip, in the order the newest
  // turn of each appears. The heading carries the stored pack_id untranslated.
  assert.equal(await page.getByTestId('secretary-review-group').count(), 2, 'one group heading per chip');
  assert.deepEqual(await page.getByTestId('secretary-review-group').allInnerTexts(),
    ['social_science', 'engineering_measurement'], 'chip headings read newest chip first, as stored');
  const firstRow = page.locator('[data-testid="secretary-review-item"][data-turn-id="t1"]');
  assert.equal(await firstRow.locator('[data-testid="secretary-review-chip"]').innerText(), 'social_science',
    'every row still names its own chip');
  assert.match(await firstRow.locator('[data-testid="secretary-review-text"]').innerText(),
    /Is the approval date the permit date\?/, 'the researcher text is shown as recorded');
  const secretaryRow = page.locator('[data-testid="secretary-review-item"][data-turn-id="t2"]');
  assert.match(await secretaryRow.locator('[data-testid="secretary-review-text"]').innerText(),
    /the approval date is recorded separately in row 4/, 'the secretary body is shown as recorded');
  // A secretary row carries the question it answers, taken from the logged researcher turn.
  assert.match(await secretaryRow.locator('[data-testid="secretary-review-question"]').innerText(),
    /Is the approval date the permit date\?/, 'the secretary row shows the question it answers');

  // --- (4) Keep changes only the status, through PATCH.
  await page.locator('[data-testid="secretary-review-item"][data-turn-id="t2"] [data-testid="secretary-review-keep"]').click();
  await page.waitForFunction(() => document.querySelector('[data-turn-id="t2"]').dataset.status === 'kept');
  assert.deepEqual(sent.patch.at(-1), { tid: 't2', body: { status: 'kept' } },
    'Keep patches only the status');
  assert.equal(store.p1.find(t => t.id === 't2').status, 'kept');
  assert.equal(await page.locator('[data-testid="secretary-review-item"][data-turn-id="t2"]').getAttribute('data-status'), 'kept');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /Only the status changed/);
  assert.equal(await page.locator('[data-testid="secretary-review-item"][data-turn-id="t2"] [data-testid="secretary-review-keep"]').isDisabled(),
    true, 'an already kept turn cannot be kept twice');
  assert.match(await page.locator('[data-turn-id="t2"] [data-testid="secretary-review-text"]').innerText(),
    /recorded separately/, 'the recorded text is untouched by Keep');

  // --- (5) Discard three turns (so "Delete all discarded" has work to do).
  for (const tid of ['t1', 't3', 't4']) {
    await page.locator(`[data-turn-id="${tid}"] [data-testid="secretary-review-delete"]`).click();
    await page.waitForFunction(id => !document.querySelector(`[data-turn-id="${id}"]`), tid);
  }
  assert.deepEqual(sent.deletes, ['t1', 't3', 't4'], 'Delete removes exactly that turn');
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['t2'], 'the deleted turns are gone from the list');
  // One chip left: no group heading repeats a single chip's name above itself.
  assert.equal(await page.getByTestId('secretary-review-group').count(), 0,
    'grouping appears only when more than one chip is present');

  // --- (6) Deleting the last turn leaves the honest empty state. That turn was
  // KEPT in (4), so it asks first; the confirmation is accepted here.
  const lastDialogs = [];
  page.once('dialog', async (dialog) => { lastDialogs.push(dialog.message()); await dialog.accept(); });
  await page.locator('[data-turn-id="t2"] [data-testid="secretary-review-delete"]').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 0);
  assert.equal(lastDialogs.length, 1, 'deleting a kept turn asks first');
  const absence = page.getByTestId('secretary-review-absence');
  await absence.waitFor();
  assert.equal(await absence.isVisible(), true, 'an empty log shows the empty state');
  assert.equal(await absence.innerText(), 'No secretary turns recorded for this project yet.',
    'the empty state says the log is empty rather than showing a sample turn');
  assert.equal(await page.getByTestId('secretary-review-clear-discarded').isVisible(), false,
    'with nothing discarded there is no clear action');

  // --- (7) "Delete all discarded" asks first, then reports the server's count.
  store.p1 = [
    { id: 'k1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A kept exchange.' }, status: 'kept', created_at: '2026-10-27T20:00:00Z' },
    { id: 'd1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T19:00:00Z' },
    { id: 'd2', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'Another discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T18:00:00Z' }
  ];
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  const clearButton = page.getByTestId('secretary-review-clear-discarded');
  assert.equal(await clearButton.isVisible(), true, 'the clear action appears once a turn is discarded');

  // First press: the confirmation is declined, so nothing is sent.
  const dialogs = [];
  page.once('dialog', async (dialog) => { dialogs.push(dialog.message()); await dialog.dismiss(); });
  await clearButton.click();
  await page.waitForFunction(() => document.querySelector('[data-testid="secretary-review-status"]').textContent.length > 0);
  assert.equal(dialogs.length, 1, 'the clear action asks before deleting');
  assert.equal(dialogs[0], 'Delete 2 discarded turns? Kept turns are never deleted.',
    'the confirmation names the count in plain words');
  assert.equal(sent.clear.length, 0, 'declining the confirmation sends nothing');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /Nothing was deleted/);
  assert.equal(await page.getByTestId('secretary-review-item').count(), 3, 'the list is unchanged after declining');

  // Second press: accepted, the route is called, and the server's count is shown.
  page.once('dialog', async (dialog) => { dialogs.push(dialog.message()); await dialog.accept(); });
  await clearButton.click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 1);
  assert.equal(sent.clear.length, 1, 'accepting the confirmation calls the clear route');
  assert.deepEqual(sent.clear[0].body, null, 'the clear route carries no rewritten count');
  // The rows go at once; the report waits for the re-read, so wait for it.
  await page.waitForFunction(() => /^Deleted \d+ discarded turn/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  const status = await page.getByTestId('secretary-review-status').innerText();
  assert.match(status, /Deleted 2 discarded turns\./,
    "the report carries the server's deleted count, not a local guess");
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['k1'], 'only the kept turn survives');
  assert.equal(await clearButton.isVisible(), false, 'with nothing discarded the clear action is gone');

  // --- (8) Switching surfaces unmounts the review list; coming back reloads it.
  await page.evaluate(() => openLibraryView());
  await page.locator('.library-citation-entry').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="citation-view"]').classList.contains('hidden') === false);
  assert.equal(await page.getByTestId('secretary-review-view').isVisible(), false, 'one working surface at a time');
  assert.equal(await page.getByTestId('view-secretary-review').getAttribute('aria-pressed'), 'false');
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 1);
  assert.equal(await page.locator('[data-testid="citation-view"]').isVisible(), false,
    'the citation composer is closed again');

  // --- (9) Switching PROJECT closes and clears the list. The rows of p1 must not
  // become the count of a bulk delete aimed at p2: that deletes a project's
  // history the person was never shown.
  store.p2 = [
    { id: 'p2d1', project_id: 'p2', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange of the second study.' }, status: 'discarded', created_at: '2026-10-28T09:00:00Z' }
  ];
  const p1IdsBeforeSwitch = store.p1.map(t => t.id);
  await page.evaluate(() => document.querySelectorAll('[data-testid="open-nyc"]')[1].click());
  await page.waitForFunction(() => S.project === 'p2');
  assert.equal(await page.getByTestId('secretary-review-view').isVisible(), false,
    'a project switch closes the review list: the new project is what the person now sees');
  assert.equal(await page.evaluate(() => document.querySelector('main section').classList.contains('secretary-review-mode')), false,
    'the review mode is off the section after the switch');
  assert.equal(await page.getByTestId('secretary-review-item').count(), 0,
    "another project's rows are gone from the list");
  assert.equal(await page.getByTestId('secretary-review-clear-discarded').isVisible(), false,
    'no stale clear action survives the switch');
  // Only after p2's own rows are loaded can a delete be aimed at p2.
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 1);
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['p2d1'], "the list shows p2's own rows, not the previous project's");
  const switchDialogs = [];
  page.once('dialog', async (dialog) => { switchDialogs.push(dialog.message()); await dialog.accept(); });
  await page.getByTestId('secretary-review-clear-discarded').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="secretary-review-status"]').textContent.length > 0);
  assert.equal(switchDialogs.length, 1, 'the clear still asks first after a switch');
  assert.equal(switchDialogs[0], 'Delete 1 discarded turn? Kept turns are never deleted.',
    "the count is the new project's own");
  assert.equal(sent.clear.at(-1).project, 'p2', 'the bulk delete is aimed at the open project');
  assert.deepEqual(store.p1.map(t => t.id), p1IdsBeforeSwitch,
    "the other project's history is untouched");

  // --- (10) A double-click on Delete sends ONE request and ends truthfully.
  store.p1 = [
    { id: 'k1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A kept exchange.' }, status: 'kept', created_at: '2026-10-27T20:00:00Z' },
    { id: 'd1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T19:00:00Z' },
    { id: 'd2', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'Another discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T18:00:00Z' }
  ];
  await page.evaluate(() => document.querySelectorAll('[data-testid="open-nyc"]')[0].click());
  await page.waitForFunction(() => S.project === 'p1');
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  const deletesBefore = sent.deletes.length;
  await page.locator('[data-turn-id="d1"] [data-testid="secretary-review-delete"]').dblclick();
  await page.waitForFunction(() => !document.querySelector('[data-turn-id="d1"]'));
  await page.waitForFunction(() => !document.querySelector('[data-turn-id="d1"] [data-testid="secretary-review-delete"]')?.disabled);
  const doubled = sent.deletes.slice(deletesBefore);
  assert.deepEqual(doubled, ['d1'], 'a double-click sends exactly one delete, got ' + JSON.stringify(doubled));
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /^Turn deleted\.$/,
    'the status reports the deletion that happened, not a second failed attempt');
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['k1', 'd2'], 'no phantom row is left behind');

  // A delete whose ANSWER was lost still really deleted: the list is re-read
  // instead of reporting a failure that did not happen.
  const deletesBeforeLost = sent.deletes.length;
  loseDeleteOf = 'd2';
  // Clear the previous line first: the wait below must be about THIS click's
  // answer, not the one already on screen.
  await page.evaluate(() => { document.querySelector('[data-testid="secretary-review-status"]').textContent = ''; });
  await page.locator('[data-turn-id="d2"] [data-testid="secretary-review-delete"]').click();
  await page.waitForFunction(() => /\w/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  assert.equal(sent.deletes.length, deletesBeforeLost + 1,
    'the lost answer is not retried as a second delete');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /^Turn deleted\.$/,
    'a delete that really happened is never reported as failed');
  assert.equal(await page.locator('[data-turn-id="d2"]').count(), 0,
    'the failed answer re-reads the list, so no phantom row is left behind');
  assert.equal(await page.getByTestId('secretary-review-item').count(), 1,
    'the re-read shows what the server holds');

  // --- (11) A double-click on Clear sends ONE request and reports the real count.
  store.p1 = [
    { id: 'k1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A kept exchange.' }, status: 'kept', created_at: '2026-10-27T20:00:00Z' },
    { id: 'd1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T19:00:00Z' },
    { id: 'd2', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'Another discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T18:00:00Z' }
  ];
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  const clearDialogs = [];
  const clearHandler = async (dialog) => { clearDialogs.push(dialog.message()); await dialog.accept(); };
  page.on('dialog', clearHandler);
  const clearCallsBefore = sent.clear.length;
  await page.getByTestId('secretary-review-clear-discarded').dblclick();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 1);
  await page.waitForFunction(() => /^Deleted \d+ discarded turn/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  page.removeListener('dialog', clearHandler);
  assert.equal(sent.clear.length, clearCallsBefore + 1,
    'a double-click sends exactly one bulk clear, got ' + JSON.stringify(sent.clear.slice(clearCallsBefore)));
  assert.equal(clearDialogs.length, 1, 'the confirmation is asked once, not twice');
  assert.equal(await page.getByTestId('secretary-review-status').innerText(), 'Deleted 2 discarded turns.',
    'the report is the number that was really deleted');
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['k1'], 'only the kept turn survives');

  // --- (12) A list longer than one page: the consent count may never understate
  // the deletion. The bulk delete removes EVERY discarded row of the project
  // while the list can only show the newest page, so the sentence must say so.
  const big = [];
  for (let i = 0; i < 205; i++) {
    // Indexes 0-29 and 200-204 are discarded; 200-204 sit beyond the page shown.
    const discarded = i < 30 || i >= 200;
    big.push({ id: `b${String(i).padStart(3, '0')}`, project_id: 'p1', pack_id: 'social_science',
      question_id: null, role: 'secretary', body_json: { text: `Stored turn ${i}.` },
      status: discarded ? 'discarded' : 'kept', created_at: `2026-10-${String(28 - Math.floor(i / 24)).padStart(2, '0')}T${String(23 - (i % 24)).padStart(2, '0')}:00:00Z` });
  }
  store.p1 = big;
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 200);
  assert.equal(sent.listLimits.at(-1), '200', 'the list asks the route for its maximum page');
  assert.equal(await page.getByTestId('secretary-review-truncated').isVisible(), true,
    'a full page discloses that older turns are not listed');
  const bigDialogs = [];
  page.once('dialog', async (dialog) => { bigDialogs.push(dialog.message()); await dialog.accept(); });
  await page.getByTestId('secretary-review-clear-discarded').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 170);
  await page.waitForFunction(() => /^Deleted \d+ discarded turn/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  assert.equal(bigDialogs.length, 1, 'the bulk delete still asks first');
  assert.doesNotMatch(bigDialogs[0], /^Delete 30 discarded turns\?/,
    'the consent never names only the rows on this page while deleting more');
  assert.match(bigDialogs[0], /At least 30/,
    'the consent names a lower bound, never a number the action exceeds');
  assert.match(bigDialogs[0], /beyond the newest 200|not listed here/,
    'the consent discloses that older turns are outside the page shown');
  assert.match(bigDialogs[0], /Kept turns are never deleted/,
    'the kept-turn promise is still part of the consent');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /Deleted 35 discarded turns\./,
    'the report is the number the server really deleted, including the rows beyond the page');
  assert.equal(store.p1.filter(t => t.status === 'discarded').length, 0, 'every discarded turn is gone');
  assert.equal(store.p1.filter(t => t.status === 'kept').length, 170, 'no kept turn was touched');

  // --- (13) A KEPT row is never destroyed by one unconfirmed click.
  store.p1 = [
    { id: 'k1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'The exchange the person deliberately preserved.' }, status: 'kept', created_at: '2026-10-27T20:00:00Z' },
    { id: 'd1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T19:00:00Z' }
  ];
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 2);
  const keptDialogs = [];
  page.once('dialog', async (dialog) => { keptDialogs.push(dialog.message()); await dialog.dismiss(); });
  const deletesBeforeKept = sent.deletes.length;
  await page.locator('[data-turn-id="k1"] [data-testid="secretary-review-delete"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="secretary-review-status"]').textContent.length > 0);
  assert.equal(keptDialogs.length, 1, 'deleting a kept turn asks in plain words first');
  assert.match(keptDialogs[0], /kept turn/i, 'the question names what is being deleted');
  assert.match(keptDialogs[0], /cannot be restored/, 'the question says it is irreversible');
  assert.equal(sent.deletes.length, deletesBeforeKept, 'declining the confirmation sends nothing');
  assert.equal(await page.locator('[data-turn-id="k1"]').count(), 1, 'the kept turn is still there after declining');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /Nothing was deleted/);
  // Accepting it does delete, and a discarded row still takes one click.
  page.once('dialog', async (dialog) => { await dialog.accept(); });
  await page.locator('[data-turn-id="k1"] [data-testid="secretary-review-delete"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-turn-id="k1"]'));
  assert.deepEqual(sent.deletes.slice(deletesBeforeKept), ['k1'], 'accepting deletes exactly that kept turn');
  await page.locator('[data-turn-id="d1"] [data-testid="secretary-review-delete"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-turn-id="d1"]'));
  assert.deepEqual(sent.deletes.slice(deletesBeforeKept), ['k1', 'd1'],
    'a discarded row is still one click: it was already marked for removal');

  // --- (14) A failed read says the list could not be loaded; it never says the
  // project has no recorded turns.
  failNextListGets = 1;
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => /unavailable/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  const failedAbsence = await page.getByTestId('secretary-review-absence').innerText();
  assert.equal(failedAbsence, 'The secretary turn list could not be loaded.',
    'a failed read has its own absence line');
  assert.doesNotMatch(failedAbsence, /No secretary turns recorded/,
    'a failed read never claims nothing was ever recorded');
  assert.match(await page.getByTestId('secretary-review-status').innerText(),
    /Secretary review unavailable: the turn store is locked\./,
    "the server's own reason is shown, as the Keep and Delete paths do");

  // --- (15) A failed re-read after a successful clear never shows an empty log
  // beside a plain success.
  store.p1 = [
    { id: 'k1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A kept exchange.' }, status: 'kept', created_at: '2026-10-27T20:00:00Z' },
    { id: 'd1', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'A discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T19:00:00Z' },
    { id: 'd2', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'Another discarded exchange.' }, status: 'discarded', created_at: '2026-10-27T18:00:00Z' }
  ];
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  failNextListGets = 1;
  page.once('dialog', async (dialog) => { await dialog.accept(); });
  await page.getByTestId('secretary-review-clear-discarded').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 1);
  const afterFailedReread = await page.getByTestId('secretary-review-status').innerText();
  assert.match(afterFailedReread, /Deleted 2 discarded turns\./,
    'the deletion that happened is still reported');
  assert.match(afterFailedReread, /could not be refreshed/,
    'the list is not passed off as current after the re-read failed');
  // The deleted rows are gone from the view: a failed re-read leaves no phantom.
  assert.deepEqual(await page.getByTestId('secretary-review-item').evaluateAll(rows => rows.map(r => r.dataset.turnId)),
    ['k1'], 'no discarded row lingers after the clear');
  assert.notEqual(await page.getByTestId('secretary-review-absence').innerText(),
    'No secretary turns recorded for this project yet.', 'a failed re-read never reads as an empty log');

  // --- (16) Review -> Research leaves Research as the pressed tab.
  failNextListGets = 0;
  await page.evaluate(() => openLibraryView());
  await page.locator('.library-citation-entry').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="citation-view"]').classList.contains('hidden') === false);
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelector('[data-testid="secretary-review-view"]').classList.contains('hidden') === false);
  await page.evaluate(() => showResearchView());
  await page.waitForFunction(() => document.querySelector('main section').classList.contains('secretary-review-mode') === false);
  assert.equal(await page.getByTestId('view-research').getAttribute('aria-pressed'), 'true',
    'returning to Research presses its own tab, as coming from citation or writing does');
  assert.equal(await page.getByTestId('view-secretary-review').getAttribute('aria-pressed'), 'false',
    'the review tab is released');

  // --- (17) The save control follows the chip's own source. GET /chips is the only
  // route that reports it, so it is read with the review load; a chip that ships
  // with the app is refused by the store and its control is therefore disabled
  // rather than offering a write that is known to fail.
  store.p1 = [
    { id: 'c1', project_id: 'p1', pack_id: 'engineering_measurement', question_id: null, role: 'secretary',
      body_json: { text: 'The tolerance column is unverified.' }, status: 'kept', created_at: '2026-10-27T12:00:00Z' },
    { id: 'c2', project_id: 'p1', pack_id: 'social_science', question_id: null, role: 'secretary',
      body_json: { text: 'The approval date is recorded separately.' }, status: 'kept', created_at: '2026-10-27T11:00:00Z' },
    { id: 'c3', project_id: 'p1', pack_id: null, question_id: null, role: 'secretary',
      body_json: { text: 'A turn recorded before the project had a chip.' }, status: 'kept', created_at: '2026-10-27T10:00:00Z' }
  ];
  const chipListsBefore = sent.chipLists;
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  assert.equal(sent.chipLists, chipListsBefore + 1,
    'the chip list is read once per review load, got ' + (sent.chipLists - chipListsBefore));
  const saveOf = (pack) => page.locator(`[data-testid="secretary-review-save-conversation"][data-pack-id="${pack}"]`);
  const installedSave = saveOf('engineering_measurement');
  const builtinSave = saveOf('social_science');
  const chiplessSave = page.locator('[data-testid="secretary-review-save-conversation"][data-pack-id=""]');
  assert.equal(await installedSave.count(), 1, 'the installed chip group carries the save control');
  assert.equal(await installedSave.isDisabled(), false,
    'a chip installed by the researcher can be written to');
  assert.equal(await builtinSave.count(), 1,
    'the shipped chip still shows the control: a missing button reads as a missing feature');
  assert.equal(await builtinSave.isDisabled(), true,
    'a built-in chip cannot be written to, so its control is disabled');
  const builtinNote = await page.locator('[data-testid="secretary-review-save-conversation-note"][data-blocked="builtin"]').innerText();
  assert.match(builtinNote, /ships with the app/,
    'the disabled control says a shipped chip cannot be written to');
  assert.equal(await chiplessSave.count(), 1, 'a chip-less group still shows the control');
  assert.equal(await chiplessSave.isDisabled(), true, 'a group with no chip id cannot save from itself');
  const chiplessNote = await page.locator('[data-testid="secretary-review-save-conversation-note"][data-blocked="nochip"]').innerText();
  assert.match(chiplessNote, /no chip id/,
    'the note says this group has no chip id rather than that nothing is written anywhere');
  assert.match(chiplessNote, /another group of this project/,
    'the note says the chip-less turns still travel into a chip saved from another group');
  assert.doesNotMatch(chiplessNote, /there is no conversation\.txt to write/,
    'the note never claims the turns are written nowhere');

  // --- (18) The control sends exactly the open project id, with the chip in the
  // path; it is disabled while the write is in flight and enabled again after.
  compileGate = new Promise(resolve => { releaseCompile = resolve; });
  const compileRequest = page.waitForRequest(request => request.method() === 'POST'
    && /\/chips\/[^/]+\/conversation\/compile$/.test(new URL(request.url()).pathname));
  await installedSave.click();
  await compileRequest;
  // The answer is still held by the route, so this is the in-flight state.
  await page.waitForFunction(() => document.querySelector(
    '[data-testid="secretary-review-save-conversation"][data-pack-id="engineering_measurement"]').disabled === true);
  assert.equal(await saveOf('social_science').isDisabled(), true,
    'a shipped chip stays disabled while another chip is being written');
  assert.equal(await saveOf('').isDisabled(), true,
    'a chip-less group stays disabled while another chip is being written');
  releaseCompile();
  compileGate = null;
  releaseCompile = null;
  await page.waitForFunction(() => /saved into chip/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  assert.equal(sent.compile.length, 1, 'one click sends one compile, got ' + JSON.stringify(sent.compile));
  assert.equal(sent.compile[0].pack, 'engineering_measurement', 'the chip id is in the path');
  assert.deepEqual(sent.compile[0].body, { project_id: 'p1' },
    'the body carries the open project id and nothing else');
  assert.equal(await installedSave.isDisabled(), false,
    'the control is enabled again once the write is answered');
  assert.match(await page.getByTestId('secretary-review-status').innerText(),
    /Conversation saved into chip engineering_measurement/, 'the stored bytes are reported');
  delete conversationOf.engineering_measurement;

  // --- (19) A 400 is reported as the server's refusal, never as a save.
  compileRefusal = { pack: 'engineering_measurement', message: 'the chip file is read-only' };
  await installedSave.click();
  await page.waitForFunction(() => /not saved/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  const refused = await page.getByTestId('secretary-review-status').innerText();
  assert.match(refused, /the chip file is read-only/, "the server's own reason is shown");
  assert.doesNotMatch(refused, /saved into chip/i,
    'a refused write never prints a saved claim');
  assert.doesNotMatch(refused, /saved\./i, 'a refused write never prints a bare saved claim');
  assert.equal(await installedSave.isDisabled(), false, 'the control recovers after a refusal');
  compileRefusal = null;

  // A lost answer whose write DID land says so instead of claiming nothing happened.
  loseCompileAnswerOf = 'engineering_measurement';
  await installedSave.click();
  await page.waitForFunction(() => /may have completed/.test(
    document.querySelector('[data-testid="secretary-review-status"]').textContent));
  const lost = await page.getByTestId('secretary-review-status').innerText();
  assert.match(lost, /the chip now holds text/, 'the lost answer is checked against the chip itself');
  assert.doesNotMatch(lost, /^The conversation was not saved/,
    'a write that landed is never reported as not saved');
  assert.match(await page.getByTestId('secretary-review-status').innerText(), /the connection dropped/,
    "the lost answer's reason is still shown");
  delete conversationOf.engineering_measurement;

  // --- (20) Activating a shipped chip's control sends nothing: the disable is a
  // real guard, not decoration.
  const compilesBeforeBuiltin = sent.compile.length;
  await builtinSave.click({ force: true }).catch(() => {});
  await page.waitForTimeout(150);
  assert.equal(sent.compile.length, compilesBeforeBuiltin,
    'a built-in chip group sends no compile request');
  await chiplessSave.click({ force: true }).catch(() => {});
  await page.waitForTimeout(150);
  assert.equal(sent.compile.length, compilesBeforeBuiltin,
    'a chip-less group sends no compile request');

  // --- (21) An unreadable chip list is not a guess: every save control stays
  // disabled and says why, rather than offering a write that may be refused.
  failNextChipLists = 1;
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  // Wait for the failed list to reach the screen: until it does, the previous
  // render is still showing the controls it built.
  await page.locator('[data-testid="secretary-review-save-conversation-note"][data-blocked="unknown"]').first().waitFor();
  const unknownSave = saveOf('engineering_measurement');
  assert.equal(await unknownSave.isDisabled(), true,
    'without a chip list no save control is enabled');
  assert.match(await page.locator('[data-testid="secretary-review-save-conversation-note"][data-blocked="unknown"]').first().innerText(),
    /chip list could not be read/, 'the disabled control says the chip list could not be read');
  const compilesBeforeUnknown = sent.compile.length;
  await unknownSave.click({ force: true }).catch(() => {});
  await page.waitForTimeout(150);
  assert.equal(sent.compile.length, compilesBeforeUnknown,
    'an unknown-source group sends no compile request');
  // Once the list reads, the installed chip's control comes back; the shipped one
  // does not.
  await page.evaluate(() => showResearchView());
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length === 3);
  await page.waitForFunction(() => document.querySelector('[data-testid="secretary-review-save-conversation"][data-pack-id="engineering_measurement"]').disabled === false);
  assert.equal(await saveOf('social_science').isDisabled(), true,
    'the shipped chip is disabled again after a successful list read');

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('SECRETARY_REVIEW_UI_PASS: newest-first list, chip grouping only for two chips, Keep through PATCH with the body untouched, honest empty state after the last delete, clear-discarded asking first and reporting the server count; project switch drops and closes the list, one request per double-click, a consent count that never understates the deletion scope, a kept row asking before it is destroyed, a failed read never reading as an empty log, Research keeping its pressed tab, and the save-conversation control reading /chips once per load so a shipped chip and a chip-less group stay disabled and send nothing, an installed chip sending only the open project id, a 400 shown as the server\'s refusal, and a lost answer checked against the chip itself');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
