"""L5: app-owned launcher lifecycle, config file, authentication and evidence.

No real inference, no model load and no real llama-server: ``subprocess.Popen``
is replaced by a fake child, the readiness/measurement probes are either faked or
answered by a **loopback test server started by this file** (the only sockets
opened are on 127.0.0.1, which ``conftest`` permits). What is asserted here:

* the argv is exact, carries the hardening flags and never carries a secret;
* the weights' pinned size and SHA-256 are verified before any spawn;
* the per-run bearer comes from an owner-only temporary file, is never in argv,
  and is removed on success, failure and interrupt;
* readiness requires the authenticated alias *and* the server's own ``/props``
  identity (alias + real ``n_ctx``), so an unrelated listener is not adopted;
* the chat-template overhead is measured, not assumed;
* the published binding is the verified one, and only the API child gets it.

The private config file is redirected to a temporary directory, so nothing
touches this machine's real configuration or any other application's state.
"""
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import local_config, local_launcher, local_runtime  # noqa: E402

EXECUTABLE = '/opt/example/runtime/b10621/llama-server'
WEIGHTS = '/opt/example/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
ALIAS = 'qwen3-4b-instruct-2507-q4_K_M'
SECRET = 'test-run-secret-not-a-real-credential'


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / 'local_runtime.json'
    monkeypatch.setenv('WB_LOCAL_CONFIG', str(path))
    return path


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Stand-ins for the installed executable and weights (never executed).

    The pinned digest and size are repointed at these stand-in bytes, so the
    verification code runs for real against a file this test controls.
    """
    exe = tmp_path / 'llama-server'
    exe.write_text('#!/bin/sh\nexit 0\n')
    exe.chmod(0o755)
    weights = tmp_path / 'weights.gguf'
    weights.write_bytes(b'GGUF' + b'0' * 1024)
    monkeypatch.setitem(local_runtime.MODEL_FACTS, 'bytes', weights.stat().st_size)
    monkeypatch.setitem(local_runtime.MODEL_FACTS, 'sha256',
                        hashlib.sha256(weights.read_bytes()).hexdigest())
    return str(exe), str(weights)


# --- the private configuration file ----------------------------------------

def test_absent_configuration_reads_as_unconfigured(cfg_path):
    assert local_config.read() == {}


def test_configuration_round_trips_the_supplied_paths(cfg_path, files):
    exe, weights = files
    local_config.save({'runtime_executable': exe, 'model_weights': weights,
                       'alias': ALIAS, 'context': 8192, 'port': 9931})
    saved = local_config.read()
    assert saved['runtime_executable'] == exe
    assert saved['model_weights'] == weights
    assert saved['alias'] == ALIAS
    assert saved['context'] == 8192
    assert saved['base_url'] == 'http://127.0.0.1:9931'


def test_configuration_file_is_owner_only(cfg_path, files):
    exe, weights = files
    local_config.save({'runtime_executable': exe, 'model_weights': weights,
                       'alias': ALIAS, 'context': 8192, 'port': 9931})
    mode = cfg_path.lstat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


@pytest.mark.parametrize('bad,field', [
    ({'runtime_executable': 'relative/llama-server'}, 'runtime_executable'),
    ({'runtime_executable': ''}, 'runtime_executable'),
    ({'model_weights': 'weights.gguf'}, 'model_weights'),
    ({'alias': 'has space'}, 'alias'),
    ({'alias': ''}, 'alias'),
    ({'context': 0}, 'context'),
    ({'context': '8192'}, 'context'),
    ({'port': 0}, 'port'),
    ({'port': 70000}, 'port'),
])
def test_invalid_configuration_is_refused_naming_the_field(cfg_path, files, bad, field):
    exe, weights = files
    values = {'runtime_executable': exe, 'model_weights': weights,
              'alias': ALIAS, 'context': 8192, 'port': 9931}
    values.update(bad)
    with pytest.raises(ValueError) as caught:
        local_config.save(values)
    assert field in str(caught.value)


def test_group_readable_configuration_is_refused(cfg_path, files):
    exe, weights = files
    local_config.save({'runtime_executable': exe, 'model_weights': weights,
                       'alias': ALIAS, 'context': 8192, 'port': 9931})
    cfg_path.chmod(0o644)
    with pytest.raises(ValueError) as caught:
        local_config.read()
    assert 'owner-only' in str(caught.value)


def test_saved_configuration_alone_is_not_a_binding(cfg_path, files, monkeypatch):
    """A saved file records what to launch; it must never claim a run exists."""
    for name in ('WB_LOCAL_BASE_URL', 'WB_LOCAL_MODEL', 'WB_LOCAL_CONTEXT',
                 'WB_LOCAL_RUN_ID', 'WB_LOCAL_SERVER_PID', 'WB_LOCAL_API_KEY',
                 'WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS', 'WB_LOCAL_WEIGHTS_SHA256',
                 'WB_LOCAL_OBSERVED_ALIAS', 'WB_LOCAL_OBSERVED_CTX',
                 'WB_LOCAL_OBSERVED_CTX_TRAIN'):
        monkeypatch.delenv(name, raising=False)
    exe, weights = files
    local_config.save({'runtime_executable': exe, 'model_weights': weights,
                       'alias': ALIAS, 'context': 4096, 'port': 9932})
    config = local_runtime.read_config()
    # The paths are known, but there is no run: not configured, no endpoint.
    assert config['configured'] is False
    assert config['origin'] is None
    assert local_runtime.chat_endpoint() is None
    assert 'local_run_not_launched' in config['blocking_reasons']
    # And the registry row says so instead of looking runnable.
    assert 'local_runtime_not_launched' in local_runtime.model_status()


def test_a_broken_config_file_never_breaks_the_app(cfg_path, monkeypatch):
    """The registry must stay importable when the file is hand-edited badly."""
    cfg_path.write_text('{not json')
    assert local_runtime.read_config()['configured'] is False


# --- argv, hardening flags and no secret in it -----------------------------

def test_argv_uses_the_configured_paths_and_hardening_flags(files):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    argv = local_launcher.build_argv(config, api_key_file=SECRET)
    assert argv == [
        exe, '-m', weights, '--alias', ALIAS,
        '--host', '127.0.0.1', '--port', '9931', '-c', '8192',
        '--no-webui',
        # With no declared app origin: one loopback origin, never `*`.
        '--cors-origins', 'http://127.0.0.1:9931',
        '--no-cors-credentials',
        '--no-context-shift',
        '--api-key-file', SECRET]
    # The hardening flags the reviewer required, exactly as documented.
    for flag in ('--no-webui', '--no-cors-credentials', '--no-context-shift',
                 '--api-key-file'):
        assert flag in argv
    # CORS is never a wildcard, with or without a declared app origin.
    assert '*' not in argv


def test_argv_uses_the_app_origin_for_cors_when_given(files):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    argv = local_launcher.build_argv({**config, 'app_origin': 'http://127.0.0.1:5173'},
                                     api_key_file='/tmp/keyfile')
    index = argv.index('--cors-origins')
    assert argv[index + 1] == 'http://127.0.0.1:5173'


# --- weights verification before any spawn --------------------------------

def test_pinned_digest_mismatch_refuses_to_launch(files, monkeypatch):
    """A different model file must never be launched under the pinned alias."""
    exe, weights = files
    monkeypatch.setitem(local_runtime.MODEL_FACTS, 'sha256', 'f' * 64)
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('spawned a mismatched model'))
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    message = str(caught.value)
    assert 'SHA-256' in message
    assert 'refusing to launch' in message


def test_pinned_size_mismatch_refuses_to_launch(files, monkeypatch):
    exe, weights = files
    monkeypatch.setitem(local_runtime.MODEL_FACTS, 'bytes', 1)
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('spawned a wrong-size model'))
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    assert 'refusing to launch a different model' in str(caught.value)


def test_verified_digest_is_recorded_for_the_binding(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = local_launcher.LocalServer(config)
    assert server._verify_weights() == hashlib.sha256(Path(weights).read_bytes()).hexdigest()


# --- preflight ------------------------------------------------------------

def test_missing_executable_refuses_before_spawning(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: started.append(a) or pytest.fail('spawned'))
    config = local_config._validate({'runtime_executable': str(tmp_path / 'nope'),
                                     'model_weights': str(tmp_path / 'w.gguf'),
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    assert 'missing or not executable' in str(caught.value)
    assert started == []


def test_non_executable_runtime_is_refused(files, tmp_path):
    _, weights = files
    exe = tmp_path / 'llama-server-not-exec'
    exe.write_text('')
    exe.chmod(0o644)
    config = local_config._validate({'runtime_executable': str(exe), 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    assert 'not executable' in str(caught.value)


def test_missing_weights_are_refused(files):
    exe, _ = files
    config = local_config._validate({'runtime_executable': exe,
                                     'model_weights': '/opt/example/absent.gguf',
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    assert 'weights are missing' in str(caught.value)


def test_occupied_port_fails_closed_without_spawning(files, monkeypatch):
    """A pre-existing listener is reported, never adopted and never killed."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned on an occupied port'))
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    message = str(caught.value)
    assert 'already in use' in message
    assert 'only stops a server it started itself' in message


