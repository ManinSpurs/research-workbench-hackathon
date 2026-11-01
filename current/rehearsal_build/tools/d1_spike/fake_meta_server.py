"""Fake OpenAI-compatible server that stands in for api.meta.ai during a rehearsal.

It is NOT Muse. It returns canned payloads so we can see exactly what Pydantic AI
sends for each output mode, and test pause/resume across processes over real HTTP.
Every request body is appended to requests.jsonl for inspection.
Behaviour mirrors one documented Meta constraint: tool_choice other than "auto" is rejected.
"""
import json
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

LOG = sys.argv[2] if len(sys.argv) > 2 else "requests.jsonl"
app = FastAPI()

CQ = {
    "cq_id": "cq-rehearsal", "gap": "Does Job Start Date mean the actual start of construction?",
    "affected": "Job Start Date (filing-to-construction design)",
    "why_matters": "If yes, a construction-delay design is possible; if not, only permit processing can be measured.",
    "response_options": ["answer", "I don't know", "use this source", "later", "decline", "cancel"],
    "branches": [{"condition": "yes", "outcome": "construction-delay design"},
                 {"condition": "no", "outcome": "permit-processing design"},
                 {"condition": "I don't know", "outcome": "investigate the definition"}],
    "independent_work": "coverage check across BIS and DOB NOW can continue",
}
EA = {
    "claim": "Job Start Date meaning in the BIS dataset",
    "evidence_ids": ["DD_DOB_Permit_Issuance:Column Info:Job Start Date"],
    "applicability": "Official BIS dictionary for this dataset.",
    "confirmation_level": "confirmed", "support_state": "supported",
    "unresolved": ["This dataset has no construction-start field."],
}


def pick(schema_text: str):
    return CQ if "cq_id" in schema_text else EA


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    with open(LOG, "a") as f:
        f.write(json.dumps(body) + "\n")
    tc = body.get("tool_choice")
    if tc not in (None, "auto"):
        return JSONResponse({"error": {"message": f"tool_choice={tc!r} not supported; only 'auto'"}}, status_code=400)
    usage = {"prompt_tokens": 321, "completion_tokens": 54, "total_tokens": 375}
    rf = body.get("response_format")
    msg = {"role": "assistant", "content": None}
    if rf and rf.get("type") == "json_schema":
        msg["content"] = json.dumps(pick(json.dumps(rf)))
    elif body.get("tools"):
        tool = body["tools"][0]["function"]
        msg["tool_calls"] = [{"id": "call_1", "type": "function",
                              "function": {"name": tool["name"],
                                           "arguments": json.dumps(pick(json.dumps(tool)))}}]
    else:
        system = " ".join(m.get("content") or "" for m in body["messages"] if m.get("role") in ("system", "developer") and isinstance(m.get("content"), str))
        msg["content"] = json.dumps(EA if "evidence_ids" in system else CQ)
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model"), "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": usage}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
