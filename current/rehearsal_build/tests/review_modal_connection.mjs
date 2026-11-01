import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const nodes = new Map();
const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {textContent:'',innerHTML:'',value:'',checked:false,disabled:true,addEventListener(){}});
  return nodes.get(id);
};
const context = vm.createContext({
  $, console, S:{question:'q',epoch:1}, current:()=>true,
  esc:s=>String(s), showError:s=>s, action:fn=>fn(1),
  call:async(method,path)=>{
    if(path==='/ai/registry') return registry;
    if(path.endsWith('/assets'))return {assets:[]};
    if(path.endsWith('/jobs'))return {jobs:[]};
    throw Error('Unexpected HTTP: '+method+' '+path);
  }
});
let registry;
vm.runInContext(fs.readFileSync(new URL('../frontend/ai.js',import.meta.url),'utf8'),context);
const run = code => vm.runInContext(code,context);
assert.equal(run("modelRoute('meta-models/Muse-Glimmer-30B')"),'modal');
assert.equal(run("modelRoute('meta-models/muse-spark-open')"),'baseten');
assert.equal(run("modelRoute('muse-wonder-1.0')"),'meta');
context.reg={};
run('renderModalStatus(reg)');
assert.match($('ai-modal-status').textContent,/not deployed\/configured/);
context.reg={settings:{modal_connection:{endpoint_configured:true,base_url:'https://public.example/v1',execution_enabled:true,proxy_secret:'NEVER_SHOW'}}};
run('renderModalStatus(reg)');
assert.match($('ai-modal-status').textContent,/https:\/\/public.example\/v1/);
assert.doesNotMatch($('ai-modal-status').textContent,/NEVER_SHOW/);
assert.equal($('ai-send').disabled,true);
context.reg={providers:[{id:'modal',endpoint_configured:true,base_url:'https://registry.example/v1',proxy_auth_configured:true,proxy_secret:'NEVER_SHOW'}]};
run('renderModalStatus(reg)');
assert.match($('ai-modal-status').textContent,/https:\/\/registry.example\/v1/);
assert.doesNotMatch($('ai-modal-status').textContent,/NEVER_SHOW/);
registry={settings:{cloud_enabled:false,budget_usd:1,local_allowance_remaining_usd:1},providers:[{id:'meta'},{id:'baseten'},{id:'modal'}],models:[{id:'meta-models/Muse-Glimmer-30B',endpoint:'/never'},{id:'muse-wonder-1.0',endpoint:'/existing'}]};
await run("loadAI('q')");
assert.doesNotMatch($('ai-model').innerHTML,/Glimmer/);
assert.doesNotMatch($('ai-model').innerHTML,/muse-wonder/);
$('ai-model').value='meta-models/Muse-Glimmer-30B';
assert.match(await run('verifyAIAccount()'),/disabled/);
assert.match(await run('prepareAI()'),/disabled/);
assert.match(await run('executeAI()'),/disabled/);
run("S.project='p'; aiPreview={provider:'modal',model:'meta-models/Muse-Glimmer-30B'}");
$('ai-model').value='auto';
$('ai-consent').checked=true;
run('updateAIControls()');
assert.equal($('ai-send').disabled,true);
const html=fs.readFileSync(new URL('../frontend/index.html',import.meta.url),'utf8');
assert.doesNotMatch(html,/id="ai-key-modal"/);
assert.match(html,/SDK login does not establish inference readiness/);
assert.match(html,/GPU time, not tokens/);
console.log('Modal connection readiness: PASS (mocked, no network)');
