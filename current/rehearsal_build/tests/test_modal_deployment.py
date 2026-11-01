"""Offline only; no model imports, credentials or Modal hydration."""
import asyncio
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = load("modal_glimmer_smoke")


def response(stage):
    content = ({"target_release": "release2023", "checks": ["Compare release IDs"]}
               if stage == "frame" else
               {"interpretation": "plan-review completion", "caveat": "Not final approval"})
    import json
    return {"model": smoke.MODEL_ID, "choices": [{
        "finish_reason": "stop", "message": {"role": "assistant",
                                          "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


@pytest.mark.parametrize("fail_at", [None, 0, 1, 2])
def test_serial_bounded_no_retry(fail_at):
    sent, events = [], []

    def handler(request):
        i = len(sent)
        sent.append(request)
        assert request.headers["Modal-Key"] == "private-id"
        assert request.headers["Modal-Secret"] == "private-secret"
        if i == fail_at:
            return httpx.Response(503)
        return httpx.Response(200, json=response(smoke.TASKS[i][0]))

    result = asyncio.run(smoke.smoke(
        "https://synthetic.modal.run",
        {"proxy_token_id": "private-id", "proxy_token_secret": "private-secret"},
        events.append, httpx.MockTransport(handler)))
    assert result is (fail_at is None)
    assert len(sent) == (3 if fail_at is None else fail_at + 1)


@pytest.mark.parametrize("mutation", ["model", "usage", "finish"])
def test_invalid_response_stops(mutation):
    data = response("baseline")
    if mutation == "model":
        data["model"] = "other"
    elif mutation == "usage":
        data["usage"]["total_tokens"] = 99
    else:
        data["choices"][0]["finish_reason"] = "length"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    assert not asyncio.run(smoke.smoke(
        "https://synthetic.modal.run",
        {"proxy_token_id": "id", "proxy_token_secret": "secret"},
        lambda event: None, httpx.MockTransport(handler)))
    assert len(calls) == 1


def test_frozen_evidence_and_bounded_payloads():
    import json
    assert len(smoke.PAYLOADS) == 3
    for payload in smoke.PAYLOADS:
        assert payload["model"] == "meta-models/Muse-Glimmer-30B"
        assert payload["max_tokens"] == 512
        assert json.loads(payload["messages"][1]["content"])["evidence"] == {
            "release2023": {"dictionary": {"approval_date": "plan-review completion"}},
            "release2025": {"note": "final approval"}}


def test_server_definition_offline(monkeypatch):
    monkeypatch.delenv("WB_MODAL_TRIAL_APP", raising=False)
    server = load("modal_glimmer_server")
    assert not hasattr(server, "app")
    command = server.server_command()
    assert command[command.index("--revision") + 1] == server.REVISION
    assert command[command.index("--tensor-parallel-size") + 1] == "1"
    assert "--trust-remote-code" not in command
    assert "--enforce-eager" in command
    assert "--language-model-only" in command
    assert command[command.index("--override-generation-config") + 1] == '{"max_new_tokens":512}'
    # Local SDK validation only. No build, hydration or account API request.
    app, endpoint = server.create_app("wb-glimmer-trial-abcdef123456")
    assert app.app_id is None
    with pytest.raises(ValueError):
        server.create_app("existing-app")


def test_resource_limits_and_minimal_source(monkeypatch):
    server = load("modal_glimmer_server")
    recorded = {}

    class Image:
        @staticmethod
        def from_registry(tag, **kwargs):
            recorded["image"] = tag
            recorded["registry_options"] = kwargs
            return Image()

        def pip_install(self, *packages):
            recorded["packages"] = packages
            return self

        def run_commands(self, *commands):
            assert "packages" not in recorded  # Python setup precedes pip.
            recorded["python_setup"] = commands
            return self

        def add_local_file(self, source, target, **kwargs):
            recorded["source"] = (source, target, kwargs)
            return self

        def env(self, values):
            return self

        def run_function(self, function, **kwargs):
            recorded["builder"] = kwargs
            return self

    class App:
        def __init__(self, name, **kwargs):
            recorded["app"] = (name, kwargs)

        def function(self, **kwargs):
            recorded["function"] = kwargs
            return lambda f: f

    def web_server(port, **kwargs):
        recorded["web"] = (port, kwargs)
        return lambda f: f

    monkeypatch.setattr(server, "modal", SimpleNamespace(
        Image=Image, App=App, web_server=web_server))
    server.create_app("wb-glimmer-trial-abcdef123456")
    limits = recorded["function"]
    assert limits["gpu"] == "H100"
    assert limits["cpu"] == (2, 8)
    assert limits["memory"] == (65536, 131072)
    assert limits["max_containers"] == 1
    assert limits["min_containers"] == limits["buffer_containers"] == 0
    assert limits["retries"] is None  # SDK rejects retries=0 for Web Functions.
    assert limits["scaledown_window"] == 2
    assert limits["timeout"] == 180 and limits["startup_timeout"] == 900
    assert limits["serialized"] is False
    assert recorded["app"][1]["include_source"] is False
    assert recorded["builder"]["include_source"] is False
    assert recorded["builder"]["timeout"] == 600
    assert recorded["source"][0].name == "modal_glimmer_server.py"
    assert recorded["source"][1] == "/root/modal_glimmer_server.py"
    assert recorded["web"] == (8000, {"startup_timeout": 900, "requires_proxy_auth": True})
    options = recorded["registry_options"]
    assert "add_python" not in options  # retain the vLLM interpreter/packages
    assert options["setup_dockerfile_commands"] == [
        "RUN ln -s /usr/bin/python3 /usr/local/bin/python"
        " && python -m pip --version && pip --version",
        "ENTRYPOINT []",
    ]
    import shlex
    command = shlex.split(recorded["python_setup"][0])
    assert command[:2] == ["/usr/bin/python3", "-c"]
    assert "\n" not in recorded["python_setup"][0]
    assert "\r" not in recorded["python_setup"][0]
    programs = []
    exec(command[2], {"exec": programs.append})
    assert programs == [server.identity_code(True)]


@pytest.mark.parametrize("failure", [None, "build", "inference", "token"])
@pytest.mark.parametrize("create_proxy", [True, False])
def test_orchestration_cleanup_only_owned_resources(monkeypatch, failure, create_proxy):
    calls, events = [], []

    class App:
        app_id = None

        @contextmanager
        def run(self, **kwargs):
            assert kwargs == {"detach": False}
            self.app_id = "ap-NewTrialOnly"  # allocated during entry, before build
            if failure == "build":
                raise RuntimeError("synthetic build failure")
            yield

    monkeypatch.setitem(sys.modules, "modal_glimmer_server", SimpleNamespace(
        create_app=lambda name: (App(), SimpleNamespace(
            get_web_url=lambda: "https://synthetic.modal.run"))))
    monkeypatch.setattr(smoke.signal, "setitimer", lambda *args: None)

    def fake_cli(*args):
        calls.append(args)
        if "list" in args:
            return SimpleNamespace(returncode=0, stdout="[]")
        if "create" in args:
            return SimpleNamespace(
                returncode=1 if failure == "token" else 0,
                stdout=json.dumps({"Modal-Key": "wk-NewTrialOnly",
                                   "Modal-Secret": "ws-PrivateNewSecret"}))
        return SimpleNamespace(returncode=0)

    async def fake_smoke(endpoint, cfg, emit):
        assert endpoint == "https://synthetic.modal.run"
        if failure == "inference":
            raise TimeoutError("synthetic deadline")
        return True

    monkeypatch.setattr(smoke, "cli", fake_cli)
    monkeypatch.setattr(smoke, "smoke", fake_smoke)
    cfg = {"base_url": None, "proxy_token_id": "wk-UserExisting",
           "proxy_token_secret": "ws-UserSecret"}
    ok = smoke.orchestrate("wb-glimmer-trial-abcdef123456", cfg,
                           events.append, create_proxy)
    assert ok is (failure is None or (failure == "token" and not create_proxy))
    assert ("app", "stop", "ap-NewTrialOnly", "--yes") in calls
    deletes = [x for x in calls if "delete" in x]
    assert deletes == ([("workspace", "proxy-tokens", "delete",
                         "wk-NewTrialOnly", "--yes")]
                       if create_proxy and failure != "build" else [])
    assert "UserSecret" not in json.dumps(events)
    assert "PrivateNewSecret" not in json.dumps(events)
    assert "wk-NewTrialOnly" not in json.dumps(events)


def test_forecast_under_working_budget_not_hard_cap():
    forecast = smoke.forecast()
    assert forecast["planning_total_usd"] < forecast["working_budget_usd"] == 10
    assert forecast["hard_billing_cap"] is False
    assert forecast["wall_seconds"] == 2100
    assert forecast["runtime_memory_mib_request_limit"] == [65536, 131072]


def test_cli_always_captures_secret_output(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="private")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    smoke.cli("workspace", "proxy-tokens", "create", "--name", "trial", "--json")
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-m", "modal"]
    assert kwargs == {"capture_output": True, "text": True, "timeout": 30, "check": False}


@pytest.mark.parametrize("creation", ["timeout", "unparseable", "invalid_id"])
@pytest.mark.parametrize("metadata", ["owned", "absent", "ambiguous", "baseline_name"])
def test_uncertain_token_reconciliation(monkeypatch, creation, metadata):
    name = "wb-glimmer-trial-abcdef123456"
    calls, events = [], []

    class App:
        app_id = None

        @contextmanager
        def run(self, **kwargs):
            self.app_id = "ap-AllocatedNew"
            yield

    monkeypatch.setitem(sys.modules, "modal_glimmer_server", SimpleNamespace(
        create_app=lambda name: (App(), SimpleNamespace(
            get_web_url=lambda: "https://synthetic.modal.run"))))
    monkeypatch.setattr(smoke.signal, "setitimer", lambda *args: None)
    inventories = 0

    def cli(*args):
        nonlocal inventories
        calls.append(args)
        assert smoke.signal.getsignal(smoke.signal.SIGINT) == smoke.signal.SIG_IGN if (
            "stop" in args or "delete" in args or inventories > 0 and "list" in args
        ) else True
        if "list" in args:
            inventories += 1
            rows = [{"name": "user-token", "token_id": "wk-UserExisting"}]
            if metadata == "baseline_name":
                rows.append({"name": name, "token_id": "wk-ExistingSameName"})
            elif inventories > 1 and metadata in ("owned", "ambiguous"):
                rows.append({"name": name, "token_id": "wk-NewOwned"})
                if metadata == "ambiguous":
                    rows.append({"name": name, "token_id": "wk-AnotherNew"})
            return SimpleNamespace(returncode=0, stdout=json.dumps(rows))
        if "create" in args:
            if creation == "timeout":
                raise smoke.subprocess.TimeoutExpired("captured", 30)
            if creation == "invalid_id":
                return SimpleNamespace(returncode=0, stdout='{"Modal-Key":123}')
            return SimpleNamespace(returncode=0, stdout="unparseable private output")
        if "stop" in args:
            # Even an unexpected app cleanup exception must not skip the token.
            raise RuntimeError("synthetic stop failure")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke, "cli", cli)
    assert not smoke.orchestrate(name, {"base_url": None}, events.append, True)
    deleted = [x for x in calls if "delete" in x]
    assert deleted == ([("workspace", "proxy-tokens", "delete", "wk-NewOwned", "--yes")]
                       if metadata == "owned" else [])
    if metadata == "baseline_name":
        assert not any("create" in x for x in calls)
    else:
        assert any("stop" in x for x in calls)
        assert any(e["event"] == "temporary_proxy_cleanup" for e in events)
    assert "private output" not in json.dumps(events)


def test_cleanup_ignores_termination_and_has_nonrenewing_deadline(monkeypatch):
    alarms = []
    monkeypatch.setattr(smoke.signal, "setitimer", lambda *args: alarms.append(args))
    old = {sig: smoke.signal.getsignal(sig) for sig in (
        smoke.signal.SIGINT, smoke.signal.SIGTERM, smoke.signal.SIGALRM)}
    with smoke.cleanup_reserve():
        assert smoke.signal.getsignal(smoke.signal.SIGINT) == smoke.signal.SIG_IGN
        assert smoke.signal.getsignal(smoke.signal.SIGTERM) == smoke.signal.SIG_IGN
        with pytest.raises(smoke.CleanupDeadline):
            smoke.signal.getsignal(smoke.signal.SIGALRM)(smoke.signal.SIGALRM, None)
        assert alarms == [(smoke.signal.ITIMER_REAL, 0),
                          (smoke.signal.ITIMER_REAL, smoke.CLEANUP_SECONDS)]
    assert all(smoke.signal.getsignal(sig) == handler for sig, handler in old.items())


def test_installed_sdk_allocates_identity_before_build_and_retains_on_unwind(monkeypatch):
    """Execute actual 1.6.3 runner/app unwind; replace all remote boundaries."""
    import modal.app
    import modal.runner as runner
    app = modal.app._App("offline-allocation-proof")
    assert app.app_id is None
    disconnected = []

    class Context:
        client = object()
        environment_name = "offline"

        def reset(self):
            return self

        async def in_place_upgrade(self, **kwargs):
            return self

    class Tasks:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def infinite_loop(self, *args, **kwargs):
            return SimpleNamespace(cancel=lambda: None)

    async def allocate(*args, **kwargs):
        assert app.app_id is None
        return SimpleNamespace(app_id="ap-AllocatedBeforeBuild", app_page_url="")

    async def build(*args):
        assert app.app_id == "ap-AllocatedBeforeBuild"
        raise RuntimeError("offline image-build failure")

    async def disconnect(client, app_id, exception):
        disconnected.append(app_id)

    app._root_load_context = Context()
    monkeypatch.setattr(runner, "TaskContext", Tasks)
    monkeypatch.setattr(runner, "_init_local_app_new", allocate)
    monkeypatch.setattr(runner, "_create_all_objects", build)
    monkeypatch.setattr(runner, "_status_based_disconnect", disconnect)
    monkeypatch.setattr(runner.OutputManager, "get", lambda: SimpleNamespace(is_enabled=False))

    async def invoke():
        async with runner._run_app(app):
            pytest.fail("build failure must precede yield")

    with pytest.raises(RuntimeError, match="offline image-build failure"):
        asyncio.run(invoke())
    assert disconnected == ["ap-AllocatedBeforeBuild"]
    assert app.app_id == "ap-AllocatedBeforeBuild"
    assert app._running_app is None


@pytest.mark.parametrize("state,identity,valid", [
    ("stopped", "ap-ExactNew", True),
    ("stopping...", "ap-ExactNew", False),
    ("ephemeral", "ap-ExactNew", False),
    ("stopped", "ap-OtherApp", False),
])
def test_failed_stop_requires_exact_readonly_confirmation(monkeypatch, state, identity, valid):
    calls, events = [], []

    def cli(*args):
        calls.append(args)
        if "stop" in args:
            return SimpleNamespace(returncode=1, stderr="App is already stopped.")
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "app_id": identity, "lifecycle": {"state": state}}))

    monkeypatch.setattr(smoke, "cli", cli)
    assert smoke.cleanup_app("ap-ExactNew", events.append) is valid
    assert calls == [("app", "stop", "ap-ExactNew", "--yes"),
                     ("app", "info", "ap-ExactNew", "--json")]
    assert events[-1]["confirmed"] is valid
    if valid:
        assert events[-1]["evidence"] == "read_only_app_info_stopped"


