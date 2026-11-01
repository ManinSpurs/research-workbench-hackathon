// Secretary review: the gathered past secretary exchanges for the open project.
//
// The researcher keeps what matters and deletes the rest. The list is a RECORD:
// a turn's text is immutable and the only thing this screen may change is its
// status (kept / discarded) or the row itself (delete). Nothing here invents a
// turn, a chip, a timestamp or a count: an empty log renders an honest empty
// line, and the cleared-count line reports the number the server returned.
//
// Same shape as citations.js: the shared `call`, `S`, `showError`, `esc` and a
// `open...View()` function that leaves one working surface mounted at a time.

// Language lookup from i18n.js, with an English fallback so this file still runs
// in a bare JS context that has no window. The alias is its own name because
// every script on the page shares one global scope (app.js owns `t`, library.js
// owns `lt`, citations.js owns `citeT`).
const WB_GLOBAL_SR = (typeof window !== 'undefined') ? window : {};
const srT = (WB_GLOBAL_SR.WB_I18N && WB_GLOBAL_SR.WB_I18N.t) ? WB_GLOBAL_SR.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});

// A keep, a delete or a clear in flight must not be overwritten by a later list
// response, and switching project invalidates both. Same counter idiom as
// citationRequest / libraryState.loading / latexRequest. `busy` names the one
// write in flight, so a second click on a row button cannot send a second one.
let secretaryReviewState = null;
let secretaryReviewRequest = 0;

// The route answers DEFAULT_LIMIT (50) rows when no limit is asked for, while the
// bulk delete clears every discarded row of the project. The list therefore asks
// for the route's maximum, and says so when even that is not the whole log.
const SECRETARY_REVIEW_LIMIT = 200;

function secretaryReviewMount(project) {
  secretaryReviewState = {
    project, turns: [], loaded: false, failed: null, loading: false, busy: '',
    // What GET /chips said about the chip ids. `chipsKnown` is false until a list was
    // actually read, so nothing claims a chip is writable on a guess.
    chipsKnown: false, builtinChips: new Set(), listedChips: new Set(), chipsFailure: '',
  };
  secretaryReviewRequest++;
}

// A project switch drops everything this surface holds. Another project's rows
// must never sit under this project's title, and the bulk delete must never be
// able to spend a count read from one project against another. The surface is
// also closed: after a switch the person is looking at the new project, not at a
// list they did not ask for.
function secretaryReviewReset() {
  // Whether this surface was the one on screen: only then does hiding it have to
  // hand the pressed state back to Research, which is what a project switch
  // returns to. An unmounted review list leaves every tab exactly as another
  // surface left it.
  const view = $('secretary-review-view');
  const wasOpen = !!(view && view.classList && !view.classList.contains('hidden'));
  secretaryReviewRequest++;
  secretaryReviewState = null;
  const host = $('secretary-review-groups');
  if (host) host.replaceChildren();
  const absence = $('secretary-review-absence');
  if (absence) { absence.hidden = true; absence.textContent = ''; }
  const clear = $('secretary-review-clear-discarded');
  if (clear) { clear.hidden = true; clear.disabled = true; }
  if (!wasOpen) { secretaryReviewStatus(''); return; }
  secretaryReviewStatus('');
  closeSecretaryReview();
  const research = $('view-research');
  if (research) research.setAttribute('aria-pressed', 'true');
}

function secretaryReviewVisible(visible) {
  const view = $('secretary-review-view');
  if (view && view.classList) { if (visible) view.classList.remove('hidden'); else view.classList.add('hidden'); }
  const section = document.querySelector('main section');
  if (section && section.classList) {
    if (visible) {
      section.classList.add('secretary-review-mode');
      section.classList.remove('library-mode', 'citation-mode', 'writing-mode');
    } else section.classList.remove('secretary-review-mode');
  }
  // One working surface at a time. Opening the review list closes the other
  // three rather than leaving a hidden panel mounted behind it.
  if (visible) {
    if (typeof closeCitationView === 'function') closeCitationView();
    if (typeof setLibraryViewVisible === 'function') setLibraryViewVisible(false);
    if (typeof setLatexViewVisible === 'function') setLatexViewVisible(false);
  }
  // Hiding resets ONLY this surface's own tab. The surface being opened owns the
  // pressed state of the others: writing every tab to "false" here left Research
  // unpressed after review -> Research, so the workspace showed no selected tab.
  const own = $('view-secretary-review');
  if (!visible) { if (own) own.setAttribute('aria-pressed', 'false'); return; }
  if (own) own.setAttribute('aria-pressed', 'true');
  for (const id of ['view-citations', 'view-writing', 'view-research']) {
    const el = $(id);
    if (el) el.setAttribute('aria-pressed', 'false');
  }
  const library = document.querySelector('[data-testid="view-library"]');
  if (library) library.setAttribute('aria-pressed', 'false');
}

