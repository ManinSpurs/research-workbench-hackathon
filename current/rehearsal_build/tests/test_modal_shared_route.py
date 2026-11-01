"""Modal Shared endpoint route: readiness, consent binding, wire identity, settlement.

Every request is answered by an injected ``httpx.MockTransport``: no socket is
opened, no real credential is read, and nothing is billed. The remote model id
is the route's single verified canonical id (a public announcement string, not
a secret): this alias carries that model's prices and context, so the route
accepts exactly it and nothing else.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import modal_shared_config, registry, secrets, service
from app.ai.provider import MetaProvider, ModalSharedProvider
from app.api import create_app
from app.harness import Conflict
from test_dot_safety import h  # noqa: F401  (re-exported as this module's fixture)

META_KEY = "meta-not-real-key"
BASETEN_KEY = "baseten-not-real-key"
SHARED_KEY = "wk-notrealid.ws-notrealsecret"
ORIGIN = "https://synthetic--ep-wb.us-west.modal.direct"
# The verified canonical remote id for this single-model route.
REMOTE_ID = "meta-models/Muse-Spark-Open-816B-A42B"

RESULT = {
    "answer": "Reported estimate, not actual cost.",
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
USAGE = {"input_tokens": 200, "output_tokens": 100}
# (200 in * $1.25 + 100 out * $4.25) / 1e6 — the documented Modal Shared rates.
ONE_CALL_USD = (200 * 1.25 + 100 * 4.25) / 1e6


def _configure(monkeypatch, origin=ORIGIN, remote_id=REMOTE_ID, key=SHARED_KEY):
    monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", origin)
    monkeypatch.setenv("WB_MODAL_SHARED_MODEL_ID", remote_id)
    monkeypatch.setattr(service.secrets, "modal_shared_key", lambda: key)
    monkeypatch.setattr(service.secrets, "modal_shared_key_present", lambda: bool(key))


def _responses(text, model=REMOTE_ID):
    return {"status": "completed", "model": model,
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": USAGE}


def _payload(req):
    return json.loads(req.content)["input"][0]["content"][0]["text"]


def wire(h, monkeypatch, responder=None, model_echo=REMOTE_ID):
    """A service wired for both routes plus every recorded Shared request.

    All three credential readers are replaced with values defined in this
    module, so no assertion here depends on what the machine running the suite
    happens to have stored in its own owner-only key files.
    """
    monkeypatch.setattr(service.secrets, "key", lambda: META_KEY)
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: BASETEN_KEY)
    _configure(monkeypatch)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": REMOTE_ID}]})
        text = responder() if responder else (
            json.dumps(FRAME) if "public work plan" in _payload(req)
            else json.dumps(RESULT))
        return httpx.Response(200, json=_responses(text, model_echo))

    def meta_handler(req):
        seen.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": x} for x, m in registry.MODELS.items() if m.endpoint]})
        return httpx.Response(500, json={"never": "reached on a Modal Shared job"})

    svc = service.AIService(
        h,
        MetaProvider(httpx.MockTransport(meta_handler), key_reader=lambda: META_KEY),
        provider_factory={
            "meta": lambda: svc.provider,
            "baseten": lambda: _never("baseten"),
            "modal": lambda: _never("modal"),
            "modal_shared": lambda: ModalSharedProvider(
                httpx.MockTransport(handler), key_reader=lambda: SHARED_KEY),
        })
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(
        pid, "What does est_cost measure?"))["question_id"]
    return svc, qid, seen


def _never(name):
    def factory():
        raise AssertionError("Transport %s must not be resolved here" % name)
    return factory


def _ready(svc):
    svc.configure(True, 150)
    return asyncio.run(svc.verify_account("modal_shared"))


def _prepare(svc, qid, **extra):
    body = {"provider": "modal_shared", "model": "modal-shared:muse-spark-open",
            "harness": False}
    body.update(extra)
    return svc.prepare(qid, body)


def test_readiness_row_reports_configuration_without_claiming_account_access(h, monkeypatch):
    _configure(monkeypatch)
    status = modal_shared_config.connection_status()
    assert status["provider"] == "modal_shared"
    assert status["display_name"] == "Modal Shared / Muse Spark Open"
    assert status["model_id"] == "modal-shared:muse-spark-open"
    assert status["remote_model_id"] == REMOTE_ID
    assert status["base_url"] == ORIGIN + "/v1"
    assert status["endpoint_configured"] is True
    assert status["remote_model_id_configured"] is True
    assert status["key_present"] is True
    assert status["prerequisites_ready"] is True
    assert status["account_verified"] == "NOT_CHECKED"
    assert status["billing"] == "per_token"


@pytest.mark.parametrize("origin", [
    "http://synthetic--ep-wb.us-west.modal.direct",   # not https
    "https://synthetic--ep.evil.example",             # not a Modal domain
    "https://synthetic--ep.us-west.modal.direct/v1",  # a path, not an origin
    "https://synthetic--ep.us-west.modal.direct?x=1",  # query
    "https://user:pw@synthetic--ep.us-west.modal.direct",  # credentials
    "synthetic--ep.us-west.modal.direct",             # no scheme
])
def test_unsupported_origin_fails_closed_and_sends_nothing(h, monkeypatch, origin):
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", origin)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="modal_shared_configuration_incomplete"):
        _prepare(svc, qid)
    assert seen == []
    assert svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_unpinned_remote_model_id_is_not_guessed(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.delenv("WB_MODAL_SHARED_MODEL_ID", raising=False)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="remote_model_id_unconfigured"):
        _prepare(svc, qid)
    assert seen == []
    # Even a catalog listing the id does not stand in for the pinned value.
    with pytest.raises(Conflict, match="remote_model_id_unconfigured"):
        asyncio.run(svc.verify_account("modal_shared"))
    assert seen == []


def test_missing_key_sends_nothing(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    monkeypatch.setattr(service.secrets, "modal_shared_key", lambda: None)
    monkeypatch.setattr(service.secrets, "modal_shared_key_present", lambda: False)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="key_missing"):
        _prepare(svc, qid)
    assert seen == []


def test_verify_account_performs_one_explicit_catalog_get(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    result = _ready(svc)
    assert result["provider"] == "modal_shared"
    assert result["remote_model_id"] == REMOTE_ID
    assert result["executable_catalog"] == ["modal-shared:muse-spark-open"]
    assert result["inference_calls"] == 0
    assert [r.url for r in seen] == [httpx.URL(ORIGIN + "/v1/models")]
    assert seen[0].headers["authorization"] == "Bearer " + SHARED_KEY
    # The internal alias is never the wire id.
    assert "modal-shared" not in str(seen[0].url)


def test_catalog_that_omits_the_pinned_id_fails_closed(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        return httpx.Response(200, json={"data": [{"id": "meta-models/Other"}]})

    svc._provider_factory["modal_shared"] = lambda: ModalSharedProvider(
        httpx.MockTransport(handler), key_reader=lambda: SHARED_KEY)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="model_identity_mismatch"):
        asyncio.run(svc.verify_account("modal_shared"))
    assert svc.db.execute(
        "SELECT count(*) FROM ai_provider_catalog WHERE provider='modal_shared'"
    ).fetchone()[0] == 0


def test_preview_names_the_real_endpoint_and_sends_nothing(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    preview = _prepare(svc, qid)
    assert preview["provider"] == "modal_shared"
    assert preview["model"] == "modal-shared:muse-spark-open"
    assert preview["transmission"]["destination"] == ORIGIN + "/v1"
    assert preview["account_ready"] is True
    assert preview["model_access_verified"] is True
    assert preview["requires_explicit_consent"] is True
    assert preview["max_inference_calls"] == 1
    assert preview["remote_model_id"] == REMOTE_ID
    record = json.loads(svc.db.execute(
        "SELECT request_json FROM ai_jobs WHERE id=?",
        (preview["job_id"],)).fetchone()["request_json"])
    assert record["provider"] == "modal_shared"
    assert record["model"] == "modal-shared:muse-spark-open"
    assert record["modal_shared_binding"] == {
        "api_base": ORIGIN + "/v1", "remote_model_id": REMOTE_ID,
        "credential_sha256": modal_shared_config.credential_fingerprint(SHARED_KEY)}
    # No credential material is ever stored or echoed.
    assert SHARED_KEY not in json.dumps(record)
    assert SHARED_KEY not in json.dumps(preview)
    assert len(seen) == before


def test_execute_substitutes_the_remote_id_on_the_wire_only(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    result = asyncio.run(svc.execute(
        preview["job_id"], preview["preview_hash"], True))
    inference = [r for r in seen if r.url.path.endswith("/v1/responses")]
    assert len(inference) == 1
    assert str(inference[0].url) == ORIGIN + "/v1/responses"
    assert inference[0].headers["authorization"] == "Bearer " + SHARED_KEY
    body = json.loads(inference[0].content)
    # Exact remote id on the wire, internal alias nowhere in the request.
    assert body["model"] == REMOTE_ID
    assert "modal-shared" not in json.dumps(body["model"])
    assert body["store"] is False
    assert body["tools"] == []
    assert body["text"]["format"]["strict"] is True
    assert "input" in body and body["input"][0]["role"] == "user"
    assert result["outputs"]["basic"]["result"]["claims"]
    assert result["outputs"]["evaluation"]["model"] == "modal-shared:muse-spark-open"
    assert len(result["stages"]) == 1


def test_single_call_settles_from_returned_usage(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    result = asyncio.run(svc.execute(
        preview["job_id"], preview["preview_hash"], True))
    assert result["usage_complete"] is True
    assert result["calculated_cost_usd"] == pytest.approx(ONE_CALL_USD, rel=1e-9)
    assert result["provider_billed_cost_usd"] is None
    assert result["billing_status"] == "NOT_VERIFIED"
    spend = svc.db.execute(
        "SELECT status,actual_cost FROM ai_spend WHERE job_id=?",
        (preview["job_id"],)).fetchone()
    assert spend["status"] == "completed_known"
    assert spend["actual_cost"] == pytest.approx(ONE_CALL_USD, rel=1e-9)
    usage = svc.db.execute(
        "SELECT accounting_json FROM ai_usage WHERE job_id=?",
        (preview["job_id"],)).fetchone()
    assert json.loads(usage["accounting_json"])["cost_usd"] == pytest.approx(
        ONE_CALL_USD, rel=1e-9)
    # The receipt keeps the alias as the recorded model and reports unknown
    # provider billing, exactly like every other route.
    receipt = svc.receipt(preview["job_id"])
    assert receipt["model"] == "modal-shared:muse-spark-open"
    assert receipt["provider_billed_cost_usd"] is None


def test_reservation_uses_the_documented_prices_not_gpu_time(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    model = registry.MODELS["modal-shared:muse-spark-open"]
    expected = (model.context * model.input_price + 2048 * model.output_price) / 1e6
    assert preview["maximum_reserved_usd"] == pytest.approx(round(expected, 8))
    assert preview["transmission"]["destination"] != registry.BASE_URL


@pytest.mark.parametrize("field,value,error", [
    ("origin", "https://moved--ep.us-west.modal.direct",
     "destination, model identity or credential changed"),
    ("key", "wk-rotated.ws-rotated",
     "destination, model identity or credential changed"),
])
def test_consent_approval_is_invalidated_by_any_binding_change(
        h, monkeypatch, field, value, error):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    before = len(seen)
    if field == "origin":
        monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", value)
    else:
        monkeypatch.setattr(service.secrets, "modal_shared_key", lambda: value)
        _ready(svc)  # keep the account record valid for the new key
    with pytest.raises(Conflict, match=error):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    inference = [r for r in seen[before:] if r.url.path.endswith("/v1/responses")]
    assert inference == []
    assert svc.db.execute(
        "SELECT status FROM ai_jobs WHERE id=?", (preview["job_id"],)
    ).fetchone()["status"] == "prepared"
    assert svc.db.execute(
        "SELECT count(*) FROM ai_spend WHERE job_id=?", (preview["job_id"],)
    ).fetchone()[0] == 0


def test_repinned_model_id_invalidates_the_approved_preview(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    before = len(seen)
    monkeypatch.setenv("WB_MODAL_SHARED_MODEL_ID", "meta-models/Muse-Spark-Open-X0X0")
    # A model identity other than the approved canonical one is refused by the
    # single-model gate, before the binding comparison and before any POST.
    with pytest.raises(Conflict, match="remote_model_id_unverified"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert [r for r in seen[before:] if r.url.path.endswith("/v1/responses")] == []
    assert svc.db.execute("SELECT status FROM ai_jobs WHERE id=?",
                          (preview["job_id"],)).fetchone()["status"] == "prepared"


@pytest.mark.parametrize("remote_id", [
    "meta-models/Muse-Spark-Open-816B-A42B ",   # trailing space
    "meta-models/muse-spark-open-816b-a42b",    # case-folded
    "meta-models/Muse-Spark-Open",              # truncated
    "meta-models/Muse-Spark-Open-816B",         # different variant
    "meta-models/Other-Model-1.0",              # a different model entirely
])
def test_only_the_verified_canonical_remote_id_is_accepted(h, monkeypatch, remote_id):
    """Wrong rates must never be billed under this alias' prices."""
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    before = len(seen)
    monkeypatch.setenv("WB_MODAL_SHARED_MODEL_ID", remote_id)
    assert modal_shared_config.read_config()["remote_model_id"] is None
    status = modal_shared_config.connection_status()
    assert status["remote_model_id_configured"] is False
    assert status["remote_model_id_unverified"] is True
    assert status["prerequisites_ready"] is False
    assert status["blocking_reasons"] == ["remote_model_id_unverified"]
    with pytest.raises(Conflict, match="remote_model_id_unverified"):
        _prepare(svc, qid)
    # A fresh explicit verify also fails closed and records nothing.
    with pytest.raises(Conflict, match="remote_model_id_unverified"):
        asyncio.run(svc.verify_account("modal_shared"))
    assert len(seen) == before
    assert svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_unverified_remote_id_is_never_replaced_by_the_verified_default(h, monkeypatch):
    """No silent fallback: the pinned value is used verbatim or not at all."""
    monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", ORIGIN)
    monkeypatch.setenv("WB_MODAL_SHARED_MODEL_ID", "meta-models/Other-Model-1.0")
    cfg = modal_shared_config.read_config()
    assert cfg["remote_model_id"] is None
    monkeypatch.delenv("WB_MODAL_SHARED_MODEL_ID", raising=False)
    cfg = modal_shared_config.read_config()
    assert cfg["remote_model_id"] is None
    assert cfg["remote_model_id_unverified"] is False


