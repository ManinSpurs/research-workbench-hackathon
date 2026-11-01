// Theme setting: System / Light / Dark.
//
// The stored choice is the only preference this file owns (key `wb.theme`, values
// 'system' | 'light' | 'dark'). The choice is resolved here and written to
// document.documentElement.dataset.theme as the concrete 'light' or 'dark' value —
// 'system' never reaches the attribute, because a missing/garbled attribute must
// still fall back to a real palette. style.css selects the dark palette from that
// hook, so the manual setting wins over the OS.
//
// This script is loaded in <head> so the ground is set before first paint and the
// workspace never flashes the wrong theme.
(function () {
  'use strict';

  var KEY = 'wb.theme';
  var CHOICES = ['system', 'light', 'dark'];
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var choice = read();

  function isChoice(v) { return CHOICES.indexOf(v) >= 0; }

  function read() {
    try { var saved = localStorage.getItem(KEY); return isChoice(saved) ? saved : 'system'; } catch (_) { return 'system'; }
  }

  // 'system' follows the OS preference; the concrete value is what gets applied.
  function resolve(c) {
    if (c === 'light') return 'light';
    if (c === 'dark') return 'dark';
    return media && media.matches ? 'dark' : 'light';
  }

  function apply() {
    var concrete = resolve(choice);
    var root = document.documentElement;
    if (root && root.dataset) root.dataset.theme = concrete;
    var select = document.getElementById('theme-select');
    if (select) select.value = choice;
  }

  function set(next) {
    choice = isChoice(next) ? next : 'system';
    try { localStorage.setItem(KEY, choice); } catch (_) {}
    apply();
  }

  if (media) {
    var onChange = function () { if (choice === 'system') apply(); };
    if (media.addEventListener) media.addEventListener('change', onChange);
    else if (media.addListener) media.addListener(onChange);
  }

  apply();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();

  window.WB_THEME = { get: function () { return choice; }, set: set, apply: apply, key: KEY };
})();
