"""Offline scoring-packet tests; synthetic recorded results only, no provider calls."""
import copy
import json
import math

import pytest

from app.ai.evaluation_runner import (
    ALLOWED_HUMAN_CITATION_MARKS,
    FIXTURE_MISSING,
    NOT_SCORED,
    PENDING_HUMAN,
    PRESENCE_CHECK_KIND,
    RUBRIC_VERSION,
    build_scoring_packet,
)


def _arm(claims, structural_valid=True):
    cited = sum(len(c["evidence_ids"]) for c in claims)
    return {
        "result": {"claims": claims},
        "grounding": {
            "structural_valid": structural_valid,
            "errors": [] if structural_valid else ["Cited source explicitly targets another release"],
            "citation_count": cited,
            "invalid_citation_count": 0 if structural_valid else 1,
            "citation_error_rate": 0.0 if structural_valid and cited else None,
            "linked_claim_fraction": 1.0 if claims else None,
            "semantic_correctness": NOT_SCORED,
            "false_citation_rate": "HUMAN_REVIEW_REQUIRED",
        },
    }


def _job(arms, **overrides):
    outputs = dict(arms)
    outputs["evaluation"] = {
        "kind": "paired",
        "accuracy": NOT_SCORED,
        "model": "muse-spark-1.3",
        "execution_order": list(arms),
    }
    job = {
        "outputs": outputs,
        "model": "muse-spark-1.3",
        "execution_order": list(arms),
        "stages": [{"stage": a, "elapsed_seconds": 1.0} for a in arms],
        "measured_known_cost_usd": 0.0171685,
        "elapsed_seconds": 12.3946,
    }
    job.update(overrides)
    return job


