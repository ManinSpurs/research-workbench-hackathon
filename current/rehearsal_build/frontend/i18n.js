// Language setting: English / 한국어.
//
// The stored choice is the only preference this file owns (key `wb.lang`, values
// 'en' | 'ko'). English is the default and the authored language of the source,
// so a missing translation falls back to the English sentence the code already
// wrote — never to a key name and never to a blank.
//
// Translation keys ARE the English sentence. That keeps the source readable and
// means a translator can work from the running screen without a catalogue of
// invented identifiers. Numbers and names come in through {placeholders}, so a
// sentence keeps its own word order in each language.
//
// Static markup is marked with data-i18n; the authored English is captured on
// first apply and used as the key. Dynamic text goes through WB_I18N.t().
(function () {
  'use strict';

  var KEY = 'wb.lang';
  var LANGS = ['en', 'ko'];
  var dicts = { en: {}, ko: {
    'Muse Spark Open (Modal Shared)': 'Muse Spark Open (Modal 공유)'
  } };
  var lang = read();
  var listeners = [];
  var statics = null;

  function isLang(v) { return LANGS.indexOf(v) >= 0; }

  function read() {
    try { var saved = localStorage.getItem(KEY); return isLang(saved) ? saved : 'en'; } catch (_) { return 'en'; }
  }

  // Register or extend one language's table. Called by each area's own
  // dictionary file, so two areas never have to edit the same object literal.
  function register(code, table) {
    if (!isLang(code) || !table) return;
    var target = dicts[code];
    for (var k in table) {
      if (Object.prototype.hasOwnProperty.call(table, k)) target[k] = table[k];
    }
    if (code === lang) apply();
  }

  function has(table, key) { return Object.prototype.hasOwnProperty.call(table, key); }

  // t('Ask a research question.') -> that sentence in the current language.
  // Missing entry: the English key itself, which is the source sentence.
  function t(key, vars) {
    var table = dicts[lang] || {};
    var out = has(table, key) ? table[key] : key;
    if (vars) {
      out = out.replace(/\{(\w+)\}/g, function (m, name) {
        return has(vars, name) ? String(vars[name]) : m;
      });
    }
    return out;
  }

  // Collect the authored English once; it is the key for every later pass, so
  // switching back to English restores the original sentences exactly.
  //
  // Only leaf nodes are translated by their text. A node that wraps an input, a
  // select or any other element keeps its markup untouched, because replacing
  // textContent on such a node would delete the control inside it. Those labels
  // carry an inner <span data-i18n> around the words instead. A node that marks
  // data-i18n-attrs is collected even when it has children: its attributes are
  // translated, its markup is not touched.
  function collect() {
    statics = [];
    var nodes = document.querySelectorAll('[data-i18n], [data-i18n-attrs]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var attrs = el.getAttribute('data-i18n-attrs');
      var hasText = el.hasAttribute('data-i18n');
      // A node that wraps a control is never rewritten by its text; if it also
      // asks for its attributes to be translated, only those are.
      if (hasText && el.children && el.children.length) hasText = false;
      if (!hasText && !attrs) continue;
      var captured = el.getAttribute('data-i18n-key');
      var key = captured == null ? el.textContent : captured;
      if (hasText && captured == null) el.setAttribute('data-i18n-key', key);
      statics.push({ el: el, key: key, text: hasText });
    }
  }

  // Re-translate every marked node, plus the attributes that carry user-facing
  // words (placeholder, title, aria-label) when a node asks for them with
  // data-i18n-attrs="placeholder title".
  function applyStatics() {
    if (!statics) collect();
    for (var i = 0; i < statics.length; i++) {
      var item = statics[i];
      var el = item.el;
      if (item.text) {
        var text = t(item.key);
        if (el.textContent !== text) el.textContent = text;
      }
      var attrs = el.getAttribute('data-i18n-attrs');
      if (!attrs) continue;
      var names = attrs.split(/\s+/);
      for (var j = 0; j < names.length; j++) {
        var name = names[j];
        if (!name) continue;
        var store = 'i18n-attr-' + name;
        var original = el.getAttribute(store);
        if (original == null) { original = el.getAttribute(name); if (original == null) continue; el.setAttribute(store, original); }
        el.setAttribute(name, t(original));
      }
    }
  }

  function apply() {
    var root = document.documentElement;
    if (root && root.setAttribute) {
      root.setAttribute('lang', lang);
      if (root.dataset) root.dataset.lang = lang;
    }
    var select = document.getElementById('lang-select');
    if (select) select.value = lang;
    applyStatics();
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](lang); } catch (_) {}
    }
  }

  function set(next) {
    lang = isLang(next) ? next : 'en';
    try { localStorage.setItem(KEY, lang); } catch (_) {}
    apply();
  }

  // Anything rendered by script registers here so a language switch redraws it
  // rather than leaving half the screen in the previous language.
  function onChange(fn) { if (typeof fn === 'function') listeners.push(fn); }

  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { statics = null; apply(); });
  } else {
    statics = null; apply();
  }

  window.WB_I18N = {
    get: function () { return lang; },
    set: set,
    t: t,
    register: register,
    onChange: onChange,
    apply: function () { statics = null; apply(); },
    key: KEY
  };
})();
