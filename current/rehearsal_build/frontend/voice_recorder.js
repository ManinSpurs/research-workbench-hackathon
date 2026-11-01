// In-browser voice memo recorder (closes RECORDER_FORMAT_GAP client-side).
// Mic -> AudioContext -> mono Float32 -> resampled 16 kHz -> 16-bit PCM WAV,
// which satisfies inputs.inspect (mono 16-bit PCM at 16 or 24 kHz).
// Submit reuses the EXISTING asset endpoint (POST /questions/{qid}/ai/assets
// with {name, mime:'audio/wav', encoded}), the same contract as the file
// picker; transcription and voice-confirm stay explicit afterwards.
// Render and stop never touch the network: only an explicit Submit click
// sends anything. No MediaRecorder is used (it emits webm/opus, rejected).
const VOICE_REC_RATE = 16000;
const VOICE_REC_MAX_SECONDS = 240;
const VOICE_REC_MAX_BYTES = 8 * 1024 * 1024;
// Language lookup from i18n.js, with an English fallback so this file still runs
// where that script is absent (the vm harness in tests, a bare JS context). The
// alias has its own name because app.js already owns `t` and library.js owns `lt`.
const VOICE_GLOBAL = (typeof window !== 'undefined') ? window : {};
const vt = (VOICE_GLOBAL.WB_I18N && VOICE_GLOBAL.WB_I18N.t) ? VOICE_GLOBAL.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});
const voiceRec = { state: 'idle', stream: null, ctx: null, src: null, proc: null, sink: null, chunks: [], inputFrames: 0, maxInputFrames: 0, inputRate: 0, blob: null, url: '', wavBytes: null, seconds: 0, notice: '', noticeVars: null, micReason: '' };

// Map a getUserMedia rejection to honest wording. We only claim a cause the
// DOMException name actually identifies; anything else stays unspecific.
// The returned sentence is the English key; voiceRecRender translates it, so a
// stored reason follows a language switch like every other status line.
function voiceRecMicReason(err) {
  const name = (err && err.name) || '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone access was denied. Allow microphone access for this page.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'No usable microphone was found.';
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return 'The microphone could not be read; another app may be using it.';
  }
  return 'The microphone could not be opened.';
}

