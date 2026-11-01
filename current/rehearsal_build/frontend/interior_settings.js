// Categorized settings interior (approved design state C): four categories in a
// 250px navigation column beside a quiet preference body.
//
// This file is the view only. frontend/startup.js owns the lifecycle (when the
// sheet is on screen, which layer it lives in, Escape, the Tab trap and focus
// restoration) and calls open()/close() here.
//
// The four preference rows the app already shipped (language, theme, startup
// sounds, research field) and the session button are MOVED out of the flat sheet
// into the categories, never cloned: their own listeners, ids and data-testid
// attributes stay exactly as they were, and no id is ever drawn twice. The
// language, theme and sound listeners therefore keep writing the same
// preferences they wrote before this file existed.
//
// Nothing here contacts a provider, reads a key, or approves anything. Model
// readiness is copied from the workspace AI panel's own status lines, and real
// configuration (keys, allowance, scope preview, consent) stays in that panel.
(function () {
  'use strict';

  var G = typeof window !== 'undefined' ? window : {};
  // Language lookup from i18n.js, with an English fallback so the module still
  // runs where that script is absent.
  var t = (G.WB_I18N && G.WB_I18N.t) ? G.WB_I18N.t : function (key, vars) {
    if (!vars) return key;
    return String(key).replace(/\{(\w+)\}/g, function (m, name) {
      return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m;
    });
  };

  function $(id) { return document.getElementById(id); }

  function node(tag, className) {
    var out = document.createElement(tag);
    if (className) out.className = className;
    return out;
  }

  // Prose that i18n.js translates. The authored English sentence is the key, so
  // a missing Korean entry falls back to the sentence and never to a key name.
  function prose(tag, key, className) {
    var out = node(tag, className);
    out.textContent = key;
    out.setAttribute('data-i18n', '');
    return out;
  }

  var CATEGORIES = ['general', 'models', 'cost', 'privacy'];
  var LABELS = {
    general: 'General',
    models: 'Model connections',
    cost: 'Scope + cost',
    privacy: 'Privacy + session'
  };
  // Readiness rows: label, the workspace element that owns the sentence, and the
  // key this module keeps the copy under. The workspace stays the authority.
  var MIRRORS = [
    ['Baseten / Muse Spark Open', 'ai-baseten-status', 'baseten'],
    ['API account', 'ai-connection', 'cloud'],
    ['Local model', 'ai-local-status', 'local'],
    ['Modal Shared / Spark Open', 'ai-modal-shared-status', 'modal_shared'],
    ['Modal / Glimmer 30B', 'ai-modal-status', 'modal']
  ];

  var mounted = false;
  var active = 'general';
  var hooks = {};
  var tabs = {};
  var panes = {};
  var parts = {};
  var statuses = {};
  var noteCount = 0;
  var navEl = null;
  var fieldValue = null;

  // --- Small builders -------------------------------------------------------
  function pane(key) {
    var section = node('section', 'is-pane');
    section.id = 'is-pane-' + key;
    section.setAttribute('role', 'tabpanel');
    section.setAttribute('aria-labelledby', 'is-tab-' + key);
    section.setAttribute('data-testid', 'interior-settings-pane-' + key);
    section.hidden = true;
    panes[key] = section;
    return section;
  }

  function statusLine(id) {
    var p = node('p', 'is-status');
    p.id = id;
    p.setAttribute('role', 'status');
    p.setAttribute('data-testid', id);
    return p;
  }

  function rowOf(el) { return (el && el.closest) ? el.closest('.settings-row') : null; }

  // Move a shipped row into a category, then say what it does under it. The row
  // element itself is moved, so the control inside keeps its listener.
  function stackRow(host, row, noteKey) {
    if (!host || !row) return null;
    var wrap = node('div', 'is-row');
    wrap.appendChild(row);
    host.appendChild(wrap);
    if (noteKey) {
      noteCount += 1;
      var note = prose('p', noteKey, 'is-note');
      note.id = 'is-note-' + noteCount;
      wrap.appendChild(note);
      var control = row.querySelector ? row.querySelector('select, input, button') : null;
      if (control && control.setAttribute) control.setAttribute('aria-describedby', note.id);
    }
    return wrap;
  }

  function actionButton(key, id, testid) {
    var button = prose('button', key);
    button.type = 'button';
    if (id) button.id = id;
    if (testid) button.setAttribute('data-testid', testid);
    return button;
  }

  // A row this module authors itself (nothing shipped to move): title, the
  // honest note, then its own controls.
  function authoredRow(host, titleKey, noteKey) {
    var wrap = node('div', 'is-row');
    wrap.appendChild(prose('p', titleKey, 'is-row-title'));
    if (noteKey) wrap.appendChild(prose('p', noteKey, 'is-note'));
    var actions = node('div', 'is-actions');
    wrap.appendChild(actions);
    host.appendChild(wrap);
    return actions;
  }

  // --- Categories -----------------------------------------------------------
  function buildGeneral() {
    var host = pane('general');
    host.appendChild(prose('p', 'Settings / General', 'is-eyebrow'));
    host.appendChild(prose('h2', 'Make room for your attention.', 'is-pane-title'));
    stackRow(host, rowOf($('settings-lang')),
      'The interface language. English is the authored default; Korean is available.');
    stackRow(host, rowOf($('settings-theme')),
      'Light, Dark, or System to follow this device.');
    stackRow(host, rowOf($('settings-sound')),
      'Short tones when the cartridge seats and when a menu is chosen. Off keeps this window silent.');
    var shipped = document.querySelector ? document.querySelector('#settings-sheet .settings-note') : null;
    if (shipped) host.appendChild(shipped);
    stackRow(host, rowOf($('settings-field')),
      'The seated research field. Changing it reopens the cartridge picker; projects, questions and stored data are not touched.');
    var current = node('p', 'is-current');
    current.appendChild(prose('span', 'Current field'));
    current.appendChild(document.createTextNode(' '));
    fieldValue = node('strong');
    fieldValue.id = 'is-current-field';
    current.appendChild(fieldValue);
    host.appendChild(current);
    host.appendChild(statusLine('is-general-status'));
    return host;
  }

  function buildModels() {
    var host = pane('models');
    host.appendChild(prose('p', 'Settings / Model connections', 'is-eyebrow'));
    host.appendChild(prose('h2', 'Research Secretary connection.', 'is-pane-title'));
    var metaInput = $('ai-key-meta');
    var metaRow = metaInput && metaInput.closest ? metaInput.closest('.ai-key-row') : null;
    if (metaRow) host.appendChild(metaRow);
    host.appendChild(prose('p',
      'Readiness is copied from the workspace AI panel. Opening this window contacts no provider and checks no account.',
      'is-note'));
    MIRRORS.forEach(function (row, index) {
      var wrap = node('div', 'is-row is-mirror');
      wrap.appendChild(prose('p', row[0], 'is-row-title'));
      var out = node('p', 'is-mirror-text');
      out.id = 'is-mirror-' + row[2];
      out.setAttribute('role', 'note');
      out.setAttribute('data-testid', 'interior-settings-mirror-' + row[2]);
      wrap.appendChild(out);
      host.appendChild(wrap);
      parts[row[2]] = out;
      return index;
    });
    host.appendChild(prose('p',
      'Modal Shared serves Muse Spark Open over a Modal-provided API endpoint and bills per token, not GPU time. Credits included with a Modal plan cannot be used for it; whether a separate promotional or hackathon grant covers it depends on that account\'s terms and is not checked here. The allowance in the AI panel is this app\'s own bookkeeping, not provider credit. The old GPU-hosted Glimmer route stays blocked and out of the model picker. Neither route is contacted by opening this window.',
      'is-notice'));
    var actions = node('div', 'is-actions');
    var inspect = actionButton('Inspect availability', 'is-inspect-availability',
      'interior-settings-inspect');
    inspect.addEventListener('click', function () {
      setStatus('is-models-status',
        'No endpoint has been checked from this window and no account request was sent. Readiness above is the last value the workspace AI panel reported.');
    });
    var openPanel = actionButton('Open the AI panel to configure', 'is-open-ai-panel',
      'interior-settings-open-ai');
    openPanel.addEventListener('click', function () {
      route('is-models-status', 'ai-panel');
    });
    actions.appendChild(inspect);
    actions.appendChild(openPanel);
    host.appendChild(actions);
    host.appendChild(statusLine('is-models-status'));
    return host;
  }

  function buildCost() {
    var host = pane('cost');
    host.appendChild(prose('p', 'Settings / Scope + cost', 'is-eyebrow'));
    host.appendChild(prose('h2', 'Consent belongs to each run.', 'is-pane-title'));
    host.appendChild(prose('p',
      'Each cloud run needs a fresh preview of its exact scope and maximum reservation, and your approval of that one preview. A scope change invalidates the approval.',
      'is-pane-lead'));
    var notice = node('div', 'is-notice');
    notice.appendChild(prose('strong', 'No transmission is approved here'));
    notice.appendChild(prose('p',
      'This window explains the boundary. Preparing a run, reviewing its scope and approving a reservation happen in the workspace AI panel. Nothing is sent from here.'));
    host.appendChild(notice);
    host.appendChild(prose('p',
      'An unknown provider charge keeps the reservation, so an unsettled run cannot quietly free budget.',
      'is-note'));
    var actions = node('div', 'is-actions');
    var prepare = actionButton('Prepare a scoped run in the AI panel', 'is-open-ai-cost',
      'interior-settings-open-ai-cost');
    prepare.addEventListener('click', function () {
      route('is-cost-status', 'ai-panel');
    });
    actions.appendChild(prepare);
    host.appendChild(actions);
    host.appendChild(statusLine('is-cost-status'));
    return host;
  }

  function buildPrivacy() {
    var host = pane('privacy');
    host.appendChild(prose('p', 'Settings / Privacy + session', 'is-eyebrow'));
    host.appendChild(prose('h2', 'Your work has boundaries.', 'is-pane-title'));
    host.appendChild(prose('p',
      'Question context stays inside its pinned pack, and registered source files are copies, so the files you chose stay unchanged.',
      'is-pane-lead'));

    var deletion = authoredRow(host, 'Question deletion',
      'Removes the question with its AI assets, jobs, usage and reviews, and keeps late results from bringing it back. Spend records and project drafts are kept by design, so deletion cannot reset spend.');
    var note = prose('p',
      'Deletion is confirmed in the workspace before anything is removed. Forensic erasure of database files is not performed.',
      'is-note');
    note.id = 'is-delete-semantics-note';
    note.hidden = true;
    var semantics = actionButton('Review deletion semantics', 'is-delete-semantics',
      'interior-settings-delete-semantics');
    semantics.setAttribute('aria-expanded', 'false');
    semantics.setAttribute('aria-controls', note.id);
    semantics.addEventListener('click', function () {
      var opening = note.hidden;
      note.hidden = !opening;
      semantics.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    var openDelete = actionButton('Open the delete control', 'is-open-delete',
      'interior-settings-open-delete');
    openDelete.addEventListener('click', function () {
      route('is-privacy-status', 'delete-btn', {
        gateId: 'delete-btn',
        gateKey: 'Deletion needs an open question. Open one in the workspace, then use its delete control.'
      });
    });
    deletion.appendChild(semantics);
    deletion.appendChild(openDelete);
    host.appendChild(note);

    // The shipped session button, moved: its own listener still calls the app's
    // end-session action, so this category never ends a session by itself.
    stackRow(host, rowOf($('settings-end')),
      'Ends the local backend session and clears session-only text. Copies already sent to an external provider cannot be deleted here.');
    host.appendChild(statusLine('is-privacy-status'));
    return host;
  }

  // --- Navigation -----------------------------------------------------------
  function buildTabs() {
    var list = node('div', 'is-tabs');
    list.setAttribute('role', 'tablist');
    list.setAttribute('aria-orientation', 'vertical');
    list.setAttribute('data-testid', 'interior-settings-tabs');
    CATEGORIES.forEach(function (key) {
      var tab = prose('button', LABELS[key], 'is-tab');
      tab.type = 'button';
      tab.id = 'is-tab-' + key;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', 'is-pane-' + key);
      tab.setAttribute('aria-selected', 'false');
      tab.setAttribute('tabindex', '-1');
      tab.setAttribute('data-category', key);
      tab.setAttribute('data-testid', 'interior-settings-tab-' + key);
      tab.addEventListener('click', function () { select(key, { focusTab: false }); });
      tabs[key] = tab;
      list.appendChild(tab);
    });
    list.addEventListener('keydown', onTabKey);
    return list;
  }

  // Standard tab keys, and automatic activation: moving to a category shows it,
  // so one keystroke is enough. Enter and Space are not intercepted; the button
  // activates natively.
  function onTabKey(event) {
    if (!event || !event.key) return;
    var index = CATEGORIES.indexOf(active);
    var next = null;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % CATEGORIES.length;
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index - 1 + CATEGORIES.length) % CATEGORIES.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = CATEGORIES.length - 1;
    if (next === null) return;
    if (event.preventDefault) event.preventDefault();
    select(CATEGORIES[next], { focusTab: true });
  }

  function select(key, options) {
    if (!mounted || CATEGORIES.indexOf(key) < 0) return;
    var opts = options || {};
    var previous = panes[active];
    var held = document.activeElement;
    var wasInside = !!(previous && held && previous.contains && previous.contains(held));
    active = key;
    CATEGORIES.forEach(function (name) {
      var tab = tabs[name];
      if (tab) {
        tab.setAttribute('aria-selected', name === key ? 'true' : 'false');
        tab.setAttribute('tabindex', name === key ? '0' : '-1');
      }
      if (panes[name]) panes[name].hidden = name !== key;
    });
    // Switching must not drop focus on the floor: if the control that held it was
    // inside the pane just hidden, the category itself takes focus. Nothing about
    // the session, a question or a control's value changes here.
    if ((opts.focusTab || wasInside) && tabs[key] && tabs[key].focus) {
      tabs[key].focus({ preventScroll: true });
    }
    refresh();
  }

  // --- Derived text and routing --------------------------------------------
  function copyMirror(sourceId, target) {
    if (!target) return;
    var source = $(sourceId);
    var text = source && typeof source.textContent === 'string' ? source.textContent.trim() : '';
    if (text && target.textContent !== text) target.textContent = text;
  }

  function currentField() {
    var tag = $('home-field');
    var text = tag && typeof tag.textContent === 'string' ? tag.textContent.trim() : '';
    return text || t('No research field');
  }

  // Read-only refresh: derived sentences and nothing else. It never assigns a
  // control's value and never moves focus, so a language or theme change cannot
  // reset a focused control or the text a person is in the middle of typing.
  function refresh() {
    if (!mounted) return;
    copyMirror('ai-connection', parts.cloud);
    copyMirror('ai-baseten-status', parts.baseten);
    copyMirror('ai-local-status', parts.local);
    copyMirror('ai-modal-shared-status', parts.modal_shared);
    copyMirror('ai-modal-status', parts.modal);
    if (fieldValue) fieldValue.textContent = currentField();
    if (navEl) navEl.setAttribute('aria-label', t('Settings categories'));
    Object.keys(statuses).forEach(renderStatus);
  }

  function statusEl(id) { return $(id); }

  function setStatus(id, key) {
    statuses[id] = { key: key };
    renderStatus(id);
  }

  function renderStatus(id) {
    var el = statusEl(id);
    if (!el) return;
    var entry = statuses[id];
    el.textContent = entry ? t(entry.key) : '';
  }

  function clearStatuses() {
    statuses = {};
    Object.keys(panes).forEach(function (name) {
      var p = panes[name] && panes[name].querySelectorAll ? panes[name].querySelectorAll('.is-status') : [];
      for (var i = 0; i < p.length; i++) p[i].textContent = '';
    });
  }

  function workspaceOpen() {
    var portal = $('portal');
    return !!(portal && !portal.hidden);
  }

  function firstFocusable(host) {
    if (!host || !host.querySelectorAll) return null;
    var items = host.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])');
    for (var i = 0; i < items.length; i++) {
      if (items[i].getClientRects && items[i].getClientRects().length === 0) continue;
      return items[i];
    }
    return items.length ? items[0] : null;
  }

  // Send the researcher to the surface that already owns this configuration,
  // instead of building a second editor here. Availability is decided before the
  // sheet closes, so a refusal is stated where it can still be read.
  function route(statusId, targetId, options) {
    var opts = options || {};
    var target = $(targetId);
    if (!workspaceOpen() || !target) {
      setStatus(statusId, 'The workspace is not open. Open a project from the home menu, then return here.');
      return false;
    }
    if (opts.gateId) {
      var gate = $(opts.gateId);
      if (gate && gate.disabled) {
        if (opts.gateKey) setStatus(statusId, opts.gateKey);
        return false;
      }
    }
    if (typeof hooks.close === 'function') {
      hooks.close({ restoreFocus: false });
    }
    if (targetId === 'ai-panel') {
      var toggle = $('toggle-secretary');
      if (toggle.getAttribute('aria-expanded') === 'false') toggle.click();
    }
    if (targetId === 'delete-btn') {
      G.showResearchView();
      if (G.WB_DECLUTTER_SHELL) G.WB_DECLUTTER_SHELL.openSupport();
    }
    if (target.tagName === 'DETAILS' && typeof target.open === 'boolean') target.open = true;
    if (target.scrollIntoView) target.scrollIntoView({ block: 'center' });
    var destination = (typeof target.focus === 'function' && target.tabIndex >= 0) ? target : firstFocusable(target);
    if (destination && destination.focus) destination.focus({ preventScroll: true });
    return true;
  }

  // --- Lifecycle hooks called by startup.js --------------------------------
  function build() {
    if (mounted) return true;
    var sheet = $('settings-sheet');
    if (!sheet || !sheet.appendChild) return false;
    var shell = node('div', 'is-shell');
    navEl = node('nav', 'is-nav');
    navEl.setAttribute('data-testid', 'interior-settings-nav');
    // The dialog's own title moves into the navigation column, where the approved
    // interior puts it. Its id still labels the dialog.
    var title = $('settings-title');
    if (title) navEl.appendChild(title);
    navEl.appendChild(buildTabs());
    var foot = node('p', 'is-footnote');
    foot.appendChild(prose('span', 'Window preferences'));
    foot.appendChild(prose('span', 'Preferences stay on this device'));
    navEl.appendChild(foot);
    var body = node('div', 'is-body');
    body.appendChild(buildGeneral());
    body.appendChild(buildModels());
    body.appendChild(buildCost());
    body.appendChild(buildPrivacy());
    shell.appendChild(navEl);
    shell.appendChild(body);
    sheet.appendChild(shell);
    // With the Tab trap in startup.js the sheet is a real modal, and says so.
    sheet.setAttribute('aria-modal', 'true');
    mounted = true;
    select(active, {});
    if (G.WB_I18N && G.WB_I18N.apply) G.WB_I18N.apply();
    return true;
  }

  function open(options) {
    hooks = options || {};
    if (!build()) return false;
    select(active, {});
    return true;
  }

  function close() {
    // The lifecycle belongs to startup.js. A transient routing message is the only
    // thing this view owes the next visit.
    if (mounted) clearStatuses();
  }

  if (G.WB_I18N && G.WB_I18N.onChange) {
    // A language switch redraws the derived sentences this file wrote; the static
    // prose is translated by i18n.js and no control value is touched.
    G.WB_I18N.onChange(function () { refresh(); });
  }

  G.WB_INTERIOR_SETTINGS = {
    build: build,
    open: open,
    close: close,
    refresh: refresh,
    select: select,
    categories: CATEGORIES.slice(),
    active: function () { return active; }
  };
})();

