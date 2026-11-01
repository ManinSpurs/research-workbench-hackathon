// Second entry point to the Citation Composer, inside the Writing surface.
// Runs the shipped index.html markup, the real frontend/i18n.js + i18n.ko.js
// translation path, the real frontend/latex.js Korean registration and the real
// frontend/citations.js openCitationView()/citationVisible() against a small DOM
// double. No browser, no network, no model call.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const html = await read('../frontend/index.html');
const css = await read('../frontend/style.css');
const latexSource = await read('../frontend/latex.js');
const citeSource = await read('../frontend/citations.js');
const i18nSource = await read('../frontend/i18n.js');
const koSource = await read('../frontend/i18n.ko.js');

// --- (1) Structure: the control lives INSIDE #latex-view, not beside it.
// writing-mode hides every direct child of main section except #latex-view, so a
// control outside that region would be invisible exactly where it is needed.
const latexStart = html.indexOf('id="latex-view"');
const citationStart = html.indexOf('id="citation-view"');
assert.ok(latexStart > 0 && citationStart > latexStart, 'index.html carries both regions in order');
const writingRegion = html.slice(latexStart, citationStart);

const buttonMatch = writingRegion.match(/<button\b[^>]*data-testid="citation-from-writing"[^>]*>([^<]*)<\/button>/);
assert.ok(buttonMatch, 'a data-testid="citation-from-writing" button lives inside #latex-view');
const buttonTag = buttonMatch[0];
const englishLabel = buttonMatch[1].trim();
assert.equal(englishLabel, 'Cite this sentence', 'the authored English label is the translation key');
assert.match(buttonTag, /type="button"/, 'it is a real button, reachable by Tab (no div or anchor)');
assert.match(buttonTag, /data-i18n(?:\s|>|=)/, 'its label opts into the data-i18n translation path');
assert.match(buttonTag, /onclick="openCitationView\(\)"/,
  'it reuses the existing openCitationView(); it does not reimplement the transition');
assert.ok(buttonTag.includes('aria-describedby="latex-citation-note"'),
  'the button names the sentence that explains what it does');
assert.ok(!buttonTag.includes('disabled'), 'it is never disabled: the no-project case is handled by openCitationView()');
// Placement: under the draft heading, before the save/reload/compile row, so the
// existing .row of compile controls and the two-column grid stay untouched.
assert.ok(writingRegion.indexOf('citation-from-writing') < writingRegion.indexOf('data-testid="latex-save"'),
  'the entry sits with the draft text, above the save/reload/compile row');
assert.ok(writingRegion.indexOf('data-testid="latex-save"') > 0, 'the compile row is still present');
assert.match(writingRegion, /<div class="row latex-citation-entry">[\s\S]*?citation-from-writing/,
  'it reuses the existing .row layout instead of a new layout system');
assert.ok(css.includes('.latex-citation-entry'), 'style.css carries one namespaced rule for the entry row');

// --- (2) Real i18n + real latex.js registration: English and Korean labels.
const elements = new Map();
const mkClassList = () => {
  const set = new Set();
  return { add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c), toggle(c, on) { on ? set.add(c) : set.delete(c); } };
};
const mkEl = (id) => ({
  id, value: '', textContent: '', hidden: false, disabled: false, children: [], dataset: {}, attrs: {},
  classList: mkClassList(),
  addEventListener() {}, removeEventListener() {}, focus() {},
  setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
  // i18n.js asks a node whether it carries data-i18n by attribute, and asks the
  // same of data-i18n-attrs; the double has to answer, or the real i18n.js throws
  // here and the label assertions below would be testing a dead double.
  hasAttribute(k) { return k in this.attrs; },
  removeAttribute(k) { delete this.attrs[k]; },
  appendChild(c) { this.children.push(c); return c; }, replaceChildren() { this.children = []; },
  remove() {}, click() {},
});
const node = (id) => { if (!elements.has(id)) elements.set(id, mkEl(id)); return elements.get(id); };

// The button as i18n.js sees it: the shipped markup's own label and attributes.
const buttonEl = mkEl('citation-from-writing');
buttonEl.textContent = englishLabel;
buttonEl.setAttribute('data-i18n', '');
for (const name of ['data-testid', 'type', 'onclick', 'aria-describedby']) {
  const m = buttonTag.match(new RegExp(name + '="([^"]*)"'));
  if (m) buttonEl.setAttribute(name, m[1]);
}
const noteEl = mkEl('latex-citation-note');
noteEl.textContent = 'Opens the Citation Composer for the sentence in this draft. Nothing is written into the paper until you approve a proposal.';

const store = new Map();
const documentDouble = {
  readyState: 'complete',
  documentElement: { attrs: {}, dataset: {}, setAttribute(k, v) { this.attrs[k] = String(v); } },
  getElementById: (id) => (id === 'citation-from-writing' ? buttonEl : id === 'latex-citation-note' ? noteEl : node(id)),
  createElement: () => mkEl('created'),
  // i18n.js collects by a selector LIST (it added [data-i18n-attrs] beside
  // [data-i18n]); matching only the old single selector would return nothing and
  // every label assertion below would pass against an empty node set.
  querySelectorAll(sel) {
    const wanted = String(sel).split(',').map((s) => s.trim());
    return [buttonEl, noteEl].filter((el) => wanted.some((s) => s === '[data-i18n]' && el.hasAttribute('data-i18n')));
  },
  querySelector(sel) {
    if (sel === 'main section') return node('main-section');
    if (sel === '[data-testid="view-library"]') return node('view-library');
    return null;
  },
  addEventListener() {},
};
const windowDouble = {};
const context = vm.createContext({
  window: windowDouble, document: documentDouble,
  localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) },
  setTimeout, clearTimeout, Date, console, URLSearchParams,
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  $: (id) => documentDouble.getElementById(id),
  esc: (s) => String(s ?? ''),
  showError: (m) => { context.lastError = m; },
  controls: () => {},
  serverText: (s) => String(s || ''),
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
});
vm.runInContext(i18nSource, context);
vm.runInContext(koSource, context);
vm.runInContext(latexSource, context);
vm.runInContext(citeSource, context);