def test_unconfigured_launch_is_a_no_op(cfg_path, monkeypatch):
    """Optional by construction: no config means the app starts exactly as before."""
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned without config'))
    assert local_launcher.launch_from_config() is None


# --- per-run secret and key file ------------------------------------------

class _FakeProcess:
    """Minimal child stand-in: records terminate/kill and reports a live state."""

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

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _observed(config):
    """What a launched server would report about itself at readiness."""
    return {'model_alias': config['alias'], 'n_ctx': config['context'],
            'n_ctx_train': 262144, 'total_slots': 1}


def _start_with(monkeypatch, config, fake, *, probe_raises=None, ready=True,
                identity=None, overhead=None, overhead_raises=None,
                spawn_seen=None):
    """Start a LocalServer whose child, readiness and measurement are faked."""
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)

    def popen(argv, **kwargs):
        if spawn_seen is not None:
            spawn_seen.append(argv)
        return fake

    monkeypatch.setattr(subprocess, 'Popen', popen)

    class _Watchdog:
        """Stand-in for the real watchdog process: alive, then stopped."""

        pid = 31337
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(local_launcher.local_reaper, 'arm',
                        lambda *a, **k: local_launcher.local_reaper.ReaperWatch(
                            _Watchdog(), getattr(fake, 'pid', 0), 1))
    monkeypatch.setattr(local_launcher.local_reaper, 'disarm', lambda watch: None)
    if probe_raises is not None:
        def probe(*a, **k):
            raise probe_raises
        monkeypatch.setattr(local_launcher, '_serves_verified_identity', probe)
    else:
        reported = identity if identity is not None else _observed(config)
        monkeypatch.setattr(local_launcher, '_serves_verified_identity',
                            lambda *a, **k: reported if ready else None)
    if overhead_raises is not None:
        def measure(self, deadline=None):
            raise overhead_raises
        monkeypatch.setattr(local_launcher.LocalServer, 'measure_template_overhead', measure)
    else:
        monkeypatch.setattr(local_launcher.LocalServer, 'measure_template_overhead',
                            lambda self, deadline=None: setattr(
                                self, 'template_overhead_tokens',
                                overhead if overhead is not None else 42) or 42)
    monkeypatch.setattr(local_launcher, 'STARTUP_TIMEOUT', 0.05)
    monkeypatch.setattr(local_launcher, 'POLL_SECONDS', 0.01)
    return local_launcher.LocalServer(config)


