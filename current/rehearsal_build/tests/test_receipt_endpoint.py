"""Trust Receipt HTTP route: strict-JSON server receipts over the real TestClient layer.

Completed mock job -> GET /questions/{qid}/ai/jobs/{jid}/receipt returns 200
with billed None/NOT_VERIFIED, registered IDs only and strict JSON.
Missing job or cross-question job -> 404 (same envelope as neighboring routes).
Failed-validation mock job -> count-only receipt; planted secret absent.

Mock transports only; no paid calls, no key reads.
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
from fastapi.testclient import TestClient

from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.api import create_app
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

SECRET = "sk-ant-RECEIPT-ENDPOINT-PROBE-7c29"

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


def _mock_ok(request):
    if request.url.path.endswith("/models"):
        endpoints = [m for m in registry.MODELS.values() if m.endpoint]
        return httpx.Response(200, json={"data": [{"id": m.id} for m in endpoints]})
    body = json.loads(request.content)
    text = json.dumps(FRAME if "public work plan" in body.get("instructions", "") else RESULT)
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "model": body["model"],
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    )


def _mock_rejected(request):
    if request.url.path.endswith("/models"):
        endpoints = [m for m in registry.MODELS.values() if m.endpoint]
        return httpx.Response(200, json={"data": [{"id": m.id} for m in endpoints]})
    body = json.loads(request.content)
    if "public work plan" in body.get("instructions", ""):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": body["model"],
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(FRAME)}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
    bad = {"answer": SECRET, "claims": [],
             "unresolved": [], "proposed_variables": [], "next_action": "x",
             "extra_undeclared_field": SECRET}
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "model": body["model"],
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(bad)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


@pytest.fixture
def wired(h, monkeypatch):
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")
    svc = service.AIService(h, MetaProvider(httpx.MockTransport(_mock_ok), key_reader=lambda: "test"))
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc, monkeypatch)
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    return svc, qid, pid


def _client(wired):
    svc, _, _ = wired
    path = svc.db.execute("PRAGMA database_list").fetchone()["file"]
    return TestClient(create_app(path, svc.h.data))


def _ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def test_receipt_endpoint_completed_job(wired):
    svc, qid, _ = wired
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    with _client(wired) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{prep['job_id']}/receipt")
    assert res.status_code == 200, res.text
    receipt = res.json()
    assert receipt["receipt_version"] == "rw-trust-receipt-1"
    assert receipt["outcome"] == "completed"
    assert receipt["job_status"] == "completed_known"
    # Untouched completed job: the completion digest binds the settled spend
    # reservation, so integrity verification must pass end to end.
    assert receipt["result_integrity_verified"] is True
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert "harness" in receipt["arms"]
    summary = receipt["arm_summaries"]["harness"]
    # Registered evidence IDs pass only by exact packet.allowed_sources
    # membership; the production gate drops shape-valid but unallowed IDs.
    allowed = json.loads(svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?", (prep["job_id"],)).fetchone()[0])
    allowed = (allowed.get("snapshot") or {}).get("packet", {}).get("allowed_sources", [])
    assert "DD-2023:est_cost" not in allowed
    assert summary["claim_count"] == 1 and summary["evidence_ids"] == []
    assert receipt["observed_source_ids"] == []
    assert receipt["stages"], "stages must carry per-stage cost_basis"
    assert all("cost_basis" in stage for stage in receipt["stages"])
    body = json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert "Reported estimate" not in body
    # Jobs list keeps serving; receipt fetch is read-only. The recorded job
    # status is the settled spend status (service.py persists
    # completed_known/completed_unknown_charge verbatim), so a fully-costed
    # success reads completed_known here.
    with _client(wired) as client:
        jobs = client.get(f"/questions/{qid}/ai/jobs").json()["jobs"]
    assert jobs[0]["status"] == "completed_known"


def test_receipt_endpoint_missing_job_is_404(wired):
    svc, qid, _ = wired
    with _client(wired) as client:
        missing = client.get(f"/questions/{qid}/ai/jobs/ai-no-such-job/receipt")
        assert missing.status_code == 404
        assert missing.json()["error"] == "not_found"
        unknown_question = client.get("/questions/q-no-such-question/ai/jobs/ai-no-job/receipt")
        assert unknown_question.status_code == 404
        assert unknown_question.json()["error"] == "not_found"


def test_receipt_endpoint_scoped_to_question(wired):
    svc, qid, pid = wired
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    other = asyncio.run(svc.h.create_question(pid, "Can I study hiring trends?"))["question_id"]
    with _client(wired) as client:
        leaked = client.get(f"/questions/{other}/ai/jobs/{prep['job_id']}/receipt")
        assert leaked.status_code == 404
        assert leaked.json()["error"] == "not_found"


def test_receipt_endpoint_failed_validation_counts_only(wired, monkeypatch):
    svc, qid, _ = wired
    _ready(svc)
    svc.provider = MetaProvider(httpx.MockTransport(_mock_rejected), key_reader=lambda: "test")
    prep = svc.prepare(qid, {})
    with pytest.raises(Exception, match="typed_output_validation_failed|no automatic retry"):
        asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    with _client(wired) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{prep['job_id']}/receipt")
    assert res.status_code == 200, res.text
    receipt = res.json()
    assert receipt["outcome"] == "failed"
    assert receipt["validation_issue_count"] is not None and receipt["validation_issue_count"] > 0
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["arm_summaries"] == {}
    body = json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert SECRET not in body