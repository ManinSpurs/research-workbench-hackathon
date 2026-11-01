"""Voice confirm wiring (mock transports only, no network/keys).

Exercises POST /questions/{qid}/ai/voice-confirm per VOICE_QUESTION_CAPTURE_EN
section 4/7: the explicit confirmation gate that turns a completed
transcription into a question via the existing create_question path.
"""
import asyncio
import base64
import io
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import httpx

from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.api import confirm_voice_question, create_app
from app.harness import Conflict, Harness
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)


@pytest.fixture
def voice(h, monkeypatch):
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]},
            )
        if "/asr/transcribe" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "transcript": "Raw unconfirmed transcript",
                    "audioDurationMs": 1000,
                    "turns": [
                        {
                            "startMs": 0,
                            "endMs": 1000,
                            "speaker": "A",
                            "transcript": "Raw unconfirmed transcript",
                        }
                    ],
                },
            )
        raise AssertionError("unexpected provider call: " + req.url.path)

    svc = service.AIService(h, MetaProvider(httpx.MockTransport(handler), key_reader=lambda: "test"))
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "Voice memo shell"))["question_id"]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    asset = svc.register_asset(
        qid,
        {"name": "interview.wav", "mime": "audio/wav", "encoded": base64.b64encode(buf.getvalue()).decode()},
    )
    return h, svc, pid, qid, asset


def ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def prepare_and_execute(svc, qid, asset):
    prep = svc.prepare(qid, {"task": "transcribe", "assets": [asset["id"]]})
    return asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))


def http_confirm(h, qid, payload):
    from fastapi.testclient import TestClient

    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as client:
        return client.post(f"/questions/{qid}/ai/voice-confirm", json=payload)


def test_happy_path_uses_confirmed_text_and_linkage(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    resp = http_confirm(
        h, qid, {"confirmed_text": "Edited memo question", "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["original_text"] == "Edited memo question"
    assert body["source_asset_id"] == asset["id"]
    assert body["source_sha256"] == asset["sha256"]
    created = h.question(body["question_id"])
    assert created["original_text"] == "Edited memo question"
    assert "Raw unconfirmed transcript" not in created["original_text"]
    events = h.db.execute(
        "SELECT * FROM events WHERE question_id=? AND kind='voice_question_confirmed'", (body["question_id"],)
    ).fetchall()
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])
    assert payload == {
        "confirmed_text": "Edited memo question",
        "source_asset_id": asset["id"],
        "source_sha256": asset["sha256"],
    }


def test_confirm_matches_verbatim_transcript(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    resp = http_confirm(
        h, qid, {"confirmed_text": "Raw unconfirmed transcript", "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["original_text"] == "Raw unconfirmed transcript"


def test_unconfirmed_transcript_creates_no_question(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    before = h.db.execute("SELECT count(*) FROM questions").fetchone()[0]
    assert h.db.execute("SELECT count(*) FROM questions").fetchone()[0] == before
    assert h.db.execute("SELECT count(*) FROM events WHERE kind='voice_question_confirmed'").fetchone()[0] == 0


def test_sha_mismatch_rejected(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    before = h.db.execute("SELECT count(*) FROM questions").fetchone()[0]
    resp = http_confirm(
        h, qid, {"confirmed_text": "Edited memo", "source_asset_id": asset["id"], "source_sha256": "0" * 64}
    )
    assert resp.status_code == 409
    assert h.db.execute("SELECT count(*) FROM questions").fetchone()[0] == before


def test_other_question_asset_rejected(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    other = asyncio.run(h.create_question(pid, "Other question"))["question_id"]
    resp = http_confirm(
        h, other, {"confirmed_text": "Edited memo", "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
    )
    assert resp.status_code == 409


def test_failed_transcription_rejected(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    svc.provider = MetaProvider(
        httpx.MockTransport(lambda req: httpx.Response(429, json={"error": "busy"})), key_reader=lambda: "test"
    )
    prep = svc.prepare(qid, {"task": "transcribe", "assets": [asset["id"]]})
    with pytest.raises(Conflict):
        asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    before = h.db.execute("SELECT count(*) FROM questions").fetchone()[0]
    resp = http_confirm(
        h, qid, {"confirmed_text": "Edited memo", "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
    )
    assert resp.status_code == 409
    assert h.db.execute("SELECT count(*) FROM questions").fetchone()[0] == before


def test_empty_confirmed_text_rejected(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    prepare_and_execute(svc, qid, asset)
    for bad in ("", "   "):
        resp = http_confirm(
            h, qid, {"confirmed_text": bad, "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
        )
        assert resp.status_code == 409


def test_execute_never_auto_creates_question(voice):
    h, svc, pid, qid, asset = voice
    ready(svc)
    before = h.db.execute("SELECT count(*) FROM questions").fetchone()[0]
    prepare_and_execute(svc, qid, asset)
    assert h.db.execute("SELECT count(*) FROM questions").fetchone()[0] == before


def test_direct_confirm_helper_rejects_unknown_job(voice):
    h, svc, pid, qid, asset = voice
    with pytest.raises(Conflict):
        asyncio.run(
            confirm_voice_question(
                h, qid, {"confirmed_text": "Edited memo", "source_asset_id": asset["id"], "source_sha256": asset["sha256"]}
            )
        )
