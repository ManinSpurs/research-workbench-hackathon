"""Model x harness matrix contract: SCORED cells vs manual-only/EXCLUDED cells.

Matrix source (read-only): ``current/MODEL_HARNESS_COVERAGE_EN.md`` Sec. 4.

SCORED cells (asserted here):
  - (muse-spark-1.3, basic)     -- 1 call, full snapshot
  - (muse-spark-1.3, harness)   -- frame + final, full snapshot
  - (muse-wonder-1.0, basic)    -- standalone restricted shape, 1 call (B1)
  - (muse-wonder-1.0, harness)  -- standalone restricted shape, frame + final (B2)

EXCLUDED from the scored packet (asserted absent here):
  - muse-spark-1.3-contributor basic/harness/compare: manual-only, explicitly
    selected, preview + receipt labelled; never a silent substitute and never
    SCORED (coverage Sec. 4: "EXCLUDED from the scored packet, never SCORED").
  - wonder-linked-basic: today's restricted linked critique is REFERENCE-ONLY
    (depends on a prior job's claims; different input shape).
  - No linked harness arm exists: the linked second look stays forced basic,
    exactly one call, permanent for hackathon scope (LEAD DECISION 2026-10-27).

Label semantics pinned here (matches code, not invented):
  - Recorded arm keys stay ``basic`` / ``harness`` (``evaluation_runner._extract_arms``
    records every ``outputs`` key except ``evaluation``). The bare key ``basic``
    is INTENTIONALLY shared across models; disambiguation is the composite
    ``(run_params.model, arm_label)`` plus the sealed unblinding key
    (``build_scoring_packet`` codebook: blind_code -> {job_index, arm_label}).
    "Unique per (model, arm)" in this file therefore means the composite is
    injective -- never that bare ``basic`` appears only once.
  - Compare is paired machinery inside one job (basic + harness arms,
    ``evaluation.kind == 'paired'``), not a fifth scored cell.

Code paths exercised (real paths, mock transports only):
  - ``AIService.prepare`` -> ``max_inference_calls`` / recorded ``calls`` /
    recorded ``execution_order`` (local; zero provider calls asserted).
  - ``AIService.execute`` -> ``outputs`` arms / ``stages`` / ``evaluation``
    (mock ``MetaProvider`` transport; body counts asserted).
  - ``evaluation_runner.build_scoring_packet`` -> blinded arms + codebook /
    unblinding key over recorded results (offline; no inference).

Companion coverage (not duplicated here): ``test_wonder_harness_arm.py``
(standalone/linked call counts, restricted shape, fail-closed) and
``test_model_harness_labels_contract.py`` (recorded keys stay basic/harness,
display ids never recorded, contributor manual route labelled).

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

from app.ai import registry, service  # noqa: E402
from app.ai.evaluation_runner import NOT_SCORED, build_scoring_packet  # noqa: E402
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

# Coverage Sec. 4 SCORED cells: 2 models x 2 arms. Compare is the paired
# mechanism that records both arms of one model in a single job.
SCORED_CELLS = (
    ('meta-models/muse-spark-open-1026', 'basic'),
    ('meta-models/muse-spark-open-1026', 'harness'),
    ('muse-wonder-1.0', 'basic'),
    ('muse-wonder-1.0', 'harness'),
)
SCORED_MODELS = ('meta-models/muse-spark-open-1026', 'muse-wonder-1.0')
RECORDED_ARM_KEYS = ('basic', 'harness')


# ---------------------------------------------------------------------------
# Offline packet helpers (no provider needed)
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


def _claim():
    return [{'text': 'Applicant estimate', 'evidence_ids': ['DD-2023:est_cost'],
             'kind': 'fact'}]


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


def _claim_text(marker):
    return [{'text': marker, 'evidence_ids': ['DD-2023:est_cost'],
             'kind': 'fact'}]


def _distinct_scored_jobs():
    """Scored jobs with per-cell-distinct claim text so a codebook swap is
    observable through the packet itself (not just test-side joins)."""
    return [
        _job('meta-models/muse-spark-open-1026',
             {'basic': _arm(_claim_text('spark-basic-claim')),
              'harness': _arm(_claim_text('spark-harness-claim'))},
             order=['basic', 'harness'], calls=3),
        _job('muse-wonder-1.0',
             {'basic': _arm(_claim_text('wonder-basic-claim'))}, calls=1),
        _job('muse-wonder-1.0',
             {'harness': _arm(_claim_text('wonder-harness-claim'))}, calls=2),
    ]


def _contributor_job():
    return _job('muse-spark-1.3-contributor',
                {'basic': _arm(_claim_text('contributor-basic-claim'))},
                calls=1)


def _local_shaped_job(model='local:ollama:qwen3-4b-q4'):
    # Shape per current/LOCAL_FREE_MODE_DESIGN_EN.md Sec. 2.1.2:
    # local:<server>:<model-tag>. Never executable today (registry has no
    # such id; prepare rejects mode='local'), but well-formed for the
    # offline packet builder, which takes any recorded-shaped dict.
    return _job(model, {'basic': _arm(_claim_text('local-basic-claim'))},
                calls=1)


# ---------------------------------------------------------------------------
# 1. Every SCORED cell has a stable arm label and a codebook entry
# ---------------------------------------------------------------------------

def test_scored_cells_have_stable_labels_and_codebook_entries():
    jobs = _distinct_scored_jobs()
    packet = build_scoring_packet(jobs, ['FX-COST-1'] * len(jobs))
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    recovered = {(r['job_index'], r['arm_label']) for r in mapping.values()}
    assert recovered == {(0, 'basic'), (0, 'harness'), (1, 'basic'), (2, 'harness')}
    # Every SCORED (model, arm) cell resolves to exactly one recorded entry
    # with the model's identity carried alongside the arm label.
    seen = set()
    for blind_code, record in mapping.items():
        job = jobs[record['job_index']]
        cell = (job['model'], record['arm_label'])
        assert cell in SCORED_CELLS
        assert blind_code not in seen
        seen.add(blind_code)
    assert seen == set(mapping)
    cells_present = {(jobs[r['job_index']]['model'], r['arm_label'])
                     for r in mapping.values()}
    assert cells_present == set(SCORED_CELLS)
    # Arm labels are the stable recorded keys; models disambiguate.
    truth = packet['unblinding_key']['cases']['FX-COST-1']
    assert truth['arm_labels'] == ['basic', 'harness']
    for arm in packet['cases'][0]['blinded_arms']:
        assert arm['run_params']['model'] in SCORED_MODELS
    assert packet['summary']['scoring_status'] == NOT_SCORED


# ---------------------------------------------------------------------------
# 2. Manual-only / EXCLUDED cells never appear in the scored arm label set
#
# Boundary pin (by design): build_scoring_packet has NO exclusion
# responsibility — it blinds every arm of every recorded job it is handed
# (see _extract_arms in app/ai/evaluation_runner.py). Exclusion is owned by
# the caller: only SCORED jobs (the 4 cells above, produced by prepare/
# execute) are ever handed to the builder. The tests below pin both sides:
# (a) the builder blindly includes whatever it is handed, and (b) the app
# layer never produces a recorded contributor/local-shaped job to hand it.
# ---------------------------------------------------------------------------

def test_builder_has_no_exclusion_responsibility():
    """Pin the boundary: feeding a contributor-shaped job through the same
    packet-building path used for scored jobs yields one blinded arm for it
    (the builder excludes nothing); feeding a local-shaped job does the same.
    Exclusion therefore lives at the caller layer, asserted below."""
    jobs = _distinct_scored_jobs() + [_contributor_job()]
    packet = build_scoring_packet(jobs, ['FX-COST-1'] * len(jobs))
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    cells = {(jobs[r['job_index']]['model'], r['arm_label']) for r in mapping.values()}
    assert ('muse-spark-1.3-contributor', 'basic') in cells
    assert len(mapping) == 5  # 4 SCORED + 1 contributor arm, all blinded

    local_jobs = _distinct_scored_jobs() + [
        _local_shaped_job(), _local_shaped_job('local:lmstudio:llama3.2-3b-q4')]
    local_packet = build_scoring_packet(local_jobs, ['FX-COST-1'] * len(local_jobs))
    local_mapping = local_packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    local_cells = {(local_jobs[r['job_index']]['model'], r['arm_label'])
                   for r in local_mapping.values()}
    assert ('local:ollama:qwen3-4b-q4', 'basic') in local_cells
    assert ('local:lmstudio:llama3.2-3b-q4', 'basic') in local_cells
    assert len(local_mapping) == 6  # 4 SCORED + 2 local-shaped arms


def test_scored_packet_contains_exactly_the_four_scored_cells():
    """The caller-side exclusion: the scored packet is built ONLY from scored
    jobs, so its model set is exactly the 4 SCORED (model, arm) cells — a
    contributor or local-shaped job is never in the input, never in the map."""
    jobs = _distinct_scored_jobs()
    packet = build_scoring_packet(jobs, ['FX-COST-1'] * len(jobs))
    truth = packet['unblinding_key']['cases']['FX-COST-1']
    assert set(truth['arm_labels']) <= set(RECORDED_ARM_KEYS)
    models = {arm['run_params']['model'] for arm in packet['cases'][0]['blinded_arms']}
    assert models == set(SCORED_MODELS)
    mapping = truth['mapping']
    cells = {(jobs[r['job_index']]['model'], r['arm_label']) for r in mapping.values()}
    assert cells == set(SCORED_CELLS)
    assert len(mapping) == 4
    # Defense-in-depth literal guard only (NOT exclusion proof): the scored
    # input above never contains an excluded id, so these absences are
    # structurally guaranteed and cannot kill a mutant. The exclusion proof
    # is the exact-cell assertions above. Kept as a belt-and-braces
    # tripwire in case a future builder change ever echoes new id fields
    # into the serialized packet.
    dumped = json.dumps(packet, sort_keys=True, allow_nan=False)
    assert 'muse-spark-1.3-contributor' not in dumped
    assert 'spark_open_self_hosted' not in dumped
    assert 'glimmer_self_hosted' not in dumped
    assert 'local:ollama:qwen3-4b-q4' not in dumped
    assert 'local:lmstudio:llama3.2-3b-q4' not in dumped
    assert 'local:' not in dumped


def test_contributor_is_manual_only_never_default(svc):
    """Contributor stays explicitly selected (policy gate); default routing
    stays Standard. The scored packet above therefore cannot gain a
    contributor arm by default."""
    assert registry.route('research_query').id == 'meta-models/muse-spark-open-1026'
    assert registry.route('research_query', 'muse-spark-1.3-contributor').id == \
        'muse-spark-1.3-contributor'
    s, qid = svc
    with pytest.raises(Conflict, match='meta_spark_blocked_credits_exhausted'):
        s.prepare(qid, {'model': 'muse-spark-1.3-contributor', 'harness': True})


def test_local_arms_absent_not_scored(svc):
    """C1/C2 are BLOCKED on an unconfigured machine: no local model id is
    executable without a configured loopback runtime, and local mode then fails
    closed at prepare with the honest blocking reason -- the local pair is
    asserted absent, not invented."""
    with pytest.raises(ValueError):
        registry.route('research_query', 'spark_open_self_hosted')
    with pytest.raises(ValueError):
        registry.route('research_query', 'glimmer_self_hosted')
    s, qid = svc
    with pytest.raises(Conflict, match='Local mode is not configured'):
        s.prepare(qid, {'task': 'research_query', 'mode': 'local'})


# ---------------------------------------------------------------------------
# Live-service section (mock transports only): matrix cells through prepare/execute
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


def _seed_prior(s, qid, prior_id='ai-prior-matrix'):
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


def _distinct_e2e_result(body):
    """Per-arm-distinct claim payload for the end-to-end leg.

    The harness final is the only non-frame call whose request input carries
    a bounded plan (``stage_context``); basic finals carry none. Keying the
    marker off that request-shape difference — plus the model id — gives
    each of the 4 SCORED arms its own claim text through the mock transport,
    so the sheet-payload equalities below can actually catch a code swap (a
    shared payload lets basic<->basic swaps pass silently). Shape is
    identical to RESULT; only the claim text varies. A body that does not
    parse as the documented reasoning shape falls back to the basic marker,
    which can only shrink the distinct set and fail loudly below.
    """
    model = body.get('model', 'unknown-model')
    arm = 'basic'
    try:
        blocks = body.get('input', [])[0]['content']
        stage_text = next(b['text'] for b in blocks if b.get('type') == 'input_text')
        if json.loads(stage_text).get('stage_context') is not None:
            arm = 'harness'
    except (IndexError, KeyError, StopIteration, ValueError):
        arm = 'basic'
    result = dict(RESULT)
    result['claims'] = [{'text': 'e2e-%s-%s-claim' % (model, arm),
                         'evidence_ids': ['DD-2023:est_cost'], 'kind': 'fact'}]
    return result


def _e2e_success(body):
    if 'public work plan' in body.get('instructions', ''):
        return _success(body)
    return _success(body, result=_distinct_e2e_result(body))


# --- 3a. Spark SCORED cells produce the labelled arms they claim ---

def test_spark_scored_cells_execute_labelled_arms(svc, monkeypatch):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep_basic = s.prepare(qid, {'task': 'research_query', 'harness': False})
    assert bodies == []  # prepare is local: zero provider calls
    assert prep_basic['model'] == 'meta-models/muse-spark-open-1026'
    assert prep_basic['max_inference_calls'] == 1
    basic = asyncio.run(s.execute(prep_basic['job_id'], prep_basic['preview_hash'], True))
    assert set(basic['outputs']) == {'basic', 'evaluation'}
    assert basic['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in basic['stages']] == ['basic']

    prep_harness = s.prepare(qid, {'task': 'research_query', 'harness': True})
    assert prep_harness['max_inference_calls'] == 2
    harness = asyncio.run(s.execute(prep_harness['job_id'], prep_harness['preview_hash'], True))
    assert [st['stage'] for st in harness['stages']] == ['frame', 'harness']
    assert harness['outputs']['evaluation']['execution_order'] == ['harness']
    assert set(harness['outputs']) == {'harness', 'evaluation'}

    prep_compare = s.prepare(qid, {'task': 'research_query', 'compare': True})
    assert prep_compare['max_inference_calls'] == 3
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep_compare['job_id'],)).fetchone()[0])
    assert sorted(record['execution_order']) == ['basic', 'harness']
    paired = asyncio.run(s.execute(prep_compare['job_id'], prep_compare['preview_hash'], True))
    assert paired['outputs']['evaluation']['kind'] == 'paired'
    assert set(paired['outputs']) == {'basic', 'harness', 'evaluation'}
    assert len(bodies) == 1 + 2 + 3


# --- 3b. Wonder standalone shapes: basic-only default (1), harness (2), compare (3) ---

@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
@pytest.mark.parametrize('kwargs,calls', [
    ({}, 1),                    # no explicit opt-in -> pre-B2 single basic
    ({'harness': False}, 1),
    ({'compare': False}, 1),    # compare-only False must not arm harness
    ({'harness': True}, 2),
    ({'harness': False, 'compare': True}, 3),
    ({'compare': True}, 3),
    ({'harness': True, 'compare': True}, 3),
])
def test_wonder_standalone_prepare_shapes(svc, monkeypatch, task, kwargs, calls):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'max_cost_usd': 50, **kwargs})
    assert bodies == []  # prepare is local: zero provider calls
    assert prep['model'] == 'muse-wonder-1.0'
    assert prep['max_inference_calls'] == calls
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    assert record['calls'] == calls
    assert record['request']['harness'] is bool(kwargs.get('harness', False))
    assert record['request']['compare'] is bool(kwargs.get('compare', False))


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_wonder_standalone_basic_executes_labelled_basic(svc, monkeypatch, task):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'harness': False, 'max_cost_usd': 50})
    assert bodies == []
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert set(result['outputs']) == {'basic', 'evaluation'}
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert [st['stage'] for st in result['stages']] == ['basic']
    assert len(bodies) == 1


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_wonder_standalone_harness_executes_labelled_harness(svc, monkeypatch, task):
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'harness': True, 'max_cost_usd': 50})
    assert prep['max_inference_calls'] == 2
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['frame', 'harness']
    assert set(result['outputs']) == {'harness', 'evaluation'}
    assert result['outputs']['evaluation']['execution_order'] == ['harness']
    assert len(bodies) == 2


@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_wonder_standalone_compare_executes_paired_labels(svc, monkeypatch, task):
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
    assert set(result['outputs']) == {'basic', 'harness', 'evaluation'}
    assert len(bodies) == 3


# --- 3c. Linked second look always basic-only ---

@pytest.mark.parametrize('task', ['methodology_review', 'cross_artifact_review'])
def test_wonder_linked_always_basic_only(svc, monkeypatch, task):
    s, qid = svc
    prior_id = _seed_prior(s, qid)
    bodies = _ready(s, monkeypatch, lambda b: _success(b))
    prep = s.prepare(qid, {'task': task, 'harness': True, 'compare': True,
                           'max_cost_usd': 50, 'second_look_of': prior_id})
    assert bodies == []  # prepare is local: zero provider calls
    assert prep['max_inference_calls'] == 1
    record = json.loads(s.db.execute(
        'SELECT request_json FROM ai_jobs WHERE id=?', (prep['job_id'],)).fetchone()[0])
    assert record['request']['harness'] is False
    assert record['request']['compare'] is False
    assert record['calls'] == 1
    result = asyncio.run(s.execute(prep['job_id'], prep['preview_hash'], True))
    assert [st['stage'] for st in result['stages']] == ['basic']
    # No separate linked-harness arm exists by design: the recorded output
    # keys are exactly basic + evaluation (positive pin, not a string-absence
    # assertion over a label the code can never emit).
    assert set(result['outputs']) == {'basic', 'evaluation'}
    assert result['outputs']['evaluation']['execution_order'] == ['basic']
    assert len(bodies) == 1


# --- 3d. End to end: real prepare/execute envelopes -> scoring packet ---

def _executed_results(svc, monkeypatch):
    """One real prepare/execute per SCORED model shape; returns the recorded
    result envelopes exactly as execute() persisted them.

    The mock transport answers each arm with its own claim text (see
    ``_e2e_success``), so the packet assertions below exercise the real
    per-arm payloads, not one shared string."""
    s, qid = svc
    bodies = _ready(s, monkeypatch, lambda b: _e2e_success(b))
    prep_spark = s.prepare(qid, {'task': 'research_query', 'compare': True})
    spark = asyncio.run(s.execute(prep_spark['job_id'], prep_spark['preview_hash'], True))
    prep_wonder_basic = s.prepare(qid, {'task': 'methodology_review',
                                        'harness': False, 'max_cost_usd': 50})
    wonder_basic = asyncio.run(s.execute(prep_wonder_basic['job_id'],
                                         prep_wonder_basic['preview_hash'], True))
    prep_wonder_harness = s.prepare(qid, {'task': 'methodology_review',
                                          'harness': True, 'max_cost_usd': 50})
    wonder_harness = asyncio.run(s.execute(prep_wonder_harness['job_id'],
                                           prep_wonder_harness['preview_hash'], True))
    return [spark, wonder_basic, wonder_harness], bodies


def test_end_to_end_real_envelopes_feed_scoring_packet(svc, monkeypatch):
    """prepare -> execute (mock transport) -> build_scoring_packet: the
    (model, arm_label) composite identity and blind-code mapping survive the
    real envelope shape (4 arms: paired Spark + B1 + B2)."""
    results, bodies = _executed_results(svc, monkeypatch)
    assert len(bodies) == 3 + 1 + 2
    packet = build_scoring_packet(results, ['FX-COST-1'] * len(results))
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    assert len(mapping) == 4
    cells = {(results[r['job_index']]['outputs']['evaluation']['model'], r['arm_label'])
             for r in mapping.values()}
    assert cells == set(SCORED_CELLS)
    # Every codebook entry points back at the real envelope's arm result via
    # the packet's own blinded sheets (no test-side joins).
    sheets = {arm['blind_code']: arm for arm in packet['cases'][0]['blinded_arms']}
    for blind_code, record in mapping.items():
        arm_result = (results[record['job_index']]['outputs'][record['arm_label']]
                      ['result']['claims'])
        sheet = sheets[blind_code]
        assert sheet['claim_count'] == len(arm_result)
        assert [c['text'] for c in sheet['claims']] == [c['text'] for c in arm_result]
    # Payload leg is independently meaningful: the four arms arrived with
    # pairwise-distinct claim texts through the mock transport, so a codebook
    # swap between any two arms fails the equalities above (with a shared
    # payload, a basic<->basic swap would pass them silently).
    sheet_texts = [tuple(c['text'] for c in arm['claims'])
                   for arm in packet['cases'][0]['blinded_arms']]
    assert len(set(sheet_texts)) == 4 == len(mapping)
    # Arm labels stay the stable recorded keys; run_params carries the model.
    assert sorted(packet['unblinding_key']['cases']['FX-COST-1']['arm_labels']) == \
        ['basic', 'harness']


def test_end_to_end_distinct_models_same_arm_label_map_back_correctly(svc, monkeypatch):
    """Same-arm-different-model codes resolve through the packet's own lookup:
    the 'basic' code for the Spark job maps back to the Spark envelope's arm,
    the 'basic' code for the Wonder job to the Wonder envelope's arm."""
    results, _ = _executed_results(svc, monkeypatch)
    packet = build_scoring_packet(results, ['FX-COST-1'] * len(results))
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    sheets = {arm['blind_code']: arm for arm in packet['cases'][0]['blinded_arms']}
    basics = {code: rec for code, rec in mapping.items() if rec['arm_label'] == 'basic'}
    assert len(basics) == 2
    models = {results[rec['job_index']]['outputs']['evaluation']['model']
              for rec in basics.values()}
    assert models == {'meta-models/muse-spark-open-1026', 'muse-wonder-1.0'}
    spark_code = next(code for code, rec in basics.items()
                      if results[rec['job_index']]['outputs']['evaluation']['model']
                      == 'meta-models/muse-spark-open-1026')
    wonder_code = next(code for code, rec in basics.items()
                       if results[rec['job_index']]['outputs']['evaluation']['model']
                       == 'muse-wonder-1.0')
    assert spark_code != wonder_code
    # The two same-label sheets carry different claim payloads: the swap the
    # codebook could hide (basic<->basic) is observable in the sheets
    # themselves, not just in the run_params model tags.
    assert [c['text'] for c in sheets[spark_code]['claims']] != \
        [c['text'] for c in sheets[wonder_code]['claims']]
    # Each code resolves to the correct (job, model, arm) through the
    # packet's own lookup: the sheet's run_params model matches the codebook
    # job_index's envelope model, the envelope carries that arm label, and the
    # two codes point at different jobs (a code swap would fail here).
    assert sheets[spark_code]['run_params']['model'] == 'meta-models/muse-spark-open-1026'
    assert sheets[wonder_code]['run_params']['model'] == 'muse-wonder-1.0'
    assert basics[spark_code]['job_index'] != basics[wonder_code]['job_index']
    for code, rec in basics.items():
        assert rec['arm_label'] in results[rec['job_index']]['outputs']


