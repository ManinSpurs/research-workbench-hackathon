// Transport-only writing regression: no server, database, engine or network.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value:'', textContent:'', innerHTML:'', listeners:{},
    classList:{add(){},remove(){}}, setAttribute(){},removeAttribute(){},
    addEventListener(k,f){this.listeners[k]=f;}, appendChild(){},replaceChildren(){}, focus(){}});
  return nodes.get(id);
};
let revokes = 0, puts = [], release;
const ctx = vm.createContext({
  console, setTimeout,clearTimeout,Blob,encodeURIComponent,
  S:{project:'p'}, $:node, confirm:()=>true, prompt:()=> 'notes.tex',
  showError:m=>{ctx.error=m;},
  URL:{createObjectURL:()=> 'blob:real',revokeObjectURL:()=>revokes++},
  document:{querySelectorAll:()=>[],querySelector:()=>null,
    createElement:()=>({...node('temporary'),click(){}})},
});
vm.runInContext(await readFile(new URL('../frontend/latex.js',import.meta.url),'utf8'),ctx);
vm.runInContext(`
latexState={project:'p',draft:{draft_id:'d',title:'Paper',main_file:'main.tex',engine:'auto',revision:0,
 files:[{path:'main.tex',text:'main'},{path:'refs.bib',text:'bib'}]},capabilities:{available:true},serial:0};
renderDraft();
`,ctx);
ctx.call=async (m,p,b)=>{
  if(m==='PUT'){puts.push(b);return new Promise(resolve=>{release=()=>resolve({...vm.runInContext('latexState.draft',ctx),files:b.files,revision:b.expected_revision+1});});}
  throw Error('unexpected route');
};
node('latex-editor').value='main edited';
vm.runInContext('scheduleLatexAutosave(); latexSelectFile("refs.bib")',ctx);
node('latex-editor').value='bib edited';
vm.runInContext('scheduleLatexAutosave()',ctx);
const saving=vm.runInContext('saveDraft("manual")',ctx);
assert.equal(puts[0].files[0].text,'main edited');
assert.equal(puts[0].files[1].text,'bib edited');
vm.runInContext('latexSelectFile("main.tex")',ctx);
node('latex-editor').value='newer main';
vm.runInContext('scheduleLatexAutosave()',ctx);
release(); await saving;
assert.equal(vm.runInContext('latexState.dirty',ctx),true);
assert.equal(vm.runInContext('latexState.draft.files[0].text',ctx),'newer main');
ctx.call=async()=>{throw Error('409 revision conflict; reload');};
await vm.runInContext('saveDraft("manual")',ctx);
assert.equal(vm.runInContext('latexState.blocked',ctx),true);
assert.match(node('latex-status').textContent,/reload/);
vm.runInContext('latexFileAction("delete")',ctx);
assert.equal(vm.runInContext('latexState.draft.files.length',ctx),2);
vm.runInContext('latexState.blocked=false; latexFileAction("rename")',ctx);
assert.match(ctx.error,/main file/);
vm.runInContext('latexState.dirty=false; latexState.capabilities.available=false',ctx);
assert.equal(await vm.runInContext('compileDraft()',ctx),null);
assert.match(node('latex-status').textContent,/no PDF was produced/);
vm.runInContext('renderPreview(new Blob(["%PDF"])); latexUnmount()',ctx);
assert.equal(revokes,1);
console.log('PASS multifile retention, in-flight edits, conflicts, main guard, absent engine, teardown');
