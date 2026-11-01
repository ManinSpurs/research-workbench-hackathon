// Research library UI review: folder create/rename, drag-drop move, note day
// grouping, PDF viewer iframe URL, search hits, and the honest unavailable-screen
// path — plus the collapsible panels and the expandable working surface.
// Serves the real frontend files and scripts the /library contract with fixtures
// (the parallel backend worker owns the real routes). No real model call.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const markup = await readFile(root + 'index.html', 'utf8');
const FILES = ['index.html', ...Array.from(markup.matchAll(/(?:src|href)="([^"]+\.(?:css|js))"/g), match => match[1])];
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  try {
    const file = path === '/' ? 'index.html' : path.slice(1);
    if (path.startsWith('/assets/')) {
      const asset = await readFile(root + file);
      res.setHeader('Content-Type', 'image/png'); res.end(asset); return;
    }
    if (!FILES.includes(file)) throw new Error('not served');
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript'
      : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(await readFile(root + file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });

try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  // --- Fixture state for the /library contract.
  let folders = [
    { id: 'f1', name: 'Papers', parent_id: null, created_at: '2026-10-20T10:00:00Z' },
    { id: 'f2', name: 'Archive', parent_id: null, created_at: '2026-10-20T10:00:00Z' },
    { id: 'f3', name: 'Papers/Drafts', parent_id: 'f1', created_at: '2026-10-20T10:00:00Z' },
  ];
  let items = [
    { id: 'i1', folder_id: null, kind: 'pdf', title: 'Permit approval report', day: null, pages: 12, chars: 4012, created_at: '2026-10-24T09:00:00Z', text_status: 'OK' },
    { id: 'i2', folder_id: 'f1', kind: 'pdf', title: 'Methods appendix', day: null, pages: 4, chars: 900, created_at: '2026-10-24T09:30:00Z', text_status: 'NO_TEXT_LAYER' },
    { id: 'n1', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-26', pages: null, chars: 20, created_at: '2026-10-26T12:00:00Z', text: 'Checked the dates.' },
    { id: 'n2', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-26', pages: null, chars: 12, created_at: '2026-10-26T15:00:00Z', text: 'Ask about scope.' },
    { id: 'n3', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-24', pages: null, chars: 9, created_at: '2026-10-24T08:00:00Z', text: 'Early idea.' },
  ];
  const sent = { folders: [], folderPatch: [], notes: [], docs: [], itemPatch: [], screen: [] };
  let serial = 0;
  const tree = () => ({ folders, items: items.map(({ text, ...rest }) => rest) });

  await page.route('http://wb.test/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { body = null; }
    const reply = data => route.fulfill({ json: data });
    // The pack list is server-owned: both the start screen and #pack-select read
    // it, so the fixture answers with the sample packs plus no installed chips.
    if (path === '/domain-packs') return reply({ packs: [
      { pack_id: 'social_science', version: '1.0.0', title: 'Social Science' },
      { pack_id: 'engineering_measurement', version: '1.0.0', title: 'Engineering: Measurement' }], default_pack: 'social_science' });
    if (path === '/chips') return reply({ chips: [] });
    if (path === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
    if (path === '/library/tree') return reply(tree());
    if (/^\/library\/docs\/[^/]+\/file$/.test(path))
      return route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n%%EOF\n') });
    if (path === '/library/folders' && req.method() === 'POST') {
      sent.folders.push(body);
      const folder = { id: `f${++serial + 10}`, name: body.name, parent_id: body.parent_id ?? null, created_at: '2026-10-27T10:00:00Z' };
      folders = [...folders, folder];
      return reply(folder);
    }
    if (/^\/library\/folders\/[^/]+$/.test(path) && req.method() === 'PATCH') {
      const fid = decodeURIComponent(path.split('/').pop());
      sent.folderPatch.push({ fid, body });
      folders = folders.map(f => f.id === fid ? { ...f, ...body } : f);
      return reply(folders.find(f => f.id === fid));
    }
    if (path === '/library/notes' && req.method() === 'POST') {
      sent.notes.push(body);
      const note = { id: `n${++serial + 20}`, folder_id: body.folder_id ?? null, kind: 'note', title: body.title || 'Note',
        day: '2026-10-27', pages: null, chars: String(body.text).length, created_at: '2026-10-27T18:00:00Z', text: body.text };
      items = [...items, note];
      return reply({ ...note });
    }
    if (path === '/library/docs' && req.method() === 'POST') {
      sent.docs.push({ ...body, pdf_base64: body.pdf_base64 ? `<${body.pdf_base64.length} b64 chars>` : null });
      const doc = { id: `d${++serial + 30}`, folder_id: body.folder_id ?? null, kind: 'pdf',
        title: body.filename, pages: 1, chars: 0, text_status: 'OK' };
      items = [...items, { ...doc, day: null, created_at: '2026-10-27T18:00:00Z' }];
      return reply(doc);
    }
    if (/^\/library\/items\/[^/]+$/.test(path) && req.method() === 'PATCH') {
      const iid = decodeURIComponent(path.split('/').pop());
      sent.itemPatch.push({ iid, body });
      items = items.map(i => i.id === iid ? { ...i, ...body } : i);
      return reply(items.find(i => i.id === iid));
    }
    if (path === '/library/search') {
      const q = url.searchParams.get('q');
      return reply(q === 'dates'
        ? [{ item_id: 'i1', title: 'Permit approval report', page: 7, snippet: 'The permit dates were recorded in row 4.' }]
        : []);
    }
    if (path === '/library/screen' && req.method() === 'POST') {
      sent.screen.push(body);
      // First call: the local model is absent. Later calls: entries that did come
      // back, with one honest dropped count.
      if (sent.screen.length === 1) return reply({ available: false, reason: 'No local model is installed on this machine.' });
      return reply({ available: true, summary: 'Three documents screened.', unverified_dropped: 2,
        items: [{ item_id: 'i1', screen: 'Relevant to the approval question.' }, { item_id: 'i2', screen: 'Unclear: no text layer.' }] });
    }
    // --- The rest of the workspace, enough to open a project.
    if (path === '/projects' && req.method() === 'GET')
      return reply({ projects: [{ id: 'p1', name: 'Permit study', kind: 'nyc', pack_id: null }] });
    if (path === '/briefing') return reply({ rows: [] });
    if (path === '/projects/p1/profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (path === '/projects/p1/briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (path === '/projects/p1/clarifications') return reply({ clarifications: [] });
    if (path === '/projects/p1/revisit-conditions') return reply({ project_id: 'p1', conditions: [] });
    if (path === '/projects/p1/secretary-turns') return reply({ project_id: 'p1', turns: [] });
    if (path === '/projects/p1/pack-memory') return reply({ questions: [] });
    if (path === '/capabilities/latex') return reply({ available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null }, note: 'No TeX engine found on this machine.' });
    if (path === '/projects/p1/drafts') return reply({ project_id: 'p1', drafts: [] });
    if (path === '/chips/social_science/record') return reply({ questions: [], answers: [], results: [], code: [], harness: [] });
    if (path === '/projects/p1/drafts' && req.method() === 'POST') return reply({ draft_id: 'draft-1', project_id: 'p1', title: 'Untitled paper', engine: 'auto', main_file: 'main.tex', revision: 0, files: [{ path: 'main.tex', text: '' }], notice: '', model_calls: 0 });
    throw new Error('Unexpected endpoint ' + path);
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await page.goto(`${origin}/?api=http://wb.test`);
  // The start console now shows the home menu first; the workspace is behind it.
  // Enter Research the way a researcher would, so the click below hits a visible
  // surface rather than a hidden portal.
  await page.getByTestId('home-menu-research').click();
  await page.locator('#quiet-projects').click();
  await page.getByTestId('open-nyc').click();
  await page.waitForFunction(() => S.project === 'p1');
  if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
    await page.locator('#quiet-projects').click();

  // --- (1) Research stays the default view.
  assert.equal(await page.getByTestId('view-research').getAttribute('aria-pressed'), 'true', 'Research is the default view');
  assert.equal(await page.getByTestId('library-view').isVisible(), false, 'library surface is hidden by default');

  // --- (2) Library view mounts and loads the tree.
  await page.evaluate(() => openLibraryView());
  await page.getByTestId('library-folder').first().waitFor();
  // Only one surface mounts at a time: Writing then Library leaves no editor behind.
  assert.equal(await page.locator('#latex-view').isVisible(), false, 'the writing view is not mounted under the library');
  assert.equal(await page.getByTestId('view-library').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByTestId('view-research').getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#question').isVisible(), false, 'library replaces the research surface');  assert.equal(await page.getByTestId('library-folder').count(), 3, 'every folder is in the tree');
  assert.equal(await page.locator('.library-folder-children .library-folder-row').count(), 1, 'nested folder is inside its parent');
  assert.equal(await page.getByTestId('library-item').count(), 5, 'PDF rows and notes are listed');

  // Switching to Writing leaves the library surface, and coming back restores it:
  // the three tabs never mount two surfaces at once.
  await page.evaluate(() => openWritingView());
  await page.getByTestId('latex-absence').waitFor();
  assert.equal(await page.getByTestId('library-view').isVisible(), false, 'the writing view replaces the library');
  assert.equal(await page.getByTestId('view-library').getAttribute('aria-pressed'), 'false');
  await page.evaluate(() => openLibraryView());
  await page.getByTestId('library-folder').first().waitFor();
  assert.equal(await page.locator('#latex-view').isVisible(), false, 'the writing view is closed again');
  assert.equal(await page.getByTestId('view-library').getAttribute('aria-pressed'), 'true');

  // --- (3) Create a folder (POST /library/folders with the open project).
  await page.getByTestId('new-folder').click();
  await page.locator('[data-testid="new-folder-input"]').fill('Field notes');
  await page.locator('[data-testid="new-folder-input"]').press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('.library-folder-open')].some(b => b.textContent === 'Field notes'));
  assert.deepEqual(sent.folders.at(-1), { project_id: 'p1', name: 'Field notes', parent_id: null },
    'new folder posts the contract payload');
  assert.match(await page.getByTestId('library-status').innerText(), /created/);

  // --- (4) Rename by double-click, and rename from the toolbar button.
  const fieldRow = page.locator('.library-folder-line', { has: page.locator('button', { hasText: 'Field notes' }) });
  await fieldRow.locator('button').dblclick();
  await page.locator('[data-testid="rename-folder-input"]').fill('Field notes 2026');
  await page.locator('[data-testid="rename-folder-input"]').press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('.library-folder-open')].some(b => b.textContent === 'Field notes 2026'));
  const renamed = folders.find(f => f.name === 'Field notes 2026');
  assert.deepEqual(sent.folderPatch.at(-1), { fid: renamed.id, body: { name: 'Field notes 2026' } }, 'rename patches the folder name');
  // The toolbar rename acts on the folder selected in the tree.
  await page.locator('.library-folder-open', { hasText: 'Archive' }).click();
  await page.getByTestId('rename-folder').click();
  await page.locator('[data-testid="rename-folder-input"]').fill('Old archive');
  await page.locator('[data-testid="rename-folder-input"]').press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('.library-folder-open')].some(b => b.textContent === 'Old archive'));
  assert.equal(sent.folderPatch.at(-1).fid, 'f2', 'toolbar rename targets the selected folder');

  // --- (5) Drag a document row onto a folder: PATCH /library/items/{id}.
  await page.locator('.library-folder-open', { hasText: 'All documents' }).click();
  const rowToDrag = page.locator('.library-item-row[data-item-id="i1"]');
  const target = page.locator('.library-folder-row[data-folder-id="f1"] .library-folder-line').first();
  const dt = await page.evaluateHandle(() => new DataTransfer());
  await rowToDrag.dispatchEvent('dragstart', { dataTransfer: dt });
  await target.dispatchEvent('dragover', { dataTransfer: dt });
  assert.ok((await target.getAttribute('class') || '').includes('drop-target'), 'the folder shows a drop target highlight');
  await target.dispatchEvent('drop', { dataTransfer: dt });
  await page.waitForFunction(() => !S.busy);
  await page.waitForFunction(() => {
    const el = document.querySelector('.library-item-row[data-item-id="i1"]');
    return el && el.dataset.kind === 'pdf' && document.querySelectorAll('.library-item-row').length === 5;
  });
  assert.deepEqual(sent.itemPatch.at(-1), { iid: 'i1', body: { folder_id: 'f1' } }, 'drag-drop moves the document into the folder');
  assert.equal(items.find(i => i.id === 'i1').folder_id, 'f1');
  await page.locator('.library-folder-open', { hasText: 'Papers' }).first().click();
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length === 2);
  assert.equal(await page.locator('.library-item-row[data-item-id="i1"]').count(), 1, 'the moved document is listed in its new folder');

  // --- (6) Note day grouping: a day header per day, newest day first.
  await page.locator('.library-folder-open', { hasText: 'All documents' }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="library-day"]').length === 2);
  assert.deepEqual(await page.getByTestId('library-day').allInnerTexts(), ['2026-10-26', '2026-10-24'],
    'notes are grouped by day with the newest day first');
  const firstDay = await page.locator('.library-day-header').first().evaluate(el => el.dataset.day);
  assert.equal(firstDay, '2026-10-26');
  // The composer mentions no date field, and saving a note needs only its text.
  assert.equal(await page.locator('#library-note-composer label').innerText(), 'New note in this folder');
  await page.getByTestId('note-input').fill('Follow up on the missing dictionary.');
  await page.getByTestId('note-save').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="library-day"]').length === 3);
  assert.deepEqual(sent.notes.at(-1), { project_id: 'p1', folder_id: null, text: 'Follow up on the missing dictionary.' },
    'the note posts text and the folder, never a date');
  assert.equal(await page.getByTestId('note-input').inputValue(), '', 'the composer clears after saving');
  // The tree listing carries no note body, so a note is opened honestly rather
  // than as an empty pane; a note written in this session keeps its own words.
  await page.locator('.library-item-row[data-item-id="n1"]').click();
  assert.match(await page.getByTestId('library-note-text').innerText(),
    /saved in an earlier session; the library listing does not include its text/,
    'a note without a stored body says so instead of showing an empty pane');
  const newNote = items.at(-1);
  await page.locator(`.library-item-row[data-item-id="${newNote.id}"]`).click();
  assert.equal(await page.getByTestId('library-note-text').innerText(), 'Follow up on the missing dictionary.',
    'a note written in this session reopens with its own text');

  // --- (7) A PDF row opens the viewer with the backend file URL.
  await page.locator('.library-item-row[data-item-id="i1"]').click();
  const src = await page.locator('#library-viewer iframe').getAttribute('src');
  assert.equal(src, 'http://wb.test/library/docs/i1/file?project_id=p1', 'the viewer points at the backend PDF file route');
  assert.match(await page.locator('#library-viewer').innerText(), /Permit approval report/);

  // --- (8) Search hit rendering, then the hit opens the doc with its snippet and page.
  await page.getByTestId('library-search').fill('dates');
  await page.getByTestId('library-search-run').click();
  await page.getByTestId('library-hit-0').waitFor();
  const hitText = await page.getByTestId('library-hit-0').innerText();
  assert.match(hitText, /Permit approval report/);
  assert.match(hitText, /Page 7/);
  await page.getByTestId('library-hit-0').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="library-snippet"]'));
  const snippet = await page.getByTestId('library-snippet').innerText();
  assert.match(snippet, /Page 7: The permit dates were recorded in row 4\./);
  assert.equal(await page.locator('#library-viewer iframe').getAttribute('src'),
    'http://wb.test/library/docs/i1/file?project_id=p1', 'the hit opened the matching document');
  await page.getByTestId('library-search').fill('nothingmatches');
  await page.getByTestId('library-search-run').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="library-hit-0"]').length === 0);
  assert.match(await page.getByTestId('library-search-summary').innerText(), /No stored text matches/);

  // --- (9b) The drop zone's file-input fallback uploads a PDF to the open project.
  await page.getByTestId('library-file-input').setInputFiles({
    name: 'new-permit.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n'),
  });
  await page.waitForFunction(() => document.querySelector('[data-testid="library-status"]').textContent.includes('PDF file added'));
  assert.equal(await page.locator('.library-item-row').count(), 3, 'the uploaded PDF joins the documents in the open folder');
  const posted = sent.docs.at(-1);
  assert.equal(posted.folder_id, 'f1', 'the fallback files the upload into the selected folder');
  assert.equal(posted.project_id, 'p1');
  assert.equal(posted.filename, 'new-permit.pdf');
  assert.match(posted.pdf_base64, /^<\d+ b64 chars>$/, 'the PDF bytes are sent as base64, not as a placeholder');
  assert.match(await page.getByTestId('library-status').innerText(), /1 PDF file added/);

  // --- (9c) The first-pass screen states an unavailable local model plainly.
  await page.getByTestId('library-screen').click();
  await page.getByTestId('library-screen-message').waitFor();
  const screenText = await page.getByTestId('library-screen-message').innerText();
  assert.equal(screenText, 'No first-pass screen: No local model is installed on this machine.',
    'the unavailable line carries the route reason and nothing else');
  assert.equal(await page.locator('#library-screen-result ul').count(), 0, 'no entries are fabricated');
  assert.equal(await page.getByTestId('library-screen-dropped').count(), 0, 'no dropped count is invented');
  const visibleDocs = items.filter(i => i.kind === 'pdf').map(i => i.id);
  assert.deepEqual(sent.screen.at(-1), { project_id: 'p1', item_ids: visibleDocs }, 'the screen posts the visible document ids');

  // --- (10) A returned screen renders its entries and counts dropped items honestly.
  await page.getByTestId('library-screen').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="library-screen-entries"] li').length === 2);
  assert.match(await page.locator('#library-screen-result').innerText(), /Three documents screened\./);
  assert.equal(await page.getByTestId('library-screen-dropped').innerText(),
    '2 items could not be verified and were dropped from this screen.', 'unverified_dropped is shown honestly');
  assert.equal(await page.getByTestId('library-screen-message').count(), 0, 'the unavailable line is gone once a screen returns');

  // --- (11) Drawers are available on demand and their collapsed state persists.
  await page.locator('#quiet-projects').click();
  assert.equal(await page.locator('#project-list').isVisible(), true, 'Projects opens the real reorderable list');
  await page.getByTestId('toggle-projects').click();
  await page.waitForFunction(() => document.querySelector('main').classList.contains('wb-collapse-projects'));
  assert.equal(await page.getByTestId('toggle-projects').getAttribute('aria-expanded'), 'false');
  await page.locator('#project-list').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#project-list').isVisible(), false, 'the collapsed panel content is hidden');
  assert.equal(await page.getByTestId('library-view').isVisible(), true, 'the working surface stays');
  await page.locator('#quiet-secretary').click();
  await page.getByTestId('toggle-secretary').click();
  await page.waitForFunction(() => document.querySelector('main').classList.contains('wb-collapse-secretary'));
  assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem('wb.panels'))),
    { projects: false, secretary: false }, 'both collapsed states persist');
  await page.reload();
  await page.waitForFunction(() => document.querySelector('main').classList.contains('wb-collapse-projects'));
  assert.equal(await page.getByTestId('toggle-projects').getAttribute('aria-expanded'), 'false', 'the collapsed state survives a reload');
  assert.equal(await page.locator('#project-list').isVisible(), false, 'the collapsed projects panel stays hidden after reload');
  // A reload returns to the start console, so re-enter the workspace the way the
  // researcher would before touching the panel controls inside it.
  await page.getByTestId('home-menu-research').click();
  await page.waitForFunction(() => S.project === 'p1');
  assert.equal(await page.locator('#quiet-projects').getAttribute('aria-expanded'), 'false');
  assert.equal(await page.locator('#quiet-secretary').getAttribute('aria-expanded'), 'false');

  // --- (12) Expand remains temporary and never rewrites drawer preferences.
  await page.evaluate(() => openLibraryView());
  await page.waitForFunction(() => document.querySelector('main').classList.contains('wb-expand-surface') === false);
  const storedPanels = await page.evaluate(() => localStorage.getItem('wb.panels'));
  const beforeWidth = (await page.getByTestId('library-view').boundingBox()).width;
  await page.getByTestId('expand-surface').click();
  await page.waitForFunction(() => document.querySelector('main').classList.contains('wb-expand-surface'));
  assert.equal(await page.getByTestId('expand-surface').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('main > nav').isVisible(), false, 'the projects panel steps out of the way');
  assert.equal(await page.locator('main > aside').isVisible(), false, 'the secretary panel steps out of the way');
  assert.ok((await page.getByTestId('library-view').boundingBox()).width >= beforeWidth - 1, 'Expand does not shrink the working surface');
  assert.equal(await page.getByTestId('library-view').isVisible(), true, 'the surface itself stays mounted');
  assert.equal(await page.evaluate(() => localStorage.getItem('wb.panels')), storedPanels, 'temporary Expand leaves saved drawer preferences alone');
  await page.getByTestId('expand-surface').click();
  await page.waitForFunction(() => !document.querySelector('main').classList.contains('wb-expand-surface'));
  assert.equal(await page.getByTestId('expand-surface').getAttribute('aria-pressed'), 'false');
  assert.ok(Math.abs((await page.getByTestId('library-view').boundingBox()).width - beforeWidth) < 2, 'the prior working surface is restored');
  assert.equal(await page.evaluate(() => localStorage.getItem('wb.panels')), storedPanels);

  // --- (13) The Secretary scratch pane writes through the same notes route.
  await page.evaluate(() => showResearchView());
  await page.locator('#quiet-secretary').click();
  assert.equal(await page.getByTestId('quick-notes').isVisible(), true,
    'the scratch pane sits below the conversation and stays visible in the research view');
  assert.ok(await page.getByTestId('quick-notes').evaluate(el => !!el.closest('aside')),
    'the scratch pane lives in the Secretary panel');
  assert.ok(await page.getByTestId('quick-notes').evaluate(el =>
    el.getBoundingClientRect().top >= document.getElementById('secretary-log').getBoundingClientRect().top),
    'the scratch pane sits below the conversation log');
  await page.getByTestId('quick-note-input').fill('Remember the approval definition.');
  await page.getByTestId('quick-note-save').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="quick-note-status"]').textContent.includes('Saved'));
  assert.deepEqual(sent.notes.at(-1), { project_id: 'p1', folder_id: null, text: 'Remember the approval definition.' },
    'the scratch note uses the library notes route with no folder');
  assert.match(await page.locator('#quick-notes').innerText(), /grouped by the day you wrote them/,
    'the pane says where notes go');

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('LIBRARY_UI_PASS: default research view, library tree, folder create/rename (double-click and toolbar), drag-drop move with drop highlight, note day grouping, PDF viewer URL, search hits and snippets, honest unavailable screen, collapsible panels persisting, expandable surface, scratch notes');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
