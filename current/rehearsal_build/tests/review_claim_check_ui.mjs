// Frames F–I of design/mockups/workbench-clarity: Claim–Evidence Verification.
//
// F  pick the claim, the sources and an optional table — no model, no cost
// G  the numbers in the claim, reconciled by ordinary code, before any model call
// H  the consent preview: exactly what is sent, where, on which model, for how
//    much — with no execute button until an explicit click
// I  the verdict: each quote marked verified or not, the downgrade explained, the
//    decision (accept / override with a required reason), the trust receipt
//
// The states a check can end in are covered too: cloud use off (no execute button
// at all), a stale result, an interrupted run with an unknown charge, and a run
// that went over its reservation.
//
// Everything runs the real frontend in headless Chromium against scripted HTTP
// fixtures. No Electron, no provider, no model, no network. Synthetic data only.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const frontend = path.resolve(import.meta.dirname, '../frontend');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };

const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = pathname === '/' ? 'index.html' : pathname.slice(1);
  try {
    const body = await readFile(path.join(frontend, file));
    res.setHeader('Content-Type', TYPES[path.extname(file)] || 'application/octet-stream');
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });

// --- the synthetic material ------------------------------------------------
const CLAIM = 'Recorded approval timing identifies the intervention date in 42 of the sampled districts.';
// The text typed over a preview that is still out. It must never inherit a
// charge approved for CLAIM.
const REPLACED = 'A completely different sentence typed while the preview was still out.';
const SOURCE_A = 'Synthetic source A: approval and intervention dates were recorded separately in every district.';
const SOURCE_B = 'Synthetic source B: the 42 sampled districts report the two dates as distinct fields.';
// A paper in the project, so a claim can be taken from a selection in it. The
// sentence below is the one a selection is made over.
const DRAFT_SENTENCE = 'Synthetic drafting sentence that the researcher selects.';
const DRAFT = {
  draft_id: 'd1', project_id: 'p1', question_id: null, title: 'Synthetic paper',
  engine: 'auto', main_file: 'main.tex', revision: 1,
  files: [{ path: 'main.tex', text: `\\documentclass{article}\n\\begin{document}\n${DRAFT_SENTENCE}\n\\end{document}\n` }],
  sections: [], previous: null, created_at: 'x', updated_at: 'x', model_calls: 0
};

// One preview, shaped exactly like app/claim_check.py's `_preview` answer.
function preview(over = {}) {
  return {
    check_id: 'cc-1', preview_hash: 'a'.repeat(64), task: 'claim_check',
    provider: 'fixture-provider', model: 'fixture-model-1', max_inference_calls: 1,
    maximum_reserved_usd: 0.42,
    estimate_note: 'Conservative reservation, not a predicted bill: the full documented context is reserved at the uncached rate. Actual usage is settled when it is reported.',
    estimate_note_code: 'conservative_reservation_not_a_bill',
    pricing_date: null, data_use_mode: 'Standard',
    model_access_verified: true, account_ready: true, cloud_enabled: true,
    requires_explicit_consent: true, expires_in_seconds: 900,
    baseline_definition: 'One direct call with the shared safety core.',
    transmission: {
      destination: 'fixture provider (synthetic, no network)', claim_text: CLAIM,
      sources: [
        { item_id: 'i1', title: 'Synthetic source A', page_count: 2,
          excerpt: [{ page: 1, text: SOURCE_A, truncated: false },
                    { page: 2, text: SOURCE_B, truncated: true }] },
        { item_id: 'i2', title: 'Synthetic source B', page_count: 1,
          excerpt: [{ page: 1, text: SOURCE_B, truncated: false }] }
      ],
      table_item_id: null, max_output_tokens: 1200, reasoning: null,
      store: false, tools: [], whole_csv_rows: false,
      includes_canvas_notes_and_question_decisions: false,
      content_sha256: 'b'.repeat(64), body_sha256: 'c'.repeat(64)
    },
    numeric_reconciliation: { numbers: [
      { text: '42', status: 'match', reason: null },
      { text: '1998', status: 'not_found', reason: 'not in the transmitted excerpt' }
    ] },
    verification_plan: 'Every returned quote is checked verbatim against the excerpt text '
      + 'actually transmitted above; a quote outside it never counts.',
    verification_plan_code: 'verbatim_excerpt_only_v1',
    verification_plan_thresholds: { min_quote_words: 5, min_quote_nonspace_chars: 20 },
    semantic_correctness: 'HUMAN_REVIEW_REQUIRED',
    ...over
  };
}

// One completed check. The second quote is the case the brief calls out: it is
// cited across a page break AND it did not match — it must not read as verified.
function completed(over = {}) {
  return {
    check_id: 'cc-1', project_id: 'p1', draft_id: null, question_id: null,
    claim_text: CLAIM, claim_location: null,
    source_items: [{ item_id: 'i1', title: 'Synthetic source A', sha256: 'd'.repeat(64) }],
    table_item_id: null, status: 'completed_known', job_id: 'job-1',
    model: 'fixture-model-1', provider: 'fixture-provider', mode: 'direct',
    reservation_usd: 0.42, error_code: null, stale: false, stale_reasons: [],
    decision: null, created_at: '2026-01-01T00:00:00Z',
    result: {
      verdict: 'partially_supported', model_verdict: 'supported', downgraded: true,
      downgrade_reason: 'One of the two quotes could not be found in the text that was sent.',
      rationale: 'The sources say the two dates are recorded separately; they do not verify the count.',
      // The backend's precedence, reproduced exactly: a downgraded verdict means no
      // substantive quote survived the verbatim check, so the model's note does not
      // lead (shown: false) and the UI supplies its own sentence for the code. The
      // note is still stored and reachable.
      secretary_note: 'I think the dates are recorded separately in the source, so this looks partly supported to me. It seems the count itself is not in the part you sent, so I would check the original table before relying on that number.',
      secretary_note_shown: false,
      note_code: 'quotes_not_found_in_sources',
      language: 'en',
      verified_quote_count: 1,
      semantic_correctness: 'HUMAN_REVIEW_REQUIRED',
      quotes: [
        { text: SOURCE_A, page: 1, item_id: 'i1', verified: true, cross_page: false, reason: null },
        { text: 'Approval timing equals the intervention date in every district.',
          page: 2, item_id: 'i1', verified: false, cross_page: true,
          reason: 'outside_transmitted_excerpt' }
      ]
    },
    ...over
  };
}

// The three checks that arrive from the project's own list, not from a run.
const HISTORY = [
  completed({ check_id: 'cc-stale', stale: true, stale_reasons: ['claim', 'source'] }),
  {
    check_id: 'cc-interrupted', project_id: 'p1', draft_id: null, question_id: null,
    claim_text: 'A second synthetic claim that was interrupted.', claim_location: null,
    source_items: [], table_item_id: null, status: 'interrupted_unknown_charge',
    job_id: 'job-2', model: 'fixture-model-1', provider: 'fixture-provider', mode: 'direct',
    reservation_usd: 0.42, result: { error_code: 'interrupted_unknown_charge' },
    error_code: 'interrupted_unknown_charge', stale: false, stale_reasons: [],
    decision: null, created_at: '2026-01-01T00:01:00Z'
  },
  {
    check_id: 'cc-over', project_id: 'p1', draft_id: null, question_id: null,
    claim_text: 'A third synthetic claim that cost more than reserved.', claim_location: null,
    source_items: [], table_item_id: null, status: 'failed_known_charge',
    job_id: 'job-3', model: 'fixture-model-1', provider: 'fixture-provider', mode: 'direct',
    reservation_usd: 0.42, result: { error_code: 'usage_exceeded_reservation' },
    error_code: 'usage_exceeded_reservation', stale: false, stale_reasons: [],
    decision: null, created_at: '2026-01-01T00:02:00Z'
  }
];

const RECEIPT = {
  job_status: 'completed_known', outcome: 'result_recorded', reservation_usd: 0.42,
  model: 'fixture-model-1', data_use_mode: 'Standard', result_integrity: 'verified'
};

// A 160-character unbroken alphanumeric run: a digest, a file stem, a store id —
// the kind of text a source title or a model note can genuinely carry. It has no
// break opportunity of any kind, so only `overflow-wrap: anywhere` keeps it
// inside the sheet.
const LONG_RUN = 'A1b2C3d4E5f6G7h8I9j0'.repeat(8);
const LONG_TITLE = 'Qz9Wx8Yv7Ut6Sr5Pq4Ab'.repeat(8);
// A quoted source sentence and a claim can each carry a bare URL, which — unlike
// prose — has no break opportunity at all. Each gets its own run, so the node it
// lands in can be told apart from the others by what it contains.
const LONG_QUOTE_RUN = ('https://example.test/' + 'Zx9Cv8Bn7Mq6'.repeat(12)).slice(0, 160);
const LONG_CLAIM_RUN = ('https://example.test/' + 'Lk4Jh5Gf6Ds7'.repeat(12)).slice(0, 160);
assert.equal(LONG_RUN.length, 160, 'the fixture run is the 160 characters the reviewer asked for');
assert.equal(LONG_TITLE.length, 160, 'the fixture title run is 160 characters too');
assert.ok(LONG_QUOTE_RUN.length === 160 && LONG_CLAIM_RUN.length === 160,
  'the quoted sentence and the claim each carry a 160-character run of their own');

// A receipt for a run that ended without a known charge: error_code is the only
// field that names what happened, so it has to survive to the screen.
const RECEIPT_INTERRUPTED = {
  job_status: 'interrupted', outcome: 'charge_unknown', reservation_usd: 0.42,
  model: 'fixture-model-1', data_use_mode: 'Standard', result_integrity: 'absent',
  error_code: 'interrupted_unknown_charge'
};

