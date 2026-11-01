"""App-owned local inference contract: one loopback llama.cpp server.

What this module is, and is not
-------------------------------
It is the *configuration and protocol contract* the Workbench speaks to reach a
locally installed 4B instruction model: where the endpoint is, which alias and
context bound are in force, how a prompt is bounded and how a response is
validated. It does not spawn processes — that is
:mod:`app.ai.local_launcher`, which is invoked by the app's own supervisor — and
it never downloads, installs or repairs anything. The ``llama-server`` binary and
the model weights belong to whoever installed them; this app only records the
operator-supplied absolute paths and starts that binary as its own child.

Why an app-owned loopback server, not a host-managed provider
------------------------------------------------------------
A managed local-model runtime in this workspace binds an ephemeral port with a
per-spawn random bearer secret that is not exposed through any supported status
surface, and it owns its own lifetime (TTL, restart). Reading that secret would
mean scraping another process's internal state, and treating that lifetime as a
stable endpoint would be a promise this app cannot keep. So the supported
integration is: this app launches the installed ``llama-server`` itself, on a
fixed loopback port, with a per-run bearer secret it generates, and stops it on
quit. Nothing here invents an endpoint; when no runtime is running the local path
fails closed with an honest reason instead of a silent cloud fallback.

Pinned model facts (operator-supplied install, recorded for honesty)
-------------------------------------------------------------------
* Repository: ``unsloth/Qwen3-4B-Instruct-2507-GGUF``
* File: ``Qwen3-4B-Instruct-2507-Q4_K_M.gguf``
* Revision: ``a06e946bb6b655725eafa393f4a9745d460374c9``
* Size: 2497281120 bytes; licence apache-2.0
* Runtime: llama.cpp b10621 (Metal) ``llama-server``
* Context: this app declares the ``-c`` value it launched with (default 8192)
  and enforces that bound on every request. It never claims the model's much
  larger native training context: the bound that matters for a job is the one
  the running server was launched with, and only that is declared, recorded and
  enforced.

Configuration sources (both optional, neither required to run the app)
---------------------------------------------------------------------
1. This app's private, owner-only file written by the local runtime setup:
   ``python3 tools/configure_local_runtime.py --runtime-executable PATH
   --model-weights PATH``. It records only the two installed paths plus
   alias/context/port.
2. The per-run binding, which only this app's supervisor publishes after it has
   started and verified its own child server: ``WB_LOCAL_BASE_URL``,
   ``WB_LOCAL_MODEL``, ``WB_LOCAL_CONTEXT``, the run id and launched pid, the
   pinned weights digest, the measured template overhead, the identity the
   server reported at readiness, and the per-run bearer. These are not settings;
   they describe one live launch and are refused when it is not the current one.

Saved paths alone are therefore never a runtime: a file outlives a process and
says nothing about what is listening now, so it can never enable a preview.

Authentication, and why a loopback bind is not enough
----------------------------------------------------
A loopback bind alone is not a security boundary: any other process or any web
page in a browser on this machine can post to a port on 127.0.0.1. llama.cpp's
defaults made that worse (a Web UI on by default, ``--cors-origins *`` with
credentials). So the launcher starts the server with ``--no-webui``,
``--no-cors-credentials``, a single app origin for CORS and
``--api-key-file``: a per-run random bearer written to an owner-only temporary
file (never in argv, which any local process can read). The bearer lives in the
supervisor's memory, is handed to the API child's environment only, and is
never written to this app's configuration, a receipt, a preview, a log line or
any renderer-visible surface — those carry :func:`run_fingerprint` instead.

Prompt bound: proven, not estimated
-----------------------------------
The earlier ``3 characters per token`` ratio was wrong for non-Latin text (a
reviewer measured 24576 Korean characters becoming 16125 tokens, i.e. roughly
0.66 tokens per character, not 0.33). It is gone. What replaces it:

* one UTF-8 byte can never yield more than :data:`MAX_TOKENS_PER_UTF8_BYTE`
  tokens, so ``utf8_bytes * MAX_TOKENS_PER_UTF8_BYTE`` is a *proven upper bound*
  for any text in any language;
* the chat template's own overhead is **measured per run** during the launch
  readiness check, with a fixed synthetic probe string (never researcher text):
  the rendered prompt is tokenized and the difference from the probe text's own
  token count is the observed template and special-token overhead;
* the output allocation is subtracted explicitly (a prompt that fills the whole
  window leaves the model nowhere to answer).

``--no-context-shift`` is launched, so a prompt that does not fit is a hard
server error rather than a silently rotated window. At execute — after the
researcher consented — the exact prompt is rendered and tokenized, so the
decision is then exact rather than bounded.

Documented launch command (built from the configured paths and executed by the
app's supervisor as an owned child, never through a shell)::

    <runtime-executable> -m <model-weights> \\
        --alias qwen3-4b-instruct-2507-q4_K_M \\
        --host 127.0.0.1 --port 9931 -c 8192 \\
        --no-webui --cors-origins <app origin> --no-cors-credentials \\
        --no-context-shift --api-key-file <per-run owner-only temp file>
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

__all__ = [
    'PROVIDER_ID', 'MODEL_ID', 'DISPLAY_NAME', 'STATUS', 'READY_STATUS',
    'READY_TOKEN', 'NOT_PROBED_STATUS', 'PROBE_PATH', 'PROBE_TIMEOUT',
    'DATA_USE', 'COST_BASIS', 'MODEL_FACTS', 'ELIGIBLE_TASKS',
    'BLOCKING_MESSAGES', 'DEFAULT_BASE_URL', 'DEFAULT_WIRE_MODEL',
    'DEFAULT_CONTEXT', 'CHAT_PATH', 'LOOPBACK_HOSTS',
    'loopback_origin', 'read_config', 'blocking_message', 'connection_status',
    'launch_command', 'configured_context', 'model_status',
    'chat_endpoint', 'prompt_budget_bytes', 'prompt_fits', 'MAX_TOKENS_PER_UTF8_BYTE',
    'TEMPLATE_PROBE_TEXT', 'DEFAULT_TEMPLATE_OVERHEAD_TOKENS',
    'DEFAULT_MAX_OUTPUT_TOKENS', 'TOKENIZE_PATH',
    'APPLY_TEMPLATE_PATH', 'PROPS_PATH', 'run_fingerprint',
    'proven_overhead_tokens', 'fit_report', 'fit_report_bytes',
    'publish_binding', 'published_binding',
]

#: Provider id stamped into prepared records and returned by the registry.
PROVIDER_ID = 'local'
#: App-facing model id, in the reserved ``local:<server>:<model-tag>`` scheme.
MODEL_ID = 'local:llamacpp:qwen3-4b-instruct-2507-q4_k_m'
#: Operator-facing name (screen text is English per the project instructions).
DISPLAY_NAME = 'Local Qwen3-4B-Instruct-2507 (llama.cpp, Q4_K_M)'
#: Registry status when no loopback endpoint is configured. Never a ready token.
STATUS = ('local_llama_cpp_endpoint_not_configured; no supported loopback endpoint is '
          'configured, so no local inference is available; the API model stays the '
          'active path')
#: Status token for a configured endpoint. Reachability is still unproven: this
#: app performs no startup probe, so the honest report is "configured".
READY_STATUS = 'local_llama_cpp_endpoint_configured'
#: The one exact token that means "an explicit readiness check succeeded".
#: Emitted only by a successful probe (:meth:`provider.LocalProvider.probe`), so
#: the frontend's exact-equality ready gate can never light up from
#: configuration alone. Never a substring of a failure token.
READY_TOKEN = 'local_ready'
#: Registered status when no probe has run in this database yet. Distinct from
#: every failure token, so "not checked" never reads as "checked and failed".
NOT_PROBED_STATUS = 'local_probe_not_run'
#: Bodyless GET used by the explicit readiness probe (no content, no POST).
PROBE_PATH = '/v1/models'
#: Authenticated GET reporting the *observed* runtime identity: served alias and
#: the actual ``n_ctx`` the server is running with. Readiness and every prepared
#: job verify against this rather than trusting the declared configuration.
PROPS_PATH = '/props'
#: Token accounting endpoint. Called only after the researcher consented, since
#: its body carries the serialized prompt; it makes the exact-count check
#: possible instead of relying on any ratio.
TOKENIZE_PATH = '/tokenize'
#: Chat-template application endpoint: renders the exact prompt text the server
#: would use, so a template change cannot silently invalidate the byte bound.
APPLY_TEMPLATE_PATH = '/apply-template'
#: Single short probe deadline. One attempt, never retried.
PROBE_TIMEOUT = 5.0
#: Data-use label stamped on local jobs. Deliberately unlike the two cloud
#: labels, so a local job can never read as a Meta job.
DATA_USE = ('Local compute only: this app sends the request to a loopback server and '
            'no cloud provider; local compute is not metered as spend')
#: Cost basis recorded on every local stage. Zero API cost is a fact about this
#: path (no provider bill exists); it is not a claim about electricity.
COST_BASIS = 'zero_api_cost_local_compute_not_metered'
#: The text-reasoning tasks the design scopes local mode to. Image,
#: transcription and segmentation tasks stay API-only: this server is chat-only.
#: ``claim_check`` joins them because it is the same shaped task — one prompt, one
#: strict JSON answer — and a local run is a free pre-screen the researcher can
#: take before consenting to a Baseten call. It inherits every bound below
#: unchanged: the declared launcher context, the measured template overhead, the
#: output reservation, the exact post-consent re-check, and no truncation.
ELIGIBLE_TASKS = ('research_query', 'literature_analysis', 'claim_check')
#: Recorded install facts for the weights this contract expects. The size and
#: SHA-256 are *verified* before every launch (:mod:`app.ai.local_launcher`):
#: launching a different file under the pinned alias would answer as the model
#: while weighing something else. Digest computed from the installed file on
#: 2026-10-27 and confirmed by the independent reviewer.
MODEL_FACTS = {
    'repository': 'unsloth/Qwen3-4B-Instruct-2507-GGUF',
    'file': 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    'revision': 'a06e946bb6b655725eafa393f4a9745d460374c9',
    'bytes': 2497281120,
    'sha256': '3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597',
    'license': 'apache-2.0',
    'runtime': 'llama.cpp b10621 (Metal)',
}
#: llama-server's documented default listen port and default bind host.
DEFAULT_BASE_URL = 'http://127.0.0.1:9931'
DEFAULT_WIRE_MODEL = 'qwen3-4b-instruct-2507-q4_K_M'
DEFAULT_CONTEXT = 8192
#: Documented llama.cpp server chat path this app is allowed to touch.
CHAT_PATH = '/v1/chat/completions'
#: Largest number of tokens one UTF-8 byte can become. This build's model is a
#: byte-level BPE tokenizer with byte fallback: every vocabulary entry decodes to
#: at least one byte (a byte that matches no merged token becomes its own
#: single-byte token), and the encoder consumes the input left to right, so no
#: text can yield more tokens than it has UTF-8 bytes. Hence
#: ``utf8_len * MAX_TOKENS_PER_UTF8_BYTE`` is a *proven upper bound* on the token
#: count for any text in any language — including Korean, where one Hangul
#: syllable is three bytes and therefore costs at most three tokens. This
#: replaced the earlier ``CHARS_PER_TOKEN = 3`` heuristic, which was not a bound
#: at all: it undercounted non-Latin text (a reviewer measured 24576 Korean
#: characters becoming 16125 tokens, i.e. 0.66 tokens per character, twice the
#: assumed 0.33).
MAX_TOKENS_PER_UTF8_BYTE = 1
#: The synthetic text whose token count is measured at launch to observe the
#: chat template's real overhead. Deliberately ASCII, short and fixed: it is
#: never researcher text, so measuring it can happen before any consent.
TEMPLATE_PROBE_TEXT = 'ok'
#: Fallback template/special-token overhead used only when the launch-time
#: measurement could not run. Generous on purpose: an actual Qwen-style ChatML
#: wrapper around one user turn costs on the order of tens of tokens, and this
#: is a *reserve*, so over-reserving can only refuse a request, never truncate
#: one. The measured value replaces it whenever a launch measured one.
DEFAULT_TEMPLATE_OVERHEAD_TOKENS = 256
#: Reserved output tokens when a caller does not ask for a specific maximum. The
#: prompt bound must leave room for the reply, otherwise a full-context prompt
#: leaves the model no space to answer and the server truncates or refuses.
#: This is the ONE output cap for the local path: ``local_profile`` reads it
#: rather than declaring a second number, so the budget a preview states and the
#: cap a request actually carries cannot drift apart.
DEFAULT_MAX_OUTPUT_TOKENS = 1024
#: Loopback only: no DNS names, no LAN or public addresses, no userinfo.
LOOPBACK_HOSTS = frozenset(('127.0.0.1', '::1', 'localhost'))


def loopback_origin(value):
    """Return the normalized loopback origin, or None when it is not allowed.

    http scheme only, bare loopback host, optional port, no path, query,
    fragment, credentials, or wildcard bind address. ``None`` means "not a
    supported local endpoint", never "assume a default".
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme != 'http' or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.hostname.lower() not in LOOPBACK_HOSTS:
        return None
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = '[::1]' if parts.hostname == '::1' else parts.hostname.lower()
    return 'http://%s:%d' % (host, port) if port else 'http://%s' % host


