// LaTeX writing view, slices 1-2: editor + save/autosave + compile wiring.
// Slice 2 compiles through the app backend only (POST /drafts/{did}/compile) and shows the
// returned PDF via a blob URL; with no engine, no PDF is ever fabricated. Draft storage lives
// on the backend.
// Language lookup from i18n.js, with an English fallback so this file still runs
// in a bare JS context that has no window (the UI tests). Every script here
// shares one global scope, so the alias stays file-specific and is never `t`.
const WB_GLOBAL_LATEX = (typeof window !== 'undefined') ? window : {};
const latexT = (WB_GLOBAL_LATEX.WB_I18N && WB_GLOBAL_LATEX.WB_I18N.t) ? WB_GLOBAL_LATEX.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});

let latexState = null, latexRequest = 0, latexTimer = null, latexPdfUrl = null;
let latexFileDialogContext = null;
// The status line keeps its English key and values, not a rendered sentence, so a
// language change can redraw it without touching the draft.
let latexLastStatus = { key: 'Idle', vars: null, state: null, extraKey: null };
const LATEX_AUTOSAVE_MS = 800;

// Source helpers, not generated prose. Each insertion remains visible and editable.
const latexSnippets = {
  heading: ['\\section{', 'Heading', '}', false],
  subheading: ['\\subsection{', 'Subheading', '}', false],
  bold: ['\\textbf{', 'Text', '}', false],
  italic: ['\\textit{', 'Text', '}', false],
  bullets: ['\\begin{itemize}\n\\item ', 'First item', '\n\\end{itemize}', true],
  numbered: ['\\begin{enumerate}\n\\item ', 'First item', '\n\\end{enumerate}', true],
  inline: ['\\(', 'x = y', '\\)', false],
  display: ['\\[\n', 'x = y', '\n\\]', true],
  table: ['\\begin{tabular}{ll}\n', 'Column 1 & Column 2', ' \\\\\nValue 1 & Value 2 \\\\\n\\end{tabular}', true],
};

function latexCanInsert() {
  return !!(latexState && latexState.draft && /\.(tex|latex)$/.test(latexState.activeFile || latexState.draft.main_file));
}