async function open(viewport, initial = {}) {
  const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  // What the next prepare answer is, and whether the list has checks in it.
  const scene = { prepare: preview(), execute: completed(), checks: [], prepared: 0, executed: 0, ...initial };
  await page.route('http://api.test/**', async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    const method = route.request().method();
    const json = (data) => route.fulfill({ json: data });
    if (pathname === '/domain-packs') return json({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (pathname === '/chips') return json({ chips: [] });
    if (pathname === '/chips/social_science') return json({ pack_id: 'social_science', source: 'builtin' });
    if (pathname === '/projects') return json({ projects: [
      { id: 'p1', name: 'Synthetic claim study', kind: 'fx', pack_id: null },
      // A second paper, so a request can be made in one project and answered
      // after the reader has moved to the other. Its kind differs because the
      // project list keys each row's testid by kind.
      { id: 'p2', name: 'Synthetic second study', kind: 'theory', pack_id: null }
    ] });
    if (pathname === '/briefing') return json({ rows: [] });
    if (pathname === '/projects/p1/profile') return json({ variables: [], computed_by: 'ordinary code, no model call' });
    if (pathname === '/projects/p1/briefing') return json({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (pathname === '/projects/p1/clarifications') return json({ clarifications: [] });
    if (pathname === '/projects/p1/pack-memory') return json({ questions: [] });
    if (pathname === '/projects/p1/revisit-conditions') return json({ conditions: [], model_calls: 0 });
    if (pathname === '/projects/p1/secretary-turns') return json({ turns: [], model_calls: 0 });
    // The second paper is empty on purpose: nothing has ever been checked there,
    // so anything that appears in it came from the other paper's request.
    if (/^\/projects\/p2\/(profile|briefing|clarifications|pack-memory|revisit-conditions|secretary-turns|drafts)$/.test(pathname)) {
      if (pathname === '/projects/p2/briefing')
        return json({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
      if (pathname === '/projects/p2/profile') return json({ variables: [], computed_by: 'ordinary code, no model call' });
      if (pathname === '/projects/p2/drafts') return json({ project_id: 'p2', drafts: [] });
      if (pathname === '/projects/p2/secretary-turns') return json({ turns: [], model_calls: 0 });
      if (pathname === '/projects/p2/revisit-conditions') return json({ conditions: [], model_calls: 0 });
      if (pathname === '/projects/p2/pack-memory') return json({ questions: [] });
      return json({ clarifications: [] });
    }
    if (pathname === '/projects/p2/claim-checks') {
      scene.listedFor = (scene.listedFor || []).concat(['p2']);
      return json({ checks: [] });
    }
    if (pathname === '/projects/p1/drafts') return json({ project_id: 'p1',
      drafts: scene.draft ? [{ draft_id: scene.draft.draft_id, title: scene.draft.title }] : [] });
    if (pathname === '/capabilities/latex') return json(scene.draft
      ? { available: true, engines: { pdflatex: '/usr/bin/pdflatex' }, checked_at: 'now', pdf_export: true }
      : { available: false, engines: {}, note: 'Synthetic fixture: no compile requested.' });
    if (scene.draft && pathname === `/drafts/${scene.draft.draft_id}` && method === 'GET')
      return json(scene.draft);
    if (pathname === '/library/tree') return json({ folders: [], items: [
      { id: 'i1', title: 'Synthetic source A', kind: 'pdf' },
      // A note is prose like any other stored text, so it is offered as a source;
      // a results table is not prose, so it belongs only in the table select.
      { id: 'n1', title: 'Synthetic note', kind: 'note', day: '2026-01-01', text: SOURCE_B },
      { id: 't1', title: 'Synthetic table', kind: 'table' },
      { id: 'i2', title: 'Synthetic source B', kind: 'pdf' }
    ] });
    // --- the claim-check endpoints, exactly the shipped paths -------------
    if (pathname === '/projects/p1/claim-checks/numbers' && method === 'POST') {
      // The free half: no consent, no preview hash, no cloud. It answers with the
      // same object prepare embeds, so the numbers survive a blocked prepare.
      scene.numbersCalls = (scene.numbersCalls || 0) + 1;
      scene.lastNumbers = JSON.parse(route.request().postData() || '{}');
      return json({ numeric: scene.numbers || preview().numeric_reconciliation });
    }
    if (pathname === '/projects/p1/claim-checks/prepare' && method === 'POST') {
      scene.prepared += 1;
      scene.lastPrepare = JSON.parse(route.request().postData() || '{}');
      // The inputs of this request, kept apart from `lastPrepare` because the
      // response below has to be an answer about *these* inputs even if the page
      // has moved on by the time it is handed over.
      const inputs = scene.lastPrepare;
      // A gate the test can hold shut, so an answer can be made to arrive after
      // the claim it described was edited. 1200 ms is the reviewer's own figure.
      if (scene.prepareGate) await scene.prepareGate;
      if (scene.prepareStatus && scene.prepareStatus !== 200) {
        return route.fulfill({ status: scene.prepareStatus,
          json: { error: scene.prepareCode, message: scene.prepareMessage || scene.prepareCode, details: {} } });
      }
      // The preview echoes the claim and language it was prepared for, so the
      // screen can be checked for a card describing something no longer true.
      return json({ ...scene.prepare,
        transmission: { ...(scene.prepare.transmission || {}),
          claim_text: inputs.claim_text, language: inputs.language } });
    }
    if (/^\/projects\/p1\/claim-checks\/[^/]+\/execute$/.test(pathname) && method === 'POST') {
      scene.executed += 1;
      scene.lastExecute = JSON.parse(route.request().postData() || '{}');
      // A gate the test can hold shut, so the screen can be inspected while the
      // run is still out. Without it the loading state is a race.
      if (scene.executeGate) await scene.executeGate;
      // A refusal set after the preview was approved: the cloud switch turned off
      // between prepare and execute, which the backend answers with a plain 409.
      if (scene.executeStatus) {
        return route.fulfill({ status: scene.executeStatus,
          json: { error: scene.executeCode || null,
            message: scene.executeMessage || 'Cloud mode is off', details: {} } });
      }
      // The executed check joins the project's list, as the real backend's does.
      scene.checks = [scene.execute];
      return json(scene.execute);
    }
    if (/^\/projects\/p1\/claim-checks\/[^/]+\/receipt$/.test(pathname)) return json(scene.receipt || RECEIPT);
    if (/^\/projects\/p1\/claim-checks\/[^/]+\/decide$/.test(pathname) && method === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      // A gate the test can hold shut, so a decision can be made to land after
      // the reader has opened another paper.
      if (scene.decideGate) await scene.decideGate;
      const decided = { ...scene.execute, decision: { decision: body.decision, note: body.note || null, decided_at: '2026-01-01T00:05:00Z' } };
      // The decision is recorded on the stored check, as the backend does, so
      // both the sheet and the list show it when the reader comes back.
      scene.checks = scene.checks.map((c) => (c.check_id === decided.check_id ? decided : c));
      return json(decided);
    }
    if (pathname === '/projects/p1/claim-checks') {
      // The backend filters by an exact match on draft_id when the query asks
      // for one. Modelled here exactly, because the defect this guards was the
      // screen sending a draft_id and then hiding every typed check (which is
      // stored with draft_id NULL) behind that filter.
      const wanted = url.searchParams.get('draft_id');
      const rows = wanted == null ? scene.checks
        : scene.checks.filter((c) => (c.draft_id == null ? '' : c.draft_id) === wanted);
      return json({ checks: rows });
    }
    // The settings re-read the screen asks for after a refused send.
    if (pathname === '/ai/registry') return json({ settings: { cloud_enabled: scene.cloudEnabled !== false, budget_usd: 150 } });
    return json({});
  });

  await page.addInitScript(() => {
    try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {}
    window.WB_DESKTOP = {
      openChip: () => {}, newChip: () => {}
    };
  });
  await page.goto(`${origin}/?api=http://api.test`);
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForTimeout(200);

  // The workspace, with the one synthetic project open.
  await page.getByTestId('home-menu-research').click();
  await page.locator('#quiet-projects').click();
  await page.getByTestId('open-fx').click();
  await page.waitForFunction(() => typeof S === 'object' && S.project === 'p1');
  if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
    await page.locator('#quiet-projects').click();
  // The source list is read from the library, so the library must be loaded
  // before the picker can offer anything.
  await page.evaluate(() => openLibraryView());
  await page.waitForFunction(() => typeof libraryState === 'object'
    && libraryState.tree !== null && !libraryState.loading);
  for (const which of ['projects', 'secretary']) {
    await page.evaluate((w) => {
      if (WB_DECLUTTER_SHELL.isPanelOpen(w)) WB_DECLUTTER_SHELL.closePanel(w);
    }, which);
  }
  await page.evaluate(() => openWritingView());
  // F–I is a sheet opened from the paper: closed it costs one line, so the draft
  // keeps the first screen. That is asserted before it is opened, once per size.
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-open"]'));
  const closed = await page.evaluate(() => {
    const host = document.getElementById('claim-check-host');
    const bar = host.querySelector('.cc-sheetbar');
    const trig = document.querySelector('[data-testid="dw-open-paper-actions"]');
    const r = trig ? trig.getBoundingClientRect() : null;
    const hit = r ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
    return {
      height: Math.round(bar.getBoundingClientRect().height),
      root: !!host.querySelector('[data-testid="cc-root"]'),
      // The shipped paper bar keeps its pointer target: the sheet must not cover it.
      paperReachable: !!trig && (hit === trig || trig.contains(hit))
    };
  });
  assert.ok(closed.height <= 56, `the closed claim sheet costs one line (${closed.height}px)`);
  assert.equal(closed.root, false, 'the closed claim sheet draws no form');
  assert.equal(closed.paperReachable, true, 'the paper bar stays reachable while the sheet is closed');
  await page.locator('[data-testid="cc-open"]').click();
  await page.waitForFunction(() => document.getElementById('claim-check-host')
    && document.getElementById('claim-check-host').querySelector('[data-testid="cc-root"]'));
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-sources"]'));
  return { page, errors, scene };
}

// A claim typed in, one source ticked, the preview asked for: the same three
// acts a person performs, so every later assertion is about what they see.
//
// It types with the keyboard rather than filling, because filling sets the value
// in one shot and would hide the defect this guards: a keystroke that re-renders
// the sheet destroys the textarea and keeps only the last character typed.
async function prepareWith(page, tag = '') {
  const field = page.locator('[data-testid="cc-claim"]');
  await field.click();
  await field.type(CLAIM, { delay: 15 });
  assert.equal(await field.inputValue(), CLAIM,
    `${tag}: the whole claim is in the field after typing it one key at a time`);
  assert.equal(await page.evaluate(() => document.activeElement
    && document.activeElement.getAttribute('data-testid')),
  'cc-claim', `${tag}: the cursor stays in the claim field while typing`);
  await page.locator('[data-testid="cc-source-i1"]').check();
  await page.locator('[data-testid="cc-prepare"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]'));
}

const text = (page, selector) => page.evaluate((sel) => {
  const el = document.querySelector(sel);
  return el ? el.textContent.trim() : null;
}, selector);

// Coming back to a paper: the switch lands on Home, so the Writing surface has
// to be opened again, and the sheet opened afresh — opening is what asks the
// backend for this paper's list, which is the thing under test here.
async function backToWriting(page, id, name) {
  await page.evaluate(([pid, pname]) => openProject(pid, pname), [id, name]);
  await page.waitForFunction((pid) => S.project === pid, id);
  await page.evaluate(() => openWritingView());
  await page.waitForFunction((pid) => WB_CLAIM_CHECK.state.project === pid, id);
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-root"]'));
  // A sheet left open from before would keep the list it already has; opening is
  // the event that re-reads it, so it is closed first when it was open.
  if (await page.locator('[data-testid="cc-close"]').isVisible().catch(() => false))
    await page.locator('[data-testid="cc-close"]').click();
  await page.locator('[data-testid="cc-open"]').click();
  await page.waitForFunction(() => WB_CLAIM_CHECK.state.open === true);
}

const count = (page, selector) => page.locator(selector).count();

// The sheet scrolls internally, so a step can exist in the DOM and still be
// entirely off screen. A person pressing a button and seeing nothing change is
// the defect this guards: after each step the new one must be inside the
// visible box of the sheet and of the window.
async function onScreen(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { found: false };
    const box = el.getBoundingClientRect();
    if (!box.height) return { found: true, visible: false, reason: 'zero height' };
    const scroller = el.closest('.cc-root') || el.parentElement;
    const area = scroller.getBoundingClientRect();
    const inSheet = box.top < area.bottom && box.bottom > area.top;
    const inWindow = box.top < window.innerHeight && box.bottom > 0;
    return { found: true, visible: inSheet && inWindow, sheet: inSheet, window: inWindow };
  }, selector);
}

try {
  for (const viewport of [{ width: 1280, height: 800 }, { width: 720, height: 480 }]) {
    const tag = `${viewport.width}x${viewport.height}`;

    // ================= F and G, then H and I on one real run ==============
    {
      const { page, errors, scene } = await open(viewport);

      // --- F: the free step, before anything is sent -----------------------
      const picker = await page.evaluate(() => {
        const host = document.getElementById('claim-check-host');
        const steps = [...host.querySelectorAll('.cc-step')];
        return {
          steps: steps.length,
          first: steps[0] ? steps[0].getAttribute('data-testid') : null,
          free: document.querySelector('.cc-free')?.textContent.trim(),
          claimLabel: document.querySelector('label[for="cc-claim"]')?.textContent.trim(),
          sources: [...document.querySelectorAll('[data-testid^="cc-source-"]')].map((b) => b.value),
          sourceKinds: [...document.querySelectorAll('.cc-source')].map((l) => l.querySelector('.cc-source-kind')?.textContent.trim() || null),
          tables: [...document.querySelectorAll('#cc-table option')].map((o) => o.value),
          prepare: document.querySelector('[data-testid="cc-prepare"]')?.textContent.trim(),
          prepareDisabled: document.querySelector('[data-testid="cc-prepare"]')?.disabled,
          consentYet: !!document.querySelector('[data-testid="cc-consent"]'),
          // The free step says so itself, and says where the reading happens.
          help: document.querySelector('.cc-step-help')?.textContent.trim(),
          privacy: document.querySelector('.cc-privacy')?.textContent.trim()
        };
      });
      assert.equal(picker.first, 'cc-picker', `${tag}: step 1 is the picker`);
      assert.equal(picker.free, 'Free — no model is called', `${tag}: step 1 says it is free, in those words`);
      assert.equal(picker.claimLabel, 'The claim', `${tag}: the claim field is labelled plainly`);
      assert.deepEqual(picker.sources, ['i1', 'n1', 'i2'],
        `${tag}: the picker offers every non-table item — PDFs and notes alike`);
      assert.deepEqual(picker.sourceKinds, [null, 'note', null],
        `${tag}: a note is marked as one in the source list`);
      assert.deepEqual(picker.tables, ['', 't1'],
        `${tag}: a results table is offered only in the table select, never as a source`);
      assert.equal(picker.prepare, 'Preview what will be sent', `${tag}: the primary action previews, it does not send`);
      assert.equal(picker.prepareDisabled, true, `${tag}: nothing to preview until a claim is entered`);
      assert.equal(picker.consentYet, false, `${tag}: there is no consent step before a preview exists`);
      assert.match(picker.help, /only reads what is already in the project/i, `${tag}: step 1 explains that it only reads`);
      assert.match(picker.privacy, /Nothing is sent anywhere until you approve/i, `${tag}: and that nothing is sent yet`);

      // The lede states both halves: verbatim checking, and whose judgement it is.
      const lede = await text(page, '.cc-lede');
      assert.match(lede, /checks the quotes word for word/i, `${tag}: the screen says the quotes are checked word for word`);
      assert.match(lede, /judgement of whether the claim is true stays with you/i,
        `${tag}: and that the truth of the claim is not decided here`);

      await prepareWith(page, tag);

      // The prepare request carried exactly what the screen showed.
      assert.equal(scene.prepared, 1, `${tag}: one prepare, on the click only`);
      assert.deepEqual(scene.lastPrepare.claim_text, CLAIM, `${tag}: the claim sent is the claim typed`);
      assert.deepEqual(scene.lastPrepare.source_item_ids, ['i1'], `${tag}: only the ticked source is sent`);
      assert.equal(scene.executed, 0, `${tag}: preparing sent nothing to a model`);

      // --- G: the numbers, from ordinary code ------------------------------
      const numbers = await page.evaluate(() => ({
        card: !!document.querySelector('[data-testid="cc-numeric"]'),
        rows: [...document.querySelectorAll('[data-testid="cc-number"]')].map((li) => ({
          value: li.querySelector('.cc-number-value')?.textContent.trim(),
          status: li.querySelector('.cc-number-status')?.textContent.trim(),
          cls: li.className
        })),
        help: document.querySelector('[data-testid="cc-numeric"] .cc-step-help')?.textContent.trim()
      }));
      assert.equal(numbers.card, true, `${tag}: the numeric reconciliation appears without a model call`);
      assert.deepEqual(numbers.rows.map((r) => r.value), ['42', '1998'], `${tag}: every number in the claim is listed`);
      assert.equal(numbers.rows[0].status, 'Appears in the sources', `${tag}: a matching number says so`);
      assert.equal(numbers.rows[1].status, 'Not found in the sources', `${tag}: a missing number says so`);
      assert.match(numbers.rows[0].cls, /cc-match/, `${tag}: a match is marked as one`);
      assert.match(numbers.rows[1].cls, /cc-not_found/, `${tag}: a miss is marked as one`);
      assert.match(numbers.help, /does not by itself make the claim true/i,
        `${tag}: the numbers are not oversold`);

      // --- H: the consent preview ------------------------------------------
      const consent = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="cc-consent"]');
        const fact = (id) => card.querySelector(`[data-testid="${id}"]`)?.textContent.trim() || null;
        return {
          title: card.querySelector('.cc-step-title')?.textContent.trim(),
          destination: fact('cc-destination'), model: fact('cc-model'), cost: fact('cc-cost'),
          maxOutput: fact('cc-max-output'), store: fact('cc-store'), tools: fact('cc-tools'),
          language: fact('cc-language'),
          claim: card.querySelector('.cc-quote-claim')?.textContent.trim(),
          excerpts: [...card.querySelectorAll('.cc-excerpt')].map((p) => ({
            page: p.querySelector('.cc-page')?.textContent.trim(),
            cut: !!p.querySelector('.cc-truncated')
          })),
          sources: [...card.querySelectorAll('[data-testid="cc-sent-source"] h4')].map((h) => h.textContent.trim()),
          execute: card.querySelector('[data-testid="cc-execute"]')?.textContent.trim(),
          warning: card.querySelector('[data-testid="cc-blocked"]')?.textContent.trim() || null,
          plan: [...card.querySelectorAll('.cc-note')].map((n) => n.textContent.trim()).join(' ')
        };
      });
      assert.equal(consent.title, 'Exactly what will be sent', `${tag}: step 3 is titled as the exact transmission`);
      assert.equal(consent.destination, 'fixture provider (synthetic, no network)', `${tag}: the destination is named`);
      assert.equal(consent.model, 'fixture-provider · fixture-model-1', `${tag}: the model and provider are named`);
      assert.equal(consent.cost, '$0.4200', `${tag}: the maximum reserved cost is shown`);
      assert.equal(consent.maxOutput, '1200', `${tag}: the output allowance is shown`);
      assert.equal(consent.store, 'no', `${tag}: whether the provider keeps a copy is shown`);
      assert.equal(consent.tools, 'none', `${tag}: the tools offered are shown`);
      assert.equal(consent.language, 'English',
        `${tag}: the language the note will be written in is shown before consent`);
      // It is not merely displayed: the same value is what was sent, so a reader
      // consents to the request that is actually made.
      assert.equal(scene.lastPrepare && scene.lastPrepare.language, 'en',
        `${tag}: and it is the language actually sent on prepare`);
      assert.equal(consent.claim, CLAIM, `${tag}: the claim is shown word for word`);
      assert.deepEqual(consent.excerpts,
        [{ page: 'p. 1', cut: false }, { page: 'p. 2', cut: true }, { page: 'p. 1', cut: false }],
        `${tag}: every page sent is shown, and a cut excerpt says it was cut`);
      assert.deepEqual(consent.sources, ['Synthetic source A · 2 page(s)', 'Synthetic source B · 1 page(s)'],
        `${tag}: each source is named with its page count`);
      assert.equal(consent.execute, 'Send this and check it', `${tag}: the one send action is explicit`);
      assert.equal(consent.warning, null, `${tag}: nothing blocks a ready account`);
      assert.match(consent.plan, /checked verbatim/, `${tag}: the verification plan is stated before paying`);
      // The plan's thresholds come from the response's own numbers, never from
      // parsing the English sentence.
      assert.match(consent.plan, /at least 5 words, or at least 20 non-space characters/,
        `${tag}: the thresholds in the plan are the numbers the response carries`);
      // The pricing note is narrated from its code, so it is a sentence a reader
      // gets in their own language rather than the backend's English.
      assert.match(consent.plan, /Conservative reservation, not a predicted bill/,
        `${tag}: the pricing note is said from its code`);

      // The step that just appeared has to be where a person is looking.
      assert.deepEqual(await onScreen(page, '[data-testid="cc-consent"]'),
        { found: true, visible: true, sheet: true, window: true },
        `${tag}: the preview appears on screen instead of below the sheet's fold`);

      assert.equal(await count(page, '[data-testid="cc-execute"]'), 1, `${tag}: exactly one execute control`);
      assert.equal(scene.executed, 0, `${tag}: looking at the preview runs nothing`);

      // --- I: the verdict ---------------------------------------------------
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));
      assert.equal(scene.executed, 1, `${tag}: one execute, and only after the click`);
      assert.deepEqual(scene.lastExecute, { preview_hash: 'a'.repeat(64), consent: true },
        `${tag}: execute carries the preview hash and the explicit consent`);

      assert.deepEqual(await onScreen(page, '[data-testid="cc-secretary-note"]'),
        { found: true, visible: true, sheet: true, window: true },
        `${tag}: the verdict is on screen when the check returns`);

      const verdict = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="cc-result"]');
        const quotes = [...card.querySelectorAll('[data-testid="cc-quote"]')].map((li) => ({
          verified: li.getAttribute('data-verified'),
          cls: li.className,
          mark: li.querySelector('.cc-quote-mark')?.textContent.trim(),
          reason: li.querySelector('.cc-quote-reason')?.textContent.trim() || null,
          crossPage: li.querySelector('.cc-quote-crosspage')?.textContent.trim() || null,
          // The order matters: the mark is read first, the note after.
          markFirst: li.firstElementChild === li.querySelector('.cc-quote-mark')
        }));
        const note = card.querySelector('[data-testid="cc-secretary-note"]');
        return {
          status: card.querySelector('[data-testid="cc-status"]')?.textContent.trim(),
          noteHead: note?.querySelector('.cc-sub')?.textContent.trim(),
          noteText: note?.querySelector('.cc-secretary-text')?.textContent.trim(),
          modelNote: card.querySelector('[data-testid="cc-model-note"]')?.textContent.trim() || null,
          modelNoteOpen: card.querySelector('[data-testid="cc-model-note"]')?.open ?? null,
          checkedHead: card.querySelector('[data-testid="cc-checked"] .cc-sub')?.textContent.trim(),
          leaning: card.querySelector('[data-testid="cc-leaning"]')?.textContent.trim(),
          closing: card.querySelector('.cc-closing')?.textContent.trim(),
          quotes,
          decide: !!card.querySelector('[data-testid="cc-decide"]')
        };
      });

      // The note leads, in the reader's language, in the secretary's voice.
      assert.equal(verdict.status, 'Checked', `${tag}: the badge names the status`);
      assert.equal(verdict.noteHead, "Secretary's note", `${tag}: the card is the secretary's note`);
      assert.match(verdict.noteText, /couldn't find the sentences I was going to quote/i,
        `${tag}: a downgraded check leads with the app's own sentence, not the model's note`);

      // The model's note is not withheld; it is secondary and collapsed.
      assert.equal(verdict.modelNoteOpen, false, `${tag}: the model's note is collapsed by default`);
      assert.match(verdict.modelNote, /What the model wrote/, `${tag}: and is labelled for what it is`);
      assert.match(verdict.modelNote, /I think the dates are recorded separately/,
        `${tag}: the stored note is still readable when opened`);

      assert.equal(verdict.checkedHead, 'What I checked', `${tag}: the checks have their own heading`);

      // A quote found in the sources says where it was found.
      assert.deepEqual(verdict.quotes[0], {
        verified: 'true', cls: 'cc-quote cc-quote-yes',
        mark: 'Found word for word in Synthetic source A, p.1',
        reason: null, crossPage: null, markFirst: true
      }, `${tag}: a matching quote says where it was found`);

      // The unverified quote that is also cross-page must still read unfound.
      const unverified = verdict.quotes[1];
      assert.equal(unverified.verified, 'false', `${tag}: the unmatched quote is marked unverified`);
      assert.equal(unverified.mark, "I couldn't find this sentence in your sources.",
        `${tag}: and says plainly that it was not found`);
      assert.equal(unverified.markFirst, true, `${tag}: the mark comes before any note`);
      assert.match(unverified.cls, /cc-quote-no/, `${tag}: it carries the failed style`);
      assert.ok(!/cc-quote-yes/.test(unverified.cls), `${tag}: and never the verified style`);
      assert.match(unverified.reason, /outside the part that was sent/i,
        `${tag}: the reason it did not count is stated`);
      assert.match(unverified.crossPage, /still couldn't find it as written/i,
        `${tag}: the page-break note does not soften it into a pass`);
      assert.ok(!/checked as one span/i.test(unverified.crossPage),
        `${tag}: a page-break note never reads as a pass`);

      // The verdict is a leaning, not a headline: a small tag, never a heading.
      assert.equal(verdict.leaning, 'Leans: partly supported',
        `${tag}: the verdict is phrased as a leaning`);
      assert.deepEqual(await page.evaluate(() => {
        const tag = document.querySelector('[data-testid="cc-leaning"]');
        const tagName = tag?.tagName.toLowerCase();
        return {
          // The element itself, not just its descendants: a tag that *is* a
          // heading is a headline whatever is inside it.
          selfHeading: /^h[1-6]$/.test(tagName),
          selfStrong: tagName === 'strong' || tagName === 'b',
          innerHeading: !!tag?.querySelector('h1,h2,h3,h4,h5,h6,strong,b'),
          // And it must come after the note, never before it.
          afterNote: !!(document.querySelector('[data-testid="cc-secretary-note"]')
            ?.compareDocumentPosition(tag) & Node.DOCUMENT_POSITION_FOLLOWING)
        };
      }), { selfHeading: false, selfStrong: false, innerHeading: false, afterNote: true },
        `${tag}: the leaning is a soft tag, not a heading, and follows the note`);

      // One quiet closing line in the secretary's voice, and no review banner.
      assert.match(verdict.closing, /you know this work best/i, `${tag}: the note ends in the secretary's voice`);
      assert.equal(await page.evaluate(() => ({
        human: !!document.querySelector('[data-testid="cc-human"]'),
        banner: !!document.querySelector('.cc-review-note'),
        raw: /\bHUMAN_REVIEW_REQUIRED\b/.test(document.body.textContent)
      })).then((r) => r.human || r.banner || r.raw), false,
        `${tag}: no human-review banner, no review paragraph, and no raw HUMAN_REVIEW_REQUIRED`);

      assert.equal(verdict.decide, true, `${tag}: a completed check offers the decision`);

      // --- the decision: override needs a reason ---------------------------
      await page.locator('[data-testid="cc-override"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-error"]'));
      assert.equal(await text(page, '[data-testid="cc-error"]'),
        'Tell me why you see it differently — the reason is required.',
        `${tag}: an override without a reason is refused, in the secretary's voice`);

      await page.locator('[data-testid="cc-decision-note"]').fill('The count is not in the transmitted excerpt.');
      await page.locator('[data-testid="cc-override"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-recorded"]'));
      const recorded = await text(page, '[data-testid="cc-recorded"]');
      assert.match(recorded, /You see it differently — your reason is on record\./,
        `${tag}: the decision is recorded as the person's act, in the secretary's voice`);
      assert.match(recorded, /The count is not in the transmitted excerpt\./, `${tag}: and the reason is kept with it`);
      assert.equal(await text(page, '[data-testid="cc-leaning"]'), 'Leans: partly supported',
        `${tag}: the leaning itself is never rewritten by the decision`);

      // --- the trust receipt -----------------------------------------------
      await page.locator('[data-testid="cc-receipt"]').click();
      await page.waitForFunction(() => document.getElementById('cc-receipt-panel'));
      const receipt = await page.evaluate(() => {
        const panel = document.getElementById('cc-receipt-panel');
        const box = panel.getBoundingClientRect();
        return {
          label: panel.getAttribute('aria-label'),
          outcome: panel.querySelector('[data-testid="cc-receipt-outcome"]')?.textContent.trim(),
          labels: [...panel.querySelectorAll('dt')].map((dt) => dt.textContent.trim()),
          facts: [...panel.querySelectorAll('dd')].map((dd) => dd.textContent.trim()),
          inView: box.left >= -1 && box.right <= innerWidth + 1 && box.bottom <= innerHeight + 1
        };
      });
      assert.equal(receipt.label, 'Trust receipt', `${tag}: the receipt is named for what it is`);
      assert.equal(receipt.outcome, 'Outcome: result_recorded', `${tag}: the receipt states the outcome`);
      assert.ok(receipt.facts.includes('verified'), `${tag}: the receipt carries the record's own integrity value`);
      assert.ok(receipt.inView, `${tag}: the receipt is fully on screen`);
      // Field names are said in words; the values stay the record's own.
      assert.deepEqual(receipt.labels,
        ['Job status', 'Outcome', 'Reservation kept (USD)', 'Model', 'Data use', 'Result integrity'],
        `${tag}: the receipt's fields are labelled, not printed as raw keys`);
      assert.ok(!receipt.labels.includes('job_status'), `${tag}: no raw backend key is shown as a label`);

      // Nothing here ever scrolls sideways, at either size.
      const overflow = await page.evaluate(() => {
        const section = document.querySelector('#app-shell > main > section');
        return { over: section.scrollWidth - section.clientWidth,
          doc: document.documentElement.scrollWidth - innerWidth };
      });
      assert.ok(overflow.over <= 1, `${tag}: the claim-check screen never scrolls sideways (${overflow.over}px)`);
      assert.ok(overflow.doc <= 1, `${tag}: the window never scrolls sideways (${overflow.doc}px)`);

      // --- the same screen in Korean ---------------------------------------
      // The backend wrote the note in Korean, so the record says 'ko' and the
      // fixture keeps that. The app's own sentences are translated either way.
      await page.evaluate(() => {
        WB_CLAIM_CHECK.state.check.result.language = 'ko';
        window.WB_I18N.set('ko');
      });
      const ko = await page.evaluate(() => ({
        title: document.querySelector('[data-testid="cc-title"]')?.textContent.trim(),
        prepare: document.querySelector('[data-testid="cc-prepare"]')?.textContent.trim(),
        noteHead: document.querySelector('[data-testid="cc-secretary-note"] .cc-sub')?.textContent.trim(),
        noteText: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        checkedHead: document.querySelector('[data-testid="cc-checked"] .cc-sub')?.textContent.trim(),
        leaning: document.querySelector('[data-testid="cc-leaning"]')?.textContent.trim(),
        closing: document.querySelector('.cc-closing')?.textContent.trim(),
        mark: [...document.querySelectorAll('.cc-quote-mark')].map((m) => m.textContent.trim()),
        status: document.querySelector('[data-testid="cc-status"]')?.textContent.trim()
      }));
      assert.equal(ko.title, '주장–근거 검증', `${tag}: the screen title is Korean after a live switch`);
      assert.equal(ko.prepare, '전송될 내용 미리보기', `${tag}: the primary action is Korean`);
      assert.equal(ko.status, '검증됨', `${tag}: the status badge is Korean`);
      assert.equal(ko.noteHead, '비서 메모', `${tag}: the note heading is Korean`);
      assert.equal(ko.checkedHead, '제가 확인한 것', `${tag}: the checks heading is Korean`);
      assert.match(ko.noteText, /인용하려던 문장을 보내주신 문헌에서 찾지 못했습니다/,
        `${tag}: the app's own note sentence is Korean, not the English original`);
      assert.equal(ko.leaning, '부분적으로 지지하는 쪽으로 보임', `${tag}: the leaning is Korean`);
      assert.match(ko.closing, /판단은 연구자님께 맡길게요/, `${tag}: the closing line is Korean`);
      assert.deepEqual(ko.mark, ['Synthetic source A, 1쪽에서 글자 그대로 찾았습니다', '이 문장은 보내주신 문헌에서 찾지 못했습니다.'],
        `${tag}: found and not-found stay distinct in Korean, and the mark is a sentence`);
      await page.evaluate(() => {
        WB_CLAIM_CHECK.state.check.result.language = 'en';
        window.WB_I18N.set('en');
      });
      assert.equal(await text(page, '[data-testid="cc-title"]'), 'Claim–evidence verification',
        `${tag}: switching back restores the English title`);

      // The Writing screen stops saying it has nothing once a check exists.
      assert.equal(await page.evaluate(() => {
        const box = document.querySelector('[data-testid="clarity-empty-writing"]');
        return box ? box.hidden : null;
      }), true, `${tag}: the Writing empty state gives way once a check is recorded`);

      assert.deepEqual(errors, [], `${tag}: no script error in the full flow`);
      await page.close();
    }

    // ================= cloud use off: no execute button at all ============
    {
      const { page, errors, scene } = await open(viewport, {
        prepare: preview({ cloud_enabled: false, account_ready: false })
      });
      // The table is what the free reconciliation compares against, and it is the
      // free route — not the refused preview — that answers with cloud use off.
      await page.locator('[data-testid="cc-table"]').selectOption('t1');
      await prepareWith(page, tag);
      const blocked = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="cc-consent"]');
        return {
          title: card.querySelector('.cc-step-title')?.textContent.trim(),
          text: card.querySelector('[data-testid="cc-blocked"]')?.textContent.trim(),
          role: card.querySelector('[data-testid="cc-blocked"]')?.getAttribute('role'),
          execute: !!card.querySelector('[data-testid="cc-execute"]'),
          anyButton: [...card.querySelectorAll('button')].map((b) => b.textContent.trim()),
          note: [...card.querySelectorAll('.cc-hint')].map((h) => h.textContent.trim()).join(' '),
          // The free numbers are ordinary code, so they survive the refusal.
          numbers: [...document.querySelectorAll('[data-testid="cc-number"]')]
            .map((li) => li.querySelector('.cc-number-value')?.textContent.trim())
        };
      });
      assert.equal(blocked.title, 'Exactly what will be sent',
        `${tag}: a ready preview keeps the transmission heading even when it is blocked`);
      assert.match(blocked.text, /Cloud use is off, so this check cannot run\./,
        `${tag}: cloud off is stated in the screen, not in a toast`);
      assert.match(blocked.text, /Turn cloud use on in Settings, and make sure an API key is saved/,
        `${tag}: and it says what to do about it`);
      assert.match(blocked.text, /Nothing was sent\./, `${tag}: and that nothing was sent`);
      assert.equal(blocked.role, 'status', `${tag}: the blocker is announced as a status`);
      assert.equal(blocked.execute, false, `${tag}: no execute button exists while cloud use is off`);
      assert.equal(blocked.anyButton.length, 0, `${tag}: and no disabled send button is left to puzzle over`);
      assert.match(blocked.note, /No request can be made until the reason above is resolved\./,
        `${tag}: the reason the action is absent is stated`);
      // The preview still shows what would have been sent.
      assert.equal(await count(page, '[data-testid="cc-sent-source"]'), 2,
        `${tag}: the preview still shows the exact content`);

      // The pricing note and the verification rule are narrated from the codes
      // the response carries, so the preview is a sentence in the reader's own
      // language with its numbers filled in — not the backend's English prose.
      // A language switch drops the standing preview (it promised a transmission
      // in the other language), so the Korean preview is the one asked for again
      // in Korean — which is also the only way the reader gets one.
      await page.evaluate(() => window.WB_I18N.set('ko'));
      assert.equal(await count(page, '[data-testid="cc-consent"]'), 0,
        `${tag}: a switch drops the preview prepared for English`);
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]')
        && document.querySelector('[data-testid="cc-language"]')?.textContent.trim() === '한국어',
        null, { timeout: 5000 });
      const ko = await page.evaluate(() => ({
        notes: [...document.querySelectorAll('[data-testid="cc-consent"] .cc-note')]
          .map((n) => n.textContent.trim()).join(' '),
        lead: document.querySelector('[data-testid="cc-blocked"] strong')?.textContent.trim(),
        execute: document.querySelector('[data-testid="cc-consent"] .cc-step-title')?.textContent.trim()
      }));
      assert.match(ko.notes, /보수적인 예약 금액/,
        `${tag}: the pricing note is Korean after a live switch`);
      assert.match(ko.notes, /5단어 이상/,
        `${tag}: the verification rule is Korean, with the response's thresholds filled in`);
      assert.ok(!/Conservative reservation|checked verbatim|at least \d+ words/.test(ko.notes),
        `${tag}: no English prose is left in the preview notes`);
      assert.equal(ko.lead, '클라우드 사용이 꺼져 있어 이 검증은 실행할 수 없습니다.',
        `${tag}: a blocked preview is Korean too`);
      await page.evaluate(() => window.WB_I18N.set('en'));
      assert.equal(await count(page, '[data-testid="cc-consent"]'), 0,
        `${tag}: switching back drops the Korean preview for the same reason`);
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-blocked"]')
        ?.textContent.includes('Cloud use is off'), null, { timeout: 5000 });
      assert.match(await text(page, '[data-testid="cc-blocked"]'),
        /Cloud use is off, so this check cannot run\./,
        `${tag}: a preview asked for again in English restores the English refusal`);

      assert.equal(scene.executed, 0, `${tag}: no execute request was made`);
      assert.deepEqual(errors, [], `${tag}: no script error with cloud use off`);
      await page.close();
    }

    // ================= a typed claim in a project that has a draft ==========
    // The backend refuses a request that carries a draft_id without the location
    // that draft_id points into — or with a location whose slice no longer equals
    // the claim. A typed claim is neither, so it must travel without a draft_id.
    {
      const { page, errors, scene } = await open(viewport, { draft: { ...DRAFT } });
      await page.waitForFunction(() => typeof latexState === 'object'
        && latexState && latexState.draft && latexState.draft.draft_id === 'd1');

      // (1) A claim typed by hand, with a draft open in the project.
      await prepareWith(page, tag);
      assert.equal(scene.lastPrepare.draft_id, undefined,
        `${tag}: a typed claim is prepared without a draft_id, even when a draft exists`);
      assert.equal(scene.lastPrepare.claim_location, undefined,
        `${tag}: and without a claim location it does not have`);

      // (2) A claim taken from a selection in the draft, then executed.
      await page.evaluate((sentence) => {
        const editor = document.getElementById('latex-editor');
        const start = editor.value.indexOf(sentence);
        editor.focus();
        editor.setSelectionRange(start, start + sentence.length);
      }, DRAFT_SENTENCE);
      await page.locator('[data-testid="cc-take-selection"]').click();
      await page.waitForFunction((sentence) => document.querySelector('[data-testid="cc-claim"]')
        && document.querySelector('[data-testid="cc-claim"]').value === sentence, DRAFT_SENTENCE);
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => {
        const state = WB_CLAIM_CHECK.state;
        return state.preview && !state.busy;
      });
      assert.equal(scene.lastPrepare.draft_id, 'd1',
        `${tag}: a claim taken from the draft carries the draft it came from`);
      assert.equal(scene.lastPrepare.claim_location.file, 'main.tex',
        `${tag}: and the location of the sentence in that file`);

      // (3) The same claim, edited by hand, is a typed claim again.
      const field = page.locator('[data-testid="cc-claim"]');
      await field.click();
      await field.type(' Edited.', { delay: 10 });
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => {
        const state = WB_CLAIM_CHECK.state;
        return state.preview && !state.busy;
      });
      assert.equal(scene.lastPrepare.draft_id, undefined,
        `${tag}: a manual edit after taking a sentence prepares as a typed claim`);
      assert.equal(scene.lastPrepare.claim_location, undefined,
        `${tag}: and drops the stale location with it`);
      assert.match(scene.lastPrepare.claim_text, / Edited\.$/,
        `${tag}: while sending the edited text itself`);

      assert.deepEqual(errors, [], `${tag}: no script error with a draft in the project`);
      await page.close();
    }

    // ================= the list is re-read on every opening ================
    // A check made from a typed claim has no draft to be filed under, and the
    // draft a result was computed against can change after the sheet is closed:
    // the staleness badge is only worth anything if the list is read again.
    {
      const { page, errors, scene } = await open(viewport);
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));
      assert.equal(await page.evaluate(() => WB_CLAIM_CHECK.count()), 1,
        `${tag}: the executed check joins the list`);

      await page.locator('[data-testid="cc-close"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-testid="cc-root"]'));

      // While the sheet is closed the draft changes, so the last check is stale.
      scene.checks = [completed({ check_id: 'cc-1', stale: true,
        stale_reasons: ['claim', 'source_missing:i1'] })];

      await page.locator('[data-testid="cc-open"]').click();
      // The list read on opening is a request, so what changes is the badge, not
      // the existence of the list: wait for the refresh to land.
      await page.waitForFunction(() => document.querySelector('.cc-history-row .cc-stale'));
      const refreshed = await page.evaluate(() => ({
        rows: document.querySelectorAll('.cc-history-row').length,
        badges: [...document.querySelectorAll('.cc-history-row .cc-badge')].map((b) => b.textContent.trim())
      }));
      assert.equal(refreshed.rows, 1, `${tag}: reopening the sheet reads the list again`);
      assert.ok(refreshed.badges.includes('stale'),
        `${tag}: and the badge shows what changed while the sheet was closed`);
      // The reason names the source by its title: the id in the reason is not a
      // name, and "source_missing:i1" tells a researcher nothing.
      await page.locator('.cc-history-row .cc-history-open').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-stale"]'));
      const banner = await text(page, '[data-testid="cc-stale"]');
      assert.match(banner, /a source this check used is gone — Synthetic source A/,
        `${tag}: a missing source is named by title, not by id`);
      assert.ok(!/source_missing|:i1/.test(banner),
        `${tag}: and no raw reason code or id is shown`);

      assert.deepEqual(errors, [], `${tag}: no script error while the list refreshes`);
      await page.close();
    }

    // ================= a typed check survives in the list ==================
    // The list is the project's checks, not one draft's. A typed claim is stored
    // with draft_id NULL, so a ?draft_id= query — which the mock now honours
    // exactly as the backend does — would answer with nothing, and the paid check
    // the person just ran would vanish while "No claims checked yet" stayed.
    {
      const { page, errors, scene } = await open(viewport, { draft: DRAFT });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      // The typed check is on the project's list and in the screen's own count.
      assert.equal(await page.evaluate(() => WB_CLAIM_CHECK.count()), 1,
        `${tag}: a typed check joins the list even while a draft is open`);
      const listed = await page.evaluate(() => ({
        rows: [...document.querySelectorAll('.cc-history-row')].map((li) => ({
          claim: li.querySelector('.cc-history-claim')?.textContent.trim(),
          from: li.querySelector('.cc-history-from')?.textContent.trim()
        }))
      }));
      assert.equal(listed.rows.length, 1,
        `${tag}: and it is drawn as a row, not left invisible behind a draft filter`);
      assert.equal(listed.rows[0].claim, CLAIM, `${tag}: the row is the claim that was checked`);
      // Both kinds live in one list, so each row says which it is.
      assert.equal(listed.rows[0].from, 'typed', `${tag}: and is labelled as a typed claim`);

      // A draft-bound check in the same list keeps its own label, so the mixed
      // list cannot read as if every row came from the draft.
      scene.checks = [completed({ check_id: 'cc-typed', draft_id: null }),
        completed({ check_id: 'cc-draft', draft_id: DRAFT.draft_id, claim_text: 'A claim selected in the draft.' })];
      await page.locator('[data-testid="cc-close"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-testid="cc-root"]'));
      await page.locator('[data-testid="cc-open"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.cc-history-row').length === 2);
      assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.cc-history-row')]
        .map((li) => li.querySelector('.cc-history-from')?.textContent.trim())), ['typed', 'from the draft'],
        `${tag}: a typed check and a draft check are both listed, each saying where it came from`);
      assert.equal(await page.evaluate(() => WB_CLAIM_CHECK.count()), 2,
        `${tag}: and the count is the rows the screen shows`);
      // count() is what clarity.js reads to decide whether the Writing screen
      // still has an empty state, so a count that disagreed with the rows would
      // leave "No claims checked yet" standing over a list of checks.
      assert.equal(await page.evaluate(() => {
        const box = document.querySelector('[data-testid="clarity-empty-writing"]');
        return box ? box.hidden : null;
      }), true, `${tag}: so the Writing empty state gives way to the list`);

      assert.deepEqual(errors, [], `${tag}: no script error while the mixed list is drawn`);
      await page.close();
    }

    // ================= an answer that arrives too late =====================
    // Consent is a statement about a specific text. A prepare that is still out
    // when the claim is edited answers about the old text, and showing it would
    // put a live execute button — and its charge — under a claim nobody approved.
    // The gate holds the answer for 1200 ms; the edit lands at 350 ms.
    {
      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      const { page, errors, scene } = await open(viewport, { prepareGate: gate });
      const field = page.locator('[data-testid="cc-claim"]');
      await field.click();
      await field.type(CLAIM, { delay: 10 });
      await page.locator('[data-testid="cc-source-i1"]').check();
      await page.locator('[data-testid="cc-prepare"]').click();
      // While the preview is out, the sentence changes under it.
      await page.waitForTimeout(350);
      await field.fill(REPLACED);
      release();
      // Give the answer time to arrive and be judged, then look at the screen.
      await page.waitForTimeout(400);

      const after = await page.evaluate(() => ({
        claim: document.querySelector('[data-testid="cc-claim"]')?.value,
        consent: !!document.querySelector('[data-testid="cc-consent"]'),
        execute: !!document.querySelector('[data-testid="cc-execute"]'),
        transmission: document.querySelector('[data-testid="cc-transmission"]')?.textContent.trim() || null
      }));
      assert.equal(after.claim, REPLACED, `${tag}: the edited claim is still what the field holds`);
      assert.equal(after.consent, false,
        `${tag}: an answer about the old claim draws no consent card`);
      assert.equal(after.execute, false,
        `${tag}: and no execute button, so the old text cannot be sent`);
      assert.equal(after.transmission, null,
        `${tag}: nothing describing the old text is left on screen`);

      // The screen is not stuck: preparing the text that is actually there works.
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]'));
      const fresh = await text(page, '[data-testid="cc-transmission"] .cc-quote-claim');
      assert.equal(fresh, REPLACED,
        `${tag}: a fresh preview describes the claim on screen, not the abandoned one`);

      assert.deepEqual(errors, [], `${tag}: no script error while the stale answer was dropped`);
      await page.close();
    }

    // ============ a refused send: no button, a reason, a way back =========
    // The cloud switch can be turned off between the preview and the send. That
    // refusal arrives as a plain 409 with English prose, and the execute button
    // must not survive it: leaving it there offers to send the same thing again.
    {
      const { page, errors, scene } = await open(viewport, { cloudEnabled: true });
      await prepareWith(page, tag);
      // Approved preview in hand; the switch is turned off before the send.
      scene.executeStatus = 409;
      scene.executeMessage = 'Cloud mode is off';
      scene.cloudEnabled = false;
      await page.locator('[data-testid="cc-execute"]').click();
      // Wait for the send to settle, whichever way it settles: the card, or the
      // raw error line the screen used to show instead. Waiting only for the card
      // would report this defect as a timeout rather than as the wrong sentence.
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-blocked"]')
        || document.querySelector('[data-testid="cc-error"]')
        || !document.querySelector('[data-testid="cc-execute"]'));

      const refused = await page.evaluate(() => ({
        execute: !!document.querySelector('[data-testid="cc-execute"]'),
        // The blocked card shares the cc-consent testid, so "no preview" is
        // checked where the preview's own content lives.
        transmission: !!document.querySelector('[data-testid="cc-transmission"]'),
        blocked: document.querySelector('[data-testid="cc-blocked"]')?.textContent.trim(),
        detail: document.querySelector('[data-testid="cc-blocked-detail"]')?.textContent.trim(),
        retry: !!document.querySelector('[data-testid="cc-prepare-again"]'),
        focus: document.activeElement?.getAttribute('data-testid')
      }));
      assert.equal(refused.execute, false, `${tag}: a refused send leaves no execute button`);
      assert.equal(refused.transmission, false, `${tag}: and no preview it could be sent from`);
      // The reason is ours, in the reader's language; the backend's sentence is
      // the detail beneath it, never the explanation.
      assert.match(refused.blocked, /Cloud use is off, so this check cannot run\./,
        `${tag}: the refusal is named by the localised cloud-off card`);
      assert.match(refused.blocked, /Turn cloud use on in Settings/,
        `${tag}: and says what to do about it`);
      assert.equal(refused.detail, 'Cloud mode is off',
        `${tag}: the backend's own sentence is kept as the secondary detail`);
      assert.equal(refused.retry, true, `${tag}: and there is a way to prepare again`);
      assert.equal(refused.focus, 'cc-blocked', `${tag}: the refusal takes the cursor`);

      // A refusal that is not the cloud switch gets its own lead, still ours.
      const other = await open(viewport, { cloudEnabled: true });
      await prepareWith(other.page, tag);
      other.scene.executeStatus = 409;
      other.scene.executeMessage = 'Local endpoint changed after preview; prepare again';
      await other.page.locator('[data-testid="cc-execute"]').click();
      await other.page.waitForFunction(() => document.querySelector('[data-testid="cc-blocked"]')
        || document.querySelector('[data-testid="cc-error"]')
        || !document.querySelector('[data-testid="cc-execute"]'));
      const otherRefused = await other.page.evaluate(() => ({
        execute: !!document.querySelector('[data-testid="cc-execute"]'),
        blocked: document.querySelector('[data-testid="cc-blocked"]')?.textContent.trim(),
        detail: document.querySelector('[data-testid="cc-blocked-detail"]')?.textContent.trim()
      }));
      assert.equal(otherRefused.execute, false, `${tag}: any refused send removes the execute button`);
      assert.match(otherRefused.blocked, /This check was refused, so nothing was sent\./,
        `${tag}: a refusal that is not the cloud switch still leads with a localised sentence`);
      assert.equal(otherRefused.detail, 'Local endpoint changed after preview; prepare again',
        `${tag}: and keeps the backend's sentence as the detail`);
      await other.page.close();

      assert.deepEqual(errors, [], `${tag}: no script error while the refusal was drawn`);
      await page.close();
    }

    // ============ a refused prepare: the code decides the card ============
    // The backend names the obstacle in a stable code next to its prose. The card
    // is built from the code; the prose is only ever the secondary detail under
    // it, in the reader's language as the lead.
    for (const [status, code, lead, hint] of [
      [409, 'cloud_off', 'Cloud use is off, so this check cannot run.',
        'Turn cloud use on in Settings, and make sure an API key is saved for that provider. Nothing was sent.'],
      [409, 'account_not_ready', 'This account is not ready for that model, so this check cannot run.',
        'Check the key and the model in the Secretary AI panel, then prepare the preview again. Nothing was sent.'],
      [409, 'local_not_ready', 'The local model is not ready, so this check cannot run.',
        'Start the local model server, then prepare the preview again. Nothing was sent, and no API cost was reserved.']
    ]) {
      const { page, errors, scene } = await open(viewport, {
        prepareStatus: status, prepareCode: code,
        prepareMessage: 'Backend prose that must not be the explanation.'
      });
      await page.locator('[data-testid="cc-table"]').selectOption('t1');
      await page.locator('[data-testid="cc-claim"]').click();
      await page.locator('[data-testid="cc-claim"]').type(CLAIM, { delay: 5 });
      await page.locator('[data-testid="cc-source-i1"]').check();
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-blocked"]'));
      // The free reconciliation is asked for on a keystroke timer and answered by
      // its own route, so it lands independently of the refusal.
      await page.waitForFunction(() => document.querySelectorAll('[data-testid="cc-number"]').length > 0);

      const card = await page.evaluate(() => {
        const box = document.querySelector('[data-testid="cc-consent"]');
        const blocked = box.querySelector('[data-testid="cc-blocked"]');
        return {
          title: box.querySelector('.cc-step-title')?.textContent.trim(),
          lead: blocked.querySelector('strong')?.textContent.trim(),
          body: blocked.querySelector('p:not(.cc-blocked-detail)')?.textContent.trim(),
          detail: blocked.querySelector('[data-testid="cc-blocked-detail"]')?.textContent.trim(),
          execute: !!box.querySelector('[data-testid="cc-execute"]'),
          buttons: [...box.querySelectorAll('button')].length,
          role: blocked.getAttribute('role'),
          focus: document.activeElement?.getAttribute('data-testid'),
          numbers: [...document.querySelectorAll('[data-testid="cc-number"]')]
            .map((li) => li.querySelector('.cc-number-value')?.textContent.trim())
        };
      });
      assert.equal(card.lead, lead, `${tag}: ${code} is explained by its code, in plain words`);
      assert.equal(card.body, hint, `${tag}: ${code} says what to do about it`);
      assert.match(card.detail, /Backend prose/,
        `${tag}: ${code} keeps the backend's sentence as a secondary detail`);
      assert.notEqual(card.lead, card.detail,
        `${tag}: ${code} never lets the backend's prose stand in for the explanation`);
      assert.equal(card.title, 'Why this cannot run',
        `${tag}: ${code} titles the card for what it is, not as a transmission`);
      assert.equal(card.execute, false, `${tag}: ${code} offers no execute button`);
      assert.equal(card.buttons, 0, `${tag}: ${code} offers no button at all`);
      assert.equal(card.role, 'status', `${tag}: ${code} is announced as a status`);
      // Item 5: the free numbers are shown even when the paid half is refused,
      // because they come from the free route and need no consent or cloud.
      assert.ok(scene.numbersCalls > 0,
        `${tag}: ${code} still asks the free numbers route`);
      assert.deepEqual(scene.lastNumbers, { claim_text: CLAIM, table_item_id: 't1' },
        `${tag}: the numbers route gets exactly the claim and the table`);
      assert.deepEqual(card.numbers, ['42', '1998'],
        `${tag}: ${code} still shows the free numeric reconciliation`);
      // The refusal is where a screen reader's cursor belongs, since the person
      // pressed a button and nothing else on the page changed.
      assert.equal(card.focus, 'cc-blocked',
        `${tag}: ${code} moves focus to the refusal`);
      assert.deepEqual(errors, [], `${tag}: no script error on a ${code} refusal`);
      await page.close();
    }

    // ============ feedback while a run is still out =======================
    // The preview stays on screen for the whole wait, so a button that still reads
    // "Send" and a silent page would look like nothing happened.
    {
      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      const { page, errors, scene } = await open(viewport, { executeGate: gate });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-execute"]')
        && document.querySelector('[data-testid="cc-execute"]').getAttribute('aria-busy') === 'true');
      const waiting = await page.evaluate(() => ({
        label: document.querySelector('[data-testid="cc-execute"]')?.textContent.trim(),
        busy: document.querySelector('[data-testid="cc-execute"]')?.getAttribute('aria-busy'),
        disabled: document.querySelector('[data-testid="cc-execute"]')?.disabled,
        status: document.querySelector('[data-testid="cc-running"]')?.textContent.trim(),
        statusRole: document.querySelector('[data-testid="cc-running"]')?.getAttribute('role'),
        statusLive: document.querySelector('[data-testid="cc-running"]')?.getAttribute('aria-live'),
        cardBusy: document.querySelector('[data-testid="cc-consent"]')?.getAttribute('aria-busy')
      }));
      assert.equal(waiting.label, 'Checking…', `${tag}: the send button says it is checking while the run is out`);
      assert.equal(waiting.busy, 'true', `${tag}: and marks itself busy`);
      assert.equal(waiting.disabled, true, `${tag}: and cannot be pressed twice`);
      assert.equal(waiting.status, 'Checking…', `${tag}: the same words are announced in a status line`);
      assert.equal(waiting.statusRole, 'status', `${tag}: which is a live region`);
      assert.equal(waiting.statusLive, 'polite', `${tag}: politely announced`);
      assert.equal(waiting.cardBusy, 'true', `${tag}: the preview card is marked busy too`);

      release();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));
      const after = await page.evaluate(() => ({
        heading: document.querySelector('#cc-result-heading')?.textContent.trim(),
        focus: document.activeElement?.id,
        live: document.querySelector('[data-testid="cc-result"]')?.getAttribute('aria-live'),
        busy: document.querySelector('[data-testid="cc-result"]')?.getAttribute('aria-busy')
      }));
      assert.equal(after.live, 'polite', `${tag}: the result is a polite live region`);
      assert.equal(after.busy, 'false', `${tag}: and stops being busy once it is written`);
      assert.equal(after.focus, 'cc-result-heading',
        `${tag}: focus moves to the result heading when the check completes`);
      assert.deepEqual(errors, [], `${tag}: no script error while the run was out`);
      await page.close();
    }

    {
      // The project already holds these three checks, so they arrive with the
      // list the screen reads when the project opens.
      const { page, errors, scene } = await open(viewport, { checks: HISTORY, receipt: RECEIPT_INTERRUPTED });
      await page.waitForFunction(() => document.querySelectorAll('.cc-history-row').length === 3);
      const rows = await page.evaluate(() => [...document.querySelectorAll('.cc-history-row')].map((li) => ({
        claim: li.querySelector('.cc-history-claim')?.textContent.trim(),
        badges: [...li.querySelectorAll('.cc-badge')].map((b) => b.textContent.trim())
      })));
      assert.equal(rows.length, 3, `${tag}: every earlier check is listed`);
      assert.ok(rows[0].badges.includes('stale'), `${tag}: a stale check is badged stale in the list`);
      assert.ok(rows[1].badges.includes('Interrupted — charge unknown'),
        `${tag}: an interrupted check is badged with the unknown charge`);
      assert.ok(rows[2].badges.includes('Failed'),
        `${tag}: an over-reservation run is badged as failed`);

      // --- stale ------------------------------------------------------------
      await page.locator('.cc-history-row').nth(0).locator('.cc-history-open').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-stale"]'));
      assert.deepEqual(await onScreen(page, '[data-testid="cc-stale"]'),
        { found: true, visible: true, sheet: true, window: true },
        `${tag}: opening an earlier check brings it on screen`);
      const stale = await page.evaluate(() => ({
        badge: document.querySelector('[data-testid="cc-status"]')?.textContent.trim(),
        banner: document.querySelector('[data-testid="cc-stale"]')?.textContent.trim(),
        verdict: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        quotes: document.querySelectorAll('[data-testid="cc-quote"]').length,
        decide: !!document.querySelector('[data-testid="cc-decide"]')
      }));
      assert.equal(stale.badge, 'Checked', `${tag}: a stale result keeps its honest status`);
      assert.match(stale.banner, /This result is stale\./, `${tag}: and is marked stale in words`);
      assert.match(stale.banner, /your sentence changed after this check/, `${tag}: the first reason is named`);
      assert.match(stale.banner, /a source changed after this check/, `${tag}: and the second`);
      assert.match(stale.banner, /describes the older text, not what is on screen now/,
        `${tag}: and it says the verdict is about the older text`);
      assert.equal(stale.quotes, 2, `${tag}: the older verdict is still readable`);
      assert.equal(stale.decide, true, `${tag}: a stale result can still be decided on`);

      // --- interrupted, charge unknown --------------------------------------
      await page.locator('.cc-history-row').nth(1).locator('.cc-history-open').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-terminal"]'));
      assert.deepEqual(await onScreen(page, '[data-testid="cc-terminal"]'),
        { found: true, visible: true, sheet: true, window: true },
        `${tag}: an interrupted run is on screen, not buried in the list`);
      const interrupted = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="cc-result"]');
        return {
          badge: card.querySelector('[data-testid="cc-status"]')?.textContent.trim(),
          head: card.querySelector('[data-testid="cc-terminal"] .cc-sub')?.textContent.trim(),
          body: card.querySelector('[data-testid="cc-terminal"] .cc-note')?.textContent.trim(),
          code: card.querySelector('.cc-code')?.textContent.trim(),
          decide: !!card.querySelector('[data-testid="cc-decide"]'),
          accept: !!card.querySelector('[data-testid="cc-accept"]'),
          override: !!card.querySelector('[data-testid="cc-override"]'),
          kept: [...card.querySelectorAll('.cc-note')].map((n) => n.textContent.trim()).join(' ')
        };
      });
      assert.equal(interrupted.badge, 'Interrupted — charge unknown',
        `${tag}: an interrupted run says the charge is unknown`);
      assert.equal(interrupted.head, 'Interrupted while running — the charge is unknown',
        `${tag}: the error code is named in plain words`);
      assert.match(interrupted.body, /charge is unknown and the reservation is kept/,
        `${tag}: and it says the reservation is kept`);
      assert.equal(interrupted.code, 'Code: interrupted_unknown_charge',
        `${tag}: the receipt's code is shown as the backend named it`);
      assert.equal(interrupted.decide, false, `${tag}: an interrupted run offers no decision`);
      assert.equal(interrupted.accept, false, `${tag}: no accept button`);
      assert.equal(interrupted.override, false, `${tag}: no override button`);
      assert.match(interrupted.kept, /nothing to accept or override/i,
        `${tag}: and the screen says why there is nothing to do`);

      // The charge-unknown runs have no verdict to accept, but their receipt is
      // where error_code names what happened, so it must still be reachable.
      await page.locator('[data-testid="cc-receipt"]').click();
      await page.waitForFunction(() => document.getElementById('cc-receipt-panel')
        && document.querySelector('[data-testid="cc-receipt-code"]'));
      const unknownReceipt = await page.evaluate(() => {
        const panel = document.getElementById('cc-receipt-panel');
        return {
          code: panel.querySelector('[data-testid="cc-receipt-code"]')?.textContent.trim(),
          label: panel.querySelector('[data-testid="cc-receipt-code"]')?.previousElementSibling?.textContent.trim(),
          text: panel.querySelector('[data-testid="cc-receipt-errortext"]')?.textContent.trim()
        };
      });
      assert.equal(unknownReceipt.code, 'interrupted_unknown_charge',
        `${tag}: an interrupted receipt keeps the error code that names the unknown charge`);
      assert.equal(unknownReceipt.label, 'Error code', `${tag}: and labels it as such`);
      assert.match(unknownReceipt.text, /Interrupted while running — the charge is unknown/,
        `${tag}: the code is also said in plain words on the receipt`);
      await page.locator('[data-testid="cc-receipt-close"]').click();
      assert.equal(await count(page, '#cc-receipt-panel'), 0, `${tag}: the receipt closes`);

      // --- over the reservation ---------------------------------------------
      await page.locator('.cc-history-row').nth(2).locator('.cc-history-open').click();
      await page.waitForFunction(() => document.querySelector('.cc-code')
        && document.querySelector('.cc-code').textContent.includes('usage_exceeded_reservation'));
      assert.deepEqual(await onScreen(page, '.cc-code'),
        { found: true, visible: true, sheet: true, window: true },
        `${tag}: an over-reservation run is on screen with its code`);
      const over = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="cc-result"]');
        return {
          badge: card.querySelector('[data-testid="cc-status"]')?.textContent.trim(),
          head: card.querySelector('[data-testid="cc-terminal"] .cc-sub')?.textContent.trim(),
          body: card.querySelector('[data-testid="cc-terminal"] .cc-note')?.textContent.trim(),
          code: card.querySelector('.cc-code')?.textContent.trim(),
          decide: !!card.querySelector('[data-testid="cc-decide"]'),
          kept: [...card.querySelectorAll('.cc-note')].map((n) => n.textContent.trim()).join(' ')
        };
      });
      assert.equal(over.badge, 'Failed', `${tag}: an over-reservation run is a failure, not a verdict`);
      assert.equal(over.head, 'Stopped: the provider used more than the reserved maximum',
        `${tag}: the over-reservation code is named in plain words`);
      assert.match(over.body, /cost more than the amount the preview reserved/,
        `${tag}: and it says what was exceeded`);
      assert.match(over.body, /Nothing was retried automatically\./, `${tag}: and that nothing was retried`);
      assert.equal(over.code, 'Code: usage_exceeded_reservation',
        `${tag}: the exact code is shown`);
      assert.equal(over.decide, false, `${tag}: a failed run offers no decision`);
      assert.match(over.kept, /keeps its reservation/, `${tag}: and the reservation is said to be kept`);

      assert.equal(scene.executed, 0, `${tag}: reading past checks sent nothing`);
      assert.deepEqual(errors, [], `${tag}: no script error in any end state`);
      await page.close();
    }

    // ============ a paper switch while a run is still out =================
    // The answer belongs to the paper it was asked for. Painting it in the paper
    // the reader has since opened would show one paper's verdict — and a live
    // Accept button — inside another paper, where it is not a fact about anything
    // on screen.
    {
      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      const { page, errors, scene } = await open(viewport, { executeGate: gate });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-execute"]')
        && document.querySelector('[data-testid="cc-execute"]').getAttribute('aria-busy') === 'true');

      // The reader leaves for the other paper while the run is still out, and
      // goes to its Writing surface — which is where the sheet lives, so this is
      // the screen that must be inspected, not a Home that paints nothing.
      await page.evaluate(() => openProject('p2', 'Synthetic second study'));
      await page.waitForFunction(() => S.project === 'p2');
      await page.evaluate(() => openWritingView());
      // The screen itself has to know about the switch, not just the app state.
      await page.waitForFunction(() => WB_CLAIM_CHECK.state.project === 'p2');
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-root"]'));
      assert.equal(scene.executed, 1, `${tag}: the run was asked for once, in the first paper`);

      const during = await page.evaluate(() => ({
        verdict: !!document.querySelector('[data-testid="cc-secretary-note"]'),
        accept: !!document.querySelector('[data-testid="cc-accept"]'),
        decide: !!document.querySelector('[data-testid="cc-decide"]'),
        error: !!document.querySelector('[data-testid="cc-error"]'),
        terminal: !!document.querySelector('[data-testid="cc-terminal"]')
      }));
      assert.equal(during.verdict, false, `${tag}: no verdict from the other paper appears while the run is out`);
      assert.equal(during.accept, false, `${tag}: and no Accept button for a claim this paper never sent`);
      assert.equal(during.decide, false, `${tag}: and no decision controls`);
      assert.equal(during.error, false, `${tag}: and nothing is reported as an error here either`);

      // The answer now lands, in the paper nobody is looking at any more.
      release();
      await page.waitForFunction(() => WB_CLAIM_CHECK.state.busy === null);
      await page.waitForTimeout(200);

      const after = await page.evaluate(() => {
        const box = document.querySelector('[data-testid="clarity-empty-writing"]');
        const r = box ? box.getBoundingClientRect() : null;
        return {
          project: S.project,
          verdict: !!document.querySelector('[data-testid="cc-secretary-note"]'),
          accept: !!document.querySelector('[data-testid="cc-accept"]'),
          decide: !!document.querySelector('[data-testid="cc-decide"]'),
          error: !!document.querySelector('[data-testid="cc-error"]'),
          terminal: !!document.querySelector('[data-testid="cc-terminal"]'),
          rows: document.querySelectorAll('.cc-history-row').length,
          count: WB_CLAIM_CHECK.count(),
          empty: box ? !box.hidden && r.height > 0 : null,
          errorText: document.querySelector('[data-testid="cc-error"]')?.textContent.trim() || null
        };
      });
      assert.equal(after.project, 'p2', `${tag}: the reader is still in the second paper`);
      assert.equal(after.verdict, false,
        `${tag}: the first paper's verdict is not painted into the second, even after the answer landed`);
      assert.equal(after.accept, false, `${tag}: and no live Accept button comes with it`);
      assert.equal(after.decide, false, `${tag}: and no decision controls`);
      assert.equal(after.error, false,
        `${tag}: the answer is dropped silently, not turned into an error in the wrong paper`);
      assert.equal(after.terminal, false, `${tag}: and no failure card from the other paper`);
      assert.equal(after.count, 0, `${tag}: the second paper has no checks`);
      assert.equal(after.rows, 0, `${tag}: and lists none`);
      // count() and the empty state are two statements about the same list, so
      // they have to agree: a count of zero with an empty state shown.
      assert.equal(after.empty, true,
        `${tag}: the empty state is shown, agreeing with count() === 0`);

      // Back in the first paper, the run that completed is the reader's to see —
      // the drop above kept it out of the wrong sheet, it did not lose it.
      await backToWriting(page, 'p1', 'Synthetic claim study');
      await page.waitForFunction(() => document.querySelectorAll('.cc-history-row').length === 1);
      const back = await page.evaluate(() => ({
        rows: [...document.querySelectorAll('.cc-history-row')].map((li) => ({
          claim: li.querySelector('.cc-history-claim')?.textContent.trim(),
          from: li.querySelector('.cc-history-from')?.textContent.trim()
        })),
        count: WB_CLAIM_CHECK.count()
      }));
      assert.deepEqual(back.rows, [{ claim: CLAIM, from: 'typed' }],
        `${tag}: returning to the first paper lists the check that completed while away`);
      assert.equal(back.count, 1, `${tag}: and count() agrees with the one row shown`);
      assert.deepEqual(errors, [], `${tag}: no script error across the paper switch`);
      await page.close();
    }

    // ============ a paper switch while a decision is still out =============
    // The decision is recorded in the paper that owned the check. A late answer
    // must not paint "You accepted this verdict" over a paper that has nothing to
    // do with it.
    {
      const { page, errors, scene } = await open(viewport, { execute: completed() });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      scene.decideGate = gate;
      await page.locator('[data-testid="cc-accept"]').click();
      await page.waitForFunction(() => WB_CLAIM_CHECK.state.busy === 'decide');

      await page.evaluate(() => openProject('p2', 'Synthetic second study'));
      await page.waitForFunction(() => S.project === 'p2');
      await page.evaluate(() => openWritingView());
      await page.waitForFunction(() => WB_CLAIM_CHECK.state.project === 'p2');
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-root"]'));

      const during = await page.evaluate(() => ({
        recorded: !!document.querySelector('[data-testid="cc-recorded"]'),
        accept: !!document.querySelector('[data-testid="cc-accept"]'),
        error: !!document.querySelector('[data-testid="cc-error"]')
      }));
      assert.equal(during.recorded, false, `${tag}: no decision from the other paper appears while it is out`);
      assert.equal(during.accept, false, `${tag}: and no Accept button for a check this paper does not have`);

      release();
      await page.waitForFunction(() => WB_CLAIM_CHECK.state.busy === null);
      await page.waitForTimeout(200);

      const after = await page.evaluate(() => {
        const box = document.querySelector('[data-testid="clarity-empty-writing"]');
        const r = box ? box.getBoundingClientRect() : null;
        return {
          project: S.project,
          recorded: !!document.querySelector('[data-testid="cc-recorded"]'),
          accept: !!document.querySelector('[data-testid="cc-accept"]'),
          verdict: !!document.querySelector('[data-testid="cc-secretary-note"]'),
          error: !!document.querySelector('[data-testid="cc-error"]'),
          count: WB_CLAIM_CHECK.count(),
          empty: box ? !box.hidden && r.height > 0 : null
        };
      });
      assert.equal(after.recorded, false,
        `${tag}: the recorded decision is not painted into the second paper`);
      assert.equal(after.accept, false, `${tag}: and no Accept button appears there`);
      assert.equal(after.verdict, false, `${tag}: and no verdict from the other paper`);
      assert.equal(after.error, false, `${tag}: the late answer is dropped, not reported as an error here`);
      assert.equal(after.count, 0, `${tag}: the second paper still has no checks`);
      assert.equal(after.empty, true, `${tag}: and its empty state agrees with the count`);

      // Returning to the first paper, the decision was recorded where it belonged.
      await backToWriting(page, 'p1', 'Synthetic claim study');
      await page.waitForFunction(() => document.querySelectorAll('.cc-history-row').length === 1);
      const badges = await page.evaluate(() => [...document.querySelectorAll('.cc-history-row')[0]
        .querySelectorAll('.cc-badge')].map((b) => b.textContent.trim()));
      assert.ok(badges.includes('accepted'),
        `${tag}: the first paper lists the check with the decision that was recorded for it`);
      assert.deepEqual(errors, [], `${tag}: no script error across the paper switch`);
      await page.close();
    }

    // ============ the other two note codes, in the secretary's voice ======
    // `quotes_not_found_in_sources` is covered above. The backend names two more,
    // and each renders differently: one keeps the model's note and adds the
    // numbers line, the other composes a note because there was none.
    {
      // --- numbers_disagree: the model's note leads, the numbers line follows.
      const disagree = completed();
      disagree.check_id = 'cc-numbers';
      disagree.result = {
        ...disagree.result,
        secretary_note: 'I think the dates are recorded separately, so I would call this mostly there. The count is the part I am not sure about.',
        secretary_note_shown: true,
        note_code: 'numbers_disagree',
        numeric: {
          numbers: [
            { text: '2,300', status: 'near_mismatch', nearest: { value: 2312, cell: '2,312', row: 1, col: 1 } },
            { text: '12', status: 'match', nearest: null }
          ]
        }
      };
      const { page, errors } = await open(viewport, { execute: disagree });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      const num = await page.evaluate(() => ({
        note: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        numbers: document.querySelector('.cc-secretary-numbers')?.textContent.trim() || null,
        // Shown: true means the model's own note leads, with no fallback detail.
        modelNote: !!document.querySelector('[data-testid="cc-model-note"]')
      }));
      assert.match(num.note, /mostly there/i,
        `${tag}: numbers_disagree keeps the model's own note as the main text`);
      assert.equal(num.modelNote, false,
        `${tag}: and does not also offer it as a collapsed detail — it is already the text`);
      assert.equal(num.numbers,
        "One thing: the 2,300 in your sentence doesn't quite match the table (2,312).",
        `${tag}: and adds the numbers line, built from the near-mismatch entry`);
      // The Korean numbers line must not staple a bracketed particle gloss onto
      // the number ("10300은(는)"): the particle belongs on a fixed noun, so every
      // number reads correctly. 10300 is the reviewer's own case — its last digit
      // is 0, and a digit-driven 은/는 choice is exactly what is being ruled out.
      await page.evaluate(() => {
        WB_CLAIM_CHECK.state.check.result.numeric.numbers[0].text = '10300';
        window.WB_I18N.set('ko');
      });
      const koNumbers = await text(page, '.cc-secretary-numbers');
      assert.equal(koNumbers, '한 가지, 문장에 쓴 숫자(10300)가 표의 값(2,312)과 조금 다릅니다.',
        `${tag}: the Korean numbers line resolves the particle on a fixed noun, never as "10300은(는)"`);
      assert.equal(/은\(는\)|는\(은\)/.test(koNumbers), false,
        `${tag}: and carries no bracketed particle gloss at all`);
      await page.evaluate(() => window.WB_I18N.set('en'));
      assert.deepEqual(errors, [], `${tag}: no script error on the numbers_disagree card`);
      await page.close();
    }

    {
      // --- no_note: nothing usable came back, so a short hedged note is composed.
      const empty = completed();
      empty.check_id = 'cc-nonote';
      empty.result = {
        ...empty.result,
        secretary_note: null,
        secretary_note_shown: false,
        note_code: 'no_note'
      };
      const { page, errors } = await open(viewport, { execute: empty });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      const none = await page.evaluate(() => ({
        note: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        modelNote: !!document.querySelector('[data-testid="cc-model-note"]'),
        // Even with no note, the leaning and the closing line are still there.
        leaning: document.querySelector('[data-testid="cc-leaning"]')?.textContent.trim(),
        closing: document.querySelector('.cc-closing')?.textContent.trim()
      }));
      assert.equal(none.note,
        "I looked at this one but I don't have much to add beyond the checks below — nothing here stands out to me either way.",
        `${tag}: no_note composes a short hedged note rather than leaving the card blank`);
      assert.equal(none.modelNote, false,
        `${tag}: and offers no "what the model wrote" detail, because there is nothing to show`);
      assert.equal(none.leaning, 'Leans: partly supported',
        `${tag}: the composed note still carries the leaning`);
      assert.match(none.closing, /you know this work best/i,
        `${tag}: and still ends in the secretary's voice`);
      assert.deepEqual(errors, [], `${tag}: no script error on the no_note card`);
      await page.close();
    }

    {
      // --- nothing_found_in_sources: the sources do not speak to the claim.
      // Distinct from a quote that failed to match: no quote was verified, and the
      // sentence has to say the reading found nothing — with the model's note, if
      // one was stored, only in the collapsed detail.
      const nothing = completed();
      nothing.check_id = 'cc-nothing';
      nothing.result = {
        ...nothing.result,
        verdict: 'not_found',
        model_verdict: 'not_found',
        downgraded: false,
        secretary_note: 'Nothing in the two pages you sent appears to mention the intervention date, so I could not check this one.',
        secretary_note_shown: false,
        note_code: 'nothing_found_in_sources',
        verified_quote_count: 0,
        quotes: []
      };
      const { page, errors } = await open(viewport, { execute: nothing });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      const notFound = await page.evaluate(() => ({
        note: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        modelNoteOpen: document.querySelector('[data-testid="cc-model-note"]')?.open ?? null,
        modelNote: document.querySelector('[data-testid="cc-model-note"]')?.textContent.trim() || null,
        leaning: document.querySelector('[data-testid="cc-leaning"]')?.textContent.trim()
      }));
      assert.equal(notFound.note,
        "I looked through the sources you picked but couldn't find anything that speaks to this claim. Want to try adding another source?",
        `${tag}: nothing_found_in_sources gets its own sentence, not the quote-mismatch one`);
      assert.equal(notFound.modelNoteOpen, false,
        `${tag}: and the stored model note is collapsed, never the main text`);
      assert.match(notFound.modelNote, /appears to mention the intervention date/,
        `${tag}: the model's note is still readable in the detail`);
      assert.equal(notFound.leaning, 'Leans: not addressed',
        `${tag}: a not-found check leans "not addressed"`);
      // The empty-checks sentence belongs to the checks section, not to the
      // collapsed detail above it — bare, it read as the detail's body.
      assert.deepEqual(await page.evaluate(() => {
        const p = [...document.querySelectorAll('[data-testid="cc-result"] p')]
          .find((x) => /came back without any quotes/.test(x.textContent));
        const section = p?.closest('[data-testid="cc-checked"]');
        const heading = section?.querySelector('.cc-sub')?.textContent.trim();
        const inDetail = !!p?.closest('[data-testid="cc-model-note"]');
        return { heading: heading || null, inDetail };
      }), { heading: 'What I checked', inDetail: false },
        `${tag}: the no-quotes line sits under "What I checked", not under the model's detail`);
      assert.deepEqual(errors, [], `${tag}: no script error on the nothing_found card`);
      await page.close();
    }

    {
      // --- the rule for every code: shown:false never lets the model's note
      // lead, even for a code this build has no sentence for. This is the case a
      // future backend code would hit, and the one the "fall back to the note"
      // shortcut would silently violate.
      const unknown = completed();
      unknown.check_id = 'cc-unknown-code';
      unknown.result = {
        ...unknown.result,
        secretary_note: 'MODELLED NOTE THAT MUST NOT LEAD.',
        secretary_note_shown: false,
        note_code: 'a_code_this_build_does_not_know'
      };
      const { page, errors } = await open(viewport, { execute: unknown });
      await prepareWith(page, tag);
      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));

      const lead = await page.evaluate(() => ({
        note: document.querySelector('.cc-secretary-text')?.textContent.trim(),
        detail: document.querySelector('[data-testid="cc-model-note"]')?.textContent.trim() || null,
        // Nothing outside the collapsed detail may carry the model's words.
        leaked: [...document.querySelectorAll('[data-testid="cc-result"] p')]
          .filter((p) => !p.closest('[data-testid="cc-model-note"]'))
          .some((p) => /MODELLED NOTE THAT MUST NOT LEAD/.test(p.textContent))
      }));
      assert.ok(!/MODELLED NOTE THAT MUST NOT LEAD/.test(lead.note),
        `${tag}: shown:false never lets the model's note be the main text`);
      assert.equal(lead.leaked, false,
        `${tag}: and no visible paragraph outside the detail carries it`);
      assert.match(lead.detail, /MODELLED NOTE THAT MUST NOT LEAD/,
        `${tag}: it is reachable only as the collapsed detail`);
      assert.deepEqual(errors, [], `${tag}: no script error on the unknown code card`);
      await page.close();
    }

    // ============ a language switch drops a preview prepared in the other ====
    // The preview is a promise about a specific transmission, and the note's
    // language is part of what is sent. Left standing across a switch, the card
    // would offer an execute button that sends English to a reader who is now
    // reading Korean — the backend would refuse it, so the card is stale the
    // moment the language changes and must go.
    {
      const { page, errors, scene } = await open(viewport);
      await prepareWith(page, tag);
      const beforeSwitch = await page.evaluate(() => ({
        language: document.querySelector('[data-testid="cc-language"]')?.textContent.trim(),
        consent: !!document.querySelector('[data-testid="cc-consent"]'),
        execute: !!document.querySelector('[data-testid="cc-execute"]')
      }));
      assert.equal(beforeSwitch.language, 'English', `${tag}: the preview says the note will be written in English`);
      assert.equal(beforeSwitch.consent, true, `${tag}: the consent card is up before the switch`);
      assert.equal(beforeSwitch.execute, true, `${tag}: with its execute button`);

      await page.evaluate(() => window.WB_I18N.set('ko'));
      const afterSwitch = await page.evaluate(() => ({
        consent: !!document.querySelector('[data-testid="cc-consent"]'),
        execute: !!document.querySelector('[data-testid="cc-execute"]'),
        preview: WB_CLAIM_CHECK.state.preview === null,
        claim: document.querySelector('[data-testid="cc-claim"]')?.value,
        prepare: document.querySelector('[data-testid="cc-prepare"]')?.textContent.trim(),
        title: document.querySelector('[data-testid="cc-title"]')?.textContent.trim()
      }));
      assert.equal(afterSwitch.preview, true,
        `${tag}: switching language drops the preview prepared for the other one`);
      assert.equal(afterSwitch.consent, false,
        `${tag}: so no consent card is left standing`);
      assert.equal(afterSwitch.execute, false,
        `${tag}: and there is no execute button to approve a transmission that is no longer what would be sent`);
      assert.equal(afterSwitch.claim, CLAIM, `${tag}: the claim itself survives the switch`);
      assert.equal(afterSwitch.prepare, '전송될 내용 미리보기', `${tag}: and the action that asks again is Korean`);
      assert.equal(afterSwitch.title, '주장–근거 검증', `${tag}: the whole sheet redraws in Korean`);

      // Asking again sends the new language, not the one the dropped card was for.
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]'));
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-language"]')
        ?.textContent.trim() === '한국어', null, { timeout: 5000 });
      assert.equal(scene.prepared, 2, `${tag}: asking again is a second prepare, on the click only`);
      assert.equal(scene.lastPrepare.language, 'ko',
        `${tag}: the re-prepared request carries the language the reader is now using`);
      assert.deepEqual(scene.lastPrepare.claim_text, CLAIM, `${tag}: and the same claim`);
      assert.equal(await page.locator('[data-testid="cc-execute"]').count(), 1,
        `${tag}: and a fresh execute button is offered for the Korean preview`);

      await page.evaluate(() => window.WB_I18N.set('en'));
      assert.deepEqual(errors, [], `${tag}: no script error across the language switch`);
      await page.close();
    }

    // ============ long unbroken text stays inside the sheet =================
    // A model note or a source title can carry a digest or a file stem with no
    // break opportunity in it, and at 720x480 the sheet is narrow. The overflow
    // check above measures the section and the document, and the sheet's own
    // scroller hides both: the text widened the sheet's content and pushed the
    // decision buttons sideways with neither number moving. So the sheet itself
    // is measured here, and the buttons against its horizontal bounds.
    if (viewport.width === 720) {
      const long = completed();
      long.check_id = 'cc-long-run';
      long.source_items = [{ item_id: 'i1', title: LONG_TITLE, sha256: 'd'.repeat(64) }];
      long.result = {
        ...long.result,
        secretary_note: `Here is what I found: ${LONG_RUN}`,
        secretary_note_shown: true,
        note_code: null,
        source_items: [{ item_id: 'i1', title: LONG_TITLE, sha256: 'd'.repeat(64) }],
        // A verified quote carrying a bare URL. It is the blockquote that holds
        // it that has to wrap: the mark above it is a short sentence, so a run in
        // the quote text is the only thing that puts pressure on .cc-quote-text.
        quotes: [{ text: `${SOURCE_A} See ${LONG_QUOTE_RUN}`, page: 1, item_id: 'i1',
          verified: true, cross_page: false, reason: null }]
      };
      // The claim carries a run of its own, so the copy inside the consent
      // transmission box (.cc-quote-claim) is under the same pressure.
      const longClaim = `${CLAIM} ${LONG_CLAIM_RUN}`;
      const { page, errors } = await open(viewport, { execute: long });
      // Typed by hand rather than through prepareWith(), because the claim itself
      // is part of what this scene is about.
      const field = page.locator('[data-testid="cc-claim"]');
      await field.click();
      await field.type(longClaim, { delay: 5 });
      await page.locator('[data-testid="cc-source-i1"]').check();
      await page.locator('[data-testid="cc-prepare"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]'));
      await page.waitForTimeout(100);

      // --- the claim inside the consent transmission box --------------------
      const claimFit = await page.evaluate(([run, claimText]) => {
        const root = document.querySelector('.cc-root');
        const box = root.getBoundingClientRect();
        const claim = document.querySelector('.cc-quote-claim');
        return {
          // The run really is in the painted claim: the measurement below is
          // about the text it claims to be about, not an empty box.
          hasRun: (claim?.textContent || '').includes(run),
          echoesClaim: (claim?.textContent || '').trim() === claimText,
          over: claim ? claim.scrollWidth - claim.clientWidth : null,
          rootOver: root.scrollWidth - root.clientWidth,
          left: box.left, right: box.right
        };
      }, [LONG_CLAIM_RUN, longClaim]);
      assert.equal(claimFit.hasRun, true,
        `${tag}: the 160-character run really is in the claim copy of the transmission box`);
      assert.equal(claimFit.echoesClaim, true,
        `${tag}: and that box holds the whole claim that was typed`);
      assert.ok(claimFit.over <= 1,
        `${tag}: the claim in the transmission box wraps its own long run (${claimFit.over}px)`);
      assert.ok(claimFit.rootOver <= 1,
        `${tag}: and the sheet does not scroll sideways because of it (${claimFit.rootOver}px)`);

      await page.locator('[data-testid="cc-execute"]').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="cc-result"]'));
      await page.waitForTimeout(150);

      const fit = await page.evaluate(([run, title, quoteRun]) => {
        const root = document.querySelector('.cc-root');
        const box = root.getBoundingClientRect();
        const noteText = document.querySelector('.cc-secretary-text')?.textContent || '';
        const mark = document.querySelector('.cc-quote-mark')?.textContent || '';
        const quoteText = document.querySelector('.cc-quote-text')?.textContent || '';
        // Every node the CSS rule names, measured on its own. A block element is
        // as wide as its container, so its bounding rect never shows this: the
        // text runs past it, and only the node's own scrollWidth says so.
        const guarded = ['.cc-secretary-text', '.cc-model-note-text', '.cc-quote-text',
          '.cc-quote-mark', '.cc-quote-reason', '.cc-quote-crosspage'].map((sel) => {
          const node = document.querySelector(sel);
          return node ? { sel, over: node.scrollWidth - node.clientWidth } : { sel, over: null };
        });
        const buttons = [...document.querySelectorAll('[data-testid="cc-accept"], [data-testid="cc-override"]')]
          .map((b) => {
            const r = b.getBoundingClientRect();
            return { id: b.getAttribute('data-testid'), left: r.left, right: r.right, width: r.width };
          });
        return {
          // The fixture really is on screen: the run is in the note and in the
          // mark, so the measurement below is about the text it claims to be about.
          runInNote: noteText.includes(run),
          titleInMark: mark.includes(title),
          runInQuote: quoteText.includes(quoteRun),
          quoteOver: (() => { const q = document.querySelector('.cc-quote-text');
            return q ? q.scrollWidth - q.clientWidth : null; })(),
          over: root.scrollWidth - root.clientWidth,
          left: box.left, right: box.right,
          guarded, buttons
        };
      }, [LONG_RUN, LONG_TITLE, LONG_QUOTE_RUN]);
      assert.equal(fit.runInNote, true,
        `${tag}: the 160-character run really is the note's painted text, so the fit below is about it`);
      assert.equal(fit.titleInMark, true,
        `${tag}: and the 160-character title really is in the quote mark`);
      assert.equal(fit.runInQuote, true,
        `${tag}: the 160-character URL really is in the quoted source sentence`);
      assert.ok(fit.quoteOver <= 1,
        `${tag}: the quoted source sentence wraps its own long run (${fit.quoteOver}px)`);
      assert.ok(fit.buttons.length >= 2,
        `${tag}: the decision buttons are on the card (${fit.buttons.map((b) => b.id).join(', ')})`);
      assert.ok(fit.over <= 1,
        `${tag}: a 160-character unbroken run never makes the sheet scroll sideways (${fit.over}px)`);
      for (const g of fit.guarded) {
        if (g.over === null) continue;
        assert.ok(g.over <= 1,
          `${tag}: ${g.sel} wraps its own long run instead of overflowing (${g.over}px)`);
      }
      // And the end the reader acts on: both decision buttons stay inside the
      // sheet's horizontal bounds. This hold is corroborating rather than
      // load-bearing — the decision row is a wrapping flex line, so it does not
      // follow the wide text out of the sheet — but it is the reader-facing
      // bound the overflow above is measured on behalf of, so it is asserted
      // rather than assumed.
      assert.equal(fit.buttons.length, 2,
        `${tag}: both decision buttons are painted (${fit.buttons.map((b) => b.id).join(', ')})`);
      for (const b of fit.buttons) {
        assert.ok(b.left >= fit.left - 1 && b.right <= fit.right + 1,
          `${tag}: ${b.id} stays within the sheet's horizontal bounds (${b.left.toFixed(1)}..${b.right.toFixed(1)} in ${fit.left.toFixed(1)}..${fit.right.toFixed(1)})`);
      }
      assert.deepEqual(errors, [], `${tag}: no script error with the long unbroken text`);
      await page.close();

      // The same run in the model's own note, which is only painted inside the
      // collapsed "What the model wrote" detail. It is a separately styled node,
      // so it is measured separately — and it is opened first, or its box would
      // be unrendered and the measurement meaningless.
      const detail = completed();
      detail.check_id = 'cc-long-detail';
      detail.source_items = [{ item_id: 'i1', title: 'Synthetic source A', sha256: 'd'.repeat(64) }];
      detail.result = {
        ...detail.result,
        secretary_note: `What I read was ${LONG_RUN} in the table.`,
        secretary_note_shown: false,
        note_code: 'numbers_disagree'
      };
      const second = await open(viewport, { execute: detail });
      await prepareWith(second.page, tag);
      await second.page.locator('[data-testid="cc-execute"]').click();
      await second.page.waitForFunction(() => document.querySelector('[data-testid="cc-model-note"]'));
      await second.page.locator('[data-testid="cc-model-note"] summary').click();
      await second.page.waitForTimeout(100);
      const detailFit = await second.page.evaluate((run) => {
        const node = document.querySelector('.cc-model-note-text');
        const root = document.querySelector('.cc-root');
        return {
          hasRun: (node?.textContent || '').includes(run),
          open: document.querySelector('[data-testid="cc-model-note"]')?.open === true,
          nodeOver: node ? node.scrollWidth - node.clientWidth : null,
          rootOver: root.scrollWidth - root.clientWidth
        };
      }, LONG_RUN);
      assert.equal(detailFit.open, true, `${tag}: the model's detail is opened before it is measured`);
      assert.equal(detailFit.hasRun, true,
        `${tag}: the 160-character run really is in the model's note, so the measurement is about it`);
      assert.ok(detailFit.nodeOver <= 1,
        `${tag}: the model's note wraps its own long run (${detailFit.nodeOver}px)`);
      assert.ok(detailFit.rootOver <= 1,
        `${tag}: and the sheet does not scroll sideways with the detail open (${detailFit.rootOver}px)`);
      assert.deepEqual(second.errors, [], `${tag}: no script error with the long model note`);
      await second.page.close();
    }
  }
  console.log('claim-check frames F–I: all checks passed at 1280x800 and 720x480');
} finally {
  await browser.close();
  server.close();
}
