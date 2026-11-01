"""L3c: local-vs-cloud comparison packet assembly + pre-registered usefulness bar.

Offline only: no provider calls, no network, no model execution, no file I/O.
Reuses ``evaluation_runner.build_scoring_packet`` unchanged (existing packet
path); this module adds no new arm semantics, no scoring, and no verdict.

Shape assembled here (per frozen case): two separate recorded jobs, one
local (``local:<server>:<model-tag>``) and one cloud (``muse-spark-1.3``),
each a paired basic+harness job. Cross-provider arms are never mixed in one
job (design §1 rule 3). Auxiliary non-reasoning functions (transcription,
segmentation, image) are rejected from the reasoning slot.

The usefulness bar is quoted verbatim from
``current/LOCAL_FREE_MODE_DESIGN_EN.md`` §3.5. Its own source marks the
numbers a proposal the Lead must confirm in writing before any run, so this
module records ``bar: DECISION-NEEDED`` and ``apply_usefulness_bar`` refuses
to emit any PASS/FAIL verdict.

Blinding honesty note: scored sheets group arms under blind codes, but each
sheet's ``run_params.model`` carries the true model id (a ``local:`` id or
``muse-spark-1.3``) and the local ``0.0`` cost stays visible as accounting
context, so blinding here is procedural only. Reviewers must not reconstruct
identities from these fields, and the comparison table is drawn from
post-unblinding aggregates (design §3.4 point 4).
"""

from __future__ import annotations

from app.ai.evaluation_runner import NOT_SCORED, build_scoring_packet

# Pre-registered usefulness bar, verbatim from its source doc.
BAR_SOURCE_FILE = "current/LOCAL_FREE_MODE_DESIGN_EN.md"
BAR_SOURCE_LINES = "134-137"
BAR_SOURCE_CITE = "%s:%s" % (BAR_SOURCE_FILE, BAR_SOURCE_LINES)

BAR_U1 = ("Floor U1 — task success: local arm `task_success == True` "
          "on at least 2 of 3 frozen cases.")
BAR_U2 = ("Floor U2 — citation accuracy: local arm "
          "`evidence_accuracy >= 0.50` AND `false_citation_rate <= 0.50` "
          "on cases where those rates are non-null (unknowns leave nulls; "
          "nulls do not pass a floor — a null rate means \"unmeasured,\" "
          "counted as *below bar* for that case).")
BAR_U3 = ("Floor U3 — structural validity: `structural_valid == True` "
          "on all 3 cases (the machine-checkable gate the app itself enforces).")
BAR_CAVEAT = ("[UNVERIFIED: exact thresholds are a proposal — the Lead sets "
              "final numbers before any run; English-only tasks.]")

# The numbers are a proposal until the Lead confirms them in writing
# (design §7 Q5; mirrored as PROPOSED — NOT YET FROZEN in
# current/HARNESS_COMPARISON_PREREG_EN.md §3.2). Until then the bar is not
# frozen and no verdict may be derived from it.
BAR_STATUS = "DECISION-NEEDED"

CLOUD_MODEL = "meta-models/muse-spark-open-1026"
FROZEN_CASES = ("FX-COST-1", "FX-APPROVAL-1", "NYC-DATE-1")
REQUIRED_ARMS = ("basic", "harness")

# Auxiliary non-reasoning functions: never permitted in the reasoning slot.
NON_REASONING_MODELS = frozenset({
    "muse-voice-transcribe-1.0",
    "sam-3.1",
    "muse-image-1.0",
})


class BarNotFrozenError(ValueError):
    """Raised when a PASS/FAIL verdict is requested while the bar is DECISION-NEEDED."""


def usefulness_bar():
    """Return the pre-registered bar record (verbatim text + frozen status)."""
    return {
        "bar": BAR_STATUS,
        "source": BAR_SOURCE_CITE,
        "u1": BAR_U1,
        "u2": BAR_U2,
        "u3": BAR_U3,
        "caveat": BAR_CAVEAT,
        "status_note": (
            "Threshold numbers are a proposal (source caveat); the Lead "
            "confirms or replaces them in writing before the first run "
            "(design §7 Q5). Mirrored as PROPOSED — NOT YET FROZEN in "
            "HARNESS_COMPARISON_PREREG_EN.md §3.2; harness-delta thresholds "
            "k/epsilons are MISSING there (§3.3)."
        ),
    }