// Walk top-level BibTeX entries so @comment bodies and quoted @article examples
// cannot become citation choices. Keys establish presence only, not source validity.
function latexBibliographyKeys() {
  const keys = new Set();
  for (const file of (latexState && latexState.draft && latexState.draft.files) || []) {
    if (!/\.bib$/.test(file.path)) continue;
    const source = file.text.replace(/(^|[^\\])%[^\n]*/g, '$1');
    const entry = /@([A-Za-z]+)\s*([{(])/g;
    let match;
    while ((match = entry.exec(source))) {
      const start = entry.lastIndex;
      const key = /^([A-Za-z0-9_:.+/-]+)\s*,/.exec(source.slice(start).trimStart());
      if (!/^(comment|string|preamble)$/i.test(match[1]) && key) keys.add(key[1]);
      let braces = match[2] === '{' ? 1 : 0, parentheses = match[2] === '(' ? 1 : 0, quoted = false;
      let i = start;
      for (; i < source.length; i++) {
        const ch = source[i];
        if (ch === '\\') { i++; continue; }
        if (ch === '"') quoted = !quoted;
        if (quoted) continue;
        if (ch === '{') braces++;
        if (ch === '}') braces--;
        if (match[2] === '(' && braces === 0) {
          if (ch === '(') parentheses++;
          if (ch === ')') parentheses--;
        }
        if (braces === 0 && parentheses === 0) { i++; break; }
      }
      entry.lastIndex = i;
    }
  }
  return [...keys].sort();
}

function syncLatexSyntax() {
  const enabled = latexCanInsert();
  for (const button of document.querySelectorAll('#latex-view [data-latex-snippet]')) button.disabled = !enabled;
  const select = $('latex-citation-key');
  const previous = select && select.value;
  const keys = latexBibliographyKeys();
  if (select) {
    select.innerHTML = '';
    for (const key of keys) {
      const option = document.createElement('option');
      option.value = key; option.textContent = key;
      if (select.appendChild) select.appendChild(option);
    }
    if (keys.includes(previous)) select.value = previous;
    select.disabled = !enabled || !keys.length;
  }
  const cite = $('latex-insert-citation');
  if (cite) cite.disabled = !enabled || !keys.length;
  const note = $('latex-syntax-note');
  if (note) note.textContent = latexT(!enabled
    ? 'Open a .tex or .latex file to insert syntax. Bibliography files stay unchanged.'
    : keys.length ? 'Citation choices are keys in this draft’s .bib files, not verified publications.'
      : 'Missing references: add real entries to a .bib file before inserting a citation.');
}

function latexInsertSyntax(kind) {
  if (!latexCanInsert()) return;
  const editor = $('latex-editor');
  const before = editor.value;
  const start = editor.selectionStart || 0, end = editor.selectionEnd || start;
  const selected = before.slice(start, end);
  let spec = latexSnippets[kind];
  if (kind === 'citation') {
    const key = $('latex-citation-key').value;
    if (!latexBibliographyKeys().includes(key)) { syncLatexSyntax(); return; }
    spec = [selected ? selected + ' \\cite{' : '\\cite{', key, '}', false];
  }
  if (!spec) return;
  let [prefix, placeholder, suffix, block] = spec;
  // Interface language must never translate manuscript text or inject CJK into
  // an otherwise pdfLaTeX-compatible source. Keep authored selections verbatim.
  let content = kind === 'citation' ? placeholder : selected || placeholder;
  if ((kind === 'bullets' || kind === 'numbered') && selected) content = selected.split('\n').join('\n\\item ');
  if (block && start && before[start - 1] !== '\n') prefix = '\n' + prefix;
  if (block && end < before.length && before[end] !== '\n') suffix += '\n';
  const inserted = prefix + content + suffix;
  const after = before.slice(0, start) + inserted + before.slice(end);
  if (encodeURIComponent(after).replace(/%[A-F0-9]{2}/gi, 'x').length > 512 * 1024) {
    showError(latexT('Insertion would exceed the 512 KiB file limit.')); return;
  }
  const history = latexState.syntaxUndo || (latexState.syntaxUndo = []);
  history.push({draft: latexState.draft.draft_id, path: latexState.activeFile, before, after, start, end});
  if (history.length > 12) history.shift();
  editor.setRangeText(inserted, start, end, 'end');
  editor.focus();
  editor.setSelectionRange(start + prefix.length, start + prefix.length + content.length);
  scheduleLatexAutosave();
}

function latexUndoSyntax() {
  const st = latexState, editor = $('latex-editor');
  if (!st || !st.draft) return;
  const history = st.syntaxUndo || [];
  const index = history.findLastIndex(item => item.draft === st.draft.draft_id && item.path === st.activeFile);
  if (index < 0) return;
  const item = history[index];
  if (editor.value !== item.after) {
    showError(latexT('Undo later typing first, or edit the source. No text was removed.')); return;
  }
  editor.value = item.before;
  history.splice(index, 1);
  editor.focus(); editor.setSelectionRange(item.start, item.end);
  scheduleLatexAutosave();
}

if (WB_GLOBAL_LATEX.WB_I18N) WB_GLOBAL_LATEX.WB_I18N.register('ko', {
  'Heading': '제목', 'Subheading': '소제목', 'Bold': '굵게', 'Italic': '기울임',
  'Text': '텍스트', 'First item': '첫 항목',
  'Bulleted list': '글머리 기호 목록', 'Numbered list': '번호 목록',
  'Inline equation': '문장 안 수식', 'Display equation': '독립 수식', 'Small table': '작은 표',
  'More syntax': '문법 더 보기', 'Existing bibliography key': '기존 참고문헌 키',
  'Insert citation key': '인용 키 삽입', 'Undo syntax insertion': '문법 삽입 취소',
  'How to use these buttons': '버튼 사용 방법',
  'Open a .tex or .latex file to insert syntax. Bibliography files stay unchanged.':
    '문법을 삽입하려면 .tex 또는 .latex 파일을 여세요. 참고문헌 파일에는 삽입하지 않습니다.',
  'Citation choices are keys in this draft’s .bib files, not verified publications.':
    '인용 선택지는 이 초안의 .bib 파일에 있는 키입니다. 출판물의 진위를 검증한 것은 아닙니다.',
  'Missing references: add real entries to a .bib file before inserting a citation.':
    '참고문헌이 없습니다. 인용하기 전에 .bib 파일에 실제 참고문헌 항목을 추가하세요.',
  'Insertion would exceed the 512 KiB file limit.': '삽입하면 파일 크기 제한인 512 KiB를 초과합니다.',
  'Undo later typing first, or edit the source. No text was removed.':
    '먼저 나중에 입력한 내용을 취소하거나 소스를 편집하세요. 텍스트를 삭제하지 않았습니다.',
  'Select words to wrap them, or place the caret and choose a button. Replace the selected placeholder. Insert inside the document body, between \\begin{document} and \\end{document}. Nothing compiles automatically.':
    '단어를 선택하거나 커서를 놓은 뒤 버튼을 누르세요. 선택된 예시를 바꾸세요. \\begin{document}와 \\end{document} 사이의 본문에 삽입하세요. 자동으로 컴파일하지 않습니다.',
  'Example: Bold creates \\textbf{Text}. Lists use \\item for each item. Tables separate columns with & and end rows with \\\\. Equations expect math syntax, not ordinary prose.':
    '예: 굵게는 \\textbf{Text}를 만듭니다. 목록의 각 항목에는 \\item을 씁니다. 표는 &로 열을 나누고 \\\\로 행을 끝냅니다. 수식에는 일반 문장 대신 수학 문법을 쓰세요.',
  'Undo syntax insertion reverses up to 12 insertions in this session. Undo later typing first; it will never discard later edits.':
    '이 세션에서 최대 12회의 문법 삽입을 취소할 수 있습니다. 이후 입력을 먼저 취소하세요. 이후 편집 내용을 버리지 않습니다.'
});

function renderLatexFiles() {
  const st = latexState;
  if (!st || !st.draft) return;
  const list = $('latex-files');
  if (list) {
    list.innerHTML = '';
    for (const file of st.draft.files) {
      const button = document.createElement('button');
      button.textContent = file.path + (file.path === st.draft.main_file ? ' · main' : '');
      button.type = 'button';
      if (button.setAttribute) button.setAttribute('aria-pressed', String(file.path === st.activeFile));
      button.onclick = () => latexSelectFile(file.path);
      if (list.appendChild) list.appendChild(button);
    }
  }
  const name = $('latex-active-file');
  if (name) name.textContent = st.activeFile || st.draft.main_file;
  const select = $('latex-draft-select');
  if (select) {
    select.innerHTML = '';
    for (const draft of st.drafts || [st.draft]) {
      const option = document.createElement('option');
      option.value = draft.draft_id;
      option.textContent = draft.title;
      if (select.appendChild) select.appendChild(option);
    }
    select.value = st.draft.draft_id;
  }
  syncLatexSyntax();
}

function latexSelectFile(path) {
  const st = latexState;
  if (!st || !st.draft) return;
  // A reload in flight replaces the whole draft; switching files now would move the
  // caret's text into a file list the GET is about to discard.
  if (st.reloading) { latexRefuseWhileReloading(); return; }
  if (!st.draft.files.some(f => f.path === path)) return;
  st.draft.files = latexFiles($('latex-editor').value);
  st.activeFile = path;
  $('latex-editor').value = st.draft.files.find(f => f.path === path).text;
  renderLatexFiles();
}

// A refusal is always stated: a click that silently does nothing reads as a broken
// button. This one sentence covers every file-list action a reload interrupts.
function latexRefuseWhileReloading() {
  latexSetStatus('Not changed: a reload is in progress. Wait for it to finish.', 'fail');
}

function latexFileAction(action) {
  const st = latexState;
  // A reload in flight will replace the file list, so an edit made now would be
  // applied to a list that is about to disappear. Refuse instead of racing it.
  if (!st || !st.draft || st.blocked) return;
  if (st.reloading) { latexRefuseWhileReloading(); return; }
  st.draft.files = latexFiles($('latex-editor').value);
  const current = st.activeFile;
  if (action !== 'add' && current === st.draft.main_file) {
    showError(latexT('The main file cannot be renamed or deleted.')); return;
  }
  if (action === 'delete') {
    // Name the exact target: a bare "this file" cannot be checked against the list.
    if (!confirm(latexT('Delete {file}, including its unsaved changes?', {file: current}))) return;
    st.draft.files = st.draft.files.filter(f => f.path !== current);
    st.activeFile = st.draft.main_file;
  } else {
    if (action === 'add' && st.draft.files.length >= 10) { showError(latexT('Maximum 10 files.')); return; }
    if (action !== 'add' && action !== 'rename') return;
    latexFileDialogContext = {state: st, draft: st.draft.draft_id, action, path: current, origin: document.activeElement};
    $('latex-file-dialog-title').textContent = latexT(action === 'add' ? 'Add file' : 'Rename file');
    $('latex-file-path').value = action === 'rename' ? current : '';
    $('latex-file-error').textContent = '';
    $('latex-file-dialog').showModal();
    $('latex-file-path').focus();
    $('latex-file-path').select();
    return;
  }
  $('latex-editor').value = st.draft.files.find(f => f.path === st.activeFile).text;
  renderLatexFiles();
  scheduleLatexAutosave();
}

function latexCloseFileDialog() {
  const context = latexFileDialogContext;
  latexFileDialogContext = null;
  const dialog = $('latex-file-dialog');
  if (dialog.open) dialog.close();
  if (context) {
    const target = context.origin && context.origin.isConnected ? context.origin : $('latex-editor');
    if (target) target.focus();
  }
}

function latexSubmitFileDialog(event) {
  event.preventDefault();
  const context = latexFileDialogContext;
  const st = latexState;
  if (!context || st !== context.state || !st.draft || st.draft.draft_id !== context.draft || st.blocked || st.reloading) {
    if (st && st.reloading) latexRefuseWhileReloading();
    latexCloseFileDialog(); return;
  }
  const path = $('latex-file-path').value;
  let error = '';
  if (!latexValidPath(path) || st.draft.files.some(f => f.path === path))
    error = latexT('Use a unique safe relative file path ending in .tex, .bib or .latex.');
  else if (context.action === 'add' && st.draft.files.length >= 10)
    error = latexT('Maximum 10 files.');
  else if (context.action === 'rename' &&
           (context.path === st.draft.main_file || !st.draft.files.some(f => f.path === context.path)))
    error = latexT('The main file cannot be renamed or deleted.');
  if (error) {
    $('latex-file-error').textContent = error;
    $('latex-file-path').focus(); return;
  }
  st.draft.files = latexFiles($('latex-editor').value);
  if (context.action === 'add') st.draft.files.push({path, text: ''});
  else st.draft.files.find(f => f.path === context.path).path = path;
  st.activeFile = path;
  $('latex-editor').value = st.draft.files.find(f => f.path === path).text;
  renderLatexFiles();
  scheduleLatexAutosave();
  latexCloseFileDialog();
}

if (WB_GLOBAL_LATEX.WB_I18N) WB_GLOBAL_LATEX.WB_I18N.register('ko', {
  'Add file': '파일 추가', 'Rename file': '파일 이름 바꾸기',
  'Paper': '논문', 'New paper': '새 논문', 'Files': '파일', 'Source': '소스',
  'Download active source': '현재 소스 내려받기', 'Download PDF': 'PDF 내려받기',
  'Secretary': '비서', 'Rename': '이름 바꾸기', 'Delete': '삭제', 'Import source': '소스 가져오기',
  'Up to 10 source files. Main file is protected.': '소스 파일은 최대 10개입니다. 주 파일은 보호됩니다.',
  'File path': '파일 경로', 'Cancel': '취소', 'Confirm file path': '파일 경로 확인',
  'Use a unique safe relative file path ending in .tex, .bib or .latex.':
    '중복되지 않는 안전한 상대 경로를 입력하세요. 확장자는 .tex, .bib 또는 .latex여야 합니다.',
  'Paths use letters, numbers, hyphens, underscores and optional folders. Example: sections/intro.tex':
    '영문자, 숫자, 하이픈, 밑줄과 폴더를 사용할 수 있습니다. 예: sections/intro.tex'
});

function latexValidPath(path) {
  return /^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.(tex|bib|latex)$/.test(path);
}

async function latexImportSource(input) {
  const st = latexState;
  const file = input.files && input.files[0];
  input.value = '';
  if (!st || !st.draft || st.blocked) return;
  if (st.reloading) { latexRefuseWhileReloading(); return; }
  if (!file) return;
  if (!latexValidPath(file.name) || file.size > 512 * 1024) {
    showError(latexT('Import a safe .tex, .bib or .latex file up to 512 KiB.')); return;
  }
  const exists = st.draft.files.some(f => f.path === file.name);
  if (exists && !confirm(latexT('Replace {file}, including any unsaved changes?', {file: file.name}))) return;
  if (!exists && st.draft.files.length >= 10) { showError(latexT('Maximum 10 files.')); return; }
  const text = await file.text();
  // The file was read while the reload was landing: the draft it would join is gone.
  if (latexState !== st) return;
  if (st.reloading) { latexRefuseWhileReloading(); return; }
  st.draft.files = latexFiles($('latex-editor').value);
  st.draft.files = st.draft.files.filter(f => f.path !== file.name);
  st.draft.files.push({path: file.name, text});
  st.activeFile = file.name;
  $('latex-editor').value = text;
  renderLatexFiles(); scheduleLatexAutosave();
}

// The paper selector is a plain <select>: once a change event fires its value already
// shows the paper the researcher picked. Every path that does not actually open that
// paper must put the shown value back to the loaded draft, or the toolbar would claim
// a paper the editor is not showing.
function restoreLatexDraftSelection() {
  const select = $('latex-draft-select');
  const st = latexState;
  if (!select || !st || !st.draft) return;
  const wanted = String(st.draft.draft_id);
  if (select.value !== wanted) select.value = wanted;
}

async function latexChooseDraft(id, create = false) {
  const st = latexState;
  if (!st || st.compiling || st.reloading || st.switchingDraft) { restoreLatexDraftSelection(); return; }
  if ((st.dirty || st.saving) && !await saveDraft('manual')) { restoreLatexDraftSelection(); return; }
  if (st.dirty || latexState !== st) { restoreLatexDraftSelection(); return; }
  const request = ++latexRequest;
  const previous = st.draft;
  st.switchingDraft = true;
  syncLatexControls();
  try {
    const draft = create
      ? await call('POST', `/projects/${encodeURIComponent(st.project)}/drafts`, {title: 'Untitled paper', question_id: null})
      : await call('GET', `/drafts/${encodeURIComponent(id)}`);
    if (latexState !== st || request !== latexRequest) return;
    if (!draft || !draft.draft_id || !Array.isArray(draft.files)) throw new Error(latexT('The backend did not return a paper.'));
    st.draft = draft;
    if (create) st.drafts.push(draft);
    st.activeFile = draft.main_file;
    st.compiledRevision = null;
    renderPreview(null); renderLatexDiagnostics([]); renderLatexLog('');
    renderDraft();
    if (create) {
      const editor = $('latex-editor');
      const bodyEnd = editor.value.lastIndexOf('\\end{document}');
      if (bodyEnd >= 0) editor.setSelectionRange(bodyEnd, bodyEnd);
      editor.focus();
    }
    latexSetStatus('Paper opened · revision {revision}', 'ok', {revision: draft.revision});
  } catch (e) {
    // The previously loaded paper is still what the editor holds, so the selector,
    // the label and the text all go back to it together.
    if (latexState !== st || request !== latexRequest) return;
    st.draft = previous;
    renderLatexFiles();
    const label = $('latex-draft-label');
    if (label) label.textContent = latexDraftLabel();
    latexSetStatus('Not opened: {reason}', 'fail', {reason: (e && e.message) || latexT('the paper could not be opened.')});
  } finally {
    if (latexState === st) {
      st.switchingDraft = false;
      restoreLatexDraftSelection();
      syncLatexControls();
    }
  }
}

async function latexDownloadSource() {
  const st = latexState;
  if (!st || ((st.dirty || st.saving) && !await saveDraft('manual')) || latexState !== st || st.dirty) return;
  const file = st.draft.files.find(f => f.path === st.activeFile);
  const url = URL.createObjectURL(new Blob([file.text], {type: 'text/plain;charset=utf-8'}));
  const a = document.createElement('a');
  a.href = url; a.download = file.path.split('/').pop(); a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function latexDownloadPdf() {
  if (!latexState || latexState.dirty || latexState.compiledRevision !== latexState.draft.revision || !latexPdfUrl) {
    showError(latexT('Compile the current saved revision before downloading a PDF.')); return;
  }
  const a = document.createElement('a');
  a.href = latexPdfUrl; a.download = 'paper.pdf'; a.click();
}

function renderLatexStatus() {
  const el = $('latex-status');
  if (!el) return;
  const { key, vars, state, extraKey } = latexLastStatus;
  let text = key == null ? '' : latexT(key, vars || undefined);
  if (extraKey) text += latexT(extraKey);
  el.textContent = text;
  if (state) el.setAttribute('data-state', state); else el.removeAttribute('data-state');
}

// `key` is the authored English sentence (a translation key); passing the English
// sentence instead of a rendered string is what keeps the line translatable later.
function latexSetStatus(key, state, vars, extraKey) {
  latexLastStatus = { key, vars: vars || null, state: state || null, extraKey: extraKey || null };
  renderLatexStatus();
}

// The absence banner keeps its English key plus values, so a language change can
// redraw the observation without re-reading the machine or touching the draft.
let latexAbsenceNote = null;

function renderLatexAbsence() {
  const box = $('latex-absence');
  if (!box) return;
  if (latexAbsenceNote) {
    box.textContent = latexT(latexAbsenceNote.key, latexAbsenceNote.vars || undefined);
    box.hidden = false;
  } else { box.textContent = ''; box.hidden = true; }
}

function latexAbsence(key, vars) {
  latexAbsenceNote = key ? {key, vars: vars || null} : null;
  renderLatexAbsence();
}

// The two observed states of this machine, worded once so the first load and the
// re-check can never drift apart in either language.
const LATEX_ENGINE_NOTE = 'A LaTeX engine is on PATH (found: {engines}). Compile sends the saved draft to the local backend and shows the returned PDF.';
const LATEX_NO_ENGINE_NOTE = 'PDF preview needs a LaTeX engine. This machine has none of pdflatex, xelatex, lualatex, latexmk, tectonic on its PATH. You can still write and autosave. Compile stays disabled; the app will not install an engine for you.';

function latexShowEngineState(caps) {
  const engines = Object.entries((caps && caps.engines) || {}).filter(([, v]) => v).map(([k]) => k);
  if (caps && caps.available) latexAbsence(LATEX_ENGINE_NOTE, {engines: engines.join(', ') || 'no engine name reported'});
  else latexAbsence(LATEX_NO_ENGINE_NOTE);
}

// Compile is enabled only while a draft is open and the backend reports an engine.
// The disabled reason stays visible on the compile note the button describes.
function latexCompileReason() {
  const st = latexState;
  if (!st || !st.draft) return latexT('Open a project to start a draft before compiling.');
  if (st.compiling) return latexT('Compiling the saved draft…');
  const caps = st.capabilities;
  if (!caps || !caps.available) return latexT('Compile stays disabled: no LaTeX engine was found on this machine.');
  return '';
}

function syncLatexControls() {
  const btn = $('latex-compile');
  if (!btn) return;
  const reason = latexCompileReason();
  const busy = !!(latexState && (latexState.reloading || latexState.switchingDraft));
  for (const id of ['latex-save', 'latex-reload']) {
    const control = $(id);
    if (control) control.disabled = !!(latexState && latexState.compiling) || busy;
  }
  const select = $('latex-draft-select');
  if (select) select.disabled = busy;
  const note = $('latex-compile-note');
  if (note) note.textContent = reason || latexT('Compile sends the saved draft to the local backend (POST /drafts/{id}/compile) and shows only the PDF returned for the current revision. It stays disabled while no LaTeX engine is found on this machine.');
  if (latexState && latexState.compiling) {
    // While a compile is in flight the whole compile row locks; app.js still owns
    // every other control, so this never enables anything.
    for (const id of ['latex-save', 'latex-reload', 'latex-compile']) {
      const el = $(id);
      if (el) el.disabled = true;
    }
    btn.setAttribute('aria-disabled', 'true');
    return;
  }
  btn.disabled = !!reason || busy;
  if (reason || busy) btn.setAttribute('aria-disabled', 'true'); else btn.removeAttribute('aria-disabled');
}

function latexDraftLabel() {
  const d = latexState && latexState.draft;
  if (!d) return latexT('No draft yet.');
  const dirty = latexState.dirty ? latexT(' · unsaved changes') : '';
  // The pane is emptied the moment the source moves on (latexDropStalePreview), so
  // there is no old PDF to disclaim here; the label only ever describes the live draft.
  return latexT('{title} · main file {file} · revision {revision}{dirty}',
    {title: d.title, file: d.main_file, revision: d.revision, dirty});
}

// The notice is the backend's retention disclosure plus, for imported non-ASCII
// text, the engine choice. Kept as one function so a language redraw reuses it
// instead of rebuilding the draft.
function latexDraftNotice(d) {
  if (!d) return '';
  const cjk = d.engine === 'xelatex'
    ? latexT(' Engine xelatex (chosen because the imported text is not plain ASCII). Korean text also needs the kotex package and fonts; not verified on this machine.') : '';
  return ((d && d.notice) || '') + cjk;
}

function renderDraft() {
  const d = latexState && latexState.draft;
  const label = $('latex-draft-label');
  if (label) label.textContent = latexDraftLabel();
  const notice = $('latex-notice');
  if (notice) notice.textContent = latexDraftNotice(d);
  if (!d) {
    const files = $('latex-files');
    if (files) files.textContent = latexT('No paper yet. Select New paper to begin.');
    const editor = $('latex-editor');
    if (editor) editor.value = '';
    syncLatexControls();
    return;
  }
  const main = (d.files || []).find(f => f.path === latexState.activeFile) || (d.files || []).find(f => f.path === d.main_file) || (d.files || [])[0];
  latexState.activeFile = main && main.path;
  const editor = $('latex-editor');
  if (editor) editor.value = main ? main.text : '';
  latexState.dirty = false;
  renderLatexFiles();
  bindLatexEditor();
  syncLatexControls();
}

// PDF rendering: only an explicit PDF response is shown; with none the pane states the truth.
// Blob URLs are revoked on replace and on unmount so the tab cannot leak the document.
function renderPreview(pdf) {
  const pane = $('latex-preview');
  if (latexPdfUrl) { URL.revokeObjectURL(latexPdfUrl); latexPdfUrl = null; }
  for (const el of document.querySelectorAll('.latex-preview iframe, .latex-preview object')) el.remove();
  if (!pdf) {
    if (pane) pane.textContent = latexT('No PDF yet.');
    return null;
  }
  latexPdfUrl = URL.createObjectURL(pdf);
  const frame = document.createElement('iframe');
  frame.src = latexPdfUrl;
  frame.title = latexT('Compiled PDF');
  if (pane && typeof pane.replaceChildren === 'function') pane.replaceChildren(frame);
  return latexPdfUrl;
}

// One rule for the whole view: a shown PDF belongs to exactly one revision. As soon
// as the draft's revision moves past it, the document is removed (blob URL revoked)
// instead of being kept and disclaimed with a label.
function latexDropStalePreview() {
  const st = latexState;
  if (!st || st.compiledRevision == null) return;
  if (st.draft && st.draft.revision === st.compiledRevision) return;
  renderPreview(null);
  st.compiledRevision = null;
}

function latexUnmount() {
  if (latexTimer) { clearTimeout(latexTimer); latexTimer = null; }
  if (latexFileDialogContext) latexCloseFileDialog();
  renderPreview(null);
  latexState = null;
  latexRequest++;
}

function setLatexViewVisible(visible) {
  const section = document.querySelector('main section');
  if (section && section.classList) {
    if (visible) section.classList.add('writing-mode'); else section.classList.remove('writing-mode');
  }
  const view = $('latex-view');
  if (view && view.classList) {
    if (visible) view.classList.remove('hidden'); else view.classList.add('hidden');
  }
  const writing = $('view-writing'), research = $('view-research');
  if (writing) writing.setAttribute('aria-pressed', String(!!visible));
  if (research) research.setAttribute('aria-pressed', String(!visible));
}

function showResearchView() {
  setLatexViewVisible(false);
}

async function openWritingView() {
  if (!S.project) { showError(latexT('Open a project before writing.')); return; }
  if (latexState && latexState.project === S.project) { setLatexViewVisible(true); return; }
  if (latexState && latexState.project !== S.project) {
    // A switch that did not go through openProject() (a pack change, a restored
    // session) still must not drop unsaved text: flush, and refuse to show the new
    // project while the old draft cannot be saved.
    if (!await latexBeforeProjectSwitch()) {
      showError(latexT('Not opened: the previous paper has unsaved changes that could not be saved.'));
      return;
    }
  }
  setLatexViewVisible(true);
  const request = ++latexRequest;
  const project = S.project;
  try {
    const caps = await call('GET', '/capabilities/latex');
    if (request !== latexRequest || S.project !== project) return;
    const list = await call('GET', `/projects/${encodeURIComponent(project)}/drafts`);
    if (request !== latexRequest || S.project !== project) return;
    let draft = (list && list.drafts && list.drafts[0]) || null;
    if (draft) draft = await call('GET', `/drafts/${encodeURIComponent(draft.draft_id)}`);
    if (request !== latexRequest || S.project !== project) return;
    latexState = {project, draft, capabilities: caps, dirty: false, saving: false, blocked: false, serial: 0, compiling: false, compiledRevision: null};
    latexState.drafts = (list && list.drafts) || [];
    renderDraft();
    renderPreview(null);
    // The absence banner is the observed state of this machine, not an error toast.
    latexShowEngineState(caps);
    latexSetStatus(draft ? 'Idle · revision {revision}' : 'No paper yet. Select New paper to begin.', null,
      draft ? {revision: draft.revision} : null);
    syncLatexControls();
  } catch (e) {
    if (request === latexRequest) { showError((e && e.message) || latexT('Writing view unavailable.')); latexSetStatus('Draft unavailable', 'fail'); }
  }
}

function latexFiles(text) {
  const d = latexState && latexState.draft;
  if (!d) return [];
  const active = latexState.activeFile || d.main_file;
  return (d.files || []).map(f => ({path: f.path, text: f.path === active ? text : f.text}));
}

async function saveDraft(source) {
  const state = latexState;
  if (!state || !state.draft) return null;
  if (state.reloading) {
    // A reload in flight will replace the whole draft. Saving now would PUT the
    // buffer that reload is about to discard, so the local text is kept instead.
    latexSetStatus('Not saved: a reload is in progress. Your text is still in the editor.', 'fail');
    return null;
  }
  if (state.saving) { await state.pendingSave; return state.dirty ? saveDraft(source) : state.draft; }
  if (state.blocked || state.compiling) return null;
  const editor = $('latex-editor');
  const text = editor ? editor.value : '';
  const draft = state.draft;
  const serial = ++state.serial;
  const version = state.editVersion || 0;
  const files = latexFiles(text);
  if (files.some(f => encodeURIComponent(f.text).replace(/%[A-F0-9]{2}/gi, 'x').length > 512 * 1024)) {
    latexSetStatus('Not saved: {reason}', 'fail', {reason: latexT('each file must be at most 512 KiB.')}); return null;
  }
  state.draft.files = files;
  state.saving = true;
  // An explicit save supersedes any queued autosave: one revision, not two.
  if (latexTimer) { clearTimeout(latexTimer); latexTimer = null; }
  latexSetStatus('Saving…', 'saving');
  let finishSave;
  let saveSucceeded = false;
  state.pendingSave = new Promise(resolve => { finishSave = resolve; });
  try {
    const saved = await call('PUT', `/drafts/${encodeURIComponent(draft.draft_id)}`,
      {expected_revision: draft.revision, files, title: draft.title, engine: draft.engine, source});
    if (serial !== state.serial || latexState !== state) return null;
    const changed = version !== (state.editVersion || 0);
    state.draft = changed ? {...saved, files: state.draft.files} : saved;
    state.dirty = changed;
    state.blocked = false;
    saveSucceeded = true;
    // A PDF built from an older revision is removed rather than disclaimed: the pane
    // never shows a document the current draft did not produce.
    latexDropStalePreview();
    const stamp = new Date().toLocaleTimeString();
    latexSetStatus('Idle · saved {time} · revision {revision}', 'ok', {time: stamp, revision: saved.revision});
    if (changed) latexSetStatus('Unsaved changes · saving again shortly', 'dirty');
    const label = $('latex-draft-label');
    if (label) label.textContent = latexDraftLabel();
    return saved;
  } catch (e) {
    if (serial !== state.serial || latexState !== state) return null;
    const message = (e && e.message) || latexT('Save failed.');
    if (/reload|conflict|409|revision/i.test(message)) {
      // Revision conflict: the editor keeps its text, and saving stops until a reload so the
      // autosave timer cannot overwrite another tab's newer draft.
      state.dirty = true;
      state.blocked = true;
      latexSetStatus('Draft changed; reload before saving. Your text is still in the editor.', 'fail');
    } else {
      latexSetStatus('Not saved: {reason}', 'fail', {reason: message});
    }
    return null;
  } finally {
    state.saving = false;
    finishSave();
    if (saveSucceeded && latexState === state && state.dirty && !state.blocked) {
      if (latexTimer) clearTimeout(latexTimer);
      latexTimer = setTimeout(() => { latexTimer = null; saveDraft('autosave'); }, LATEX_AUTOSAVE_MS);
    }
  }
}

function scheduleLatexAutosave() {
  if (!latexState || !latexState.draft) return;
  latexState.dirty = true;
  latexState.editVersion = (latexState.editVersion || 0) + 1;
  latexState.draft.files = latexFiles($('latex-editor').value);
  renderPreview(null);
  latexState.compiledRevision = null;
  latexSetStatus('Unsaved changes', 'dirty');
  const label = $('latex-draft-label');
  if (label) label.textContent = latexDraftLabel();
  if (latexState.blocked) return;  // a revision conflict must be reloaded first
  if (latexTimer) clearTimeout(latexTimer);
  latexTimer = setTimeout(() => { latexTimer = null; saveDraft('autosave'); }, LATEX_AUTOSAVE_MS);
}

async function reloadDraft() {
  if (!latexState || !latexState.draft) return null;
  // Reloading while a PUT is in flight would restore the text that save is about to
  // replace, so it refuses instead of racing the write.
  if (latexState.saving) {
    latexSetStatus('Reload refused while a save is in progress. Wait for it to finish.', 'fail');
    return null;
  }
  if (latexState.dirty && !confirm(latexT('Discard all unsaved file changes and reload?'))) return null;
  if (latexTimer) { clearTimeout(latexTimer); latexTimer = null; }
  const state = latexState;
  const request = ++latexRequest;
  // From here until the GET settles the buffer is about to be discarded: every save,
  // compile and file mutation is refused rather than persisting pre-reload text.
  const versionAtStart = state.editVersion || 0;
  state.reloading = true;
  syncLatexControls();
  try {
    const draft = await call('GET', `/drafts/${encodeURIComponent(state.draft.draft_id)}`);
    if (request !== latexRequest || latexState !== state) return null;
    if ((state.editVersion || 0) !== versionAtStart) {
      // The researcher kept typing while the GET was in flight. Adopting the fetched
      // draft would throw that text away, so the local buffer stays exactly as it is.
      latexSetStatus('Not reloaded: the editor changed while reloading. Your text is still in the editor; reload again to discard it.', 'fail');
      return null;
    }
    state.draft = draft;
    state.blocked = false;
    renderPreview(null);
    renderLatexDiagnostics([]);
    renderLatexLog('');
    renderDraft();
    latexSetStatus('Idle · revision {revision}', null, {revision: draft.revision});
    return draft;
  } catch (e) {
    // The local buffer is untouched: nothing is replaced, and nothing turns clean.
    latexSetStatus('Reload failed: {reason}', 'fail', {reason: (e && e.message) || latexT('unknown error')});
    return null;
  } finally {
    if (latexState === state) {
      state.reloading = false;
      syncLatexControls();
      restoreLatexDraftSelection();
      // An autosave that fired while the reload was in flight was refused, so unsaved
      // text kept during a failed or aborted reload gets its queued save again.
      if (state.dirty && !state.blocked && !latexTimer)
        latexTimer = setTimeout(() => { latexTimer = null; saveDraft('autosave'); }, LATEX_AUTOSAVE_MS);
    }
  }
}

// Re-poll the cheap capability probe (shutil.which ×5). It never installs an engine and
// never enables Compile, because this build ships no compile route.
async function recheckLatex() {
  const request = latexRequest;
  const project = S.project;
  try {
    const caps = await call('GET', '/capabilities/latex');
    if (request !== latexRequest || S.project !== project || !latexState) return null;
    latexState.capabilities = caps;
    latexShowEngineState(caps);
    syncLatexControls();
    return caps;
  } catch (e) {
    latexSetStatus('Engine check failed: {reason}', 'fail', {reason: (e && e.message) || latexT('unknown error')});
    return null;
  }
}

// Diagnostics: server entries rendered with severity, line and message. A diagnostic
// whose code is UNKNOWN_CITATION_KEY is a draft problem, not an engine failure, so it
// is labelled plainly and never reads as a successful build.
function latexDiagLine(d) {
  const l = d.line ?? d.lineno ?? null;
  return (typeof l === 'number' && l >= 1) ? l : null;
}

function latexJumpToLine(line) {
  const editor = $('latex-editor');
  if (!editor || typeof editor.value !== 'string' || !(line >= 1)) return;
  const lines = editor.value.split('\n');
  const n = Math.min(line, lines.length);
  let off = 0;
  for (let i = 0; i < n - 1; i++) off += lines[i].length + 1;
  try {
    if (typeof editor.focus === 'function') editor.focus();
    if ('selectionStart' in editor) { editor.selectionStart = off; editor.selectionEnd = off; }
  } catch (_) {}
}

function renderLatexDiagnostics(list) {
  const ul = $('latex-diagnostics');
  if (!ul) return;
  if (typeof ul.replaceChildren === 'function') ul.replaceChildren();
  ul.hidden = true;
  for (const d of list || []) {
    const sev = d.severity || d.level || 'info';
    const line = latexDiagLine(d);
    const msg = d.message || d.text || '';
    const code = d.code || d.kind || '';
    const li = document.createElement('li');
    if (code === 'UNKNOWN_CITATION_KEY') {
      li.textContent = (line != null)
        ? latexT('Unknown citation key · line {line}: {message} — fix the key or add it to the bibliography.', {line, message: msg})
        : latexT('Unknown citation key: {message} — fix the key or add it to the bibliography.', {message: msg});
      li.setAttribute('data-code', 'UNKNOWN_CITATION_KEY');
    } else {
      li.textContent = (line != null)
        ? latexT('{severity} · line {line}: {message}', {severity: sev, line, message: msg})
        : latexT('{severity}: {message}', {severity: sev, message: msg});
    }
    li.setAttribute('data-severity', String(sev));
    if (line != null) {
      li.setAttribute('data-line', String(line));
      li.tabIndex = 0;
      const jump = () => { if (d.file || d.path) latexSelectFile(d.file || d.path); latexJumpToLine(line); };
      li.addEventListener('click', jump);
      li.addEventListener('keydown', (e) => {
        if (e && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); jump(); }
      });
    }
    if (typeof ul.appendChild === 'function') ul.appendChild(li);
  }
  if ((list || []).length) ul.hidden = false;
}

function renderLatexLog(text) {
  const pre = $('latex-raw-log');
  if (!pre) return;
  if (text) { pre.textContent = latexBoundLog(text); pre.hidden = false; }
  else { pre.textContent = ''; pre.hidden = true; }
}

// The raw log is kept for the run it came from, bounded so a pathological build
// cannot grow the panel without limit. Only the tail is kept, and it says so.
const LATEX_LOG_LIMIT = 8000;
function latexBoundLog(text) {
  const full = String(text);
  if (full.length <= LATEX_LOG_LIMIT) return full;
  return latexT('(earlier log lines omitted; showing the last {count} characters)', {count: LATEX_LOG_LIMIT})
    + '\n' + full.slice(full.length - LATEX_LOG_LIMIT);
}

// The PDF bytes always come from the server (export.pdf behind the compile's pdf_url);
// call() only parses JSON, so the binary leg uses fetch directly. A PDF is shown only
// for the revision it was built from — never for a draft that has moved on.
async function latexFetchPdf(url) {
  const res = await fetch(API + url, {method: 'GET'});
  if (!res.ok) throw new Error(latexT('PDF unavailable (HTTP {status})', {status: res.status}));
  const buf = await res.arrayBuffer();
  const signature = new Uint8Array(buf, 0, Math.min(buf.byteLength, 4));
  if (signature.length !== 4 || signature[0] !== 37 || signature[1] !== 80 || signature[2] !== 68 || signature[3] !== 70)
    throw new Error(latexT('The backend did not return PDF bytes.'));
  return new Blob([buf], {type: 'application/pdf'});
}

async function latexHandleCompileResult(res, requestedRevision) {
  const state = latexState;
  const revision = (res && typeof res.revision === 'number') ? res.revision : requestedRevision;
  if (!state || !state.draft || state.dirty || state.draft.revision !== revision) {
    // The source moved on while the build ran. Say which revision was built and
    // show nothing from it: no stale PDF, and no diagnostics pinned to the live file.
    renderLatexDiagnostics([]);
    renderLatexLog((res && (res.log_tail ?? res.log_text)) || '');
    latexSetStatus('Not shown: the draft changed during compilation (compiled revision {revision}).', 'fail', {revision});
    return null;
  }
  renderLatexDiagnostics((res && res.diagnostics) || []);
  renderLatexLog((res && (res.log_tail ?? res.log_text)) || '');
  const status = res && res.status;
  if (status === 'ok' && res.pdf_url) {
    try {
      const pdf = await latexFetchPdf(res.pdf_url);
      if (latexState !== state || state.dirty || !state.draft || state.draft.revision !== revision) {
        // Same rule after the bytes arrive: the build is for an older source, so its
        // line numbers are cleared and only its raw text is kept.
        renderLatexDiagnostics([]);
        latexSetStatus('Not shown: the draft changed during compilation (compiled revision {revision}).', 'fail', {revision});
        return null;
      }
      renderPreview(pdf);
      state.compiledRevision = revision;
      latexSetStatus('Compiled from revision {revision}', 'ok', {revision},
        res.citations_resolved === false ? ' · citations unresolved (bibliography processing is not implemented)' : null);
    } catch (e) {
      latexSetStatus('Compiled, but the PDF could not be shown: {reason}', 'fail', {reason: (e && e.message) || latexT('download failed')});
    }
    return res;
  }
  if (status === 'toolchain_absent') {
    const checked = Array.isArray(res.checked) ? res.checked.join(', ') : '';
    latexSetStatus(checked
      ? 'Not compiled: no LaTeX engine on this machine (checked {engines}).'
      : 'Not compiled: no LaTeX engine on this machine.', 'fail', checked ? {engines: checked} : null);
    return res;
  }
  latexSetStatus('Not compiled ({status}): see the diagnostics and raw log.', 'fail', {status: status || latexT('error')});
  return res;
}

async function compileDraft() {  // Slice 2: POST the real route; never pretend a PDF exists.
  const state = latexState;
  if (!state || !state.draft) { latexSetStatus('Nothing to compile: open a draft first.', 'fail'); return null; }
  if (state.reloading) {
    latexSetStatus('Not compiled: a reload is in progress. Wait for it to finish.', 'fail');
    return null;
  }
  if (state.dirty || state.saving) {
    if (!await saveDraft('manual') || latexState !== state || state.dirty) return null;
  }
  const caps = state.capabilities;
  if (!caps || !caps.available) {
    latexSetStatus('Not compiled: no LaTeX engine was found on this machine, so no PDF was produced.', 'fail');
    return null;
  }
  if (state.compiling) return null;
  const did = state.draft.draft_id;
  const revision = state.draft.revision;
  const request = latexRequest;
  state.compiling = true;
  syncLatexControls();
  latexSetStatus('Compiling…', null);
  renderPreview(null);
  state.compiledRevision = null;
  renderLatexDiagnostics([]);
  renderLatexLog('');
  try {
    // Exact-key body per the testspec: {source: "manual"} and nothing else.
    const res = await call('POST', `/drafts/${encodeURIComponent(did)}/compile`, {source: 'manual'});
    if (request !== latexRequest || latexState !== state) return null;
    return await latexHandleCompileResult(res, revision);
  } catch (e) {
    if (request !== latexRequest || latexState !== state) return null;
    latexSetStatus('Not compiled: {reason}', 'fail', {reason: (e && e.message) || latexT('the compile request failed.')});
    return null;
  } finally {
    if (latexState === state) {
      state.compiling = false; syncLatexControls();
      if (state.dirty && !state.blocked && !latexTimer)
        latexTimer = setTimeout(() => { latexTimer = null; saveDraft('autosave'); }, LATEX_AUTOSAVE_MS);
    }
  }
}

// Autosave on keystroke idle; the explicit Save button and ⌘S stay available.
function bindLatexEditor() {
  const editor = $('latex-editor');
  if (!editor || editor.__latexBound) return;
  editor.__latexBound = true;
  editor.addEventListener('input', scheduleLatexAutosave);
}

// ⌘S / Ctrl+S while the writing view is open, wherever focus sits in it: toolbar,
// file list, syntax palette, dialog buttons or the editor itself. One scoped handler
// means the editor no longer needs its own, so a keystroke can never save twice.
//
// "Open" is not just the hidden class: another surface (the library, the citation
// composer) takes the workspace by swapping the section's mode class while the
// writing view keeps `hidden` unset in some paths. So the writing mode must be the
// active mode, and when the element can be measured it must actually have layout.
function latexIsWritingVisible() {
  const view = $('latex-view');
  if (!view || !view.classList || view.classList.contains('hidden')) return false;
  const section = document.querySelector('main section');
  if (section && section.classList && !section.classList.contains('writing-mode')) return false;
  if (typeof view.getClientRects === 'function') {
    const rects = view.getClientRects();
    if (rects && typeof rects.length === 'number' && rects.length === 0) return false;
  }
  return true;
}

function latexOnDocumentKeydown(e) {
  if (!latexIsWritingVisible()) return;
  if (!(e && (e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === 's')) return;
  if (e.repeat) return;
  if (typeof e.preventDefault === 'function') e.preventDefault();
  saveDraft('manual');
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('keydown', latexOnDocumentKeydown, true);
}

// A language change must not rebuild the draft: renderDraft() would reset the dirty
// flag and replace the editor text. Only the chrome is redrawn, so unsaved text,
// caret, selected file, compiled revision and the shown PDF all survive.
function redrawLatexLanguage() {
  const st = latexState;
  const label = $('latex-draft-label');
  if (label) label.textContent = st && st.draft ? latexDraftLabel() : latexT('Open a project to start a draft.');
  renderLatexStatus();
  renderLatexAbsence();
  const notice = $('latex-notice');
  if (notice && st && st.draft) notice.textContent = latexDraftNotice(st.draft);
  renderLatexFiles();
  syncLatexControls();
  // The pane keeps exactly the document it showed: only its accessible name is
  // re-worded, and no blob URL is revoked or refetched.
  for (const frame of document.querySelectorAll('.latex-preview iframe')) frame.title = latexT('Compiled PDF');
  const pane = $('latex-preview');
  if (pane && !latexPdfUrl && pane.textContent) pane.textContent = latexT('No PDF yet.');
  if (!st || !st.draft) renderPreview(null);
}

if (WB_GLOBAL_LATEX.WB_I18N && WB_GLOBAL_LATEX.WB_I18N.onChange) {
  WB_GLOBAL_LATEX.WB_I18N.onChange(redrawLatexLanguage);
}

// The writing view hides, but the project's draft stays in memory with its timer and
// unsaved text intact: clearing a question, deleting one or ending a session is not a
// project switch. Callers that truly leave the project use latexBeforeProjectSwitch().
function clearLatex() {
  setLatexViewVisible(false);
}

async function latexFlush(reason) {
  const state = latexState;
  if (!state || !state.draft) return true;
  // A reload in flight owns the draft: its GET will replace the buffer, so saving the
  // pre-reload text here would persist exactly what the researcher asked to discard.
  // Checked before the queued autosave is dropped, so a refused switch leaves it alive.
  if (state.reloading) {
    latexSetStatus('Not switched: a reload is in progress. Wait for it to finish.', 'fail');
    return false;
  }
  if (latexTimer) { clearTimeout(latexTimer); latexTimer = null; }
  if (state.saving) {
    if (state.pendingSave) await state.pendingSave;
    if (latexState !== state) return true;
  }
  // A reload may have started while the in-flight save was awaited.
  if (state.reloading) {
    latexSetStatus('Not switched: a reload is in progress. Wait for it to finish.', 'fail');
    return false;
  }
  // A build in flight is harmless: leaving drops its result. Unsaved text is not.
  if (state.compiling && (state.dirty || state.saving)) {
    latexSetStatus('Not switched: wait for the compile to finish, or save the draft first.', 'fail');
    return false;
  }
  if (state.blocked) {
    latexSetStatus('Not switched: this paper has a save conflict. Reload it before leaving the project.', 'fail');
    return false;
  }
  if (!state.dirty) return true;
  const saved = await saveDraft(reason || 'manual');
  if (latexState !== state) return true;
  return !!(saved && !state.dirty && !state.blocked);
}

// Hooks for the project owner (app.js). openProject() must await this BEFORE it
// assigns S.project, so a failed or conflicted save can still cancel the switch.
async function latexBeforeProjectSwitch() {
  const state = latexState;
  if (!state || !state.draft) { latexUnmount(); return true; }
  const flushed = await latexFlush('manual');
  if (!flushed) return false;
  latexUnmount();
  const editor = $('latex-editor');
  if (editor) editor.value = '';
  const label = $('latex-draft-label');
  if (label) label.textContent = latexT('Open a project to start a draft.');
  latexAbsence(null);
  latexLastStatus = {key: 'Idle', vars: null, state: null, extraKey: null};
  renderLatexStatus();
  setLatexViewVisible(false);
  return true;
}

// The app owns project switching; expose both hooks on the global scope where app.js
// can reach them. A missing openProject() call only costs a flush, never data.
WB_GLOBAL_LATEX.latexBeforeProjectSwitch = latexBeforeProjectSwitch;

// Closing the window with unsaved text asks the standard question. Nothing is
// promised beyond that browser-owned prompt: no async flush runs on unload, and a
// saved, clean draft never blocks the close.
function latexHasUnsavedWork() {
  return !!(latexState && latexState.draft && (latexState.dirty || latexState.saving));
}

function latexOnBeforeUnload(e) {
  if (!latexHasUnsavedWork()) return undefined;
  if (e) { e.preventDefault(); e.returnValue = ''; }
  return '';
}

if (WB_GLOBAL_LATEX.addEventListener) {
  WB_GLOBAL_LATEX.addEventListener('beforeunload', latexOnBeforeUnload);
}
// Keep the Compile control honest across every existing controls() pass: the base
// function still owns all other buttons; this only re-asserts the compile state.
if (typeof controls === 'function') {
  const latexBaseControls = controls;
  controls = function () {
    latexBaseControls();
    syncLatexControls();
  };
}

// --- Korean strings for the writing view -----------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// Sentences that carry a value use {placeholder} so each language keeps its own
// word order. Technical names (LaTeX, PDF, engine names, revision) stay as-is:
// they name a thing the researcher also sees on disk or in the compile note.
if (WB_GLOBAL_LATEX.WB_I18N) WB_GLOBAL_LATEX.WB_I18N.register('ko', {
  'Open a project to start a draft before compiling.': '컴파일하려면 먼저 프로젝트를 열어 초안을 시작하세요.',
  'Compiling the saved draft…': '저장된 초안을 컴파일하는 중…',
  'Compile stays disabled: no LaTeX engine was found on this machine.': '컴파일이 비활성 상태입니다: 이 컴퓨터에서 LaTeX 엔진을 찾지 못했습니다.',
  'Compile sends the saved draft to the local backend and shows the returned PDF.':
    '컴파일은 저장된 초안을 로컬 백엔드로 보내고, 돌아온 PDF를 보여줍니다.',
  'No draft yet.': '아직 초안이 없습니다.',
  ' · unsaved changes': ' · 저장하지 않은 변경 사항',
  '{title} · main file {file} · revision {revision}{dirty}':
    '{title} · 주 파일 {file} · 리비전 {revision}{dirty}',
  'Delete {file}, including its unsaved changes?': '{file} 파일과 저장하지 않은 변경 사항을 삭제할까요?',
  'Replace {file}, including any unsaved changes?': '저장하지 않은 변경 사항을 포함해 {file} 파일을 바꿀까요?',
  'Maximum 10 files.': '파일은 최대 10개입니다.',
  'Import a safe .tex, .bib or .latex file up to 512 KiB.': '안전한 .tex, .bib 또는 .latex 파일을 512 KiB 이하로 가져오세요.',
  'Not switched: this paper has a save conflict. Reload it before leaving the project.':
    '전환하지 않았습니다: 이 논문은 저장 충돌 상태입니다. 프로젝트를 떠나기 전에 다시 불러오세요.',
  'Not switched: wait for the compile to finish, or save the draft first.':
    '전환하지 않았습니다: 컴파일이 끝날 때까지 기다리거나 먼저 초안을 저장하세요.',
  'Not opened: the previous paper has unsaved changes that could not be saved.':
    '열지 않았습니다: 이전 논문에 저장하지 못한 변경 사항이 있습니다.',
  'Reload refused while a save is in progress. Wait for it to finish.':
    '저장이 진행 중일 때는 다시 불러올 수 없습니다. 저장이 끝날 때까지 기다리세요.',
  'Not saved: a reload is in progress. Your text is still in the editor.':
    '저장하지 못했습니다: 다시 불러오는 중입니다. 작성한 텍스트는 편집기에 그대로 있습니다.',
  'Not reloaded: the editor changed while reloading. Your text is still in the editor; reload again to discard it.':
    '다시 불러오지 못했습니다: 불러오는 동안 편집기가 바뀌었습니다. 작성한 텍스트는 편집기에 그대로 있으니, 버리려면 다시 불러오세요.',
  'Not compiled: a reload is in progress. Wait for it to finish.':
    '컴파일하지 못했습니다: 다시 불러오는 중입니다. 끝날 때까지 기다리세요.',
  'Not switched: a reload is in progress. Wait for it to finish.':
    '전환하지 않았습니다: 다시 불러오는 중입니다. 끝날 때까지 기다리세요.',
  'Not changed: a reload is in progress. Wait for it to finish.':
    '변경하지 않았습니다: 다시 불러오는 중입니다. 끝날 때까지 기다리세요.',
  'Not opened: {reason}': '열지 못했습니다: {reason}',
  'the paper could not be opened.': '논문을 열지 못했습니다.',
  'The backend did not return a paper.': '백엔드가 논문을 반환하지 않았습니다.',
  '(earlier log lines omitted; showing the last {count} characters)':
    '(앞선 로그 줄은 생략되었습니다. 마지막 {count}자만 표시합니다)',

  ' Engine xelatex (chosen because the imported text is not plain ASCII). Korean text also needs the kotex package and fonts; not verified on this machine.':
    ' 엔진은 xelatex입니다(가져온 텍스트가 순수 ASCII가 아니어서 선택되었습니다). 한글 본문은 kotex 패키지와 글꼴도 필요하며, 이 컴퓨터에서는 검증되지 않았습니다.',
  'Compiled PDF': '컴파일된 PDF',
  'Open a project before writing.': '글을 쓰려면 먼저 프로젝트를 열어 주세요.',
  'A LaTeX engine is on PATH. Compile sends the saved draft to the local backend and shows the returned PDF.':
    'LaTeX 엔진이 PATH에 있습니다. 컴파일은 저장된 초안을 로컬 백엔드로 보내고, 돌아온 PDF를 보여줍니다.',
  'A LaTeX engine is on PATH (found: {engines}). Compile sends the saved draft to the local backend and shows the returned PDF.':
    'LaTeX 엔진이 PATH에 있습니다(발견: {engines}). 컴파일은 저장된 초안을 로컬 백엔드로 보내고, 돌아온 PDF를 보여줍니다.',
  'PDF preview needs a LaTeX engine. This machine has none of pdflatex, xelatex, lualatex, latexmk, tectonic on its PATH. You can still write and autosave. Compile stays disabled; the app will not install an engine for you.':
    'PDF 미리보기에는 LaTeX 엔진이 필요합니다. 이 컴퓨터의 PATH에는 pdflatex, xelatex, lualatex, latexmk, tectonic 중 어느 것도 없습니다. 작성과 자동 저장은 그대로 쓸 수 있습니다. 컴파일은 비활성 상태로 남으며, 앱이 엔진을 대신 설치하지는 않습니다.',
  'Writing view unavailable.': '작성 화면을 사용할 수 없습니다.',
  'Draft unavailable': '초안을 사용할 수 없습니다',
  'Idle · revision {revision}': '대기 중 · 리비전 {revision}',
  'Idle · saved {time} · revision {revision}': '대기 중 · {time}에 저장됨 · 리비전 {revision}',
  'Save failed.': '저장하지 못했습니다.',
  'Draft changed; reload before saving. Your text is still in the editor.':
    '초안이 변경되었습니다. 저장하기 전에 다시 불러오세요. 작성한 텍스트는 편집기에 그대로 있습니다.',
  'Not saved: {reason}': '저장하지 못했습니다: {reason}',
  'Reload failed: {reason}': '다시 불러오지 못했습니다: {reason}',
  'unknown error': '알 수 없는 오류',
  'Engine check failed: {reason}': '엔진 확인에 실패했습니다: {reason}',
  'Unknown citation key · line {line}: {message} — fix the key or add it to the bibliography.':
    '알 수 없는 인용 키 · {line}행: {message} — 키를 고치거나 참고문헌에 추가하세요.',
  'Unknown citation key: {message} — fix the key or add it to the bibliography.':
    '알 수 없는 인용 키: {message} — 키를 고치거나 참고문헌에 추가하세요.',
  '{severity} · line {line}: {message}': '{severity} · {line}행: {message}',
  '{severity}: {message}': '{severity}: {message}',
  'PDF unavailable (HTTP {status})': 'PDF를 가져오지 못했습니다 (HTTP {status})',
  'Not shown: the draft changed during compilation (compiled revision {revision}).':
    '표시하지 않음: 컴파일 도중 초안이 바뀌었습니다 (컴파일된 리비전 {revision}).',
  'Compiled from revision {revision}': '리비전 {revision}에서 컴파일됨',
  ' · citations unresolved (bibliography processing is not implemented)': ' · 인용 미해결 (참고문헌 처리 기능은 아직 구현되지 않음)',
  'Compiled, but the PDF could not be shown: {reason}': '컴파일은 되었지만 PDF를 표시하지 못했습니다: {reason}',
  'download failed': '내려받기에 실패했습니다',
  'Not compiled: no LaTeX engine on this machine (checked {engines}).':
    '컴파일하지 못했습니다: 이 컴퓨터에 LaTeX 엔진이 없습니다 ({engines} 확인).',
  'Not compiled: no LaTeX engine on this machine.': '컴파일하지 못했습니다: 이 컴퓨터에 LaTeX 엔진이 없습니다.',
  'Not compiled ({status}): see the diagnostics and raw log.':
    '컴파일하지 못했습니다 ({status}): 진단과 원본 로그를 확인하세요.',
  'error': '오류',
  'Nothing to compile: open a draft first.': '컴파일할 대상이 없습니다: 먼저 초안을 여세요.',
  'Not compiled: no LaTeX engine was found on this machine, so no PDF was produced.':
    '컴파일하지 못했습니다: 이 컴퓨터에서 LaTeX 엔진을 찾지 못해 PDF가 만들어지지 않았습니다.',
  'Compiling…': '컴파일하는 중…',
  'Not compiled: {reason}': '컴파일하지 못했습니다: {reason}',
  'the compile request failed.': '컴파일 요청이 실패했습니다.',
  'Cite this sentence': '이 문장에 인용 붙이기',
  'Opens the Citation Composer for the sentence in this draft. Nothing is written into the paper until you approve a proposal.':
    '이 초안의 문장에 대해 인용 작성기를 엽니다. 제안을 승인하기 전에는 논문에 아무것도 쓰지 않습니다.',
  'No PDF yet.': '아직 PDF가 없습니다.',
  'Korean (Hangul) text is not supported yet: this build compiles with pdfLaTeX only, and Hangul needs XeLaTeX with the kotex package, which is not enabled here. Your imported source text is never rewritten.':
    '한글 본문은 아직 지원하지 않습니다: 이 빌드는 pdfLaTeX만 사용하며, 한글에는 kotex 패키지가 포함된 XeLaTeX가 필요하지만 여기서는 활성화되어 있지 않습니다. 가져온 소스 텍스트는 절대 바꾸지 않습니다.',
  'The main file cannot be renamed or deleted.': '주 파일은 이름을 바꾸거나 삭제할 수 없습니다.',
  'Compile the current saved revision before downloading a PDF.': 'PDF를 내려받으려면 먼저 현재 저장된 리비전을 컴파일하세요.',
  'No paper yet. Select New paper to begin.': '아직 논문이 없습니다. 새 논문을 선택해 시작하세요.',
  'each file must be at most 512 KiB.': '각 파일은 512 KiB 이하여야 합니다.',
  'Discard all unsaved file changes and reload?': '저장하지 않은 모든 파일 변경을 버리고 다시 불러올까요?',
  'The backend did not return PDF bytes.': '백엔드가 PDF 바이트를 반환하지 않았습니다.',
  'Paper opened · revision {revision}': '논문을 열었습니다 · 리비전 {revision}',
  'Saving…': '저장하는 중…',
  'Unsaved changes · saving again shortly': '저장하지 않은 변경 사항 · 곧 다시 저장합니다',
  'Unsaved changes': '저장하지 않은 변경 사항',
  'Open a project to start a draft.': '프로젝트를 열면 초안을 시작할 수 있습니다.',
  'Idle': '대기 중'
});

