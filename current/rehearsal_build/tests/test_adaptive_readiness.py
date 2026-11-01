"""Slice D2 tests -- read-only adaptation-readiness composer.

The composer takes plain dicts shaped like the public outputs of
``pack_memory``, ``pending_clarifications``, ``briefing`` and
``launch_briefing`` plus an optional proposal/version dict. Tests seed a
throwaway SQLite DB through ``app.store.SCHEMA`` + public ``Harness``
methods only (``register_project`` / ``capture_evaluation_input`` /
``decide`` / ``briefing`` / ``pack_memory`` / ``pending_clarifications`` --
never harness internals, never the real project DB). Question capture and
decisions need no model call; the ``Harness`` falls back to the offline
adapter, so this file makes zero model calls, uses mock transports only,
and consumes no credits. Gap inputs the harness only creates via a model
run are supplied as hand-built plain dicts, which the composer accepts by
design.

Covers the D2 acceptance surface of
current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md sections 6-7: no
cross-project/pack leakage, missing timestamps degrade to count-only +
``unknown``, no deadline wording, proposals never rendered as decisions,
honest readiness states, benchmark shape check, repeated-gap threshold,
and read-only composition (identical DB dump before/after).
"""
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import adaptive_readiness as ar
from app import store

SOCIAL = "social_science"
ENGINEERING = "engineering_measurement"

APPLE = "zzapplea1"
ORANGE = "zzorangeb2"
GAP_APPLE = "zzgapapple awaiting column mapping"
GAP_ORANGE = "zzgapother awaiting release note"
GAP_SOLO = "zzgapsolo single unit question"


@pytest.fixture
def harness(tmp_path):
    from app.harness import Harness
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    db_path = str(tmp_path / "wb.db")
    yield Harness(db_path, data)


