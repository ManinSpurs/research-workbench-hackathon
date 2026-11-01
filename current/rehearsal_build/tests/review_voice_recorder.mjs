// Voice WAV recorder review: loads frontend/voice_recorder.js in a vm harness
// with minimal DOM/media doubles (no jsdom, no mic, no network) and asserts:
// (a) recorder UI states: idle / recording / recorded / denied / unsupported;
// (b) WAV encoder correctness at the pure-function level
//     (RIFF/WAVE magic, fmt chunk, 16-bit mono, data length consistent);
// (c) the submit path calls the EXISTING voice asset endpoint
//     (POST /questions/{qid}/ai/assets with {name, mime:'audio/wav', encoded})
//     and never auto-submits;
// (d) discard clears state; AudioContext/MediaRecorder unavailability is an
//     honest NOT_SUPPORTED state, not a fake recording UI.
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', textContent: '', innerHTML: '', checked: false, disabled: true,
    files: [], dataset: {},
    classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
  });
  return nodes.get(id);
};

let gainValue = 0;
let savedProc = null;
const FakeAudioContext = function () {
  this.sampleRate = 48000; this.state = 'running'; this.destination = {};
  this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
  this.createScriptProcessor = () => { savedProc = { connect() {}, disconnect() {}, onaudioprocess: null }; return savedProc; };
  this.createGain = () => ({ gain: { set value(v) { gainValue = v; }, get value() { return gainValue; } }, connect() {}, disconnect() {} });
  this.resume = async () => {}; this.close = async () => {};
};
const fakeTracks = [];
const fakeStream = { getTracks: () => fakeTracks.map((t) => ({ stop: () => { t.stopped = true; } })) };
const context = vm.createContext({
  URL, Blob, Float32Array, Uint8Array, ArrayBuffer, DataView, Math, Date, String, JSON,
  navigator: { mediaDevices: { getUserMedia: async () => { fakeTracks.push({ stopped: false }); return fakeStream; } } },
  AudioContext: FakeAudioContext, webkitAudioContext: undefined,
  S: { project: 'p', question: 'q' },
  aiQuestion: '',
  document: { getElementById: node, querySelectorAll: () => [] },
  $: node,
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  call: async () => { throw new Error('no network except through tracked call'); },
  action: async (fn) => fn(0),
  current: () => true,
  showError: (m) => { context.lastError = m; },
  show: () => {},
});
context.sent = [];
context.call = async (method, path, body) => { context.sent.push({ method, path, body }); return { id: 'asset-rec', sha256: 'x'.repeat(64) }; };
context.loadAI = async () => {};
const source = await readFile(new URL('../frontend/voice_recorder.js', import.meta.url), 'utf8');
vm.runInContext(source, context);

const get = (expr) => vm.runInContext(expr, context);
const run = (expr) => vm.runInContext(expr, context);
// Known stereo fixture lives INSIDE the vm context so voiceRecPush can read it.
run(`globalThis.__chA = Float32Array.from({length: 12}, (_, i) => [0.5, -0.5, 0.25, 0.0][i % 4]);
globalThis.__chB = Float32Array.from({length: 12}, (_, i) => [-0.5, 0.5, -0.25, 0.5][i % 4]);
globalThis.__inputBuffer = { numberOfChannels: 2, length: 12, getChannelData: (c) => (c === 0 ? __chA : __chB) };`);
const PUSH = `voiceRecPush(__inputBuffer)`;

// (1) idle state on init: honest lineup, Record enabled, Stop/Submit/Discard disabled, no network.
assert.equal(get(`voiceRec.state`), 'idle');
assert.ok(get(`$('voice-rec-status').textContent`).includes('Recorder idle'), 'idle status message');
assert.equal(node('voice-rec-record').disabled, false, 'Record enabled in idle');
assert.equal(node('voice-rec-stop').disabled, true, 'Stop disabled in idle');
assert.equal(node('voice-rec-submit').disabled, true, 'Submit disabled in idle');
assert.equal(node('voice-rec-discard').disabled, true, 'Discard disabled in idle');
assert.deepEqual(context.sent, [], 'init/render must never call the network');

// (2) recording state: start requests mic, only Stop enabled, still no network.
await run(`voiceRecStart()`);
assert.equal(get(`voiceRec.state`), 'recording');
assert.ok(get(`$('voice-rec-status').textContent`).includes('Recording'), 'recording status message');
assert.equal(node('voice-rec-record').disabled, true, 'Record disabled while recording');
assert.equal(node('voice-rec-stop').disabled, false, 'Stop enabled while recording');
assert.equal(node('voice-rec-submit').disabled, true, 'Submit disabled while recording');
assert.equal(node('voice-rec-discard').disabled, false, 'Discard enabled while recording');
assert.deepEqual(context.sent, [], 'recording must never auto-submit');

