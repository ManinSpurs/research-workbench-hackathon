// Local HTTP only. Rehearsal output is not real-model acceptance evidence.
const API = new URLSearchParams(location.search).get('api') || 'http://localhost:8123';
// composerMode is the one explicit composer choice: 'reply' | 'new' | 'talk', or null when
// no choice was made on this surface yet (automatic routing, see composerMode()).
const S = { project: null, question: null, questionText: null, briefing: null, cqPrompt: null, turns: [], cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science', composerMode: null };
let decisionRequest = null, responseRequest = null;
function requestAction(previous, payload) {
  return previous && JSON.stringify(previous.payload) === JSON.stringify(payload)
    ? previous : {payload, key: crypto.randomUUID()};
}
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Local alias for the language lookup defined in i18n.js. When that script is
// absent (a harness that loads only this file, a page that lost the script, a
// bare JS context with no window at all), this falls back to the English
// sentence with its placeholders filled, so every render still produces the same
// text it did before the language layer existed.
const WB_GLOBAL = (typeof window !== 'undefined') ? window : {};
const t = (WB_GLOBAL.WB_I18N && WB_GLOBAL.WB_I18N.t) ? WB_GLOBAL.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});
// The error line is researcher-facing, so it carries only sentences this app
// wrote or the backend wrote. A raw exception message ("Cannot read properties
// of undefined (reading 'length')", "Failed to fetch") is a developer diagnostic:
// it is logged to the console where it can be read, and the researcher sees one
// plain sentence instead. Nothing is hidden from the developer, and no failure
// is reported as success.
const ERROR_TRANSPORT = ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.', 'The network connection was lost.'];
const ERROR_UNEXPECTED = /^(Cannot read propert|Cannot set propert|undefined is not|null is not|[A-Za-z_$][\w$]* is not a function|[A-Za-z_$][\w$]* is not defined|Unexpected token|JSON\.parse)/;
function errorText(m) {
  const raw = (m == null) ? '' : String(m).trim();
  if (!raw) return '';
  if (ERROR_TRANSPORT.some(s => raw === s || raw.includes(s))) return t('The local program is not responding. Start it again, then reload this page.');
  if (ERROR_UNEXPECTED.test(raw) || /^HTTP \d{3}$/.test(raw)) return t('That action could not be completed, and nothing was changed.');
  return raw;
}
function showError(m) {
  const text = errorText(m);
  if (text && text !== String(m == null ? '' : m).trim()) { try { console.error('[workbench]', m); } catch (_) {} }
  $('error').textContent = text ? t('Error: {message}', {message: text}) : '';
}
// --- Backend sentences on the researcher's screen.
// The backend composes a handful of fixed sentences and a few templated coaching
// notes (app/harness.py, app/radar.py). Those modules stay English on purpose:
// that is the record's own wording, and the Python tests assert it verbatim.
// Translation happens here, where the text becomes screen text, and only in
// Korean mode. English stays byte-for-byte what it was: t() returns its argument
// unchanged whenever no Korean entry exists.
//
// A templated note carries values this app must not rewrite — the researcher's
// own record wording, a count, an age. Those are captured and placed back
// verbatim, so Korean keeps its own word order without touching the data.
const SERVER_NOTE_PATTERNS = [
  [/^No pressure — your latest decision is recorded, so start the next question whenever you are ready\.$/,
    () => '서두르지 않으셔도 됩니다. 최근 결정이 기록되어 있으니, 준비되실 때 다음 질문을 시작하세요.'],
  [/^No pressure — nothing is waiting, so starting a fresh question is a fine next step whenever you are ready\.$/,
    () => '서두르지 않으셔도 됩니다. 기다리는 항목이 없으니, 준비되실 때 새 질문을 시작하시면 됩니다.'],
  [/^No pressure — when you have a moment, picking up "([\s\S]*)" would be a good next step\.$/,
    m => `서두르지 않으셔도 됩니다. 시간이 되실 때 "${serverText(m[1])}"부터 이어가시면 좋겠습니다.`],
  [/^No items are waiting; (\d+) recorded decision\(s\) on file\.$/,
    m => `기다리는 항목은 없고, 결정 ${m[1]}건이 기록되어 있습니다.`],
  [/^No items are waiting and no decisions are recorded\.$/,
    () => '기다리는 항목도, 기록된 결정도 없습니다.'],
  [/^Oldest waiting age is unknown; (\d+) items? waiting\.$/,
    m => `가장 오래된 항목의 경과 시간은 확인할 수 없습니다. ${m[1]}건이 기다리고 있습니다.`],
  [/^Oldest waiting item is (\d+) days? old; (\d+) items? waiting\.$/,
    m => `가장 오래된 대기 항목은 ${m[1]}일째입니다. ${m[2]}건이 기다리고 있습니다.`],
  [/^"([\s\S]*)" has been waiting (\d+) days with (\d+) items open — please say in words what the next step is and when you will take it, or defer it explicitly with a reason\.$/,
    m => `"${serverText(m[1])}" 항목이 ${m[2]}일째 열려 있고, 함께 ${m[3]}건이 남아 있습니다. 다음 단계와 시기를 말로 정해 주시거나, 이유를 적어 명시적으로 미뤄 주세요.`],
  [/^"([\s\S]*)" has been waiting (\d+) days — please act on it or defer it explicitly so it stays a conscious choice\.$/,
    m => `"${serverText(m[1])}" 항목이 ${m[2]}일째입니다. 처리하시거나, 의식적인 선택으로 남도록 이유를 적어 미뤄 주세요.`],
  [/^(\d+) items are waiting, including "([\s\S]*)" — please act on one or defer it explicitly so nothing sits open by accident\.$/,
    m => `"${serverText(m[2])}"를 포함해 ${m[1]}건이 기다리고 있습니다. 하나를 처리하시거나, 실수로 열린 채 남지 않도록 이유를 적어 미뤄 주세요.`],
  // Readiness detail (collapsed disclosure). The counts and identifiers are
  // bookkeeping the researcher can open on demand; they stay verbatim, only the
  // wording around them is Korean.
  [/^Readiness for project ([\s\S]+) \+ pack ([\s\S]+): ([\s\S]+)\.$/,
    m => `프로젝트 ${m[1]} + 자료 묶음 ${m[2]} 준비 상태: ${m[3]}.`],
  [/^Counts: (\d+) eligible records \(confirmed decisions (\d+), evidence cards (\d+), recommendation outcomes (\d+), user corrections (\d+), registered sources (\d+), repeated gap occurrences (\d+)\); (\d+) ineligible inputs ignored\.$/,
    m => `집계: 사용 가능한 기록 ${m[1]}건 (확정 결정 ${m[2]}, 근거 카드 ${m[3]}, 권고 결과 ${m[4]}, 연구자 수정 ${m[5]}, 등록 출처 ${m[6]}, 반복된 공백 ${m[7]}); 제외된 입력 ${m[8]}건.`],
  [/^Oldest waiting age: (\d+) days?\.$/, m => `가장 오래된 대기 경과: ${m[1]}일.`],
  [/^Oldest waiting age: unknown\.$/, () => '가장 오래된 대기 경과: 확인할 수 없음.'],
  [/^Pattern gap '([\s\S]*)' seen (\d+) times \(count only; never a quality claim\)\.$/,
    m => `반복된 공백 '${m[1]}'이(가) ${m[2]}회 나타났습니다 (횟수 집계일 뿐, 품질 평가가 아닙니다).`],
  [/^(\d+) single gap occurrence\(s\) below pattern threshold; not a pattern\.$/,
    m => `한 번만 나타난 공백 ${m[1]}건은 반복 기준에 미치지 않습니다. 반복된 유형이 아닙니다.`],
  [/^Suggested next action: ([\s\S]+)$/, m => `다음에 할 일 제안: ${m[1]}`],
  [/^Evidence refs: ([\s\S]+)$/, m => `근거 참조: ${m[1]}`],
  [/^Benchmark report: (\d+) cases with refs and outcomes, no scores\.$/,
    m => `벤치마크 보고서: 참조와 결과가 있는 사례 ${m[1]}건, 점수는 없습니다.`],
  [/^Benchmark report invalid: ([\s\S]+)$/, m => `벤치마크 보고서가 올바르지 않습니다: ${m[1]}`],
  [/^Quoted recorded date: '([\s\S]*)'$/, m => `기록된 날짜 그대로: '${m[1]}'`],
  // Adaptive readiness notes (app/adaptive_readiness.py). Counts and reasons
  // stay verbatim; the sentence around them is Korean.
  [/^no eligible accumulation records yet; base guidance only\.$/,
    () => '아직 쌓인 기록이 없어 기본 안내만 드립니다.'],
  [/^(\d+) eligible records; counts only, no scores\.$/,
    m => `사용 가능한 기록 ${m[1]}건이며, 개수만 집계하고 점수는 매기지 않습니다.`],
  // Waiting-item texts composed in app/harness.py from a saved question. The
  // quoted question is the researcher's own wording and stays verbatim.
  [/^Refinement needs review: "([\s\S]*?)"?$/, m => `질문 다듬기 확인 필요: "${m[1]}"`],
  [/^Not started yet: "([\s\S]*?)"?$/, m => `아직 시작 전: "${m[1]}"`],
  [/^proposal ignored \(([\s\S]*)\); no eligible records, base guidance only\.$/,
    m => `제안을 반영하지 않았습니다(${m[1]}). 사용 가능한 기록이 없어 기본 안내만 드립니다.`],
  [/^proposal ignored \(([\s\S]*)\); (\d+) eligible records, counts only\.$/,
    m => `제안을 반영하지 않았습니다(${m[1]}). 사용 가능한 기록 ${m[2]}건을 개수만 집계했습니다.`],
  [/^version ignored \(([\s\S]*)\); no eligible records, base guidance only\.$/,
    m => `버전을 반영하지 않았습니다(${m[1]}). 사용 가능한 기록이 없어 기본 안내만 드립니다.`],
  [/^version ignored \(([\s\S]*)\); (\d+) eligible records, counts only\.$/,
    m => `버전을 반영하지 않았습니다(${m[1]}). 사용 가능한 기록 ${m[2]}건을 개수만 집계했습니다.`]
];
function langIsKo() {
  try { return WB_GLOBAL.WB_I18N && WB_GLOBAL.WB_I18N.get && WB_GLOBAL.WB_I18N.get() === 'ko'; } catch (_) { return false; }
}
function serverText(s) {
  if (s == null) return s;
  const raw = String(s);
  if (!raw) return raw;
  // English is the authored language of these strings: leave them untouched.
  if (!langIsKo()) return raw;
  const exact = t(raw);
  if (exact !== raw) return exact;
  for (const [pattern, render] of SERVER_NOTE_PATTERNS) {
    const m = raw.match(pattern);
    if (m) return render(m);
  }
  return raw;
}
function show(id, html) { $(id).innerHTML = html; $(id).classList.remove('hidden'); }
function current(epoch) { return epoch === S.epoch; }
function controls() {
  document.querySelectorAll('section button, section input, section textarea, section select, aside button').forEach(el => { el.disabled = S.busy || !S.project; });
  $('shadow-btn').disabled = $('delete-btn').disabled = S.busy || !S.question;
  document.querySelectorAll('[data-testid^="register-"]').forEach(el => { el.disabled = S.busy; });
  document.querySelectorAll('#upload-form input, #upload-form button').forEach(el => { el.disabled = S.busy; });
  $('upload-form').setAttribute('aria-busy', String(S.busy));
  // Settings owns "End session" now; the workspace header no longer carries a
  // duplicate of it, so the busy guard follows the one control that exists.
  const endBtn = $('settings-end');
  if (endBtn) endBtn.disabled = S.busy;
  $('ask-form').setAttribute('aria-busy', String(S.busy));
  if(typeof updateAIControls==='function') updateAIControls();
  const sendBtn=$('secretary-send'), composeInput=$('secretary-input');
  // Reply chosen while nothing is waiting on an answer: the send stays off and
  // the control says why, rather than turning the text into a new question.
  if(sendBtn) sendBtn.disabled = S.busy || !S.project || composerMode() === 'reply-unavailable';
  if(composeInput) composeInput.disabled = S.busy || !S.project;
  syncComposerMode();
  if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary();
}
function clearActive() {
  revisitDecision = null; revisitRows = []; revisitSerial++;
  if ($('revisit-create')) $('revisit-create').innerHTML = '';
  if ($('revisit-management')) $('revisit-management').innerHTML = '';
  S.question = S.cq = S.task = null; S.questionText = null; S.briefing = null; S.cqPrompt = null; S.turns = [];
  S.preparations = null; S.prepScope = null; prepPending = null;
  const prepBox = $('briefing');
  if (prepBox && typeof prepBox.innerHTML === 'string') { const prepCut = prepBox.innerHTML.indexOf(PREP_SECTION_MARK); if (prepCut >= 0) prepBox.innerHTML = prepBox.innerHTML.slice(0, prepCut); }
  if(typeof clearCanvas === "function") clearCanvas();
  if(typeof clearLatex === "function") clearLatex();
  if(typeof clearAI === "function") clearAI();
  decisionRequest = responseRequest = null;
  ['refinement','design','clarification','result','decision','shadow'].forEach(id => { $(id).classList.add('hidden'); if (id !== 'decision') $(id).textContent = ''; });
  ['reason','question'].forEach(id => { $(id).value = ''; });
  ['decision-status','delete-status'].forEach(id => { $(id).textContent = ''; });
  adapterReport = null;
  renderAdapterBadge();
  controls();
}
async function call(method, path, body) {
  const res = await fetch(API + path, {method, headers:{'Content-Type':'application/json'}, body:body ? JSON.stringify(body) : undefined});
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}
async function action(work) {
  if (S.busy || (WB_GLOBAL.WB_CHIP_BUSY && WB_GLOBAL.WB_CHIP_BUSY())) return;
  const epoch = S.epoch;
  S.busy = true; showError(''); controls();
  try { await work(epoch); } catch (e) { if (current(epoch)) { showError(e.message); await Promise.all([loadPending(),loadBriefing()]); } }
  finally { S.busy = false; controls(); }
}
let adapterReport = null;
function renderAdapterBadge() {
  $('adapter-badge').textContent = adapterReport == null
    ? t('REHEARSAL · adapter not yet reported')
    : adapterReport === 'mock'
      ? t('MOCK · fixture output, not real Muse')
      : t('adapter: {name}', {name: adapterReport});
}
function badge(adapter) {
  if (!adapter) return;
  adapterReport = adapter;
  renderAdapterBadge();
}
if (WB_GLOBAL.WB_I18N && WB_GLOBAL.WB_I18N.onChange) WB_GLOBAL.WB_I18N.onChange(renderAdapterBadge);
async function loadProjects() { return loadProjectsRead(false); }
async function loadProjectsRead(retried) {
  const serial = ++projectListSerial;
  // The save serial is read *before* the request: a GET that started under one
  // save and lands after that save completed (or after a newer one started) is
  // answered by stale data, and painting it would show an order the backend no
  // longer holds. The test is against the serial, not against `pending`, because
  // pending is already cleared by the time such a GET arrives.
  const orderSerial = projectOrderSerial;
  const {projects} = await call('GET', '/projects');
  const rows = Array.isArray(projects) ? projects : [];
  if (serial !== projectListSerial) return projectListRows;
  if (projectOrderPending) {
    // A refresh asked for while an order is being saved can still name a project
    // that was registered in the meantime (a new project's own refresh). Dropping
    // it silently would hide that project, so one follow-up refresh is queued to
    // run once the save has settled.
    projectListRefreshQueued = true;
    return projectListRows;
  }
  if (orderSerial !== projectOrderSerial) {
    // The save finished while this GET was in flight, so its answer predates it.
    // Ask again once: the list is the backend's, and only a fresh answer may paint.
    if (!retried) return loadProjectsRead(true);
    return projectListRows;
  }
  projectListRows = rows;
  renderProjectList();
  return projectListRows;
}
// --- Project list order -------------------------------------------------------
// The order is the backend's, not this page's. GET /projects returns the saved
// order with new projects appended, and PUT /projects/order takes the complete
// list of current project ids and answers with the rows in persisted order. No
// second copy is kept here (nothing in localStorage), so a reload always shows
// what was actually saved.
//
// Reordering is offered twice: a dedicated drag handle (pointer), and the same
// handle with Alt + ArrowUp/ArrowDown for a keyboard. Both paths end in
// commitProjectOrder, so they share one persisted outcome, one rollback and one
// spoken status line.
let projectListRows = [];
let projectListSerial = 0;      // bumps per GET; newest refresh wins
let projectOrderSerial = 0;     // bumps per save; newest save wins
let projectOrderPending = null; // the id list currently being saved
let projectDrag = null;         // the in-progress pointer drag, if any
let projectOrderStatus = null;  // the polite live region under the list
let projectListRefreshQueued = false; // a refresh was dropped mid-save; run one after

const PROJECT_DRAG_ICON = '<svg viewBox="0 0 10 16" width="10" height="16" aria-hidden="true" focusable="false" fill="currentColor"><circle cx="3" cy="3" r="1.2"/><circle cx="7" cy="3" r="1.2"/><circle cx="3" cy="8" r="1.2"/><circle cx="7" cy="8" r="1.2"/><circle cx="3" cy="13" r="1.2"/><circle cx="7" cy="13" r="1.2"/></svg>';

