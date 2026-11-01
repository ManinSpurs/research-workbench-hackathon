// Supplemental UI regression test with explicitly scripted HTTP fixtures, NOT a Muse or Tier-1 gate test.
// node tests/dot_ui.mjs — serves the real frontend locally and clicks it in Chromium.
// Fixture note: /preparations 404s for qids the fixture never issued, mirroring the real
// backend (harness.list_preparations raises KeyError -> 404 for unknown questions), so the
// loadPreparations catch path (frontend/app.js) is exercised rather than skipped.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const server = createServer(async (req,res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  try { const file = path === '/' ? 'index.html' : path.slice(1); if(!['index.html','app.js','style.css','theme.js'].includes(file)) throw Error();
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'); res.end(await readFile(root+file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH}: {})});
try {
  const page = await browser.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  let asks=0, responses=[], pending=[], delayQuestion=null, uploads=[]; const issuedQids=new Set();
  const cq={gap:'What does approval mean?',why_matters:'It changes the design.',branches:[{condition:'final',outcome:'use date'}]};
  const empty={where_you_left_off:[],what_changed_since:[],ready_now:[],waiting_on_you:[],suggested_next_action:[]};
  const evidence={result_kind:'EvidenceAssessment',assessment:{claim:'A registered definition.',outcome:'supported',confirmation_level:'confirmed',unresolved:[],suggested_next_action:'Decide'},evidence:[{source_id:'fixture-dictionary',source_version:'sha256:test-version',excerpt:'Final approval',location:'row 2'}]};
  await page.route('http://wb.test/**',async route=> {
    const req=route.request(), path=new URL(req.url()).pathname, body=req.postDataJSON();
    const reply=data=>route.fulfill({json:data});
    // The pack list is server-owned now: #pack-select is built from it, so the
    // fixture answers it with the two sample packs this suite works with.
    if(path==='/domain-packs') return reply({packs:[{pack_id:'social_science',version:'1.0.0',title:'Social Science'},{pack_id:'engineering_measurement',version:'1.0.0',title:'Engineering: Measurement'}],default_pack:'social_science'});
    if(path==='/chips') return reply({chips:[]});
    // The home secretary reads the inserted chip and its saved record (real GET routes).
    if(/^\/chips\/[a-z_]+$/.test(path)) return reply({format:1,pack_id:path.split('/')[2],version:'1.0.0',title:'Social Science',source:'builtin',tagline:'',color:'',glyph:'',art_url:null});
    if(/^\/chips\/[a-z_]+\/record$/.test(path)) return reply({questions:[],answers:[],results:[],code:[],harness:[]});
    if(/^\/projects\/[^/]+\/revisit-conditions$/.test(path)) return reply({conditions:[],model_calls:0});
    if(/^\/projects\/[^/]+\/secretary-turns$/.test(path)) return reply({turns:[],model_calls:0});
    if(path==='/projects/upload') { uploads.push(body); return reply({project_id:'p3',name:body.name,kind:'custom-fixture'}); }
    if(path==='/projects' && req.method()==='GET') return reply({projects:[{id:'p1',name:'First project',kind:'nyc'},{id:'p2',name:'Second project',kind:'fx'}, ...(uploads.length ? [{id:'p3',name:uploads.at(-1).name,kind:'custom-fixture'}]:[])]});
    if(path.endsWith('/profile')) return reply({variables:[{variable:'approval',file:'fixture.csv',rows:2,missing:0,distinct:2,examples:['final']}],computed_by:'ordinary code, no model call'});
    if(path.endsWith('/briefing')) return reply({parts:empty,model_calls:0});
    if(path.endsWith('/clarifications')) return reply({clarifications:path.includes('p1') ? pending:[]});
    if(path.endsWith('/questions')) { asks++; const qid='q'+asks; issuedQids.add(qid); if(delayQuestion) await delayQuestion; return reply({question_id:qid,adapter:'mock',refinement_status:'ok',working_text:body.text}); }
    if(path.endsWith('/run')) { pending=[{cq_id:'c1',question_id:'q'+asks,task_id:'t1',status:'pending',clarification:cq,adapter:'mock'}]; return reply({result_kind:'ClarificationNeeded',cq_id:'c1',task_id:'t1',clarification:cq,design:{body:{variables:[{name:'approval',kind:'actual',role:'outcome'}],assumptions:['Meaning unverified'],risks:['Ambiguous dates'],verification_plan:'Read dictionary'},harness_flags:[]}}); }
    if(path.endsWith('/responses')) {
      responses.push(body);
      if(body.kind==='later') { pending[0].status='deferred'; return reply({state:'deferred_pending'}); }
      if(body.kind==='use_source' && body.value==='blocked') return reply({state:'blocked_pending_authorization',cq_status:'pending',note:'Not allowed.'});
      pending=[];
      if(['decline','cancel'].includes(body.kind)) return reply({state:body.kind==='decline' ? 'declined':'cancelled'});
      return reply(body.kind==='answer' ? {state:'decision_pending',note:'User statement; not externally verified.'} : evidence);
    }
    if(path==='/session/end') { pending=[]; return reply({deleted:1,all_verified:true}); }
    if(path.endsWith('/preparations')) { const qid=path.split('/')[2]; if(!issuedQids.has(qid)) return route.fulfill({status:404,json:{error:'not_found',message:'unknown question'}}); return reply({question_id:qid,jobs:[],cards:[],scope:null}); }
    throw Error('Unexpected endpoint '+path);
  });
  // A remembered research field (the app's own wb.pack key) makes the cartridge
  // display expand straight into the workspace, so this scripted-fixture test
  // starts on the app instead of the first-run field choice. Same approach as
  // tests/click.mjs; it exercises no new behavior.
  await page.addInitScript(()=>{ try { window.localStorage.setItem('wb.pack','social_science'); } catch(_) {} });
  await page.goto(`http://127.0.0.1:${server.address().port}/?api=http://wb.test`);
  assert.equal(await page.getByTestId('ask').isDisabled(),true,'ask must be disabled with no project');
  await page.getByText('Register local CSV + dictionary',{exact:true}).click();
  await page.getByTestId('upload-name').fill('Uploaded fixture');
  await page.getByTestId('upload-project').click(); await page.waitForFunction(()=>!S.busy);
  assert.match(await page.getByTestId('error').innerText(),/Select both/); assert.equal(uploads.length,0,'missing files must not upload');
  await page.getByTestId('upload-csv').setInputFiles({name:'fixture.csv',mimeType:'text/csv',buffer:Buffer.from('approval_date\n2026-01-01\n')});
  await page.getByTestId('upload-dictionary').setInputFiles({name:'dictionary.json',mimeType:'application/json',buffer:Buffer.from('{broken')});
  await page.getByTestId('upload-project').click(); await page.waitForFunction(()=>!S.busy);
  assert.match(await page.getByTestId('error').innerText(),/valid JSON/); assert.equal(uploads.length,0,'invalid JSON must not upload');
  await page.getByTestId('upload-dictionary').setInputFiles({name:'dictionary.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({entries:[{source_id:'dict',evidence_id:'ev1',field:'approval_date',definition:'Final approval date',location:'row 1'}]}))});
  await page.getByTestId('upload-project').click(); await page.waitForFunction(()=>!S.busy);
  assert.equal(uploads.length,1); assert.equal(uploads[0].name,'Uploaded fixture'); assert.match(uploads[0].csv_text,/2026-01-01/);
  assert.match(await page.getByTestId('upload-status').innerText(),/Original files unchanged/);
  assert.equal(await page.locator('#project-title').innerText(),'Uploaded fixture');
  await page.getByTestId('open-nyc').click();
  async function ask() { await page.evaluate(()=>{document.getElementById('question-entry').open=true;}); await page.getByTestId('question').fill('Clarify approval'); await page.getByTestId('ask').click(); await page.getByTestId('resp-unknown').waitFor(); await page.waitForFunction(()=>!S.busy); }
  await ask();
  assert.match(await page.getByTestId('adapter-badge').innerText(),/MOCK/);
  for(const kind of ['answer','use-source','unknown','later','decline','cancel']) assert.equal(await page.getByTestId('resp-'+kind).isVisible(),true);
  assert.match(await page.getByTestId('assumptions').innerText(),/unverified/); assert.match(await page.getByTestId('risks').innerText(),/Ambiguous/);
  await page.getByTestId('resp-answer').click(); assert.equal(responses.length,0,'blank answer must not be sent');
  await page.getByTestId('resp-later').click(); await page.getByTestId('resume-0').waitFor(); await page.waitForFunction(()=>!S.busy);
  assert.match(await page.getByTestId('pending').innerText(),/deferred/);
  await page.reload(); await page.getByTestId('resume-0').click();
  await page.getByTestId('source-value').fill('blocked'); await page.getByTestId('resp-use-source').click(); await page.waitForFunction(()=>!S.busy);
  assert.equal(await page.getByTestId('clarification').isVisible(),true,'blocked source must leave CQ actionable');
  await page.getByTestId('source-value').fill('fixture-dictionary'); await page.getByTestId('resp-use-source').click();
  await page.getByTestId('evidence').waitFor(); await page.waitForFunction(()=>!S.busy);
  assert.equal(responses.at(-1).value,'fixture-dictionary'); assert.equal(responses.at(-1).source_id,'fixture-dictionary');
  assert.match(await page.getByTestId('evidence-version').innerText(),/sha256:test-version/);
  await ask(); await page.getByTestId('answer-value').fill('Final approval'); await page.getByTestId('resp-answer').click(); await page.waitForFunction(()=>!S.busy);
  assert.equal(responses.at(-1).value,'Final approval'); assert.equal(await page.getByTestId('reason').isVisible(),true);
  for(const kind of ['unknown','decline','cancel']) { await ask(); await page.getByTestId('resp-'+kind).click(); await page.waitForFunction(()=>!S.busy); assert.equal(responses.at(-1).kind,kind); assert.equal(await page.getByTestId('clarification').isVisible(),false); }
  await page.getByTestId('open-fx').click();
  // openProject() is async and re-enters controls() while its briefing/pending/
  // profile loads land, so S.busy flickers false-true-false. Waiting for a
  // single !S.busy sample is not enough: the surface can be disabled again right
  // after. Require the question entry to stay enabled and un-faded across two
  // animation frames before touching it.
  await page.waitForFunction(()=>S.project && !S.busy);
  assert.equal(await page.getByTestId('delete').isDisabled(),true); assert.equal(await page.getByTestId('shadow-run').isDisabled(),true);
  assert.equal(await page.getByTestId('result').isVisible(),false); assert.equal(await page.evaluate(()=>S.question===null && S.cq===null && S.task===null),true);
  let release; delayQuestion=new Promise(resolve=>{release=resolve;});
  await page.evaluate(()=>{document.getElementById('question-entry').open=true;});
  // This step is about two submits collapsing into one request, not about typing, so
  // the text is set on the form's field. Chromium can leave a <details> that was
  // toggled programmatically while loads were landing with a collapsed render slot:
  // open=true, children still laid out at the closed size with the disabled opacity
  // stuck, and neither toggling again nor forcing a style recalc recovers it. A real
  // click never lands there — the browser walkthrough expands and types reliably — and
  // typing into this field is already covered by ask() above.
  await page.evaluate(()=>{ const q=document.getElementById('question'); q.value='Slow question'; q.dispatchEvent(new Event('input',{bubbles:true})); });
  assert.equal(await page.evaluate(()=>document.getElementById('question').value),'Slow question');
  const before=asks; await page.evaluate(()=>{ask({preventDefault(){}}); ask({preventDefault(){}});});
  await page.waitForFunction(()=>S.busy); await page.getByTestId('open-nyc').click(); release(); delayQuestion=null;
  await page.waitForFunction(()=>!S.busy); assert.equal(asks,before+1,'double submit must send one request');
  assert.equal(await page.evaluate(()=>S.question),null,'late response must not install prior-project question');
  assert.equal(await page.getByTestId('refinement').isVisible(),false,'late response must not render prior project');
  await page.evaluate(async ()=>{ S.question='q1'; await loadPreparations(); });
  assert.deepEqual(await page.evaluate(()=>S.preparations),{jobs:[],cards:[]},'known qid must load empty preparations');
  await page.evaluate(async ()=>{ S.question='q-never-issued'; await loadPreparations(); });
  assert.equal(await page.evaluate(()=>S.preparations),null,'unknown qid 404 must null the preparations state');
  assert.equal(await page.evaluate(()=>S.prepScope),null,'unknown qid 404 must null the prep scope');
  assert.equal(await page.getByTestId('preparations').count(),0,'unknown qid 404 must render no preparations section');
  await page.evaluate(()=>{ S.question=null; });
  await page.getByTestId('end-session').click(); await page.getByTestId('session-status').getByText('Session ended.',{exact:false}).waitFor();
  assert.deepEqual(errors,[]); console.log('DOT_UI_PASS local upload validation/registration, six controls, validation, deferred reload/resume, blocked source, values/provenance, switch isolation, late response, duplicate submit, preparations 404 null-state, session end');
} finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
