import asyncio, json
import pytest
from test_dot_safety import h, capture
from test_tier1 import client, fake, mode, project, ask_run, usage, DEMO
from app.harness import Conflict, Harness
from app import store
from app.results import EvidenceAssessment
from pydantic import ValidationError


def test_upload_registration_profiles_and_survives_restart(h):
    csv = 'permit_id,cost\nP1,10\nP2,\n'
    dictionary = {'entries':[{'source_id':'LOCAL1','evidence_id':'LOCAL1:cost','field':'cost',
                              'definition':'Reported cost','location':'Dictionary row 1'}]}
    p = h.register_upload('Uploaded test',csv,dictionary)
    prof = h.profile(p['project_id'])
    assert len(prof['variables']) == 2
    assert prof['variables'][1]['missing'] == 1
    assert prof['registered_sources'][0]['source_id'] == 'LOCAL1'
    assert len(prof['registered_sources'][0]['version']) == 64
    db_path = h.db.execute('PRAGMA database_list').fetchone()['file']
    again = Harness(db_path,h.data)
    assert again.profile(p['project_id']) == prof
    assert (h.data / again.specs[p['kind']]['files'][0]).read_text() == csv
    again.db.close()

@pytest.mark.parametrize('csv,dictionary', [('',{'entries':[]}),('a,a\n1,2\n',{'entries':[]}),
 ('a\n1\n',{'entries':[{'field':'x'}]}), ('a\n1,2\n',{'entries':[]})])
def test_invalid_upload_never_registers_or_writes(h,csv,dictionary):
    before=list(h.data.rglob('*'))
    with pytest.raises(Conflict):h.register_upload('test',csv,dictionary)
    assert list(h.data.rglob('*')) == before
    assert h.db.execute('SELECT count(*) FROM projects').fetchone()[0] == 0


def test_persisted_call_budget_stops_before_extra_call(h):
    async def run():
        pid,qid=await capture(h)
        limits=json.loads(h.db.execute('SELECT limits_json FROM question_budgets WHERE question_id=?',(qid,)).fetchone()[0])
        limits['model_requests']=4
        h.db.execute('UPDATE question_budgets SET limits_json=? WHERE question_id=?',(json.dumps(limits),qid));h.db.commit()
        first=await h.run(qid)
        before=list(h.adapter.calls)
        with pytest.raises(Conflict,match='allowance exhausted'):await h.respond(first['cq_id'],'unknown')
        assert h.adapter.calls == before
        db_path=h.db.execute('PRAGMA database_list').fetchone()['file']
        again=Harness(db_path,h.data)
        with pytest.raises(Conflict,match='allowance exhausted'):await again.shadow(qid)
        again.db.close()
    asyncio.run(run())


def test_expired_budget_stops_before_call(h):
    async def run():
        _,qid=await capture(h)
        h.db.execute("UPDATE question_budgets SET created_at='2000-01-01T00:00:00+00:00'");h.db.commit()
        before=list(h.adapter.calls)
        with pytest.raises(Conflict,match='allowance exhausted'):await h.run(qid)
        assert h.adapter.calls == before
    asyncio.run(run())


def test_failed_validation_attempts_remain_in_ledger(client,mode):
    mode('hallucinate_id');pid=project(client);q,r=ask_run(client,pid,DEMO)
    out=client.post(f"/clarifications/{r['cq_id']}/responses",json={'kind':'unknown'})
    assert out.json()['outcome']=='invalid_output'
    rows=[u for u in usage(client,pid) if u['stage']=='assess']
    assert len(rows)==1 and rows[0]['requests']==2 and rows[0]['input_tokens']>0


def test_literal_deletion_search_has_no_sql_wildcards(h):
    assert store.find_strings(h.db,['%%%%']) == []


def test_confirmed_insufficient_rejected_even_without_unresolved():
    with pytest.raises(ValidationError):
        EvidenceAssessment(claim='Unknown',evidence_ids=[],applicability='Unknown',outcome='insufficient',
           confirmation_level='confirmed',unresolved=[],precedent_label=None,precedent_source_ids=[],suggested_next_action='Check')


def test_refinement_retains_numbers_and_known_variables():
    from app.refine_check import check
    assert check('Can I study firms with at least 10 workers?','Can I study firms with at least 100 workers?')[0]=='mismatch'
    assert check('Can I use approval_date?','Can I use issue_date?',['approval_date','issue_date'])[0]=='mismatch'


def test_missing_tokens_not_zero():
    from app.compare import measured_tokens
    assert measured_tokens([{'input_tokens':None,'output_tokens':1}]) is None


def test_unscripted_mock_is_not_a_research_outcome(client):
    pid=project(client)
    q,r=ask_run(client,pid,'What is the role of lunar geology in architecture?')
    assert r['outcome']=='mock_unsupported' and r['adapter']=='mock'
    assert r['evidence']==[]


def test_all_six_clarification_choices_and_versions_via_api(client):
    pid=project(client)
    for kind,value in [('answer','I believe it means permit issuance'),('unknown',None),('use_source','DD-BIS-2019'),
                       ('later',None),('decline',None),('cancel',None)]:
        q,r=ask_run(client,pid,DEMO)
        out=client.post(f"/clarifications/{r['cq_id']}/responses",json={'kind':kind,'value':value})
        assert out.status_code==200
        if out.json().get('evidence'):
            assert all(len(e['source_version'])==64 for e in out.json()['evidence'])
        if kind=='later':
            pending=client.get(f'/projects/{pid}/clarifications').json()['clarifications']
            assert any(c['cq_id']==r['cq_id'] and c['status']=='deferred' for c in pending)
            resumed=client.post(f"/clarifications/{r['cq_id']}/responses",json={'kind':'unknown'})
            assert resumed.status_code==200


def test_session_end_during_refinement_cannot_save_private_question(h):
    async def run():
        pid=h.register_project('fx')['project_id']
        h.adapter.pause='refine'
        pending=asyncio.create_task(h.create_question(pid,'PRIVATE-INFLIGHT-SESSION-42?', 'session_only'))
        await asyncio.wait_for(h.adapter.entered.wait(),2)
        assert h.end_session()['all_verified']
        h.adapter.release.set()
        with pytest.raises(Conflict,match='session ended'):
            await asyncio.wait_for(pending,2)
        assert h.db.execute('SELECT count(*) FROM questions').fetchone()[0] == 0
        assert 'PRIVATE-INFLIGHT-SESSION-42' not in '\n'.join(h.db.iterdump())
    asyncio.run(run())
