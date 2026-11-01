import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

// Synthetic UI fixture. No provider, user database or research file is opened.
export async function interiorFixture() {
  const frontend = path.resolve(import.meta.dirname, '../frontend');
  const server = createServer(async (req, res) => {
    const requestPath = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(frontend, requestPath === '/' ? 'index.html' : '.' + requestPath);
    if (!file.startsWith(frontend + path.sep)) { res.writeHead(403); res.end(); return; }
    try {
      const body = await readFile(file);
      const ext = path.extname(file);
      res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png' })[ext] || 'application/octet-stream');
      res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 950 }, reducedMotion: 'reduce' });
  const errors = [], unexpected = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().startsWith(origin) && response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.route('http://interior.test/**', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    requests.push({ method: request.method(), path: p });
    const reply = json => route.fulfill({ json });
    if (p === '/domain-packs') return reply({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (p === '/chips') return reply({ chips: [] });
    if (p === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
    if (p === '/chips/social_science/record') return reply({ questions: [], answers: [], results: [], code: [], harness: [] });
    if (p === '/projects') return reply({ projects: [{ id: 'p1', name: 'Synthetic interior study', kind: 'fx', pack_id: null }] });
    if (p === '/briefing') return reply({ rows: [] });
    if (p === '/projects/p1/profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (p === '/projects/p1/briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (p === '/projects/p1/clarifications') return reply({ clarifications: [] });
    if (p === '/projects/p1/pack-memory') return reply({ questions: [] });
    if (p === '/projects/p1/revisit-conditions') return reply({ conditions: [], model_calls: 0 });
    if (p === '/projects/p1/secretary-turns') return reply({ turns: [], model_calls: 0 });
    if (p === '/projects/p1/drafts') return reply({ project_id: 'p1', drafts: [] });
    if (p === '/capabilities/latex') return reply({ available: false, engines: {}, note: 'Synthetic fixture: no compile requested.' });
    if (p === '/library/tree') return reply({ folders: [], items: [] });
    unexpected.push(`${request.method()} ${p}`);
    return route.fulfill({ status: 404, json: { error: 'Unexpected fixture endpoint' } });
  });
  await page.addInitScript(() => localStorage.setItem('wb.pack', 'social_science'));
  await page.goto(`${origin}/?api=http://interior.test`);
  await page.getByTestId('home-menu-research').waitFor();
  return {
    page, requests, errors,
    async workspace() {
      await page.getByTestId('home-menu-research').click();
      await page.locator('#quiet-projects').click();
      await page.getByTestId('open-fx').click();
      await page.waitForFunction(() => S.project === 'p1');
      if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
        await page.locator('#quiet-projects').click();
    },
    async close() {
      await browser.close();
      await new Promise(resolve => server.close(resolve));
      assert.deepEqual(unexpected, [], 'all API requests belong to the fixture contract');
      assert.deepEqual(errors, [], 'the real UI loads without page or asset errors');
    }
  };
}

export async function contrast(page, selector) {
  return page.locator(selector).evaluate(el => {
    const style = getComputedStyle(el), color = style.color;
    let host = el;
    while (getComputedStyle(host).backgroundColor === 'rgba(0, 0, 0, 0)') host = host.parentElement;
    const background = getComputedStyle(host).backgroundColor;
    const luminance = value => {
      const c = value.match(/[\d.]+/g).slice(0, 3).map(Number).map(x => {
        x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4;
      });
      return .2126 * c[0] + .7152 * c[1] + .0722 * c[2];
    };
    const a = luminance(color), b = luminance(background);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  });
}
