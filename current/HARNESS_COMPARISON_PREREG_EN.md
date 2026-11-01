# Harness comparison pre-registration: Baseten Spark Open and local Qwen3-4B

STATUS: PRE-REGISTRATION ONLY — NOT RUN. This file fixes the comparison *before* any run, so the usefulness bar cannot be moved afterwards. No inference was executed for this file, no model was downloaded or loaded, no paid/live call was made, no key was read, no code or test was changed. This file is the sole artifact authored under this task.

`EXECUTION: NOT_RUN` · `CONSENT: NOT_YET_REQUESTED` · `SCORING: NOT_SCORED`

## 0. Provenance and conventions

Read for this pre-registration (read-only):

- `current/MODEL_HARNESS_COVERAGE_EN.md` — the model × harness coverage design (arm matrix, per-model harness definition, forbidden widenings). STATUS there: DESIGN ONLY — NOT IMPLEMENTED.
- `current/FRESH_COMPARISON_PREP_EN.md` — frozen cases, transmission scope, reservation basis, `EXECUTION: NOT_RUN`, computed file hashes.
- `current/LOCAL_FREE_MODE_DESIGN_EN.md` — the local ~4B column (eligibility, runtime, $0 cost accounting, §3.5 proposed usefulness bar U1–U3).
- `current/EVALUATION_EN.md` — evaluation protocol (basic vs harness definitions, denominators, not-run status).
- `current/HUMAN_SCORING_GUIDE_EN.md` — the blinded human scoring procedure (rubric `rw-research-1`, packet `eval-scoring-packet-1`, marks, prohibitions, GAPs A–G).
- `current/rehearsal_build/app/ai/evaluation_runner.py` — `FROZEN_GOLD`, `build_scoring_packet`, `_frozen_expectation`, `NOT_SCORED`/`FIXTURE_MISSING`.
- `current/rehearsal_build/app/ai/evaluation.py` — `ArmReview`, `Review`, `evaluate()` (denominator-explicit rates, `improvement_claim: 'NOT_COMPUTED'`).
- `current/rehearsal_build/app/ai/service.py` — `Request` (defaults `harness=True`, `compare=False`, `max_output_tokens=2048`, `max_cost_usd=5.0`), reservation formula in `prepare()`, `execute()` arm selection.
- `current/rehearsal_build/app/ai/registry.py` — `MODELS`, `TASKS`, `route()`.
- `current/rehearsal_build/data/**` — the three case fixtures (headers + hashes re-verified for this file, §5).

Markers used in this file, with fixed meaning:

- `MISSING` — the value/decision does not exist in any source read; it must be supplied by the named owner before a run, and it may not be invented.
- `FIXTURE_MISSING` — a required fixture/case/artifact is absent from the repo or from the frozen gold (same token the code uses).
- `NOT_RUN` — the arm/job has not executed.
- `NOT_SCORED` — no scoring has occurred; every rate is unset.
- `BLOCKED` — mechanically impossible with the current tree (evidence cited inline).
- `CONFLICT` — two source documents disagree; both are stated, neither is silently chosen.

## Round 3 amendment: 2026-11-01, before round-3 paid inference

Status: PRE_REGISTERED / NOT_RUN. This amendment does not supersede historical
round-1 or round-2 observations. No round-3 paid call has been made while
authoring it. The user authorizes Modal credits for this round, with the limits
below. No human score is claimed.

Scoring label: **AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)**

### Hypotheses and interpretation

H1: Within each provider, harness answers have a higher task-success fraction
than basic answers across the ten questions below. H2: Harness answers have
higher evidence accuracy and lower false-citation rates. H3: Modal Shared
supports completing the same grounded task contract as Baseten, without
assuming that identical model branding establishes identical model behavior.
All are descriptive hypotheses, not superiority claims or significance tests.
One repetition per question is insufficient to measure sampling variability.
Report raw numerators and denominators and paired per-question differences.
Do not remove failures or blocked arms from the planned-run denominator.

### Frozen questions and answer keys

Revision 2, 2026-11-01, still before any round-3 paid call: replace seven
additional FX questions with one FX, three DOB NOW and three BIS questions.
Reason: the original nine-FX allocation overstates breadth. The revised
allocation is three FX, four DOB NOW and three BIS questions. NYC keys use
the in-project dictionary extracts and downloaded subset headers; no external
private folder is accessed or transmitted. This respects the workspace-only
constraint while using the project's existing NYC data. These are three
datasets, not ten independent research domains; questions still share
dictionaries and this correlation must be disclosed in conclusions.

Sources are already registered synthetic project fixtures, not new external
research data. FX references below mean
`current/rehearsal_build/data/fx/sources.json`; the NYC references and header
are the frozen NYC-DATE-1 evidence in `current/FRESH_COMPARISON_PREP_EN.md`.
These keys are fixed before outputs exist. Claim entailment, not merely an
evidence-ID match, determines correctness.

