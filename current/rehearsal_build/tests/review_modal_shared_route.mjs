// Modal Shared opt-in route: picker entry, provider-scoped payload, isolated
// key save, explicit account check, no unsolicited request, and the old Glimmer
// hardblock still in force.
// Runs the real frontend/index.html + frontend/ai.js in a vm with a minimal DOM
// double. No browser, no network, no backend, no credential.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const aiSource = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
const settingsSource = await readFile(new URL('../frontend/interior_settings.js', import.meta.url), 'utf8');

const SHARED_MODEL = 'modal-shared:muse-spark-open';
const SHARED_PROVIDER = 'modal_shared';
const GLIMMER = 'meta-models/Muse-Glimmer-30B';

// --- (1) Markup: one opt-in disclosure block inside the existing AI panel ---
assert.ok(html.includes('id="ai-modal-shared"'), 'a Modal Shared block exists');
assert.ok(html.indexOf('id="ai-modal-shared"') > html.indexOf('id="ai-panel"'),
  'the Shared block lives inside the existing AI panel');
for (const hook of ['id="ai-connection"', 'id="ai-model"', 'id="ai-cloud-enabled"',
  'id="ai-keys"', 'id="ai-send"', 'id="ai-consent"', 'id="ai-modal-status"'])
  assert.ok(html.includes(hook), `existing control preserved: ${hook}`);
