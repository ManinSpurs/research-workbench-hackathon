# Claim-Evidence verification

Status: implemented and independently reviewed in this build (`app/claim_check.py`,
`frontend/claim_check_ui.js`). The local path ran live in the packaged build on 2026-10-31.
The cloud path is live on Baseten Muse Spark Open: job aichk-d6f42801700a ran a Korean
claim check in 3.4 s for a calculated 0.004114 USD.

1. Numeric reconciliation compares numbers with table cells without a model; unmatched
   numbers show the nearest cell and difference.
2. Statement checking asks a model for supporting or contradicting passages. Code checks
   quotes verbatim against the excerpt actually sent. Calls require an exact preview of
   content, destination, model and cost, plus consent. The result is a secretary's
   suggestion the researcher decides on, never a ruling that a sentence is true.
   Entailment needs a human reader; without a verified quote the app says so.

The first live call exposed two defects, since fixed and reviewed: an internal ID where the
source title belonged, and Korean rendering claim as the billing term. A later 10-call
benchmark, 5 per arm, leaked neither.

It does not score research quality, repair a claim to make it pass, or report a mismatch as
a success. The research comparison ran on 2026-11-01 on Baseten Muse Spark Open and
local Qwen3-4B, basic versus harness, n=1 per case-arm.
Scoring: AI first-pass scoring, human second-pass review (after unblinding).
See the contribution ledger note for ratings and limitations; no established harness
effect is claimed. Preregistered aggregates remain NOT_SCORED.
