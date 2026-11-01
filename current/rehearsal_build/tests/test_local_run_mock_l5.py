"""L5: ``run_mock.py`` supervisor ordering and cleanup on an interrupted start.

The supervisor is executed as a real subprocess, but with a stub ``app.ai``
package ahead of the real one on ``PYTHONPATH``: the stub's ``LocalServer``
records that it started and whether it was asked to stop instead of loading a
model. No llama-server is launched and no port is bound — the preflight ports are
also stubbed out. The assertions are about *ordering and cleanup* only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: A stand-in `app.ai` whose LocalServer fails readiness the way an interrupt
#: during model load does, and whose LocalServer records stop().
STUB_AI = '''
import pathlib, sys
RECORD = pathlib.Path(sys.argv[0]).with_name('stub_record.txt')
def note(text):
    with open(RECORD, 'a') as fh:
        fh.write(text + '\\n')
class LocalLaunchError(RuntimeError):
    pass
class _Server:
    def __init__(self, config, log_path=None, app_origin=None):
        note('constructed')
    def start(self):
        note('started')
        raise LocalLaunchError('simulated interrupt during model load')
    def stop(self):
        note('stopped')
class local_config:
    @staticmethod
    def read():
        return {'runtime_executable': '/nonexistent/llama-server',
                'model_weights': '/nonexistent/m.gguf', 'alias': 'a',
                'context': 8192, 'port': 9, 'base_url': 'http://127.0.0.1:9'}
class local_launcher:
    LocalLaunchError = LocalLaunchError
    LocalServer = _Server
    @staticmethod
    def stop_preserving(server, **kwargs):
        try:
            server.stop()
        except BaseException as exc:
            note('cleanup_error:' + str(exc))
'''


@pytest.fixture
def supervisor_workspace(tmp_path, monkeypatch):
    """A throwaway copy of run_mock.py with a stub app.ai and stub ports."""
    stub = tmp_path / 'app' / 'ai'
    stub.mkdir(parents=True)
    (tmp_path / 'app' / '__init__.py').write_text('')
    (stub / '__init__.py').write_text('')
    (stub / 'local_stub.py').write_text(STUB_AI)
    (stub / 'local_config.py').write_text(
        'import pathlib, sys\n'
        'RECORD = pathlib.Path(__file__).with_name("stub_record.txt")\n'
        'def read():\n'
        '    return {"runtime_executable": "/nonexistent/llama-server",\n'
        '            "model_weights": "/nonexistent/m.gguf", "alias": "a",\n'
        '            "context": 8192, "port": 9, "base_url": "http://127.0.0.1:9"}\n')
    (stub / 'local_launcher.py').write_text(
        'import pathlib\n'
        'RECORD = pathlib.Path(__file__).with_name("stub_record.txt")\n'
        'def note(text):\n'
        '    with open(RECORD, "a") as fh:\n'
        '        fh.write(text + "\\n")\n'
        'class LocalLaunchError(RuntimeError):\n'
        '    pass\n'
        'class LocalServer:\n'
        '    def __init__(self, config, log_path=None, app_origin=None):\n'
        '        note("constructed")\n'
        '    def start(self):\n'
        '        note("started")\n'
        '        raise LocalLaunchError("simulated failure during model load")\n'
        '    def stop(self):\n'
        '        note("stopped")\n'
        'def stop_preserving(server, **kwargs):\n'
        '    try:\n'
        '        server.stop()\n'
        '    except BaseException as exc:\n'
        '        note("cleanup_error:" + str(exc))\n')
    return tmp_path, stub


def _run_supervisor(tmp_path, stub, *extra):
    """Run run_mock.py with the stub package first on sys.path."""
    source = (ROOT / 'run_mock.py').read_text()
    # Only the port preflight is neutralised; the ordering and cleanup code is
    # the real file, unmodified.
    source = source.replace(
        "for port in ports:\n",
        "for port in ():\n")
    runner = tmp_path / 'run_mock_under_test.py'
    runner.write_text(source)
    env = {**os.environ, 'PYTHONPATH': str(tmp_path), 'WB_LOCAL_CONFIG': str(tmp_path / 'cfg.json')}
    return subprocess.run([sys.executable, str(runner), '--local-runtime', *extra],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60)


def test_failed_local_start_reports_and_never_leaves_the_rehearsal_running(supervisor_workspace):
    tmp_path, stub = supervisor_workspace
    result = _run_supervisor(tmp_path, stub)
    assert result.returncode != 0, 'a failed local start must not report success'
    assert 'Local runtime did not start' in result.stderr
    assert 'simulated failure during model load' in result.stderr
    record = (stub / 'stub_record.txt').read_text().split()
    # It must be constructed, started, and then stopped by the supervisor's
    # cleanup: an interrupted start may not leave the model child behind.
    assert record == ['constructed', 'started', 'stopped'], record


def test_cleanup_failure_is_reported_on_stderr(supervisor_workspace):
    """A failing stop() is reported, not silently dropped."""
    tmp_path, stub = supervisor_workspace
    (stub / 'local_launcher.py').write_text(
        'import pathlib\n'
        'RECORD = pathlib.Path(__file__).with_name("stub_record.txt")\n'
        'def note(text):\n'
        '    with open(RECORD, "a") as fh:\n'
        '        fh.write(text + "\\n")\n'
        'class LocalLaunchError(RuntimeError):\n'
        '    pass\n'
        'class LocalServer:\n'
        '    def __init__(self, config, log_path=None, app_origin=None):\n'
        '        note("constructed")\n'
        '    def start(self):\n'
        '        raise LocalLaunchError("simulated failure")\n'
        'def stop_preserving(server, **kwargs):\n'
        '    raise RuntimeError("stop exploded")\n')
    result = _run_supervisor(tmp_path, stub)
    assert result.returncode != 0
    # The original failure reason survives; the cleanup problem is separate.
    assert 'simulated failure' in result.stderr
    assert 'cleanup could not run' in result.stderr


def test_default_invocation_starts_no_model_process(tmp_path):
    """Without --local-runtime the rehearsal stays mock-only by contract."""
    source = (ROOT / 'run_mock.py').read_text()
    assert "'--local-runtime' in sys.argv" in source
    # No unconditional LocalServer/LocalServer.start in the module body.
    assert 'local_wanted=' in source


#: A stand-in launcher whose start() succeeds, so the supervisor reaches the point
#: where it hands the per-run binding to its children.
STUB_SUCCESS_LAUNCHER = '''
import pathlib
RECORD = pathlib.Path(__file__).with_name("stub_record.txt")
SECRET = "stub-per-run-secret-value"
def note(text):
    with open(RECORD, "a") as fh:
        fh.write(text + "\\n")
class LocalLaunchError(RuntimeError):
    pass
class LocalServer:
    def __init__(self, config, log_path=None, app_origin=None):
        self.config = config
        self.secret = SECRET
        self.run_id = "stub-run"
        self.weights_sha256 = "c" * 64
        self.template_overhead_tokens = 17
        class _P:
            pid = 9191
            def poll(self):
                return None
        self.process = _P()
    def start(self):
        note("started")
    def stop(self):
        note("stopped")
def binding_environment(server):
    return {"WB_LOCAL_BASE_URL": server.config["base_url"],
            "WB_LOCAL_MODEL": server.config["alias"],
            "WB_LOCAL_CONTEXT": str(server.config["context"]),
            "WB_LOCAL_RUN_ID": server.run_id,
            "WB_LOCAL_SERVER_PID": "9191",
            "WB_LOCAL_WEIGHTS_SHA256": server.weights_sha256,
            "WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS": str(server.template_overhead_tokens),
            "WB_LOCAL_OBSERVED_ALIAS": server.config["alias"],
            "WB_LOCAL_OBSERVED_CTX": str(server.config["context"]),
            "WB_LOCAL_OBSERVED_CTX_TRAIN": "262144",
            "WB_LOCAL_API_KEY": server.secret}
def stop_preserving(server, **kwargs):
    server.stop()
'''


def test_the_run_secret_reaches_only_the_api_child(supervisor_workspace):
    """B2: the shared environment spawns the static server and the shell.

    The per-run bearer must be visible to exactly one child — the API process
    that authenticates the model calls — and never to the shared environment the
    static file server and the desktop shell are started from.
    """
    tmp_path, stub = supervisor_workspace
    (stub / 'local_launcher.py').write_text(STUB_SUCCESS_LAUNCHER)
    source = (ROOT / 'run_mock.py').read_text()
    source = source.replace("for port in ports:\n", "for port in ():\n")
    record_file = tmp_path / 'envs.json'
    # Replace the spawn loop with a recorder, so no child process is started and
    # the environment split itself is what is measured.
    source = source.replace(
        "    for cmd,cwd,wants_api_env in commands:\n"
        "        processes.append(subprocess.Popen(cmd,cwd=cwd,env=api_env if wants_api_env else env))\n",
        "    import json as _json\n"
        "    _seen = []\n"
        "    for cmd,cwd,wants_api_env in commands:\n"
        "        _e = api_env if wants_api_env else env\n"
        "        _seen.append({'wants_api_env': wants_api_env,\n"
        "                      'has_secret': 'WB_LOCAL_API_KEY' in _e,\n"
        "                      'secret_value': _e.get('WB_LOCAL_API_KEY'),\n"
        "                      'shared_has_secret': 'WB_LOCAL_API_KEY' in env})\n"
        "    open(%r, 'w').write(_json.dumps(_seen))\n"
        "    raise SystemExit(0)\n" % str(record_file))
    runner = tmp_path / 'run_mock_env_under_test.py'
    runner.write_text(source)
    env = {**os.environ, 'PYTHONPATH': str(tmp_path),
           'WB_LOCAL_CONFIG': str(tmp_path / 'cfg.json')}
    (stub / 'stub_record.txt').unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(runner), '--local-runtime'],
                            cwd=str(tmp_path), env=env, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    seen = json.loads(record_file.read_text())
    # Exactly one child is the API process, and it is the only one that sees the
    # secret; the shared environment every other child inherits never has it.
    api = [row for row in seen if row['wants_api_env']]
    others = [row for row in seen if not row['wants_api_env']]
    assert len(api) == 1 and api[0]['has_secret'] is True
    assert api[0]['secret_value'] == 'stub-per-run-secret-value'
    assert others and all(row['has_secret'] is False for row in others)
    assert all(row['shared_has_secret'] is False for row in seen)
    # The model was started and stopped by the supervisor's own cleanup.
    assert (stub / 'stub_record.txt').read_text().split() == ['started', 'stopped']
