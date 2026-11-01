// Real rendered state checks; synthetic transport only, no user data or AI.
import assert from 'node:assert/strict';
import { interiorFixture } from './interior_fixture.mjs';

const fixture = await interiorFixture();
const { page } = fixture;
try {
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => {
      document.documentElement.dataset.theme = theme;
      for (const control of document.querySelectorAll('#home-project, #home-composer-slot button')) control.disabled = true;
    }, theme);
    const states = await page.locator('#home-project, #home-composer-slot button').evaluateAll(controls => controls.map(el => {
      const probe = document.createElement('span');
      probe.style.backgroundColor = 'var(--surface-2)';
      probe.style.color = 'var(--muted)';
      el.parentElement.append(probe);
      const actual = getComputedStyle(el), expected = getComputedStyle(probe);
      const state = [actual.backgroundColor, expected.backgroundColor, actual.color, expected.color];
      probe.remove();
      return state;
    }));
    assert.ok(states.length >= 2, 'home controls really exist');
    for (const [bg, expectedBg, color, expectedColor] of states) {
      assert.equal(bg, expectedBg, theme + ' disabled home fill');
      assert.equal(color, expectedColor, theme + ' disabled home text');
    }
  }
  await fixture.workspace();
  await page.locator('#project-list').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#latex-view').isVisible(), false,
    'the inactive writing surface remains hidden');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => {
      document.documentElement.dataset.theme = theme;
      for (const selector of ['[data-testid="ask"]', '#dataset-select', '#reason', '#quick-note-input', '#register-fx', '#refresh-briefing']) {
        const control = document.querySelector(selector);
        if (control) control.disabled = true;
      }
    }, theme);
    for (const selector of ['[data-testid="ask"]', '#dataset-select', '#reason', '#quick-note-input', '#register-fx', '#refresh-briefing']) {
      const control = page.locator(selector);
      if (!await control.count()) continue;
    const style = await control.evaluate(el => {
      const probe = document.createElement('span');
      probe.style.backgroundColor = 'var(--surface-2)';
      probe.style.color = 'var(--muted)';
      el.parentElement.append(probe);
      const actual = getComputedStyle(el), expected = getComputedStyle(probe);
      const result = {
        background: actual.backgroundColor, color: actual.color,
        expectedBackground: expected.backgroundColor, expectedColor: expected.color,
      };
      probe.remove();
      return result;
    });
    assert.equal(style.background, style.expectedBackground, theme + ' disabled fill');
    assert.equal(style.color, style.expectedColor, theme + ' disabled text');
    }
  }
  await page.getByTestId('interior-settings-open').click();
  await page.setViewportSize({ width: 375, height: 812 });
  const columns = await page.locator('#settings-sheet .is-shell')
    .evaluate(el => getComputedStyle(el).gridTemplateColumns);
  assert.equal(columns.trim().split(/\s+/).length, 1,
    'narrow settings stacks its category navigation above the content');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  console.log('MACOS_STATES_PASS: inactive writing hidden, disabled controls muted in both themes, narrow settings single-column');
} finally {
  await fixture.close();
}
