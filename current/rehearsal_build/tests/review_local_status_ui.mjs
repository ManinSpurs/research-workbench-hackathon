// Local L4 status surface: honest local/API execution-path status.
// Runs the real frontend/index.html + frontend/ai.js + frontend/style.css in
// a vm with a minimal DOM double. No browser, no network, no backend.
// Label sources (never invented here):
//   recorded arm keys 'basic'/'harness' — tests/test_model_harness_labels_contract.py
//   prereg C1/C2 display ids — manifest-level only, never recorded, never
//     rendered (HARNESS_COMPARISON_PREREG_EN.md Sec. 1).
// Backend-shape assumption under test (WIRING POINT): the surface renders
// ONLY from GET /ai/registry fields that exist today — providers[] ('local'
// entry + status text) and models[] (kind/endpoint/status rows). No
// /ai/local-status route exists yet; no second endpoint is assumed. All
// registry-shaped inputs below are synthetic fixtures inside this test only.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const aiSource = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../frontend/style.css', import.meta.url), 'utf8');

// (0) Structural + quiet-doctrine checks on the shipped files.
assert.ok(html.includes('id="ai-local-status"'), 'index.html must carry ai-local-status');
assert.ok(html.includes('role="status"'), 'status elements keep role=status');
assert.ok(html.indexOf('id="ai-local-status"') > html.indexOf('id="ai-connection"'),
  'local status sits with the existing model/provider status, not elsewhere');
assert.ok(html.indexOf('id="ai-local-status"') > html.indexOf('id="ai-panel"'),
  'local status lives inside the existing AI panel, not a new surface');
for (const hook of ['id="ai-connection"', 'id="ai-model"', 'id="ai-results"', 'data-testid="end-session"'])
  assert.ok(html.includes(hook), `existing surface preserved: ${hook}`);
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!css.toLowerCase().includes(banned), `style.css must not use ${banned}`);
assert.ok(!/[\u2728\u{1F916}\u{1F399}\u{1F50A}\u2705\u274C\u26A0\u{1FA84}]/u.test(css + html + aiSource), 'no emoji in UI chrome');
assert.ok(css.includes('#ai-local-status'), 'namespaced local-status rule present');
const DISPLAY_A = 'local4b' + '-basic';
const DISPLAY_B = 'local4b' + '-harness';
assert.ok(!aiSource.includes(DISPLAY_A) && !aiSource.includes(DISPLAY_B),
  'prereg display ids are manifest-level only and must never reach rendered UI copy');

function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
      files: [], dataset: {},
      classList: { add() {}, remove() {} },
      addEventListener() {}, setAttribute() {},
    });
    return nodes.get(id);
  };
  return { nodes, node };
}

const dom = makeDom();
const context = vm.createContext({
  location: { search: '' }, URLSearchParams,
  API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: dom.node, querySelectorAll: () => [] },
  $: dom.node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async () => { throw new Error('no network in local-status test'); },
  action: async (fn) => fn(0),
  current: () => true,
  showError: () => {},
  show: () => {},
});
vm.runInContext(aiSource, context);
const renderStatus = (reg) => {
  context.reg_input = reg;
  return vm.runInContext('renderLocalStatus(reg_input)', context);
};
const localText = () => dom.node('ai-local-status').textContent;

// (1) Honest unavailable state: no runnable local entry (mirrors today's
// tree: local_candidate rows with endpoint null, provider status text
// carried through verbatim, never rewritten).
const unavailableReg = {
  providers: [
    { id: 'meta', base_url: 'https://api.meta.ai/v1', secret: 'test-synth-secret-note' },
    { id: 'local', status: 'test-synth: local inference runtime not installed; offline execution not available' },
  ],
  models: [
    { id: 'muse-spark-1.3', kind: 'reasoning', endpoint: '/responses', status: 'test-synth' },
    { id: 'spark_open_self_hosted', kind: 'local_candidate', endpoint: null, status: 'test-synth-unverified' },
    { id: 'local:example-server:example-model-tag', kind: 'local_candidate', endpoint: null, status: 'test-synth-not-configured' },
  ],
  settings: {},
};
let text = renderStatus(unavailableReg);
assert.ok(text.startsWith('Local: unavailable ('), 'unavailable prefix when nothing runnable');
assert.ok(text.includes('test-synth: local inference runtime not installed'), 'backend reason carried verbatim');
assert.ok(text.includes('Active path: API model (basic/harness arms by task).'),
  'active path honestly named as the API model with recorded arm keys');
assert.ok(!text.toLowerCase().includes('local: ready') && !text.includes('active: local'),
  'local never shown as active when unavailable');
assert.ok(!text.includes(DISPLAY_A) && !text.includes(DISPLAY_B),
  'no prereg display id in status copy');

// (2) Runnable-but-not-ready stays unavailable with API active. Readiness comes
// from the RECORDED probe (settings.local_probe), not from a model row status and
// not from configuration alone: a configured endpoint is not a running server.
const notReadyReg = {
  providers: [{ id: 'local', status: 'test-synth: configured, readiness not probed',
    endpoint_configured: true, wire_model: 'test-synth-alias', context: 8192 }],
  // Synthetic fixture (test-synth labelled): the negating probe must stay
  // not-ready — regression for the exact-match gate (a substring gate would
  // render 'not local_ready' as ready).
  models: [{ id: 'local:test-server:test-tag', kind: 'local_reasoning', endpoint: 'http://127.0.0.1:9931/v1/chat/completions', status: 'test-synth: configured' }],
  settings: { local_connection: { endpoint_configured: true, wire_model: 'test-synth-alias', context: 8192 },
    local_probe: { status: 'not local_ready', ready: false } },
};
text = renderStatus(notReadyReg);
assert.ok(text.startsWith('Local: configured but not ready ('), 'not-ready stays not-ready');
assert.ok(text.includes('test-synth-alias') === false, 'ready-only detail omitted when not ready');
assert.ok(text.includes('declared context 8192 tokens'), 'declared context reported');
assert.ok(text.includes('Active path: API model'), 'API path active until ready');