def seed_project(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def capture(h, pid, text, pack_id=SOCIAL):
    return h.capture_evaluation_input(pid, text, None, pack_id)


def decide(h, pid, qid, verdict, reason):
    return h.decide(pid, qid, verdict, reason, None)


def entry_for(h, pid, pack_id, records=None, pending=None):
    identity = h.pack_memory(pid, pack_id)
    return {
        "project_id": pid,
        "pack_id": pack_id,
        "pack_version": identity["pack_version"],
        "manifest_sha256": identity["manifest_sha256"],
        "briefing": h.briefing(pid, pack_id),
        "pack_memory": identity,
        "pending": pending if pending is not None else h.pending_clarifications(pid),
        "records": records or [],
    }


def pending_with(*gaps, pid="pid-x", pack_id=SOCIAL):
    return {
        "project_id": pid,
        "clarifications": [
            {"cq_id": "cq-%d" % i, "question_id": "q-%d" % i, "task_id": "t-%d" % i,
             "status": "pending", "clarification": {"gap": gap},
             "context": {"pack_id": pack_id}}
            for i, gap in enumerate(gaps)
        ],
    }


def valid_proposal(**overrides):
    proposal = {
        "wording": "Proposed: reorder clarification cards. %s" % ar.PROPOSAL_BADGE,
        "suggested_next_actions": ["pilot the reordered cards on the next question"],
        "uncertainty_limits": "only two questions observed so far; may not generalize",
        "evidence_refs": ["dec-1", "dec-2"],
    }
    proposal.update(overrides)
    return proposal


def approved_version(**overrides):
    version = {"status": "approved", "overlay_version": "v3",
               "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": ["bm-1"]}
    version.update(overrides)
    return version


# Mirrors the validator's forbidden set (adaptive_readiness.py): bare "due" as
# in "due to" is legitimate causal wording in stored reasons, so only "due
# by/date" counts as deadline wording here.
DEADLINE_SCAN = re.compile(r"\bdeadline\b|\boverdue\b|\bdue\s+(by|date)\b|\basap\b", re.IGNORECASE)


def assert_no_deadline_wording(row):
    assert not DEADLINE_SCAN.search(json.dumps(row, ensure_ascii=False)), row["lines"]


# ---- no cross-project / cross-pack leakage --------------------------------

def test_no_cross_project_leakage_in_composed_rows(harness):
    pid_a, pid_b = seed_project(harness), seed_project(harness)
    qa = capture(harness, pid_a, "Can I study filing-to-start time %s?" % APPLE)
    qb = capture(harness, pid_b, "Can I study time to final approval %s?" % ORANGE)
    decide(harness, pid_a, qa, "adopt", "reason %s marker" % APPLE)
    decide(harness, pid_b, qb, "reject", "reason %s marker" % ORANGE)
    records_a = [
        {"record_type": "evidence_card", "project_id": pid_a, "pack_id": SOCIAL,
         "confirmed": True, "record_ids": ["ev-a1"], "note": "note %s" % APPLE},
        {"record_type": "registered_source", "project_id": pid_b, "pack_id": SOCIAL,
         "confirmed": True, "record_ids": ["src-b1"], "note": "note %s" % ORANGE},
        {"record_type": "ai_draft", "project_id": pid_a, "pack_id": SOCIAL,
         "confirmed": True, "record_ids": ["draft-1"]},
    ]
    records_b = [
        {"record_type": "user_correction", "project_id": pid_b, "pack_id": SOCIAL,
         "confirmed": True, "record_ids": ["corr-b1"], "note": "note %s" % ORANGE},
    ]
    out = ar.compose_readiness([
        entry_for(harness, pid_a, SOCIAL, records=records_a,
                  pending=pending_with(GAP_APPLE, pid=pid_a)),
        entry_for(harness, pid_b, SOCIAL, records=records_b,
                  pending=pending_with(GAP_ORANGE, pid=pid_b)),
    ])
    assert out["row_count"] == 2 and out["model_calls"] == 0
    row_a = next(r for r in out["rows"] if r["project_id"] == pid_a)
    row_b = next(r for r in out["rows"] if r["project_id"] == pid_b)
    dump_a, dump_b = json.dumps(row_a, ensure_ascii=False), json.dumps(row_b, ensure_ascii=False)
    assert ORANGE not in dump_a and APPLE not in dump_b
    assert GAP_ORANGE not in dump_a and GAP_APPLE not in dump_b
    # counts stayed on their own side: the cross-tagged source counted for B only
    assert row_a["counts"]["registered_sources"] == 0
    assert row_b["counts"]["registered_sources"] == 0  # fed via records_b? no: source record was in records_a
    assert row_a["counts"]["confirmed_decisions"] == 1
    assert row_b["counts"]["confirmed_decisions"] == 1
    assert row_a["counts"]["evidence_cards"] == 1
    assert row_b["counts"]["user_corrections"] == 1
    assert row_a["ineligible_ignored"] >= 2  # draft + cross-project source


def test_no_cross_pack_leakage_same_project(harness):
    pid = seed_project(harness)
    qs = capture(harness, pid, "Social question %s?" % APPLE, SOCIAL)
    qe = capture(harness, pid, "Engineering question %s?" % ORANGE, ENGINEERING)
    decide(harness, pid, qs, "adopt", "social reason %s" % APPLE)
    decide(harness, pid, qe, "adopt", "engineering reason %s" % ORANGE)
    out = ar.compose_readiness([
        entry_for(harness, pid, SOCIAL, pending=pending_with(GAP_APPLE, pid=pid, pack_id=SOCIAL)),
        entry_for(harness, pid, ENGINEERING, pending=pending_with(GAP_ORANGE, pid=pid, pack_id=ENGINEERING)),
    ])
    row_s = next(r for r in out["rows"] if r["pack_id"] == SOCIAL)
    row_e = next(r for r in out["rows"] if r["pack_id"] == ENGINEERING)
    assert ORANGE not in json.dumps(row_s, ensure_ascii=False)
    assert APPLE not in json.dumps(row_e, ensure_ascii=False)
    assert GAP_ORANGE not in json.dumps(row_s, ensure_ascii=False)
    assert GAP_APPLE not in json.dumps(row_e, ensure_ascii=False)
    assert row_s["counts"]["confirmed_decisions"] == 1
    assert row_e["counts"]["confirmed_decisions"] == 1


# ---- missing timestamps degrade to count-only + unknown --------------------

def test_missing_timestamps_yield_unknown_age(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    entry = entry_for(harness, pid, SOCIAL)
    entry["briefing"]["parts"]["waiting_on_you"] = [{"text": "Decide on: something", "saved_at": None}]
    entry["briefing"]["parts"]["where_you_left_off"] = [
        {"text": "Adopt: something. Reason: recorded.", "saved_at": "not-a-time"}]
    entry.pop("verified_at", None)
    entry["briefing"].pop("verified_at", None)
    row = ar.compose_row(**{k: v for k, v in entry.items()
                             if k in ("project_id", "pack_id", "pack_version", "manifest_sha256",
                                      "briefing", "pack_memory", "pending", "records")})
    assert row["oldest_age"] == ar.UNKNOWN == "unknown"
    assert row["counts"]["confirmed_decisions"] == 1
    assert row["readiness"] == "accumulating"
    assert_no_deadline_wording(row)


# ---- no deadline wording ----------------------------------------------------

def test_no_deadline_wording_anywhere(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "defer", "waiting on a mapping choice")
    pending = pending_with(GAP_APPLE, GAP_APPLE, pid=pid)
    pending["clarifications"][0]["created_at"] = store.now()
    out = ar.compose_readiness(
        [entry_for(harness, pid, SOCIAL,
                   records=[{"record_type": "recommendation_outcome", "project_id": pid,
                              "pack_id": SOCIAL, "confirmed": True,
                              "record_ids": ["dec-1"], "outcome": "accepted"}],
                   pending=pending)],
        proposals={(pid, SOCIAL): valid_proposal()},
        benchmark_reports={(pid, SOCIAL): {"cases": [
            {"case_ref": "F1", "outcome": "supported"},
            {"case_ref": "F2", "outcome": "partial"}]}},
    )
    row = out["rows"][0]
    assert_no_deadline_wording(row)
    for line in row["lines"]:
        assert not DEADLINE_SCAN.search(line), line


def test_recorded_date_is_quoted_only_when_passed(harness):
    pid = seed_project(harness)
    row_plain = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                               pending=harness.pending_clarifications(pid))
    assert "quoted_recorded_date" not in row_plain
    assert_no_deadline_wording(row_plain)
    row_quoted = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                                pending=harness.pending_clarifications(pid),
                                recorded_date="2026-10-20")
    assert row_quoted["quoted_recorded_date"] == "2026-10-20"
    assert "2026-10-20" in " ".join(row_quoted["lines"])