function projectListItems() {
  const list = $('project-list');
  return list && list.children ? [...list.children] : [];
}
function projectListRow(id) {
  return projectListItems().find(el => el.dataset && el.dataset.projectId === id) || null;
}
function projectRowHandle(li) {
  if (!li || !li.children) return null;
  return [...li.children].find(el => el.dataset && el.dataset.projectHandle === 'true') || null;
}
// The open link sits in the row body, one level down from the handle.
function projectRowLink(li) {
  if (!li || !li.children) return null;
  const groups = [li, ...li.children].filter(el => el && el.children);
  for (const group of groups) {
    const link = [...group.children].find(el => el.dataset && typeof el.dataset.testid === 'string'
      && el.dataset.testid.indexOf('open-') === 0);
    if (link) return link;
  }
  return null;
}
function projectRowName(id) {
  const row = projectListRows.find(p => p.id === id);
  return row ? row.name : id;
}
function projectOrderStatusNode() {
  if (projectOrderStatus) return projectOrderStatus;
  const list = $('project-list');
  if (!list) return null;
  const node = document.createElement('p');
  node.id = 'project-order-status';
  node.className = 'project-order-status';
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');
  node.setAttribute('data-testid', 'project-order-status');
  if (list.parentNode) list.parentNode.insertBefore(node, list.nextSibling || null);
  projectOrderStatus = node;
  return node;
}
function announceProjectOrder(text) {
  const node = projectOrderStatusNode();
  if (node) node.textContent = text;
}
// The open project is marked where style.css already expects it. A reorder must
// never move that mark: the list changes order, the selection does not.
function markOpenProject() {
  projectListItems().forEach(li => {
    const link = projectRowLink(li);
    if (!link) return;
    if (li.dataset && li.dataset.projectId === S.project) link.setAttribute('aria-current', 'true');
    else link.removeAttribute('aria-current');
  });
}
function projectRowNode(p) {
  const li = document.createElement('li');
  li.className = 'project-row';
  li.dataset.projectId = p.id;
  // A dedicated handle, not the whole row: the row still opens the project, so a
  // drag can never be mistaken for an open, and the name stays a normal link.
  const handle = document.createElement('button');
  handle.type = 'button';
  handle.className = 'project-drag-handle';
  handle.dataset.projectHandle = 'true';
  handle.dataset.testid = 'project-handle';
  handle.title = t('Drag to reorder, or press Alt with the up and down arrow keys');
  handle.setAttribute('aria-label', t('Reorder {name}. Drag, or press Alt with the up and down arrow keys.', {name: p.name}));
  handle.innerHTML = PROJECT_DRAG_ICON;
  handle.onpointerdown = e => startProjectDrag(e, p.id);
  handle.onkeydown = e => moveProjectByKeyboard(e, p.id);
  li.append(handle);
  const body = document.createElement('div');
  body.className = 'project-row-body';
  const link = document.createElement('a');
  link.href = '#'; link.dataset.testid = 'open-' + p.kind; link.textContent = p.name;
  link.onclick = e => {
    e.preventDefault();
    // The list marks the open project; mark it again only once the open actually
    // succeeded, so a refused open leaves the previous mark in place.
    openProject(p.id, p.name).then(() => markOpenProject()).catch(err => showError(err.message));
  };
  body.append(link);
  if (p.pack_id == null) {
    const attach = document.createElement('button'); attach.type='button'; attach.textContent=t('Attach to active chip');
    attach.onclick = () => attachProjectToChip(p.id,p.name);
    body.append(attach);
  }
  li.append(body);
  return li;
}
// Replacing the rows removes whatever element held the focus. Only a control that
// was inside this list is put back, at the same project: a refresh that arrives
// while the researcher types in the editor must not pull the caret out of it, and
// a keyboard reorder must not lose its place.
function projectFocusTarget() {
  const active = typeof document !== 'undefined' ? document.activeElement : null;
  if (!active || !active.closest || active === document.body) return null;
  const li = active.closest('#project-list li.project-row');
  if (!li || !li.dataset) return null;
  return {id: li.dataset.projectId, handle: !!(active.dataset && active.dataset.projectHandle === 'true')};
}
function renderProjectList(focusId) {
  const list = $('project-list');
  if (!list) return;
  const held = focusId ? null : projectFocusTarget();
  // A refresh can land in the middle of a drag (the secretary refresh, a rejected
  // save re-reading, a project registered meanwhile). The rows are rebuilt, so the
  // drag keeps the project it is moving and is re-pointed at that row's new
  // element; the insertion line is redrawn on the next pointer move, which is what
  // computes it anyway. Cancelling here instead would abort the gesture under the
  // pointer, and the Escape that followed it would no longer be the drag's key.
  const drag = projectDrag;
  list.replaceChildren(...projectListRows.map(projectRowNode));
  if (projectOrderPending) list.setAttribute('aria-busy', 'true');
  else list.removeAttribute('aria-busy');
  if (drag) {
    drag.li = projectListRow(drag.id) || drag.li;
    drag.marker = null;
    drag.index = null;
    if (drag.li && drag.li.classList) drag.li.classList.add('dragging');
    if (drag.active) list.classList.add('project-list-dragging');
  }
  markOpenProject();
  projectOrderStatusNode();
  if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary(projectListRows);
  const focus = focusId ? {id: focusId, handle: true} : held;
  if (focus && projectListRow(focus.id)) {
    const target = focus.handle ? projectRowHandle(projectListRow(focus.id)) : projectRowLink(projectListRow(focus.id));
    if (target && target.focus) target.focus();
  }
}
function projectIdsAfterMove(rows, id, index) {
  const ids = rows.map(p => p.id), from = ids.indexOf(id);
  if (from < 0) return null;
  const rest = ids.slice(); rest.splice(from, 1);
  rest.splice(Math.max(0, Math.min(rest.length, index)), 0, id);
  return rest;
}
// Ids that carry a row we do not have would drop a project from the list; the
// rows we hold that the ids do not name are kept at the end instead of vanishing.
function projectRowsInOrder(rows, ids) {
  const byId = new Map(rows.map(p => [p.id, p]));
  const ordered = ids.map(id => byId.get(id)).filter(Boolean);
  rows.forEach(p => { if (!ids.includes(p.id)) ordered.push(p); });
  return ordered;
}
function projectDragRows(drag) {
  return [...drag.list.children].filter(el => el.dataset && el.dataset.projectId && el !== drag.li);
}
function projectDragMarker(drag) {
  if (!drag.marker) {
    const marker = document.createElement('li');
    marker.className = 'project-drop-marker';
    marker.dataset.projectMarker = 'true';
    marker.setAttribute('aria-hidden', 'true');
    drag.marker = marker;
  }
  return drag.marker;
}
// Insertion point from the pointer, not from the dragged row: the first row whose
// middle is below the pointer wins, so the line shows above or below that row.
function projectDragIndex(clientY) {
  const drag = projectDrag;
  if (!drag) return null;
  const rows = projectDragRows(drag);
  for (let i = 0; i < rows.length; i++) {
    const rect = rows[i].getBoundingClientRect ? rows[i].getBoundingClientRect() : null;
    if (rect && clientY < rect.top + rect.height / 2) return i;
  }
  return rows.length;
}
function projectDragPaint(clientY) {
  const drag = projectDrag, index = projectDragIndex(clientY);
  if (!drag || index == null) return;
  const rows = projectDragRows(drag), marker = projectDragMarker(drag);
  const reference = index < rows.length ? rows[index] : null;
  if (reference) drag.list.insertBefore(marker, reference); else drag.list.append(marker);
  drag.index = index;
}
function startProjectDrag(event, id) {
  if (!event || (event.button != null && event.button !== 0) || projectDrag) return;
  if (S.busy || projectOrderPending) return;
  const list = $('project-list'), li = projectListRow(id);
  if (!list || !li) return;
  if (event.preventDefault) event.preventDefault();
  // No pointer capture and no listeners on the rows themselves: a touch that
  // grabs the handle does not scroll the list, and the list keeps scrolling
  // everywhere else while it is long.
  projectDrag = {id, list, li, startY: event.clientY, active: false, index: null, marker: null};
  document.addEventListener('pointermove', projectDragMove);
  document.addEventListener('pointerup', projectDragEnd);
  document.addEventListener('pointercancel', projectDragCancel);
  // Escape belongs to the drag while one is in progress, and it is heard before
  // the shell's own Escape (which returns to the start menu). Without the capture
  // phase a cancel would also navigate away from the workspace.
  document.addEventListener('keydown', projectDragKey, true);
}
function finishProjectDrag() {
  const drag = projectDrag;
  projectDrag = null;
  document.removeEventListener('pointermove', projectDragMove);
  document.removeEventListener('pointerup', projectDragEnd);
  document.removeEventListener('pointercancel', projectDragCancel);
  document.removeEventListener('keydown', projectDragKey, true);
  if (!drag) return;
  drag.li.classList.remove('dragging');
  drag.list.classList.remove('project-list-dragging');
  if (drag.marker && drag.marker.parentNode) drag.marker.parentNode.removeChild(drag.marker);
}
function projectDragMove(event) {
  const drag = projectDrag;
  if (!drag) return;
  if (!drag.active) {
    if (Math.abs((event.clientY || 0) - (drag.startY || 0)) < 4) return;
    drag.active = true;
    drag.li.classList.add('dragging');
    drag.list.classList.add('project-list-dragging');
  }
  projectDragPaint(event.clientY);
}
function projectDragEnd(event) {
  const drag = projectDrag;
  if (!drag) return;
  const moved = drag.active, index = moved ? projectDragIndex(event.clientY) : null;
  finishProjectDrag();
  if (!moved || index == null) return;
  const ids = projectIdsAfterMove(projectListRows, drag.id, index);
  if (ids) commitProjectOrder(ids, drag.id);
}
function projectDragCancel() {
  const drag = projectDrag;
  if (!drag) return;
  const moved = drag.active;
  finishProjectDrag();
  if (moved) announceProjectOrder(t('Move cancelled. The order is unchanged.'));
}
function projectDragKey(event) {
  if (!projectDrag || event.key !== 'Escape') return;
  // Claim the key: a cancel must not also close the workspace behind it.
  if (event.preventDefault) event.preventDefault();
  if (event.stopPropagation) event.stopPropagation();
  const moved = projectDrag.active;
  finishProjectDrag();
  if (moved) announceProjectOrder(t('Move cancelled. The order is unchanged.'));
}
// The keyboard equivalent of the drag: the same handle, Alt + ArrowUp/Down, the
// same save, and the focus follows the row the researcher just moved.
function moveProjectByKeyboard(event, id) {
  const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
  if (!step || !event.altKey) return;
  if (event.preventDefault) event.preventDefault();
  // One move at a time: a keyboard move must not start while a pointer drag is
  // still deciding where to drop, or while a save owns the list.
  if (S.busy || projectOrderPending || projectDrag) return;
  const ids = projectListRows.map(p => p.id), from = ids.indexOf(id);
  if (from < 0) return;
  const next = from + step;
  if (next < 0) { announceProjectOrder(t('Already the first project.')); return; }
  if (next > ids.length - 1) { announceProjectOrder(t('Already the last project.')); return; }
  const order = projectIdsAfterMove(projectListRows, id, next);
  if (order) commitProjectOrder(order, id);
}
// A refresh dropped while a save owned the list is run once here, after the save
// has settled, so a project that a caller registered in the meantime still reaches
// the list (and its open mark) instead of disappearing with the dropped answer.
async function runQueuedProjectRefresh() {
  if (!projectListRefreshQueued) return;
  projectListRefreshQueued = false;
  if (projectOrderPending) return;
  try { await loadProjects(); } catch (_) {}
}
async function commitProjectOrder(nextIds, movedId) {
  const before = projectListRows;
  const currentIds = before.map(p => p.id);
  if (!Array.isArray(nextIds) || nextIds.length !== currentIds.length) return before;
  if (nextIds.some(id => !currentIds.includes(id))) return before;
  if (nextIds.every((id, i) => id === currentIds[i])) return before;
  const serial = ++projectOrderSerial;
  projectOrderPending = {ids: nextIds.slice()};
  projectListRows = projectRowsInOrder(before, nextIds);
  renderProjectList(movedId);
  try {
    const result = await call('PUT', '/projects/order', {project_ids: nextIds.slice()});
    if (serial !== projectOrderSerial) return projectListRows;
    projectOrderPending = null;
    // The answer is the persisted order; it is also where a project added while
    // this save was in flight shows up, so nothing is lost in the swap.
    const saved = result && Array.isArray(result.projects) ? result.projects.filter(p => p && p.id) : [];
    projectListRows = saved.length ? saved : projectRowsInOrder(projectListRows, nextIds);
    renderProjectList(movedId);
    const position = projectListRows.findIndex(p => p.id === movedId) + 1;
    announceProjectOrder(t('Order saved. {name} is now number {position} of {count}.',
      {name: projectRowName(movedId), position: position < 1 ? '—' : position, count: projectListRows.length}));
    await runQueuedProjectRefresh();
  } catch (error) {
    if (serial !== projectOrderSerial) return projectListRows;
    projectOrderPending = null;
    projectListRows = before;
    renderProjectList(movedId);
    showError(error.message);
    announceProjectOrder(t('Order not changed.'));
    // A rejected list means the backend holds projects this page had not seen
    // (409 stale). Fetching shows them instead of leaving them out of sight.
    try { await loadProjects(); } catch (_) {}
    await runQueuedProjectRefresh();
  }
  return projectListRows;
}
async function registerProject(kind) { return action(async epoch => {
  const p = await call('POST','/projects',{kind}); await loadProjects();
  if (current(epoch)) await openProject(p.project_id,p.name);
}); }
// Count logical CSV records, respecting quoted newlines and escaped quotes.
function csvDataRows(text) {
  let quoted=false, rows=0, hasContent=false;
  for(let i=0;i<text.length;i++) {
    const c=text[i];
    if(c==='"') { hasContent=true; if(quoted && text[i+1]==='"') i++; else quoted=!quoted; }
    else if(!quoted && (c==='\n' || c==='\r')) { if(hasContent) rows++; hasContent=false; if(c==='\r' && text[i+1]==='\n') i++; }
    else hasContent=true;
  }
  if(quoted) throw new Error(t('CSV has an unclosed quoted field.'));
  if(hasContent) rows++;
  return Math.max(0,rows-1);
}
async function uploadProject(e) {
  e.preventDefault();
  return action(async epoch=> {
    $('upload-status').textContent='';
    const name=$('upload-name').value.trim(), csv=$('upload-csv').files[0], dictionaryFile=$('upload-dictionary').files[0];
    if(!name || name.length>120) throw new Error(t('Enter a project name of 1–120 characters.'));
    if(!csv || !dictionaryFile) throw new Error(t('Select both a CSV file and a JSON dictionary file.'));
    if(csv.size>2*1024*1024) throw new Error(t('CSV exceeds the 2 MiB limit.'));
    const [csv_text,dictionaryText]=await Promise.all([csv.text(),dictionaryFile.text()]);
    if(!current(epoch)) return;
    if(!csv_text.trim()) throw new Error(t('CSV is empty.'));
    if(csvDataRows(csv_text)>50000) throw new Error(t('CSV exceeds 50,000 data rows.'));
    let dictionary;
    try { dictionary=JSON.parse(dictionaryText); } catch(_) { throw new Error(t('Dictionary must contain valid JSON.')); }
    if(!dictionary || !Array.isArray(dictionary.entries) || !dictionary.entries.length || dictionary.entries.length>2000)
      throw new Error(t('Dictionary requires 1–2,000 entries.'));
    const fields=['source_id','evidence_id','field','definition','location'];
    if(dictionary.entries.some(entry=>!entry || fields.some(key=>typeof entry[key]!=='string' || !entry[key].trim())))
      throw new Error(t('Each dictionary entry needs nonempty source_id, evidence_id, field, definition, and location text.'));
    const p=await call('POST','/projects/upload',{name,csv_text,dictionary});
    if(!current(epoch)) { await loadProjects(); return; }
    $('upload-form').reset(); $('upload-status').textContent=t('Registered a local read-only copy. Original files unchanged.');
    await loadProjects(); if(current(epoch)) await openProject(p.project_id,p.name);
  });
}
async function openProject(id,name,chipOwned=false) {
  if (chipOwned !== true && window.WB_CHIP_BUSY && window.WB_CHIP_BUSY()) return;
  const epoch = S.epoch;
  const pack = S.pack;
  const [{projects}, chip] = await Promise.all([call('GET','/projects'), call('GET',`/chips/${encodeURIComponent(pack)}`)]);
  if (epoch !== S.epoch || pack !== S.pack) return;
  const project = projects.find(p => p.id === id);
  if (!project || (project.pack_id !== pack && !(project.pack_id == null && chip.source === 'builtin'))) {
    showError('This project is not linked to the active chip. Use Attach to active chip in the project list for an unbound compatible project. Foreign linked projects must be opened with their own chip.');
    return;
  }
  const switchingFrom = S.project;
  const switched = S.project !== id;
  // Switching projects replaces the writing surface, so the draft being edited
  // must be flushed first. latex.js owns that flush (writing-ui worker); when the
  // hook exists and reports false, the flush failed or hit a conflict, and the
  // switch does not happen: S.project, the editor and the open draft stay as they
  // were, so no unsaved text is dropped. The await can overlap another switch, so
  // everything openProject checks before it is checked again after it.
  // latex.js owns that flush (writing-ui worker) and publishes it on window; both
  // spellings are checked so a harness that loads only part of this file still
  // resolves the hook.
  const hookHost = (typeof window !== 'undefined' && typeof window.latexBeforeProjectSwitch === 'function') ? window
    : (typeof WB_GLOBAL !== 'undefined' && WB_GLOBAL && typeof WB_GLOBAL.latexBeforeProjectSwitch === 'function') ? WB_GLOBAL : null;
  if (switched && hookHost) {
    const flushed = await hookHost.latexBeforeProjectSwitch();
    if (epoch !== S.epoch || pack !== S.pack || S.project !== switchingFrom) return;
    // A refused flush would otherwise look like a click that did nothing: the
    // writing view can be hidden, so its own status line is not on screen. The
    // project, the editor and the draft stay exactly as they were.
    if (!flushed) { showError(t('Not opened: the previous paper has unsaved changes that could not be saved.')); return; }
  }
  S.epoch++; S.project=id; S.pending=[]; clearActive(); showError('');
  // The list marks the open project. Setting it here, not only in the row's own
  // click handler, keeps the mark right when a switch races a list refresh (a
  // queued one can paint before this assignment) or is triggered another way.
  try { markOpenProject(); } catch (_) {}
  // Membership is checked asynchronously, so the legacy synchronous wrapper
  // cannot observe this switch. Drop the previous project's review here.
  if (switched && typeof secretaryReviewReset === 'function') secretaryReviewReset();
  $('profile').textContent=t('Loading profile…'); $('project-title').textContent=name; $('briefing').textContent=t('Loading briefing…'); $('pending').textContent=t('Loading clarifications…');
  try { localStorage.setItem('wb.project',JSON.stringify({id,name})); } catch (_) {}
  await Promise.all([loadBriefing(),loadPending(),loadProfile()]);
}
async function attachProjectToChip(id,name) {
  if (S.busy || (window.WB_CHIP_BUSY && window.WB_CHIP_BUSY())) return;
  const pack=S.pack, epoch=S.epoch;
  S.busy=true;
  try {
    await call('POST',`/projects/${encodeURIComponent(id)}/chip`,{pack_id:pack});
    if (epoch !== S.epoch || pack !== S.pack) return;
    await loadProjects();
    await openProject(id,name);
  } catch(e) { if (epoch === S.epoch) showError(e.message); }
  finally { S.busy=false; controls(); }
}
async function loadProfile() {
  const epoch=S.epoch;
  try {
    const p=await call('GET',`/projects/${S.project}/profile`);
    if(!current(epoch)) return;
    $('dataset-select').replaceChildren(new Option(t('Use question wording or all registered datasets'),''),
      ...(p.datasets || []).map(id=>new Option(id,id)));
    $('profile').innerHTML=`<p class="muted">${esc(p.computed_by)}</p><div class="table-scroll"><table><thead><tr><th>${esc(t('Variable'))}</th><th>${esc(t('File'))}</th><th>${esc(t('Rows'))}</th><th>${esc(t('Missing'))}</th><th>${esc(t('Distinct'))}</th><th>${esc(t('Examples'))}</th></tr></thead><tbody>${p.variables.map(v=>`<tr data-canvas-dataset="${esc(v.file)}" data-canvas-variable="${esc(v.variable)}"><td>${esc(v.variable)}</td><td>${esc(v.file)}</td><td>${esc(v.rows)}</td><td>${esc(v.missing)}</td><td>${esc(v.distinct)}</td><td>${esc((v.examples || []).join(', '))}</td></tr>`).join('')}</tbody></table></div>`;
    if(p.registered_sources) $('profile').innerHTML+=`<details><summary>${esc(t('Registered source IDs and versions'))}</summary><ul>${p.registered_sources.map(s=>`<li><code>${esc(s.source_id)}</code> / ${esc(s.version || s.source_version || t('version not provided'))}</li>`).join('')}</ul></details>`;
  } catch(e) { if(current(epoch)) { $('profile').textContent=t('Profile unavailable.'); showError(e.message); } }
}
// --- Domain packs ---------------------------------------------------------
// The #pack-select options used to be authored in index.html, which hard-coded the
// two sample packs and hid every chip the researcher installed. The server is the
// only source of truth for which packs exist, so the list is rebuilt from it: the
// samples, then each installed chip under its own name.
let domainPacks = [];
async function loadDomainPacks() {
  try {
    const r = await call('GET', '/domain-packs');
    domainPacks = Array.isArray(r.packs) ? r.packs : [];
  } catch (_) { domainPacks = []; return; }
  const sel = $('pack-select');
  if (!sel) return;
  const chosen = sel.value || S.pack;
  sel.replaceChildren(...domainPacks.map(p => new Option(`${p.title} ${p.version}`, p.pack_id)));
  if (domainPacks.some(p => p.pack_id === chosen)) sel.value = chosen;
  else if (domainPacks.length) sel.value = domainPacks[0].pack_id;
  S.pack = sel.value;
}
// Chips the research field picker offers: every usable pack, in server order.
// WB_GLOBAL, not window: the vm-hosted verification tests provide no window.
WB_GLOBAL.WB_DOMAIN_PACKS = function () { return domainPacks.slice(); };