function voiceRecAudioCtor() {
  if (typeof AudioContext !== 'undefined') return AudioContext;
  if (typeof webkitAudioContext !== 'undefined') return webkitAudioContext;
  return null;
}
function voiceRecSupported() {
  try {
    return !!(typeof navigator !== 'undefined' && navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function' && voiceRecAudioCtor());
  } catch (_) { return false; }
}
function voiceRecEsc(s) { return typeof esc === 'function' ? esc(s) : String(s ?? ''); }

// Pure: Float32 mono samples -> 16-bit PCM WAV bytes (RIFF/WAVE, fmt chunk).
function voiceEncodeWav(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const wstr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  wstr(0, 'RIFF');
  v.setUint32(4, 36 + n * 2, true);
  wstr(8, 'WAVE');
  wstr(12, 'fmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); // byte rate: 1 channel * 16 bit
  v.setUint16(32, 2, true); // block align
  v.setUint16(34, 16, true); // bits per sample
  wstr(36, 'data');
  v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Uint8Array(buf);
}
// Pure: average channels to mono, then linearly resample to VOICE_REC_RATE.
function voiceResampleMono(channels, fromRate) {
  const frames = channels.length ? channels[0].length : 0;
  const nCh = channels.length;
  const mono = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let sum = 0;
    for (let c = 0; c < nCh; c++) sum += channels[c][i];
    mono[i] = nCh ? sum / nCh : 0;
  }
  if (fromRate === VOICE_REC_RATE) return mono;
  const n = Math.floor(frames * VOICE_REC_RATE / fromRate);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const pos = i * fromRate / VOICE_REC_RATE;
    const i0 = Math.floor(pos), frac = pos - i0;
    out[i] = mono[i0] + ((mono[i0 + 1] || 0) - mono[i0]) * frac;
  }
  return out;
}
function voiceRecTeardown() {
  const r = voiceRec;
  try { if (r.proc) r.proc.disconnect(); } catch (_) {}
  try { if (r.src) r.src.disconnect(); } catch (_) {}
  try { if (r.sink) r.sink.disconnect(); } catch (_) {}
  try { if (r.stream) r.stream.getTracks().forEach(t => { try { t.stop(); } catch (_) {} }); } catch (_) {}
  try { if (r.ctx && typeof r.ctx.close === 'function') r.ctx.close(); } catch (_) {}
  r.stream = r.ctx = r.src = r.proc = r.sink = null;
  r.chunks = []; r.inputFrames = 0;
}
function voiceRecForgetBlob() {
  try { if (voiceRec.url && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(voiceRec.url); } catch (_) {}
  voiceRec.blob = null; voiceRec.url = ''; voiceRec.wavBytes = null; voiceRec.seconds = 0;
}
// A notice is stored as its English sentence plus its values, never as finished
// text: the status line is re-rendered on every change and on a language switch,
// so a stored sentence stays translatable after it was first shown.
function voiceRecNotice(key, vars) { voiceRec.notice = key || ''; voiceRec.noticeVars = vars || null; }
function voiceRecReset() {
  voiceRecTeardown(); voiceRecForgetBlob();
  voiceRec.state = voiceRecSupported() ? 'idle' : 'unsupported';
  voiceRecNotice('');
  voiceRecRender();
}
function voiceRecRender() {
  if (typeof $ !== 'function') return;
  const st = $('voice-rec-status'), rec = $('voice-rec-record'), stop = $('voice-rec-stop'),
    sub = $('voice-rec-submit'), dis = $('voice-rec-discard'), play = $('voice-rec-playback');
  if (!st || !rec || !stop || !sub || !dis || !play) return;
  const s = voiceRec.state;
  let text = '';
  if (s === 'recording') text = vt('Recording. Press Stop to finish. Nothing is sent while recording.');
  else if (s === 'recorded') text = vt('Recording ready: 16-bit PCM mono WAV at 16 kHz, {seconds} s. Play it back, submit it to register it as an asset, or discard it. Submitting only registers the file; transcription and confirmation stay explicit.', { seconds: voiceRec.seconds.toFixed(1) });
  else if (s === 'denied') text = vt('{reason} Nothing was recorded. Press Record to try again.', { reason: vt(voiceRec.micReason || 'The microphone could not be opened.') });
  else if (s === 'unsupported') text = vt('Voice recording is not supported in this browser (NOT_SUPPORTED). Upload a validated WAV file instead.');
  else text = vt('Recorder idle. Nothing is recorded or sent until you press Record and then Submit.');
  if (voiceRec.notice) text += ' ' + vt(voiceRec.notice, voiceRec.noticeVars);
  st.textContent = text;
  rec.disabled = !(s === 'idle' || s === 'denied');
  stop.disabled = s !== 'recording';
  sub.disabled = s !== 'recorded';
  dis.disabled = !(s === 'recorded' || s === 'recording');
  play.innerHTML = (s === 'recorded' && voiceRec.url) ? '<audio controls preload="none" src="' + voiceRecEsc(voiceRec.url) + '"></audio>' : '';
}
async function voiceRecStart() {
  if (voiceRec.state === 'recording') return;
  if (!voiceRecSupported()) { voiceRec.state = 'unsupported'; voiceRecRender(); return; }
  voiceRecTeardown(); voiceRecForgetBlob(); voiceRecNotice('');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    voiceRec.state = 'denied';
    voiceRec.micReason = voiceRecMicReason(err);
    voiceRecRender();
    return;
  }
  let ctx;
  try {
    ctx = new (voiceRecAudioCtor())();
    if (ctx.state === 'suspended' && typeof ctx.resume === 'function') { try { await ctx.resume(); } catch (_) {} }
  } catch (_) {
    try { stream.getTracks().forEach(t => { try { t.stop(); } catch (_) {} }); } catch (_) {}
    voiceRec.state = 'idle';
    voiceRecNotice('Audio capture could not start in this browser.');
    voiceRecRender();
    return;
  }
  voiceRec.stream = stream; voiceRec.ctx = ctx;
  voiceRec.inputRate = ctx.sampleRate || VOICE_REC_RATE;
  voiceRec.maxInputFrames = VOICE_REC_MAX_SECONDS * voiceRec.inputRate;
  // Graph construction is inside the guard: a throw here must release the mic
  // and the context, never leave a live track behind.
  try {
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 2, 1);
    const sink = (typeof ctx.createGain === 'function') ? ctx.createGain() : null;
    if (sink) { try { sink.gain.value = 0; } catch (_) {} }
    proc.onaudioprocess = (e) => voiceRecPush(e.inputBuffer);
    src.connect(proc);
    if (sink && ctx.destination) { proc.connect(sink); sink.connect(ctx.destination); }
    voiceRec.src = src; voiceRec.proc = proc; voiceRec.sink = sink;
  } catch (_) {
    voiceRecTeardown();
    voiceRec.state = 'idle';
    voiceRecNotice('The audio capture graph could not be built in this browser.');
    voiceRecRender();
    return;
  }
  voiceRec.state = 'recording';
  voiceRecRender();
}
function voiceRecPush(input) {
  if (voiceRec.state !== 'recording' || !input) return;
  try {
    const nCh = input.numberOfChannels || 1;
    const len = input.length || (nCh ? input.getChannelData(0).length : 0);
    const mono = new Float32Array(len);
    for (let i = 0; i < len; i++) {
      let sum = 0;
      for (let c = 0; c < nCh; c++) sum += input.getChannelData(c)[i];
      mono[i] = sum / nCh;
    }
    voiceRec.chunks.push(mono);
    voiceRec.inputFrames += len;
    if (voiceRec.inputFrames >= voiceRec.maxInputFrames) voiceRecStop('Stopped automatically at the {seconds} s limit.', { seconds: VOICE_REC_MAX_SECONDS });
  } catch (_) {}
}
// The note is an English sentence plus its values, kept untranslated until the
// render, because the line can be redrawn by a language switch after it is set.
function voiceRecStop(note, noteVars) {
  if (voiceRec.state !== 'recording') return;
  const rate = voiceRec.inputRate || VOICE_REC_RATE;
  const frames = new Float32Array(voiceRec.inputFrames);
  let off = 0;
  for (const c of voiceRec.chunks) { frames.set(c, off); off += c.length; }
  voiceRecTeardown();
  const mono16k = voiceResampleMono([frames], rate);
  const bytes = voiceEncodeWav(mono16k, VOICE_REC_RATE);
  voiceRecForgetBlob();
  try {
    voiceRec.blob = new Blob([bytes], { type: 'audio/wav' });
    voiceRec.wavBytes = bytes;
    if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') voiceRec.url = URL.createObjectURL(voiceRec.blob);
  } catch (_) { voiceRec.blob = null; }
  voiceRec.seconds = mono16k.length / VOICE_REC_RATE;
  if (!mono16k.length) {
    // Empty take: no frames means nothing to transcribe. Refuse it rather than
    // register a 0-second asset the backend would happily accept.
    voiceRecForgetBlob();
    voiceRec.state = 'idle';
    voiceRecNotice('That take was empty, so nothing was kept. Press Record and speak before stopping.');
    voiceRecRender();
    return;
  }
  voiceRec.state = 'recorded';
  voiceRecNotice(note, noteVars);
  voiceRecRender();
}
async function voiceRecSubmit() {
  if (voiceRec.state !== 'recorded' || !voiceRec.blob) return;
  const qid = (typeof S !== 'undefined' && S.question) ? S.question : ((typeof aiQuestion === 'string' && aiQuestion) ? aiQuestion : null);
  if (!qid) { if (typeof showError === 'function') showError(vt('Open or save a question first.')); return; }
  if (voiceRec.blob.size > VOICE_REC_MAX_BYTES) { if (typeof showError === 'function') showError(vt('Recording exceeds the 8 MiB limit; record a shorter memo.')); return; }
  if (typeof action !== 'function') return;
  const blob = voiceRec.blob;
  const name = 'voice-memo-' + new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15) + '.wav';
  return action(async (epoch) => {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = '';
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const isCurrent = (typeof current === 'function') ? current(epoch) : true;
    const sameQ = (typeof S !== 'undefined') ? S.question === qid : true;
    if (!isCurrent || !sameQ) return;
    if (typeof call !== 'function') return;
    await call('POST', '/questions/' + qid + '/ai/assets', { name: name, mime: 'audio/wav', encoded: btoa(binary) });
    // Only the submit's own epoch+question may rewrite the UI: a submit that
    // resolves after the user navigated must leave the current state alone.
    const stillCurrent = (typeof current === 'function') ? current(epoch) : true;
    const stillSameQ = (typeof S !== 'undefined') ? S.question === qid : true;
    if (!stillCurrent || !stillSameQ) return;
    voiceRecReset();
    voiceRecNotice('Registered {name}. Select it under Attach to prepare transcription; nothing was transcribed or confirmed.', { name: name });
    voiceRecRender();
    if ((typeof current === 'function' ? current(epoch) : true) && typeof S !== 'undefined' && S.question === qid && typeof loadAI === 'function') await loadAI(qid);
  });
}
function voiceRecDiscard() {
  if (voiceRec.state !== 'recorded' && voiceRec.state !== 'recording') return;
  voiceRecReset();
}
function voiceRecInit() { voiceRecReset(); }
try { voiceRecInit(); } catch (_) {}

