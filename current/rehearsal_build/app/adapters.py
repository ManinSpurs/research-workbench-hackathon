"""Model adapter (lite §4.2): turns a prompt into a typed result. Harness logic lives elsewhere.

With a loopback FAKE_BASE_URL it uses the fake endpoint (adapter="mock").
Real Muse is fail-closed unless WB_ALLOW_REAL_MUSE=1; opt-in uses META_API_KEY.
Mock usage caps are provisional and do not establish a hard real-provider spend ceiling.
Settings follow lite §10.2/§5.5: strict native JSON-schema output, retries=1, SDK retries off, explicit reasoning effort.
"""
from __future__ import annotations

import os
import asyncio
import math
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from dataclasses import dataclass

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai import Agent, ModelRetry, NativeOutput
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.meta import MetaProvider

from . import prompts
from .packs import instructions_for
from .results import EvidenceAssessment, FirstStep, InvestigationPlan, RefinedQuestion


@dataclass
class Usage:
    requests: int
    input_tokens: int | None
    output_tokens: int | None
    usage_complete: bool = True


@dataclass(frozen=True)
class CallLimits:
    request_limit: int = 2
    total_tokens_limit: int = 16000
    timeout: float = 60.0


_limits: ContextVar[CallLimits] = ContextVar("workbench_call_limits", default=CallLimits())


@contextmanager
def call_limits(*, request_limit: int = 2, total_tokens_limit: int = 16000,
                timeout: float = 60.0):
    """Task-local limits; nested scopes can only tighten the provisional mock caps.

    Tokens are checked after responses (the chat provider cannot pre-count input).
    The provider also receives a completion-token cap. This is not a hard dollar cap.
    """
    if (not isinstance(request_limit, int) or not isinstance(total_tokens_limit, int)
            or not math.isfinite(timeout)):
        raise ValueError("Invalid model call limits")
    parent = _limits.get()
    token = _limits.set(CallLimits(min(parent.request_limit, max(0, request_limit)),
                                 min(parent.total_tokens_limit, max(0, total_tokens_limit)),
                                 min(parent.timeout, max(0.0, timeout))))
    try:
        yield
    finally:
        _limits.reset(token)


@dataclass
class _CallObservation:
    attempts: int = 0


_observation: ContextVar[_CallObservation | None] = ContextVar("workbench_http_attempts", default=None)


async def _observe_request(request):
    observation = _observation.get()
    if observation is not None:
        if observation.attempts >= _limits.get().request_limit:
            raise UsageLimitExceeded("Model request budget exhausted")
        observation.attempts += 1


def _usage(u: RunUsage, attempts: int = 0) -> Usage:
    missing = bool(u.unmeasured_requests) or u.requests == 0 or attempts > u.requests
    return Usage(max(attempts, u.requests), None if missing else u.input_tokens,
                 None if missing else u.output_tokens,
                 not missing and attempts <= u.requests)


class ModelAdapter:
    def __init__(self) -> None:
        base = os.environ.get("FAKE_BASE_URL")
        if base:
            parsed = urlsplit(base)
            if (parsed.scheme not in {"http", "https"}
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username or parsed.password):
                raise ValueError("FAKE_BASE_URL must be a local loopback endpoint")
        elif os.environ.get("WB_ALLOW_REAL_MUSE") != "1":
            raise RuntimeError("Real Muse calls are disabled; explicit opt-in is required")
        self.label = "mock" if base else "muse"
        client = AsyncOpenAI(base_url=base or "https://api.meta.ai/v1",
                             api_key="fake-not-a-key" if base else os.environ.get("META_API_KEY"),
                             max_retries=0,
                             http_client=DefaultAsyncHttpxClient(
                                 trust_env=not bool(base), follow_redirects=False,
                                 event_hooks={"request": [_observe_request]}))
        self.model = OpenAIChatModel(os.environ.get("MUSE_MODEL", "muse-spark-1.3"),
                                     provider=MetaProvider(openai_client=client))
        self.settings = {"openai_reasoning_effort": os.environ.get("MUSE_REASONING_EFFORT", "low")}

    def _agent(self, out, instructions):
        return Agent(self.model, output_type=NativeOutput(out, strict=True), retries=1,
                     model_settings=self.settings, instructions=instructions_for(instructions))

    async def _run(self, agent, prompt, **kwargs):
        if self.label != "mock":
            raise RuntimeError("Live model execution is disabled in this revision")
        limits = _limits.get()
        usage = RunUsage()
        observation = _CallObservation()
        token = _observation.set(observation)
        try:
            if not limits.request_limit or not limits.total_tokens_limit or not limits.timeout:
                raise UsageLimitExceeded("No model call budget remains")
            async with asyncio.timeout(limits.timeout):
                result = await agent.run(
                    prompt, **kwargs, usage=usage,
                    usage_limits=UsageLimits(request_limit=limits.request_limit,
                                             total_tokens_limit=limits.total_tokens_limit),
                    model_settings={"max_tokens": min(4096, limits.total_tokens_limit)},
                )
            return result, _usage(usage, observation.attempts)
        except BaseException as exc:
            # Keep the original exception for control flow, but never log its text here:
            # provider errors can contain prompts, response bodies, or credentials.
            exc.workbench_usage = _usage(usage, observation.attempts)
            raise
        finally:
            _observation.reset(token)

    async def refine(self, question: str):
        r, usage = await self._run(self._agent(RefinedQuestion, prompts.REFINEMENT), f"QUESTION: {question}")
        return r.output, usage

    async def first_step(self, question: str, context: str):
        r, usage = await self._run(self._agent(FirstStep, prompts.FIRST_STEP), f"QUESTION: {question}\nCONTEXT: {context}")
        return r.output, ModelMessagesTypeAdapter.dump_json(r.all_messages()).decode(), usage

    async def plan(self, history_json: str | None, prompt: str):
        history = ModelMessagesTypeAdapter.validate_json(history_json) if history_json else None
        r, usage = await self._run(self._agent(InvestigationPlan, prompts.PLAN), prompt, message_history=history)
        return r.output, ModelMessagesTypeAdapter.dump_json(r.all_messages()).decode(), usage

    async def assess(self, history_json: str, observations_text: str, observed_ids: set[str]):
        agent = self._agent(EvidenceAssessment, prompts.ASSESS)

        @agent.output_validator
        def _only_read_ids(out: EvidenceAssessment) -> EvidenceAssessment:
            unread = [e for e in out.evidence_ids + out.precedent_source_ids if e not in observed_ids]
            if unread:
                raise ModelRetry(f"Cite only evidence IDs from the observations. Not read: {unread}")
            return out

        r, usage = await self._run(agent, f"OBSERVATIONS:\n{observations_text}",
                            message_history=ModelMessagesTypeAdapter.validate_json(history_json))
        return r.output, ModelMessagesTypeAdapter.dump_json(r.all_messages()).decode(), usage

    async def basic(self, question: str, materials: str):
        agent = Agent(self.model, output_type=str, retries=1, model_settings=self.settings, instructions=instructions_for(prompts.BASIC))
        r, usage = await self._run(agent, f"QUESTION: {question}\nMATERIALS: {materials}")
        return r.output, usage
