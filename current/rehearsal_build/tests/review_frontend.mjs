// Actual frontend functions in a VM with a small DOM double. No browser or network.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const nodes=new Map();
const node=id=>{
  if(!nodes.has(id)) nodes.set(id,{value:'',textContent:'',disabled:false,classList:{add(){},remove(){}},setAttribute(){}});
  return nodes.get(id);
};
let serial=0;
const context=vm.createContext({
  URLSearchParams, location:{search:''}, crypto:{randomUUID:()=>`click-${++serial}`},
  // app.js publishes a global for the start screen's picker, as it does in the page.
  window:{},
  document:{getElementById:node,querySelectorAll:()=>[]},
});
let source=await readFile(new URL('../frontend/app.js',import.meta.url),'utf8');
// Skip startup loading; all tested application functions are used unchanged.
source=source.slice(0,source.lastIndexOf('(async()=> {'));
vm.runInContext(source,context);
vm.runInContext(`
  S.project='p'; S.question='q';
  controls=()=>{}; loadPending=async()=>{}; loadBriefing=async()=>{};
  globalThis.sent=[]; globalThis.failNext=false;
  call=async(method,path,body)=>{
    sent.push({path,body});
    if(failNext) { failNext=false; throw Error('Lost response'); }
    return {verdict:body.verdict,reason_display:body.reason,state:'deferred_pending'};
  };
`,context);
node('reason').value='Reason A';
await vm.runInContext(`decide('adopt')`,context);
node('reason').value='Reason B';
await vm.runInContext(`decide('reject')`,context);
node('reason').value='Reason A';
await vm.runInContext(`decide('adopt')`,context);
let sent=JSON.parse(vm.runInContext('JSON.stringify(sent)',context));
assert.equal(new Set(sent.map(s=>s.body.client_action_id)).size,3,'each new decision needs a new action ID');
node('reason').value='Corrected reason';
await vm.runInContext(`decide('adopt')`,context);
sent=JSON.parse(vm.runInContext('JSON.stringify(sent)',context));
assert.equal(sent.at(-1).body.reason,'Corrected reason');
assert.notEqual(sent.at(-1).body.client_action_id,sent.at(-2).body.client_action_id);

await vm.runInContext(`failNext=true; decide('defer')`,context);
await vm.runInContext(`decide('defer')`,context);
sent=JSON.parse(vm.runInContext('JSON.stringify(sent)',context));
assert.equal(sent.at(-1).body.client_action_id,sent.at(-2).body.client_action_id,'a retry must replay the same decision action');

await vm.runInContext(`S.cq='cq'; failNext=true; respond('unknown')`,context);
await vm.runInContext(`respond('unknown')`,context);
sent=JSON.parse(vm.runInContext('JSON.stringify(sent)',context));
assert.equal(sent.at(-1).body.client_action_id,sent.at(-2).body.client_action_id,'a retry must replay the accepted response');
assert.equal(sent.at(-1).body.kind,'unknown');
assert.equal(sent.at(-1).body.value,null);
console.log('REVIEW_FRONTEND_PASS: new decision IDs, reason correction, decision retry and clarification retry reuse');

// A selector changes the next-question scope and performs no mutation/model request.
await vm.runInContext(`S.busy=false; S.question='existing'; S.pack='social_science'; sent=[];`,context);
node('pack-select').value='engineering_measurement';
await vm.runInContext('changePack()',context);
assert.equal(vm.runInContext('S.pack',context),'engineering_measurement');
assert.equal(vm.runInContext('S.question',context),null);
assert.equal(vm.runInContext('sent.length',context),0);
await vm.runInContext('S.busy=true',context);
node('pack-select').value='social_science';
await vm.runInContext('changePack()',context);
assert.equal(vm.runInContext('S.pack',context),'engineering_measurement');
console.log('PACK_FRONTEND_PASS: selection changes future scope, no mutation, busy guard');
