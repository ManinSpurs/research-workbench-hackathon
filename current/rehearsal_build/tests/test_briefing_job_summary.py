"""Secretary C11: briefing preparation/AI-job history summary (read-only, model-free).

_project_briefing (and therefore the launch_briefing rows built from it)
carries an additive ``job_summary`` key: counts/status of the project's
recorded preparation_jobs plus a recent-ai_jobs summary (count + latest
status/model as recorded). Empty tables yield an explicit zero/empty
summary, never invented text. Pure stored-row derivation: no model calls,
no writes; other projects' jobs never leak in.
"""
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.harness import Harness

QUESTION = "Can I study time to final approval?"


@pytest.fixture
def h(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    harness = Harness(str(tmp_path / "wb.db"), data)
    yield harness
    harness.db.close()


_qn = 0


def _question(h, pid, text=QUESTION):
    """Seed a remembered question row directly (no adapter/model involved)."""
    global _qn
    _qn += 1
    from app import store as store_mod
    qid = f"q-c11-{_qn:04d}"
    h.db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?)",
                 (qid, pid, text, text, text, "ok", "remember", "captured",
                  store_mod.now()))
    h.db.commit()
    return qid


def _seed_prep(h, pid, qid, n, status):
    for i in range(n):
        h.db.execute("INSERT INTO preparation_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"prep-{pid[-4:]}-{status}-{i}", pid, qid, "decision", f"obj-{i}",
                      pid, json.dumps({"frozen": True}), status, "2026-01-01T00:00:00+00:00"))
    h.db.commit()


def _seed_ai(h, qid, n, status="completed", model="mock-reasoning"):
    ts = time.time()
    for i in range(n):
        h.db.execute("INSERT INTO ai_jobs(id,question_id,status,request_json,snapshot,preview_hash,reservation,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"ai-{qid[-4:]}-{i}", qid, status,
                      json.dumps({"model": model, "request": {"model": model, "task": "t"}}),
                      "snap", "ph", 0.01, None, ts + i))
    h.db.commit()


def test_mixed_prep_statuses_and_latest_ai_reflected(h):
    pid = h.register_project("fx")["project_id"]
    qid = _question(h, pid)
    _seed_prep(h, pid, qid, 2, "queued")
    _seed_prep(h, pid, qid, 1, "paused_foreground")
    _seed_prep(h, pid, qid, 3, "completed")
    _seed_prep(h, pid, qid, 1, "interrupted")
    _seed_ai(h, qid, 2, status="completed", model="mock-reasoning")
    before = "\n".join(sorted(r["sql"] or "" for r in
                              h.db.execute("SELECT sql FROM sqlite_master")))
    b = h._project_briefing(pid)
    summary = b["job_summary"]
    assert summary["preparation_jobs"] == {
        "total": 7,
        "by_status": {"queued": 2, "paused_foreground": 1, "completed": 3, "interrupted": 1}}
    assert summary["ai_jobs"]["total"] == 2
    latest = summary["ai_jobs"]["latest"]
    assert latest["job_id"] == f"ai-{qid[-4:]}-1"
    assert latest["status"] == "completed" and latest["model"] == "mock-reasoning"
    assert latest["created_at"] is not None
    # Additive only: existing keys untouched; read-only (schema identical).
    assert set(b) >= {"project_id", "project", "parts", "top_items", "verified_at",
                      "model_calls", "guidance", "lines", "job_summary"}
    assert b["model_calls"] == 0
    assert "\n".join(sorted(r["sql"] or "" for r in
                            h.db.execute("SELECT sql FROM sqlite_master"))) == before


def test_empty_tables_yield_zero_empty_summary(h):
    pid = h.register_project("fx")["project_id"]
    _question(h, pid)
    assert h._project_briefing(pid)["job_summary"] == {
        "preparation_jobs": {"total": 0, "by_status": {}},
        "ai_jobs": {"total": 0, "latest": None}}


def test_other_projects_jobs_never_leak(h):
    pid_a = h.register_project("fx")["project_id"]
    qid_a = _question(h, pid_a)
    pid_b = h.register_project("fx")["project_id"]
    qid_b = _question(h, pid_b, "Can I study time to final approval for 2024?")
    _seed_prep(h, pid_b, qid_b, 2, "completed")
    _seed_prep(h, pid_b, qid_b, 1, "queued")
    _seed_ai(h, qid_b, 1, model="other-model")
    a = h._project_briefing(pid_a)["job_summary"]
    assert a == {"preparation_jobs": {"total": 0, "by_status": {}},
                 "ai_jobs": {"total": 0, "latest": None}}
    b = h._project_briefing(pid_b)["job_summary"]
    assert b["preparation_jobs"] == {"total": 3, "by_status": {"completed": 2, "queued": 1}}
    assert b["ai_jobs"]["total"] == 1 and b["ai_jobs"]["latest"]["model"] == "other-model"
    # Launch rows carry the same per-project summary.
    rows = {r["project_id"]: r for r in h.launch_briefing()["rows"]}
    assert rows[pid_a]["job_summary"] == a and rows[pid_b]["job_summary"] == b


def test_statuses_use_recorded_values_only(h):
    pid = h.register_project("fx")["project_id"]
    qid = _question(h, pid)
    h.db.execute("INSERT INTO preparation_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                 ("prep-weird-1", pid, qid, "decision", "obj-9", pid,
                  json.dumps({}), "weird_future_status", "2026-01-01T00:00:00+00:00"))
    h.db.commit()
    summary = h._project_briefing(pid)["job_summary"]
    assert summary["preparation_jobs"] == {"total": 1, "by_status": {"weird_future_status": 1}}


def test_ai_without_project_questions_excluded(h):
    pid = h.register_project("fx")["project_id"]
    qid = _question(h, pid)
    other_pid = h.register_project("fx")["project_id"]
    other_qid = _question(h, other_pid)
    _seed_ai(h, qid, 1, model="mine")
    _seed_ai(h, other_qid, 1, model="theirs")
    summary = h._project_briefing(pid)["job_summary"]
    assert summary["ai_jobs"]["total"] == 1
    assert summary["ai_jobs"]["latest"]["model"] == "mine"


def test_ai_usage_rows_are_question_scoped_via_questions_table(h):
    pid = h.register_project("fx")["project_id"]
    qid = _question(h, pid)
    _seed_ai(h, qid, 1, model="q-model")
    db_path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    con = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(ai_jobs)")]
    finally:
        con.close()
    assert "project_id" not in cols
    assert h._project_briefing(pid)["job_summary"]["ai_jobs"]["total"] == 1