def test_secret_never_appears_in_argv(files, monkeypatch):
    """The bearer reaches the server through the key file, never through argv."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    seen = []
    server = _start_with(monkeypatch, config, _FakeProcess(), spawn_seen=seen)
    server.start()
    try:
        argv = seen[0]
        assert '--api-key-file' in argv
        key_path = argv[argv.index('--api-key-file') + 1]
        # The secret itself is not in argv; only the path to the file holding it.
        assert server.secret not in argv
        assert key_path.endswith('.txt')
        assert os.path.isfile(key_path)
        assert oct(os.stat(key_path).st_mode & 0o777) == '0o600'
        assert Path(key_path).read_text().strip() == server.secret
    finally:
        server.stop()
    # Removed on stop: a per-run credential must not outlive its run.
    assert not os.path.exists(key_path)


def test_packaged_fallback_preserves_listener_config_and_publishes_new_port(files, cfg_path, monkeypatch):
    exe, weights = files
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        preferred = other.getsockname()[1]
        local_config.save({'runtime_executable': exe, 'model_weights': weights,
                           'alias': ALIAS, 'context': 8192, 'port': preferred})
        saved_bytes = cfg_path.read_bytes()
        config = local_config.read()
        real_port_check = local_launcher.port_in_use
        seen = []
        server = _start_with(monkeypatch, config, _FakeProcess(), spawn_seen=seen)
        monkeypatch.setattr(local_launcher, 'port_in_use', real_port_check)
        server.allow_port_fallback = True
        server.start()
        try:
            selected = server.config['port']
            assert selected != preferred
            assert seen[0][seen[0].index('--port') + 1] == str(selected)
            assert local_launcher.binding_environment(server)['WB_LOCAL_BASE_URL'] == (
                'http://127.0.0.1:%d' % selected)
            assert config['port'] == preferred
            assert cfg_path.read_bytes() == saved_bytes
        finally:
            server.stop()
        # The unrelated listener survives both startup and owned-child cleanup.
        assert real_port_check(preferred)


def test_port_allocation_failure_starts_nothing(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: True)
    def unavailable():
        raise OSError('test allocation failed')
    monkeypatch.setattr(local_launcher, 'unused_loopback_port', unavailable)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned without a port'))
    server = local_launcher.LocalServer(config, allow_port_fallback=True)
    with pytest.raises(local_launcher.LocalLaunchError, match='No free loopback port'):
        server.start()
    assert server.key_file is None
    assert config['port'] == 9931


def test_key_file_is_removed_when_readiness_fails(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = _start_with(monkeypatch, config, _FakeProcess(), ready=False)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.start()
    assert 'did not become ready' in str(caught.value)
    assert server.key_file is None
    assert server.secret is None


def test_key_file_is_removed_when_measurement_fails(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = _start_with(monkeypatch, config, _FakeProcess(), overhead_raises=
                         local_launcher.LocalLaunchError('measurement failed'))
    with pytest.raises(local_launcher.LocalLaunchError, match='measurement failed'):
        server.start()
    assert server.key_file is None


def test_a_fresh_secret_per_run(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    first = _start_with(monkeypatch, config, _FakeProcess())
    first.start()
    secret_one, path_one = first.secret, first.key_file
    first.stop()
    second = _start_with(monkeypatch, config, _FakeProcess())
    second.start()
    try:
        assert second.secret != secret_one
        assert second.key_file != path_one
        assert second.run_id != first.run_id
    finally:
        second.stop()


def test_the_secret_reaches_neither_the_saved_configuration_nor_the_console(
        files, cfg_path, monkeypatch, capsys):
    """B2: the bearer exists in memory, the API child's env and nothing else.

    The saved file describes what to launch and is rewritten by nothing in a
    launch; the launcher's own reporting prints the run, never the credential.
    The registry snapshot and the receipt are covered in the L1/L4 suites.
    """
    exe, weights = files
    local_config.save({'runtime_executable': exe, 'model_weights': weights,
                       'alias': ALIAS, 'context': 8192, 'port': 9931})
    saved_before = cfg_path.read_text()
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = _start_with(monkeypatch, config, _FakeProcess())
    server.start()
    secret = server.secret
    serving_log = open(server.key_file).read().strip()   # the file holds it, by design
    server.stop()
    assert serving_log == secret
    assert cfg_path.read_text() == saved_before
    assert secret not in cfg_path.read_text()
    printed = capsys.readouterr()
    assert secret not in printed.out and secret not in printed.err


def test_published_binding_carries_the_verified_run(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 4096, 'port': 9933})
    server = _start_with(monkeypatch, config, _FakeProcess(pid=5151), overhead=37)
    server.start()
    try:
        binding = local_launcher.binding_environment(server)
        # Captured while the run is live: stop() clears the bearer, which is the
        # point (a per-run credential must not outlive its run).
        run_secret = server.secret
    finally:
        server.stop()
    assert binding['WB_LOCAL_BASE_URL'] == 'http://127.0.0.1:9933'
    assert binding['WB_LOCAL_MODEL'] == ALIAS
    assert binding['WB_LOCAL_CONTEXT'] == '4096'
    assert binding['WB_LOCAL_SERVER_PID'] == '5151'
    assert binding['WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS'] == '37'
    assert binding['WB_LOCAL_WEIGHTS_SHA256'] == hashlib.sha256(
        Path(weights).read_bytes()).hexdigest()
    assert binding['WB_LOCAL_RUN_ID']
    # What the server reported about itself, published next to what was launched.
    assert binding['WB_LOCAL_OBSERVED_ALIAS'] == ALIAS
    assert binding['WB_LOCAL_OBSERVED_CTX'] == '4096'
    assert binding['WB_LOCAL_OBSERVED_CTX_TRAIN'] == '262144'
    assert run_secret and binding['WB_LOCAL_API_KEY'] == run_secret
    assert server.secret is None, 'the bearer must not survive the run'


def test_a_run_whose_identity_was_never_observed_publishes_no_binding(files, monkeypatch):
    """B3: intent is not evidence. No observed identity, no usable binding."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = _start_with(monkeypatch, config, _FakeProcess())
    server.start()
    try:
        # Simulates a launch path that skipped the identity report.
        server.observed_identity = None
        assert local_launcher.binding_environment(server) == {}
    finally:
        server.stop()


