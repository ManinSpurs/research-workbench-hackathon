// The console is a boot frame, not a permanent frame.
// Once boot finishes and the home menu opens, the console exterior (the approved
// artwork and the cartridge bay) must be gone from the screen and the inner
// display must be the whole window. This runs the real frontend in headless
// Chromium against scripted HTTP fixtures: no Electron, no network, no model.
//
// It also pins what must NOT regress: boot still draws the console, the four
// menus and the settings gear are real controls a pointer can hit, a narrow
// window keeps all four, and reduced motion settles the same state.
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

// Serve the real frontend directory. home_expand.css is injected with addStyleTag
// because index.html is not owned by this change: the sheet has to be linked by
// the Lead, and this test proves the sheet itself, not the link.
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
const pages = [];

// Only the endpoints the home screen reads. Every other path answers {} so no
// request can leave this process.
async function routeFixtures(page) {
  await page.route('http://api.test/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const json = (data) => route.fulfill({ json: data });
    if (pathname === '/domain-packs') return json({ packs: [{ pack_id: 'social_science', version: '1.0.0', title: 'Social Science' }] });
    if (pathname === '/chips') return json({ chips: [] });
    if (pathname === '/projects') return json({ projects: [] });
    if (pathname === '/briefing') return json({ rows: [] });
    if (pathname.endsWith('/profile')) return json({ variables: [], computed_by: 'ordinary code, no model call' });
    if (pathname.endsWith('/clarifications')) return json({ clarifications: [] });
    if (pathname.endsWith('/secretary-turns')) return json({ turns: [] });
    return json({});
  });
}

// A remembered field seats the cartridge and lands on the home menu, which is the
// state under test. `signal` lets a caller stop the run at the boot screen.
async function open({ width = 1280, height = 800, reduced = false, stopAtBoot = false } = {}) {
  const page = await browser.newPage({ viewport: { width, height },
    reducedMotion: reduced ? 'reduce' : 'no-preference' });
  pages.push(page);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await routeFixtures(page);
  await page.addInitScript((boot) => {
    try { window.localStorage.setItem('wb.pack', 'social_science'); } catch (_) {}
    if (boot) { window.__stopAtBoot = true; }
  }, stopAtBoot);
  await page.goto(`${origin}/?api=http://api.test`);
  await page.addStyleTag({ path: path.join(frontend, 'home_expand.css') });
  if (stopAtBoot) {
    // The boot screen is the console's own state; hold it there.
    await page.waitForSelector('#startup-boot:not([hidden])', { timeout: 3000 });
    return { page, errors };
  }
  await page.waitForSelector('#startup-menu:not([hidden])');
  // The screen's own open animation is 140 ms; wait it out so measurements are of
  // the settled state.
  await page.waitForTimeout(400);
  return { page, errors };
}

const box = (node) => { const r = node.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; };
const boxes = () => ({
  viewport: { width: innerWidth, height: innerHeight },
  screen: box(document.getElementById('startup-screen')),
  menu: box(document.getElementById('startup-menu')),
  body: box(document.querySelector('#startup-menu .home-body')),
});
// What the console exterior is made of, as actually painted.
const exterior = () => ({
  hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
  card: getComputedStyle(document.getElementById('startup-card')).visibility,
  hardwareRects: document.querySelector('.startup-hardware').getClientRects().length,
});
// The centre of each control, resolved the way a pointer would: whatever is
// painted on top there is what a click would reach.
const hitTargets = () => {
  const probe = (node) => {
    const r = node.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!at) return null;
    const tile = at.closest('[data-menu]');
    if (tile) return tile.getAttribute('data-menu');
    const control = at.closest('button, select');
    return control ? control.id : at.id || at.tagName.toLowerCase();
  };
  return {
    tiles: [...document.querySelectorAll('#home-menu [data-menu]')].map(probe),
    settings: probe(document.getElementById('home-settings')),
    project: probe(document.getElementById('home-project')),
  };
};

