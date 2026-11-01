"""L2: LocalProvider loopback-only transport. Mock transport only, no network.

Owned scope: LocalProvider in app/ai/provider.py. No registry/service edits.
"""
import asyncio
import inspect
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
import pytest

from app.ai import registry
from app.ai.provider import (
    LOCAL_CHAT_PATH,
    LocalProvider,
    MetaProvider,
    ProviderError,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

OPENAI_OK = {
    "id": "chatcmpl-local-test",
    "object": "chat.completion",
    "created": 1760000000,
    "model": "local:ollama:qwen3-4b-q4",
    "choices": [{"index": 0, "message": {"role": "assistant",
                "content": '{"answer": "ok"}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

LOCAL_MODEL_ID = "local:ollama:qwen3-4b-q4"
#: The per-run bearer the launcher would generate. A test value: never a real
#: credential, and it never leaves this process.
SECRET = "test-run-bearer-not-a-real-credential"


def _local_model(model_id=LOCAL_MODEL_ID, kind="local_reasoning"):
    return registry.Model(model_id, kind, ("text",),
                          "http://127.0.0.1:11434" + LOCAL_CHAT_PATH)


def _provider(handler, base_url="http://127.0.0.1:11434", **kw):
    # Every local transport is authenticated: the per-run secret is mandatory at
    # construction, so a test that omitted it could only exercise a state this
    # app refuses to run in.
    kw.setdefault("bearer", SECRET)
    return LocalProvider(base_url, model=LOCAL_MODEL_ID,
                         transport=httpx.MockTransport(handler), **kw)


def _seen_hosts(seen):
    return {urlsplit(str(r.url)).hostname for r in seen}


# --- construction: loopback accepted ---

@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "http://[::1]:11434",
    "HTTP://LOCALHOST:11434",
    "http://127.0.0.1:11434/prefix",
])
def test_loopback_base_urls_accepted(base_url):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=dict(OPENAI_OK))

    provider = _provider(handler, base_url)
    result, elapsed = asyncio.run(provider.request(
        "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert result == OPENAI_OK and elapsed >= 0
    assert _seen_hosts(seen) <= LOOPBACK_HOSTS
    assert all(urlsplit(str(r.url)).scheme == "http" for r in seen)


# --- construction: non-loopback refused fail-fast ---

@pytest.mark.parametrize("base_url", [
    "https://api.meta.ai/v1",          # https-to-public
    "http://api.meta.ai/v1",           # DNS name
    "http://example.com:11434",        # DNS name
    "http://192.168.1.10:11434",       # LAN IP
    "http://10.0.0.5:11434",           # LAN IP
    "http://0.0.0.0:11434",            # unspecified address, not loopback
    "http://8.8.8.8:11434",            # public IP
    "http://localhost.evil.com:11434",  # suffix trick
    "http://127.0.0.1.evil.com:11434",  # suffix trick
    "http://[::ffff:127.0.0.1]:11434",  # IPv4-mapped, not a bare loopback literal
    "http://config:secret@127.0.0.1:11434",  # userinfo trick on loopback host
    "https://127.0.0.1:11434",         # https refused: loopback is http-only
    "ftp://127.0.0.1:11434",           # wrong scheme
    "not-a-url",
    "",
])
def test_non_loopback_base_urls_refused_at_construction(base_url):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=dict(OPENAI_OK))

    transport = httpx.MockTransport(handler)
    with pytest.raises(ProviderError) as exc:
        LocalProvider(base_url, model=LOCAL_MODEL_ID, transport=transport)
    assert exc.value.code == "local_unreachable"
    assert not seen  # fail fast: no request attempted


# --- interface parity with the provider abstraction ---

def test_same_call_interface_as_meta_provider():
    for name in ("request", "catalog", "infer"):
        assert callable(getattr(LocalProvider, name, None))
    assert (list(inspect.signature(LocalProvider.infer).parameters)
            == list(inspect.signature(MetaProvider.infer).parameters))
    assert (list(inspect.signature(LocalProvider.catalog).parameters)
            == list(inspect.signature(MetaProvider.catalog).parameters))
    params = inspect.signature(LocalProvider.request).parameters
    assert {"self", "method", "path", "body"} <= set(params)


# --- success path ---

def test_success_path_returns_parsed_body():
    seen = []

    def handler(req):
        seen.append(req)
        assert req.method == "POST"
        assert req.url.path == LOCAL_CHAT_PATH
        # The only credential is this run's bearer; no cloud key shape appears.
        assert req.headers.get("authorization") == "Bearer " + SECRET
        assert "x-api-key" not in req.headers
        payload = json.loads(req.content)
        assert payload["model"] == LOCAL_MODEL_ID
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(200, json=dict(OPENAI_OK))

    provider = _provider(handler)
    model = _local_model()
    response, elapsed = asyncio.run(
        provider.infer(model, {"instructions": "answer"}, []))
    assert elapsed >= 0
    # infer() returns the cloud response envelope, not the raw chat body: the
    # shared pipeline reads output[].content[].text (output_text), gates on
    # response['model'] against the APP model id, and requires status
    # 'completed'. Emitting the raw body here would make every local job fail
    # those gates, so this normalized shape is the contract.
    assert set(response) == {"model", "status", "output", "usage"}
    assert response["model"] == LOCAL_MODEL_ID
    assert response["status"] == "completed"
    assert response["output"] == [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": '{"answer": "ok"}'}]}]
    # Chat-completion token names are mapped to the cloud usage names the
    # accounting code reads; no price or cost field is invented.
    assert response["usage"] == {"input_tokens": 10, "output_tokens": 5,
                                 "total_tokens": 15}
    assert not any(k in response for k in ("cost", "cost_usd", "pricing"))
    assert _seen_hosts(seen) <= LOOPBACK_HOSTS


def test_catalog_is_configured_tag_no_network():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=dict(OPENAI_OK))

    assert asyncio.run(_provider(handler).catalog()) == [LOCAL_MODEL_ID]
    assert not seen


def test_request_rejects_non_chat_path_and_method():
    provider = _provider(lambda req: httpx.Response(200, json={}))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.request("GET", LOCAL_CHAT_PATH))
    assert exc.value.code == "endpoint_not_allowed"
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.request("POST", "/responses", body={}))
    assert exc.value.code == "endpoint_not_allowed"


# --- honest failure mapping ---

def test_connection_refused_maps_with_local_cause():
    def handler(req):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert exc.value.code == "transport_or_response_error"
    assert isinstance(exc.value.__cause__, httpx.ConnectError)


@pytest.mark.parametrize("timeout_exc", [
    httpx.ConnectTimeout("connect timed out"),
    httpx.ReadTimeout("read timed out"),
])
def test_timeout_maps_with_local_cause(timeout_exc):
    def handler(req):
        raise timeout_exc

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert exc.value.code == "transport_or_response_error"
    assert exc.value.__cause__ is timeout_exc


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_non_200_maps_to_existing_http_vocabulary(status):
    def handler(req):
        return httpx.Response(status, json={"error": "x"})

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert exc.value.code == "provider_http_" + str(status)


@pytest.mark.parametrize("content", [b"not json{{", b"[1, 2]", b""])
def test_malformed_body_maps_without_fabrication(content):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=content)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert exc.value.code == "transport_or_response_error"
    assert len(calls) == 1  # failed once: no retry, no completion fabricated


