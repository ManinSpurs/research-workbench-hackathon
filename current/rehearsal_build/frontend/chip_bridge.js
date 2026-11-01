/*
 * chip_bridge.js — bridge between the native macOS menu and the web renderer.
 *
 * Plain browser script: no imports, no modules, no build step. Loaded by
 * index.html after the app scripts. Safe in a plain browser: when
 * window.WB_DESKTOP does not exist (no Electron preload) this does nothing.
 *
 * Layer: desktop shell only. It never translates text and never renders UI;
 * it just forwards a chosen .chip path to window.WB_CHIP_HOOK and a "new chip"
 * request to window.WB_CHIP_NEW_HOOK, both of which renderer code defines.
 * Nothing is queued when a hook is missing: the event is simply ignored, and
 * the researcher can repeat it from the 파일 menu.
 *
 * window.WB_DESKTOP (injected by electron/preload.js) stays untouched, so
 * renderer code can call WB_DESKTOP.setWindowSize('boot' | 'home' | 'menu').
 *
 * Audio note: inside Electron, playback is allowed without a user gesture,
 * unlike a browser tab where the autoplay policy blocks it. No audio is wired
 * up here.
 */
(function () {
  'use strict';

  var desktop = window.WB_DESKTOP;
  if (!desktop) return;

  if (typeof desktop.onChipOpened === 'function') {
    desktop.onChipOpened(function (payload) {
      var filePath = payload && typeof payload.path === 'string' ? payload.path : '';
      if (!filePath) return;
      if (typeof window.WB_CHIP_HOOK === 'function') {
        window.WB_CHIP_HOOK(filePath, payload);
      }
    });
  }

  if (typeof desktop.onNewChip === 'function') {
    desktop.onNewChip(function () {
      if (typeof window.WB_CHIP_NEW_HOOK === 'function') {
        window.WB_CHIP_NEW_HOOK();
      }
    });
  }
})();
