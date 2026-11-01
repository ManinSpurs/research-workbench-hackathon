// Writing-view lifecycle: what survives a hidden view, a real project switch, a
// language change, ⌘S from outside the textarea, and a build that finished too late.
// Real frontend/latex.js + real frontend/i18n.js, transport and DOM doubled only.
// No browser, no server, no network, no TeX engine, no PDF bytes.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {
    id, value: '', textContent: '', innerHTML: '', hidden: false, disabled: false,
    open: false, isConnected: true, children: [], listeners: {}, attrs: {}, title: '',
    selectionStart: 0, selectionEnd: 0,
    classList: {
      set: new Set(),
      add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); },
    },
    addEventListener(t, f) { this.listeners[t] = f; },
    removeEventListener(t) { delete this.listeners[t]; },
    setAttribute(n, v) { this.attrs[n] = String(v); }, getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; },
    removeAttribute(n) { delete this.attrs[n]; },
    appendChild(k) { this.children.push(k); return k; }, replaceChildren(...k) { this.children = k; },
    remove() { this.removed = (this.removed || 0) + 1; },
    focus() { this.focused = true; }, select() {},
    showModal() { this.open = true; }, close() { this.open = false; },
    setRangeText(text, a, b) { this.value = this.value.slice(0, a) + text + this.value.slice(b); },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
    click() {},
  });
  return nodes.get(id);
};

let blobs = { created: 0, revoked: [] };
let sent = [], lastError = '', created = 0;
const iframes = [];
const documentHandlers = {};
const storage = new Map();

const makeEl = (tag) => {
  const el = node(`created-${++created}`);
  el.tag = tag;
  el.dataset = {};
  el.remove = () => { iframes.splice(iframes.indexOf(el), 1); };
  if (tag === 'iframe') { el.setAttribute = (n, v) => { el.attrs[n] = String(v); if (n === 'src') el.src = v; }; iframes.push(el); }
  return el;
};

const win = { addEventListener(t, f) { win.handlers = win.handlers || {}; win.handlers[t] = f; } };
// One workspace section owns the active mode (writing / library / citation). The
// writing view's own `hidden` class is not enough to decide what the researcher sees.
const section = {
  classList: {
    set: new Set(['writing-mode']),
    add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); },
  },
};
const context = vm.createContext({
  console, setTimeout, clearTimeout, Date, encodeURIComponent, Blob,
  window: win,
  localStorage: { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k) },
  S: { project: 'p1' },
  $: (id) => node(id),
  confirm: () => true,
  showError: (m) => { lastError = m; },
  controls: () => {},
  document: {
    readyState: 'complete',
    activeElement: null,
    getElementById: (id) => node(id),
    addEventListener(t, f) { documentHandlers[t] = f; },
    querySelectorAll(sel) {
      if (sel === '.latex-preview iframe') return iframes;
      if (sel === '[data-i18n]') return [];
      return [];
    },
    querySelector(sel) { return sel === 'main section' ? section : null; },
    createElement: makeEl,
  },
  URL: {
    createObjectURL: () => { blobs.created++; return `blob:url-${blobs.created}`; },
    revokeObjectURL: (u) => { blobs.revoked.push(u); },
  },
  call: async (m, p, b) => {
    sent.push({ method: m, path: p, body: b });
    if (context.route) return context.route(m, p, b);
    throw new Error(`unexpected request ${m} ${p}`);
  },
});

vm.runInContext(await readFile(new URL('../frontend/i18n.js', import.meta.url), 'utf8'), context);
vm.runInContext(await readFile(new URL('../frontend/latex.js', import.meta.url), 'utf8'), context);
const run = (code) => vm.runInContext(code, context);

