import assert from 'node:assert/strict';
import { interiorFixture, contrast } from './interior_fixture.mjs';

const fixture = await interiorFixture(), { page, requests } = fixture;
try {
  await page.getByTestId('home-settings').click();
  await page.locator('.is-shell').waitFor();
  assert.equal(await page.getByTestId('settings-sheet').getAttribute('aria-modal'), 'true');
  assert.equal(await page.getByRole('tab').count(), 4);
  for (const id of ['settings-lang', 'settings-theme', 'settings-sound', 'settings-field', 'settings-end']) {
    assert.equal(await page.locator('#' + id).count(), 1, 'existing controls are moved, not duplicated');
  }
  await page.getByTestId('interior-settings-tab-general').focus();
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.getByTestId('interior-settings-tab-models').getAttribute('aria-selected'), 'true');
  // Home can refresh its read-only notebook while this sheet is open.
  const providerOrWriteRequests = () => requests.filter(request =>
    request.method !== 'GET' || request.path.startsWith('/ai/') || request.path.includes('/ai/'));
  const beforeInspect = providerOrWriteRequests().length;
  await page.getByTestId('interior-settings-inspect').click();
  assert.equal(providerOrWriteRequests().length, beforeInspect, 'inspecting the displayed readiness makes no provider request or write');
  for (const category of ['cost', 'privacy', 'general']) {
    await page.getByTestId('interior-settings-tab-' + category).click();
  }
  await page.locator('#settings-sound').uncheck();
  await page.locator('#settings-lang').selectOption('ko');
  assert.equal(await page.getByTestId('interior-settings-tab-general').innerText(), '일반');
  assert.equal(await page.locator('#settings-sound').locator('..').innerText(), '시작 효과음');
  await page.getByTestId('interior-settings-tab-models').click();
  assert.match(await page.getByTestId('interior-settings-mirror-modal_shared').innerText(), /이 실행 환경에는 엔드포인트가 설정되어 있지 않습니다/);
  await page.getByTestId('interior-settings-tab-general').click();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('#home-project option').textContent === '프로젝트 선택');
  assert.match(await page.locator('#home-conversation').innerText(), /프로젝트를 선택하면 지난 작업을 이어갈 수 있습니다/);
  assert.equal(await page.locator('#secretary-input').getAttribute('placeholder'), '비서에게 물어보세요...');
  assert.match(await page.locator('#secretary-compose').innerText(), /메시지 종류/);
  assert.match(await page.locator('#secretary-compose').innerText(), /새 연구 질문 시작/);
  assert.match(await page.locator('#secretary-compose').innerText(), /먼저 프로젝트를 선택하거나 여세요/);
  assert.equal(await page.getByText('칩 저장 / 꺼내기…', { exact: true }).count(), 1);
  await page.getByTestId('home-settings').click();
  for (const theme of ['light', 'dark']) {
    await page.locator('#settings-theme').selectOption(theme);
    assert.ok(await contrast(page, '.is-pane:not([hidden]) .is-pane-title') >= 4.5);
  }
  await page.locator('#settings-lang').selectOption('en');
  await page.keyboard.press('Escape');
  assert.equal(await page.getByTestId('settings-sheet').isVisible(), false);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'home-settings');
  await page.getByTestId('home-settings').click();
  assert.equal(await page.locator('#settings-sound').isChecked(), false, 'the actual startup preference persists');
  assert.equal(await page.evaluate(() => localStorage.getItem('wb.sounds')), 'off');
  await page.keyboard.press('Escape');
  await fixture.workspace();
  await page.getByTestId('interior-settings-open').click();
  assert.equal(await page.getByTestId('settings-sheet').isVisible(), true, 'settings are visible above the expanded workspace');
  await page.getByTestId('interior-settings-tab-general').click();
  await page.locator('#settings-close').focus();
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'settings-field', 'focus wraps inside the active pane');
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'settings-close');
  await page.setViewportSize({ width: 375, height: 812 });
  const bounds = await page.getByTestId('settings-sheet').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 375);
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 812);
  const closeBounds = await page.locator('#settings-close').boundingBox();
  assert.ok(closeBounds.width >= 44 && closeBounds.height >= 44);
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'interior-settings-open');
  assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'settings navigation never sends, deletes or ends a session');
  console.log('INTERIOR_SETTINGS_PASS: home/workspace, four real categories, same controls/preferences, keyboard/focus, no provider calls, KO, themes, narrow layout');
} finally { await fixture.close(); }
