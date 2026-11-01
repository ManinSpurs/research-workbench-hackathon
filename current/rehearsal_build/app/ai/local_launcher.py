"""App-owned lifecycle for this app's own loopback ``llama-server`` process.

Scope of ownership, precisely
-----------------------------
This module starts, watches and stops *one child process it started itself*, and
nothing else. It never invokes a managed runtime's server, never signals a
process it did not spawn (a pre-existing listener on the port is reported and the
start is refused, not adopted or killed), and never installs, downloads or
repairs anything. The executable and weights paths come from
:mod:`app.ai.local_config`, which only ever holds operator-supplied absolute
paths.

Why a supervisor at the launch boundary, not inside the API process
------------------------------------------------------------------
The API server is started by a launcher (``run_mock.py`` for the rehearsal, the
packaged supervisor for a bundle). Keeping the child there means the server's
lifetime is owned by the same process that owns the other servers: it is stopped
by the same ``finally`` on quit, it cannot outlive the app, and a crash of the API
process is observable rather than hidden. The API process only ever *reads* the
resulting loopback origin through :mod:`app.ai.local_runtime`.

Opt-in, never implied
---------------------
Nothing here runs unless a configuration file exists. The rehearsal stays
mock-only unless the operator has both configured the runtime and asked for it,
so ``run_mock.py``'s "never selects a real provider" promise keeps holding.
"""
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from . import local_config, local_reaper, local_runtime

#: Seconds allowed for the server to answer its own readiness endpoint. A 4B
#: Q4_K_M model loads from local disk in a few seconds on this hardware; this is
#: a generous ceiling that still fails rather than hanging the app forever.
#: A caller with a shorter overall deadline (the packaged shell gives the whole
#: backend 90 s) passes its own value to :class:`LocalServer` instead of relying
#: on this default.
STARTUP_TIMEOUT = 120.0
#: Poll spacing while waiting for readiness.
POLL_SECONDS = 0.25
#: Deadline for each individual readiness/measurement request. Short: these are
#: loopback calls to a server that has already answered once.
PROBE_TIMEOUT = 5.0
#: Largest readiness body read. ``/v1/models`` for one alias is a few hundred
#: bytes; a megabyte upper bound keeps a hostile or broken server from making
#: startup read unbounded data.
PROBE_BODY_LIMIT = 1024 * 1024
#: Seconds a child gets to exit after ``SIGTERM`` before it is killed, and then
#: for the kill to take effect.
STOP_GRACE_SECONDS = 10.0
KILL_WAIT_SECONDS = 5.0
#: The same two waits on a *failure* path, where the whole packaged startup must
#: still finish well inside the shell's 90 s ready timeout. A model that ignores
#: both signals is left to the watchdog rather than holding the app's startup
#: open: the app has to become ready with local mode unavailable.
FAILURE_STOP_GRACE_SECONDS = 3.0
FAILURE_KILL_WAIT_SECONDS = 2.0


def _probe_timeout(deadline):
    """Seconds one request may take: ``PROBE_TIMEOUT`` capped by the start budget.

    ``deadline`` is an absolute :func:`time.monotonic` value for the whole start,
    so every probe, the weight verification and the template measurement share
    one budget instead of each adding its own timeout to the worst case.
    """
    if deadline is None:
        return PROBE_TIMEOUT
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(PROBE_TIMEOUT, remaining)


class LocalLaunchError(RuntimeError):
    """A local runtime could not be launched; the message is the honest reason."""