const draft = (rev = 0) => ({
  draft_id: 'd1', project_id: 'p1', title: 'Paper', engine: 'pdftex', main_file: 'main.tex', revision: rev,
  files: [{ path: 'main.tex', text: 'main body' }, { path: 'refs.bib', text: '@article{Real2026, title={A}}' }],
});
const caps = { available: true, engines: { pdflatex: '/usr/bin/pdflatex' }, checked_at: 'now', pdf_export: true };
const putOk = (body) => ({ ...draft(body.expected_revision + 1), files: body.files });

// Fixture: an open draft with unsaved text, a shown PDF for the same revision.
function openFixture({ dirty = true, rev = 0 } = {}) {
  run(`latexState={project:'p1',draft:${JSON.stringify({ ...draft(rev), files: [{ path: 'main.tex', text: 'stored main' }, { path: 'refs.bib', text: '@article{Real2026, title={A}}' }] })},
    capabilities:${JSON.stringify(caps)},drafts:[${JSON.stringify(draft(rev))}],activeFile:'main.tex',
    dirty:${dirty},saving:false,blocked:false,serial:0,compiling:false,compiledRevision:${dirty ? 'null' : rev}};`);
  node('latex-editor').value = 'stored main';
  node('latex-editor').selectionStart = node('latex-editor').selectionEnd = 5;
  node('latex-view').classList.remove('hidden');
  section.classList.add('writing-mode');
  sent = []; lastError = ''; blobs = { created: 0, revoked: [] }; iframes.length = 0;
}

// (1) Hiding the view for a question change is not a project switch: the buffer, the
// dirty flag, the caret and the pending autosave all survive, and the autosave fires.
openFixture({ dirty: true });
node('latex-editor').value = 'text that is not saved yet';
node('latex-editor').selectionStart = node('latex-editor').selectionEnd = 7;
node('latex-draft-label').textContent = '';
context.route = (m, p, b) => putOk(b);
run('scheduleLatexAutosave(); clearLatex()');
assert.equal(run('latexState === null'), false, 'clearLatex keeps the held draft');
assert.equal(run('latexState.dirty'), true, 'clearLatex keeps the unsaved flag');
assert.equal(node('latex-editor').value, 'text that is not saved yet', 'clearLatex keeps the editor text');
assert.equal(node('latex-editor').selectionStart, 7, 'clearLatex keeps the caret');
assert.equal(node('latex-view').classList.contains('hidden'), true, 'the view is hidden');
assert.equal(run('latexTimer !== null'), true, 'the pending autosave timer survives the hidden view');
await new Promise((r) => setTimeout(r, 950));
assert.equal(sent.length, 1, 'and it really saves while the view is hidden');
assert.equal(sent[0].body.files[0].text, 'text that is not saved yet', 'the kept autosave saves the researcher text');

// (2) A real project switch flushes first, then drops the draft; a visible editor
// never shows one project's text under another project's title.
openFixture({ dirty: true });
node('latex-editor').value = 'flush before switching';
context.route = (m, p, b) => putOk(b);
sent = [];
assert.equal(await run('latexBeforeProjectSwitch()'), true, 'the hook reports a safe flush');
assert.deepEqual(sent.map((r) => r.method), ['PUT'], 'exactly one flush save');
assert.equal(sent[0].body.files[0].text, 'flush before switching', 'the flushed text is current');
assert.deepEqual(Array.from(sent[0].body.files, (f) => f.path), ['main.tex', 'refs.bib'], 'no extra file is dropped by the flush');
assert.equal(run('latexState === null'), true, 'after a safe flush the draft is dropped');
assert.equal(node('latex-editor').value, '', 'the editor is emptied');
assert.equal(node('latex-view').classList.contains('hidden'), true, 'the writing view is hidden');