def test_binding_is_empty_without_a_launch(files):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    assert local_launcher.binding_environment(None) == {}
    # Constructed but never started: no run, so no binding and no secret.
    assert local_launcher.binding_environment(local_launcher.LocalServer(config)) == {}


# --- readiness: identity, not just a listener -----------------------------

def test_readiness_failure_stops_the_owned_child(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    fake = _FakeProcess()
    server = _start_with(monkeypatch, config, fake, ready=False)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.start()
    assert 'did not become ready' in str(caught.value)
    assert fake.terminated is True, 'a timed-out start must stop the child it spawned'


def test_a_child_that_exits_during_startup_is_reported_with_its_code(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})

    class DeadProcess(_FakeProcess):
        returncode = 3

        def poll(self):
            return 3

        def terminate(self):
            pytest.fail('terminated an already-exited child')

    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: DeadProcess())
    monkeypatch.setattr(local_launcher, 'POLL_SECONDS', 0.01)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        local_launcher.LocalServer(config).start()
    assert 'exited during startup with code 3' in str(caught.value)


def test_readiness_passes_the_launched_identity_and_secret(files, monkeypatch):
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    seen = []
    server = _start_with(monkeypatch, config, _FakeProcess())
    monkeypatch.setattr(local_launcher, '_serves_verified_identity',
                        lambda origin, alias, secret, context, deadline=None: seen.append(
                            (origin, alias, secret, context, deadline)) or _observed(config))
    server.start()
    try:
        # The server's own reported identity is what is checked, with this run's
        # bearer — never a bare "something is listening".
        assert seen[0][0] == 'http://127.0.0.1:9931'
        assert seen[0][1] == ALIAS
        assert seen[0][2] == server.secret
        assert seen[0][3] == 8192
        # One absolute deadline, threaded in: the probe gets no time budget of
        # its own, so the worst case is the start budget rather than the sum.
        assert seen[0][4] is not None
    finally:
        server.stop()


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit, RuntimeError])
def test_interrupt_during_readiness_stops_the_child(files, monkeypatch, interrupt):
    """SIGINT/SIGTERM (the handler raises KeyboardInterrupt) must not orphan it."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    fake = _FakeProcess()
    server = _start_with(monkeypatch, config, fake, probe_raises=interrupt)
    with pytest.raises(interrupt):
        server.start()
    assert fake.terminated is True, 'an interrupted start must stop its child'
    assert server.key_file is None


def test_interrupt_cleanup_failure_does_not_replace_the_original_error(files, monkeypatch, capsys):
    """A cleanup that itself fails must not mask the interrupt."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    fake = _FakeProcess()
    server = _start_with(monkeypatch, config, fake, probe_raises=KeyboardInterrupt)

    def broken_stop():
        raise RuntimeError('terminate exploded')
    monkeypatch.setattr(server, 'stop', broken_stop)
    with pytest.raises(KeyboardInterrupt):
        server.start()
    assert 'cleanup failed' in capsys.readouterr().err.lower()


def test_stop_reports_failure_instead_of_swallowing_it(monkeypatch):
    """A child that cannot be stopped is a condition callers must be able to see."""
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})

    class StuckProcess(_FakeProcess):
        def terminate(self):
            raise OSError('no such process')

    server = local_launcher.LocalServer(config)
    server.process = StuckProcess()
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.stop()
    assert 'could not be asked to stop' in str(caught.value)


def test_stop_never_touches_a_child_it_did_not_start(monkeypatch):
    """An unstarted launcher has no child; stop() must be a silent no-op."""
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = local_launcher.LocalServer(config)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned'))
    server.stop()  # no process was ever created
    assert server.process is None


def test_stop_preserving_reports_without_raising(monkeypatch, capsys):
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})

    class StuckProcess(_FakeProcess):
        def terminate(self):
            raise OSError('no such process')

    server = local_launcher.LocalServer(config)
    server.process = StuckProcess()
    message = local_launcher.stop_preserving(server)
    assert message and 'did not stop cleanly' in message
    assert 'did not stop cleanly' in capsys.readouterr().err


def test_stop_preserving_is_silent_on_success(capsys):
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    server = local_launcher.LocalServer(config)
    server.process = _FakeProcess()
    assert local_launcher.stop_preserving(server) is None
    assert capsys.readouterr().err == ''