def test_build_capture_static_only_and_safe_exception():
    events = []
    # Use the actual Modal enable_output context, but no SDK app/network.
    with smoke.build_output(events.append):
        print("Building image im-private; ak-private as-private")
        print("/bin/sh: 1: python: not found wk-private ws-private")
        from modal.output import OutputManager
        OutputManager.get().print("ImageBuildError as-private-sdk-output")
        print("arbitrary personal/private raw output")
    assert {"event": "modal_build", "code": "python_executable_missing"} in events
    assert not any("private" in json.dumps(event) for event in events)
    from modal.exception import ImageBuildError
    assert smoke.safe_exception(ImageBuildError("Image build failed as-private", "im-TestOnly")) == {
        "exception_type": "ImageBuildError", "code": "image_build_failed",
        "qualified_exception_type": "modal.exception.ImageBuildError",
        "traceback_frames": [],
        "image_id": "im-TestOnly"}
    assert smoke.safe_exception(RuntimeError("personal info and as-private")) == {
        "exception_type": "RuntimeError", "code": "unclassified_failure",
        "qualified_exception_type": "builtins.RuntimeError", "traceback_frames": []}


def test_build_failure_summary_zero_posts(monkeypatch):
    from modal.exception import ImageBuildError
    events = []

    class App:
        app_id = None

        @contextmanager
        def run(self, **kwargs):
            self.app_id = "ap-NewBuildFailed"
            print("=> Step 1: RUN python -m pip install transformers==5.15.0")
            print("/bin/sh: 1: python: not found")
            raise ImageBuildError("Image build failed; raw private content", "im-TestOnly")
            yield

    monkeypatch.setitem(sys.modules, "modal_glimmer_server", SimpleNamespace(
        create_app=lambda name: (App(), None)))
    monkeypatch.setattr(smoke.signal, "setitimer", lambda *args: None)
    monkeypatch.setattr(smoke, "cli", lambda *args: SimpleNamespace(returncode=0))
    assert not smoke.orchestrate("wb-glimmer-trial-abcdef123456",
                                 {"base_url": None}, events.append)
    failure = next(e for e in events if e["event"] == "trial_failure")
    assert failure["exception_type"] == "ImageBuildError"
    assert failure["phase"] == "app_build_startup"
    assert failure["code"] == "image_build_failed"
    assert failure["inference_post_attempts"] == 0
    assert events[-1] == {"event": "trial_summary", "inference_post_attempts": 0,
                          "proxy_creation_attempted": False, "success": False,
                          "cost_usd": None}
    assert "raw private content" not in json.dumps(events)