// (3) A failed or conflicted flush refuses the switch: app.js can keep S.project.
for (const [label, route, blocked] of [
  ['a server failure', () => { throw new Error('HTTP 500'); }, false],
  ['a revision conflict', () => { throw new Error('409 revision conflict; reload'); }, false],
  ['an unresolved conflict', (m, p, b) => putOk(b), true],
]) {
  openFixture({ dirty: true });
  node('latex-editor').value = 'must not be dropped';
  context.route = route;
  if (blocked) run('latexState.blocked = true');
  sent = [];
  assert.equal(await run('latexBeforeProjectSwitch()'), false, `${label} cancels the switch`);
  assert.notEqual(run('latexState === null'), true, `${label} keeps the held draft`);
  assert.equal(node('latex-editor').value, 'must not be dropped', `${label} keeps the researcher text`);
  assert.ok(node('latex-status').textContent.length > 0, `${label} explains itself in the status line`);
}
// A clean draft switches without any write at all.
openFixture({ dirty: false });
sent = [];
assert.equal(await run('latexBeforeProjectSwitch()'), true, 'a clean draft needs no write');
assert.equal(sent.length, 0, 'and issues no request');

// (4) A language change redraws only the chrome: no draft reload, no lost text, no
// lost caret, no lost selection, no lost compiled revision, no replaced PDF.
openFixture({ dirty: false });
run("latexState.compiledRevision = 0; renderPreview(new Blob(['%PDF']));");
const urlBefore = run('latexPdfUrl');
node('latex-editor').value = 'unsaved words in the editor';
run('latexState.dirty = true');
node('latex-editor').selectionStart = 9; node('latex-editor').selectionEnd = 14;
node('latex-draft-label').textContent = 'english label';
context.route = () => { throw new Error('a language change must not fetch anything'); };
sent = [];
run('window.WB_I18N.set("ko")');
assert.equal(run('window.WB_I18N.get()'), 'ko', 'the real i18n layer switched');
assert.equal(sent.length, 0, 'a language change reloads nothing');
assert.equal(run('latexState.dirty'), true, 'the dirty flag survives');
assert.equal(node('latex-editor').value, 'unsaved words in the editor', 'the unsaved text survives');
assert.equal(node('latex-editor').selectionStart, 9, 'the caret survives');
assert.equal(node('latex-editor').selectionEnd, 14, 'the selection survives');
assert.equal(run('latexState.activeFile'), 'main.tex', 'the selected file survives');
assert.equal(run('latexState.compiledRevision'), 0, 'the compiled revision survives');
assert.equal(run('latexPdfUrl'), urlBefore, 'the shown PDF is the same blob, not refetched');
assert.deepEqual(blobs.revoked, [], 'and it was not revoked');
assert.match(node('latex-draft-label').textContent, /저장하지 않은 변경 사항/, 'the dynamic label is translated in place');
run('window.WB_I18N.set("en")');
assert.equal(node('latex-editor').value, 'unsaved words in the editor', 'switching back keeps the text too');
assert.equal(run('latexState.dirty'), true, 'and the dirty flag');

// (5) Reload refuses while a save is in flight, instead of restoring the text the
// in-flight save is about to replace.
openFixture({ dirty: false });
run('latexState.saving = true');
sent = [];
assert.equal(await run('reloadDraft()'), null, 'reload during a save returns nothing');
assert.equal(sent.filter((r) => r.method === 'GET').length, 0, 'and issues no read');
assert.match(node('latex-status').textContent, /Reload refused while a save is in progress/);

// (6) An explicit save supersedes a queued autosave: one revision, not two.
openFixture({ dirty: true });
node('latex-editor').value = 'save me now';
context.route = (m, p, b) => putOk(b);
sent = [];
run('scheduleLatexAutosave()');
assert.equal(run('latexTimer !== null'), true, 'an autosave is queued');
await run("saveDraft('manual')");
assert.equal(sent.length, 1, 'the explicit save sends exactly one PUT');
assert.equal(run('latexTimer'), null, 'and the queued autosave timer was cleared');
await new Promise((r) => setTimeout(r, 950));
assert.equal(sent.length, 1, 'no redundant second revision arrives later');

