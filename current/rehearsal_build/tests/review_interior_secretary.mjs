// Research Secretary interior: the explicit message mode, the lead next action,
// unsent-word retention, and scroll behaviour. Runs the real frontend/app.js in
// a DOM double, plus the real stylesheet and companion script as source.
// No browser, no network, no backend, no model call, no cloud consent.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const appSource = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../frontend/interior_secretary.css', import.meta.url), 'utf8');
const companion = await readFile(new URL('../frontend/interior_secretary.js', import.meta.url), 'utf8');

// A DOM double rich enough for the composer: the form, its input and send
// button, the hint, and whatever the code appended to the form. Every created
// element carries its own children so the built control can be inspected.
// getElementById answers null for the three ids this feature is supposed to
// create, exactly as a browser does before they exist: an element invented for
// every id would make "has the control been built yet?" answer yes forever.
const NOT_YET_PRESENT = ['secretary-mode', 'secretary-mode-label', 'secretary-mode-context'];
function makeContext(overrides = {}) {
  const nodes = new Map();
  const created = [];
  function element(id) {
    const el = {
      id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
      className: '', children: [], options: [], dataset: {}, attributes: {},
      classList: { add() {}, remove() {}, contains: () => false },
      setAttribute(n, v) { this.attributes[n] = v; },
      getAttribute(n) { return this.attributes[n] ?? null; },
      appendChild(child) { this.children.push(child); if (child.tag === 'option') this.options.push(child); return child; },
      insertBefore(child) { this.children.unshift(child); return child; },
      addEventListener(name, fn) { this['on' + name] = fn; },
      focus() { this.focused = true; },
      replaceChildren() {},
      querySelectorAll: () => [],
    };
    return el;
  }
  const node = (id) => {
    if (NOT_YET_PRESENT.includes(id)) return created.find(el => el.id === id) || null;
    if (!nodes.has(id)) nodes.set(id, element(id));
    return nodes.get(id);
  };
  let serial = 0;
  function Option(text, value) { return { tag: 'option', textContent: text, value }; }
  function createElement(tag) {
    const el = element(''); el.tag = tag; created.push(el); return el;
  }
  function getById(id) { return node(id); }
  const ctx = {
    URLSearchParams, location: { search: '' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => `is-${++serial}` },
    fetch: async () => { throw new Error('no network in interior secretary harness'); },
    Option,
    document: { getElementById: getById, querySelectorAll: () => [], createElement },
    ...overrides,
  };
  // The composer path reads window.WB_CHIP_BUSY (another surface's lock) and the
  // language table; neither is present here, which is the point: no chip lock is
  // held, and text falls back to the authored English.
  ctx.window = ctx;
  return { ctx, node, created, getById };
}

async function loadApp(overrides = {}) {
  const { ctx, node, created, getById } = makeContext(overrides);
  const context = vm.createContext(ctx);
  vm.runInContext(appSource.slice(0, appSource.lastIndexOf('(async()=> {')), context);
  return { context, node, created, getById };
}

// (1) The control is built inside the existing form, with stable ids, and it is
// the only composer: nothing clones #secretary-input.
{
  const { context, node, getById, created } = await loadApp();
  await vm.runInContext(`S.project='p1'; S.cq=null; syncComposerMode();`, context);
  const form = node('secretary-compose');
  const wrap = form.children.find(c => c.className === 'secretary-mode');
  assert.ok(wrap, 'the mode control lives inside #secretary-compose, so it moves with the form');
  const select = wrap.children.find(c => c.id === 'secretary-mode');
  assert.ok(select, 'the select keeps a stable id');
  assert.equal(select.attributes['data-testid'], 'secretary-mode');
  assert.deepEqual(select.options.map(o => o.value), ['reply', 'new']);
  assert.equal(select.options[0].textContent, 'Answer current question', 'English by default');
  assert.equal(select.options[1].textContent, 'Start new research question');
  const inputKind = await vm.runInContext('typeof document.getElementById("secretary-input").value', context);
  assert.equal(inputKind, 'string', 'the one real input is untouched, never duplicated');
  assert.equal(created.filter(el => el.tag === 'input').length, 0, 'the control builds no second input');
  console.log('INTERIOR_MODE_CONTROL_PASS: built inside the single form, stable id/testid, two explicit modes');
}

