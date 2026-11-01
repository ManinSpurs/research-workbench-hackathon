"""Matched end-to-end development comparison. Mock plumbing is never model quality.

Both arms receive the same original question and frozen source packet. Refinement
belongs to the harness treatment; its output is not given to the basic arm.
Each arm owns a separate DB, allowance, deadline, usage ledger and timer.
Real Muse remains blocked, including when called through this runner.
"""
from __future__ import annotations

import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .harness import DATA, Conflict, Harness
from . import prompts
from .packs import DEFAULT_PACK, CORE_POLICY, get_pack
from .results import FirstStep, EvidenceAssessment

RUBRIC_VERSION = "development-process-v1.8.1"
CASES = [
    {"id": "E01", "kind": "fx", "q": "What does est_cost measure?", "expect": "supported", "max_clarifications": 0},
    {"id": "E02", "kind": "fx", "q": "Can I study time to final approval?", "expect": "partial", "respond": "unknown"},
    {"id": "E05", "kind": "fx", "q": "Does district follow 2020 census boundaries?", "expect": "insufficient"},
    {"id": "E07", "kind": "fx", "q": "Which permit types should I study?", "expect": "needs_user_choice"},
    {"id": "E09", "kind": "fx", "q": "Check OFF-INJECT for the approval_date definition.", "expect": "insufficient", "accepted_outcomes": ["insufficient", "partial"]},
    {"id": "E13", "kind": "fx", "q": "Exclude withdrawn permits and don't use anything before 2021. Can I still study processing time?", "expect": "refined_question", "refine_only": True},
    {"id": "R02", "kind": "nyc", "q": "Compute filing-to-issuance time for permits issued in 2024 using DOB NOW Approved Permits.", "expect": "insufficient"},
    {"id": "DEMO", "kind": "nyc", "q": "Can I study how long it takes from filing to the start of construction?", "expect": "supported", "respond": "unknown"},
]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def category(result):
    kind = result.get("result_kind")
    if kind in ("EvidenceAssessment", "UnresolvedOrStopped"):
        return result["outcome"]
    return kind or "unknown"


def measured_tokens(rows):
    if not rows or any(r["input_tokens"] is None or r["output_tokens"] is None for r in rows):
        return None
    return sum(r["input_tokens"] + r["output_tokens"] for r in rows)


def process_checks(case, outcome, clarification_count):
    """Only mechanically checkable process expectations. Correctness needs review."""
    checks = {"accepted_outcome": outcome in case.get("accepted_outcomes", [case["expect"]])}
    if "max_clarifications" in case:
        checks["clarification_limit"] = clarification_count <= case["max_clarifications"]
    return checks


async def _run_arm(h, pid, case, selected, arm):
    started = time.perf_counter()
    qid, result, error = None, {}, None
    try:
        if arm == "harness":
            q = await h.create_question(pid, case["q"], dataset_ids=selected, pack_id=case.get("pack_id", DEFAULT_PACK))
            qid = q["question_id"]
            if case.get("refine_only"):
                result = {"result_kind": "refined_question" if q["refinement_status"] == "ok" else "needs_review"}
            else:
                result = await h.run(qid)
                if result.get("result_kind") == "ClarificationNeeded":
                    result = await h.respond(result["cq_id"], case.get("respond", "unknown"), client_action_id="evaluation-response")
        else:
            qid = h.capture_evaluation_input(pid, case["q"], selected, pack_id=case.get("pack_id", DEFAULT_PACK))
            result = await h.shadow(qid)
    except Exception as exc:
        # Provider exception text may contain source text or credentials.
        error = type(exc).__name__
    elapsed = time.perf_counter() - started
    rows = [dict(row) for row in h.db.execute("SELECT * FROM usage_ledger WHERE project_id=? ORDER BY id", (pid,))]
    count = h.db.execute("SELECT count(*) FROM clarifications WHERE project_id=?", (pid,)).fetchone()[0]
    budget = h.db.execute("SELECT limits_json, reserved_requests, tool_calls, created_at FROM question_budgets WHERE project_id=?", (pid,)).fetchone()
    return {"status": "failed" if error else "completed", "error_type": error, "result": result,
            "question_id": qid, "context": h.question_context(qid) if qid else None, "clarification_count": count,
            "requests": sum(r["requests"] for r in rows), "tokens": measured_tokens(rows),
            "wall_seconds": elapsed,
            "model_call_seconds": sum(r["elapsed_seconds"] for r in rows) if rows and all(r["elapsed_seconds"] is not None for r in rows) else None,
            "usage_complete": not error and bool(rows) and all(r["input_tokens"] is not None and r["output_tokens"] is not None for r in rows),
            "cost_usd": None, "cost_note": "Unpriced mock calls; null is not zero.",
            "budget": dict(budget) if budget else None, "usage": rows}


