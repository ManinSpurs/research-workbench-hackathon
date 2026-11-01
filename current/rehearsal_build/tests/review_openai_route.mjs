// OpenAI test route: pinned model routes to the openai provider, the key field
// is a password write-only save through the existing generic path, Prepare
// carries the public-test-data confirmation, and no other provider is touched.
// Runs the real frontend/index.html + frontend/ai.js in a vm with a minimal DOM
// double (same shape as review_modal_shared_route.mjs). No browser, no network,
// no backend, no key.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const aiSource = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');

const OPENAI_MODEL = 'gpt-5.4-mini-2026-03-17';

// --- (1) Markup: one masked, write-only key row and one data attestation ---
assert.ok(html.includes('data-ai-key="openai"'), 'an OpenAI key row exists');
const keyGroupStart = html.indexOf('id="ai-keys"');
const groupOpen = html.lastIndexOf('<div', keyGroupStart);
let depth = 0, keyGroupEnd = -1;
for (const tag of html.slice(groupOpen).matchAll(/<\/?div\b[^>]*>/g)) {
  depth += tag[0].startsWith('</') ? -1 : 1;
  if (depth === 0) { keyGroupEnd = groupOpen + tag.index + tag[0].length; break; }
}
assert.ok(keyGroupStart >= 0 && keyGroupEnd > keyGroupStart);
assert.ok(html.slice(groupOpen, keyGroupEnd).includes('data-ai-key="openai"'),
  'the OpenAI key row lives in the existing per-provider key group');
assert.ok(html.indexOf('data-ai-key="openai"') > html.indexOf('id="ai-panel"'),
  'the OpenAI key row lives inside the existing AI panel');
const keyInput = html.match(/<input[^>]*id="ai-key-openai"[^>]*>/);
assert.ok(keyInput, 'the OpenAI key field is an input');
assert.ok(keyInput[0].includes('type="password"'), 'the OpenAI key field is masked');
assert.ok(keyInput[0].includes('autocomplete="off"'), 'the OpenAI key field opts out of autofill');
assert.ok(html.includes("onclick=\"saveAIKey('openai')\""), 'the field reuses the generic save path');
assert.match(html, /<span[^>]*id="ai-key-status-openai"[^>]*role="status"/, 'a status line reports presence only');
// The $1 attestation is this database's own reservation cap, not provider credit.
assert.match(html, /capped at \$1 on this local database/);
assert.match(html, /not your provider credit balance/);
// The confirmation covers the whole transmission and is its own control.
const publishInput = html.match(/<input[^>]*id="ai-public-test-data"[^>]*>/);
assert.ok(publishInput, 'the public-test-data confirmation exists as a checkbox');
assert.ok(publishInput[0].includes('type="checkbox"'), 'the confirmation is a checkbox');
assert.match(html, /entire transmitted question, notes, decisions and source context are public or synthetic test data/);
assert.match(html, /No private research/);
// Toggling the confirmation invalidates consent inline, in the markup itself.
assert.match(publishInput[0], /onchange="[^"]*aiPreview=null[^"]*"/, 'toggle drops the prepared preview inline');
assert.match(publishInput[0], /onchange="[^"]*getElementById\('ai-consent'\)\.checked=false[^"]*"/,
  'toggle clears consent inline');
for (const hook of ['id="ai-connection"', 'id="ai-model"', 'id="ai-consent"', 'id="ai-send"'])
  assert.ok(html.includes(hook), `existing control preserved: ${hook}`);

// --- (2) Minimal DOM double (option list rebuilt on innerHTML, as the real one) ---
function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, textContent: '', checked: false, disabled: false, files: [], dataset: {}, options: [],
      classList: {
        values: new Set(),
        add(name) { this.values.add(name); },
        remove(name) { this.values.delete(name); },
        contains(name) { return this.values.has(name); },
      }, listeners: {},
      addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
      setAttribute() {},
      get innerHTML() { return this._html || ''; },
      set innerHTML(markup) {
        this._html = String(markup);
        const found = [...this._html.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]);
        if (found.length) { this.options = found; this.value = found[0]; }
      },
      get value() { return this._value || ''; },
      set value(next) {
        const v = String(next ?? '');
        if (this.options.length && v !== '' && !this.options.includes(v)) { this._value = ''; return; }
        this._value = v;
      },
    });
    return nodes.get(id);
  };
  return { nodes, node };
}
const dom = makeDom();
const sent = [];
let registryFixture = null;

