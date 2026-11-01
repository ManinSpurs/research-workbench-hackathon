// Research library + workspace layout: collapsible side panels, an expandable
// working surface, a folders+PDF library view, and the Secretary scratch-notes
// pane. Every network call below is a /library route from the backend contract;
// each one fails quietly and visibly in its own panel (status line or muted
// note), never as an uncaught error, and nothing here invents content: an empty
// response renders as empty, and an unavailable local screen says so.

// --- Layout: collapsible side panels + expandable working surface.
const PANELS_KEY = 'wb.panels';
const PANEL_STATE = { projects: true, secretary: true };
let surfaceExpanded = false;

// Language lookup from i18n.js, with an English fallback so this file
// still runs in a bare JS context that has no window (the UI tests).
const WB_GLOBAL_LIB = (typeof window !== 'undefined') ? window : {};
const lt = (WB_GLOBAL_LIB.WB_I18N && WB_GLOBAL_LIB.WB_I18N.t) ? WB_GLOBAL_LIB.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});

function readPanelState() {
  try {
    const raw = localStorage.getItem(PANELS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      for (const key of ['projects', 'secretary']) {
        if (typeof parsed[key] === 'boolean') PANEL_STATE[key] = parsed[key];
      }
    }
  } catch (_) { /* a garbled or blocked preference leaves both panels open */ }
}

function savePanelState() {
  try { localStorage.setItem(PANELS_KEY, JSON.stringify(PANEL_STATE)); } catch (_) {}
}

// Each panel button reports the state it would produce, so screen readers hear
// "expanded"/"collapsed" rather than a bare chevron.
function syncPanelButtons() {
  const projects = $('toggle-projects'), secretary = $('toggle-secretary');
  if (projects) {
    const label = lt(PANEL_STATE.projects ? 'Collapse the projects panel' : 'Expand the projects panel');
    projects.setAttribute('aria-expanded', String(PANEL_STATE.projects));
    projects.setAttribute('aria-label', label);
    projects.setAttribute('title', label);
  }
  if (secretary) {
    const label = lt(PANEL_STATE.secretary ? 'Collapse the secretary panel' : 'Expand the secretary panel');
    secretary.setAttribute('aria-expanded', String(PANEL_STATE.secretary));
    secretary.setAttribute('aria-label', label);
    secretary.setAttribute('title', label);
  }
}

// The grid itself changes columns; the content is hidden as well, so a collapsed
// panel is a narrow rail rather than a dangling empty column.
function applyPanelState() {
  const main = document.querySelector('main');
  if (main && main.classList) {
    main.classList.toggle('wb-collapse-projects', !PANEL_STATE.projects);
    main.classList.toggle('wb-collapse-secretary', !PANEL_STATE.secretary);
    main.classList.toggle('wb-expand-surface', surfaceExpanded);
  }
  syncPanelButtons();
}

function togglePanel(which) {
  if (which !== 'projects' && which !== 'secretary') return;
  PANEL_STATE[which] = !PANEL_STATE[which];
  savePanelState();
  applyPanelState();
}

function syncExpandButton() {
  const btn = $('expand-surface');
  if (!btn) return;
  btn.setAttribute('aria-pressed', String(surfaceExpanded));
  btn.textContent = surfaceExpanded ? lt('Restore panels') : lt('Expand surface');
  btn.setAttribute('title', lt(surfaceExpanded
    ? 'Restore the panels to their previous state'
    : 'Expand the working area, hiding both side panels'));
}

// Expand is temporary: it never writes the saved panel preference, so the second
// press restores exactly the state the researcher left behind.
function toggleSurfaceExpand() {
  surfaceExpanded = !surfaceExpanded;
  applyPanelState();
  syncExpandButton();
  if (surfaceExpanded) {
    const surface = document.querySelector('main section');
    if (surface && typeof surface.scrollIntoView === 'function') {
      try { surface.scrollIntoView({block: 'start'}); } catch (_) {}
    }
  }
}

// --- Library state and rendering.
const libraryState = { project: null, tree: null, folder: null, dragItem: null, selected: null, loading: false,
  // PDF ordering: one request at a time, a token so a slow answer can never
  // repaint a newer list, and the row the pointer is currently over.
  orderBusy: false, orderToken: 0, dropTarget: null, suppressClick: null, dragDropped: false,
  // The open tree read and whether another one was asked for while it ran. A
  // refresh requested by a mutation is never dropped: it is queued behind the
  // read in flight, so a save can never report success over a stale list.
  loadPromise: null, reloadPending: false, loadError: null,
  // One upload batch at a time: two batches would interleave their status lines.
  uploadBusy: false };

// Which folder (and project) the first-pass screen on screen was computed for,
// and the number of the request whose answer is still wanted.
let libraryScreenKey = null;
let libraryScreenSerial = 0;
// The number of the search whose answer is still wanted.
let librarySearchSerial = 0;

function libraryProjectOrError() {
  if (S.project) return S.project;
  libraryStatus(lt('Open a project to use its library.'));
  return null;
}

// The library status line is the one place a library action reports itself. The
// tone is a rendering detail only: 'fail' and 'busy' are announced as weight and
// a rule as well as colour, so the state survives a monochrome screen.
function libraryStatus(message, tone) {
  const box = $('library-status');
  if (!box) return;
  box.textContent = message || '';
  if (tone) box.setAttribute('data-tone', tone); else box.removeAttribute('data-tone');
}

// Busy state for the whole surface while a library request is in flight: the
// region itself says it is busy, so a slow tree read is never silent.
function libraryBusy(busy) {
  const view = $('library-view');
  if (view && view.setAttribute) view.setAttribute('aria-busy', busy ? 'true' : 'false');
}

// A tool button reports its own work: disabled and marked busy while the request
// it started is open, so the button and the status line agree.
function libraryToolBusy(testid, busy) {
  const button = document.querySelector ? document.querySelector(`[data-testid="${testid}"]`) : null;
  if (!button || !button.setAttribute) return;
  if (busy) { button.setAttribute('aria-busy', 'true'); button.setAttribute('disabled', 'true'); }
  else { button.removeAttribute('aria-busy'); button.removeAttribute('disabled'); }
}

function libraryItems() {
  const tree = libraryState.tree;
  return tree && Array.isArray(tree.items) ? tree.items : [];
}

function libraryFolders() {
  const tree = libraryState.tree;
  return tree && Array.isArray(tree.folders) ? tree.folders : [];
}

function libraryFolderById(id) {
  return libraryFolders().find(f => f && f.id === id) || null;
}

function libraryItemById(id) {
  return libraryItems().find(i => i && i.id === id) || null;
}

// A project switch invalidates the held tree: the next view load refetches.
// This is the one place the per-project context is dropped, so the surface's
// busy flags cannot be left set by a request that belonged to the old project.
// It does NOT touch the reader: an open document stays open across a reorder,
// and only an explicit navigation replaces it.
function libraryResetProjectContext() {
  libraryState.tree = null;
  libraryState.folder = null;
  libraryState.selected = null;
  libraryState.dragItem = null;
  libraryState.dropTarget = null;
  // Bumping the token disowns any save still in flight for the old project: the
  // save's own finally is token-guarded, so it will not repaint this one's list.
  libraryState.orderToken += 1;
  libraryState.orderBusy = false;
  libraryState.suppressClick = null;
  libraryState.dragDropped = false;
  libraryState.loadError = null;
  libraryState.reloadPending = false;
  // The list's own busy flag is cleared here, on the new project's surface. It is
  // never cleared from the old save's finally, which is guarded for exactly this
  // reason: an old completion must not report the new list as idle while it saves.
  libraryItemsBusy(false);
  libraryClearDerivedResults('project');
}

function libraryResetIfProjectChanged() {
  if (libraryState.project !== S.project) {
    libraryState.project = S.project;
    libraryResetProjectContext();
  }
}

