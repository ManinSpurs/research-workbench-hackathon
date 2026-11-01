"""Trust Receipt builder tests: synthetic records only, no live calls."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import spend
from app.ai.receipt import RECEIPT_VERSION, build_receipt, result_digest, verify_result_digest

SECRET = "sk-ant-S3CR3T-receipt-probe-9f8e7d6c5b4a"

ANSWER = "The dataset shows a rising trend in the observed window."
CLAIM_TEXT = "Sales rose in Q3 according to the registered source."
UNRESOLVED_TEXT = "Whether the Q4 figures follow the same definition is unclear."
ANSWER_DUP = "FULL-ANSWER-DUPLICATE-PLANTED-IN-UNRESOLVED-zz9"

MARK_STAGE = "STAGE-NAME-MARKER-qq1"
MARK_STATUS = "STATUS-MARKER-qq2"
MARK_ARM = "ARM-KEY-MARKER-qq3"
MARK_USAGE_KEY = "USAGE-KEY-MARKER-qq4"
MARK_USAGE_VAL = "USAGE-VAL-MARKER-qq5"
MARK_BASIS = "BASIS-MARKER-qq6"
MARK_BILLING = "Provider invoice verified; semantic correctness guaranteed."
MARK_REQUEST = "REQUEST-TEXT-MARKER-qq8"
MARK_EID = "sk-ant-EVIDENCE-SECRET-MARKER-qq9"
MARK_JOB = "JOBSTATUS-MARKER-qq10"
MARK_ERROR = "ERRORCODE-MARKER-qq11"


def _completed_result():
    parsed = {
        "answer": ANSWER,
        "claims": [
            {"text": CLAIM_TEXT, "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"},
            {"text": "A follow-on interpretation.", "evidence_ids": ["DD-2023:est_cost", "DD-2023:approval_date"], "kind": "interpretation"},
        ],
        "unresolved": [UNRESOLVED_TEXT],
        "proposed_variables": [],
        "next_action": "Ask the researcher to confirm the Q4 definition.",
    }
    grounding = {
        "structural_valid": True,
        "errors": [],
        "citation_count": 3,
        "invalid_citation_count": 0,
        "citation_error_rate": 0.0,
        "linked_claim_fraction": 1.0,
        "semantic_correctness": "NOT_SCORED",
        "false_citation_rate": "HUMAN_REVIEW_REQUIRED",
    }
    stage = {
        "stage": "harness",
        "model": "muse-spark-1.3",
        "status": "response_received",
        "elapsed_seconds": 1.25,
        "request_sha256": "abc123",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "cost_usd": 0.0003375,
        "cost_basis": "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified",
    }
    return {
        "outputs": {
            "harness": {"result": parsed, "grounding": grounding},
            "evaluation": {"kind": "single", "accuracy": "NOT_SCORED", "model": "muse-spark-1.3", "execution_order": ["harness"]},
        },
        "stages": [stage],
        "usage_complete": True,
        "calculated_cost_usd": 0.0003375,
        "measured_known_cost_usd": 0.0003375,
        "elapsed_seconds": 1.5,
        "pricing_date": "2026-10-26",
        "provider_billed_cost_usd": None,
        "billing_status": "NOT_VERIFIED",
    }


def _failed_validation_result():
    return {
        "error_code": "typed_output_validation_failed",
        "validation_issues": [
            {"loc": ("claims", 0), "msg": f"bad value {SECRET}", "type": "value_error", "ctx": {"secret": SECRET}},
            {"loc": ("answer",), "msg": "missing", "type": "missing"},
        ],
        "partial_outputs": {
            "harness": {
                "result": {"answer": SECRET, "claims": [{"text": SECRET, "evidence_ids": [SECRET]}]},
                "grounding": {"errors": [f"rejected because {SECRET}"]},
                "raw_rejected_text": SECRET,
            },
        },
        "stages": [
            {
                "stage": "harness",
                "model": "muse-spark-1.3",
                "status": "response_received",
                "elapsed_seconds": 0.9,
                "usage": {"input_tokens": 10},
                "cost_usd": None,
                "cost_basis": "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified",
            }
        ],
        "calculated_cost_usd": None,
        "usage_complete": False,
        "provider_billed_cost_usd": None,
        "billing_status": "NOT_VERIFIED",
    }


def _serialized(receipt: dict) -> str:
    return json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _assert_finite_costs(receipt: dict) -> None:
    for key in ("calculated_cost_usd", "measured_known_cost_usd", "reservation_usd", "provider_billed_cost_usd"):
        value = receipt.get(key)
        assert value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")), key
    for stage in receipt["stages"]:
        value = stage.get("cost_usd")
        assert value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf"))


def test_completed_hashes_counts_and_ids():
    receipt = build_receipt(
        "completed",
        _completed_result(),
        reservation_usd=0.005,
        model="muse-spark-1.3",
        data_use_mode="Standard",
        request={"task": "research_query", "model": "auto", "mode": "cloud", "harness": True, "compare": False, "max_output_tokens": 2048, "max_cost_usd": 5.0},
    )
    assert receipt["receipt_version"] == RECEIPT_VERSION
    assert receipt["outcome"] == "completed"
    assert receipt["job_status"] == "completed"
    assert receipt["model"] == "muse-spark-1.3"
    assert receipt["data_use_mode"] == "Standard"
    assert receipt["arms"] == ["harness"]
    assert receipt["execution_order"] == ["harness"]
    summary = receipt["arm_summaries"]["harness"]
    assert summary["answer_sha256"] == hashlib.sha256(ANSWER.encode("utf-8")).hexdigest()
    assert summary["claim_count"] == 2
    assert summary["evidence_ids"] == ["DD-2023:approval_date", "DD-2023:est_cost"]
    assert receipt["observed_source_ids"] == ["DD-2023:approval_date", "DD-2023:est_cost"]
    assert summary["unresolved_count"] == 1
    assert "unresolved_items" not in summary
    assert receipt["unresolved_total"] == 1
    assert summary["grounding"]["semantic_correctness"] == "NOT_SCORED"
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["reservation_usd"] == 0.005
    assert receipt["error_code"] is None
    stage = receipt["stages"][0]
    assert stage["stage"] == "harness"
    assert stage["model"] == "muse-spark-1.3"
    assert stage["status"] == "response_received"
    assert stage["elapsed_seconds"] == 1.25
    assert stage["usage"] == {"input_tokens": 100, "output_tokens": 50}
    assert stage["cost_usd"] == 0.0003375
    assert stage["cost_basis"] == "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified"
    _assert_finite_costs(receipt)
    body = _serialized(receipt)
    # Validated unresolved text must NOT appear (count only); raw answer/claim must not.
    assert ANSWER not in body
    assert CLAIM_TEXT not in body
    assert UNRESOLVED_TEXT not in body


def test_validation_failure_leaks_no_raw_text():
    receipt = build_receipt("failed_unknown_charge", _failed_validation_result(), reservation_usd=0.004)
    assert receipt["outcome"] == "failed"
    assert receipt["error_code"] == "typed_output_validation_failed"
    assert receipt["validation_issue_count"] == 2
    assert receipt["partial_outputs"]["arms"] == ["harness"]
    assert receipt["partial_outputs"]["arm_count"] == 1
    assert receipt["arm_summaries"] == {}
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["calculated_cost_usd"] is None
    _assert_finite_costs(receipt)
    body = _serialized(receipt)
    assert SECRET not in body


def test_transport_unknown_like_stays_unverified():
    result = {
        "error_code": "response_not_completed",
        "validation_issues": [],
        "partial_outputs": {},
        "stages": [
            {
                "stage": "frame",
                "model": "muse-spark-1.3",
                "status": "attempted_charge_unknown",
                "elapsed_seconds": None,
                "usage": {},
                "cost_usd": float("nan"),
                "cost_basis": "Calculated from published rates and returned usage; provider bill not verified",
            }
        ],
        "calculated_cost_usd": float("inf"),
        "measured_known_cost_usd": float("nan"),
        "usage_complete": False,
        "provider_billed_cost_usd": None,
        "billing_status": "NOT_VERIFIED",
    }
    receipt = build_receipt("interrupted_unknown_charge", result, reservation_usd=0.004)
    assert receipt["outcome"] == "interrupted_or_unknown"
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["calculated_cost_usd"] is None
    assert receipt["measured_known_cost_usd"] is None
    assert receipt["stages"][0]["cost_usd"] is None
    _assert_finite_costs(receipt)
    _serialized(receipt)


def test_missing_fields_still_json_safe():
    for job_status, result in [
        ("completed", None),
        ("interrupted_unknown_charge", {}),
        (None, {"stages": "not-a-list", "calculated_cost_usd": float("nan")}),
        ("failed_unknown_charge", {"error_code": "scope_or_validation_failure"}),
    ]:
        receipt = build_receipt(job_status, result)
        assert receipt["receipt_version"] == RECEIPT_VERSION
        assert receipt["provider_billed_cost_usd"] is None
        assert receipt["billing_status"] == "NOT_VERIFIED"
        assert isinstance(receipt["stages"], list)
        _assert_finite_costs(receipt)
        _serialized(receipt)
    receipt = build_receipt("failed_unknown_charge", {"error_code": "scope_or_validation_failure"})
    assert receipt["outcome"] == "failed"
    assert receipt["validation_issue_count"] is None


def test_adversarial_arm_keys_collapse():
    result = _completed_result()
    result["outputs"][MARK_ARM] = {
        "result": {
            "answer": "x",
            "claims": [],
            "unresolved": [],
            "proposed_variables": [],
            "next_action": "y",
        },
        "grounding": {},
    }
    receipt = build_receipt("completed", result)
    body = _serialized(receipt)
    assert MARK_ARM not in body
    assert receipt["arms"] == ["harness", "<unknown>"]
    assert receipt["arm_summaries"]["<unknown>"]["answer_sha256"] == hashlib.sha256(b"x").hexdigest()
    assert receipt["execution_order"] == ["harness"]


def test_adversarial_stage_name_status_collapse():
    result = {
        "outputs": {},
        "stages": [
            {"stage": MARK_STAGE, "model": "muse-spark-1.3", "status": MARK_STATUS,
             "elapsed_seconds": 1.0, "usage": {"input_tokens": 5}, "cost_usd": 0.1,
             "cost_basis": "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified"},
            {"stage": "frame", "model": "muse-spark-1.3", "status": "ok",
             "elapsed_seconds": 1.0, "usage": {"input_tokens": 5}, "cost_usd": 0.1,
             "cost_basis": "Calculated from published rates and returned usage; absent cached count assumed zero; provider bill not verified"},
        ],
        "usage_complete": True,
        "calculated_cost_usd": 0.1,
        "measured_known_cost_usd": 0.1,
    }
    receipt = build_receipt("completed_unknown_charge", result)
    assert receipt["outcome"] == "completed"
    assert receipt["stages"][0]["stage"] == "<unknown>"
    assert receipt["stages"][0]["status"] == "<unknown>"
    assert receipt["stages"][1]["stage"] == "frame"
    assert receipt["stages"][1]["status"] == "<unknown>"
    body = _serialized(receipt)
    assert MARK_STAGE not in body
    assert MARK_STATUS not in body


def test_adversarial_usage_keys_values_dropped():
    result = _completed_result()
    result["stages"][0]["usage"] = {
        "input_tokens": 100,
        "output_tokens": 50,
        MARK_USAGE_KEY: 999,
        "total_tokens": MARK_USAGE_VAL,
        "cached_tokens": float("nan"),
        "images_processed": "7",
        "nested": {"deep": MARK_USAGE_VAL},
    }
    receipt = build_receipt("completed", result)
    assert receipt["stages"][0]["usage"] == {"input_tokens": 100, "output_tokens": 50}
    body = _serialized(receipt)
    assert MARK_USAGE_KEY not in body
    assert MARK_USAGE_VAL not in body


def test_adversarial_cost_basis_and_billing_forced():
    result = _completed_result()
    result["stages"][0]["cost_basis"] = MARK_BASIS
    result["billing_status"] = MARK_BILLING
    receipt = build_receipt("completed", result)
    assert receipt["stages"][0]["cost_basis"] == "unknown"
    assert receipt["billing_status"] == "NOT_VERIFIED"
    body = _serialized(receipt)
    assert MARK_BASIS not in body
    assert MARK_BILLING not in body


def test_allowlisted_billing_without_billed_number_forced():
    result = _completed_result()
    result["provider_billed_cost_usd"] = None
    result["billing_status"] = "PAID_IN_FULL"
    receipt = build_receipt("completed", result)
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    body = _serialized(receipt)
    assert "PAID_IN_FULL" not in body


def test_adversarial_request_fields_dropped():
    receipt = build_receipt(
        "completed",
        _completed_result(),
        request={
            "task": MARK_REQUEST,
            "model": MARK_REQUEST,
            "mode": MARK_REQUEST,
            "harness": "yes",
            "compare": 1,
            "max_output_tokens": float("nan"),
            "max_cost_usd": 5.0,
            "text": MARK_REQUEST,
            "assets": [MARK_REQUEST],
            MARK_REQUEST: MARK_REQUEST,
        },
    )
    assert receipt["task"] is None
    summary = receipt["request_summary"]
    assert summary == {"max_cost_usd": 5.0}
    body = _serialized(receipt)
    assert MARK_REQUEST not in body


def test_adversarial_evidence_ids_dropped():
    result = _completed_result()
    result["outputs"]["harness"]["result"]["claims"] = [
        {"text": "planted", "evidence_ids": [MARK_EID, "not-an-id", "DD-2023:est_cost"], "kind": "fact"},
    ]
    receipt = build_receipt("completed", result)
    summary = receipt["arm_summaries"]["harness"]
    assert summary["evidence_ids"] == ["DD-2023:est_cost"]
    assert receipt["observed_source_ids"] == ["DD-2023:est_cost"]
    body = _serialized(receipt)
    assert MARK_EID not in body
    assert "not-an-id" not in body


def test_full_answer_in_unresolved_stays_count_only():
    result = _completed_result()
    result["outputs"]["harness"]["result"]["unresolved"] = [ANSWER_DUP, ANSWER_DUP]
    result["outputs"]["harness"]["result"]["answer"] = ANSWER_DUP
    receipt = build_receipt("completed", result)
    summary = receipt["arm_summaries"]["harness"]
    assert summary["unresolved_count"] == 2
    assert "unresolved_items" not in summary
    assert receipt["unresolved_total"] == 2
    assert summary["answer_sha256"] == hashlib.sha256(ANSWER_DUP.encode("utf-8")).hexdigest()
    body = _serialized(receipt)
    assert ANSWER_DUP not in body


def test_case_insensitive_completed_and_safe_wrappers():
    for status in ("Completed", "COMPLETED", "COMPLETED_KNOWN"):
        receipt = build_receipt(status, _completed_result())
        assert receipt["outcome"] == "completed", status
    receipt = build_receipt(MARK_JOB, {"error_code": MARK_ERROR, "billing_status": 123,
                                       "settlement_status": MARK_REQUEST, "pricing_date": MARK_REQUEST})
    assert receipt["job_status"] == "<unknown>"
    assert receipt["error_code"] == "unknown_error"
    assert receipt["billing_status"] == "NOT_VERIFIED"
    assert receipt["settlement_status"] is None
    assert receipt["pricing_date"] is None
    assert receipt["model"] is None and receipt["data_use_mode"] is None
    body = _serialized(receipt)
    assert MARK_JOB not in body
    assert MARK_ERROR not in body
    assert MARK_REQUEST not in body


SOL_TAIL_MARKER = "DD-2023:sk-ant-INDEPENDENT-LEAK-MARKER-7c29"
SOL_HEAD_MARKER = "OFF-INDEPENDENT-LEAK-MARKER-7C29"
TAIL_VARIANTS = [
    SOL_TAIL_MARKER,
    "DD-2023:AKIA-INDEPENDENT-LEAK-MARKER-7C29",
    "DD-2023:est cost leaked",  # space in tail
    "DD-2023:est_cost\ninjected",  # newline in tail
    "DD-2023:" + "a" * 201,  # tail over 200 chars
    SOL_HEAD_MARKER,
]


def _claims_result_with_ids(eids):
    result = _completed_result()
    result["outputs"]["harness"]["result"]["claims"] = [
        {"text": "planted", "evidence_ids": list(eids), "kind": "fact"},
    ]
    return result


def test_sol_counterexample_ids_dropped_without_registry():
    for marker in (SOL_TAIL_MARKER, SOL_HEAD_MARKER):
        receipt = build_receipt("completed", _claims_result_with_ids([marker, "DD-2023:est_cost"]))
        summary = receipt["arm_summaries"]["harness"]
        assert summary["evidence_ids"] == ["DD-2023:est_cost"], marker
        assert receipt["observed_source_ids"] == ["DD-2023:est_cost"], marker
        body = _serialized(receipt)
        assert marker not in body


def test_sol_counterexample_ids_dropped_with_registry():
    registry = ["DD-2023:est_cost", "DD-2023:approval_date"]
    for marker in (SOL_TAIL_MARKER, SOL_HEAD_MARKER):
        receipt = build_receipt(
            "completed",
            _claims_result_with_ids([marker, "DD-2023:est_cost"]),
            registered_source_ids=registry,
        )
        summary = receipt["arm_summaries"]["harness"]
        assert summary["evidence_ids"] == ["DD-2023:est_cost"], marker
        assert receipt["observed_source_ids"] == ["DD-2023:est_cost"], marker
        body = _serialized(receipt)
        assert marker not in body


def test_tail_smuggling_variants_dropped_both_modes():
    registry = ["DD-2023:est_cost"]
    for marker in TAIL_VARIANTS:
        for kwargs in ({}, {"registered_source_ids": registry}):
            receipt = build_receipt("completed", _claims_result_with_ids([marker]), **kwargs)
            summary = receipt["arm_summaries"]["harness"]
            assert summary["evidence_ids"] == [], (marker, kwargs)
            assert receipt["observed_source_ids"] == [], (marker, kwargs)
            body = _serialized(receipt)
            assert marker not in body


def test_registry_membership_exact_match_only():
    # In-registry real ID passes; shape-valid but unregistered ID does not.
    receipt = build_receipt(
        "completed",
        _claims_result_with_ids(["DD-2023:est_cost", "DD-2023:approval_date"]),
        registered_source_ids=["DD-2023:est_cost"],
    )
    assert receipt["arm_summaries"]["harness"]["evidence_ids"] == ["DD-2023:est_cost"]
    assert receipt["observed_source_ids"] == ["DD-2023:est_cost"]
    # Real registered ID also passes the no-registry shape check.
    receipt = build_receipt("completed", _claims_result_with_ids(["DD-2023:est_cost"]))
    assert receipt["arm_summaries"]["harness"]["evidence_ids"] == ["DD-2023:est_cost"]


def test_asset_ids_unchanged_for_source_and_reference_assets():
    asset = "asset-abcdef123456"
    for kwargs in ({}, {"registered_source_ids": ["DD-2023:est_cost"]}):
        result = {
            "outputs": {"source_asset": {"id": asset}, "reference_assets": [{"id": asset}, {"id": SOL_TAIL_MARKER}]},
            "stages": [],
            "usage_complete": True,
            "calculated_cost_usd": 0.0,
            "measured_known_cost_usd": 0.0,
        }
        receipt = build_receipt("completed", result, **kwargs)
        assert receipt["observed_source_ids"] == [asset], kwargs
        body = _serialized(receipt)
        assert asset in body
        assert SOL_TAIL_MARKER not in body


def test_billed_amount_never_carried():
    for extra in (
        {"provider_billed_cost_usd": 1.23},
        {"provider_billed_cost_usd": 999.99, "billing_status": "PAID_IN_FULL"},
        {"provider_billed_cost_usd": 0.0, "billing_status": "NOT_VERIFIED"},
    ):
        result = _completed_result()
        result.update(extra)
        receipt = build_receipt("completed", result)
        assert receipt["provider_billed_cost_usd"] is None, extra
        assert receipt["billing_status"] == "NOT_VERIFIED", extra
        body = _serialized(receipt)
        assert "PAID_IN_FULL" not in body


# --- New-shape failure records (service.py diagnostics/partial_summary) ---

NEW_MARK_SECRET = "sk-ant-NEWSHAPE-SECRET-MARKER-zz1"
NEW_MARK_BOGUS_ARM = "ARM-KEY-NEWSHAPE-MARKER-zz2"
NEW_MARK_BOGUS_FIELD = "x_NEW_SHAPE_MARKER_zz3"

NEW_MARK_LOC = "sk-ant-LOC-MARKER-zz4"
NEW_MARK_TYPE = "value_error leaked raw text " + NEW_MARK_SECRET
NEW_MARK_MSG = "msg leaked " + NEW_MARK_SECRET


def _new_failed_result():
    return {
        "error_code": None,
        "diagnostics": {
            "error_code": "typed_output_validation_failed",
            "issues": [
                {"loc": "claims.0", "type": "value_error", "msg_category": "value_error",
                 "msg": NEW_MARK_SECRET, "ctx": {"secret": NEW_MARK_SECRET},
                 "input": NEW_MARK_SECRET},
                {"loc": "answer", "type": "missing", "msg_category": "missing"},
                {"loc": NEW_MARK_LOC, "type": "missing", "msg_category": "missing"},
                {"loc": "claims", "type": NEW_MARK_TYPE, "msg_category": "value_error"},
                {"loc": "claims", "type": "value_error", "msg_category": NEW_MARK_MSG},
                "not-a-dict-entry",
                {"loc": ["claims"], "type": "missing", "msg_category": "missing",
                 NEW_MARK_BOGUS_FIELD: NEW_MARK_SECRET},
                {"loc": "claims." + NEW_MARK_SECRET, "type": "missing", "msg_category": "missing"},
            ],
            "issue_count": 8,
            "truncated": True,
            "extra_extra": NEW_MARK_SECRET,
        },
        "validation_issue_count": 8,
        "partial_summary": {
            "arms": ["harness", NEW_MARK_BOGUS_ARM, 123, "harness"],
            "arm_count": 99,
            "stages": [{"arm": "harness", "note": NEW_MARK_SECRET}],
            "costs": {"cost_usd": NEW_MARK_SECRET, "calculated_cost_usd": 1.23},
        },
        "stages": [],
        "calculated_cost_usd": None,
        "usage_complete": False,
        "provider_billed_cost_usd": None,
        "billing_status": "NOT_VERIFIED",
    }


def test_new_failed_shape_surfaces_counts_and_allowlisted_entries():
    receipt = build_receipt("failed_unknown_charge", _new_failed_result(), reservation_usd=0.004)
    assert receipt["outcome"] == "failed"
    # error_code recovered from the allowlisted diagnostics dict.
    assert receipt["error_code"] == "typed_output_validation_failed"
    assert receipt["validation_issue_count"] == 8
    # Only the two fully allowlisted entries survive; planted secret-looking
    # values and extra keys are dropped, never copied.
    assert receipt["validation_issues"] == [
        {"loc": "claims.0", "type": "value_error", "msg_category": "value_error"},
        {"loc": "answer", "type": "missing", "msg_category": "missing"},
    ]
    assert receipt["validation_truncated"] is True
    # Bogus arm keys collapse to <unknown>; tampered arm_count recomputed.
    assert receipt["partial_outputs"]["arms"] == ["harness", "<unknown>"]
    assert receipt["partial_outputs"]["arm_count"] == 2
    assert set(receipt["partial_outputs"]) == {"arms", "arm_count"}
    assert receipt["arm_summaries"] == {}
    assert receipt["provider_billed_cost_usd"] is None
    assert receipt["billing_status"] == "NOT_VERIFIED"
    _assert_finite_costs(receipt)
    body = _serialized(receipt)
    for marker in (NEW_MARK_SECRET, NEW_MARK_BOGUS_ARM, NEW_MARK_BOGUS_FIELD,
                   NEW_MARK_LOC, NEW_MARK_TYPE, NEW_MARK_MSG):
        assert marker not in body


def test_new_shape_top_level_error_code_takes_precedence():
    result = _new_failed_result()
    result["error_code"] = "response_not_completed"
    receipt = build_receipt("failed_unknown_charge", result)
    assert receipt["error_code"] == "response_not_completed"
    _serialized(receipt)


def test_legacy_failed_shape_still_works():
    receipt = build_receipt("failed_unknown_charge", _failed_validation_result(), reservation_usd=0.004)
    assert receipt["outcome"] == "failed"
    assert receipt["error_code"] == "typed_output_validation_failed"
    assert receipt["validation_issue_count"] == 2
    # No new-shape diagnostics dict: no allowlisted entries or flag.
    assert receipt["validation_issues"] == []
    assert receipt["validation_truncated"] is None
    assert receipt["partial_outputs"]["arms"] == ["harness"]
    assert receipt["partial_outputs"]["arm_count"] == 1
    _serialized(receipt)


def test_new_shape_malformed_fields_degrade_safely():
    for diag, expected_count in [
        ({"error_code": "typed_output_validation_failed", "issues": "not-a-list", "issue_count": "8", "truncated": "yes"}, None),
        ({"error_code": "typed_output_validation_failed", "issues": [], "issue_count": True, "truncated": 1}, None),
        ({"error_code": "typed_output_validation_failed", "issues": [], "issue_count": -3, "truncated": None}, None),
        ({"error_code": "typed_output_validation_failed", "issues": [{"loc": "answer", "type": "missing", "msg_category": "missing"}], "issue_count": float("nan"), "truncated": False}, None),
        ({"error_code": MARK_ERROR, "issues": None, "issue_count": 0, "truncated": False}, 0),
        ("not-a-dict", None),
    ]:
        result = {"diagnostics": diag, "validation_issue_count": 0, "partial_summary": {"arms": "nope", "arm_count": 5}}
        receipt = build_receipt("failed_unknown_charge", result)
        assert receipt["validation_issue_count"] is None or isinstance(receipt["validation_issue_count"], int), diag
        if isinstance(diag, dict) and diag.get("issue_count") == 0 and diag.get("issues") is None:
            assert receipt["validation_issue_count"] == 0
        elif expected_count is not None:
            assert receipt["validation_issue_count"] == expected_count
        else:
            assert receipt["validation_issue_count"] is None
        assert receipt["validation_issues"] == [] or all(
            set(e) == {"loc", "type", "msg_category"} for e in receipt["validation_issues"]
        ), diag
        assert receipt["validation_truncated"] in (True, False, None), diag
        assert receipt["partial_outputs"] == {"arms": [], "arm_count": 0}, diag
        _serialized(receipt)
    body = _serialized(build_receipt("failed_unknown_charge", {"diagnostics": diag, "partial_summary": None}))
    assert MARK_ERROR not in body


# --- Result-integrity digest: settled-reservation binding, corruption, no-digest ---

_SETTLED_STAGE = {"stage": "harness", "status": spend.RECEIVED, "cost_usd": 0.0003375}


def test_digest_binds_the_settled_spend_reservation():
    """Completion and verification must bind the same reservation value.

    Settlement stores ``ai_spend.reservation`` (the settled value) and the
    receipt reads that column back, so the digest computed at completion
    must cover the settled reservation — not the original job reservation.
    """
    result = _completed_result()
    job_reservation = 5.0
    decision = spend.settle(job_reservation, [_SETTLED_STAGE], "none")
    assert decision.status == "completed_known"
    assert decision.reserved_after != job_reservation, "settlement must move the stored reservation"
    digest = result_digest(result, decision.reserved_after, decision.actual_cost, decision.status)
    assert digest is not None
    assert verify_result_digest(
        digest, result, decision.reserved_after, decision.actual_cost, decision.status
    ) is True
    # Binding the pre-settlement job reservation instead is exactly the defect
    # that made untouched completed jobs report integrity False.
    assert verify_result_digest(
        digest, result, job_reservation, decision.actual_cost, decision.status
    ) is False


def test_digest_detects_mutated_result_and_mutated_spend():
    result = _completed_result()
    digest = result_digest(result, 0.0003375, 0.0003375, "completed_known")
    assert digest is not None
    assert verify_result_digest(digest, result, 0.0003375, 0.0003375, "completed_known") is True
    tampered = json.loads(json.dumps(result))
    tampered["calculated_cost_usd"] = 999.99
    assert verify_result_digest(digest, tampered, 0.0003375, 0.0003375, "completed_known") is False
    assert verify_result_digest(digest, result, 0.0003375, 999.99, "completed_known") is False
    assert verify_result_digest(digest, result, 999.99, 0.0003375, "completed_known") is False
    assert verify_result_digest(digest, result, 0.0003375, 0.0003375, "completed_unknown_charge") is False
    assert build_receipt("completed_known", tampered, result_integrity=False)["result_integrity_verified"] is False


def test_non_canonicalizable_envelope_with_stored_digest_fails_integrity():
    """NaN in the result or infinity in spend is corruption, never a pass-through."""
    result = _completed_result()
    digest = result_digest(result, 0.0003375, 0.0003375, "completed_known")
    assert digest is not None
    nan_result = json.loads(json.dumps(result))
    nan_result["measured_known_cost_usd"] = float("nan")
    assert verify_result_digest(digest, nan_result, 0.0003375, 0.0003375, "completed_known") is False
    assert verify_result_digest(digest, result, 0.0003375, float("inf"), "completed_known") is False
    assert verify_result_digest(digest, result, float("inf"), 0.0003375, "completed_known") is False
    receipt = build_receipt("completed_known", nan_result, result_integrity=False)
    assert receipt["result_integrity_verified"] is False
    assert "changed after completion" in receipt["result_integrity_note"]
    _assert_finite_costs(receipt)
    _serialized(receipt)


def test_no_stored_digest_stays_unverifiable():
    result = _completed_result()
    for missing in (None, "", 0.0, b"digest", ["d"]):
        assert verify_result_digest(missing, result, 0.0003375, 0.0003375, "completed_known") is None
    assert result_digest({"k": float("nan")}, 0.0003375, 0.0003375, "completed_known") is None
    assert result_digest(result, float("inf"), 0.0003375, "completed_known") is None
    assert build_receipt("completed_known", result, result_integrity=None)["result_integrity_verified"] is None
