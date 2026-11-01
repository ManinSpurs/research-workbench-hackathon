// Cartridge start screen integration: the approved Concept A art is composed
// unchanged, the real workspace is revealed inside the console display (never
// remounted), and research-field-ready fires only after expansion settles.
// No browser, no network: static source checks plus the startup script in a DOM
// double driven through its real handlers.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const startup = await read('../frontend/startup.js');
const app = await read('../frontend/app.js');
const css = await read('../frontend/style.css');
const html = await read('../frontend/index.html');
const art = await readFile(new URL('../frontend/assets/Console_Concept_A.png', import.meta.url));

// --- 1. Approved art is untouched and composed at its documented anchors.
const APPROVED_SHA = 'c70aaba99130a46f1ea5b628a3497f9e1c415f39830e6fec77936a12b8c8c5e7';
assert.equal(crypto.createHash('sha256').update(art).digest('hex'), APPROVED_SHA,
  'frontend/assets/Console_Concept_A.png must be the approved bytes, unmodified');
assert.ok(html.includes('href="assets/Console_Concept_A.png"'), 'the start screen renders the approved PNG');
assert.ok(html.includes('clipPath id="approvedHardwareSilhouette"'), 'the orthogonal silhouette clip is preserved');
assert.ok(html.includes('clip-path="url(#approvedHardwareSilhouette)"'), 'the image is clipped by that silhouette');
assert.ok(!/data:image\/png;base64/.test(html), 'the art is served as a file, not re-inlined by a build step');
for (const anchor of ['x: 24, y: 170, width: 1488, height: 656', 'x: 333, y: 261, width: 871, height: 486', 'centerX: 1179, restTop: 173'])
  assert.ok(startup.includes(anchor), `ART_ANCHORS keeps the measured anchor ${anchor}`);
// The image-space anchors drive the CSS percentages, so the overlay cannot drift.
const x = (333 - 24) / 1488 * 100, y = (261 - 170) / 656 * 100;
assert.ok(css.includes(`--display-x:${x.toFixed(6)}%`), 'display overlay derives from the inner-display anchor');
assert.ok(css.includes(`--display-y:${y.toFixed(6)}%`), 'display overlay top derives from the inner-display anchor');
assert.ok(css.includes(`--slot-x:${((1179 - 24) / 1488 * 100).toFixed(6)}%`), 'slot anchor derives from the source pixels');

// --- 2. The superseded sidebar cartridge is fully gone; one start screen only.
assert.ok(!/packCart|mountPackCart|cartPixelText|CART_FONT|pc-tray|pc-svg/.test(app),
  'the old sidebar pixel cartridge is removed from app.js');
