// Code-rendered research canvas. No image generation, provider call or automatic inference.
// Language lookup from i18n.js, with an English fallback so this file still runs
// where that script is absent (a harness that mounts one file, a page that lost
// the script, a bare JS context). The alias has its own name because app.js owns
// `t` and library.js owns `lt`; a second top-level `t` would break the page.
const CANVAS_GLOBAL = (typeof window !== 'undefined') ? window : {};
const ct = (CANVAS_GLOBAL.WB_I18N && CANVAS_GLOBAL.WB_I18N.t) ? CANVAS_GLOBAL.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});
let canvasState=null, canvasRequest=0;
// Edge drafts live here between re-renders so adding or removing one row never
// touches the node rows above it. Older saved drafts omit `edges` entirely.
let canvasEdgeDraft=[];
function clearCanvas() {
  canvasState=null; canvasRequest++; canvasEdgeDraft=[];
  savedCanvasQuestions=[]; $('saved-canvases').textContent='';
  ['canvas-panel','canvas-evidence'].forEach(id=>$(id).classList.add('hidden'));
  ['research-canvas','canvas-editor','canvas-comparison','canvas-status','canvas-evidence'].forEach(id=>$(id).textContent='');
  document.querySelectorAll('.canvas-linked-row').forEach(el=>el.classList.remove('canvas-linked-row'));
}
// A missing edges array is an empty edges array, never a guess.
function canvasEdges(c) { return Array.isArray(c && c.edges) ? c.edges : []; }
function canvasLabel(nodes,index) {
  const n=nodes && nodes[index];
  return n && n.label ? n.label : ct('Component {number}', {number: Number(index)+1});
}
function canvasEdgeKey(e) { return `${Number(e.from_index)}>${Number(e.to_index)}`; }
// The joining sentence is translated; the labels, the description and the note
// are the researcher's own data and are escaped before they take a placeholder.
// Escaping the finished sentence instead would turn its own punctuation into
// entities, so callers insert this result as it is.
function canvasEdgeText(e,nodes) {
  const base=ct('{from} to {to}', {from: esc(canvasLabel(nodes,e.from_index)), to: esc(canvasLabel(nodes,e.to_index))});
  return `${base}${e.relation?`: ${esc(e.relation)}`:''}${e.note?` (${esc(e.note)})`:''}`;
}
// Arc geometry is measured after layout, so a viewport change, a collapsed side
// panel or an expanded surface redraws it rather than leaving connectors over
// columns that moved. The observer is optional; the code works without it.
window.addEventListener('resize',()=>{ if(canvasState&&$('canvas-edges')) drawCanvasEdges(); });
document.addEventListener('toggle',event=>{
  if(event.target&&event.target.id==='canvas-panel'&&event.target.open) drawCanvasEdges();
},true);
if(typeof ResizeObserver==='function') {
  // The root element's size is set by the viewport and the panel grid, never by
  // the SVG, so redrawing here cannot feed back into its own trigger.
  new ResizeObserver(()=>{ if(canvasState&&$('canvas-edges')) drawCanvasEdges(); }).observe(document.documentElement);
}
async function loadCanvas(qid) {
  const epoch=S.epoch,request=++canvasRequest;
  const data=await call('GET',`/questions/${encodeURIComponent(qid)}/canvas`);
  if(!current(epoch)||request!==canvasRequest||S.question!==qid) return;
  canvasState=data; renderCanvas();
  if(typeof loadAI === "function") await loadAI(qid);
}
function canvasOption(dataset,variable) { return JSON.stringify([dataset,variable]); }
function renderCanvas() {
  const c=canvasState;if(!c) return;
  $('canvas-panel').classList.remove('hidden');
  const edges=canvasEdges(c);
  canvasEdgeDraft=edges.map(e=>({from_index:Number(e.from_index),to_index:Number(e.to_index),relation:String(e.relation||''),note:String(e.note||'')}));
  // The node row always renders, so a canvas with 2 nodes and 0 edges is a
  // complete picture rather than a blank area. With no edges there is no SVG at
  // all: the empty state below says plainly that nothing is asserted.
  const diagram=`<figure class="canvas-diagram"><div class="canvas-node-scroll"><div class="canvas-graph">
      ${edges.length?`<svg id="canvas-edges" class="canvas-connectors" role="img" aria-label="${ct('Researcher-proposed relationships, not verified timing or causality')}"><title>${ct('Researcher-proposed relationships, not verified timing or causality')}</title><desc>${edges.map((e,k)=>ct('Relationship {number}: {text}', {number: k+1, text: canvasEdgeText(e,c.nodes)})).join('; ')}</desc><defs><marker id="canvas-arrow" viewBox="0 0 6 6" refX="5.5" refY="3" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L6 3 L0 6" fill="none" stroke="currentColor" stroke-width="1.6"/></marker></defs><g id="canvas-edge-paths"></g></svg>`:''}
      ${canvasNodeButtons(c)}</div></div>
      ${edges.length?`<figcaption class="muted">${ct('Arrows are researcher-authored assertions about order or relationship. They are not verified timing, causality or meaning.')}</figcaption>`:''}</figure>`;
  const edgeList=edges.length
    ? `<div class="canvas-edge-list-wrap"><p class="canvas-edge-list-heading">${ct('Researcher-asserted relationships ({count})', {count: edges.length})}</p><ol class="canvas-edge-list">${edges.map((e,k)=>`<li data-edge-index="${k}">${ct('{from} to {to}', {from: esc(canvasLabel(c.nodes,e.from_index)), to: esc(canvasLabel(c.nodes,e.to_index))})}: <strong>${esc(e.relation||ct('Relationship not yet described'))}</strong>${e.note?` <span class="muted">${esc(e.note)}</span>`:''} <span class="canvas-edge-status">${ct('Researcher-asserted; unverified')}</span></li>`).join('')}</ol></div>`
    : `<p class="canvas-edge-empty muted" data-canvas-edge-count="0">${ct('No relationships recorded. This canvas names {count} components and asserts no connection between them, which says nothing about their order, timing or causality. Use the editor below to assert one.', {count: c.nodes.length})}</p>`;
  $('research-canvas').innerHTML=`<p><strong>${esc(c.question)}</strong></p><p class="muted">${esc(c.context.pack_id)} ${esc(c.context.pack_version)} / ${ct('Draft revision {number}', {number: c.revision})}</p>
    ${diagram}${edgeList}<p class="muted">${esc(c.notice)}</p>`;
  const options=c.datasets.flatMap(d=>d.columns.map(v=>({value:canvasOption(d.dataset_id,v),label:`${d.dataset_id}: ${v}`})));
  $('canvas-editor').innerHTML=c.nodes.map((n,i)=>`<div class="canvas-edit-row"><label>${ct('Event / component {number}', {number: i+1})}<input id="canvas-label-${i}" maxlength="100" value="${esc(n.label)}"></label><label>${ct('Registered variable')}<select id="canvas-var-${i}"><option value="">${ct('No variable linked')}</option>${options.map(o=>`<option value="${esc(o.value)}" ${o.value===canvasOption(n.dataset_id,n.variable)?'selected':''}>${esc(o.label)}</option>`).join('')}</select></label><label>${ct('Your assumption or note')}<input id="canvas-note-${i}" maxlength="500" value="${esc(n.note)}"></label></div>`).join('')+canvasEdgeEditor();
  renderCanvasEdgeRows();
  drawCanvasEdges();
  $('canvas-comparison').innerHTML=canvasComparison(c,edges);
}
function canvasNodeButtons(c) {
  return `<div class="canvas-nodes">${c.nodes.map((n,i)=>`<button type="button" data-canvas-node="${i}" aria-pressed="false" onclick="focusCanvasNode(${i})"><strong>${esc(n.label)}</strong><br><code>${esc(n.variable||ct('No variable linked'))}</code><br><span class="muted">${ct(n.status==='column_linked'?'Column linked; interpretation unverified':'Unmapped; availability not established')}</span></button>`).join('')}</div>`;
}
// One path per edge, drawn from the source column to the target column at the
// centers the buttons actually landed on. Nothing here is inferred: with no
// edges the SVG is absent and the empty state above speaks instead.
function drawCanvasEdges() {
  const c=canvasState;if(!c) return;
  const svg=$('canvas-edges'), nodesEl=document.querySelector('#research-canvas .canvas-nodes');
  if(!svg||!nodesEl) return;
  const edges=canvasEdges(c);
  const buttons=[...nodesEl.querySelectorAll('[data-canvas-node]')];
  const base=nodesEl.getBoundingClientRect().left;
  const centers=buttons.map(b=>{const r=b.getBoundingClientRect();return r.left-base+r.width/2;});
  const width=Math.max(nodesEl.scrollWidth,Math.ceil(centers.at(-1)||0)+40,120);
  const H=56, Y=52;
  svg.setAttribute('viewBox',`0 0 ${width} ${H}`);
  svg.setAttribute('width',String(width));
  svg.setAttribute('height',String(H));
  const paths=edges.map((e,k)=>{
    const a=Number(e.from_index),b=Number(e.to_index);
    const x1=centers[a],x2=centers[b];
    if(!Number.isFinite(x1)||!Number.isFinite(x2)) return '';
    const loop=a===b;
    // Bend grows with the span and then steps in bands, so several edges over
    // the same columns stay separately traceable without any gradient or fill.
    const bend=Math.min(28,10+Math.abs(x2-x1)/14)+(k%3)*4;
    const d=loop
      ? `M${x1-6} ${Y} C${x1-34} ${Y-2*bend}, ${x1+34} ${Y-2*bend}, ${x1+6} ${Y}`
      : `M${x1} ${Y} Q${(x1+x2)/2} ${Y-2*(bend+4)} ${x2} ${Y}`;
    // A self-referencing row gets no arrowhead: the curve already returns to its
    // own column, and an arrow there would claim a direction it does not have.
    const marker=loop?'':'marker-end="url(#canvas-arrow)"';
    return `<g class="canvas-edge-line" data-edge-index="${k}"><title>${canvasEdgeText(e,c.nodes)}</title><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.25" ${marker}/></g>`;
  }).join('');
  // A small tick at each column baseline marks where an arrow lands, so an arc
  // that spans several columns cannot be read as pointing at the ones between.
  $('canvas-edge-paths').innerHTML=buttons.map((_,i)=>`<circle cx="${centers[i]}" cy="${Y}" r="2.5" fill="currentColor"/>`).join('')+paths;
}
function canvasEdgeEditor() {
  return `<fieldset class="canvas-edge-editor"><legend>${ct('Researcher-asserted relationships')}</legend>
    <p class="muted">${ct('A connection here is your own assertion about sequence or association. It is not verified timing, causality or meaning, and the app never infers one for you.')}</p>
    <div id="canvas-edge-rows"></div>
    <button type="button" id="canvas-add-edge" onclick="addCanvasEdge()">${ct('Add relationship')}</button></fieldset>`;
}
function canvasNodeOptions(nodes,value) {
  return nodes.map((n,i)=>`<option value="${i}" ${i===value?'selected':''}>${esc(n.label||ct('Component {number}', {number: i+1}))}</option>`).join('');
}
function renderCanvasEdgeRows() {
  const c=canvasState;if(!c) return;
  const rows=$('canvas-edge-rows');if(!rows) return;
  rows.innerHTML=canvasEdgeDraft.length?canvasEdgeDraft.map((e,i)=>`<div class="canvas-edit-row canvas-edge-row" data-edge-row="${i}">
      <label for="canvas-edge-from-${i}">${ct('Relationship {number} source', {number: i+1})}<select id="canvas-edge-from-${i}" onchange="syncCanvasEdgeDraft()">${canvasNodeOptions(c.nodes,e.from_index)}</select></label>
      <label for="canvas-edge-to-${i}">${ct('Relationship {number} target', {number: i+1})}<select id="canvas-edge-to-${i}" onchange="syncCanvasEdgeDraft()">${canvasNodeOptions(c.nodes,e.to_index)}</select></label>
      <label for="canvas-edge-relation-${i}">${ct('Relationship {number} description', {number: i+1})}<input id="canvas-edge-relation-${i}" maxlength="60" value="${esc(e.relation)}" placeholder="${ct('For example: precedes')}"></label>
      <label for="canvas-edge-note-${i}">${ct('Relationship {number} note (optional)', {number: i+1})}<input id="canvas-edge-note-${i}" maxlength="500" value="${esc(e.note)}"></label>
      <button type="button" class="canvas-edge-remove" onclick="removeCanvasEdge(${i})" aria-label="${ct('Remove relationship {number}', {number: i+1})}">${ct('Remove')}</button></div>`).join('')
    : `<p class="muted" id="canvas-edge-none">${ct('No relationship rows. Adding one records an assertion of yours; it does not verify anything.')}</p>`;
}
function syncCanvasEdgeDraft() {
  canvasEdgeDraft=canvasEdgeDraft.map((e,i)=>({
    from_index:Number($(`canvas-edge-from-${i}`)?.value ?? e.from_index),
    to_index:Number($(`canvas-edge-to-${i}`)?.value ?? e.to_index),
    relation:String($(`canvas-edge-relation-${i}`)?.value ?? e.relation),
    note:String($(`canvas-edge-note-${i}`)?.value ?? e.note)}));
  return canvasEdgeDraft;
}
function addCanvasEdge() {
  if(!canvasState) return;
  syncCanvasEdgeDraft();
  // A new row continues from the last target and points at the next column. At
  // the end of the row it points back one instead, so the default is a pair the
  // researcher can accept or change rather than a self-referencing placeholder.
  const last=canvasEdgeDraft[canvasEdgeDraft.length-1];
  const from=last?last.to_index:0;
  const lastNode=canvasState.nodes.length-1;
  const to=from<lastNode?from+1:Math.max(0,from-1);
  canvasEdgeDraft.push({from_index:from,to_index:to,relation:'',note:''});
  renderCanvasEdgeRows();
  $(`canvas-edge-from-${canvasEdgeDraft.length-1}`)?.focus();
}
function removeCanvasEdge(index) {
  syncCanvasEdgeDraft();
  canvasEdgeDraft.splice(index,1);
  renderCanvasEdgeRows();
  const next=$('canvas-add-edge');
  if(next) next.focus();
}
// One row of a comparison table: the two labels and the asserted description.
// The labels and the description are data, so they are escaped inside the
// placeholders; the sentence around them is inserted as it comes out of ct().
function canvasEdgeDescription(nodes,e) {
  return ct('{from} to {to}: {relation}', {from: esc(canvasLabel(nodes,e.from_index)), to: esc(canvasLabel(nodes,e.to_index)), relation: esc(e.relation||ct('no description'))});
}
function canvasEdgeChangeRows(previous,current) {
  const before=new Map(canvasEdges(previous).map(e=>[canvasEdgeKey(e),e]));
  const after=new Map(canvasEdges(current).map(e=>[canvasEdgeKey(e),e]));
  const rows=[];
  for(const [key,e] of after) {
    const old=before.get(key);
    if(!old) rows.push([ct('Added'), '-', canvasEdgeDescription(current.nodes,e)]);
    else if(String(old.relation||'')!==String(e.relation||'')) rows.push([ct('Description changed'), canvasEdgeDescription(previous.nodes,old), canvasEdgeDescription(current.nodes,e)]);
  }
  for(const [key,e] of before) if(!after.has(key)) rows.push([ct('Removed'), canvasEdgeDescription(previous.nodes,e), '-']);
  return rows;
}
// One component row of the draft comparison: label, dataset, variable and note.
// Every value is escaped as data before it reaches a placeholder, so the row's
// own wording is never turned into entities.
function canvasDraftRowText(n) {
  return ct('{label}: {dataset} / {variable}; {note}', {
    label: esc(n.label), dataset: esc(n.dataset_id||''), variable: esc(n.variable||ct('unmapped')), note: esc(n.note)
  });
}
function canvasComparison(c,edges) {
  if(!c.previous) return ct('No previous saved draft.');
  const nodeTable=`<table><thead><tr><th>${ct('Component')}</th><th>${ct('Previous draft')}</th><th>${ct('Current saved draft')}</th></tr></thead><tbody>${c.nodes.map((n,i)=>{const old=c.previous.nodes[i];return `<tr><td>${i+1}</td><td>${old?canvasDraftRowText(old):ct('Not present')}</td><td>${canvasDraftRowText(n)}</td></tr>`;}).join('')}</tbody></table>`;
  const changes=canvasEdgeChangeRows(c.previous,{...c,edges});
  const edgeTable=`<table class="canvas-edge-comparison"><caption>${ct('Researcher-asserted relationships')}</caption><thead><tr><th>${ct('Change')}</th><th>${ct('Previous draft')}</th><th>${ct('Current saved draft')}</th></tr></thead><tbody>${changes.length?changes.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join(''):`<tr><td colspan="3">${ct('No relationship changes. Both drafts assert the same connections.')}</td></tr>`}</tbody></table>`;
  return `${nodeTable}${edgeTable}<p class="muted">${ct('Draft comparison only. Saving does not change the research question or approve a design, and a relationship here remains unverified.')}</p>`;
}
function focusCanvasNode(index) {
  const n=canvasState?.nodes[index];if(!n) return;
  document.querySelectorAll("[data-canvas-node]").forEach(el=>el.setAttribute("aria-pressed",String(Number(el.dataset.canvasNode)===index)));
  document.querySelectorAll('[data-canvas-dataset]').forEach(el=>el.classList.toggle('canvas-linked-row',el.dataset.canvasDataset===n.dataset_id&&el.dataset.canvasVariable===n.variable));
  const profile=$('profile').closest('details');if(profile) profile.open=true;
  show('canvas-evidence',`<h3>${esc(n.label)}</h3><p>${esc(n.dataset_id||ct('No dataset linked'))} / ${esc(n.variable||ct('No variable linked'))}</p><p class="muted">${ct('Registered excerpts below are source material. Applicability and meaning remain unverified.')}</p>${n.evidence.length?n.evidence.map(e=>`<article><p><code>${esc(e.evidence_id)}</code></p><blockquote>${esc(e.definition)}</blockquote><p>${esc(e.location)} / ${esc(e.notes||'')}</p></article>`).join(''):`<p>${ct('No registered excerpt for this linked column. This does not prove the information is absent elsewhere.')}</p>`}<p>${ct('Your note: {note}', {note: esc(n.note||ct('None'))})}</p>`);
}
// The status line is read by a researcher, so it carries a plain reason rather
// than a raw exception message. app.js already owns that wording (errorText);
// this reuses it where the page has it and falls back to one quiet sentence,
// never to an internal identifier or a stack trace.
function canvasErrorReason(error) {
  const raw = String((error && error.message) || '').trim();
  if (raw && typeof errorText === 'function') { try { return errorText(raw); } catch (_) {} }
  return raw || ct('the request failed');
}
async function saveCanvas() {
  if(!canvasState||S.question!==canvasState.question_id) return;
  const c=canvasState;
  return action(async epoch=>{
    const nodes=c.nodes.map((n,i)=>{const value=$(`canvas-var-${i}`).value;const [dataset_id,variable]=value?JSON.parse(value):[null,null];return {label:$(`canvas-label-${i}`).value,dataset_id,variable,note:$(`canvas-note-${i}`).value};});
    // Edge rows are read straight from the editor so unsaved typing is sent too;
    // the backend contract carries no relation_status on write.
    const edges=syncCanvasEdgeDraft()
      .filter(e=>Number.isInteger(e.from_index)&&Number.isInteger(e.to_index)&&e.from_index>=0&&e.to_index>=0&&e.from_index<c.nodes.length&&e.to_index<c.nodes.length)
      .map(e=>({from_index:e.from_index,to_index:e.to_index,relation:String(e.relation).slice(0,60),note:String(e.note).slice(0,500)}));
    const result=await call('PUT',`/questions/${encodeURIComponent(c.question_id)}/canvas`,{expected_revision:c.revision,nodes,edges}).catch(error=>{
      // A rejected draft is reported on the canvas status line as well, and the
      // original error path still runs, so the editor and every unsaved row stay
      // exactly as the researcher left them.
      if(current(epoch)) show('canvas-status',ct('Draft not saved: {reason}. Your edits are still in the editor.', {reason: esc(canvasErrorReason(error))}));
      throw error;
    });
    if(!current(epoch)||S.question!==c.question_id) return;
    canvasState=result;renderCanvas();$('canvas-edit').open=false;$('canvas-status').textContent=ct('Draft saved. Research question and researcher decisions are unchanged.');
  });
}