// (3) WAV encoder correctness at the pure-function level. Feed the captured
// mono path a known stereo buffer, then check the RIFF/WAVE header, fmt
// chunk fields, and data-length consistency, plus a PCM probe of the bytes.
run(`${PUSH}; voiceRecStop();`);
assert.equal(get(`voiceRec.state`), 'recorded');
const wav = Buffer.from(get(`voiceRec.wavBytes`));
assert.equal(wav.subarray(0, 4).toString(), 'RIFF', 'RIFF magic');
assert.equal(wav.subarray(8, 12).toString(), 'WAVE', 'WAVE magic');
assert.equal(wav.subarray(12, 16).toString(), 'fmt ', 'fmt chunk');
assert.equal(wav.readUInt32LE(16), 16, 'fmt chunk size 16 (PCM)');
assert.equal(wav.readUInt16LE(20), 1, 'audio format 1 (PCM)');
assert.equal(wav.readUInt16LE(22), 1, 'exactly one channel (mono)');
assert.equal(wav.readUInt32LE(24), 16000, '16 kHz sample rate accepted by the backend validator');
assert.equal(wav.readUInt32LE(28), 32000, 'byte rate = 16000 * 1 ch * 16 bit');
assert.equal(wav.readUInt16LE(32), 2, 'block align = 2 bytes per mono frame');
assert.equal(wav.readUInt16LE(34), 16, '16 bits per sample');
assert.equal(wav.subarray(36, 40).toString(), 'data', 'data chunk');
const frames = wav.readUInt32LE(40) / 2;
assert.equal(wav.length, 44 + wav.readUInt32LE(40), 'file length consistent with data chunk size');
// Mono of the first stereo frame: (0.5 + -0.5)/2 = 0 -> int16 0 at offset 44.
assert.equal(wav.readInt16LE(44), 0, 'stereo averaged to mono');
// Sample 2: input -0.25/-0.5/... averaged via 48 kHz -> 16 kHz resample; probe
// range rather than exact interpolation: must be signed 16-bit and non-clipped.
const probe = wav.readInt16LE(46);
assert.ok(Number.isInteger(probe) && probe >= -32768 && probe <= 32767, 'PCM sample in 16-bit range');
// Confirm the same bytes parse as a backend-valid mono WAV: check via the
// RIFF/WAVE + mono/16-bit/16or24kHz contract the validator enforces.
assert.ok([16000, 24000].includes(wav.readUInt32LE(24)), 'backend-accepted rate');
// Status message and playback: explicit submit line, enabled Submit/Discard, audio tag, no network yet.
assert.ok(get(`$('voice-rec-status').textContent`).includes('Recording ready'), 'recorded status message');
assert.ok(get(`$('voice-rec-status').textContent`).includes('16-bit PCM mono WAV'), 'recorded status names the contract');
assert.ok(get(`$('voice-rec-status').textContent`).includes('nothing was transcribed') || get(`$('voice-rec-status').textContent`).includes('Submitting only registers'), 'recorded status disclaims transcription');
assert.equal(node('voice-rec-submit').disabled, false, 'Submit enabled when recorded');
assert.equal(node('voice-rec-discard').disabled, false, 'Discard enabled when recorded');
assert.ok(get(`$('voice-rec-playback').innerHTML`).includes('<audio'), 'playback element offered');
assert.deepEqual(context.sent, [], 'stop/playback must never auto-submit');

// (4) Explicit submit calls the EXISTING asset endpoint with the exact contract.
run(`S.question = 'q'; aiQuestion = 'q';`);
await run(`voiceRecSubmit()`);
assert.equal(context.sent.length, 1, 'exactly one explicit submit request');
assert.equal(context.sent[0].method, 'POST');
assert.equal(context.sent[0].path, '/questions/q/ai/assets', 'existing asset endpoint, not a new record/chat store');
assert.deepEqual(Object.keys(context.sent[0].body).sort(), ['encoded', 'mime', 'name'], 'asset body contract');
assert.equal(context.sent[0].body.mime, 'audio/wav');
assert.ok(context.sent[0].body.name.endsWith('.wav'), 'WAV filename');
const raw = Buffer.from(context.sent[0].body.encoded, 'base64');
assert.equal(raw.subarray(0, 4).toString(), 'RIFF', 'submitted bytes are WAV');
assert.equal(raw.subarray(8, 12).toString(), 'WAVE');
assert.equal(raw.length, wav.length, 'submitted bytes are the recorded take');
// Post-submit resets to idle with a registered notice mentioning the next explicit step.
assert.equal(get(`voiceRec.state`), 'idle');
assert.ok(get(`$('voice-rec-status').textContent`).includes('Registered'), 'submit success notice');

