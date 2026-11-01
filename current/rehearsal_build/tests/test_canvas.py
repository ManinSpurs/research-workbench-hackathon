"""Deterministic canvas boundary tests, no model quality claims."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from app import canvas,store
from app.harness import Conflict,Harness
from test_dot_safety import h,StubAdapter


def capture(h,memory='remember',pack='social_science',dataset=None):
    pid=h.register_project('fx' if dataset is None else 'nyc')['project_id']
    q=asyncio.run(h.create_question(pid,'Canvas boundary question',memory=memory,pack_id=pack,dataset_ids=dataset))
    return pid,q['question_id']


def nodes(variable='application_date',dataset='fx/permits_2023.csv'):
    return [{'label':'Filing','dataset_id':dataset,'variable':variable,'note':'Researcher mapping, not verified'},
            {'label':'Target construction start','dataset_id':None,'variable':None,'note':'Needs evidence'}]


def edges():
    return [{'from_index':0,'to_index':1,'relation':'precedes','note':'Researcher expectation, not verified'}]


def test_canvas_read_does_not_call_model_or_spend_budget(h):
    pid,qid=capture(h)
    before=dict(h.db.execute('SELECT * FROM question_budgets').fetchone())
    calls=list(h.adapter.calls)
    first=canvas.read(h,qid)
    assert first['revision']==0 and all(n['status']=='unmapped' for n in first['nodes'])
    assert first['model_calls']==0 and h.adapter.calls==calls
    assert dict(h.db.execute('SELECT * FROM question_budgets').fetchone())==before
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_link_and_evidence_remain_distinct_from_verification(h):
    pid,qid=capture(h)
    result=canvas.save(h,qid,{'expected_revision':0,'nodes':nodes()})
    first=result['nodes'][0]
    assert first['status']=='column_linked' and first['interpretation_status']=='NOT_VERIFIED'
    assert first['evidence'][0]['evidence_id']=='DD-2023:application_date'
    assert result['nodes'][1]['status']=='unmapped'
    assert result['model_calls']==0 and h.adapter.calls==['refine']
    assert h.question(qid)['original_text']=='Canvas boundary question'
    assert h.db.execute('SELECT count(*) FROM decisions').fetchone()[0]==0


@pytest.mark.parametrize('variable,dataset',[('construction_start','fx/permits_2023.csv'),('application_date','outside.csv'),('application_date',None),(None,'fx/permits_2023.csv')])
def test_invalid_binding_rejected(h,variable,dataset):
    _,qid=capture(h)
    with pytest.raises(Conflict): canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(variable,dataset)})
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_cannot_borrow_column_from_other_dataset(h):
    _,qid=capture(h,dataset=['nyc/dobnow_2024_subset.csv'])
    with pytest.raises(Conflict): canvas.save(h,qid,{'expected_revision':0,'nodes':nodes('filing_date','nyc/bis_2024_subset.csv')})


def test_revision_conflict_preserves_previous_draft(h):
    _,qid=capture(h)
    canvas.save(h,qid,{'expected_revision':0,'nodes':nodes()})
    changed=nodes();changed[1]['note']='Second draft'
    result=canvas.save(h,qid,{'expected_revision':1,'nodes':changed})
    assert result['revision']==2 and result['previous']['nodes'][1]['note']=='Needs evidence'
    with pytest.raises(Conflict,match='reload'):canvas.save(h,qid,{'expected_revision':1,'nodes':nodes()})
    assert canvas.read(h,qid)['nodes'][1]['note']=='Second draft'


def test_restart_and_delete_include_canvas_history(h):
    _,qid=capture(h)
    draft=nodes();draft[0]['note']='UNIQUE-CANVAS-PRIVATE-NOTE-96731'
    canvas.save(h,qid,{'expected_revision':0,'nodes':draft})
    canvas.save(h,qid,{'expected_revision':1,'nodes':nodes()})
    path=h.db.execute('PRAGMA database_list').fetchone()['file']
    other=Harness(path,h.data,adapter=StubAdapter())
    try:
        assert canvas.read(other,qid)['previous']['nodes'][0]['note']==draft[0]['note']
        assert other.deletion_preview(qid)['by_store']['research_canvases']==1
        other.delete_question(qid)
        assert not store.find_strings(other.db,[draft[0]['note']])
        with pytest.raises(KeyError):canvas.read(other,qid)
    finally:other.db.close()


def test_session_cleanup_removes_canvas(h):
    _,qid=capture(h,memory='session_only')
    canvas.save(h,qid,{'expected_revision':0,'nodes':nodes()})
    h.end_session()
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_engineering_defaults_are_components_not_event_times(h):
    _,qid=capture(h,pack='engineering_measurement')
    result=canvas.read(h,qid)
    assert result['layout']=='measurement_links'
    assert [n['label'] for n in result['nodes']]==['Measured quantity','Unit','Test condition','Instrument','Uncertainty']
    assert all(n['variable'] is None for n in result['nodes'])


@pytest.mark.parametrize('payload',[{'expected_revision':0,'nodes':[]},{'expected_revision':0,'nodes':nodes(),'confirmed':True},{'expected_revision':True,'nodes':nodes()}])
def test_invalid_edits_fail_before_writing(h,payload):
    _,qid=capture(h)
    with pytest.raises(Conflict):canvas.save(h,qid,payload)
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_changed_registered_data_blocks_canvas_write(h):
    _,qid=capture(h)
    (h.data/'fx/permits_2023.csv').write_text('changed\n1\n')
    with pytest.raises(Conflict,match='version changed'):canvas.save(h,qid,{'expected_revision':0,'nodes':nodes()})


@pytest.mark.parametrize('count',[1,9])
def test_node_count_bounds_are_explicit(h,count):
    """A mind map needs one relationship (2 nodes); more than 8 is rejected."""
    _,qid=capture(h)
    payload=[{'label':f'Node {i}','dataset_id':None,'variable':None,'note':''} for i in range(count)]
    with pytest.raises(Conflict,match='2 to 8'):canvas.save(h,qid,{'expected_revision':0,'nodes':payload})
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_edge_save_and_read_round_trip(h):
    _,qid=capture(h)
    result=canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':edges()})
    assert result['revision']==1
    assert result['edges']==[{'from_index':0,'to_index':1,'relation':'precedes',
                              'note':'Researcher expectation, not verified',
                              'relation_status':'RESEARCHER_ASSERTED'}]
    # No evidence is attached to an edge and node evidence is untouched.
    assert 'evidence' not in result['edges'][0]
    assert result['nodes'][0]['interpretation_status']=='NOT_VERIFIED'
    assert result['model_calls']==0 and h.adapter.calls==['refine']
    reread=canvas.read(h,qid)
    assert reread['edges']==result['edges']


def test_edge_notice_explains_researcher_assertion(h):
    _,qid=capture(h)
    notice=canvas.read(h,qid)['notice']
    # Existing sentences are preserved verbatim and the edge clause is appended.
    assert notice.startswith('Links and sequence are researcher-authored assumptions.')
    assert 'An unmapped node does not prove a variable is absent.' in notice
    assert 'A drawn edge is the researcher' in notice
    assert 'RESEARCHER_ASSERTED' in notice
    assert 'verified relationship' in notice and 'causal claim' in notice


@pytest.mark.parametrize('edge,message',[
    ({'from_index':0,'to_index':2,'relation':'precedes'},'node 2'),
    ({'from_index':2,'to_index':0,'relation':'precedes'},'node 2'),
    ({'from_index':1,'to_index':1,'relation':'same event'},'itself'),
    ({'from_index':0,'to_index':1,'relation':''},'2 to 8'),
    ({'from_index':0,'to_index':1},'2 to 8'),
    ({'from_index':0,'to_index':-1,'relation':'precedes'},'2 to 8'),
])
def test_invalid_edges_rejected(h,edge,message):
    _,qid=capture(h)
    with pytest.raises(Conflict,match=message):
        canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':[edge]})
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_duplicate_edge_triple_rejected(h):
    _,qid=capture(h)
    duplicate=edges()*2
    with pytest.raises(Conflict,match='Duplicate canvas edge'):canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':duplicate})
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0
    # A different relation between the same two nodes is a distinct, allowed edge.
    distinct=edges()+[{'from_index':0,'to_index':1,'relation':'motivates','note':''}]
    result=canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':distinct})
    assert [e['relation'] for e in result['edges']]==['precedes','motivates']


def test_too_many_edges_rejected(h):
    _,qid=capture(h)
    many=[{'from_index':0,'to_index':1,'relation':f'relation {i}','note':''} for i in range(17)]
    with pytest.raises(Conflict,match='2 to 8'):canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':many})
    assert h.db.execute('SELECT count(*) FROM research_canvases').fetchone()[0]==0


def test_legacy_body_without_edges_reads_empty_then_upgrades(h):
    _,qid=capture(h)
    canvas.save(h,qid,{'expected_revision':0,'nodes':nodes()})
    legacy=json.dumps({'nodes':nodes()})
    h.db.execute('UPDATE research_canvases SET body_json=?, previous_json=? WHERE question_id=?',(legacy,legacy,qid))
    h.db.commit()
    result=canvas.read(h,qid)
    assert result['edges']==[]
    assert result['previous']['edges']==[]
    assert [n['label'] for n in result['previous']['nodes']]==['Filing','Target construction start']
    upgraded=canvas.save(h,qid,{'expected_revision':1,'nodes':nodes(),'edges':edges()})
    stored=json.loads(h.db.execute('SELECT body_json FROM research_canvases WHERE question_id=?',(qid,)).fetchone()[0])
    assert stored['edges']==[{'from_index':0,'to_index':1,'relation':'precedes','note':'Researcher expectation, not verified'}]
    assert upgraded['edges'][0]['relation_status']=='RESEARCHER_ASSERTED'


def test_edges_survive_restart_and_appear_in_previous(h):
    _,qid=capture(h)
    canvas.save(h,qid,{'expected_revision':0,'nodes':nodes(),'edges':edges()})
    changed=nodes();changed[1]['note']='Second draft'
    canvas.save(h,qid,{'expected_revision':1,'nodes':changed,'edges':[]})
    result=canvas.read(h,qid)
    assert result['edges']==[]
    assert result['previous']['edges']==[{'from_index':0,'to_index':1,'relation':'precedes',
                                          'note':'Researcher expectation, not verified'}]
    path=h.db.execute('PRAGMA database_list').fetchone()['file']
    other=Harness(path,h.data,adapter=StubAdapter())
    try:
        assert canvas.read(other,qid)['previous']['edges'][0]['relation']=='precedes'
    finally:other.db.close()
