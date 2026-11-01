"""Cross-project launch briefing (model-free): one compact row per active project.

Derived only from each project's own records via Harness._project_briefing;
zero model calls, zero writes. Fixture pattern follows tests/test_tier1.py (mock adapter)."""
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="session")
def fake(tmp_path_factory):
    mode_file = tmp_path_factory.mktemp("fake") / "mode.txt"
    mode_file.write_text("")
    port = free_port()
    env = {**os.environ, "FAKE_MODE_FILE": str(mode_file), "PYDANTIC_AI_NO_BANNER": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.fake_meta", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["FAKE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost"); os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)
    yield mode_file
    proc.terminate()


@pytest.fixture
def client(fake, tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    app = create_app(str(tmp_path / "wb.db"), data)
    with TestClient(app) as c:
        c.data_dir = data
        c.db_path = str(tmp_path / "wb.db")
        yield c


def project(c, kind="nyc"):
    return c.post("/projects", json={"kind": kind}).json()["project_id"]


def ask(c, pid, text):
    return c.post(f"/projects/{pid}/questions", json={"text": text}).json()


def ask_run(c, pid, text):
    q = ask(c, pid, text)
    return q, c.post(f"/questions/{q['question_id']}/run").json()


def events(c, pid):
    return c.get(f"/projects/{pid}/events").json()["events"]


def usage(c, pid):
    return c.get(f"/projects/{pid}/usage").json()["usage"]


DEMO_A = "Can I study how long it takes from filing to the start of construction?"
DEMO_B = "Can I study time to final approval?"
SECRET = "Session-only launch briefing probe QQ99?"


def _get(client):
    r = client.get("/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def _row(payload, pid):
    return next(r for r in payload["rows"] if r["project_id"] == pid)


def test_shape_ordering_rule_and_zero_model_calls(client):
    pid = project(client)
    payload = _get(client)
    assert payload["model_calls"] == 0 and payload["verified_at"]
    assert payload["ordering_rule"] == ("projects with waiting items first, then projects with a latest decision, "
                                        "then project name ascending (case-insensitive), then project id")
    assert payload["project_count"] == len(payload["rows"]) == 1
    row = _row(payload, pid)
    assert set(row) >= {"project_id", "project", "parts", "top_items", "verified_at", "model_calls",
                        "has_waiting", "waiting_count", "has_decision", "decision_count", "input_changed",
                        "last_activity"}
    assert set(row["parts"]) == {"where_you_left_off", "what_changed_since", "ready_now",
                                 "waiting_on_you", "suggested_next_action"}
    assert row["model_calls"] == 0 and len(row["top_items"]) <= 3 and row["verified_at"]
    assert row["waiting_count"] == 0 and row["decision_count"] == 0
    assert row["last_activity"] is None  # registration alone (question_id NULL event) is not activity
    assert row["parts"]["where_you_left_off"][0]["text"] == "No decisions yet."
    assert row["parts"]["waiting_on_you"][0]["text"] == "Nothing waiting."
    assert row["project_id"] == client.get(f"/projects/{pid}/briefing").json()["project_id"]


def test_deterministic_ordering_waiting_then_decided_then_empty(client):
    pid_empty = project(client)                       # no activity: sorts last
    pid_decided, pid_waiting = project(client), project(client)
    q_wait, r_wait = ask_run(client, pid_waiting, DEMO_A)          # clarification pending -> waiting
    assert r_wait["result_kind"] == "ClarificationNeeded"
    q_dec, _ = ask_run(client, pid_decided, DEMO_B)
    client.post(f"/projects/{pid_decided}/decisions",
                json={"question_id": q_dec["question_id"], "verdict": "defer", "reason": "launch order check"})
    payload = _get(client)
    assert [r["project_id"] for r in payload["rows"]] == [pid_waiting, pid_decided, pid_empty]
    assert _row(payload, pid_waiting)["has_waiting"] and not _row(payload, pid_decided)["has_waiting"]
    assert _row(payload, pid_decided)["has_decision"] and not _row(payload, pid_empty)["has_decision"]
    assert _get(client)["rows"] == payload["rows"]      # deterministic repeat read


def test_read_only_events_and_usage_unchanged(client):
    pid_waiting, pid_decided = project(client), project(client)
    q_wait, r_wait = ask_run(client, pid_waiting, DEMO_A)
    assert r_wait["result_kind"] == "ClarificationNeeded"
    q_dec, _ = ask_run(client, pid_decided, DEMO_B)
    client.post(f"/projects/{pid_decided}/decisions",
                json={"question_id": q_dec["question_id"], "verdict": "adopt", "reason": "launch read-only check"})
    before = {p: (len(events(client, p)), len(usage(client, p))) for p in (pid_waiting, pid_decided)}
    _get(client)
    assert all((len(events(client, p)), len(usage(client, p))) == before[p] for p in before)


def test_per_project_isolation(client, monkeypatch):
    pid_a, pid_b = project(client), project(client)
    q_a, _ = ask_run(client, pid_a, DEMO_A)
    client.post(f"/projects/{pid_a}/decisions",
                json={"question_id": q_a["question_id"], "verdict": "adopt", "reason": "Launch isolation reason A"})
    q_b, _ = ask_run(client, pid_b, DEMO_B)
    client.post(f"/projects/{pid_b}/decisions",
                json={"question_id": q_b["question_id"], "verdict": "reject", "reason": "Launch isolation reason B"})
    # Freeze only the read-only comparison, not consent timestamps during setup.
    monkeypatch.setattr("app.store.now", lambda: "2026-10-28T12:00:00+00:00")
    a, b = _row(_get(client), pid_a), _row(_get(client), pid_b)
    assert "Launch isolation reason A" in a["parts"]["where_you_left_off"][0]["text"]
    assert "Launch isolation reason A" not in str(b) and "Launch isolation reason B" not in str(a)
    assert DEMO_A not in str(b) and DEMO_B not in str(a)
    # Each launch row matches its own per-project briefing parts exactly.
    single_a = client.get(f"/projects/{pid_a}/briefing").json()
    assert a["parts"] == single_a["parts"] and a["top_items"] == single_a["top_items"][:3]


def test_session_only_content_does_not_leak(client):
    pid_empty, pid_secret = project(client), project(client)
    q = client.post(f"/projects/{pid_secret}/questions", json={"text": SECRET, "memory": "session_only"}).json()
    client.post(f"/questions/{q['question_id']}/run")
    payload = _get(client)
    assert SECRET not in str(payload)
    row = _row(payload, pid_secret)
    assert row["parts"]["waiting_on_you"] == [{"text": "Nothing waiting.", "saved_at": None}]
    assert row["parts"]["where_you_left_off"] == [{"text": "No decisions yet.", "saved_at": None}]
    assert [r["project_id"] for r in payload["rows"]] == [r["project_id"] for r in sorted(
        payload["rows"], key=lambda r: (r["project"].lower(), r["project_id"]))]


def test_empty_database_returns_empty_rows(client):
    payload = _get(client)
    assert payload["rows"] == [] and payload["project_count"] == 0


def _db(c):
    db = sqlite3.connect(c.db_path)
    db.row_factory = sqlite3.Row
    return db


def test_last_activity_events_shows_max_timestamp(client):
    pid = project(client)
    _, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    db = _db(client)
    try:
        ids = [row["id"] for row in db.execute(
            "SELECT id FROM events WHERE project_id=? AND question_id IS NOT NULL", (pid,))]
        assert ids, "expected question-scoped events"
        for eid in ids:
            db.execute("UPDATE events SET created_at='2026-01-10T00:00:00+00:00' WHERE id=?", (eid,))
        db.execute("UPDATE events SET created_at='2026-06-15T12:00:00+00:00' WHERE id=?", (max(ids),))
        db.commit()
    finally:
        db.close()
    assert _row(_get(client), pid)["last_activity"] == "2026-06-15T12:00:00+00:00"


def test_last_activity_decisions_only(client):
    pid = project(client)
    q, _ = ask_run(client, pid, DEMO_B)
    client.post(f"/projects/{pid}/decisions",
                json={"question_id": q["question_id"], "verdict": "adopt", "reason": "last-activity decisions-only"})
    db = _db(client)
    try:
        dec_ts = db.execute("SELECT created_at FROM decisions WHERE project_id=?", (pid,)).fetchone()["created_at"]
        assert dec_ts
        db.execute("DELETE FROM events WHERE project_id=?", (pid,))
        db.commit()
    finally:
        db.close()
    assert _row(_get(client), pid)["last_activity"] == dec_ts


def test_last_activity_none_without_recorded_activity(client):
    pid = project(client)
    # Project registration alone (question_id NULL event) is not activity.
    assert _row(_get(client), pid)["last_activity"] is None


def test_last_activity_cross_project_isolation(client):
    pid_a, pid_b = project(client), project(client)
    ask_run(client, pid_a, DEMO_A)
    ask_run(client, pid_b, DEMO_B)
    db = _db(client)
    try:
        db.execute("UPDATE events SET created_at='2026-09-01T00:00:00+00:00' WHERE project_id=?", (pid_a,))
        db.execute("UPDATE events SET created_at='2026-02-01T00:00:00+00:00' WHERE project_id=?", (pid_b,))
        db.execute("DELETE FROM decisions WHERE project_id IN (?, ?)", (pid_a, pid_b))
        db.commit()
    finally:
        db.close()
    payload = _get(client)
    assert _row(payload, pid_a)["last_activity"] == "2026-09-01T00:00:00+00:00"
    assert _row(payload, pid_b)["last_activity"] == "2026-02-01T00:00:00+00:00"
