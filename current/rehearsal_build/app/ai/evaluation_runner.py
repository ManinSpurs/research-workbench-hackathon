"""Offline human-scoring packet builder for recorded AI job results.

Pure offline module: no provider calls, no network, no file I/O, no clock.
It turns already-recorded job results (as produced by
``AIService.execute`` in ``service.py``) into a blinded human-scoring
packet for the frozen gold cases in ``current/EVALUATION_EN.md``.

REUSES (never duplicates) the existing mechanics:

- ``evaluation.py``: ``rubric_version`` ``'rw-research-1'``, the
  verdict-per-claim-and-per-citation-occurrence requirement
  (``ArmReview.claim_verdicts`` / ``ArmReview.citation_verdicts``), the
  denominator-explicit accuracy fields, and the unknowns-leave-rates-null
  rule (never zero-filled).
- ``grounding.py``: structural fields (``structural_valid``, ``errors``,
  citation counts, ``linked_claim_fraction``) are copied verbatim from the
  already-recorded per-arm ``grounding`` block, never recomputed here.
- ``service.py``: the recorded result shape
  (``outputs.<arm>.{result.claims, grounding}``, ``outputs.evaluation``,
  ``stages``, ``measured_known_cost_usd``, ``elapsed_seconds``) and the
  ``accuracy: NOT_SCORED`` / ``provider bill NOT_VERIFIED`` conventions.

No semantic auto-judgment happens here. Matching a cited ID against a
frozen expected ID is a structural citation-presence flag only, always
labeled ``STRUCTURAL_CITATION_PRESENCE_ONLY_NOT_CORRECTNESS``, and every
human verdict field defaults to ``PENDING_HUMAN``. Human citation marks
reuse the ``evaluation.ArmReview.citation_verdicts`` vocabulary verbatim
(``supports`` / ``wrong`` / ``unknown``) so completed sheets feed
``ArmReview`` with zero translation; human claim marks reuse
``claim_verdicts`` (``supported`` / ``contradicted`` / ``unverifiable``).
(Rename note: earlier draft wording ``supported`` / ``unsupported`` /
``applicability-pending`` was renamed to ``supports`` / ``wrong`` /
``unknown`` respectively; the old words never appear as emitted marks.)
"""

from __future__ import annotations

import json
import math

PACKET_VERSION = "eval-scoring-packet-1"
RUBRIC_VERSION = "rw-research-1"  # Reuses evaluation.Review.rubric_version.
# Named human reviewer (inventory G4 / catalog GAP-1), approved 2026-10-27.
# The single human on the project scores every sheet, so the reviewer is
# disclosed as an AUTHOR-reviewer: a self-score, never independent validation.
REVIEWER_NAME = "Author"
REVIEWER_KIND = "author_reviewer"
REVIEWER_DISCLOSURE = "author self-score; not independent external review"
REVIEWER_APPROVED_AT = "2026-10-27"
PENDING_HUMAN = "PENDING_HUMAN"
NOT_SCORED = "NOT_SCORED"
FIXTURE_MISSING = "FIXTURE_MISSING"
PRESENCE_CHECK_KIND = "STRUCTURAL_CITATION_PRESENCE_ONLY_NOT_CORRECTNESS"

# Human marks for each citation occurrence. Verbatim reuse of the existing
# evaluation.ArmReview.citation_verdicts vocabulary (supports / wrong /
# unknown) so completed sheets feed ArmReview directly with zero translation.
ALLOWED_HUMAN_CITATION_MARKS = ("supports", "wrong", "unknown")
# Human marks for each claim. Reuses the evaluation.ArmReview.claim_verdicts enum.
ALLOWED_HUMAN_CLAIM_MARKS = ("supported", "contradicted", "unverifiable")

# Frozen gold expectations. An expected citation ID is hardcoded ONLY when
# EVALUATION_EN.md literally requires citing it. FX-COST-1 literally
# requires ``cite DD-2023:est_cost``; the other two cases state gold
# constraints without any literal cite-ID requirement, so their expected
# citation IDs are FIXTURE_MISSING (with the verbatim constraints kept for
# the human scorer). Nothing is borrowed from any other document.
FROZEN_GOLD = {
    "FX-COST-1": {
        "question": "What does est_cost measure?",
        "gold_constraints": (
            "Applicant-reported estimated cost in USD; "
            "not actual final cost; cite DD-2023:est_cost"
        ),
        "expected_citation_ids": ["DD-2023:est_cost"],
    },
    "FX-APPROVAL-1": {
        "question": "Does approval_date mean final approval?",
        "gold_constraints": (
            "Plan-review completion in release 2023; "
            "OFF-NOTES-2025 does not establish the 2023 meaning; "
            "final approval remains unestablished"
        ),
        "expected_citation_ids": FIXTURE_MISSING,
        "fixture_reason": (
            "EVALUATION_EN.md states only a negative constraint for "
            "OFF-NOTES-2025 and no affirmative cite-ID requirement, so no "
            "expected citation ID is literally frozen and none is hardcoded."
        ),
    },
    "NYC-DATE-1": {
        "question": "Can filing_date be used in DOB NOW?",
        "gold_constraints": (
            "Check the selected DOB NOW file's actual columns; "
            "do not borrow BIS filing_date"
        ),
        "expected_citation_ids": FIXTURE_MISSING,
        "fixture_reason": (
            "EVALUATION_EN.md requires checking the actual dataset columns "
            "and names no literal citation ID, so no expected citation ID "
            "is literally frozen and none is hardcoded."
        ),
    },
}

