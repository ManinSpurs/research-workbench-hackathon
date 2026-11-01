// Secretary coaching/radar UI review (inventory item C4, S4 frontend part).
// Runs the real frontend/index.html + frontend/app.js with a minimal DOM double.
// No browser, no network, no backend, no model calls.
// Coaching lines render ONLY from briefing payload fields that exist today
// (guidance note, top items, waiting counts). The radar feed (stale flags,
// ready_now card lines) lands in parallel backend work (S2); while those
// fields are absent the UI must simply not render that block — never
// placeholder flags, fake stale warnings, or sample data.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
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
    crypto: { randomUUID: () => `c-${++serial}` },
    fetch: async () => { throw new Error('no network in coaching/radar harness'); },
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

// Tokens the UI must never fabricate: no deadline wording (D1 does not exist
// yet), no stale/neglect claims (S2 radar feed not landed), no scores.
const FABRICATED = ['deadline', 'due', 'overdue', 'stale', 'needs attention', 'neglected', 'untouched for',
  'productivity', 'health score', 'sample', 'lorem'];

// (1) Guidance renders from the fixture payload's own fields only.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'calm', text: 'Steady: decision recorded', basis: 'One decision on record', model_calls: 0 },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(out.includes('Steady: decision recorded'), 'guidance text shown');
  assert.ok(out.includes('One decision on record'), 'basis shown');
  for (const h of ['Where you left off', 'What changed since', 'Ready now', 'Waiting on you', 'Suggested next action']) {
    assert.ok(out.includes(h), `keeps "${h}"`);
  }
  console.log('COACHING_GUIDANCE_PASS');
}

// (2) Absent radar fields: no radar block at all; the existing briefing lines
// and model-call line stay intact (observable current-contract assertion).
{
  const { context, node } = await loadApp();
  context.briefing_input = { model_calls: 0, parts: baseParts() };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(!out.includes('Project radar'), 'no radar block without radar fields');
  assert.ok(!out.includes("Secretary's note"), 'no empty note section');
  for (const t of ['Left off', 'Changed', 'Ready', 'Waiting', 'Next']) {
    assert.ok(out.includes(t), `keeps text "${t}"`);
  }
  assert.ok(out.includes('model calls: 0'), 'model-call line intact');
  console.log('COACHING_ABSENT_RADAR_PASS');
}

// (2b) Empty guidance shapes render no note section at all: {} and
// missing/blank text carry no recorded guidance text, so no header-only
// "Secretary's note" block may appear. Recorded text with absent basis still
// renders (text is the required field); absent/blank text renders nothing.
{
  const shapes = [{}, { level: 'calm' }, { level: 'firm', text: '' }, { level: 'calm', text: '   ' }, { text: '  ' }];
  for (const guidance of shapes) {
    const { context, node } = await loadApp();
    context.briefing_input = { model_calls: 0, parts: baseParts(), guidance };
    await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
    await new Promise((r) => setTimeout(r, 0));
    const out = node('briefing').innerHTML;
    assert.ok(!out.includes("Secretary's note"), `no header-only note for ${JSON.stringify(guidance)}`);
    assert.ok(out.includes('model calls: 0'), 'model-call line intact');
  }
  const { context } = await loadApp();
  const textOnly = vm.runInContext(`renderCoachingLine({guidance:{level:'calm',text:'Recorded line'}})`, context);
  assert.ok(textOnly.includes("Secretary's note") && textOnly.includes('Recorded line'), 'text-only guidance renders');
  assert.ok(!textOnly.includes('<p class="muted"></p>'), 'no empty basis paragraph');
  assert.equal(vm.runInContext(`renderCoachingLine({guidance:{}})`, context), '', 'empty guidance object renders nothing');
  assert.equal(vm.runInContext(`renderCoachingLine({guidance:{level:'calm'}})`, context), '', 'missing text renders nothing');
  assert.equal(vm.runInContext(`renderCoachingLine({guidance:{level:'calm',text:'  '}})`, context), '', 'blank text renders nothing');
  assert.equal(vm.runInContext(`renderCoachingLine(null)`, context), '', 'missing briefing renders nothing');
  assert.equal(vm.runInContext(`renderLaunchCoachingLine({guidance:{}})`, context), '', 'launch: empty guidance renders nothing');
  console.log('COACHING_EMPTY_GUIDANCE_PASS');
}

// (3) No fabricated stale/deadline wording anywhere in rendered text.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'firm', text: 'Two items waiting', basis: 'Oldest waiting age is unknown', model_calls: 0 },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const lowered = node('briefing').innerHTML.toLowerCase();
  for (const token of FABRICATED) {
    assert.ok(!lowered.includes(token), `no fabricated token "${token}"`);
  }
  console.log('COACHING_NO_FABRICATION_PASS');
}

