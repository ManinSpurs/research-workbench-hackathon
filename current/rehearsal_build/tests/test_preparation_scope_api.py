"""Slice B: Background Scope + preparation trigger API (BACKGROUND_PREPARATION_DESIGN_EN §§1-3, §7 slice B).

Routes under test (all NEW in app/api.py; no existing route touched):
  POST /projects/{pid}/background-scope
  POST /questions/{qid}/prepare-request
  GET  /questions/{qid}/preparations

The Harness preparation methods belong to Slice A (a separate worker). These
tests therefore stub them with an in-memory fake (monkeypatched onto the
Harness class, so the TestClient app's own Harness instance uses it too) and
pass independently of Slice A. Mock transports only; no live calls.

Harness interface the routes call (Slice A owns the real methods):
  approve_background_scope(pid: str, allowed_sources: list[str]) -> dict
  mark_preparation(qid: str, object_type: str, object_id: str,
                   scope_id: str, client_action_id: str) -> dict
  list_preparations(qid: str) -> dict
"""
import asyncio
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient

from app import harness as module
from app.api import create_app
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)


def _seed(h, text="What does est_cost measure?"):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    return pid, qid


@pytest.fixture
def prep(h, monkeypatch):
    """In-memory stand-in for the Slice A Harness preparation methods.

    state: scopes/jobs/cards stores plus failure-injection hooks and call
    counters. Unknown project/question surface as KeyError (404) by delegating
    existence checks to the real project()/question() methods.
    """
    state = {"scopes": {}, "jobs": {}, "cards": [],
             "mark_calls": 0, "approve_calls": 0,
             "scope_conflict": None, "mark_conflict": None}

    def _live_input_version(pid):
        # Live source of truth for drift: the same projects.input_version the
        # real _scope_valid compares against. Kept as a free function (not a
        # Harness method) so the fake never shadows production symbols, and so
        # _list can pass the version in without a closure/self mix-up.
        return holder["harness"].project(pid)["input_version"]

    holder = {}

    def _scope_view(pid, live_input_version):
        # Mirrors the real Harness._preparation_scope_view block exactly (same
        # keys, same summary format, same stale rule): scope_id is the project
        # id, the summary is built only from stored sources + bundle, and bundle
        # drift (stored bundle != live input_version) is served with stale=True
        # plus re-approval wording per design §2, never hidden/raised.
        # NOTE: exact mirror, not a reuse — the real builder is inline in the
        # view and reads the background_scopes DB row, while this fake keeps
        # scopes in-memory; sharing code would couple the fake to a live DB row.
        record = state["scopes"].get(pid)
        if record is None:
            return None
        allowed = list(record["allowed_sources"])
        count = len(allowed)
        summary = ("Background scope: preparation may read only registered material plus "
                   f"{count} permitted source{'s' if count != 1 else ''} "
                   f"({', '.join(allowed)}); registered bundle {record['bundle_sha256']}.")
        stale = record["bundle_sha256"] != live_input_version
        if stale:
            summary += (" Scope is stale: the registered bundle changed since approval; "
                        "re-approve the scope explicitly before confirming.")
        return {"scope_id": pid,
                "summary": summary,
                "allowed_sources": allowed,
                "bundle_sha256": record["bundle_sha256"],
                "stale": stale}

    def _approve(self, pid, allowed_sources):
        state["approve_calls"] += 1
        self.project(pid)  # KeyError -> 404 for unknown projects
        if state["scope_conflict"]:
            raise Conflict(state["scope_conflict"])
        record = {"project_id": pid, "scope_id": pid,
                  "allowed_sources": list(allowed_sources),
                  "bundle_sha256": self.project(pid)["input_version"],
                  "approved_by": "researcher"}
        state["scopes"][pid] = record
        return record

    def _mark(self, qid, object_type, object_id, scope_id, client_action_id):
        self.question(qid)  # KeyError -> 404 for unknown questions
        if state["mark_conflict"]:
            raise Conflict(state["mark_conflict"])
        key = (qid, client_action_id)
        if key in state["jobs"]:
            return state["jobs"][key]  # idempotent replay: no second job
        q = self.question(qid)
        pid = q["project_id"]
        if pid not in state["scopes"]:
            raise Conflict("no approved background scope for this project; approve one explicitly")
        if scope_id != pid:
            raise Conflict("scope_id does not match the project's stored background scope")
        state["mark_calls"] += 1
        record = {"job_id": f"prep-{len(state['jobs']) + 1}", "project_id": pid,
                  "question_id": qid, "object_type": object_type, "object_id": object_id,
                  "scope_id": scope_id, "client_action_id": client_action_id,
                  "status": "queued"}
        state["jobs"][key] = record
        return record

    def _list(self, qid):
        holder["harness"] = self
        q = self.question(qid)  # KeyError -> 404 for unknown questions
        jobs = [j for (jq, _), j in state["jobs"].items() if jq == qid]
        cards = [c for c in state["cards"] if c["question_id"] == qid]
        return {"question_id": qid, "jobs": jobs, "cards": cards,
                "scope": _scope_view(q["project_id"],
                                     _live_input_version(q["project_id"]))}

    monkeypatch.setattr(module.Harness, "approve_background_scope", _approve, raising=False)
    monkeypatch.setattr(module.Harness, "mark_preparation", _mark, raising=False)
    monkeypatch.setattr(module.Harness, "list_preparations", _list, raising=False)
    # This slice stubs the runner; lifecycle dispatch is covered with real SQLite
    # by test_preparation_lifecycle_http.py, not by these in-memory fake rows.
    from app.preparation_queue import PreparationQueue
    monkeypatch.setattr(PreparationQueue, "enqueue", lambda self, qid, jid: True)
    return state


