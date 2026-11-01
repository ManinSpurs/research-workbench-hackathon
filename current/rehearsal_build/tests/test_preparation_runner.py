"""Slice A2: background preparation runner step (BACKGROUND_PREPARATION_DESIGN_EN §§3-5, slice A).

Drives ``Harness.run_preparation_step`` directly -- no API, no UI, no threads, no
timers. The stub adapter is in-process (label "mock"), so every test makes zero
network calls and consumes no credits; the offline case uses ``OfflineAdapter``.
The runner writes at most one ``prepared_cards`` draft row per job and never
writes ``evidence``/``assessments``/``decisions``/``ai_jobs``/``ai_usage``.

Design silences this file pins (fail-closed, reported to the Lead):
  * statuses beyond §5's enum: ``stopped_stale``, ``stopped_unavailable``,
    ``stopped_incomplete`` (a stale scope, an unusable adapter and an
    evidence-free/limit-free draft are never reported as ``stopped_exhausted``);
  * a draft with no ``unresolved`` limits is rejected at write time (§5 requires
    non-empty ``limitations_text``/``unresolved_json``);
  * a ``running`` row found at the seam is recorded as ``interrupted`` (no silent
    replay, §4);
  * ``paused_foreground`` is checked before any read/model call and is resumable.
"""
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from pydantic_ai.exceptions import UnexpectedModelBehavior

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.adapters import Usage
from app.harness import Harness
from app.results import (ClarificationNeeded, DesignProposal, EvidenceAssessment,
                         FirstStep, InvestigationPlan, RefinedQuestion, VariableRef)

QUESTION = "Can I study time to final approval?"
GAP = "Does approval_date indicate final approval?"
TARGET = "DD-2023"
OUT_OF_SCOPE = "SOCRATA-LIVE-API"


class StubAdapter:
    """Scripted in-process adapter: label 'mock', no transport, records call stages."""

    label = "mock"

    def __init__(self):
        self.calls = []
        self.plan_sources = None
        self.unresolved = ["Final approval is not established; check status_code."]

    async def refine(self, question):
        self.calls.append("refine")
        return RefinedQuestion(refined_text=question, kept_constraints=[]), Usage(1, 1, 1)

    async def first_step(self, question, context):
        self.calls.append("first_step")
        return FirstStep(
            route="clarify",
            design=DesignProposal(question=question,
                variables=[VariableRef(name="approval_date", dataset_id="fx/permits_2023.csv",
                                       kind="unverified", role="end (meaning unverified)")],
                assumptions=["approval_date marks final approval (unverified)"],
                risks=["approval_date may mark plan-review completion instead"],
                verification_plan="Read DD-2023 and the registered official pages"),
            clarification=ClarificationNeeded(gap=GAP, affected="approval_date (processing-time design)",
                why_matters="If yes design A is usable; if not, check status codes first.",
                branches=[], independent_work="status_code / issue_date check can continue"),
            direct_answer=None, alternatives=[]), "HISTORY", Usage(1, 1, 1)

    async def plan(self, history, prompt):
        self.calls.append("plan")
        sources = self.plan_sources or [TARGET, "OFF-FAQ-2023"]
        return InvestigationPlan(target_gap="Meaning of approval_date", sources=sources,
                                 fields=["approval_date"], stop_condition="Stop after the listed sources"), "HISTORY", Usage(1, 1, 1)

    async def assess(self, history, observations, observed_ids):
        self.calls.append("assess")
        assert "DD-2023:approval_date" in observed_ids
        return EvidenceAssessment(claim="approval_date records plan-review completion",
            evidence_ids=["DD-2023:approval_date"], applicability="DD-2023 applies to this extract.",
            outcome="partial", confirmation_level="partial", unresolved=list(self.unresolved),
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Check status codes before using approval_date as final approval."), "HISTORY", Usage(1, 1, 1)

    async def basic(self, question, materials):
        self.calls.append("shadow")
        return "check definition", Usage(1, 1, 1)


@pytest.fixture
def stub():
    return StubAdapter()


@pytest.fixture
def h(tmp_path, stub):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    harness = Harness(str(tmp_path / "wb.db"), data, adapter=stub)
    yield harness
    harness.db.close()


def count(db_path, table, where="1=1", args=()):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]
    finally:
        con.close()


