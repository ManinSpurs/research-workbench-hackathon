"""Scripted domain boundary checks, not model quality tests."""
import asyncio
import json
import pytest
from pydantic import ValidationError
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import packs
from app.harness import Conflict, Harness
from app.packs import DomainPack, get_pack, pack_scope, instructions_for
from app.results import VariableRef
from test_dot_safety import h, StubAdapter


def test_pack_cannot_expand_permissions():
    manifest=get_pack().model_dump()
    for key in ('budget','network_access','python_module'):
        with pytest.raises(ValidationError): DomainPack(**{**manifest,key:'override'})
    with pytest.raises(ValidationError): DomainPack(**{**manifest,'allowed_tools':('shell_exec',)})


def test_prompt_composition_and_context_cleanup():
    for name in packs.PACKS:
        with pack_scope(get_pack(name)):
            prompt=instructions_for('STRICT STAGE SCHEMA')
            assert prompt.startswith(packs.CORE_POLICY)
            assert 'STRICT STAGE SCHEMA' in prompt and get_pack(name).instructions in prompt
    assert 'TRUSTED DOMAIN GUIDANCE' not in instructions_for('stage')


@pytest.mark.parametrize('pack_id,capability',[('missing','mock_text'),('social_science','meta_text'),('social_science','transcription')])
def test_invalid_selection_before_calls_and_budget(h,pack_id,capability):
    async def run():
        pid=h.register_project('fx')['project_id']
        with pytest.raises(Conflict): await h.create_question(pid,'What is cost?',pack_id=pack_id,capability_id=capability)
        assert not h.adapter.calls
        assert h.db.execute('SELECT count(*) FROM question_budgets').fetchone()[0]==0
    asyncio.run(run())


def test_memory_and_briefing_are_pack_scoped(h):
    async def run():
        pid=h.register_project('fx')['project_id']
        first=await h.create_question(pid,'Social inquiry about cost')
        second=await h.create_question(pid,'Engineering inquiry about measurement',pack_id='engineering_measurement')
        private=await h.create_question(pid,'Private measurement inquiry',memory='session_only',pack_id='engineering_measurement')
        calls=list(h.adapter.calls)
        for name,q in [('social_science',first),('engineering_measurement',second)]:
            assert [r['id'] for r in h.pack_memory(pid,name)['questions']]==[q['question_id']]
            b=h.briefing(pid,name)
            assert b['context']['pack_id']==name and b['model_calls']==0
            assert ('Social inquiry' in json.dumps(b))==(name=='social_science')
            assert private['question_id'] not in json.dumps(b)
        assert h.adapter.calls==calls
        assert h.question_context(first['question_id'])['pack_id']=='social_science'
    asyncio.run(run())


def test_manifest_drift_blocks_call_without_budget_reset(h,monkeypatch):
    async def run():
        pid=h.register_project('fx')['project_id']
        q=await h.create_question(pid,'What is cost?')
        before=dict(h.db.execute('SELECT * FROM question_budgets').fetchone())
        original=get_pack()
        monkeypatch.setitem(packs.PACKS,original.pack_id,original.model_copy(update={'instructions':'Changed interpretation'}))
        with pytest.raises(Conflict,match='pack changed'): await h.run(q['question_id'])
        assert h.adapter.calls==['refine']
        assert dict(h.db.execute('SELECT * FROM question_budgets').fetchone())==before
    asyncio.run(run())


def test_restart_and_delete_pack_pin(h):
    async def run():
        pid=h.register_project('fx')['project_id']
        q=await h.create_question(pid,'Distinct measurement question for restart',pack_id='engineering_measurement')
        pin=h.question_context(q['question_id'])
        path=h.db.execute('PRAGMA database_list').fetchone()['file']
        other=Harness(path,h.data,adapter=StubAdapter())
        try:
            assert other.question_context(q['question_id'])==pin and not other.adapter.calls
            other.delete_question(q['question_id'])
            assert other.db.execute('SELECT count(*) FROM question_contexts').fetchone()[0]==0
        finally: other.db.close()
    asyncio.run(run())


