"""Slice D1 tests -- pure unit tests over hand-built dict rows only.

No DB, no harness, no API, no UI. Nothing is mocked.
Covers AD01, AD02, AD03, AD05, AD07, AD08 per
current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md section 7.3.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import adaptive_pack
from app.adaptive_pack import (
    ALLOWLIST_KINDS,
    ALLOWLIST_SOURCES,
    PROPOSAL_STAMP,
    check_source_eligibility,
    compose_layers,
    render_maturity,
    validate_content_kind,
    validate_overlay_entry,
    validate_proposal,
)


def _eligible_row(kind, **overrides):
    row = {
        "record_type": kind,
        "confirmed": True,
        "record_ids": ["D-1"],
        "session_only": False,
        "cross_project": False,
    }
    if kind == "recommendation_outcome":
        row["outcome"] = "accepted"
    if kind == "repeated_gap":
        row["scope_ids"] = ["Q-1", "Q-2"]
    row.update(overrides)
    return row


def _valid_proposal(**overrides):
    proposal = {
        "wording": "Proposed: reorder clarification cards. %s" % PROPOSAL_STAMP,
        "suggested_next_actions": ["pilot the reordered cards on the next question"],
        "uncertainty_limits": "only two questions observed so far; may not generalize",
        "evidence_refs": ["D-1", "D-2"],
    }
    proposal.update(overrides)
    return proposal


# ---- AD02: allowlist A1-A6 accepted -------------------------------------

@pytest.mark.parametrize(
    "kind",
    [
        "confirmed_decision",
        "evidence_card",
        "recommendation_outcome",
        "user_correction",
        "registered_source",
        "repeated_gap",
    ],
)
def test_ad02_allowlist_sources_accepted(kind):
    ok, reason = check_source_eligibility(_eligible_row(kind))
    assert ok, reason


def test_ad02_rejected_recommendation_outcome_accepted():
    ok, reason = check_source_eligibility(
        _eligible_row("recommendation_outcome", outcome="rejected")
    )
    assert ok, reason


# ---- AD02: every denylist category rejected with reason ------------------

DENY_ROWS = {
    "ai_draft": {"record_type": "ai_draft", "confirmed": True, "record_ids": ["X-1"]},
    "unconfirmed_proposal": {
        "record_type": "unconfirmed_proposal",
        "confirmed": True,
        "record_ids": ["X-1"],
    },
    "shadow_output": {"record_type": "shadow_output", "confirmed": True, "record_ids": ["X-1"]},
    "mock_output": {"record_type": "mock_output", "confirmed": True, "record_ids": ["X-1"]},
    "session_only": {"record_type": "session_only", "confirmed": True, "record_ids": ["X-1"]},
    "cross_project": {"record_type": "cross_project", "confirmed": True, "record_ids": ["X-1"]},
    "uncited": {"record_type": "uncited", "confirmed": True, "record_ids": ["X-1"]},
    "pseudo_confirmation": {
        "record_type": "pseudo_confirmation",
        "confirmed": True,
        "record_ids": ["X-1"],
    },
}


@pytest.mark.parametrize("kind,row", list(DENY_ROWS.items()))
def test_ad02_denylist_rejected_with_reason(kind, row):
    ok, reason = check_source_eligibility(dict(row))
    assert not ok
    assert isinstance(reason, str) and reason.strip()


def test_ad02_unconfirmed_decision_rejected():
    ok, reason = check_source_eligibility(_eligible_row("confirmed_decision", confirmed=False))
    assert not ok and reason.strip()


def test_ad02_session_only_flag_rejected():
    ok, reason = check_source_eligibility(_eligible_row("evidence_card", session_only=True))
    assert not ok and reason.strip()


def test_ad02_cross_project_flag_rejected():
    ok, reason = check_source_eligibility(_eligible_row("evidence_card", cross_project=True))
    assert not ok and reason.strip()


@pytest.mark.parametrize("via", ["timeout", "silence", "nudge", "revisit", "preparation"])
def test_ad02_pseudo_confirmation_rejected(via):
    row = _eligible_row("confirmed_decision", confirmed_via=via)
    ok, reason = check_source_eligibility(row)
    assert not ok and reason.strip()


def test_ad02_single_occurrence_gap_rejected():
    row = _eligible_row("repeated_gap", scope_ids=["Q-1"])
    ok, reason = check_source_eligibility(row)
    assert not ok and reason.strip()


def test_ad02_recommendation_without_verdict_rejected():
    row = _eligible_row("recommendation_outcome")
    del row["outcome"]
    ok, reason = check_source_eligibility(row)
    assert not ok and reason.strip()


# ---- AD02 citation gate --------------------------------------------------

@pytest.mark.parametrize("bad_ids", [[], None, "D-1", ["", "  "], ["D-1", 42]])
def test_ad02_citation_gate(bad_ids):
    row = _eligible_row("evidence_card", record_ids=bad_ids)
    ok, reason = check_source_eligibility(row)
    assert not ok and reason.strip()
    entry = {"kind": "checklist_item", "text": "verify units", "record_ids": bad_ids}
    ok, reason = validate_overlay_entry(entry)
    assert not ok and reason.strip()


def test_ad02_every_accepted_entry_carries_cited_ids():
    entry = {
        "kind": "terminology_mapping",
        "text": "use release-note wording for the variable",
        "record_ids": ["D-7"],
    }
    ok, reason = validate_overlay_entry(entry)
    assert ok, reason


# ---- AD03: no activation path --------------------------------------------

def test_ad03_no_auto_activate_function():
    names = [n for n in dir(adaptive_pack) if not n.startswith("__")]
    for name in names:
        lowered = name.lower()
        assert "auto" not in lowered, name
        assert "activat" not in lowered, name
        assert "adopt" not in lowered, name
        assert "apply" not in lowered, name


def test_ad03_silence_or_timeout_cannot_produce_approved():
    with pytest.raises(ValueError):
        render_maturity("approved")
    with pytest.raises(ValueError):
        render_maturity("approved", version=None)


def test_ad03_approved_requires_explicit_version_and_refs():
    with pytest.raises(ValueError):
        render_maturity("approved", version={"overlay_version": "v2"})
    with pytest.raises(ValueError):
        render_maturity("approved", version={"evidence_refs": ["D-1"]})
    # AD03 / section 3 step 6 / section 6.1: approval requires benchmark
    # report refs filed alongside evidence refs -- evidence refs alone fail.
    with pytest.raises(ValueError):
        render_maturity(
            "approved",
            version={"overlay_version": "v2", "evidence_refs": ["D-1", "D-2"]},
        )
    with pytest.raises(ValueError):
        render_maturity(
            "approved",
            version={"overlay_version": "v2", "evidence_refs": ["D-1", "D-2"],
                     "benchmark_refs": []},
        )
    line = render_maturity(
        "approved",
        version={"overlay_version": "v2", "evidence_refs": ["D-1", "D-2"],
                 "benchmark_refs": ["bm-1"]},
    )
    assert "v2" in line and "D-1" in line and "bm-1" in line


@pytest.mark.parametrize(
    "bad_benchmark_refs",
    ["", None, "bm-1", ("bm-1",), [None], ["  "], {"refs": ["bm-1"]}, []],
)
def test_ad03_approved_refuses_invalid_benchmark_refs(bad_benchmark_refs):
    # Same _valid_ids-equivalent rule as evidence refs and the D2 gate:
    # a real list of non-blank strings only. Bare strings pass
    # ``isinstance(x, str)`` but are not a filed ref list; tuples/dicts are
    # not lists; None/blank entries are not filed refs.
    with pytest.raises(ValueError):
        render_maturity(
            "approved",
            version={"overlay_version": "v2", "evidence_refs": ["D-1"],
                     "benchmark_refs": bad_benchmark_refs},
        )


def test_ad03_deprecated_needs_no_benchmark_refs():
    # Design requires benchmark refs for approval only: deprecated renders
    # with version + reason and preserved history, benchmark refs absent.
    line = render_maturity(
        "deprecated",
        version={"overlay_version": "v1", "evidence_refs": ["D-1"]},
        reason="benchmark showed no effect",
    )
    assert "v1" in line and "benchmark showed no effect" in line


# ---- AD05: no weight-training artifact; public API surface ----------------

EXPECTED_PUBLIC_API = {
    "check_source_eligibility",
    "validate_content_kind",
    "validate_overlay_entry",
    "validate_proposal",
    "compose_layers",
    "render_maturity",
    "contains_forbidden_claims",
    "ALLOWLIST_SOURCES",
    "DENYLIST_SOURCES",
    "ALLOWLIST_KINDS",
    "MATURITY_STATES",
    "PSEUDO_CONFIRMATIONS",
    "PROPOSAL_STAMP",
}


def test_ad05_public_api_surface_exact():
    assert set(adaptive_pack.__all__) == EXPECTED_PUBLIC_API


def test_ad05_no_training_or_activation_api():
    for name in dir(adaptive_pack):
        lowered = name.lower()
        assert "train" not in lowered, name
        assert "fit(" not in lowered and lowered != "fit", name
        assert "weight" not in lowered, name
        assert "gradient" not in lowered, name
        assert "embed" not in lowered, name


# ---- AD07: maturity honesty -----------------------------------------------

def test_ad07_cold_start_invents_nothing():
    line = render_maturity("cold_start")
    assert "base guidance" in line
    for invented in ("accomplish", "improv", "success", "complete", "v1", "active"):
        assert invented not in line.lower()


def test_ad07_accumulating_counts_only():
    line = render_maturity(
        "accumulating",
        counts={"confirmed_decisions": 3, "evidence_cards": 5},
    )
    assert "3" in line and "5" in line
    assert "Counts only" in line
    for fabricated in ("score:", "deadline:", "%", "guaranteed"):
        assert fabricated not in line.lower()


def test_ad07_proposed_shows_stamp():
    line = render_maturity("proposed", proposal=_valid_proposal())
    assert PROPOSAL_STAMP in line
    assert "pilot the reordered cards" in line


def test_ad07_proposed_requires_explicit_proposal():
    with pytest.raises(ValueError):
        render_maturity("proposed")
    with pytest.raises(ValueError):
        render_maturity("proposed", proposal=_valid_proposal(suggested_next_actions=[]))


def test_ad07_deprecated_restores_prior_version_with_reason():
    with pytest.raises(ValueError):
        render_maturity("deprecated", version={"overlay_version": "v1"})
    with pytest.raises(ValueError):
        render_maturity(
            "deprecated", version={"overlay_version": "v1"}, reason="  "
        )
    # Deprecated keeps no benchmark-ref requirement: evidence refs suffice.
    line = render_maturity(
        "deprecated",
        version={"overlay_version": "v1", "evidence_refs": ["D-1"]},
        reason="benchmark showed no effect",
    )
    assert "v1" in line and "benchmark showed no effect" in line
    # The legacy bare version+reason shape still renders (no refs invented).
    line = render_maturity(
        "deprecated",
        version={"overlay_version": "v1"},
        reason="benchmark showed no effect",
    )
    assert "v1" in line and "benchmark showed no effect" in line


def test_ad07_unknown_state_rejected():
    with pytest.raises(ValueError):
        render_maturity("launched")


# ---- AD08: guards ----------------------------------------------------------

def test_ad08_single_anecdote_pattern_rejected():
    entry = {
        "kind": "evidence_pattern",
        "text": "source S resolves gap class G",
        "record_ids": ["E-1"],
        "is_pattern_claim": True,
    }
    ok, reason = validate_overlay_entry(entry)
    assert not ok and ">=2" in reason


def test_ad08_repeated_pattern_accepted():
    entry = {
        "kind": "evidence_pattern",
        "text": "source S resolves gap class G",
        "record_ids": ["E-1", "E-2"],
        "is_pattern_claim": True,
    }
    ok, reason = validate_overlay_entry(entry)
    assert ok, reason


@pytest.mark.parametrize(
    "bad_text",
    [
        "this is 100% correct",
        "guaranteed improvement",
        "confirmed-correct mapping",
        "you must adopt this",
        "quality score 9 out of 10",
        "95% fewer rounds",
        "fix by the deadline Friday",
        "complete ASAP",
    ],
)
def test_ad08_certainty_deadline_score_wording_rejected(bad_text):
    entry = {"kind": "checklist_item", "text": bad_text, "record_ids": ["D-1"]}
    ok, reason = validate_overlay_entry(entry)
    assert not ok, bad_text
    assert reason.strip()
    proposal = _valid_proposal(
        wording="Proposed: %s. %s" % (bad_text, PROPOSAL_STAMP)
    )
    ok, reason = validate_proposal(proposal)
    assert not ok, bad_text
    assert reason.strip()


# ---- Proposal-shape validator ----------------------------------------------

def test_proposal_shape_valid():
    ok, reason = validate_proposal(_valid_proposal())
    assert ok, reason


@pytest.mark.parametrize(
    "actions",
    [[], ["first action", "second action"], ["   "], "just do it"],
)
def test_proposal_shape_action_count(actions):
    ok, reason = validate_proposal(_valid_proposal(suggested_next_actions=actions))
    assert not ok and reason.strip()


def test_proposal_shape_empty_uncertainty():
    ok, reason = validate_proposal(_valid_proposal(uncertainty_limits="  "))
    assert not ok and reason.strip()


def test_proposal_shape_missing_refs():
    ok, reason = validate_proposal(_valid_proposal(evidence_refs=[]))
    assert not ok and reason.strip()


def test_proposal_shape_missing_stamp():
    ok, reason = validate_proposal(_valid_proposal(wording="Proposed: reorder cards."))
    assert not ok and reason.strip()


# ---- Content-kind closed allowlist ------------------------------------------

def test_content_kinds_allowlisted():
    assert set(ALLOWLIST_KINDS) == {
        "terminology_mapping",
        "variable_mapping",
        "evidence_pattern",
        "clarification_priority",
        "checklist_item",
        "tool_preference",
        "routing_preference",
        "methodological_caution",
    }


@pytest.mark.parametrize("kind", ["model_draft", "score", "deadline", "new_tool", "", None])
def test_content_kinds_outside_rejected(kind):
    ok, reason = validate_content_kind(kind)
    assert not ok and reason.strip()
    entry = {"kind": kind, "text": "something", "record_ids": ["D-1"]}
    ok, reason = validate_overlay_entry(entry)
    assert not ok and reason.strip()


# ---- AD01: three-block composition ------------------------------------------

def _blocks():
    base = "TRUSTED DOMAIN GUIDANCE: check units before analysis."
    overlay = [
        {
            "kind": "methodological_caution",
            "text": "release-N note does not establish release-M meaning",
            "record_ids": ["D-3", "D-9"],
        }
    ]
    pin = {
        "project_id": "P-1",
        "pack_id": "social_science",
        "pack_version": "1.0.0",
        "question_id": "Q-1",
    }
    return compose_layers(base, overlay, pin)


def test_ad01_three_distinct_labeled_blocks_in_order():
    text = _blocks()
    base_h = "### BLOCK 1/3 -- BASE PACK GUIDANCE (immutable, versioned)"
    over_h = "### BLOCK 2/3 -- OVERLAY GUIDANCE (project-confirmed deltas; proposals only)"
    sess_h = "### BLOCK 3/3 -- SESSION CONTEXT (ephemeral; this session only)"
    assert base_h in text and over_h in text and sess_h in text
    assert text.index(base_h) < text.index(over_h) < text.index(sess_h)


def test_ad01_overlay_grants_nothing():
    text = compose_layers(
        "base text",
        [
            {
                "kind": "tool_preference",
                "text": "prefer registered_evidence_read first",
                "record_ids": ["D-2"],
            }
        ],
        {"question_id": "Q-1"},
    )
    assert "Overlay grants no tools, scope, or budget" in text
    assert "registered_evidence_read" in text  # content preserved, only labeled
    assert "TRUSTED DOMAIN GUIDANCE" not in text  # base block keeps caller text only


def test_ad01_base_text_preserved_verbatim():
    base = "TRUSTED DOMAIN GUIDANCE: check units before analysis."
    text = compose_layers(base, [], {"question_id": "Q-1"})
    assert base in text
    assert "(no overlay entries -- base guidance only)" in text


def test_ad01_session_pin_rendered():
    text = _blocks()
    assert "Q-1" in text and "social_science" in text and "P-1" in text


def test_ad01_empty_inputs_stay_honest():
    text = compose_layers("", [], {})
    assert "(no base guidance provided)" in text
    assert "(no overlay entries -- base guidance only)" in text
    assert "(no active session)" in text


def test_ad01_allowlist_sources_documented():
    assert set(ALLOWLIST_SOURCES) == {
        "confirmed_decision",
        "evidence_card",
        "recommendation_outcome",
        "user_correction",
        "registered_source",
        "repeated_gap",
    }
