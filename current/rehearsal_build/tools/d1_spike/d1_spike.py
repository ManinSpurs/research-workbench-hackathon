"""D1 real-model spike (lite §10.2). Reference tool, not submission code.

Checks the four pass criteria with one command per phase:
  1. a real Muse call succeeds        2. output validates as ClarificationNeeded
  3. history stored -> new process resumes -> output validates as EvidenceAssessment
  4. usage recorded (or null with a reason)

Real run (D1, only after explicit authorization and budget review):
                 unset FAKE_BASE_URL
                 export WB_ALLOW_REAL_MUSE=1
                 export META_API_KEY=...   (never paste the key into files or logs)
                 python d1_spike.py phase1 spike.db native
                 python d1_spike.py phase2 spike.db native
Rehearsal:       export FAKE_BASE_URL=http://127.0.0.1:8199/v1  (with fake_meta_server.py running)

Modes, in the order to try (lite §10.2): native -> prompted -> tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.meta import MetaProvider

MODEL = os.environ.get("MUSE_MODEL", "muse-spark-1.3")
EFFORT = os.environ.get("MUSE_REASONING_EFFORT", "low")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Branch(Strict):
    condition: str = Field(min_length=1)
    outcome: str = Field(min_length=1)


class ClarificationNeeded(Strict):
    cq_id: str = Field(min_length=1)
    gap: str = Field(min_length=1)
    affected: str = Field(min_length=1)
    why_matters: str = Field(min_length=1)
    response_options: list[str] = Field(min_length=1)
    branches: list[Branch] = Field(max_length=3)
    independent_work: str = Field(min_length=1)


class EvidenceAssessment(Strict):
    claim: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    applicability: str = Field(min_length=1)
    confirmation_level: Literal["confirmed", "supported", "partial", "conflicting", "unconfirmed"]
    support_state: Literal["supported", "partial", "conflicting", "not_supported", "unconfirmed"]
    unresolved: list[str]


DICTIONARY_EXCERPT = (
    "Source DD_DOB_Permit_Issuance_2019-07-29.xlsx, sheet Column Info, row 'Job Start Date': "
    "\"The date that the initial permit was issued for this Permit Type.\""
)


def make_model() -> OpenAIChatModel:
    from openai import AsyncOpenAI
    from urllib.parse import urlsplit

    base = os.environ.get("FAKE_BASE_URL")
    if base:
        endpoint = urlsplit(base)
        if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("FAKE_BASE_URL must use a loopback HTTP endpoint")
        client = AsyncOpenAI(base_url=base, api_key="rehearsal-not-a-key", max_retries=0)
    else:
        if os.environ.get("WB_ALLOW_REAL_MUSE") != "1":
            raise RuntimeError("Real Muse calls disabled; require explicit WB_ALLOW_REAL_MUSE=1")
        api_key = os.environ.get("META_API_KEY")
        if not api_key:
            raise RuntimeError("META_API_KEY is required for explicitly authorized real Muse calls")
        client = AsyncOpenAI(base_url="https://api.meta.ai/v1", api_key=api_key, max_retries=0)
    # Only the Agent's retries=1 policy may retry; never stack SDK retries.
    provider = MetaProvider(openai_client=client)
    return OpenAIChatModel(MODEL, provider=provider)


def wrap(t, mode):
    if mode == "native":
        return NativeOutput(t, strict=True)
    return {"prompted": PromptedOutput, "tool": ToolOutput}[mode](t)


def usage_dict(r):
    u = r.usage() if callable(r.usage) else r.usage
    return {"requests": u.requests, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cost": None, "cost_note": "not returned by the API response; compute from the price sheet if needed"}


def store(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS runs(task TEXT PRIMARY KEY, history TEXT NOT NULL, cq_id TEXT NOT NULL,"
              " status TEXT NOT NULL, log TEXT NOT NULL)")
    return c


async def phase1(path, mode):
    agent = Agent(make_model(), output_type=wrap(ClarificationNeeded, mode), retries=1,
                  model_settings={"openai_reasoning_effort": EFFORT},
                  instructions=("You are a research assistant. Before recommending a design, ask exactly one "
                                "clarification whose answer would change the recommendation. Give at most three "
                                "conditional branches. Do not answer the clarification yourself."))
    t0 = time.time()
    r = await agent.run("Dataset: NYC DOB Permit Issuance (BIS). Columns include Filing Date, Issuance Date, "
                        "Job Start Date. Question: Can I study how long it takes from filing to the start of construction?")
    log = {"phase": 1, "mode": mode, "model": MODEL, "seconds": round(time.time() - t0, 1),
           "kind": "ClarificationNeeded", "output": r.output.model_dump(), "usage": usage_dict(r)}
    c = store(path)
    c.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
              ("t1", ModelMessagesTypeAdapter.dump_json(r.all_messages()).decode(), r.output.cq_id,
               "awaiting_clarification", json.dumps(log)))
    c.commit()
    print(json.dumps(log, indent=2))


async def phase2(path, mode):
    c = store(path)
    row = c.execute("SELECT history, status FROM runs WHERE task='t1'").fetchone()
    if row is None:
        raise SystemExit("no stored run: run phase1 first")
    history, status = row
    if status != "awaiting_clarification":
        raise SystemExit(f"rejected: clarification is not pending (status={status})")
    agent = Agent(make_model(), output_type=wrap(EvidenceAssessment, mode), retries=1,
                  model_settings={"openai_reasoning_effort": EFFORT},
                  instructions=("Assess only the evidence provided below. Cite it by the given source ID. "
                                "Do not mark anything confirmed unless the source states it. List what remains unresolved."))
    t0 = time.time()
    r = await agent.run(f"I don't know. Find the definition.\nEvidence read: {DICTIONARY_EXCERPT}",
                        message_history=ModelMessagesTypeAdapter.validate_json(history))
    log = {"phase": 2, "mode": mode, "model": MODEL, "seconds": round(time.time() - t0, 1),
           "kind": "EvidenceAssessment", "output": r.output.model_dump(), "usage": usage_dict(r)}
    c.execute("UPDATE runs SET status='completed' WHERE task='t1'")
    c.commit()
    print(json.dumps(log, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("phase1", "phase2") or sys.argv[3] not in ("native", "prompted", "tool"):
        raise SystemExit("usage: python d1_spike.py phase1|phase2 <db path> native|prompted|tool")
    step, path, mode = sys.argv[1:]
    asyncio.run(phase1(path, mode) if step == "phase1" else phase2(path, mode))
