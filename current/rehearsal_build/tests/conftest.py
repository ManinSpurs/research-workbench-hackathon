"""The test process may contact loopback fakes only. Never a real model endpoint."""
import ipaddress
import os
import socket
import tempfile
import uuid

import pytest

# Set at import time, before any test module is imported: the registry builds
# its local row from the live configuration when `app.ai.registry` is imported,
# which happens during collection. A fixture would run too late, and a developer
# machine with a real local model saved would then change what these tests see.
# A fresh, non-existent path per run means "not configured" unless a test writes
# one, and this machine's actual config is never read or modified.
os.environ['WB_LOCAL_CONFIG'] = (
    tempfile.mkdtemp(prefix='wb-localcfg-') + '/local_runtime.json')

#: Every environment name that carries a local *run* binding or its credential.
#: Stripped from the process environment before collection, so a developer shell
#: (or a previous rehearsal in the same terminal) that exports WB_LOCAL_API_KEY
#: and friends cannot make a test believe a verified launch happened. A test
#: that wants a binding must publish it explicitly through monkeypatch.
LOCAL_RUN_ENV = (
    'WB_LOCAL_BASE_URL', 'WB_LOCAL_MODEL', 'WB_LOCAL_CONTEXT', 'WB_LOCAL_RUN_ID',
    'WB_LOCAL_SERVER_PID', 'WB_LOCAL_WEIGHTS_SHA256',
    'WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS', 'WB_LOCAL_OBSERVED_ALIAS',
    'WB_LOCAL_OBSERVED_CTX', 'WB_LOCAL_OBSERVED_CTX_TRAIN', 'WB_LOCAL_API_KEY')
#: Also unset WB_LOCAL_RUNTIME so a developer shell cannot make run_mock's tests
#: think the local runtime was requested.
for _name in LOCAL_RUN_ENV + ('WB_LOCAL_RUNTIME',):
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def no_ambient_local_run_binding(monkeypatch):
    """Each test starts with no inherited run binding; monkeypatch still works.

    This runs before the test body, so a test that publishes a binding with
    ``monkeypatch.setenv`` keeps it (monkeypatch is applied after this fixture
    and is undone at teardown), while one that forgot to publish sees the honest
    unconfigured state instead of whatever the developer shell exported.
    """
    for name in LOCAL_RUN_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_local_runtime_config(monkeypatch, tmp_path_factory):
    """Per-test local-runtime configuration, isolated from the file above.

    Each test gets its own empty path so a test that writes a configuration
    cannot affect the next one; the process-level path set at import time
    remains the fallback for reads that happen outside a test.
    """
    monkeypatch.setenv('WB_LOCAL_CONFIG',
                       str(tmp_path_factory.mktemp('localcfg_' + uuid.uuid4().hex[:8])
                           / 'local_runtime.json'))


@pytest.fixture(autouse=True)
def loopback_network_only(monkeypatch):
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    def allowed(address):
        if not isinstance(address, tuple):  # AF_UNIX test infrastructure
            return
        host = address[0]
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("Non-loopback network access is forbidden in this test suite")
    def connect(sock, address):
        allowed(address)
        return original(sock, address)
    def connect_ex(sock, address):
        allowed(address)
        return original_ex(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