// (7) ⌘S / Ctrl+S works from a toolbar/file control, not only from the textarea.
openFixture({ dirty: true });
node('latex-editor').value = 'saved from the toolbar';
context.route = (m, p, b) => putOk(b);
sent = [];
assert.equal(typeof documentHandlers.keydown, 'function', 'a scoped document handler exists');
let prevented = 0;
documentHandlers.keydown({ metaKey: true, key: 's', preventDefault() { prevented++; } });
await new Promise((r) => setTimeout(r, 10));
assert.equal(prevented, 1, 'the browser default Save dialog is suppressed');
assert.equal(sent.length, 1, 'exactly one save runs from the toolbar context');
assert.equal(sent[0].body.files[0].text, 'saved from the toolbar', 'and it saves the current text');
// The same key with the writing view hidden does nothing at all.
run("$('latex-view').classList.add('hidden')");
node('latex-editor').value = 'typed elsewhere';
run('latexState.dirty = true');
sent = [];
documentHandlers.keydown({ ctrlKey: true, key: 's', preventDefault() { prevented++; } });
await new Promise((r) => setTimeout(r, 10));
assert.equal(sent.length, 0, 'no save hijack when the writing view is hidden');
assert.equal(prevented, 1, 'and no default suppression outside the writing view');
run("$('latex-view').classList.remove('hidden')");

// (8) A build that finished after the draft moved on reports the revision it built
// and shows nothing from it: no PDF, no diagnostics pinned to the current lines,
// and only a bounded raw log is kept.
openFixture({ dirty: false, rev: 5 });
const bigLog = 'X'.repeat(9000);
context.route = (m, p, b) => ({ status: 'ok', revision: 4, pdf_url: '/drafts/d1/pdf?revision=4',
  diagnostics: [{ severity: 'error', line: 2, message: 'Undefined control sequence' }], log_tail: bigLog });
assert.equal(await run('compileDraft()'), null, 'the stale result is not applied');
assert.match(node('latex-status').textContent, /Not shown: the draft changed during compilation \(compiled revision 4\)/);
assert.equal(node('latex-status').getAttribute('data-state'), 'fail');
assert.equal(blobs.created, 0, 'no PDF is shown for a revision that is no longer current');
assert.equal(node('latex-diagnostics').children.length, 0, 'no diagnostic points at a line of the newer source');
assert.equal(node('latex-diagnostics').hidden, true, 'the diagnostics list is hidden');
assert.ok(node('latex-raw-log').textContent.length <= 9000, 'the kept log is bounded');
assert.ok(node('latex-raw-log').textContent.length > 4000, 'the tail of the log is still there to read');
assert.equal(node('latex-raw-log').hidden, false, 'the bounded log stays visible');
assert.equal(run('latexState.compiledRevision'), null, 'no revision is claimed as compiled');

// (9) The window-close guard asks only while text is unsaved, and never locks a
// clean draft in; it promises nothing async.
assert.equal(typeof win.handlers.beforeunload, 'function', 'a beforeunload guard is registered');
function closeAttempt() {
  const e = { prevented: 0, preventDefault() { this.prevented++; } };
  const ret = win.handlers.beforeunload(e);
  return { ret, prevented: e.prevented };
}
run('latexState.dirty = false; latexState.saving = false');
assert.deepEqual(closeAttempt(), { ret: undefined, prevented: 0 }, 'a clean draft closes without a question');
run('latexState.dirty = true');
assert.deepEqual(closeAttempt(), { ret: '', prevented: 1 }, 'unsaved text asks the standard question');
run('latexState.dirty = false; latexState.saving = true');
assert.equal(closeAttempt().prevented, 1, 'an in-flight save also asks');
run('latexState.saving = false; latexState.draft = null');
assert.equal(closeAttempt().prevented, 0, 'no draft, no question');

// (10) Destructive confirmations name the exact target file.
openFixture({ dirty: false });
run("latexState.activeFile = 'refs.bib'");
assert.equal(node('latexState').textContent, '', 'harness sanity');
const prompts = [];
context.confirm = (m) => { prompts.push(m); return false; };
run("latexFileAction('delete')");
assert.match(prompts[0], /Delete refs\.bib, including its unsaved changes\?/, 'the delete confirmation names the file');
assert.equal(run('latexState.draft.files.length'), 2, 'a declined confirmation deletes nothing');

