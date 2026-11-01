/* Claim–Evidence Verification (frames F–I of design/mockups/workbench-clarity).
 *
 * This file is the whole client for app/claim_check.py. It adds no route, no
 * model call and no authorization of its own: every action here is a request to
 * the endpoints that already exist, made with the app's own `call()` helper — or,
 * where the response's stable `error` code is what decides what the screen says
 * (a refused prepare) or where the route is free (the numeric reconciliation),
 * with the same origin and body shape read directly, so that code is not lost.
 * The consent, spend and receipt controls stay exactly where they shipped.
 *
 * The one thing this file owns is *how the answer is said*: which status a check
 * is in, what an error code means in plain words, and which of the two things a
 * person is looking at — a verbatim quote check or a model's opinion. That
 * vocabulary is defined once, in MAP below, so a backend string change is one
 * edit here rather than a hunt through the view.
 *
 * Frames:
 *   F  pick the claim, the sources and an optional table — no AI, free
 *   G  the numeric reconciliation appears immediately, from the same prepare call
 *   H  the consent preview: exactly what is sent, where, on which model, for how
 *      much, with execute only on an explicit click
 *   I  the secretary's note: the note first, each quote with a quiet mark saying
 *      where it was found, the leaning as a soft tag, the decision (keep the note
 *      / see it differently with a required reason), the stale badge
 */
