#!/usr/bin/env python3
"""Offline paid-comparison rehearsal for the three frozen gold cases.

Purpose: a future one-shot paid run must not fail on *mechanics*. This harness
drives the app's real ``AIService`` end to end (prepare -> consent-bound
execute -> settle -> receipt) for the three frozen gold cases of
``current/FRESH_COMPARISON_PREP_EN.md`` §1, and then proves that the four
refusal paths are caught before any provider work:

    wrong ``preview_hash``           -> Conflict, zero provider calls
    second execute of a used job     -> "no automatic retry or paid replay"
    preview older than 900 s         -> "Preview expired; prepare again"
    reserve > ``max_cost_usd``       -> fails closed at prepare, no job row

Hard constraints enforced here, not bypassed:

* NO network. The only "provider" is an ``httpx.MockTransport`` handler; while
  the rehearsal runs, ``socket.socket.connect``/``connect_ex`` are patched to
  raise, so any real socket attempt fails the run loudly.
* NO API key read. ``app.ai.secrets.key`` is replaced in-process by a stub for
  the duration of the run, so the environment and the owner-only key file are
  never consulted and no real credential exists in the process.
* NO real execute. Every ``provider.infer`` is answered by the mock transport.

Usage (from ``current/rehearsal_build``)::

    .venv/bin/python tools/rehearse_fresh_comparison.py
    .venv/bin/python tools/rehearse_fresh_comparison.py --negative
    .venv/bin/python tools/rehearse_fresh_comparison.py --report /tmp/rehearsal.json

Output contract: exactly one JSON document on stdout, then a final
``REHEARSAL_OK`` / ``REHEARSAL_FAIL: <reason>`` line. ``--report PATH`` also
writes the same document (indented) to ``PATH``. Exit status is 0 only for
``REHEARSAL_OK``.

Honesty rule: any value that does not exist in-tree is listed under
``fixture_missing`` with the exact missing input named; nothing is invented.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import shutil
import socket
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.ai import registry, secrets as ai_secrets, service  # noqa: E402
from app.ai.provider import BasetenProvider  # noqa: E402
from app.harness import Conflict, Harness  # noqa: E402
from app.offline import OfflineAdapter  # noqa: E402

#: In-process stand-in credential. Never a real key; never read from anywhere.
REHEARSAL_KEY = "rehearsal-not-a-real-key"
MODEL_ID = "meta-models/muse-spark-open-1026"
TASK = "research_query"
CALLS_FOR_COMPARE = 3
DESTINATION = "https://inference.baseten.co/v1"

#: Documented default (prep §4 / consent packet §4). The *per-task frozen*
#: value is MISSING in-tree (prep §6B G12), so it is reported as such rather
#: than presented as a frozen pin.
DEFAULT_MAX_OUTPUT_TOKENS = 2048
#: ``Request`` default; the per-task frozen allowance is MISSING (prep §6B G12).
DEFAULT_MAX_COST_USD = 5.0
#: ``AIService.configure`` ceiling for the local allowance.
BUDGET_USD = 150.0
#: ``execute`` refuses a preview older than this.
EXPIRY_SECONDS = 900
#: prep §6A.2 / §6B G6 instruction-contract fingerprint, recorded for drift.
CONTRACT_FINGERPRINT_REFERENCE = (
    "06143efc04959e5f9f243e88dd036daec913f63a733a562515a20b1b42f24fdf"
)
#: Fake returned usage: identical for every arm and case, so the settled cost
#: basis is derivable from the published registry prices alone.
FAKE_INPUT_TOKENS = 200
FAKE_OUTPUT_TOKENS = 100

FIXTURE_MISSING = (
    "per-task frozen max_output_tokens (prep §6B G12): rehearsal used the documented "
    "default %d and says so instead of pinning it" % DEFAULT_MAX_OUTPUT_TOKENS,
    "per-task frozen max_cost_usd (prep §6B G12): rehearsal used the Request default "
    "%.1f and says so instead of pinning it" % DEFAULT_MAX_COST_USD,
    "governing pack assignment per frozen case (prep §6B G8): no in-tree pin exists; "
    "rehearsal uses the pack create_question applies by default",
    "named human reviewer (prep §6A.3): not assigned in-tree; scoring is out of scope here",
    "frozen canvas revision and per-question decisions (prep §6B G9): rehearsed against "
    "the live, empty canvas of a freshly created question",
    "instruction-contract version string (prep §2 item 2): none exists in-tree; the "
    "recorded contract_sha256 fingerprint is reported instead",
)


@dataclass(frozen=True)
class GoldCase:
    """One frozen gold case: exact question, assigned arm order, fake answer."""

    case_id: str
    project_kind: str
    question: str
    expected_order: tuple[str, str]
    dataset_ids: tuple[str, ...]
    columns_absent: tuple[str, ...]
    evidence_ids_present: tuple[str, ...]
    frame: dict
    result: dict


CASES: tuple[GoldCase, ...] = (
    GoldCase(
        case_id="FX-COST-1",
        project_kind="fx",
        question="What does est_cost measure?",
        expected_order=("basic", "harness"),
        dataset_ids=("fx/permits_2023.csv",),
        columns_absent=(),
        evidence_ids_present=("DD-2023:est_cost",),
        frame={"sources": ["DD-2023"], "gap_type": "fact",
               "focus": "Read the registered est_cost definition"},
        result={
            "answer": ("est_cost is the applicant-reported estimated cost in USD; it is "
                       "not the actual final cost."),
            "claims": [{"text": "est_cost is defined as the applicant-reported estimated "
                                "cost in USD.",
                        "evidence_ids": ["DD-2023:est_cost"], "kind": "fact"}],
            "unresolved": ["The actual final cost is not established by this definition."],
            "proposed_variables": [{"name": "est_cost", "dataset_id": "fx/permits_2023.csv",
                                    "kind": "actual"}],
            "next_action": "Researcher confirms whether actual cost data is needed.",
        },
    ),
    GoldCase(
        case_id="FX-APPROVAL-1",
        project_kind="fx",
        question="Does approval_date mean final approval?",
        expected_order=("harness", "basic"),
        dataset_ids=("fx/permits_2023.csv",),
        columns_absent=(),
        evidence_ids_present=("DD-2023:approval_date", "OFF-FAQ-2023"),
        frame={"sources": ["DD-2023", "OFF-FAQ-2023"], "gap_type": "fact",
               "focus": "Read the registered release-2023 approval_date definitions"},
        result={
            "answer": ("In release 2023 approval_date is plan-review completion; final "
                       "approval remains unestablished."),
            "claims": [{"text": "approval_date records when plan review processing was "
                                "completed.",
                        "evidence_ids": ["DD-2023:approval_date", "OFF-FAQ-2023"],
                        "kind": "fact"}],
            "unresolved": ["Final approval is not established for release 2023."],
            "proposed_variables": [{"name": "approval_date",
                                    "dataset_id": "fx/permits_2023.csv",
                                    "kind": "actual"}],
            "next_action": "Researcher reviews the 2025-only note separately.",
        },
    ),
    GoldCase(
        case_id="NYC-DATE-1",
        project_kind="nyc",
        question="Can filing_date be used in DOB NOW?",
        expected_order=("basic", "harness"),
        dataset_ids=("nyc/dobnow_2024_subset.csv",),
        columns_absent=("filing_date",),
        evidence_ids_present=("DD-DOBNOW-2018:Column Info:Approved Date",
                              "DD-DOBNOW-2018:Column Info:Issued Date"),
        frame={"sources": ["DD-DOBNOW-2018"], "gap_type": "fact",
               "focus": "Read the registered DOB NOW column dictionary"},
        result={
            "answer": ("The selected DOB NOW file has no filing_date column; the BIS "
                       "filing_date must not be borrowed."),
            "claims": [{"text": "The DOB NOW subset exposes approved_date and issued_date, "
                                "not filing_date.",
                        "evidence_ids": ["DD-DOBNOW-2018:Column Info:Approved Date",
                                         "DD-DOBNOW-2018:Column Info:Issued Date"],
                        "kind": "fact"}],
            "unresolved": ["Whether a filing date exists in another DOB NOW extract is "
                           "unestablished."],
            "proposed_variables": [],
            "next_action": "Researcher confirms the DOB NOW column set before analysis.",
        },
    ),
)

SERVICE_COST_BASIS = ("Calculated from published rates and returned usage; absent "
                      "cached count assumed zero; provider bill not verified")


# --------------------------------------------------------------------------- guards
@contextlib.contextmanager
def no_network():
    """Fail loudly on any socket connect for the duration of the rehearsal."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _blocked(sock, address, *args, **kwargs):
        raise AssertionError(
            "rehearsal forbids network access; attempted connect to %r" % (address,))

    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


