"""Private, owner-only configuration for the app-owned local inference launcher.

What this file holds, and what it deliberately does not
------------------------------------------------------
Three ordinary install facts: where the ``llama-server`` executable is, where
the model weights file is, and the alias/context/port this app will launch that
server with. They are supplied explicitly by whoever installed the runtime; no
path, version or size is hardcoded here, because a source checkout has no way to
know where a model was installed on a given machine. An absent file therefore
means "local runtime not configured", never "use a guess".

It is written next to this app's other owner-only files (mode 600, owner uid),
never inside the project tree and never into another application's configuration
or internal store. Nothing here reads, copies or mirrors a managed runtime's
state; the paths are the only thing taken from it, and only because the operator
supplied them.

Override the location with ``WB_LOCAL_CONFIG`` (used by tests and by a launcher
that keeps its own profile).
"""
import json
import os
import stat
from pathlib import Path


def config_path():
    """This app's private local-runtime file, or the ``WB_LOCAL_CONFIG`` override."""
    override = os.environ.get('WB_LOCAL_CONFIG')
    if override:
        return Path(override)
    return Path.home() / 'Library/Application Support/ResearchWorkbench/local_runtime.json'


def _validate(raw):
    """Return the normalized fields, or raise ValueError naming the bad one."""
    if not isinstance(raw, dict):
        raise ValueError('Local runtime config must be a JSON object')
    executable = raw.get('runtime_executable')
    weights = raw.get('model_weights')
    alias = raw.get('alias')
    context = raw.get('context')
    port = raw.get('port')
    if not (isinstance(executable, str) and os.path.isabs(executable)):
        raise ValueError('runtime_executable must be an absolute path')
    if not (isinstance(weights, str) and os.path.isabs(weights)):
        raise ValueError('model_weights must be an absolute path')
    if (not isinstance(alias, str) or not alias.strip() or alias != alias.strip()
            or any(c.isspace() for c in alias)):
        raise ValueError('alias must be a nonempty string with no whitespace')
    if type(context) is not int or context <= 0:
        raise ValueError('context must be a positive integer')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('port must be an integer in 1..65535')
    return {'runtime_executable': executable, 'model_weights': weights,
            'alias': alias, 'context': context, 'port': port,
            'base_url': 'http://127.0.0.1:%d' % port}


def read():
    """The configured paths, or ``{}`` when nothing is configured.

    Raises ValueError only for a file that exists but is not an owner-only
    regular file or does not validate; callers turn that into an honest
    blocking reason rather than a silent fallback.
    """
    path = config_path()
    if not path.exists():
        return {}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Local runtime config must be a regular owner-only file (mode 600)')
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Local runtime config is not readable JSON: %s' % exc) from None
    return _validate(raw)


def save(values):
    """Write one validated configuration, owner-only, replacing the previous one."""
    clean = _validate(values)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(clean, indent=2, sort_keys=True) + '\n').encode())
    finally:
        os.close(fd)
    return path
