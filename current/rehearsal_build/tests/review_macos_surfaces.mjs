// macOS interior surfaces, checked as source: no browser, no server, no GUI.
// frontend/macos_surfaces.css restyles the screens a researcher works in. The
// checks below are the promises that sheet makes, in the terms this file can
// prove without rendering: it draws only the surfaces it owns, it names handles
// that really exist in the shipped markup or scripts, it overrides the older
// layers by specificity rather than by force, it never hides or disables
// anything, it keeps every colour on a shared token, and it does not repaint the
// console exterior.
import { readFile, readdir } from 'node:fs/promises';
import assert from 'node:assert/strict';

const frontend = new URL('../frontend/', import.meta.url);
const read = (name) => readFile(new URL(name, frontend), 'utf8');
const [css, html, tokens, interiorResearch, interiorSecretary, interiorSettings] = await Promise.all([
  read('macos_surfaces.css'), read('index.html'), read('macos_theme.css'),
  read('interior_research.css'), read('interior_secretary.css'), read('interior_settings.css'),
]);
// Every script and page the app ships is a source of handles: a name this sheet
// addresses has to be written by the markup or built by one of them.
const pages = (await readdir(frontend)).filter((name) => /\.(js|html)$/.test(name));
const source = (await Promise.all(pages.map(read))).join('\n');
const rules = css.replace(/\/\*[\s\S]*?\*\//g, '');
const lower = css.toLowerCase();

// Handles, collected the three ways this codebase writes them: id attributes in
// markup, class attributes, and the quoted class names the DOM helpers take
// (`node('div','is-shell')`, `button(label,'dw-trigger',testid)`). Test ids are
// collected separately: they are `data-testid`, never `id`, so a `#handle` that
// matches only a test id is a selector that draws nothing.
const ids = new Set([...source.matchAll(/\bid="([\w-]+)"/g)].map((match) => match[1]));
const testids = new Set([...source.matchAll(/(?:dataset\.testid|data-testid)\s*=\s*['"`]([^'"`]+)/g)]
  .map((match) => match[1]));
const classes = new Set([...source.matchAll(/class="([^"]+)"/g)]
  .flatMap((match) => match[1].split(/\s+/)).filter(Boolean));
for (const match of source.matchAll(/className\s*=\s*['"`]([^'"`]+)/g))
  for (const part of match[1].split(/\s+/)) if (/^[\w-]+$/.test(part)) classes.add(part);
for (const match of source.matchAll(/classList\.(?:add|toggle|remove)\(\s*['"`]([\w-]+)/g))
  classes.add(match[1]);
// A helper call passes the class as a quoted literal in its argument list
// (`node('div','is-shell')`), so quoted tokens count as built - but only the
// ones that are not another kind of handle. A token that is really an id or a
// data-testid is not a class, which is how `#citation-status` came to be written
// as `.citation-status`: the rule matched nothing.
for (const match of source.matchAll(/'([\w-]+(?:\s+[\w-]+)*)'|"([\w-]+(?:\s+[\w-]+)*)"/g))
  for (const part of (match[1] || match[2] || '').split(/\s+/))
    if (/^[a-z][\w-]*$/.test(part) && !ids.has(part) && !testids.has(part)) classes.add(part);

// (1) The sheet is the interior layer, and index.html loads it after the tokens.
const order = ['macos_theme.css', 'macos_surfaces.css']
  .map((name) => html.indexOf(`href="${name}"`));
assert.ok(order.every((position) => position > 0), 'both macOS sheets are linked');
assert.ok(order[1] > order[0], 'macos_surfaces.css is loaded after macos_theme.css');
for (const later of ['style.css', 'interior_research.css', 'interior_secretary.css',
  'interior_settings.css', 'declutter_shell.css', 'declutter_writing.css'])
  assert.ok(html.indexOf(`href="${later}"`) < order[1], `${later} loads before the macOS layer`);

// (2) Presentation only. The single hidden-state guard preserves the existing
// script's view state against the older writing grid's higher specificity.
const hiddenGuard = '#app-shell.wb-dc #latex-view.dw-writing.hidden { display: none; }';
assert.ok(rules.includes(hiddenGuard));
const stateSafeRules = lower.replace(hiddenGuard.toLowerCase(), '');
assert.ok(!stateSafeRules.includes('display: none'), 'no other content is hidden');
for (const banned of ['!important', 'visibility: hidden',
  'pointer-events: none', 'tabindex', 'outline: none', 'outline: 0'])
  assert.ok(!lower.includes(banned), `macos_surfaces.css must not use ${banned}`);
// The only animation left alone is the reduced-motion opt-out, which removes one.
assert.equal((rules.match(/@media/g) || []).length, 2, 'two media blocks: narrow width, reduced motion');

// (3) Content stays flat and no asset is borrowed. macos27.css alone owns
// navigation glass and its solid fallbacks; the content sheet retains its bans.
const navigation = await read('macos27.css');
assert.match(navigation, /@supports\s*\(\(backdrop-filter:/,
  'glass progressively enhances the solid navigation fallback');
assert.match(navigation, /@media \(prefers-reduced-transparency: reduce\)[\s\S]*background: var\(--surface\);[\s\S]*backdrop-filter: none;/,
  'reduced transparency restores opaque navigation');
for (const banned of ['gradient(', '@import', 'url(', 'text-shadow'])
  assert.ok(!navigation.includes(banned), `navigation does not borrow assets or use ${banned}`);
for (const rule of navigation.matchAll(/([^{}]+)\{([^{}]*backdrop-filter:[^{}]*)\}/g)) {
  assert.ok(!/#(?:result|decision|latex|library)|>\s*section|home-bubble/.test(rule[1]),
    'filter effects never paint research content, cards or writing paper');
}
for (const banned of ['gradient(', 'backdrop-filter', 'filter: blur', '@import',
  'url(', 'text-shadow', 'glow'])
  assert.ok(!lower.includes(banned), `macos_surfaces.css must not use ${banned}`);
for (const line of css.split('\n').filter((row) => row.includes('box-shadow:')))
  assert.match(line, /box-shadow:\s*(none|var\(--shadow-menu\)|0 \d+px \d+px rgb\(0 0 0 \/ \.\d+\))/,
    `only a floating surface casts a shadow: ${line.trim()}`);
// A generated glyph would be content in a ::before/::after; the sheet has none.
assert.equal((rules.match(/content:\s*["']/g) || []).length, 0,
  'no rule invents a glyph with generated content');

// (4) Every colour is a shared token or a transparency of black. No literal hex,
//     no invented palette, and the theme's own variables are what is used.
const withoutComments = rules;
assert.deepEqual(withoutComments.match(/(?<![\w&])#[0-9a-f]{3,8}\b/gi) || [], [],
  'no literal colour below the token block');
const tokensDeclared = new Set((tokens.match(/--[\w-]+(?=\s*:)/g) || []));
const used = new Set((withoutComments.match(/var\((--[\w-]+)\)/g) || [])
  .map((use) => use.slice(4, -1)));
const local = new Set((withoutComments.match(/(--ms-[\w-]+)(?=\s*:)/g) || []));
for (const token of used) {
  if (local.has(token)) continue;
  const known = tokensDeclared.has(token)
    || ['--shadow-menu', '--on-accent', '--font', '--mono'].includes(token);
  assert.ok(known, `${token} is defined by macos_theme.css, not invented here`);
}

// (5) The type and control ladder the brief fixes, stated once in section 1.
for (const measure of ['--ms-gutter: 24px', '--ms-control: 36px',
  '--ms-radius-control: 7px', '--ms-radius-panel: 10px', '--ms-radius-sheet: 12px'])
  assert.ok(css.includes(measure), `the sheet states ${measure}`);
// Body 15 and meta 13 come from the token sheet; the surfaces only reference them.
assert.ok(/font-size: var\(--text-sm\)/.test(css) && /font-size: var\(--text-xs\)/.test(css),
  'body and meta step come from the shared tokens');

// (6) Specificity, not force: every rule names an id, and the two layers the
//     brief names are overridden by adding one more id, never by !important.
const blocks = [...withoutComments.matchAll(/([^{}]+)\{/g)]
  .map((match) => match[1].trim()).filter((selector) => !selector.startsWith('@'));
assert.ok(blocks.length > 100, `the sheet carries the interior rules (${blocks.length})`);
for (const block of blocks)
  for (const selector of block.split(','))
    assert.ok(selector.includes('#'), `every selector is scoped to an id: ${selector.trim()}`);
// #app-shell.wb-dc is the app shell the interior sheets address, one id more than
// the shipped bare #app-shell, and #settings-layer #settings-sheet is one id more
// than #settings-sheet, so both beat the layers they correct.
assert.ok(/#app-shell\.wb-dc/.test(rules), 'the app shell is addressed as #app-shell.wb-dc');
assert.ok(/#settings-layer #settings-sheet/.test(rules), 'the settings sheet is addressed through its layer');

// (7) Two focus rings the older sheets raised to 3px are restated at the macOS
//     2px, at a specificity that wins: the settings sheet, and the shell.
assert.ok(/#settings-layer #settings-sheet :focus-visible \{ outline: 2px solid var\(--accent\)/.test(css),
  'the settings ring is 2px where interior_settings.css set 3px');
assert.ok(interiorSettings.includes('#settings-sheet :focus-visible { outline: 3px solid'),
  'that 3px rule still exists, so the restatement is what corrects it');
assert.ok(interiorResearch.includes('outline: 3px solid') && interiorSecretary.includes('outline: 3px solid'),
  'the interior sheets still raise the ring inside the shell');
assert.ok(tokens.includes(':root #app-shell :focus-visible'), 'macos_theme.css corrects the shell ring');

// (8) A hidden view stays hidden. The citation composer sets display:grid, so it
//     is scoped with :not(.hidden); without that the sheet would out-specify the
//     .hidden class those views are closed by.
assert.ok(/#citation-view:not\(\.hidden\)/.test(css), 'the composer grid respects .hidden');
for (const line of rules.split('\n').filter((row) => /display:\s*(grid|flex|inline-flex|block)/.test(row)))
  assert.ok(!/^#app-shell\.wb-dc (?:#\w+-view|\#\w+) \{/.test(line.trim()),
    `a display rule on a toggled view needs :not(.hidden): ${line.trim()}`);
// Every view the markup closes with .hidden and this sheet names in a display
// rule is either unaddressed or guarded.
for (const id of ['citation-view', 'library-view', 'secretary-review-view'])
  assert.ok(!new RegExp(`#${id} \\{[^}]*display:`).test(rules), `${id} is never given a raw display`);

// (9) Every handle the sheet names exists in the shipped page or is built by a
//     shipped script. A selector for a node that does not exist draws nothing,
//     and a `#name` that only exists as a data-testid is exactly that mistake.
const selectorsOnly = rules.split(/\}/).map((chunk) => chunk.split(/\{/)[0]).join('\n');
for (const id of new Set([...selectorsOnly.matchAll(/#([\w-]+)/g)].map((match) => match[1]))) {
  assert.ok(ids.has(id), `#${id} is a real id in the shipped markup or scripts`);
  // A test id is a data-testid, never an id: reaching one as `#name` draws
  // nothing and silently drops the rule it was meant to carry. A node may carry
  // both (`#latex-view` does), and then the id is the correct handle.
  assert.ok(!testids.has(id) || ids.has(id), `#${id} is a data-testid with no id: use [data-testid="${id}"]`);
}
for (const name of new Set([...selectorsOnly.matchAll(/\.([\w-]+)/g)].map((match) => match[1]))) {
  assert.ok(classes.has(name), `.${name} is a real class in the shipped markup or scripts`);
  assert.ok(!ids.has(name) || classes.has(name), `.${name} is addressed as the id it is`);
}
// The tone classes on the two status lines are set by classList on nodes looked
// up by id, so the sheet must address both as ids.
for (const status of ['#citation-status', '#secretary-review-status'])
  assert.ok(css.includes(status), `${status} is addressed as the id its script uses`);
for (const tone of ['.ok', '.fail']) {
  assert.ok(css.includes(tone), `the ${tone} tone the scripts set has a rule`);
}
// Tone is never colour alone: each tone rule carries a leading rule as well.
const failRule = css.slice(css.indexOf('#citation-status.fail'), css.indexOf('#citation-status.ok'));
assert.ok(/border-left: 2px solid var\(--fail\)/.test(failRule), 'a refusal also carries a rule, not just red ink');

// (10) The controls the review suites measure keep their boxes. The research
//      tool row, the writing surface and the settings close control are 44px at
//      every width, and the panel toggle - which shipped at 28px - is raised to
//      the same floor rather than lowered.
assert.ok(/--ms-control: 36px/.test(css), 'the desktop control is 36px');
for (const [name, pattern] of [
  ['the research tools', /#research-tools button[\s\S]{0,400}min-height: 44px/],
  ['the writing surface', /#latex-view button[\s\S]{0,200}min-height: 44px/],
  ['the settings close control', /\.settings-close \{\s*width: 44px;\s*height: 44px;\s*min-height: 44px/],
])
  assert.ok(pattern.test(css), `${name} keep the 44px box`);
// The toggle is a flex child, so flex-basis - not width - is what decides its
// main size: all four have to say 44px or the target shrinks anyway.
const toggleRule = /#app-shell\.wb-dc > main > nav \.panel-toggle,[\s\S]{0,300}width: 44px;\s*min-height: 44px;\s*height: 44px;\s*flex: 0 0 44px/;
assert.ok(toggleRule.test(css), 'the panel toggle is a 44px target, not the 28px box it shipped with');
assert.ok(!/\.panel-toggle \{[^}]*height: 28px/.test(css), 'no rule leaves the toggle under the floor');

// (11) The five working surfaces are the ones the app has, and each is drawn: no
//      invented screen, no second counter, no new card.
for (const surface of ['#app-shell.wb-dc #research-intro', '#app-shell.wb-dc #library-view',
  '#app-shell.wb-dc #secretary-review-view', '#app-shell.wb-dc #citation-view:not(.hidden)',
  '#app-shell.wb-dc #latex-view.dw-writing', '#settings-layer #settings-sheet'])
  assert.ok(css.includes(surface), `the sheet draws ${surface}`);

// (12) The console's exterior is untouched: no hardware, display geometry, boot
//      sequence, cartridge animation or home tile rule lives here.
for (const untouched of ['--display-x', '--display-y', '--display-w', '--display-h',
  '--slot-x', 'startup-hardware', 'startup-card', 'dot-boot', 'startup-menu',
  '.menu-item', 'menu-label', 'Console_Concept'])
  assert.ok(!rules.includes(untouched), `macos_surfaces.css leaves ${untouched} to its own sheet`);
assert.ok(!/#startup|\.startup/.test(rules), 'the start screen is not repainted from this sheet');

// (13) Tone is never colour alone. citations.js and secretary_review.js set .ok
//      and .fail on lines they look up by id, and no shipped sheet styles either
//      class; here each also takes a rule, so tone is not carried by ink alone.
for (const tone of ['#citation-status.fail', '#citation-status.ok',
  '#secretary-review-status.fail', '#secretary-review-status.ok'])
  assert.ok(css.includes(tone), `${tone} is drawn`);
for (const tone of ['fail', 'ok']) {
  const block = new RegExp(`#(?:citation|secretary-review)-status\\.${tone}[^{]*\\{([^}]*)\\}`).exec(rules);
  assert.ok(block && /border-left/.test(block[1]), `${tone} carries a leading rule as well as its ink`);
}
// The refusal and success inks come from the token sheet, never from a literal.
assert.ok(/color: var\(--fail\)/.test(css) && /color: var\(--ok\)/.test(css),
  'both tones read their colour from the theme');

// (14) Density is not one size fits all: the desktop is 36px, and the narrow
//      layout returns the drawers and the form to 44px.
assert.ok(/@media \(max-width: 900px\)/.test(css), 'the narrow layout is stated');
const narrow = css.slice(css.indexOf('@media (max-width: 900px)'));
assert.ok(/min-height: 44px/.test(narrow), 'the narrow layout raises controls to 44px');
assert.ok(/grid-template-columns: minmax\(0, 1fr\)/.test(narrow), 'the citation composer stacks when narrow');

// (15) The sheet says what it owns and what it leaves alone, so the next reader
//      can tell which layer to edit.
assert.match(css.slice(0, 2000), /Owned here:/, 'the header names the surfaces this sheet owns');
assert.match(css.slice(0, 2000), /Not here:/, 'the header names the layer that is not');
assert.match(css.slice(0, 2000), /No JavaScript, node, id, label, handler, order or state is touched/,
  'the header states the presentation-only contract');
assert.ok(!/\u2014/.test(css), 'no U+2014 in the authored prose');

console.log('MACOS_SURFACES_PASS: loaded last, presentation only, token colours, id-scoped overrides, 2px rings, 44px floors, hidden views respected, real handles only, calm tones, console untouched');