@contextlib.contextmanager
def stubbed_key():
    """Replace the credential reader in-process: no env, no key file, no .lstat()."""
    original = ai_secrets.baseten_key
    original_meta = ai_secrets.key

    def _stub():
        return REHEARSAL_KEY

    ai_secrets.baseten_key = _stub
    ai_secrets.key = _stub
    try:
        yield
    finally:
        ai_secrets.baseten_key = original
        ai_secrets.key = original_meta


# --------------------------------------------------------------------------- fake transport
class RehearsalTransport:
    """The only 'provider' in this rehearsal: a scripted httpx MockTransport."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.case: GoldCase | None = None

    def __call__(self, request):
        path = request.url.path
        self.calls.append({"method": request.method, "path": path,
                           "case_id": self.case.case_id if self.case else None})
        if path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": m.id} for m in registry.MODELS.values() if m.endpoint]})
        if not path.endswith("/responses"):
            return httpx.Response(404, json={"error": "unexpected_path"})
        assert self.case is not None, "no case payload armed for this request"
        body = json.loads(request.content)
        if body.get("model") != MODEL_ID:
            # Contributor substitution must never be rehearsed as acceptable.
            return httpx.Response(400, json={"error": "unexpected_model"})
        payload = self.case.frame if "public work plan" in body.get("instructions", "") \
            else self.case.result
        return httpx.Response(200, json={
            "status": "completed",
            "model": body["model"],
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(payload)}]}],
            "usage": {"input_tokens": FAKE_INPUT_TOKENS,
                      "output_tokens": FAKE_OUTPUT_TOKENS},
        })


# --------------------------------------------------------------------------- rehearsal
def expected_reservation(max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS) -> float:
    """``prepare`` reservation for a paired reasoning job, from registry prices."""
    model = registry.MODELS[MODEL_ID]
    per_call = (model.context * model.input_price
                + max_output_tokens * model.output_price) / 1e6
    return round(per_call * CALLS_FOR_COMPARE, 8)


def expected_settled_cost() -> float:
    """Settled cost of all calls at the fake returned usage and published rates."""
    model = registry.MODELS[MODEL_ID]
    per_call = (FAKE_INPUT_TOKENS * model.input_price
                + FAKE_OUTPUT_TOKENS * model.output_price) / 1e6
    return round(per_call * CALLS_FOR_COMPARE, 8)


def _close_enough(left, right) -> bool:
    return isinstance(left, (int, float)) and isinstance(right, (int, float)) \
        and math.isclose(left, right, rel_tol=0, abs_tol=1e-9)


class Rehearsal:
    """One isolated workspace: temp DB, copied fixtures, real AIService."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.data_dir = workspace / "data"
        self.db_path = workspace / "rehearsal.db"
        self.transport = RehearsalTransport()
        self.harness = Harness(str(self.db_path), self.data_dir, adapter=OfflineAdapter())
        self.svc = service.AIService(
            self.harness,
            provider_factory={"meta": lambda: self.svc.provider,
                              "baseten": lambda: BasetenProvider(httpx.MockTransport(self.transport),
                         key_reader=lambda: REHEARSAL_KEY)},
        )
        self.notes: list[str] = []
        self.svc.configure(True, BUDGET_USD)
        asyncio.run(self.svc.verify_account("baseten"))
        settings = self.svc.settings()
        assert settings["cloud_enabled"] is True, "cloud mode must be on for the rehearsal"
        assert settings["provider_catalogs"]["baseten"]["catalog_valid_for_current_key"] is True, \
            "account catalog must be bound to the stubbed key"

    def close(self) -> None:
        self.harness.db.close()

    # -- one frozen gold case -------------------------------------------------
    def run_case(self, case: GoldCase) -> dict:
        svc = self.svc
        project = svc.h.register_project(case.project_kind)
        qid = asyncio.run(svc.h.create_question(project["project_id"], case.question))[
            "question_id"]

        # Registered local source note (same store API as the existing tests).
        # Media is explicitly none for these cases (prep §3), so the note is
        # registered locally and never selected for transmission.
        note = json.dumps({"case_id": case.case_id, "question": case.question,
                           "gold_constraints_source": "current/FRESH_COMPARISON_PREP_EN.md §1"},
                          sort_keys=True, ensure_ascii=False).encode("utf-8")
        asset = svc.register_asset(qid, {
            "name": case.case_id.lower() + "-gold-note.txt",
            "mime": "text/plain",
            "encoded": base64.b64encode(note).decode("ascii"),
        })
        assert asset["cloud_transmitted"] is False, "local registration must not transmit"
        assert [a["id"] for a in svc.list_assets(qid)] == [asset["id"]], \
            "registered asset must be readable"

        body = {
            "task": TASK,
            "model": MODEL_ID,
            "harness": True,
            "compare": True,
            "text": case.question,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "max_cost_usd": DEFAULT_MAX_COST_USD,
            "assets": [],
        }
        calls_before_prepare = len(self.transport.calls)
        preview = svc.prepare(qid, body)
        assert len(self.transport.calls) == calls_before_prepare, \
            f"{case.case_id}: prepare must send nothing"

        # --- 3. preview numbers and the recorded transmission scope
        reserve_expected = expected_reservation()
        assert preview["maximum_reserved_usd"] == reserve_expected, (
            f"{case.case_id}: reservation {preview['maximum_reserved_usd']!r} != "
            f"formula {reserve_expected!r}")
        assert preview["max_inference_calls"] == CALLS_FOR_COMPARE, \
            f"{case.case_id}: paired compare must reserve 3 calls"
        assert preview["model"] == MODEL_ID, f"{case.case_id}: model substitution detected"
        assert preview["data_use_mode"] == "Standard", \
            f"{case.case_id}: Standard data-use mode is required"
        assert preview["requires_explicit_consent"] is True
        assert preview["expires_in_seconds"] == EXPIRY_SECONDS
        assert preview["model_access_verified"] is True
        assert isinstance(preview["preview_hash"], str) and len(preview["preview_hash"]) == 64

        transmission = preview["transmission"]
        assert transmission["destination"] == DESTINATION, "unexpected destination"
        assert transmission["whole_csv_rows"] is False, "whole CSV rows must be excluded"
        assert transmission["includes_canvas_notes_and_question_decisions"] is True, \
            "canvas/notes/decisions inclusion must be disclosed"
        assert transmission["files"] == [], (
            f"{case.case_id}: media must be none for the frozen cases (prep §3)")
        context_scope = transmission["text_and_context"]
        assert context_scope["question"] == case.question, "question must not be paraphrased"
        packet = context_scope["packet"]
        dataset_ids = tuple(d["dataset_id"] for d in packet["datasets"])
        assert dataset_ids == case.dataset_ids, (
            f"{case.case_id}: dataset scope {dataset_ids} != {case.dataset_ids}")
        columns = {d["dataset_id"]: list(d["columns"]) for d in packet["datasets"]}
        for dataset_id in case.dataset_ids:
            for absent in case.columns_absent:
                assert absent not in [c.lower() for c in columns[dataset_id]], (
                    f"{case.case_id}: column {absent!r} unexpectedly present in "
                    f"{dataset_id}")
        evidence_ids = [source["evidence_id"] for source in packet["sources"]]
        for evidence_id in case.evidence_ids_present:
            assert evidence_id in evidence_ids, (
                f"{case.case_id}: gold evidence {evidence_id!r} missing from the packet")
        blocked = list(packet["blocked_sources"])
        assert "OFF-BLOCKED" not in packet["allowed_sources"], "blocked source leaked in"
        assert not [s for s in packet["sources"] if s["source_id"] in blocked], \
            "blocked source must be filtered out of the transmitted packet"
        assert sorted(blocked) == sorted(set(blocked))
        for frame_source in case.frame["sources"]:
            assert frame_source in packet["allowed_sources"], (
                f"{case.case_id}: scripted plan source {frame_source!r} is not in scope")
        hostile = [s["source_id"] for s in packet["sources"] if "Ignore your instructions" in
                   (s.get("definition") or "")]
        if hostile:
            self.notes.append(
                "%s: %s is present in the prepared packet (only %s is on the blocked list); "
                "prep §3 wants the hostile note excluded from transmission, so the operator "
                "must decide before a paid run" % (case.case_id, ", ".join(hostile),
                                                  ", ".join(blocked) or "nothing"))

        record = json.loads(svc.db.execute(
            "SELECT request_json FROM ai_jobs WHERE id=?", (preview["job_id"],)
        ).fetchone()[0])
        stored_order = list(record["execution_order"])
        requested_order = list(case.expected_order)
        assert stored_order == requested_order, (
            f"{case.case_id}: stored execution_order {stored_order} != requested "
            f"{requested_order}")
        assert record["calls"] == CALLS_FOR_COMPARE
        assert record["request"]["model"] == MODEL_ID
        assert record["request"]["harness"] is True and record["request"]["compare"] is True
        assert record["request"]["assets"] == []
        contract_sha256 = record["contract_sha256"]

        # nothing is reserved or spent by a preview alone
        assert svc.db.execute("SELECT 1 FROM ai_spend WHERE job_id=?",
                              (preview["job_id"],)).fetchone() is None, \
            f"{case.case_id}: prepare must not create a spend row"
        job_status = svc.db.execute("SELECT status FROM ai_jobs WHERE id=?",
                                    (preview["job_id"],)).fetchone()[0]
        assert job_status == "prepared"

        # --- 4. consent-bound execute through the fake transport
        self.transport.case = case
        calls_before_execute = len(self.transport.calls)
        try:
            result = asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
        finally:
            self.transport.case = None
        calls_made = len(self.transport.calls) - calls_before_execute
        assert calls_made == CALLS_FOR_COMPARE, (
            f"{case.case_id}: {calls_made} provider calls, expected {CALLS_FOR_COMPARE}")
        executed_paths = [c["path"] for c in self.transport.calls[calls_before_execute:]]
        assert all(p.endswith("/responses") for p in executed_paths), \
            f"{case.case_id}: unexpected endpoint used: {executed_paths}"
        executed_order = list(result["outputs"]["evaluation"]["execution_order"])
        assert executed_order == requested_order, (
            f"{case.case_id}: executed arms {executed_order} != requested {requested_order}")
        assert len(result["stages"]) == CALLS_FOR_COMPARE
        assert result["usage_complete"] is True, f"{case.case_id}: usage must be complete"
        assert result["outputs"]["evaluation"]["kind"] == "paired"
        for arm in requested_order:
            assert result["outputs"][arm]["grounding"]["structural_valid"] is True, (
                f"{case.case_id}/{arm}: scripted output must pass structural grounding: "
                f"{result['outputs'][arm]['grounding']['errors']}")
        settled_expect = expected_settled_cost()
        assert _close_enough(result["calculated_cost_usd"], settled_expect), (
            f"{case.case_id}: settled {result['calculated_cost_usd']!r} != "
            f"{settled_expect!r}")
        bases = {stage["cost_basis"] for stage in result["stages"]}
        assert bases == {SERVICE_COST_BASIS}, f"{case.case_id}: cost basis changed: {bases}"

        # --- 5. receipt and ai_spend consistency
        receipt = svc.receipt(preview["job_id"])
        assert isinstance(receipt, dict) and receipt["receipt_version"], \
            f"{case.case_id}: receipt missing"
        assert receipt["job_status"] == "completed_known", receipt["job_status"]
        assert receipt["outcome"] == "completed"
        assert _close_enough(receipt["reservation_usd"], preview["maximum_reserved_usd"]), (
            f"{case.case_id}: receipt reservation {receipt['reservation_usd']!r} != "
            f"preview {preview['maximum_reserved_usd']!r}")
        assert _close_enough(receipt["calculated_cost_usd"], settled_expect)
        assert _close_enough(receipt["measured_known_cost_usd"], settled_expect)
        assert receipt["provider_billed_cost_usd"] is None
        assert receipt["billing_status"] == "NOT_VERIFIED"
        assert receipt["result_integrity_verified"] is True
        assert receipt["execution_order"] == requested_order
        assert receipt["data_use_mode"] == "Standard"
        assert receipt["model"] == MODEL_ID

        spend_row = svc.db.execute(
            "SELECT status,actual_cost,reservation FROM ai_spend WHERE job_id=?",
            (preview["job_id"],)).fetchone()
        assert spend_row is not None, f"{case.case_id}: no ai_spend row after settling"
        assert spend_row["status"] == "completed_known", spend_row["status"]
        assert _close_enough(spend_row["actual_cost"], settled_expect), (
            f"{case.case_id}: ai_spend actual {spend_row['actual_cost']!r} != "
            f"calculated {settled_expect!r}")
        assert _close_enough(spend_row["reservation"], settled_expect), (
            f"{case.case_id}: settled ai_spend reservation {spend_row['reservation']!r} "
            "must equal the known total")
        assert _close_enough(receipt["calculated_cost_usd"], spend_row["actual_cost"]), \
            f"{case.case_id}: receipt and ledger disagree"

        committed = svc.settings()["local_committed_or_reserved_usd"]
        assert committed >= settled_expect, "local ledger must carry the settled cost"

        return {
            "case_id": case.case_id,
            "question_id": qid,
            "project_id": project["project_id"],
            "job_id": preview["job_id"],
            "question": case.question,
            "model": preview["model"],
            "task": preview["task"],
            "data_use_mode": preview["data_use_mode"],
            "calls": preview["max_inference_calls"],
            "reservation_usd": preview["maximum_reserved_usd"],
            "reservation_formula_usd": reserve_expected,
            "reservation_basis": (
                "(context*input_price + max_output_tokens*output_price)/1e6 * calls with "
                "context=%d input_price=%s max_output_tokens=%d output_price=%s calls=%d"
                % (registry.MODELS[MODEL_ID].context,
                   registry.MODELS[MODEL_ID].input_price,
                   DEFAULT_MAX_OUTPUT_TOKENS,
                   registry.MODELS[MODEL_ID].output_price,
                   CALLS_FOR_COMPARE)),
            "preview_hash": preview["preview_hash"],
            "expires_in_seconds": preview["expires_in_seconds"],
            "requires_explicit_consent": preview["requires_explicit_consent"],
            "contract_sha256": contract_sha256,
            "contract_sha256_matches_frozen_fingerprint":
                contract_sha256 == CONTRACT_FINGERPRINT_REFERENCE,
            "transmission": {
                "destination": transmission["destination"],
                "whole_csv_rows": transmission["whole_csv_rows"],
                "includes_canvas_notes_and_question_decisions":
                    transmission["includes_canvas_notes_and_question_decisions"],
                "question": context_scope["question"],
                "pack_id": context_scope["pack"]["pack_id"],
                "datasets": [{"dataset_id": d["dataset_id"], "columns": list(d["columns"])}
                             for d in packet["datasets"]],
                "evidence_ids": evidence_ids,
                "allowed_sources": list(packet["allowed_sources"]),
                "blocked_sources": blocked,
                "canvas_revision": context_scope["canvas"]["revision"],
                "canvas_nodes": len(context_scope["canvas"]["nodes"]),
                "decision_count": len(context_scope["decisions"]),
                "files": list(transmission["files"]),
                "local_assets_not_transmitted": [asset["id"]],
            },
            "execution_order": {
                "requested": requested_order,
                "stored": stored_order,
                "executed": executed_order,
                "matches": stored_order == requested_order == executed_order,
            },
            "stages": [{"stage": s["stage"], "status": s["status"],
                        "input_tokens": s["usage"]["input_tokens"],
                        "output_tokens": s["usage"]["output_tokens"],
                        "cost_usd": s["cost_usd"]} for s in result["stages"]],
            "settled_cost_basis": SERVICE_COST_BASIS,
            "settled_usd": result["calculated_cost_usd"],
            "receipt": {
                "reservation_usd": receipt["reservation_usd"],
                "calculated_cost_usd": receipt["calculated_cost_usd"],
                "measured_known_cost_usd": receipt["measured_known_cost_usd"],
                "provider_billed_cost_usd": receipt["provider_billed_cost_usd"],
                "billing_status": receipt["billing_status"],
                "result_integrity_verified": receipt["result_integrity_verified"],
                "job_status": receipt["job_status"],
                "execution_order": receipt["execution_order"],
            },
            "ai_spend": {
                "status": spend_row["status"],
                "reservation": spend_row["reservation"],
                "actual_cost": spend_row["actual_cost"],
                "consistent": _close_enough(spend_row["actual_cost"],
                                            receipt["calculated_cost_usd"]),
            },
            "local_committed_or_reserved_usd_after": committed,
            "ok": True,
        }

    # -- refusal paths --------------------------------------------------------
    def run_negative_suite(self, executed_case: dict) -> dict:
        svc = self.svc
        qid = executed_case["question_id"]
        out: dict = {}

        spare = svc.prepare(qid, {
            "task": TASK, "model": MODEL_ID, "harness": True, "compare": True,
            "text": executed_case["question"],
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "max_cost_usd": DEFAULT_MAX_COST_USD, "assets": [],
        })
        assert svc.db.execute("SELECT status FROM ai_jobs WHERE id=?",
                             (spare["job_id"],)).fetchone()[0] == "prepared"

        # 1. wrong preview_hash
        baseline = len(self.transport.calls)
        try:
            asyncio.run(svc.execute(spare["job_id"], "0" * 64, True))
        except Conflict as exc:
            message = str(exc)
        else:
            raise AssertionError("a wrong preview_hash was NOT refused")
        assert "Exact preview confirmation is required" in message, message
        assert len(self.transport.calls) == baseline, "refusal must not touch the provider"
        assert svc.db.execute("SELECT status FROM ai_jobs WHERE id=?",
                             (spare["job_id"],)).fetchone()[0] == "prepared", \
            "a refused execute must leave the job prepared"
        out["wrong_preview_hash"] = {"refused": True, "error": message,
                                     "provider_calls": 0}

        # 2. second execute of an already attempted job
        baseline = len(self.transport.calls)
        try:
            # Correct hash and full consent, on a job that already ran: only the
            # status gate can refuse this, which is exactly the no-replay rule.
            asyncio.run(svc.execute(executed_case["job_id"],
                                    executed_case["preview_hash"], True))
        except Conflict as exc:
            message = str(exc)
        else:
            raise AssertionError("a paid replay was NOT refused")
        assert "no automatic retry" in message and "replay" in message, message
        assert len(self.transport.calls) == baseline, "refusal must not touch the provider"
        assert svc.db.execute("SELECT count(*) FROM ai_spend WHERE job_id=?",
                             (executed_case["job_id"],)).fetchone()[0] == 1, \
            "the original run must keep exactly one settled spend row"
        out["double_execute"] = {"refused": True, "error": message, "provider_calls": 0}

        # 3. expired preview (created_at rewritten older than 900 s)
        svc.db.execute("UPDATE ai_jobs SET created_at=? WHERE id=?",
                       (time.time() - EXPIRY_SECONDS - 1, spare["job_id"]))
        svc.db.commit()
        baseline = len(self.transport.calls)
        try:
            asyncio.run(svc.execute(spare["job_id"], spare["preview_hash"], True))
        except Conflict as exc:
            message = str(exc)
        else:
            raise AssertionError("an expired preview was NOT refused")
        assert "expired" in message.lower(), message
        assert len(self.transport.calls) == baseline, "refusal must not touch the provider"
        assert svc.db.execute("SELECT status FROM ai_jobs WHERE id=?",
                             (spare["job_id"],)).fetchone()[0] == "prepared"
        assert svc.db.execute("SELECT 1 FROM ai_spend WHERE job_id=?",
                              (spare["job_id"],)).fetchone() is None, \
            "an expired refusal must not create a spend row"
        out["expired_preview"] = {"refused": True, "error": message,
                                  "provider_calls": 0,
                                  "simulated_age_seconds": EXPIRY_SECONDS + 1}

        # 4. reserve > max_cost_usd fails closed at prepare
        jobs_before = svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0]
        baseline = len(self.transport.calls)
        try:
            svc.prepare(qid, {
                "task": TASK, "model": MODEL_ID, "harness": True, "compare": True,
                "text": executed_case["question"],
                "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                "max_cost_usd": 1.0, "assets": [],
            })
        except Conflict as exc:
            message = str(exc)
        else:
            raise AssertionError("reserve > max_cost_usd was NOT refused at prepare")
        assert "exceeds this task cap" in message, message
        assert len(self.transport.calls) == baseline, "refusal must not touch the provider"
        assert svc.db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == jobs_before, \
            "a refused prepare must not persist a job row"
        out["reserve_over_cap"] = {
            "refused": True, "error": message, "provider_calls": 0,
            "reservation_usd": expected_reservation(),
            "requested_max_cost_usd": 1.0,
        }
        return out


