"""HTTP-level coverage for GET /domain-packs (in-process test client, no network)."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.api import create_app
from app.packs import PACKS  # structural cross-checks only; every content field below is pinned literally


def _skip_installed_chips(directory, names):
    # Chips a person installed into the working copy are user data, not
    # fixtures: tests start from the shipped packs only.
    return ["installed"] if Path(directory).name == "chips" else []


def make_client(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data, ignore=_skip_installed_chips)
    app = create_app(str(tmp_path / "wb.db"), data)
    return TestClient(app)


def test_domain_packs_route_shape(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/domain-packs")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"packs", "default_pack", "capabilities", "pipeline", "h2_frame_resolve"}


def test_domain_packs_route_values_match_canonical_packs(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/domain-packs")
    assert r.status_code == 200
    body = r.json()
    assert body["default_pack"] == "social_science"
    assert body["pipeline"] == "legacy_four_stage"
    assert body["h2_frame_resolve"] == "NOT_IMPLEMENTED"
    # Literal pin of CAPABILITIES as defined in app/packs.py today; any drift must fail here.
    assert body["capabilities"] == [
        {"id": "mock_text", "kind": "text_reasoning", "status": "local_mock_only",
         "usage_unit": "mock_tokens"},
        {"id": "meta_text", "kind": "text_reasoning", "status": "blocked",
         "usage_unit": "provider_tokens_unverified"},
        {"id": "transcription", "kind": "audio_to_text", "status": "not_implemented",
         "usage_unit": "audio_duration"},
        {"id": "image_generation", "kind": "image_output", "status": "not_implemented",
         "usage_unit": "images"},
        {"id": "segmentation", "kind": "segmentation_geometry", "status": "not_implemented",
         "usage_unit": "images_or_frames"},
    ]
    assert next(x for x in body["capabilities"] if x["id"] == "meta_text")["status"] == "blocked"
    packs = body["packs"]
    assert len(packs) == len(PACKS) == 2  # structural cross-check only; pins below are literal
    assert {p["pack_id"] for p in packs} == {"social_science", "engineering_measurement"}
    by_id = {p["pack_id"]: p for p in packs}
    social = by_id["social_science"]
    assert social["title"] == "Social Science"
    assert social["version"] == "1.0.0"
    assert social["pack_version"] == "1.0.0"
    assert list(social["required_context"]) == ["research_question", "unit_of_analysis", "population", "time_window"]
    assert list(social["checks"]) == ["dataset_specific_columns", "construct_validity", "time_order", "coverage", "estimand_preserved"]
    assert social["instructions"] == "Distinguish constructs from observed variables, association from causation, and sample coverage from the target population. Preserve the estimand. Do not substitute a convenient interval for the requested outcome. Ask the researcher before changing goals. Report missing dates, temporal coverage and selection limits."
    # Literal hash of the canonical SOCIAL definition via DomainPack.identity(); content drift must change it.
    assert social["manifest_sha256"] == "c62aa93f9f9ea61c177aa737a33b3d7e2b6c1a5249c17749adb5bc2b50aecbd8"
    eng = by_id["engineering_measurement"]
    assert eng["title"] == "Engineering: Measurement"
    assert eng["version"] == "1.0.0"
    assert eng["pack_version"] == "1.0.0"
    assert list(eng["required_context"]) == ["quantity", "unit", "test_condition", "instrument", "uncertainty"]
    assert list(eng["checks"]) == ["dataset_specific_columns", "unit_documented", "conditions_comparable", "calibration_documented"]
    assert eng["instructions"] == "Limit this pack to measurement interpretation. Identify quantity, unit, test condition, instrument and uncertainty when documented. Never infer a unit or calibration from column names alone. Do not compare measurements across incompatible conditions or infer safety or compliance. Missing unit, calibration or test-condition facts require registered evidence or an unresolved finding."
    # Literal hash of the canonical ENGINEERING definition via DomainPack.identity(); content drift must change it.
    assert eng["manifest_sha256"] == "a97fd00c86f5105bb6cdb623aa37658145de41c9d236f54687ebb2fb6765f164"
    assert {p["title"] for p in packs} == {"Social Science", "Engineering: Measurement"}
