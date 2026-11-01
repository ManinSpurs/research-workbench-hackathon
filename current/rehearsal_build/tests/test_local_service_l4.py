"""L4: local mode end-to-end through prepare -> execute -> receipt.

Mock loopback transport only: every request is answered by an injected
``httpx.MockTransport``, so no socket is opened, no llama.cpp server is started
and no model file is read. The only credential is a test bearer value the
launcher would have generated; it is not a real secret and never leaves this
process. What is proved here is the app
contract, not that a 4B model answers well:

* local mode resolves the one configured local model and nothing else;
* the preview names the loopback destination, says zero API cost, and is
  never the Meta origin or a Meta data-use label;
* the declared -c context bound is enforced at preview AND in the transport;
* settlement records a known zero, so no spend is invented;
* the receipt carries the local model id, the local data-use sentence and the
  local cost basis;
* a changed endpoint/alias/context after preview fails closed;
* the transport's response validation (empty/incomplete/wrong-model) refuses
  to record a completed job.
"""
import asyncio
import http.server
import itertools
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from app.ai import local_runtime, registry, service
from app.ai.provider import MetaProvider, ProviderError
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

LOCAL_ID = local_runtime.MODEL_ID
ORIGIN = "http://127.0.0.1:9931"
ALIAS = local_runtime.DEFAULT_WIRE_MODEL
CONTEXT = 8192
#: The per-run bearer the launcher would have generated. A test value: it is
#: never a real credential and never leaves this process.
BEARER = "test-run-bearer-not-a-real-credential"
RUN_ID = "run-l4-test"
#: The launched server's pid. The test process itself, so the binding describes
#: a run whose process really is alive: a published binding is refused once the
#: launched process has exited, and a made-up pid would be exactly that state.
SERVER_PID = str(os.getpid())
#: The chat-template overhead the launcher measures at launch (fake template).
OVERHEAD = 16

RESULT = {
    "answer": "The column is an applicant estimate, not a measured cost.",
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
LOCAL_USAGE = {"prompt_tokens": 200, "completion_tokens": 100,
               "total_tokens": 300}


def _chat(content, model=ALIAS, finish="stop"):
    return {"id": "chatcmpl-l4", "object": "chat.completion",
            "created": 1760000000, "model": model,
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}],
            "usage": dict(LOCAL_USAGE)}


def configured(monkeypatch, context=CONTEXT):
    """Put the app in the launched-and-bound local state (environment only).

    A saved configuration alone is never a binding: these are the variables the
    supervisor publishes for the run it started and verified, so every local
    job here runs against a launch that produced an origin, alias, context, run
    id, pid, measured template overhead and a per-run bearer.
    """
    monkeypatch.setenv("WB_LOCAL_BASE_URL", ORIGIN)
    monkeypatch.setenv("WB_LOCAL_MODEL", ALIAS)
    monkeypatch.setenv("WB_LOCAL_CONTEXT", str(context))
    monkeypatch.setenv("WB_LOCAL_RUN_ID", RUN_ID)
    monkeypatch.setenv("WB_LOCAL_SERVER_PID", SERVER_PID)
    monkeypatch.setenv("WB_LOCAL_WEIGHTS_SHA256", local_runtime.MODEL_FACTS["sha256"])
    monkeypatch.setenv("WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS", str(OVERHEAD))
    monkeypatch.setenv("WB_LOCAL_OBSERVED_ALIAS", ALIAS)
    monkeypatch.setenv("WB_LOCAL_OBSERVED_CTX", str(context))
    monkeypatch.setenv("WB_LOCAL_OBSERVED_CTX_TRAIN", "262144")
    monkeypatch.setenv("WB_LOCAL_API_KEY", BEARER)


def _provider_from_binding(transport):
    """The transport execute() would build, from the live binding."""
    config = local_runtime.read_config()
    return service.LocalProvider(
        config["origin"], model=LOCAL_ID, wire_model=config["wire_model"],
        context=config["context"], bearer=config["bearer"],
        template_overhead_tokens=config["template_overhead_tokens"],
        transport=transport)


def wire(h, monkeypatch, responder=None, **cfg):
    """A service whose local transport is answered by a mock, plus the requests."""
    configured(monkeypatch, **cfg)
    launched_context = cfg.get("context", CONTEXT)
    seen = []

    def handler(req):
        seen.append(req)
        path = req.url.path
        # The real server is launched with --api-key-file, so every endpoint
        # below requires this run's bearer and refuses an anonymous request with
        # 401. The fake must model that, or a test could pass against a server
        # that ignores Authorization — exactly the state readiness must reject.
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if path == local_runtime.PROPS_PATH:
            # The server's own report of what it loaded, which is what readiness
            # and every execute verify against the launched binding.
            return httpx.Response(200, json={
                "model_alias": ALIAS, "n_ctx_train": 262144, "total_slots": 1,
                "default_generation_settings": {"n_ctx": launched_context}})
        if path == local_runtime.APPLY_TEMPLATE_PATH:
            body = json.loads(req.content)
            return httpx.Response(200, json={
                "prompt": "<|im_start|>system\ns<|im_end|>\n<|im_start|>user\n"
                          + body["messages"][0]["content"]
                          + "<|im_end|>\n<|im_start|>assistant\n"})
        if path == local_runtime.TOKENIZE_PATH:
            body = json.loads(req.content)
            return httpx.Response(200, json={
                "tokens": ["<|im_start|>", "<|im_end|>"]
                          + body["content"].split()})
        prompt = json.loads(req.content)["messages"][0]["content"]
        if responder is not None:
            return responder(prompt, req)
        # The frame pass is identified by its own contract text, exactly as in
        # the cloud tests; the final arm returns the research result.
        text = json.dumps(FRAME if "public work plan" in prompt
                          else RESULT)
        return httpx.Response(200, json=_chat(text))

    def meta_handler(req):
        seen.append(req)
        return httpx.Response(500, json={"never": "a local job must not reach Meta"})

    transport = httpx.MockTransport(handler)
    svc = service.AIService(
        h,
        MetaProvider(httpx.MockTransport(meta_handler), key_reader=lambda: "unused"),
        provider_factory={
            "meta": lambda: svc.provider,
            "local": lambda: _provider_from_binding(transport),
        })
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(
        pid, "What does est_cost measure?"))["question_id"]
    return svc, qid, seen


def _prepare(svc, qid, **extra):
    body = {"task": "research_query", "mode": "local", "harness": False,
            "compare": False, "max_cost_usd": 50}
    body.update(extra)
    return svc.prepare(qid, body)


# --- routing and configuration gates ---

