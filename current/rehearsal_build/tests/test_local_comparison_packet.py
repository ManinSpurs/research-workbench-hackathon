"""L3c: local-vs-cloud comparison packet wiring + pre-registered usefulness bar.

Mock transports only: all job results are synthetic recorded shapes already
in hand; no model is executed, no provider is called, nothing is downloaded.
"""
import copy
import json

import pytest

from app.ai.evaluation_runner import (
    NOT_SCORED,
    PENDING_HUMAN,
    build_scoring_packet,
)
from app.ai.local_comparison_packet import (
    BAR_CAVEAT,
    BAR_SOURCE_CITE,
    BAR_STATUS,
    BAR_U1,
    BAR_U2,
    BAR_U3,
    CLOUD_MODEL,
    FROZEN_CASES,
    BarNotFrozenError,
    apply_usefulness_bar,
    build_local_comparison_packet,
    usefulness_bar,
)

LOCAL_MODEL = "local:example-server:example-4b-q4"

BAR_EXPECTED_SOURCE = "current/LOCAL_FREE_MODE_DESIGN_EN.md:134-137"

# Full expected bar strings, transcribed from LOCAL_FREE_MODE_DESIGN_EN.md
# lines 134-137 (markdown bullets/bold stripped, wording otherwise exact).
BAR_EXPECTED_U1 = ("Floor U1 — task success: local arm `task_success == True` "
                   "on at least 2 of 3 frozen cases.")
BAR_EXPECTED_U2 = ("Floor U2 — citation accuracy: local arm "
                   "`evidence_accuracy >= 0.50` AND `false_citation_rate <= 0.50` "
                   "on cases where those rates are non-null (unknowns leave nulls; "
                   "nulls do not pass a floor — a null rate means \"unmeasured,\" "
                   "counted as *below bar* for that case).")
BAR_EXPECTED_U3 = ("Floor U3 — structural validity: `structural_valid == True` "
                   "on all 3 cases (the machine-checkable gate the app itself enforces).")
BAR_EXPECTED_CAVEAT = ("[UNVERIFIED: exact thresholds are a proposal — the Lead sets "
                       "final numbers before any run; English-only tasks.]")