function secretaryReviewStatus(message, tone) {
  const node = $('secretary-review-status');
  if (!node) return;
  node.textContent = message || '';
  if (node.classList) { node.classList.remove('ok', 'fail'); if (tone) node.classList.add(tone); }
  if (tone) node.setAttribute('data-tone', tone); else node.removeAttribute('data-tone');
}

// Every turn the list holds, newest first. The route already answers newest
// first; sorting here repeats that guarantee after a local status change. The
// sort is stable, so two turns recorded in the same instant keep the server's
// own order rather than swapping places on every redraw.
function secretaryReviewOrdered() {
  const turns = (secretaryReviewState && Array.isArray(secretaryReviewState.turns)) ? secretaryReviewState.turns : [];
  const stamp = (turn) => (turn && turn.created_at != null) ? String(turn.created_at) : '';
  return turns.slice().sort((a, b) => {
    const x = stamp(a), y = stamp(b);
    return x < y ? 1 : x > y ? -1 : 0;
  });
}

function secretaryReviewChipOf(turn) {
  return (turn && turn.pack_id != null && String(turn.pack_id).trim() !== '') ? String(turn.pack_id) : '';
}

// Chips in the order their newest turn appears, so the list reads as one stack
// rather than a re-sorted catalogue.
function secretaryReviewGroups(turns) {
  const order = [];
  const groups = new Map();
  for (const turn of turns) {
    const key = secretaryReviewChipOf(turn);
    if (!groups.has(key)) { groups.set(key, []); order.push(key); }
    groups.get(key).push(turn);
  }
  return order.map((key) => ({ packId: key, turns: groups.get(key) }));
}

// A recorded body is text: a plain string, or an object/array of strings. Each
// non-empty string is its own paragraph. No label is invented for a body field,
// and nothing is translated: it is what the secretary actually recorded.
function secretaryReviewTexts(turn) {
  // The contract names this field body_json; the running route answers the same
  // recorded body as `body`. Both are the recorded text, so either is accepted.
  const body = turn ? (turn.body_json != null ? turn.body_json : turn.body) : null;
  const keep = (value) => typeof value === 'string' && value.trim() !== '';
  if (keep(body)) return [body];
  if (Array.isArray(body)) return body.filter(keep);
  if (body && typeof body === 'object') {
    return Object.keys(body).map((key) => body[key]).filter(keep);
  }
  return [];
}

// A secretary turn answers a question; when that researcher turn is in the same
// list, its text is shown on the secretary row so an exchange reads as one.
function secretaryReviewLinkedText(turn, all) {
  if (!turn || turn.role !== 'secretary' || turn.question_id == null) return null;
  const asked = all.find((other) => other && other.id !== turn.id && other.role === 'researcher'
    && other.question_id != null && String(other.question_id) === String(turn.question_id));
  if (!asked) return null;
  const texts = secretaryReviewTexts(asked);
  return texts.length ? texts.join(' ') : null;
}

function secretaryReviewStateLabel(status) {
  if (status === 'kept') return srT('Kept');
  if (status === 'discarded') return srT('Discarded');
  return srT('Recorded');
}

function secretaryReviewSpan(className, text, testid) {
  const span = document.createElement('span');
  span.className = className;
  if (testid) span.setAttribute('data-testid', testid);
  span.textContent = text;
  return span;
}

