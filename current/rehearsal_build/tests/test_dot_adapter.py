"""Bounded adapter accounting through the real SDK, using only an in-memory transport."""
import asyncio
import json
import sys
from pathlib import Path

import httpx2 as httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import adapters
from app.adapters import ModelAdapter, Usage, call_limits
from pydantic_ai.exceptions import UsageLimitExceeded


def reply(content='{"refined_text":"Question","kept_constraints":[]}', *, usage=True):
    body = {"id": "mock", "object": "chat.completion", "created": 0,
            "model": "muse-spark-1.3", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return httpx.Response(200, json=body)


def make_adapter(monkeypatch, handler):
    monkeypatch.setenv("FAKE_BASE_URL", "http://127.0.0.1:9/v1")
    original = adapters.DefaultAsyncHttpxClient
    monkeypatch.setattr(adapters, "DefaultAsyncHttpxClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return ModelAdapter()


def test_success_and_retry_usage(monkeypatch):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return reply('{}' if len(requests) == 1 else '{"refined_text":"Question","kept_constraints":[]}')
    adapter = make_adapter(monkeypatch, handler)
    out, usage = asyncio.run(adapter.refine("Question"))
    assert out.refined_text == "Question"
    assert usage == Usage(2, 20, 10)
    assert len(requests) == 2
    assert requests[0]["max_completion_tokens"] == 4096
    assert requests[0]["reasoning_effort"] == "low"
    assert requests[0]["response_format"]["json_schema"]["strict"] is True


def test_validation_failure_has_consumed_usage_and_two_request_cap(monkeypatch):
    seen = []
    adapter = make_adapter(monkeypatch, lambda req: seen.append(req) or reply('{}'))
    with pytest.raises(Exception) as error:
        asyncio.run(adapter.refine("Question"))
    assert len(seen) == 2
    assert error.value.workbench_usage == Usage(2, 20, 10)


@pytest.mark.parametrize("request_limit,token_limit,expected", [(1, 16000, 1), (2, 5, 1), (0, 100, 0), (2, 0, 0)])
def test_remaining_limits(monkeypatch, request_limit, token_limit, expected):
    seen = []
    adapter = make_adapter(monkeypatch, lambda req: seen.append(req) or reply('{}'))
    with call_limits(request_limit=request_limit, total_tokens_limit=token_limit):
        with pytest.raises(UsageLimitExceeded) as error:
            asyncio.run(adapter.refine("Question"))
    assert len(seen) == expected
    assert error.value.workbench_usage.requests == expected


@pytest.mark.parametrize("after_response", [False, True])
def test_transport_failure_counts_attempts_without_inventing_tokens(monkeypatch, after_response):
    seen = []
    def handler(req):
        seen.append(req)
        if after_response and len(seen) == 1:
            return reply('{}')
        raise httpx.ConnectError("sensitive provider text", request=req)
    adapter = make_adapter(monkeypatch, handler)
    with pytest.raises(Exception) as error:
        asyncio.run(adapter.refine("Question"))
    assert len(seen) == (2 if after_response else 1)  # SDK retries disabled
    assert error.value.workbench_usage == Usage(len(seen), None, None, False)


def test_missing_provider_usage_is_unknown(monkeypatch):
    adapter = make_adapter(monkeypatch, lambda req: reply(usage=False))
    with pytest.warns(Warning, match="omitted usage"):
        _, usage = asyncio.run(adapter.refine("Question"))
    assert usage == Usage(1, None, None, False)


def test_timeout_retains_outbound_attempt(monkeypatch):
    async def handler(req):
        await asyncio.sleep(1)
        return reply()
    adapter = make_adapter(monkeypatch, handler)
    with call_limits(timeout=0.02):
        with pytest.raises(TimeoutError) as error:
            asyncio.run(adapter.refine("Question"))
    assert error.value.workbench_usage == Usage(1, None, None, False)


def test_task_local_limits_and_observation(monkeypatch):
    async def handler(req):
        await asyncio.sleep(0.01)
        return reply('{}')
    adapter = make_adapter(monkeypatch, handler)
    async def run(limit):
        with call_limits(request_limit=limit):
            try:
                await adapter.refine("Question")
            except Exception as error:
                return error.workbench_usage.requests
    async def main():
        return await asyncio.gather(run(1), run(2))
    assert asyncio.run(main()) == [1, 2]
    assert adapters._limits.get().request_limit == 2
    assert adapters._observation.get() is None


def test_nested_limits_cannot_relax_caps():
    with call_limits(request_limit=1, total_tokens_limit=20, timeout=2):
        with call_limits(request_limit=100, total_tokens_limit=100000, timeout=100):
            assert adapters._limits.get() == adapters.CallLimits(1, 20, 2)
    assert adapters._limits.get() == adapters.CallLimits()


def test_real_endpoint_fails_closed(monkeypatch):
    monkeypatch.delenv("FAKE_BASE_URL", raising=False)
    monkeypatch.delenv("WB_ALLOW_REAL_MUSE", raising=False)
    monkeypatch.setenv("META_API_KEY", "not-authorized-for-use")
    with pytest.raises(RuntimeError, match="disabled"):
        ModelAdapter()


@pytest.mark.parametrize("url", ["https://api.meta.ai/v1", "https://example.org/v1", "http://secret@localhost/v1"])
def test_remote_fake_endpoint_is_not_an_opt_in_bypass(monkeypatch, url):
    monkeypatch.setenv("FAKE_BASE_URL", url)
    with pytest.raises(ValueError, match="loopback"):
        ModelAdapter()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_is_not_retried_and_usage_unknown(monkeypatch, status):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(status, json={"error": {"message": "sensitive response"}})
    adapter = make_adapter(monkeypatch, handler)
    with pytest.raises(Exception) as error:
        asyncio.run(adapter.refine("Question"))
    assert len(seen) == 1
    assert error.value.workbench_usage == Usage(1, None, None, False)


def test_cancellation_carries_usage_and_resets_context(monkeypatch):
    async def handler(req):
        await asyncio.sleep(10)
        return reply()
    adapter = make_adapter(monkeypatch, handler)
    async def main():
        task = asyncio.create_task(adapter.refine("Question"))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert error.value.workbench_usage == Usage(1, None, None, False)
        assert adapters._observation.get() is None
    asyncio.run(main())
