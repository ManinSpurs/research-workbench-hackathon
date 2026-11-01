# Model support and evidence

Documentation and account catalog checked on 2026-10-26. Official base URL: https://api.meta.ai/v1. Account enumeration verifies listing, not every feature or inference entitlement.

| Model / binding | Route and output | Current evidence |
| --- | --- | --- |
| muse-spark-1.3 | /responses, typed research text | Account listed; two real responses received; paired workflow stopped on plan validation |
| muse-wonder-1.0 | /responses, complex review text | Account listed; mock routing/transport tests; live inference NOT_RUN |
| muse-spark-1.3-contributor | /responses, research text | User permits selection; account listed; lower-price route locally tested; live inference NOT_RUN |
| muse-image-1.0 | /responses, generation/edit | Account listed; generated provenance and canvas preservation mocked; one live smoke row COMPLETED per run-summary dated 2026-10-26 (calc $0.01, billed NOT_VERIFIED; single-row result, not general support; synthetic fixture only, quality NOT_SCORED) — see current/logs/LIVE_SMOKE_ROWS2-5_2026-10-26/SUMMARY.md; broader live inference NOT_RUN |
| muse-voice-transcribe-1.0 | /asr/transcribe multipart, transcript/turns | Account listed; WAV request and audio-turn correspondence mocked; one live smoke row COMPLETED per run-summary dated 2026-10-26 (calc $0.0001, billed NOT_VERIFIED; single-row result, not general support; synthetic fixture only — per run summary, 440 Hz tone -> "Yeah.", quality NOT_SCORED) — see current/logs/LIVE_SMOKE_ROWS2-5_2026-10-26/SUMMARY.md; broader live inference NOT_RUN |
| sam-3.1 | POST /segmentation/interactive (multipart, non-streaming), detections-JSON corner conversion; mask raster decode NOT_IMPLEMENTED | Account listed; detections/corner conversion mocked; image live attempt HALTED per run-summary dated 2026-10-26 ("HALTED pre-send-settle" wording) — ai_jobs.status and ai_spend.status both failed_unknown_charge; provider_http_400; job ai-9fcfc811f1dc; reservation $0.0025 retained — see current/logs/LIVE_SMOKE_ROWS2-5_2026-10-26/SUMMARY.md row 4 and scratch DB current/logs/LIVE_SMOKE_ROWS2-5_2026-10-26/scratch_rows2to5_qa.db (ai_jobs.status and ai_spend.status both failed_unknown_charge for ai-9fcfc811f1dc; ai_spend reservation 0.0025, actual_cost NULL; re-verified 2026-10-27); video execution blocked per code (current/rehearsal_build/app/ai/service.py prepare()) |
| Spark Open | Self-/partner-hosted candidate | No invented Model API binding; hardware/server benchmark NOT_RUN |
| Glimmer | Self-hosted candidate | Hardware/server benchmark NOT_RUN |
| meta-models/Muse-Glimmer-30B on Modal | Separate text-only connection-code preparation; OpenAI Chat Completions protocol | No deployment or live inference authorized. GPU-time billing is not a token price. Existing Modal account login does not establish an inference endpoint. |

The Modal connection is a separate Glimmer path, not a replacement name for Spark or
Wonder. Configuration and mocked transport checks must not be presented as deployed
model access. Until a serving endpoint, enforceable GPU-cost controls and explicit
execution consent exist, the app must keep this route non-executable.

Wonder Full is not treated as a direct API model. Automatic routing keeps Standard Spark for ordinary tasks and Wonder for method/code/manuscript review. Incompatible manual model/task combinations fail before transmission. Contributor selection is explicit and included in the preview; the already approved Standard smoke was not switched.

Sources: [models](https://dev.meta.ai/docs/models), [rates](https://dev.meta.ai/docs/pricing-rate-limits), [Responses](https://dev.meta.ai/docs/protocols/responses), [structured output](https://dev.meta.ai/docs/structured-output), [files](https://dev.meta.ai/docs/file-handling), [Voice](https://dev.meta.ai/docs/speech-to-text), [SAM](https://dev.meta.ai/docs/sam/reading-segmentation), [Muse Image](https://dev.meta.ai/docs/image-generation).
