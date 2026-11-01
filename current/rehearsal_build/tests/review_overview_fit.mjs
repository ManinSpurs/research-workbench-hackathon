import assert from 'node:assert/strict';
import { interiorFixture } from './interior_fixture.mjs';

// Real app and stylesheet chain, synthetic transport only.
const f = await interiorFixture();
try {
  const { page } = f;
  await f.workspace();
  await page.route('http://interior.test/projects/p1/drafts', route => route.fulfill({
    json: { drafts: [{ draft_id: 'd1', title: 'Synthetic measurement study' }] }
  }));
  const puts = [];
  let draft = { draft_id: 'd1', project_id: 'p1', question_id: null,
      title: 'Synthetic measurement study', engine: 'auto', main_file: 'main.tex', revision: 3,
      files: [{ path: 'main.tex', text: '\\documentclass{article}\n\\begin{document}\n' +
        'Synthetic registered measurement discussion.\n'.repeat(150) + '\\end{document}\n' }],
      sections: [], previous: null, created_at: 'x', updated_at: 'x', model_calls: 0 };
  await page.route('http://interior.test/drafts/d1', route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      assert.equal(body.expected_revision, draft.revision);
      puts.push(body);
      draft = { ...draft, files: body.files, revision: draft.revision + 1 };
    }
    return route.fulfill({ json: draft });
  });
  assert.ok(await page.evaluate(() => {
    const links = [...document.querySelectorAll('link[rel="stylesheet"]')];
    return links.at(-1)?.href.endsWith('/overview_fit.css') &&
      [...document.styleSheets].some(s => s.href?.endsWith('/overview_fit.css'));
  }), 'real index loads overview_fit.css last');
  const inView = async selector => {
    // A panel slides out when the workspace opens; sampling geometry mid-slide
    // measured the drawer on its way past, not the settled layout. Wait for the
    // element to actually be the thing a pointer reaches at its own centre, then
    // assert that: a control that stays covered still fails.
    await page.waitForFunction(sel => {
      const el = document.querySelector(sel);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      if (!(r.height > 0 && r.y >= 0 && r.y + r.height <= innerHeight + 1)) return false;
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return !!hit && (hit === el || el.contains(hit));
    }, selector, { timeout: 5000 }).catch(() => {});
    const box = await page.locator(selector).boundingBox();
    assert.ok(box && box.height > 0 && box.y >= 0 &&
      box.y + box.height <= page.viewportSize().height + 1, selector);
    const section = await page.locator('#app-shell > main > section').boundingBox();
    assert.ok(box.x >= section.x - 1 && box.x + box.width <= section.x + section.width + 1 &&
      box.y >= section.y - 1 && box.y + box.height <= section.y + section.height + 1,
    `${selector} must be inside the section clipping rectangle`);
    const hitTest = await page.locator(selector).evaluate(el => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return { clear: hit === el || el.contains(hit), hit: hit?.outerHTML.slice(0, 240) };
    });
    assert.ok(hitTest.clear, `${selector} must not be covered: ${hitTest.hit}`);
  };
  const bounded = async () => {
    assert.ok(await page.locator('#app-shell').evaluate(el =>
      el.scrollHeight <= el.clientHeight + 1), 'workspace must not scroll as a whole');
  };
  // The fixture's page is created 1400px wide; resizing it needs one reflow
  // before geometry is meaningful. Waiting for the shell to match the viewport
  // replaces the implicit wait a Playwright click used to give us.
  const settle = () => page.waitForFunction(() =>
    Math.round(document.getElementById('app-shell').getBoundingClientRect().width) === innerWidth);
  for (const viewport of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => showResearchView());
    await settle();
    // H1 made the clarity pane Research's single statement, so the purpose
    // heading and the New question action asserted here are the pane's, not the
    // shipped intro's. #research-intro is still in the document with its ids (for
    // the other contracts that read it) but it is deliberately not painted, so
    // asserting its box here would assert a screen the user does not get.
    await inView('[data-testid="clarity-title-research"]');
    await inView('#clarity-primary-research');
    await page.evaluate(() => {
      S.question = 'q-fit'; S.questionText = 'Does recorded approval timing identify the intervention?';
      renderResult({ result_kind: 'EvidenceAssessment', outcome: 'partial',
        assessment: { claim: 'The recorded approval date alone does not verify intervention timing.',
          outcome: 'partial', confirmation_level: 'human review required',
          unresolved: ['Check source release compatibility and the implementation-date variable.'],
          suggested_next_action: 'Inspect the registered source before adopting the proposal.' },
        evidence: Array.from({ length: 16 }, (_, i) => ({ source_id: `synthetic-source-${i}`,
          source_version: 'fixture-release-1', location: `page ${i + 1}`,
          excerpt: 'Approval and implementation are distinct events. The registered date needs researcher verification.' })) });
    });
    await inView('#result');
    await inView('#decision');
    for (const action of ['adopt', 'defer', 'reject']) await inView(`[data-testid="${action}"]`);
    assert.ok(await page.locator('#result').evaluate(el => el.scrollHeight > el.clientHeight),
      'long evidence scrolls inside result');
    assert.ok(await page.locator('#app-shell > main > section').evaluate(el =>
      el.scrollTop === 0 && el.scrollHeight <= el.clientHeight + 1),
    'populated Research needs no section scrolling');
    assert.ok(await page.getByTestId('evidence').textContent().then(t => t.includes('needs researcher verification')));
    await page.locator('#clarity-primary-research').click();
    await inView('#question');
    await inView('[data-testid="ask"]');
    const privacy = '#ask-form > p.muted:last-of-type';
    await inView(privacy);
    assert.match(await page.locator(privacy).textContent(), /stored on disk/);
    assert.equal(await page.locator('#app-shell > main > section').evaluate(el => el.scrollTop), 0);
    await page.locator('.wb-dc-support > summary').click();
    for (const action of ['adopt', 'defer', 'reject']) await inView(`[data-testid="${action}"]`);
    await bounded();
    assert.equal(await page.locator('#research-tool-code').isDisabled(), true);
    await page.evaluate(() => openLibraryView());
    await page.waitForFunction(() => libraryState.tree !== null && !libraryState.loading);
    await inView('#library-search');
    await bounded();
    // Fill existing content hosts with long synthetic records. This isolates
    // layout from provider/database availability without inventing app controls.
    await page.locator('#library-items').evaluate(el => {
      for (let i = 0; i < 60; i++) {
        const row = document.createElement('li');
        row.className = 'library-item-row';
        row.textContent = `Synthetic paper ${i}: measurement timing and identification`;
        el.append(row);
      }
    });
    assert.ok(await page.locator('.library-index').evaluate(el => el.scrollHeight > el.clientHeight));
    await page.locator('#quiet-secretary').click();
    await page.locator('#view-secretary-review').click();
    await page.waitForFunction(() => secretaryReviewState?.loaded && !secretaryReviewState.loading);
    await page.waitForFunction(() =>
      document.querySelector('#app-shell > main > aside').getBoundingClientRect().left >= innerWidth);
    await page.locator('#secretary-review-groups').evaluate(el => {
      for (let i = 0; i < 50; i++) {
        const p = document.createElement('p');
        p.textContent = `Recorded exchange ${i}: confirm the registered variable and retain the source warning.`;
        el.append(p);
      }
    });
    await inView('.secretary-review-head');
    assert.ok(await page.locator('#secretary-review-groups').evaluate(el => el.scrollHeight > el.clientHeight));
    await bounded();
    await page.evaluate(() => openWritingView());
    await page.waitForFunction(() => document.querySelector('#latex-editor').value.includes('Synthetic registered'));
    await inView('#latex-compile');
    await inView('#latex-editor');
    assert.ok(await page.locator('#latex-editor').evaluate(el => el.scrollHeight > el.clientHeight));
    await bounded();
    assert.equal(await page.locator('#latex-compile').isDisabled(), true);
    const edited = await page.locator('#latex-editor').inputValue() + `% Unsaved viewport ${viewport.width}\n`;
    await page.locator('#latex-editor').fill(edited);
    assert.ok(await page.evaluate(() => latexState.dirty), 'real input handler marks draft dirty');
    await page.evaluate(() => showResearchView());
    assert.equal(await page.locator('#latex-view').isVisible(), false, 'inactive writing stays hidden');
    await page.evaluate(() => openWritingView());
    assert.equal(await page.locator('#latex-editor').inputValue(), edited, 'view switch preserves draft text');
    await page.evaluate(() => saveDraft('manual'));
    assert.equal(puts.at(-1).files[0].text, edited, 'real save handler sends the preserved buffer');
    assert.equal(await page.evaluate(() => latexState.dirty), false);
    await page.evaluate(() => showResearchView());
  }
  await page.setViewportSize({ width: 375, height: 812 });
  await bounded();
  await page.locator('#clarity-primary-research').click();
  for (const selector of ['#question', '#ask-form > p.muted:last-of-type', '[data-testid="ask"]']) {
    await page.locator(selector).scrollIntoViewIfNeeded();
    await inView(selector);
  }
  await page.locator('#question').fill('Synthetic phone question: verify recorded timing');
  assert.equal(await page.locator('#question').inputValue(), 'Synthetic phone question: verify recorded timing');
  console.log('PASS overview fit: desktop section clipping/Ask privacy, populated research, draft preservation; phone reachable form');
} finally {
  await f.close();
}
