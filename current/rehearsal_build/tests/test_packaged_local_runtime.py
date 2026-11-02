"""Packaged-app local runtime: config path, optional launch, cleanup, reaper.

What the packaged build must do, and what these tests pin
--------------------------------------------------------
The packaged backend is the only Python process a review .app starts, and it
moves ``HOME`` into a per-session directory before anything else imports. The
real user's local-runtime configuration therefore has to be resolved *before*
that move and pinned through ``WB_LOCAL_CONFIG``: otherwise the packaged app can
never use a model the person already installed, while the source launcher can.

So these tests cover, in the order the requirements list them:

* the config path is resolved from the real home (or the ``WB_LOCAL_CONFIG``
  override) before ``HOME`` is overridden, and stays reachable afterwards;
* no configuration: the backend becomes ready with local mode unavailable and
  starts no model;
* an invalid configuration: the backend still becomes ready, local mode is
  unavailable, and the reason is short and non-secret;
* a successful launch: the verified binding is published into the backend's own
  environment, the ready file and session identity say local mode is running, and
  the per-run bearer appears in none of the ready file, the session file, the
  console or the log;
* a failed launch and an interrupt during start: the app still starts (failure)
  or exits cleanly (interrupt), and the owned child is stopped either way;
* normal quit via SIGTERM, SIGINT and the parent-pid watchdog: the owned model is
  stopped on every one of those exit paths;
* the backend-death reaper: a ``SIGKILL`` of the backend leaves a watchdog that
  stops the model, and the watchdog never targets anything but the pid it was
  given.

No real model is involved and nothing here downloads, installs or reads the
weights: the backend is run as a real subprocess with a stub ``app`` package (so
the ordering, spawning and cleanup code is the real file, unmodified), and the
launcher's ``LocalServer`` is a recorder. The reaper is exercised through its own
functions with a recorded ``os.kill``; the one place a real child process appears
is the reaper process the launcher is asked to arm, and that is a stub too.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "packaging" / "backend_server.py"
sys.path.insert(0, str(ROOT))

from app.ai import local_config, local_launcher, local_reaper, local_runtime  # noqa: E402

SECRET = "packaged-run-secret-never-in-a-file"
ALIAS = "qwen3-4b-instruct-2507-q4_K_M"
BASE_URL = "http://127.0.0.1:9931"
CONTEXT = 8192

#: An inherited machine credential. Discovered from app/ai/secrets.py: every
#: provider reader prefers its environment override over the owner-only key file.
MACHINE_KEY = "machine-owner-key-must-never-be-inherited"
MACHINE_KEY_ENV = ("META_API_KEY", "MODEL_API_KEY", "BASETEN_API_KEY",
                   "OPENAI_API_KEY", "WB_MODAL_SHARED_API_KEY")

#: The stub ``app`` package the packaged backend is run against. It records what
#: the real supervisor did (which launcher calls, in which order) and nothing
#: else: no model, no weights, no runtime executable.
STUB_API = '''
import json, os, subprocess, sys
ORIGINS = []
RECORD = os.environ.get("WB_TEST_RECORD")

def _note(entry):
    if not RECORD:
        return
    with open(RECORD, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")

def create_app(db_path=None, data_dir=None):
    _note({"event": "create_app", "db_path": db_path,
           "home": os.environ.get("HOME"),
           "local_config": os.environ.get("WB_LOCAL_CONFIG")})
    # A child of the API process, spawned exactly as app/library.py (pdftotext) or
    # app/apple_ai.py (xcrun/swiftc) would: inherited environment, no env= given.
    # What it reports is what any later helper would be able to read.
    if os.environ.get("WB_TEST_SPAWN_CHILD"):
        names = ("WB_LOCAL_API_KEY", "WB_LOCAL_BASE_URL", "META_API_KEY", "MODEL_API_KEY",
                 "BASETEN_API_KEY", "OPENAI_API_KEY", "WB_MODAL_SHARED_API_KEY")
        child = subprocess.run(
            [sys.executable, "-c",
             "import json, os; print(json.dumps({n: os.environ.get(n) for n in %r}))" % (names,)],
            capture_output=True, text=True)
        _note({"event": "child_environment", "seen": json.loads(child.stdout)})
    return object()
'''

STUB_FAKE_META = 'app = object()\n'

STUB_UVICORN = '''
import threading, time

class Config:
    def __init__(self, app, host=None, port=None, log_level=None, access_log=None):
        self.app = app
        self.host = host
        self.port = port
        self.log_level = log_level
        self.access_log = access_log

class Server:
    def __init__(self, config):
        self.config = config
        self.started = False
        self.should_exit = False
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        self.started = True
        while not self.should_exit:
            time.sleep(0.02)

    def run(self):
        self.thread.start()
'''

#: Stub configuration reader. ``WB_TEST_CONFIG`` selects the behaviour:
#: "absent", "invalid" or "present".
STUB_LOCAL_CONFIG = '''
import json, os

def _note(entry):
    path = os.environ.get("WB_TEST_RECORD")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")

def read():
    _note({"event": "config_read", "home": os.environ.get("HOME"),
           "config_path": os.environ.get("WB_LOCAL_CONFIG")})
    mode = os.environ.get("WB_TEST_CONFIG", "absent")
    if mode == "absent":
        return {}
    if mode == "invalid":
        raise ValueError("Local runtime config must be a regular owner-only file (mode 600)")
    if mode == "unreadable":
        raise PermissionError(13, "Permission denied")
    return {"runtime_executable": "/nonexistent/llama-server",
            "model_weights": "/nonexistent/weights.gguf",
            "alias": %r, "context": %d, "port": 9931,
            "base_url": %r}
''' % (ALIAS, CONTEXT, BASE_URL)

#: Stub launcher. ``WB_TEST_LAUNCH`` selects what start() does. Every call is
#: recorded, so the ordering and the cleanup can be asserted.
STUB_LOCAL_LAUNCHER = '''
import json, os

SECRET = %r
ALIAS = %r
BASE_URL = %r
CONTEXT = %d

def _note(entry):
    path = os.environ.get("WB_TEST_RECORD")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")

class LocalLaunchError(RuntimeError):
    pass

class _Process:
    pid = 4242
    def poll(self):
        return None

class LocalServer:
    def __init__(self, config, log_path=None, app_origin=None,
                 startup_timeout=None, watch_parent=True):
        self.config = config
        self.log_path = log_path
        self.app_origin = app_origin
        self.startup_timeout = startup_timeout
        self.watch_parent = watch_parent
        self.process = None
        self.secret = None
        self.run_id = None
        self.weights_sha256 = None
        self.template_overhead_tokens = None
        self.observed_identity = None
        _note({"event": "constructed", "app_origin": app_origin,
               "log_path": log_path, "startup_timeout": startup_timeout,
               "watch_parent": watch_parent,
               "home": os.environ.get("HOME")})

    def start(self):
        _note({"event": "start"})
        mode = os.environ.get("WB_TEST_LAUNCH", "ok")
        if mode == "fail":
            raise LocalLaunchError("Local runtime did not become ready within 60 seconds.")
        if mode == "interrupt":
            raise KeyboardInterrupt
        self.process = _Process()
        self.secret = SECRET
        self.run_id = "stub-run-id"
        self.weights_sha256 = "c" * 64
        self.template_overhead_tokens = 42
        self.observed_identity = {"model_alias": ALIAS, "n_ctx": CONTEXT}

    def stop(self, *, grace=None, kill_wait=None):
        _note({"event": "stop", "grace": grace, "kill_wait": kill_wait})
        self.secret = None

def binding_environment(server):
    if server is None or server.secret is None:
        return {}
    return {"WB_LOCAL_BASE_URL": BASE_URL, "WB_LOCAL_MODEL": ALIAS,
            "WB_LOCAL_CONTEXT": str(CONTEXT), "WB_LOCAL_RUN_ID": server.run_id,
            "WB_LOCAL_SERVER_PID": "4242",
            "WB_LOCAL_WEIGHTS_SHA256": server.weights_sha256,
            "WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS": str(server.template_overhead_tokens),
            "WB_LOCAL_OBSERVED_ALIAS": ALIAS,
            "WB_LOCAL_OBSERVED_CTX": str(CONTEXT),
            "WB_LOCAL_OBSERVED_CTX_TRAIN": "262144",
            "WB_LOCAL_API_KEY": server.secret}

#: The failure-path budget the packaged backend is expected to ask for. Recorded
#: on the stop event so a test can assert the short grace was used.
FAILURE_STOP_GRACE_SECONDS = 3.0
FAILURE_KILL_WAIT_SECONDS = 2.0

def stop_preserving(server, **kwargs):
    if server is None:
        return None
    try:
        server.stop(**kwargs)
    except BaseException as exc:
        _note({"event": "stop_error", "message": str(exc)})
        return str(exc)
    return None
''' % (SECRET, ALIAS, BASE_URL, CONTEXT)

#: Stub registry/transport view of the binding. ``WB_TEST_BINDING`` selects
#: whether the published binding reads back as usable. It mirrors the real
#: module's contract: the in-process store first, then the environment, and the
#: environment alone must NOT be enough when the store is the carrier.
STUB_LOCAL_RUNTIME = '''
import os

_BINDING = {}

def publish_binding(binding=None):
    global _BINDING
    _BINDING = dict(binding or {})
    _note({"event": "published", "names": sorted(_BINDING),
           "in_environment": sorted(n for n in _BINDING if n in os.environ)})
    return dict(_BINDING)

def published_binding():
    return dict(_BINDING)

def read_config():
    mode = os.environ.get("WB_TEST_BINDING", "ok")
    bearer = (_BINDING.get("WB_LOCAL_API_KEY")
              or os.environ.get("WB_LOCAL_API_KEY"))
    if mode == "ok" and bearer:
        return {"configured": True}
    return {"configured": False}
'''

#: Prepended to the stub local_runtime above: it needs the same recorder hook.
STUB_LOCAL_RUNTIME_PRELUDE = '''
import json

def _note(entry):
    path = os.environ.get("WB_TEST_RECORD")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")
'''

FRONTEND_INDEX = "<!doctype html><title>Research Workbench</title>\n"


def _write_stub_tree(tmp_path, *, config_mode, launch_mode, binding_mode="ok"):
    """A throwaway copy of the packaged layout with a stubbed application."""
    packaging = tmp_path / "packaging"
    packaging.mkdir(parents=True, exist_ok=True)
    shutil_copy(BACKEND, packaging / "backend_server.py")
    ai = tmp_path / "app" / "ai"
    ai.mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (ai / "__init__.py").write_text("")
    (ai / "local_config.py").write_text(STUB_LOCAL_CONFIG)
    (ai / "local_launcher.py").write_text(STUB_LOCAL_LAUNCHER)
    (ai / "local_runtime.py").write_text(STUB_LOCAL_RUNTIME_PRELUDE + STUB_LOCAL_RUNTIME)
    (tmp_path / "app" / "api.py").write_text(STUB_API)
    (tmp_path / "app" / "fake_meta.py").write_text(STUB_FAKE_META)
    (tmp_path / "uvicorn.py").write_text(STUB_UVICORN)
    fixture = tmp_path / "data" / "fx"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "permits_2023.csv").write_text("id,value\n1,2\n")
    frontend = tmp_path / "frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text(FRONTEND_INDEX)
    return packaging / "backend_server.py"


def shutil_copy(source, target):
    target.write_bytes(Path(source).read_bytes())


def _records(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(path):
    return [entry["event"] for entry in _records(path)]


class _Run:
    """One backend subprocess and everything it wrote."""

    def __init__(self, process, ready_path, state, record, real_home):
        self.process = process
        self.ready_path = ready_path
        self.state = state
        self.record = record
        self.real_home = real_home
        self.stdout = ""
        self.stderr = ""
        self.ready = None
        self.session = None

    def wait_ready(self, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return False
            if self.ready_path.is_file():
                self.ready = json.loads(self.ready_path.read_text())
                return True
            time.sleep(0.05)
        return False

    def finish(self, sig=signal.SIGTERM, timeout=30):
        if self.process.poll() is None:
            try:
                self.process.send_signal(sig)
            except ProcessLookupError:
                pass
        try:
            self.stdout, self.stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.stdout, self.stderr = self.process.communicate(timeout=timeout)
            raise
        session_path = self.state / "REVIEW_SESSION.json"
        if session_path.is_file():
            self.session = json.loads(session_path.read_text())
        self.ready_text = self.ready_path.read_text() if self.ready_path.is_file() else ""
        return self.process.returncode

    def wait_for_exit(self, timeout=30):
        """Let the backend end on its own (the parent-pid watchdog path)."""
        try:
            self.stdout, self.stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.stdout, self.stderr = self.process.communicate(timeout=timeout)
            raise
        session_path = self.state / "REVIEW_SESSION.json"
        if session_path.is_file():
            self.session = json.loads(session_path.read_text())
        return self.process.returncode


@pytest.fixture
def packaged(tmp_path):
    """Start the packaged backend as a real subprocess, with a stub app."""
    running = []

    def start(*, config_mode="absent", launch_mode="ok", binding_mode="ok",
              parent_pid=0, extra_argv=(), spawn_child=False, machine_keys=True):
        source = _write_stub_tree(tmp_path, config_mode=config_mode,
                                  launch_mode=launch_mode, binding_mode=binding_mode)
        # The real user's home this test pretends to be: the launcher config is
        # resolved from it, so the file must exist there and nowhere else.
        real_home = tmp_path / "real-home"
        (real_home / "Library" / "Application Support" / "ResearchWorkbench").mkdir(
            parents=True, exist_ok=True)
        state = tmp_path / "state"
        ready_path = tmp_path / "ready.json"
        record = tmp_path / "record.jsonl"
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith("WB_LOCAL_") and k not in {"HOME", "WB_TEST_RECORD"}}
        environment.update({
            "HOME": str(real_home),
            "WB_TEST_RECORD": str(record),
            "WB_TEST_CONFIG": config_mode,
            "WB_TEST_LAUNCH": launch_mode,
            "WB_TEST_BINDING": binding_mode,
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(tmp_path),
        })
        if spawn_child:
            environment["WB_TEST_SPAWN_CHILD"] = "1"
        if machine_keys:
            # Exactly what an owner's shell might export on the build machine.
            # The packaged backend must strip every one of them.
            environment.update({name: MACHINE_KEY for name in MACHINE_KEY_ENV})
            environment["WB_LOCAL_API_KEY"] = MACHINE_KEY
            environment["WB_LOCAL_BASE_URL"] = "http://127.0.0.1:9931"
        process = subprocess.Popen(
            [sys.executable, str(source), "--state-dir", str(state),
             "--ready-file", str(ready_path), "--parent-pid", str(parent_pid),
             *extra_argv],
            cwd=str(tmp_path / "packaging"), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        run = _Run(process, ready_path, state, record, real_home)
        running.append(run)
        return run

    yield start
    for run in running:
        if run.process.poll() is None:
            run.process.kill()
            try:
                run.process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                pass


# --- 1. the config path, resolved before HOME moves ------------------------

@pytest.fixture
def packaged_module():
    """The real ``packaging/backend_server.py`` loaded as a module, not run."""
    spec = importlib.util.spec_from_file_location("wb_packaged_backend_under_test",
                                                  BACKEND)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_path_uses_the_wb_local_config_override(packaged_module, tmp_path):
    environ = {"WB_LOCAL_CONFIG": str(tmp_path / "pinned.json"), "HOME": str(tmp_path)}
    path = packaged_module._resolve_local_config_path(environ)
    assert path == tmp_path / "pinned.json"
    assert environ["WB_LOCAL_CONFIG"] == str(tmp_path / "pinned.json")


def test_config_path_defaults_to_the_real_home_and_is_pinned(packaged_module, tmp_path,
                                                             monkeypatch):
    """Real home before the session HOME: the default is the user's own file."""
    real_home = tmp_path / "real-home"
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.delenv("WB_LOCAL_CONFIG", raising=False)
    environ = {"HOME": str(real_home)}
    path = packaged_module._resolve_local_config_path(environ)
    assert path == real_home / "Library/Application Support/ResearchWorkbench/local_runtime.json"
    assert environ["WB_LOCAL_CONFIG"] == str(path)


