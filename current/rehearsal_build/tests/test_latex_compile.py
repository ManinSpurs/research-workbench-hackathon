"""Protocol stubs only, not evidence of real TeX or native sandbox correctness."""
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import latex, latex_compile as lc
from app.harness import Conflict
from test_latex_store import h, seed, client
import pytest


def draft(h):
    pid, _ = seed(h)
    return latex.create_draft(h, pid, {})


def enable(monkeypatch):
    monkeypatch.setattr(lc, "discover", lambda: {"pdflatex": ("/trusted/bin/pdftex", "/trusted", "/trusted/bin")})
    monkeypatch.setattr(lc, "sandbox_available", lambda: True)


def test_absent_and_invalid(h, monkeypatch):
    d = draft(h)
    c = lc.Compiler()
    monkeypatch.setattr(lc, "discover", lambda: {})
    r = asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
    assert r["status"] == "toolchain_absent" and r["pdf_url"] is None
    for body in ({}, {"source": "manual", "command": "id"}):
        with pytest.raises(Conflict):
            asyncio.run(c.compile(h, d["draft_id"], body))
    for path in ("../main.tex", "/etc/passwd", "-shell-escape.tex"):
        bad = dict(d, main_file=path, files=[{"path": path, "text": ""}])
        if path.startswith("-"):
            lc._validate(bad)  # safe because argv uses ./ prefix
        else:
            with pytest.raises(Conflict):
                lc._validate(bad)
    with pytest.raises(Conflict):
        lc._validate(dict(d, engine="sh"))
    with pytest.raises(Conflict):
        lc._validate(dict(d, files=[{"path": "main.tex", "text": r"\input{/etc/passwd}"}]))


def test_stub_success_scope_stale_and_deletion(h, monkeypatch):
    enable(monkeypatch)
    d = draft(h)
    c = lc.Compiler()
    monkeypatch.setattr(lc, "_work", lambda d, e: (b"%PDF-STUB", "", None, "ok"))
    r = asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
    assert r["status"] == "ok" and r["revision"] == 0
    assert c.pdf(h, d["draft_id"], 0) == b"%PDF-STUB"
    with pytest.raises(KeyError):
        c.pdf(h, "other", 0)
    latex.save_draft(h, d["draft_id"], {"expected_revision": 0, "files": d["files"]})
    with pytest.raises(KeyError):
        c.pdf(h, d["draft_id"], 0)

    def deleted(d, e):
        h.db.execute("DELETE FROM latex_drafts WHERE id=?", (d["draft_id"],))
        h.db.commit()
        return b"%PDF-STUB", "", None, "ok"
    monkeypatch.setattr(lc, "_work", deleted)
    with pytest.raises(KeyError):
        asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
    assert not c.artifacts


def test_stub_process_policy_timeout_and_log_bounds(h, monkeypatch):
    d = draft(h)
    monkeypatch.setenv("OPENAI_API_KEY", "MUST-NOT-INHERIT")
    def run(argv, root, env, log, timeout):
        assert "OPENAI_API_KEY" not in env
        assert "-no-shell-escape" in argv
        assert argv[-1] == "./main.tex"
        assert "(deny default)" in argv[argv.index("-p") + 1]
        log.write_bytes(b"x" * (lc.MAX_LOG + 100))
        raise subprocess.TimeoutExpired(argv, timeout)
    monkeypatch.setattr(lc, "_run", run)
    data, tail, error, status = lc._work(d, ("/trusted/bin/pdftex", "/trusted", "/trusted/bin"))
    assert data is None and "20 second" in error
    assert status == "timeout"
    assert len(tail.encode()) <= lc.MAX_LOG and "truncated" in tail


def test_portable_engine_ancestor_metadata_is_literal_only(h, monkeypatch):
    # Assert emitted sandbox permissions at the subprocess boundary; no TeX run.
    import json
    import re
    profiles = []
    def refused(argv, root, env, log, timeout):
        profiles.append(argv[argv.index("-p") + 1])
        return 1
    monkeypatch.setattr(lc, "_run", refused)
    texroot = "/opt/homebrew/opt/texlive-tinytex/TinyTeX"
    binary = texroot + "/bin/universal-darwin/pdflatex"
    result = lc._work(draft(h), (binary, texroot, texroot + "/bin/universal-darwin"))
    assert result[3] == "failed"
    blocks = re.findall(r"\(allow file-read-metadata((?: \(literal \"[^\"]*\"\))*)\)", profiles[0])
    assert len(blocks) == 1
    allowed = [json.loads(value) for value in re.findall(r'\(literal ("[^"]*")\)', blocks[0])]
    assert allowed == ["/opt/homebrew/opt/texlive-tinytex", "/opt/homebrew/opt",
                       "/opt/homebrew", "/opt", "/"]
    assert '(subpath "/opt")' not in profiles[0]
    assert '(allow file-read-metadata)' not in profiles[0]


