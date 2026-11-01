"""Contract tests for tools/verify_all_local.py (local/mock only; no paid calls).

Scope guard (anti-recursion): these tests MUST NEVER invoke the script's
default full run, which would re-run this whole pytest suite inside itself
(slow and recursive). Subprocess invocations are therefore limited to the two
cheapest plumbing probes:

    verify_all_local.py --help                (expect exit 0)
    verify_all_local.py --not-a-real-flag     (expect exit non-zero)

Everything else exercises the script's own module-level seams in-process:

* ``run_step`` is called directly with trivial ``sys.executable -c`` stubs,
  proving a failing exit code / missing pass-marker is reported FAIL and is
  never converted to PASS.
* ``main(argv)`` is called with ``run_step`` monkeypatched to a stub, proving
  the ``--with-http`` skip logic, the per-step PASS/FAIL summary format, and
  the non-zero exit on any failure -- without running any real step.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_all_local.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "verify_all_local_under_test", TOOL
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool_module(monkeypatch):
    module = load_tool_module()
    # Hermetic: these tests must not depend on a real `.venv` existing beside the
    # tool. Without this, a fresh clone (no `.venv` yet) fails 22 tests because
    # main() returns 2 before any step runs. The missing-venv behaviour has its
    # own test, which overrides VENV_PY explicitly.
    monkeypatch.setattr(module, "VENV_PY", Path(sys.executable))
    return module


def _run_tool(*args):
    # Cheapest real invocations only: argument parsing, never the full run.
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- Black-box plumbing (subprocess, no full suite) ---


def test_help_exits_zero_and_advertises_with_http():
    proc = _run_tool("--help")
    assert proc.returncode == 0
    assert "--with-http" in proc.stdout


def test_unknown_flag_exits_nonzero():
    proc = _run_tool("--definitely-not-a-real-flag")
    assert proc.returncode != 0


# --- run_step seam: failures are never converted to PASS ---


def test_run_step_success_reports_pass(tool_module, capsys):
    ok, _ = tool_module.run_step(
        "stub-ok", [sys.executable, "-c", "print('hello')"]
    )
    assert ok is True
    out = capsys.readouterr().out
    assert "PASS: stub-ok" in out


def test_run_step_failing_exit_is_fail_never_pass(tool_module, capsys):
    ok, detail = tool_module.run_step(
        "stub-broken", [sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    assert ok is False
    assert "exit 3" in detail
    out = capsys.readouterr().out
    assert "FAIL: stub-broken" in out
    assert "PASS" not in out


def test_run_step_missing_marker_is_fail_despite_exit_zero(
    tool_module, capsys
):
    ok, detail = tool_module.run_step(
        "stub-marker",
        [sys.executable, "-c", "print('no token here')"],
        pass_markers=("TOKEN_NEVER_PRINTED_XYZ",),
    )
    assert ok is False
    assert "missing pass token" in detail
    out = capsys.readouterr().out
    assert "FAIL: stub-marker" in out
    assert "PASS" not in out


def test_run_step_present_marker_is_pass(tool_module, capsys):
    ok, _ = tool_module.run_step(
        "stub-token",
        [sys.executable, "-c", "print('TOKEN_XYZ_PRESENT')"],
        pass_markers=("TOKEN_XYZ_PRESENT",),
    )
    assert ok is True
    out = capsys.readouterr().out
    assert "PASS: stub-token" in out


def test_run_step_missing_executable_is_fail(tool_module, capsys):
    ok, detail = tool_module.run_step(
        "stub-gone", ["/nonexistent/nope-binary-xyz", "--version"]
    )
    assert ok is False
    assert detail == "executable not found"
    out = capsys.readouterr().out
    assert "FAIL: stub-gone" in out
    assert "PASS" not in out


# --- main() seam (run_step stubbed): http skip, summary, exit status ---


def _stub_all_pass(calls):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        calls.append(name)
        return True, "stubbed exit 0"

    return fake


def test_main_skips_http_by_default(tool_module, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tool_module, "run_step", _stub_all_pass(calls))
    rc = tool_module.main([])
    assert rc == 0
    assert len(calls) == 22
    assert not any("http" in name.lower() for name in calls)
    out = capsys.readouterr().out
    assert "Skipping verify_http_restart.py" in out
    assert "=== VERIFY_ALL_LOCAL SUMMARY" in out
    for name in calls:
        assert name in out
    assert out.count("  PASS  ") >= 22  # one per-step summary token
    assert "RESULT: PASS" in out


def test_main_includes_http_with_flag(tool_module, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tool_module, "run_step", _stub_all_pass(calls))
    rc = tool_module.main(["--with-http"])
    assert rc == 0
    assert len(calls) == 23
    assert any("http" in name.lower() for name in calls)
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out


def _stub_all_pass_recording_markers(calls):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        calls.append((name, tuple(pass_markers)))
        return True, "stubbed exit 0"

    return fake


def test_main_includes_ui_polish_step_token_gated(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_markers(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(calls)
    assert "review_ui_polish.mjs" in by_name
    assert "UI_POLISH_PASS" in by_name["review_ui_polish.mjs"]


def test_main_includes_segmentation_overlay_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_markers(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(calls)
    assert "review_segmentation_overlay.mjs" in by_name
    assert "SEGMENTATION_OVERLAY_PASS" in by_name["review_segmentation_overlay.mjs"]


def test_main_includes_app_js_syntax_check(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    argv_by_name = {name: list(argv) for name, argv in
                    ((n, a) for n, a, _ in calls)}
    assert "node --check frontend/app.js" in argv_by_name
    assert argv_by_name["node --check frontend/app.js"] == [
        "node", "--check", "frontend/app.js",
    ]


def test_main_preserves_existing_node_step_order(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    names = [name for name, _, _ in calls]
    expected = [
        "pytest (full suite, .venv python)",
        "node --check frontend/ai.js",
        "node --check frontend/app.js",
        "node --check frontend/latex.js",
        "node --check frontend/voice_recorder.js",
        "review_frontend.mjs",
        "review_ai_receipt.mjs",
        "review_voice_confirm.mjs",
        "review_ui_polish.mjs",
        "review_segmentation_overlay.mjs",
        "review_latex_layout.mjs",
        "review_secretary_panel.mjs",
        "review_preparation_card.mjs",
        "review_deletion_preview_ui.mjs",
        "review_secretary_guidance.mjs",
        "review_voice_recorder.mjs",
        "review_coaching_radar.mjs",
        "review_wonder_second_look_ui.mjs",
        "review_adaptation_lines.mjs",
        "review_startup_integration.mjs",
        "review_ai_review_readback.mjs",
        "review_provider_key_fields.mjs",
    ]
    assert names == expected


def _stub_all_pass_recording_all(calls):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        calls.append((name, list(argv), tuple(pass_markers)))
        return True, "stubbed exit 0"

    return fake


def test_main_includes_latex_syntax_check(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    argv_by_name = {name: list(argv) for name, argv in
                    ((n, a) for n, a, _ in calls)}
    assert "node --check frontend/latex.js" in argv_by_name
    assert argv_by_name["node --check frontend/latex.js"] == [
        "node", "--check", "frontend/latex.js",
    ]


def test_main_includes_latex_layout_step_token_gated(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_markers(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(calls)
    assert "review_latex_layout.mjs" in by_name
    assert "LATEX_VIEW_PASS" in by_name["review_latex_layout.mjs"]


def test_main_includes_secretary_panel_step_all_tokens_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_secretary_panel.mjs" in by_name
    argv, markers = by_name["review_secretary_panel.mjs"]
    assert list(argv) == ["node", "tests/review_secretary_panel.mjs"]
    for token in (
        "SECRETARY_ORDER_PASS",
        "LAUNCH_RENDER_PASS",
        "LAUNCH_OPEN_PASS",
        "PER_PROJECT_BRIEFING_PASS",
        "LAUNCH_FAILURE_PASS",
        "SECRETARY_PANEL_PASS",
    ):
        assert token in markers


def test_main_includes_preparation_card_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_preparation_card.mjs" in by_name
    argv, markers = by_name["review_preparation_card.mjs"]
    assert list(argv) == ["node", "tests/review_preparation_card.mjs"]
    assert "PREPARATION_CARD_UI_PASS" in markers


def test_main_includes_deletion_preview_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_deletion_preview_ui.mjs" in by_name
    argv, markers = by_name["review_deletion_preview_ui.mjs"]
    assert list(argv) == ["node", "tests/review_deletion_preview_ui.mjs"]
    assert "DELETION_PREVIEW_UI_PASS" in markers


def test_main_includes_secretary_guidance_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_secretary_guidance.mjs" in by_name
    argv, markers = by_name["review_secretary_guidance.mjs"]
    assert list(argv) == ["node", "tests/review_secretary_guidance.mjs"]
    assert "SECRETARY_GUIDANCE_UI_PASS" in markers


def test_main_includes_voice_recorder_syntax_check(tool_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    argv_by_name = {name: list(argv) for name, argv in
                    ((n, a) for n, a, _ in calls)}
    assert "node --check frontend/voice_recorder.js" in argv_by_name
    assert argv_by_name["node --check frontend/voice_recorder.js"] == [
        "node", "--check", "frontend/voice_recorder.js",
    ]


def test_main_includes_voice_recorder_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_voice_recorder.mjs" in by_name
    argv, markers = by_name["review_voice_recorder.mjs"]
    assert list(argv) == ["node", "tests/review_voice_recorder.mjs"]
    assert "VOICE_RECORDER_FRONTEND_PASS" in markers


def test_main_includes_coaching_radar_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_coaching_radar.mjs" in by_name
    argv, markers = by_name["review_coaching_radar.mjs"]
    assert list(argv) == ["node", "tests/review_coaching_radar.mjs"]
    assert "COACHING_RADAR_PASS" in markers


def test_main_includes_wonder_second_look_ui_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_wonder_second_look_ui.mjs" in by_name
    argv, markers = by_name["review_wonder_second_look_ui.mjs"]
    assert list(argv) == ["node", "tests/review_wonder_second_look_ui.mjs"]
    assert "SECOND_LOOK_UI_PASS" in markers


def test_main_includes_adaptation_lines_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_adaptation_lines.mjs" in by_name
    argv, markers = by_name["review_adaptation_lines.mjs"]
    assert list(argv) == ["node", "tests/review_adaptation_lines.mjs"]
    assert "ADAPTATION_LINES_PASS" in markers


def test_main_includes_ai_review_readback_step_token_gated(
    tool_module, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        tool_module, "run_step", _stub_all_pass_recording_all(calls)
    )
    assert tool_module.main([]) == 0
    by_name = dict(
        (name, (list(argv), tuple(markers)))
        for name, argv, markers in calls
    )
    assert "review_ai_review_readback.mjs" in by_name
    argv, markers = by_name["review_ai_review_readback.mjs"]
    assert list(argv) == ["node", "tests/review_ai_review_readback.mjs"]
    assert "AI_REVIEW_READBACK_FRONTEND_PASS" in markers


def test_main_failing_new_harness_means_nonzero_exit_and_fail_summary(
    tool_module, monkeypatch, capsys
):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        if "review_ui_polish" in name or "review_segmentation_overlay" in name:
            return False, "stubbed exit 1"
        return True, "stubbed exit 0"

    monkeypatch.setattr(tool_module, "run_step", fake)
    rc = tool_module.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "RESULT: PASS" not in out
    assert "review_ui_polish" in out
    assert "review_segmentation_overlay" in out


def test_main_failing_latex_steps_means_nonzero_exit_and_fail_summary(
    tool_module, monkeypatch, capsys
):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        if "latex" in name:
            return False, "stubbed exit 1"
        return True, "stubbed exit 0"

    monkeypatch.setattr(tool_module, "run_step", fake)
    rc = tool_module.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "RESULT: PASS" not in out
    assert "latex" in out


def test_main_failing_secretary_panel_means_nonzero_exit_and_fail_summary(
    tool_module, monkeypatch, capsys
):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        if "review_secretary_panel" in name:
            return False, "stubbed exit 1"
        return True, "stubbed exit 0"

    monkeypatch.setattr(tool_module, "run_step", fake)
    rc = tool_module.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "RESULT: PASS" not in out
    assert "review_secretary_panel" in out


def test_main_failing_step_means_nonzero_exit_and_fail_summary(
    tool_module, monkeypatch, capsys
):
    def fake(name, argv, pass_markers=(), timeout=1200, env=None):
        if "review_frontend" in name:
            return False, "stubbed exit 3"
        return True, "stubbed exit 0"

    monkeypatch.setattr(tool_module, "run_step", fake)
    rc = tool_module.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "=== VERIFY_ALL_LOCAL SUMMARY" in out
    assert "FAIL" in out
    assert "RESULT: FAIL" in out
    assert "RESULT: PASS" not in out
    summary_lines = [ln for ln in out.splitlines() if "review_frontend" in ln]
    assert summary_lines and all("FAIL" in ln for ln in summary_lines)


def test_main_missing_venv_python_exits_two(tool_module, monkeypatch):
    monkeypatch.setattr(
        tool_module, "VENV_PY", Path("/nonexistent/venv/bin/python")
    )
    assert tool_module.main([]) == 2