// (2b) A configured endpoint with NO probe recorded is still not ready, and
// says so explicitly rather than implying a check happened.
const neverProbedReg = {
  providers: [{ id: 'local', status: 'test-synth: configured' }],
  models: [{ id: 'local:test-server:test-tag', kind: 'local_reasoning', endpoint: 'http://127.0.0.1:9931/v1/chat/completions', status: 'test-synth: configured' }],
  settings: { local_connection: { context: 8192 }, local_probe: { status: 'local_probe_not_run', ready: false } },
};
text = renderStatus(neverProbedReg);
assert.ok(text.startsWith('Local: configured but not ready ('), 'never-probed is not ready');
assert.ok(text.includes('local_probe_not_run'), 'honest not-run token carried verbatim');
assert.ok(!text.includes('Local: ready'), 'never-probed never renders ready');

// (3) Ready shape renders ready with the exact model id, no API-model names.
// Ready requires the exact recorded token AND ready===true, never a row status.
const readyReg = {
  providers: [{ id: 'local', status: 'test-synth: probe ok' }],
  models: [{ id: 'local:test-server:test-tag', kind: 'local_reasoning', endpoint: 'http://127.0.0.1:9931/v1/chat/completions', status: 'test-synth: configured' }],
  settings: { local_connection: { endpoint_configured: true, wire_model: 'test-synth-alias', context: 8192 },
    local_probe: { status: 'local_ready', ready: true } },
};
text = renderStatus(readyReg);
assert.ok(text.includes('Local: ready'), 'ready prefix for a ready probe');
assert.ok(text.includes('local:test-server:test-tag'), 'exact local model id shown');
assert.ok(text.includes('test-synth-alias'), 'configured alias shown when ready');
assert.ok(text.includes('declared context 8192 tokens'), 'declared context shown when ready');
assert.ok(text.includes('no API cost is metered'), 'zero-API-cost stated, not implied');
assert.ok(!text.includes('muse-'), 'no API model name on a local-ready line');

// (3a) The server's OBSERVED identity wins over the declared launch values: the
// panel states what is loaded, so a /props report that differs from the intent
// is shown truthfully rather than hidden behind the configured numbers.
text = renderStatus({ ...readyReg,
  settings: { ...readyReg.settings,
    local_connection: { endpoint_configured: true, wire_model: 'test-synth-alias',
      context: 8192, observed_model_alias: 'test-observed-alias', observed_context: 4096 } } });
assert.ok(text.includes('test-observed-alias'), 'observed alias shown');
assert.ok(!text.includes('test-synth-alias'), 'declared alias superseded by observed');
assert.ok(text.includes('declared context 4096 tokens'), 'observed context shown');
assert.ok(!text.includes('8192'), 'declared context superseded by observed');

// (3b) ready:true with a WRONG status token must not render ready: the two
// facts must agree, so a backend that sets only one of them cannot light it up.
text = renderStatus({ ...readyReg,
  settings: { ...readyReg.settings, local_probe: { status: 'local_model_mismatch', ready: true } } });
assert.ok(!text.includes('Local: ready'), 'ready flag alone never renders ready');
text = renderStatus({ ...readyReg,
  settings: { ...readyReg.settings, local_probe: { status: 'local_ready', ready: false } } });
assert.ok(!text.includes('Local: ready'), 'ready token alone never renders ready');

// (4) Job-level honesty: a local-shaped job gets a local-path note; an API
// job gets none. Fixture model ids are synthetic and test-local only.
const renderJobs = (jobs) => {
  context.jobs_input = jobs;
  return vm.runInContext("renderAIJobs(jobs_input, {}, {}); $('ai-results').innerHTML", context);
};
const armOut = {
  result: { answer: 'Test answer.', claims: [], unresolved: [], proposed_variables: [], next_action: 'Test.' },
  grounding: { structural_valid: true, errors: [] },
};
const localJob = {
  id: 'job-local-synth-1', status: 'completed', reservation_usd: 0,
  result: { model: 'local:test-server:test-tag', provider: 'local', calculated_cost_usd: 0,
    measured_known_cost_usd: 0, billing_status: 'NOT_VERIFIED', usage_complete: true,
    elapsed_seconds: 1.0, stages: [], outputs: { basic: armOut } },
};
let jobsHtml = renderJobs([localJob]);
assert.ok(jobsHtml.includes('Local job: executed on the local path'), 'local job labelled as local path');
assert.ok(jobsHtml.includes('local:test-server:test-tag'), 'local job shows its exact local id');
assert.ok(jobsHtml.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed UNKNOWN even at $0');
const apiJob = {
  id: 'job-api-synth-1', status: 'completed', reservation_usd: 0.05,
  result: { model: 'muse-spark-1.3', calculated_cost_usd: 0.0001, measured_known_cost_usd: 0.0001,
    billing_status: 'NOT_VERIFIED', usage_complete: true, elapsed_seconds: 1.0, stages: [],
    outputs: { basic: armOut } },
};
jobsHtml = renderJobs([apiJob]);
assert.ok(!jobsHtml.includes('Local job:'), 'API job carries no local-path note');
assert.ok(jobsHtml.includes('muse-spark-1.3'), 'API job shows its API model id');

console.log('LOCAL_STATUS_UI_PASS: unavailable/not-ready/ready from registry shape only, API path honest, local job labelled, no display-id leakage');