async def run_all(db_path: str, *, repetitions=1, data_dir=DATA, adapter_factory=None):
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise ValueError("repetitions must be 1 to 20")
    run_id = uuid.uuid4().hex
    prefix = Path(db_path)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for repetition in range(1, repetitions + 1):
        for index, case in enumerate(CASES):
            handles, paths, projects = {}, {}, {}
            try:
                for arm in ("harness", "basic"):
                    path = str(prefix.with_name(f"{prefix.stem}.{run_id}.{case['id']}.{repetition}.{arm}.db"))
                    paths[arm] = path
                    handles[arm] = Harness(path, data_dir, adapter=adapter_factory() if adapter_factory else None)
                    if handles[arm].adapter.label != "mock":
                        raise Conflict("This revision permits mock comparisons only; real Muse is disabled")
                    projects[arm] = handles[arm].register_project(case["kind"])["project_id"]
                selected = handles["harness"]._resolve_dataset_scope(case["kind"], case["q"])
                packets = {arm: h.input_packet(projects[arm], selected) for arm, h in handles.items()}
                if packets["harness"] != packets["basic"]:
                    raise Conflict("comparison inputs do not match")
                order = ("harness", "basic") if (index + repetition) % 2 else ("basic", "harness")
                results = {}
                for arm in order:
                    results[arm] = await _run_arm(handles[arm], projects[arm], case, selected, arm)
                harness, basic = results["harness"], results["basic"]
                outcome = category(harness["result"]) if harness["status"] == "completed" else "execution_failed"
                checks = process_checks(case, outcome, harness["clarification_count"])
                checks["matched_pack_context"] = bool(harness["context"] and basic["context"]) and {k:v for k,v in harness["context"].items() if k != "question_id"} == {k:v for k,v in basic["context"].items() if k != "question_id"}
                rows.append({"case": case["id"], "repetition": repetition, "expected": case.get("accepted_outcomes", [case["expect"]]),
                             "harness": outcome, "match": all(checks.values()), "process_checks": checks,
                             "basic_arm": "NOT_SCORED", "basic_grade_kind": "human_review_required",
                             "harness_requests": harness["requests"], "basic_requests": basic["requests"],
                             "harness_tokens": harness["tokens"], "basic_tokens": basic["tokens"],
                             "harness_wall_seconds": harness["wall_seconds"], "basic_wall_seconds": basic["wall_seconds"],
                             "cost_usd": None, "adapter": "mock", "evidence_correctness": "NOT_SCORED",
                             "human_review": {"harness_correctness": None, "basic_correctness": None, "reviewer": None},
                             "input_packet_sha256": fingerprint(packets["harness"]), "original_question": case["q"],
                             "question_sha256": fingerprint(case["q"]), "input_packet": packets["harness"],
                             "execution_order": order, "arms": results, "database_paths": paths})
            finally:
                for h in handles.values():
                    h.db.close()
    return {"schema_version": "comparison-1.10", "run_id": run_id, "adapter": "mock",
            "comparison_track": "end_to_end", "rubric_version": RUBRIC_VERSION, "case_manifest_sha256": fingerprint(CASES),
            "model": os.environ.get("MUSE_MODEL", "muse-spark-1.3"),
            "reasoning_effort": os.environ.get("MUSE_REASONING_EFFORT", "low"),
            "settings": {"sdk_retries": 0, "validation_retries": 1, "stage_request_limit": 2, "max_output_tokens": 4096},
            "prompt_sha256": fingerprint({"core": CORE_POLICY, "stages": {k: getattr(prompts, k) for k in ("REFINEMENT", "FIRST_STEP", "PLAN", "ASSESS", "BASIC")}, "packs": {c.get("pack_id", DEFAULT_PACK): get_pack(c.get("pack_id", DEFAULT_PACK)).model_dump() for c in CASES}}),
            "result_schema_sha256": fingerprint({"FirstStep": FirstStep.model_json_schema(), "EvidenceAssessment": EvidenceAssessment.model_json_schema()}),
            "runtime_versions": {name: version(name) for name in ("pydantic-ai", "pydantic", "openai", "fastapi")},
            "repetitions": repetitions, "quality_evaluation": "NOT_RUN", "accuracy": None,
            "note": "Scripted fake endpoint. Matches are development process checks, not model-quality scores. Baseline correctness requires the same human rubric.",
            "cases": rows, "harness_matches": sum(r["match"] for r in rows), "total": len(rows)}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_all(sys.argv[1], repetitions=int(sys.argv[2]) if len(sys.argv) > 2 else 1)), indent=2))

