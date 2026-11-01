// Trust Receipt panel test: loads frontend/ai.js in a vm harness with a
// minimal DOM double (no jsdom, no browser, no network) and exercises
// renderAIJobs over synthetic job records.
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
// network helpers (call/action/current/showError/show) are stubbed because
// renderAIJobs never touches the network. The load-time listener loop only
// needs $(id).addEventListener to exist.
const context = vm.createContext({
  location: { search: '' },
  URLSearchParams,
  API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async () => { throw new Error('no network in receipt test'); },
  action: async (fn) => fn(0),
  current: () => true,
  showError: () => {},
  show: () => {},
});

const source = await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8');
vm.runInContext(source, context);

const render = (jobs, receipts) => {
  context.jobs_input = jobs;
  context.receipts_input = receipts;
  // receipts === undefined reproduces the original single-argument fallback path.
  return vm.runInContext("renderAIJobs(jobs_input, receipts_input); $('ai-results').innerHTML", context);
};

// Secret-looking markers: must never appear in rendered HTML. They are
// planted in validation_issues entries and in unrendered raw/rejected fields.
const SECRET_A = 'sk-live-RECEIPT-SECRET-MARKER-aaa111';
const SECRET_B = 'AKIA-RECEIPT-SECRET-MARKER-bbb222';

// (1) Completed reasoning job: stages plus basic+harness outputs.
const completedJob = {
  status: 'completed',
  reservation_usd: 0.05,
  data_use_mode: 'local-only',
  result: {
    model: 'meta-test-reasoner',
    data_use_mode: 'local-only',
    calculated_cost_usd: 0.000123,
    measured_known_cost_usd: 0.0001,
    billing_status: 'NOT_VERIFIED',
    usage_complete: true,
    elapsed_seconds: 12.5,
    stages: [
      { stage: 'reason', model: 'meta-test-reasoner', status: 'ok', elapsed_seconds: 3.25, input_tokens: 100, output_tokens: 50, total_tokens: 150, calculated_cost_usd: 0.00005 },
      { stage: 'answer', model: 'meta-test-reasoner', status: 'ok', elapsed_seconds: 1.5, input_tokens: 200, output_tokens: 80, total_tokens: 280, calculated_cost_usd: 0.000073 },
    ],
    outputs: {
      basic: {
        result: { answer: 'Baseline answer.', claims: [{ text: 'Claim one', kind: 'fact', evidence_ids: ['ev1', 'ev2'] }, { text: 'Claim two', kind: 'method', evidence_ids: ['ev3'] }] },
        grounding: { structural_valid: true, errors: [] },
      },
      harness: {
        result: { answer: 'Harness answer.', claims: [{ text: 'Harness claim', kind: 'fact', evidence_ids: [] }] },
        grounding: { structural_valid: false, errors: ['missing evidence link'] },
      },
    },
  },
};

// (2) Failed validation job: error code plus validation issues carrying
// secret-looking markers and partial/rejected raw text (kept in fields the
// renderer must not echo).
const failedJob = {
  status: 'failed_validation',
  reservation_usd: 0.02,
  result: {
    error_code: 'VALIDATION_FAILED',
    validation_issues: [
      'rejected output contained a pasted credential ' + SECRET_A,
      { issue: 'credential-like token in rejected raw text', raw: SECRET_B },
    ],
    rejected_raw: 'partial raw text withheld from display ' + SECRET_A + ' ' + SECRET_B,
    partial_text: 'rejected partial answer text',
  },
};

// (3) Job with no result at all.
const queuedJob = { status: 'queued', reservation_usd: 0.01 };

let html = render([completedJob]);
assert.ok(html.includes('Trust Receipt'), 'completed job must show a Trust Receipt');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost UNKNOWN, not a bill');
assert.ok(html.includes('this is not a bill'), 'completed charge line disclaims billing');
assert.ok(html.includes('basic: claims 2 / evidence IDs 3 / structural pass'), 'basic arm counts and structural pass');
assert.ok(html.includes('harness: claims 1 / evidence IDs 0 / structural fail'), 'harness arm counts and structural fail');
assert.ok(html.includes('input 100 / output 50 / total 150'), 'stage token usage shown');
assert.ok(html.includes('$0.000050'), 'stage finite cost shown');
assert.ok(html.includes('meta-test-reasoner'), 'stage/job model shown');
assert.ok(!html.includes('settled to known charges'), 'no settled wording without settled fields');

