// LaTeX writing view, slice 1: honest unavailable-compile state, save flow, autosave,
// revision conflict, blob URL revocation, quiet doctrine. Runs the real frontend files in a
// vm with a small DOM double and a stubbed call(). No browser, no network, no PDF.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const latexSource = await readFile(new URL('../frontend/latex.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../frontend/style.css', import.meta.url), 'utf8');

// (0) Structural and quiet-doctrine checks on the shipped files.
for (const hook of ['latex-view', 'latex-editor', 'latex-compile', 'latex-diagnostics',
                    'latex-raw-log', 'latex-preview', 'latex-absence', 'latex-status', 'latex-save',
                    'latex-recheck'])
  assert.ok(html.includes(`data-testid="${hook}"`), `index.html must carry data-testid ${hook}`);
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  assert.ok(!css.toLowerCase().includes(banned), `style.css must not use ${banned}`);
assert.ok(!/[\u2728\u{1F916}\u{1F399}\u{1F50A}\u2705\u274C\u26A0\u{1FA84}]/u.test(css + html), 'no emoji in UI chrome');
assert.ok(css.includes('#latex-view {') && css.includes('.latex-absence'), 'namespaced writing-view rules present');
assert.ok(html.indexOf('id="latex-view"') > html.indexOf('<section aria-label="Research surface">'),
  'writing view lives inside the existing research surface, not a new page');

// Preserved panels: the writing view must not have displaced existing behaviour.
for (const keep of ['data-testid="launch-briefing"', 'data-testid="briefing"', 'data-testid="pending"',
                    'id="research-canvas"', 'id="ai-results"', 'data-testid="end-session"'])
  assert.ok(html.includes(keep), `existing surface preserved: ${keep}`);
assert.ok(html.includes('<script src="latex.js"></script>'), 'latex.js is loaded by index.html');

function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: '', textContent: '', innerHTML: '', hidden: false, disabled: false, checked: false,
      listeners: {}, dataset: {}, attrs: {},
      classList: { add() {}, remove() {} },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      focus() {}, selectionStart: 0, selectionEnd: 0,
      removeAttribute(name) { delete this.attrs[name]; },
      setAttribute(name, value) { this.attrs[name] = String(value); },
      getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
      replaceChildren() {}, remove() {},
    });
    return nodes.get(id);
  };
  return { nodes, node };
}

let blobs = { created: 0, revoked: [] };
const context = vm.createContext({
  URLSearchParams, location: { search: '' }, console, setTimeout, clearTimeout, Date,
  confirm: () => true,
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: (id) => dom.node(id), querySelectorAll: () => [], querySelector: () => null,
              createElement: (tag) => ({ tag, dataset: {}, set src(v) { this._src = v; }, get src() { return this._src; } }) },
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  $: (id) => dom.node(id),
  showError: (m) => { context.lastError = m; },
  controls: () => {},
  URL: { createObjectURL: (b) => { blobs.created++; blobs.last = b; return `blob:url-${blobs.created}`; },
         revokeObjectURL: (u) => { blobs.revoked.push(u); } },
});
const dom = makeDom();
dom.node('latex-draft-label');
for (const id of ['latex-status', 'latex-absence', 'latex-editor', 'latex-compile', 'latex-preview',
                  'latex-draft-label', 'latex-notice', 'latex-view'])
  dom.node(id);
vm.runInContext(latexSource, context);

const capsAbsent = { available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null },
  checked_at: 'now', note: 'No TeX engine found on this machine (checked pdflatex, xelatex, lualatex, latexmk, tectonic on PATH).',
  pdf_export: false };
const draft = { draft_id: 'draft-1', project_id: 'p1', question_id: null, title: 'Untitled paper', engine: 'auto',
  main_file: 'main.tex', revision: 0, files: [{ path: 'main.tex', text: '\\section{Intro}\n' }], sections: [],
  previous: null, created_at: 'x', updated_at: 'x', model_calls: 0,
  notice: 'The newest 50 autosave steps are kept; older steps are discarded.' };

// (1) Opening the view with an absent toolchain: banner + disabled compile, no PDF claims.
context.sent = [];
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  if (path === '/capabilities/latex') return capsAbsent;
  if (path === '/projects/p1/drafts') return { project_id: 'p1', drafts: [draft] };
  if (path === '/drafts/draft-1') return draft;
  throw new Error(`unexpected request ${method} ${path}`);
};
vm.runInContext('call = async (m, p, b) => route(m, p, b);', context);
context.S.project = 'p1';
await vm.runInContext('openWritingView()', context);

assert.equal(dom.node('latex-absence').hidden, false, 'absence banner is shown, not hidden');
assert.ok(dom.node('latex-absence').textContent.includes('PDF preview needs a LaTeX engine'),
  'banner states the engineering absence in plain words');
assert.ok(dom.node('latex-absence').textContent.includes('none of pdflatex, xelatex, lualatex, latexmk, tectonic'),
  'banner names the engines actually checked');
assert.equal(dom.node('latex-compile').disabled, true, 'Compile stays disabled without an engine');
assert.equal(dom.node('latex-preview').textContent, 'No PDF yet.',
  'preview pane states the truth instead of a PDF');