@pytest.fixture
def client(h, prep):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


# (i) scope approval happy path + readback shape
def test_scope_approval_happy_path(h, client, prep):
    pid, _ = _seed(h)
    res = client.post(f"/projects/{pid}/background-scope", json={"allowed_sources": ["DD-2023"]})
    assert res.status_code == 200, res.text
    scope = res.json()
    assert scope["project_id"] == pid
    assert scope["allowed_sources"] == ["DD-2023"]
    assert scope["scope_id"]
    assert scope["bundle_sha256"] == h.project(pid)["input_version"]
    assert prep["scopes"][pid]["scope_id"] == scope["scope_id"]
    assert prep["approve_calls"] == 1


# (ii) body-shape rejections
@pytest.mark.parametrize("body", [
    {"allowed_sources": ["DD-2023"], "extra": 1},   # extra keys
    {"allowed_sources": "DD-2023"},                  # non-list
    {"allowed_sources": ["DD-2023", ""]},            # empty string
    {"allowed_sources": ["   "]},                    # whitespace-only string
    {"allowed_sources": ["DD-2023", 7]},             # non-string member
    {"something_else": []},                          # missing key
    {},                                              # empty body
])
def test_scope_body_shape_rejected(h, client, prep, body):
    pid, _ = _seed(h)
    before = prep["approve_calls"]
    res = client.post(f"/projects/{pid}/background-scope", json=body)
    assert res.status_code == 409, res.text
    assert res.json()["error"] == "conflict"
    assert prep["approve_calls"] == before, "shape rejection must not reach harness"


@pytest.mark.parametrize("body", [
    {"object_type": "decision", "object_id": "d1", "scope_id": "s1",
     "client_action_id": "a1", "extra": 1},          # extra key
    {"object_type": "decision", "object_id": "d1", "scope_id": "s1"},  # missing key
    {"object_type": "", "object_id": "d1", "scope_id": "s1",
     "client_action_id": "a1"},                      # empty string
    {"object_type": "decision", "object_id": "d1", "scope_id": "s1",
     "client_action_id": 7},                         # non-string
])
def test_prepare_body_shape_rejected(h, client, prep, body):
    _, qid = _seed(h)
    before = prep["mark_calls"]
    res = client.post(f"/questions/{qid}/prepare-request", json=body)
    assert res.status_code == 409, res.text
    assert res.json()["error"] == "conflict"
    assert prep["mark_calls"] == before, "shape rejection must not reach harness"


# (iii) harness-side scope validation errors surface as 409
@pytest.mark.parametrize("message", [
    "allowed_sources must be a subset of the live input packet allowlist",
    "blocked source DD-BLOCKED is always excluded",
    "stale bundle: bundle_sha256 does not match projects.input_version",
])
def test_scope_validation_errors_are_409(h, client, prep, message):
    pid, _ = _seed(h)
    prep["scope_conflict"] = message
    res = client.post(f"/projects/{pid}/background-scope", json={"allowed_sources": ["DD-2023"]})
    assert res.status_code == 409, res.text
    assert res.json() == {"error": "conflict", "message": message, "details": {}}
    assert pid not in prep["scopes"]