def test_stop_preserving_tolerates_no_child(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned'))
    assert local_launcher.stop_preserving(None) is None


# --- F1: the watchdog is disarmed only once the child is confirmed reaped --

class _SurvivesEverySignal(_FakeProcess):
    """A child that ignores SIGTERM and SIGKILL: still running after both."""

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired('model', timeout)

    def poll(self):
        return None


def test_stop_keeps_the_watchdog_armed_for_a_child_that_will_not_exit(
        monkeypatch, capsys):
    """Disarming here would leave a live model unwatched — the case the reaper covers."""
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    armed = []

    class Watchdog:
        pid = 31337

        def poll(self):
            return None

    def arm(model_pid, backend_pid=None, poll_seconds=None, log_path=None):
        watch = local_launcher.local_reaper.ReaperWatch(Watchdog(), model_pid, 1)
        armed.append(watch)
        return watch

    disarmed = []
    monkeypatch.setattr(local_launcher.local_reaper, 'arm', arm)
    monkeypatch.setattr(local_launcher.local_reaper, 'disarm',
                        lambda watch: disarmed.append(watch) or None)
    server = local_launcher.LocalServer(config, startup_timeout=5.0)
    server.process = _SurvivesEverySignal()
    server.reaper = arm(server.process.pid)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.stop(grace=0.01, kill_wait=0.01)
    assert 'did not exit after being stopped' in str(caught.value)
    # F1: never disarmed, and the arming object was not dropped either.
    assert disarmed == [], 'a child that survived both signals must stay watched'
    assert server.reaper is not None
    message = capsys.readouterr().err
    assert 'stays armed' in message
    assert 'stays armed' in str(caught.value)


def test_stop_disarms_once_poll_confirms_the_child_is_reaped(monkeypatch):
    """The normal path still disarms: confirmation, not the attempt, is the trigger."""
    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    disarmed = []
    monkeypatch.setattr(local_launcher.local_reaper, 'disarm',
                        lambda watch: disarmed.append(watch) or None)
    server = local_launcher.LocalServer(config)
    child = _FakeProcess()
    server.process = child
    watch = local_launcher.local_reaper.ReaperWatch(object(), child.pid, 1)
    server.reaper = watch
    server.stop()
    assert child.terminated is True
    assert disarmed == [watch]
    assert server.reaper is None


def test_stop_is_not_fooled_by_wait_returning_for_a_live_child(monkeypatch):
    """``poll()`` is the verdict, so a lying ``wait`` cannot disarm the watchdog."""

    class Sly(_FakeProcess):
        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0            # claims an exit that never happened

        def poll(self):
            return None         # the child is still there

    config = local_config._validate({'runtime_executable': EXECUTABLE, 'model_weights': WEIGHTS,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    disarmed = []
    monkeypatch.setattr(local_launcher.local_reaper, 'disarm',
                        lambda watch: disarmed.append(watch) or None)
    server = local_launcher.LocalServer(config)
    server.process = Sly()
    watch = local_launcher.local_reaper.ReaperWatch(object(), 4242, 1)
    server.reaper = watch
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.stop(grace=0.01, kill_wait=0.01)
    assert 'still running after being stopped' in str(caught.value)
    assert disarmed == []
    assert server.reaper is watch


# --- F2: one absolute deadline covers the whole start ---------------------

class _Clock:
    """A monotonic clock this test advances explicitly, so budgets are exact."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(local_launcher.time, 'monotonic', fake.monotonic)
    monkeypatch.setattr(local_launcher.time, 'sleep', fake.sleep)
    monkeypatch.setattr(local_launcher, 'PROBE_TIMEOUT', 5.0)
    monkeypatch.setattr(local_launcher, 'POLL_SECONDS', 0.25)
    monkeypatch.setattr(local_launcher, 'STARTUP_TIMEOUT', 90.0)
    _watchdog(monkeypatch)
    return fake


def _watchdog(monkeypatch):
    """A watchdog stand-in that is alive and stops on request."""

    class Watchdog:
        pid = 31337
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(local_launcher.local_reaper, 'arm',
                        lambda *a, **k: local_launcher.local_reaper.ReaperWatch(
                            Watchdog(), 4242, 1))
    monkeypatch.setattr(local_launcher.local_reaper, 'disarm', lambda watch: None)


def test_probe_timeout_is_capped_by_what_is_left_of_the_deadline(clock):
    """min(PROBE_TIMEOUT, remaining) — not PROBE_TIMEOUT per probe."""
    deadline = clock.now + 1.5
    assert local_launcher._probe_timeout(deadline) == pytest.approx(1.5)
    assert local_launcher._probe_timeout(clock.now + 60.0) == 5.0
    # An expired deadline grants nothing at all: the caller must stop, not probe.
    assert local_launcher._probe_timeout(clock.now - 0.001) == 0.0


def test_every_readiness_probe_shares_one_absolute_deadline(clock, files, monkeypatch):
    """Three overrunning probes used to add 3xPROBE_TIMEOUT; now they share one budget."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    seen = []

    def probe(*a, **k):
        deadline = k.get('deadline')
        seen.append(deadline)
        # Each probe consumes its whole allowance plus the 2.51 s of slack the
        # reviewer measured, and answers "not ready".
        clock.now += local_launcher._probe_timeout(deadline) + 2.51
        return None

    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: _FakeProcess())
    monkeypatch.setattr(local_launcher, '_serves_verified_identity', probe)
    server = local_launcher.LocalServer(config, startup_timeout=90.0)
    begun = clock.now
    with pytest.raises(local_launcher.LocalLaunchError):
        server.start()
    elapsed = clock.now - begun
    # Every iteration was handed the same absolute end, not a fresh timeout each.
    assert seen and all(d == seen[0] for d in seen), 'one deadline, threaded'
    # The worst case is therefore one budget plus at most ONE probe's slack; the
    # old additive behaviour gave 90 + 3*5 + 3*2.51.
    assert elapsed <= 90.0 + 2.51, 'start took %.2f s, over the one-budget ceiling' % elapsed
    assert len(seen) <= 40, 'probes must be spaced by POLL_SECONDS, not spun'


def test_a_slow_measurement_respects_the_start_deadline(clock, files, monkeypatch):
    """The measurement had no deadline at all, so it could add 3 full probes."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    ready = {'model_alias': ALIAS, 'n_ctx': 8192,
             'n_ctx_train': 262144, 'total_slots': 1}
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: _FakeProcess())
    monkeypatch.setattr(local_launcher, '_serves_verified_identity', lambda *a, **k: ready)
    # The server answers /apply-template and /tokenize only after the deadline.
    calls = []

    def slow_post(self, deadline=None):
        calls.append(deadline)
        clock.now += 5.0 + 2.00   # one probe timeout plus the measured 2.00 s
        raise local_launcher.LocalLaunchError(
            'Measuring the local model chat template did not finish within the allowed '
            'startup time; local mode is unavailable for this session.')

    monkeypatch.setattr(local_launcher.LocalServer, 'measure_template_overhead', slow_post)
    server = local_launcher.LocalServer(config, startup_timeout=90.0)
    begun = clock.now
    with pytest.raises(local_launcher.LocalLaunchError):
        server.start()
    elapsed = clock.now - begun
    assert calls == [pytest.approx(begun + 90.0)], 'the measurement is handed the start deadline'
    assert elapsed <= 90.0 + 2.00, 'measurement overran: %.2f s' % elapsed


def test_measurement_granted_no_time_fails_before_sending_anything(clock, files, monkeypatch):
    """An already-expired deadline means no request at all, not three 5 s attempts."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    opened = []
    monkeypatch.setattr(urllib.request, 'build_opener',
                        lambda *a, **k: opened.append(True) or pytest.fail('opened'))
    server = local_launcher.LocalServer(config)
    server.secret = SECRET
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.measure_template_overhead(clock.now - 1.0)
    assert 'did not finish within the allowed startup time' in str(caught.value)
    assert opened == []


def test_an_expired_deadline_stops_the_weights_digest_before_spawning(clock, files,
                                                                     monkeypatch):
    """The reviewer measured a 4.11 s overrun here: the digest is inside the budget."""
    exe, weights = files
    config = local_config._validate({'runtime_executable': exe, 'model_weights': weights,
                                     'alias': ALIAS, 'context': 8192, 'port': 9931})
    monkeypatch.setattr(local_launcher, 'port_in_use', lambda port: False)
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('spawned after the deadline expired'))
    server = local_launcher.LocalServer(config, startup_timeout=90.0)
    begun = clock.now
    # The clock advances past the deadline while the file is being read.
    real_open = open

    def slow_open(path, *a, **k):
        clock.now += 90.0 + 4.11
        return real_open(path, *a, **k)

    monkeypatch.setattr('builtins.open', slow_open)
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.start()
    assert 'Verifying the local model weights did not finish within the allowed startup ' \
           'time' in str(caught.value)
    # No spawn happened, so no overrun was inherited by a running child.
    assert server.process is None


def test_the_worst_case_start_fits_the_desktop_ready_timeout(clock):
    """Budget arithmetic: the whole failed-start chain must fit the 90 s window.

    The chain is the port probe, the shared start deadline, the launcher's own
    stop of the child it abandoned, and the backend's failure-path stop.
    """
    import importlib.util
    backend = ROOT / 'packaging' / 'backend_server.py'
    spec = importlib.util.spec_from_file_location('wb_backend_budget', backend)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    failure_stop = (local_launcher.FAILURE_STOP_GRACE_SECONDS
                    + local_launcher.FAILURE_KILL_WAIT_SECONDS)
    port_probe = 1.0
    worst = module.LOCAL_STARTUP_TIMEOUT + port_probe + 2 * failure_stop
    assert module.LOCAL_STARTUP_TIMEOUT <= 90.0
    assert worst < 90.0, 'start plus failure stop does not fit: %.1f s' % worst
    # And enough headroom that the API, the ready file and the JSON write fit.
    assert 90.0 - worst >= 25.0, 'too little slack for the rest of startup: %.1f s' % (90.0 - worst)


def test_an_abandoned_start_stops_the_child_with_the_failure_budget(monkeypatch):
    """The launcher's own cleanup after a failed start uses the short budget."""
    server = local_launcher.LocalServer({'base_url': 'http://127.0.0.1:9931'})
    seen = {}

    def fake_stop(*, grace=None, kill_wait=None):
        seen.update(grace=grace, kill_wait=kill_wait)

    monkeypatch.setattr(server, 'stop', fake_stop)
    server._abandon_after_failed_start()
    assert seen == {'grace': local_launcher.FAILURE_STOP_GRACE_SECONDS,
                    'kill_wait': local_launcher.FAILURE_KILL_WAIT_SECONDS}


# --- a real loopback fake server: authentication and identity -------------

class _FakeLlama:
    """A loopback stand-in for llama.cpp's documented endpoints.

    Every endpoint requires ``Authorization: Bearer <secret>``; an insufficient
    or wrong bearer is answered 401, exactly as the real server does. ``/props``
    reports ``model_alias`` and ``default_generation_settings.n_ctx``.
    ``/apply-template`` wraps the user message in a fake ChatML envelope and
    ``/tokenize`` counts whitespace-separated pieces plus one per special marker,
    so the measured overhead is a known number rather than a guess.
    """

    def __init__(self, secret, alias, n_ctx):
        self.secret = secret
        self.alias = alias
        self.n_ctx = n_ctx
        self.requests = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                return self.headers.get('Authorization') == 'Bearer ' + fake.secret

            def do_GET(self):
                fake.requests.append(('GET', self.path,
                                      self.headers.get('Authorization')))
                if not self._authorized():
                    return self._reply(401, {'error': 'unauthorized'})
                if self.path == '/v1/models':
                    return self._reply(200, {'data': [{'id': fake.alias}]})
                if self.path == '/props':
                    return self._reply(200, {'model_alias': fake.alias,
                                             'n_ctx_train': 262144,
                                             'total_slots': 1,
                                             'default_generation_settings': {
                                                 'n_ctx': fake.n_ctx}})
                return self._reply(404, {'error': 'not found'})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length) or b'{}')
                fake.requests.append(('POST', self.path,
                                      self.headers.get('Authorization')))
                if not self._authorized():
                    return self._reply(401, {'error': 'unauthorized'})
                if self.path == '/apply-template':
                    content = payload['messages'][0]['content']
                    return self._reply(200, {
                        'prompt': '<|im_start|>system\nhelper<|im_end|>\n'
                                  '<|im_start|>user\n' + content +
                                  '<|im_end|>\n<|im_start|>assistant\n'})
                if self.path == '/tokenize':
                    return self._reply(200, {
                        'tokens': ['<|im_start|>', '<|im_end|>', '<|im_start|>']
                                  + payload['content'].split()})
                return self._reply(404, {'error': 'not found'})

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_server():
    servers = []

    def start(secret=SECRET, alias=ALIAS, n_ctx=8192):
        server = _FakeLlama(secret, alias, n_ctx)
        servers.append(server)
        return server
    yield start
    for server in servers:
        server.close()