// A language switch has to redraw the status line: it is one script-built
// paragraph whose words changed, and the round buttons keep their own labels.
if (typeof window !== 'undefined' && window.WB_I18N && window.WB_I18N.onChange) {
  window.WB_I18N.onChange(function () { try { voiceRecRender(); } catch (_) {} });
}

// --- Korean strings for the voice recorder ---------------------------------
// Keyed by the authored English sentence. Values that come from the recording
// itself (the length in seconds, the generated file name) arrive as
// {placeholders}. Technical names — WAV, 16 kHz, 16-bit PCM, MiB, NOT_SUPPORTED,
// Attach, Stop, Record, Submit — stay as they are: they name a format, a button
// the researcher can see, or a state the rest of the tool also uses.
if (typeof window !== 'undefined' && window.WB_I18N) window.WB_I18N.register('ko', {
  'Recording. Press Stop to finish. Nothing is sent while recording.':
    '녹음 중입니다. 마치려면 정지(Stop)를 누르세요. 녹음 중에는 아무것도 전송되지 않습니다.',
  'Recording ready: 16-bit PCM mono WAV at 16 kHz, {seconds} s. Play it back, submit it to register it as an asset, or discard it. Submitting only registers the file; transcription and confirmation stay explicit.':
    '녹음이 준비되었습니다: 16 kHz, 16비트 PCM 모노 WAV, {seconds}초. 재생해 확인한 뒤 자료로 등록하려면 제출하고, 아니면 버리세요. 제출은 파일을 등록할 뿐이며, 전사와 확인은 따로 진행됩니다.',
  'The microphone could not be opened.': '마이크를 열 수 없습니다.',
  'Microphone access was denied. Allow microphone access for this page.':
    '마이크 접근이 거부되었습니다. 이 페이지에 마이크 사용을 허용하세요.',
  'No usable microphone was found.': '사용할 수 있는 마이크를 찾지 못했습니다.',
  'The microphone could not be read; another app may be using it.':
    '마이크를 읽을 수 없습니다. 다른 프로그램이 사용 중일 수 있습니다.',
  '{reason} Nothing was recorded. Press Record to try again.':
    '{reason} 녹음된 내용은 없습니다. 녹음(Record)을 눌러 다시 시도하세요.',
  'Voice recording is not supported in this browser (NOT_SUPPORTED). Upload a validated WAV file instead.':
    '이 브라우저에서는 음성 녹음을 지원하지 않습니다 (NOT_SUPPORTED). 대신 검증된 WAV 파일을 올려 주세요.',
  'Recorder idle. Nothing is recorded or sent until you press Record and then Submit.':
    '녹음기 대기 중입니다. 녹음(Record)을 누르고 제출(Submit)하기 전까지는 아무것도 녹음되거나 전송되지 않습니다.',
  'Audio capture could not start in this browser.': '이 브라우저에서는 오디오 입력을 시작할 수 없습니다.',
  'The audio capture graph could not be built in this browser.': '이 브라우저에서는 오디오 입력 경로를 구성할 수 없습니다.',
  'Stopped automatically at the {seconds} s limit.': '{seconds}초 한도에 이르러 자동으로 멈췄습니다.',
  'That take was empty, so nothing was kept. Press Record and speak before stopping.':
    '녹음된 내용이 없어 아무것도 남기지 않았습니다. 녹음(Record)을 누르고 말한 뒤에 정지하세요.',
  'Registered {name}. Select it under Attach to prepare transcription; nothing was transcribed or confirmed.':
    '{name} 파일을 등록했습니다. 전사하려면 Attach에서 이 파일을 선택하세요. 아직 전사되거나 확인된 내용은 없습니다.',
  'Open or save a question first.': '먼저 질문을 열거나 저장하세요.',
  'Recording exceeds the 8 MiB limit; record a shorter memo.': '녹음이 8 MiB 한도를 넘습니다. 더 짧게 녹음하세요.'
});