# ---- proposals never render as decisions -------------------------------------

def test_proposal_renders_with_badge_never_as_decision(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid),
                         proposal=valid_proposal())
    assert row["readiness"] == "proposed"
    assert row["proposal_badge"] == ar.PROPOSAL_BADGE
    text = json.dumps(row, ensure_ascii=False)
    assert ar.PROPOSAL_BADGE in text
    # The badge itself reads "not decided"; strip it before checking that no
    # decision wording (verdict/decided/decision record) leaked into the row.
    stripped = text.replace(ar.PROPOSAL_BADGE, "")
    assert "verdict" not in stripped
    assert "decided" not in stripped.lower()
    assert "decision_id" not in stripped and "decision_committed" not in stripped
    assert row["proposal_action"] == "pilot the reordered cards on the next question"
    assert_no_deadline_wording(row)


def test_invalid_proposal_falls_back_without_state_invention(harness):
    pid = seed_project(harness)
    bad = valid_proposal(suggested_next_actions=["one", "two"])
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid), proposal=bad)
    assert row["readiness"] == "cold"
    assert row["proposal_badge"] is None and row["proposal_action"] is None
    no_stamp = valid_proposal(wording="Proposed: reorder cards, no stamp here.")
    row2 = ar.compose_row(pid, SOCIAL, pending=harness.pending_clarifications(pid),
                          proposal=no_stamp)
    assert row2["readiness"] == "cold"


# ---- honest readiness states --------------------------------------------------

