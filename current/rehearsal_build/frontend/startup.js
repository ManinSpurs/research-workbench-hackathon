// Cartridge start screen: first-run field choice, then the inner display expands
// into the existing workspace mounted in #portal. Extracted from the approved
// Research_Cartridge_Starter_v4 handoff and reduced to the app's own field set.
//
// Contracts kept from the handoff:
//   - The approved Concept A PNG is used unchanged, clipped by its orthogonal
//     silhouette, at the image-space anchors in ART_ANCHORS.
//   - Only the inner display grows; the console is never scaled.
//   - `research-field-ready` fires on window only after expansion settles, and
//     carries detail.field (the seated pack_id) and detail.label (its title).
//   - The cartridge keeps its physical size through insertion.
//   - Reduced motion and resize both settle without leaving a half-open portal.
//
// Differences from the demo, required by INTEGRATION.md:
//   - The mock workspace is gone. The real app already sits in #portal/#app-shell
//     and is revealed, not remounted, so an open project survives field changes.
//   - The field preference is the app's own wb.pack key, which app.js also reads
//     and writes. No second preference store is introduced, and the demo notes
//     and fieldnote.cartridge.v1 key are not migrated.
//   - A field change only reselects the research context. It never touches
//     projects, questions, documents or stored data.
(function () {
  'use strict';

  var KEY = 'wb.pack';
  var SOURCE = 'fieldnote-cartridge';
  var READY_EVENT = 'research-field-ready';
  // How long the cartridge takes to seat (frontend/style.css :startup-chip-insert).
  // The display lights only once it is home, so the reveal never races the insert.
  var INSERT_MS = 440;

  // Image-space coordinates of the unchanged 1536x1024 approved PNG.
  var ART_ANCHORS = Object.freeze({
    crop: { x: 24, y: 170, width: 1488, height: 656 },
    display: { x: 333, y: 261, width: 871, height: 486 },
    slot: { centerX: 1179, restTop: 173 }
  });

  // Language lookup from i18n.js, with an English fallback so the start screen
  // still runs where that script is absent. The alias has its own name because
  // app.js already owns `t` at the top level of the page.
  var SU_GLOBAL = (typeof window !== 'undefined') ? window : {};
  var st = (SU_GLOBAL.WB_I18N && SU_GLOBAL.WB_I18N.t) ? SU_GLOBAL.WB_I18N.t : function (key, vars) {
    if (!vars) return key;
    return String(key).replace(/\{(\w+)\}/g, function (m, name) {
      return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m;
    });
  };

  // The two entries here are samples that ship with the app, never the whole list.
  // The real list comes from the server (GET /domain-packs), which includes every
  // chip the researcher installed; see adoptPacks() and loadPacks().
  var FIELDS = {
    // `label` stays the authored English name: it is what research-field-ready
    // carries to the rest of the app, and a machine-readable field name rather
    // than screen text. Every sentence that shows it runs it through st().
    social_science: { field: 'social', label: 'Social Science' },
    engineering_measurement: { field: 'engineering', label: 'Engineering' }
  };

  // The server marks its own packs `source: "builtin"` (GET /chips). Those are
  // authored here in the app, exactly like the two sample names above, so they
  // are translated like them. Every other row — a chip the researcher installed,
  // or one whose source the server did not say — keeps its own words verbatim:
  // machine-translating a researcher's title would be putting words in their
  // mouth, so an unknown source is treated as researcher-authored.
  function serverEntry(row, note, source, artUrl) {
    return {
      field: row.pack_id,
      label: row.title || row.pack_id,
      note: note || '',
      raw: source !== 'builtin',
      // A ready-to-load picture URL from the server, or '' when the chip has none.
      art: artUrl || ''
    };
  }

  // Replace the sample list with the packs the server actually offers. A chip made
  // for urban policy becomes a cartridge like any other, which is the whole point
  // of a chip. `notes` maps pack_id to the chip's tagline, `sources` to its
  // `source` (GET /chips is the only route that reports a source) and `arts` to
  // the chip's own picture, which is drawn on its face when it has one.
  function adoptPacks(rows, notes, sources, arts) {
    if (!rows || !rows.length) return false;
    var next = {};
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row.pack_id !== 'string') continue;
      next[row.pack_id] = serverEntry(row, notes && notes[row.pack_id],
        sources && sources[row.pack_id], arts && arts[row.pack_id]);
    }
    if (!Object.keys(next).length) return false;
    FIELDS = next;
    // The tray is redrawn, not merely relabelled: a chip the samples could not
    // show has no button yet to relabel.
    buildChoices();
    if (selected) markSelection(selected);
    lock(locked);
    renderHome();
    return true;
  }

  // --- The pack list comes from the server ---------------------------------
  // The page derives its backend from the rule app.js uses: the `?api=` query
  // parameter, else the local rehearsal port. Copying the rule keeps the start
  // screen talking to the backend the workspace will actually use.
  function apiBase() {
    try {
      var Params = SU_GLOBAL.URLSearchParams;
      var search = (SU_GLOBAL.location && SU_GLOBAL.location.search) || '';
      var base = Params ? new Params(search).get('api') : null;
      return base || 'http://localhost:8123';
    } catch (_) { return 'http://localhost:8123'; }
  }

  // One GET, resolved to data or null. No failure of a start-up request escapes as
  // a rejection — an engine without fetch, an offline backend, a body that is not
  // JSON — because the boot must never wait on, or break on, a missing server.
  function readJson(path) {
    return new Promise(function (resolve) {
      var pending;
      try { pending = SU_GLOBAL.fetch(apiBase() + path, { headers: { 'Accept': 'application/json' } }); }
      catch (_) { resolve(null); return; }
      Promise.resolve(pending).then(function (res) {
        if (!res || !res.ok) { resolve(null); return; }
        Promise.resolve(res.json()).then(function (data) { resolve(data || null); }, function () { resolve(null); });
      }, function () { resolve(null); });
    });
  }

  // Feed the server's packs to adoptPacks, with each chip's tagline as the second
  // line of its cartridge and its source deciding whether that text is translated.
  // Anything unusable leaves the two samples in place, so the start screen boots
  // exactly as it did before this list existed; a missing /chips answer leaves
  // every row without a source, which adoptPacks reads as researcher-authored.
  function loadPacks() {
    if (typeof SU_GLOBAL.fetch !== 'function') return Promise.resolve(false);
    return Promise.all([readJson('/domain-packs'), readJson('/chips')]).then(function (answers) {
      var packList = answers[0] && Array.isArray(answers[0].packs) ? answers[0].packs : null;
      if (!packList || !packList.length) return false;
      var notes = {};
      var sources = {};
      var arts = {};
      var chips = answers[1] && Array.isArray(answers[1].chips) ? answers[1].chips : [];
      for (var i = 0; i < chips.length; i++) {
        var chip = chips[i];
        if (!chip || typeof chip.pack_id !== 'string') continue;
        if (chip.tagline) notes[chip.pack_id] = String(chip.tagline);
        if (typeof chip.source === 'string') sources[chip.pack_id] = chip.source;
        // art_url is the server's own word that a picture exists. A chip without
        // one keeps the drawn face, so nothing regresses for the built-ins.
        if (typeof chip.art_url === 'string' && chip.art_url) arts[chip.pack_id] = chip.art_url;
      }
      if (!adoptPacks(packList, notes, sources, arts)) return false;
      // A remembered chip the samples could not vouch for is known only now, so a
      // first-run picker hands over to it instead of asking the researcher again.
      if (phase === 'choosing' && !selected && !previous) {
        var saved = readPack();
        if (saved) seatRemembered(saved);
      }
      return true;
    }, function () { return false; });
  }

  var $ = function (id) { return document.getElementById(id); };
  var reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  var generation = 0;
  var expansionMotion = null;
  var expansionFrom = null;
  var phase = 'idle';
  var selected = null;
  var previous = null;
  var locked = false;
  var storageNotice = false;
  // Where the collapse in flight is heading: 'menu' for the return to the home
  // screen (which keeps the window's size while the workspace fades), 'picker'
  // for the return to the console display. Resize uses it to follow the right box.
  var collapseTarget = 'picker';
  // Whether this screen opened the settings sheet. Escape closes it before any
  // other Escape rule, and this flag is what makes that rule precise.
  var settingsOpen = false;
  var resolveReady;
  var ready = new Promise(function (resolve) { resolveReady = resolve; });
  var gateOpen = false;

  function packLabel(pack) {
    var entry = FIELDS[pack];
    if (!entry) return String(pack || st('Research field')).replace(/_/g, ' ');
    return entry.raw ? entry.label : st(entry.label);
  }
  function isKnown(pack) { return Object.prototype.hasOwnProperty.call(FIELDS, pack); }
  // Every browser this app runs in has replaceChildren; a DOM double that does
  // not still gets a clean tray, because it only holds the buttons drawn here.
  function clearChildren(node) {
    if (typeof node.replaceChildren === 'function') node.replaceChildren();
    else if (node.children && node.children.length) { node.children.length = 0; node.childElementCount = 0; }
  }
  // The seated pack always resolves to something the ready event can name: a chip
  // the server no longer lists still opens the session it was seated for, by id.
  function entryFor(pack) {
    return FIELDS[pack] || { field: pack, label: String(pack), note: '', raw: true };
  }
  // The second line of a cartridge: the chip's own tagline when it has one, so the
  // researcher recognises the pack they made rather than reading a generic line.
  // A built-in tagline is authored here and translated like its title; a chip's
  // own tagline is shown exactly as the researcher wrote it.
  function choiceNote(pack) {
    var entry = FIELDS[pack];
    var note = entry && entry.note;
    if (note) return entry.raw ? note : st(note);
    return st('Insert the {field} cartridge', { field: packLabel(pack) });
  }
  function status(message, keep) {
    var node = $('startup-status');
    if (!node) return;
    // A storage failure outranks the routine progress line: it is the one
    // message the researcher needs to act on, so it is not overwritten.
    if (keep) node.textContent = message;
    else if (!storageNotice) node.textContent = message;
  }
  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, (reduced && reduced.matches) ? Math.min(ms, 45) : ms); });
  }
  function viewportBounds() {
    var v = window.visualViewport;
    return { left: (v && v.offsetLeft) || 0, top: (v && v.offsetTop) || 0, width: (v && v.width) || window.innerWidth, height: (v && v.height) || window.innerHeight };
  }
  function boxStyle(r) {
    return { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' };
  }

  function readPack() {
    try { var saved = localStorage.getItem(KEY); return isKnown(saved) ? saved : null; } catch (_) { return null; }
  }
  // The app owns persistence: app.js writes the same key on an explicit pack pick.
  // The start screen writes it once, before the workspace reads it at startup.
  function savePack(pack) {
    try { localStorage.setItem(KEY, pack); return true; } catch (_) { return false; }
  }

  function lock(value) {
    locked = !!value;
    var choices = document.querySelectorAll('[data-startup-field]');
    for (var i = 0; i < choices.length; i++) choices[i].disabled = value;
    var cancel = $('startup-cancel');
    if (cancel) cancel.disabled = value;
    var change = $('startup-change');
    if (change) change.disabled = value;
  }

  function setCard(on) {
    var card = $('startup-card');
    if (card) card.classList.toggle('on', !!on);
    var screen = $('startup-screen');
    if (screen) screen.classList.toggle('on', !!on);
  }

  function buildChoices() {
    var host = $('startup-choices');
    if (!host) return;
    // Rebuild from the current pack list: an installed chip must appear as a
    // cartridge, so a previously drawn shorter list is discarded, not reused.
    clearChildren(host);
    Object.keys(FIELDS).forEach(function (pack) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'startup-choice';
      button.setAttribute('data-startup-field', pack);
      button.setAttribute('aria-pressed', 'false');
      // The chip's own picture, on the same row as its name. A chip with no art
      // gets no img at all rather than a broken frame, so the two samples and any
      // researcher-authored chip keep exactly the cartridge they had.
      var art = choiceArt(pack);
      if (art) button.appendChild(art);
      var name = document.createElement('strong');
      name.className = 'startup-choice-name';
      var note = document.createElement('span');
      note.className = 'startup-choice-note';
      button.appendChild(name);
      button.appendChild(note);
      button.addEventListener('click', function () { insert(pack); });
      host.appendChild(button);
    });
    refreshChoices();
  }

  // An <img> for a chip that has its own picture, else null. The URL comes from
  // the server's art_url, so a chip without one never reaches /art (which answers
  // 404 for it) and never leaves a broken image on the tray.
  function choiceArt(pack) {
    var entry = FIELDS[pack];
    if (!entry || !entry.art) return null;
    if (typeof document.createElement !== 'function') return null;
    var img = document.createElement('img');
    img.className = 'startup-choice-art';
    img.alt = '';
    // Decorative: the name label beside it already carries the accessible name,
    // so an empty alt keeps a screen reader from reading the chip twice.
    img.setAttribute('aria-hidden', 'true');
    img.src = apiBase() + entry.art;
    return img;
  }

  // The two lines of each choice are script output, so a language switch has to
  // rewrite them; the static markup around them is handled by i18n.js. Found by
  // class, not by position: a chip with its own picture puts an <img> first.
  function refreshChoices() {
    var choices = document.querySelectorAll('[data-startup-field]');
    for (var i = 0; i < choices.length; i++) {
      var pack = choices[i].getAttribute('data-startup-field');
      if (!isKnown(pack)) continue;
      var parts = choices[i].children || [];
      for (var j = 0; j < parts.length; j++) {
        var cls = parts[j].className || '';
        if (cls === 'startup-choice-name') parts[j].textContent = packLabel(pack);
        else if (cls === 'startup-choice-note') parts[j].textContent = choiceNote(pack);
      }
    }
  }

  function markSelection(pack) {
    var choices = document.querySelectorAll('[data-startup-field]');
    for (var i = 0; i < choices.length; i++) {
      choices[i].setAttribute('aria-pressed', choices[i].getAttribute('data-startup-field') === pack ? 'true' : 'false');
    }
  }

  // Only the inner display grows, from its measured anchors to the viewport.
  function expandDisplay(token) {
    return new Promise(function (resolve) {
      if (token !== generation) return resolve(false);
      phase = 'expanding';
      var portal = $('portal');
      var screen = $('startup-screen');
      var startup = $('startup');
      if (!portal || !screen) return resolve(false);
      if (startup) { startup.classList.add('console-leaving'); startup.inert = true; }
      // Until the reveal has finished the workspace is a picture of itself arriving,
      // not a surface anyone can use: the surface that is about to open is not the
      // one on screen yet, so nothing under this layer is a target.
      portal.inert = true;
      // Measured after the leaving state applies: the home screen already fills
      // the window (frontend/home_expand.css), so the reveal starts from the
      // window. Measuring first would grow the workspace out of a console bezel
      // that is no longer painted on screen.
      var from = screen.getBoundingClientRect();
      expansionFrom = from;
      portal.classList.add('opening');
      portal.hidden = false;
      Object.assign(portal.style, boxStyle(from));
      if (!reduced || !reduced.matches) {
        var motion = portal.animate([boxStyle(from), boxStyle(viewportBounds())], { duration: 320, easing: 'cubic-bezier(.22,.75,.22,1)', fill: 'forwards' });
        expansionMotion = motion;
        var settle = function () { return resolve(finish()); };
        motion.finished.then(settle, settle);
      } else {
        Object.assign(portal.style, boxStyle(viewportBounds()));
        resolve(finish());
      }
      function finish() {
        // Released before the token guard: a reveal that was cancelled must not
        // leave the workspace permanently unclickable.
        portal.inert = false;
        if (token !== generation) return false;
        Object.assign(portal.style, boxStyle(viewportBounds()));
        // Release the animation's frozen viewport dimensions after a native
        // window resize; inline bounds must own subsequent resize events.
        if (motion && typeof motion.cancel === 'function') motion.cancel();
        expansionMotion = null;
        expansionFrom = null;
        portal.classList.remove('opening');
        portal.classList.add('expanded');
        if (startup) { startup.hidden = true; startup.inert = true; startup.classList.remove('console-leaving'); }
        if (document.body && document.body.classList) document.body.classList.add('workbench-open');
        var heading = $('project-title');
        if (heading && heading.focus) heading.focus({ preventScroll: true });
        phase = 'ready';
        lock(false);
        var seated = entryFor(selected);
        window.dispatchEvent(new CustomEvent(READY_EVENT, {
          detail: { field: seated.field, label: seated.label, source: SOURCE }
        }));
        status(st('Opening the {field} workbench.', { field: packLabel(selected) }));
        resolveReady(selected);
        return true;
      }
    });
  }

  // From the open workspace back to the home menu. Nothing is changed and nothing
  // is written: this is navigation, not a preference.
  // The home screen owns the whole window (frontend/home_expand.css) and the
  // workspace was opened over that same window, so the way back is a dissolve,
  // not a shrink into a bezel that is not painted any more. The console is drawn
  // under the workspace first, so the menu is already there as it fades in.
  function collapseToMenu(token) {
    return new Promise(function (resolve) {
      var portal = $('portal');
      if (!portal || portal.hidden) return resolve(false);
      phase = 'collapsing';
      lock(true);
      portal.classList.remove('expanded');
      if (document.body && document.body.classList) document.body.classList.remove('workbench-open');
      var startup = $('startup');
      collapseTarget = 'menu';
      if (startup) { startup.hidden = false; startup.inert = false; }
      // Painting only: showHome still owns the phase change, the ready
      // announcement and the focus move, and runs it once the dissolve is done,
      // so focus never lands on a tile that is still covered.
      paintHome();
      // The workspace keeps its open project: it is hidden, never unmounted, so
      // returning to a menu does not throw the researcher's work away.
      Object.assign(portal.style, boxStyle(viewportBounds()));
      var settle = function () { return resolve(done()); };
      var motion = null;
      if (!reduced || !reduced.matches) {
        motion = portal.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, easing: 'ease-out', fill: 'forwards' });
        motion.finished.then(settle, settle);
      } else {
        resolve(done());
      }
      function done() {
        if (token !== generation) return false;
        // A finished fill:forwards animation would keep the portal transparent
        // when it is opened again, so its effect ends with the transition.
        if (motion && typeof motion.cancel === 'function') motion.cancel();
        motion = null;
        portal.hidden = true;
        // Home is not a workspace surface, so no surface outlives the visit: every
        // open view is closed, not just stripped of its mode class. Dropping the
        // class alone left #library-view, #latex-view and #secretary-review-view
        // without the `.hidden` that is the only thing taking them off screen, so
        // the next Home→tile reveal painted the previous surface over the new one
        // and let it take the clicks. It runs here, once the portal is hidden,
        // so the surface the reader left is what fades out rather than the bare
        // Research scaffold behind it.
        closeWorkspaceSurfaces();
        showHome(token);
        return true;
      }
    });
  }

  // Reverse: collapse the display back into the console, then offer the choice.
  // This is the only collapse that really shrinks into the bezel, because it ends
  // at the cartridge picker, and the picker is drawn on the console. The home
  // state comes off first, so the display being measured and animated into is the
  // display that is actually drawn inside the approved bezel.
  function collapseDisplay(token) {
    return new Promise(function (resolve) {
      var portal = $('portal');
      var screen = $('startup-screen');
      if (!portal || !screen || portal.hidden) return resolve(true);
      phase = 'collapsing';
      lock(true);
      portal.classList.remove('expanded');
      if (document.body && document.body.classList) document.body.classList.remove('workbench-open');
      var startup = $('startup');
      collapseTarget = 'picker';
      if (startup) { startup.hidden = false; startup.inert = false; startup.classList.remove('home'); }
      Object.assign(portal.style, boxStyle(viewportBounds()));
      if (!reduced || !reduced.matches) {
        var motion = portal.animate([boxStyle(viewportBounds()), boxStyle(screen.getBoundingClientRect())], { duration: 540, easing: 'cubic-bezier(.22,.75,.22,1)', fill: 'forwards' });
        var settle = function () { resolve(done()); };
        motion.finished.then(settle, settle);
      } else {
        Object.assign(portal.style, boxStyle(screen.getBoundingClientRect()));
        resolve(done());
      }
      function done() {
        if (token !== generation) return false;
        portal.hidden = true;
        return true;
      }
    });
  }

  function insert(pack) {
    if (phase !== 'choosing' || !isKnown(pack)) return;
    var token = ++generation;
    phase = 'inserting';
    lock(true);
    status(st('Inserting the {field} cartridge.', { field: packLabel(pack) }));
    selected = pack;
    markSelection(pack);
    // Two beats, not a fade: the cartridge drops into the top bay, the display
    // lights when it is home, then the home menu appears on that display.
    setCard(true);
    return sleep(INSERT_MS).then(function () {
      if (token !== generation) return;
      if (!savePack(pack)) {
        storageNotice = true;
        status(st('This browser refused to store the field, so it applies to this window only.'), true);
      }
      return sleep(120).then(function () {
        if (token !== generation) return;
        return showHome(token);
      });
    });
  }

  // --- Boot sequence -------------------------------------------------------
  // The display powers on before the home menu. Each step is discrete (the CSS
  // uses steps(1, end)), and the whole build is skipped under reduced motion, so
  // a researcher who asked for less motion lands on the menu immediately.
  var BOOT_STEP_MS = 210;

  function bootSequence() {
    var boot = $('startup-boot');
    var rule = $('boot-rule');
    if (!boot) return Promise.resolve();
    boot.hidden = false;
    var boot = $('startup-boot');
    if (boot) boot.hidden = false;
    if (rule && !rule.getAttribute('data-built')) {
      rule.setAttribute('data-built', '1');
      for (var i = 0; i < 6; i++) {
        var pip = document.createElement('span');
        pip.className = 'boot-pip';
        rule.appendChild(pip);
      }
    }
    if (reduced && reduced.matches) {
      var lit = rule && rule.children ? rule.children : [];
      for (var j = 0; j < lit.length; j++) lit[j].className = 'boot-pip lit';
      boot.hidden = true;
      return Promise.resolve();
    }
    return sleep(BOOT_STEP_MS).then(function () {
      var pips = rule && rule.children ? rule.children : [];
      // Light the pips one at a time; the field and note lines settle with them.
      var step = function (n) {
        if (n > 0 && pips[n - 1]) pips[n - 1].className = 'boot-pip lit';
        if (n >= pips.length) return sleep(BOOT_STEP_MS);
        return sleep(BOOT_STEP_MS / 2).then(function () { return step(n + 1); });
      };
      return step(0);
    }).then(function () {
      boot.hidden = true;
    });
  }

  // --- Home menu -----------------------------------------------------------
  // Left: the secretary panel. Right: the four workbench menus. Settings stays a
  // small control at the top rather than a fifth tile, because it configures the
  // machine instead of opening a research surface.
  // Four home destinations, in research-workflow order: find the literature,
  // work the data, write it up, then review the conversations that got you there.
  // Citation tools remain available from literature and manuscript contexts.
  // Only the order changes; keys, labels, notes, open functions and the
  // data-testid each tile derives from its key stay exactly as they were.
  var MENUS = [
    { key: 'library', label: 'Literature Review', note: 'Sources, notes and documents', open: 'openLibraryView' },
    { key: 'research', label: 'Research', note: 'Ask about the data and review the evidence', open: 'showResearchView' },
    { key: 'writing', label: 'Writing', note: 'Draft and revise the manuscript', open: 'openWritingView' },
    { key: 'review', label: 'Conversation Review', note: 'Keep or remove past conversations', open: 'openSecretaryReview' }
  ];

  function homeVisible(on) {
    var menu = $('startup-menu');
    if (menu) menu.hidden = !on;
    var startup = $('startup');
    if (startup) {
      if (on) startup.classList.add('home');
      else startup.classList.remove('home');
    }
  }

  // --- Keyboard operation of the 2x2 tile grid -----------------------------
  // Standard roving tabindex: exactly one tile is in the Tab order, so Tab enters
  // the group once and Tab again leaves it (the Settings gear keeps its own place
  // in the normal order). The four tiles are laid out row-major, two per row:
  // index 0,1 on top and 2,3 below. Left/Right walk within a row, Up/Down within a
  // column, and because the row length is 2 the vertical step is 2 — a full 4-tile
  // cycle, so both axes wrap the same way.
  var HOME_COLS = 2;
  var homeActive = 0;

  function homeTiles() {
    var host = $('home-menu');
    var out = [];
    if (!host || !host.children) return out;
    for (var i = 0; i < host.children.length; i++) {
      if (host.children[i] && host.children[i].getAttribute('data-menu')) out.push(host.children[i]);
    }
    return out;
  }

  function setHomeActive(index) {
    var tiles = homeTiles();
    if (!tiles.length) return;
    var target = ((index % tiles.length) + tiles.length) % tiles.length;
    for (var i = 0; i < tiles.length; i++) tiles[i].setAttribute('tabindex', i === target ? '0' : '-1');
    homeActive = target;
  }

  function focusHomeTile(index) {
    var tiles = homeTiles();
    if (!tiles.length) return;
    setHomeActive(index);
    var tile = tiles[homeActive];
    if (tile && tile.focus) tile.focus({ preventScroll: true });
  }

  function homeStep(key, from) {
    if (key === 'ArrowLeft') return from - 1;
    if (key === 'ArrowRight') return from + 1;
    if (key === 'ArrowUp') return from - HOME_COLS;
    if (key === 'ArrowDown') return from + HOME_COLS;
    return null;
  }

  // Bound on the menu host, never on the document: the boot sequence and the
  // cartridge picker keep their own keys, and a hidden menu receives nothing.
  function onHomeKey(event) {
    if (!event) return;
    var tiles = homeTiles();
    if (!tiles.length) return;
    var from = homeActive;
    for (var i = 0; i < tiles.length; i++) if (tiles[i] === event.target) from = i;
    var next;
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tiles.length - 1;
    else next = homeStep(event.key, from);
    // Enter, Space and Tab fall through untouched: the buttons activate natively
    // (one bootTone + one enterMenu, not two) and Tab leaves the group.
    if (next === null || next === undefined) return;
    if (event.preventDefault) event.preventDefault();
    focusHomeTile(next);
  }

  function renderHome() {
    var host = $('home-menu');
    if (!host) return;
    if (host.childElementCount) { relabelHome(); return; }
    MENUS.forEach(function (menu, idx) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'menu-item';
      button.setAttribute('data-menu', menu.key);
      button.setAttribute('data-testid', 'home-menu-' + menu.key);
      // One tile in the Tab order; the arrows move which one that is.
      button.setAttribute('tabindex', idx === 0 ? '0' : '-1');
      var cursor = document.createElement('span');
      cursor.className = 'menu-cursor';
      var text = document.createElement('span');
      text.className = 'menu-text';
      var label = document.createElement('span');
      label.className = 'menu-label';
      var note = document.createElement('span');
      note.className = 'menu-note';
      text.appendChild(label);
      text.appendChild(note);
      button.appendChild(cursor);
      button.appendChild(text);
      button.addEventListener('click', function () { bootTone('select'); enterMenu(menu.key); });
      host.appendChild(button);
    });
    var gear = $('home-settings');
    if (gear && !gear.getAttribute('data-wired')) {
      gear.setAttribute('data-wired', '1');
      gear.addEventListener('click', function () { bootTone('select'); enterMenu('settings'); });
    }
    if (!host.getAttribute('data-keyed')) {
      host.setAttribute('data-keyed', '1');
      host.addEventListener('keydown', onHomeKey);
      // A click or a Tab that lands on a tile makes that tile the active one, so
      // the arrows continue from where the person actually is.
      host.addEventListener('focusin', function (event) {
        var tiles = homeTiles();
        for (var i = 0; i < tiles.length; i++) if (tiles[i] === event.target) setHomeActive(i);
      });
    }
    relabelHome();
  }

  function relabelHome() {
    var host = $('home-menu');
    var tiles = host && host.children ? host.children : [];
    for (var i = 0; i < tiles.length; i++) {
      var key = tiles[i].getAttribute('data-menu');
      var menu = null;
      for (var m = 0; m < MENUS.length; m++) if (MENUS[m].key === key) menu = MENUS[m];
      if (!menu) continue;
      var text = tiles[i].children && tiles[i].children[1];
      var parts = text && text.children ? text.children : [];
      if (parts[0]) parts[0].textContent = st(menu.label);
      if (parts[1]) parts[1].textContent = st(menu.note);
    }
    var tag = $('home-field');
    if (tag) tag.textContent = selected ? packLabel(selected) : st('No research field');
    var gear = $('home-settings');
    if (gear) {
      var gearText = gear.children && gear.children.length ? gear.children[0] : null;
      if (gearText) gearText.textContent = st('Settings');
      else gear.textContent = st('Settings');
      gear.setAttribute('aria-label', st('Settings'));
      gear.setAttribute('title', st('Settings'));
    }
    var title = $('home-secretary-title');
    if (title && !title.getAttribute('data-live')) title.textContent = st('Nothing waiting');
    var note = $('home-secretary-note');
    if (note && !note.getAttribute('data-live')) note.textContent = st('Open a project to see what the secretary has for you.');
  }

  // The field is seated, so the workspace behind the display may load its data
  // (the secretary panel reads the same briefing the workspace does). The display
  // itself stays closed until a menu is chosen, so phase stays 'home'.
  function announceReady() {
    lock(false);
    var seated = entryFor(selected);
    window.dispatchEvent(new CustomEvent(READY_EVENT, {
      detail: { field: seated.field, label: seated.label, source: SOURCE }
    }));
    status(st('Choose a menu to open.'));
    if (!gateOpen) { gateOpen = true; resolveReady(selected); }
  }

  function showHome(token) {
    if (token !== generation) return Promise.resolve(false);
    phase = 'home';
    bootTone('boot');
    paintHome();
    announceReady();
    var first = $('home-menu');
    var focusTarget = first && first.children && first.children[0];
    // The first tile is focused as the screen appears, so the arrow keys work
    // immediately and the roving tabindex starts on that same tile.
    if (focusTarget && focusTarget.focus) { setHomeActive(0); focusTarget.focus({ preventScroll: true }); }
    return Promise.resolve(true);
  }

  // The home screen as it looks, without announcing or moving focus. Returning
  // from the workspace draws this first so the menu is already behind the
  // workspace while it dissolves; showHome then does the announce and focus once
  // the transition is over, so focus never moves to a tile still covered.
  function paintHome() {
    setCard(true);
    renderHome();
    homeVisible(true);
    var boot = $('startup-boot');
    if (boot) boot.hidden = true;
    var choices = $('startup-choices');
    if (choices) choices.hidden = true;
    var cancel = $('startup-cancel');
    if (cancel) cancel.hidden = true;
  }

  function enterMenu(key) {
    if (phase !== 'home' && phase !== 'ready') return;
    // Settings is not a tile: it configures the machine instead of opening a
    // research surface, so the gear reaches it before the menu lookup.
    if (key === 'settings') { openSettings(); return; }
    var menu = null;
    for (var i = 0; i < MENUS.length; i++) if (MENUS[i].key === key) menu = MENUS[i];
    if (!menu) return;
    var token = ++generation;
    // Opening a tile opens its surface only after the display has expanded, which
    // takes a moment. A project can be opened and a surface chosen inside that
    // window, and the surface on screen at the click is remembered here: if
    // another surface is showing by the time the expansion ends, that one was
    // asked for later and stays. Without this the delayed open took the screen
    // back from whatever the researcher had already chosen.
    var surfaceAtClick = activeSurface();
    homeVisible(false);
    expandDisplay(token).then(function (ok) {
      if (!ok || token !== generation) return;
      var now = activeSurface();
      // `now` is '' while the plain Research screen is showing, which is also
      // what an untouched workspace reads as, so only a named surface counts as
      // "the researcher went somewhere".
      if (now && now !== surfaceAtClick) return;
      var fn = window[menu.open];
      if (typeof fn === 'function') {
        try { fn(); } catch (_) { /* a failing view must not strand the shell */ }
      }
    });
  }

  // Which working surface is on screen, by the mode class the surface functions
  // themselves set on <main><section>. A section with no mode class is the plain
  // Research screen; while the home menu is showing, no working surface is open
  // at all, and that is a different thing from plain Research — a tile opened
  // from Home must not be mistaken for "nothing changed since the click".
  var SURFACE_MODES = ['library-mode', 'writing-mode', 'citation-mode', 'secretary-review-mode'];
  var NO_SURFACE = 'none';
  function activeSurface() {
    if (phase === 'home') return NO_SURFACE;
    var section = document.querySelector('main section');
    if (!section || !section.classList) return '';
    for (var i = 0; i < SURFACE_MODES.length; i++) {
      if (section.classList.contains(SURFACE_MODES[i])) return SURFACE_MODES[i];
    }
    return '';
  }
  // The mode classes are the workspace's statement of "which view is open", and
  // the workspace is hidden — not unmounted — while the home menu is up. Leaving
  // the class behind would make the next tile open onto a stale "library is the
  // current view" and would put Home visibly in a surface mode it is not in.
  function clearSurfaceModes() {
    var section = document.querySelector('main section');
    if (!section || !section.classList) return;
    for (var i = 0; i < SURFACE_MODES.length; i++) section.classList.remove(SURFACE_MODES[i]);
  }

  // Every surface is closed, by the function that owns it, before Home is shown.
  // A mode class is not the same claim as `.hidden`: #library-view, #latex-view
  // and #secretary-review-view are hidden by that class alone (style.css:533,
  // writing.css:17, style.css:756), and it is the surface functions, not Home,
  // that add it. So Home closes them through their own entry points — which keep
  // the surface's state and only take it off screen — and then clears the modes
  // as a last guard for a surface that is not loaded.
  function closeWorkspaceSurfaces() {
    if (typeof closeCitationView === 'function') closeCitationView();
    if (typeof setLibraryViewVisible === 'function') setLibraryViewVisible(false);
    if (typeof setLatexViewVisible === 'function') setLatexViewVisible(false);
    if (typeof secretaryReviewVisible === 'function') secretaryReviewVisible(false);
    clearSurfaceModes();
  }

  function choose(isChange) {
    var startup = $('startup');
    if (startup) { startup.hidden = false; startup.inert = false; }
    phase = 'choosing';
    lock(false);
    setCard(false);
    homeVisible(false);
    var boot = $('startup-boot');
    if (boot) boot.hidden = true;
    var choices = $('startup-choices');
    if (choices) choices.hidden = false;
    var cancel = $('startup-cancel');
    if (cancel) cancel.hidden = !isChange;
    announceChoice(isChange);
  }

  // The kicker and the status line of the choosing phase, in one place so a
  // language switch can restate them without repeating the wording.
  function announceChoice(isChange) {
    var kicker = $('startup-kicker');
    if (kicker) kicker.textContent = isChange ? st('Choose a different research field') : st('Choose a research field');
    status(isChange
      ? st('Pick a field to switch context. Cancel keeps {current}.', { current: previous ? packLabel(previous) : st('the current field') })
      : st('Choose a cartridge to open the workbench.'));
  }

  // Script output on this screen: the two lines of each field choice, the
  // kicker and the status line. A language switch redraws them for the phase the
  // console is actually in; nothing else about the screen changes.
  function relabel() {
    refreshChoices();
    relabelHome();
    if (storageNotice) {
      status(st('This browser refused to store the field, so it applies to this window only.'), true);
      return;
    }
    if (phase === 'choosing') announceChoice(!!previous);
    else if (phase === 'inserting' || phase === 'booting') status(st('Inserting the {field} cartridge.', { field: packLabel(selected) }));
    else if (phase === 'home') status(st('Choose a menu to open.'));
    else if (phase === 'ready') status(st('Opening the {field} workbench.', { field: packLabel(selected) }));
  }

  function change() {
    if (phase !== 'home' && phase !== 'ready') return;
    var token = ++generation;
    previous = selected;
    var returning = phase === 'home';
    if (!returning) {
      collapseDisplay(token).then(function (ok) {
        if (!ok || token !== generation) return;
        choose(true);
        var first = document.querySelector('[data-startup-field]');
        if (first && first.focus) first.focus({ preventScroll: true });
      });
      return;
    }
    // From the home menu the display is already closed, so the picker is simply
    // shown again; there is no portal to collapse.
    choose(true);
    var field = document.querySelector('[data-startup-field]');
    if (field && field.focus) field.focus({ preventScroll: true });
  }

  function cancel() {
    if (phase !== 'choosing' || !previous) return;
    var pack = previous;
    previous = null;
    var token = ++generation;
    selected = pack;
    markSelection(pack);
    setCard(true);
    // Returning from a cancelled change goes back to the home menu, not straight
    // into the workspace: the researcher cancelled, so nothing is opened.
    showHome(token);
  }

  // A dialog owns Escape while it is actually on screen. `hidden` on the sheet's
  // own element is not enough: the shipped sheet hides its *layer*, so the sheet
  // still reads as `[role="dialog"]` and would swallow every Escape.
  function anOpenDialogOwnsEscape() {
    var nodes = document.querySelectorAll ? document.querySelectorAll('dialog[open], [role="dialog"]') : [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!el || (el.getClientRects && el.getClientRects().length === 0)) continue;
      var hidden = false, n = el;
      while (n && n !== document.body) {
        if (n.hidden === true) { hidden = true; break; }
        n = n.parentElement;
      }
      if (!hidden) return true;
    }
    return false;
  }

  // The menu is always reachable from the workspace, by one key. Escape is the
  // reverse of choosing a tile, so it is the shortest path back; the palette
  // keeps its own Escape behaviour when it is the thing open, which is why this
  // only fires when no menu, dialog or overlay claims the key first.
  //
  // "Always" includes the 320 ms the display spends growing: Escape is pressed
  // because the person changed their mind, so it cancels the reveal that is still
  // under way instead of waiting for a workspace they no longer want. Without
  // this the key did nothing during the reveal — the phase was 'expanding', not
  // 'ready' — and the tile finished opening behind it.
  function backToMenu() {
    if (phase === 'expanding') { cancelExpansion(++generation); return Promise.resolve(true); }
    if (phase !== 'ready') return Promise.resolve(false);
    var token = ++generation;
    return collapseToMenu(token);
  }

  // Undo a reveal that has not settled. Bumping the generation is what stops the
  // opening: the animation's own settle resolves false and enterMenu's delayed open
  // never runs, so the tile that was arriving is dropped with the frame it was in.
  // The workspace is put back exactly as a finished collapse leaves it, less the
  // dissolve, because there is nothing on screen to dissolve yet.
  function cancelExpansion(token) {
    var portal = $('portal');
    var startup = $('startup');
    if (expansionMotion && typeof expansionMotion.cancel === 'function') expansionMotion.cancel();
    expansionMotion = null;
    expansionFrom = null;
    if (portal) {
      if (portal.classList) { portal.classList.remove('opening'); portal.classList.remove('expanded'); }
      portal.inert = false;
      portal.hidden = true;
      // The inline bounds belonged to the reveal; the next open measures again.
      portal.style.left = ''; portal.style.top = ''; portal.style.width = ''; portal.style.height = '';
    }
    if (document.body && document.body.classList) document.body.classList.remove('workbench-open');
    if (startup) {
      startup.hidden = false;
      startup.inert = false;
      if (startup.classList) startup.classList.remove('console-leaving');
    }
    // Same contract as the finished collapse: no surface outlives the visit, so
    // the next tile opens onto its own view and not the one that was arriving.
    closeWorkspaceSurfaces();
    showHome(token);
  }

  function handleResize() {
    var portal = $('portal');
    if (!portal) return;
    if (phase === 'home') return;
    if (phase === 'ready') Object.assign(portal.style, boxStyle(viewportBounds()));
    else if (phase === 'expanding') {
      Object.assign(portal.style, boxStyle(viewportBounds()));
      // Native resizing runs alongside the display zoom. Update the destination
      // without restarting the animation or leaving a snap at its old viewport.
      if (expansionMotion && expansionMotion.effect && expansionMotion.effect.setKeyframes && expansionFrom) {
        expansionMotion.effect.setKeyframes([boxStyle(expansionFrom), boxStyle(viewportBounds())]);
      }
    }
    else if (phase === 'collapsing') {
      // Two collapses end in different places: the one back to the menu keeps the
      // workspace at the window while it fades, and the one back to the picker
      // tracks the console display it is shrinking into.
      if (collapseTarget === 'menu') Object.assign(portal.style, boxStyle(viewportBounds()));
      else {
        var screen = $('startup-screen');
        if (screen) Object.assign(portal.style, boxStyle(screen.getBoundingClientRect()));
      }
    }
  }

  function start() {
    buildChoices();
    wireSettings();
    window.addEventListener('resize', handleResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleResize);
      window.visualViewport.addEventListener('scroll', handleResize);
    }
    window.addEventListener('orientationchange', handleResize);
    document.addEventListener('keydown', function (event) {
      // A control that already handled Escape owns the key. The library's inline
      // folder input, the project drag and the writing sheets all cancel
      // themselves with preventDefault, and the shell must not also read the same
      // key as "go back to the menu" — that closed the whole workspace on what the
      // researcher meant as "discard this rename". Same idiom as declutter_shell.js.
      if (event.defaultPrevented) return;
      if (event.key === 'Escape' && phase === 'choosing' && previous) cancel();
      // The settings sheet is ours and it is the innermost thing on screen while
      // it is open, so Escape closes it before anything behind it reacts. Without
      // this the sheet read as an open dialog and swallowed the key: the person
      // pressed Escape and nothing happened.
      if (event.key === 'Escape' && closeSettingsIfOpen()) {
        if (event.preventDefault) event.preventDefault();
        return;
      }
      // From the open workspace, Escape returns to the menu. A dialog, the
      // palette, or any open overlay owns Escape first: only the plain surface
      // responds, so this never steals a key from something the person opened.
      // The reveal in flight counts as reachable too: the menu is one key away
      // while the display is still growing, not only once it has settled.
      if (event.key === 'Escape' && (phase === 'ready' || phase === 'expanding') && !anOpenDialogOwnsEscape()) backToMenu();
    });
    var changeButton = $('startup-change');
    if (changeButton) {
      changeButton.hidden = false;
      changeButton.addEventListener('click', change);
    }
    var menuButton = $('back-to-menu');
    if (menuButton) {
      menuButton.hidden = false;
      menuButton.addEventListener('click', function () { backToMenu(); });
    }
    var cancelButton = $('startup-cancel');
    if (cancelButton) cancelButton.addEventListener('click', cancel);

    // The server's pack list is asked for beside the boot, never awaited by it, so
    // a backend that is absent, slow or still starting cannot hold this screen.
    loadPacks();
    var saved = readPack();
    if (saved) seatRemembered(saved);
    else choose(false);
  }

  // A remembered field skips selection, but the cartridge still seats: the insert
  // is the machine's own start-up, not an extra ceremony for changing a setting.
  // The dots power on after it is home, then the menu appears. The server list can
  // also arrive after the picker was drawn, so both paths seat through here.
  function seatRemembered(pack) {
    selected = pack;
    markSelection(pack);
    phase = 'booting';
    lock(true);
    setCard(true);
    status(st('Inserting the {field} cartridge.', { field: packLabel(pack) }));
    sleep(INSERT_MS).then(function () {
      if (phase !== 'booting') return;
      bootSequence().then(function () {
        if (phase !== 'booting') return;
        showHome(++generation);
      });
    });
  }

  // The workspace keeps #pack-select as its own control for new questions. When
  // that select changes, the seated field must follow, or a later "Change research
  // field" would return to a field the researcher already left.
  function setField(pack) {
    if (!isKnown(pack) || (phase !== 'ready' && phase !== 'home') || pack === selected) return;
    selected = pack;
    previous = null;
    markSelection(pack);
    setCard(true);
    relabelHome();
  }

  // --- Settings sheet -------------------------------------------------------
  // Every control here changes real state, because a decorative settings panel
  // is worse than none: language and theme delegate to the modules that already
  // own those preferences, the sounds switch gates boot audio, the field button
  // re-enters the cartridge picker, and the session button calls the app's own
  // end-session action. Nothing is stored twice.
  var settingsReturnFocus = null;
  var settingsParent = null;

  function openSettings() {
    var layer = $('settings-layer');
    if (!layer) return;
    if (settingsOpen) return;
    settingsReturnFocus = document.activeElement;
    settingsParent = layer.parentNode;
    document.body.appendChild(layer);
    settingsOpen = true;
    layer.hidden = false;
    if (SU_GLOBAL.WB_INTERIOR_SETTINGS) {
      SU_GLOBAL.WB_INTERIOR_SETTINGS.open({ close: closeSettings });
    }
    var lang = $('settings-lang');
    if (lang && SU_GLOBAL.WB_I18N) lang.value = SU_GLOBAL.WB_I18N.get();
    var theme = $('settings-theme');
    if (theme && SU_GLOBAL.WB_THEME) theme.value = SU_GLOBAL.WB_THEME.get();
    var sound = $('settings-sound');
    if (sound) sound.checked = soundsOn();
    var sheet = $('settings-sheet');
    var first = sheet && sheet.querySelector
      ? sheet.querySelector('[role="tab"][aria-selected="true"]') : null;
    var focus = first || $('settings-close');
    if (focus && focus.focus) focus.focus({ preventScroll: true });
  }

  function closeSettings(options) {
    settingsOpen = false;
    var layer = $('settings-layer');
    if (layer) layer.hidden = true;
    if (SU_GLOBAL.WB_INTERIOR_SETTINGS) SU_GLOBAL.WB_INTERIOR_SETTINGS.close();
    if (layer && settingsParent) settingsParent.appendChild(layer);
    var opener = settingsReturnFocus;
    settingsReturnFocus = null;
    settingsParent = null;
    if ((!options || options.restoreFocus !== false) && opener && opener.focus) {
      opener.focus({ preventScroll: true });
    }
  }

  // True only when the sheet was actually opened by this screen, so the caller
  // can tell "I closed it" from "there was nothing to close" and let Escape fall
  // through. The flag is the authority, not the element's `hidden`: the shipped
  // markup starts hidden and any layer left mounted must not swallow the key.
  function closeSettingsIfOpen() {
    if (!settingsOpen) return false;
    closeSettings();
    return true;
  }

  function wireSettings() {
    var layer = $('settings-layer');
    if (!layer || layer.getAttribute('data-wired')) return;
    layer.setAttribute('data-wired', '1');
    var close = $('settings-close');
    if (close) close.addEventListener('click', closeSettings);
    layer.addEventListener('click', function (event) {
      if (event.target === layer) closeSettings();
    });
    document.addEventListener('keydown', function (event) {
      if (!settingsOpen || event.key !== 'Tab') return;
      var sheet = $('settings-sheet');
      var items = Array.from(sheet.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
      )).filter(function (item) { return item.getClientRects().length > 0; });
      var first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }, true);
    var lang = $('settings-lang');
    if (lang) lang.addEventListener('change', function () {
      if (SU_GLOBAL.WB_I18N) SU_GLOBAL.WB_I18N.set(lang.value);
    });
    var theme = $('settings-theme');
    if (theme) theme.addEventListener('change', function () {
      if (SU_GLOBAL.WB_THEME) SU_GLOBAL.WB_THEME.set(theme.value);
    });
    var sound = $('settings-sound');
    if (sound) sound.addEventListener('change', function () {
      setSoundsOn(sound.checked);
      if (sound.checked) bootTone('select');
    });
    var field = $('settings-field');
    if (field) field.addEventListener('click', function () {
      closeSettings();
      change();
    });
    var end = $('settings-end');
    if (end) end.addEventListener('click', function () {
      closeSettings();
      if (typeof window.endSession === 'function') window.endSession();
    });
  }

  // --- Startup sounds -------------------------------------------------------
  // Browsers refuse audio before the first interaction, so these tones are all
  // triggered by a click or key the researcher already made. The context is
  // created on first use and kept; a remembered preference can switch it off.
  var SOUND_KEY = 'wb.sounds';
  var audioContext = null;

  function soundsOn() {
    try { return localStorage.getItem(SOUND_KEY) !== 'off'; } catch (_) { return true; }
  }
  function setSoundsOn(on) {
    try { localStorage.setItem(SOUND_KEY, on ? 'on' : 'off'); } catch (_) {}
  }
  function audio() {
    if (!soundsOn()) return null;
    if (audioContext) {
      if (audioContext.state === 'suspended' && audioContext.resume) audioContext.resume();
      return audioContext;
    }
    var Ctor = SU_GLOBAL.AudioContext || SU_GLOBAL.webkitAudioContext;
    if (!Ctor) return null;
    try { audioContext = new Ctor(); } catch (_) { return null; }
    return audioContext;
  }
  // Short synthesized tones, no asset files: a soft two-note seat for the
  // cartridge, a click for a menu, a low acknowledgement for a refusal.
  var TONES = {
    boot: [[196, 0.00, 0.10], [294, 0.12, 0.16], [392, 0.26, 0.22]],
    select: [[440, 0.00, 0.07], [660, 0.07, 0.09]],
    refuse: [[180, 0.00, 0.12], [140, 0.10, 0.16]]
  };
  function bootTone(name) {
    var ctx = audio();
    var notes = TONES[name] || TONES.select;
    if (!ctx || typeof ctx.createOscillator !== 'function') return;
    var now = ctx.currentTime;
    for (var i = 0; i < notes.length; i++) {
      var freq = notes[i][0], at = notes[i][1], dur = notes[i][2];
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      // A quiet envelope; the sound is texture, not a notification.
      gain.gain.setValueAtTime(0.0001, now + at);
      gain.gain.exponentialRampToValueAtTime(0.06, now + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + dur);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(now + at); osc.stop(now + at + dur + 0.02);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  // A language switch redraws the words this screen produced itself. i18n.js
  // cannot reach them: they are set on script-created nodes, not data-i18n ones.
  if (SU_GLOBAL.WB_I18N && SU_GLOBAL.WB_I18N.onChange) SU_GLOBAL.WB_I18N.onChange(function () { relabel(); });

  window.WB_STARTUP = { ready: ready, change: change, setField: setField, backToMenu: backToMenu,
    openSettings: openSettings, closeSettings: closeSettings,
    // Which surface is open, and what "no surface at all" is called. The navigation
    // guard above reads the first; publishing it here is what lets a review hold
    // the guard to its contract without reaching into this closure.
    activeSurface: activeSurface,
    noSurface: NO_SURFACE,
    eject: function () {
      return collapseDisplay(++generation).then(function () { selected = null; previous = null; choose(false); });
    },
    openPack: function (pack) {
      return loadPacks().then(function () {
        if (!isKnown(pack)) throw new Error('This chip is not available.');
        if (locked) throw new Error('Wait for the current transition to finish.');
        var next = phase === 'ready' ? collapseDisplay(++generation) : Promise.resolve(true);
        return next.then(function () { choose(false); return insert(pack); });
      });
    }
  };
})();