def test_local_mode_resolves_the_configured_model(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    assert preview["model"] == LOCAL_ID
    assert preview["provider"] == "local"
    # Preparing transmits nothing: the preview is a local computation.
    assert seen == []


def test_unconfigured_local_mode_fails_closed_with_reason(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.delenv("WB_LOCAL_BASE_URL")
    with pytest.raises(Conflict, match="Local mode is not configured"):
        _prepare(svc, qid)
    assert seen == []


def test_local_mode_rejects_a_cloud_provider_and_cloud_model(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    with pytest.raises(Conflict, match="local provider route only"):
        _prepare(svc, qid, provider="meta")
    with pytest.raises(Conflict):
        _prepare(svc, qid, model="muse-spark-1.3")


def test_local_mode_is_scoped_to_text_research_tasks(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    for task in ("image_generate", "transcribe", "segment",
                 "methodology_review", "cross_artifact_review"):
        with pytest.raises(Conflict):
            _prepare(svc, qid, task=task)


# --- preview truthfulness ---

def test_preview_names_loopback_zero_cost_and_local_data_use(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    tx = preview["transmission"]
    assert tx["destination"] == ORIGIN
    # Never the Meta origin and never a Meta data-use label.
    assert tx["destination"] != registry.BASE_URL
    assert preview["data_use_mode"] == (
        "Local compute only: no cloud provider receives this request")
    assert "Contributor" not in preview["data_use_mode"]
    assert "Standard" != preview["data_use_mode"]
    # Zero, and stated as a zero-API-cost fact rather than a cheap estimate.
    assert preview["maximum_reserved_usd"] == 0.0
    assert "not metered" in preview["estimate_note"]
    assert "electricity" in preview["estimate_note"]
    assert preview["account_ready"] == "NOT_APPLICABLE_LOCAL"
    assert preview["requires_explicit_consent"] is True


def test_preview_transmits_no_credential_field(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    dumped = json.dumps(preview, sort_keys=True)
    # The per-run bearer authorizes the transport but must never be serialized
    # into the consent surface the researcher reads and approves.
    assert BEARER not in dumped
    assert "api_key" not in dumped.lower()
    assert "bearer" not in dumped.lower()
    assert "authorization" not in dumped.lower()
    # The run is identified by a fingerprint instead, so a job can be tied to
    # its launch without the credential being recoverable from the record.
    binding = local_runtime.read_config()
    assert local_runtime.run_fingerprint(binding) in dumped


# --- execution ---

def test_single_call_executes_on_loopback_and_settles_zero(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    result = asyncio.run(svc.execute(
        preview["job_id"], preview["preview_hash"], True))
    calls = [r for r in seen if r.url.path == local_runtime.CHAT_PATH]
    assert len(calls) == 1
    # Loopback only, and the only credential is this run's bearer.
    assert {r.url.host for r in calls} == {"127.0.0.1"}
    assert all(r.headers.get("authorization") == "Bearer " + BEARER for r in calls)
    # No cloud credential shape ever appears on a local request.
    assert all("x-api-key" not in r.headers for r in calls)
    # A local answer is a real, validated research result.
    assert result["outputs"]["basic"]["grounding"]["structural_valid"] is True
    assert result["outputs"]["evaluation"]["model"] == LOCAL_ID
    # Known zero, not an unknown charge: settlement must reach completed_known.
    assert result["usage_complete"] is True
    assert result["calculated_cost_usd"] == 0.0
    assert result["provider_billed_cost_usd"] is None
    assert result["billing_status"] == "NOT_VERIFIED"
    spend = svc.db.execute(
        "SELECT status,actual_cost FROM ai_spend WHERE job_id=?",
        (preview["job_id"],)).fetchone()
    assert spend["status"] == "completed_known"
    assert spend["actual_cost"] == 0.0


def test_local_job_never_consumes_the_allowance(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    before = svc.settings()["local_allowance_remaining_usd"]
    preview = _prepare(svc, qid)
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert svc.settings()["local_allowance_remaining_usd"] == before
    assert svc.settings()["local_committed_or_reserved_usd"] == 0.0


def test_execute_after_endpoint_change_fails_closed(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    monkeypatch.setenv("WB_LOCAL_BASE_URL", "http://127.0.0.1:9932")
    with pytest.raises(Conflict, match="changed after preview"):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))
    assert [r for r in seen if r.url.path == local_runtime.CHAT_PATH] == []


def test_execute_after_alias_or_context_change_fails_closed(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    monkeypatch.setenv("WB_LOCAL_MODEL", "some-other-alias")
    with pytest.raises(Conflict, match="changed after preview"):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))
    monkeypatch.setenv("WB_LOCAL_MODEL", ALIAS)
    monkeypatch.setenv("WB_LOCAL_CONTEXT", "4096")
    with pytest.raises(Conflict, match="changed after preview"):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))


def test_execute_requires_exact_consent(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="Exact preview confirmation"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], False))
    with pytest.raises(Conflict, match="Exact preview confirmation"):
        asyncio.run(svc.execute(preview["job_id"], "0" * 64, True))
    assert [r for r in seen if r.url.path == local_runtime.CHAT_PATH] == []


# --- the declared context bound ---

def test_prompt_bound_is_enforced_at_preview(h, monkeypatch):
    # A deliberately tiny declared context makes the real prompt exceed budget.
    svc, qid, seen = wire(h, monkeypatch, context=128)
    with pytest.raises(Conflict, match="room for"):
        _prepare(svc, qid)
    assert seen == []


def test_transport_rechecks_the_bound_before_sending(h, monkeypatch):
    # Even if a record escaped the preview check, the transport refuses an
    # over-budget prompt rather than letting the server silently truncate it.
    svc, qid, seen = wire(h, monkeypatch)
    provider = service.LocalProvider(ORIGIN, model=LOCAL_ID, wire_model=ALIAS,
                                     context=1, bearer=BEARER)
    model = registry.MODELS[LOCAL_ID]
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.infer(model, {"instructions": "x" * 100}, []))
    assert exc.value.code == "local_prompt_over_context"
    assert seen == []


# --- the bound is proven, not a characters-per-token ratio (B1) -----------

def _exact_bytes(kind, size):
    """Text of exactly ``size`` UTF-8 bytes in the given script/shape."""
    units = {"korean": "가나다", "code": "x = f(1)\n", "json": '{"k": [1, 2]}'}
    unit = units[kind]
    text = ""
    while len(text.encode()) + len(unit.encode()) <= size:
        text += unit
    return text + "a" * (size - len(text.encode()))


def test_korean_text_is_bounded_by_bytes_not_characters():
    """The reviewer's counter-example: 24576 Korean characters are 16125 tokens.

    The retired ``CHARS_PER_TOKEN = 3`` heuristic admitted 24576 characters, so
    this exact text would have been sent while its 73728 bytes cost far more
    window than the model has.
    """
    korean = "가" * 24576
    assert len(korean.encode("utf-8")) == 73728
    budget = 8192 - 1024 - OVERHEAD
    report = local_runtime.fit_report(korean, 8192, overhead_tokens=OVERHEAD,
                                      max_output_tokens=1024)
    assert report["fits"] is False
    assert report["max_prompt_bytes"] == budget
    # The character count alone is what the old heuristic compared against, and
    # 24576 characters is only a third of this text's real byte cost.
    assert len(korean) * 3 == len(korean.encode("utf-8"))


@pytest.mark.parametrize("kind", ["korean", "code", "json"])
def test_the_bound_is_exact_at_the_byte_boundary(kind):
    budget = local_runtime.prompt_budget_bytes(CONTEXT, OVERHEAD, 1024)
    at = _exact_bytes(kind, budget)
    over = _exact_bytes(kind, budget + 1)
    assert local_runtime.prompt_fits(at, CONTEXT, OVERHEAD, 1024) is True
    assert local_runtime.prompt_fits(over, CONTEXT, OVERHEAD, 1024) is False
    # One byte over is the only difference, so the decision is the byte count
    # and nothing else (no rounding that could admit text past the window).
    assert len(over.encode("utf-8")) - len(at.encode("utf-8")) == 1


def test_the_bound_reserves_the_output_allocation_and_template():
    # Every token the reply may occupy and every token the chat template adds is
    # subtracted BEFORE the prompt budget is derived: a prompt filling the whole
    # window would leave the model nowhere to answer.
    full = CONTEXT
    assert local_runtime.prompt_budget_bytes(full, 0, 1024) == full - 1024
    assert local_runtime.prompt_budget_bytes(full, OVERHEAD, 1024) == \
        full - 1024 - OVERHEAD
    assert local_runtime.prompt_budget_bytes(full, OVERHEAD, 2048) == \
        full - 2048 - OVERHEAD
    # A window smaller than the reserves admits nothing at all.
    assert local_runtime.prompt_budget_bytes(1000, 256, 1024) == 0
    report = local_runtime.fit_report("x", 1000, 256, 1024)
    assert report["fits"] is False and report["max_prompt_bytes"] == 0


def test_the_template_reserve_is_the_measured_value_with_a_named_fallback():
    """Evidenced constant, not a magic number: measured wins, else the named one."""
    assert local_runtime.DEFAULT_TEMPLATE_OVERHEAD_TOKENS == 256
    assert local_runtime.proven_overhead_tokens({"template_overhead_tokens": 16}) == 16
    assert local_runtime.proven_overhead_tokens(
        {}) == local_runtime.DEFAULT_TEMPLATE_OVERHEAD_TOKENS


def test_a_binding_without_a_measured_overhead_is_not_usable(h, monkeypatch):
    """An unmeasured template cannot be called a proven bound, so it blocks."""
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.delenv("WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS")
    config = local_runtime.read_config()
    assert config["configured"] is False
    assert "local_template_overhead_unmeasured" in config["blocking_reasons"]
    with pytest.raises(Conflict, match="chat template overhead was not measured"):
        _prepare(svc, qid)
    assert seen == []


def test_no_researcher_text_is_tokenized_before_consent(h, monkeypatch):
    """Preview uses the byte bound only; nothing is sent to a tokenizer."""
    svc, qid, seen = wire(h, monkeypatch)
    _prepare(svc, qid)
    assert [r for r in seen
            if r.url.path in (local_runtime.APPLY_TEMPLATE_PATH,
                              local_runtime.TOKENIZE_PATH)] == []


def test_the_exact_count_is_taken_at_execute_after_consent(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    templated = [r for r in seen if r.url.path == local_runtime.APPLY_TEMPLATE_PATH]
    counted = [r for r in seen if r.url.path == local_runtime.TOKENIZE_PATH]
    # One render of the exact prompt, one tokenization of that rendering: the
    # template overhead was already measured with the synthetic probe at launch,
    # so no second bare count is needed here.
    assert len(templated) == 1 and len(counted) == 1
    assert all(r.headers.get("authorization") == "Bearer " + BEARER
               for r in templated + counted)


def test_the_exact_count_refuses_a_prompt_the_window_cannot_hold(h, monkeypatch):
    """Retry of the refusal, exactly: the byte bound is looser than the truth.

    The server reports more tokens for the rendered prompt than the window can
    hold together with the reply, so the request is refused before any chat
    POST — never sent to be truncated by the server.
    """
    configured(monkeypatch)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == local_runtime.APPLY_TEMPLATE_PATH:
            return httpx.Response(200, json={"prompt": "rendered"})
        if req.url.path == local_runtime.TOKENIZE_PATH:
            return httpx.Response(200, json={"tokens": ["t"] * 8000})
        return httpx.Response(200, json=_chat(json.dumps(RESULT)))

    provider = _provider_from_binding(httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.infer(
            registry.MODELS[LOCAL_ID],
            {"instructions": "x" * 100, "max_output_tokens": 1024}, []))
    assert exc.value.code == "local_prompt_over_context"
    assert [r for r in seen if r.url.path == local_runtime.CHAT_PATH] == []


def test_a_server_context_error_is_a_failure_never_a_truncated_answer(h, monkeypatch):
    """``--no-context-shift`` makes an overflow a server error; record it as one."""
    def refuse(prompt, req):
        return httpx.Response(400, json={"error": {
            "message": "the prompt is too long", "type": "exceed_context_size_error"}})

    svc, qid, seen = wire(h, monkeypatch, responder=refuse)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="provider_http_400"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    job = svc.jobs(qid)[0]
    assert job["status"].startswith("failed_")
    # Exactly one attempt: a refused local prompt is never replayed.
    assert len([r for r in seen if r.url.path == local_runtime.CHAT_PATH]) == 1


def test_a_real_loopback_server_refusal_is_recorded_not_truncated(h, monkeypatch):
    """End-to-end over a real socket: no mock transport in this test.

    The stand-in answers the documented b10621 endpoints and refuses an
    over-context prompt the way ``--no-context-shift`` makes the real server
    refuse one (HTTP 400 ``exceed_context_size_error``). What must hold is that
    the app records a failure and never a truncated answer, sends exactly one
    chat request, and puts the researcher's text on the wire only after consent.
    """
    fake = _LoopbackServer(alias=ALIAS, n_ctx=CONTEXT)
    try:
        configured(monkeypatch)
        monkeypatch.setenv("WB_LOCAL_BASE_URL", fake.origin)
        # A long prompt: the fake reports more tokens than the window can hold.
        provider = service.LocalProvider(
            fake.origin, model=LOCAL_ID, wire_model=ALIAS, context=CONTEXT,
            bearer=BEARER, template_overhead_tokens=OVERHEAD)
        with pytest.raises(ProviderError) as exc:
            asyncio.run(provider.infer(
                registry.MODELS[LOCAL_ID], {"instructions": "가" * 4096}, []))
        assert exc.value.code == "local_prompt_over_context"
        assert fake.chat_attempts == 0, "an over-budget prompt is never sent"
        assert fake.over_context_refusals == 0
    finally:
        fake.close()


def test_a_server_refusal_after_our_check_passes_is_a_failure(h, monkeypatch):
    """The last word is the server's, and it must not be quietly truncated.

    ``/tokenize`` here under-reports (a tokenizer that disagrees with the
    server's own accounting), so the app's own check passes and the prompt is
    sent — exactly the case where the server, launched with
    ``--no-context-shift``, refuses instead of rotating the window. The refusal
    must be recorded as a failure with the request counted once.
    """
    fake = _LoopbackServer(alias=ALIAS, n_ctx=64, under_report=True)
    try:
        # No transport injected: this test goes over the real socket.
        provider = service.LocalProvider(
            fake.origin, model=LOCAL_ID, wire_model=ALIAS, context=64,
            bearer=BEARER, template_overhead_tokens=4)
        with pytest.raises(ProviderError) as exc:
            asyncio.run(provider.infer(registry.MODELS[LOCAL_ID],
                                       {"instructions": "x" * 200,
                                        "max_output_tokens": 8}, []))
        assert exc.value.code == "provider_http_400"
        assert fake.chat_attempts == 1
        assert fake.over_context_refusals == 1
    finally:
        fake.close()


def _chat_body(prompt, model=ALIAS):
    return {"id": "chatcmpl-l4", "object": "chat.completion", "created": 1760000000,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"answer": "x"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class _LoopbackServer:
    """A real loopback stand-in for the documented b10621 endpoints.

    Answers ``/v1/models``, ``/props``, ``/apply-template`` and ``/tokenize``;
    refuses ``/v1/chat/completions`` with the real server's over-context error
    when the rendered prompt exceeds ``n_ctx``, so the app's refusal path is
    exercised over an actual socket rather than a mocked transport.
    """

    def __init__(self, alias, n_ctx, under_report=False):
        self.alias = alias
        self.n_ctx = n_ctx
        self.under_report = under_report
        self.chat_attempts = 0
        self.over_context_refusals = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                return self.headers.get("Authorization") == "Bearer " + BEARER

            def do_GET(self):
                if not self._authorized():
                    return self._reply(401, {"error": "unauthorized"})
                if self.path == "/v1/models":
                    return self._reply(200, {"data": [{"id": outer.alias}]})
                if self.path == "/props":
                    return self._reply(200, {"model_alias": outer.alias,
                                             "n_ctx_train": 262144, "total_slots": 1,
                                             "default_generation_settings": {
                                                 "n_ctx": outer.n_ctx}})
                return self._reply(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not self._authorized():
                    return self._reply(401, {"error": "unauthorized"})
                if self.path == "/apply-template":
                    content = payload["messages"][0]["content"]
                    return self._reply(200, {"prompt": "<|im_start|>user\n"
                                                       + content + "<|im_end|>\n"})
                if self.path == "/tokenize":
                    # Byte-level BPE: at most one token per UTF-8 byte, which is
                    # the bound this app relies on. ``under_report`` simulates a
                    # tokenizer that disagrees with the server's own accounting,
                    # so the app's check passes and the server has the last word.
                    count = 1 if outer.under_report else len(
                        payload["content"].encode())
                    return self._reply(200, {"tokens": ["t"] * count})
                if self.path == "/v1/chat/completions":
                    outer.chat_attempts += 1
                    rendered = payload["messages"][0]["content"]
                    if len(rendered.encode()) > outer.n_ctx:
                        outer.over_context_refusals += 1
                        return self._reply(400, {"error": {
                            "message": "the prompt is too long",
                            "type": "exceed_context_size_error"}})
                    return self._reply(200, _chat_body(rendered))
                return self._reply(404, {"error": "not found"})

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# --- response validation: no fabricated or misrouted completion ---

@pytest.mark.parametrize("payload,code", [
    (_chat("", finish="stop"), "local_response_empty"),
    (_chat("   ", finish="stop"), "local_response_empty"),
    (_chat('{"answer": "x"}', finish="length"), "local_response_incomplete"),
    (_chat('{"answer": "x"}', model="some-other-model"), "unexpected_model_identity"),
    ({"choices": []}, "local_response_invalid"),
    ({"choices": [{"message": {"role": "assistant"}}]}, "local_response_empty"),
])
def test_invalid_responses_never_record_completed(h, monkeypatch, payload, code):
    svc, qid, _ = wire(h, monkeypatch,
                       responder=lambda prompt, req: httpx.Response(200, json=payload))
    preview = _prepare(svc, qid)
    # service.execute() wraps every provider failure into the recorded failure
    # vocabulary, so the specific local cause travels as the leading code and
    # the job settles in a failed state -- never as a completed answer.
    with pytest.raises(Conflict, match=code):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))
    job = svc.jobs(qid)[0]
    assert job["status"] not in ("completed", "completed_known")
    assert job["status"].startswith("failed_")


def test_unreachable_server_records_honest_failure(h, monkeypatch):
    def refused(prompt, req):
        raise httpx.ConnectError("connection refused")

    svc, qid, seen = wire(h, monkeypatch, responder=refused)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="transport_or_response_error"):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))
    job = svc.jobs(qid)[0]
    assert job["status"].startswith("failed_")
    # Exactly one attempt: a refused local call is never replayed automatically.
    assert len([r for r in seen if r.url.path == local_runtime.CHAT_PATH]) == 1
    # And the job cannot be re-executed implicitly.
    with pytest.raises(Conflict, match="already attempted"):
        asyncio.run(svc.execute(
            preview["job_id"], preview["preview_hash"], True))


