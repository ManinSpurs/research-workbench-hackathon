"""Envelope-shape logging on provider gate failures. Mock transports only.

When a provider response fails the model-identity or completion gate in
``AIService.execute``'s ``invoke()``, the persisted stage record captures the
response envelope's SHAPE only: top-level key names, the status token, and
output-item type labels/count. Message text, content, deltas and any other
values are never persisted. Gate semantics are unchanged.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import httpx
import pytest

from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

SECRET = "SECRET-MARKER-TEXT"

RESULT = {
    "answer": "Reported estimate, not actual cost.",
    "claims": [
        {"text": "Applicant estimate", "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"},
    ],
    "unresolved": ["Actual cost unknown"],
    "proposed_variables": [],
    "next_action": "Researcher reviews",
}
FRAME = {"sources": ["DD-2023"], "gap_type": "fact", "focus": "Check cost definition"}

USAGE = {"input_tokens": 200, "output_tokens": 100}


def _transport(handler):
    return MetaProvider(httpx.MockTransport(handler), key_reader=lambda: "test-not-real-key")


def _models_ok(request):
    if request.url.path.endswith("/models"):
        endpoints = [m for m in registry.MODELS.values() if m.endpoint]
        return httpx.Response(200, json={"data": [{"id": m.id} for m in endpoints]})
    return None


def _wire(h, monkeypatch, handler):
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")
    svc = service.AIService(h, _transport(handler))
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc, monkeypatch)
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    return svc, qid


def _ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def _usage_row(svc, jid):
    row = svc.db.execute(
        "SELECT accounting_json FROM ai_usage WHERE job_id=?", (jid,)
    ).fetchone()
    assert row is not None, "expected a persisted usage row"
    return json.loads(row[0])


def _job_row(svc, jid):
    row = svc.db.execute(
        "SELECT status, result_json FROM ai_jobs WHERE id=?", (jid,)
    ).fetchone()
    assert row is not None
    return row[0], json.loads(row[1]) if row[1] else None


def test_incomplete_status_records_shape_and_drops_text(h, monkeypatch):
    calls = []

    def handler(request):
        hit = _models_ok(request)
        if hit is not None:
            return hit
        calls.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "model": body["model"],
                "output": [{"type": "message", "content": [{"type": "output_text", "text": SECRET}]}],
                "usage": dict(USAGE),
            },
        )

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    prep = svc.prepare(qid, {})
    with pytest.raises(Conflict, match="response_not_completed"):
        asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))

    status, _ = _job_row(svc, prep["job_id"])
    assert status == "failed_unknown_charge"
    assert len(calls) == 1, "gate failure must stop after exactly one provider call"

    item = _usage_row(svc, prep["job_id"])
    assert item["response_keys"] == ["model", "output", "status", "usage"]
    assert item["response_status"] == "incomplete"
    assert item["response_output_count"] == 1
    assert item["response_output_types"] == ["message"]

    stored = svc.db.execute(
        "SELECT accounting_json FROM ai_usage WHERE job_id=?", (prep["job_id"],)
    ).fetchone()[0]
    _, result = _job_row(svc, prep["job_id"])
    receipt = svc.receipt(prep["job_id"])
    for blob in (stored, json.dumps(result), json.dumps(receipt, allow_nan=False)):
        assert SECRET not in blob


def test_completed_status_unchanged(h, monkeypatch):
    def handler(request):
        hit = _models_ok(request)
        if hit is not None:
            return hit
        body = json.loads(request.content)
        text = json.dumps(FRAME if "public work plan" in body.get("instructions", "") else RESULT)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": body["model"],
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": dict(USAGE),
            },
        )

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    prep = svc.prepare(qid, {})
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert result["usage_complete"]
    status, _ = _job_row(svc, prep["job_id"])
    # Complete usage settles to completed_known in ai_jobs.status (service.py:684); the receipt outcome stays "completed".
    assert status == "completed_known"


def test_missing_status_still_passes_gate(h, monkeypatch):
    def handler(request):
        hit = _models_ok(request)
        if hit is not None:
            return hit
        body = json.loads(request.content)
        text = json.dumps(FRAME if "public work plan" in body.get("instructions", "") else RESULT)
        envelope = {
            "model": body["model"],
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": dict(USAGE),
        }
        assert "status" not in envelope
        return httpx.Response(200, json=envelope)

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    prep = svc.prepare(qid, {})
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert result["usage_complete"]
    status, _ = _job_row(svc, prep["job_id"])
    # A missing envelope status still passes the gate and settles as completed_known (service.py:684).
    assert status == "completed_known"


def test_wrong_model_with_incomplete_status_raises_identity_and_records_shape(h, monkeypatch):
    calls = []

    def handler(request):
        hit = _models_ok(request)
        if hit is not None:
            return hit
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "model": "bogus-model-id",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": SECRET}]}],
                "usage": dict(USAGE),
            },
        )

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    prep = svc.prepare(qid, {})
    with pytest.raises(Conflict, match="unexpected_model_identity"):
        asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))

    assert len(calls) == 1
    item = _usage_row(svc, prep["job_id"])
    assert item["response_keys"] == ["model", "output", "status", "usage"]
    assert item["response_status"] == "incomplete"
    assert item["response_output_count"] == 1
    assert item["response_output_types"] == ["message"]

    stored = svc.db.execute(
        "SELECT accounting_json FROM ai_usage WHERE job_id=?", (prep["job_id"],)
    ).fetchone()[0]
    assert SECRET not in stored