// --- Korean strings for the start screen -----------------------------------
// Keyed by the authored English sentence. A field name is a {placeholder} so
// Korean can put it where it reads naturally. The field keys the app stores in
// wb.pack and the `detail.field` of research-field-ready stay English: they are
// machine values, not screen text.
if (typeof window !== 'undefined' && window.WB_I18N) window.WB_I18N.register('ko', {
  'Social Science': '사회과학',
  'Engineering': '공학',
  // The built-in packs keep their own server titles, which are the keys here. A
  // title the server changes has no entry and is shown verbatim, so a built-in
  // never wears a translation that no longer describes it.
  'Engineering: Measurement': '공학: 측정',
  'Constructs, coverage, and the estimand': '구성개념, 포괄 범위, 추정 대상',
  'Quantity, unit, condition, uncertainty': '수량, 단위, 조건, 불확실성',
  'Research': '연구',
  'Literature Review': '문헌 검토',
  'Citation Composer': '인용 작성기',
  // 'Writing' is one word with one translation, owned by i18n.ko.js. A second
  // copy here said 논문 작성 and lost to 집필 depending on load order.
  'Settings': '설정',
  'Home menu': '홈 메뉴',
  'Ask about the data and review the evidence': '데이터에 질문하고 근거를 검토합니다',
  'Sources, notes and documents': '출처, 노트, 문서',
  'Attach evidence to a sentence': '문장에 근거를 붙입니다',
  'Draft and revise the manuscript': '원고를 쓰고 고칩니다',
  'Language, theme, sounds, field': '언어, 테마, 소리, 분야',
  'Nothing waiting': '대기 중인 항목 없음',
  'Open a project to see what the secretary has for you.':
    '프로젝트를 열면 비서가 정리한 내용을 볼 수 있습니다.',
  'Choose a menu to open.': '열 메뉴를 고르세요.',
  'No research field': '연구 분야 없음',
  'Insert the {field} cartridge': '{field} 카트리지를 넣습니다',
  'Inserting the {field} cartridge.': '{field} 카트리지를 넣는 중입니다.',
  'Opening the {field} workbench.': '{field} 작업대를 여는 중입니다.',
  'Choose a research field': '연구 분야를 고르세요',
  'Choose a different research field': '다른 연구 분야를 고르세요',
  'Choose a cartridge to open the workbench.': '카트리지를 골라 작업대를 여세요.',
  'Pick a field to switch context. Cancel keeps {current}.': '분야를 고르면 맥락이 바뀝니다. 취소하면 {current}이 유지됩니다.',
  'the current field': '현재 분야',
  'Research field': '연구 분야',
  'This browser refused to store the field, so it applies to this window only.':
    '이 브라우저가 분야를 저장하지 않아, 이 창에서만 적용됩니다.'
});