def test_verified_identity_requires_the_bearer(fake_server):
    fake = fake_server()
    identity = local_launcher._serves_verified_identity(
        fake.origin, ALIAS, SECRET, 8192)
    # On success it reports what the server said about itself, so the binding can
    # carry an observed identity rather than the launch intent.
    assert identity == {'model_alias': ALIAS, 'n_ctx': 8192,
                        'n_ctx_train': 262144, 'total_slots': 1}
    # No bearer: the server refuses, so readiness fails even though it listens.
    assert local_launcher._serves_verified_identity(fake.origin, ALIAS, None, 8192) is None
    assert local_launcher._serves_verified_identity(
        fake.origin, ALIAS, 'the-wrong-secret', 8192) is None
    # And every request that was attempted carried the header (or was refused).
    assert all(auth in (None, 'Bearer ' + SECRET, 'Bearer the-wrong-secret')
               for _, _, auth in fake.requests)


def test_verified_identity_requires_the_launched_context(fake_server):
    """A server running a different window is not the one that was launched."""
    fake = fake_server(n_ctx=4096)
    assert local_launcher._serves_verified_identity(
        fake.origin, ALIAS, SECRET, 8192) is None
    assert local_launcher._serves_verified_identity(
        fake.origin, ALIAS, SECRET, 4096) is not None