def test_readiness_states_honest(harness):
    pid_empty = seed_project(harness)
    row_cold = ar.compose_row(pid_empty, SOCIAL, briefing=harness.briefing(pid_empty, SOCIAL),
                              pending=harness.pending_clarifications(pid_empty))
    assert row_cold["readiness"] == "cold"
    assert row_cold["eligible_total"] == 0
    assert "overlay_version" not in json.dumps(row_cold)

    pid_full = seed_project(harness)
    qid = capture(harness, pid_full, "Can I study filing-to-start time?")
    decide(harness, pid_full, qid, "adopt", "a recorded reason")
    row_acc = ar.compose_row(
        pid_full, SOCIAL, briefing=harness.briefing(pid_full, SOCIAL),
        pending=harness.pending_clarifications(pid_full),
        records=[{"record_type": "evidence_card", "project_id": pid_full,
                  "pack_id": SOCIAL, "confirmed": True, "record_ids": ["ev-1"]},
                 {"record_type": "unconfirmed_proposal", "project_id": pid_full,
                  "pack_id": SOCIAL, "confirmed": True, "record_ids": ["p-1"]},
                 {"record_type": "confirmed_decision", "project_id": pid_full,
                  "pack_id": SOCIAL, "confirmed": False, "record_ids": ["d-9"]}])
    assert row_acc["readiness"] == "accumulating"
    assert row_acc["eligible_total"] == 2  # briefing decision + evidence ref
    assert row_acc["ineligible_ignored"] == 2  # denylist + unconfirmed

    # approved/deprecated are impossible without a passed-in version dict
    for label in ("proposed", "approved", "deprecated"):
        assert row_acc["readiness"] != label
    assert "overlay" not in " ".join(row_acc["lines"]).lower() or "Overlay" not in " ".join(row_acc["lines"])

    row_approved = ar.compose_row(
        pid_full, SOCIAL, briefing=harness.briefing(pid_full, SOCIAL),
        pending=harness.pending_clarifications(pid_full), version=approved_version())
    assert row_approved["readiness"] == "approved"
    assert "v3" in row_approved["version_line"]

    row_dep = ar.compose_row(
        pid_full, SOCIAL, briefing=harness.briefing(pid_full, SOCIAL),
        pending=harness.pending_clarifications(pid_full),
        version=approved_version(status="deprecated", reason="benchmark showed no effect"))
    assert row_dep["readiness"] == "deprecated"
    assert "benchmark showed no effect" in row_dep["version_line"]
    assert_no_deadline_wording(row_dep)


# ---- benchmark shape check flags invented scores -------------------------------

def test_benchmark_shape_check_flags_scores():
    clean = {"cases": [{"case_ref": "F1", "outcome": "supported"},
                       {"case_ref": "F2", "outcome": "partial"}]}
    ok = ar.check_benchmark_report(clean)
    assert ok["valid"] and ok["case_count"] == 2

    scored = {"cases": [{"case_ref": "F1", "outcome": "supported", "score": 0.9}]}
    bad = ar.check_benchmark_report(scored)
    assert not bad["valid"]
    assert any("score" in reason for reason in bad["reasons"])
    assert "0.9" not in json.dumps(bad)  # values never rendered

    percent = {"cases": [{"case_ref": "F1", "outcome": "supported (95%)"}]}
    bad_pct = ar.check_benchmark_report(percent)
    assert not bad_pct["valid"]

    missing = {"cases": [{"case_ref": "F1"}]}
    assert not ar.check_benchmark_report(missing)["valid"]
    assert not ar.check_benchmark_report({"cases": []})["valid"]
    assert not ar.check_benchmark_report("not-a-dict")["valid"]


def test_benchmark_invalid_report_marks_row(harness):
    pid = seed_project(harness)
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid),
                         benchmark_report={"cases": [{"case_ref": "F1", "outcome": "supported",
                                                     "certainty": "high"}]})
    assert row["benchmark"]["valid"] is False
    assert any("certainty" in reason for reason in row["benchmark"]["reasons"])
    assert_no_deadline_wording(row)


# ---- repeated-gap threshold -----------------------------------------------------

def test_repeated_gap_threshold(harness):
    pid = seed_project(harness)
    pending = pending_with(GAP_APPLE, GAP_APPLE, GAP_SOLO, pid=pid)
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL), pending=pending)
    tally = row["repeated_gaps_tally"]
    assert tally["patterns"] == [{"gap_class": GAP_APPLE, "occurrences": 2}]
    assert tally["single_occurrences"] == 1
    assert tally["single_classes"] == [GAP_SOLO]
    assert row["counts"]["repeated_gaps"] == 3

    answered = pending_with(GAP_APPLE, pid=pid)
    answered["clarifications"][0]["status"] = "answered"
    row2 = ar.compose_row(pid, SOCIAL, pending=answered)
    assert row2["repeated_gaps_tally"]["patterns"] == []
    assert row2["repeated_gaps_tally"]["single_occurrences"] == 0

    cross_pack = pending_with(GAP_APPLE, GAP_APPLE, pid=pid, pack_id=ENGINEERING)
    row3 = ar.compose_row(pid, SOCIAL, pending=cross_pack)
    assert row3["repeated_gaps_tally"]["patterns"] == []
    assert row3["counts"]["repeated_gaps"] == 0