assert.equal(dom.node('latex-editor').value, '\\section{Intro}\n', 'editor shows the stored main file');
assert.ok(dom.node('latex-notice').textContent.includes('50 autosave steps'),
  'the retention limit is disclosed in the view, not silently applied');
assert.match(dom.node('latex-status').textContent, /Idle · revision 0/, 'status line reports the revision');
assert.equal(blobs.created, 0, 'no blob URL is created without a PDF');

// (2) Compile never fabricates a PDF, even when an engine is on PATH.
vm.runInContext('latexState.capabilities = newCaps', Object.assign(context, { newCaps: { ...capsAbsent, available: true, engines: { ...capsAbsent.engines, pdflatex: '/usr/bin/pdflatex' } } }));
vm.runInContext('syncLatexControls()', context);
assert.equal(dom.node('latex-compile').disabled, false, 'Compile enables when a draft exists and an engine is reported');

// (2b) Re-check re-polls capabilities only; Compile stays disabled and no PDF appears.
context.sent = [];
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  return capsAbsent;
};
const rechecked = await vm.runInContext('recheckLatex()', context);
assert.equal(rechecked.available, false, 're-check returns the observed capability');
assert.deepEqual(vm.runInContext('sent.map(r => r.path)', context), ['/capabilities/latex'],
  're-check only re-polls the cheap probe');
assert.equal(dom.node('latex-compile').disabled, true, 'Compile stays disabled after a re-check');
assert.equal(blobs.created, 0, 're-check creates no PDF');

// (3) Manual save: PUT with expected_revision, source manual, status ok, revision advances.
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  if (method === 'PUT' && path === '/drafts/draft-1') {
    assert.equal(body.expected_revision, 0, 'save carries the revision it read');
    assert.equal(body.source, 'manual');
    return { ...draft, revision: 1, files: [{ path: 'main.tex', text: body.files[0].text }] };
  }
  throw new Error(`unexpected request ${method} ${path}`);
};
dom.node('latex-editor').value = '\\section{Intro}\nEdited text\n';
const saved = await vm.runInContext("saveDraft('manual')", context);
assert.equal(saved.revision, 1, 'save accepted and revision advanced');
assert.equal(vm.runInContext('latexState.draft.revision', context), 1, 'view state follows the saved revision');
assert.equal(vm.runInContext('latexState.dirty', context), false, 'a successful save clears the dirty flag');
assert.match(dom.node('latex-status').textContent, /saved .*revision 1/, 'status confirms the save');

// (4) Autosave is debounced, and a keystroke alone sends nothing immediately.
context.sent = [];
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  return { ...draft, revision: 2, files: [{ path: 'main.tex', text: body.files[0].text }] };
};
vm.runInContext('scheduleLatexAutosave(); scheduleLatexAutosave();', context);
assert.equal(vm.runInContext('sent.length', context), 0, 'autosave waits for the idle debounce');
await new Promise((r) => setTimeout(r, 900));
assert.equal(vm.runInContext('sent.length', context), 1, 'one debounced autosave request after idle');
assert.equal(vm.runInContext('sent[0].body.source', context), 'autosave', 'autosave is labelled autosave');
assert.equal(vm.runInContext('sent[0].method', context), 'PUT');

// (5) Revision conflict: honest 409 message, editor text preserved, no retry loop.
context.sent = [];
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  throw new Error('Draft changed; reload before saving');
};
dom.node('latex-editor').value = 'unsaved text that must survive';
const conflicted = await vm.runInContext("saveDraft('manual')", context);
assert.equal(conflicted, null, 'a conflicted save returns nothing');
assert.equal(vm.runInContext('latexState.draft.revision', context), 2, 'the stored revision is not advanced locally');
assert.equal(vm.runInContext('latexState.dirty', context), true, 'the draft stays dirty after a conflict');
assert.match(dom.node('latex-status').textContent, /reload before saving/, 'conflict message matches the backend contract');
assert.equal(dom.node('latex-editor').value, 'unsaved text that must survive', 'the editor keeps the researcher text');
assert.equal(vm.runInContext('sent.length', context), 1, 'a conflict does not retry automatically');
// While the conflict is unresolved the autosave timer must not fire and overwrite another
// tab's newer draft; the explicit Save button is blocked by the same flag.
context.sent = [];
vm.runInContext('scheduleLatexAutosave()', context);
await new Promise((r) => setTimeout(r, 900));
assert.equal(vm.runInContext('sent.length', context), 0, 'autosave stays blocked until reload');
assert.equal(await vm.runInContext("saveDraft('manual')", context), null, 'manual save is blocked too');
assert.equal(vm.runInContext('sent.length', context), 0, 'a blocked save sends no request');

// (6) Reload accepts the newer server revision.
context.route = (method, path, body) => ({ ...draft, revision: 3, files: [{ path: 'main.tex', text: 'server text' }] });
const reloaded = await vm.runInContext('reloadDraft()', context);
assert.equal(reloaded.revision, 3);
assert.equal(dom.node('latex-editor').value, 'server text', 'reload restores the stored text');
assert.equal(vm.runInContext('latexState.dirty', context), false, 'reload clears the dirty flag');
assert.equal(vm.runInContext('latexState.blocked', context), false, 'reload lifts the conflict block');

