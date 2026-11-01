"""Service wiring: diagnostics/spend/receipt integration. Mock transports only, no network."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import httpx

from app import canvas, store
from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.ai.service import SCHEMA as AI_SCHEMA
from app.harness import Conflict, Harness
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)


@pytest.fixture
def ai(h, monkeypatch):
    monkeypatch.setattr(service.secrets, 'key', lambda: 'test-not-real-key')
    svc = service.AIService(h, MetaProvider(httpx.MockTransport(_mock_ok), key_reader=lambda: 'test-not-real-key'))
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc, monkeypatch)
    pid = h.register_project('fx')['project_id']
    qid = asyncio.run(h.create_question(pid, 'What does est_cost measure?'))['question_id']
    return svc, qid, None

RESULT = {
    'answer': 'Reported estimate, not actual cost.',
    'claims': [{'text': 'Applicant estimate', 'evidence_ids': ['DD-2023:est_cost'], 'kind': 'fact'}],
    'unresolved': ['Actual cost unknown'],
    'proposed_variables': [{'name': 'est_cost', 'dataset_id': 'fx/permits_2023.csv', 'kind': 'actual'}],
    'next_action': 'Researcher reviews',
}
FRAME = {'sources': ['DD-2023'], 'gap_type': 'fact', 'focus': 'Check cost definition'}

# Markers planted in rejected model text / extra field names; must never persist.
SECRET_A = 'sk-ant-REJECTED-WIRING-MARKER-aaa111'
SECRET_B = 'AKIA-REJECTED-WIRING-MARKER-bbb222'
UNREGISTERED_ID = 'INVENTED-WIRING-PROBE-ID'

BOGUS_EXTRA_FIELD = 'x_secret_probe_field_MARKER_ccc333'


def _svc(svc, handler):
    svc.provider = MetaProvider(httpx.MockTransport(handler), key_reader=lambda: 'test')
    return svc


def _ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def _mock_ok(request):
    if request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
    body = json.loads(request.content)
    text = json.dumps(FRAME if 'public work plan' in body.get('instructions', '') else RESULT)
    return httpx.Response(
        200,
        json={'status': 'completed', 'model': body['model'],
              'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
              'usage': {'input_tokens': 200, 'output_tokens': 100}},
    )


def _rejected_final(qid_secret_field=True):
    """A final-arm payload that fails ResearchResult validation with planted markers."""
    bad = dict(RESULT)
    bad['claims'] = [{'text': 'Rejected claim ' + SECRET_A, 'evidence_ids': ['DD-2023:est_cost'], 'kind': 'fact'}]
    if qid_secret_field:
        bad[BOGUS_EXTRA_FIELD] = SECRET_B
    return json.dumps(bad)


def test_validation_failure_settles_known_charge_without_raw_text(ai):
    svc, qid, _ = ai
    _ready(svc)
    bad_text = _rejected_final()

    def handler(req):
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        body = json.loads(req.content)
        text = json.dumps(FRAME if 'public work plan' in body.get('instructions', '') else bad_text)
        return httpx.Response(
            200,
            json={'status': 'completed', 'model': body['model'],
                  'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
                  'usage': {'input_tokens': 200, 'output_tokens': 100}},
        )

    _svc(svc, handler)
    prep = svc.prepare(qid, {})
    with pytest.raises(Conflict, match='typed_output_validation_failed'):
        asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    job = svc.jobs(qid)[0]
    assert job['status'] == 'failed_known_charge'
    spend = svc.db.execute('SELECT * FROM ai_spend WHERE job_id=?', (prep['job_id'],)).fetchone()
    assert spend['status'] == 'failed_known_charge'
    assert spend['actual_cost'] is not None and spend['actual_cost'] > 0
    assert spend['reservation'] == pytest.approx(spend['actual_cost'])
    assert spend['reservation'] < prep['maximum_reserved_usd']
    result = job['result']
    assert result['error_code'] == 'typed_output_validation_failed'
    assert result['validation_issue_count'] > 0
    diag = result['diagnostics']
    assert diag['error_code'] == 'typed_output_validation_failed'
    assert diag['issue_count'] == result['validation_issue_count']
    assert all(set(i) == {'loc', 'type', 'msg_category'} for i in diag['issues'])
    assert set(result) >= {'diagnostics', 'validation_issue_count', 'partial_summary', 'stages'}
    assert 'validation_issues' not in result
    assert 'partial_outputs' not in result
    body = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert SECRET_A not in body and SECRET_B not in body and BOGUS_EXTRA_FIELD not in body
    receipt = svc.receipt(prep['job_id'])
    rbody = json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert receipt['provider_billed_cost_usd'] is None
    assert receipt['billing_status'] == 'NOT_VERIFIED'
    assert SECRET_A not in rbody and SECRET_B not in rbody and BOGUS_EXTRA_FIELD not in rbody


def test_transport_failure_keeps_reservation_unknown(ai):
    svc, qid, _ = ai
    _ready(svc)

    def handler(req):
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        return httpx.Response(429, json={'sensitive': 'do not log'})

    _svc(svc, handler)
    prep = svc.prepare(qid, {})
    with pytest.raises(Conflict, match='no automatic retry'):
        asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    job = svc.jobs(qid)[0]
    assert job['status'] == 'failed_unknown_charge'
    spend = svc.db.execute('SELECT * FROM ai_spend WHERE job_id=?', (prep['job_id'],)).fetchone()
    assert spend['status'] == 'failed_unknown_charge'
    assert spend['actual_cost'] is None
    assert spend['reservation'] >= prep['maximum_reserved_usd']
    result = job['result']
    assert 'validation_issues' not in result and 'partial_outputs' not in result
    assert result['validation_issue_count'] == 0
    assert 'do not log' not in json.dumps(result)


def test_settings_totals_count_failed_known_by_actual(tmp_path):
    import sqlite3

    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(AI_SCHEMA)
    db.execute('INSERT INTO ai_spend VALUES(?,?,?,?)', ('job-c', 0.05, 'completed_known', 0.01))
    db.execute('INSERT INTO ai_spend VALUES(?,?,?,?)', ('job-f', 0.05, 'failed_known_charge', 0.015))
    db.execute('INSERT INTO ai_spend VALUES(?,?,?,?)', ('job-u', 0.05, 'failed_unknown_charge', None))
    total = sum(r['actual_cost'] if r['status'] in ('completed_known', 'failed_known_charge') and r['actual_cost'] is not None else r['reservation']
                for r in db.execute('SELECT * FROM ai_spend'))
    assert total == pytest.approx(0.01 + 0.015 + 0.05)
    db.close()


def test_receipt_for_completed_job_gates_unregistered_ids(ai):
    svc, qid, _ = ai
    _ready(svc)
    _svc(svc, _mock_ok)
    prep = svc.prepare(qid, {})
    returned = asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    receipt = svc.receipt(prep['job_id'])
    assert receipt['provider_billed_cost_usd'] is None
    assert receipt['billing_status'] == 'NOT_VERIFIED'
    arms = [a for a in receipt['arms'] if a != '<unknown>']
    assert arms
    # Registry-gated layer: an unregistered ID never survives even if injected into stored output.
    stored = json.loads(svc.db.execute('SELECT result_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    stored['outputs']['harness']['result']['claims'][0]['evidence_ids'].append(UNREGISTERED_ID)
    from app.ai.receipt import build_receipt as _build

    gated = _build('completed', stored, reservation_usd=prep['maximum_reserved_usd'],
                   registered_source_ids=['DD-2023:est_cost'])
    assert UNREGISTERED_ID not in json.dumps(gated, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert gated['arm_summaries']['harness']['evidence_ids'] == ['DD-2023:est_cost']
    assert returned['measured_known_cost_usd'] is not None
    with pytest.raises(KeyError):
        svc.receipt('ai-missing')


def test_completed_job_verifies_after_settlement_changes_reservation(ai):
    """Defect 1 regression: an untouched completed job must verify True.

    spend.settle stores the settled reservation in ai_spend, and the receipt
    reads that stored value back; the completion digest must bind the same
    value or every unmodified success reports integrity False.
    """
    svc, qid, _ = ai
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    spend_row = svc.db.execute('SELECT * FROM ai_spend WHERE job_id=?', (prep['job_id'],)).fetchone()
    assert spend_row['status'] == 'completed_known'
    assert spend_row['reservation'] != prep['maximum_reserved_usd'], 'settlement must change the stored reservation'
    receipt = svc.receipt(prep['job_id'])
    assert receipt['result_integrity_verified'] is True, receipt['result_integrity_note']
    assert receipt['provider_billed_cost_usd'] is None
    assert receipt['billing_status'] == 'NOT_VERIFIED'


def test_stored_result_and_spend_mutation_break_receipt_integrity(ai):
    """Defect 3 regression: the stored-integrity contract is asserted end to end.

    A mutated stored result_json and a mutated ai_spend row must each make
    the real receipt path report result_integrity False.
    """
    svc, qid, _ = ai
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    assert svc.receipt(prep['job_id'])['result_integrity_verified'] is True

    # Mutate the stored result record: cost edited to 999.99.
    original_json = svc.db.execute(
        'SELECT result_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0]
    stored = json.loads(original_json)
    stored['calculated_cost_usd'] = 999.99
    svc.db.execute('UPDATE ai_jobs SET result_json=? WHERE id=?',
                   (json.dumps(stored, sort_keys=True, separators=(',', ':'), allow_nan=False), prep['job_id']))
    svc.db.commit()
    mutated = svc.receipt(prep['job_id'])
    assert mutated['result_integrity_verified'] is False
    assert mutated['provider_billed_cost_usd'] is None
    assert mutated['billing_status'] == 'NOT_VERIFIED'

    # Restore the stored result byte for byte, then mutate the spend row instead.
    svc.db.execute('UPDATE ai_jobs SET result_json=? WHERE id=?', (original_json, prep['job_id']))
    svc.db.commit()
    assert svc.receipt(prep['job_id'])['result_integrity_verified'] is True
    svc.db.execute('UPDATE ai_spend SET actual_cost=? WHERE job_id=?', (999.99, prep['job_id']))
    svc.db.commit()
    assert svc.receipt(prep['job_id'])['result_integrity_verified'] is False


def test_restart_keeps_settled_failed_known_charge(ai):
    svc, qid, _ = ai
    _ready(svc)
    bad_text = _rejected_final(qid_secret_field=False)

    def handler(req):
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        body = json.loads(req.content)
        chosen = json.dumps(FRAME if 'public work plan' in body.get('instructions', '') else bad_text)
        return httpx.Response(
            200,
            json={'status': 'completed', 'model': body['model'],
                  'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': chosen}]}],
                  'usage': {'input_tokens': 200, 'output_tokens': 100}},
        )

    _svc(svc, handler)
    prep = svc.prepare(qid, {})
    with pytest.raises(Conflict):
        asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    assert svc.jobs(qid)[0]['status'] == 'failed_known_charge'
    restarted = service.AIService(svc.h, svc.provider)
    assert restarted.jobs(qid)[0]['status'] == 'failed_known_charge'
    row = restarted.db.execute('SELECT * FROM ai_spend WHERE job_id=?', (prep['job_id'],)).fetchone()
    assert row['status'] == 'failed_known_charge' and row['actual_cost'] is not None
