"""Conversation must never take the question-creation path."""
import asyncio
import json
import pytest

from app import secretary_chat
from test_secretary_log import h, seed
from app.ai.service import AIService
from app.harness import Conflict


@pytest.mark.parametrize("paid", [False, True])
def test_history_keeps_latest_long_exchange_whole_and_drops_oldest_to_fit(h, monkeypatch, paid):
    from app import secretary_log
    from app.ai.provider import local_prompt_text
    pid = seed(h)
    for i in range(3):
        for role in ("researcher", "secretary"):
            secretary_log.record_turn(h, pid, {"role": role, "body": {"text": "obsolete " * 600}})
    last_question = "What are the limitations?"
    last_reply = "Background explanation. " * 45 + "First limitation: permit coverage is incomplete."
    for role, text in (("researcher", last_question), ("secretary", last_reply)):
        secretary_log.record_turn(h, pid, {"role": role, "body": {"text": text}})
    config = {**secretary_chat.local_runtime.read_config(),
              "configured": True, "context": 8192, "template_overhead_tokens": 256}
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: config)
    monkeypatch.setattr(secretary_chat.local_runtime, "run_fingerprint", lambda config: "run")
    monkeypatch.setattr(h, "briefing", lambda pid: {"text": "large briefing " * 3000})
    requests = []
    class Model:
        async def infer(self, model, body, assets):
            requests.append(body)
            return {"usage": {"input_tokens": 100, "output_tokens": 20}, "output": []}, .1
    ai = None
    if paid:
        ai = AIService(h)
        ai.configure(True, 40)
        monkeypatch.setattr(ai, "_assert_catalog", lambda *args: None)
        authorize(ai, pid)
        monkeypatch.setattr(ai, "_provider_for", lambda provider: Model())
    asyncio.run(secretary_chat.talk(h, pid, {"text": "Explain the first limitation you just mentioned."},
                                     "run", Model, ai=ai))
    assert len(requests) == 1
    transmitted = requests[0]["input"][0]["content"][0]["text"]
    context = json.loads(transmitted.split("\nCurrent message: ")[0])
    assert context["recent_turns"] == [
        {"role": "researcher", "text": last_question}, {"role": "secretary", "text": last_reply}]
    assert "obsolete" not in transmitted
    if paid:
        assert len(json.dumps(requests[0], ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
    else:
        assert len(local_prompt_text(requests[0]).encode()) + 256 + 700 + 128 <= 8192


def authorize(ai, pid):
    option = secretary_chat.settings(ai, pid, issue=True)["options"][0]
    body = {"provider": "baseten", "project_id": pid, "consent": True,
            "consent_nonce": option["consent_nonce"]}
    secretary_chat.configure(ai, body)
    return body


def test_paid_consent_cannot_be_replayed_or_used_for_another_project(h, monkeypatch):
    ai = AIService(h)
    ai.configure(True, 40)
    monkeypatch.setattr(ai, "_assert_catalog", lambda *args: None)
    pid = seed(h)
    other = seed(h)
    confirmation = authorize(ai, pid)
    with pytest.raises(Conflict):
        secretary_chat.configure(ai, confirmation)
    with pytest.raises(Conflict):
        asyncio.run(secretary_chat.paid_infer(ai, other, {"input": "do not send"}))
    secretary_chat.configure(ai, {"provider": "local"})
    with pytest.raises(Conflict):
        secretary_chat.configure(ai, confirmation)
    option = secretary_chat.settings(ai, pid, issue=True)["options"][0]
    with pytest.raises(Conflict):
        secretary_chat.configure(ai, {"provider": "baseten", "project_id": other,
                                      "consent": True, "consent_nonce": option["consent_nonce"]})
    assert secretary_chat.settings(ai, other)["selected"] == "local"


@pytest.mark.parametrize("envelope", [{"model": "wrong-model", "status": "completed"},
                                     {"status": "incomplete"}])
def test_paid_rejected_envelope_is_not_shown_but_cost_is_settled(h, monkeypatch, envelope):
    ai = AIService(h)
    ai.configure(True, 40)
    monkeypatch.setattr(ai, "_assert_catalog", lambda *args: None)
    pid = seed(h)
    authorize(ai, pid)
    class Paid:
        async def infer(self, model, body, assets):
            return {**envelope, "usage": {"input_tokens": 100, "output_tokens": 20},
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "Do not show this answer"}]}]}, .1
    monkeypatch.setattr(ai, "_provider_for", lambda provider: Paid())
    result = asyncio.run(secretary_chat.talk(h, pid, {"text": "hello"}, None, ai=ai))
    assert result["text"] == secretary_chat.UNAVAILABLE
    assert result["cost_usd"] == .000210
    row = ai.db.execute("SELECT * FROM ai_spend WHERE job_id=?", (result["receipt_id"],)).fetchone()
    assert row["status"] == "failed_known_charge"


