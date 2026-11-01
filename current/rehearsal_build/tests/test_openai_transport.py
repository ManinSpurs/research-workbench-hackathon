"""OpenAI transport + secret container, offline and synthetic only.

No socket, no real key, no paid call: the provider runs on an injected
``httpx.MockTransport`` and every secret test points at a temporary HOME.
"""
import asyncio
import json
import stat
from pathlib import Path

import httpx
import pytest

from app.ai import grounding, registry, secrets
from app.ai.provider import OpenAIProvider, ProviderError

KEY = "sk-synthetic-openai-transport-key"
MODEL = registry.MODELS[registry.OPENAI_MODEL_ID]
RESULT = {"model": registry.OPENAI_MODEL_ID, "status": "completed",
          "output": [{"type": "message", "content": [
              {"type": "output_text", "text": json.dumps({"answer": "ok"})}]}],
          "usage": {"input_tokens": 200, "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 50}}}


def body(**over):
    base = {"model": registry.OPENAI_MODEL_ID, "store": False,
            "instructions": "system",
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "synthetic prompt"}]}],
            "max_output_tokens": 50, "tools": [],
            "text": {"format": {"type": "json_schema", "name": "Answer",
                                "strict": True,
                                "schema": {"type": "object",
                                           "properties": {"answer": {"type": "string"}},
                                           "required": ["answer"],
                                           "additionalProperties": False}}}}
    base.update(over)
    return base


def run(payload=None, result=None, assets=None, key=KEY, status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=RESULT if result is None else result)

    provider = OpenAIProvider(httpx.MockTransport(handler), key_reader=lambda: key)

    async def invoke():
        return await provider.infer(MODEL, body() if payload is None else payload,
                                    assets or [])
    return seen, invoke


def test_fixed_origin_and_bearer_only():
    seen, invoke = run()
    result, elapsed = asyncio.run(invoke())
    request = seen[0]
    # Official global origin with the documented /v1 base and one path segment.
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer " + KEY
    assert "openai-organization" not in request.headers
    assert isinstance(elapsed, float) and elapsed >= 0
    assert result["model"] == registry.OPENAI_MODEL_ID


def test_store_false_and_default_service_tier_are_forced():
    payload = body()
    del payload["store"]
    seen, invoke = run(payload)
    asyncio.run(invoke())
    sent = json.loads(seen[0].content)
    # Nothing may be retained provider-side, and no paid priority tier is used.
    assert sent["store"] is False
    assert sent["service_tier"] == "default"
    assert sent["tools"] == []
    assert sent["model"] == registry.OPENAI_MODEL_ID
    # The body is otherwise forwarded verbatim: no invented keys.
    assert set(sent) == {"model", "input", "store", "instructions",
                         "max_output_tokens", "tools", "text", "service_tier"}


def test_request_that_asks_for_storage_or_tools_fails_closed():
    for payload in (body(store=True), body(tools=[{"type": "web_search"}]),
                    body(session_id="sid"), body(text_prompt="hi"),
                    body(mode="DIARIZATION")):
        seen, invoke = run(payload)
        with pytest.raises(ProviderError):
            asyncio.run(invoke())
        assert seen == []


def test_assets_are_never_transmitted():
    seen, invoke = run(assets=[{"id": "a1", "meta": {"name": "x.png"},
                                "encoded": "AAAA"}])
    with pytest.raises(ProviderError) as exc:
        asyncio.run(invoke())
    assert exc.value.code == "openai_assets_not_supported"
    assert seen == []


def test_non_text_input_part_fails_closed():
    payload = body(input=[{"role": "user", "content": [
        {"type": "input_image", "image_url": "https://example.test/i.png"}]}])
    seen, invoke = run(payload)
    with pytest.raises(ProviderError):
        asyncio.run(invoke())
    assert seen == []


def test_model_identity_must_be_the_pinned_snapshot():
    seen, invoke = run(result={**RESULT, "model": "gpt-5.4-mini"})
    with pytest.raises(ProviderError) as exc:
        asyncio.run(invoke())
    assert exc.value.code == "unexpected_model_identity"


def test_wrong_registered_model_is_refused_before_send():
    seen, invoke = run(body(model="muse-spark-1.3"))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(invoke())
    assert exc.value.code == "openai_invalid_request"
    assert seen == []


def test_missing_key_sends_nothing():
    seen, invoke = run(key=None)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(invoke())
    assert exc.value.code == "key_missing"
    assert seen == []


def test_provider_rejects_paid_failures_with_documented_codes():
    seen, invoke = run(status=401)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(invoke())
    assert exc.value.code == "authentication_failed"


def test_catalog_uses_fixed_origin_and_reports_ids():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": registry.OPENAI_MODEL_ID},
                                                  {"id": "gpt-5.4-mini"}]})

    provider = OpenAIProvider(httpx.MockTransport(handler), key_reader=lambda: KEY)
    ids = asyncio.run(provider.catalog())
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer " + KEY
    assert ids == sorted([registry.OPENAI_MODEL_ID, "gpt-5.4-mini"])