function secretaryReviewButton(testid, label, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('data-testid', testid);
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function secretaryReviewRow(turn, all) {
  const li = document.createElement('li');
  li.className = 'secretary-review-item';
  li.setAttribute('data-testid', 'secretary-review-item');
  li.dataset.turnId = String((turn && turn.id != null) ? turn.id : '');
  li.dataset.role = String((turn && turn.role) || '');
  li.dataset.status = String((turn && turn.status) || '');
  li.dataset.packId = secretaryReviewChipOf(turn);

  const head = document.createElement('p');
  head.className = 'secretary-review-head';
  head.append(secretaryReviewSpan('secretary-review-role',
    turn && turn.role === 'secretary' ? srT('Secretary') : srT('Researcher'), 'secretary-review-role'));
  // The chip name is the server's own pack_id: it is shown exactly as stored and
  // is never translated.
  head.append(secretaryReviewSpan('muted secretary-review-chip',
    secretaryReviewChipOf(turn) || srT('No chip'), 'secretary-review-chip'));
  head.append(secretaryReviewSpan('muted secretary-review-state',
    secretaryReviewStateLabel(turn && turn.status), 'secretary-review-row-status'));
  head.append(secretaryReviewSpan('muted secretary-review-when',
    (turn && turn.created_at != null && String(turn.created_at).trim() !== '') ? String(turn.created_at) : srT('Undated'),
    'secretary-review-when'));
  li.append(head);

  const asked = secretaryReviewLinkedText(turn, all);
  if (asked) {
    const p = document.createElement('p');
    p.className = 'secretary-review-question muted';
    p.setAttribute('data-testid', 'secretary-review-question');
    const strong = document.createElement('strong');
    strong.textContent = srT('Your question') + ': ';
    p.append(strong, document.createTextNode(asked));
    li.append(p);
  }

  const texts = secretaryReviewTexts(turn);
  if (!texts.length) {
    const p = document.createElement('p');
    p.className = 'secretary-review-text muted';
    p.setAttribute('data-testid', 'secretary-review-text');
    p.textContent = srT('No recorded text.');
    li.append(p);
  } else {
    for (const text of texts) {
      const p = document.createElement('p');
      p.className = 'secretary-review-text';
      p.setAttribute('data-testid', 'secretary-review-text');
      // The recorded text is rendered as text, never as markup.
      p.textContent = text;
      li.append(p);
    }
  }

  const actions = document.createElement('div');
  actions.className = 'row';
  const keep = secretaryReviewButton('secretary-review-keep', srT('Keep'), () => secretaryReviewKeep(turn));
  keep.setAttribute('aria-pressed', String(!!turn && turn.status === 'kept'));
  // A kept turn is already kept; the action says so rather than repeating a
  // request that could only return the status it already has.
  if (turn && turn.status === 'kept') keep.disabled = true;
  const remove = secretaryReviewButton('secretary-review-delete', srT('Delete'), () => secretaryReviewDelete(turn));
  // One write at a time: while a keep, a delete or a bulk clear is in flight
  // every row action is disabled, so a second click cannot send a second
  // request whose answer would contradict the first.
  if (secretaryReviewState && secretaryReviewState.busy) { keep.disabled = true; remove.disabled = true; }
  actions.append(keep, remove);
  li.append(actions);
  return li;
}

// Why this group's conversation cannot be written from here, or '' when it can.
//
// The composed file is not the group on screen: the compile route writes the
// project's turns for that pack id PLUS every chip-less turn of the project, minus
// the discarded ones, minus anything older than the newest 200. So a group with no
// chip id cannot save from this group, while its turns do travel into a chip saved
// from another group of the same project.
//
// The store refuses a built-in pack id, so a shipped chip is shown disabled rather
// than offering a write that is known to fail. And GET /chips is the only thing that
// says which chips are shipped: without a list this does not guess, it stays off.
function secretaryReviewConversationBlock(state, packId) {
  if (!packId) return 'nochip';
  if (!state || !state.chipsKnown) return 'unknown';
  if (state.builtinChips.has(packId)) return 'builtin';
  if (!state.listedChips.has(packId)) return 'unlisted';
  return '';
}

function secretaryReviewConversationNote(reason, packId, state) {
  if (reason === 'nochip') {
    return srT('This group has no chip id, so it cannot save from here. Its turns are still written into a chip saved from another group of this project.');
  }
  if (reason === 'builtin') {
    return srT('This chip ships with the app, so it cannot be written to. Install a chip of your own to save a conversation.');
  }
  if (reason === 'unlisted') {
    return srT('The chip {chip} is not installed in this app, so there is no file to write into.', { chip: packId });
  }
  return srT('The chip list could not be read, so saving is off until it loads: {reason}.',
    { reason: (state && state.chipsFailure) || srT('the chip list could not be read') });
}

// One chip's conversation.txt is written by this control. The compile route
// composes the log out of the project's own records and stores the text inside the
// chip, so the control belongs to the chip group and not to a single turn. A group
// that cannot save is still shown, disabled, and says why — a missing button would
// read as a feature that does not exist.
function secretaryReviewConversationAction(group, busy) {
  const packId = (group && group.packId) ? String(group.packId) : '';
  const blocked = secretaryReviewConversationBlock(secretaryReviewState, packId);
  const row = document.createElement('div');
  row.className = 'row secretary-review-chip-actions';
  row.setAttribute('data-testid', 'secretary-review-chip-actions');
  const noteId = 'secretary-review-save-conversation-note-' + (packId || 'nochip');
  const save = secretaryReviewButton('secretary-review-save-conversation',
    srT('Save conversation to chip'), () => secretaryReviewSaveConversation(packId));
  // The chip id is the server's own pack_id and is never translated; it is
  // carried on the button so the in-place busy pass knows which groups it may
  // re-enable, and `blocked` so a disabled group stays disabled while another
  // write is in flight.
  save.dataset.packId = packId;
  save.dataset.blocked = blocked;
  if (blocked === 'nochip') save.textContent = srT('No chip: the conversation cannot be saved');
  if (blocked || busy) save.disabled = true;
  if (blocked) save.setAttribute('aria-describedby', noteId);
  row.append(save);
  if (blocked) {
    const note = document.createElement('span');
    note.className = 'muted';
    note.id = noteId;
    note.setAttribute('data-testid', 'secretary-review-save-conversation-note');
    note.dataset.blocked = blocked;
    note.textContent = secretaryReviewConversationNote(blocked, packId, secretaryReviewState);
    row.append(note);
  }
  return row;
}

function renderSecretaryReview() {
  const host = $('secretary-review-groups');
  const absence = $('secretary-review-absence');
  const clear = $('secretary-review-clear-discarded');
  const turns = secretaryReviewOrdered();
  const discarded = turns.filter((turn) => turn && turn.status === 'discarded').length;
  const busy = !!(secretaryReviewState && secretaryReviewState.busy);

  if (absence) {
    absence.hidden = true;
    if (!turns.length) {
      // An honest empty state: "nothing was ever recorded" may only be said
      // after a list was actually read and came back empty. A failed read, and
      // a read still in flight, each say their own thing instead.
      if (!secretaryReviewState || !S.project) absence.textContent = srT('Open a project to review its secretary exchanges.');
      else if (secretaryReviewState.failed) absence.textContent = srT('The secretary turn list could not be loaded.');
      else if (!secretaryReviewState.loaded) absence.textContent = srT('Loading secretary turns…');
      else absence.textContent = srT('No secretary turns recorded for this project yet.');
      absence.hidden = false;
    }
  }
  // The clear action appears only when there is something to clear.
  if (clear) {
    clear.hidden = !discarded;
    clear.disabled = !discarded || busy;
  }
  if (!host) return;
  host.replaceChildren();
  if (!turns.length) return;
  // The route answers at most SECRETARY_REVIEW_LIMIT rows while the bulk delete
  // clears every discarded row of the project, so a full page is disclosed
  // rather than passed off as the whole log.
  if (secretaryReviewState && secretaryReviewState.turns.length >= SECRETARY_REVIEW_LIMIT) {
    const note = document.createElement('p');
    note.className = 'muted secretary-review-truncated';
    note.setAttribute('data-testid', 'secretary-review-truncated');
    note.textContent = srT('Only the newest {limit} turns are listed here. Older turns are not shown.',
      { limit: SECRETARY_REVIEW_LIMIT });
    host.append(note);
  }

  const groups = secretaryReviewGroups(turns);
  // One chip = no group headings: the chip line on every row already says it,
  // and a single heading would only repeat the same name above itself.
  const grouped = groups.length > 1;
  for (const group of groups) {
    const box = document.createElement('div');
    box.className = 'secretary-review-group';
    box.setAttribute('data-testid', 'secretary-review-group-box');
    if (grouped) {
      const title = document.createElement('h4');
      title.className = 'secretary-review-group-title';
      title.setAttribute('data-testid', 'secretary-review-group');
      title.dataset.packId = group.packId;
      // The chip title is the stored pack_id and is never translated.
      title.textContent = group.packId || srT('No chip');
      box.append(title);
    }
    const list = document.createElement('ul');
    list.className = 'secretary-review-items';
    for (const turn of group.turns) list.append(secretaryReviewRow(turn, turns));
    box.append(list);
    box.append(secretaryReviewConversationAction(group, busy));
    host.append(box);
  }
}

// GET /chips is the only route that reports a chip's `source`, so it is read once
// per review load and its answer decides which save controls are live. `builtin`
// marks a chip that ships with the app: the store refuses to write into one, so the
// control is disabled instead of offering a write that is known to fail. A read that
// failed is not a guess in either direction: nothing is enabled and the control says
// the list could not be read.
async function secretaryReviewLoadChips(state) {
  try {
    const res = await call('GET', '/chips');
    const rows = (res && Array.isArray(res.chips)) ? res.chips : [];
    const builtin = new Set();
    const listed = new Set();
    for (const chip of rows) {
      const id = (chip && chip.pack_id != null) ? String(chip.pack_id) : '';
      if (!id) continue;
      listed.add(id);
      if (chip.source === 'builtin') builtin.add(id);
    }
    state.builtinChips = builtin;
    state.listedChips = listed;
    state.chipsKnown = true;
    state.chipsFailure = '';
  } catch (e) {
    state.chipsKnown = false;
    state.builtinChips = new Set();
    state.listedChips = new Set();
    state.chipsFailure = serverText(e.message) || '';
  }
}

// `reload` is how a write asks for a fresh read even while another read is in
// flight: the write owns the newest request number, so its read is the one whose
// answer the screen shows.
async function secretaryReviewLoad(options) {
  const reload = !!(options && options.reload);
  const project = S.project;
  if (!project) {
    if (secretaryReviewState) {
      secretaryReviewState.turns = [];
      secretaryReviewState.loaded = false;
      secretaryReviewState.failed = null;
    }
    renderSecretaryReview();
    secretaryReviewStatus('');
    return;
  }
  if (!secretaryReviewState || secretaryReviewState.project !== project) secretaryReviewMount(project);
  const state = secretaryReviewState;
  if (!reload && state.loading) return;
  state.loading = true;
  // A read in flight is not an empty log either: the absence line says the list
  // is being read rather than that nothing was ever recorded.
  if (!state.loaded) renderSecretaryReview();
  const request = ++secretaryReviewRequest;
  try {
    // The route answers DEFAULT_LIMIT (50) rows when no limit is asked for. The
    // bulk delete clears every discarded row of the project, so the list asks for
    // the route's maximum instead of showing a page the actions outrun.
    // The chip list is read beside it, once per load: it decides which save
    // controls may be enabled, and it must be known before the first render.
    const res = (await Promise.all([
      call('GET', `/projects/${encodeURIComponent(project)}/secretary-turns?limit=${SECRETARY_REVIEW_LIMIT}`),
      secretaryReviewLoadChips(state),
    ]))[0];
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    state.turns = (res && Array.isArray(res.turns)) ? res.turns : [];
    state.loaded = true;
    state.failed = null;
    renderSecretaryReview();
  } catch (e) {
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    // A read that failed is not an empty log: the rows already on screen stay,
    // and the absence line says the list could not be loaded rather than that
    // nothing was ever recorded.
    state.failed = serverText(e.message) || srT('the list could not be loaded');
    state.loaded = false;
    renderSecretaryReview();
    secretaryReviewStatus(srT('Secretary review unavailable: {reason}.', { reason: state.failed }), 'fail');
  } finally {
    if (secretaryReviewState === state) state.loading = false;
  }
}

async function openSecretaryReview() {
  const project = S.project;
  if (!project) {
    secretaryReviewVisible(true);
    if (secretaryReviewState) { secretaryReviewState.turns = []; secretaryReviewState.loaded = false; }
    renderSecretaryReview();
    secretaryReviewStatus(srT('Open a project before reviewing secretary exchanges.'), 'fail');
    return;
  }
  // Activation guard: a state left over from another project is dropped and
  // remounted, so a stale row can never act through this project's title.
  if (!secretaryReviewState || secretaryReviewState.project !== project) {
    secretaryReviewReset();
    secretaryReviewMount(project);
  }
  secretaryReviewVisible(true);
  await secretaryReviewLoad();
}

function closeSecretaryReview() {
  secretaryReviewVisible(false);
}

// The one write in flight, applied in place. The buttons keep their nodes, so a
// second click delivered to the same spot lands on a disabled button instead of
// a freshly rendered copy of it.
function secretaryReviewApplyBusy() {
  const busy = !!(secretaryReviewState && secretaryReviewState.busy);
  const host = $('secretary-review-groups');
  if (host) {
    for (const row of host.querySelectorAll('[data-testid="secretary-review-item"]')) {
      const keep = row.querySelector('[data-testid="secretary-review-keep"]');
      const remove = row.querySelector('[data-testid="secretary-review-delete"]');
      if (keep) keep.disabled = busy || row.dataset.status === 'kept';
      if (remove) remove.disabled = busy;
    }
    for (const save of host.querySelectorAll('[data-testid="secretary-review-save-conversation"]')) {
      // A group that cannot save stays disabled whatever else is in flight: no chip
      // id, a built-in chip, or a chip whose source is not known because GET /chips
      // did not answer. Re-enabling one of those here would offer a write that is
      // known to fail.
      save.disabled = busy || !!save.dataset.blocked;
    }
  }
  const clear = $('secretary-review-clear-discarded');
  if (clear) {
    const turns = (secretaryReviewState && Array.isArray(secretaryReviewState.turns)) ? secretaryReviewState.turns : [];
    const discarded = turns.filter((turn) => turn && turn.status === 'discarded').length;
    clear.disabled = busy || !discarded;
  }
}

function secretaryReviewEndBusy(state) {
  if (secretaryReviewState !== state) return;
  state.busy = '';
  secretaryReviewApplyBusy();
}

// Only the status may change. The recorded body is never sent back, so a keep
// cannot rewrite what the secretary said.
async function secretaryReviewKeep(turn) {
  const project = S.project;
  const state = secretaryReviewState;
  if (!project || !turn || turn.id == null || !state || state.busy) return;
  state.busy = 'keep';
  secretaryReviewApplyBusy();
  const request = ++secretaryReviewRequest;
  try {
    const res = await call('PATCH', `/secretary-turns/${encodeURIComponent(turn.id)}`, { status: 'kept' });
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    const status = (res && (res.status === 'kept' || res.status === 'discarded')) ? res.status : 'kept';
    state.turns = state.turns.map((item) => (
      item && String(item.id) === String(turn.id) ? Object.assign({}, item, { status }) : item));
    renderSecretaryReview();
    secretaryReviewStatus(srT('Kept. Only the status changed.'), 'ok');
  } catch (e) {
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    secretaryReviewStatus(srT('Not kept: {reason}.', { reason: serverText(e.message) }), 'fail');
  } finally {
    secretaryReviewEndBusy(state);
  }
}

// One write of one chip's conversation.txt. The compile route reads what the app
// already recorded and stores the composed text inside the chip; it makes no
// model call and deletes nothing, which is why this action asks no question
// first. Only the chip id and the open project id are sent: the route refuses
// any other field, and it refuses a built-in pack id.
async function secretaryReviewSaveConversation(packId) {
  const project = S.project;
  const state = secretaryReviewState;
  const pack = (packId != null) ? String(packId).trim() : '';
  if (!project || !pack || !state || state.busy) return;
  // A group that cannot save sends nothing: no chip id, a shipped chip the store
  // refuses, or a chip whose source GET /chips did not report. The control is
  // already disabled; this keeps a stray activation from becoming a known failure.
  if (secretaryReviewConversationBlock(state, pack)) return;
  state.busy = 'conversation';
  secretaryReviewApplyBusy();
  const request = ++secretaryReviewRequest;
  try {
    const res = await call('POST', `/chips/${encodeURIComponent(pack)}/conversation/compile`,
      { project_id: project });
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) {
      // The write itself completed; only this surface's turn is stale. Saying
      // nothing would hide a save that happened, so the panel is re-read and shows
      // the chip as it now stands. No success line is printed here: the chip's
      // contents are the evidence, and the re-read is what reports them.
      if (S.project === project && secretaryReviewState === state) {
        await secretaryReviewLoad({ reload: true });
      }
      return;
    }
    // The server reports the bytes it actually stored, so the line repeats its
    // number rather than a guess of what the chip now holds.
    const bytes = (res && typeof res.bytes === 'number') ? res.bytes : null;
    secretaryReviewStatus(bytes == null
      ? srT('Conversation saved into chip {chip}.', { chip: pack })
      : srT('Conversation saved into chip {chip} ({bytes} bytes).', { chip: pack, bytes }),
      'ok');
  } catch (e) {
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    const reason = serverText(e.message);
    // The request may have reached the server even though its answer did not, so
    // the chip is read back before anything is claimed: only the stored text says
    // whether the write landed. A chip holding no text was not written.
    let stored = '';
    let readFailed = false;
    try {
      const read = await call('GET', `/chips/${encodeURIComponent(pack)}/conversation`);
      stored = (read && typeof read.text === 'string') ? read.text : '';
    } catch (_) { readFailed = true; }
    if (S.project !== project || secretaryReviewState !== state) return;
    if (!readFailed && stored.trim() !== '') {
      secretaryReviewStatus(srT('The save may have completed: the chip now holds text. Check the chip before saving again ({reason}).',
        { reason }), 'fail');
      await secretaryReviewLoad({ reload: true });
      return;
    }
    secretaryReviewStatus(srT('The conversation was not saved: {reason}.', { reason }), 'fail');
  } finally {
    secretaryReviewEndBusy(state);
  }
}

