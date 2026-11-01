// Slice C: prepared draft card + "Prepare this for me" + interruption/exhaustion notices.
// Runs the real frontend/app.js with a minimal DOM double. No browser, no backend:
// fetch is stubbed per test, modelling the REAL backend contract (app/api.py Slice B
// routes + app/harness.py background-preparation methods): the preparations list returns
// the {question_id, jobs, cards, scope} shape (scope from _preparation_scope_view:
// scope_id == project id, or null without an approved scope) with real DB row fields,
// Failed status reads retain the rest of the panel and expose stale status.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const appSource = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');

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
  let serial = 0;
  const calls = [];
  const ctx = {
    URLSearchParams, location: { search: '' }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => `g-${++serial}` },
    fetch: async (url, opts) => {
      calls.push({ url: String(url), method: opts && opts.method, body: opts && opts.body ? JSON.parse(opts.body) : undefined });
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
  return { context, node, calls };
}

const ok = (payload) => ({ ok: true, status: 200, json: async () => payload });
const fail = (status, message) => ({ ok: false, status, json: async () => ({ message }) });
const tick = async (n = 5) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };

const baseParts = () => ({
  where_you_left_off: [{ text: 'Left off' }],
  what_changed_since: [{ text: 'Changed' }],
  ready_now: [{ text: 'Ready' }],
  waiting_on_you: [{ text: 'Waiting' }],
  suggested_next_action: [{ text: 'Next' }],
});

const cardFixture = () => ({
  // Real prepared_cards rows carry object_id, never object_label/label (store.py
  // prepared_cards DDL; harness.py card writer inserts none). The frontend falls back
  // through c.object_label ?? c.label ?? c.object_id, so real rows render object_id.
  object_id: 'dec-1',
  status: 'draft_proposal',
  created_at: '2026-10-27T00:00:00+00:00',
  evidence: [{ evidence_id: 'ev-1', excerpt: 'INVENTED TEXT MUST NOT APPEAR' }, 'ev-2'],
  limitations_text: 'Only covers registered sources A<script>alert(1)</script>.',
  unresolved: ['Whether source B applies', '<b>bold</b> question'],
});

// (a) draft card renders heading, decision disclaimer, IDs, limits, unresolved — all escaped.
{
  const { context } = await loadApp();
  context.card_input = cardFixture();
  const out = await vm.runInContext('renderPreparedCard(card_input)', context);
  assert.ok(out.includes('Draft proposal'), 'heading has the literal words "Draft proposal"');
  assert.ok(out.includes('Nothing is decided.'), 'decision disclaimer present');
  assert.ok(out.includes('ev-1') && out.includes('ev-2'), 'evidence IDs shown');
  assert.ok(!out.includes('INVENTED TEXT MUST NOT APPEAR'), 'no invented excerpts, IDs only');
  assert.ok(out.includes('Only covers registered sources A&lt;script&gt;'), 'limitations escaped');
  assert.ok(!out.includes('<script>alert(1)'), 'no raw injection from limitations');
  assert.ok(out.includes('&lt;b&gt;bold&lt;/b&gt; question'), 'unresolved items escaped');
  assert.ok(out.includes('Whether source B applies'), 'unresolved text shown');
  assert.ok(out.includes('Prepared: dec-1.'), 'real fallback renders object_id in the Prepared line');
  assert.ok(!out.includes('deferred decision'), 'no invented object-label text');
  // A non-draft status still renders honestly: draft labeling kept, status shown plainly.
  context.card_input = { object_id: 'dec-9', status: 'completed', evidence: [], limitations_text: 'L', unresolved: [] };
  const out2 = await vm.runInContext('renderPreparedCard(card_input)', context);
  assert.ok(out2.includes('Draft proposal') && out2.includes('Nothing is decided.'), 'draft labeling kept');
  assert.ok(out2.includes('Status:') && out2.includes('completed'), 'non-draft status shown plainly');
  assert.ok(out2.includes('Prepared: dec-9.'), 'fallback renders object_id for non-draft rows too');
  console.log('PREP_CARD_PASS');
}

// (b) the card never claims a decided/adopted state for itself.
{
  const { context } = await loadApp();
  context.card_input = cardFixture();
  const out = await vm.runInContext('renderPreparedCard(card_input)', context);
  const stripped = out.replace('Nothing is decided.', '');
  assert.ok(!/decided/i.test(stripped), 'no "decided" state claim beyond the fixed line');
  assert.ok(!/adopted/i.test(stripped), 'no "adopted" state claim');
  assert.ok(!out.includes('Decision:'), 'no "Decision:" labeling');
  console.log('PREP_HONEST_PASS');
}

