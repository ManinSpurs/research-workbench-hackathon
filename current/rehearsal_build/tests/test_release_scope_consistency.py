"""One release-scope rule for both paths that read a note's declared release.

Synthetic packets only: no provider, no network, no paid call. Covers the
shared parser/check, the legacy harness boolean, grounding.validate, the
receipt summary block and the explicit NOT_CHECKED state when the question has
no release year.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import release_scope
from app.ai import grounding
from app.ai.receipt import build_receipt
from app.harness import p_release_mismatch

RESULT = {
    "answer": "est_cost is the applicant-reported estimate.",
    "claims": [
        {"text": "est_cost is an estimate.", "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}
    ],
    "unresolved": ["Whether approval_date implies final approval."],
    "proposed_variables": [
        {"name": "est_cost", "dataset_id": "fx/permits_2023.csv", "kind": "actual"}
    ],
    "next_action": "Researcher reviews the result.",
}


def _packet(release, notes="applies to release 2023", evidence_id="S-1:est_cost"):
    return {
        "release": release,
        "sources": [
            {"evidence_id": evidence_id, "source_id": "S-1", "notes": notes},
        ],
        "datasets": [{"dataset_id": "fx/permits_2023.csv", "columns": ["est_cost"]}],
        "allowed_sources": ["S-1"],
        "blocked_sources": [],
    }


def _result_citing(evidence_id="S-1:est_cost", kind="fact"):
    result = json.loads(json.dumps(RESULT))
    result["claims"] = [
        {"text": "Cited claim.", "evidence_ids": [evidence_id], "kind": kind}
    ]
    return json.dumps(result)


# --------------------------------------------------------------- shared parser
@pytest.mark.parametrize(
    ("note", "year"),
    [
        ("applies to release 2025", "2025"),
        ("applies to release 2025 only", "2025"),
        ("Applies To Release 2025", "2025"),
        ("APPLIES TO RELEASE 2025 ONLY", "2025"),
        ("line 1. applies to release 2023 only.", "2023"),
        ("no scope note here", None),
        ("", None),
        (None, None),
        (2025, None),
        ("applies to release 20255", None),
    ],
)
def test_declared_release_parser(note, year):
    assert release_scope.declared_release(note) == year


@pytest.mark.parametrize(
    ("release", "year"),
    [
        ("2023", "2023"),
        (" 2023 ", "2023"),
        (2023, "2023"),
        # Format-only normalization: four ASCII digits are the year format, so
        # no extra plausibility rule is invented here.
        ("0000", "0000"),
        ("23", None),
        ("", None),
        ("  ", None),
        (None, None),
        (True, None),
        (2023.0, None),
    ],
)
def test_question_release_normalization(release, year):
    assert release_scope.question_release(release) == year


# --------------------------------------------------------- shared check states
@pytest.mark.parametrize(
    ("note", "release", "status"),
    [
        ("applies to release 2023", "2023", release_scope.MATCH),
        ("applies to release 2023 only", "2023", release_scope.MATCH),
        ("APPLIES TO RELEASE 2023 ONLY", "2023", release_scope.MATCH),
        ("applies to release 2025", "2023", release_scope.MISMATCH),
        ("applies to release 2025 only", "2023", release_scope.MISMATCH),
        ("Applies to Release 2025 Only", 2023, release_scope.MISMATCH),
        ("no declared scope", "2023", release_scope.UNDECLARED),
        ("", "2023", release_scope.UNDECLARED),
        ("applies to release 2025", None, release_scope.NOT_CHECKED),
        ("applies to release 2023 only", None, release_scope.NOT_CHECKED),
        ("no declared scope", None, release_scope.NOT_CHECKED),
        ("applies to release 2025", "", release_scope.NOT_CHECKED),
    ],
)
def test_year_applicability_states(note, release, status):
    scope = release_scope.year_applicability(note, release)
    assert scope["status"] == status
    assert scope["question_release"] == release_scope.question_release(release)


def test_missing_question_release_never_reports_match():
    # The uncheckable case must not read as a pass, even when the note's year
    # would have matched a plausible project release.
    for note in ("applies to release 2023", "applies to release 2023 only", "mix"):
        assert release_scope.year_applicability(note, None)["status"] == release_scope.NOT_CHECKED
        assert release_scope.release_mismatch(note, None) is False


# ------------------------------------------------- legacy harness boolean path
@pytest.mark.parametrize(
    ("note", "release", "mismatch"),
    [
        ("applies to release 2025 only", "2023", True),
        ("applies to release 2025", "2023", True),
        ("APPLIES TO RELEASE 2025 ONLY", "2023", True),
        ("applies to release 2023", "2023", False),
        ("applies to release 2023 only", "2023", False),
        ("applies to release 2025 only", None, False),
        ("no declared scope", "2023", False),
        ("applies to release 2025 only", "", False),
    ],
)
def test_legacy_boolean_api_preserved(note, release, mismatch):
    assert p_release_mismatch(note, release) is mismatch


# --------------------------------------------- grounding path agrees with it
@pytest.mark.parametrize(
    ("note", "release"),
    [
        ("applies to release 2023", "2023"),
        ("applies to release 2023 only", "2023"),
        ("applies to release 2025", "2023"),
        ("applies to release 2025 only", "2023"),
        ("Applies To Release 2025 Only", "2023"),
        ("APPLIES TO RELEASE 2023", 2023),
        ("no declared scope", "2023"),
        ("applies to release 2025 only", None),
        ("applies to release 2023", None),
    ],
)
def test_grounding_and_harness_agree(note, release):
    _, checks = grounding.validate(_result_citing(), _packet(release, note), [])
    cited_other_release = any("another release" in e for e in checks["errors"])
    assert cited_other_release is p_release_mismatch(note, release)


def test_same_note_same_verdict_in_both_paths():
    # The reported defect: one note, one release, two answers.
    note = "applies to release 2025"
    _, checks = grounding.validate(_result_citing(), _packet("2023", note), [])
    assert p_release_mismatch(note, "2023") is True
    assert any("another release" in e for e in checks["errors"])
    assert not checks["structural_valid"]


def test_cross_release_warning_kept_and_not_a_semantic_verdict():
    # A cross-release citation is a scope warning. It is not suppressed here,
    # and nothing in the block claims the claim's meaning is false.
    _, checks = grounding.validate(_result_citing(), _packet("2023", "applies to release 2025 only"), [])
    assert checks["release_year_applicability"]["status"] == release_scope.MISMATCH
    assert checks["release_year_applicability"]["mismatched_evidence_ids"] == ["S-1:est_cost"]
    assert checks["semantic_correctness"] == "NOT_SCORED"
    assert checks["false_citation_rate"] == "HUMAN_REVIEW_REQUIRED"


def test_uncheckable_year_is_exposed_not_passed():
    _, checks = grounding.validate(_result_citing(), _packet(None, "applies to release 2025 only"), [])
    assert checks["release_year_applicability"]["status"] == release_scope.NOT_CHECKED
    assert checks["release_year_applicability"]["mismatched_evidence_ids"] == []
    assert checks["release_year_applicability"]["question_release"] is None
    # Application of the year check: nothing was compared, so no year error.
    assert not any("another release" in e for e in checks["errors"])


# ------------------------------------ structural ID/variable checks independent
def test_structural_checks_independent_of_release_year():
    packet = _packet(None, "applies to release 2025 only")
    _, checks = grounding.validate(_result_citing("INVENTED:nope"), packet, [])
    assert not checks["structural_valid"]
    assert any("Unobserved evidence ID" in e for e in checks["errors"])
    assert checks["release_year_applicability"]["status"] == release_scope.NOT_CHECKED

    bad_variable = json.loads(_result_citing())
    bad_variable["proposed_variables"] = [
        {"name": "not_a_column", "dataset_id": "fx/permits_2023.csv", "kind": "actual"}
    ]
    _, checks = grounding.validate(json.dumps(bad_variable), packet, [])
    assert any("Actual variable not present" in e for e in checks["errors"])

    good = _packet(None, "applies to release 2025 only")
    _, checks = grounding.validate(_result_citing(), good, [])
    assert checks["structural_valid"]


def test_legacy_metrics_keys_unchanged():
    _, checks = grounding.validate(_result_citing(), _packet("2023"), [])
    assert {
        "structural_valid", "errors", "citation_count", "invalid_citation_count",
        "citation_error_rate", "linked_claim_fraction", "semantic_correctness",
        "false_citation_rate",
    } <= set(checks)


# ------------------------------------------------------ receipt summary block
def _receipt(checks):
    return build_receipt(
        "completed",
        {"outputs": {"harness": {"result": json.loads(json.dumps(RESULT)), "grounding": checks}}},
        reservation_usd=0.005,
        model="muse-spark-1.3",
        data_use_mode="Standard",
    )


def test_receipt_carries_uncheckable_year_status():
    _, checks = grounding.validate(_result_citing(), _packet(None, "applies to release 2025 only"), [])
    summary = _receipt(checks)["arm_summaries"]["harness"]["grounding"]
    assert summary["release_year_applicability"]["status"] == "NOT_CHECKED"
    assert summary["release_year_applicability"]["question_release"] is None
    assert "not passed" in summary["release_year_applicability"]["note"]
    assert summary["structural_valid"] is True


def test_receipt_omits_block_for_legacy_records():
    legacy = {
        "structural_valid": True,
        "errors": [],
        "citation_count": 1,
        "invalid_citation_count": 0,
        "citation_error_rate": 0.0,
        "linked_claim_fraction": 1.0,
        "semantic_correctness": "NOT_SCORED",
        "false_citation_rate": "HUMAN_REVIEW_REQUIRED",
    }
    summary = _receipt(legacy)["arm_summaries"]["harness"]["grounding"]
    assert "release_year_applicability" not in summary


def test_receipt_rejects_unknown_scope_status_and_shapes():
    base = {"structural_valid": True, "errors": []}
    for block in (
        None,
        "NOT_CHECKED",
        {"status": "PASSED"},
        {"status": 1},
        {},
    ):
        summary = _receipt({**base, "release_year_applicability": block})["arm_summaries"]["harness"]["grounding"]
        assert "release_year_applicability" not in summary, block
    sanitized = _receipt({**base, "release_year_applicability": {
        "status": "MISMATCH",
        "question_release": "20xx",
        "mismatched_evidence_ids": ["DD-2023:est_cost", "sk-ant-SECRET-7c29", 5],
    }})["arm_summaries"]["harness"]["grounding"]["release_year_applicability"]
    assert sanitized == {
        "status": "NOT_CHECKED",
        "question_release": None,
        "mismatched_evidence_ids": ["DD-2023:est_cost"],
        "note": (
            "No usable question release: release-year applicability of the cited "
            "sources was NOT checked (not passed)."
        ),
    }


def test_receipt_never_keeps_checked_status_without_a_usable_recorded_year():
    for status in ("MATCH", "MISMATCH", "UNDECLARED", "NOT_CHECKED"):
        for year in (None, "", "20xx", "２０２３", "٢٠٢٣", 2023, True, " 2023 "):
            block = {"status": status, "question_release": year}
            summary = _receipt({"release_year_applicability": block})["arm_summaries"]["harness"]["grounding"]
            scope = summary["release_year_applicability"]
            assert scope["status"] == "NOT_CHECKED", block
            assert scope["question_release"] is None
            assert "not passed" in scope["note"]
        summary = _receipt({"release_year_applicability": {"status": status}})["arm_summaries"]["harness"]["grounding"]
        assert summary["release_year_applicability"]["status"] == "NOT_CHECKED"

        block = {"status": status, "question_release": "2023"}
        summary = _receipt({"release_year_applicability": block})["arm_summaries"]["harness"]["grounding"]
        assert summary["release_year_applicability"]["status"] == status
        assert summary["release_year_applicability"]["question_release"] == "2023"
