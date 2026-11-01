// Citation Composer: the researcher starts from a sentence they wrote, attaches
// the registered evidence that supports or limits it, writes the replacement
// they would accept, and then approves or rejects that proposal.
//
// The composer PROPOSES. It never writes into a draft: approval records the
// decision, it does not edit the manuscript. Every call below is a /citations
// route; nothing here invents evidence, IDs, counts or timestamps, and an
// unavailable screen says so rather than showing placeholder proposals.

// Language lookup from i18n.js, with an English fallback so this file still runs
// in a bare JS context that has no window (the UI tests). The alias is its own
// name because app.js already owns `t` and canvas.js already owns `ct` at the
// top level of the page; every script shares one global scope.
const WB_GLOBAL_CITE = (typeof window !== 'undefined') ? window : {};
const citeT = (WB_GLOBAL_CITE.WB_I18N && WB_GLOBAL_CITE.WB_I18N.t) ? WB_GLOBAL_CITE.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});

// A create or decide in flight must not be overwritten by a later list response,
// and switching project must invalidate both. Same counter idiom as the rest of
// the frontend (libraryState.loading / latexRequest / briefingRequest).
let citationState = null;
let citationRequest = 0;

function citationMount(project) {
  citationState = { project, items: [], loading: false };
  citationRequest++;
}

function citationVisible(visible) {
  const view = $('citation-view');
  if (view && view.classList) { if (visible) view.classList.remove('hidden'); else view.classList.add('hidden'); }
  const section = document.querySelector('main section');
  if (section && section.classList) {
    if (visible) { section.classList.add('citation-mode'); section.classList.remove('library-mode', 'writing-mode'); }
    else section.classList.remove('citation-mode');
  }
  // One working surface at a time. Opening the composer closes the other two
  // rather than leaving a hidden panel mounted behind it.
  if (visible) {
    if (typeof setLibraryViewVisible === 'function') setLibraryViewVisible(false);
    if (typeof setLatexViewVisible === 'function') setLatexViewVisible(false);
  }
  for (const id of ['view-citations', 'view-writing', 'view-research']) {
    const el = $(id);
    if (el) el.setAttribute('aria-pressed', String(visible && id === 'view-citations'));
  }
  if (visible) {
    const library = document.querySelector('[data-testid="view-library"]');
    if (library) library.setAttribute('aria-pressed', 'false');
  }
}

function citationStatus(message, tone) {
  const node = $('citation-status');
  if (!node) return;
  node.textContent = message || '';
  if (node.classList) { node.classList.remove('ok', 'fail'); if (tone) node.classList.add(tone); }
}

// The list is plain bookkeeping: every field shown is a stored value. An empty
// list renders its own honest line rather than a blank panel.
function renderCitations() {
  const host = $('citation-items');
  const absence = $('citation-absence');
  if (!host) return;
  const items = (citationState && Array.isArray(citationState.items)) ? citationState.items : [];
  if (absence) {
    absence.hidden = true;
    absence.textContent = citationState
      ? (items.length ? '' : citeT('No proposals yet for this project.'))
      : citeT('Open a project to compose a citation.');
    if (!items.length) absence.hidden = false;
  }
  host.replaceChildren();
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'citation-item';
    li.setAttribute('data-testid', 'citation-item');
    li.setAttribute('data-status', String(item.status || ''));

    const head = document.createElement('p');
    head.className = 'citation-head';
    const status = document.createElement('span');
    status.className = 'citation-badge';
    status.textContent = citationStatusLabel(item.status);
    head.appendChild(status);
    const rev = document.createElement('span');
    rev.className = 'muted';
    rev.textContent = citeT(' · revision {revision}', { revision: item.revision });
    head.appendChild(rev);
    li.appendChild(head);

    li.appendChild(citationField(citeT('Your sentence'), item.selected_sentence, 'citation-selected'));
    const ids = Array.isArray(item.evidence_ids) ? item.evidence_ids.filter(id => typeof id === 'string' && id) : [];
    li.appendChild(citationField(citeT('Evidence'), ids.join(', ') || citeT('none recorded'), 'citation-evidence-ids'));
    if (item.support) li.appendChild(citationField(citeT('Supports'), item.support, 'citation-support-text'));
    if (item.limitations) li.appendChild(citationField(citeT('Does not support'), item.limitations, 'citation-limitations-text'));
    li.appendChild(citationField(citeT('Proposed replacement'), item.proposed_text, 'citation-proposed-text'));

    if (item.status === 'proposed') {
      const row = document.createElement('div');
      row.className = 'row';
      const approve = document.createElement('button');
      approve.type = 'button';
      approve.setAttribute('data-testid', 'citation-approve');
      approve.textContent = citeT('Approve');
      approve.addEventListener('click', () => citationDecide(item, 'approve'));
      const reject = document.createElement('button');
      reject.type = 'button';
      reject.setAttribute('data-testid', 'citation-reject');
      reject.textContent = citeT('Reject');
      reject.addEventListener('click', () => citationDecide(item, 'reject'));
      row.appendChild(approve);
      row.appendChild(reject);
      li.appendChild(row);
    } else if (item.decision_reason) {
      li.appendChild(citationField(citeT('Reason'), item.decision_reason, 'citation-reason'));
    }
    host.appendChild(li);
  }
}

