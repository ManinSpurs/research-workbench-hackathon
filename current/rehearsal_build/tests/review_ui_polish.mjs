// UI polish regression: restrained visual language, structural hooks, and
// unchanged honesty strings. No browser, no network.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const css = await readFile(new URL('../frontend/style.css', import.meta.url), 'utf8');
const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!css.toLowerCase().includes(banned), `style.css must not use ${banned}`);
assert.ok(!/[\u2728\u{1F916}\u{1F399}\u{1F50A}\u2705\u274C\u26A0\u{1FA84}]/u.test(css + html), 'no emoji in UI chrome');
assert.ok(css.includes(':focus-visible'), 'visible focus state required');
assert.ok(css.includes('.wb-chip') && css.includes('.wb-job') && css.includes('.wb-confirm'), 'polish classes present');
for (const h of ['Access and allowance', 'Task definition', 'Review and run'])
  assert.ok(html.includes(h), `AI panel section heading missing: ${h}`);

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
    files: [], dataset: {},
    classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
  });
  return nodes.get(id);
};
const context = vm.createContext({
  location: { search: '' }, URLSearchParams, API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async () => { throw new Error('no network in polish test'); },
  action: async (fn) => fn(0), current: () => true,
  showError: () => {}, show: () => {},
});
vm.runInContext(await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8'), context);
const render = (jobs, receipts) => {
  context.jobs_input = jobs; context.receipts_input = receipts;
  return vm.runInContext("renderAIJobs(jobs_input, receipts_input); $('ai-results').innerHTML", context);
};
const job = { id: 'j-polish-1', status: 'completed', reservation_usd: 0.05,
  result: { model: 'm', billing_status: 'NOT_VERIFIED', usage_complete: true, calculated_cost_usd: 0.0001,
    measured_known_cost_usd: 0.0001, data_use_mode: 'local-only', stages: [],
    outputs: { transcript: 't', turns: [], source_asset: { id: 'a', sha256: 's' } } } };
const out = render([job]);
for (const hook of ['wb-job', 'wb-chip', 'wb-receipt', 'wb-record', 'wb-confirm'])
  assert.ok(out.includes(hook), `rendered job must carry ${hook} hook`);
assert.ok(out.includes('Trust Receipt'), 'Trust Receipt heading preserved');
assert.ok(out.includes('Provider billed cost: UNKNOWN (not a bill)'), 'UNKNOWN billing preserved');
assert.ok(out.includes('RECORDER_FORMAT_GAP'), 'recorder gap disclosure preserved');
assert.ok(out.includes('proposal only'), 'proposal-only wording preserved');
const out2 = render([{ status: 'completed', reservation_usd: 0.01,
  result: { billing_status: 'NOT_VERIFIED', usage_complete: true, stages: [], outputs: {} } }]);
assert.ok(out2.includes('Server receipt preferred') === false, 'no server note without server receipt');
console.log('UI_POLISH_PASS: restrained CSS, section headings, structural hooks, honesty strings intact');
