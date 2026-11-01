// Real-browser foreign-Origin regression for the one local API Origin policy
// (app/api.py `origin_rejected`). The in-process companion is
// tests/test_origin_policy.py; it pins the rule but never drives a browser.
//
// What this file actually does, and what it does not:
//   * a real Chromium page is served from its OWN origin (attacker), on a
//     with its site relation reported explicitly, and it sends
//     the shapes such a page can send with no preflight: a text/plain POST fetch,
//     a text/plain <form> POST, a CORS-mode GET of a model route, and a no-cors
//     GET of a plain read;
//   * rejection is judged from the SERVER: a witness middleware wrapped around
//     the real app (test-side launch script only, no product change) records the
//     Origin header and the response status each request produced;
//   * the page's own view of those responses is opaque (status 0, or a thrown
//     TypeError), which is exactly why CORS unreadability is NOT treated as proof
//     of rejection here; the proof is the recorded 403 plus an unchanged database;
//   * the workbench origin still works: the real frontend, served from the
//     accepted origin, runs its own endSession() and the session-only question
//     it targets is deleted.
//
// Honest limit, asserted rather than hidden: a no-cors GET carries no Origin
// header, so the policy lets it through by design and this file records that.
//
// Scope of the simulation: two loopback origins reached through different names.
// The accepted workbench origin is 127.0.0.1; the attacker page is served from
// 127.0.0.2 when the host routes 127.0.0.0/8 to loopback, else from `localhost`,
// which the browser also treats as a different site. It is still a local
// simulation: not a public-internet attacker, and it says nothing about Host or
// DNS rebinding or about other names that resolve to the loopback address.
//
// Isolation: fresh temporary HOME, database and data directory; the copied
// synthetic `fx` sample; no provider keys, no FAKE_BASE_URL, no paid call. The
// Playwright browser keeps the real HOME (it needs its downloaded browser).
// Every process and server this file starts is stopped in `finally`.
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, readFile, rm, cp } from 'node:fs/promises';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const execute = promisify(execFile);
const root = path.resolve(import.meta.dirname, '..');
const frontend = path.join(root, 'frontend');
const python = path.join(root, '.venv/bin/python');
const SECRET = 'PRIVATE-SESSION-ONLY-ORIGIN-4417: does a foreign page see this?';
const REMEMBERED = 'REMEMBERED-ORIGIN-CONTROL-5521: must survive a session end';

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

// Whole-database row dump from a copy of the SQLite files, so "nothing was
// written" is measured against the store, not against an HTTP response. The copy
// is taken while the backend is idle: db plus its -wal, and the -shm is left out
// so the copy rebuilds its own index on open.
const SNAPSHOT_PY = `
import json, os, shutil, sqlite3, sys, tempfile
source, work = sys.argv[1], sys.argv[2]
os.makedirs(work, exist_ok=True)
copy = os.path.join(work, "snapshot.db")
shutil.copyfile(source, copy)
if os.path.exists(source + "-wal"):
    shutil.copyfile(source + "-wal", copy + "-wal")
conn = sqlite3.connect(copy)
conn.row_factory = sqlite3.Row
names = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
out = {}
for table in names:
    rows = conn.execute('SELECT * FROM "' + table + '"').fetchall()
    out[table] = sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in rows)
conn.close()
print(json.dumps(out, sort_keys=True))
`;

// The test-side launch script: real create_app(), ORIGINS replaced exactly as
// the packaged build does it, plus a witness that records Origin and status.
const LAUNCH_PY = `
import json, os
from pathlib import Path
import uvicorn
import app.api as api

LOG = os.environ["ORIGIN_WITNESS"]
api.ORIGINS[:] = [os.environ["WEB_ORIGIN"]]
app = api.create_app(db_path=os.environ["WB_DB"], data_dir=Path(os.environ["WB_DATA"]))

async def witness(scope, receive, send):
    if scope["type"] != "http":
        return await app(scope, receive, send)
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    seen = {"status": None}
    async def send2(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
        await send(message)
    try:
        await app(scope, receive, send2)
    finally:
        with open(LOG, "a") as stream:
            stream.write(json.dumps({"method": scope["method"], "path": scope["path"],
                                     "origin": headers.get("origin"),
                                     "content_type": headers.get("content-type"),
                                     "sec_fetch_site": headers.get("sec-fetch-site"),
                                     "sec_fetch_mode": headers.get("sec-fetch-mode"),
                                     "status": seen["status"]}) + "\\n")

uvicorn.run(witness, host="127.0.0.1", port=int(os.environ["API_PORT"]), log_level="info")
`;

