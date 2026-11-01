// Slice D4 adaptation-lines review: renderLaunchAdaptationLines will carry ONLY
// the D2 readiness composer's own lines (app/adaptive_readiness.py `row["lines"]`,
// plain human-readable readiness lines) into each launch-briefing row once the
// backend wiring slice lands; until then the mount renders nothing.
// Runs the real frontend/app.js with a minimal DOM double.
// No browser, no network, no backend, no model calls.
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
    crypto: { randomUUID: () => `d4-${++serial}` },
    fetch: async () => { throw new Error('no network in adaptation-lines harness'); },
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

// (1) Payload WITH lines: every line renders verbatim, cold state as cold.
{
  const { context, node } = await loadApp();
  context.rows_with = [{
    project_id: 'p1', project: 'Alpha',
    parts: { suggested_next_action: [{ text: 'Next step' }] }, waiting_count: 0,
    lines: [
      'Readiness for project p1 + pack social_science: cold.',
      'Counts: 0 eligible records (confirmed decisions 0, evidence cards 0); 0 ineligible inputs ignored.',
      'Guidance text only; grants no tools, scope, or budget.',
    ],
  }];
  await vm.runInContext('renderLaunchBriefing(rows_with)', context);
  const out = node('launch-briefing').innerHTML;
  for (const line of context.rows_with[0].lines) {
    assert.ok(out.includes(line), `renders line verbatim: ${line.slice(0, 40)}…`);
  }
  assert.ok(out.includes('Next step'), 'today fields render as before');
  console.log('ADAPTATION_LINES_PRESENT_PASS');
}

// (2) Payload WITHOUT the field: no adaptation block, existing rows unchanged.
{
  const { context, node } = await loadApp();
  assert.equal(vm.runInContext('renderLaunchAdaptationLines({})', context), '', 'absent field returns empty string');
  assert.equal(vm.runInContext('renderLaunchAdaptationLines({lines: []})', context), '', 'empty array returns empty string');
  assert.equal(vm.runInContext('renderLaunchAdaptationLines({lines: "cold"})', context), '', 'non-array field returns empty string');
  context.rows_plain = [{ project_id: 'p2', project: 'Beta', parts: { suggested_next_action: [{ text: 'Next step' }] }, waiting_count: 1 }];
  await vm.runInContext('renderLaunchBriefing(rows_plain)', context);
  const out = node('launch-briefing').innerHTML;
  assert.ok(!out.includes('Adaptation'), 'no adaptation heading without the field');
  assert.ok(!/maturity|benchmark|deadline|active/i.test(out), 'no adaptation claim words without the field');
  assert.ok(out.includes('Next step') && out.includes('1 waiting'), 'today fields render as before');
  console.log('ADAPTATION_LINES_ABSENT_PASS');
}

// (3) Approved line with benchmark refs renders them verbatim.
{
  const { context, node } = await loadApp();
  const approved = 'Overlay v3 active for new work only; old work unchanged. Evidence refs: d7, e2. Benchmark refs: frozen-a, frozen-b.';
  assert.equal(vm.runInContext('typeof renderLaunchAdaptationLines', context), 'function', 'renderLaunchAdaptationLines is the single D4 mount point');
  context.rows_approved = [{
    project_id: 'p3', project: 'Gamma',
    parts: { suggested_next_action: [{ text: 'Next step' }] },
    lines: ['Readiness for project p3 + pack social_science: approved.', approved],
  }];
  await vm.runInContext('renderLaunchBriefing(rows_approved)', context);
  const out = node('launch-briefing').innerHTML;
  assert.ok(out.includes(approved), 'approved version + benchmark refs rendered verbatim');
  assert.ok(out.includes('frozen-a, frozen-b'), 'benchmark refs present');
  console.log('ADAPTATION_LINES_APPROVED_PASS');
}

// (4) Malicious string in a line is escaped, never injected as HTML.
{
  const { context, node } = await loadApp();
  context.rows_evil = [{
    project_id: 'p4', project: 'Delta',
    parts: { suggested_next_action: [{ text: 'Next step' }] },
    lines: ['Readiness for project p4 + pack social_science: cold.', '<img src=x onerror=alert(1)>'],
  }];
  await vm.runInContext('renderLaunchBriefing(rows_evil)', context);
  const out = node('launch-briefing').innerHTML;
  assert.ok(out.includes('&lt;img src=x onerror=alert(1)&gt;'), 'malicious line escaped');
  assert.ok(!out.includes('<img src=x onerror=alert(1)>'), 'no raw markup injected');
  assert.ok(!out.includes('style='), 'no inline styling');
  assert.ok(!/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(out), 'no emoji');
  assert.ok(!/gradient|glow|glassmorphism|✨|🚀/i.test(out), 'no AI marketing styling');
  console.log('ADAPTATION_LINES_ESCAPE_PASS');
}

console.log('ADAPTATION_LINES_PASS: present verbatim, absent renders nothing, approved refs intact, malicious input escaped');
