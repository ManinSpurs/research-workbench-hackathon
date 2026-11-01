"""Safe validation-diagnostics helper wired into the service.py failure branch.

Purpose: when an intermediate plan (Frame) or final ResearchResult fails local
validation, service.py stores content-free diagnostics that retain the error shape
without retaining raw rejected model text or source excerpts. The first live
attempt discarded the rejected frame text, so its precise error cannot be
reconstructed; the wired failure branch retains partial outputs and safe
diagnostics on failed jobs via ``safe_validation_diagnostics`` (validation
issues only) and ``partial_output_summary`` (count-only, allowlisted arm/stage
and cost labels).

Guarantees:
- Never includes input values, ctx values, raw model text, URLs, or
  ``str(exc)`` (which may embed submitted text).
- Never copies attacker-/model-controlled strings: error locations are
  constrained to schema-safe tokens (unknown field names collapse to
  ``<extra_field>``), and error types/messages map to a fixed finite
  category set (never raw message or error-type text).
- For ``pydantic.ValidationError`` uses
  ``exc.errors(include_input=False, include_context=False, include_url=False)``
  and keeps only sanitized location / fixed error category.
- For any other exception returns only the exception class name.
- Caps retained issues at 20 entries and marks truncation explicitly.
- Arm names and statuses are restricted to application-defined labels
  evidenced from ``app/ai/service.py``; anything else collapses to
  ``<unknown>`` without being copied.
- Cost fields keep only finite numbers, so the summary always survives
  ``json.dumps(..., allow_nan=False)``.
- Pure functions: no I/O, no mutation of inputs, JSON-serializable output.
"""
from __future__ import annotations

import math
from typing import Any

MAX_ISSUES = 20

EXTRA_FIELD = "<extra_field>"
UNKNOWN_TOKEN = "<unknown>"

# Schema-safe location tokens: field names of the validated contracts
# (Frame in app/ai/service.py; Claim/ProposedVariable/ResearchResult in
# app/ai/grounding.py). Integer sequence indices are also allowed. Any other
# location part -- e.g. an unexpected extra field whose NAME carries
# model-controlled text -- collapses to EXTRA_FIELD without being echoed.
KNOWN_LOC_TOKENS = frozenset(
    {
        # Frame
        "sources",
        "gap_type",
        "focus",
        # ResearchResult and nested models
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
    }
)

# Fixed finite message-category set. Raw pydantic messages and raw error-type
# strings are never copied: arbitrary custom error types (e.g.
# PydanticCustomError with interpolated secrets) collapse to a fixed bucket.
MSG_CATEGORIES = frozenset(
    {
        "missing",
        "type_error",
        "value_error",
        "extra_forbidden",
        "constraint",
        "custom_or_unknown",
        "other",
    }
)

_TYPE_ERROR_PREFIXES = (
    "string_",
    "int_",
    "float_",
    "bool_",
    "bytes_",
    "list_",
    "dict_",
    "set_",
    "frozenset_",
    "tuple_",
    "model_",
    "literal_",
    "json_",
    "uuid_",
    "url_",
    "datetime_",
    "date_",
    "time_",
    "timedelta_",
    "decimal_",
    "enum_",
    "none_",
    "union_",
    "callable_",
    "complex_",
    "dataclass_",
    "typed_dict_",
    "arguments_",
    "is_instance",
    "get_attribute",
    "finite_number",
)

_CONSTRAINT_HINTS = (
    "too_long",
    "too_short",
    "max_length",
    "min_length",
    "greater_than",
    "greater_than_equal",
    "less_than",
    "less_than_equal",
    "multiple_of",
    "max_digits",
    "max_items",
    "min_items",
    "pattern",
    "predicate",
    "length",
)


def _categorize_error(err_type: Any) -> str:
    """Map a pydantic error type to a fixed finite category (never copied)."""
    t = err_type if isinstance(err_type, str) else ""
    t = t.lower()
    if t.startswith("missing"):
        return "missing"
    if "extra" in t:
        return "extra_forbidden"
    if "value_error" in t or t.startswith("assertion"):
        return "value_error"
    if any(h in t for h in _CONSTRAINT_HINTS):
        return "constraint"
    if t.startswith(_TYPE_ERROR_PREFIXES):
        return "type_error"
    if not t or "custom" in t or t.startswith("general"):
        return "custom_or_unknown"
    return "other"


def _loc_to_str(loc: Any) -> str:
    """Join a pydantic error location into dotted schema-safe tokens only.

    Known contract field names and integer indices pass through; any other
    part (e.g. an unexpected extra-field name carrying model-controlled
    text) becomes the fixed EXTRA_FIELD placeholder without being echoed.
    """
    try:
        parts = list(loc) if isinstance(loc, (list, tuple)) else [loc]
    except TypeError:
        parts = [loc]
    safe: list[str] = []
    for p in parts:
        if isinstance(p, bool):
            safe.append(EXTRA_FIELD)
        elif isinstance(p, int):
            safe.append(str(p))
        elif isinstance(p, str) and p in KNOWN_LOC_TOKENS:
            safe.append(p)
        else:
            safe.append(EXTRA_FIELD)
    return ".".join(safe)