// (11) The writing markup owns no static i18n binding on the three nodes whose text
// is rendered by script (a static pass would wipe the live status and the iframe).
const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
for (const id of ['latex-draft-label', 'latex-status', 'latex-preview']) {
  const tag = html.match(new RegExp(`<[^>]*id="${id}"[^>]*>`));
  assert.ok(tag, `${id} exists`);
  assert.ok(!/data-i18n(\s|>)/.test(tag[0]), `${id} must not carry a static data-i18n binding`);
}
assert.match(html, /<link rel="stylesheet" href="writing\.css">\s*<link rel="stylesheet" href="project_order\.css">\s*<link rel="stylesheet" href="library_order\.css">/,
  'the ordering stylesheets load after writing.css (style.css stays first)');
assert.ok(html.indexOf('href="style.css"') < html.indexOf('href="writing.css"'), 'the global stylesheet stays first');

// (12) The reverse reload race: a GET reload is in flight, and the researcher presses
// ⌘S, presses Save, presses Compile, or changes project. The pre-reload buffer must
// never be written: no PUT, no compile POST, no unmount. When the GET lands, its text
// wins; when the GET fails, the local buffer stays and never turns clean.
context.confirm = () => true;
openFixture({ dirty: true });
node('latex-editor').value = 'buffer that reload will discard';
run('latexState.dirty = false');  // the reload was confirmed, so no unsaved prompt
let releaseReload;
context.route = (m, p, b) => {
  if (m === 'GET' && p === '/drafts/d1') return new Promise((resolve) => { releaseReload = () => resolve({ ...draft(9), files: [{ path: 'main.tex', text: 'server text after reload' }, { path: 'refs.bib', text: '@article{Real2026, title={A}}' }] }); });
  if (m === 'PUT') return putOk(b);
  if (p === '/drafts/d1/compile') return { status: 'ok', revision: 9, pdf_url: '/drafts/d1/pdf?revision=9', diagnostics: [], log_tail: '' };
  throw new Error(`unexpected request ${m} ${p}`);
};
sent = [];
const reloading = run('reloadDraft()');
await new Promise((r) => setTimeout(r, 10));
assert.equal(run('latexState.reloading'), true, 'the reload flag is set before the GET settles');
assert.equal(node('latex-save').disabled, true, 'Save is disabled while the reload is in flight');
assert.equal(node('latex-reload').disabled, true, 'Reload is disabled while it is in flight');
assert.equal(node('latex-compile').disabled, true, 'Compile is disabled while the reload is in flight');
assert.equal(node('latex-draft-select').disabled, true, 'the paper selector is disabled while the reload is in flight');

// ⌘S from the toolbar during the pending GET.
let preventedWhileReloading = 0;
documentHandlers.keydown({ metaKey: true, key: 's', preventDefault() { preventedWhileReloading++; } });
// The explicit Save button path, the Compile path and the project-switch hook.
assert.equal(await run("saveDraft('manual')"), null, 'an explicit save during a reload writes nothing');
assert.equal(await run('compileDraft()'), null, 'a compile during a reload requests nothing');
assert.equal(await run('latexBeforeProjectSwitch()'), false, 'a project switch during a reload is refused');
assert.equal(run('latexState === null'), false, 'and the project is not unmounted');
assert.equal(node('latex-editor').value, 'buffer that reload will discard', 'the local buffer is untouched');
assert.equal(sent.filter((r) => r.method === 'PUT').length, 0, 'no PUT happened while the reload was pending');
assert.equal(sent.filter((r) => r.path === '/drafts/d1/compile').length, 0, 'no compile POST happened while the reload was pending');
assert.match(node('latex-status').textContent, /a reload is in progress/, 'the refusal is explained, not silent');
assert.equal(sent.filter((r) => r.method === 'GET').length, 1, 'only the reload read was made');