def port_in_use(port):
    """True when something already listens on loopback ``port``.

    Checked before starting: adopting a listener this app did not start would
    mean talking to an unknown server, and killing it would destroy someone
    else's process. Either way the correct action is to refuse and say so.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def build_argv(config, api_key_file=None):
    """The exact argv for the configured runtime. No shell, no interpolation.

    Hardening flags are chosen from what this build's ``--help`` actually
    documents (verified against b10621):

    * ``--no-webui``: no browser surface over the model at all.
    * ``--cors-origins <app origin>``: exactly one origin, never ``*``. This
      app's own frontend never calls the model port — the API process does, on
      the server's behalf — so a launch that declares no app origin falls back to
      the server's own loopback origin, a value no page can ever have. The
      reviewer noted that empty-string semantics are unverified, so no behavior
      is assumed from ``""``. This is defense in depth: the bearer is what
      actually authorizes a request, CORS only constrains browsers.
    * ``--no-cors-credentials``: with ``*`` origins plus credentials enabled
      (the defaults) any web page could drive this model. Turning both off is
      what stops that.
    * ``--no-context-shift``: an over-long prompt must be a hard server error,
      never a silently rotated/truncated window.
    * ``--api-key-file``: the per-run bearer comes from an owner-only temporary
      file, never from argv (argv is visible in the process list).
    """
    argv = [config['runtime_executable'],
            '-m', config['model_weights'],
            '--alias', config['alias'],
            '--host', '127.0.0.1',
            '--port', str(config['port']),
            '-c', str(config['context']),
            '--no-webui',
            # Never `*`: one origin, and a launch with no declared app origin
            # names this server's own loopback origin, which no page can have.
            '--cors-origins', config.get('app_origin') or config['base_url'],
            '--no-cors-credentials',
            '--no-context-shift']
    if api_key_file:
        argv += ['--api-key-file', api_key_file]
    return argv


class LocalServer:
    """One owned, authenticated ``llama-server`` child, or none.

    The bearer is generated per run, kept in this object's memory and in the API
    child's environment only. It is written to an owner-only temporary key file
    because that is the documented way to pass it to the server without putting
    it in argv (which any local process can read). It is never written to the
    app's saved configuration, the receipt, the preview, the registry snapshot or
    a log line: those carry :func:`local_runtime.run_fingerprint` instead.
    """

    def __init__(self, config, log_path=None, app_origin=None, startup_timeout=None,
                 watch_parent=True):
        self.config = config
        self.log_path = log_path
        self.app_origin = app_origin
        #: Deadline for this start. ``None`` uses :data:`STARTUP_TIMEOUT`; the
        #: packaged supervisor passes a value that fits inside the shell's own
        #: ready timeout so a slow model cannot make the app miss it.
        self.startup_timeout = STARTUP_TIMEOUT if startup_timeout is None else float(startup_timeout)
        #: Whether to arm the parent-death reaper for the model child. The
        #: packaged supervisor arms it; a supervisor that stops its child itself
        #: on every exit path may not need it.
        self.watch_parent = watch_parent
        self.process = None
        self.secret = None
        self.key_file = None
        self.run_id = None
        self.weights_sha256 = None
        self.template_overhead_tokens = None
        self.observed_identity = None
        self.reaper = None

    def _make_key_file(self):
        """Owner-only temp file holding this run's bearer, removed on stop."""
        secret = secrets.token_urlsafe(32)
        handle, path = tempfile.mkstemp(prefix='wb-local-key-', suffix='.txt')
        try:
            os.fchmod(handle, 0o600)
            os.write(handle, (secret + '\n').encode())
        finally:
            os.close(handle)
        self.secret = secret
        self.key_file = path
        return path

    def _remove_key_file(self):
        """Remove this run's key file on every path out, success or not."""
        path, self.key_file = self.key_file, None
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Reported, never raised: the caller is usually already handling an
            # error, and a leftover owner-only file must not mask that reason.
            print('Local runtime key file could not be removed: %s' % exc,
                  file=sys.stderr, flush=True)

    def _verify_weights(self, deadline=None):
        """Verify the pinned digest and size before launching anything.

        The weights are the model. Launching a file whose digest does not match
        would produce a server that answers as the pinned alias while weighing
        something else entirely, so a mismatch is fatal before spawn.

        ``deadline`` is the absolute end of the whole start: a digest that cannot
        finish inside it fails the launch rather than silently overrunning the
        caller's ready timeout.
        """
        path = self.config['model_weights']
        expected_bytes = local_runtime.MODEL_FACTS['bytes']
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            raise LocalLaunchError('Local model weights are unreadable: %s' % exc) from None
        if size != expected_bytes:
            raise LocalLaunchError(
                'Local model weights are %d bytes but the pinned file is %d bytes; refusing to '
                'launch a different model.' % (size, expected_bytes))
        digest = hashlib.sha256()
        try:
            with open(path, 'rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
                    if deadline is not None and time.monotonic() > deadline:
                        raise LocalLaunchError(
                            'Verifying the local model weights did not finish within the '
                            'allowed startup time; local mode is unavailable for this session.')
        except OSError as exc:
            raise LocalLaunchError('Local model weights could not be read: %s' % exc) from None
        observed = digest.hexdigest()
        expected = local_runtime.MODEL_FACTS['sha256']
        if observed != expected:
            raise LocalLaunchError(
                'Local model weights do not match the pinned SHA-256 (expected %s, observed %s); '
                'refusing to launch a different model.' % (expected, observed))
        self.weights_sha256 = observed
        return observed

    def start(self):
        """Preflight, verify, spawn, wait for readiness. Raises LocalLaunchError.

        One absolute deadline covers the whole start: the port probe, the weight
        digest, every readiness probe and the template measurement each get at
        most the time that is left, so the worst case is the budget itself
        rather than the sum of the individual timeouts.
        """
        config = self.config
        deadline = time.monotonic() + self.startup_timeout
        executable = config['runtime_executable']
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise LocalLaunchError('Local runtime executable is missing or not executable: %s'
                                   % executable)
        if not os.path.isfile(config['model_weights']):
            raise LocalLaunchError('Local model weights are missing: %s' % config['model_weights'])
        if port_in_use(config['port']):
            raise LocalLaunchError(
                'Port %d is already in use, so the local model was not started. This app only '
                'stops a server it started itself; close the other program or choose another '
                'port in the local runtime settings.' % config['port'])
        self._verify_weights(deadline)
        key_file = self._make_key_file()
        try:
            argv = build_argv({**config, 'app_origin': self.app_origin}, api_key_file=key_file)
            log = open(self.log_path, 'ab') if self.log_path else subprocess.DEVNULL
            try:
                self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                                stdout=log, stderr=subprocess.STDOUT,
                                                # New session: the child is not in this
                                                # process's group, so a terminal Ctrl+C
                                                # reaches the supervisor, which stops it
                                                # deliberately rather than twice.
                                                start_new_session=True)
            except OSError as exc:
                raise LocalLaunchError('Local runtime could not be started: %s' % exc) from None
            finally:
                if log is not subprocess.DEVNULL:
                    log.close()
            self.run_id = uuid.uuid4().hex
            try:
                # A child that is already gone is reported with its own exit code
                # before any watchdog is armed: arming one for a dead pid would
                # fail its own liveness confirmation and mask this reason.
                code = self.process.poll()
                if code is not None:
                    raise LocalLaunchError(
                        'Local runtime exited during startup with code %s; see the local '
                        'runtime log.' % code)
                # Armed as soon as the model exists, before readiness: a
                # supervisor killed during model load must not leave the loading
                # child behind either. A watchdog that cannot start is a failed
                # start, not a silently unprotected run.
                if self.watch_parent:
                    try:
                        self.reaper = local_reaper.arm(getattr(self.process, 'pid', 0),
                                                       log_path=self.log_path)
                    except local_reaper.ReaperError as exc:
                        raise LocalLaunchError(str(exc)) from None
                self._wait_ready(deadline)
                # Only after the server proved it is the one we launched: the
                # overhead is measured against the real chat template, with a
                # fixed synthetic string, so no researcher text is involved.
                self.measure_template_overhead(deadline)
            except BaseException:
                # Any way readiness can end — a launch failure, KeyboardInterrupt
                # or SIGTERM raised by the supervisor's signal handler,
                # SystemExit — must not leave the child we just spawned running.
                self._abandon_after_failed_start()
                raise
        except BaseException:
            # The key file exists as soon as it is written; every failure path,
            # including an interrupt, must remove it.
            self._remove_key_file()
            raise

    def _abandon_after_failed_start(self):
        """Stop the child after a failed start, preserving the original error."""
        try:
            self.stop(grace=FAILURE_STOP_GRACE_SECONDS, kill_wait=FAILURE_KILL_WAIT_SECONDS)
        except BaseException as exc:  # noqa: BLE001 - must not mask the cause
            # Report, never re-raise: the original exception is propagating and
            # the caller's failure reason must not be replaced by a cleanup one.
            print('Local runtime cleanup failed after a failed start: %s' % exc,
                  file=sys.stderr, flush=True)

    def _wait_ready(self, deadline=None):
        """Poll until the launched server serves the alias with the launched ctx.

        Readiness is proved, not assumed: the authenticated ``/v1/models`` list
        must name the alias, and the server's own ``/props`` must agree on alias
        and ``n_ctx``. Anything else — an unrelated listener that inherited the
        port, a server launched with a different window — is a failed start, so
        no request can be approved against a runtime this app did not launch.

        Every probe's own timeout is capped by what is left of ``deadline``, so a
        server that accepts connections and never answers cannot stretch one
        iteration past the start budget.
        """
        if deadline is None:
            deadline = time.monotonic() + self.startup_timeout
        origin = self.config['base_url']
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                raise LocalLaunchError(
                    'Local runtime exited during startup with code %s; see the local runtime log.'
                    % code)
            remaining = _probe_timeout(deadline)
            if remaining <= 0:
                break
            identity = _serves_verified_identity(origin, self.config['alias'], self.secret,
                                                 self.config['context'], deadline=deadline)
            if identity is not None:
                # Recorded, not discarded: the binding must publish what the
                # server said about itself, so a receipt describing this run is
                # evidence of the loaded model rather than of the launch intent.
                self.observed_identity = identity
                return
            time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        raise LocalLaunchError('Local runtime did not become ready within %d seconds.'
                               % int(self.startup_timeout))

    def measure_template_overhead(self, deadline=None):
        """Measure the chat template's real token overhead against this server.

        Uses a fixed synthetic string (:data:`local_runtime.TEMPLATE_PROBE_TEXT`),
        never researcher text, so this may run before any consent. The rendered
        prompt's token count minus the probe string's own token count is the
        overhead the template and special tokens add — the constant the prompt
        bound needs. Two documented endpoints, one attempt each.

        ``deadline`` is the absolute end of the whole start. Each of the three
        posts may take at most what is left of it, so a slow measurement fails
        the launch instead of overrunning the caller's ready timeout.

        Raises LocalLaunchError when the server does not answer in the documented
        shape: without a measurement the bound cannot be called proven, so the
        launch fails rather than shipping a guessed reserve.
        """
        origin = self.config['base_url']
        if local_runtime.loopback_origin(origin) is None:
            raise LocalLaunchError('Local runtime origin is not loopback; refusing to probe it.')
        # Checked before building the opener: with no time left there is nothing
        # to send, and the answer is the same either way.
        if _probe_timeout(deadline) <= 0:
            raise LocalLaunchError(
                'Measuring the local model chat template did not finish within the allowed '
                'startup time; local mode is unavailable for this session.')
        headers = {'Authorization': 'Bearer ' + self.secret,
                   'Content-Type': 'application/json'}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect())
        probe = local_runtime.TEMPLATE_PROBE_TEXT

        def post(path, payload):
            timeout = _probe_timeout(deadline)
            if timeout <= 0:
                raise LocalLaunchError(
                    'Measuring the local model chat template did not finish within the allowed '
                    'startup time; local mode is unavailable for this session.')
            body = json.dumps(payload).encode()
            request = urllib.request.Request(origin + path, data=body, method='POST',
                                             headers=headers)
            with opener.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise LocalLaunchError(
                        'Local runtime answered %s on %s; the template measurement could not '
                        'run.' % (response.status, path))
                return json.loads(response.read(PROBE_BODY_LIMIT + 1))

        try:
            rendered = post(local_runtime.APPLY_TEMPLATE_PATH,
                            {'messages': [{'role': 'user', 'content': probe}]})
            text = rendered.get('prompt') if isinstance(rendered, dict) else None
            if not isinstance(text, str) or not text:
                raise LocalLaunchError('Local runtime did not return a rendered prompt; the '
                                       'template overhead could not be measured.')
            templated = post(local_runtime.TOKENIZE_PATH,
                             {'content': text, 'add_special': True})
            self._template_tokens = _count_tokens(templated, local_runtime.APPLY_TEMPLATE_PATH)
            bare = post(local_runtime.TOKENIZE_PATH,
                        {'content': probe, 'add_special': True})
            self._bare_tokens = _count_tokens(bare, local_runtime.TOKENIZE_PATH)
        except LocalLaunchError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LocalLaunchError('Local runtime template overhead could not be measured: %s'
                                   % exc) from None
        delivered = self._template_tokens - self._bare_tokens
        if delivered < 0:
            raise LocalLaunchError(
                'Local runtime reported fewer tokens for the templated prompt (%d) than for the '
                'bare text (%d); the template overhead cannot be derived from that answer.'
                % (self._template_tokens, self._bare_tokens))
        self.template_overhead_tokens = delivered
        return delivered

    def stop(self, *, grace=STOP_GRACE_SECONDS, kill_wait=KILL_WAIT_SECONDS):
        """Stop only the child this object started, and wait for it to be reaped.

        Never swallows a failure: a child that cannot be stopped is a real
        condition the caller must be able to report. Callers that are already
        handling an error use :func:`stop_preserving`, which reports instead of
        raising so the original failure is not replaced.

        This run's key file is removed on the way out, whether or not the child
        was still running: a per-run credential must not outlive its run.

        The parent-death watchdog is disarmed **only once ``poll()`` confirms the
        child is gone**. If the child survived both ``SIGTERM`` and ``SIGKILL``,
        the watchdog stays armed and responsible for it, and the failure is
        raised: disarming there would leave a model running with nothing watching
        it, which is the very case the watchdog exists to cover.
        """
        reaped = False
        try:
            self._stop_child(grace, kill_wait)
            reaped = True
        finally:
            if reaped:
                self._disarm_reaper()
            else:
                self._keep_reaper_armed()
            self._remove_key_file()
            self.secret = None

    def _keep_reaper_armed(self):
        """Report that the watchdog is still watching a child that would not exit.

        The reference is deliberately kept: dropping it would let the still
        running watchdog process be collected without anyone able to state it is
        there, and a later ``stop()`` should report the same condition rather
        than quietly belong to nobody.

        Reported, never raised: the real failure is already propagating, and the
        model is not unwatched — the watchdog it was armed with is still holding
        the pid.
        """
        reaper = self.reaper
        if reaper is None:
            return
        print('Local model did not exit; its cleanup watchdog (pid %s) stays armed for model '
              'pid %s.' % (getattr(reaper.process, 'pid', '?'), reaper.model_pid),
              file=sys.stderr, flush=True)

    def _disarm_reaper(self):
        """Stop the watchdog for this run, reporting rather than raising.

        Reported, never raised: the caller is usually already handling the reason
        this run is ending, and a watchdog that could not be disarmed is a fact
        to state, not a second failure to replace the first with.
        """
        reaper, self.reaper = self.reaper, None
        message = local_reaper.disarm(reaper)
        if message:
            print(message, file=sys.stderr, flush=True)

    def _stop_child(self, grace=STOP_GRACE_SECONDS, kill_wait=KILL_WAIT_SECONDS):
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError as exc:
            raise LocalLaunchError(
                'Local runtime could not be asked to stop (pid %s): %s'
                % (getattr(process, 'pid', '?'), exc)) from None
        try:
            process.wait(timeout=grace)
            if process.poll() is not None:
                return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
            process.wait(timeout=kill_wait)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalLaunchError(
                'Local runtime did not exit after being stopped (pid %s): %s; its cleanup '
                'watchdog stays armed for it.' % (getattr(process, 'pid', '?'), exc)) from None
        if process.poll() is None:
            # Both signals sent, no exit and no exception reported: the child is
            # still alive. Never pretend it was stopped.
            raise LocalLaunchError(
                'Local runtime is still running after being stopped (pid %s); its cleanup '
                'watchdog stays armed for it.' % getattr(process, 'pid', '?'))


