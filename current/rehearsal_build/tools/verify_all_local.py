"""Local/mock-only full verification entry point. No paid calls, no credits consumed.

Runs, from ``current/rehearsal_build`` (paths resolved from this script's own
location, so invoke as ``.venv/bin/python tools/verify_all_local.py``):

  1. the full pytest suite with the project ``.venv`` python,
  2. ``node --check frontend/ai.js``,
  3. ``node --check frontend/app.js``,
  4. ``node --check frontend/latex.js``,
  5. ``node --check frontend/voice_recorder.js``,
  6. ``node tests/review_frontend.mjs``,
  7. ``node tests/review_ai_receipt.mjs``,
  8. ``node tests/review_voice_confirm.mjs``,
  9. ``node tests/review_ui_polish.mjs``,
  10. ``node tests/review_segmentation_overlay.mjs``,
  11. ``node tests/review_latex_layout.mjs``,
  12. ``node tests/review_secretary_panel.mjs``,
  13. ``node tests/review_preparation_card.mjs``,
  14. ``node tests/review_deletion_preview_ui.mjs``,
  15. ``node tests/review_secretary_guidance.mjs``,
  16. ``node tests/review_voice_recorder.mjs``,
  17. ``node tests/review_coaching_radar.mjs``,
  18. ``node tests/review_wonder_second_look_ui.mjs``,
  19. ``node tests/review_adaptation_lines.mjs``,
  20. ``node tests/review_startup_integration.mjs``,
  21. ``node tests/review_ai_review_readback.mjs``,
  22. ``node tests/review_provider_key_fields.mjs``,
  23. ``tools/verify_http_restart.py`` -- ONLY with ``--with-http``
      (so the default run is 22 steps; 23 with ``--with-http``).

Every step uses mock/fake transports on loopback only; nothing here contacts a
real model provider, spends money, or consumes credits. The pytest suite itself
refuses non-loopback network access (see ``tests/conftest.py``).

Why the HTTP restart check is opt-in: ``tools/verify_http_restart.py`` takes no
arguments and keeps its test database in a temp dir, but it spawns a fake model
server plus two uvicorn backend processes on loopback ports and writes log
artifacts outside any temp dir (``WB_VERIFY_LOGS``, defaulting to a ``logs/``
directory next to the project root). That makes it slower, port-binding, and
state-writing outside a temp dir, so unattended default runs skip it; pass
``--with-http`` to include it.

Exit status is non-zero if any step fails. A failing step is always reported as
FAIL -- failures are never converted to PASS.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]  # current/rehearsal_build
VENV_PY = ROOT / ".venv" / "bin" / "python"
HTTP_TOOL = ROOT / "tools" / "verify_http_restart.py"

PASS = "PASS"
FAIL = "FAIL"


def run_step(name, argv, pass_markers=(), timeout=1200, env=None):
    """Run one step; return (ok, detail). Never raises on step failure."""
    print(f"=== STEP: {name} ===", flush=True)
    print(f"$ {' '.join(str(a) for a in argv)}", flush=True)
    try:
        proc = subprocess.run(
            list(argv),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        print(f"{FAIL}: {name} (executable not found: {exc})", flush=True)
        return False, "executable not found"
    except subprocess.TimeoutExpired:
        print(f"{FAIL}: {name} (timed out after {timeout}s)", flush=True)
        return False, f"timeout after {timeout}s"
    output = (proc.stdout or "") + (proc.stderr or "")
    if output:
        print(output.rstrip(), flush=True)
    missing = [m for m in pass_markers if m not in (proc.stdout or "")]
    if proc.returncode == 0 and not missing:
        token = f" (exit 0{'; ' + ', '.join(pass_markers) if pass_markers else ''})"
        print(f"{PASS}: {name}{token}", flush=True)
        return True, f"exit 0; markers: {', '.join(pass_markers) or 'n/a'}"
    reasons = []
    if proc.returncode != 0:
        reasons.append(f"exit {proc.returncode}")
    reasons.extend(f"missing pass token {m!r}" for m in missing)
    print(f"{FAIL}: {name} ({'; '.join(reasons)})", flush=True)
    return False, "; ".join(reasons)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local/mock-only full verification (no paid calls, no credits)."
    )
    parser.add_argument(
        "--with-http",
        action="store_true",
        help=(
            "Also run tools/verify_http_restart.py (fake-only loopback check). "
            "Opt-in because it binds loopback ports, spawns servers, is slow, "
            "and writes logs outside a temp dir."
        ),
    )
    args = parser.parse_args(argv)

    steps = []
    if not VENV_PY.is_file():
        print(f"Missing project venv python: {VENV_PY}", flush=True)
        print("Create it first (see Start_Mock.command); cannot verify without it.", flush=True)
        return 2
    if sys.executable:
        pass  # informational only; VENV_PY is authoritative for pytest
    steps.append(
        ("pytest (full suite, .venv python)",
         [str(VENV_PY), "-m", "pytest", "tests", "-q"],
         ()),
    )
    steps.append(("node --check frontend/ai.js", ["node", "--check", "frontend/ai.js"], ()))
    steps.append(("node --check frontend/app.js", ["node", "--check", "frontend/app.js"], ()))
    steps.append(("node --check frontend/latex.js", ["node", "--check", "frontend/latex.js"], ()))
    steps.append(("node --check frontend/voice_recorder.js", ["node", "--check", "frontend/voice_recorder.js"], ()))
    steps.append(
        ("review_frontend.mjs",
         ["node", "tests/review_frontend.mjs"],
         ("REVIEW_FRONTEND_PASS", "PACK_FRONTEND_PASS")),
    )
    steps.append(
        ("review_ai_receipt.mjs",
         ["node", "tests/review_ai_receipt.mjs"],
         ("AI_RECEIPT_FRONTEND_PASS", "AI_RECEIPT_SERVER_BRANCH_PASS")),
    )
    steps.append(
        ("review_voice_confirm.mjs",
         ["node", "tests/review_voice_confirm.mjs"],
         ("VOICE_CONFIRM_FRONTEND_PASS",)),
    )
    steps.append(
        ("review_ui_polish.mjs",
         ["node", "tests/review_ui_polish.mjs"],
         ("UI_POLISH_PASS",)),
    )
    steps.append(
        ("review_segmentation_overlay.mjs",
         ["node", "tests/review_segmentation_overlay.mjs"],
         ("SEGMENTATION_OVERLAY_PASS",)),
    )
    steps.append(
        ("review_latex_layout.mjs",
         ["node", "tests/review_latex_layout.mjs"],
         ("LATEX_VIEW_PASS",)),
    )
    steps.append(
        ("review_secretary_panel.mjs",
         ["node", "tests/review_secretary_panel.mjs"],
         ("SECRETARY_ORDER_PASS",
          "LAUNCH_RENDER_PASS",
          "LAUNCH_OPEN_PASS",
          "PER_PROJECT_BRIEFING_PASS",
          "LAUNCH_FAILURE_PASS",
          "SECRETARY_PANEL_PASS")),
    )
    steps.append(
        ("review_preparation_card.mjs",
         ["node", "tests/review_preparation_card.mjs"],
         ("PREPARATION_CARD_UI_PASS",)),
    )
    steps.append(
        ("review_deletion_preview_ui.mjs",
         ["node", "tests/review_deletion_preview_ui.mjs"],
         ("DELETION_PREVIEW_UI_PASS",)),
    )
    steps.append(
        ("review_secretary_guidance.mjs",
         ["node", "tests/review_secretary_guidance.mjs"],
         ("SECRETARY_GUIDANCE_UI_PASS",)),
    )
    steps.append(
        ("review_voice_recorder.mjs",
         ["node", "tests/review_voice_recorder.mjs"],
         ("VOICE_RECORDER_FRONTEND_PASS",)),
    )
    steps.append(
        ("review_coaching_radar.mjs",
         ["node", "tests/review_coaching_radar.mjs"],
         ("COACHING_RADAR_PASS",)),
    )
    steps.append(
        ("review_wonder_second_look_ui.mjs",
         ["node", "tests/review_wonder_second_look_ui.mjs"],
         ("SECOND_LOOK_UI_PASS",)),
    )
    steps.append(
        ("review_adaptation_lines.mjs",
         ["node", "tests/review_adaptation_lines.mjs"],
         ("ADAPTATION_LINES_PASS",)),
    )
    steps.append(
        ("review_startup_integration.mjs",
         ["node", "tests/review_startup_integration.mjs"],
         ("STARTUP_INTEGRATION_PASS",)),
    )
    steps.append(
        ("review_ai_review_readback.mjs",
         ["node", "tests/review_ai_review_readback.mjs"],
         ("AI_REVIEW_READBACK_FRONTEND_PASS",)),
    )
    steps.append(
        ("review_provider_key_fields.mjs",
         ["node", "tests/review_provider_key_fields.mjs"],
         ("PROVIDER_KEY_FIELDS_PASS",)),
    )
    if args.with_http:
        if not HTTP_TOOL.is_file():
            print(f"{FAIL}: verify_http_restart.py (missing: {HTTP_TOOL})", flush=True)
            steps.append(("verify_http_restart.py", [str(VENV_PY), str(HTTP_TOOL)], ("\"result\": \"PASS\"",)))
        else:
            steps.append(
                ("verify_http_restart.py (fake-only, --with-http)",
                 [str(VENV_PY), str(HTTP_TOOL)], ('"result": "PASS"',)),
            )
    else:
        print("Skipping verify_http_restart.py (pass --with-http to include it).", flush=True)

    env = dict(os.environ)
    env.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    results = []
    for name, cmd, markers in steps:
        ok, detail = run_step(name, cmd, pass_markers=markers, env=env)
        results.append((name, ok, detail))

    print("", flush=True)
    print("=== VERIFY_ALL_LOCAL SUMMARY (local/mock only; no paid calls) ===", flush=True)
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"  {name.ljust(width)}  {'PASS' if ok else 'FAIL'}  ({detail})", flush=True)
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print(f"RESULT: FAIL ({len(failed)} of {len(results)} steps failed)", flush=True)
        return 1
    print(f"RESULT: PASS (all {len(results)} steps passed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