// File-list actions are refused the same way, and they say so instead of doing
// nothing: a reload is about to replace the file list they would edit.
const filesBefore = JSON.stringify(run('latexState.draft.files'));
run("latexSelectFile('refs.bib')");
assert.equal(run('latexState.activeFile'), 'main.tex', 'a file switch during a reload does not move the editor');
assert.equal(node('latex-editor').value, 'buffer that reload will discard', 'and the text stays in place');
assert.match(node('latex-status').textContent, /Not changed: a reload is in progress/, 'the refused file switch is stated');
run("latexFileAction('delete')");
run("latexFileAction('add')");
assert.equal(JSON.stringify(run('latexState.draft.files')), filesBefore, 'no file was added or deleted during the reload');
assert.equal(node('latex-file-dialog').open, false, 'and no file dialog opened on a draft about to be replaced');
assert.equal(sent.length, 1, 'none of those actions sent anything');
// A refused switch leaves a queued autosave alive: it must not be disarmed and lost.
run('latexTimer = setTimeout(() => {}, 5000)');
assert.equal(await run('latexBeforeProjectSwitch()'), false, 'the switch is still refused');
assert.equal(run('latexTimer !== null'), true, 'and the queued autosave was not silently dropped');
run('clearTimeout(latexTimer); latexTimer = null;');
releaseReload();
const reloaded = await reloading;
assert.equal(reloaded.revision, 9, 'the fetched draft is adopted once it lands');
assert.equal(node('latex-editor').value, 'server text after reload', 'and its text replaces the discarded buffer');
assert.equal(run('latexState.reloading'), false, 'the reload flag is cleared');
assert.equal(node('latex-save').disabled, false, 'Save is usable again');
assert.equal(preventedWhileReloading, 1, 'the keyboard save was intercepted once, and still wrote nothing');

// A failed reload keeps the local buffer exactly as it was, and never claims clean.
openFixture({ dirty: true });
node('latex-editor').value = 'text kept across a failed reload';
run('latexState.dirty = true');
context.route = () => { throw new Error('HTTP 500'); };
sent = [];
assert.equal(await run('reloadDraft()'), null, 'a failed reload returns nothing');
assert.equal(node('latex-editor').value, 'text kept across a failed reload', 'a failed reload replaces no text');
assert.equal(run('latexState.dirty'), true, 'a failed reload does not pretend the draft is saved');
assert.equal(run('latexState.reloading'), false, 'the reload flag is cleared after the failure');
assert.match(node('latex-status').textContent, /Reload failed/, 'the failure is stated');
assert.equal(sent.filter((r) => r.method === 'PUT').length, 0, 'and nothing was written');
// The autosave that the reload refused is queued again, so kept text still reaches
// the server once the reload is over instead of waiting for the next keystroke.
assert.equal(run('latexTimer !== null'), true, 'a failed reload re-arms the autosave');
run('clearTimeout(latexTimer); latexTimer = null;');

// Typing while the GET is pending is not silently discarded either.
openFixture({ dirty: false });
node('latex-editor').value = 'server copy';
let releaseTyped;
context.route = (m, p) => new Promise((resolve) => { releaseTyped = () => resolve({ ...draft(3), files: [{ path: 'main.tex', text: 'server copy, newer' }] }); });
const typedReload = run('reloadDraft()');
await new Promise((r) => setTimeout(r, 10));
run("latexState.editVersion = (latexState.editVersion || 0) + 1");
node('latex-editor').value = 'typed while reloading';
releaseTyped();
assert.equal(await typedReload, null, 'the fetched draft is not adopted over newer typing');
assert.equal(node('latex-editor').value, 'typed while reloading', 'the text typed during the reload survives');
assert.match(node('latex-status').textContent, /the editor changed while reloading/, 'and the researcher is told why');

