# Development project content

This folder holds the seed for the in-app development project "Research Workbench -
hackathon build". It is authored content, not user data: everything here is derived from
the repository's own design documents in `current/` and from the build's recorded
measurements, and nothing here is private.

- `plan.json`: the steps. A project, four literature folders, concise notes, recorded
  decisions with their questions, one LaTeX draft and one results table.
- `measurements.csv`: the real numbers the draft claims are reconciled against, each with
  its provenance in the source column.
- `notes/`: the literature notes, one file per note, written as English screen text.
- `draft/main.tex`: the submission write-up draft whose claims the demo verifies.

Two consumers apply this plan and both are idempotent:

- `tools/seed_dev_project.py` seeds a running app over HTTP or a database directly.
- `_seed` in `packaging/backend_server.py` seeds every fresh packaged review session,
  beside the synthetic FX sample.

The project is created by the upload mechanism, not by a fixture kind: the dataset is
`measurements.csv` with a dictionary entry per column. It names no chip, so it is not
hidden behind one, and it opens from whichever chip is active.

The content is public-safe by rule: no credential or credential path, no absolute local
path, no material from any private folder, and no claim that the build has not earned
(`NOT_RUN`, `NOT_IMPLEMENTED`, `IN_PROGRESS` and `NOT_SCORED` stay stated wherever they are
still true). Every rule above is a grep in `tests/test_seed_dev_project.py`, not an
intention.

## The measurements and where they come from

Every number in `measurements.csv` is labelled
"Lead-observed live run, 2026-10-31 (log outside repository)", except the two that are
declared constants in `app/ai/local_runtime.py`. The latencies are 4.615 s, 4.64 s,
4.89 s, 5.21 s and 6.37 s across jobs ai-60d36123b56f, ai-96f536df03a5,
ai-b8297f813eb8, ai-7a62cfdcd455 and ai-0e8e3734d1bf, and the last of those ran in the
packaged DMG app, so the range shown is 4.6 to 6.4 s. A claim check with sources is
slower on the same local path: 7.97 s and 11.09 s across jobs aichk-7b84a5395773 and
aichk-79e4909e85e6, so the band shown for it is 8.0 to 11.1 s. The cloud claim check and
the reasoning-effort benchmark carry their own rows, with the job id where one exists and
with every dollar figure marked as calculated rather than invoice-verified. The full-suite
count of 2,224 passed with 1 xfailed is a dated snapshot from that day, not the current
count.

## The claims, and what the demo shows for each

The Claim-Evidence check is implemented and independently reviewed in this build: the
engine is `app/claim_check.py` and the interface is `frontend/claim_check_ui.js`. It
verifies every returned quote verbatim against the excerpt actually sent, reconciles
numbers against the results table with no AI and no cost, and requires an exact preview
with the researcher's consent before any model call. What it returns is a secretary's note:
a hedged, first person suggestion in the researcher's language that the researcher decides
on, never a ruling. When no quote is verified, the app says so in its own words instead of
showing the model's note. These are the claims the demo runs it against.

1. Local mode makes no API call, so a rehearsal costs nothing in API credits: supported by
   the local-model note (`zero API cost`, `no request leaves the machine`).
2. The app stops the local model server it started when the app quits: supported by the
   same note (`stops the child on quit`).
3. Grounding checks confirm that a sentence is true: contradicted by the Claim-Evidence
   note, which says the returned reading is a secretary's suggestion backed by a verbatim
   quote match, never a ruling that a sentence is true.
4. In rehearsal the local model answered in 4.6 to 6.4 seconds per question: the results
   table reconciles both ends of the range.
5. The context bound, the template overhead and the weights size: three matches
   (8192 tokens, 8 tokens, 2,497,281,120 bytes).
6. The full Python suite reached 2,224 passed and 1 xfailed: two matches.
7. An earlier count recorded 2,300 passed: one near mismatch against 2,224, which is the
   deliberate error the demo shows.

A verdict is the model's judgement, backed by a verbatim quote match, and the secretary's
note is a suggestion the researcher decides on. It is not a claim of semantic truth, and no
wording here may grow into one.

## What is shipped and what is not