function citationStatusLabel(status) {
  if (status === 'approved') return citeT('approved');
  if (status === 'rejected') return citeT('rejected');
  return citeT('proposed');
}

function citationField(label, value, testid) {
  const p = document.createElement('p');
  p.className = 'citation-field';
  const strong = document.createElement('strong');
  strong.textContent = label + ': ';
  p.appendChild(strong);
  // The researcher's own text is rendered as text, never as markup.
  p.appendChild(document.createTextNode(String(value)));
  p.setAttribute('data-testid', testid);
  return p;
}

async function citationLoad() {
  const project = S.project;
  if (!project) {
    if (citationState) citationState.items = [];
    renderCitations();
    citationStatus('');
    return;
  }
  if (!citationState || citationState.project !== project) citationMount(project);
  if (citationState.loading) return;
  citationState.loading = true;
  const request = ++citationRequest;
  try {
    const res = await call('GET', `/projects/${encodeURIComponent(project)}/citations`);
    if (request !== citationRequest || S.project !== project || !citationState) return;
    citationState.items = (res && Array.isArray(res.citations)) ? res.citations : [];
    renderCitations();
  } catch (e) {
    if (request !== citationRequest || S.project !== project) return;
    citationState.items = [];
    renderCitations();
    citationStatus(citeT('Citations unavailable: {reason}.', { reason: citeT('the list could not be loaded') }), 'fail');
  } finally {
    if (citationState) citationState.loading = false;
  }
}

async function openCitationView() {
  if (!S.project) {
    citationVisible(true);
    if (citationState) citationState.items = [];
    renderCitations();
    citationStatus(citeT('Open a project before composing a citation.'), 'fail');
    return;
  }
  citationVisible(true);
  citationLoad();
}

function closeCitationView() {
  citationVisible(false);
}

// The IDs are typed by the researcher, so they are split here and sent as a
// plain list. Validation of whether each ID resolves belongs to the backend:
// this screen never pretends to know which sources exist.
function citationEvidenceIds() {
  const raw = ($('citation-evidence') || {}).value || '';
  return raw.split(',').map(part => part.trim()).filter(Boolean);
}

function citationClearForm() {
  for (const id of ['citation-sentence', 'citation-evidence', 'citation-support', 'citation-limitations', 'citation-proposed']) {
    const el = $(id);
    if (el) el.value = '';
  }
  citationStatus('');
}

async function citationCreate() {
  const project = S.project;
  if (!project) { citationStatus(citeT('Open a project before composing a citation.'), 'fail'); return; }
  const sentence = (($('citation-sentence') || {}).value || '').trim();
  const proposed = (($('citation-proposed') || {}).value || '').trim();
  const evidence = citationEvidenceIds();
  // Stated plainly instead of a server round trip that can only fail.
  if (!sentence) { citationStatus(citeT('Write the sentence you want to support.'), 'fail'); return; }
  if (!evidence.length) { citationStatus(citeT('Add at least one registered source ID.'), 'fail'); return; }
  if (!proposed) { citationStatus(citeT('Write the replacement you would accept.'), 'fail'); return; }
  const request = ++citationRequest;
  citationStatus(citeT('Saving the proposal…'));
  try {
    await call('POST', `/projects/${encodeURIComponent(project)}/citations`, {
      selected_sentence: sentence,
      evidence_ids: evidence,
      support: (($('citation-support') || {}).value || '').trim() || null,
      limitations: (($('citation-limitations') || {}).value || '').trim() || null,
      proposed_text: proposed,
      question_id: S.question || null
    });
    if (request !== citationRequest || S.project !== project) return;
    citationClearForm();
    citationStatus(citeT('Proposal recorded. Nothing was written into the paper.'), 'ok');
    await citationLoad();
  } catch (e) {
    if (request !== citationRequest || S.project !== project) return;
    // A refused create names the field; the raw exception stays off this screen.
    citationStatus(serverText(e.message), 'fail');
  }
}

