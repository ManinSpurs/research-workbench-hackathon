"""Wonder harness arm B2 — standalone restricted-shape pair (mock transports only).

LEAD DECISION (2026-10-27, overnight autonomous authority; user direction:
"Spark and Wonder each also need their own basic-vs-harness arms"; pending
user morning ratification):

1. B2 exists ONLY as a standalone restricted-shape pair: Wonder task with
   `second_look_of is None` may opt into harness/compare using EXACTLY the
   same restricted subset payload as B1 (claims/evidence IDs/packet slice
   only — never full snapshot, never assets). B2 scoring counts
   within-restricted-shape only (B1 vs B2), never toward Spark-vs-Wonder
   cross-shape deltas.
2. Linked second look (`second_look_of` set) NEVER gets harness/compare: it
   stays forced basic, exactly one call, byte-identical behaviour to today.
   This is permanent for hackathon scope, not a stub to enable later.
3. request_json whole-record immutability, restricted-subset-only
   transmission, and all other second-look invariants are unchanged. No
   widening on any arm.

Behaviour matrix proved here (prepare -> execute, mock transports):

                    prepare       execute       recorded arms
  standalone basic      1              1        ['basic'] (also the default when
                                                 neither harness nor compare
                                                 is explicitly sent: B2 opt-in
                                                 is explicit, so an unflagged
                                                 standalone prepare keeps the
                                                 pre-B2 single call)
  standalone harness    2              2        frame -> ['harness']
  standalone compare    3              3        execution_order
                                                 (basic+harness)
  linked (any flags)    1              1        ['basic'] (forced off)

Mock transports only; no paid/live calls, no key reads.
Live execution: NOT_RUN.
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

FRAME = {'sources': ['DD-2023'], 'gap_type': 'fact', 'focus': 'Check cost definition'}
RESULT = {
    'answer': 'Reported estimate, not actual cost.',
    'claims': [{'text': 'Applicant estimate', 'evidence_ids': ['DD-2023:est_cost'],
                'kind': 'fact'}],
    'unresolved': ['Actual cost unknown'],
    'proposed_variables': [{'name': 'est_cost',
                            'dataset_id': 'fx/permits_2023.csv', 'kind': 'actual'}],
    'next_action': 'Researcher reviews',
}


def _mock_models(request):
    if request.url.path.endswith('/models'):
        return httpx.Response(
            200,
            json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
    return httpx.Response(200, json={'status': 'completed', 'model': 'muse-wonder-1.0',
                                     'output': [], 'usage': {}})


@pytest.fixture
def svc(h, monkeypatch):
    s = service.AIService(
        h, MetaProvider(httpx.MockTransport(_mock_models), key_reader=lambda: 'test-not-real-key'),
        meta_available=True)
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(s, monkeypatch)
    pid = h.register_project('fx')['project_id']
    qid = asyncio.run(h.create_question(pid, 'What does est_cost measure?'))['question_id']
    return s, qid


def _seed_prior(s, qid, prior_id='ai-prior-b2'):
    claims = [{'text': 'Applicant estimate', 'evidence_ids': ['DD-2023:est_cost'],
               'kind': 'fact'}]
    arms = {'basic': {'result': {'answer': 'Reported estimate, not actual cost.',
                                 'claims': claims, 'unresolved': ['Actual cost unknown'],
                                 'proposed_variables': [], 'next_action': 'Researcher reviews'},
                      'grounding': {'structural_valid': True}}}
    result = {'outputs': {**arms,
                          'evaluation': {'kind': 'single', 'accuracy': 'NOT_SCORED',
                                         'model': 'muse-spark-1.3',
                                         'execution_order': ['basic']}},
              'stages': [], 'usage_complete': True, 'calculated_cost_usd': 0.0,
              'measured_known_cost_usd': 0.0, 'pricing_date': '2026-10-26',
              'provider_billed_cost_usd': None, 'billing_status': 'NOT_VERIFIED'}
    packet = s._snapshot(qid)['packet']
    record = {'execution_order': ['basic'],
              'request': {'task': 'research_query', 'model': 'auto', 'mode': 'cloud',
                          'text': '', 'assets': [], 'harness': False, 'compare': False,
                          'max_output_tokens': 2048, 'max_cost_usd': 5.0,
                          'second_look_of': None},
              'model': 'muse-spark-1.3', 'snapshot': {'packet': packet},
              'assets': [], 'calls': 1, 'reserve': 0.0, 'contract_sha256': 'seed',
              'model_contract': {}}
    s.db.execute('INSERT INTO ai_jobs(id,question_id,status,request_json,snapshot,preview_hash,reservation,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                 (prior_id, qid, 'completed', json.dumps(record),
                  service.digest(packet), service.digest(record), 0.0, json.dumps(result), 0.0))
    s.db.execute("INSERT INTO ai_spend VALUES(?,?,'completed_known',?)", (prior_id, 0.0, 0.0))
    s.db.commit()
    return prior_id


def _ready(s, monkeypatch, handler_impl):
    """Ready the service with a mock transport; handler_impl(body) -> response json."""
    monkeypatch.setattr(service.secrets, 'key', lambda: 'test-not-real-key')
    bodies = []

    def handler(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS
                                                      if registry.MODELS[m].endpoint]})
        body = json.loads(request.content)
        bodies.append(body)
        return handler_impl(body)

    s.provider = MetaProvider(httpx.MockTransport(handler),
                              key_reader=lambda: 'test-not-real-key')
    s.configure(True, 150)
    asyncio.run(s.verify_account())
    return bodies


def _success(body, frame=None, result=None):
    frame = FRAME if frame is None else frame
    result = RESULT if result is None else result
    text = json.dumps(frame if 'public work plan' in body.get('instructions', '') else result)
    return httpx.Response(200, json={
        'status': 'completed', 'model': body['model'],
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
        'usage': {'input_tokens': 200, 'output_tokens': 100}})


def _ctx(body):
    return json.loads(body['input'][0]['content'][0]['text'])


# --- Prepare: standalone B2 opts in; linked stays forced basic ---

@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
@pytest.mark.parametrize('kwargs,calls', [
    ({}, 1),                          # no explicit opt-in -> pre-B2 single basic
    ({'harness': True}, 2),
    ({'harness': False}, 1),
    ({'harness': False, 'compare': True}, 3),
    ({'harness': True, 'compare': True}, 3),
])
def test_standalone_prepare_opt_in(svc, task, kwargs, calls):
    s, qid = svc
    prep = s.prepare(qid, {'task': task, 'max_cost_usd': 50, **kwargs})
    assert prep['model'] == 'muse-wonder-1.0'
    assert prep['max_inference_calls'] == calls
    # Reservation honesty: existing per-call formula, n = calls (Wonder rates).
    m = registry.MODELS['muse-wonder-1.0']
    per_call = (m.context * m.input_price + 2048 * m.output_price) / 1e6
    assert prep['maximum_reserved_usd'] == pytest.approx(round(per_call * calls, 8))
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    assert record['calls'] == calls
    assert record['request']['compare'] is bool(kwargs.get('compare'))
    if not kwargs.get('compare'):
        assert record['request']['harness'] is bool(kwargs.get('harness', False))
    # Restricted shape on every arm: no question, no canvas/decisions, no assets.
    assert record['snapshot']['question'] is None
    assert record['snapshot']['canvas'] == {'revision': 0, 'nodes': []}
    assert record['snapshot']['decisions'] == []
    assert record['assets'] == []
    assert record['snapshot']['claims'] == [] and record['snapshot']['evidence_ids'] == []
    tx = prep['transmission']
    assert tx['claims'] == [] and tx['evidence_ids'] == []
    assert tx['source_job_id'] is None
    assert tx['whole_csv_rows'] is False
    assert tx['includes_canvas_notes_and_question_decisions'] is False
    assert {'text_and_context', 'task_text', 'files'} & set(tx) == set()
    # Frame instruction surfaces in the preview exactly when a frame call exists.
    assert (prep['instruction_contract']['frame'] is None) == (calls == 1)


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_linked_prepare_forced_basic(svc, task):
    """Linked: harness/compare requested -> normalised off, exactly one call,
    recorded flags False, reservation for n=1 (stronger than the pre-B2 pin,
    which asserted only the call count)."""
    s, qid = svc
    prior_id = _seed_prior(s, qid)
    prep = s.prepare(qid, {'task': task, 'harness': True, 'compare': True,
                           'max_cost_usd': 50, 'second_look_of': prior_id})
    assert prep['max_inference_calls'] == 1
    m = registry.MODELS['muse-wonder-1.0']
    per_call = (m.context * m.input_price + 2048 * m.output_price) / 1e6
    assert prep['maximum_reserved_usd'] == pytest.approx(round(per_call, 8))
    assert prep['instruction_contract']['frame'] is None
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    assert record['request']['harness'] is False
    assert record['request']['compare'] is False
    assert record['calls'] == 1
    assert record['second_look_of'] == prior_id


# --- Execute: call counts, arms, restricted transmission ---

@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
@pytest.mark.parametrize('kwargs', [{}, {'harness': False}])
def test_standalone_basic_executes_one_call(svc, monkeypatch, task, kwargs):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'max_cost_usd': 50, **kwargs})
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert list(result['outputs']) == ['basic', 'evaluation'] or set(result['outputs']) == {'basic', 'evaluation'}
    assert result['outputs']['evaluation']['kind'] == 'single'
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert len(bodies) == 1
    assert 'public work plan' not in bodies[0].get('instructions', '')


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_standalone_harness_executes_frame_then_final(svc, monkeypatch, task):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'harness': True, 'max_cost_usd': 50})
    assert prep['max_inference_calls'] == 2
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['frame', 'harness']
    assert list(result['outputs']['harness']['result']['claims'])
    assert result['outputs']['evaluation']['execution_order'] == ['harness']
    assert len(bodies) == 2
    # Frame stage uses the frame contract/schema; final carries stage_context.
    assert 'public work plan' in bodies[0].get('instructions', '')
    assert 'public work plan' not in bodies[1].get('instructions', '')
    frame_ctx = _ctx(bodies[0])
    final_ctx = _ctx(bodies[1])
    # Same restricted snapshot in both calls; stage_context is the only delta.
    assert frame_ctx['context'] == final_ctx['context']
    assert frame_ctx['context']['question'] is None
    assert frame_ctx['context']['canvas'] == {'revision': 0, 'nodes': []}
    stage_context = final_ctx['stage_context']
    assert stage_context['bounded_plan']['sources'] == ['DD-2023']
    observations = stage_context['observations']
    # Observations are drawn from the RESTRICTED slice's packet sources, not
    # the full packet: the standalone slice's source list is empty (empty
    # claim set, same slice shape as B1), so no observation leaks in. The
    # frame enum itself is bound to the slice's allowed_sources.
    assert observations == []
    assert bodies[0]['text']['format']['schema']['properties']['sources']['items']['enum'] == \
        frame_ctx['context']['packet']['allowed_sources']
    assert frame_ctx['stage_context'] is None


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_standalone_compare_executes_paired_in_recorded_order(svc, monkeypatch, task):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'compare': True, 'max_cost_usd': 50})
    assert prep['max_inference_calls'] == 3
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    order = record['execution_order']
    assert sorted(order) == ['basic', 'harness']
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert result['outputs']['evaluation']['kind'] == 'paired'
    assert result['outputs']['evaluation']['execution_order'] == order
    expected_stages = []
    for arm in order:
        expected_stages += (['frame', 'harness'] if arm == 'harness' else ['basic'])
    assert [st['stage'] for st in result['stages']] == expected_stages
    assert len(bodies) == 3
    assert set(result['outputs']) == {'basic', 'harness', 'evaluation'}


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_linked_execute_forced_basic_one_call(svc, monkeypatch, task):
    """Linked with harness/compare requested at prepare: normalised off, one
    basic call, no frame stage — byte-identical to pre-B2 linked behaviour."""
    s, qid = svc
    prior_id = _seed_prior(s, qid)
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'harness': True, 'compare': True,
                           'max_cost_usd': 50, 'second_look_of': prior_id})
    assert prep['max_inference_calls'] == 1
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert len(bodies) == 1
    # Restricted subset transmitted: prior claims in, question/canvas absent.
    sent = json.dumps(bodies, sort_keys=True)
    assert 'Applicant estimate' in sent
    ctx = _ctx(bodies[0])['context']
    assert ctx['question'] is None


# --- Fail closed: no widening on any arm ---

def test_standalone_harness_rejects_assets(svc):
    s, qid = svc
    asset = s.register_asset(qid, {'name': 'note.txt', 'mime': 'text/plain',
                                   'encoded': __import__('base64').b64encode(b'local note').decode()})
    jobs_before = s.db.execute('SELECT count(*) FROM ai_jobs').fetchone()[0]
    with pytest.raises(Conflict, match='no files|restricted subset'):
        s.prepare(qid, {'task': 'methodology_review', 'harness': True,
                        'assets': [asset['id']], 'max_cost_usd': 50})
    assert s.db.execute('SELECT count(*) FROM ai_jobs').fetchone()[0] == jobs_before


def test_standalone_harness_transmits_no_full_snapshot_markers(svc, monkeypatch):
    """Markers in the question text / canvas / decisions never reach any B2
    frame or final body, and never enter the stored record."""
    from app import canvas as canvas_mod
    s, qid = svc
    pid = s.h.question(qid)['project_id']
    marked_qid = asyncio.run(s.h.create_question(
        pid, 'What does est_cost measure? MARKER-B2-QUESTION'))['question_id']
    c = canvas_mod.read(s.h, marked_qid)
    canvas_mod.save(s.h, marked_qid, {
        'expected_revision': 0,
        'nodes': [{**{k: n[k] for k in ('label', 'dataset_id', 'variable', 'note') if k in n},
                   'label': 'MARKER-B2-CANVAS', 'note': 'MARKER-B2-NOTE'} for n in c['nodes']]})
    s.h.decide(pid, marked_qid, 'defer', 'MARKER-B2-DECISION', None)
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(marked_qid, {'task': 'methodology_review', 'compare': True,
                                  'max_cost_usd': 50, 'text': 'Standalone B2 critique.'})
    asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert len(bodies) == 3
    sent = json.dumps(bodies, sort_keys=True)
    for marker in ('MARKER-B2-QUESTION', 'MARKER-B2-CANVAS', 'MARKER-B2-NOTE', 'MARKER-B2-DECISION'):
        assert marker not in sent
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    stored = json.dumps(record, sort_keys=True)
    for marker in ('MARKER-B2-QUESTION', 'MARKER-B2-CANVAS', 'MARKER-B2-NOTE', 'MARKER-B2-DECISION'):
        assert marker not in stored


def test_standalone_frame_source_outside_allowed_fails(svc, monkeypatch):
    """Frame planner returning a source outside allowed_sources fails closed
    (existing Frame subset check, unchanged on the B2 path)."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(
        b, frame={'sources': ['OFF-BLOCKED'], 'gap_type': 'fact', 'focus': 'Widen'}))
    prep = s.prepare(qid, {'task': 'methodology_review', 'harness': True, 'max_cost_usd': 50})
    with pytest.raises(Conflict, match='typed_output_validation_failed|scope_or_validation_failure'):
        asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert len(bodies) == 1
    assert s.db.execute('SELECT status FROM ai_jobs WHERE id=?',
                        (prep['job_id'],)).fetchone()[0] != 'completed'