(function () {
  'use strict';

  var G = window;
  var doc = G.document;
  var tr = function (text, vars) { return G.WB_I18N ? G.WB_I18N.t(text, vars) : text; };

  // The app's own state objects are declared with const/let in their files, which
  // makes them global *lexical* bindings: reachable by name from any script, but
  // absent from `window`. A `typeof` guard is the only safe way to read them, and
  // it is also what keeps this file loadable before those modules exist. The
  // accessor's own name must differ from the binding it reads, or the function
  // would resolve to itself and never reach the app's state.
  function appState() { return typeof S !== 'undefined' ? S : null; }
  function libState() { return typeof libraryState !== 'undefined' ? libraryState : null; }
  function texState() { return typeof latexState !== 'undefined' ? latexState : null; }
  function callApi() { return typeof call === 'function' ? call : null; }
  function apiBase() {
    try { return API; } catch (_) { return null; }
  }

  // The app's `call()` throws `new Error(data.message || 'HTTP n')`, which keeps
  // the sentence and drops both the status and the stable `error` code. Two
  // routes here are decided by what the refusal *is* rather than what it says:
  // a prepare refused by a gate (`cloud_off`, `account_not_ready`,
  // `local_not_ready`), and an execute refused with a 409 — which can be the same
  // cloud switch turning off between the preview and the send. A message read as
  // prose is exactly the thing the mapping exists to avoid, so those two are read
  // here directly: same origin, same JSON body, same error prose, and the status
  // and code survive.
  async function postForCode(path, body) {
    var base = apiBase();
    if (base == null || typeof fetch !== 'function') throw { code: null, status: 0, message: tr('Could not prepare the check.') };
    var res = await fetch(base + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    var data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) throw { code: (data && data.error) || null, status: res.status,
      message: (data && (data.message || data.error)) || ('HTTP ' + res.status) };
    return data;
  }

  // --- the vocabulary, in one place ---------------------------------------
  // Statuses a check can be in. `terminal` statuses end the story: no verdict to
  // read and nothing to decide. `decidable` ones have a result the researcher can
  // accept or override.
  var MAP = {
    status: {
      prepared: { key: 'prepared', label: 'Waiting for your consent', tone: 'wait',
        note: 'Prepared, not sent. Nothing has been transmitted or charged yet.' },
      running: { key: 'running', label: 'Running', tone: 'wait',
        note: 'The request is with the provider. This screen cannot stop it once it has started.' },
      completed: { key: 'completed', label: 'Checked', tone: 'ok', decidable: true },
      completed_known: { key: 'completed_known', label: 'Checked', tone: 'ok', decidable: true },
      completed_unknown_charge: { key: 'completed_unknown_charge', label: 'Checked', tone: 'ok',
        decidable: true,
        note: 'The result arrived, but the provider did not report its cost. The charge is unknown.' },
      failed_known_charge: { key: 'failed', label: 'Failed', tone: 'fail', terminal: true },
      failed_unknown_charge: { key: 'failed_unknown', label: 'Failed — charge unknown', tone: 'fail',
        terminal: true },
      interrupted_unknown_charge: { key: 'interrupted', label: 'Interrupted — charge unknown',
        tone: 'fail', terminal: true }
    },
    // Error codes, said the way a person would say them. The two terminal codes
    // from the backend are named exactly; anything else falls back to the status
    // text plus the code itself, so an unrecognized code is still visible.
    error: {
      usage_exceeded_reservation: {
        label: 'Stopped: the provider used more than the reserved maximum',
        body: 'The run cost more than the amount the preview reserved. The result was not kept, and the spend ledger keeps the reservation. Nothing was retried automatically.'
      },
      interrupted_unknown_charge: {
        label: 'Interrupted while running — the charge is unknown',
        body: 'The app stopped while this check was running. The request may have reached the provider, so the charge is unknown and the reservation is kept. There is nothing to accept or override here; prepare a new check if you want one.'
      }
    },
    // Why a quote did not count. `verified: true` is the only state that reads as
    // verified; every reason below is stated as a reason it did not.
    quoteReason: {
      not_in_source: 'Not found anywhere in this source.',
      outside_transmitted_excerpt: 'Found in the source, but outside the part that was sent.',
      page_out_of_range: 'The cited page is not in this source.',
      unknown_item: 'The answer named a source that was not sent.',
      empty_quote: 'The answer returned an empty quote.',
      too_short: 'Too short to count (a quote needs 5 words, or 20 characters if it has no spaces).'
    },
    // The verdict as a leaning, never a headline. The secretary suggests; it
    // does not judge. This is the only place the categorical judgement is
    // allowed to surface, and it surfaces as a small tag under the note.
    leaning: {
      supported: 'Leans: supported',
      partially_supported: 'Leans: partly supported',
      contradicted: 'Leans: contradicted',
      not_found: 'Leans: not addressed',
      unverified_evidence: 'Leans: too little to go on'
    },
    // What the secretary says instead of the model's note, keyed on the stable
    // code. `secretary_note_shown: false` is the backend telling the UI to use
    // its own sentence here, in the reader's language. `no_note` needs no entry:
    // it has no code-specific sentence, and the generic last resort below is
    // exactly what it should say.
    secretaryFallback: {
      quotes_not_found_in_sources: 'I tried to back this up, but I couldn\'t find the sentences I was going to quote in your sources, so I\'d hold off on this one for now.',
      // A "not found" check where nothing was verified: the sources themselves do
      // not speak to the claim, which is a different situation from a quote that
      // failed to match, and it gets its own sentence rather than the same one.
      nothing_found_in_sources: 'I looked through the sources you picked but couldn\'t find anything that speaks to this claim. Want to try adding another source?'
    },
    // Staleness reasons. The backend names the object that moved, and for a
    // source it names it by library id (`source:<id>`, `source_missing:<id>`): an
    // id is not a title, so the code is split from its argument and the id is
    // resolved to the title the check itself recorded before anything is shown.
    staleReason: {
      claim: 'your sentence changed after this check',
      source: 'a source changed after this check',
      source_missing: 'a source this check used is gone',
      table: 'the table changed after this check',
      table_missing: 'the table is gone',
      draft_missing: 'the draft this claim was taken from is gone'
    },
    // A prepare refusal that names its obstacle. The card built from these is the
    // whole explanation; the backend's own sentence is only ever a detail under it.
    // One table, keyed by the stable code the backend sends, so a code is mapped
    // in exactly one place.
    blocked: {
      cloud_off: {
        label: 'Cloud use is off, so this check cannot run.',
        body: 'Turn cloud use on in Settings, and make sure an API key is saved for that provider. Nothing was sent.'
      },
      account_not_ready: {
        label: 'This account is not ready for that model, so this check cannot run.',
        body: 'Check the key and the model in the Secretary AI panel, then prepare the preview again. Nothing was sent.'
      },
      local_not_ready: {
        label: 'The local model is not ready, so this check cannot run.',
        body: 'Start the local model server, then prepare the preview again. Nothing was sent, and no API cost was reserved.'
      },
      // A refused *execute* that is not one of the gates above. The backend's own
      // sentence is the detail; this is the lead. Nothing was charged, because a
      // refused send never reaches the provider.
      conflict: {
        label: 'This check was refused, so nothing was sent.',
        body: 'What the preview described is no longer what the app will send. Prepare the preview again and approve the current version.'
      }
    },
    // The two prose fields the preview returns as a stable code plus text. The
    // code is narrated here in the reader's language and the thresholds are
    // filled from the numbers the response carries; a code this build does not
    // know falls back to the backend's own text, which is never paraphrased.
    estimateNote: {
      local_zero_api_cost: 'Local compute is not metered by this app, so the reserved amount is exactly zero. That is a zero-API-cost fact, not a claim about electricity or hardware.',
      conservative_reservation_not_a_bill: 'Conservative reservation, not a predicted bill: the full documented context is reserved at the uncached rate. Actual usage is settled when it is reported.'
    },
    verificationPlan: {
      verbatim_excerpt_only_v1: 'Every returned quote is checked verbatim against the excerpt text actually transmitted above, with Unicode NFKC normalization and whitespace collapsing only; a quote that occurs only outside the transmitted excerpt never counts. A quote counts as evidence only if it is at least {words} words, or at least {chars} non-space characters when it has no spaces; a shorter verbatim quote does not count either.'
    }
  };

  // Field names in the backend's receipt, said in words. The values are printed
  // exactly as recorded; only these labels are ours.
  var RECEIPT_LABEL = {
    job_status: 'Job status',
    outcome: 'Outcome',
    reservation_usd: 'Reservation kept (USD)',
    model: 'Model',
    provider: 'Provider',
    data_use_mode: 'Data use',
    result_integrity: 'Result integrity',
    error_code: 'Error code',
    check_id: 'Check',
    created_at: 'Recorded at'
  };

  /** Everything the view needs to say about a check, derived in one place. */
  function describe(check) {
    var status = MAP.status[check.status] || { key: String(check.status), label: String(check.status), tone: 'wait' };
    var error = check.error_code ? (MAP.error[check.error_code] || null) : null;
    var result = check.result && check.result.verdict ? check.result : null;
    return {
      status: status,
      error: error,
      errorCode: check.error_code || null,
      result: result,
      // A terminal status has no reading and no buttons, whatever else is set.
      terminal: !!status.terminal,
      decidable: !!status.decidable && !!(result || status.key === 'completed_unknown_charge'),
      stale: !!check.stale,
      staleReasons: (check.stale_reasons || []).map(function (reason) {
        return staleReason(check, reason);
      }),
      decided: check.decision || null
    };
  }

  /** One staleness reason said in words, with a source id replaced by its title. */
  function staleReason(check, reason) {
    var text = String(reason == null ? '' : reason);
    var cut = text.indexOf(':');
    var key = cut < 0 ? text : text.slice(0, cut);
    var arg = cut < 0 ? '' : text.slice(cut + 1);
    if (!cut) return tr(MAP.staleReason[key] || text);
    var said = tr(MAP.staleReason[key] || key);
    if (key !== 'source' && key !== 'source_missing') return said;
    // A source is named by library id in the reason and by title in the check's
    // own record, so the record is what the reader is shown: an id is not a name,
    // and a sentence about "source:i7f3" tells a researcher nothing.
    var items = Array.isArray(check.source_items) ? check.source_items : [];
    for (var i = 0; i < items.length; i++) {
      if (items[i] && String(items[i].item_id) === arg) {
        return said + ' — ' + String(items[i].title);
      }
    }
    return said + ' (' + arg + ')';
  }

  // --- state ---------------------------------------------------------------
  var state = {
    mounted: false,
    host: null,
    open: false,         // the sheet is closed until it is asked for
    project: null,
    draft: null,
    claimText: '',
    claimLocation: null,
    picked: {},          // item_id -> true
    tableId: '',
    preview: null,       // last prepare response
    numbers: null,       // the free numeric reconciliation, from the numbers route
    numbersFor: '',      // the claim+table that `numbers` describes
    numbersSent: '',     // the same key, in flight, so a request is not repeated
    blocked: null,       // {code, message} from a prepare refusal
    check: null,         // last check object
    reveal: null,        // the step to bring on screen after the next render
    focus: null,         // 'result' | 'error': where the cursor belongs after a render
    busy: null,          // null, 'prepare', 'execute' or 'decide'
    error: '',
    checks: []           // the list read from the backend on the last opening
  };

  // What is happening, while it is happening. Named once, so the button and the
  // announced line cannot drift apart.
  var BUSY = {
    prepare: 'Preparing the preview…',
    execute: 'Checking…',
    decide: 'Recording your decision…'
  };

  function clear() {
    state.claimText = ''; state.claimLocation = null; state.picked = {};
    state.tableId = ''; state.preview = null; state.check = null; state.error = '';
    state.numbers = null; state.numbersFor = ''; state.numbersSent = '';
    state.blocked = null; state.focus = null;
  }

  // --- small DOM helpers ---------------------------------------------------
  function el(tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function button(label, cls, testid, onClick) {
    var b = el('button', cls, label);
    b.type = 'button';
    if (testid) b.setAttribute('data-testid', testid);
    b.addEventListener('click', onClick);
    return b;
  }

  // Focus a node once it is really on screen. The sheet is painted inside a pane
  // that may still be settling, so the first frame that reports a painted box
  // wins; there is no fixed timer and no loop left running if it never appears.
  function armFocus(target) {
    if (!target) return;
    var tries = 0;
    function attempt() {
      tries++;
      if (target.getClientRects && target.getClientRects().length) {
        if (target.focus) target.focus({ preventScroll: true });
        return;
      }
      if (tries > 60) return;
      if (typeof G.requestAnimationFrame === 'function') G.requestAnimationFrame(attempt);
    }
    attempt();
  }

  // --- the free part: what to check ---------------------------------------
  // Nothing in this section calls a model. The claim is taken from the draft, or
  // typed; the sources are the ones already in the project. It costs nothing and
  // its result (the numeric reconciliation) is computed by ordinary code.
  function draftText() {
    var st = texState();
    if (!st || !st.draft || !Array.isArray(st.draft.files)) return '';
    for (var i = 0; i < st.draft.files.length; i++) {
      var file = st.draft.files[i];
      if (file && file.path === st.draft.main_file && typeof file.content === 'string') return file.content;
    }
    return '';
  }

  function currentSelection() {
    var editor = doc.getElementById('latex-editor');
    if (!editor) return null;
    var start = editor.selectionStart, end = editor.selectionEnd;
    if (typeof start !== 'number' || start === end) return null;
    var text = String(editor.value || '').slice(start, end).trim();
    if (!text) return null;
    return { text: text, location: { file: (texState() && texState().activeFile) || '', start: start, end: end } };
  }

  function libraryItems() {
    var tree = libState() && libState().tree;
    if (!tree || !Array.isArray(tree.items)) return [];
    return tree.items.filter(function (item) { return !!item; });
  }

  // A results table is not prose evidence: the backend refuses one as a source
  // and takes it as `table_item_id` instead, so it is offered only in the table
  // select. Everything else the library holds — PDFs *and* notes, which the
  // backend reads like any other stored text — can be checked against.
  function pickerItems() {
    return libraryItems().filter(function (item) { return item.kind !== 'table'; });
  }

  function tableItems() {
    return libraryItems().filter(function (item) { return item.kind === 'table'; });
  }

  function buildPicker(host) {
    var card = el('section', 'cc-step');
    card.setAttribute('data-testid', 'cc-picker');
    var head = el('div', 'cc-step-head');
    head.appendChild(el('span', 'cc-step-n', '1'));
    head.appendChild(el('h3', 'cc-step-title', tr('Choose what to check')));
    var free = el('span', 'cc-free', tr('Free — no model is called'));
    card.appendChild(free);
    card.appendChild(head);
    card.appendChild(el('p', 'cc-step-help', tr('Pick the sentence you want to check and the sources it should be checked against. This step only reads what is already in the project.')));

    // The claim. Typing is the one interaction this screen must never interrupt:
    // the textarea is the node the cursor is in, so a keystroke updates the state
    // and the two nodes that read it, and nothing else. Re-rendering here would
    // replace the textarea mid-word and drop the cursor to the body.
    var claimWrap = el('div', 'cc-field');
    var claimLabel = el('label', 'cc-label', tr('The claim'));
    claimLabel.setAttribute('for', 'cc-claim');
    claimWrap.appendChild(claimLabel);
    var claim = el('textarea', 'cc-claim');
    claim.id = 'cc-claim';
    claim.setAttribute('data-testid', 'cc-claim');
    claim.rows = 2;
    claim.placeholder = tr('Select a sentence in the draft, or type the claim here.');
    claim.value = state.claimText;
    claimWrap.appendChild(claim);
    var hint = el('span', 'cc-hint', '');
    var prep = button(tr('Preview what will be sent'), 'cc-primary', 'cc-prepare', function () { prepare(); });

    function paintHint() {
      var ready = state.claimText.trim().length > 0;
      var picked = Object.keys(state.picked).length;
      var preparing = state.busy === 'prepare';
      prep.disabled = !!state.busy || !ready;
      // The button says what it is doing while it does it, so a slow prepare
      // never looks like a dead click.
      prep.textContent = preparing ? tr(BUSY.prepare) : tr('Preview what will be sent');
      prep.setAttribute('aria-busy', preparing ? 'true' : 'false');
      hint.textContent = ready
        ? tr('{n} source(s) selected.', { n: picked })
        : tr('Type or select a claim to continue.');
    }

    claim.addEventListener('input', function () {
      state.claimText = claim.value;
      // Typing is a typed claim. A leftover draft location would make prepare
      // send a draft_id whose slice no longer equals this text, and the backend
      // would refuse a claim the person just wrote. The location is the sentence
      // that was actually selected, so it is dropped the moment the text changes
      // by hand, and re-taken only by the selection button.
      state.claimLocation = null;
      // A preview is a promise about a specific claim text, so it stops being
      // true the instant the text changes, and a refusal is about the claim that
      // was prepared. Their card is removed from the page directly — the one
      // thing this handler must not do is re-render, because that rebuilds the
      // textarea holding the cursor.
      if (state.preview || state.blocked) {
        state.preview = null;
        state.blocked = null;
        var staleCard = state.host && state.host.querySelector('[data-testid="cc-consent"]');
        if (staleCard && staleCard.parentNode) staleCard.parentNode.removeChild(staleCard);
      }
      paintHint();
      queueNumbers();
    });

    var take = button(tr('Use the sentence selected in the draft'), 'cc-quiet', 'cc-take-selection', function () {
      var picked = currentSelection();
      if (!picked) { state.error = tr('Select a sentence in the draft first.'); render(); return; }
      state.claimText = picked.text;
      state.claimLocation = picked.location;
      state.preview = null;
      state.blocked = null;
      state.error = '';
      render();
      // The textarea is rebuilt with the sentence in it and the cursor in it,
      // because this is a click, not a keystroke.
      armFocus(doc.getElementById('cc-claim'));
    });
    claimWrap.appendChild(take);
    card.appendChild(claimWrap);

    // The sources: every stored item that carries text, notes included. Only a
    // results table is left out, because a table is not prose evidence — it goes
    // in the table select below, which is the field the backend reads for it.
    var srcWrap = el('fieldset', 'cc-fieldset');
    var legend = el('legend', 'cc-label', tr('Check it against these sources'));
    srcWrap.appendChild(legend);
    var items = pickerItems();
    if (!items.length) {
      srcWrap.appendChild(el('p', 'cc-hint', tr('This project has no sources yet. Add PDFs in Literature Review, then come back.')));
    } else {
      var list = el('div', 'cc-source-list');
      list.setAttribute('data-testid', 'cc-sources');
      items.forEach(function (item) {
        var label = el('label', 'cc-source');
        var box = doc.createElement('input');
        box.type = 'checkbox';
        box.value = String(item.id);
        box.setAttribute('data-testid', 'cc-source-' + item.id);
        box.checked = !!state.picked[item.id];
        box.addEventListener('change', function () {
          if (box.checked) state.picked[item.id] = true; else delete state.picked[item.id];
          state.preview = null;
          paintHint();
        });
        label.appendChild(box);
        label.appendChild(el('span', 'cc-source-title', item.title || tr('Untitled document')));
        if (item.kind === 'note') label.appendChild(el('span', 'cc-source-kind', tr('note')));
        list.appendChild(label);
      });
      srcWrap.appendChild(list);
    }
    card.appendChild(srcWrap);

    // The optional table.
    var tables = tableItems();
    if (tables.length) {
      var tableWrap = el('div', 'cc-field');
      var tableLabel = el('label', 'cc-label', tr('Compare with a table (optional)'));
      tableLabel.setAttribute('for', 'cc-table');
      tableWrap.appendChild(tableLabel);
      var select = doc.createElement('select');
      select.id = 'cc-table';
      select.setAttribute('data-testid', 'cc-table');
      var none = doc.createElement('option');
      none.value = ''; none.textContent = tr('No table');
      select.appendChild(none);
      tables.forEach(function (item) {
        var option = doc.createElement('option');
        option.value = String(item.id);
        option.textContent = item.title || tr('Untitled table');
        select.appendChild(option);
      });
      select.value = state.tableId;
      select.addEventListener('change', function () {
        state.tableId = select.value; state.preview = null; state.numbers = null; state.numbersFor = '';
        queueNumbers();
      });
      tableWrap.appendChild(select);
      card.appendChild(tableWrap);
    }

    // Why this is safe to press.
    card.appendChild(el('p', 'cc-privacy', tr('This step reads the project on this machine. Nothing is sent anywhere until you approve the preview in step 3.')));

    var actions = el('div', 'cc-actions');
    actions.appendChild(prep);
    actions.appendChild(hint);
    card.appendChild(actions);
    paintHint();
    host.appendChild(card);
  }

  // --- step 2: the numbers, instantly -------------------------------------
  // numeric.reconcile runs before any model call, so these numbers are ordinary
  // computation. The free route returns exactly the same object prepare embeds,
  // and it works with cloud use off, so the numbers are available even when the
  // paid half of the feature is refused.
  function numbersCard(numeric) {
    if (!numeric || !Array.isArray(numeric.numbers) || !numeric.numbers.length) return null;
    var card = el('section', 'cc-step');
    card.setAttribute('data-testid', 'cc-numeric');
    var head = el('div', 'cc-step-head');
    head.appendChild(el('span', 'cc-step-n', '2'));
    head.appendChild(el('h3', 'cc-step-title', tr('The numbers in this claim')));
    card.appendChild(head);
    card.appendChild(el('p', 'cc-step-help', tr('Checked by ordinary code against the source text. A matching number does not by itself make the claim true.')));
    var list = el('ul', 'cc-numbers');
    numeric.numbers.forEach(function (n) {
      var li = el('li', 'cc-number cc-' + (n.status || 'unknown'));
      li.setAttribute('data-testid', 'cc-number');
      li.appendChild(el('span', 'cc-number-value', String(n.text)));
      var said = n.status === 'match' ? tr('Appears in the sources')
        : n.status === 'near_mismatch' ? tr('Appears, but not exactly')
          : tr('Not found in the sources');
      li.appendChild(el('span', 'cc-number-status', said));
      if (n.reason) li.appendChild(el('span', 'cc-number-why', n.reason));
      list.appendChild(li);
    });
    card.appendChild(list);
    return card;
  }

  // The numbers are painted into their own slot, never through render(): they
  // arrive from an asynchronous free request that a keystroke started, and
  // re-rendering the sheet to show them would rebuild the textarea the person is
  // typing in.
  function paintNumbers() {
    var slot = state.numbersSlot;
    if (!slot) return;
    slot.textContent = '';
    var numeric = state.numbers
      || (state.preview && state.preview.numeric_reconciliation) || null;
    var card = numbersCard(numeric);
    if (card) slot.appendChild(card);
  }

  /** Which free reconciliation the screen is currently describing. */
  function numbersKey() {
    var text = state.claimText.trim();
    if (!text || !state.tableId) return '';
    return state.tableId + '\u0000' + text;
  }

  // A keystroke asks for the free route once the typing pauses. Nothing is
  // requested for a claim with no table, because reconciliation is a comparison
  // with a table and there would be nothing to compare.
  var numbersTimer = null;
  function queueNumbers() {
    if (numbersTimer) { clearTimeout(numbersTimer); numbersTimer = null; }
    var key = numbersKey();
    if (!key) {
      state.numbers = null; state.numbersFor = '';
      paintNumbers();
      return;
    }
    if (key === state.numbersFor) return;
    numbersTimer = setTimeout(function () { numbersTimer = null; fetchNumbers(); }, 350);
  }

  async function fetchNumbers() {
    var key = numbersKey();
    if (!key || !state.project || state.numbersSent === key) return;
    state.numbersSent = key;
    try {
      var response = await postForCode('/projects/' + encodeURIComponent(state.project) + '/claim-checks/numbers',
        { claim_text: state.claimText.trim(), table_item_id: state.tableId });
      if (numbersKey() !== key) return;  // a later edit owns the screen now
      state.numbers = (response && response.numeric) || null;
      state.numbersFor = key;
    } catch (_) {
      // The free reconciliation is an aid, not the check: a refusal leaves the
      // step exactly as it was, and prepare remains the place that explains.
    } finally {
      if (state.numbersSent === key) state.numbersSent = '';
      paintNumbers();
    }
  }

  // --- step 3: consent -----------------------------------------------------
  // One card for a refusal, whoever refused. The obstacle is named here, in the
  // reader's language, from the code alone; the backend's own sentence follows it
  // as a secondary detail so nothing it said is lost and nothing it said stands
  // in for the explanation. There is never an execute button in this card.
  function buildBlocked(host, block) {
    if (!block) return;
    var known = MAP.blocked[block.code] || null;
    var card = el('section', 'cc-step cc-consent cc-blocked-card');
    card.setAttribute('data-testid', 'cc-consent');
    var head = el('div', 'cc-step-head');
    head.appendChild(el('span', 'cc-step-n', '3'));
    // There is no preview here — the refusal came instead of one — so the card
    // does not promise a transmission it does not have.
    head.appendChild(el('h3', 'cc-step-title', tr('Why this cannot run')));
    card.appendChild(head);

    var warn = el('div', 'cc-blocked');
    warn.setAttribute('role', 'status');
    warn.setAttribute('tabindex', '-1');
    warn.setAttribute('data-testid', 'cc-blocked');
    warn.appendChild(el('strong', null, tr(known ? known.label
      : 'This check cannot run right now.')));
    warn.appendChild(el('p', null, tr(known ? known.body
      : 'Nothing was sent. Resolve the obstacle and prepare the preview again.')));
    if (block.message) {
      var detail = el('p', 'cc-blocked-detail', String(block.message));
      detail.setAttribute('data-testid', 'cc-blocked-detail');
      warn.appendChild(detail);
    }
    card.appendChild(warn);
    // A refusal that came *after* a preview still has its inputs on the picker
    // above, so the way forward is to ask for one again — not to hunt for the
    // obstacle in another panel. A prepare refusal has no such button, because
    // nothing on this screen can resolve it.
    if (block.retry) {
      var actions = el('div', 'cc-actions');
      actions.appendChild(button(tr('Prepare again'), 'cc-secondary', 'cc-prepare-again', function () { prepare(); }));
      card.appendChild(actions);
      card.appendChild(el('p', 'cc-hint', tr('Nothing was sent, so nothing was charged.')));
    } else {
      card.appendChild(el('p', 'cc-hint', tr('No request can be made until the reason above is resolved.')));
    }
    host.appendChild(card);
  }

  function buildConsent(host, preview) {
    var card = el('section', 'cc-step cc-consent');
    card.setAttribute('data-testid', 'cc-consent');
    var head = el('div', 'cc-step-head');
    head.appendChild(el('span', 'cc-step-n', '3'));
    head.appendChild(el('h3', 'cc-step-title', tr('Exactly what will be sent')));
    card.appendChild(head);

    // A preview that itself reports an obstacle (cloud use turned off, the
    // account not ready) is drawn as the same blocked card a refused prepare
    // gets, above the exact content: the run cannot happen, but what would have
    // been sent is still the thing the person came to look at.
    var blockedCode = previewBlock(preview);
    if (blockedCode) {
      var warn = el('div', 'cc-blocked');
      warn.setAttribute('role', 'status');
      warn.setAttribute('data-testid', 'cc-blocked');
      var known = MAP.blocked[blockedCode];
      warn.appendChild(el('strong', null, tr(known.label)));
      warn.appendChild(el('p', null, tr(known.body)));
      card.appendChild(warn);
    }

    var t = preview.transmission || {};
    var facts = el('dl', 'cc-facts');
    function fact(label, value, testid) {
      var dt = el('dt', null, label);
      var dd = el('dd', null, value);
      if (testid) dd.setAttribute('data-testid', testid);
      facts.appendChild(dt); facts.appendChild(dd);
    }
    fact(tr('Sent to'), String(t.destination || tr('unknown')), 'cc-destination');
    fact(tr('Model'), [preview.provider, preview.model].filter(Boolean).join(' · ') || tr('unknown'), 'cc-model');
    fact(tr('Maximum reserved cost'), '$' + Number(preview.maximum_reserved_usd || 0).toFixed(4), 'cc-cost');
    fact(tr('Output tokens allowed'), String(t.max_output_tokens == null ? tr('not reported') : t.max_output_tokens), 'cc-max-output');
    fact(tr('Reasoning'), t.reasoning && t.reasoning.effort
      ? t.reasoning.effort : tr('none requested'), 'cc-reasoning');
    fact(tr('Provider keeps a copy'), t.store ? tr('yes') : tr('no'), 'cc-store');
    fact(tr('Tools offered'), Array.isArray(t.tools) && t.tools.length ? t.tools.join(', ') : tr('none'), 'cc-tools');
    fact(tr('Data use'), String(preview.data_use_mode || tr('unknown')), 'cc-data-use');
    // The note is written by the model in this language, so the reader is told
    // which one will be asked for before they consent. From the preview's own
    // echo when it carries it, so the preview cannot describe a language the
    // request did not send.
    fact(tr('Note language'), languageName(t.language || uiLanguage()), 'cc-language');
    card.appendChild(facts);

    // The pricing note: said from its stable code, so a reader gets it in their
    // own language; the backend's prose is the fallback for a code this build
    // does not know, and is never matched, parsed or paraphrased.
    var noteCode = preview.estimate_note_code;
    var note = noteCode && MAP.estimateNote[noteCode] ? tr(MAP.estimateNote[noteCode])
      : (preview.estimate_note ? String(preview.estimate_note) : '');
    if (note) card.appendChild(el('p', 'cc-note', note));

    // The exact claim text and the exact excerpts.
    var sent = el('div', 'cc-transmission');
    sent.setAttribute('data-testid', 'cc-transmission');
    sent.appendChild(el('h4', 'cc-sub', tr('Your claim, word for word')));
    sent.appendChild(el('blockquote', 'cc-quote-claim', String(t.claim_text || '')));
    (t.sources || []).forEach(function (source) {
      var block = el('div', 'cc-source-sent');
      block.setAttribute('data-testid', 'cc-sent-source');
      block.appendChild(el('h4', 'cc-sub', String(source.title || source.item_id) +
        ' · ' + tr('{n} page(s)', { n: source.page_count })));
      (source.excerpt || []).forEach(function (part) {
        var p = el('p', 'cc-excerpt');
        p.appendChild(el('span', 'cc-page', tr('p. {n}', { n: part.page })));
        p.appendChild(el('span', 'cc-excerpt-text', String(part.text || '')));
        if (part.truncated) p.appendChild(el('span', 'cc-truncated', tr('(cut to fit the window)')));
        block.appendChild(p);
      });
      sent.appendChild(block);
    });
    if (t.table_item_id) sent.appendChild(el('p', 'cc-note', tr('A table ({id}) is included.', { id: String(t.table_item_id) })));
    card.appendChild(sent);

    // The verification rule, from its code plus the numbers the response carries
    // for it: the thresholds are never read out of the English sentence.
    var plan = verificationPlan(preview);
    if (plan) {
      card.appendChild(el('h4', 'cc-sub', tr('How the answer will be checked')));
      card.appendChild(el('p', 'cc-note', plan));
    }

    var actions = el('div', 'cc-actions');
    if (blockedCode) {
      actions.appendChild(el('span', 'cc-hint', tr('No request can be made until the reason above is resolved.')));
    } else {
      // While the run is out, the button says so and a status line says the same
      // thing out loud: the preview stays on screen for the whole wait, so a
      // button that still reads "Send" would look like nothing was happening.
      var running = state.busy === 'execute';
      var run = button(running ? tr(BUSY.execute) : tr('Send this and check it'),
        'cc-primary', 'cc-execute', function (ev) {
          if (ev && ev.currentTarget) ev.currentTarget.disabled = true;
          execute(preview);
        });
      run.disabled = !!state.busy;
      run.setAttribute('aria-busy', running ? 'true' : 'false');
      actions.appendChild(run);
      actions.appendChild(el('span', 'cc-hint', tr('This sends the text above and reserves the maximum above. It cannot be called back once sent.')));
      var listening = el('span', 'cc-hint', running ? tr(BUSY.execute) : '');
      listening.setAttribute('role', 'status');
      listening.setAttribute('aria-live', 'polite');
      listening.setAttribute('data-testid', 'cc-running');
      actions.appendChild(listening);
    }
    card.appendChild(actions);
    card.setAttribute('aria-busy', state.busy === 'execute' ? 'true' : 'false');
    host.appendChild(card);
  }

  /** The obstacle a *preview* reports, in the same vocabulary a refusal uses. */
  function previewBlock(preview) {
    if (!preview) return null;
    if (preview.cloud_enabled === false) return 'cloud_off';
    if (preview.account_ready === false) return 'account_not_ready';
    return null;
  }

  /** The verification rule in the reader's language, thresholds filled in. */
  function verificationPlan(preview) {
    var code = preview && preview.verification_plan_code;
    var template = code && MAP.verificationPlan[code] ? MAP.verificationPlan[code] : null;
    if (!template) return preview && preview.verification_plan ? String(preview.verification_plan) : '';
    var thresholds = preview.verification_plan_thresholds || {};
    return tr(template, {
      words: thresholds.min_quote_words == null ? '—' : thresholds.min_quote_words,
      chars: thresholds.min_quote_nonspace_chars == null ? '—' : thresholds.min_quote_nonspace_chars
    });
  }

  /** The two languages the backend accepts for the note, from the live setting.
   *  The wire value is always 'en' or 'ko': a setting the backend does not know
   *  would be refused as a conflict, so it is normalized here, once. */
  function uiLanguage() {
    var lang = G.WB_I18N && typeof G.WB_I18N.get === 'function' ? G.WB_I18N.get() : 'en';
    return lang === 'ko' ? 'ko' : 'en';
  }

  function languageName(code) {
    return code === 'ko' ? tr('Korean') : tr('English');
  }

  // --- the secretary's voice ----------------------------------------------
  // The assistant suggests; it does not judge. Everything below is phrased as a
  // reading offered to the researcher, and the categorical verdict appears only
  // as a leaning tag. `secretary_note_shown` is the backend's decision about
  // whose sentence leads: when it is false the note does not rest on evidence,
  // so the UI supplies its own localized sentence keyed on `note_code`.
  function sourceTitle(sources, itemId) {
    for (var i = 0; i < sources.length; i++) {
      if (sources[i] && sources[i].item_id === itemId) return String(sources[i].title || '');
    }
    return '';
  }

  /** The main text of the note: the model's own words when they may be shown,
   *  otherwise the UI's sentence for the code, in the reader's language.
   *
   *  The rule for every code: `secretary_note_shown: false` is absolute — the
   *  model's note never becomes the main text, not even when the UI has no
   *  sentence for the code. A stored note is only ever offered as the collapsed
   *  "What the model wrote" detail below. */
  function secretaryNoteText(result) {
    var code = result.note_code || null;
    var note = typeof result.secretary_note === 'string' ? result.secretary_note : '';
    var shown = result.secretary_note_shown !== false;
    if (shown && note) return note;
    if (code && MAP.secretaryFallback[code]) return tr(MAP.secretaryFallback[code]);
    // `no_note`, a code this build does not know, or `shown: false` with no
    // sentence of ours: say something true and short rather than leave the
    // card's main text empty or borrow the model's note for it.
    return tr('I looked at this one but I don\'t have much to add beyond the checks below — nothing here stands out to me either way.');
  }

  /** One quiet mark per quote: where it was found, or plainly that it was not. */
  function quoteMark(sources, quote) {
    if (!quote.verified) return tr('I couldn\'t find this sentence in your sources.');
    var title = sourceTitle(sources, quote.item_id);
    var page = quote.page == null ? '' : tr('p.{n}', { n: quote.page });
    if (title && page) return tr('Found word for word in {title}, {page}', { title: title, page: page });
    if (title) return tr('Found word for word in {title}', { title: title });
    if (page) return tr('Found word for word in the source you sent, {page}', { page: page });
    return tr('Found word for word in the source you sent.');
  }

  /** The numbers the secretary noticed, only when they disagree. Built from the
   *  near-mismatch entries the reconciliation already returned. */
  function secretaryNumbersLine(result) {
    var numeric = result.numeric;
    var numbers = (numeric && Array.isArray(numeric.numbers)) ? numeric.numbers : [];
    var near = [];
    numbers.forEach(function (n) { if (n && n.status === 'near_mismatch') near.push(n); });
    if (!near.length) return null;
    var first = near[0];
    var cell = first.nearest && (first.nearest.cell || first.nearest.value);
    if (cell == null) {
      return tr('One thing: the {number} in your sentence doesn\'t quite match the table.', { number: String(first.text) });
    }
    return tr('One thing: the {number} in your sentence doesn\'t quite match the table ({cell}).',
      { number: String(first.text), cell: String(cell) });
  }

  // --- step 4: the verdict ------------------------------------------------
  function buildResult(host, check) {
    var said = describe(check);
    var card = el('section', 'cc-step cc-result');
    card.setAttribute('data-testid', 'cc-result');
    // The verdict arrives without any act of the reader's — the click was on
    // "Send". Announcing the card politely is what tells a screen reader that the
    // thing it was waiting for is here, and aria-busy says whether it is still
    // being written.
    card.setAttribute('aria-live', 'polite');
    card.setAttribute('aria-busy', state.busy ? 'true' : 'false');

    var head = el('div', 'cc-step-head');
    head.appendChild(el('span', 'cc-step-n', '4'));
    var heading = el('h3', 'cc-step-title', tr('What the check found'));
    heading.id = 'cc-result-heading';
    heading.setAttribute('tabindex', '-1');
    head.appendChild(heading);
    var badge = el('span', 'cc-badge cc-' + said.status.tone, tr(said.status.label));
    badge.setAttribute('data-testid', 'cc-status');
    head.appendChild(badge);
    card.appendChild(head);

    if (said.status.note) card.appendChild(el('p', 'cc-note', said.status.note));

    // A terminal failure says what happened and stops. No verdict, no decision —
    // but the receipt stays reachable, because on an unknown charge it is the
    // record that says what the run cost.
    if (said.terminal) {
      var fail = el('div', 'cc-terminal');
      fail.setAttribute('data-testid', 'cc-terminal');
      fail.appendChild(el('h4', 'cc-sub', tr(said.error ? said.error.label : said.status.label)));
      fail.appendChild(el('p', 'cc-note', said.error ? said.error.body
        : tr('This check ended without a result.')));
      if (said.errorCode) fail.appendChild(el('p', 'cc-code', tr('Code: {code}', { code: said.errorCode })));
      card.appendChild(fail);
      card.appendChild(el('p', 'cc-note', tr('There is nothing to accept or override. The record stays here, and the spend entry keeps its reservation.')));
      var failRow = el('div', 'cc-actions cc-quiet-actions');
      failRow.appendChild(button(tr('Open the trust receipt'), 'cc-quiet', 'cc-receipt', function () { openReceipt(check); }));
      card.appendChild(failRow);
      host.appendChild(card);
      return;
    }

    if (said.stale) {
      var stale = el('div', 'cc-stale');
      stale.setAttribute('data-testid', 'cc-stale');
      stale.appendChild(el('strong', null, tr('This result is stale.')));
      stale.appendChild(el('p', null, tr('It was computed before {reasons}. The verdict below describes the older text, not what is on screen now. Check the claim again to compare the current version.',
        { reasons: said.staleReasons.join(', ') })));
      card.appendChild(stale);
    }

    var result = said.result;
    if (result) {
      // The secretary's reading is the main text, not a verdict banner. The
      // assistant suggests; it does not judge, so what the person reads first is a
      // first-person note from someone who looked at their sources — and the
      // categorical judgement is demoted to a soft leaning underneath it.
      var note = el('div', 'cc-secretary-note');
      note.setAttribute('data-testid', 'cc-secretary-note');
      note.appendChild(el('h4', 'cc-sub', tr('Secretary\'s note')));
      note.appendChild(el('p', 'cc-secretary-text', secretaryNoteText(result)));
      // When the UI's own sentence leads, the model's note is not withheld: it
      // stays reachable, collapsed and clearly secondary, so the reader can see
      // what was written without it reading as the result.
      var modelNote = typeof result.secretary_note === 'string' ? result.secretary_note : '';
      if (result.secretary_note_shown === false && modelNote) {
        var detail = el('details', 'cc-model-note');
        detail.setAttribute('data-testid', 'cc-model-note');
        var summary = el('summary', null, tr('What the model wrote'));
        detail.appendChild(summary);
        detail.appendChild(el('p', 'cc-model-note-text', modelNote));
        note.appendChild(detail);
      }
      card.appendChild(note);

      var quotes = Array.isArray(result.quotes) ? result.quotes : [];
      // The sources a mark can name: the result's own list when it carries one,
      // otherwise the check's, which is where a stored check keeps the titles.
      var sources = Array.isArray(result.source_items) && result.source_items.length
        ? result.source_items
        : (Array.isArray(check.source_items) ? check.source_items : []);
      // "What I checked" — every quote with the quietest possible mark: where it
      // was found, or plainly that it was not. No badge, no colour-only state.
      if (quotes.length) {
        var checked = el('div', 'cc-checked');
        checked.setAttribute('data-testid', 'cc-checked');
        checked.appendChild(el('h4', 'cc-sub', tr('What I checked')));
        var list = el('ul', 'cc-quotes');
        quotes.forEach(function (quote) {
          var li = el('li', 'cc-quote ' + (quote.verified ? 'cc-quote-yes' : 'cc-quote-no'));
          li.setAttribute('data-testid', 'cc-quote');
          li.setAttribute('data-verified', quote.verified ? 'true' : 'false');
          // The mark is the sentence: where it was found, or that it was not.
          var mark = el('span', 'cc-quote-mark', quoteMark(sources, quote));
          li.appendChild(mark);
          li.appendChild(el('blockquote', 'cc-quote-text', String(quote.text || '')));
          if (!quote.verified) {
            li.appendChild(el('span', 'cc-quote-reason',
              MAP.quoteReason[quote.reason] || tr('It did not match the text that was sent.')));
          }
          // A cross-page note is always a note, and always after the verified
          // state. On a quote that did not match it must not soften the "not
          // found" mark above it.
          if (quote.cross_page) {
            li.appendChild(el('span', 'cc-quote-crosspage', quote.verified
              ? tr('This quote spans a page break; the two pages were checked as one span.')
              : tr('The answer cited this across a page break. I still couldn\'t find it as written.')));
          }
          list.appendChild(li);
        });
        checked.appendChild(list);
        card.appendChild(checked);
      } else {
        // Still its own section, under the same heading as a list would be. Left
        // bare, this sentence sat directly below the collapsed "What the model
        // wrote" detail and read as that detail's body rather than as what the
        // checks found.
        var noQuotes = el('div', 'cc-checked');
        noQuotes.setAttribute('data-testid', 'cc-checked');
        noQuotes.appendChild(el('h4', 'cc-sub', tr('What I checked')));
        noQuotes.appendChild(el('p', 'cc-note', tr('The answer came back without any quotes, so there is nothing to check word for word.')));
        card.appendChild(noQuotes);
      }

      // The numbers the secretary noticed, in the same voice.
      var numbersLine = secretaryNumbersLine(result);
      if (numbersLine) card.appendChild(el('p', 'cc-secretary-numbers', numbersLine));

      // The leaning, as a soft tag. It is never a heading and never the first
      // thing read; it qualifies the note above it.
      var leaning = el('p', 'cc-leaning');
      leaning.setAttribute('data-testid', 'cc-leaning');
      leaning.appendChild(el('span', 'cc-leaning-tag',
        tr(MAP.leaning[result.verdict] || String(result.verdict))));
      card.appendChild(leaning);

      // One quiet closing line, in the secretary's voice. The limit of what this
      // feature does is said here once, plainly, and not as a banner.
      card.appendChild(el('p', 'cc-closing', tr('That\'s my reading — you know this work best.')));
    }

    // The decision: two acts, one of them needing a reason.
    if (said.decidable && !said.decided) {
      var decide = el('div', 'cc-decide');
      decide.setAttribute('data-testid', 'cc-decide');
      decide.appendChild(el('h4', 'cc-sub', tr('Your call')));
      decide.appendChild(el('p', 'cc-hint', tr('Keeping my note records that you agree with it. Telling me you see it differently records why, and the note itself is never rewritten.')));
      var noteLabel = el('label', 'cc-label', tr('Reason (required to override)'));
      noteLabel.setAttribute('for', 'cc-note');
      decide.appendChild(noteLabel);
      var note = el('input', 'cc-note-input');
      note.id = 'cc-note';
      note.type = 'text';
      note.setAttribute('data-testid', 'cc-decision-note');
      note.placeholder = tr('Why you decided this');
      decide.appendChild(note);
      var row = el('div', 'cc-actions');
      var accept = button(tr('Keep my note'), 'cc-primary', 'cc-accept', function () {
        decideCheck('accept', note.value);
      });
      var override = button(tr('I see it differently'), 'cc-secondary', 'cc-override', function () {
        decideCheck('override', note.value);
      });
      accept.disabled = state.busy;
      override.disabled = state.busy;
      row.appendChild(accept); row.appendChild(override);
      decide.appendChild(row);
      card.appendChild(decide);
    } else if (said.decided) {
      var recorded = el('div', 'cc-recorded');
      recorded.setAttribute('data-testid', 'cc-recorded');
      recorded.appendChild(el('h4', 'cc-sub', said.decided.decision === 'override'
        ? tr('You see it differently — your reason is on record.')
        : tr('You kept my note.')));
      if (said.decided.note) recorded.appendChild(el('p', 'cc-note', said.decided.note));
      card.appendChild(recorded);
    }

    if (said.decidable || said.status.terminal) {
      var receiptRow = el('div', 'cc-actions cc-quiet-actions');
      receiptRow.appendChild(button(tr('Open the trust receipt'), 'cc-quiet', 'cc-receipt', function () { openReceipt(check); }));
      card.appendChild(receiptRow);
    }
    host.appendChild(card);
  }

  // --- the list of earlier checks -----------------------------------------
  function buildHistory(host) {
    if (!state.checks.length) return;
    var card = el('section', 'cc-step cc-history');
    card.setAttribute('data-testid', 'cc-history');
    var head = el('div', 'cc-step-head');
    head.appendChild(el('h3', 'cc-step-title', tr('Earlier checks in this paper')));
    card.appendChild(head);
    var list = el('ul', 'cc-history-list');
    state.checks.forEach(function (check) {
      var said = describe(check);
      var li = el('li', 'cc-history-row');
      var open = button('', 'cc-history-open', 'cc-history-open', function () {
        state.check = check; state.preview = null; state.error = ''; state.reveal = 'check'; render();
      });
      open.appendChild(el('span', 'cc-history-claim', String(check.claim_text || '').slice(0, 120)));
      // The list now holds both kinds of check, so each row says which it is: one
      // taken from a sentence in the draft, or one typed in. Without this the
      // mixed list would claim a typed claim came from a draft it never had.
      open.appendChild(el('span', 'cc-history-from',
        tr(check.draft_id ? 'from the draft' : 'typed')));
      open.appendChild(el('span', 'cc-badge cc-' + said.status.tone, tr(said.status.label)));
      if (said.stale) open.appendChild(el('span', 'cc-badge cc-stale', tr('stale')));
      if (said.decided) open.appendChild(el('span', 'cc-badge cc-ok', tr(said.decided.decision === 'override' ? 'overridden' : 'accepted')));
      li.appendChild(open);
      list.appendChild(li);
    });
    card.appendChild(list);
    host.appendChild(card);
  }

  // --- rendering -----------------------------------------------------------
  // Frames F–I are a sheet opened from the paper, the way the mockup draws them:
  // the Writing screen keeps its draft, its files and its PDF, and the check
  // appears over them only when it is asked for. Closed, this costs one line.
  function render() {
    if (!state.mounted || !state.host) return;
    var host = state.host;
    // The free numbers live in their own slot and are painted by paintNumbers(),
    // so the render that follows a keystroke never has to own them.
    state.numbersSlot = null;
    host.textContent = '';
    host.classList.toggle('cc-open', !!state.open);

    var bar = el('div', 'cc-sheetbar');
    var title = el('h2', 'cc-title', tr('Claim–evidence verification'));
    title.setAttribute('data-testid', 'cc-title');
    bar.appendChild(title);
    var toggle = button(tr('Check a claim…'), state.open ? 'cc-quiet' : 'cc-secondary', 'cc-open', function () {
      state.open = true;
      // Opening is what asks for this paper's earlier checks: the list is read
      // every time the sheet is opened, because a check can have gone stale
      // since the last look and the badge is the whole value of the list.
      loadChecks();
      render();
    });
    if (state.open) toggle = button(tr('Close'), 'cc-quiet', 'cc-close', function () {
      state.open = false;
      render();
    });
    bar.appendChild(toggle);
    host.appendChild(bar);
    if (!state.open) return;

    var wrap = el('div', 'cc-root');
    wrap.setAttribute('data-testid', 'cc-root');
    wrap.appendChild(el('p', 'cc-lede', tr('Pick a sentence from your draft, choose the sources it should be checked against, then approve exactly what gets sent. The app checks the quotes word for word; the judgement of whether the claim is true stays with you.')));

    if (!state.project) {
      wrap.appendChild(el('p', 'cc-empty', tr('Open a project to check a claim.')));
      host.appendChild(wrap);
      return;
    }

    if (state.error) {
      var err = el('p', 'cc-error', state.error);
      err.setAttribute('role', 'alert');
      err.setAttribute('tabindex', '-1');
      err.setAttribute('data-testid', 'cc-error');
      wrap.appendChild(err);
    }

    // The flow is a sequence, so only the furthest step reached is shown in full:
    // the picker stays, and each later step appears once its input exists.
    buildPicker(wrap);
    var slot = el('div', 'cc-numeric-slot');
    slot.setAttribute('data-testid', 'cc-numeric-slot');
    state.numbersSlot = slot;
    wrap.appendChild(slot);
    if (state.preview) buildConsent(wrap, state.preview);
    if (state.blocked) buildBlocked(wrap, state.blocked);
    if (state.check) buildResult(wrap, state.check);
    buildHistory(wrap);

    host.appendChild(wrap);
    paintNumbers();

    // The sheet scrolls internally, so a step that appears further down would
    // otherwise land below the fold: the button looks like it did nothing.
    // Bring whatever is newly reached into view, without stealing focus.
    if (state.reveal && state.open) {
      var target = SCROLL_TO[state.reveal] && wrap.querySelector(SCROLL_TO[state.reveal]);
      state.reveal = null;
      if (target) target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }

    // A result or a refusal arrives without the reader's cursor going anywhere,
    // so the place the new text starts is focused once it is painted: a heading
    // for a result, the refusal line for an error or a blocked prepare. All are
    // tabindex=-1, so this moves reading position without adding a stop to the
    // tab order.
    if (state.focus) {
      var where = state.focus === 'error'
        ? '[data-testid="cc-error"], [data-testid="cc-blocked"]'
        : '#cc-result-heading';
      state.focus = null;
      var node = wrap.querySelector(where);
      if (node && node.focus) node.focus({ preventScroll: false });
    }
  }

  // Which element to bring into view once a step has been reached.
  var SCROLL_TO = {
    consent: '[data-testid="cc-consent"]',
    check: '[data-testid="cc-result"]'
  };

  // --- talking to the backend ---------------------------------------------
  // The request a prepare *would* send for the state the screen is in right now.
  // Building the body in one place is what lets the answer be checked against the
  // inputs it is an answer about: the same function produces the body that is
  // sent and the body that is compared afterwards, so the two can never drift.
  function prepareBody() {
    var body = {
      claim_text: state.claimText.trim(),
      source_item_ids: Object.keys(state.picked),
      table_item_id: state.tableId || undefined,
      // The note is written by the model in the language the reader is using, so
      // the language is part of what is consented to and part of the hash. It is
      // read from WB_I18N at the moment of the request, and it is in
      // `prepareKey()`, so switching language invalidates a preview prepared in
      // the other one rather than silently sending the wrong language.
      language: uiLanguage()
    };
    // A draft location is only ever sent with the draft it points into, and only
    // when the claim really came from a selection there. A typed claim is a typed
    // claim: sending a draft_id with no location — or with a location whose text
    // no longer matches — would make the backend refuse a claim the person just
    // wrote.
    if (state.claimLocation && state.draft && state.draft.draft_id) {
      body.draft_id = state.draft.draft_id;
      body.claim_location = state.claimLocation;
    }
    return body;
  }

  // Everything a prepare answer is an answer *about*. A preview is a promise that
  // executing it charges for exactly these inputs, so an answer may only be shown
  // while the inputs it described are still the inputs on screen. `open` and
  // `project` are in it too: a preview for a sheet that has been closed, or for a
  // paper that has been left, is not on screen at all. Reading it from live state
  // is the whole point — a snapshot compared with itself would prove nothing.
  function prepareKey() {
    return JSON.stringify([state.open ? 1 : 0, state.project || '', prepareBody()]);
  }

  async function prepare() {
    if (state.busy || !apiBase()) return;
    state.busy = 'prepare'; state.error = ''; state.blocked = null;
    // The free numbers are not cleared here: they are ordinary computation that
    // stays valid whatever the paid half answers, and they are exactly what the
    // screen still has to show when prepare is refused because cloud use is off.
    state.check = null; state.preview = null;
    render();
    try {
      var body = prepareBody();
      var key = prepareKey();
      var response = await postForCode('/projects/' + encodeURIComponent(state.project) + '/claim-checks/prepare', body);
      // The answer describes the inputs that were sent. If any of them moved
      // while it was out — a keystroke, a ticked source, another table, a close, a
      // project switch — this answer is about text that is no longer on screen,
      // and showing it would put a live execute button under a claim the reader
      // never approved. It is dropped instead, and the screen keeps asking for a
      // preview of what is actually there.
      if (prepareKey() !== key) return;
      state.preview = response;
      state.reveal = 'consent';
    } catch (e) {
      var code = e && e.code;
      var message = (e && e.message) || tr('Could not prepare the check.');
      if (code && MAP.blocked[code]) {
        // The obstacle is what the screen says; the backend's sentence is kept
        // beside it as the detail, never as the explanation.
        state.blocked = { code: code, message: message };
        state.focus = 'error';
      } else {
        state.error = tr('Could not prepare the check.') +
          (message ? ' ' + tr('The app said: {detail}', { detail: message }) : '');
        state.focus = 'error';
      }
    } finally {
      state.busy = null; render();
      // The free half is asked for again here, because a refused prepare is
      // exactly when the numbers are the only thing the screen still has to
      // offer: they need no consent, no key and no cloud use.
      queueNumbers();
    }
  }

  // Does the app itself now say cloud use is off? Asked only after an execute
  // was refused, so the extra read costs nothing on the success path. A refusal
  // whose prose names the cloud switch is also accepted as cloud-off, because the
  // backend raises the same plain `Conflict('Cloud mode is off')` on this route
  // and a re-read cannot be more authoritative than the refusal it is explaining.
  async function cloudIsOff(message) {
    if (/cloud mode is off|cloud use is off/i.test(String(message || ''))) return true;
    var base = apiBase();
    if (base == null || typeof fetch !== 'function') return false;
    try {
      var res = await fetch(base + '/ai/registry');
      if (!res.ok) return false;
      var data = await res.json();
      var settings = data && data.settings;
      return !!(settings && settings.cloud_enabled === false);
    } catch (_) { return false; }
  }

  // Which sheet a request was made for: the same project, still open. An answer
  // that arrives after the reader closed the sheet or opened another project
  // belongs to a paper no longer on screen, so it is dropped rather than painted
  // there (the run itself is kept by the backend and listed in its own project).
  function sheetKey(checkId) {
    return JSON.stringify([state.open, state.project, checkId]);
  }

  async function execute(preview) {
    if (state.busy || !callApi()) return;
    state.busy = 'execute'; state.error = ''; render();
    var key = sheetKey(preview.check_id);
    try {
      var response = await postForCode('/projects/' + encodeURIComponent(state.project) +
        '/claim-checks/' + encodeURIComponent(preview.check_id) + '/execute',
        { preview_hash: preview.preview_hash, consent: true });
      if (sheetKey(preview.check_id) !== key) return;
      state.check = response;
      state.preview = null;
      state.reveal = 'check';
      state.focus = 'result';
      await reloadChecks();
    } catch (e) {
      if (sheetKey(preview.check_id) !== key) return;
      var status = e && e.status;
      var message = (e && e.message) || tr('Could not run the check.');
      if (status === 409) {
        // A refused send is a refused send: there is nothing left to press, and
        // the preview it belonged to has been overtaken by whatever caused the
        // conflict. Both go, so the screen cannot offer to send the same thing
        // again as if the refusal had not happened.
        state.preview = null;
        var off = await cloudIsOff(message);
        // The reason is said in the reader's language; the backend's own sentence
        // is kept beneath it as the detail, never as the explanation.
        state.blocked = { code: off ? 'cloud_off' : 'conflict', message: message, retry: true };
        state.focus = 'error';
      } else {
        state.error = message;
        state.focus = 'error';
      }
    } finally {
      state.busy = null; render();
    }
  }

  async function decideCheck(decision, note) {
    if (state.busy || !state.check || !callApi()) return;
    var text = String(note == null ? '' : note).trim();
    if (decision === 'override' && !text) {
      state.error = tr('Tell me why you see it differently — the reason is required.');
      render();
      return;
    }
    state.busy = 'decide'; state.error = ''; render();
    var checkId = state.check.check_id;
    var key = sheetKey(checkId);
    try {
      var payload = { decision: decision };
      if (text) payload.note = text;
      var response = await callApi()('POST',
        '/projects/' + encodeURIComponent(state.project) + '/claim-checks/' +
        encodeURIComponent(checkId) + '/decide', payload);
      if (sheetKey(checkId) !== key) return;
      state.check = response;
      await reloadChecks();
    } catch (e) {
      if (sheetKey(checkId) !== key) return;
      state.error = (e && e.message) || tr('Could not record the decision.');
      state.focus = 'error';
    } finally {
      state.busy = null; render();
    }
  }

  async function openReceipt(check) {
    if (!callApi() || !check) return;
    state.error = '';
    try {
      var receipt = await callApi()('GET', '/projects/' + encodeURIComponent(state.project) +
        '/claim-checks/' + encodeURIComponent(check.check_id) + '/receipt');
      showReceipt(receipt);
    } catch (e) {
      state.error = (e && e.message) || tr('Could not read the receipt.');
      render();
    }
  }

  function showReceipt(receipt) {
    var existing = doc.getElementById('cc-receipt-panel');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var panel = el('div', 'cc-receipt-panel');
    panel.id = 'cc-receipt-panel';
    panel.setAttribute('data-testid', 'cc-receipt-panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', tr('Trust receipt'));
    var head = el('div', 'cc-receipt-head');
    head.appendChild(el('h3', null, tr('Trust receipt')));
    var close = button(tr('Close'), 'cc-quiet', 'cc-receipt-close', function () {
      if (panel.parentNode) panel.parentNode.removeChild(panel);
    });
    head.appendChild(close);
    panel.appendChild(head);
    // The receipt is the backend's own document, so its values are printed as
    // recorded and never paraphrased. Only the field names are labelled: a raw
    // `result_integrity` tells a reader nothing, and an unlabelled `error_code`
    // is the one field that explains an unknown charge.
    var facts = el('dl', 'cc-facts');
    Object.keys(receipt || {}).forEach(function (key) {
      var value = receipt[key];
      if (value == null || typeof value === 'object') return;
      facts.appendChild(el('dt', null, tr(RECEIPT_LABEL[key] || key)));
      var dd = el('dd', null, String(value));
      if (key === 'error_code') dd.setAttribute('data-testid', 'cc-receipt-code');
      facts.appendChild(dd);
    });
    panel.appendChild(facts);

    // The code, said again in plain words, because on an interrupted run this is
    // the only thing that says whether the charge is known.
    var code = receipt && receipt.error_code;
    if (code) {
      var known = MAP.error[code];
      var codeLine = el('p', known ? 'cc-note cc-receipt-error' : 'cc-note',
        known ? known.label + ' — ' + known.body : tr('Code: {code}', { code: String(code) }));
      codeLine.setAttribute('data-testid', 'cc-receipt-errortext');
      panel.appendChild(codeLine);
    }
    var outcome = receipt && receipt.outcome;
    if (outcome) {
      var line = el('p', 'cc-note', tr('Outcome: {outcome}', { outcome: String(outcome) }));
      line.setAttribute('data-testid', 'cc-receipt-outcome');
      panel.appendChild(line);
    }
    doc.body.appendChild(panel);
  }

  async function reloadChecks() {
    if (!callApi() || !state.project) return;
    try {
      // The project's checks, not one draft's. A typed claim is stored with
      // draft_id NULL, so asking for ?draft_id= would hide every typed check the
      // person just paid for and leave "No claims checked yet" standing after a
      // completed run. Both kinds belong in this list, and each row says which it
      // is (see buildHistory), so nothing is shown without saying where it came
      // from.
      var data = await callApi()('GET', '/projects/' + encodeURIComponent(state.project) + '/claim-checks');
      state.checks = (data && data.checks) || [];
    } catch (_) { /* the list is a convenience; the check itself already rendered */ }
  }

  // Read this paper's earlier checks. This runs on every opening of the sheet, not
  // once per project: a check can have gone stale since the last look — the draft
  // or a source may have changed — and a badge read only once is a badge that is
  // wrong. The check on screen is then re-derived from the refreshed row, so the
  // stale banner describes the text that is on screen now, not what was true when
  // the sheet was first opened.
  async function loadChecks() {
    if (!callApi() || !state.project) return;
    await reloadChecks();
    if (state.check) {
      var id = state.check.check_id;
      for (var i = 0; i < state.checks.length; i++) {
        if (state.checks[i] && state.checks[i].check_id === id) { state.check = state.checks[i]; break; }
      }
    }
    render();
  }

  // --- mounting ------------------------------------------------------------
  // The host is the empty container clarity.js left in the Writing view. Mounting
  // is idempotent and never replaces the container itself, so the stable id stays
  // stable for anything else holding a reference to it.
  function mount() {
    var host = doc.getElementById('claim-check-host');
    if (!host) return false;
    if (state.mounted && state.host === host) { render(); return true; }
    state.host = host;
    state.mounted = true;
    render();
    return true;
  }

  function sync() {
    var app = appState();
    var project = app && app.project ? app.project : null;
    var draft = texState() && texState().draft ? texState().draft : null;
    var projectChanged = project !== state.project;
    var draftChanged = (draft && draft.draft_id) !== (state.draft && state.draft.draft_id);
    state.project = project;
    state.draft = draft;
    if (projectChanged) { clear(); state.checks = []; }
    if (draftChanged && !projectChanged) { state.check = null; state.preview = null; }
    mount();
  }

  // The project, the open draft and the library tree are written by modules that
  // announce nothing, so this screen watches the places their change becomes
  // visible: the workspace view switch and the project title. The handle is only
  // recomputed from those values, and rendering is skipped unless it changed, so
  // the observer cannot drive itself through this screen's own writes.
  function watch() {
    var seen = '', seenTree = null;
    function handle() {
      var app = appState();
      var draft = texState() && texState().draft ? texState().draft : null;
      var tree = libState() && libState().tree ? libState().tree : null;
      var items = tree && Array.isArray(tree.items) ? tree.items.length : -1;
      return [app && app.project ? app.project : '', draft && draft.draft_id ? draft.draft_id : '',
        doc.getElementById('claim-check-host') ? 1 : 0, items].join('|');
    }
    function check() {
      // The tree is compared by identity as well as by size: a reload can return
      // a different tree with the same number of sources, and the picker offers
      // what that tree holds.
      var tree = libState() && libState().tree ? libState().tree : null;
      var next = handle();
      if (next === seen && tree === seenTree) return;
      seen = next; seenTree = tree;
      sync();
    }
    check();
    if (typeof G.MutationObserver !== 'function') return;
    var observer = new G.MutationObserver(check);
    var section = doc.querySelector('#app-shell > main > section');
    if (section) observer.observe(section, { childList: true, subtree: true,
      attributes: true, attributeFilter: ['class'] });
    var shell = doc.getElementById('app-shell');
    if (shell) observer.observe(shell, { attributes: true, attributeFilter: ['class'] });
    var title = doc.getElementById('project-title');
    if (title) observer.observe(title, { childList: true, subtree: true, characterData: true });
  }

  // A preview promises a note in the language it was prepared in. When the
  // reader switches language after it landed, that promise no longer matches
  // the screen, so the preview is dropped and must be prepared again; the
  // in-flight case is already caught by `prepareKey()`.
  if (G.WB_I18N) G.WB_I18N.onChange(function () {
    var sent = state.preview && state.preview.transmission && state.preview.transmission.language;
    if (sent && sent !== uiLanguage()) state.preview = null;
    render();
  });

  G.WB_CLAIM_CHECK = {
    mount: mount,
    sync: sync,
    state: state,
    describe: describe,
    map: MAP,
    // How many checks this paper has. clarity.js reads it to decide whether the
    // Writing screen still has nothing to show.
    count: function () { return state.checks.length; },
    // Test seam: the same path the UI takes, without a real backend.
    _prepare: prepare,
    _execute: execute,
    _decide: decideCheck
  };

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', watch);
  else watch();
})();