const context = {
  location: { search: '' }, URLSearchParams, API: 'http://localhost:8123',
  S: { project: 'p', question: 'q', cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: dom.node, querySelectorAll: () => [] },
  $: dom.node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  current: () => true,
  showError: (m) => { context.lastError = m; return m; },
  loadAI: async () => {},
  fetchAIReceipts: async () => ({}),
  fetchAIReviews: async () => ({}),
  setTimeout, clearTimeout, console,
  async call(method, path, body) {
    sent.push({ method, path, body });
    if (path === '/ai/registry') return registryFixture;
    if (path.endsWith('/ai/assets')) return { assets: [] };
    if (path.endsWith('/ai/jobs')) return { jobs: [] };
    if (path === '/ai/keys') return { provider: body.provider, saved: true, key_present: true, env_override: null };
    if (path === '/ai/verify-account') return { catalog: [OPENAI_MODEL], provider: body.provider, inference_calls: 0 };
    if (path.endsWith('/ai/prepare')) return { job_id: 'job-openai-1', preview_hash: 'ph-openai-1', model: body.model, provider: body.provider, task: body.task, data_use_mode: 'contributor' };
    if (path.endsWith('/execute')) return { status: 'queued' };
    throw new Error('unexpected request: ' + method + ' ' + path);
  },
  action: async (fn) => fn(0),
};
vm.createContext(context);
vm.runInContext(aiSource, context);
const run = (code) => vm.runInContext(code, context);

// --- (3) The pinned model identifies the openai route, others unchanged ---
assert.equal(run(`modelRoute('${OPENAI_MODEL}')`), 'openai', 'the pinned test model routes to openai');
assert.equal(run('modelRoute("muse-spark-1.3")'), 'meta', 'an ordinary Meta model still routes to meta');
assert.equal(run('modelRoute("meta-models/muse-spark-open")'), 'baseten', 'the Baseten slug keeps baseten');
assert.equal(run("modelRoute('modal-shared:muse-spark-open')"), 'modal_shared', 'Modal Shared keeps its own id');
for (const other of ['muse-wonder-1.0', 'muse-spark-1.3', 'meta-models/muse-spark-open'])
  assert.notEqual(run(`modelRoute('${other}')`), 'openai', `${other} never routes to openai`);

// --- (4) Opening the panel sends no provider request ---
sent.length = 0;
registryFixture = {
  providers: [
    { id: 'meta', base_url: 'https://api.meta.ai/v1' },
    { id: 'local', status: 'not_configured' },
    { id: 'baseten', base_url: 'https://inference.baseten.co/v1' },
  ],
  models: [
    { id: 'muse-spark-1.3', endpoint: '/responses', kind: 'reasoning' },
    { id: 'muse-wonder-1.0', endpoint: '/responses', kind: 'reasoning' },
    { id: 'meta-models/muse-spark-open', endpoint: '/responses', kind: 'reasoning' },
    { id: OPENAI_MODEL, endpoint: '/responses', kind: 'reasoning' },
  ],
  tasks: { research_query: 'muse-spark-1.3', methodology_review: 'muse-wonder-1.0' },
  settings: {
    key_configured: true, baseten_key_configured: false, openai_key_configured: false,
    provider_catalogs: { meta: { catalog: ['muse-spark-1.3'] }, baseten: { catalog: [] }, openai: { catalog: [] } },
    cloud_enabled: false, budget_usd: 150, local_allowance_remaining_usd: 150,
  },
};
await run("loadAI('q')");
assert.deepEqual(sent.map((r) => r.method + ' ' + r.path),
  ['GET /ai/registry', 'GET /questions/q/ai/assets', 'GET /questions/q/ai/jobs'],
  'opening the panel reads only the existing local routes');
