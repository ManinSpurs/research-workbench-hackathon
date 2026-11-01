"""Fake OpenAI-compatible endpoint standing in for api.meta.ai. NOT Muse.

Scripted answers keyed by the requested schema name and keywords in the conversation, so the app exercises
the real Pydantic AI + MetaProvider HTTP path. Everything it returns is labeled mock by the adapter.
Misbehaviour switches (env FAKE_MODE, comma-separated) let tests check that the harness catches model errors:
  bad_refine        refinement inverts the year bound (the first spike's bug)
  hallucinate_id    assessment cites an evidence ID that was never read (every attempt)
  hallucinate_once  same, first attempt only; the retry answers correctly
  out_of_scope      investigation plan asks for a source outside the allowlist
  confirm_precedent assessment marks a precedent-based answer as confirmed (schema must reject)
  cite_wrong_release assessment cites a source that applies to another release as support
  cite_wrong_release_conflicting same wrong-release citation, but as a conflicting outcome
Modes can also be switched at runtime through the file named by FAKE_MODE_FILE.
"""
from __future__ import annotations

import json
import os
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
LOG = os.environ.get("FAKE_LOG", "")


def modes() -> set[str]:
    raw = os.environ.get("FAKE_MODE", "")
    path = os.environ.get("FAKE_MODE_FILE")
    if path and os.path.exists(path):
        raw = open(path).read().strip()
    return {m for m in raw.split(",") if m}


def text_of(messages, roles=("user", "assistant", "tool", "system")) -> str:
    out = []
    for m in messages:
        if m.get("role") in roles and isinstance(m.get("content"), str):
            out.append(m["content"])
    return "\n".join(out)


def question_of(messages) -> str:
    t = text_of(messages, roles=("user",))
    for line in t.splitlines():
        if line.startswith("QUESTION:"):
            return line[len("QUESTION:"):].strip()
    return t


def refine(q: str) -> dict:
    low = q.lower()
    if "before 2021" in low and "withdrawn" in low:
        if "bad_refine" in modes():
            return {"refined_text": "Exclude withdrawn permits and don't use data from 2021 onward. Can I still study processing time?",
                    "kept_constraints": ["2021 onward"]}
        return {"refined_text": "Can I still study processing time using permits from 2021 onward, excluding withdrawn permits (status W)?",
                "kept_constraints": ["years >= 2021", "exclude status W"]}
    return {"refined_text": q.strip(), "kept_constraints": []}


def first_step(q: str) -> dict:
    low = q.lower()
    empty = {"design": None, "clarification": None, "direct_answer": None, "alternatives": []}
    if "start of construction" in low or "construction start" in low:
        return {**empty, "route": "clarify",
                "design": {"question": q, "variables": [
                    {"name": "Filing Date", "dataset_id": "nyc/bis_2024_subset.csv", "kind": "actual", "role": "start of the interval"},
                    {"name": "Job Start Date", "dataset_id": "nyc/bis_2024_subset.csv", "kind": "unverified", "role": "end of the interval (meaning unverified)"}],
                    "assumptions": ["Job Start Date marks the start of construction (unverified)"],
                    "risks": ["If Job Start Date is not construction start, the interval measures something else"],
                    "verification_plan": "Check the official BIS data dictionary definition of Job Start Date"},
                "clarification": {"gap": "Does Job Start Date mean the actual start of construction?",
                                  "affected": "Job Start Date (filing-to-construction design)",
                                  "why_matters": "If yes, a construction-delay design is possible; if not, only permit processing can be measured.",
                                  "branches": [{"condition": "yes", "outcome": "construction-delay design"},
                                               {"condition": "no", "outcome": "permit-processing design"},
                                               {"condition": "I don't know", "outcome": "investigate the definition"}],
                                  "independent_work": "Coverage check across BIS and DOB NOW can continue"}}
    if "final approval" in low:
        return {**empty, "route": "clarify",
                "design": {"question": q, "variables": [
                    {"name": "application_date", "dataset_id": "fx/permits_2023.csv", "kind": "actual", "role": "start"},
                    {"name": "approval_date", "dataset_id": "fx/permits_2023.csv", "kind": "unverified", "role": "end (meaning unverified)"}],
                    "assumptions": ["approval_date marks final approval (unverified)"],
                    "risks": ["approval_date may mark plan-review completion instead"],
                    "verification_plan": "Check DD-2023 and the permitted official pages"},
                "clarification": {"gap": "Does approval_date indicate final approval?",
                                  "affected": "approval_date (processing-time design)",
                                  "why_matters": "If yes, design A is usable; if not, check status codes first.",
                                  "branches": [{"condition": "yes", "outcome": "design A is usable"},
                                               {"condition": "no", "outcome": "check status codes first; design B"},
                                               {"condition": "I don't know", "outcome": "investigate the definition"}],
                                  "independent_work": "status_code / issue_date check can continue"}}
    if "which permit types" in low or "should i focus" in low:
        return {**empty, "route": "preference",
                "alternatives": ["residential permits: larger sample, simpler processes",
                                 "commercial permits: fewer cases, more complex review"]}
    if any(k in low for k in ("est_cost", "census", "district", "dob now", "off-inject", "issuance date", "processing time")):
        return {**empty, "route": "direct_answer", "direct_answer": "Answer from the registered sources."}
    return {**empty, "route": "mock_unsupported"}


