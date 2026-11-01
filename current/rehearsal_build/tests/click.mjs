// Real browser clicks (lite §10.1 rule 6). Usage: node tests/click.mjs phase1|phase2
// phase1: register -> ask -> clarification (3 branches) -> "I don't know" -> evidence card -> defer with reason -> shadow
// phase2 (after a backend restart): briefing shows the reason with zero model calls -> new question -> delete -> verified
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

import { writeFileSync } from "fs";

const phase = process.argv[2];
const APP_URL = "http://localhost:5173/?api=http://localhost:8123";
const REASON = "Job Start Date is the first permit issuance date, not construction start";
const fail = (m) => { console.log("CLICK_FAIL " + m); process.exit(1); };

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {});
const ctx = await browser.newContext();
const page = await ctx.newPage();
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
page.on("requestfailed", (r) => problems.push("requestfailed " + r.url()));
await ctx.addInitScript(() => {
  window.__phase = true;
  // The cartridge start screen now gates the workspace. Seeding the remembered
  // field (the app's own wb.pack key) makes it expand straight into the app, so
  // these clicks test the workbench, not the first-run picker.
  try { window.localStorage.setItem("wb.pack", "social_science"); } catch (_) {}
});

if (phase === "phase1") {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByTestId("register-nyc").click();
  await page.getByText("NYC permit study", { exact: false }).first().waitFor();
  // Capture the project id phase1 registered+opened so phase2 can open exactly
  // this row even when repeated runs leave multiple same-named "NYC permit
  // study" rows (registerProject opens the created project: frontend/app.js
  // registerProject -> openProject(p.project_id) -> localStorage 'wb.project'
  // {id, name}; project ids are uuid-suffixed: app/store.py new_id). Backend
  // lists projects oldest-first (app/api.py projects: ORDER BY created_at, no
  // tiebreak on second-granularity app/store.py now()), so .first()/.last()
  // position heuristics are NOT stable -> carry the id explicitly.
  const pid = await page.evaluate(() => {
    try { const p = JSON.parse(localStorage.getItem("wb.project") || "null") || {}; return p.id || null; }
    catch (_) { return null; }
  });
  if (!pid) fail("phase1: no project id in localStorage wb.project after register");
  writeFileSync(new URL("phase1-project.json", import.meta.url), JSON.stringify({ project_id: pid }) + "\n");
  console.log("phase1 project:", pid);
  await page.getByTestId("question").fill("Can I study how long it takes from filing to the start of construction?");
  await page.getByTestId("ask").click();
  await page.getByTestId("branches").waitFor({ timeout: 20000 });
  const branches = await page.getByTestId("branches").locator("li").count();
  if (branches !== 3) fail(`expected 3 branches, got ${branches}`);
  console.log("clarification:", await page.getByTestId("clarification").locator("h3").innerText(), `| branches=${branches}`);
  await page.getByTestId("resp-unknown").click();
  await page.getByTestId("evidence").waitFor({ timeout: 20000 });
  const ev = await page.getByTestId("evidence").innerText();
  if (!ev.includes("initial permit was issued") || !ev.includes("row 30")) fail("evidence card missing definition or location");
  console.log("evidence:", ev.replace(/\s+/g, " ").slice(0, 160));
  await page.getByTestId("reason").fill(REASON);
  await page.getByTestId("defer").click();
  await page.getByText("defer recorded", { exact: false }).waitFor();
  await page.getByTestId("shadow-run").click();
  await page.getByTestId("shadow").getByText("Basic Muse", { exact: false }).waitFor();
  console.log("shadow:", (await page.getByTestId("shadow").innerText()).replace(/\s+/g, " ").slice(0, 160));
} else if (phase === "phase2") {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByTestId("register-nyc").waitFor();
  // The startup gate delays app init until the cartridge display expands, so the
  // project list is still empty right after the shell appears. Wait for the rows
  // to be rendered before counting them.
  await page.getByTestId("open-nyc").first().waitFor({ timeout: 20000 });
  // Open the exact project phase1 wrote the defer reason to. Identity is the
  // project id captured in phase1 (tests/phase1-project.json), written from
  // localStorage 'wb.project' after register. Fall back to the pre-existing
  // .first() behavior only if the identity file is absent (fresh/standalone
  // phase2 run, not the two-phase flow).
  let target = null;
  try {
    target = JSON.parse(require("fs").readFileSync(new URL("phase1-project.json", import.meta.url), "utf8")).project_id || null;
  } catch (_) { target = null; }
  if (target) {
    // Rows carry only kind-based testids (open-<kind>); disambiguate by click
    // handler identity (openProject(p.id, p.name) closes over the row's id:
    // frontend/app.js loadProjects). Click the row whose handler opens target.
    const n = await page.getByTestId("open-nyc").count();
    let opened = false;
    for (let i = 0; i < n && !opened; i++) {
      await page.getByTestId("open-nyc").nth(i).click();
      const cur = await page.evaluate(() => {
        try { const p = JSON.parse(localStorage.getItem("wb.project") || "null") || {}; return p.id || null; }
        catch (_) { return null; }
      });
      if (cur === target) opened = true;
    }
    if (!opened) fail("phase2: phase1 project row not found in list");
  } else {
    // a fresh browser context has no stored project: open it from the list, as a returning user would
    await page.getByTestId("open-nyc").first().click();
  }
  await page.getByTestId("briefing").getByText(REASON, { exact: false }).first().waitFor({ timeout: 20000 });
  const b = await page.getByTestId("briefing").innerText();
  if (!b.includes("model calls: 0")) fail("briefing did not report zero model calls");
  console.log("briefing after restart:", b.replace(/\s+/g, " ").slice(0, 200));
  await page.getByTestId("question").fill("What does Issuance Date record in the BIS data?");
  await page.getByTestId("ask").click();
  await page.getByTestId("result").waitFor({ timeout: 20000 });
  await page.getByTestId("delete").click();
  await page.getByTestId("delete-confirm-yes").waitFor({ timeout: 20000 });
  await page.getByTestId("delete-confirm-yes").click();
  await page.getByText("Deleted from the app's stored records. Forensic erasure of database files is not performed.").waitFor({ timeout: 20000 });
  const b2 = await page.getByTestId("briefing").innerText();
  if (b2.includes("What does Issuance Date record")) fail("deleted question still in briefing");
  console.log("delete: verified; briefing no longer mentions it");
} else fail("usage: phase1|phase2");

const cors = problems.filter((p) => /CORS|requestfailed/i.test(p));
console.log(`console/network problems: ${problems.length} (CORS/network: ${cors.length})`, problems.slice(0, 3));
if (cors.length) fail("CORS or network errors");
await browser.close();
console.log("CLICK_PASS " + phase);
process.exit(0);
