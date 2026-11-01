"""Secretary guidance (deterministic, model-free coaching note).

Fixture pattern follows tests/test_launch_briefing.py (subprocess fake-meta server + TestClient).
Guidance is purely additive: every endpoint carries a per-project `guidance` note built only from
that project's own recorded waiting/decision items, with zero model calls and zero writes.
Mock transports only; no credits consumed.
"""
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_A = "Can I study how long it takes from filing to the start of construction?"
DEMO_C = "Can I study the delay from filing to construction start?"
SECRET = "Session-only guidance probe QQ77?"


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


def _launch_rows(c):
    r = c.get("/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def _row(payload, pid):
    return next(r for r in payload["rows"] if r["project_id"] == pid)


def _single(c, pid):
    r = c.get(f"/projects/{pid}/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def backdate(c, days):
    """Move every waiting record timestamp for calibration: clarification/question created_at."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    db = sqlite3.connect(c.db_path)
    db.execute("UPDATE clarifications SET created_at=?", (ts,))
    db.execute("UPDATE questions SET created_at=?", (ts,))
    db.commit()
    db.close()
    return ts


def events(c, pid):
    return c.get(f"/projects/{pid}/events").json()["events"]


def usage(c, pid):
    return c.get(f"/projects/{pid}/usage").json()["usage"]


def assert_guidance_shape(g):
    assert set(g) >= {"level", "text", "basis", "model_calls"}
    assert g["level"] in ("calm", "firm", "pointed")
    assert isinstance(g["text"], str) and g["text"].strip()
    assert isinstance(g["basis"], str) and g["basis"].strip()
    assert g["model_calls"] == 0
    for blob in (g["text"], g["basis"]):
        assert "deadline" not in blob.lower()


def test_a_fresh_waiting_is_calm(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    g = _single(client, pid)["guidance"]
    assert_guidance_shape(g)
    assert g["level"] == "calm"
    assert "no pressure" in g["text"].lower()
    assert "1 item waiting" in g["basis"]
    row = _row(_launch_rows(client), pid)
    assert_guidance_shape(row["guidance"])
    assert row["guidance"] == g


def test_b_aged_waiting_is_firm(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    backdate(client, 5)
    g = _single(client, pid)["guidance"]
    assert_guidance_shape(g)
    assert g["level"] == "firm"
    assert "act on it or defer it" in g["text"].lower()


def test_c_old_plus_multiple_waiting_is_pointed(client):
    pid = project(client)
    q0, r0 = ask_run(client, pid, DEMO_A)
    assert r0["result_kind"] == "ClarificationNeeded"
    q1, r1 = ask_run(client, pid, DEMO_C)
    assert r1["result_kind"] == "ClarificationNeeded"
    backdate(client, 20)
    pointed = _row(_launch_rows(client), pid)["guidance"]
    assert_guidance_shape(pointed)
    assert pointed["level"] == "pointed"
    assert "what the next step is" in pointed["text"].lower()
    assert "defer it" in pointed["text"].lower()


def test_d_unparseable_timestamp_does_not_escalate(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    db = sqlite3.connect(client.db_path)
    db.execute("UPDATE clarifications SET created_at='not-a-timestamp !!!' WHERE question_id=?", (q["question_id"],))
    db.commit()
    db.close()
    g = _single(client, pid)["guidance"]
    assert_guidance_shape(g)
    assert g["level"] == "calm"  # age unknown: still counts as waiting, never escalates
    assert "unknown" in g["basis"].lower()


def test_e_no_deadlines_invented(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    backdate(client, 20)
    for g in (_single(client, pid)["guidance"], _row(_launch_rows(client), pid)["guidance"]):
        assert_guidance_shape(g)
        assert "deadline" not in g["text"].lower() and "deadline" not in g["basis"].lower()


def test_f_per_project_isolation(client):
    pid_fresh, pid_old = project(client), project(client)
    qf, rf = ask_run(client, pid_fresh, DEMO_A)
    assert rf["result_kind"] == "ClarificationNeeded"
    qo, ro = ask_run(client, pid_old, DEMO_C)
    assert ro["result_kind"] == "ClarificationNeeded"
    db = sqlite3.connect(client.db_path)
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(timespec="seconds")
    db.execute("UPDATE clarifications SET created_at=? WHERE question_id=?", (old, qo["question_id"]))
    db.execute("UPDATE questions SET created_at=? WHERE id=?", (old, qo["question_id"]))
    db.commit()
    db.close()
    payload = _launch_rows(client)
    assert _row(payload, pid_fresh)["guidance"]["level"] == "calm"
    assert _row(payload, pid_old)["guidance"]["level"] == "firm"
    assert DEMO_C not in _single(client, pid_fresh)["guidance"]["text"]
    assert DEMO_A not in _single(client, pid_old)["guidance"]["text"]


def test_g_read_only_events_and_usage_unchanged(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    before_e, before_u = len(events(client, pid)), len(usage(client, pid))
    before_dump = "\n".join(sqlite3.connect(client.db_path).iterdump())
    _single(client, pid)
    _launch_rows(client)
    _single(client, pid)
    after_dump = "\n".join(sqlite3.connect(client.db_path).iterdump())
    assert (len(events(client, pid)), len(usage(client, pid))) == (before_e, before_u)
    assert after_dump == before_dump  # briefing reads write nothing at all


def test_h_session_only_content_never_leaks(client):
    pid = project(client)
    q = client.post(f"/projects/{pid}/questions", json={"text": SECRET, "memory": "session_only"}).json()
    client.post(f"/questions/{q['question_id']}/run")
    for g in (_single(client, pid)["guidance"], _row(_launch_rows(client), pid)["guidance"]):
        assert_guidance_shape(g)
        assert SECRET not in g["text"] and SECRET not in g["basis"]
        assert g["level"] == "calm"
    assert SECRET not in str(_single(client, pid)) and SECRET not in str(_launch_rows(client))


def test_i_existing_keys_unchanged_purely_additive(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO_A)
    assert r["result_kind"] == "ClarificationNeeded"
    single = _single(client, pid)
    assert set(single) >= {"project_id", "project", "parts", "top_items", "verified_at", "model_calls", "guidance", "scope"}
    assert set(single["parts"]) == {"where_you_left_off", "what_changed_since", "ready_now",
                                    "waiting_on_you", "suggested_next_action"}
    assert len(single["top_items"]) <= 3 and single["model_calls"] == 0 and single["verified_at"]
    payload = _launch_rows(client)
    assert payload["model_calls"] == 0 and payload["verified_at"]
    row = _row(payload, pid)
    assert set(row) >= {"project_id", "project", "parts", "top_items", "verified_at", "model_calls",
                        "guidance", "has_waiting", "waiting_count", "has_decision", "decision_count", "input_changed"}
    # Rows and per-project briefings carry the identical guidance object.
    assert row["guidance"] == single["guidance"]
    assert row["parts"] == single["parts"] and row["top_items"] == single["top_items"][:3]


def test_j_calm_with_decision_acknowledges_left_off(client):
    pid_empty, pid_decided = project(client), project(client)
    q, r = ask_run(client, pid_decided, DEMO_C)
    assert r["result_kind"] == "ClarificationNeeded"
    client.post(f"/projects/{pid_decided}/decisions",
                json={"question_id": q["question_id"], "verdict": "defer", "reason": "guidance calm check"})
    for pid in (pid_empty, pid_decided):
        g = _single(client, pid)["guidance"]
        assert_guidance_shape(g)
        assert g["level"] == "calm"
    assert "no pressure" in _single(client, pid_empty)["guidance"]["text"].lower()
    assert "decision" in _single(client, pid_decided)["guidance"]["text"].lower()