def test_verified_identity_requires_the_launched_alias(fake_server):
    fake = fake_server(alias='some-other-model')
    assert local_launcher._serves_verified_identity(
        fake.origin, ALIAS, SECRET, 8192) is None


def test_verified_identity_refuses_a_non_loopback_origin(monkeypatch):
    """Readiness never contacts another host, even if the config says to."""
    opened = []
    monkeypatch.setattr(urllib.request, 'build_opener',
                        lambda *a, **k: opened.append(True) or pytest.fail('opened'))
    assert local_launcher._serves_verified_identity(
        'http://example.com:9931', ALIAS, SECRET, 8192) is None
    assert opened == []


def test_verified_identity_without_a_secret_fails_closed(fake_server):
    fake = fake_server()
    assert local_launcher._serves_verified_identity(fake.origin, ALIAS, '', 8192) is None


@pytest.mark.parametrize('props', [
    {},                                     # nothing reported at all
    {'total_slots': 1},                     # slots but no alias/context
    {'model_alias': ALIAS},                 # alias but no context
    {'model_alias': ALIAS, 'total_slots': 1},                             # no n_ctx
    {'default_generation_settings': {'n_ctx': 8192}, 'total_slots': 1},  # no alias
])
def test_verified_identity_requires_presence_equality(fake_server, props):
    """An absent field is not agreement, so a partial /props is a refusal.

    The earlier defect accepted any /props whose reported alias merely differed
    from the expected one when present; a server reporting nothing therefore
    satisfied readiness. The two identity fields — alias and ``n_ctx`` — must be
    present AND equal. ``total_slots`` is not one of them (this runtime uses
    AUTO slots with the full ``-c`` per request), so its absence alone is never
    a failure; here it is absent together with alias or context.
    """
    fake = fake_server()
    original = local_launcher._read_json

    def patched(origin, path, headers, timeout=None):
        if path == local_runtime.PROPS_PATH:
            return dict(props)
        return original(origin, path, headers, timeout)

    local_launcher._read_json = patched
    try:
        assert local_launcher._serves_verified_identity(
            fake.origin, ALIAS, SECRET, 8192) is None
    finally:
        local_launcher._read_json = original


def test_verified_identity_accepts_multiple_slots_and_only_records_them(fake_server):
    """Slot count is recorded, never a requirement.

    The installed runtime reports AUTO slots with ``kv_unified=true`` and the
    full declared ``-c`` genuine per request, so four slots do not divide the
    window. A single-slot requirement would refuse a working launch for no
    reason, so it is not part of the identity check.
    """
    fake = fake_server()
    original = local_launcher._read_json

    def patched(origin, path, headers, timeout=None):
        if path == local_runtime.PROPS_PATH:
            return {'model_alias': ALIAS, 'total_slots': 4,
                    'default_generation_settings': {'n_ctx': 8192}}
        return original(origin, path, headers, timeout)

    local_launcher._read_json = patched
    try:
        identity = local_launcher._serves_verified_identity(
            fake.origin, ALIAS, SECRET, 8192)
        assert identity is not None
        assert identity['model_alias'] == ALIAS
        assert identity['n_ctx'] == 8192
        # Recorded for the audit trail, but not a pass/fail condition.
        assert identity['total_slots'] == 4
        # A /props that omits total_slots entirely is equally acceptable.
        assert local_launcher._serves_verified_identity(
            fake.origin, ALIAS, SECRET, 8192) is not None
    finally:
        local_launcher._read_json = original


def test_verified_identity_does_not_require_total_slots(fake_server):
    """An absent total_slots is not an identity failure: alias + n_ctx decide."""
    fake = fake_server()
    original = local_launcher._read_json

    def patched(origin, path, headers, timeout=None):
        if path == local_runtime.PROPS_PATH:
            return {'model_alias': ALIAS,
                    'default_generation_settings': {'n_ctx': 8192}}
        return original(origin, path, headers, timeout)

    local_launcher._read_json = patched
    try:
        identity = local_launcher._serves_verified_identity(
            fake.origin, ALIAS, SECRET, 8192)
        assert identity is not None
        assert identity['total_slots'] is None
    finally:
        local_launcher._read_json = original