async function loadLibrary() {
  const project = libraryProjectOrError();
  if (!project) return null;
  // One tree read at a time; a refresh asked for while a read is open is
  // remembered and run by that read before it reports back, so every caller that
  // awaits this receives the list that includes its own change.
  if (libraryState.loadPromise) {
    libraryState.reloadPending = true;
    return libraryState.loadPromise.then(() => (libraryState.reloadPending ? loadLibrary() : null));
  }
  libraryState.loading = true;
  libraryBusy(true);
  const run = (async () => {
    let last = null;
    do {
      libraryState.reloadPending = false;
      last = await loadLibraryOnce(project);
    } while (libraryState.reloadPending && libraryState.project === S.project);
    return last;
  })();
  const done = run.finally(() => {
    libraryState.loadPromise = null;
    libraryState.loading = false;
    libraryBusy(false);
    // The list's own placeholder depends on the loading flag, so it is redrawn
    // once more after the read settles — otherwise a failed read would leave the
    // "loading" line standing forever.
    renderLibraryItems();
  });
  libraryState.loadPromise = done;
  return done;
}

async function loadLibraryOnce(project) {
  // A tree read that overlaps an order save would restore the pre-move sequence.
  // The token tells the two apart: if an order was written while this fetch was
  // open, the save's own answer is the newer truth and this reply is dropped.
  const orderToken = libraryState.orderToken;
  try {
    const tree = await call('GET', `/library/tree?project_id=${encodeURIComponent(project)}`);
    if (libraryState.project !== S.project) return null;
    if (orderToken !== libraryState.orderToken) return null;
    libraryState.tree = (tree && typeof tree === 'object') ? tree : {folders: [], items: []};
    libraryState.loadError = null;
    libraryStatus('');
    renderLibrary();
    return libraryState.tree;
  } catch (e) {
    if (libraryState.project !== S.project) return null;
    // A read that was already open when an order save completed is older than the
    // save: its failure must not blank the newer tree the save returned, exactly
    // as its success would have been refused above.
    if (orderToken !== libraryState.orderToken) return null;
    libraryState.tree = null;
    libraryState.loadError = lt('the tree could not be loaded');
    renderLibrary();
    // Only a plain reason reaches the reader; the exception text stays in the console.
    libraryStatus(lt('Library unavailable: {reason}.', {reason: libraryState.loadError}), 'fail');
    return null;
  }
}

function openLibraryView() {
  if (!S.project) { showError(lt('Open a project before opening its library.')); return; }
  setLibraryViewVisible(true);
  libraryResetIfProjectChanged();
  // The fetch is started first so the list's first paint already says it is
  // loading, rather than claiming the project has no documents.
  const pending = loadLibrary();
  renderLibrary();
  return pending;
}

function setLibraryViewVisible(visible) {
  const section = document.querySelector('main section');
  if (section && section.classList) {
    if (visible) { section.classList.add('library-mode'); section.classList.remove('writing-mode'); }
    else section.classList.remove('library-mode');
  }
  const view = $('library-view');
  if (view && view.classList) {
    if (visible) view.classList.remove('hidden'); else view.classList.add('hidden');
  }
  // Opening the library leaves the citation composer: one working surface at a
  // time, so a hidden panel can never hold focus or a half-typed proposal.
  if (visible && typeof closeCitationView === 'function') closeCitationView();
  // The tab buttons are keyed by data-testid (the surface div owns the id).
  const tab = (name) => document.querySelector(`[data-testid="${name}"]`);
  const library = tab('view-library'), research = tab('view-research'), writing = tab('view-writing');
  if (library) library.setAttribute('aria-pressed', String(!!visible));
  if (visible) {
    if (research) research.setAttribute('aria-pressed', 'false');
    if (writing) writing.setAttribute('aria-pressed', 'false');
  }
}

// A first-pass screen answers a question about ONE project and ONE folder, so a
// change to either makes the answer on screen stale. Search is project-wide (the
// route takes project_id and the query only), so a folder change leaves it valid
// and only a project change clears it. Neither ever closes the reader.
function libraryClearDerivedResults(scope) {
  const clear = (id) => {
    const box = $(id);
    if (!box) return;
    if (box.replaceChildren) box.replaceChildren();
    box.hidden = true;
    if (box.removeAttribute) box.removeAttribute('aria-busy');
  };
  if (scope !== 'project' && scope !== 'folder') return;
  clear('library-screen-result');
  libraryScreenKey = null;
  libraryScreenSerial += 1;
  // The screen button belongs to the result box that was just dropped, so it is
  // released here. The request that owned it can no longer speak for this
  // context: its own finally is serial-guarded and will not clear a newer one.
  libraryToolBusy('library-screen', false);
  if (scope === 'project') {
    clear('library-search-results');
    librarySearchSerial += 1;
    // Same for the search button: the query it started belongs to the project
    // that just went away, so the new project must be able to search.
    libraryToolBusy('library-search-run', false);
    const input = $('library-search');
    if (input) { try { input.value = ''; } catch (_) {} }
  }
}

// Selecting a folder is a context change: the list redraws for the new folder and
// any result computed for the old one is dropped rather than left standing.
function librarySelectFolder(folderId) {
  const next = folderId ?? null;
  const changed = libraryState.folder !== next;
  libraryState.folder = next;
  if (changed) libraryClearDerivedResults('folder');
  renderLibrary();
}

// --- Folder tree.
function libraryFolderName(id) {
  const f = libraryFolderById(id);
  return f && f.name ? String(f.name) : null;
}

function libraryChildrenOf(parentId) {
  return libraryFolders().filter(f => f && (f.parent_id ?? null) === (parentId ?? null));
}

function libraryDocsInFolder(folderId) {
  // No folder selected = every document. A folder shows only its own items.
  if (folderId == null) return libraryItems();
  return libraryItems().filter(i => i && (i.folder_id ?? null) === folderId);
}

function libraryNotesInFolder(folderId) {
  return libraryDocsInFolder(folderId).filter(i => i && i.kind === 'note');
}

function libraryRenderFolderRow(folder, depth, seen) {
  if (seen) seen.add(folder.id);
  const li = document.createElement('li');
  li.className = 'library-folder-row';
  li.dataset.folderId = folder.id;
  li.dataset.depth = String(depth);
  li.dataset.testid = 'library-folder';

  const row = document.createElement('div');
  row.className = 'library-folder-line';
  if (libraryState.folder === folder.id) row.classList.add('selected');

  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'library-folder-open';
  open.textContent = (folder.name != null && String(folder.name).trim() !== '') ? String(folder.name) : lt('Untitled folder');
  if (libraryState.folder === folder.id) open.setAttribute('aria-current', 'true');
  open.addEventListener('click', (e) => {
    e.preventDefault();
    // A drop that ends on this folder line must not also navigate the tree.
    if (libraryClickSuppressed(libraryFolderScope(folder.id))) return;
    librarySelectFolder(libraryState.folder === folder.id ? null : folder.id);
  });
  open.addEventListener('dblclick', (e) => { e.preventDefault(); libraryStartRename(folder.id); });

  // A dragged document row drops into this folder and is filed there.
  row.addEventListener('dragover', (e) => {
    if (!libraryState.dragItem && !(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'))) return;
    e.preventDefault();
    row.classList.add('drop-target');
  });
  row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
  row.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    row.classList.remove('drop-target');
    // A drop of any kind ends the gesture: the following click is not a request
    // to open the row underneath.
    libraryState.dragDropped = true;
    librarySuppressClick(libraryFolderScope(folder.id));
    const dragged = libraryState.dragItem;
    if (dragged) { libraryState.dragItem = null; libraryMoveItem(dragged, folder.id); return; }
    const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
    if (files.length) libraryUploadFiles(files, folder.id);
  });

  row.append(open);
  li.append(row);

  const kids = libraryChildrenOf(folder.id);
  if (kids.length && depth < 12) {
    const sub = document.createElement('ul');
    sub.className = 'library-folder-children';
    for (const kid of kids) {
      if (seen && seen.has(kid.id)) continue;
      if (seen) seen.add(kid.id);
      sub.append(libraryRenderFolderRow(kid, depth + 1, seen));
    }
    li.append(sub);
  }
  return li;
}