// (c) "Prepare this for me" on exactly the three surfaces, absent elsewhere.
{
  const { context, node, calls } = await loadApp();
  const clarifications = { clarifications: [{ cq_id: 'cq-1', question_id: 'q1', task_id: 't1', status: 'pending', clarification: { gap: 'What is X?' }, adapter: 'mock' }] };
  const briefing = { model_calls: 0, parts: baseParts() };
  context.__impl = async (url, opts) => {
    if (url.includes('/clarifications')) return ok(clarifications);
    if (url.includes('/briefing')) return ok(briefing);
    if (url.includes('/decisions')) return ok({ verdict: 'defer', reason_display: 'R', decision_id: 'dec-1' });
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [], cards: [] });
    throw new Error('unexpected ' + url);
  };
  // (i) waiting question row in the pending list.
  await vm.runInContext(`S.project='p1'; S.pack='x'; loadPending();`, context);
  await tick();
  const pending = node('pending').innerHTML;
  assert.ok(pending.includes('data-testid="prepare-pending-0"'), 'prepare button on waiting row');
  // (ii) open clarification card, both branches.
  await vm.runInContext(`S.question='q1'; S.cq='cq-1'; renderClarification({status:'pending', clarification:{gap:'G', why_matters:'W', branches:[]}});`, context);
  assert.ok(node('clarification').innerHTML.includes('data-testid="prepare-clarification"'), 'prepare button on open clarification card');
  await vm.runInContext(`renderClarification({status:'retryable', accepted_response:{kind:'unknown', value:null, client_action_id:'g-1'}, clarification:{gap:'G'}});`, context);
  assert.ok(node('clarification').innerHTML.includes('data-testid="prepare-clarification"'), 'prepare button on retryable card');
  // (iii) deferred decision row: button appears only after a defer verdict with an id.
  node('reason').value = 'Because';
  await vm.runInContext(`S.project='p1'; S.question='q1'; decide('defer');`, context);
  await tick();
  assert.ok(node('decision-status').innerHTML.includes('data-testid="prepare-decision"'), 'prepare button on deferred decision');
  // Absent elsewhere.
  await vm.runInContext(`S.question='q1'; S.cq='cq-1'; renderResult({result_kind:'EvidenceAssessment', assessment:{claim:'C', outcome:'supported', confirmation_level:'low', unresolved:[], suggested_next_action:'N'}, evidence:[]});`, context);
  assert.ok(!node('result').innerHTML.includes('Prepare this for me'), 'no prepare button on evidence card');
  await vm.runInContext(`renderDesign({body:{variables:[], assumptions:[], risks:[], verification_plan:'V'}});`, context);
  assert.ok(!node('design').innerHTML.includes('Prepare this for me'), 'no prepare button on design candidate');
  context.launch_input = [{ project_id: 'p1', project: 'Alpha', parts: { suggested_next_action: [{ text: 'Next' }] } }];
  await vm.runInContext('renderLaunchBriefing(launch_input)', context);
  assert.ok(!node('launch-briefing').innerHTML.includes('Prepare this for me'), 'no prepare button on launch rows');
  assert.ok(!node('briefing').innerHTML.includes('Prepare this for me'), 'no prepare button inside briefing parts');
  assert.equal(calls.filter((c) => c.url.includes('/prepare-request')).length, 0, 'rendering makes no prepare requests');
  console.log('PREP_BUTTONS_PASS');
}

// (c2) no button after adopt/reject verdicts.
{
  const { context, node } = await loadApp();
  const briefing = { model_calls: 0, parts: baseParts() };
  context.verdict_input = 'adopt';
  context.__impl = async (url) => {
    if (url.includes('/briefing')) return ok(briefing);
    if (url.includes('/clarifications')) return ok({ clarifications: [] });
    if (url.includes('/decisions')) return ok({ verdict: 'adopt', reason_display: 'R' });
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [], cards: [] });
    throw new Error('unexpected ' + url);
  };
  node('reason').value = 'Because <b>html</b>';
  await vm.runInContext(`S.project='p1'; S.question='q1'; decide(verdict_input);`, context);
  await tick();
  assert.ok(!node('decision-status').innerHTML.includes('Prepare this for me'), 'no prepare button after adopt');
  assert.ok(node('decision-status').textContent.includes('adopt recorded. Reason: R'), 'status uses textContent (never parsed as HTML)');
  console.log('PREP_NODEFER_PASS');
}

