"""Stored execution_order honesty: prepare record == executed arms == receipt.

Cold-review finding: ``AIService.prepare()`` stamped EVERY stored record with
a global-parity pair (``['basic','harness']`` / ``['harness','basic']``) even
for single-arm jobs, while ``execute()`` derives single-arm lists from the
verified flags. The stored request record therefore claimed an order that was
never executed. The receipt reads ``outputs.evaluation`` (actual arms), so it
matched the result but not the stored request.

Contract pinned here: for every shape, the stored ``request_json``
``execution_order`` (set at prepare time, before hashing/storage — never
rewritten) must equal the executed evaluation order, and the served receipt
order must equal both. Genuine multi-arm compare jobs keep the alternating
pair (counterbalanced across prior compare jobs).

Mock transports only; no paid/live calls, no key reads.
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app.ai import registry, service  # noqa: E402
from app.ai.provider import MetaProvider  # noqa: E402
from app.api import create_app  # noqa: E402
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

FRAME = {"sources": ["DD-2023"], "gap_type": "fact", "focus": "Check cost definition"}
RESULT = {
    "answer": "Reported estimate, not actual cost.",
    "claims": [
        {"text": "Applicant estimate", "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"},
    ],
    "unresolved": ["Actual cost unknown"],
    "proposed_variables": [{"name": "est_cost", "dataset_id": "fx/permits_2023.csv",
                            "kind": "actual"}],
    "next_action": "Researcher reviews",
}


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


@pytest.fixture
def wired(h, monkeypatch):
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")
    svc = service.AIService(h, MetaProvider(httpx.MockTransport(_mock_ok), key_reader=lambda: "test"),
                            meta_available=True)
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc, monkeypatch)
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    return svc, qid


def _ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def _record(svc, jid):
    return json.loads(svc.db.execute(
        "SELECT request_json FROM ai_jobs WHERE id=?", (jid,)).fetchone()[0])


def _receipt(wired, qid, jid):
    svc, _ = wired
    path = svc.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, svc.h.data)) as client:
        res = client.get(f"/questions/{qid}/ai/jobs/{jid}/receipt")
    assert res.status_code == 200, res.text
    return res.json()


def test_harness_only_stored_order_matches_execution_and_receipt(wired):
    svc, qid = wired
    _ready(svc)
    prep = svc.prepare(qid, {"task": "research_query", "harness": True})
    assert prep["max_inference_calls"] == 2
    stored = _record(svc, prep["job_id"])["execution_order"]
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    executed = result["outputs"]["evaluation"]["execution_order"]
    assert set(result["outputs"]) == {"harness", "evaluation"}
    assert stored == ["harness"], f"prepare record fiction: {stored}"
    assert executed == ["harness"]
    assert stored == executed
    assert _receipt(wired, qid, prep["job_id"])["execution_order"] == stored


def test_basic_only_stored_order_matches_execution_and_receipt(wired):
    svc, qid = wired
    _ready(svc)
    prep = svc.prepare(qid, {"task": "research_query", "harness": False})
    assert prep["max_inference_calls"] == 1
    stored = _record(svc, prep["job_id"])["execution_order"]
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    executed = result["outputs"]["evaluation"]["execution_order"]
    assert set(result["outputs"]) == {"basic", "evaluation"}
    assert stored == ["basic"], f"prepare record fiction: {stored}"
    assert executed == ["basic"]
    assert stored == executed
    assert _receipt(wired, qid, prep["job_id"])["execution_order"] == stored


def test_compare_keeps_alternating_pair_across_jobs(wired):
    svc, qid = wired
    _ready(svc)
    first = svc.prepare(qid, {"task": "research_query", "compare": True})
    second = svc.prepare(qid, {"task": "research_query", "compare": True})
    order_a = _record(svc, first["job_id"])["execution_order"]
    order_b = _record(svc, second["job_id"])["execution_order"]
    assert sorted(order_a) == ["basic", "harness"]
    assert sorted(order_b) == ["basic", "harness"]
    # Legitimate alternation semantics for genuine multi-arm compare jobs.
    assert order_b == list(reversed(order_a))
    result = asyncio.run(svc.execute(first["job_id"], first["preview_hash"], True))
    executed = result["outputs"]["evaluation"]["execution_order"]
    assert result["outputs"]["evaluation"]["kind"] == "paired"
    assert set(result["outputs"]) == {"basic", "harness", "evaluation"}
    assert executed == order_a
    assert _receipt(wired, qid, first["job_id"])["execution_order"] == order_a


@pytest.mark.parametrize("task", ["methodology_review", "cross_artifact_review"])
def test_linked_wonder_forced_basic_stored_order_matches(wired, task):
    svc, qid = wired
    _ready(svc)
    prior = svc.prepare(qid, {"task": "research_query", "harness": False})
    asyncio.run(svc.execute(prior["job_id"], prior["preview_hash"], True))
    prep = svc.prepare(qid, {"task": task, "harness": True, "compare": True,
                             "max_cost_usd": 50, "second_look_of": prior["job_id"]})
    assert prep["max_inference_calls"] == 1
    stored = _record(svc, prep["job_id"])["execution_order"]
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    executed = result["outputs"]["evaluation"]["execution_order"]
    assert [st["stage"] for st in result["stages"]] == ["basic"]
    assert stored == ["basic"], f"prepare record fiction: {stored}"
    assert executed == ["basic"]
    assert stored == executed
    assert _receipt(wired, qid, prep["job_id"])["execution_order"] == stored
