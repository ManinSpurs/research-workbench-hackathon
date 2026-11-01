"""AD04/AD06 gap coverage -- no retroactive reinterpretation; swap + isolation.
Covers current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md acceptance tests AD04 and
AD06, which the D1/D2 suites (test_adaptive_pack.py, test_adaptive_readiness.py)
do not address directly.

AD04 -- No retroactive reinterpretation: old questions/decisions/briefings keep
their pinned pack identity and wording after an overlay version is rendered
approved; manifest drift still raises Conflict; the loop stays
propose -> benchmark -> approve -> render -> rollback, with no activation path.

AD06 -- Swap + isolation: one reasoning route serves both packs; switching packs
changes only new work and the selected view; a Q-row contains no P-text, counts,
reasons, cards, or overlay content.

SCOPE HONESTY (design section 7.1): overlay activation (the version store +
adopt wiring) is CONCEPTUAL-ONLY and does not exist in this codebase. Nothing
here certifies preservation-under-activation, because there is no activation to
preserve under. What this file certifies is the D1/D2 read-only slice only:
compose/render calls make no DB writes (absence of coupling), approval-shaped
renders require explicit caller-supplied dicts carrying evidence AND benchmark
refs, and rows are keyed to their own (project_id, pack_id) scope. The render
tests below are rendering-copy checks, never activation claims.

Only ordinary Harness capture/decide calls touch the DB; overlay versions and
proposals are caller-supplied dicts (the codebase exposes no activation path by
design). Zero model calls.
"""
import ast
import inspect
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import adaptive_pack as ap
from app import adaptive_readiness as ar
from app import packs
from app.harness import Conflict, Harness

SOCIAL = "social_science"
ENGINEERING = "engineering_measurement"


