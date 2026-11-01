"""Wonder second-look contract tests (spec: current/WONDER_SECOND_LOOK_EN.md).

Pure contract tests: registry routing + grounding structural validation only.
No provider, no paid calls, no paid-call mocks. FX fixtures reused from
data/fx/sources.json (read-only). Live execution: NOT_RUN.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import grounding, registry

REPO_FX = ROOT / "data" / "fx" / "sources.json"


def _fx_packet():
    fx = json.loads(REPO_FX.read_text(encoding="utf-8"))
    entries = fx["dd2023"] + fx["official"]
    return {
        "release": "2023",
        "sources": entries,
        "datasets": [
            {
                "dataset_id": "fx/permits_2023.csv",
                "columns": ["permit_id", "est_cost", "approval_date"],
            }
        ],
        "allowed_sources": sorted({e["source_id"] for e in entries}),
        "blocked_sources": list(fx.get("blocked", [])),
    }


def _second_look_result():
    # Wonder-shaped critique: same ResearchResult contract, critique wording.
    return {
        "answer": "Second-look critique: the Spark result is consistent with "
        "the registered definitions; final approval remains unestablished.",
        "claims": [
            {
                "text": "est_cost is applicant-reported estimated cost, not final cost.",
                "evidence_ids": ["DD-2023:est_cost"],
                "kind": "fact",
            },
            {
                "text": "approval_date marks plan-review completion in the 2023 release.",
                "evidence_ids": ["DD-2023:approval_date", "OFF-FAQ-2023"],
                "kind": "interpretation",
            },
        ],
        "unresolved": ["Whether approval_date implies final approval."],
        "proposed_variables": [],
        "next_action": "Researcher decides whether to revise the conclusion.",
    }


def test_methodology_and_cross_artifact_review_route_to_wonder():
    assert registry.route("methodology_review").id == "muse-wonder-1.0"
    assert registry.route("cross_artifact_review").id == "muse-wonder-1.0"


def test_manual_spark_for_methodology_review_current_behavior():
    # ACTUAL router behavior (kind-check only): manual Spark is ACCEPTED for
    # methodology_review today. Pinned so any future tightening is deliberate.
    assert registry.route("methodology_review", "muse-spark-1.3").id == "muse-spark-1.3"
    assert (
        registry.route("methodology_review", "muse-spark-1.3-contributor").id
        == "muse-spark-1.3-contributor"
    )
    with pytest.raises(ValueError, match="cannot perform this typed task"):
        registry.route("methodology_review", "muse-image-1.0")


def test_wonder_shaped_second_look_validates_structurally():
    packet = _fx_packet()
    parsed, checks = grounding.validate(
        json.dumps(_second_look_result()), packet, []
    )
    assert checks["structural_valid"], checks["errors"]
    assert checks["errors"] == []
    assert parsed["next_action"].startswith("Researcher")


def test_fabricated_evidence_ids_fail():
    packet = _fx_packet()
    bad = _second_look_result()
    bad["claims"] = [
        {"text": "invented", "evidence_ids": ["FAKE-9:nope"], "kind": "fact"}
    ]
    _, checks = grounding.validate(json.dumps(bad), packet, [])
    assert not checks["structural_valid"]
    assert any("Unobserved evidence ID" in e for e in checks["errors"])


def test_release_mismatched_citation_fails():
    packet = _fx_packet()
    bad = _second_look_result()
    bad["claims"] = [
        {
            "text": "approval means final approval.",
            "evidence_ids": ["OFF-NOTES-2025"],
            "kind": "fact",
        }
    ]
    _, checks = grounding.validate(json.dumps(bad), packet, [])
    assert not checks["structural_valid"]
    assert any("another release" in e for e in checks["errors"])