// The attacker page: served from a second loopback origin, no CORS headers.
const ATTACK_HTML = `<!doctype html>
<meta charset="utf-8">
<title>Synthetic foreign page</title>
<h1>Synthetic foreign page</h1>
<p>This page lives on a different origin than the workbench API.</p>
<iframe name="sink" id="sink" hidden></iframe>
<script>
const TARGET = "__TARGET__";
window.__attack = { simplePost: null, formPost: null, modelRead: null, plainRead: null };
(async () => {
  // 1. The classic simple request: text/plain, no preflight, no readable reply.
  try {
    const r = await fetch(TARGET + '/session/end', {
      method: 'POST', mode: 'no-cors', credentials: 'omit',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' }, body: 'ignore-this-body',
    });
    window.__attack.simplePost = { type: r.type, status: r.status, ok: r.ok };
  } catch (error) { window.__attack.simplePost = { error: String(error) }; }
  // 2. A cross-origin HTML form POST: no JavaScript fetch involved at all.
  await new Promise(resolve => {
    const sink = document.getElementById('sink');
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = TARGET + '/session/end';
    form.enctype = 'text/plain';
    form.target = 'sink';
    const field = document.createElement('input');
    field.name = 'reason'; field.value = 'foreign-form';
    form.appendChild(field);
    document.body.appendChild(form);
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    sink.addEventListener('load', finish, { once: true });
    setTimeout(finish, 5000);
    form.submit();
  });
  window.__attack.formPost = { submitted: true };
  // 3. A CORS-mode GET of a route that runs a model. CORS mode is what makes the
  // browser attach Origin on a GET; the reply is unreadable to a foreign origin,
  // which is the point: the page cannot read it even if the server answered.
  try {
    const r = await fetch(TARGET + '/ai/registry', { mode: 'cors', credentials: 'omit' });
    window.__attack.modelRead = { type: r.type, status: r.status, ok: r.ok };
  } catch (error) { window.__attack.modelRead = { error: String(error) }; }
  // 4. A no-cors GET: recorded to show an honest limit. A no-cors GET carries no
  // Origin header at all, so the server cannot tell it from a local client; the
  // page still cannot read the reply from a foreign origin.
  try {
    const r = await fetch(TARGET + '/projects', { mode: 'no-cors', credentials: 'omit' });
    window.__attack.plainRead = { type: r.type, status: r.status, ok: r.ok };
  } catch (error) { window.__attack.plainRead = { error: String(error) }; }
  window.__attackDone = true;
})();
</script>`;

const temp = await mkdtemp(path.join(tmpdir(), 'wb-origin-browser-'));
const tempHome = path.join(temp, 'home');
const tempData = path.join(temp, 'data');
const dbPath = path.join(temp, 'origin.db');
const witnessPath = path.join(temp, 'origin-witness.jsonl');
const snapshotPy = path.join(temp, 'snapshot.py');
await mkdir(tempHome, { recursive: true });
await mkdir(tempData, { recursive: true });
await cp(path.join(root, 'data/fx'), path.join(tempData, 'fx'), { recursive: true });
await writeFile(snapshotPy, SNAPSHOT_PY);

const apiPort = await freePort();
const apiUrl = `http://127.0.0.1:${apiPort}`;
let backend = null;
let backendLog = '';
let frontendServer = null;
let attackerServer = null;
const ownedPorts = [apiPort];
let browser = null;
let passed = false;

const snapshot = async () => JSON.parse((await execute(python, [snapshotPy, dbPath, path.join(temp, 'snapshot')])).stdout);
const witnessEntries = async () => {
  try {
    return (await readFile(witnessPath, 'utf8')).split('\n').filter(Boolean).map(line => JSON.parse(line));
  } catch { return []; }
};
const settle = (ms) => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(check, label, attempts = 100) {
  for (let i = 0; i < attempts; i++) {
    const value = await check();
    if (value) return value;
    await settle(100);
  }
  throw new Error(`timed out waiting for ${label}`);
}
const listenerOn = async (port) => {
  try { return (await execute('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN'])).stdout.trim(); }
  catch { return ''; }
};