function renderLibraryFolders() {
  const box = $('library-folder-list');
  if (!box) return;
  const rows = [];
  const all = document.createElement('li');
  all.className = 'library-folder-row';
  all.dataset.testid = 'library-folder-all';
  const allBtn = document.createElement('button');
  allBtn.type = 'button';
  allBtn.className = 'library-folder-open';
  allBtn.textContent = lt('All documents');
  if (libraryState.folder == null) { allBtn.setAttribute('aria-current', 'true'); allBtn.classList.add('selected'); }
  allBtn.addEventListener('click', (e) => { e.preventDefault(); librarySelectFolder(null); });
  all.append(allBtn);
  rows.push(all);

  // Roots first; a folder whose parent is unknown is a root too, so a reparented
  // or deleted parent never hides its children. `seen` guards a cyclic parent
  // chain: each folder appears in the tree exactly once.
  const known = new Set(libraryFolders().map(f => f && f.id));
  const seen = new Set();
  const walk = (folder, depth) => {
    if (!folder || seen.has(folder.id) || depth > 12) return;
    seen.add(folder.id);
    rows.push(libraryRenderFolderRow(folder, depth, seen));
  };
  const roots = libraryFolders().filter(f => f && ((f.parent_id ?? null) === null || !known.has(f.parent_id)));
  for (const root of roots) walk(root, 0);

  if (box.replaceChildren) box.replaceChildren(...rows);
  else box.innerHTML = '';
}

// Inline folder creation: the row is an input, Enter commits, Escape cancels.
// No prompt dialog, so the flow is keyboard-reachable and scriptable.
function libraryCreateFolder() {
  if (!libraryProjectOrError()) return;
  const parent = libraryState.folder;
  libraryStartInlineInput({
    testid: 'new-folder-input',
    label: lt('New folder name'),
    placeholder: lt('Folder name'),
    onCommit: async (name) => {
      if (!name) return;
      const project = libraryProjectOrError();
      if (!project) return;
      try {
        await call('POST', '/library/folders', {project_id: project, name, parent_id: parent});
        await loadLibrary();
        libraryStatus(lt('Folder "{name}" created.', {name}), 'ok');
      } catch (e) {
        libraryStatus(lt('Folder not created: {reason}.', {reason: lt('the request failed')}), 'fail');
      }
    },
  });
}

function libraryStartRename(fid) {
  const folder = libraryFolderById(fid);
  if (!folder) { libraryStatus(lt('Select a folder to rename.')); return; }
  libraryStartInlineInput({
    testid: 'rename-folder-input',
    label: lt('Folder name'),
    placeholder: lt('Folder name'),
    value: folder.name != null ? String(folder.name) : '',
    onCommit: async (name) => {
      if (!name) return;
      try {
        await call('PATCH', `/library/folders/${encodeURIComponent(fid)}`, {name});
        await loadLibrary();
        libraryStatus(lt('Folder renamed to "{name}".', {name}), 'ok');
      } catch (e) {
        libraryStatus(lt('Folder not renamed: {reason}.', {reason: lt('the request failed')}), 'fail');
      }
    },
  });
}

function libraryRenameSelectedFolder() {
  if (!libraryState.folder) { libraryStatus(lt('Select a folder in the tree to rename it.')); return; }
  libraryStartRename(libraryState.folder);
}

function libraryStartInlineInput({testid, label, placeholder, value, onCommit}) {
  const box = $('library-folder-list');
  if (!box) return;
  const existing = box.querySelector ? box.querySelector(`[data-testid="${testid}"]`) : null;
  if (existing) { if (existing.focus) existing.focus(); return; }
  const li = document.createElement('li');
  li.className = 'library-folder-row library-inline-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.dataset.testid = testid;
  input.setAttribute('aria-label', label);
  input.placeholder = placeholder;
  input.value = value || '';
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const text = String(input.value || '').trim();
    if (li.parentNode) li.parentNode.removeChild(li);
    if (commit) onCommit(text);
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  // A blur with nothing typed is a cancel. A blur with text is not a decision the
  // researcher made: the row stays open with their words in it rather than
  // throwing them away, and Escape is still the explicit way to discard it.
  input.addEventListener('blur', () => {
    if (String(input.value || '').trim() === '') finish(false);
  });
  li.append(input);
  box.append(li);
  if (input.focus) input.focus();
  // The tree can be longer than its pane, so a new row has to be brought into
  // view: an input scrolled out of sight is the same as no input at all.
  if (input.scrollIntoView) { try { input.scrollIntoView({block: 'nearest'}); } catch (_) {} }
}

// --- Item rows, day-grouped notes, viewer.
// Note bodies are held for this session only: the tree listing carries a note's
// day and length, and the composer's own text is remembered locally so a note can
// be reopened right after it is written. Older notes say so rather than claim an
// empty body.
const libraryNoteTexts = new Map();
function libraryRememberNote(item, text) {
  if (item && item.id != null) libraryNoteTexts.set(item.id, String(text));
}
function libraryNoteText(item) {
  if (!item) return null;
  if (item.text != null) return String(item.text);
  const held = libraryNoteTexts.get(item.id);
  return held != null ? held : null;
}

function libraryDayOf(item) {
  if (item && item.day != null && String(item.day).trim() !== '') return String(item.day);
  if (item && item.created_at != null && String(item.created_at).trim() !== '') return String(item.created_at).slice(0, 10);
  return 'Undated';
}

function libraryRenderItemRow(item) {
  const li = document.createElement('li');
  li.className = 'library-item-row';
  li.dataset.testid = 'library-item';
  li.dataset.itemId = item.id;
  li.dataset.kind = item.kind || '';
  li.draggable = true;
  li.tabIndex = 0;
  // One dragged-item id serves both gestures: a folder row files the document,
  // a PDF row re-orders it. The drop target decides which one it was.
  const isPdf = item.kind !== 'note';
  li.addEventListener('dragstart', (e) => {
    libraryState.dragItem = item.id;
    libraryState.dropTarget = null;
    libraryState.dragDropped = false;
    li.classList.add('dragging');
    if (e && e.dataTransfer) { try { e.dataTransfer.effectAllowed = 'move'; } catch (_) {} }
  });
  li.addEventListener('dragend', () => {
    libraryState.dragItem = null;
    li.classList.remove('dragging');
    libraryClearDropIndicators();
    libraryState.dragDropped = false;
  });

  const open = () => {
    if (libraryClickSuppressed(libraryRowScope(item.id))) return;
    libraryOpenItem(item.id);
  };
  li.addEventListener('click', open);
  li.addEventListener('keydown', (e) => {
    if (isPdf && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      libraryMovePdfByKeyboard(item.id, e.key === 'ArrowUp' ? -1 : 1);
      return;
    }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });

  // The row that is open in the reader carries both a class and an ARIA state,
  // so the current document is announced, not merely tinted.
  if (libraryState.selected != null && String(libraryState.selected) === String(item.id)) {
    li.classList.add('selected');
    li.setAttribute('aria-current', 'true');
  }

  // A PDF carries a handle so dragging reads as "move this document", while the
  // row itself stays a plain click-to-read target. A note never gets one: notes
  // are grouped by day and never enter the PDF ordering.
  if (isPdf) {
    li.dataset.reorder = 'true';
    li.setAttribute('aria-keyshortcuts', 'Alt+ArrowUp Alt+ArrowDown');
    const handle = document.createElement('span');
    handle.className = 'library-drag-handle';
    handle.setAttribute('aria-hidden', 'true');
    handle.title = lt('Drag to reorder, or focus the row and press Alt+ArrowUp / Alt+ArrowDown');
    handle.textContent = '⠿';
    li.append(handle);
    li.addEventListener('dragover', (e) => libraryRowDragOver(e, li, item));
    li.addEventListener('dragleave', (e) => {
      // Moving onto a child element is not leaving this row.
      if (e && e.relatedTarget && li.contains && li.contains(e.relatedTarget)) return;
      libraryClearDropIndicators();
    });
    li.addEventListener('drop', (e) => libraryRowDrop(e, li, item));
  }

  const title = document.createElement('span');
  title.className = 'library-item-title';
  title.textContent = (item.title != null && String(item.title).trim() !== '') ? String(item.title)
    : (item.kind === 'note' ? lt('Untitled note') : lt('Untitled document'));
  li.append(title);

  const meta = document.createElement('span');
  meta.className = 'library-item-meta muted';
  if (item.kind === 'pdf') {
    // English keeps its plural suffix in the code; the Korean entry ignores {plural}.
    const pages = Number.isFinite(item.pages)
      ? lt(' · {count} page{plural}', {count: item.pages, plural: item.pages === 1 ? '' : 's'})
      : '';
    meta.textContent = `PDF${pages}`;
    if (item.text_status === 'NO_TEXT_LAYER') meta.textContent += lt(' · no text layer');
    li.append(meta);
  }
  // A note carries no size line: the day header above it already places it, and
  // the stored title is the researcher's own wording. Repeating "note" beside a
  // note title would only add noise.
  return li;
}

