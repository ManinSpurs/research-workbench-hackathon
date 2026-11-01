// Actual source helpers with DOM/transport doubles only.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {
    value:'',textContent:'',innerHTML:'',selectionStart:0,selectionEnd:0,children:[],
    setAttribute(){},removeAttribute(){},appendChild(n){this.children.push(n);},
    replaceChildren(){},addEventListener(){},focus(){this.focused=true;},
    setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;},
    setRangeText(text,a,b){this.value=this.value.slice(0,a)+text+this.value.slice(b);}
  });
  return nodes.get(id);
};
let sent=[], failure;
const ctx=vm.createContext({
  console,setTimeout,clearTimeout,S:{project:'p'},$:node,window:{},
  document:{querySelectorAll:()=>[],createElement:()=>({}),querySelector:()=>null,getElementById:node},
  showError:m=>{failure=m;},URL:{revokeObjectURL(){}},
  call:async (method,path,body)=>{
    sent.push({method,path,body});
    return {...vm.runInContext('latexState.draft',ctx),files:body.files,revision:body.expected_revision+1};
  }
});
vm.runInContext(await readFile(new URL('../frontend/i18n.js',import.meta.url),'utf8'),ctx);
vm.runInContext(await readFile(new URL('../frontend/latex.js',import.meta.url),'utf8'),ctx);
vm.runInContext(`latexState={draft:{draft_id:'d',main_file:'main.tex',revision:0,
files:[{path:'main.tex',text:'Before words after'},{path:'refs.bib',text:
'% @article{fakeComment,}\\n@comment{ @article{fakeNested, title={No}} }\\n@article{Real2026, title={Actual entry}}\\n@book(OtherKey, title="Book")'}]},
activeFile:'main.tex',serial:0}; renderDraft();`,ctx);
const editor=node('latex-editor');
const run=code=>vm.runInContext(code,ctx);
const originalBib=run('latexState.draft.files[1].text');
editor.setSelectionRange(7,12);
run('latexInsertSyntax("bold")');
assert.equal(editor.value,'Before \\textbf{words} after');
assert.equal(editor.value.slice(editor.selectionStart,editor.selectionEnd),'words');
assert.equal(run('latexState.dirty'),true);
assert.equal(run('latexState.draft.files[1].text'),originalBib);
run('latexUndoSyntax()');
assert.equal(editor.value,'Before words after');
editor.setSelectionRange(0,0);
run('latexInsertSyntax("heading")');
assert.equal(editor.value,'\\section{Heading}Before words after');
assert.equal(editor.value.slice(editor.selectionStart,editor.selectionEnd),'Heading');
assert.equal(editor.focused,true);
run('latexUndoSyntax()');
assert.deepEqual(Array.from(run('latexBibliographyKeys()')),['OtherKey','Real2026']);
node('latex-citation-key').value='Invented';
const unchanged=editor.value;
run('latexInsertSyntax("citation")');
assert.equal(editor.value,unchanged);
node('latex-citation-key').value='Real2026';
editor.setSelectionRange(7,12);
run('latexInsertSyntax("citation")');
assert.equal(editor.value,'Before words \\cite{Real2026} after');
run('latexUndoSyntax(); latexSelectFile("refs.bib"); latexInsertSyntax("heading")');
assert.equal(editor.value,originalBib,'no TeX commands in bibliography');
run('latexSelectFile("main.tex")');
editor.setSelectionRange(editor.value.length,editor.value.length);
run('latexInsertSyntax("table")');
assert.ok(editor.value.includes('\\begin{tabular}{ll}\nColumn 1 & Column 2 \\\\\nValue 1 & Value 2 \\\\\n\\end{tabular}'));
editor.value+=' later edit';
run('scheduleLatexAutosave(); latexUndoSyntax()');
assert.match(failure,/No text was removed/);
assert.ok(editor.value.endsWith(' later edit'));
await new Promise(r=>setTimeout(r,900));
assert.equal(sent.length,1,'one debounced autosave');
assert.equal(sent[0].method,'PUT');
assert.equal(sent[0].body.source,'autosave');
assert.equal(sent[0].body.files[1].text,originalBib);
assert.equal(sent[0].body.files[0].text,editor.value);
assert.equal(run('latexState.dirty'),false);
const englishPaper=editor.value;
run('window.WB_I18N.set("ko")');
assert.equal(run('window.WB_I18N.t("Heading")'),'제목','real UI language is Korean');
assert.equal(editor.value,englishPaper,'UI language change does not translate the paper');
assert.equal(run('latexState.draft.files[0].text'),englishPaper);
const expectedPlaceholders={
  heading:'Heading',subheading:'Subheading',bold:'Text',italic:'Text',
  bullets:'First item',numbered:'First item',inline:'x = y',display:'x = y',table:'Column 1 & Column 2'
};
for(const [kind,placeholder] of Object.entries(expectedPlaceholders)) {
  editor.setSelectionRange(editor.value.length,editor.value.length);
  run(`latexInsertSyntax(${JSON.stringify(kind)})`);
  assert.equal(editor.value.slice(editor.selectionStart,editor.selectionEnd),placeholder,`Korean UI keeps ${kind} source placeholder neutral`);
  assert.ok(/^[\x00-\x7f]*$/.test(editor.value),'no incompatible translated text injected');
  run('latexUndoSyntax()');
  assert.equal(editor.value,englishPaper,'undo restores original English manuscript');
}
editor.value='Original 연구 source';
run('scheduleLatexAutosave()');
editor.setSelectionRange(9,11);
run('latexInsertSyntax("bold")');
assert.equal(editor.value,'Original \\textbf{연구} source','user-authored text stays verbatim');
run('latexUndoSyntax(); window.WB_I18N.set("en")');
assert.equal(editor.value,'Original 연구 source','language switch never translates user text');
run('latexUnmount()');
console.log('PASS syntax selection/caret/undo, bibliography key parsing and guard, preservation, dirty/autosave, real Korean UI with neutral source placeholders');