// (2) Reply with nothing waiting is refused, explained and disabled — never
// silently turned into a fresh question.
{
  const { context, node, getById } = await loadApp();
  const posts = [];
  context.__posts = posts;
  await vm.runInContext(`S.project='p1'; S.cq=null; S.composerMode='reply'; syncComposerMode();`, context);
  assert.equal(await vm.runInContext('composerMode()', context), 'reply-unavailable');
  assert.equal(getById('secretary-send').disabled, true, 'send is off, not repurposed');
  assert.match(getById('secretary-mode-context').textContent, /Nothing was created/, 'the refusal is explained');
  node('secretary-input').value = 'Keep this wording';
  const before = await vm.runInContext('S.question', context);
  await vm.runInContext(`call = async (m, p, b) => { __posts.push([m, p, b]); throw new Error('must not be reached'); };
    sendSecretaryTurn({ preventDefault() {} });`, context);
  await new Promise(r => setTimeout(r, 30));
  assert.equal(posts.length, 0, 'a reply with no question waiting sends nothing at all');
  assert.equal(node('secretary-input').value, 'Keep this wording', 'the words stay in the box');
  assert.equal(await vm.runInContext('S.question', context), before, 'no question was created behind their back');
  console.log('INTERIOR_REPLY_UNAVAILABLE_PASS: disabled, explained, nothing sent, no silent question');
}

// (3) New mode creates the question through the existing path and leaves a
// waiting clarification of the same project untouched.
{
  const { context, node, getById } = await loadApp();
  const posts = [];
  context.__posts = posts;
  await vm.runInContext(`S.project='p1'; S.pack='social_science'; S.question='q-old'; S.cq='c1';
    S.composerMode='new'; syncComposerMode();
    // The real route shapes, reduced to what this path reads.
    call = async (m, p, b) => {
      __posts.push([m, p, b]);
      if (p.endsWith('/questions')) return { question_id: 'q-new', adapter: 'offline', refinement_status: 'ok', working_text: b.text };
      if (p.endsWith('/briefing')) return { parts: {}, model_calls: 0 };
      if (p.includes('/clarifications')) return { clarifications: [] };
      if (p.includes('/canvas')) return { nodes: [], edges: [], datasets: [] };
      if (p.includes('/preparations')) return { jobs: [], cards: [] };
      return {};
    };`, context);
  assert.equal(await vm.runInContext('composerMode()', context), 'question', 'new mode always starts a question');
  node('secretary-input').value = 'A different question';
  await vm.runInContext(`sendSecretaryTurn({ preventDefault() {} });`, context);
  await new Promise(r => setTimeout(r, 40));
  const questionPost = posts.find(p => p[1].endsWith('/questions'));
  assert.ok(questionPost, 'the question goes through the one existing submit path');
  assert.equal(questionPost[2].text, 'A different question');
  assert.equal(posts.filter(p => p[1].includes('/responses')).length, 0,
    'the waiting clarification is not answered by a new-question send');
  assert.equal(node('secretary-input').value, '', 'a successful send clears the box');
  console.log('INTERIOR_NEW_MODE_PASS: new mode uses the existing question path and leaves the clarification alone');
}

// (4) A failed send keeps the researcher's words: no clearing on failure.
{
  const { context, node, getById } = await loadApp();
  await vm.runInContext(`S.project='p1'; S.pack='x'; S.composerMode='new';
    call = async (m, p) => { if (p.endsWith('/questions')) throw new Error('HTTP 500'); return { parts: {}, clarifications: [], jobs: [], cards: [] }; };`, context);
  node('secretary-input').value = 'Do not lose this';
  await vm.runInContext(`sendSecretaryTurn({ preventDefault() {} });`, context);
  await new Promise(r => setTimeout(r, 40));
  assert.equal(node('secretary-input').value, 'Do not lose this',
    'a failed send leaves the typed words in the box');
  console.log('INTERIOR_UNSENT_WORDS_PASS: a failed send never clears the composer');
}

// (5) Reply mode answers the pending clarification through the existing route.
{
  const { context, node, getById } = await loadApp();
  const posts = [];
  context.__posts = posts;
  await vm.runInContext(`S.project='p1'; S.pack='x'; S.cq='c1'; S.question='q1'; S.questionText='Which date?';
    // A clarification surfaced by a run on q1: that is what makes S.question the
    // question this reply answers, so its wording is authoritative here.
    S.cqPrompt={gap:'Which date do you mean?'};
    S.composerMode='reply'; syncComposerMode();
    call = async (m, p, b) => {
      __posts.push([m, p, b]);
      if (p.includes('/responses')) return { state: 'decision_pending', note: 'ok', adapter: 'mock' };
      return { parts: {}, clarifications: [], jobs: [], cards: [] };
    };`, context);
  assert.equal(await vm.runInContext('composerMode()', context), 'clarification');
  assert.match(getById('secretary-mode-context').textContent, /Replying to: Which date\?/,
    'the question being answered is on screen');
  assert.equal(getById('secretary-send').disabled, false);
  node('secretary-input').value = 'The permit date';
  await vm.runInContext(`sendSecretaryTurn({ preventDefault() {} });`, context);
  await new Promise(r => setTimeout(r, 40));
  const answer = posts.find(p => p[1].includes('/responses'));
  assert.ok(answer, 'the answer uses the existing clarification route');
  assert.equal(answer[2].kind, 'answer');
  assert.equal(answer[2].value, 'The permit date');
  console.log('INTERIOR_REPLY_MODE_PASS: reply answers the pending question through the existing route');
}

