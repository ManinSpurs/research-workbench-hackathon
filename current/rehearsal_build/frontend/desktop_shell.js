/* Presentation bridge only. Never reads project content or calls an AI provider. */
(function () {
  'use strict';
  var desktop = window.WB_DESKTOP;
  if (!desktop || !desktop.isDesktop) return;
  document.documentElement.classList.add('desktop-shell');
  document.addEventListener('DOMContentLoaded', function () {
    var stage = document.getElementById('startup-stage');
    var portal = document.getElementById('portal');
    var lastMode = null;
    var lastIgnore = null;
    function controls(parent, cls) {
      if (!parent) return;
      var group = document.createElement('div');
      group.className = 'desktop-window-controls ' + cls;
      [['minimize', '\u2212', 'Minimize window'], ['close', '\u00d7', 'Close window']].forEach(function (item) {
        var button = document.createElement('button');
        button.type = 'button'; button.textContent = item[1];
        button.setAttribute('aria-label', item[2]); button.title = item[2];
        button.addEventListener('click', function () { desktop.windowControl(item[0]); });
        group.appendChild(button);
      });
      parent.appendChild(group);
    }
    if (stage) {
      var drag = document.createElement('div');
      drag.className = 'desktop-console-drag'; drag.setAttribute('aria-hidden', 'true');
      stage.appendChild(drag);
      controls(stage, 'desktop-console-controls');
    }
    controls(document.querySelector('#app-shell > header'), 'desktop-workspace-controls');
    function mode() {
      var next = (portal && !portal.hidden) || homeFillsWindow() ? 'menu' : 'home';
      if (next === lastMode) return;
      lastMode = next; lastIgnore = false;
      desktop.setWindowSize(next);
      desktop.ignorePointer(false);
    }
    // The existing startup state machine still owns all navigation and preserves
    // the mounted workspace. Observe its portal instead of adding another router.
    if (portal) new MutationObserver(mode).observe(portal, { attributes: true, attributeFilter: ['hidden'] });
    var startupRoot = document.getElementById('startup');
    if (startupRoot) new MutationObserver(mode).observe(startupRoot, { attributes: true, attributeFilter: ['class', 'hidden'] });
    mode();
    // True while the home menu owns the whole window (frontend/home_expand.css).
    // The console exterior is not painted then, so there is no bezel silhouette
    // left to test a point against: every part of the window is the console's own
    // screen and must keep accepting pointer events, corners included.
    function homeFillsWindow() {
      var startup = document.getElementById('startup');
      return Boolean(startup && !startup.hidden && startup.classList.contains('home'));
    }
    document.addEventListener('mousemove', function (event) {
      if (lastMode === 'menu') return;
      if (homeFillsWindow()) {
        if (lastIgnore !== false) { lastIgnore = false; desktop.ignorePointer(false); }
        return;
      }
      var target = event.target;
      var interactive = target && target.closest && target.closest(
        '.startup-choices, .startup-cancel, .settings-sheet, .desktop-window-controls, .desktop-console-drag');
      var inside = Boolean(interactive);
      var silhouette = document.querySelector('#approvedHardwareSilhouette path');
      // Reuse the exact SVG clipping path: the original raster stays untouched.
      if (!inside && silhouette && silhouette.getScreenCTM) {
        var matrix = silhouette.getScreenCTM();
        if (matrix) {
          var point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
          inside = silhouette.isPointInFill(point);
        }
      }
      var ignore = !inside;
      if (ignore !== lastIgnore) {
        lastIgnore = ignore;
        desktop.ignorePointer(ignore);
      }
    });
  });
})();
