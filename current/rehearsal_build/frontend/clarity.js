/* Research Workbench — clarity frames A–E.

   A: one plain purpose line under each home tile.
   B–E: each menu's first screen gets a one-sentence "what this place is for"
        heading, ONE primary next action, demoted secondary controls and an empty
        state for a new user.

   This file adds words and one button per screen. It never replaces a control:
   the primary action is a new button that calls the handler the menu already
   exposed, and every existing control keeps its node, id, handler and place —
   clarity.css only makes it quieter. The four menus are fixed: nothing here adds,
   renames or merges one.

   Loaded after the modules whose functions it calls (startup.js, app.js,
   library.js, latex.js, secretary_review.js) and before declutter_shell.js, so
   the shell can move these panes but never has to know they exist. */
(function () {
  'use strict';
  var G = window;
  var tr = (G.WB_I18N && G.WB_I18N.t) ? G.WB_I18N.t : function (key) { return key; };
  var doc = document;
  function $id(id) { return doc.getElementById(id); }

  if (G.WB_I18N) G.WB_I18N.register('ko', {
    // --- A: the four tiles ------------------------------------------------
    'Collect and read your sources': '자료를 모으고 읽습니다',
    'Ask questions and check the evidence': '질문하고 근거를 확인합니다',
    'Draft and verify your claims': '초안을 쓰고 주장을 검증합니다',
    'Keep or remove past conversations': '지난 대화를 남기거나 지웁니다',

    // --- B: Literature Review --------------------------------------------
    'This is where your sources live.': '자료를 모아 두고 읽는 곳입니다.',
    'Add the PDFs you are reading, keep notes beside them, and later pick these same sources when you check a claim in Writing.':
      '읽고 있는 PDF를 넣고 옆에 노트를 남기세요. 나중에 집필에서 주장을 검증할 때 같은 자료를 근거로 고르게 됩니다.',
    'Add PDFs…': 'PDF 추가…',
    'PDFs only. The original file is never changed.': 'PDF만 추가할 수 있습니다. 원본 파일은 바뀌지 않습니다.',
    'No documents yet': '아직 자료가 없습니다',
    'Drop PDFs on the list below, or use {action}. A file dropped on a folder is filed into that folder.':
      '아래 목록에 PDF를 끌어다 놓거나 {action}을(를) 누르세요. 폴더에 떨어뜨리면 그 폴더로 들어갑니다.',
    'Anything you add stays inside this project.': '추가한 자료는 이 프로젝트 안에만 저장됩니다.',

    // --- C: Research ------------------------------------------------------
    'Ask a question, look at the evidence, then decide.':
      '질문을 던지고, 근거를 확인하고, 결정을 내리는 곳입니다.',
    'The app only proposes an answer. Nothing is applied to your work until you record a decision.':
      '앱은 답을 제안할 뿐입니다. 직접 결정을 기록하기 전까지는 내 작업에 아무것도 적용되지 않습니다.',
    'New question…': '새 질문…',
    'One question, one answer. You can reopen it later.': '질문 하나에 답 하나. 나중에 다시 열어 볼 수 있습니다.',
    'Your question': '내 질문',
    'One sentence about the data you were given. Example: “Does approval_date mean final approval?”':
      '주어진 데이터에 대해 궁금한 것을 한 문장으로 적습니다. 예: “승인일(approval_date)이 최종 승인을 뜻하나요?”',
    'Evidence card': '근거 카드',
    'What the record actually says, with its source and page. If the record is thin, the card says so instead of guessing.':
      '기록이 실제로 말하는 내용을 출처·페이지와 함께 보여 줍니다. 근거가 부족하면 추측하지 않고 그렇다고 말합니다.',
    'Your decision': '내 결정',
    'Adopt keeps the proposal, Defer comes back to it later, Reject leaves only the record of your reason.':
      '채택하면 제안을 남기고, 보류하면 나중에 다시 보며, 기각하면 결정한 이유만 기록으로 남습니다.',
    'No question yet': '아직 질문이 없습니다',
    'Open a project and start with {action}. As soon as a question is saved, its evidence card and the three decision buttons appear here.':
      '프로젝트를 열고 {action}으로 시작하세요. 질문을 저장하면 근거 카드와 세 가지 결정 버튼이 여기에 나타납니다.',
    'Three steps: your question, then the evidence card, then your decision.':
      '세 단계: 내 질문, 그다음 근거 카드, 그다음 내 결정.',

    // --- D: Writing -------------------------------------------------------
    'Write the draft, then check that its claims match the sources.':
      '초안을 쓰고, 쓴 주장이 근거와 맞는지 확인합니다.',
    'Select a sentence and choose {action}. The check compares it with the sources in Literature Review, including the numbers.':
      '문장을 고르고 {action}을(를) 누르면, 문헌 검토에 있는 자료와 숫자를 대조해 결과를 보여 줍니다.',
    'New paper…': '새 논문…',
    'One paper holds one set of LaTeX files, up to 10.': '논문 하나에 LaTeX 원본 파일 한 묶음, 최대 10개입니다.',
    'No claims checked yet': '아직 점검한 주장이 없습니다',
    'Select a sentence in the draft and the {action} action appears. Every checked sentence is listed here with its result.':
      '초안에서 문장을 고르면 {action} 버튼이 나타납니다. 점검한 문장은 결과와 함께 여기에 쌓입니다.',
    'Verify claim': '주장 검증',

    // --- E: Conversation Review ------------------------------------------
    'Decide what to keep from past conversations with the secretary.':
      '지난 비서 대화를 남길지 지울지 정하는 곳입니다.',
    'Newest first. Keeping an exchange changes only its status; the recorded text is never rewritten.':
      '최신 대화부터 보여 줍니다. 남기면 상태만 바뀌고, 기록된 본문은 다시 쓰이지 않습니다.',
    'Ask the secretary…': '비서에게 묻기…',
    'With nothing to review yet, this is the shortest way to get something to review.':
      '아직 검토할 대화가 없을 때, 검토할 대화를 가장 빨리 만드는 방법입니다.',
    'No conversations recorded yet': '아직 기록된 대화가 없습니다',
    'Ask the secretary anything, from Home or from any screen, and each exchange appears here.':
      '홈이나 어느 화면에서든 비서에게 질문하면, 그 대화가 여기에 하나씩 쌓입니다.',
    'There are only two actions here: keep it, or delete it.': '여기서 하는 일은 두 가지뿐입니다. 남기기, 지우기.'
  });

  // --- frame A: the purpose line under each tile --------------------------
  // The tile's own note already holds a longer phrase; this line is the short,
  // plain one the frame shows. It is a child of the tile's .menu-text, so the
  // tile stays one button and the roving tabindex is untouched.
  var TILE_PURPOSE = {
    library: 'Collect and read your sources',
    research: 'Ask questions and check the evidence',
    writing: 'Draft and verify your claims',
    review: 'Keep or remove past conversations'
  };
  function paintTilePurpose() {
    var host = $id('home-menu');
    if (!host || !host.children) return;
    for (var i = 0; i < host.children.length; i++) {
      var tile = host.children[i];
      var key = tile && tile.getAttribute ? tile.getAttribute('data-menu') : null;
      if (!key || !TILE_PURPOSE[key]) continue;
      var text = tile.querySelector('.menu-text');
      if (!text) continue;
      var line = text.querySelector('.clarity-tile-purpose');
      if (!line) {
        line = doc.createElement('span');
        line.className = 'clarity-tile-purpose';
        line.setAttribute('data-testid', 'home-tile-purpose-' + key);
        text.appendChild(line);
      }
      // The tile's purpose is authored here, not captured by data-i18n: it is
      // chosen at runtime by key, so it is labelled the way the tile's siblings are.
      line.textContent = tr(TILE_PURPOSE[key]);
    }
  }
  // startup.js draws the tiles from its own boot sequence, which finishes long
  // after this file is parsed, so painting once here would find an empty host.
  // The host is watched directly rather than with subtree: the tiles are appended
  // to #home-menu itself, while this function writes inside each tile — a level
  // the observer does not watch — so the two cannot feed each other.
  function watchTileHost() {
    paintTilePurpose();
    var host = $id('home-menu');
    if (!host) return;
    if (host.children.length) return;
    if (typeof G.MutationObserver !== 'function') {
      if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', paintTilePurpose);
      return;
    }
    var hostObserver = new G.MutationObserver(function () {
      paintTilePurpose();
      if (host.children.length) hostObserver.disconnect();
    });
    hostObserver.observe(host, { childList: true });
  }
  watchTileHost();

  // --- B–E: the purpose row ------------------------------------------------
  // One builder for all four screens: the same shape, so a person learns it once.
  // `action` is optional (Conversation Review's primary action moves focus to the
  // composer rather than creating something).
  function pane(name) {
    var el = doc.createElement('div');
    el.className = 'clarity-pane';
    el.setAttribute('data-clarity-pane', name);
    el.setAttribute('data-testid', 'clarity-pane-' + name);
    return el;
  }
  // Every string a pane writes records the English key it was built from on its
  // own node. A language switch then re-applies that same key to that same node,
  // so a heading, a note, a button, an empty state's title, one of its body
  // paragraphs or a language switch cannot be forgotten: the node itself says
  // which sentence it is. A sentence that names a control in bold also records
  // that control's key, and is rebuilt as nodes so the <b> survives the switch.
  function keyed(node, key, argKey) {
    node.setAttribute('data-clarity-key', key);
    if (argKey) node.setAttribute('data-clarity-arg', argKey);
    return node;
  }
  function writeCopy(node) {
    var key = node.getAttribute('data-clarity-key');
    var argKey = node.getAttribute('data-clarity-arg');
    if (argKey) {
      var html = tr(key, { action: strong(tr(argKey)) });
      if (node.innerHTML !== html) node.innerHTML = html;
      return;
    }
    var text = tr(key);
    if (node.textContent !== text) node.textContent = text;
  }
  function applyCopy(root) {
    var nodes = root.querySelectorAll('[data-clarity-key]');
    for (var i = 0; i < nodes.length; i++) writeCopy(nodes[i]);
  }
  // Focus a control once it is really on screen. A drawer slides for 160ms and
  // its contents are not focusable until it has arrived, so focusing straight
  // away would leave the cursor on nothing visible. The first animation frame
  // that reports a painted box wins; there is no fixed timer to fall out of step
  // with the transition, and no polling loop left running if the node never
  // appears.
  function armFocus(target) {
    if (!target) return;
    var tries = 0;
    function attempt() {
      tries++;
      if (target.getClientRects && target.getClientRects().length) {
        if (target.focus) target.focus({ preventScroll: true });
        return;
      }
      if (tries > 60) return;
      if (typeof G.requestAnimationFrame === 'function') G.requestAnimationFrame(attempt);
    }
    attempt();
  }
  // An inline <b> around the words that name a control, so the sentence can point
  // at it without a second copy of the control.
  function strong(words) { return '<b>' + words + '</b>'; }

  // The three-step explainer is open at every normal window size and folded at the
  // app's minimum height, where the sentence, the action and the empty state have
  // to share the first screen with the question form. The rule is kept here as
  // well as in clarity.css so the element's own `open` state agrees with what is
  // painted: a fold drawn shut by CSS but still `open` in the DOM would read out
  // its hidden cards to a screen reader.
  var STEPS_FOLD_BELOW = 561;
  function fitSteps() { return !(G.innerHeight > 0 && G.innerHeight < STEPS_FOLD_BELOW); }
  function applyStepsFold() {
    var fold = panes.research && panes.research.querySelector('[data-testid="clarity-steps-fold"]');
    if (!fold) return;
    var open = fitSteps();
    if (fold.open !== open) fold.open = open;
  }

  function purposeRow(name, titleKey, subKey, subArgKey, action) {
    var row = doc.createElement('div');
    row.className = 'clarity-purpose';
    row.setAttribute('data-testid', 'clarity-purpose-' + name);
    var copy = doc.createElement('div');
    copy.className = 'clarity-purpose-copy';
    var h = keyed(doc.createElement('h2'), titleKey);
    h.className = 'clarity-title';
    h.setAttribute('data-testid', 'clarity-title-' + name);
    writeCopy(h);
    var sub = keyed(doc.createElement('p'), subKey, subArgKey);
    sub.className = 'clarity-sub';
    sub.setAttribute('data-testid', 'clarity-sub-' + name);
    writeCopy(sub);
    copy.appendChild(h); copy.appendChild(sub);
    row.appendChild(copy);
    if (action) {
      var act = doc.createElement('div');
      act.className = 'clarity-purpose-act';
      var button = keyed(doc.createElement('button'), action.label);
      button.type = 'button';
      button.className = 'clarity-primary';
      button.id = 'clarity-primary-' + name;
      button.setAttribute('data-testid', 'clarity-primary-' + name);
      writeCopy(button);
      button.addEventListener('click', action.run);
      act.appendChild(button);
      if (action.note) {
        var note = keyed(doc.createElement('span'), action.note);
        note.className = 'clarity-act-note';
        writeCopy(note);
        act.appendChild(note);
      }
      row.appendChild(act);
    }
    return row;
  }
  function emptyState(name, titleKey, bodies) {
    var box = doc.createElement('div');
    box.className = 'clarity-empty';
    box.setAttribute('data-testid', 'clarity-empty-' + name);
    box.hidden = true;
    var h = keyed(doc.createElement('h3'), titleKey);
    writeCopy(h);
    box.appendChild(h);
    (bodies || []).forEach(function (body) {
      var p = keyed(doc.createElement('p'), body.key, body.arg);
      writeCopy(p);
      box.appendChild(p);
    });
    return box;
  }

  var section = doc.querySelector('#app-shell > main > section');
  if (!section) return;
  var panes = {};

  // --- B: Literature Review -----------------------------------------------
  // Primary action is "Add PDFs…", which runs the existing picker. The button is
  // new; the handler, the file input and the upload path are the ones that
  // already shipped.
  (function libraryPane() {
    var el = pane('library');
    el.appendChild(purposeRow('library',
      'This is where your sources live.',
      'Add the PDFs you are reading, keep notes beside them, and later pick these same sources when you check a claim in Writing.',
      null,
      { label: 'Add PDFs…', note: 'PDFs only. The original file is never changed.',
        run: function () {
          if (typeof G.libraryPickFiles === 'function') { G.libraryPickFiles(); return; }
          var open = $id('library-dropzone');
          if (open && open.focus) open.focus();
        } }));
    el.appendChild(emptyState('library', 'No documents yet', [
      { key: 'Drop PDFs on the list below, or use {action}. A file dropped on a folder is filed into that folder.',
        arg: 'Add PDFs…' },
      { key: 'Anything you add stays inside this project.' }
    ]));
    panes.library = el;    var view = $id('library-view');
    if (view && view.parentNode) view.parentNode.insertBefore(el, view);
  })();

  // --- C: Research ---------------------------------------------------------
  // The three real objects, in the order the menu works: a question you ask, an
  // evidence card the app produces, and a decision you make yourself. The primary
  // action focuses the existing New question control, which opens the one
  // existing form.
  (function researchPane() {
    var el = pane('research');
    var intro = $id('research-intro');
    el.appendChild(purposeRow('research',
      'Ask a question, look at the evidence, then decide.',
      'The app only proposes an answer. Nothing is applied to your work until you record a decision.',
      null,
      { label: 'New question…', note: 'One question, one answer. You can reopen it later.',
        run: function () {
          var existing = $id('quiet-new-question');
          if (existing && existing.click) { existing.click(); return; }
          var entry = $id('question-entry');
          if (entry) entry.open = true;
          var field = $id('question');
          if (field && field.focus) field.focus();
        } }));
    // The three steps are orientation, not a control: on a short window they fold
    // into one summary line so the sentence, the action and the empty state keep
    // the first screen. Above that height the fold is open and its summary hidden,
    // so nothing about the three-step copy is lost at a normal size.
    var fold = doc.createElement('details');
    fold.className = 'clarity-steps-fold';
    fold.setAttribute('data-testid', 'clarity-steps-fold');
    fold.open = fitSteps();
    var foldSummary = keyed(doc.createElement('summary'),
      'Three steps: your question, then the evidence card, then your decision.');
    foldSummary.className = 'clarity-steps-summary';
    foldSummary.setAttribute('data-testid', 'clarity-steps-summary');
    writeCopy(foldSummary);
    fold.appendChild(foldSummary);
    var steps = doc.createElement('ol');
    steps.className = 'clarity-steps';
    steps.setAttribute('data-testid', 'clarity-steps-research');
    [['Your question',
      'One sentence about the data you were given. Example: “Does approval_date mean final approval?”'],
     ['Evidence card',
      'What the record actually says, with its source and page. If the record is thin, the card says so instead of guessing.'],
     ['Your decision',
      'Adopt keeps the proposal, Defer comes back to it later, Reject leaves only the record of your reason.']
    ].forEach(function (step, index) {
      var li = doc.createElement('li');
      var n = doc.createElement('span');
      n.className = 'clarity-step-n';
      n.setAttribute('aria-hidden', 'true');
      n.textContent = String(index + 1);
      var t = keyed(doc.createElement('span'), step[0]);
      t.className = 'clarity-step-t';
      writeCopy(t);
      var p = keyed(doc.createElement('p'), step[1]);
      p.className = 'clarity-step-p';
      writeCopy(p);
      li.appendChild(n); li.appendChild(t); li.appendChild(p);
      steps.appendChild(li);
    });
    fold.appendChild(steps);
    el.appendChild(fold);
    el.appendChild(emptyState('research', 'No question yet', [
      { key: 'Open a project and start with {action}. As soon as a question is saved, its evidence card and the three decision buttons appear here.',
        arg: 'New question…' }
    ]));
    panes.research = el;
    // Directly above the question block it introduces.
    var anchor = intro || $id('project-title');
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(el, anchor);
    else section.insertBefore(el, section.firstChild);
  })();

  // --- D: Writing ----------------------------------------------------------
  // Primary action is "New paper…", which runs the existing latexChooseDraft(null,
  // true) the paper bar's own New paper button runs. The claim-check container is
  // the clean insertion point for frames F–I, which are not implemented yet.
  (function writingPane() {
    var el = pane('writing');
    el.appendChild(purposeRow('writing',
      'Write the draft, then check that its claims match the sources.',
      'Select a sentence and choose {action}. The check compares it with the sources in Literature Review, including the numbers.',
      'Verify claim',
      { label: 'New paper…', note: 'One paper holds one set of LaTeX files, up to 10.',
        run: function () {
          if (typeof G.latexChooseDraft === 'function') { G.latexChooseDraft(null, true); return; }
          // Before a paper exists the module is not mounted, so the same command
          // is reached through the button that carries it.
          var viaBar = doc.querySelector('#latex-view button[onclick*="latexChooseDraft(null, true)"]');
          if (viaBar && viaBar.click) viaBar.click();
        } }));
    var claims = emptyState('writing', 'No claims checked yet', [
      { key: 'Select a sentence in the draft and the {action} action appears. Every checked sentence is listed here with its result.',
        arg: 'Verify claim' }
    ]);
    el.appendChild(claims);
    // Frames F–I (Claim–Evidence Verification) mount here once the claim-check
    // contract is wired to this surface. Until then this container is empty and
    // carries no behaviour: nothing in it can look like a control that does not
    // exist yet.
    var claimsHost = doc.createElement('div');
    claimsHost.id = 'claim-check-host';
    claimsHost.className = 'claim-check-host';
    claimsHost.setAttribute('data-testid', 'claim-check-host');
    el.appendChild(claimsHost);
    panes.writing = el;
    var view = $id('latex-view');
    if (view && view.parentNode) view.parentNode.insertBefore(el, view);
  })();

  // --- E: Conversation Review ---------------------------------------------
  // Primary action is "Ask the secretary…", which opens the shipped secretary
  // drawer and puts the cursor in the one composer. With nothing to review yet
  // that is the shortest way to get something to review. The destructive bulk
  // action stays a secondary control and stays hidden until something is
  // discarded, exactly as it already behaved.
  (function reviewPane() {
    var el = pane('review');
    el.appendChild(purposeRow('review',
      'Decide what to keep from past conversations with the secretary.',
      'Newest first. Keeping an exchange changes only its status; the recorded text is never rewritten.',
      null,
      { label: 'Ask the secretary…',
        note: 'With nothing to review yet, this is the shortest way to get something to review.',
        run: function () {
          // The shipped shell owns the drawer. Its openPanel is the call that
          // records the state, updates aria-expanded and slides the panel in; a
          // bare class test here would be a second, wrong notion of "open".
          if (G.WB_DECLUTTER_SHELL && G.WB_DECLUTTER_SHELL.openPanel) {
            G.WB_DECLUTTER_SHELL.openPanel('secretary');
          }
          // The drawer slides for 160ms and the composer is not focusable until
          // it has arrived, so focus is set once the panel is actually visible.
          armFocus($id('secretary-input'));
        } }));
    el.appendChild(emptyState('review', 'No conversations recorded yet', [
      { key: 'Ask the secretary anything, from Home or from any screen, and each exchange appears here.' },
      { key: 'There are only two actions here: keep it, or delete it.' }
    ]));
    panes.review = el;
    var view = $id('secretary-review-view');
    if (view && view.parentNode) view.parentNode.insertBefore(el, view);
  })();

  // --- the empty states follow the real data ------------------------------
  // The three state objects are top-level declarations in app.js, library.js and
  // secretary_review.js. A classic script's let/const creates a global lexical
  // binding rather than a window property, so they are read by name behind a
  // typeof guard: a page without that module reads as "not known", never throws.
  function stateOf(name) {
    try { return eval('typeof ' + name + ' === "object" ? ' + name + ' : null'); }
    catch (_) { return null; }
  }
  // Writing an attribute records a mutation even when the value is unchanged, and
  // these states are refreshed from a MutationObserver. Assigning only on a real
  // change is what keeps that observer from driving itself: read, compare, write
  // at most once, and the loop has nowhere to close.
  function setHidden(box, hidden) {
    if (box.hidden !== hidden) box.hidden = hidden;
  }
  function libraryEmpty() {
    var box = panes.library && panes.library.querySelector('[data-testid="clarity-empty-library"]');
    if (!box) return;
    var state = stateOf('libraryState');
    if (!state) { setHidden(box, true); return; }
    var known = state.tree && Array.isArray(state.tree.items);
    setHidden(box, !(known && state.tree.items.length === 0 && !state.loading && !state.loadError));
  }
  function researchEmpty() {
    var box = panes.research && panes.research.querySelector('[data-testid="clarity-empty-research"]');
    if (!box) return;
    // A question with a result is not "no question yet".
    var app = stateOf('S');
    var working = !!(app && (app.question || app.busy));
    setHidden(box, working);
    // The three steps are orientation for someone who has not started: once a
    // question is being worked, its evidence card and decision buttons are on
    // screen doing the same job, and the space belongs to them.
    var steps = panes.research && panes.research.querySelector('[data-testid="clarity-steps-research"]');
    if (steps) setHidden(steps, working);
    var fold = panes.research && panes.research.querySelector('[data-testid="clarity-steps-fold"]');
    if (fold) setHidden(fold, working);  }
  function writingEmpty() {
    var box = panes.writing && panes.writing.querySelector('[data-testid="clarity-empty-writing"]');
    if (!box) return;
    var total = 0;
    try {
      var api = G.WB_CLAIM_CHECK;
      total = api && api.count ? Number(api.count()) || 0 : 0;
    } catch (_) { total = 0; }
    setHidden(box, total > 0);
  }
  function reviewEmpty() {
    var box = panes.review && panes.review.querySelector('[data-testid="clarity-empty-review"]');
    if (!box) return;
    var state = stateOf('secretaryReviewState');
    // Nothing is claimed until the list itself said it was read.
    setHidden(box, !(state && state.loaded && !state.failed &&
      Array.isArray(state.turns) && state.turns.length === 0));
  }
  var refreshEmptyStates = function () {
    libraryEmpty(); applyStepsFold(); researchEmpty(); writingEmpty(); reviewEmpty();
  };

  // --- keep the wording in one place across a language switch -------------
  // The panes are built once, so their strings are re-applied from the same
  // tables rather than captured by data-i18n (which reads authored markup).
  function relabel() {
    paintTilePurpose();
    // One walk over every node that recorded a key at build time. Nothing is
    // enumerated a second time, so a sentence cannot be left out of the switch by
    // forgetting to add it to a list here.
    Object.keys(panes).forEach(function (name) { applyCopy(panes[name]); });
    refreshEmptyStates();
  }

  // The data these empty states read is written by modules that will not announce
  // it, so the states are re-read when the workspace changes and when a view is
  // switched. The filter watches the view switches (`class`) and added or removed
  // rows; `hidden` is deliberately absent, because that is the attribute these
  // states write, and setHidden keeps those writes from recording at all.
  if (typeof G.MutationObserver === 'function') {
    var observer = new G.MutationObserver(refreshEmptyStates);
    observer.observe(section, { childList: true, subtree: true,
      attributes: true, attributeFilter: ['class'] });
    var shell = $id('app-shell');
    if (shell) observer.observe(shell, { attributes: true, attributeFilter: ['class'] });
  }
  if (G.WB_I18N) G.WB_I18N.onChange(relabel);

  refreshEmptyStates();
  G.WB_CLARITY = {
    panes: panes,
    refresh: refreshEmptyStates,
    relabel: relabel,
    purposeOf: function (key) { return TILE_PURPOSE[key] ? tr(TILE_PURPOSE[key]) : ''; }
  };
})();
