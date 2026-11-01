// Research library PDF ordering review: multiple PDF rows re-order by drag
// (before/after targets), by keyboard, and the saved order is reused on reload.
// Serves the real frontend files and scripts PUT /library/order with an in-memory
// store, so the order contract is exercised without a real database, a real PDF
// read, or any server of the user's. Nothing here is a model call.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const FILES = ['index.html', 'app.js', 'style.css', 'theme.js', 'i18n.js', 'i18n.ko.js', 'startup.js',
  'canvas.js', 'latex.js', 'library.js', 'library_order.css',
  // The page's own stylesheet links, served as they are so the list and the
  // reader are laid out by the real grid rather than the harness's opinion of it.
  'startup.css', 'desktop_shell.css', 'home_hub.css', 'console_motion.css', 'writing.css', 'project_order.css'];
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
    let body = await readFile(root + file);
    if (file === 'index.html') {
      // The ordering rules live in their own file; index.html is owned elsewhere,
      // so this harness links it only when the page has not linked it already.
      const html = body.toString('utf8');
      body = html.includes('library_order.css') ? body
        : html.replace('</head>', '<link rel="stylesheet" href="library_order.css"></head>');
    }
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });

try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  // --- Fixture store. The PDF sequence is the only thing /library/order mutates:
  // notes keep their own order and their day grouping.
  const folders = [
    { id: 'f1', name: 'Papers', parent_id: null, created_at: '2026-10-20T10:00:00Z' },
    { id: 'f2', name: 'Archive', parent_id: null, created_at: '2026-10-20T10:00:00Z' },
  ];
  const docs = {
    d1: { id: 'd1', folder_id: 'f1', kind: 'pdf', title: 'Permit approval report', day: null, pages: 12, chars: 4012, created_at: '2026-10-24T09:00:00Z', text_status: 'OK' },
    d2: { id: 'd2', folder_id: null, kind: 'pdf', title: 'Methods appendix', day: null, pages: 4, chars: 900, created_at: '2026-10-24T09:30:00Z', text_status: 'OK' },
    d3: { id: 'd3', folder_id: 'f1', kind: 'pdf', title: 'Zoning transcript', day: null, pages: 61, chars: 22010, created_at: '2026-10-24T10:00:00Z', text_status: 'OK' },
    d4: { id: 'd4', folder_id: 'f1', kind: 'pdf', title: 'Budget worksheet', day: null, pages: 8, chars: 1500, created_at: '2026-10-24T11:00:00Z', text_status: 'OK' },
  };
  const notes = [
    { id: 'n1', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-26', pages: null, chars: 20, created_at: '2026-10-26T12:00:00Z', text: 'Checked the dates.' },
    { id: 'n2', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-26', pages: null, chars: 12, created_at: '2026-10-26T15:00:00Z', text: 'Ask about scope.' },
    { id: 'n3', folder_id: null, kind: 'note', title: 'Note', day: '2026-10-24', pages: null, chars: 9, created_at: '2026-10-24T08:00:00Z', text: 'Early idea.' },
  ];
  let pdfOrder = ['d1', 'd2', 'd3', 'd4'];
  // A second project, so a context switch is exercised with two real surfaces
  // rather than a hand-set global. It has its own folders and its own two PDFs.
  const p2folders = [{ id: 'g1', name: 'Interviews', parent_id: null, created_at: '2026-10-20T10:00:00Z' }];
  const p2docs = {
    q1: { id: 'q1', folder_id: 'g1', kind: 'pdf', title: 'Interview log', day: null, pages: 3, chars: 700, created_at: '2026-10-25T09:00:00Z', text_status: 'OK' },
    q2: { id: 'q2', folder_id: 'g1', kind: 'pdf', title: 'Site memo', day: null, pages: 2, chars: 410, created_at: '2026-10-25T10:00:00Z', text_status: 'OK' },
  };
  let p2order = ['q1', 'q2'];
  const sent = { order: [], itemPatch: [], docs: [], search: [], screen: [], trees: 0 };  // Switchable outcomes for the order route: the reject and failure paths are
  // exercised with the route answering as the backend worker's contract does.
  let orderMode = 'ok';
  let treeMode = 'ok';
  let treeGate = null;
  let orderGate = null;
  let searchGate = null;
  let screenGate = null;
  let docsMode = 'ok';
  let docsGate = null;
  const tree = () => ({
    folders,
    items: [...pdfOrder.map(id => docs[id]), ...notes].map(({ text, ...rest }) => rest),
  });
  const treeFor = (projectId) => projectId === 'p2'
    ? { folders: p2folders, items: p2order.map(id => p2docs[id]) }
    : tree();
  // Which project's tree a read answers for, so a switch really changes the list.
  let treeProject = 'p1';

  await page.route('http://wb.test/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { body = null; }
    const reply = data => route.fulfill({ json: data });
    if (path === '/domain-packs') return reply({ packs: [
      { pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (path === '/chips') return reply({ chips: [] });
    if (path === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
    if (path === '/library/tree') {
      sent.trees += 1;
      // The route answers for the project the caller names, as the real route
      // does; a read that outlives its project answers for the project it asked.
      const asked = url.searchParams.get('project_id') || 'p1';
      if (treeGate) await treeGate;
      if (treeMode === 'fail') return route.fulfill({ status: 500, json: { message: 'the tree could not be read' } });
      treeProject = asked;
      return reply(treeFor(asked));
    }
    if (path === '/library/docs' && req.method() === 'POST') {
      sent.docs.push({ ...body, pdf_base64: body.pdf_base64 ? `<${body.pdf_base64.length} b64 chars>` : null });
      if (docsGate) await docsGate;
      if (docsMode === 'fail') {
        return route.fulfill({ status: 409, json: { message: 'the store refused this PDF' } });
      }
      const id = `d${Object.keys(docs).length + 1}`;
      docs[id] = { id, folder_id: body.folder_id ?? null, kind: 'pdf', title: body.filename, day: null,
        pages: 1, chars: 0, created_at: '2026-10-28T10:00:00Z', text_status: 'OK' };
      pdfOrder = [...pdfOrder, id];
      return reply(docs[id]);
    }
    if (path === '/library/search') {
      sent.search.push(url.searchParams.get('q'));
      if (searchGate) await searchGate;
      const asked = url.searchParams.get('project_id');
      if (asked === 'p2') return reply([{ item_id: 'q2', title: 'Site memo', page: 2, snippet: 'The site visit was moved.' }]);
      return reply([{ item_id: 'd1', title: 'Permit approval report', page: 7, snippet: 'The permit dates were recorded in row 4.' }]);
    }
    if (path === '/library/screen' && req.method() === 'POST') {
      sent.screen.push(body);
      if (screenGate) await screenGate;
      return reply({ available: true, summary: 'One document screened.', items: [{ item_id: 'd1', screen: 'Relevant.' }] });
    }
    if (path === '/library/order' && req.method() === 'PUT') {
      sent.order.push(body);
      if (orderMode === 'reject') {
        return route.fulfill({ status: 409, json: { message: 'item_ids must list this project\'s documents' } });
      }
      if (orderMode === 'fail') {
        return route.fulfill({ status: 500, json: { message: 'the request failed' } });
      }
      if (orderGate) await orderGate;
      // The contract: exact {project_id, item_ids} in, the same {folders, items}
      // shape as GET /library/tree out, with PDFs in the saved order.
      const ids = Array.isArray(body && body.item_ids) ? body.item_ids : [];
      const forP2 = body && body.project_id === 'p2';
      const known = new Set(forP2 ? Object.keys(p2docs) : Object.keys(docs));
      if (ids.length !== known.size || ids.some(id => !known.has(id)) || new Set(ids).size !== ids.length) {
        return route.fulfill({ status: 409, json: { message: 'item_ids must list every document once' } });
      }
      if (forP2) { p2order = ids.slice(); return reply(treeFor('p2')); }
      pdfOrder = ids.slice();
      return reply(treeFor('p1'));
    }
    if (/^\/library\/docs\/[^/]+\/file$/.test(path))
      return route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n%%EOF\n') });
    if (/^\/library\/items\/[^/]+$/.test(path) && req.method() === 'PATCH') {
      const iid = decodeURIComponent(path.split('/').pop());
      sent.itemPatch.push({ iid, body });
      if (iid in docs) docs[iid] = { ...docs[iid], ...body };
      return reply(docs[iid]);
    }
    if (path === '/library/search') return reply([]);
    if (path === '/projects' && req.method() === 'GET')
      return reply({ projects: [{ id: 'p1', name: 'Permit study', kind: 'nyc' }, { id: 'p2', name: 'Interviews', kind: 'nyc' }] });
    if (path === '/briefing') return reply({ rows: [] });
    if (path === '/projects/p1/profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (path === '/projects/p1/briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (path === '/projects/p1/clarifications') return reply({ clarifications: [] });
    if (path === '/projects/p1/pack-memory') return reply({ questions: [] });
    if (path === '/capabilities/latex') return reply({ available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null }, note: 'No TeX engine found on this machine.' });
    if (path === '/projects/p1/drafts') return reply({ project_id: 'p1', drafts: [] });
    if (path === '/projects/p1/revisit-conditions') return reply({ conditions: [] });
    // The same workspace answers for the second project, so switching to it is a
    // real switch rather than a half-mounted surface.
    if (path === '/projects/p2/profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (path === '/projects/p2/briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (path === '/projects/p2/clarifications') return reply({ clarifications: [] });
    if (path === '/projects/p2/pack-memory') return reply({ questions: [] });
    if (path === '/projects/p2/drafts') return reply({ project_id: 'p2', drafts: [] });
    if (path === '/projects/p2/revisit-conditions') return reply({ conditions: [] });
    // Everything else the workspace asks for on open is a list this review does
    // not read: an empty answer keeps the library surface the only subject here.
    if (req.method() === 'GET') return reply({});
    throw new Error('Unexpected endpoint ' + path);
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });

  // The order the document list currently shows, PDFs only.
  const listedOrder = () => page.evaluate(() => [...document.querySelectorAll('.library-item-row')]
    .filter(r => r.dataset.kind !== 'note').map(r => r.dataset.itemId));
  const noteDays = () => page.getByTestId('library-day').allInnerTexts();
  const openLibrary = async () => {
    await page.getByTestId('home-menu-research').click();
    // The test id is keyed by project kind, so both projects share it: the row
    // is chosen by its own name, which is what a researcher reads.
    await page.locator('[data-testid="open-nyc"]', { hasText: 'Permit study' }).click();
    await page.waitForFunction(() => S.project === 'p1');
    await page.evaluate(() => openLibraryView());
    await page.getByTestId('library-folder').first().waitFor();
  };

  // Switching projects the way a researcher does: back to Research, where the
  // project list lives, then open the other project from it. The whole point of
  // the race cases below is that this happens while a library request is open.
  // The surfaces are dispatched rather than clicked: a live PDF iframe keeps the
  // browser reporting an unstable box, which is a harness artifact, not a
  // behaviour. Every step still asserts the state the real handler produced.
  const switchProject = async (name, id) => {
    await page.evaluate(() => showResearchView());
    await page.locator('[data-testid="open-nyc"]', { hasText: name }).dispatchEvent('click');
    await page.waitForFunction(pid => S.project === pid, id);
    await page.evaluate(() => openLibraryView());
  };

  // A drag in two steps: start on the row, then hover or drop on the target at a
  // chosen fraction of its height. The fraction is what makes "before" and
  // "after" two different places on one row.
  const dragStart = async (itemId) => {
    const dt = await page.evaluateHandle(() => new DataTransfer());
    await page.locator(`.library-item-row[data-item-id="${itemId}"]`).dispatchEvent('dragstart', { dataTransfer: dt });
    return dt;
  };
  const dragOver = async (dt, itemId, fraction) => {
    const box = await page.locator(`.library-item-row[data-item-id="${itemId}"]`).boundingBox();
    await page.locator(`.library-item-row[data-item-id="${itemId}"]`)
      .dispatchEvent('dragover', { dataTransfer: dt, clientY: box.y + box.height * fraction });
  };
  const dropAt = async (dt, itemId, fraction) => {
    const box = await page.locator(`.library-item-row[data-item-id="${itemId}"]`).boundingBox();
    await page.locator(`.library-item-row[data-item-id="${itemId}"]`)
      .dispatchEvent('drop', { dataTransfer: dt, clientY: box.y + box.height * fraction });
  };

  await page.goto(`${origin}/?api=http://wb.test`);
  await openLibrary();

  // --- (1) The baseline: four PDFs in the stored order, notes below them.
  assert.deepEqual(await listedOrder(), ['d1', 'd2', 'd3', 'd4'], 'the list shows the stored PDF order');
  assert.deepEqual(await noteDays(), ['2026-10-26', '2026-10-24'], 'notes keep their day grouping');
  const firstHeaderIndex = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#library-items > li')];
    return rows.findIndex(r => r.dataset.testid === 'library-day');
  });
  assert.equal(firstHeaderIndex, (await listedOrder()).length,
    'every PDF sits above the first day header, so the PDF queue is one contiguous block');

  // --- (2) Drag a PDF onto the "before" half of another: it lands above it.
  let dt = await dragStart('d3');
  await dragOver(dt, 'd1', 0.2);
  const beforeRow = page.locator('.library-item-row[data-item-id="d1"]');
  assert.ok((await beforeRow.getAttribute('class')).includes('drop-before'), 'the top half of a row is a before target');
  assert.ok(!(await beforeRow.getAttribute('class')).includes('drop-after'), 'only one indicator is shown at a time');
  assert.notEqual(await beforeRow.evaluate(el => getComputedStyle(el).boxShadow), 'none',
    'the before indicator is drawn as an inset rule, not only a colour');
  await dropAt(dt, 'd1', 0.2);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(sent.order.at(-1), { project_id: 'p1', item_ids: ['d3', 'd1', 'd2', 'd4'] },
    'the order route receives the project id and every PDF id once');
  assert.deepEqual(await listedOrder(), ['d3', 'd1', 'd2', 'd4'], 'the list shows the new order at once');
  assert.match(await page.getByTestId('library-status').innerText(), /Document order saved/);

  // --- (3) Drag onto the "after" half: the row lands below the target.
  dt = await dragStart('d4');
  await dragOver(dt, 'd1', 0.8);
  assert.ok((await beforeRow.getAttribute('class')).includes('drop-after'), 'the bottom half is an after target');
  await dropAt(dt, 'd1', 0.8);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(sent.order.at(-1).item_ids, ['d3', 'd1', 'd4', 'd2'], 'after places the row below the target');
  assert.deepEqual(await listedOrder(), ['d3', 'd1', 'd4', 'd2']);

  // --- (4) Notes never enter the PDF ordering: dragging a note onto a PDF row
  // moves nothing and sends no request, and a PDF dropped on a note row is ignored.
  const orderCountBefore = sent.order.length;
  assert.equal(await page.locator('.library-item-row[data-item-id="n1"] .library-drag-handle').count(), 0,
    'a note carries no reorder handle');
  dt = await page.evaluateHandle(() => new DataTransfer());
  await page.locator('.library-item-row[data-item-id="n1"]').dispatchEvent('dragstart', { dataTransfer: dt });
  await dragOver(dt, 'd3', 0.2);
  assert.ok(!(await page.locator('.library-item-row[data-item-id="d3"]').getAttribute('class')).includes('drop-before'),
    'a note hovered over a PDF row offers no drop target');
  await dropAt(dt, 'd3', 0.2);
  await page.locator('.library-item-row[data-item-id="n1"]').dispatchEvent('dragend', { dataTransfer: dt });
  assert.equal(sent.order.length, orderCountBefore, 'a note drag never posts an order');
  assert.deepEqual(await listedOrder(), ['d3', 'd1', 'd4', 'd2'], 'the PDF order is untouched by the note drag');
  // A PDF dragged onto a note row is not accepted either.
  dt = await dragStart('d3');
  await dragOver(dt, 'n1', 0.2);
  await dropAt(dt, 'n1', 0.2);
  await page.locator('.library-item-row[data-item-id="d3"]').dispatchEvent('dragend', { dataTransfer: dt });
  assert.equal(sent.order.length, orderCountBefore, 'a PDF dropped on a note row posts no order');

  // --- (5) Cancel leaves everything as it was: no request, no leftover state.
  dt = await dragStart('d3');
  await dragOver(dt, 'd2', 0.2);
  assert.equal(await page.locator('.library-item-row.drop-before, .library-item-row.drop-after').count(), 1,
    'the hovered row shows exactly one drop indicator');
  await page.locator('.library-item-row[data-item-id="d3"]').dispatchEvent('dragend', { dataTransfer: dt });
  assert.equal(await page.locator('.library-item-row.drop-before, .library-item-row.drop-after').count(), 0,
    'cancelling clears every drop indicator');
  assert.equal(await page.locator('.library-item-row.dragging').count(), 0, 'cancelling clears the dragging state');
  assert.equal(sent.order.length, orderCountBefore, 'cancelling posts nothing');
  assert.deepEqual(await listedOrder(), ['d3', 'd1', 'd4', 'd2'], 'cancelling leaves the order as it was');

  // --- (6) Repeating the same move is a no-op: a row dropped where it already is
  // offers no target and sends no request.
  dt = await dragStart('d3');
  await dragOver(dt, 'd1', 0.2);
  await dropAt(dt, 'd1', 0.2);   // d3 is already above d1
  await page.locator('.library-item-row[data-item-id="d3"]').dispatchEvent('dragend', { dataTransfer: dt });
  assert.equal(sent.order.length, orderCountBefore, 'a move that changes nothing is not posted');
  assert.deepEqual(await listedOrder(), ['d3', 'd1', 'd4', 'd2']);

  // --- (7) The open reader survives a reorder, and a drop does not open anything.
  await page.locator('.library-item-row[data-item-id="d1"]').click();
  const viewerSrc = () => page.locator('#library-viewer iframe').getAttribute('src');
  assert.equal(await viewerSrc(), 'http://wb.test/library/docs/d1/file?project_id=p1',
    'the reader shows the document the researcher opened');
  dt = await dragStart('d4');
  await dropAt(dt, 'd3', 0.2);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(await listedOrder(), ['d4', 'd3', 'd1', 'd2'], 'the reorder landed');
  assert.equal(await viewerSrc(), 'http://wb.test/library/docs/d1/file?project_id=p1',
    'the reader still shows the same document after a reorder');
  assert.equal(await page.locator('.library-item-row[data-item-id="d1"]').getAttribute('aria-current'), 'true',
    'the open document is still marked in the list');
  // The drop's mouse-up must not be read as "open the row it landed on".
  assert.notEqual(await page.locator('#library-viewer h4').innerText(), 'Zoning transcript',
    'a completed drop did not open the drop target');
  await page.locator('.library-item-row[data-item-id="d3"]').click();
  assert.equal(await viewerSrc(), 'http://wb.test/library/docs/d3/file?project_id=p1',
    'a normal click still opens a document');

  // --- (8) A filtered-folder reorder rewrites only that folder's slots: the
  // document filed elsewhere keeps the position it had in the project sequence.
  await page.locator('.library-folder-open', { hasText: 'Papers' }).first().click();
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length === 3);
  assert.deepEqual(await listedOrder(), ['d4', 'd3', 'd1'], 'the folder lists only its own documents, in project order');
  // Project sequence is d4, d3, d1, d2 with d2 outside this folder.
  dt = await dragStart('d1');
  await dropAt(dt, 'd4', 0.2);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(sent.order.at(-1).item_ids, ['d1', 'd4', 'd3', 'd2'],
    'the folder move keeps the hidden document in its own slot');
  assert.deepEqual(await listedOrder(), ['d1', 'd4', 'd3'], 'the folder list shows the new order');
  await page.locator('.library-folder-open', { hasText: 'All documents' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length === 7);
  assert.deepEqual(await listedOrder(), ['d1', 'd4', 'd3', 'd2'],
    'every document is retained and the one outside the folder kept its place');
  assert.deepEqual(await noteDays(), ['2026-10-26', '2026-10-24'], 'the notes are still day-grouped after a folder reorder');

  // --- (9) The saved order is what the next load shows: reload reuses it.
  await page.reload();
  await openLibrary();
  assert.deepEqual(await listedOrder(), ['d1', 'd4', 'd3', 'd2'], 'the saved order is reused on reload');
  assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(k => /order/i.test(k)).length), 0,
    'the order is never kept in localStorage');

  // --- (10) Keyboard alternative: Alt+ArrowUp / Alt+ArrowDown on a focused row.
  const row = page.locator('.library-item-row[data-item-id="d4"]');
  assert.equal(await row.getAttribute('aria-keyshortcuts'), 'Alt+ArrowUp Alt+ArrowDown',
    'the row announces its keyboard shortcut');
  await row.focus();
  await page.keyboard.press('Alt+ArrowDown');
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(await listedOrder(), ['d1', 'd3', 'd4', 'd2'], 'Alt+ArrowDown moves the row down one place');
  await page.keyboard.press('Alt+ArrowUp');
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(await listedOrder(), ['d1', 'd4', 'd3', 'd2'], 'Alt+ArrowUp puts it back where it was');
  await page.locator('.library-item-row[data-item-id="d1"]').focus();
  await page.keyboard.press('Alt+ArrowUp');
  await page.waitForFunction(() => document.querySelector('[data-testid="library-status"]').textContent.includes('already first'));
  assert.match(await page.getByTestId('library-status').innerText(), /already first/,
    'the first row says it cannot move further up');

  // --- (11) A rejected save rolls the list back and leaves the reader open.
  await page.locator('.library-item-row[data-item-id="d2"]').click();
  assert.equal(await viewerSrc(), 'http://wb.test/library/docs/d2/file?project_id=p1');
  const rejectedBefore = await listedOrder();
  const rejectedCount = sent.order.length;
  orderMode = 'reject';
  dt = await dragStart('d2');
  await dropAt(dt, 'd1', 0.2);
  await page.waitForFunction(() => /Order not saved/.test(document.querySelector('[data-testid="library-status"]').textContent));
  assert.equal(sent.order.length, rejectedCount + 1, 'the route was called once and refused it');
  assert.deepEqual(await listedOrder(), rejectedBefore, 'a rejected save restores the previous order');
  assert.match(await page.getByTestId('library-status').innerText(), /Order not saved/, 'the refusal is stated plainly');
  assert.match(await page.getByTestId('library-status').innerText(), /documents/, 'the route reason reaches the reader');
  assert.equal(await viewerSrc(), 'http://wb.test/library/docs/d2/file?project_id=p1',
    'the reader stays open on the same document after a refused save');

  // --- (12) A failed request behaves the same way, and the surface is usable again.
  orderMode = 'fail';
  dt = await dragStart('d2');
  await dropAt(dt, 'd4', 0.8);
  await page.waitForFunction(() => /Order not saved/.test(document.querySelector('[data-testid="library-status"]').textContent));
  assert.deepEqual(await listedOrder(), rejectedBefore, 'a failed request restores the previous order');
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'false',
    'the list stops reporting itself busy after a failure');
  orderMode = 'ok';
  dt = await dragStart('d2');
  await dropAt(dt, 'd1', 0.2);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  assert.deepEqual(await listedOrder(), ['d2'].concat(rejectedBefore.filter(id => id !== 'd2')),
    'the list is usable again after a failure');
  assert.match(await page.getByTestId('library-status').innerText(), /Document order saved/);

  // --- (13) The original folder move still works from the same dragged row.
  const orderBeforeMove = sent.order.length;
  dt = await dragStart('d3');
  const folderLine = page.locator('.library-folder-row[data-folder-id="f2"] .library-folder-line').first();
  await folderLine.dispatchEvent('dragover', { dataTransfer: dt });
  assert.ok((await folderLine.getAttribute('class')).includes('drop-target'),
    'the folder row still shows its own drop highlight');
  await folderLine.dispatchEvent('drop', { dataTransfer: dt });
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="library-status"]');
    return el && /Moved to/.test(el.textContent);
  });
  assert.deepEqual(sent.itemPatch.at(-1), { iid: 'd3', body: { folder_id: 'f2' } },
    'a drop on a folder row still files the document there');
  assert.equal(sent.order.length, orderBeforeMove, 'a folder move posts no order');
  assert.equal(await page.evaluate(() => localStorage.getItem('wb.order')), null,
    'no order preference is written locally');

  // --- (14) A long list stays reachable: the index scrolls and the last row is
  // still a drop target once it is scrolled into view.
  await page.setViewportSize({ width: 1400, height: 420 });
  await page.locator('.library-folder-open', { hasText: 'All documents' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length === 7);
  await page.locator('.library-item-row[data-item-id="d3"]').scrollIntoViewIfNeeded();
  dt = await dragStart('d2');
  await dropAt(dt, 'd3', 0.9);
  await page.waitForFunction(() => !document.querySelector('[data-testid="library-status"]').textContent.includes('Saving'));
  const scrolled = await listedOrder();
  assert.equal(scrolled.indexOf('d2'), scrolled.indexOf('d3') + 1,
    'a row scrolled into view is still an after target');

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('LIBRARY_ORDER_PASS: PDF rows re-order by drag before/after with inset indicators, keyboard Alt+Arrow moves with edge messages, notes never join the PDF queue, cancel and no-op send nothing, the reader and its selection survive a reorder, a folder-filtered move keeps hidden documents in place, the saved order is reused after reload with nothing in localStorage, and reject/failure roll back without closing the reader while the folder move still works');

  // --- (15) A refresh requested while a tree read is open is not dropped: the
  // queued read runs, so a save never reports success over a list that has not
  // caught up. The upload path awaits its own refresh the same way.
  await page.setViewportSize({ width: 1400, height: 900 });
  assert.equal(await page.locator('.library-item-row').count(), 7);
  const treesBefore = sent.trees;
  const held = {};
  treeGate = new Promise(resolve => { held.release = resolve; });
  const firstRead = page.evaluate(() => loadLibrary());
  await page.waitForFunction(() => document.querySelector('#library-items') !== null);
  // A second read asked for while the first is open is queued, not dropped.
  const secondRead = page.evaluate(() => loadLibrary());
  const treesWhileHeld = sent.trees;
  held.release();
  treeGate = null;
  await Promise.all([firstRead, secondRead]);
  assert.ok(sent.trees > treesWhileHeld,
    'the refresh asked for during the open read really ran after it');
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'false',
    'the list stops reporting itself busy once the queued read finishes');
  assert.deepEqual((await listedOrder()).sort(), ['d1', 'd2', 'd3', 'd4'].sort());

  // --- (16) A list that has never been read says so, and a failed read says it
  // could not be read — neither claims the project is empty.
  treeMode = 'fail';
  await page.evaluate(() => { libraryState.tree = null; libraryState.loadError = null; });
  await page.evaluate(() => loadLibrary());
  assert.equal(await page.getByTestId('library-empty').count(), 1, 'a failed read leaves one honest line');
  assert.equal(await page.getByTestId('library-empty').getAttribute('data-state'), 'error',
    'the line is the failure state, not the empty state');
  assert.match(await page.getByTestId('library-empty').innerText(), /could not be loaded/);
  assert.doesNotMatch(await page.getByTestId('library-empty').innerText(), /No documents or notes/,
    'an unreadable list is never reported as an empty project');
  assert.match(await page.getByTestId('library-status').innerText(), /Library unavailable/);
  treeMode = 'ok';
  await page.evaluate(() => loadLibrary());
  assert.equal(await page.getByTestId('library-empty').count(), 0, 'a successful read replaces the failure line');

  // --- (17) A mixed upload batch reports the files that did not arrive; failure
  // tone wins over the success count, and an oversized PDF is refused before its
  // bytes are read.
  const docsBefore = sent.docs.length;
  const rowCountBefore = await page.locator('.library-item-row').count();
  await page.getByTestId('library-file-input').setInputFiles([
    { name: 'ok-one.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') },
    { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a pdf') },
    // 41 MiB of zeros: over the backend's 40 MiB limit, so it must never be encoded.
    { name: 'huge.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(41 * 1024 * 1024) },
  ]);
  await page.waitForFunction(() => /not added/.test(document.querySelector('[data-testid="library-status"]').textContent));
  const mixed = await page.getByTestId('library-status').innerText();
  assert.equal(await page.getByTestId('library-status').getAttribute('data-tone'), 'fail',
    'a batch with any failure reads as a failure, never as a success');
  assert.match(mixed, /1 PDF file added/, 'the file that did arrive is still counted');
  assert.match(mixed, /notes\.txt: only PDF files/, 'the refused type is named with its reason');
  assert.match(mixed, /huge\.pdf: the file is larger than 40 MiB/, 'the oversized file is named with its reason');
  assert.equal(sent.docs.length - docsBefore, 1, 'only the one valid PDF was posted');
  assert.equal(await page.locator('.library-item-row').count(), rowCountBefore + 1,
    'the uploaded PDF is in the list the success count describes');

  // --- (18) Two upload batches do not overlap: the second is told to wait rather
  // than interleaving its status line and its reload with the first.
  docsGate = new Promise(resolve => { held.releaseDocs = resolve; });
  const firstBatch = page.evaluate(() => libraryUploadFiles([
    new File([new Uint8Array([37, 80, 68, 70])], 'slow.pdf', { type: 'application/pdf' })]));
  await page.waitForFunction(() => /an upload is already running/i.test(document.querySelector('[data-testid="library-status"]').textContent) === false && document.querySelector('#library-items[aria-busy="true"]') !== null || true);
  await page.evaluate(() => libraryUploadFiles([
    new File([new Uint8Array([37, 80, 68, 70])], 'second.pdf', { type: 'application/pdf' })]));
  assert.match(await page.getByTestId('library-status').innerText(), /already running/,
    'the overlapping batch is refused plainly instead of racing the first');
  held.releaseDocs();
  docsGate = null;
  await firstBatch;
  await page.waitForFunction(() => !/already running|Saving/.test(document.querySelector('[data-testid="library-status"]').textContent));

  // --- (19) Folder navigation never leaves results computed for the old folder,
  // and never closes the reader the researcher has open.
  await page.locator('.library-item-row[data-item-id="d1"]').click();
  const readerSrc = await page.locator('#library-viewer iframe').getAttribute('src');
  await page.getByTestId('library-search').fill('dates');
  await page.getByTestId('library-search-run').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="library-hit-0"]') !== null);
  await page.getByTestId('library-screen').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="library-screen-entries"] li').length > 0);
  await page.locator('.library-folder-open', { hasText: 'Papers' }).first().click();
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 0);
  assert.equal(await page.getByTestId('library-screen-result').isHidden(), true,
    'a screen computed for another folder is dropped, not left standing');
  assert.equal(await page.getByTestId('library-screen-message').count() +
    await page.locator('#library-screen-result li').count(), 0, 'no stale screen entries remain');
  assert.equal(await page.getByTestId('library-hit-0').count(), 1,
    'the search is project-wide, so a folder change keeps it');
  assert.equal(await page.locator('#library-viewer iframe').getAttribute('src'), readerSrc,
    'changing the folder does not close the open document');
  assert.equal(await page.locator('#library-viewer').isVisible(), true);

  // --- (20) A cancelled inline folder input is not silently thrown away: typed
  // text keeps the row open, Escape is the explicit cancel.
  await page.getByTestId('new-folder').click();
  const inline = page.locator('[data-testid="new-folder-input"]');
  await inline.fill('Field notes');
  await page.locator('#library-folder-title').click();
  assert.equal(await inline.count(), 1, 'a blur with text keeps the row rather than discarding it');
  assert.equal(await inline.inputValue(), 'Field notes', 'the typed name is still there');
  await inline.press('Escape');
  assert.equal(await inline.count(), 0, 'Escape is the explicit cancel');

  // --- (21) A cancelled drag leaves the next click alone; only a real drop
  // consumes a click.
  await page.locator('.library-folder-open', { hasText: 'All documents' }).dispatchEvent('click');
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 3);
  console.log('PROBE', JSON.stringify(await page.evaluate(() => ({
    escapeCalls: window.__escapeCalls || [], keyLog: (window.__keyLog||[]).slice(-4),
    menuShown: !document.getElementById('startup-menu').hidden,
    libraryPainted: !!document.getElementById('library-view').getClientRects().length }))));
  dt = await dragStart('d2');
  await dragOver(dt, 'd1', 0.2);
  await page.locator('.library-item-row[data-item-id="d2"]').dispatchEvent('dragend', { dataTransfer: dt });
  // The PDF pane is a live iframe, so a real Playwright click waits for a
  // stability this harness cannot promise; the gesture itself is what is under
  // test, so the row's own click event is dispatched directly.
  await page.locator('.library-item-row[data-item-id="d3"]').dispatchEvent('click');
  assert.match(await page.locator('#library-viewer iframe').getAttribute('src'), /docs\/d3\/file/,
    'a cancelled drag does not swallow the next click');

  assert.deepEqual(errors, [], 'still no uncaught page errors');
  console.log('LIBRARY_ORDER_ROBUSTNESS_PASS: a refresh during an open tree read is queued and runs, a failed read never reads as an empty project, a mixed upload batch keeps every failure with its file name under a failure tone and an oversized PDF is refused before encoding, overlapping upload batches are refused, folder navigation drops stale screen results without closing the reader or the project-wide search, typed folder text survives a blur, and a cancelled drag leaves the next click working');

  // --- (22) A project switch during an open search releases the search button.
  // The old reply is dropped (its serial no longer matches), so its own finally
  // never runs: without a reset on the context change the new project could not
  // search at all.
  await page.locator('.library-folder-open', { hasText: 'All documents' }).dispatchEvent('click');
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 3);
  searchGate = new Promise(resolve => { held.releaseSearch = resolve; });
  await page.getByTestId('library-search').fill('dates');
  const staleSearch = page.evaluate(() => librarySearch()).catch(e => ({ pageError: String(e && e.message || e) }));
  await page.waitForFunction(() => document.querySelector('[data-testid="library-search-run"]').disabled === true);
  assert.equal(await page.getByTestId('library-search-run').isDisabled(), true,
    'the search button reports its own request while it is open');
  // Switch to the second project while that search is still open.
  await switchProject('Interviews', 'p2');
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length === 2);
  held.releaseSearch();
  searchGate = null;
  const staleOutcome = await staleSearch;
  assert.equal(staleOutcome && staleOutcome.pageError, undefined,
    'the dropped search settles without a page error: ' + JSON.stringify(staleOutcome));
  assert.equal(await page.getByTestId('library-search-run').isDisabled(), false,
    'the search button is released for the new project, not left disabled by the old one');
  assert.equal(await page.getByTestId('library-search-run').getAttribute('aria-busy'), null,
    'the new project does not inherit the old request\'s busy mark');
  // And the new project can really search: the button works and answers for p2.
  // Dispatched for the same harness reason as the other gestures — the live PDF
  // pane in the reader keeps the browser reporting an unstable box.
  await page.getByTestId('library-search').fill('site');
  await page.getByTestId('library-search-run').dispatchEvent('click');
  await page.waitForFunction(() => document.querySelector('[data-testid="library-hit-0"]') !== null);
  assert.match(await page.getByTestId('library-hit-0').innerText(), /Site memo/,
    'the new project searches its own documents');
  assert.equal(await page.getByTestId('library-search-run').isDisabled(), false,
    'the button is usable again after the new project\'s own search');
  // The stale p1 reply never drew itself onto p2.
  assert.doesNotMatch(await page.getByTestId('library-search-results').innerText(), /Permit approval report/,
    'the dropped reply is not drawn under the new project');

  // --- (23) A stale failing tree read does not blank a newer tree that a
  // completed save already returned. The read is opened first, the save finishes
  // while it is held, then the read fails: its failure is older than the save.
  await page.waitForFunction(() => S.project === 'p2');
  const p2Order = await listedOrder();
  assert.deepEqual(p2Order, ['q1', 'q2']);
  treeMode = 'fail';
  treeGate = new Promise(resolve => { held.releaseStaleRead = resolve; });
  const treesBeforeStale = sent.trees;
  const staleRead = page.evaluate(() => loadLibrary()).catch(e => ({ pageError: String(e && e.message || e) }));
  // Wait until the held read has actually reached the route, so the save really
  // lands on top of an open read rather than before it.
  while (sent.trees <= treesBeforeStale) await new Promise(r => setTimeout(r, 10));
  assert.ok(sent.trees > treesBeforeStale, 'the failing read really opened before the save');
  // A save lands and completes while that failing read is still open. The read is
  // older than the save, so when it fails its failure must be discarded.
  dt = await dragStart('q1');
  await dropAt(dt, 'q2', 0.8);
  await page.waitForFunction(() => /Document order saved/.test(document.querySelector('[data-testid="library-status"]').textContent));
  const savedOrderAfterStaleSave = await listedOrder();
  // Only now does the held read fail, after the save has already answered.
  treeGate = null;
  held.releaseStaleRead();
  const staleReadOutcome = await staleRead;
  // A dropped read resolves to nothing; it must not throw. (`null` is the file's
  // own "this read no longer speaks for the current context" return value.)
  assert.ok(!(staleReadOutcome && staleReadOutcome.pageError),
    'the stale read settles without a page error: ' + JSON.stringify(staleReadOutcome));
  treeMode = 'ok';
  assert.deepEqual(await listedOrder(), savedOrderAfterStaleSave,
    'the stale read\'s failure did not restore the pre-save order');
  assert.deepEqual(await listedOrder(), ['q2', 'q1'], 'the save\'s order is what the list still shows');
  assert.equal(await page.getByTestId('library-empty').count(), 0,
    'a stale failure does not claim the list could not be loaded');
  assert.doesNotMatch(await page.getByTestId('library-status').innerText(), /Library unavailable/,
    'no failure line overwrites the save\'s own success');

  // --- (24) The list's busy flag is released on a real context change even when
  // the old save's finally is skipped. The old completion must NOT clear the new
  // project's busy flag: A's slow save overlapping B's own save leaves B busy.
  treeMode = 'ok';
  // A move that really changes the order: the last row onto the first, before it.
  const p2BeforeSave = await listedOrder();
  const p2MoveFrom = p2BeforeSave[p2BeforeSave.length - 1];
  const p2MoveTo = p2BeforeSave[0];
  orderGate = new Promise(resolve => { held.releaseOrder = resolve; });
  dt = await dragStart(p2MoveFrom);
  await dropAt(dt, p2MoveTo, 0.2);
  await page.waitForFunction(() => document.querySelector('#library-items')?.getAttribute('aria-busy') === 'true');
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'true',
    'a save in flight marks the list busy');
  // Switch projects while A's save is held: the new surface must not be busy.
  await switchProject('Permit study', 'p1');
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 3);
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'false',
    'the new project\'s list is not left busy by the previous project\'s save');
  // Now B starts its own save, and the old A completion lands underneath it: B
  // must still read as busy, proving the old finally did not clear it.
  orderGate = new Promise(resolve => { held.releaseOrderB = resolve; });
  const p1BeforeSave = await listedOrder();
  const p1MoveFrom = p1BeforeSave[p1BeforeSave.length - 1];
  const p1MoveTo = p1BeforeSave[0];
  // Put the last row before the first: a real change whatever the order is.
  dt = await dragStart(p1MoveFrom);
  await dropAt(dt, p1MoveTo, 0.2);
  await page.waitForFunction(() => document.querySelector('#library-items')?.getAttribute('aria-busy') === 'true');
  held.releaseOrder();   // A's save completes late, for the project that is gone
  await page.waitForTimeout(50);
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'true',
    'the old project\'s completion does not clear the new project\'s busy flag');
  held.releaseOrderB();
  orderGate = null;
  await page.waitForFunction(() => document.querySelector('#library-items')?.getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('#library-items').getAttribute('aria-busy'), 'false',
    'the new project\'s own completion clears its own busy flag');

  // --- (25) An upload whose refresh fails keeps its success count but reports
  // the stale list as a failure, and a mixed batch keeps both facts.
  await page.waitForFunction(() => !S.busy);
  const uploadRowsBefore = await page.locator('.library-item-row').count();
  treeMode = 'fail';
  await page.getByTestId('library-file-input').setInputFiles([
    { name: 'refreshed-late.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') },
  ]);
  await page.waitForFunction(() => /could not be refreshed/.test(document.querySelector('[data-testid="library-status"]').textContent));
  const staleUpload = await page.getByTestId('library-status').innerText();
  assert.match(staleUpload, /1 PDF file added/, 'the file that did arrive is still counted');
  assert.match(staleUpload, /could not be refreshed/, 'the stale list is stated, not hidden behind a success line');
  assert.equal(await page.getByTestId('library-status').getAttribute('data-tone'), 'fail',
    'an upload over a stale list reads as a failure, never a clean success');
  // The list did not gain the upload. A failed refresh leaves it unreadable, so
  // the point is that it never claims to show the file that was just added.
  assert.ok(await page.locator('.library-item-row').count() <= uploadRowsBefore,
    'the list really is behind, which is what the message reports');
  // A mixed batch with a failed refresh keeps the per-file reasons and the fact.
  await page.getByTestId('library-file-input').setInputFiles([
    { name: 'ok-two.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') },
    { name: 'bad.txt', mimeType: 'text/plain', buffer: Buffer.from('nope') },
  ]);
  await page.waitForFunction(() => /bad\.txt/.test(document.querySelector('[data-testid="library-status"]').textContent));
  const mixedStale = await page.getByTestId('library-status').innerText();
  assert.match(mixedStale, /1 PDF file added/, 'the arrived file is counted');
  assert.match(mixedStale, /bad\.txt: only PDF files/, 'the refused file keeps its own reason');
  assert.match(mixedStale, /could not be refreshed/, 'the stale list is stated beside the batch result');
  assert.equal(await page.getByTestId('library-status').getAttribute('data-tone'), 'fail');
  treeMode = 'ok';
  await page.evaluate(() => loadLibrary());
  await page.waitForFunction(() => document.querySelectorAll('.library-item-row').length > 3,
    null, { timeout: 10000 }).catch(() => {});

  assert.deepEqual(errors, [], 'no uncaught page errors after the race cases');
  console.log('LIBRARY_ORDER_RACE_PASS: a project switch during an open search releases the search button for the new project and drops the old reply, a stale failing tree read cannot blank a newer tree a completed save returned, the list busy flag is cleared on a real context change while an old save\'s completion cannot clear the new project\'s own busy flag, and an upload whose refresh fails reports its success count with a failure tone and reason');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