def db_path(h):
    return h.db.execute("PRAGMA database_list").fetchone()["file"]


async def _question(h, pid, text=QUESTION):
    return (await h.create_question(pid, text))["question_id"]


def setup_job(h, object_type="decision", pid=None):
    """Ask + clarify-run + defer decision + approved scope + mark; returns (pid, qid, job)."""
    pid = pid or h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    run = asyncio.run(h.run(qid))
    assert run["result_kind"] == "ClarificationNeeded", run
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, object_type, decision["decision_id"], pid, "action-run-1")
    return pid, qid, job


def step(h, job_id):
    return asyncio.run(h.run_preparation_step(job_id))


def job_row(h, job_id):
    return dict(h.db.execute("SELECT * FROM preparation_jobs WHERE id=?", (job_id,)).fetchone())


def cards(h, qid=None):
    sql = "SELECT * FROM prepared_cards" + (" WHERE question_id=?" if qid else "")
    return [dict(r) for r in h.db.execute(sql, (qid,) if qid else ())]


def content_tables(h):
    return {t: [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}"')]
            for t in ("evidence", "assessments", "decisions", "ai_jobs", "ai_usage",
                      "prepared_cards", "preparation_jobs", "questions")}


# ---------------------------------------------------------------- happy path

def test_step_completes_and_writes_exactly_one_draft_card(h, stub):
    pid, qid, job = setup_job(h)
    evidence_before = count(db_path(h), "evidence")
    assessments_before = count(db_path(h), "assessments")
    decisions_before = count(db_path(h), "decisions")
    state_before = h.question(qid)["state"]
    out = step(h, job["job_id"])
    assert out["status"] == "completed" and out["performed"] is True
    assert out["model_calls"] == 2
    assert stub.calls[-2:] == ["plan", "assess"], stub.calls
    assert job_row(h, job["job_id"])["status"] == "completed"
    rows = cards(h, qid)
    assert len(rows) == 1
    card = rows[0]
    assert card["status"] == "draft_proposal"
    assert card["job_id"] == job["job_id"] and card["project_id"] == pid
    assert card["object_type"] == "decision" and card["object_id"] == job["object_id"]
    evidence = json.loads(card["evidence_json"])
    assert [e["evidence_id"] for e in evidence] == ["DD-2023:approval_date"]
    assert evidence[0]["version_kind"] == "registered_bundle_sha256"
    assert card["limitations_text"].strip() and "Draft proposal only" in card["limitations_text"]
    assert card["limitations_text"].startswith("Draft proposal only; nothing is decided.")
    assert json.loads(card["unresolved_json"]) == ["Final approval is not established; check status_code."]
    # Draft isolation: no research-state writes at all.
    assert count(db_path(h), "evidence") == evidence_before
    assert count(db_path(h), "assessments") == assessments_before
    assert count(db_path(h), "decisions") == decisions_before
    assert h.question(qid)["state"] == state_before
    assert count(db_path(h), "ai_jobs") == 0 and count(db_path(h), "ai_usage") == 0
    # Off-scope plan entry is recorded, never read.
    assert out["out_of_scope_not_called"] == []
    listed = h.list_preparations(qid)
    assert len(listed["cards"]) == 1


def test_step_is_idempotent_after_completion(h, stub):
    _, qid, job = setup_job(h)
    first = step(h, job["job_id"])
    assert first["status"] == "completed"
    calls = list(stub.calls)
    before = cards(h, qid)
    second = step(h, job["job_id"])
    assert second["status"] == "completed" and second["performed"] is False
    assert "already completed" in second["reason"]
    assert stub.calls == calls, "a completed step must not call the model again"
    assert cards(h, qid) == before and len(cards(h, qid)) == 1


def test_step_rerun_after_stop_does_not_duplicate_cards(h, stub):
    _, qid, job = setup_job(h)
    stub.unresolved = []
    first = step(h, job["job_id"])
    assert first["status"] == "stopped_incomplete" and cards(h, qid) == []
    calls = list(stub.calls)
    second = step(h, job["job_id"])
    assert second["performed"] is False and stub.calls == calls
    assert cards(h, qid) == [], "a stopped step never writes a card, even on replay"


