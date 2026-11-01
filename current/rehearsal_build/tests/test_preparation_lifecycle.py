"""Slice A3: Background Preparation lifecycle (pause/resume/cancel) + fail-closed
commit validation (BACKGROUND_PREPARATION_DESIGN_EN §§0,4-6, §7 slice A).

Drives ``Harness.pause_preparation`` / ``resume_preparation`` /
``cancel_preparation`` / ``commit_prepared_card`` directly -- no API routes, no
UI, no threads, no timers. The stub adapter is in-process (label "mock"), so
every test makes zero network calls and consumes no credits.

What this pins (from the design, not invented):
  * Legal transitions only. Job statuses come from §5's enum
    (queued/running/paused_foreground/completed/stopped_exhausted/cancelled/
    interrupted) plus Slice A2's honest-stop states (stopped_stale/
    stopped_unavailable/stopped_incomplete). Illegal transitions raise
    Conflict (the design §1 HTTP-409 family) with zero writes:
      pause:  queued -> paused_foreground (also from paused_foreground);
              running/interrupted/terminal -> Conflict.
      resume: queued/paused_foreground/interrupted -> queued, only after the
              frozen scope is re-checked end to end; terminal -> Conflict,
              and a drifted scope -> Conflict with status unchanged.
      cancel: queued/running/paused_foreground/interrupted -> cancelled;
              terminal -> Conflict.
  * pause/resume/cancel never create cards, never mark slices complete, and
    never alter evidence/assessments/decisions/ai_jobs/ai_usage.
  * commit_prepared_card is FAIL-CLOSED AND EFFECT-FREE: the design (§§0/5)
    pins prepared cards as draft proposals whose only path into evidence,
    memory or decisions is the researcher confirming through the existing
    decide()/respond(); it specifies no prepared_cards status other than
    'draft_proposal' and no commit target/effect. The method therefore
    certifies a genuine draft (identity, provenance, completeness, scope
    freshness) and returns it, performing ZERO writes. Forged status strings
    (caller-relabeled or DB-tampered), non-draft cards, incomplete-slice jobs
    (paused/queued/cancelled/interrupted/stopped), cross-project cards,
    off-scope citations, unstated limitations, and double commit (a second
    recorded draft for the same job) all fail closed with zero writes.
"""
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.adapters import Usage
from app.harness import Conflict, Harness
from app.results import (ClarificationNeeded, DesignProposal, EvidenceAssessment,
                         FirstStep, InvestigationPlan, RefinedQuestion, VariableRef)

QUESTION = "Can I study time to final approval?"
GAP = "Does approval_date indicate final approval?"
TARGET = "DD-2023"
OUT_OF_SCOPE = "SOCRATA-LIVE-API"


class StubAdapter:
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


def db_path(h):
    return h.db.execute("PRAGMA database_list").fetchone()["file"]


def count(dbpath, table, where="1=1", args=()):
    con = sqlite3.connect(dbpath)
    try:
        return con.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]
    finally:
        con.close()


def dump(dbpath):
    con = sqlite3.connect(dbpath)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def job_row(h, job_id):
    return dict(h.db.execute("SELECT * FROM preparation_jobs WHERE id=?", (job_id,)).fetchone())


def cards(h, qid=None):
    sql = "SELECT * FROM prepared_cards" + (" WHERE question_id=?" if qid else "")
    return [dict(r) for r in h.db.execute(sql, (qid,) if qid else ())]


def setup_job(h, pid=None, action="action-life-1"):
    pid = pid or h.register_project("fx")["project_id"]
    qid = asyncio.run(_question(h, pid))
    run = asyncio.run(h.run(qid))
    assert run["result_kind"] == "ClarificationNeeded", run
    decision = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", decision["decision_id"], pid, action)
    return pid, qid, job


async def _question(h, pid, text=QUESTION):
    return (await h.create_question(pid, text))["question_id"]


def step(h, job_id):
    return asyncio.run(h.run_preparation_step(job_id))


def completed_job(h):
    pid, qid, job = setup_job(h)
    out = step(h, job["job_id"])
    assert out["status"] == "completed", out
    return pid, qid, job, out