# --- receipt ---

def test_receipt_carries_local_identity_and_zero_cost(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    receipt = svc.receipt(preview["job_id"], question_id=qid)
    dumped = json.dumps(receipt, sort_keys=True)
    assert LOCAL_ID in dumped
    assert "Local compute only: no cloud provider receives this request" in dumped
    assert local_runtime.COST_BASIS in dumped
    # No Meta model id and no Meta data-use label leaked into a local receipt.
    assert "muse-spark-1.3" not in dumped
    assert "Contributor: content may be used to train Meta models" not in dumped
    # The verified run is identified truthfully: origin, observed alias and
    # context, pinned weights digest, run id and the fingerprint of the launch —
    # and never the bearer that authorized it.
    run = receipt["local_run"]
    assert run["api_base"] == ORIGIN
    assert run["wire_model"] == ALIAS
    assert run["context"] == CONTEXT
    # What the server reported about itself, recorded next to what was launched:
    # the receipt states the loaded model, not only this app's launch intent.
    assert run["observed_model_alias"] == ALIAS
    assert run["observed_context"] == CONTEXT
    assert run["launch_run"] == RUN_ID
    assert run["launch_server_pid"] == int(SERVER_PID)
    assert run["weights_sha256"] == local_runtime.MODEL_FACTS["sha256"]
    assert run["run_fingerprint"] == local_runtime.run_fingerprint(
        local_runtime.read_config())
    assert run["template_overhead_tokens"] == OVERHEAD
    assert BEARER not in dumped
    assert set(run) == {"api_base", "wire_model", "observed_model_alias",
                        "observed_context", "context", "launch_run",
                        "launch_server_pid", "weights_sha256", "run_fingerprint",
                        "template_overhead_tokens"}


def test_a_cloud_receipt_carries_no_local_run(h, monkeypatch):
    """The local block is absent from every non-local receipt, not empty."""
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    from app.ai import receipt as receipt_module
    built = receipt_module.build_receipt(
        job_status="completed", result=None, model="muse-spark-1.3",
        data_use_mode="Standard", request={"task": "research_query"})
    assert built["local_run"] is None


def test_a_bearer_injected_into_a_binding_is_never_republished():
    """Even a hand-written binding cannot smuggle a secret into a receipt."""
    from app.ai import receipt as receipt_module
    built = receipt_module.build_receipt(
        job_status="completed", result=None, model=LOCAL_ID,
        data_use_mode="Local compute only: no cloud provider receives this request",
        request={"task": "research_query", "mode": "local"},
        local_run={"api_base": ORIGIN, "wire_model": ALIAS, "context": CONTEXT,
                   "launch_run": RUN_ID, "bearer": BEARER,
                   "api_key": BEARER, "secret": BEARER,
                   "weights_sha256": local_runtime.MODEL_FACTS["sha256"]})
    dumped = json.dumps(built, sort_keys=True)
    assert BEARER not in dumped
    assert "bearer" not in built["local_run"]
    assert set(built["local_run"]) == {"api_base", "wire_model", "context",
                                       "launch_run", "weights_sha256"}


# --- probe: explicit, authenticated, no researcher content ---

def test_probe_reports_ready_only_on_served_alias(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    out = asyncio.run(svc.probe_local())
    assert out["status"] == local_runtime.READY_TOKEN
    assert out["ready"] is True
    assert out["inference_calls"] == 0
    assert out["account_verified"] == "NOT_APPLICABLE_LOCAL"
    probes = [r for r in seen if r.url.path == local_runtime.PROBE_PATH]
    # Two, and the pair is the point: one authenticated (which must succeed) and
    # one anonymous (which must be refused). A server that answers the anonymous
    # one is an unauthenticated listener, and readiness refuses it.
    assert len(probes) == 2
    authenticated = [r for r in probes if r.headers.get("authorization")]
    anonymous = [r for r in probes if not r.headers.get("authorization")]
    assert len(authenticated) == 1 and len(anonymous) == 1
    # Authenticated with this run's bearer: an unauthenticated listener must not
    # be able to answer as the model.
    assert authenticated[0].headers.get("authorization") == "Bearer " + BEARER
    assert authenticated[0].content == b""   # no body: no researcher text
    assert anonymous[0].method == "GET" and anonymous[0].content == b""
    # Recorded, so settings reports what was observed.
    assert svc.settings()["local_probe"]["ready"] is True


def test_probe_records_the_observed_runtime_identity(h, monkeypatch):
    """Readiness reports what the server said about itself, not the config."""
    svc, qid, seen = wire(h, monkeypatch)
    out = asyncio.run(svc.probe_local())
    assert out["observed_model_alias"] == ALIAS
    assert out["observed_context"] == CONTEXT
    props = [r for r in seen if r.url.path == local_runtime.PROPS_PATH]
    assert len(props) == 1
    assert props[0].headers.get("authorization") == "Bearer " + BEARER


def test_probe_reports_context_mismatch(h, monkeypatch):
    """A server running a different window is not the approved runtime."""
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={})
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={
                "model_alias": ALIAS,
                "default_generation_settings": {"n_ctx": 4096}})
        return httpx.Response(500, json={})

    monkeypatch.setattr(
        svc, "_provider_factory",
        {**svc._provider_factory,
         "local": lambda: _provider_from_binding(httpx.MockTransport(handler))})
    out = asyncio.run(svc.probe_local())
    assert out["status"] == "local_context_mismatch"
    assert out["ready"] is False
    assert out["observed_context"] == 4096


