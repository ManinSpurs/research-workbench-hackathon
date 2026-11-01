// Secretary turn capture: talking to the secretary records the exchange.
//
// Serves the real frontend files and scripts POST /projects/{pid}/secretary-turns
// with fixtures (no model call, no money). Covered: a question submission posts
// one researcher turn and one secretary turn bound to the project and the created
// question, the question itself is still submitted, a failed turn POST does not
// stop the question, nothing is recorded for empty text or with no project open,
// a repeated capture records only once, and the recorded turns are what the
// review list then shows.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const FILES = ['index.html', 'app.js', 'style.css', 'theme.js', 'i18n.js', 'i18n.ko.js', 'startup.js',
  'startup.css', 'canvas.js', 'latex.js', 'library.js', 'citations.js', 'secretary_review.js'];
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

  // --- Fixture state for the secretary-turn contract.
  const turns = [];          // what the route has recorded, newest last
  const turnPosts = [];      // body of every POST /projects/p1/secretary-turns
  const questionPosts = [];  // body of every POST /projects/p1/questions
  const responsePosts = [];  // body of every POST /clarifications/{id}/responses
  let turnSerial = 0;
  let failTurnPost = false;
  // The turn POSTs are bookkeeping: they leave after the real action settles, so
  // every count assertion waits for them rather than racing a fixed sleep.
  const until = async (test, what) => {
    for (let i = 0; i < 200; i++) {
      if (test()) return;
      await new Promise(r => setTimeout(r, 25));
    }
    throw new Error('timed out waiting for ' + what);
  };
  await page.route('http://wb.test/**', async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { body = null; }
    const reply = data => route.fulfill({ json: data });
    const fail = (status, message) => route.fulfill({ status, json: { error: 'fixture', message } });

    // --- Secretary-turn routes (the contract under test).
    if (path === '/projects/p1/secretary-turns' && req.method() === 'POST') {
      turnPosts.push(body);
      if (failTurnPost) return fail(500, 'fixture turn store unavailable');
      const turn = { id: 'turn-' + (++turnSerial), project_id: 'p1', pack_id: body.pack_id ?? null,
        question_id: body.question_id ?? null, role: body.role, body_json: body.body,
        status: 'recorded', created_at: '2026-10-27T2' + turnSerial + ':00:00Z', model_calls: 0 };
      turns.push(turn);
      return reply({ ...turn, status: 'recorded' });
    }
    if (path === '/projects/p1/secretary-turns' && req.method() === 'GET')
      return reply({ project_id: 'p1', turns: turns.slice().reverse(), model_calls: 0 });

    // --- The rest of the workspace, enough to open a project and talk.
    if (/^\/projects\/[^/]+\/revisit-conditions$/.test(path) && req.method() === 'GET')
      return reply({ conditions: [], verified_at: '2026-10-28T12:00:00Z', model_calls: 0 });
    if (path === '/projects' && req.method() === 'GET')
      return reply({ projects: [{ id: 'p1', name: 'Permit study', kind: 'nyc' }] });
    if (path === '/domain-packs') return reply({ packs: [
      { pack_id: 'social_science', title: 'Social Science', version: '1.0.0' }] });
    if (path === '/projects/p1/questions' && req.method() === 'POST') {
      questionPosts.push(body);
      // adapter 'offline' keeps the run out of this test: the acknowledgement the
      // app drew is the refinement card, which is what must be recorded.
      return reply({ question_id: 'q' + questionPosts.length, adapter: 'offline',
        context: { pack_id: 'social_science', pack_version: '1.0.0' },
        refinement_status: 'ok', working_text: 'Refined: ' + body.text });
    }
    if (/^\/clarifications\/[^/]+\/responses$/.test(path) && req.method() === 'POST') {
      responsePosts.push(body);
      return reply({ state: 'decision_pending', note: 'User statement; not externally verified.', adapter: 'mock' });
    }
    if (path === '/projects/p1/citations') return reply({ citations: [] });
    if (path === '/chips') return reply({ chips: [] });
    if (path === '/chips/social_science') return reply({ pack_id: 'social_science', source: 'builtin' });
    // A created question opens its canvas and its advanced panel; both are
    // answered from the smallest honest shape so no unrelated call fails.
    if (/^\/questions\/[^/]+\/canvas$/.test(path)) return reply({
      question: 'fixture question',
      context: { pack_id: 'social_science', pack_version: '1.0.0', project_id: 'p1' },
      revision: 1, edges: [], notice: 'Researcher-authored draft; unverified.',
      nodes: [{ label: 'Fixture node', variable: null, dataset_id: null, status: 'unmapped' }],
      datasets: [{ dataset_id: 'fixture.csv', columns: [] }] });
    if (path === '/ai/registry') return reply({
      settings: { cloud_enabled: false, budget_usd: 0, key_configured: false,
        baseten_key_configured: false, local_allowance_remaining_usd: 0 },
      providers: [], models: [] });
    if (/^\/questions\/[^/]+\/ai\/(assets|jobs)$/.test(path)) return reply(path.endsWith('/assets') ? { assets: [] } : { jobs: [] });
    if (/^\/questions\/[^/]+\/preparations$/.test(path)) return reply({ jobs: [], cards: [], scope: null });
    if (path === '/briefing') return reply({ rows: [] });
    if (path === '/projects/p1/profile') return reply({ variables: [], computed_by: 'ordinary code, no model call' });
    if (path === '/projects/p1/briefing') return reply({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (path === '/projects/p1/clarifications') return reply({ clarifications: [] });
    if (path === '/projects/p1/pack-memory') return reply({ questions: [] });
    if (path === '/capabilities/latex') return reply({ available: false, engines: { pdflatex: null, xelatex: null, lualatex: null, latexmk: null, tectonic: null }, note: 'No TeX engine found on this machine.' });
    if (path === '/projects/p1/drafts') return reply({ project_id: 'p1', drafts: [] });
    throw new Error('Unexpected endpoint ' + path);
  });
  await page.addInitScript(() => { try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await page.goto(`${origin}/?api=http://wb.test`);
  await page.getByTestId('home-menu-research').click();
  await page.getByTestId('open-nyc').click();
  await page.waitForFunction(() => S.project === 'p1' && !S.busy);

  // --- (1) A question told to the secretary records both sides of the exchange.
  const fill = async (text) => {
    await page.getByTestId('secretary-input').fill(text);
  };
  await fill('Is the approval date the permit date?');
  await page.getByTestId('secretary-send').click();
  await page.waitForFunction(() => !S.busy && document.querySelectorAll('#refinement *').length > 0);
  // The record is bookkeeping after the action, so it lands after the question.
  await until(() => turnPosts.length === 2, 'the two turn POSTs');
  assert.equal(questionPosts.length, 1, 'the question itself is still submitted');
  assert.equal(questionPosts[0].text, 'Is the approval date the permit date?');
  assert.equal(turns.length, 2, 'one researcher turn and one secretary turn');
  assert.deepEqual(turns.map(t => t.role), ['researcher', 'secretary'],
    'the person is recorded first, then the reply');
  assert.equal(turns[0].body_json.text, 'Is the approval date the permit date?',
    "the researcher turn carries the person's own text");
  assert.equal(turns[0].project_id, 'p1', 'the turn is bound to the open project');
  assert.equal(turns[0].pack_id, 'social_science', 'the turn carries the active chip');
  assert.equal(turns[1].project_id, 'p1');
  // The recorded reply is the app's real acknowledgement, not a made-up sentence.
  assert.match(turns[1].body_json.text, /Working question:/,
    'the secretary turn carries the acknowledgement the app actually drew');
  assert.match(turns[1].body_json.text, /Refined: Is the approval date the permit date\?/,
    'the acknowledgement names the working question the server returned');
  assert.equal(turnPosts.length, 2, 'exactly two turn POSTs for one submission');
  assert.deepEqual(Object.keys(turnPosts[0]).sort(), ['body', 'pack_id', 'question_id', 'role'],
    'no field the route would reject is sent');
  // The recorded question_id is the question the server actually created.
  assert.equal(turns[0].question_id, 'q1', 'the researcher turn carries the created question id');
  assert.equal(turns[1].question_id, 'q1', 'the secretary turn carries the created question id');
  assert.equal(turnPosts[0].question_id, 'q1', 'the id recorded is the one the server returned');

  // --- (2) A failed turn POST does not block the real action.
  failTurnPost = true;
  const beforePosts = turnPosts.length, beforeQuestions = questionPosts.length;
  await fill('Second question while the log is down');
  await page.getByTestId('secretary-send').click();
  await page.waitForFunction(() => !S.busy && S.question !== null);
  await until(() => turnPosts.length === beforePosts + 2, 'both turn POSTs to be attempted');
  assert.equal(questionPosts.length, beforeQuestions + 1,
    'the question is submitted even though recording failed');
  assert.equal(questionPosts.at(-1).text, 'Second question while the log is down');
  assert.equal(turnPosts.length, beforePosts + 2, 'both sides were attempted');
  assert.equal(turns.length, 2, 'nothing new reached the log');
  assert.equal(await page.getByTestId('error').innerText(), '',
    'a failed record is not shown as a research error, and never as saved');
  assert.equal(await page.evaluate(() => S.question), 'q2', 'the question really did get created');
  failTurnPost = false;

  // --- (3) Empty text and no open project record nothing.
  const beforeEmpty = turnPosts.length;
  await page.getByTestId('secretary-input').fill('   ');
  await page.getByTestId('secretary-send').click();
  // A negative expectation needs a settle window: nothing may arrive at all.
  await new Promise(r => setTimeout(r, 150));
  assert.equal(turnPosts.length, beforeEmpty, 'whitespace-only text is not an exchange');
  assert.equal(questionPosts.length, beforeQuestions + 1, 'and it submits no question either');
  const noProject = await page.evaluate(async () => {
    const saved = S.project;
    S.project = null;
    const input = document.getElementById('secretary-input');
    input.value = 'No project open';
    const before = S.busy;
    await sendSecretaryTurn({ preventDefault() {} });
    S.project = saved;
    return { before };
  });
  assert.deepEqual(noProject, { before: false });
  await new Promise(r => setTimeout(r, 150));
  assert.equal(turnPosts.length, beforeEmpty, 'no turn is posted while no project is open');

  // --- (4) A repeated capture records only once.
  const beforeGuard = turnPosts.length;
  const guard = await page.evaluate(async () => {
    // The refinement card still holds the last acknowledgement, so both sides of
    // this exchange have text to record: finishing the same capture twice must
    // still yield exactly one researcher turn and one secretary turn.
    const capture = secretaryCaptureBegin('Guarded submission');
    const a = secretaryCaptureFinish(capture);
    const b = secretaryCaptureFinish(capture);
    await Promise.all([a, b]);
    // And the per-side guard holds on its own, independent of finish().
    const again = await secretaryCaptureTurn(capture, 'researcher', 'Guarded submission');
    return { capture, again };
  });
  await until(() => turnPosts.length === beforeGuard + 2, 'the guarded exchange to be recorded');
  await new Promise(r => setTimeout(r, 150));
  assert.equal(turnPosts.length, beforeGuard + 2,
    'the same submission records exactly one researcher turn and one secretary turn');
  assert.deepEqual(turnPosts.slice(beforeGuard).map(p => p.role), ['researcher', 'secretary']);
  assert.equal(guard.again, null, 'the researcher side is never posted twice');
  assert.equal(turnPosts.at(-2).body.text, 'Guarded submission');
  assert.equal(guard.capture.researcherRecorded, true);
  assert.equal(guard.capture.secretaryRecorded, true);
  // The same through the real submit path: a second submit while the first is in
  // flight records nothing extra.
  const beforeDouble = { posts: turnPosts.length, questions: questionPosts.length };
  await page.getByTestId('secretary-input').fill('Double submit');
  await page.evaluate(() => {
    const form = document.getElementById('secretary-compose');
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    form.dispatchEvent(new Event('submit', { cancelable: true }));
  });
  await page.waitForFunction(() => !S.busy && S.question !== null);
  await until(() => turnPosts.length === beforeDouble.posts + 2, 'the double-submit exchange to be recorded');
  await new Promise(r => setTimeout(r, 150));
  assert.equal(questionPosts.length, beforeDouble.questions + 1, 'one question for two submits');
  assert.equal(turnPosts.length, beforeDouble.posts + 2, 'one exchange recorded for two submits');

  // --- (5) Talking in clarification mode records the answer and its reply.
  await page.evaluate(() => { S.cq = 'c1'; S.question = 'q3'; syncComposerMode(); });
  assert.match(await page.getByTestId('secretary-compose-hint').innerText(), /Reply to the clarification/);
  const beforeClar = { posts: turnPosts.length, responses: responsePosts.length };
  await page.getByTestId('secretary-input').fill('The permit date is row 4.');
  await page.getByTestId('secretary-send').click();
  await page.waitForFunction(() => !S.busy);
  await until(() => turnPosts.length === beforeClar.posts + 2, 'the clarification exchange to be recorded');
  await new Promise(r => setTimeout(r, 150));
  assert.equal(responsePosts.length, beforeClar.responses + 1, 'the answer itself is still sent');
  assert.equal(responsePosts.at(-1).value, 'The permit date is row 4.');
  assert.equal(turnPosts.length, beforeClar.posts + 2, 'the answer records both sides');
  const clarTurns = turns.slice(-2);
  assert.deepEqual(clarTurns.map(t => t.role), ['researcher', 'secretary']);
  assert.equal(clarTurns[0].body_json.text, 'The permit date is row 4.');
  assert.equal(clarTurns[0].question_id, 'q3', 'the answer turn belongs to the open question');
  assert.match(clarTurns[1].body_json.text, /decision_pending/,
    "the reply recorded is the state sentence the app actually drew");

  // --- (6) What was recorded is what the review list then shows.
  assert.equal(errors.length, 0, 'no uncaught page errors: ' + errors.join(' | '));
  await page.evaluate(() => openSecretaryReview());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="secretary-review-item"]').length > 0);
  const roles = await page.getByTestId('secretary-review-role').allInnerTexts();
  assert.ok(roles.length >= 2, 'the review list lists the recorded exchanges');
  // Newest first, and the reply is written after the question it answers: the
  // top row is that reply sitting directly above the text that prompted it.
  assert.equal(roles[0], 'Secretary', 'newest row first is the reply that was just recorded');
  assert.equal(roles[1], 'Researcher', 'the words that prompted it sit right below');
  assert.match(await page.getByTestId('secretary-review-text').first().innerText(), /decision_pending|State:/,
    'the listed reply is the acknowledgement the app really drew');
  console.log('SECRETARY_TURN_CAPTURE_PASS: both sides of a composer exchange recorded against the project and question, question still submitted when recording fails, nothing recorded for empty text or no project, repeated capture recorded once, recorded turns listed by the review screen');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