function renderLibraryItems() {
  const box = $('library-items');
  if (!box) return;
  const items = libraryDocsInFolder(libraryState.folder);
  const title = $('library-folder-title');
  if (title) {
    title.textContent = libraryState.folder == null
      ? lt('All documents')
      : (libraryFolderName(libraryState.folder) || lt('Folder'));
  }
  const rows = [];
  const pdfs = items.filter(i => i && i.kind !== 'note');
  const notes = items.filter(i => i && i.kind === 'note');
  for (const doc of pdfs) rows.push(libraryRenderItemRow(doc));
  // Notes group by the day they were written, newest day first. The day header
  // is the stored day itself; the UI never asks for a date and never guesses one.
  const byDay = new Map();
  for (const note of notes) {
    const day = libraryDayOf(note);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(note);
  }
  const days = [...byDay.keys()].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  for (const day of days) {
    const header = document.createElement('li');
    header.className = 'library-day-header';
    header.dataset.testid = 'library-day';
    header.dataset.day = day;
    // The stored day is shown as it is; only this file's own fallback word is
    // translated, so a date from the backend never changes language.
    header.textContent = day === 'Undated' ? lt('Undated') : day;
    rows.push(header);
    for (const note of byDay.get(day)) rows.push(libraryRenderItemRow(note));
  }
  // A list with no rows is three different states, and they must not look alike:
  // a tree that is still being fetched says so, a failed fetch says it could not
  // be read, and only a real answer claims the folder is empty. The placeholder
  // carries no item id or test hook, so nothing can mistake it for a document.
  //
  // The project-level empty answer is the one case this file leaves unsaid: the
  // screen's own pane above the list states it, with the next action named, and
  // two lines saying the same thing is the thing that made a new project look
  // broken. A folder that is empty keeps its line, because the pane speaks for
  // the project and not for the folder inside it.
  if (!rows.length && !(libraryState.tree != null && libraryState.folder == null)) {
    const empty = document.createElement('li');
    empty.className = 'library-empty';
    empty.dataset.testid = 'library-empty';
    if (libraryState.tree == null && libraryState.loadError) {
      empty.textContent = lt('The library could not be loaded: {reason}.', {reason: libraryState.loadError});
      empty.dataset.state = 'error';
    } else if (libraryState.tree == null && libraryState.loading) {
      empty.textContent = lt('Loading this project\'s library…');
      empty.dataset.state = 'loading';
    } else if (libraryState.tree == null) {
      // No fetch has run for this project yet: the list is unknown, not empty.
      empty.textContent = lt('Open the library to load this project\'s documents.');
      empty.dataset.state = 'idle';
    } else {
      empty.textContent = lt('This folder is empty.');
      empty.dataset.state = 'empty';
    }
    rows.push(empty);
  }
  if (box.replaceChildren) box.replaceChildren(...rows);
}

// --- PDF ordering: drag a document row onto another to place it before or
// after it. Only PDFs are ordered; notes keep their day grouping and never move.
// The saved order lives in the backend (PUT /library/order), so the sequence
// survives a reload; nothing about it is kept in localStorage.
function libraryPdfSequence() {
  // Every PDF in the project, in the order the tree returned: the full sequence
  // the order route expects, not just the ones this folder shows.
  return libraryItems().filter(i => i && i.kind !== 'note').map(i => i.id).filter(id => id != null);
}

function libraryVisiblePdfIds() {
  return libraryDocsInFolder(libraryState.folder).filter(i => i && i.kind !== 'note')
    .map(i => i.id).filter(id => id != null);
}

// The order route takes the whole project, so a reorder inside one folder only
// rewrites the slots its visible PDFs already occupy; a document filed in
// another folder keeps the position it had.
function libraryReorderPdfIds(dragId, targetId, place) {
  const visible = libraryVisiblePdfIds();
  if (visible.indexOf(dragId) < 0 || visible.indexOf(targetId) < 0 || dragId === targetId) return null;
  const next = visible.filter(id => id !== dragId);
  const at = next.indexOf(targetId);
  if (at < 0) return null;
  next.splice(at + (place === 'after' ? 1 : 0), 0, dragId);
  if (next.length === visible.length && next.every((id, i) => id === visible[i])) return null;
  const full = libraryPdfSequence();
  const visibleSet = new Set(visible);
  let cursor = 0;
  return full.map(id => (visibleSet.has(id) ? next[cursor++] : id));
}

function libraryTreeSnapshot() {
  return libraryState.tree ? JSON.parse(JSON.stringify(libraryState.tree)) : null;
}

// Replaces the local PDF order so the move is visible at once and can be undone
// exactly if the route refuses it.
function libraryApplyLocalOrder(ids) {
  const tree = libraryState.tree;
  if (!tree || !Array.isArray(tree.items)) return;
  const position = new Map(ids.map((id, index) => [id, index]));
  const pdfs = [];
  for (const item of tree.items) {
    if (item && item.kind !== 'note' && position.has(item.id)) pdfs.push(item);
  }
  pdfs.sort((a, b) => position.get(a.id) - position.get(b.id));
  // Notes keep the exact slots they held; only the PDF queue is rebuilt.
  const merged = [];
  let p = 0;
  for (const item of tree.items) {
    if (item && item.kind !== 'note' && position.has(item.id)) merged.push(pdfs[p++]);
    else merged.push(item);
  }
  tree.items = merged;
}

// The document list itself reports the save, so a slow reorder is visible on the
// surface that changed rather than only in the status line.
function libraryItemsBusy(busy) {
  const list = $('library-items');
  if (!list || !list.setAttribute) return;
  list.setAttribute('aria-busy', busy ? 'true' : 'false');
}

// One suppression rule for every drag that ended in a drop on the library
// surface: a row reorder, a folder-row drop and the drop zone all end with a
// mouse-up whose click belongs to the drop, not to whatever is underneath. The
// gate names the one target that received the drop and is dropped again as soon
// as the current task ends, so it can never eat a later deliberate click — the
// synthetic click a browser fires from a drop always arrives inside that task.
function librarySuppressClick(scope) {
  const gate = {scope: scope || null, until: Date.now() + 400};
  libraryState.suppressClick = gate;
  setTimeout(() => { if (libraryState.suppressClick === gate) libraryState.suppressClick = null; }, 0);
}

function libraryClickSuppressed(scope) {
  const gate = libraryState.suppressClick;
  if (!gate) return false;
  libraryState.suppressClick = null;
  return gate.until > Date.now() && (gate.scope == null || gate.scope === scope);
}

function libraryRowScope(itemId) { return `row:${itemId}`; }
function libraryFolderScope(folderId) { return `folder:${folderId}`; }

function libraryClearDropIndicators() {
  libraryState.dropTarget = null;
  if (document.querySelectorAll) {
    for (const row of document.querySelectorAll('.library-item-row')) {
      if (!row.classList) continue;
      row.classList.remove('drop-before');
      row.classList.remove('drop-after');
      row.removeAttribute('data-drop');
    }
    for (const line of document.querySelectorAll('.library-folder-line.drop-target')) {
      if (line.classList) line.classList.remove('drop-target');
    }
    for (const zone of document.querySelectorAll('.library-dropzone.drop-target')) {
      if (zone.classList) zone.classList.remove('drop-target');
    }
  }
}

function libraryDropPlaceFor(row, event) {
  // Half-and-half so a row has one unambiguous before and one after zone.
  let rect = null;
  try { rect = row.getBoundingClientRect ? row.getBoundingClientRect() : null; } catch (_) { rect = null; }
  if (!rect || !rect.height) return 'after';
  const y = event && Number.isFinite(event.clientY) ? event.clientY : rect.top + rect.height / 2;
  return y < rect.top + rect.height / 2 ? 'before' : 'after';
}

