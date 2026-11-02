# Research Workbench

A local-first research secretary that briefs you on where you left off,
connects claims to evidence, and previews model transmissions before explicit
consent. A chip packages reusable research context, not a model or executable.

## Four menus

- Literature Review: organize sources, notes and documents in the library.
- Research: projects, questions, evidence and the secretary briefing.
- Writing: drafts, citations and LaTeX work.
- Conversation Review: inspect, keep or remove past conversations.

Settings is a separate control for model routes, keys, local runtime and language.

You can talk with the Secretary about anything, including the research itself.
It answers with the open project's briefing and recent conversation as context,
using local Qwen by default; a paid Baseten model can be enabled in Settings after
a one-time consent. Conversations are kept in Conversation Review, and a message
becomes a research question only when you press "Register as research question".

## Install

Download the macOS DMG (Apple silicon) from the
[v1.1.0 release](https://github.com/ManinSpurs/research-workbench-hackathon/releases/tag/v1.1.0).
Open it and drag Research Workbench into Applications. The build is not signed
or notarized yet, so macOS will block the first launch. Try to open it once, then
go to System Settings > Privacy & Security, scroll to Security and click
"Open Anyway" for Research Workbench, and confirm. Only do this if you trust the
download; do not disable system security globally. No DMG is included in source
control.

## Run from source

Python 3.11+ and Node.js are required. From `current/rehearsal_build`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
./Start_Mock.command
```

The mock launcher starts local services. In another terminal in the same
directory, `npm run app` starts Electron. Stop the launcher and its services
when finished. Model weights and a llama.cpp runtime are separate optional
downloads, not bundled dependencies. LaTeX compilation requires its optional
engine. No cloud inference is required to explore the app.

## Try a chip

Use Open Chip in the app to import
`current/rehearsal_build/chips/builtin/sample_permit_notebook.chip`.
Then try `deliverables/research_workbench_dev_log_en.chip` or
`deliverables/research_workbench_dev_log_ko.chip`. These authored development
records demonstrate resuming context, recorded decisions and evidence.
See `current/CHIP_FILE_FORMAT_EN.md` for the format.

## Models and privacy

Supported research routes include local Qwen3-4B Instruct, Baseten Muse Spark
Open (`meta-models/muse-spark-open-1026`), and Modal Shared Muse Spark Open.
Local inference requires separately configured weights/runtime. Cloud routes
require your own account and credentials; balances and promotional coverage
are not verified by the app. Opening Settings does not run a model.
Keys are read from environment variables or owner-only local configuration
files, not committed source. Transmission previews, content-bound consent and
app-side spending limits do not replace provider billing controls.

## Round-3 comparison

**AI first-pass scoring, human second review after unblinding**

This describes the review workflow, not a completed human review: round-3
human second review has NOT occurred. Task successes per ten planned questions:

| Provider | Basic | Harness |
|---|---:|---:|
| Baseten | 9/10 | 9/10 |
| Local Qwen | 4/10 | 6/10 |
| Modal Shared | 7/10 | 7/10 |

56/60 planned answers were retained; Modal had eight scored answers per arm.
The scorer was the execution assistant, not an independent reviewer. Three
correlated datasets, one sample per case-arm, unequal provider profiles,
missing answers and unknown citation entailment prevent superiority claims.
False-citation rates are null, not zero. A Modal unknown-charge failure did
not stop subsequent jobs as preregistered; this historical deviation was not
replayed or repaired. Raw logs are deliberately excluded.
The preregistration and its dated post-run record are in
`current/HARNESS_COMPARISON_PREREG_EN.md`; earlier NOT_RUN markers there
describe historical stages, not the final round-3 status.

## What was built

See `current/HACKATHON_CONTRIBUTION_LEDGER_EN.md` for file-cited inherited
versus hackathon contributions. The prior local rehearsal research/evidence
workflow is inherited; secretary briefings, adaptive readiness, consent-bound
model orchestration and chips include event-period additions. Dated ledger
entries retain their original scope and are not blanket claims of completion.

## Limits and checks

Human round-3 scoring: NOT_RUN. Independent scientific validation: NOT_RUN.
Autonomous background research and full segmentation masks: NOT_IMPLEMENTED.
Legacy/experimental provider entries do not establish live endpoint support.
No inference, paid smoke tests or deployment are part of source verification.

```sh
cd current/rehearsal_build
TMPDIR=/tmp .venv/bin/python -m pytest tests -q
```

Browser review harnesses use Playwright (`npx playwright install chromium`).
Some historical harnesses require excluded private verification artifacts or
an independently running mock API; those are not a source-only test lane.

## License

The project's own source is released under the MIT License (see `LICENSE`).
Third-party components keep their own licenses: Python packages, Electron,
the bundled TeX distribution in the DMG, and model weights (for example Qwen3-4B,
Apache-2.0), which are downloaded separately and are not part of this repository.
