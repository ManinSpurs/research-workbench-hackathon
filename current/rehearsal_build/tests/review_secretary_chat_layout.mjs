// Multi-exchange conversation layout, using the real UI and recorded-turn API.
// All replies are synthetic; no model, user database or paid endpoint is used.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { interiorFixture } from './interior_fixture.mjs';

const fixture = await interiorFixture();
const { page } = fixture;
const output = path.resolve(import.meta.dirname, '../../../design/previews/secretary-chat/layout');
await mkdir(output, { recursive: true });
let turns = [], serial = 0, release;
const long = {
  en: Array.from({length: 16}, (_, i) => `${i + 1}. Compare the recorded permit date with the approval date before drawing a conclusion. Keep missing observations visible, describe the uncertainty, and check whether the same definition applies to each neighborhood.`).join('\n\n'),
  ko: Array.from({length: 16}, (_, i) => `${i + 1}. 허가 날짜와 승인 날짜를 먼저 비교하고, 누락된 관측값은 따로 표시해 주세요. 동네마다 같은 정의가 적용되는지 확인하고 불확실성의 원인을 설명하면 연구 질문을 더 명확하게 만들 수 있습니다.`).join('\n\n')
};
const off = {
  en: 'The local model is off. Turn it on in Settings, or send a research question with "Start new research question".',
  ko: '로컬 모델이 꺼져 있습니다. 설정에서 켜거나, "새 연구 질문 시작"으로 연구 질문을 보낼 수 있습니다.'
};
const unfinished = {
  en: "The local model couldn't finish this reply. Try a shorter question.",
  ko: '로컬 모델이 이번 답변을 마치지 못했습니다. 질문을 더 짧게 해 보세요.'
};
let nextReply;
await page.route('http://interior.test/**', async route => {
  const request = route.request(), p = new URL(request.url()).pathname;
  if (p === '/projects/p1/secretary-turns') {
    const pack = new URL(request.url()).searchParams.get('pack_id');
    return route.fulfill({json: {turns: turns.filter(t => pack == null || t.pack_id === pack).reverse()}});
  }
  if (p !== '/projects/p1/secretary-chat') return route.fallback();
  const body = request.postDataJSON();
  const record = (role, text) => turns.push({id: `t${++serial}`, role, body_json: {text}, pack_id: body.pack_id ?? null, status: 'recorded'});
  record('researcher', body.text);
  await new Promise(resolve => { release = resolve; });
  record('secretary', nextReply);
  return route.fulfill({json: {text: nextReply, model: 'Synthetic local reply', cost_usd: 0,
    receipt: {fixture: true, billing_status: 'NOT_VERIFIED', detail: 'x'.repeat(160)}}});
});

async function checkLayout(expected, newest = true) {
  await page.waitForFunction(n => document.querySelectorAll('#secretary-chat-history > *').length === n, expected);
  const layout = await page.evaluate(() => {
    const log = document.getElementById('secretary-chat-history'), host = log.parentElement;
    const bubbles = [...log.children], rect = el => el.getBoundingClientRect();
    return {
      inComposer: document.querySelectorAll('#secretary-compose .secretary-chat-bubble').length,
      overlaps: bubbles.some((b, i) => i && rect(b).top < rect(bubbles[i - 1]).bottom - 1),
      squeezed: bubbles.some(b => rect(b).width < 100),
      overflow: bubbles.some(b => b.scrollWidth > b.clientWidth + 1),
      aligned: bubbles.every(b => b.classList.contains('secretary-chat-researcher')
        ? Math.abs(rect(b).right - rect(log).right) < 2 : Math.abs(rect(b).left - rect(log).left) < 2),
      newest: host.scrollHeight - host.clientHeight - host.scrollTop < 3,
      composerOverflow: document.getElementById('secretary-compose').scrollWidth >
        document.getElementById('secretary-compose').clientWidth + 1
    };
  });
  assert.deepEqual(layout, {inComposer: 0, overlaps: false, squeezed: false, overflow: false,
    aligned: true, newest, composerOverflow: false});
}