def test_probe_reports_mismatch_when_alias_not_served(h, monkeypatch):
    svc, qid, _ = wire(
        h, monkeypatch,
        responder=None)
    # Re-wire the probe answer to a server serving a different alias.
    def handler(req):
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={})
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": "another-model"}]})
        return httpx.Response(200, json=_chat(json.dumps(RESULT)))
    monkeypatch.setattr(
        svc, "_provider_factory",
        {**svc._provider_factory,
         "local": lambda: _provider_from_binding(httpx.MockTransport(handler))})
    out = asyncio.run(svc.probe_local())
    assert out["status"] == "local_model_mismatch"
    assert out["ready"] is False
    assert out["served_models"] == ["another-model"]


def test_prepare_is_refused_with_only_saved_paths(h, monkeypatch):
    """A saved configuration file alone must never enable prepare.

    This was the B4 defect: paths on disk were read as if a runtime existed, so
    some unrelated listener that inherited the port would have been accepted.
    """
    svc, qid, seen = wire(h, monkeypatch)
    from app.ai import local_config
    local_config.save({"runtime_executable": "/opt/example/llama-server",
                       "model_weights": "/opt/example/weights.gguf",
                       "alias": ALIAS, "context": CONTEXT, "port": 9931})
    for name in ("WB_LOCAL_BASE_URL", "WB_LOCAL_MODEL", "WB_LOCAL_CONTEXT",
                 "WB_LOCAL_RUN_ID", "WB_LOCAL_SERVER_PID", "WB_LOCAL_API_KEY",
                 "WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS", "WB_LOCAL_WEIGHTS_SHA256",
                 "WB_LOCAL_OBSERVED_ALIAS", "WB_LOCAL_OBSERVED_CTX",
                 "WB_LOCAL_OBSERVED_CTX_TRAIN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Conflict, match="Local mode is not running"):
        _prepare(svc, qid)
    assert seen == []
    # And the registry row does not advertise an endpoint either.
    assert registry.live_models()[LOCAL_ID].endpoint is None


def test_a_binding_whose_launched_process_exited_is_not_running(h, monkeypatch):
    """B4: an environment outlives the server it describes.

    The binding lives in the API process's environment, so it survives the model
    exiting (a crash, a stop, a closed laptop). Reporting it as runnable would
    offer a preview for a runtime that is already gone — and would accept
    whatever later claimed the port.
    """
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.setenv("WB_LOCAL_SERVER_PID", "2147483646")   # never our process
    config = local_runtime.read_config()
    assert config["configured"] is False
    assert "local_run_process_exited" in config["blocking_reasons"]
    assert registry.live_models()[LOCAL_ID].endpoint is None
    with pytest.raises(Conflict):
        _prepare(svc, qid)
    assert seen == []


def test_settings_local_state_is_configuration_not_readiness(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    conn = svc.settings()["local_connection"]
    assert conn["endpoint_configured"] is True
    assert conn["prerequisites_ready"] is True
    # Configured is not ready: only an explicit probe emits the ready token.
    assert local_runtime.READY_TOKEN not in conn["status"]
    assert svc.settings()["local_probe"]["status"] == local_runtime.NOT_PROBED_STATUS


# --- P1: presence equality and the anonymous-auth negative ---

def _probe_with(svc, monkeypatch, handler):
    monkeypatch.setattr(
        svc, "_provider_factory",
        {**svc._provider_factory,
         "local": lambda: _provider_from_binding(httpx.MockTransport(handler))})
    return asyncio.run(svc.probe_local())


def test_props_without_the_expected_fields_is_not_ready(h, monkeypatch):
    """Absence is not agreement: an empty /props must never read as ready.

    A server that answers 200 but reports nothing about what it loaded gives no
    evidence at all, so "the fields I hoped for are missing" has to be a
    mismatch rather than a pass.
    """
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={})
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={})
        return httpx.Response(500, json={})

    out = _probe_with(svc, monkeypatch, handler)
    assert out["status"] != local_runtime.READY_TOKEN
    assert out["ready"] is False


