"""Code-only Modal registration cannot start an endpoint through the HTTP API."""
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import harness, packs
from app.ai import secrets
from app.ai.modal_config import MODEL_ID
from app.ai.modal_provider import ModalProvider
from app.ai.provider import MetaProvider, BasetenProvider
from app.api import create_app
from test_dot_safety import StubAdapter


def test_modal_http_readiness_and_execution_barriers(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    database = tmp_path / "isolated.db"
    monkeypatch.setenv("WB_DB", str(database))
    monkeypatch.setenv("WB_MODAL_BASE_URL", "https://synthetic.us-east.modal.direct")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_ID", "synthetic-private-id")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_SECRET", "synthetic-private-secret")
    monkeypatch.setattr(harness, "ModelAdapter", StubAdapter)
    monkeypatch.setattr(packs, "INSTALLED_PACKS", {})
    monkeypatch.setattr(secrets, "key", lambda: "synthetic-meta-key")
    monkeypatch.setattr(secrets, "baseten_key", lambda: "synthetic-baseten-key")

    async def forbidden(*args, **kwargs):
        raise AssertionError("No transport or catalog probe is authorized")

    for transport in (ModalProvider, MetaProvider, BasetenProvider):
        monkeypatch.setattr(transport, "infer", forbidden)
        monkeypatch.setattr(transport, "catalog", forbidden)
    with TestClient(create_app(data_dir=data)) as client:
        response = client.get("/ai/registry")
        assert response.status_code == 200
        registry = response.json()
        modal = registry["settings"]["modal_connection"]
        assert modal["endpoint_configured"] is True
        assert modal["execution_enabled"] is False
        assert modal["billing"] == "gpu_time"
        candidate = next(row for row in registry["models"] if row["id"] == MODEL_ID)
        assert candidate["endpoint"] is None
        assert candidate["input_price"] is None and candidate["output_price"] is None
        assert "synthetic-private" not in response.text
        assert "synthetic-meta-key" not in response.text
        assert registry["tasks"]["research_query"] == "meta-models/muse-spark-open-1026"
        assert registry["tasks"]["methodology_review"] == "muse-wonder-1.0"
        assert client.post("/ai/verify-account", json={"provider": "modal"}).status_code == 409
        pid = client.post("/projects", json={"kind": "fx"}).json()["project_id"]
        question = client.post(f"/projects/{pid}/questions", json={"text": "Synthetic saved question."})
        assert question.status_code == 200, question.text
        qid = question.json()["question_id"]
        response = client.post(f"/questions/{qid}/ai/prepare", json={"model": MODEL_ID, "provider": "modal"})
        assert response.status_code == 409
        assert "GPU-time accounting" in response.json()["message"]
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM ai_spend").fetchone()[0] == 0
            record = {"provider": "modal", "model": MODEL_ID, "request": {"provider": "modal", "model": MODEL_ID}}
            db.execute(
                "INSERT INTO ai_jobs(id,question_id,status,request_json,snapshot,preview_hash,reservation,created_at)"
                " VALUES(?,?,'prepared',?,'snapshot','preview',1,?)",
                ("synthetic-modal-job", qid, json.dumps(record), time.time()))
        response = client.post("/ai/jobs/synthetic-modal-job/execute", json={"preview_hash": "preview", "consent": True})
        assert response.status_code == 409
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT status FROM ai_jobs WHERE id='synthetic-modal-job'").fetchone()[0] == "prepared"
            assert db.execute("SELECT count(*) FROM ai_spend").fetchone()[0] == 0