// One row, gone for good. A kept turn was preserved on purpose, so it asks in
// plain words before the click becomes irreversible; a discarded or merely
// recorded turn is already the row the person marked for removal, so its one
// click is the whole decision and no second question is asked (the bulk action
// exists for exactly that). Either way only one delete can be in flight: a
// second click on the same spot lands on a disabled button.
async function secretaryReviewDelete(turn) {
  const project = S.project;
  const state = secretaryReviewState;
  if (!project || !turn || turn.id == null || !state || state.busy) return;
  state.busy = 'delete';
  secretaryReviewApplyBusy();
  if (turn.status === 'kept') {
    const question = srT('Delete this kept turn? It is deleted for good and cannot be restored.');
    if (!(window.confirm && window.confirm(question))) {
      secretaryReviewStatus(srT('Nothing was deleted.'));
      secretaryReviewEndBusy(state);
      return;
    }
  }
  const request = ++secretaryReviewRequest;
  try {
    await call('DELETE', `/secretary-turns/${encodeURIComponent(turn.id)}`);
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    state.turns = state.turns.filter((item) => !(item && String(item.id) === String(turn.id)));
    renderSecretaryReview();
    secretaryReviewStatus(srT('Turn deleted.'), 'ok');
  } catch (e) {
    if (S.project !== project || secretaryReviewState !== state) return;
    // The request may have reached the server even though its answer did not:
    // re-read the list rather than reporting a failure that did not happen and
    // leaving a row on screen the server no longer has.
    const reason = serverText(e.message);
    await secretaryReviewLoad({ reload: true });
    if (S.project !== project || secretaryReviewState !== state) return;
    const still = state.turns.some((item) => item && String(item.id) === String(turn.id));
    if (still) secretaryReviewStatus(srT('Not deleted: {reason}.', { reason }), 'fail');
    else secretaryReviewStatus(srT('Turn deleted.'), 'ok');
  } finally {
    secretaryReviewEndBusy(state);
  }
}