def test_pdf_route_stub(client, h, monkeypatch):
    enable(monkeypatch)
    d = draft(h)
    monkeypatch.setattr(lc, "_work", lambda d, e: (b"%PDF-STUB", "", None, "ok"))
    response = client.post(f"/drafts/{d['draft_id']}/compile", json={"source": "manual"})
    assert response.status_code == 200
    pdf = client.get(response.json()["pdf_url"])
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["cache-control"] == "no-store"
    assert client.get(f"/drafts/{d['draft_id']}/pdf?revision=9").status_code == 404


def test_changed_revision_during_compile(h, monkeypatch):
    enable(monkeypatch)
    d = draft(h)
    c = lc.Compiler()
    def changed(saved, engine):
        latex.save_draft(h, d["draft_id"], {"expected_revision": 0, "files": d["files"]})
        return b"%PDF-STUB", "", None, "ok"
    monkeypatch.setattr(lc, "_work", changed)
    with pytest.raises(Conflict):
        asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
    assert not c.artifacts


def test_cancel_does_not_publish_or_unlock_early(h, monkeypatch):
    import threading
    enable(monkeypatch)
    d = draft(h)
    c = lc.Compiler()
    entered, release = threading.Event(), threading.Event()
    def work(d, e):
        entered.set()
        release.wait(2)
        return b"%PDF-STUB", "", None, "ok"
    monkeypatch.setattr(lc, "_work", work)
    async def scenario():
        task = asyncio.create_task(c.compile(h, d["draft_id"], {"source": "manual"}))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert c.lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not c.artifacts
    asyncio.run(scenario())


def test_real_subprocess_timeout_reaped(tmp_path, monkeypatch):
    # This is a Python process probe, not a fake claim of real TeX compilation.
    import os
    pids = []
    original = subprocess.Popen
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        pids.append(process.pid)
        return process
    monkeypatch.setattr(lc.subprocess, "Popen", spawn)
    with pytest.raises(subprocess.TimeoutExpired):
        lc._run([sys.executable, "-c", "import time; time.sleep(10)"],
                tmp_path, {"PATH": "/usr/bin:/bin"}, tmp_path / "log", 0.05)
    with pytest.raises(ProcessLookupError):
        os.killpg(pids[0], 0)


def test_rss_threshold_kills_process_group(tmp_path, monkeypatch):
    # Forced supervisor reading; no actual multi-GiB allocation or TeX invocation.
    import os
    groups = []
    def over_limit(pgid, timeout):
        groups.append(pgid)
        assert timeout > 0
        return lc.MAX_RSS + 1
    monkeypatch.setattr(lc, "_group_rss", over_limit)
    with pytest.raises(lc.MemoryLimitExceeded, match="approximate 2 GiB"):
        lc._run([sys.executable, "-c", "import time; time.sleep(10)"],
                tmp_path, {"PATH": "/usr/bin:/bin"}, tmp_path / "log", 2)
    with pytest.raises(ProcessLookupError):
        os.killpg(groups[0], 0)


