"""Model x harness labels contract: recorded arm labels vs design display labels.

Authoritative contracts (read-only; this file changes no src, no docs):

- ``app/ai/evaluation_runner.py``: ``_extract_arms`` records exactly the
  ``outputs`` keys except ``evaluation``; today those keys are ``basic`` /
  ``harness``. ``build_scoring_packet`` blind-codes every (job, arm) pair and
  seals the true ``(job_index, arm_label)`` in ``unblinding_key``.
- ``app/ai/service.py``: ``execute()`` derives ``arms`` from the verified
  record (``['basic','harness'] if compare else ['harness' if harness else
  'basic']``, overridden by ``record['execution_order']`` when paired; a
  linked second look is forced ``['basic']`` — LEAD DECISION 2026-10-27,
  permanent for hackathon scope).
- ``current/HARNESS_COMPARISON_PREREG_EN.md``: display arm ids A1..C2
  (``spark-basic``, ``spark-harness``, ``wonder-basic``, ``wonder-harness``,
  ``local4b-basic``, ``local4b-harness``) plus reference-only
  ``wonder-linked-basic`` — manifest-level labels, NOT recorded keys.
- ``current/MODEL_HARNESS_COVERAGE_EN.md`` Sec. 5 Step 1 / Step 3: the Step-1
  display-name annotation (``arm_display_names``) is not implemented, and
  Step 3 allows drafting matrix assertions against current code with
  ``xfail`` for design-reserved-but-unimplemented pieces.

FINDING pinned here (reported, not papered over): code labels and prereg arm
ids disagree by design. The runner records ``basic`` / ``harness`` plus
``run_params.model``; the A1..C2 display ids live only in the manifest and
the unblinding interpretation, never in ``outputs``, ``evaluation``, or any
scoring sheet. ``build_scoring_packet`` has no display-name parameter today.

Mock transports only; no paid/live calls, no key reads.
Live execution: NOT_RUN.
"""
import asyncio
import inspect
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import registry, service  # noqa: E402
from app.ai.evaluation_runner import (  # noqa: E402
    ALLOWED_HUMAN_CITATION_MARKS,
    FIXTURE_MISSING,
    NOT_SCORED,
    PENDING_HUMAN,
    build_scoring_packet,
)
from app.ai.provider import MetaProvider  # noqa: E402
from app.harness import Conflict  # noqa: E402
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

# Prereg display ids (HARNESS_COMPARISON_PREREG_EN.md Sec. 1): manifest-level
# labels only. None of these may ever appear as a recorded arm key.
DISPLAY_IDS = ('spark-basic', 'spark-harness', 'wonder-basic', 'wonder-harness',
               'local4b-basic', 'local4b-harness', 'wonder-linked-basic',
               'wonder-linked-harness')


# ---------------------------------------------------------------------------
# Synthetic recorded-job helpers (offline runner section; no provider needed)
# ---------------------------------------------------------------------------

def _arm(claims, structural_valid=True):
    cited = sum(len(c['evidence_ids']) for c in claims)
    return {
        'result': {'claims': claims},
        'grounding': {
            'structural_valid': structural_valid,
            'errors': [],
            'citation_count': cited,
            'invalid_citation_count': 0,
            'citation_error_rate': 0.0 if cited else None,
            'linked_claim_fraction': 1.0 if claims else None,
            'semantic_correctness': NOT_SCORED,
            'false_citation_rate': 'HUMAN_REVIEW_REQUIRED',
        },
    }


def _job(model, arms, order=None, calls=None):
    outputs = dict(arms)
    outputs['evaluation'] = {
        'kind': 'paired' if len(arms) > 1 else 'single',
        'accuracy': NOT_SCORED,
        'model': model,
        'execution_order': list(order) if order else list(arms),
    }
    return {
        'outputs': outputs,
        'model': model,
        'execution_order': list(order) if order else list(arms),
        'stages': [{'stage': a, 'elapsed_seconds': 1.0} for a in arms],
        'measured_known_cost_usd': 0.01,
        'elapsed_seconds': 5.0,
        **({'calls': calls} if calls is not None else {}),
    }


def _claim():
    return [{'text': 'Applicant estimate', 'evidence_ids': ['DD-2023:est_cost'],
             'kind': 'fact'}]


