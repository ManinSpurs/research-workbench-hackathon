// Startup cartridge tray in Korean: a built-in pack is authored by the app, so
// its server title and tagline are translated; a chip the researcher installed
// keeps its own words exactly as written, in either language.
//
// No browser and no network: the real frontend/i18n.js and startup.js run in a VM
// with a small DOM double, and loadPacks is fed the shapes the live backend
// answers (/domain-packs rows have no `source`; /chips rows carry it). Only those
// two scripts are loaded, so a Korean string that works here is owned by the
// start screen's own registration rather than borrowed from i18n.ko.js.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const i18n = await read('../frontend/i18n.js');
const startup = await read('../frontend/startup.js');

// The built-ins exactly as GET /domain-packs + /chips describe them.
const BUILTIN = [
  { pack_id: 'social_science', title: 'Social Science', tagline: 'Constructs, coverage, and the estimand' },
  { pack_id: 'engineering_measurement', title: 'Engineering: Measurement', tagline: 'Quantity, unit, condition, uncertainty' },
];
// A researcher's own chip. Its title and tagline are deliberately strings that DO
// have Korean entries in the app's dictionaries ('Literature Review', 'Writing'),
// so this assertion can only pass while an authored chip bypasses st() entirely.
const AUTHORED = { pack_id: 'urban_policy', title: 'Literature Review', tagline: 'Writing' };

// A chip whose name collides with nothing; used for the fallback cases.
const PLAIN = { pack_id: 'coastal_adaptation', title: 'Coastal Adaptation', tagline: 'Tides, barriers, retreat' };

const settle = async () => { for (let i = 0; i < 120; i++) await new Promise((r) => setImmediate(r)); };

// One start screen, booted against a scripted pack list, at one language.
async function boot({ lang = 'en', packs, chips }) {
  const elements = new Map();
  const store = new Map([['wb.lang', lang]]);
  const dispatched = [];
  const HIDDEN_AT_LOAD = new Set(['portal', 'startup-change', 'startup-cancel']);
  const mkEl = (id) => ({
    id, hidden: HIDDEN_AT_LOAD.has(id), inert: false, disabled: false, textContent: '',
    className: '', childElementCount: 0, children: [], style: { setProperty() {} },
    attrs: {}, dataset: {},
    classList: {
      set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
      toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); }, contains(c) { return this.set.has(c); },
    },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); this.childElementCount = this.children.length; return c; },
    addEventListener(t, fn) { (this.listeners = this.listeners || {}), (this.listeners[t] = this.listeners[t] || []).push(fn); },
    focus() { this.focused = true; },
    getBoundingClientRect() { return { left: 10, top: 20, width: 100, height: 60 }; },
    animate() { return { finished: Promise.resolve(), cancel() {} }; },
  });
  const get = (id) => { if (!elements.has(id)) elements.set(id, mkEl(id)); return elements.get(id); };
  const choices = () => get('startup-choices').children;
  const document = {
    readyState: 'complete',
    documentElement: mkEl('html'),
    body: mkEl('body'),
    getElementById: get,
    createElement(tag) { return mkEl('created-' + tag); },
    querySelectorAll(sel) {
      if (sel === '[data-startup-field]') return choices().slice();
      return [];
    },
    querySelector(sel) { return sel === '[data-startup-field]' ? choices()[0] || null : null; },
    addEventListener() {},
  };
  const fetch = (url) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, '');
    const answer = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    if (path === '/domain-packs') return answer({ packs });
    if (path === '/chips') return answer({ chips });
    return Promise.resolve({ ok: false, json: () => Promise.resolve(null) });
  };
  const window = {
    innerWidth: 1200, innerHeight: 800, visualViewport: null,
    addEventListener() {}, dispatchEvent(e) { dispatched.push(e); },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    fetch,
  };
  const context = vm.createContext({
    window, document, URLSearchParams, fetch,
    location: { search: '' },
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail; } },
    localStorage: {
      getItem(k) { return store.has(k) ? store.get(k) : null; },
      setItem(k, v) { store.set(k, v); },
      removeItem(k) { store.delete(k); },
    },
    setTimeout(fn) { Promise.resolve().then(fn); return 1; },
    Object, Promise, String, Math, Set, JSON, Array,
  });
  vm.runInContext(i18n, context);
  vm.runInContext(startup, context);
  await settle();
  const button = (pack) => choices().find((b) => b.getAttribute('data-startup-field') === pack);
  const trayRow = (pack) => {
    const b = button(pack);
    assert.ok(b, `the tray shows a cartridge for ${pack}`);
    return { name: b.children[0].textContent, note: b.children[1].textContent };
  };
  const press = async (pack) => {
    const b = button(pack);
    assert.ok(b, `choice ${pack} rendered`);
    (b.listeners.click || []).forEach((fn) => fn({}));
    await settle();
  };
  const readyEvents = () => dispatched.filter((e) => e.type === 'research-field-ready');
  return { trayRow, press, readyEvents, store, homeField: () => get('home-field').textContent };
}

const DOMAIN_PACKS = (rows) => rows.map((r) => ({ pack_id: r.pack_id, version: '1.0.0', title: r.title }));
const builtinChips = BUILTIN.map((r) => ({ ...r, source: 'builtin' }));

