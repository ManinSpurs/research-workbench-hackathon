Local model: the pinned install

Source: the local runtime contract in the launcher.

The app starts the 4B llama-server child on loopback with a per-run token, verifies
identity, and stops the child on quit. No cloud fallback.

- Cost: local compute only, zero API cost. No request leaves the machine.
- Context: 8192 tokens (app/ai/local_runtime.py), enforced per request.
- Template overhead: 8 tokens, measured at launch by the app-owned supervisor.
- Weights: supplied, digest-pinned, 2,497,281,120 bytes.
- Latency: 4.615 s, 4.64 s, 4.89 s, 5.21 s and 6.37 s on short questions, so 4.6 to 6.4 s.
- A claim check with sources is slower: 7.97 s (aichk-7b84a5395773, packaged DMG) and
  11.09 s (aichk-79e4909e85e6), so 8.0 to 11.1 s. Both cost 0; the note was Korean.

Provenance: Lead-observed live run, 2026-10-31 (log outside repository); jobs
ai-60d36123b56f, ai-96f536df03a5, ai-b8297f813eb8, ai-7a62cfdcd455 and ai-0e8e3734d1bf.

The cloud check is implemented, live on Baseten Muse Spark Open, job aichk-d6f42801700a
at 3.4 s. The research comparison ran on 2026-11-01 on Baseten and local Qwen3-4B,
basic versus harness, n=1 per case-arm.
Scoring: AI first-pass scoring, human second-pass review (after unblinding).
Local Qwen: harness higher on 1, lower on 1, tied on 1.
No established harness effect is claimed.
Preregistered aggregates remain NOT_SCORED.