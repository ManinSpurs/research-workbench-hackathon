import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value:'',textContent:'',innerHTML:'',classList:{add(){},remove(){}},addEventListener(){}});
  return nodes.get(id);
};
const context = vm.createContext({
  $:node, S:{}, esc:s=>String(s??''), document:{querySelectorAll:()=>[]},
});
vm.runInContext(await readFile(new URL('../frontend/ai.js',import.meta.url),'utf8'),context);
vm.runInContext(`renderAIJobs([{id:'prior',status:'completed',reservation_usd:0.05,result:{outputs:{basic:{result:{answer:'Answer',claims:[]},grounding:{structural_valid:true,errors:[]}}}}}])`,context);
assert.ok(!/Wonder|requestSecondLook|wb-secondlook/.test(node('ai-results').innerHTML));
assert.equal(vm.runInContext('typeof requestSecondLook',context),'undefined');
console.log('SECOND_LOOK_UI_PASS: completed results have no second-look entry point');
