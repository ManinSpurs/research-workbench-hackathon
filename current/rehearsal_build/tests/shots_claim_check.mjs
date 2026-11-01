// Screenshots of frames F–I in the real frontend, for design/previews/clarity/after.
// Synthetic fixtures only; no provider, no model, no network.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const frontend = path.resolve(import.meta.dirname, '../frontend');
const out = path.resolve(import.meta.dirname, '../../../design/previews/clarity/after');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };

const server = createServer(async (req, res) => {
  const p = new URL(req.url, 'http://localhost').pathname;
  const f = p === '/' ? 'index.html' : p.slice(1);
  try {
    const body = await readFile(path.join(frontend, f));
    res.setHeader('Content-Type', TYPES[path.extname(f)] || 'application/octet-stream');
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });

const CLAIM = 'Recorded approval timing identifies the intervention date in 42 of the sampled districts.';
const PREVIEW = {
  check_id: 'cc-1', preview_hash: 'a'.repeat(64), task: 'claim_check',
  provider: 'fixture-provider', model: 'fixture-model-1', max_inference_calls: 1,
  maximum_reserved_usd: 0.42,
  estimate_note: 'Conservative reservation, not a predicted bill: the full documented context is reserved at the uncached rate. Actual usage is settled when it is reported.',
  estimate_note_code: 'conservative_reservation_not_a_bill',
  pricing_date: null, data_use_mode: 'Standard', model_access_verified: true,
  account_ready: true, cloud_enabled: true, requires_explicit_consent: true,
  expires_in_seconds: 900, baseline_definition: 'One direct call with the shared safety core.',
  transmission: {
    destination: 'fixture provider (synthetic, no network)', claim_text: CLAIM,
    sources: [{ item_id: 'i1', title: 'Synthetic source A', page_count: 2,
      excerpt: [{ page: 1, text: 'Approval and intervention dates were recorded separately in every district.', truncated: false },
                { page: 2, text: 'The 42 sampled districts report the two dates as distinct fields.', truncated: true }] }],
    table_item_id: null, max_output_tokens: 1200, reasoning: null, store: false, tools: [],
    whole_csv_rows: false, includes_canvas_notes_and_question_decisions: false,
    content_sha256: 'b'.repeat(64), body_sha256: 'c'.repeat(64)
  },
  numeric_reconciliation: { numbers: [
    { text: '42', status: 'match', reason: null },
    { text: '1998', status: 'not_found', reason: 'not in the transmitted excerpt' }] },
  verification_plan: 'Every returned quote is checked verbatim against the excerpt text actually transmitted above; a quote outside it never counts.',
  verification_plan_code: 'verbatim_excerpt_only_v1',
  verification_plan_thresholds: { min_quote_words: 5, min_quote_nonspace_chars: 20 },
  semantic_correctness: 'HUMAN_REVIEW_REQUIRED'
};
const COMPLETED = {
  check_id: 'cc-1', project_id: 'p1', draft_id: null, question_id: null,
  claim_text: CLAIM, claim_location: null, source_items: [{ item_id: 'i1', title: 'Synthetic source A' }],
  table_item_id: null, status: 'completed_known', job_id: 'job-1', model: 'fixture-model-1',
  provider: 'fixture-provider', mode: 'direct', reservation_usd: 0.42, error_code: null,
  stale: false, stale_reasons: [], decision: null, created_at: '2026-01-01T00:00:00Z',
  result: {
    verdict: 'partially_supported', model_verdict: 'supported', downgraded: true,
    downgrade_reason: 'One of the two quotes could not be found in the text that was sent.',
    rationale: 'The sources say the two dates are recorded separately; they do not verify the count.',
    secretary_note: 'I think the dates are recorded separately in the source, so this looks partly supported to me. It seems the count itself is not in the part you sent, so I would check the original table before relying on that number.',
    secretary_note_shown: false,
    note_code: 'quotes_not_found_in_sources',
    language: 'en',
    verified_quote_count: 1, semantic_correctness: 'HUMAN_REVIEW_REQUIRED',
    quotes: [
      { text: 'Approval and intervention dates were recorded separately in every district.', page: 1,
        item_id: 'i1', verified: true, cross_page: false, reason: null },
      { text: 'Approval timing equals the intervention date in every district.', page: 2,
        item_id: 'i1', verified: false, cross_page: true, reason: 'outside_transmitted_excerpt' }
    ]
  }
};
const NOTHING = {
  ...COMPLETED,
  check_id: 'cc-nothing',
  result: {
    ...COMPLETED.result,
    verdict: 'not_found', model_verdict: 'not_found', downgraded: false,
    downgrade_reason: null,
    secretary_note: 'Nothing in the two pages you sent appears to mention the intervention date, so I could not check this one.',
    secretary_note_shown: false,
    note_code: 'nothing_found_in_sources',
    verified_quote_count: 0,
    quotes: []
  }
};

const HISTORY = [COMPLETED,
  { check_id: 'cc-interrupted', project_id: 'p1', claim_text: 'A second synthetic claim, interrupted.',
    source_items: [], status: 'interrupted_unknown_charge', job_id: 'job-2',
    model: 'fixture-model-1', provider: 'fixture-provider', reservation_usd: 0.42,
    result: { error_code: 'interrupted_unknown_charge' }, error_code: 'interrupted_unknown_charge',
    stale: false, stale_reasons: [], decision: null, created_at: '2026-01-01T00:01:00Z' },
  { check_id: 'cc-over', project_id: 'p1', claim_text: 'A third synthetic claim, over the reservation.',
    source_items: [], status: 'failed_known_charge', job_id: 'job-3',
    model: 'fixture-model-1', provider: 'fixture-provider', reservation_usd: 0.42,
    result: { error_code: 'usage_exceeded_reservation' }, error_code: 'usage_exceeded_reservation',
    stale: false, stale_reasons: [], decision: null, created_at: '2026-01-01T00:02:00Z' },
  NOTHING];

await mkdir(out, { recursive: true });

for (const viewport of [{ width: 1280, height: 800 }, { width: 720, height: 480 }]) {
  const tag = `${viewport.width}x${viewport.height}`;
  const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
  const scene = { checks: HISTORY };
  await page.route('http://api.test/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    const j = (d) => route.fulfill({ json: d });
    if (p === '/domain-packs') return j({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (p === '/chips') return j({ chips: [] });
    if (p === '/chips/social_science') return j({ pack_id: 'social_science', source: 'builtin' });
    if (p === '/projects') return j({ projects: [{ id: 'p1', name: 'Synthetic claim study', kind: 'fx', pack_id: null }] });
    if (p === '/briefing') return j({ rows: [] });
    if (p === '/capabilities/latex') return j({ available: false, engines: {}, note: 'Synthetic fixture: no compile requested.' });
    if (p === '/projects/p1/drafts') return j({ project_id: 'p1', drafts: [] });
    if (p === '/library/tree') return j({ folders: [], items: [
      { id: 'i1', title: 'Synthetic source A', kind: 'pdf' },
      { id: 'i2', title: 'Synthetic source B', kind: 'pdf' }] });
    if (p === '/projects/p1/claim-checks/prepare') return j(PREVIEW);
    if (p === '/projects/p1/claim-checks/numbers')
      return j({ numeric: PREVIEW.numeric_reconciliation });
    if (/claim-checks\/[^/]+\/execute$/.test(p)) return j(COMPLETED);
    if (/claim-checks\/[^/]+\/receipt$/.test(p)) return j({ job_status: 'completed_known', outcome: 'result_recorded', reservation_usd: 0.42, model: 'fixture-model-1', data_use_mode: 'Standard', result_integrity: 'verified' });
    if (p === '/projects/p1/claim-checks') return j({ checks: scene.checks });
    if (p.endsWith('/profile')) return j({ variables: [] });
    if (p.endsWith('/briefing')) return j({ parts: {} });
    if (p.endsWith('/clarifications')) return j({ clarifications: [] });
    if (p.endsWith('/pack-memory')) return j({ questions: [] });
    if (p.endsWith('/revisit-conditions')) return j({ conditions: [], model_calls: 0 });
    if (p.endsWith('/secretary-turns')) return j({ turns: [], model_calls: 0 });
    return j({});
  });
  await page.addInitScript(() => {
    try { localStorage.setItem('wb.pack', 'social_science'); } catch (_) {}
    window.WB_DESKTOP = { openChip() {}, newChip() {} };
  });
  await page.goto(`${origin}/?api=http://api.test`);
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.getByTestId('home-menu-research').click();
  await page.locator('#quiet-projects').click();
  await page.getByTestId('open-fx').click();
  await page.waitForFunction(() => typeof S === 'object' && S.project === 'p1');
  if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
    await page.locator('#quiet-projects').click();
  await page.evaluate(() => openLibraryView());
  await page.waitForFunction(() => typeof libraryState === 'object'
    && libraryState.tree !== null && !libraryState.loading);
  await page.evaluate(() => openWritingView());
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-open"]'));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, `claims-closed-${tag}.png`) });

  // F: the free step.
  await page.locator('[data-testid="cc-open"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-sources"]'));
  await page.locator('[data-testid="cc-claim"]').fill(CLAIM);  await page.locator('[data-testid="cc-source-i1"]').check();
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `claims-F-${tag}.png`) });

  // G and H: numbers and the consent preview.
  await page.locator('[data-testid="cc-prepare"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-consent"]'));
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `claims-GH-${tag}.png`) });

  // I: the secretary's note.
  await page.locator('[data-testid="cc-execute"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-secretary-note"]'));
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `claims-I-${tag}.png`) });

  // The receipt.
  await page.locator('[data-testid="cc-receipt"]').click();
  await page.waitForFunction(() => document.getElementById('cc-receipt-panel'));
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `claims-receipt-${tag}.png`) });

  // The three end states, from the project's own list.
  await page.locator('[data-testid="cc-receipt-close"]').click();
  for (const [i, name] of [[1, 'interrupted'], [2, 'over-reservation'], [3, 'nothing-found']]) {
    await page.locator('.cc-history-row').nth(i).locator('.cc-history-open').click();
    await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(out, `claims-${name}-${tag}.png`) });
  }
  await page.close();
  console.log(`saved claims screenshots at ${tag}`);
}
await browser.close();
server.close();
