// Segmentation overlay "(server)" gate regression: the "(server)" wording may
// render ONLY when BOTH o.source_dimensions_origin==='server_decoded' AND
// typeof o.geometry.in_bounds==='boolean'. Geometry without the server origin
// falls back to the client-side segBoxStatus wording. No browser, no network.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
    files: [], dataset: {},
    classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
  });
  return nodes.get(id);
};
const context = vm.createContext({
  location: { search: '' }, URLSearchParams, API: 'http://localhost:8123',
  S: { project: null, question: null, cq: null, task: null, epoch: 0, busy: false, pending: [], pack: 'social_science' },
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  call: async () => { throw new Error('no network in segmentation overlay test'); },
  action: async (fn) => fn(0), current: () => true,
  showError: () => {}, show: () => {},
});
vm.runInContext(await readFile(new URL('../frontend/ai.js', import.meta.url), 'utf8'), context);
const render = (objects) => {
  context.jobs_input = [{ id: 'j-seg-1', status: 'completed', reservation_usd: 0.01,
    result: { billing_status: 'NOT_VERIFIED', usage_complete: true, stages: [],
      outputs: { objects, source_asset: { id: 'a', sha256: 's' } } } }];
  context.receipts_input = undefined;
  return vm.runInContext("renderAIJobs(jobs_input, receipts_input); $('ai-results').innerHTML", context);
};
const box = { x1: 0, y1: 0, x2: 10, y2: 10, w: 100, h: 100 };

// (1) Geometry present but server origin missing -> NO "(server)" label;
// falls back to the existing client-side wording ("in bounds").
let out = render([{ object_id: 'A', label: 'alpha', box, geometry: { in_bounds: true } }]);
assert.ok(!out.includes('(server)'), 'geometry without server origin must not render (server)');
assert.ok(out.includes('in bounds'), 'geometry without server origin falls back to client-side wording');

// (2) Geometry present WITH server origin -> "(server)" label correct for
// both in_bounds true and false.
out = render([
  { object_id: 'B', label: 'beta', box, source_dimensions_origin: 'server_decoded', geometry: { in_bounds: true } },
  { object_id: 'C', label: 'gamma', box, source_dimensions_origin: 'server_decoded', geometry: { in_bounds: false } },
]);
assert.ok(out.includes('in bounds (server)'), 'server origin + in_bounds true renders in bounds (server)');
assert.ok(out.includes('out of range (server)'), 'server origin + in_bounds false renders out of range (server)');

// (3) No geometry at all -> fallback wording, never "(server)".
out = render([{ object_id: 'D', label: 'delta', box }]);
assert.ok(!out.includes('(server)'), 'object without geometry must not render (server)');
assert.ok(out.includes('in bounds'), 'object without geometry falls back to client-side wording');

console.log('SEGMENTATION_OVERLAY_PASS: (server) only with server_decoded origin + boolean in_bounds; fallback otherwise');