// (4) Radar mount points: one named function per surface with absent-field
// guards. renderRadarBlock() is the per-project briefing mount (renders stale
// flags plus the designed ready_now card lines); renderLaunchRadarLine() is
// the launch-surface mount (renders the designed stale-flag subset only: per
// current/SECRETARY_COACHING_PROJECT_RADAR_EN.md §§4-5, ready_now card
// lines/counts belong to the per-project briefing, so the launch mount must
// not silently drop payload fields it claims to support — it states its
// designed subset and renders it). Absent fields render nothing on both.
{
  const { context, node } = await loadApp();
  const perProject = vm.runInContext('typeof renderRadarBlock', context);
  const launch = vm.runInContext('typeof renderLaunchRadarLine', context);
  assert.equal(perProject, 'function', 'renderRadarBlock is the per-project radar mount point');
  assert.equal(launch, 'function', 'renderLaunchRadarLine is the launch radar mount point');
  context.rows_plain = [{ project_id: 'p1', project: 'Alpha', parts: { suggested_next_action: [{ text: 'Next step' }] }, waiting_count: 1 }];
  await vm.runInContext('renderLaunchBriefing(rows_plain)', context);
  const before = node('launch-briefing').innerHTML;
  assert.ok(!before.toLowerCase().includes('stale'), 'plain launch rows carry no radar block');
  assert.ok(before.includes('Next step') && before.includes('1 waiting'), 'recorded top item and waiting count render');
  const none = vm.runInContext(`renderRadarBlock({})`, context);
  assert.equal(none, '', 'absent radar returns empty string');
  const empty = vm.runInContext(`renderRadarBlock({radar: {}})`, context);
  assert.equal(empty, '', 'empty radar object returns empty string');
  // Launch mount renders its designed stale-flag subset; ready_now cards are
  // not part of that subset and render nothing there by design (they render
  // in the per-project mount).
  const launchOut = vm.runInContext(
    `renderLaunchRadarLine({radar:{stale_flags:[{project:'Alpha',text:'Old item',waiting_count:2}],ready_now_cards:[{text:'Card line'}]}})`,
    context,
  );
  assert.ok(launchOut.includes('Old item') && launchOut.includes('2 waiting'), 'launch renders its stale-flag subset');
  assert.ok(!launchOut.includes('Card line'), 'launch does not render ready_now cards (briefing-mount subset)');
  const projectOut = vm.runInContext(
    `renderRadarBlock({radar:{stale_flags:[{project:'Alpha',text:'Old item',waiting_count:2}],ready_now_cards:[{text:'Card line'}]}})`,
    context,
  );
  assert.ok(projectOut.includes('Old item') && projectOut.includes('Card line'), 'per-project briefing renders flags and card lines');
  // Infinite waiting counts never render; missing project renders no leading
  // " — " separator.
  const bad = vm.runInContext(`renderRadarBlock({radar:{stale_flags:[{project:'Alpha',text:'T',waiting_count:Infinity},{project:'Alpha',text:'U',waiting_count:NaN}]}})`, context);
  assert.ok(!bad.includes('Infinity') && !bad.includes('NaN') && !bad.includes('waiting'), 'non-finite waiting counts render nothing');
  const noProject = vm.runInContext(`renderRadarBlock({radar:{stale_flags:[{text:'Only text'}]}})`, context);
  assert.ok(!noProject.includes(' — ') || !noProject.includes('<li> — '), 'no leading separator when project missing');
  assert.ok(noProject.includes('Only text'), 'flag text still shown');
  assert.equal(vm.runInContext(`renderRadarBlock({radar:{stale_flags:[{project:'',text:'  '}]}})`, context), '', 'blank-only flag renders nothing');
  assert.equal(vm.runInContext(`renderRadarBlock({radar:{stale_flags:[{project:'Alpha',text:0,waiting_count:0}]}})`, context).includes('0 waiting'), true, 'numeric zero text and waiting render');
  console.log('COACHING_RADAR_MOUNT_PASS');
}

// (5) Quiet research-tool styling only + collapsible panel behaviour intact.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'pointed', text: 'Needs a spoken next step', basis: 'Two items waiting', model_calls: 0 },
    radar: { stale_flags: [{ project: 'Alpha', text: 'ignored decoy', waiting_count: 2 }] },
  };
  await vm.runInContext(`S.project='p'; S.pack='x'; call=async()=>briefing_input; loadBriefing();`, context);
  await new Promise((r) => setTimeout(r, 0));
  const out = node('briefing').innerHTML;
  assert.ok(!out.includes('style='), 'no inline styling');
  assert.ok(!/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(out), 'no emoji');
  assert.ok(!/gradient|glow|glassmorphism|✨|🚀/i.test(out), 'no AI marketing styling');
  const start = html.indexOf('<aside aria-label="Research Secretary">');
  assert.ok(start >= 0, 'the Secretary aside is present');
  const aside = html.slice(start, html.indexOf('</aside>', start));
  // The panel's job is that it collapses; the exact attribute order is not the
  // contract. The markup now carries data-i18n on the summary, so match the
  // structure (a details whose summary is the saved-questions label) instead of
  // one frozen string.
  assert.ok(aside.includes('<details id="ai-panel">'), 'collapsible advanced panel preserved');
  assert.ok(/<details[^>]*>\s*<summary[^>]*>Saved questions and canvases<\/summary>/.test(aside), 'collapsible saved panel preserved');
  assert.ok(aside.includes('id="launch-briefing"') && aside.includes('id="briefing"'), 'Secretary briefing regions preserved');
  console.log('COACHING_STYLE_COLLAPSE_PASS');
}

console.log('COACHING_RADAR_PASS: guidance render, empty-guidance guard, absent-radar guard, no fabrication, radar mount points, quiet styling + collapsible panel');
