import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../frontend/app.js', import.meta.url), 'utf8');
const nodes = new Map();
const calls = [];
let finish;
const ctx = vm.createContext({
  window: {}, URLSearchParams, location:{search:''},
  localStorage:{getItem:()=>null}, crypto:{randomUUID:()=> 'action-1'},
  document:{getElementById:id => {
    if (!nodes.has(id)) nodes.set(id,{innerHTML:'',scrollIntoView(){}});
    return nodes.get(id);
  }},
  fetch: async (url, options) => {
    calls.push(url);
    if (options.method === 'POST') await new Promise(resolve=> {finish=resolve;});
    return {ok:true,json:async()=>({jobs:[],cards:[]})};
  }
});
vm.runInContext(source.slice(0,source.lastIndexOf('(async()=> {')),ctx);
vm.runInContext("S.project='p'; S.question='q'; S.prepScope={scope_id:'p',text:'sources A'}; prepAsk('question','q','q','pending'); S.prepScope={scope_id:'p',text:'sources B'}",ctx);
await vm.runInContext('prepConfirm()',ctx);
assert.equal(calls.length,0,'stale scope cannot confirm');
vm.runInContext("prepAsk('question','q','q','pending')",ctx);
const first = vm.runInContext('prepConfirm()',ctx);
await vm.runInContext('prepConfirm()',ctx);
assert.equal(calls.length,1,'multi-click submits once');
finish();
await first;
assert.ok(nodes.get('pending').innerHTML.includes('not completed'));
console.log('PREPARATION_LIFECYCLE_PASS');
const pending = [];
let scheduled = 0;
ctx.setTimeout = () => ++scheduled;
ctx.clearTimeout = () => {};
ctx.fetch = () => new Promise((resolve,reject)=>pending.push({resolve,reject}));
const payload = status => ({ok:true,json:async()=>({
  jobs:[{id:'job-1',status}],cards:[],scope:{scope_id:'p',summary:status}
})});
const older = vm.runInContext('loadPreparations()',ctx);
const newer = vm.runInContext('loadPreparations()',ctx);
pending[1].resolve(payload('completed'));
await newer;
pending[0].resolve(payload('queued'));
await older;
assert.equal(vm.runInContext('S.preparations.jobs[0].status',ctx),'completed');
const oldFailure = vm.runInContext('loadPreparations()',ctx);
const latest = vm.runInContext('loadPreparations()',ctx);
pending[3].resolve(payload('completed'));
await latest;
pending[2].reject(new Error('Failed to fetch'));
await oldFailure;
assert.equal(vm.runInContext('S.preparations.stale',ctx),undefined);
for (let i=0;i<3;i++) {
  const request = vm.runInContext('loadPreparations()',ctx);
  pending[4+i].reject(new Error('Failed to fetch'));
  await request;
}
assert.equal(vm.runInContext('S.preparations.jobs[0].status',ctx),'completed');
assert.equal(vm.runInContext('S.prepScope.text',ctx),'completed');
assert.ok(nodes.get('briefing').innerHTML.includes('Preparation status is stale'));
assert.equal(scheduled,2,'only two automatic retry timers after three consecutive failures');
console.log('PREPARATION_STATUS_STALE_SERIAL_PASS');