def test_engineering_upload_reuses_core_without_invented_unit(h):
    async def run():
        dictionary={'entries':[{'source_id':'ENG-TEST','evidence_id':'ENG-UNIT','field':'reading','definition':'Unit and calibration are not documented. Test condition is room temperature.','location':'fixture dictionary:reading'}]}
        pid=h.register_upload('Engineering measurement fixture','reading\n12\n13\n',dictionary)['project_id']
        dataset=next(iter(h.dataset_columns(h.project(pid)['kind'])))
        original=h.adapter.first_step
        async def proposal(*args):
            step,history,usage=await original(*args)
            step.design.variables=[VariableRef(name='reading',dataset_id=dataset,kind='actual',role='measurement')]
            return step,history,usage
        h.adapter.first_step=proposal
        q=await h.create_question(pid,'What unit is reading measured in?',pack_id='engineering_measurement')
        result=await h.run(q['question_id'])
        assert result['result_kind']=='ClarificationNeeded'
        assert 'not documented' in json.dumps(h.input_packet(pid,[dataset]))
        assert h.pending_clarifications(pid,'social_science')['clarifications']==[]
        assert len(h.pending_clarifications(pid,'engineering_measurement')['clarifications'])==1
    asyncio.run(run())


def test_failed_refinement_cleans_pin(h):
    async def run():
        pid=h.register_project('fx')['project_id']
        async def fail(*args): raise RuntimeError('scripted failure')
        h.adapter.refine=fail
        with pytest.raises(RuntimeError): await h.create_question(pid,'Question that fails')
        assert h.db.execute('SELECT count(*) FROM question_contexts').fetchone()[0]==0
    asyncio.run(run())


def test_non_text_capabilities_rejected():
    for item in packs.CAPABILITIES:
        if item['kind']!='text_reasoning':
            with pytest.raises(ValueError): packs.require_text_binding(item['id'])


def test_task_local_pack_context_is_not_shared():
    async def run():
        entered=asyncio.Event()
        release=asyncio.Event()
        async def first():
            with pack_scope(get_pack('engineering_measurement')):
                entered.set()
                await release.wait()
                assert 'Engineering: Measurement' in instructions_for('stage')
        async def second():
            await entered.wait()
            with pack_scope(get_pack('social_science')):
                assert 'Engineering: Measurement' not in instructions_for('stage')
            release.set()
        await asyncio.gather(first(),second())
    asyncio.run(run())


def test_live_adapter_execution_blocked_even_after_construction():
    from app.adapters import ModelAdapter
    adapter=object.__new__(ModelAdapter)
    adapter.label='muse'
    async def run():
        with pytest.raises(RuntimeError,match='Live model execution'):
            await adapter._run(None,'must not reach network')
    asyncio.run(run())


def test_pack_can_tighten_tool_access(h,monkeypatch):
    async def run():
        original=get_pack()
        monkeypatch.setitem(packs.PACKS,original.pack_id,original.model_copy(update={'allowed_tools':()}))
        pid=h.register_project('fx')['project_id']
        q=await h.create_question(pid,'What is cost?')
        before=h.db.execute('SELECT tool_calls FROM question_budgets').fetchone()[0]
        with pytest.raises(Conflict,match='does not permit'): h._reserve_tools(q['question_id'],1)
        assert h.db.execute('SELECT tool_calls FROM question_budgets').fetchone()[0]==before
    asyncio.run(run())


def test_comparison_binds_same_pack_to_both_arms(monkeypatch,tmp_path):
    from app import compare
    seen=[]
    class Recorder(StubAdapter):
        async def first_step(self,*args):
            seen.append(('harness',instructions_for('stage')))
            return await super().first_step(*args)
        async def basic(self,*args):
            seen.append(('basic',instructions_for('stage')))
            return await super().basic(*args)
    # A binding test against a scripted fixture, not an engineering quality case.
    monkeypatch.setattr(compare,'CASES',[{'id':'PACK-BINDING','kind':'fx','q':'What does cost mean?',
        'pack_id':'engineering_measurement','expect':'supported'}])
    result=asyncio.run(compare.run_all(str(tmp_path/'paired.db'),adapter_factory=Recorder))
    row=result['cases'][0]
    assert row['process_checks']['matched_pack_context']
    assert row['arms']['basic']['context']['pack_id']=='engineering_measurement'
    assert {arm for arm,_ in seen}=={'basic','harness'}
    assert all('Engineering: Measurement' in prompt for _,prompt in seen)
    assert result['accuracy'] is None and result['quality_evaluation']=='NOT_RUN'