@pytest.mark.parametrize("wheel,valid", [
    ("0.28.0+cu129", True), ("0.28.0", False),
    ("0.28.0+cu130", False), ("0.28.0+cu129.extra", False),
    ("0.29.0+cu129", False),
])
@pytest.mark.parametrize("transformers_version", ["5.14.0", "5.15.0", "5.16.0"])
def test_runtime_identity_version_policy(monkeypatch, wheel, valid, transformers_version):
    import importlib.metadata
    import subprocess
    server = load("modal_glimmer_server")
    versions = {"vllm": wheel, "transformers": transformers_version, "tokenizers": "0.22.0"}
    calls = []
    monkeypatch.setattr(importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        MuseGlimmerForConditionalGeneration=object()))

    def install(command, **kwargs):
        calls.append(command)
        versions["transformers"] = "5.15.0"

    monkeypatch.setattr(subprocess, "run", install)
    if valid:
        exec(server.identity_code(True), {})
        assert calls == ([["/usr/bin/python3", "-m", "pip", "install",
                           "transformers==5.15.0"]]
                         if transformers_version == "5.14.0" else [])
    else:
        with pytest.raises(AssertionError, match="vllm_cuda_variant_mismatch"):
            exec(server.identity_code(True), {})
        assert calls == []


def test_cpu_only_preflight_does_not_define_gpu_or_download(monkeypatch):
    server = load("modal_glimmer_server")
    recorded = {}

    class App:
        def __init__(self, name, **kwargs):
            pass

        def function(self, **kwargs):
            recorded.update(kwargs)
            return lambda f: f

    monkeypatch.setattr(server, "runtime_image", lambda: "offline-runtime-image")
    monkeypatch.setattr(server, "modal", SimpleNamespace(App=App))
    app, function = server.create_preflight_app("wb-glimmer-trial-abcdef123456")
    assert function is server.cpu_preflight
    assert "gpu" not in recorded
    assert recorded["cpu"] == (1, 2)
    assert recorded["memory"] == (2048, 4096)
    assert recorded["retries"] == 0
    assert recorded["image"] == "offline-runtime-image"


