"""One Origin policy for the whole local API (app/api.py).

The confirmed defect: the earlier guards covered only the AI routes and the LaTeX
compile route, so a foreign page could send a *simple* cross-site request
(Content-Type: text/plain, no preflight) to POST /session/end and destroy
session-only questions. A browser enforces nothing here on its own: CORS tells a
page whether it may READ a response, it does not stop the request from running.

Policy pinned here, in-process only (StubAdapter, copied sample data, isolated DB,
no sockets):

* a request that carries NO Origin header is a local client (TestClient, CLI,
  build script) and stays compatible;
* a request that carries an Origin must name an accepted workbench origin for
  EVERY state-changing method (POST/PUT/PATCH/DELETE and any other non-safe verb),
  checked before the handler runs;
* the routes that ran a model keep their earlier read protection too (a foreign
  Origin on GET /ai/... or /library/screen is still refused);
* the literal Origin: null and any unlisted origin are refused, never accepted.

Nothing here claims a real remote browser was driven; the repro is the request a
foreign page is able to send.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import harness as harness_module
from app.api import create_app
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared isolated harness fixture)

SECRET = "PRIVATE-SESSION-ONLY-9187: does this leak?"
FOREIGN = "https://evil.example"
ALLOWED = "http://localhost:5173"


@pytest.fixture
def stub(h, monkeypatch):
    """The adapter the HTTP app itself will use, so model calls are observable."""
    adapter = StubAdapter()
    monkeypatch.setattr(harness_module, "ModelAdapter", lambda: adapter)
    return adapter


@pytest.fixture
def client(h, stub):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


def snapshot(h):
    """Whole-DB row dump, so "nothing was written" is checked, not assumed."""
    tables = [r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    snap = {}
    for t in tables:
        try:
            snap[t] = [tuple(row) for row in h.db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
        except Exception:
            cols = [c["name"] for c in h.db.execute(f"PRAGMA table_info('{t}')")]
            order = ", ".join(f'"{c}"' for c in cols) if cols else "1"
            snap[t] = [tuple(row) for row in h.db.execute(f'SELECT * FROM "{t}" ORDER BY {order}')]
    return snap


def project(c, kind="fx"):
    return c.post("/projects", json={"kind": kind}).json()["project_id"]


def session_only(c, pid, text=SECRET):
    body = c.post(f"/projects/{pid}/questions", json={"text": text, "memory": "session_only"}).json()
    return body["question_id"]


def request_for(method, path, origin=None):
    headers = [(b"host", b"127.0.0.1:8123")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"",
                    "root_path": "", "headers": headers, "server": ("127.0.0.1", 8123),
                    "client": ("127.0.0.1", 50000)})


# ------------------------------------------------------------------ (1) exact defect
def test_foreign_simple_post_session_end_is_refused_and_question_kept(client, h, stub):
    pid = project(client)
    qid = session_only(client, pid)
    assert client.get(f"/questions/{qid}/deletion-preview").status_code == 200
    before = snapshot(h)
    calls_before = list(stub.calls)

    # Exactly what a foreign page can send without a preflight: text/plain, no body.
    rejected = client.post("/session/end", content=b"",
                           headers={"Origin": FOREIGN, "Content-Type": "text/plain"})
    assert rejected.status_code == 403
    assert rejected.json()["error"] == "origin_rejected"

    # Nothing moved: same rows, same ledger, no model call.
    assert snapshot(h) == before
    assert stub.calls == calls_before
    stored = h.db.execute("SELECT memory, original_text FROM questions WHERE id=?", (qid,)).fetchone()
    assert stored is not None and stored["memory"] == "session_only" and stored["original_text"] == SECRET
    preview = client.get(f"/questions/{qid}/deletion-preview")
    assert preview.status_code == 200 and preview.json()["question_removed"]["count"] == 1
    assert h.db.execute("SELECT count(*) c FROM usage_ledger").fetchone()["c"] == len(before.get("usage_ledger", []))


def test_literal_null_and_unlisted_origins_are_refused(client):
    for origin in ("null", "https://localhost:5173.evil.example", "http://127.0.0.1:5173.evil.example",
                   "http://localhost:5174", ""):
        r = client.post("/session/end", headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["error"] == "origin_rejected", origin


# ------------------------------------------------------------------ (2) intended clients
def test_allowed_and_no_origin_clients_still_end_the_session(client, h):
    pid = project(client)
    same = session_only(client, pid, SECRET + " same-origin")
    local = session_only(client, pid, SECRET + " no-origin")
    out = client.post("/session/end", headers={"Origin": ALLOWED})
    assert out.status_code == 200 and out.json()["deleted"] == 2
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id IN (?,?)", (same, local)).fetchone()["c"] == 0
    # A no-Origin local client keeps working, including a repeat call.
    out = client.post("/session/end")
    assert out.status_code == 200 and out.json()["deleted"] == 0


def test_runtime_origin_swap_is_honoured(client):
    # The packaged build and the desktop review scripts replace ORIGINS in place
    # (packaging/backend_server.py: `api_module.ORIGINS[:] = [...]`, a random
    # frontend port), so the guard must read the module list at call time. The
    # slice assignment is reproduced exactly, and the original list is restored.
    import app.api as api_module
    original = list(api_module.ORIGINS)
    try:
        api_module.ORIGINS[:] = ["http://127.0.0.1:49152", "http://localhost:49152"]
        assert client.post("/session/end", headers={"Origin": "http://127.0.0.1:49152"}).status_code == 200
        assert client.post("/session/end", headers={"Origin": ALLOWED}).status_code == 403
    finally:
        api_module.ORIGINS[:] = original


# ------------------------------------------------------------------ (3) representative writes
def test_foreign_origin_refused_on_representative_writes(client, h):
    pid = project(client)
    qid = session_only(client, pid, SECRET + " keep")
    draft = client.post(f"/projects/{pid}/drafts", json={"title": "Paper"}).json()
    folder = client.post("/library/folders", json={"project_id": pid, "name": "Notes"}).json()
    turn = client.post(f"/projects/{pid}/secretary-turns",
                       json={"role": "researcher", "body": {"text": "kept note"}}).json()
    before = snapshot(h)
    foreign = {"Origin": FOREIGN}
    two_nodes = [{"label": "a", "dataset_id": None, "variable": None},
                 {"label": "b", "dataset_id": None, "variable": None}]

    cases = [
        ("create project", client.post("/projects", json={"kind": "fx"}, headers=foreign)),
        ("create question", client.post(f"/projects/{pid}/questions",
                                        json={"text": "another"}, headers=foreign)),
        ("update canvas", client.put(f"/questions/{qid}/canvas", headers=foreign,
                                     json={"expected_revision": 0, "nodes": two_nodes, "edges": []})),
        ("reorder projects", client.put("/projects/order", json={"project_ids": [pid]}, headers=foreign)),
        ("delete question", client.delete(f"/questions/{qid}", headers=foreign)),
        ("delete turn", client.delete(f"/secretary-turns/{turn['id']}", headers=foreign)),
        ("rename library folder", client.patch(f"/library/folders/{folder['id']}",
                                               json={"name": "renamed"}, headers=foreign)),
        ("compile", client.post(f"/drafts/{draft['draft_id']}/compile", json={"source": "manual"},
                                headers=foreign)),
        ("ai settings", client.post("/ai/settings", json={"cloud_enabled": True, "budget_usd": 10},
                                    headers=foreign)),
        ("end session", client.post("/session/end", headers=foreign)),
    ]
    for label, response in cases:
        assert response.status_code == 403, label
        assert response.json()["error"] == "origin_rejected", label

    assert snapshot(h) == before
    assert client.get("/ai/registry").json()["settings"]["cloud_enabled"] is False


def test_same_origin_and_no_origin_writes_still_apply(client, h):
    pid = project(client)
    qid = client.post(f"/projects/{pid}/questions", json={"text": "keep me"},
                      headers={"Origin": ALLOWED}).json()["question_id"]
    canvas = client.put(f"/questions/{qid}/canvas", headers={"Origin": ALLOWED},
                        json={"expected_revision": 0,
                              "nodes": [{"label": "a", "dataset_id": None, "variable": None},
                                        {"label": "b", "dataset_id": None, "variable": None}],
                              "edges": []})
    assert canvas.status_code == 200 and canvas.json()["revision"] == 1
    assert client.put("/projects/order", json={"project_ids": [pid]}).status_code == 200
    assert client.delete(f"/questions/{qid}").status_code == 200
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id=?", (qid,)).fetchone()["c"] == 0


# ------------------------------------------------------------------ (4) earlier model protections
def test_foreign_origin_still_refused_on_model_reads(client):
    pid = project(client)
    qid = session_only(client, pid, SECRET + " ai")
    for path in ("/ai/registry", f"/questions/{qid}/ai/jobs", f"/questions/{qid}/ai/assets"):
        r = client.get(path, headers={"Origin": FOREIGN})
        assert r.status_code == 403 and r.json()["error"] == "origin_rejected", path
    # ... and the allowed frontend origin still reads them.
    assert client.get("/ai/registry", headers={"Origin": ALLOWED}).status_code == 200


def test_screen_keeps_its_own_rejection(client):
    pid = project(client)
    for origin in (FOREIGN, "null"):
        r = client.post("/library/screen", json={"project_id": pid, "item_ids": []}, headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["error"] == "origin_rejected", origin


def test_plain_reads_stay_open_as_before(client):
    # Deliberate existing boundary, unchanged by this fix: reads that run no
    # model stay readable from any local page (tests/test_library.py pins it too).
    pid = project(client)
    assert client.get("/library/tree", params={"project_id": pid},
                      headers={"Origin": FOREIGN}).status_code == 200
    assert client.get("/projects", headers={"Origin": FOREIGN}).status_code == 200


# ------------------------------------------------------------------ (5) method coverage
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "purge"])
@pytest.mark.parametrize("path", ["/session/end", "/projects", "/questions/q1"])
def test_every_state_changing_method_is_guarded(method, path):
    from app.api import origin_rejected
    assert origin_rejected(request_for(method, path, FOREIGN)) is True
    assert origin_rejected(request_for(method, path, "null")) is True
    assert origin_rejected(request_for(method, path, ALLOWED)) is False
    assert origin_rejected(request_for(method, path, None)) is False


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_only_guarded_on_model_paths(method):
    from app.api import origin_rejected
    assert origin_rejected(request_for(method, "/projects", FOREIGN)) is False
    assert origin_rejected(request_for(method, "/ai/registry", FOREIGN)) is True
    assert origin_rejected(request_for(method, "/library/screen", FOREIGN)) is True
