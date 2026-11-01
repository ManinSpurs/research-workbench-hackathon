// Deletion-preview confirm UI: loads the real frontend/app.js in a vm harness
// with a minimal DOM double (no jsdom, no browser, no network) and exercises
// the gated delete flow against the real backend contract:
// GET /questions/{qid}/deletion-preview returns exactly
// {question_id, by_store, retained} (app/api.py preview route +
// Harness.deletion_preview), 404 for unknown questions via guard(KeyError). Clicking delete previews first; DELETE issues
// only after an explicit confirm. The confirm presentation reuses the Slice C
// inline Confirm/Cancel block style (plain text + muted class, no new panels).
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const appSource = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');
const i18nSource = await readFile(new URL('../frontend/i18n.js', import.meta.url), 'utf8');
const koSource = await readFile(new URL('../frontend/i18n.ko.js', import.meta.url), 'utf8');

function makeContext() {
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
  const calls = [];
  const ctx = {
    URLSearchParams, location: { search: '' }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => 'g-1' },
    fetch: async (url, opts) => {
      calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : undefined });
      return ctx.__impl(String(url), opts);
    },
    __impl: async () => { throw new Error('no route stubbed'); },
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      querySelector: () => ({ onclick: null }),
      createElement: (tag) => ({ tag, dataset: {}, textContent: '', innerHTML: '', href: '', onclick: null }),
    },
  };
  return { ctx, node, calls };
}

async function loadApp() {
  const { ctx, node, calls } = makeContext();
  const context = vm.createContext(ctx);
  vm.runInContext(appSource.slice(0, appSource.lastIndexOf('(async()=> {')), context);
  // Post-delete refresh targets are irrelevant here; keep them side-effect free.
  vm.runInContext('loadPending=async()=>{}; loadBriefing=async()=>{};', context);
  return { context, node, calls };
}

// Same app, but with the real translation layer (frontend/i18n.js +
// frontend/i18n.ko.js) loaded first and the language set to Korean, so the
// deletion report is exercised on the path a Korean reader actually sees. The
// language layer must be in place before app.js is evaluated because app.js
// binds its t() at load time.
async function loadAppKo() {
  const { ctx, node, calls } = makeContext();
  ctx.window = ctx;                       // i18n.js publishes window.WB_I18N
  ctx.document.documentElement = { setAttribute() {}, dataset: {} };
  const context = vm.createContext(ctx);
  vm.runInContext(i18nSource, context);
  vm.runInContext(koSource, context);
  vm.runInContext("window.WB_I18N.set('ko');", context);
  vm.runInContext(appSource.slice(0, appSource.lastIndexOf('(async()=> {')), context);
  vm.runInContext('loadPending=async()=>{}; loadBriefing=async()=>{};', context);
  return { context, node, calls };
}

const ok = (payload) => ({ ok: true, status: 200, json: async () => payload });
const fail = (status, message) => ({ ok: false, status, json: async () => ({ message }) });
const tick = async (n = 5) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };

// Real {question_id, by_store, retained, question_removed} shape: store
// names are schema table names, values are plain ints. Zero-count stores
// exist in the payload (full delete-list set per backend). retained entries
// are {name: {count, reason}}; only count>0 entries are rendered, and never
// in the removal total. question_removed is reported separately so the dialog
// can name the question-row deletion honestly.
const previewPayload = () => ({ question_id: 'q1',
  by_store: { questions: 1, answers: 2, evidence: 0 },
  retained: {
    events_payload_nulled: { count: 1, reason: 'event rows are kept; payload redacted to NULL' },
    usage_ledger_kept: { count: 1, reason: 'spend-accounting rows are kept so deletion cannot reset spend (spec section 19)' },
    latex_drafts_kept: { count: 0, reason: '0 draft(s) kept with their 0 version row(s), 0 citation row(s) and 0 compile record(s); project-owned drafts keyed by draft_id survive question deletion (design section 3.2)' },
  },
  question_removed: { count: 1, reason: 'the question row itself is deleted' } });