let savedCanvasQuestions=[];
async function loadSavedCanvases() {
  if(!S.project) return;
  const epoch=S.epoch,pack=S.pack;
  const memory=await call('GET',`/projects/${encodeURIComponent(S.project)}/pack-memory?pack_id=${encodeURIComponent(pack)}`);
  if(!current(epoch)||pack!==S.pack) return;
  savedCanvasQuestions=memory.questions;
  $('saved-canvases').innerHTML=memory.questions.length?memory.questions.map((q,i)=>`<p><button type="button" onclick="openSavedCanvas(${i})">${esc(q.original_text)}</button></p>`).join(''):ct('No remembered questions in this pack.');
  controls();
}
async function openSavedCanvas(index) {
  const q=savedCanvasQuestions[index];if(!q) return;
  return action(async epoch=>{
    clearActive();S.question=q.id;
    await loadCanvas(q.id);
    if(!current(epoch)) return;
    $('canvas-panel').open=true;$('question-entry').open=false;
    const pending=S.pending.find(c=>c.question_id===q.id);
    if(pending) {S.task=pending.task_id;renderClarification(pending);}
    await loadSavedCanvases();
  });
}

// A language switch has to redraw the canvas: the node buttons, the edge list,
// the editor and the comparison table are all script output, and i18n.js cannot
// reach them. Only the wording changes, so whatever the researcher had already
// typed stays in place across the redraw. renderCanvas() rebuilds the edge rows
// from the saved draft, so the draft is read out of the editor first and put
// back after the rebuild.
if (CANVAS_GLOBAL.WB_I18N && CANVAS_GLOBAL.WB_I18N.onChange) {
  CANVAS_GLOBAL.WB_I18N.onChange(() => {
    try {
      if (!canvasState) return;
      const typed = canvasState.nodes.map((n, i) => {
        const label = $(`canvas-label-${i}`), variable = $(`canvas-var-${i}`), note = $(`canvas-note-${i}`);
        return label ? { label: label.value, variable: variable ? variable.value : null, note: note ? note.value : null } : null;
      });
      if ($('canvas-edge-rows')) syncCanvasEdgeDraft();
      const edges = canvasEdgeDraft.map(e => ({ from_index: e.from_index, to_index: e.to_index, relation: e.relation, note: e.note }));
      renderCanvas();
      canvasEdgeDraft = edges;
      renderCanvasEdgeRows();
      typed.forEach((v, i) => {
        if (!v) return;
        const label = $(`canvas-label-${i}`), variable = $(`canvas-var-${i}`), note = $(`canvas-note-${i}`);
        if (label) label.value = v.label;
        if (variable && v.variable != null) variable.value = v.variable;
        if (note) note.value = v.note;
      });
    } catch (_) {}
  });
}

