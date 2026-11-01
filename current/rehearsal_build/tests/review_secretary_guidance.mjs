// Secretary guidance UI review: per-project note + launch-row line.
// Runs the real frontend/app.js with a minimal DOM double. No browser, no network.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const appSource = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');

function makeContext(overrides = {}) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) {
      nodes.set(id, {
        id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
        files: [], dataset: {}, open: false,
        classList: { add() {}, remove() {} },
        setAttribute() {}, replaceChildren() {},
        querySelectorAll: () => [],
      });
    }
    return nodes.get(id);
  };
  let serial = 0;
  const ctx = {
    URLSearchParams, location: { search: '' }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => `g-${++serial}` },
    fetch: async () => { throw new Error('no network in guidance harness'); },
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      createElement: (tag) => ({ tag, dataset: {}, textContent: '', innerHTML: '', href: '', onclick: null }),
    },
    ...overrides,
  };
  return { ctx, node };
}

async function loadApp(overrides = {}) {
  const { ctx, node } = makeContext(overrides);
  const context = vm.createContext(ctx);
  vm.runInContext(appSource.slice(0, appSource.lastIndexOf('(async()=> {')), context);
  return { context, node };
}

const baseParts = () => ({
  where_you_left_off: [{ text: 'Left off' }],
  what_changed_since: [{ text: 'Changed' }],
  ready_now: [{ text: 'Ready' }],
  waiting_on_you: [{ text: 'Waiting' }],
  suggested_next_action: [{ text: 'Next' }],
});

// (a) firm guidance renders note section with escaped text and basis.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'firm', text: 'Consider closing Q2', basis: 'Two items verified', model_calls: 0 },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(out.includes("Secretary's note"), 'note section present');
  assert.ok(out.includes('Consider closing Q2'), 'guidance text shown');
  assert.ok(out.includes('Two items verified'), 'basis shown');
  assert.ok(out.includes('worth a look'), 'firm maps to "worth a look"');
  assert.ok(out.indexOf("Secretary's note") > out.indexOf('Suggested next action'), 'note after suggested action');
  assert.ok(out.includes('model calls: 0'), 'model-call line intact');
  console.log('GUIDANCE_NOTE_PASS');
}

// (b) HTML in guidance text is escaped.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'pointed', text: '<img src=x onerror=alert(1)>', basis: '<b>basis</b>', model_calls: 0 },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(!out.includes('<img src=x'), 'no raw injection');
  assert.ok(out.includes('&lt;img'), 'text escaped');
  assert.ok(out.includes('&lt;b&gt;basis&lt;/b&gt;'), 'basis escaped');
  console.log('GUIDANCE_ESCAPE_PASS');
}

// (c) launch rows: with guidance vs identical-without.
{
  const { context, node } = await loadApp();
  const row = { project_id: 'p1', project: 'Alpha', parts: { suggested_next_action: [{ text: 'Next step' }] }, waiting_count: 1 };
  context.r0 = [row];
  await vm.runInContext('renderLaunchBriefing(r0)', context);
  const before = node('launch-briefing').innerHTML;
  context.r1 = [{ ...row, guidance: { level: 'calm', text: 'Steady progress', basis: 'x', model_calls: 0 } }];
  await vm.runInContext('renderLaunchBriefing(r1)', context);
  const after = node('launch-briefing').innerHTML;
  assert.ok(!before.includes('Steady progress'), 'absent guidance renders as today');
  assert.ok(after.includes('Steady progress'), 'guidance text appended');
  assert.ok(after.includes('Next step'), 'existing line preserved');
  console.log('GUIDANCE_LAUNCH_PASS');
}

// (d) level labels.
{
  for (const [level, label] of [['calm', 'note'], ['firm', 'worth a look'], ['pointed', 'needs a decision']]) {
    const { context, node } = await loadApp();
    context.briefing_input = { model_calls: 0, parts: baseParts(), guidance: { level, text: 'T', basis: 'B', model_calls: 0 } };
    await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(node('briefing').innerHTML.includes(label), `${level} -> "${label}"`);
  }
  console.log('GUIDANCE_LEVELS_PASS');
}

// (e) missing guidance: no error, no empty note section.
{
  const { context, node } = await loadApp();
  context.briefing_input = { model_calls: 0, parts: baseParts() };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(!out.includes("Secretary's note"), 'no empty note section');
  for (const h of ['Where you left off', 'What changed since', 'Ready now', 'Waiting on you', 'Suggested next action']) {
    assert.ok(out.includes(h), `keeps "${h}"`);
  }
  console.log('GUIDANCE_ABSENT_PASS');
}

console.log('SECRETARY_GUIDANCE_UI_PASS');
