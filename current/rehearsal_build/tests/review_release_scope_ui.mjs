// Release-year applicability UI: an uncheckable year must be shown as NOT
// checked, never left reading as a year-check pass, in both the arm text and
// the Trust Receipt arm rows. Runs the real frontend/ai.js in a vm with a
// minimal DOM double. No browser, no network, no backend.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const NOTE_EN = 'Release-year applicability: NOT checked. This question has no usable release year, so no year comparison was possible. This is not a year-check pass.';
const NOTE_KO = '출시 연도 적용 여부: 확인하지 않았습니다. 이 질문에는 비교 가능한 출시 연도가 없어 연도 비교가 불가능했습니다. 연도 확인을 통과한 것이 아닙니다.';
const GROUNDING_PASS = 'Evidence IDs and variable bindings passed structural checks.';

function makeNodes() {
  const nodes = new Map();
  return {
    node(id) {
      if (!nodes.has(id)) nodes.set(id, {
        value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
        files: [], dataset: {},
        classList: { add() {}, remove() {} },
        addEventListener() {}, removeEventListener() {}, setAttribute() {}, getAttribute() { return null; },
        querySelectorAll: () => [],
      });
      return nodes.get(id);
    },
  };
}

async function makeContext({ withI18n = false } = {}) {
  const { node } = makeNodes();
  const S = { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' };
  const ctx = {
    location: { search: '' }, URLSearchParams, API: 'http://localhost:8123', S,
    document: { getElementById: node, querySelectorAll: () => [], documentElement: { setAttribute() {}, dataset: {} } },
    $: node,
    esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    call: async () => { throw new Error('no network in release-scope UI test'); },
    action: async (fn) => fn(0),
    current: () => true,
    showError: () => {}, show: () => {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  if (withI18n) {
    ctx.window = ctx;                     // i18n.js publishes window.WB_I18N
    ctx.document.readyState = 'complete';
  }
  const context = vm.createContext(ctx);
  if (withI18n) {
    vm.runInContext(await readFile(new URL('../frontend/i18n.js', import.meta.url), 'utf8'), context);
  }
  vm.runInContext(await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8'), context);
  const render = (jobs, receipts) => {
    ctx.jobs_input = jobs;
    ctx.receipts_input = receipts;
    return vm.runInContext("renderAIJobs(jobs_input, receipts_input); $('ai-results').innerHTML", context);
  };
  return { context, render };
}

const countOf = (haystack, needle) => haystack.split(needle).length - 1;

// The Trust Receipt arm rows and the arm answer paragraphs both can carry the
// same sentence, so an assertion on the whole document cannot tell them apart.
// These helpers split the rendered page: the arm text is everything before the
// receipt <details>, and one receipt arm row is the <li> around its SHA-256 or
// "structural" segment.
function armTextOnly(out) {
  const at = out.indexOf('<details class="wb-receipt"');
  assert.ok(at > 0, 'Trust Receipt block is rendered');
  return out.slice(0, at);
}
function receiptArmRow(out, marker) {
  const at = out.indexOf(marker);
  assert.ok(at >= 0, `receipt arm row marker present: ${marker}`);
  return out.slice(out.lastIndexOf('<li>', at), out.indexOf('</li>', at));
}

const grounding = (extra = {}) => ({
  structural_valid: true, errors: [], citation_count: 1, invalid_citation_count: 0,
  citation_error_rate: 0, linked_claim_fraction: 1,
  semantic_correctness: 'NOT_SCORED', false_citation_rate: 'HUMAN_REVIEW_REQUIRED',
  ...extra,
});
const arm = (extraGrounding = {}) => ({
  result: { answer: 'a', claims: [{ text: 'c', kind: 'fact', evidence_ids: ['DD-2023:est_cost'] }] },
  grounding: grounding(extraGrounding),
});
const job = (extraGrounding = {}) => ({
  id: 'j-release-1', status: 'completed', reservation_usd: 0.05,
  result: {
    model: 'm', billing_status: 'NOT_VERIFIED', usage_complete: true,
    calculated_cost_usd: 0.0001, measured_known_cost_usd: 0.0001,
    stages: [], outputs: { harness: arm(extraGrounding) },
  },
});

// (1) Legacy record with no applicability block: arm text unchanged.
{
  const { render } = await makeContext();
  const out = render([job()], undefined);
  assert.ok(out.includes(GROUNDING_PASS), 'legacy pass text preserved');
  assert.ok(!out.includes('NOT checked'), 'no uncheckable line without a recorded block');
  assert.ok(!out.includes('data-release-scope'), 'no hook without a recorded block');
}

// (2) Explicit NOT_CHECKED: shown in the arm text as uncheckable, not a pass.
{
  const { render } = await makeContext();
  const out = render([job({ release_year_applicability: { status: 'NOT_CHECKED', question_release: null, mismatched_evidence_ids: [] } })], undefined);
  assert.ok(out.includes('data-release-scope="not_checked"'), 'uncheckable state carries its own hook');
  assert.ok(out.includes(NOTE_EN), 'uncheckable state is stated in English');
  assert.ok(out.includes('This is not a year-check pass.'), 'uncheckable is explicitly not a pass');
  assert.ok(!out.includes('NOTE_CHECKED_PASS_SENTINEL'), 'sanity');
  // The structural sentence may still be shown, but the year check is not
  // presented as passed: the uncheckable line accompanies it.
  const passAt = out.indexOf(GROUNDING_PASS);
  const noteAt = out.indexOf(NOTE_EN);
  assert.ok(passAt >= 0 && noteAt > passAt, 'uncheckable line follows the structural sentence');
}

// (3) Receipt arm summary path (server receipt) shows the same uncheckable fact
// in the receipt arm row itself, sourced from the receipt's arm summary and not
// from the arm output paragraph.
const notChecked = { status: 'NOT_CHECKED', question_release: null, mismatched_evidence_ids: [] };
const receiptFor = (harnessGrounding) => ({ 'j-release-1': {
  receipt_version: 'trust-receipt-1', job_status: 'completed', outcome: 'completed',
  arms: ['harness'], execution_order: ['harness'], stages: [],
  usage_complete: true, calculated_cost_usd: 0.0001, measured_known_cost_usd: 0.0001,
  billing_status: 'NOT_VERIFIED',
  arm_summaries: { harness: {
    arm: 'harness', answer_sha256: 'a'.repeat(64), claim_count: 1, evidence_ids: ['DD-2023:est_cost'],
    unresolved_count: 0,
    grounding: harnessGrounding,
  } },
} });

// (3a) Receipt summary carries NOT_CHECKED, arm output does not: the notice can
// only come from the receipt row, so the row is asserted on its own.
{
  const { render } = await makeContext();
  const out = render([job()], receiptFor({ structural_valid: true, release_year_applicability: notChecked }));
  assert.ok(out.includes('answer SHA-256'), 'server receipt arm summary path used');
  assert.equal(countOf(out, 'data-release-scope="not_checked"'), 1, 'notice rendered exactly once');
  assert.equal(countOf(out, NOTE_EN), 1, 'notice text rendered exactly once');
  assert.ok(!armTextOnly(out).includes('data-release-scope'), 'arm output paragraph carries no notice when its grounding has no block');
  assert.ok(!armTextOnly(out).includes('NOT checked'), 'arm output text stays free of the receipt-only notice');
  const row = receiptArmRow(out, 'answer SHA-256');
  assert.ok(row.includes('data-release-scope="not_checked"'), 'receipt arm row carries the uncheckable hook');
  assert.ok(row.includes('This is not a year-check pass.'), 'receipt arm row states uncheckable, not a pass');
}

// (3b) Converse: arm output carries NOT_CHECKED, receipt summary does not. The
// receipt row must not fabricate a notice from the arm output, proving the row
// reads its own summary rather than whatever the arm paragraphs printed.
{
  const { render } = await makeContext();
  const out = render([job({ release_year_applicability: notChecked })], receiptFor({ structural_valid: true }));
  assert.ok(out.includes('answer SHA-256'), 'server receipt arm summary path used');
  assert.equal(countOf(out, 'data-release-scope="not_checked"'), 1, 'notice still rendered exactly once');
  assert.ok(armTextOnly(out).includes(NOTE_EN), 'arm output paragraph keeps its own notice');
  const row = receiptArmRow(out, 'answer SHA-256');
  assert.ok(!row.includes('data-release-scope'), 'receipt arm row adds no notice absent from its summary');
  assert.ok(!row.includes('NOT checked'), 'receipt arm row states no uncheckable claim of its own');
}

// (4) MATCH / MISMATCH / UNDECLARED render no extra line (the mismatch is
// already an error; a pass or undeclared note needs no claim either way).
for (const status of ['MATCH', 'MISMATCH', 'UNDECLARED']) {
  const { render } = await makeContext();
  const out = render([job({ release_year_applicability: { status, question_release: '2023', mismatched_evidence_ids: [] } })], undefined);
  assert.ok(!out.includes('data-release-scope'), `no extra line for status ${status}`);
}

// (5) Malformed / missing block is ignored rather than rendered as a pass.
for (const bad of [null, 'NOT_CHECKED', {}, { status: 'PASSED' }, { status: 1 }]) {
  const { render } = await makeContext();
  const out = render([job({ release_year_applicability: bad })], undefined);
  assert.ok(!out.includes('data-release-scope'), `malformed block ignored: ${JSON.stringify(bad)}`);
}

// (6) Korean: the real i18n layer translates the sentence.
{
  const { context, render } = await makeContext({ withI18n: true });
  vm.runInContext("window.WB_I18N.set('ko');", context);
  const out = render([job({ release_year_applicability: { status: 'NOT_CHECKED', question_release: null, mismatched_evidence_ids: [] } })], undefined);
  assert.ok(out.includes(NOTE_KO), 'Korean translation rendered');
  assert.ok(!out.includes(NOTE_EN), 'English sentence replaced in Korean');
  vm.runInContext("window.WB_I18N.set('en');", context);
  const outEn = render([job({ release_year_applicability: { status: 'NOT_CHECKED', question_release: null, mismatched_evidence_ids: [] } })], undefined);
  assert.ok(outEn.includes(NOTE_EN), 'English is the default sentence');
}

console.log('RELEASE_SCOPE_UI_PASS: uncheckable release year is displayed as NOT checked (EN default, KO translated) in arm text and receipt rows; receipt arm row notice is asserted independently of the arm output notice (both directions); no extra line for match/mismatch/undeclared; malformed blocks ignored');
