// Renderer lifecycle regression using mocked DOM, transport and native saves.
import vm from 'node:vm';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const source = await fs.readFile(new URL('../frontend/home_hub.js', import.meta.url), 'utf8');
class Node {
  constructor() { this.children=[]; this.hidden=true; this.value=''; this.listeners={}; this.dataset={}; }
  appendChild(n) { this.children.push(n); return n; }
  before() {}
  after() {}
  setAttribute() {}
  addEventListener(k,f) { this.listeners[k]=f; }
  querySelector(key) { this.queries ||= new Map(); if(!this.queries.has(key)) this.queries.set(key,new Node()); return this.queries.get(key); }
  querySelectorAll() { return []; }
  replaceChildren(...children) { this.children=children; }
}
const nodes = new Map();
const get = id => { if(!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id); };
const state = {project:'previous',pack:'old',epoch:1,busy:false,pending:[]};
let saved = null, saveError = false, exportError = false, ejects = 0, opened, message, release, transitionError = false, installError = false, installPayload, builtinResponse = false;
const storage = new Map([['wb.pack','old'],['wb.project','previous']]);
const calls=[];
const context = {
  S:state, console, Option:class {}, crypto:globalThis.crypto,
  document:{getElementById:get,createElement:()=>new Node(),createComment:()=>new Node(),body:new Node(),querySelectorAll:()=>[]},
  MutationObserver:class { observe() {} },
  setTimeout:()=>0,clearTimeout:()=>{},
  localStorage:{removeItem:k=>storage.delete(k),setItem:(k,v)=>storage.set(k,v)},
  esc:String,serverText:String,errorText:String,clearActive(){},showError(x){message=x;},
  syncComposerMode(){},
  loadDomainPacks:async()=>{},loadProjects:async()=>{calls.push('loadProjects');},
  changePack:async()=>{state.pack=get('pack-select').value;state.epoch++;},
  openProject:async(id,name)=>{calls.push('openProject');state.project=id;opened=id;},
  call:async(method,url,body)=>{
    calls.push(url);
    if(url==='/chips') return {chips:[]};
    if(url==='/chips/install') throw Error('Opening must not use the replacement installer');
    if(url==='/chips/open') {
      installPayload=body;
      if(installError) throw Error('topic version 1.0.0 is already installed with different bytes');
      if(release) await new Promise(resolve=>{ release.resolve=resolve; });
      return builtinResponse ? {pack_id:'sample',source:'builtin'} : {pack_id:'topic',source:'file'};
    }
    if(url.endsWith('/workspace')) return {project_id:'own',name:'Topic',pack_id:'topic'};
    if(url.endsWith('/projects')) return {projects:[]};
    if(url.startsWith('/chips/export/')) { if(exportError) throw Error('export refused'); return {base64:'UEs=',filename:'topic.chip'}; }
    return {source:'installed'};
  },
};
context.window={
  WB_DESKTOP:{saveChip:async()=>{if(saveError) throw Error('disk refused'); return saved;}},
  WB_STARTUP:{openPack:async()=>{if(transitionError) throw Error('locked transition');},eject:async()=>{ejects++;}},
};
vm.runInNewContext(source,context);
const save = get('home-composer-slot').children.find(n=>n.textContent==='Save / Eject Chip…');
const dialog = context.document.body.children.find(n=>n.className==='new-chip-dialog');
get('new-chip-name').value='   ';
await dialog.querySelector('form').onsubmit({preventDefault(){}});
assert.equal(context.window.WB_CHIP_BUSY(),false,'blank name never takes operation lock');
transitionError=true;
await context.window.WB_CHIP_HOOK('',{base64:'UEs='});
assert.equal(state.project,'previous');
assert.equal(state.pack,'old');
assert.equal(storage.get('wb.pack'),'old');
assert.equal(storage.get('wb.project'),'previous');
assert.equal(context.window.WB_CHIP_BUSY(),false);
transitionError=false;
installError=true;
const refused=await context.window.WB_CHIP_HOOK('',{base64:'UEs=',filename:'urban_policy.chip'});
assert.equal(refused,false);
assert.equal(installPayload.filename,'urban_policy.chip','chosen native basename is passed unchanged');
assert.match(message,/different bytes/,'backend differing-file refusal is visible, not a generic HTTP status');
assert.equal(state.project,'previous');assert.equal(state.pack,'old');
assert.equal(context.window.WB_CHIP_BUSY(),false);
installError=false;
await save.onclick();
assert.equal(ejects,0); assert.equal(state.project,'previous','cancel preserves selection');
saveError=true; await save.onclick();
assert.equal(ejects,0); assert.match(message,/disk refused/);
saveError=false; exportError=true; await save.onclick();
assert.equal(ejects,0); assert.match(message,/export refused/);
exportError=false;
await context.window.WB_CHIP_HOOK('',{base64:'UEs='});
assert.equal(opened,'own'); assert.equal(state.pack,'topic');
assert.ok(calls.indexOf('loadProjects')<calls.indexOf('openProject'),'refresh list before opening workspace');
saved={filename:'topic.chip'}; await save.onclick();
assert.equal(ejects,1); assert.equal(state.project,null);
assert.equal(state.pack,''); assert.equal(storage.has('wb.pack'),false);
// Backend archive equality is tested by Lead. This exercises the UI consuming
// an accepted exact-reopen response, without implementing archive logic here.
const reopened=await context.window.WB_CHIP_HOOK('/saved/urban_policy.chip',{path:'/saved/urban_policy.chip',base64:'UEs=',filename:'urban_policy.chip'});
assert.equal(reopened,true); assert.equal(state.project,'own');assert.equal(state.pack,'topic');
assert.equal(installPayload.base64,'UEs=');assert.equal(installPayload.filename,'urban_policy.chip');
const priorProject=state.project,priorEpoch=state.epoch;
installError=true;
assert.equal(await context.window.WB_CHIP_HOOK('',{base64:'ZGlmZmVyZW50',filename:'urban_policy.chip'}),false);
assert.equal(state.project,priorProject);assert.equal(state.epoch,priorEpoch);assert.equal(state.pack,'topic');
assert.equal(context.window.WB_CHIP_BUSY(),false);
installError=false;
builtinResponse=true;
const beforeBuiltin=calls.length;
assert.equal(await context.window.WB_CHIP_HOOK('/untrusted/sample.chip',{path:'/untrusted/sample.chip'}),true);
assert.equal(installPayload.path,'/untrusted/sample.chip');
assert.ok(calls.slice(beforeBuiltin).includes('/chips/open'));
assert.ok(!calls.slice(beforeBuiltin).some(url=>url.endsWith('/workspace')),'only server-validated builtin source skips writable workspace');
builtinResponse=false;
state.project='prior'; state.pack='old'; release={};
const pending=context.window.WB_CHIP_HOOK('',{base64:'UEs='});
await new Promise(resolve=>setImmediate(resolve));
state.epoch++; release.resolve(); await pending;
assert.equal(state.project,'prior','late install does not select its workspace');
console.log('PASS: cancel, export/save refusal, successful eject, exact-reopen response, differing-file refusal, validated builtin, workspace selection, late install');