@pytest.fixture
def harness(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    yield Harness(str(tmp_path / "wb.db"), data)


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def capture(h, pid, text, pack_id=SOCIAL):
    return h.capture_evaluation_input(pid, text, None, pack_id)


def decide(h, pid, qid, verdict, reason):
    return h.decide(pid, qid, verdict, reason, None)


def decisions_rows(h):
    return [dict(r) for r in h.db.execute("SELECT * FROM decisions ORDER BY rowid")]


def pin_row(h, qid):
    return dict(h.db.execute("SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone())


# ---- AD04: base packs are immutable -------------------------------------------

def test_ad04_base_pack_releases_are_immutable():
    first = packs.get_pack(SOCIAL)
    identity_before = first.identity()
    with pytest.raises(ValidationError):
        first.instructions = "Changed interpretation"
    assert packs.get_pack(SOCIAL).identity() == identity_before
    # A changed instruction set is a NEW version with a new manifest hash,
    # never an in-place edit of the released pin.
    altered = first.model_copy(update={"instructions": "Changed interpretation"})
    assert altered.identity()["manifest_sha256"] != identity_before["manifest_sha256"]
    assert altered.identity()["pack_id"] == identity_before["pack_id"]


# ---- AD04: approved overlay render leaves pinned history untouched -------------

def _db_dump(h):
    with sqlite3.connect(h.db.execute("PRAGMA database_list").fetchone()[2]) as db:
        return "\n".join(db.iterdump())


def test_ad04_approved_render_makes_no_db_writes(harness):
    # Load-bearing property, asserted honestly (not just call-signature
    # shape): the D1/D2 slice modules' source imports contain no harness,
    # api, store, sqlite3, or activation path, so their render/compose calls
    # have no handle to any DB at all. The behavioural dump check below is a
    # real paired check: it brackets render/compose calls with full SQLite
    # iterdumps of the fixture DB, so any write would fail the comparison.
    for module, label in ((ap, "adaptive_pack"), (ar, "adaptive_readiness")):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.update(part.lower() for part in node.module.split("."))
                imported.update(alias.name.split(".")[0].lower() for alias in node.names)
        for forbidden in ("harness", "api", "store", "sqlite3",
                          "frontend", "adaptive_store"):
            assert forbidden not in imported, (label, forbidden)
        assert "sqlite3" not in source, label
        exported = {name.lower() for name in dir(module)}
        for name in ("activate", "adopt", "apply_overlay", "write_overlay",
                     "commit_overlay", "persist", "save", "write"):
            assert name not in exported, (label, name)
        for token in (".execute(", ".executemany(", ".commit(",
                      "INSERT INTO", "DELETE FROM"):
            assert token not in source, (label, token)
    pid = seed(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "filing date establishes the start")
    before = _db_dump(harness)
    proposal = {
        "wording": "Proposed: reorder clarification cards. %s" % ap.PROPOSAL_STAMP,
        "suggested_next_actions": ["pilot the reordered cards on the next question"],
        "uncertainty_limits": "only two questions observed so far; may not generalize",
        "evidence_refs": ["dec-1", "dec-2"],
    }
    ok, _ = ap.validate_proposal(proposal)
    assert ok
    report = {"cases": [{"case_ref": "F1", "outcome": "supported"},
                        {"case_ref": "F2", "outcome": "partial"}]}
    assert ar.check_benchmark_report(report)["valid"]
    ar.compose_row(
        pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
        pending=harness.pending_clarifications(pid),
        version={"status": "approved", "overlay_version": "v2",
                 "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": ["bm-1"]},
        benchmark_report=report,
    )
    ap.render_maturity(
        "approved",
        version={"overlay_version": "v2", "evidence_refs": ["dec-1", "bm-1"],
                 "benchmark_refs": ["bm-1"]},
    )
    assert _db_dump(harness) == before
    # D1 approved without benchmark refs is refused -- the same bypass the D2
    # gate closes -- and fails closed without touching the DB.
    with pytest.raises(ValueError):
        ap.render_maturity(
            "approved",
            version={"overlay_version": "v2", "evidence_refs": ["dec-1", "bm-1"]},
        )
    assert _db_dump(harness) == before


def test_ad04_render_leaves_pinned_history_byte_identical(harness):
    pid = seed(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "filing date establishes the start")
    pin_before = harness.question_context(qid)
    stored_before = pin_row(harness, qid)
    decisions_before = decisions_rows(harness)
    briefing_before = json.dumps(harness.briefing(pid, SOCIAL), sort_keys=True)

    proposal = {
        "wording": "Proposed: reorder clarification cards. %s" % ap.PROPOSAL_STAMP,
        "suggested_next_actions": ["pilot the reordered cards on the next question"],
        "uncertainty_limits": "only two questions observed so far; may not generalize",
        "evidence_refs": ["dec-1", "dec-2"],
    }
    ok, _ = ap.validate_proposal(proposal)
    assert ok
    report = {"cases": [{"case_ref": "F1", "outcome": "supported"},
                        {"case_ref": "F2", "outcome": "partial"}]}
    assert ar.check_benchmark_report(report)["valid"]
    row = ar.compose_row(
        pid, SOCIAL, briefing=harness.briefing(pid, SOCIAL),
        pending=harness.pending_clarifications(pid),
        version={"status": "approved", "overlay_version": "v2",
                 "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": ["bm-1"]},
        benchmark_report=report,
    )
    assert row["readiness"] == "approved"
    assert "v2" in row["version_line"]
    assert row["benchmark"]["valid"] is True

    # Old work is byte-identical: same pin, same decision rows, same briefing.
    # Canonical-byte comparison for the pinned history/context structures
    # (json.dumps with sort_keys=True), not just dict ==, so key order or
    # encoding drift would fail the check.
    assert json.dumps(harness.question_context(qid), sort_keys=True) == \
        json.dumps(pin_before, sort_keys=True)
    assert pin_row(harness, qid) == stored_before
    assert decisions_rows(harness) == decisions_before
    assert json.dumps(harness.briefing(pid, SOCIAL), sort_keys=True) == briefing_before
    assert "filing date establishes the start" in briefing_before


def test_ad04_approved_rendering_copy_marks_new_work_only(harness):
    line = ap.render_maturity(
        "approved",
        version={"overlay_version": "v2", "evidence_refs": ["D-1", "D-2"],
                 "benchmark_refs": ["bm-1"]},
    )
    assert "v2" in line and "D-1" in line and "bm-1" in line
    assert "old work unchanged" in line
    # A rendering copy, not an activation record: it names the version and
    # refs only; it cites no stored record and alters no DB row.


# ---- AD04: drift still raises Conflict after an overlay render ------------------

def test_ad04_manifest_drift_still_raises_conflict(harness, monkeypatch):
    pid = seed(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    pin_before = pin_row(harness, qid)
    # Render an approved overlay first: it must not heal or mask drift.
    ap.render_maturity(
        "approved",
        version={"overlay_version": "v2", "evidence_refs": ["D-1"],
                 "benchmark_refs": ["bm-1"]},
    )
    original = packs.get_pack(SOCIAL)
    monkeypatch.setitem(
        packs.PACKS, SOCIAL, original.model_copy(update={"instructions": "Changed interpretation"})
    )
    with pytest.raises(Conflict, match="pack changed"):
        harness.question_context(qid)
    # The stored pin itself was never rewritten by the drift or the render.
    assert pin_row(harness, qid) == pin_before


# ---- AD04: loop order propose -> benchmark -> approve -> rollback ---------------

def test_ad04_approval_without_evidence_refs_is_refused():
    ok, note, status = ar.validate_version_shape(
        {"status": "approved", "overlay_version": "v2",
         "evidence_refs": [], "benchmark_refs": ["bm-1"]})
    assert not ok and status is None and note.strip()
    # Refusal degrades the row instead of inventing an approved state.
    row = ar.compose_row("p-x", SOCIAL,
                         version={"status": "approved", "overlay_version": "v2",
                                  "evidence_refs": [], "benchmark_refs": ["bm-1"]})
    assert row["readiness"] in ("cold", "accumulating")
    assert row["version_line"] is None


def test_ad04_approval_without_benchmark_refs_is_refused():
    # Design AD03: "benchmark refs filed before approval" (section 7.3);
    # section 3 step 6 commits the delta "with its evidence refs, benchmark
    # report refs, approver, and timestamp"; section 6.1 Approved shows
    # "overlay vN active with evidence/benchmark refs". Approval with
    # evidence refs but NO benchmark refs is therefore refused.
    ok, note, status = ar.validate_version_shape(
        {"status": "approved", "overlay_version": "v2",
         "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": []})
    assert not ok and status is None and "enchmark" in note
    row = ar.compose_row("p-x", SOCIAL,
                         version={"status": "approved", "overlay_version": "v2",
                                  "evidence_refs": ["dec-1", "bm-1"],
                                  "benchmark_refs": []})
    assert row["readiness"] in ("cold", "accumulating")
    assert row["version_line"] is None
    # Zero refs and missing refs fail identically: nothing may stand in for
    # filed benchmark refs.
    missing = dict(version={"status": "approved", "overlay_version": "v2",
                            "evidence_refs": ["dec-1"]})
    ok2, _, status2 = ar.validate_version_shape(missing["version"])
    assert not ok2 and status2 is None


def test_ad04_approval_with_empty_version_dict_is_refused():
    ok, _, status = ar.validate_version_shape({})
    assert not ok and status is None
    ok, _, status = ar.validate_version_shape(
        {"status": "approved", "evidence_refs": ["dec-1"], "benchmark_refs": ["bm-1"]})
    assert not ok and status is None  # no overlay_version: never invented


def test_ad04_silence_timeout_never_activates():
    # Design sections 2.2/3 step 5: timeouts, silence, nudges, revisit
    # satisfaction, preparation completion are pseudo-confirmation, never
    # confirmation. No version/status dict can be conjured from them: an
    # absent version dict yields only cold/accumulating, never approved.
    for pseudo in ("timeout", "silence", "nudge", "revisit", "preparation"):
        row = ar.compose_row("p-x", SOCIAL, records=[
            {"record_type": "evidence_card", "project_id": "p-x", "pack_id": SOCIAL,
             "confirmed": True, "confirmed_via": pseudo, "record_ids": ["ev-1"]}])
        assert row["readiness"] == "cold"  # pseudo-confirmed row ignored
        assert row["version_line"] is None
        row2 = ar.compose_row("p-x", SOCIAL, version=None, proposal=None)
        assert row2["readiness"] == "cold"


def test_ad04_approved_requires_explicit_adopt_and_refs(harness):
    # Design section 3 step 5 + section 6.2 authority-creep guard: only an
    # explicit adopt confirms; proposal-shaped and deprecated-shaped dicts
    # can never render as approved.
    pid = seed(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    entry = {"project_id": pid, "pack_id": SOCIAL,
             "briefing": harness.briefing(pid, SOCIAL),
             "pending": harness.pending_clarifications(pid)}
    proposal_only = {
        "wording": "Proposed: reorder cards. %s" % ar.PROPOSAL_BADGE,
        "suggested_next_actions": ["pilot the reorder next"],
        "uncertainty_limits": "one question only",
        "evidence_refs": ["dec-1", "dec-2"],
    }
    row_p = ar.compose_row(**dict(entry, proposal=proposal_only))
    assert row_p["readiness"] == "proposed"  # never approved without a version
    row_d = ar.compose_row(
        **dict(entry, version={"status": "deprecated",
                               "overlay_version": "v1", "reason": "no effect",
                               "evidence_refs": ["dec-1"], "benchmark_refs": ["bm-1"]}))
    assert row_d["readiness"] == "deprecated"
    assert "v2" not in (row_d["version_line"] or "")


def test_ad04_loop_order_propose_benchmark_approve_rollback():
    record = {"record_type": "evidence_card", "confirmed": True, "record_ids": ["E-1"]}
    ok, _ = ap.check_source_eligibility(record)
    assert ok
    entry = {"kind": "checklist_item", "text": "verify units",
             "record_ids": ["E-1", "E-2"], "is_pattern_claim": True}
    ok, _ = ap.validate_overlay_entry(entry)
    assert ok
    proposal = {
        "wording": "Proposed: order checks from confirmed misses. %s" % ap.PROPOSAL_STAMP,
        "suggested_next_actions": ["pilot the ordered checklist on the next question"],
        "uncertainty_limits": "two misses only; may not generalize",
        "evidence_refs": ["E-1", "E-2"],
    }
    ok, _ = ap.validate_proposal(proposal)
    assert ok
    report = {"cases": [{"case_ref": "F1", "outcome": "supported"}]}
    assert ar.check_benchmark_report(report)["valid"]
    approved = ap.render_maturity(
        "approved",
        version={"overlay_version": "v2", "evidence_refs": ["E-1", "E-2"],
                 "benchmark_refs": ["bm-1"]},
    )
    assert "v2" in approved and "bm-1" in approved
    rolled_back = ap.render_maturity(
        "deprecated", version={"overlay_version": "v1"},
        reason="benchmark showed no effect",
    )
    assert "v1" in rolled_back and "benchmark showed no effect" in rolled_back


# ---- AD04: non-benchmark refs are not benchmark refs --------------------------

def test_ad04_zero_non_benchmark_refs_prove_the_gate():
    # Both gates now refuse: an approval carrying evidence refs but zero
    # benchmark refs is refused by the D2 gate (validate_version_shape) AND
    # by the D1 render gate (render_maturity's approved branch), and must
    # never render as an active overlay line. Quote: AD03 "benchmark refs
    # filed before approval".
    ev_only = {"status": "approved", "overlay_version": "v2",
               "evidence_refs": ["dec-1", "dec-2"], "benchmark_refs": []}
    ok, note, status = ar.validate_version_shape(ev_only)
    assert not ok and status is None and "enchmark" in note
    row = ar.compose_row("p-x", SOCIAL, version=ev_only)
    assert row["readiness"] in ("cold", "accumulating")
    assert row["version_line"] is None
    with pytest.raises(ValueError):
        ap.render_maturity(
            "approved",
            version={"overlay_version": "v2",
                     "evidence_refs": ["dec-1", "dec-2"], "benchmark_refs": []},
        )
    with pytest.raises(ValueError):
        ap.render_maturity(
            "approved",
            version={"overlay_version": "v2",
                     "evidence_refs": ["dec-1", "dec-2"]},
        )
    # Conversely a version WITH benchmark refs passes both gates and renders.
    gated = dict(ev_only, benchmark_refs=["bm-1"])
    ok, _, status = ar.validate_version_shape(gated)
    assert ok and status == "approved"
    row_ok = ar.compose_row("p-x", SOCIAL, version=gated)
    assert row_ok["readiness"] == "approved"
    assert "v2" in row_ok["version_line"] and "bm-1" in row_ok["version_line"]
    line = ap.render_maturity(
        "approved",
        version={"overlay_version": "v2",
                 "evidence_refs": ["dec-1", "dec-2"], "benchmark_refs": ["bm-1"]},
    )
    assert "v2" in line and "bm-1" in line


def test_ad04_deprecated_needs_no_benchmark_refs_in_both_paths(harness):
    # Design requires benchmark refs for approval only: deprecated renders
    # with version + reason and NO benchmark refs in both the D2 gate and
    # the D1 render path.
    deprecated = {"status": "deprecated", "overlay_version": "v1",
                  "reason": "no effect", "evidence_refs": ["dec-1"]}
    ok, _, status = ar.validate_version_shape(deprecated)
    assert ok and status == "deprecated"
    row = ar.compose_row("p-x", SOCIAL, version=deprecated)
    assert row["readiness"] == "deprecated"
    assert "v1" in row["version_line"] and "no effect" in row["version_line"]
    pid = seed(harness)
    qid = capture(harness, pid, "Can I study filing-to-start time?")
    decide(harness, pid, qid, "adopt", "a recorded reason")
    line = ap.render_maturity(
        "deprecated", version={"overlay_version": "v1"},
        reason="benchmark showed no effect",
    )
    assert "v1" in line and "benchmark showed no effect" in line


@pytest.mark.parametrize("bad_benchmark_refs",
                         ["", None, "bm-1", ("bm-1",), [None], ["  "],
                          {"refs": ["bm-1"]}, []])
def test_ad04_approved_refuses_invalid_benchmark_ref_shapes(bad_benchmark_refs):
    # Fail-closed edge values: every non-list or blank-entry shape is
    # refused for approved in BOTH gates -- nothing stands in for filed
    # benchmark refs. Deprecated without benchmark refs still passes the
    # D2 gate (with a reason) since refs are an approval precondition only.
    bad_version = {"status": "approved", "overlay_version": "v2",
                   "evidence_refs": ["dec-1"],
                   "benchmark_refs": bad_benchmark_refs}
    ok, note, status = ar.validate_version_shape(bad_version)
    assert not ok and status is None and "enchmark" in note
    degraded = ar.compose_row("p-x", SOCIAL, version=bad_version)
    assert degraded["readiness"] in ("cold", "accumulating")
    assert degraded["version_line"] is None
    with pytest.raises(ValueError):
        ap.render_maturity(
            "approved",
            version={"overlay_version": "v2", "evidence_refs": ["dec-1"],
                     "benchmark_refs": bad_benchmark_refs},
        )
    deprecated_ok = {"status": "deprecated", "overlay_version": "v1",
                     "reason": "no effect", "evidence_refs": ["dec-1"]}
    ok_dep, _, status_dep = ar.validate_version_shape(deprecated_ok)
    assert ok_dep and status_dep == "deprecated"


# ---- AD06: shared-route capability check for both packs -------------------------

def test_ad06_shared_route_check_passes_for_both_packs(harness):
    # What this proves (and all it proves): the one shared capability binding
    # ``mock_text`` is accepted for BOTH packs alike, and a non-text binding
    # is rejected for both alike -- i.e. packs share the route check rather
    # than owning routes. It proves nothing about which route any content
    # flows through.
    pid = seed(harness)
    qid_s = capture(harness, pid, "Social question about cost?", SOCIAL)
    qid_e = capture(harness, pid, "Engineering question about units?", ENGINEERING)
    for qid, pack_id in ((qid_s, SOCIAL), (qid_e, ENGINEERING)):
        pin = harness.question_context(qid)
        assert pin["pack_id"] == pack_id
        assert pin["capability_id"] == "mock_text"
    assert packs.require_text_binding("mock_text") == "mock_text"
    # A non-text route is rejected for BOTH packs alike: packs never own a route.
    for pack_id in (SOCIAL, ENGINEERING):
        with pytest.raises(Conflict):
            harness._pin_context("qid-probe-%s" % pack_id, pack_id, "transcription")


def test_ad06_cross_pack_same_project_isolation_via_readiness(harness):
    # Same project, different packs: rows are keyed (project_id, pack_id), so
    # the SOCIAL row under project P must carry no ENGINEERING content and
    # vice versa. Here the gap classes are the leak probes: they are the ONE
    # channel compose_row actually copies into rows (pattern lines), planted
    # with project+pack-scoped markers.
    pid = seed(harness)
    qs = capture(harness, pid, "Social question about spending?")
    qe = capture(harness, pid, "Engineering question about load?", ENGINEERING)
    decide(harness, pid, qs, "adopt", "social reason recorded")
    decide(harness, pid, qe, "adopt", "engineering reason recorded")
    gap_s = "zzleakgap social-shape awaiting budget mapping"
    gap_e = "zzleakgap engineering-shape awaiting unit mapping"

    def pending_two(gap, pid, pack_id, qid):
        return {
            "project_id": pid,
            "clarifications": [
                {"cq_id": "cq-%s-%d" % (pack_id, i), "question_id": "%s-%d" % (qid, i),
                 "task_id": "t-%s-%d" % (pack_id, i), "status": "pending",
                 "clarification": {"gap": gap}, "context": {"pack_id": pack_id}}
                for i in range(2)
            ],
        }

    out = ar.compose_readiness([
        {"project_id": pid, "pack_id": SOCIAL,
         "briefing": harness.briefing(pid, SOCIAL),
         "pending": pending_two(gap_s, pid, SOCIAL, qs)},
        {"project_id": pid, "pack_id": ENGINEERING,
         "briefing": harness.briefing(pid, ENGINEERING),
         "pending": pending_two(gap_e, pid, ENGINEERING, qe)},
    ])
    assert out["row_count"] == 2
    row_s = next(r for r in out["rows"] if r["pack_id"] == SOCIAL)
    row_e = next(r for r in out["rows"] if r["pack_id"] == ENGINEERING)
    dump_s, dump_e = json.dumps(row_s), json.dumps(row_e)
    assert gap_s in dump_s and gap_e in dump_e  # probes landed in their own rows
    assert gap_e not in dump_s and gap_s not in dump_e
    assert row_s["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": gap_s, "occurrences": 2}]
    assert row_e["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": gap_e, "occurrences": 2}]
    # Version/proposal scoping agrees: keyed (project_id, pack_id) too.
    vp = {"status": "approved", "overlay_version": "vS",
          "evidence_refs": ["dec-s"], "benchmark_refs": ["bm-s"]}
    out2 = ar.compose_readiness([
        {"project_id": pid, "pack_id": SOCIAL,
         "briefing": harness.briefing(pid, SOCIAL)},
        {"project_id": pid, "pack_id": ENGINEERING,
         "briefing": harness.briefing(pid, ENGINEERING)},
    ], versions={(pid, SOCIAL): vp})
    row_s2 = next(r for r in out2["rows"] if r["pack_id"] == SOCIAL)
    row_e2 = next(r for r in out2["rows"] if r["pack_id"] == ENGINEERING)
    assert row_s2["readiness"] == "approved" and row_e2["readiness"] != "approved"
    assert "vS" in (row_s2["version_line"] or "")


# ---- AD06: switching changes only new work and the selected view ----------------

def test_ad06_switching_changes_only_new_work_and_view(harness, monkeypatch):
    # Compare scope isolation at one verification instant, not wall-clock drift.
    monkeypatch.setattr("app.store.now", lambda: "2026-10-28T12:00:00+00:00")
    pid = seed(harness)
    qid_s = capture(harness, pid, "Social inquiry about cost?")
    decide(harness, pid, qid_s, "adopt", "social reason recorded")
    pin_s_before = harness.question_context(qid_s)
    briefing_s_before = json.dumps(harness.briefing(pid, SOCIAL), sort_keys=True)

    qid_e = capture(harness, pid, "Engineering inquiry about measurement?", ENGINEERING)
    decide(harness, pid, qid_e, "adopt", "engineering reason recorded")

    # The social view is untouched by the later engineering work.
    assert json.dumps(harness.briefing(pid, SOCIAL), sort_keys=True) == briefing_s_before
    assert harness.question_context(qid_s) == pin_s_before
    assert [q["id"] for q in harness.pack_memory(pid, SOCIAL)["questions"]] == [qid_s]
    assert [q["id"] for q in harness.pack_memory(pid, ENGINEERING)["questions"]] == [qid_e]
    eng_briefing = json.dumps(harness.briefing(pid, ENGINEERING))
    assert "engineering reason recorded" in eng_briefing
    assert "social reason recorded" not in eng_briefing


# ---- AD06: a Q-row contains no P-text, counts, reasons, cards, or overlay ------

def test_ad06_q_row_contains_no_p_content(harness):
    # Leak probes are planted ONLY in channels compose_row actually copies
    # into rows (the composer is count-only for everything else, so markers
    # in record notes/reasons would be vacuous): gap_class strings (copied
    # into pattern lines), recorded_date (quoted verbatim), and proposal
    # fields (action/refs rendered). Each probe is scoped to project P (or
    # pack P in the cross-pack test below); the assertion is that Q rows --
    # and cross-pack rows -- contain none of them.
    pid_p, pid_q = seed(harness), seed(harness)
    qp = capture(harness, pid_p, "Can I study filing-to-start time?")
    decide(harness, pid_p, qp, "adopt", "reason zznotcopiedP alpha")
    qq = capture(harness, pid_q, "Can I study time to final approval?")
    decide(harness, pid_q, qq, "reject", "reason zznotcopiedQ beta")
    gap_p = "zzleakp awaiting column mapping"
    gap_q = "zzleakq awaiting release note"

    def pending_two(gap, pid, qid):
        return {
            "project_id": pid,
            "clarifications": [
                {"cq_id": "cq-%s-%d" % (pid, i), "question_id": "%s-%d" % (qid, i),
                 "task_id": "t-%s-%d" % (pid, i), "status": "pending",
                 "clarification": {"gap": gap}, "context": {"pack_id": SOCIAL}}
                for i in range(2)
            ],
        }

    proposal_p = {
        "wording": "Proposed: pilot zzactp cards. %s" % ar.PROPOSAL_BADGE,
        "suggested_next_actions": ["pilot zzactp reorder next"],
        "uncertainty_limits": "p history only; may not generalize",
        "evidence_refs": ["dec-p1", "dec-p2"],
    }
    records_p = [{"record_type": "evidence_card", "project_id": pid_p, "pack_id": SOCIAL,
                  "confirmed": True, "record_ids": ["ev-p1"],
                  "note": "card zznotcopiedP"}]
    records_q = [{"record_type": "evidence_card", "project_id": pid_q, "pack_id": SOCIAL,
                  "confirmed": True, "record_ids": ["ev-q1"],
                  "note": "card zznotcopiedQ"}]
    out = ar.compose_readiness([
        {"project_id": pid_p, "pack_id": SOCIAL,
         "briefing": harness.briefing(pid_p, SOCIAL),
         "pack_memory": harness.pack_memory(pid_p, SOCIAL),
         "pending": pending_two(gap_p, pid_p, qp), "records": records_p},
        {"project_id": pid_q, "pack_id": SOCIAL,
         "briefing": harness.briefing(pid_q, SOCIAL),
         "pack_memory": harness.pack_memory(pid_q, SOCIAL),
         "pending": pending_two(gap_q, pid_q, qq), "records": records_q},
    ], proposals={(pid_p, SOCIAL): proposal_p},
        recorded_dates={(pid_p, SOCIAL): "2031-zzdatep"})
    row_p = next(r for r in out["rows"] if r["project_id"] == pid_p)
    row_q = next(r for r in out["rows"] if r["project_id"] == pid_q)
    dump_p, dump_q = json.dumps(row_p), json.dumps(row_q)
    # Real-channel markers landed in P's own row...
    assert gap_p in dump_p and "zzactp" in dump_p and "2031-zzdatep" in dump_p
    assert gap_q in dump_q  # ...and Q's own marker in Q's row...
    # ...but no P-channel marker appears in Q's row, nor vice versa.
    assert gap_p not in dump_q and "zzactp" not in dump_q
    assert "2031-zzdatep" not in dump_q
    assert "dec-p1" not in dump_q
    assert gap_q not in dump_p  # Q's channel content stays out of P's row
    # Counts stayed on their own side: 1 briefing decision + 1 evidence card
    # + 2 gap occurrences each.
    assert row_p["eligible_total"] == 4 and row_q["eligible_total"] == 4
    assert row_p["counts"]["evidence_cards"] == 1
    assert row_q["counts"]["evidence_cards"] == 1
    assert row_p["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": gap_p, "occurrences": 2}]
    assert row_q["repeated_gaps_tally"]["patterns"] == [
        {"gap_class": gap_q, "occurrences": 2}]
    # Harness-level views agree: no P-text in Q's briefing or memory rows.
    # (Record-note/reason markers are never copied by the composer by
    # design -- count-only rows -- so they are asserted only at the
    # harness level, not as composer probes.)
    assert "zznotcopiedP" not in json.dumps(harness.briefing(pid_q, SOCIAL))
    assert "zznotcopiedQ" not in json.dumps(harness.briefing(pid_p, SOCIAL))
    assert [q["id"] for q in harness.pack_memory(pid_q, SOCIAL)["questions"]] == [qq]
    assert [q["id"] for q in harness.pack_memory(pid_p, SOCIAL)["questions"]] == [qp]


def test_ad06_overlay_content_never_crosses_rows(harness):
    pid_p, pid_q = seed(harness), seed(harness)
    qp = capture(harness, pid_p, "Can I study filing-to-start time?")
    decide(harness, pid_p, qp, "adopt", "a recorded P reason")
    qq = capture(harness, pid_q, "Can I study time to final approval?")
    decide(harness, pid_q, qq, "adopt", "a recorded Q reason")
    proposal_p = {
        "wording": "Proposed: reorder P cards. %s" % ar.PROPOSAL_BADGE,
        "suggested_next_actions": ["pilot the P reorder next"],
        "uncertainty_limits": "P history only; may not generalize",
        "evidence_refs": ["dec-p1", "dec-p2"],
    }
    out = ar.compose_readiness(
        [{"project_id": pid_p, "pack_id": SOCIAL,
          "briefing": harness.briefing(pid_p, SOCIAL),
          "pending": harness.pending_clarifications(pid_p)},
         {"project_id": pid_q, "pack_id": SOCIAL,
          "briefing": harness.briefing(pid_q, SOCIAL),
          "pending": harness.pending_clarifications(pid_q)}],
        proposals={(pid_p, SOCIAL): proposal_p},
        versions={(pid_q, SOCIAL): {"status": "approved", "overlay_version": "v9",
                                    "evidence_refs": ["dec-q1"], "benchmark_refs": ["bm-q1"]}},
        benchmark_reports={(pid_p, SOCIAL): {"cases": [{"case_ref": "FP-1",
                                                        "outcome": "supported"}]}},
    )
    row_p = next(r for r in out["rows"] if r["project_id"] == pid_p)
    row_q = next(r for r in out["rows"] if r["project_id"] == pid_q)
    assert row_p["readiness"] == "proposed"
    assert row_q["readiness"] == "approved"
    dump_p, dump_q = json.dumps(row_p), json.dumps(row_q)
    assert "pilot the P reorder next" in dump_p
    assert "pilot the P reorder next" not in dump_q
    assert "v9" in dump_q and "v9" not in dump_p
    assert row_p["benchmark"]["valid"] is True
    assert row_q["benchmark"] is None  # no report matched to Q: invents nothing
    assert row_p["benchmark"]["case_count"] == 1