def test_registry_exports_and_prices_match_the_pinned_snapshot():
    assert registry.OPENAI_PROVIDER == "openai"
    assert registry.OPENAI_MODEL_ID == "gpt-5.4-mini-2026-03-17"
    assert registry.OPENAI_BASE_URL == "https://api.openai.com/v1"
    assert MODEL.kind == "reasoning" and MODEL.endpoint == "/responses"
    assert MODEL.modalities == ("text",)
    assert MODEL.context == 400000
    assert MODEL.input_price == .75 and MODEL.output_price == 4.5
    assert registry.OPENAI_CACHED_INPUT_PRICE == .075
    assert "test_only_live_not_run" in MODEL.status
    # The whole reservation path: full documented context at the uncached rate.
    assert round((MODEL.context * MODEL.input_price + 50 * MODEL.output_price) / 1e6, 6) == .300225


def test_tasks_defaults_are_unchanged_by_the_new_row():
    assert registry.TASKS == {"research_query": "meta-models/muse-spark-open-1026",
                              "literature_analysis": "meta-models/muse-spark-open-1026",
                              "methodology_review": "muse-wonder-1.0",
                              "cross_artifact_review": "muse-wonder-1.0",
                              "image_generate": "muse-image-1.0",
                              "image_edit": "muse-image-1.0",
                              "transcribe": "muse-voice-transcribe-1.0",
                              "segment": "sam-3.1",
                              # Baseten-hosted by design (Meta credits exhausted).
                              "claim_check": "meta-models/muse-spark-open-1026"}
    # The alias id is not routable: no recorded price belongs to a floating name.
    assert "gpt-5.4-mini" not in registry.MODELS


def test_registry_row_exposes_presence_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    snap = registry.registry()
    row = {p["id"]: p for p in snap["providers"]}["openai"]
    assert row["key_present"] is True
    assert row["base_url"] == "https://api.openai.com/v1"
    assert row["model_id"] == registry.OPENAI_MODEL_ID
    assert row["account_verified"] == "NOT_CHECKED"
    assert row["store"] is False and row["service_tier"] == "default"
    assert row["tools"] == []
    assert KEY not in json.dumps(snap)
    with monkeypatch.context() as patch:
        patch.delenv("OPENAI_API_KEY", raising=False)
        patch.setattr(secrets, "openai_secret_path",
                      lambda: Path("/nonexistent/openai_api_key"))
        assert secrets.openai_key_present() is False


def test_structured_output_schemas_satisfy_documented_strict_rules():
    """The two schemas the route sends must fit the documented strict subset.

    Rules checked (OpenAI structured-outputs guide): every object sets
    ``additionalProperties: false``, every property is listed in ``required``,
    the root is an object (never ``anyOf``), and an optional field is expressed
    as a nullable type rather than by omission.
    """
    def walk(node):
        if isinstance(node, dict):
            assert not (node.get("type") == "object" and "properties" in node
                        and node.get("additionalProperties") is not False)
            if "properties" in node:
                assert sorted(node.get("required") or []) == sorted(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for schema in (grounding.ResearchResult.model_json_schema(),
                   grounding.Claim.model_json_schema(),
                   grounding.ProposedVariable.model_json_schema()):
        assert schema["type"] == "object" and "anyOf" not in schema
        walk(schema)
    nullable = grounding.ProposedVariable.model_json_schema()["properties"]["dataset_id"]
    assert nullable["anyOf"] == [{"type": "string"}, {"type": "null"}]
    # No numeric or string constraint that the documented subset forbids.
    assert "minLength" not in json.dumps(grounding.ResearchResult.model_json_schema()
                                          .get("properties", {}))


def test_secret_container_is_isolated_owner_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for name in ("META_API_KEY", "MODEL_API_KEY", "BASETEN_API_KEY",
                 "WB_MODAL_SHARED_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Nothing is configured at first, and no provider borrows another's key.
    assert secrets.openai_key() is None and secrets.openai_key_present() is False
    assert secrets.provider_key("openai") is None
    path = secrets.save("openai", KEY)
    assert path == home / "Library" / "Application Support" / "ResearchWorkbench" / "openai_api_key"
    assert path.read_text() == KEY
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Other providers' files were not created or written.
    assert not (path.parent / "meta_api_key").exists()
    assert not (path.parent / "modal_shared_api_key").exists()
    assert secrets.openai_key() == KEY
    assert secrets.openai_key_present() is True
    assert secrets.provider_key("openai") == KEY
    assert secrets.present("openai") is True
    assert secrets.env_override("openai") is None
    assert secrets.env_override("meta") is None
    # Reuse of the shared mapping: unknown providers still have no key file.
    with pytest.raises(ValueError):
        secrets.save("openai-v2", KEY)
    assert secrets.provider_key("openai-v2") is None
    # An environment override wins over the file and is reported as the source.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-override")
    assert secrets.openai_key() == "sk-env-override"
    assert secrets.env_override("openai") == "OPENAI_API_KEY"
    # A group/world-readable file is a hard error, never a silent downgrade.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        secrets.openai_key()


def test_openai_key_never_reads_another_provider_secret(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for name in ("META_API_KEY", "MODEL_API_KEY", "BASETEN_API_KEY",
                 "WB_MODAL_SHARED_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    secrets.save("meta", "sk-meta-only")
    secrets.save("baseten", "b10-baseten-only")
    assert secrets.key() == "sk-meta-only"
    assert secrets.baseten_key() == "b10-baseten-only"
    assert secrets.openai_key() is None
    with pytest.raises(ProviderError) as exc:
        asyncio.run(run(key=None)[1]())
    assert exc.value.code == "key_missing"