def _fx_cost_jobs():
    ok = _arm([{"text": "est_cost is applicant-reported estimated cost in USD.",
                "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}])
    fabricated = _arm([{"text": "est_cost isApplicant-reported estimated cost in USD.",
                        "evidence_ids": ["DD-2023:made_up"], "kind": "fact"}],
                      structural_valid=False)
    missing = _arm([{"text": "est_cost is cost.", "evidence_ids": [], "kind": "fact"}])
    return ([_job({"basic": ok, "harness": fabricated}), _job({"basic": missing})],
            ["FX-COST-1", "FX-COST-1"])


def _case(packet, case_id):
    for entry in packet["cases"]:
        if entry["case_id"] == case_id:
            return entry
    raise AssertionError("missing case %s" % case_id)


def test_frozen_packet_defaults_and_structural_presence():
    jobs, cases = _fx_cost_jobs()
    packet = build_scoring_packet(jobs, cases)
    entry = _case(packet, "FX-COST-1")
    assert entry["fixture_status"] == "FROZEN"
    assert entry["frozen_expectation"]["expected_citation_ids"] == ["DD-2023:est_cost"]
    assert entry["aggregate"]["evidence_accuracy"] == NOT_SCORED
    assert entry["aggregate"]["false_citation_rate"] == NOT_SCORED
    assert packet["summary"]["scoring_status"] == NOT_SCORED
    assert packet["summary"]["accuracy"]["evidence_accuracy"] == NOT_SCORED
    assert packet["rubric"]["rubric_version"] == RUBRIC_VERSION == "rw-research-1"
    cited, fabricated, missing = (
        entry["blinded_arms"][0]["claims"][0],
        entry["blinded_arms"][1]["claims"][0],
        entry["blinded_arms"][2]["claims"][0],
    )
    assert cited["expected_citation_presence"]["cited"] is True
    assert fabricated["expected_citation_presence"]["cited"] is False
    assert missing["expected_citation_presence"]["cited"] is False
    assert fabricated["expected_citation_presence"]["check_kind"] == PRESENCE_CHECK_KIND
    # Structural verdicts copied from the record, never auto-scored.
    assert entry["blinded_arms"][0]["structural"]["structural_valid"] is True
    assert entry["blinded_arms"][1]["structural"]["structural_valid"] is False
    for arm in entry["blinded_arms"]:
        for item in arm["claims"]:
            assert item["human_claim_verdict"] == PENDING_HUMAN
            for occurrence in item["citation_items"]:
                assert occurrence["human_mark"] == PENDING_HUMAN
                assert set(occurrence["allowed_marks"]) == set(ALLOWED_HUMAN_CITATION_MARKS)
    json.dumps(packet, allow_nan=False)


def test_run_params_and_blind_mapping_reversibility():
    jobs, cases = _fx_cost_jobs()
    packet = build_scoring_packet(jobs, cases)
    entry = _case(packet, "FX-COST-1")
    codes = [arm["blind_code"] for arm in entry["blinded_arms"]]
    assert len(set(codes)) == len(codes)  # blind labels unique
    sheet_names = {arm["blind_code"] for arm in entry["blinded_arms"]}
    assert "basic" not in sheet_names and "harness" not in sheet_names
    mapping = packet["unblinding_key"]["cases"]["FX-COST-1"]["mapping"]
    # Every blind code reverses to the true (job, arm); every true arm is covered.
    recovered = {(record["job_index"], record["arm_label"]) for record in mapping.values()}
    assert recovered == {(0, "basic"), (0, "harness"), (1, "basic")}
    for arm in entry["blinded_arms"]:
        params = arm["run_params"]
        assert params["model"] == "muse-spark-1.3"
        assert params["measured_known_cost_usd"] == pytest.approx(0.0171685)
        assert params["elapsed_seconds"] == pytest.approx(12.3946)
        assert params["calls"] >= 1


def test_unknown_case_is_fixture_missing():
    job = _job({"basic": _arm([{"text": "x", "evidence_ids": ["Y"], "kind": "fact"}])})
    packet = build_scoring_packet([job], ["NOPE-1"])
    entry = _case(packet, "NOPE-1")
    assert entry["fixture_status"] == FIXTURE_MISSING
    assert entry["frozen_expectation"]["expected_citation_ids"] == FIXTURE_MISSING
    assert entry["blinded_arms"][0]["claims"][0]["expected_citation_presence"] == FIXTURE_MISSING
    assert entry["aggregate"]["evidence_accuracy"] == NOT_SCORED


def test_citation_marks_match_arm_review_vocabulary():
    assert ALLOWED_HUMAN_CITATION_MARKS == ("supports", "wrong", "unknown")


def test_approval_and_nyc_date_emit_fixture_missing_with_verbatim_constraints():
    approval = _job({"basic": _arm([{"text": "approval_date is plan-review completion.",
                                     "evidence_ids": ["DD-2023:approval_date"],
                                     "kind": "fact"}])})
    nyc = _job({"harness": _arm([{"text": "filing_date is not a DOB NOW column.",
                                  "evidence_ids": ["DD-DOBNOW-2018:Column Info:Issued Date"],
                                  "kind": "fact"}])})
    packet = build_scoring_packet([approval, nyc], ["FX-APPROVAL-1", "NYC-DATE-1"])
    approval_entry = _case(packet, "FX-APPROVAL-1")
    assert approval_entry["fixture_status"] == "FROZEN"
    assert approval_entry["frozen_expectation"]["status"] == FIXTURE_MISSING
    assert approval_entry["frozen_expectation"]["expected_citation_ids"] == FIXTURE_MISSING
    assert approval_entry["frozen_expectation"]["gold_constraints"] == (
        "Plan-review completion in release 2023; "
        "OFF-NOTES-2025 does not establish the 2023 meaning; "
        "final approval remains unestablished"
    )
    nyc_entry = _case(packet, "NYC-DATE-1")
    assert nyc_entry["fixture_status"] == "FROZEN"
    assert nyc_entry["frozen_expectation"]["status"] == FIXTURE_MISSING
    assert nyc_entry["frozen_expectation"]["expected_citation_ids"] == FIXTURE_MISSING
    assert nyc_entry["frozen_expectation"]["gold_constraints"] == (
        "Check the selected DOB NOW file's actual columns; "
        "do not borrow BIS filing_date"
    )
    for entry in (approval_entry, nyc_entry):
        for arm in entry["blinded_arms"]:
            for item in arm["claims"]:
                assert item["expected_citation_presence"] == FIXTURE_MISSING
                assert item["human_claim_verdict"] == PENDING_HUMAN
                for occurrence in item["citation_items"]:
                    assert occurrence["human_mark"] == PENDING_HUMAN
                    assert occurrence["allowed_marks"] == ["supports", "wrong", "unknown"]
        assert entry["aggregate"]["evidence_accuracy"] == NOT_SCORED
    json.dumps(packet, allow_nan=False)


def test_nan_cost_excluded_as_null():
    job = _job(
        {"basic": _arm([{"text": "x", "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}])},
        measured_known_cost_usd=math.nan,
        elapsed_seconds=math.inf,
    )
    packet = build_scoring_packet([job], ["FX-COST-1"])
    params = packet["cases"][0]["blinded_arms"][0]["run_params"]
    assert params["measured_known_cost_usd"] is None
    assert params["elapsed_seconds"] is None


def test_inputs_not_mutated_and_strict_json():
    jobs, cases = _fx_cost_jobs()
    snapshot = copy.deepcopy((jobs, cases))
    packet = build_scoring_packet(jobs, cases)
    assert (jobs, cases) == snapshot
    json.dumps(packet, sort_keys=True, allow_nan=False)


def test_mismatched_lengths_rejected():
    job = _job({"basic": _arm([])})
    with pytest.raises(ValueError):
        build_scoring_packet([job], ["FX-COST-1", "FX-APPROVAL-1"])