function libraryRowDragOver(e, row, item) {
  const draggedId = libraryState.dragItem;
  if (!draggedId || draggedId === item.id || libraryState.orderBusy) return;
  const dragged = libraryItemById(draggedId);
  // A note is never dropped into the PDF queue, and a PDF only lands on a PDF.
  if (!dragged || dragged.kind === 'note' || item.kind === 'note') return;
  const place = libraryDropPlaceFor(row, e);
  if (!libraryReorderPdfIds(draggedId, item.id, place)) return;
  if (e && e.preventDefault) e.preventDefault();
  if (e && e.dataTransfer) { try { e.dataTransfer.dropEffect = 'move'; } catch (_) {} }
  if (libraryState.dropTarget === `${item.id}:${place}` && row.classList.contains(`drop-${place}`)) return;
  libraryClearDropIndicators();
  libraryState.dropTarget = `${item.id}:${place}`;
  row.classList.add(`drop-${place}`);
  row.setAttribute('data-drop', place);
}

function libraryRowDrop(e, row, item) {
  const draggedId = libraryState.dragItem;
  libraryState.dragItem = null;
  libraryClearDropIndicators();
  if (document.querySelectorAll) {
    for (const other of document.querySelectorAll('.library-item-row.dragging')) other.classList.remove('dragging');
  }
  if (!draggedId || libraryState.orderBusy) return false;
  const dragged = libraryItemById(draggedId);
  if (!dragged || dragged.kind === 'note' || item.kind === 'note') return false;
  if (draggedId === item.id) return false;
  const place = libraryDropPlaceFor(row, e);
  const next = libraryReorderPdfIds(draggedId, item.id, place);
  if (!next) return false;
  if (e && e.preventDefault) e.preventDefault();
  if (e && e.stopPropagation) e.stopPropagation();
  // The gesture ends here: the click that follows the mouse-up belongs to the
  // reorder, not to the reader.
  libraryState.dragDropped = true;
  librarySuppressClick(libraryRowScope(item.id));
  librarySaveOrder(next);
  return true;
}

// Keyboard path for the same move: Alt+ArrowUp / Alt+ArrowDown on a focused PDF
// row shifts it one place inside the folder's visible PDF list.
function libraryMovePdfByKeyboard(itemId, delta) {
  if (libraryState.orderBusy) { libraryStatus(lt('Saving the new order…')); return; }
  const visible = libraryVisiblePdfIds();
  const at = visible.indexOf(itemId);
  if (at < 0) return;
  const targetAt = at + delta;
  if (targetAt < 0 || targetAt >= visible.length) {
    libraryStatus(delta < 0 ? lt('This document is already first.') : lt('This document is already last.'));
    return;
  }
  const next = libraryReorderPdfIds(itemId, visible[targetAt], delta < 0 ? 'before' : 'after');
  if (!next) return;
  librarySaveOrder(next, {focusId: itemId});
}

async function librarySaveOrder(ids, options) {
  const project = libraryProjectOrError();
  if (!project || !Array.isArray(ids) || !ids.length) return;
  if (libraryState.orderBusy) {
    libraryStatus(lt('Saving the new order…'));
    return;
  }
  const token = ++libraryState.orderToken;
  const before = libraryTreeSnapshot();
  libraryState.orderBusy = true;
  libraryApplyLocalOrder(ids);
  renderLibraryItems();
  libraryStatus(lt('Saving the new order…'));
  // The library surface says it is busy, so a slow save is never silent — and the
  // open reader is left exactly as it is.
  libraryItemsBusy(true);
  try {
    const tree = await call('PUT', '/library/order', {project_id: project, item_ids: ids});
    if (token !== libraryState.orderToken || libraryState.project !== project) return;
    if (tree && typeof tree === 'object' && Array.isArray(tree.items)) {
      libraryState.tree = {folders: Array.isArray(tree.folders) ? tree.folders : libraryFolders(), items: tree.items};
    }
    renderLibrary();
    libraryStatus(lt('Document order saved.'), 'ok');
  } catch (e) {
    if (token !== libraryState.orderToken || libraryState.project !== project) return;
    // Roll back to the order that was on screen before the move: the list and the
    // reader stay exactly as the researcher left them.
    if (before) libraryState.tree = before;
    renderLibrary();
    // The route's own plain reason when it sends one (a 409 explains exactly what
    // was wrong with the list), this file's honest fallback otherwise.
    const reason = (e && e.message && String(e.message).trim()) ? String(e.message) : lt('the request failed');
    libraryStatus(lt('Order not saved: {reason}.', {reason}), 'fail');
  } finally {
    if (token === libraryState.orderToken) {
      libraryState.orderBusy = false;
      libraryItemsBusy(false);
      const focusId = options && options.focusId;
      if (focusId != null && document.querySelector) {
        const row = document.querySelector(`.library-item-row[data-item-id="${String(focusId).replace(/"/g, '')}"]`);
        if (row && row.focus) row.focus();
      }
    }
  }
}

function libraryClearNode(id) {
  const box = $(id);
  if (!box) return null;
  if (box.replaceChildren) box.replaceChildren();
  else box.innerHTML = '';
  return box;
}

// The reader's current document is marked on its index row too, so the list and
// the reader never disagree about what is open.
function libraryMarkSelectedRow(itemId) {
  if (!document.querySelectorAll) return;
  const rows = document.querySelectorAll('.library-item-row');
  for (const row of rows) {
    const isCurrent = itemId != null && row.dataset && row.dataset.itemId != null
      && String(row.dataset.itemId) === String(itemId);
    if (row.classList) {
      if (isCurrent) row.classList.add('selected'); else row.classList.remove('selected');
    }
    if (isCurrent) row.setAttribute('aria-current', 'true'); else row.removeAttribute('aria-current');
  }
}

// The viewer shows the stored PDF through the backend file URL (the browser's
// own PDF pane inside an iframe) or a note's stored text. Nothing is generated.
function libraryOpenItem(itemId, highlight) {
  const item = libraryItemById(itemId);
  const box = $('library-viewer');
  if (!box) return null;
  libraryState.selected = itemId || null;
  libraryMarkSelectedRow(libraryState.selected);
  box.hidden = false;
  libraryClearNode('library-viewer');
  if (!item) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = lt('This item is no longer in the library.');
    box.append(p);
    return null;
  }
  const heading = document.createElement('h4');
  heading.textContent = (item.title != null && String(item.title).trim() !== '') ? String(item.title) : lt('Untitled');
  box.append(heading);

  if (item.kind === 'note') {
    const stored = libraryNoteText(item);
    const text = document.createElement('p');
    text.className = 'library-note-text';
    text.dataset.testid = 'library-note-text';
    if (stored == null) {
      // The tree listing carries no note body and this session never saw this
      // note's text. Say so instead of showing an empty pane.
      text.className = 'muted';
      text.textContent = lt('This note was saved in an earlier session; the library listing does not include its text.');
    } else {
      text.textContent = stored;
    }
    box.append(text);
  } else {
    const frame = document.createElement('iframe');
    // The file itself comes from the backend route; the query keeps the call
    // inside the same project_id contract as every other /library call.
    const query = S.project ? `?project_id=${encodeURIComponent(S.project)}` : '';
    frame.src = `${API}/library/docs/${encodeURIComponent(item.id)}/file${query}`;
    frame.title = heading.textContent;
    frame.className = 'library-pdf-frame';
    box.append(frame);
    if (item.text_status === 'NO_TEXT_LAYER') {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = lt('This PDF has no text layer. Search and the first-pass screen cannot read it; the pages are shown as scanned images.');
      box.append(note);
    }
  }
  if (highlight && highlight.snippet != null) {
    const line = document.createElement('p');
    line.className = 'library-snippet';
    line.dataset.testid = 'library-snippet';
    const page = Number.isFinite(highlight.page) ? lt('Page {number}: ', {number: highlight.page}) : '';
    line.textContent = page + String(highlight.snippet);
    box.append(line);
  }
  return box;
}

// --- Uploads (drop zone, file input, and drops on a folder row).
function libraryPickFiles(event) {
  const input = $('library-file-input');
  if (!input) return;
  if (event && event.target && event.target.id === 'library-file-input') return;
  if (input.click) input.click();
}

function libraryBytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// The backend refuses a PDF over 40 MiB, so the same limit is checked here before
// the bytes are read: a 200 MiB file is never turned into base64 only to be
// refused, and the researcher hears about it straight away.
const LIBRARY_MAX_PDF_BYTES = 40 * 1024 * 1024;

async function libraryUploadFiles(fileList, folderId) {
  const project = libraryProjectOrError();
  if (!project) return;
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const input = $('library-file-input');
  // Two batches at once would interleave their status lines and their reloads, so
  // the second batch is refused plainly instead of racing the first.
  if (libraryState.uploadBusy) {
    libraryStatus(lt('An upload is already running. Wait for it to finish before adding more files.'), 'fail');
    if (input) { try { input.value = ''; } catch (_) {} }
    return;
  }
  libraryState.uploadBusy = true;
  const target = folderId !== undefined ? folderId : libraryState.folder;
  const added = [], failed = [];
  try {
    for (const file of files) {
      const name = file.name || lt('a file');
      const looksPdf = /\.pdf$/i.test(file.name || '') || file.type === 'application/pdf';
      if (!looksPdf) {
        failed.push(lt('{name}: only PDF files are accepted here', {name}));
        continue;
      }
      if (Number.isFinite(file.size) && file.size > LIBRARY_MAX_PDF_BYTES) {
        failed.push(lt('{name}: the file is larger than 40 MiB', {name}));
        continue;
      }
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        await call('POST', '/library/docs', {
          project_id: project, folder_id: target ?? null, filename: file.name || 'document.pdf',
          pdf_base64: libraryBytesToBase64(bytes),
        });
        added.push(name);
      } catch (e) {
        // The route's own reason when it gives one, this file's honest fallback otherwise.
        const reason = (e && e.message && String(e.message).trim()) ? String(e.message) : lt('the upload failed');
        failed.push(lt('{name}: {reason}', {name, reason}));
      }
    }
    if (input) { try { input.value = ''; } catch (_) {} }
    // The refresh is awaited so the summary below never describes a list that has
    // not caught up with what was just written. A refresh that fails does not undo
    // the upload, but it must not be reported as a clean success either: the list
    // the researcher is looking at has not caught up.
    let refreshFailed = false;
    if (added.length) {
      await loadLibrary();
      // The list's own state is the authority, not this call's return value: a
      // refresh queued behind a read already in flight resolves to the queued
      // answer, so only the held tree can say whether the list actually came back.
      refreshFailed = libraryState.loadError != null;
    }
    // One summary for the whole batch, and a batch with any failure reads as a
    // failure: a success line must never overwrite the files that did not arrive.
    const plural = added.length === 1 ? '' : 's';
    const where = target ? (libraryFolderName(target) || lt('the folder')) : null;
    const okLine = added.length
      ? (where ? lt('{count} PDF file{plural} added to {folder}.', {count: added.length, plural, folder: where})
        : lt('{count} PDF file{plural} added.', {count: added.length, plural}))
      : '';
    if (failed.length) {
      const failedLine = lt('{count} file{plural} not added: {detail}.',
        {count: failed.length, plural: failed.length === 1 ? '' : 's', detail: failed.join('; ')});
      // A refresh failure is a third fact: it is stated beside the batch result
      // rather than replacing it, so neither the file outcomes nor the fact that
      // the list is behind is lost.
      const staleLine = refreshFailed ? lt(' the list could not be refreshed.') : '';
      libraryStatus(okLine ? `${okLine} ${failedLine}${staleLine}` : `${failedLine}${staleLine}`, 'fail');
    } else if (okLine) {
      if (refreshFailed) {
        libraryStatus(`${okLine} ${lt('The list could not be refreshed: reload the library to see it.')}`, 'fail');
      } else {
        libraryStatus(okLine, 'ok');
      }
    }
  } finally {
    libraryState.uploadBusy = false;
  }
}

function libraryMoveItem(itemId, folderId) {
  const project = libraryProjectOrError();
  if (!project || !itemId) return;
  return (async () => {
    try {
      await call('PATCH', `/library/items/${encodeURIComponent(itemId)}`, {folder_id: folderId ?? null});
      await loadLibrary();
      libraryStatus(lt('Moved to {folder}.', {folder: libraryFolderName(folderId) || lt('no folder')}), 'ok');
    } catch (e) {
      libraryStatus(lt('Not moved: {reason}.', {reason: lt('the request failed')}), 'fail');
    }
  })();
}

// --- Notes.
// The composer's Save button reports its own state while the note is written, so
// a slow save is visible on the control that started it, not only in the status
// line beside the tree.
function librarySetNoteBusy(busy) {
  const button = document.querySelector && document.querySelector('[data-testid="note-save"]');
  if (!button || !button.setAttribute) return;
  if (busy) {
    button.setAttribute('aria-busy', 'true');
    button.setAttribute('disabled', 'true');
  } else {
    button.removeAttribute('aria-busy');
    button.removeAttribute('disabled');
  }
}

async function librarySaveNote() {
  const project = libraryProjectOrError();
  if (!project) return;
  const input = $('note-input');
  const text = input ? String(input.value || '').trim() : '';
  if (!text) { libraryStatus(lt('Write something before saving the note.')); return; }
  librarySetNoteBusy(true);
  libraryStatus(lt('Saving note…'));
  try {
    const saved = await call('POST', '/library/notes', {project_id: project, folder_id: libraryState.folder ?? null, text});
    // Keep the body for this session so the new note opens with its own words.
    if (saved && saved.id != null) libraryRememberNote({id: saved.id}, saved.text != null ? saved.text : text);
    if (input) input.value = '';
    await loadLibrary();
    libraryStatus(lt('Note saved.'), 'ok');
  } catch (e) {
    libraryStatus(lt('Note not saved: {reason}.', {reason: lt('the request failed')}), 'fail');
  } finally {
    librarySetNoteBusy(false);
  }
}

// The Secretary scratch pane writes through the same notes route. It sends no
// folder, so the note lands in the project library ungrouped, and the Library
// view still shows it under the day it was written.
async function saveQuickNote() {
  const status = $('quick-note-status');
  const input = $('quick-note-input');
  if (status) status.textContent = '';
  if (!S.project) { if (status) status.textContent = lt('Open a project to save a note.'); return; }
  const text = input ? String(input.value || '').trim() : '';
  if (!text) { if (status) status.textContent = lt('Write something before saving the note.'); return; }
  try {
    const saved = await call('POST', '/library/notes', {project_id: S.project, folder_id: null, text});
    if (saved && saved.id != null) libraryRememberNote({id: saved.id}, saved.text != null ? saved.text : text);
    if (input) input.value = '';
    if (status) status.textContent = lt('Saved to the Library.');
    // The library tree, if open, must not keep showing a stale list.
    if ($('library-view') && $('library-view').classList) loadLibrary();
  } catch (e) {
    if (status) status.textContent = lt('Note not saved: {reason}.', {reason: lt('the request failed')});
  }
}

