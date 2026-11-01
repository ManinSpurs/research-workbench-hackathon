import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import { interiorFixture } from './interior_fixture.mjs';
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value:'',innerHTML:'',textContent:'',classList:{add(){},remove(){}},setAttribute(){}});
  return nodes.get(id);
};
const c = vm.createContext({URLSearchParams,Intl,location:{search:''},window:{},crypto:{randomUUID:()=> 'test'},document:{getElementById:node,querySelectorAll:()=>[]}});
let src = await readFile(new URL('../frontend/app.js',import.meta.url),'utf8');
vm.runInContext(src.slice(0,src.lastIndexOf('(async()=> {')),c);
vm.runInContext(`S.project='p'; S.question='q'; controls=()=>{}; loadPending=async()=>{}; loadBriefing=async()=>{};
globalThis.sent=[]; call=async(method,path,body)=>{sent.push({method,path,body});return {verdict:'defer',decision_id:'exact-decision',reason_display:'Reason',conditions:[{id:'r',display_status:'due',recorded_date:'2020-01-01T00:00:00Z',note:'Own note'}]};};`,c);
await vm.runInContext(`decide('defer')`,c);
assert.match(node('revisit-create').innerHTML,/type="datetime-local" value=""/);
await vm.runInContext(`saveRevisit('new')`,c);
assert.equal(vm.runInContext('sent.length',c),1);
node('revisit-date-new').value='2030-04-05T12:30';
node('revisit-note-new').value='Own note';
await vm.runInContext(`saveRevisit('new')`,c);
const body=JSON.parse(vm.runInContext('JSON.stringify(sent.at(-1).body)',c));
assert.equal(body.subject_id,'exact-decision');
assert.equal(body.recorded_date,new Date('2030-04-05T12:30').toISOString());
assert.equal(body.recorded_by,'researcher');
assert.equal(body.source,'researcher_revisit_form');
assert.match(vm.runInContext('sent.at(-1).path',c),/\?pack_id=social_science$/);
await vm.runInContext('loadRevisits()',c);
assert.match(node('revisit-management').innerHTML,/Ready to revisit/);
node('revisit-date-row-r').value='2031-05-06T11:00';
await vm.runInContext(`saveRevisit('row-r')`,c);
assert.equal(vm.runInContext('sent.at(-1).body.kind',c),'defer');
await vm.runInContext(`finishRevisit('row-r')`,c);
assert.equal(vm.runInContext('sent.at(-1).body.kind',c),'done');
assert.match(vm.runInContext('sent.at(-1).path',c),/\/r\/actions\?pack_id=social_science$/);
assert.equal(vm.runInContext('sent.filter(x=>x.path.endsWith("/decisions")).length',c),1);
vm.runInContext(`revisitRows=[{id:'future',display_status:'recorded'}]; renderRevisits();`,c);
assert.match(node('revisit-management').innerHTML,/Reschedule/);
assert.doesNotMatch(node('revisit-management').innerHTML,/finishRevisit/);
assert.match(node('revisit-management').innerHTML,/type="datetime-local" value=""/);
node('revisit-date-row-future').value='2032-06-07T10:00';
vm.runInContext(`call=(method,path,body)=>{sent.push({method,path,body});return new Promise((resolve,reject)=>{globalThis.resolveSave=resolve;globalThis.rejectSave=reject;});};`,c);
const saving=vm.runInContext(`saveRevisit('row-future')`,c);
assert.equal(node('revisit-date-row-future').disabled,true);
vm.runInContext(`cancelRevisit('row-future')`,c);
assert.equal(node('revisit-date-row-future').value,'2032-06-07T10:00');
assert.match(node('revisit-error-row-future').textContent,/cannot stop/);
// A reordered list must not change the pending action's exact reminder ID.
vm.runInContext(`revisitRows=[{id:'different',display_status:'recorded'},{id:'future',display_status:'recorded'}]; rejectSave(new Error('Save rejected'));`,c);
await saving;
assert.match(vm.runInContext('sent.at(-1).path',c),/\/future\/actions\?pack_id=social_science$/);
assert.equal(node('revisit-error-row-future').textContent,'Save rejected');
assert.equal(node('revisit-date-row-future').disabled,false);
assert.equal(node('revisit-date-row-future').value,'2032-06-07T10:00');
const retry=vm.runInContext(`saveRevisit('row-future')`,c);
vm.runInContext(`cancelRevisit('row-future'); resolveSave({});`,c);
await retry;
assert.match(node('revisit-error-row-future').textContent,/Reminder saved/);
assert.equal(node('revisit-date-row-future').value,'');
node('revisit-date-row-future').value='2033-06-07T10:00';
const staleError=vm.runInContext(`saveRevisit('row-future')`,c);
node('revisit-error-row-future').textContent='New scope';
vm.runInContext(`S.epoch++; rejectSave(new Error('Old scope failure'));`,c);
await staleError;
assert.equal(node('revisit-error-row-future').textContent,'New scope');
vm.runInContext(`call=()=>new Promise(resolve=>globalThis.resolveRead=resolve);`,c);
const pending=vm.runInContext('loadRevisits()',c);
vm.runInContext(`S.project='other'; S.pack='other'; S.epoch++; revisitRows=[]; resolveRead({conditions:[{id:'leak'}]});`,c);
await pending;
assert.equal(vm.runInContext('revisitRows.length',c),0);
const home = await interiorFixture();
try {
  await home.page.route(url => url.origin === 'http://interior.test' && url.pathname === '/projects/p1/briefing', route => route.fulfill({json:{
    model_calls:0, parts:{
      where_you_left_off:[
        {text:'Previous decision and reason'},
        {text:'Second saved reason'},
        {text:'Third saved reason'},
        {text:'Fourth saved reason'},
        {text:'Fifth saved reason'}
      ],
      waiting_on_you:[],
      ready_now:[{text:'Revisit due today: due one',display_status:'due'}],
      suggested_next_action:[{text:'Revisit due today: due one',display_status:'due'}]
    }
  }}));
  await home.page.locator('#home-project').selectOption('p1');
  await home.page.waitForFunction(() => document.querySelector('#home-conversation').textContent.includes('Revisit due today: due one'));
  const sections = home.page.locator('.home-brief-section');
  assert.equal(await sections.count(),3,'the current home briefing keeps its three labelled sections');
  assert.match(await sections.first().innerText(),/Previous decision and reason/);
  assert.deepEqual(await sections.first().locator('.home-bubble').allTextContents(),[
    'Previous decision and reason','Second saved reason','Third saved reason','+2 more'
  ],'the baseline three-item section limit keeps the first reasons and reports its overflow');
  assert.match(await sections.nth(2).innerText(),/Suggested next action[\s\S]*Revisit due today: due one/,
    'a due reminder supplied as the next action is visible on home');
  assert.equal((await home.page.locator('#home-conversation').innerText()).split('Revisit due today: due one').length-1,1,
    'the due reminder is not duplicated through ready_now');
} finally { await home.close(); }
console.log('REVIEW_REVISIT_UI_PASS: blank authoring, exact decision, UTC and pack payloads, reopen, due panel/home hook, future reschedule, done, pending cancel/success/error races, stable reminder ID, stale scope, no adoption');