// --- 1. Korean with a live server: the built-ins read Korean, the chip does not.
{
  const h = await boot({
    lang: 'ko',
    packs: DOMAIN_PACKS([...BUILTIN, AUTHORED, PLAIN]),
    chips: [...builtinChips,
      { ...AUTHORED, source: 'file' },
      { ...PLAIN, source: 'file' }],
  });
  assert.deepEqual(h.trayRow('social_science'), { name: '사회과학', note: '구성개념, 포괄 범위, 추정 대상' },
    'the social-science built-in is Korean: title and tagline both');
  assert.deepEqual(h.trayRow('engineering_measurement'), { name: '공학: 측정', note: '수량, 단위, 조건, 불확실성' },
    'the engineering built-in is Korean under its own server title');
  assert.deepEqual(h.trayRow('coastal_adaptation'), { name: 'Coastal Adaptation', note: 'Tides, barriers, retreat' },
    'an untranslated chip with no dictionary entry stays verbatim');
  assert.deepEqual(h.trayRow('urban_policy'), { name: 'Literature Review', note: 'Writing' },
    'an authored chip whose words DO have Korean entries is still shown verbatim');
  assert.equal(h.homeField(), 'No research field'.replace('No research field', '연구 분야 없음'),
    'before a pick, the home header has no field to name');

  await h.press('engineering_measurement');
  assert.equal(h.homeField(), '공학: 측정', 'the home header names the seated built-in in Korean');
  const seated = h.readyEvents().at(-1).detail;
  assert.equal(seated.field, 'engineering_measurement', 'detail.field stays the English pack_id machine value');
  assert.equal(seated.label, 'Engineering: Measurement', 'detail.label stays the authored English title');
  assert.equal(h.store.get('wb.pack'), 'engineering_measurement', 'wb.pack stores the English pack_id, never a translation');
}

// --- 2. Korean, seating the researcher's own chip: the header is verbatim too.
{
  const h = await boot({
    lang: 'ko',
    packs: DOMAIN_PACKS([...BUILTIN, AUTHORED]),
    chips: [...builtinChips, { ...AUTHORED, source: 'file' }],
  });
  await h.press('urban_policy');
  assert.equal(h.homeField(), 'Literature Review', 'the home header never translates an authored chip');
  assert.equal(h.readyEvents().at(-1).detail.label, 'Literature Review', 'the authored title reaches the app unchanged');
  assert.equal(h.store.get('wb.pack'), 'urban_policy', 'the authored pack_id is what gets stored');
}

// --- 3. English is byte-identical to the server's own words.
{
  const h = await boot({
    lang: 'en',
    packs: DOMAIN_PACKS([...BUILTIN, AUTHORED]),
    chips: [...builtinChips, { ...AUTHORED, source: 'file' }],
  });
  assert.deepEqual(h.trayRow('social_science'), { name: 'Social Science', note: 'Constructs, coverage, and the estimand' },
    'English shows the built-in exactly as the server names it');
  assert.deepEqual(h.trayRow('engineering_measurement'), { name: 'Engineering: Measurement', note: 'Quantity, unit, condition, uncertainty' },
    'English shows the engineering built-in and its tagline unchanged');
  assert.deepEqual(h.trayRow('urban_policy'), { name: 'Literature Review', note: 'Writing' },
    'English shows the authored chip unchanged');
  await h.press('social_science');
  assert.equal(h.homeField(), 'Social Science', 'the English home header is the server title');
}

// --- 4. A built-in whose title or tagline the server changed is never given the
// old translation: the lookups miss and the server's own words survive.
{
  const h = await boot({
    lang: 'ko',
    packs: DOMAIN_PACKS([{ pack_id: 'social_science', title: 'Social Science: Extended' }]),
    chips: [{ pack_id: 'social_science', title: 'Social Science: Extended', source: 'builtin',
              tagline: 'Networks and panel structure' }],
  });
  assert.deepEqual(h.trayRow('social_science'), { name: 'Social Science: Extended', note: 'Networks and panel structure' },
    'an unknown built-in title falls back to the server text, not to a stale translation');
}

// --- 5. A row the server did not label keeps the researcher's words: anything
// not explicitly `builtin` is treated as authored.
{
  const h = await boot({
    lang: 'ko',
    packs: DOMAIN_PACKS([{ pack_id: 'urban_policy', title: 'Literature Review' }]),
    chips: [{ pack_id: 'urban_policy', title: 'Literature Review', tagline: 'Writing' }],
  });
  assert.deepEqual(h.trayRow('urban_policy'), { name: 'Literature Review', note: 'Writing' },
    'a chip with no source field at all is shown verbatim, never translated');

  // The /chips answer missing entirely is the same situation, not an error.
  const noChips = await boot({
    lang: 'ko',
    packs: DOMAIN_PACKS([...BUILTIN, AUTHORED]),
    chips: [],
  });
  // The line the screen writes itself is Korean; the name it interpolates is the
  // chip's own and stays verbatim, so no part of a chip's words is translated.
  assert.deepEqual(noChips.trayRow('engineering_measurement'),
    { name: 'Engineering: Measurement', note: 'Engineering: Measurement 카트리지를 넣습니다' },
    'without a /chips answer every row keeps its server title verbatim');
}

console.log('STARTUP_PACK_I18N_PASS: source:builtin rows are translated in ko (Social Science and Engineering: Measurement, with both taglines) while file and unlabelled rows, including one whose text collides with app strings, stay verbatim; the home header follows the same rule; an unknown built-in title falls back to the server text; detail.field/detail.label/wb.pack stay English; en shows the server words unchanged.');