def test_the_pin_survives_the_session_home_override(packaged_module, tmp_path, monkeypatch):
    """After HOME moves, the pinned path still names the real user's config.

    This is the defect this test pins: ``local_config.read()`` uses
    ``Path.home()``, so without the pin the packaged backend would look for the
    configuration inside its own throwaway session and never find the model the
    person installed.
    """
    real_home = tmp_path / "real-home"
    session_home = tmp_path / "state" / "home"
    session_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.delenv("WB_LOCAL_CONFIG", raising=False)
    pinned = packaged_module._resolve_local_config_path()
    assert pinned.parent.parent.parent.parent == real_home
    # Now the session HOME override the packaged backend performs next.
    monkeypatch.setenv("HOME", str(session_home))
    assert local_config.config_path() == pinned
    # A configuration at that path is what the packaged app would read.
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(json.dumps({"runtime_executable": "/opt/example/llama-server",
                                  "model_weights": "/opt/example/w.gguf",
                                  "alias": ALIAS, "context": CONTEXT, "port": 9931}))
    pinned.chmod(0o600)
    config = local_config.read()
    assert config["alias"] == ALIAS
    assert config["base_url"] == BASE_URL
    # And the same read inside the session HOME would have found nothing.
    assert not (session_home / "Library/Application Support/ResearchWorkbench/"
                "local_runtime.json").exists()


