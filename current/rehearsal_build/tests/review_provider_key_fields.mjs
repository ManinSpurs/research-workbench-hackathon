// Per-provider API key fields: one write-only container per route.
// Runs the real frontend/index.html + frontend/ai.js + frontend/style.css in a
// vm with a minimal DOM double. No browser, no network, no backend, no key.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const aiSource = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../frontend/style.css', import.meta.url), 'utf8');

// (1) One field per provider, inside the existing AI panel, password-typed so a
// pasted key is never rendered in clear text.
for (const provider of ['meta', 'baseten']) {
  assert.ok(html.includes(`id="ai-key-${provider}"`), `a key field exists for ${provider}`);
  assert.ok(html.includes(`data-ai-key="${provider}"`), `${provider} field is labelled by provider`);
  const input = html.match(new RegExp(`<input[^>]*id="ai-key-${provider}"[^>]*>`));
  assert.ok(input, `${provider} field is an input`);
  assert.ok(input[0].includes('type="password"'), `${provider} key input must be masked`);
  assert.ok(input[0].includes('autocomplete="off"'), `${provider} key input must opt out of autofill`);
}
assert.ok(html.indexOf('id="ai-keys"') > html.indexOf('id="ai-panel"'),
  'key fields live inside the existing AI panel, not a new surface');
for (const hook of ['id="ai-connection"', 'id="ai-model"', 'id="ai-cloud-enabled"'])
  assert.ok(html.includes(hook), `existing access controls preserved: ${hook}`);
// Quiet doctrine, unchanged by this feature.
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!css.toLowerCase().includes(banned), `style.css must not use ${banned}`);

// (2) The page never carries a stored key back: no field is populated from
// settings, and only a presence flag is rendered.
assert.ok(!/value\s*=\s*["']b10_/.test(html), 'no literal Baseten key authored into the page');
assert.ok(aiSource.includes("'POST','/ai/keys'"), 'the save action posts to /ai/keys');

function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
      files: [], dataset: {}, classList: { add() {}, remove() {} },
      addEventListener() {}, setAttribute() {},
    });
    return nodes.get(id);
  };
  return { nodes, node };
}

const dom = makeDom();
const posted = [];
const context = {
  location: { search: '' }, URLSearchParams, API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: dom.node, querySelectorAll: () => [] },
  $: dom.node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  current: () => true,
  money4: (n) => (typeof n === 'number' ? `$${n.toFixed(4)}` : null),
  knownNumber: (n) => (typeof n === 'number' && isFinite(n) ? n : null),
  showError: (m) => { context.lastError = m; },
  loadAI: async () => {},
  fetchAIReceipts: async () => ({}),
  fetchAIReviews: async () => ({}),
  setTimeout, clearTimeout, console,
  async call(method, path, body) {
    posted.push([method, path, body]);
    if (path === '/ai/keys') return { provider: body.provider, saved: true, key_present: true, env_override: null };
    if (path === '/ai/verify-account') return { catalog: ['meta-models/muse-spark-open'], provider: body.provider };
    return {};
  },
  action: (fn) => fn(0),
};
vm.createContext(context);
vm.runInContext(aiSource, context);

// (3) Presence is rendered per provider from flags only.
const registry = {
  providers: [{ id: 'meta', base_url: 'https://api.meta.ai/v1' },
              { id: 'local', status: 'not_configured' },
              { id: 'baseten', base_url: 'https://inference.baseten.co/v1' }],
  models: [{ id: 'muse-spark-1.3', endpoint: '/responses', kind: 'reasoning' },
           { id: 'meta-models/muse-spark-open', endpoint: '/responses', kind: 'reasoning' }],
  settings: { key_configured: true, baseten_key_configured: false,
              provider_catalogs: { meta: { catalog: ['muse-spark-1.3'] },
                                   baseten: { catalog: [] } },
              cloud_enabled: false, budget_usd: 150, local_allowance_remaining_usd: 150 },
};
context.registryFixture = registry;
vm.runInContext('renderKeyStatus(registryFixture)', context);
assert.equal(dom.node('ai-key-status-meta').textContent, 'No key saved yet.');
assert.equal(dom.node('ai-key-meta').value, '');
assert.equal(dom.node('ai-key-status-baseten').textContent, 'No key saved yet.');

// (4) Saving clears the field and never echoes the key.
dom.node('ai-key-baseten').value = 'b10_synthetic_test_value';
await vm.runInContext('saveAIKey("baseten")', context);
const keyPost = posted.find(([m, p]) => m === 'POST' && p === '/ai/keys');
assert.ok(keyPost, 'a key save was posted');
assert.equal(keyPost[2].provider, 'baseten');
assert.equal(keyPost[2].key, 'b10_synthetic_test_value');
assert.deepEqual(Object.keys(keyPost[2]).sort(), ['key', 'provider'],
  'the save body carries exactly provider and key');
assert.equal(dom.node('ai-key-baseten').value, '', 'the field is cleared after saving');
assert.ok(!dom.node('ai-key-status-baseten').textContent.includes('b10_synthetic_test_value'),
  'the status line must not echo the key');
assert.ok(!html.includes('b10_synthetic_test_value'), 'no test key value is authored into the page');

// (5) A key with whitespace is refused before any request.
const before = posted.length;
dom.node('ai-key-meta').value = 'has space';
await vm.runInContext('saveAIKey("meta")', context);
assert.equal(posted.length, before, 'a malformed key is rejected client-side');
assert.ok(/no spaces/.test(context.lastError || ''), 'the refusal explains itself');

// (6) A chosen model determines which account the check verifies.
dom.node('ai-model').value = 'meta-models/muse-spark-open';
await vm.runInContext('verifyAIAccount()', context);
const verifyPost = posted.find(([m, p]) => m === 'POST' && p === '/ai/verify-account');
assert.ok(verifyPost, 'verification was posted');
assert.equal(verifyPost[2].provider, 'baseten', 'the Baseten model verifies the Baseten account');

console.log('PROVIDER_KEY_FIELDS_PASS: one masked write-only field per provider inside the AI panel, '
  + 'presence rendered from flags, no key echoed back, whitespace refused client-side, '
  + 'account check follows the selected model route');