def test_gap_record_merges_with_pending_counts(harness):
    pid = seed_project(harness)
    pending = pending_with(GAP_APPLE, pid=pid)
    records = [{"record_type": "repeated_gap", "project_id": pid, "pack_id": SOCIAL,
                "confirmed": True, "record_ids": ["cq-9"], "gap_class": GAP_APPLE,
                "scope_ids": ["t-x", "t-y"]},
               {"record_type": "repeated_gap", "project_id": pid, "pack_id": SOCIAL,
                "confirmed": True, "record_ids": ["cq-10"], "gap_class": ""}]
    row = ar.compose_row(pid, SOCIAL, pending=pending, records=records)
    assert row["repeated_gaps_tally"]["patterns"] == [{"gap_class": GAP_APPLE, "occurrences": 2}]
    assert row["ineligible_ignored"] == 1  # empty gap class rejected


# ---- read-only: identical DB dump before/after ------------------------------------

def test_compose_is_read_only(harness):
    pid_a, pid_b = seed_project(harness), seed_project(harness)
    qa = capture(harness, pid_a, "Can I study filing-to-start time %s?" % APPLE)
    decide(harness, pid_a, qa, "adopt", "reason %s" % APPLE)

    def dump():
        with sqlite3.connect(harness.db.execute("PRAGMA database_list").fetchone()[2]) as db:
            return "\n".join(db.iterdump())

    before = dump()
    out = ar.compose_readiness([
        entry_for(harness, pid_a, SOCIAL, pending=pending_with(GAP_APPLE, GAP_APPLE, pid=pid_a)),
        entry_for(harness, pid_b, SOCIAL, pending=pending_with(GAP_ORANGE, pid=pid_b)),
    ], proposals={(pid_a, SOCIAL): valid_proposal()},
        benchmark_reports={(pid_a, SOCIAL): {"cases": [{"case_ref": "F1", "outcome": "supported"}]}})
    ar.check_benchmark_report({"cases": [{"case_ref": "F9", "outcome": "partial", "score": 3}]})
    after = dump()
    assert before == after
    assert out["row_count"] == 2


# ---- regression: reviewer findings (distinct-scope patterns, D1 alignment) ----

def _pending_same_question_twice(gap, pid, pack_id=SOCIAL):
    return {
        "project_id": pid,
        "clarifications": [
            {"cq_id": "cq-same-%d" % i, "question_id": "q-same", "task_id": "t-same",
             "status": "pending", "clarification": {"gap": gap},
             "context": {"pack_id": pack_id}}
            for i in range(2)
        ],
    }


def test_same_question_repeat_is_not_a_pattern(harness):
    pid = seed_project(harness)
    row = ar.compose_row(pid, SOCIAL,
                         pending=_pending_same_question_twice(GAP_APPLE, pid))
    assert row["repeated_gaps_tally"]["patterns"] == []
    assert row["repeated_gaps_tally"]["single_occurrences"] == 2
    assert row["counts"]["repeated_gaps"] == 2


def test_distinct_questions_repeat_is_a_pattern(harness):
    pid = seed_project(harness)
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=pending_with(GAP_APPLE, GAP_APPLE, pid=pid))
    assert row["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": GAP_APPLE, "occurrences": 2}]


