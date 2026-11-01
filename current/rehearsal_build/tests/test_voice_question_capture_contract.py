"""Voice question-capture contracts (offline, pure).

Covers only existing contracts importable without keys, network, or DB:
registry routing + app/ai/inputs.py validation. No service/api/frontend/harness
changes here. Transcript->question wiring is implemented in app code
(`confirm_voice_question` + POST /questions/{qid}/ai/voice-confirm, frontend
submitVoiceConfirm/renderVoiceConfirm/targetVoiceConfirm; see
current/VOICE_QUESTION_CAPTURE_EN.md section 4); the confirmation gate below is a
TEST-LOCAL state model of that gate, not app code. End-to-end coverage lives in
tests/test_voice_question_wiring.py.
"""
import base64
import hashlib
import io
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import inputs, registry


def make_wav_bytes(seconds=1, framerate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\0\0" * framerate * seconds)
    return buf.getvalue()


def test_transcribe_routes_to_voice_model():
    model = registry.route("transcribe")
    assert model.id == "muse-voice-transcribe-1.0"
    assert model.kind == "transcription"
    assert model.endpoint == "/asr/transcribe"
    assert "audio" in model.modalities
    assert registry.TASKS["transcribe"] == "muse-voice-transcribe-1.0"


def test_manual_reasoning_model_fails_transcribe_by_kind():
    with pytest.raises(ValueError):
        registry.route("transcribe", "muse-spark-1.3")
    with pytest.raises(ValueError):
        registry.route("transcribe", "muse-wonder-1.0")


def test_valid_pcm_wav_passes_inputs_validation():
    raw = make_wav_bytes()
    encoded = base64.b64encode(raw).decode()
    out_raw, meta = inputs.inspect("interview.wav", "audio/wav", encoded)
    assert out_raw == raw
    assert meta["kind"] == "audio"
    assert meta["mime"] == "audio/wav"
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["duration_seconds"] == pytest.approx(1.0)


def test_invalid_bytes_fail_wav_validation():
    with pytest.raises(ValueError):
        inputs.inspect("bad.wav", "audio/wav", base64.b64encode(b"not a wav").decode())
    with pytest.raises(ValueError):
        inputs.inspect("text.wav", "audio/wav", base64.b64encode(b"%PDF-1.4 fake").decode())


def test_audio_refused_by_text_reasoning_block():
    raw = make_wav_bytes()
    encoded = base64.b64encode(raw).decode()
    _, meta = inputs.inspect("interview.wav", "audio/wav", encoded)
    with pytest.raises(ValueError):
        inputs.block(meta, encoded)


# --- TEST-LOCAL confirmation gate (not app code) ---------------------------
# Models doc section 4: proposed transcript must never become a question write
# without an explicit confirm carrying the final string. App code implements
# this gate in `confirm_voice_question` (POST /questions/{qid}/ai/voice-confirm),
# covered end-to-end by tests/test_voice_question_wiring.py; this pure model
# pins the same state contract offline.
class _TestLocalProposal:
    def __init__(self, transcript, source_sha256):
        self.state = "proposed"
        self.transcript = transcript
        self.source_sha256 = source_sha256
        self.question_write = None

    def confirm(self, final_text, explicit):
        if not explicit:
            return self  # stays proposed; no write
        assert isinstance(final_text, str) and final_text.strip()
        self.state = "question_confirmed"
        self.question_write = {"text": final_text, "source_sha256": self.source_sha256}
        return self


def test_transcript_proposal_requires_explicit_confirmation():
    gate = _TestLocalProposal("Research interview memo", "deadbeef" * 8)
    gate.confirm(gate.transcript, explicit=False)
    assert gate.state == "proposed" and gate.question_write is None
    gate.confirm("  edited memo text  ".strip(), explicit=True)
    assert gate.state == "question_confirmed"
    assert gate.question_write == {"text": "edited memo text", "source_sha256": "deadbeef" * 8}
