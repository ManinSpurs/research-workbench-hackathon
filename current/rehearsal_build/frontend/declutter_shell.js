/* Declutter shell: one quiet header, one view nav, two panels on demand.

   This file moves and wraps. It never rebuilds.

   Everything it shows is a node that already ships in index.html — the same
   project list, the same secretary aside with its single composer, the same
   settings button, the same adapter badge, the same view buttons with their
   onclick handlers. Nothing is cloned, so no id exists twice and no listener is
   lost; state (S.project, PANEL_STATE, latexState, the library tree, the open
   PDF iframe) stays exactly where the owning module keeps it.

   Open/closed is the shipped `wb-collapse-projects` / `wb-collapse-secretary`
   pair on <main>, which app.js/library.js already maintain together with the
   panel buttons' aria-expanded. That is the one authority here too: a panel can
   never be off screen while its button claims it is open, which is what the
   Settings "AI panel" route depends on.

   Loaded LAST, after every module it coordinates. If any of them is missing
   every call is skipped and the shipped layout stands. */
(function () {
  'use strict';
  var doc = document;
  var G = window;
  var shell = doc.getElementById('app-shell');
  if (!shell || shell.getAttribute('data-dc-wired') === '1') return;
  shell.setAttribute('data-dc-wired', '1');

  var tr = (G.WB_I18N && G.WB_I18N.t) ? G.WB_I18N.t : function (key) { return key; };
  var preview = false;
  try { preview = new URLSearchParams(location.search).get('preview') === '1'; } catch (_) { preview = false; }

  if (G.WB_I18N) G.WB_I18N.register('ko', {
    'No project open': '열린 프로젝트 없음',
    'Projects': '프로젝트',
    'Secretary': '비서',
    'More': '더 보기',
    'Build status': '빌드 상태',
    'Test · sample data · no live AI': '테스트 · 예시 데이터 · 실시간 AI 없음',
    'Local rehearsal · no live AI': '로컬 리허설 · 실시간 AI 없음',
    'New question': '새 질문',
    'Close the panel': '패널 닫기',
    'Home': '홈',
    'Data, canvas and previous records': '데이터, 캔버스, 이전 기록',
    'The registered data profile, the research canvas, saved questions and the delete action. They stay here, out of the work area, until you need them.':
      '등록한 데이터 프로필, 연구 캔버스, 저장한 질문과 삭제 동작입니다. 필요할 때까지 작업 영역 밖에 있습니다.',
    'Cloud tasks stay off until you enable one and approve its exact scope in the Secretary AI panel. Readiness is reported there and in Settings; this tag never stands in for it.':
      '클라우드 작업은 비서 AI 패널에서 직접 켜고 정확한 범위를 승인하기 전까지 실행되지 않습니다. 준비 상태는 그곳과 설정에 표시되며, 이 표시가 그것을 대신하지 않습니다.',
    'Test · sample data': '테스트 · 예시 데이터',
    'REHEARSAL · adapter not yet reported': '리허설 · 어댑터 미보고',
    'adapter: {name}': '어댑터: {name}',
    'Proposed design and checks': '제안 설계와 점검',
    'Pinned pack and working question': '고정 팩과 작업 질문',
    'This build has not reported which adapter it would use, so this window makes no claim about live AI either way. Sample records are shown.':
      '이 빌드는 어떤 어댑터를 쓸지 아직 보고하지 않았으므로, 이 창은 실시간 AI 여부를 어느 쪽으로도 주장하지 않습니다. 예시 기록을 표시합니다.',
    'The adapter reports fixture output: no live model is called in this window, and every cloud action stays off until you enable and approve one.':
      '어댑터가 픽스처 출력을 보고합니다. 이 창에서는 실시간 모델을 호출하지 않으며, 모든 클라우드 작업은 직접 켜고 승인하기 전까지 실행되지 않습니다.',
    'The adapter reports {name}. That is a report of configuration, not a verified live connection, and no cloud action runs from this window without its own approved scope.':
      '어댑터가 {name}을(를) 보고합니다. 이는 설정 보고이며 검증된 실시간 연결이 아니고, 이 창에서는 자체 승인 범위 없이 어떤 클라우드 작업도 실행되지 않습니다.'
  });

  function $id(id) { return doc.getElementById(id); }
  function mainEl() { return shell.querySelector(':scope > main'); }
  // Generated nodes are labelled by relabel(), never by a data-i18n key: i18n.js
  // captures the authored text of that attribute, and these lines are chosen at
  // runtime (the build-status tag depends on ?preview=1).
  function el(tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function move(parent, node) { if (parent && node && node.parentNode !== parent) parent.appendChild(node); }

  // --- 1. Header: brand, project selector, Projects, Secretary, Settings, truth.
  var header = shell.querySelector(':scope > header');
  var brand = header && header.querySelector(':scope > strong');
  var settingsButton = $id('interior-settings-open');
  var badge = $id('adapter-badge');

  var projectSelect = doc.createElement('select');
  projectSelect.id = 'quiet-project-select';
  projectSelect.setAttribute('data-testid', 'quiet-project-select');
  projectSelect.setAttribute('aria-label', 'Project');
  projectSelect.setAttribute('data-i18n-attrs', 'aria-label');

  var projectsBtn = doc.createElement('button');
  projectsBtn.type = 'button';
  projectsBtn.id = 'quiet-projects';
  projectsBtn.setAttribute('aria-expanded', 'false');
  projectsBtn.setAttribute('aria-controls', 'project-list');
  projectsBtn.setAttribute('data-testid', 'quiet-projects');
  projectsBtn.textContent = 'Projects';

  var secretaryBtn = doc.createElement('button');
  secretaryBtn.type = 'button';
  secretaryBtn.id = 'quiet-secretary';
  secretaryBtn.setAttribute('aria-expanded', 'false');
  secretaryBtn.setAttribute('aria-controls', 'secretary-log');
  secretaryBtn.setAttribute('data-testid', 'quiet-secretary');
  secretaryBtn.textContent = 'Secretary';

  // The build's own truth, always on screen in one compact tag, with the shipped
  // adapter badge beside it and the full sentence one fold away. Nothing about AI
  // readiness is hidden: the panel that owns it stays in the Secretary drawer.
  var truth = doc.createElement('details');
  truth.className = 'wb-dc-truth';
  var truthHead = doc.createElement('summary');
  truthHead.setAttribute('data-testid', 'quiet-build-status');
  var truthTag = el('span', 'wb-dc-truth-tag', '');
  truthHead.appendChild(truthTag);
  var truthBody = el('div', 'wb-dc-truth-body');
  var truthText = el('p', 'wb-dc-truth-text', '');
  truthBody.appendChild(truthText);
  var truthNote = el('p', 'wb-dc-truth-text', tr('Cloud tasks stay off until you enable one and approve its exact scope in the Secretary AI panel. Readiness is reported there and in Settings; this tag never stands in for it.'));
  truthBody.appendChild(truthNote);
  truth.appendChild(truthHead);
  truth.appendChild(truthBody);

  var more = null;
  var moreHead = null;
  var moreBody = null;
  var moreNote = null;

  if (header) {
    // The badge is app.js's own report; it keeps updating in place.
    if (badge) truthHead.appendChild(badge);
    projectsBtn.addEventListener('click', function () { togglePanelFromHeader('projects'); });
    secretaryBtn.addEventListener('click', function () { togglePanelFromHeader('secretary'); });
    // Order after the brand: selector, panels, settings, then the truth tag.
    if (brand && brand.nextSibling) header.insertBefore(projectSelect, brand.nextSibling);
    else header.insertBefore(projectSelect, header.firstChild);
    projectSelect.after(projectsBtn);
    projectsBtn.after(secretaryBtn);
    secretaryBtn.after(settingsButton || truth);
    if (settingsButton) settingsButton.after(truth);
    // "← Home" is a top-level control, not something behind a fold: with the
    // duplicated Research / Writing / Library tab strip gone it is the one way
    // back, so it sits first after the brand where it is read first. It keeps its
    // id, its testid, its inline handler and its own language sync.
    if ($id('back-to-menu')) header.insertBefore($id('back-to-menu'), projectSelect);
    // The More fold is gone with the controls it used to hold. Only "Expand
    // surface" (Widen workspace) had no other home, so it is offered directly in
    // the header instead of behind a fold that would hold a single item. Its id,
    // inline onclick, aria-pressed and library.js's syncExpandButton() are
    // untouched.
    if ($id('expand-surface')) header.insertBefore($id('expand-surface'), truth);
    // Settings already owns theme, language, research field and end session, so
    // the workspace copies of those four controls leave the visible header rather
    // than duplicating them behind a "same controls" note. The nodes are removed
    // there and then, never left in the bar to reappear: settings owns the state,
    // and theme.js / i18n.js / app.js only read these controls behind a null check.
    ['startup-change', 'end-session'].forEach(function (id) {
      var node = $id(id);
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    Array.prototype.forEach.call(header.querySelectorAll(':scope > .theme-control'), function (control) {
      if (control.parentNode) control.parentNode.removeChild(control);
    });
  }

  // #project-title is where startup.js moves focus when the workspace opens, and
  // it is visually hidden now that the selector names the project. A keyboard user
  // still has to see where focus went, so the surface itself takes the visible
  // focus ring while that heading is the active element. No node is added, no
  // focus is moved, and the heading keeps its role and its name.
  doc.addEventListener('focusin', function (event) {
    var sectionEl = mainEl() && mainEl().querySelector(':scope > section');
    if (!sectionEl || !sectionEl.classList) return;
    sectionEl.classList.toggle('wb-dc-surface-focus', event.target === $id('project-title'));
  }, true);
  doc.addEventListener('focusout', function () {
    var sectionEl = mainEl() && mainEl().querySelector(':scope > section');
    if (sectionEl && sectionEl.classList) sectionEl.classList.remove('wb-dc-surface-focus');
  }, true);

  // --- 1b. Refusals and session lines stay on screen -----------------------
  // The shipped lines sat after </main>, below a full-height surface, so an error
  // or a session change could be written outside the window with nothing to
  // scroll to. The same two nodes (ids, roles, live-region behaviour untouched)
  // move into a slot pinned under the header. They are never focused, never
  // duplicated, and an empty line takes no space.
  var alerts = null;
  (function () {
    var errorLine = $id('error'), statusLine = $id('session-status');
    if (!errorLine && !statusLine) return;
    alerts = el('div', 'wb-dc-alerts');
    alerts.setAttribute('data-testid', 'quiet-alerts');
    if (statusLine) alerts.appendChild(statusLine);
    if (errorLine) alerts.appendChild(errorLine);
    if (header && header.nextSibling) header.parentNode.insertBefore(alerts, header.nextSibling);
    else if (header) header.after(alerts);
  })();

  function renderTruthText() {
    // Three states, read from the shipped badge itself, which is app.js's own
    // report. English and Korean both keep the badge's own wording, so a language
    // switch cannot turn one state into another.
    var text = badge ? String(badge.textContent || '').trim() : '';
    var state = 'unknown';
    // Exactly the three shapes the shipped badge writes, in both languages:
    // the locale-stable MOCK literal, `adapter: <name>` / `어댑터: <name>`, and
    // the unreported sentence. Anything else is treated as unreported, never as
    // proof that AI is off.
    var reported = /^(?:adapter|어댑터):\s*(.+)$/i.exec(text);
    if (/MOCK|모의/.test(text)) state = 'mock';
    else if (reported && !/not yet reported|미보고/i.test(text)) state = 'reported';
    var name = reported ? reported[1].trim() : text;
    // The badge beside this tag is the adapter's own wording, so the tag never
    // repeats it: it says only what it alone can prove — that this window was
    // opened from a preview build and shows sample records. The state itself is
    // read from the badge, and stated in words one fold away.
    truthTag.textContent = preview ? tr('Test · sample data') : '';
    truthTag.hidden = !preview;
    if (state === 'mock') {
      truthText.textContent = tr('The adapter reports fixture output: no live model is called in this window, and every cloud action stays off until you enable and approve one.');
    } else if (state === 'reported') {
      truthText.textContent = tr('The adapter reports {name}. That is a report of configuration, not a verified live connection, and no cloud action runs from this window without its own approved scope.', { name: name });
    } else {
      // The build has not said which adapter it would use. That is not evidence
      // of a mock and not evidence of a live model, so neither claim is made.
      truthText.textContent = tr('This build has not reported which adapter it would use, so this window makes no claim about live AI either way. Sample records are shown.');
    }
    // The tag is short and the badge is clamped by CSS; the whole sentence stays
    // one hover or one fold away rather than being replaced by it.
    if (badge) badge.title = text + ' — ' + truthText.textContent;
  }
  renderTruthText();
  if (badge && typeof MutationObserver === 'function') {
    new MutationObserver(renderTruthText).observe(badge, { childList: true, characterData: true, subtree: true });
  }

  // --- 2. Panels: the shipped class pair is the authority -------------------
  function panelOpen(which) {
    var main = mainEl();
    return !!(main && main.classList && !main.classList.contains('wb-collapse-' + which));
  }
  function setPanel(which, open) {
    if (panelOpen(which) === !!open) return false;
    if (typeof G.togglePanel === 'function') { G.togglePanel(which); return true; }
    return false;
  }
  // The shell never claims a panel is on screen while the shipped temporary
  // "expand" state is hiding it: opening a drawer leaves that state first, by
  // pressing the shipped control itself. Nothing is written to the saved
  // preference, because that control never writes it either.
  function exitTemporaryExpand() {
    var main = mainEl();
    if (!main || !main.classList || !main.classList.contains('wb-expand-surface')) return false;
    if (typeof G.toggleSurfaceExpand !== 'function') return false;
    G.toggleSurfaceExpand();
    return !main.classList.contains('wb-expand-surface');
  }
  function openPanel(which) {
    if (which !== 'projects' && which !== 'secretary') return false;
    exitTemporaryExpand();
    var changed = setPanel(which, true);
    syncPanelChrome();
    if (changed) focusPanel(which);
    return true;
  }
  function closePanel(which, restoreFocus) {
    if (which !== 'projects' && which !== 'secretary') return false;
    var changed = setPanel(which, false);
    syncPanelChrome();
    if (changed && restoreFocus !== false) {
      var button = which === 'projects' ? projectsBtn : secretaryBtn;
      if (button && button.focus) button.focus({ preventScroll: true });
    }
    return true;
  }
  function togglePanelFromHeader(which) {
    if (panelOpen(which)) { closePanel(which); return; }
    // One drawer at a time: the other panel closes before this one opens, so two
    // panels never overlap on a narrow window.
    var other = which === 'projects' ? 'secretary' : 'projects';
    if (panelOpen(other)) closePanel(other, false);
    openPanel(which);
  }
  function panelNode(which) {
    var main = mainEl();
    return main ? main.querySelector(':scope > ' + (which === 'projects' ? 'nav' : 'aside')) : null;
  }
  function panelFocusTarget(which) {
    var panel = panelNode(which);
    if (!panel || !panel.querySelector) return null;
    var items = panel.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])');
    for (var i = 0; i < items.length; i++) {
      if (items[i].getClientRects && items[i].getClientRects().length === 0) continue;
      return items[i];
    }
    return null;
  }
  function focusPanel(which) {
    var target = panelFocusTarget(which);
    if (target && target.focus) { try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); } }
  }

  var scrim = doc.createElement('div');
  scrim.className = 'wb-dc-scrim';
  scrim.hidden = true;
  scrim.setAttribute('aria-hidden', 'true');
  scrim.addEventListener('click', function () {
    if (panelOpen('secretary')) closePanel('secretary');
    if (panelOpen('projects')) closePanel('projects');
  });

  function syncPanelChrome() {
    var openProjects = panelOpen('projects'), openSecretary = panelOpen('secretary');
    projectsBtn.setAttribute('aria-expanded', String(openProjects));
    secretaryBtn.setAttribute('aria-expanded', String(openSecretary));
    scrim.hidden = !(openProjects || openSecretary);
    // The shipped list toggle stays the panel's own close control, so its label
    // keeps saying what pressing it does (library.js writes it).
    var main = mainEl();
    if (main && scrim.parentNode !== main) main.appendChild(scrim);
  }

  // --- 3. The view bar leaves the section so it is always on screen ---------
  var main = mainEl();
  var section = main && main.querySelector(':scope > section');
  var viewBar = section && section.querySelector(':scope > .view-bar');
  if (main && viewBar) main.insertBefore(viewBar, main.firstChild);

  // "Expand surface" keeps the shipped behaviour exactly: library.js toggles a
  // temporary flag, never the saved panel preference. It already moved to the
  // header above, where it is offered directly instead of inside the retired
  // More fold. Its id, inline onclick, aria-pressed and its own language sync
  // (syncExpandButton) are untouched.

  // --- 4. Research: question, evidence and decision first ------------------
  var supportDetails = null;
  var supportSummary = null, supportNoteText = null, newQuestionBtn = null;
  var designSummary = null, refinementSummary = null;
  var intro = $id('research-intro');
  if (intro) {
    var newQuestion = doc.createElement('button');
    newQuestion.type = 'button';
    newQuestion.className = 'wb-dc-new-question';
    newQuestion.id = 'quiet-new-question';
    newQuestion.setAttribute('data-testid', 'quiet-new-question');
    newQuestion.textContent = tr('New question');
    newQuestionBtn = newQuestion;
    newQuestion.setAttribute('aria-controls', 'question-entry');
    newQuestion.addEventListener('click', function () {
      var entry = $id('question-entry');
      if (!entry) return;
      // The one existing form, made visible by its own explicit action. No second
      // copy of the fields exists anywhere.
      if (supportDetails) supportDetails.open = true;
      entry.open = true;
      var field = $id('question');
      if (field && field.focus) { field.focus({ preventScroll: false }); }
      if (entry.scrollIntoView) entry.scrollIntoView({ block: 'nearest' });
    });
    intro.appendChild(newQuestion);
  }

  // One working area: the evidence and the decision keep the middle of the page.
  // Data profile, the new-question form, the canvas and the older record paths
  // follow it, mounted and reachable, not in the way. Only order and grouping
  // change; every node keeps its id, handler and rendered state.
  var support = doc.createElement('div');
  support.className = 'wb-dc-support-body';
  if (section) {
    supportDetails = doc.createElement('details');
    supportDetails.className = 'wb-dc-support';
    var supportHead = el('summary', null, tr('Data, canvas and previous records'));
    var supportNote = el('p', 'wb-dc-more-note',
      tr('The registered data profile, the research canvas, saved questions and the delete action. They stay here, out of the work area, until you need them.'));
    supportSummary = supportHead;
    supportNoteText = supportNote;
    supportDetails.appendChild(supportHead);
    supportDetails.appendChild(supportNote);
    supportDetails.appendChild(support);
    var deleteRow = $id('delete-btn'), deleteStatus = $id('delete-status');
    var deleteRowParent = deleteRow && deleteRow.parentNode;
    if (deleteRowParent) move(support, deleteRowParent);
    // A deletion preview, its confirmation buttons and a deletion's outcome are
    // critical: they are never behind a fold. The line stays outside the support
    // details and is placed right after it, always on screen.
    var profile = $id('profile');
    if (profile && profile.closest) move(support, profile.closest('details'));
    move(support, $id('question-entry'));
    move(support, $id('canvas-panel'));

    // Primary content, in reading order, right under the question.
    var cursor = intro || $id('project-title') || section.firstChild;
    function placeAfter(node) {
      if (!node || !cursor) return;
      cursor.after(node);
      cursor = node;
    }
    ['clarification', 'result', 'decision', 'shadow'].forEach(function (id) { placeAfter($id(id)); });
    // Two read-only records are one line until asked for: the design candidate
    // (assumptions, risks, verification plan) and the acknowledgement that names
    // the pinned pack and the refined working question, which repeated the
    // question already at the top of the surface. Each keeps its own node, id,
    // testids and rendered HTML inside a labelled fold; app.js writes their
    // innerHTML and the fold label states what they hold. Neither carries an
    // interactive control, so nothing usable is put behind the fold.
    function foldCard(id, label, testid) {
      var card = $id(id);
      if (!card) return null;
      var fold = doc.createElement('details');
      fold.className = 'wb-dc-record';
      fold.setAttribute('data-testid', testid);
      var head = doc.createElement('summary');
      head.textContent = label;
      fold.appendChild(head);
      if (id === 'design') designSummary = head; else refinementSummary = head;
      // app.js empties and re-fills the card; the fold simply stops holding it
      // while the card is hidden, so an absent record shows no empty fold.
      card.parentNode && card.parentNode.insertBefore(fold, card);
      fold.appendChild(card);
      return { fold: fold, card: card };
    }
    var designFold = foldCard('design', tr('Proposed design and checks'), 'design-fold');
    var refinementFold = foldCard('refinement', tr('Pinned pack and working question'), 'refinement-fold');
    // Both records start closed and sit above the evidence and the decision, as
    // one line each: the note about the question first, the design second.
    if (intro) { if (refinementFold) intro.after(refinementFold.fold); if (designFold) refinementFold
      ? refinementFold.fold.after(designFold.fold) : intro.after(designFold.fold); }
    // A hidden card takes its fold with it, both ways app.js toggles it.
    [[designFold, 'design'], [refinementFold, 'refinement']].forEach(function (pair) {
      var entry = pair[0], id = pair[1];
      if (!entry || !main || typeof MutationObserver !== 'function') return;
      var sync = function () {
        entry.fold.hidden = entry.card.classList.contains('hidden');
      };
      new MutationObserver(sync).observe(entry.card, { attributes: true, attributeFilter: ['class'] });
      sync();
    });
    // Then the tool row, then the fold. The unsupported Code control is hidden by
    // the stylesheet; Data, Canvas and Sources keep their own onclick handlers.
    placeAfter($id('research-tools'));
    placeAfter(supportDetails);
    // The deletion outcome sits directly after the fold that holds the button, so
    // the message and the action it belongs to are read together.
    if (deleteStatus && deleteStatus.parentNode !== section) supportDetails.after(deleteStatus);
  }

  // --- 5. Project selector: the same data, one control ---------------------
  // The list in the projects panel stays the authority: app.js renders it from
  // GET /projects in the persisted order. This selector reads those rows, so a
  // reorder, a registration or a refresh lands here too, and it writes nothing of
  // its own — choosing a row calls the existing openProject(), exactly like the
  // row's own link.
  var lastSignature = null;
  var rowsCache = [];
  function projectRows() {
    var list = $id('project-list');
    var rows = [];
    if (!list) return rows;
    Array.prototype.forEach.call(list.children || [], function (li) {
      if (!li.dataset || li.dataset.projectId == null) return;
      var link = li.querySelector('a');
      rows.push({ id: String(li.dataset.projectId), name: link ? String(link.textContent || '') : String(li.dataset.projectId) });
    });
    return rows;
  }
  // The open project is the row app.js itself marked with aria-current, so this
  // reads the app's own selection rather than keeping a second copy of it.
  // (app.js keeps S in a lexical binding, not on window; its mark is the state it
  // publishes.)
  function currentProjectId() {
    var list = $id('project-list');
    if (!list || !list.querySelector) return '';
    var marked = list.querySelector('a[aria-current="true"]');
    if (!marked || !marked.closest) return '';
    var row = marked.closest('li');
    return row && row.dataset && row.dataset.projectId != null ? String(row.dataset.projectId) : '';
  }
  function syncProjectSelect() {
    var rows = projectRows(), current = currentProjectId();
    var signature = rows.map(function (row) { return row.id + '\u0000' + row.name; }).join('\u0001') + '\u0002' + current + '\u0002' + tr('No project open');
    // The early exit is only safe when the control already shows the app's own
    // selection: after a refused switch nothing about the rows changed, so the
    // value still has to fall back even though the list is identical.
    if (signature === lastSignature && projectSelect.value === current) return;
    lastSignature = signature;
    rowsCache = rows;
    var options = [new Option(tr('No project open'), '')];
    rows.forEach(function (row) { options.push(new Option(row.name, row.id)); });
    // replaceChildren never moves focus, so a refresh that lands while the
    // researcher types in the editor cannot pull the caret out of it.
    projectSelect.replaceChildren.apply(projectSelect, options);
    projectSelect.value = current;
    projectSelect.disabled = rows.length === 0;
  }
  projectSelect.addEventListener('change', function () {
    var id = projectSelect.value;
    if (!id || id === currentProjectId()) return;
    var row = null;
    for (var i = 0; i < rowsCache.length; i++) if (rowsCache[i].id === id) row = rowsCache[i];
    if (typeof G.openProject !== 'function') return;
    // The same call the row's own link makes, including its unsaved-draft flush.
    var done = G.openProject(id, row ? row.name : id);
    if (!done || typeof done.then !== 'function') { syncProjectSelect(); return; }
    // openProject() resolves even when it refuses: a chip that is busy or a project
    // that is not linked to the active chip returns without opening anything. So
    // the selector is re-read from the app's own mark after every settle, not only
    // on a rejection, and it therefore falls back to the project that is really
    // open instead of claiming a switch that never happened.
    done.then(function () {
      syncProjectSelect();
    }, function (error) {
      var line = $id('error');
      if (line && typeof G.showError === 'function') G.showError(error && error.message);
      syncProjectSelect();
    });
    if (panelOpen('projects')) closePanel('projects', false);
  });
  var listNode = $id('project-list');
  if (listNode && typeof MutationObserver === 'function') {
    // Scoped to the one list app.js repaints. Not a document-wide observer: the
    // list is the only place the project sequence and the open mark are written,
    // so a reorder, a refresh and a row clicked inside the panel all reach the
    // selector from here.
    new MutationObserver(function () { syncProjectSelect(); })
      .observe(listNode, { childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ['aria-current'] });
  }
  syncProjectSelect();

  // --- 6. Secretary review stays reachable from the Secretary panel --------
  // It is not a fourth top-level view. The button keeps its id, its testid, its
  // onclick and its aria-pressed contract; only its parent moves.
  var reviewTab = $id('view-secretary-review');
  var aside = main && main.querySelector(':scope > aside');
  if (reviewTab && aside) {
    var actions = doc.createElement('div');
    actions.className = 'wb-dc-panel-actions';
    aside.insertBefore(actions, aside.firstChild);
    actions.appendChild(reviewTab);
    // The review surface is a full-width view, so the drawer it was opened from
    // steps aside. The hook lives on the function, not only on this button's
    // click: the tab strip that used to carry navigation is gone from the
    // visible UI, so the same surface can be reached by the home-menu tile, this
    // panel action, or a script — and all of them must leave the drawer the same
    // way. Only this view is wrapped: the other three are the workspace's own
    // surfaces and keep the saved drawer state, as they always did.
    var openReview = window.openSecretaryReview;
    if (typeof openReview === 'function') {
      window.openSecretaryReview = function () {
        closePanel('secretary', false);
        return openReview.apply(this, arguments);
      };
    }
  }

  // --- 7. Escape closes a panel, never the whole app ------------------------
  // Capture phase, so this is heard before the start screen's own Escape rule
  // (which returns to the home menu). One open panel is the innermost thing on
  // screen while it is open, so it takes the key — unless something inside it
  // already handled the key (a settings sheet, a dialog, a cancelled drag) or a
  // modal is on screen, in which case that surface keeps it.
  function modalOnScreen() {
    var candidates = doc.querySelectorAll('dialog[open], #settings-layer:not([hidden])');
    for (var i = 0; i < candidates.length; i++) {
      var node = candidates[i];
      if (!node || node.hidden === true) continue;
      if (node.getClientRects && node.getClientRects().length === 0) continue;
      return true;
    }
    return false;
  }
  doc.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (modalOnScreen()) return;
    // A project drag owns Escape while it is in progress: cancelling the move must
    // not also shut the panel the rows live in.
    var list = $id('project-list');
    if (list && ((list.classList && list.classList.contains('project-list-dragging')) ||
      (list.querySelector && list.querySelector('li.dragging')))) return;
    var openSecretary = panelOpen('secretary'), openProjects = panelOpen('projects');
    if (!openSecretary && !openProjects) return;
    event.preventDefault();
    if (event.stopPropagation) event.stopPropagation();
    if (openSecretary) closePanel('secretary');
    if (openProjects) closePanel('projects');
  }, true);

  // A pointer press outside an open panel closes it, the way a drawer behaves.
  // The scrim owns its own click, so it is skipped here: closing on pointerdown
  // would hide it before the click and send that click to whatever is underneath.
  doc.addEventListener('pointerdown', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    if (target.closest('main > nav, main > aside, header, .wb-dc-scrim')) return;
    if (panelOpen('secretary')) closePanel('secretary', false);
    if (panelOpen('projects')) closePanel('projects', false);
  }, true);

  // --- 8. Language, theme and the shipped panel state stay in step ---------
  // Every line the shell generates is read through tr() here, so a language
  // switch repaints it in the new language instead of leaving English behind.
  function relabel() {
    projectSelect.setAttribute('aria-label', tr('Project'));
    projectsBtn.textContent = tr('Projects');
    secretaryBtn.textContent = tr('Secretary');
    if (moreHead) moreHead.textContent = tr('More');
    if (moreNote) moreNote.textContent = tr('Language, theme, session and research field. The same controls as Settings, not a second copy of them.');
    if (newQuestionBtn) newQuestionBtn.textContent = tr('New question');
    if (supportSummary) supportSummary.textContent = tr('Data, canvas and previous records');
    if (supportNoteText) supportNoteText.textContent = tr('The registered data profile, the research canvas, saved questions and the delete action. They stay here, out of the work area, until you need them.');
    if (designSummary) designSummary.textContent = tr('Proposed design and checks');
    if (refinementSummary) refinementSummary.textContent = tr('Pinned pack and working question');
    if (truthNote) truthNote.textContent = tr('Cloud tasks stay off until you enable one and approve its exact scope in the Secretary AI panel. Readiness is reported there and in Settings; this tag never stands in for it.');
    // The tag and the full sentence are read from the badge itself, so this one
    // call is what keeps the build's truth current through a language change.
    renderTruthText();
  }
  relabel();
  if (G.WB_I18N && G.WB_I18N.onChange) G.WB_I18N.onChange(relabel);
  // The shipped modules repaint the panel classes themselves; this keeps the
  // header's own aria and the scrim honest for every route, including the
  // Settings "AI panel" route that clicks the shipped secretary toggle.
  if (main && typeof MutationObserver === 'function') {
    new MutationObserver(syncPanelChrome).observe(main, { attributes: true, attributeFilter: ['class'] });
  }

  // --- 9. Boot: closed by default, through the shipped toggle --------------
  shell.classList.add('wb-dc');
  // An empty view bar would paint its sticky background and hairline as a dead
  // strip across the top of the work area. Now that Expand and the review action
  // have both moved out and the tab strip is hidden, the bar holds nothing a
  // person can press, so it is hidden too. Measured from real layout: a button
  // counts only if the browser actually renders it.
  if (viewBar) {
    var barButtons = viewBar.querySelectorAll('button');
    var anyRendered = false;
    for (var bi = 0; bi < barButtons.length; bi++) {
      if (barButtons[bi].getClientRects().length > 0) { anyRendered = true; break; }
    }
    viewBar.hidden = !anyRendered;
  }
  syncPanelChrome();
  // A fresh profile starts with both panels out of the way. Once the researcher
  // opens one, library.js has already saved that choice in wb.panels and replayed
  // it at boot, so it is left exactly as they left it. Closing runs through the
  // shipped togglePanel, so PANEL_STATE, the saved preference, aria-expanded and
  // what is on screen stay in one agreement.
  var savedPanels = null;
  try { savedPanels = localStorage.getItem('wb.panels'); } catch (_) { savedPanels = null; }
  if (!savedPanels) {
    closePanel('projects', false);
    closePanel('secretary', false);
  }
  syncPanelChrome();
  syncProjectSelect();

  // Keep the global tools and refusals reachable above an open drawer, including
  // when the header wraps at a narrow width. Measure real layout, not a timer.
  function measureChrome() {
    var height = header.getBoundingClientRect().height;
    if (height > 0) shell.style.setProperty('--dc-header', height + 'px');
    shell.style.setProperty('--dc-alerts-height', alerts ? alerts.getBoundingClientRect().height + 'px' : '0px');
  }
  var chromeObserver = new ResizeObserver(measureChrome);
  chromeObserver.observe(header);
  if (alerts) chromeObserver.observe(alerts);
  measureChrome();

  G.WB_DECLUTTER_SHELL = {
    openPanel: function (which) { return openPanel(which); },
    // With a name, exactly that panel closes; with none, both do. Either way the
    // shipped toggle runs, so the saved preference and aria-expanded follow.
    closePanel: function (which) {
      if (which === 'projects' || which === 'secretary') return closePanel(which, false);
      closePanel('secretary', false);
      closePanel('projects', false);
      return true;
    },
    refresh: function () {
      lastSignature = null;
      syncProjectSelect();
      syncPanelChrome();
      renderTruthText();
      return true;
    },
    // The Lead calls this from the existing Data and Canvas routes so a tool the
    // researcher asked for is not left behind a closed fold. It only opens the
    // fold: the route's own logic and the control it pressed are untouched.
    openSupport: function () {
      if (!supportDetails) return false;
      supportDetails.open = true;
      return true;
    },
    isPanelOpen: function (which) { return panelOpen(which); }
  };
})();