const deletedPayload = () => ({ question_id: 'q1', verified: true, remaining_hits: [] });

const deletesOf = (calls) => calls.filter((c) => c.method === 'DELETE' && c.url.includes('/questions/q1'));
const previewsOf = (calls) => calls.filter((c) => c.method === 'GET' && c.url.includes('/deletion-preview'));

// (a) clicking delete previews first; confirming deletes exactly once and keeps
// the existing post-delete UI behaviour.
{
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok(previewPayload());
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  assert.equal(previewsOf(calls).length, 1, 'delete first GETs the deletion preview');
  assert.equal(deletesOf(calls).length, 0, 'no DELETE before an explicit confirm');
  const confirmHtml = node('delete-status').innerHTML;
  assert.ok(confirmHtml.includes('data-testid="delete-confirm"'), 'inline confirm block shown');
  assert.ok(confirmHtml.includes('data-testid="delete-confirm-yes"'), 'explicit Confirm control');
  assert.ok(confirmHtml.includes('data-testid="delete-confirm-no"'), 'Cancel control');
  assert.ok(confirmHtml.includes('3 stored records plus the question itself'), 'total rendered from the payload, question named separately');
  assert.ok(confirmHtml.includes('questions: 1') && confirmHtml.includes('answers: 2'), 'per-store counts rendered');
  assert.ok(!confirmHtml.includes('evidence'), 'zero-count stores are not presented as removals');
  assert.ok(confirmHtml.includes('Kept by design (not deleted):'), 'retained sentence rendered');
  assert.ok(confirmHtml.includes('1 event payload redacted'), 'event retention rendered');
  assert.ok(confirmHtml.includes('1 spend-accounting row kept'), 'usage retention rendered');
  assert.ok(!confirmHtml.includes('project draft'), 'zero-count retained entries are not rendered');
  assert.ok(!confirmHtml.includes('4 stored records') && !confirmHtml.includes('5 stored records'), 'retained rows excluded from the removal total');
  assert.ok(confirmHtml.includes('Deletion cannot be undone.'), 'destructive wording kept plain');
  await vm.runInContext(`deleteConfirm();`, context);
  await tick();
  assert.equal(deletesOf(calls).length, 1, 'exactly one DELETE after confirm');
  assert.equal(node('delete-status').textContent, "Deleted from the app's stored records. Forensic erasure of database files is not performed.", 'post-delete status text states the forensic-erasure limit');
  assert.equal(vm.runInContext('S.question', context), null, 'question cleared after delete');
  // A second confirm for the same request deletes nothing more.
  await vm.runInContext(`deleteConfirm();`, context);
  await tick();
  assert.equal(deletesOf(calls).length, 1, 'no second DELETE for one confirm');
  console.log('DELETE_PREVIEW_CONFIRM_PASS');
}

// (b) cancel aborts the delete: no DELETE then, and a later confirm stays inert.
{
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok(previewPayload());
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  assert.ok(node('delete-status').innerHTML.includes('data-testid="delete-confirm"'), 'confirm shown before cancel');
  await vm.runInContext(`deleteCancel();`, context);
  assert.ok(!node('delete-status').innerHTML.includes('delete-confirm'), 'cancel removes the confirm block');
  assert.equal(deletesOf(calls).length, 0, 'cancel issues no DELETE');
  await vm.runInContext(`deleteConfirm();`, context);
  await tick();
  assert.equal(deletesOf(calls).length, 0, 'confirm after cancel issues no DELETE');
  console.log('DELETE_PREVIEW_CANCEL_PASS');
}

