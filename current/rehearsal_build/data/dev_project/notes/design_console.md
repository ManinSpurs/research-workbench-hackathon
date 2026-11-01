# Design: the console and the AI layer

Source documents: AI_ARCHITECTURE_EN.md, Research_Workbench_v1_8_Integrated_Design_EN.md.

The Workbench is a local research console. A project holds registered datasets, a pinned
source catalog, questions, a research canvas, recorded decisions and a launch briefing.
One SQLite file is the only authority, and the interface is served from the same machine.

The AI layer is additive. Question refinement, the four-stage rehearsal harness, an
evidence-grounded result contract and a trust receipt sit beside the older canvas,
profile and domain-pack code, which stays. With no model reachable the app still captures
questions, profiles datasets and saves work. It does not pretend that a local model
answered.

A domain pack is an instruction pack, never code. Two packs ship with the app, and a
researcher-made chip becomes a usable pack at start-up, because a chip they built for
their own field has to work for their own questions.

Status at the time of this note: typed routing, the consent snapshot, the usage ledger and
the Secretary panel are implemented. The basic-versus-harness comparison ran on
2026-11-01 on Baseten Muse Spark Open and local Qwen3-4B, n=1 per case-arm.
Scoring: AI first-pass scoring, human second-pass review (after unblinding).
See the contribution ledger note for ratings and limitations; no established harness
effect is claimed. Preregistered aggregates remain NOT_SCORED.