assert.ok(!/pack-cart/.test(html), 'index.html no longer ships the sidebar cartridge host');
assert.ok(!/\.pc-svg|\.pc-tray|\.pack-cart\s*\{/.test(css), 'style.css no longer styles the removed cartridge');
assert.equal((html.match(/id="startup"/g) || []).length, 1, 'exactly one start screen');
assert.ok(!/<script\s+src="[^"]*cart[^"]*"/i.test(html), 'no cartridge script file is loaded');
// chip_bridge.js is the desktop-menu bridge (Electron only); it draws nothing and
// renders no cartridge, so it does not reintroduce the removed sidebar cartridge.
assert.ok(html.includes('<script src="chip_bridge.js"></script>'), 'the desktop chip bridge is loaded');
assert.ok(!/gradient\(|box-shadow:|text-shadow:|backdrop-filter:/i.test(startup + css),
  'flat surfaces only: no gradients, shadows or glow anywhere');

// --- 3. The real app lives inside the display layer, not a mock workspace.
assert.ok(!/research-workspace|sampleLibrary|demoNote|mock/i.test(html), 'the demo workspace is gone');
assert.ok(html.indexOf('id="portal"') < html.indexOf('id="app-shell"'), 'the real app is mounted inside the portal');
assert.ok(html.indexOf('id="app-shell"') < html.indexOf('<header>'), 'the existing header moved into the app shell');
for (const hook of ['id="pack-select"', 'id="project-list"', 'id="ai-results"', 'data-testid="end-session"',
                    'id="research-canvas"', 'data-testid="launch-briefing"', 'id="latex-view"'])
  assert.ok(html.includes(hook), `existing workspace surface preserved: ${hook}`);
assert.ok(html.includes('<script src="startup.js"></script>'), 'startup.js is loaded');
assert.ok(html.indexOf('startup.js') < html.indexOf('app.js'),
  'startup.js is parsed first so its ready gate exists before app.js starts');

// --- 4. app.js waits for the start screen but fails safe.
assert.ok(/if \(window\.WB_STARTUP\) await window\.WB_STARTUP\.ready;/.test(app),
  'app.js awaits the start gate before loading its workspace');
assert.ok(/try \{ if \(window\.WB_STARTUP\) await window\.WB_STARTUP\.ready; \} catch \(_\) \{\}/.test(app),
  'a missing or failing start screen never blocks the workspace');
assert.ok(/var KEY = 'wb\.pack';/.test(startup),
  'the field preference is the app key that app.js also reads and writes');
assert.ok(!/localStorage\.(getItem|setItem|removeItem)\(\s*['"]/.test(startup),
  'every storage call goes through the single app key (no second preference store)');
assert.ok(!/demoNotes|demoNote/.test(startup), 'the demo note store is not migrated');
assert.ok(!/localStorage\.clear/.test(startup), 'no reset path clears unrelated storage');

const mk = ({ saved = null, reduced = false, storageBlocked = false } = {}) => {
  const elements = new Map();
  const listeners = {};
  const created = [];
  const animations = [];
  const store = new Map();
  if (saved !== null) store.set('wb.pack', saved);
  // Initial hidden state mirrors the shipped index.html: the portal, the change
  // control and the cancel button start hidden; the console starts visible.
  const HIDDEN_AT_LOAD = new Set(['portal', 'startup-change', 'startup-cancel']);
  const mkEl = (id) => {
    const el = {
      id, hidden: HIDDEN_AT_LOAD.has(id), inert: false, disabled: false, textContent: '', className: '', childElementCount: 0,
      children: [], style: { setProperty() {} }, attrs: {}, dataset: {},
      classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); }, contains(c) { return this.set.has(c); } },
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
      appendChild(c) { this.children.push(c); this.childElementCount = this.children.length; return c; },
      addEventListener(t, fn) { (this.listeners = this.listeners || {}), (this.listeners[t] = this.listeners[t] || []).push(fn); },
      focus() { this.focused = true; }, getBoundingClientRect() { return { left: 10, top: 20, width: 100, height: 60 }; },
      animate() { const m = { finished: Promise.resolve(), cancel() {} }; animations.push(m); return m; },
    };
    return el;
  };
  const get = (id) => { if (!elements.has(id)) elements.set(id, mkEl(id)); return elements.get(id); };
  const choices = () => get('startup-choices').children || [];
  const document = {
    readyState: 'complete',
    body: mkEl('body'),
    getElementById: get,
    createElement(tag) { const el = mkEl('created-' + tag); created.push(el); return el; },
    querySelectorAll(sel) { return sel === '[data-startup-field]' ? choices() : []; },
    querySelector(sel) { return sel === '[data-startup-field]' ? choices()[0] || null : null; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
  };
  const dispatched = [];
  const window = {
    innerWidth: 1200, innerHeight: 800, visualViewport: null,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    dispatchEvent(e) { dispatched.push(e); },
    matchMedia: () => ({ matches: reduced, addEventListener() {} }),
  };
  const context = vm.createContext({
    window, document, CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail; } },
    localStorage: {
      getItem(k) { if (storageBlocked) throw Error('blocked'); return store.has(k) ? store.get(k) : null; },
      setItem(k, v) { if (storageBlocked) throw Error('blocked'); store.set(k, v); },
      removeItem(k) { if (storageBlocked) throw Error('blocked'); store.delete(k); },
    },
    setTimeout(fn) { Promise.resolve().then(fn); return 1; },
    Object, Promise, String, Math, Set,
  });
  vm.runInContext(startup, context);
  const fields = () => choices().map((b) => b.getAttribute('data-startup-field'));
  const press = (pack) => {
    const b = choices().find((c) => c.getAttribute('data-startup-field') === pack);
    assert.ok(b, `choice ${pack} rendered`);
    (b.listeners.click || []).forEach((fn) => fn({}));
  };
  return { context, get, store, dispatched, animations, fields, press, created, choices, window, listeners };
};
const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };
const readyEvents = (h) => h.dispatched.filter((e) => e.type === 'research-field-ready');