// (d) Cancel leaves no record and makes zero fetch calls.
{
  const { context, node, calls } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/clarifications')) return ok({ clarifications: [{ cq_id: 'cq-1', question_id: 'q1', task_id: 't1', status: 'pending', clarification: { gap: 'G?' }, adapter: 'mock' }] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.pack='x'; loadPending();`, context);
  await tick();
  calls.length = 0;
  await vm.runInContext(`prepAsk('clarification','cq-1','q1','pending');`, context);
  const confirmHtml = node('pending').innerHTML;
  assert.ok(confirmHtml.includes('data-testid="prepare-confirm"'), 'inline confirm block shown');
  assert.ok(confirmHtml.includes('Scope and allowance will be frozen when confirmed.'), 'scope placeholder shown');
  assert.ok(confirmHtml.includes('Runs only while the app is open. If you quit, the job is marked interrupted; nothing replays silently.'), 'run notice shown');
  await vm.runInContext(`prepCancel();`, context);
  assert.ok(!node('pending').innerHTML.includes('prepare-confirm'), 'cancel removes the confirm block');
  assert.equal(calls.length, 0, 'cancel makes zero fetch calls');
  console.log('PREP_CANCEL_PASS');
}

// (e) Confirm posts the pinned body shape with a generated client_action_id.
{
  const { context, node, calls } = await loadApp();
  // Real-shaped approved scope for fixture project 'p1', mirroring
  // _preparation_scope_view (harness.py:1001-1017): scope_id IS the project id.
  const approvedScope = {
    scope_id: 'p1',
    summary: 'Background scope: preparation may read only registered material plus 2 permitted sources (A, B); registered bundle bundle-sha-1.',
    allowed_sources: ['A', 'B'],
    bundle_sha256: 'bundle-sha-1',
  };
  context.__impl = async (url, opts) => {
    // Honest backend contract, enforced in stub scope semantics below:
    //  (1) api.py prepare_request body-shape guard: scope_id must be a string
    //      (null/non-string -> 409 'scope_id must be nonempty text'); the real
    //      harness guard additionally rejects empty/whitespace scope_id as
    //      'scope_id must be nonempty text' (harness.py:905-912).
    //  (2) harness.py mark_preparation: with an approved scope stored for the
    //      question's project, scope_id must EQUAL that project's id ('p1'
    //      here, since list scope_id == project id); any other value -> 409
    //      'scope_id does not match the project's stored background scope'
    //      (harness.py:938-940). This stub models an approved scope existing
    //      for 'p1' — the same scope block its /preparations stub returns.
    if (url.includes('/prepare-request')) {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      if (typeof body.scope_id !== 'string' || !body.scope_id.trim()) return fail(409, 'scope_id must be nonempty text');
      if (body.scope_id !== 'p1') return fail(409, "scope_id does not match the project's stored background scope");
      // Real mark_preparation return shape: the queued job row (harness.py:905-998).
      return ok({ id: 'prep-1', job_id: 'prep-1', project_id: 'p1', question_id: 'q1', object_type: 'decision', object_id: 'dec-1', scope_id: body.scope_id, status: 'queued', created_at: '2026-10-27T00:00:00+00:00' });
    }
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [], cards: [], scope: approvedScope });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; prepAsk('decision','dec-1','q1','decision');`, context);
  calls.length = 0;
  await vm.runInContext(`prepConfirm();`, context);
  await tick();
  const posts = calls.filter((c) => c.method === 'POST' && c.url.includes('/questions/q1/prepare-request'));
  assert.equal(posts.length, 1, 'exactly one prepare request');
  assert.equal(posts[0].body.object_type, 'decision', 'object_type pinned');
  assert.equal(posts[0].body.object_id, 'dec-1', 'object_id pinned');
  assert.equal(posts[0].body.scope_id, null, 'frontend sends scope_id null when no scope known');
  assert.ok(typeof posts[0].body.client_action_id === 'string' && /^g-\d+$/.test(posts[0].body.client_action_id), 'client_action_id generated');
  // Honest outcome: the real backend 409s a null scope, and prepConfirm surfaces the
  // server reason escaped in the target box (same error path as PREP_CONFLICT_PASS).
  assert.ok(node('decision').innerHTML.includes('scope_id must be nonempty text'), 'null-scope 409 reason surfaced, escaped');
  assert.ok(!node('decision').innerHTML.includes('Preparation queued.'), 'no queued line for a rejected null-scope request');
  // Real-shaped carried-scope SUCCESS path: the failed confirm above already
  // triggered loadPreparations(), whose stubbed payload carries data.scope
  // (scope_id == project id 'p1', summary) exactly as the real
  // list_preparations/_preparation_scope_view delivers it, so prepScopeSummary
  // populated S.prepScope with no hand-injection.
  assert.equal(vm.runInContext('S.prepScope && S.prepScope.scope_id', context), 'p1', 'real scope block carries the project id as scope_id');
  await vm.runInContext(`prepAsk('decision','dec-1','q1','decision');`, context);
  assert.ok(node('decision').innerHTML.includes('Background scope: preparation may read only registered material plus 2 permitted sources (A, B)'), 'real scope summary shown');
  calls.length = 0;
  await vm.runInContext(`prepConfirm();`, context);
  await tick();
  const posts2 = calls.filter((c) => c.method === 'POST' && c.url.includes('/questions/q1/prepare-request'));
  assert.equal(posts2.length, 1, 'exactly one carried-scope prepare request');
  assert.equal(posts2[0].body.scope_id, 'p1', 'real scope_id (project id) carried');
  assert.ok(node('decision').innerHTML.includes('Request accepted, not completed.'), 'acceptance is not completion');
  // SYNTHETIC-STATE path (hand-injected only here, where state is truly
  // fabricated: the real backend never delivers scope_id 'scope-9' for project
  // 'p1'): a mismatched scope_id must 409 like the real mark_preparation, with
  // the server reason surfaced and no queued line.
  await vm.runInContext(`S.prepScope={text:'Frozen scope: A, B.', scope_id:'scope-9'}; prepAsk('decision','dec-1','q1','decision');`, context);
  assert.ok(node('decision').innerHTML.includes('Frozen scope: A, B.'), 'hand-injected frozen scope summary shown');
  calls.length = 0;
  await vm.runInContext(`prepConfirm();`, context);
  await tick();
  const posts3 = calls.filter((c) => c.method === 'POST' && c.url.includes('/questions/q1/prepare-request'));
  assert.equal(posts3.length, 1, 'exactly one mismatched-scope prepare request');
  assert.equal(posts3[0].body.scope_id, 'scope-9', 'mismatched scope_id carried');
  assert.ok(node('decision').innerHTML.includes('scope_id does not match the project&#39;s stored background scope'), 'mismatched-scope 409 reason surfaced, escaped');
  assert.ok(!node('decision').innerHTML.includes('Preparation queued.'), 'no queued line for a mismatched-scope request');
  console.log('PREP_CONFIRM_PASS');
}

// (f) a 409 shows the server reason plainly.
{
  const { context, node } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/prepare-request')) return fail(409, 'scope changed <b>since freeze</b>');
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [], cards: [] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; prepAsk('decision','dec-1','q1','decision');`, context);
  await vm.runInContext(`prepConfirm();`, context);
  await tick();
  const out = node('decision').innerHTML;
  assert.ok(out.includes('scope changed &lt;b&gt;since freeze&lt;/b&gt;'), '409 reason shown, escaped');
  assert.ok(!out.includes('<b>since freeze</b>'), 'no raw markup from server reason');
  console.log('PREP_CONFLICT_PASS');
}