html = render([failedJob]);
assert.ok(html.includes('Trust Receipt'), 'failed job must show a Trust Receipt');
assert.ok(html.includes('VALIDATION_FAILED'), 'error code surfaced');
assert.ok(html.includes('Validation issues: 2.'), 'validation issues shown as count only');
assert.ok(html.includes('validation issues 2'), 'receipt shows issue count only');
assert.ok(!html.includes(SECRET_A) && !html.includes(SECRET_B), 'secret markers must not appear anywhere in the HTML');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost UNKNOWN, not a bill');
assert.ok(html.includes('reservation retained') && html.includes('charge uncertain'), 'reservation retained, charge uncertain');
assert.ok(!html.includes('settled to known charges'), 'no settled wording without settled fields');

html = render([queuedJob]);
assert.ok(html.includes('Trust Receipt'), 'result-less job must show a Trust Receipt');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost UNKNOWN, not a bill');
assert.ok(html.includes('reservation retained') && html.includes('charge uncertain'), 'reservation retained, charge uncertain');
assert.ok(!html.includes('settled to known charges'), 'no settled wording without settled fields');
assert.ok(html.includes('Maximum reservation: $0.0100'), 'reservation shown');
assert.ok(!html.includes('accounted cost'), 'no accounted cost without a result');
assert.ok(html.includes('Stages: none reported.'), 'empty stages disclosed');

