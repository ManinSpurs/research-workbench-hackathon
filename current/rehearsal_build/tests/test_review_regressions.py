"""Review counterexamples. Scripted adapters check state contracts, never Muse quality."""
import asyncio
import json
import sqlite3

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from app.harness import Conflict, Harness
from test_dot_safety import h, capture


def test_timeout_response_is_retryable_without_duplicate_answer_or_budget_reset(h):
    async def scenario():
        pid, qid = await capture(h)
        first = await h.run(qid)
        original = h.adapter.plan

        async def timeout(*args):
            raise TimeoutError("scripted timeout")

        h.adapter.plan = timeout
        with pytest.raises(TimeoutError):
            await h.respond(first["cq_id"], "unknown", client_action_id="retry-one")
        pending = h.pending_clarifications(pid)["clarifications"]
        assert len(pending) == 1 and pending[0]["status"] == "retryable"
        reserved = h.db.execute("SELECT reserved_requests FROM question_budgets WHERE question_id=?", (qid,)).fetchone()[0]
        h.adapter.plan = original
        out = await h.respond(first["cq_id"], "unknown", client_action_id="retry-one")
        assert out["outcome"] == "supported"
        assert h.db.execute("SELECT count(*) FROM answers WHERE question_id=?", (qid,)).fetchone()[0] == 1
        assert h.db.execute("SELECT reserved_requests FROM question_budgets WHERE question_id=?", (qid,)).fetchone()[0] > reserved
        assert await h.respond(first["cq_id"], "unknown", client_action_id="retry-one") == out
        with pytest.raises(Conflict):
            await h.respond(first["cq_id"], "answer", "changed", client_action_id="retry-one")
    asyncio.run(scenario())


def test_retryable_response_survives_restart_with_accepted_payload(h):
    async def scenario():
        pid, qid = await capture(h)
        first = await h.run(qid)
        original = h.adapter.plan

        async def timeout(*args):
            raise TimeoutError("scripted timeout")

        h.adapter.plan = timeout
        with pytest.raises(TimeoutError):
            await h.respond(first["cq_id"], "answer", "PRIVATE accepted statement", pid, "restart-key")
        path = h.db.execute("PRAGMA database_list").fetchone()["file"]
        again = Harness(path, h.data)
        try:
            c = again.pending_clarifications(pid)["clarifications"][0]
            assert c["accepted_response"] == {"kind": "answer", "value": "PRIVATE accepted statement", "client_action_id": "restart-key"}
            again.adapter.plan = original
            out = await again.respond(c["cq_id"], **c["accepted_response"])
            assert out["outcome"] == "supported"
        finally:
            again.db.close()
    asyncio.run(scenario())


def test_invalid_assessment_after_answer_stays_failed(h):
    async def scenario():
        _, qid = await capture(h)
        first = await h.run(qid)

        async def invalid(*args):
            raise UnexpectedModelBehavior("scripted invalid assessment")

        h.adapter.assess = invalid
        out = await h.respond(first["cq_id"], "answer", "My interpretation")
        assert out["outcome"] == "invalid_output"
        assert h.question(qid)["state"] == "invalid_output"
        assert h.db.execute("SELECT status FROM task_runs WHERE id=?", (first["task_id"],)).fetchone()[0] == "failed_validation"
    asyncio.run(scenario())


def test_answer_pending_decision_is_in_restart_briefing(h):
    async def scenario():
        pid, qid = await capture(h)
        first = await h.run(qid)
        await h.respond(first["cq_id"], "answer", "My interpretation")
        path = h.db.execute("PRAGMA database_list").fetchone()["file"]
        again = Harness(path, h.data)
        try:
            brief = again.briefing(pid)
            assert any("Decide on:" in row["text"] for row in brief["parts"]["waiting_on_you"])
            assert brief["parts"]["suggested_next_action"][0]["text"] != "No pending actions."
            assert brief["model_calls"] == 0
        finally:
            again.db.close()
    asyncio.run(scenario())


def test_session_cleanup_preserves_remembered_sibling_resume(h):
    async def scenario():
        pid, qid = await capture(h)
        first = await h.run(qid)
        await h.create_question(pid, "SESSION PRIVATE SIBLING 948?", "session_only")
        assert h.end_session()["deleted"] == 1
        path = h.db.execute("PRAGMA database_list").fetchone()["file"]
        again = Harness(path, h.data)
        try:
            assert again.question(qid)["memory"] == "remember"
            assert (await again.respond(first["cq_id"], "unknown"))["outcome"] == "supported"
        finally:
            again.db.close()
    asyncio.run(scenario())


