// LaTeX slice 2 (compile) UI wiring: LC26 plus LC01/LC03/LC16/LC17 frontend halves.
// Runs the real frontend/latex.js in a vm with a DOM double and stubbed transports.
// No browser, no network, no TeX engine, no PDF bytes.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('../frontend/index.html', import.meta.url), 'utf8');
const latexSource = await readFile(new URL('../frontend/latex.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../frontend/style.css', import.meta.url), 'utf8');

// (LC26) Required anchors exist in the LaTeX region.
for (const hook of ['latex-view', 'latex-editor', 'latex-compile', 'latex-diagnostics',
                    'latex-raw-log', 'latex-preview', 'latex-absence'])
  assert.ok(html.includes(`data-testid="${hook}"`), `index.html must carry data-testid ${hook}`);
// Style the suite owns stays quiet (no AI gradients/glow in .latex-* rules).
const latexRules = css.split('}').filter((block) => block.includes('.latex') || block.includes('#latex'));
for (const banned of ['-gradient(', 'box-shadow:', 'text-shadow:', 'backdrop-filter:'])
  for (const block of latexRules)
    assert.ok(!block.toLowerCase().includes(banned), `.latex rules must not use ${banned}`);
assert.ok(!/[\u2728\u{1F916}\u{1F399}\u{1F50A}\u2705\u274C\u26A0\u{1FA84}]/u.test(html), 'no emoji in the LaTeX region');

function makeDom() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: '', textContent: '', innerHTML: '', hidden: false, disabled: false, checked: false,
      listeners: {}, dataset: {}, attrs: {}, tabIndex: -1,
      classList: { add() {}, remove() {} },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      focus() {}, selectionStart: 0, selectionEnd: 0,
      removeEventListener(type) { delete this.listeners[type]; },
      removeAttribute(name) { delete this.attrs[name]; },
      setAttribute(name, value) { this.attrs[name] = String(value); },
      getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
      replaceChildren(...kids) { this.children = kids; },
      appendChild(kid) { (this.children ||= []).push(kid); return kid; },
      click() { if (this.listeners.click) this.listeners.click(); },
      remove() {},
    });
    return nodes.get(id);
  };
  return { nodes, node };
}

const dom = makeDom();
for (const id of ['latex-status', 'latex-absence', 'latex-editor', 'latex-compile', 'latex-preview',
                  'latex-draft-label', 'latex-notice', 'latex-view', 'latex-diagnostics',
                  'latex-raw-log', 'latex-compile-note', 'latex-save', 'latex-reload'])
  dom.node(id);

let blobs = { created: 0, revoked: [] };
let fetched = [];
const context = vm.createContext({
  URLSearchParams, location: { search: '' }, console, setTimeout, clearTimeout, Date,
  API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: (id) => dom.node(id), querySelectorAll: () => [], querySelector: () => null,
    createElement: (tag) => ({ tag, dataset: {}, children: [],
      set src(v) { this._src = v; }, get src() { return this._src; },
      setAttribute(n, v) { this[n] = String(v); }, getAttribute(n) { return this[n] ?? null; },
      addEventListener(t, f) { this['on' + t] = f; }, removeEventListener() {},
      click() { if (typeof this['onclick'] === 'function') this['onclick'](); },
      replaceChildren() {}, remove() {} }) },
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  $: (id) => dom.node(id),
  Blob: class Blob { constructor(parts, opts) { this.parts = parts; this.type = opts && opts.type; } },
  showError: (m) => { context.lastError = m; },
  controls: () => {},
  fetch: async (url) => { fetched.push(url); return { ok: true, status: 200, arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer }; },
  URL: { createObjectURL: (b) => { blobs.created++; blobs.last = b; return `blob:url-${blobs.created}`; },
         revokeObjectURL: (u) => { blobs.revoked.push(u); } },
});
vm.runInContext(latexSource, context);

const capsAbsent = { available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null },
  checked_at: 'now', note: 'No TeX engine found on this machine (checked pdflatex, xelatex, lualatex, latexmk, tectonic on PATH).',
  pdf_export: false };
const capsPresent = { ...capsAbsent, available: true,
  engines: { ...capsAbsent.engines, pdflatex: '/usr/bin/pdflatex' } };
