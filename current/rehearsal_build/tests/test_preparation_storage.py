"""Slice A1: Background Preparation storage + scope + marking (BACKGROUND_PREPARATION_DESIGN_EN §§2,3,5).

NO runner execution: no model-driven preparation runs, no commit_prepared_card,
no briefing wiring. Mock transports only (fake-meta subprocess); no credits.

Harness interface under test (owned by Slice A; routes already landed in Slice B):
  approve_background_scope(pid: str, allowed_sources: list[str]) -> dict
  mark_preparation(qid: str, object_type: str, object_id: str,
                   scope_id: str, client_action_id: str) -> dict
  list_preparations(qid: str) -> dict

scope_id choice: the project id itself is the stable scope identifier (documented
in harness.approve_background_scope). One background_scopes row per project;
re-approval replaces it in place, so scope_id never changes.

Fixture pattern follows tests/test_launch_briefing.py (subprocess fake-meta
server + TestClient over a copied data dir).
"""
import json
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

FX_ALLOWED = ["DD-2023", "OFF-FAQ-2023", "OFF-NOTES-2025", "OFF-INJECT"]
FX_BLOCKED = "OFF-BLOCKED"


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
    proc.wait(timeout=5)


@pytest.fixture
def client(fake, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app.preparation_queue import PreparationQueue
    # Storage-only slice: retain all real persistence and validation, but do
    # not dispatch execution. Actual scheduling has separate lifecycle tests.
    monkeypatch.setattr(PreparationQueue, "enqueue", lambda self, qid, jid: True)
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    app = create_app(str(tmp_path / "wb.db"), data)
    with TestClient(app) as c:
        c.data_dir = data
        c.db_path = str(tmp_path / "wb.db")
        yield c


def project(c, kind="fx"):
    return c.post("/projects", json={"kind": kind}).json()["project_id"]


def ask(c, pid, text, **kw):
    return c.post(f"/projects/{pid}/questions", json={"text": text, **kw}).json()


def approve(c, pid, sources):
    return c.post(f"/projects/{pid}/background-scope", json={"allowed_sources": sources})


def mark(c, qid, object_type, object_id, scope_id, action):
    return c.post(f"/questions/{qid}/prepare-request",
                  json={"object_type": object_type, "object_id": object_id,
                        "scope_id": scope_id, "client_action_id": action})


def db(c):
    return sqlite3.connect(c.db_path)


def db_dump(c):
    return "\n".join(db(c).iterdump())


def setup_deferred_decision(c, pid, text="Can I study time to final approval?"):
    """Ask + run (clarify route) + defer decision; returns (qid, decision_id, cq_id)."""
    q = ask(c, pid, text)
    qid = q["question_id"]
    r = c.post(f"/questions/{qid}/run").json()
    assert r["result_kind"] == "ClarificationNeeded", r
    d = c.post(f"/projects/{pid}/decisions",
               json={"question_id": qid, "verdict": "defer", "reason": "needs preparation"}).json()
    return qid, d["decision_id"], r["cq_id"]


def setup_pending_clarification(c, pid, text="Can I study time to final approval?"):
    q = ask(c, pid, text)
    qid = q["question_id"]
    r = c.post(f"/questions/{qid}/run").json()
    assert r["result_kind"] == "ClarificationNeeded", r
    return qid, r["cq_id"]


def setup_investigated_question(c, pid, text="What does est_cost measure in the 2023 permits?"):
    q = ask(c, pid, text)
    qid = q["question_id"]
    r = c.post(f"/questions/{qid}/run").json()
    assert r["result_kind"] == "EvidenceAssessment", r
    return qid


# ---------------------------------------------------------------- scope approval

def test_scope_approve_happy_path_and_stable_id(client):
    pid = project(client)
    res = approve(client, pid, ["DD-2023"])
    assert res.status_code == 200, res.text
    scope = res.json()
    assert scope["project_id"] == pid
    assert scope["scope_id"] == pid, "scope_id is the project id (stable across re-approvals)"
    assert scope["allowed_sources"] == ["DD-2023"]
    assert scope["bundle_sha256"]
    row = db(client).execute("SELECT * FROM background_scopes WHERE project_id=?", (pid,)).fetchone()
    assert row is not None and json.loads(row[1]) == ["DD-2023"]
    assert row[2] == scope["bundle_sha256"]
    # Stored bundle equals the project's current input_version.
    proj = db(client).execute("SELECT input_version FROM projects WHERE id=?", (pid,)).fetchone()
    assert scope["bundle_sha256"] == proj[0]
    # Re-approval replaces in place: one row, same scope_id, new sources.
    res2 = approve(client, pid, FX_ALLOWED)
    assert res2.status_code == 200, res2.text
    assert res2.json()["scope_id"] == pid
    assert res2.json()["allowed_sources"] == FX_ALLOWED
    rows = db(client).execute("SELECT * FROM background_scopes WHERE project_id=?", (pid,)).fetchall()
    assert len(rows) == 1


@pytest.mark.parametrize("sources, fragment", [
    (["NOPE-1"], "subset"),                       # not in the live allowlist
    (["DD-2023", "NOPE-1"], "subset"),            # partial subset still rejected
    ([FX_BLOCKED], "blocked"),                    # blocked source always excluded
    (["DD-2023", FX_BLOCKED], "blocked"),         # blocked poisons an otherwise valid set
    ([], "non-empty"),                            # empty list rejected by the harness
])
def test_scope_approval_violations_are_409(client, sources, fragment):
    pid = project(client)
    before = db(client).execute("SELECT count(*) FROM background_scopes").fetchone()[0]
    res = approve(client, pid, sources)
    assert res.status_code == 409, res.text
    assert fragment in res.json()["message"].lower()
    after = db(client).execute("SELECT count(*) FROM background_scopes").fetchone()[0]
    assert after == before, "rejected approval must store nothing"


def test_scope_approve_unknown_project_is_404(client):
    res = approve(client, "p-no-such-project", ["DD-2023"])
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------- eligible marks

def test_mark_deferred_decision(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    res = mark(client, qid, "decision", did, scope["scope_id"], "action-dec-1")
    assert res.status_code == 200, res.text
    job = res.json()
    assert job["status"] == "queued"
    assert job["job_id"] and job["question_id"] == qid and job["project_id"] == pid
    assert job["object_type"] == "decision" and job["object_id"] == did
    assert job["scope_id"] == pid and job["created_at"]
    rows = db(client).execute("SELECT * FROM preparation_jobs WHERE question_id=?", (qid,)).fetchall()
    assert len(rows) == 1 and rows[0][0] == job["job_id"] and rows[0][7] == "queued"
    # NEW tables only: consent-bound ai_jobs/ai_usage stay empty.
    assert db(client).execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0
    assert db(client).execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0


def test_mark_pending_clarification(client):
    pid = project(client)
    qid, cq = setup_pending_clarification(client, pid)
    scope = approve(client, pid, FX_ALLOWED).json()
    res = mark(client, qid, "clarification", cq, scope["scope_id"], "action-cq-1")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "queued"


@pytest.mark.parametrize("cq_status", ["pending", "deferred", "retryable"])
def test_mark_each_open_clarification_status(client, cq_status):
    pid = project(client)
    qid, cq = setup_pending_clarification(client, pid)
    if cq_status != "pending":
        con = db(client)
        con.execute("UPDATE clarifications SET status=? WHERE id=?", (cq_status, cq))
        con.commit()
        con.close()
    scope = approve(client, pid, ["DD-2023"]).json()
    res = mark(client, qid, "clarification", cq, scope["scope_id"], f"action-cq-{cq_status}")
    assert res.status_code == 200, res.text


def test_mark_investigated_question(client):
    pid = project(client)
    qid = setup_investigated_question(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    res = mark(client, qid, "question", qid, scope["scope_id"], "action-q-1")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "queued"


# ---------------------------------------------------------------- rejections

def test_mark_unknown_object_is_409(client):
    pid = project(client)
    qid, _, _ = setup_deferred_decision(client, pid)
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, "decision", "dec-missing", pid, "action-unk-1")
    assert res.status_code == 409, res.text
    assert "unknown object" in res.json()["message"]
    assert db(client).execute("SELECT count(*) FROM preparation_jobs").fetchone()[0] == 0


def test_mark_already_decided_object_is_409(client):
    pid = project(client)
    qid = ask(client, pid, "Can I study time to final approval?")["question_id"]
    assert c_post_run_clarify(client, qid)
    d = client.post(f"/projects/{pid}/decisions",
                    json={"question_id": qid, "verdict": "adopt", "reason": "good enough"}).json()
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, "decision", d["decision_id"], pid, "action-adopt-1")
    assert res.status_code == 409, res.text
    assert "already decided" in res.json()["message"]


def c_post_run_clarify(client, qid):
    r = client.post(f"/questions/{qid}/run").json()
    return r["result_kind"] == "ClarificationNeeded"


def test_mark_answered_clarification_is_409(client):
    pid = project(client)
    qid, cq = setup_pending_clarification(client, pid)
    con = db(client)
    con.execute("UPDATE clarifications SET status='answered' WHERE id=?", (cq,))
    con.commit()
    con.close()
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, "clarification", cq, pid, "action-ans-1")
    assert res.status_code == 409, res.text
    assert "already answered" in res.json()["message"]