def test_main_resolves_the_config_before_overriding_home():
    """Order, not intent: the resolution must run before HOME moves.

    ``main`` is one long function whose first side effect is the HOME override,
    so the ordering is asserted on the shipped source: the resolution call must
    appear before the ``os.environ["HOME"] = ...`` assignment.
    """
    source = BACKEND.read_text()
    resolve_at = source.index("_resolve_local_config_path()")
    home_at = source.index('os.environ["HOME"] = str(home)')
    assert resolve_at < home_at, "the real-home config path must be resolved before HOME moves"
    # And the assignment really is the session home, not the inherited one.
    assert 'home = state / "home"' in source


def test_the_session_home_and_pinned_config_are_both_in_force(packaged):
    """One real run: session HOME for credentials, real-home config path for local."""
    run = packaged(config_mode="absent")
    assert run.wait_ready(), run.process.stderr.read() if run.process.stderr else ""
    run.finish()
    reads = [entry for entry in _records(run.record) if entry["event"] == "config_read"]
    assert reads, "the backend must consult the local-runtime configuration"
    first = reads[0]
    # The private session HOME is still what every credential reader sees.
    assert first["home"] == str(run.state / "home")
    # And the config path is the real user's file, resolved before that override.
    assert first["config_path"] == str(
        run.real_home / "Library/Application Support/ResearchWorkbench/local_runtime.json")
    created = [entry for entry in _records(run.record) if entry["event"] == "create_app"]
    assert created and created[0]["home"] == str(run.state / "home")