Implemented and independently reviewed in this build: the Claim-Evidence check, in
`app/claim_check.py` and `frontend/claim_check_ui.js`, on the local path and on the cloud
path.

The local path was verified live in the packaged build on 2026-10-31. The local Qwen3-4B
wrote a Korean secretary note for a claim check in 7.97 s (job aichk-7b84a5395773) and in
11.09 s in an earlier build's run (job aichk-79e4909e85e6). Both cost 0.

The cloud path is live on Baseten Muse Spark Open (`meta-models/muse-spark-open-1026`), and
it has run against a real claim and a real stored note. The claim was "In rehearsal the
local model answered in 4.6 to 6.4 seconds per question." and the source was the local-model
note. The first live call, job aichk-d6f42801700a, returned a supported verdict with one
quote verified verbatim in 3.4 s, for a calculated 0.004114 USD on 1,105 input and 643
output tokens. That call exposed two defects, both since fixed and independently reviewed:
the note showed an internal source ID where the source title belonged, and Korean rendered
the word for an assertion as the word for a billing claim. Every cost in this file is
calculated, not invoice-verified.

A reasoning-effort benchmark then ran five alternating calls per arm. At low, the median was
2.55 s (range 2.32 to 4.04 s, 0.0151 USD for the arm); at none it was 2.11 s (range 1.91 to
2.61 s, 0.0126 USD). All ten calls were supported with one verified quote, and no note
leaked an ID or used the billing word. Reasoning effort stays low, because the 0.45 s gap
sits inside run-to-run spread and caching favored the none arm, so the gap is not evidence
of a real speed difference.

## Current comparison: round 3

AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)

Ten questions: 3 FX, 4 DOB NOW, 3 BIS; 56/60 planned answers retained and
AI-scored. Human round-3 review is pending, not completed.

| Provider | Basic success / planned | Harness success / planned |
|---|---:|---:|
| Baseten | 9/10 | 9/10 |
| Local Qwen | 4/10 | 6/10 |
| Modal Shared | 7/10 | 7/10 |

Calculated round spend: Baseten 0.13738895 USD; Modal 0.21011545 USD;
local API 0 USD. Baseten cross-round total 0.20959970 USD, below 40 USD.
Modal has an unresolved 3.958272 USD reservation: conservative exposure
4.16838745 USD, below 25 USD. No invoice verification or superiority claim.
Two Modal jobs failed; one partial answer was discarded and cannot be scored.
Later Modal jobs continued despite the preregistered unknown-charge stop rule.
Correlated datasets, one repetition, provider profiles and missing answers
limit the conclusion. See the round-3 post-run preregistration and
the round-3 `ai-proxy-scores.json`.

## Historical round 2

The research comparison ran on 2026-11-01: Baseten `meta-models/muse-spark-open-1026`
and local Qwen3-4B, each basic versus harness, n=1 per case-arm across FX-COST-1,
FX-APPROVAL-1 and NYC-DATE-1. All 9 Baseten calls completed, at a calculated cost of
about 0.0722 USD, not invoice-verified.

Scoring: AI first-pass scoring, human second-pass review (after unblinding).
GPT-6.1-Sol medium blind-scored all 36 claims and 80 citation occurrences;
author-reviewer Author then agreed with those scores, changing no marks. The second pass
was not blinded or independent. The result table is in `notes/hackathon_ledger.md`
and `draft/main.tex`, separate from the latency Measurements table.

Baseten harness ratings were higher on 2 of 3 cases and tied on 1. Local Qwen was mixed:
higher on 1, lower on 1, tied on 1. All four FX-COST-1 answers omitted "not actual final
cost". This is an indication, not an established harness effect: n=1, AI first pass,
unblinded second pass, thresholds never set. Preregistered aggregates remain NOT_SCORED.
Sources: `HARNESS_COMPARISON_PREREG_EN.md`, Post-run addendum, and `ai-proxy-scores.json`.
Wonder remains blocked because Meta credits are exhausted.

Latency is not one band. The 4.6 to 6.4 second range describes short questions; a claim
check that sends source excerpts took 8.0 to 11.1 seconds on the same local path.