# (iv) prepare-request happy path returns the job record
def test_prepare_request_happy_path(h, client, prep):
    pid, qid = _seed(h)
    scope = client.post(f"/projects/{pid}/background-scope",
                        json={"allowed_sources": ["DD-2023"]}).json()
    assert scope["scope_id"] == pid, "scope_id is the project id"
    body = {"object_type": "decision", "object_id": "d1",
            "scope_id": pid, "client_action_id": "action-1"}
    res = client.post(f"/questions/{qid}/prepare-request", json=body)
    assert res.status_code == 200, res.text
    job = res.json()
    assert job["question_id"] == qid
    assert job["object_type"] == "decision" and job["object_id"] == "d1"
    assert job["scope_id"] == pid
    assert job["status"] == "queued" and job["job_id"]
    assert prep["mark_calls"] == 1


def test_prepare_request_scope_id_mismatch_is_409(h, client, prep):
    pid, qid = _seed(h)
    client.post(f"/projects/{pid}/background-scope",
                json={"allowed_sources": ["DD-2023"]})
    body = {"object_type": "decision", "object_id": "d1",
            "scope_id": "scope-wrong", "client_action_id": "action-mismatch-1"}
    res = client.post(f"/questions/{qid}/prepare-request", json=body)
    assert res.status_code == 409, res.text
    assert "scope_id" in res.json()["message"]
    assert prep["mark_calls"] == 0, "mismatched scope must not create a job"


