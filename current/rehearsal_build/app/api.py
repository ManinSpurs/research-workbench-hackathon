"""FastAPI backend, lite §4.1 API contract v0 (Tier 1 subset). Async endpoints await the adapter (lite §4.2)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
import base64,json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .harness import DATA, Conflict, Harness
from .preparation_queue import PreparationQueue
from . import canvas
from . import chips
from . import chip_conversation
from . import citations
from . import claim_check
from . import latex
from . import library
from . import list_order
from . import secretary_log
from . import store
from .ai.service import AIService
from .ai import secrets as ai_secrets
from .ai.registry import registry as ai_registry
from .packs import PACKS, CAPABILITIES, DEFAULT_PACK
from . import packs as packs_module
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from openai import APIError

ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# One Origin policy for the local API, defined once and applied by the single
# `origin_guard` middleware in create_app(). CORS is not this check: it tells a
# page whether it may READ a response and never stops the request from running,
# and a cross-site <form>/fetch POST with Content-Type: text/plain is a "simple"
# request that no preflight protects. An earlier pair of guards covered only the
# AI routes and the LaTeX compile route, so a foreign page could still reach
# POST /session/end and destroy session-only questions before deletion.
#
# The rule, deliberately narrow: a request that carries NO Origin is a local
# client (TestClient, a CLI, a build script) and stays compatible. A request that
# carries an Origin must name one of the accepted workbench origins -- on every
# method that can change stored state, and additionally on the read paths that
# run a model. ORIGINS is read at call time, so an in-place replacement at
# startup (packaged build, random frontend port) takes effect immediately.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def model_route(path: str) -> bool:
    """The local routes that can start a model call (/library/screen is the only
    library one). Their protections predate this policy and are kept."""
    return path.startswith("/ai/") or "/ai/" in path or path == "/library/screen"


def origin_rejected(request: Request) -> bool:
    """True when a present Origin is not accepted for this method/path."""
    origin = request.headers.get("origin")
    if origin is None or origin in ORIGINS:
        return False
    # A present, unaccepted Origin -- including the literal "null" and an empty
    # header value: fail closed rather than guess what a client meant.
    return request.method.upper() not in SAFE_METHODS or model_route(request.url.path)


async def confirm_voice_question(h, qid, body):
    """Explicit voice-confirmation gate (VOICE_QUESTION_CAPTURE_EN §4/§7).

    Only this entry point may turn a completed transcription into a question.
    `execute()` never auto-fires. Body accepts ONLY confirmed_text,
    source_asset_id and source_sha256. Any other key is rejected.
    """
    if not isinstance(body, dict):
        raise Conflict("confirmed_text, source_asset_id and source_sha256 are required")
    if set(body) != {"confirmed_text", "source_asset_id", "source_sha256"}:
        raise Conflict("body must carry exactly confirmed_text, source_asset_id and source_sha256")
    confirmed_text = body.get("confirmed_text", "")
    source_asset_id = body.get("source_asset_id")
    source_sha256 = body.get("source_sha256")
    if not isinstance(confirmed_text, str) or not confirmed_text.strip():
        raise Conflict("confirmed_text must be nonempty text")
    if not isinstance(source_asset_id, str) or not source_asset_id:
        raise Conflict("source_asset_id is required")
    if not isinstance(source_sha256, str) or not source_sha256:
        raise Conflict("source_sha256 is required")
    question = h.question(qid)
    asset = h.db.execute(
        "SELECT * FROM ai_assets WHERE id=? AND question_id=?", (source_asset_id, qid)
    ).fetchone()
    if asset is None:
        raise Conflict("source asset belongs to another question or does not exist")
    stored_sha = json.loads(asset["meta_json"]).get("sha256")
    if stored_sha != source_sha256:
        raise Conflict("source_sha256 does not match the registered asset")
    job = h.db.execute(
        "SELECT * FROM ai_jobs WHERE question_id=? AND status IN ('completed','completed_known','completed_unknown_charge') ORDER BY created_at DESC",
        (qid,),
    ).fetchone()
    if job is None:
        raise Conflict("no completed transcription job for this question")
    result = json.loads(job["result_json"]) if job["result_json"] else None
    source_asset = (result or {}).get("outputs", {}).get("source_asset") if isinstance(result, dict) else None
    if not isinstance(source_asset, dict) or source_asset.get("id") != source_asset_id:
        raise Conflict("no completed transcription for this asset")
    # Existing capture path with the user-confirmed string as original_text.
    # No dedicated linkage column exists on questions; record the linkage via
    # the existing events payload (no migration, no question-text mutation).
    created = await h.create_question(question["project_id"], confirmed_text)
    store.log_event(
        h.db, question["project_id"], created["question_id"],
        "voice_question_confirmed",
        {"confirmed_text": confirmed_text, "source_asset_id": source_asset_id,
         "source_sha256": source_sha256},
    )
    h.db.commit()
    return {**created, "source_asset_id": source_asset_id, "source_sha256": source_sha256}


def err(code, message, status):
    return JSONResponse({"error": code, "message": message, "details": {}}, status_code=status)


def conflict_code(e):
    """The stable code for a Conflict, defaulting to the historical "conflict".

    A :class:`claim_check.Blocked` names its own obstacle (``cloud_off``,
    ``account_not_ready``, ``local_not_ready``) so the UI can localize the "why
    is this blocked" card instead of matching English. Every other Conflict is
    unchanged: same 409, same "conflict" code, same message.
    """
    return getattr(e, "code", None) or "conflict"


def create_app(db_path: str | None = None, data_dir: Path | None = None) -> FastAPI:
    state = {}

    @asynccontextmanager
    async def lifespan(app):
        state["h"] = Harness(db_path or os.environ.get("WB_DB", "workbench.db"), data_dir or DATA)
        state["ai"] = AIService(state["h"])
        # Claim–evidence verification. It shares the one AIService (and therefore
        # the one spend ledger, account catalog, settings and provider factory),
        # so there is no second gate or budget path. Built here, once, so a
        # prepared check and its execution always see the same providers.
        state["claim"] = claim_check.ClaimCheckService(state["h"], state["ai"])
        state["preparation"] = PreparationQueue(state["h"])
        await state["preparation"].start()
        # Installed chips become usable packs before the first request: a chip the
        # researcher made is a real research field, not a file that only lists.
        try:
            chips_module.load_installed_packs(state["h"].data)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                await state["preparation"].close()
            finally:
                state["h"].db.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        # Applied here, before any handler runs, so a rejected request has no side
        # effect at all: nothing is written and no model is called. One policy
        # (origin_rejected) replaces the two route-shaped guards this file used to
        # carry, and now covers /session/end and every other state-changing route.
        if origin_rejected(request):
            return err("origin_rejected", "This workbench accepts writes from its own local origin only", 403)
        return await call_next(request)

    H = lambda: state["h"]  # noqa: E731

    def guard(fn):
        try:
            return fn()
        except Conflict as e:
            return err(conflict_code(e), str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    async def aguard(coro):
        try:
            return await coro
        except (UnexpectedModelBehavior, UsageLimitExceeded, APIError, TimeoutError):
            return err("model_stopped", "Model request failed or reached its allowance; inspect recorded usage", 502)
        except Conflict as e:
            return err(conflict_code(e), str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    @app.get("/ai/registry")
    async def ai_registry_route():
        return {**ai_registry(), "settings": state["ai"].settings()}

    @app.post("/ai/settings")
    async def ai_settings(body: dict):
        return guard(lambda: state["ai"].configure(body.get("cloud_enabled"), body.get("budget_usd", 150)))

    @app.post("/ai/verify-account")
    async def ai_verify(body: dict | None = None):
        # Optional provider names the account to check; the default stays Meta,
        # so an existing caller that posts nothing behaves exactly as before.
        # The local provider has no account: this dispatches to the explicit
        # loopback readiness probe (authenticated GET /v1/models plus /props,
        # carrying only this run's bearer and no researcher text).
        provider = (body or {}).get("provider") or "meta"
        return await aguard(state["ai"].verify_account(provider))

    @app.post("/ai/keys")
    async def ai_save_key(body: dict):
        # One container per provider. The key travels one way only: it is
        # written to the owner-only file and never echoed, logged or returned.
        payload = body if isinstance(body, dict) else {}
        provider = payload.get("provider")
        # The provider id selects a fixed on-disk path, so it is checked against
        # the registered set here rather than trusted to the writer.
        if not isinstance(provider, str) or provider not in ai_secrets.PROVIDER_SECRETS:
            return err("conflict", "Unknown provider; no key file exists for it", 409)
        try:
            ai_secrets.save(provider, payload.get("key"))
        except ValueError as exc:
            return err("conflict", str(exc), 409)
        return {"provider": provider, "saved": True,
                "key_present": ai_secrets.present(provider),
                "env_override": ai_secrets.env_override(provider)}

    @app.get("/questions/{qid}/ai/assets")
    async def ai_assets(qid: str):
        return guard(lambda: {"assets": state["ai"].list_assets(qid)})

    @app.get("/questions/{qid}/ai/assets/{aid}/content")
    async def ai_asset_content(qid: str, aid: str):
        def content():
            H().question(qid)
            row=H().db.execute("SELECT * FROM ai_assets WHERE id=? AND question_id=?", (aid,qid)).fetchone()
            if row is None:raise KeyError("Asset not found")
            return Response(content=base64.b64decode(row["encoded"]), media_type=json.loads(row["meta_json"])["mime"],
                            headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})
        return guard(content)

    @app.post("/questions/{qid}/ai/assets")
    async def ai_asset_register(qid: str, body: dict):
        return guard(lambda: state["ai"].register_asset(qid, body))

    @app.post("/questions/{qid}/ai/prepare")
    async def ai_prepare(qid: str, body: dict):
        return guard(lambda: state["ai"].prepare(qid, body))

    @app.post("/ai/jobs/{jid}/execute")
    async def ai_execute(jid: str, body: dict):
        async with state["preparation"].foreground():
            return await aguard(state["ai"].execute(jid, body.get("preview_hash"), body.get("consent")))

    @app.post("/ai/jobs/{jid}/review")
    async def ai_review(jid: str, body: dict):
        return guard(lambda: state["ai"].review(jid, body))

    @app.get("/questions/{qid}/ai/jobs")
    async def ai_jobs(qid: str):
        return guard(lambda: {"jobs": state["ai"].jobs(qid)})

    @app.get("/questions/{qid}/ai/jobs/{jid}/receipt")
    async def ai_receipt(qid: str, jid: str):
        def receipt():
            if jid not in {j["id"] for j in state["ai"].jobs(qid)}:
                raise KeyError("AI task not found")
            return state["ai"].receipt(jid, question_id=qid)
        return guard(receipt)

    @app.get("/questions/{qid}/ai/jobs/{jid}/review")
    async def ai_review_readback(qid: str, jid: str):
        return guard(lambda: {"reviews": state["ai"].review_readback(qid, jid),
                              "provider_billed_cost_usd": None, "billing_status": "NOT_VERIFIED"})

    @app.post("/questions/{qid}/ai/voice-confirm")
    async def ai_voice_confirm(qid: str, body: dict):
        async def confirm():
            return await confirm_voice_question(H(), qid, body)
        return await aguard(confirm())

    # --- Claim–evidence verification (app/claim_check.py) ---
    # The consent contract is the AI pipeline's own: prepare returns a preview
    # (destination, model, exact transmitted content, maximum reserved cost),
    # execute needs that preview's hash plus an explicit consent flag, and the
    # spend ledger is the shared ai_spend table. Nothing here is a bypass.
    @app.post("/projects/{pid}/claim-checks/prepare")
    async def claim_check_prepare(pid: str, body: dict):
        return guard(lambda: state["claim"].prepare(pid, body))

    @app.post("/projects/{pid}/claim-checks/numbers")
    async def claim_check_numbers(pid: str, body: dict):
        # The free half of the feature: numeric reconciliation is a local
        # comparison of the claim's numbers with a stored table, so it needs no
        # AI, no consent, no preview hash and writes no record or spend row. It
        # is deliberately reachable while cloud use is off.
        return guard(lambda: state["claim"].numbers(pid, body))

    @app.post("/projects/{pid}/claim-checks/{cid}/execute")
    async def claim_check_execute(pid: str, cid: str, body: dict):
        async with state["preparation"].foreground():
            return await aguard(state["claim"].execute(
                cid, body.get("preview_hash"), body.get("consent"), pid))

    @app.get("/projects/{pid}/claim-checks")
    async def claim_checks(pid: str, draft_id: str | None = None):
        return guard(lambda: {"checks": state["claim"].list(pid, draft_id)})

    @app.post("/projects/{pid}/claim-checks/{cid}/decide")
    async def claim_check_decide(pid: str, cid: str, body: dict):
        return guard(lambda: state["claim"].decide(cid, body, pid))

    @app.get("/projects/{pid}/claim-checks/{cid}/receipt")
    async def claim_check_receipt(pid: str, cid: str):
        return guard(lambda: state["claim"].receipt(cid, pid))

    @app.get("/questions/{qid}/canvas")
    async def get_canvas(qid: str):
        return guard(lambda: canvas.read(H(), qid))

    @app.put("/questions/{qid}/canvas")
    async def save_canvas(qid: str, body: dict):
        return guard(lambda: canvas.save(H(), qid, body))

    @app.get("/domain-packs")
    async def domain_packs():
        # Every usable pack: the two samples plus every installed chip. A chip the
        # researcher made for urban policy must appear here, or it cannot be chosen.
        return {"packs": [{**p.model_dump(), **p.identity()} for p in packs_module.all_packs().values()],
                "default_pack": DEFAULT_PACK, "capabilities": CAPABILITIES,
                "pipeline": "legacy_four_stage", "h2_frame_resolve": "NOT_IMPLEMENTED"}

    @app.get("/projects/{pid}/pack-memory")
    async def pack_memory(pid: str, pack_id: str | None = None):
        # No pack named means the project's own chip (the historical default for a
        # project that predates chips); naming another chip's pack is refused by the
        # harness isolation guard instead of reading across chips.
        return guard(lambda: H().pack_memory(pid, pack_id))

    @app.get("/questions/{qid}/context")
    async def question_context(qid: str):
        return guard(lambda: (H().question(qid), H().question_context(qid))[1])

    @app.get("/projects")
    async def projects():
        # The researcher's saved card order, else the historical creation order.
        return guard(lambda: {"projects": list_order.projects(H())})

    @app.put("/projects/order")
    async def order_projects(body: dict):
        # Exact body {"project_ids": [...]} naming every current project once;
        # anything else is a 409 with no row written, so no order is partial.
        return guard(lambda: list_order.save_projects(H(), body))

    @app.post("/projects")
    async def register(body: dict):
        return guard(lambda: H().register_project(body.get("kind", "nyc"), body.get("pack_id")))

    @app.post("/projects/upload")
    async def register_upload(body: dict):
        return guard(lambda: H().register_upload(body.get("name"), body.get("csv_text"), body.get("dictionary"),
                                                body.get("pack_id")))

    @app.post("/projects/{pid}/chip")
    async def move_project_chip(pid: str, body: dict):
        # Membership changes only here, never as a side effect: a project keeps the
        # chip it was created under until this explicit call. Refused with a readable
        # 409 when a question is already pinned to a different pack, because that pin
        # is the record of what was asked.
        return guard(lambda: H().move_project_chip(pid, body.get("pack_id")))

    @app.get("/projects/{pid}/clarifications")
    async def pending_clarifications(pid: str, pack_id: str | None = None):
        return guard(lambda: H().pending_clarifications(pid, pack_id))

    @app.get("/projects/{pid}/profile")
    async def profile(pid: str):
        return guard(lambda: H().profile(pid))

    @app.post("/projects/{pid}/questions")
    async def ask(pid: str, body: dict):
        # No pack named means the project's own chip; the harness resolves that, and
        # refuses a pack that belongs to a different chip.
        async with state["preparation"].foreground():
            return await aguard(H().create_question(pid, body.get("text", ""), body.get("memory", "remember"), body.get("dataset_ids"),
                                                    body.get("pack_id"), body.get("capability_id", "mock_text")))

    @app.post("/questions/{qid}/run")
    async def run(qid: str):
        async with state["preparation"].foreground():
            return await aguard(H().run(qid))

    @app.get("/tasks/{tid}")
    async def task(tid: str):
        row = H().db.execute("SELECT id, question_id, project_id, status, created_at FROM task_runs WHERE id=?", (tid,)).fetchone()
        if row is None:
            return err("not_found", "task not found", 404)
        used = H().db.execute("SELECT stage, requests, input_tokens, output_tokens, cost_usd FROM usage_ledger WHERE task_id=?", (tid,))
        return {**dict(row), "usage": [dict(u) for u in used]}

    @app.post("/tasks/{tid}/cancel")
    async def cancel(tid: str):
        return guard(lambda: H().cancel_task(tid))

    @app.post("/clarifications/{cq_id}/responses")
    async def respond(cq_id: str, body: dict):
        async with state["preparation"].foreground():
            return await aguard(H().respond(cq_id, body.get("kind", ""), body.get("value"), body.get("project_id"),
                                            body.get("client_action_id")))

    @app.post("/projects/{pid}/decisions")
    async def decide(pid: str, body: dict):
        return guard(lambda: H().decide(pid, body.get("question_id", ""), body.get("verdict", ""), body.get("reason"),
                                        body.get("client_action_id")))

    @app.get("/briefing")
    async def launch_briefing():
        return guard(H().launch_briefing)

    @app.get("/projects/{pid}/revisit-conditions")
    async def revisit_conditions(pid: str, pack_id: str | None = None):
        return guard(lambda: H().revisit_conditions(pid, pack_id))

    @app.post("/projects/{pid}/revisit-conditions")
    async def record_revisit(pid: str, body: dict, pack_id: str | None = None):
        return guard(lambda: H().write_revisit(pid, body, pack_id=pack_id))

    @app.post("/projects/{pid}/revisit-conditions/{cid}/actions")
    async def revisit_action(pid: str, cid: str, body: dict, pack_id: str | None = None):
        return guard(lambda: H().write_revisit(pid, body, cid, pack_id=pack_id))

    @app.get("/projects/{pid}/briefing")
    async def briefing(pid: str, pack_id: str | None = None):
        return guard(lambda: H().briefing(pid, pack_id))

    @app.get("/questions/{qid}/deletion-preview")
    async def preview(qid: str):
        return guard(lambda: H().deletion_preview(qid))

    @app.delete("/questions/{qid}")
    async def delete(qid: str):
        return guard(lambda: H().delete_question(qid))

    @app.post("/questions/{qid}/shadow")
    async def shadow(qid: str):
        return await aguard(H().shadow(qid))

    @app.post("/session/end")
    async def end_session():
        return H().end_session()

    @app.get("/projects/{pid}/events")
    async def events(pid: str):
        rows = H().db.execute("SELECT id, question_id, kind, payload FROM events WHERE project_id=? ORDER BY id", (pid,))
        return {"events": [dict(r) for r in rows]}

    @app.get("/projects/{pid}/usage")
    async def usage(pid: str):
        rows = H().db.execute("SELECT stage, adapter, requests, input_tokens, output_tokens, cost_usd, cost_note FROM usage_ledger"
                              " WHERE project_id=? ORDER BY id", (pid,))
        return {"usage": [dict(r) for r in rows]}

    # --- Background Preparation, Slice B: scope + allowance API (design §§1-3) ---
    # New routes only; no existing route touched. Harness-side validation
    # (subset of live input_packet allowed_sources, blocked always excluded,
    # bundle_sha256 == projects.input_version, eligibility/idempotency checks)
    # lives in Slice A methods; these routes validate body shape following the
    # confirm_voice_question exact-shape precedent and map Conflict->409 /
    # KeyError->404 via the existing guard pattern.
    # Harness interface called (Slice A owns these methods):
    #   approve_background_scope(pid: str, allowed_sources: list[str]) -> dict
    #   mark_preparation(qid: str, object_type: str, object_id: str,
    #                    scope_id: str, client_action_id: str) -> dict
    #   list_preparations(qid: str) -> dict

    @app.post("/projects/{pid}/background-scope")
    async def approve_background_scope(pid: str, body: dict):
        def approve():
            if not isinstance(body, dict) or set(body) != {"allowed_sources"}:
                raise Conflict("body must carry exactly allowed_sources")
            allowed = body.get("allowed_sources")
            if (not isinstance(allowed, list)
                    or any(not isinstance(s, str) or not s.strip() for s in allowed)):
                raise Conflict("allowed_sources must be a list of non-empty strings")
            return H().approve_background_scope(pid, allowed)
        return guard(approve)

    @app.post("/questions/{qid}/prepare-request")
    async def prepare_request(qid: str, body: dict):
        def mark():
            expected = {"object_type", "object_id", "scope_id", "client_action_id"}
            if not isinstance(body, dict) or set(body) != expected:
                raise Conflict("body must carry exactly object_type, object_id, scope_id and client_action_id")
            for key in ("object_type", "object_id", "scope_id", "client_action_id"):
                value = body.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise Conflict(f"{key} must be nonempty text")
            result = H().mark_preparation(qid, body["object_type"], body["object_id"],
                                          body["scope_id"], body["client_action_id"])
            state["preparation"].enqueue(qid, result["job_id"])
            return result  # acceptance receipt; GET preparations is current state
        return guard(mark)

    @app.get("/questions/{qid}/preparations")
    async def preparations(qid: str):
        return guard(lambda: H().list_preparations(qid))

    @app.post("/questions/{qid}/preparations/{jid}/{action}")
    async def preparation_action(qid: str, jid: str, action: str, body: dict):
        def perform():
            if body:
                raise Conflict("lifecycle controls require an empty body")
            queue = state["preparation"]
            queue._owned(qid, jid)
            if action == "step":
                queue.enqueue(qid, jid)
                return H()._preparation_result(H()._preparation_job(jid))
            if action not in ("pause", "resume", "cancel"):
                raise Conflict("unknown preparation control")
            return getattr(queue, action)(qid, jid)
        return guard(perform)

    @app.post("/questions/{qid}/prepared-cards/{cid}/commit")
    async def preparation_commit(qid: str, cid: str, body: dict):
        def commit():
            if body:
                raise Conflict("commit requires an empty body")
            H().question(qid)
            card = H().db.execute("SELECT * FROM prepared_cards WHERE id=? AND question_id=?",
                                  (cid, qid)).fetchone()
            if card is None:
                raise KeyError("prepared card not found for this question")
            return H().commit_prepared_card(cid)
        return guard(commit)

    # --- Project-scoped LaTeX sources and explicit, local compile/PDF routes ---
    # Commands are passed to latex_module.capabilities() with no arguments after the zero-argument check.
    latex_module = latex

    @app.get("/capabilities/latex")
    async def latex_capabilities():
        return guard(latex_module.capabilities)

    @app.get("/projects/{pid}/drafts")
    async def drafts(pid: str):
        return guard(lambda: latex_module.list_drafts(H(), pid))

    @app.post("/projects/{pid}/drafts")
    async def create_draft(pid: str, body: dict):
        return guard(lambda: latex_module.create_draft(H(), pid, body))

    @app.get("/drafts/{did}")
    async def read_draft(did: str):
        return guard(lambda: latex_module.read_draft(H(), did))

    @app.put("/drafts/{did}")
    async def save_draft(did: str, body: dict):
        return guard(lambda: latex_module.save_draft(H(), did, body))

    from .latex_compile import Compiler
    from fastapi.responses import Response
    compiler = Compiler()

    @app.post("/drafts/{did}/compile")
    async def compile_draft(did: str, body: dict):
        return await aguard(compiler.compile(H(), did, body))

    @app.get("/drafts/{did}/pdf")
    async def draft_pdf(did: str, revision: int):
        return guard(lambda: Response(compiler.pdf(H(), did, revision),
                                      media_type="application/pdf",
                                      headers={"Cache-Control": "no-store",
                                               "X-Content-Type-Options": "nosniff"}))

    # --- Citation Composer: selected sentence + registered evidence -> proposed diff, then a decision ---
    # New routes only; no existing route touched. The composer PROPOSES: it stores the
    # researcher's own text and never applies it to a draft, so no draft route is involved.
    citations_module = citations

    def cguard(fn):
        # Same error shape as guard(), plus a 400 for a request the researcher can fix
        # (an unfixable server-side fault must never look like a bad request).
        try:
            return fn()
        except citations_module.Invalid as e:
            return err("invalid_request", str(e), 400)
        except Conflict as e:
            return err("conflict", str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    @app.get("/projects/{pid}/citations")
    async def project_citations(pid: str):
        return cguard(lambda: citations_module.list_citations(H(), pid))

    @app.post("/projects/{pid}/citations")
    async def create_citation(pid: str, body: dict):
        return cguard(lambda: citations_module.create_citation(H(), pid, body))

    @app.get("/citations/{cid}")
    async def read_citation(cid: str):
        return cguard(lambda: citations_module.read_citation(H(), cid))

    @app.post("/citations/{cid}/decide")
    async def decide_citation(cid: str, body: dict):
        return cguard(lambda: citations_module.decide_citation(H(), cid, body))

    @app.delete("/citations/{cid}")
    async def delete_citation(cid: str):
        return cguard(lambda: citations_module.delete_citation(H(), cid))

    # --- Secretary review log: the past secretary exchanges, kept or deleted by the person ---
    # New routes only; no existing route touched. This is a record, not a transcript:
    # nothing here is fed to a prompt, a briefing or any app/ai path.
    secretary_module = secretary_log

    def sguard(fn):
        # Same error shape as guard(), plus 400 for a request the person can fix.
        try:
            return fn()
        except secretary_module.Invalid as e:
            return err("invalid_request", str(e), 400)
        except Conflict as e:
            return err("conflict", str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    @app.get("/projects/{pid}/secretary-turns")
    async def project_secretary_turns(pid: str, pack_id: str | None = None, status: str | None = None,
                                      limit: int | None = None):
        return sguard(lambda: secretary_module.list_turns(H(), pid, pack_id, status, limit))

    @app.post("/projects/{pid}/secretary-turns")
    async def record_secretary_turn(pid: str, body: dict):
        return sguard(lambda: secretary_module.record_turn(H(), pid, body))

    @app.patch("/secretary-turns/{tid}")
    async def update_secretary_turn(tid: str, body: dict):
        return sguard(lambda: secretary_module.update_turn(H(), tid, body))

    @app.delete("/secretary-turns/{tid}")
    async def delete_secretary_turn(tid: str):
        return sguard(lambda: secretary_module.delete_turn(H(), tid))

    @app.post("/projects/{pid}/secretary-turns/clear-discarded")
    async def clear_discarded_secretary_turns(pid: str, status: str = "discarded"):
        return sguard(lambda: secretary_module.clear_turns(H(), pid, status))

    # --- Research library: folders, notes, PDFs, literal search, on-device screen ---
    # New routes only; no existing route touched. Everything here is local: the
    # store keeps metadata, bytes live under <data_dir>/library/, and the only
    # model that can answer /library/screen is the Apple on-device one (app/apple_ai.py).
    library_module = library

    @app.get("/library/tree")
    async def library_tree(project_id: str):
        return guard(lambda: library_module.tree(H(), project_id))

    @app.put("/library/order")
    async def library_order(body: dict):
        # Exact body {"project_id": "...", "item_ids": [...]} naming every current
        # PDF of that project once; answers with the same tree as GET /library/tree.
        return guard(lambda: library_module.save_order(H(), body))

    @app.post("/library/folders")
    async def library_create_folder(body: dict):
        return guard(lambda: library_module.create_folder(H(), body.get("project_id"), body.get("name"),
                                                         body.get("parent_id")))

    @app.patch("/library/folders/{fid}")
    async def library_update_folder(fid: str, body: dict):
        return guard(lambda: library_module.update_folder(H(), fid, body))

    @app.delete("/library/folders/{fid}")
    async def library_delete_folder(fid: str):
        return guard(lambda: library_module.delete_folder(H(), fid))

    @app.post("/library/notes")
    async def library_create_note(body: dict):
        return guard(lambda: library_module.create_note(H(), body))

    @app.post("/library/docs")
    async def library_create_doc(body: dict):
        return guard(lambda: library_module.create_doc(H(), body))

    @app.patch("/library/items/{iid}")
    async def library_update_item(iid: str, body: dict):
        return guard(lambda: library_module.update_item(H(), iid, body))

    @app.get("/library/docs/{iid}/file")
    async def library_doc_file(iid: str, project_id: str | None = None):
        def read():
            # The viewer appends project_id, so a file is only served inside its
            # own project when the caller names one.
            data, name = library_module.doc_file(H(), iid, project_id)
            return Response(content=data, media_type="application/pdf",
                            headers={"Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff",
                                     "Content-Disposition": f'inline; filename="{name}"'})
        return guard(read)

    @app.get("/library/search")
    async def library_search(project_id: str, q: str = ""):
        return guard(lambda: library_module.search(H(), project_id, q))

    @app.post("/library/screen")
    async def library_screen(body: dict):
        return guard(lambda: library_module.screen(H(), body))

    # --- Results tables for numeric reconciliation (app/library.py) ---
    # These two are guarded lazily: the table module is owned by a parallel
    # worker, so api.py must import and serve every other route even before it
    # lands. An absent implementation is an honest 409 naming what is missing,
    # never an ImportError at startup and never a silently skipped check.
    def _table_api(name):
        fn = getattr(library_module, name, None)
        if fn is None:
            raise Conflict(claim_check.TABLE_API_UNAVAILABLE)
        return fn

    @app.post("/projects/{pid}/library/tables")
    async def library_create_table(pid: str, body: dict):
        return guard(lambda: _table_api("create_table")(H(), pid, body))

    @app.get("/library/tables/{iid}")
    async def library_get_table(iid: str, project_id: str | None = None):
        def read():
            # Scoped like the doc-file route above: the caller names the project
            # and the load is filtered by it, so another project's table reads as
            # a missing table. Required, not optional: an unscoped read of a
            # numeric table would answer for any project that guessed the id.
            if not project_id:
                raise Conflict('project_id is required to read a results table')
            return _table_api("load_table")(H(), iid, project_id)
        return guard(read)

    # --- Chips: a domain pack as a portable file on disk (app/chips.py) ---
    # New routes only; no existing route touched. A chip is data, never code: the module
    # validates the archive (member names, caps, PNG magic), and nothing here executes it.
    # Installed chips live under <data dir>/chips/installed/; built-ins are exposed through
    # the same listing so the UI has one source of truth.
    chips_module = chips

    def chipguard(fn):
        # Same error shape as guard(), plus 400 for a chip or payload the researcher can fix.
        try:
            return fn()
        except chips_module.Invalid as e:
            return err("invalid_chip", str(e), 400)
        except Conflict as e:
            return err("conflict", str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    def chips_store():
        return chips_module.ChipStore(H().data)

    @app.get("/chips")
    async def chips_list():
        return chipguard(lambda: chips_store().list())

    @app.get("/chip-samples")
    async def chip_samples():
        from .demo_chip import demo_bytes
        import base64
        return {"samples": [{"pack_id": "sample_permit_notebook",
                             "filename": "sample_permit_notebook.chip",
                             "base64": base64.b64encode(demo_bytes()).decode("ascii")}]}

    @app.get("/chips/{pack_id}")
    async def chips_read(pack_id: str):
        return chipguard(lambda: chips_store().record(pack_id))

    @app.get("/chips/{pack_id}/projects")
    async def chips_projects(pack_id: str):
        # One chip's portfolio: a chip may hold several projects (one urban_policy
        # chip with tax_policy and regulation) or a single narrow one. Newest first,
        # with the question count each project actually holds. An id that names no
        # chip answers exactly like GET /chips/{pack_id}: a 404 keyed by the id.
        return chipguard(lambda: H().chip_projects(pack_id))

    @app.post("/chips/{pack_id}/workspace")
    async def chips_workspace(pack_id: str):
        return chipguard(lambda: H().open_chip_workspace(pack_id))

    @app.post("/chips/{pack_id}/import")
    async def chips_import(pack_id: str, body: dict):
        from .chip_import import import_notebook
        try:
            return await import_notebook(H(), pack_id, body)
        except chips_module.Invalid as e:
            return err("invalid_chip", str(e), 400)
        except Conflict as e:
            return err("conflict", str(e), 409)
        except KeyError as e:
            return err("not_found", str(e).strip("'"), 404)

    @app.get("/chips/{pack_id}/record")
    async def chips_record_read(pack_id: str):
        # The research record stored inside one chip: questions, answers, results, code
        # and harness notes. A chip that carries no record.json reads as the empty record
        # (which is what a built-in chip is, and what ChipStore.read_record returns for
        # one), so a record-less chip is not an error; only an id that names no chip is.
        return chipguard(lambda: chips_store().read_record(pack_id))

    @app.put("/chips/{pack_id}/record")
    async def chips_record_write(pack_id: str, body: dict):
        def write():
            # Same shape of check as POST /chips/install: the envelope is validated here,
            # every rule about the record and every cap come from app/chips.py, and the
            # record is data only — nothing inside it is executed, imported or evaluated.
            if not isinstance(body, dict):
                raise chips_module.Invalid("The record body must be a JSON object holding the record")
            extra = sorted(set(body) - {"record"})
            if extra:
                raise chips_module.Invalid(f"record does not accept the field(s): {', '.join(extra)}")
            record = body.get("record")
            if not isinstance(record, dict):
                raise chips_module.Invalid(
                    "record is required and must be a JSON object holding the chip record fields")
            return chips_store().write_record(pack_id, record)
        return chipguard(write)

    @app.get("/chips/{pack_id}/conversation")
    async def chips_conversation_read(pack_id: str):
        # The secretary conversation stored inside one chip as conversation.txt: the talk,
        # the decision each question reached and the evidence behind it. A chip that
        # carries none reads as empty text, so a chip without a log is not an error.
        return chipguard(lambda: chips_store().read_conversation(pack_id))

    @app.put("/chips/{pack_id}/conversation")
    async def chips_conversation_write(pack_id: str, body: dict):
        def write():
            # Same envelope rule as the record: one field, and every cap comes from
            # app/chips.py. The text is a note, never executed, imported or evaluated.
            if not isinstance(body, dict):
                raise chips_module.Invalid("The conversation body must be a JSON object holding the text")
            extra = sorted(set(body) - {"text"})
            if extra:
                raise chips_module.Invalid(f"conversation does not accept the field(s): {', '.join(extra)}")
            if "text" not in body:
                raise chips_module.Invalid("text is required: send the conversation as a string")
            return chips_store().write_conversation(pack_id, body["text"])
        return chipguard(write)

    @app.post("/chips/{pack_id}/conversation/compile")
    async def chips_conversation_compile(pack_id: str, body: dict):
        def compile_log():
            # Builds conversation.txt out of what the app already recorded (the secretary
            # turns, the decisions and their reasons, the evidence) and writes it into the
            # installed chip. Read-only against the records; no model call.
            if not isinstance(body, dict):
                raise chips_module.Invalid("The compile body must be a JSON object holding project_id")
            extra = sorted(set(body) - {"project_id"})
            if extra:
                raise chips_module.Invalid(
                    f"conversation compile does not accept the field(s): {', '.join(extra)}")
            project_id = body.get("project_id")
            if not isinstance(project_id, str) or not project_id.strip():
                raise chips_module.Invalid("project_id is required: name the project whose log to compile")
            members = H().chip_projects(pack_id)["projects"]
            if len(members) != 1 or members[0]["id"] != project_id:
                raise Conflict("compile requires the chip's sole linked project notebook")
            H().require_notebook_contents(project_id, pack_id)
            return chip_conversation.compose_and_store(H(), project_id, pack_id, chips_store())
        return chipguard(compile_log)

    @app.get("/chips/{pack_id}/art")
    async def chips_art(pack_id: str):
        def art():
            png = chips_store().art(pack_id)
            return Response(content=png, media_type="image/png",
                            headers={"Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff"})
        return chipguard(art)

    def accept_chip_file(body, *, open_existing=False):
        def accept():
            if not isinstance(body, dict):
                raise chips_module.Invalid("The install body must be a JSON object with path or base64")
            forms = {key for key in ("path", "base64") if body.get(key) is not None}
            extra = sorted(set(body) - {"path", "base64", "filename"})
            if extra:
                raise chips_module.Invalid(f"install does not accept the field(s): {', '.join(extra)}")
            if forms == {"path"}:
                return chips_store().install_path(body["path"], open_existing=open_existing)
            if forms == {"base64"}:
                if set(body) - {"base64", "filename"}:
                    raise chips_module.Invalid("base64 accepts only an optional filename beside it")
                return chips_store().install_base64(body["base64"], body.get("filename"),
                                                   open_existing=open_existing)
            raise chips_module.Invalid("Send either {\"path\": ...} or {\"base64\": ..., \"filename\": ...}, not both")
        return chipguard(accept)

    @app.post("/chips/install")
    async def chips_install(body: dict):
        return accept_chip_file(body)

    @app.post("/chips/open")
    async def chips_open(body: dict):
        return accept_chip_file(body, open_existing=True)

    @app.post("/chips/build")
    async def chips_build(body: dict):
        return chipguard(lambda: chips_module.build_result(body))

    @app.post("/chips/export/{pack_id}")
    async def chips_export(pack_id: str):
        def take_out():
            # Taking a chip out is when its notebook must be complete: the file leaves the
            # app, and whatever it does not carry cannot be recovered from inside it. So the
            # log is written into the chip first, and a chip that cannot be brought up to
            # date is not handed out as if it were -- the person would otherwise believe a
            # file holds a log it never received.
            #
            # A built-in chip is exported exactly as shipped: it has no project of its own to
            # narrate and the store refuses to rewrite it, so it is passed through untouched.
            if pack_id not in chips_module.PACKS:
                members = H().chip_projects(pack_id)["projects"]
                if len(members) == 1:
                    H().require_notebook_contents(members[0]["id"], pack_id)
                conversations = chip_conversation.store_on_export(H(), pack_id, chips_store())
                stored = [c for c in conversations if c.get("stored", True)]
                if conversations and not stored:
                    # More than one project works under this chip, and one file cannot hold
                    # two notebooks. Say so instead of writing one over the other.
                    raise chips_module.Invalid(
                        f"{len(conversations)} projects are working under {pack_id}, and the chip"
                        " holds one conversation.txt. Export it from a project that has its own"
                        " chip, or name the project whose log this file should carry")
            return chips_store().export(pack_id)
        return chipguard(take_out)

    @app.post("/chips/art/preview")
    async def chips_art_preview(body: dict):
        return chipguard(lambda: chips_module.label_preview(body))

    @app.delete("/chips/{pack_id}")
    async def chips_delete(pack_id: str):
        return chipguard(lambda: chips_store().delete(pack_id))

    return app


app = create_app()
