// macOS visual foundation, checked as source: no browser, no network, no GUI.
// The sheet is presentation only, so the checks are the promises the sheet makes:
// one token set per theme, the macOS type and control measures, the system font
// stack, no ornamental paint, and rules whose specificity can actually beat the
// shipped sheets it corrects.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const css = await readFile(new URL('../frontend/macos_theme.css', import.meta.url), 'utf8');
const lower = css.toLowerCase();

// (1) One token set per theme. Every colour the app draws comes from these.
const light = css.slice(css.indexOf(':root {'), css.indexOf(':root[data-theme="dark"]'));
const dark = css.slice(css.indexOf(':root[data-theme="dark"]'));
const tokens = ['--bg', '--surface', '--surface-2', '--surface-3', '--text', '--muted',
  '--line', '--line-strong', '--accent', '--fail', '--warn', '--ok'];
for (const token of tokens) {
  assert.ok(new RegExp(`${token}:\\s*#`).test(light), `light declares ${token}`);
  assert.ok(dark.includes(`${token}:`) || ['--bg', '--surface-2', '--surface-3'].some((t) => t === token),
    `dark restates ${token} or shares it with light`);
}
assert.ok(/--bg:\s*#f5f5f7/.test(light) && /--surface:\s*#ffffff/.test(light), 'light is the neutral macOS ground');
assert.ok(/--bg:\s*#1c1c1e/.test(dark) && /--surface:\s*#252527/.test(dark), 'dark is the macOS elevated ground');
assert.ok(/--accent:\s*#0069d9/.test(light) && /--accent:\s*#6aafff/.test(dark), 'one accent per theme');
assert.ok(/--on-accent:/.test(light) && /--on-accent:/.test(dark), 'the label on a filled accent control is per theme');
// No shipped accent survives: the teal and coral aliases resolve to the one accent
// and the one warning tone, so no surface keeps a second colour language.
assert.ok(/--cyan:\s*var\(--accent\)/.test(css) && /--coral:\s*var\(--warn\)/.test(css),
  'the shipped accent aliases resolve to the macOS accent');

// (2) Type and controls: the macOS measures, stated in the sheet.
for (const measure of ['--text-title: 26px', '--text-xl: 20px', '--text-lg: 17px',
  '--text-base: 15px', '--text-meta: 13px', '--radius: 7px', '--radius-panel: 10px',
  '--radius-sheet: 12px', '--control-h: 36px'])
  assert.ok(css.includes(measure), `the sheet states ${measure}`);
assert.ok(css.includes("-apple-system, BlinkMacSystemFont"), 'the system font stack is used');
assert.ok(/outline:\s*2px solid var\(--accent\)/.test(css), 'keyboard focus is one 2px blue ring');

// (3) Content foundation stays flat; macos27.css owns navigation-only glass.
// No borrowed asset or !important is permitted in either layer.
for (const banned of ['gradient(', 'backdrop-filter', 'filter: blur', '@import', 'url(',
  'text-shadow: 0', 'text-shadow: 1', '!important'])
  assert.ok(!lower.includes(banned), `macos_theme.css must not use ${banned}`);
// A shadow is allowed only on the one floating surface the sheet names.
for (const line of css.split('\n').filter((row) => row.includes('box-shadow:')))
  assert.match(line, /box-shadow:\s*(none|var\(--shadow-menu\)|var\(--ms)/,
    `only the floating menu casts a shadow: ${line.trim()}`);
assert.ok(!/#[0-9a-f]{3,8}(?![0-9a-z-])/i.test(css.slice(css.indexOf('/* --- 2.'))),
  'every colour below the token block comes from a token');

// (4) The rules exist for the surfaces this sheet owns, and beat the shipped ones.
const owned = [
  '.startup.home .startup-screen.on', '.startup.home .menu-item', '.startup.home .menu-item .menu-label',
  '#app-shell.wb-dc > main > .view-bar .view-tabs > button',
  '#app-shell.wb-dc > main > .view-bar .view-tabs > button[aria-pressed="true"]',
  '#app-shell.wb-dc .wb-dc-truth-body', '#app-shell > header',
  'h1, h2', 'th, td', 'button, input, select, textarea',
];
for (const selector of owned) assert.ok(css.includes(selector), `the sheet styles ${selector}`);
// The interior sheet sets .view-tabs button at #app-shell scope and a 3px ring;
// both are restated here with one id more, so the macOS value is what wins.
assert.ok(css.includes(':root #app-shell :focus-visible'), 'the 3px interior ring is restated at 2px');
assert.ok(css.includes('#app-shell.wb-dc > header > button'), 'the header controls are restated at the same specificity');

// (5) The console's exterior is untouched: no hardware, display-geometry or boot rule.
const rules = css.replace(/\/\*[\s\S]*?\*\//g, '');
for (const untouched of ['--display-x', '--display-y', '--display-w', '--display-h',
  '--slot-x', 'startup-hardware', 'Console_Concept_A', 'startup-card', 'dot-boot', 'boot-rule'])
  assert.ok(!rules.includes(untouched), `macos_theme.css leaves ${untouched} to its own sheet`);
// The four tile labels keep the size the user asked to keep.
assert.ok(/menu-label[\s\S]{0,200}clamp\(20px, 1\.2vw, 28px\)/.test(css),
  'the four home tile labels stay 20-28px at a normal window');

console.log('MACOS_THEME_PASS: one token set per theme, macOS type and control measures, system font, 2px focus ring, no ornamental paint, the home screen and the app chrome styled without touching the console exterior');