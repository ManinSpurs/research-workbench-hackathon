"""Meta transport. Fixed official origin, no redirects, proxies, SDK retries or hidden tools."""
import asyncio,json,time,base64
from urllib.parse import urlsplit
import httpx
from .registry import (BASE_URL,BASETEN_BASE_URL,SAM_INTERACTIVE_PATH,
                       OPENAI_BASE_URL,OPENAI_MODEL_ID)
from . import secrets,modal_config,modal_shared_config,local_runtime

class ProviderError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

#: Response-size ceiling shared by every JSON transport below.
MAX_RESPONSE_BYTES=24*1024*1024
#: Bounded per-request timeout (seconds) and its outer cancellation deadline.
REQUEST_TIMEOUT=90
REQUEST_DEADLINE=100


async def _json_request(transport,base_url,headers,method,path,*,body=None,files=None):
    """One single-attempt request: fixed origin, no redirects, no proxies.

    The whole wire policy lives here so every bearer-authenticated route (Meta,
    Baseten, Modal Shared) is byte-identical: no retry, no environment proxy,
    no redirect following, bounded body size, and the same status-code error
    vocabulary. Returns ``(raw_body_bytes, elapsed_seconds)``; parsing is the
    caller's job because the Meta stream path reads the body as text.
    """
    started=time.perf_counter()
    try:
        async with httpx.AsyncClient(base_url=base_url,headers=headers,
                transport=transport,trust_env=False,follow_redirects=False,
                timeout=REQUEST_TIMEOUT) as client:
            async with asyncio.timeout(REQUEST_DEADLINE):
                async with client.stream(method,path,json=body if files is None else None,files=files) as response:
                    if response.status_code>=300:raise ProviderError({401:'authentication_failed',403:'model_or_region_not_authorized',402:'payment_required',429:'rate_limit_or_credit_exhausted',529:'provider_overloaded'}.get(response.status_code,'provider_http_'+str(response.status_code)))
                    chunks=[];size=0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>MAX_RESPONSE_BYTES:raise ProviderError('provider_response_too_large')
                        chunks.append(chunk)
                    data=b''.join(chunks)
        return data,time.perf_counter()-started
    except ProviderError:raise
    except (httpx.HTTPError,TimeoutError,UnicodeError,ValueError):raise ProviderError('transport_or_response_error') from None

class MetaProvider:
    def __init__(self,transport=None,key_reader=secrets.key):
        self.transport=transport;self.key_reader=key_reader
    async def request(self,method,path,*,body=None,files=None,stream=False):
        secret=self.key_reader()
        if not secret:raise ProviderError('key_missing')
        if path.split('?')[0] not in ('/models','/responses','/asr/transcribe',SAM_INTERACTIVE_PATH):raise ProviderError('endpoint_not_allowed')
        try:
            data,elapsed=await _json_request(self.transport,BASE_URL,{'Authorization':'Bearer '+secret},
                                             method,path,body=body,files=files)
            if stream:
                deltas=[];terminal=None
                for line in data.decode().splitlines():
                    if not line.startswith('data:'):continue
                    raw=line[5:].strip()
                    if raw=='[DONE]':continue
                    event=json.loads(raw)
                    if event.get('type')=='response.output_text.delta':deltas.append(event.get('delta',''))
                    if event.get('type')=='response.completed':terminal=event.get('response')
                    if event.get('type') in ('error','response.failed'):raise ProviderError('stream_failed')
                if terminal is None:raise ProviderError('stream_completion_missing')
                result={**terminal}
            else:result=json.loads(data)
            return result,elapsed
        except ProviderError:raise
        except (UnicodeError,ValueError):raise ProviderError('transport_or_response_error') from None
    async def catalog(self):
        result,_=await self.request('GET','/models')
        if not isinstance(result.get('data'),list):raise ProviderError('invalid_catalog')
        return sorted({x['id'] for x in result['data'] if isinstance(x,dict) and isinstance(x.get('id'),str)})
    async def infer(self,model,body,assets):
        if model.kind=='transcription':
            item=assets[0];raw=base64.b64decode(item['encoded'])
            files={'request':(None,json.dumps({'mode':'DIARIZATION','model':model.id,'audioEncoding':'WAV'}),'application/json'),
                   'audio':(item['meta']['name'],raw,'audio/wav')}
            return await self.request('POST','/asr/transcribe?sessionId='+body['session_id'],files=files)
        if model.kind=='segmentation':
            # Documented interactive-segmentation transport (Meta Model API
            # docs, 2026-10-26): non-streaming multipart POST carrying the
            # source image bytes plus the minimal documented fields
            # derivable from the current request (task prompt text; no
            # points/boxes source exists in the current request shape, so
            # none is invented). The old streamed /responses transport is
            # retired and must not be reintroduced for this kind.
            item=assets[0];raw=base64.b64decode(item['encoded'])
            filename=item['meta'].get('name') or 'source-image'
            files={'image':(filename,raw,item['meta'].get('mime') or 'application/octet-stream')}
            if isinstance(body.get('text_prompt'),str) and body['text_prompt']:
                files['text']=(None,body['text_prompt'][:2000],'text/plain; charset=utf-8')
            return await self.request('POST',SAM_INTERACTIVE_PATH,files=files)
        return await self.request('POST','/responses',body=body,stream=False)