assert.ok(!sent.some((r) => r.path === '/ai/verify-account'), 'no account check runs unsolicited');
assert.ok(!sent.some((r) => r.path === '/ai/keys'), 'no key path is touched unsolicited');
assert.ok(!dom.node('ai-model').innerHTML.includes(`value="${OPENAI_MODEL}"`), 'the test-only model is not in the restricted picker');
// Retained protocol coverage exercises a stale option from an older build.
dom.node('ai-model').options.push(OPENAI_MODEL, 'muse-spark-1.3', 'muse-wonder-1.0', 'meta-models/muse-spark-open');

// --- (5) Account verification for the selected route goes to openai only ---
sent.length = 0;
dom.node('ai-model').value = OPENAI_MODEL;
assert.equal(dom.node('ai-model').value, OPENAI_MODEL, 'protocol coverage starts with the stale option selected');
await run('verifyAIAccount()');
const verifyPosts = sent.filter((r) => r.path === '/ai/verify-account');
assert.equal(verifyPosts.length, 1, 'one explicit verification request');
assert.deepEqual(Object.keys(verifyPosts[0].body), ['provider'], 'verification carries the provider only');
assert.equal(verifyPosts[0].body.provider, 'openai', 'verification names only the openai provider');assert.ok(!sent.some((r) => r.path.endsWith('/prepare') || r.path.endsWith('/execute')),
  'verification sends no research material and runs no inference');
// The real registry refresh discards the stale selection.
assert.equal(dom.node('ai-model').value, 'auto', 'the removed route is discarded on refresh');
dom.node('ai-model').options.push(OPENAI_MODEL, 'muse-spark-1.3', 'muse-wonder-1.0', 'meta-models/muse-spark-open');
dom.node('ai-model').value = OPENAI_MODEL;
assert.equal(dom.node('ai-model').value, OPENAI_MODEL, 'protocol coverage starts with the injected option selected');
await run("loadAI('q')");
assert.equal(dom.node('ai-model').value, 'auto', 'a real refresh removes the injected option');
// Subsequent protocol-only checks isolate transport from registry refresh.
context.loadAI = async () => {};
dom.node('ai-model').options.push(OPENAI_MODEL, 'muse-spark-1.3', 'muse-wonder-1.0', 'meta-models/muse-spark-open');

// --- (6) Key save is provider-scoped, write-only, and echoes nothing ---
dom.node('ai-key-meta').value = '';
dom.node('ai-key-status-meta').textContent = 'No key saved yet.';
sent.length = 0;
dom.node('ai-key-openai').value = 'synthetic_openai_value';
await run("saveAIKey('openai')");
const keyPost = sent.filter((r) => r.path === '/ai/keys');
assert.equal(keyPost.length, 1, 'one key save request');
assert.deepEqual(Object.keys(keyPost[0].body).sort(), ['key', 'provider'], 'the save body carries exactly provider and key');
assert.equal(keyPost[0].body.provider, 'openai', 'the save is scoped to openai');
assert.equal(keyPost[0].body.key, 'synthetic_openai_value');
assert.ok(!('stored_value' in keyPost[0].body) && !('value' in keyPost[0].body), 'no extra echo field is authored');
assert.equal(dom.node('ai-key-openai').value, '', 'the field is cleared after saving');
assert.ok(!dom.node('ai-key-status-openai').textContent.includes('synthetic_openai_value'),
  'the status line never echoes the key');
assert.equal(dom.node('ai-key-status-openai').textContent, 'Saved on this machine.', 'only presence is reported');
assert.equal(dom.node('ai-key-status-meta').textContent, 'No key saved yet.', 'another provider status is untouched');
assert.equal(dom.node('ai-key-meta').value, '', 'another provider field is untouched');
assert.ok(!html.includes('synthetic_openai_value'));