// --- Search.
async function librarySearch() {
  const project = libraryProjectOrError();
  if (!project) return;
  const input = $('library-search');
  const q = input ? String(input.value || '').trim() : '';
  const box = $('library-search-results');
  if (!box) return;
  if (!q) {
    box.hidden = true;
    if (box.replaceChildren) box.replaceChildren();
    return;
  }
  box.hidden = false;
  const waiting = document.createElement('p');
  waiting.className = 'muted';
  waiting.textContent = lt('Searching…');
  if (box.replaceChildren) box.replaceChildren(waiting);
  // The results block itself is the busy region while the query is open, so the
  // waiting state is announced and styled as the same element the answer lands in.
  if (box.setAttribute) box.setAttribute('aria-busy', 'true');
  libraryToolBusy('library-search-run', true);
  const serial = ++librarySearchSerial;
  // A query is answered for the project it was asked in: a project switch while
  // it runs makes the reply stale, so it is dropped rather than drawn.
  const current = () => serial === librarySearchSerial && libraryState.project === S.project;
  try {
    const hits = await call('GET', `/library/search?project_id=${encodeURIComponent(project)}&q=${encodeURIComponent(q)}`);
    if (!current()) return;
    if (box.replaceChildren) box.replaceChildren();
    const list = Array.isArray(hits) ? hits : [];
    const summary = document.createElement('p');
    summary.className = 'muted';
    summary.dataset.testid = 'library-search-summary';
    summary.textContent = list.length
      ? lt('{count} matches for "{query}".', {count: list.length, query: q})
      : lt('No stored text matches "{query}".', {query: q});
    box.append(summary);
    list.forEach((hit, index) => {
      if (!hit || typeof hit !== 'object') return;
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'library-hit';
      row.dataset.testid = `library-hit-${index}`;
      const page = Number.isFinite(hit.page) ? lt('Page {number} · ', {number: hit.page}) : '';
      row.textContent = `${hit.title != null ? String(hit.title) : lt('Document')} · ${page}${hit.snippet != null ? String(hit.snippet) : ''}`;
      row.addEventListener('click', () => {
        if (hit.item_id != null) {
          const item = libraryItemById(hit.item_id);
          // Following a hit reveals the document in its own folder. The result
          // list is deliberately kept: it is the researcher's search, not a
          // folder-scoped answer, so navigating from it must not erase it. A
          // first-pass screen for the old folder is dropped, as it would be for
          // any other folder change.
          if (item && item.folder_id != null) librarySelectFolder(item.folder_id);
          else renderLibrary();
          libraryOpenItem(hit.item_id, {page: hit.page, snippet: hit.snippet});
        }
      });
      box.append(row);
    });
  } catch (e) {
    if (!current()) return;
    if (box.replaceChildren) box.replaceChildren();
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = lt('Search unavailable: {reason}.', {reason: lt('the request failed')});
    box.append(p);
  } finally {
    if (current()) {
      libraryToolBusy('library-search-run', false);
      if (box.removeAttribute) box.removeAttribute('aria-busy');
    }
  }
}

// --- First-pass screen. Renders exactly what the route returns. An unavailable
// local model is stated plainly in one muted line; no summary is invented and no
// other model is substituted.
function libraryScreenText(entry) {
  if (entry == null) return '';
  if (typeof entry === 'string') return entry;
  if (typeof entry !== 'object') return String(entry);
  const bits = [];
  for (const key of ['title', 'screen', 'first_pass', 'classification', 'decision', 'label', 'status']) {
    if (entry[key] != null && String(entry[key]).trim() !== '') bits.push(String(entry[key]).trim());
  }
  for (const key of ['note', 'summary', 'reason', 'detail', 'text']) {
    if (entry[key] != null && String(entry[key]).trim() !== '') bits.push(String(entry[key]).trim());
  }
  return bits.join(' — ');
}

async function libraryScreen() {
  const project = libraryProjectOrError();
  if (!project) return;
  const box = $('library-screen-result');
  if (!box) return;
  const items = libraryDocsInFolder(libraryState.folder).filter(i => i && i.kind !== 'note');
  const ids = items.map(i => i.id).filter(id => id != null);
  // The screen answers a question about ONE folder: if the researcher moves to
  // another folder or project while it runs, the answer is stale on arrival and
  // must not be drawn under the new folder's heading.
  const key = `${project}\u0000${libraryState.folder ?? ''}`;
  const serial = ++libraryScreenSerial;
  libraryScreenKey = key;
  box.hidden = false;
  if (box.replaceChildren) box.replaceChildren();
  const waiting = document.createElement('p');
  waiting.className = 'muted';
  waiting.textContent = lt('Running the first-pass screen…');
  box.append(waiting);
  if (box.setAttribute) box.setAttribute('aria-busy', 'true');
  libraryToolBusy('library-screen', true);
  try {
    await libraryScreenRequest(project, ids, box, key, serial);
  } finally {
    libraryToolBusy('library-screen', false);
    if (key === libraryScreenKey && box.removeAttribute) box.removeAttribute('aria-busy');
  }
}

async function libraryScreenRequest(project, ids, box, key, serial) {
  let res;
  const current = () => serial === libraryScreenSerial && libraryScreenKey === key
    && libraryState.project === project
    && `${project}\u0000${libraryState.folder ?? ''}` === key && box === $('library-screen-result');
  try {
    res = await call('POST', '/library/screen', {project_id: project, item_ids: ids});
  } catch (e) {
    if (!current()) return;
    if (box.replaceChildren) box.replaceChildren();
    const p = document.createElement('p');
    p.className = 'muted';
    p.dataset.testid = 'library-screen-message';
    p.textContent = lt('First-pass screen unavailable: {reason}.', {reason: lt('the request failed')});
    box.append(p);
    return;
  }
  if (!current()) return;
  if (box.replaceChildren) box.replaceChildren();
  if (res && res.available === false) {
    // One muted line, the route's own reason. Nothing else is shown.
    const p = document.createElement('p');
    p.className = 'muted';
    p.dataset.testid = 'library-screen-message';
    // The route's own plain reason, or this file's honest fallback. The reason
    // already ends its own sentence, so the key adds no second full stop.
    p.textContent = lt('No first-pass screen: {reason}', {
      reason: (res.reason != null && String(res.reason).trim() !== '') ? String(res.reason) : lt('the local AI is unavailable on this machine.'),
    });
    box.append(p);
    return;
  }
  if (!res || typeof res !== 'object') {
    const p = document.createElement('p');
    p.className = 'muted';
    p.dataset.testid = 'library-screen-message';
    p.textContent = lt('The screen returned no entries.');
    box.append(p);
    return;
  }
  if (res.summary != null && String(res.summary).trim() !== '') {
    const p = document.createElement('p');
    p.textContent = String(res.summary);
    box.append(p);
  }
  const entries = ['items', 'screens', 'results', 'records'].map(k => res[k]).find(v => Array.isArray(v) && v.length);
  if (entries) {
    const ul = document.createElement('ul');
    ul.dataset.testid = 'library-screen-entries';
    for (const entry of entries) {
      const li = document.createElement('li');
      li.textContent = libraryScreenText(entry);
      ul.append(li);
    }
    box.append(ul);
  }
  const dropped = Number.isFinite(res.unverified_dropped) ? res.unverified_dropped : 0;
  if (dropped > 0) {
    // Honest count: these entries could not be checked and were left out of the screen.
    const p = document.createElement('p');
    p.className = 'muted';
    p.dataset.testid = 'library-screen-dropped';
    p.textContent = lt('{count} items could not be verified and were dropped from this screen.', {count: dropped});
    box.append(p);
  }
  if (!entries && (res.summary == null || String(res.summary).trim() === '') && !(dropped > 0)) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.dataset.testid = 'library-screen-message';
    p.textContent = lt('The screen returned no entries.');
    box.append(p);
  }
}

function renderLibrary() {
  renderLibraryFolders();
  renderLibraryItems();
}

// --- Boot: restore the saved panel layout and keep the layout controls usable
// even before a project is open.
readPanelState();
applyPanelState();
syncExpandButton();

// Everything this file writes is script output, so a language switch has to
// redraw it: the layout controls and the folder tree keep their own words.
if (WB_GLOBAL_LIB.WB_I18N && WB_GLOBAL_LIB.WB_I18N.onChange) {
  WB_GLOBAL_LIB.WB_I18N.onChange(function () {
    try { applyPanelState(); } catch (_) {}
    try { syncExpandButton(); } catch (_) {}
    try { renderLibrary(); } catch (_) {}
  });
}

// Drop zone wiring: highlight while a file is over it, accept the drop.
(function bindLibraryDropzone() {
  const zone = $('library-dropzone');
  if (!zone || !zone.addEventListener) return;
  zone.addEventListener('dragover', (e) => {
    if (!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'))) return;
    e.preventDefault();
    zone.classList.add('drop-target');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drop-target'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drop-target');
    librarySuppressClick('zone');
    const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
    if (files.length) libraryUploadFiles(files, libraryState.folder ?? null);
  });
})();

// A project switch drops the held tree so one project's documents can never be
// shown under another project's title. The next Library load refetches.
// `openProject` is asynchronous, so this wrapper usually sees no change at call
// time: `libraryResetIfProjectChanged()` on the next Library open is the
// authority. When the wrapper does observe a switch (a synchronous path), the
// same reset runs, so both routes leave identical state.
(function watchProject() {
  const wrap = (name) => {
    const base = window[name];
    if (typeof base !== 'function') return;
    window[name] = function (...args) {
      const before = S.project;
      const out = base.apply(this, args);
      if (S.project !== before) {
        libraryState.project = S.project;
        libraryResetProjectContext();
        setLibraryViewVisible(false);
        renderLibrary();
      }
      return out;
    };
  };
  wrap('openProject');
})();