def test_repeated_gap_record_needs_distinct_scope_ids():
    solo = {"record_type": "repeated_gap", "project_id": "p", "pack_id": SOCIAL,
            "confirmed": True, "record_ids": ["cq-1"],
            "gap_class": "zzz gap", "scope_ids": ["only-one"]}
    ok, reason = ar.is_eligible_record(solo, "p", SOCIAL)
    assert not ok and reason.strip()
    duo = dict(solo, scope_ids=["q-1", "q-2"])
    assert ar.is_eligible_record(duo, "p", SOCIAL)[0]
    assert not ar.is_eligible_record(dict(solo, scope_ids=None), "p", SOCIAL)[0]
    assert not ar.is_eligible_record(
        dict(solo, scope_ids=["q-1", "q-1", " "]), "p", SOCIAL)[0]


def test_d1_shaped_evidence_card_row_eligible(harness):
    pid = seed_project(harness)
    record = {"record_type": "evidence_card", "project_id": pid, "pack_id": SOCIAL,
              "confirmed": True, "record_ids": ["ev-1"]}
    assert ar.is_eligible_record(record, pid, SOCIAL)[0]
    assert "evidence_card" in ar.ALLOWLIST_RECORD_TYPES
    assert "confirmed_evidence_ref" not in ar.ALLOWLIST_RECORD_TYPES
    row = ar.compose_row(pid, SOCIAL,
                         pending=harness.pending_clarifications(pid),
                         records=[record])
    assert row["counts"]["evidence_cards"] == 1
    assert row["readiness"] == "accumulating"


def test_deprecated_version_reason_with_deadline_wording_rejected(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    bad = approved_version(status="deprecated",
                           reason="must ship before the deadline, guaranteed 100%")
    ok, note, _status = ar.validate_version_shape(bad)
    assert not ok and note.strip()
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid), version=bad)
    assert row["readiness"] == "accumulating"
    assert_no_deadline_wording(row)


def test_version_reason_must_wording_rejected():
    ok, _, _ = ar.validate_version_shape(
        approved_version(status="deprecated", reason="you must adopt this now"))
    assert not ok


@pytest.mark.parametrize("bad_benchmark_refs",
                         ["", None, "bm-1", ("bm-1",), [None], ["  "],
                          {"refs": ["bm-1"]}, []])
def test_approved_refuses_invalid_benchmark_ref_shapes(harness, bad_benchmark_refs):
    # Fail-closed D2 gate: every non-list or blank-entry benchmark_refs shape
    # is refused for approved in validate_version_shape AND degrades the row
    # instead of inventing an approved state.
    bad_version = approved_version(benchmark_refs=bad_benchmark_refs)
    ok, note, status = ar.validate_version_shape(bad_version)
    assert not ok and status is None and "enchmark" in note and note.strip()
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid),
                         version=bad_version)
    assert row["readiness"] == "accumulating"
    assert row["version_line"] is None


def test_deprecated_needs_no_benchmark_refs(harness):
    # Design requires benchmark refs for approval only: a deprecated version
    # with evidence refs + reason and NO benchmark_refs key validates and
    # renders as deprecated.
    version = {"status": "deprecated", "overlay_version": "v1",
               "reason": "benchmark showed no effect",
               "evidence_refs": ["dec-1"]}
    ok, _, status = ar.validate_version_shape(version)
    assert ok and status == "deprecated"
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid),
                         version=version)
    assert row["readiness"] == "deprecated"
    assert "v1" in row["version_line"] and "benchmark showed no effect" in row["version_line"]
    missing = dict(version)
    del missing["evidence_refs"]
    ok_missing, _, status_missing = ar.validate_version_shape(missing)
    assert not ok_missing and status_missing is None


@pytest.mark.parametrize("wording", [
    "You must adopt this now. %s",
    "This is confirmed-correct. %s",
    "Guaranteed improvement here. %s",
    "Scores improve 20 percent. %s",
])
def test_proposal_rejects_d1_forbidden_wording(harness, wording):
    pid = seed_project(harness)
    bad = valid_proposal(wording=wording % ar.PROPOSAL_BADGE)
    ok, note = ar.validate_proposal_shape(bad)
    assert not ok and note.strip()
    row = ar.compose_row(pid, SOCIAL, pending=harness.pending_clarifications(pid),
                         proposal=bad)
    assert row["readiness"] == "cold"  # no records: falls back without invention
    assert_no_deadline_wording(row)