const draft = { draft_id: 'draft-1', project_id: 'p1', question_id: null, title: 'Untitled paper', engine: 'auto',
  main_file: 'main.tex', revision: 0, files: [{ path: 'main.tex', text: 'line one\n\\cite{missing-key}\nline three\n' }],
  sections: [], previous: null, created_at: 'x', updated_at: 'x', model_calls: 0,
  notice: 'The newest 50 autosave steps are kept; older steps are discarded.' };

const setRoute = (fn) => {
  context.route = (m, p, b) => {
    context.sent.push({ method: m, path: p, body: b });
    return fn(m, p, b);
  };
  vm.runInContext('call = async (m, p, b) => route(m, p, b);', context);
};

async function openView(caps, route) {
  context.sent = [];
  context.route = route;
  vm.runInContext('call = async (m, p, b) => route(m, p, b);', context);
  context.S.project = 'p1';
  await vm.runInContext('openWritingView()', context);
}

// (1) LC01/LC26 frontend half: no engine -> Compile disabled with a visible reason.
await openView(capsAbsent, (method, path) => {
  context.sent.push({ method, path });
  if (path === '/capabilities/latex') return capsAbsent;
  if (path === '/projects/p1/drafts') return { project_id: 'p1', drafts: [draft] };
  if (path === '/drafts/draft-1') return draft;
  throw new Error(`unexpected request ${method} ${path}`);
});
assert.equal(dom.node('latex-compile').disabled, true, 'Compile disabled with no engine');
assert.match(dom.node('latex-compile-note').textContent, /no LaTeX engine/i, 'disabled reason is visible');
assert.ok(html.includes('data-testid="latex-compile"') && html.includes('aria-describedby="latex-compile-note"'),
  'Compile exposes its reason through aria-describedby on latex-compile-note');
assert.equal(blobs.created, 0, 'no PDF fabricated while disabled');

// (2) Engine present -> Compile enables; in-flight -> controls lock + plain status.
vm.runInContext('latexState.capabilities = presentCaps', Object.assign(context, { presentCaps: capsPresent }));
vm.runInContext('syncLatexControls()', context);
assert.equal(dom.node('latex-compile').disabled, false, 'Compile enables when a draft exists and an engine is reported');
let release;
setRoute((method, path, body) => {
  return new Promise((resolve) => { release = () => resolve({ status: 'failed', revision: 0, diagnostics: [], log_tail: 'log' }); });
});
const flight = vm.runInContext('compileDraft()', context);
await new Promise((r) => setTimeout(r, 10));
assert.equal(dom.node('latex-compile').disabled, true, 'Compile locks while compiling');
assert.equal(dom.node('latex-save').disabled, true, 'Save locks while compiling');
assert.equal(dom.node('latex-status').textContent, 'Compiling…', 'plain in-flight status');
release();
await flight;
assert.equal(dom.node('latex-compile').disabled, false, 'controls unlock after the run');
assert.equal(vm.runInContext('latexState.compiling', context), false, 'compiling flag cleared');

// (3) LC03 frontend half: toolchain_absent -> failure + log, zero PDF bytes.
// Capabilities say "engine", the server answers "absent" anyway: the UI trusts the response, never the probe.
vm.runInContext('latexState.capabilities = presentCaps', Object.assign(context, { presentCaps: capsPresent }));
fetched = [];
setRoute((method, path, body) => {
  if (path === '/drafts/draft-1/compile') {
    assert.equal(JSON.stringify(body), '{"source":"manual"}', 'compile body carries exactly {source}');
    return { status: 'toolchain_absent', revision: 0, checked: ['pdflatex', 'xelatex', 'lualatex', 'latexmk', 'tectonic'], diagnostics: [], log_tail: '' };
  }
  throw new Error(`unexpected request ${method} ${path}`);
});
await vm.runInContext('compileDraft()', context);
assert.match(dom.node('latex-status').textContent, /no LaTeX engine/i, 'toolchain absence reported honestly');
assert.equal(dom.node('latex-status').getAttribute('data-state'), 'fail');
assert.equal(blobs.created, 0, 'toolchain absence fabricates no PDF');
assert.equal(fetched.length, 0, 'no PDF fetch without a server PDF');

