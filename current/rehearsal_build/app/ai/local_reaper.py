"""Watchdog that stops this app's own local model if the supervisor is killed.

Why a second process
--------------------
The model server is started by the packaged supervisor with
``start_new_session=True``, so it is deliberately not in the supervisor's process
group: a terminal interrupt reaches the supervisor, which stops the model itself
instead of the group killing both. The cost of that isolation is that a ``SIGKILL``
of the supervisor, or a crash that skips every ``finally``, leaves the model
running with nothing left in that process to stop it. macOS has no
"signal the child when the parent dies" facility this app can rely on, so the
supported means is one more owned process: this reaper, started as a child of the
supervisor that owns the model.

What it does, and what it refuses to do
--------------------------------------
It watches two independent facts about the supervisor it was started by: the
process this reaper is still a child of (``getppid()``) and that pid's liveness.
Reparenting is checked first, because it is the one signal a reused pid cannot
forge: once the supervisor exits this process becomes a child of launchd, and no
other process can make that untrue. It then asks the model to exit (``SIGTERM``,
then ``SIGKILL`` after a grace period) and exits.

It is given exactly one model pid and one supervisor pid. It never looks a process
up by name or port, never scans the process table, and never signals anything
else. The residual risk that remains is pid reuse on the model pid during the half
second in which a death is detected: the pid is signalled once, only after the
supervisor it belongs to is gone, and never again.

This module is started two ways, both without a shell: by the supervisor through
:func:`reaper_command` (``python -c ... app.ai.local_reaper.main``), and directly
as ``python -m app.ai.local_reaper MODEL_PID SUPERVISOR_PID [POLL_SECONDS]``.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

__all__ = ['ReaperError', 'ReaperWatch', 'arm', 'disarm', 'main', 'reaper_command',
           'REAPER_POLL_SECONDS', 'MODEL_STOP_GRACE_SECONDS', 'WATCHDOG_STOP_TIMEOUT']

#: Seconds between supervisor-liveness checks. Half a second keeps a leftover
#: model from surviving noticeably longer than the crash that stranded it while
#: costing almost nothing.
REAPER_POLL_SECONDS = 0.5
#: Seconds the model gets to exit after ``SIGTERM`` before ``SIGKILL``.
MODEL_STOP_GRACE_SECONDS = 10.0
#: Seconds to wait for the watchdog itself to exit when the supervisor disarms it.
WATCHDOG_STOP_TIMEOUT = 5.0
#: Seconds allowed for a freshly started watchdog to prove it is still running.
#: A ``Popen`` that succeeded proves only that ``fork``/``exec`` was attempted:
#: an interpreter that fails to start, or a bootstrap that raises, exits almost
#: immediately, and a supervisor that treated that as "protected" would leave the
#: model unwatched. This is one short confirmation, not a retry loop.
ARM_CONFIRM_SECONDS = 0.25
#: The app root (``.../app/ai/local_reaper.py`` -> ``...``), put on the child's
#: ``sys.path`` so it imports this same module from the same bundle.
APP_ROOT = Path(__file__).resolve().parents[2]

#: The child's bootstrap. The package module is imported from disk rather than
#: pasted in, so the watchdog that runs is this file's own code and the tests
#: exercise the same functions the packaged app runs. ``sys.exit`` carries
#: ``main``'s code out: without it a watchdog that could not parse its argv would
#: exit 0 and look like a clean run.
BOOTSTRAP = ('import sys; sys.path.insert(0, {location!r}); '
             'from app.ai import local_reaper; sys.exit(local_reaper.main(sys.argv[1:]))')


class ReaperError(RuntimeError):
    """The watchdog could not be started; the message is the honest reason."""


class ReaperWatch:
    """The started watchdog process, so the supervisor can disarm it."""

    def __init__(self, process, model_pid, backend_pid):
        self.process = process
        self.model_pid = model_pid
        self.backend_pid = backend_pid

    def poll(self):
        """The watchdog's exit code, or None while it is still watching."""
        return self.process.poll()


def reaper_command(model_pid, backend_pid, poll_seconds=REAPER_POLL_SECONDS):
    """The exact argv for the watchdog. No shell, no interpolation of data."""
    return [sys.executable, '-c', BOOTSTRAP.format(location=str(APP_ROOT)),
            str(int(model_pid)), str(int(backend_pid)), repr(float(poll_seconds))]