def test_runner_records_basic_harness_only_never_display_ids():
    """Every implemented arm records exactly `basic`/`harness`; the codebook
    maps blind codes back to those exact strings; prereg display ids appear
    nowhere in the packet."""
    jobs = [
        _job('muse-spark-1.3', {'basic': _arm(_claim()), 'harness': _arm(_claim())},
             order=['basic', 'harness'], calls=3),          # A1 + A2 (paired)
        _job('muse-wonder-1.0', {'basic': _arm(_claim())}, calls=1),   # B1
        _job('muse-wonder-1.0', {'harness': _arm(_claim())}, calls=2),  # B2
        _job('muse-wonder-1.0', {'basic': _arm(_claim())}, calls=1),   # linked ref
    ]
    packet = build_scoring_packet(jobs, ['FX-COST-1'] * 4)
    entry = packet['cases'][0]
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    labels = {record['arm_label'] for record in mapping.values()}
    assert labels <= {'basic', 'harness'}
    assert labels == {'basic', 'harness'}
    recovered = {(r['job_index'], r['arm_label']) for r in mapping.values()}
    assert recovered == {(0, 'basic'), (0, 'harness'), (1, 'basic'),
                         (2, 'harness'), (3, 'basic')}
    sheet_codes = {arm['blind_code'] for arm in entry['blinded_arms']}
    assert not (sheet_codes & set(DISPLAY_IDS))
    assert not ({'basic', 'harness'} & sheet_codes)  # sheets carry blind codes only
    for arm in entry['blinded_arms']:
        assert arm['run_params']['model'] in ('muse-spark-1.3', 'muse-wonder-1.0')
        assert 'provider billed cost NOT_VERIFIED' in arm['run_params']['cost_basis_note']
    assert entry['aggregate']['evidence_accuracy'] == NOT_SCORED
    assert packet['summary']['scoring_status'] == NOT_SCORED
    dumped = json.dumps(packet, sort_keys=True, allow_nan=False)
    for display in DISPLAY_IDS:
        assert display not in dumped


def test_runner_param_marks_stay_pending_human():
    jobs = [_job('muse-spark-1.3', {'basic': _arm(_claim()), 'harness': _arm(_claim())})]
    packet = build_scoring_packet(jobs, ['FX-COST-1'])
    for arm in packet['cases'][0]['blinded_arms']:
        for item in arm['claims']:
            assert item['human_claim_verdict'] == PENDING_HUMAN
            for occurrence in item['citation_items']:
                assert occurrence['human_mark'] == PENDING_HUMAN
                assert set(occurrence['allowed_marks']) == set(ALLOWED_HUMAN_CITATION_MARKS)


def test_packet_names_author_reviewer_not_sentinel():
    """G4 closed 2026-10-27: every reviewer slot names the real human, and the
    packet discloses that this is an author self-score, not independent review."""
    jobs = [_job('muse-spark-1.3', {'basic': _arm(_claim()), 'harness': _arm(_claim())})]
    packet = build_scoring_packet(jobs, ['FX-COST-1'])
    assert packet['reviewer'] == 'Author'
    assert packet['reviewer_kind'] == 'author_reviewer'
    assert packet['reviewer_approved_at'] == '2026-10-27'
    assert 'not independent' in packet['reviewer_disclosure']
    assert packet['rubric']['reviewer'] == 'Author'
    assert packet['summary']['reviewer'] == 'Author'
    assert 'HUMAN_REQUIRED_MISSING' not in json.dumps(packet, ensure_ascii=False)
    # Scoring itself still has not happened: naming a reviewer never implies a score.
    assert packet['scoring_status'] == NOT_SCORED
    assert packet['summary']['scoring_status'] == NOT_SCORED
    rates = {k: v for k, v in packet['summary']['accuracy'].items() if k != 'note'}
    assert set(rates.values()) == {NOT_SCORED}


def test_evaluate_rejects_display_label_arms():
    """`evaluation.evaluate` only aggregates `basic`/`harness`; a review keyed
    by prereg display ids is rejected — display ids are not reviewable arms."""
    from app.ai.evaluation import evaluate
    result = {'outputs': {'basic': _arm(_claim()), 'harness': _arm(_claim())},
              'elapsed_seconds': 5.0, 'calculated_cost_usd': 0.01, 'stages': []}
    review = {'reviewer': 'HUMAN_REQUIRED_MISSING', 'gold_case_id': 'FX-COST-1',
              'rubric_version': 'rw-research-1',
              'arms': {'spark-basic': {'claim_verdicts': ['supported'],
                                       'citation_verdicts': ['supports'],
                                       'variable_method_correct': None,
                                       'task_success': None},
                       'spark-harness': {'claim_verdicts': ['supported'],
                                         'citation_verdicts': ['supports'],
                                         'variable_method_correct': None,
                                         'task_success': None}}}
    with pytest.raises(ValueError):
        evaluate(result, review)


@pytest.mark.xfail(strict=True,
                   reason='Coverage Step 1 (arm_display_names annotation in '
                          'build_scoring_packet) is designed but not implemented; '
                          'recorded keys stay basic/harness until it lands.')
def test_step1_display_name_annotation_exists():
    assert 'arm_display_names' in inspect.signature(build_scoring_packet).parameters


# ---------------------------------------------------------------------------
# Live-service section (mock transports only): arm derivation per model
# ---------------------------------------------------------------------------

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


def _seed_prior(s, qid, prior_id='ai-prior-labels'):
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