def _count_tokens(body, where):
    """Token count from a ``/tokenize`` answer, or a launch error naming where."""
    tokens = body.get('tokens') if isinstance(body, dict) else None
    if not isinstance(tokens, list):
        raise LocalLaunchError(
            'Local runtime did not return a token list from %s; the prompt bound cannot be '
            'stated as a proven bound.' % where)
    return len(tokens)


def _read_json(origin, path, headers, timeout=PROBE_TIMEOUT):
    """One bounded, redirect-refusing loopback GET parsed as JSON, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect())
    request = urllib.request.Request(origin + path, method='GET', headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            raw = response.read(PROBE_BODY_LIMIT + 1)
    except Exception:
        return None
    if len(raw) > PROBE_BODY_LIMIT:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _anonymous_request_refused(origin, timeout=PROBE_TIMEOUT):
    """Whether an UNAUTHENTICATED ``GET /v1/models`` is refused (401/403).

    The bearer this app generates is only load-bearing if the server actually
    requires it. A server that answers 200 without one is an anonymous listener
    that ignores ``Authorization``, and readiness against it would prove nothing.
    Only 401/403 counts as proof; a 200, a 404 or a connection error is "not
    proved required", never "probably fine".
    """
    if local_runtime.loopback_origin(origin) is None:
        # Same invariant as every other probe: never contact another host, so a
        # misconfigured origin cannot turn this into an outbound request.
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect())
    request = urllib.request.Request(origin + local_runtime.PROBE_PATH, method='GET')
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status in (401, 403)
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)
    except Exception:
        return False


def _serves_verified_identity(origin, alias, secret, context, deadline=None):
    """The identity the server reports about itself, or None when it is not ours.

    Four separate claims, all required:

    1. the origin is still a supported loopback origin, so a readiness check can
       never talk to anything else whatever the configuration says;
    2. an authenticated ``GET /v1/models`` (carrying this run's bearer) lists the
       alias — an unauthenticated listener fails here, and so does a server that
       serves a different model;
    3. an authenticated ``GET /props`` names that same alias and reports
       ``default_generation_settings.n_ctx`` equal to the launched ``-c`` —
       presence equality, since an absent field is not agreement;
   4. an UNAUTHENTICATED ``GET /v1/models`` is refused with 401/403, proving the
       bearer is actually required rather than ignored.

    ``total_slots`` is deliberately NOT a requirement. This runtime reports its
    ``--parallel`` slots (AUTO on this install) with ``kv_unified=true``, and the
    full declared ``-c`` is genuine per request rather than divided between
    slots, so a slot count of 4 says nothing about whether the prompt bound
    holds. It is recorded when reported, for the audit trail only.

    On success it returns what the server said about itself (alias, ``n_ctx``,
    ``n_ctx_train``, ``total_slots`` when present), so the published binding can
    record an *observed* identity rather than the values this app merely intended
    to launch. Redirects are refused (urllib follows them by default, which would
    turn a readiness probe into an unintended outbound request) and bodies are
    capped.

    ``deadline`` is the absolute end of the whole start. Each of the three
    requests gets at most ``PROBE_TIMEOUT`` *and* at most what is left of it, so
    one slow probe cannot overrun the caller's ready timeout.
    """
    if local_runtime.loopback_origin(origin) is None:
        return None
    if not secret:
        return None
    headers = {'Authorization': 'Bearer ' + secret}
    models = _read_json(origin, local_runtime.PROBE_PATH, headers, _probe_timeout(deadline))
    data = models.get('data') if isinstance(models, dict) else None
    if not isinstance(data, list):
        return None
    if not any(isinstance(x, dict) and x.get('id') == alias for x in data):
        return None
    props = _read_json(origin, local_runtime.PROPS_PATH, headers, _probe_timeout(deadline))
    if not isinstance(props, dict):
        return None
    observed_alias = props.get('model_alias')
    if observed_alias != alias:
        return None
    generation = props.get('default_generation_settings')
    observed_ctx = generation.get('n_ctx') if isinstance(generation, dict) else None
    if type(observed_ctx) is not int or observed_ctx != context:
        return None
    if not _anonymous_request_refused(origin, _probe_timeout(deadline)):
        return None
    total_slots = props.get('total_slots')
    return {'model_alias': observed_alias,
            'n_ctx': observed_ctx,
            'n_ctx_train': props.get('n_ctx_train')
                           if type(props.get('n_ctx_train')) is int else None,
            # Reported only when the server states it; never a requirement.
            'total_slots': total_slots if type(total_slots) is int else None}


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into an error: the probe stays on its loopback origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code,
                                     'readiness probe refuses redirect to %s' % newurl,
                                     headers, fp)


def launch_from_config(log_path=None, app_origin=None, startup_timeout=None,
                       watch_parent=True):
    """Launch from the private config file, or return None when unconfigured.

    Returning None (rather than raising) is what keeps the local runtime
    genuinely optional: an app with no configuration launches exactly as before.
    On success the started server has verified its own identity, measured the
    chat template overhead, and is ready to publish a binding.
    """
    try:
        config = local_config.read()
    except ValueError as exc:
        raise LocalLaunchError(str(exc)) from None
    if not config:
        return None
    server = LocalServer(config, log_path=log_path, app_origin=app_origin,
                         startup_timeout=startup_timeout, watch_parent=watch_parent)
    server.start()
    return server


def binding_environment(server):
    """The per-run binding, as the environment variables the API child reads.

    This is the ONLY place the per-run bearer enters an environment, and it is
    returned as a dict for the caller to pass to the API process alone — never
    the shared environment that also holds the static server and Electron, whose
    children must not be able to read the model's credential.

    Everything here describes the *verified* launch: the origin, alias and
    context the server itself reported, the run id, the launched pid, the pinned
    weights digest, the measured template overhead and the bearer. A saved
    configuration alone publishes nothing, so a file can never make the app
    think a runtime is running.
    """
    if server is None or server.process is None or server.secret is None:
        return {}
    if server.template_overhead_tokens is None or not server.observed_identity:
        return {}
    identity = server.observed_identity
    return {'WB_LOCAL_BASE_URL': server.config['base_url'],
            'WB_LOCAL_MODEL': server.config['alias'],
            'WB_LOCAL_CONTEXT': str(server.config['context']),
            'WB_LOCAL_RUN_ID': str(server.run_id),
            'WB_LOCAL_SERVER_PID': str(getattr(server.process, 'pid', '')),
            'WB_LOCAL_WEIGHTS_SHA256': server.weights_sha256 or '',
            'WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS': str(server.template_overhead_tokens),
            # What the server reported about itself at readiness, so a preview or
            # receipt can state the observed loaded model instead of only the
            # values this app intended to launch with.
            'WB_LOCAL_OBSERVED_ALIAS': identity.get('model_alias') or '',
            'WB_LOCAL_OBSERVED_CTX': str(identity.get('n_ctx') or ''),
            'WB_LOCAL_OBSERVED_CTX_TRAIN': str(identity.get('n_ctx_train') or ''),
            'WB_LOCAL_API_KEY': server.secret}


def stop_preserving(server, *, on_cleanup_error=print, grace=STOP_GRACE_SECONDS,
                    kill_wait=KILL_WAIT_SECONDS):
    """Stop an owned server during cleanup, reporting failure without raising.

    Used on the exit path, where an exception would replace whatever actually
    ended the run (an interrupt, a failed start, a normal quit). A child that
    could not be stopped is *reported*, never silently ignored: a leaked model
    process is exactly the condition the caller needs to know about. Returns
    ``None`` when the server stopped or there was nothing to stop, otherwise a
    message describing the failure.

    ``grace``/``kill_wait`` are the failure path's budget when a caller has an
    outer timeout to respect; see :data:`FAILURE_STOP_GRACE_SECONDS`.
    """
    if server is None:
        return None
    try:
        server.stop(grace=grace, kill_wait=kill_wait)
    except BaseException as exc:  # noqa: BLE001 - cleanup must not replace the cause
        message = 'Local runtime did not stop cleanly: %s' % exc
        try:
            on_cleanup_error(message, file=sys.stderr, flush=True)
        except TypeError:
            on_cleanup_error(message)
        except BaseException:  # noqa: BLE001 - a reporter must not mask the cause
            pass
        return message
    return None
