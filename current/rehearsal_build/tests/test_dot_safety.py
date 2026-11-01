"""Independent Lite §4/§5/§7 safety contracts, not model-quality/held-out tests.

§4: harness owns validation and state; stale/cancelled work cannot commit.
§5: clarification response kinds remain distinct; deferral is resumable.
§7: session-only content never enters events/search/briefing; deletion cannot
be undone by an in-flight result. Scripted typed outputs test orchestration only.
No sockets, external model, pytest-asyncio, sleeps, or held-out case files.
"""
import asyncio
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import harness as module
from app.adapters import Usage
from app.harness import Conflict
from app.results import (ClarificationNeeded, DesignProposal, EvidenceAssessment,
                         FirstStep, InvestigationPlan, RefinedQuestion, VariableRef)

QUESTION = "PRIVATE-QUESTION-735: what does the cost field mean?"
GAP = "PRIVATE-GAP-735: which cost interpretation?"
ANSWER = "PRIVATE-ANSWER-735: use reported costs"
REASON = "PRIVATE-REASON-735: preserve my research intent"
HISTORY = json.dumps([{"private": QUESTION, "gap": GAP}])


class StubAdapter:
    label = "mock"

    def __init__(self):
        self.pause = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def gate(self, stage):
        self.calls.append(stage)
        if self.pause == stage:
            self.entered.set()
            await self.release.wait()

    async def refine(self, question):
        await self.gate("refine")
        return RefinedQuestion(refined_text=question, kept_constraints=[]), Usage(1, 1, 1)

    async def first_step(self, question, context):
        await self.gate("first_step")
        return FirstStep(
            route="clarify",
            design=DesignProposal(question=question,
                variables=[VariableRef(name="est_cost", dataset_id="fx/permits_2023.csv", kind="actual", role="outcome")],
                assumptions=[], risks=[], verification_plan="Read registered dictionary"),
            clarification=ClarificationNeeded(gap=GAP, affected="Interpretation",
                why_matters="Research interpretation changes", branches=[],
                independent_work="Read the registered cost definition"),
            direct_answer=None, alternatives=[]), HISTORY, Usage(1, 1, 1)

    async def plan(self, history, prompt):
        await self.gate("plan")
        return InvestigationPlan(target_gap=GAP, sources=["DD-2023"],
            fields=["est_cost"], stop_condition="Definition read"), HISTORY, Usage(1, 1, 1)

    async def assess(self, history, observations, observed_ids):
        await self.gate("assess")
        assert "DD-2023:est_cost" in observed_ids
        return EvidenceAssessment(claim="Applicant-reported estimated cost in USD",
            evidence_ids=["DD-2023:est_cost"], applicability="Registered release",
            outcome="supported", confirmation_level="confirmed", unresolved=[],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Researcher decides"), HISTORY, Usage(1, 1, 1)

    async def basic(self, question, materials):
        await self.gate("shadow")
        return QUESTION + " dictionary: check definition", Usage(1, 1, 1)


@pytest.fixture
def h(tmp_path, monkeypatch):
    stub = StubAdapter()
    monkeypatch.setattr(module, "ModelAdapter", lambda: stub)
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    harness = module.Harness(str(tmp_path / "safe.db"), data)
    yield harness
    harness.db.close()


async def capture(h, memory="remember", pid=None):
    pid = pid or h.register_project("fx")["project_id"]
    q = await h.create_question(pid, QUESTION, memory)
    return pid, q["question_id"]


def dump(h):
    return "\n".join(h.db.iterdump())


def content_state(h):
    """Allow content-free incurred-usage accounting (§5.5), never derived writes."""
    tables = [r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'questions_fts_%' "
        "AND name != 'usage_ledger' ORDER BY name")]
    return {table: [tuple(row) for row in h.db.execute(f'SELECT * FROM "{table}"')]
            for table in tables}


def content_surfaces(h, pid):
    events = [dict(r) for r in h.db.execute("SELECT * FROM events")]
    fts = [dict(r) for r in h.db.execute("SELECT * FROM questions_fts")]
    return json.dumps([events, fts, h.briefing(pid)])


@pytest.mark.parametrize("response", ["answer", "unknown", "use_source"])
def test_session_only_full_workflow_has_no_private_content_in_events(h, response):
    async def scenario():
        pid, qid = await capture(h, "session_only")
        snapshots = [content_surfaces(h, pid)]
        r = await h.run(qid)
        snapshots.append(content_surfaces(h, pid))
        await h.respond(r["cq_id"], response,
                        "DD-2023" if response == "use_source" else ANSWER)
        snapshots.append(content_surfaces(h, pid))
        h.decide(pid, qid, "defer", REASON, "private-action")
        await h.shadow(qid)
        snapshots.append(content_surfaces(h, pid))
        leaks = [secret for secret in (QUESTION, GAP, ANSWER, REASON)
                 if any(secret in snapshot for snapshot in snapshots)]
        assert leaks == [], f"Private content leaked into persistent event/search/briefing surfaces: {leaks}"
        assert h.end_session()["all_verified"]
        assert all(secret not in dump(h) for secret in (QUESTION, GAP, ANSWER, REASON))
    asyncio.run(scenario())