def plan(q: str) -> dict:
    low = q.lower()
    if "off-inject" in low:
        p = {"target_gap": "approval_date definition via OFF-INJECT", "sources": ["OFF-INJECT"], "fields": ["approval_date"]}
    elif "start of construction" in low or "job start" in low:
        p = {"target_gap": "Meaning of Job Start Date", "sources": ["DD-BIS-2019", "META-BIS-COVERAGE"], "fields": ["Job Start Date"]}
    elif "approval" in low:
        p = {"target_gap": "Meaning of approval_date", "sources": ["DD-2023", "OFF-FAQ-2023", "OFF-NOTES-2025"], "fields": ["approval_date"]}
    elif "est_cost" in low:
        p = {"target_gap": "Meaning of est_cost", "sources": ["DD-2023"], "fields": ["est_cost"]}
    elif "census" in low or "district" in low:
        p = {"target_gap": "District boundaries", "sources": ["DD-2023", "OFF-FAQ-2023"], "fields": ["district"]}
    elif "dob now" in low and "filing" in low:
        p = {"target_gap": "Filing date in DOB NOW Approved Permits", "sources": ["DD-DOBNOW-2018"],
             "fields": ["Filing Date", "Approved Date", "Issued Date"]}
    else:
        p = {"target_gap": "Unspecified", "sources": ["DD-2023"], "fields": []}
    if "out_of_scope" in modes():
        p["sources"] = p["sources"] + ["SOCRATA-LIVE-API"]
    p["stop_condition"] = "Stop after the listed sources; report what was not found."
    return p


