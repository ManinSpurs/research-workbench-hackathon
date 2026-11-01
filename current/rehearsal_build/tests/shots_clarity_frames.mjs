// Screenshots of frames A–E in the real frontend, for design/previews/clarity/after.
// Same fixtures as review_clarity_frames.mjs: no provider, no model, no network,
// synthetic data only. This script only draws; every rule it captures is asserted
// in review_clarity_frames.mjs.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const frontend = path.resolve(import.meta.dirname, '../frontend');
const out = path.resolve(import.meta.dirname, '../../../design/previews/clarity/after');
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

async function open({ width, height }) {
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
  await page.route('http://api.test/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (data) => route.fulfill({ json: data });
    if (p === '/domain-packs') return json({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (p === '/chips') return json({ chips: [] });
    if (p === '/chips/social_science') return json({ pack_id: 'social_science', source: 'builtin' });
    if (p === '/projects') return json({ projects: [{ id: 'p1', name: 'Synthetic interior study', kind: 'fx', pack_id: null }] });
    if (p === '/briefing') return json({ rows: [] });
    if (p === '/projects/p1/profile') return json({ variables: [], computed_by: 'ordinary code, no model call' });
    if (p === '/projects/p1/briefing') return json({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (p === '/projects/p1/clarifications') return json({ clarifications: [] });
    if (p === '/projects/p1/pack-memory') return json({ questions: [] });
    if (p === '/projects/p1/revisit-conditions') return json({ conditions: [], model_calls: 0 });
    if (p === '/projects/p1/secretary-turns') return json({ turns: [], model_calls: 0 });
    if (p === '/projects/p1/drafts') return json({ project_id: 'p1', drafts: [] });
    if (p === '/capabilities/latex') return json({ available: false, engines: {}, note: 'Synthetic fixture: no compile requested.' });
    if (p === '/library/tree') return json({ folders: [], items: [] });
    return json({});
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await page.goto(`${origin}/?api=http://api.test`);
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForTimeout(250);
  return page;
}
const closeDrawers = async (page) => {
  await page.evaluate(() => {
    for (const which of ['projects', 'secretary']) {
      if (window.WB_DECLUTTER_SHELL && WB_DECLUTTER_SHELL.isPanelOpen(which)) WB_DECLUTTER_SHELL.closePanel(which);
    }
  });
  await page.waitForTimeout(250);
};

for (const { width, height } of [{ width: 1280, height: 800 }, { width: 720, height: 480 }]) {
  const tag = `${width}x${height}`;
  const page = await open({ width, height });

  // A: the home screen, with one purpose line under each tile.
  await page.screenshot({ path: path.join(out, `home-${tag}.png`) });

  // Into the workspace, with the one synthetic project open.
  await page.getByTestId('home-menu-research').click();
  await page.locator('#quiet-projects').click();
  await page.getByTestId('open-fx').click();
  await page.waitForFunction(() => typeof S === 'object' && S.project === 'p1');
  if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
    await page.locator('#quiet-projects').click();
  await page.waitForFunction(() =>
    Math.round(document.getElementById('app-shell').getBoundingClientRect().width) === innerWidth);
  await page.waitForTimeout(250);

  // C: Research, before a question. The pane is the single statement here.
  await page.evaluate(() => showResearchView());
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `research-${tag}.png`) });

  // B: Literature Review, with its one empty state.
  await page.evaluate(() => openLibraryView());
  await page.waitForFunction(() => typeof libraryState === 'object' &&
    libraryState.tree !== null && !libraryState.loading);
  await closeDrawers(page);
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(out, `library-${tag}.png`) });

  // D: Writing, with the claim-check sheet still closed on its one line.
  await page.evaluate(() => openWritingView());
  await page.waitForFunction(() => document.querySelector('[data-testid="cc-open"]'));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, `writing-${tag}.png`) });

  // E: Conversation Review, with its one empty state.
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => typeof secretaryReviewState === 'object' && secretaryReviewState &&
    secretaryReviewState.loaded && !secretaryReviewState.loading);
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(out, `review-${tag}.png`) });

  await page.close();
  console.log(`saved A–E screenshots at ${tag}`);
}
await browser.close();
await new Promise((resolve) => server.close(resolve));