try {
  // --- 1. The home menu owns the window; the console exterior is not painted.
  {
    const { page, errors } = await open();
    const seen = await page.evaluate(() => {
      const b = (node) => { const r = node.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; };
      return {
        viewport: { width: innerWidth, height: innerHeight },
        screen: b(document.getElementById('startup-screen')),
        menu: b(document.getElementById('startup-menu')),
        body: b(document.querySelector('#startup-menu .home-body')),
        startupClasses: document.getElementById('startup').className,
        hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
        card: getComputedStyle(document.getElementById('startup-card')).visibility,
        idle: document.querySelector('.home-chip-idle').hidden,
        portalHidden: document.getElementById('portal').hidden,
        overflowX: document.documentElement.scrollWidth > innerWidth,
        overflowY: document.documentElement.scrollHeight > innerHeight,
      };
    });
    assert.ok(/\bhome\b/.test(seen.startupClasses), 'the home state is on the console');
    assert.equal(seen.hardware, 'hidden', 'the console artwork is not painted on the home screen');
    assert.equal(seen.card, 'hidden', 'the cartridge bay is not painted on the home screen');
    // The inner display is the app window, to the pixel.
    for (const key of ['left', 'top']) {
      assert.equal(Math.round(seen.screen[key]), 0, `the inner display starts at the window ${key}`);
    }
    assert.equal(Math.round(seen.screen.width), seen.viewport.width, 'the inner display is full width');
    assert.equal(Math.round(seen.screen.height), seen.viewport.height, 'the inner display is full height');
    assert.deepEqual({ ...seen.menu, left: Math.round(seen.menu.left), top: Math.round(seen.menu.top), width: Math.round(seen.menu.width), height: Math.round(seen.menu.height) },
      { left: 0, top: 0, width: seen.viewport.width, height: seen.viewport.height },
      'the home menu fills the window instead of a bezel-sized box');
    // No huge empty area: the panel is sized to what it draws, not a strip in a corner.
    assert.ok(seen.body.width >= seen.viewport.width * 0.9, 'the home body uses the window width');
    assert.ok(seen.body.height >= seen.viewport.height * 0.75, 'the home body uses the window height');
    assert.equal(seen.idle, true, 'the idle console chip stays out of the way on the home screen');
    assert.equal(seen.portalHidden, true, 'the workspace is still closed until a menu is chosen');
    assert.equal(seen.overflowX, false, 'the home screen does not scroll sideways');
    assert.equal(seen.overflowY, false, 'the home screen does not scroll vertically');
    assert.deepEqual(errors, [], 'no script error while reaching the home screen');

    // --- 2. The four menus and the settings gear are real, hittable controls.
    const hits = await page.evaluate(hitTargets);
    assert.deepEqual(hits.tiles, ['library', 'research', 'writing', 'review'],
      'each of the four tiles is the element a pointer reaches at its own centre, in workflow order');
    assert.equal(hits.settings, 'home-settings', 'the settings control is reachable, not covered by the layout');
    assert.equal(hits.project, 'home-project', 'the project selector is reachable');
    // A real pointer click at a tile's own centre must reach that tile and open
    // its view: the grid is laid out, not merely styled.
    const target = await page.evaluate(() => {
      const r = document.querySelector('#home-menu [data-menu="library"]').getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    });
    await page.mouse.click(target.x, target.y);
    await page.waitForFunction(() => document.body.classList.contains('workbench-open'));
    assert.equal(await page.evaluate(() => document.getElementById('startup').hidden), true,
      'clicking a tile actually opens the workspace, so the control is live');

    // --- 3. Choosing a menu reveals the workspace at the window, and the console
    //        does not come back as a frame around it.
    const workspace = await page.evaluate(() => {
      const b = (node) => { const r = node.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; };
      return {
        portal: b(document.getElementById('portal')),
        portalHidden: document.getElementById('portal').hidden,
        viewport: { width: innerWidth, height: innerHeight },
        shellHidden: document.getElementById('startup').hidden,
        exteriorPainted: document.querySelector('.startup-hardware').getClientRects().length > 0,
      };
    });
    assert.equal(workspace.portalHidden, false, 'the workspace is shown');
    assert.equal(Math.round(workspace.portal.width), workspace.viewport.width, 'the workspace is full width');
    assert.equal(Math.round(workspace.portal.height), workspace.viewport.height, 'the workspace is full height');
    assert.equal(workspace.shellHidden, true, 'the console shell is dismissed, not left behind the workspace');
    assert.equal(workspace.exteriorPainted, false, 'the console exterior paints no box over the workspace');

    // --- 4. Escape returns to the menu, which still fills the window.
    await page.keyboard.press('Escape');
    await page.waitForSelector('#startup-menu:not([hidden])');
    await page.waitForFunction(() => document.getElementById('portal').hidden);
    const returned = await page.evaluate(() => ({
      hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
      screen: (() => { const r = document.getElementById('startup-screen').getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }; })(),
      viewport: { width: innerWidth, height: innerHeight },
      tiles: [...document.querySelectorAll('#home-menu [data-menu]')].map((tile) => {
        const r = tile.getBoundingClientRect();
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return at && at.closest('[data-menu]') ? at.closest('[data-menu]').getAttribute('data-menu') : null;
      }),
      escape: document.getElementById('portal').style.opacity,
    }));
    assert.equal(returned.hardware, 'hidden', 'the exterior stays gone after returning to the menu');
    assert.equal(returned.screen.width, returned.viewport.width, 'the returned display is still the window');
    assert.deepEqual(returned.tiles, ['library', 'research', 'writing', 'review'],
      'all four menus are reachable again after returning, in workflow order');
    assert.equal(returned.escape, '', 'returning leaves no stale inline opacity on the workspace');

    // Re-opening after a return must still show the workspace: a finished
    // fill:forwards fade would otherwise leave it permanently transparent.
    await page.mouse.click(...await page.evaluate(() => {
      const r = document.querySelector('#home-menu [data-menu="library"]').getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    }));
    await page.waitForFunction(() => document.body.classList.contains('workbench-open'));
    const reopened = await page.evaluate(() => ({
      opacity: getComputedStyle(document.getElementById('portal')).opacity,
      width: Math.round(document.getElementById('portal').getBoundingClientRect().width),
      viewportWidth: innerWidth,
    }));
    assert.equal(reopened.opacity, '1', 're-opening the workspace after a return is fully opaque');
    assert.equal(reopened.width, reopened.viewportWidth, 'and it is at the window width again');

    // --- 4b. Changing the research field does go back to the drawn console,
    //         because the picker lives on it.
    await page.evaluate(() => window.WB_STARTUP.change());
    await page.waitForFunction(() => !document.getElementById('startup-choices').hidden);
    const picker = await page.evaluate(() => ({
      hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
      screen: Math.round(document.getElementById('startup-screen').getBoundingClientRect().width),
      viewport: innerWidth,
      home: document.getElementById('startup').classList.contains('home'),
    }));
    assert.equal(picker.hardware, 'visible', 'the picker is drawn on the console, so its exterior comes back');
    assert.ok(picker.screen < picker.viewport * 0.9, 'the picker display is the console-sized inner screen again');
    assert.equal(picker.home, false, 'the home state is off while choosing a field');
  }

  // --- 5. Boot still draws the console: this change removes the frame only after
  //        the boot state ends, never during it.
  {
    const { page, errors } = await open({ stopAtBoot: true });
    const boot = await page.evaluate(() => {
      const hardware = document.querySelector('.startup-hardware');
      const screen = document.getElementById('startup-screen').getBoundingClientRect();
      const b = document.getElementById('startup-boot');
      return {
        hardware: getComputedStyle(hardware).visibility,
        card: getComputedStyle(document.getElementById('startup-card')).visibility,
        displayWidth: Math.round(screen.width),
        viewportWidth: innerWidth,
        bootVisible: !b.hidden,
        startupClasses: document.getElementById('startup').className,
        menuHidden: document.getElementById('startup-menu').hidden,
      };
    });
    assert.equal(boot.hardware, 'visible', 'the console artwork is still drawn during boot');
    assert.ok(boot.card !== 'hidden', 'the cartridge bay is still drawn during boot');
    assert.ok(boot.displayWidth < boot.viewportWidth * 0.9,
      'the boot display is still the console-sized inner screen, not the window');
    assert.equal(boot.bootVisible, true, 'the boot sequence is on screen');
    assert.ok(!/\bhome\b/.test(boot.startupClasses), 'the home state is not applied during boot');
    assert.equal(boot.menuHidden, true, 'the menu has not opened yet');
    assert.deepEqual(errors, [], 'no script error during boot');
  }

  // --- 6. Narrow window: the same contract at a phone-sized and a small-window size.
  for (const [width, height] of [[720, 500], [390, 780]]) {
    const { page, errors } = await open({ width, height });
    const narrow = await page.evaluate(() => ({
      viewport: { width: innerWidth, height: innerHeight },
      screen: (() => { const r = document.getElementById('startup-screen').getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }; })(),
      hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
      tiles: [...document.querySelectorAll('#home-menu [data-menu]')].map((tile) => {
        const r = tile.getBoundingClientRect();
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {
          menu: at && at.closest('[data-menu]') ? at.closest('[data-menu]').getAttribute('data-menu') : null,
          width: Math.round(r.width), height: Math.round(r.height),
        };
      }),
      settings: (() => {
        const r = document.getElementById('home-settings').getBoundingClientRect();
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return at ? (at.closest('button') || at).id || at.tagName.toLowerCase() : null;
      })(),
      overflowX: document.documentElement.scrollWidth > innerWidth,
    }));
    assert.equal(narrow.hardware, 'hidden', `${width}px: the exterior is gone`);
    assert.equal(narrow.screen.width, narrow.viewport.width, `${width}px: the display is full width`);
    assert.equal(narrow.screen.height, narrow.viewport.height, `${width}px: the display is full height`);
    assert.deepEqual(narrow.tiles.map((tile) => tile.menu), ['library', 'research', 'writing', 'review'],
      `${width}px: all four menus stay reachable`);
    for (const tile of narrow.tiles) {
      assert.ok(tile.width >= 60 && tile.height >= 40, `${width}px: tiles stay clickable (${tile.width}x${tile.height})`);
    }
    assert.equal(narrow.settings, 'home-settings', `${width}px: settings stays reachable`);
    assert.equal(narrow.overflowX, false, `${width}px: no sideways scroll`);
    assert.deepEqual(errors, [], `${width}px: no script error`);
  }

  // Short native windows must keep the enlarged titles inside their tiles.
  {
    const { page } = await open({ width: 720, height: 350, reduced: true });
    for (const lang of ['ko', 'en']) for (const height of [350, 400]) {
      await page.setViewportSize({ width: 720, height });
      await page.evaluate(lang => WB_I18N.set(lang), lang);
      const fits = await page.locator('#home-menu .menu-item').evaluateAll(tiles => tiles.map(tile => {
        const outer = tile.getBoundingClientRect(), label = tile.querySelector('.menu-label');
        const inner = label.getBoundingClientRect();
        return inner.top >= outer.top && inner.bottom <= outer.bottom &&
          inner.left >= outer.left && inner.right <= outer.right &&
          label.scrollHeight <= label.clientHeight + 1 && label.scrollWidth <= label.clientWidth + 1;
      }));
      assert.equal(fits.length, 4);
      assert.ok(fits.every(Boolean), `${lang} titles fit at 720x${height}`);
    }
  }
  // Desktop title size and narrow/short composer reachability are separate
  // invariants: a label-fit-only probe misses a clipped Send/error below it.
  {
    const { page } = await open({ width: 1280, height: 800, reduced: true });
    for (const lang of ['ko', 'en']) {
      await page.evaluate(lang => WB_I18N.set(lang), lang);
      for (const width of [721, 750, 899, 900, 1106, 1280]) {
        await page.setViewportSize({ width, height: 800 });
        const labels = await page.locator('#home-menu .menu-label').evaluateAll(es =>
          es.map(e => {
            const node = e.firstChild;
            const wordsFit = [...e.textContent.matchAll(/\S+/g)].every(match => {
              const range = document.createRange();
              range.setStart(node, match.index);
              range.setEnd(node, match.index + match[0].length);
              return range.getClientRects().length === 1 &&
                range.getBoundingClientRect().width <= e.clientWidth + 1;
            });
            return { size: parseFloat(getComputedStyle(e).fontSize), wordsFit,
              clipped: e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1 };
          }));
        assert.equal(labels.length, 4);
        assert.ok(labels.every(e => !e.clipped), `${lang} labels fit at width ${width}`);
        assert.ok(labels.every(e => e.wordsFit), `${lang} words stay intact at width ${width}`);
        if (width >= 900) assert.ok(labels.every(e => e.size >= 28), 'normal desktop titles are at least 28px');
      }
      for (const [width, height] of [[720, 480], [667, 375]]) {
        await page.setViewportSize({ width, height });
        await page.evaluate(() => {
          showError('Synthetic error: request refused.');
        });
        await page.waitForFunction(() => document.getElementById('home-error').textContent.includes('Synthetic error: request refused.'));
        const panel = page.locator('#home-secretary');
        const scrolling = await panel.evaluate(el => {
          el.scrollTop = 0;
          return { overflow: getComputedStyle(el).overflowY, needed: el.scrollHeight > el.clientHeight };
        });
        assert.ok(['auto', 'scroll'].includes(scrolling.overflow), 'the panel permits user scrolling');
        assert.ok(scrolling.needed, 'the short panel really has overflow to exercise');
        const bounds = await panel.boundingBox();
        // Wheel over the panel heading, not the nested conversation history
        // (which intentionally contains its own scroll gestures).
        await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 8);
        await page.mouse.wheel(0, 2000);
        await page.waitForFunction(() => document.getElementById('home-secretary').scrollTop > 0);
        for (const id of ['secretary-input', 'secretary-send', 'home-error']) {
          const control = page.locator('#' + id);
          assert.ok(await control.isVisible(), `${lang} ${id} exists visibly before scrolling at ${width}x${height}`);
          const reachable = await control.evaluate(el => {
            const r = el.getBoundingClientRect();
            const panel = document.getElementById('home-secretary').getBoundingClientRect();
            const top = Math.max(r.top, panel.top, 0), bottom = Math.min(r.bottom, panel.bottom, innerHeight);
            const x = (r.left + r.right) / 2, y = (top + bottom) / 2;
            const hit = document.elementFromPoint(x, y);
            return bottom > top && Boolean(hit && (el === hit || el.contains(hit)));
          });
          assert.ok(reachable, `${lang} ${id} reachable at ${width}x${height}`);
        }
      }
    }
  }
  // --- 7. Reduced motion: the same state, no open animation.
  {
    const { page, errors } = await open({ reduced: true });
    const reducedState = await page.evaluate(() => ({
      animation: getComputedStyle(document.getElementById('startup-screen')).animationName,
      hardware: getComputedStyle(document.querySelector('.startup-hardware')).visibility,
      screen: (() => { const r = document.getElementById('startup-screen').getBoundingClientRect(); return { width: Math.round(r.width), height: Math.round(r.height) }; })(),
      viewport: { width: innerWidth, height: innerHeight },
      tiles: [...document.querySelectorAll('#home-menu [data-menu]')].length,
    }));
    assert.equal(reducedState.animation, 'none', 'reduced motion skips the display open animation');
    assert.equal(reducedState.hardware, 'hidden', 'reduced motion still removes the exterior');
    assert.equal(reducedState.screen.width, reducedState.viewport.width, 'reduced motion lands on the window');
    assert.equal(reducedState.screen.height, reducedState.viewport.height);
    assert.equal(reducedState.tiles, 4, 'reduced motion keeps all four menus');
    assert.deepEqual(errors, [], 'no script error under reduced motion');
  }

  console.log('HOME_EXPAND_PASS: after boot the console exterior (approved artwork and cartridge bay) is not painted and the inner display fills the window at 1280x800, 720x500 and 390x780; the home menu fills that window with no empty frame; the four tiles, the settings gear and the project selector are the elements a pointer actually reaches and clicking a tile opens the workspace; the workspace reveals at the window size and Escape returns to the same full-window menu with no stale opacity; re-opening a menu after that return is opaque, not left transparent by a finished fade; changing the research field does restore the console for the cartridge picker; boot still draws the console and its console-sized display; reduced motion settles the same state with no animation; no script errors anywhere.');
} finally {
  for (const page of pages) await page.close().catch(() => {});
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
