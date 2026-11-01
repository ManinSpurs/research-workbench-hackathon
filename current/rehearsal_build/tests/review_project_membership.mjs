// Existing project/create/upload functions, mocked transport only. No database.
import vm from 'node:vm';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const source=await fs.readFile(new URL('../frontend/app.js',import.meta.url),'utf8');
const functions=source.slice(source.indexOf('async function loadProjects()'),source.indexOf('async function loadProfile()'));
class Node {
  constructor(){this.dataset={};this.value='';this.files=[];this.attributes={};this.className='';
    this.classList={_set:new Set(),add(c){this._set.add(c);},remove(c){this._set.delete(c);},contains(c){return this._set.has(c);}};}
  replaceChildren(...nodes){this.children=nodes;nodes.forEach(n=>{n.parentNode=this;});}
  append(...nodes){this.children=(this.children||[]).concat(nodes);nodes.forEach(n=>{n.parentNode=this;});}
  insertBefore(node,ref){const kids=this.children||[];const at=ref?kids.indexOf(ref):-1;
    if(at<0) kids.push(node); else kids.splice(at,0,node);
    this.children=kids;node.parentNode=this;return node;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  removeAttribute(k){delete this.attributes[k];}
  getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attributes,k)?this.attributes[k]:null;}
  focus(){this.focused=true;}
  reset(){}
}
const nodes=new Map(), $=id=>{if(!nodes.has(id)) nodes.set(id,new Node());return nodes.get(id);};
const S={epoch:0,pack:'sample',project:null,pending:[],busy:false};
let rows=[{id:'legacy',name:'Legacy',pack_id:null},{id:'foreign',name:'Foreign',pack_id:'other'}], sourceType='builtin', error='', incompatible=false;
const writes=[];
const context={S,$,window:{},document:{createElement:()=>new Node()},t:x=>x,
  current:e=>S.epoch===e,clearActive(){},showError:x=>{error=x;},controls(){},
  localStorage:{setItem(){}},loadBriefing:async()=>{},loadPending:async()=>{},
  loadProfile:async()=>{},action:async work=>work(S.epoch),
  call:async(method,url,body)=>{
    if(method==='GET'&&url==='/projects') return {projects:rows};
    if(method==='GET'&&url.startsWith('/chips/')) return {source:sourceType};
    writes.push({url,body});
    if(url.endsWith('/chip')){
      if(incompatible) throw Error('incompatible pins');
      rows.find(p=>url.includes(p.id)).pack_id=body.pack_id;
      return {};
    }
    const id=url==='/projects/upload'?'upload':'created';
    rows.push({id,name:id,pack_id:null});
    return {project_id:id,name:id};
  }};
vm.createContext(context);vm.runInContext(functions,context);
await context.openProject('legacy','Legacy');assert.equal(S.project,'legacy');
assert.equal(writes.length,0,'opening legacy does not migrate membership');
await context.registerProject('fx');assert.equal(S.project,'created');
$('upload-name').value='Uploaded';
$('upload-csv').files=[{size:8,text:async()=> 'x\n1\n'}];
$('upload-dictionary').files=[{text:async()=>JSON.stringify({entries:[{source_id:'s',evidence_id:'e',field:'x',definition:'x',location:'x'}]})}];
await context.uploadProject({preventDefault(){}});assert.equal(S.project,'upload');
sourceType='installed';S.pack='topic';
await context.openProject('legacy','Legacy');assert.equal(S.project,'upload');assert.match(error,/not linked/);
await context.openProject('foreign','Foreign');assert.equal(S.project,'upload');
incompatible=true;
await context.attachProjectToChip('legacy','Legacy');
assert.match(error,/incompatible pins/);assert.equal(rows[0].pack_id,null);
incompatible=false;
await context.attachProjectToChip('legacy','Legacy');
assert.equal(rows[0].pack_id,'topic');assert.equal(S.project,'legacy');
console.log('PASS: legacy open/create/upload, custom foreign refusal, explicit attachment and incompatible refusal');