// (13) Library mode takes the workspace by swapping the mode class, leaving the
// writing view's own hidden class unset in that path. ⌘S must not intercept there.
openFixture({ dirty: true });
run("latexState.dirty = true");
node('latex-editor').value = 'library-mode text';
context.route = (m, p, b) => putOk(b);
assert.equal(run('latexIsWritingVisible()'), true, 'writing mode is the active mode in the fixture');
sent = [];
section.classList.remove('writing-mode');
section.classList.add('library-mode');
assert.equal(node('latex-view').classList.contains('hidden'), false, 'the writing view is not merely hidden here');
assert.equal(run('latexIsWritingVisible()'), false, 'writing mode is no longer what the researcher sees');
let preventedInLibrary = 0;
documentHandlers.keydown({ metaKey: true, key: 's', preventDefault() { preventedInLibrary++; } });
await new Promise((r) => setTimeout(r, 20));
assert.equal(preventedInLibrary, 0, 'Library mode keeps its own ⌘S: nothing is suppressed');
assert.equal(sent.length, 0, 'Library mode sends no draft PUT');
// Back to writing mode: the same keystroke saves again.
section.classList.remove('library-mode');
section.classList.add('writing-mode');
documentHandlers.keydown({ metaKey: true, key: 's', preventDefault() { preventedInLibrary++; } });
await new Promise((r) => setTimeout(r, 20));
assert.equal(preventedInLibrary, 1, 'writing mode intercepts the save again');
assert.equal(sent.length, 1, 'and writes exactly once');

// (14) A failed paper change restores the selector, label and editor to the loaded
// paper instead of showing a paper the editor does not hold.
openFixture({ dirty: false });
run(`latexState.drafts = [${JSON.stringify(draft(0))}, {draft_id: 'd2', title: 'Second paper', main_file: 'main.tex', revision: 0, files: []}]; renderLatexFiles();`);
node('latex-draft-select').value = 'd1';
node('latex-editor').value = 'text of paper one';
context.route = () => { throw new Error('HTTP 500'); };
sent = [];
assert.equal(await run("latexChooseDraft('d2')"), undefined, 'a failed change resolves');
assert.equal(node('latex-draft-select').value, 'd1', 'the selector goes back to the loaded paper');
assert.equal(run('latexState.draft.draft_id'), 'd1', 'the held draft is still paper one');
assert.equal(node('latex-editor').value, 'text of paper one', 'the editor still shows paper one text');
assert.match(node('latex-draft-label').textContent, /Paper/, 'the label still describes the loaded paper');
assert.match(node('latex-status').textContent, /Not opened: HTTP 500/, 'the failure is stated plainly');
assert.equal(run('latexState.switchingDraft'), false, 'the busy flag is cleared on failure');
// A dirty draft whose save fails before the switch also restores the selector.
openFixture({ dirty: true });
run(`latexState.drafts = [${JSON.stringify(draft(0))}, {draft_id: 'd2', title: 'Second paper', main_file: 'main.tex', revision: 0, files: []}]; renderLatexFiles();`);
node('latex-draft-select').value = 'd2';
node('latex-editor').value = 'unsaved paper one text';
context.route = () => { throw new Error('HTTP 500'); };
sent = [];
await run("latexChooseDraft('d2')");
assert.equal(node('latex-draft-select').value, 'd1', 'a failed save before the switch also restores the selector');
assert.equal(node('latex-editor').value, 'unsaved paper one text', 'and the unsaved text is kept');
assert.equal(run('latexState.draft.draft_id'), 'd1', 'no premature draft swap');
// A successful change keeps the newly chosen value.
openFixture({ dirty: false });
run(`latexState.drafts = [${JSON.stringify(draft(0))}, {draft_id: 'd2', title: 'Second paper', main_file: 'main.tex', revision: 0, files: [{path:'main.tex',text:'paper two'}]}]; renderLatexFiles();`);
context.route = (m, p) => ({ draft_id: 'd2', title: 'Second paper', main_file: 'main.tex', revision: 4, files: [{ path: 'main.tex', text: 'paper two' }] });
assert.equal(await run("latexChooseDraft('d2')"), undefined);
assert.equal(node('latex-draft-select').value, 'd2', 'a successful change shows the new paper');
assert.equal(node('latex-editor').value, 'paper two', 'with its text');