// (c) preview failure fails closed: the failure is shown, nothing is deleted,
// and no confirm block is offered.
{
  for (const mode of ['http-404', 'network']) {
    const { context, node, calls } = await loadApp();
    context.__impl = async (url) => {
      if (url.includes('/deletion-preview')) {
        if (mode === 'http-404') return fail(404, 'question not found');
        throw new Error('fetch failed');
      }
      if (url.includes('/questions/q1')) return ok(deletedPayload());
      throw new Error('unexpected ' + url);
    };
    await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
    await tick();
    assert.equal(deletesOf(calls).length, 0, `${mode}: no DELETE when the preview fails`);
    assert.ok(!node('delete-status').innerHTML.includes('delete-confirm'), `${mode}: no confirm block without a preview`);
    assert.ok(node('delete-status').textContent.includes('Nothing was deleted.'), `${mode}: failure shown, fail closed`);
    assert.equal(vm.runInContext('S.question', context), 'q1', `${mode}: question kept`);
  }
  console.log('DELETE_PREVIEW_FAIL_CLOSED_PASS');
}

// (d) counts render honestly from the payload: escaped store names, no invented
// categories, and an empty preview still requires the explicit confirm.
{
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1', by_store: { '<b>evil</b>': 2, real_store: 1 } });
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  const html = node('delete-status').innerHTML;
  assert.ok(html.includes('&lt;b&gt;evil&lt;/b&gt;: 2'), 'store names escaped');
  assert.ok(!html.includes('<b>evil</b>'), 'no raw markup from the payload');
  assert.ok(html.includes('real_store: 1'), 'real store shown');
  assert.ok(html.includes('3 stored records plus the question itself'), 'total is the payload sum, question named separately');
  assert.ok(!html.includes('Kept by design'), 'no retained sentence without retained counts');

  // Retained sentence renders from the new shape and stays honest: pluralised,
  // escaped, and excluded from the removal total.
  const kept = await loadApp();
  kept.context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1',
      by_store: { answers: 1 },
      retained: {
        events_payload_nulled: { count: 2, reason: 'event rows are kept; payload redacted to NULL' },
        usage_ledger_kept: { count: 1, reason: 'spend survives' },
        latex_drafts_kept: { count: 3, reason: '3 draft(s) kept with their 3 version row(s), 0 citation row(s) and 0 compile record(s); drafts survive' },
      } });
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, kept.context);
  await tick();
  const keptHtml = kept.node('delete-status').innerHTML;
  assert.ok(keptHtml.includes('1 stored record plus the question itself:'), 'singular removal total, question named');
  assert.ok(keptHtml.includes('2 event payloads redacted'), 'retained event plural rendered');
  assert.ok(keptHtml.includes('3 project drafts kept (with versions, citations and compiles)'), 'retained draft plural rendered');
  assert.ok(!keptHtml.includes('7 stored records') && !keptHtml.includes('6 stored records'), 'retained excluded from the total');

  const empty = await loadApp();
  empty.context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1', by_store: {},
      retained: {
        events_payload_nulled: { count: 0, reason: 'event rows are kept; payload redacted to NULL' },
        usage_ledger_kept: { count: 0, reason: 'spend survives' },
        latex_drafts_kept: { count: 0, reason: 'drafts survive' },
      } });
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, empty.context);
  await tick();
  assert.equal(deletesOf(empty.calls).length, 0, 'empty preview does not auto-delete');
  assert.ok(empty.node('delete-status').innerHTML.includes('No stored records found for this question.'), 'true-empty impact said plainly');
  assert.ok(!empty.node('delete-status').innerHTML.includes('Kept by design'), 'no retained sentence when nothing is retained');
  assert.ok(empty.node('delete-status').innerHTML.includes('data-testid="delete-confirm"'), 'confirm still required when empty');

  // Finding 3: total===0 with retained>0 must not claim "no stored records".
  // The empty-deletable wording applies only when nothing is retained either.
  const emptyKept = await loadApp();
  emptyKept.context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1', by_store: {},
      retained: {
        events_payload_nulled: { count: 2, reason: 'event rows are kept; payload redacted to NULL' },
        usage_ledger_kept: { count: 1, reason: 'spend survives' },
        latex_drafts_kept: { count: 0, reason: 'drafts survive' },
      } });
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, emptyKept.context);
  await tick();
  assert.equal(deletesOf(emptyKept.calls).length, 0, 'retained-only preview does not auto-delete');
  const emptyKeptHtml = emptyKept.node('delete-status').innerHTML;
  assert.ok(emptyKeptHtml.includes('No deletable stored records found for this question.'), 'retained-only empty impact is honest');
  assert.ok(!emptyKeptHtml.includes('No stored records found for this question.'), 'true-empty wording not used when retained rows exist');
  assert.ok(emptyKeptHtml.includes('Kept by design (not deleted):'), 'retained sentence rendered when retained exists');
  assert.ok(emptyKeptHtml.includes('2 event payloads redacted'), 'retained event plural rendered in empty impact');
  assert.ok(emptyKeptHtml.includes('1 spend-accounting row kept'), 'retained usage singular rendered in empty impact');
  assert.ok(emptyKeptHtml.includes('data-testid="delete-confirm"'), 'confirm still required when only retained rows exist');
  console.log('DELETE_PREVIEW_COUNTS_PASS');
}