async def blocked_operation(h, stage):
    pid, qid = await capture(h)
    if stage in ("plan", "assess"):
        initial = await h.run(qid)
        operation = h.respond(initial["cq_id"], "unknown")
    else:
        operation = h.shadow(qid) if stage == "shadow" else h.run(qid)
    h.adapter.pause = stage
    task = asyncio.create_task(operation)
    await asyncio.wait_for(h.adapter.entered.wait(), 2)
    return pid, qid, task


async def finish_blocked(h, task):
    h.adapter.release.set()
    try:
        return await asyncio.wait_for(task, 2)
    except Conflict:
        return None


@pytest.mark.parametrize("stage", ["first_step", "plan", "assess", "shadow"])
def test_delete_during_await_cannot_resurrect_any_data(h, stage):
    async def scenario():
        _, qid, task = await blocked_operation(h, stage)
        assert h.delete_question(qid)["verified"]
        after_delete = content_state(h)
        await finish_blocked(h, task)
        assert content_state(h) == after_delete, f"Late {stage} wrote after deletion"
        assert QUESTION not in dump(h) and GAP not in dump(h)
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["first_step", "assess"])
def test_cancel_during_await_preserves_cancelled_state_without_derived_writes(h, stage):
    async def scenario():
        _, qid, task = await blocked_operation(h, stage)
        tid = h.db.execute("SELECT id FROM task_runs WHERE question_id=?", (qid,)).fetchone()["id"]
        h.cancel_task(tid)
        after_cancel = content_state(h)
        await finish_blocked(h, task)
        assert content_state(h) == after_cancel, f"Late {stage} wrote after cancellation"
        assert h.db.execute("SELECT status FROM task_runs WHERE id=?", (tid,)).fetchone()["status"] == "cancelled"
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["first_step", "plan", "assess", "shadow"])
def test_input_changed_midawait_rejects_result_without_writes(h, stage):
    async def scenario():
        _, _, task = await blocked_operation(h, stage)
        with (h.data / "fx/permits_2023.csv").open("a") as f:
            f.write("\nchanged-input\n")
        before = content_state(h)
        h.adapter.release.set()
        with pytest.raises(Conflict, match="input|version|changed|stale"):
            await asyncio.wait_for(task, 2)
        assert content_state(h) == before
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["question", "project", "verdict", "reason"])
def test_idempotency_key_is_bound_to_question_project_and_payload(h, change):
    async def scenario():
        pid, qid = await capture(h)
        original = h.decide(pid, qid, "defer", "original reason", "same-action")
        assert h.decide(pid, qid, "defer", "original reason", "same-action") == original
        if change == "question":
            _, qid = await capture(h, pid=pid)
        elif change == "project":
            pid, qid = await capture(h)
        verdict = "adopt" if change == "verdict" else "defer"
        reason = "changed reason" if change == "reason" else "original reason"
        before = dump(h)
        with pytest.raises(Conflict):
            h.decide(pid, qid, verdict, reason, "same-action")
        assert dump(h) == before
    asyncio.run(scenario())


def test_invalid_memory_rejected_before_model_or_storage(h):
    async def scenario():
        pid = h.register_project("fx")["project_id"]
        before = dump(h)
        with pytest.raises(Conflict):
            await h.create_question(pid, QUESTION, "typo-session-only")
        assert dump(h) == before
        assert h.adapter.calls == []
    asyncio.run(scenario())


def test_invalid_response_kind_rejected_without_any_write(h):
    async def scenario():
        _, qid = await capture(h)
        r = await h.run(qid)
        before = dump(h)
        with pytest.raises(Conflict):
            await h.respond(r["cq_id"], "not-a-response-kind", ANSWER)
        assert dump(h) == before
    asyncio.run(scenario())


def test_deferred_clarification_can_resume_same_question(h):
    async def scenario():
        pid, qid = await capture(h)
        r = await h.run(qid)
        assert (await h.respond(r["cq_id"], "later"))["state"] == "deferred_pending"
        assert GAP in json.dumps(h.briefing(pid)["parts"]["waiting_on_you"])
        resumed = await h.respond(r["cq_id"], "unknown")
        assert resumed["result_kind"] == "EvidenceAssessment"
        assert resumed["outcome"] == "supported"
        assert h.db.execute("SELECT count(*) FROM task_runs WHERE question_id=?", (qid,)).fetchone()[0] == 1
        with pytest.raises(Conflict):
            await h.respond(r["cq_id"], "answer", ANSWER)
    asyncio.run(scenario())