let briefingRequest = 0, pendingRequest = 0;
// The research field is remembered across launches. The cartridge start screen
// (startup.js) writes this same key on an explicit insert, and changePack()
// rewrites it when #pack-select changes.
const PACK_KEY = 'wb.pack';
function savePack() { try { localStorage.setItem(PACK_KEY, S.pack); } catch (_) {} }
// #pack-select stays the in-workspace control for new questions. Re-rooting the
// field can only happen through the start screen, so announce it there.
function syncStartupField() {
  try {
    if (!window.WB_STARTUP || !window.WB_STARTUP.setField) return;
    const sel = $('pack-select'); if (sel) window.WB_STARTUP.setField(sel.value);
  } catch (_) {}
}
async function changePack(chipOwned=false) {
  if(S.busy) return;
  if (chipOwned !== true && window.WB_CHIP_BUSY && window.WB_CHIP_BUSY()) return;
  if (S.pack !== $('pack-select').value) {
    S.project=null; S.pending=[]; S.briefing=null;
    try { localStorage.removeItem('wb.project'); } catch (_) {}
  }
  S.pack=$('pack-select').value;
  S.epoch++; clearActive(); savePack();
  syncStartupField();
  await Promise.all([loadBriefing(),loadPending()]);
}
// --- Secretary coaching/radar render (inventory item C4; S4 frontend part).
// Quiet research-tool styling only: existing h3/p/muted classes, no gradients,
// glow, emoji, or marketing copy. Coaching lines render ONLY from briefing
// payload fields that exist today (guidance note, top items, waiting counts).
// renderRadarBlock() is the per-project briefing mount point for the future S2
// radar feed (stale flags, ready_now card lines); renderLaunchRadarLine() is
// the launch-surface mount point (one mount point per surface). While those
// fields are absent each returns '', never placeholder flags, stale warnings,
// or sample data.
function renderCoachingLine(b, levelLabel) {
  const g = b && b.guidance;
  if (!g || typeof g !== 'object' || Array.isArray(g)) return '';
  if (g.text == null || String(g.text).trim() === '') return '';
  const basis = (g.basis != null && String(g.basis).trim() !== '') ? `<p class="muted">${esc(serverText(g.basis))}</p>` : '';
  return `<h3>${t("Secretary's note")}</h3><p>${esc(serverText(g.text))} <span class="muted">${esc(t(levelLabel))}</span></p>${basis}`;
}
function renderRadarBlock(b) {
  const radar = b && b.radar;
  if (!radar || typeof radar !== 'object' || Array.isArray(radar)) return '';
  const hasText = v => v != null && String(v).trim() !== '';
  const flags = Array.isArray(radar.stale_flags) ? radar.stale_flags.filter(f => f && hasText(f.text)) : [];
  const cards = Array.isArray(radar.ready_now_cards) ? radar.ready_now_cards.filter(c => c && hasText(c.text)) : [];
  if (!flags.length && !cards.length) return '';
  const shown = flags.slice(0, 3);
  return (shown.length ? `<h3>${esc(t('Project radar'))}</h3><ul>${shown.map(f => {
    const proj = (f.project != null && String(f.project).trim() !== '') ? esc(f.project) : '';
    const txt = hasText(f.text) ? esc(serverText(f.text)) : '';
    const sep = (proj && txt) ? ' — ' : '';
    const waiting = Number.isFinite(f.waiting_count) ? ` <span class="muted">${esc(t('{count} waiting', {count: f.waiting_count}))}</span>` : '';
    return `<li>${proj}${sep}${txt}${waiting}</li>`;
  }).join('')}</ul>` : '')
    + (cards.length ? cards.map(c => `<p class="muted">${esc(c.text)}</p>`).join('') : '');
}
// --- Secretary conversation (two-sided log).
// The panel reads as a conversation: the secretary speaks on the left, the
// researcher on the right, oldest at the top. NOTHING here is invented: every
// secretary bubble is built from a briefing/clarification payload field, and a
// researcher bubble only appears for wording the app actually holds (the text
// the researcher typed, or a saved clarification response value). When the
// records carry nothing, the panel says so in one muted line.
function researcherTurn(text, note) {
  if (text == null || String(text).trim() === '') return '';
  return `<div class="turn turn-researcher"><p class="turn-bubble">${esc(text)}${note ? ` <span class="muted">${esc(t(note))}</span>` : ''}</p></div>`;
}
function secretaryTurn(html) { return `<div class="turn turn-secretary">${html}</div>`; }
const RESEARCHER_TURN_MARK = '<div class="turn turn-researcher">';
// The researcher's side of the log: only turns the app actually holds. S.turns
// holds this session's own typed turns in the order they were sent; the current
// question wording is shown too, but never twice.
function researcherTurnsHtml() {
  const turns = Array.isArray(S.turns) ? S.turns : [];
  const html = turns.map(t => researcherTurn(t.text, t.note)).join('');
  const current = S.questionText;
  const already = current != null && turns.some(t => t.text === current);
  return html + (already ? '' : researcherTurn(current, 'your question'));
}
// Re-render only the researcher side once the wording for the open question
// becomes known (the saved-question list arrives after the briefing).
function refreshResearcherTurns() {
  const box = $('briefing');
  if (!box || typeof box.innerHTML !== 'string') return;
  const cut = box.innerHTML.indexOf(RESEARCHER_TURN_MARK);
  const head = cut >= 0 ? box.innerHTML.slice(0, cut) : box.innerHTML;
  box.innerHTML = head + researcherTurnsHtml();
}
// The wording of a question the app already has on record, if any: the saved
// clarifications carry question_id, and canvas.js holds the pack's remembered
// questions (original_text). No wording is ever constructed.
function savedQuestionWording(qid) {
  try {
    if (typeof savedCanvasQuestions === 'undefined' || !Array.isArray(savedCanvasQuestions) || !qid) return null;
    const q = savedCanvasQuestions.find(x => x && x.id === qid);
    return q && q.original_text ? String(q.original_text) : null;
  } catch (_) { return null; }
}
// Fill in the researcher's side from wording the app already holds: the open
// clarification's question, or the first waiting one. If no saved wording is
// available, nothing is shown — the panel never invents a past question.
function adoptSavedWording() {
  if (S.questionText) return;
  const rec = (S.cq && S.pending.find(c => c && c.cq_id === S.cq)) || S.pending[0];
  const wording = rec ? savedQuestionWording(rec.question_id) : null;
  if (!wording) return;
  S.questionText = wording;
  refreshResearcherTurns();
}
// Scroll the log to the newest turn — but only when the person was already
// reading the newest end. Re-reading an older saved briefing is a normal thing to
// do, and a render that yanks the log to the bottom takes the sentence they were
// reading out of view. `pinned` is measured BEFORE the new HTML is written, so
// the decision describes where the person was, not where the render left them.
// Guarded for the harnesses: a DOM double without layout scrolls nothing.
const SECRETARY_SCROLL_SLACK = 24;
function secretaryLogPinned() {
  const log = $('secretary-log');
  if (!log || typeof log.scrollHeight !== 'number' || typeof log.clientHeight !== 'number'
    || typeof log.scrollTop !== 'number') return true;
  return log.scrollHeight - log.scrollTop - log.clientHeight <= SECRETARY_SCROLL_SLACK;
}
function scrollSecretaryToBottom(pinned) {
  const log = $('secretary-log');
  if (!log || typeof log.scrollHeight !== 'number') return;
  if (pinned === false) return;
  log.scrollTop = log.scrollHeight;
}
// The next required action leads the briefing; the rest of the saved record is
// long, so it opens one click away instead of pushing the one line that matters
// off the top of the panel. Both halves render from the same payload fields.
function briefingTextSet(parts) {
  const seen = new Set();
  return text => {
    const key = String(text ?? '');
    if (!key.trim() || seen.has(key)) return false;
    seen.add(key);
    return true;
  };
}
// `keep` is the shared first-occurrence guard, applied to the payload's fields in
// the order they are printed. A list that was already filtered passes no guard: a
// second pass over the same set would drop the very lines it just admitted.
function briefItemsHtml(list, keep) {
  const items = (Array.isArray(list) ? list : []).filter(i => i && i.text != null
    && (keep ? keep(i.text) : String(i.text).trim() !== ''));
  if (!items.length) return `<p class="muted">${esc(t('Nothing recorded.'))}</p>`;
  return `<ul>${items.map(i => `<li>${esc(serverText(i.text))}${i.saved_at ? ` <span class="muted">${esc(t('saved {stamp}', {stamp: i.saved_at}))}</span>` : ''}${i.verified_at ? ` <span class="muted">${esc(t('verified {stamp}', {stamp: i.verified_at}))}</span>` : ''}</li>`).join('')}</ul>`;
}
async function loadBriefing() {
  if (!S.project) return;
  const epoch=S.epoch, request=++briefingRequest;
  try {
    const b=await call('GET',`/projects/${S.project}/briefing?pack_id=${encodeURIComponent(S.pack)}`);
    if (!current(epoch) || request !== briefingRequest) return;
    S.briefing = b;
    if ($('revisit-management')) loadRevisits();
    // --- Secretary coaching/radar render (inventory item C4).
    // Renders coaching lines ONLY from briefing payload fields that exist today
    // (guidance note, top items, waiting counts). Designed fields that do not
    // exist yet (radar stale flags, ready_now card lines) render nothing: one
    // mount point per surface — renderRadarBlock() for the per-project
    // briefing, renderLaunchRadarLine() for the launch surface — for the
    // later S2 wiring.
    const levelLabel={calm:'note',firm:'worth a look',pointed:'needs a decision'}[(b && b.guidance && b.guidance.level)] || 'note';
    const guidanceHtml=renderCoachingLine(b, levelLabel);
    const radarHtml=renderRadarBlock(b);
    const parts = (b && b.parts) || {};
    // The clarification the app is currently waiting on is a secretary message
    // too, but only when it is not already shown in the waiting list below (the
    // same record must not appear twice).
    const inWaiting = S.cq && (S.pending || []).some(c => c && c.cq_id === S.cq);
    const promptHtml = (S.cqPrompt && !inWaiting)
      ? secretaryTurn(`<section class="turn-section"><h3>${esc(t('Clarification'))}</h3>`
        + `<p>${esc(S.cqPrompt.gap)}</p>`
        + (S.cqPrompt.why_matters ? `<p class="muted">${esc(S.cqPrompt.why_matters)}</p>` : '') + '</section>')
      : '';
    // The next required action leads the briefing; the rest of the saved record is
    // long, so it opens one click away instead of pushing the one line that
    // matters off the top of the panel. Both halves render from the same payload
    // fields, and both keep the section headings the panel has always shown.
    const keep = briefingTextSet(parts);
    const nextList = Array.isArray(parts.suggested_next_action) ? parts.suggested_next_action : [];
    const nextItems = nextList.filter(i => i && i.text != null && keep(i.text));
    const lead = nextItems.slice(0, 1), rest = nextItems.slice(1);
    const nextHtml = '<section class="brief-next" data-testid="brief-next-action">'
      + `<h3>${esc(t('Suggested next action'))}</h3>`
      + (lead.length
        ? `<p class="brief-next-text">${esc(serverText(lead[0].text))}</p>`
        : `<p class="muted">${esc(t('No next action recorded.'))}</p>`)
      + '</section>';
    const savedHtml = '<details class="brief-saved" open><summary>' + esc(t('Saved briefing'))
      + '</summary>'
      + (rest.length ? `<section class="turn-section"><h3>${esc(t('Suggested next action'))}</h3>${briefItemsHtml(rest)}</section>` : '')
      + `<section class="turn-section brief-decisions"><h3>${esc(t('Where you left off'))}</h3>${briefItemsHtml(parts.where_you_left_off, keep)}</section>`
      + `<section class="turn-section"><h3>${esc(t('What changed since'))}</h3>${briefItemsHtml(parts.what_changed_since, keep)}</section>`
      + `<section class="turn-section"><h3>${esc(t('Ready now'))}</h3>${briefItemsHtml(parts.ready_now, keep)}</section>`
      + `<section class="turn-section brief-waiting"><h3>${esc(t('Waiting on you'))}</h3>${briefItemsHtml(parts.waiting_on_you, keep)}</section>`
      + '</details>';
    const pinned = secretaryLogPinned();
    // The report comes first: it is the secretary's arrival message, built from
    // the saved briefing. The researcher's own question follows it as the newest
    // turn, so the log reads oldest-to-newest top-to-bottom.
    $('briefing').innerHTML = secretaryTurn(
      nextHtml + savedHtml
      + guidanceHtml + radarHtml
      + `<p class="muted">${esc(t('Built from saved records; model calls: {count}', {count: b.model_calls}))}</p>`)
      + promptHtml
      + researcherTurnsHtml();
    if (S.question) loadPreparations();
    adoptSavedWording();
    scrollSecretaryToBottom(pinned);
    if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary();
  } catch(e) { if(current(epoch)) showError(e.message); }
}
// --- Background preparation (Slice C): prepared draft cards + explicit marks.
// Quiet, non-AI research-tool styling only: text labels, existing muted class.
// Every network call below fails quietly: unknown endpoints or payload shapes
// leave the rest of the panel unchanged.
let prepPending = null;
const PREP_SECTION_MARK = '<section data-testid="preparations">';
const PREP_CONFIRM_MARK = '<div data-testid="prepare-confirm">';
const PREP_RESULT_MARK = '<p class="muted" data-testid="prepare-result">';
const PREP_NOTICE_INTERRUPTED = 'Preparation interrupted — nothing was replayed.';
const PREP_NOTICE_EXHAUSTED = 'Preparation stopped: allowance exhausted.';
const PREP_SCOPE_PLACEHOLDER = 'Scope and allowance will be frozen when confirmed.';
const PREP_RUN_NOTICE = 'Runs only while the app is open. If you quit, the job is marked interrupted; nothing replays silently.';
function prepSafeId(v) { return String(v ?? '').replace(/[^A-Za-z0-9_-]/g, ''); }
function prepAsList(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') { try { const p = JSON.parse(v); return Array.isArray(p) ? p : (v ? [v] : []); } catch (_) { return v ? [v] : []; } }
  return [];
}
function prepEvidenceIds(evidence) {
  const ids = [];
  for (const e of prepAsList(evidence)) {
    if (typeof e === 'string') { if (e && !ids.includes(e)) ids.push(e); }
    else if (e && typeof e === 'object') {
      for (const k of ['evidence_id', 'source_id', 'id']) {
        if (e[k] != null && String(e[k]) && !ids.includes(String(e[k]))) ids.push(String(e[k]));
      }
    }
  }
  return ids;
}
function renderPreparedCard(card) {
  const c = (card && typeof card === 'object') ? card : {};
  const label = c.object_label ?? c.label ?? c.object_id ?? t('this item');
  const status = c.status ?? 'draft_proposal';
  const ids = prepEvidenceIds(c.evidence ?? c.evidence_json ?? c.evidence_ids ?? []);
  const limitations = c.limitations_text ?? c.limitations ?? '';
  const unresolved = prepAsList(c.unresolved ?? c.unresolved_json ?? [])
    .map(x => (x && typeof x === 'object' ? (x.text ?? '') : x)).filter(x => x !== '' && x != null);
  const created = c.created_at ?? c.saved_at ?? '';
  return `<div class="card" data-testid="prepared-card"><h3>${esc(t('Draft proposal'))}</h3><p>${esc(t('Nothing is decided.'))}</p>`
    + `<p class="muted">${esc(t('Prepared: {label}.', {label: String(label)}))}</p>`
    + (status === 'draft_proposal' ? '' : `<p class="muted">${esc(t('Status: {status}', {status: String(status)}))}</p>`)
    + `<h3>${esc(t('Evidence'))}</h3>` + (ids.length ? `<ul>${ids.map(id => `<li><code>${esc(id)}</code></li>`).join('')}</ul>` : `<p class="muted">${esc(t('No evidence cited.'))}</p>`)
    + `<h3>${esc(t('Limitations'))}</h3><p>${limitations ? esc(limitations) : esc(t('None recorded.'))}</p>`
    + `<h3>${esc(t('Still unresolved'))}</h3>` + (unresolved.length ? `<ul>${unresolved.map(u => `<li>${esc(u)}</li>`).join('')}</ul>` : `<p class="muted">${esc(t('nothing listed'))}</p>`)
    + (created ? `<p class="muted">${esc(t('saved {stamp}', {stamp: String(created)}))}</p>` : '') + '</div>';
}
function prepScopeSummary(data) {
  if (!data || typeof data !== 'object') return null;
  const cand = data.scope_summary ?? data.scope ?? data.background_scope ?? null;
  if (typeof cand === 'string') return cand ? {text: cand, scope_id: data.scope_id ?? null} : null;
  if (cand && typeof cand === 'object') {
    const text = cand.summary ?? cand.text ?? null;
    return {text: typeof text === 'string' ? text : '', scope_id: cand.scope_id ?? cand.id ?? data.scope_id ?? null};
  }
  if (data.scope_id != null) return {text: '', scope_id: data.scope_id};
  return null;
}
function prepClearBox(id) {
  const box = $(id); if (!box || typeof box.innerHTML !== 'string') return;
  let cut = -1;
  for (const m of [PREP_CONFIRM_MARK, PREP_RESULT_MARK]) { const i = box.innerHTML.indexOf(m); if (i >= 0) cut = cut < 0 ? i : Math.min(cut, i); }
  if (cut >= 0) box.innerHTML = box.innerHTML.slice(0, cut);
}
function prepAsk(object_type, object_id, question_id, target) {
  ['decision', 'clarification', 'pending'].forEach(prepClearBox);
  prepPending = {object_type: String(object_type ?? ''), object_id: String(object_id ?? ''), question_id: String(question_id ?? ''), target, epoch: S.epoch, project: S.project, scope: JSON.stringify(S.prepScope)};
  const box = $(target); if (!box || typeof box.innerHTML !== 'string') { prepPending = null; return; }
  const scopeLine = (S.prepScope && S.prepScope.text) ? esc(S.prepScope.text) : PREP_SCOPE_PLACEHOLDER;
  box.innerHTML += `${PREP_CONFIRM_MARK}<p>${scopeLine}</p><p class="muted">${PREP_RUN_NOTICE}</p><div class="row"><button type="button" data-testid="prepare-confirm-yes" onclick="prepConfirm()">Confirm</button><button type="button" data-testid="prepare-confirm-no" onclick="prepCancel()">Cancel</button></div></div>`;
}
function prepCancel() {
  ['decision', 'clarification', 'pending'].forEach(prepClearBox);
  prepPending = null;
}
async function prepConfirm() {
  const p = prepPending; if (!p) return;
  prepPending = null;
  if (!current(p.epoch) || p.project !== S.project || p.scope !== JSON.stringify(S.prepScope)) {
    prepClearBox(p.target);
    return;
  }
  const scope_id = (S.prepScope && (S.prepScope.scope_id ?? null)) ?? null;
  const body = {object_type: p.object_type, object_id: p.object_id, scope_id, client_action_id: crypto.randomUUID()};
  try {
    await call('POST', `/questions/${p.question_id}/prepare-request`, body);
    if (!current(p.epoch) || p.project !== S.project) return;
    prepClearBox(p.target);
    const box = $(p.target);
    if (current(p.epoch) && box && typeof box.innerHTML === 'string') box.innerHTML += `${PREP_RESULT_MARK}Request accepted, not completed. See preparation status below.</p>`;
  } catch (e) {
    if (!current(p.epoch) || p.project !== S.project) return;
    prepClearBox(p.target);
    const box = $(p.target);
    if (current(p.epoch) && box && typeof box.innerHTML === 'string') box.innerHTML += `${PREP_RESULT_MARK}${esc(errorText((e && e.message) || t('Preparation unavailable.')))}</p>`;
  }
  if (S.question) loadPreparations();
}
let preparationRequestSerial = 0;
async function loadPreparations() {
  if (!S.project || !S.question) return;
  const epoch = S.epoch, question = S.question, project = S.project;
  const serial = ++preparationRequestSerial;
  if (typeof clearTimeout === 'function') clearTimeout(WB_GLOBAL.preparationRefresh);
  const valid = () => current(epoch) && question === S.question && project === S.project && serial === preparationRequestSerial;
  try {
    const data = await call('GET', `/questions/${S.question}/preparations`);
    if (!valid()) return;
    const jobs = (data && (data.jobs ?? data.preparation_jobs)) || [];
    const cards = (data && (data.cards ?? data.prepared_cards ?? data.drafts)) || [];
    S.preparations = {jobs: Array.isArray(jobs) ? jobs : [], cards: Array.isArray(cards) ? cards : [],
      sourceOptions: data.scope_options || [], runtime: data.runtime || null};
    S.prepScope = prepScopeSummary(data);
    renderPreparations();
  } catch (e) {
    if (!valid()) return;
    S.preparations = {...(S.preparations || {jobs: [], cards: []}),
      stale: true, error: errorText(e.message), failures: ((S.preparations && S.preparations.failures) || 0) + 1};
    renderPreparations();
  }
}
async function prepApproveScope() {
  const project = S.project, question = S.question, epoch = S.epoch;
  const sources = (S.preparations && S.preparations.sourceOptions) || [];
  if (!sources.length || !confirm('Approve local registered reads only: ' + sources.join(', ') +
      '. This is not cloud transmission consent. Runs only while the app is open; nothing is adopted automatically.')) return;
  try {
    await call('POST', `/projects/${encodeURIComponent(project)}/background-scope`, {allowed_sources: sources});
  } catch (e) { if (current(epoch)) alert(errorText(e.message)); }
  if (current(epoch) && project === S.project && question === S.question) await loadPreparations();
}
async function prepControl(job, action) {
  const question = S.question, epoch = S.epoch;
  try {
    await call('POST', `/questions/${encodeURIComponent(question)}/preparations/${encodeURIComponent(job)}/${action}`, {});
  } catch (e) { if (current(epoch)) alert(errorText(e.message)); }
  if (current(epoch) && question === S.question) await loadPreparations();
}
async function prepCommit(card) {
  const question = S.question, epoch = S.epoch;
  try {
    await call('POST', `/questions/${encodeURIComponent(question)}/prepared-cards/${encodeURIComponent(card)}/commit`, {});
    if (current(epoch)) alert('Draft validated for review only. Nothing is adopted; use the existing decision controls.');
  } catch (e) { if (current(epoch)) alert(errorText(e.message)); }
}
function renderPreparations() {
  const box = $('briefing'); if (!box || typeof box.innerHTML !== 'string') return;
  // Measured before the write, like the briefing render: the background poll
  // re-renders this section every 1200 ms while a preparation is queued or
  // running, and an unconditional scroll would drag a reader away from the saved
  // briefing a few times a minute for as long as the job lasts.
  const pinned = secretaryLogPinned();
  const i = box.innerHTML.indexOf(PREP_SECTION_MARK);
  let html = i >= 0 ? box.innerHTML.slice(0, i) : box.innerHTML;
  const prep = S.preparations;
  const jobs = prep && Array.isArray(prep.jobs) ? prep.jobs : [];
  const cards = prep && Array.isArray(prep.cards) ? prep.cards : [];
  if (prep && prep.stale) html += PREP_SECTION_MARK + `<p role="status">Preparation status is stale; last known records are shown. ${esc(prep.error)}</p>`
    + '<button onclick="loadPreparations()">Refresh preparation status</button>';
  if (prep && prep.runtime) html += PREP_SECTION_MARK + `<p>${esc(prep.runtime.notice)}</p>`;
  if (prep && prep.sourceOptions && prep.sourceOptions.length && !S.prepScope) {
    html += PREP_SECTION_MARK + '<button onclick="prepApproveScope()">Approve registered preparation scope</button>';
  }
  const notes = [];
  for (const j of jobs) {
    if (!j || typeof j !== 'object') continue;
    if (j.status === 'interrupted') notes.push(t(PREP_NOTICE_INTERRUPTED));
    else if (j.status === 'stopped_exhausted') notes.push(t(PREP_NOTICE_EXHAUSTED));
    const status = String(j.status || '');
    const label = status === 'completed' ? 'Ready: draft proposal only' :
      status.startsWith('stopped_') ? `Blocked: ${status}. No eligible consent-backed preparation runtime or usable result; nothing completed.` :
      status === 'queued' ? 'Pending: queued, not completed' : status;
    const id = esc(String(j.id || j.job_id || ''));
    const buttons = (['queued','paused_foreground','interrupted'].includes(status)
      ? `<button onclick="prepControl('${id}','resume')">Resume / run step</button>` : '')
      + (['queued','running','paused_foreground'].includes(status) ? `<button onclick="prepControl('${id}','pause')">Pause</button>` : '');
    html += PREP_SECTION_MARK + `<div class="card"><p>${esc(label)}</p>${buttons}`
      + (!['completed','cancelled'].includes(status) && !status.startsWith('stopped_') ? `<button onclick="prepControl('${id}','cancel')">Cancel preparation</button>` : '') + '</div>';
  }
  if (notes.length || cards.length) {
    html += PREP_SECTION_MARK + '<div class="turn turn-secretary">' + notes.map(n => `<p class="muted">${esc(n)}</p>`).join('')
      + cards.map(c => renderPreparedCard(c) + `<button onclick="prepCommit('${esc(String(c.id || c.card_id || ''))}')">Validate draft for review (not adoption)</button>`).join('') + '</div></section>';
  }
  box.innerHTML = html;
  if (typeof setTimeout === 'function' && (prep && prep.stale ? prep.failures < 3 :
      jobs.some(j => ['queued','running','paused_foreground'].includes(j.status)))) {
    const epoch = S.epoch, question = S.question;
    clearTimeout(WB_GLOBAL.preparationRefresh);
    WB_GLOBAL.preparationRefresh = setTimeout(() => {
      if (current(epoch) && question === S.question) loadPreparations();
    }, 1200);
  }
  scrollSecretaryToBottom(pinned);
}
let launchRows = [];
function launchWaitingCount(r) {
  if (r && typeof r === 'object') {
    for (const v of [r.waiting_count, r.waiting, r.counts && r.counts.waiting]) {
      if (Number.isFinite(v)) return v;
    }
    if (r.parts && Array.isArray(r.parts.waiting_on_you)) return r.parts.waiting_on_you.length;
  }
  return null;
}
function launchTopText(r) {
  const firstOf = v => {
    if (typeof v === 'string') return v;
    if (v && typeof v === 'object' && v.text != null) return String(v.text);
    return '';
  };
  if (r && r.parts && Array.isArray(r.parts.suggested_next_action) && r.parts.suggested_next_action.length)
    return firstOf(r.parts.suggested_next_action[0]);
  if (r && Array.isArray(r.top_items) && r.top_items.length) return firstOf(r.top_items[0]);
  return '';
}
function renderLaunchBriefing(rows) {
  const box = $('launch-briefing'); if (!box) return;
  launchRows = Array.isArray(rows) ? rows : [];
  if (!launchRows.length) { box.innerHTML = `<h3>${esc(t('Return briefing'))}</h3><p class="muted">${esc(t('No saved briefings yet. Open a project to start one.'))}</p>`; return; }
  box.innerHTML = `<h3>${esc(t('Return briefing'))}</h3>` + launchRows.map((r, i) => {
    const name = (r && (r.project || r.project_id)) || t('Project {number}', {number: i + 1});
    const top = serverText(launchTopText(r)) || t('No next action recorded.');    const waiting = launchWaitingCount(r);
    const waitingText = waiting == null ? '' : esc(' · ' + t('{count} waiting', {count: waiting}));
    const parts = r && r.parts;
    const arrival = parts ? [
      ['Where you left off', parts.where_you_left_off],
      ['Waiting on you', parts.waiting_on_you],
      ['Suggested next action', parts.suggested_next_action]
    ].map(([label, items]) => `<section><h4>${esc(t(label))}</h4>${briefItemsHtml(Array.isArray(items) ? items.slice(0, 1) : [])}</section>`).join('') : '';
    // Coaching line from today's fields only (guidance note, top items, waiting
    // counts). renderLaunchRadarLine() guards the future S2 radar feed: absent
    // radar fields render nothing, never placeholder flags or stale warnings.
    // renderLaunchAdaptationLines() guards the D4 adaptation feed the same way:
    // absent lines render nothing, never placeholder maturity or benchmark claims.
    // A div, not a p: the readiness disclosure below is a details element, and a
    // details inside a paragraph makes the parser close the paragraph early.
    return `<div class="launch-row"><strong>${esc(name)}</strong>${arrival}<br><span class="muted">${esc(top)}${waitingText}</span>${renderLaunchCoachingLine(r)}${renderLaunchRadarLine(r)}${renderLaunchAdaptationLines(r)}<br><button type="button" data-testid="open-launch-${i}" onclick="openLaunchProject(${i})">${esc(t('Open'))}</button></div>`;
  }).join('');
}
function renderLaunchCoachingLine(r) {
  const g = r && r.guidance;
  if (!g || typeof g !== 'object' || Array.isArray(g)) return '';
  if (g.text == null || String(g.text).trim() === '') return '';
  return `<br><span class="muted">${esc(serverText(g.text))}</span>`;
}
function renderLaunchRadarLine(r) {
  const radar = r && r.radar;
  if (!radar || typeof radar !== 'object' || Array.isArray(radar)) return '';
  const hasText = v => v != null && String(v).trim() !== '';
  // The launch surface renders the designed stale-flag subset only (no
  // ready_now cards/counts: per the design doc, those belong to the
  // per-project briefing mount point). Claimed-but-unrendered fields fail
  // closed: they render nothing here, never placeholder or invented content.
  const flags = Array.isArray(radar.stale_flags) ? radar.stale_flags.filter(f => f && hasText(f.text)) : [];
  if (!flags.length) return '';
  const shown = flags.slice(0, 3);
  return shown.map(f => {
    const proj = (f.project != null && String(f.project).trim() !== '') ? esc(f.project) : '';
    const txt = hasText(f.text) ? esc(serverText(f.text)) : '';
    const sep = (proj && txt) ? ' — ' : '';
    const waiting = Number.isFinite(f.waiting_count) ? ' · ' + t('{count} waiting', {count: f.waiting_count}) : '';
    return `<br><span class="muted">${proj}${sep}${txt}${waiting}</span>`;
  }).join('');
}
// --- Adaptive pack readiness (Slice D4, frontend part).
// Adaptation lines will come from the D2 read-only composer
// (app/adaptive_readiness.py `row["lines"]`); once the backend wiring slice
// (impl-adaptive-d3-briefing-wiring) lands they will be wired into each
// launch-briefing row as `lines`. Until then the mount renders nothing. Renders
// ONLY when the field exists: approved overlay version + benchmark refs when
// present, cold as cold. Quiet research-tool styling (existing muted class,
// no gradients/glow/emoji); panels' collapse behaviour untouched.
function renderLaunchAdaptationLines(r) {
  const raw = r && r.lines;
  if (!Array.isArray(raw) || !raw.length) return '';
  const items = raw.filter(l => (typeof l === 'string' ? l.trim() : (l && l.text != null ? String(l.text).trim() : '')) !== '');
  if (!items.length) return '';
  // These lines are the engine's own audit of what it counted before it offered
  // this project: project ids, per-type record counts, "cold"/"accumulating",
  // and the standing no-grant note. They have to stay readable and verbatim
  // (that is the honesty contract), but they are not what a researcher reads on
  // opening the panel — printed open they buried the one line that matters,
  // "what do I do next", under two paragraphs of bookkeeping. So they sit
  // behind one disclosure row: same text, one click away, nothing hidden.
  return `<details class="launch-basis"><summary>${esc(t('Readiness detail'))}</summary>${
    items.map(l => `<span class="muted">${esc(serverText(typeof l === 'string' ? l : l.text))}</span>`).join('<br>')}</details>`;
}
async function loadLaunchBriefing() {
  const box = $('launch-briefing'); if (!box) return;
  try {
    const data = await call('GET', '/briefing');
    renderLaunchBriefing(data && Array.isArray(data.rows) ? data.rows : []);
  } catch (e) { box.innerHTML = `<h3>${esc(t('Return briefing'))}</h3><p class="muted">${esc(t('Return briefing unavailable. Open a project below.'))}</p>`; }
}
function openLaunchProject(i) {
  const r = launchRows[i]; if (!r || !r.project_id) return;
  openProject(r.project_id, r.project || r.project_id);
}
async function loadPending() {
  if (!S.project) return;
  const epoch=S.epoch, request=++pendingRequest;
  try {
    const r=await call('GET',`/projects/${S.project}/clarifications?pack_id=${encodeURIComponent(S.pack)}`);
    if(!current(epoch) || request !== pendingRequest) return;
    S.pending=r.clarifications.filter(c=>['pending','deferred','retryable'].includes(c.status));
    // Each waiting clarification is a secretary message carrying only payload
    // fields: the gap, why it matters, its status, and the response the app
    // already holds for it (if any). The buttons are the existing controls.
    const rows = S.pending.map((c,i) => {
      const clar = c.clarification || {};
      const accepted = c.accepted_response;
      return secretaryTurn(`<section class="turn-section"><h3>${esc(t('Pending / deferred questions'))}</h3>`
        + `<p>${esc(clar.gap)} <span class="muted">${esc(c.status)}</span></p>`
        + (clar.why_matters ? `<p class="muted">${esc(clar.why_matters)}</p>` : '')
        + (accepted && accepted.value != null && String(accepted.value).trim() !== ''
          ? `<p class="muted">${esc(t('Your saved response ({kind}): {value}', {kind: String(accepted.kind), value: String(accepted.value)}))}</p>` : '')
        + `<p><button data-testid="resume-${i}" onclick="resumeClarification(${i})">${esc(t('Resume'))}</button> <button type="button" data-testid="prepare-pending-${i}" onclick="prepAsk('clarification','${prepSafeId(c.cq_id)}','${prepSafeId(c.question_id)}','pending')">${esc(t('Prepare this for me'))}</button></p></section>`);
    });
    // Read the reader's position before the DOM changes: this runs on every
    // project load and after each answer, so measuring afterwards would report
    // wherever the render left them and pull them out of older content.
    const pinned = secretaryLogPinned();
    $('pending').innerHTML = S.pending.length ? rows.join('') : `<p class="muted">${esc(t('No clarification is waiting on you.'))}</p>`;
    controls();
    if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary();
    if(typeof loadSavedCanvases === 'function') await loadSavedCanvases();
    adoptSavedWording();
    scrollSecretaryToBottom(pinned);
  } catch(e) { if(current(epoch)) { $('pending').textContent=t('Pending clarifications unavailable.'); showError(e.message); } }
}
function resumeClarification(index) {
  if(S.busy) return;
  const c=S.pending[index]; if(!c) return;
  const savedTurns = S.turns;
  // clearActive() drops the saved-question list, so read the wording first.
  const wording = savedQuestionWording(c.question_id);
  clearActive(); S.question=c.question_id; S.task=c.task_id; S.turns=savedTurns; badge(c.adapter); renderClarification(c); controls(); loadPreparations();
  S.questionText = wording;
  refreshResearcherTurns();
  if(typeof loadCanvas === "function") loadCanvas(c.question_id).catch(e=>showError(e.message));
}
// --- Secretary composer: the panel is where research actually proceeds.
// One input drives existing routes only. With a clarification pending, typed
// text goes out through respond() as kind 'answer' — the only clarification
// response kind whose value genuinely carries free text (use_source carries a
// registered source ID; unknown/later/decline/cancel carry none). With nothing
// pending, typed text starts an ordinary research question through the same
// path the ask form uses. No new endpoint, no new model call, no new payload.
//
// There are two different things a person can be doing in this one box, and the
// app stopped guessing between them. The mode control says which one is next:
// "Answer current question" sends the text as the answer the panel is waiting
// for, "Start new research question" creates a new question for this project.
// The control is built here as a child of #secretary-compose, so home_hub.js can
// keep moving that single form between the home screen and the panel and the
// choice travels with it. No second form, no second input id.
//
// With no explicit choice, waiting clarifications route to reply; otherwise talk.
const COMPOSER_REPLY = 'reply', COMPOSER_NEW = 'new', COMPOSER_TALK = 'talk', COMPOSER_MODE_ID = 'secretary-mode';
// A reply needs a question the app is genuinely waiting on. A pending
// clarification is the only such state: it is the one place where typed text is
// an answer rather than a new question.
function composerQuestionPending() { return !!(S.project && S.cq); }
function composerMode() {
  if (!S.project) return 'no-project';
  if (S.composerMode === COMPOSER_REPLY) return composerQuestionPending() ? 'clarification' : 'reply-unavailable';
  if (S.composerMode === COMPOSER_NEW) return 'question';
  if (S.composerMode === COMPOSER_TALK) return 'talk';
  return composerQuestionPending() ? 'clarification' : 'talk';
}
// The one clarification this composer would answer, or nothing. Selection is by
// cq_id only: a reply is sent to S.cq, so no other waiting item may stand in for it.
function composerPendingClarification() {
  if (!S.cq) return null;
  return (S.pending || []).find(c => c && c.cq_id === S.cq) || null;
}
// The wording of the question a reply would answer, if the app really holds it.
// Nothing is composed: a saved clarification carries question_id, and canvas.js
// keeps the pack's remembered wording for it. The question is resolved from the
// clarification being answered, never from whichever item happens to be first:
// with two waiting questions, naming the wrong one would misdescribe the send.
// `S.questionText` is believed only when it describes that same question.
function composerTargetWording() {
  const rec = composerPendingClarification();
  const qid = (rec && rec.question_id) || (S.cqPrompt ? S.question : null);
  const wording = qid ? savedQuestionWording(qid) : null;
  if (wording) return wording;
  if (qid && S.questionText && S.question === qid) return S.questionText;
  return null;
}
// Built once, inside the existing form. A harness whose DOM double has no
// appendChild simply keeps the older composer, with no error and no half markup.
function ensureComposerModeControls() {
  try {
    const form = $('secretary-compose');
    if (!form || $('secretary-mode')) return;
    if (typeof document.createElement !== 'function' || typeof form.appendChild !== 'function' || typeof Option !== 'function') return;
    const wrap = document.createElement('div');
    wrap.className = 'secretary-mode';
    const label = document.createElement('label');
    label.className = 'muted secretary-mode-label';
    label.id = 'secretary-mode-label';
    label.setAttribute('for', COMPOSER_MODE_ID);
    const select = document.createElement('select');
    select.id = COMPOSER_MODE_ID;
    select.setAttribute('data-testid', 'secretary-mode');
    if (select.dataset) select.dataset.testid = 'secretary-mode';
    select.appendChild(new Option('', COMPOSER_REPLY));
    select.appendChild(new Option('', COMPOSER_NEW));
    select.appendChild(new Option('', COMPOSER_TALK));
    select.setAttribute('aria-describedby', 'secretary-compose-hint secretary-mode-context');
    const context = document.createElement('p');
    context.className = 'muted secretary-mode-context';
    context.id = 'secretary-mode-context';
    context.setAttribute('role', 'status');
    wrap.appendChild(label); wrap.appendChild(select); wrap.appendChild(context);
    // Ahead of the input: the choice is read before the words are typed, and the
    // send button stays after them.
    form.insertBefore(wrap, form.firstChild || null);
    select.addEventListener('change', () => {
      S.composerMode = select.value;
      syncComposerMode();
      controls();
    });
  } catch (_) {}
}
function syncComposerMode() {
  ensureComposerModeControls();
  const mode = composerMode();
  const hint = $('secretary-compose-hint');
  const select = $('secretary-mode');
  const context = $('secretary-mode-context');
  const label = $('secretary-mode-label');
  const send = $('secretary-send');
  const wording = composerTargetWording();
  // What the control shows is the mode that is in force. The person's own choice
  // stays shown even when it cannot send (reply with nothing waiting), so the
  // control never contradicts the explanation underneath it; with no explicit
  // choice, it shows the automatic mode, so what the next send would do is
  // always visible before the words are typed.
  if (select) {
    const shown = S.composerMode === COMPOSER_REPLY ? COMPOSER_REPLY
      : S.composerMode === COMPOSER_NEW ? COMPOSER_NEW
        : (mode === 'clarification' ? COMPOSER_REPLY : COMPOSER_TALK);
    if (select.value !== shown) select.value = shown;
    select.disabled = !!S.busy || !S.project;
    if (select.options && select.options.length >= 2) {
      select.options[0].textContent = t('Answer current question');
      select.options[1].textContent = t('Start new research question');
      if (select.options[2]) select.options[2].textContent = t('Talk with the secretary');
    }
    select.setAttribute('aria-describedby', 'secretary-compose-hint secretary-mode-context');
  }
  if (label) label.textContent = t('Message mode');
  if (hint) {
    hint.textContent = mode === 'clarification'
      ? t('Reply to the clarification. Your text is sent as your answer, stored as your statement, not external verification.')
      : mode === 'question'
        ? t('Ask a research question. It is submitted as a new question for this project.')
        : mode === 'reply-unavailable'
          ? t('No question is waiting on an answer here, so a reply has nowhere to go. Choose "Start new research question", or resume a waiting item below.')
          : mode === 'talk' ? t('Talk with the secretary. Nothing is registered automatically.')
          : t('Open a project to talk to the secretary.');
  }
  if (context) {
    context.hidden = mode === 'talk';
    context.textContent = mode === 'clarification'
      ? t('Replying to: {question}', {question: wording || t('the question this project is waiting on')})
      : mode === 'question'
        ? (S.composerMode === COMPOSER_NEW
          ? t('A new question opens in this project. Recorded questions, decisions and the open draft stay as they are.')
            + (S.cq ? ' ' + t('The clarification that is waiting stays listed and unanswered.') : '')
          : t('Nothing is waiting on an answer, so this text starts a new question.'))
        : mode === 'reply-unavailable'
          ? t('Sending is off in this mode. Nothing was created, and your words stay in the box.')
          : mode === 'talk' ? t('Talk with the secretary. Nothing is registered automatically.')
          : t('Choose or open a project first.');
  }
  // Reply with nothing to answer: the send is off rather than quietly starting a
  // fresh question in place of the answer the person asked to give.
  if (send) send.disabled = !!S.busy || !S.project || mode === 'reply-unavailable';
}
// --- Secretary review log: recording one exchange -----------------------
// The review list (frontend/secretary_review.js) gathers the past exchanges so
// the person keeps what matters and deletes the rest, but nothing wrote a turn.
// A submission to the composer now records BOTH sides through
// POST /projects/{pid}/secretary-turns: the person's text and the reply the app
// actually gave. It is a record only — nothing recorded here is read back into a
// prompt, and it costs no model call. Recording is bookkeeping: a failed turn
// POST never blocks, rolls back or delays the question/answer that was really
// asked for, and never claims the turn was saved.
//
// One capture object per submission carries the two already-recorded flags, so
// the same submission reaching this code twice (a retry, or clarification mode
// and question mode both firing) still yields exactly one researcher turn and at
// most one secretary turn.
function secretaryCaptureBegin(text) {
  const project = S.project;
  const trimmed = String(text == null ? '' : text).trim();
  // No open project, or nothing said: there is no exchange to record.
  if (!project || !trimmed) return null;
  const mode = composerMode();
  return {
    project, text: trimmed, mode, pack: S.pack || null, ok: false,
    // In clarification mode the text answers the open question, so its turns
    // belong to that question, the same way a created question owns its own.
    questionId: mode === 'clarification' ? (S.question || null) : null,
    researcherRecorded: false, secretaryRecorded: false,
  };
}