def test_stored_standalone_flag_tamper_fails_closed(svc, monkeypatch):
    """Whole-record immutability on the B2 arm: flipping stored harness off
    (without a hash rewrite) fails closed before any provider call."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'methodology_review', 'harness': True, 'max_cost_usd': 50})
    row = s.db.execute('SELECT request_json FROM ai_jobs WHERE id=?',
                       (prep['job_id'],)).fetchone()
    record = json.loads(row[0])
    record['request']['harness'] = False
    s.db.execute('UPDATE ai_jobs SET request_json=? WHERE id=?',
                 (json.dumps(record), prep['job_id']))
    s.db.commit()
    with pytest.raises(Conflict, match='Stored record changed'):
        asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert bodies == []
    assert s.db.execute('SELECT status FROM ai_jobs WHERE id=?',
                        (prep['job_id'],)).fetchone()[0] == 'prepared'


def test_stored_linked_flag_flip_fails_closed(svc, monkeypatch):
    """Linked invariant at execute: flipping stored harness/compare on (without
    a hash rewrite) is caught by the link-aware re-assertion, not widened."""
    s, qid = svc
    prior_id = _seed_prior(s, qid)
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'methodology_review', 'harness': False, 'compare': False,
                           'max_cost_usd': 50, 'second_look_of': prior_id})
    row = s.db.execute('SELECT request_json FROM ai_jobs WHERE id=?',
                       (prep['job_id'],)).fetchone()
    record = json.loads(row[0])
    record['request']['harness'] = True
    record['request']['compare'] = True
    s.db.execute('UPDATE ai_jobs SET request_json=? WHERE id=?',
                 (json.dumps(record), prep['job_id']))
    s.db.commit()
    with pytest.raises(Conflict, match='Stored'):
        asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert bodies == []
    assert s.db.execute('SELECT status FROM ai_jobs WHERE id=?',
                        (prep['job_id'],)).fetchone()[0] == 'prepared'


# --- Single-key standalone shapes: each flag defaults off unless explicitly passed ---
#
# Regression for the standalone single-key default leak: Request defaults
# harness=True for ordinary tasks, so a standalone second-look prepare
# carrying only {'compare': False} used to keep harness=True (2 calls for a
# researcher who never opted into harness). Each flag now defaults off unless
# explicitly passed. Prepare is local: zero provider calls in every case.

@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
@pytest.mark.parametrize('kwargs,calls', [
    ({'compare': False}, 1),   # compare-only False must not silently arm harness
    ({'harness': False}, 1),   # harness-only False stays single basic
    ({'compare': True}, 3),    # compare-only True keeps paired machinery
    ({'harness': True}, 2),    # harness-only True keeps frame+final
])
def test_standalone_single_key_defaults_off(svc, monkeypatch, task, kwargs, calls):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'max_cost_usd': 50, **kwargs})
    assert bodies == []
    assert prep['max_inference_calls'] == calls
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    assert record['calls'] == calls
    assert record['request']['compare'] is bool(kwargs.get('compare', False))
    assert record['request']['harness'] is bool(kwargs.get('harness', False))
    if calls == 1:
        assert prep['instruction_contract']['frame'] is None
    else:
        assert prep['instruction_contract']['frame'] is not None
    if kwargs.get('compare') is True:
        # Paired machinery intact without an explicit harness key: both arms
        # recorded in execution_order, exactly as the both-flags shape models.
        assert sorted(record['execution_order']) == ['basic', 'harness']


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_standalone_compare_false_only_executes_single_basic(svc, monkeypatch, task):
    """The leaky shape end to end: only {'compare': False} prepares 1 call
    with zero provider traffic, then executes one basic call."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'compare': False, 'max_cost_usd': 50})
    assert prep['max_inference_calls'] == 1
    assert bodies == []
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert len(bodies) == 1
