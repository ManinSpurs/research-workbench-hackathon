"""Secretary ready_now briefing engine: prepared-card part (model-free, read-only).

Drives ``Harness._project_briefing`` / ``launch_briefing`` through the public
``GET /projects/{pid}/briefing`` and ``GET /briefing`` routes after completing
real preparation jobs via the in-process stub adapter -- no API writes beyond
seeding, no live/paid calls. Fixture pattern follows
tests/test_launch_briefing.py (subprocess fake-meta server + TestClient).

What this pins (from BACKGROUND_PREPARATION_DESIGN_EN §6, as implemented in
harness.py `_ready_now_items`):
  * each completed prepared card whose job is still trustworthy produces one
    ready_now item in the briefing payload, shaped like every other briefing
    item ({text, saved_at}) plus real record references (card_id, job_id,
    question_id) and saved_at taken from the card's recorded created_at;
  * every item is labelled a draft proposal awaiting a researcher decision --
    never decided/confirmed work; no invented deadlines, metrics, or progress
    claims appear anywhere in the item;
  * non-completed cards (queued/draft-in-progress, failed/stopped_*, cancelled,
    interrupted, or non-draft status) produce NO ready_now item;
  * deterministic ordering: project order follows the launch ordering rule;
    within a project, card creation order (prepared_cards rowid, then card
    id); no duplicates for the same card;
  * no completed cards -> the current honest fallback is preserved exactly:
    ready_now == [{"text": "Nothing ready.", "saved_at": None}].

The coach-recommendation hook (secretary_coach.py, S1/S2 slices) is out of
scope here by brief-owner instruction and is NOT exercised: no import of
app.secretary_coach exists in this file.
"""
import asyncio
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

from app.adapters import Usage
from app.harness import Harness
from app.results import (ClarificationNeeded, DesignProposal, EvidenceAssessment,
                         FirstStep, InvestigationPlan, RefinedQuestion, VariableRef)

QUESTION = "Can I study time to final approval?"
GAP = "Does approval_date indicate final approval?"
TARGET = "DD-2023"
FORBIDDEN = ("deadline", "due", "overdue", "confirmed", "progress",
             "complete%", "metric")


