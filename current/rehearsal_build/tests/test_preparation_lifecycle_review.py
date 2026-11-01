import asyncio
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import api
from app.harness import Harness
from app.preparation_queue import PreparationQueue
from test_preparation_runner import StubAdapter, QUESTION


def test_worker_failure_survives_and_close_bookkeeping(tmp_path):
    async def scenario():
        data = tmp_path / "data"
        shutil.copytree(ROOT / "data", data)
        h = Harness(str(tmp_path / "review.sqlite"), data, StubAdapter())
        pid = h.register_project("fx")["project_id"]
        qid = (await h.create_question(pid, QUESTION))["question_id"]
        await h.run(qid)
        oid = h.db.execute("SELECT id FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0]
        h.approve_background_scope(pid, ["DD-2023", "OFF-FAQ-2023"])
        queue = PreparationQueue(h)
        await queue.start()
        try:
            first, second = [h.mark_preparation(qid, "clarification", oid, pid, action)["job_id"]
                             for action in ("fail", "next")]
            original = h.run_preparation_step
            finished = asyncio.Event()

            async def step(jid):
                if jid == first:
                    raise RuntimeError("synthetic worker failure")
                result = await original(jid)
                finished.set()
                return result

            h.run_preparation_step = step
            queue.enqueue(qid, first)
            queue.enqueue(qid, second)
            await asyncio.wait_for(finished.wait(), 3)
            assert h._preparation_job(first)["status"] == "stopped_incomplete"
            assert h._preparation_job(second)["status"] == "completed"
            assert not queue._worker.done()
            await queue.close()
            await queue.start()
            waiting = h.mark_preparation(qid, "clarification", oid, pid, "waiting")["job_id"]
            # A fault outside the per-job boundary must still run close cleanup.
            queue._worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queue._worker

            async def failed_worker():
                raise RuntimeError("synthetic worker task failure")

            queue._worker = asyncio.create_task(failed_worker())
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="synthetic worker task failure"):
                await queue.close()
            assert h._preparation_job(waiting)["status"] == "interrupted"
            assert not queue._members and not queue._pending
        finally:
            await queue.close()
            h.db.close()
    asyncio.run(scenario())


def test_ask_gate_and_lifespan_closes_db_on_queue_error(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    holder = {}

    def harness(path, directory):
        h = Harness(path, directory, StubAdapter())
        holder["h"] = h
        return h

    monkeypatch.setattr(api, "Harness", harness)
    original_create = Harness.create_question
    original_close = PreparationQueue.close

    async def create(self, *args, **kwargs):
        assert self._preparation_foreground_requested == 1
        holder["gated"] = True
        return await original_create(self, *args, **kwargs)

    async def close(self):
        await original_close(self)
        raise RuntimeError("synthetic shutdown failure")

    monkeypatch.setattr(Harness, "create_question", create)
    monkeypatch.setattr(PreparationQueue, "close", close)
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        with TestClient(api.create_app(str(tmp_path / "api.sqlite"), data)) as client:
            pid = holder["h"].register_project("fx")["project_id"]
            response = client.post(f"/projects/{pid}/questions", json={"text": QUESTION})
            assert response.status_code == 200, response.text
            assert holder["gated"]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        holder["h"].db.execute("SELECT 1")
