"""HTTP-level tests for GET /tasks/{tid} (task-status introspection route).

Covers the route that had zero HTTP tests: only POST /tasks/{tid}/cancel was
exercised (tests/test_tier1.py). Fixture/client pattern copied exactly from
tests/test_tier1.py: in-process TestClient, fake Meta endpoint on loopback,
no network, no credits.

NOTE: the session `fake` fixture from test_tier1.py is duplicated here under
the file-unique name `task_status_fake` (it cannot live in conftest.py in this
scope). Both fixtures write the process-global FAKE_BASE_URL, last-writer-wins;
the `client` fixture below asserts FAKE_BASE_URL still points at this file's
fake server so a server swap can never pass silently.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEMO = "Can I study how long it takes from filing to the start of construction?"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="session")
def task_status_fake(tmp_path_factory):
    mode_file = tmp_path_factory.mktemp("fake") / "mode.txt"
    mode_file.write_text("")
    port = free_port()
    env = {**os.environ, "FAKE_MODE_FILE": str(mode_file), "PYDANTIC_AI_NO_BANNER": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.fake_meta", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["FAKE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["TASK_STATUS_FAKE_URL"] = os.environ["FAKE_BASE_URL"]
    # Sentinel recording which server THIS file started. test_tier1.py has its
    # own same-shaped session fixture writing the same global; `client` below
    # compares the two so a last-wins swap fails loudly instead of silently
    # talking to the other file's fake server (whose mode file we don't own).
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost"); os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)
    yield mode_file
    proc.terminate()


@pytest.fixture
def mode(task_status_fake):
    def set_mode(m: str):
        task_status_fake.write_text(m)
    yield set_mode
    task_status_fake.write_text("")


@pytest.fixture
def client(task_status_fake, tmp_path):
    # Guard against the known duplication hazard: if another session fixture
    # (e.g. test_tier1.py's `fake`) overwrote FAKE_BASE_URL after ours started,
    # building the app now would silently bind it to the wrong fake server.
    assert os.environ.get("FAKE_BASE_URL") == os.environ.get("TASK_STATUS_FAKE_URL"), \
        "FAKE_BASE_URL no longer points at this file's fake server"
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


def test_task_status_returns_real_shape(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    resp = client.get(f"/tasks/{r['task_id']}")
    assert resp.status_code == 200
    body = resp.json()
    # Real response shape from app/api.py task(): task_runs row + usage ledger.
    assert set(body) == {"id", "question_id", "project_id", "status", "created_at", "usage"}
    assert body["id"] == r["task_id"]
    assert body["question_id"] == q["question_id"]
    assert body["project_id"] == pid
    assert body["status"] == "awaiting_clarification"
    assert isinstance(body["usage"], list)
    # The ask+run flow above always records usage rows against this task_id
    # (Harness._invoke logs first_step, then plan/assess on the clarify path),
    # so an empty list would be a ledger regression, not "no usage yet".
    assert len(body["usage"]) >= 1
    for entry in body["usage"]:
        assert set(entry) == {"stage", "requests", "input_tokens", "output_tokens", "cost_usd"}


def test_task_status_unknown_id_returns_404(client):
    resp = client.get("/tasks/task-does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "message": "task not found", "details": {}}


def test_task_status_reflects_cancel(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    cancel_resp = client.post(f"/tasks/{r['task_id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["clarifications_closed"] == 1
    body = client.get(f"/tasks/{r['task_id']}").json()
    assert body["id"] == r["task_id"]
    assert body["status"] == "cancelled"
