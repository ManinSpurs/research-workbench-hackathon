// Local execution-path status (LOCAL_FREE_MODE_DESIGN_EN L4; prereg C1/C2
// display ids are manifest-level only, never recorded, never rendered).
// WIRING POINT (no /ai/local-status route yet; L3 in progress): this renders
// ONLY from fields GET /ai/registry already returns — providers[] (the
// 'local' entry with its status text) and models[] (local_candidate rows with
// kind/endpoint/status). A backend local_status()/settings() L3 shape, if it
// later lands, must be adopted by extending renderLocalStatus() to accept it;
// until then no second endpoint is assumed and nothing is invented.
// Exact recorded-arm vocabulary (labels contract): 'basic' / 'harness'.
const LOCAL_ARMS = ['basic', 'harness'];
// Exact ready token (LOCAL_FREE_MODE_DESIGN_EN probe vocabulary: L3's
// local_status() probe reports it as the model status on success). No
// /ai/local-status route or probe exists yet and registry.py emits no ready
// status today (local rows carry hardware_and_server_not_verified /
// not_configured), so this gate is currently unreachable and the panel
// always renders unavailable until L3 emits it. Exact === only — never a
// substring check, so a negating status such as 'not local_ready' never
// renders ready.
const LOCAL_READY_STATUS = 'local_ready';
// Language lookup from i18n.js, with an English fallback so this file still runs
// in a bare JS context that has no window (the UI tests). Every script here
// shares one global scope, so the alias stays file-specific and is never `t`.
const WB_GLOBAL_AI = (typeof window !== 'undefined') ? window : {};
const ait = (WB_GLOBAL_AI.WB_I18N && WB_GLOBAL_AI.WB_I18N.t) ? WB_GLOBAL_AI.WB_I18N.t : ((key, vars) => {
  if (!vars) return key;
  return String(key).replace(/\{(\w+)\}/g, (m, name) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
});
function renderLocalStatus(reg){
  const el = (typeof $ === 'function') ? $('ai-local-status') : null;
  if(!el) return 'local-status-element-missing';
  const providers = (reg && Array.isArray(reg.providers)) ? reg.providers : [];
  const local = providers.find(p => p && p.id === 'local') || null;
  const models = (reg && Array.isArray(reg.models)) ? reg.models : [];
  const localRows = models.filter(m => m && (m.id === 'local' || m.kind === 'local_candidate' || (typeof m.id === 'string' && m.id.indexOf('local:') === 0)));
  const statusText = (local && typeof local.status === 'string' && local.status) ? local.status : '';
  const runnable = localRows.filter(m => m && m.endpoint);
  const arms = LOCAL_ARMS.join('/');
  const settings = (reg && reg.settings) || {};
  const connection = settings.local_connection || local || {};
  const probe = settings.local_probe || {};
  // Readiness comes from the RECORDED probe, never from configuration: a
  // configured endpoint only means the operator's server may be running. The
  // exact token is compared with === so a negating status ('not local_ready')
  // can never render as ready.
  const probedReady = probe.ready === true && probe.status === LOCAL_READY_STATUS;
  // Observed identity from the launched server's own /props wins over the values
  // the app launched with: the panel says what is loaded, not what was intended.
  const sampled = (typeof connection.observed_context === 'number') ? connection.observed_context : null;
  const context = (sampled !== null) ? sampled : ((typeof connection.context === 'number') ? connection.context : null);
  const sampledAlias = (typeof connection.observed_model_alias === 'string' && connection.observed_model_alias) ? connection.observed_model_alias : null;
  const alias = sampledAlias || ((typeof connection.wire_model === 'string' && connection.wire_model) ? connection.wire_model : null);
  const declared = (context !== null)
    ? ait('declared context {context} tokens', {context})
    : ait('declared context unknown');
  if(!runnable.length){
    const reason = statusText || ait('local inference runtime not installed');
    el.textContent = ait('Local: unavailable ({reason}). Active path: API model ({arms} arms by task).', {reason, arms});
  } else if(probedReady){
    el.textContent = ait('Local: ready — {models} ({alias}, {declared}). Nothing left this machine for the readiness check, and no API cost is metered. Active path: API model ({arms} arms by task).', {models: runnable.map(m => m.id).join(', '), alias: alias || ait('configured alias'), declared, arms});
  } else {
    const probed = (typeof probe.status === 'string' && probe.status)
      ? ait('probe result: {status}', {status: probe.status})
      : ait('not probed yet; press the local readiness check');
    el.textContent = ait('Local: configured but not ready ({probed}; {declared}). Active path: API model ({arms} arms by task).', {probed, declared, arms});
  }
  return el.textContent;
}
// Explicit, user-triggered readiness check. Sends one bodyless request to the
// configured loopback server: no research text, no files, and no API cost. The
// per-run bearer the app generated at launch authorizes it and never leaves the
// app process: it is not sent from here, not rendered, not stored. This is the
// only way the local row can ever render as ready.
async function probeLocalRuntime(){return action(async()=>{
  const result=await call('POST','/ai/verify-account',{provider:'local'});
  if(S.question)await loadAI(S.question);
  return result;
});}
let aiPreview=null,aiQuestion=null,aiEpoch=0;
let voiceConfirmTargets={},voiceConfirmNotice={};
let metaKeyEntered=false;
function clearAI(){aiEpoch++;aiPreview=null;aiQuestion=null;voiceConfirmTargets={};voiceConfirmNotice={};try{if(typeof voiceRecReset==='function')voiceRecReset();}catch(_){}['ai-preview-panel'].forEach(id=>$(id).classList.add('hidden'));['ai-assets','ai-results','ai-preview'].forEach(id=>$(id).textContent='');$('ai-consent').checked=false;$('ai-send').disabled=true;$('ai-instruction').value='';$('ai-file').value='';}
async function loadAI(qid){
  const epoch=S.epoch,request=++aiEpoch;
  const [reg,assets,jobs]=await Promise.all([call('GET','/ai/registry'),call('GET',`/questions/${qid}/ai/assets`),call('GET',`/questions/${qid}/ai/jobs`)]);
  if(!current(epoch)||request!==aiEpoch||S.question!==qid)return;
  aiQuestion=qid;voiceConfirmTargets={};voiceConfirmNotice={};aiRegistry=reg;$('ai-cloud-enabled').checked=reg.settings.cloud_enabled;$('ai-budget').value=reg.settings.budget_usd;
  $('ai-connection').textContent=ait('Key: {key}. Cloud: {cloud}. Local allowance remaining: {amount}. Provider credit balance: unknown.', {key: reg.settings.baseten_key_configured?ait('configured'):ait('missing'), cloud: reg.settings.cloud_enabled?ait('on'):ait('off'), amount: '$' + reg.settings.local_allowance_remaining_usd.toFixed(2)});
  renderKeyStatus(reg);
  $('ai-baseten-status').textContent=(reg.settings.provider_catalogs?.baseten?.catalog||[]).includes(BASETEN_SLUG)?ait('Baseten account: model listed.'):ait('Baseten account: not verified.');
  // Automatic tasks must resolve to a model offered by this picker too.
  const previousModel=$('ai-model').value||'auto';
  const executable=reg.models.filter(m=>m.endpoint&&(m.id==='meta-models/muse-spark-open-1026'||modelRoute(m.id)==='local'||(m.id===MODAL_SHARED_MODEL_ID&&reg.providers?.some(p=>p.id===MODAL_SHARED_PROVIDER&&p.prerequisites_ready===true))));
  const taskSelect=$('ai-task'),previousTask=taskSelect.value;
  const taskTemplate=$('ai-task-options');
  const tasks=Array.from(taskTemplate?.content?.querySelectorAll('option')||[]).filter(option=>
    executable.some(model=>model.id===reg.tasks?.[option.value]));
  taskSelect.innerHTML=tasks.map(option=>`<option value="${esc(option.value)}">${esc(ait(option.textContent))}</option>`).join('');
  taskSelect.value=tasks.some(option=>option.value===previousTask)?previousTask:'research_query';
  taskSelect.disabled=!tasks.length;
  if(previousTask&&previousTask!==taskSelect.value){
    aiPreview=null;$('ai-consent').checked=false;$('ai-preview-panel').classList.add('hidden');
  }
  $('ai-model').innerHTML='<option value="auto">'+ait('Automatic by task')+'</option>'+executable.map(m=>{
    const route=modelRoute(m.id);const listed=(reg.settings.provider_catalogs?.[route]?.catalog||[]).includes(m.id);
    const label=MODEL_LABELS[m.id]?ait(MODEL_LABELS[m.id]):route==='local'?ait('Qwen3 4B Instruct (local)'):m.id;
    return `<option value="${esc(m.id)}">${esc(label)} / ${esc(route)} / ${ait('account {state}', {state: listed?ait('listed'):ait('not verified')})}</option>`;
  }).join('');
  // Re-rendering the picker must not move the researcher's chosen route. A
  // refresh that follows an account check keeps the same selection when this
  // registry still offers it as executable, so Prepare sends the route the
  // researcher chose rather than the default. This never selects a route the
  // researcher did not pick: an unlisted or now-unavailable choice falls back to
  // automatic, and any prepared transmission is discarded with it.
  const kept=previousModel!=='auto'&&executable.some(m=>m.id===previousModel);
  $('ai-model').value=kept?previousModel:'auto';
  if(previousModel!=='auto'&&!kept){
    aiPreview=null;$('ai-consent').checked=false;$('ai-preview-panel').classList.add('hidden');
  }
  updateAIControls();
  renderLocalStatus(reg);
  renderModalStatus(reg);
  renderModalSharedStatus(reg);
  $('ai-assets').innerHTML=assets.assets.map(a=>`<label><input type="checkbox" data-ai-asset="${esc(a.id)}"> ${esc(a.name)} / ${esc(a.kind)} / ${ait('{bytes} bytes', {bytes: esc(a.bytes)})} / ${ait('local')}</label>`).join('');
  renderAIJobs(jobs.jobs,await fetchAIReceipts(qid,jobs.jobs),await fetchAIReviews(qid,jobs.jobs));
}
async function fetchAIReceipts(qid,jobs){
  const entries=await Promise.all((jobs||[]).filter(j=>j&&typeof j.id==='string').map(async j=>{
    try{return [j.id,await call('GET',`/questions/${qid}/ai/jobs/${j.id}/receipt`)];}
    catch(e){return [j.id,null];}
  }));
  return Object.fromEntries(entries);
}
async function configureAI(){return action(async()=>{await call('POST','/ai/settings',{cloud_enabled:$('ai-cloud-enabled').checked,budget_usd:Number($('ai-budget').value)});if(S.question)await loadAI(S.question);});}
async function fetchAIReviews(qid,jobs){
  const entries=await Promise.all((jobs||[]).filter(j=>j&&typeof j.id==='string').map(async j=>{
    try{return [j.id,await call('GET',`/questions/${qid}/ai/jobs/${j.id}/review`)];}
    catch(e){return [j.id,null];}
  }));
  return Object.fromEntries(entries);
}
// Which provider serves a model id. Mirrors the server rule (app/ai/service.py
// _route_provider): the Baseten-hosted slug never leaves on the Meta wire, so
// the picker must show the same route the job will actually use.
const BASETEN_SLUG='meta-models/muse-spark-open-1026';
const MODAL_GLIMMER_ID='meta-models/Muse-Glimmer-30B';
// Modal Shared: a different provider id from the old GPU-hosted 'modal'
// Glimmer route, so the old hardblock below never applies to it. The internal
// model id is fixed by the backend contract; the readable label is this pane's
// own words, not the remote repository slug.
const MODAL_SHARED_PROVIDER='modal_shared';
const MODAL_SHARED_MODEL_ID='modal-shared:muse-spark-open';
const OPENAI_TEST_MODEL_ID='gpt-5.4-mini-2026-03-17';
const MODEL_LABELS={[MODAL_SHARED_MODEL_ID]:'Muse Spark Open (Modal Shared)',[BASETEN_SLUG]:'Muse Spark Open (Baseten)'};
// Local provider id and the reserved `local:` id scheme. The app-local id is
// assigned by the backend (app/ai/local_runtime.py); this pane recognises it by
// the scheme prefix and its `local_reasoning` kind rather than duplicating the
// exact tag, so a backend id change never silently routes local work to Meta.
const LOCAL_PROVIDER='local';
const LOCAL_ID_PREFIX='local:';
const LOCAL_KIND='local_reasoning';
function isLocalModelId(id){
  if(typeof id!=='string'||id.indexOf(LOCAL_ID_PREFIX)!==0)return false;
  // The reserved placeholder row is a non-executable example, never a route.
  const row=aiModelRow(id);
  return !row||row.kind===LOCAL_KIND;
}
function modelRoute(id){
  if(id===OPENAI_TEST_MODEL_ID)return'openai';
  if(id===MODAL_GLIMMER_ID)return'modal';
  if(id===MODAL_SHARED_MODEL_ID)return MODAL_SHARED_PROVIDER;
  // Checked before the Meta fallback: a local id on the Meta wire would be a
  // cloud transmission of research material under the wrong provider label.
  if(isLocalModelId(id))return LOCAL_PROVIDER;
  return id===BASETEN_SLUG||id==='meta-models/muse-spark-open'?'baseten':'meta';
}
// Registry snapshot the picker was rendered from, so a typed-task check can
// compare the selected model's kind with the kind the task route needs.
let aiRegistry=null;
function aiModelRow(id){
  const models=(aiRegistry&&Array.isArray(aiRegistry.models))?aiRegistry.models:[];
  return models.find(m=>m&&m.id===id)||null;
}
function taskKind(task){
  const tasks=(aiRegistry&&aiRegistry.tasks)||{};
  const id=Object.prototype.hasOwnProperty.call(tasks,task)?tasks[task]:null;
  if(!id)return null;
  const row=aiModelRow(id);
  return row&&typeof row.kind==='string'&&row.kind?row.kind:null;
}
// A typed task needs the kind its own route binds. When the registry is loaded
// and names a different kind, the preview would be refused by the backend, so
// this pane refuses it first with the same rule and sends nothing.
function aiTaskMismatch(){
  const el=(typeof $ === 'function')?$('ai-model'):null;
  const model=el&&el.value;
  if(!model||model==='auto')return null;
  const kind=(aiModelRow(model)||{}).kind;
  const taskEl=$('ai-task');
  const expected=taskKind(taskEl?taskEl.value:null);
  if(typeof kind!=='string'||!kind||!expected||kind===expected)return null;
  // A local row is its own kind on purpose (so a cloud route can never resolve
  // it), but it serves exactly the reasoning tasks. Comparing raw kinds would
  // refuse every local request, so the local kind is judged as reasoning.
  if(kind===LOCAL_KIND&&expected==='reasoning')return null;
  return {kind,expected};
}
function renderModalStatus(reg){
  const el=$('ai-modal-status');
  if(!el)return;
  // Only the backend's public connection metadata is consumed. Never probe
  // Modal: even a catalog request may cold-start a billable GPU.
  const connection=reg?.settings?.modal_connection||(reg?.providers||[]).find(p=>p?.id==='modal')||{};
  el.textContent=connection.endpoint_configured===true&&typeof connection.base_url==='string'&&connection.base_url
    ?ait('Endpoint configured: {url}. Configuration readiness only; runtime and account access have not been verified.', {url:connection.base_url})
    :ait('Endpoint not deployed/configured. Configuration readiness only; runtime and account access have not been verified.');
  return el.textContent;
}
// Modal Shared readiness. Two separate facts are reported and never merged:
// configuration readiness (an endpoint and a key are present) and account
// verification (this machine asked the backend to check, and the backend's own
// per-provider catalog is the record). Rendering never itself contacts Modal, so
// opening the panel issues no account, metadata or inference request.
function renderModalSharedStatus(reg){
  const el=$('ai-modal-shared-status');
  if(!el)return;
  const providers=(reg&&Array.isArray(reg.providers))?reg.providers:[];
  const entry=providers.find(p=>p&&p.id===MODAL_SHARED_PROVIDER)||{};
  const settings=(reg&&reg.settings)||{};
  const connection=settings.modal_shared_connection||entry;
  const url=(typeof connection.base_url==='string'&&connection.base_url)?connection.base_url:null;
  const configured=connection.endpoint_configured===true&&!!url;
  // Remote model id is a second, separate prerequisite: a configured origin
  // with no pinned remote model cannot serve this route.
  const remotePinned=connection.remote_model_id_configured!==false;
  const keyPresent=typeof connection.key_present==='boolean'?connection.key_present:providerKeyPresent(MODAL_SHARED_PROVIDER,reg);
  const grant=settings.provider_catalogs?.[MODAL_SHARED_PROVIDER];
  const catalog=grant?.catalog;
  const age=Date.now()/1000-grant?.catalog_at;
  const verified=Array.isArray(catalog)&&catalog.includes(MODAL_SHARED_MODEL_ID)&&
    grant.catalog_valid_for_current_key===true&&age>=0&&age<=86400;
  // The backend owns executable readiness. A route it reports as not having its
  // prerequisites met is shown blocked with its own reason list; nothing here
  // upgrades it.
  const blocked=connection.prerequisites_ready===false||connection.execution_enabled===false;
  const reasons=Array.isArray(connection.blocking_reasons)?connection.blocking_reasons:[];
  let text;
  if(!configured)text=ait('Modal Shared: endpoint not configured in this runtime. Configuration readiness only; nothing was sent.');
  else if(!remotePinned)text=ait('Modal Shared: endpoint {url} configured, but no remote model id is pinned in this runtime. Configuration readiness only; nothing was sent.', {url});
  else if(!keyPresent)text=ait('Modal Shared: endpoint {url} configured, but no key is saved for this provider yet. Configuration readiness only; nothing was sent.', {url});
  else if(blocked)text=ait('Modal Shared: endpoint {url} configured and key present, but the backend still reports the route blocked ({reason}). Nothing was sent.', {url, reason: reasons.length?reasons.join(', '):ait('unknown')});
  else text=ait('Modal Shared: endpoint {url} configured and key present. Configuration readiness only; runtime and account access are not verified until you run the account check.', {url});
  const verifiedText=verified
    ?ait('Account check: the backend lists {model} for this provider.', {model: MODAL_SHARED_MODEL_ID})
    :ait('Account check: not verified for this provider (or verified for a different key).');
  el.textContent=text+' '+verifiedText;
  return el.textContent;
}
if(WB_GLOBAL_AI.WB_I18N)WB_GLOBAL_AI.WB_I18N.onChange(()=>{
  if(aiRegistry)renderModalSharedStatus(aiRegistry);
});
// Key fields are write-only containers: they show whether each provider has a
// key and never carry a stored value back into the page.
// Presence is read from flags the registry already publishes. The backend may
// report a provider's presence either on its settings entry
// (settings.<id>_key_configured) or on its own provider row; both shapes are
// accepted. A missing flag reads as no key, never as present.
function providerKeyPresent(provider,reg){
  const settings=(reg&&reg.settings)||{};
  const providers=(reg&&Array.isArray(reg.providers))?reg.providers:[];
  const flagNames=['key_configured','key_present','configured'];
  for(const name of flagNames){
    if(typeof settings[provider+'_'+name]==='boolean')return settings[provider+'_'+name];
  }
  const entry=providers.find(p=>p&&p.id===provider);
  if(entry)for(const name of flagNames)if(typeof entry[name]==='boolean')return entry[name];
  return false;
}
function renderKeyStatus(reg){
  const providers=(reg&&reg.providers)||[],settings=(reg&&reg.settings)||{};
  for(const p of providers){
    const id=p&&p.id,el=id&&$('ai-key-status-'+id);
    if(!el)continue;
    const present=id==='meta'?metaKeyEntered:providerKeyPresent(id,reg);
    el.textContent=present?ait('Saved on this machine.'):ait('No key saved yet.');
  }
}
async function saveAIKey(provider){
  const input=$('ai-key-'+provider),status=$('ai-key-status-'+provider);
  const value=input?input.value:'';
  if(!value||value!==value.trim()||/\s/.test(value))return showError(ait('Paste the full key with no spaces.'));
  return action(async()=>{
    const result=await call('POST','/ai/keys',{provider,key:value});
    if(provider==='meta')metaKeyEntered=true;
    if(input)input.value='';
    if(status)status.textContent=(result.env_override?ait('Saved. {name} currently overrides it in this process.', {name: result.env_override}):ait('Saved on this machine.'));
    if(S.question)await loadAI(S.question);
  });
}
// Verify the account of the route currently chosen in the picker, so a Baseten
// model is never "verified" against the Meta account (and vice versa).
async function verifyAIAccount(){return action(async()=>{
  const el=$('ai-model'),provider=el&&el.value&&el.value!=='auto'?modelRoute(el.value):'baseten';
  if(provider==='meta')return;
  if(provider==='modal')return showError(ait('Modal live execution is disabled. No account check or inference request was sent.'));
  const result=await call('POST','/ai/verify-account',{provider});
  $('ai-connection').textContent=ait('{provider} account lists {count} models. No inference request sent.', {provider, count: result.catalog.length});
  if(S.question)await loadAI(S.question);
});}
// The Modal Shared route has its own account check, because its credential is
// its own key and never the Meta or Baseten one. This is the only path in this
// pane that asks the backend to contact the provider, and it runs only when the
// researcher presses the button. It sends no research material.
async function verifyModalSharedAccount(){return action(async()=>{
  const status=$('ai-modal-shared-status');
  const result=await call('POST','/ai/verify-account',{provider: MODAL_SHARED_PROVIDER});
  const models=Array.isArray(result&&result.catalog)?result.catalog:[];
  if(status&&Array.isArray(models)&&models.length){
    status.textContent=ait('Account check: the backend lists {count} model(s) for {provider}: {list}. No inference request sent.', {provider: MODAL_SHARED_PROVIDER, count: models.length, list: models.join(', ')});
  }
  if(S.question)await loadAI(S.question);
});}
async function registerAIAsset(){if(!S.question)return showError(ait('Open or save a question first.'));return action(async epoch=>{
  const qid=S.question,file=$('ai-file').files[0];if(!file)throw Error(ait('Select a file.'));if(file.size>8*1024*1024)throw Error(ait('File exceeds 8 MiB.'));
  const types={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',pdf:'application/pdf',mp4:'video/mp4',wav:'audio/wav',txt:'text/plain'};
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  if(!current(epoch)||S.question!==qid)return;
  await call('POST',`/questions/${qid}/ai/assets`,{name:file.name,mime:types[file.name.split('.').at(-1).toLowerCase()],encoded:btoa(binary)});aiPreview=null;$('ai-consent').checked=false;await loadAI(qid);
});}
async function prepareAI(){if(!S.question)return showError(ait('Open or save a question first.'));return action(async epoch=>{
  const route=modelRoute($('ai-model').value);
  if(route==='modal')return showError(ait('Modal live execution is disabled. No account check or inference request was sent.'));
  // A typed task needs the kind its own route binds, and the backend enforces
  // that rule. Refusing here means an impossible pair never reaches the
  // network, and the message names the mismatch instead of a bare conflict.
  const mismatch=aiTaskMismatch();
  if(mismatch)return showError(ait('This model kind ({kind}) cannot perform the selected task, which needs kind {expected}. Choose a matching model. No request was sent.', mismatch));
  const qid=S.question;
  // Provider is only sent when the model id does not itself identify the route,
  // so the existing Meta and Baseten payloads stay byte-identical.
  const body={task:$('ai-task').value,model:$('ai-model').value,mode:'cloud',text:$('ai-instruction').value,assets:[...document.querySelectorAll('[data-ai-asset]:checked')].map(el=>el.dataset.aiAsset),harness:$('ai-harness').checked,compare:$('ai-compare').checked,max_output_tokens:2048,max_cost_usd:Number($('ai-max-cost').value)};
  if(route===MODAL_SHARED_PROVIDER)body.provider=MODAL_SHARED_PROVIDER;
  if(route===LOCAL_PROVIDER){
    // Local mode is its own mode on the backend: it resolves the one configured
    // loopback model, reserves $0 and refuses a cloud provider. Sending
    // mode:'cloud' for a local pick would be a routing lie.
    body.mode='local';
    body.provider=LOCAL_PROVIDER;
    // The local path is text only; a selected file would be refused anyway, so
    // the payload states it rather than relying on that refusal.
    body.assets=[];
  }
  if(route==='openai'){
    body.provider='openai';
    body.public_test_data=!!$('ai-public-test-data')?.checked;
  }
  const preview=await call('POST',`/questions/${qid}/ai/prepare`,body);
  if(!current(epoch)||qid!==S.question)return;
  aiPreview=preview;$('ai-consent').checked=false;$('ai-send').disabled=false;$('ai-preview-panel').classList.remove('hidden');$('ai-preview-panel').open=true;$('ai-preview').textContent=ait('Data-use mode: {mode}', {mode: preview.data_use_mode??ait('unknown')})+'\n'+JSON.stringify(preview,null,2);
});}
async function executeAI(){if(modelRoute($('ai-model').value)==='modal'||aiPreview?.provider==='modal'||modelRoute(aiPreview?.model)==='modal'){ $('ai-send').disabled=true;return showError(ait('Modal live execution is disabled. No account check or inference request was sent.')); }if(!aiPreview||!$('ai-consent').checked)return showError(ait('Review and approve the prepared transmission first.'));const p=aiPreview;aiPreview=null;return action(async epoch=>{
  $('ai-send').disabled=true;const qid=S.question;
  try{await call('POST',`/ai/jobs/${p.job_id}/execute`,{preview_hash:p.preview_hash,consent:true});}
  finally{if(current(epoch)&&S.question===qid)await loadAI(qid);}
});}
function segBoxStatus(box){
  const vals=['x1','y1','x2','y2','w','h'].map(k=>Number(box?.[k]));
  if(vals.some(v=>typeof v!=='number'||!isFinite(v)))return{ok:false,inBounds:false,label:ait('invalid geometry (non-finite or missing)'),box:null};
  const[x1,y1,x2,y2,w,h]=vals;
  if(!(w>0&&h>0))return{ok:false,inBounds:false,label:ait('invalid geometry (non-positive frame)'),box:null};
  if(x2<x1||y2<y1)return{ok:false,inBounds:false,label:ait('out of range (inverted corners)'),box:null};
  const shaped={x1,y1,x2,y2,w,h};
  if(x2===x1||y2===y1)return{ok:false,inBounds:false,label:ait('degenerate (zero area)'),box:shaped};
  if(x1<0||y1<0||x2>w||y2>h)return{ok:false,inBounds:false,label:ait('out of range'),box:shaped};
  return{ok:true,inBounds:true,label:ait('in bounds'),box:shaped};
}
// The label a segmentation object carries beside its id: the model's own label
// verbatim, never translated, so the reader sees the recorded output.
function segObjectLabel(o,i){
  const id=String(o?.object_id??i);
  const own=(typeof o?.label==='string'&&o.label)?o.label:null;
  return '#'+esc(id)+' '+(own!=null?esc(own):ait('object {id}', {id: esc(id)}));
}
function segReported(o){
  return (o&&o.source_dimensions_origin==='server_decoded'&&o.geometry&&typeof o.geometry.in_bounds==='boolean')
    ?{inBounds:o.geometry.in_bounds,label:o.geometry.in_bounds?ait('in bounds (server)'):ait('out of range (server)'),box:o.box}
    :segBoxStatus(o?.box);
}
function segOverlay(out){
  const objects=Array.isArray(out.objects)?out.objects:[];
  const src=out.source_asset||{};
  const imgUrl=src.id?esc(API+'/questions/'+aiQuestion+'/ai/assets/'+src.id+'/content'):'';
  const pct=(v,m)=>String(Math.max(0,Math.min(100,v/m*100)));
  const overlays=objects.map((o,i)=>{
    const reported=segReported(o);
    const b=reported.box;
    const drawable=b&&isFinite(b.w)&&b.w>0&&isFinite(b.h)&&b.h>0;
    const style=drawable?`left:${pct(b.x1,b.w)}%;top:${pct(b.y1,b.h)}%;width:${pct(b.x2-b.x1,b.w)}%;height:${pct(b.y2-b.y1,b.h)}%;`:'display:none;';
    const color=reported.inBounds?'#1a7f37':'#b42318';
    const label=`${segObjectLabel(o,i)} / ${reported.label}`;
    return `<div title="${label}" style="position:absolute;${style}outline:2px solid ${color};box-sizing:border-box;"><span style="background:${color};color:#fff;font-size:11px;padding:0 4px;position:absolute;top:-18px;left:0;white-space:nowrap;">${label}</span></div>`;
  }).join('');
  const rows=objects.map((o,i)=>{
    const reported=segReported(o);
    const label=`${segObjectLabel(o,i)} / ${reported.label}`;
    return `<li>${label}${reported.box?'':ait(' (no drawable geometry)')}</li>`;
  }).join('');
  const raw=esc(JSON.stringify(objects.map(o=>({frame:o.frame_index,id:o.object_id,box:o.box,mask:o.mask?{height:o.mask.height,width:o.mask.width,encoding:o.mask.encoding}:null})),null,2));
  const finiteDim=v=>typeof v==='number'&&isFinite(v)&&v>0;
  const decoded=objects.filter(o=>o&&o.source_dimensions_origin==='server_decoded'&&finiteDim(o.source_image_width)&&finiteDim(o.source_image_height));
  const boundsNote=(objects.length&&decoded.length===objects.length)
    ?ait('Bounds status checked server-side against server-decoded source dimensions ({width}x{height}), not the model-declared frame size.', {width: decoded[0].source_image_width, height: decoded[0].source_image_height})
    :(decoded.length
      ?ait('Bounds status checked server-side against server-decoded source dimensions for {checked} of {total} objects; the remainder fall back to client-side checks against the model-declared frame size.', {checked: decoded.length, total: objects.length})
      :ait('Bounds status is checked client-side against the model-declared frame size, not independently verified source dimensions.'));
  return `<h3 class="wb-arm">${ait('Segmentation geometry')}</h3><p>${ait('{count} object records. Boxes are model-proposed geometry in source-pixel coordinates, not verified evidence. Positions are scaled to the displayed image. Raster masks are not rendered (NOT_IMPLEMENTED).', {count: objects.length})}</p>`
    +(imgUrl?`<figure><div style="position:relative;display:inline-block;max-width:100%;"><img alt="${ait('Source image for segmentation review')}" src="${imgUrl}" style="max-width:100%;display:block;">${overlays}</div><figcaption>${ait('Source SHA-256: {hash}. {note}', {hash: esc(src.sha256)||ait('unknown'), note: boundsNote})}</figcaption></figure>`:`<p class="flag">${ait('Source image unavailable for overlay.')}</p>`)
    +`<ul>${rows}</ul><details><summary>${ait('Raw geometry records')}</summary><pre>${raw}</pre></details>`;
}
function targetVoiceConfirm(job){
  const jobId=(job&&typeof job.id==='string')?job.id:'';
  if(String(job&&job.status)!=='completed')return null;
  const out=job&&job.result&&job.result.outputs;
  if(!out||typeof out!=='object')return null;
  if(typeof out.transcript!=='string')return null;
  const src=out.source_asset;
  if(!src||typeof src!=='object')return null;
  const sid=(typeof src.id==='string'&&src.id)?src.id:null;
  const sha=(typeof src.sha256==='string'&&src.sha256)?src.sha256:null;
  if(!jobId||!sid||!sha)return null;
  return {jobId:jobId,assetId:sid,sha256:sha,transcript:out.transcript};
}
function presetConfirmText(jobId){
  const target=voiceConfirmTargets[jobId];
  const area=$('voice-confirm-'+jobId);
  if(target&&area)area.value=target.transcript;
}
async function submitVoiceConfirm(jobId){
  if(!aiQuestion)return showError(ait('Open or save a question first.'));
  const target=voiceConfirmTargets[jobId];
  if(!target)return showError(ait('This transcription is not eligible for confirmation.'));
  const area=$('voice-confirm-'+jobId);
  const confirmedText=area&&typeof area.value==='string'?area.value:'';
  return action(async epoch=>{
    const qid=aiQuestion;
    let resp;
    try{
      resp=await call('POST',`/questions/${qid}/ai/voice-confirm`,{confirmed_text:confirmedText,source_asset_id:target.assetId,source_sha256:target.sha256});
    }catch(e){
      if(current(epoch)&&qid===S.question){
        const el=$('voice-confirm-status-'+jobId);
        if(el)el.textContent=ait('Confirmation failed: {reason}', {reason: (e&&e.message)||String(e)});
      }
      throw e;
    }
    if(!current(epoch)||qid!==S.question)return;
    voiceConfirmNotice[jobId]={ok:true,text:(resp&&resp.question_id)?String(resp.question_id):''};
    await loadAI(qid);
  });
}
function renderVoiceConfirm(target){
  const errSlug='voice-confirm-'+target.jobId;
  voiceConfirmTargets[target.jobId]=target;
  const notice=voiceConfirmNotice[target.jobId];
  // Only the surrounding sentence is translated; a server-supplied detail reaches
  // the reader as data and stays escaped.
  const noticeText=notice
    ?(notice.ok?ait('Question created: {id}', {id: esc(notice.text)||ait('unknown')}):ait('Confirmation failed: {reason}', {reason: esc(notice.text)}))
    :'';
  return `<div class="wb-confirm"><h3 class="wb-arm">${ait('Confirm transcript as question text')}</h3>`
    +`<p class="muted">${ait('The transcript below is a proposal only. Nothing is created until you explicitly confirm. The recorder above captures microphone audio as 16-bit PCM mono WAV at 16 kHz; if it reports NOT_SUPPORTED, upload a validated WAV instead (RECORDER_FORMAT_GAP).')}</p>`
    +`<label for="${esc(errSlug)}">${ait('Proposed question text (editable)')}</label>`
    +`<textarea id="${esc(errSlug)}" rows="3">${esc(target.transcript)}</textarea>`
    +`<div class="row"><button type="button" onclick="presetConfirmText('${esc(target.jobId)}')">${ait('Use verbatim transcript')}</button>`
    +`<button type="button" onclick="submitVoiceConfirm('${esc(target.jobId)}')">${ait('Confirm as question text')}</button></div>`
    +`<p id="voice-confirm-status-${esc(target.jobId)}" role="status">${noticeText}</p></div>`;
}
function chipTone(status){const s=String(status??'');if(s==='completed')return' data-tone="ok"';if(s.indexOf('failed')===0||s.indexOf('interrupted')===0)return' data-tone="fail"';if(s.indexOf('unknown_charge')>=0)return' data-tone="warn"';return'';}
// Release-year applicability recorded by the grounding check. Only the
// NOT_CHECKED state is surfaced here: a question with no release year makes the
// year comparison impossible, so the panel says so explicitly instead of
// letting a structurally valid result read as if the year check had passed.
// (A year mismatch already appears as a structural error; a match or an
// undeclared note needs no extra line.) Returns null when there is nothing to
// add, so the existing arm rows are unchanged for records without this block.
function releaseScopeNotChecked(grounding){
  const scope=grounding&&grounding.release_year_applicability;
  if(!scope||typeof scope!=='object'||scope.status!=='NOT_CHECKED')return null;
  return `<span class="muted" data-release-scope="not_checked">${ait('Release-year applicability: NOT checked. This question has no usable release year, so no year comparison was possible. This is not a year-check pass.')}</span>`;
}
function renderAIJobs(jobs,receipts,reviews){
  const money4=v=>(typeof v==='number'&&isFinite(v))?'$'+v.toFixed(4):null;
  const money6=v=>(typeof v==='number'&&isFinite(v))?'$'+v.toFixed(6):null;
  const knownNumber=v=>(typeof v==='number'&&isFinite(v))?v:null;
  $('ai-results').innerHTML=(jobs||[]).map(j=>{
    const receipt=j&&typeof j.id==='string'&&receipts?receipts[j.id]:null;
    const stored=j&&typeof j.id==='string'&&reviews?reviews[j.id]:null;
    const hasServerReceipt=receipt&&typeof receipt.receipt_version==='string';
    const result=j?.result||null,out=result?.outputs||result?.partial_outputs;let content='';
    const recModel=(result&&typeof result.model==='string'&&result.model)||[j?.model,j?.request?.model,result?.request?.model].find(v=>typeof v==='string'&&v)||'';
    const localJob=recModel.indexOf('local:')===0||(typeof j?.mode==='string'&&j.mode==='local')||(result&&typeof result.provider==='string'&&result.provider==='local');
    if(localJob)content+=`<p class="muted">${ait('Local job: executed on the local path. Quality measured, not benchmarked against API models.')}</p>`;
    // Translated output is already safe and goes in unescaped; every value that
    // came from the server or the model is escaped as it enters a {placeholder}.
    for(const arm of ['basic','harness']) if(out?.[arm]){
      const a=out[arm];content+=`<h3 class="wb-arm">${arm==='basic'?ait('Direct single-call baseline'):ait('Research harness')}</h3><p>${esc(a.result.answer)}</p><ul>${a.result.claims.map(c=>`<li>${esc(c.text)} <small>${esc(c.kind)} / ${esc(c.evidence_ids.join(', '))||ait('No evidence linked')}</small></li>`).join('')}</ul><p class="muted">${a.grounding.structural_valid?ait('Evidence IDs and variable bindings passed structural checks.'):ait('Structural validation failed: {errors}', {errors: esc(a.grounding.errors.join('; '))})} ${ait('Meaning and applicability require review.')}${releaseScopeNotChecked(a.grounding)?' '+releaseScopeNotChecked(a.grounding):''}</p>`;
    }
    if(out?.transcript){const asset=(out&&out.source_asset)||{};const assetId=(typeof asset.id==='string'&&asset.id)?asset.id:null;const voiceTarget=targetVoiceConfirm(j);if(voiceTarget)content+=renderVoiceConfirm(voiceTarget);content+=`<h3 class="wb-arm">${ait('Interview transcript')}</h3>`+(assetId?`<audio controls preload="none" src="${esc(API+'/questions/'+aiQuestion+'/ai/assets/'+assetId+'/content')}"></audio>`:`<p class="flag">${ait('Source audio unavailable.')}</p>`)+`<p>${esc(out.transcript)}</p><ul>${(out.turns||[]).map(t=>`<li>${ait('{start} to {end} ms / speaker {speaker}: {text}', {start: esc(t.startMs), end: esc(t.endMs), speaker: esc(t.speaker||ait('unknown')), text: esc(t.transcript)})}</li>`).join('')}</ul><p class="muted">${ait('Turn timestamps and speaker labels are model outputs. Speaker identity is unverified. Source SHA-256: {hash}', {hash: esc(asset.sha256)||ait('unknown')})}</p>`;}
    if(out?.objects)content+=segOverlay(out);
    if(out?.images)content+=out.images.map(img=>`<figure><img style="width:100%" alt="${ait('AI-generated concept image, not research evidence')}" src="data:image/png;base64,${esc(img.base64)}"><figcaption>${ait('AI-generated concept image. Not research evidence. SHA-256: {hash}', {hash: esc(img.sha256)})}</figcaption></figure>`).join('');
    if(result?.error_code)content+=`<p class="flag">${ait('{code}. No automatic retry. Review charge uncertainty, then prepare a new task explicitly.', {code: esc(result.error_code)})}</p>`;
    if(result?.error_code&&Array.isArray(result.validation_issues))content+=`<p>${ait('Validation issues: {count}.', {count: esc(result.validation_issues.length)})}</p>`;
    const status=String(j?.status??'unknown');
    const dataUse=j?.data_use_mode??result?.data_use_mode;
    // An unknown-charge status means the provider charge was never determined,
    // so no settled dollar figure may be rendered for it: any amount shown
    // would be fabricated.
    const unknownCharge=status.indexOf('unknown_charge')>=0||(hasServerReceipt&&String(receipt.job_status??'').indexOf('unknown_charge')>=0);
    // Local settled fields are only ever a labelled fallback. A server receipt
    // is the sole authority for settlement when present, and the receipt
    // publishes no settled amount (receipt.py settles nothing it cannot
    // verify), so with a receipt no settled figure is shown at all.
    const localSettled=unknownCharge?null:[result?.settled_cost_usd,j?.settled_cost_usd,result?.settled_known_cost_usd].find(v=>typeof v==='number'&&isFinite(v));
    let charge='';
    if(unknownCharge){
      charge=`<p class="flag" data-charge="unknown">${ait('Charge status: charge UNKNOWN. The provider charge for this run could not be determined, so no settled dollar figure is shown. The maximum reservation ({reservation}) is retained.', {reservation: money4(j?.reservation_usd)??ait('unknown')})}</p>`;
    }else if(status==='completed'){
      const calc=knownNumber(result?.calculated_cost_usd);
      const measured=knownNumber(result?.measured_known_cost_usd);
      const stageBasis=Array.isArray(result?.stages)?result.stages.map(s=>s&&s.cost_basis).find(b=>typeof b==='string'&&b):null;
      const billingStatus=result?.billing_status||'NOT_VERIFIED';
      charge=`<p>${ait('Calculated known cost: {calculated}. Measured known cost: {measured}. Basis: calculated from published rates and returned usage{basis}{date}.', {
        calculated: (calc!=null?money6(calc):ait('unknown'))+(result?.usage_complete===false?ait(' (usage incomplete)'):''),
        measured: measured!=null?money6(measured):ait('unknown'),
        basis: stageBasis?ait('; {basis}', {basis: esc(stageBasis)}):'',
        date: result?.pricing_date?ait('; pricing date {date}', {date: esc(result.pricing_date)}):''
      })}</p><p>${ait('Provider billed cost: UNKNOWN (billing status {status}; this is not a bill).', {status: esc(billingStatus)})}</p>`;
    }else if(status.indexOf('failed')===0||status.indexOf('interrupted')===0||result?.error_code){
      const settled=hasServerReceipt?null:localSettled;
      charge=(settled!=null)?`<p>${ait('Charge status: settled to known charges: {cost} (unverified local value; no server receipt). Any remaining reservation is retained; charge uncertain for unknown portions.', {cost: money6(settled)})}</p>`:`<p>${ait('Charge status: reservation retained; charge uncertain. Maximum reservation fully retained due to unknown charge.')}</p>`;
    }
    const reservation=money4(j?.reservation_usd);
    const accounted=result?((typeof result.calculated_cost_usd==='number'&&isFinite(result.calculated_cost_usd))?money6(result.calculated_cost_usd):ait('unknown')):null;
    const rStages=hasServerReceipt?receipt.stages:(Array.isArray(result?.stages)?result.stages:[]);
    const rModel=hasServerReceipt?(receipt.model||null):[result?.model,j?.model,j?.request?.model,result?.request?.model,...rStages.map(s=>s?.model??s?.model_id)].find(v=>typeof v==='string'&&v)||null;
    const rCalc=hasServerReceipt?knownNumber(receipt.calculated_cost_usd):knownNumber(result?.calculated_cost_usd);
    const rMeas=hasServerReceipt?knownNumber(receipt.measured_known_cost_usd):knownNumber(result?.measured_known_cost_usd);
    const rBilling=hasServerReceipt?receipt.billing_status:(result?.billing_status??null);
    // usage_complete is a stored boolean; the three words below are this panel's own
    // rendering of it, so they are translated like any other label. The server's
    // status slugs beside them stay verbatim, because those are the record's own
    // vocabulary and a researcher compares them against the stored job.
    const rUsage=hasServerReceipt?(receipt.usage_complete===true?'complete':receipt.usage_complete===false?'incomplete':'unknown'):(result?.usage_complete===true?'complete':result?.usage_complete===false?'incomplete':'unknown');
    const rSettled=hasServerReceipt?null:localSettled;
    const rStageRows=rStages.map(s=>{
      const sCost=knownNumber(s?.calculated_cost_usd)??knownNumber(s?.measured_known_cost_usd)??knownNumber(s?.cost_usd)??knownNumber(s?.known_cost_usd);
      const sBasis=typeof s?.cost_basis==='string'?s.cost_basis:null;
      const sElapsed=knownNumber(s?.elapsed_seconds);
      const usage=hasServerReceipt?s?.usage:s;
      const inTok=knownNumber(usage?.input_tokens);
      const outTok=knownNumber(usage?.output_tokens);
      const totTok=knownNumber(usage?.total_tokens);
      const cacheTok=hasServerReceipt?knownNumber(usage?.cached_tokens):null;
      const tokBits=(inTok!=null||outTok!=null||totTok!=null)?ait('input {input} / output {output} / total {total}', {input: inTok??ait('unknown'), output: outTok??ait('unknown'), total: totTok??ait('unknown')}):null;
      const tok=tokBits?tokBits+(cacheTok!=null?ait(' / cached {count}', {count: cacheTok}):''):null;
      return `<li>${ait('{stage} / model {model} / status {status}{basis} / elapsed {elapsed}{tokens} / cost {cost}', {
        stage: esc(s?.stage??s?.name)||ait('unknown'),
        model: esc(s?.model??s?.model_id)||ait('unknown'),
        status: esc(s?.status)||ait('unknown'),
        basis: sBasis?ait(' / basis {basis}', {basis: esc(sBasis)}):'',
        elapsed: sElapsed!=null?esc(sElapsed)+'s':ait('unknown'),
        tokens: tok?ait(' / tokens {tokens}', {tokens: esc(tok)}):'',
        cost: sCost!=null?money6(sCost):ait('unknown')
      })}</li>`;
    }).join('');
    const armOrder=hasServerReceipt&&Array.isArray(receipt.arms)&&receipt.arms.length?receipt.arms:['basic','harness'];
    const rArmRows=armOrder.map(arm=>{
      const a=out?.[arm];
      if(!a)return '';
      if(hasServerReceipt&&receipt.arm_summaries&&receipt.arm_summaries[arm]){
        const sum=receipt.arm_summaries[arm];
        const short=typeof sum.answer_sha256==='string'?sum.answer_sha256.slice(0,12):null;
        const evIds=Array.isArray(sum.evidence_ids)?sum.evidence_ids:[];
        const struct=sum.grounding&&typeof sum.grounding.structural_valid==='boolean'?(sum.grounding.structural_valid?'pass':'fail'):'unknown';
        const scopeNote=releaseScopeNotChecked(sum.grounding);
        return `<li>${ait('{arm}: claims {claims} / evidence IDs {evidence} / answer SHA-256 {digest} / structural {structural} / unresolved {unresolved}', {
          arm: esc(arm),
          claims: esc(sum.claim_count)||ait('unknown'),
          evidence: evIds.length+(evIds.length?' ('+esc(evIds.join(', '))+')':''),
          digest: short?esc(short)+'...':ait('unknown'),
          structural: ait(struct),
          unresolved: esc(sum.unresolved_count)||ait('unknown')
        })}${scopeNote?' '+scopeNote:''}</li>`;
      }
      const claims=Array.isArray(a?.result?.claims)?a.result.claims:[];
      const evCount=claims.reduce((n,c)=>n+(Array.isArray(c?.evidence_ids)?c.evidence_ids.length:0),0);
      const struct=a?.grounding?.structural_valid===true?'pass':a?.grounding?.structural_valid===false?'fail':'unknown';
      const unresolved=Array.isArray(a?.grounding?.errors)?a.grounding.errors.length:null;
      const scopeNote=releaseScopeNotChecked(a?.grounding);
      return `<li>${ait('{arm}: claims {claims} / evidence IDs {evidence} / structural {structural} / unresolved {unresolved}', {
        arm: esc(arm),
        claims: claims.length,
        evidence: evCount,
        structural: ait(struct),
        unresolved: unresolved!=null?unresolved:ait('unknown')
      })}${scopeNote?' '+scopeNote:''}</li>`;
    }).join('');
    const rOutcome=hasServerReceipt&&typeof receipt.outcome==='string'?receipt.outcome:null;
    const rSettleStatus=hasServerReceipt&&typeof receipt.settlement_status==='string'?receipt.settlement_status:null;
    const rExecOrder=hasServerReceipt&&Array.isArray(receipt.execution_order)&&receipt.execution_order.length?receipt.execution_order:null;
    // Result-integrity state is tri-state on the server receipt
    // (result_integrity_verified: true / false / null). An absent or null value
    // is reported as unverified, never as a pass. A false value is surfaced
    // both inside the panel and outside the collapsed <details>, so a failed
    // integrity check can never be hidden behind a summary click or read as a
    // normal receipt.
    const integrityValue=hasServerReceipt?receipt.result_integrity_verified:undefined;
    const integrity=integrityValue===true?'verified':integrityValue===false?'failed':'unverified';
    const integrityRow=integrity==='failed'
      ?`<li class="flag" data-integrity="failed"><strong>${ait('Result integrity: FAILED.')}</strong> ${ait('The stored result failed integrity verification; the cost figures below are NOT trustworthy. Treat this record as untrusted.')}</li>`
      :integrity==='verified'
      ?`<li data-integrity="verified">${ait('Result integrity: verified. The stored result digest matches the persisted record (unsigned SHA-256, not a MAC: it detects post-completion mutation only).')}</li>`
      :`<li class="flag" data-integrity="unverified">${ait('Result integrity: NOT verified. No verified result digest was reported for this row, so integrity is unverified, not passed.')}</li>`;
    const integrityFlag=integrity==='failed'?`<p class="flag" data-integrity="failed"><span class="wb-chip" data-tone="fail">${ait('integrity failed')}</span> <strong>${ait('The stored result failed integrity verification. These figures and outputs are NOT trustworthy.')}</strong></p>`:'';
    const rErr=result?.error_code?`<li>${ait('Error: {code} / validation issues {count}', {code: esc(result.error_code), count: Array.isArray(result.validation_issues)?result.validation_issues.length:(hasServerReceipt&&receipt.validation_issue_count!=null?esc(receipt.validation_issue_count):ait('unknown'))})}</li>`:'';
    const rSettle=unknownCharge
      ?`<li class="flag" data-charge="unknown">${ait('Charge settlement: charge UNKNOWN — no settled dollar figure is shown; the reservation is retained.')}</li>`
      :(rSettled!=null?`<li>${ait('Charge settlement: settled to known charges {cost} (unverified local value; no server receipt). Reservation retained for unknown portions.', {cost: money6(rSettled)})}</li>`
      :(rSettleStatus?`<li>${ait('Charge settlement: {status}. Reservation retained for unknown portions.', {status: esc(rSettleStatus)})}</li>`
      :`<li>${ait('Charge settlement: reservation retained; charge uncertain.')}</li>`));
    const receiptHead=hasServerReceipt?`<li>${ait('Receipt: {version} / outcome {outcome}', {version: esc(receipt.receipt_version), outcome: esc(receipt.outcome)||ait('unknown')})}</li>`:'';
    const trustReceipt=`<details class="wb-receipt"><summary>${ait('Trust Receipt')}</summary><ul>${integrityRow}${receiptHead}<li>${ait('Status: {status}', {status: esc(status)})}</li><li>${ait('Model: {model}', {model: rModel?esc(rModel):ait('unknown')})}</li>${dataUse!=null?`<li>${ait('Data-use mode: {mode}', {mode: esc(dataUse)})}</li>`:`<li>${ait('Data-use mode: {mode}', {mode: ait('unknown')})}</li>`}<li>${ait('Maximum reservation: {amount}', {amount: reservation??ait('unknown')})}</li><li>${ait('Calculated known cost: {amount}', {amount: rCalc!=null?money6(rCalc):ait('unknown')})}</li><li>${ait('Measured known cost: {amount}', {amount: rMeas!=null?money6(rMeas):ait('unknown')})}</li><li>${ait('Provider billed cost: UNKNOWN (not a bill)')}</li><li>${ait('Billing status: {status}', {status: rBilling?esc(rBilling):ait('unknown')})}</li><li>${ait('Usage completeness: {state}', {state: ait(rUsage)})}</li>${rOutcome?`<li>${ait('Outcome: {outcome}', {outcome: esc(rOutcome)})}</li>`:''}${rExecOrder?`<li>${ait('Execution order: {order}', {order: rExecOrder.map(esc).join(ait(' then '))})}</li>`:''}</ul>${rStageRows?`<p>${ait('Stages:')}</p><ul>${rStageRows}</ul>`:`<p>${ait('Stages: none reported.')}</p>`}${rArmRows?`<p>${ait('Outputs:')}</p><ul>${rArmRows}</ul>`:''}${rErr?`<ul>${rErr}</ul>`:''}<ul>${rSettle}</ul>${hasServerReceipt?`<p class="muted">${ait('Server receipt preferred; fallback fields shown only where the server omits them.')}</p>`:''}</details>`;
    const storedReviews=Array.isArray(stored?.reviews)?stored.reviews:[];
    const storedReview=storedReviews.length?`<details class="wb-record"><summary>${ait('Stored review (saved result, {count} saved)', {count: storedReviews.length})}</summary><p class="muted">${ait('Saved human assessment of the recorded output above. Read-only: viewing this never runs a new review or a new inference.')}</p><ul>${storedReviews.map(r=>`<li>${ait('Review {id} / reviewer {reviewer} / rubric {rubric} / gold case {gold}', {id: esc(r.review_id)||ait('unknown'), reviewer: esc(r?.report?.reviewer)||ait('unknown'), rubric: esc(r?.report?.rubric_version)||ait('unknown'), gold: esc(r?.report?.gold_case_id)||ait('unknown')})}</li>`).join('')}</ul><details><summary>${ait('Saved report payload')}</summary><pre>${esc(JSON.stringify(storedReviews.map(r=>r.report),null,2))}</pre></details></details>`:'';
    return `<article class="wb-job"><h3 class="wb-status"><span class="wb-chip"${chipTone(status)}>${esc(status)}</span></h3><p>${ait('Maximum reservation: {amount}', {amount: reservation??ait('unknown')})}${result?ait(' / accounted cost: {amount}', {amount: accounted}):''}</p>${dataUse!=null?`<p>${ait('Data-use mode: {mode}', {mode: esc(dataUse)})}</p>`:''}${integrityFlag}${charge}${content}${trustReceipt}${storedReview}<details class="wb-record"><summary>${ait('Usage and reproducible record')}</summary><pre>${esc(JSON.stringify(result?{stages:result.stages,elapsed_seconds:result.elapsed_seconds,evaluation:out?.evaluation}:null,null,2))}</pre></details></article>`;
  }).join('');
}

function updateAIControls(){ $('ai-send').disabled=S.busy||!S.project||!aiPreview||!$('ai-consent').checked||modelRoute($('ai-model').value)==='modal'||aiPreview?.provider==='modal'||modelRoute(aiPreview?.model)==='modal'; }
for(const id of ['ai-task','ai-model','ai-instruction','ai-harness','ai-compare','ai-max-cost','ai-file']){
  $(id).addEventListener('change',()=>{aiPreview=null;$('ai-consent').checked=false;$('ai-preview-panel').classList.add('hidden');updateAIControls();});
}

// --- Korean strings for this file ------------------------------------------
// Keyed by the authored English sentence, exactly as the render calls pass it.
// Sentences that carry a value use {placeholder} so each language keeps its own
// word order. Technical names (API, WAV, PCM, SHA-256, MASK, PDF, NOT_IMPLEMENTED)
// stay as-is: they name a thing the researcher also sees on disk or in the record.
if (WB_GLOBAL_AI.WB_I18N) WB_GLOBAL_AI.WB_I18N.register('ko', {
  'local inference runtime not installed': '로컬 추론 런타임이 설치되어 있지 않습니다',
  'Local: unavailable ({reason}). Active path: API model ({arms} arms by task).':
    '로컬: 사용할 수 없습니다 ({reason}). 현재 경로: API 모델 (작업에 따라 {arms} 중 하나).',
  'Local: configured but not ready ({probed}; {declared}). Active path: API model ({arms} arms by task).':
    '로컬: 설정되었지만 준비되지 않았습니다 ({probed}; {declared}). 현재 경로: API 모델 (작업에 따라 {arms} 중 하나).',
  'Local: ready — {models} ({alias}, {declared}). Nothing left this machine for the readiness check, and no API cost is metered. Active path: API model ({arms} arms by task).':
    '로컬: 사용 가능 — {models} ({alias}, {declared}). 준비 점검에서는 이 컴퓨터를 벗어난 내용이 없고 API 비용도 과금되지 않습니다. 현재 경로: API 모델 (작업에 따라 {arms} 중 하나).',
  'declared context {context} tokens': '선언된 컨텍스트 {context} 토큰',
  'declared context unknown': '선언된 컨텍스트 알 수 없음',
  'configured alias': '설정된 별칭',
  'probe result: {status}': '점검 결과: {status}',
  'not probed yet; press the local readiness check': '아직 점검하지 않았습니다. 로컬 준비 점검을 실행하세요',
  'unknown': '알 수 없음',
  'pass': '통과',
  'fail': '실패',
  'complete': '완전',
  'incomplete': '불완전',
  'Key: {key}. Cloud: {cloud}. Local allowance remaining: {amount}. Provider credit balance: unknown.':
    '키: {key}. 클라우드: {cloud}. 남은 로컬 한도: {amount}. 제공자 크레딧 잔액: 알 수 없음.',
  'configured': '설정됨',
  'missing': '없음',
  'on': '사용',
  'off': '사용 안 함',
  'Automatic by task': '작업에 따라 자동 선택',
  'account {state}': '계정 {state}',
  'listed': '확인됨',
  'not verified': '확인되지 않음',
  '{bytes} bytes': '{bytes}바이트',
  'local': '로컬',
  'Saved on this machine.': '이 컴퓨터에 저장했습니다.',
  'No key saved yet.': '아직 저장된 키가 없습니다.',
  'Paste the full key with no spaces.': '공백 없이 키 전체를 붙여 넣으세요.',
  'Saved. {name} currently overrides it in this process.': '저장했습니다. 현재 이 프로세스에서는 {name} 값이 우선합니다.',
  '{provider} account lists {count} models. No inference request sent.':
    '{provider} 계정에서 모델 {count}개를 확인했습니다. 추론 요청은 보내지 않았습니다.',
  'Open or save a question first.': '먼저 질문을 열거나 저장하세요.',
  'Select a file.': '파일을 선택하세요.',
  'File exceeds 8 MiB.': '파일이 8 MiB를 넘습니다.',
  'Data-use mode: {mode}': '데이터 사용 방식: {mode}',
  'OpenAI API key': 'OpenAI API 키',
  'Save OpenAI key': 'OpenAI 키 저장',
  'OpenAI test: GPT-5.4 Mini. Total reservations are capped at $1 on this local database, even after deleting questions. This is not your provider credit balance. No hosted tools or files.':
    'OpenAI 테스트: GPT-5.4 Mini. 이 로컬 데이터베이스의 누적 예약 상한은 $1이며 질문을 삭제해도 복구되지 않습니다. 제공자의 실제 크레딧 잔액이 아닙니다. 외부 도구나 파일은 사용하지 않습니다.',
  'I confirm the entire transmitted question, notes, decisions and source context are public or synthetic test data. No private research.':
    '전송되는 질문·노트·결정·출처 내용 전체가 공개 자료 또는 가상 테스트 자료이며 비공개 연구 내용이 없음을 확인합니다.',
  'Review and approve the prepared transmission first.': '먼저 준비된 전송 내용을 검토하고 승인하세요.',
  'Modal Shared: endpoint not configured in this runtime. Configuration readiness only; nothing was sent.':
    'Modal 공유: 이 실행 환경에는 엔드포인트가 설정되어 있지 않습니다. 설정 상태만 표시하며 아무것도 전송하지 않았습니다.',
  'Modal Shared: endpoint {url} configured, but no key is saved for this provider yet. Configuration readiness only; nothing was sent.':
    'Modal 공유: 엔드포인트 {url}이(가) 설정되어 있지만 이 제공자의 키가 아직 저장되지 않았습니다. 설정 상태만 표시하며 아무것도 전송하지 않았습니다.',
  'Modal Shared: endpoint {url} configured, but no remote model id is pinned in this runtime. Configuration readiness only; nothing was sent.':
    'Modal 공유: 엔드포인트 {url}은(는) 설정되어 있지만 이 실행 환경에 원격 모델 ID가 고정되어 있지 않습니다. 설정 상태만 표시하며 아무것도 전송하지 않았습니다.',
  'Modal Shared: endpoint {url} configured and key present, but the backend still reports the route blocked ({reason}). Nothing was sent.':
    'Modal 공유: 엔드포인트 {url}과 키가 준비되어 있지만 백엔드가 아직 이 경로를 차단 상태로 보고합니다 ({reason}). 아무것도 전송하지 않았습니다.',
  'Modal Shared: endpoint {url} configured and key present. Configuration readiness only; runtime and account access are not verified until you run the account check.':
    'Modal 공유: 엔드포인트 {url}과 키가 준비되어 있습니다. 설정 상태일 뿐이며, 계정 확인을 직접 실행하기 전까지 실행 환경과 계정 접근은 검증되지 않습니다.',
  'Account check: the backend lists {model} for this provider.':
    '계정 확인: 백엔드가 이 제공자에서 {model}을(를) 확인했습니다.',
  'Account check: not verified for this provider (or verified for a different key).':
    '계정 확인: 이 제공자는 아직 확인되지 않았습니다 (또는 다른 키로 확인된 기록만 있습니다).',
  'Account check: the backend lists {count} model(s) for {provider}: {list}. No inference request sent.':
    '계정 확인: 백엔드가 {provider}에서 모델 {count}개를 확인했습니다: {list}. 추론 요청은 보내지 않았습니다.',
  'This model kind ({kind}) cannot perform the selected task, which needs kind {expected}. Choose a matching model. No request was sent.':
    '이 모델 종류({kind})로는 선택한 작업을 수행할 수 없습니다. 이 작업에는 {expected} 종류가 필요합니다. 맞는 모델을 선택하세요. 요청을 보내지 않았습니다.',
  'invalid geometry (non-finite or missing)': '좌표가 올바르지 않음 (값이 없거나 유한하지 않음)',
  'invalid geometry (non-positive frame)': '좌표가 올바르지 않음 (프레임 크기가 0 이하)',
  'out of range (inverted corners)': '범위를 벗어남 (모서리가 뒤집힘)',
  'degenerate (zero area)': '면적이 0임',
  'out of range': '범위를 벗어남',
  'in bounds': '범위 안',
  'object {id}': '객체 {id}',
  'in bounds (server)': '범위 안 (서버 확인)',
  'out of range (server)': '범위를 벗어남 (서버 확인)',
  ' (no drawable geometry)': ' (그릴 수 있는 좌표 없음)',
  'Bounds status checked server-side against server-decoded source dimensions ({width}x{height}), not the model-declared frame size.':
    '범위 여부는 모델이 선언한 프레임 크기가 아니라 서버가 해독한 원본 크기({width}x{height})를 기준으로 서버에서 확인했습니다.',
  'Bounds status checked server-side against server-decoded source dimensions for {checked} of {total} objects; the remainder fall back to client-side checks against the model-declared frame size.':
    '객체 {total}개 중 {checked}개는 서버가 해독한 원본 크기를 기준으로 서버에서 범위를 확인했고, 나머지는 모델이 선언한 프레임 크기를 기준으로 화면에서 확인했습니다.',
  'Bounds status is checked client-side against the model-declared frame size, not independently verified source dimensions.':
    '범위 여부는 독립적으로 확인된 원본 크기가 아니라 모델이 선언한 프레임 크기를 기준으로 화면에서 확인합니다.',
  'Segmentation geometry': '분할 좌표',
  '{count} object records. Boxes are model-proposed geometry in source-pixel coordinates, not verified evidence. Positions are scaled to the displayed image. Raster masks are not rendered (NOT_IMPLEMENTED).':
    '객체 기록 {count}건. 상자는 모델이 제안한 원본 픽셀 좌표이며 검증된 근거가 아닙니다. 위치는 표시된 이미지에 맞춰 조정됩니다. 래스터 마스크는 표시하지 않습니다 (NOT_IMPLEMENTED).',
  'Source image for segmentation review': '분할 검토용 원본 이미지',
  'Source SHA-256: {hash}. {note}': '원본 SHA-256: {hash}. {note}',
  'Source image unavailable for overlay.': '겹쳐 보기에 쓸 원본 이미지를 가져오지 못했습니다.',
  'Raw geometry records': '원본 좌표 기록',
  'This transcription is not eligible for confirmation.': '이 전사는 확정할 수 없습니다.',
  'Confirmation failed: {reason}': '확정하지 못했습니다: {reason}',
  'Question created: {id}': '질문을 만들었습니다: {id}',
  'Confirm transcript as question text': '전사를 질문 문구로 확정',
  'The transcript below is a proposal only. Nothing is created until you explicitly confirm. The recorder above captures microphone audio as 16-bit PCM mono WAV at 16 kHz; if it reports NOT_SUPPORTED, upload a validated WAV instead (RECORDER_FORMAT_GAP).':
    '아래 전사는 제안일 뿐입니다. 직접 확정하기 전에는 아무것도 만들어지지 않습니다. 위의 녹음기는 마이크 소리를 16 kHz 16비트 PCM 모노 WAV로 저장하며, NOT_SUPPORTED가 보고되면 검증된 WAV 파일을 대신 올리세요 (RECORDER_FORMAT_GAP).',
  'Proposed question text (editable)': '제안된 질문 문구 (수정 가능)',
  'Use verbatim transcript': '전사한 그대로 사용',
  'Confirm as question text': '질문 문구로 확정',
  'Local job: executed on the local path. Quality measured, not benchmarked against API models.':
    '로컬 작업: 로컬 경로에서 실행했습니다. 품질은 측정했을 뿐 API 모델과 비교 평가하지 않았습니다.',
  'Direct single-call baseline': '단일 호출 기본 실행',
  'Research harness': '연구 하네스',
  'No evidence linked': '연결된 근거 없음',
  'Evidence IDs and variable bindings passed structural checks.': '근거 ID와 변수 연결이 구조 검사를 통과했습니다.',
  'Structural validation failed: {errors}': '구조 검증에 실패했습니다: {errors}',
  'Meaning and applicability require review.': '의미와 적용 가능성은 검토가 필요합니다.',
  'Release-year applicability: NOT checked. This question has no usable release year, so no year comparison was possible. This is not a year-check pass.':
    '출시 연도 적용 여부: 확인하지 않았습니다. 이 질문에는 비교 가능한 출시 연도가 없어 연도 비교가 불가능했습니다. 연도 확인을 통과한 것이 아닙니다.',
  'Interview transcript': '인터뷰 전사',
  'Source audio unavailable.': '원본 오디오를 가져오지 못했습니다.',
  '{start} to {end} ms / speaker {speaker}: {text}': '{start}–{end} ms / 화자 {speaker}: {text}',
  'Turn timestamps and speaker labels are model outputs. Speaker identity is unverified. Source SHA-256: {hash}':
    '발화 시각과 화자 표시는 모델 출력입니다. 화자가 누구인지는 확인되지 않았습니다. 원본 SHA-256: {hash}',
  'AI-generated concept image, not research evidence': 'AI가 만든 개념 이미지이며 연구 근거가 아닙니다',
  'AI-generated concept image. Not research evidence. SHA-256: {hash}':
    'AI가 만든 개념 이미지입니다. 연구 근거가 아닙니다. SHA-256: {hash}',
  '{code}. No automatic retry. Review charge uncertainty, then prepare a new task explicitly.':
    '{code}. 자동으로 다시 시도하지 않습니다. 비용 불확실성을 확인한 뒤 새 작업을 직접 준비하세요.',
  'Validation issues: {count}.': '검증 문제: {count}건.',
  'Charge status: charge UNKNOWN. The provider charge for this run could not be determined, so no settled dollar figure is shown. The maximum reservation ({reservation}) is retained.':
    '비용 상태: 청구 금액을 알 수 없습니다. 이번 실행의 제공자 청구액을 확정하지 못해 정산된 금액은 표시하지 않습니다. 최대 예약액({reservation})은 그대로 유지됩니다.',
  'Calculated known cost: {calculated}. Measured known cost: {measured}. Basis: calculated from published rates and returned usage{basis}{date}.':
    '계산된 확인 비용: {calculated}. 측정된 확인 비용: {measured}. 산정 기준: 공개 단가와 반환된 사용량으로 계산{basis}{date}.',
  ' (usage incomplete)': ' (사용량 불완전)',
  '; {basis}': '; {basis}',
  '; pricing date {date}': '; 단가 기준일 {date}',
  'Provider billed cost: UNKNOWN (billing status {status}; this is not a bill).':
    '제공자 청구액: 알 수 없음 (청구 상태 {status}; 청구서가 아닙니다).',
  'Charge status: settled to known charges: {cost} (unverified local value; no server receipt). Any remaining reservation is retained; charge uncertain for unknown portions.':
    '비용 상태: 확인된 비용까지 정산했습니다: {cost} (서버 영수증이 없는, 확인되지 않은 로컬 값). 남은 예약액은 유지되며, 알 수 없는 부분의 비용은 불확실합니다.',
  'Charge status: reservation retained; charge uncertain. Maximum reservation fully retained due to unknown charge.':
    '비용 상태: 예약액 유지, 비용 불확실. 청구액을 알 수 없어 최대 예약액이 전액 유지됩니다.',
  'input {input} / output {output} / total {total}': '입력 {input} / 출력 {output} / 합계 {total}',
  ' / cached {count}': ' / 캐시 {count}',
  '{stage} / model {model} / status {status}{basis} / elapsed {elapsed}{tokens} / cost {cost}':
    '{stage} / 모델 {model} / 상태 {status}{basis} / 소요 {elapsed}{tokens} / 비용 {cost}',
  ' / basis {basis}': ' / 기준 {basis}',
  ' / tokens {tokens}': ' / 토큰 {tokens}',
  '{arm}: claims {claims} / evidence IDs {evidence} / answer SHA-256 {digest} / structural {structural} / unresolved {unresolved}':
    '{arm}: 주장 {claims} / 근거 ID {evidence} / 답변 SHA-256 {digest} / 구조 {structural} / 미해결 {unresolved}',
  '{arm}: claims {claims} / evidence IDs {evidence} / structural {structural} / unresolved {unresolved}':
    '{arm}: 주장 {claims} / 근거 ID {evidence} / 구조 {structural} / 미해결 {unresolved}',
  'Result integrity: FAILED.': '결과 무결성: 실패.',
  'The stored result failed integrity verification; the cost figures below are NOT trustworthy. Treat this record as untrusted.':
    '저장된 결과가 무결성 검증을 통과하지 못했습니다. 아래 비용 수치는 신뢰할 수 없습니다. 이 기록을 신뢰할 수 없는 것으로 다루세요.',
  'Result integrity: verified. The stored result digest matches the persisted record (unsigned SHA-256, not a MAC: it detects post-completion mutation only).':
    '결과 무결성: 확인됨. 저장된 결과의 다이제스트가 보관된 기록과 일치합니다 (서명 없는 SHA-256이며 MAC이 아니므로 완료 후 변경만 감지합니다).',
  'Result integrity: NOT verified. No verified result digest was reported for this row, so integrity is unverified, not passed.':
    '결과 무결성: 확인되지 않음. 이 항목에는 검증된 결과 다이제스트가 보고되지 않아 무결성은 통과가 아니라 미확인입니다.',
  'integrity failed': '무결성 실패',
  'The stored result failed integrity verification. These figures and outputs are NOT trustworthy.':
    '저장된 결과가 무결성 검증을 통과하지 못했습니다. 이 수치와 출력은 신뢰할 수 없습니다.',
  'Error: {code} / validation issues {count}': '오류: {code} / 검증 문제 {count}건',
  'Charge settlement: charge UNKNOWN — no settled dollar figure is shown; the reservation is retained.':
    '비용 정산: 청구액 알 수 없음 — 정산된 금액은 표시하지 않으며 예약액은 유지됩니다.',
  'Charge settlement: settled to known charges {cost} (unverified local value; no server receipt). Reservation retained for unknown portions.':
    '비용 정산: 확인된 비용까지 정산했습니다 {cost} (서버 영수증이 없는, 확인되지 않은 로컬 값). 알 수 없는 부분의 예약액은 유지됩니다.',
  'Charge settlement: {status}. Reservation retained for unknown portions.':
    '비용 정산: {status}. 알 수 없는 부분의 예약액은 유지됩니다.',
  'Charge settlement: reservation retained; charge uncertain.': '비용 정산: 예약액 유지, 비용 불확실.',
  'Receipt: {version} / outcome {outcome}': '영수증: {version} / 결과 {outcome}',
  'Trust Receipt': '신뢰 영수증',
  'Status: {status}': '상태: {status}',
  'Model: {model}': '모델: {model}',
  'Maximum reservation: {amount}': '최대 예약액: {amount}',
  'Calculated known cost: {amount}': '계산된 확인 비용: {amount}',
  'Measured known cost: {amount}': '측정된 확인 비용: {amount}',
  'Provider billed cost: UNKNOWN (not a bill)': '제공자 청구액: 알 수 없음 (청구서가 아님)',
  'Billing status: {status}': '청구 상태: {status}',
  'Usage completeness: {state}': '사용량 완전성: {state}',
  'Outcome: {outcome}': '결과: {outcome}',
  'Execution order: {order}': '실행 순서: {order}',
  ' then ': ' 다음 ',
  'Stages:': '단계:',
  'Stages: none reported.': '단계: 보고된 단계가 없습니다.',
  'Outputs:': '출력:',
  'Server receipt preferred; fallback fields shown only where the server omits them.':
    '서버 영수증을 우선하며, 대체 항목은 서버가 값을 주지 않을 때만 표시합니다.',
  'Stored review (saved result, {count} saved)': '저장된 검토 (저장된 결과, {count}건)',
  'Saved human assessment of the recorded output above. Read-only: viewing this never runs a new review or a new inference.':
    '위에 기록된 출력에 대해 사람이 남긴 평가입니다. 읽기 전용이며, 이것을 보는 것만으로 새 검토나 새 추론이 실행되지는 않습니다.',
  'Review {id} / reviewer {reviewer} / rubric {rubric} / gold case {gold}':
    '검토 {id} / 검토자 {reviewer} / 평가 기준 {rubric} / 정답 사례 {gold}',
  'Saved report payload': '저장된 보고서 내용',
  ' / accounted cost: {amount}': ' / 집계된 비용: {amount}',
  'Usage and reproducible record': '사용량 및 재현 기록'
});