@pytest.mark.parametrize("wording", [
    "Ship before the deadline. %s",
    "Do this ASAP. %s",
    "Finish due by Friday. %s",
    "This is overdue. %s",
])
def test_proposal_rejects_deadline_wording(harness, wording):
    pid = seed_project(harness)
    bad = valid_proposal(wording=wording % ar.PROPOSAL_BADGE)
    ok, note = ar.validate_proposal_shape(bad)
    assert not ok and note.strip()
    row = ar.compose_row(pid, SOCIAL, pending=harness.pending_clarifications(pid),
                         proposal=bad)
    assert row["readiness"] == "cold"  # no records: falls back without invention
    # The diagnostic readiness note names the forbidden category, so scan only
    # the rendered content lines: the proposal's deadline wording itself must
    # not be rendered there.
    content = [line for line in row["lines"] if line != row["readiness_note"]]
    assert not DEADLINE_SCAN.search(" ".join(content)), content


def test_deadline_proposal_falls_back_to_accumulating(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    bad = valid_proposal(wording="Ship before the deadline. %s" % ar.PROPOSAL_BADGE)
    ok, note = ar.validate_proposal_shape(bad)
    assert not ok and note.strip()
    row = ar.compose_row(pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
                         pending=harness.pending_clarifications(pid), proposal=bad)
    assert row["readiness"] == "accumulating"  # eligible records: fallback, no invention
    content = [line for line in row["lines"] if line != row["readiness_note"]]
    assert not DEADLINE_SCAN.search(" ".join(content)), content


def test_benchmark_flags_neutral_key_numeric():
    bad = ar.check_benchmark_report(
        {"cases": [{"case_ref": "F1", "outcome": "supported", "result": 0.95}]})
    assert not bad["valid"]
    assert any("numeric" in reason for reason in bad["reasons"])
    assert "0.95" not in json.dumps(bad)  # values never rendered
    assert ar.check_benchmark_report(
        {"cases": [{"case_ref": "F1", "outcome": "supported",
                    "passed": True, "reviewer": "human"}]})["valid"]


def test_processing_status_not_counted_as_unresolved_gap(harness):
    pid = seed_project(harness)
    pending = pending_with(GAP_APPLE, GAP_APPLE, pid=pid)
    for item in pending["clarifications"]:
        item["status"] = "processing"
    row = ar.compose_row(pid, SOCIAL, pending=pending)
    assert row["repeated_gaps_tally"]["patterns"] == []
    assert row["counts"]["repeated_gaps"] == 0


def test_deferred_retryable_counted_unknown_excluded(harness):
    pid = seed_project(harness)
    pending = pending_with(GAP_APPLE, GAP_APPLE, GAP_SOLO, pid=pid)
    pending["clarifications"][0]["status"] = "deferred"
    pending["clarifications"][1]["status"] = "retryable"
    pending["clarifications"][2]["status"] = "unknown"
    row = ar.compose_row(pid, SOCIAL, pending=pending)
    assert row["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": GAP_APPLE, "occurrences": 2}]
    assert row["repeated_gaps_tally"]["single_occurrences"] == 0
    assert row["repeated_gaps_tally"]["single_classes"] == []
    assert row["counts"]["repeated_gaps"] == 2  # solo gap with unknown status excluded


def test_no_grant_note_on_all_row_shapes(harness):
    pid = seed_project(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    cold_pid = seed_project(harness)
    rows = {
        "cold": ar.compose_row(cold_pid, SOCIAL),
        "accumulating": ar.compose_row(
            pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
            pending=harness.pending_clarifications(pid)),
        "proposed": ar.compose_row(
            pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
            pending=harness.pending_clarifications(pid),
            proposal=valid_proposal()),
        "approved": ar.compose_row(
            pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
            pending=harness.pending_clarifications(pid),
            version=approved_version()),
        "deprecated": ar.compose_row(
            pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
            pending=harness.pending_clarifications(pid),
            version=approved_version(status="deprecated", reason="no effect seen")),
    }
    assert [rows[label]["readiness"] for label in
            ("cold", "accumulating", "proposed", "approved", "deprecated")] == \
        ["cold", "accumulating", "proposed", "approved", "deprecated"]
    for label, row in rows.items():
        assert row["no_grant_note"] == ar.NO_GRANT_NOTE, label
        assert ar.NO_GRANT_NOTE in row["lines"], label
        assert_no_deadline_wording(row)