// (g) interrupted / stopped_exhausted jobs render as plain muted lines, no urgency language.
{
  const { context, node } = await loadApp();
  context.__impl = async (url) => {
    if (url.includes('/briefing')) return ok({ model_calls: 0, parts: baseParts() });
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [{ status: 'interrupted' }, { status: 'stopped_exhausted' }, { status: 'queued' }], cards: [] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p1'; S.question='q1'; S.pack='x'; loadBriefing();`, context);
  await tick();
  const out = node('briefing').innerHTML;
  assert.ok(out.includes('Preparation interrupted — nothing was replayed.'), 'interrupted notice shown');
  assert.ok(out.includes('Preparation stopped: allowance exhausted.'), 'exhaustion notice shown');
  assert.ok(out.includes('class="muted"'), 'notices are muted lines');
  assert.ok(!/deadline|estimat|urgent|as soon as possible/i.test(out), 'no deadlines, estimates, or urgency language');
  // Cards ride the same load: prepared draft appears in the briefing area.
  // Real prepared_cards DB row shape (store.py:53-56, harness.py:1211-1216): no
  // object_label, evidence/unresolved as JSON strings, status 'draft_proposal'.
  const realCardRow = {
    id: 'card-1', job_id: 'prep-1', project_id: 'p1', question_id: 'q1',
    object_type: 'decision', object_id: 'dec-1',
    evidence_json: JSON.stringify([
      { evidence_id: 'ev-1', source_id: 'src-A', excerpt: 'INVENTED TEXT MUST NOT APPEAR', location: 'L1' },
      { evidence_id: 'ev-2', source_id: 'src-A', excerpt: 'registered excerpt', location: 'L2' },
    ]),
    limitations_text: 'Only covers registered sources A<script>alert(1)</script>.',
    unresolved_json: JSON.stringify(['Whether source B applies', '<b>bold</b> question']),
    status: 'draft_proposal', created_at: '2026-10-27T00:00:00+00:00',
  };
  context.__impl = async (url) => {
    if (url.includes('/briefing')) return ok({ model_calls: 0, parts: baseParts() });
    if (url.includes('/preparations')) return ok({ question_id: 'q1', jobs: [], cards: [realCardRow] });
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`loadBriefing();`, context);
  await tick();
  const out2 = node('briefing').innerHTML;
  assert.ok(out2.includes('Draft proposal') && out2.includes('Nothing is decided.'), 'prepared card in briefing area');
  assert.ok(out2.includes('Prepared: dec-1.'), 'real row renders object_id in the Prepared line');
  // Same excerpt-leak / IDs-only guards as the hand-built fixture path (a) above,
  // mirrored onto this real row. Intended ID surface per prepEvidenceIds
  // (frontend/app.js:428-439): for each evidence object it emits evidence_id,
  // then source_id, then id (deduped, in that order) — so 'src-A' IS intended
  // rendered output (as an ID line), while excerpt/location text never renders.
  assert.ok(!out2.includes('INVENTED TEXT MUST NOT APPEAR'), 'real row: no invented excerpts, IDs only');
  assert.ok(!out2.includes('registered excerpt'), 'real row: no stored excerpt text either, IDs only');
  assert.ok(out2.includes('ev-1') && out2.includes('ev-2'), 'real row: evidence IDs shown');
  assert.ok(out2.includes('src-A'), 'real row: source_id shown as an ID line (prepEvidenceIds emits it)');
  assert.ok(out2.includes('Only covers registered sources A&lt;script&gt;'), 'real row: limitations escaped');
  assert.ok(!out2.includes('<script>alert(1)'), 'real row: no raw injection from limitations');
  assert.ok(out2.includes('&lt;b&gt;bold&lt;/b&gt; question'), 'real row: unresolved items escaped');
  assert.ok(out2.includes('Whether source B applies'), 'real row: unresolved text shown');
  assert.ok(out2.includes('Where you left off'), 'existing parts intact alongside the card');
  console.log('PREP_NOTICES_PASS');
}

// (h) unknown question id: the backend 404s (api.py guard maps KeyError -> 404,
// list_preparations raises for unknown questions at harness.py:1001), and the
// briefing + guidance rendering is unchanged — the one genuine quiet-fail path.
{
  const { context, node } = await loadApp();
  context.briefing_input = {
    model_calls: 0, parts: baseParts(),
    guidance: { level: 'firm', text: 'Consider closing Q2', basis: 'Two items verified', model_calls: 0 },
  };
  context.__impl = async (url) => {
    if (url.includes('/briefing')) return ok(context.briefing_input);
    if (url.includes('/preparations')) return fail(404, 'unknown question');
    throw new Error('unexpected ' + url);
  };
  await vm.runInContext(`S.project='p'; S.question='q'; S.pack='x'; loadBriefing();`, context);
  await tick();
  const out = node('briefing').innerHTML;
  for (const h of ['Where you left off', 'What changed since', 'Ready now', 'Waiting on you', 'Suggested next action']) {
    assert.ok(out.includes(h), `keeps "${h}"`);
  }
  assert.ok(out.includes("Secretary's note"), 'guidance note kept');
  assert.ok(out.includes('worth a look'), 'level label kept');
  assert.ok(out.includes('Two items verified'), 'basis kept');
  assert.ok(out.indexOf("Secretary's note") > out.indexOf('Suggested next action'), 'note ordering kept');
  assert.ok(out.includes('model calls: 0'), 'model-call line kept');
  assert.ok(out.includes('Preparation status is stale'), 'failed initial read exposes unknown status instead of hiding it');
  const before = out;
  await vm.runInContext(`loadPreparations();`, context);
  await tick();
  assert.ok(node('briefing').innerHTML.startsWith(before), 'failed status read preserves existing briefing');
  assert.ok(node('briefing').innerHTML.includes('Preparation status is stale'), 'failed status read is visible');
  console.log('PREP_QUIET_PASS');
}

console.log('PREPARATION_CARD_UI_PASS');
