import assert from 'node:assert/strict';
import { interiorFixture, contrast } from './interior_fixture.mjs';

const fixture = await interiorFixture(), { page, requests } = fixture;
try {
  await page.locator('#home-project').selectOption('p1');
  await page.locator('#secretary-mode-context').waitFor();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => WB_THEME.set(value), theme);
    assert.ok(await contrast(page, '#secretary-mode-context') >= 4.5, 'home mode guidance remains readable');
  }
  await page.evaluate(() => WB_THEME.set('light'));
  await fixture.workspace();
  // H1: the clarity pane is Research's single statement, so the heading a user
  // actually reads and that must be legible is the pane's title. The shipped
  // #interior-question-title is deliberately not painted any more; its node and
  // its text are still asserted, as a document contract rather than a visible one.
  await page.locator('[data-testid="clarity-title-research"]').waitFor();
  assert.equal(await page.locator('#interior-question-title').textContent(),
    'Start with a research question.', 'the shipped intro keeps its node and its sentence');
  assert.equal(await page.locator('#research-location').innerText(), 'Synthetic interior study');
  assert.equal(await page.locator('[data-testid="clarity-title-research"]').innerText(),
    'Ask a question, look at the evidence, then decide.', 'and the pane states the purpose on screen');
  assert.equal(await page.locator('#research-intro').isVisible(), false,
    'the duplicate intro is not painted alongside it');
  assert.equal(await page.locator('#research-tool-code').isDisabled(), true, 'no code workspace is fabricated');
  assert.equal(await page.locator('#research-tool-canvas').isDisabled(), true, 'an unavailable canvas remains disabled');
  await page.locator('#research-tool-data').click();
  assert.equal(await page.locator('#profile').evaluate(el => el.closest('details').open), true);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => WB_THEME.set(value), theme);
    assert.ok(await contrast(page, '[data-testid="clarity-title-research"]') >= 4.5);
  }
  await page.evaluate(() => openWritingView());
  assert.equal(await page.locator('[data-testid="clarity-pane-research"]').isVisible(), false);
  assert.equal(await page.locator('#latex-view').isVisible(), true);
  await page.evaluate(() => openLibraryView());
  assert.equal(await page.locator('[data-testid="clarity-pane-research"]').isVisible(), false);
  assert.equal(await page.locator('#latex-view').isVisible(), false);
  await page.evaluate(() => showResearchView());
  // A synthetic render state tests the evidence/decision layout, not a finding.
  await page.locator('#result').evaluate(el => { el.textContent = 'Synthetic evidence layout'; el.classList.remove('hidden'); });
  await page.locator('#decision').evaluate(el => el.classList.remove('hidden'));
  const evidence = await page.locator('#result').boundingBox(), decision = await page.locator('#decision').boundingBox();
  assert.ok(decision.x > evidence.x && Math.abs(decision.y - evidence.y) < 2, 'evidence and the researcher decision are distinct columns');
  await page.setViewportSize({ width: 375, height: 812 });
  const narrowEvidence = await page.locator('#result').boundingBox(), narrowDecision = await page.locator('#decision').boundingBox();
  assert.ok(narrowDecision.y > narrowEvidence.y, 'the same content stacks without clipping at a narrow width');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 375, 'the interior fits a narrow viewport');
  const targets = await page.locator('#research-tools button').evaluateAll(buttons => buttons.filter(button => button.getClientRects().length > 0).map(button => button.getBoundingClientRect().height));
  assert.ok(targets.length > 0 && targets.every(height => height >= 44), 'visible research tools retain 44px targets');
  assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'navigation and rendering do not mutate research records');
  console.log('INTERIOR_RESEARCH_PASS: real assets, truthful tools, preserved views, light/dark contrast, narrow layout, 44px controls, no writes');
} finally { await fixture.close(); }