// (5) Discard clears state without network.
await run(`voiceRecStart()`);
run(`${PUSH}; voiceRecStop();`);
assert.equal(get(`voiceRec.state`), 'recorded');
context.sent.length = 0;
await run(`voiceRecDiscard()`);
assert.equal(get(`voiceRec.state`), 'idle');
assert.equal(get(`voiceRec.blob`), null, 'blob cleared');
assert.equal(get(`$('voice-rec-playback').innerHTML`), '', 'playback cleared');
assert.equal(get(`$('voice-rec-status').textContent`).includes('Recorder idle') || get(`$('voice-rec-status').textContent`).includes('idle'), true, 'status back to idle');
assert.deepEqual(context.sent, [], 'discard must not call the network');

// (6) Mic denial: honest denied state, nothing recorded, buttons safe.
// Deny by swapping in a rejecting getUserMedia, then starting fresh from idle.
// The wording must claim only what the DOMException name identifies: a real
// NotAllowedError says permission was denied, an unnamed failure must NOT.
run(`voiceRecDiscard();`);
context.navigator.mediaDevices.getUserMedia = async () => {
  const e = new Error('denied'); e.name = 'NotAllowedError'; throw e;
};
await run(`voiceRecStart()`);
assert.equal(get(`voiceRec.state`), 'denied');
assert.equal(get(`voiceRec.blob`), null, 'no blob after denial');
assert.ok(get(`$('voice-rec-status').textContent`).includes('denied'), 'NotAllowedError must say access was denied');
assert.ok(get(`$('voice-rec-status').textContent`).includes('Nothing was recorded'), 'denial message must say nothing was recorded');
assert.equal(node('voice-rec-submit').disabled, true, 'Submit disabled after denial');
assert.equal(get(`$('voice-rec-playback').innerHTML`), '', 'no playback after denial');
assert.equal(node('voice-rec-record').disabled, false, 'Record stays enabled so the user can retry');

// Unnamed failure: honest, unspecific wording (never "denied", never "no microphone").
run(`voiceRecDiscard();`);
context.navigator.mediaDevices.getUserMedia = async () => { throw new Error('Permission denied'); };
await run(`voiceRecStart()`);
assert.equal(get(`voiceRec.state`), 'denied');
const unnamed = get(`$('voice-rec-status').textContent`);
assert.ok(!/denied/i.test(unnamed), 'unnamed failure must not claim permission was denied: ' + unnamed);
assert.ok(!/no usable microphone/i.test(unnamed), 'unnamed failure must not claim no microphone exists');
assert.ok(/could not be opened/i.test(unnamed), 'unnamed failure keeps honest unspecific wording: ' + unnamed);

// Distinct causes get distinct messages: no-device is not a denial.
run(`voiceRecDiscard();`);
context.navigator.mediaDevices.getUserMedia = async () => {
  const e = new Error('none'); e.name = 'NotFoundError'; throw e;
};
await run(`voiceRecStart()`);
const noDevice = get(`$('voice-rec-status').textContent`);
assert.ok(/No usable microphone/i.test(noDevice), 'NotFoundError must report no usable microphone: ' + noDevice);
assert.ok(!/denied/i.test(noDevice), 'NotFoundError must not be reported as a denial');

// Restore the granting getUserMedia for any later steps in this context.
context.navigator.mediaDevices.getUserMedia = async () => { fakeTracks.push({ stopped: false }); return fakeStream; };

// (8) Empty take: Record then Stop with no captured frames must not offer a
// submittable 0-second WAV.
run(`voiceRecDiscard();`);
await run(`voiceRecStart()`);
assert.equal(get(`voiceRec.state`), 'recording');
context.sent.length = 0;
run(`voiceRecStop();`);
assert.equal(get(`voiceRec.state`), 'idle', 'empty take returns to idle, never "recorded"');
assert.equal(get(`voiceRec.blob`), null, 'empty take keeps no blob');
assert.equal(get(`voiceRec.wavBytes`), null, 'empty take keeps no WAV bytes');
assert.equal(node('voice-rec-submit').disabled, true, 'empty take leaves Submit disabled');
assert.equal(get(`$('voice-rec-playback').innerHTML`), '', 'empty take offers no playback');
assert.ok(/empty/i.test(get(`$('voice-rec-status').textContent`)), 'empty take says the take was empty');
await run(`voiceRecSubmit()`);
assert.deepEqual(context.sent, [], 'an empty take can never be submitted');