// (7) Blob URL lifecycle: replaced URLs are revoked, never leaked.
vm.runInContext('latexState.capabilities = newCaps', Object.assign(context, { newCaps: capsAbsent }));
const first = vm.runInContext('renderPreview({tag:"pdf-1"})', context);
assert.ok(first && first.startsWith('blob:'), 'a real PDF response is shown via a blob URL');
const second = vm.runInContext('renderPreview({tag:"pdf-2"})', context);
assert.notEqual(first, second);
assert.deepEqual(blobs.revoked, [first], 'the previous blob URL is revoked on replace');
vm.runInContext('renderPreview(null)', context);
assert.deepEqual(blobs.revoked, [first, second], 'the last blob URL is revoked on unmount');
assert.equal(dom.node('latex-preview').textContent, 'No PDF yet.');

// (8) Hiding the writing view (question cleared, session ended) is NOT a project
// switch: the same project's draft, unsaved text and autosave timer are kept, so a
// reopen shows the researcher's work again. Only latexBeforeProjectSwitch() unmounts,
// and that runs from openProject() before S.project changes.
context.sent = [];
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  if (method === 'PUT') return { ...draft, revision: 4, files: body.files };
  throw new Error(`unexpected request ${method} ${path}`);
};
dom.node('latex-editor').value = 'unsaved text kept across a hidden view';
vm.runInContext('scheduleLatexAutosave()', context);
context.sent = [];
vm.runInContext('clearLatex()', context);
assert.notEqual(vm.runInContext('latexState === null', context), true, 'hiding the view keeps the held draft');
assert.equal(vm.runInContext('latexState.dirty', context), true, 'hiding the view keeps the unsaved flag');
assert.equal(dom.node('latex-editor').value, 'unsaved text kept across a hidden view', 'hiding the view keeps the editor text');
assert.equal(vm.runInContext('latexTimer !== null', context), true, 'the pending autosave still runs while the view is hidden');
await new Promise((r) => setTimeout(r, 900));
assert.equal(vm.runInContext('sent.length', context), 1, 'the kept autosave really fires');
assert.equal(vm.runInContext('sent[0].body.files[0].text', context), 'unsaved text kept across a hidden view',
  'and it saves the researcher text for its own project');

// A true project switch flushes first and only then drops the held draft.
context.route = (method, path, body) => {
  context.sent.push({ method, path, body });
  if (method === 'PUT') return { ...draft, revision: 4, files: body.files };
  throw new Error(`unexpected request ${method} ${path}`);
};
dom.node('latex-editor').value = 'flush me before switching';
vm.runInContext('scheduleLatexAutosave()', context);
context.sent = [];
const switched = await vm.runInContext('latexBeforeProjectSwitch()', context);
assert.equal(switched, true, 'the hook reports a safe flush');
assert.deepEqual(vm.runInContext('sent.map(r => r.method)', context), ['PUT'], 'exactly one flush save, no discard');
assert.equal(vm.runInContext('sent[0].body.files[0].text', context), 'flush me before switching', 'the flushed text is the current editor text');
assert.equal(vm.runInContext('latexState === null', context), true, 'after a safe flush the held draft is dropped');
assert.equal(dom.node('latex-editor').value, '', 'no old project text is left in the editor');
assert.equal(dom.node('latex-compile').disabled, true, 'compile remains disabled after unmount');

// A failed flush refuses the switch, so app.js can keep S.project as it was.
vm.runInContext(`latexState={project:'p1',draft:${JSON.stringify(draft)},capabilities:${JSON.stringify(capsAbsent)},
  dirty:true,saving:false,blocked:false,serial:0,compiling:false,compiledRevision:null};`, context);
dom.node('latex-editor').value = 'text that must not be dropped';
context.route = () => { throw new Error('HTTP 500'); };
const refused = await vm.runInContext('latexBeforeProjectSwitch()', context);
assert.equal(refused, false, 'a failed flush cancels the switch');
assert.equal(vm.runInContext('latexState.dirty', context), true, 'the draft is still held and still dirty');
assert.equal(dom.node('latex-editor').value, 'text that must not be dropped', 'and the text is still in the editor');
// A conflict refuses the switch for the same reason: reload is the only way forward.
vm.runInContext('latexState.blocked = true', context);
assert.equal(await vm.runInContext('latexBeforeProjectSwitch()', context), false, 'a conflicted draft cancels the switch');
assert.match(dom.node('latex-status').textContent, /save conflict/, 'and says why');

// (9) The view is project-bound: no project means no request at all.
context.S.project = null;
context.sent = [];
await vm.runInContext('openWritingView()', context);
assert.equal(vm.runInContext('sent.length', context), 0, 'no draft request without an open project');
assert.match(vm.runInContext('lastError || String()', context), /Open a project/, 'the UI asks for a project instead of guessing one');

console.log('LATEX_VIEW_PASS: honest absence, no fake PDF, manual save, debounced autosave, conflict kept text, blob revoke, project binding');