def test_paid_request_bound_controls_reservation_and_refuses_oversize(h, monkeypatch):
    ai = AIService(h)
    ai.configure(True, 40)
    monkeypatch.setattr(ai, "_assert_catalog", lambda *args: None)
    pid = seed(h)
    authorize(ai, pid)
    calls = []
    class Paid:
        async def infer(self, model, body, assets):
            calls.append(body)
            return {"usage": {"input_tokens": 100, "output_tokens": 20},
                    "output": []}, .1
    monkeypatch.setattr(ai, "_provider_for", lambda provider: Paid())
    result = asyncio.run(secretary_chat.paid_infer(ai, pid, {"input": "hello"}))
    assert result["receipt"]["reservation_usd"] == (
        secretary_chat.MAX_PAID_INPUT_TOKENS * 1.25 + 700 * 4.25) / 1e6
    assert result["receipt"]["reservation_usd"] < .05
    with pytest.raises(Conflict):
        asyncio.run(secretary_chat.paid_infer(ai, pid, {"input": "한" * secretary_chat.MAX_PAID_REQUEST_BYTES}))
    assert len(calls) == 1


def test_incomplete_local_reply_is_not_reported_as_model_off(h, monkeypatch, caplog):
    from app.ai.provider import LocalProvider
    from types import SimpleNamespace
    pid = seed(h)
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: {"configured": True})
    monkeypatch.setattr(secretary_chat.local_runtime, "run_fingerprint", lambda config: "run")
    class Local:
        async def infer(self, model, body, assets):
            assert body["max_output_tokens"] == 700
            return LocalProvider._normalize(SimpleNamespace(wire_model="qwen"), {
                "choices": [{"finish_reason": "length", "message": {"content": "잘린 답변"}}]
            }, model.id), .1
    reply = asyncio.run(secretary_chat.talk(h, pid, {"text": "질문", "language": "ko"}, "run", Local))
    assert reply["text"] == secretary_chat.UNFINISHED["ko"]
    assert reply["text"] != secretary_chat.OFF["ko"]
    assert "local_response_incomplete" in caplog.text


@pytest.mark.parametrize("failure,expected", [("refused", secretary_chat.OFF["en"]),
                                             ("http", secretary_chat.UNFINISHED["en"])])
def test_local_transport_errors_distinguish_off_from_failed_reply(h, monkeypatch, failure, expected):
    import httpx
    from app.ai.provider import LocalProvider
    pid = seed(h)
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: {"configured": True})
    monkeypatch.setattr(secretary_chat.local_runtime, "run_fingerprint", lambda config: "run")
    def respond(request):
        if failure == "refused":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(500, json={})
    def provider():
        return LocalProvider("http://127.0.0.1:1", bearer="test-only",
                             wire_model="qwen", transport=httpx.MockTransport(respond))
    reply = asyncio.run(secretary_chat.talk(h, pid, {"text": "hello"}, "run", provider))
    assert reply["text"] == expected


def test_paid_settings_consent_revoke_cost_receipt_limit_and_unavailable(h, monkeypatch):
    ai = AIService(h)
    ai.configure(True, 40)
    monkeypatch.setattr(ai, "_assert_catalog", lambda provider, model: None)
    def not_ready():
        raise Conflict("not ready")
    monkeypatch.setattr(ai, "_assert_modal_shared_ready", not_ready)
    assert secretary_chat.settings(ai)["selected"] == "local"
    pid = seed(h)
    options = secretary_chat.settings(ai, pid, issue=True)["options"]
    assert [o["provider"] for o in options] == ["baseten"]
    with pytest.raises(Conflict):
        secretary_chat.configure(ai, {"provider": "baseten"})
    option = options[0]
    authorization = {"provider": "baseten", "project_id": pid, "consent": True, "consent_nonce": option["consent_nonce"]}
    secretary_chat.configure(ai, authorization)
    assert secretary_chat.settings(ai, pid)["selected"] == "baseten"
    requests = []

    class Paid:
        async def infer(self, model, body, assets):
            requests.append(body)
            return {"usage": {"input_tokens": 100, "output_tokens": 20},
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "A short suggestion."}]}]}, .1

    monkeypatch.setattr(ai, "_provider_for", lambda provider: Paid())
    reply = asyncio.run(secretary_chat.talk(h, pid, {"text": "hello"}, None, ai=ai))
    assert reply["text"] == "A short suggestion."
    assert reply["model"] == secretary_chat.PAID["baseten"]
    assert reply["cost_usd"] == .000210
    assert reply["receipt"]["provider_billed_cost_usd"] is None
    spend = h.db.execute("SELECT * FROM ai_spend WHERE job_id=?", (reply["receipt_id"],)).fetchone()
    assert spend["status"] == "completed_known"
    assert spend["actual_cost"] == .000210
    assert h.db.execute("SELECT count(*) FROM secretary_chat_receipts").fetchone()[0] == 1
    secretary_chat.configure(ai, {"provider": "local"})
    with pytest.raises(Conflict):
        secretary_chat.configure(ai, authorization)
    authorize(ai, pid)
    h.db.execute("INSERT INTO ai_spend VALUES('other',40,'running',NULL)")
    h.db.commit()
    blocked = asyncio.run(secretary_chat.talk(h, pid, {"text": "more"}, None, ai=ai))
    assert blocked["text"] == secretary_chat.LIMIT
    assert len(requests) == 1
    monkeypatch.setattr(ai, "_assert_catalog", lambda *args: not_ready())
    unavailable = asyncio.run(secretary_chat.talk(h, pid, {"text": "hello"}, None, ai=ai))
    assert unavailable["text"] == secretary_chat.UNAVAILABLE
    assert len(requests) == 1