def test_mark_cross_project_object_is_409(client):
    pid_a, pid_b = project(client), project(client)
    qid_a, _, _ = setup_deferred_decision(client, pid_a)
    qid_b, did_b, _ = setup_deferred_decision(client, pid_b)
    assert qid_a != qid_b
    approve(client, pid_a, ["DD-2023"])
    approve(client, pid_b, ["DD-2023"])
    res = mark(client, qid_a, "decision", did_b, pid_a, "action-cross-1")
    assert res.status_code == 409, res.text
    assert "cross-project" in res.json()["message"]


def test_mark_session_only_question_is_409(client):
    pid = project(client)
    qid = ask(client, pid, "Session-only prep probe?", memory="session_only")["question_id"]
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, "question", qid, pid, "action-sess-1")
    assert res.status_code == 409, res.text
    assert "session_only" in res.json()["message"]


@pytest.mark.parametrize("object_type", ["revisit_condition", "evidence", "answer", ""])
def test_mark_ineligible_object_type_is_409(client, object_type):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, object_type or "mystery", did, pid, f"action-type-{object_type or 'empty'}")
    # Empty/whitespace object_type is rejected by route shape; the rest by the harness.
    assert res.status_code == 409, res.text


def test_mark_scope_id_mismatch_is_409(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    approve(client, pid, ["DD-2023"])
    res = mark(client, qid, "decision", did, "scope-wrong", "action-scope-1")
    assert res.status_code == 409, res.text
    assert "scope_id" in res.json()["message"]


def test_mark_without_approved_scope_is_409(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    res = mark(client, qid, "decision", did, pid, "action-noscope-1")
    assert res.status_code == 409, res.text
    assert "no approved background scope" in res.json()["message"]


def test_mark_stale_scope_after_bundle_drift_is_409(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    approve(client, pid, ["DD-2023"])
    con = db(client)
    original = con.execute("SELECT input_version FROM projects WHERE id=?", (pid,)).fetchone()[0]
    con.execute("UPDATE projects SET input_version='tampered-bundle' WHERE id=?", (pid,))
    con.commit()
    try:
        res = mark(client, qid, "decision", did, pid, "action-drift-1")
        assert res.status_code == 409, res.text
        assert "stale" in res.json()["message"]
        assert db(client).execute("SELECT count(*) FROM preparation_jobs").fetchone()[0] == 0
    finally:
        con.execute("UPDATE projects SET input_version=? WHERE id=?", (original, pid))
        con.commit()
        con.close()


def test_mark_unknown_question_is_404(client):
    res = mark(client, "q-no-such-question", "decision", "d1", "s1", "action-404-1")
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------- idempotency

def test_mark_idempotent_same_action_returns_prior_job(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    approve(client, pid, ["DD-2023"])
    kwargs = dict(object_type="decision", object_id=did, scope_id=pid)
    first = mark(client, qid, kwargs["object_type"], kwargs["object_id"], kwargs["scope_id"], "action-idem-1")
    second = mark(client, qid, kwargs["object_type"], kwargs["object_id"], kwargs["scope_id"], "action-idem-1")
    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == first.json(), "duplicate must return the prior job record"
    rows = db(client).execute(
        "SELECT * FROM preparation_jobs WHERE question_id=?", (qid,)).fetchall()
    assert len(rows) == 1 and rows[0][0] == first.json()["job_id"]


def test_mark_same_action_different_object_is_409(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    qid2 = setup_investigated_question(client, pid)
    approve(client, pid, ["DD-2023"])
    assert mark(client, qid, "decision", did, pid, "action-reuse-1").status_code == 200
    res = mark(client, qid2, "question", qid2, pid, "action-reuse-1")
    assert res.status_code == 409, res.text


# ---------------------------------------------------------------- frozen manifest

def test_manifest_freezes_budget_bundle_and_pack(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    cur = db(client).execute(
        "SELECT * FROM question_budgets WHERE question_id=?", (qid,))
    row = cur.fetchone()
    before_budget = dict(zip([d[0] for d in cur.description], row))
    assert mark(client, qid, "decision", did, scope["scope_id"], "action-man-1").status_code == 200
    manifest = json.loads(db(client).execute(
        "SELECT manifest_json FROM preparation_jobs WHERE question_id=?", (qid,)).fetchone()[0])
    assert manifest["budget"]["limits"]["model_requests"] == 16
    assert manifest["budget"]["reserved_requests"] == before_budget["reserved_requests"]
    assert manifest["budget"]["tool_calls"] == before_budget["tool_calls"]
    proj = db(client).execute(
        "SELECT input_version, deletion_generation FROM projects WHERE id=?", (pid,)).fetchone()
    assert manifest["input_version"] == proj[0] == scope["bundle_sha256"]
    assert manifest["deletion_generation"] == proj[1]
    assert manifest["scope"] == {"scope_id": pid, "allowed_sources": ["DD-2023"],
                                 "bundle_sha256": scope["bundle_sha256"]}
    assert manifest["object"] == {"object_type": "decision", "object_id": did}
    context = client.get(f"/questions/{qid}/context").json()
    assert manifest["pack"] == {k: context[k] for k in
                                ("pack_id", "pack_version", "manifest_sha256", "capability_id")}


# ---------------------------------------------------------------- read-only list

def test_list_preparations_shape_and_read_only(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    assert mark(client, qid, "decision", did, scope["scope_id"], "action-list-1").status_code == 200
    before = db_dump(client)
    first = client.get(f"/questions/{qid}/preparations")
    second = client.get(f"/questions/{qid}/preparations")
    assert first.status_code == 200 and second.status_code == 200, first.text
    assert first.json() == second.json()
    payload = first.json()
    assert payload["question_id"] == qid
    assert set(payload) == {"question_id", "jobs", "cards", "scope", "runtime", "scope_options"}
    assert len(payload["jobs"]) == 1 and payload["jobs"][0]["status"] == "queued"
    assert payload["cards"] == [], "no runner exists in this slice, so no cards are committed"
    assert payload["scope"] == {"scope_id": pid,
                                "summary": ("Background scope: preparation may read only registered material plus "
                                            "1 permitted source (DD-2023); "
                                            f"registered bundle {scope['bundle_sha256']}."),
                                "allowed_sources": ["DD-2023"],
                                "bundle_sha256": scope["bundle_sha256"],
                                "stale": False}
    assert db_dump(client) == before, "list_preparations must not write"


def test_list_preparations_scope_none_without_approval(client):
    pid = project(client)
    qid, _, _ = setup_deferred_decision(client, pid)
    before = db_dump(client)
    res = client.get(f"/questions/{qid}/preparations")
    assert res.status_code == 200, res.text
    payload = res.json()
    assert set(payload) == {"question_id", "jobs", "cards", "scope", "runtime", "scope_options"}
    assert payload["scope"] is None, "no approved scope row exists for this project"
    assert payload["jobs"] == [] and payload["cards"] == []
    assert db_dump(client) == before, "list_preparations must not write"


def test_list_preparations_serves_drifted_scope_as_stale(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    assert mark(client, qid, "decision", did, scope["scope_id"], "action-stale-list-1").status_code == 200
    con = db(client)
    original = con.execute("SELECT input_version FROM projects WHERE id=?", (pid,)).fetchone()[0]
    con.execute("UPDATE projects SET input_version='tampered-bundle' WHERE id=?", (pid,))
    con.commit()
    try:
        before = db_dump(client)
        res = client.get(f"/questions/{qid}/preparations")
        assert res.status_code == 200, res.text
        payload = res.json()
        assert set(payload) == {"question_id", "jobs", "cards", "scope", "runtime", "scope_options"}
        served = payload["scope"]
        assert served is not None, "a drifted scope must be served with a stale indicator, not hidden"
        assert served["stale"] is True
        assert served["scope_id"] == pid
        assert served["allowed_sources"] == ["DD-2023"]
        assert served["bundle_sha256"] == scope["bundle_sha256"]
        assert "stale" in served["summary"].lower()
        assert "re-approve" in served["summary"].lower()
        assert served["summary"].startswith("Background scope: preparation may read only registered material plus ")
        drift = client.post(f"/questions/{qid}/prepare-request",
                            json={"object_type": "decision", "object_id": did,
                                  "scope_id": pid, "client_action_id": "action-stale-list-2"})
        assert drift.status_code == 409, drift.text
        assert "stale" in drift.json()["message"]
        assert db_dump(client) == before, "list_preparations must not write, even for a drifted scope"
    finally:
        con.execute("UPDATE projects SET input_version=? WHERE id=?", (original, pid))
        con.commit()
        con.close()


def test_list_preparations_unknown_question_is_404(client):
    res = client.get("/questions/q-no-such-question/preparations")
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------- deletion cascade

def test_delete_question_cascades_jobs_and_cards(client):
    pid = project(client)
    qid, did, _ = setup_deferred_decision(client, pid)
    scope = approve(client, pid, ["DD-2023"]).json()
    job = mark(client, qid, "decision", did, scope["scope_id"], "action-del-1").json()
    con = db(client)
    con.execute("INSERT INTO prepared_cards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("card-1", job["job_id"], pid, qid, "decision", did, "[]",
                 "Registered release only.", '["Actual cost unknown"]',
                 "draft_proposal", "2026-10-27T00:00:00+00:00"))
    con.commit()
    con.close()
    assert db(client).execute("SELECT count(*) FROM preparation_jobs WHERE question_id=?",
                              (qid,)).fetchone()[0] == 1
    assert db(client).execute("SELECT count(*) FROM prepared_cards WHERE question_id=?",
                              (qid,)).fetchone()[0] == 1
    res = client.delete(f"/questions/{qid}")
    assert res.status_code == 200, res.text
    assert res.json()["verified"] is True
    con = db(client)
    assert con.execute("SELECT count(*) FROM preparation_jobs WHERE question_id=?",
                       (qid,)).fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM prepared_cards WHERE question_id=?",
                       (qid,)).fetchone()[0] == 0
    # Per-project scopes survive question deletion: they are not per-question state.
    assert con.execute("SELECT count(*) FROM background_scopes WHERE project_id=?",
                       (pid,)).fetchone()[0] == 1
    con.close()