// (4) ok run: ONLY the server PDF for the current revision is shown, via export.pdf.
fetched = [];
vm.runInContext('latexState.capabilities = newCaps', Object.assign(context, { newCaps: capsPresent }));
const okDiag = [
  { severity: 'warning', code: 'UNKNOWN_CITATION_KEY', line: 2, message: "Citation 'missing-key' is undefined" },
  { severity: 'error', line: 3, message: 'Missing $ inserted' },
];
setRoute((method, path, body) => {
  if (path === '/drafts/draft-1/compile') {
    assert.equal(JSON.stringify(body), '{"source":"manual"}');
    return { status: 'ok', revision: 0, pdf_url: '/drafts/draft-1/export.pdf', diagnostics: okDiag, log_tail: 'raw log lines', citations_resolved: true };
  }
  throw new Error(`unexpected request ${method} ${path}`);
});
dom.node('latex-editor').value = draft.files[0].text;
await vm.runInContext('compileDraft()', context);
assert.deepEqual(fetched, ['http://localhost:8123/drafts/draft-1/export.pdf'], 'PDF fetched only from the server pdf_url');
assert.equal(blobs.created, 1, 'the server PDF is shown once');
assert.match(dom.node('latex-status').textContent, /Compiled from revision 0/, 'status pins the built revision');
assert.equal(dom.node('latex-draft-label').textContent.includes('stale'), false, 'fresh preview is not stale');
const diagList = dom.node('latex-diagnostics');
assert.equal(diagList.hidden, false, 'diagnostics list shown');
assert.equal(diagList.children.length, 2, 'two server diagnostics rendered');
assert.match(diagList.children[0].textContent, /^Unknown citation key · line 2:/, 'UNKNOWN_CITATION_KEY renders distinctly with line');
assert.ok(diagList.children[0].textContent.includes('missing-key'), 'citation message preserved');
assert.equal(diagList.children[0]['data-code'], 'UNKNOWN_CITATION_KEY');
assert.match(diagList.children[1].textContent, /^error · line 3:/, 'severity/line/message rendered');
assert.equal(dom.node('latex-raw-log').hidden, false, 'raw log shown');
assert.equal(dom.node('latex-raw-log').textContent, 'raw log lines', 'raw log is server text verbatim');
// LC26: click-to-jump moves the caret to the parsed line.
diagList.children[1].click();
assert.equal(vm.runInContext(`(() => { const e = $('latex-editor'); return e.selectionStart; })()`, context), 'line one\n\\cite{missing-key}\n'.length,
  'clicking a diagnostic jumps the caret to the parsed line');

// (5) LC17 frontend half: editing after a success REMOVES the old PDF. A pane that
// belongs to one revision is emptied instead of keeping a document that is no longer
// the current draft's.
dom.node('latex-editor').value = 'line one\nEDITED\nline three\n';
assert.equal(blobs.created, 1, 'one blob before the edit');
setRoute((method, path, body) => {
  if (method === 'PUT' && path === '/drafts/draft-1') return { ...draft, revision: 1, files: [{ path: 'main.tex', text: body.files[0].text }] };
  throw new Error(`unexpected request ${method} ${path}`);
});
await vm.runInContext("saveDraft('manual')", context);
assert.match(dom.node('latex-status').textContent, /revision 1/, 'status follows the saved revision');
assert.equal(blobs.created, 1, 'saving creates no new PDF');
assert.deepEqual(blobs.revoked, ['blob:url-1'], 'the superseded PDF bytes are revoked, not kept as a stale document');
assert.equal(vm.runInContext('latexState.compiledRevision', context), null, 'no revision is still claimed as compiled');
assert.equal(dom.node('latex-preview').textContent, 'No PDF yet.', 'the pane states the truth after the draft moves on');

// (6) LC16 frontend half: failure shows failure + its log and leaves no PDF shown.
fetched = [];
setRoute((method, path, body) => {
  if (path === '/drafts/draft-1/compile')
    return { status: 'failed', revision: 1, diagnostics: [{ severity: 'error', line: 1, message: 'Undefined control sequence' }], log_tail: 'failure log' };
  throw new Error(`unexpected request ${method} ${path}`);
});
await vm.runInContext('compileDraft()', context);
assert.match(dom.node('latex-status').textContent, /Not compiled \(failed\)/, 'failure status names the outcome');
assert.equal(dom.node('latex-status').getAttribute('data-state'), 'fail');
assert.equal(dom.node('latex-raw-log').textContent, 'failure log', 'failure log retained');
assert.equal(blobs.created, 1, 'a failure shows no new PDF');
assert.equal(dom.node('latex-preview').textContent, 'No PDF yet.', 'a failed build leaves no document in the pane');

console.log('LATEX_COMPILE_UI_PASS: enable gate, post-only-server-pdf, diagnostics + citation flag, raw log, stale pin, failure honesty');