def test_verification_is_scoped_to_origin_and_remote_model_not_just_the_key(
        h, monkeypatch):
    """Repointing the same credential must require a fresh explicit verify."""
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    verified_row = svc.db.execute(
        "SELECT key_hash FROM ai_provider_catalog WHERE provider='modal_shared'"
    ).fetchone()["key_hash"]
    before = len(seen)
    monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", "https://other--ep.us-west.modal.direct")
    # Same credential, same model id, different origin: the recorded catalog
    # was read from endpoint A and proves nothing about endpoint B.
    with pytest.raises(Conflict, match="Verify account model access"):
        _prepare(svc, qid)
    assert [r for r in seen[before:] if r.url.path.endswith("/v1/models")] == []
    assert len(seen) == before
    # Rejected before the job record and the reservation exist.
    assert svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0
    assert svc.db.execute("SELECT count(*) FROM ai_spend").fetchone()[0] == 0
    # The stored grant itself is untouched; it is simply out of scope now.
    assert svc.db.execute(
        "SELECT key_hash FROM ai_provider_catalog WHERE provider='modal_shared'"
    ).fetchone()["key_hash"] == verified_row
    assert svc._catalog_state("modal_shared")["catalog_valid_for_current_key"] is False
    # Back on the verified origin, the same grant applies again: an unchanged
    # endpoint keeps working with no extra request or re-verification.
    monkeypatch.setenv("WB_MODAL_SHARED_BASE_URL", ORIGIN)
    preview = _prepare(svc, qid)
    assert preview["transmission"]["destination"] == ORIGIN + "/v1"
    assert preview["model_access_verified"] is True
    assert len(seen) == before