def _check_job(job, case_id, side):
    if not isinstance(job, dict):
        raise ValueError("%s job for %s must be a mapping" % (side, case_id))
    outputs = job.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("%s job for %s must carry a non-empty 'outputs' mapping" % (side, case_id))
    arms = sorted(k for k in outputs if k != "evaluation")
    if arms != sorted(REQUIRED_ARMS):
        raise ValueError(
            "%s job for %s must record exactly basic+harness arms, got %r"
            % (side, case_id, arms)
        )
    model = job.get("model")
    if isinstance(model, str) and model.split(":")[-1] in NON_REASONING_MODELS:
        raise ValueError(
            "%s job for %s uses auxiliary non-reasoning model %r in the reasoning slot"
            % (side, case_id, model)
        )
    if side == "local":
        if not (isinstance(model, str) and model.startswith("local:")):
            raise ValueError(
                "local job for %s must carry a 'local:<server>:<model-tag>' id, got %r"
                % (case_id, model)
            )
        if "measured_known_cost_usd" not in job:
            raise ValueError(
                "local job for %s must record $0 API cost "
                "(missing 'measured_known_cost_usd')" % case_id
            )
        cost = job["measured_known_cost_usd"]
        if (isinstance(cost, bool) or not isinstance(cost, (int, float))
                or cost != 0):
            raise ValueError("local job for %s must record $0 API cost" % case_id)
    else:
        if model != CLOUD_MODEL:
            raise ValueError(
                "cloud job for %s must use %r, got %r" % (case_id, CLOUD_MODEL, model)
            )
    return arms


def build_local_comparison_packet(local_jobs, cloud_jobs, case_ids):
    """Assemble one comparison packet: local basic/harness + cloud basic/harness.

    Blinding is procedural only (see module docstring): blind codes group
    the arms, but each sheet's ``run_params.model`` carries the true id.

    Args:
        local_jobs / cloud_jobs: recorded result dicts (``AIService.execute``
            shape) already in hand — no execution happens here.
        case_ids: parallel frozen case ids, one per job pair.

    Every result defaults to NOT_SCORED / NOT_RUN: the scoring packet keeps
    the runner's NOT_SCORED aggregates, and the comparison manifest marks
    every arm cell NOT_RUN (nothing executed for this packet).

    Blinding is procedural only: each blinded arm's ``run_params.model``
    carries the true model id and the local ``0.0`` cost stays visible as
    accounting context (design §3.4 point 4) — reviewers must not
    reconstruct identities from these fields.
    """
    for name, jobs in (("local_jobs", local_jobs), ("cloud_jobs", cloud_jobs)):
        if not isinstance(jobs, list) or not jobs:
            raise ValueError("%s must be a non-empty list" % name)
    if list(case_ids) != list(FROZEN_CASES):
        raise ValueError(
            "case_ids must be exactly the three frozen cases in order: %r"
            % (list(FROZEN_CASES),)
        )
    if len(local_jobs) != 3 or len(cloud_jobs) != 3:
        raise ValueError("one local job and one cloud job per frozen case (3 + 3)")

    for job, case_id in zip(local_jobs, case_ids):
        _check_job(job, case_id, "local")
    for job, case_id in zip(cloud_jobs, case_ids):
        _check_job(job, case_id, "cloud")

    ordered_results = []
    ordered_cases = []
    for local_job, cloud_job, case_id in zip(local_jobs, cloud_jobs, case_ids):
        ordered_results.extend([local_job, cloud_job])
        ordered_cases.extend([case_id, case_id])
    packet = build_scoring_packet(ordered_results, ordered_cases)

    cells = []
    for case_id in case_ids:
        mapping = packet["unblinding_key"]["cases"][case_id]["mapping"]
        case_entry = next(e for e in packet["cases"] if e["case_id"] == case_id)
        blind_by_code = {a["blind_code"]: a for a in case_entry["blinded_arms"]}
        seen_jobs = set()
        for code, record in mapping.items():
            job_index = record["job_index"]
            seen_jobs.add(job_index)
            source_job = ordered_results[job_index]
            model = source_job.get("model", "")
            side = ("local" if isinstance(model, str) and model.startswith("local:")
                    else "cloud")
            sheet = blind_by_code[code]
            cells.append({
                "case_id": case_id,
                "side": side,
                "arm": record["arm_label"],
                "blind_code": code,
                "model": model,
                "run_status": "NOT_RUN",
                "scoring_status": NOT_SCORED,
                "claim_count": sheet["claim_count"],
            })
        assert seen_jobs == {
            case_ids.index(case_id) * 2, case_ids.index(case_id) * 2 + 1}, seen_jobs

    comparison = {
        "arms_total": len(cells),
        "cases": list(case_ids),
        "cells": cells,
        "scoring_status": NOT_SCORED,
        "run_status": "NOT_RUN",
    }
    return {
        "scoring_packet": packet,
        "comparison": comparison,
        "usefulness_bar": usefulness_bar(),
    }


def apply_usefulness_bar(comparison_bundle):
    """Refuse to emit a PASS/FAIL verdict: the bar is DECISION-NEEDED.

    The bar numbers are an unfrozen proposal until the Lead confirms them in
    writing, so any verdict derived now would move the goalposts. Raises
    BarNotFrozenError unconditionally.
    """
    _ = comparison_bundle
    raise BarNotFrozenError(
        "verdict REFUSED: usefulness bar is DECISION-NEEDED "
        "(source %s; thresholds are a proposal pending Lead confirmation). "
        "No PASS/FAIL emitted." % BAR_SOURCE_CITE
    )