# (v) client_action_id idempotency: same record, exactly one mark call
def test_prepare_request_idempotent(h, client, prep):
    pid, qid = _seed(h)
    client.post(f"/projects/{pid}/background-scope",
                json={"allowed_sources": ["DD-2023"]})
    body = {"object_type": "clarification", "object_id": "c1",
            "scope_id": pid, "client_action_id": "action-9"}
    first = client.post(f"/questions/{qid}/prepare-request", json=body)
    second = client.post(f"/questions/{qid}/prepare-request", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == first.json(), "duplicate must return the prior record"
    assert prep["mark_calls"] == 1
    other = client.post(f"/questions/{qid}/prepare-request",
                        json={**body, "client_action_id": "action-10"})
    assert other.status_code == 200 and other.json()["job_id"] != first.json()["job_id"]
    assert prep["mark_calls"] == 2


# (vi) each 409 rejection case for prepare-request
@pytest.mark.parametrize("message", [
    "unknown object: decision d-missing does not exist",
    "object already decided",
    "object already answered",
    "object deleted",
    "stale scope_id: bundle changed since approval",
    "session_only questions cannot be prepared",
    "cross-project object_id",
    "object_type revisit_condition is not eligible in the hackathon slice",
])
def test_prepare_rejections_are_409(h, client, prep, message):
    pid, qid = _seed(h)
    client.post(f"/projects/{pid}/background-scope",
                json={"allowed_sources": ["DD-2023"]})
    prep["mark_conflict"] = message
    body = {"object_type": "decision", "object_id": "d1",
            "scope_id": pid, "client_action_id": "action-2"}
    res = client.post(f"/questions/{qid}/prepare-request", json=body)
    assert res.status_code == 409, res.text
    assert res.json() == {"error": "conflict", "message": message, "details": {}}
    assert prep["mark_calls"] == 0


# (vii) GET preparations is read-only and shows the scope block + draft cards
def test_get_preparations_read_only_with_scope_and_draft_cards(h, client, prep):
    pid, qid = _seed(h)
    scope = client.post(f"/projects/{pid}/background-scope",
                        json={"allowed_sources": ["DD-2023", "OFF-FAQ-2023"]}).json()
    assert scope["scope_id"] == pid, "scope_id is the project id"
    body = {"object_type": "decision", "object_id": "d1",
            "scope_id": pid, "client_action_id": "action-3"}
    assert client.post(f"/questions/{qid}/prepare-request", json=body).status_code == 200
    prep["cards"].append({"card_id": "card-1", "job_id": "prep-1",
                         "question_id": qid, "object_type": "decision", "object_id": "d1",
                         "status": "draft_proposal",
                         "evidence_ids": ["DD-2023:est_cost"],
                         "limitations_text": "Registered release only; actual cost unknown.",
                         "unresolved": ["Actual cost unknown"]})
    before_state = copy.deepcopy({k: v for k, v in prep.items()})
    before_dump = "\n".join(h.db.iterdump())
    first = client.get(f"/questions/{qid}/preparations")
    second = client.get(f"/questions/{qid}/preparations")
    assert first.status_code == 200 and second.status_code == 200, first.text
    assert first.json() == second.json()
    payload = first.json()
    assert payload["question_id"] == qid
    assert set(payload) == {"question_id", "jobs", "cards", "scope"}
    assert len(payload["jobs"]) == 1 and payload["jobs"][0]["status"] == "queued"
    assert len(payload["cards"]) == 1
    assert payload["cards"][0]["status"] == "draft_proposal"
    assert payload["scope"] == {"scope_id": pid,
                                "summary": ("Background scope: preparation may read only registered material plus "
                                            "2 permitted sources (DD-2023, OFF-FAQ-2023); "
                                            f"registered bundle {scope['bundle_sha256']}."),
                                "allowed_sources": ["DD-2023", "OFF-FAQ-2023"],
                                "bundle_sha256": scope["bundle_sha256"],
                                "stale": False}
    assert prep == before_state, "GET must not mutate preparation state"
    assert "\n".join(h.db.iterdump()) == before_dump, "GET must not write to the DB"


def test_get_preparations_serves_drifted_scope_as_stale(h, client, prep):
    # Route-layer stale path: the fake stores an approved scope whose bundle no
    # longer matches the live project input_version; GET must serve it with
    # stale=True + re-approval wording (design §2), never hide it, and stay
    # read-only. Drift is injected only by mutating the live project version
    # already present in the harness DB — no invented bundle content.
    pid, qid = _seed(h)
    scope = client.post(f"/projects/{pid}/background-scope",
                        json={"allowed_sources": ["DD-2023"]}).json()
    assert scope["scope_id"] == pid, "scope_id is the project id"
    live = h.db.execute("SELECT input_version FROM projects WHERE id=?", (pid,)).fetchone()
    original = live["input_version"] if isinstance(live, dict) else live[0]
    h.db.execute("UPDATE projects SET input_version='tampered-bundle' WHERE id=?", (pid,))
    h.db.commit()
    try:
        before_state = copy.deepcopy({k: v for k, v in prep.items()})
        before_dump = "\n".join(h.db.iterdump())
        first = client.get(f"/questions/{qid}/preparations")
        second = client.get(f"/questions/{qid}/preparations")
        assert first.status_code == 200 and second.status_code == 200, first.text
        assert first.json() == second.json()
        payload = first.json()
        assert set(payload) == {"question_id", "jobs", "cards", "scope"}
        served = payload["scope"]
        assert served is not None, "a drifted scope must be served with a stale indicator, not hidden"
        assert served["stale"] is True
        assert served["scope_id"] == pid
        assert served["allowed_sources"] == ["DD-2023"]
        assert served["bundle_sha256"] == scope["bundle_sha256"]
        assert "stale" in served["summary"].lower()
        assert "re-approve" in served["summary"].lower()
        assert prep == before_state, "GET must not mutate preparation state"
        assert "\n".join(h.db.iterdump()) == before_dump, "GET must not write to the DB"
    finally:
        h.db.execute("UPDATE projects SET input_version=? WHERE id=?", (original, pid))
        h.db.commit()


def test_get_preparations_scope_none_without_approval(h, client, prep):
    _, qid = _seed(h)
    res = client.get(f"/questions/{qid}/preparations")
    assert res.status_code == 200, res.text
    payload = res.json()
    assert set(payload) == {"question_id", "jobs", "cards", "scope"}
    assert payload["scope"] is None, "no approved scope exists for this project"


# (viii) unknown project/question -> 404 per existing route conventions
def test_unknown_project_scope_is_404(client):
    res = client.post("/projects/p-no-such-project/background-scope",
                      json={"allowed_sources": ["DD-2023"]})
    assert res.status_code == 404, res.text
    assert res.json()["error"] == "not_found"


def test_unknown_question_prepare_and_get_are_404(client):
    body = {"object_type": "decision", "object_id": "d1",
            "scope_id": "scope-1", "client_action_id": "action-4"}
    res = client.post("/questions/q-no-such-question/prepare-request", json=body)
    assert res.status_code == 404, res.text
    assert res.json()["error"] == "not_found"
    res = client.get("/questions/q-no-such-question/preparations")
    assert res.status_code == 404, res.text
    assert res.json()["error"] == "not_found"