def test_oversize_body_rejected():
    def handler(req):
        return httpx.Response(200, content=b"x" * (24 * 1024 * 1024 + 1))

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert exc.value.code == "provider_response_too_large"


def test_no_retry_beyond_single_send():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ProviderError):
        asyncio.run(_provider(handler).request(
            "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert len(calls) == 1


def test_model_mismatch_fails_closed():
    provider = _provider(lambda req: httpx.Response(200, json=dict(OPENAI_OK)))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.infer(
            _local_model("local:ollama:other-tag"), {"instructions": ""}, []))
    assert exc.value.code == "local_model_mismatch"


@pytest.mark.parametrize("kind", ["transcription", "segmentation", "image"])
def test_non_reasoning_kinds_rejected(kind):
    provider = _provider(lambda req: httpx.Response(200, json=dict(OPENAI_OK)))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.infer(
            _local_model(LOCAL_MODEL_ID, kind), {"instructions": ""}, []))
    assert exc.value.code == "endpoint_not_allowed"


# --- authentication is mandatory, not optional ----------------------------

def test_a_transport_without_the_run_secret_is_refused():
    """Loopback alone authorizes nothing: no bearer, no transport."""
    with pytest.raises(ProviderError) as exc:
        LocalProvider("http://127.0.0.1:11434", model=LOCAL_MODEL_ID,
                      transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert exc.value.code == "local_run_secret_missing"
    # And the same refusal for an empty string: presence, not truthiness.
    with pytest.raises(ProviderError):
        LocalProvider("http://127.0.0.1:11434", bearer="", model=LOCAL_MODEL_ID,
                      transport=httpx.MockTransport(lambda r: httpx.Response(500)))


def test_the_bearer_is_the_only_credential_and_is_never_in_the_body():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=dict(OPENAI_OK))

    asyncio.run(_provider(handler).request(
        "POST", LOCAL_CHAT_PATH, body={"messages": [], "api_key": "ignored"}))
    request = seen[0]
    assert request.headers.get("authorization") == "Bearer " + SECRET
    # The secret authorizes via the header only; it is not copied into content.
    assert SECRET.encode() not in request.content


# --- zero spend semantics ---

def test_no_pricing_no_cost_fields():
    provider = _provider(lambda req: httpx.Response(200, json=dict(OPENAI_OK)))
    for attr in ("input_price", "output_price", "price", "cost", "pricing"):
        assert not hasattr(provider, attr)
    result, _ = asyncio.run(provider.request(
        "POST", LOCAL_CHAT_PATH, body={"messages": []}))
    assert set(result) == set(OPENAI_OK)  # nothing invented, no cost added
