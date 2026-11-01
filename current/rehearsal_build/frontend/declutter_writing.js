/* Research Workbench — writing surface declutter (presentation only).
 *
 * This module never re-implements the writing view. It MOVES existing nodes
 * (file list, file actions, syntax helpers, citation picker, undo, cite entry,
 * save/reload/compile row, engine and CJK notes, re-check, compile help) into
 * the places the approved declutter design puts them:

 *   paper bar   : paper select · one save status · Compile · Files · Insert · Paper actions
 *   Files panel : the existing file list + file actions + import input
 *   Insert panel: the existing 9 syntax helpers, citation key picker, undo, help
 *   Paper actions dialog: New paper, Rename, Download source, Download PDF,
 *                         manual Save, Reload
 *   one disclosure in the preview pane: engine state, CJK note, compile help,
 *                         retention notice, re-check, raw TeX tail
 *
 * The parsed diagnostics stay on the preview surface: a failed build is read from
 * them, so they are never folded away.
 *
 * Every id, data-testid, inline handler and listener stays attached to the node it
 * already belonged to, so latex.js keeps working unchanged. Opening or closing a
 * panel never writes #latex-editor and never calls renderDraft()/renderPreview():
 * an open menu cannot touch the draft, the caret or a compiled PDF.
 */
(function () {
  'use strict';

  if (typeof window === 'undefined') return;
  if (window.WB_DECLUTTER_WRITING) return;

  var t = function (key, vars) {
    var i18n = window.WB_I18N;
    return (i18n && i18n.t) ? i18n.t(key, vars) : key;
  };

  function q(parent, selector) {
    if (!parent) return null;
    try { return parent.querySelector(selector); } catch (_) { return null; }
  }

  function button(label, className, testid) {
    var el = document.createElement('button');
    el.type = 'button';
    el.textContent = label;
    if (className) el.className = className;
    if (testid) el.setAttribute('data-testid', testid);
    return el;
  }

  function notes(msg) {
    // Never invent a status line: reuse the view's own error surface when it exists.
    if (typeof window.showError === 'function') { window.showError(msg); return; }
    var view = document.getElementById('latex-view');
    if (view) view.setAttribute('data-declutter-note', msg);
  }

  function mount() {
    var view = document.getElementById('latex-view');
    if (!view || view.getAttribute('data-declutter') === 'mounted') return;
    var toolbar = q(view, ':scope > .writing-toolbar');
    var editorPane = q(view, ':scope > .latex-editor');
    var previewPane = q(view, ':scope > .latex-preview');
    if (!toolbar || !editorPane || !previewPane) return;
    view.setAttribute('data-declutter', 'mounted');
    view.classList.add('dw-writing');

    /* ---- panel shell: a plain overlay div, never the top layer --------------- */
    function makePanel(id, titleKey) {
      var panel = document.createElement('div');
      panel.id = id;
      panel.className = 'dw-panel';
      panel.hidden = true;
      panel.tabIndex = -1;
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-modal', 'false');
      panel.setAttribute('aria-label', titleKey);
      panel.setAttribute('data-i18n-attrs', 'aria-label');
      var head = document.createElement('div');
      head.className = 'dw-panel-head';
      var heading = document.createElement('h3');
      heading.className = 'dw-panel-title';
      heading.textContent = titleKey;
      heading.setAttribute('data-i18n', '');
      var close = button('Close', 'dw-panel-close');
      close.setAttribute('data-i18n', '');
      close.setAttribute('aria-label', 'Close');
      close.setAttribute('data-i18n-attrs', 'aria-label');
      head.appendChild(heading);
      head.appendChild(close);
      panel.appendChild(head);
      view.appendChild(panel);
      close.addEventListener('click', function () { closePanels(false); });
      return panel;
    }

    /* ---- Files: the drawer holds the existing list and its actions ----------- */
    var filesPanel = makePanel('dw-files-panel', 'Files');
    var filesAside = q(view, '.writing-files');
    if (filesAside) filesPanel.appendChild(filesAside);

    /* ---- Insert: the existing helpers, picker, undo and help ---------------- */
    var insertPanel = makePanel('dw-insert-panel', 'Insert');
    var syntax = q(view, '.writing-syntax');
    if (syntax) insertPanel.appendChild(syntax);
    var citeEntry = q(view, '.latex-citation-entry');
    if (citeEntry) insertPanel.appendChild(citeEntry);

    /* ---- paper bar: one status line, Compile, and the three triggers -------- */
    var actions = q(view, '.writing-editor-actions');
    if (actions) toolbar.appendChild(actions);

    var triggers = document.createElement('div');
    triggers.className = 'dw-triggers';

    function trigger(panel, labelKey, testid) {
      var el = button(labelKey, 'dw-trigger', testid);
      el.setAttribute('data-i18n', '');
      el.setAttribute('aria-expanded', 'false');
      el.setAttribute('aria-controls', panel.id);
      triggers.appendChild(el);
      return el;
    }

    var filesTrigger = trigger(filesPanel, 'Files', 'dw-open-files');
    var insertTrigger = trigger(insertPanel, 'Insert', 'dw-open-insert');
    toolbar.appendChild(triggers);

    // Compile is the one prominent action, so it sits at the end of the paper bar as
    // the same button with the same handler and the same aria-describedby.
    var compileBtn = document.getElementById('latex-compile');
    if (compileBtn) {
      var primary = document.createElement('div');
      primary.className = 'dw-primary';
      primary.appendChild(compileBtn);
      toolbar.appendChild(primary);
    }

    // The Secretary entry already in the paper bar is a toggle for a panel the
    // desktop shell owns. It keeps its node and handler; it is only drawn quieter so
    // it stops competing with Compile.
    var barButtons = toolbar.querySelectorAll('button');
    for (var s = 0; s < barButtons.length; s++) {
      var barOnclick = barButtons[s].getAttribute('onclick') || '';
      if (barOnclick.indexOf('toggle-secretary') >= 0) barButtons[s].classList.add('dw-tertiary');
    }

    /* ---- Paper actions: one modal dialog above any open panel --------------- */
    var dialog = document.createElement('dialog');
    dialog.id = 'dw-paper-actions';
    dialog.setAttribute('aria-labelledby', 'dw-paper-actions-title');
    dialog.setAttribute('data-testid', 'dw-paper-actions');
    var dialogTitle = document.createElement('h3');
    dialogTitle.className = 'dw-panel-title';
    dialogTitle.id = 'dw-paper-actions-title';
    dialogTitle.textContent = 'Paper actions';
    dialogTitle.setAttribute('data-i18n', '');
    dialog.appendChild(dialogTitle);

    var actionList = document.createElement('div');
    actionList.className = 'dw-action-list';

    // The existing paper commands move in as they are: same nodes, same handlers.
    var paperButtons = toolbar.querySelectorAll('button');
    for (var j = 0; j < paperButtons.length; j++) {
      var onClick = paperButtons[j].getAttribute('onclick') || '';
      if (/latexChooseDraft\(null, true\)|latexDownloadSource\(\)|latexDownloadPdf\(\)/.test(onClick)) {
        actionList.appendChild(paperButtons[j]);
      }
    }
    var saveBtn = document.getElementById('latex-save');
    var reloadBtn = document.getElementById('latex-reload');
    if (saveBtn) actionList.appendChild(saveBtn);
    if (reloadBtn) actionList.appendChild(reloadBtn);

    var rename = document.createElement('div');
    rename.className = 'dw-rename';
    var renameLabel = document.createElement('label');
    renameLabel.className = 'dw-rename-label';
    renameLabel.htmlFor = 'dw-rename-title';
    renameLabel.textContent = 'Paper title';
    renameLabel.setAttribute('data-i18n', '');
    var renameInput = document.createElement('input');
    renameInput.id = 'dw-rename-title';
    renameInput.className = 'dw-rename-input';
    renameInput.type = 'text';
    renameInput.autocomplete = 'off';
    renameInput.setAttribute('data-testid', 'dw-rename-title');
    var renameBtn = button('Rename paper', '', 'dw-rename-paper');
    renameBtn.setAttribute('data-i18n', '');
    var renameError = document.createElement('p');
    renameError.className = 'dw-hint';
    renameError.id = 'dw-rename-error';
    renameError.setAttribute('role', 'alert');
    rename.appendChild(renameLabel);
    rename.appendChild(renameInput);
    rename.appendChild(renameBtn);
    rename.appendChild(renameError);
    actionList.appendChild(rename);

    var hint = document.createElement('p');
    hint.className = 'dw-hint';
    hint.textContent = 'Manual save and Escape stay available while this dialog is open. ⌘S saves anywhere in the writing view.';
    hint.setAttribute('data-i18n', '');
    actionList.appendChild(hint);
    dialog.appendChild(actionList);

    var footer = document.createElement('div');
    footer.className = 'dw-dialog-footer';
    var dialogClose = button('Close', 'dw-panel-close', 'dw-paper-actions-close');
    dialogClose.setAttribute('data-i18n', '');
    footer.appendChild(dialogClose);
    dialog.appendChild(footer);
    view.appendChild(dialog);

    /* ---- one contextual disclosure instead of repeated banners -------------- */
    var info = document.createElement('details');
    info.id = 'dw-info';
    info.className = 'dw-info';
    var summary = document.createElement('summary');
    summary.textContent = 'Compile, engine and source details';
    summary.setAttribute('data-i18n', '');
    info.appendChild(summary);
    var draftLabel = document.getElementById('latex-draft-label');
    var notice = document.getElementById('latex-notice');
    var absence = document.getElementById('latex-absence');
    var cjk = document.getElementById('latex-cjk-note');
    var compileNote = document.getElementById('latex-compile-note');
    var recheck = q(view, '[data-testid="latex-recheck"]');
    var aboutCompile = q(view, '.writing-editor-actions details');
    // The observed engine state leads the disclosure: when it matters it is the first
    // thing the reader sees, and otherwise it never stands between the paper and the
    // work as a separate banner.
    if (absence) {
      absence.classList.add('dw-absence');
      info.appendChild(absence);
    }
    for (var k = 0; k < [draftLabel, notice, compileNote, cjk].length; k++) {
      var node = [draftLabel, notice, compileNote, cjk][k];
      if (node) info.appendChild(node);
    }
    if (recheck && recheck.parentElement) info.appendChild(recheck.parentElement);
    if (aboutCompile) info.appendChild(aboutCompile);
    // The raw TeX output is the longest evidence in the view and the least often
    // needed: it belongs with the other details. The parsed diagnostics stay out
    // there, because those are what a failed build is actually read from.
    var rawLog = document.getElementById('latex-raw-log');
    if (rawLog) info.appendChild(rawLog);
    previewPane.appendChild(info);
    // The real reason text lives in this disclosure. While Compile is disabled that
    // text is what the button describes, so the disclosure is opened rather than left
    // shut over it; it closes again once there is nothing to explain. A disclosure the
    // reader opened themselves is never closed for them.
    if (compileBtn && compileNote) {
      var autoOpened = false;
      var syncInfoOpen = function () {
        if (compileBtn.disabled) {
          if (!info.open) { info.open = true; autoOpened = true; }
        } else if (autoOpened) {
          info.open = false; autoOpened = false;
        }
      };
      syncInfoOpen();
      if (typeof MutationObserver === 'function') {
        try { new MutationObserver(syncInfoOpen).observe(compileBtn, {attributes: true, attributeFilter: ['disabled']}); } catch (_) {}
      }
      info.addEventListener('toggle', function () { if (info.open === false) autoOpened = false; });
    }

    /* ---- open / close: state only, never the draft -------------------------- */
    function setTrigger(triggerEl, open) {
      if (triggerEl) triggerEl.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function closePanels(restore) {
      var changed = false;
      [filesPanel, insertPanel].forEach(function (panel) {
        if (!panel.hidden) { panel.hidden = true; changed = true; }
      });
      setTrigger(filesTrigger, false);
      setTrigger(insertTrigger, false);
      if (dialog.open) {
        dialog.close();
        setTrigger(actionsTrigger, false);
      }
      var back = restore === 'files' ? filesTrigger : restore === 'insert' ? insertTrigger : null;
      if (changed && back) back.focus();
      return changed;
    }

    function openPanel(panel, triggerEl) {
      if (!panel) return false;
      var wasOpen = !panel.hidden;
      closePanels(false);
      if (wasOpen) return false;
      panel.hidden = false;
      setTrigger(triggerEl, true);
      panel.focus();
      return true;
    }

    function openFiles() { return openPanel(filesPanel, filesTrigger); }
    function openInsert() { return openPanel(insertPanel, insertTrigger); }

    function openPaperActions() {
      closePanels(false);
      var state = typeof latexState !== 'undefined' ? latexState : null;
      var draft = state && state.draft;
      renameInput.value = draft ? draft.title : '';
      renameError.textContent = '';
      renameInput.disabled = !draft;
      renameBtn.disabled = !draft;
      if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.open = true;
      setTrigger(actionsTrigger, true);
      // Focus lands on the first command, not on the title field: the title is not
      // selected for overwriting just because the menu was opened, and no keystroke
      // meant for the draft can reach it by accident.
      if (!draft && dialogClose.focus) dialogClose.focus();
      return true;
    }

    function openInfo(open) {
      info.open = open === undefined ? true : !!open;
      if (info.open && summary.focus) summary.focus();
      return info.open;
    }

    filesTrigger.addEventListener('click', function () {
      if (filesPanel.hidden) openFiles(); else closePanels('files');
    });
    insertTrigger.addEventListener('click', function () {
      if (insertPanel.hidden) openInsert(); else closePanels('insert');
    });

    var actionsTrigger = button('Paper actions', 'dw-trigger', 'dw-open-paper-actions');
    actionsTrigger.setAttribute('data-i18n', '');
    actionsTrigger.setAttribute('aria-haspopup', 'dialog');
    actionsTrigger.setAttribute('aria-expanded', 'false');
    actionsTrigger.setAttribute('aria-controls', 'dw-paper-actions');
    triggers.appendChild(actionsTrigger);
    actionsTrigger.addEventListener('click', function () { openPaperActions(); });

    dialogClose.addEventListener('click', function () { closePanels('paper'); });
    dialog.addEventListener('close', function () {
      if (dialog.open) return;
      actionsTrigger.setAttribute('aria-expanded', 'false');
      if (filesPanel.hidden && insertPanel.hidden) actionsTrigger.focus();
    });

    // A dialog is in the top layer, so the existing Files panel can never cover it
    // and the existing Add/Rename file dialog still opens above everything. Save and
    // Reload keep working from inside it: the editor buffer belongs to latex.js,
    // which is never re-rendered just because a menu opened or closed.

    /* ---- rename: reuses the existing save path, adds no second writer ------- */
    async function renamePaper() {
      var state = typeof latexState !== 'undefined' ? latexState : null;
      if (!state || !state.draft) { renameError.textContent = t('Open a paper before renaming it.'); return null; }
      if (state.reloading || state.compiling || state.saving || state.switchingDraft) {
        renameError.textContent = t('Wait for the current save or compile to finish.');
        return null;
      }
      if (state.blocked) {
        renameError.textContent = t('Reload this paper before renaming it: a save conflict is unresolved.');
        return null;
      }
      var title = String(renameInput.value || '').replace(/\s+/g, ' ').trim();
      if (!title) { renameError.textContent = t('Enter a title.'); return null; }
      if (title.length > 120) { renameError.textContent = t('Use at most 120 characters.'); return null; }
      if (title === state.draft.title) { closePanels('paper'); return state.draft; }
      var previous = state.draft.title;
      state.draft.title = title;
      renameError.textContent = '';
      var saved = await window.saveDraft('manual');
      if (!saved) {
        if (state.draft) state.draft.title = previous;
        renameError.textContent = t('Not renamed: the paper could not be saved.');
        return null;
      }
      // Only the selector text is refreshed; the editor, caret and PDF are untouched.
      // The selector is drawn from the loaded draft list, so that one entry keeps the
      // same title as the saved draft instead of showing the old name.
      if (Array.isArray(state.drafts)) {
        for (var d = 0; d < state.drafts.length; d++) {
          if (state.drafts[d] && state.drafts[d].draft_id === saved.draft_id) state.drafts[d].title = saved.title;
        }
      }
      if (typeof window.renderLatexFiles === 'function') window.renderLatexFiles();
      closePanels('paper');
      return saved;
    }
    renameBtn.addEventListener('click', function () { renamePaper(); });
    renameInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); renamePaper(); }
    });

    /* ---- dismissal: Escape closes a panel, never the draft ------------------ */
    view.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' || dialog.open) return;
      if (filesPanel.hidden && insertPanel.hidden) return;
      if (event.stopPropagation) event.stopPropagation();
      closePanels(filesPanel.hidden ? 'insert' : 'files');
    }, true);

    document.addEventListener('pointerdown', function (event) {
      if (dialog.open || (filesPanel.hidden && insertPanel.hidden)) return;
      var target = event.target;
      if (!target || !target.closest) return;
      // A click inside another open dialog (the existing Add/Rename file dialog, or
      // the paper-actions dialog itself) must never close the panel it sits over.
      if (target.closest('.dw-panel, .dw-trigger, dialog')) return;
      closePanels(false);
    });

    /* ---- labels follow the existing language switch ------------------------- */
    if (window.WB_I18N && window.WB_I18N.register) {
      window.WB_I18N.register('ko', {
        'Insert': '삽입',
        'Paper actions': '논문 작업',
        'Close': '닫기',
        'Paper title': '논문 제목',
        'Rename paper': '논문 이름 바꾸기',
        'Compile, engine and source details': '컴파일·엔진·소스 안내',
        'Manual save and Escape stay available while this dialog is open. ⌘S saves anywhere in the writing view.':
          '이 대화상자가 열려 있어도 수동 저장과 Esc를 쓸 수 있습니다. ⌘S는 글쓰기 화면 어디서나 저장합니다.',
        'Open a paper before renaming it.': '이름을 바꾸려면 먼저 논문을 여세요.',
        'Wait for the current save or compile to finish.': '진행 중인 저장이나 컴파일이 끝날 때까지 기다리세요.',
        'Reload this paper before renaming it: a save conflict is unresolved.':
          '이름을 바꾸기 전에 이 논문을 다시 불러오세요: 저장 충돌이 아직 해결되지 않았습니다.',
        'Enter a title.': '제목을 입력하세요.',
        'Use at most 120 characters.': '최대 120자까지 쓸 수 있습니다.',
        'Not renamed: the paper could not be saved.': '이름을 바꾸지 못했습니다: 논문을 저장할 수 없습니다.'
      });
      if (window.WB_I18N.apply) window.WB_I18N.apply();
    }

    /* ---- panel placement: measured once, never on a timer ------------------- */
    // The panels float below the paper bar. Its height depends on the viewport, so
    // it is measured from the layout itself rather than assumed.
    function measureBar() {
      var height = toolbar.getBoundingClientRect ? toolbar.getBoundingClientRect().height : 0;
      if (height > 0) view.style.setProperty('--dw-bar', Math.round(height) + 'px');
    }
    measureBar();
    if (typeof ResizeObserver === 'function') {
      try { new ResizeObserver(measureBar).observe(toolbar); } catch (_) {}
    }
    if (window.addEventListener) window.addEventListener('resize', measureBar);

    var api = {
      version: 1,
      root: '#latex-view',
      selectors: {
        files: '#dw-files-panel', insert: '#dw-insert-panel',
        paperActions: '#dw-paper-actions', info: '#dw-info',
        triggers: {files: '[data-testid="dw-open-files"]', insert: '[data-testid="dw-open-insert"]',
          paperActions: '[data-testid="dw-open-paper-actions"]'}
      },
      // Kept for callers that address the moved originals directly.
      preserve: {save: 'latex-save', reload: 'latex-reload', compile: 'latex-compile',
        fileList: 'latex-files', fileDialog: 'latex-file-dialog', cite: 'citation-from-writing'},
      openFiles: openFiles,
      closeFiles: function () { if (!filesPanel.hidden) closePanels('files'); },
      toggleFiles: function () { if (filesPanel.hidden) openFiles(); else closePanels('files'); },
      openInsert: openInsert,
      closeInsert: function () { if (!insertPanel.hidden) closePanels('insert'); },
      toggleInsert: function () { if (insertPanel.hidden) openInsert(); else closePanels('insert'); },
      openPaperActions: openPaperActions,
      closePaperActions: function () { closePanels('paper'); },
      openInfo: openInfo,
      closeInfo: function () { info.open = false; },
      closePanels: function () { closePanels(false); },
      isOpen: function (name) {
        if (name === 'files') return !filesPanel.hidden;
        if (name === 'insert') return !insertPanel.hidden;
        if (name === 'paperActions') return !!dialog.open;
        if (name === 'info') return !!info.open;
        return false;
      },
      renamePaper: renamePaper
    };
    window.WB_DECLUTTER_WRITING = api;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
