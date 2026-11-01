"""App-open preparation scheduling. No transport, extra database or paid consent.

The lifespan owner must start/close this object and route explicit marks/resumes
through enqueue(). Existing database jobs are never replayed on startup.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager

from .harness import Conflict


class PreparationQueue:
    def __init__(self, harness):
        self.harness = harness
        self._pending = deque()
        self._members = set()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._foreground = 0
        self._paused = set()
        self._worker = None
        self.active_job = None

    def _interrupt(self):
        # Content-free state transition only; never replenish an allowance.
        self.harness.db.execute(
            "UPDATE preparation_jobs SET status='interrupted' "
            "WHERE status IN ('queued','running','paused_foreground')")
        self.harness.db.commit()

    async def start(self):
        if self._worker is not None:
            raise Conflict("preparation queue is already active")
        self._interrupt()
        self._worker = asyncio.create_task(self._run())

    async def close(self):
        worker, self._worker = self._worker, None
        try:
            if worker is not None:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
        finally:
            try:
                self._interrupt()
            finally:
                self._pending.clear()
                self._members.clear()
                self._paused.clear()

    def enqueue(self, question_id, job_id):
        job = self.harness._preparation_job(job_id)
        if job["question_id"] != question_id:
            raise KeyError("preparation job not found for this question")
        if self._worker is None or self._worker.done():
            raise Conflict("preparation queue is not active")
        if job["status"] not in self.harness.PREPARATION_RUNNABLE or job_id in self._paused:
            return False
        if job_id in self._members:
            return False
        self._pending.append(job_id)
        self._members.add(job_id)
        self._wake.set()
        return True

    def pause(self, question_id, job_id):
        self._owned(question_id, job_id)
        result = self.harness.pause_preparation(job_id)
        self._paused.add(job_id)
        # Harness uses paused_foreground for both explicit and automatic pauses.
        # Remove explicit pauses from this queue; only explicit resume enqueues.
        self._pending = deque(j for j in self._pending if j != job_id)
        self._members.discard(job_id)
        return result

    def resume(self, question_id, job_id):
        self._owned(question_id, job_id)
        if self.active_job == job_id:
            raise Conflict("preparation is reaching a safe boundary; resume after it pauses")
        if self._worker is None or self._worker.done():
            raise Conflict("preparation queue is not active")
        result = self.harness.resume_preparation(job_id)
        self._paused.discard(job_id)
        self.enqueue(question_id, job_id)
        return result

    def cancel(self, question_id, job_id):
        self._owned(question_id, job_id)
        result = self.harness.cancel_preparation(job_id)
        self._pending = deque(j for j in self._pending if j != job_id)
        if self.active_job != job_id:
            self._members.discard(job_id)
        return result

    def _owned(self, question_id, job_id):
        self.harness.question(question_id)
        job = self.harness._preparation_job(job_id)
        if job["question_id"] != question_id:
            raise KeyError("preparation job not found for this question")
        return job

    @asynccontextmanager
    async def foreground(self):
        """Call before foreground model work, not after its first await.

        Already in-flight work finishes its guarded bounded step; all waiting
        foreground work runs before the next preparation. No mid-call replay.
        """
        self._foreground += 1
        self.harness._preparation_foreground_requested = self._foreground
        try:
            await self._idle.wait()
            yield
        finally:
            self._foreground -= 1
            self.harness._preparation_foreground_requested = self._foreground
            self._wake.set()

    async def _run(self):
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                if self._foreground or self.harness._preparation_foreground_busy():
                    # Existing foreground routes may not use foreground() yet.
                    # Poll only while an explicitly enqueued job is waiting.
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=0.1)
                    except TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                job_id = self._pending.popleft()
                self.active_job = job_id
                self._idle.clear()
                try:
                    try:
                        job = self.harness._preparation_job(job_id)
                    except KeyError:
                        continue  # deletion already purged the job
                    if job["status"] in self.harness.PREPARATION_RUNNABLE:
                        result = await self.harness.run_preparation_step(job_id)
                        if result["status"] == "paused_foreground" and job_id not in self._paused:
                            self._pending.append(job_id)
                except Exception as exc:
                    # CancelledError is a BaseException: shutdown cancellation
                    # still propagates to the lifespan owner's cleanup.
                    self.harness.db.rollback()
                    try:
                        fresh = self.harness._preparation_job(job_id)
                    except KeyError:
                        fresh = None
                    if fresh is not None and fresh["status"] in ("queued", "running", "interrupted"):
                        self.harness._preparation_stop(
                            fresh, "stopped_incomplete",
                            f"Preparation failed unexpectedly ({type(exc).__name__}); no automatic retry.")
                finally:
                    self._members.discard(job_id)
                    if job_id in self._pending:
                        self._members.add(job_id)
                    self.active_job = None
                    self._idle.set()