async function citationDecide(item, decision) {
  const project = S.project;
  if (!project || !item || !item.citation_id) return;
  let reason = null;
  if (decision === 'reject') {
    const typed = window.prompt ? window.prompt(citeT('Why are you rejecting this proposal? A reason is required.'), '') : null;
    reason = (typed || '').trim();
    if (!reason) { citationStatus(citeT('A rejection needs a reason.'), 'fail'); return; }
  }
  const request = ++citationRequest;
  try {
    await call('POST', `/citations/${encodeURIComponent(item.citation_id)}/decide`, {
      decision,
      reason,
      expected_revision: item.revision
    });
    if (request !== citationRequest || S.project !== project) return;
    citationStatus(decision === 'approve'
      ? citeT('Approved. The proposal is recorded; the paper was not edited.')
      : citeT('Rejected. The reason is recorded.'), 'ok');
    await citationLoad();
  } catch (e) {
    if (request !== citationRequest || S.project !== project) return;
    citationStatus(serverText(e.message), 'fail');
  }
}

// A language switch has to redraw what this file built: the status words, the
// field labels and the honest empty line are script output, not data-i18n nodes.
if (WB_GLOBAL_CITE.WB_I18N && WB_GLOBAL_CITE.WB_I18N.onChange) {
  WB_GLOBAL_CITE.WB_I18N.onChange(() => { try { renderCitations(); } catch (_) {} });
}

// --- Korean strings for this file ------------------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// Sentences carrying a value use {placeholder} so Korean keeps its own word
// order. The researcher's own sentence, the source IDs and the proposed text are
// data: they are never translated.
if (WB_GLOBAL_CITE.WB_I18N) WB_GLOBAL_CITE.WB_I18N.register('ko', {
  'proposed': '제안됨',
  'approved': '승인됨',
  'rejected': '거절됨',
  'Your sentence': '작성한 문장',
  'Evidence': '근거',
  'Supports': '뒷받침하는 점',
  'Does not support': '뒷받침하지 않는 점',
  'Proposed replacement': '제안하는 대체 문장',
  'Reason': '이유',
  'none recorded': '기록 없음',
  ' · revision {revision}': ' · 리비전 {revision}',
  'Approve': '승인',
  'Reject': '거절',
  'No proposals yet for this project.': '이 프로젝트에는 아직 제안이 없습니다.',
  'Open a project to compose a citation.': '인용을 작성하려면 프로젝트를 여세요.',
  'Open a project before composing a citation.': '인용을 작성하려면 먼저 프로젝트를 여세요.',
  'Citations unavailable: {reason}.': '인용 목록을 불러올 수 없습니다: {reason}.',
  'the list could not be loaded': '목록을 불러오지 못함',
  'Write the sentence you want to support.': '근거를 붙일 문장을 먼저 작성하세요.',
  'Add at least one registered source ID.': '등록된 출처 ID를 하나 이상 추가하세요.',
  'Write the replacement you would accept.': '받아들일 수 있는 대체 문장을 작성하세요.',
  'Saving the proposal…': '제안을 저장하는 중…',
  'Proposal recorded. Nothing was written into the paper.':
    '제안을 기록했습니다. 논문에는 아무것도 쓰지 않았습니다.',
  'Why are you rejecting this proposal? A reason is required.':
    '이 제안을 거절하는 이유는 무엇인가요? 이유는 반드시 적어야 합니다.',
  'A rejection needs a reason.': '거절에는 이유가 필요합니다.',
  'Approved. The proposal is recorded; the paper was not edited.':
    '승인했습니다. 제안을 기록했으며 논문은 수정하지 않았습니다.',
  'Rejected. The reason is recorded.': '거절했습니다. 이유를 기록했습니다.'
});