# ---------------------------------------------------------------------------
# 4. No cell silently maps two different (model, arm) pairs to the same label
# ---------------------------------------------------------------------------

def test_no_cell_maps_two_model_arm_pairs_to_one_label():
    """Bare 'basic' is shared across models BY DESIGN (stable recorded key);
    the scored identity is the (model, arm_label) composite plus a unique
    blind code per (job, arm). A silent collision would be two different
    composites sharing one blind code or one codebook entry."""
    # Injectivity that can fail in the harmful direction: the two jobs sharing
    # bare 'basic' receive distinct blind codes, and each code maps back to
    # the correct (job, model, arm) through the packet's own lookup — test the
    # packet's mapping + sheets, not a test-side composite set.
    jobs = _distinct_scored_jobs()
    packet = build_scoring_packet(jobs, ['FX-COST-1'] * len(jobs))
    mapping = packet['unblinding_key']['cases']['FX-COST-1']['mapping']
    composites = [(jobs[r['job_index']]['model'], r['arm_label']) for r in mapping.values()]
    basics = {code: rec for code, rec in mapping.items() if rec['arm_label'] == 'basic'}
    # Both models record bare 'basic' -- shared by design, composites differ.
    assert sum(1 for _, arm in composites if arm == 'basic') == 2
    # Same-arm-different-model through the packet's own lookup: the two
    # 'basic' codes are distinct, point at different jobs, and each sheet's
    # run_params model matches its codebook job's model.
    assert len(basics) == 2
    sheets = {arm['blind_code']: arm for arm in packet['cases'][0]['blinded_arms']}
    assert len({rec['job_index'] for rec in basics.values()}) == 2
    for code, rec in basics.items():
        assert sheets[code]['run_params']['model'] == jobs[rec['job_index']]['model']
    assert ('meta-models/muse-spark-open-1026', 'basic') in composites
    assert ('muse-wonder-1.0', 'basic') in composites
    # Injectivity: the count pin (exactly 4 codebook entries) plus the
    # exact-cell equality below. (`len(composites)` equals `len(mapping)` by
    # construction — composites is built 1:1 from mapping.values() — so that
    # equality is not asserted as if it could fail.)
    assert len(mapping) == 4
    assert sorted(composites) == sorted(SCORED_CELLS)
    blinded_codes = [arm['blind_code'] for arm in packet['cases'][0]['blinded_arms']]
    assert len(set(blinded_codes)) == 4 == len(mapping)