// Repeated New paper clicks share one pending change, never overlapping POSTs.
openFixture({ dirty: false });
run('latexState.drafts = [latexState.draft]; renderLatexFiles();');
let resolveNewPaper;
context.route = () => new Promise(resolve => { resolveNewPaper = resolve; });
sent = [];
const creatingPaper = run('latexChooseDraft(null, true)');
assert.equal(typeof resolveNewPaper, 'function', 'the first creation is in flight');
await run('latexChooseDraft(null, true)');
assert.equal(sent.filter(request => request.method === 'POST').length, 1, 'a second click sends no competing creation');
resolveNewPaper({ draft_id: 'd-created', title: 'Created once', main_file: 'main.tex', revision: 0,
  files: [{ path: 'main.tex', text: 'single created paper' }] });
await creatingPaper;
assert.equal(run('latexState.draft.draft_id'), 'd-created');
assert.equal(node('latex-draft-select').value, 'd-created', 'the selector names the paper whose text is shown');
assert.equal(node('latex-editor').value, 'single created paper');

// (15) Every dynamically rendered status sentence has an exact Korean entry, so no
// English sentence leaks into the Korean writing UI.
const latexSrc = await readFile(new URL('../frontend/latex.js', import.meta.url), 'utf8');
const enKeys = await readFile(new URL('../frontend/i18n.ko.js', import.meta.url), 'utf8');
const registered = new Set();
for (const src of [latexSrc, enKeys])
  for (const m of src.matchAll(/^\s*'((?:[^'\\]|\\.)*)':\s*'/gm)) registered.add(m[1]);
const used = new Set();
for (const re of [/latexT\(\s*'((?:[^'\\]|\\.)*)'/g, /latexSetStatus\(\s*'((?:[^'\\]|\\.)*)'/g, /showError\(\s*'((?:[^'\\]|\\.)*)'/g])
  for (const m of latexSrc.matchAll(re)) used.add(m[1]);
const untranslated = [...used].filter((k) => !registered.has(k));
assert.deepEqual(untranslated, [], `every status key has a KO entry; missing: ${untranslated.join(' | ')}`);
for (const key of ['Not saved: a reload is in progress. Your text is still in the editor.',
                   'Not compiled: a reload is in progress. Wait for it to finish.',
                   'Not switched: a reload is in progress. Wait for it to finish.'])
  assert.ok(latexSrc.includes(`'${key}':`), `the exact key is registered: ${key}`);
// The compile note renders from latex.js but is registered in the shared Korean map,
// so its entry must match the sentence latex.js passes character for character.
assert.ok(enKeys.includes(`'Compile sends the saved draft to the local backend (POST /drafts/{id}/compile) and shows only the PDF returned for the current revision. It stays disabled while no LaTeX engine is found on this machine.':`),
  'the exact compile-note key is registered in the Korean map');
assert.match(latexSrc, /'Compile sends the saved draft to the local backend \(POST \/drafts\/\{id\}\/compile\)/,
  'the compile note key keeps the literal POST path, not a template value');

console.log('PASS lifecycle: hidden view keeps buffer+autosave, switch flushes before S.project, failed/conflicted flush cancels, language redraw preserves text/caret/file/revision/PDF, reload refused while saving, one revision per explicit save, ⌘S from anywhere in the view, stale build reported not shown, bounded log, close guard, named delete, no static i18n on dynamic nodes, reload-in-flight blocks save/compile/switch with no PUT, failed reload keeps buffer+dirty, mode-aware ⌘S, failed paper change restores selector, every status key translated');