# ---------------------------------------------------------------- offline / unavailable

def test_offline_adapter_stops_unavailable_without_a_card(tmp_path):
    from app.offline import OfflineAdapter
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    h = Harness(str(tmp_path / "offline.db"), data, adapter=OfflineAdapter())
    try:
        pid = h.register_project("fx")["project_id"]
        qid = asyncio.run(_question(h, pid))
        decision = h.decide(pid, qid, "defer", "needs preparation", None)
        h.approve_background_scope(pid, [TARGET])
        job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, "action-off-1")
        before = content_tables(h)
        out = step(h, job["job_id"])
        assert out["status"] == "stopped_unavailable"
        assert "no local model" in out["reason"]
        assert out["performed"] is True and out["card"] is None
        assert cards(h, qid) == []
        after = content_tables(h)
        assert after["evidence"] == before["evidence"]
        assert after["prepared_cards"] == before["prepared_cards"]
        # Replay of the stopped step is read-only.
        calls_before = dict(h.db.execute("SELECT * FROM question_budgets").fetchone())
        again = step(h, job["job_id"])
        assert again["performed"] is False
        assert dict(h.db.execute("SELECT * FROM question_budgets").fetchone()) == calls_before
    finally:
        h.db.close()


# ---------------------------------------------------------------- foreground priority

def test_foreground_running_task_pauses_then_resumes(h, stub):
    pid, qid, job = setup_job(h)
    h.db.execute("INSERT INTO task_runs VALUES(?,?,?,?,?,?,?)",
                 ("task-foreground", qid, pid, "running", "v", 0, "2026-10-27T00:00:00+00:00"))
    h.db.commit()
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["status"] == "paused_foreground"
    assert "foreground" in out["reason"]
    assert stub.calls == calls, "no model call may start when foreground work owns the loop"
    assert job_row(h, job["job_id"])["status"] == "paused_foreground"
    assert cards(h, qid) == []
    # Foreground finishes: the same job resumes from its frozen manifest.
    h.db.execute("UPDATE task_runs SET status='completed' WHERE id='task-foreground'")
    h.db.commit()
    resumed = step(h, job["job_id"])
    assert resumed["status"] == "completed" and resumed["performed"] is True
    assert len(cards(h, qid)) == 1


def test_running_clarification_response_also_pauses(h):
    _, qid, job = setup_job(h)
    h.db.execute("INSERT INTO clarification_responses VALUES(?,?,?,?,?,'running',NULL)",
                 ("cq-foreground", qid, None, "answer", "text"))
    h.db.commit()
    out = step(h, job["job_id"])
    assert out["status"] == "paused_foreground" and cards(h, qid) == []


def test_one_running_job_invariant_blocks_second_step(h, stub):
    pid, qid, job = setup_job(h)
    other = h.mark_preparation(qid, "decision", job["object_id"], pid, "action-run-2")
    h.db.execute("UPDATE preparation_jobs SET status='running' WHERE id=?", (other["job_id"],))
    h.db.commit()
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["performed"] is False
    assert other["job_id"] in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "queued"
    assert stub.calls == calls and cards(h, qid) == []


def test_stale_running_row_is_recorded_interrupted(h):
    _, qid, job = setup_job(h)
    h.db.execute("UPDATE preparation_jobs SET status='running' WHERE id=?", (job["job_id"],))
    h.db.commit()
    out = step(h, job["job_id"])
    assert out["status"] == "interrupted" and out["performed"] is False
    assert "fresh explicit mark" in out["reason"]
    assert cards(h, qid) == []
    kinds = [r[0] for r in h.db.execute("SELECT kind FROM events WHERE question_id=?", (qid,))]
    assert "preparation_interrupted" in kinds


# ---------------------------------------------------------------- frozen-scope drift

def test_stale_registered_bundle_stops_without_a_card(h, stub):
    _, qid, job = setup_job(h)
    h.db.execute("UPDATE projects SET input_version='tampered-bundle'")
    h.db.commit()
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "input version" in out["reason"]
    assert stub.calls == calls and cards(h, qid) == []


