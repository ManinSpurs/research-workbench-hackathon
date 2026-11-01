"""Lifecycle HTTP ownership and strictness against isolated SQLite, mock only."""
import asyncio
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import api
from app.harness import Harness
from test_preparation_runner import StubAdapter, QUESTION


def test_lifecycle_http(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    db = str(tmp_path / "http.sqlite")
    seed = Harness(db, data, StubAdapter())
    pid = seed.register_project("fx")["project_id"]
    qid = asyncio.run(seed.create_question(pid, QUESTION))["question_id"]
    asyncio.run(seed.run(qid))
    oid = seed.db.execute("SELECT id FROM clarifications WHERE question_id=?", (qid,)).fetchone()[0]
    seed.approve_background_scope(pid, ["DD-2023", "OFF-FAQ-2023"])
    seed.db.close()
    monkeypatch.setattr(api, "Harness", lambda path, directory: Harness(path, directory, StubAdapter()))
    with TestClient(api.create_app(db, data)) as client:
        body = dict(object_type="clarification", object_id=oid, scope_id=pid, client_action_id="http-1")
        url = f"/questions/{qid}/prepare-request"
        assert client.post(url, json={**body, "status":"completed"}).status_code == 409
        result = client.post(url, json=body)
        assert result.status_code == 200, result.text
        jid = result.json()["job_id"]
        assert client.post(url, json=body).json()["job_id"] == jid
        assert client.post(f"/questions/wrong/preparations/{jid}/cancel", json={}).status_code == 404
        assert client.post(f"/questions/{qid}/preparations/{jid}/cancel", json={"status":"cancelled"}).status_code == 409
        assert client.post(f"/questions/{qid}/preparations/{jid}/forged", json={}).status_code == 409
        listing = client.get(f"/questions/{qid}/preparations").json()
        assert len(listing["jobs"]) == 1
        assert listing["jobs"][0]["status"] == "completed"
        cid = listing["cards"][0]["id"]
        commit = f"/questions/{qid}/prepared-cards/{cid}/commit"
        assert client.post(commit, json={}).status_code == 200
        assert client.post(commit, json={"status":"adopted"}).status_code == 409
        assert client.post(f"/questions/wrong/prepared-cards/{cid}/commit", json={}).status_code == 404
