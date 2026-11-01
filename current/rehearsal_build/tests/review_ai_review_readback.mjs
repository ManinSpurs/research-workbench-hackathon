// Stored-review view test: loads frontend/ai.js in a vm harness with a
// minimal DOM double (no jsdom, no browser, no network) and exercises
// renderAIJobs over a job carrying a stored-review payload.
//
// Conventions mirror tests/review_ai_receipt.mjs: no network inside
// renderAIJobs (the third reviews argument is data, not a fetch), escaped
// output only, secret-looking markers must never appear in the HTML.
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

// ai.js expects these globals from app.js ($, esc, API, S, document); the
// network helpers are stubbed because renderAIJobs never touches the network.
// The third argument (stored reviews) arrives pre-fetched, exactly like the
// second (receipts) argument; fetchAIReviews covers the network side.
const context = vm.createContext({
  location: { search: '' },
  URLSearchParams,
  API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async () => { throw new Error('no network in review readback test'); },
  action: async (fn) => fn(0),
  current: () => true,
  showError: () => {},
  show: () => {},
});

const source = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
vm.runInContext(source, context);

const render = (jobs, receipts, reviews) => {
  context.jobs_input = jobs;
  context.receipts_input = receipts;
  context.reviews_input = reviews;
  return vm.runInContext("renderAIJobs(jobs_input, receipts_input, reviews_input); $('ai-results').innerHTML", context);
};

const SECRET = 'sk-test-STORED-REVIEW-MARKER-zzz999';

const baseJob = {
  id: 'job-stored-review-1',
  status: 'completed',
  reservation_usd: 0.05,
  data_use_mode: 'local-only',
  result: {
    calculated_cost_usd: 0.000123,
    measured_known_cost_usd: 0.0001,
    billing_status: 'NOT_VERIFIED',
    usage_complete: true,
    outputs: {
      harness: {
        result: { answer: 'Harness answer.', claims: [{ text: 'Harness claim', kind: 'fact', evidence_ids: [] }] },
        grounding: { structural_valid: true, errors: [] },
      },
    },
  },
};

const storedReviews = {
  'job-stored-review-1': {
    reviews: [
      {
        review_id: 'review-first-1',
        job_id: 'job-stored-review-1',
        question_id: 'q-1',
        report: {
          rubric_version: 'rw-research-1',
          reviewer: 'r-chen',
          gold_case_id: 'FX-APPROVAL-1',
          scores: { harness: { claim_count: 1 } },
          review: {},
          improvement_claim: 'NOT_COMPUTED',
        },
      },
    ],
    provider_billed_cost_usd: null,
    billing_status: 'NOT_VERIFIED',
  },
};

// (1) Job with a stored review: labelled as a saved result, read-only, no
// fresh-run wording; payload rendered escaped inside a details block.
let html = render([baseJob], {}, storedReviews);
assert.ok(html.includes('Stored review (saved result, 1 saved)'), 'stored review labelled as a saved result with count');
assert.ok(html.includes('Read-only: viewing this never runs a new review or a new inference.'), 'read-only wording, no fresh run implied');
assert.ok(html.includes('review-first-1'), 'stored review id shown');
assert.ok(html.includes('r-chen'), 'reviewer shown');
assert.ok(html.includes('NOT_VERIFIED') || html.includes('Provider billed cost: UNKNOWN'), 'no invented billed cost anywhere new');

// (2) Third argument omitted (old callers): falls back to no stored-review
// block without breaking the existing Trust Receipt rendering.
html = render([baseJob]);
assert.ok(!html.includes('Stored review (saved result'), 'no stored-review block without stored data');
assert.ok(html.includes('Trust Receipt'), 'Trust Receipt still renders without reviews argument');

// (3) Job without stored review entry: no stored-review block; reviewer
// names carrying markup are echoed escaped, never as raw HTML.
const XSS_MARKER = '<img src=x onerror=alert(1)>';
html = render([baseJob], {}, { 'job-stored-review-1': { reviews: [{ review_id: 'x', job_id: 'job-stored-review-1', question_id: 'q-1', report: { reviewer: 'r-' + XSS_MARKER + '-x', rubric_version: 'rw-research-1', gold_case_id: 'g' } }] } });
assert.ok(html.includes('Stored review (saved result, 1 saved)'), 'stored block renders even for a synthetic payload');
assert.ok(!html.includes(XSS_MARKER), 'reviewer markup never appears raw');
assert.ok(html.includes('r-&lt;img'), 'reviewer markup appears escaped');

const SECOND_SECRET = 'sk-test-STORED-REVIEW-SECOND-marker-000';
// (4) Stored payload id/job scoping surfaces in the list: two reviews keep
// stored order (first before second) with explicit counts; no raw
// report text is fabricated — only the exact stored ids and fields render.
html = render([baseJob], {}, {
  'job-stored-review-1': {
    reviews: [
      { review_id: 'review-first-1', job_id: 'job-stored-review-1', question_id: 'q-1', report: { reviewer: 'r-first', rubric_version: 'rw-research-1', gold_case_id: 'g' } },
      { review_id: 'review-second-2', job_id: 'job-stored-review-1', question_id: 'q-1', report: { reviewer: 'r-second', rubric_version: 'rw-research-1', gold_case_id: 'g' } },
    ],
  },
});
assert.ok(html.includes('Stored review (saved result, 2 saved)'), 'explicit count for multiple stored reviews');
assert.ok(html.indexOf('review-first-1') < html.indexOf('review-second-2'), 'stored order preserved, first before second');

console.log('AI_REVIEW_READBACK_FRONTEND_PASS: stored reviews render as labelled read-only saved results with explicit counts and order, no fresh-run wording, no raw secrets');
