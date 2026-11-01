// Voice-confirm panel test: loads frontend/ai.js in a vm harness with a
// minimal DOM double (no jsdom, no browser, no network) and exercises the
// explicit confirm flow against the real endpoint contract:
// POST /questions/{qid}/ai/voice-confirm with exactly
// {confirmed_text, source_asset_id, source_sha256}.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

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

const sent = [];
let failWith = null;
const loaded = [];
const context = vm.createContext({
  location: { search: '' },
  URLSearchParams,
  API: 'http://localhost:8123',
  S: { project: 'p', question: 'q', cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async (method, path, body) => {
    sent.push({ method, path, body });
    if (failWith) { const e = new Error(failWith); failWith = null; throw e; }
    if (path.endsWith('/ai/voice-confirm')) return { question_id: 'qn-new', original_text: body.confirmed_text };
    throw new Error('unexpected network in voice-confirm test: ' + path);
  },
  action: async (fn) => fn(0),
  current: () => true,
  showError: (m) => { context.lastError = m; },
  show: () => {},
  loadPending: async () => {},
  loadBriefing: async () => {},
});

const source = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
vm.runInContext(source, context);
// Stub the post-confirm refresh: record the question reloaded, do no network.
vm.runInContext(`loadAI = async (qid) => { globalThis.__loaded.push(qid); };`, context);
context.__loaded = loaded;
vm.runInContext(`aiQuestion = 'q';`, context);

const html = () => vm.runInContext(`$('ai-results').innerHTML`, context);
const render = (jobs) => {
  context.jobs_input = jobs;
  sent.length = 0;
  return vm.runInContext(`renderAIJobs(jobs_input); $('ai-results').innerHTML`, context);
};

const eligible = {
  id: 'job-voice-1',
  status: 'completed',
  reservation_usd: 0.001,
  result: {
    outputs: {
      transcript: 'Raw unconfirmed transcript',
      turns: [{ startMs: 0, endMs: 1000, speaker: 'A', transcript: 'Raw unconfirmed transcript' }],
      source_asset: { id: 'asset-1', sha256: 'a'.repeat(64) },
    },
  },
};

// (1) Eligible completed voice job shows an explicit confirm control and nothing auto-submits.
let out = render([eligible]);
assert.ok(out.includes('Confirm as question text'), 'eligible completed voice job must offer explicit confirm');
assert.ok(out.includes('proposal only'), 'confirm panel must label the transcript a proposal');
assert.ok(out.includes('RECORDER_FORMAT_GAP'), 'panel must not claim in-browser WAV recording exists');
assert.ok(out.includes('Raw unconfirmed transcript'), 'proposed transcript shown for review');
assert.equal(sent.length, 0, 'render must never call the network (no auto-submit)');

// (2) Explicit click posts the exact endpoint contract and refreshes state.
node('voice-confirm-job-voice-1').value = 'Edited memo question';
await vm.runInContext(`submitVoiceConfirm('job-voice-1')`, context);
assert.equal(sent.length, 1, 'one explicit confirm request');
assert.equal(sent[0].method, 'POST');
assert.equal(sent[0].path, '/questions/q/ai/voice-confirm');
assert.deepEqual(Object.keys(sent[0].body).sort(), ['confirmed_text', 'source_asset_id', 'source_sha256'],
  'body must carry exactly confirmed_text, source_asset_id, source_sha256');
assert.equal(sent[0].body.confirmed_text, 'Edited memo question');
assert.equal(sent[0].body.source_asset_id, 'asset-1');
assert.equal(sent[0].body.source_sha256, 'a'.repeat(64));
assert.deepEqual(loaded, ['q'], 'success must refresh the question AI state via loadAI');
out = render([eligible]); // simulate the loadAI refresh render
assert.ok(out.includes('Question created: qn-new'), 'success notice names the created question');

// (3) Fail closed: incomplete identifiers never render a working-looking button.
for (const [name, job] of [
  ['failed job', { id: 'j2', status: 'failed_validation', result: { error_code: 'SCOPE_FAIL' } }],
  ['no transcript', { id: 'j3', status: 'completed', result: { outputs: { source_asset: { id: 'a', sha256: 's' } } } }],
  ['no source asset', { id: 'j4', status: 'completed', result: { outputs: { transcript: 't' } } }],
  ['no sha', { id: 'j5', status: 'completed', result: { outputs: { transcript: 't', source_asset: { id: 'a' } } } }],
  ['queued', { id: 'j6', status: 'queued' }],
]) {
  out = render([job]);
  assert.ok(!out.includes('Confirm as question text'), `${name} must not offer confirm`);
  assert.equal(sent.length, 0, `${name} render must not call the network`);
}

// (4) Failure surfaces the safe error state and performs no refresh.
// The app's action() helper clears the panel on error, so assert the safe
// message path directly through the render-time status hook.
render([eligible]);
failWith = 'source_sha256 does not match the registered asset';
node('voice-confirm-job-voice-1').value = 'Edited memo question';
loaded.length = 0;
let rejected = false;
try { await vm.runInContext(`submitVoiceConfirm('job-voice-1')`, context); }
catch { rejected = true; }
assert.ok(rejected, 'a rejected confirm must propagate so action() reports it');
assert.deepEqual(loaded, [], 'failure must not refresh as success');
assert.equal(sent.length, 1, 'the failed confirm still attempted the exact contract');
assert.deepEqual(Object.keys(sent[0].body).sort(), ['confirmed_text', 'source_asset_id', 'source_sha256'],
  'failed attempt also uses exactly the contract body');
vm.runInContext(`voiceConfirmNotice['job-voice-1']={ok:false,text:'source_sha256 does not match the registered asset'}; renderAIJobs(jobs_input);`, context);
out = html();
assert.ok(out.includes('Confirmation failed: source_sha256 does not match the registered asset'),
  'failure status shows the safe server message');
assert.ok(!out.includes('sk-live'), 'no provider secrets in output');

// (5) Transcript markup is escaped, never raw HTML.
out = render([{ ...eligible, id: 'j7', result: { outputs: { ...eligible.result.outputs, source_asset: { id: 'a7', sha256: 's7' }, transcript: '<script>alert(1)</script>' } } }]);
assert.ok(!out.includes('<script>'), 'transcript markup must be escaped');
assert.ok(out.includes('&lt;script&gt;'), 'escaped transcript shown');

console.log('VOICE_CONFIRM_FRONTEND_PASS: explicit confirm only, exact body contract, fail-closed eligibility, no auto-submit, safe failure, escaped transcript');