def test_explicit_consent_and_preview_hash_are_still_required(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    before = len(seen)
    for hash_, consent in (("wrong", True), (preview["preview_hash"], False),
                           (preview["preview_hash"], None)):
        with pytest.raises(Conflict, match="Exact preview confirmation"):
            asyncio.run(svc.execute(preview["job_id"], hash_, consent))
    assert len(seen) == before


def test_expired_preview_sends_nothing(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = _prepare(svc, qid)
    svc.db.execute("UPDATE ai_jobs SET created_at=created_at-901 WHERE id=?",
                   (preview["job_id"],))
    svc.db.commit()
    before = len(seen)
    with pytest.raises(Conflict, match="Preview expired"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    assert len(seen) == before


@pytest.mark.parametrize("model_echo", [
    "meta-models/Muse-Spark-Open-816B-A42b",   # case-folded
    "meta-models/Muse-Spark-Open-816B-A42B ",  # whitespace
    "Meta-Models/Muse-Spark-Open-816B-A42B",   # different prefix
    "meta-models/Muse-Spark-Open-816B",        # truncated
])
def test_response_identity_mismatch_fails_closed(h, monkeypatch, model_echo):
    svc, qid, seen = wire(h, monkeypatch, model_echo=model_echo)
    _ready(svc)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="unexpected_model_identity"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    row = svc.db.execute("SELECT status,result_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    assert row["status"] == "failed_unknown_charge"
    assert "outputs" not in (row["result_json"] or "")


def test_missing_response_model_identity_fails_closed(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)

    def handler(req):
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": REMOTE_ID}]})
        return httpx.Response(200, json=_responses(json.dumps(RESULT), model=None))

    svc._provider_factory["modal_shared"] = lambda: ModalSharedProvider(
        httpx.MockTransport(handler), key_reader=lambda: SHARED_KEY)
    _ready(svc)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="unexpected_model_identity"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))


def test_schema_violation_fails_closed_before_storage(h, monkeypatch):
    invalid = json.dumps({**RESULT, "unexpected_field": "not in the contract"})
    svc, qid, seen = wire(h, monkeypatch, responder=lambda: invalid)
    _ready(svc)
    preview = _prepare(svc, qid)
    with pytest.raises(Conflict, match="typed_output_validation_failed"):
        asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
    row = svc.db.execute("SELECT status,result_json FROM ai_jobs WHERE id=?",
                         (preview["job_id"],)).fetchone()
    assert row["status"] == "failed_known_charge"
    assert "not in the contract" not in row["result_json"]
    assert "outputs" not in json.loads(row["result_json"])


def test_http_error_code_is_preserved_and_charge_stays_unknown(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    svc._provider_factory["modal_shared"] = lambda: ModalSharedProvider(
        httpx.MockTransport(lambda req: httpx.Response(429, json={"error": "slow down"})),
        key_reader=lambda: SHARED_KEY)
    svc.configure(True, 150)
    with pytest.raises(Conflict, match="rate_limit_or_credit_exhausted"):
        asyncio.run(svc.verify_account("modal_shared"))
    assert svc.db.execute(
        "SELECT count(*) FROM ai_provider_catalog WHERE provider='modal_shared'"
    ).fetchone()[0] == 0


def test_text_only_route_refuses_a_selected_asset(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    asset = svc.register_asset(qid, {"name": "note.txt", "mime": "text/plain",
                                    "encoded": "cmVwb3J0ZWQgZXN0aW1hdGU="})
    before = len(seen)
    with pytest.raises(Conflict, match="text only"):
        _prepare(svc, qid, assets=[asset["id"]])
    assert len(seen) == before


def test_route_ownership_is_enforced_in_both_directions(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    _ready(svc)
    with pytest.raises(Conflict, match="does not belong to this provider"):
        _prepare(svc, qid, model=registry.OPENAI_MODEL_ID)
    # The Shared alias never leaves on the Meta or Baseten route.
    with pytest.raises(Conflict, match="Modal-Shared-hosted"):
        svc.prepare(qid, {"provider": "meta",
                          "model": "modal-shared:muse-spark-open"})
    # The code-only Glimmer route keeps its own guard and does not accept it.
    with pytest.raises(Conflict, match="GPU-time accounting"):
        svc.prepare(qid, {"provider": "modal",
                          "model": "modal-shared:muse-spark-open"})
    assert svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_shared_alias_routes_without_an_explicit_provider(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    preview = svc.prepare(qid, {"model": "modal-shared:muse-spark-open",
                                "harness": False})
    assert preview["provider"] == "modal_shared"
    assert preview["transmission"]["destination"] == ORIGIN + "/v1"


def test_verifying_shared_does_not_verify_meta_or_baseten(h, monkeypatch):
    """A Shared verification is scoped to Shared: it authorizes nothing else.

    Each provider keeps its own verification record keyed by its own
    credential, so the other routes still refuse to prepare. Both branches are
    asserted explicitly rather than by accepting either error: with a key
    configured the block is the missing verification, and without a key it is
    the missing key. Neither branch may reach a transport.
    """
    svc, qid, seen = wire(h, monkeypatch)
    _ready(svc)
    catalogs = svc.settings()["provider_catalogs"]
    assert catalogs["modal_shared"]["catalog"] == ["modal-shared:muse-spark-open"]
    assert catalogs["modal_shared"]["catalog_valid_for_current_key"] is True
    assert catalogs["meta"]["catalog"] == []
    assert catalogs["meta"]["catalog_valid_for_current_key"] is False
    assert catalogs["baseten"]["catalog"] == []
    assert catalogs["baseten"]["catalog_valid_for_current_key"] is False
    assert catalogs["modal"]["catalog_valid_for_current_key"] is False
    # A configured (fake) Baseten key does not turn a Shared grant into a
    # Baseten grant: the recorded baseten catalog is still empty.
    assert svc.settings()["baseten_key_configured"] is True
    with pytest.raises(Conflict, match="Verify account model access"):
        svc.prepare(qid, {"provider": "baseten",
                          "model": registry.BASETEN_MODEL_ID})
    # Nor into a Meta grant. Meta's readiness is re-checked at execute, and a
    # Shared verification leaves its catalog empty, so the execution gate is
    # where that refusal appears.
    svc.meta_available = True
    meta = svc.prepare(qid, {"provider": "meta", "model": "muse-wonder-1.0",
                             "harness": False})
    assert meta["provider"] == "meta"
    assert meta["model_access_verified"] is False
    with pytest.raises(Conflict, match="Verify account model access"):
        asyncio.run(svc.execute(meta["job_id"], meta["preview_hash"], True))
    # Missing-key branch: with no Baseten credential the same attempt fails on
    # the key gate instead, which is the only correct message there. This is a
    # separate, explicit case, not an alternative accepted for one expectation.
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: None)
    assert svc.settings()["baseten_key_configured"] is False
    with pytest.raises(Conflict, match="key_missing"):
        svc.prepare(qid, {"provider": "baseten",
                          "model": registry.BASETEN_MODEL_ID})
    # None of the three refused routes transmitted anything, and no verification
    # record was written for them; the Shared grant itself is still valid.
    assert [r for r in seen if not r.url.path.endswith("/v1/models")] == []
    with_shared_ready = svc.settings()["provider_catalogs"]
    assert with_shared_ready["meta"]["catalog"] == []
    assert with_shared_ready["baseten"]["catalog"] == []
    assert with_shared_ready["modal_shared"][
        "catalog_valid_for_current_key"] is True


def test_key_rotation_invalidates_a_recorded_verification(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    _ready(svc)
    monkeypatch.setattr(service.secrets, "modal_shared_key", lambda: "wk-new.ws-new")
    state = svc._catalog_state("modal_shared")
    assert state["catalog_valid_for_current_key"] is False
    with pytest.raises(Conflict, match="Verify account model access"):
        _prepare(svc, qid)


def test_settings_expose_readiness_booleans_and_no_secret(h, monkeypatch):
    svc, qid, _ = wire(h, monkeypatch)
    settings = svc.settings()
    row = settings["modal_shared_connection"]
    assert row["endpoint_configured"] is True
    assert row["key_present"] is True
    assert row["remote_model_id"] == REMOTE_ID
    assert SHARED_KEY not in json.dumps(settings)
    assert SHARED_KEY not in json.dumps(registry.registry())
    # The legacy Glimmer readiness record is untouched and still blocked.
    assert settings["modal_connection"]["execution_enabled"] is False
    assert settings["modal_connection"]["status"] == "blocked"


def test_settings_open_makes_no_request(h, monkeypatch):
    svc, qid, seen = wire(h, monkeypatch)
    svc.settings()
    registry.registry()
    assert seen == []


def test_key_container_is_separate_and_write_only(h, monkeypatch, tmp_path):
    monkeypatch.setattr(service.secrets, "modal_shared_key_present",
                        secrets.modal_shared_key_present)
    monkeypatch.setattr(secrets, "modal_shared_key_present",
                        secrets.modal_shared_key_present)
    home = tmp_path / "home"
    home.mkdir()
    shared_file = home / "Application Support" / "ResearchWorkbench" / "modal_shared_api_key"
    meta_file = home / "Application Support" / "ResearchWorkbench" / "meta_api_key"
    monkeypatch.setattr(secrets, "secret_path", lambda: meta_file)
    monkeypatch.delenv("WB_MODAL_SHARED_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "PROVIDER_SECRETS", {
        "meta": (lambda: meta_file, ("META_API_KEY", "MODEL_API_KEY")),
        "modal_shared": (lambda: shared_file, ("WB_MODAL_SHARED_API_KEY",))})
    svc, qid, _ = wire(h, monkeypatch)
    monkeypatch.setattr(service.secrets, "modal_shared_key", lambda: (
        secrets._owner_only(shared_file)))
    with TestClient(create_app(
            svc.db.execute("PRAGMA database_list").fetchone()["file"],
            svc.h.data)) as client:
        res = client.post("/ai/keys", json={"provider": "modal_shared",
                                           "key": "wk-test.ws-test"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["saved"] is True and body["key_present"] is True
        assert "wk-test" not in res.text
    assert shared_file.read_text() == "wk-test.ws-test"
    assert shared_file.stat().st_mode & 0o777 == 0o600
    assert not meta_file.exists()
    # The owner-only gate applies to this file exactly like the others.
    shared_file.chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        secrets.modal_shared_key()
