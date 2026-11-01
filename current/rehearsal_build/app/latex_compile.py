"""Bounded local compilation. Requires macOS sandbox-exec, not just TeX policies.

Only researcher-requested saved revisions are compiled. No network, installers,
LuaTeX, latexmk or shell commands. The OS sandbox permits TeX distribution reads
and this job's directory, not home/research files. Real engine compatibility and
sandbox enforcement require native validation before claiming hostile-TeX safety.
Artifacts are ephemeral bytes, bounded per app instance; SQLite stays authoritative.
"""
import asyncio
from collections import OrderedDict, deque
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from . import latex, store
from .harness import Conflict

TIMEOUT = 20
MAX_PDF = 8 * 1024 * 1024
MAX_LOG = 64 * 1024
MAX_RSS = 2 * 1024 * 1024 * 1024
RSS_INTERVAL = 0.1
MAX_ARTIFACTS = 8
TTL = 600
ENGINES = ("pdflatex",)
# Owner-managed installations only. PATH discovery is informational, never executable authority.
# Portable TinyTeX root agreed with Lead/runtime owner; installation is not performed here.
# The first entry is the TeX tree that ships inside a packaged review build. It is
# derived from this file's own location, never from the environment or a request,
# so a caller cannot nominate a trusted root; discover() still rejects a root that
# is a symlink or that resolves outside itself.
BUNDLED_TEX_ROOT = Path(__file__).resolve().parent.parent / "tex" / "TinyTeX"
TRUSTED_TEX_ROOTS = (BUNDLED_TEX_ROOT,
                     Path("/opt/homebrew/opt/texlive-tinytex/TinyTeX"),
                     Path("/usr/local/texlive"))
LIMIT_EXEC = (
    "import os,resource,sys;"
    "resource.setrlimit(resource.RLIMIT_CPU,(18,18));"
    "resource.setrlimit(resource.RLIMIT_FSIZE,(16777216,16777216));"
    "resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));"
    "resource.setrlimit(resource.RLIMIT_CORE,(0,0));"
    "os.execve(sys.argv[1],sys.argv[1:],dict(os.environ))"
)


class MemoryLimitExceeded(Exception):
    pass


def _group_rss(pgid, timeout):
    """Approximate group RSS snapshot in bytes, not a kernel hard memory cap.

    macOS rejects/unreliably implements RLIMIT_AS/DATA. The parent samples the
    engine's own process group every 100 ms and kills it above 2 GiB. Allocation
    bursts between samples can exceed that threshold. Measurement failures fail
    closed; no unsupervised compile is allowed.
    """
    try:
        sample = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-g", str(pgid)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError("RSS measurement timed out; compilation stopped") from exc
    if sample.returncode:
        raise OSError("RSS measurement failed; compilation stopped")
    try:
        values = [int(row) for row in sample.stdout.splitlines()]
        if not values or any(value < 0 for value in values):
            raise ValueError("invalid RSS")
        return sum(values) * 1024
    except ValueError as exc:
        raise OSError("RSS measurement was invalid; compilation stopped") from exc


def discover():
    found = {}
    for root in TRUSTED_TEX_ROOTS:
        if not root.is_dir() or root.is_symlink():
            continue
        root = root.resolve()
        for name in ENGINES:
            for candidate in sorted(root.glob("**/bin/*/" + name)):
                resolved = candidate.resolve()
                if resolved.is_relative_to(root) and resolved.is_file() and os.access(resolved, os.X_OK):
                    found.setdefault(name, (str(candidate), str(root), str(candidate.parent)))
    return found


def sandbox_available():
    return sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


def _snapshot(h, did):
    d = latex.read_draft(h, did)
    project = h.project(d["project_id"])
    h.require_chip_scope(d["project_id"])
    if d["question_missing"]:
        raise Conflict("The linked question was deleted")
    # Bind the complete project record: a chip/pack move invalidates publication.
    return d, dict(project)


