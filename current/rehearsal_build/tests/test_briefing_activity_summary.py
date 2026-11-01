"""C14 session-end summary + C15 launch-briefing last_activity (mock data only).

Harness-level tests using the scripted StubAdapter pattern from
test_dot_safety.py (no sockets, no fake server, no live/API calls).
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_dot_safety import StubAdapter, capture  # noqa: E402

from app import harness as module  # noqa: E402


def _harness(tmp_path, monkeypatch):
    import shutil
    stub = StubAdapter()
    monkeypatch.setattr(module, "ModelAdapter", lambda: stub)
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    h = module.Harness(str(tmp_path / "act.db"), data)
    h.adapter = stub
    return h


def _row(payload, pid):
    return next(r for r in payload["rows"] if r["project_id"] == pid)


def test_last_activity_present_for_active_project_and_null_for_untouched(tmp_path, monkeypatch):
    async def scenario(h):
        pid_active, qid = await capture(h)
        await h.run(qid)
        pid_quiet = h.register_project("fx")["project_id"]
        payload = h.launch_briefing()
        active = _row(payload, pid_active)
        quiet = _row(payload, pid_quiet)
        assert active["last_activity"] is not None
        stored = h.db.execute(
            "SELECT max(created_at) c FROM ("
            "SELECT created_at FROM events WHERE project_id=? AND question_id IS NOT NULL "
            "UNION ALL SELECT created_at FROM decisions WHERE project_id=?)",
            (pid_active, pid_active)).fetchone()["c"]
        assert active["last_activity"] == stored
        assert quiet["last_activity"] is None
        # Ordering rule unchanged: active (waiting) sorts before untouched.
        assert [r["project_id"] for r in payload["rows"]].index(pid_active) < \
            [r["project_id"] for r in payload["rows"]].index(pid_quiet)
    h = _harness(tmp_path, monkeypatch)
    try:
        asyncio.run(scenario(h))
    finally:
        h.db.close()


def test_last_activity_advances_on_decision_and_matches_latest_event(tmp_path, monkeypatch):
    async def scenario(h):
        pid, qid = await capture(h)
        await h.run(qid)
        before = _row(h.launch_briefing(), pid)["last_activity"]
        h.decide(pid, qid, "adopt", "activity probe reason", None)
        after = _row(h.launch_briefing(), pid)["last_activity"]
        assert after is not None and after >= before
        latest = h.db.execute(
            "SELECT max(created_at) c FROM ("
            "SELECT created_at FROM events WHERE project_id=? AND question_id IS NOT NULL "
            "UNION ALL SELECT created_at FROM decisions WHERE project_id=?)",
            (pid, pid)).fetchone()["c"]
        assert after == latest
    h = _harness(tmp_path, monkeypatch)
    try:
        asyncio.run(scenario(h))
    finally:
        h.db.close()


def test_end_session_summary_counts_match_seeded_rows_and_deletion_unchanged(tmp_path, monkeypatch):
    async def scenario(h):
        pid, qid = await capture(h)
        await h.run(qid)
        h.decide(pid, qid, "adopt", "summary probe reason", None)
        keep_text = h.question(qid)["original_text"]
        session_q = await h.create_question(pid, "SESSION SUMMARY PROBE 512?", "session_only")
        remembered = h.db.execute(
            "SELECT count(*) c FROM questions WHERE memory='remember'").fetchone()["c"]
        decisions = h.db.execute("SELECT count(*) c FROM decisions").fetchone()["c"]
        kinds = {r["kind"]: r["c"] for r in h.db.execute(
            "SELECT kind, count(*) c FROM events GROUP BY kind")}
        out = h.end_session()
        assert out["deleted"] == 1 and out["all_verified"]
        summary = out["summary"]
        assert summary["remembered_questions"] == remembered
        assert summary["session_only_questions_deleted"] == 1
        assert summary["decisions"] == decisions
        assert summary["events_by_kind"] == kinds
        assert "session" in summary["scope"].lower()
        # Deletion behaviour unchanged: remembered rows survive, session-only gone.
        assert h.question(qid)["original_text"] == keep_text
        assert h.db.execute(
            "SELECT count(*) c FROM questions WHERE memory='session_only'").fetchone()["c"] == 0
        assert session_q["question_id"] not in [
            r["question_id"] for r in h.db.execute("SELECT question_id FROM questions_fts")]
    h = _harness(tmp_path, monkeypatch)
    try:
        asyncio.run(scenario(h))
    finally:
        h.db.close()


def test_last_activity_matches_hand_inserted_known_max(tmp_path, monkeypatch):
    """Independent oracle: fixed created_at literals, expected max is a literal.

    Guards against a wrong-but-identical-SQL oracle: no query text is reused
    to compute the expectation; the max is known by construction.
    """
    async def scenario(h):
        pid = h.register_project("fx")["project_id"]
        qid = (await h.create_question(pid, "ORACLE MAX PROBE?", "remember"))["question_id"]
        # NULL question_id activity (e.g. registration-style) must not count,
        # even with the largest timestamp present.
        h.db.execute(
            "INSERT INTO events(project_id, question_id, kind, payload, created_at)"
            " VALUES(?,?,?,?,?)",
            (pid, None, "oracle_null_scope", None, "2031-01-01T00:00:00+00:00"))
        h.db.execute(
            "INSERT INTO events(project_id, question_id, kind, payload, created_at)"
            " VALUES(?,?,?,?,?)",
            (pid, qid, "oracle_event", None, "2030-05-01T00:00:00+00:00"))
        h.db.execute(
            "INSERT INTO decisions(id, project_id, question_id, verdict, reason, created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("dec-oracle-max-1", pid, qid, "adopt", "oracle", "2030-05-02T00:00:00+00:00"))
        h.db.commit()
        assert _row(h.launch_briefing(), pid)["last_activity"] == "2030-05-02T00:00:00+00:00"
    h = _harness(tmp_path, monkeypatch)
    try:
        asyncio.run(scenario(h))
    finally:
        h.db.close()


def test_last_activity_cross_project_isolation(tmp_path, monkeypatch):
    """Activity recorded in project B must not change project A's last_activity."""
    async def scenario(h):
        pid_a = h.register_project("fx")["project_id"]
        qid_a = (await h.create_question(pid_a, "ORACLE ISOLATION A?", "remember"))["question_id"]
        pid_b = h.register_project("fx")["project_id"]
        qid_b = (await h.create_question(pid_b, "ORACLE ISOLATION B?", "remember"))["question_id"]
        h.db.execute(
            "INSERT INTO events(project_id, question_id, kind, payload, created_at)"
            " VALUES(?,?,?,?,?)",
            (pid_a, qid_a, "oracle_event", None, "2030-06-01T00:00:00+00:00"))
        h.db.commit()
        before_a = _row(h.launch_briefing(), pid_a)["last_activity"]
        assert before_a == "2030-06-01T00:00:00+00:00"
        h.db.execute(
            "INSERT INTO events(project_id, question_id, kind, payload, created_at)"
            " VALUES(?,?,?,?,?)",
            (pid_b, qid_b, "oracle_event", None, "2040-01-01T00:00:00+00:00"))
        h.db.commit()
        payload = h.launch_briefing()
        assert _row(payload, pid_a)["last_activity"] == before_a
        assert _row(payload, pid_b)["last_activity"] == "2040-01-01T00:00:00+00:00"
    h = _harness(tmp_path, monkeypatch)
    try:
        asyncio.run(scenario(h))
    finally:
        h.db.close()