try {
  await fixture.workspace();
  for (const language of ['en', 'ko']) {
    if (language === 'ko') {
      turns = [];
      await page.reload();
      await fixture.workspace();
    }
    await page.evaluate(lang => { WB_I18N.set(lang); S.composerMode = 'talk'; syncComposerMode(); WB_DECLUTTER_SHELL.openPanel('secretary'); }, language);
    for (const [index, reply] of [long[language], long[language], off[language], unfinished[language]].entries()) {
      nextReply = reply;
      release = null;
      await page.getByTestId('secretary-input').fill(language === 'ko' ? `허가 자료를 함께 검토해 주세요 ${index}` : `Review the permit evidence with me ${index}`);
      await page.getByTestId('secretary-send').click();
      await page.locator('#secretary-chat-history [role="status"]').filter({hasText: language === 'ko' ? '준비' : 'thinking'}).waitFor();
      assert.equal(await page.locator('#secretary-compose .secretary-chat-reply').count(), 0);
      assert.ok(release, 'the mocked request is held while the pending bubble is visible');
      release();
      await page.waitForFunction(() => !S.chatPending);
      await page.evaluate(() => refreshHomeSecretary());
      await page.waitForFunction(n => secretaryChatView.recorded.length === n, (index + 1) * 2);
      await checkLayout((index + 1) * 2);
    }
    // Repeated identical reply text still represents two distinct exchanges.
    assert.equal(await page.locator('#secretary-chat-history .secretary-chat-reply').count(), 4);
    assert.equal(await page.locator('#secretary-chat-history button').count(), 4);
    const position = await page.evaluate(() => document.getElementById('secretary-log').scrollTop);
    await page.locator('#secretary-chat-history details').last().evaluate(el => { el.open = true; });
    await page.evaluate(() => refreshHomeSecretary());
    await page.waitForResponse(r => r.url().includes('/secretary-turns'));
    assert.equal(await page.evaluate(() => document.getElementById('secretary-log').scrollTop), position,
      'opening a receipt and refreshing does not yank the view');
    await checkLayout(8, false);
    await page.locator('#secretary-chat-history details').last().evaluate(el => { el.open = false; });
    assert.equal(await page.locator('#secretary-mode-context').isVisible(), false, 'talk hint appears only once');
    for (const theme of ['light', 'dark']) {
      for (const [width, height] of [[1280, 860], [1440, 1000], [390, 844]]) {
        await page.setViewportSize({width, height});
        await page.emulateMedia({reducedMotion: 'reduce', contrast: 'more'});
        const cdp = await page.context().newCDPSession(page);
        await cdp.send('Emulation.setEmulatedMedia', {features: [
          {name: 'prefers-reduced-transparency', value: 'reduce'},
          {name: 'prefers-reduced-motion', value: 'reduce'}
        ]});
        assert.equal(await page.evaluate(() => matchMedia('(prefers-reduced-transparency: reduce)').matches), true);
        await page.evaluate(value => WB_THEME.set(value), theme);
        for (const surface of ['research', 'home']) {
          if (surface === 'home') await page.locator('#back-to-menu').click();
          else {
            if (await page.getByTestId('home-menu-research').isVisible())
              await page.getByTestId('home-menu-research').click();
            await page.evaluate(() => WB_DECLUTTER_SHELL.openPanel('secretary'));
          }
          await page.waitForFunction(surface => document.getElementById('secretary-chat-history').parentElement.id ===
            (surface === 'home' ? 'home-conversation' : 'secretary-log'), surface);
          await page.evaluate(() => scrollSecretaryChat());
          await page.locator('#secretary-chat-history').waitFor({state: 'visible'});
          await checkLayout(8);
          assert.equal(await page.locator(surface === 'home' ? '#home-secretary' : '#app-shell aside[aria-label="Research Secretary"]')
            .evaluate(el => getComputedStyle(el).backdropFilter), 'none');
          await page.screenshot({path: path.join(output, `${language}-${theme}-${width}-${surface}.png`)});
        }
        await cdp.detach();
      }
    }
    // Reload uses the authoritative recorded turns; live-only controls disappear.
    await page.reload();
    await fixture.workspace();
    await page.waitForFunction(() => document.querySelectorAll('#secretary-chat-history > *').length === 8);
    assert.equal(await page.locator('#secretary-chat-history button').count(), 0);
  }
  console.log('SECRETARY_CHAT_LAYOUT_PASS: ordered pending/live/recorded exchanges, no duplicates, wrapping and alignment on both surfaces in KO/EN, light/dark, 1280/1440/390, reload persistence; 24 screenshots');
} finally {
  await fixture.close();
}