// --- 5. First use: both fields offered, no preference assumed, nothing written yet.
{
  const h = mk();
  assert.equal(h.get('startup').hidden, false, 'start screen is visible on first launch');
  assert.deepEqual(h.fields(), ['social_science', 'engineering_measurement'], 'both research fields are offered');
  assert.equal(h.choices().length, 2, 'each field is a real button, not decorative pixels');
  for (const b of h.choices()) { assert.equal(b.type, 'button', 'choice is a button'); assert.equal(b.getAttribute('aria-pressed'), 'false', 'nothing pre-selected'); }
  assert.ok(!h.store.has('wb.pack'), 'first launch assumes no field and writes nothing');
  assert.equal(h.get('startup-cancel').hidden, true, 'no cancel on first use: there is no previous field');
  assert.equal(readyEvents(h).length, 0, 'no ready event before a choice');
}

// --- 6. Insertion persists once, powers on, then shows the home menu.
{
  const h = mk();
  h.press('engineering_measurement');
  await flush();
  assert.equal(h.store.get('wb.pack'), 'engineering_measurement', 'the explicit choice is persisted once');
  assert.equal(h.get('startup').hidden, false, 'the console stays up: the home menu is drawn on its display');
  assert.equal(h.get('startup-menu').hidden, false, 'the home menu appears on the display');
  assert.equal(h.get('portal').hidden, true, 'the workspace stays closed until a menu is chosen');
  const events = readyEvents(h);
  assert.equal(events.length, 1, 'research-field-ready fires exactly once when the field is seated');
  assert.equal(events[0].detail.field, 'engineering', 'detail.field is the field key');
  assert.equal(events[0].detail.label, 'Engineering', 'detail.label is human readable');
  assert.equal(events[0].detail.source, 'fieldnote-cartridge', 'detail.source names the cartridge');
  assert.equal(readyEvents(h)[0].detail.demo, undefined, 'the demo flag is gone now that the real app is mounted');
  // Only the display grows: no scale transform anywhere in the reveal.
  assert.ok(!/scale\(/.test(startup), 'the console is never scaled to fake expansion');
  assert.ok(!/transform:/.test(startup.slice(startup.indexOf('function expandDisplay'), startup.indexOf('function collapseDisplay'))),
    'expansion animates bounds only');
}

// --- 6b. Entering a menu from the home screen grows the display into the workspace.
{
  const h = mk();
  h.press('engineering_measurement');
  await flush();
  const tile = (h.get('home-menu').children || []).find((b) => b.getAttribute('data-menu') === 'library');
  assert.ok(tile, 'the Literature Review tile is rendered');
  (tile.listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.get('portal').classList.contains('expanded'), true, 'choosing a menu expands the portal');
  assert.equal(h.get('portal').hidden, false, 'the portal is revealed');
  assert.equal(h.get('startup').hidden, true, 'the console is dismissed, not scaled away');
  assert.ok(h.animations.length >= 1, 'the reveal is animated');
  assert.equal(h.get('startup').inert, true, 'the dismissed console is inert');
  assert.equal(h.get('project-title').focused, true, 'focus moves into the workspace');
}

// --- 7. A saved field skips selection, powers on, and shows the home menu.
{
  const h = mk({ saved: 'social_science' });
  await flush();
  assert.equal(h.store.get('wb.pack'), 'social_science', 'the stored preference is preserved');
  assert.equal(readyEvents(h).length, 1, 'a returning launch fires ready once');
  assert.equal(readyEvents(h)[0].detail.field, 'social', 'the remembered field is restored');
  assert.equal(h.get('startup-cancel').hidden, true, 'no cancel on a returning launch');
  assert.equal(h.get('startup-menu').hidden, false, 'a returning launch lands on the home menu');
  assert.equal(h.get('portal').hidden, true, 'the workspace does not open by itself');
}

// --- 8. An unknown stored value is rejected instead of half-applied.
{
  const h = mk({ saved: 'not_a_field' });
  await flush();
  assert.equal(h.get('startup').hidden, false, 'an unknown preference falls back to the picker');
  assert.equal(readyEvents(h).length, 0, 'no workspace opens on a corrupt preference');
  assert.equal(h.get('portal').hidden, true, 'the portal stays closed');
}

// --- 9. Change reads the previous field, cancel restores it, and no data is touched.
{
  const h = mk({ saved: 'social_science' });
  await flush();
  (h.get('startup-change').listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.get('startup').hidden, false, 'change reopens the console');
  assert.equal(h.get('startup-cancel').hidden, false, 'change offers cancel, unlike first use');
  assert.equal(h.get('startup-menu').hidden, true, 'the home menu is hidden while choosing');
  assert.equal(h.store.get('wb.pack'), 'social_science', 'opening the picker changes no preference');
  (h.get('startup-cancel').listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.store.get('wb.pack'), 'social_science', 'cancel keeps the previous field');
  assert.equal(h.get('startup-menu').hidden, false, 'cancel returns to the home menu, opening nothing');
  assert.equal(h.get('portal').hidden, true, 'cancel does not open the workspace');
  assert.equal(readyEvents(h).length, 2, 'cancel re-announces the restored field');
  assert.equal(readyEvents(h)[1].detail.field, 'social', 'the restored field is the original one');
}

// --- 10. Switching fields persists the new one; Escape cancels like the button.
{
  const h = mk({ saved: 'social_science' });
  await flush();
  (h.get('startup-change').listeners.click || []).forEach((fn) => fn({}));
  await flush();
  h.press('engineering_measurement');
  await flush();
  assert.equal(h.store.get('wb.pack'), 'engineering_measurement', 'a confirmed change persists the new field');
  assert.equal(readyEvents(h).at(-1).detail.field, 'engineering', 'the new field is announced');
}
{
  const h = mk({ saved: 'engineering_measurement' });
  await flush();
  (h.get('startup-change').listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.get('startup').hidden, false, 'picker is open before Escape');
  const onKey = (h.listeners || {}).keydown || [];
  assert.ok(onKey.length >= 1, 'Escape is wired on document');
  onKey.forEach((fn) => fn({ key: 'Escape' }));
  await flush();
  assert.equal(h.store.get('wb.pack'), 'engineering_measurement', 'Escape keeps the previous field');
  assert.equal(readyEvents(h).at(-1).detail.field, 'engineering', 'Escape restores the previous field');
  assert.equal(h.get('startup-menu').hidden, false, 'Escape returns to the home menu');
}

// --- 11. Blocked storage still opens a usable session and says so.
{
  const h = mk({ storageBlocked: true });
  assert.equal(h.get('startup').hidden, false, 'blocked storage falls back to the picker');
  h.press('social_science');
  await flush();
  assert.equal(readyEvents(h).length, 1, 'blocked storage still seats the field');
  assert.equal(h.get('startup-menu').hidden, false, 'blocked storage still reaches the home menu');
  assert.ok(/refused to store/i.test(h.get('startup-status').textContent), 'the storage failure is stated, not hidden');
}

// --- 12. Reduced motion settles the same contract with no animation.
{
  const h = mk({ saved: 'social_science', reduced: true });
  await flush();
  assert.equal(readyEvents(h).length, 1, 'reduced motion still announces the field');
  assert.equal(h.get('startup-menu').hidden, false, 'reduced motion still reaches the home menu');
  assert.equal(h.get('portal').hidden, true, 'reduced motion does not open the workspace by itself');
  assert.equal(h.animations.length, 0, 'reduced motion schedules no animation');
}

// --- 13. The gate resolves so app.js can start; it never rejects.
{
  const h = mk({ saved: 'social_science' });
  const resolved = await vm.runInContext('window.WB_STARTUP.ready.then(function (v) { return v; })', h.context);
  assert.equal(resolved, 'social_science', 'the ready gate resolves with the chosen field');
  assert.equal(vm.runInContext('typeof window.WB_STARTUP.change', h.context), 'function', 'change is exposed for the workspace control');
  assert.equal(vm.runInContext('typeof window.WB_STARTUP.backToMenu', h.context), 'function',
    'backToMenu is exposed so the workspace can always return to the menu');
}

// --- 13b. The menu is reachable from the open workspace: one key, or one
// button. Opening a menu is a one-way door otherwise, and a person who cannot
// get back to the four tiles is stranded outside the shell they started from.
{
  const h = mk({ saved: 'social_science' });
  await flush();
  const tile = (h.get('home-menu').children || []).find((b) => b.getAttribute('data-menu') === 'research');
  (tile.listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.get('portal').hidden, false, 'a menu opens the workspace');
  assert.equal(h.get('startup').hidden, true, 'the console is dismissed while the workspace is open');

  // Escape on the plain surface returns to the menu.
  (h.listeners.keydown || []).forEach((fn) => fn({ key: 'Escape' }));
  await flush();
  assert.equal(h.get('portal').hidden, true, 'Escape returns the display into the console');
  assert.equal(h.get('startup').hidden, false, 'the console is visible again on the menu');
  assert.equal(h.get('startup-menu').hidden, false, 'the four tiles are reachable again');
  assert.equal(h.get('startup').classList.contains('home'), true, 'the console is in its home state');

  // Returning is navigation, not a preference change: nothing is rewritten and
  // the field is unchanged, so the same research field reopens.
  assert.equal(h.store.get('wb.pack'), 'social_science', 'returning to the menu writes no preference');
  assert.equal(readyEvents(h).filter((e) => e.detail.field !== 'social').length, 0,
    'no other field is announced by returning to the menu');

  // An open dialog owns Escape first: the menu must not steal it.
  const p2 = mk({ saved: 'social_science' });
  await flush();
  const t2 = (p2.get('home-menu').children || []).find((b) => b.getAttribute('data-menu') === 'research');
  (t2.listeners.click || []).forEach((fn) => fn({}));
  await flush();
  const realQueryAll = p2.context.document.querySelectorAll;
  const onScreen = { hidden: false, parentElement: null, getClientRects: () => [{}] };
  p2.context.document.querySelectorAll = (sel) => (/role="dialog"/.test(sel) ? [onScreen] : realQueryAll(sel));
  (p2.listeners.keydown || []).forEach((fn) => fn({ key: 'Escape' }));
  await flush();
  assert.equal(p2.get('portal').hidden, false, 'a dialog keeps Escape: the workspace is not collapsed from under it');
  // A dialog that is mounted but hidden (the shipped settings sheet lives inside a
  // hidden layer) must NOT keep Escape: it is not on screen to be closed.
  onScreen.getClientRects = () => [];
  (p2.listeners.keydown || []).forEach((fn) => fn({ key: 'Escape' }));
  await flush();
  assert.equal(p2.get('portal').hidden, true, 'a hidden dialog does not swallow Escape');
  p2.context.document.querySelectorAll = realQueryAll;

  // The visible control does the same thing, for a person not using the keyboard.
  const menuButton = h.get('back-to-menu');
  assert.ok(menuButton, 'the workspace carries a home-menu control');
  assert.equal(menuButton.hidden, false, 'the control is shown once the workspace exists');
  assert.ok(html.includes('data-testid="back-to-menu"'), 'the control is in the shipped markup');
  assert.ok(/WB_STARTUP &amp;&amp; WB_STARTUP\.backToMenu\(\)/.test(html), 'the control calls the same entry point');
  (menuButton.listeners.click || []).forEach((fn) => fn({}));
  await flush();
  assert.equal(h.get('startup-menu').hidden, false, 'the control returns to the menu too');
}

// --- 13c. The home tiles are a real 2x2 square grid and stay one.
{
  const homeCss = await read('../frontend/startup.css');
  assert.ok(/grid-template-columns:\s*repeat\(2/.test(homeCss), 'the tiles are laid out two per row');
  assert.ok(/grid-template-rows:\s*repeat\(2/.test(homeCss), 'and two per column: a true 2x2 grid, not four bars');
  assert.ok(/aspect-ratio:\s*1\s*\/\s*1/.test(homeCss), 'each tile is square');
  assert.equal((startup.match(/var MENUS = \[/g) || []).length, 1, 'one menu table drives the grid');
  assert.equal((startup.match(/key: '[a-z]+', label:/g) || []).length, 4,
    'exactly four menus, matching the hardcoded two-column key map (HOME_COLS)');

  // The four destinations follow research-workflow order: find the literature,
  // work the data, write it up, then review the conversations that led there.
  // Reading order is left-to-right, top-to-bottom, so this is the order a person
  // meets the tiles: Literature Review, Research / Writing / Conversation Review.
  const menuKeys = [...startup.matchAll(/\{ key: '([a-z]+)', label: '([^']+)'/g)].map(m => m[1]);
  assert.deepEqual(menuKeys, ['library', 'research', 'writing', 'review'],
    'the tiles are ordered Literature Review, Research, Writing, Conversation Review');
  // Keys, labels, open functions and testids are unchanged: only the order moved.
  const menuRows = [...startup.matchAll(/\{ key: '([a-z]+)', label: '([^']+)', note: '([^']+)', open: '([a-zA-Z]+)' \}/g)]
    .map(m => ({ key: m[1], label: m[2], open: m[4] }));
  assert.deepEqual(menuRows, [
    { key: 'library', label: 'Literature Review', open: 'openLibraryView' },
    { key: 'research', label: 'Research', open: 'showResearchView' },
    { key: 'writing', label: 'Writing', open: 'openWritingView' },
    { key: 'review', label: 'Conversation Review', open: 'openSecretaryReview' },
  ], 'each key keeps the label and the view function it always had');
  // The keyboard map is row-length driven, so reordering must not change the axes.
  assert.ok(/ArrowRight'\) return from \+ 1/.test(startup), 'Right still steps one tile');
  assert.ok(/ArrowDown'\) return from \+ HOME_COLS/.test(startup), 'Down still steps one row');
  assert.ok(/var HOME_COLS = 2/.test(startup), 'the grid is still two columns wide');
}

// --- 14. The cartridge is inserted, not faded in: it enters the bay, and the
// display lights only once it is home. A fade would read as a panel appearing.
{
  assert.ok(css.includes('@keyframes startup-chip-insert'), 'the insertion has its own keyframes');
  const keyframes = css.slice(css.indexOf('@keyframes startup-chip-insert'),
    css.indexOf('@media (prefers-reduced-motion: reduce)'));
  assert.ok(/\.startup-card\.on\s*\{[^}]*animation:\s*startup-chip-insert/.test(css),
    'the cartridge runs that insertion when it is pushed in');
  const durationRule = (css.match(/\.startup-card\.on\s*\{\s*animation:\s*startup-chip-insert\s+(\d+)ms/) || [])[1];
  const duration = Number(durationRule);
  assert.ok(duration > 0, 'the insertion has a real duration, not a static swap');
  // It enters the bay: the cartridge starts taller and above its resting lip, and
  // settles on the lip the slot keeps. Height and top only.
  assert.ok(/0%\s*\{[^}]*top:\s*calc\(var\(--card-rest-y\) - [\d.]+%\)/.test(keyframes),
    'the cartridge starts above its resting position, standing out of the slot');
  assert.ok(/0%\s*\{[^}]*height:\s*[\d.]+%/.test(keyframes), 'and taller than the lip it leaves behind');
  assert.ok(/100%\s*\{[^}]*top:\s*var\(--card-rest-y\)[^}]*height:\s*1\.8%/.test(keyframes),
    'it settles exactly on the resting lip');
  assert.ok(!/transform:/.test(keyframes), 'the cartridge is not scaled to fake insertion');
  assert.ok(!/translate/.test(keyframes), 'it does not slide across the console face');
  const reducedBlock = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
  assert.ok(reducedBlock.length > 0, 'reduced motion is honoured for the insertion');
  assert.ok(/\.startup-card\.on\s*\{[^}]*animation:\s*none/.test(reducedBlock), 'reduced motion skips the insertion');
  assert.ok(/\.startup-card\.on\s*\{[^}]*opacity:\s*1/.test(reducedBlock), 'the seated cartridge is still shown');
  // The reveal must not start before the cartridge is home, or the display grows
  // over a cartridge that is still descending.
  const insertMs = Number((startup.match(/var INSERT_MS = (\d+);/) || [])[1]);
  assert.ok(insertMs >= duration, `the display waits for the insertion: INSERT_MS ${insertMs} >= ${duration}ms`);
  // Both entry points seat a cartridge: an explicit choice and a remembered field.
  const boot = startup.slice(startup.indexOf('var saved = readPack();'));
  assert.ok(/setCard\(true\)/.test(boot) && /sleep\(INSERT_MS\)/.test(boot),
    'a remembered field also seats its cartridge instead of only powering on');
}

console.log('STARTUP_INTEGRATION_PASS: approved PNG bytes unchanged and clipped by the silhouette; overlay anchors derived from source pixels; real app mounted inside #portal (no mock workspace); app.js gated on WB_STARTUP.ready; first use offers both fields and writes nothing; insertion persists once, fires research-field-ready when the field seats, and shows the home menu with the workspace still closed; choosing a menu grows the display into the workspace; the cartridge is pushed into the bay (height/top only, never scaled) and the display lights after it seats; saved field skips the picker and lands on the home menu; corrupt preference falls back; change/cancel restore the field without touching data and cancel opens nothing; blocked storage still reaches the menu; reduced motion settles without animation.');