// Research is still the default view: switching back clears the library surface,
// and opening the writing view leaves the library surface too, so the two
// surfaces can never be mounted at once.
(function wrapViews() {
  const research = window.showResearchView;
  window.showResearchView = function (...args) {
    if (typeof research === 'function') research.apply(this, args);
    setLibraryViewVisible(false);
  };
  const writing = window.openWritingView;
  window.openWritingView = function (...args) {
    setLibraryViewVisible(false);
    if (typeof writing === 'function') return writing.apply(this, args);
  };
})();

// Keep the layout controls reachable: app.js disables section/aside controls
// when no project is open, but collapsing a panel and expanding the surface are
// workspace layout choices, not project actions.
if (typeof controls === 'function') {
  const libraryBaseControls = controls;
  controls = function () {
    libraryBaseControls();
    for (const id of ['expand-surface', 'toggle-projects', 'toggle-secretary']) {
      const el = $(id);
      if (el) el.disabled = false;
    }
  };
}

// --- Korean strings for the library screen ---------------------------------
// Keyed by the authored English sentence. Count-bearing sentences use
// {placeholder} so Korean can put the number where it reads naturally; English
// pluralisation stays in the code because Korean has no plural suffix to add.
if (WB_GLOBAL_LIB.WB_I18N) WB_GLOBAL_LIB.WB_I18N.register('ko', {
  'Restore panels': '패널 되돌리기',
  'Expand surface': '작업 영역 넓히기',
  'Restore the panels to their previous state': '패널을 이전 상태로 되돌립니다',
  'Expand the working area, hiding both side panels': '양쪽 패널을 숨기고 작업 영역을 넓힙니다',
  'Collapse the projects panel': '프로젝트 패널 접기',
  'Expand the projects panel': '프로젝트 패널 펼치기',
  'Collapse the secretary panel': '비서 패널 접기',
  'Expand the secretary panel': '비서 패널 펼치기',
  'Open a project to use its library.': '라이브러리를 사용하려면 프로젝트를 여세요.',
  'Open a project before opening its library.': '라이브러리를 열기 전에 프로젝트를 여세요.',
  'Library unavailable: {reason}.': '라이브러리를 사용할 수 없습니다: {reason}.',
  'the tree could not be loaded': '목록을 불러오지 못했습니다',
  'New folder name': '새 폴더 이름',
  'Folder name': '폴더 이름',
  'Folder "{name}" created.': '"{name}" 폴더를 만들었습니다.',
  'Folder not created: {reason}.': '폴더를 만들지 못했습니다: {reason}.',
  'Select a folder to rename.': '이름을 바꿀 폴더를 선택하세요.',
  'Folder renamed to "{name}".': '폴더 이름을 바꿨습니다: "{name}".',
  'Folder not renamed: {reason}.': '폴더 이름을 바꾸지 못했습니다: {reason}.',
  'Select a folder in the tree to rename it.': '이름을 바꾸려면 트리에서 폴더를 선택하세요.',
  'Undated': '날짜 없음',
  'Untitled folder': '이름 없는 폴더',
  'All documents': '모든 문서',
  'Folder': '폴더',
  'Untitled note': '제목 없는 노트',
  'Untitled document': '제목 없는 문서',
  'Untitled': '제목 없음',
  ' · {count} page{plural}': ' · {count}쪽',
  ' · no text layer': ' · 텍스트 레이어 없음',
  'No documents or notes in this project yet.': '이 프로젝트에는 아직 문서나 노트가 없습니다.',
  'This folder is empty.': '이 폴더는 비어 있습니다.',
  'This item is no longer in the library.': '이 항목은 더 이상 라이브러리에 없습니다.',
  'This note was saved in an earlier session; the library listing does not include its text.':
    '이 노트는 이전 세션에서 저장되었고, 라이브러리 목록에는 본문이 포함되어 있지 않습니다.',
  'This PDF has no text layer. Search and the first-pass screen cannot read it; the pages are shown as scanned images.':
    '이 PDF에는 텍스트 레이어가 없습니다. 검색과 1차 스크리닝이 읽을 수 없어 페이지를 스캔 이미지로 보여줍니다.',
  'Open a project to save a note.': '노트를 저장하려면 프로젝트를 여세요.',
  'Write something before saving the note.': '노트를 저장하기 전에 내용을 적어 주세요.',
  'Saved to the Library.': '라이브러리에 저장했습니다.',
  'Note not saved: {reason}.': '노트를 저장하지 못했습니다: {reason}.',
  'the request failed': '요청이 실패했습니다',
  'Searching…': '검색하는 중…',
  '{count} matches for "{query}".': '"{query}"에 대해 {count}건 일치합니다.',
  'No stored text matches "{query}".': '저장된 텍스트에서 "{query}"와 일치하는 내용이 없습니다.',
  'Document': '문서',
  'Page {number} · ': '{number}쪽 · ',
  'Page {number}: ': '{number}쪽: ',
  'Search unavailable: {reason}.': '검색을 사용할 수 없습니다: {reason}.',
  'Saving note…': '노트를 저장하는 중…',
  'Note saved.': '노트를 저장했습니다.',
  'a file': '파일',
  'An upload is already running. Wait for it to finish before adding more files.':
    '업로드가 이미 진행 중입니다. 끝난 뒤에 파일을 더 추가하세요.',
  '{name}: only PDF files are accepted here': '{name}: 여기에는 PDF 파일만 올릴 수 있습니다',
  '{name}: the file is larger than 40 MiB': '{name}: 파일이 40 MiB보다 큽니다',
  '{name}: {reason}': '{name}: {reason}',
  '{count} file{plural} not added: {detail}.': '{count}개 파일을 추가하지 못했습니다: {detail}.',
  'the upload failed': '업로드가 실패했습니다',
  '{count} PDF file{plural} added to {folder}.': 'PDF 파일 {count}개를 {folder}에 추가했습니다.',
  '{count} PDF file{plural} added.': 'PDF 파일 {count}개를 추가했습니다.',
  'The list could not be refreshed: reload the library to see it.':
    '목록을 새로 고치지 못했습니다: 라이브러리를 다시 열어 확인하세요.',
  ' the list could not be refreshed.': ' 목록을 새로 고치지 못했습니다.',
  'the folder': '폴더',
  'Moved to {folder}.': '옮긴 위치: {folder}.',
  'no folder': '폴더 없음',
  'Not moved: {reason}.': '옮기지 못했습니다: {reason}.',
  'No first-pass screen: {reason}': '1차 스크리닝을 실행할 수 없습니다: {reason}',
  'Running the first-pass screen…': '1차 스크리닝을 실행하는 중…',
  'First-pass screen unavailable: {reason}.': '1차 스크리닝을 사용할 수 없습니다: {reason}.',
  'the local AI is unavailable on this machine.': '이 컴퓨터에서 로컬 AI를 사용할 수 없습니다.',
  'The screen returned no entries.': '스크리닝 결과가 비어 있습니다.',
  '{count} items could not be verified and were dropped from this screen.':
    '{count}개 항목은 검증되지 않아 이 스크리닝에서 제외했습니다.',
  'Drag to reorder, or focus the row and press Alt+ArrowUp / Alt+ArrowDown':
    '끌어서 순서를 바꾸거나, 행에 초점을 두고 Alt+위/아래 화살표를 누르세요.',
  'Saving the new order…': '새 순서를 저장하는 중…',
  'Document order saved.': '문서 순서를 저장했습니다.',
  'Order not saved: {reason}.': '순서를 저장하지 못했습니다: {reason}.',
  'This document is already first.': '이 문서는 이미 맨 앞에 있습니다.',
  'This document is already last.': '이 문서는 이미 맨 뒤에 있습니다.',
  'Loading this project\'s library…': '이 프로젝트의 라이브러리를 불러오는 중…',
  'The library could not be loaded: {reason}.': '라이브러리를 불러오지 못했습니다: {reason}.',
  'Open the library to load this project\'s documents.': '라이브러리를 열어 이 프로젝트의 문서를 불러오세요.'
});