def output_text(response):
    return '\n'.join(c.get('text','') for item in response.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text')


class BasetenProvider(MetaProvider):
    """Baseten-hosted Muse Spark Open transport.

    Same call interface and the same wire contract as ``MetaProvider``
    (``/responses`` with a bearer key), so ``service.py`` swaps it in
    unchanged. Only the origin, the key reader and the catalog differ.

    Verified Serverless-inference contract (Baseten docs, 2026-10-26):
    Responses API at ``https://inference.baseten.co/v1/responses``,
    ``Authorization: Bearer $BASETEN_API_KEY``, model slug
    ``meta-models/muse-spark-open-1026`` (the dated snapshot; the
    hackathon key is scoped to it and the account also lists the undated
    ``meta-models/muse-spark-open`` alias, which this app does not name).
    Published rates per 1M tokens are
    input $1.25 / cache input $0.15 / output $4.25 — identical to the
    ``muse-spark-1.3`` row and to the alias, so the existing settlement
    arithmetic is reused with no second accounting path.

    Verified limitation this class does NOT paper over: Baseten accepts
    ``json_schema`` for this model but does not enforce it (the only Model
    API route without enforced structured outputs). Schema conformance is
    therefore validated downstream by the existing
    ``Frame.model_validate_json`` / ``grounding.validate`` call sites, which
    fail closed before any result is stored or settled. This transport never
    repairs, retries or coerces a non-conforming body.
    """

    def __init__(self,transport=None,key_reader=secrets.baseten_key):
        super().__init__(transport=transport,key_reader=key_reader)

    #: Origin and allowed paths; the shared helper applies the wire policy.
    base_url=BASETEN_BASE_URL
    allowed_paths=('/models','/responses')

    async def request(self,method,path,*,body=None,files=None,stream=False):
        secret=self.key_reader()
        if not secret:raise ProviderError('key_missing')
        # Same single-attempt hardening as the Meta transport: fixed origin,
        # no environment proxies, no redirects, no SDK retries, no tools.
        if path.split('?')[0] not in self.allowed_paths:raise ProviderError('endpoint_not_allowed')
        started=time.perf_counter()
        try:
            data,_=await _json_request(self.transport,self.base_url,{'Authorization':'Bearer '+secret},
                                       method,path,body=body,files=files)
            return json.loads(data),time.perf_counter()-started
        except ProviderError:raise
        except (UnicodeError,ValueError):raise ProviderError('transport_or_response_error') from None

    async def catalog(self):
        result,_=await self.request('GET','/models')
        if not isinstance(result.get('data'),list):raise ProviderError('invalid_catalog')
        return sorted({x['id'] for x in result['data'] if isinstance(x,dict) and isinstance(x.get('id'),str)})


class ModalSharedProvider(BasetenProvider):
    """Modal-provided Shared endpoint transport for Muse Spark Open (text only).

    Verified live contract (Modal announcment 2026-10-15 for repoID
    ``meta-models/Muse-Spark-Open-816B-A42B``; endpoint metadata 2026-10-27):
    ``POST <origin>/v1/responses``, ``Authorization: Bearer
    wk-<proxy-token-id>.ws-<proxy-token-secret>``, ``GET <origin>/v1/models``
    for the executable model list. Published rates per 1M tokens are input
    $1.25 / cached input $0.15 / output $4.25 — identical to the
    ``muse-spark-1.3`` row, so settlement reuses one arithmetic path and no
    GPU-time accounting is invented for this route.

    Two contracts this class enforces rather than assumes:

    * **Identity substitution.** The app's internal id ``modal-shared:muse-
      spark-open`` is an alias and must never reach the wire. ``infer``
      substitutes the locally pinned exact remote id in the request body, and
      the response's reported model is checked against that same exact string.
      A catalog identity that does not match fails closed; nothing normalizes
      case, slashes or versions, and no alternate slug is ever tried.
    * **Single attempt.** Origin comes only from the validated environment
      value re-checked here at call time; there is no redirect, proxy, retry,
      stream or tool path, and the response is size- and time-bounded.
    """

    def __init__(self,transport=None,key_reader=secrets.modal_shared_key,
                 config_reader=modal_shared_config.read_config):
        MetaProvider.__init__(self,transport=transport,key_reader=key_reader)
        self.config_reader=config_reader

    async def request(self,method,path,*,body=None,files=None,stream=False):
        secret=self.key_reader()
        if not secret:raise ProviderError('key_missing')
        if files is not None or stream:raise ProviderError('endpoint_not_allowed')
        config=self.config_reader()
        # Revalidate the injected configuration through the same origin policy:
        # a bad origin never becomes a request, and no fallback host is used.
        origin=modal_config.endpoint_origin(config.get('origin') or '')
        if not origin:raise ProviderError('modal_shared_configuration_incomplete')
        if path not in (modal_shared_config.CATALOG_PATH,modal_shared_config.RESPONSES_PATH):
            raise ProviderError('endpoint_not_allowed')
        started=time.perf_counter()
        try:
            data,_=await _json_request(self.transport,origin+modal_shared_config.BASE_SUFFIX,
                                       {'Authorization':'Bearer '+secret},method,
                                       path[len(modal_shared_config.BASE_SUFFIX):],body=body)
            return json.loads(data),time.perf_counter()-started
        except ProviderError:raise
        except (UnicodeError,ValueError):raise ProviderError('transport_or_response_error') from None

    async def catalog(self,remote_model_id=None):
        """Explicit user-click account check: one GET /v1/models, nothing else."""
        # The pinned id is required here too: a catalog read without one could
        # record a grant for whatever the endpoint happens to list, which is
        # exactly the wrong-model-billed-at-this-alias'-rates failure.
        pinned=remote_model_id or self.config_reader().get('remote_model_id')
        if not pinned:raise ProviderError('remote_model_id_unconfigured')
        result,_=await self.request('GET',modal_shared_config.CATALOG_PATH)
        if not isinstance(result.get('data'),list):raise ProviderError('invalid_catalog')
        ids=sorted({x['id'] for x in result['data']
                    if isinstance(x,dict) and isinstance(x.get('id'),str)})
        if pinned not in ids:raise ProviderError('model_identity_mismatch')
        return ids

    async def infer(self,model,body,assets,remote_model_id=None):
        if assets:raise ProviderError('endpoint_not_allowed')
        if not isinstance(body,dict) or body.get('model')!=getattr(model,'id',None):
            raise ProviderError('modal_shared_invalid_request')
        pinned=remote_model_id or self.config_reader().get('remote_model_id')
        if not pinned:raise ProviderError('remote_model_id_unconfigured')
        payload={**body,'model':pinned}
        result,elapsed=await self.request('POST',modal_shared_config.RESPONSES_PATH,body=payload)
        if not isinstance(result,dict) or result.get('model')!=pinned:
            # Fail closed on any response identity other than the pinned exact
            # id: no coercion, no case folding, no "closest match" acceptance.
            raise ProviderError('unexpected_model_identity')
        # Identity remap: only after the exact remote id was confirmed does the
        # app-facing response carry the internal alias, so downstream validation
        # sees the registered id while the wire saw (and returned) the real one.
        return {**result,'model':model.id},elapsed

class OpenAIProvider(BasetenProvider):
    """First-party OpenAI Responses transport for the pinned test snapshot.

    Contract source (fetched 2026-10-27,
    https://developers.openai.com/api/docs/models/gpt-5.4-mini): global origin
    ``https://api.openai.com/v1``, model snapshot
    ``gpt-5.4-mini-2026-03-17`` (400000 context, 272000 max input, input
    $0.75 / cached input $0.075 / output $4.50 per 1M), Responses API with
    Structured Outputs and reasoning effort ``none`` by default.

    This is a separate small test beside the Modal route: nothing here runs
    on import, no request is issued by the registry or by readiness checks,
    and the route is reported as ``test_only_live_not_run``. The wire policy is
    the shared single-attempt helper (fixed origin, no redirects, no proxy, no
    retries, bounded size/time), reused unchanged from ``BasetenProvider``.

    Request hardening this class enforces on every call, because the caller
    builds the body from a stored preview:

    * ``store`` is always false — no request may be retained by the provider.
    * ``service_tier`` is ``default``; no priority/flex billing mode.
    * ``tools`` stays empty and no asset bytes are transmitted, so no hosted
      tool, web search or file upload can be introduced by a caller.
    * The field set is a strict allowlist, so a Meta-specific field that the
      Responses API does not accept (``session_id``, ``text_prompt``,
      ``mode``, ``audioEncoding``) fails closed instead of being forwarded.
    """

    base_url=OPENAI_BASE_URL
    allowed_paths=('/models','/responses')
    #: Fields the pinned /responses request may carry. Everything the service
    #: body builder emits for a reasoning model is here; nothing else is sent.
    allowed_fields=('model','input','store','instructions','max_output_tokens',
                    'tools','text','service_tier')

    def __init__(self,transport=None,key_reader=secrets.openai_key):
        super().__init__(transport=transport,key_reader=key_reader)

    async def infer(self,model,body,assets):
        if assets:raise ProviderError('openai_assets_not_supported')
        if not isinstance(body,dict) or body.get('model')!=OPENAI_MODEL_ID:
            raise ProviderError('openai_invalid_request')
        extra=set(body)-set(self.allowed_fields)
        if extra or body.get('store') is True or body.get('tools'):
            raise ProviderError('openai_invalid_request')
        payload={**body,'store':False,
                 'service_tier':body.get('service_tier') or 'default'}
        # Text-only, no tools: every input part must be plain text.
        for item in payload.get('input') or []:
            content=item.get('content') if isinstance(item,dict) else None
            for part in content if isinstance(content,list) else []:
                if not isinstance(part,dict) or part.get('type')!='input_text':
                    raise ProviderError('openai_invalid_request')
        result,elapsed=await self.request('POST','/responses',body=payload)
        if not isinstance(result,dict) or result.get('model')!=OPENAI_MODEL_ID:
            # The pinned snapshot is what the recorded prices belong to; any
            # other reported identity fails closed with no remap or coercion.
            raise ProviderError('unexpected_model_identity')
        return result,elapsed

#: Local free-mode loopback transport (L2). Loopback only: 127.0.0.1,
#: ::1, or localhost. No other host, no DNS names, no LAN IPs, no public IPs.
_LOCAL_HOSTS = frozenset(('127.0.0.1', '::1', 'localhost'))

#: Single allowed chat path on the local loopback server (OpenAI-style).
LOCAL_CHAT_PATH = '/v1/chat/completions'

#: No retry beyond a single send: at most one HTTP request per call.
_LOCAL_MAX_ATTEMPTS = 1

#: Deterministic sampling for the local path only (cloud bodies carry no
#: temperature and stay untouched). The 4B profile's recorded floor.
LOCAL_TEMPERATURE = 0.0


def local_prompt_text(body):
    """Flatten a reasoning request body into the one chat prompt sent locally.

    Single source of truth: ``prepare()`` calls this to enforce the declared
    context bound exactly, and ``LocalProvider.infer`` calls it again to build
    the request. Instructions, the serialized task/snapshot text blocks and the
    pinned JSON schema all travel in one user message, because llama.cpp's chat
    endpoint takes chat messages rather than the cloud ``input`` block shape.
    """
    parts = []
    instructions = body.get('instructions')
    if isinstance(instructions, str) and instructions:
        parts.append(instructions)
    for item in body.get('input') or []:
        if not isinstance(item, dict):
            continue
        for block in item.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'input_text':
                text = block.get('text')
                if isinstance(text, str) and text:
                    parts.append(text)
    fmt = (body.get('text') or {}).get('format') or {}
    schema = fmt.get('schema') if isinstance(fmt, dict) else None
    if schema is not None:
        parts.append('<json_schema>' + json.dumps(schema, sort_keys=True,
                                                  separators=(',', ':')) + '</json_schema>')
    return '\n\n'.join(parts)


def _local_base_or_raise(base_url):
    """Validate the loopback base URL at construction; fail fast otherwise.

    Uses the design's ``local_unreachable`` code (raised as ProviderError, not
    plain ValueError, because ``provider.py`` names that error type).
    """
    try:
        parts = urlsplit(base_url or '')
    except ValueError:
        raise ProviderError('local_unreachable') from None
    if parts.scheme != 'http' or not parts.hostname:
        raise ProviderError('local_unreachable') from None
    if parts.username is not None or parts.password is not None:
        raise ProviderError('local_unreachable') from None
    if parts.hostname.lower() not in _LOCAL_HOSTS:
        raise ProviderError('local_unreachable') from None
    return base_url


class LocalProvider:
    """Loopback-only local transport. Same call interface as MetaProvider.

    Exposes no pricing and invents no cost fields (zero-spend layer).

    Two distinct identities are carried, because they are not the same string:
    ``model`` is the app/registry id used for the identity check, and
    ``wire_model`` is the exact alias the operator launched the server with
    (llama.cpp ``--alias``) and therefore the only value the server will accept.
    ``context`` is the declared ``-c`` bound.

    ``bearer`` is the per-run secret the launcher generated and published for
    this run. It is sent as an ``Authorization: Bearer`` header and nowhere else:
    never in a body, a URL, a log line, a preview, a receipt or the registry
    snapshot. A loopback bind is not a security boundary on its own — any other
    process, and any web page in a browser on this machine, can reach
    127.0.0.1 — so the bearer is what actually authorizes a request.

    ``template_overhead_tokens`` is the chat-template overhead the launcher
    measured against the real server at launch. The prompt bound is derived from
    UTF-8 bytes (one byte can never yield more than
    :data:`local_runtime.MAX_TOKENS_PER_UTF8_BYTE` tokens) with this overhead and
    the output allocation reserved. An exact token count is taken at execute
    time, after the researcher consented.
    """

    def __init__(self, base_url, model=None, transport=None, timeout=30,
                 wire_model=None, context=None, bearer=None,
                 template_overhead_tokens=None):
        self.base_url = _local_base_or_raise(base_url)
        self.model = model
        self.wire_model = wire_model or model
        self.context = context
        self.transport = transport
        self.timeout = timeout
        self.bearer = bearer or None
        if not self.bearer:
            # A loopback bind is not an authorization boundary: without the
            # per-run secret this transport could only reach a server that
            # accepts anonymous requests, which is exactly the state the app
            # refuses to run in. Fail at construction rather than send.
            raise ProviderError('local_run_secret_missing')
        self.template_overhead_tokens = template_overhead_tokens

    def _headers(self):
        """The one header this transport may add. Never logged, never echoed."""
        return {'Authorization': 'Bearer ' + self.bearer}

    async def _get_json(self, path, timeout=None, authenticated=True):
        """One GET of a documented endpoint, parsed as JSON.

        Not routed through :meth:`request` because that method is deliberately
        pinned to the single chat POST path; these are the read-only identity and
        accounting endpoints. Same wire policy: no proxies, no redirects, one
        attempt. ``authenticated=False`` is used only for the deliberate negative
        check that the server really requires this run's bearer.
        """
        headers = self._headers() if authenticated else {}
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers,
                transport=self.transport, trust_env=False,
                follow_redirects=False, timeout=timeout or self.timeout) as client:
            async with asyncio.timeout((timeout or self.timeout) + 10):
                response = await client.get(path)
        if response.status_code >= 300:
            raise ProviderError('provider_http_' + str(response.status_code))
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError('provider_response_too_large')
        body = response.json()
        if not isinstance(body, dict):
            raise ProviderError('transport_or_response_error')
        return body

    async def probe_anonymous_refused(self):
        """Whether an UNAUTHENTICATED ``GET /v1/models`` is refused (401/403).

        A server that answers 200 without a credential is not this app's local
        runtime: it is an anonymous listener that ignores ``Authorization``, and
        the bearer this app generates proves nothing against it. Any answer other
        than 401/403 — a 200, a 404, a connection error — means "not proved
        required", never "probably fine".
        """
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport,
                    trust_env=False, follow_redirects=False,
                    timeout=local_runtime.PROBE_TIMEOUT) as client:
                async with asyncio.timeout(local_runtime.PROBE_TIMEOUT):
                    response = await client.get(local_runtime.PROBE_PATH)
        except (httpx.HTTPError, TimeoutError, UnicodeError, ValueError):
            return False
        return response.status_code in (401, 403)

    async def _post_json(self, path, payload, timeout=None):
        """One authenticated POST to a documented endpoint, parsed as JSON."""
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(),
                transport=self.transport, trust_env=False,
                follow_redirects=False, timeout=timeout or self.timeout) as client:
            async with asyncio.timeout((timeout or self.timeout) + 10):
                response = await client.post(path, json=payload)
        if response.status_code >= 300:
            raise ProviderError('provider_http_' + str(response.status_code))
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError('provider_response_too_large')
        body = response.json()
        if not isinstance(body, dict):
            raise ProviderError('transport_or_response_error')
        return body

    async def props(self):
        """The server's own report of what it is running: alias and real n_ctx.

        Readiness must be about the server that is actually listening, not the
        values this app intended to launch: an unrelated listener on the port
        would satisfy a bare ``GET /v1/models``. The observed alias and the
        context from ``default_generation_settings.n_ctx`` are compared with the
        launched binding, and any difference is a mismatch rather than a pass.
        """
        body = await self._get_json(local_runtime.PROPS_PATH,
                                    timeout=local_runtime.PROBE_TIMEOUT)
        observed_alias = body.get('model_alias')
        generation = body.get('default_generation_settings')
        observed_ctx = generation.get('n_ctx') if isinstance(generation, dict) else None
        return {'model_alias': observed_alias if isinstance(observed_alias, str) else None,
                'n_ctx': observed_ctx if type(observed_ctx) is int else None,
                'n_ctx_train': body.get('n_ctx_train')
                              if type(body.get('n_ctx_train')) is int else None,
                'total_slots': body.get('total_slots')
                               if type(body.get('total_slots')) is int else None}

    async def request(self, method, path, *, body=None):
        if path.split('?')[0] != LOCAL_CHAT_PATH or method != 'POST':
            raise ProviderError('endpoint_not_allowed')
        payload = dict(body or {})
        if self.model is not None:
            payload.setdefault('model', self.model)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(base_url=self.base_url,
                    headers=self._headers(), transport=self.transport,
                    trust_env=False, follow_redirects=False, timeout=self.timeout) as client:
                async with asyncio.timeout(self.timeout + 10):
                    # Single send only: no retry loop (design §3.5/§5.3 —
                    # a failed local call fails the job, never re-issues).
                    for _ in range(_LOCAL_MAX_ATTEMPTS):
                        response = await client.request(method, path, json=payload)
                    if response.status_code >= 300:
                        # Existing provider vocabulary (same generic branch
                        # as MetaProvider): the HTTP status is the preserved
                        # local cause — no billing-implying code is invented.
                        raise ProviderError('provider_http_' + str(response.status_code))
                    data = response.content
                    if len(data) > 24 * 1024 * 1024:
                        raise ProviderError('provider_response_too_large')
                    result = json.loads(data)
            if not isinstance(result, dict):
                raise ProviderError('transport_or_response_error')
            return result, time.perf_counter() - started
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, UnicodeError, ValueError) as exc:
            # Local cause preserved via chaining: connection refused,
            # timeout, and malformed body stay identifiable as local
            # transport/response failures. Never retried, never completed.
            raise ProviderError('transport_or_response_error') from exc

    async def catalog(self):
        # Config-based, no network: the executable local set is
        # administrator-configured (design §2.1.2), so the catalog is the
        # configured tag. Server reachability is never claimed here.
        if self.model is None:
            raise ProviderError('local_unreachable')
        return [self.model]

    async def probe(self):
        """One authenticated readiness check: alias on the wire AND /props identity.

        Sends no researcher text, no instructions and no assets. Three read-only
        GETs: ``/v1/models`` and ``/props`` with the per-run bearer, then one
        UNAUTHENTICATED ``/v1/models`` that must be refused — a server answering
        it with 200 is some other listener, so the bearer would prove nothing.
        Exactly one attempt each inside a fixed short deadline — a failure is
        reported, never retried, never converted into a fallback.

        Returns ``(status, detail)`` where ``status`` is an exact token in the
        recorded probe vocabulary: ``local_ready`` only when the configured wire
        alias is listed by the server *and* the server's own ``/props`` report
        names that alias with the launched ``n_ctx``. ``detail`` carries only the
        served alias list, the observed alias/context and elapsed time — never
        request content, never the bearer.
        """
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(base_url=self.base_url,
                    headers=self._headers(), transport=self.transport,
                    trust_env=False, follow_redirects=False,
                    timeout=local_runtime.PROBE_TIMEOUT) as client:
                async with asyncio.timeout(local_runtime.PROBE_TIMEOUT):
                    response = await client.get(local_runtime.PROBE_PATH)
        except (httpx.TimeoutException, TimeoutError):
            return 'local_timeout', {'elapsed_seconds': time.perf_counter() - started}
        except httpx.HTTPError:
            return 'local_unreachable', {'elapsed_seconds': time.perf_counter() - started}
        except (UnicodeError, ValueError):
            return 'local_unreachable', {'elapsed_seconds': time.perf_counter() - started}
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            return ('local_http_' + str(response.status_code),
                    {'elapsed_seconds': elapsed, 'http_status': response.status_code})
        if len(response.content) > 1024 * 1024:
            return 'local_malformed_response', {'elapsed_seconds': elapsed}
        try:
            body = response.json()
        except ValueError:
            return 'local_malformed_response', {'elapsed_seconds': elapsed}
        data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(data, list):
            return 'local_malformed_response', {'elapsed_seconds': elapsed}
        served = sorted({x['id'] for x in data
                         if isinstance(x, dict) and isinstance(x.get('id'), str)})[:32]
        if self.wire_model not in served:
            # A reachable server that does not serve the configured alias is a
            # mismatch, not a pass: never relabelled, never auto-selected.
            return 'local_model_mismatch', {'elapsed_seconds': elapsed, 'served_models': served}
        # The wire list is not enough: /props is where the server states what it
        # actually loaded and with which context.
        try:
            observed = await self.props()
        except ProviderError as exc:
            return (exc.code, {'elapsed_seconds': time.perf_counter() - started,
                               'served_models': served})
        except (httpx.HTTPError, TimeoutError):
            return ('local_unreachable',
                    {'elapsed_seconds': time.perf_counter() - started,
                     'served_models': served})
        except (UnicodeError, ValueError):
            return ('local_malformed_response',
                    {'elapsed_seconds': time.perf_counter() - started,
                     'served_models': served})
        detail = {'elapsed_seconds': time.perf_counter() - started,
                  'served_models': served,
                  'observed_model_alias': observed['model_alias'],
                  'observed_context': observed['n_ctx'],
                  'observed_context_train': observed['n_ctx_train']}
        # Presence equality, not "whatever was reported is fine": an absent alias
        # or context is not agreement. ``total_slots`` is deliberately NOT part
        # of the readiness identity: this runtime reports AUTO slots
        # (``--parallel`` default, kv_unified=true) with the full declared ``-c``
        # genuine per request, so slot count says nothing about whether the
        # prompt bound holds. Requiring one slot would refuse a working launch
        # for no reason. It is reported for the record only.
        if observed['model_alias'] != self.wire_model:
            return 'local_model_mismatch', detail
        if self.context is None or observed['n_ctx'] != self.context:
            # A server running a different window than the one approved would
            # make every prompt bound this app computed wrong, and a missing
            # report proves nothing at all.
            return 'local_context_mismatch', detail
        if not await self.probe_anonymous_refused():
            # The bearer must be load-bearing, not decorative: a server that
            # answers an unauthenticated request is some other listener.
            return 'local_anonymous_access', detail
        return local_runtime.READY_TOKEN, detail

    async def exact_prompt_tokens(self, prompt):
        """Token count the server itself reports for the exact rendered prompt.

        Called only at execute time, i.e. after the researcher consented to send
        this text to the loopback server — before consent, only the synthetic
        launch probe and the byte bound are used.

        Returns ``(tokens, overhead)`` or ``None`` when the server does not answer
        in the documented shape. ``None`` is not a failure: the caller then relies
        on the proven byte bound, which is a real upper bound, so nothing is ever
        sent that could exceed the window.
        """
        payload = {'messages': [{'role': 'user', 'content': prompt}]}
        try:
            rendered = await self._post_json(local_runtime.APPLY_TEMPLATE_PATH, payload)
            text = rendered.get('prompt')
            if not isinstance(text, str) or not text:
                return None
            counted = await self._post_json(local_runtime.TOKENIZE_PATH,
                                            {'content': text, 'add_special': True})
        except (ProviderError, httpx.HTTPError, TimeoutError, UnicodeError, ValueError):
            return None
        tokens = counted.get('tokens')
        if not isinstance(tokens, list):
            return None
        return len(tokens)

    async def infer(self, model, body, assets):
        """One chat-completions call against the operator's loopback server.

        Only text reasoning is served: this server is chat-only, so any asset
        or non-reasoning kind is refused before sending rather than silently
        dropped from the prompt.

        The returned dict is normalized to the cloud response shape the shared
        pipeline reads (``model``/``status``/``output``/``usage``), because
        ``execute()``'s identity gate, status gate, grounding validation and
        usage accounting are provider-agnostic and must not be duplicated or
        weakened for the local path. ``Assistant`` (the server's real alias) is
        reported as ``model`` only when it matches; anything else raises
        ``unexpected_model_identity`` at the existing gate.

        Context enforcement, in this order and all after consent:

        1. the exact token count of the rendered prompt, from the server's own
           ``/apply-template`` + ``/tokenize``;
        2. the proven byte bound (UTF-8 bytes ×
           :data:`local_runtime.MAX_TOKENS_PER_UTF8_BYTE`, plus the measured
           template overhead, plus the output allocation) when the exact count is
           unavailable.

        An over-budget prompt is refused, never truncated: the server is launched
        with ``--no-context-shift``, so a prompt that does not fit is its error,
        not a silently rotated window.
        """
        if getattr(model, 'kind', None) not in ('reasoning', 'local_reasoning'):
            raise ProviderError('endpoint_not_allowed')
        if getattr(model, 'id', None) is not None and self.model is not None \
                and model.id != self.model:
            raise ProviderError('local_model_mismatch')
        if assets:
            raise ProviderError('local_assets_not_supported')
        prompt = local_prompt_text(body)
        if not prompt:
            raise ProviderError('local_empty_prompt')
        max_tokens = body.get('max_output_tokens')
        output_tokens = (max_tokens if isinstance(max_tokens, int)
                         and not isinstance(max_tokens, bool) and max_tokens > 0 else None)
        if self.context is not None:
            exact = await self.exact_prompt_tokens(prompt)
            if exact is not None:
                reserve_out = (output_tokens if output_tokens is not None
                               else local_runtime.DEFAULT_MAX_OUTPUT_TOKENS)
                won = exact + reserve_out <= self.context
            else:
                won = local_runtime.prompt_fits(
                    prompt, self.context,
                    overhead_tokens=self.template_overhead_tokens,
                    max_output_tokens=output_tokens)
            if not won:
                raise ProviderError('local_prompt_over_context')
        payload = {'model': self.wire_model or getattr(model, 'id', None),
                   'messages': [{'role': 'user', 'content': prompt}],
                   'temperature': LOCAL_TEMPERATURE,
                   'stream': False}
        if output_tokens is not None:
            payload['max_tokens'] = output_tokens
        raw, elapsed = await self.request('POST', LOCAL_CHAT_PATH, body=payload)
        return self._normalize(raw, getattr(model, 'id', None)), elapsed

    def _normalize(self, raw, model_id):
        """Map one chat completion to the cloud response envelope, or fail.

        Only the shapes the shared pipeline consumes are produced. A response
        carrying no assistant text, a finish reason other than ``stop``, or a
        ``model`` echoing an alias other than the one requested (a different
        server than the operator configured) is an error: an empty, cut-off or
        misrouted answer must never be recorded as completed.
        """
        choices = raw.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError('local_response_invalid')
        choice = choices[0]
        message = choice.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError('local_response_empty')
        if choice.get('finish_reason') not in (None, 'stop'):
            raise ProviderError('local_response_incomplete')
        reported = raw.get('model')
        if reported is not None and reported != self.wire_model:
            # The server answered as some other alias than the one this job was
            # approved for. Never relabel it into the requested model.
            raise ProviderError('unexpected_model_identity')
        usage = raw.get('usage') if isinstance(raw.get('usage'), dict) else {}
        normalized_usage = {}
        for source, target in (('prompt_tokens', 'input_tokens'),
                               ('completion_tokens', 'output_tokens'),
                               ('total_tokens', 'total_tokens')):
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                normalized_usage[target] = value
        return {'model': model_id,
                'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': content}]}],
                'usage': normalized_usage}
