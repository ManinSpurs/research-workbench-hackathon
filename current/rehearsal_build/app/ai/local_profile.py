"""L3b: LOCAL_SMALL_PROFILE tuning for ~4B-class local-model prepares.

STATUS: profile module + tests only (service.py wiring is L3's scope; the
exact wiring point is listed below so L3 can consume this unchanged).
Mock/stub only: pure data + pure functions. No transport, no model call, no
spend/receipt change, no live/API call, no install, no download.

Placement: beside the registry/provider code (`app/ai/`), NOT inside
`service.py`, for two reasons: (1) the design
(`current/LOCAL_FREE_MODE_DESIGN_EN.md` §3.3) describes the tuning as living
in "the local branch of service.py", but that branch does not exist yet and
`service.py` wiring collides with the parallel L3 routing work — a
standalone import-safe module keeps L3b independent; (2) the profile must
never import `service` (L3's `prepare()` will import this module, so the
dependency runs one way only and no import cycle is possible).

Consumption contract for L3 (exact wiring point, `app/ai/service.py`):
- `prepare()`: the local branch replacing the line-214 fail-closed
  `if req.mode!='cloud': raise Conflict(...)` calls
  `constrain_prepare_request(req.model_dump())` and stores the returned
  `local_profile: {name, version}` identity in the prepare record built at
  line 341 — the profile is part of the local prepare record from the start,
  never a post-hoc rewrite. Cloud prepares (`mode != 'local'`) must never
  call it: `constrain_prepare_request` raises `ValueError` fail-closed on
  non-local input, and whole-record immutability for cloud jobs is untouched.
- Contract binding: `contract_fingerprint(service.WIRE_VERSION)` replaces the
  plain `WIRE_VERSION` in the line-341 `contract_sha256` digest for local
  records only, so the existing line-406 check fails closed on a
  cloud/local contract swap. Cloud records keep the unchanged digest.
- Repair loop: `repair_allowed(prior_repairs)` bounds the in-stage JSON
  re-prompt inside `invoke()` (line 549), ledgered at $0.
- Chunking/temperature/single-arm: `truncate_snapshot_text`,
  `PROFILE.temperature`, `PROFILE.default_harness` apply in `_body()` (line
  355) and arm selection for the local branch only.

Numbers provenance (each value cites its source; assumptions flagged):
- name/version/fingerprint `local-small-1`: recorded in the design §3.3
  item 1 example fingerprint `rw-meta-strict-1+local-small-1`.
- max_json_repairs = 1: recorded in the design §3.3 item 2
  ("max 1 re-prompt ... reusing the existing stages attempt ledger").
- truncation_marker `TRUNCATED_FOR_LOCAL_CONTEXT`: exact marker text
  recorded in the design §3.3 item 4 ("never silent truncation").
- temperature = 0.0: the FLOOR of the design §3.3 item 5 recorded range
  0.0–0.2 (exact value marked [UNVERIFIED] in the design; the range itself
  is recorded, the endpoint choice is the deterministic one).
- single-arm default (`default_harness=False`, compare allowed but off by
  default): recorded in the design §3.3 item 3 ("single-call
  (harness=False-equivalent, basic arm only) ... allowed but discouraged by
  default").
- structured-output-first + strict JSON: recorded in the design §3.3 item 2
  (keep `text.format.json_schema` enforcement, `strict=True`) and the
  MUST-NOT list (strict JSON-schema validation is never weakened).
- [ASSUMPTION — Lead sign-off pending] max_output_tokens = 1024: the design
  records NO number ("reduced max output tokens", §3.3 item 1, no value).
  Per the task brief this falls back to code-derived limits: the cloud
  default is `Request.max_output_tokens = 2048` (service.py line 34,
  allowed range 128–4096), so the profile pins HALF the cloud default,
  inside the allowed range. The value lives in ONE place —
  `local_runtime.DEFAULT_MAX_OUTPUT_TOKENS` — because the prompt budget
  subtracts that same number; declaring it twice would let the preview's
  stated bound and the request's actual cap drift apart. Tests pin 1024.
- context_budget_tokens: NOT a pinned constant. It is a live property that
  reads `local_runtime.configured_context()`, i.e. the `-c` value the
  operator actually launched `llama-server` with (default 8192). The earlier
  1048576 value was the cloud registry default and is explicitly wrong for a
  4B Q4_K_M runtime: the model's native training context is not a guarantee
  the running server honours, so the budget must be the declared runtime
  bound and nothing larger.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import local_runtime

__all__ = [
    'LocalSmallProfile',
    'PROFILE',
    'FINGERPRINT_SUFFIX',
    'contract_fingerprint',
    'is_local_request',
    'constrain_prepare_request',
    'truncate_snapshot_text',
    'repair_allowed',
]


@dataclass(frozen=True)
class LocalSmallProfile:
    """Named, importable tuning profile for local ~4B-class prepares."""
    name: str = 'local-small'
    version: str = '1'
    # The output cap is NOT declared here: it is `local_runtime`'s one constant,
    # because that is also the number the prompt budget subtracts. Two numbers
    # would let the budget a preview states and the cap a request carries drift
    # apart, which would make the stated bound wrong.
    @property
    def max_output_tokens(self) -> int:
        return local_runtime.DEFAULT_MAX_OUTPUT_TOKENS

    @property
    def context_budget_tokens(self) -> int:
        """The declared runtime context bound, read live (never a constant).

        The app must enforce the context the running server was launched with,
        not the model's much larger native training context: the latter is not
        a guarantee any actual 4B Q4_K_M runtime honours. Read per access so a
        re-launched server with a different ``-c`` is reflected immediately.
        """
        return local_runtime.configured_context()
    # Floor of the design-recorded 0.0–0.2 range (exact value UNVERIFIED).
    temperature: float = 0.0
    # Recorded in design §3.3 item 2: max 1 re-prompt per stage attempt.
    max_json_repairs: int = 1
    # Exact marker text recorded in design §3.3 item 4.
    truncation_marker: str = 'TRUNCATED_FOR_LOCAL_CONTEXT'
    # Design §3.3 item 3: single-call default; compare allowed, off default.
    default_harness: bool = False
    allow_compare: bool = True
    default_compare: bool = False
    # Design §3.3 item 2 + MUST-NOT list: strict structured output stays on.
    structured_output_first: bool = True
    strict_json: bool = True


#: The single canonical instance L3 wires into local prepares.
PROFILE = LocalSmallProfile()

#: Fingerprint suffix; the design's example contract id is
#: `rw-meta-strict-1+local-small-1`.
FINGERPRINT_SUFFIX = f'{PROFILE.name}-{PROFILE.version}'


def contract_fingerprint(wire_version: str) -> str:
    """Local-branch contract id: `<WIRE_VERSION>+local-small-1`.

    Takes `wire_version` as an argument (pass `service.WIRE_VERSION`) so
    this module never imports `service` — the dependency runs L3 -> this
    module only. Local records stamp this; cloud records keep the plain
    wire version, so the existing contract check fails closed on swaps.
    """
    return f'{wire_version}+{FINGERPRINT_SUFFIX}'


def is_local_request(request: dict) -> bool:
    """True only for local-mode prepare requests. Anything else (cloud jobs
    included) is untouched by this profile."""
    return isinstance(request, dict) and request.get('mode') == 'local'


def constrain_prepare_request(request: dict) -> dict:
    """Apply LOCAL_SMALL_PROFILE to a local prepare request.

    Returns a NEW dict (the input is never mutated — whole-record
    immutability: the profile is part of the local prepare record from the
    start, not a post-hoc rewrite). Raises `ValueError` fail-closed for any
    non-local input so cloud records can never be stamped by accident.
    """
    if not is_local_request(request):
        raise ValueError(
            'LOCAL_SMALL_PROFILE applies to local-mode prepares only; '
            'cloud prepare records are immutable and must never be stamped'
        )
    out = dict(request)
    try:
        wanted = int(request.get('max_output_tokens', PROFILE.max_output_tokens))
    except (TypeError, ValueError):
        wanted = PROFILE.max_output_tokens
    out['max_output_tokens'] = min(wanted, PROFILE.max_output_tokens)
    out['temperature'] = PROFILE.temperature
    out['harness'] = bool(request.get('harness', PROFILE.default_harness))
    out['compare'] = bool(request.get('compare', PROFILE.default_compare))
    out['local_profile'] = {'name': PROFILE.name, 'version': PROFILE.version}
    return out


def truncate_snapshot_text(text: str, max_chars: int,
                           marker: str | None = None) -> tuple[str, bool]:
    """Cap serialized snapshot text for the local context budget.

    Returns `(possibly_truncated_text, was_truncated)`. Truncation is never
    silent: over-budget text is cut at `max_chars` with the explicit design
    marker appended. Under-budget text returns byte-identical with `False`.
    """
    marker = PROFILE.truncation_marker if marker is None else marker
    if not isinstance(text, str):
        raise ValueError('snapshot text must be a string')
    if not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError('max_chars must be a non-negative int')
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + marker, True


def repair_allowed(prior_repairs: int) -> bool:
    """Bounded JSON-repair loop gate: at most `max_json_repairs` (1)
    re-prompts inside the same stage attempt, ledgered at $0."""
    if not isinstance(prior_repairs, int) or prior_repairs < 0:
        raise ValueError('prior_repairs must be a non-negative int')
    return prior_repairs < PROFILE.max_json_repairs
