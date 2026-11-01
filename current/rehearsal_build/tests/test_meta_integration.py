"""Meta protocol and lifecycle checks. All provider traffic uses an injected HTTP transport."""
import asyncio,base64,io,json,sys,wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest,httpx
from app import canvas,store
from app.ai import service,registry,grounding
from app.ai.provider import MetaProvider
from app.harness import Conflict,Harness
from test_dot_safety import h
RESULT={'answer':'Reported estimate, not actual cost.','claims':[{'text':'Applicant estimate','evidence_ids':['DD-2023:est_cost'],'kind':'fact'}],'unresolved':['Actual cost unknown'],'proposed_variables':[{'name':'est_cost','dataset_id':'fx/permits_2023.csv','kind':'actual'}],'next_action':'Researcher reviews'}
FRAME={'sources':['DD-2023'],'gap_type':'fact','focus':'Check cost definition'}
@pytest.fixture
def ai(h,monkeypatch):
    monkeypatch.setattr(service.secrets,'key',lambda:'test-not-real-key');seen=[]
    def handler(req):
        seen.append(req)
        if req.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':x} for x,m in registry.MODELS.items() if m.endpoint]})
        if '/asr/transcribe' in req.url.path:return httpx.Response(200,json={'transcript':'Research interview','audioDurationMs':1000,'turns':[{'startMs':0,'endMs':1000,'speaker':'A','transcript':'Research interview'}]})
        # Documented SAM contract (POST /segmentation/interactive, multipart,
        # non-streaming): the retired SSE token grammar no longer parses, so
        # segmentation requests get a synthetic detections-JSON payload of
        # the documented shape instead of an SSE text body.
        if 'segmentation' in req.url.path:
            return httpx.Response(200,json={'image_dimensions':{'width':10,'height':10},
                'detections':[{'id':'det-1','object_type':'building','bbox':{'x':1,'y':2,'width':2,'height':2},'confidence':0.93,'source_prompt_type':'text'}]})
        body=json.loads(req.content)
        text=json.dumps(FRAME if 'public work plan' in body.get('instructions','') else RESULT)
        return httpx.Response(200,json={'status':'completed','model':body['model'],'output':[{'type':'message','content':[{'type':'output_text','text':text}]}],'usage':{'input_tokens':200,'output_tokens':100}})
    svc=service.AIService(h,MetaProvider(httpx.MockTransport(handler),key_reader=lambda:'test-not-real-key'),meta_available=True)
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc,monkeypatch)
    seen.clear()
    pid=h.register_project('fx')['project_id'];qid=asyncio.run(h.create_question(pid,'What does est_cost measure?'))['question_id']
    return svc,qid,seen

def ready(svc):svc.configure(True,150);asyncio.run(svc.verify_account())

def test_typed_routing():
    assert registry.route('methodology_review').id=='muse-wonder-1.0'
    assert registry.route('research_query').id=='meta-models/muse-spark-open-1026'
    assert registry.route('research_query','muse-wonder-1.0').kind=='reasoning'
    with pytest.raises(ValueError):registry.route('research_query','sam-3.1')
    with pytest.raises(ValueError):registry.route('research_query','glimmer_self_hosted')

def test_preview_sends_nothing(ai):
    svc,qid,seen=ai;p=svc.prepare(qid,{'compare':True})
    assert not seen and p['max_inference_calls']==3 and p['requires_explicit_consent']
    assert p['transmission']['text_and_context']['question']=='What does est_cost measure?'
    assert svc.settings()['local_committed_or_reserved_usd']==0

def test_consent_and_cloud_gate(ai):
    svc,qid,seen=ai;p=svc.prepare(qid,{})
    with pytest.raises(Conflict):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    ready(svc)
    for hash_,consent in [('wrong',True),(p['preview_hash'],False)]:
        with pytest.raises(Conflict):asyncio.run(svc.execute(p['job_id'],hash_,consent))
    assert len(seen)==1

def test_paired_same_model_input_usage_and_no_replay(ai):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{'compare':True})
    r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(r['stages'])==3 and r['usage_complete'] and r['calculated_cost_usd']>0
    reqs=[json.loads(x.content) for x in seen[1:]]
    contexts=[json.loads(x['input'][0]['content'][0]['text'])['context'] for x in reqs]
    assert all(c==contexts[0] for c in contexts)
    assert all(x['model']=='meta-models/muse-spark-open-1026' and x['store'] is False and not x['tools'] for x in reqs)
    assert r['outputs']['harness']['grounding']['semantic_correctness']=='NOT_SCORED'
    with pytest.raises(Conflict):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))

