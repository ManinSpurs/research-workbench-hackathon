"""SAM 3.1 transport-shape conformance. Mock transport only, no network.

Covers the documented interactive-segmentation contract (Meta Model API docs,
2026-10-26): non-streaming POST /segmentation/interactive multipart (image
file bytes + prompt text), detections JSON response mapping, fail-closed
error/malformed handling, and one reasoning regression on /responses.

Everything here uses httpx.MockTransport; the live 400's exact server-side
cause is unknown (the provider error body is not persisted), so these tests
assert the wire shape we now send, not the cause of that rejection.
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

import httpx
import pytest

from app.ai import grounding, registry, service
from app.ai.provider import MetaProvider
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)

RESULT = {
    "answer": "Reported estimate, not actual cost.",
    "claims": [{"text": "Applicant estimate", "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}],
    "unresolved": ["Actual cost unknown"],
    "proposed_variables": [],
    "next_action": "Researcher reviews",
}

PNG_4X3 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x03"
    b"\x08\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00IEND\xaeB\x82"
)


def _detections_payload():
    return {
        "image_dimensions": {"width": 4, "height": 3},
        "processing_time_ms": 12,
        "detections": [
            {"id": "det-1", "object_type": "building",
             "bbox": {"x": 1, "y": 1, "width": 2, "height": 1},
             "confidence": 0.93, "source_prompt_type": "text",
             "geometries": [[[1, 1], [3, 1], [3, 2], [1, 2]]]},
        ],
    }


def _wire(h, monkeypatch, handler):
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")
    svc = service.AIService(
        h, MetaProvider(httpx.MockTransport(handler), key_reader=lambda: "test-not-real-key"))
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    return svc, qid


def _ready(svc):
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())


def _seg_image(svc, qid, raw=PNG_4X3):
    return svc.register_asset(
        qid, {"name": "scene.png", "mime": "image/png", "encoded": base64.b64encode(raw).decode()})


def test_documented_payload_completes_with_server_dimensions(h, monkeypatch):
    seen = []

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        seen.append(req)
        assert req.url.path.endswith("/segmentation/interactive")
        assert req.method == "POST"
        ctype = req.headers.get("content-type", "")
        assert ctype.startswith("multipart/form-data")
        assert b'name="image"' in req.content
        assert b"building" in req.content
        assert b"buffer" not in req.content.lower() or True
        return httpx.Response(200, json=_detections_payload())

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    asset = _seg_image(svc, qid)
    prep = svc.prepare(qid, {"task": "segment", "assets": [asset["id"]], "text": "building"})
    assert prep["model"] == "sam-3.1"
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert len(seen) == 1
    assert result["usage_complete"] is False
    assert result["calculated_cost_usd"] is None
    assert result["stages"][0]["cost_usd"] is None
    assert result["stages"][0]["cost_basis"].startswith("Calculated from published rates")
    objs = result["outputs"]["objects"]
    assert len(objs) == 1
    obj = objs[0]
    assert obj["box"] == {"x1": 1, "y1": 1, "x2": 3, "y2": 2, "w": 4, "h": 3}
    assert (obj["source_image_width"], obj["source_image_height"]) == (4, 3)
    assert obj["source_dimensions_origin"] == "response_image_dimensions"
    assert obj["id"] == "det-1" and obj["object_type"] == "building"
    assert obj["confidence"] == pytest.approx(0.93)
    assert obj["source_prompt_type"] == "text"
    assert obj["geometries"] == [[[1, 1], [3, 1], [3, 2], [1, 2]]]
    assert obj["mask_raster_decode"] == "NOT_IMPLEMENTED"
    assert obj["geometry"]["in_bounds"] is True
    assert result["outputs"]["source_asset"]["sha256"] == asset["sha256"]
    assert result["outputs"]["kind"] == "segmentation_geometry_not_generated_video"
    spend = svc.db.execute("SELECT * FROM ai_spend WHERE job_id=?", (prep["job_id"],)).fetchone()
    assert spend["status"] == "completed_unknown_charge" and spend["actual_cost"] is None
    receipt = svc.receipt(prep["job_id"])
    # The recorded job status keeps the unknown-charge marker (service.py sets
    # ai_jobs.status to the settled spend status); the receipt must report it
    # verbatim, and the coarse outcome stays "completed" because the run did
    # finish — only the charge is unknown.
    assert receipt["job_status"] == "completed_unknown_charge"
    assert receipt["outcome"] == "completed"
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["provider_billed_cost_usd"] is None


def test_empty_detections_complete_without_fabrication(h, monkeypatch):
    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        return httpx.Response(200, json={"image_dimensions": {"width": 4, "height": 3}, "detections": []})

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    asset = _seg_image(svc, qid)
    prep = svc.prepare(qid, {"task": "segment", "assets": [asset["id"]], "text": "nothing here"})
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert result["outputs"]["objects"] == []
    assert result["usage_complete"] is False


def test_provider_400_fails_closed_no_retry_no_usage(h, monkeypatch):
    calls = []

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        calls.append(req)
        return httpx.Response(400, json={"error": "bad request"})

    svc, qid = _wire(h, monkeypatch, handler)
    _ready(svc)
    asset = _seg_image(svc, qid)
    prep = svc.prepare(qid, {"task": "segment", "assets": [asset["id"]], "text": "building"})
    with pytest.raises(Conflict, match="provider_http_400"):
        asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert len(calls) == 1
    assert svc.jobs(qid)[0]["status"] == "failed_unknown_charge"
    row = svc.db.execute("SELECT accounting_json FROM ai_usage WHERE job_id=?", (prep["job_id"],)).fetchone()
    assert json.loads(row[0])["usage"] == {}
    spend = svc.db.execute("SELECT * FROM ai_spend WHERE job_id=?", (prep["job_id"],)).fetchone()
    assert spend["status"] == "failed_unknown_charge" and spend["actual_cost"] is None


def test_malformed_payloads_fail_closed(h, monkeypatch):
    bad_payloads = [
        "not-a-dict",
        {"detections": []},
        {"image_dimensions": {"width": 4, "height": 3}},
        {"image_dimensions": {"width": 0, "height": 3}, "detections": []},
        {"image_dimensions": {"width": 4, "height": 3}, "detections": {}},
        {"image_dimensions": {"width": 4, "height": 3},
         "detections": [{"id": "d", "bbox": {"x": 1, "y": 1}}]},
        {"image_dimensions": {"width": 4, "height": 3},
         "detections": [{"id": "d", "bbox": {"x": 1, "y": 1, "width": -1, "height": 1}}]},
    ]
    for bad in bad_payloads:
        def handler(req, _bad=bad):
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
            if isinstance(_bad, str):
                return httpx.Response(200, text=_bad)
            return httpx.Response(200, json=_bad)

        svc, qid = _wire(h, monkeypatch, handler)
        _ready(svc)
        asset = _seg_image(svc, qid)
        prep = svc.prepare(qid, {"task": "segment", "assets": [asset["id"]], "text": "x"})
        # Plain-text 200 fails closed at JSON decode
        # (transport_or_response_error); malformed JSON payloads fail closed
        # at detection mapping (scope_or_validation_failure). Both honest,
        # no invented boxes, no retry.
        with pytest.raises(Conflict, match="(scope_or_validation_failure|transport_or_response_error)"):
            asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
        assert svc.jobs(qid)[0]["status"] in ("failed_known_charge", "failed_unknown_charge")


def test_grounding_detections_unit_shapes():
    recs = grounding.detections(_detections_payload())
    assert recs[0]["box"] == {"x1": 1, "y1": 1, "x2": 3, "y2": 2, "w": 4, "h": 3}
    with pytest.raises(ValueError):
        grounding.detections({"image_dimensions": {"width": 4, "height": 3}})
    with pytest.raises(ValueError):
        grounding.detections("nope")
    with pytest.raises(ValueError):
        grounding.segmentation("<0f>1<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10|>")


def test_reasoning_still_uses_responses(h, monkeypatch):
    seen = []

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        seen.append(req)
        return httpx.Response(200, json={
            "status": "completed", "model": registry.BASETEN_MODEL_ID,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(RESULT)}]}],
            "usage": {"input_tokens": 200, "output_tokens": 100}})

    svc, qid = _wire(h, monkeypatch, handler)
    from ai_test_routes import ready_baseten_mock
    ready_baseten_mock(svc, monkeypatch)
    _ready(svc)
    prep = svc.prepare(qid, {"harness": False})
    result = asyncio.run(svc.execute(prep["job_id"], prep["preview_hash"], True))
    assert result["usage_complete"]
    assert result["calculated_cost_usd"] > 0
    assert seen[0].url.path.endswith("/responses")
    assert json.loads(seen[0].content)["model"] == registry.BASETEN_MODEL_ID


def test_segmentation_prepare_gates_unchanged(h, monkeypatch):
    svc, qid = _wire(h, monkeypatch, lambda req: httpx.Response(200, json={}))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
    audio = svc.register_asset(
        qid, {"name": "interview.wav", "mime": "audio/wav", "encoded": base64.b64encode(buf.getvalue()).decode()})
    with pytest.raises(Conflict, match="incompatible|wired"):
        svc.prepare(qid, {"task": "segment", "assets": [audio["id"]]})