// (e) The post-delete report is a sentence, never raw JSON. A surviving row
// used to be dumped as `Deletion incomplete: [{...},{...}]`. The report now
// names what survived and where, and states the erasure limit once.
{
  // A real backend hit list: one entry per surviving row, keyed by store.
  const surviving = () => ({ question_id: 'q1', verified: false,
    remaining_hits: [
      { table: 'library_items', column: 'title', needle: 'How long does processing take?', rows: 1 },
      { table: 'revisit_conditions', column: 'note', needle: 'revisit: How long does processing take?', rows: 1 },
    ],
    method: 'searched every text column of every table for the deleted strings; one entry per surviving row' });
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok(previewPayload());
    if (url.includes('/questions/q1')) return ok(surviving());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  await vm.runInContext(`deleteConfirm();`, context);
  await tick();
  const status = node('delete-status').textContent;
  assert.ok(status.includes('Deletion incomplete'), 'incompleteness still reported');
  assert.ok(status.includes('1 library note') && status.includes('1 revisit reminder'),
    'what survived is named by place: ' + status);
  assert.ok(!/[\[\]{}"]/.test(status), 'no raw JSON reaches the researcher: ' + status);
  assert.ok(!status.includes('library_items') && !status.includes('needle'),
    'no internal store or field names: ' + status);
  assert.ok(status.includes('Forensic erasure of database files is not performed.'),
    'the erasure limit is still stated');

  // An unknown table still gets a plain count, never JSON and never a raw name.
  const unknown = await loadApp();
  unknown.context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok(previewPayload());
    if (url.includes('/questions/q1')) return ok({ question_id: 'q1', verified: false,
      remaining_hits: [{ table: 'some_new_table', column: 'body', needle: 'x', rows: 2 }] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, unknown.context);
  await tick();
  await vm.runInContext(`deleteConfirm();`, unknown.context);
  await tick();
  const unknownStatus = unknown.node('delete-status').textContent;
  assert.ok(!/[\[\]{}"]/.test(unknownStatus), 'unknown store still no JSON: ' + unknownStatus);
  assert.ok(unknownStatus.includes('Deletion incomplete'), 'unknown store still reported');
  console.log('DELETE_PREVIEW_HONEST_REPORT_PASS');
}

// (f) The preview declares every retention it can exclude, in plain words. An
// internal key name such as `revisit_conditions_kept: 1 kept` must never reach
// the researcher; the older map handled only three keys.
{
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1', by_store: { answers: 1 },
      retained: {
        events_payload_nulled: { count: 1, reason: 'event rows are kept; payload redacted to NULL' },
        usage_ledger_kept: { count: 1, reason: 'spend-accounting rows are kept' },
        latex_drafts_kept: { count: 0, reason: 'none' },
        latex_drafts_quoting_kept: { count: 1, reason: 'another question\u2019s draft quotes this one' },
        revisit_conditions_kept: { count: 1, reason: 'reminders are kept' },
        revisit_conditions_other_kept: { count: 0, reason: 'none' },
        library_notes_kept: { count: 2, reason: 'library notes belong to the project' },
        a_future_retained_class: { count: 1, reason: 'not yet mapped to copy' },
      } });
    if (url.includes('/questions/q1')) return ok(deletedPayload());
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  const html = node('delete-status').innerHTML;
  for (const name of ['revisit_conditions_kept', 'revisit_conditions_other_kept',
                      'library_notes_kept', 'latex_drafts_quoting_kept', 'a_future_retained_class'])
    assert.ok(!html.includes(name), `internal key name ${name} never rendered: ${html}`);
  assert.ok(html.includes('1 revisit reminder kept'), 'the older missing key now has real copy');
  assert.ok(html.includes('1 draft on another question keep text quoting this question'),
    'drafts bound elsewhere are declared');
  assert.ok(html.includes('2 library notes keep text quoting this question'),
    'library notes are declared before the irreversible step');
  assert.ok(html.includes('1 kept record (kept by design)'), 'an unmapped class still renders a plain count');
  assert.ok(html.includes('Kept by design (not deleted):'), 'declaration sentence still present');
  console.log('DELETE_PREVIEW_RETAINED_COPY_PASS');
}

// (g) Korean: every absence line and summary has a Korean string, and the
// translated sentence is not escaped a second time (no &quot; / &#39; leakage
// from the established "translate at the render point" rule).
{
  const { context, node, calls } = await loadAppKo();
  context.__impl = async (url) => {
    if (url.includes('/deletion-preview')) return ok({ question_id: 'q1',
      by_store: { answers: 2, decisions: 1 },
      retained: {
        events_payload_nulled: { count: 1, reason: 'event rows are kept; payload redacted to NULL' },
        usage_ledger_kept: { count: 1, reason: 'spend-accounting rows are kept' },
        latex_drafts_kept: { count: 0, reason: 'none' },
        latex_drafts_quoting_kept: { count: 0, reason: 'none' },
        revisit_conditions_kept: { count: 1, reason: 'reminders are kept' },
        revisit_conditions_other_kept: { count: 0, reason: 'none' },
        library_notes_kept: { count: 1, reason: 'library notes belong to the project' },
      } });
    if (url.includes('/questions/q1')) return ok({ question_id: 'q1', verified: false,
      remaining_hits: [{ table: 'library_items', column: 'body_text', needle: 'x', rows: 1 }] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; deleteQuestion();`, context);
  await tick();
  const html = node('delete-status').innerHTML;
  const korean = /[\uAC00-\uD7A3]/;
  assert.ok(korean.test(html), 'Korean preview copy: ' + html);
  assert.ok(html.includes('다시 볼 조건 1건 유지'), 'the previously untranslated key is Korean');
  assert.ok(html.includes('자료 노트 1건에 이 질문을 인용한 문구가 남습니다'), 'library retention is Korean');
  // Every retained line in this payload is Korean, including the ones the old
  // summary map fell back to an internal key name for.
  assert.ok(html.includes('이벤트 페이로드 1건을 지웠습니다') && html.includes('지출 기록 1건 유지'),
    'the older retained lines stay Korean too');
  assert.ok(!html.includes('&quot;') && !html.includes('&#39;'), 'translated copy is not double-escaped');
  await vm.runInContext(`deleteConfirm();`, context);
  await tick();
  const status = node('delete-status').textContent;
  assert.ok(korean.test(status), 'Korean absence line: ' + status);
  assert.ok(status.includes('삭제가 완전히 끝나지 않았습니다'), 'incomplete line is Korean');
  assert.ok(status.includes('자료 노트 1건'), 'the surviving place is Korean');
  assert.ok(!/[\[\]{}"]/.test(status), 'no raw JSON in Korean either: ' + status);
  assert.ok(status.includes('데이터베이스 파일까지 완전히 지우는 작업은 하지 않습니다'), 'erasure limit is Korean');
  console.log('DELETE_PREVIEW_KO_PASS');
}

console.log('DELETION_PREVIEW_UI_PASS');
