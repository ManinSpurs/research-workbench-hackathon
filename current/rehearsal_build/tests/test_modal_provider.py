"""Offline protocol tests only: no Modal SDK, deployment or network."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.ai import modal_config as cfg
from app.ai.modal_provider import ModalProvider, MAX_RESPONSE_BYTES
from app.ai.provider import ProviderError, output_text

MODEL = SimpleNamespace(id=cfg.MODEL_ID)
CONFIG = {"base_url": "https://example.modal.run", "proxy_token_id": "test-id",
          "proxy_token_secret": "test-secret"}
BODY = {"model": cfg.MODEL_ID, "store": False, "instructions": "system",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "max_output_tokens": 50, "tools": [],
        "text": {"format": {"type": "json_schema", "name": "Answer", "strict": True,
                            "schema": {"type": "object"}}}}
RESULT = {"model": cfg.MODEL_ID,
          "choices": [{"finish_reason": "stop", "message": {
              "role": "assistant", "content": '{"answer":"ok"}'}}],
          "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def run(body=None, result=None, config=None, assets=None, status=200, error=None):
    seen = []
    def handler(request):
        seen.append(request)
        if error:
            raise error
        return httpx.Response(status, json=result if result is not None else RESULT)
    provider = ModalProvider(httpx.MockTransport(handler),
                             config_reader=lambda: CONFIG if config is None else config)
    async def invoke():
        return await provider.infer(MODEL, BODY if body is None else body, assets or [])
    return seen, invoke


def test_translation_and_normalization():
    seen, invoke = run()
    result, elapsed = asyncio.run(invoke())
    request = seen[0]
    assert str(request.url) == CONFIG["base_url"] + cfg.CHAT_PATH
    assert request.headers["Modal-Key"] == "test-id"
    assert request.headers["Modal-Secret"] == "test-secret"
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "model": cfg.MODEL_ID, "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        "max_tokens": 50, "stream": False,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "Answer", "strict": True, "schema": {"type": "object"}}}}
    assert output_text(result) == '{"answer":"ok"}'
    assert result["model"] == cfg.MODEL_ID and elapsed >= 0
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3,
                               "input_tokens_details": {"cached_tokens": 0}}
    assert "cost" not in result


@pytest.mark.parametrize("url", ["", "http://example.modal.run", "https://localhost",
    "https://example.com", "https://user:secret@example.modal.run",
    "https://example.modal.run/path", "https://example.modal.run?secret",
    "https://example.modal.run#secret", "https://example.modal.run:443",
    "https://example.modal.run.evil.org", "https://example.modal.run\\@evil.org",
    "https://server.us-east.modal.direct.evil.org",
    "https://server.us-east.modal.run.evil.org",
    "https://server.us-east.evilmodal.direct",
    "https://server..modal.direct", "https://-server.us-east.modal.direct",
    "https://server-.us-east.modal.direct",
    "https://user:secret@server.us-east.modal.direct",
    "https://server.us-east.modal.direct:443",
    "https://server.us-east.modal.direct/path",
    "https://server.us-east.modal.direct?secret",
    "https://server.us-east.modal.direct#secret",
    "http://server.us-east.modal.direct"])
def test_config_fail_closed(monkeypatch, url):
    monkeypatch.setenv("WB_MODAL_BASE_URL", url)
    monkeypatch.delenv("WB_MODAL_PROXY_TOKEN_ID", raising=False)
    monkeypatch.delenv("WB_MODAL_PROXY_TOKEN_SECRET", raising=False)
    status = cfg.connection_status()
    assert status["base_url"] is None and not status["execution_enabled"]
    assert not status["endpoint_configured"]
    assert "gpu_accounting_missing" in status["blocking_reasons"]
    assert "secret" not in json.dumps(status)


def test_valid_status_still_blocked(monkeypatch):
    monkeypatch.setenv("WB_MODAL_BASE_URL", "https://example.modal.direct/")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_ID", "test-id")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_SECRET", "test-secret")
    status = cfg.connection_status()
    assert status["endpoint_configured"] and status["proxy_auth_configured"]
    assert status["execution_enabled"] is False and status["status"] == "blocked"
    assert status["billing"] == "gpu_time"


@pytest.mark.parametrize("origin", [
    "https://your-workspace-name--example-vllm-inference-server.us-east.modal.direct",
    "https://server.us-east.modal.run"])
def test_regional_endpoint_status_and_transport(monkeypatch, origin):
    monkeypatch.setenv("WB_MODAL_BASE_URL", origin + "/")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_ID", "test-id")
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_SECRET", "test-secret")
    status = cfg.connection_status()
    assert status["base_url"] == origin
    assert status["endpoint_configured"] is True
    assert status["execution_enabled"] is False
    seen, invoke = run(config={**CONFIG, "base_url": origin})
    asyncio.run(invoke())
    assert len(seen) == 1
    assert str(seen[0].url) == origin + cfg.CHAT_PATH


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(model="alias"), lambda b: b.update(tools=[{"type": "web_search"}]),
    lambda b: b.update(stream=True), lambda b: b.update(store=True),
    lambda b: b.update(temperature=1),
    lambda b: b["input"][0]["content"][0].update(type="input_image"),
    lambda b: b["text"]["format"].update(schema={"type": "invalid"}),
    lambda b: b.update(max_output_tokens=True)])
def test_invalid_requests_no_send(mutation):
    body = copy.deepcopy(BODY)
    mutation(body)
    seen, invoke = run(body=body)
    with pytest.raises(ProviderError):
        asyncio.run(invoke())
    assert seen == []


@pytest.mark.parametrize("config,assets", [({}, []), (CONFIG, [{"id": "media"}]),
    ({**CONFIG, "proxy_token_secret": ""}, []),
    ({**CONFIG, "base_url": "https://evil.org"}, [])])
def test_incomplete_and_assets_no_send(config, assets):
    seen, invoke = run(config=config, assets=assets)
    with pytest.raises(ProviderError):
        asyncio.run(invoke())
    assert not seen


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(model="alias"), lambda r: r.pop("usage"),
    lambda r: r["usage"].update(prompt_tokens=True),
    lambda r: r["usage"].update(completion_tokens=-1),
    lambda r: r["choices"][0].update(finish_reason="length"),
    lambda r: r["choices"][0]["message"].update(refusal="no"),
    lambda r: r["choices"][0]["message"].update(tool_calls=[{}]),
    lambda r: r["choices"][0]["message"].update(content=None)])
def test_invalid_results_no_retry(mutation):
    result = copy.deepcopy(RESULT)
    mutation(result)
    seen, invoke = run(result=result)
    with pytest.raises(ProviderError):
        asyncio.run(invoke())
    assert len(seen) == 1


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_no_retry(status):
    seen, invoke = run(status=status)
    with pytest.raises(ProviderError, match="modal_http_error"):
        asyncio.run(invoke())
    assert len(seen) == 1


def test_timeout_no_retry():
    seen, invoke = run(error=httpx.ReadTimeout("sensitive"))
    with pytest.raises(ProviderError, match="^modal_transport_or_response_error$"):
        asyncio.run(invoke())
    assert len(seen) == 1


def test_deep_json_sanitized_no_retry():
    depth = sys.getrecursionlimit() * 2
    raw = b"[" * depth + b'"sensitive-response"' + b"]" * depth
    assert len(raw) < MAX_RESPONSE_BYTES
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw)
    provider = ModalProvider(httpx.MockTransport(handler), lambda: CONFIG)
    with pytest.raises(ProviderError, match="^modal_(transport_or_response_error|invalid_response)$") as exc:
        asyncio.run(provider.infer(MODEL, BODY, []))
    assert exc.value.__suppress_context__ is True
    assert "sensitive-response" not in str(exc.value)
    assert len(calls) == 1


def test_json_recursion_error_sanitized_no_retry(monkeypatch):
    raw = b'{"sensitive-response":[]}'
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw)
    def exhausted_decoder(data):
        assert data == raw
        raise RecursionError("sensitive-response")
    monkeypatch.setattr("app.ai.modal_provider.json.loads", exhausted_decoder)
    provider = ModalProvider(httpx.MockTransport(handler), lambda: CONFIG)
    with pytest.raises(ProviderError, match="^modal_transport_or_response_error$") as exc:
        asyncio.run(provider.infer(MODEL, BODY, []))
    assert exc.value.__suppress_context__ is True
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [
    "https://exam\tple.modal.run", "https://exam\nple.modal.run",
    "https://exam\rple.modal.run", "https://example.modal.run/\n",
    "https://example.us-east.modal.direct/\t", "https://example.modal.run/\x01"])
def test_control_characters_no_send(monkeypatch, raw):
    monkeypatch.setenv("WB_MODAL_BASE_URL", raw)
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_ID", CONFIG["proxy_token_id"])
    monkeypatch.setenv("WB_MODAL_PROXY_TOKEN_SECRET", CONFIG["proxy_token_secret"])
    assert cfg.endpoint_origin(raw) is None
    assert cfg.connection_status()["endpoint_configured"] is False
    for config in (cfg.read_config(), {**CONFIG, "base_url": raw}):
        seen, invoke = run(config=config)
        with pytest.raises(ProviderError, match="^modal_configuration_incomplete$"):
            asyncio.run(invoke())
        assert not seen


@pytest.mark.parametrize("total", [True, False, -1, 1.0, "10", None, 9, 11])
def test_invalid_total_tokens_no_retry(total):
    result = copy.deepcopy(RESULT)
    result["usage"]["total_tokens"] = total
    seen, invoke = run(result=result)
    with pytest.raises(ProviderError, match="^modal_invalid_response$"):
        asyncio.run(invoke())
    assert len(seen) == 1


def test_valid_total_tokens_preserved():
    result = copy.deepcopy(RESULT)
    result["usage"]["total_tokens"] = 10
    seen, invoke = run(result=result)
    normalized, _ = asyncio.run(invoke())
    assert normalized["usage"]["total_tokens"] == 10
    assert len(seen) == 1


def test_oversized_and_catalog():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
    provider = ModalProvider(httpx.MockTransport(handler), lambda: CONFIG)
    with pytest.raises(ProviderError, match="modal_catalog_disabled"):
        asyncio.run(provider.catalog())
    assert not calls
    with pytest.raises(ProviderError, match="modal_response_too_large"):
        asyncio.run(provider.infer(MODEL, BODY, []))
    assert len(calls) == 1
