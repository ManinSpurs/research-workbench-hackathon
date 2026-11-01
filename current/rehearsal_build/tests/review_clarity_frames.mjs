// Frames A–E of design/mockups/workbench-clarity, in the real app.
//
// A: every home tile carries one plain purpose line under its own label.
// B–E: each menu's first screen says what the place is for in one sentence, offers
//      exactly ONE primary action, keeps its secondary controls (demoted, not
//      removed) and shows an empty state before the user has done anything.
//
// This runs the real frontend in headless Chromium against scripted HTTP fixtures:
// no Electron, no network, no provider, no model. Synthetic data only.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const frontend = path.resolve(import.meta.dirname, '../frontend');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };

const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = pathname === '/' ? 'index.html' : pathname.slice(1);
  try {
    const body = await readFile(path.join(frontend, file));
    res.setHeader('Content-Type', TYPES[path.extname(file)] || 'application/octet-stream');
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });

// The frame's own words. Each screen is asserted against the sentence the mockup
// wrote, so a paraphrase that loses the meaning fails here.
const FRAME = {
  tile: {
    library: 'Collect and read your sources',
    research: 'Ask questions and check the evidence',
    writing: 'Draft and verify your claims',
    review: 'Keep or remove past conversations'
  },
  purpose: {
    library: 'This is where your sources live.',
    research: 'Ask a question, look at the evidence, then decide.',
    writing: 'Write the draft, then check that its claims match the sources.',
    review: 'Decide what to keep from past conversations with the secretary.'
  },
  primary: {
    library: 'Add PDFs…',
    research: 'New question…',
    writing: 'New paper…',
    review: 'Ask the secretary…'
  },
  empty: {
    library: 'No documents yet',
    research: 'No question yet',
    writing: 'No claims checked yet',
    review: 'No conversations recorded yet'
  }
};

async function routeFixtures(page) {
  await page.route('http://api.test/**', (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    const json = (data) => route.fulfill({ json: data });
    if (pathname === '/domain-packs') return json({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }], default_pack: 'social_science' });
    if (pathname === '/chips') return json({ chips: [] });
    if (pathname === '/chips/social_science') return json({ pack_id: 'social_science', source: 'builtin' });
    if (pathname === '/projects') return json({ projects: [{ id: 'p1', name: 'Synthetic interior study', kind: 'fx', pack_id: null }] });
    if (pathname === '/briefing') return json({ rows: [] });
    if (pathname === '/projects/p1/profile') return json({ variables: [], computed_by: 'ordinary code, no model call' });
    if (pathname === '/projects/p1/briefing') return json({ parts: { where_you_left_off: [], what_changed_since: [], ready_now: [], waiting_on_you: [], suggested_next_action: [] }, model_calls: 0 });
    if (pathname === '/projects/p1/clarifications') return json({ clarifications: [] });
    if (pathname === '/projects/p1/pack-memory') return json({ questions: [] });
    if (pathname === '/projects/p1/revisit-conditions') return json({ conditions: [], model_calls: 0 });
    if (pathname === '/projects/p1/secretary-turns') return json({ turns: [], model_calls: 0 });
    if (pathname === '/projects/p1/drafts') return json({ project_id: 'p1', drafts: [] });
    if (pathname === '/capabilities/latex') return json({ available: false, engines: {}, note: 'Synthetic fixture: no compile requested.' });
    // An EMPTY library: the empty state is a real answer, not a missing fetch.
    if (pathname === '/library/tree') return json({ folders: [], items: [] });
    return json({});
  });
}

async function open({ width, height, motion = 'reduce' }) {
  // `reduce` settles the reveal synchronously, which is what most of these frames
  // want. The one place the real 320 ms window matters asks for no-preference.
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: motion });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await routeFixtures(page);
  await page.addInitScript(() => {
    try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {}
    // The desktop app's preload bridge, stubbed. home_hub.js only offers the two
    // idle buttons when it is present, and those buttons are what follow-up 1 is
    // about. Nothing is sent: these record calls.
    window.WB_DESKTOP = {
      openChip: () => { window.__chipCalls = (window.__chipCalls || []).concat('open'); },
      newChip: () => { window.__chipCalls = (window.__chipCalls || []).concat('new'); }
    };
  });
  await page.goto(`${origin}/?api=http://api.test`);
  await page.waitForSelector('#startup-menu:not([hidden])');
  await page.waitForTimeout(250);
  return { page, errors };
}

// Every surface that is taken off screen by a mode class of its own, and the id a
// frame sample reports when a pointer reaches inside one.
const SURFACE_IDS = ['library-view', 'latex-view', 'secretary-review-view', 'citation-view'];

// Sample every frame of the reveal that follows a tile click, with motion real.
// A frame records which surfaces are painted, which mode classes the section
// carries, whether the workspace is still growing, and where a pointer at the
// centre of the window actually lands. `elementFromPoint` is the honest test: it is
// what a click does, so this is the same question the reviewer asked, in the
// browser's own terms.
//
// The hit test alone would be vacuous here, because the arriving workspace is inert
// and an inert subtree is skipped by elementFromPoint — so it can only ever report
// "nothing inside". It is kept because it is what makes the defect explicit, and
// `calibrateHit` below proves the probe is not blind: the same call must reach
// inside the portal once the reveal has settled.
async function sampleReveal(page, tileTestId) {
  await page.evaluate((surfaces) => {
    window.__frames = [];
    window.__sampling = true;
    const read = () => {
      if (!window.__sampling) return;
      const painted = surfaces.filter((id) => {
        const node = document.getElementById(id);
        return !!node && node.getClientRects().length > 0;
      });
      const section = document.querySelector('#app-shell > main > section');
      const portal = document.getElementById('portal');
      const at = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      let host = at, hitSurface = '';
      while (host) {
        if (host.id && surfaces.includes(host.id)) { hitSurface = host.id; break; }
        host = host.parentElement;
      }
      window.__frames.push({
        painted,
        modes: ['library-mode', 'writing-mode', 'citation-mode', 'secretary-review-mode']
          .filter((m) => section.classList.contains(m)),
        hitSurface,
        hitInWorkspace: !!(at && portal.contains(at)),
        hitTag: at ? at.tagName.toLowerCase() + '#' + (at.id || '') : null,
        inert: portal.inert === true,
        opening: portal.classList.contains('opening'),
        portalHidden: portal.hidden
      });
      requestAnimationFrame(read);
    };
    requestAnimationFrame(read);
  }, SURFACE_IDS);
  await page.getByTestId(tileTestId).click();
  await page.waitForTimeout(700);
  return page.evaluate(() => { window.__sampling = false; return window.__frames; });
}

// Sample every frame of the dissolve that follows returning Home, with motion
// real. This is the mirror image of `sampleReveal`: there the question is whether
// the surface the reader is leaving is still painted, here it is whether the
// surface the reader left is the one that fades. A frame records the portal's own
// paint state and opacity (the fade is an opacity animation on #portal), whether
// the left surface and the bare Research scaffold are painted, which mode classes
// the section still carries, and whether the menu is already drawn underneath.
async function sampleDissolve(page, surfaceId) {
  await page.evaluate((id) => {
    window.__dissolve = [];
    window.__dissolving = true;
    const read = () => {
      if (!window.__dissolving) return;
      const portal = document.getElementById('portal');
      const node = document.getElementById(id);
      const question = document.getElementById('question-entry');
      const section = document.querySelector('#app-shell > main > section');
      window.__dissolve.push({
        portalHidden: portal.hidden === true,
        portalPainted: portal.getClientRects().length > 0,
        portalOpacity: portal.hidden ? null : Number(getComputedStyle(portal).opacity),
        paintedSurface: !!node && node.getClientRects().length > 0,
        paintedQuestion: !!question && question.getClientRects().length > 0,
        modes: ['library-mode', 'writing-mode', 'citation-mode', 'secretary-review-mode']
          .filter((m) => section.classList.contains(m)),
        menuShown: !document.getElementById('startup-menu').hidden
      });
      requestAnimationFrame(read);
    };
    requestAnimationFrame(read);
  }, surfaceId);
  await page.evaluate(() => WB_STARTUP.backToMenu());
  await page.waitForFunction(() => document.getElementById('portal').hidden, null, { timeout: 5000 });
  return page.evaluate(() => { window.__dissolving = false; return window.__dissolve; });
}

// The positive control for the hit test: with the reveal settled, the very same
// elementFromPoint call must land inside the workspace. Without this the "never
// reaches the old surface" assertions could pass because the probe sees nothing at
// all, and would hold to nothing.
async function calibrateHit(page) {
  return page.evaluate(() => {
    const portal = document.getElementById('portal');
    const at = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
    return { inert: portal.inert === true, hitInWorkspace: !!(at && portal.contains(at)),
      tag: at ? at.tagName.toLowerCase() + '#' + (at.id || '') : null };
  });
}