def test_rss_measurement_failure_stops_compile(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError("measurement unavailable")
    monkeypatch.setattr(lc, "_group_rss", unavailable)
    with pytest.raises(OSError, match="measurement unavailable"):
        lc._run([sys.executable, "-c", "import time; time.sleep(10)"],
                tmp_path, {"PATH": "/usr/bin:/bin"}, tmp_path / "log", 2)


def test_normal_exit_retains_waitable_pid_until_group_cleanup(tmp_path, monkeypatch):
    import os
    original = os.killpg
    observed = []
    def kill(group, sig):
        info = os.waitid(os.P_PID, group, os.WEXITED | os.WNOWAIT)
        observed.append(info.si_pid)
        return original(group, sig)
    monkeypatch.setattr(lc.os, "killpg", kill)
    assert lc._run([sys.executable, "-c", "pass"], tmp_path, {},
                   tmp_path / "log", 2) == 0
    assert len(observed) == 1
    with pytest.raises(ChildProcessError):
        os.waitid(os.P_PID, observed[0], os.WEXITED | os.WNOWAIT)


@pytest.mark.parametrize(("log", "expected"), [
    ("!pdfTeX error (file tcrm1000): Font tcrm1000 at 600 not found", "Font tcrm1000"),
    ("! LaTeX Error: File `missing.sty' not found.", "Package missing.sty"),
    ("lstat(/opt) failed: /opt: Operation not permitted\nkpathsea: Can't get directory", "OS sandbox blocked"),
])
def test_observed_failure_diagnostics(log, expected):
    assert expected in lc._failure_message(log)


def test_pack_change_discards_pdf(h, monkeypatch):
    enable(monkeypatch)
    d = draft(h)
    c = lc.Compiler()
    monkeypatch.setattr(lc, "_work", lambda d, e: (b"%PDF-STUB", "", None, "ok"))
    asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
    h.db.execute("UPDATE projects SET pack_id='removed-chip' WHERE id=?", (d["project_id"],))
    h.db.commit()
    with pytest.raises(KeyError):
        c.pdf(h, d["draft_id"], 0)
    assert not c.artifacts


def test_attempt_status_and_bounded_history(h, monkeypatch):
    enable(monkeypatch)
    d = draft(h)
    c = lc.Compiler()
    for status in ("failed", "timeout"):
        monkeypatch.setattr(lc, "_work", lambda d, e: (None, "bounded log", "Stopped", status))
        result = asyncio.run(c.compile(h, d["draft_id"], {"source": "manual"}))
        row = h.db.execute("SELECT * FROM latex_compiles ORDER BY rowid DESC LIMIT 1").fetchone()
        assert result["status"] == row["status"] == status
        assert row["pdf_path"] is None and row["revision"] == 0
        assert row["log_text"] == "bounded log" and row["elapsed_ms"] >= 0


def test_remote_compile_origin_denied(client, h, monkeypatch):
    monkeypatch.setattr(lc, "discover", lambda: {})
    d = draft(h)
    result = client.post(f"/drafts/{d['draft_id']}/compile", json={"source": "manual"},
                         headers={"Origin": "https://attacker.invalid"})
    assert result.status_code == 403
    result = client.post(f"/drafts/{d['draft_id']}/compile", json={"source": "manual"},
                         headers={"Origin": "http://localhost:5173"})
    assert result.status_code == 200


def test_discovery_ignores_path_and_escape(tmp_path, monkeypatch):
    root = tmp_path / "TeX"
    bindir = root / "bin" / "test"
    bindir.mkdir(parents=True)
    engine = bindir / "pdflatex"
    engine.write_text("stub")
    engine.chmod(0o700)
    monkeypatch.setattr(lc, "TRUSTED_TEX_ROOTS", (root,))
    monkeypatch.setenv("PATH", "/attacker")
    assert lc.discover()["pdflatex"][0] == str(engine)
    outside = tmp_path / "outside"
    outside.write_text("not trusted")
    outside.chmod(0o700)
    (bindir / "xelatex").symlink_to(outside)
    assert "xelatex" not in lc.discover()


def test_caps_rediscovers_portable_root_without_path_or_restart(client, tmp_path, monkeypatch):
    # Executable marker only: verifies discovery/API behavior, not native TeX.
    root = tmp_path / "TinyTeX"
    monkeypatch.setattr(lc, "TRUSTED_TEX_ROOTS", (root,))
    monkeypatch.setattr(lc, "sandbox_available", lambda: True)
    monkeypatch.setattr(latex.shutil, "which", lambda n: None)
    before = client.get("/capabilities/latex").json()
    assert not before["available"] and not before["pdf_export"]
    binary = root / "bin" / "universal-darwin" / "pdflatex"
    binary.parent.mkdir(parents=True)
    binary.write_text("explicit test marker, not an engine")
    binary.chmod(0o700)
    after = client.get("/capabilities/latex").json()
    assert after["available"] and after["pdf_export"]
    assert after["engines"]["pdflatex"] == str(binary)


def test_xelatex_refused_without_launch(h, monkeypatch):
    d = draft(h)
    latex.save_draft(h, d["draft_id"], {"expected_revision": 0, "engine": "xelatex", "files": d["files"]})
    enable(monkeypatch)
    def no_launch(*args):
        pytest.fail("XeLaTeX must not launch")
    monkeypatch.setattr(lc, "_work", no_launch)
    result = asyncio.run(lc.Compiler().compile(h, d["draft_id"], {"source": "manual"}))
    assert result["status"] == "toolchain_absent"
    assert result["pdf_url"] is None
