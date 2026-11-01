Decision: review the build before it ships

Sources: SUBMISSION_PACKAGE_CHECKLIST_EN.md and the review build's release scope.

1. Meta reasoning runs on Baseten-hosted Muse Spark Open while Meta credits are exhausted,
   chosen per prepared job, never silently; an unapproved paid attempt is refused.
2. A local Qwen3-4B model is the zero-cost arm: opt-in per job, never replacing the cloud
   route on its own.
3. Review each DMG on a fresh database before release; failed builds are not shipped.
4. Claim-evidence verification is the core feature, implemented and independently reviewed
   here (`app/claim_check.py`, `frontend/claim_check_ui.js`). Code reconciles numbers.
   Model quotes must match verbatim against the excerpt actually sent, after preview and
   consent; the result is a secretary's suggestion. The local path ran live in the packaged
   build on 2026-10-31; the cloud path is live on Baseten, where job aichk-d6f42801700a took
   3.4 s for a calculated 0.004114 USD. The research comparison ran on 2026-11-01 on
   Baseten Muse Spark Open and local Qwen3-4B, basic versus harness, n=1 per case-arm.
   Scoring: AI first-pass scoring, human second-pass review (after unblinding).
   See contribution ledger. Preregistered aggregates remain NOT_SCORED.
5. Reasoning effort stays low for the cloud claim check: five alternating calls per arm gave
   a 2.55 s median at low against 2.11 s at none, a 0.45 s gap inside run-to-run spread, and
   caching favored none. All 10 calls were supported with 1 verified quote. Costs are
   calculated, not invoice-verified.