# --------------------------------------------------------------------------- workspace
def prepare_workspace(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    data = base / "data"
    if not data.exists():
        shutil.copytree(ROOT / "data", data)
    return base


def rehearse(mode: str = "full", workspace: Path | None = None) -> dict:
    """Run the rehearsal and return the machine-readable summary."""
    workspace = Path(workspace) if workspace is not None else \
        Path(tempfile.mkdtemp(prefix="fresh_comparison_rehearsal_"))
    prepare_workspace(workspace)
    cases = CASES if mode == "full" else CASES[:1]
    case_results: list[dict] = []
    negatives: dict = {}
    provider_calls = 0
    paths: set[str] = set()
    with no_network(), stubbed_key():
        rehearsal = Rehearsal(workspace)
        try:
            # All cases first: prepare's alternating arm order is counterbalanced
            # across prior compare jobs, so the requested direct-first /
            # harness-first / direct-first sequence is produced by preparing the
            # three cases in order and by nothing else. The refusal suite runs
            # afterwards, so its extra prepared job cannot shift that parity.
            for case in cases:
                case_results.append(rehearsal.run_case(case))
            negatives = rehearsal.run_negative_suite(case_results[0])
            provider_calls = len(rehearsal.transport.calls)
            paths = {call["path"] for call in rehearsal.transport.calls}
            notes = list(rehearsal.notes)
        finally:
            rehearsal.close()

    refusals = {name: entry["refused"] for name, entry in negatives.items()}
    all_refused = bool(refusals) and all(refusals.values())
    total_reservation = round(sum(c["reservation_usd"] for c in case_results), 8)
    total_settled = round(sum(c["settled_usd"] for c in case_results), 8)
    ok = all(c["ok"] for c in case_results) and all_refused
    summary = {
        "harness": "tools/rehearse_fresh_comparison.py",
        "mode": mode,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "database": str(workspace / "rehearsal.db"),
        "network": {
            "mode": "blocked",
            "consumed_by": "httpx.MockTransport (no socket was opened)",
            "socket_guard": "socket.socket.connect/connect_ex raise during the run",
            "transport": "httpx.MockTransport",
            "provider_calls": provider_calls,
            "endpoints": sorted(paths),
            "destination": DESTINATION,
        },
        "credentials": {
            "real_key_reader_consulted": False,
            "key_source": ("in-process stub replacing app.ai.secrets.key; the environment "
                           "and the owner-only key file were never read"),
            "logged": False,
        },
        "cases": case_results,
        "negative": negatives,
        "totals": {
            "cases": len(case_results),
            "reservation_usd": total_reservation,
            "settled_usd": total_settled,
            "provider_calls": provider_calls,
        },
        "fixture_missing": list(FIXTURE_MISSING),
        "notes": notes,
        "result": "REHEARSAL_OK" if ok else "REHEARSAL_FAIL",
        "reason": (
            "%d/%d gold case(s) rehearsed; %d/%d refusal path(s) proven; all provider "
            "traffic answered by the mock transport"
            % (len(case_results), len(cases), sum(1 for v in refusals.values() if v),
               len(refusals))
            if ok else "one or more rehearsal checks did not hold"),
    }
    assert json.dumps(summary, allow_nan=False)  # strict-JSON self-check
    return summary


def failure_summary(mode: str, workspace: Path, exc: BaseException) -> dict:
    return {
        "harness": "tools/rehearse_fresh_comparison.py",
        "mode": mode,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "fixture_missing": list(FIXTURE_MISSING),
        "result": "REHEARSAL_FAIL",
        "reason": "%s: %s" % (type(exc).__name__, exc),
    }


def main(argv=None) -> int:
    command_args = sys.argv[1:] if argv is None else argv
    if command_args and command_args[0] in ("prepare", "execute"):
        from app.ai.comparison_run import main as comparison_main
        return comparison_main(command_args, CASES)
    parser = argparse.ArgumentParser(
        description=("Offline rehearsal of the frozen paid comparison "
                     "(no network, no API key, no paid call)."))
    parser.add_argument(
        "--negative", action="store_true",
        help=("prove only the four refusal paths (wrong hash, second execute, expired "
              "preview, reserve over cap). One case is rehearsed because the replay "
              "refusal needs an already executed job."))
    parser.add_argument("--report", metavar="PATH",
                        help="also write the JSON summary to PATH")
    parser.add_argument("--workdir", metavar="PATH",
                        help="workspace for the rehearsal DB and data copy "
                             "(default: a fresh temp directory)")
    parser.add_argument("--keep-workdir", action="store_true",
                        help="keep the temporary workspace (default: delete it)")
    args = parser.parse_args(argv)

    mode = "negative" if args.negative else "full"
    created_temp = args.workdir is None
    workspace = (Path(args.workdir).resolve() if args.workdir
                 else Path(tempfile.mkdtemp(prefix="fresh_comparison_rehearsal_")))
    try:
        summary = rehearse(mode=mode, workspace=workspace)
    except Exception as exc:  # a failed check is reported, never hidden
        summary = failure_summary(mode, workspace, exc)

    if args.report:
        report_path = Path(args.report)
        if report_path.parent and str(report_path.parent) not in ("", "."):
            report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")

    if created_temp and not args.keep_workdir:
        shutil.rmtree(workspace, ignore_errors=True)

    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, allow_nan=False))
    if summary["result"] == "REHEARSAL_OK":
        print("REHEARSAL_OK: " + summary["reason"])
        return 0
    print("REHEARSAL_FAIL: " + summary["reason"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