// Record one side of one submission. The already-recorded flag is claimed
// synchronously, before the request leaves, so a duplicate call for the same
// side returns without a second POST.
async function secretaryCaptureTurn(capture, role, text) {
  if (!capture || !capture.project) return null;
  const body = String(text == null ? '' : text).trim();
  // Empty or whitespace-only text is not an exchange.
  if (!body) return null;
  if (role === 'researcher') { if (capture.researcherRecorded) return null; capture.researcherRecorded = true; }
  else if (role === 'secretary') { if (capture.secretaryRecorded) return null; capture.secretaryRecorded = true; }
  else return null;
  const payload = {role, body: {text: body}};
  if (capture.pack) payload.pack_id = String(capture.pack);
  const qid = capture.questionId;
  if (qid != null && String(qid).trim() !== '') payload.question_id = String(qid);
  try {
    const recorded = await call('POST', `/projects/${encodeURIComponent(capture.project)}/secretary-turns`, payload);
    if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary();
    return recorded;
  } catch (e) {
    // Bookkeeping only. The action this describes has already run (or already
    // failed) on its own, so the failure is logged for the developer and the
    // person is told nothing: no error line, and no claim that it was saved.
    try { console.error('[workbench] secretary turn not recorded', e); } catch (_) {}
    return null;
  }
}

