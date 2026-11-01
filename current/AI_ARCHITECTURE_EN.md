# Optional AI integration architecture

## Preserved foundation

The existing project, dataset registration, source catalog, question refinement, four-stage rehearsal harness, Social Science and Engineering packs, research canvas, local SQLite persistence, decisions, restart briefing, deletion and comparison remain in their original modules. The integration is additive. `app/offline.py` supplies honest local question capture, not a pretend local LLM. Missing cloud access does not prevent canvas/profile work.

## Eight components

| Component | Implementation | Responsibility |
| --- | --- | --- |
| Provider Registry | app/ai/registry.py | Official Meta origin, Baseten origin and self-hosting candidate status |
| Capability Registry | Model records and typed task map | Text reasoning vs image output vs transcription vs segmentation |
| Task Router | route() | Automatic task model or explicit compatible manual model |
| Multimodal Processor | app/ai/inputs.py | Local registration, hash, size/header/WAV checks, typed inline blocks |
| Grounding Validator | app/ai/grounding.py | Claim links, actual-column scope, explicit release mismatch and SAM geometry |
| Agent Orchestrator | AIService.execute() | Bounded public plan, app-selected registered observations, final typed result |
| Cost / Usage Tracker | ai_usage and ai_spend | Attempts, returned usage, published-rate calculations and retained uncertainty |
| Local / Cloud Controller | settings, preview and execution gates | Default-off cloud and continued local editing |

## Execution boundary

A preparation operation has no inference side effects. It pins the model, instruction version, selected media metadata, registered input hashes, original question, current canvas and this question's decisions. The preview shows this exact scope and the maximum reservation. It expires after 15 minutes. Generation requires explicit consent to its hash, a cloud-enabled setting and an account catalog verified for the current key within 24 hours.

The reasoning path is a direct single call or a bounded two-call extension: public evidence plan, ordinary registered-source read, final result. A paired task makes at most three inference calls, alternating arm order across prepared tasks. No recursive delegation, critic loop, provider tools, automatic retries or silent fallback is enabled. This is not the full planned H2 runtime.

Strict Responses JSON schemas now constrain both final results and intermediate plans. The plan's source IDs use an enum from the approved catalog. This follow-up was added after the first live attempt failed intermediate-plan validation. It is tested with mock transport and was exercised live on Baseten in the 2026-11-01 comparison run 2 (all nine calls validated). Shape validation does not establish scientific correctness.

## Provider routes

Two paid routes exist and are selected per prepared job, never silently. Since 2026-10-31 the Meta API credits are exhausted: research tasks with model "auto" resolve to the pinned Baseten slug `meta-models/muse-spark-open-1026` (`https://inference.baseten.co/v1`) with reasoning effort `low`, the floating alias `meta-models/muse-spark-open` is refused, and the Meta Spark ids (`meta_spark_blocked_credits_exhausted`) and Wonder and second-look tasks (`wonder_blocked_meta_credits_exhausted`) are refused before any network call. The `meta` route (`https://api.meta.ai/v1`) remains in code for when a key with credits is entered. An explicit `provider` field names a route, and without one the model id decides. A slug that does not belong to the named route fails closed in both directions, so a Baseten model can never leave on the Meta wire or the reverse. The prepared record stores the resolved provider, and `execute()` re-binds to that same transport.

Each route has its own credential (`BASETEN_API_KEY` or an owner-only `baseten_api_key` file, separate from `META_API_KEY`/`meta_api_key`) and its own account verification, stored per provider in `ai_provider_catalog`. Verifying one route never authorizes the other; the legacy `ai_settings` catalog columns keep holding the Meta record so existing readers are unchanged. `POST /ai/verify-account` takes an optional `{"provider": ...}` and defaults to Meta.

Both keys can be entered in the UI: one masked field per provider in the Secretary AI panel, saved through `POST /ai/keys`. That endpoint accepts only a registered provider id — never a path — and writes to that provider's fixed owner-only file. The secret is write-only: responses carry presence flags, never the value, and no endpoint reads a key back.

The Baseten route is text-only: the verified contract documents no asset modality for that slug, so an asset selection fails closed. Its published rates (input $1.25 / cached input $0.15 / output $4.25 per 1M tokens) equal the `muse-spark-1.3` row, so reservation and settlement arithmetic is shared with no second accounting path.

**Known limitation, enforced client-side:** Baseten accepts `json_schema` for this model but does not enforce it. The existing `Frame` and `ResearchResult` validation therefore runs before any result is stored or settled; a non-conforming body fails the job closed. No repair, coercion or retry is attempted. In the 2026-11-01 comparison, run 1 failed closed this way (reasoning used the 2048 output cap; one duplicate-source plan); after pinning effort `low` and requiring distinct plan sources, run 2 completed all nine Baseten calls with validated plans and results.

## Persistence and privacy

All application state remains in the same local SQLite DB. ai_settings is scoped to the current OS user and separates cloud activation from secret availability. ai_assets stores locally registered media; ai_jobs stores consent-bound payloads and outcomes; ai_usage stores usage and request fingerprints rather than raw transport/error text; ai_reviews stores explicit reviewer assessments; it is currently write-only via the API-only POST /ai/jobs/{jid}/review endpoint (no read path yet). Question deletion and session cleanup include those tables.

The content-free ai_spend ledger survives question deletion and restart. It prevents spend resets and preserves unknown charges after transport failures or interruptions. The current release conservatively retains a failed job's full reservation, including validation-only failures; billing reconciliation and more precise known-charge settlement remain follow-up work.

The private key file is outside this tree. No route returns the key. The provider uses a fixed HTTPS origin, no environment proxies, no redirects and no transport retries. Unapproved browser origins are rejected on AI routes. The app is a single-user localhost prototype, not a production multi-tenant authentication service. Bind it to loopback only.

`store:false` avoids server-managed response history; it does not claim universal provider-side deletion. The UI displays Contributor data use when selected. Mixdog's own chat/model calls and its settings are independent of this application's ledger and consent gate.

## Media limits

Local registration currently supports PNG/JPEG/WebP, PDF, MP4, PCM WAV and UTF-8 text, up to 8 MiB each and eight registered assets per question. At most three are selected for a task. Header checks are not full parser or antivirus validation. No external file URL is fetched by the app.

Reasoning calls can carry images, PDFs, video and text inline. Voice file transcription is mono 16-bit PCM WAV at 16 or 24 kHz; other formats require user-controlled conversion first. Turn timestamps and speaker labels are retained as model outputs, not verified identities.

SAM image segmentation preserves explicit frame/object IDs, source dimensions, pixel boxes and encoded base85 masks. Binary raster decoding, physical area measurement and video frame-count cost control remain unimplemented. Muse Image generation/editing is labeled AI-generated; built-in search and shell use are disabled. It never overwrites the data profile or canvas.

## Cost semantics

Text reservations use the full documented context at the uncached input rate plus capped output, multiplied by the maximum call count. They are deliberately much larger than a small prompt's likely bill. Voice uses validated WAV duration, images use per-image pricing, and SAM image calls use per-image pricing. SAM video execution is blocked pending safe frame accounting.

Returned usage produces calculated costs with cached-token discounts when reported. Missing cached counts are assumed zero, conservatively. Provider billed cost and remaining promotional balance stay UNKNOWN. The default $150 local allowance is a cap reference, not confirmation that credits were granted. Another client such as Mixdog can spend the same team's credits outside this ledger.