// The workspace, with the one synthetic project open.
async function workspace(page) {
  await page.getByTestId('home-menu-research').click();
  await page.locator('#quiet-projects').click();
  await page.getByTestId('open-fx').click();
  await page.waitForFunction(() => typeof S === 'object' && S.project === 'p1');
  // The project was opened from the projects drawer; close it, or its scrim
  // covers the workspace and nothing below it can be clicked.
  if (await page.evaluate(() => WB_DECLUTTER_SHELL.isPanelOpen('projects')))
    await page.locator('#quiet-projects').click();
  await page.waitForFunction(() => {
    const shell = document.getElementById('app-shell');
    return shell && Math.round(shell.getBoundingClientRect().width) === innerWidth;
  });
}

// A control is "primary" here when it is the single filled accent button on the
// screen. Counting that class is how the frame's core claim is checked: one
// primary next action, everything else demoted.
//
// The fill is asserted, not assumed: macos_surfaces.css is loaded after
// clarity.css and paints every control in a surface on the plain surface fill, so
// a rule that only *asks* for the accent loses to it. The computed background is
// therefore compared with the theme's own --accent token, and the accent-filled
// buttons in the viewport are counted, so a second accent control on the first
// screen fails here rather than in a screenshot nobody diffs.
const onePrimary = (page, name) => page.evaluate((paneName) => {
  const pane = document.querySelector(`[data-clarity-pane="${paneName}"]`);
  if (!pane) return { found: false };
  const primaries = [...pane.querySelectorAll('.clarity-primary')];
  const accent = (() => {
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    const hex = raw.replace('#', '');
    return /^[0-9a-f]{6}$/i.test(hex)
      ? `rgb(${parseInt(hex.slice(0, 2), 16)}, ${parseInt(hex.slice(2, 4), 16)}, ${parseInt(hex.slice(4, 6), 16)})`
      : raw;
  })();
  // Every button whose background is the accent colour and whose box is on
  // screen. A hidden/offscreen navigation drawer is not a second primary:
  // the approved glass Secretary has its own accent Send control.
  const painted = primaries[0] ? getComputedStyle(primaries[0]).backgroundColor : null;
  const accentInViewport = [...document.querySelectorAll('button')].filter((b) => {
    const r = b.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight)) return false;
    if (r.right <= 0 || r.left >= innerWidth || getComputedStyle(b).visibility === 'hidden') return false;
    return getComputedStyle(b).backgroundColor === accent;
  }).map((b) => b.id || b.getAttribute('data-testid') || b.textContent.trim().slice(0, 24));
  return {
    found: true,
    count: primaries.length,
    text: primaries[0] ? primaries[0].textContent.trim() : null,
    visible: primaries[0] ? primaries[0].getClientRects().length > 0 : false,
    // The fill this screen is actually painted with, against the accent token.
    accent,
    painted,
    accentInViewport,
    // The primary action must be a real, enabled control a pointer can reach.
    reachable: primaries[0] ? (() => {
      const r = primaries[0].getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!hit && (hit === primaries[0] || primaries[0].contains(hit));
    })() : false
  };
}, name);

const purposeOf = (page, name) => page.evaluate((paneName) => {
  const pane = document.querySelector(`[data-clarity-pane="${paneName}"]`);
  if (!pane) return null;
  const title = pane.querySelector('.clarity-title');
  const sub = pane.querySelector('.clarity-sub');
  const empty = pane.querySelector('.clarity-empty');
  return {
    title: title ? title.textContent.trim() : null,
    sub: sub ? sub.textContent.trim() : null,
    // The pane is on screen only when the view it belongs to is.
    shown: pane.getClientRects().length > 0,
    emptyHidden: empty ? empty.hidden : null,
    emptyTitle: empty && empty.querySelector('h3') ? empty.querySelector('h3').textContent.trim() : null
  };
}, name);

const noSectionScroll = (page) => page.evaluate(() => {
  const section = document.querySelector('#app-shell > main > section');
  return { over: section.scrollHeight - section.clientHeight, top: section.scrollTop };
});

// Both side panels are drawers on a narrow window. A test that judges reachability
// must first put the drawers away, exactly as the shipped library harness does.
// They slide out over 160ms, so this also waits for the closed drawer to stop
// covering the workspace rather than judging it mid-slide.
const closeDrawers = async (page) => {
  await page.evaluate(() => {
    for (const which of ['projects', 'secretary']) {
      if (window.WB_DECLUTTER_SHELL && WB_DECLUTTER_SHELL.isPanelOpen(which)) WB_DECLUTTER_SHELL.closePanel(which);
    }
  });
  await page.waitForFunction(() => {
    const main = document.querySelector('#app-shell > main');
    return [...main.children].filter((el) => el.tagName === 'NAV' || el.tagName === 'ASIDE')
      .every((panel) => getComputedStyle(panel).visibility === 'hidden');
  }, null, { timeout: 2000 }).catch(() => {});
};

// The product's own first-screen rule (the one review_overview_fit enforces):
// the shell never scrolls as a whole, and the pane's sentence, its one action and
// its empty state are all painted inside the window and not covered by anything.
// This checks reachability of the frame's content. The stricter rule — the section
// itself does not scroll at 720x480 and no text in the pane is clamped — is
// asserted separately, in the 720x480 pass, and not waived here.
const firstScreen = (page, name) => page.evaluate((paneName) => {
  const shell = document.getElementById('app-shell');
  const pane = document.querySelector(`[data-clarity-pane="${paneName}"]`);
  const check = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return { inView: r.height > 0 && r.top >= -1 && r.bottom <= innerHeight + 1,
      clear: !!hit && (hit === el || el.contains(hit)),
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      hit: hit ? `${hit.tagName}.${hit.className}` : null,
      hitBox: hit ? (() => { const q = hit.getBoundingClientRect(); return [Math.round(q.left), Math.round(q.top), Math.round(q.width), Math.round(q.height)]; })() : null };
  };
  return {
    shellOver: shell.scrollHeight - shell.clientHeight,
    title: check(pane && pane.querySelector('.clarity-title')),
    primary: check(pane && pane.querySelector('.clarity-primary')),
    empty: check(pane && pane.querySelector('.clarity-empty'))
  };
}, name);

const assertFirstScreen = async (page, name, tag, where) => {
  const seen = await firstScreen(page, name);
  assert.ok(seen.shellOver <= 1, `${tag}: ${where} never scrolls the whole window (${seen.shellOver}px)`);
  for (const part of ['title', 'primary', 'empty']) {
    assert.ok(seen[part], `${tag}: ${where} draws its ${part}`);
    assert.ok(seen[part].inView, `${tag}: ${where} keeps its ${part} on the first screen`);
    assert.ok(seen[part].clear, `${tag}: ${where} leaves its ${part} uncovered`);
  }
};