// A bulk delete asks in plain words first and names how many rows it will take,
// then reports the count the server actually deleted — never the local guess.
async function secretaryReviewClearDiscarded() {
  const project = S.project;
  const state = secretaryReviewState;
  if (!project || !state || state.busy) return;
  const discarded = state.turns.filter((turn) => turn && turn.status === 'discarded');
  if (!discarded.length) return;
  // The route answers at most SECRETARY_REVIEW_LIMIT rows, so a full page does
  // not prove the loaded count is the whole scope. The consent sentence says so
  // instead of naming a number lower than what the action destroys.
  const truncated = state.turns.length >= SECRETARY_REVIEW_LIMIT;
  state.busy = 'clear';
  secretaryReviewApplyBusy();
  const question = truncated
    ? srT('Delete every discarded turn in this project? At least {count} are discarded, and older turns beyond the newest {limit} are not listed here. Kept turns are never deleted.',
      { count: discarded.length, limit: SECRETARY_REVIEW_LIMIT })
    : srT('Delete {count} discarded turn{plural}? Kept turns are never deleted.',
      { count: discarded.length, plural: discarded.length === 1 ? '' : 's' });
  const confirmed = window.confirm ? window.confirm(question) : false;
  if (!confirmed) {
    secretaryReviewStatus(srT('Nothing was deleted.'));
    secretaryReviewEndBusy(state);
    return;
  }
  const request = ++secretaryReviewRequest;
  try {
    const res = await call('POST', `/projects/${encodeURIComponent(project)}/secretary-turns/clear-discarded`);
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    const count = (res && typeof res.deleted === 'number') ? res.deleted : 0;
    // The route removed every discarded row of this project, including any beyond
    // the page shown, so they are dropped here before the re-read: a re-read that
    // fails must not leave a phantom row behind.
    state.turns = state.turns.filter((turn) => !(turn && turn.status === 'discarded'));
    renderSecretaryReview();
    // Then the list is re-read: only the server knows what else changed. The
    // re-read takes the newest request number, so the checks after it are about
    // the project and the mounted state, not this write's own token.
    await secretaryReviewLoad({ reload: true });
    if (S.project !== project || secretaryReviewState !== state) return;
    const reported = srT('Deleted {count} discarded turn{plural}.',
      { count, plural: count === 1 ? '' : 's' });
    if (state.failed) {
      secretaryReviewStatus(srT('{reported} The list could not be refreshed: {reason}.',
        { reported, reason: state.failed }), 'fail');
    } else secretaryReviewStatus(reported, 'ok');
  } catch (e) {
    if (request !== secretaryReviewRequest || S.project !== project || secretaryReviewState !== state) return;
    const reason = serverText(e.message);
    // Nothing may be claimed deleted here, so the rows stay and the list is
    // re-read to show exactly what the server still holds.
    await secretaryReviewLoad({ reload: true });
    if (S.project !== project || secretaryReviewState !== state) return;
    secretaryReviewStatus(srT('Nothing was deleted: {reason}.', { reason }), 'fail');
  } finally {
    secretaryReviewEndBusy(state);
  }
}