def test_spark_basic_records_basic_only(svc, monkeypatch):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'research_query', 'harness': False})
    assert prep['model'] == 'meta-models/muse-spark-open-1026'
    assert prep['max_inference_calls'] == 1
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert set(result['outputs']) == {'basic', 'evaluation'}
    assert result['outputs']['evaluation']['kind'] == 'single'
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert len(bodies) == 1


def test_spark_harness_records_frame_then_harness(svc, monkeypatch):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'research_query', 'harness': True})
    assert prep['max_inference_calls'] == 2
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['frame', 'harness']
    assert set(result['outputs']) == {'harness', 'evaluation'}
    assert result['outputs']['evaluation']['execution_order'] == ['harness']
    assert 'public work plan' in bodies[0].get('instructions', '')
    final_ctx = json.loads(bodies[1]['input'][0]['content'][0]['text'])
    assert final_ctx['stage_context']['bounded_plan']['sources'] == ['DD-2023']


def test_spark_compare_records_paired_execution_order(svc, monkeypatch):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'research_query', 'compare': True})
    assert prep['max_inference_calls'] == 3
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    order = record['execution_order']
    assert sorted(order) == ['basic', 'harness']
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert result['outputs']['evaluation']['kind'] == 'paired'
    assert result['outputs']['evaluation']['execution_order'] == order
    assert set(result['outputs']) == {'basic', 'harness', 'evaluation'}
    assert len(bodies) == 3


def test_contributor_manual_route_blocked(svc):
    """A historical catalog entry is not permission to prepare a paid job."""
    assert registry.route('research_query').id == 'meta-models/muse-spark-open-1026'
    assert registry.route('research_query', 'muse-spark-1.3-contributor').id == \
        'muse-spark-1.3-contributor'
    s, qid = svc
    with pytest.raises(Conflict, match='meta_spark_blocked_credits_exhausted'):
        s.prepare(qid, {'model': 'muse-spark-1.3-contributor', 'harness': True})


def test_baseten_manual_route_executes_harness(svc, monkeypatch):
    """The supported explicit route retains single-harness arm semantics."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'research_query', 'model': registry.BASETEN_MODEL_ID,
                           'harness': True})
    assert prep['model'] == registry.BASETEN_MODEL_ID
    assert prep['max_inference_calls'] == 2
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert set(result['outputs']) == {'harness', 'evaluation'}
    assert result['outputs']['evaluation']['kind'] == 'single'
    assert result['outputs']['evaluation']['execution_order'] == ['harness']
    assert [st['stage'] for st in result['stages']] == ['frame', 'harness']
    assert len(bodies) == 2


def test_wonder_standalone_basic_records_basic_only(svc, monkeypatch):
    """B1 live pin: standalone methodology_review with harness=False explicitly
    set executes one basic call (mock transport) — the arm-table B1 cell."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'methodology_review', 'harness': False})
    assert prep['model'] == 'muse-wonder-1.0'
    assert prep['max_inference_calls'] == 1
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert set(result['outputs']) == {'basic', 'evaluation'}
    assert result['outputs']['evaluation']['kind'] == 'single'
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert len(bodies) == 1


def test_wonder_standalone_and_linked_recorded_labels(svc, monkeypatch):
    """B1/B2 record `basic`/`harness` over the restricted shape; the linked
    path records `basic` only — no `wonder-linked-harness` key exists."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': 'methodology_review', 'harness': True,
                           'max_cost_usd': 50})
    assert prep['model'] == 'muse-wonder-1.0'
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert set(result['outputs']) == {'harness', 'evaluation'}
    assert result['outputs']['evaluation']['execution_order'] == ['harness']
    prior_id = _seed_prior(s, qid)
    prep_linked = s.prepare(qid, {'task': 'methodology_review', 'harness': True,
                                  'compare': True, 'max_cost_usd': 50,
                                  'second_look_of': prior_id})
    assert prep_linked['max_inference_calls'] == 1
    linked = asyncio.run(s.execute(prep_linked['job_id'], prep_linked['preview_hash'], True))
    assert set(linked['outputs']) == {'basic', 'evaluation'}
    assert linked['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in linked['stages']] == ['basic']
    dumped = json.dumps(linked, sort_keys=True)
    for display in DISPLAY_IDS:
        assert display not in dumped


def test_local_arms_absent_rejected(svc):
    """C1/C2 are BLOCKED on an unconfigured machine: no local model id is
    executable without a configured loopback runtime, and local mode then fails
    closed at prepare with the honest blocking reason — the local pair is
    asserted absent, not invented."""
    with pytest.raises(ValueError):
        registry.route('research_query', 'spark_open_self_hosted')
    with pytest.raises(ValueError):
        registry.route('research_query', 'glimmer_self_hosted')
    s, qid = svc
    with pytest.raises(Conflict, match='Local mode is not configured'):
        s.prepare(qid, {'task': 'research_query', 'mode': 'local'})


def test_kind_mismatch_route_rejected():
    with pytest.raises(ValueError):
        registry.route('research_query', 'muse-image-1.0')
