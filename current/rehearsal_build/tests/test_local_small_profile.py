"""L3b: LOCAL_SMALL_PROFILE tests. Mock/stub only — pure data + pure
functions; no transport, no provider, no service import, no live/API call.

Numbers provenance:
- name 'local-small', version '1', fingerprint `rw-meta-strict-1+local-small-1`,
  max_json_repairs 1, TRUNCATED_FOR_LOCAL_CONTEXT marker, temperature floor
  0.0 of the recorded 0.0–0.2 range, single-arm/structured-output-first
  defaults: recorded in current/LOCAL_FREE_MODE_DESIGN_EN.md §3.3 items 1–5.
- [ASSUMPTION — Lead sign-off pending] max_output_tokens 1024 = half the
  cloud default 2048 (service.py Request.max_output_tokens, allowed range
  128–4096): design records no number. Pinned here so drift fails loudly.
- [ASSUMPTION — Lead sign-off pending] context_budget_tokens == registry
  local-row context (registry.py local row): design records no tightened N.
  Pinned against the LIVE registry row so a registry change fails loudly.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import local_profile, local_runtime, registry as registry_module
from app.ai.local_profile import (
    FINGERPRINT_SUFFIX,
    PROFILE,
    constrain_prepare_request,
    contract_fingerprint,
    is_local_request,
    repair_allowed,
    truncate_snapshot_text,
)

ROOT = Path(__file__).resolve().parents[1]
SERVICE_SRC = (ROOT / 'app' / 'ai' / 'service.py').read_text()
WIRE_VERSION = re.search(r"^WIRE_VERSION='([^']+)'", SERVICE_SRC, re.M).group(1)
CLOUD_DEFAULT_MAX_OUT = int(re.search(
    r"max_output_tokens:int=Field\(default=(\d+),ge=128,le=4096\)",
    SERVICE_SRC).group(1))


def test_profile_identity_recorded_name_version():
    # Design §3.3 item 1: fingerprint example `rw-meta-strict-1+local-small-1`.
    assert PROFILE.name == 'local-small'
    assert PROFILE.version == '1'
    assert FINGERPRINT_SUFFIX == 'local-small-1'
    assert contract_fingerprint(WIRE_VERSION) == (
        WIRE_VERSION + '+local-small-1'
    )
    assert contract_fingerprint(WIRE_VERSION) != WIRE_VERSION
    # Cloud fingerprint input is service-independent: recorded suffix holds
    # for the live WIRE_VERSION string too.
    assert contract_fingerprint('rw-meta-strict-1') == 'rw-meta-strict-1+local-small-1'
    assert WIRE_VERSION == 'rw-meta-strict-1'


def test_design_recorded_values_pinned():
    # Every design-recorded number lives here verbatim (design §3.3):
    # max 1 re-prompt (item 2), exact truncation marker (item 4),
    # temperature floor of the recorded 0.0–0.2 range (item 5, exact value
    # UNVERIFIED — the floor is the deterministic endpoint), single-call
    # default + compare-allowed-but-off (item 3), structured-output-first
    # + strict JSON (item 2 + MUST-NOT list).
    assert PROFILE.max_json_repairs == 1
    assert PROFILE.truncation_marker == 'TRUNCATED_FOR_LOCAL_CONTEXT'
    assert PROFILE.temperature == 0.0
    assert 0.0 <= PROFILE.temperature <= 0.2
    assert PROFILE.default_harness is False
    assert PROFILE.allow_compare is True
    assert PROFILE.default_compare is False
    assert PROFILE.structured_output_first is True
    assert PROFILE.strict_json is True
    # The marker also governs the default truncation path.
    text = 'x' * 100
    cut, flag = truncate_snapshot_text(text, 10)
    assert flag is True
    assert cut == 'x' * 10 + 'TRUNCATED_FOR_LOCAL_CONTEXT'
    under, flag2 = truncate_snapshot_text(text, 100)
    assert flag2 is False and under == text


def test_assumed_numbers_sourced_not_invented(monkeypatch):
    # Deterministic: the declared-context reader must not see this machine's
    # own WB_LOCAL_* settings.
    for name in ('WB_LOCAL_BASE_URL', 'WB_LOCAL_MODEL', 'WB_LOCAL_CONTEXT',
                 'WB_LOCAL_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    # [ASSUMPTION] max_output_tokens 1024 = half the cloud default:
    # Request.max_output_tokens default is 2048, allowed range 128–4096
    # (service.py). Inside the range, strictly below the cloud default.
    assert CLOUD_DEFAULT_MAX_OUT == 2048
    assert PROFILE.max_output_tokens == CLOUD_DEFAULT_MAX_OUT // 2 == 1024
    assert 128 <= PROFILE.max_output_tokens < CLOUD_DEFAULT_MAX_OUT <= 4096
    # context budget is the DECLARED runtime bound, read live — never the
    # cloud registry default and never the 4B model's native training context.
    # An unconfigured machine reports the documented default 8192.
    local_row = registry_module.MODELS[local_runtime.MODEL_ID]
    assert PROFILE.context_budget_tokens == local_row.context
    assert PROFILE.context_budget_tokens == 8192
    assert PROFILE.context_budget_tokens < 1048576
    assert local_row.input_price == 0 and local_row.output_price == 0


def test_context_budget_follows_declared_runtime_bound(monkeypatch):
    # The budget tracks the same -c value the transport enforces: a re-launched
    # server with a different declared context moves the budget with it, so the
    # profile can never keep enforcing a stale (or larger) bound.
    monkeypatch.setenv('WB_LOCAL_CONTEXT', '4096')
    assert PROFILE.context_budget_tokens == 4096
    assert PROFILE.context_budget_tokens == local_runtime.configured_context()
    monkeypatch.setenv('WB_LOCAL_CONTEXT', 'not-a-number')
    # Invalid declaration falls back to the documented default and blocks
    # readiness, rather than silently widening the enforced bound.
    assert PROFILE.context_budget_tokens == local_runtime.DEFAULT_CONTEXT


def test_local_prepare_record_carries_profile_identity():
    base = {'task': 'research_query', 'model': 'auto', 'mode': 'local',
            'max_output_tokens': 4096}
    out = constrain_prepare_request(base)
    # Identity stamped on the record so receipts stay honest.
    assert out['local_profile'] == {'name': 'local-small', 'version': '1'}
    # Output cap enforced even when the request asks higher.
    assert out['max_output_tokens'] == 1024
    # Request asking below the cap keeps its lower value (cap, not floor).
    low = constrain_prepare_request({**base, 'max_output_tokens': 512})
    assert low['max_output_tokens'] == 512
    # Single-arm default + temperature pin.
    assert out['harness'] is False
    assert out['compare'] is False
    assert out['temperature'] == 0.0
    # Input never mutated: the profile is part of the record from the start.
    assert base == {'task': 'research_query', 'model': 'auto', 'mode': 'local',
                    'max_output_tokens': 4096}
    assert 'local_profile' not in base


def test_cloud_prepare_records_unaffected_fail_closed():
    # Cloud records are never stamped: non-local input raises, never rewrites.
    cloud = {'task': 'research_query', 'model': 'auto', 'mode': 'cloud',
             'max_output_tokens': 2048}
    try:
        constrain_prepare_request(cloud)
    except ValueError:
        pass
    else:
        raise AssertionError('cloud request must fail closed')
    assert cloud == {'task': 'research_query', 'model': 'auto', 'mode': 'cloud',
                     'max_output_tokens': 2048}
    assert is_local_request(cloud) is False
    assert is_local_request({'mode': 'local'}) is True
    assert is_local_request({}) is False
    assert is_local_request(None) is False
    # Cloud contract fingerprint stays the plain wire version.
    assert WIRE_VERSION == 'rw-meta-strict-1'
    assert 'local-small' not in WIRE_VERSION
    # Cloud instructions in the live service source carry no local tuning:
    # SAFE_CORE / FINAL_CONTRACT / FRAME_CONTRACT text is unchanged and no
    # local-small marker exists anywhere in service.py.
    assert 'local-small' not in SERVICE_SRC.lower()
    # The profile IS wired now (L3), but only inside the local branch: the
    # call site must be guarded by the local provider check, so a cloud
    # prepare can never reach it. Pinned as source shape because the guard is
    # what makes the fail-closed ValueError unreachable from cloud mode.
    call = 'req_dict=local_profile.constrain_prepare_request(req_dict)'
    assert call in SERVICE_SRC
    before = SERVICE_SRC.split(call)[0]
    assert before.rsplit('if provider_name==LOCAL:', 1)[1].count('if ') <= 1
    # Cloud tuning constants stay out of the shared instruction text.
    for marker in ('TRUNCATED_FOR_LOCAL_CONTEXT', 'max_output_tokens=1024'):
        assert marker not in SERVICE_SRC


def test_repair_loop_bounded_at_one():
    # Design §3.3 item 2: max 1 re-prompt inside the same stage attempt.
    assert repair_allowed(0) is True
    assert repair_allowed(1) is False
    assert repair_allowed(7) is False
    try:
        repair_allowed(-1)
    except ValueError:
        pass
    else:
        raise AssertionError('negative repair count must fail')


def test_profile_module_has_no_service_import():
    # One-way dependency: L3's service.py imports this module, never the
    # reverse — so wiring the profile into prepare() cannot create a cycle.
    import inspect
    src = inspect.getsource(local_profile)
    assert 'from .service' not in src
    assert 'from app.ai.service' not in src
    assert 'import service' not in src