class StubAdapter:
    """Scripted in-process adapter: label 'mock' (mirrors test_preparation_runner.py)."""

    label = "mock"

    def __init__(self):
        self.unresolved = ["Final approval is not established; check status_code."]

    async def refine(self, question):
        return RefinedQuestion(refined_text=question, kept_constraints=[]), Usage(1, 1, 1)

    async def first_step(self, question, context):
        return FirstStep(
            route="clarify",
            design=DesignProposal(question=question,
                variables=[VariableRef(name="approval_date", dataset_id="fx/permits_2023.csv",
                                       kind="unverified", role="end (meaning unverified)")],
                assumptions=["approval_date marks final approval (unverified)"],
                risks=["approval_date may mark plan-review completion instead"],
                verification_plan="Read DD-2023 and the registered official pages"),
            clarification=ClarificationNeeded(gap=GAP, affected="approval_date (processing-time design)",
                why_matters="If yes design A is usable; if not, check status codes first.",
                branches=[], independent_work="status_code / issue_date check can continue"),
            direct_answer=None, alternatives=[]), "HISTORY", Usage(1, 1, 1)

    async def plan(self, history, prompt):
        return InvestigationPlan(target_gap="Meaning of approval_date", sources=[TARGET, "OFF-FAQ-2023"],
                                 fields=["approval_date"], stop_condition="Stop after the listed sources"), "HISTORY", Usage(1, 1, 1)

    async def assess(self, history, observations, observed_ids):
        assert "DD-2023:approval_date" in observed_ids
        return EvidenceAssessment(claim="approval_date records plan-review completion",
            evidence_ids=["DD-2023:approval_date"], applicability="DD-2023 applies to this extract.",
            outcome="partial", confirmation_level="partial", unresolved=list(self.unresolved),
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Check status codes before using approval_date as final approval."), "HISTORY", Usage(1, 1, 1)

    async def basic(self, question, materials):
        return "check definition", Usage(1, 1, 1)


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
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app import api
    monkeypatch.setattr(api, "Harness", lambda path, directory: Harness(path, directory, adapter=StubAdapter()))
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    app = create_app(str(tmp_path / "wb.db"), data)
    with TestClient(app) as c:
        c.data_dir = data
        c.db_path = str(tmp_path / "wb.db")
        yield c


@pytest.fixture
def harness(tmp_path):
    from app.harness import Harness as H
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    h = H(str(tmp_path / "wb.db"), data, adapter=StubAdapter())
    yield h
    h.db.close()


def project(c, kind="fx"):
    return c.post("/projects", json={"kind": kind}).json()["project_id"]


def ask_run(c, pid, text):
    q = c.post(f"/projects/{pid}/questions", json={"text": text}).json()
    return q, c.post(f"/questions/{q['question_id']}/run").json()


def briefing(c, pid):
    r = c.get(f"/projects/{pid}/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def launch(c):
    r = c.get("/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def db_rows(db_path, sql, args=()):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def await_card(c, qid, jid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = c.get(f"/questions/{qid}/preparations")
        assert response.status_code == 200, response.text
        data = response.json()
        job = next(j for j in data["jobs"] if j["id"] == jid)
        if job["status"] == "completed":
            card = next(card for card in data["cards"] if card["job_id"] == jid)
            return {"status": "completed", "card": {**card, "card_id": card["id"]}}
        assert job["status"] in ("queued", "running", "paused_foreground"), job
        time.sleep(0.01)
    pytest.fail(f"app queue did not complete preparation {jid}: {data}")


def complete_card(c, pid, text=QUESTION, action="action-ready-1"):
    """Run a full preparation job to 'completed' through the public API + runner."""
    q, run = ask_run(c, pid, text)
    assert run["result_kind"] == "ClarificationNeeded", run
    dec = c.post(f"/projects/{pid}/decisions",
                 json={"question_id": q["question_id"], "verdict": "defer",
                       "reason": "needs preparation"}).json()
    assert c.post(f"/projects/{pid}/background-scope",
                  json={"allowed_sources": [TARGET, "OFF-FAQ-2023"]}).status_code in (200, 201)
    mark = c.post(f"/questions/{q['question_id']}/prepare-request",
                  json={"object_type": "decision", "object_id": dec["decision_id"],
                        "scope_id": pid, "client_action_id": action}).json()
    out = await_card(c, q["question_id"], mark["job_id"])
    assert out["status"] == "completed", out
    return q, dec, mark, out["card"]


def assert_draft_item(item, q, mark):
    assert set(item) >= {"text", "saved_at", "card_id", "job_id", "question_id"}
    assert isinstance(item["text"], str) and item["text"].strip()
    lowered = item["text"].lower()
    assert "draft proposal" in lowered and "nothing is decided" in lowered
    assert "confirmed" not in lowered and "decided" not in lowered.replace("nothing is decided", "")
    for blob in (item["text"], str(item["saved_at"])):
        assert not any(w in blob.lower() for w in ("deadline", "overdue")), blob
    assert item["job_id"] == mark["job_id"]
    assert item["question_id"] == q["question_id"]
    assert item["saved_at"]  # real recorded card created_at, never None/invented
    assert mark["object_id"] in item["text"] and q["question_id"] in item["text"]


# ---------------------------------------------------------------- fallback preserved

def test_no_cards_keeps_honest_fallback(client):
    pid = project(client)
    ask_run(client, pid, QUESTION)
    single = briefing(client, pid)
    assert single["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    rows = launch(client)["rows"]
    row = next(r for r in rows if r["project_id"] == pid)
    assert row["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    assert row["parts"] == single["parts"]  # launch row inherits _project_briefing exactly


def test_empty_project_fallback(client):
    pid = project(client)
    assert briefing(client, pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


# ---------------------------------------------------------------- completed card -> one ready_now item

def test_completed_card_produces_one_draft_item(client):
    pid = project(client)
    q, dec, mark, card = complete_card(client, pid)
    single = briefing(client, pid)
    assert single["model_calls"] == 0
    items = single["parts"]["ready_now"]
    assert len(items) == 1
    assert_draft_item(items[0], q, mark)
    card_row = db_rows(client.db_path, "SELECT * FROM prepared_cards WHERE id=?",
                       (items[0]["card_id"],))[0]
    assert items[0]["saved_at"] == card_row["created_at"]  # real row timestamp
    rows = launch(client)["rows"]
    row = next(r for r in rows if r["project_id"] == pid)
    assert row["parts"]["ready_now"] == items  # identical object through launch_briefing


def test_item_carries_no_invented_claims(client):
    pid = project(client)
    q, dec, mark, card = complete_card(client, pid)
    item = briefing(client, pid)["parts"]["ready_now"][0]
    for forbidden in FORBIDDEN:
        assert forbidden not in item["text"].lower(), forbidden


def test_launch_isolation_card_only_in_own_project(client):
    pid_a, pid_b = project(client), project(client)
    q_a, dec_a, mark_a, _ = complete_card(client, pid_a, action="action-iso-a")
    single_a, single_b = briefing(client, pid_a), briefing(client, pid_b)
    assert len(single_a["parts"]["ready_now"]) == 1
    assert single_b["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    payload = str(launch(client))
    assert mark_a["job_id"] in payload and q_a["question_id"] in payload
    row_b = next(r for r in launch(client)["rows"] if r["project_id"] == pid_b)
    # Real isolation (not a tautology): project A's card text, job id,
    # card id, question id and object id appear nowhere in the serialized
    # rendering of project B's own row.
    row_b_text = str(row_b)
    assert single_a["parts"]["ready_now"][0]["text"] not in row_b_text
    assert single_a["parts"]["ready_now"][0]["card_id"] not in row_b_text
    assert mark_a["job_id"] not in row_b_text
    assert q_a["question_id"] not in row_b_text
    assert mark_a["object_id"] not in row_b_text


def test_queued_job_produces_no_item(client, monkeypatch):
    from app.preparation_queue import PreparationQueue
    # Hold this one job at the queue boundary so the queued-state briefing
    # assertion is deterministic. Completion tests exercise the real worker.
    monkeypatch.setattr(PreparationQueue, "enqueue", lambda self, qid, jid: True)
    pid = project(client)
    q, run = ask_run(client, pid, QUESTION)
    assert run["result_kind"] == "ClarificationNeeded"
    dec = client.post(f"/projects/{pid}/decisions",
                      json={"question_id": q["question_id"], "verdict": "defer",
                            "reason": "needs preparation"}).json()
    client.post(f"/projects/{pid}/background-scope",
                json={"allowed_sources": [TARGET, "OFF-FAQ-2023"]})
    client.post(f"/questions/{q['question_id']}/prepare-request",
                json={"object_type": "decision", "object_id": dec["decision_id"],
                      "scope_id": pid, "client_action_id": "action-queued-1"})
    assert briefing(client, pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_non_completed_statuses_write_path(harness):
    """Statuses other than completed/draft_proposal never surface via harness directly."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, QUESTION))["question_id"]
    assert asyncio.run(h.run(qid))["result_kind"] == "ClarificationNeeded"
    dec = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-status-1")
    # queued: nothing eligible
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    h.db.execute("UPDATE preparation_jobs SET status='running' WHERE id=?", (job["job_id"],))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_cancelled_and_failed_cards_excluded(harness):
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, QUESTION))["question_id"]
    assert asyncio.run(h.run(qid))["result_kind"] == "ClarificationNeeded"
    dec = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-cancel-1")
    h.cancel_preparation(job["job_id"])
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    # a stale stopped job with a leftover non-draft card still yields nothing
    h2 = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-cancel-2")
    h.db.execute("UPDATE preparation_jobs SET status='stopped_exhausted' WHERE id=?", (h2["job_id"],))
    h.db.execute("INSERT INTO prepared_cards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("card-stale-1", h2["job_id"], pid, qid, "decision", dec["decision_id"],
                  '[{"evidence_id": "x", "source_id": "DD-2023"}]', "limits", '["open item"]',
                  "completed", "2026-10-27T00:00:00+00:00"))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_superseded_object_card_excluded(harness):
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, QUESTION))["question_id"]
    assert asyncio.run(h.run(qid))["result_kind"] == "ClarificationNeeded"
    dec = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-sup-1")
    out = asyncio.run(h.run_preparation_step(job["job_id"]))
    assert out["status"] == "completed"
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    # a superseding adopt decision retires the deferred object -> item disappears
    h.decide(pid, qid, "adopt", "researcher decided", None)
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


# ---------------------------------------------------------------- drift-class: one bad card never breaks the briefing

def _harness_question(h, pid, text=QUESTION):
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    assert asyncio.run(h.run(qid))["result_kind"] == "ClarificationNeeded"
    return qid


def _complete_decision_card(h, pid, qid, action):
    dec = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", dec["decision_id"], pid, action)
    out = asyncio.run(h.run_preparation_step(job["job_id"]))
    assert out["status"] == "completed", out
    return dec, job, out["card"]


def test_missing_scope_row_excludes_card_briefing_succeeds(client):
    """Defect repro: the scope row deleted after completion makes the drift
    check raise Conflict; the GET briefing must exclude the card, not 500."""
    pid = project(client)
    q, dec, mark, card = complete_card(client, pid)
    assert len(briefing(client, pid)["parts"]["ready_now"]) == 1
    con = sqlite3.connect(client.db_path)
    try:
        con.execute("DELETE FROM background_scopes WHERE project_id=?", (pid,))
        con.commit()
    finally:
        con.close()
    ready = briefing(client, pid)["parts"]["ready_now"]  # GET asserts 200
    assert ready == [{"text": "Nothing ready.", "saved_at": None}]
    rows = launch(client)["rows"]
    row = next(r for r in rows if r["project_id"] == pid)
    assert row["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_stale_scope_bundle_excludes_card(harness):
    """Rotated/tampered scope bundle: _scope_valid raises Conflict -> excluded."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = _harness_question(h, pid)
    _complete_decision_card(h, pid, qid, "action-stale-scope-1")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("UPDATE background_scopes SET bundle_sha256='stale-bundle' WHERE project_id=?", (pid,))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_missing_pack_binding_excludes_card(harness):
    """Pack binding gone (legacy/mismatched): question_context raises Conflict
    -> the card is excluded and the briefing still succeeds."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = _harness_question(h, pid)
    _complete_decision_card(h, pid, qid, "action-stale-pack-1")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("DELETE FROM question_contexts WHERE question_id=?", (qid,))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_deleted_question_excludes_card(harness):
    """Question row deleted with the card left behind: the briefing join drops
    the orphan card and still returns the honest fallback."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = _harness_question(h, pid)
    _complete_decision_card(h, pid, qid, "action-del-q-1")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("DELETE FROM question_contexts WHERE question_id=?", (qid,))
    h.db.execute("DELETE FROM questions WHERE id=?", (qid,))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_closed_clarification_excludes_card(harness):
    """Clarification object closed after completion: drift reports the object
    no longer open -> excluded, briefing still succeeds."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, QUESTION))["question_id"]
    run = asyncio.run(h.run(qid))
    assert run["result_kind"] == "ClarificationNeeded", run
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "clarification", run["cq_id"], pid, "action-clar-drift-1")
    out = asyncio.run(h.run_preparation_step(job["job_id"]))
    assert out["status"] == "completed", out
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("UPDATE clarifications SET status='answered' WHERE id=?", (run["cq_id"],))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


def test_corrupt_manifest_excludes_only_that_card(harness):
    """One card's unreadable manifest excludes only that card; the other good
    card in the same project still surfaces (per-card fail-closed)."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid_a = _harness_question(h, pid)
    _, job_a, card_a = _complete_decision_card(h, pid, qid_a, "action-manifest-a")
    qid_b = _harness_question(h, pid, "Can I study time to final approval for 2024?")
    _, job_b, card_b = _complete_decision_card(h, pid, qid_b, "action-manifest-b")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 2
    h.db.execute("UPDATE preparation_jobs SET manifest_json='not json' WHERE id=?", (job_a["job_id"],))
    h.db.commit()
    items = h._project_briefing(pid)["parts"]["ready_now"]
    assert len(items) == 1
    assert items[0]["card_id"] == card_b["card_id"]
    assert items[0]["job_id"] == job_b["job_id"]


def test_unexpected_drift_error_excludes_card(harness, caplog):
    """Fail-closed for ANY per-card drift error: even a backing-table fault
    (sqlite OperationalError, outside the old TypeError/ValueError/KeyError
    guard) excludes the card instead of breaking the briefing."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = _harness_question(h, pid)
    _complete_decision_card(h, pid, qid, "action-unexpected-1")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("DROP TABLE background_scopes")
    h.db.commit()
    with caplog.at_level("WARNING", logger="app.harness"):
        items = h._project_briefing(pid)["parts"]["ready_now"]  # must not 500
    assert items == [{"text": "Nothing ready.", "saved_at": None}]
    assert len(caplog.records) == 1  # exactly one exclusion, nothing else logged
    rec = caplog.records[0]
    assert rec.levelname == "WARNING"
    assert rec.name == "app.harness"
    assert pid in rec.getMessage()
    assert "OperationalError" in rec.getMessage()


def test_unexpected_drift_error_excludes_only_that_card(harness, caplog):
    """Per-card isolation for non-(TypeError, ValueError, KeyError) drift
    errors: one card whose drift check raises sqlite3.OperationalError is
    excluded (with a log record naming the bad card), while the other good
    card in the same project still surfaces."""
    import app.harness as harness_mod

    h = harness
    pid = h.register_project("fx")["project_id"]
    qid_a = _harness_question(h, pid)
    _, job_a, card_a = _complete_decision_card(h, pid, qid_a, "action-isolate-a")
    qid_b = _harness_question(h, pid, "Can I study time to final approval for 2024?")
    _, job_b, card_b = _complete_decision_card(h, pid, qid_b, "action-isolate-b")
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 2
    real_drift = h._preparation_drift

    def flaky_drift(job, manifest):
        if job["id"] == job_a["job_id"]:
            raise sqlite3.OperationalError("no such table: background_scopes")
        return real_drift(job, manifest)

    h._preparation_drift = flaky_drift
    try:
        with caplog.at_level("WARNING", logger="app.harness"):
            items = h._project_briefing(pid)["parts"]["ready_now"]
    finally:
        h._preparation_drift = real_drift
    assert len(items) == 1  # good card still surfaces
    assert items[0]["card_id"] == card_b["card_id"]
    assert items[0]["job_id"] == job_b["job_id"]
    assert len(caplog.records) == 1  # the bad card was logged, nothing else
    rec = caplog.records[0]
    assert harness_mod.logger.name == "app.harness"
    assert rec.name == "app.harness"
    msg = rec.getMessage()
    assert pid in msg and job_a["job_id"] in msg and card_a["card_id"] in msg
    assert "OperationalError" in msg
    assert job_b["job_id"] not in msg and card_b["card_id"] not in msg
    assert rec.exc_info is not None  # exception type/message captured


def test_paused_interrupted_stopped_stale_cards_excluded(harness):
    """paused_foreground / interrupted jobs surface nothing; a stopped_stale
    job with a leftover draft_proposal card is still excluded."""
    h = harness
    pid = h.register_project("fx")["project_id"]
    qid = _harness_question(h, pid)
    dec = h.decide(pid, qid, "defer", "needs preparation", None)
    h.approve_background_scope(pid, [TARGET, "OFF-FAQ-2023"])
    job = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-lifecycle-1")
    h.pause_preparation(job["job_id"])
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    h.db.execute("UPDATE preparation_jobs SET status='interrupted' WHERE id=?", (job["job_id"],))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    job2 = h.mark_preparation(qid, "decision", dec["decision_id"], pid, "action-lifecycle-2")
    out = asyncio.run(h.run_preparation_step(job2["job_id"]))
    assert out["status"] == "completed", out
    assert len(h._project_briefing(pid)["parts"]["ready_now"]) == 1
    h.db.execute("UPDATE preparation_jobs SET status='stopped_stale' WHERE id=?", (job2["job_id"],))
    h.db.commit()
    assert h._project_briefing(pid)["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]


# ---------------------------------------------------------------- ordering + no duplicates

def test_deterministic_order_no_duplicates(client):
    pid = project(client)
    q, dec, mark, card = complete_card(client, pid, action="action-ord-1")
    first = briefing(client, pid)["parts"]["ready_now"]
    again = briefing(client, pid)["parts"]["ready_now"]
    assert first == again  # deterministic repeat read
    assert len({i["card_id"] for i in first}) == len(first) == 1  # no duplicates
    # second completed card on another question appends in creation order
    q2, run2 = ask_run(client, pid, "Can I study time to final approval for 2024?")
    assert run2["result_kind"] == "ClarificationNeeded"
    dec2 = client.post(f"/projects/{pid}/decisions",
                       json={"question_id": q2["question_id"], "verdict": "defer",
                             "reason": "needs preparation too"}).json()
    mark2 = client.post(f"/questions/{q2['question_id']}/prepare-request",
                        json={"object_type": "decision", "object_id": dec2["decision_id"],
                              "scope_id": pid, "client_action_id": "action-ord-2"}).json()
    out2 = await_card(client, q2["question_id"], mark2["job_id"])
    assert out2["status"] == "completed", out2
    items = briefing(client, pid)["parts"]["ready_now"]
    assert [i["card_id"] for i in items] == [card["card_id"], out2["card"]["card_id"]]
    assert items == briefing(client, pid)["parts"]["ready_now"]  # stable across reads


def test_launch_rows_share_identical_ready_now(client):
    pid_a, pid_b = project(client), project(client)
    complete_card(client, pid_a, action="action-lr-a")
    rows = launch(client)["rows"]
    by_id = {r["project_id"]: r for r in rows}
    assert len(by_id[pid_a]["parts"]["ready_now"]) == 1
    assert by_id[pid_b]["parts"]["ready_now"] == [{"text": "Nothing ready.", "saved_at": None}]
    assert by_id[pid_a]["parts"]["ready_now"] == briefing(client, pid_a)["parts"]["ready_now"]


# ---------------------------------------------------------------- read-only

def test_briefing_writes_nothing(client):
    pid = project(client)
    complete_card(client, pid)
    before = "\n".join(r["sql"] or "" for r in
                       db_rows(client.db_path, "SELECT sql FROM sqlite_master WHERE type='table'"))
    dump_before = "\n".join(sqlite3.connect(client.db_path).iterdump())
    events_before = client.get(f"/projects/{pid}/events").json()
    usage_before = client.get(f"/projects/{pid}/usage").json()
    briefing(client, pid)
    launch(client)
    assert "\n".join(sqlite3.connect(client.db_path).iterdump()) == dump_before
    assert client.get(f"/projects/{pid}/events").json() == events_before
    assert client.get(f"/projects/{pid}/usage").json() == usage_before
    assert "\n".join(r["sql"] or "" for r in
                     db_rows(client.db_path, "SELECT sql FROM sqlite_master WHERE type='table'")) == before