// (4) Server-preferred Trust Receipt branch: one job rendered with a clearly
// synthetic, versioned server receipt passed as the second argument. Every
// receipt field name/shape mirrors app/ai/receipt.py build_receipt() output
// only: receipt_version, job_status, outcome, model (allowlisted), arms,
// execution_order, stages[] ({stage, model, status, elapsed_seconds, usage
// with allowlisted token keys, cost_usd, cost_basis}), arm_summaries[]
// ({arm, answer_sha256, claim_count, evidence_ids, unresolved_count,
// grounding.structural_valid}), calculated/measured costs, billing_status,
// usage_complete, settlement_status, validation_issue_count. Stage costs use
// cost_usd (the only cost key the builder emits per stage); no settled_*
// cost fields exist on either the job or the result, so no settled wording
// may appear. Token counts, SHA hex and claim counts are synthetic and chosen
// to differ from the fallback result decoys below, proving which source won.
const SECRET_C = 'sk-test-SERVER-BRANCH-MARKER-ccc333';
const serverJob = {
  id: 'job-server-synth-1',
  status: 'completed',
  reservation_usd: 0.05,
  data_use_mode: 'local-only',
  result: {
    model: 'meta-test-reasoner',
    data_use_mode: 'local-only',
    calculated_cost_usd: 0.000999,
    measured_known_cost_usd: 0.000888,
    billing_status: 'NOT_VERIFIED',
    usage_complete: false,
    elapsed_seconds: 12.5,
    stages: [
      { stage: 'reason', model: 'meta-test-reasoner', status: 'ok', elapsed_seconds: 3.25, input_tokens: 100, output_tokens: 50, total_tokens: 150, calculated_cost_usd: 0.00005 },
    ],
    outputs: {
      basic: {
        result: { answer: 'Fallback baseline answer; receipt rows must not use it.', claims: [{ text: 'Decoy claim', kind: 'fact', evidence_ids: ['ev9'] }] },
        grounding: { structural_valid: false, errors: ['decoy'] },
      },
      harness: {
        result: { answer: 'Fallback harness answer.', claims: [] },
        grounding: { structural_valid: false, errors: ['decoy'] },
      },
    },
    // Renderer must never echo raw/rejected text.
    rejected_raw: 'withheld raw text carrying ' + SECRET_C,
  },
};
const serverReceipt = {
  receipt_version: 'rw-trust-receipt-1',
  job_status: 'completed',
  outcome: 'completed',
  model: 'muse-spark-1.3',
  arms: ['basic', 'harness'],
  execution_order: ['basic', 'harness'],
  stages: [
    { stage: 'basic', model: 'muse-spark-1.3', status: 'response_received', elapsed_seconds: 2.5, usage: { input_tokens: 1111, output_tokens: 222, total_tokens: 1333, cached_tokens: 11 }, cost_usd: 0.000101, cost_basis: 'unknown' },
    { stage: 'harness', model: 'muse-spark-1.3', status: 'response_received', elapsed_seconds: 4.5, usage: { input_tokens: 3333, output_tokens: 444, total_tokens: 3777 }, cost_usd: 0.00011, cost_basis: 'unknown' },
  ],
  arm_summaries: {
    basic: { arm: 'basic', answer_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', claim_count: 7, evidence_ids: [], unresolved_count: 2, grounding: { structural_valid: true } },
    harness: { arm: 'harness', answer_sha256: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210', claim_count: 5, evidence_ids: [], unresolved_count: 1, grounding: { structural_valid: false } },
  },
  calculated_cost_usd: 0.000211,
  measured_known_cost_usd: 0.00019,
  reservation_usd: 0.05,
  provider_billed_cost_usd: null,
  billing_status: 'NOT_VERIFIED',
  usage_complete: true,
  settlement_status: null,
  pricing_date: null,
  error_code: null,
  validation_issue_count: null,
  disclaimers: ['Provider invoice not verified; calculated costs are local estimates only.'],
};

html = render([serverJob], { 'job-server-synth-1': serverReceipt });
assert.ok(html.includes('Receipt: rw-trust-receipt-1 / outcome completed'), 'server receipt header proves versioned server branch');
assert.ok(html.includes('Server receipt preferred; fallback fields shown only where the server omits them.'), 'server-preferred note shown');
assert.ok(html.includes('Outcome: completed'), 'server outcome row shown');
assert.ok(html.includes('Execution order: basic then harness'), 'server execution order shown');
assert.ok(html.includes('<li>Model: muse-spark-1.3</li>'), 'server model preferred over result decoy');
assert.ok(html.includes('input 1111 / output 222 / total 1333 / cached 11'), 'server nested usage incl. cached tokens (fallback never shows cached)');
assert.ok(html.includes('$0.000101'), 'server stage cost_usd shown');
assert.ok(html.includes('basic: claims 7 / evidence IDs 0 / answer SHA-256 0123456789ab... / structural pass / unresolved 2'), 'server basic arm summary preferred over fallback counts');
assert.ok(html.includes('harness: claims 5 / evidence IDs 0 / answer SHA-256 fedcba987654... / structural fail / unresolved 1'), 'server harness arm summary preferred');
assert.ok(html.includes('Usage completeness: complete'), 'server usage_complete preferred over result decoy');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost UNKNOWN, not a bill');
assert.ok(html.includes('Billing status: NOT_VERIFIED'), 'billing status NOT_VERIFIED');
assert.ok(html.includes('reservation retained') && html.includes('charge uncertain'), 'reservation retained, charge uncertain');
assert.ok(!html.includes('settled to known charges'), 'no settled wording without settled fields');
assert.ok(!html.includes(SECRET_C), 'withheld raw marker must not appear');
// A server receipt that omits result_integrity_verified must read as unverified,
// never as a pass.
assert.ok(html.includes('data-integrity="unverified"'), 'receipt without an integrity verdict reads as unverified');
assert.ok(!html.includes('data-integrity="verified"'), 'receipt without an integrity verdict is never marked verified');;

// (5) Negative: a receipt-like object WITHOUT receipt_version is not treated
// as a server receipt, so the fallback path renders result data instead.
const impostorJob = { ...completedJob, id: 'job-impostor-1' };
const impostorReceipt = {
  outcome: 'completed',
  billing_status: 'BILLED_IMPOSTOR',
  arms: ['basic'],
  execution_order: ['basic'],
  stages: [{ stage: 'basic', model: 'muse-spark-1.3', status: 'response_received', elapsed_seconds: 9.9, usage: { input_tokens: 9999, output_tokens: 9999, total_tokens: 19998 }, cost_usd: 0.009999, cost_basis: 'unknown' }],
  arm_summaries: { basic: { arm: 'basic', answer_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', claim_count: 99, evidence_ids: [], unresolved_count: 0, grounding: { structural_valid: true } } },
};
html = render([impostorJob], { 'job-impostor-1': impostorReceipt });
assert.ok(!html.includes('Server receipt preferred'), 'version-less receipt must not trigger server branch');
assert.ok(!html.includes('Receipt: rw-trust-receipt'), 'no server receipt header without receipt_version');
assert.ok(!html.includes('BILLED_IMPOSTOR'), 'impostor billing status ignored');
assert.ok(!html.includes('9999'), 'impostor stage tokens ignored');
assert.ok(html.includes('basic: claims 2 / evidence IDs 3 / structural pass'), 'fallback arm counts render for version-less receipt');
assert.ok(html.includes('input 100 / output 50 / total 150'), 'fallback flat stage tokens render for version-less receipt');
assert.ok(html.includes('Billing status: NOT_VERIFIED'), 'fallback billing status from result, not impostor');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost UNKNOWN, not a bill');

// (6) Display integrity, FAILED: a server receipt carrying
// result_integrity_verified=false must state the failure explicitly, must be
// visible without expanding the receipt, and must never print a settled
// amount taken from raw job/result fields (the original defect: an injected
// result.settled_cost_usd rendered as "settled to known charges").
const FAKE_SETTLED = 999.99;
const taintedJob = {
  id: 'job-integrity-false-1',
  status: 'completed',
  reservation_usd: 0.05,
  settled_cost_usd: FAKE_SETTLED,
  result: {
    model: 'meta-test-reasoner',
    data_use_mode: 'local-only',
    calculated_cost_usd: 0.000999,
    measured_known_cost_usd: 0.000888,
    billing_status: 'NOT_VERIFIED',
    usage_complete: true,
    settled_cost_usd: FAKE_SETTLED,
    settled_known_cost_usd: FAKE_SETTLED,
  },
};
const taintedReceipt = {
  ...serverReceipt,
  receipt_version: 'rw-trust-receipt-tainted-1',
  job_status: 'completed',
  result_integrity_verified: false,
  result_integrity_note: 'Stored result record changed after completion; costs are not verified — treat as untrusted.',
  settlement_status: null,
};
html = render([taintedJob], { 'job-integrity-false-1': taintedReceipt });
assert.ok(html.includes('Result integrity: FAILED'), 'integrity false must be stated as FAILED');
assert.ok(html.includes('failed integrity verification'), 'integrity false must say verification failed');
assert.ok(html.includes('NOT trustworthy'), 'integrity false must say the figures are not trustworthy');
assert.ok(html.includes('data-integrity="failed"'), 'failed integrity must carry a machine-checkable marker');
assert.ok(!html.includes('Result integrity: verified'), 'failed integrity must never read as verified');
assert.ok(!html.includes('data-integrity="verified"'), 'no verified marker on a failed row');
assert.ok(!html.includes('999.99'), 'fabricated settled amount from local fields must not be printed');
assert.ok(!html.includes('settled to known charges'), 'no settled wording on a receipt-backed row');
const taintedVisible = html.slice(0, html.indexOf('<details class="wb-receipt">'));
assert.ok(taintedVisible.includes('failed integrity verification'), 'integrity failure is visible without expanding the receipt');

// (7) completed_unknown_charge: the charge was never determined, so no
// settled dollar figure may be rendered at all — injected settlement fields on
// the job and the result are ignored, and the reservation is stated as retained.
const unknownChargeJob = {
  id: 'job-unknown-charge-1',
  status: 'completed_unknown_charge',
  reservation_usd: 0.02,
  settled_cost_usd: FAKE_SETTLED,
  result: {
    model: 'meta-test-reasoner',
    calculated_cost_usd: 0.0005,
    billing_status: 'NOT_VERIFIED',
    settled_cost_usd: FAKE_SETTLED,
    settled_known_cost_usd: FAKE_SETTLED,
  },
};
html = render([unknownChargeJob]);
assert.ok(html.includes('charge UNKNOWN'), 'unknown charge must be stated as UNKNOWN');
assert.ok(html.includes('retained'), 'unknown charge must say the reservation is retained');
assert.ok(html.includes('data-charge="unknown"'), 'unknown charge must carry a machine-checkable marker');
assert.ok(!html.includes('999.99'), 'no settled dollar figure for an unknown charge');
assert.ok(!html.includes('settled to known charges'), 'no settled wording for an unknown charge');

// Same status arriving through a server receipt: still no settled figure.
const unknownChargeReceipt = {
  ...serverReceipt,
  receipt_version: 'rw-trust-receipt-unknown-charge-1',
  job_status: 'completed_unknown_charge',
  result_integrity_verified: null,
  settlement_status: 'completed_unknown_charge',
};
html = render([{ ...unknownChargeJob, id: 'job-unknown-charge-2' }], { 'job-unknown-charge-2': unknownChargeReceipt });
assert.ok(html.includes('charge UNKNOWN'), 'receipt-reported unknown charge must be stated as UNKNOWN');
assert.ok(html.includes('data-integrity="unverified"'), 'null integrity must read as unverified, not verified');
assert.ok(!html.includes('999.99'), 'no settled dollar figure for a receipt-reported unknown charge');
assert.ok(!html.includes('settled to known charges'), 'no settled wording for a receipt-reported unknown charge');

// (8) Clean verified case renders normally: no warning styling, no fabricated
// settlement, cost rows and receipt structure unchanged.
const verifiedReceipt = {
  ...serverReceipt,
  result_integrity_verified: true,
  result_integrity_note: 'Stored result digest matches the persisted record; unsigned SHA-256, not a MAC: detects post-completion mutation, not a same-writer rewrite.',
};
html = render([{ ...serverJob, id: 'job-integrity-true-1' }], { 'job-integrity-true-1': verifiedReceipt });
assert.ok(html.includes('Result integrity: verified'), 'verified integrity is stated');
assert.ok(html.includes('data-integrity="verified"'), 'verified integrity carries a marker');
assert.ok(!html.includes('data-integrity="failed"'), 'verified row carries no failure marker');
assert.ok(!html.includes('NOT trustworthy'), 'verified row carries no untrustworthy warning');
assert.ok(html.includes('Receipt: rw-trust-receipt-1 / outcome completed'), 'receipt header still renders');
assert.ok(html.includes('Server receipt preferred; fallback fields shown only where the server omits them.'), 'server-preferred note still renders');
assert.ok(html.includes('Provider billed cost: UNKNOWN (not a bill)'), 'billed cost still UNKNOWN');
assert.ok(html.includes('$0.000101'), 'stage cost rows still render');
assert.ok(html.includes('input 1111 / output 222 / total 1333 / cached 11'), 'stage usage rows still render');
assert.ok(html.includes('Calculated known cost: $0.000211'), 'calculated cost still renders from the receipt');
assert.ok(!html.includes('settled to known charges'), 'verified row invents no settlement');
assert.ok(!html.includes('999.99'), 'verified row invents no settled amount');

// (9) Only when no server receipt exists may a local settled field be shown,
// and then it must be labelled an unverified local value.
const localSettledJob = {
  status: 'failed_transport',
  reservation_usd: 0.03,
  result: { error_code: 'TRANSPORT_ERROR', settled_cost_usd: 0.0004 },
};
html = render([localSettledJob]);
assert.ok(html.includes('$0.000400'), 'local settled field is shown only in the no-receipt fallback');
assert.ok(html.includes('unverified local value'), 'local settled field is labelled unverified');

console.log('AI_RECEIPT_FRONTEND_PASS: Trust Receipt in all three states, UNKNOWN billing, count-only validation issues, no secret leak, retained/uncertain wording');
console.log('AI_RECEIPT_SERVER_BRANCH_PASS: versioned server receipt preferred (outcome/stages/arm summaries), billed UNKNOWN/NOT_VERIFIED, no settled wording, no secret leak; version-less receipt falls back');
console.log('AI_RECEIPT_INTEGRITY_PASS: result_integrity_verified false renders a visible FAILED/untrustworthy warning, null/absent renders unverified (never verified), verified renders normally');
console.log('AI_RECEIPT_UNKNOWN_CHARGE_PASS: completed_unknown_charge never renders a settled dollar figure (local settled fields ignored), states charge UNKNOWN and reservation retained');