def set_status(h, job_id, status):
    h.db.execute("UPDATE preparation_jobs SET status=? WHERE id=?", (status, job_id))
    h.db.commit()


CANON_STATUSES = ["queued", "running", "paused_foreground", "completed",
                  "stopped_exhausted", "stopped_stale", "stopped_unavailable",
                  "stopped_incomplete", "cancelled", "interrupted"]


def insert_card(h, card_id, job_id, pid, qid, object_type, object_id, evidence_json, lim, unres_json, status):
    h.db.execute("INSERT INTO prepared_cards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (card_id, job_id, pid, qid, object_type, object_id, evidence_json, lim, unres_json,
                  status, "2026-10-27T00:00:00+00:00"))
    h.db.commit()


# ---------------------------------------------------------------- happy path: lifecycle

def test_pause_then_resume_then_step_completes(h, stub):
    _, qid, job = setup_job(h)
    paused = h.pause_preparation(job["job_id"])
    assert paused["status"] == "paused_foreground" and paused["performed"] is True
    assert job_row(h, job["job_id"])["status"] == "paused_foreground"
    assert cards(h, qid) == [], "pause never creates a card"
    resumed = h.resume_preparation(job["job_id"])
    assert resumed["status"] == "queued" and resumed["performed"] is True
    assert job_row(h, job["job_id"])["status"] == "queued"
    calls = list(stub.calls)
    out = step(h, job["job_id"])
    assert out["status"] == "completed" and len(cards(h, qid)) == 1


def test_pause_from_paused_foreground_is_legal_and_idempotent_state(h):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], "paused_foreground")
    out = h.pause_preparation(job["job_id"])
    assert out["status"] == "paused_foreground"
    assert job_row(h, job["job_id"])["status"] == "paused_foreground"


def test_cancel_queued_job_is_terminal_and_step_writes_nothing(h, stub):
    _, qid, job = setup_job(h)
    out = h.cancel_preparation(job["job_id"])
    assert out["status"] == "cancelled" and out["performed"] is True
    assert job_row(h, job["job_id"])["status"] == "cancelled"
    before = dump(db_path(h))
    calls = list(stub.calls)
    replay = step(h, job["job_id"])
    assert replay["performed"] is False and replay["status"] == "cancelled"
    assert stub.calls == calls and cards(h, qid) == []
    assert dump(db_path(h)) == before, "a cancelled job's step must write nothing"


@pytest.mark.parametrize("status", ["running", "paused_foreground", "interrupted"])
def test_cancel_non_terminal_statuses(h, status):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], status)
    out = h.cancel_preparation(job["job_id"])
    assert out["status"] == "cancelled"
    assert job_row(h, job["job_id"])["status"] == "cancelled"


def test_pause_resume_cancel_never_touch_evidence_or_cards(h, stub):
    pid, qid, job, _ = completed_job(h)
    watched = ("evidence", "assessments", "decisions", "prepared_cards", "ai_jobs", "ai_usage")
    before = {t: count(db_path(h), t) for t in watched}
    # Illegal on the completed job: zero writes, state untouched.
    for fn in (h.pause_preparation, h.resume_preparation, h.cancel_preparation):
        with pytest.raises(Conflict):
            fn(job["job_id"])
    assert {t: count(db_path(h), t) for t in watched} == before
    assert job_row(h, job["job_id"])["status"] == "completed"
    assert len(cards(h, qid)) == 1


# ---------------------------------------------------------------- happy path: commit

def test_commit_genuine_draft_card_certifies_without_writes(h):
    _, qid, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    before = dump(db_path(h))
    committed = h.commit_prepared_card(card_id)
    assert committed["status"] == "completed" and committed["performed"] is False
    assert committed["card"]["card_id"] == card_id
    assert committed["card"]["status"] == "draft_proposal"
    assert dump(db_path(h)) == before, "commit validation must write nothing at all"
    assert len(cards(h, qid)) == 1
    # Evidence/decisions were never created by either the runner or the commit.
    assert count(db_path(h), "evidence") == 0
    assert count(db_path(h), "decisions") == 1  # the original defer decision only
    # Re-committing the same genuine draft is still just a validation: still no writes.
    assert h.commit_prepared_card(card_id)["performed"] is False
    assert dump(db_path(h)) == before