def test_bounded_actual_traceback_detail_without_source_or_secrets():
    events = []
    sink = smoke.BuildOutput(events.append)
    for chunk in [
        'Traceback (most recent call last):\n  File "<string>", line 2, in <module>\n',
        "  personal_source = 'do not emit me'\n",
        "ModuleNotFoundError: No module named 'packaging'\n",
        "NameError: name 'example_name' is not defined\n",
        "RuntimeError: arbitrary private data\n",
        "ImportError: api_key=private\n",
        "NameError: name 'as_confidential' is not defined ws-ab",
        "cdef\n",
    ]:
        sink.write(chunk)
    assert {"event": "modal_build_detail", "message":
            "ModuleNotFoundError: No module named 'packaging'"} in events
    assert {"event": "modal_build_detail", "file": "<string>",
            "line": 2, "function": "<module>"} in events
    raw = json.dumps(events)
    assert "do not emit" not in raw and "private" not in raw
    assert "abcdef" not in raw and "confidential" not in raw
    for _ in range(50):
        sink.write("ValueError: arbitrary content\n")
    assert sum(event["event"] == "modal_build_detail" for event in events) == 16


def test_qualified_exception_and_bounded_frames_no_source_message_or_locals(monkeypatch):
    import linecache

    def no_source(*args, **kwargs):
        pytest.fail("diagnostics must not read source")

    monkeypatch.setattr(linecache, "getline", no_source)
    monkeypatch.setattr(linecache, "getlines", no_source)

    def registration_seam(depth):
        credential_local = "ws-DO_NOT_EMIT_LOCAL"
        if depth:
            return registration_seam(depth - 1)
        raise AttributeError("DO_NOT_EMIT_MESSAGE " + credential_local)

    try:
        registration_seam(12)
    except AttributeError as exc:
        diagnostic = smoke.safe_exception(exc)
    assert diagnostic["qualified_exception_type"] == "builtins.AttributeError"
    assert len(diagnostic["traceback_frames"]) == 8
    assert all(frame["file"] == "test_modal_deployment.py" and
               frame["function"] == "registration_seam" and frame["line"] > 0
               for frame in diagnostic["traceback_frames"])
    raw = json.dumps(diagnostic)
    assert "DO_NOT_EMIT" not in raw
    assert "credential_local" not in raw
    assert set(diagnostic["traceback_frames"][0]) == {"file", "function", "line"}


@pytest.mark.parametrize("name", ["FAILED_PRECONDITION", "ABORTED"])
def test_conflict_diagnostic_preserves_exact_grpc_status_without_message(name):
    from grpclib.const import Status
    from modal.exception import ConflictError
    exc = ConflictError("ws-DO_NOT_EMIT_MESSAGE")
    exc._grpc_status = Status[name]
    exc._grpc_details = {"secret": "DO_NOT_EMIT_DETAILS"}
    diagnostic = smoke.safe_exception(exc)
    assert diagnostic["exception_type"] == "ConflictError"
    assert diagnostic["grpc_status"] == name
    assert diagnostic["code"] == "grpc_" + name.lower()
    assert "DO_NOT_EMIT" not in json.dumps(diagnostic)


def test_grpc_diagnostic_rejects_untyped_remote_status():
    from modal.exception import ConflictError
    exc = ConflictError("DO_NOT_EMIT")
    exc._grpc_status = "ws-DO_NOT_EMIT"
    assert "grpc_status" not in smoke.safe_exception(exc)