def test_a_second_backend_cannot_open_a_workspace_another_backend_holds(packaged):
    """A backend orphaned by a force-killed shell must not share the workspace."""
    first = packaged(config_mode="absent")
    assert first.wait_ready()
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    second = packaged(config_mode="absent", parent_pid=gone.pid)
    assert second.wait_for_exit(timeout=30) == 0
    assert _events(first.record).count("create_app") == 1
    assert json.loads(first.ready_path.read_text())["identity"]["pid"] == first.ready["identity"]["pid"]
    assert first.finish() == 0, first.stderr


# --- 2. no configuration: the app starts exactly as today ------------------

def test_no_configuration_starts_no_model_and_says_local_is_unavailable(packaged):
    run = packaged(config_mode="absent")
    assert run.wait_ready(), "the app must become ready with no local model configured"
    assert _events(run.record).count("start") == 0, "nothing may be launched without a config"
    assert "constructed" not in _events(run.record)
    local = run.ready["identity"]["local_runtime"]
    assert local["available"] is False
    assert local["status"] == "local_model_not_configured"
    assert local["model_bundled"] is False
    assert local["cloud"] is False
    assert run.ready["identity"]["live_ai"] is False
    assert run.ready["identity"]["cloud_ai"] is False
    code = run.finish()
    assert code == 0, run.stderr
    assert "local model unavailable" in run.stdout


# --- 3. invalid configuration: the app still starts -----------------------

def test_an_invalid_configuration_still_becomes_ready(packaged):
    run = packaged(config_mode="invalid")
    assert run.wait_ready(), "a broken local config must not stop the app"
    assert "start" not in _events(run.record), "an invalid config must start nothing"
    local = run.ready["identity"]["local_runtime"]
    assert local["available"] is False
    assert local["status"] == "local_model_config_invalid"
    assert "owner-only" in local["reason"]
    # The reason is short and carries no credential.
    assert SECRET not in json.dumps(local)
    assert run.finish() == 0, run.stderr
    assert "local model unavailable" in run.stdout
    assert run.session["local_runtime"]["status"] == "local_model_config_invalid"


def test_a_configuration_that_cannot_be_read_still_becomes_ready(packaged):
    """An unreadable path is not a valid config either, and must not crash."""
    run = packaged(config_mode="unreadable")
    assert run.wait_ready(), "an unreadable local config must not stop the app"
    assert "start" not in _events(run.record)
    local = run.ready["identity"]["local_runtime"]
    assert local["available"] is False
    assert local["status"] == "local_model_config_invalid"
    assert "could not be read" in local["reason"]
    assert SECRET not in json.dumps(local)
    assert run.finish() == 0, run.stderr


# --- 4. a launch publishes the binding, and only that ---------------------

def test_a_verified_launch_publishes_the_binding_without_leaking_the_secret(packaged):
    run = packaged(config_mode="present", launch_mode="ok")
    assert run.wait_ready(), run.stderr
    events = _events(run.record)
    # The model is started and published before the API is created, so no
    # request can race a runtime that is still loading.
    assert events.index("start") < events.index("published") < events.index("create_app")
    published = [entry for entry in _records(run.record) if entry["event"] == "published"][0]
    assert "WB_LOCAL_API_KEY" in published["names"]
    constructed = [entry for entry in _records(run.record)
                   if entry["event"] == "constructed"][0]
    # The app origin is this session's frontend, and the log lives in the state dir.
    assert constructed["app_origin"].startswith("http://127.0.0.1:")
    assert Path(constructed["log_path"]).parent == run.state
    assert constructed["watch_parent"] is True
    # The launch budget fits inside the desktop shell's 90 s ready timeout.
    assert 0 < constructed["startup_timeout"] <= 90

    local = run.ready["identity"]["local_runtime"]
    assert local["available"] is True
    assert local["status"] == "local_model_running"
    assert local["model"] == ALIAS
    assert local["context"] == CONTEXT
    assert local["base_url"] == BASE_URL
    assert local["template_overhead_tokens"] == 42
    assert local["weights_sha256"] == "c" * 64
    # Truthful about the rest: local compute, not cloud, not a bundled model.
    assert local["cloud"] is False
    assert local["model_bundled"] is False
    assert run.ready["identity"]["live_ai"] is False
    assert run.ready["identity"]["cloud_ai"] is False

    code = run.finish()
    assert code == 0, run.stderr
    # The bearer is in the backend's memory and its own environment only.
    for surface in (run.ready_text,
                    (run.state / "REVIEW_SESSION.json").read_text(),
                    run.stdout, run.stderr):
        assert SECRET not in surface, "the per-run bearer must not reach a session surface"
    assert "local model: %s" % BASE_URL in run.stdout


def test_the_bearer_never_reaches_a_session_file_even_on_a_failed_launch(packaged):
    run = packaged(config_mode="present", launch_mode="fail")
    assert run.wait_ready(), "a failed local launch must not stop the app"
    local = run.ready["identity"]["local_runtime"]
    assert local["available"] is False
    assert local["status"] == "local_model_launch_failed"
    assert "did not become ready" in local["reason"]
    assert SECRET not in run.ready_path.read_text()
    assert run.finish() == 0, run.stderr