// --- (7) Prepare carries the confirmation, and only for the openai route ---
sent.length = 0;
dom.node('ai-model').value = OPENAI_MODEL;
dom.node('ai-task').value = 'research_query';
dom.node('ai-instruction').value = 'Summarize the registered sources.';
dom.node('ai-public-test-data').checked = true;
await run('prepareAI()');
let prepares = sent.filter((r) => r.path.endsWith('/ai/prepare'));
assert.equal(prepares.length, 1, 'one prepare request');
assert.equal(prepares[0].body.model, OPENAI_MODEL, 'Prepare sends the pinned model');
assert.equal(prepares[0].body.provider, 'openai', 'Prepare names the openai provider');
assert.equal(prepares[0].body.public_test_data, true, 'the confirmation is carried when checked');
assert.deepEqual(Object.keys(prepares[0].body).sort(),
  ['assets', 'compare', 'harness', 'max_cost_usd', 'max_output_tokens', 'mode', 'model', 'provider', 'public_test_data', 'task', 'text'],
  'the prepare payload is the existing contract plus provider and the confirmation');

// The confirmation is reported as false, never omitted, when unchecked.
sent.length = 0;
dom.node('ai-public-test-data').checked = false;
await run('prepareAI()');
prepares = sent.filter((r) => r.path.endsWith('/ai/prepare'));
assert.equal(prepares.length, 1);
assert.equal(prepares[0].body.public_test_data, false, 'an unchecked confirmation is sent as false');

// No other provider payload gains a provider or confirmation field.
for (const [model, expected] of [['muse-spark-1.3', 'meta'], ['muse-wonder-1.0', 'meta'], ['meta-models/muse-spark-open', 'baseten']]) {
  sent.length = 0;
  dom.node('ai-model').value = model;
  dom.node('ai-task').value = 'research_query';
  await run('prepareAI()');
  const prepare = sent.find((r) => r.path.endsWith('/ai/prepare'));
  assert.ok(prepare, `${expected} still prepares`);
  assert.ok(!('provider' in prepare.body), `${expected} prepare payload is unchanged`);
  assert.ok(!('public_test_data' in prepare.body), `${expected} carries no openai confirmation`);
  assert.equal(prepare.body.model, model);
}

// --- (8) Toggling the confirmation invalidates consent and the preview ---
const toggleBody = publishInput[0].match(/onchange="([^"]*)"/)[1]
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
sent.length = 0;
dom.node('ai-model').value = OPENAI_MODEL;
await run('prepareAI()');
assert.ok(run('aiPreview') !== null, 'a preview exists before the toggle');
dom.node('ai-consent').checked = true;
run('updateAIControls()');
assert.equal(dom.node('ai-send').disabled, false, 'consent enables the run button');
dom.node('ai-public-test-data').checked = false;
run(toggleBody);
assert.equal(run('aiPreview'), null, 'the toggle drops the prepared preview');
assert.equal(dom.node('ai-consent').checked, false, 'the toggle clears consent');
assert.ok(dom.node('ai-preview-panel').classList.contains('hidden'), 'the discarded preview is hidden by the handler');
assert.equal(dom.node('ai-send').disabled, true, 'a stale preview cannot be executed after the toggle');
sent.length = 0;
assert.match(await run('executeAI()'), /Review and approve the prepared transmission first/);
assert.equal(sent.filter((r) => r.path.endsWith('/execute')).length, 0, 'no execution is sent without consent');

// --- (9) Every request in this file stayed on the existing local routes ---
const allowed = new Set(['/ai/registry', '/ai/keys', '/ai/verify-account', '/questions/q/ai/assets',
  '/questions/q/ai/jobs', '/questions/q/ai/prepare', '/ai/jobs/job-openai-1/execute']);
for (const r of sent) assert.ok(allowed.has(r.path), `unexpected route: ${r.path}`);
assert.ok(!aiSource.includes('api.openai.com'), 'the frontend never addresses a provider host directly');

console.log('OPENAI_ROUTE_PASS: model absent from picker; stale-option protocol routes to openai only, '
  + 'masked write-only key save via the generic path with no echo and no other provider touched, '
  + 'account verification scoped to openai, prepare carries the public-test-data confirmation, '
  + 'other provider payloads unchanged, confirmation toggle invalidates consent inline, no network');
