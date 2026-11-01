// App-owned dialog behavior, with no prompt support and no network.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {
    value:'',textContent:'',innerHTML:'',open:false,isConnected:true,
    focus(){this.focused=true;},select(){this.selected=true;},
    showModal(){this.open=true;},close(){this.open=false;},
    setAttribute(){},removeAttribute(){},appendChild(){},addEventListener(){},replaceChildren(){}
  });
  return nodes.get(id);
};
const origin=node('add-button');
let sent=[], error;
const ctx=vm.createContext({
  console,setTimeout,clearTimeout,$:node,S:{project:'p'},
  prompt(){throw Error('Native prompt must never be called');},
  confirm:()=>true,showError:m=>{error=m;},
  document:{activeElement:origin,querySelectorAll:()=>[],querySelector:()=>null,createElement:()=>({})},
  call:async(method,path,body)=>{sent.push(body);return {...vm.runInContext('latexState.draft',ctx),files:body.files,revision:body.expected_revision+1};}
});
vm.runInContext(await readFile(new URL('../frontend/latex.js',import.meta.url),'utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
run(`latexState={project:'p',serial:0,draft:{draft_id:'d',main_file:'main.tex',revision:0,
files:[{path:'main.tex',text:'keep main'},{path:'notes.tex',text:'keep notes'}]}};renderDraft();`);
const submit=()=>run('latexSubmitFileDialog({preventDefault(){}})');
run('latexFileAction("add")');
assert.equal(node('latex-file-dialog').open,true);
assert.equal(node('latex-file-path').focused,true);
assert.equal(run('latexState.draft.files.length'),2,'opening is not mutation');
for (const bad of ['../escape.tex','/root.tex','main.tex','bad.txt','']) {
  node('latex-file-path').value=bad; submit();
  assert.equal(node('latex-file-dialog').open,true);
  assert.ok(node('latex-file-error').textContent);
  assert.equal(run('latexState.draft.files.length'),2);
}
node('latex-file-path').value='references.bib'; submit();
assert.equal(node('latex-file-dialog').open,false);
assert.equal(origin.focused,true,'focus restored after confirmation');
assert.equal(run('latexState.activeFile'),'references.bib');
assert.equal(run('latexState.dirty'),true);
assert.equal(run('latexState.draft.files[0].text'),'keep main');
assert.equal(run('latexState.draft.files[1].text'),'keep notes');
node('latex-editor').value='real bibliography text';
run('scheduleLatexAutosave();latexFileAction("rename")');
assert.equal(node('latex-file-path').value,'references.bib');
assert.equal(node('latex-file-path').selected,true);
node('latex-file-path').value='sources.bib';submit();
assert.equal(run('latexState.draft.files[2].path'),'sources.bib');
assert.equal(run('latexState.draft.files[2].text'),'real bibliography text');
run('latexFileAction("add");latexCloseFileDialog()');
assert.equal(run('latexState.draft.files.length'),3,'cancel adds nothing');
assert.equal(node('latex-file-dialog').open,false);
run('latexSelectFile("main.tex");latexFileAction("rename")');
assert.match(error,/main file/);
assert.equal(node('latex-file-dialog').open,false);
await new Promise(r=>setTimeout(r,900));
assert.equal(sent.length,1);
assert.equal(sent[0].files.length,3);
run('latexFileAction("add");latexUnmount()');
assert.equal(node('latex-file-dialog').open,false,'context teardown dismisses modal');
const html=await readFile(new URL('../frontend/index.html',import.meta.url),'utf8');
assert.match(html,/<dialog id="latex-file-dialog"[^>]*oncancel=/);
assert.match(html,/onsubmit="latexSubmitFileDialog\(event\)"/);
assert.match(html,/id="latex-file-cancel"/);
console.log('PASS app-owned add/rename dialog, validation, cancel/focus, preservation, main guard, autosave, teardown; no native prompt');