def safe_validation_diagnostics(exc: BaseException) -> dict:
    """Build a content-free diagnostics dict for a validation failure.

    Args:
        exc: a ``pydantic.ValidationError`` (uses ``.errors(...)`` with
            input/context/url excluded) or any other exception (class name only).

    Returns:
        ``{"error_code": str, "issues": [...], "issue_count": int,
        "truncated": bool}`` where each issue is
        ``{"loc": str, "type": str, "msg_category": str}``. ``loc`` contains
        only schema-safe tokens (unknown names -> ``<extra_field>``) and
        ``type``/``msg_category`` are drawn from a fixed finite category set
        derived from the pydantic error type -- never submitted values, raw
        model text, URLs, or ``str(exc)``.
    """
    from pydantic import ValidationError as _ValidationError

    if isinstance(exc, _ValidationError):
        raw = exc.errors(include_input=False, include_context=False, include_url=False)
        issues: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            category = _categorize_error(item.get("type"))
            issues.append(
                {
                    "loc": _loc_to_str(item.get("loc", ())),
                    "type": category,
                    "msg_category": category,
                }
            )
        truncated = len(issues) > MAX_ISSUES
        kept = issues[:MAX_ISSUES]
        return {
            "error_code": "typed_output_validation_failed",
            "issues": kept,
            "issue_count": len(issues),
            "truncated": truncated,
        }
    return {
        "error_code": type(exc).__name__,
        "issues": [],
        "issue_count": 0,
        "truncated": False,
    }


# Arm/stage labels evidenced verbatim in app/ai/service.py: reasoning arms
# ('basic', 'harness'), the bounded-plan stage ('frame'), and non-reasoning
# invoke stages ('transcription', 'segmentation', 'image'). Anything else --
# e.g. a model-controlled arm key carrying secret text -- collapses to
# UNKNOWN_TOKEN without being copied.
KNOWN_ARMS = frozenset({"basic", "harness", "frame", "transcription", "segmentation", "image"})

# Status labels evidenced verbatim in app/ai/service.py attempt records.
KNOWN_STATUSES = frozenset({"attempted_charge_unknown", "response_received"})

_COST_KEYS = ("cost_usd", "calculated_cost_usd", "measured_known_cost_usd", "reservation", "reserve")


def _safe_arm_name(name: Any) -> str:
    """Restrict an arm key to the application-defined set; never copies unknowns."""
    return name if isinstance(name, str) and name in KNOWN_ARMS else UNKNOWN_TOKEN


def _safe_status(status: Any) -> str:
    """Restrict a status value to application-defined labels; never copies unknowns."""
    return status if isinstance(status, str) and status in KNOWN_STATUSES else UNKNOWN_TOKEN


def _is_finite_number(value: Any) -> bool:
    """True for strict-JSON-safe finite numbers (bools and non-finite rejected)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def partial_output_summary(results: dict) -> dict:
    """Summarize partial per-arm outputs without any model/claim/answer text.

    Keeps only arm names, claim counts, stage names, statuses, and cost fields
    when present. Never includes claim text, answer text, grounding error
    strings, or raw result payloads. Only dict-valued, result-bearing entries
    count as arms; scalar/None/list metadata entries (e.g. top-level cost
    keys) never inflate ``arm_count`` (recognized cost scalars are still
    surfaced separately under ``costs``). Non-finite cost values (NaN, inf,
    -inf) are rejected so the summary always survives
    ``json.dumps(..., allow_nan=False)``.

    Args:
        results: mapping of arm name -> ``{"result": ..., "grounding": ...}``
            (plus optional scalar cost keys and other metadata).
    """
    if not isinstance(results, dict):
        return {"arms": [], "arm_count": 0, "stages": [], "costs": {}}
    arms: list[dict] = []
    for name, entry in results.items():
        # Only result-bearing dict entries are arms; scalar/None/list
        # metadata must not inflate arm_count. Arm keys are restricted to the
        # application-defined set -- unknown keys collapse to <unknown>
        # instead of being copied.
        if not isinstance(entry, dict) or "result" not in entry:
            continue
        safe_name = _safe_arm_name(name)
        claim_count: int | None = None
        result = entry.get("result")
        if isinstance(result, dict):
            claims = result.get("claims")
            if isinstance(claims, list):
                claim_count = len(claims)
        elif isinstance(result, list):
            claim_count = len(result)
        status = entry.get("status")
        arm_summary: dict = {
            "arm": safe_name,
            "claim_count": claim_count,
            "status": _safe_status(status),
        }
        for cost_key in ("cost_usd", "calculated_cost_usd"):
            cost_value = entry.get(cost_key)
            if _is_finite_number(cost_value):
                arm_summary[cost_key] = cost_value
        arms.append(arm_summary)
    costs: dict = {}
    for key in _COST_KEYS:
        try:
            value = results.get(key)
        except AttributeError:
            value = None
        if _is_finite_number(value):
            costs[key] = value
    return {
        "arms": [a["arm"] for a in arms],
        "arm_count": len(arms),
        "stages": arms,
        "costs": costs,
    }
