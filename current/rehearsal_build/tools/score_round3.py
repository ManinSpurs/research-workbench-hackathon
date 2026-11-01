"""Replay explicit blind AI judgments authored after reading all retained answers.

This is annotation serialization, not an automatic semantic scoring algorithm.
No inference, credential access, or paid replay occurs here.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.round3_cases import ROUND3_CASES

LABEL = "AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)"
FOLDER = Path(__file__).resolve().parents[2] / "logs/comparison_20261101T203959Z_cduuevnj"

# Explicit exceptions to supported entailment, indexed before unblinding.
EXCEPTIONS = {
    ("NYC-DATE-1", "B001", 5): ("unverifiable", "unknown",
        "Definitions distinguish milestones but do not establish researcher approval or estimand policy."),
    ("NYC-DATE-1", "B002", 1): ("unverifiable", "wrong",
        "Approved Date definition does not establish that it is the closest date to filing."),
    ("NYC-DATE-1", "B003", 3): ("supported", "unknown",
        "Registration scope is context; Job Filing Number definition does not establish the scope note."),
    ("NYC-DATE-1", "B005", 1): ("contradicted", "wrong",
        "Selected header also contains expired_date; approved_date and issued_date are not the only dates."),
    ("NYC-SEQUENCE-1", "B001", 1): ("unverifiable", "wrong",
        "Numeric issuance order does not define initial/subsequent/amendment filing order."),
    ("NYC-SEQUENCE-1", "B002", 0): ("unverifiable", "unknown",
        "Definition does not specify partition by job filing."),
    ("NYC-SEQUENCE-1", "B002", 1): ("unverifiable", "unknown",
        "Definition does not establish within-filing scope or a relationship to calendar dates."),
    ("BIS-FILING-1", "B001", 1): ("contradicted", "wrong",
        "Application filed is a completed administrative event; definition does not say it is not an event."),
    ("BIS-SEQUENCE-1", "B001", 2): ("supported", "unknown",
        "Joint definitions support the distinction, but Filing Status alone does not establish sequence mapping."),
    ("BIS-SEQUENCE-1", "B002", 3): ("supported", "unknown",
        "Job Type and Permit Type support work-type distinctions, not sequence-number renewal status."),
    ("BIS-SEQUENCE-1", "B004", 2): ("unverifiable", "unknown",
        "Successive renewals are documented; extensions are not established by the cited definition."),
}
FAILURES = {
    ("FX-APPROVAL-1", "B004"): "Proposes nonexistent plan_review_completion_date as actual and an ungrounded final_approval_date derivation.",
    ("NYC-DATE-1", "B002"): "Unsupported closest-field suggestion; absence citation does not prove schema absence.",
    ("NYC-DATE-1", "B005"): "Materially incomplete date inventory omits expired_date.",
    ("NYC-SEQUENCE-1", "B001"): "Conflates permit issuance sequence with filing/amendment sequence.",
    ("NYC-SEQUENCE-1", "B002"): "Adds unsupported within-filing scope and later-date interpretation.",
    ("BIS-FILING-1", "B001"): "Denies that filing records a completed event.",
    ("BIS-SEQUENCE-1", "B002"): "Proposes nonexistent job_identification_unit as an actual column.",
    ("BIS-SEQUENCE-1", "B004"): "Adds extensions without evidence.",
}
ABSENCE = {"B000": [0, 4], "B001": [0, 4], "B002": [0],
           "B003": [4], "B004": [0], "B005": [0, 3]}


def save(name, obj):
    (FOLDER / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main():
    packet = json.loads((FOLDER / "blind-scoring-packet.json").read_text())
    answers = json.loads((FOLDER / "blind-answers.json").read_text())
    evidence = {}
    for domain in ("fx", "nyc"):
        for rows in json.loads((FOLDER.parents[1] / "rehearsal_build/data" / domain / "sources.json").read_text()).values():
            if isinstance(rows, list):
                evidence.update({r["evidence_id"]: r for r in rows if isinstance(r, dict)})
    gold = {c.case_id: c for c in ROUND3_CASES}
    scores = {"label": LABEL, "scorer": "AI assistant, blind first pass",
              "human_review_status": "PENDING", "human_reviewer": None,
              "scored_at": datetime.now(timezone.utc).isoformat(), "cases": []}
    for case in packet["cases"]:
        cid = case["case_id"]
        c = gold[cid]
        case["fixture_status"] = "FROZEN"
        case["frozen_expectation"] = {
            "status": "FROZEN", "question": c.question,
            "gold_constraints": c.result["answer"],
            "expected_citation_ids": list(c.evidence_ids_present),
            "full_key_reference": "current/HARNESS_COMPARISON_PREREG_EN.md, round-3 revision 2"}
        out = {"case_id": cid, "arms": []}
        for arm in case["blinded_arms"]:
            code = arm["blind_code"]
            reason = FAILURES.get((cid, code))
            if cid == "FX-COST-1":
                reason = "Omits the required explicit limitation: not actual final cost."
            entry = {"blind_code": code, "task_success": reason is None,
                     "variable_method_correct": (cid, code) not in FAILURES,
                     "reason": reason or "Retained answer and claims meet the frozen key without a material contradictory addition.",
                     "human_task_success": None, "human_variable_method_correct": None,
                     "answer": answers[cid][code], "claims": []}
            for claim in arm["claims"]:
                index = claim["claim_index"]
                verdict, mark, rationale = EXCEPTIONS.get((cid, code, index), (
                    "supported", "supports",
                    "Checked against registered definitions and selected-file header; claim matches the stated event, code, identifier or release limitation."))
                if cid == "NYC-DATE-1" and index in ABSENCE.get(code, []):
                    mark = "unknown"
                    rationale = "Selected header supports absence; field definitions alone do not establish an exhaustive column inventory."
                citations = []
                for item in claim["citation_items"]:
                    eid = item["evidence_id"]
                    assert eid in evidence
                    citations.append({**item, "human_mark": None, "ai_mark": mark,
                                      "evidence": evidence[eid], "rationale": rationale})
                claim["human_claim_verdict"] = None
                for item in claim["citation_items"]:
                    item["human_mark"] = None
                entry["claims"].append({"claim_index": index, "text": claim["text"],
                                        "ai_verdict": verdict, "rationale": rationale,
                                        "human_claim_verdict": None, "citation_items": citations})
            out["arms"].append(entry)
        scores["cases"].append(out)
    packet["protocol"] = LABEL + "; round 3, approved paid execution; human review pending."
    packet["reviewer"] = None
    packet["rubric"]["reviewer"] = None
    packet["summary"]["reviewer"] = None
    save("blind-scoring-packet.json", packet)
    save("blind-scoring-sheet.json", {"label": LABEL, "cases": packet["cases"]})
    save("ai-proxy-scores.json", scores)
    # Only after durable blind scores exist does this serializer open the key.
    frozen_hash = hashlib.sha256((FOLDER / "ai-proxy-scores.json").read_bytes()).hexdigest()
    key = json.loads((FOLDER / "unblinding-key.json").read_text())
    summary = json.loads((FOLDER / "execution-summary.json").read_text())
    table = {}
    for c in scores["cases"]:
        for a in c["arms"]:
            m = key["cases"][c["case_id"]]["mapping"][a["blind_code"]]
            provider = summary["jobs"][m["job_index"]]["provider"]
            arm = m.get("arm_label", m.get("arm"))
            row = table.setdefault((provider, arm), {"provider": provider, "arm": arm,
                "planned": 10, "scored": 0, "success": 0, "supported": 0,
                "claims": 0, "wrong": 0, "unknown": 0, "citations": 0})
            row["scored"] += 1
            row["success"] += int(a["task_success"])
            for claim in a["claims"]:
                row["claims"] += 1
                row["supported"] += int(claim["ai_verdict"] == "supported")
                for citation in claim["citation_items"]:
                    row["citations"] += 1
                    row["wrong"] += int(citation["ai_mark"] == "wrong")
                    row["unknown"] += int(citation["ai_mark"] == "unknown")
    save("unblinded-results.json", {"label": LABEL, "blind_scores_sha256": frozen_hash,
         "unblinded_at": datetime.now(timezone.utc).isoformat(), "rows": list(table.values())})
    print(json.dumps(list(table.values()), indent=2))


if __name__ == "__main__":
    main()