# --- F5: the bearer stays in-process; no child inherits a credential ------

def test_a_child_spawned_after_launch_inherits_neither_the_bearer_nor_a_key(packaged):
    """``pdftotext``/``xcrun`` are spawned with the inherited environment.

    So the packaged backend must publish the binding in-process only: an
    ``WB_LOCAL_API_KEY`` in its own ``os.environ`` would be readable by every
    later child, which is exactly the credential-isolation claim that has to be
    true for a review session.
    """
    run = packaged(config_mode="present", launch_mode="ok", spawn_child=True)
    assert run.wait_ready(), run.stderr
    assert run.ready["identity"]["local_runtime"]["available"] is True
    assert run.finish() == 0, run.stderr
    child = [entry for entry in _records(run.record) if entry["event"] == "child_environment"]
    assert child, "the child spawned after the launch must be observed"
    seen = child[0]["seen"]
    # The per-run bearer, and the stale local binding the launcher must not inherit.
    assert seen["WB_LOCAL_API_KEY"] is None, "a child could read the model bearer"
    assert seen["WB_LOCAL_BASE_URL"] is None, "a child could read a stale binding"
    # And no machine credential survived the strip.
    for name in MACHINE_KEY_ENV:
        assert seen[name] is None, "a child inherited %s from the machine" % name
    assert MACHINE_KEY not in run.stdout and MACHINE_KEY not in run.stderr


def test_the_backend_strips_every_cloud_key_secrets_py_reads(packaged_module):
    """The strip list is discovered from secrets.py, not guessed."""
    from app.ai import secrets
    declared = set()
    for _provider, (_path, names) in secrets.PROVIDER_SECRETS.items():
        declared.update(names)
    missing = declared - set(packaged_module.CLOUD_KEY_ENV)
    assert not missing, "secrets.py reads env names the packaged backend does not strip: %s" % (
        sorted(missing),)


def test_the_launch_binding_never_lands_in_the_backend_environment(packaged):
    """In-process, not exported: the backend's own environment stays clean."""
    run = packaged(config_mode="present", launch_mode="ok")
    assert run.wait_ready(), run.stderr
    # The stub records which published names were already in os.environ; a
    # binding exported through the environment would show up here.
    published = [entry for entry in _records(run.record) if entry["event"] == "published"][0]
    assert "WB_LOCAL_API_KEY" in published["names"], "the binding must carry the bearer"
    assert published["in_environment"] == [], (
        "the binding was exported to os.environ, so children inherit the bearer: %s"
        % (published["in_environment"],))


# --- 5. a failed or interrupted start stops the owned child ---------------

def test_a_failed_launch_stops_the_owned_child(packaged):
    run = packaged(config_mode="present", launch_mode="fail")
    assert run.wait_ready()
    run.finish()
    events = _events(run.record)
    assert "start" in events and "stop" in events, events
    assert events.index("stop") >= events.index("start")


def test_an_interrupt_during_a_launch_stops_the_child_and_exits(packaged):
    run = packaged(config_mode="present", launch_mode="interrupt")
    # Nothing becomes ready: this run ends during startup.
    assert run.wait_ready(timeout=15) is False
    code = run.finish()
    assert code == 0, run.stderr
    events = _events(run.record)
    assert "start" in events and "stop" in events, events
    assert not run.ready_path.is_file()


# --- 6. every exit path stops the owned model ----------------------------

@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_on_quit_stops_the_owned_model(packaged, sig):
    run = packaged(config_mode="present", launch_mode="ok")
    assert run.wait_ready(), run.stderr
    assert "stop" not in _events(run.record), "the model must run while the session does"
    code = run.finish(sig=sig)
    assert code == 0, run.stderr
    events = _events(run.record)
    assert events.count("stop") == 1, "the owned model is stopped exactly once"
    assert not run.ready_path.is_file(), "the ready file is removed on the way out"


def test_the_parent_pid_watchdog_stops_the_owned_model(packaged):
    """Electron's death ends this backend, which stops the model it started."""
    # A pid that is certainly dead: a child this test started and reaped.
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    run = packaged(config_mode="present", launch_mode="ok", parent_pid=gone.pid)
    # The backend notices within its 0.25 s poll, stops the model and exits on
    # its own: no signal from this test is involved.
    assert run.wait_for_exit(timeout=30) == 0, run.stderr
    events = _events(run.record)
    assert "start" in events and events.count("stop") == 1, events
    assert not run.ready_path.is_file()


# --- 7. the backend-death reaper -----------------------------------------

def test_the_reaper_is_armed_for_the_launched_model(files, monkeypatch, tmp_path):
    """A start arms one watchdog for the model pid, and a stop disarms it."""
    exe, weights = files
    config = local_config._validate({"runtime_executable": exe, "model_weights": weights,
                                     "alias": ALIAS, "context": CONTEXT, "port": 9931})
    armed, disarmed = [], []

    class FakeReaper:
        def poll(self):
            return None

    def arm(model_pid, log_path=None):
        armed.append((model_pid, log_path))
        return FakeReaper()

    def disarm(watch):
        disarmed.append(watch)
        return None

    monkeypatch.setattr(local_launcher, "port_in_use", lambda port: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess(pid=6161))
    monkeypatch.setattr(local_launcher.local_reaper, "arm", arm)
    monkeypatch.setattr(local_launcher.local_reaper, "disarm", disarm)
    monkeypatch.setattr(local_launcher, "_serves_verified_identity",
                        lambda *a, **k: {"model_alias": ALIAS, "n_ctx": CONTEXT,
                                        "n_ctx_train": 262144, "total_slots": 1})
    monkeypatch.setattr(local_launcher.LocalServer, "measure_template_overhead",
                        lambda self, deadline=None: setattr(self, "template_overhead_tokens", 42) or 42)
    server = local_launcher.LocalServer(config, log_path=str(tmp_path / "local.log"))
    server.start()
    assert armed == [(6161, str(tmp_path / "local.log"))]
    server.stop()
    assert len(disarmed) == 1


