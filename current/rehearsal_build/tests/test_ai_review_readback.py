"""AI review readback: stored ai_reviews rows are readable, exactly as stored.

GET /questions/{qid}/ai/jobs/{jid}/review returns the stored report payload(s)
for that exact job: stored content plus stored ids, billed None/NOT_VERIFIED
(following receipt conventions). The read path never calls a provider and
never writes. Unknown job, cross-question job, or a job with no stored
review -> honest 404 (never 409, never a neighbouring review, never a
fabricated empty report).

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

SECRET = "sk-ant-REVIEW-READBACK-PROBE-9d41"

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


def _review_body(reviewer):
    return {
        "reviewer": reviewer,
        "gold_case_id": "FX-APPROVAL-1",
        "rubric_version": "rw-research-1",
        "arms": {
            "harness": {
                "claim_verdicts": ["supported"],
                "citation_verdicts": ["supports"],
                "variable_method_correct": None,
                "task_success": None,
            }
        },
    }


async def _boom(*args, **kwargs):
    raise AssertionError("review readback must not call the provider")


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


def _seed_reviewed_job(svc, qid, reviewer="r-chen"):
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    stored = svc.review(prep["job_id"], _review_body(reviewer))
    return prep["job_id"], stored


def _dump(svc):
    return "\n".join(svc.db.iterdump())


def test_review_readback_returns_stored_report_exactly(wired, monkeypatch):
    svc, qid, _ = wired
    _ready(svc)
    jid, stored = _seed_reviewed_job(svc, qid)
    # From here on any provider touch fails loudly, including inside the
    # TestClient app (which builds its own AIService around MetaProvider).
    monkeypatch.setattr(MetaProvider, "request", _boom)
    monkeypatch.setattr(MetaProvider, "infer", _boom)
    monkeypatch.setattr(MetaProvider, "catalog", _boom)
    with _client(wired) as client:
        assert client.get(f"/questions/{qid}/ai/jobs/no-such-job/review").status_code == 404
    before = _dump(svc)
    reviews_before = svc.db.execute("SELECT COUNT(*) FROM ai_reviews").fetchone()[0]
    with _client(wired) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{jid}/review")
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body["reviews"], list) and len(body["reviews"]) == 1
    entry = body["reviews"][0]
    assert entry["review_id"]
    assert entry["job_id"] == jid
    assert entry["question_id"] == qid
    assert entry["report"] == stored
    assert body["provider_billed_cost_usd"] is None
    assert body["billing_status"] == "NOT_VERIFIED"
    assert SECRET not in json.dumps(body)
    # Zero writes on the read path: logical DB content and review count fixed.
    assert _dump(svc) == before
    assert svc.db.execute("SELECT COUNT(*) FROM ai_reviews").fetchone()[0] == reviews_before
    # Stored bytes are the source of truth, not a re-evaluation.
    row = svc.db.execute("SELECT report_json FROM ai_reviews WHERE job_id=?", (jid,)).fetchone()
    assert json.loads(row[0]) == stored


def test_review_readback_cross_question_is_404(wired):
    svc, qid, pid = wired
    _ready(svc)
    jid, _ = _seed_reviewed_job(svc, qid)
    other = asyncio.run(svc.h.create_question(pid, "Can I study hiring trends?"))["question_id"]
    with _client(wired) as client:
        leaked = client.get(f"/questions/{other}/ai/jobs/{jid}/review")
        assert leaked.status_code == 404
        assert leaked.json()["error"] == "not_found"


def test_review_readback_unknown_job_is_404(wired):
    svc, qid, _ = wired
    with _client(wired) as client:
        missing = client.get(f"/questions/{qid}/ai/jobs/ai-no-such-job/review")
        assert missing.status_code == 404
        assert missing.json()["error"] == "not_found"
        unknown_question = client.get("/questions/q-no-such-question/ai/jobs/ai-no-job/review")
        assert unknown_question.status_code == 404
        assert unknown_question.json()["error"] == "not_found"


def test_review_readback_no_stored_review_is_404(wired):
    svc, qid, _ = wired
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    with _client(wired) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{prep['job_id']}/review")
        assert res.status_code == 404
        assert res.json()["error"] == "not_found"


def test_review_readback_multiple_reviews_list_in_stored_order(wired, monkeypatch):
    svc, qid, _ = wired
    _ready(svc)
    prep = svc.prepare(qid, {})
    asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    first = svc.review(prep["job_id"], _review_body("r-first"))
    second = svc.review(prep["job_id"], _review_body("r-second"))
    monkeypatch.setattr(MetaProvider, "request", _boom)
    monkeypatch.setattr(MetaProvider, "infer", _boom)
    monkeypatch.setattr(MetaProvider, "catalog", _boom)
    before = _dump(svc)
    with _client(wired) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{prep['job_id']}/review")
    assert res.status_code == 200, res.text
    reviews = res.json()["reviews"]
    assert isinstance(reviews, list) and len(reviews) == 2
    assert reviews[0]["report"] == first
    assert reviews[1]["report"] == second
    assert reviews[0]["report"]["reviewer"] == "r-first"
    assert reviews[1]["report"]["reviewer"] == "r-second"
    assert _dump(svc) == before