def test_reserve_before_call(ai):
    svc,qid,seen=ai;ready(svc);svc.configure(True,1);p=svc.prepare(qid,{})
    with pytest.raises(Conflict,match='budget'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(seen)==1

def test_changed_canvas_invalidates_consent(ai):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{})
    c=canvas.read(svc.h,qid);canvas.save(svc.h,qid,{'expected_revision':0,'nodes':[{k:n[k] for k in ('label','dataset_id','variable','note')} for n in c['nodes']]})
    with pytest.raises(Conflict,match='changed'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(seen)==1

def test_delete_keeps_content_free_financial_ledger(ai):
    svc,qid,_=ai;ready(svc);p=svc.prepare(qid,{'compare':True})
    r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True));svc.h.delete_question(qid)
    assert svc.settings()['local_committed_or_reserved_usd']==pytest.approx(r['calculated_cost_usd'])
    for table in ('ai_assets','ai_jobs','ai_usage'):assert svc.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    assert not store.find_strings(svc.db,['What does est_cost measure?'])

def test_429_no_retry_unknown_charge_retained(ai):
    svc,qid,_=ai;ready(svc);failed=[]
    def handler(req):failed.append(req);return httpx.Response(429,json={'sensitive':'do not log'})
    svc.provider=MetaProvider(httpx.MockTransport(handler),key_reader=lambda:'test')
    p=svc.prepare(qid,{})
    with pytest.raises(Conflict,match='no automatic retry'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(failed)==1 and svc.settings()['local_committed_or_reserved_usd']==pytest.approx(p['maximum_reserved_usd'], abs=1e-9)
    assert 'do not log' not in json.dumps(svc.jobs(qid))

def test_local_registration_and_scope(ai):
    svc,qid,_=ai;a=svc.register_asset(qid,{'name':'notes.txt','mime':'text/plain','encoded':base64.b64encode(b'Private local notes').decode()})
    p=svc.prepare(qid,{'assets':[a['id']]});assert p['transmission']['files'][0]['sha256']==a['sha256']
    other=asyncio.run(svc.h.create_question(svc.h.question(qid)['project_id'],'Other question'))['question_id']
    with pytest.raises(Conflict):svc.prepare(other,{'assets':[a['id']]})
    with pytest.raises(Conflict):svc.register_asset(qid,{'name':'bad.png','mime':'image/png','encoded':base64.b64encode(b'not an image').decode()})

def test_voice_audio_turn_correspondence(ai):
    svc,qid,seen=ai;ready(svc);buf=io.BytesIO()
    with wave.open(buf,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000)
    a=svc.register_asset(qid,{'name':'interview.wav','mime':'audio/wav','encoded':base64.b64encode(buf.getvalue()).decode()})
    p=svc.prepare(qid,{'task':'transcribe','assets':[a['id']]});r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert r['outputs']['source_asset']['sha256']==a['sha256'] and r['outputs']['turns'][0]['speaker']=='A'
    assert b'name="request"' in seen[-1].content and b'name="audio"' in seen[-1].content

def test_sam_pixel_geometry(ai):
    # New-contract equivalent: the bbox {x:1,y:2,width:2,height:2} converts
    # to corners {x1:1,y1:2,x2:3,y2:4} validated against the response dims
    # (source_pixels units, response_image_dimensions origin). Raster mask
    # decode remains honestly NOT_IMPLEMENTED — there is no mask raster to
    # assert on under the new contract.
    svc,qid,_=ai;ready(svc);a=svc.register_asset(qid,{'name':'scene.png','mime':'image/png','encoded':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()})
    p=svc.prepare(qid,{'task':'segment','assets':[a['id']],'text':'building'});r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    obj=r['outputs']['objects'][0];assert obj['id']=='det-1' and obj['object_type']=='building'
    assert obj['box']=={'x1':1,'y1':2,'x2':3,'y2':4,'w':10,'h':10}
    assert (obj['source_image_width'],obj['source_image_height'])==(10,10)
    assert obj['source_dimensions_origin']=='response_image_dimensions' and obj['units']=='source_pixels'
    assert obj['confidence']==pytest.approx(0.93)
    assert obj['geometry']['in_bounds'] is True
    assert obj['mask_raster_decode']=='NOT_IMPLEMENTED'

def test_grounding_flags_invented_id_and_variable(ai):
    svc,qid,_=ai;r=json.loads(json.dumps(RESULT));r['claims'][0]['evidence_ids']=['INVENTED'];r['proposed_variables'][0]['name']='actual_cost'
    _,check=grounding.validate(json.dumps(r),svc._snapshot(qid)['packet'],[])
    assert not check['structural_valid'] and check['invalid_citation_count']==1

def test_offline_launch_and_canvas(tmp_path,monkeypatch):
    monkeypatch.delenv('FAKE_BASE_URL',raising=False);monkeypatch.delenv('WB_ALLOW_REAL_MUSE',raising=False)
    from app.harness import Harness
    h=Harness(str(tmp_path/'offline.db'))
    try:
        p=h.register_project('fx')['project_id'];q=asyncio.run(h.create_question(p,'Local question'))
        assert q['adapter']=='offline' and canvas.read(h,q['question_id'])['model_calls']==0
        assert h.db.execute('SELECT reserved_requests FROM question_budgets').fetchone()[0]==0
    finally:h.db.close()


def test_missing_usage_keeps_reservation(ai):
    svc,qid,_=ai;ready(svc)
    def handler(req):return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(RESULT)}]}]})
    svc.provider=MetaProvider(httpx.MockTransport(handler),key_reader=lambda:'test')
    p=svc.prepare(qid,{'harness':False});r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert r['calculated_cost_usd'] is None and not r['usage_complete']
    assert svc.settings()['local_committed_or_reserved_usd']==p['maximum_reserved_usd']


