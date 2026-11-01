// Secretary panel review: return briefing first, launch rows, per-project briefing.
// Runs the real frontend/index.html + frontend/app.js with a minimal DOM double.
// No browser, no network, no backend.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const root = new URL('../', import.meta.url);
const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const appSource = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');

function makeNodes() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) {
      const el = {
        id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
        files: [], dataset: {}, open: false,
        classList: { add() {}, remove() {} },
        setAttribute() {}, replaceChildren() {},
        querySelectorAll: () => [],
      };
      nodes.set(id, el);
    }
    return nodes.get(id);
  };
  return { nodes, node };
}

function makeContext(overrides = {}) {
  const { nodes, node } = makeNodes();
  let serial = 0;
  return {
    ctx: {
      URLSearchParams, location: { search: '' }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      crypto: { randomUUID: () => `launch-${++serial}` },
      fetch: async () => { throw new Error('no network in secretary harness'); },
      document: {
        getElementById: node,
        querySelectorAll: () => [],
        createElement: (tag) => ({ tag, dataset: {}, textContent: '', innerHTML: '', href: '', onclick: null }),
      },
      ...overrides,
    },
    nodes,
    node,
  };
}

// (1) Document order: briefing and pending come before ai-panel in the Secretary aside.
{
  const start = html.indexOf('<aside aria-label="Research Secretary">');
  assert.ok(start >= 0, 'the Secretary aside is present');
  const aside = html.slice(start, html.indexOf('</aside>', start));
  for (const needle of ['id="launch-briefing"', 'id="briefing"', 'id="pending"', 'data-testid="refresh-briefing"', 'id="ai-panel"']) {
    assert.ok(aside.includes(needle), `Secretary aside must contain ${needle}`);
  }
  const pos = (n) => aside.indexOf(n);
  assert.ok(pos('id="launch-briefing"') < pos('id="briefing"'), 'launch briefing before per-project briefing');
  assert.ok(pos('id="briefing"') < pos('id="pending"'), 'briefing before pending');
  assert.ok(pos('data-testid="refresh-briefing"') < pos('id="ai-panel"'), 'refresh before advanced panel');
  assert.ok(pos('id="saved-canvases"') < pos('id="ai-panel"'), 'saved questions before advanced panel');
  assert.ok(html.includes('Advanced: optional Meta AI tasks'), 'advanced panel summary marks the cloud tasks as advanced/optional');
  assert.ok(html.includes('onclick="loadBriefing()"'), 'refresh briefing handler preserved');
  console.log('SECRETARY_ORDER_PASS: launch, briefing, pending, refresh, saved, advanced panel');
}

import vm from 'node:vm';

async function loadApp(overrides = {}) {
  const { ctx, node } = makeContext(overrides);
  const context = vm.createContext(ctx);
  const source = appSource.slice(0, appSource.lastIndexOf('(async()=> {'));
  vm.runInContext(source, context);
  return { context, node };
}

// (2) Launch fetch renders one escaped row per project.
{
  const { context, node } = await loadApp();
  const rows = [
    { project_id: 'p1', project: 'Alpha <b>study</b>', parts: { suggested_next_action: [{ text: 'Review <deferred> decision' }] }, waiting_count: 2, top_items: [{ text: 'Ignored decoy' }] },
    { project_id: 'p2', project: 'Beta', parts: { suggested_next_action: [] }, top_items: ['Finish "quotes" & notes'], counts: {} },
  ];
  context.launch_input = rows;
  await vm.runInContext('renderLaunchBriefing(launch_input)', context);
  const out = node('launch-briefing').innerHTML;
  assert.ok(out.includes('Alpha &lt;b&gt;study&lt;/b&gt;'), 'project name escaped');
  assert.ok(!out.includes('<b>study</b>'), 'no raw markup from project name');
  assert.ok(out.includes('Review &lt;deferred&gt; decision'), 'suggested next action escaped');
  assert.ok(out.includes('2 waiting'), 'waiting count shown');
  assert.ok(out.includes('Finish &quot;quotes&quot; &amp; notes'), 'top item fallback escaped');
  assert.ok(out.includes('onclick="openLaunchProject(0)"'), 'row opens via openLaunchProject');
  assert.ok(out.includes('onclick="openLaunchProject(1)"'), 'second row opens via openLaunchProject');
  console.log('LAUNCH_RENDER_PASS: rows escaped, waiting shown, open hooks present');
}

// (3) Using a launch row calls the existing openProject path / state.
{
  const { context } = await loadApp();
  context.launch_input = [{ project_id: 'p9', project: 'Gamma' }];
  await vm.runInContext('renderLaunchBriefing(launch_input)', context);
  const calls = [];
  context.openProject = (id, name) => { calls.push([id, name]); };
  await vm.runInContext('openLaunchProject(0)', context);
  assert.deepEqual(calls, [['p9', 'Gamma']], 'launch row uses the existing openProject path');
  console.log('LAUNCH_OPEN_PASS: launch row calls openProject with stored id and name');
}

// (4) Per-project loadBriefing still renders the five saved-record parts.
{
  const { context, node } = await loadApp();
  const briefing = {
    model_calls: 0,
    parts: {
      where_you_left_off: [{ text: 'Left off' }],
      what_changed_since: [{ text: 'Changed' }],
      ready_now: [{ text: 'Ready' }],
      waiting_on_you: [{ text: 'Waiting' }],
      suggested_next_action: [{ text: 'Next' }],
    },
  };
  context.briefing_input = briefing;
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  for (const h of ['Where you left off', 'What changed since', 'Ready now', 'Waiting on you', 'Suggested next action']) {
    assert.ok(out.includes(h), `per-project briefing keeps "${h}"`);
  }
  for (const t of ['Left off', 'Changed', 'Ready', 'Waiting', 'Next']) {
    assert.ok(out.includes(t), `per-project briefing keeps text "${t}"`);
  }
  assert.ok(out.includes('model calls: 0'), 'model call count still disclosed');
  console.log('PER_PROJECT_BRIEFING_PASS: five parts and model-call disclosure intact');
}

// (5) Absent or failing /briefing must not throw and must keep prior flow working.
{
  const { context, node } = await loadApp();
  node('launch-briefing').innerHTML = 'prior';
  await vm.runInContext(`call=async()=>{ throw new Error('no server'); }; loadLaunchBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('launch-briefing').innerHTML;
  assert.ok(out.toLowerCase().includes('unavailable'), 'launch area shows a short unavailable note');
  console.log('LAUNCH_FAILURE_PASS: missing /briefing fails quietly without throwing');
}

console.log('SECRETARY_PANEL_PASS: ordering, escaped launch rows, openProject path, per-project briefing, quiet failure');
