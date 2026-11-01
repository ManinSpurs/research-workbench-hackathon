/* Research Secretary interior: keeps the message-mode control present and in
   step with the composer wherever the composer currently is.

   app.js owns the composer: the routing decision (composerMode), the control it
   builds inside #secretary-compose, and every sentence that explains it. This
   file owns nothing of its own; it exists so the control is rebuilt and
   refreshed on two events app.js cannot see on its own:

   - the page is ready and app.js ran before the workspace settled, and
   - home_hub.js physically moves the single #secretary-compose form between the
     home screen and the aside.

   It never clones the form, never creates a second input, and never decides what
   a send means. If app.js is absent, every call is a no-op. */
(function () {
  'use strict';
  var doc = document;

  function sync() {
    try {
      if (typeof window.syncComposerMode === 'function') window.syncComposerMode();
    } catch (_) { /* a composer that cannot be drawn yet is not an error here */ }
  }

  function observe() {
    if (typeof MutationObserver !== 'function') return;
    var slots = [doc.getElementById('home-composer-slot'),
      doc.querySelector('aside[aria-label="Research Secretary"]')];
    var observer = new MutationObserver(sync);
    for (var i = 0; i < slots.length; i++) {
      if (slots[i]) observer.observe(slots[i], { childList: true });
    }
  }

  function start() { sync(); observe(); }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start);
  else start();

  window.WB_INTERIOR_SECRETARY = { sync: sync };
})();