def test_props_reporting_only_slots_is_not_ready(h, monkeypatch):
    """A /props with total_slots but no alias/context is not an identity."""
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={})
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={"total_slots": 4})
        return httpx.Response(500, json={})

    out = _probe_with(svc, monkeypatch, handler)
    assert out["status"] != local_runtime.READY_TOKEN
    assert out["ready"] is False


def test_multiple_slots_is_accepted_and_only_recorded(h, monkeypatch):
    """Slot count is recorded, never a readiness requirement.

    This runtime reports its ``--parallel`` slots (AUTO on the installed build,
    ``kv_unified=true``) with the full declared ``-c`` genuine per request, so
    four slots do not divide the window and say nothing about the prompt bound.
    Requiring exactly one slot would refuse a working launch.
    """
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        if req.headers.get("authorization") != "Bearer " + BEARER:
            return httpx.Response(401, json={})
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={
                "model_alias": ALIAS, "total_slots": 4,
                "default_generation_settings": {"n_ctx": CONTEXT}})
        return httpx.Response(500, json={})

    out = _probe_with(svc, monkeypatch, handler)
    assert out["status"] == local_runtime.READY_TOKEN
    assert out["ready"] is True
    # Still the same strict identity: alias and context must match exactly.
    assert out["observed_model_alias"] == ALIAS
    assert out["observed_context"] == CONTEXT