// (6) The briefing leads with the next action; the rest is one disclosure away.
{
  const { context, node, getById } = await loadApp();
  context.__brief = {
    model_calls: 0,
    parts: {
      where_you_left_off: [{ text: 'Left off' }],
      what_changed_since: [{ text: 'Changed' }],
      ready_now: [{ text: 'Ready' }],
      waiting_on_you: [{ text: 'Waiting' }],
      suggested_next_action: [{ text: 'Check the date definition' }, { text: 'Second step' }],
    },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>__brief; loadBriefing();`, context);
  await new Promise(r => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  const nextAt = out.indexOf('Check the date definition');
  const detailAt = out.indexOf('Saved briefing');
  assert.ok(nextAt >= 0, 'the next required action is rendered');
  assert.ok(detailAt > nextAt, 'it comes before the saved briefing');
  assert.ok(out.includes('<details class="brief-saved" open>'), 'the saved cards start expanded inside a usable disclosure');
  assert.equal((out.match(/Check the date definition/g) || []).length, 1, 'the lead line is not repeated');
  for (const line of ['Left off', 'Changed', 'Ready', 'Waiting', 'Second step']) {
    assert.ok(out.includes(line), `the saved record keeps "${line}"`);
  }
  for (const h of ['Where you left off', 'What changed since', 'Ready now', 'Waiting on you', 'Suggested next action']) {
    assert.ok(out.includes(h), `the section heading "${h}" is kept`);
  }
  assert.ok(out.includes('model calls: 0'), 'the model-call disclosure is untouched');
  console.log('INTERIOR_BRIEFING_LEAD_PASS: next action first, saved record disclosed, headings and count kept');
}

// (7) Coaching renders once per render, and no render auto-scrolls a reader who
// is not at the newest end.
{
  const { context, node, getById } = await loadApp();
  context.__brief = {
    model_calls: 0, parts: { suggested_next_action: [{ text: 'Next' }] },
    guidance: { level: 'firm', text: 'Consider closing Q2', basis: 'Basis', model_calls: 0 },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>__brief; loadBriefing();`, context);
  await new Promise(r => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.equal((out.match(/Consider closing Q2/g) || []).length, 1, 'the coaching note is not repeated within one render');

  // A reader scrolled up into the saved briefing must stay where they are.
  const log = node('secretary-log');
  log.scrollHeight = 2000; log.clientHeight = 400; log.scrollTop = 120;
  await vm.runInContext(`call=async()=>__brief; loadBriefing();`, context);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(log.scrollTop, 120, 'reading older content is not interrupted by a scroll');
  // A reader already at the newest end follows new turns.
  log.scrollTop = 1600;
  await vm.runInContext(`call=async()=>__brief; loadBriefing();`, context);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(log.scrollTop, 2000, 'a reader at the newest end still follows it');
  console.log('INTERIOR_NO_SCROLL_PASS: coaching once per render, no pull away from older content');
}

// (8) The stylesheet uses existing tokens only, and the approved reading sizes.
{
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(css), 'no literal colour: light and dark both come from the tokens');
  assert.ok(css.includes('var(--surface)') || css.includes('var(--bg)') || css.includes('var(--text)'),
    'colour comes from the shared tokens');
  assert.ok(/--secretary-base:\s*15px/.test(css), 'workspace text is 15px');
  assert.ok(/--secretary-target:\s*44px/.test(css), 'targets are 44px');
  assert.ok(/padding:\s*26px 20px/.test(css), 'the aside keeps the approved 26x20 padding');
  assert.ok(!/gradient|box-shadow|blur\(|glow/i.test(css), 'flat surfaces only, as the direction asks');
  assert.ok(!/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(css + companion), 'no emoji');
  assert.ok(!/\u2014/.test(css + companion), 'no U+2014 in authored prose');
  console.log('INTERIOR_CSS_TOKENS_PASS: tokens only, 15px/44px/26x20, flat, no emoji, no U+2014');
}

// (9) The companion never decides anything: it only re-syncs the composer.
{
  assert.ok(companion.includes('window.syncComposerMode'), 'it calls the one owner of the composer mode');
  assert.ok(!/innerHTML\s*=/.test(companion), 'it writes no markup of its own');
  assert.ok(!/createElement\('input'\)/.test(companion), 'it never builds a second input');
  assert.ok(!/fetch\(/.test(companion), 'it makes no request');
  const calls = [];
  const context = vm.createContext({
    document: { readyState: 'complete', getElementById: () => null, querySelector: () => null, addEventListener() {} },
    window: { syncComposerMode: () => calls.push('sync') },
    MutationObserver: undefined,
  });
  vm.runInContext(companion, context);
  assert.deepEqual(calls, ['sync'], 'loading it syncs once and then stops');
  console.log('INTERIOR_COMPANION_PASS: one sync per load, no markup, no request, no second input');
}

// (10) The no-yank rule covers every render that can land while someone is
// reading: pending clarifications and the 1200 ms background preparation poll,
// not only the saved briefing. Position is captured BEFORE the DOM write.
for (const kind of ['pending', 'preparations']) {
  for (const atBottom of [false, true]) {
    const { context, node } = await loadApp();
    const log = node('secretary-log');
    log.scrollHeight = 2000; log.clientHeight = 400; log.scrollTop = atBottom ? 1600 : 120;
    await vm.runInContext(`S.project='p1'; S.pack='x'; S.question='q1';
      call = async (m, p) => p.includes('/clarifications') ? { clarifications: [] }
        : p.includes('/preparations') ? { jobs: [{ id: 'j1', status: 'running' }], cards: [] }
        : { parts: {}, model_calls: 0 };`, context);
    await vm.runInContext(`${kind === 'pending' ? 'loadPending' : 'loadPreparations'}();`, context);
    await new Promise(r => setTimeout(r, 40));
    const box = node(kind === 'pending' ? 'pending' : 'briefing');
    assert.ok(box.innerHTML.length > 0, `the ${kind} render really wrote markup, so the scroll check is not vacuous`);
    assert.equal(log.scrollTop, atBottom ? 2000 : 120,
      `a reader ${atBottom ? 'at the newest end follows' : 'reading older content is not pulled by'} the ${kind} render`);
  }
}
console.log('INTERIOR_NO_YANK_ALL_RENDERS_PASS: pending and the preparation poll obey the position read before the write');

// (11) With two waiting clarifications, the reply names the one it is sent to,
// never the first item, and never an unrelated S.questionText.
{
  const { context, getById } = await loadApp();
  await vm.runInContext(`S.project='p1'; S.pack='x'; S.cq='c2'; S.question='q-b';
    S.questionText='An unrelated open question';
    S.pending=[{cq_id:'c1',question_id:'q-a'},{cq_id:'c2',question_id:'q-b'}];`, context);
  await vm.runInContext(`savedCanvasQuestions=[{id:'q-a',original_text:'First waiting question'},{id:'q-b',original_text:'Second waiting question'}];
    S.composerMode='reply'; syncComposerMode();`, context);
  const wording = await vm.runInContext('composerTargetWording()', context);
  assert.equal(wording, 'Second waiting question', 'the answered clarification supplies the wording, not pending[0]');
  assert.match(getById('secretary-mode-context').textContent, /Replying to: Second waiting question/);
  // No wording on record for the answered question, and the researcher has since
  // opened a different one: a truthful generic label, never another question's
  // text and never the unrelated S.questionText.
  const generic = await vm.runInContext(`savedCanvasQuestions=[{id:'q-a',original_text:'First waiting question'}];
    S.question='q-other'; S.questionText='An unrelated open question';
    composerTargetWording();`, context);
  assert.equal(generic, null, 'unavailable wording stays unavailable');
  const shown = await vm.runInContext(`syncComposerMode(); document.getElementById('secretary-mode-context').textContent;`, context);
  assert.match(shown, /Replying to: the question this project is waiting on/,
    'the generic pending label is used instead of a false wording');
  assert.ok(!/An unrelated open question|First waiting question/.test(shown), 'no other question is named');
  console.log('INTERIOR_PENDING_WORDING_PASS: reply names the clarification it answers, generic label when unknown');
}

console.log('INTERIOR_SECRETARY_PASS: explicit modes inside the single composer form, reply refused honestly, unsent words kept, next action leads, no auto-scroll, tokens only');