// --- Korean strings for the canvas -----------------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// Sentences that carry a value use {placeholder} so Korean keeps its own word
// order. Technical names (dataset ids, variable names, file names) stay as-is.
//
// The canvas rule survives translation with the same strength: a connection is
// the researcher's own assertion, never a verified cause. 주장 keeps 주장, and
// 미검증 stays 미검증.
if (CANVAS_GLOBAL.WB_I18N) CANVAS_GLOBAL.WB_I18N.register('ko', {
  'Component {number}': '구성요소 {number}',
  'Component': '구성요소',
  '{from} to {to}': '{from} → {to}',
  'Researcher-proposed relationships, not verified timing or causality': '연구자가 제안한 관계이며, 시점이나 인과는 검증되지 않았습니다',
  'Relationship {number}: {text}': '관계 {number}: {text}',
  'Arrows are researcher-authored assertions about order or relationship. They are not verified timing, causality or meaning.':
    '화살표는 순서나 관계에 대해 연구자가 직접 작성한 주장입니다. 검증된 시점이나 인과, 의미가 아닙니다.',
  'Researcher-asserted relationships ({count})': '연구자가 주장한 관계 ({count})',
  'Researcher-asserted relationships': '연구자가 주장한 관계',
  'Researcher-asserted; unverified': '연구자 주장, 미검증',
  'Relationship not yet described': '아직 설명하지 않은 관계',
  'No relationships recorded. This canvas names {count} components and asserts no connection between them, which says nothing about their order, timing or causality. Use the editor below to assert one.':
    '기록된 관계가 없습니다. 이 캔버스는 구성요소 {count}개를 지정할 뿐 그 사이의 연결을 주장하지 않으며, 이는 순서나 시점, 인과에 대해 아무것도 말하지 않습니다. 아래 편집기에서 직접 주장을 추가할 수 있습니다.',
  'A connection here is your own assertion about sequence or association. It is not verified timing, causality or meaning, and the app never infers one for you.':
    '여기의 연결은 순서나 연관에 대한 연구자 자신의 주장입니다. 검증된 시점이나 인과, 의미가 아니며, 앱이 대신 추론하지 않습니다.',
  'Add relationship': '관계 추가',
  'Relationship {number} source': '관계 {number} 출발',
  'Relationship {number} target': '관계 {number} 도착',
  'Relationship {number} description': '관계 {number} 설명',
  'Relationship {number} note (optional)': '관계 {number} 메모 (선택)',
  'For example: precedes': '예: 선행한다',
  'Remove relationship {number}': '관계 {number} 삭제',
  'Remove': '삭제',
  'No relationship rows. Adding one records an assertion of yours; it does not verify anything.':
    '관계 행이 없습니다. 하나를 추가하면 연구자의 주장이 기록될 뿐, 무엇도 검증되지 않습니다.',
  'Added': '추가됨',
  'Description changed': '설명 변경',
  'Removed': '삭제됨',
  'no description': '설명 없음',
  '{from} to {to}: {relation}': '{from} → {to}: {relation}',
  'No previous saved draft.': '이전에 저장된 초안이 없습니다.',
  'Previous draft': '이전 초안',
  'Current saved draft': '현재 저장한 초안',
  'Not present': '없음',
  '{label}: {dataset} / {variable}; {note}': '{label}: {dataset} / {variable} · {note}',
  'unmapped': '연결 없음',
  'Change': '변경',
  'No relationship changes. Both drafts assert the same connections.':
    '관계 변경이 없습니다. 두 초안이 주장하는 연결은 같습니다.',
  'Draft comparison only. Saving does not change the research question or approve a design, and a relationship here remains unverified.':
    '초안 비교일 뿐입니다. 저장은 연구 질문을 바꾸거나 설계를 승인하지 않으며, 여기의 관계는 미검증 상태로 남습니다.',
  'Draft revision {number}': '초안 리비전 {number}',
  'Event / component {number}': '사건·구성요소 {number}',
  'Registered variable': '등록된 변수',
  'No variable linked': '연결된 변수 없음',
  'Your assumption or note': '연구자의 가정 또는 메모',
  'Column linked; interpretation unverified': '변수 연결됨, 해석 미검증',
  'Unmapped; availability not established': '연결되지 않음, 자료 유무 확인되지 않음',
  'No dataset linked': '연결된 데이터셋 없음',
  'Registered excerpts below are source material. Applicability and meaning remain unverified.':
    '아래 등록된 발췌는 원자료입니다. 적용 가능성과 의미는 아직 검증되지 않았습니다.',
  'No registered excerpt for this linked column. This does not prove the information is absent elsewhere.':
    '이 연결된 열에 등록된 발췌가 없습니다. 다른 곳에 그 정보가 없다는 뜻은 아닙니다.',
  'Your note: {note}': '연구자 메모: {note}',
  'None': '없음',
  'Draft not saved: {reason}. Your edits are still in the editor.':
    '초안을 저장하지 못했습니다: {reason}. 편집 중인 내용은 그대로 남아 있습니다.',
  'the request failed': '요청이 실패했습니다',
  'Draft saved. Research question and researcher decisions are unchanged.':
    '초안을 저장했습니다. 연구 질문과 연구자의 결정은 그대로입니다.',
  'No remembered questions in this pack.': '이 팩에 기억된 질문이 없습니다.'
});