const WB = windowDouble.WB_I18N;
assert.ok(WB, 'the real i18n object is present');

// English is the authored language: the key itself, unchanged.
assert.equal(WB.get(), 'en', 'the double starts in English, the default language');
assert.equal(WB.t(englishLabel), englishLabel, 'English mode shows the English label');
assert.equal(buttonEl.textContent, englishLabel, 'English mode renders the English label');

// Korean: latex.js registers the string locally (the same table its other
// writing-view sentences live in), so the Korean UI is not left English.
WB.set('ko');
assert.equal(WB.get(), 'ko');
const koreanLabel = WB.t(englishLabel);
assert.notEqual(koreanLabel, englishLabel, 'the Korean table really carries this label');
assert.match(koreanLabel, /[\uAC00-\uD7A3]/, 'the Korean label is Korean text');
assert.equal(buttonEl.textContent, koreanLabel, 'Korean mode renders the Korean label through data-i18n');
assert.match(WB.t(noteEl.textContent), /[\uAC00-\uD7A3]/, 'the explanatory sentence is Korean too');
assert.ok(koSource.includes(englishLabel) === false,
  'the string is registered where the file registers its own strings, not duplicated into i18n.ko.js');
WB.set('en');
assert.equal(buttonEl.textContent, englishLabel, 'switching back restores the authored English exactly');

// --- (3) Clicking it from the Writing surface: real transition, real tabs.
const mainSection = node('main-section');
const latexView = node('latex-view');
const citationView = node('citation-view');
// Seed the double's classes from the shipped markup, so "hidden" is the real
// state index.html ships rather than an assumption of the test.
const markupClasses = (id) => {
  const tag = html.match(new RegExp(`<[a-z]+ id="${id}"[^>]*>`));
  assert.ok(tag, `index.html declares #${id}`);
  const m = tag[0].match(/class="([^"]*)"/);
  return m ? m[1].split(/\s+/).filter(Boolean) : [];
};
for (const cls of markupClasses('latex-view')) latexView.classList.add(cls);
for (const cls of markupClasses('citation-view')) citationView.classList.add(cls);
for (const id of ['view-citations', 'view-writing', 'view-research', 'citation-status', 'citation-items', 'citation-absence']) node(id);

context.S.project = 'p1';
// A project is open: openCitationView() shows the composer and loads the list.
context.route = (method, path) => {
  context.sent.push({ method, path });
  if (method === 'GET' && path === '/projects/p1/citations') return { citations: [] };
  throw new Error(`unexpected request ${method} ${path}`);
};
context.sent = [];
vm.runInContext('call = async (m, p, b) => route(m, p, b);', context);

// Enter the Writing surface first, exactly as openWritingView() leaves it.
vm.runInContext('setLatexViewVisible(true)', context);
assert.equal(latexView.classList.contains('hidden'), false, 'writing surface starts visible');
assert.equal(mainSection.classList.contains('writing-mode'), true, 'writing mode is on');
assert.equal(citationView.classList.contains('hidden'), true, 'the composer starts hidden while writing');

// The click: the markup's own handler string, which is what a real click runs.
const handler = buttonEl.getAttribute('onclick');
assert.equal(handler, 'openCitationView()', 'the button runs the existing entry function');
await vm.runInContext(handler, context);

assert.equal(citationView.classList.contains('hidden'), false, 'clicking shows the citation composer');
assert.equal(latexView.classList.contains('hidden'), true, 'clicking hides the latex editor');
assert.equal(mainSection.classList.contains('writing-mode'), false, 'writing mode is left behind');
assert.equal(mainSection.classList.contains('citation-mode'), true, 'citation mode owns the surface');
assert.equal(node('view-citations').getAttribute('aria-pressed'), 'true', 'the Citation Composer tab reads pressed');
assert.equal(node('view-writing').getAttribute('aria-pressed'), 'false', 'the Writing tab releases its pressed state');
assert.equal(node('view-research').getAttribute('aria-pressed'), 'false', 'Research stays unpressed');
assert.deepEqual(context.sent.map((r) => r.path), ['/projects/p1/citations'],
  'the composer loads its own list through the existing path');

// Re-entering Writing must not leave the composer's tab lying about its state.
vm.runInContext('setLatexViewVisible(true)', context);
assert.equal(node('view-writing').getAttribute('aria-pressed'), 'true', 'the Writing tab re-owns its pressed state');

// --- (4) No project: the same control still opens the composer honestly.
context.S.project = null;
context.sent = [];
vm.runInContext('setLatexViewVisible(true)', context);
await vm.runInContext(handler, context);
assert.equal(citationView.classList.contains('hidden'), false, 'the composer opens even with no project');
assert.equal(latexView.classList.contains('hidden'), true, 'the editor is hidden in that case too');
assert.match(node('citation-status').textContent, /Open a project before composing a citation\./,
  'the no-project case states the requirement instead of failing silently');
assert.equal(context.sent.length, 0, 'no request is sent without a project');

console.log('CITATION_FROM_WRITING_PASS: control inside #latex-view, real openCitationView transition, English/Korean labels');