def test_deleted_inflight_question_cannot_return_or_restore_content(ai):
    svc,qid,_=ai;ready(svc)
    async def handler(req):
        svc.h.delete_question(qid)
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(RESULT)}]}],'usage':{'input_tokens':1,'output_tokens':1}})
    svc.provider=MetaProvider(httpx.MockTransport(handler),key_reader=lambda:'test')
    p=svc.prepare(qid,{'harness':False})
    with pytest.raises(Conflict):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    for table in ('ai_jobs','ai_usage'):assert svc.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    assert svc.settings()['local_committed_or_reserved_usd']==pytest.approx(p['maximum_reserved_usd'], abs=1e-9)


def test_account_missing_model_does_not_substitute(ai):
    svc,qid,seen=ai;ready(svc)
    svc.db.execute('UPDATE ai_settings SET catalog_json=?',(json.dumps(['muse-spark-1.3']),));svc.db.commit()
    p=svc.prepare(qid,{'task':'methodology_review','max_cost_usd':20.0})
    with pytest.raises(Conflict,match='model access'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(seen)==1 and svc.settings()['local_committed_or_reserved_usd']==0


def test_session_only_cleanup_removes_multimodal_assets(ai):
    svc,qid,_=ai;pid=svc.h.question(qid)['project_id']
    private=asyncio.run(svc.h.create_question(pid,'Private interview',memory='session_only'))['question_id']
    svc.register_asset(private,{'name':'notes.txt','mime':'text/plain','encoded':base64.b64encode(b'Private asset content').decode()})
    svc.prepare(private,{})
    svc.h.end_session()
    assert svc.db.execute('SELECT count(*) FROM ai_assets WHERE question_id=?',(private,)).fetchone()[0]==0
    assert svc.db.execute('SELECT count(*) FROM ai_jobs WHERE question_id=?',(private,)).fetchone()[0]==0


def test_manual_review_is_required_for_semantic_scores(ai):
    from app.ai.evaluation import evaluate
    svc,qid,_=ai;ready(svc);p=svc.prepare(qid,{'compare':True});r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    review={'reviewer':'test-reviewer','gold_case_id':'FX-COST-1','rubric_version':'rw-research-1','arms':{k:{'claim_verdicts':['supported'],'citation_verdicts':['supports'],'variable_method_correct':True,'task_success':True} for k in ('basic','harness')}}
    score=evaluate(r,review)
    assert score['scores']['basic']['evidence_accuracy']==1
    review['arms']['basic']['claim_verdicts']=[]
    with pytest.raises(ValueError):evaluate(r,review)


def test_image_generation_is_labeled_and_does_not_touch_canvas(ai):
    svc,qid,_=ai;ready(svc);before=canvas.read(svc.h,qid)
    png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGZkAAAAASUVORK5CYII='
    requests=[]
    def handler(req):
        body=json.loads(req.content);requests.append(body)
        return httpx.Response(200,json={'status':'completed','output':[{'type':'image_generation_call','result':png}]})
    svc.provider=MetaProvider(httpx.MockTransport(handler),key_reader=lambda:'test')
    p=svc.prepare(qid,{'task':'image_generate','text':'Concept illustration, not data visualization'})
    r=asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert r['outputs']['images'][0]['label']=='AI_GENERATED_IMAGE_NOT_RESEARCH_EVIDENCE'
    assert canvas.read(svc.h,qid)==before
    assert requests[0]['tools'][0]['enable_web_search'] is False
    assert requests[0]['tools'][0]['enable_shell'] is False


def test_contract_change_invalidates_prepared_transmission(ai,monkeypatch):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{})
    monkeypatch.setattr(service,'SAFE_CORE','Changed policy')
    with pytest.raises(Conflict,match='contract changed'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(seen)==1


def test_restart_preserves_unknown_charge_without_replay(ai):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{})
    svc.db.execute("INSERT INTO ai_spend VALUES(?,?,'running',NULL)",(p['job_id'],p['maximum_reserved_usd']))
    svc.db.execute("UPDATE ai_jobs SET status='running' WHERE id=?",(p['job_id'],));svc.db.commit()
    restarted=service.AIService(svc.h,svc.provider)
    assert restarted.jobs(qid)[0]['status']=='interrupted_unknown_charge'
    assert restarted.settings()['local_committed_or_reserved_usd']==p['maximum_reserved_usd']
    assert len(seen)==1


