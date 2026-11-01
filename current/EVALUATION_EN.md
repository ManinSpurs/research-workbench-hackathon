# Research harness evaluation protocol

## Current result: round 3, 2026-11-01

**AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)**

Human round-3 review is pending; all human fields are empty. The ten frozen
questions span three FX, four DOB NOW and three BIS questions, one repetition.
Artifacts: `current/logs/comparison_20261101T203959Z_cduuevnj/`.

| Provider | Basic success / planned | Harness success / planned | Retained answers |
|---|---:|---:|---:|
| Baseten | 9/10 | 9/10 | 20/20 |
| Local Qwen | 4/10 | 6/10 | 20/20 |
| Modal Shared | 7/10 | 7/10 | 16/20 |

Calculated round spend: Baseten $0.13738895, Modal $0.21011545, local API $0.
Baseten cross-round cumulative is $0.20959970 including $0.07221075 prior
comparison spend, below $40. Modal has an unresolved $3.958272 reservation;
known-plus-reservation exposure is $4.16838745, below $25. Invoice charges
are not verified. Subsequent Modal calls continued after an unknown-charge
failure, contrary to the stopping policy. One generated partial answer was
discarded by the service and cannot be scored. No retry was made.

The full claim/citation denominators, failures and protocol deviations are in
the round-3 post-run record in `current/HARNESS_COMPARISON_PREREG_EN.md`.
Unknown citation judgments make false-citation rates null, not zero.
AI scores are descriptive, not human judgments or independent validation.
Dataset correlation, provider profiles and missing answers weaken the result.
The text below describes historical protocol and earlier runs, not round-3 status.

The direct arm is one call with the same safety/domain/output contract, original question, registered evidence and selected media. The harness arm adds a public bounded plan and ordinary evidence selection before its final call. This is a direct single-call application baseline, not an unprompted consumer chat session. Neither arm receives the other arm's answer.

Each task pins source and prompt fingerprints, model, pack identity, execution order, stage latency, returned tokens, cost calculation basis and validation results. Prepared paired tasks alternate arm order. The original four-stage fake comparison remains preserved separately.

Use the synthetic FX dataset and its registered DD-2023 definitions for the first small gold cases. Do not count development fixtures as held-out scientific evaluation. Freeze the case manifest, source hashes and rubric before repeated testing.

Status correction 2026-10-31: review readback is implemented, superseding the
historical no-readback statement below. The basic-arm rubric is approved.
No live paired comparison has completed and nothing is human-scored.
The active comparison uses pinned Baseten Spark Open and local Qwen3-4B;
Wonder and its second look are blocked because Meta credits are exhausted.

| Case | Exact question | Gold constraints |
| --- | --- | --- |
| FX-COST-1 | What does est_cost measure? | Applicant-reported estimated cost in USD; not actual final cost; cite DD-2023:est_cost |
| FX-APPROVAL-1 | Does approval_date mean final approval? | Plan-review completion in release 2023; OFF-NOTES-2025 does not establish the 2023 meaning; final approval remains unestablished |
| NYC-DATE-1 | Can filing_date be used in DOB NOW? | Check the selected DOB NOW file's actual columns; do not borrow BIS filing_date |

Structural checks cover registered IDs, explicit release applicability and dataset-specific columns. They do not prove entailment or research correctness. `app/ai/evaluation.py` requires a named reviewer, declared gold reference, verdicts for claims and citation occurrences, method correctness and task success. `POST /ai/jobs/{jid}/review` stores the record locally, with zero provider calls and zero spend. Review readback and its frontend are implemented. The 2026-11-01 comparison run was scored in two passes: an AI first pass (blind), then a human second-pass review by Author, done after unblinding, which agreed with the AI marks (see the post-run addendum in `HARNESS_COMPARISON_PREREG_EN.md`). Reviewer Author is the author, not an independent reviewer. The packet's own human fields remain `PENDING_HUMAN` and the preregistered aggregates `NOT_SCORED`. Gold-reference identity is researcher supplied, not automatically authenticated.

Metrics use explicit denominators: supported claims / all claims; wrong citations / all citation occurrences when every citation is judged; supported factual linked claims / all claims; reviewer method and task-success judgments; stage latency and returned token usage; published-rate calculated cost. Unknown citation judgments leave the false-citation rate null. Do not replace unknowns with zero or infer quality from valid JSON.

## Observed live attempt

The approved synthetic Standard Spark task (recorded attempt: job `ai-fda0ae9885d3`, `current/logs/LIVE_SMOKE_RESULT.json` + `current/logs/LIVE_SMOKE_PREVIEW.json`) was bounded to three calls and $3.958272 maximum reservation. It received the basic response (1100 input, 1924 output tokens) and frame response (1085 input, 1473 output tokens). The frame failed local validation, so the final harness call was not made and no automatic retry occurred. Stage latencies were about 7.7405 and 4.6541 seconds. Calculated known response costs were $0.009552 and $0.0076165, totaling $0.0171685. Provider billing and semantic accuracy were not verified.

The first failure record did not retain the parsed baseline answer or rejected frame text. Consequently, do not reconstruct a quality score from memory. Follow-up code retains partial outputs and safe validation diagnostics and uses strict JSON/source enums. That correction is locally tested only. A fresh paid test requires a new preview and consent; the previous authorization is not reusable.

Live comparison success, accuracy improvement, false-citation improvement and engineering benchmark results remain NOT_RUN / NOT_ESTABLISHED. The preserved eight-case fake comparison remains process evidence only.