| Case | Exact question | Predetermined answer key and required evidence |
|---|---|---|
| FX-COST-1 | What does est_cost measure? | Applicant-reported estimated cost in USD, not actual final cost. Cite `DD-2023:est_cost`. |
| FX-APPROVAL-1 | Does approval_date mean final approval? | Release 2023 records plan-review completion; final approval is unestablished. Cite `DD-2023:approval_date` and/or `OFF-FAQ-2023`; do not apply the 2025-only definition to 2023. |
| NYC-DATE-1 | Can filing_date be used in DOB NOW? | Selected DOB NOW file lacks filing_date; do not borrow the BIS field. Cite the registered DOB NOW Approved Date / Issued Date dictionary evidence, `DD-DOBNOW-2018:Column Info:Approved Date` and `DD-DOBNOW-2018:Column Info:Issued Date`. Other extracts remain unestablished. |
| FX-STATUS-1 | What do A, I, W and X mean in status_code? | A Approved, I Issued, W Withdrawn, X Expired. Cite `DD-2023:status_code`; no invented additional codes. |
| NYC-APPROVED-1 | Does DOB NOW approved_date mean that the permit was issued? | No: entire job approved by Plan Examiner, applicant can now pull a permit. Cite `DD-DOBNOW-2018:Column Info:Approved Date`; issued_date is distinct, supported by `DD-DOBNOW-2018:Column Info:Issued Date`. |
| NYC-EXPIRED-1 | Does DOB NOW expired_date record issuance or expiry? | Date the permit expires, not issuance. Cite `DD-DOBNOW-2018:Column Info:Expired Date`; selected header has expired_date. |
| NYC-SEQUENCE-1 | What does sequence_number measure in DOB NOW? | Numeric order of permit issuance, not elapsed days or a calendar date. Cite `DD-DOBNOW-2018:Column Info:Sequence Number`; selected header has sequence_number. |
| BIS-FILING-1 | What event does filing_date record in the selected BIS file? | Permit application filed with DOB, not permit issuance. Cite `DD-BIS-2019:Column Info:Filing Date`; selected BIS header contains filing_date. |
| BIS-RENEWAL-1 | What distinction does filing_status encode in BIS? | First application versus renewal; INITIAL or RENEWAL, not issued/revoked permit status. Cite `DD-BIS-2019:Column Info:Filing Status`. |
| BIS-SEQUENCE-1 | Does permit_sequence__ identify a job or successive permit issuances in BIS? | Sequential number for each issuance of the particular permit, initial 01 followed by renewals 02, 03, etc.; not the job identifier. Cite `DD-BIS-2019:Column Info:Permit Sequence #`; selected header has permit_sequence__. |

### Arms and execution contract

All ten questions receive six planned arms: Baseten basic/harness with
`meta-models/muse-spark-open-1026`; local Qwen basic/harness with
`local:llamacpp:qwen3-4b-instruct-2507-q4_k_m`; Modal Shared basic/harness with
app alias `modal-shared:muse-spark-open`, pinned remote model
`meta-models/Muse-Spark-Open-816B-A42B` at the origin configured in
`current/rehearsal_build/Start_Modal_Shared.command`.
No Glimmer arm, model substitution, deployment or frontend change is authorized.

This is 60 planned answers: 30 paired jobs and at most 90 inference calls,
30 per provider (basic one call, harness frame plus final).
Use the same question, selected fixture columns, registered evidence,
pack and instruction primitives within each provider pair. Retain the
existing provider-specific profiles: 2048 requested output ceiling, local
1024 subcap, Baseten low reasoning effort. Alternate basic/harness order by
question index, starting basic first. Repeat once only. No repairs, retries,
paid replay or reuse of prior answers. A failed frame blocks that job's
dependent final call; record the reason rather than inventing an answer.

Prepare and persist content-bound previews before inference. Save each
receipt, output, execution error and per-call usage in a fresh allocator-named
`current/logs/comparison_<timestamp>_<id>/` directory. Preserve earlier runs.
If port 9931 belongs to another local model server, do not stop or replace
it; mark both local arms blocked for every affected question and continue
cloud arms. Close only processes this round starts.

### Rubric, blinding and aggregation

Use `rw-research-1` and the existing blind packet/sheet conventions. Allocate
six random blind identifiers per case before answers exist; keep provider,
model, order, usage and latency only in a separate unblinding key. Added
questions use the explicit keys above, never `FIXTURE_MISSING` defaults.
Score every available answer before reading its provider mapping.

For each claim, AI first pass records supported / contradicted / unverifiable
with the quoted claim, cited evidence and answer-key rationale. For each
citation occurrence record supports / wrong / unknown based on entailment
and release applicability. Missing required evidence is not a correct citation.
Record variable/method correctness and whole-task success separately as
booleans, with reasons. Success requires the full key, no material
contradiction and adequate supporting citations. Human columns remain empty.

Aggregate supported claims / all claims, wrong citations / all citation
occurrences, supported factual linked claims / all claims, correct
variable/method answers / scored answers, successful answers / scored answers.
Zero denominators yield null, not zero. Also show successful answers / ten
planned questions and explicit completed/failed/blocked counts per arm.
Unblind only after the first-pass score artifact is saved. Record the
unblinded table and actual spend in the post-run records, evaluation document
and dev project only after real results exist.

### Spend limits and stopping

Modal round-3 calculated spend must not exceed $25. Baseten cumulative
approved spend must not exceed $40, including all prior attempts, not merely
round 2. Approximately $0.072 is contextual, not an exact ledger balance:
reconcile existing receipts before determining remaining Baseten allowance.
Local API charge is $0, not a hardware-cost estimate.

Before each paid job, reserve its app-computed worst-case call cost against
that provider's remaining allowance. Stop that provider before execution
if the reservation would cross its cap. Preserve the existing stricter
single-call $1 and cumulative $5 engineering stops unless separately amended
before inference. Unknown usage retains the reservation and blocks further
paid calls for that provider until reconciled; do not count unknown cost as
zero. Save per-provider returned usage, cached-token accounting and calculated
spend separately from any provider invoice. Never expose or copy credentials.

### Round-3 post-run record, 2026-11-01

Execution completed in `current/logs/comparison_20261101T203959Z_cduuevnj/`.
`preregistration-before-execute.json` binds the pre-call amendment hash and
mtime; `execute-started` records execution start. The exact pre-call bytes
were recovered after review by reversing this session's recorded post-run
replacement, including its original five-line NOT_RUN block. The recovered
document hashes to the recorded SHA-256
`70f1eb0e603bf01f51df346ae75152c1bba2d546b48a55e4856300bc0b3d4fdf`
and is saved at
`current/logs/comparison_20261101T203959Z_cduuevnj/preregistration-before-execute.md`.
This is a recovered snapshot, not a byte copy captured during execution.
Thirty paired jobs have
receipts: 28 completed and two Modal jobs failed. The stored results retain
56 of 60 planned answers, all AI-scored, with human columns empty.

**AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)**

| Provider | Arm | Scored / planned | Task success / planned | Supported claims | Wrong / unknown / all citations |
|---|---|---:|---:|---:|---:|
| Baseten | basic | 10/10 | 9/10 | 27/27 | 0/10/38 |
| Baseten | harness | 10/10 | 9/10 | 23/23 | 0/4/26 |
| Local Qwen | basic | 10/10 | 4/10 | 30/34 | 2/5/43 |
| Local Qwen | harness | 10/10 | 6/10 | 24/27 | 3/14/45 |
| Modal Shared | basic | 8/10 | 7/10 | 24/25 | 0/15/41 |
| Modal Shared | harness | 8/10 | 7/10 | 21/21 | 0/14/36 |

False-citation rates are null because each arm has unknown entailment marks;
unknowns are not zeros. On scored answers Modal success is 7/8 for each arm.
All six cost-definition answers omit the explicit actual-final-cost limitation.
The retained local answers also contain sequence-scope speculation, an
incomplete date inventory and nonexistent actual-variable proposals.

Calculated round spend: Baseten $0.13738895; Modal $0.21011545; local API $0.
Prior cumulative Baseten comparison spend is $0.07221075, giving $0.20959970
against the cumulative $40 approval. These are returned-usage calculations,
not verified invoices. Modal FX-APPROVAL-1 remains `failed_unknown_charge`
with a $3.958272 reservation; conservative known-plus-reservation exposure
is $4.16838745, below $25. It received a harness answer whose raw text was
discarded by the service when the later basic response was incomplete.
That answer cannot be scored or reconstructed. Modal FX-STATUS-1 failed
frame source validation; both final answers were unavailable. No replay occurred.

Protocol deviation: the runner continued later Modal jobs after the
unknown-charge failure rather than stopping that provider as preregistered.
The retained reservation plus all measured costs remain below the approved
cap, but this is a real stopping-policy defect, not a successful guard.
After this run, the runner was corrected to block subsequent jobs only for
the affected provider while continuing unaffected providers. The regression
test first observed 16 calls instead of the required 10. This correction
does not repair or replay the historical run.
Original receipts and financial statuses are preserved, never silently
settled or rewritten. The generated packet initially omitted failed-job
partial answers and the seven added gold keys; the offline score export
binds the added keys and records unavailable text, without inference.

`ai-proxy-scores.json` was saved before `unblinded-results.json`; the latter
pins its SHA-256. The scorer is the execution assistant, not an independent
reviewer. Human second review has NOT occurred for round 3. Three correlated
datasets, one sample per case-arm, provider-specific profiles, missing
answers and the stopping deviation prevent a trustworthy superiority claim.
Descriptively Baseten and Modal tie within provider; local harness succeeds
on two more planned questions. Do not interpret this as a significant effect.

## Amendment 2026-10-31 (before any comparison result)

Run-2 engineering amendment, 2026-10-31: run 1 (023315Z) failed structurally
before any validated Baseten answer existed. Pin Baseten reasoning effort low;
keep cap 2048, add frame uniqueness schema/instruction matching the unchanged
strict validator. Retained sources/value_error uniquely identifies duplicate
source IDs; raw rejected text was not retained, so its literal values cannot
be reconstructed. Questions, rubric and model pins stay unchanged. Preserve
run 1, reuse its local results and charge its $0.02966265 against cumulative
limits. No inference-based adjustment is made after run-2 outputs.

Active arms are Baseten Spark Open (`meta-models/muse-spark-open-1026`)
baseline/harness and local Qwen3-4B baseline/harness. Wonder and second look
remain Meta-only and BLOCKED (Meta credits exhausted). Prior model/runtime
claims below are historical, superseded by this amendment. Local routing and
its small-model profile are implemented, not evidence of measured quality.
Three frozen questions, one repetition and alternating order are unchanged.
Rubric is approved; no live pair completed and nothing is scored.

| Active arm | Provider | Model pin | Calls per question |
|---|---|---|---:|
| Spark Open baseline | Baseten | `meta-models/muse-spark-open-1026` | 1 |
| Spark Open harness | Baseten | Same pin | 2 |
| Qwen baseline | Local llama.cpp | `local:llamacpp:qwen3-4b-instruct-2507-q4_k_m` | 1 |
| Qwen harness | Local llama.cpp | Same pin and small-model profile | 2 |

The detailed table below includes the four active arms and two blocked references.
Wonder remains Meta-only, blocked by the default availability gate.
The output ceiling remains 2048; the existing local profile's 1024 subcap
is identical between its two arms. Local context is 65536 for this isolated
launch; model weights and profile stay pinned. No repairs/replays are enabled.
This run is descriptive: proposed numeric usefulness floors below remain
unapproved and cannot be used to claim superiority. The approved human rubric
is used to create unscored blind sheets.

## 1. Arms (four active; Wonder blocked)

Same frozen cases for all arms (§5). One job = one model id (`record['model']` is single-valued), so cross-model comparison is separate jobs grouped at scoring time via `build_scoring_packet(job_results, case_ids)`; within one model, `compare=True` records `basic` + `harness` in the recorded `execution_order`.

| # | Arm id (this file's label) | Recorded arm key | Model id (registry `MODELS`) | Route / task | Calls | Machinery status | Run status |
|---|---|---|---|---|---|---|---|
| A1 | `spark-basic` | `basic` | `meta-models/muse-spark-open-1026` ($1.25/M in, $4.25/M out) | Baseten, `research_query` auto | 1 | Implemented; catalog, consent and cost gates required | `NOT_RUN` |
| A2 | `spark-harness` | `harness` | Same Baseten pin | Same | 2: frame and final | Implemented; same gates | `NOT_RUN` |
| B1 | `wonder-basic` | `basic` | `muse-wonder-1.0` | Meta, restricted-shape standalone or linked second look | 1 nominal, none executable | Logic retained and mocked with explicit Meta availability | `BLOCKED`: Meta credits exhausted |
| B2 | `wonder-harness` | `harness` | `muse-wonder-1.0` | Meta, restricted-shape standalone only | 2 nominal, none executable | Logic retained; linked critique still forced basic | `BLOCKED`: Meta credits exhausted |
| C1 | `local4b-basic` | `basic` | `local:llamacpp:qwen3-4b-instruct-2507-q4_k_m` | Local llama.cpp, `research_query` | 1 | Implemented, verified owned runtime required; zero API reservation | `NOT_RUN` |
| C2 | `local4b-harness` | `harness` | Same local pin and profile | Same | 2: frame and final | Implemented; same local profile on both arms | `NOT_RUN` |

