// A real, interactive preview of the current app, using sample records only.
// Owns its ports, profile and database; never restarts an existing user window.
import { _electron as electron } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, cp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const liveShared = process.env.WB_PREVIEW_MODAL_SHARED === '1';
const workspace = await mkdtemp(path.join(tmpdir(), 'wb-interactive-preview-'));
const data = path.join(workspace, 'data');
await mkdir(data);
await cp(path.join(root, 'data/fx'), path.join(data, 'fx'), { recursive: true });
const output = path.join(root, 'tmp', 'interactive-preview');
await mkdir(output, { recursive: true });
async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}
const fakePort = await freePort(), apiPort = await freePort(), webPort = await freePort();
const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
const env = {
  ...process.env, WB_DB: path.join(workspace, 'preview.db'),
  WB_APP_URL: `${web}/?api=${api}&preview=1`,
  FAKE_BASE_URL: `http://127.0.0.1:${fakePort}/v1`, WB_ALLOW_REAL_MUSE: '0',
  NO_PROXY: 'localhost,127.0.0.1', no_proxy: 'localhost,127.0.0.1',
};
for (const key of ['META_API_KEY', 'WB_MODAL_BASE_URL', 'WB_MODAL_PROXY_TOKEN_ID', 'WB_MODAL_PROXY_TOKEN_SECRET']) delete env[key];
const children = [];
let app;
async function request(route, method = 'GET', body) {
  const response = await fetch(api + route, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  assert.ok(response.ok, `${method} ${route}: ${JSON.stringify(result)}`);
  return result;
}
const source = String.raw`\documentclass{article}
\usepackage{lmodern}
\begin{document}
\section{A sample paper}
This is synthetic preview material, not a research finding.

\subsection{Try the writing tools}
Select these words and insert bold text, a list or an equation.
\[
y = \alpha + \beta x
\]
\end{document}
`;
try {
  for (const [args, cwd] of [
    [['-m', 'app.fake_meta', String(fakePort)], root],
    [['-c', `import app.api as api; from pathlib import Path; import uvicorn; api.ORIGINS.append(${JSON.stringify(web)}); uvicorn.run(api.create_app(data_dir=Path(${JSON.stringify(data)})),host='127.0.0.1',port=${apiPort})`], root],
    [['-m', 'http.server', String(webPort), '--bind', '127.0.0.1'], path.join(root, 'frontend')],
  ]) {
    children.push(spawn(path.join(root, '.venv/bin/python'), args, { cwd, env, stdio: ['ignore', 'inherit', 'inherit'] }));
  }
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      if ((await fetch(api + '/projects')).ok && (await fetch(web)).ok) { ready = true; break; }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, 'The preview servers did not become ready.');
  const first = await request('/projects', 'POST', { kind: 'fx', pack_id: 'social_science' });
  const csv = await readFile(path.join(root, 'data/fx/permits_2023.csv'), 'utf8');
  const dictionary = JSON.parse(await readFile(path.join(root, 'data/fx/sources.json'), 'utf8'));
  for (const name of ['Sample: Reading notes', 'Sample: Second study']) {
    await request('/projects/upload', 'POST', {
      name, csv_text: csv, dictionary: { entries: dictionary.dd2023 }, pack_id: 'social_science',
    });
  }
  const draft = await request(`/projects/${first.project_id}/drafts`, 'POST', { title: 'Sample paper' });
  await request(`/drafts/${draft.draft_id}`, 'PUT', {
    expected_revision: draft.revision, source: 'manual', engine: 'pdflatex',
    files: [{ path: 'main.tex', text: source }, { path: 'references.bib', text: '% Add your own verified bibliography entries here.\n' }],
  });
  const compiled = await request(`/drafts/${draft.draft_id}/compile`, 'POST', { source: 'manual' });
  assert.equal(compiled.status, 'ok', JSON.stringify(compiled));
  const pdfResponse = await fetch(api + compiled.pdf_url);
  assert.ok(pdfResponse.ok);
  const pdf = Buffer.from(await pdfResponse.arrayBuffer());
  assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
  for (const name of ['Sample A.pdf', 'Sample B.pdf', 'Sample C.pdf']) {
    await request('/library/docs', 'POST', {
      project_id: first.project_id, folder_id: null, filename: name, pdf_base64: pdf.toString('base64'),
    });
  }
  const folder = await request('/library/folders', 'POST', { project_id: first.project_id, name: 'Sample folder' });
  await request('/library/notes', 'POST', {
    project_id: first.project_id, folder_id: folder.id, text: 'Sample note: try moving a PDF, opening a paper, or changing the project order.',
  });
  app = await electron.launch({ args: [root, '--user-data-dir=' + path.join(workspace, 'profile')], cwd: root, env });
  const page = await app.firstWindow();
  page.on('pageerror', error => console.error('PREVIEW_PAGE_ERROR', error.message));
  await page.waitForSelector('[data-startup-field="social_science"]', { state: 'attached' });
  await page.evaluate(() => WB_STARTUP.openPack('social_science'));
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForFunction(() => document.querySelectorAll('#home-project option').length >= 3);
  await page.locator('#home-project').selectOption(first.project_id);
  await page.waitForFunction(() => S.project && !S.busy);
  await page.locator('#secretary-mode').selectOption('new');
  await page.locator('#secretary-input').fill('Does approval_date mean final approval?');
  await page.locator('#secretary-send').click();
  await page.waitForFunction(() => !!S.cq && !S.busy);
  await page.locator('[data-menu="research"]').click();
  await page.getByTestId('resp-unknown').click();
  await page.getByTestId('evidence').waitFor();
  let liveResult = null;
  if (liveShared) {
    assert.ok(env.WB_MODAL_SHARED_BASE_URL && env.WB_MODAL_SHARED_MODEL_ID, 'Explicit Shared endpoint and remote model are required.');
    await request('/ai/settings', 'POST', { cloud_enabled: true, budget_usd: 10 });
    await request('/ai/verify-account', 'POST', { provider: 'modal_shared' });
    const question = await page.evaluate(() => S.question);
    const preview = await request(`/questions/${question}/ai/prepare`, 'POST', {
      provider: 'modal_shared', model: 'modal-shared:muse-spark-open',
      harness: false, compare: false, max_output_tokens: 2048,
    });
    assert.equal(preview.provider, 'modal_shared');
    assert.equal(preview.requires_explicit_consent, true);
    const result = await request(`/ai/jobs/${preview.job_id}/execute`, 'POST', {
      preview_hash: preview.preview_hash, consent: true,
    });
    assert.equal(result.usage_complete, true, 'The real app must receive complete usage before claiming success.');
    const recorded = (await request(`/questions/${question}/ai/jobs`)).jobs.find(job => job.id === preview.job_id);
    assert.equal(recorded?.status, 'completed_known', 'The real response and usage must be persisted.');
    liveResult = {
      job_id: preview.job_id, provider: preview.provider,
      calculated_cost_usd: result.calculated_cost_usd, billing_status: result.billing_status,
      grounding_warnings: Object.values(recorded.result.outputs).flatMap(output => output.grounding?.errors || []),
    };
    await page.evaluate(async () => {
      await loadAI(S.question);
      document.getElementById('ai-model').value = 'modal-shared:muse-spark-open';
      document.getElementById('ai-harness').checked = false;
      WB_DECLUTTER_SHELL.openPanel('secretary');
      document.getElementById('ai-panel').open = true;
      document.getElementById('ai-panel').scrollIntoView({ block: 'start' });
    });
    await page.waitForFunction(() => {
      const drawer = document.querySelector('main > aside').getBoundingClientRect();
      return drawer.left >= 0 && drawer.right <= innerWidth + 1;
    });
    await page.locator('#ai-results').scrollIntoViewIfNeeded();
  }
  await page.evaluate(live => {
    document.title = live ? 'Research Workbench - Modal Shared synthetic test' : 'Research Workbench - Larger console test';
    document.getElementById('app-shell').scrollTop = 0;
  }, liveShared);
  await page.screenshot({ path: path.join(output, 'research.png') });
  if (!liveShared) {
    await page.keyboard.press('Escape');
    await page.waitForSelector('#startup-menu:not([hidden])');
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(output, 'home.png'), omitBackground: true });
  }
  await app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0];
    window.show();
    window.focus();
  });
  console.log(JSON.stringify({
    event: 'INTERACTIVE_PREVIEW_READY', web, api, data_directory: workspace,
    project_id: first.project_id, sample_projects: 3, sample_pdfs: 3,
    real_local_pdf_compile: true, live_ai: liveShared, mock_core_fixture: true,
    live_result: liveResult, existing_user_app_untouched: true,
  }));
  // Keep this genuine application open for the user, not an automated test run.
  await new Promise(resolve => app.once('close', resolve));
  app = null;
} finally {
  if (app) await app.close();
  for (const child of children) if (child.exitCode === null) child.kill('SIGTERM');
  await Promise.all(children.map(child => child.exitCode !== null || child.signalCode !== null
    ? Promise.resolve() : new Promise(resolve => child.once('exit', resolve))));
}