def test_commit_unknown_card_is_404(h):
    before = dump(db_path(h))
    with pytest.raises(KeyError):
        h.commit_prepared_card("card-missing")
    assert dump(db_path(h)) == before


# ---------------------------------------------------------------- illegal transitions (zero writes)

@pytest.mark.parametrize("status", ["interrupted", "completed", "stopped_exhausted",
                                    "stopped_stale", "stopped_unavailable", "stopped_incomplete",
                                    "cancelled"])
def test_pause_illegal_statuses(h, status):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], status)
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.pause_preparation(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == status
    assert dump(db_path(h)) == before


@pytest.mark.parametrize("status", ["completed", "stopped_exhausted", "stopped_stale",
                                    "stopped_unavailable", "stopped_incomplete", "cancelled", "running"])
def test_resume_illegal_statuses(h, status):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], status)
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.resume_preparation(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == status
    assert dump(db_path(h)) == before


@pytest.mark.parametrize("status", ["completed", "stopped_exhausted", "stopped_stale",
                                    "stopped_unavailable", "stopped_incomplete", "cancelled"])
def test_cancel_terminal_statuses(h, status):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], status)
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.cancel_preparation(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == status
    assert dump(db_path(h)) == before


def test_resume_cancelled_then_all_recovered_actions_stay_illegal(h):
    _, _, job = setup_job(h)
    h.cancel_preparation(job["job_id"])
    for fn in (h.resume_preparation, h.pause_preparation, h.cancel_preparation):
        with pytest.raises(Conflict):
            fn(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == "cancelled"


def test_unknown_job_lifecycle_is_404(h):
    for fn in (h.pause_preparation, h.resume_preparation, h.cancel_preparation):
        with pytest.raises(KeyError):
            fn("prep-missing")


# ---------------------------------------------------------------- scope drift on resume / commit

def test_pause_then_scope_drift_refuses_resume(h):
    pid, _, job = setup_job(h)
    h.pause_preparation(job["job_id"])
    h.approve_background_scope(pid, ["DD-2023"])  # re-approved with different sources
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.resume_preparation(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == "paused_foreground", "drift refusal leaves the job paused"
    assert dump(db_path(h)) == before


def test_interrupted_resume_refused_after_bundle_drift(h):
    _, _, job = setup_job(h)
    set_status(h, job["job_id"], "interrupted")
    h.db.execute("UPDATE projects SET input_version='tampered-bundle'")
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.resume_preparation(job["job_id"])
    assert job_row(h, job["job_id"])["status"] == "interrupted"
    assert dump(db_path(h)) == before


def test_resume_after_interrupted_completes_without_duplicate_cards(h, stub):
    _, qid, job = setup_job(h)
    set_status(h, job["job_id"], "interrupted")
    resumed = h.resume_preparation(job["job_id"])
    assert resumed["status"] == "queued"
    out = step(h, job["job_id"])
    assert out["status"] == "completed"
    assert len(cards(h, qid)) == 1, "an interrupted job resumes through the one-card runner; no duplicates"
    again = step(h, job["job_id"])
    assert again["performed"] is False and len(cards(h, qid)) == 1


def test_commit_refused_after_bundle_drift(h):
    _, _, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    h.db.execute("UPDATE projects SET input_version='tampered-bundle'")
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before
    assert job_row(h, job["job_id"])["status"] == "completed"


def test_commit_refused_after_deletion_generation_bump(h):
    _, _, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    h.db.execute("UPDATE projects SET deletion_generation=deletion_generation+1")
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


# ---------------------------------------------------------------- forged / incomplete / cross-project commits

def _genuine_card_parts(h, job):
    row = h.db.execute("SELECT * FROM prepared_cards").fetchone()
    assert row is not None
    return dict(row)


def test_forged_card_status_string_never_commits(h):
    _, qid, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    # Caller-side forgery: relabel the stored status away from draft_proposal.
    h.db.execute("UPDATE prepared_cards SET status='completed' WHERE id=?", (card_id,))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before
    # Forgery the other way: claim draft status on a never-drafted job's card fails provenance too.
    assert count(db_path(h), "prepared_cards") == 1


def test_non_draft_card_from_unfinished_job_fails(h):
    pid, qid, job = setup_job(h)  # still queued, no slices ran
    insert_card(h, "card-forged-1", job["job_id"], pid, qid, "decision", job["object_id"],
                json.dumps([{"evidence_id": "DD-2023:approval_date", "source_id": TARGET}]),
                "Registered release only.", json.dumps(["Unresolved item"]), "draft_proposal")
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card("card-forged-1")
    assert dump(db_path(h)) == before
    assert count(db_path(h), "prepared_cards") == 1


@pytest.mark.parametrize("status", ["queued", "paused_foreground", "cancelled", "interrupted",
                                    "stopped_stale", "stopped_exhausted", "stopped_incomplete",
                                    "running"])
def test_commit_paused_or_unfinished_job_fails(h, status):
    pid, qid, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    set_status(h, job["job_id"], status)
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before
    assert job_row(h, job["job_id"])["status"] == status


def test_cross_project_card_fails(h):
    pid_a, qid_a, job_a, out_a = completed_job(h)
    card_id = out_a["card"]["card_id"]
    # Point the genuine card at a different project's scope: cross-project provenance.
    other_pid = h.register_project("fx")["project_id"]
    h.db.execute("UPDATE prepared_cards SET project_id=? WHERE id=?", (other_pid, card_id))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


def test_cross_question_card_fails(h):
    pid, qid_a, job_a, out_a = completed_job(h)
    other_qid = asyncio.run(_question(h, pid))
    card_id = out_a["card"]["card_id"]
    h.db.execute("UPDATE prepared_cards SET question_id=? WHERE id=?", (other_qid, card_id))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


def test_double_commit_second_recorded_draft_fails(h):
    pid, qid, job, out = completed_job(h)
    genuine = _genuine_card_parts(h, job)
    insert_card(h, "card-second-1", job["job_id"], pid, qid, genuine["object_type"],
                genuine["object_id"], genuine["evidence_json"], genuine["limitations_text"],
                genuine["unresolved_json"], "draft_proposal")
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(out["card"]["card_id"])
    with pytest.raises(Conflict):
        h.commit_prepared_card("card-second-1")
    assert dump(db_path(h)) == before
    assert count(db_path(h), "prepared_cards") == 2


def test_card_without_stated_limits_fails(h):
    pid, qid, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    for col, value in (("limitations_text", "  "), ("unresolved_json", "[]"), ("evidence_json", "[]")):
        h.db.execute(f"UPDATE prepared_cards SET {col}=? WHERE id=?", (value, card_id))
        h.db.commit()
        before = dump(db_path(h))
        with pytest.raises(Conflict):
            h.commit_prepared_card(card_id)
        assert dump(db_path(h)) == before


def test_card_citing_off_scope_evidence_fails(h):
    _, _, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    h.db.execute("UPDATE prepared_cards SET evidence_json=? WHERE id=?",
                 (json.dumps([{"evidence_id": "SOCRATA:approval_date", "source_id": OUT_OF_SCOPE}]), card_id))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


def test_card_with_dangling_job_fails(h):
    pid, qid, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    h.db.execute("UPDATE prepared_cards SET job_id='prep-missing' WHERE id=?", (card_id,))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


def test_card_object_mismatch_fails(h):
    _, _, job, out = completed_job(h)
    card_id = out["card"]["card_id"]
    h.db.execute("UPDATE prepared_cards SET object_id='dec-other' WHERE id=?", (card_id,))
    h.db.commit()
    before = dump(db_path(h))
    with pytest.raises(Conflict):
        h.commit_prepared_card(card_id)
    assert dump(db_path(h)) == before


def test_commit_never_writes_evidence_decisions_or_question_state(h):
    pid, qid, job, out = completed_job(h)
    state_before = h.question(qid)["state"]
    committed = h.commit_prepared_card(out["card"]["card_id"])
    assert committed["status"] == "completed"
    assert count(db_path(h), "evidence") == 0
    assert count(db_path(h), "assessments") == 0
    assert h.question(qid)["state"] == state_before
    assert count(db_path(h), "prepared_cards", "question_id=?", (qid,)) == 1