def test_chat_records_two_turns_without_creating_question(h, monkeypatch):
    pid = seed(h)
    before = h.db.execute("SELECT count(*) FROM questions").fetchone()[0]
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: {"configured": True})
    monkeypatch.setattr(secretary_chat.local_runtime, "run_fingerprint", lambda c: "run")
    requests = []

    class Local:
        async def infer(self, model, body, assets):
            requests.append(body)
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Hello. How can I help?"}]}]}, 0.1

    reply = asyncio.run(secretary_chat.talk(h, pid, {"text": "hello", "language": "en"},
                                            "run", Local))
    assert reply["text"] == "Hello. How can I help?"
    assert h.db.execute("SELECT count(*) FROM questions").fetchone()[0] == before
    assert h.db.execute("SELECT count(*) FROM secretary_turns WHERE project_id=?", (pid,)).fetchone()[0] == 2
    asyncio.run(secretary_chat.talk(h, pid, {"text": "What did I just say?", "language": "en"}, "run", Local))
    assert "hello" in json.dumps(requests[-1])
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: {"configured": False})
    off = asyncio.run(secretary_chat.talk(h, pid, {"text": "안녕", "language": "ko"}, None, Local))
    assert off["text"] == secretary_chat.OFF["ko"]
    assert len(requests) == 2


def test_chat_turns_survive_the_pack_filtered_history_reload(h, monkeypatch):
    pid = seed(h)
    monkeypatch.setattr(secretary_chat.local_runtime, "read_config", lambda: {"configured": True})
    monkeypatch.setattr(secretary_chat.local_runtime, "run_fingerprint", lambda c: "run")

    class Local:
        async def infer(self, model, body, assets):
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Hello."}]}]}, 0.1

    asyncio.run(secretary_chat.talk(h, pid, {"text": "hello", "language": "en", "pack_id": "social_science"},
                                    "run", Local))
    reloaded = secretary_chat.secretary_log.list_turns(h, pid, pack_id="social_science")["turns"]
    assert sorted((t["role"], t["body"]["text"]) for t in reloaded) == [("researcher", "hello"), ("secretary", "Hello.")]


def test_project_descriptor_contains_registered_permit_context_and_is_bounded(h, monkeypatch):
    pid = seed(h)
    monkeypatch.setattr(h, "dataset_columns", lambda kind: {
        "permits.csv": ["est_cost"] + ["column" * 100] * 100,
        **{str(i): ["extra"] for i in range(20)}})
    monkeypatch.setattr(h, "sources", lambda kind: {"entries": [
        {"source_id": "DD-2023", "field": "est_cost",
         "definition": "Applicant-reported estimated construction cost."},
        *[{"source_id": "long" * 100, "field": "x" * 100, "definition": "y" * 1000}] * 100]})
    descriptor = secretary_chat.project_descriptor(h, pid)
    assert descriptor["pack"]["id"]
    assert descriptor["datasets"][0]["id"] == "permits.csv"
    assert descriptor["datasets"][0]["columns"][0] == "est_cost"
    assert descriptor["variable_definitions"][0] == {
        "id": "DD-2023:est_cost", "meaning": "Applicant-reported estimated construction cost."}
    assert len(json.dumps(descriptor)) < 8000
    assert len(descriptor["datasets"]) == 4
    assert len(descriptor["datasets"][0]["columns"]) == 24
    assert len(descriptor["variable_definitions"]) == 16


def test_conversation_instructions_use_only_ui_language_examples():
    korean = secretary_chat.instructions_for("ko")
    english = secretary_chat.instructions_for("en")
    assert "~로 보입니다" in korean
    assert "답변 전체를 한국어로" in korean
    assert "it seems" not in korean
    assert "Suggestion:" not in korean
    assert "it seems" in english
    assert "entire reply in English" in english
    assert "~로 보입니다" not in english
