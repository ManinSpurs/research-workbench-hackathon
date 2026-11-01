"""Baseten route: origin, credential isolation, settlement, fail-closed schema.

Every provider call is answered by an injected ``httpx.MockTransport``: no
socket is opened, no real credential is read, and the missing-key case proves
the transport is never reached at all.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import registry, secrets, service
from app.ai.provider import BasetenProvider, MetaProvider
from app.api import create_app
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (re-exported as this module's fixture)

META_KEY = "meta-not-real-key"
BASETEN_KEY = "baseten-not-real-key"

RESULT = {
    "answer": "Reported estimate, not actual cost.",
    "claims": [{"text": "Applicant estimate",
                "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}],
    "unresolved": ["Actual cost unknown"],
    "proposed_variables": [{"name": "est_cost",
                            "dataset_id": "fx/permits_2023.csv",
                            "kind": "actual"}],
    "next_action": "Researcher reviews",
}
FRAME = {"sources": ["DD-2023"], "gap_type": "fact",
         "focus": "Check cost definition"}
USAGE = {"input_tokens": 200, "output_tokens": 100}
# (200 in * $1.25 + 100 out * $4.25) / 1e6 — the verified Baseten rates.
ONE_CALL_USD = (200 * 1.25 + 100 * 4.25) / 1e6


def _responses(text, model=registry.BASETEN_MODEL_ID):
    return {"status": "completed", "model": model,
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": USAGE}


def _payload(req):
    """Prompt text of a Responses-API request, per app.ai.service._body."""
    body = json.loads(req.content)
    return body["input"][0]["content"][0]["text"]


def wire(h, monkeypatch, responder=None):
    """A service wired for both routes plus the recorded Baseten requests."""
    monkeypatch.setattr(service.secrets, "key", lambda: META_KEY)
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: BASETEN_KEY)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": registry.BASETEN_MODEL_ID}]})
        text = responder() if responder else (
            json.dumps(FRAME) if "public work plan" in json.loads(req.content).get("instructions", "")
            else json.dumps(RESULT))
        return httpx.Response(200, json=_responses(text))

    def meta_handler(req):
        seen.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": x} for x, m in registry.MODELS.items() if m.endpoint]})
        # Any inference call on the Meta origin during a Baseten test is a
        # routing defect, so it fails loudly instead of answering.
        return httpx.Response(500, json={"never": "reached on a Baseten job"})

    svc = service.AIService(
        h,
        MetaProvider(httpx.MockTransport(meta_handler), key_reader=lambda: META_KEY),
        provider_factory={
            "meta": lambda: svc.provider,
            "baseten": lambda: BasetenProvider(httpx.MockTransport(handler),
                                              key_reader=lambda: BASETEN_KEY),
        })
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(
        pid, "What does est_cost measure?"))["question_id"]
    return svc, qid, seen


def _ready(svc, provider="baseten"):
    svc.configure(True, 150)
    return asyncio.run(svc.verify_account(provider))


def _stub_meta(transport=None, key_reader=None):
    def handler(req):
        return httpx.Response(200, json={
            "data": [{"id": x} for x, m in registry.MODELS.items() if m.endpoint]})
    return MetaProvider(httpx.MockTransport(handler), key_reader=lambda: META_KEY)


def _stub_baseten(transport=None, key_reader=None):
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": registry.BASETEN_MODEL_ID}]})
    return BasetenProvider(httpx.MockTransport(handler),
                           key_reader=lambda: BASETEN_KEY)


def _prepare(svc, qid, **extra):
    body = {"provider": "baseten", "model": registry.BASETEN_MODEL_ID,
            "harness": False}
    body.update(extra)
    return svc.prepare(qid, body)


def test_baseten_preview_names_the_origin_it_will_use(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    preview = _prepare(svc, qid)
    assert preview["provider"] == "baseten"
    assert preview["transmission"]["destination"] == registry.BASETEN_BASE_URL
    assert preview["transmission"]["destination"] == "https://inference.baseten.co/v1"
    assert preview["requires_explicit_consent"] is True
    assert preview["account_ready"] is True
    # A preview transmits nothing beyond the account check already recorded.
    assert len(seen) == before


def test_auto_research_uses_baseten_and_wonder_is_blocked(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = svc.prepare(qid, {"model": "auto", "compare": True})
    assert preview["provider"] == "baseten"
    assert preview["model"] == registry.BASETEN_MODEL_ID
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert len([r for r in seen if r.url.path.endswith("/responses")]) == 3
    with pytest.raises(Conflict, match="wonder_blocked_meta_credits_exhausted"):
        svc.prepare(qid, {"task": "methodology_review"})


@pytest.mark.parametrize("model", ["muse-spark-1.3", "muse-spark-1.3-contributor"])
def test_meta_spark_refused_at_prepare_and_legacy_execute(h, monkeypatch, model):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    with pytest.raises(Conflict, match="meta_spark_blocked_credits_exhausted"):
        svc.prepare(qid, {"model": model})
    # Model a job persisted before the availability rule existed.
    with monkeypatch.context() as legacy:
        legacy.setattr(svc, "_assert_available", lambda *args: None)
        preview = svc.prepare(qid, {"model": model})
    with pytest.raises(Conflict, match="meta_spark_blocked_credits_exhausted"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert len(seen) == before


@pytest.mark.parametrize("task", service.SECOND_LOOK_TASKS)
def test_second_look_cannot_escape_meta_gate_with_baseten_override(h, monkeypatch, task):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    with pytest.raises(Conflict, match="wonder_blocked_meta_credits_exhausted"):
        svc.prepare(qid, {"task": task, "model": registry.BASETEN_MODEL_ID})
    svc.meta_available = True
    with pytest.raises(Conflict, match="second_look_model_override_forbidden"):
        svc.prepare(qid, {"task": task, "model": registry.BASETEN_MODEL_ID})
    with monkeypatch.context() as legacy:
        legacy.setattr(svc, "_assert_available", lambda *args: None)
        preview = svc.prepare(qid, {"task": task, "model": registry.BASETEN_MODEL_ID})
    svc.meta_available = False
    with pytest.raises(Conflict, match="wonder_blocked_meta_credits_exhausted"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    svc.meta_available = True
    with pytest.raises(Conflict, match="second_look_model_override_forbidden"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert len(seen) == before


def test_paid_comparison_stops_after_costed_call_before_next_stage(h, monkeypatch):
    from app.ai.provider import ProviderError
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)

    def stop_after_costed_call(stage):
        assert stage["cost_usd"] == pytest.approx(ONE_CALL_USD)
        raise ProviderError("comparison_cost_stop")

    svc.after_call = stop_after_costed_call
    preview = svc.prepare(qid, {"compare": True})
    with pytest.raises(Conflict, match="comparison_cost_stop"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert len([r for r in seen if r.url.path.endswith("/responses")]) == 1
    assert svc.jobs(qid)[0]["result"]["stages"][0]["cost_usd"] == pytest.approx(ONE_CALL_USD)


def test_baseten_single_call_settles_from_returned_usage(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    result = asyncio.run(svc.execute(
        preview["job_id"], preview["preview_hash"], True))
    inference = [r for r in seen if r.url.path.endswith("/responses")]
    assert len(inference) == 1
    assert result["usage_complete"] is True
    assert result["calculated_cost_usd"] == pytest.approx(ONE_CALL_USD, rel=1e-9)
    assert result["provider_billed_cost_usd"] is None
    assert result["billing_status"] == "NOT_VERIFIED"
    spend = svc.db.execute(
        "SELECT status,actual_cost FROM ai_spend WHERE job_id=?",
        (preview["job_id"],)).fetchone()
    assert spend["status"] == "completed_known"
    assert spend["actual_cost"] == pytest.approx(ONE_CALL_USD, rel=1e-9)


def test_baseten_low_effort_and_output_budget_are_preview_bound(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid, compare=True)
    assert preview["reasoning"] == {"effort": "low"}
    assert preview["max_output_tokens"] == 2048
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    for req in seen:
        if req.url.path.endswith("/responses"):
            body = json.loads(req.content)
            assert body["reasoning"] == {"effort": "low"}
            assert body["max_output_tokens"] == 2048
    assert svc.receipt(preview["job_id"])["request_summary"]["reasoning_effort"] == "low"


def test_duplicate_frame_sources_are_declared_and_still_rejected(h, monkeypatch):
    frame = {**FRAME, "sources": ["DD-2023", "DD-2023"]}
    svc, qid, seen = wire(h, monkeypatch, responder=lambda: json.dumps(frame))
    _ready(svc)
    preview = _prepare(svc, qid, harness=True)
    with pytest.raises(Conflict, match="typed_output_validation_failed"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    request = next(r for r in seen if r.url.path.endswith("/responses"))
    body = json.loads(request.content)
    assert body["text"]["format"]["schema"]["properties"]["sources"]["uniqueItems"] is True
    assert "distinct source_id" in body["instructions"]
    assert len([r for r in seen if r.url.path.endswith("/responses")]) == 1


def test_baseten_requests_carry_only_the_baseten_credential(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert seen, "no request was recorded"
    for req in seen:
        assert req.headers["authorization"] == "Bearer " + BASETEN_KEY
        assert "meta-not-real-key" not in req.headers["authorization"]
    inference = [r for r in seen if r.url.path.endswith("/responses")]
    assert [str(r.url) for r in inference] == [
        "https://inference.baseten.co/v1/responses"]
    body = json.loads(inference[0].content)
    assert body["model"] == registry.BASETEN_MODEL_ID
    assert body["store"] is False
    assert body["tools"] == []
    assert body["text"]["format"]["strict"] is True


def test_schema_violation_fails_closed_before_storage(h, monkeypatch):
    # Baseten documents json_schema as accepted but not enforced for this
    # model, so a conforming-looking but invalid body must never be stored as
    # a result or settled as a known cost.
    invalid = json.dumps({**RESULT, "unexpected_field": "not in the contract"})
    svc, qid, seen = wire(h, monkeypatch, responder=lambda: invalid)
    _ready(svc)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="typed_output_validation_failed"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    row = svc.db.execute("SELECT status,result_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    # Usage came back, so the charge is known even though the body was not
    # accepted; the invariant is that no result and no settled success exist.
    assert row["status"] == "failed_known_charge"
    stored = json.loads(row["result_json"])
    assert stored["calculated_cost_usd"] is None
    assert "outputs" not in stored
    assert "not in the contract" not in row["result_json"]
    spend = svc.db.execute("SELECT status FROM ai_spend WHERE job_id=?",
                           (preview["job_id"],)).fetchone()
    assert spend["status"] == "failed_known_charge"


def test_missing_baseten_key_sends_nothing(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: None)
    svc.configure(True, 150)
    before = len(seen)
    with pytest.raises(Conflict, match="key_missing"):
        _prepare(svc, qid)
    assert len(seen) == before


def test_unverified_account_refuses_to_prepare(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="Verify account model access"):
        _prepare(svc, qid)
    assert seen == []  # nothing was even attempted: no verified account


def test_default_route_uses_pinned_baseten(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc, "baseten")
    preview = svc.prepare(qid, {"harness": False})
    assert preview["provider"] == "baseten"
    assert preview["transmission"]["destination"] == registry.BASETEN_BASE_URL
    record = json.loads(svc.db.execute(
        "SELECT request_json FROM ai_jobs WHERE id=?",
        (preview["job_id"],)).fetchone()["request_json"])
    assert record["provider"] == "baseten"
    assert record["model"] == registry.TASKS["research_query"]


def test_baseten_route_rejects_another_provider_model(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    _ready(svc)
    with pytest.raises(Conflict, match="does not belong to this provider"):
        _prepare(svc, qid, model=registry.OPENAI_MODEL_ID)


def test_unknown_provider_name_fails_closed(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    with pytest.raises(Conflict, match="Unknown provider"):
        _prepare(svc, qid, provider="not-a-provider")
    assert len(seen) == before


def test_baseten_slug_routes_without_an_explicit_provider(h, monkeypatch):
    # The UI sends the chosen model id and no provider field. A Baseten slug
    # must still route to Baseten: defaulting to Meta would send research
    # material to the wrong origin.
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = svc.prepare(qid, {"model": registry.BASETEN_MODEL_ID,
                                "harness": False})
    assert preview["provider"] == "baseten"
    assert preview["transmission"]["destination"] == registry.BASETEN_BASE_URL
    record = json.loads(svc.db.execute(
        "SELECT request_json FROM ai_jobs WHERE id=?",
        (preview["job_id"],)).fetchone()["request_json"])
    # The stored request names the resolved route, not a blank caller value.
    assert record["provider"] == "baseten"


def test_meta_provider_refuses_the_baseten_slug(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    with pytest.raises(Conflict, match="Baseten-hosted"):
        _prepare(svc, qid, provider="meta")


def test_verifying_baseten_does_not_verify_meta(h, monkeypatch):
    # Two independent accounts: a Baseten check must never read as Meta access.
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc, "baseten")
    settings = svc.settings()
    assert settings["provider_catalogs"]["baseten"]["catalog"] == [
        registry.BASETEN_MODEL_ID]
    assert settings["provider_catalogs"]["baseten"][
        "catalog_valid_for_current_key"] is True
    assert settings["provider_catalogs"]["meta"]["catalog"] == []
    assert settings["provider_catalogs"]["meta"][
        "catalog_valid_for_current_key"] is False
    assert settings["catalog"] == []          # legacy field stays Meta's
    assert settings["baseten_key_configured"] is True


def test_meta_verification_does_not_authorize_baseten(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc, "meta")
    with pytest.raises(Conflict, match="Verify account model access"):
        _prepare(svc, qid)
    assert not [r for r in seen if r.url.path.endswith("/responses")]


def test_key_containers_are_per_provider_and_write_only(h, monkeypatch, tmp_path):
    # Each provider writes its own fixed file; nothing is ever echoed back.
    monkeypatch.setattr(service, "MetaProvider", _stub_meta)
    monkeypatch.setattr(service, "BasetenProvider", _stub_baseten)
    home = tmp_path / "home"
    home.mkdir()
    meta_file = home / "Application Support" / "ResearchWorkbench" / "meta_api_key"
    baseten_file = home / "Application Support" / "ResearchWorkbench" / "baseten_api_key"
    monkeypatch.setattr(secrets, "secret_path", lambda: meta_file)
    monkeypatch.setattr(secrets, "baseten_secret_path", lambda: baseten_file)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "PROVIDER_SECRETS", {
        "meta": (lambda: meta_file, ("META_API_KEY", "MODEL_API_KEY")),
        "baseten": (lambda: baseten_file, ("BASETEN_API_KEY",))})
    svc, qid, _ = wire(h, monkeypatch)
    # Use the real readers so the saved files are what the app would present.
    monkeypatch.setattr(secrets, "key", lambda: (
        os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
        or secrets._owner_only(meta_file)))
    monkeypatch.setattr(secrets, "baseten_key", lambda: (
        os.environ.get("BASETEN_API_KEY") or secrets._owner_only(baseten_file)))
    svc.configure(True, 150)
    with TestClient(create_app(
            svc.db.execute("PRAGMA database_list").fetchone()["file"],
            svc.h.data)) as client:
        res = client.post("/ai/keys", json={"provider": "baseten", "key": "b10_test_key"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["saved"] is True and body["key_present"] is True
        # The response must never carry the key back.
        assert "b10_test_key" not in res.text
        assert client.post("/ai/keys", json={"provider": "meta",
                                            "key": "sk-test-key"}).status_code == 200
        assert client.post("/ai/keys", json={"provider": "local",
                                            "key": "x"}).status_code == 409
        assert client.post("/ai/keys", json={"provider": "baseten",
                                            "key": "has space"}).status_code == 409
        assert client.post("/ai/keys", json={"provider": "baseten",
                                            "key": ""}).status_code == 409
    # Two separate files, each owner-only, each holding only its own key.
    assert baseten_file.read_text() == "b10_test_key"
    assert meta_file.read_text() == "sk-test-key"
    for path in (meta_file, baseten_file):
        assert path.stat().st_mode & 0o777 == 0o600
    # A stored key is what the provider actually presents, and it is never
    # read back out through settings() or the registry snapshot.
    assert secrets.baseten_key() == "b10_test_key"
    assert secrets.key() == "sk-test-key"
    settings = svc.settings()
    assert settings["baseten_key_configured"] is True
    assert settings["key_configured"] is True
    assert "b10_test_key" not in json.dumps(settings)
    assert "sk-test-key" not in json.dumps(registry.registry())


def test_key_save_rejects_a_path_traversal_provider(h, monkeypatch, tmp_path):
    served = []
    monkeypatch.setattr(secrets, "save",
                        lambda provider, value: served.append((provider, value)))
    svc, qid, _ = wire(h, monkeypatch)
    with TestClient(create_app(
            svc.db.execute("PRAGMA database_list").fetchone()["file"],
            svc.h.data)) as client:
        for bad in ("../../evil", "/tmp/evil", "", None, 5):
            assert client.post("/ai/keys",
                              json={"provider": bad, "key": "x"}).status_code == 409
    assert served == []


def test_verify_route_accepts_a_provider_and_defaults_to_meta(h, monkeypatch):
    # The app builds its own transports, so both provider classes are replaced
    # with mock-transport factories: same wire rules, no socket.
    svc, qid, seen = wire(h, monkeypatch)
    svc.configure(True, 150)
    monkeypatch.setattr(service, "MetaProvider", _stub_meta)
    monkeypatch.setattr(service, "BasetenProvider", _stub_baseten)
    with TestClient(create_app(
            svc.db.execute("PRAGMA database_list").fetchone()["file"],
            svc.h.data)) as client:
        assert client.post("/ai/verify-account", json={}).status_code == 200
        assert client.post("/ai/verify-account",
                           json={"provider": "baseten"}).json()["provider"] == "baseten"
        assert client.post("/ai/verify-account",
                           json={"provider": "nope"}).status_code == 409
    catalogs = svc.settings()["provider_catalogs"]
    assert catalogs["meta"]["catalog"]               # Meta verified by the default call
    assert catalogs["baseten"]["catalog"] == [registry.BASETEN_MODEL_ID]
