"""Text-only, opt-in transport primitive; application execution stays blocked."""
import asyncio
import json
import time

import httpx
from jsonschema import Draft202012Validator

from . import modal_config as config
from .provider import ProviderError

MAX_RESPONSE_BYTES = 24 * 1024 * 1024


def _reject():
    raise ProviderError("modal_invalid_request")


def _integer(value):
    return type(value) is int and value >= 0


def _chat_body(model, body, assets):
    if (assets or getattr(model, "id", None) != config.MODEL_ID or
            not isinstance(body, dict) or body.get("model") != config.MODEL_ID):
        _reject()
    allowed = {"model", "input", "instructions", "max_output_tokens", "text",
               "tools", "store", "stream"}
    if (set(body) - allowed or body.get("store") is not False or
            body.get("stream", False) is not False or
            body.get("tools", []) != [] or
            not isinstance(body.get("instructions"), str) or
            not _integer(body.get("max_output_tokens")) or
            body["max_output_tokens"] == 0):
        _reject()
    messages = [{"role": "system", "content": body["instructions"]}]
    items = body.get("input")
    if not isinstance(items, list) or not items:
        _reject()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"role", "content"} or
                item["role"] != "user" or not isinstance(item["content"], list) or
                not item["content"]):
            _reject()
        blocks = []
        for block in item["content"]:
            if (not isinstance(block, dict) or set(block) != {"type", "text"} or
                    block["type"] != "input_text" or not isinstance(block["text"], str)):
                _reject()
            blocks.append({"type": "text", "text": block["text"]})
        messages.append({"role": "user", "content": blocks})
    text = body.get("text")
    if not isinstance(text, dict) or set(text) != {"format"}:
        _reject()
    fmt = text["format"]
    if (not isinstance(fmt, dict) or set(fmt) != {"type", "name", "strict", "schema"} or
            fmt["type"] != "json_schema" or fmt["strict"] is not True or
            not isinstance(fmt["name"], str) or not fmt["name"] or
            not isinstance(fmt["schema"], dict)):
        _reject()
    try:
        Draft202012Validator.check_schema(fmt["schema"])
    except Exception:
        _reject()
    return {"model": config.MODEL_ID, "messages": messages, "stream": False,
            "max_tokens": body["max_output_tokens"],
            "response_format": {"type": "json_schema", "json_schema": {
                k: fmt[k] for k in ("name", "strict", "schema")}}}


def _normalize(data):
    try:
        if not isinstance(data, dict) or data.get("model") != config.MODEL_ID or data.get("error"):
            raise ValueError()
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
        if (choice.get("finish_reason") != "stop" or message.get("role") != "assistant" or
                message.get("refusal") or message.get("tool_calls") or
                message.get("function_call") or not isinstance(content, str) or not content.strip()):
            raise ValueError()
        usage = data["usage"]
        incoming, outgoing = usage["prompt_tokens"], usage["completion_tokens"]
        details = usage.get("prompt_tokens_details", {})
        cached = details.get("cached_tokens", 0)
        if not all(_integer(n) for n in (incoming, outgoing, cached)) or cached > incoming:
            raise ValueError()
        if "total_tokens" in usage and (
                not _integer(usage["total_tokens"]) or usage["total_tokens"] != incoming + outgoing):
            raise ValueError()
        return {"model": data["model"], "status": "completed",
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": content}]}],
                "usage": {"input_tokens": incoming, "output_tokens": outgoing,
                          "input_tokens_details": {"cached_tokens": cached},
                          **({"total_tokens": usage["total_tokens"]} if "total_tokens" in usage else {})}}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("modal_invalid_response") from None


class ModalProvider:
    def __init__(self, transport=None, config_reader=config.read_config):
        self.transport = transport
        self.config_reader = config_reader

    async def catalog(self):
        raise ProviderError("modal_catalog_disabled")

    async def infer(self, model, body, assets):
        payload = _chat_body(model, body, assets)
        cfg = self.config_reader()
        # Revalidate injected configuration through the same origin policy.
        base = cfg.get("base_url")
        base = config.endpoint_origin(base)
        if not base or not config.valid_proxy_pair(cfg):
            raise ProviderError("modal_configuration_incomplete")
        started = time.perf_counter()
        try:
            async with asyncio.timeout(100):
                async with httpx.AsyncClient(
                        transport=self.transport, trust_env=False,
                        follow_redirects=False, timeout=90) as client:
                    async with client.stream("POST", base + config.CHAT_PATH,
                            headers={"Modal-Key": cfg["proxy_token_id"],
                                     "Modal-Secret": cfg["proxy_token_secret"]},
                            json=payload) as response:
                        if response.status_code != 200:
                            raise ProviderError("modal_http_error")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ProviderError("modal_response_too_large")
                            chunks.append(chunk)
            result = _normalize(json.loads(b"".join(chunks)))
            return result, time.perf_counter() - started
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, RecursionError):
            raise ProviderError("modal_transport_or_response_error") from None