// A language switch has to redraw what this file built: the role and status
// words, the field labels and the honest empty line are script output, not
// data-i18n nodes.
if (WB_GLOBAL_SR.WB_I18N && WB_GLOBAL_SR.WB_I18N.onChange) {
  WB_GLOBAL_SR.WB_I18N.onChange(() => { try { renderSecretaryReview(); } catch (_) {} });
}

// The citation composer and the review list follow the same shape, but only the
// project switch matters here: the list holds rows of ONE project and its bulk
// delete acts on the OPEN project, so a switch must drop the rows and close the
// surface rather than leave one project's count pointed at another's store.
// Same wrap idiom as frontend/library.js.
(function watchSecretaryReviewProject() {
  const base = window.openProject;
  if (typeof base !== 'function') return;
  window.openProject = function (...args) {
    const before = S.project;
    const out = base.apply(this, args);
    if (S.project !== before) secretaryReviewReset();
    return out;
  };
})();

// Leaving this surface for another one takes the review mode off the section, so
// the research surface is restored exactly as it was.
(function wrapSecretaryReviewViews() {
  const research = window.showResearchView;
  window.showResearchView = function (...args) {
    if (typeof research === 'function') research.apply(this, args);
    secretaryReviewVisible(false);
  };
  const library = window.openLibraryView;
  window.openLibraryView = function (...args) {
    secretaryReviewVisible(false);
    if (typeof library === 'function') return library.apply(this, args);
  };
  const writing = window.openWritingView;
  window.openWritingView = function (...args) {
    secretaryReviewVisible(false);
    if (typeof writing === 'function') return writing.apply(this, args);
  };
  const citation = window.openCitationView;
  window.openCitationView = function (...args) {
    secretaryReviewVisible(false);
    if (typeof citation === 'function') return citation.apply(this, args);
  };
})();