def test_duplicate_run_creates_only_one_task_and_clarification(h):
    async def scenario():
        _, qid = await capture(h)
        h.adapter.pause = "first_step"
        first = asyncio.create_task(h.run(qid))
        await asyncio.wait_for(h.adapter.entered.wait(), 2)
        with pytest.raises(Conflict):
            await asyncio.wait_for(h.run(qid), 0.2)
        h.adapter.release.set()
        await first
        with pytest.raises(Conflict):
            await h.run(qid)
        assert h.db.execute("SELECT count(*) FROM task_runs WHERE question_id=?", (qid,)).fetchone()[0] == 1
        assert h.db.execute("SELECT count(*) FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0] == 1
    asyncio.run(scenario())


def test_late_run_cannot_overwrite_researcher_decision(h):
    async def scenario():
        pid, qid = await capture(h)
        h.adapter.pause = "first_step"
        task = asyncio.create_task(h.run(qid))
        await asyncio.wait_for(h.adapter.entered.wait(), 2)
        h.decide(pid, qid, "defer", "Researcher chose to stop", "decision-during-run")
        h.adapter.release.set()
        with pytest.raises(Conflict):
            await task
        assert h.question(qid)["state"] == "decided"
        assert h.pending_clarifications(pid)["clarifications"] == []
    asyncio.run(scenario())


def test_decision_changes_use_distinct_actions_and_brief_latest(h):
    async def scenario():
        pid, qid = await capture(h)
        first = h.decide(pid, qid, "adopt", "Reason A", "click-1")
        h.decide(pid, qid, "reject", "Reason B", "click-2")
        last = h.decide(pid, qid, "adopt", "Reason A", "click-3")
        assert first["decision_id"] != last["decision_id"]
        assert h.decide(pid, qid, "adopt", "Reason A", "click-3") == last
        rows = h.briefing(pid)["parts"]["where_you_left_off"]
        assert len(rows) == 1 and rows[0]["text"].startswith("Adopt:")
    asyncio.run(scenario())


def test_interrupted_continuation_reopens_on_startup_without_resetting_budget(h):
    async def scenario():
        pid, qid = await capture(h)
        first = await h.run(qid)
        # Persist exactly the accepted-response boundary, then simulate process loss.
        h.db.execute("INSERT INTO answers VALUES('accepted',?,?, 'unknown',NULL,'2026-10-26T00:00:00+00:00')", (first["cq_id"], qid))
        h.db.execute("INSERT INTO clarification_responses VALUES(?,?,?,'unknown',NULL,'running',NULL)", (first["cq_id"], qid, "crash-action"))
        h.db.execute("UPDATE clarifications SET status='processing' WHERE id=?", (first["cq_id"],))
        h.db.execute("UPDATE task_runs SET status='continuation_running' WHERE id=?", (first["task_id"],))
        h.db.commit()
        reserved = h.db.execute("SELECT reserved_requests FROM question_budgets WHERE question_id=?", (qid,)).fetchone()[0]
        path = h.db.execute("PRAGMA database_list").fetchone()["file"]
        again = Harness(path, h.data)
        try:
            c = again.pending_clarifications(pid)["clarifications"][0]
            assert c["status"] == "retryable"
            assert again.db.execute("SELECT reserved_requests FROM question_budgets WHERE question_id=?", (qid,)).fetchone()[0] == reserved
            assert (await again.respond(c["cq_id"], **c["accepted_response"]))["outcome"] == "supported"
        finally:
            again.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["first_step", "plan"])
def test_interrupted_initial_run_reuses_task_and_preserved_allowance(h, stage):
    async def scenario():
        _, qid = await capture(h)
        if stage == "plan":
            original_first = h.adapter.first_step
            async def direct(*args):
                step, history, usage = await original_first(*args)
                step.route, step.clarification, step.direct_answer = "direct_answer", None, "Read registered evidence"
                return step, history, usage
            h.adapter.first_step = direct
        original = getattr(h.adapter, stage)
        async def timeout(*args):
            raise TimeoutError("scripted stage failure")
        setattr(h.adapter, stage, timeout)
        with pytest.raises(TimeoutError):
            await h.run(qid)
        before = h.db.execute("SELECT id FROM task_runs WHERE question_id=?", (qid,)).fetchone()[0]
        setattr(h.adapter, stage, original)
        out = await h.run(qid)
        assert out["task_id"] == before
        assert h.db.execute("SELECT count(*) FROM task_runs WHERE question_id=?", (qid,)).fetchone()[0] == 1
    asyncio.run(scenario())


def test_failed_final_write_rolls_back_partial_evidence_and_can_retry(h):
    async def scenario():
        _, qid = await capture(h)
        first = await h.run(qid)
        h.db.execute("CREATE TRIGGER reject_final BEFORE UPDATE OF status ON clarification_responses "
                     "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'scripted final write failure'); END")
        h.db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            await h.respond(first["cq_id"], "unknown", client_action_id="atomic-action")
        assert h.db.execute("SELECT count(*) FROM assessments WHERE question_id=?", (qid,)).fetchone()[0] == 0
        assert h.db.execute("SELECT count(*) FROM evidence WHERE question_id=?", (qid,)).fetchone()[0] == 0
        assert h.db.execute("SELECT status FROM clarifications WHERE id=?", (first["cq_id"],)).fetchone()[0] == "retryable"
        h.db.execute("DROP TRIGGER reject_final"); h.db.commit()
        assert (await h.respond(first["cq_id"], "unknown", client_action_id="atomic-action"))["outcome"] == "supported"
        assert h.db.execute("SELECT count(*) FROM assessments WHERE question_id=?", (qid,)).fetchone()[0] == 1
    asyncio.run(scenario())


def test_api_passes_action_id_and_replays_completed_response(h, tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    original = h.adapter.plan
    failed = False
    async def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise TimeoutError("scripted API failure")
        return await original(*args)
    h.adapter.plan = fail_once
    with TestClient(create_app(str(tmp_path / "api-retry.db"), h.data)) as client:
        pid = client.post("/projects", json={"kind": "fx"}).json()["project_id"]
        qid = client.post(f"/projects/{pid}/questions", json={"text": "Can I interpret the cost field?"}).json()["question_id"]
        first = client.post(f"/questions/{qid}/run").json()
        url = f"/clarifications/{first['cq_id']}/responses"
        body = {"kind": "unknown", "client_action_id": "api-retry", "project_id": pid}
        assert client.post(url, json=body).status_code == 502
        pending = client.get(f"/projects/{pid}/clarifications").json()["clarifications"]
        assert pending[0]["accepted_response"]["client_action_id"] == "api-retry"
        success = client.post(url, json=body)
        assert success.status_code == 200 and success.json()["outcome"] == "supported"
        assert client.post(url, json=body).json() == success.json()
        assert client.post(url, json={**body, "kind": "answer", "value": "Different"}).status_code == 409