def test_deletion_generation_bump_stops_late_commit(h, stub):
    _, qid, job = setup_job(h)
    h.db.execute("UPDATE projects SET deletion_generation=deletion_generation+1")
    h.db.commit()
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "deletion generation" in out["reason"]
    assert stub.calls == calls and cards(h, qid) == []


def test_reapproved_scope_with_different_sources_stops(h):
    pid, qid, job = setup_job(h)
    h.approve_background_scope(pid, ["DD-2023"])
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "scope" in out["reason"] and cards(h, qid) == []


def test_object_no_longer_open_stops(h):
    pid, qid, job = setup_job(h)
    h.decide(pid, qid, "adopt", "researcher moved on", None)
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "superseding decision" in out["reason"] and cards(h, qid) == []


def test_unknown_job_is_404(h):
    with pytest.raises(KeyError):
        step(h, "prep-missing")


# ---------------------------------------------------------------- late commit rejection

def test_late_commit_rejected_after_in_flight_decision(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.assess

    async def decide_mid_flight(history, observations, observed_ids):
        out = await original(history, observations, observed_ids)
        # The researcher decides while the assess call is in flight.
        h.decide(pid, qid, "adopt", "decided during preparation", None)
        return out

    stub.assess = decide_mid_flight
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "stale result" in out["reason"] or "superseding" in out["reason"]
    assert cards(h, qid) == [], "a late draft is never committed over a newer decision"


def test_mid_await_clarification_answer_stops_stale_without_card(h, stub):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    run = asyncio.run(h.run(qid))
    assert run["result_kind"] == "ClarificationNeeded", run
    cq_id = run["cq_id"]
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "clarification", cq_id, pid, "action-clar-mid-1")
    original = stub.assess
    fired = []

    async def answer_mid_flight(history, observations, observed_ids):
        out = await original(history, observations, observed_ids)
        # One-shot: the nested investigation that respond() runs also calls
        # this stub; only the outer in-flight assess performs the answer.
        if not fired:
            fired.append(True)
            # The researcher answers the marked clarification while assess is in flight.
            await h.respond(cq_id, "answer", "approval_date marks plan-review completion")
        return out

    stub.assess = answer_mid_flight
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "no longer open" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "stopped_stale"
    assert cards(h, qid) == [], "a card is never committed against a closed clarification object"


def test_mid_await_scope_reapproval_with_different_sources_stops_stale(h, stub):
    # Re-approval replaces the scope row in place (same scope_id, same bundle);
    # only the source list changes, which the _checkpoint() tuple cannot see.
    pid, qid, job = setup_job(h)
    original = stub.assess

    async def reapprove_mid_flight(history, observations, observed_ids):
        out = await original(history, observations, observed_ids)
        h.approve_background_scope(pid, [TARGET])
        return out

    stub.assess = reapprove_mid_flight
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_stale"
    assert "scope" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "stopped_stale"
    assert cards(h, qid) == [], "a card is never committed against a re-approved scope"


def test_mid_await_cancel_leaves_cancelled_without_card(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.assess

    async def cancel_mid_flight(history, observations, observed_ids):
        out = await original(history, observations, observed_ids)
        # The researcher cancels while the assess call is in flight.
        h.cancel_preparation(job["job_id"])
        return out

    stub.assess = cancel_mid_flight
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled", "a cancelled job is never overwritten to completed"
    assert cards(h, qid) == [], "a cancelled job never commits a card"
    kinds = [r[0] for r in h.db.execute("SELECT kind FROM events WHERE question_id=?", (qid,))]
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


# ---------------------------------------------------------------- frozen budget

def test_frozen_tool_allowance_stops_exhausted(h, stub):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    asyncio.run(h.run(qid))
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    h.db.execute("UPDATE question_budgets SET limits_json=? WHERE question_id=?",
                 (json.dumps({"model_requests": 16, "tokens": 100000, "tool_calls": 0,
                              "wall_seconds": 300, "cost_usd": 1.0, "mode": "rehearsal"}), qid))
    h.db.commit()
    job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, "action-budget-1")
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_exhausted"
    assert "tool allowance" in out["reason"]
    assert stub.calls == calls and cards(h, qid) == []
    # A later budget increase never widens the frozen copy.
    h.db.execute("UPDATE question_budgets SET limits_json=? WHERE question_id=?",
                 (json.dumps({"model_requests": 16, "tokens": 100000, "tool_calls": 12,
                              "wall_seconds": 300, "cost_usd": 1.0, "mode": "rehearsal"}), qid))
    h.db.commit()
    assert step(h, job["job_id"])["status"] == "stopped_exhausted"
    assert cards(h, qid) == []