def _validate(d):
    if d["engine"] not in latex.ENGINES:
        raise Conflict("Invalid compile engine")
    paths = [latex.sanitize_rel_path(f["path"]) for f in d["files"]]
    if len(paths) > 10 or len(set(paths)) != len(paths):
        raise Conflict("Invalid draft files")
    if d["main_file"] not in paths:
        raise Conflict("Main document is missing")
    latex.sanitize_rel_path(d["main_file"])
    if not d["main_file"].endswith((".tex", ".latex")):
        raise Conflict("Main document must be TeX")
    if any(len(f["text"].encode()) > latex.MAX_IMPORT_BYTES for f in d["files"]):
        raise Conflict("Draft file exceeds 512 KiB")
    for f in d["files"]:
        for match in re.finditer(
                r"\\(?:input|include|includegraphics|bibliography|addbibresource)"
                r"(?:\[[^\]]*\])?\s*\{([^{}]*)\}", f["text"]):
            for reference in match[1].split(","):
                if reference.strip().startswith(("/", "~")) or ".." in reference.split("/") or "\\" in reference:
                    raise Conflict("This draft points outside its own folder; external references are refused")


def _run(argv, cwd, env, log, timeout=TIMEOUT):
    """A bounded file sink avoids communicate() accumulating unlimited output."""
    with log.open("ab") as output:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, shell=False)
        try:
            deadline = time.monotonic() + timeout
            while os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    rss = _group_rss(p.pid, min(1, remaining))
                except OSError:
                    if os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                        break  # ps can race a normal process exit.
                    raise
                if rss > MAX_RSS:
                    raise MemoryLimitExceeded("Process-group RSS exceeded the approximate 2 GiB memory threshold")
                time.sleep(min(RSS_INTERVAL, max(0.001, deadline - time.monotonic())))
        finally:
            # WNOWAIT pins the leader PID/group until after this signal. Never
            # call poll()/wait() first: those reap and permit PID/group reuse.
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin returns EPERM when the group contains only zombies.
                # Accept that case only after observing every member's state;
                # other permission failures remain failures, never silent leaks.
                states = subprocess.run(
                    ["/bin/ps", "-o", "stat=", "-g", str(p.pid)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, shell=False)
                rows = states.stdout.split()
                if states.returncode or not rows or any(not row.startswith(b"Z") for row in rows):
                    raise
            finally:
                code = p.wait(timeout=5)
        return code


def _failure_message(log):
    font = re.search(r"\bFont\s+([^\s]+)\s+at\s+\d+\s+not found", log)
    if font:
        return (f"Font {font[1]} is not available in the local TeX installation. "
                "Automatic font generation and installation are disabled. "
                "The bundled lmodern package is an option for documents you choose to edit; source was not changed.")
    package = re.search(r"File\s+[`'\"]([^`'\"\n]+\.sty)['`\"]\s+not found", log)
    if package:
        return f"Package {package[1]} is not installed locally. Compilation does not install packages automatically."
    if "Operation not permitted" in log and ("kpathsea" in log or "lstat(" in log):
        return ("The OS sandbox blocked TeX executable or installation-path traversal. "
                "The local sandbox policy needs review; do not disable its guards.")
    return "TeX failed; see the bounded log. Missing packages are not installed automatically."


def _work(d, tool):
    engine, texroot, bindir = tool
    with tempfile.TemporaryDirectory(prefix="wb-latex-") as name:
        root = Path(name).resolve()
        sources = root / "sources"
        output = root / "output"
        sources.mkdir()
        output.mkdir()
        for f in d["files"]:
            target = sources / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["text"], encoding="utf-8")
        # JSON string escaping also escapes the sandbox profile path literals.
        quote = lambda p: json.dumps(str(p), ensure_ascii=False)
        # kpathsea lstat()s each executable ancestor. Grant metadata only,
        # not directory contents/data, outside the selected trusted TeX root.
        ancestors = "".join(
            f" (literal {quote(parent)})"
            for parent in Path(texroot).parents)
        profile = (
            '(version 1)(deny default)(allow process-fork)(allow sysctl-read)'
            '(allow file-read* (literal "/") (subpath "/System") (subpath "/usr/lib")'
            f' (subpath {quote(texroot)})'
            f' (subpath {quote(root)}) (literal "/dev/null") (literal "/dev/urandom"))'
            f'(allow file-read-metadata{ancestors})'
            f'(allow file-write* (subpath {quote(root)}) (literal "/dev/null"))'
            f'(allow process-exec (literal {quote(Path(engine).resolve())}))'
        )
        env = {"PATH": bindir, "HOME": str(root), "TMPDIR": str(root),
               "LANG": "C.UTF-8", "openin_any": "p", "openout_any": "p",
               "shell_escape": "f", "MKTEXPK": "0", "MKTEXTFM": "0",
               "MKTEXFMT": "0", "TEXMFVAR": str(root / "texmf-var"),
               "TEXMFCONFIG": str(root / "texmf-config"), "TEXMFHOME": str(root / "texmf-home"),
               # The wrapper interpreter is the packaged runtime; bytecode it wrote
               # would land inside the app bundle with this machine's paths.
               "PYTHONDONTWRITEBYTECODE": "1"}
        log = root / "compile-output.txt"
        argv = [sys.executable, "-c", LIMIT_EXEC, "/usr/bin/sandbox-exec", "-p", profile,
                engine, "-no-shell-escape", "-interaction=nonstopmode",
                "-halt-on-error", "-file-line-error", "-jobname=compiled",
                "-output-directory=" + str(output),
                "./" + d["main_file"]]
        try:
            code = 0
            status = "ok"
            deadline = time.monotonic() + TIMEOUT
            for _ in range(2):  # explicit reference pass, no automatic retry
                code = _run(argv, sources, env, log, max(0.01, deadline - time.monotonic()))
                if code:
                    break
            message = None if code == 0 else "TeX failed; see the bounded log. Missing packages are not installed automatically."
            if message:
                status = "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
            message = "Compilation exceeded the 20 second limit."
        except MemoryLimitExceeded as exc:
            status = "failed"
            message = str(exc)
        except OSError as exc:
            status = "failed"
            message = "Local compiler or resource supervisor failed: " + str(exc)
        tail = ""
        if log.exists():
            with log.open("rb") as stream:
                size = log.stat().st_size
                stream.seek(max(0, size - MAX_LOG + 40))
                tail = stream.read(MAX_LOG - 40).decode("utf-8", errors="ignore")
                if size > MAX_LOG - 40:
                    tail = "[Earlier compiler output truncated]\n" + tail
        if status == "failed" and message and message.startswith("TeX failed;"):
            message = _failure_message(tail)
        pdf = output / "compiled.pdf"
        data = None
        if message is None:
            if pdf.is_symlink() or not pdf.is_file() or not 0 < pdf.stat().st_size <= MAX_PDF:
                message = "Compiler produced no PDF or exceeded the 8 MiB PDF limit."
            else:
                with pdf.open("rb") as stream:
                    data = stream.read(MAX_PDF + 1)
                if len(data) > MAX_PDF or not data.startswith(b"%PDF-"):
                    data, message = None, "Compiler output is not a PDF."
        if message and status == "ok":
            status = "failed"
        return data, tail, message, status