CASE_CLAIMS = {
    "FX-COST-1": [{"text": "est_cost is applicant-reported estimated cost in USD.",
                   "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}],
    "FX-APPROVAL-1": [{"text": "approval_date is plan-review completion.",
                       "evidence_ids": ["DD-2023:approval_date"], "kind": "fact"}],
    "NYC-DATE-1": [{"text": "filing_date is not a DOB NOW column.",
                    "evidence_ids": ["DD-DOBNOW-2018:Column Info:Issued Date"],
                    "kind": "fact"}],
}


def _arm(case_id, structural_valid=True):
    claims = CASE_CLAIMS[case_id]
    cited = sum(len(c["evidence_ids"]) for c in claims)
    return {
        "result": {"claims": [dict(c) for c in claims]},
        "grounding": {
            "structural_valid": structural_valid,
            "errors": [],
            "citation_count": cited,
            "invalid_citation_count": 0,
            "citation_error_rate": 0.0,
            "linked_claim_fraction": 1.0,
            "semantic_correctness": NOT_SCORED,
            "false_citation_rate": "HUMAN_REVIEW_REQUIRED",
        },
    }


def _recorded_job(model, case_id, cost):
    return {
        "outputs": {
            "basic": _arm(case_id),
            "harness": _arm(case_id),
            "evaluation": {
                "kind": "paired",
                "accuracy": NOT_SCORED,
                "model": model,
                "execution_order": ["basic", "harness"],
            },
        },
        "model": model,
        "execution_order": ["basic", "harness"],
        "stages": [{"stage": "basic"}, {"stage": "harness"}],
        "measured_known_cost_usd": cost,
        "elapsed_seconds": 5.0,
    }


def _bundle():
    cases = list(FROZEN_CASES)
    local_jobs = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    return build_local_comparison_packet(local_jobs, cloud_jobs, cases)


def test_bar_recorded_verbatim_from_source_doc():
    bar = usefulness_bar()
    assert bar["bar"] == "DECISION-NEEDED" == BAR_STATUS
    assert bar["source"] == BAR_SOURCE_CITE == BAR_EXPECTED_SOURCE
    assert bar["u1"] == BAR_U1 == BAR_EXPECTED_U1
    assert bar["u2"] == BAR_U2 == BAR_EXPECTED_U2
    assert bar["u3"] == BAR_U3 == BAR_EXPECTED_U3
    assert bar["caveat"] == BAR_CAVEAT == BAR_EXPECTED_CAVEAT


def test_packet_has_all_four_arms_per_case_with_distinct_blind_codes():
    bundle = _bundle()
    packet = bundle["scoring_packet"]
    assert packet["summary"]["scoring_status"] == NOT_SCORED
    assert bundle["comparison"]["arms_total"] == 12
    for case_id in FROZEN_CASES:
        mapping = packet["unblinding_key"]["cases"][case_id]["mapping"]
        assert len(mapping) == 4  # local basic/harness + cloud basic/harness
        entry = next(e for e in packet["cases"] if e["case_id"] == case_id)
        codes = [arm["blind_code"] for arm in entry["blinded_arms"]]
        assert len(set(codes)) == 4
        cells = [c for c in bundle["comparison"]["cells"] if c["case_id"] == case_id]
        assert {(c["side"], c["arm"]) for c in cells} == {
            ("local", "basic"), ("local", "harness"),
            ("cloud", "basic"), ("cloud", "harness"),
        }
        assert {c["blind_code"] for c in cells} == set(codes)
        local_models = {c["model"] for c in cells if c["side"] == "local"}
        cloud_models = {c["model"] for c in cells if c["side"] == "cloud"}
        assert local_models == {LOCAL_MODEL}
        assert cloud_models == {CLOUD_MODEL}
        local_costs = {a["run_params"]["measured_known_cost_usd"]
                       for a in entry["blinded_arms"]
                       if a["run_params"]["model"] == LOCAL_MODEL}
        cloud_costs = {a["run_params"]["measured_known_cost_usd"]
                       for a in entry["blinded_arms"]
                       if a["run_params"]["model"] == CLOUD_MODEL}
        assert local_costs == {0.0}
        assert all(c > 0 for c in cloud_costs)


def test_every_result_defaults_not_scored_not_run():
    bundle = _bundle()
    packet = bundle["scoring_packet"]
    for entry in packet["cases"]:
        assert entry["aggregate"]["evidence_accuracy"] == NOT_SCORED
        assert entry["aggregate"]["false_citation_rate"] == NOT_SCORED
        assert entry["aggregate"]["task_success"] == NOT_SCORED
        for arm in entry["blinded_arms"]:
            for item in arm["claims"]:
                assert item["human_claim_verdict"] == PENDING_HUMAN
                for occurrence in item["citation_items"]:
                    assert occurrence["human_mark"] == PENDING_HUMAN
    assert packet["summary"]["scoring_status"] == NOT_SCORED
    assert packet["summary"]["accuracy"]["task_success"] == NOT_SCORED
    assert bundle["comparison"]["scoring_status"] == NOT_SCORED
    assert bundle["comparison"]["run_status"] == "NOT_RUN"
    assert all(c["run_status"] == "NOT_RUN" for c in bundle["comparison"]["cells"])
    assert all(c["scoring_status"] == NOT_SCORED for c in bundle["comparison"]["cells"])
    assert bundle["usefulness_bar"]["bar"] == "DECISION-NEEDED"
    json.dumps(bundle, sort_keys=True, allow_nan=False)


def test_reuses_existing_packet_builder_no_second_runner():
    cases = list(FROZEN_CASES)
    local_jobs = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    snapshot = copy.deepcopy((local_jobs, cloud_jobs, cases))
    bundle = build_local_comparison_packet(local_jobs, cloud_jobs, cases)
    assert (local_jobs, cloud_jobs, cases) == snapshot  # inputs not mutated
    expected = build_scoring_packet(
        [j for pair in zip(local_jobs, cloud_jobs) for j in pair],
        [c for c in cases for _ in (0, 1)],
    )
    assert bundle["scoring_packet"] == expected


def test_verdict_refused_while_bar_unfrozen():
    bundle = _bundle()
    with pytest.raises(BarNotFrozenError, match="DECISION-NEEDED"):
        apply_usefulness_bar(bundle)
    with pytest.raises(BarNotFrozenError, match="No PASS/FAIL"):
        apply_usefulness_bar(bundle)


def test_auxiliary_non_reasoning_model_never_in_reasoning_slot():
    cases = list(FROZEN_CASES)
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    for bad_model in ("muse-voice-transcribe-1.0", "sam-3.1", "muse-image-1.0"):
        local_jobs = [_recorded_job(bad_model, c, 0.0) for c in cases]
        with pytest.raises(ValueError, match="non-reasoning"):
            build_local_comparison_packet(local_jobs, cloud_jobs, cases)
        cloud_bad = [_recorded_job(bad_model, c, 0.01) for c in cases]
        good_local = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
        with pytest.raises(ValueError, match="non-reasoning"):
            build_local_comparison_packet(good_local, cloud_bad, cases)


def test_non_reasoning_guard_rejects_wrapped_ids_in_both_slots():
    cases = list(FROZEN_CASES)
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    for bad_model in ("muse-voice-transcribe-1.0", "sam-3.1", "muse-image-1.0"):
        wrapped_local = "local:example-server:%s" % bad_model
        local_jobs = [_recorded_job(wrapped_local, c, 0.0) for c in cases]
        with pytest.raises(ValueError, match="non-reasoning"):
            build_local_comparison_packet(local_jobs, cloud_jobs, cases)
        wrapped_cloud = "gateway:%s" % bad_model
        cloud_bad = [_recorded_job(wrapped_cloud, c, 0.01) for c in cases]
        good_local = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
        with pytest.raises(ValueError, match="non-reasoning"):
            build_local_comparison_packet(good_local, cloud_bad, cases)
    # Near-miss final segments must NOT trip the guard (exact-segment match).
    near_miss = [_recorded_job("local:example-server:sam-3.10", c, 0.0)
                 for c in cases]
    bundle = build_local_comparison_packet(near_miss, cloud_jobs, cases)
    assert bundle["comparison"]["arms_total"] == 12


def test_local_zero_cost_requires_present_numeric_zero():
    cases = list(FROZEN_CASES)
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    missing = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
    for job in missing:
        del job["measured_known_cost_usd"]
    with pytest.raises(ValueError, match=r"\$0"):
        build_local_comparison_packet(missing, cloud_jobs, cases)
    false_cost = [_recorded_job(LOCAL_MODEL, c, False) for c in cases]
    with pytest.raises(ValueError, match=r"\$0"):
        build_local_comparison_packet(false_cost, cloud_jobs, cases)
    for cost in (0, 0.0):
        local_jobs = [_recorded_job(LOCAL_MODEL, c, cost) for c in cases]
        bundle = build_local_comparison_packet(local_jobs, cloud_jobs, cases)
        assert bundle["comparison"]["arms_total"] == 12


def test_shape_guards_single_arm_mixed_job_wrong_cases():
    cases = list(FROZEN_CASES)
    local_jobs = [_recorded_job(LOCAL_MODEL, c, 0.0) for c in cases]
    cloud_jobs = [_recorded_job(CLOUD_MODEL, c, 0.0171685) for c in cases]
    single = _recorded_job(LOCAL_MODEL, cases[0], 0.0)
    del single["outputs"]["harness"]
    single["execution_order"] = ["basic"]
    with pytest.raises(ValueError, match="exactly basic\\+harness"):
        build_local_comparison_packet(
            [single] + local_jobs[1:], cloud_jobs, cases)
    mixed = _recorded_job(LOCAL_MODEL, cases[0], 0.0)
    mixed["outputs"]["muse-spark-1.3-basic"] = mixed["outputs"].pop("basic")
    with pytest.raises(ValueError, match="exactly basic\\+harness"):
        build_local_comparison_packet(
            [mixed] + local_jobs[1:], cloud_jobs, cases)
    with pytest.raises(ValueError, match="exactly the three frozen cases"):
        build_local_comparison_packet(local_jobs, cloud_jobs, ["FX-COST-1"] * 3)
    with pytest.raises(ValueError, match="must carry a 'local:"):
        build_local_comparison_packet(cloud_jobs, cloud_jobs, cases)
