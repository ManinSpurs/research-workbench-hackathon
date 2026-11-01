"""Official documentation snapshot, 2026-10-26. Account access is separate."""
from dataclasses import dataclass,asdict,replace
from .modal_config import PROVIDER_ID as MODAL_PROVIDER,MODEL_ID as MODAL_MODEL_ID
from .modal_shared_config import (PROVIDER_ID as MODAL_SHARED_PROVIDER,
                                  MODEL_ID as MODAL_SHARED_MODEL_ID,
                                  DISPLAY_NAME as MODAL_SHARED_DISPLAY,
                                  STATUS as MODAL_SHARED_STATUS,
                                  INPUT_PRICE as MODAL_SHARED_INPUT,
                                  OUTPUT_PRICE as MODAL_SHARED_OUTPUT,
                                  CONTEXT as MODAL_SHARED_CONTEXT,
                                  RESPONSES_PATH as MODAL_SHARED_PATH,
                                  connection_status as modal_shared_status)
from . import local_runtime

BASE_URL='https://api.meta.ai/v1'
DOC_DATE='2026-10-26'
#: SAM 3.1 interactive segmentation endpoint (Meta Model API docs, 2026-10-26):
#: non-streaming POST <base>/segmentation/interactive, multipart form
#: (image file bytes + the minimal documented fields derivable from the
#: current request). The streamed /responses + invented token grammar
#: previously wired here matched neither documented contract and is retired.
SAM_INTERACTIVE_PATH='/segmentation/interactive'
#: Provider identities stamped into prepared records, so execute() re-binds the
#: same transport and a swapped provider fails closed exactly like a contract
#: change. Ordinary research auto routing uses the pinned Baseten model.
META_PROVIDER='meta'
BASETEN_PROVIDER='baseten'
#: Baseten-hosted inference origin (verified contract:
#: https://inference.baseten.co/v1/responses, Authorization: Bearer key).
#: Separate from BASE_URL so the preview names the actual provider.
BASETEN_BASE_URL='https://inference.baseten.co/v1'
#: Baseten's Muse Spark Open slug, pinned to the dated snapshot. A live GET
#: /models on the hackathon key lists BOTH `meta-models/muse-spark-open` and this
#: `-1026` snapshot; the key is scoped to the snapshot, so the snapshot is what
#: this app names. Prices are identical between the two ids ($1.25 in / $0.15
#: cached / $4.25 out per 1M), so cost arithmetic is unchanged.
BASETEN_MODEL_ID='meta-models/muse-spark-open-1026'
#: The undated alias, recorded only so a caller that asks for it is refused BY
#: NAME with an explanation rather than an anonymous "not executable" error: the
#: scoped key does not authorize it even though the account lists it.
BASETEN_FLOATING_ALIAS='meta-models/muse-spark-open'
#: Distinct status marking the row as Baseten-hosted and recording the verified
#: constraint this route is built around: Baseten accepts json_schema but does
#: not enforce it, so the client validates every response and fails closed.
#: Only text is claimed for this route (no asset modality is documented).
BASETEN_STATUS=('baseten_hosted; text_only_modality_claimed; json_schema_accepted_but_'
                'not_enforced_by_provider_fail_closed_client_validation')
#: Documented static executable set for the Baseten route: the verified Baseten
#: contract this app speaks exposes no account model-catalog endpoint, so the
#: slug list stands in for it and access is proven by the response
#: model-identity gate in AIService.execute.
BASETEN_CATALOG=(BASETEN_MODEL_ID,)
#: First-party OpenAI route, a separate small test beside Modal: user approved
#: one OpenAI test, synthetic/public input only, total reservation <= $1.
#: Fixed official origin (https://developers.openai.com/api/docs/models/
#: gpt-5.4-mini, fetched 2026-10-27): global api.openai.com/v1.
OPENAI_PROVIDER='openai'
OPENAI_BASE_URL='https://api.openai.com/v1'
#: The explicit model snapshot, never the floating alias: the pinned snapshot
#: is what prices and the 400000-token context recorded here belong to.
OPENAI_MODEL_ID='gpt-5.4-mini-2026-03-17'
#: Published per-1M-token rates for the pinned snapshot (input / cached input /
#: output in USD). Reserved cost uses the uncached input rate.
OPENAI_INPUT_PRICE=.75
OPENAI_CACHED_INPUT_PRICE=.075
OPENAI_OUTPUT_PRICE=4.5
#: Total context window, and the smaller documented per-request input ceiling.
#: The existing reasoning reservation charges the full context at the uncached
#: rate, which is conservative against both numbers.
OPENAI_CONTEXT=400000
OPENAI_MAX_INPUT=272000
#: Registry status text: what the official snapshot documents, and the fact
#: that nothing on this route has been executed.
OPENAI_STATUS=('openai_first_party; text_only_modality_claimed; responses_protocol_wired; '
               'pinned_snapshot_gpt-5.4-mini-2026-03-17; structured_outputs_supported_and_'
               'provider_enforced; reasoning_effort_default_none; context_400000_max_input_'
               '272000_reserved_at_full_context_uncached; separate_test_budget_1_usd_total; '
               'test_only_live_not_run')