def test_a_watchdog_that_cannot_start_fails_the_launch(files, monkeypatch):
    """No watchdog, no protected run: the launch fails instead of hiding it."""
    exe, weights = files
    config = local_config._validate({"runtime_executable": exe, "model_weights": weights,
                                     "alias": ALIAS, "context": CONTEXT, "port": 9931})
    fake = _FakeProcess(pid=7171)
    monkeypatch.setattr(local_launcher, "port_in_use", lambda port: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(local_launcher, "_serves_verified_identity", lambda *a, **k: None)

    def broken(*a, **k):
        raise local_reaper.ReaperError("watchdog could not start")

    monkeypatch.setattr(local_launcher.local_reaper, "arm", broken)
    server = local_launcher.LocalServer(config)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.start()
    assert "watchdog" in str(caught.value)
    assert fake.terminated is True, "the model spawned before arming must still be stopped"
    # The per-run key file is removed on this failure path too.
    assert server.key_file is None
    assert server.secret is None


def test_the_watchdog_is_optional(monkeypatch, files):
    exe, weights = files
    config = local_config._validate({"runtime_executable": exe, "model_weights": weights,
                                     "alias": ALIAS, "context": CONTEXT, "port": 9931})
    monkeypatch.setattr(local_launcher, "port_in_use", lambda port: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    monkeypatch.setattr(local_launcher, "_serves_verified_identity",
                        lambda *a, **k: {"model_alias": ALIAS, "n_ctx": CONTEXT,
                                        "n_ctx_train": 262144, "total_slots": 1})
    monkeypatch.setattr(local_launcher.LocalServer, "measure_template_overhead",
                        lambda self, deadline=None: setattr(self, "template_overhead_tokens", 42) or 42)
    monkeypatch.setattr(local_launcher.local_reaper, "arm",
                        lambda *a, **k: pytest.fail("armed when it was told not to"))
    server = local_launcher.LocalServer(config, watch_parent=False)
    server.start()
    try:
        assert server.reaper is None
        assert local_launcher.binding_environment(server)["WB_LOCAL_MODEL"] == ALIAS
    finally:
        server.stop()


def test_the_reaper_argv_carries_only_the_two_pids():
    argv = local_reaper.reaper_command(1234, 5678, 0.25)
    assert argv[0] == sys.executable
    assert argv[1] == "-c"
    # The module is imported from this bundle's own app root, never a pasted copy.
    assert str(ROOT) in argv[2]
    assert "local_reaper" in argv[2]
    assert argv[3:] == ["1234", "5678", "0.25"]


def test_the_spawned_watchdog_reports_main_exit_codes(tmp_path):
    """A real run of the real argv: the bootstrap must carry ``main``'s code out.

    Without ``sys.exit`` a watchdog that could not parse its argv would exit 0
    and look like a clean run, so a broken arm would read as a healthy one.
    """
    completed = subprocess.run(local_reaper.reaper_command(1, 1)[:3] + ["only-one"],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2, (completed.returncode, completed.stderr[:500])
    assert "usage" in completed.stderr.lower()


def test_arm_starts_one_detached_watchdog(monkeypatch, tmp_path):
    seen = []

    class FakeProcess:
        pid = 31337

        def poll(self):
            return None

        def wait(self, timeout=None):
            # Still running after the arm-confirmation wait: a healthy watchdog.
            raise subprocess.TimeoutExpired("watchdog", timeout)

    def popen(argv, **kwargs):
        seen.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(local_reaper.subprocess, "Popen", popen)
    watch = local_reaper.arm(4242, 4343, 0.5, log_path=str(tmp_path / "reaper.log"))
    assert watch.model_pid == 4242 and watch.backend_pid == 4343
    argv, kwargs = seen[0]
    assert argv == local_reaper.reaper_command(4242, 4343, 0.5)
    # Its own session, no stdin, no shell: the same rules as the model child.
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_arm_refuses_a_pid_that_is_not_a_pid(monkeypatch):
    monkeypatch.setattr(local_reaper.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("spawned a watchdog for no model"))
    for bad in (0, -1, None, "4242", True):
        with pytest.raises(local_reaper.ReaperError):
            local_reaper.arm(bad)


# --- F4: an arm is confirmed, not assumed ---------------------------------

def test_arm_confirms_the_watchdog_is_alive_before_returning(monkeypatch, tmp_path):
    """Popen returning only means a process was created, so liveness is checked."""
    observed = []

    class FakeProcess:
        pid = 31337
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            observed.append(timeout)
            raise subprocess.TimeoutExpired("watchdog", timeout)

    monkeypatch.setattr(local_reaper.subprocess, "Popen", lambda *a, **k: FakeProcess())
    watch = local_reaper.arm(4242, 4343, 0.5, log_path=str(tmp_path / "reaper.log"))
    assert watch.model_pid == 4242
    # A bounded observation window, not an instant return and not an endless wait.
    assert observed == [pytest.approx(local_reaper.ARM_CONFIRM_SECONDS)]
    assert 0.0 < local_reaper.ARM_CONFIRM_SECONDS <= 1.0


def test_arm_turns_an_early_exit_into_a_reaper_error(monkeypatch):
    """A watchdog that dies at once would leave the model unwatched."""

    class DiesImmediately:
        pid = 31337
        returncode = 1

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(local_reaper.subprocess, "Popen", lambda *a, **k: DiesImmediately())
    with pytest.raises(local_reaper.ReaperError) as caught:
        local_reaper.arm(4242, 4343)
    assert "exited immediately" in str(caught.value)
    assert "unwatched" in str(caught.value)


def test_arm_is_not_fooled_by_wait_returning_without_an_exit(monkeypatch):
    """The verdict comes from poll(), the same rule stop() uses.

    A ``wait`` that returns without the child having exited (or a stand-in that
    reports nothing) must be read as "no exit observed", never as a dead child.
    """

    class WaitReturnsButAlive:
        pid = 31337
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return None

    monkeypatch.setattr(local_reaper.subprocess, "Popen", lambda *a, **k: WaitReturnsButAlive())
    assert local_reaper.arm(4242, 4343).model_pid == 4242


def test_an_early_watchdog_exit_fails_the_launch_and_stops_the_model(files, monkeypatch):
    """F4 end to end: a failed arm is a failed start, and the model is stopped."""
    exe, weights = files
    config = local_config._validate({"runtime_executable": exe, "model_weights": weights,
                                     "alias": ALIAS, "context": CONTEXT, "port": 9931})
    fake = _FakeProcess(pid=8181)
    monkeypatch.setattr(local_launcher, "port_in_use", lambda port: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(local_launcher, "_serves_verified_identity", lambda *a, **k: None)
    real_arm = local_reaper.arm

    def arm_that_dies(*a, **k):
        # Exercise the real confirmation path, but with a child that exits at once.
        class DiesImmediately:
            pid = 31337
            returncode = 3

            def poll(self):
                return 3

            def wait(self, timeout=None):
                return 3

        monkeypatch.setattr(local_reaper.subprocess, "Popen",
                            lambda *a2, **k2: DiesImmediately())
        try:
            return real_arm(*a, **k)
        finally:
            monkeypatch.setattr(local_reaper.subprocess, "Popen", lambda *a2, **k2: fake)

    monkeypatch.setattr(local_launcher.local_reaper, "arm", arm_that_dies)
    server = local_launcher.LocalServer(config)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.start()
    assert "exited immediately" in str(caught.value)
    # The model spawned before the failed arm is stopped, and local is unavailable.
    assert fake.terminated is True
    assert server.process.poll() is not None
    assert server.key_file is None and server.secret is None


def test_the_watchdog_treats_a_reparent_as_the_supervisor_dying(monkeypatch):
    """Reparenting is the signal a reused pid cannot forge, so it is checked first."""
    monkeypatch.setattr(local_reaper.os, "getppid", lambda: 4242)
    monkeypatch.setattr(local_reaper.os, "kill", lambda pid, num: None)
    assert local_reaper._backend_gone(4242) is False
    # Same pid still reported alive, but this process is no longer its child.
    monkeypatch.setattr(local_reaper.os, "getppid", lambda: 1)
    assert local_reaper._backend_gone(4242) is True


def test_the_watchdog_treats_a_dead_supervisor_as_gone(monkeypatch):
    monkeypatch.setattr(local_reaper.os, "getppid", lambda: 4242)
    calls = []

    def kill(pid, number):
        calls.append((pid, number))
        raise ProcessLookupError

    monkeypatch.setattr(local_reaper.os, "kill", kill)
    assert local_reaper._backend_gone(4242) is True
    assert calls == [(4242, 0)], "liveness is a signal-0 existence check"


def test_the_watchdog_stops_the_model_with_sigterm_then_signals_again(monkeypatch):
    """The model is asked to exit, and only then killed."""
    sent = []
    alive = {8123: True}

    def kill(pid, number):
        sent.append((pid, number))
        if number == signal.SIGKILL:
            alive[pid] = False

    monkeypatch.setattr(local_reaper.os, "kill", kill)
    monkeypatch.setattr(local_reaper, "_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(local_reaper.time, "sleep", lambda seconds: None)
    last = local_reaper._stop_model(8123, grace=0)
    assert sent[0] == (8123, signal.SIGTERM)
    assert sent[-1] == (8123, signal.SIGKILL)
    assert last == signal.SIGKILL


def test_the_watchdog_does_not_kill_a_model_that_already_exited(monkeypatch):
    alive = {8123: True}
    sent = []

    def kill(pid, number):
        sent.append((pid, number))
        if number == signal.SIGTERM:
            alive[pid] = False

    monkeypatch.setattr(local_reaper.os, "kill", kill)
    monkeypatch.setattr(local_reaper, "_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(local_reaper.time, "sleep", lambda seconds: None)
    assert local_reaper._stop_model(8123, grace=5) == signal.SIGTERM
    assert [number for _, number in sent] == [signal.SIGTERM]


def test_the_watchdog_main_waits_for_the_supervisor_then_stops_the_model(monkeypatch):
    states = iter([False, False, True])

    def backend_gone(pid):
        return next(states)

    stopped = []
    monkeypatch.setattr(local_reaper, "_backend_gone", backend_gone)
    monkeypatch.setattr(local_reaper, "_stop_model", lambda pid: stopped.append(pid) or None)
    monkeypatch.setattr(local_reaper.time, "sleep", lambda seconds: None)
    assert local_reaper.main(["8123", "4242", "0.01"]) == 0
    assert stopped == [8123], "the model is stopped only after the supervisor is gone"


def test_the_watchdog_refuses_a_missing_or_nonsense_pid(monkeypatch):
    monkeypatch.setattr(local_reaper, "_stop_model",
                        lambda pid: pytest.fail("stopped something with no valid pid"))
    assert local_reaper.main([]) == 2
    assert local_reaper.main(["8123"]) == 2
    assert local_reaper.main(["not-a-pid", "4242"]) == 2
    assert local_reaper.main(["0", "4242"]) == 2


def test_the_watchdog_watches_only_the_pid_it_was_given(monkeypatch):
    """It never scans for a process: one model pid, one supervisor pid."""
    signalled = []

    class FakeProcess:
        pid = 9090

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("watchdog", timeout)

    monkeypatch.setattr(local_reaper.subprocess, "Popen",
                        lambda argv, **k: (signalled.append(argv), FakeProcess())[1])
    local_reaper.arm(4242, 4343)
    argv = signalled[0]
    assert argv[3:] == ["4242", "4343", "0.5"]
    # No name, no port, no pattern: the argv carries two integers and a period.
    assert all(part.replace(".", "").isdigit() for part in argv[3:])


def test_disarm_stops_the_watchdog_and_waits(monkeypatch):
    class FakeProcess:
        pid = 5050
        returncode = None

        def __init__(self):
            self.terminated = 0
            self.killed = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated += 1
            self.returncode = -15

        def kill(self):
            self.killed += 1
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    process = FakeProcess()
    watch = local_reaper.ReaperWatch(process, 4242, 4343)
    assert local_reaper.disarm(watch) is None
    assert process.terminated == 1 and process.killed == 0


def test_disarm_reports_a_watchdog_that_will_not_exit(monkeypatch):
    class StuckProcess:
        pid = 5051
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("watchdog", timeout)

    message = local_reaper.disarm(local_reaper.ReaperWatch(StuckProcess(), 4242, 4343))
    assert message and "did not exit" in message


def test_disarm_tolerates_no_watchdog():
    assert local_reaper.disarm(None) is None


#: A supervisor whose only job is to start one detached child (the stand-in for
#: the model) and arm the real watchdog for it. Written to a temp file and run as
#: a real process because the property under test IS the behaviour after this
#: process is killed: nothing inside a single process can show that.
REAPER_SUPERVISOR = '''
import subprocess, sys, time
sys.path.insert(0, {app_root!r})
from app.ai import local_reaper
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                         start_new_session=True)
local_reaper.arm(child.pid, poll_seconds=0.1)
print(child.pid, flush=True)
time.sleep(600)
'''


def test_a_killed_supervisor_does_not_strand_its_child(tmp_path):
    """The reaper, exercised for real with a plain sleeping child, no model.

    The supervisor process here is killed with SIGKILL, so no handler, no
    ``finally`` and no parent-pid poll in it can run. Only the watchdog it armed
    can stop the child, which is exactly the condition the packaged app has to
    survive. The "model" is ``time.sleep``: no model, no runtime, no weights.
    """
    script = tmp_path / "reaper_supervisor.py"
    script.write_text(REAPER_SUPERVISOR.format(app_root=str(ROOT)))
    supervisor = subprocess.Popen([sys.executable, str(script)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        line = supervisor.stdout.readline().strip()
        assert line.isdigit(), "the supervisor reported no child pid"
        child_pid = int(line)
        assert _pid_alive(child_pid), "the stand-in child should be running"

        supervisor.kill()
        supervisor.wait(timeout=30)
        assert supervisor.returncode == -signal.SIGKILL

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and _pid_alive(child_pid):
            time.sleep(0.1)
        assert not _pid_alive(child_pid), (
            "the watchdog must stop the child when its supervisor is SIGKILLed")
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=15)
        if child_pid and _pid_alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def _pid_alive(pid):
    """Whether ``pid`` still exists, treating a zombie as exited."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A reaped-but-not-yet-reaped child can still answer signal 0; ask the OS
    # whether it is a zombie by reading its state through ps-free means.
    try:
        state = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True,
                               timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(state) and not state.startswith("Z")


# --- fixtures shared with the launcher tests -----------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    """Stand-ins for the installed executable and weights (never executed)."""
    import hashlib
    exe = tmp_path / "llama-server"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    weights = tmp_path / "weights.gguf"
    weights.write_bytes(b"GGUF" + b"0" * 1024)
    monkeypatch.setitem(local_runtime.MODEL_FACTS, "bytes", weights.stat().st_size)
    monkeypatch.setitem(local_runtime.MODEL_FACTS, "sha256",
                        hashlib.sha256(weights.read_bytes()).hexdigest())
    return str(exe), str(weights)


class _FakeProcess:
    """Minimal spawned-child stand-in: records terminate and reports liveness."""

    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


# --- F3: the staged bundle must actually ship the local runtime ----------

@pytest.fixture
def dmg_tool():
    """The real ``tools/build_review_dmg.py`` loaded as a module, not executed."""
    spec = importlib.util.spec_from_file_location(
        "wb_build_review_dmg_under_test", ROOT / "tools" / "build_review_dmg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def staged_bundle(tmp_path):
    """A copy of ``app/ai`` holding only the modules the local runtime needs."""
    root = tmp_path / "wb"
    ai = root / "app" / "ai"
    ai.mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (ai / "__init__.py").write_text("")
    shipped = ("local_config.py", "local_runtime.py", "local_launcher.py", "local_reaper.py")
    for name in shipped:
        (ai / name).write_bytes((ROOT / "app" / "ai" / name).read_bytes())
    return root


def _staged_environment(root):
    """The environment ``verify_staged_runtime`` hands the staged interpreter."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root.parent),
            # Pinned absent, so the check can never need or leave a model.
            "WB_LOCAL_CONFIG": str(root.parent / "no-local-runtime-configured.json"),
            "PYTHONNOUSERSITE": "1"}


def test_staged_local_modules_are_proven_present(dmg_tool, staged_bundle):
    """The shipped bundle answers both checks with no model and no weights."""
    result = dmg_tool._verify_local_modules(sys.executable, staged_bundle,
                                            _staged_environment(staged_bundle))
    assert result == {"import_ok": True, "reaper_usage_exit": 2}


@pytest.mark.parametrize("dropped", ["local_launcher.py", "local_reaper.py",
                                     "local_config.py", "local_runtime.py"])
def test_a_stage_missing_a_local_module_is_rejected(dmg_tool, staged_bundle, dropped):
    """A stage that dropped one would otherwise look like an honest "not configured"."""
    (staged_bundle / "app" / "ai" / dropped).unlink()
    with pytest.raises(RuntimeError) as caught:
        dmg_tool._verify_local_modules(sys.executable, staged_bundle,
                                       _staged_environment(staged_bundle))
    message = str(caught.value)
    assert "optional local runtime" in message
    assert dropped.removesuffix(".py") in message


def test_the_staged_check_needs_no_model_and_leaves_nothing_running(dmg_tool, staged_bundle):
    """Only argv parsing runs: no process is armed, no weight file is opened."""
    environment = _staged_environment(staged_bundle)
    assert not Path(environment["WB_LOCAL_CONFIG"]).exists()
    dmg_tool._verify_local_modules(sys.executable, staged_bundle, environment)
    # Nothing was created at the pinned path, and no watchdog was spawned with
    # its own session (which would outlive this test).
    assert not Path(environment["WB_LOCAL_CONFIG"]).exists()
    assert sorted(p.name for p in (staged_bundle / "app" / "ai").iterdir()
                  if p.suffix == ".py") == ["__init__.py", "local_config.py",
                                            "local_launcher.py", "local_reaper.py",
                                            "local_runtime.py"]