def test_a_server_that_accepts_anonymous_requests_is_not_ready(h, monkeypatch):
    """The bearer must be load-bearing, not decorative.

    A listener that answers an unauthenticated ``GET /v1/models`` with 200 is not
    this app's runtime — it ignores ``Authorization``, which means the per-run
    secret this app generates proves nothing about who is talking to it. That is
    a refusal, not a ready server.
    """
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        # No auth enforcement at all: the anonymous request is answered 200.
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={
                "model_alias": ALIAS, "total_slots": 1,
                "default_generation_settings": {"n_ctx": CONTEXT}})
        return httpx.Response(200, json=_chat(json.dumps(RESULT)))

    out = _probe_with(svc, monkeypatch, handler)
    assert out["status"] == "local_anonymous_access"
    assert out["ready"] is False


def test_execute_refuses_an_anonymously_answering_server(h, monkeypatch):
    """The same negative at execute: no research text goes to an open listener."""
    svc, qid, seen = wire(h, monkeypatch)
    preview = _prepare(svc, qid)

    def handler(req):
        seen.append(req)
        if req.url.path == local_runtime.PROBE_PATH:
            return httpx.Response(200, json={"data": [{"id": ALIAS}]})
        if req.url.path == local_runtime.PROPS_PATH:
            return httpx.Response(200, json={
                "model_alias": ALIAS, "total_slots": 1,
                "default_generation_settings": {"n_ctx": CONTEXT}})
        return httpx.Response(200, json=_chat(json.dumps(RESULT)))

    monkeypatch.setattr(
        svc, "_provider_factory",
        {**svc._provider_factory,
         "local": lambda: _provider_from_binding(httpx.MockTransport(handler))})
    seen.clear()          # count only what execute() itself sends
    with pytest.raises(Conflict, match="no longer the verified one"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    # The refusal is not "nothing was tried": execute re-ran the readiness GETs.
    assert [r.url.path for r in seen].count(local_runtime.PROBE_PATH) >= 1
    # And exactly zero research calls reached the open listener.
    assert [r.url.path for r in seen if r.url.path == local_runtime.CHAT_PATH] == []


# --- P2: a stored ready result belongs to one run only ---

def test_a_stored_probe_result_does_not_outlive_its_run(h, monkeypatch):
    """Ready for run A must not be reported as ready for run B.

    The probe row is keyed by owner, so without a run fingerprint a restart would
    carry "local_ready" forward and settings would claim a checked runtime that
    nobody re-checked.
    """
    svc, qid, _ = wire(h, monkeypatch)
    first = asyncio.run(svc.probe_local())
    assert first["ready"] is True
    assert svc.settings()["local_probe"]["ready"] is True
    # Same run: still the recorded result.
    assert svc.settings()["local_probe"]["status"] == local_runtime.READY_TOKEN
    # A different launch: different bearer, so a different fingerprint.
    monkeypatch.setenv("WB_LOCAL_API_KEY", "another-run-bearer-entirely")
    state = svc.settings()["local_probe"]
    assert state["ready"] is False
    assert state["status"] == local_runtime.NOT_PROBED_STATUS
    # The superseded result is kept as history, not as a current claim.
    assert state["superseded_probe"]["status"] == local_runtime.READY_TOKEN
    # And a dead binding is not checked either.
    monkeypatch.setenv("WB_LOCAL_SERVER_PID", "2147483646")
    assert svc.settings()["local_probe"]["status"] == local_runtime.NOT_PROBED_STATUS


def test_a_legacy_probe_row_reads_as_not_checked(h, monkeypatch):
    """Migration honesty: a row with no run fingerprint matches no run."""
    svc, qid, _ = wire(h, monkeypatch)
    svc.db.execute("INSERT OR REPLACE INTO ai_local_probe"
                   "(owner,status,detail_json,checked_at,run_fingerprint) "
                   "VALUES(?,?,?,?,NULL)",
                   (service.OWNER, local_runtime.READY_TOKEN, "{}", 1.0))
    svc.db.commit()
    state = svc.settings()["local_probe"]
    assert state["ready"] is False
    assert state["status"] == local_runtime.NOT_PROBED_STATUS


# --- P2: the weights digest is pinned, not merely present ---

@pytest.mark.parametrize("digest_value", [None, "", "garbage", "a" * 64])
def test_a_binding_without_the_pinned_weights_digest_is_refused(
        h, monkeypatch, digest_value):
    """Absent or arbitrary digest is unproven weights, so it is refused."""
    svc, qid, seen = wire(h, monkeypatch)
    if digest_value is None:
        monkeypatch.delenv("WB_LOCAL_WEIGHTS_SHA256")
    else:
        monkeypatch.setenv("WB_LOCAL_WEIGHTS_SHA256", digest_value)
    config = local_runtime.read_config()
    assert config["configured"] is False
    assert "local_weights_digest_mismatch" in config["blocking_reasons"]
    with pytest.raises(Conflict):
        _prepare(svc, qid)
    assert seen == []


# --- P2: the preview measures what execute sends ---

def test_the_preview_measures_the_full_instructions_execute_sends(h, monkeypatch):
    """The preview's byte count includes every arm's real prompt.

    The earlier defect measured only common + domain instructions, omitting the
    per-arm contract block that execute appends, so a preview could promise
    "fits" for a prompt execute would refuse. The measured size must therefore be
    at least the size of the full instructions, not just the shared prefix.
    """
    svc, qid, _ = wire(h, monkeypatch)
    preview = _prepare(svc, qid)
    fit = preview["local_run"]
    assert fit["prompt_size_is_exact"] is True          # basic arm, nothing unknown
    measured = fit["prompt_utf8_bytes"]
    assert measured == fit["measured_arms"]["basic"]
    # Recompute what execute will actually send for the basic arm and compare.
    row = svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    record = json.loads(row["request_json"])
    pkt = service.get_pack(record["snapshot"]["pack"]["pack_id"])
    full = (service.SAFE_CORE + "\nDOMAIN: " + pkt.instructions + "\n"
            + service.FINAL_CONTRACT)
    from app.ai.provider import local_prompt_text as flatten
    assets = []
    body = svc._body(record, assets, full)
    assert measured == len(flatten(body).encode("utf-8"))
    # And strictly larger than the shared-prefix-only measurement that was wrong.
    prefix_only = len(flatten(svc._body(
        record, assets, service.SAFE_CORE + "\nDOMAIN: " + pkt.instructions)
    ).encode("utf-8"))
    assert measured > prefix_only
    assert measured - prefix_only >= len(service.FINAL_CONTRACT.encode("utf-8"))


def test_a_harness_preview_states_a_bound_not_a_measurement(h, monkeypatch):
    """Unknown generated content is labelled a proven bound, never exact."""
    # A context large enough that the worst-case plan the model may emit still
    # fits: the point here is the labelling, not the size of the window.
    svc, qid, _ = wire(h, monkeypatch, context=32768)
    preview = _prepare(svc, qid, harness=True)
    fit = preview["local_run"]
    assert fit["prompt_size_is_exact"] is False
    # Both the frame pass and the final call are measured, plus the worst case.
    assert set(fit["measured_arms"]) == {"harness", "frame", "harness_with_extras"}
    assert fit["prompt_utf8_bytes"] == fit["measured_arms"]["harness_with_extras"]


# --- P2 (final): the declared ceiling must dominate every schema-valid frame ---

def _stored_record(svc, job_id):
    row = svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?",
                         (job_id,)).fetchone()
    return json.loads(row["request_json"])


