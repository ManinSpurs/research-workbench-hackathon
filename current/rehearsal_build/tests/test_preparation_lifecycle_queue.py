"""In-process scheduling tests only. No model transport or real database."""
import asyncio
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.harness import Harness, Conflict
from app.preparation_queue import PreparationQueue
from test_preparation_runner import StubAdapter, QUESTION


def test_queue_serialization_priority_pause_cancel_restart(tmp_path):
    async def scenario():
        data = tmp_path / "data"
        shutil.copytree(ROOT / "data", data)
        adapter = StubAdapter()
        h = Harness(str(tmp_path / "queue.sqlite"), data, adapter)
        queue = PreparationQueue(h)
        pid = h.register_project("fx")["project_id"]
        q = await h.create_question(pid, QUESTION)
        qid = q["question_id"]
        await h.run(qid)
        oid = h.db.execute("SELECT id FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0]
        h.approve_background_scope(pid, ["DD-2023", "OFF-FAQ-2023"])

        def mark(action):
            return h.mark_preparation(qid, "clarification", oid, pid, action)["job_id"]

        await queue.start()
        try:
            async with queue.foreground():
                first = mark("queue-first")
                assert queue.enqueue(qid, first)
                assert not queue.enqueue(qid, first)
                assert mark("queue-first") == first
                second = mark("queue-second")
                queue.enqueue(qid, second)
                queue.pause(qid, second)
                assert not queue.enqueue(qid, second)  # acceptance retry cannot resume a user pause
                third = mark("queue-third")
                queue.enqueue(qid, third)
                queue.cancel(qid, third)
                await asyncio.sleep(0)
                assert h._preparation_job(first)["status"] == "queued"
            # Deterministic completion notification, not a timing assumption.
            original = h.run_preparation_step
            finished = asyncio.Event()
            calls = []

            async def step(job):
                calls.append(job)
                assert queue.active_job == job
                result = await original(job)
                finished.set()
                return result

            h.run_preparation_step = step
            await asyncio.wait_for(finished.wait(), 3)
            assert calls == [first]
            assert h._preparation_job(first)["status"] == "completed"
            assert h._preparation_job(second)["status"] == "paused_foreground"
            assert h._preparation_job(third)["status"] == "cancelled"
            card = h._preparation_card(first)
            h.commit_prepared_card(card["id"])
            assert card["status"] == "draft_proposal"
            # Explicitly paused items do not run on reopening.
            await queue.close()
            await queue.start()
            assert h._preparation_job(second)["status"] == "interrupted"
            assert not queue._pending
            finished.clear()
            queue.resume(qid, second)
            await asyncio.wait_for(finished.wait(), 3)
            assert calls == [first, second]
        finally:
            await queue.close()
            h.db.close()

    asyncio.run(scenario())


def test_queue_ownership_delete_and_close_inflight(tmp_path):
    async def scenario():
        data = tmp_path / "data"
        shutil.copytree(ROOT / "data", data)
        h = Harness(str(tmp_path / "queue.sqlite"), data, StubAdapter())
        pid = h.register_project("fx")["project_id"]
        qid = (await h.create_question(pid, QUESTION))["question_id"]
        await h.run(qid)
        oid = h.db.execute("SELECT id FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0]
        h.approve_background_scope(pid, ["DD-2023", "OFF-FAQ-2023"])
        queue = PreparationQueue(h)
        await queue.start()
        entered = asyncio.Event()
        hold = asyncio.Event()
        original = h.adapter.plan

        async def blocked(*args):
            entered.set()
            await hold.wait()
            return await original(*args)

        h.adapter.plan = blocked
        try:
            job = h.mark_preparation(qid, "clarification", oid, pid, "inflight")["job_id"]
            with pytest.raises(KeyError):
                queue.enqueue("other-question", job)
            queue.enqueue(qid, job)
            await asyncio.wait_for(entered.wait(), 3)
            queue.cancel(qid, job)
            hold.set()
            await asyncio.wait_for(queue._idle.wait(), 3)
            assert h._preparation_job(job)["status"] == "cancelled"
            assert h._preparation_card(job) is None
            entered.clear()
            hold.clear()
            job2 = h.mark_preparation(qid, "clarification", oid, pid, "shutdown")["job_id"]
            queue.enqueue(qid, job2)
            await asyncio.wait_for(entered.wait(), 3)
            await queue.close()
            assert h._preparation_job(job2)["status"] == "interrupted"
            assert h._preparation_card(job2) is None
            await queue.start()
            assert not queue._pending
            # A queued job purged before dispatch is never resurrected.
            async with queue.foreground():
                job3 = h.mark_preparation(qid, "clarification", oid, pid, "deleted")["job_id"]
                queue.enqueue(qid, job3)
                h.delete_question(qid)
            await asyncio.sleep(0)
            assert h.db.execute("SELECT count(*) FROM prepared_cards").fetchone()[0] == 0
        finally:
            await queue.close()
            h.db.close()

    asyncio.run(scenario())


def test_running_pause_and_foreground_boundary(tmp_path):
    async def scenario():
        data = tmp_path / "data"
        shutil.copytree(ROOT / "data", data)
        h = Harness(str(tmp_path / "boundaries.sqlite"), data, StubAdapter())
        pid = h.register_project("fx")["project_id"]
        qid = (await h.create_question(pid, QUESTION))["question_id"]
        await h.run(qid)
        oid = h.db.execute("SELECT id FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0]
        h.approve_background_scope(pid, ["DD-2023", "OFF-FAQ-2023"])
        queue = PreparationQueue(h)
        await queue.start()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.adapter.plan

        async def gated(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        h.adapter.plan = gated
        try:
            jid = h.mark_preparation(qid, "clarification", oid, pid, "pause-running")["job_id"]
            queue.enqueue(qid, jid)
            await asyncio.wait_for(entered.wait(), 3)
            before = h.adapter.calls.count("assess")
            queue.pause(qid, jid)
            release.set()
            await asyncio.wait_for(queue._idle.wait(), 3)
            assert h._preparation_job(jid)["status"] == "paused_foreground"
            assert h.adapter.calls.count("assess") == before
            assert not queue._pending
            entered.clear()
            release.clear()
            queue.resume(qid, jid)
            await asyncio.wait_for(entered.wait(), 3)
            foreground_started = asyncio.Event()
            foreground_release = asyncio.Event()

            async def foreground():
                async with queue.foreground():
                    foreground_started.set()
                    await foreground_release.wait()

            task = asyncio.create_task(foreground())
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(foreground_started.wait(), 3)
            assert h._preparation_card(jid) is None
            assert h.adapter.calls.count("assess") == before
            foreground_release.set()
            await task
        finally:
            await queue.close()
            h.db.close()

    asyncio.run(scenario())
