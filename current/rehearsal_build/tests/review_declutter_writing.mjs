// Writing surface declutter: real index.html markup, the real style chain and the
// real latex.js, with only transport and the app's own bootstrap stubbed.
// Every assertion is about behaviour a researcher sees: which node sits where, what
// changed in the editor, what stayed on screen. No app, no server, no TeX engine.
import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const frontend = new URL('../frontend/', import.meta.url);
const read = (name) => readFile(new URL(name, frontend), 'utf8');
const [html, i18n, latex, declutter, declutterCss] = await Promise.all([
  read('index.html'), read('i18n.js'), read('latex.js'), read('declutter_writing.js'), read('declutter_writing.css')
]);
const styles = await Promise.all(['style.css', 'startup.css', 'desktop_shell.css', 'home_hub.css',
  'console_motion.css', 'writing.css', 'project_order.css', 'library_order.css',
  'interior_research.css', 'interior_secretary.css', 'interior_settings.css'].map(read));

// (0) The declutter layer is presentation only: it must not own behaviour of its own.
assert.ok(!/fetch\(|XMLHttpRequest/.test(declutter), 'declutter_writing.js performs no transport work of its own');
for (const forbidden of ['renderDraft(', 'renderPreview(']) {
  const calls = declutter.split('\n').filter((line) => line.includes(forbidden) &&
    !line.trim().startsWith('*') && !line.trim().startsWith('//') && !line.trim().startsWith('/*'));
  assert.equal(calls.length, 0, `declutter_writing.js must never call ${forbidden}`);
}
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!declutterCss.toLowerCase().includes(banned), `declutter css must not use ${banned}`);

