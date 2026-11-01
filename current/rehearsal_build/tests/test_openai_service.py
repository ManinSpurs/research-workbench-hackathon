"""Synthetic-only OpenAI route regression; injected transport, no sockets or keys."""
import asyncio
import json

import httpx
import pytest

from app.ai import service, secrets
from app.ai.provider import OpenAIProvider
from app.ai.registry import OPENAI_MODEL_ID
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401
from test_modal_shared_route import RESULT


def wire(h, monkeypatch):
    for name in ("key", "baseten_key", "modal_shared_key"):
        monkeypatch.setattr(secrets, name, lambda: None)
    monkeypatch.setattr(secrets, "openai_key", lambda: "synthetic-openai-key")
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": OPENAI_MODEL_ID}]})
        body = json.loads(request.content)
        assert body["store"] is False and body["tools"] == []
        return httpx.Response(200, json={
            "model": OPENAI_MODEL_ID, "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(RESULT)}]}],
            "usage": {"input_tokens": 200, "output_tokens": 100,
                      "input_tokens_details": {"cached_tokens": 50}},
        })

    provider = OpenAIProvider(httpx.MockTransport(handler), key_reader=lambda: "synthetic-openai-key")
    svc = service.AIService(h, provider_factory={"meta": lambda: None, "openai": lambda: provider})
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    svc.configure(True, 150)
    return svc, qid, seen


def prepare(svc, qid, **changes):
    body = dict(model=OPENAI_MODEL_ID, provider="openai", harness=False,
                public_test_data=True, max_cost_usd=1.0)
    return svc.prepare(qid, {**body, **changes})


def test_verification_attestation_and_provider_ownership(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    with pytest.raises(Conflict, match="Verify account"):
        prepare(svc, qid)
    asyncio.run(svc.verify_account("openai"))
    with pytest.raises(Conflict, match="public or synthetic"):
        prepare(svc, qid, public_test_data=False)
    with pytest.raises(Conflict, match="does not belong"):
        prepare(svc, qid, provider="meta")
    assert len(seen) == 1
    preview = prepare(svc, qid)
    assert preview["provider"] == "openai"
    assert "api.openai.com" in json.dumps(preview)
    assert preview["maximum_reserved_usd"] == pytest.approx(.309216)


def test_cap_persists_after_settlement_and_question_deletion(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    asyncio.run(svc.verify_account("openai"))
    for _ in range(3):
        preview = prepare(svc, qid)
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
        receipt = svc.receipt(preview["job_id"])
        assert receipt["model"] == OPENAI_MODEL_ID
        assert receipt["request_summary"]["model"] == OPENAI_MODEL_ID
    assert svc._openai_test_total() == pytest.approx(3 * .309216)
    with pytest.raises(Conflict, match="limit is"):
        prepare(svc, qid)
    h.delete_question(qid)
    assert svc._openai_test_total() == pytest.approx(3 * .309216)
    assert len([r for r in seen if r.method == "POST"]) == 3


def test_prepared_jobs_cannot_bypass_total_cap(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    asyncio.run(svc.verify_account("openai"))
    previews = [prepare(svc, qid) for _ in range(4)]
    for preview in previews[:3]:
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    with pytest.raises(Conflict, match="limit is"):
        asyncio.run(svc.execute(previews[3]["job_id"], previews[3]["preview_hash"], True))
    assert len([r for r in seen if r.method == "POST"]) == 3