try {
  for (const viewport of [{ width: 1280, height: 800 }, { width: 720, height: 480 }]) {
    const tag = `${viewport.width}x${viewport.height}`;
    const { page, errors } = await open(viewport);

    // --- A: one plain purpose line under each home tile -------------------
    const tiles = await page.evaluate(() => [...document.querySelectorAll('#home-menu [data-menu]')].map((tile) => {
      const label = tile.querySelector('.menu-label');
      const purpose = tile.querySelector('.clarity-tile-purpose');
      const box = purpose ? purpose.getBoundingClientRect() : null;
      return {
        key: tile.getAttribute('data-menu'),
        label: label ? label.textContent.trim() : null,
        purpose: purpose ? purpose.textContent.trim() : null,
        // The line is under the label, inside the same button.
        sameButton: !!(purpose && tile.contains(purpose)),
        belowLabel: !!(purpose && label && purpose.getBoundingClientRect().top >= label.getBoundingClientRect().bottom - 1),
        // It must actually be painted, not merely present.
        painted: !!box && box.height > 0 && box.width > 0,
        insideTile: !!(box && (() => {
          const t = tile.getBoundingClientRect();
          return box.top >= t.top - 1 && box.bottom <= t.bottom + 1;
        })())
      };
    }));
    assert.deepEqual(tiles.map((t) => t.key), ['library', 'research', 'writing', 'review'],
      `${tag}: the four menus are unchanged and in their approved order`);
    for (const tile of tiles) {
      assert.equal(tile.purpose, FRAME.tile[tile.key], `${tag}: the ${tile.key} tile carries its plain purpose line`);
      assert.ok(tile.sameButton, `${tag}: the ${tile.key} purpose line belongs to the tile button (one control)`);
      assert.ok(tile.belowLabel, `${tag}: the ${tile.key} purpose line sits under the tile's name`);
      assert.ok(tile.painted, `${tag}: the ${tile.key} purpose line is painted at this size`);
      assert.ok(tile.insideTile, `${tag}: the ${tile.key} purpose line stays inside its tile`);
    }
    // The home layout is preserved: four tiles, one gear, one secretary panel.
    assert.equal(await page.locator('#home-menu [data-menu]').count(), 4, `${tag}: four tiles`);
    assert.equal(await page.locator('#home-settings').count(), 1, `${tag}: one settings gear`);
    assert.ok(await page.locator('#home-secretary').isVisible(), `${tag}: the secretary panel is still on the home screen`);
    assert.equal(await page.locator('.clarity-pane').filter({ visible: true }).count(), 0,
      `${tag}: no workspace pane leaks onto the home screen`);

    // --- frame A must survive a live language switch ---------------------
    await page.evaluate(() => window.WB_I18N.set('ko'));
    const koTiles = await page.evaluate(() => [...document.querySelectorAll('#home-menu [data-menu]')]
      .map((tile) => tile.querySelector('.clarity-tile-purpose')?.textContent.trim() || ''));
    assert.deepEqual(koTiles, ['자료를 모으고 읽습니다', '질문하고 근거를 확인합니다', '초안을 쓰고 주장을 검증합니다', '지난 대화를 남기거나 지웁니다'],
      `${tag}: the four purpose lines have natural Korean`);
    await page.evaluate(() => window.WB_I18N.set('en'));
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#home-menu [data-menu]')]
      .map((tile) => tile.querySelector('.clarity-tile-purpose')?.textContent.trim() || '')),
    Object.values(FRAME.tile), `${tag}: switching back restores the English lines exactly`);

    await workspace(page);

    // --- B: Literature Review --------------------------------------------
    await page.evaluate(() => openLibraryView());
    await page.waitForFunction(() => typeof libraryState === 'object' && libraryState.tree !== null && !libraryState.loading);
    // At the minimum width the shell deliberately opens the projects drawer when a
    // document view is entered. That drawer is a real panel over the workspace, so
    // close it before judging whether the pane is reachable.
    await closeDrawers(page);
    let seen = await purposeOf(page, 'library');
    assert.equal(seen.shown, true, `${tag}: the library pane is on screen with the library`);
    assert.equal(seen.title, FRAME.purpose.library, `${tag}: Literature Review says what it is for in one sentence`);
    assert.ok(seen.sub && seen.sub.length > 20, `${tag}: and explains what to do there`);
    let primary = await onePrimary(page, 'library');
    assert.deepEqual({ count: primary.count, text: primary.text, visible: primary.visible, reachable: primary.reachable },
      { count: 1, text: FRAME.primary.library, visible: true, reachable: true },
      `${tag}: Literature Review offers exactly one primary action, and a pointer reaches it`);
    // C1: the primary is painted with the accent, and it is the only accent-filled
    // button in the viewport. Asking for the fill in CSS was not enough: the
    // surface stylesheet loads later and repaints it white.
    assert.equal(primary.painted, primary.accent,
      `${tag}: the library primary is painted with the accent fill (${primary.painted} vs ${primary.accent})`);
    assert.deepEqual(primary.accentInViewport, ['clarity-primary-library'],
      `${tag}: and it is the only accent-filled button on the first screen`);
    assert.equal(seen.emptyHidden, false, `${tag}: a new user sees the library empty state`);
    assert.equal(seen.emptyTitle, FRAME.empty.library, `${tag}: and it names the empty state plainly`);
    // H2: one empty state, not two. The list draws its own line for an empty
    // folder, and the pane above it says the project has nothing yet; a new
    // project used to show both, which reads as two different problems. Exactly
    // one may be painted, and the one that survives must tell a first-time user
    // what to do, not just that something is absent.
    const empties = await page.evaluate(() => {
      const onScreen = (el) => !!el && el.getClientRects().length > 0;
      const visible = [...document.querySelectorAll('.clarity-empty, [data-testid="library-empty"]')]
        .filter(onScreen)
        .map((el) => ({ testid: el.getAttribute('data-testid') || el.className,
          text: el.textContent.replace(/\s+/g, ' ').trim() }));
      return visible;
    });
    assert.equal(empties.length, 1,
      `${tag}: Literature Review paints one empty state, not two (found: ${JSON.stringify(empties.map((e) => e.testid))})`);
    // "What to do first", in the product's own words: adding the PDFs is the one
    // action that ends the empty state, so the sentence has to name it.
    assert.match(empties[0].text, /Add PDFs/,
      `${tag}: and it tells a new user what to do first (${empties[0].text})`);
    // Secondary controls are demoted, never removed.
    const libControls = await page.evaluate(() => {
      const ids = ['new-folder', 'library-search', 'library-search-run', 'rename-folder', 'library-screen'];
      return ids.map((id) => {
        const el = document.getElementById(id) || document.querySelector(`[data-testid="${id}"]`);
        return { id, present: !!el, visible: !!el && el.getClientRects().length > 0 };
      });
    });
    for (const control of libControls) {
      assert.ok(control.present, `${tag}: ${control.id} is still in the library (demoted, not removed)`);
    }
    // The purpose row must not cost the working area its first screen.
    await assertFirstScreen(page, 'library', tag, 'Literature Review');

    // --- C: Research ------------------------------------------------------
    await page.evaluate(() => showResearchView());
    seen = await purposeOf(page, 'research');
    assert.equal(seen.shown, true, `${tag}: the research pane is on screen with Research`);
    assert.equal(seen.title, FRAME.purpose.research, `${tag}: Research says what it is for in one sentence`);
    primary = await onePrimary(page, 'research');
    assert.deepEqual({ count: primary.count, text: primary.text, visible: primary.visible, reachable: primary.reachable },
      { count: 1, text: FRAME.primary.research, visible: true, reachable: true },
      `${tag}: Research offers exactly one primary action, and a pointer reaches it`);
    assert.equal(primary.painted, primary.accent,
      `${tag}: the research primary is painted with the accent fill (${primary.painted} vs ${primary.accent})`);
    assert.deepEqual(primary.accentInViewport, ['clarity-primary-research'],
      `${tag}: and it is the only accent-filled button on the first screen`);
    assert.equal(seen.emptyHidden, false, `${tag}: a new user sees the research empty state`);
    assert.equal(seen.emptyTitle, FRAME.empty.research, `${tag}: and it names the empty state plainly`);
    // The real loop in plain words, in order: question -> evidence card -> decision.
    // At a normal window size the three cards are drawn in full. At the app's 720x480
    // minimum they are the one thing on this screen that is orientation rather than
    // content, so there — and only there — the same three steps sit behind a single
    // summary line that opens into them. The copy asserted below is identical at both
    // sizes; only whether it is expanded differs.
    const foldedAtMinimum = await page.evaluate(() => {
      const fold = document.querySelector('[data-testid="clarity-steps-fold"]');
      const summary = document.querySelector('[data-testid="clarity-steps-summary"]');
      const open = fold.open;
      if (!open) fold.open = true;
      return { open, summaryShown: !!summary && summary.getClientRects().length > 0,
        summaryText: summary ? summary.textContent.trim() : null };
    });
    assert.equal(foldedAtMinimum.open, tag !== '720x480',
      `${tag}: the three steps are open at a normal size and folded at the minimum height`);
    if (!foldedAtMinimum.open) {
      assert.ok(foldedAtMinimum.summaryShown, `${tag}: the folded steps say what they hold in one line`);
      assert.match(foldedAtMinimum.summaryText, /Three steps/,
        `${tag}: and the summary is the step explainer, not a different sentence`);
    }
    const steps = await page.evaluate(() => [...document.querySelectorAll('[data-testid="clarity-steps-research"] > li')]
      .map((li) => ({
        n: li.querySelector('.clarity-step-n')?.textContent.trim(),
        title: li.querySelector('.clarity-step-t')?.textContent.trim(),
        body: li.querySelector('.clarity-step-p')?.textContent.trim() || ''
      })));
    assert.deepEqual(steps.map((s) => s.n), ['1', '2', '3'], `${tag}: the research loop is numbered 1-2-3`);
    assert.deepEqual(steps.map((s) => s.title), ['Your question', 'Evidence card', 'Your decision'],
      `${tag}: the loop names the three real objects in order`);
    assert.match(steps[2].body, /Adopt/, `${tag}: the decision step names the real actions (Adopt)`);
    assert.match(steps[2].body, /Defer/, `${tag}: the decision step names the real actions (Defer)`);
    assert.match(steps[2].body, /Reject/, `${tag}: the decision step names the real actions (Reject)`);
    // Put the fold back exactly as the app drew it before the copy above was read.
    // Forcing it shut here would measure a screen the user never sees, and it was
    // that forced state that hid the 720x480 overflow the fit check now catches.
    await page.evaluate((open) => {
      const fold = document.querySelector('[data-testid="clarity-steps-fold"]');
      if (fold) fold.open = open;
    }, foldedAtMinimum.open);
    await assertFirstScreen(page, 'research', tag, 'Research before a question');

    // H1: one purpose heading and one "New question" action. The shipped intro
    // repeats both, so it is hidden — still in the document, with its ids, but out
    // of the tab order and the accessibility tree.
    const oneStatement = await page.evaluate(() => {
      const section = document.querySelector('#app-shell > main > section');
      const visibleHeadings = [...section.querySelectorAll('h1, h2')]
        .filter((el) => el.getClientRects().length > 0)
        .map((el) => el.textContent.trim());
      const visibleNewQuestion = [...section.querySelectorAll('button, a[href], [role="button"]')]
        .filter((el) => el.getClientRects().length > 0 && /new question/i.test(el.textContent || ''))
        .map((el) => el.id || el.getAttribute('data-testid') || el.textContent.trim());
      const intro = document.getElementById('research-intro');
      const introNewQuestion = document.getElementById('quiet-new-question');
      const focusable = introNewQuestion ? introNewQuestion.getClientRects().length > 0 : false;
      return {
        // The project name is the document's own title and is not a purpose line;
        // it is counted separately so this stays about the frame's statement.
        purposeHeadings: visibleHeadings.filter((t) => t !== document.getElementById('project-title')?.textContent.trim()),
        visibleNewQuestion, introPresent: !!intro, introBox: intro ? intro.getClientRects().length : 0,
        introNewQuestionInTabOrder: focusable
      };
    });
    assert.equal(oneStatement.purposeHeadings.length, 1,
      `${tag}: Research states its purpose once (found: ${JSON.stringify(oneStatement.purposeHeadings)})`);
    assert.equal(oneStatement.purposeHeadings[0], FRAME.purpose.research,
      `${tag}: and that one statement is the frame's sentence`);
    assert.deepEqual(oneStatement.visibleNewQuestion, ['clarity-primary-research'],
      `${tag}: and offers exactly one visible "New question" action`);
    assert.equal(oneStatement.introPresent, true, `${tag}: the shipped intro is still in the document for its contracts`);
    assert.equal(oneStatement.introBox, 0, `${tag}: but it is not painted`);
    assert.equal(oneStatement.introNewQuestionInTabOrder, false,
      `${tag}: and its duplicate New question is out of the tab order`);

    // H3/H4: at the app's minimum size the section does not scroll on the first
    // screen, no text inside the pane is clamped, and the tool row is whole.
    if (tag === '720x480') {
      const fit = await page.evaluate(() => {
        const section = document.querySelector('#app-shell > main > section');
        const pane = document.querySelector('[data-clarity-pane="research"]');
        const clipped = [...pane.querySelectorAll('*')].filter((el) => {
          const cs = getComputedStyle(el);
          return (cs.webkitLineClamp && cs.webkitLineClamp !== 'none') ||
            el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
        }).map((el) => ({ cls: String(el.className).slice(0, 40),
          sh: el.scrollHeight, ch: el.clientHeight, clamp: getComputedStyle(el).webkitLineClamp }));
        const tools = document.getElementById('research-tools');
        const r = tools.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {
          sectionOver: section.scrollHeight - section.clientHeight,
          sectionTop: section.scrollTop,
          clipped,
          toolsInView: r.top >= 0 && r.bottom <= innerHeight + 1,
          toolsUncovered: !!hit && (hit === tools || tools.contains(hit)),
          toolsBox: [Math.round(r.top), Math.round(r.bottom)]
        };
      });
      assert.equal(fit.sectionOver, 0,
        `${tag}: the Research first screen needs no section scrolling (${fit.sectionOver}px over)`);
      assert.equal(fit.sectionTop, 0, `${tag}: and it starts at the top`);
      assert.deepEqual(fit.clipped, [], `${tag}: no text inside the pane is clamped or cut`);
      assert.equal(fit.toolsInView, true, `${tag}: the tool row is fully on screen ${JSON.stringify(fit.toolsBox)}`);
      assert.equal(fit.toolsUncovered, true, `${tag}: and nothing covers it`);
    }

    // A question in progress: the empty state and the steps give way, and the
    // evidence still fits the first screen with its own internal scroll.
    await page.evaluate(() => {
      S.question = 'q-clarity';
      S.questionText = 'Does recorded approval timing identify the intervention?';
      renderResult({ result_kind: 'EvidenceAssessment', outcome: 'partial',
        assessment: { claim: 'The recorded approval date alone does not verify intervention timing.',
          outcome: 'partial', confirmation_level: 'human review required',
          unresolved: ['Check the implementation-date variable.'],
          suggested_next_action: 'Inspect the registered source before adopting the proposal.' },
        evidence: Array.from({ length: 16 }, (_, i) => ({ source_id: `synthetic-${i}`,
          source_version: 'fixture-release-1', location: `page ${i + 1}`,
          excerpt: 'Approval and implementation are distinct events. The registered date needs researcher verification.' })) });
      controls();
    });
    await page.waitForTimeout(200);
    const working = await page.evaluate(() => {
      const empty = document.querySelector('[data-testid="clarity-empty-research"]');
      const steps = document.querySelector('[data-testid="clarity-steps-research"]');
      const result = document.getElementById('result');
      return { emptyHidden: empty.hidden, stepsHidden: steps.hidden,
        resultScrolls: result.scrollHeight > result.clientHeight };
    });
    assert.equal(working.emptyHidden, true, `${tag}: a working question hides "No question yet"`);
    assert.equal(working.stepsHidden, true, `${tag}: and its orientation steps give the room to the evidence`);
    // overview_fit.css gives #result its own scroll on a wide window only; below
    // 1100px the evidence scrolls with the section, by design.
    if (tag === '1280x800') {
      assert.equal(working.resultScrolls, true, `${tag}: long evidence scrolls inside the evidence card`);
    }
    // Populated Research keeps the evidence card scroll-free at the section level
    // at desktop size; at the 720x480 minimum the content is still reachable.
    const overflow = await noSectionScroll(page);
    if (tag === '1280x800') {
      assert.ok(overflow.over <= 1, `${tag}: populated Research needs no section scrolling (${overflow.over}px over)`);
    } else {
      const shellOver = await page.evaluate(() => {
        const shell = document.getElementById('app-shell');
        return shell.scrollHeight - shell.clientHeight;
      });
      assert.ok(shellOver <= 1, `${tag}: populated Research never scrolls the whole window (${shellOver}px)`);
    }
    await page.evaluate(() => { S.question = null; S.questionText = null; controls(); });

    // --- D: Writing -------------------------------------------------------
    await page.evaluate(() => openWritingView());
    await page.waitForTimeout(200);
    seen = await purposeOf(page, 'writing');
    assert.equal(seen.shown, true, `${tag}: the writing pane is on screen with Writing`);
    assert.equal(seen.title, FRAME.purpose.writing, `${tag}: Writing says what it is for in one sentence`);
    primary = await onePrimary(page, 'writing');
    assert.deepEqual({ count: primary.count, text: primary.text, visible: primary.visible, reachable: primary.reachable },
      { count: 1, text: FRAME.primary.writing, visible: true, reachable: true },
      `${tag}: Writing offers exactly one primary action, and a pointer reaches it`);
    assert.equal(primary.painted, primary.accent,
      `${tag}: the writing primary is painted with the accent fill (${primary.painted} vs ${primary.accent})`);
    assert.deepEqual(primary.accentInViewport, ['clarity-primary-writing'],
      `${tag}: and it is the only accent-filled button on the first screen`);
    assert.equal(seen.emptyHidden, false, `${tag}: a new user sees the writing empty state`);
    assert.equal(seen.emptyTitle, FRAME.empty.writing, `${tag}: and it names the empty state plainly`);
    // The paper bar's own controls are all still there, demoted.
    const writingControls = await page.evaluate(() => ({
      paperSelect: !!document.getElementById('latex-draft-select'),
      compile: !!document.getElementById('latex-compile'),
      editor: !!document.getElementById('latex-editor'),
      save: document.querySelectorAll('#latex-save').length,
      // Frames F–I mount here. The stable id is kept, the sheet offers its one
      // opener, and it draws no form until it is asked for.
      claimHost: !!document.getElementById('claim-check-host'),
      claimOpener: !!document.querySelector('#claim-check-host [data-testid="cc-open"]'),
      claimFormDrawn: !!document.querySelector('#claim-check-host [data-testid="cc-root"]')
    }));
    assert.deepEqual(writingControls, {
      paperSelect: true, compile: true, editor: true, save: 1,
      claimHost: true, claimOpener: true, claimFormDrawn: false
    }, `${tag}: the paper bar, the editor and the closed claim-check sheet are all in place`);
    await assertFirstScreen(page, 'writing', tag, 'Writing');

    // --- E: Conversation Review ------------------------------------------
    // The view tabs live in a strip that is hidden by design; the opener is the
    // same global the tab calls.
    await page.evaluate(() => openSecretaryReview());
    await page.waitForFunction(() => typeof secretaryReviewState === 'object' && secretaryReviewState &&
      secretaryReviewState.loaded && !secretaryReviewState.loading, null, { timeout: 5000 });
    await page.waitForTimeout(150);
    seen = await purposeOf(page, 'review');
    assert.equal(seen.shown, true, `${tag}: the review pane is on screen with Conversation Review`);
    assert.equal(seen.title, FRAME.purpose.review, `${tag}: Conversation Review says what it is for in one sentence`);
    primary = await onePrimary(page, 'review');
    assert.deepEqual({ count: primary.count, text: primary.text, visible: primary.visible, reachable: primary.reachable },
      { count: 1, text: FRAME.primary.review, visible: true, reachable: true },
      `${tag}: Conversation Review offers exactly one primary action, and a pointer reaches it`);
    assert.equal(primary.painted, primary.accent,
      `${tag}: the review primary is painted with the accent fill (${primary.painted} vs ${primary.accent})`);
    assert.deepEqual(primary.accentInViewport, ['clarity-primary-review'],
      `${tag}: and it is the only accent-filled button on the first screen`);
    assert.equal(seen.emptyHidden, false, `${tag}: a new user sees the review empty state`);
    assert.equal(seen.emptyTitle, FRAME.empty.review, `${tag}: and it names the empty state plainly`);
    // The opened view's title is the tile's title: no rename on the way in.
    const titles = await page.evaluate(() => ({
      tile: document.querySelector('#home-menu [data-menu="review"] .menu-label')?.textContent.trim(),
      view: document.querySelector('.secretary-review-head h3')?.textContent.trim(),
      tab: document.getElementById('view-secretary-review')?.textContent.trim()
    }));
    assert.equal(titles.view, titles.tile,
      `${tag}: the opened view is titled exactly what its tile says (${titles.tile} / ${titles.view})`);
    assert.equal(titles.tab, titles.tile, `${tag}: and the underlying tab contract agrees`);
    // The destructive bulk action is present but hidden until something is discarded.
    assert.equal(await page.locator('#secretary-review-clear-discarded').isHidden(), true,
      `${tag}: "Delete all discarded" stays hidden while nothing is discarded`);
    assert.equal(await page.locator('#secretary-review-clear-discarded').count(), 1,
      `${tag}: and the control itself is not removed`);
    await assertFirstScreen(page, 'review', tag, 'Conversation Review');

    // C2: the review primary is a working action, not a dead button. A pointer
    // click must open the shipped secretary panel and leave the cursor in the one
    // composer, so "Ask the secretary…" really starts an exchange.
    await page.getByTestId('clarity-primary-review').click();
    const asked = await page.evaluate(async () => {
      const visible = (el) => !!el && el.getClientRects().length > 0;
      // The drawer slides for 160ms; its composer is not focusable until it lands.
      const settled = await new Promise((resolve) => {
        const started = performance.now();
        (function poll() {
          const input = document.getElementById('secretary-input');
          if (input && visible(input)) return resolve(true);
          if (performance.now() - started > 3000) return resolve(false);
          requestAnimationFrame(poll);
        })();
      });
      const input = document.getElementById('secretary-input');
      return {
        panelOpen: !!(window.WB_DECLUTTER_SHELL && WB_DECLUTTER_SHELL.isPanelOpen('secretary')),
        settled,
        inputVisible: visible(input),
        focusInInput: document.activeElement === input,
        expanded: document.getElementById('quiet-secretary')?.getAttribute('aria-expanded'),
        error: typeof S === 'object' && S && S.error ? String(S.error) : null
      };
    });
    assert.equal(asked.panelOpen, true, `${tag}: the review primary opens the secretary panel`);
    assert.equal(asked.expanded, 'true', `${tag}: and the panel's own control says so`);
    assert.equal(asked.settled, true, `${tag}: the panel really arrived on screen`);
    assert.equal(asked.inputVisible, true, `${tag}: with its composer visible`);
    assert.equal(asked.focusInInput, true, `${tag}: and the cursor in #secretary-input`);
    assert.equal(asked.error, null, `${tag}: pressing it raises no error (${asked.error})`);
    // Put the panel away so the language pass below measures the same screen.
    await page.evaluate(() => {
      if (WB_DECLUTTER_SHELL.isPanelOpen('secretary')) WB_DECLUTTER_SHELL.closePanel('secretary');
    });
    await page.waitForFunction(() => {
      const input = document.getElementById('secretary-input');
      return !input || input.getClientRects().length === 0;
    }, null, { timeout: 3000 }).catch(() => {});

    // C3: every registered clarity string switches, in both directions. Each node
    // the panes built recorded its English key at build time, so the switch is
    // checked against those keys rather than against a hand-written list that
    // could drift from the code. After ko, no node's text may still BE its English
    // key; after en, every node's text must be that key again.
    const language = await page.evaluate(() => {
      // Every registered sentence, including the ones inside an empty state that
      // is not on screen yet: a string that only picks up the new language when
      // it is first revealed would show English to a Korean user at exactly the
      // moment the screen tells them it has nothing to show.
      const nodes = () => [...document.querySelectorAll('.clarity-pane [data-clarity-key], ' +
        '.clarity-pane [data-clarity-arg]')];
      const read = () => nodes().map((el) => ({
        key: el.getAttribute('data-clarity-key'),
        arg: el.getAttribute('data-clarity-arg'),
        text: el.textContent.trim()
      }));
      const en = read();
      window.WB_I18N.set('ko');
      const ko = read();
      window.WB_I18N.set('en');
      const back = read();
      return { en, ko, back };
    });
    // After ko: a node whose text is exactly its English key was never translated.
    // A key that legitimately has no Korean yet would show up here, which is the
    // point — the harness fails rather than the screen being half-translated.
    const untranslated = language.ko.filter((row, i) => row.key && row.text === language.en[i].text &&
      /[A-Za-z]{3,}/.test(row.text));
    assert.deepEqual(untranslated, [],
      `${tag}: after en→ko no registered clarity string is still in English`);
    // A sentence that names a control in bold carries a second string inside it.
    // Comparing whole nodes would call that sentence translated while the bold
    // action was still English, so the embedded name is checked on its own: after
    // ko, the English name of the action must not survive inside the sentence.
    const embedded = language.ko.filter((row, i) => row.arg &&
      /[A-Za-z]{3,}/.test(row.arg) && row.text.includes(row.arg));
    assert.deepEqual(embedded, [],
      `${tag}: after en→ko no bolded action name inside a sentence is still English`);
    // And the switch back is exact: the same text, in the same order.
    assert.deepEqual(language.back.map((r) => r.text), language.en.map((r) => r.text),
      `${tag}: after ko→en every registered string is restored exactly`);

    // Every string the user can see is covered: the review empty state's two body
    // sentences and the library note are rendered text, so they are counted here
    // as well as through their keys.
    await page.evaluate(() => window.WB_I18N.set('ko'));
    const visibleKo = await page.evaluate(() => {
      const pane = document.querySelector('[data-clarity-pane="review"]');
      const english = [...pane.querySelectorAll('h2, h3, p, summary, button')]
        .filter((el) => el.getClientRects().length > 0)
        .map((el) => el.textContent.trim())
        .filter((text) => /[A-Za-z]{4,}/.test(text) && !/^(Home|Esc)$/.test(text));
      const backTitle = document.getElementById('back-to-menu')?.title || '';
      return { english, backTitle };
    });
    assert.deepEqual(visibleKo.english, [],
      `${tag}: no visible English text is left in the Korean review pane`);
    assert.equal(/Return to the home menu/.test(visibleKo.backTitle), false,
      `${tag}: the back-to-menu title is translated too (${visibleKo.backTitle})`);
    await page.evaluate(() => window.WB_I18N.set('en'));
    assert.equal(await page.evaluate(() => document.querySelector('[data-testid="clarity-title-review"]')?.textContent.trim()),
      FRAME.purpose.review, `${tag}: and switching back restores the English sentence`);

    // --- the menus are still exactly the four that were approved ----------
    const menus = await page.evaluate(() => {
      const table = [...document.querySelectorAll('#home-menu [data-menu]')].map((t) => t.getAttribute('data-menu'));
      return table;
    });
    assert.deepEqual(menus, ['library', 'research', 'writing', 'review'],
      `${tag}: clarity adds no menu, renames none and merges none`);

    assert.deepEqual(errors, [], `${tag}: no script error anywhere in the run`);
    await page.close();
  }

  // --- the five follow-ups ride along in this harness ---------------------
  {
    const { page, errors } = await open({ width: 1280, height: 800 });

    // (1) A live language switch updates the idle hint and both buttons.
    const beforeSwitch = await page.evaluate(() => {
      const hint = document.querySelector('.home-chip-hint');
      const open = document.querySelector('[data-testid="idle-open-chip"]');
      const fresh = document.querySelector('[data-testid="idle-new-chip"]');
      return { hint: hint?.textContent.trim(), open: open?.textContent.trim(), fresh: fresh?.textContent.trim() };
    });
    await page.evaluate(() => window.WB_I18N.set('ko'));
    const afterSwitch = await page.evaluate(() => {
      const hint = document.querySelector('.home-chip-hint');
      const open = document.querySelector('[data-testid="idle-open-chip"]');
      const fresh = document.querySelector('[data-testid="idle-new-chip"]');
      return { hint: hint?.textContent.trim(), open: open?.textContent.trim(), fresh: fresh?.textContent.trim() };
    });
    assert.notEqual(afterSwitch.hint, beforeSwitch.hint, 'a live switch translates the idle hint');
    assert.match(afterSwitch.hint, /새 칩/, 'and the Korean hint is the real Korean sentence');
    assert.equal(afterSwitch.open, '열기…', 'the Open… button follows the language switch');
    assert.equal(afterSwitch.fresh, '새 칩 만들기…', 'the New Chip… button follows the language switch');
    await page.evaluate(() => window.WB_I18N.set('en'));
    const backAgain = await page.evaluate(() => ({
      open: document.querySelector('[data-testid="idle-open-chip"]')?.textContent.trim(),
      fresh: document.querySelector('[data-testid="idle-new-chip"]')?.textContent.trim()
    }));
    assert.deepEqual(backAgain, { open: 'Open…', fresh: 'New Chip…' },
      'and switching back restores the English labels exactly');

    // (3) 'Writing' has one Korean translation, not two.
    await page.evaluate(() => window.WB_I18N.set('ko'));
    const writingKo = await page.evaluate(() => window.WB_I18N.t('Writing'));
    assert.equal(writingKo, '집필', "the one 'Writing' translation is 집필");
    // Only i18n.ko.js may define it: a second copy in startup.js once said 논문 작성.
    const startupWriting = await page.evaluate(async () => {
      const text = await (await fetch('/startup.js')).text();
      return /'Writing'\s*:\s*'[^']*'/.test(text);
    });
    assert.equal(startupWriting, false, 'startup.js no longer defines its own Writing translation');
    await page.evaluate(() => window.WB_I18N.set('en'));

    // (4) The hidden tab strip is hidden by its own attribute, not only by its parent.
    const strip = await page.evaluate(() => {
      const tabs = document.querySelector('.view-bar .view-tabs');
      const bar = document.querySelector('.view-bar');
      return { tabsHidden: tabs.hasAttribute('hidden'), tabsDisplay: getComputedStyle(tabs).display,
        barDisplay: getComputedStyle(bar).display };
    });
    assert.equal(strip.tabsHidden, true, 'the tab strip still carries hidden');
    assert.equal(strip.tabsDisplay, 'none', 'and its own hidden attribute is what hides it');
    // The four workspace tabs stay in the document for the aria-pressed contract,
    // and the fifth (Conversation Review) is moved into the Secretary panel by
    // declutter_shell.js — still in the document, under its own id.
    assert.equal(await page.locator('.view-tabs > button').count(), 4, 'the four workspace tabs remain in the document');
    assert.equal(await page.locator('#view-secretary-review').count(), 1,
      'and the fifth tab button is still in the document, in the Secretary panel');

    assert.deepEqual(errors, [], 'no script error in the follow-up checks');
    await page.close();
  }

  // (2) The opened view's title matches its tile in both languages.
  {
    const { page } = await open({ width: 1280, height: 800 });
    await workspace(page);
    await page.evaluate(() => openSecretaryReview());
    const pair = await page.evaluate(() => {
      const out = {};
      for (const lang of ['en', 'ko']) {
        window.WB_I18N.set(lang);
        out[lang] = {
          tile: window.WB_I18N.t('Conversation Review'),
          view: document.querySelector('.secretary-review-head h3').textContent.trim()
        };
      }
      window.WB_I18N.set('en');
      return out;
    });
    assert.equal(pair.en.view, pair.en.tile, 'English: the opened view is titled what the tile says');
    assert.equal(pair.ko.view, pair.ko.tile, 'Korean: the opened view is titled what the tile says');
    assert.equal(pair.ko.tile, '대화 기록 모음', 'and the tile keeps its Korean name');
    // The existing explanatory text is untouched.
    const explain = await page.evaluate(() => document.querySelector('.secretary-review-head').textContent);
    assert.match(explain, /Keeping a turn changes only its status/,
      'the view keeps its existing explanation of what keeping a turn does');
    await page.close();
  }

  // (5) The busy guard points at the end-session control that actually exists.
  {
    const { page } = await open({ width: 1280, height: 800 });
    await workspace(page);
    const guard = await page.evaluate(() => {
      document.getElementById('settings-end').disabled = false;
      S.busy = true;
      controls();
      const whileBusy = document.getElementById('settings-end').disabled;
      S.busy = false;
      controls();
      return { whileBusy, whenIdle: document.getElementById('settings-end').disabled,
        staleLookup: !!document.getElementById('end-session') };
    });
    assert.equal(guard.staleLookup, false, 'the removed header control is gone from the document');
    assert.equal(guard.whileBusy, true, 'the busy guard disables the Settings end-session control');
    assert.equal(guard.whenIdle, false, 'and enables it again when the task finishes');
    await page.close();
  }

  // (6) H2: returning Home drops the surface mode, "nothing open" is its own
  // sentinel, and the enterMenu guard still does its job in all three shapes the
  // reviewer named — a double click, Escape during the expansion, and a
  // Library→Home→tile round trip.
  {
    const { page, errors } = await open({ width: 1280, height: 800 });
    await workspace(page);
    const modes = () => page.evaluate(() =>
      ['library-mode', 'writing-mode', 'citation-mode', 'secretary-review-mode']
        .filter((m) => document.querySelector('#app-shell > main > section').classList.contains(m)));

    await page.evaluate(() => openLibraryView());
    await page.waitForFunction(() => libraryState.tree !== null && !libraryState.loading);
    assert.deepEqual(await modes(), ['library-mode'], 'opening the library enters library mode');
    const sentinel = await page.evaluate(() => ({
      workspace: WB_STARTUP.activeSurface(),
      // The sentinel itself, published by the module that owns the guard.
      none_: WB_STARTUP.noSurface
    }));
    assert.equal(sentinel.workspace, 'library-mode', 'activeSurface names the open library');

    // Library → Home: the mode must not outlive the visit.
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForFunction(() => !document.getElementById('back-to-menu')?.getClientRects().length ||
      document.getElementById('portal')?.hidden, null, { timeout: 5000 }).catch(() => {});
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(120);
    assert.deepEqual(await modes(), [], 'returning Home clears every surface mode');
    const atHome = await page.evaluate(() => ({
      surface: WB_STARTUP.activeSurface(),
      sentinel: WB_STARTUP.noSurface,
      portalHidden: document.getElementById('portal').hidden,
      libraryStillMounted: !!document.getElementById('library-view')
    }));
    assert.equal(atHome.surface, atHome.sentinel,
      `Home reports "nothing open" as its own sentinel (${atHome.surface})`);
    assert.notEqual(atHome.sentinel, '', 'and that sentinel is distinct from plain Research');
    assert.equal(atHome.portalHidden, true, 'the workspace is off screen at Home');
    assert.equal(atHome.libraryStillMounted, true, 'and hidden, not unmounted');

    // Home → tile: the tile still opens its menu, exactly as a first entry does.
    await page.getByTestId('home-menu-library').click();
    await page.waitForFunction(() => libraryState.tree !== null && !libraryState.loading, null, { timeout: 5000 });
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => document.getElementById('library-view').classList.contains('hidden')), false,
      'a tile opened from Home after a prior visit still opens its surface');
    assert.deepEqual(await modes(), ['library-mode'], 'and re-enters library mode');

    // Double click on one tile: the second click is not a second transition, and
    // the surface it names is the one that ends up open.
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(120);
    await page.getByTestId('home-menu-research').dblclick();
    await page.waitForFunction(() => !document.getElementById('portal')?.hidden, null, { timeout: 5000 });
    await page.waitForTimeout(600);
    const afterDouble = await page.evaluate(() => ({
      portalOpen: !document.getElementById('portal').hidden,
      startupHidden: document.getElementById('startup').hidden,
      surface: WB_STARTUP.activeSurface()
    }));
    assert.equal(afterDouble.portalOpen, true, 'a double click still leaves the workspace open');
    assert.equal(afterDouble.startupHidden, true, 'and the home console off screen');
    assert.equal(afterDouble.surface, '', 'and lands on plain Research, the tile that was double-clicked');

    // Escape during the expansion: the collapse wins, the menu comes back, and the
    // delayed open does not repaint the workspace over it. Under reduced motion the
    // reveal settles synchronously, so this is Escape-from-the-open-workspace; the
    // real 320 ms window is covered by the motion block below.
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(120);
    await page.getByTestId('home-menu-library').click();
    // No wait: Escape lands inside the 320ms expansion, which is the window the
    // guard exists for.
    await page.keyboard.press('Escape');
    await page.waitForTimeout(900);
    const afterEscape = await page.evaluate(() => ({
      menuShown: !document.getElementById('startup-menu').hidden,
      portalHidden: document.getElementById('portal').hidden,
      surface: WB_STARTUP.activeSurface(),
      sentinel: WB_STARTUP.noSurface,
      // Painted or not, rather than the class alone: the library was opened
      // earlier in this round trip, so its own class is already off and the only
      // thing that can put it back on screen is the deferred open.
      libraryPainted: !!document.getElementById('library-view')?.getClientRects().length
    }));
    assert.equal(afterEscape.menuShown, true, 'Escape during the expansion brings the menu back');
    assert.equal(afterEscape.portalHidden, true, 'and leaves the workspace collapsed');
    assert.equal(afterEscape.surface, afterEscape.sentinel,
      'and reports "nothing open" once the workspace is gone');
    assert.equal(afterEscape.libraryPainted, false,
      'the cancelled tile open does not repaint the library under the menu');

    assert.deepEqual(errors, [], 'no script error in the H2 navigation round trips');
    await page.close();
  }

  // (7) The same claims with the real 320 ms reveal. Every frame above runs under
  // reducedMotion:'reduce', where the expansion resolves synchronously — so nothing
  // there can see the window in which the old surface used to paint over the new
  // one. This block leaves motion at 'no-preference', samples every frame of the
  // reveal, and asserts the old surface is never painted and never hittable.
  {
    const { page, errors } = await open({ width: 1280, height: 800, motion: 'no-preference' });
    await workspace(page);

    // Shape 1: Library → Home → Research. Under the old code the library was
    // still painted while Research arrived, and took the clicks.
    await page.evaluate(() => openLibraryView());
    await page.waitForFunction(() => libraryState.tree !== null && !libraryState.loading);
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(150);
    const libraryAtHome = await page.evaluate(() => {
      const view = document.getElementById('library-view');
      return { painted: !!view.getClientRects().length,
        hidden: view.classList.contains('hidden'),
        modes: ['library-mode', 'writing-mode', 'citation-mode', 'secretary-review-mode']
          .filter((m) => document.querySelector('#app-shell > main > section').classList.contains(m)) };
    });
    assert.equal(libraryAtHome.painted, false, 'returning Home takes the library off screen, not just out of its mode');
    assert.equal(libraryAtHome.hidden, true, 'and the library carries the .hidden that actually hides it');
    assert.deepEqual(libraryAtHome.modes, [], 'and Home carries no surface mode');

    await page.evaluate(() => { window.__frames = []; });
    const libraryToResearch = await sampleReveal(page, 'home-menu-research');
    const growing = libraryToResearch.filter((frame) => frame.opening || frame.portalHidden === false);
    assert.ok(growing.length >= 2,
      `the reveal is sampled while it is under way, not only once it has settled (${growing.length} frames)`);
    for (const frame of libraryToResearch) {
      assert.ok(!frame.painted.includes('library-view'),
        `the library is never painted during the reveal into Research (frame ${JSON.stringify(frame)})`);
      assert.ok(frame.painted.length <= 1,
        `at most one surface is painted at a time (frame ${JSON.stringify(frame)})`);
      // No click can reach the old surface while the new one arrives: the whole
      // workspace is inert, so the browser's own hit test cannot land in it.
      if (frame.opening) {
        assert.equal(frame.inert, true,
          `the workspace is inert while the reveal is under way (frame ${JSON.stringify(frame)})`);
        assert.notEqual(frame.hitSurface, 'library-view',
          `and a click at the centre of the window never reaches the library (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.hitInWorkspace, false,
          `and no click lands anywhere in the arriving workspace (frame ${JSON.stringify(frame)})`);
      }
    }
    await page.waitForFunction(() => !document.getElementById('portal').hidden &&
      !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });
    await page.waitForTimeout(200);
    // The probe is not blind: the same call reaches the workspace once it settles.
    const calibrated = await calibrateHit(page);
    assert.equal(calibrated.inert, false, 'the workspace is no longer inert once the reveal has settled');
    assert.equal(calibrated.hitInWorkspace, true,
      `the hit probe really does see the settled workspace (landed on ${calibrated.tag}), so the inert frame assertions are not vacuous`);
    const researchSettled = await page.evaluate(() => ({
      libraryPainted: !!document.getElementById('library-view').getClientRects().length,
      questionPainted: !!document.getElementById('question')?.getClientRects().length,
      surface: WB_STARTUP.activeSurface(),
      hit: (() => { const at = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
        return at ? (at.closest('#library-view') ? 'library-view' : at.tagName.toLowerCase()) : null; })()
    }));
    assert.equal(researchSettled.libraryPainted, false, 'once settled, the library is gone from the screen');
    assert.equal(researchSettled.questionPainted, true, 'and Research is what is there');
    assert.equal(researchSettled.surface, '', 'Research is plain Research, with no mode class');
    assert.notEqual(researchSettled.hit, 'library-view', 'and the centre of the window is not the library');

    // Shape 2: Writing → Home → Library. The mirror image: the writing view must
    // not paint during the reveal into the library.
    await page.evaluate(() => openWritingView());
    await page.waitForFunction(() => !!document.getElementById('latex-view') &&
      !document.getElementById('latex-view').classList.contains('hidden'), null, { timeout: 5000 });
    await page.waitForTimeout(150);
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(150);
    const writingAtHome = await page.evaluate(() => ({
      painted: !!document.getElementById('latex-view').getClientRects().length,
      hidden: document.getElementById('latex-view').classList.contains('hidden')
    }));
    assert.equal(writingAtHome.painted, false, 'returning Home takes the writing view off screen');
    assert.equal(writingAtHome.hidden, true, 'and the writing view carries .hidden');
    const writingToLibrary = await sampleReveal(page, 'home-menu-library');
    for (const frame of writingToLibrary) {
      assert.ok(!frame.painted.includes('latex-view'),
        `the writing view is never painted during the reveal into the library (frame ${JSON.stringify(frame)})`);
      assert.ok(frame.painted.length <= 1,
        `at most one surface is painted at a time (frame ${JSON.stringify(frame)})`);
      if (frame.opening) {
        assert.equal(frame.inert, true,
          `the workspace is inert while the reveal is under way (frame ${JSON.stringify(frame)})`);
        assert.notEqual(frame.hitSurface, 'latex-view',
          `and a click at the centre never reaches the writing view (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.hitInWorkspace, false,
          `and no click lands anywhere in the arriving workspace (frame ${JSON.stringify(frame)})`);
      }
    }
    await page.waitForFunction(() => !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });

    // Shape 3: Review → Home → Research.
    await page.evaluate(() => openSecretaryReview());
    await page.waitForFunction(() => !!document.getElementById('secretary-review-view') &&
      !document.getElementById('secretary-review-view').classList.contains('hidden'), null, { timeout: 5000 });
    await page.waitForTimeout(150);
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(150);
    const reviewAtHome = await page.evaluate(() => ({
      painted: !!document.getElementById('secretary-review-view').getClientRects().length,
      hidden: document.getElementById('secretary-review-view').classList.contains('hidden')
    }));
    assert.equal(reviewAtHome.painted, false, 'returning Home takes the review list off screen');
    assert.equal(reviewAtHome.hidden, true, 'and the review list carries .hidden');
    const reviewToResearch = await sampleReveal(page, 'home-menu-research');
    for (const frame of reviewToResearch) {
      assert.ok(!frame.painted.includes('secretary-review-view'),
        `the review list is never painted during the reveal into Research (frame ${JSON.stringify(frame)})`);
      assert.ok(frame.painted.length <= 1,
        `at most one surface is painted at a time (frame ${JSON.stringify(frame)})`);
      if (frame.opening) {
        assert.equal(frame.inert, true,
          `the workspace is inert while the reveal is under way (frame ${JSON.stringify(frame)})`);
        assert.notEqual(frame.hitSurface, 'secretary-review-view',
          `and a click at the centre never reaches the review list (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.hitInWorkspace, false,
          `and no click lands anywhere in the arriving workspace (frame ${JSON.stringify(frame)})`);
      }
    }
    // The probe reaches the workspace here too, in the third shape as well.
    await page.waitForFunction(() => !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });
    assert.equal((await calibrateHit(page)).hitInWorkspace, true,
      'and the hit probe sees the settled workspace after the Review→Home→Research round trip too');

    // -- the workspace is not a target while it is still arriving ------------
    // Sampled from inside the reveal: the palette, the tab strip and the whole
    // shell must not take a click that was aimed at the menu underneath.
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(150);
    await page.getByTestId('home-menu-library').click();
    const duringReveal = await page.evaluate(() => new Promise((resolve) => {
      const portal = document.getElementById('portal');
      const sample = () => {
        if (!portal.classList.contains('opening')) return requestAnimationFrame(sample);
        const at = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
        const insidePortal = !!(at && portal.contains(at));
        resolve({ opening: true, inert: portal.inert === true, insidePortal,
          tag: at ? at.tagName.toLowerCase() : null, id: at ? at.id : null });
      };
      requestAnimationFrame(sample);
    }));
    assert.equal(duringReveal.opening, true, 'the sample really lands inside the reveal');
    assert.equal(duringReveal.inert, true, 'the workspace is inert while the reveal is under way');
    assert.equal(duringReveal.insidePortal, false,
      `so a click at the centre of the window does not land in the arriving workspace (hit ${duringReveal.tag}#${duringReveal.id})`);
    await page.waitForFunction(() => !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });
    assert.equal(await page.evaluate(() => document.getElementById('portal').inert), false,
      'and it is a target again once the reveal has finished');

    // -- Escape inside the real window cancels the reveal -------------------
    await page.evaluate(() => WB_STARTUP.backToMenu());
    await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
    await page.waitForTimeout(150);
    await page.getByTestId('home-menu-library').click();
    // Land the key while the display is genuinely still growing.
    await page.waitForFunction(() => document.getElementById('portal').classList.contains('opening'),
      null, { timeout: 2000 });
    await page.keyboard.press('Escape');
    const afterCancel = await page.evaluate(() => ({
      opening: document.getElementById('portal').classList.contains('opening'),
      portalHidden: document.getElementById('portal').hidden,
      inert: document.getElementById('portal').inert === true,
      menuShown: !document.getElementById('startup-menu').hidden,
      startupHidden: document.getElementById('startup').hidden,
      bodyOpen: document.body.classList.contains('workbench-open'),
      surface: WB_STARTUP.activeSurface(),
      libraryPainted: !!document.getElementById('library-view')?.getClientRects().length
    }));
    assert.equal(afterCancel.menuShown, true, 'an Escape inside the 320 ms reveal brings the menu back');
    assert.equal(afterCancel.portalHidden, true, 'and closes the workspace it was opening');
    assert.equal(afterCancel.opening, false, 'the reveal did not keep growing behind the menu');
    assert.equal(afterCancel.inert, false, 'and the closed workspace is not left inert');
    assert.equal(afterCancel.startupHidden, false, 'the console is on screen again');
    assert.equal(afterCancel.bodyOpen, false, 'and the window is not left in its workspace state');
    assert.equal(afterCancel.surface, await page.evaluate(() => WB_STARTUP.noSurface),
      'and the shell reports "nothing open"');
    assert.equal(afterCancel.libraryPainted, false, 'the cancelled tile open does not repaint the library');
    // The cancelled open must not fire early or late: give it the time it would
    // have taken, then check the menu is untouched and a fresh tile still works.
    await page.waitForTimeout(500);
    const afterCancelSettled = await page.evaluate(() => ({
      portalHidden: document.getElementById('portal').hidden,
      menuShown: !document.getElementById('startup-menu').hidden
    }));
    assert.equal(afterCancelSettled.menuShown, true, 'the menu stays up after the cancelled reveal would have finished');
    assert.equal(afterCancelSettled.portalHidden, true, 'and the workspace the tile asked for is not opened late');
    await page.getByTestId('home-menu-library').click();
    await page.waitForFunction(() => libraryState.tree !== null && !libraryState.loading, null, { timeout: 5000 });
    await page.waitForFunction(() => !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });
    assert.equal(await page.evaluate(() => document.getElementById('library-view').classList.contains('hidden')), false,
      'a tile clicked after a cancelled reveal still opens its surface');

    assert.deepEqual(errors, [], 'no script error in the real-motion reveal round trips');
    await page.close();
  }

  // (8) The way out, under the same real motion. Home closes the workspace by
  // fading #portal over 320 ms, and the surfaces must be closed at the *end* of
  // that fade: closeWorkspaceSurfaces() runs in done(), once the portal is
  // hidden, so the surface the reader left is what dissolves. Run before the
  // animation instead and the whole fade paints the bare Research scaffold —
  // #question-entry over an empty shell — which is what the reader sees on the
  // way out. Sampled frame by frame for Library, Writing and Review.
  {
    const { page, errors } = await open({ width: 1280, height: 800, motion: 'no-preference' });
    await workspace(page);

    const enter = {
      'library-view': async () => {
        await page.evaluate(() => openLibraryView());
        await page.waitForFunction(() => typeof libraryState === 'object' &&
          libraryState.tree !== null && !libraryState.loading, null, { timeout: 5000 });
      },
      'latex-view': async () => {
        await page.evaluate(() => openWritingView());
        await page.waitForFunction(() => !!document.getElementById('latex-view') &&
          !document.getElementById('latex-view').classList.contains('hidden'), null, { timeout: 5000 });
      },
      'secretary-review-view': async () => {
        await page.evaluate(() => openSecretaryReview());
        await page.waitForFunction(() => !!document.getElementById('secretary-review-view') &&
          !document.getElementById('secretary-review-view').classList.contains('hidden'), null, { timeout: 5000 });
      }
    };

    const surfaces = ['library-view', 'latex-view', 'secretary-review-view'];
    for (const surface of surfaces) {
      await enter[surface]();
      await page.waitForTimeout(150);
      const before = await page.evaluate((id) => ({
        painted: !!document.getElementById(id).getClientRects().length,
        questionPainted: !!document.getElementById('question-entry').getClientRects().length
      }), surface);
      assert.equal(before.painted, true, `${surface} is on screen before Home is asked for`);

      const frames = await sampleDissolve(page, surface);
      const open = frames.filter((frame) => !frame.portalHidden);
      // The first sample can land before the click handler has run, so "the
      // dissolve is under way" is the menu already being drawn or the opacity
      // having started to fall — not simply "the portal is not hidden yet".
      const fading = open.filter((frame) => frame.menuShown || (frame.portalOpacity !== null && frame.portalOpacity < 1));
      assert.ok(fading.length >= 2,
        `the dissolve out of ${surface} is sampled while it is under way, not only once it has settled (${fading.length} frames)`);
      assert.ok(fading.some((frame) => frame.portalOpacity !== null && frame.portalOpacity < 0.99),
        `at least one sampled frame catches the ${surface} dissolve part-way through the fade`);
      for (const frame of fading) {
        assert.equal(frame.portalPainted, true,
          `the workspace is still painted while it fades out of ${surface} (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.menuShown, true,
          `the menu is already drawn under the fading ${surface} workspace (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.paintedSurface, true,
          `${surface} is what fades out, so it is painted in every frame of the dissolve (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.paintedQuestion, false,
          `the bare Research scaffold never shows through the ${surface} dissolve (frame ${JSON.stringify(frame)})`);
        assert.equal(frame.modes.includes('library-mode') || frame.modes.includes('writing-mode') ||
          frame.modes.includes('secretary-review-mode') || frame.modes.includes('citation-mode'), true,
          `and the surface keeps its mode for the whole fade (frame ${JSON.stringify(frame)})`);
      }
      // Closing the surfaces early would not merely lose the mode: the whole
      // workspace would show Research for the entire fade, scaffold included.
      assert.equal(open.every((frame) => frame.paintedQuestion === false), true,
        `not one sampled frame of the ${surface} dissolve shows the Research scaffold`);
      assert.equal(open.every((frame) => frame.paintedSurface === true), true,
        `and ${surface} is painted in every frame the workspace is not yet closed`);

      await page.waitForSelector('#startup-menu:not([hidden])', { timeout: 5000 });
      await page.waitForTimeout(150);
      const settled = await page.evaluate(() => ({
        portalHidden: document.getElementById('portal').hidden === true,
        hidden: ['library-view', 'latex-view', 'secretary-review-view', 'citation-view']
          .map((id) => ({ id, hidden: (function (node) {
            return !node || node.classList.contains('hidden');
          })(document.getElementById(id)) })),
        surface: WB_STARTUP.activeSurface(),
        noSurface: WB_STARTUP.noSurface,
        questionPainted: !!document.getElementById('question-entry').getClientRects().length,
        paintedSurfaces: ['library-view', 'latex-view', 'secretary-review-view', 'citation-view']
          .filter((id) => { const node = document.getElementById(id); return !!node && node.getClientRects().length > 0; })
      }));
      assert.equal(settled.portalHidden, true, `the workspace is closed once the ${surface} dissolve ends`);
      for (const entry of settled.hidden)
        assert.equal(entry.hidden, true, `after the ${surface} dissolve, ${entry.id} carries the .hidden that takes it off screen`);
      assert.deepEqual(settled.paintedSurfaces, [], `and no surface is left painted after the ${surface} dissolve`);
      assert.equal(settled.questionPainted, false, `and the Research scaffold is not left on screen after the ${surface} dissolve`);
      assert.equal(settled.surface, settled.noSurface,
        'and the shell reports "nothing open" once the dissolve has settled');

      if (surface !== surfaces[surfaces.length - 1]) {
        await page.getByTestId('home-menu-research').click();
        await page.waitForFunction(() => !document.getElementById('portal').hidden &&
          !document.getElementById('portal').classList.contains('opening'), null, { timeout: 5000 });
        await page.waitForTimeout(150);
      }
    }

    assert.deepEqual(errors, [], 'no script error in the real-motion dissolves back to Home');
    await page.close();
  }

  console.log('CLARITY_FRAMES_PASS: frame A puts one plain purpose line under each of the four home tiles (same button, under the label, painted, inside the tile) and translates it live; frames B–E each say what the place is for in one sentence, offer exactly one reachable primary action painted with the theme accent (and no other accent-filled control on the first screen), demote their secondary controls without removing any, and show their real empty state, at 1280x800 and 720x480, with the minimum height fitting Research without section scroll, clamping or a clipped tool row; Research states its purpose once and offers one New question; Literature Review shows one empty state; the review primary really opens the secretary composer; the claim-check sheet sits closed on one line with a stable id and draws no form until opened; Korean covers every registered string in both directions; and the follow-ups hold (live idle-hint and button relabelling, tile and view sharing one title in both languages, one Korean for Writing, the hidden tab strip hidden by its own attribute, the busy guard on #settings-end, and Home dropping the surface mode with "nothing open" as its own sentinel through double-click, Escape-during-expansion and Library→Home→tile round trips; and the same Home contract under real motion, where the reveal is sampled frame by frame: returning Home closes every surface for real (not just its mode class), at most one surface is painted and elementFromPoint never reaches the previous one during Library→Home→Research, Writing→Home→Library or Review→Home→Research, the arriving workspace is inert until it settles, and Escape inside the real 320 ms window cancels the reveal and returns Home; and the same real motion on the way out, where the dissolve back to Home is sampled frame by frame: for Library→Home, Writing→Home and Review→Home the surface that was left is what is painted in every frame of the fade and the bare Research scaffold never shows through, the menu is already drawn underneath, and once each dissolve settles every surface carries .hidden with the shell reporting "nothing open").');
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
