"""Code-only Modal readiness and unconditional execution barriers; no live calls."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.ai import registry,service
from app.harness import Conflict
from test_dot_safety import h


@pytest.fixture
def svc(h,monkeypatch):
    monkeypatch.setattr(service.secrets,'key',lambda:'fake-meta')
    monkeypatch.setattr(service.secrets,'baseten_key',lambda:'fake-baseten')
    def forbidden():
        raise AssertionError('Transport must not be resolved')
    return service.AIService(h,provider_factory={
        'meta':forbidden,'modal':forbidden,'baseten':forbidden})


@pytest.mark.parametrize('configured',[False,True])
def test_registry_and_settings(svc,monkeypatch,configured):
    for name,value in [('WB_MODAL_BASE_URL','https://example.modal.run'),
                       ('WB_MODAL_PROXY_TOKEN_ID','private-id'),
                       ('WB_MODAL_PROXY_TOKEN_SECRET','private-secret')]:
        if configured:monkeypatch.setenv(name,value)
        else:monkeypatch.delenv(name,raising=False)
    r=registry.registry()
    m=next(m for m in r['models'] if m['id']==registry.MODAL_MODEL_ID)
    assert m['kind']=='cloud_candidate' and m['endpoint'] is None
    assert m['input_price'] is None and m['output_price'] is None
    assert m['modalities']==('text',)
    # claim_check's task value is a Baseten id (Meta credits exhausted), so it
    # is the one value in this set that is not a Meta slug.
    assert set(r['tasks'].values())=={
        'muse-wonder-1.0','muse-image-1.0',
        'muse-voice-transcribe-1.0','sam-3.1',registry.BASETEN_MODEL_ID}
    p=next(p for p in r['providers'] if p['id']=='modal')
    assert p['model_id']==registry.MODAL_MODEL_ID
    assert p['billing']=='gpu_time' and not p['execution_enabled']
    settings=svc.settings()
    reasons=['deployment_runtime_not_verified','gpu_accounting_missing',
             'live_execution_approval_missing']
    if not configured:reasons=['endpoint_undeployed_or_unconfigured',
                               'proxy_auth_unconfigured']+reasons
    expected={'model_id':registry.MODAL_MODEL_ID,'provider':'modal',
              'base_url':'https://example.modal.run' if configured else None,
              'endpoint_configured':configured,'proxy_auth_configured':configured,
              'transport':'openai_chat_completions','billing':'gpu_time',
              'execution_enabled':False,'status':'blocked','blocking_reasons':reasons}
    assert settings['modal_connection']==expected
    assert {key:p[key] for key in expected}==expected
    assert not settings['cloud_enabled']
    assert 'private-secret' not in json.dumps([settings,r])
    assert 'private-id' not in json.dumps([settings,r])
    assert svc._provider_key('modal') is None
    assert not settings['provider_catalogs']['modal']['catalog_valid_for_current_key']


@pytest.mark.parametrize('provider,model',[
    ('modal','auto'),('modal','muse-spark-1.3'),
    ('meta',registry.MODAL_MODEL_ID),('baseten',registry.MODAL_MODEL_ID),
    (None,registry.MODAL_MODEL_ID),('modal',registry.MODAL_MODEL_ID)])
def test_prepare_no_writes(svc,provider,model):
    with pytest.raises(Conflict,match='GPU-time accounting'):
        svc.prepare('nonexistent',{'provider':provider,'model':model})
    assert svc.db.execute('SELECT count(*) FROM ai_jobs').fetchone()[0]==0
    assert svc.db.execute('SELECT count(*) FROM ai_spend').fetchone()[0]==0


def test_verify_never_probes_or_records(svc):
    with pytest.raises(Conflict,match='may start GPU billing'):
        asyncio.run(svc.verify_account('modal'))
    assert svc.db.execute('SELECT count(*) FROM ai_provider_catalog').fetchone()[0]==0


@pytest.mark.parametrize('record',[
    {'provider':'modal','model':'muse-spark-1.3'},
    {'model':registry.MODAL_MODEL_ID},
    {'model':'muse-spark-1.3','request':{'provider':'modal'}},
    {'model':'muse-spark-1.3','request':{'model':registry.MODAL_MODEL_ID}},
])
def test_execute_forged_and_legacy_records(svc,record,monkeypatch):
    monkeypatch.setenv('WB_MODAL_BASE_URL','https://example.modal.run')
    svc.db.execute("""INSERT INTO ai_jobs
        (id,question_id,status,request_json,snapshot,preview_hash,reservation,created_at)
        VALUES ('forged','q','prepared',?,'s','hash',1,?)""",
        (json.dumps(record),time.time()))
    svc.db.commit()
    with pytest.raises(Conflict,match='GPU-time accounting'):
        asyncio.run(svc.execute('forged','hash',True))
    assert svc.db.execute("SELECT status FROM ai_jobs WHERE id='forged'").fetchone()[0]=='prepared'
    assert svc.db.execute('SELECT count(*) FROM ai_spend').fetchone()[0]==0


@pytest.mark.parametrize('raw',[
    '{', '', 'null', '[]', '[1]', '"text"', 'true', '1',
    *[json.dumps({'model':'muse-spark-1.3','request':value})
      for value in (None,[],[1],'','text',False,True,0,1)],
])
@pytest.mark.parametrize('status,consent',[
    ('prepared',True),('prepared',False),('completed',True),
])
def test_execute_malformed_record_is_controlled_and_immutable(svc,raw,status,consent):
    svc.db.execute("""INSERT INTO ai_jobs
        (id,question_id,status,request_json,snapshot,preview_hash,reservation,created_at)
        VALUES ('malformed','q',?,?,'s','hash',1,?)""",
        (status,raw,time.time()))
    svc.db.commit()
    before=dict(svc.db.execute("SELECT * FROM ai_jobs WHERE id='malformed'").fetchone())
    with pytest.raises(Conflict,match='Stored AI request'):
        asyncio.run(svc.execute('malformed','hash',consent))
    assert dict(svc.db.execute("SELECT * FROM ai_jobs WHERE id='malformed'").fetchone())==before
    assert svc.db.execute('SELECT count(*) FROM ai_jobs').fetchone()[0]==1
    assert svc.db.execute('SELECT count(*) FROM ai_spend').fetchone()[0]==0