def arm(model_pid, backend_pid=None, poll_seconds=REAPER_POLL_SECONDS, log_path=None):
    """Start the watchdog for one model pid. Raises :class:`ReaperError`.

    ``backend_pid`` defaults to this process, which is the normal case: the
    supervisor arms the watchdog for the model child it just started.
    """
    if not isinstance(model_pid, int) or isinstance(model_pid, bool) or model_pid <= 0:
        raise ReaperError('the local model has no pid to watch')
    backend_pid = os.getpid() if backend_pid is None else backend_pid
    log = open(log_path, 'ab') if log_path else subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            reaper_command(model_pid, backend_pid, poll_seconds),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            # Its own session for the same reason as the model: a terminal
            # interrupt must reach the supervisor, which disarms this
            # deliberately rather than killing it from under the cleanup.
            start_new_session=True, close_fds=True)
    except OSError as exc:
        raise ReaperError('the local model cleanup watchdog could not be started: %s'
                          % exc) from None
    finally:
        if log is not subprocess.DEVNULL:
            log.close()
    _confirm_alive(process)
    return ReaperWatch(process, model_pid, backend_pid)


def _confirm_alive(process, confirm_seconds=ARM_CONFIRM_SECONDS):
    """Raise :class:`ReaperError` if a just-started watchdog has already exited.

    ``Popen`` returning only means a child was created. An interpreter that
    cannot load, or a bootstrap that raises, is gone within milliseconds; if the
    supervisor accepted that as a working watchdog the model would be left with
    nothing watching it. One short wait is enough to tell the two apart, and it
    is not a retry: a watchdog that died is a failed arm, and the caller stops
    the model it just started.
    """
    try:
        # A bounded observation window; its return value is not the verdict.
        process.wait(timeout=confirm_seconds)
    except subprocess.TimeoutExpired:
        return
    except OSError as exc:
        raise ReaperError('the local model cleanup watchdog could not be confirmed: %s'
                          % exc) from None
    # The verdict comes from ``poll()``, the same rule ``LocalServer.stop`` uses:
    # only a child that is confirmed gone is treated as gone.
    code = process.poll()
    if code is None:
        return
    raise ReaperError('the local model cleanup watchdog exited immediately (code %s); the model '
                      'would be unwatched, so the launch is refused.' % code)


def disarm(watch):
    """Stop the watchdog. Returns None, or a message describing the failure."""
    if watch is None:
        return None
    process = watch.process
    if process is None or process.poll() is not None:
        return None
    try:
        process.terminate()
    except OSError as exc:
        return 'Local model watchdog could not be asked to stop: %s' % exc
    try:
        process.wait(timeout=WATCHDOG_STOP_TIMEOUT)
        return None
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
        process.wait(timeout=WATCHDOG_STOP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ('Local model watchdog did not exit (pid %s): %s'
                % (getattr(process, 'pid', '?'), exc))
    return None


def _alive(pid):
    """Whether ``pid`` names a live process. Never raises."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists but is not ours to signal: still a live process.
        return True
    except OSError:
        return False
    return True


def _backend_gone(backend_pid):
    """Whether the supervisor this watchdog watches has gone away.

    Reparenting first: this process was started as a child of the supervisor, so
    ``getppid()`` no longer naming it is proof the supervisor exited, and proof a
    reused pid cannot forge.
    """
    if os.getppid() != backend_pid:
        return True
    return not _alive(backend_pid)


def _signal(pid, number):
    """Send one signal, tolerating a process that has already exited."""
    try:
        os.kill(pid, number)
    except OSError:
        return False
    return True


def _stop_model(model_pid, grace=None):
    """Ask the model to exit, then insist. Returns the last signal sent."""
    grace = MODEL_STOP_GRACE_SECONDS if grace is None else grace
    if not _alive(model_pid):
        return None
    _signal(model_pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _alive(model_pid):
            return signal.SIGTERM
        time.sleep(0.1)
    _signal(model_pid, signal.SIGKILL)
    return signal.SIGKILL


def main(argv=None):
    """Watch the supervisor, then stop the model. Returns a process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print('usage: local_reaper MODEL_PID SUPERVISOR_PID [POLL_SECONDS]',
              file=sys.stderr, flush=True)
        return 2
    try:
        model_pid = int(argv[0])
        backend_pid = int(argv[1])
        poll_seconds = float(argv[2]) if len(argv) > 2 else REAPER_POLL_SECONDS
    except (TypeError, ValueError):
        print('local_reaper: MODEL_PID and SUPERVISOR_PID must be integers',
              file=sys.stderr, flush=True)
        return 2
    if model_pid <= 0 or backend_pid <= 0:
        print('local_reaper: refusing a non-positive pid', file=sys.stderr, flush=True)
        return 2
    if not poll_seconds > 0:
        poll_seconds = REAPER_POLL_SECONDS
    while not _backend_gone(backend_pid):
        time.sleep(poll_seconds)
    _stop_model(model_pid)
    return 0


if __name__ == '__main__':
    sys.exit(main())