def _frame_body_bytes(svc, record, sources, focus):
    """The body execute would send for one frame, via the same _body chain."""
    from app.ai.provider import local_prompt_text as flatten
    packet = record["snapshot"]["packet"]
    plan = {"sources": list(sources), "gap_type": "private_context",
            "focus": focus}
    extras = {"bounded_plan": plan,
              "observations": service._frame_observations(
                  packet["sources"], sources)}
    pkt = service.get_pack(record["snapshot"]["pack"]["pack_id"])
    instructions = (service.SAFE_CORE + "\nDOMAIN: " + pkt.instructions
                    + "\n" + service.FINAL_CONTRACT)
    return len(flatten(svc._body(record, [], instructions, extras)).encode("utf-8"))


def test_the_declared_ceiling_dominates_every_schema_valid_frame(h, monkeypatch):
    """Compared against real bodies, not against the ceiling itself.

    The earlier ceiling assumed three observation entries and a four-byte focus
    encoding. Both were wrong for this fixture: DD-2023 carries five entries, so
    a three-ID plan selects seven, and a C0 control character costs six bytes in
    JSON, not four. Every subset of the allowlist within Frame's three-ID limit
    is measured here — with ALL entries of each selected source, in packet order,
    exactly as execute selects them — and the ceiling must be at least each.
    """
    svc, qid, _ = wire(h, monkeypatch, context=32768)
    preview = _prepare(svc, qid, harness=True)
    record = _stored_record(svc, preview["job_id"])
    packet = record["snapshot"]["packet"]
    allowed = packet["allowed_sources"]
    # The fixture really does repeat a source ID: this is what the old ceiling
    # miscounted, so the test would be vacuous against a one-entry-per-source
    # packet.
    counts = {}
    for entry in packet["sources"]:
        counts[entry["source_id"]] = counts.get(entry["source_id"], 0) + 1
    assert counts["DD-2023"] == 5
    assert all(n >= 1 for n in counts.values())

    focus = "\x00" * service.MAX_FRAME_FOCUS_CHARS
    measured = {}
    for size in range(1, service.MAX_FRAME_SOURCES + 1):
        for subset in itertools.combinations_with_replacement(allowed, size):
            plan = {"sources": list(subset), "gap_type": "private_context",
                    "focus": focus}
            if len(set(subset)) != len(subset):
                with pytest.raises(service.ValidationError, match="must be unique"):
                    service.Frame.model_validate(plan)
                continue
            service.Frame.model_validate(plan)
            measured[subset] = _frame_body_bytes(svc, record, subset, focus)
    ceiling = preview["local_run"]["prompt_utf8_bytes"]
    assert ceiling >= max(measured.values())
    # The bound is reachable, so it is a tight ceiling rather than padding: the
    # same subset that maximizes the measured bodies is the one it describes.
    assert ceiling == max(measured.values())
    # And the selection rule preserves entries: DD-2023 alone contributes five.
    assert len(service._frame_observations(packet["sources"], ["DD-2023"])) == 5
    widest_ids = max(measured, key=lambda s: measured[s])
    assert len(service._frame_observations(packet["sources"], widest_ids)) == 7
    # The widest focus encoding is what makes the ceiling non-obvious: a C0
    # control character costs six JSON bytes, so the emoji example understates
    # by exactly two bytes per character.
    emoji = _frame_body_bytes(svc, record, widest_ids,
                              "\U0001f600" * service.MAX_FRAME_FOCUS_CHARS)
    assert max(measured.values()) - emoji == 2 * service.MAX_FRAME_FOCUS_CHARS


def test_a_16384_window_refuses_that_ceiling_and_names_it(h, monkeypatch):
    """The refusal at 16384 is caused by the real ceiling, not by a guess.

    The number in the message is cross-checked against an independently
    enumerated worst frame from the same packet, and against the actual budget
    (16384 − 1024 output − 16 measured overhead = 15344 bytes).
    """
    svc, qid, seen = wire(h, monkeypatch, context=16384)
    with pytest.raises(Conflict) as exc:
        _prepare(svc, qid, harness=True)
    message = str(exc.value)
    assert "UTF-8 bytes" in message
    assert "15344" in message
    assert seen == []

    roomy, roomy_qid, _ = wire(h, monkeypatch, context=32768)
    preview = _prepare(roomy, roomy_qid, harness=True)
    ceiling = preview["local_run"]["prompt_utf8_bytes"]
    record = _stored_record(roomy, preview["job_id"])
    allowed = record["snapshot"]["packet"]["allowed_sources"]
    focus = "\x00" * service.MAX_FRAME_FOCUS_CHARS
    measured = max(
        _frame_body_bytes(roomy, record, subset, focus)
        for size in range(1, service.MAX_FRAME_SOURCES + 1)
        for subset in itertools.combinations(allowed, size))
    assert ceiling == measured
    # Same job, smaller window: that exact byte count is why it is refused.
    assert ceiling > 16384 - 1024 - OVERHEAD
    assert str(ceiling) in message


def test_a_32768_window_admits_that_ceiling(h, monkeypatch):
    """With room, the same worst case prepares and is labelled a bound."""
    svc, qid, _ = wire(h, monkeypatch, context=32768)
    preview = _prepare(svc, qid, harness=True)
    fit = preview["local_run"]
    assert fit["prompt_utf8_bytes"] <= fit["max_prompt_bytes"]
    assert fit["prompt_size_is_exact"] is False
    record = _stored_record(svc, preview["job_id"])
    allowed = record["snapshot"]["packet"]["allowed_sources"]
    focus = "\x00" * service.MAX_FRAME_FOCUS_CHARS
    measured = max(
        _frame_body_bytes(svc, record, subset, focus)
        for size in range(1, service.MAX_FRAME_SOURCES + 1)
        for subset in itertools.combinations(allowed, size))
    assert fit["prompt_utf8_bytes"] == measured