// --- Korean strings for the settings interior --------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// 'Settings' itself is registered by i18n.ko.js with the rest of the shell.
if (typeof window !== 'undefined' && window.WB_I18N) window.WB_I18N.register('ko', {
  'General': '일반',
  'Model connections': '모델 연결',
  'Scope + cost': '범위 + 비용',
  'Privacy + session': '개인정보 + 세션',
  'Settings categories': '설정 항목',
  'Window preferences': '창 환경설정',
  'Preferences stay on this device': '환경설정은 이 기기에 저장됩니다',
  'Settings / General': '설정 / 일반',
  'Make room for your attention.': '주의를 기울일 여지를 남깁니다.',
  'The interface language. English is the authored default; Korean is available.':
    '인터페이스 언어입니다. 영어가 기본이며 한국어를 선택할 수 있습니다.',
  'Light, Dark, or System to follow this device.': '밝게, 어둡게, 또는 기기를 따르는 시스템입니다.',
  'Short tones when the cartridge seats and when a menu is chosen. Off keeps this window silent.':
    '카트리지가 자리 잡을 때와 메뉴를 고를 때 짧은 소리가 납니다. 끄면 이 창은 조용합니다.',
  'The seated research field. Changing it reopens the cartridge picker; projects, questions and stored data are not touched.':
    '현재 자리 잡은 연구 분야입니다. 바꾸면 카트리지 선택이 다시 열리며, 프로젝트와 질문, 저장된 데이터는 바뀌지 않습니다.',
  'Current field': '현재 분야',
  'No research field': '연구 분야 없음',
  'Settings / Model connections': '설정 / 모델 연결',
  'Research Secretary connection.': '연구 비서 연결.',
  'Readiness is copied from the workspace AI panel. Opening this window contacts no provider and checks no account.':
    '준비 상태는 작업대의 AI 패널에서 그대로 옮겨 옵니다. 이 창을 여는 것만으로 제공자에 접속하거나 계정을 확인하지 않습니다.',
  'Local model': '로컬 모델',
  'Modal Shared / Spark Open': 'Modal 공유 / Spark Open',
  'Modal / Glimmer 30B': 'Modal / Glimmer 30B',
  'Modal Shared serves Muse Spark Open over a Modal-provided API endpoint and bills per token, not GPU time. Credits included with a Modal plan cannot be used for it; whether a separate promotional or hackathon grant covers it depends on that account\'s terms and is not checked here. The allowance in the AI panel is this app\'s own bookkeeping, not provider credit. The old GPU-hosted Glimmer route stays blocked and out of the model picker. Neither route is contacted by opening this window.':
    'Modal 공유는 Modal이 제공하는 API 엔드포인트로 Muse Spark Open을 실행하며 GPU 시간이 아니라 토큰 단위로 과금합니다. Modal 요금제에 포함된 크레딧은 이 경로에 쓸 수 없습니다. 별도의 프로모션 또는 해커톤 지원금으로 비용이 충당되는지는 해당 계정의 약관에 따르며 이 앱에서는 확인하지 않습니다. AI 패널의 한도는 이 앱이 자체적으로 기록하는 지출 한도이며 제공자 크레딧이 아닙니다. 예전 GPU 기반 Glimmer 경로는 계속 차단되어 모델 선택 목록에도 나오지 않습니다. 이 창을 여는 것만으로 두 경로 중 어느 것에도 접속하지 않습니다.',
  'API account': 'API 계정',
  'Inspect availability': '사용 가능 여부 보기',
  'Open the AI panel to configure': '설정하려면 AI 패널 열기',
  'No endpoint has been checked from this window and no account request was sent. Readiness above is the last value the workspace AI panel reported.':
    '이 창에서는 엔드포인트를 확인하지 않았고 계정 요청도 보내지 않았습니다. 위 준비 상태는 작업대 AI 패널이 마지막으로 보고한 값입니다.',
  'Settings / Scope + cost': '설정 / 범위 + 비용',
  'Consent belongs to each run.': '동의는 실행마다 따로입니다.',
  'Each cloud run needs a fresh preview of its exact scope and maximum reservation, and your approval of that one preview. A scope change invalidates the approval.':
    '클라우드 실행마다 정확한 범위와 최대 예약 금액을 새로 미리 보고, 그 미리보기 하나에 동의해야 합니다. 범위가 바뀌면 동의는 효력을 잃습니다.',
  'No transmission is approved here': '여기서는 전송을 승인하지 않습니다',
  'This window explains the boundary. Preparing a run, reviewing its scope and approving a reservation happen in the workspace AI panel. Nothing is sent from here.':
    '이 창은 경계를 설명합니다. 실행 준비와 범위 검토, 예약 승인은 작업대 AI 패널에서 합니다. 여기서 전송되는 것은 없습니다.',
  'An unknown provider charge keeps the reservation, so an unsettled run cannot quietly free budget.':
    '제공자 청구가 확인되지 않으면 예약 금액이 유지되므로, 정산되지 않은 실행이 예산을 조용히 풀어 주지 않습니다.',
  'Prepare a scoped run in the AI panel': 'AI 패널에서 범위가 정해진 실행 준비하기',
  'Settings / Privacy + session': '설정 / 개인정보 + 세션',
  'Your work has boundaries.': '연구에는 경계가 있습니다.',
  'Question context stays inside its pinned pack, and registered source files are copies, so the files you chose stay unchanged.':
    '질문 맥락은 고정된 팩 안에 머물고, 등록한 원천 파일은 사본이므로 선택한 파일은 그대로입니다.',
  'Question deletion': '질문 삭제',
  'Removes the question with its AI assets, jobs, usage and reviews, and keeps late results from bringing it back. Spend records and project drafts are kept by design, so deletion cannot reset spend.':
    '질문과 그 AI 자산, 작업, 사용 기록, 검토를 지우고, 늦게 도착한 결과가 질문을 되살리지 못하게 합니다. 지출 기록과 프로젝트 초안은 설계상 남으므로 삭제로 지출이 초기화되지 않습니다.',
  'Review deletion semantics': '삭제 기준 보기',
  'Deletion is confirmed in the workspace before anything is removed. Forensic erasure of database files is not performed.':
    '삭제 전에 작업대에서 확인을 받습니다. 데이터베이스 파일의 포렌식 삭제는 하지 않습니다.',
  'Open the delete control': '삭제 조작부 열기',
  'Session': '세션',
  'Ends the local backend session and clears session-only text. Copies already sent to an external provider cannot be deleted here.':
    '로컬 백엔드 세션을 끝내고 세션 한정 텍스트를 지웁니다. 외부 제공자로 이미 보낸 사본은 여기서 삭제할 수 없습니다.',
  'The workspace is not open. Open a project from the home menu, then return here.':
    '작업대가 열려 있지 않습니다. 홈 메뉴에서 프로젝트를 연 뒤 다시 오세요.',
  'Deletion needs an open question. Open one in the workspace, then use its delete control.':
    '삭제하려면 질문이 열려 있어야 합니다. 작업대에서 질문을 연 뒤 삭제 조작부를 사용하세요.'
});
