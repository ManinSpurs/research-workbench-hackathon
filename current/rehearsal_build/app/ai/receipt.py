"""Pure Trust Receipt builder for a recorded AI job.

Decoupled from service.py: inputs mirror ``AIService.execute()`` result dicts
and job fields, but this module performs no I/O and makes no network calls.

Trust guarantees:
- Never claims provider invoice amounts or semantic correctness.
- Never copies raw rejected model text from failed validation records.
- Completed reasoning arms expose answer SHA-256 hashes, claim counts and
  validated evidence IDs only (unresolved items are count-only), never full
  answers or model-controlled strings. Every string field is allowlisted:
  unknown arm/stage/status/cost-basis/billing/request/model values collapse
  to ``<unknown>``/``unknown``/``None``/``NOT_VERIFIED`` without being copied.
- Evidence/source IDs pass only by exact registry membership when a registry
  is supplied, else by a strict registered-ID shape check; planted IDs are
  dropped in both modes.
- The receipt never carries a provider billed amount: billed cost is always
  None and billing status always NOT_VERIFIED.
- Output always survives ``json.dumps(..., allow_nan=False)``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from typing import Any

RECEIPT_VERSION = "rw-trust-receipt-1"

# Result-integrity digest: SHA-256 over a canonical JSON envelope of the
# persisted result record plus the settled ai_spend triple, recorded by
# service.py at completion time. Unsigned (no key management anywhere in
# this codebase, same threat model as the preview_hash binding): it detects
# accidental or silent row mutation after completion (the mutation probe:
# stored cost edited to 999.99), but NOT a same-writer rewrite that
# recomputes the digest consistently. The receipt NEVER fails to serve for
# legacy rows that predate the digest column: those carry digest None and
# verified False. A present-but-mismatched digest is served with
# verified False (fail-visible, not fail-closed), so altered costs are
# never served silently.
RESULT_DIGEST_VERSION = "rw-result-digest-1"

_COMPLETED = "completed"
_FAILED = "failed"
_INTERRUPTED_OR_UNKNOWN = "interrupted_or_unknown"

_UNKNOWN = "<unknown>"

# Arm names pinned to AIService.execute() invoke() call sites in service.py:
# reasoning arms ('basic', 'harness'), the bounded-plan stage ('frame'), and
# non-reasoning stages ('transcription', 'segmentation', 'image'). Any other
# key collapses to _UNKNOWN (deduplicated), never copied through.
KNOWN_ARMS = frozenset({"basic", "harness", "frame", "transcription", "segmentation", "image", "claim_check"})

# Stage names: exactly the `stage` arguments passed to invoke() in service.py,
# plus `claim_check`, the single stage app/claim_check.py records.
KNOWN_STAGE_NAMES = frozenset({"frame", "basic", "harness", "transcription", "segmentation", "image", "claim_check"})

# Stage statuses: exactly the status values written to attempt records in
# service.py ('attempted_charge_unknown' at attempt creation,
# 'response_received' once a provider response arrives).
KNOWN_STAGE_STATUSES = frozenset({"attempted_charge_unknown", "response_received"})

# Usage keys actually read for costing in service.py: reasoning token counts
# (input_tokens_details.cached_tokens is lifted to flat 'cached_tokens') and
# segmentation 'images_processed'. 'total_tokens' is a conventional provider
# accounting key. Anything else is dropped; values must be finite numbers.
KNOWN_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "total_tokens", "cached_tokens", "images_processed"})

# Exact cost_basis string written by service.py for every costed stage.
# Anything else collapses to "unknown", never raw text.
_SERVICE_COST_BASIS = "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified"

# Local stage cost basis: a known zero, not a computed estimate. Allowlisted as
# its own exact value so a local stage never collapses to "unknown" and never
# borrows the cloud string (which would imply a provider bill was computed).
_LOCAL_COST_BASIS = "zero_api_cost_local_compute_not_metered"

# Billing: the receipt never carries a billed amount (service.py always
# records provider_billed_cost_usd=None; a real billed value would need a
# provider-invoice source of truth that does not exist yet). billing_status
# is therefore always NOT_VERIFIED; no other value is representable here.

# Allowlisted validation-diagnostics categories (mirrors diagnostics.MSG_CATEGORIES).
# Raw pydantic messages / error-type strings are never copied; only these
# fixed buckets pass through.
_DIAG_CATEGORIES = frozenset({
    "missing",
    "type_error",
    "value_error",
    "extra_forbidden",
    "constraint",
    "custom_or_unknown",
    "other",
})

# Schema-safe location tokens (mirrors diagnostics.KNOWN_LOC_TOKENS).
# Dotted loc strings may only contain these tokens, integer indices, or the
# fixed <extra_field> placeholder; anything else is dropped, never copied.
_DIAG_LOC_TOKENS = frozenset({
    "sources",
    "gap_type",
    "focus",
    "answer",
    "claims",
    "unresolved",
    "proposed_variables",
    "next_action",
    "text",
    "evidence_ids",
    "kind",
    "name",
    "dataset_id",
})
_DIAG_EXTRA_FIELD = "<extra_field>"

# Request enums pinned to service.Request and registry.py (TASKS keys, model
# ids plus 'auto', mode values). Free-text fields (text, assets, ...) are
# dropped; only fixed scalar keys with finite-number or allowlisted-enum
# values survive.
KNOWN_TASKS = frozenset({"research_query", "literature_analysis", "methodology_review", "cross_artifact_review", "image_generate", "image_edit", "transcribe", "segment", "claim_check"})
KNOWN_MODELS = frozenset({"auto", "muse-spark-1.3", "muse-spark-1.3-contributor", "muse-wonder-1.0", "muse-image-1.0", "muse-voice-transcribe-1.0", "sam-3.1", "spark_open_self_hosted", "glimmer_self_hosted", "meta-models/muse-spark-open-1026", "modal-shared:muse-spark-open", "gpt-5.4-mini-2026-03-17", "local:llamacpp:qwen3-4b-instruct-2507-q4_k_m"})
KNOWN_MODES = frozenset({"cloud", "local"})

#: Fields a recorded local run binding may publish. Everything else (notably the
#: per-run bearer) is dropped: a receipt is a shareable audit document, and the
#: credential that authorized the request must never travel in it.
_LOCAL_RUN_FIELDS = ("api_base", "wire_model", "observed_model_alias",
                     "observed_context", "context", "launch_run",
                     "launch_server_pid", "weights_sha256", "run_fingerprint",
                     "template_overhead_tokens")


def _safe_local_run(value: Any) -> dict | None:
    """Narrow a recorded local run binding to publishable identity fields.

    The run fingerprint identifies the launch without the secret, so an auditor
    can tie the receipt to a verified run and still be unable to authorize a
    request with anything in this document.
    """
    if not isinstance(value, dict):
        return None
    run: dict[str, Any] = {}
    for key in _LOCAL_RUN_FIELDS:
        field = value.get(key)
        if isinstance(field, str) and field:
            run[key] = field
        elif isinstance(field, int) and not isinstance(field, bool) and field >= 0:
            run[key] = field
    return run or None

# error_code values service.py / provider.py can emit. provider_http_<NNN>
# is dynamic so it is matched by pattern; anything else is "unknown_error".
KNOWN_ERROR_CODES = frozenset({
    "typed_output_validation_failed",
    "scope_or_validation_failure",
    "unexpected_model_identity",
    # claim_check.py: returned usage above the consented reservation, and a
    # check interrupted mid-request (charge unknown) reconciled on restart.
    "usage_exceeded_reservation",
    "interrupted_unknown_charge",
    "response_not_completed",
    "key_missing",
    "endpoint_not_allowed",
    "authentication_failed",
    "model_or_region_not_authorized",
    "payment_required",
    "provider_overloaded",
    "rate_limit_or_credit_exhausted",
    "provider_response_too_large",
    "stream_failed",
    "stream_completion_missing",
    "transport_or_response_error",
    "invalid_catalog",
    "openai_invalid_request",
    "openai_assets_not_supported",
    # Local loopback path (provider.LocalProvider). Each is a distinct recorded
    # cause, so a failed local job reports what actually happened instead of a
    # generic transport error.
    "local_unreachable",
    "local_model_mismatch",
    "local_response_invalid",
    "local_response_empty",
    "local_response_incomplete",
    "local_assets_not_supported",
    "local_empty_prompt",
    "local_prompt_over_context",
    "local_timeout",
    "local_malformed_response",
    "local_context_mismatch",
    "local_template_overhead_unmeasured",
    "local_run_not_launched",
    "local_run_secret_missing",
    "local_endpoint_unconfigured",
    "local_endpoint_invalid",
    "local_model_alias_unconfigured",
    "local_context_invalid",
})
_PROVIDER_HTTP_RE = re.compile(r"provider_http_\d{3}")

# Registered ID gate. Production path: exact membership in the caller-supplied
# registry (packet.allowed_sources wired by service.py). Fallback shape check
# (registry=None) pinned from data/*/sources.json evidence_ids:
# head is PREFIX(-PART){1,2} in capitals/digits with year- or name-style parts
# (DD-2023, DD-BIS-2019, DD-DOBNOW-2018, OFF-FAQ-2023, OFF-INJECT,
# OFF-NOTES-2025, META-BIS-COVERAGE); the field part after ':' is one or more
# ':'-separated lowercase snake segments with optional dot-nested columns
# (DD-2023:est_cost, DD-2023:application_date). No uppercase, no dashes, no
# spaces/newlines in the tail; tails over 200 chars rejected. Anything shaped
# like sk-ant-..., AKIA..., or OFF-INDEPENDENT-LEAK-MARKER-7C29 fails in both
# modes and is dropped, never copied.
# Store-minted asset ids (store.new_id("asset") -> asset-<12 hex>) are also
# allowed where asset ids legitimately appear (observed_source_ids).
_ID_HEAD_RE = re.compile(r"[A-Z]{2,}(-[A-Z0-9]{2,}){1,2}")
_ID_TAIL_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*(?::[a-z0-9_]+(?:\.[a-z0-9_]+)*)*")
_ASSET_ID_RE = re.compile(r"asset-[0-9a-f]{12}")

_DISCLAIMERS = (
    "Provider invoice not verified; calculated costs are local estimates only.",
    "Semantic correctness is not scored by this receipt.",
)



def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _finite_or_none(value: Any) -> float | int | None:
    return value if _is_finite_number(value) else None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_envelope(result: Any, reservation: Any, actual_cost: Any, spend_status: Any) -> str:
    """Canonical strict-JSON envelope the completion digest covers.

    Inputs are the persisted ``result_json`` record plus the settled spend
    triple (``reservation`` is the settled ``ai_spend.reservation`` the
    service stores, never the original job reservation). Canonical form: ``json.dumps(sort_keys=True, separators=(',',':'),
    ensure_ascii=False, allow_nan=False)`` — the same ``dumps`` service.py
    uses to persist, so a byte-identical stored record re-hashes identically.
    Non-JSON-safe inputs return "" (never hashed into a false match).
    """
    try:
        return json.dumps(
            {"v": RESULT_DIGEST_VERSION, "result": result if isinstance(result, dict) else {},
             "reservation": reservation, "actual_cost": actual_cost, "spend_status": spend_status},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""


def result_digest(result: Any, reservation: Any, actual_cost: Any, spend_status: Any) -> str | None:
    """Unsigned SHA-256 digest of the canonical result+spend envelope.

    Honest labeling: detects post-completion mutation of the stored
    result/spend record, but not a same-writer rewrite (no MAC; no key
    management in this codebase). Returns None when the envelope is not
    JSON-safe (caller must not persist a digest then).
    """
    envelope = _canonical_envelope(result, reservation, actual_cost, spend_status)
    if not envelope:
        return None
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def verify_result_digest(stored_digest: Any, result: Any, reservation: Any, actual_cost: Any, spend_status: Any) -> bool | None:
    """Recompute and compare: True match, False mismatch, None no digest stored.

    ``stored_digest`` must be a 64-hex string; anything else (None for
    legacy rows, wrong type) is "no digest", never a match. A ``False``
    return means the stored result/spend row changed after completion.
    """
    if not isinstance(stored_digest, str) or not stored_digest:
        return None
    expected = result_digest(result, reservation, actual_cost, spend_status)
    if expected is None:
        # MEDIUM: a digest IS stored, so the row was digested at completion
        # time; an envelope that no longer canonicalizes (NaN in the stored
        # result, infinity in a stored spend value) is exactly the corruption
        # the digest exists to catch. Report an integrity failure, never the
        # "no digest stored" pass-through.
        return False
    try:
        return hmac.compare_digest(stored_digest, expected)
    except (TypeError, ValueError):
        return stored_digest == expected


def _classify_outcome(job_status: Any) -> str:
    status = job_status if isinstance(job_status, str) else ""
    low = status.lower()
    if low == "completed" or low == "completed_known" or low == "completed_unknown_charge":
        # Recorded but charge unknown: still a completed run, not a failure.
        return _COMPLETED
    if "fail" in low or "validation" in low or low.startswith("failed"):
        return _FAILED
    return _INTERRUPTED_OR_UNKNOWN


def _safe_arm(name: Any) -> str:
    """Restrict an arm/stage-derived key to KNOWN_ARMS; never copies unknowns."""
    return name if isinstance(name, str) and name in KNOWN_ARMS else _UNKNOWN


def _dedupe(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _valid_evidence_id(eid: Any, registry: set[str] | None = None) -> str | None:
    """Gate an evidence/source ID; drop non-members/non-matches.

    When a registry is supplied (production path: packet.allowed_sources),
    the ID passes iff it is an exact member. Otherwise the strict
    registered-ID shape applies: head PREFIX(-PART){1,2} plus, after ':',
    lowercase snake field segments (dots allowed for nested columns).
    Secret-shaped strings (sk-..., AKIA..., free sentences, over-long or
    newline-bearing tails like DD-2023:sk-ant-...-7c29, or over-long heads
    like OFF-INDEPENDENT-LEAK-MARKER-7C29) fail in both modes and are
    dropped instead of copied through.
    """
    if not isinstance(eid, str) or not eid or len(eid) > 256:
        return None
    if registry is not None:
        return eid if eid in registry else None
    head, sep, tail = eid.partition(":")
    if not _ID_HEAD_RE.fullmatch(head):
        return None
    if sep:
        if not tail or len(tail) > 200:
            return None
        if not _ID_TAIL_RE.fullmatch(tail):
            return None
    return eid


def _passes_asset_id(aid: Any, registry: set[str] | None = None) -> bool:
    """Gate a source_asset/reference asset ID: registered evidence ID or asset hex."""
    if isinstance(aid, str) and _ASSET_ID_RE.fullmatch(aid):
        return True
    return _valid_evidence_id(aid, registry) is not None


def _sanitize_usage(value: Any) -> dict:
    """Keep only allowlisted usage keys with finite-number values; drop the rest.

    No arbitrary nested dicts or strings survive: every value must be a
    finite number (bool/NaN/inf/strings/lists/dicts rejected), so
    secret-shaped text planted in usage can never pass through.
    """
    out: dict[str, Any] = {}
    if not isinstance(value, dict):
        return out
    for key in list(value.keys())[:50]:
        if not isinstance(key, str) or key not in KNOWN_USAGE_KEYS:
            continue
        entry = value[key]
        if _is_finite_number(entry):
            out[key] = entry
        elif isinstance(entry, dict):
            # input_tokens_details:{cached_tokens:N} nested shape read by
            # service.py for cached-rate costing: lift the known count only.
            if key == "input_tokens":
                cached = entry.get("cached_tokens")
                if _is_finite_number(cached):
                    out["cached_tokens"] = cached
    return out


def _sanitize_stages(stages: Any) -> list[dict]:
    if not isinstance(stages, list):
        return []
    out: list[dict] = []
    for entry in stages[:50]:
        if not isinstance(entry, dict):
            continue
        stage = entry.get("stage")
        status = entry.get("status")
        raw_basis = entry.get("cost_basis")
        out.append(
            {
                "stage": stage if isinstance(stage, str) and stage in KNOWN_STAGE_NAMES else _UNKNOWN,
                "model": _safe_model(entry.get("model")),
                "status": status if isinstance(status, str) and status in KNOWN_STAGE_STATUSES else _UNKNOWN,
                "elapsed_seconds": _finite_or_none(entry.get("elapsed_seconds")),
                "usage": _sanitize_usage(entry.get("usage")),
                "cost_usd": _finite_or_none(entry.get("cost_usd")),
                "cost_basis": (raw_basis if raw_basis in (_SERVICE_COST_BASIS, _LOCAL_COST_BASIS)
                               else "unknown"),
            }
        )
    return out


def _safe_model(value: Any) -> str | None:
    return value if isinstance(value, str) and value in KNOWN_MODELS else None


def _safe_data_use(value: Any) -> str | None:
    if value == "Standard":
        return "Standard"
    if value == "Contributor: content may be used to train Meta models":
        return value
    # Local jobs carry no cloud data-use label: the exact local sentence is the
    # only third value, so a local job can never read as a Meta transmission.
    if value == "Local compute only: no cloud provider receives this request":
        return value
    return None


#: Release-year applicability states the grounding check may record. NOT_CHECKED
#: means no comparison was possible because the question had no usable release;
#: it is never a pass.
_RELEASE_SCOPE_STATES = frozenset({"MATCH", "MISMATCH", "UNDECLARED", "NOT_CHECKED"})


def _safe_release_scope(value: Any) -> dict | None:
    """Narrow a recorded release-year applicability block to allowlisted fields.

    Returns None when the value is not a dict or carries no recognized status,
    so a legacy record without this block simply omits the key. Mismatched
    evidence IDs are kept only as registered-ID-shaped strings (same shape rule
    as ``_valid_evidence_id`` without a registry), count-bounded.
    """
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in _RELEASE_SCOPE_STATES:
        return None
    scope: dict[str, Any] = {"status": status}
    year = value.get("question_release")
    if isinstance(year, str) and re.fullmatch(r"[0-9]{4}", year):
        scope["question_release"] = year
    else:
        scope["question_release"] = None
        status = "NOT_CHECKED"
        scope["status"] = status
    mismatched = value.get("mismatched_evidence_ids")
    ids: list[str] = []
    if isinstance(mismatched, list):
        for eid in mismatched[:20]:
            valid = _valid_evidence_id(eid, None)
            if valid is not None and valid not in ids:
                ids.append(valid)
    scope["mismatched_evidence_ids"] = sorted(ids)
    if status == "NOT_CHECKED":
        scope["note"] = (
            "No usable question release: release-year applicability of the cited "
            "sources was NOT checked (not passed)."
        )
    return scope


def _safe_job_status(value: Any) -> str:
    """Restrict job status to recorded values (case-insensitive); else <unknown>."""
    known = {
        "prepared", "running", "completed",
        "completed_known", "completed_unknown_charge",
        "failed_known_charge", "failed_unknown_charge",
        "interrupted_unknown_charge",
    }
    if isinstance(value, str) and value.lower() in known:
        return value.lower()
    return _UNKNOWN


def _safe_error_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value in KNOWN_ERROR_CODES or _PROVIDER_HTTP_RE.fullmatch(value):
        return value
    return "unknown_error"


def _safe_pricing_date(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def _safe_settlement(value: Any) -> str | None:
    known = {"completed_known", "completed_unknown_charge", "failed_known_charge", "failed_unknown_charge"}
    return value if isinstance(value, str) and value in known else None


def _summarize_completed_arms(outputs: Any, registry: set[str] | None = None) -> tuple[list[str], dict, list[str]]:
    """Return (arms, per-arm summaries, observed evidence IDs) for validated output.

    Arm names are allowlisted (unknowns collapse to <unknown>, deduped).
    Evidence IDs pass only by exact registry membership when a registry is
    supplied, else by the strict registered-ID shape; non-matches are
    dropped. Unresolved items are COUNT ONLY -- no item texts, since a full
    answer duplicated into unresolved would otherwise leak verbatim.
    """
    if not isinstance(outputs, dict):
        return [], {}, []
    arms: list[str] = []
    summaries: dict[str, Any] = {}
    observed: set[str] = set()
    for raw_name, entry in outputs.items():
        if raw_name == "evaluation":
            continue
        name = _safe_arm(raw_name)
        if not isinstance(entry, dict) or "result" not in entry:
            continue
        if name in summaries:
            continue  # dedupe collisions on <unknown>; first record wins
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        arms.append(name)
        claims = result.get("claims")
        claim_count = len(claims) if isinstance(claims, list) else None
        evidence_ids: list[str] = []
        if isinstance(claims, list):
            seen: set[str] = set()
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                eids = claim.get("evidence_ids")
                if not isinstance(eids, list):
                    continue
                for eid in eids:
                    valid = _valid_evidence_id(eid, registry)
                    if valid is not None and valid not in seen:
                        seen.add(valid)
                        evidence_ids.append(valid)
                        observed.add(valid)
            evidence_ids = sorted(evidence_ids)
        answer = result.get("answer")
        answer_sha256 = _sha256_text(answer) if isinstance(answer, str) else None
        unresolved = result.get("unresolved")
        unresolved_count = len(unresolved) if isinstance(unresolved, list) else None
        proposed = result.get("proposed_variables")
        proposed_count = len(proposed) if isinstance(proposed, list) else None
        grounding = entry.get("grounding")
        ground_summary: dict[str, Any] | None = None
        if isinstance(grounding, dict):
            errors = grounding.get("errors")
            ground_summary = {
                "structural_valid": grounding.get("structural_valid") if isinstance(grounding.get("structural_valid"), bool) else None,
                "citation_count": grounding.get("citation_count") if isinstance(grounding.get("citation_count"), int) and not isinstance(grounding.get("citation_count"), bool) else None,
                "invalid_citation_count": grounding.get("invalid_citation_count") if isinstance(grounding.get("invalid_citation_count"), int) and not isinstance(grounding.get("invalid_citation_count"), bool) else None,
                "citation_error_rate": _finite_or_none(grounding.get("citation_error_rate")),
                "linked_claim_fraction": _finite_or_none(grounding.get("linked_claim_fraction")),
                "error_count": len(errors) if isinstance(errors, list) else (0 if errors is None else 1),
                "semantic_correctness": "NOT_SCORED",
            }
            release_scope = _safe_release_scope(grounding.get("release_year_applicability"))
            if release_scope is not None:
                ground_summary["release_year_applicability"] = release_scope
        summaries[name] = {
            "arm": name,
            "answer_sha256": answer_sha256,
            "claim_count": claim_count,
            "evidence_ids": evidence_ids,
            "unresolved_count": unresolved_count,
            "proposed_variable_count": proposed_count,
            "grounding": ground_summary,
        }
    return _dedupe(arms), summaries, sorted(observed)


def _summarize_partial_outputs(partial: Any) -> dict:
    if not isinstance(partial, dict):
        return {"arms": [], "arm_count": 0}
    arms: list[str] = []
    for raw_name, entry in partial.items():
        # Arms present only: never copy values (may hold rejected raw text).
        # Unknown keys collapse to <unknown> without being copied.
        if isinstance(entry, dict) and "result" in entry:
            arms.append(_safe_arm(raw_name))
        elif raw_name in KNOWN_ARMS:
            arms.append(raw_name)
    # Unknown non-result metadata keys (e.g. cost scalars) must not inflate arms.
    return {"arms": _dedupe(arms), "arm_count": len(_dedupe(arms))}


def _safe_diag_loc(value: Any) -> str | None:
    """Validate a diagnostics loc string; None when any part is unsafe.

    Only dotted schema-safe tokens (mirroring diagnostics.KNOWN_LOC_TOKENS),
    integer indices, or the fixed <extra_field> placeholder pass. A tampered
    part (e.g. a planted secret-looking token) fails the whole loc closed
    so the entry is dropped, never copied.
    """
    if not isinstance(value, str) or not value:
        return None
    for part in value.split("."):
        if not part:
            return None
        if part in _DIAG_LOC_TOKENS or part == _DIAG_EXTRA_FIELD:
            continue
        if part.isascii() and part.isdigit():
            continue
        return None
    return value


def _safe_diag_issue(entry: Any) -> dict | None:
    """Keep only a fully allowlisted diagnostics issue entry.

    The entry must be a dict whose loc passes _safe_diag_loc and whose type
    and msg_category are strings from the fixed category set (never raw
    pydantic messages or error-type text). Extra keys (msg, ctx, input
    excerpts) are never copied. Anything else returns None so the caller
    drops the entry and keeps the count only.
    """
    if not isinstance(entry, dict):
        return None
    loc = entry.get("loc")
    if _safe_diag_loc(loc) is None:
        return None
    issue_type = entry.get("type")
    if not isinstance(issue_type, str) or issue_type not in _DIAG_CATEGORIES:
        return None
    msg_category = entry.get("msg_category")
    if not isinstance(msg_category, str) or msg_category not in _DIAG_CATEGORIES:
        return None
    return {"loc": loc, "type": issue_type, "msg_category": msg_category}


def _summarize_new_diagnostics(diag: Any) -> tuple[int | None, list[dict], bool | None]:
    """Surface validated fields from a new-shape diagnostics dict.

    issue_count must be an int >= 0 (bools rejected); issues must be a list
    with each entry passing _safe_diag_issue; truncated must be a bool.
    Malformed values degrade to None/[] safely instead of being copied.
    """
    if not isinstance(diag, dict):
        return None, [], None
    raw_count = diag.get("issue_count")
    if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
        count = None
    else:
        count = raw_count
    entries: list[dict] = []
    raw_issues = diag.get("issues")
    if isinstance(raw_issues, list):
        for item in raw_issues[:50]:
            safe = _safe_diag_issue(item)
            if safe is not None:
                entries.append(safe)
    raw_truncated = diag.get("truncated")
    truncated = raw_truncated if isinstance(raw_truncated, bool) else None
    return count, entries, truncated


def _summarize_partial_summary(summary: Any) -> dict:
    """Surface arm names/count from a new-shape partial_summary dict.

    Only the arms list is read: names pass the same KNOWN_ARMS allowlist as
    legacy partials (unknowns collapse to <unknown>, non-strings dropped).
    stages/costs entries are never copied; the count is recomputed from the
    surfaced arms so a tampered arm_count cannot inflate it.
    """
    if not isinstance(summary, dict):
        return {"arms": [], "arm_count": 0}
    raw_arms = summary.get("arms")
    if not isinstance(raw_arms, list):
        return {"arms": [], "arm_count": 0}
    arms = _dedupe([_safe_arm(name) for name in raw_arms if isinstance(name, str)])
    return {"arms": arms, "arm_count": len(arms)}


def _summarize_request(request: Any) -> tuple[str | None, dict | None]:
    """Keep only fixed scalar request fields with finite/enum-safe values.

    Fields: task (allowlisted), model (allowlisted), mode (allowlisted),
    harness/compare (bool), max_output_tokens/max_cost_usd (finite numbers).
    Arbitrary strings -- task text, asset ids, extra keys -- are dropped.
    """
    if not isinstance(request, dict):
        return None, None
    raw_task = request.get("task")
    task = raw_task if isinstance(raw_task, str) and raw_task in KNOWN_TASKS else None
    safe: dict[str, Any] = {}
    model = request.get("model")
    if _safe_model(model) is not None:
        safe["model"] = model
    mode = request.get("mode")
    if isinstance(mode, str) and mode in KNOWN_MODES:
        safe["mode"] = mode
    if isinstance(request.get("harness"), bool):
        safe["harness"] = request["harness"]
    if isinstance(request.get("compare"), bool):
        safe["compare"] = request["compare"]
    for key in ("max_output_tokens", "max_cost_usd"):
        value = request.get(key)
        if _is_finite_number(value):
            safe[key] = value
    if request.get("reasoning_effort") == "low":
        safe["reasoning_effort"] = "low"
    if task is not None:
        safe["task"] = task
    if not safe:
        return task, None
    return task, safe


def build_receipt(
    job_status: Any,
    result: Any,
    reservation_usd: Any = None,
    model: Any = None,
    data_use_mode: Any = None,
    request: Any = None,
    registered_source_ids: Any = None,
    result_integrity: Any = None,
    local_run: Any = None,
) -> dict:
    """Build a JSON-safe trust receipt for a recorded AI job.

    Args:
        job_status: recorded job status string (e.g. ``completed``,
            ``failed_unknown_charge``, ``interrupted_unknown_charge``).
        result: recorded ``result_json`` dict (completed or failed shape),
            or None when missing.
        reservation_usd: conservative reservation in USD when known.
        model: model id override; falls back to result-derived model.
        data_use_mode: data-use label override (e.g. ``Standard``).
        request: prepared request record; only safe scalar fields are kept.
        registered_source_ids: iterable of registered evidence/source IDs
            (production path: packet.allowed_sources). An ID passes iff it
            is an exact member. When None, the strict registered-ID shape
            check applies instead.
        result_integrity: ``True`` (stored digest matches the persisted
            record), ``False`` (mismatch: the stored record changed after
            completion — costs served fail-visible, never silently), or
            ``None`` (no digest stored, e.g. legacy rows; unverifiable).
        local_run: recorded local run binding for a local job (origin, observed
            alias/context, run id, weights digest, fingerprint). Only the
            allowlisted identity fields are republished; the per-run bearer is
            dropped even if present.

    Returns:
        JSON-serializable dict (``json.dumps(..., allow_nan=False)`` safe).
        ``result_integrity_verified`` is True/False/None per the above;
        ``result_integrity_note`` documents the unsigned-digest limit.
    """
    data = result if isinstance(result, dict) else {}
    outcome = _classify_outcome(job_status)

    # Registry gate for evidence/source IDs (production path passes
    # packet.allowed_sources). Exact membership when supplied; strict shape
    # check otherwise.
    registry: set[str] | None = None
    if registered_source_ids is not None:
        if isinstance(registered_source_ids, str):
            registry = {registered_source_ids}
        else:
            try:
                registry = {i for i in registered_source_ids if isinstance(i, str)}
            except TypeError:
                registry = None

    resolved_model = _safe_model(model)
    if resolved_model is None:
        outputs = data.get("outputs")
        candidate = None
        if isinstance(outputs, dict):
            evaluation = outputs.get("evaluation")
            if isinstance(evaluation, dict) and isinstance(evaluation.get("model"), str):
                candidate = evaluation.get("model")
        if candidate is None:
            raw_stages = data.get("stages")
            if isinstance(raw_stages, list):
                for entry in raw_stages:
                    if isinstance(entry, dict) and isinstance(entry.get("model"), str):
                        candidate = entry.get("model")
                        break
        resolved_model = _safe_model(candidate)

    resolved_data_use = _safe_data_use(data_use_mode)

    task, safe_request = _summarize_request(request)

    arms: list[str] = []
    arm_summaries: dict[str, Any] = {}
    observed_ids: list[str] = []
    execution_order: list[str] | None = None
    unresolved_total: int | None = None

    error_code: str | None = None
    validation_issue_count: int | None = None
    validation_issue_entries: list[dict] = []
    validation_truncated: bool | None = None
    partial_summary: dict | None = None

    if outcome == _COMPLETED:
        outputs = data.get("outputs")
        if isinstance(outputs, dict):
            evaluation = outputs.get("evaluation")
            if isinstance(evaluation, dict):
                raw_order = evaluation.get("execution_order")
                if isinstance(raw_order, list):
                    ordered = [_safe_arm(e) for e in raw_order if isinstance(e, str)]
                    execution_order = _dedupe(ordered)[:10] or None
            arms, arm_summaries, observed_ids = _summarize_completed_arms(outputs, registry)
            if execution_order is None and arms:
                execution_order = list(arms)
            # Validated unresolved counts only (completed arms); never texts.
            total = 0
            has_unresolved = False
            for summary in arm_summaries.values():
                if isinstance(summary.get("unresolved_count"), int):
                    has_unresolved = True
                    total += summary["unresolved_count"]
            unresolved_total = total if has_unresolved else None
            # Non-reasoning outputs: source/asset IDs passing the registry/shape gate.
            if not observed_ids and isinstance(outputs, dict):
                extra: set[str] = set()
                source_asset = outputs.get("source_asset")
                if isinstance(source_asset, dict):
                    aid = source_asset.get("id")
                    if _passes_asset_id(aid, registry):
                        extra.add(aid)
                ref_assets = outputs.get("reference_assets")
                if isinstance(ref_assets, list):
                    for item in ref_assets:
                        if isinstance(item, dict):
                            aid = item.get("id")
                            if _passes_asset_id(aid, registry):
                                extra.add(aid)
                observed_ids = sorted(extra)
    else:
        raw_code = data.get("error_code")
        error_code = _safe_error_code(raw_code)
        validation_issue_entries: list[dict] = []
        validation_truncated: bool | None = None
        legacy_count: int | None = None
        issues = data.get("validation_issues")
        if isinstance(issues, list):
            # Count only: never copy raw rejected entries.
            legacy_count = len(issues)
        elif issues is not None:
            legacy_count = 1
        # New shape: allowlisted diagnostics dict from
        # diagnostics.safe_validation_diagnostics. Never copies raw issue
        # contents; only validated entries from the fixed category set pass.
        new_count: int | None = None
        if isinstance(data.get("diagnostics"), dict):
            new_count, validation_issue_entries, validation_truncated = _summarize_new_diagnostics(data.get("diagnostics"))
            diag_code = data["diagnostics"].get("error_code")
            if error_code is None and isinstance(diag_code, str):
                error_code = _safe_error_code(diag_code)
        # New top-level count wins when valid; else legacy list count;
        # absent both stays None. Tampered new counts never fall back to
        # legacy raw lists (legacy count only applies when no diagnostics
        # dict is present).
        if isinstance(data.get("diagnostics"), dict):
            validation_issue_count = new_count
        else:
            validation_issue_count = legacy_count
        partial_summary = None
        if isinstance(data.get("partial_summary"), dict):
            # New shape: allowlisted arm names from
            # diagnostics.partial_output_summary.
            partial_summary = _summarize_partial_summary(data.get("partial_summary"))
        elif "partial_outputs" in data:
            partial_summary = _summarize_partial_outputs(data.get("partial_outputs"))

    stages = _sanitize_stages(data.get("stages"))

    calculated = _finite_or_none(data.get("calculated_cost_usd"))
    measured = _finite_or_none(data.get("measured_known_cost_usd"))
    reservation = _finite_or_none(reservation_usd)

    # The receipt never carries a billed amount. service.py always records
    # provider_billed_cost_usd=None, so any finite number in result JSON is
    # by definition injected and must not be republished; a real billed value
    # would need a provider-invoice source of truth that does not exist yet.
    # billing_status is therefore always NOT_VERIFIED.
    provider_billed = None
    billing_status = "NOT_VERIFIED"

    usage_complete = data.get("usage_complete")
    if not isinstance(usage_complete, bool):
        usage_complete = None

    settlement_status: str | None = None
    for key in ("settlement_status", "spend_status", "settlement_state"):
        value = data.get(key)
        candidate_status = _safe_settlement(value)
        if candidate_status is not None:
            settlement_status = candidate_status
            break

    pricing_date = _safe_pricing_date(data.get("pricing_date"))

    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "job_status": _safe_job_status(job_status),
        "outcome": outcome,
        "result_integrity_verified": True if result_integrity is True else (False if result_integrity is False else None),
        "result_integrity_note": (
            "Stored result digest matches the persisted record; unsigned SHA-256, not a MAC: "
            "detects post-completion mutation, not a same-writer rewrite."
            if result_integrity is True else
            ("Stored result record changed after completion; costs are not verified — treat as untrusted."
             if result_integrity is False else
             "No stored result digest for this row (legacy or unsettled); integrity unverifiable, not asserted.")
        ),
        "model": resolved_model,
        "data_use_mode": resolved_data_use,
        "local_run": _safe_local_run(local_run),
        "task": task,
        "request_summary": safe_request,
        "arms": arms,
        "execution_order": execution_order,
        "observed_source_ids": observed_ids,
        "arm_summaries": arm_summaries,
        "unresolved_total": unresolved_total,
        "stages": stages,
        "calculated_cost_usd": calculated,
        "measured_known_cost_usd": measured,
        "reservation_usd": reservation,
        "provider_billed_cost_usd": provider_billed,
        "billing_status": billing_status,
        "usage_complete": usage_complete,
        "settlement_status": settlement_status,
        "pricing_date": pricing_date,
        "error_code": error_code,
        "validation_issue_count": validation_issue_count,
        "validation_issues": validation_issue_entries,
        "validation_truncated": validation_truncated,
        "partial_outputs": partial_summary,
        "disclaimers": list(_DISCLAIMERS),
    }

    # Strict-JSON self-check: raises on NaN/Infinity before returning.
    json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return receipt
