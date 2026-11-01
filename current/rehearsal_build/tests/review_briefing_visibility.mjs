// Run against an isolated source API: WB_TEST_API=http://127.0.0.1:<port> node tests/review_briefing_visibility.mjs
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';
// A structured briefing item without the legacy `text` must still render.
{
  const home = await readFile(new URL('../frontend/home_hub.js', import.meta.url), 'utf8');
  const code = home.slice(home.indexOf('function briefingShown(item)'), home.indexOf('const text = item.text'));
  const context = vm.createContext({tr: (s, v) => v ? s.replace(/\{(\w+)\}/g, (_, k) => v[k]) : s});
  vm.runInContext(`${code} return null; }\nvar result = [{kind: 'decision', verdict: 'adopt', question: 'Q?', reason: 'R'}, {}].filter(briefingShown).map(briefingText);`, context);
  assert.deepEqual([...context.result], ['Adopted · Q?\nReason: R']);
}
const api = process.env.WB_TEST_API;
assert.ok(api, 'An isolated source API is required');
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = new URL('../frontend' + (pathname === '/' ? '/index.html' : pathname), import.meta.url);
    res.setHeader('Content-Type', file.pathname.endsWith('.js') ? 'text/javascript' : file.pathname.endsWith('.css') ? 'text/css' : file.pathname.endsWith('.png') ? 'image/png' : 'text/html');
    res.end(await readFile(file));
  } catch (_) { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch();
try {
  for (const lang of ['en', 'ko']) {
    const page = await browser.newPage({viewport: {width: 1000, height: 700}, reducedMotion: 'reduce'});
    await page.route('http://localhost:8123/**', async route => {
      await route.fulfill({response: await page.request.fetch(route.request().url().replace('http://localhost:8123', api), {
        method: route.request().method(), data: route.request().postData(), headers: {'Content-Type': 'application/json'}
      })});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByTestId('try-sample-chip').waitFor();
    await page.evaluate(lang => WB_I18N.set(lang), lang);
    assert.equal(await page.locator('#launch-briefing').isVisible(), false);
    await page.getByTestId('try-sample-chip').click();
    if (lang === 'en') {
      await page.getByTestId('continue-notebook').waitFor({state: 'visible'});
      assert.ok(await page.locator('.home-import-note').evaluate(node => node.getBoundingClientRect().bottom <= document.querySelector('#home-conversation').getBoundingClientRect().top), 'Import helper must not overlap the briefing');
      page.on('dialog', dialog => dialog.accept());
      const imported = page.waitForResponse(response => response.url().endsWith('/import') && response.request().method() === 'POST');
      await page.getByTestId('continue-notebook').click();
      assert.equal((await imported).status(), 200);
    }
    await page.waitForFunction(() => document.querySelector('#home-conversation').innerText.includes('withdrawn'));
    assert.equal(await page.locator('.home-brief-section').count(), 3);
    assert.equal(await page.locator('#home-conversation').isVisible(), true);
    await page.getByTestId('continue-notebook').waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.querySelector('#secretary-mode').value === 'reply');
    await page.waitForFunction(() => S.briefing && S.cq && document.querySelectorAll('.home-brief-section').length === 3);
    assert.match(await page.locator('#secretary-mode-context').innerText(), /withdrawn/);
    assert.match(await page.locator('.home-brief-section').first().innerText(), lang === 'ko' ? /채택 ·[\s\S]*이유:/ : /Adopted ·[\s\S]*Reason:/);
    assert.match(await page.locator('#home-project option:checked').innerText(), lang === 'ko' ? /^가져온 노트:/ : /^Imported:/);
    assert.ok(await page.locator('.home-bubble').first().evaluate(node => !getComputedStyle(node).fontFamily.includes('monospace')));
    const noOverlap = () => document.querySelector('#home-conversation').getBoundingClientRect().bottom <= document.querySelector('#home-composer-slot').getBoundingClientRect().top;
    assert.ok(await page.evaluate(noOverlap), 'Briefing and save/composer area must not overlap');
    await page.screenshot({path: `../../design/previews/briefing/sample-import-${lang}.png`});
    await page.screenshot({path: `../../design/previews/briefing/after-${lang}.png`});
    const scrolling = await page.locator('#home-conversation').evaluate(node => {
      node.scrollTop = node.scrollHeight;
      return node.scrollHeight <= node.clientHeight || node.scrollTop > 0;
    });
    assert.ok(scrolling, 'All briefing content must be reachable');
    await page.setViewportSize({width: 720, height: 350});
    assert.ok(await page.evaluate(noOverlap), 'Smallest supported window must not overlap');
    console.log(`SAMPLE_IMPORT_${lang.toUpperCase()}_PASS`, await page.locator('.home-brief-section').allTextContents());
    await page.close();
  }
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