class Compiler:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.artifacts = OrderedDict()
        self.requests = OrderedDict()

    def _prune(self, h):
        for did, (stamp, snapshot, _) in list(self.artifacts.items()):
            try:
                valid = time.monotonic() - stamp < TTL and _snapshot(h, did) == snapshot
            except (KeyError, Conflict):
                valid = False
            if not valid:
                del self.artifacts[did]

    async def compile(self, h, did, body):
        if body != {"source": "manual"}:
            raise Conflict("Compile requires exactly {source: 'manual'} and uses saved text only")
        snapshot = _snapshot(h, did)
        d = snapshot[0]
        _validate(d)
        self._prune(h)
        result = {"status": "error", "revision": d["revision"], "diagnostics": [],
                  "log_tail": "", "pdf_url": None, "citations_resolved": False}
        selected = ENGINES if d["engine"] == "auto" else (d["engine"],)
        tools = discover()
        tool = next((tools[n] for n in selected if n in tools), None)
        if not tool:
            result.update(status="toolchain_absent", diagnostics=[{
                "severity": "error", "line": None,
                "message": "No enabled trusted pdfLaTeX installation for this draft. XeLaTeX and LuaLaTeX are disabled; PATH alone is not trusted."}])
            return result
        if not sandbox_available():
            result["diagnostics"] = [{"severity": "error", "line": None,
                                      "message": "Compilation refused: required macOS OS sandbox is unavailable."}]
            return result
        now = time.monotonic()
        history = self.requests.setdefault(did, deque())
        while history and now - history[0] > 60:
            history.popleft()
        if len(history) >= 10 or self.lock.locked():
            raise Conflict("Compiler busy or draft compile rate limit reached")
        history.append(now)
        self.requests.move_to_end(did)
        while len(self.requests) > 100:
            self.requests.popitem(last=False)
        async with self.lock:
            # Shield the worker so cancellation cannot release the concurrency guard early.
            started = time.monotonic()
            job = asyncio.create_task(asyncio.to_thread(_work, d, tool))
            try:
                data, tail, message, status = await asyncio.shield(job)
            except asyncio.CancelledError:
                await job
                raise
            if _snapshot(h, did) != snapshot:
                self.artifacts.pop(did, None)
                raise Conflict("Draft or project changed during compilation; result discarded")
            result["log_tail"] = tail
            result["status"] = status
            if message:
                result["diagnostics"] = [{"severity": "error", "message": message, "line": None}]
                for match in list(re.finditer(r"(?:^|\n)([^:\n]+\.tex):(\d+):\s*([^\n]+)", tail))[:32]:
                    result["diagnostics"].append({"severity": "error", "file": match[1],
                                                   "line": int(match[2]), "message": match[3]})
            else:
                result.update(status="ok",
                          pdf_url=f"/drafts/{did}/pdf?revision={d['revision']}")
                result["diagnostics"].append({"severity": "warning", "line": None,
                    "message": "Citation resolution is not verified; no bibliography processor was run."})
            # No await after the identity barrier above: deletion cannot interleave publication.
            h.db.execute(
                "INSERT INTO latex_compiles(id,draft_id,project_id,status,engine,revision,"
                "pdf_path,pdf_bytes,log_text,diagnostics_json,elapsed_ms,citations_resolved,created_at)"
                " VALUES(?,?,?,?,?,?,NULL,?,?,?,?,0,?)",
                (store.new_id("compile"), did, d["project_id"], result["status"], tool[0],
                 d["revision"], len(data) if data else None, tail,
                 json.dumps(result["diagnostics"]), int((time.monotonic()-started)*1000), store.now()))
            h.db.execute("DELETE FROM latex_compiles WHERE draft_id=? AND id NOT IN"
                         " (SELECT id FROM latex_compiles WHERE draft_id=? ORDER BY rowid DESC LIMIT 10)",
                         (did, did))
            h.db.commit()
            if data is not None:
                self.artifacts[did] = (time.monotonic(), snapshot, data)
                self.artifacts.move_to_end(did)
                while len(self.artifacts) > MAX_ARTIFACTS:
                    self.artifacts.popitem(last=False)
            return result

    def pdf(self, h, did, revision):
        self._prune(h)
        artifact = self.artifacts.get(did)
        if artifact is None or artifact[1][0]["revision"] != revision:
            raise KeyError("No current compiled PDF; compile the saved revision again")
        return artifact[2]