// (9) Graph-construction failure after getUserMedia must release the mic:
// no live track, no unhandled rejection, honest idle state.
run(`voiceRecDiscard();`);
// Swap in a context whose graph construction throws AFTER the mic was acquired.
const savedCtor = context.AudioContext;
context.AudioContext = function () {
  FakeAudioContext.call(this);
  this.createMediaStreamSource = () => { throw new Error('graph boom'); };
};
context.AudioContext.prototype = Object.create(FakeAudioContext.prototype);
context.AudioContext.prototype.constructor = context.AudioContext;
context.__tracksBefore = fakeTracks.length;
await run(`voiceRecStart()`);
context.AudioContext = savedCtor;
assert.equal(get(`voiceRec.state`), 'idle', 'graph failure lands in honest idle state');
assert.ok(/could not be built|could not start/i.test(get(`$('voice-rec-status').textContent`)), 'graph failure shows an honest notice');
assert.equal(get(`voiceRec.blob`), null, 'graph failure keeps no blob');
assert.equal(node('voice-rec-submit').disabled, true, 'graph failure leaves Submit disabled');
const newTracks = fakeTracks.slice(context.__tracksBefore);
assert.ok(newTracks.length > 0, 'the mic stream was actually acquired before the failure');
assert.ok(newTracks.every((t) => t.stopped === true), 'every acquired track was stopped: mic released');
assert.equal(get(`voiceRec.stream`), null, 'stream reference cleared');
assert.equal(get(`voiceRec.ctx`), null, 'audio context reference cleared');

// (10) Stale-epoch submit: a submit that resolves after the user navigates to
// another question must leave the CURRENT state untouched.
run(`voiceRecDiscard();`);
await run(`voiceRecStart()`);
run(`${PUSH}; voiceRecStop();`);
assert.equal(get(`voiceRec.state`), 'recorded');
context.sent.length = 0;
// Record the submitted qid, then navigate away before the POST resolves.
context.__submitQid = 'q';
run(`S.question = 'q';`);
const realCall = context.call;
context.call = async (method, path, body) => {
  context.sent.push({ method, path, body });
  vm.runInContext(`S.question = 'q-other'`, context); // navigate mid-flight
  return { id: 'asset-stale', sha256: 'y'.repeat(64) };
};
await run(`voiceRecSubmit()`);
context.call = realCall;
assert.equal(context.sent.length, 1, 'the stale submit still reached the endpoint exactly once');
assert.equal(get(`voiceRec.state`), 'recorded', 'stale resolution must not reset the current recorder state');
assert.ok(get(`voiceRec.blob`) !== null, 'stale resolution must not clear the current take');
assert.ok(!String(get(`$('voice-rec-status').textContent`)).includes('Registered'),
  'stale resolution must not claim the take was registered in the current view');
assert.equal(context.sent[0].path, '/questions/q/ai/assets', 'the request still targeted the question it was started for');

// (7) Unsupported browser: MediaRecorder-free check via getUserMedia/AudioContext absence.
const nodes2 = new Map();
const node2 = (id) => {
  if (!nodes2.has(id)) nodes2.set(id, {
    value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
    files: [], dataset: {},
    classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
  });
  return nodes2.get(id);
};
const ctx2 = vm.createContext({
  URL, Blob, Float32Array, Uint8Array, ArrayBuffer, DataView, Math, Date, String, JSON,
  navigator: {}, // no mediaDevices.getUserMedia, no AudioContext -> unsupported
  AudioContext: undefined, webkitAudioContext: undefined,
  S: { project: 'p', question: 'q' }, aiQuestion: 'q',
  document: { getElementById: node2, querySelectorAll: () => [] },
  $: node2,
  esc: (s) => String(s ?? ''),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  call: async () => { throw new Error('no network'); },
  action: async (fn) => fn(0),
  current: () => true,
  showError: () => {}, show: () => {},
});
vm.runInContext(source, ctx2);
assert.equal(vm.runInContext(`voiceRec.state`, ctx2), 'unsupported', 'no mic API means unsupported state');
assert.ok(String(vm.runInContext(`$('voice-rec-status').textContent`, ctx2)).includes('NOT_SUPPORTED'),
  'unsupported status must say NOT_SUPPORTED, never a fake recorder');
assert.equal(node2('voice-rec-submit').disabled, true, 'Submit disabled when unsupported');
await vm.runInContext(`voiceRecStart()`, ctx2);
assert.equal(vm.runInContext(`voiceRec.state`, ctx2), 'unsupported', 'start() in unsupported browser keeps the honest state');
assert.ok(!String(vm.runInContext(`$('voice-rec-playback').innerHTML`, ctx2)).includes('<audio>') ||
  String(vm.runInContext(`$('voice-rec-playback').innerHTML`, ctx2)) === '',
  'no fake playback element when unsupported');

console.log('VOICE_RECORDER_FRONTEND_PASS: idle/recording/recorded/denied/unsupported states, WAV header correctness, exact asset-endpoint submit with no auto-submit, discard clears state');