def test_readiness_requires_the_bearer_to_be_load_bearing():
    """A listener that answers unauthenticated requests is not our runtime.

    This fake ignores Authorization entirely and answers 200 to everything — an
    open listener. Readiness must refuse it, because a server that does not
    demand the per-run secret proves nothing about who is talking to it.
    """
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            body = json.dumps(
                {'data': [{'id': ALIAS}]} if self.path == '/v1/models' else
                {'model_alias': ALIAS, 'total_slots': 1,
                 'default_generation_settings': {'n_ctx': 8192}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    origin = 'http://127.0.0.1:%d' % server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert local_launcher._serves_verified_identity(
            origin, ALIAS, SECRET, 8192) is None
        # And the negative really was made: one request without a bearer.
        assert any(auth is None for _, auth in requests), requests
    finally:
        server.shutdown()
        server.server_close()


def test_anonymous_refusal_requires_401_or_403(fake_server):
    """Only 401/403 proves the credential is required; anything else does not."""
    fake = fake_server()
    assert local_launcher._anonymous_request_refused(fake.origin) is True
    # A dead origin is not proof either (no listener, no evidence).
    assert local_launcher._anonymous_request_refused('http://127.0.0.1:1') is False
    # A non-loopback origin is refused before any request is made.
    assert local_launcher._anonymous_request_refused('http://example.com:9931') is False


def test_measure_template_overhead_uses_the_real_template(fake_server):
    """The overhead is measured, not a constant: it comes from the server."""
    fake = fake_server()
    config = local_config._validate({'runtime_executable': EXECUTABLE,
                                     'model_weights': WEIGHTS, 'alias': ALIAS,
                                     'context': 8192, 'port': 9931})
    config['base_url'] = fake.origin
    server = local_launcher.LocalServer(config)
    server.secret = SECRET
    overhead = server.measure_template_overhead()
    # The fake template adds a system turn and assistant prefix around the probe
    # word; its rendered form tokenizes to more pieces than the bare word.
    assert overhead > 0
    assert overhead == server.template_overhead_tokens
    # Only the fixed synthetic probe string was sent: no researcher text.
    posts = [path for method, path, _ in fake.requests if method == 'POST']
    assert '/apply-template' in posts
    assert '/tokenize' in posts


def test_measurement_failure_is_a_launch_error(fake_server):
    fake = fake_server(secret='not-the-run-secret')
    config = local_config._validate({'runtime_executable': EXECUTABLE,
                                     'model_weights': WEIGHTS, 'alias': ALIAS,
                                     'context': 8192, 'port': 9931})
    config['base_url'] = fake.origin
    server = local_launcher.LocalServer(config)
    server.secret = SECRET
    with pytest.raises(local_launcher.LocalLaunchError):
        server.measure_template_overhead()


def test_measurement_refuses_a_non_loopback_origin():
    config = local_config._validate({'runtime_executable': EXECUTABLE,
                                     'model_weights': WEIGHTS, 'alias': ALIAS,
                                     'context': 8192, 'port': 9931})
    config['base_url'] = 'http://example.com:9931'
    server = local_launcher.LocalServer(config)
    server.secret = SECRET
    with pytest.raises(local_launcher.LocalLaunchError) as caught:
        server.measure_template_overhead()
    assert 'not loopback' in str(caught.value)


def test_probe_refuses_to_follow_redirects(monkeypatch):
    """urllib follows redirects by default; a redirect must not be honoured."""
    handler = local_launcher._RefuseRedirect()
    with pytest.raises(urllib.error.HTTPError) as caught:
        handler.redirect_request(
            urllib.request.Request('http://127.0.0.1:9931/v1/models'), None,
            302, 'Found', {}, 'http://example.com/steal')
    assert caught.value.code == 302
    assert 'refuses redirect' in str(caught.value)


def test_probe_returns_false_against_a_real_redirecting_loopback_server():
    """End-to-end: a server that redirects must not produce readiness."""
    target_hits = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # the redirect destination: must never be reached
            target_hits.append(self.path)
            payload = json.dumps({'data': [{'id': ALIAS}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    class Redirector(http.server.BaseHTTPRequestHandler):
        target = ''

        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', self.target)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *a):
            pass

    target = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    Redirector.target = 'http://127.0.0.1:%d/v1/models' % target.server_address[1]
    redirector = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirector)
    threading.Thread(target=redirector.serve_forever, daemon=True).start()
    try:
        origin = 'http://127.0.0.1:%d' % redirector.server_address[1]
        assert local_launcher._serves_verified_identity(origin, ALIAS, SECRET, 8192) is None
        assert target_hits == [], 'the redirect target must not be contacted'
    finally:
        redirector.shutdown()
        target.shutdown()
        redirector.server_close()
        target.server_close()


def test_probe_rejects_an_oversized_body(monkeypatch):
    class Response:
        status = 200

        def read(self, amount):
            return b'x' * amount

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout=None):
            return Response()

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **k: Opener())
    assert local_launcher._serves_verified_identity(
        'http://127.0.0.1:9931', ALIAS, SECRET, 8192) is None


@pytest.mark.parametrize('body', [b'[]', b'{}', b'{"data":{}}', b'{"data":[1]}',
                                  b'not json', b'{"data":[{"id":"other-model"}]}'])
def test_probe_requires_the_configured_alias_in_a_data_list(monkeypatch, body):
    class Response:
        status = 200

        def read(self, amount):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout=None):
            return Response()

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **k: Opener())
    assert local_launcher._serves_verified_identity(
        'http://127.0.0.1:9931', ALIAS, SECRET, 8192) is None
