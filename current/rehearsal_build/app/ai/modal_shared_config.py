"""Modal-provided Shared endpoint configuration (not the code-only Glimmer route).

This route speaks the documented OpenAI Responses protocol on a Modal Shared
endpoint: ``POST <origin>/v1/responses`` with ``Authorization: Bearer
wk-<proxy-token-id>.ws-<proxy-token-secret>``. It is text only, bills per
token (never GPU time), and is entirely separate from ``modal_config``, whose
Glimmer deployment remains code-only with its own no-execution guards.

Two values are environment-pinned, never guessed:

* ``WB_MODAL_SHARED_BASE_URL``: the endpoint *origin* (scheme + host, no path).
  It is revalidated through the same https / exact-Modal-domain policy the
  Glimmer route uses, so ``*.modal.direct`` and ``*.modal.run`` are the only
  accepted hosts. A path, query, fragment, credentials or any other domain is
  rejected, and no alternate origin is ever derived.
* ``WB_MODAL_SHARED_MODEL_ID``: the exact remote model id reported by the
  endpoint's own metadata (``/v1/models``). This route is single-model, because
  the alias' prices and context belong to one model: the pinned value must be
  exactly the verified id below. Anything else (a different model, a case or
  whitespace variant, an empty value) leaves the route unconfigured rather than
  billing another model at Muse Spark Open rates, and nothing here normalizes,
  lowercases or substitutes an alternative slug or falls back to it.

The bearer credential lives in the environment ``WB_MODAL_SHARED_API_KEY`` or
the fixed owner-only file ``modal_shared_api_key`` (see ``secrets``). Readiness
reports presence booleans only: the credential value is never serialized.
"""
import hashlib
import os

from . import modal_config, secrets

#: Provider id stamped into prepared records and returned by the registry.
PROVIDER_ID = 'modal_shared'
#: Internal model id. It is an app-local alias and is NEVER sent on the wire.
MODEL_ID = 'modal-shared:muse-spark-open'
#: Operator-facing name shared with the frontend contract.
DISPLAY_NAME = 'Modal Shared / Muse Spark Open'
#: API base suffix and the two allowed paths on that base.
BASE_SUFFIX = '/v1'
RESPONSES_PATH = BASE_SUFFIX + '/responses'
CATALOG_PATH = BASE_SUFFIX + '/models'
#: Documented per-1M-token rates for Muse Spark Open on Modal Shared. They
#: equal the published muse-spark-1.3 row (input 1.25, cached input 0.15,
#: output 4.25), so the existing settlement arithmetic is reused unchanged.
INPUT_PRICE = 1.25
CACHED_INPUT_PRICE = 0.15
OUTPUT_PRICE = 4.25
CONTEXT = 1048576
#: Registry status text: what is verified, and what still gates execution.
STATUS = ('modal_shared_hosted; text_only_modality_claimed; openai_responses_protocol_'
          'wired; per_token_pricing_not_gpu; execution_requires_configured_origin_'
          'pinned_remote_model_id_and_verified_account')

#: The only remote model id this alias may bill: the official 2026-10-15
#: announcement id for repoID ``meta-models/Muse-Spark-Open-816B-A42B``. The
#: alias' per-token prices and 1M context are that model's, so a different
#: pinned id would silently price one model at another's rates.
VERIFIED_REMOTE_MODEL_ID = 'meta-models/Muse-Spark-Open-816B-A42B'


def read_config():
    """Private transport configuration; this mapping is not serialized as-is."""
    raw = os.environ.get('WB_MODAL_SHARED_BASE_URL', '')
    origin = modal_config.endpoint_origin(raw)
    remote = os.environ.get('WB_MODAL_SHARED_MODEL_ID', '')
    remote = remote if isinstance(remote, str) else ''
    # Single-model route: the value must be exactly the verified id. A blank
    # value is "unconfigured"; any other value is "unverified" — both leave the
    # route unusable, and neither is ever replaced by the verified default.
    unverified = bool(remote) and remote != VERIFIED_REMOTE_MODEL_ID
    if remote != VERIFIED_REMOTE_MODEL_ID:
        remote = None
    return {'origin': origin,
            'endpoint_invalid': bool(raw) and origin is None,
            'remote_model_id': remote,
            'remote_model_id_unverified': unverified,
            'api_base': (origin + BASE_SUFFIX) if origin else None}


def credential_fingerprint(key):
    """SHA-256 of the presented bearer, for binding a preview to a credential.

    Only the digest is stored: a prepared record can prove which credential it
    was approved for without ever holding the secret.
    """
    return hashlib.sha256((key or '').encode()).hexdigest() if key else None


def catalog_scope_digest(origin, remote_model_id, key):
    """Digest of the exact scope a catalog verification was performed for.

    The route is single-model but not single-destination: the same credential
    may be pointed at a different endpoint, and the verified catalog belongs to
    the origin and remote model id it was read from. Stored in the existing
    ``key_hash`` column, this value makes a recorded verification valid only
    for the same credential *and* the same origin *and* the same pinned remote
    model id, so repointing the route requires a fresh explicit verify instead
    of reusing an older grant.
    """
    credential = credential_fingerprint(key)
    if not origin or not remote_model_id or not credential:
        return None
    return hashlib.sha256(
        '\x00'.join((origin, remote_model_id, credential)).encode()).hexdigest()


def connection_status():
    """Configuration readiness only. Never proves account access or executes."""
    config = read_config()
    key_present = secrets.modal_shared_key_present()
    reasons = []
    if not config['origin']:
        reasons.append('endpoint_invalid' if config['endpoint_invalid'] else
                       'endpoint_unconfigured')
    if config['remote_model_id_unverified']:
        reasons.append('remote_model_id_unverified')
    elif not config['remote_model_id']:
        reasons.append('remote_model_id_unconfigured')
    if not key_present:
        reasons.append('key_missing')
    return {'provider': PROVIDER_ID, 'display_name': DISPLAY_NAME,
            'model_id': MODEL_ID, 'remote_model_id': config['remote_model_id'],
            'verified_remote_model_id': VERIFIED_REMOTE_MODEL_ID,
            'base_url': config['api_base'],
            'endpoint_configured': bool(config['origin']),
            'remote_model_id_configured': bool(config['remote_model_id']),
            'remote_model_id_unverified': config['remote_model_id_unverified'],
            'key_present': key_present,
            'transport': 'openai_responses', 'billing': 'per_token',
            'prerequisites_ready': not reasons,
            'account_verified': 'NOT_CHECKED',
            'blocking_reasons': reasons}
