"""L1: local registry entries only (id scheme, no runtime). No live/API calls.

The design shortlist (current/LOCAL_MODEL_SHORTLIST_EN.md) records NO final
model choice (V1-V9 all NOT_RUN, RAM/latency/conformance UNKNOWN), so this
slice registers only the `local:` id scheme (not_configured) and invents no
model pick. No transport, routing-mode, spend, receipt, frontend, or existing
test change. Mock-free: registry lookups are pure local data structures.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ai import local_runtime, registry, secrets

LOCAL_SCHEME_ID = 'local:example-server:example-model-tag'
LOCAL_STATUS = 'wire_adapter_tested_live_not_run'
LOCAL_PLACEHOLDER_STATUS = ('not_configured; id scheme local:<server>:<model-tag> '
                            'reserved as example placeholder only - no model configured; '
                            'local inference runtime not installed')
#: The one executable local row: app id, then the exact unconfigured status text.
LOCAL_MODEL_ID = 'local:llamacpp:qwen3-4b-instruct-2507-q4_k_m'
LOCAL_MODEL_STATUS = ('local_llama_cpp_endpoint_not_configured; no supported loopback endpoint '
                      'is configured, so no local inference is available; the API model stays '
                      'the active path')
#: The pinned local provider entry, unconfigured. Pinned literally (not imported
#: from local_runtime) so a reworded status or an invented ready flag fails here.
EXPECTED_LOCAL_PROVIDER = {
    'id': 'local', 'provider': 'local',
    'display_name': 'Local Qwen3-4B-Instruct-2507 (llama.cpp, Q4_K_M)',
    'model_id': LOCAL_MODEL_ID, 'base_url': None,
    'endpoint_configured': False, 'wire_model': None, 'context': None,
    'observed_model_alias': None, 'observed_context': None,
    'observed_context_train': None,
    'max_prompt_bytes': 0, 'template_overhead_tokens': None,
    'launch_run': None, 'launch_server_pid': None, 'run_fingerprint': None,
    'weights_sha256': None,
    'transport': 'openai_chat_completions_loopback_authenticated',
    'billing': 'zero_api_cost_local_compute_not_metered',
    'prerequisites_ready': False,
    'account_verified': 'NOT_APPLICABLE_LOCAL',
    'status': LOCAL_MODEL_STATUS,
    'blocking_reasons': ['local_endpoint_unconfigured',
                         'local_model_alias_unconfigured',
                         'local_context_invalid',
                         'local_run_not_launched',
                         'local_run_secret_missing',
                         'local_weights_digest_mismatch',
                         'local_template_overhead_unmeasured',
                         'local_runtime_identity_unobserved'],
    'model_facts': {'repository': 'unsloth/Qwen3-4B-Instruct-2507-GGUF',
                    'file': 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
                    'revision': 'a06e946bb6b655725eafa393f4a9745d460374c9',
                    'bytes': 2497281120,
                    'sha256': '3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597',
                    'license': 'apache-2.0',
                    'runtime': 'llama.cpp b10621 (Metal)'},
    'launch_command': ('llama-server -m Qwen3-4B-Instruct-2507-Q4_K_M.gguf --alias '
                       'qwen3-4b-instruct-2507-q4_K_M --host 127.0.0.1 --port 9931 -c 8192 '
                       '--no-webui --cors-origins <app origin> --no-cors-credentials '
                       '--no-context-shift --api-key-file <per-run owner-only temp file>'),
}

@pytest.fixture(autouse=True)
def unconfigured_modal(monkeypatch):
    # Pin the no-deployment registry snapshot independently of machine settings.
    for name in ("WB_MODAL_BASE_URL", "WB_MODAL_PROXY_TOKEN_ID", "WB_MODAL_PROXY_TOKEN_SECRET",
                 "WB_MODAL_SHARED_BASE_URL", "WB_MODAL_SHARED_MODEL_ID", "WB_MODAL_SHARED_API_KEY",
                 # The local row reads its endpoint/context/status from these, so
                 # the pinned snapshot is independent of this machine's setup.
                 "WB_LOCAL_BASE_URL", "WB_LOCAL_MODEL", "WB_LOCAL_CONTEXT",
                 "WB_LOCAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Readiness reports presence only, so the owner-only file (if this machine
    # has one) must not leak a live value into a pinned snapshot.
    monkeypatch.setattr(secrets, "modal_shared_key_present", lambda: False)

# Full-equality pin table (live values read from app/ai/registry.py at fix
# time): kind + context + status for every cloud row, both self-hosted rows,
# and the local placeholder. A silent kind edit (e.g. contributor
# reasoning->chat), context change, or status-text rewrite trips exactly
# one row here. Added/removed model ids trip the exact-ID pin in
# test_all_rows_pinned_kind_context_status_full_equality below.
EXPECTED_ROW_FIELDS = {
    'muse-spark-1.3':
        {'kind': 'reasoning', 'modalities': ('text', 'image', 'pdf', 'video'),
         'endpoint': '/responses', 'input_price': 1.25, 'output_price': 4.25,
         'context': 1048576, 'status': LOCAL_STATUS},
    'muse-spark-1.3-contributor':
        {'kind': 'reasoning', 'modalities': ('text', 'image', 'pdf', 'video'),
         'endpoint': '/responses', 'input_price': 0.10, 'output_price': 0.20,
         'context': 1048576, 'status': LOCAL_STATUS},
    'muse-wonder-1.0':
        {'kind': 'reasoning', 'modalities': ('text', 'image', 'pdf', 'video'),
         'endpoint': '/responses', 'input_price': 4, 'output_price': 20,
         'context': 1048576, 'status': LOCAL_STATUS},
    'muse-image-1.0':
        {'kind': 'image', 'modalities': ('text', 'image'),
         'endpoint': '/responses', 'input_price': 0, 'output_price': 0,
         'context': 0, 'status': LOCAL_STATUS},
    'muse-voice-transcribe-1.0':
        {'kind': 'transcription', 'modalities': ('audio',),
         'endpoint': '/asr/transcribe', 'input_price': 0, 'output_price': 0,
         'context': 0, 'status': LOCAL_STATUS},
    'sam-3.1':
        {'kind': 'segmentation', 'modalities': ('image', 'video'),
         'endpoint': '/segmentation/interactive', 'input_price': 0,
         'output_price': 0, 'context': 0, 'status': LOCAL_STATUS},
    'spark_open_self_hosted':
        {'kind': 'local_candidate',
         'modalities': ('text', 'image', 'video', 'audio'), 'endpoint': None,
         'input_price': 0, 'output_price': 0, 'context': 1048576,
         'status': 'hardware_and_server_not_verified'},
    'glimmer_self_hosted':
        {'kind': 'local_candidate', 'modalities': ('text', 'image'),
         'endpoint': None, 'input_price': 0, 'output_price': 0,
         'context': 1048576, 'status': 'hardware_and_server_not_verified'},
    LOCAL_SCHEME_ID:
        {'kind': 'local_candidate', 'modalities': ('text',), 'endpoint': None,
         'input_price': 0, 'output_price': 0, 'context': 1048576,
         'status': LOCAL_PLACEHOLDER_STATUS},
    # Executable local row for the operator-run loopback llama.cpp server
    # (2026-10-27). `context` is the DECLARED -c bound (default 8192), never the
    # model's larger native training context: an unconfigured machine reports
    # the documented default, and a configured one reports the live value. The
    # row's status text is pinned literally (not read from local_runtime) so a
    # silent honesty rewrite fails here.
    LOCAL_MODEL_ID:
        {'kind': 'local_reasoning', 'modalities': ('text',), 'endpoint': None,
         'input_price': 0, 'output_price': 0, 'context': 8192,
         'status': LOCAL_MODEL_STATUS},
    # Baseten-hosted Muse Spark Open (added 2026-10-27). Prices are Baseten's
    # published rates and equal the muse-spark-1.3 row; status records the
    # verified limitation that json_schema is accepted but not enforced, so
    # the client validates and fails closed. Text-only: no asset modality is
    # documented for this route.
    # Pinned to the DATED snapshot: the hackathon key is scoped to it, so the
    # undated alias is not a row the app may name.
    'meta-models/muse-spark-open-1026':
        {'kind': 'reasoning', 'modalities': ('text',),
         'endpoint': '/responses', 'input_price': 1.25, 'output_price': 4.25,
         'context': 1048576,
         'status': ('baseten_hosted; text_only_modality_claimed; json_schema_accepted_but_'
                    'not_enforced_by_provider_fail_closed_client_validation')},
    'meta-models/Muse-Glimmer-30B':
        {'kind': 'cloud_candidate', 'modalities': ('text',), 'endpoint': None,
         'input_price': None, 'output_price': None, 'context': 0,
         'status': 'code_only; runtime_not_verified; gpu_accounting_and_paid_approval_required'},
    # Modal-provided Shared endpoint (2026-10-27). Its own provider id and its
    # own internal alias; per-token rates equal the muse-spark-1.3 row. The
    # remote model id is configuration, never a registry row.
    'modal-shared:muse-spark-open':
        {'kind': 'reasoning', 'modalities': ('text',),
         'endpoint': '/v1/responses', 'input_price': 1.25, 'output_price': 4.25,
         'context': 1048576,
         'status': ('modal_shared_hosted; text_only_modality_claimed; openai_responses_'
                    'protocol_wired; per_token_pricing_not_gpu; execution_requires_'
                    'configured_origin_pinned_remote_model_id_and_verified_account')},
    # First-party OpenAI test route (2026-10-27): the pinned snapshot with its
    # published per-1M rates and documented 400000 context; no execution yet.
    'gpt-5.4-mini-2026-03-17':
        {'kind': 'reasoning', 'modalities': ('text',), 'endpoint': '/responses',
         'input_price': .75, 'output_price': 4.5, 'context': 400000,
         'status': registry.OPENAI_STATUS},
}

EXPECTED_PROVIDERS = [
    {'id': 'meta', 'base_url': 'https://api.meta.ai/v1',
     'secret': 'environment or owner-only local file'},
    EXPECTED_LOCAL_PROVIDER,
    {'id': 'modal', 'model_id': 'meta-models/Muse-Glimmer-30B', 'provider': 'modal',
     'base_url': None, 'endpoint_configured': False, 'proxy_auth_configured': False,
     'transport': 'openai_chat_completions', 'billing': 'gpu_time',
     'execution_enabled': False, 'status': 'blocked',
     'blocking_reasons': ['endpoint_undeployed_or_unconfigured', 'proxy_auth_unconfigured',
                          'deployment_runtime_not_verified', 'gpu_accounting_missing',
                          'live_execution_approval_missing'],
     'protocol': 'openai_chat_completions', 'readiness': 'code_only_not_verified'},
    {'id': 'baseten', 'base_url': 'https://inference.baseten.co/v1',
     'secret': 'environment or owner-only local file (BASETEN_API_KEY)'},
    {'id': 'modal_shared', 'provider': 'modal_shared',
     'display_name': 'Modal Shared / Muse Spark Open',
     'model_id': 'modal-shared:muse-spark-open', 'remote_model_id': None,
     'verified_remote_model_id': 'meta-models/Muse-Spark-Open-816B-A42B',
     'base_url': None, 'endpoint_configured': False,
     'remote_model_id_configured': False,
     'remote_model_id_unverified': False, 'key_present': False,
     'transport': 'openai_responses', 'billing': 'per_token',
     'prerequisites_ready': False, 'account_verified': 'NOT_CHECKED',
     'blocking_reasons': ['endpoint_unconfigured', 'remote_model_id_unconfigured',
                          'key_missing'],
     'secret': 'environment or owner-only local file (WB_MODAL_SHARED_API_KEY)'},
    {'id': 'openai', 'provider': 'openai', 'model_id': 'gpt-5.4-mini-2026-03-17',
     'base_url': 'https://api.openai.com/v1', 'context': 400000,
     'max_input': 272000, 'input_price': .75, 'cached_input_price': .075,
     'output_price': 4.5, 'transport': 'openai_responses', 'billing': 'per_token',
     'reasoning_effort_default': 'none', 'text_only': True, 'store': False,
     'service_tier': 'default', 'tools': [], 'test_total_limit_usd': 1.0,
     'status': registry.OPENAI_STATUS, 'key_present': False,
     'account_verified': 'NOT_CHECKED',
     'secret': 'environment or owner-only local file (OPENAI_API_KEY)'},
]

EXPECTED_TASKS = {'research_query': 'meta-models/muse-spark-open-1026',
                  'literature_analysis': 'meta-models/muse-spark-open-1026',
                  'methodology_review': 'muse-wonder-1.0',
                  'cross_artifact_review': 'muse-wonder-1.0',
                  'image_generate': 'muse-image-1.0',
                  'image_edit': 'muse-image-1.0',
                  'transcribe': 'muse-voice-transcribe-1.0',
                  'segment': 'sam-3.1',
                  # Claim–evidence verification is the one task whose TASKS value
                  # is a Baseten id, not a Meta slug: the Meta API credits are
                  # exhausted, so defaulting it to a Meta model would route the
                  # feature to an account that cannot pay for it.
                  'claim_check': 'meta-models/muse-spark-open-1026'}


def test_local_scheme_id_registered_with_entry_shape():
    m = registry.MODELS[LOCAL_SCHEME_ID]
    assert m.id == LOCAL_SCHEME_ID
    assert m.kind == 'local_candidate'
    assert m.modalities == ('text',)
    assert m.endpoint is None


def test_local_entry_carries_zero_cloud_pricing():
    # Zero-price convention matches the existing unpriced entries
    # (image/transcribe/sam all default to input_price=0/output_price=0):
    # no invented per-token prices, unknown/None-equivalent zeros only.
    m = registry.MODELS[LOCAL_SCHEME_ID]
    assert m.input_price == 0
    assert m.output_price == 0


def test_local_id_resolves_through_real_lookup_path():
    # Same lookup route() performs for every manual model choice: the id
    # resolves in MODELS, then fails closed because no runtime is configured
    # (endpoint None -> existing non-executable error, not silent success).
    assert registry.MODELS.get(LOCAL_SCHEME_ID) is not None
    with pytest.raises(ValueError, match='Model binding not executable; self-hosting needs separate verification'):
        registry.route('research_query', LOCAL_SCHEME_ID)
    with pytest.raises(ValueError, match='Model binding not executable; self-hosting needs separate verification'):
        registry.route('literature_analysis', LOCAL_SCHEME_ID)


def test_cloud_entries_pinned_prices_endpoints_providers():
    # A silent cloud edit (price, endpoint, modality, BASE_URL, provider
    # list) must fail: pin every cloud row's priced values, endpoints,
    # modalities, the base URL, and the provider id list.
    assert registry.TASKS == EXPECTED_TASKS
    assert registry.BASE_URL == 'https://api.meta.ai/v1'
    snap = registry.registry()
    assert [p['id'] for p in snap['providers']] == ['meta', 'local', 'modal', 'baseten', 'modal_shared', 'openai']
    assert snap['providers'][0]['base_url'] == registry.BASE_URL == 'https://api.meta.ai/v1'
    # Priced cloud rows (values verified in app/ai/registry.py at fix time).
    spark = registry.MODELS['muse-spark-1.3']
    assert (spark.input_price, spark.output_price) == (1.25, 4.25)
    assert spark.endpoint == '/responses'
    assert spark.modalities == ('text', 'image', 'pdf', 'video')
    contrib = registry.MODELS['muse-spark-1.3-contributor']
    assert (contrib.input_price, contrib.output_price) == (0.10, 0.20)
    assert contrib.endpoint == '/responses'
    assert contrib.modalities == ('text', 'image', 'pdf', 'video')
    wonder = registry.MODELS['muse-wonder-1.0']
    assert (wonder.input_price, wonder.output_price) == (4, 20)
    assert wonder.endpoint == '/responses'
    assert wonder.modalities == ('text', 'image', 'pdf', 'video')
    # Unpriced cloud rows: prices pinned to exactly zero (dataclass defaults
    # in app/ai/registry.py — these rows pass no price args). A silent
    # nonzero-price edit trips these; endpoints + modalities also pinned.
    assert (registry.MODELS['muse-image-1.0'].input_price, registry.MODELS['muse-image-1.0'].output_price) == (0, 0)
    assert (registry.MODELS['muse-voice-transcribe-1.0'].input_price, registry.MODELS['muse-voice-transcribe-1.0'].output_price) == (0, 0)
    assert (registry.MODELS['sam-3.1'].input_price, registry.MODELS['sam-3.1'].output_price) == (0, 0)
    assert registry.MODELS['muse-image-1.0'].endpoint == '/responses'
    assert registry.MODELS['muse-image-1.0'].modalities == ('text', 'image')
    assert registry.MODELS['muse-voice-transcribe-1.0'].endpoint == '/asr/transcribe'
    assert registry.MODELS['muse-voice-transcribe-1.0'].modalities == ('audio',)
    assert registry.MODELS['sam-3.1'].endpoint == '/segmentation/interactive'
    assert registry.MODELS['sam-3.1'].modalities == ('image', 'video')
    # Snapshot rows carry the same pinned values (registry() shaping edits fail too).
    rows = {r['id']: r for r in snap['models']}
    assert (rows['muse-spark-1.3']['input_price'], rows['muse-spark-1.3']['output_price']) == (1.25, 4.25)
    assert (rows['muse-wonder-1.0']['input_price'], rows['muse-wonder-1.0']['output_price']) == (4, 20)
    assert (rows['muse-spark-1.3-contributor']['input_price'], rows['muse-spark-1.3-contributor']['output_price']) == (0.10, 0.20)
    assert (rows['muse-image-1.0']['input_price'], rows['muse-image-1.0']['output_price']) == (0, 0)
    assert (rows['muse-voice-transcribe-1.0']['input_price'], rows['muse-voice-transcribe-1.0']['output_price']) == (0, 0)
    assert (rows['sam-3.1']['input_price'], rows['sam-3.1']['output_price']) == (0, 0)
    assert rows['muse-spark-1.3']['endpoint'] == '/responses'
    assert rows['sam-3.1']['endpoint'] == '/segmentation/interactive'
    # Routing behaviour preserved.
    assert registry.route('research_query').id == 'meta-models/muse-spark-open-1026'
    assert registry.route('methodology_review').id == 'muse-wonder-1.0'
    assert registry.route('research_query', 'muse-wonder-1.0').kind == 'reasoning'
    with pytest.raises(ValueError, match='Selected model cannot perform this typed task'):
        registry.route('research_query', 'sam-3.1')
    with pytest.raises(ValueError, match='Model binding not executable; self-hosting needs separate verification'):
        registry.route('research_query', 'glimmer_self_hosted')


def test_all_rows_pinned_kind_context_status_full_equality():
    # One full-equality table for kind/context/status/modalities/prices/
    # endpoints over all six cloud rows, both self-hosted rows, and the
    # local placeholder: any silent kind edit (e.g. contributor
    # reasoning->chat), context change, or status-text rewrite trips exactly
    # one row here. Snapshot rows carry the same fields so registry()
    # shaping edits fail too. Both directions pinned: EXPECTED_ROW_FIELDS
    # must equal the live id set exactly, so adding/removing a model row
    # (e.g. a new self-hosted id) trips this even though per-row checks
    # alone would pass.
    assert set(EXPECTED_ROW_FIELDS) == {
        'muse-spark-1.3', 'muse-spark-1.3-contributor', 'muse-wonder-1.0',
        'muse-image-1.0', 'muse-voice-transcribe-1.0', 'sam-3.1',
        'spark_open_self_hosted', 'glimmer_self_hosted',
        'meta-models/muse-spark-open-1026',
        'meta-models/Muse-Glimmer-30B',
        'modal-shared:muse-spark-open',
        'gpt-5.4-mini-2026-03-17',
        LOCAL_SCHEME_ID,
        LOCAL_MODEL_ID,
    }
    assert set(registry.MODELS) == set(EXPECTED_ROW_FIELDS)
    for mid, expected in EXPECTED_ROW_FIELDS.items():
        m = registry.MODELS[mid]
        actual = {k: getattr(m, k) for k in expected}
        assert actual == expected, mid
    rows = {r['id']: r for r in registry.registry()['models']}
    assert set(rows) == set(EXPECTED_ROW_FIELDS)
    for mid, expected in EXPECTED_ROW_FIELDS.items():
        for field in ('kind', 'modalities', 'endpoint', 'input_price',
                      'output_price', 'context', 'status'):
            snap_val = rows[mid][field]
            want = expected[field]
            if field == 'modalities':
                snap_val = tuple(snap_val)
            assert snap_val == want, (mid, field)


def test_providers_pinned_full_shape_equality():
    # Full provider shape as returned by registry(): exact list equality,
    # which pins list order plus each dict's keys/values (dict == does not
    # pin dict key order). A silent provider edit — added key, reworded
    # status, swapped base_url, reordered providers — trips this.
    assert registry.registry()['providers'] == EXPECTED_PROVIDERS


def test_registry_snapshot_flags_pinned_exact():
    # registry() envelope flags (live values read from app/ai/registry.py):
    # exact key set plus stable values. documentation_checked pins the
    # DOC_DATE code constant exactly — it moves only when the constant
    # moves, so this is a code pin, not calendar brittleness.
    snap = registry.registry()
    assert set(snap) == {
        'providers', 'models', 'tasks', 'documentation_checked',
        'account_access', 'contributor_mode_enabled',
        'contributor_selection', 'sources',
    }
    assert snap['documentation_checked'] == registry.DOC_DATE == '2026-10-26'
    assert snap['account_access'] == 'NOT_CHECKED'
    assert snap['contributor_mode_enabled'] is True
    assert snap['contributor_selection'] == (
        'User permits Contributor; select explicitly per task; '
        'default routing stays Standard'
    )
    assert snap['sources'] == [
        'https://dev.meta.ai/docs/models',
        'https://dev.meta.ai/docs/pricing-rate-limits',
        'https://dev.meta.ai/docs/speech-to-text',
        'https://dev.meta.ai/docs/sam/reading-segmentation',
    ]
    assert snap['tasks'] == EXPECTED_TASKS


def test_local_id_cannot_be_mistaken_for_meta_cloud_id():
    # Every `local:`-prefixed id is either the reserved non-executable
    # placeholder or the executable local row; no TASKS default binding and no
    # cloud kind ever carries the prefix.
    assert LOCAL_SCHEME_ID.startswith('local:')
    assert not any(v.startswith('local:') for v in registry.TASKS.values())
    for mid, m in registry.MODELS.items():
        if mid.startswith('local:'):
            # Local kinds are their own vocabulary: a local row can never
            # satisfy a cloud kind check, so no cloud pipeline branch (reasoning
            # cost, transcription usage, image output) can claim it by accident.
            assert m.kind in ('local_candidate', 'local_reasoning'), mid
            assert m.kind not in ('reasoning', 'image', 'transcription', 'segmentation',
                                  'cloud_candidate')
        else:
            assert not mid.startswith('local')
    # Default routing never returns a local id.
    for task in registry.TASKS:
        assert not registry.route(task).id.startswith('local:')
    # The executable local row is not routable as a cloud model: cloud mode
    # rejects it by kind, so a local binding can never be selected while the
    # request claims its provider is Meta/Baseten/Modal/OpenAI.
    with pytest.raises(ValueError, match='Model binding not executable'):
        registry.route('research_query', LOCAL_MODEL_ID)
    # The registry() snapshot exposes the local entries with zero prices,
    # never as priced cloud models.
    snap = registry.registry()
    local_rows = [r for r in snap['models'] if r['id'].startswith('local:')]
    assert [r['id'] for r in local_rows] == [LOCAL_SCHEME_ID, LOCAL_MODEL_ID]
    assert all(r['input_price'] == 0 and r['output_price'] == 0 for r in local_rows)


def test_local_row_unconfigured_is_not_offered_as_runnable():
    # With no loopback endpoint configured the row carries no endpoint, so the
    # model picker cannot offer it and local mode fails closed with the
    # blocking reason instead of silently routing to a cloud model.
    assert registry.MODELS[LOCAL_MODEL_ID].endpoint is None
    with pytest.raises(ValueError, match='Model binding not executable'):
        registry.route('research_query', 'auto', mode='local')
    # Local mode is scoped to the two text-reasoning tasks.
    with pytest.raises(ValueError, match='Local mode serves text research tasks only'):
        registry.route('image_generate', 'auto', mode='local')
    with pytest.raises(ValueError, match='Local mode resolves only the configured local model'):
        registry.route('research_query', 'muse-spark-1.3', mode='local')


def test_status_text_honest_runtime_absent():
    m = registry.MODELS[LOCAL_SCHEME_ID]
    assert 'not_configured' in m.status
    assert 'runtime not installed' in m.status
    assert 'local_ready' not in m.status
    # The executable local row reports configuration, never readiness: this app
    # runs no startup probe, so no status text may claim a reachable server.
    local = registry.MODELS[LOCAL_MODEL_ID]
    assert 'local_ready' not in local.status
    assert 'not_configured' in local.status
    # And it never claims the model's larger native training context.
    assert local.context == 8192
    assert local.context < 1048576
    providers = {p['id']: p for p in registry.registry()['providers']}
    assert providers['meta']['secret'].startswith('environment or owner-only local file')
    assert providers['local']['status'] == EXPECTED_PROVIDERS[1]['status']
    assert m.status == LOCAL_PLACEHOLDER_STATUS
    assert 'legacy' not in providers['local']['status']
    assert 'preserved' not in providers['local']['status']


def test_a_launched_run_is_resolved_and_advertised_from_the_live_binding(monkeypatch):
    """The row must follow the run, not the import-time snapshot (B4)."""
    monkeypatch.setenv('WB_LOCAL_BASE_URL', 'http://127.0.0.1:9955')
    monkeypatch.setenv('WB_LOCAL_MODEL', 'qwen3-4b-instruct-2507-q4_K_M')
    monkeypatch.setenv('WB_LOCAL_CONTEXT', '4096')
    monkeypatch.setenv('WB_LOCAL_RUN_ID', 'run-live')
    monkeypatch.setenv('WB_LOCAL_SERVER_PID', str(os.getpid()))
    monkeypatch.setenv('WB_LOCAL_API_KEY', 'test-run-bearer-not-a-real-credential')
    monkeypatch.setenv('WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS', '21')
    monkeypatch.setenv('WB_LOCAL_WEIGHTS_SHA256', local_runtime.MODEL_FACTS['sha256'])
    # The identity the server reported at readiness. A binding without it is not
    # usable, so this is part of what a real launch publishes.
    monkeypatch.setenv('WB_LOCAL_OBSERVED_ALIAS', 'qwen3-4b-instruct-2507-q4_K_M')
    monkeypatch.setenv('WB_LOCAL_OBSERVED_CTX', '4096')
    monkeypatch.setenv('WB_LOCAL_OBSERVED_CTX_TRAIN', '262144')
    row = registry.live_models()[LOCAL_MODEL_ID]
    # Live endpoint and context, never the import-time defaults.
    assert row.endpoint == 'http://127.0.0.1:9955/v1/chat/completions'
    assert row.context == 4096
    resolved = registry.route('research_query', 'auto', mode='local')
    assert resolved.endpoint == row.endpoint
    assert resolved.context == 4096
    # The provider entry reports the observed run identity, but never the secret.
    conn = local_runtime.connection_status()
    assert conn['run_fingerprint'] == local_runtime.run_fingerprint()
    assert conn['launch_run'] == 'run-live'
    assert conn['launch_server_pid'] == os.getpid()
    # What the server said about itself, recorded next to what we launched.
    assert conn['wire_model'] == conn['observed_model_alias']
    assert conn['observed_context'] == conn['context'] == 4096
    assert conn['observed_context_train'] == 262144
    assert conn['max_prompt_bytes'] == local_runtime.prompt_budget_bytes(4096, 21)
    assert 'test-run-bearer-not-a-real-credential' not in repr(registry.registry())


def test_a_binding_without_the_observed_identity_is_not_runnable(monkeypatch):
    """B3: the launched values alone are intent, not evidence of the run.

    Every other part of the binding is present, but the server never reported
    its own alias/context (so nothing proves the listener is the model this app
    started). The row must then carry no endpoint, and local mode must refuse.
    """
    monkeypatch.setenv('WB_LOCAL_BASE_URL', 'http://127.0.0.1:9955')
    monkeypatch.setenv('WB_LOCAL_MODEL', 'qwen3-4b-instruct-2507-q4_K_M')
    monkeypatch.setenv('WB_LOCAL_CONTEXT', '4096')
    monkeypatch.setenv('WB_LOCAL_RUN_ID', 'run-unobserved')
    monkeypatch.setenv('WB_LOCAL_SERVER_PID', '778')
    monkeypatch.setenv('WB_LOCAL_API_KEY', 'test-run-bearer-not-a-real-credential')
    monkeypatch.setenv('WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS', '21')
    monkeypatch.setenv('WB_LOCAL_WEIGHTS_SHA256', local_runtime.MODEL_FACTS['sha256'])
    config = local_runtime.read_config()
    assert config['configured'] is False
    assert 'local_runtime_identity_unobserved' in config['blocking_reasons']
    assert registry.live_models()[LOCAL_MODEL_ID].endpoint is None
    with pytest.raises(ValueError, match='Model binding not executable'):
        registry.route('research_query', 'auto', mode='local')


def test_an_in_process_binding_is_read_without_touching_the_environment(monkeypatch):
    """The packaged backend publishes in-process so no child inherits the bearer.

    The store and the environment are parsed by the same rules, and the store
    wins; nothing is exported, so ``os.environ`` stays free of the credential.
    """
    for name in ('WB_LOCAL_BASE_URL', 'WB_LOCAL_MODEL', 'WB_LOCAL_CONTEXT',
                 'WB_LOCAL_RUN_ID', 'WB_LOCAL_SERVER_PID', 'WB_LOCAL_API_KEY',
                 'WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS', 'WB_LOCAL_WEIGHTS_SHA256',
                 'WB_LOCAL_OBSERVED_ALIAS', 'WB_LOCAL_OBSERVED_CTX',
                 'WB_LOCAL_OBSERVED_CTX_TRAIN'):
        monkeypatch.delenv(name, raising=False)
    environments = []
    original = local_runtime._binding_value

    def spy(name, default=''):
        environments.append(name in local_runtime._BINDING)
        return original(name, default)

    monkeypatch.setattr(local_runtime, '_binding_value', spy)
    published = local_runtime.publish_binding({
        'WB_LOCAL_BASE_URL': 'http://127.0.0.1:9955',
        'WB_LOCAL_MODEL': 'qwen3-4b-instruct-2507-q4_K_M',
        'WB_LOCAL_CONTEXT': '4096',
        'WB_LOCAL_RUN_ID': 'run-in-process',
        'WB_LOCAL_SERVER_PID': str(os.getpid()),
        'WB_LOCAL_API_KEY': 'test-run-bearer-not-a-real-credential',
        'WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS': '21',
        'WB_LOCAL_WEIGHTS_SHA256': local_runtime.MODEL_FACTS['sha256'],
        'WB_LOCAL_OBSERVED_ALIAS': 'qwen3-4b-instruct-2507-q4_K_M',
        'WB_LOCAL_OBSERVED_CTX': '4096',
        'WB_LOCAL_OBSERVED_CTX_TRAIN': '262144',
    })
    try:
        config = local_runtime.read_config()
        assert config['configured'] is True
        assert config['origin'] == 'http://127.0.0.1:9955'
        assert config['context'] == 4096
        assert config['template_overhead_tokens'] == 21
        # The bearer came from the store, and the store is all that was consulted.
        assert all(environments), 'every field must come from the in-process store'
        assert 'WB_LOCAL_API_KEY' not in os.environ
        assert published['WB_LOCAL_API_KEY'] == 'test-run-bearer-not-a-real-credential'
        # Clearing the store with None withdraws the run.
        assert local_runtime.publish_binding(None) == {}
        assert local_runtime.read_config()['configured'] is False
    finally:
        local_runtime.publish_binding(None)