def _launched_process_alive(pid):
    """Whether the launched server process still exists. Never raises.

    A published binding lives in the API process's environment, so it would
    otherwise outlive the server it describes: after the model exited (a crash,
    a stop, a closed laptop) the variables would still be there and a preview
    would be offered for a runtime that is gone. The pid is a cheap local
    existence check, not an identity proof — identity is proved at execute time
    by the authenticated ``/props`` read, which no other process can answer
    without this run's bearer.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is not ours to signal: it is still a live process.
        return True
    except OSError:
        return False
    return True


def _configured_paths():
    """The private launcher configuration, or ``{}`` when absent or invalid.

    Imported lazily and defensively: this module must stay importable (and the
    whole app runnable) on a machine where no local runtime is configured, and a
    hand-broken configuration file must degrade to "unconfigured" rather than
    making the registry unimportable.
    """
    try:
        from . import local_config
        return local_config.read()
    except Exception:
        return {}


#: The in-process binding, for a supervisor whose API runs in the same process.
#:
#: The environment is the right carrier when the API is a *separate* child: the
#: supervisor hands it ``binding_environment()`` and nothing else in that process
#: tree can read it. In one process there is no such boundary, so exporting the
#: bearer to ``os.environ`` would hand it to every child this process later
#: spawns (a ``pdftotext`` or ``xcrun`` subprocess, for example) and to any code
#: that copies the environment. This store holds the same verified binding in a
#: module attribute instead, so the credential stays inside this process's own
#: objects and the environment stays credential-free.
#:
#: Only a supervisor calls :func:`publish_binding`, after it verified its own
#: child; :func:`read_config` consults this store before the environment and
#: then parses both by the same rules, so a hand-made store is exactly as
#: untrusted as a hand-made environment.
_BINDING = {}


def publish_binding(binding=None):
    """Publish (or, with ``None``, clear) this process's verified local binding.

    The argument is the mapping :func:`app.ai.local_launcher.binding_environment`
    returns: only a verified launch produces one. Returns the published mapping
    (never the caller's object) so a caller can assert what it published without
    touching the stored copy.
    """
    global _BINDING
    _BINDING = dict(binding or {})
    return dict(_BINDING)


def published_binding():
    """A copy of the published in-process binding, or ``{}``."""
    return dict(_BINDING)


def _binding_value(name, default=''):
    """One binding field: the in-process store first, then the environment.

    The store wins because it is what this process's own supervisor verified for
    the run it is serving; the environment is the separate-child carrier and the
    source of truth when a supervisor handed the binding over that way. Neither
    is trusted more than :func:`read_config` trusts any binding.
    """
    if name in _BINDING:
        value = _BINDING[name]
        return default if value is None else value
    return os.environ.get(name, default)


def read_config():
    """The *current run's* local binding. Never raises, never guesses.

    Sources, and why they are not interchangeable
    ---------------------------------------------
    The private configuration file supplies **only** what to launch: the two
    executable/weights paths and the launch parameters. It is deliberately NOT a
    binding: a saved file must never be enough to make a request, because a file
    outlives a process and says nothing about what is running right now. That was
    the defect where saved paths alone enabled ``prepare`` with no runtime — and
    would happily accept any unrelated listener that happened to own the port.

    The binding comes solely from what the supervisor publishes for this run,
    after it started and verified its own child: origin, alias, context, run id,
    the launched server's pid, the expected weights digest and the per-run
    bearer. A partial or hand-made environment is therefore not a binding.

    The bearer is in this dict because the transport must send it; nothing here
    is serialized to a client. Every recorded/displayed surface uses
    :func:`run_fingerprint` instead, so the secret itself never reaches a
    receipt, a preview, a log line or the registry snapshot.

    ``configured`` means "a binding from a verified launch exists". It does not
    claim the server is still reachable or still the same model;
    :meth:`LocalProvider.props` proves that at prepare and execute time.

    The fields come from :func:`_binding_value`, so a supervisor that keeps its
    API in one process can publish the binding without exporting the bearer to
    ``os.environ`` (see :func:`publish_binding`); a supervisor that runs the API
    as a child still hands it the environment. Both carriers are parsed here by
    exactly the same rules.
    """
    raw_base = _binding_value('WB_LOCAL_BASE_URL')
    origin = loopback_origin(raw_base)
    raw_model = _binding_value('WB_LOCAL_MODEL')
    wire_model = raw_model.strip() if isinstance(raw_model, str) else ''
    raw_context = _binding_value('WB_LOCAL_CONTEXT')
    try:
        declared = int(str(raw_context).strip()) if str(raw_context).strip() else 0
    except ValueError:
        declared = 0
    if declared <= 0:
        declared = 0
    run_id = (_binding_value('WB_LOCAL_RUN_ID') or '').strip()
    bearer = (_binding_value('WB_LOCAL_API_KEY') or '').strip()
    raw_pid = (_binding_value('WB_LOCAL_SERVER_PID') or '').strip()
    server_pid = int(raw_pid) if raw_pid.isdigit() and int(raw_pid) > 0 else 0
    weights_sha256 = (_binding_value('WB_LOCAL_WEIGHTS_SHA256') or '').strip().lower()
    raw_overhead = (_binding_value('WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS') or '').strip()
    overhead = int(raw_overhead) if raw_overhead.isdigit() else 0
    observed_alias = (_binding_value('WB_LOCAL_OBSERVED_ALIAS') or '').strip()
    raw_observed_ctx = (_binding_value('WB_LOCAL_OBSERVED_CTX') or '').strip()
    observed_ctx = int(raw_observed_ctx) if raw_observed_ctx.isdigit() else 0
    raw_observed_train = (_binding_value('WB_LOCAL_OBSERVED_CTX_TRAIN') or '').strip()
    observed_train = int(raw_observed_train) if raw_observed_train.isdigit() else 0
    reasons = []
    if not origin:
        reasons.append('local_endpoint_invalid' if raw_base.strip() else 'local_endpoint_unconfigured')
    if not wire_model:
        reasons.append('local_model_alias_unconfigured')
    if not declared:
        reasons.append('local_context_invalid')
    if not run_id or not server_pid:
        reasons.append('local_run_not_launched')
    elif not _launched_process_alive(server_pid):
        # Published bindings live in this process's environment and would
        # otherwise outlive the server they describe. Once the launched model has
        # exited, the honest state is "not running", so a preview is refused
        # instead of being offered for a runtime that is gone.
        reasons.append('local_run_process_exited')
    if not bearer:
        reasons.append('local_run_secret_missing')
    if weights_sha256 != MODEL_FACTS['sha256']:
        # The launch verifies the weights digest before spawning, so a published
        # binding always carries the pinned value. Accepting an absent or
        # arbitrary digest here would let a hand-edited environment claim a
        # launch over unverified weights: whatever file the server loaded would
        # then answer under the pinned alias. Absent is not "trust the launch";
        # it is unproven, and unproven is refused.
        reasons.append('local_weights_digest_mismatch')
    if not overhead:
        # The launch measures the template overhead; a binding without it cannot
        # state a proven prompt bound, so it is not usable rather than guessed.
        reasons.append('local_template_overhead_unmeasured')
    if not observed_alias or not observed_ctx:
        # The launcher publishes what the server reported at readiness. Without
        # that, this would be a claim about launch intent rather than about the
        # loaded model, and an unrelated listener could stand in for it.
        reasons.append('local_runtime_identity_unobserved')
    elif (observed_alias != wire_model) or (declared and observed_ctx != declared):
        # Values that disagree cannot both describe one server. A verified launch
        # never publishes this (it compares the two before publishing), so the
        # only way here is a hand-assembled environment — not a binding.
        reasons.append('local_runtime_identity_mismatch')
    return {'origin': origin,
            'endpoint_invalid': bool(raw_base.strip()) and origin is None,
            'wire_model': wire_model or None,
            'context': declared or None,
            'context_configured': bool(raw_context.strip()),
            'observed_model_alias': observed_alias or None,
            'observed_context': observed_ctx or None,
            'observed_context_train': observed_train or None,
            'run_id': run_id or None,
            'bearer': bearer or None,
            'server_pid': server_pid or None,
            'weights_sha256': weights_sha256 or None,
            'template_overhead_tokens': overhead or None,
            'configured': not reasons,
            'blocking_reasons': reasons,
            'chat_path': CHAT_PATH,
            # The provable prompt budget in bytes for this binding. Zero means
            # nothing can be sent at all, not "send anyway".
            'max_prompt_bytes': prompt_budget_bytes(declared, overhead) if declared and overhead else 0}


def run_fingerprint(binding=None):
    """Short digest of the per-run bearer: identifies the run without the secret.

    Recorded in previews, the local binding and receipts so an audit can tie a
    job to the launch that served it, while the bearer itself stays only in this
    process's memory. Returns None when no run is bound.
    """
    bearer = (binding or read_config()).get('bearer')
    if not bearer:
        return None
    return hashlib.sha256(bearer.encode('utf-8')).hexdigest()[:16]


#: Operator-facing text for each blocking reason. Each sentence states what is
#: missing and what still works, never a cloud fallback.
BLOCKING_MESSAGES = {
    'local_endpoint_unconfigured': (
        'Local mode is not configured: no local runtime is set up for this app yet. '
        'Set the installed llama-server and model paths once in the local runtime '
        'settings; editing and saved work stay available meanwhile, and no local '
        'request is sent.'),
    'local_endpoint_invalid': (
        'Local mode is not available: the configured local endpoint must be an http '
        'loopback origin such as http://127.0.0.1:9931, with no path, credentials or '
        'other host.'),
    'local_model_alias_unconfigured': (
        'Local mode is not configured: the server alias this app must ask for is '
        'unknown, so no local request is sent.'),
    'local_context_invalid': (
        'Local mode is not configured: the declared context must be the positive -c '
        'value the server is launched with, so the prompt bound is a real one.'),
    'local_template_overhead_unmeasured': (
        'Local mode is not available for this launch: the chat template overhead was not '
        'measured when the app started the model, so the prompt budget cannot be stated '
        'as a proven bound. Restart the app with the local runtime option.'),
    'local_runtime_identity_unobserved': (
        'Local mode is not available for this launch: the model server did not report its '
        'own alias and context when the app started it, so this app cannot prove it is '
        'still talking to the model it launched. Restart the app with the local runtime '
        'option.'),
    'local_runtime_identity_mismatch': (
        'Local mode is not available: the model server reported a different alias or '
        'context than the one this app launched it with, so no request is sent to it. '
        'Restart the app with the local runtime option.'),
    'local_run_not_launched': (
        'Local mode is not running: this app has not launched a local model in this session. '
        'Start the app with the local runtime option so it can start and verify the model it '
        'then talks to; editing and saved work stay available meanwhile.'),
    'local_run_secret_missing': (
        'Local mode is not available: the per-run local credential this app generates at '
        'launch is missing, so a request would reach an unverified server. Restart the app '
        'with the local runtime option.'),
    'local_run_process_exited': (
        'Local mode is not running: the local model this app started is no longer running. '
        'Restart the app with the local runtime option; editing and saved work stay available '
        'meanwhile, and no local request is sent.'),
    'local_weights_digest_mismatch': (
        'Local mode is not available: the model weights this session was launched with do not '
        'match the pinned file this app verifies, so no request is sent. Restart the app with '
        'the local runtime option.'),
}


def blocking_message(config):
    """First human-readable reason a local request cannot be prepared.

    When install paths are recorded but nothing was launched, the honest reason
    is "not running", not "not configured": the operator has already done the
    setup step, so telling them to set paths again would send them in a circle.
    """
    if config['configured']:
        return None
    reasons = config['blocking_reasons']
    if 'local_run_not_launched' in reasons and _configured_paths():
        return BLOCKING_MESSAGES['local_run_not_launched']
    return BLOCKING_MESSAGES.get(reasons[0],
                                 'Local mode is not available on this machine')


def connection_status():
    """Launch-binding readiness only. Proves nothing about the running server.

    The per-run bearer is never included: not here, not in the receipt, not in
    any surface a renderer can read. Only its presence is implied by the binding
    being configured.
    """
    config = read_config()
    return {'id': PROVIDER_ID, 'provider': PROVIDER_ID,
            'display_name': DISPLAY_NAME, 'model_id': MODEL_ID,
            'base_url': config['origin'],
            'endpoint_configured': bool(config['origin']),
            'wire_model': config['wire_model'],
            'context': config['context'],
            'observed_model_alias': config['observed_model_alias'],
            'observed_context': config['observed_context'],
            'observed_context_train': config['observed_context_train'],
            'max_prompt_bytes': config['max_prompt_bytes'],
            'template_overhead_tokens': config['template_overhead_tokens'],
            'launch_run': config['run_id'],
            'launch_server_pid': config['server_pid'],
            'run_fingerprint': run_fingerprint(config),
            'weights_sha256': config['weights_sha256'],
            'transport': 'openai_chat_completions_loopback_authenticated',
            'billing': 'zero_api_cost_local_compute_not_metered',
            'prerequisites_ready': config['configured'],
            'account_verified': 'NOT_APPLICABLE_LOCAL',
            # Never the ready token: this row describes a launched, bound run;
            # the ready token comes only from a successful authenticated probe.
            'status': (READY_STATUS + '; launched_and_bound_run; explicit_probe_required'
                       if config['configured'] else STATUS),
            'blocking_reasons': config['blocking_reasons'],
            'model_facts': dict(MODEL_FACTS),
            'launch_command': launch_command()}


def launch_command():
    """The exact command the supervisor builds. Recorded for audit, not run here.

    Includes the hardening flags this app requires of the runtime it launches:

    * ``--no-webui`` — no browser surface over the model;
    * ``--cors-origins <app origin>`` — exactly one origin, never ``*``. This
      app's frontend never calls the model port directly (the API process does,
      on its behalf), so a launch with no declared app origin names the server's
      own loopback origin, which no page can have. The reviewer flagged that
      ``""`` has unverified semantics in this build, so no behavior is assumed
      from an empty value;
    * ``--no-cors-credentials`` — the pair that stops a web page on this machine
      from driving the model through a browser;
    * ``--no-context-shift`` — an over-long prompt is a hard server error, never
      a silently rotated window;
    * ``--api-key-file`` — the per-run bearer comes from an owner-only temporary
      file, never from argv (which any local process can read).
    """
    return ('llama-server -m %s --alias %s --host 127.0.0.1 --port %d -c %d '
            '--no-webui --cors-origins <app origin> --no-cors-credentials '
            '--no-context-shift --api-key-file <per-run owner-only temp file>'
            % (MODEL_FACTS['file'], DEFAULT_WIRE_MODEL,
               int(DEFAULT_BASE_URL.rsplit(':', 1)[1]), DEFAULT_CONTEXT))


def configured_context():
    """The declared context bound, for the registry row's ``context`` field.

    Falls back to the documented default when unset so the row is never
    context-free; an invalid value yields the default here and blocks readiness
    in :func:`read_config` instead of silently widening the bound.
    """
    return read_config()['context'] or DEFAULT_CONTEXT


def chat_endpoint():
    """The exact loopback URL the transport posts to, or None when unconfigured.

    This is the registry row's ``endpoint``. The frontend treats a row with an
    endpoint as runnable, so it is emitted only when the whole contract is
    configured (loopback origin, wire alias, declared context, no unsupported
    credential): a half-configured local row stays unrunnable rather than being
    offered in the model picker.
    """
    config = read_config()
    if not config['configured']:
        return None
    return config['origin'] + CHAT_PATH


def model_status():
    """Registry status text for the local row, derived from the live binding.

    Deliberately never reads ``local_ready``: this row is a claim about a launch,
    not a probe result. A saved configuration with no launched run is reported as
    ``local_runtime_not_launched`` so the row cannot look runnable merely because
    paths were once recorded.

    The wording is exact about *when* identity is verified. The launcher verifies
    the server's own alias/context at startup and again at execute; ``prepare``
    does not probe the server (it must not, since before consent only a
    content-free check would be permissible and the preview's job is the bound).
    So the token says ``identity_verified_at_launch_and_execute`` and does not
    claim a prepare-time verification that never happens.
    """
    config = read_config()
    if config['configured']:
        return ('local_llama_cpp_launched_run_bound; app_owned_loopback_llama_server; '
                'authenticated_per_run; zero_api_cost_local_compute_not_metered; '
                'identity_verified_at_launch_and_execute; prompt_bound_proven_from_'
                'utf8_bytes_with_output_and_template_reserved')
    if _configured_paths():
        # Paths are recorded but nothing is running: not runnable, and said so.
        return ('local_runtime_not_launched; install paths are recorded but no local '
                'server was launched in this session, so no local request can be made')
    return STATUS


def proven_overhead_tokens(binding=None):
    """The template/special-token overhead to reserve, measured when possible.

    Read from the run binding the launcher published after it measured the
    real chat template with a fixed synthetic probe string. Falls back to
    :data:`DEFAULT_TEMPLATE_OVERHEAD_TOKENS` only for a caller that has no
    binding at all (a preview computed before a launch, for display); a
    *usable* binding always carries a measured value, and ``read_config`` treats
    a missing one as a blocking reason rather than guessing.
    """
    measured = (binding or read_config()).get('template_overhead_tokens')
    if isinstance(measured, int) and measured > 0:
        return measured
    return DEFAULT_TEMPLATE_OVERHEAD_TOKENS


def prompt_budget_bytes(context, overhead_tokens=None, max_output_tokens=None):
    """Largest serialized prompt, in UTF-8 bytes, that provably fits ``context``.

    Derived, not estimated. Output tokens and the measured chat-template
    overhead are reserved first; the remainder is divided by
    :data:`MAX_TOKENS_PER_UTF8_BYTE`, the proven maximum number of tokens one
    byte can produce. A zero result means "nothing can be sent", never "send it
    and hope". Each reserved term is an actual subtraction in the code below —
    there is no unstated slack, because a reserve nobody subtracts is a claim
    the bound cannot honour.

    The bytes counted are the *decoded prompt text* — the message content the
    server's tokenizer consumes — not the JSON envelope that carries it. JSON
    escaping can only enlarge the request on the wire, and the wire size is not
    what competes for the context window, so counting the envelope would make the
    bound wrong in the wrong direction (it could refuse text that fits).
    """
    if not isinstance(context, int) or context <= 0:
        return 0
    reserve_out = (max_output_tokens if isinstance(max_output_tokens, int)
                   and max_output_tokens > 0 else DEFAULT_MAX_OUTPUT_TOKENS)
    reserve_template = (overhead_tokens if isinstance(overhead_tokens, int)
                        and overhead_tokens >= 0 else proven_overhead_tokens())
    available = context - reserve_out - reserve_template
    if available <= 0:
        return 0
    return available // MAX_TOKENS_PER_UTF8_BYTE


def fit_report_bytes(utf8_bytes, context, overhead_tokens=None, max_output_tokens=None):
    """The bound decision from an already-counted byte total.

    Split out because the preview sometimes knows the exact prompt (a measured
    body) and sometimes only a proven upper bound on part of it (the harness
    extras the model has not produced yet). Both are byte totals; the decision
    is the same arithmetic.
    """
    reserve_out = (max_output_tokens if isinstance(max_output_tokens, int)
                   and max_output_tokens > 0 else DEFAULT_MAX_OUTPUT_TOKENS)
    reserve_template = (overhead_tokens if isinstance(overhead_tokens, int)
                        and overhead_tokens >= 0 else proven_overhead_tokens())
    budget = prompt_budget_bytes(context, reserve_template, reserve_out)
    if not isinstance(utf8_bytes, int):
        return {'fits': False, 'prompt_utf8_bytes': None, 'max_prompt_bytes': budget,
                'reserved_output_tokens': reserve_out,
                'template_overhead_tokens': reserve_template, 'context': context}
    return {'fits': budget > 0 and utf8_bytes <= budget,
            'prompt_utf8_bytes': utf8_bytes,
            'max_prompt_bytes': budget,
            'reserved_output_tokens': reserve_out,
            'template_overhead_tokens': reserve_template,
            'context': context}


def fit_report(text, context, overhead_tokens=None, max_output_tokens=None):
    """The bound decision plus the numbers behind it, for previews and receipts.

    Returns a dict so the researcher-facing preview and the recorded receipt can
    state the same evidence: the prompt's UTF-8 byte count, the byte budget, the
    reserved output and the measured template overhead. Never includes text
    beyond the counts.
    """
    try:
        encoded = len(text.encode('utf-8'))
    except (AttributeError, UnicodeError):
        encoded = None
    return fit_report_bytes(encoded, context, overhead_tokens, max_output_tokens)


def prompt_fits(text, context, overhead_tokens=None, max_output_tokens=None):
    """Whether ``text`` provably fits in ``context``, output and template reserved.

    The check is on the actual UTF-8 encoding, so multi-byte scripts are counted
    honestly: 24576 characters of Korean is 73728 bytes, and the budget is in
    bytes, not in an assumed characters-per-token ratio.
    """
    return fit_report(text, context, overhead_tokens, max_output_tokens)['fits']