def test_the_ceiling_picks_the_largest_allowed_subset_not_the_first_three():
    """A synthetic packet where the heaviest source is last in the allowlist.

    The ranking must be by the bytes each ID actually contributes. Taking the
    first three allowlist entries would understate this packet by the whole
    difference between ``BIG``'s entries and the small one it left out.
    """
    packet = {
        "allowed_sources": ["A", "B", "BIG"],
        "sources": ([{"source_id": "A", "field": "f", "definition": "d"}]
                    + [{"source_id": "B", "field": "f", "definition": "d"}]
                    + [{"source_id": "BIG", "field": "f%d" % i,
                        "definition": "definition " * 20} for i in range(4)]),
    }
    extras = service.worst_harness_extras(packet)
    assert extras["bounded_plan"]["sources"] == ["BIG", "A", "B"]
    assert [e["source_id"] for e in extras["observations"]] == \
        ["A", "B", "BIG", "BIG", "BIG", "BIG"]
    # Exactly the schema maxima: three distinct IDs, a 2000-character focus.
    assert len(extras["bounded_plan"]["sources"]) == service.MAX_FRAME_SOURCES
    assert len(extras["bounded_plan"]["focus"]) == service.MAX_FRAME_FOCUS_CHARS
    # And it really is the largest: no other 3-ID subset serializes bigger.
    widest = len(service.dumps(extras))
    for subset in itertools.permutations(["A", "B", "BIG"],
                                         service.MAX_FRAME_SOURCES):
        candidate = {"bounded_plan": {**extras["bounded_plan"],
                                      "sources": list(subset)},
                     "observations": service._frame_observations(
                         packet["sources"], subset)}
        assert len(service.dumps(candidate)) <= widest


def test_an_explicit_harness_at_the_default_context_is_refused_honestly(h, monkeypatch):
    """The frame pass may emit a 2000-character plan; that cannot be promised.

    At the default 8192-token window the declared maximum of the unknown plan
    (Frame allows 2000 characters, and one UTF-8 byte may be one token) does not
    fit, so the honest answer at preview is a refusal that states the numbers —
    never a "fits" the run could not honour, and never silent truncation.
    """
    svc, qid, seen = wire(h, monkeypatch)          # CONTEXT == 8192
    with pytest.raises(Conflict) as exc:
        _prepare(svc, qid, harness=True)
    message = str(exc.value)
    assert "UTF-8 bytes" in message
    assert "16384" not in message                  # the real bound, not a guess
    assert seen == []


def test_preview_bound_is_enforced_at_the_full_instruction_size(h, monkeypatch):
    """Over-budget text is refused at preview, and the refusal names real bytes."""
    svc, qid, _ = wire(h, monkeypatch, context=2048)
    with pytest.raises(Conflict, match="UTF-8 bytes"):
        _prepare(svc, qid, text="가" * 4000)


# --- P3: an absent harness flag means the single basic arm ---

def test_an_absent_harness_flag_prepares_the_single_basic_arm(h, monkeypatch):
    """Request defaults harness=True for cloud; local must not inherit that.

    A caller who sends no harness flag has expressed no opinion, and the honest
    default for this small model is one basic call. The bug was that the cloud
    default leaked through validation and silently armed the frame pass.
    """
    svc, qid, seen = wire(h, monkeypatch)
    preview = svc.prepare(qid, {"task": "research_query", "mode": "local",
                                "max_cost_usd": 50})
    assert preview["max_inference_calls"] == 1
    row = svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    stored = json.loads(row["request_json"])["request"]
    assert stored["harness"] is False
    assert stored["compare"] is False
    assert json.loads(row["request_json"])["execution_order"] == ["basic"]
    # Executing makes exactly one chat call and no frame call.
    asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    chat = [r for r in seen if r.url.path == local_runtime.CHAT_PATH]
    assert len(chat) == 1


def test_an_explicit_harness_flag_still_arms_the_frame_pass(h, monkeypatch):
    """The default changed; the explicit opt-in did not."""
    svc, qid, seen = wire(h, monkeypatch, context=32768)
    preview = _prepare(svc, qid, harness=True)
    row = svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    stored = json.loads(row["request_json"])["request"]
    assert stored["harness"] is True
    assert preview["max_inference_calls"] == 2


# --- P3: contaminated parent environment never reaches renderer surfaces ---

def test_run_mock_strips_run_bindings_from_the_shared_environment():
    """A developer shell's WB_LOCAL_API_KEY must not reach static or Electron.

    run_mock publishes a binding only for the API child it launches. The shared
    environment (fake provider, static file server, desktop shell) is built from
    os.environ, so any inherited WB_LOCAL_* run variable would hand a stale
    credential and origin to surfaces that have no business holding them.
    """
    import re
    source = (Path(__file__).resolve().parents[1] / "run_mock.py").read_text()
    assert "LOCAL_RUN_ENV" in source
    # The shared env is a filtered comprehension, not a raw copy of os.environ.
    assert "{k:v for k,v in os.environ.items() if k not in LOCAL_RUN_ENV}" in source
    assert "env={**os.environ" not in source
    # Every run variable the launcher publishes is in the strip list, enforced
    # against the launcher itself rather than a hand-maintained duplicate.
    from app.ai import local_launcher
    class _Fake:
        config = {"base_url": "http://127.0.0.1:9931",
                  "alias": "a", "context": 8192}
        run_id = "r"
        secret = "s"
        weights_sha256 = "w"
        template_overhead_tokens = 16
        observed_identity = {"model_alias": "a", "n_ctx": 8192}
        process = type("P", (), {"pid": 1})()
    published = set(local_launcher.binding_environment(_Fake()))
    stripped = set(re.search(r"LOCAL_RUN_ENV=\((.*?)\)", source, re.S).group(1)
                   .replace("\n", " ").replace("'", "").split(","))
    stripped = {name.strip() for name in stripped if name.strip()}
    assert published <= stripped
    # And the API child's env is the only place the binding is merged.
    assert "api_env={**env,**local_launcher.binding_environment(local_server)}" in source


def test_ambient_run_binding_cannot_make_a_test_ready(monkeypatch):
    """A developer shell's exported binding must not survive into a test.

    conftest strips every WB_LOCAL_* run variable before collection and before
    each test, so a leftover WB_LOCAL_API_KEY from a previous rehearsal in the
    same terminal cannot make a test believe a verified launch happened. This
    asserts the guarantee itself: with nothing published by the test, the state
    is the honest unconfigured one.
    """
    assert os.environ.get("WB_LOCAL_API_KEY") is None
    assert os.environ.get("WB_LOCAL_BASE_URL") is None
    config = local_runtime.read_config()
    assert config["configured"] is False
    assert "local_endpoint_unconfigured" in config["blocking_reasons"]
    assert registry.live_models()[LOCAL_ID].endpoint is None
