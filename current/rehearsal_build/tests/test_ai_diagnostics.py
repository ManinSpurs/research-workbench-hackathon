"""Safe validation-diagnostics tests: content-free errors, no raw text retention."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai.diagnostics import (
    MAX_ISSUES,
    partial_output_summary,
    safe_validation_diagnostics,
)
from app.ai.grounding import ResearchResult


SECRET = "sk-ant-S3CR3T-leak-probe-9f8e7d6c5b4a"


class FrameLike(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sources: list[str] = Field(max_length=3)
    gap_type: Literal["fact", "preference", "private_context", "permission", "none"]
    focus: str = Field(max_length=2000)


class EchoValidator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str

    @field_validator("label")
    @classmethod
    def _check(cls, v: str) -> str:
        if v == SECRET:
            raise ValueError(f"rejected label value {v}")
        return v


def _serialized(diag: dict) -> str:
    return json.dumps(diag, sort_keys=True, ensure_ascii=False)


def test_research_result_error_leaks_no_values():
    with pytest.raises(ValidationError) as ei:
        ResearchResult.model_validate(
            {
                "answer": SECRET,
                "claims": [{"text": SECRET, "evidence_ids": "not-a-list", "kind": "fact"}],
                "unresolved": [SECRET],
                "proposed_variables": [],
                "next_action": SECRET,
            }
        )
    diag = safe_validation_diagnostics(ei.value)
    blob = _serialized(diag)
    assert SECRET not in blob
    assert diag["error_code"] == "typed_output_validation_failed"
    assert diag["issue_count"] == len(ei.value.errors(include_input=False))
    assert diag["truncated"] is False
    assert diag["issues"], "expected at least one issue for the bad evidence_ids"
    for issue in diag["issues"]:
        assert set(issue) == {"loc", "type", "msg_category"}
        assert SECRET not in _serialized(issue)


def test_frame_like_invalid_choice_leaks_nothing():
    with pytest.raises(ValidationError) as ei:
        FrameLike.model_validate({"sources": [], "gap_type": SECRET, "focus": SECRET})
    diag = safe_validation_diagnostics(ei.value)
    assert SECRET not in _serialized(diag)
    assert diag["issues"] and all(set(i) == {"loc", "type", "msg_category"} for i in diag["issues"])
    assert diag["truncated"] is False


def test_validator_message_echo_is_stripped():
    with pytest.raises(ValidationError) as ei:
        EchoValidator.model_validate({"label": SECRET})
    diag = safe_validation_diagnostics(ei.value)
    blob = _serialized(diag)
    assert SECRET not in blob
    assert diag["issues"], "expected the wrapped ValueError to surface as an issue"
    assert diag["issues"][0]["msg_category"] == "value_error"
    assert diag["issues"][0]["type"] == "value_error"


def test_non_validation_exception_returns_class_name_only():
    diag = safe_validation_diagnostics(RuntimeError(f"boom {SECRET} with raw text"))
    blob = _serialized(diag)
    assert SECRET not in blob
    assert "boom" not in blob
    assert diag == {
        "error_code": "RuntimeError",
        "issues": [],
        "issue_count": 0,
        "truncated": False,
    }


def test_issue_cap_and_truncation_flag():
    class Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")
        a: int

    payload = {"a": 1}
    for i in range(MAX_ISSUES + 5):
        payload[f"extra_{i}"] = f"{SECRET}-{i}"
    with pytest.raises(ValidationError) as ei:
        Strict.model_validate(payload)
    assert len(ei.value.errors(include_input=False)) > MAX_ISSUES
    diag = safe_validation_diagnostics(ei.value)
    assert diag["issue_count"] > MAX_ISSUES
    assert len(diag["issues"]) == MAX_ISSUES
    assert diag["truncated"] is True
    assert SECRET not in _serialized(diag)


def test_partial_output_summary_keeps_shape_only():
    results = {
        "basic": {
            "result": {
                "answer": SECRET,
                "claims": [
                    {"text": SECRET, "evidence_ids": ["e1"], "kind": "fact"},
                    {"text": "other claim", "evidence_ids": [], "kind": "proposal"},
                ],
            },
            "grounding": {"errors": [f"bad {SECRET}"]},
            "status": "response_received",
            "cost_usd": 0.0123,
        },
        "harness": {
            "result": {"answer": "x", "claims": []},
            "status": "response_received",
        },
        "calculated_cost_usd": 0.02,
    }
    summary = partial_output_summary(results)
    blob = _serialized(summary)
    assert SECRET not in blob
    assert "bad" not in blob
    assert summary["arms"] == ["basic", "harness"]
    assert summary["arm_count"] == 2
    basic = next(s for s in summary["stages"] if s["arm"] == "basic")
    assert basic["claim_count"] == 2
    assert basic["status"] == "response_received"
    assert basic["cost_usd"] == 0.0123
    assert summary["costs"] == {"calculated_cost_usd": 0.02}


def test_top_level_cost_scalar_not_counted_as_arm():
    results = {
        "basic": {
            "result": {"answer": "a", "claims": [{"text": "c", "evidence_ids": [], "kind": "proposal"}]},
            "status": "response_received",
        },
        "harness": {
            "result": {"answer": "b", "claims": []},
            "status": "response_received",
        },
        "calculated_cost_usd": 0.02,
        "usage_complete": None,
        "tags": ["paired"],
        "evaluation": "paired",
    }
    summary = partial_output_summary(results)
    assert summary["arms"] == ["basic", "harness"]
    assert summary["arm_count"] == 2
    assert len(summary["stages"]) == 2
    assert summary["costs"] == {"calculated_cost_usd": 0.02}
    json.dumps(summary, allow_nan=False)


def test_non_finite_costs_excluded_and_strict_json_safe():
    results = {
        "basic": {
            "result": {"answer": "a", "claims": []},
            "status": "response_received",
            "cost_usd": float("inf"),
        },
        "harness": {
            "result": {"answer": "b", "claims": []},
            "status": "response_received",
        },
        "calculated_cost_usd": float("inf"),
        "measured_known_cost_usd": float("-inf"),
        "reservation": float("nan"),
        "reserve": True,
    }
    summary = partial_output_summary(results)
    assert summary["costs"] == {}
    basic = next(s for s in summary["stages"] if s["arm"] == "basic")
    assert "cost_usd" not in basic
    assert summary["arm_count"] == 2
    # Strict-JSON proof: raises ValueError if any inf/nan survived.
    blob = json.dumps(summary, allow_nan=False)
    assert isinstance(blob, str)


def test_extra_field_name_with_secret_collapses_to_placeholder():
    class Closed(BaseModel):
        model_config = ConfigDict(extra="forbid")
        focus: str

    probe_field = f"mystery_{SECRET}_field"
    with pytest.raises(ValidationError) as ei:
        Closed.model_validate({"focus": "ok", probe_field: "x"})
    diag = safe_validation_diagnostics(ei.value)
    blob = json.dumps(diag, allow_nan=False)
    assert SECRET not in blob
    assert probe_field not in blob
    assert diag["issues"], "expected the extra field to surface as an issue"
    assert diag["issues"][0]["loc"] == "<extra_field>"
    assert diag["issues"][0]["type"] == "extra_forbidden"
    assert diag["issues"][0]["msg_category"] == "extra_forbidden"


def test_custom_error_message_with_secret_never_copied():
    class CustomRaised(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: str

        @field_validator("answer")
        @classmethod
        def _check(cls, v: str) -> str:
            raise PydanticCustomError("arbitrary_custom_kind", "rejected because {secret}", {"secret": SECRET})

    with pytest.raises(ValidationError) as ei:
        CustomRaised.model_validate({"answer": "anything"})
    diag = safe_validation_diagnostics(ei.value)
    blob = json.dumps(diag, allow_nan=False)
    assert SECRET not in blob
    assert "rejected because" not in blob
    assert diag["issues"], "expected the custom error to surface as an issue"
    assert diag["issues"][0]["loc"] == "answer"
    assert diag["issues"][0]["type"] == "custom_or_unknown"
    assert diag["issues"][0]["msg_category"] == "custom_or_unknown"


def test_arm_and_status_secrets_collapse_to_placeholder():
    probe_arm = f"basic_{SECRET}"
    probe_status = f"response_received_{SECRET}"
    results = {
        "basic": {
            "result": {"answer": "a", "claims": []},
            "status": "response_received",
        },
        probe_arm: {
            "result": {"answer": SECRET, "claims": []},
            "status": probe_status,
        },
        "calculated_cost_usd": 0.02,
    }
    summary = partial_output_summary(results)
    blob = json.dumps(summary, allow_nan=False)
    assert SECRET not in blob
    assert probe_arm not in blob
    assert probe_status not in blob
    assert summary["arm_count"] == 2
    assert summary["arms"] == ["basic", "<unknown>"]
    by_arm = {s["arm"]: s for s in summary["stages"]}
    assert by_arm["basic"]["status"] == "response_received"
    assert by_arm["<unknown>"]["status"] == "<unknown>"
    assert summary["costs"] == {"calculated_cost_usd": 0.02}