try {
  // --- the accepted workbench origin, serving the real frontend files ---------
  frontendServer = createServer(async (req, res) => {
    const requestPath = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(frontend, requestPath === '/' ? 'index.html' : '.' + requestPath);
    if (file !== path.join(frontend, 'index.html') && !file.startsWith(frontend + path.sep)) {
      res.writeHead(403); res.end(); return;
    }
    try {
      const body = await readFile(file);
      const ext = path.extname(file);
      res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml' })[ext] || 'application/octet-stream');
      res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => frontendServer.listen(0, '127.0.0.1', resolve));
  const webPort = frontendServer.address().port;
  const webUrl = `http://127.0.0.1:${webPort}`;
  ownedPorts.push(webPort);

  // The attacker is served on a different site from the accepted origin: 127.0.0.2
  // when the host routes the whole 127.0.0.0/8 to loopback, else `localhost`. If
  // neither address is usable the page falls back to 127.0.0.1 and this file
  // records same-site instead of claiming a stronger result.
  const attackerHost = await new Promise(async resolve => {
    for (const candidate of ['127.0.0.2', 'localhost']) {
      const usable = await new Promise(done => {
        const probe = createServer((request, response) => { response.writeHead(200); response.end('probe'); });
        probe.once('error', () => done(false));
        probe.listen(0, candidate, () => probe.close(() => done(true)));
      });
      if (usable) return resolve(candidate);
    }
    resolve('127.0.0.1');
  });
  const attackAddress = attackerHost;
  attackerServer = createServer((req, res) => {    const requestPath = new URL(req.url, 'http://localhost').pathname;
    if (requestPath !== '/' && requestPath !== '/attack.html') { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(ATTACK_HTML.replace(/__TARGET__/g, () => apiUrl));
  });
  await new Promise((resolve, reject) => {
    attackerServer.once('error', reject);
    attackerServer.listen(0, attackAddress, resolve);
  });
  const attackPort = attackerServer.address().port;
  const attackOrigin = `http://${attackAddress}:${attackPort}`;
  const expectedSite = attackAddress === '127.0.0.1' ? 'same-site' : 'cross-site';
  ownedPorts.push(attackPort);
  assert.notEqual(attackOrigin, webUrl, 'the attacker page must not share the accepted origin');
  console.log(`FOREIGN_ORIGIN=${attackOrigin} ACCEPTED_ORIGIN=${webUrl} EXPECTED_SEC_FETCH_SITE=${expectedSite}`);

  // --- backend with an isolated home, database and data directory ------------
  const env = { ...process.env, HOME: tempHome, WB_DB: dbPath, WB_DATA: tempData,
    ORIGIN_WITNESS: witnessPath, WEB_ORIGIN: webUrl, API_PORT: String(apiPort),
    WB_ALLOW_REAL_MUSE: '0', PYDANTIC_AI_NO_BANNER: '1',
    NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost' };
  for (const key of ['FAKE_BASE_URL', 'META_API_KEY', 'WB_MODAL_BASE_URL', 'WB_MODAL_PROXY_TOKEN_ID',
    'WB_MODAL_PROXY_TOKEN_SECRET', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY']) delete env[key];
  backend = spawn(python, ['-c', LAUNCH_PY], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  backend.stdout.on('data', data => { backendLog += data; });
  backend.stderr.on('data', data => { backendLog += data; });
  await waitFor(async () => { try { return (await fetch(`${apiUrl}/projects`)).ok; } catch { return false; } },
    'the isolated backend');

  // --- synthetic session-only question, written by a no-Origin local client --
  const postJson = (route, body) => fetch(apiUrl + route, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const project = await (await postJson('/projects', { kind: 'fx' })).json();
  const secretQuestion = await (await postJson(`/projects/${project.project_id}/questions`,
    { text: SECRET, memory: 'session_only' })).json();
  const keptQuestion = await (await postJson(`/projects/${project.project_id}/questions`,
    { text: REMEMBERED, memory: 'remember' })).json();
  const secretId = secretQuestion.question_id, keptId = keptQuestion.question_id;
  assert.ok(secretId && keptId, 'both synthetic questions were created');
  assert.equal(secretQuestion.adapter, 'offline', 'no provider adapter: refinement ran offline, no key, no call');
  const aware = await (await fetch(`${apiUrl}/questions/${secretId}/deletion-preview`)).json();
  assert.equal(aware.question_removed.count, 1, 'the session-only question is a real deletion target');
  const before = await snapshot();
  const ledgerBefore = before.usage_ledger.length;
  const witnessBefore = (await witnessEntries()).length;

  // --- drive the real foreign page ------------------------------------------
  browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const wire = [];
  const attacker = await browser.newPage();
  attacker.on('response', async response => {
    if (!response.url().startsWith(apiUrl)) return;
    wire.push({ url: response.url(), requestMethod: response.request().method(), status: response.status() });
  });
  await attacker.goto(`${attackOrigin}/attack.html`, { waitUntil: 'load' });
  await attacker.waitForFunction(() => window.__attackDone === true, null, { timeout: 30000 });
  const pageView = await attacker.evaluate(() => window.__attack);

  // The page cannot read those replies. That unreadability is exactly why it is
  // not used as the proof: a foreign page sees status 0 / a thrown TypeError even
  // when the server did the work. Rejection is judged from the server below.
  assert.equal(pageView.simplePost.type, 'opaque', 'the foreign POST is a no-cors request from the page');
  assert.equal(pageView.simplePost.status, 0, 'the foreign page sees no status: CORS unreadability, not proof');
  assert.ok(pageView.modelRead.error, 'the CORS-mode foreign read throws in the page, also not proof');
  assert.equal(pageView.plainRead.type, 'opaque', 'and a no-cors GET is opaque as well');

  // Playwright reports the two POST navigations/requests the browser completed, but
  // not the CORS-mode GET once the page's read is blocked, so completeness of the
  // foreign set is judged from the server witness below.
  await waitFor(async () => (await witnessEntries()).slice(witnessBefore)
    .filter(entry => entry.origin === attackOrigin).length === 3, 'the three foreign requests at the server');
  await settle(300);
  const bridged = wire.map(entry => ({ method: entry.requestMethod, path: new URL(entry.url).pathname,
    status: entry.status }));
  const foreignPosts = bridged.filter(entry => entry.method === 'POST' && entry.path === '/session/end');
  assert.equal(foreignPosts.length, 2, 'both foreign POST shapes reached the network: fetch and form');
  for (const entry of foreignPosts) assert.equal(entry.status, 403, 'the server refused the foreign write');

  // --- the server's own record: foreign Origin received, 403 produced ---------
  const server = (await witnessEntries()).slice(witnessBefore);
  const serverForeign = server.filter(entry => entry.origin === attackOrigin);
  assert.equal(serverForeign.length, 3, 'the server received exactly the three foreign requests');
  for (const entry of serverForeign) {
    assert.ok(['/session/end', '/ai/registry'].includes(entry.path), `unexpected foreign path ${entry.path}`);
    assert.equal(entry.status, 403, `the server answered ${entry.status} to a foreign Origin on ${entry.path}`);
    assert.equal(entry.sec_fetch_site, expectedSite, `sec-fetch-site on ${entry.path}`);
  }
  const plainWrites = serverForeign.filter(entry => entry.method === 'POST' && entry.path === '/session/end');
  assert.equal(plainWrites.length, 2, 'fetch and form POSTs both arrived with the foreign Origin');
  assert.ok(plainWrites.every(entry => String(entry.content_type).startsWith('text/plain')),
    'both arrived as text/plain: no preflight could have protected them');
  assert.equal(serverForeign.find(entry => entry.path === '/ai/registry').sec_fetch_mode, 'cors',
    'the model read was a CORS-mode GET, which is what makes the browser send Origin');
  // Honest limit, recorded rather than hidden: a no-cors GET of a plain read route
  // carries no Origin at all, so it is indistinguishable from a local client and
  // the policy lets it through by design. The page still cannot read the reply.
  const plainRead = server.find(entry => entry.method === 'GET' && entry.path === '/projects'
    && entry.sec_fetch_site === expectedSite);
  assert.ok(plainRead, 'the no-cors GET reached the server');
  assert.equal(plainRead.origin, null, 'a no-cors GET sends no Origin header');
  assert.equal(plainRead.status, 200, 'so a plain read stays open, exactly as the pinned policy says');

  // --- database preservation; offline adapter separately prevents paid calls --
  const afterAttack = await snapshot();
  assert.deepEqual(afterAttack, before, 'rejected requests wrote nothing to the database');
  assert.equal(afterAttack.usage_ledger.length, ledgerBefore, 'usage ledger stayed unchanged with the offline adapter');
  const stillStored = afterAttack.questions.find(row => JSON.parse(row).id === secretId);
  assert.ok(stillStored, 'the session-only question survived the foreign page');
  assert.equal(JSON.parse(stillStored).original_text, SECRET, 'and it still holds the private text');
  const preview = await (await fetch(`${apiUrl}/questions/${secretId}/deletion-preview`)).json();
  assert.equal(preview.question_removed.count, 1, 'the question is still a deletion target: nothing was deleted');

  // --- the accepted origin keeps working, through the product's own code ----
  const allowed = await browser.newPage();
  const allowedErrors = [];
  allowed.on('pageerror', error => allowedErrors.push(error.message));
  await allowed.addInitScript(() => { try { localStorage.setItem('wb.pack', 'social_science'); } catch (_) {} });
  await allowed.goto(`${webUrl}/?api=${encodeURIComponent(apiUrl)}`, { waitUntil: 'domcontentloaded' });
  await allowed.waitForFunction(() => typeof endSession === 'function', null, { timeout: 20000 });
  const readable = await allowed.evaluate(async url => (await fetch(`${url}/ai/registry`)).json(), apiUrl);
  assert.ok(readable && readable.settings, 'the accepted origin reads the model route the foreign page could not');
  await allowed.evaluate(() => endSession());
  await allowed.waitForFunction(() => document.getElementById('session-status').textContent.includes('Session ended'),
    null, { timeout: 20000 });
  const sessionStatus = await allowed.locator('#session-status').textContent();
  assert.match(sessionStatus, /"deleted":\s*1/, 'the product end-session deleted exactly the session-only question');
  assert.deepEqual(allowedErrors, [], 'the accepted-origin page ran the product code without a page error');

  const afterAllowed = await snapshot();
  assert.equal(afterAllowed.questions.some(row => JSON.parse(row).id === secretId), false,
    'the accepted origin really deleted the session-only question');
  assert.ok(afterAllowed.questions.some(row => JSON.parse(row).id === keptId),
    'the remembered question is kept');
  assert.equal(afterAllowed.usage_ledger.length, ledgerBefore, 'ending the session preserved the offline usage ledger');
  const allowedServer = (await witnessEntries()).slice(witnessBefore)
    .filter(entry => entry.origin === webUrl && entry.path === '/session/end' && entry.method === 'POST');
  assert.ok(allowedServer.length >= 1, 'the accepted Origin reached the server on the same route');
  assert.ok(allowedServer.every(entry => entry.status === 200),
    'and every accepted-Origin POST /session/end succeeded');

  passed = true;
  console.log(JSON.stringify({
    result: 'PASS',
    regression: expectedSite === 'cross-site' ? 'CROSS_SITE_ORIGIN_BROWSER_PASS' : 'SAME_SITE_FOREIGN_ORIGIN_BROWSER_PASS',
    simulation: `local loopback only: accepted ${webUrl}, foreign ${attackOrigin}, `
      + `browser site relation ${expectedSite}; not a public-internet or Host/DNS-rebinding test`,
    page_view_of_foreign_requests: pageView,
    browser_observed: bridged,
    server_witness_foreign: serverForeign,
    server_witness_allowed_end: allowedServer,
    database: { rows_unchanged_during_attack: true, usage_ledger_before: ledgerBefore,
      usage_ledger_after_attack: afterAttack.usage_ledger.length,
      session_only_deleted_only_by_accepted_origin: true },
    end_session_status_line: sessionStatus,
    isolation: { home: tempHome, database: dbPath, data: tempData,
      provider_keys_removed: true, fake_base_url: false, adapter: secretQuestion.adapter },
    ports: ownedPorts,
  }));
  // Teardown and its proof are printed by the finally block below, which runs
  // after this summary: a CLEANUP_OK line means no listener and no browser
  // survived, and CLEANUP_FAIL/KEPT_TEMP name anything that did.
} finally {
  if (browser) { try { await browser.close(); } catch {} }
  const browserGone = !browser || browser.isConnected() === false;
  for (const server of [frontendServer, attackerServer]) {
    if (server) { try { await new Promise(resolve => server.close(resolve)); } catch {} }
  }
  const stop = async (child) => {
    if (!child || child.exitCode !== null) return;
    child.kill('SIGTERM');
    const exited = await Promise.race([new Promise(resolve => child.once('exit', () => resolve(true))), settle(8000).then(() => false)]);
    if (!exited) { try { child.kill('SIGKILL'); } catch {} }
  };
  await stop(backend);
  const stillListening = [];
  for (const port of ownedPorts) {
    const proof = await listenerOn(port);
    if (proof) stillListening.push({ port, proof });
  }
  const backendGone = !backend || backend.exitCode !== null || backend.signalCode !== null;
  if (stillListening.length || !backendGone || !browserGone) {
    console.error('CLEANUP_FAIL listeners remain: ' + JSON.stringify(stillListening)
      + ` backendGone=${backendGone} browserGone=${browserGone}`);
    passed = false;
  } else {
    console.error(`CLEANUP_OK no listener on ${ownedPorts.join(', ')}; browser closed; `
      + `backend gone (exitCode=${backend.exitCode}, signal=${backend.signalCode})`);
  }
  if (backendLog) await writeFile(path.join(temp, 'backend.log'), backendLog);
  if (passed) await rm(temp, { recursive: true, force: true });
  else console.error(`KEPT_TEMP: ${temp}`);
  if (!passed) process.exitCode = 1;
}