def test_sam_empty_match_and_sparse_frames():
    # Original intent (empty-match + sparse-frame handling) under the new
    # contract: grounding.detections returns [] for an empty detections
    # array, and the retired token parser now fails closed on any string
    # input (no invented boxes, no mask raster). Frame/object-id coverage
    # moved to the detections-JSON id/object_type fields asserted above.
    assert grounding.detections({'image_dimensions':{'width':4,'height':3},'detections':[]})==[]
    with pytest.raises(ValueError):
        grounding.segmentation('')
    with pytest.raises(ValueError):
        grounding.segmentation('<7f>9<|box;x1=1;y1=1;x2=2;y2=2;w=10;h=10|><|mask;x=0;y=0;data=2,2,~a|b|>')


def test_key_rotation_requires_new_account_verification(ai,monkeypatch):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{})
    monkeypatch.setattr(service.secrets,'baseten_key',lambda:'rotated-test-key')
    with pytest.raises(Conflict,match='model access'):asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    assert len(seen)==1


def test_http_ai_preview_and_origin_guard(ai):
    from fastapi.testclient import TestClient
    from app.api import create_app
    svc,qid,_=ai
    path=svc.db.execute('PRAGMA database_list').fetchone()['file']
    with TestClient(create_app(path,svc.h.data)) as client:
        registry_response=client.get('/ai/registry');assert registry_response.status_code==200
        assert registry_response.json()['settings']['cloud_enabled'] is False
        preview=client.post(f'/questions/{qid}/ai/prepare',json={'task':'research_query'})
        assert preview.status_code==200 and preview.json()['requires_explicit_consent']
        blocked=client.post('/ai/settings',json={'cloud_enabled':True,'budget_usd':5},headers={'Origin':'https://untrusted.example'})
        assert blocked.status_code==403
        not_approved=client.post(f"/ai/jobs/{preview.json()['job_id']}/execute",json={'consent':False,'preview_hash':preview.json()['preview_hash']})
        assert not_approved.status_code==409


def test_native_schema_constrains_plan_source_ids(ai):
    svc,qid,seen=ai;ready(svc);p=svc.prepare(qid,{})
    asyncio.run(svc.execute(p['job_id'],p['preview_hash'],True))
    bodies=[json.loads(r.content) for r in seen[1:]]
    assert all(b['text']['format']['strict'] is True for b in bodies)
    frame=bodies[0]['text']['format']['schema']
    assert frame['additionalProperties'] is False
    assert 'DD-2023' in frame['properties']['sources']['items']['enum']
    assert set(frame['properties']['gap_type']['enum'])=={'fact','preference','private_context','permission','none'}


def test_explicit_contributor_selection_is_bound_to_preview(ai):
    svc,qid,_=ai
    with pytest.raises(Conflict,match='meta_spark_blocked_credits_exhausted'):
        svc.prepare(qid,{'model':'muse-spark-1.3-contributor'})
    assert registry.route('research_query').id=='meta-models/muse-spark-open-1026'


def test_explicit_source_release_mismatch_is_flagged(ai):
    svc,qid,_=ai;r=json.loads(json.dumps(RESULT))
    r['claims'][0]['evidence_ids']=['OFF-NOTES-2025']
    _,check=grounding.validate(json.dumps(r),svc._snapshot(qid)['packet'],[])
    assert not check['structural_valid'] and any('another release' in e for e in check['errors'])