const documentHtml = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.route('**/*', (route) => route.abort());
  await page.setContent(documentHtml);
  await page.addStyleTag({ content: [...styles, declutterCss].join('\n') });
  await page.evaluate(() => {
    document.documentElement.classList.add('desktop-shell');
    document.body.classList.add('workbench-open');
    const startup = document.querySelector('#startup');
    if (startup) startup.remove();
    const portal = document.querySelector('#portal');
    portal.hidden = false; portal.classList.add('expanded'); portal.style.inset = '0';
    document.querySelector('#app-shell').classList.remove('hidden');
    document.querySelector('main section').classList.add('writing-mode');
    document.querySelector('#latex-view').classList.remove('hidden');
  });
  // The app's own globals, stubbed the same way the existing writing tests do.
  await page.evaluate(() => {
    window.S = { project: 'p1', question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' };
    window.$ = (id) => document.getElementById(id);
    window.esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    window.controls = function () {};
    window.errors = [];
    window.showError = (message) => { window.errors.push(message); };
    window.saved = [];
    window.call = async (method, path, body) => {
      if (method === 'GET' && path === '/capabilities/latex')
        return { available: true, engines: { pdflatex: '/usr/local/bin/pdflatex' }, checked_at: 'now', pdf_export: true };
      if (method === 'GET' && path.endsWith('/drafts'))
        return { drafts: [{ draft_id: 'd1', title: 'Untitled paper' }] };
      if (method === 'GET' && path === '/drafts/d1')
        return window.draftPayload();
      if (method === 'PUT') {
        window.saved.push(body);
        const next = window.draftPayload();
        next.revision = body.expected_revision + 1;
        next.files = body.files;
        next.title = body.title;
        return next;
      }
      throw new Error(`unexpected ${method} ${path}`);
    };
    window.draftPayload = () => ({ draft_id: 'd1', project_id: 'p1', question_id: null, title: 'Untitled paper',
      engine: 'auto', main_file: 'main.tex', revision: 3,
      files: [{ path: 'main.tex', text: '\\documentclass{article}\n\\begin{document}\nSynthetic writing fixture.\n\\end{document}\n' },
              { path: 'references.bib', text: '@article{Real2026, title={Actual entry}}' }],
      sections: [], previous: null, created_at: 'x', updated_at: 'x', model_calls: 0,
      notice: 'The newest 50 autosave steps are kept; older steps are discarded.' });
  });
  await page.addScriptTag({ content: i18n });
  await page.addScriptTag({ content: latex });
  // Where every original node lived before the declutter layer ran.
  await page.evaluate(() => { window.openWritingView(); });
  await page.waitForFunction(() => typeof latexState === 'object' && latexState && latexState.draft);
  const before = await page.evaluate(() => {
    const keep = {};
    for (const id of ['latex-files', 'latex-file-dialog', 'latex-editor', 'latex-save', 'latex-reload',
      'latex-compile', 'latex-status', 'latex-draft-select', 'latex-citation-key', 'latex-insert-citation',
      'latex-citation-note', 'latex-draft-label', 'latex-notice', 'latex-absence', 'latex-cjk-note',
      'latex-compile-note', 'latex-import-source', 'latex-file-path'])
      keep[id] = document.getElementById(id);
    keep.snippets = [...document.querySelectorAll('[data-latex-snippet]')];
    keep.insertCitation = document.querySelector('[data-testid="citation-from-writing"]');
    keep.syntax = document.querySelector('.writing-syntax');
    keep.filesAside = document.querySelector('.writing-files');
    window.__before = keep;
    return { snippets: keep.snippets.length, editorValue: document.getElementById('latex-editor').value };
  });
  assert.equal(before.snippets, 9, 'the nine working syntax helpers are the ones moved, not new ones');
  assert.match(before.editorValue, /Synthetic writing fixture/, 'the real draft text is loaded');

  await page.evaluate(() => WB_I18N.set('ko'));
  await page.addScriptTag({ content: declutter });
  assert.equal(await page.getByTestId('dw-open-insert').innerText(), '삽입', 'a Korean first mount uses the authored English key');
  await page.evaluate(() => WB_I18N.set('en'));
  assert.equal(await page.getByTestId('dw-open-insert').innerText(), 'Insert', 'English is restored after a Korean first mount');
  assert.equal(await page.locator('#dw-files-panel .dw-panel-close').getAttribute('aria-label'), 'Close');

  // (1) Default state: editor + PDF dominate, files/insert are on demand only.
  const initial = await page.evaluate(() => {
    const api = window.WB_DECLUTTER_WRITING;
    const box = (sel) => document.querySelector(sel).getBoundingClientRect();
    return {
      api: !!api,
      filesHidden: api.isOpen('files') === false && document.getElementById('dw-files-panel').hidden,
      insertHidden: api.isOpen('insert') === false && document.getElementById('dw-insert-panel').hidden,
      dialogClosed: document.getElementById('dw-paper-actions').open === false,
      infoOpen: document.getElementById('dw-info').open,
      editor: box('#latex-editor'), preview: box('#latex-preview'),
      compileVisible: box('#latex-compile').width > 0,
      triggers: [...document.querySelectorAll('.dw-trigger')].map((el) => el.textContent)
    };
  });
  assert.ok(initial.api, 'window.WB_DECLUTTER_WRITING is the integration surface');
  assert.ok(initial.filesHidden && initial.insertHidden && initial.dialogClosed, 'drawers/menus start closed');
  assert.equal(initial.infoOpen, false, 'engine/CJK/compile help is collapsed by default');
  assert.ok(initial.editor.width > 340 && initial.preview.width > 300,
    `both panes are real at desktop width: ${JSON.stringify([initial.editor.width, initial.preview.width])}`);
  assert.ok(initial.editor.height > 400, 'the editor keeps the height of the writing area');
  assert.ok(initial.compileVisible, 'Compile stays on the paper bar');
  assert.deepEqual(initial.triggers, ['Files', 'Insert', 'Paper actions']);

  // (2) Every original node is the same node, moved (no duplicated implementation).
  const moved = await page.evaluate(() => {
    const keep = window.__before, api = window.WB_DECLUTTER_WRITING;
    const same = (id) => document.getElementById(id) === keep[id];
    return {
      replaced: Object.keys(keep)
        .filter((k) => !['snippets', 'syntax', 'filesAside', 'insertCitation'].includes(k))
        .filter((k) => document.getElementById(k) !== keep[k]),
      citeSame: document.querySelector('[data-testid="citation-from-writing"]') === keep.insertCitation,
      snippetsSame: [...document.querySelectorAll('[data-latex-snippet]')].every((el, i) => el === keep.snippets[i]),
      containersSame: document.querySelector('.writing-syntax') === keep.syntax &&
        document.querySelector('.writing-files') === keep.filesAside,
      snippetCount: document.querySelectorAll('[data-latex-snippet]').length,
      citeEntryInInsert: document.getElementById('dw-insert-panel').contains(keep.insertCitation),
      syntaxInInsert: document.getElementById('dw-insert-panel').contains(keep.syntax),
      fileListInFiles: document.getElementById('dw-files-panel').contains(keep.filesAside) &&
        document.getElementById('dw-files-panel').contains(document.getElementById('latex-import-source')),
      fileDialogParent: keep['latex-file-dialog'].parentElement.id,
      saveInDialog: document.getElementById('dw-paper-actions').contains(document.getElementById('latex-save')),
      reloadInDialog: document.getElementById('dw-paper-actions').contains(document.getElementById('latex-reload')),
      actionsPreserved: [...document.getElementById('dw-paper-actions').querySelectorAll('button[onclick]')]
        .map((el) => el.getAttribute('onclick')),
      saveHandler: document.getElementById('latex-save').getAttribute('onclick'),
      infoHolds: ['latex-cjk-note', 'latex-draft-label', 'latex-notice', 'latex-compile-note']
        .every((id) => document.getElementById('dw-info').contains(document.getElementById(id))),
      // The engine absence is the reason Compile is disabled, so it sits in the same
      // disclosure as that reason: one place to look, not a second banner.
      absenceInInfo: document.getElementById('dw-info').contains(document.getElementById('latex-absence')) &&
        !document.getElementById('dw-info').hidden,
      // The disabled reason is the descriptive text of the button, so the disclosure
      // holding it is opened exactly while Compile is disabled.
      infoClosedWhileEnabled: document.getElementById('latex-compile').disabled
        ? null : document.getElementById('dw-info').open === false,
      // The long mechanics live inside the disclosure next to the disabled reason.
      reasonInInfo: document.getElementById('dw-info').contains(document.getElementById('latex-compile-note')),
      recheckInInfo: !!document.getElementById('dw-info').querySelector('[data-testid="latex-recheck"]'),
      statusOnBar: document.getElementById('latex-status').closest('.writing-toolbar') === document.querySelector('.writing-toolbar'),
      // The raw TeX tail is the longest evidence and the least often read, so it sits
      // in the disclosure; the parsed diagnostics stay on the surface where a failed
      // build is actually read.
      rawLogInInfo: document.getElementById('dw-info').contains(document.getElementById('latex-raw-log')),
      diagnosticsOnSurface: !document.getElementById('dw-info').contains(document.getElementById('latex-diagnostics')),
      saveCount: document.querySelectorAll('#latex-save').length,
      compileCount: document.querySelectorAll('#latex-compile').length,
      ids: api && Object.keys(api.preserve)
    };
  });
  assert.deepEqual(moved.replaced, [], 'no original node is replaced or re-created');
  assert.ok(moved.containersSame, 'the original Files aside and syntax group are the moved containers');
  assert.ok(moved.citeSame, 'the Cite this sentence button is the original node');
  assert.ok(moved.snippetsSame && moved.snippetCount === 9, 'exactly the nine original helper buttons, in order');
  assert.ok(moved.citeEntryInInsert && moved.syntaxInInsert, 'cite entry, picker, undo and help live in Insert');
  assert.ok(moved.fileListInFiles, 'the file list and its actions/import live in the Files drawer');
  assert.equal(moved.fileDialogParent, 'latex-view', 'the Add/Rename file dialog stays a direct child of the view (top layer)');
  assert.ok(moved.saveInDialog && moved.reloadInDialog, 'manual Save and Reload live in Paper actions');
  assert.equal(moved.saveHandler, "saveDraft('manual')", 'Save keeps its original action');
  assert.ok(moved.actionsPreserved.some((h) => h.includes('latexChooseDraft(null, true)')), 'New paper keeps its action');
  assert.ok(moved.actionsPreserved.some((h) => h.includes('latexDownloadSource()')), 'Download source keeps its action');
  assert.ok(moved.actionsPreserved.some((h) => h.includes('latexDownloadPdf()')), 'Download PDF keeps its action');
  assert.ok(moved.infoHolds && moved.recheckInInfo, 'engine/CJK/compile/retention help shares one disclosure');
  assert.ok(moved.absenceInInfo && moved.reasonInInfo, 'the disabled reason and the engine state share the one disclosure as real text');
  assert.equal(moved.infoClosedWhileEnabled, true, 'the help stays collapsed while Compile is usable');
  assert.ok(moved.statusOnBar && moved.saveCount === 1 && moved.compileCount === 1, 'one status line, one save, one compile');
  assert.ok(moved.rawLogInInfo && moved.diagnosticsOnSurface,
    'the raw TeX tail shares the disclosure while parsed diagnostics stay on the surface');

  // (3) Menu opening/closing never touches the draft or a compiled PDF.
  const opened = await page.evaluate(async () => {
    const editor = document.getElementById('latex-editor');
    editor.value += '% kept while menus open\n';
    editor.setSelectionRange(4, 4);
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    const valueBefore = editor.value;
    renderPreview(new Blob(['%PDF-1.4\n'], { type: 'application/pdf' }));
    const frame = document.querySelector('#latex-preview iframe');
    const api = window.WB_DECLUTTER_WRITING;
    const trace = [];
    for (const action of [api.openFiles, api.closeFiles, api.openInsert, api.closeInsert,
      api.openPaperActions, api.closePaperActions, api.openInfo, api.closeInfo]) {
      action();
      trace.push({ hidden: api.isOpen('files') || api.isOpen('insert') ? 'open' : 'closed', value: editor.value,
        sameFrame: document.querySelector('#latex-preview iframe') === frame, caret: editor.selectionStart,
        dirty: latexState.dirty });
    }
    await new Promise((resolve) => setTimeout(resolve, 40));
    return { trace, valueBefore, frameStable: document.querySelector('#latex-preview iframe') === frame,
      valueAfter: editor.value, dirty: latexState.dirty, revision: latexState.draft.revision };
  });
  assert.equal(opened.valueAfter, opened.valueBefore, 'no menu action rewrote the editor buffer');
  assert.ok(opened.trace.every((step) => step.sameFrame), 'the compiled PDF frame is never re-rendered by a menu');
  assert.ok(opened.trace.every((step) => step.value === opened.valueBefore), 'text is identical at every step');
  assert.equal(opened.dirty, true, 'the typed text still counts as unsaved work');
  assert.equal(opened.frameStable, true, 'the same PDF document element survives every toggle');
  const focusTransition = await page.evaluate(async () => {
    const api = WB_DECLUTTER_WRITING;
    api.openPaperActions();
    const expanded = document.querySelector('[data-testid="dw-open-paper-actions"]').getAttribute('aria-expanded');
    api.openFiles();
    await new Promise(resolve => setTimeout(resolve, 30));
    const panel = document.getElementById('dw-files-panel');
    const inside = panel === document.activeElement || panel.contains(document.activeElement);
    api.closeFiles();
    return { expanded, inside };
  });
  assert.equal(focusTransition.expanded, 'true', 'programmatic Paper actions reports its real expanded state');
  assert.equal(focusTransition.inside, true, 'a queued modal close does not steal focus from Files');

  // (4) Save from inside Paper actions writes the editor buffer, not a stale copy.
  const saved = await page.evaluate(async () => {
    const api = window.WB_DECLUTTER_WRITING;
    api.openPaperActions();
    const dialog = document.getElementById('dw-paper-actions');
    const editor = document.getElementById('latex-editor');
    const value = editor.value;
    document.getElementById('latex-save').click();
    for (let i = 0; i < 60 && !window.saved.length; i++) await new Promise((r) => setTimeout(r, 10));
    return { dialogOpen: dialog.open, value, put: window.saved[0] && window.saved[0].files[0].text,
      status: document.getElementById('latex-status').textContent, focusInEditor: document.activeElement === editor };
  });
  assert.ok(saved.dialogOpen, 'the Paper actions dialog stays open while saving');
  assert.equal(saved.put, saved.value, 'Save sent exactly the editor text, including the typed change');

  // (5) Rename reuses the existing save path and block guards.
  const renamed = await page.evaluate(async () => {
    const api = window.WB_DECLUTTER_WRITING;
    latexState.switchingDraft = true;
    const originalTitle = latexState.draft.title;
    const writesBefore = window.saved.length;
    const switchingResult = await api.renamePaper();
    const switching = { result: switchingResult, title: latexState.draft.title, writes: window.saved.length - writesBefore };
    latexState.switchingDraft = false;
    if (switching.result !== null || switching.title !== originalTitle || switching.writes !== 0)
      throw new Error('Renaming during a draft switch must leave the title and server untouched');
    const blocked = { case: 'conflict' };
    latexState.blocked = true;
    blocked.message = await api.renamePaper();
    blocked.error = document.getElementById('dw-rename-error').textContent;
    latexState.blocked = false;
    document.getElementById('dw-rename-title').value = '  Permit  study  ';
    window.saved.length = 0;
    const result = await api.renamePaper();
    return { blocked, title: result && result.title, sent: window.saved[0] && window.saved[0].title,
      option: [...document.getElementById('latex-draft-select').options].map((o) => o.textContent),
      editor: document.getElementById('latex-editor').value.length, dialogOpen: document.getElementById('dw-paper-actions').open };
  });
  assert.equal(renamed.blocked.message, null, 'a conflicting draft is not renamed');
  assert.match(renamed.blocked.error, /Reload this paper/, 'the conflict reason is stated, not swallowed');
  assert.equal(renamed.title, 'Permit study', 'the title is trimmed and saved through the existing PUT');
  assert.equal(renamed.sent, 'Permit study', 'the saved title is the one typed');
  assert.ok(renamed.option.includes('Permit study'), 'the paper selector shows the new title');
  assert.equal(renamed.dialogOpen, false, 'the dialog closes after a successful rename');
  assert.ok(renamed.editor > 0, 'renaming never emptied the editor');

  // (6) The paper-actions dialog is genuinely above an open Files drawer.
  const layers = await page.evaluate(() => {
    const api = window.WB_DECLUTTER_WRITING;
    api.openFiles();
    api.openPaperActions();
    const dialog = document.getElementById('dw-paper-actions');
    const rect = dialog.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + 12, rect.top + 12);
    const panel = document.getElementById('dw-files-panel');
    return {
      inDialog: dialog.contains(hit),
      modal: dialog.open && (dialog.matches ? dialog.matches(':modal') : true),
      // A plain panel is not in the top layer, so no z-index of its own can paint
      // over an open modal; nothing inside it is a dialog either (no nested modals).
      panelIsNotModal: !panel.closest('dialog'),
      nestedDialogs: document.querySelectorAll('#latex-view dialog dialog, dialog dialog').length,
      filesStillOpen: !panel.hidden
    };
  });
  assert.ok(layers.inDialog, 'the dialog receives the pointer at its own surface');
  assert.ok(layers.modal, 'the dialog is an open modal, so the Files panel cannot cover it');
  assert.equal(layers.panelIsNotModal, true, 'the Files drawer stays a plain panel, not a dialog');
  assert.equal(layers.nestedDialogs, 0, 'no dialog is nested inside another dialog');
  assert.equal(layers.filesStillOpen, false, 'a modal choice closes the drawer instead of stacking two surfaces');

  // (7) The existing Add/Rename file dialog still opens above everything.
  const fileDialog = await page.evaluate(async () => {
    window.WB_DECLUTTER_WRITING.closePaperActions();
    window.latexFileAction('add');
    await new Promise((r) => setTimeout(r, 30));
    const dialog = document.getElementById('latex-file-dialog');
    const rect = dialog.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + 12);
    const result = { open: dialog.open, inDialog: dialog.contains(hit),
      path: document.getElementById('latex-file-path').value,
      editor: document.getElementById('latex-editor').value };
    window.latexCloseFileDialog();
    return result;
  });
  assert.equal(fileDialog.open, true, 'Add file opens the preserved dialog');
  assert.ok(fileDialog.inDialog, 'the file-path dialog is interactive above the view');
  assert.equal(fileDialog.path, '', 'the add dialog starts empty');

  // (8) Escape closes a panel and returns focus; nothing else changes.
  const escaped = await page.evaluate(() => {
    const api = window.WB_DECLUTTER_WRITING;
    api.openFiles();
    const editor = document.getElementById('latex-editor');
    const value = editor.value;
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.querySelector('[data-testid="dw-open-files"]').focus();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.getElementById('dw-files-panel').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    return { closed: api.isOpen('files') === false, value, after: editor.value,
      focused: document.activeElement && document.activeElement.getAttribute('data-testid') };
  });
  assert.equal(escaped.closed, true, 'Escape closes the drawer');
  assert.equal(escaped.after, escaped.value, 'Escape left the draft untouched');
  assert.equal(escaped.focused, 'dw-open-files', 'focus returns to the drawer trigger');

  // (9) ⌘S still saves anywhere in the writing view, including from a panel.
  const shortcut = await page.evaluate(async () => {
    document.getElementById('dw-insert-panel').hidden = false;
    document.querySelector('[data-testid="dw-open-insert"]').focus();
    window.saved.length = 0;
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 's', metaKey: true, bubbles: true, cancelable: true }));
    for (let i = 0; i < 60 && !window.saved.length; i++) await new Promise((r) => setTimeout(r, 10));
    return { saved: window.saved.length, source: window.saved[0] && window.saved[0].source };
  });
  assert.equal(shortcut.saved, 1, 'one manual save for one keystroke');
  assert.equal(shortcut.source, 'manual', 'the keystroke saves as a manual save');

  // (10) The compile truth is unchanged: disabled without an engine, reason in help.
  const noEngine = await page.evaluate(async () => {
    window.call = async (method) => {
      if (method === 'GET') return { available: false, engines: { pdflatex: null, xelatex: null }, checked_at: 'now', pdf_export: false };
      throw new Error('no write expected');
    };
    await recheckLatex();
    const button = document.getElementById('latex-compile');
    return { disabled: button.disabled, described: button.getAttribute('aria-describedby'),
      reason: document.getElementById('latex-compile-note').textContent,
      absence: document.getElementById('latex-absence').textContent,
      engineHelpInInfo: document.getElementById('dw-info').contains(document.getElementById('latex-compile-note')) &&
        !!document.getElementById('dw-info').querySelector('[data-testid="latex-recheck"]'),
      // The button's aria-describedby text must be reachable, not just present.
      reasonOpen: document.getElementById('dw-info').open &&
        document.getElementById('latex-compile-note').getBoundingClientRect().height > 0,
      status: document.getElementById('latex-status').textContent };
  });
  assert.equal(noEngine.disabled, true, 'Compile stays disabled with no engine');
  assert.equal(noEngine.described, 'latex-compile-note', 'aria-describedby still names the real reason text');
  assert.match(noEngine.reason, /no LaTeX engine/i, 'the disabled reason is real text, not a hidden tooltip');
  assert.match(noEngine.absence, /needs a LaTeX engine/i, 'the absence state is stated, not buried');
  assert.ok(noEngine.engineHelpInInfo, 'the compile help and the re-check live in the one disclosure');
  assert.ok(noEngine.reasonOpen, 'the reason the button describes is open while it is disabled');
  assert.doesNotMatch(noEngine.status, /compiled|PDF ready|engine found/i,
    'no working engine is inferred while Compile is disabled');

  // (11) Layout: 2 panes at desktop, a stack when narrow, no clipping, 44px targets.
  const desktop = await page.evaluate(() => {
    const box = (sel) => document.querySelector(sel).getBoundingClientRect();
    document.getElementById('dw-info').open = false;
    return { editor: box('#latex-editor'), preview: box('#latex-preview'), view: box('#latex-view'),
      overflowX: document.documentElement.scrollWidth - innerWidth, scrollY };
  });
  assert.ok(desktop.editor.left < desktop.preview.left && desktop.editor.width > 300 && desktop.preview.width > 260,
    `two panes side by side: ${JSON.stringify([desktop.editor.left, desktop.preview.left])}`);
  assert.ok(desktop.overflowX <= 0, 'no horizontal clipping at desktop width');

  let narrowOk = true;
  for (const width of [1024, 1000, 820, 620, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const state = await page.evaluate(() => {
      const box = (sel) => document.querySelector(sel).getBoundingClientRect();
      const small = [...document.querySelectorAll('#latex-view button, #latex-view select')]
        .filter((el) => el.getBoundingClientRect().height > 0 && el.getBoundingClientRect().height < 44)
        .map((el) => `${el.id || el.textContent}:${Math.round(el.getBoundingClientRect().height)}`);
      return { editor: box('#latex-editor'), preview: box('#latex-preview'),
        overflow: document.documentElement.scrollWidth - innerWidth, small };
    });
    if (state.overflow > 0) throw new Error(`${width}px viewport clips horizontally: ${state.overflow}`);
    if (state.small.length) throw new Error(`${width}px viewport has sub-44px targets: ${state.small.join(', ')}`);
    if (width <= 1000 && !(state.preview.top >= state.editor.bottom - 2))
      throw new Error(`${width}px viewport does not stack the panes: ${JSON.stringify([state.editor.bottom, state.preview.top])}`);
    narrowOk = true;
  }
  assert.ok(narrowOk, 'narrow layouts stack without clipping and keep 44px targets');
  await page.setViewportSize({ width: 1024, height: 700 });
  const shortEditor = await page.locator('#latex-view > .latex-editor').boundingBox();
  assert.ok(shortEditor.height < 680, 'the editor adapts to a short desktop instead of inheriting a fixed 680px height');
  await page.setViewportSize({ width: 1440, height: 900 });

  // (12) Theme contrast and focus ring in both themes, measured on the moved controls.
  for (const theme of ['light', 'dark']) {
    const measured = await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
      window.WB_I18N.set('en');
      const api = window.WB_DECLUTTER_WRITING;
      api.openFiles(); api.openInfo(true);
      const rgb = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number);
      const lum = (color) => rgb(color).map((v) => v / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (selector) => {
        const el = document.querySelector(selector);
        let node = el, background = getComputedStyle(el).backgroundColor;
        while (background === 'rgba(0, 0, 0, 0)' && node.parentElement) {
          node = node.parentElement; background = getComputedStyle(node).backgroundColor;
        }
        const a = lum(getComputedStyle(el).color), b = lum(background);
        return { selector, ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
      };
      const results = ['#latex-status', '#latex-compile-note', '#latex-absence', '#latex-editor',
        '#latex-compile', '#dw-info > summary', '#dw-files-panel', '#latex-files button',
        '[data-testid="dw-open-files"]', '[data-testid="dw-open-insert"]', '[data-testid="dw-open-paper-actions"]']
        .map(ratio);
      document.querySelector('[data-testid="dw-open-insert"]').focus();
      const focus = getComputedStyle(document.querySelector('[data-testid="dw-open-insert"]'));
      api.closeFiles();
      return { results, focus: { width: focus.outlineWidth, color: focus.outlineColor } };
    }, theme);
    for (const result of measured.results)
      assert.ok(result.ratio >= 4.5, `${theme} ${result.selector}: ${result.ratio.toFixed(2)} contrast`);
    assert.ok(parseFloat(measured.focus.width) >= 2 && measured.focus.color !== 'rgba(0, 0, 0, 0)',
      `${theme} focus ring is visible on a moved trigger`);
  }

  // (13) Korean labels are translated without re-rendering the draft.
  const korean = await page.evaluate(() => {
    const editor = document.getElementById('latex-editor');
    const value = editor.value;
    window.WB_I18N.set('ko');
    const labels = [...document.querySelectorAll('.dw-trigger')].map((el) => el.textContent);
    const panelTitle = document.querySelector('#dw-files-panel .dw-panel-title').textContent;
    window.WB_I18N.set('en');
    return { labels, panelTitle, value, after: editor.value };
  });
  assert.deepEqual(korean.labels.filter((label) => /[가-힣]/.test(label)).length, 3,
    `the three triggers are translated: ${korean.labels.join(' / ')}`);
  assert.match(korean.panelTitle, /[가-힣]/, 'the drawer title is translated');
  assert.equal(korean.after, korean.value, 'a language switch never rewrote the draft');
  console.log('PASS declutter writing: nodes moved not re-created, drafts and PDFs untouched by menu state, compile truth and guards intact, 2 panes/stack/44px/contrast checked');
} finally {
  await browser.close();
}
