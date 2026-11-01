"""Frame-schema audit for AIService._body. Mock-only: no provider traffic is allowed.

Covers: (1) the frame request schema constrains source IDs to exactly the
prepared packet allowlist; (2) an empty allowlist must be rejected in prepare()
before any paid call instead of emitting an unsatisfiable ``enum: []``;
(3) building the frame schema must not corrupt the final-result schema path.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ai import grounding, service
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (shared harness fixture)


class _NoCallProvider:
    """Fake provider that records any attempt to make a paid call."""

    def __init__(self):
        self.calls = []

    async def infer(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("no paid provider call allowed in schema audit")


@pytest.fixture
def svc(h, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service.secrets, "key", lambda: "test-not-real-key")
    provider = _NoCallProvider()
    s = service.AIService(h, provider, meta_available=True)
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    return s, qid, provider


def _record(s, job_id):
    row = s.db.execute("SELECT request_json FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    return json.loads(row[0])


def test_frame_body_enum_matches_prepared_allowed_sources(svc):
    s, qid, provider = svc
    p = s.prepare(qid, {"model": "muse-wonder-1.0", "max_cost_usd": 20.0})
    record = _record(s, p["job_id"])
    allowed = record["snapshot"]["packet"]["allowed_sources"]
    assert allowed, "fixture packet must carry a non-empty allowlist"
    body = s._body(record, [], "instructions", stage="frame")
    fmt = body["text"]["format"]
    assert fmt["strict"] is True
    assert fmt["name"] == "ResearchFrame"
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"sources", "gap_type", "focus"}
    assert schema["properties"]["sources"]["items"] == {"type": "string", "enum": allowed}
    assert set(schema["properties"]["gap_type"]["enum"]) == {
        "fact", "preference", "private_context", "permission", "none",
    }
    assert provider.calls == []


def test_final_body_schema_unaffected_by_frame_mutation(svc):
    s, qid, _ = svc
    p = s.prepare(qid, {"model": "muse-wonder-1.0", "max_cost_usd": 20.0})
    record = _record(s, p["job_id"])
    pristine = grounding.ResearchResult.model_json_schema()
    frame_body = s._body(record, [], "x", stage="frame")
    assert frame_body["text"]["format"]["name"] == "ResearchFrame"
    final_body = s._body(record, [], "x", stage="harness")
    fmt = final_body["text"]["format"]
    assert fmt["name"] == "ResearchResult"
    assert fmt["strict"] is True
    assert fmt["schema"] == pristine == grounding.ResearchResult.model_json_schema()
    # Successive frame builds must not leak state between calls either.
    alt = json.loads(json.dumps(record))
    alt["snapshot"]["packet"]["allowed_sources"] = ["ONLY-THIS"]
    alt_body = s._body(alt, [], "x", stage="frame")
    assert alt_body["text"]["format"]["schema"]["properties"]["sources"]["items"] == {
        "type": "string", "enum": ["ONLY-THIS"],
    }
    again = s._body(record, [], "x", stage="frame")
    assert again["text"]["format"]["schema"]["properties"]["sources"]["items"]["enum"] == record[
        "snapshot"
    ]["packet"]["allowed_sources"]


def test_empty_allowed_sources_rejected_before_any_paid_call(svc, monkeypatch):
    s, qid, provider = svc
    snap = json.loads(json.dumps(s._snapshot(qid)))
    snap["packet"]["sources"] = []
    snap["packet"]["allowed_sources"] = []
    monkeypatch.setattr(s, "_snapshot", lambda _qid: snap)
    with pytest.raises(Conflict):
        s.prepare(qid, {})
    assert provider.calls == []
    assert s.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_canvas_edges_never_enter_the_cloud_snapshot(h):
    """A drawn edge is the researcher's assertion, not source material, so it must
    stay out of what leaves the machine. This pins the exclusion as contract: the
    snapshot currently drops edges by projecting a fixed node key list, and that
    omission must not be silently reversed by a future field addition."""
    from app import canvas

    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    canvas.save(h, qid, {
        "expected_revision": 0,
        "nodes": [
            {"label": "Filing", "dataset_id": "fx/permits_2023.csv", "variable": "application_date", "note": "mapped"},
            {"label": "Construction start", "dataset_id": None, "variable": None, "note": ""},
        ],
        "edges": [{"from_index": 0, "to_index": 1, "relation": "precedes", "note": "UNIQUE-EDGE-NOTE-55231"}],
    })
    assert canvas.read(h, qid)["edges"][0]["relation"] == "precedes"

    s = service.AIService(h, _NoCallProvider())
    snap = s._snapshot(qid)
    assert set(snap["canvas"]) == {"revision", "nodes"}
    assert all(set(n) == {"label", "dataset_id", "variable", "note"} for n in snap["canvas"]["nodes"])
    assert "edges" not in json.dumps(snap)
    assert "UNIQUE-EDGE-NOTE-55231" not in json.dumps(snap)