// The app's own words on the surface it actually drew, read back verbatim. No
// sentence is composed here: the log records what the person was really told.
function secretaryCaptureText(id) {
  const node = $(id);
  if (!node) return '';
  if (node.classList && node.classList.contains('hidden')) return '';
  return typeof node.textContent === 'string' ? node.textContent.trim() : '';
}

async function secretaryCaptureFinish(capture) {
  if (!capture) return null;
  // An explicit one-shot guard: the same capture never records its two sides
  // twice, however many times the submission path reaches this point.
  if (capture.finished) return null;
  capture.finished = true;
  // The person's own words, whatever the action then did with them.
  await secretaryCaptureTurn(capture, 'researcher', capture.text);
  if (!capture.ok) {
    // The action failed. What the app told the person is the error line it drew;
    // that sentence is recorded verbatim. If the surface says nothing, there is
    // no reply text to record and only the researcher turn stands.
    return secretaryCaptureTurn(capture, 'secretary', secretaryCaptureText('error'));
  }
  // Another project opened while the request was in flight: the open surface no
  // longer shows this exchange's reply, so only the researcher side is recorded
  // rather than borrowing a sentence from another project.
  if (S.project !== capture.project) return null;
  // Question mode acknowledges the created question on the refinement card;
  // clarification mode answers on the result card. A surface that drew nothing
  // means there is no reply text — then only the researcher turn is recorded,
  // because inventing a secretary sentence would falsify the log.
  const reply = secretaryCaptureText(capture.mode === 'clarification' ? 'result' : 'refinement');
  if (!reply) return null;
  return secretaryCaptureTurn(capture, 'secretary', reply);
}