@dataclass(frozen=True)
class Model:
    id:str
    kind:str
    modalities:tuple[str,...]
    endpoint:str|None
    input_price:float|None=0
    output_price:float|None=0
    context:int=1048576
    status:str='wire_adapter_tested_live_not_run'

MODELS={m.id:m for m in (
    Model('muse-spark-1.3','reasoning',('text','image','pdf','video'),'/responses',1.25,4.25),
    Model('muse-spark-1.3-contributor','reasoning',('text','image','pdf','video'),'/responses',.10,.20),
    Model('muse-wonder-1.0','reasoning',('text','image','pdf','video'),'/responses',4,20),
    Model('muse-image-1.0','image',('text','image'),'/responses',context=0),
    Model('muse-voice-transcribe-1.0','transcription',('audio',),'/asr/transcribe',context=0),
    Model('sam-3.1','segmentation',('image','video'),'/segmentation/interactive',context=0),
    Model('spark_open_self_hosted','local_candidate',('text','image','video','audio'),None,status='hardware_and_server_not_verified'),
    Model('glimmer_self_hosted','local_candidate',('text','image'),None,status='hardware_and_server_not_verified'),
    Model('local:example-server:example-model-tag','local_candidate',('text',),None,status='not_configured; id scheme local:<server>:<model-tag> reserved as example placeholder only - no model configured; local inference runtime not installed'),
    # Executable local row (operator-run loopback llama.cpp server). Zero prices
    # are the zero-API-cost fact, not a claim about electricity. `context` is
    # the declared -c bound, read from the same configuration the transport and
    # prepare() enforce, so the row never claims the model's larger native
    # training context. `status` derives from live configuration and is never
    # the ready token: nothing here probes the server.
    Model(local_runtime.MODEL_ID,'local_reasoning',('text',),
          local_runtime.chat_endpoint(),
          context=local_runtime.configured_context(),
          status=local_runtime.model_status()),
    Model(BASETEN_MODEL_ID,'reasoning',('text',),'/responses',1.25,4.25,context=1048576,status=BASETEN_STATUS),
    Model(MODAL_SHARED_MODEL_ID,'reasoning',('text',),MODAL_SHARED_PATH,
          MODAL_SHARED_INPUT,MODAL_SHARED_OUTPUT,context=MODAL_SHARED_CONTEXT,
          status=MODAL_SHARED_STATUS),
    Model(MODAL_MODEL_ID,'cloud_candidate',('text',),None,None,None,context=0,
          status='code_only; runtime_not_verified; gpu_accounting_and_paid_approval_required'),
    Model(OPENAI_MODEL_ID,'reasoning',('text',),'/responses',
          OPENAI_INPUT_PRICE,OPENAI_OUTPUT_PRICE,context=OPENAI_CONTEXT,status=OPENAI_STATUS),
)}
TASKS={'research_query':BASETEN_MODEL_ID,'literature_analysis':BASETEN_MODEL_ID,
       'methodology_review':'muse-wonder-1.0','cross_artifact_review':'muse-wonder-1.0',
       'image_generate':'muse-image-1.0','image_edit':'muse-image-1.0',
       'transcribe':'muse-voice-transcribe-1.0','segment':'sam-3.1',
       # Claim–evidence verification defaults to the Baseten-hosted model, NOT a
       # Meta slug: the Meta API credits are exhausted, so the historical
       # "TASKS value is a Meta id" convention cannot hold here. The value is
       # therefore the Baseten id, and route() resolves it to the baseten
       # provider because BASETEN_MODEL_ID names it.
       'claim_check':BASETEN_MODEL_ID}

def _openai_key_present():
    """Presence-only readiness flag for the OpenAI route.

    Imported lazily so ``registry`` keeps no module-level dependency on the
    secret container, and so no key value can be read into this snapshot: only
    the boolean leaves this function.
    """
    from . import secrets
    return bool(secrets.openai_key_present())