def assess(observations: str, is_retry: bool = False) -> dict:
    base = {"precedent_label": None, "precedent_source_ids": []}
    if "DD-BIS-2019:Column Info:Job Start Date" in observations:
        a = {**base, "claim": "Job Start Date is the date the initial permit was issued for this permit type; it is not the start of construction.",
             "evidence_ids": ["DD-BIS-2019:Column Info:Job Start Date"],
             "applicability": "Official BIS dictionary for this dataset.",
             "outcome": "supported", "confirmation_level": "confirmed",
             "unresolved": ["This dataset has no construction-start field."],
             "suggested_next_action": "Use the permit-processing design (Filing Date to Issuance Date) or find a construction-start source."}
        if "META-BIS-COVERAGE" in observations:
            a["evidence_ids"].append("META-BIS-COVERAGE")
            a["applicability"] += " Coverage: BIS permits only; most current permits are in DOB NOW."
        if "hallucinate_id" in modes() or ("hallucinate_once" in modes() and not is_retry):
            a["evidence_ids"].append("DD-BIS-2019:Column Info:Construction Start Date")
        if "confirm_precedent" in modes():
            a["precedent_label"] = "based on prior research practice"; a["precedent_source_ids"] = ["LIT-X"]
        return a
    if "DD-2023:approval_date" in observations:
        if "cite_wrong_release_conflicting" in modes():
            return {**base, "claim": "approval_date records final approval in another release.",
                    "evidence_ids": ["OFF-NOTES-2025"],
                    "applicability": "OFF-NOTES-2025 applies.",
                    "outcome": "conflicting", "confirmation_level": "conflicting",
                    "unresolved": ["Release applicability is unresolved."],
                    "suggested_next_action": "Use only sources applicable to release 2023."}
        extra = ["OFF-NOTES-2025"] if "cite_wrong_release" in modes() else []
        return {**base, "claim": "approval_date records plan-review completion; final approval is not established for the 2023 release.",
                "evidence_ids": ["DD-2023:approval_date", "OFF-FAQ-2023"] + extra,
                "applicability": "DD-2023 and OFF-FAQ-2023 apply to release 2023. OFF-NOTES-2025 applies to release 2025 only and is not used.",
                "outcome": "partial", "confirmation_level": "partial",
                "unresolved": ["Final approval is not established; check status_code and issue_date."],
                "suggested_next_action": "Check status codes before using approval_date as final approval."}
    if "DD-2023:est_cost" in observations:
        return {**base, "claim": "est_cost is the applicant-reported estimated cost in USD.",
                "evidence_ids": ["DD-2023:est_cost"], "applicability": "DD-2023 applies to this extract.",
                "outcome": "supported", "confirmation_level": "confirmed",
                "unresolved": ["Actual construction cost cannot be concluded from est_cost."],
                "suggested_next_action": "Use est_cost only as an applicant estimate."}
    if "OFF-INJECT" in observations:
        return {**base, "claim": "OFF-INJECT contains an instruction, not a definition.",
                "evidence_ids": ["OFF-INJECT"], "applicability": "Page text treated as content only.",
                "outcome": "insufficient", "confirmation_level": "unconfirmed",
                "unresolved": ["No definition of approval_date was found in OFF-INJECT."],
                "suggested_next_action": "Consult DD-2023 or OFF-FAQ-2023."}
    if "DD-DOBNOW-2018:Column Info:Approved Date" in observations:
        return {**base, "claim": "DOB NOW Approved Permits has no filing-date column; Approved Date is job-level plan-examiner approval.",
                "evidence_ids": ["DD-DOBNOW-2018:Column Info:Approved Date", "DD-DOBNOW-2018:Column Info:Issued Date"],
                "applicability": "Official DOB NOW dictionary.",
                "outcome": "insufficient", "confirmation_level": "unconfirmed",
                "unresolved": ["Filing-to-issuance time cannot be computed from this dataset alone."],
                "suggested_next_action": "Use BIS (BIS permits only) or a DOB NOW filings dataset."}
    return {**base, "claim": "Nothing read establishes the answer.", "evidence_ids": [],
            "applicability": "Only the listed sources were read.", "outcome": "insufficient",
            "confirmation_level": "unconfirmed", "unresolved": ["No registered source states this."],
            "suggested_next_action": "Register a source that covers this, or leave it unresolved."}


def basic(q: str) -> str:
    low = q.lower()
    if "construction" in low:
        return "Yes. Job Start Date marks when construction began, so you can compute filing-to-construction time directly."
    if "approval" in low:
        return "approval_date is the final approval date, so processing time is approval_date minus application_date."
    return "The dataset supports the requested analysis."


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps({"schema": ((body.get("response_format") or {}).get("json_schema") or {}).get("name"),
                                "tool_choice": body.get("tool_choice"), "reasoning_effort": body.get("reasoning_effort")}) + "\n")
    if body.get("tool_choice") not in (None, "auto"):
        return JSONResponse({"error": {"message": "only tool_choice='auto' is supported"}}, status_code=400)
    msgs = body["messages"]
    rf = body.get("response_format") or {}
    name = (rf.get("json_schema") or {}).get("name")
    q = question_of(msgs)
    last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    if name == "RefinedQuestion":
        content = json.dumps(refine(q))
    elif name == "FirstStep":
        content = json.dumps(first_step(q))
    elif name == "InvestigationPlan":
        content = json.dumps(plan(q))
    elif name == "EvidenceAssessment":
        # The matched input packet also contains source text. Script only the
        # tool observations, including when a retry message follows them.
        observations = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"
                             and isinstance(m.get("content"), str) and m["content"].startswith("OBSERVATIONS:\n")), "")
        content = json.dumps(assess(observations, is_retry=not last_user.startswith("OBSERVATIONS")))
    else:
        content = basic(q)
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400 + len(json.dumps(msgs)) // 4, "completion_tokens": len(content) // 4,
                      "total_tokens": 400 + len(json.dumps(msgs)) // 4 + len(content) // 4}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