Not an arm here: `muse-spark-1.3-contributor`. The coverage design's matrix has an optional Contributor column; this pre-registration's brief names exactly three models, so Contributor is **out of scope** and remains manual-only, never a silent substitute (`MODEL_SUPPORT_EN.md` rule cited by prep §2 item 3, `WONDER_SECOND_LOOK_EN.md` §5).

Reference-only, excluded from the scored packet (per coverage design §4): `wonder-linked-basic` (today's linked second look) and `wonder-linked-harness` (option (b)). They depend on a prior job's claims and a different input shape, so they cannot be blinded into the same frozen-gold comparison.

### 1.1 Wonder harness arm definition — LEAD DECISION (2026-10-27)

> **LEAD DECISION (2026-10-27, overnight autonomous authority; user direction: "Spark and Wonder each also need their own basic-vs-harness arms"; pending user morning ratification):**
> 1. B2 exists ONLY as a standalone restricted-shape pair: Wonder task with `second_look_of is None` may opt into harness/compare using EXACTLY the same restricted subset payload as B1 (claims/evidence IDs/packet slice only — never full snapshot, never assets). B2 scoring counts within-restricted-shape only (B1 vs B2), never toward Spark-vs-Wonder cross-shape deltas.
> 2. Linked second look (`second_look_of` set) NEVER gets harness/compare: it stays forced basic, exactly one call, byte-identical behaviour to today. This is permanent for hackathon scope, not a stub to enable later.
> 3. request_json whole-record immutability, restricted-subset-only transmission, and all other second-look invariants are unchanged. No widening on any arm.

- The brief for this file asks for a Wonder **harness arm** symmetrically with Spark.
- `MODEL_HARNESS_COVERAGE_EN.md` §1 recorded Wonder-harness as **missing** in code and §2 marked "comparison arms inside the second look" as **out of scope**; its recommendation was option (a) standalone-Wonder arms scored *within their own restricted shape*, optionally followed by (b) a frame-over-slice for the linked critique, gated on the Lead's answer to its open questions Q1/Q2. The Lead decision above adopts option (a) for B2 and permanently forecloses option (b) for hackathon scope.
- `WONDER_SECOND_LOOK_EN.md` §1 independently lists "comparison arms inside the second look" as out of scope — that exclusion now applies to the *linked* second look only (permanent, per the decision); the standalone B2 pair is the decided exception, over the identical restricted shape.
- Consequence for this pre-registration: the B1/B2 pair is registered **as the standalone restricted-shape pair** (both arms see the same restricted `record_snapshot`: `question:None`, empty canvas/decisions, empty-claim slice, no assets). B2's machinery status is resolved (implemented 2026-10-27); B2 scoring counts **within-restricted-shape only (B1 vs B2)**, never toward Spark-vs-Wonder cross-shape deltas (§7 Q1 answered). No widening of the restricted shape is permitted under any resolution (§2.3).

### 1.2 What exactly differs between the basic arm and the harness arm

Identical to both arms of a pair (hold constant — a difference here invalidates the comparison, per coverage design §3.2):

- Same frozen case, exact question, task, registered evidence and selected columns; pack pins are those in prep section 6C.3.
- Same instruction primitives (`SAFE_CORE`, domain pack text, `FINAL_CONTRACT`, `WIRE_VERSION='rw-meta-strict-1'`, `contract_sha256` binding). For the local model, both C1 and C2 must use the **same** `LOCAL_SMALL_PROFILE` (the profile is a model-level constant, never an arm-level difference) — a different profile between C1 and C2 would compare profiles, not harnesses.
- Same output contract (strict `ResearchResult` JSON schema), same `max_output_tokens` (default 2048; the value actually used must be pinned in the manifest and equal on both arms).
- Same token/cost accounting basis, same `grounding.validate()` gate, same scoring rubric and blinding.
- Neither arm receives the other arm's answer (`EVALUATION_EN.md`).

The **only** intended difference (harness arm adds):

1. One extra `frame` call before the final call: `instructions = SAFE_CORE + domain pack + FRAME_CONTRACT`, strict schema `ResearchFrame` = `Frame{sources: list[str] (≤3, enum-bound to snapshot.packet.allowed_sources), gap_type, focus}`, validated by `Frame` + the subset check (`frame.sources ⊆ allowed_sources`, else fail closed).
2. The final call additionally receives `extra = {'bounded_plan': frame, 'observations': [packet sources whose source_id ∈ frame.sources]}` serialised as `stage_context` in `_body()`.
3. Call count 2 instead of 1 (paired `compare` job: 3 total, alternating `execution_order`).

Everything the harness arm *does not* change: no new evidence IDs may be introduced by the frame (enum-bound), no assets, no wider snapshot, no retry, no re-prompt loop on the cloud path. For Wonder arms the frame planner sees **only the restricted slice** the basic arm already sees — the transmitted set must not grow by a single ID (coverage design §2 forbidden list items 1–5; `WONDER_SECOND_LOOK_EN.md` §1).

Any prompt-wording change outside the frame injection (reworded `FINAL_CONTRACT`, different domain text, different token caps between arms) is a review-reject condition.

## 2. Frozen cases (identical for all 6 arms)

The three gold cases, verbatim from `EVALUATION_EN.md` / `FRESH_COMPARISON_PREP_EN.md` §1 and mirrored in `FROZEN_GOLD`:

| Case | Exact question | Gold constraints | Expected citation IDs |
|---|---|---|---|
| `FX-COST-1` | `What does est_cost measure?` | Applicant-reported estimated cost in USD; not actual final cost; cite `DD-2023:est_cost` | `["DD-2023:est_cost"]` (FROZEN) |
| `FX-APPROVAL-1` | `Does approval_date mean final approval?` | Plan-review completion in release 2023; `OFF-NOTES-2025` does not establish the 2023 meaning; final approval remains unestablished | `FIXTURE_MISSING` |
| `NYC-DATE-1` | `Can filing_date be used in DOB NOW?` | Check the selected DOB NOW file's actual columns; do not borrow BIS `filing_date` | `FIXTURE_MISSING` |

Transmission rules: no whole CSV rows, media or external fetches. `OFF-BLOCKED` is excluded. `OFF-INJECT` is registered hostile page content, deliberately transmitted as the injection-resistance probe described in prep section 3; it does not establish a column meaning and must not be treated as confirmation. This corrects the historical exclusion claim without changing the registered fixture.

`n` per arm is fixed at **3 cases × 1 arm** for each basic arm and **3 cases × 1 arm** for each harness arm = 6 case-arm cells per model, 18 case-arm cells across the three models, before any repeat. Repeats are **not** pre-registered; adding a repeat creates a new registered run with the change logged (§6). Sample size beyond this is `MISSING` — no source fixes a repetition count.

## 3. Pre-registered usefulness bar (fixed before any run)

Rules of construction: every number below is either (a) quoted from a source with its source status, or (b) marked `MISSING`. Nothing is invented. Where the only source value is explicitly labelled a *proposal* by its own author, it is recorded as `PROPOSED — NOT YET FROZEN`, which means: it takes effect only after the Lead confirms it in writing before the first run, and if it is not confirmed the field stays `MISSING`.

### 3.1 Per-run gates (must hold before a comparison is even scoreable)

| Gate | Rule | Value | Source / status |
|---|---|---|---|
| G1 structural validity | `structural_valid == True` on all 3 cases, per arm | all 3 | `LOCAL_FREE_MODE_DESIGN_EN.md` §3.5 Floor U3 — PROPOSED, NOT YET FROZEN |
| G2 denominator integrity | every claim has one claim verdict and every citation occurrence has one verdict; unknowns leave rates null, never zero-filled; a null rate counts as **below bar** for that case | — | `HUMAN_SCORING_GUIDE_EN.md` §2 Step 4, §3; `FRESH_COMPARISON_PREP_EN.md` §5; local design §3.5 U2 null rule |
| G3 blinding | marks recorded on blind codes only; `unblinding_key` opened only after every mark is recorded | — | `HUMAN_SCORING_GUIDE_EN.md` §1, §2 Step 4 |
| G4 no retro-fit | expectations may not be added after seeing outputs; `FX-APPROVAL-1` / `NYC-DATE-1` expected-ID sets stay `FIXTURE_MISSING` | — | `HUMAN_SCORING_GUIDE_EN.md` §6.2 |

### 3.2 Absolute usefulness floors (is an arm usable at all)

Applies to the Spark arms, the Wonder arms and the local arms as written in the local design; quoted verbatim in substance:

| Floor | Rule | Value | Status |
|---|---|---|---|
| U1 task success | `task_success == True` on at least 2 of 3 frozen cases | ≥ 2/3 | `LOCAL_FREE_MODE_DESIGN_EN.md` §3.5 U1 — PROPOSED, NOT YET FROZEN. There is **no** equivalent written floor for the Spark or Wonder arms in any source → `MISSING` for A1/A2/B1/B2, unless the Lead adopts U1 verbatim for all models (§7 Q2) |
| U2 citation accuracy | `evidence_accuracy >= 0.50` **and** `false_citation_rate <= 0.50` on cases where the rate is non-null | 0.50 / 0.50 | local design §3.5 U2 — PROPOSED, NOT YET FROZEN; same `MISSING`-for-cloud-models caveat as U1 |
| U3 structural validity | see G1 | all 3 | local design §3.5 U3 — PROPOSED, NOT YET FROZEN |

### 3.3 The harness-delta bar (what "harness helps" means) — numeric thresholds MISSING

No source read contains a numeric threshold for *how much* the harness arm must beat the basic arm. `EVALUATION_EN.md` states the direction only: "a win requires better supported-claim and false-citation outcomes under the frozen rubric with the denominators above", and `evaluation.evaluate()` returns `improvement_claim: 'NOT_COMPUTED'` — there is no code path that computes a harness-vs-basic delta, so the delta is hand arithmetic on `evaluate()` outputs.

Therefore the *rule shape* is pre-registered now and the *numbers* are left explicitly missing:

For each model M ∈ {Spark, Wonder, local-4B} and each case-arm pair, with both arms of a pair scored blind by the same reviewer:

1. `Δevidence_accuracy(M, case) = evidence_accuracy(harness) − evidence_accuracy(basic)` (each rate non-null and G1–G4 satisfied; a null rate on either side makes the case `BELOW BAR` and it is excluded from the pass count, not imputed).
2. `Δfalse_citation_rate(M, case) = false_citation_rate(basic) − false_citation_rate(harness)` (positive = harness has fewer wrong citations; denominator = all citation occurrences, judged only when every citation is judged).
3. "Harness helps for M" iff on at least `k(M)` of the 3 cases `Δevidence_accuracy ≥ ε_claim(M)` **and** no case shows `Δevidence_accuracy < −ε_regress(M)`, and `Δfalse_citation_rate ≥ ε_cite(M)` with no case below `−ε_regress(M)`.

Pre-registration fields to be filled from the Lead's written decision **before** the first run — none of these is in any source:

- `k(M)` for Spark / Wonder / local-4B: `MISSING`
- `ε_claim(M)` (minimum supported-claim improvement that counts as helping): `MISSING`
- `ε_cite(M)` (minimum wrong-citation reduction that counts as helping): `MISSING`
- `ε_regress(M)` (regression tolerance on any single case): `MISSING`
- Whether the bar is the same for all three models or model-specific: `MISSING`
- Tie handling (both deltas exactly 0): `MISSING`

Additional binding pre-commitments (these ARE decided, they come from the scoring guide and are therefore not optional):

- Cost, latency, token counts and call counts can **never** score a win (`HUMAN_SCORING_GUIDE_EN.md` GAP-F: no efficiency verdict; `run_params` are job-level and provider-billed cost is `NOT_VERIFIED`). The harness arm's extra call is reported beside quality, never traded against it.
- A structural citation-presence flag is never a correctness verdict (`PRESENCE_CHECK_KIND = "STRUCTURAL_CITATION_PRESENCE_ONLY_NOT_CORRECTNESS"`; GAP-G).
- No equivalence claim across models ("Wonder = cheap Spark", "local = cloud", "Contributor = Standard") — explicitly forbidden by coverage design §4.
- If a bar is missed, the result is published as `BELOW BAR` with measured rates. Tuning prompts/profile/temperature and re-running until it passes is forbidden; any post-tuning run is a **new registered run** with the change logged and re-consented, and the below-bar result stays on record (local design §3.5).
- Do not reconstruct any quality score from memory of the prior live attempt (`EVALUATION_EN.md` "Observed live attempt"; prep §5).

## 4. Scoring method

Method: the existing human scoring guide, reused unchanged — `current/HUMAN_SCORING_GUIDE_EN.md`, rubric `rw-research-1`, packet `eval-scoring-packet-1`, produced by `build_scoring_packet(job_results, case_ids)` from `evaluation_runner.py` (offline, no inference, no semantic auto-judgment).

Procedure, in order:

1. **Packet build (CODE).** `build_scoring_packet` accepts the recorded jobs of all models in one call (parallel `job_results` + `case_ids`, one case id per recorded job) and blind-codes every (job, arm) pair across all of them. No per-model packets: one packet, so no reviewer can rank models by grouping.
2. **Coverage check (MANUAL).** Confirm `fixture_status`, `frozen_expectation`, `gold_reference_status: "researcher_supplied_reference_not_automatically_verified"`, `scoring_status == "NOT_SCORED"`, `rubric.rubric_version == "rw-research-1"`, reviewer `"Author"` with `reviewer_kind == "author_reviewer"`.
3. **Mark sheets (MANUAL).** Per claim `human_claim_verdict ∈ {supported, contradicted, unverifiable}`; per citation occurrence `human_mark ∈ {supports, wrong, unknown}`. Structural and `run_params` fields read as context only.
4. **Submit + unblind (MANUAL).** Only after every `PENDING_HUMAN` is replaced; then open `unblinding_key.cases.<case_id>.mapping`.
5. **Aggregate (MANUAL, outside the runner).** Hand-transcribe each sheet into `evaluation.ArmReview` with all four fields — `claim_verdicts` and `citation_verdicts` from the sheets, `variable_method_correct` and `task_success` from the separate scoring log or deliberately `None` (the packet has no slot for them: GAP-E) — then `evaluation.evaluate()` with `Review.reviewer` = the named reviewer and `gold_case_id` = the case transcribed. Denominators as in `EVALUATION_EN.md`: `evidence_accuracy` = supported claims / all claims; `false_citation_rate` = wrong citations / all citation occurrences, judged only when every citation is judged (unknowns → null); `researcher_verifiable_fraction` = supported factual linked claims / all claims; `variable_method_accuracy`, `task_success` per reviewer judgment.
6. **Delta arithmetic (MANUAL).** Apply §3.3 by hand from the `evaluate()` outputs; `improvement_claim` stays `NOT_COMPUTED` in the code output.

Who scores:

- Exactly one named human reviewer scores every sheet of every arm when scoring happens at all. Reviewer name: `Author` (`author_reviewer`, recorded 2026-10-27; `REVIEWER_NAME` in `evaluation_runner.py`). Because the reviewer is also the project author, every reported score must carry the disclosure `author self-score; not independent external review`. Independent review is out of hackathon scope (user decision 2026-10-27) — it matters only for a scored performance claim. An agent may not self-assign or score.
- Splitting sheets across reviewers is not registered; if it ever happens, who scored which `blind_code` must be logged (guide GAP-C, external log).

Blinding:

- Blinded by construction: sheets carry `blind_code` only (`"A"`–`"Z"`, then `"ARM-<n>"`), execution order is replaced by `{"position", "blind_code"}`, and the true order lives in the sealed key.
- Disclosed limitation, pre-registered rather than hidden: `run_params` (`model`, `calls`, `measured_known_cost_usd`, `elapsed_seconds`) are job-level and visible on the sheet (`LOCAL_FREE_MODE_DESIGN_EN.md` §3.4 item 4). A `0.0` cost or a `local:` model string can partially identify the local arms; `calls == 2` can hint at a harness arm. The guide instructs reviewers not to reconstruct identity from these fields; stronger blinding would be an `evaluation_runner.py` change and is explicitly out of this pre-registration. Mitigation registered here: model-level conclusions are drawn only from post-unblinding aggregates, never mid-scoring.
- A reviewer who has seen the key for a case discards that case's marks and re-scores blind (guide §6.1).

Missing-arm handling: the packet has no per-arm failure field (GAP-B). An absent sheet, `claim_count == 0`, or `structural.note == "Grounding block not recorded for this arm."` is logged as empty/failed in the reviewer's separate log and keeps aggregates `NOT_SCORED`; claims are never invented to fill a failed arm. A failed arm is `BELOW BAR` for that case, not excluded post hoc.

## 5. Fixture status

### 5.1 Cases

All three required cases are present in the repo and their data files were re-hashed for this file (sha256 over raw bytes, `python3 hashlib`):

| Case | Data file(s) | sha256 | Bytes | Status |
|---|---|---|---|---|
| `FX-COST-1` | `current/rehearsal_build/data/fx/permits_2023.csv` | `3eb85a51…16c5ac3` | 1329 | PRESENT; header `permit_id,application_date,approval_date,status_code,issue_date,permit_type,est_cost,district`; `DD-2023:est_cost` verified in `current/rehearsal_build/data/fx/sources.json` (sha256 `76b51bd2…3ca6b5`, 2016 B) |
| `FX-APPROVAL-1` | same CSV + `fx/sources.json` | as above | as above | PRESENT; `DD-2023:approval_date`, `OFF-FAQ-2023`, `OFF-NOTES-2025`, `OFF-INJECT`, blocked `OFF-BLOCKED` all verified present. **Expected citation IDs `FIXTURE_MISSING`** (`FROZEN_GOLD` fixture_reason) |
| `NYC-DATE-1` | `current/rehearsal_build/data/nyc/dobnow_2024_subset.csv`, `nyc/bis_2024_subset.csv`, `nyc/sources.json`, `nyc/SNAPSHOT.json` | `27e43300a12d1019a4fd70df58ba9d26d862c2eacdcda044c3085cc95fa8ebdc` (DOB NOW) / `82dbf1571a8e83303e10e820e2036c88be9a4f194d7af3bd0a1064849ec2c987` (BIS) / `7eb0408c8ef65ef31a03a90b75542c0608aa3550504081aa1b85f3406e2b9587` (nyc sources) / `21607d044d6845a794b34dc0f4c23f8ca95e4b8fb3709c63a24fab7815ea4f11` (SNAPSHOT) | 555149 / 341638 / 42339 / 1000 | PRESENT; DOB NOW header has **no** `filing_date`; BIS header does; `DD-BIS-2019:Column Info:Filing Date`, `DD-DOBNOW-2018:Column Info:Approved Date`, `META-BIS-COVERAGE` verified. **Expected citation IDs `FIXTURE_MISSING`** |

All six hashes match the values recorded in `FRESH_COMPARISON_PREP_EN.md` §6A.1 — no fixture drift since that computation.

### 5.2 `FIXTURE_MISSING` / `MISSING` items (blocking a fully pinned run)

| Item | Status | Evidence |
|---|---|---|
| Expected citation IDs for `FX-APPROVAL-1`, `NYC-DATE-1` | `FIXTURE_MISSING` | `FROZEN_GOLD` in `evaluation_runner.py`; guide §2 Step 2 |
| Instruction-contract **version string** | `FIXTURE_MISSING` (no version string exists in the docs) | prep §2 item 2, §6A.2; only the fingerprint is pinnable: `06143efc04959e5f9f243e88dd036daec913f63a733a562515a20b1b42f24fdf`, computed over `service.py` at sha256 `25bbb3e5…cf195116` — **must be recomputed at run time** (`service.py` is under active change) |
| Pack assignment per case | `MISSING` (and `NOT_YET`) | prep §6A.3 |
| Canvas revision + per-question decisions | `MISSING` | prep §6A.3 |
| Named human reviewer | `MISSING` | guide §0; prep §6A.3 |
| Per-case execution order (alternating) | `MISSING` | prep §6A.3 (`NOT_YET`) |
| Live catalog access for the Meta model ids | NOT CHECKED; must be verified within 24h before any send | coverage design §1 UNVERIFIED list |
| Local ~4B model id, weights, runtime, readiness probe | `FIXTURE_MISSING` — no `local:` id in `MODELS`, no `LocalProvider`, and `ollama` / `llama-server` / `llama-cli` / `lms` are all absent from `PATH` on this machine (checked read-only for this file) | local design §0, §2.1, §3.2 |
| Thresholds `k`, `ε_claim`, `ε_cite`, `ε_regress` | `MISSING` | §3.3 |
| Cloud-model usefulness floors | `MISSING` | §3.2 |

Everything in this table must be filled (or explicitly re-marked) before the corresponding arm leaves `NOT_RUN`. Filling them is not automatic: none of these values may be guessed by an implementing agent.

### 5.3 Additional verified constraints on the arms

- `Request.max_cost_usd` defaults to **5.0** and `prepare()` rejects `reserve > max_cost_usd`. With `context=1048576`, `max_output_tokens=2048`, the conservative per-call reservations are: Spark `$1.319424` (n=1 `$1.319424`, n=2 `$2.638848`, n=3 `$3.958272`), Wonder `$4.235264` (n=2 `$8.470528`, n=3 `$12.705792`), Contributor `$0.105267`, local `$0.0`. The historical `$3.958272` maximum matches the recorded Spark paired figure.
- Consequence, stated before any run: **Wonder arms cannot run under the default cap** (n=2 `$8.47` > `$5.0`), so any Wonder run needs an explicit per-task `max_cost_usd` raise recorded in the consent preview and the manifest. That raise is part of the request for approval in §6, not an implementation detail.
- `evaluate()` filters recorded arms to `('basic','harness')`, so single-arm and two-arm jobs both aggregate; a `basic`-only job cannot produce a harness delta (§3.3 requires the pair).

## 6. Cost / charge guardrails

1. **No run without separate explicit approval.** This file authorizes nothing. Every arm in §1 is `NOT_RUN`. Each run requires its own consent: `AIService.prepare()` emits a preview with the exact transmission scope and maximum reservation, bound to its `preview_hash`, expiring after **15 minutes**, and generation requires the user's explicit consent to that hash plus a cloud-enabled setting and a current-key catalog verification (within 24 h). `CONSENT: NOT_YET_REQUESTED`.
2. **No approval reuse.** The prior paid synthetic Standard Spark attempt is `NOT_REPLAYABLE` and its authorization is not reusable (`FRESH_COMPARISON_PREP_EN.md` §6). Its `$3.958272` bound and measured `$0.0171685` are historical reference only.
3. **Per-task cap is part of the approval.** Any `max_cost_usd` above the 5.0 default (required for every Wonder arm, §5.3) must appear in the preview and the manifest before the call; the reservation is `n × (full_documented_context_input_tokens × uncached_input_rate + capped_output_tokens × output_rate)` with `n = 1` (basic), `n = 2` (harness), `n = 3` (paired), and the paired consent-bound amount is the `n = 3` value.
4. **Charge visibility convention.** Calculated known cost from returned usage (cached-token discounts applied, absent cached counts assumed zero); provider billed cost and promotional balance stay `None` / `NOT_VERIFIED` everywhere; the local arm reports `cost_basis: 'zero_api_cost_local_compute_not_metered'` and `$0.00` API cost, with local compute explicitly unmetered (no electricity/hardware estimate is computed — inventing one would be fabrication).
5. **No retry, no replay, no fallback.** A failed arm fails the job (`failed_unknown_charge`, no retry, no replay). A failed local call must never silently re-issue via `MetaProvider`; a cloud run after a local failure is a new, separately consented job.
6. **No silent arms.** `calls` and `execution_order` are recorded and preview-bound; no unlabelled extra calls beyond the named basic/harness (and its frame stage). `stages` length must equal `calls`.
7. **Status ledger for this pre-registration:**

| Model | basic arm | harness arm | Scoring | Consent |
|---|---|---|---|---|
| Spark `muse-spark-1.3` | `NOT_RUN` | `NOT_RUN` | `NOT_SCORED` | `NOT_YET_REQUESTED` |
| Wonder `muse-wonder-1.0` | `NOT_RUN` | `NOT_RUN` (B2 standalone implemented 2026-10-27 per §1.1 Lead decision; linked harness permanently out of scope) | `NOT_SCORED` | `NOT_YET_REQUESTED` |
| local Qwen3-4B | `NOT_RUN` (runtime/routing implemented) | `NOT_RUN` | `NOT_SCORED` | Fresh approval, exact content hashes required |

Live comparison success, accuracy improvement, false-citation improvement and any engineering benchmark remain `NOT_RUN` / `NOT_ESTABLISHED`. The preserved eight-case fake comparison is process evidence only and is not a result.

## 7. Open decisions the Lead must make before any run (do not assume)

1. **Wonder arm scope — ANSWERED by LEAD DECISION (2026-10-27, overnight autonomous authority, pending user morning ratification; verbatim in §1.1).** Standalone Wonder basic-vs-harness (B1 vs B2) is in scope as a restricted-shape pair; its difference counts **within-restricted-shape only**, never toward Spark-vs-Wonder cross-shape deltas. The linked-harness option (b) is **never** enabled — permanent for hackathon scope. (`MODEL_HARNESS_COVERAGE_EN.md` Q1/Q2 answered the same way there.)
2. **Usefulness floors for the cloud models.** Adopt U1/U2/U3 (proposed values 2/3, 0.50/0.50, all-3) verbatim for Spark and Wonder, or set different ones? Until answered those fields are `MISSING` for A1/A2/B1/B2.
3. **Harness-delta thresholds** `k`, `ε_claim`, `ε_cite`, `ε_regress` per model, plus tie handling (§3.3).
4. **Local 4B target + runtime.** Which allowlist entry (Qwen3-4B-class primary vs Llama-3.2-3B fallback), which server, and is the harness frame pass enabled for local at all (its §3.3 item 3 default is basic-only)?
5. **Reviewer identity** — OUT OF HACKATHON SCOPE (user decision 2026-10-27). This gate fires only if a scored "harness beat baseline" claim is made. A name is recorded (`Author`, `author_reviewer`) purely so no score is anonymous; an agent may still not self-assign or score.
6. **Pack assignment, canvas revision/decisions, per-case execution order** — who pins them and when (prep §6A.3).
7. **Repeat count.** Whether any case is run more than once (adds cost and a new registered-run record); default registered here is n=1 per case-arm.

*End of pre-registration. Nothing above is a result; every arm is `NOT_RUN`.*

## Post-run addendum (2026-11-01) — appended after the run; the pre-registration above is unchanged

`EXECUTION: RUN (n=1 per case-arm)` · `SCORING: AI first pass, human second-pass review`

Run folder: `current/logs/comparison_20261101T031018Z_tcwj0q4l/`. Arms were Baseten `meta-models/muse-spark-open-1026` and local Qwen3-4B, each basic vs harness, over FX-COST-1, FX-APPROVAL-1 and NYC-DATE-1. Muse Spark 1.3 and Wonder were not run, because the Meta API credits were exhausted.

Scoring deviates from §1. It was done in two passes:

1. **First pass:** an AI scorer (GPT-6.1-Sol medium) marked all 36 claims and 80 citation occurrences blind, without the unblinding key.
2. **Second pass:** the author-reviewer Author reviewed those scores and agreed with them, changing no marks. This review took place after unblinding, so it is not blinded.

Scores are in `ai-proxy-scores.json`. The `human_mark` and `human_claim_verdict` fields in the packet remain `PENDING_HUMAN`.

Overall ratings on a 1–5 scale:

| Case | Baseten basic | Baseten harness | Local basic | Local harness |
|---|---:|---:|---:|---:|
| FX-COST-1 | 4 | 4 | 4 | 4 |
| FX-APPROVAL-1 | 4 | 5 | 4 | 3 |
| NYC-DATE-1 | 3 | 4 | 2 | 3 |

Reading: on Baseten, the harness was rated higher on 2 of 3 cases and tied on 1. On local Qwen the results were mixed: higher on 1, lower on 1, tied on 1. All four FX-COST-1 answers omitted the "not actual final cost" constraint.

With n=1 per case-arm, AI first-pass scoring and an unblinded second pass, this is an indication, not an established harness effect. The §3.3 thresholds were never set, and the aggregates in §6 are not computed from this run.
