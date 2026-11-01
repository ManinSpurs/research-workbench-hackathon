"""Contract tests for tools/rehearse_fresh_comparison.py (offline, no paid calls).

The rehearsal tool drives the real ``AIService`` for the three frozen gold cases
of ``current/FRESH_COMPARISON_PREP_EN.md`` §1 with an ``httpx.MockTransport``
provider and a stubbed credential reader, then proves the four refusal paths.

These tests exercise the tool's module-level seams in-process. They do NOT run
the tool as a subprocess by default (it would spawn a second interpreter and a
second copy of the fixture tree); the one subprocess probe is ``--help``, which
neither rehearses nor touches a provider. No network, no API key, no paid call.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "rehearse_fresh_comparison.py"


@pytest.fixture(scope="module")
def tool_module():
    spec = importlib.util.spec_from_file_location("rehearse_fresh_comparison_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the module defines a dataclass, and dataclasses
    # resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def full_run(tool_module, tmp_path_factory):
    """One complete rehearsal shared by the read-only assertions below."""
    workspace = tmp_path_factory.mktemp("rehearsal") / "ws"
    return tool_module.rehearse(mode="full", workspace=workspace)


@pytest.fixture(scope="module")
def negative_run(tool_module, tmp_path_factory):
    workspace = tmp_path_factory.mktemp("rehearsal_negative") / "ws"
    return tool_module.rehearse(mode="negative", workspace=workspace)


def _case(summary, case_id):
    for entry in summary["cases"]:
        if entry["case_id"] == case_id:
            return entry
    raise AssertionError("case %s missing from the summary: %s"
                        % (case_id, [c["case_id"] for c in summary["cases"]]))


# --- plumbing -------------------------------------------------------------


def test_help_exits_zero_and_mentions_both_modes():
    proc = subprocess.run([sys.executable, str(TOOL), "--help"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    assert "--negative" in proc.stdout
    assert "--report" in proc.stdout


# --- full run -------------------------------------------------------------


def test_full_run_reports_ok_for_three_cases(full_run):
    assert full_run["result"] == "REHEARSAL_OK", full_run.get("reason")
    assert full_run["mode"] == "full"
    assert [c["case_id"] for c in full_run["cases"]] == [
        "FX-COST-1", "FX-APPROVAL-1", "NYC-DATE-1"]
    assert all(c["ok"] for c in full_run["cases"])
    assert full_run["totals"]["cases"] == 3


def test_frozen_questions_are_used_verbatim(full_run):
    assert _case(full_run, "FX-COST-1")["question"] == "What does est_cost measure?"
    assert _case(full_run, "FX-APPROVAL-1")["question"] == \
        "Does approval_date mean final approval?"
    assert _case(full_run, "NYC-DATE-1")["question"] == \
        "Can filing_date be used in DOB NOW?"


def test_reservation_matches_the_published_formula(full_run):
    context, input_price, output_price = 1048576, 1.25, 4.25
    per_call = (context * input_price + 2048 * output_price) / 1e6
    expected = round(per_call * 3, 8)
    assert expected == 3.958272
    for case in full_run["cases"]:
        assert case["calls"] == 3
        assert case["reservation_usd"] == expected
        assert case["reservation_formula_usd"] == expected
        assert case["expires_in_seconds"] == 900
        assert case["requires_explicit_consent"] is True
        assert len(case["preview_hash"]) == 64


def test_model_binding_is_standard_spark_and_never_contributor(full_run):
    for case in full_run["cases"]:
        assert case["model"] == "meta-models/muse-spark-open-1026"
        assert case["data_use_mode"] == "Standard"
        assert case["task"] == "research_query"


def test_transmission_scope_is_recorded_per_case(full_run):
    # Only the FX registry carries a blocked list (data/fx/sources.json
    # "blocked": ["OFF-BLOCKED"]); data/nyc/sources.json has none, so the NYC
    # case honestly reports an empty blocked set rather than an invented one.
    expected_blocked = {"FX-COST-1": ["OFF-BLOCKED"],
                        "FX-APPROVAL-1": ["OFF-BLOCKED"],
                        "NYC-DATE-1": []}
    for case in full_run["cases"]:
        scope = case["transmission"]
        assert scope["destination"] == "https://inference.baseten.co/v1"
        assert scope["whole_csv_rows"] is False
        assert scope["includes_canvas_notes_and_question_decisions"] is True
        assert scope["question"] == case["question"]
        assert scope["files"] == [], "frozen cases transmit no media"
        assert scope["local_assets_not_transmitted"], "the local asset stays local"
        assert scope["blocked_sources"] == expected_blocked[case["case_id"]]
        for blocked in scope["blocked_sources"]:
            assert blocked not in scope["allowed_sources"]


def test_nyc_case_reserves_no_filing_date_column(full_run):
    scope = _case(full_run, "NYC-DATE-1")["transmission"]
    assert [d["dataset_id"] for d in scope["datasets"]] == ["nyc/dobnow_2024_subset.csv"]
    columns = [c.lower() for c in scope["datasets"][0]["columns"]]
    assert "filing_date" not in columns
    assert scope["datasets"][0]["columns"] == [
        "job_filing_number", "work_permit", "sequence_number", "filing_reason",
        "work_type", "borough", "approved_date", "issued_date", "expired_date",
        "permit_status"]


def test_fx_cases_select_only_the_question_dataset(full_run):
    for case_id in ("FX-COST-1", "FX-APPROVAL-1"):
        scope = _case(full_run, case_id)["transmission"]
        assert [d["dataset_id"] for d in scope["datasets"]] == ["fx/permits_2023.csv"]
        assert "est_cost" in scope["datasets"][0]["columns"]


def test_gold_evidence_is_in_scope_and_hostile_text_excluded_from_blocked(full_run):
    fx = _case(full_run, "FX-COST-1")["transmission"]
    assert "DD-2023:est_cost" in fx["evidence_ids"]
    approval = _case(full_run, "FX-APPROVAL-1")["transmission"]
    for evidence_id in ("DD-2023:approval_date", "OFF-FAQ-2023", "OFF-NOTES-2025"):
        assert evidence_id in approval["evidence_ids"]
    nyc = _case(full_run, "NYC-DATE-1")["transmission"]
    assert "DD-DOBNOW-2018:Column Info:Approved Date" in nyc["evidence_ids"]
    assert not [e for e in nyc["evidence_ids"] if "Filing Date" in e]


def test_arm_order_alternates_and_matches_request_storage_and_execution(full_run):
    expected = {"FX-COST-1": ["basic", "harness"],
                "FX-APPROVAL-1": ["harness", "basic"],
                "NYC-DATE-1": ["basic", "harness"]}
    for case in full_run["cases"]:
        order = case["execution_order"]
        assert order["requested"] == expected[case["case_id"]]
        assert order["stored"] == expected[case["case_id"]]
        assert order["executed"] == expected[case["case_id"]]
        assert order["matches"] is True


def test_execute_costs_settle_and_receipt_agrees_with_the_ledger(full_run):
    # per_call = (200 * 1.25 + 100 * 4.25) / 1e6; three calls per paired job.
    expected_settled = round(((200 * 1.25 + 100 * 4.25) / 1e6) * 3, 8)
    assert expected_settled == 0.002025
    # A paired job always makes exactly three calls; harness-first runs the
    # frame stage before its own final call, so the stage list follows the arms.
    expected_stages = {"FX-COST-1": ["basic", "frame", "harness"],
                       "FX-APPROVAL-1": ["frame", "harness", "basic"],
                       "NYC-DATE-1": ["basic", "frame", "harness"]}
    for case in full_run["cases"]:
        assert case["settled_usd"] == expected_settled
        assert case["settled_cost_basis"].startswith("Calculated from published rates")
        assert [s["stage"] for s in case["stages"]] == expected_stages[case["case_id"]]
        assert all(s["status"] == "response_received" for s in case["stages"])
        assert all(s["cost_usd"] == 0.000675 for s in case["stages"])
        receipt = case["receipt"]
        assert receipt["job_status"] == "completed_known"
        assert receipt["reservation_usd"] == 3.958272
        assert receipt["calculated_cost_usd"] == expected_settled
        assert receipt["measured_known_cost_usd"] == expected_settled
        assert receipt["provider_billed_cost_usd"] is None
        assert receipt["billing_status"] == "NOT_VERIFIED"
        assert receipt["result_integrity_verified"] is True
        spend = case["ai_spend"]
        assert spend["status"] == "completed_known"
        assert spend["actual_cost"] == expected_settled
        assert spend["reservation"] == expected_settled
        assert spend["consistent"] is True


def test_contract_fingerprint_matches_the_frozen_pin(full_run):
    for case in full_run["cases"]:
        assert case["contract_sha256"] == \
            "06143efc04959e5f9f243e88dd036daec913f63a733a562515a20b1b42f24fdf"
        assert case["contract_sha256_matches_frozen_fingerprint"] is True


def test_no_network_and_no_real_key(full_run):
    network = full_run["network"]
    assert network["mode"] == "blocked"
    assert network["transport"] == "httpx.MockTransport"
    assert network["provider_calls"] == full_run["totals"]["provider_calls"]
    assert network["provider_calls"] > 0, "the rehearsal must exercise the transport"
    assert all(path.endswith("/responses") or path.endswith("/models")
               for path in network["endpoints"])
    credentials = full_run["credentials"]
    assert credentials["real_key_reader_consulted"] is False
    assert credentials["logged"] is False


def test_fixture_missing_is_declared_not_invented(full_run):
    declared = full_run["fixture_missing"]
    assert isinstance(declared, list) and declared
    joined = " ".join(declared)
    for marker in ("max_output_tokens", "max_cost_usd", "pack", "reviewer",
                   "canvas", "version string"):
        assert marker in joined, marker
    body = json.dumps(full_run, allow_nan=False)
    assert "sk-ant" not in body and "rehearsal-not-a-real-key" not in body


# --- refusal paths --------------------------------------------------------


def test_negative_mode_proves_all_four_refusals(negative_run):
    assert negative_run["result"] == "REHEARSAL_OK", negative_run.get("reason")
    assert negative_run["mode"] == "negative"
    negative = negative_run["negative"]
    assert set(negative) == {"wrong_preview_hash", "double_execute",
                             "expired_preview", "reserve_over_cap"}
    for name, entry in negative.items():
        assert entry["refused"] is True, name
        assert entry["provider_calls"] == 0, name


def test_refusal_messages_are_the_documented_ones(negative_run):
    negative = negative_run["negative"]
    assert "Exact preview confirmation is required" in \
        negative["wrong_preview_hash"]["error"]
    assert "no automatic retry" in negative["double_execute"]["error"]
    assert "replay" in negative["double_execute"]["error"]
    assert "expired" in negative["expired_preview"]["error"].lower()
    assert negative["expired_preview"]["simulated_age_seconds"] == 901
    assert "exceeds this task cap" in negative["reserve_over_cap"]["error"]
    assert negative["reserve_over_cap"]["reservation_usd"] == 3.958272
    assert negative["reserve_over_cap"]["requested_max_cost_usd"] == 1.0


# --- report flag ----------------------------------------------------------


def test_report_file_is_written_and_json_only(tool_module, tmp_path):
    workspace = tmp_path / "ws"
    report = tmp_path / "out" / "summary.json"
    exit_code = tool_module.main(["--negative", "--report", str(report),
                                  "--workdir", str(workspace), "--keep-workdir"])
    assert exit_code == 0
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["result"] == "REHEARSAL_OK"
    assert written["mode"] == "negative"
    assert (workspace / "rehearsal.db").is_file(), "--keep-workdir keeps the DB"