def test_frozen_model_allowance_stops_exhausted(h):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    asyncio.run(h.run(qid))
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET])
    h.db.execute("UPDATE question_budgets SET limits_json=? WHERE question_id=?",
                 (json.dumps({"model_requests": 2, "tokens": 100000, "tool_calls": 12,
                              "wall_seconds": 300, "cost_usd": 1.0, "mode": "rehearsal"}), qid))
    h.db.commit()
    job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, "action-budget-2")
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_exhausted"
    assert "question allowance exhausted" in out["reason"] and cards(h, qid) == []


# ---------------------------------------------------------------- honest non-card outcomes

def test_plan_outside_frozen_scope_reads_nothing(h, stub):
    _, qid, job = setup_job(h)
    stub.plan_sources = [OUT_OF_SCOPE]
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_incomplete"
    assert "no source inside the frozen scope" in out["reason"]
    assert cards(h, qid) == []


def test_off_scope_sources_are_reported_and_never_read(h, stub):
    _, qid, job = setup_job(h)
    stub.plan_sources = [TARGET, OUT_OF_SCOPE]
    out = step(h, job["job_id"])
    assert out["status"] == "completed"
    assert out["out_of_scope_not_called"] == [OUT_OF_SCOPE]
    card = cards(h, qid)[0]
    assert json.loads(card["evidence_json"])[0]["source_id"] == TARGET


def test_draft_without_stated_limits_is_not_written(h, stub):
    _, qid, job = setup_job(h)
    stub.unresolved = []
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_incomplete"
    assert "unresolved" in out["reason"]
    assert cards(h, qid) == []