// Billing truth: per token, explicitly not GPU time; only plan-included credits
// are stated as unusable. A promotional or hackathon grant is framed as
// account-terms dependent and unchecked, never as proven ineligible, and the
// panel's allowance control is named as this app's own bookkeeping rather than
// provider credit. Configuration is not runtime verification.
assert.match(html, /bills per token, not per GPU second/);
assert.match(html, /Credits included with your Modal plan cannot be used for this route/);
assert.match(html, /promotional or hackathon grant may or may not cover this route/);
assert.match(html, /depends on this account's own terms, and this app cannot check it/);
assert.match(html, /own spending bookkeeping, not provider credit/);
assert.doesNotMatch(html, /promotional credits do not cover/,
  'a grant is never asserted ineligible');
assert.doesNotMatch(html, /allowance and promotional credits do not cover/,
  'the app allowance is never conflated with provider credit eligibility');
assert.match(html, /Listing the route proves only that it is configured/);
assert.match(html, /sends no account, metadata or inference request/);
assert.match(html, /set by this machine's runtime environment/);
// The endpoint is not editable here, and no key field for the old route exists.
assert.doesNotMatch(html, /id="ai-key-modal"/);
assert.doesNotMatch(html, /id="ai-modal-shared-base-url"/);
// The readable picker label carries no long remote repository slug.
assert.doesNotMatch(html, /Muse-Spark-Open-816B-A42B/);
// The key field is masked and one-way, exactly like the other providers.
const keyInput = html.match(/<input[^>]*id="ai-key-modal_shared"[^>]*>/);
assert.ok(keyInput, 'a Shared key field exists');
assert.ok(keyInput[0].includes('type="password"'), 'the Shared key field is masked');
assert.ok(keyInput[0].includes('autocomplete="off"'), 'the Shared key field opts out of autofill');
assert.ok(html.includes('onclick="saveAIKey(\'modal_shared\')"'), 'the field reuses the existing save path');
assert.ok(html.includes('onclick="verifyModalSharedAccount()"'), 'verification is an explicit button');
// Settings mirror reads the new row without adding a second dashboard, and its
// notice keeps the same distinction: plan credits excluded, grant unchecked.
assert.match(settingsSource, /'ai-modal-shared-status'/);
assert.match(settingsSource, /Credits included with a Modal plan cannot be used for it/);
assert.match(settingsSource, /hackathon grant covers it depends on that account\\?'s terms and is not checked here/);
assert.match(settingsSource, /allowance in the AI panel is this app\\?'s own bookkeeping, not provider credit/);
assert.doesNotMatch(settingsSource, /local allowance does not cover/);

// --- (2) A minimal DOM double: every element records reads, never hassles ---
// A <select> double that behaves like the real one where it matters here:
// replacing innerHTML rebuilds the option list and resets the selection to the
// first option, and assigning a value no option carries leaves it empty. Without
// that behaviour the selection-preservation check below could not fail.
function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: '', textContent: '', checked: false, disabled: false,
      open: false, files: [], dataset: {}, options: [],
      classList: { add() {}, remove() {} },
      listeners: {},
      addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
      setAttribute() {},
      get innerHTML() { return this._html || ''; },
      set innerHTML(markup) {
        this._html = String(markup);
        const found = [...this._html.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]);
        if (found.length) {
          this.options = found;
          this.value = found[0];
        }
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
let verifyResult = { catalog: [SHARED_MODEL], provider: SHARED_PROVIDER, inference_calls: 0 };

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
    if (path === '/ai/verify-account') return verifyResult;
    if (path.endsWith('/ai/prepare')) return { job_id: 'job-shared-1', preview_hash: 'ph-shared-1', model: body.model, provider: body.provider, task: body.task, data_use_mode: 'contributor' };
    if (path.endsWith('/execute')) return { status: 'queued' };
    throw new Error('unexpected request: ' + method + ' ' + path);
  },
  action: async (fn) => fn(0),
};
vm.createContext(context);
vm.runInContext(aiSource, context);
const run = (code) => vm.runInContext(code, context);

// --- (3) Model identity and route mapping -------------------------------
assert.equal(run(`modelRoute('${SHARED_MODEL}')`), SHARED_PROVIDER, 'the Shared model routes to its own provider');
assert.equal(run(`modelRoute('${GLIMMER}')`), 'modal', 'the old Glimmer route keeps its own id');
assert.equal(run('modelRoute("meta-models/muse-spark-open")'), 'baseten');
assert.equal(run('modelRoute("muse-wonder-1.0")'), 'meta');
assert.notEqual(run(`modelRoute('${SHARED_MODEL}')`), 'modal',
  'the Shared model must never be treated as the blocked Glimmer route');

// --- (4) Opening the panel issues no provider request -------------------
sent.length = 0;
registryFixture = {
  providers: [
    { id: 'meta', base_url: 'https://api.meta.ai/v1' },
    { id: 'local', status: 'not_configured' },
    { id: 'baseten', base_url: 'https://inference.baseten.co/v1' },
    { id: 'modal', endpoint_configured: false, execution_enabled: false },
    // The shape app/ai/modal_shared_config.connection_status() actually publishes.
    { id: SHARED_PROVIDER, display_name: 'Modal Shared / Muse Spark Open', model_id: SHARED_MODEL,
      remote_model_id: 'meta-models/Muse-Spark-Open-816B-A42B', base_url: 'https://shared.example.modal.direct/v1',
      endpoint_configured: true, remote_model_id_configured: true, key_present: true,
      transport: 'openai_responses', billing: 'per_token', prerequisites_ready: true,
      account_verified: 'NOT_CHECKED', blocking_reasons: [] },
  ],
  models: [
    { id: 'muse-spark-1.3', endpoint: '/responses', kind: 'reasoning' },
    { id: 'muse-wonder-1.0', endpoint: '/responses', kind: 'reasoning' },
    { id: 'muse-image-1.0', endpoint: '/responses', kind: 'image' },
    { id: 'meta-models/muse-spark-open', endpoint: '/responses', kind: 'reasoning' },
    { id: GLIMMER, endpoint: '/responses', kind: 'reasoning' },
    { id: SHARED_MODEL, endpoint: '/responses', kind: 'reasoning' },
  ],
  tasks: { research_query: 'muse-spark-1.3', methodology_review: 'muse-wonder-1.0', image_generate: 'muse-image-1.0' },
  settings: {
    key_configured: true, baseten_key_configured: false, modal_shared_key_configured: true,
    provider_catalogs: { meta: { catalog: ['muse-spark-1.3'] }, baseten: { catalog: [] }, [SHARED_PROVIDER]: { catalog: [] } },
    cloud_enabled: false, budget_usd: 150, local_allowance_remaining_usd: 150,
    modal_connection: { endpoint_configured: false },
  },
};
await run("loadAI('q')");
assert.deepEqual(sent.map((r) => r.method + ' ' + r.path),
  ['GET /ai/registry', 'GET /questions/q/ai/assets', 'GET /questions/q/ai/jobs'],
  'opening the panel reads only the three existing local routes');
assert.ok(!sent.some((r) => r.path === '/ai/verify-account'),
  'no account check runs unsolicited');
assert.ok(!sent.some((r) => r.path === '/ai/keys'),
  'no key path is touched unsolicited');

// --- (5) Picker: Shared executable only; Glimmer absent -----------------
const picker = dom.node('ai-model').innerHTML;
assert.ok(picker.includes(`value="${SHARED_MODEL}"`), 'executable Shared is offered');
assert.doesNotMatch(picker, /Muse-Spark-Open-816B-A42B/, 'the remote repository slug is not shown');
assert.ok(!picker.includes(GLIMMER), 'the old Glimmer row stays out of the picker even with an endpoint');
assert.doesNotMatch(picker, /muse-wonder/);

// A registry that gives the Shared row no endpoint leaves it unlisted.
const readyRegistry = registryFixture;
registryFixture = { ...registryFixture, providers: registryFixture.providers.map(p => p.id === SHARED_PROVIDER ? {...p, prerequisites_ready:false} : p) };
await run("loadAI('q')");
assert.ok(!dom.node('ai-model').innerHTML.includes(`value="${SHARED_MODEL}"`),
  'incomplete backend prerequisites never offer Shared even with an endpoint');
registryFixture = readyRegistry;
registryFixture = { ...registryFixture, models: registryFixture.models.map((m) => m.id === SHARED_MODEL ? { ...m, endpoint: null } : m) };
await run("loadAI('q')");
assert.ok(!dom.node('ai-model').innerHTML.includes(`value="${SHARED_MODEL}"`),
  'a non-executable Shared row is not offered');

// --- (6) Truthful status: configuration vs verification ----------------
registryFixture = { ...registryFixture, models: registryFixture.models.map((m) => m.id === SHARED_MODEL ? { ...m, endpoint: '/responses' } : m) };
context.registry_probe = registryFixture;
run('renderModalSharedStatus(registry_probe)');
let status = dom.node('ai-modal-shared-status').textContent;
assert.match(status, /endpoint https:\/\/shared\.example\.modal\.direct\/v1 configured/);
assert.match(status, /Configuration readiness only/);
assert.match(status, /are not verified until you run the account check/);
assert.doesNotMatch(status, /verified\.$/, 'configuration is never rendered as verified');

// Absent endpoint: honest unavailable state.
run('renderModalSharedStatus({providers:[],settings:{}})');
assert.match(dom.node('ai-modal-shared-status').textContent, /endpoint not configured in this runtime/);

// Configured origin with no pinned remote model is its own unavailable state.
run(`renderModalSharedStatus({providers:[{id:'${SHARED_PROVIDER}',base_url:'https://x.modal.direct/v1',endpoint_configured:true,remote_model_id_configured:false,key_present:true,prerequisites_ready:false}],settings:{}})`);
assert.match(dom.node('ai-modal-shared-status').textContent, /no remote model id is pinned/);

// Backend-reported incomplete prerequisites are shown blocked, never upgraded.
run(`renderModalSharedStatus({providers:[{id:'${SHARED_PROVIDER}',base_url:'https://x.modal.direct/v1',endpoint_configured:true,remote_model_id_configured:true,key_present:true,prerequisites_ready:false,blocking_reasons:['runtime_not_verified']}],settings:{}})`);
assert.match(dom.node('ai-modal-shared-status').textContent, /blocked \(runtime_not_verified\)/);

// A key reported missing by the backend is reported as missing, not assumed.
run(`renderModalSharedStatus({providers:[{id:'${SHARED_PROVIDER}',base_url:'https://x.modal.direct/v1',endpoint_configured:true,remote_model_id_configured:true,key_present:false,prerequisites_ready:false,blocking_reasons:['key_missing']}],settings:{}})`);
assert.match(dom.node('ai-modal-shared-status').textContent, /no key is saved for this provider yet/);

// A verified catalog is reported as a backend listing, not as inference proof.
run(`renderModalSharedStatus({providers:[{id:'${SHARED_PROVIDER}',base_url:'https://x.modal.direct/v1',endpoint_configured:true,remote_model_id_configured:true,key_present:true,prerequisites_ready:true}],settings:{provider_catalogs:{'${SHARED_PROVIDER}':{catalog:['${SHARED_MODEL}'],catalog_valid_for_current_key:true,catalog_at:Date.now()/1000}}}})`);
assert.match(dom.node('ai-modal-shared-status').textContent, /Account check: the backend lists modal-shared:muse-spark-open/);
// The remote repository slug the backend pins is never rendered in this pane.
assert.doesNotMatch(dom.node('ai-modal-shared-status').textContent, /Muse-Spark-Open-816B-A42B/);
for(const grant of [
  {catalog:[SHARED_MODEL],catalog_valid_for_current_key:false,catalog_at:Date.now()/1000},
  {catalog:[SHARED_MODEL],catalog_valid_for_current_key:true,catalog_at:Date.now()/1000-86401},
]){
  run(`renderModalSharedStatus({settings:{provider_catalogs:{'${SHARED_PROVIDER}':${JSON.stringify(grant)}}}})`);
  assert.match(dom.node('ai-modal-shared-status').textContent,/Account check: not verified/);
}

// Key presence is reported from flags only, never from a value.
run(`renderKeyStatus({providers:[{id:'${SHARED_PROVIDER}'}],settings:{modal_shared_key_configured:false}})`);
assert.equal(dom.node('ai-key-status-modal_shared').textContent, 'No key saved yet.');
// The backend may publish presence on the provider row instead of settings;
// both shapes read the same, and a present flag reads as saved.
run(`renderKeyStatus({providers:[{id:'${SHARED_PROVIDER}',key_present:true}],settings:{}})`);
assert.equal(dom.node('ai-key-status-modal_shared').textContent, 'Saved on this machine.');

// --- (7) Key save is provider-scoped and echoes nothing ----------------
sent.length = 0;
dom.node('ai-key-modal_shared').value = 'synthetic_shared_value';
await run(`saveAIKey('${SHARED_PROVIDER}')`);
const keyPost = sent.find((r) => r.path === '/ai/keys');
assert.ok(keyPost, 'the Shared key save posts to the existing route');
assert.deepEqual(Object.keys(keyPost.body).sort(), ['key', 'provider'],
  'the save body carries exactly provider and key');
assert.equal(keyPost.body.provider, SHARED_PROVIDER, 'the save is scoped to the Shared provider');
assert.equal(keyPost.body.key, 'synthetic_shared_value');
assert.equal(dom.node('ai-key-modal_shared').value, '', 'the field is cleared after saving');
assert.ok(!dom.node('ai-key-status-modal_shared').textContent.includes('synthetic_shared_value'),
  'the status line never echoes the key');
assert.ok(!html.includes('synthetic_shared_value'));

// --- (8) Explicit verification targets the Shared provider -------------
sent.length = 0;
dom.node('ai-model').value = SHARED_MODEL;
await run('verifyModalSharedAccount()');
const verifyPost = sent.filter((r) => r.path === '/ai/verify-account');
assert.equal(verifyPost.length, 1, 'one explicit verification request');
assert.deepEqual(Object.keys(verifyPost[0].body), ['provider']);
assert.equal(verifyPost[0].body.provider, SHARED_PROVIDER, 'verification asks for the Shared provider');
assert.ok(!sent.some((r) => r.path.endsWith('/prepare') || r.path.endsWith('/execute')),
  'verification sends no research material and runs no inference');

// --- (8b) Protocol coverage: a stale option is discarded on real refresh --
registryFixture = { ...registryFixture, models: registryFixture.models.map(m => m.id === SHARED_MODEL ? {...m, endpoint:null} : m) };
sent.length = 0;
dom.node('ai-model').options.push(SHARED_MODEL);
dom.node('ai-model').value = SHARED_MODEL;
assert.equal(dom.node('ai-model').value, SHARED_MODEL, 'the stale Shared option is selected before refresh');
await run('verifyModalSharedAccount()');
assert.equal(dom.node('ai-model').value, 'auto',
  'a removed route returns to automatic on refresh');
dom.node('ai-task').value = 'research_query';
await run('prepareAI()');
const afterVerify = sent.filter((r) => r.path.endsWith('/ai/prepare'));
assert.equal(afterVerify.length, 1);
assert.equal(afterVerify[0].body.model, 'auto', 'Prepare uses the current automatic selection');
assert.ok(!('provider' in afterVerify[0].body), 'automatic routing is owned by the backend');

// A refresh never selects a route the researcher did not choose: with nothing
// chosen, the picker stays on automatic.
dom.node('ai-model').value = 'auto';
await run('loadAI(\'q\')');
assert.equal(dom.node('ai-model').value, 'auto', 'no route is auto-selected');

// Protocol coverage: a stale Meta option also falls back on real refresh.
dom.node('ai-model').options.push('muse-spark-1.3');
dom.node('ai-model').value = 'muse-spark-1.3';
assert.equal(dom.node('ai-model').value, 'muse-spark-1.3', 'the stale Meta option is selected before refresh');
await run('loadAI(\'q\')');
assert.equal(dom.node('ai-model').value, 'auto', 'a removed Meta selection is not preserved');

// A selection the new registry no longer offers executable falls back to
// automatic, and the prepared transmission it belonged to is discarded.
sent.length = 0;
// Exercise the retained protocol with a stale option from an older build.
dom.node('ai-model').options.push(SHARED_MODEL);
dom.node('ai-model').value = SHARED_MODEL;
await run('prepareAI()');
assert.equal(run('aiPreview')?.model, SHARED_MODEL, 'a preview exists for the chosen route');
dom.node('ai-consent').checked = true;
const sharedOnly = { ...registryFixture, models: registryFixture.models.map((m) => m.id === SHARED_MODEL ? { ...m, endpoint: null } : m) };
const keptRegistry = registryFixture;
registryFixture = sharedOnly;
await run('loadAI(\'q\')');
assert.equal(dom.node('ai-model').value, 'auto', 'an unavailable selection falls back to automatic');
assert.equal(run('aiPreview'), null, 'the stale preview of the lost route is discarded');
assert.equal(dom.node('ai-consent').checked, false, 'consent for the lost route is cleared');
assert.equal(dom.node('ai-send').disabled, true, 'nothing can be executed for the lost route');
registryFixture = keptRegistry;
registryFixture = { ...registryFixture, models: registryFixture.models.map(m => m.id === SHARED_MODEL ? {...m, endpoint:'/responses'} : m) };
// Reload, then inject stale options for protocol coverage below.
await run('loadAI(\'q\')');
dom.node('ai-model').options.push(SHARED_MODEL, 'muse-spark-1.3', 'meta-models/muse-spark-open');
dom.node('ai-model').value = SHARED_MODEL;
await run('prepareAI()');

// --- (9) Prepare carries the provider only for the Shared route -------
sent.length = 0;
dom.node('ai-model').value = SHARED_MODEL;
dom.node('ai-task').value = 'research_query';
dom.node('ai-instruction').value = 'Summarize the registered sources.';
dom.node('ai-harness').checked = true;
dom.node('ai-compare').checked = false;
dom.node('ai-max-cost').value = '2.5';
await run('prepareAI()');
const sharedPrepare = sent.filter((r) => r.path.endsWith('/ai/prepare'));
assert.equal(sharedPrepare.length, 1);
assert.equal(sharedPrepare[0].body.model, SHARED_MODEL);
assert.equal(sharedPrepare[0].body.provider, SHARED_PROVIDER, 'the Shared route names its provider');
assert.deepEqual(Object.keys(sharedPrepare[0].body).sort(),
  ['assets', 'compare', 'harness', 'max_cost_usd', 'max_output_tokens', 'mode', 'model', 'provider', 'task', 'text'],
  'the prepare payload is the existing contract plus one provider field');

// Existing routes keep their byte-identical payload: no provider key added.
for (const [model, expected] of [['muse-spark-1.3', 'meta'], ['meta-models/muse-spark-open', 'baseten']]) {
  sent.length = 0;
  dom.node('ai-model').value = model;
  await run('prepareAI()');
  const prepare = sent.find((r) => r.path.endsWith('/ai/prepare'));
  assert.ok(!('provider' in prepare.body), `${expected} prepare payload is unchanged`);
  assert.equal(prepare.body.model, model);
}

// --- (10) Text-only typed tasks: kind mismatch refused locally --------
sent.length = 0;
dom.node('ai-model').value = SHARED_MODEL;
dom.node('ai-task').value = 'image_generate';
await run('prepareAI()');
assert.equal(sent.filter((r) => r.path.endsWith('/ai/prepare')).length, 0,
  'a text-only model is not offered to a typed task needing another kind');
assert.match(context.lastError || '', /cannot perform the selected task/);

// A matching text task is not blocked by the kind check.
sent.length = 0;
dom.node('ai-task').value = 'methodology_review';
await run('prepareAI()');
assert.equal(sent.filter((r) => r.path.endsWith('/ai/prepare')).length, 1,
  'a reasoning task with a reasoning model is allowed');

// --- (11) Consent path and stale preview ------------------------------
assert.equal(dom.node('ai-send').disabled, false, 'a prepared preview enables the run button');
dom.node('ai-consent').checked = false;
run('updateAIControls()');
assert.equal(dom.node('ai-send').disabled, true, 'consent is required to run');
dom.node('ai-consent').checked = true;
run('updateAIControls()');
assert.equal(dom.node('ai-send').disabled, false, 'consent enables the run button');

// Changing any task input discards the prepared preview.
const modelListener = dom.node('ai-model').listeners.change;
assert.ok(Array.isArray(modelListener) && modelListener.length, 'model changes are wired');
modelListener[0]();
assert.equal(run('aiPreview'), null, 'a model change clears the prepared preview');
assert.equal(dom.node('ai-consent').checked, false, 'a model change clears consent');
assert.equal(dom.node('ai-send').disabled, true, 'a stale preview cannot be executed');

// Execute still sends exactly the consent contract, for the Shared route too.
sent.length = 0;
dom.node('ai-model').value = SHARED_MODEL;
await run('prepareAI()');
await run('executeAI()');
assert.equal(sent.filter(r => r.path.endsWith('/execute')).length, 0, 'no execution before exact transmission consent');
dom.node('ai-consent').checked = true;
await run('executeAI()');
const exec = sent.find((r) => r.path.endsWith('/execute'));
assert.ok(exec, 'the existing execute route is used');
assert.deepEqual(Object.keys(exec.body).sort(), ['consent', 'preview_hash']);
assert.equal(exec.body.consent, true);

// --- (12) The old Glimmer hardblock is preserved ----------------------
assert.equal(run(`modelRoute('${GLIMMER}')`), 'modal');
sent.length = 0;
// The picker above proves this row is never offered. Force it into the option
// list to stand in for a stale DOM or an older build, so the guard itself is
// what refuses the route rather than the picker's absence of it.
dom.node('ai-model').options = ['auto', GLIMMER];
dom.node('ai-model').value = GLIMMER;
assert.equal(dom.node('ai-model').value, GLIMMER, 'the stale row is selected for this check');
assert.match(await run('verifyAIAccount()'), /Modal live execution is disabled/);
assert.match(await run('prepareAI()'), /Modal live execution is disabled/);
assert.match(await run('executeAI()'), /Modal live execution is disabled/);
assert.equal(sent.filter((r) => r.path.endsWith('/ai/prepare')).length, 0, 'Glimmer never prepares');
assert.equal(sent.filter((r) => r.path === '/ai/verify-account').length, 0, 'Glimmer never verifies');
// A stale preview recorded against the old route also stays blocked.
run(`aiPreview={provider:'modal',model:'${GLIMMER}'}`);
dom.node('ai-model').value = 'auto';
dom.node('ai-consent').checked = true;
run('updateAIControls()');
assert.equal(dom.node('ai-send').disabled, true, 'a preview owned by the old route cannot run');
run('aiPreview=null');

console.log('MODAL_SHARED_ROUTE_PASS: executable Shared offered; consent protocol routes to modal_shared, '
  + 'key save isolated per provider with no echo, explicit verification only, no unsolicited provider request, '
  + 'existing prepare payload unchanged for Meta and Baseten, inner route label short, '
  + 'text-only typed tasks refused locally, stale preview cleared on model change, '
  + 'old Glimmer hardblock preserved');