# Structural grounding keys copied verbatim from recorded results.
_STRUCTURAL_KEYS = (
    "structural_valid",
    "errors",
    "citation_count",
    "invalid_citation_count",
    "citation_error_rate",
    "linked_claim_fraction",
    "semantic_correctness",
    "false_citation_rate",
)


def _is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _sanitize(value):
    """Replace non-finite floats with None so strict JSON always holds."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise ValueError("Packet holds a non-JSON value of type %s" % type(value).__name__)


def _blind_codes(count):
    codes = []
    for index in range(count):
        if index < 26:
            codes.append(chr(ord("A") + index))
        else:  # More arms than letters; keep codes unique and JSON-safe.
            codes.append("ARM-%d" % (index + 1))
    return codes


def _extract_arms(job):
    outputs = job.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("Recorded job result must carry a non-empty 'outputs' mapping")
    arms = {k: v for k, v in outputs.items() if k != "evaluation"}
    if not arms:
        raise ValueError("Recorded job result carries no arm outputs")
    for label, arm in arms.items():
        if not isinstance(arm, dict):
            raise ValueError("Arm '%s' must be a mapping" % label)
    evaluation = outputs.get("evaluation", {})
    if evaluation is None:
        evaluation = {}
    if not isinstance(evaluation, dict):
        raise ValueError("'outputs.evaluation' must be a mapping when present")
    return arms, evaluation


def _extract_run_params(job, evaluation, arm_count):
    stages = job.get("stages", [])
    calls = job.get("calls", job.get("max_inference_calls", None))
    if calls is None:
        calls = len(stages) if isinstance(stages, list) and stages else arm_count
    if isinstance(calls, bool) or not isinstance(calls, int):
        calls = None
    cost = job.get("measured_known_cost_usd", job.get("calculated_cost_usd", None))
    elapsed = job.get("elapsed_seconds", None)
    return {
        "model": job.get("model", evaluation.get("model", "UNKNOWN")),
        "execution_order_labels": list(
            job.get("execution_order", evaluation.get("execution_order", []))
        ),
        "calls": calls,
        "measured_known_cost_usd": cost if _is_finite_number(cost) else None,
        "elapsed_seconds": elapsed if _is_finite_number(elapsed) else None,
        "cost_basis_note": (
            "Measured known cost from recorded usage; provider billed cost "
            "NOT_VERIFIED. Paired totals include planning; stage timings and "
            "token usage permit per-arm accounting."
        ),
    }


def _structural_block(arm):
    grounding = arm.get("grounding", None)
    if not isinstance(grounding, dict) or not grounding:
        return {
            "structural_valid": None,
            "note": "Grounding block not recorded for this arm.",
        }
    return {key: grounding[key] for key in _STRUCTURAL_KEYS if key in grounding}


def _claim_item(claim, index, expected_ids):
    if not isinstance(claim, dict):
        raise ValueError("Claim %d must be a mapping" % index)
    cited = list(claim.get("evidence_ids", []))
    if expected_ids is FIXTURE_MISSING:
        presence = FIXTURE_MISSING
    else:
        matched = [eid for eid in cited if eid in expected_ids]
        presence = {
            "expected_ids": list(expected_ids),
            "cited": bool(matched),
            "matched_ids": matched,
            "check_kind": PRESENCE_CHECK_KIND,
        }
    citation_items = []
    for occurrence, eid in enumerate(cited):
        citation_items.append(
            {
                "occurrence_index": occurrence,
                "evidence_id": eid,
                "human_mark": PENDING_HUMAN,
                "allowed_marks": list(ALLOWED_HUMAN_CITATION_MARKS),
            }
        )
    return {
        "claim_index": index,
        "text": claim.get("text", ""),
        "kind": claim.get("kind", ""),
        "cited_ids": cited,
        "expected_citation_presence": presence,
        "citation_items": citation_items,
        "human_claim_verdict": PENDING_HUMAN,
        "allowed_claim_marks": list(ALLOWED_HUMAN_CLAIM_MARKS),
    }


def _not_scored_block():
    # Mirrors the denominator-explicit fields of evaluation.evaluate();
    # every rate stays literally NOT_SCORED until a human fills verdicts.
    return {
        "evidence_accuracy": NOT_SCORED,
        "false_citation_rate": NOT_SCORED,
        "researcher_verifiable_fraction": NOT_SCORED,
        "variable_method_accuracy": NOT_SCORED,
        "task_success": NOT_SCORED,
        "note": (
            "A human reviewer must record one verdict per claim and per "
            "citation occurrence (evaluation.ArmReview) before any rate is "
            "computed. Unknown citation judgments leave the false-citation "
            "rate null; unknowns are never replaced with zero."
        ),
    }


def _frozen_expectation(case_id):
    gold = FROZEN_GOLD.get(case_id)
    if gold is None:
        return {
            "status": FIXTURE_MISSING,
            "expected_citation_ids": FIXTURE_MISSING,
            "reason": (
                "Case id is not one of the three frozen gold cases in "
                "EVALUATION_EN.md (FX-COST-1, FX-APPROVAL-1, NYC-DATE-1)."
            ),
        }
    if isinstance(gold["expected_citation_ids"], list):
        return {
            "status": "FROZEN",
            "question": gold["question"],
            "gold_constraints": gold["gold_constraints"],
            "expected_citation_ids": list(gold["expected_citation_ids"]),
        }
    return {
        "status": FIXTURE_MISSING,
        "question": gold["question"],
        "gold_constraints": gold["gold_constraints"],
        "expected_citation_ids": FIXTURE_MISSING,
        "reason": gold["fixture_reason"],
    }


def _build_case(case_id, indexed_jobs):
    expectation = _frozen_expectation(case_id)
    expected_ids = expectation.get("expected_citation_ids", FIXTURE_MISSING)
    pairs = []  # (job_index, arm_label, arm, run_params)
    for job_index, job in indexed_jobs:
        arms, evaluation = _extract_arms(job)
        run_params = _extract_run_params(job, evaluation, len(arms))
        order = run_params.pop("execution_order_labels")
        if not order:
            order = sorted(arms)
        for label in sorted(arms):
            pairs.append((job_index, label, arms[label], run_params, order))
    codes = _blind_codes(len(pairs))
    code_of = {(job_index, label): code for code, (job_index, label, _, _, _) in zip(codes, pairs)}
    codebook = {}  # blind_code -> unblinding record
    blinded_arms = []
    for code, (job_index, label, arm, run_params, order) in zip(codes, pairs):
        claims = arm.get("result", {}).get("claims", []) if isinstance(
            arm.get("result", {}), dict
        ) else []
        if claims is None:
            claims = []
        if not isinstance(claims, list):
            raise ValueError("Arm '%s' claims must be a list" % label)
        items = [_claim_item(c, i, expected_ids) for i, c in enumerate(claims)]
        blind_order = []
        for position, original in enumerate(order):
            sibling = code_of.get((job_index, original))
            blind_order.append(
                {
                    "position": position,
                    "blind_code": sibling if sibling is not None else "NOT_RECORDED_ARM",
                }
            )
        codebook[code] = {"job_index": job_index, "arm_label": label}
        blinded_arms.append(
            {
                "blind_code": code,
                "structural": _structural_block(arm),
                "claims": items,
                "claim_count": len(items),
                "citation_occurrence_count": sum(
                    len(item["citation_items"]) for item in items
                ),
                "run_params": {
                    "model": run_params["model"],
                    "execution_order_blinded": blind_order,
                    "execution_order_note": (
                        "Arm labels replaced with this sheet's blind codes; "
                        "true labels live only in the unblinding key."
                    ),
                    "calls": run_params["calls"],
                    "measured_known_cost_usd": run_params["measured_known_cost_usd"],
                    "elapsed_seconds": run_params["elapsed_seconds"],
                    "cost_basis_note": run_params["cost_basis_note"],
                    "scope": "job-level; shared by every arm of the same recorded job",
                },
            }
        )
    true_order = []
    seen = set()
    for _, _, _, _, order in pairs:
        for label in order:
            if label not in seen:
                seen.add(label)
                true_order.append(label)
    return (
        {
            "case_id": case_id,
            "fixture_status": "FROZEN" if case_id in FROZEN_GOLD else FIXTURE_MISSING,
            "frozen_expectation": expectation,
            "gold_reference_status": (
                "researcher_supplied_reference_not_automatically_verified"
            ),
            "blinded_arms": blinded_arms,
            "aggregate": _not_scored_block(),
        },
        {code: dict(record) for code, record in codebook.items()},
        {
            "execution_order_labels": true_order,
            "arm_labels": sorted({label for _, label, _, _, _ in pairs}),
        },
    )


def build_scoring_packet(job_results, case_ids):
    """Build a blinded human-scoring packet from recorded job results.

    Args:
        job_results: list of recorded result dicts (``AIService.execute``
            shape: ``outputs.<arm>.{result.claims, grounding}``,
            ``outputs.evaluation``, ``stages``,
            ``measured_known_cost_usd``, ``elapsed_seconds``).
        case_ids: parallel list of gold case ids, one per job result.

    Returns:
        A strict-JSON packet dict with blinded per-case/per-arm scoring
        sheets, a separate unblinding key, and NOT_SCORED aggregates.
    """
    if not isinstance(job_results, list) or not isinstance(case_ids, list):
        raise ValueError("job_results and case_ids must both be lists")
    if len(job_results) != len(case_ids):
        raise ValueError("job_results and case_ids must be parallel arrays of equal length")
    for case_id in case_ids:
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every case id must be a non-empty string")
    for job in job_results:
        if not isinstance(job, dict):
            raise ValueError("Every recorded job result must be a mapping")

    order = []
    grouped = {}
    for index, (job, case_id) in enumerate(zip(job_results, case_ids)):
        grouped.setdefault(case_id, []).append((index, job))
        if case_id not in order:
            order.append(case_id)

    cases = []
    unblind_cases = {}
    pending = 0
    arms_total = 0
    for case_id in order:
        entry, codebook, truth = _build_case(case_id, grouped[case_id])
        cases.append(entry)
        unblind_cases[case_id] = {
            "mapping": codebook,
            "execution_order_labels": truth["execution_order_labels"],
            "arm_labels": truth["arm_labels"],
        }
        arms_total += len(entry["blinded_arms"])
        for arm in entry["blinded_arms"]:
            for item in arm["claims"]:
                pending += sum(
                    1
                    for c in item["citation_items"]
                    if c["human_mark"] == PENDING_HUMAN
                )
                if item["human_claim_verdict"] == PENDING_HUMAN:
                    pending += 1

    packet = {
        "packet_version": PACKET_VERSION,
        "protocol": (
            "Frozen protocol current/EVALUATION_EN.md; preparation "
            "current/FRESH_COMPARISON_PREP_EN.md (EXECUTION NOT_RUN, consent "
            "not requested). Offline scoring packet only; no inference."
        ),
        "rubric": {
            "rubric_version": RUBRIC_VERSION,
            "gold_reference_status": (
                "researcher_supplied_reference_not_automatically_verified"
            ),
            "citation_marks": list(ALLOWED_HUMAN_CITATION_MARKS),
            "claim_marks": list(ALLOWED_HUMAN_CLAIM_MARKS),
            "marks_note": (
                "Human marks reuse the evaluation.ArmReview verdict "
                "vocabularies verbatim so completed sheets feed ArmReview "
                "with zero translation."
            ),
            "reviewer": REVIEWER_NAME,
            "reviewer_kind": REVIEWER_KIND,
            "reviewer_disclosure": REVIEWER_DISCLOSURE,
            "denominators": {
                "evidence_accuracy": "supported claims / all claims",
                "false_citation_rate": (
                    "wrong citations / all citation occurrences, judged only "
                    "when every citation is judged; unknowns leave it null"
                ),
                "researcher_verifiable_fraction": (
                    "supported factual linked claims / all claims"
                ),
            },
        },
        "reviewer": REVIEWER_NAME,
        "reviewer_kind": REVIEWER_KIND,
        "reviewer_disclosure": REVIEWER_DISCLOSURE,
        "reviewer_approved_at": REVIEWER_APPROVED_AT,
        "scoring_status": NOT_SCORED,
        "cases": cases,
        "unblinding_key": {
            "warning": (
                "UNBLIND_ONLY_AFTER_SCORING: keep sealed until every human "
                "verdict is recorded; scoring sheets above carry blind codes only."
            ),
            "cases": unblind_cases,
        },
        "summary": {
            "scoring_status": NOT_SCORED,
            "accuracy": _not_scored_block(),
            "counts": {
                "cases": len(cases),
                "blinded_arms": arms_total,
                "human_marks_pending": pending,
            },
            "reviewer": REVIEWER_NAME,
            "reviewer_kind": REVIEWER_KIND,
        },
    }
    packet = _sanitize(packet)
    try:
        json.dumps(packet, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Scoring packet is not strict JSON: %s" % exc) from None
    return packet