def test_assessment_citing_unreadable_evidence_stops_incomplete_without_raising(h, stub):
    _, qid, job = setup_job(h)

    async def citing_unreadable(history, observations, observed_ids):
        return EvidenceAssessment(
            claim="approval_date records final approval",
            evidence_ids=["UNREADABLE-EVIDENCE-999"], applicability="Claimed to apply.",
            outcome="partial", confirmation_level="partial",
            unresolved=["Final approval is not established; check status_code."],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Check status codes."), "HISTORY", Usage(1, 1, 1)

    stub.assess = citing_unreadable
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_incomplete"
    assert "not readable within the frozen scope" in out["reason"]
    assert "UNREADABLE-EVIDENCE-999" in out["reason"]
    assert cards(h, qid) == []
    assert job_row(h, job["job_id"])["status"] == "stopped_incomplete"


def test_startup_recovery_marks_stale_running_preparation_interrupted(h):
    _, qid, job = setup_job(h)
    h.db.execute("UPDATE preparation_jobs SET status='running' WHERE id=?", (job["job_id"],))
    h.db.commit()
    h._recover_interrupted()
    assert job_row(h, job["job_id"])["status"] == "interrupted"
    assert cards(h, qid) == []


def test_constructor_recovery_marks_stale_running_preparation_interrupted(h, stub):
    _, qid, job = setup_job(h)
    h.db.execute("UPDATE preparation_jobs SET status='running' WHERE id=?", (job["job_id"],))
    h.db.commit()
    restarted = Harness(db_path(h), h.data, adapter=stub)
    try:
        assert job_row(restarted, job["job_id"])["status"] == "interrupted"
        assert cards(restarted, qid) == []
    finally:
        restarted.db.close()


def test_misapplied_release_citation_rejected_for_conflicting_outcome(h, stub):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    asyncio.run(h.run(qid))
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-NOTES-2025"])
    job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, "action-misapplied-1")
    stub.plan_sources = [TARGET, "OFF-NOTES-2025"]

    async def conflicting_assess(history, observations, observed_ids):
        return EvidenceAssessment(
            claim="approval_date records final approval",
            evidence_ids=["OFF-NOTES-2025"], applicability="OFF-NOTES-2025 applies.",
            outcome="conflicting", confirmation_level="conflicting",
            unresolved=["Release applicability is unresolved."],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Use only sources applicable to release 2023."), "HISTORY", Usage(1, 1, 1)

    stub.assess = conflicting_assess
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_incomplete"
    assert "not applicable to this release" in out["reason"]
    assert cards(h, qid) == []
    assert job_row(h, job["job_id"])["status"] == "stopped_incomplete"


def test_readable_evidence_required_before_any_card(h, stub):
    _, qid, job = setup_job(h)
    # Plan asks for a field that has no registered observation for the source.
    stub.plan_sources = ["OFF-FAQ-2023"]
    original = stub.plan

    async def empty_field_plan(history, prompt):
        out = await original(history, prompt)
        plan = out[0].model_copy(update={"fields": ["not_a_registered_field"]})
        return plan, out[1], out[2]

    stub.plan = empty_field_plan
    out = step(h, job["job_id"])
    assert out["status"] == "stopped_incomplete"
    assert "no readable registered evidence" in out["reason"]
    assert cards(h, qid) == []


# ---------------------------------------------------------------- post-await liveness (cold-review follow-up)

def _events(h, qid):
    return [r[0] for r in h.db.execute("SELECT kind FROM events WHERE question_id=?", (qid,))]


def test_mid_await_cancel_then_plan_names_nothing_in_scope_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_then_empty_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight; the returned
        # plan names no in-scope source, which previously overwrote 'cancelled'
        # with 'stopped_incomplete' plus a preparation_stopped event.
        h.cancel_preparation(job["job_id"])
        plan = out[0].model_copy(update={"sources": [OUT_OF_SCOPE]})
        return plan, out[1], out[2]

    stub.plan = cancel_then_empty_plan
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == [], "a cancelled job never commits a card"
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_unreadable_evidence_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_then_unreadable_plan(history, prompt):
        out = await original(history, prompt)
        h.cancel_preparation(job["job_id"])
        plan = out[0].model_copy(update={"fields": ["not_a_registered_field"]})
        return plan, out[1], out[2]

    stub.plan = cancel_then_unreadable_plan
    # Plan names one in-scope source so the read path runs, but the field has
    # no registered observation: previously stopped_incomplete overwrote cancelled.
    stub.plan_sources = ["OFF-FAQ-2023"]
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == []
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_bad_citation_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_mid_flight(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight.
        h.cancel_preparation(job["job_id"])
        return out

    async def citing_unreadable(history, observations, observed_ids):
        return EvidenceAssessment(
            claim="approval_date records final approval",
            evidence_ids=["UNREADABLE-EVIDENCE-999"], applicability="Claimed to apply.",
            outcome="partial", confirmation_level="partial",
            unresolved=["Final approval is not established; check status_code."],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Check status codes."), "HISTORY", Usage(1, 1, 1)

    stub.plan = cancel_mid_flight
    stub.assess = citing_unreadable
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == []
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_misapplied_citation_keeps_cancelled(h, stub):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    asyncio.run(h.run(qid))
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-NOTES-2025"])
    job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, "action-misapplied-cancel-1")
    stub.plan_sources = [TARGET, "OFF-NOTES-2025"]
    original = stub.plan

    async def cancel_mid_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight; the assess
        # step then cites a source not applicable to this release, which
        # previously overwrote 'cancelled' with 'stopped_incomplete' plus a
        # preparation_stopped event.
        h.cancel_preparation(job["job_id"])
        return out

    async def conflicting_assess(history, observations, observed_ids):
        return EvidenceAssessment(
            claim="approval_date records final approval",
            evidence_ids=["OFF-NOTES-2025"], applicability="OFF-NOTES-2025 applies.",
            outcome="conflicting", confirmation_level="conflicting",
            unresolved=["Release applicability is unresolved."],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Use only sources applicable to release 2023."), "HISTORY", Usage(1, 1, 1)

    stub.plan = cancel_mid_plan
    stub.assess = conflicting_assess
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == [], "a cancelled job never commits a card"
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_empty_unresolved_draft_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_mid_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight; the assess
        # step then returns a draft with no unresolved limits, which
        # previously overwrote 'cancelled' with 'stopped_incomplete' plus a
        # preparation_stopped event.
        h.cancel_preparation(job["job_id"])
        return out

    stub.plan = cancel_mid_plan
    # The default assess echoes stub.unresolved, so an empty list drives the
    # draft through the empty-unresolved stop after the assess await.
    stub.unresolved = []
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == [], "a cancelled job never commits a card"
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_assess_conflict_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original_plan = stub.plan
    original_assess = stub.assess

    async def cancel_mid_plan(history, prompt):
        out = await original_plan(history, prompt)
        # The researcher cancels while the plan call is in flight.
        h.cancel_preparation(job["job_id"])
        return out

    # The barrier raises on the genuine late-decision path (no fabricated
    # raise inside the stub): keep the stub honest and barrier-driven.
    async def honest_conflicting_assess(history, observations, observed_ids):
        out = await original_assess(history, observations, observed_ids)
        h.decide(pid, qid, "adopt", "decided during preparation", None)
        return out

    stub.plan = cancel_mid_plan
    stub.assess = honest_conflicting_assess
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert "left unchanged" in out["reason"] and "nothing was committed" in out["reason"]
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == []
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_model_error_keeps_cancelled(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_mid_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight.
        h.cancel_preparation(job["job_id"])
        return out

    async def failing_assess(history, observations, observed_ids):
        raise UnexpectedModelBehavior("mid-flight model failure")

    stub.plan = cancel_mid_plan
    stub.assess = failing_assess
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is False
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == []
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds


def test_mid_await_cancel_then_unexpected_error_keeps_cancelled_without_interrupt(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def cancel_mid_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher cancels while the plan call is in flight.
        h.cancel_preparation(job["job_id"])
        return out

    async def exploding_assess(history, observations, observed_ids):
        raise RuntimeError("boom inside the assess stub")

    stub.plan = cancel_mid_plan
    stub.assess = exploding_assess
    # Cancellation now stops at the plan boundary: no further assess call.
    out = step(h, job["job_id"])
    assert out["status"] == "cancelled"
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    assert cards(h, qid) == []
    kinds = _events(h, qid)
    assert kinds.count("preparation_cancelled") == 1
    assert "preparation_stopped" not in kinds and "preparation_card_drafted" not in kinds
    assert "preparation_interrupted" not in kinds


# Note on the fresh-None path (job row deleted mid-await): reachable
# in-process via a mid-await delete_question. The plan _commit_barrier raises
# Conflict ("question deleted while model call was in flight"), the Conflict
# handler routes through _preparation_stop_guarded, the fresh lookup misses,
# and _preparation_stop_deleted returns the honest `deleted` outcome pinned
# below. Only the pre-commit gate's inline fresh-None (second barrier raises
# nothing by itself) needs surgical setup and is not pinned here.


def test_mid_await_delete_question_returns_deleted_without_record(h, stub):
    pid, qid, job = setup_job(h)
    original = stub.plan

    async def delete_mid_plan(history, prompt):
        out = await original(history, prompt)
        # The researcher deletes the question while the plan call is in flight.
        h.delete_question(qid)
        return out

    stub.plan = delete_mid_plan
    out = step(h, job["job_id"])
    assert out["status"] == "deleted" and out["performed"] is False
    assert "no longer exists" in out["reason"] and "no state was recorded" in out["reason"]
    assert out["status"] not in Harness.PREPARATION_TERMINAL
    assert h.db.execute("SELECT * FROM preparation_jobs WHERE id=?", (job["job_id"],)).fetchone() is None
    assert cards(h, qid) == [], "a deleted job never commits a card"
    stopped = [json.loads(r[0]) for r in h.db.execute(
        "SELECT payload FROM events WHERE question_id=? AND kind=?", (qid, "preparation_stopped"))]
    assert len(stopped) == 1
    assert stopped[0]["status"] == "deleted"
    assert stopped[0]["job_row_exists"] is False and stopped[0]["recorded_on_job"] is False
    assert "no longer exists" in stopped[0]["reason"] and "no state was recorded" in stopped[0]["reason"]
    kinds = _events(h, qid)
    assert "preparation_card_drafted" not in kinds