def route(task,manual='auto',mode='cloud'):
    if task not in TASKS:raise ValueError('Unknown task type')
    if mode=='local':
        # Local mode is scoped to the two text-reasoning tasks and exactly one
        # configured local model: the operator-run loopback server speaks chat
        # completions only, so image/voice/segmentation/frozen-critique tasks
        # stay on their API routes rather than silently changing transport.
        if task not in local_runtime.ELIGIBLE_TASKS:raise ValueError('Local mode serves text research tasks only')
        if manual not in ('auto',local_runtime.MODEL_ID):raise ValueError('Local mode resolves only the configured local model')
        # Read the endpoint from LIVE configuration, not the row's import-time
        # value: the row is a snapshot taken when this module was first imported,
        # while the operator's runtime may be started (or re-pointed) later. A
        # local request is executable only when the whole contract is configured
        # right now — a half-configured machine never resolves a local model.
        if local_runtime.chat_endpoint() is None:raise ValueError('Model binding not executable; self-hosting needs separate verification')
        # The LIVE row, not the import-time snapshot: its endpoint and context
        # are the ones this run published, so a job resolved through here can
        # never carry a stale origin or a stale context bound.
        return live_models()[local_runtime.MODEL_ID]
    if mode!='cloud':raise ValueError('Unknown mode')
    model=MODELS.get(TASKS[task] if manual=='auto' else manual)
    if model is None or model.endpoint is None:raise ValueError('Model binding not executable; self-hosting needs separate verification')
    expected=MODELS[TASKS[task]].kind
    if model.kind!=expected:raise ValueError('Selected model cannot perform this typed task')
    return model

def live_models():
    """The model rows with the local row resolved from the current run.

    ``MODELS`` is a snapshot taken at import time, which is correct for every
    cloud row (their endpoints are static) and wrong for the local row: its
    endpoint and context exist only once a run has launched and verified a
    server. Emitting the snapshot would advertise a stale endpoint after the run
    that owned it has ended — exactly the case where a later, unrelated listener
    could inherit the port. So the local row is rebuilt here from the live
    binding, and carries no endpoint (hence is not offered) without one.
    """
    rows = dict(MODELS)
    binding = local_runtime.read_config()
    rows[local_runtime.MODEL_ID] = replace(
        MODELS[local_runtime.MODEL_ID],
        endpoint=local_runtime.chat_endpoint(),
        context=binding['context'] or local_runtime.DEFAULT_CONTEXT,
        status=local_runtime.model_status())
    return rows


def registry():
    from .modal_config import connection_status
    return {'providers':[{'id':META_PROVIDER,'base_url':BASE_URL,'secret':'environment or owner-only local file'},
                         {'id':local_runtime.PROVIDER_ID,**local_runtime.connection_status()},
                         {'id':MODAL_PROVIDER,**connection_status(),
                          'protocol':'openai_chat_completions','readiness':'code_only_not_verified'},
                         {'id':BASETEN_PROVIDER,'base_url':BASETEN_BASE_URL,'secret':'environment or owner-only local file (BASETEN_API_KEY)'},
                         {'id':MODAL_SHARED_PROVIDER,**modal_shared_status(),
                          'secret':'environment or owner-only local file (WB_MODAL_SHARED_API_KEY)'},
                         {'id':OPENAI_PROVIDER,'provider':OPENAI_PROVIDER,
                          'model_id':OPENAI_MODEL_ID,'base_url':OPENAI_BASE_URL,
                          'context':OPENAI_CONTEXT,'max_input':OPENAI_MAX_INPUT,
                          'input_price':OPENAI_INPUT_PRICE,
                          'cached_input_price':OPENAI_CACHED_INPUT_PRICE,
                          'output_price':OPENAI_OUTPUT_PRICE,
                          'transport':'openai_responses','billing':'per_token',
                          'reasoning_effort_default':'none','text_only':True,
                          'store':False,'service_tier':'default','tools':[],
                          'test_total_limit_usd':1.0,
                          'status':OPENAI_STATUS,
                          # Presence only: the credential value is never read
                          # or serialized by the registry snapshot.
                          'key_present':_openai_key_present(),
                          'account_verified':'NOT_CHECKED',
                          'secret':'environment or owner-only local file (OPENAI_API_KEY)'}],
            'models':[asdict(m) for m in live_models().values()], 'tasks':TASKS,'documentation_checked':DOC_DATE,
            'account_access':'NOT_CHECKED','contributor_mode_enabled':True,'contributor_selection':'User permits Contributor; select explicitly per task; default routing stays Standard',
            'sources':['https://dev.meta.ai/docs/models','https://dev.meta.ai/docs/pricing-rate-limits',
                       'https://dev.meta.ai/docs/speech-to-text','https://dev.meta.ai/docs/sam/reading-segmentation']}