async function sendSecretaryTurn(e) {
  if (e && e.preventDefault) e.preventDefault();
  if (S.busy) return;
  const input = $('secretary-input');
  if (!input) return;
  const text = String(input.value || '').trim();
  if (!text) return;
  if (!S.project) { showError(t('Open a project to talk to the secretary.')); return; }
  const mode = composerMode();
  if (mode === 'talk') return sendSecretaryChat(text, input);
  // "Answer current question" with nothing waiting: nothing is created and the
  // words stay where they are. Guessing here would either drop the answer or
  // silently start a different question than the one the person chose to answer.
  if (mode === 'reply-unavailable') {
    syncComposerMode();
    showError(t('No question is waiting on an answer, so nothing was sent. Choose "Start new research question", or resume a waiting item below.'));
    if (input.focus) input.focus();
    return;
  }
  const capture = secretaryCaptureBegin(text);
  // The box is emptied only once the send really happened: a failed send leaves
  // the person's own words in place, so nothing typed is lost to an error line.
  const clearIfSent = () => {
    if (!capture || !capture.ok) return;
    if (String(input.value || '') === String(text)) input.value = '';
  };
  const flow = mode === 'clarification' ? respond('answer', null, text, capture)
    : submitQuestion(text, $('session-only') && $('session-only').checked ? 'session_only' : 'remember',
      $('dataset-select') ? $('dataset-select').value : '', capture);
  // Recording runs after the real action settles, so a slow or failing turn POST
  // can never hold up the question or the answer. action() reports a failed
  // action on screen and returns normally, so capture.ok says which floor it was:
  // a failed question or answer is still recorded, as it really ended.
  if (!capture) return Promise.resolve(flow).then(() => { syncComposerMode(); });
  return Promise.resolve(flow).then(() => secretaryCaptureFinish(capture)).then(r => {
    // After the capture, not before it: capture.ok is set by the real route, so a
    // failure keeps the text and a success clears it.
    clearIfSent();
    syncComposerMode();
    return r;
  });
}
let secretaryChatView = null;
function secretaryChatConversation() {
  const scope = `${S.project || ''}/${S.pack}/${S.epoch}`;
  if (!secretaryChatView || secretaryChatView.scope !== scope) {
    if (secretaryChatView) secretaryChatView.log.remove();
    const log = document.createElement('div');
    log.id = 'secretary-chat-history';
    secretaryChatView = {scope, log, recorded: [], live: []};
  }
  const home = $('startup-menu') && !$('startup-menu').hidden;
  const host = $(home ? 'home-conversation' : 'secretary-log');
  if (host && secretaryChatView.log.parentNode !== host) host.appendChild(secretaryChatView.log);
  return secretaryChatView;
}
function secretaryChatBubble(text, role) {
  const node = document.createElement('div');
  node.className = `secretary-chat-bubble secretary-chat-${role}`;
  if (role === 'secretary') node.classList.add('secretary-chat-reply');
  node.textContent = text;
  return node;
}
function renderSecretaryChatHistory(turns) {
  const view = secretaryChatConversation();
  if (turns) {
    const previous = view.recorded.at(-1)?.id;
    view.recorded = turns.filter(turn => turn.status !== 'discarded').slice().reverse();
    view.scrollNewest = view.recorded.at(-1)?.id !== previous;
  }
  const available = view.recorded.slice(), attached = new Map();
  // The chat endpoint records both sides but does not return their IDs. Bind
  // live controls only to new, exact recorded turns, never a previous exchange.
  for (const live of view.live) {
    const index = available.findIndex(turn => !live.before.has(turn.id)
      && turn.role === live.role && secretaryReviewTexts(turn).join('\n') === live.text);
    if (index >= 0) {
      const [turn] = available.splice(index, 1);
      live.id = turn.id;
      attached.set(turn.id, live.node);
    }
  }
  const nodes = view.recorded.map(turn => {
    if (attached.has(turn.id)) return attached.get(turn.id);
    const text = secretaryReviewTexts(turn).join('\n');
    const marker = text.indexOf('Working question:');
    const concise = turn.role === 'secretary' && text.startsWith('Pinned pack:') && marker >= 0 ? text.slice(marker) : text;
    return secretaryChatBubble(concise, turn.role === 'researcher' ? 'researcher' : 'secretary');
  });
  for (const live of view.live) if (!live.id) nodes.push(live.node);
  const host = view.log.parentNode, position = host?.scrollTop;
  if (nodes.length !== view.log.children.length || nodes.some((node, i) => node !== view.log.children[i]))
    view.log.replaceChildren(...nodes);
  if (host && position != null) host.scrollTop = position;
}
function scrollSecretaryChat() {
  const view = secretaryChatConversation();
  const host = view.log.parentNode;
  if (host) host.scrollTop = host.scrollHeight;
}
async function sendSecretaryChat(text, input) {
  if (S.chatPending) return;
  S.chatPending = true;
  const project = S.project;
  const view = secretaryChatConversation();
  const before = new Set(view.recorded.map(turn => turn.id));
  const researcher = {role: 'researcher', text, before, node: secretaryChatBubble(text, 'researcher')};
  const live = {role: 'secretary', text: null, before, node: secretaryChatBubble(t('The secretary is thinking…'), 'secretary')};
  view.live.push(researcher, live);
  const bubble = live.node;
  bubble.setAttribute('role', 'status');
  renderSecretaryChatHistory();
  scrollSecretaryChat();
  input.value = '';
  try {
    const reply = await call('POST', `/projects/${encodeURIComponent(project)}/secretary-chat`,
      {text, language: document.documentElement.lang === 'ko' ? 'ko' : 'en', ...(S.pack ? {pack_id: S.pack} : {})});
    live.text = reply.text;
    bubble.textContent = t(reply.text);
    const foot = document.createElement('p');
    foot.className = 'muted';
    foot.textContent = reply.provider
      ? `${reply.model} · ${t('Calculated cost: {cost} USD; not invoice-verified', {cost: reply.cost_usd == null ? t('Unknown') : Number(reply.cost_usd).toFixed(6)})}`
      : `${reply.model} · ${t('Local compute: zero API cost')}`;
    bubble.appendChild(foot);
    if (reply.receipt) {
      const receipt = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = t('Trust Receipt');
      receipt.appendChild(summary);
      const content = document.createElement('pre');
      content.textContent = JSON.stringify(reply.receipt, null, 2);
      receipt.appendChild(content);
      bubble.appendChild(receipt);
    }
    const register = document.createElement('button');
    register.type = 'button';
    register.textContent = t('Register as research question');
    register.addEventListener('click', () => {
      if (S.project !== project || S.busy) return;
      return submitQuestion(text, $('session-only')?.checked ? 'session_only' : 'remember',
        $('dataset-select')?.value || '');
    });
    bubble.appendChild(register);
    if (secretaryChatView === view) scrollSecretaryChat();
    if (typeof refreshHomeSecretary === 'function') refreshHomeSecretary();
  } catch (error) {
    bubble.textContent = t('The conversation could not be sent. Your message is back in the composer.');
    if (!input.value) input.value = text;
    if (secretaryChatView === view) scrollSecretaryChat();
  } finally {
    S.chatPending = false;
  }
}
async function authorizeSecretaryChat() {
  const settings = await call('GET', '/secretary-chat/settings');
  if (!settings.ready) {
    showError(t('The local model is off. Start the local model in Settings first.'));
    return;
  }
  if (!window.confirm(t('Allow recent conversation turns and the project briefing to be sent to this local Qwen runtime? API cost is $0. Authorization lasts until this app session ends or the runtime changes.'))) return;
  await call('POST', '/secretary-chat/settings', {consent: true, binding: settings.binding});
}
async function revokeSecretaryChat() {
  await call('POST', '/secretary-chat/settings', {consent: false});
  await call('POST', '/secretary-chat/settings', {provider: 'local'});
}
let secretaryModelSettings = null;
async function openSecretaryModels() {
  secretaryModelSettings = await call('GET', '/secretary-chat/settings' + (S.project ? `?project_id=${encodeURIComponent(S.project)}` : ''));
  secretaryModelSettings.project_id = S.project;
  const select = $('secretary-model-choice');
  select.replaceChildren(new Option(t('Local Qwen (free)'), 'local'));
  for (const option of secretaryModelSettings.options) {
    select.appendChild(new Option(`${option.provider}: ${option.model}`, option.provider));
  }
  if (secretaryModelSettings.selected !== 'local' &&
      !secretaryModelSettings.options.some(o => o.provider === secretaryModelSettings.selected)) {
    select.appendChild(new Option(t('Selected model unavailable'), secretaryModelSettings.selected));
  }
  select.value = secretaryModelSettings.selected;
  showSecretaryModelConsent();
  $('secretary-model-dialog').showModal();
}
function showSecretaryModelConsent() {
  const option = secretaryModelSettings.options.find(o => o.provider === $('secretary-model-choice').value);
  $('secretary-model-consent').textContent = option
    ? `${option.provider} / ${option.model}. ` + t('Recent conversation turns and the project briefing leave this device. Each reply has a calculated cost, not invoice-verified. Shared spend limit: {budget} USD. Confirm to authorize future conversation messages without per-message prompts. Switching to local revokes this authorization.', {budget: option.budget_usd})
    : t('Local Qwen is free. Choosing local revokes paid conversation authorization.');
  if (option) $('secretary-model-consent').textContent += ' ' + t('The compact project descriptor is included.');
}
async function saveSecretaryModel() {
  const provider = $('secretary-model-choice').value;
  const option = secretaryModelSettings.options.find(o => o.provider === provider);
  try {
    await call('POST', '/secretary-chat/settings', {provider, project_id: secretaryModelSettings.project_id,
      consent: true, consent_nonce: option?.consent_nonce});
    $('secretary-model-dialog').close();
  } catch (error) { $('secretary-model-consent').textContent = t('The selected conversation model is unavailable. Switch models in Settings; nothing was sent to another provider.'); }
}
async function ask(e) {
  e.preventDefault(); if(!S.project || !$('question').value.trim()) return;
  const text=$('question').value, memory=$('session-only').checked ? 'session_only':'remember', dataset=$('dataset-select').value;
  return submitQuestion(text, memory, dataset);
}
function refinementReasonText(reason) {
  const text = String(reason);
  const variable = text.match(/^registered variable reference changed: (.*)$/);
  if (variable) return t('Registered variable reference changed: {name}', {name: variable[1]});
  const years = text.match(/^kept years changed: original (.*) vs refined (.*)$/);
  if (years) return t('Kept years changed: original {original} vs refined {refined}', {original: years[1], refined: years[2]});
  const exclusions = text.match(/^exclusions changed: original (.*) vs refined (.*)$/);
  if (exclusions) return t('Exclusions changed: original {original} vs refined {refined}', {original: exclusions[1], refined: exclusions[2]});
  return t(text);
}
// The one question-submission path. The ask form and the secretary composer
// both go through here, so there is a single payload shape and a single run.
// `capture` is the secretary exchange to record afterwards; the ask form passes
// none, so nothing is logged for a question asked outside the composer.
async function submitQuestion(text, memory, dataset, capture=null) {
  if (window.WB_CHIP_BUSY && window.WB_CHIP_BUSY()) return;
  const priorTurns = Array.isArray(S.turns) ? S.turns : [];
  return action(async epoch=> {
    clearActive(); $('question').value=text;
    const q=await call('POST',`/projects/${S.project}/questions`,{text,memory,pack_id:S.pack,capability_id:"mock_text",...(dataset ? {dataset_ids:[dataset]}:{})});
    // The question exists now, so the exchange really happened and its turns
    // belong to it — a later question deletion can then take them with it.
    if(capture) { capture.ok=true; capture.questionId=q.question_id; }
    if(!current(epoch)) return;
    S.question=q.question_id; S.questionText=text; S.turns=[...priorTurns,{text, note:'your question'}]; badge(q.adapter);
    $("question-entry").open=false;
    if(typeof loadCanvas === "function") await loadCanvas(q.question_id);
    show('refinement',(q.context ? `<p class="muted">${esc(t('Pinned pack: {pack} {version}', {pack: q.context.pack_id, version: q.context.pack_version}))}</p>\n` : '') + (q.refinement_status==='ok' ? `<span class="muted">${esc(t('Working question:'))}</span> ${esc(q.working_text)}` : `<span class="flag">${esc(t('Refinement needs review'))}</span>. ${esc(t('Kept your original wording.'))} ${esc((q.refinement_reasons || []).map(refinementReasonText).join('; '))}`));
    if(q.adapter==='offline') {show('result','<p>Question saved locally. Canvas, data profile and saved work are available. Optional cloud tasks are in Secretary.</p>');await loadPending();}
    else if ($('dataset-select').options.length <= 1) { show('result','<p>Question saved locally. This topic workspace has no dataset. No analysis or model execution was run. Register data and explicitly approve any optional cloud task before analysis.</p>'); await loadPending(); }
    else await runQuestion(q.question_id,epoch);
  });
}
async function runQuestion(qid,epoch) {
    let r;
    try { r=await call('POST',`/questions/${qid}/run`); }
    catch(e) { if(current(epoch)) show('result','<p>Run interrupted. Your question and remaining allowance are saved.</p><button data-testid="retry-run" onclick="retryRun()">Retry this run</button>'); throw e; }
    if(!current(epoch)) return;
    S.task=r.task_id; badge(r.adapter); if(r.design) renderDesign(r.design);
    if(r.result_kind==='ClarificationNeeded') renderClarification(r); else renderResult(r);
    loadPreparations();
    await Promise.all([loadPending(), loadBriefing()]);
}
async function retryRun() { if(S.question) return action(epoch=>runQuestion(S.question,epoch)); }
function renderDesign(d) {
  const list=items=>items.map(i=>`<li>${esc(i)}</li>`).join('') || '<li>None listed</li>';
  show('design',`<h3>Design candidate: proposal, not a decision</h3><ul>${d.body.variables.map(v=>`<li><code>${esc(v.name)}</code>: ${esc(v.kind)}, ${esc(v.role)} · dataset: ${esc(v.dataset_id || 'not specified')}</li>`).join('')}</ul><h3>Assumptions</h3><ul data-testid="assumptions">${list(d.body.assumptions || [])}</ul><h3>Risks</h3><ul data-testid="risks">${list(d.body.risks || [])}</ul><p class="muted">Verification plan: ${esc(d.body.verification_plan)}</p>${(d.harness_flags || []).map(f=>`<p class="flag">${esc(f)}</p>`).join('')}`);
}
function renderClarification(r) {
  S.cq=r.cq_id; const c=r.clarification;
  // The open clarification prompt is a secretary message built from its payload
  // fields only (gap, why it matters). loadBriefing() renders it in the log.
  S.cqPrompt = {gap: c.gap, why_matters: c.why_matters};
  if(r.status==='retryable' && r.accepted_response) {
    const accepted=r.accepted_response;
    responseRequest={payload:{kind:accepted.kind,value:accepted.value,project_id:S.project},key:accepted.client_action_id || crypto.randomUUID()};
    show('clarification',`<h3>${esc(c.gap)}</h3><p>Investigation interrupted. Your ${esc(accepted.kind)} response is saved; retry uses the remaining allowance.</p><button data-testid="retry-response">Retry saved response</button><button onclick="respond('cancel')">Cancel</button><div><button type="button" data-testid="prepare-clarification" onclick="prepAsk('clarification','${prepSafeId(S.cq)}','${prepSafeId(S.question)}','clarification')">Prepare this for me</button></div>`);
    document.querySelector('[data-testid="retry-response"]').onclick=()=>respond(accepted.kind,accepted);
    controls(); return;
  }
  const privateGap = c.gap_kind === 'private_context';
  const branchList = privateGap ? '' : `<ul data-testid="branches">${(c.branches || []).map(b=>`<li>If ${esc(b.condition)} → ${esc(b.outcome)}</li>`).join('')}</ul>`;
  const privateNote = privateGap && c.researcher_note ? `<p class="muted">${esc(c.researcher_note)}</p>` : '';
  show('clarification',`<h3>${esc(c.gap)}</h3><p>${esc(c.why_matters)}</p>${privateNote}${branchList}
    <label for="answer-value">${esc(t('Your answer (stored as your statement, not external verification)'))}</label><input id="answer-value" data-testid="answer-value"><button data-testid="resp-answer" onclick="respond('answer')">${esc(t('Submit answer'))}</button>
    <label for="source-value">${esc(t('Registered source ID (only permitted sources can be used)'))}</label><input id="source-value" data-testid="source-value"><button data-testid="resp-use-source" onclick="respond('use_source')">${esc(t('Use this source'))}</button>
    <div class="row"><button data-testid="resp-unknown" onclick="respond('unknown')">${esc(t("I don't know. Find the definition"))}</button><button data-testid="resp-later" onclick="respond('later')">${esc(t('Answer later'))}</button><button data-testid="resp-decline" onclick="respond('decline')">${esc(t('Decline'))}</button><button data-testid="resp-cancel" onclick="respond('cancel')">${esc(t('Cancel'))}</button></div><div><button type="button" data-testid="prepare-clarification" onclick="prepAsk('clarification','${prepSafeId(S.cq)}','${prepSafeId(S.question)}','clarification')">${esc(t('Prepare this for me'))}</button></div>`); controls();
}
async function respond(kind, accepted=null, typed=null, capture=null) {
  if(!S.cq || !S.project) return;
  const value=accepted ? accepted.value : kind==='answer' ? (typed != null ? String(typed).trim() : $('answer-value').value.trim()) : kind==='use_source' ? (typed != null ? String(typed).trim() : $('source-value').value.trim()) : null;
  if(['answer','use_source'].includes(kind) && !value) { showError(kind==='answer' ? t('Enter an answer.') : t('Enter a registered source ID.')); return; }
  return action(async epoch=> {
    const payload={kind,value,project_id:S.project};
    responseRequest=requestAction(responseRequest,payload);
    const r=await call('POST',`/clarifications/${S.cq}/responses`,{...payload,...(kind==='use_source' ? {source_id:value}:{}),client_action_id:responseRequest.key});
    // The clarification answered the open question, so a composer answer records
    // its turns against that question.
    if(capture) { capture.ok=true; capture.questionId=S.question || capture.questionId; }
    if(!current(epoch)) return; badge(r.adapter);
    responseRequest=null;
    // The researcher's own words, as actually sent, become a right-side turn.
    if(['answer','use_source'].includes(kind)) S.turns=[...(S.turns || []), {text: value, note: `your ${kind === 'answer' ? 'answer' : 'source'} response`}];
    if(r.cq_status!=='pending') { $('clarification').classList.add('hidden'); S.cq=null; S.cqPrompt=null; }
    if(r.result_kind) renderResult(r); else { show('result',`<p>State: ${esc(r.state)}</p><p>${esc(r.note || '')}</p>`); if(r.state==='decision_pending') $('decision').classList.remove('hidden'); }
    await Promise.all([loadBriefing(),loadPending()]);
  });
}
function renderResult(r) {
  if(r.result_kind==='EvidenceAssessment') {
    const a=r.assessment;
    show('result',`<h3>${t('Evidence card')}</h3><p data-testid="claim">${esc(a.claim)}</p><p class="muted">${t('Support status: {status} · confirmation: {level}', {status: '<b>'+esc(a.outcome)+'</b>', level: esc(a.confirmation_level)})}</p><ul data-testid="evidence">${r.evidence.map(e=>`<li><strong>Source:</strong> ${esc(e.source_id)}<br><strong>Version:</strong> <span data-testid="evidence-version">${esc(e.source_version || e.version || t('Not provided — unverified'))}</span><br><q>${esc(e.excerpt)}</q><br><span class="muted">Location: ${esc(e.location)}</span></li>`).join('')}</ul><p><strong>${t('Still unresolved:')}</strong> ${esc(a.unresolved.join('; ') || t('nothing listed'))}</p><p><strong>${t('Next:')}</strong> ${esc(a.suggested_next_action)}</p>`);
  } else if(r.result_kind==='needs_user_choice') {
    show('result',`<h3>${t('Your choice')}</h3><ul>${r.alternatives.map(a=>`<li>${esc(a)}</li>`).join('')}</ul><p class="muted">Reference only (${esc(r.reference_window)}): ${esc((r.recent_references || []).map(l=>l.id+' — '+l.note).join('; ') || t('no registered literature'))}</p>`);
  } else show('result',`<h3>${t('Unresolved')}</h3><p>${t('Outcome: {outcome}', {outcome: esc(r.outcome)})}</p><p class="muted">${esc(r.reason || '')} ${t('Searched: {terms}', {terms: esc((r.searched || []).join(', '))})}</p>`);
  if(r.outcome!=='mock_unsupported') $('decision').classList.remove('hidden'); controls();
}
async function decide(verdict) { if(!S.question) return; return action(async epoch=> {
  revisitDecision = null;
  if ($('revisit-create')) $('revisit-create').innerHTML = '';
  const decisionScope = {project:S.project, pack:S.pack, question:S.question, epoch};
  const payload={question_id:S.question,verdict,reason:$('reason').value || null};
  decisionRequest=requestAction(decisionRequest,payload);
  const d=await call('POST',`/projects/${S.project}/decisions`,{...payload,client_action_id:decisionRequest.key});
  if(!revisitCurrent(decisionScope)) return; decisionRequest=null; $('decision-status').textContent=`${d.verdict} recorded. Reason: ${d.reason_display}`;
  if (d.verdict === 'defer' && d.decision_id) {
    revisitDecision = {...decisionScope, id:d.decision_id};
    const box = $('revisit-create');
    if (box) { box.innerHTML = revisitForm('new'); box.classList.remove('hidden'); }
  }
  if (d.verdict==='defer' && d.decision_id) $('decision-status').innerHTML=`${esc(d.verdict)} recorded. Reason: ${esc(d.reason_display)} <button type="button" data-testid="prepare-decision" onclick="prepAsk('decision','${prepSafeId(d.decision_id)}','${prepSafeId(S.question)}','decision')">Prepare this for me</button>`;
  await Promise.all([loadBriefing(),loadPending()]);
}); }
let revisitDecision = null, revisitRows = [], revisitSerial = 0, revisitSubmission = null;
function revisitRowKey(row) { return 'row-' + encodeURIComponent(row.id).replace(/'/g, '%27'); }
function revisitCurrent(scope) {
  return S.project === scope.project && S.pack === scope.pack && S.question === scope.question && S.epoch === scope.epoch;
}
function revisitForm(key) {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `<p>${esc(t('Independently enter a revisit time, or leave blank for no reminder.'))}</p>
    <label for="revisit-date-${key}">${esc(t('Revisit time'))}</label>
    <input id="revisit-date-${key}" type="datetime-local" value="">
    <p class="muted">${esc(t('Entered in local timezone {zone}; saved as UTC.', {zone}))}</p>
    <label for="revisit-note-${key}">${esc(t('Note (optional)'))}</label><input id="revisit-note-${key}" value="">
    <button type="button" onclick="saveRevisit('${key}')">${esc(t('Record revisit time'))}</button>
    <button type="button" onclick="cancelRevisit('${key}')">${esc(t('Cancel'))}</button>
    <p id="revisit-error-${key}" role="status"></p>`;
}
function cancelRevisit(key) {
  if (revisitSubmission && revisitSubmission.key === key && revisitCurrent(revisitSubmission.scope)) {
    $('revisit-error-'+key).textContent = t('Saving is in progress. Cancel cannot stop this server write. Please wait.');
    return;
  }
  $('revisit-date-'+key).value = ''; $('revisit-note-'+key).value = '';
  $('revisit-error-'+key).textContent = '';
}
function renderRevisits() {
  const box = $('revisit-management'); if (!box) return;
  box.innerHTML = `<h3>${esc(t('Revisit reminders'))}</h3>` + revisitRows.map(r => {
    const key = revisitRowKey(r), active = r.display_status === 'due' || r.display_status === 'acknowledged';
    return `<div class="card"><p><strong>${esc(t(r.display_status === 'due' ? 'Ready to revisit' : r.display_status === 'done' ? 'Reminder done' : 'Revisit time recorded'))}</strong></p>
      <p>${esc(r.recorded_date)} (UTC)</p><p>${esc(r.note || '')}</p>
      <p>${esc(r.reason_display || r.decision_reason || '')}</p>
      <p class="muted">${esc(t('Reminder only; the research decision is unchanged.'))}</p>
      ${active ? `<button type="button" onclick="finishRevisit('${key}')">${esc(t('Done'))}</button>` : ''}
      ${active || r.display_status === 'recorded' ? `<details><summary>${esc(t('Reschedule'))}</summary>${revisitForm(key)}</details>` : ''}</div>`;
  }).join('');
  if (revisitSubmission && revisitCurrent(revisitSubmission.scope)) {
    const key = revisitSubmission.key;
    for (const prefix of ['revisit-date-', 'revisit-note-']) {
      const input = $(prefix+key); if (input) input.disabled = true;
    }
  }
}
async function loadRevisits() {
  const scope = {project:S.project,pack:S.pack,question:S.question,epoch:S.epoch}, serial = ++revisitSerial;
  try {
    const data = await call('GET',`/projects/${encodeURIComponent(scope.project)}/revisit-conditions?pack_id=${encodeURIComponent(scope.pack)}`);
    if (!revisitCurrent(scope) || serial !== revisitSerial) return;
    revisitRows = data.conditions || []; renderRevisits();
  } catch(e) {
    if (revisitCurrent(scope) && serial === revisitSerial && $('revisit-management')) $('revisit-management').textContent = t('Revisit reminders could not be loaded.');
  }
}
async function saveRevisit(key) { return writeRevisit(key, false); }
async function finishRevisit(key) { return writeRevisit(key, true); }
async function writeRevisit(key, done) {
  const scope = {project:S.project,pack:S.pack,question:S.question,epoch:S.epoch};
  if (S.busy || revisitSubmission) return;
  const decision = revisitDecision, row = key === 'new' ? null : revisitRows.find(r => revisitRowKey(r) === key);
  if (key !== 'new' && (!row || (done && !['due','acknowledged'].includes(row.display_status)))) return;
  if (key === 'new' && (!decision || !revisitCurrent(decision))) return;
  const error = $('revisit-error-'+key);
  const raw = done ? null : $('revisit-date-'+key).value;
  const date = raw ? new Date(raw) : null;
  if (!done && (!date || !Number.isFinite(date.getTime()))) {
    if (error) error.textContent = t('Enter your own revisit date and time. Nothing was saved.'); return;
  }
  const actor = {recorded_by:'researcher',source:'researcher_revisit_form'};
  const dated = done ? {} : {recorded_date:date.toISOString(),note:$('revisit-note-'+key).value || null};
  const body = key === 'new' ? {...actor,...dated,subject_kind:'decision',subject_id:decision.id,status:'recorded',condition_type:'explicit_date'} : {...actor,...dated,kind:done?'done':'defer'};
  const submission = {key,scope};
  revisitSubmission = submission;
  S.busy = true; controls();
  const inputs = ['revisit-date-', 'revisit-note-'].map(prefix => $(prefix+key)).filter(Boolean);
  inputs.forEach(input => { input.disabled = true; });
  const sameForm = () => revisitCurrent(scope) && $('revisit-error-'+key) === error && (key !== 'new' || revisitDecision === decision);
  try {
    await call('POST',`/projects/${encodeURIComponent(scope.project)}/revisit-conditions${row ? '/'+encodeURIComponent(row.id)+'/actions' : ''}?pack_id=${encodeURIComponent(scope.pack)}`,body);
    if (!revisitCurrent(scope) || (key === 'new' && revisitDecision !== decision)) return;
    if (sameForm()) {
      if (!done) { $('revisit-date-'+key).value = ''; $('revisit-note-'+key).value = ''; }
      if (error) error.textContent = t('Reminder saved. The research decision is unchanged.');
    }
    await loadBriefing();
  } catch(e) { if (sameForm() && error) error.textContent = errorText(e.message); }
  finally {
    if (revisitSubmission === submission) revisitSubmission = null;
    inputs.forEach(input => { input.disabled = false; });
    if (revisitCurrent(scope)) {
      for (const prefix of ['revisit-date-', 'revisit-note-']) {
        const input = $(prefix+key); if (input) input.disabled = false;
      }
    }
    S.busy = false; controls();
  }
}
async function shadowRun() { if(!S.question) return; return action(async epoch=> {
  const s=await call('POST',`/questions/${S.question}/shadow`); if(!current(epoch)) return;
  show('shadow',`<h3>${t('Basic Muse (no harness)')} <span class="badge">${esc(s.adapter==='mock' ? 'MOCK':s.adapter)}</span></h3><p>${esc(s.basic_answer)}</p>${s.flags.map(f=>`<p class="flag">${esc(f)}</p>`).join('')}<p class="muted">${esc(s.note)}</p>`);
}); }
// Deletion is destructive, so the frontend always shows the deletion preview
// first and deletes only on an explicit confirm. The confirm presentation reuses
// the Slice C "Prepare this for me" inline Confirm/Cancel block style: plain
// text plus the muted class, no new panels.
let deletePending = null;
const DELETE_CONFIRM_MARK = '<div data-testid="delete-confirm">';
// Retained rows, said in the researcher's words before the irreversible step.
// Every entry the backend reports under `retained` gets a sentence of its own,
// so no internal key name ("revisit_conditions_kept: 1 kept") is ever shown.
// Korean is not pluralised: {plural} is simply unused there.
function deleteRetainedLine(name, n) {
  const plural = n === 1 ? '' : 's';
  if (name === 'events_payload_nulled')
    return t('{count} event payload{plural} redacted (rows kept)', {count: n, plural});
  if (name === 'usage_ledger_kept')
    return t('{count} spend-accounting row{plural} kept', {count: n, plural});
  if (name === 'latex_drafts_kept')
    return t('{count} project draft{plural} kept (with versions, citations and compiles)', {count: n, plural});
  if (name === 'latex_drafts_quoting_kept')
    return t('{count} draft{plural} on another question keep text quoting this question', {count: n, plural});
  if (name === 'revisit_conditions_kept')
    return t('{count} revisit reminder{plural} kept', {count: n, plural});
  if (name === 'library_notes_kept')
    return t('{count} library note{plural} keep text quoting this question', {count: n, plural});
  // Unknown retained entry: the count is still stated in the researcher's
  // language and nothing is dropped; the internal key name is not shown.
  return t('{count} kept record{plural} (kept by design)', {count: n, plural});
}
function deletePreviewSummary(preview) {
  const byStore = (preview && preview.by_store && typeof preview.by_store === 'object') ? preview.by_store : {};
  const lines = [];
  let total = 0;
  for (const name of Object.keys(byStore)) {
    const n = byStore[name];
    if (typeof n !== 'number' || !(n > 0)) continue;
    total += n;
    lines.push(`${name}: ${n}`);
  }
  // Retained by design: rows delete_question deliberately keeps (event rows
  // with payload redacted, spend-accounting rows, project drafts with their
  // versions, citations and compiles). Never
  // counted in the removal total. Only entries with count>0 are rendered.
  // Shape: {name: {count, reason}}; unknown keys render generically.
  const retainedRaw = (preview && preview.retained && typeof preview.retained === 'object') ? preview.retained : {};
  const retainedLines = [];
  for (const name of Object.keys(retainedRaw)) {
    const entry = retainedRaw[name];
    const n = entry && typeof entry.count === 'number' ? entry.count : NaN;
    if (!(n > 0)) continue;
    retainedLines.push(deleteRetainedLine(name, n));
  }
  return {total, lines, retainedLines};
}
async function deleteQuestion() {
  if(!S.question || S.busy) return;
  if(deletePending && deletePending.question_id === S.question) return;
  deletePending = null;
  const qid = S.question;
  let preview;
  try {
    preview = await call('GET',`/questions/${qid}/deletion-preview`);
  } catch(e) {
    $('delete-status').textContent=t('Deletion preview failed: {reason}. Nothing was deleted.', {reason: ((e && e.message) || e)});
    return;
  }
  if(S.question !== qid) return;
  const summary = deletePreviewSummary(preview);
  deletePending = {question_id: qid};
  const hasKept = summary.retainedLines.length > 0;
  // English keeps its own plural suffix in the code; Korean needs none, so the
  // two sentences are built separately rather than forced through one template.
  const impact = summary.total === 0
    ? (hasKept
      ? t('No deletable stored records found for this question.')
      : t('No stored records found for this question.'))
    : (langIsKo()
      ? t('This will remove {count} stored records plus the question itself: {items}.', {count: summary.total, items: summary.lines.join(', ')})
      : `This will remove ${summary.total} stored record${summary.total === 1 ? '' : 's'} plus the question itself: ${summary.lines.join(', ')}.`);
  const kept = summary.retainedLines.length
    ? ' ' + t('Kept by design (not deleted): {items}.', {items: summary.retainedLines.join(', ')})
    : '';
  $('delete-status').innerHTML=`${DELETE_CONFIRM_MARK}<p>${esc(impact)}${esc(kept)} ${esc(t('Deletion cannot be undone.'))}</p><div class="row"><button type="button" data-testid="delete-confirm-yes" onclick="deleteConfirm()">${esc(t('Confirm'))}</button><button type="button" data-testid="delete-confirm-no" onclick="deleteCancel()">${esc(t('Cancel'))}</button></div></div>`;
}
function deleteCancel() {
  deletePending = null;
  const box = $('delete-status');
  if(box && typeof box.innerHTML === 'string') {
    const i = box.innerHTML.indexOf(DELETE_CONFIRM_MARK);
    if(i >= 0) box.innerHTML = box.innerHTML.slice(0, i);
  }
}
// What survived, said as a place a researcher recognizes. The backend reports
// rows (table, column, string); a researcher never sees that shape as JSON.
const DELETE_HIT_NOUNS = {
  library_items: 'library note', latex_drafts: 'LaTeX draft', latex_draft_versions: 'LaTeX draft version',
  latex_citations: 'LaTeX citation', latex_compiles: 'LaTeX compile record',
  revisit_conditions: 'revisit reminder', usage_ledger: 'spend-accounting row',
  events: 'event record', questions: 'question record'
};
function deleteHitPlaces(hits) {
  const counts = new Map();
  for (const h of Array.isArray(hits) ? hits : []) {
    const table = (h && typeof h.table === 'string') ? h.table : '';
    if (!table) continue;
    const rows = (h && typeof h.rows === 'number' && h.rows > 0) ? h.rows : 1;
    counts.set(table, (counts.get(table) || 0) + rows);
  }
  const parts = [];
  for (const [table, n] of counts) {
    const noun = DELETE_HIT_NOUNS[table] ? t(DELETE_HIT_NOUNS[table]) : table;
    parts.push(t('{count} {what}{plural}', {count: n, what: noun, plural: n === 1 ? '' : 's'}));
  }
  return parts.join(`, `);
}
// Post-delete honesty line. Nothing about the payload is dumped: the sentence
// names what still holds the text, and the erasure limit is stated once.
function deleteOutcomeText(r) {
  if (r && r.verified)
    return t("Deleted from the app's stored records. Forensic erasure of database files is not performed.");
  const places = deleteHitPlaces(r && r.remaining_hits);
  const base = places
    ? t('Deletion incomplete: the text is still stored in {places}. The question and its own records were removed.',
        {places})
    : t('Deletion incomplete: some stored text could not be verified as removed. The question itself was removed.');
  return `${base} ${t('Forensic erasure of database files is not performed.')}`;
}
async function deleteConfirm() {
  const p = deletePending; if(!p || !S.question || p.question_id !== S.question) { deletePending = null; return; }
  deletePending = null;
  const qid = p.question_id;
  return action(async epoch=> {
    const r=await call('DELETE',`/questions/${qid}`); if(!current(epoch)) return;
    clearActive(); $('delete-status').textContent = deleteOutcomeText(r);
    await Promise.all([loadBriefing(),loadPending()]);
  });
}
async function endSession() { return action(async epoch=> {
  const r=await call('POST','/session/end'); if(!current(epoch)) return;
  clearActive(); $('session-status').textContent=t('Session ended. Session-only cleanup: {detail}', {detail: JSON.stringify(r)});
  await Promise.all([loadBriefing(),loadPending()]);
}); }
// The start screen owns the reveal of #portal. If that script is missing or
// broken, the documented fail-safe must still open the workspace: otherwise a
// single failed script leaves the whole app hidden behind a start screen that
// can never resolve. This only fills in the reveal; it never fabricates a field.
function revealWithoutStartScreen() {
  const portal = $('portal');
  if (portal) {
    // The start screen normally sizes this layer from its display anchors; with
    // no start screen it takes the whole viewport, exactly as the settled state.
    Object.assign(portal.style, { left: '0px', top: '0px', width: '100vw', height: '100vh' });
    portal.hidden = false; portal.classList.add('expanded');
  }
  const startup = $('startup');
  if (startup) { startup.hidden = true; if ('inert' in startup) startup.inert = true; }
  if (document.body && document.body.classList) document.body.classList.add('workbench-open');
}
// #profile intentionally has no data-i18n: loadProfile() replaces this element
// wholesale, so a static key would wipe the table on every language switch. The
// authored placeholder is therefore translated here, both at first paint and on
// a later switch, and never while a project's profile is on screen.
function setProfilePlaceholder() {
  const prof = $('profile');
  if (prof && !S.project) prof.textContent = t('Open a project to inspect its variables.');
}
(async()=> {
  // The cartridge start screen owns the first-run field choice and resolves this
  // gate on research-field-ready. It fails safe: a missing or failed start screen
  // leaves the workspace loading exactly as before. startup.js writes the chosen
  // pack to PACK_KEY before resolving, so the saved-pack restore below applies it.
  try { if (window.WB_STARTUP) await window.WB_STARTUP.ready; } catch (_) {}
  // Fail safe, then reveal: with no start screen the workspace must open instead
  // of staying hidden behind a gate that can never resolve.
  if (!window.WB_STARTUP) revealWithoutStartScreen();
  controls();
  setProfilePlaceholder();
  await loadDomainPacks();
  try {
    // Only an option the shipped #pack-select actually offers may be restored.
    let saved = null; try { saved = localStorage.getItem(PACK_KEY); } catch (_) {}
    const sel = $('pack-select');
    const offered = sel && sel.options ? [...sel.options].map(o => o.value) : [];
    if (saved && offered.includes(saved)) { sel.value = saved; S.pack = saved; }
  } catch (_) {}
  renderAdapterBadge();
  try { await loadProjects(); loadLaunchBriefing(); let p; try { p=JSON.parse(localStorage.getItem('wb.project') || 'null'); } catch(_) {} if(p) await openProject(p.id,p.name); }
  catch(e) { showError(e.message); }
})();

// --- Korean strings for this file ------------------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// Sentences that carry a value use {placeholder} so each language keeps its own
// word order. Technical names (CSV, JSON, MIB, MOCK, id field names) stay as-is:
// they name a thing the researcher also sees on disk.
if (WB_GLOBAL.WB_I18N) WB_GLOBAL.WB_I18N.register('ko', {
  'MOCK · fixture output, not real Muse': 'MOCK · 예제 출력이며 실제 Muse가 아닙니다',
  'adapter: {name}': '어댑터: {name}',
  'REHEARSAL · adapter not yet reported': '리허설 · 어댑터 미보고',
  'Use question wording or all registered datasets': '질문 문구 또는 등록된 모든 데이터 사용',
  'Loading profile…': '구성을 불러오는 중…',
  'Loading briefing…': '브리핑을 불러오는 중…',
  'Loading clarifications…': '확인 사항을 불러오는 중…',
  'Profile unavailable.': '구성을 불러올 수 없습니다.',
  'Pending clarifications unavailable.': '대기 중인 확인 사항을 불러올 수 없습니다.',
  'Enter a project name of 1–120 characters.': '프로젝트 이름을 1–120자로 입력하세요.',
  'Select both a CSV file and a JSON dictionary file.': 'CSV 파일과 JSON 사전 파일을 모두 선택하세요.',
  'CSV exceeds the 2 MiB limit.': 'CSV가 2 MiB 한도를 넘습니다.',
  'CSV is empty.': 'CSV가 비어 있습니다.',
  'CSV exceeds 50,000 data rows.': 'CSV 데이터가 50,000행을 넘습니다.',
  'Dictionary must contain valid JSON.': '사전은 올바른 JSON이어야 합니다.',
  'Dictionary requires 1–2,000 entries.': '사전에는 1–2,000개의 항목이 필요합니다.',
  'Each dictionary entry needs nonempty source_id, evidence_id, field, definition, and location text.':
    '각 사전 항목에는 비어 있지 않은 source_id, evidence_id, field, definition, location 텍스트가 필요합니다.',
  'CSV has an unclosed quoted field.': 'CSV에 닫히지 않은 따옴표 필드가 있습니다.',
  'Registered a local read-only copy. Original files unchanged.': '로컬에 읽기 전용 사본을 등록했습니다. 원본 파일은 그대로입니다.',
  'Evidence card': '근거 카드',
  'Support status: {status} · confirmation: {level}': '지지 상태: {status} · 확인 수준: {level}',
  'Not provided — unverified': '제공되지 않음 — 미검증',
  'Still unresolved:': '아직 해결되지 않음:',
  'nothing listed': '기록 없음',
  'Next:': '다음:',
  'Your choice': '연구자의 선택',
  'no registered literature': '등록된 문헌 없음',
  'Unresolved': '미해결',
  'Outcome: {outcome}': '결과: {outcome}',
  'Searched: {terms}': '검색어: {terms}',
  'Basic Muse (no harness)': '기본 Muse (하네스 없음)',
  'Deletion preview failed: {reason}. Nothing was deleted.': '삭제 미리보기에 실패했습니다: {reason}. 삭제된 것은 없습니다.',
  'Session ended. Session-only cleanup: {detail}': '세션을 종료했습니다. 세션 한정 정리: {detail}',
  'Enter an answer.': '답변을 입력하세요.',
  'Enter a registered source ID.': '등록된 출처 ID를 입력하세요.',
  'Open a project to talk to the secretary.': '비서와 대화하려면 프로젝트를 여세요.',
  'Error: {message}': '오류: {message}',
  'The local program is not responding. Start it again, then reload this page.':
    '로컬 프로그램이 응답하지 않습니다. 다시 실행한 뒤 이 페이지를 새로 고침하세요.',
  'That action could not be completed, and nothing was changed.':
    '이 작업을 마치지 못했으며, 바뀐 내용은 없습니다.',
  'Registered source IDs and versions': '등록된 출처 ID와 버전',
  'version not provided': '버전 미제공',

  // --- Secretary briefing sections ---------------------------------------
  'Where you left off': '지난번까지 한 일',
  'What changed since': '그 뒤로 달라진 점',
  'Ready now': '지금 할 수 있는 일',
  'Waiting on you': '결정을 기다리는 항목',
  'Suggested next action': '다음에 할 일',
  'Nothing recorded.': '기록된 내용이 없습니다.',
  'Built from saved records; model calls: {count}': '저장된 기록으로 만들었습니다. 모델 호출 {count}회',
  'Secretary\'s note': '비서 메모',
  'Project radar': '프로젝트 점검',
  'note': '참고',
  'worth a look': '확인 권장',
  'needs a decision': '결정 필요',
  'your question': '연구자 질문',
  'saved {stamp}': '저장 {stamp}',
  'verified {stamp}': '확인 {stamp}',

  // --- Return briefing (launch surface) ----------------------------------
  'Return briefing': '돌아오기 브리핑',
  'No saved briefings yet. Open a project to start one.': '아직 저장된 브리핑이 없습니다. 프로젝트를 열어 시작하세요.',
  'Return briefing unavailable. Open a project below.': '돌아오기 브리핑을 불러올 수 없습니다. 아래에서 프로젝트를 여세요.',
  'Saved briefing': '저장된 브리핑',

  // --- Secretary composer modes ------------------------------------------
  'Message mode': '메시지 종류',
  'Answer current question': '지금 질문에 답하기',
  'Start new research question': '새 연구 질문 시작',
  'Replying to: {question}': '답변 대상: {question}',
  'the question this project is waiting on': '이 프로젝트가 기다리는 질문',
  'A new question opens in this project. Recorded questions, decisions and the open draft stay as they are.':
    '이 프로젝트에 새 질문을 엽니다. 기록된 질문, 결정, 작성 중인 원고는 그대로 남습니다.',
  'The clarification that is waiting stays listed and unanswered.':
    '기다리고 있는 확인 질문은 목록에 그대로 남고 답변되지 않습니다.',
  'Nothing is waiting on an answer, so this text starts a new question.':
    '답변을 기다리는 질문이 없으므로 이 글은 새 질문으로 시작됩니다.',
  'Sending is off in this mode. Nothing was created, and your words stay in the box.':
    '이 모드에서는 보내기가 꺼져 있습니다. 아무것도 만들어지지 않았고, 입력한 글은 그대로 남아 있습니다.',
  'Choose or open a project first.': '먼저 프로젝트를 선택하거나 여세요.',
  'No question is waiting on an answer here, so a reply has nowhere to go. Choose "Start new research question", or resume a waiting item below.':
    '답변을 기다리는 질문이 없어 회신할 대상이 없습니다. "새 연구 질문 시작"을 선택하시거나, 아래에서 기다리는 항목을 이어가세요.',
  'No question is waiting on an answer, so nothing was sent. Choose "Start new research question", or resume a waiting item below.':
    '답변을 기다리는 질문이 없어 아무것도 보내지 않았습니다. "새 연구 질문 시작"을 선택하시거나, 아래에서 기다리는 항목을 이어가세요.',
  'No next action recorded.': '기록된 다음 행동이 없습니다.',
  'Open': '열기',
  'Project {number}': '프로젝트 {number}',
  'Readiness detail': '준비 상태 세부 내용',
  'No saved briefings yet.': '아직 저장된 브리핑이 없습니다.',
  'No clarification is waiting on you.': '기다리는 확인 사항이 없습니다.',
  'Pending / deferred questions': '대기·보류 중인 질문',
  'Resume': '이어서 진행',
  'Prepare this for me': '이 항목 준비하기',
  'Your saved response ({kind}): {value}': '저장된 응답({kind}): {value}',
  'Clarification': '확인 사항',

  // --- prepared cards ----------------------------------------------------
  'Draft proposal': '초안 제안',
  'Nothing is decided.': '아직 결정된 것은 없습니다.',
  'Prepared: {label}.': '준비된 항목: {label}.',
  'Status: {status}': '상태: {status}',
  'Evidence': '근거',
  'No evidence cited.': '인용된 근거가 없습니다.',
  'Limitations': '한계',
  'None recorded.': '기록된 내용이 없습니다.',
  'Still unresolved': '아직 해결되지 않은 것',
  'this item': '이 항목',
  'Preparation interrupted — nothing was replayed.': '준비가 중단되었습니다. 다시 실행된 것은 없습니다.',
  'Preparation stopped: allowance exhausted.': '준비가 중단되었습니다. 허용량을 다 썼습니다.',
  'Scope and allowance will be frozen when confirmed.': '확인하면 범위와 허용량이 고정됩니다.',
  'Runs only while the app is open. If you quit, the job is marked interrupted; nothing replays silently.':
    '앱이 열려 있는 동안에만 실행됩니다. 앱을 끄면 작업은 중단으로 표시되며, 조용히 다시 실행되지 않습니다.',
  'Preparation unavailable.': '준비 기능을 사용할 수 없습니다.',

  // --- Fixed sentences composed by the backend ---------------------------
  // These are read-only briefing/briefing-part sentences built in
  // app/harness.py and app/adaptive_readiness.py. They stay English in the
  // record and its tests; only the screen shows them translated.
  'No decisions yet.': '아직 기록된 결정이 없습니다.',
  'No decisions in this pack.': '이 자료 묶음에는 기록된 결정이 없습니다.',
  'No changes detected in registered data.': '등록된 데이터에 달라진 점이 없습니다.',
  'Registered data changed; review dependent work.': '등록된 데이터가 바뀌었습니다. 연관된 작업을 확인하세요.',
  'Nothing confirmed ready automatically.': '자동으로 준비된 항목은 없습니다.',
  'Nothing waiting.': '기다리는 항목이 없습니다.',
  'Nothing waiting in this pack.': '이 자료 묶음에는 기다리는 항목이 없습니다.',
  '{count} waiting': '{count}건 대기 중',
  'Open a project to inspect its variables.': '프로젝트를 열면 변수를 확인할 수 있습니다.',
  'No pending actions.': '대기 중인 작업이 없습니다.',
  'Open a new question or review a saved decision.': '새 질문을 열거나 저장된 결정을 확인하세요.',
  'Guidance text only; grants no tools, scope, or budget.': '안내 문구일 뿐이며, 도구·범위·예산을 허용하지 않습니다.',
  'proposal — not decided': '제안 — 결정되지 않음',
  'stale — needs attention': '오래됨 — 확인 필요',

  // --- Clarification, deletion and composer wording ----------------------
  'Reply to the clarification. Your text is sent as your answer, stored as your statement, not external verification.':
    '확인 사항에 답변합니다. 입력하신 내용은 연구자의 진술로 저장되며, 외부에서 검증된 것은 아닙니다.',
  'Ask a research question. It is submitted as a new question for this project.':
    '연구 질문을 입력하세요. 이 프로젝트의 새 질문으로 등록됩니다.',
  'No deletable stored records found for this question.': '이 질문에서 삭제할 수 있는 저장 기록이 없습니다.',
  'No stored records found for this question.': '이 질문에 해당하는 저장 기록이 없습니다.',
  'This will remove {count} stored records plus the question itself: {items}.':
    '저장 기록 {count}건과 질문 자체를 삭제합니다: {items}.',
  'Kept by design (not deleted): {items}.': '설계상 남겨 두는 기록(삭제하지 않음): {items}.',
  'Deletion cannot be undone.': '삭제한 내용은 되돌릴 수 없습니다.',
  'Deletion incomplete: {detail}': '삭제가 완전히 끝나지 않았습니다: {detail}',
  "Deleted from the app's stored records. Forensic erasure of database files is not performed.":
    '앱의 저장 기록에서 삭제했습니다. 데이터베이스 파일까지 완전히 지우는 작업은 하지 않습니다.',
  'Confirm': '확인',
  'The open clarification prompt is a secretary message': '확인 사항 안내',
  'Your answer (stored as your statement, not external verification)':
    '답변 (연구자의 진술로 저장되며, 외부 검증이 아닙니다)',
  'Registered source ID (only permitted sources can be used)':
    '등록된 출처 ID (허용된 출처만 사용할 수 있습니다)',
  'Submit answer': '답변 제출',
  'Use this source': '이 출처 사용',
  "I don't know. Find the definition": '모르겠습니다. 정의를 찾아 주세요',
  'Answer later': '나중에 답변',
  'Decline': '거절',
  'Variable': '변수',
  'File': '파일',
  'Rows': '행 수',
  'Missing': '결측',
  'Distinct': '고유값',
  'Examples': '예시',
  'Pinned pack: {pack} {version}': '고정된 자료 묶음: {pack} {version}',
  'Working question:': '작업 중인 질문:',
  'Refinement needs review': '질문 다듬기 확인 필요',
  'Kept your original wording.': '원래 문구를 그대로 두었습니다.',

  // --- Project list order -------------------------------------------------
  'Drag to reorder, or press Alt with the up and down arrow keys':
    '끌어서 순서를 바꾸거나, Alt 키와 위·아래 방향키를 누르세요',
  'Reorder {name}. Drag, or press Alt with the up and down arrow keys.':
    '{name} 순서 바꾸기. 끌어서 옮기거나, Alt 키와 위·아래 방향키를 누르세요.',
  'Move cancelled. The order is unchanged.': '이동을 취소했습니다. 순서는 그대로입니다.',
  'Already the first project.': '이미 첫 번째 프로젝트입니다.',
  'Already the last project.': '이미 마지막 프로젝트입니다.',
  'Order saved. {name} is now number {position} of {count}.':
    '순서를 저장했습니다. {name}은(는) 이제 {count}개 중 {position}번째입니다.',
  'Order not changed.': '순서를 바꾸지 않았습니다.',
  'Not opened: the previous paper has unsaved changes that could not be saved.':
    '열지 않았습니다. 이전 논문에 저장하지 못한 변경 사항이 있습니다.'
});

// A language switch has to redraw what script built, not just the static markup:
// the project list, the briefing, the launch rows and the saved-project title are
// all script output, so without this they would stay in the previous language.
if (WB_GLOBAL.WB_I18N) WB_GLOBAL.WB_I18N.onChange(function () {
  syncComposerMode();
  try { loadProjects(); } catch (_) {}
  try { loadLaunchBriefing(); } catch (_) {}
  if (typeof S !== 'undefined' && S.project) {
    const title = $('project-title');
    if (title) { try { const p = JSON.parse(localStorage.getItem('wb.project') || 'null'); if (p && p.name) title.textContent = p.name; } catch (_) {} }
    try { loadBriefing(); } catch (_) {}
    try { loadPending(); } catch (_) {}
  } else {
    // Before a project is opened the profile box still holds its authored
    // placeholder; see setProfilePlaceholder() for why it is not a static key.
    setProfilePlaceholder();
  }
});
