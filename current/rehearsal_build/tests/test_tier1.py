"""Tier 1 must-pass tests (lite §9.1) against the fake Meta endpoint, plus counterexamples.

All model answers are scripted (adapter=mock). These tests check harness behavior: scope, validation, state,
briefing, deletion, usage. They do not measure model quality."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEMO = "Can I study how long it takes from filing to the start of construction?"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="session")
def fake(tmp_path_factory):
    mode_file = tmp_path_factory.mktemp("fake") / "mode.txt"
    mode_file.write_text("")
    port = free_port()
    env = {**os.environ, "FAKE_MODE_FILE": str(mode_file), "PYDANTIC_AI_NO_BANNER": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.fake_meta", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["FAKE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost"); os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)
    yield mode_file
    proc.terminate()


@pytest.fixture
def mode(fake):
    def set_mode(m: str):
        fake.write_text(m)
    yield set_mode
    fake.write_text("")


@pytest.fixture
def client(fake, tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    app = create_app(str(tmp_path / "wb.db"), data)
    with TestClient(app) as c:
        c.data_dir = data
        c.db_path = str(tmp_path / "wb.db")
        yield c


def project(c, kind="nyc"):
    return c.post("/projects", json={"kind": kind}).json()["project_id"]


def ask(c, pid, text):
    return c.post(f"/projects/{pid}/questions", json={"text": text}).json()


def ask_run(c, pid, text):
    q = ask(c, pid, text)
    return q, c.post(f"/questions/{q['question_id']}/run").json()


def events(c, pid):
    return c.get(f"/projects/{pid}/events").json()["events"]


def usage(c, pid):
    return c.get(f"/projects/{pid}/usage").json()["usage"]


# --- N01 faithful refinement --------------------------------------------------------------------
E13 = "Exclude withdrawn permits and don't use anything before 2021. Can I still study processing time?"


def test_N01_refinement_kept_and_checked(client):
    pid = project(client, "fx")
    q = ask(client, pid, E13)
    assert q["refinement_status"] == "ok" and "2021 onward" in q["working_text"] and "(status W)" in q["working_text"]


def test_N01_counterexample_inverted_refinement_is_flagged(client, mode):
    mode("bad_refine")
    pid = project(client, "fx")
    q = ask(client, pid, E13)
    assert q["refinement_status"] == "needs_review"
    assert q["working_text"] == E13                       # original kept as the working version
    assert any("kept years changed" in r for r in q["refinement_reasons"])


def test_N01_checker_paraphrase_counterexamples():
    from app.refine_check import check
    assert check("Exclude anything before 2021. Can I study it?", "using data before 2021, can I study it?")[0] == "mismatch"
    assert check("Don't use anything after 2021. Can I study it?", "using data from 2022 onward, can I study it?")[0] == "mismatch"
    assert check("Don't use anything after 2021. Can I study it?", "Can I study it using data through 2021?")[0] == "ok"


# --- N06 answer already in registered material ------------------------------------------------------
def test_N06_registered_answer_without_clarification(client):
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "What does est_cost measure?")
    assert r["result_kind"] == "EvidenceAssessment" and r["outcome"] == "supported"
    assert [e["evidence_id"] for e in r["evidence"]] == ["DD-2023:est_cost"] and r["evidence"][0]["location"]
    assert "clarification_asked" not in [e["kind"] for e in events(client, pid)]


# --- N07 "I don't know" enters investigation ----------------------------------------------------------
def test_N07_unknown_starts_investigation_not_a_guess(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    assert r["result_kind"] == "ClarificationNeeded" and len(r["clarification"]["branches"]) == 3
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["result_kind"] == "EvidenceAssessment"
    assert "DD-BIS-2019:Column Info:Job Start Date" in [e["evidence_id"] for e in a["evidence"]]
    stages = [u["stage"] for u in usage(client, pid)]
    assert stages[-2:] == ["plan", "assess"]
    assert "decision_committed" not in [e["kind"] for e in events(client, pid)]   # no decision was made for the user


# --- N08 preference question --------------------------------------------------------------------------
def test_N08_preference_offers_alternatives_and_never_chooses(client):
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "Which permit types should I study?")
    assert r["result_kind"] == "needs_user_choice" and len(r["alternatives"]) >= 2
    ids = [l["id"] for l in r["recent_references"]]
    assert "LIT-2019-D" not in ids and all(l["year"] >= 2024 for l in r["recent_references"])
    assert any("different city" in l["note"] for l in r["recent_references"])
    assert not {"plan", "assess"} & {u["stage"] for u in usage(client, pid)}      # nothing searched for the preference


# --- N09 response kinds stay distinct --------------------------------------------------------------------
def test_N09_response_kinds_are_distinct(client):
    pid = project(client, "fx")
    states = {}
    for kind in ("later", "decline", "cancel", "answer"):
        q, r = ask_run(client, pid, "Can I study time to final approval?")
        out = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": kind, "value": "yes" if kind == "answer" else None}).json()
        states[kind] = out["state"]
    assert states == {"later": "deferred_pending", "decline": "declined", "cancel": "cancelled", "answer": "decision_pending"}


# --- N10 / H11 evidence actually read ------------------------------------------------------------------------
def test_N10_every_cited_id_has_source_and_location(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert {e["evidence_id"] for e in a["evidence"]} == set(a["assessment"]["evidence_ids"])
    assert all(e["location"] and e["excerpt"] for e in a["evidence"])


def test_H11_counterexample_unread_evidence_id_is_rejected(client, mode):
    mode("hallucinate_id")
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "invalid_output" and a["evidence"] == []
    import sqlite3
    db = sqlite3.connect(client.db_path)
    assert db.execute("SELECT count(*) FROM evidence WHERE question_id=?", (q["question_id"],)).fetchone()[0] == 0


# --- N11 wrong release ---------------------------------------------------------------------------------------
def test_H11_one_time_unread_id_is_corrected_by_the_single_retry(client, mode):
    mode("hallucinate_once")
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "supported"
    assert "DD-BIS-2019:Column Info:Construction Start Date" not in a["assessment"]["evidence_ids"]
    assess = [u for u in usage(client, pid) if u["stage"] == "assess"][-1]
    assert assess["requests"] == 2                                   # the retry is counted, not hidden


def test_N11_other_release_not_used_as_support(client):
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "Can I study time to final approval?")
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "partial" and "OFF-NOTES-2025" not in a["assessment"]["evidence_ids"]


def test_N11_counterexample_harness_rejects_wrong_release_citation(client, mode):
    mode("cite_wrong_release")
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "Can I study time to final approval?")
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "invalid_output" and "OFF-NOTES-2025" in a["reason"]


def test_N11_counterexample_wrong_release_citation_rejected_for_nonsupporting_outcome(client, mode):
    mode("cite_wrong_release_conflicting")
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "Can I study time to final approval?")
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "invalid_output" and "OFF-NOTES-2025" in a["reason"]
    assert a["evidence"] == []
    import sqlite3
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT count(*) FROM evidence WHERE question_id=?", (q["question_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM assessments WHERE question_id=?", (q["question_id"],)).fetchone()[0] == 0


# --- N12 / N13 distinct outcomes and scope ---------------------------------------------------------------------
def test_N12_outcomes_remain_distinct(client):
    pid = project(client, "fx")
    _, insufficient = ask_run(client, pid, "Does district follow 2020 census boundaries?")
    _, r = ask_run(client, pid, "Can I study time to final approval?")
    partial = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    _, supported = ask_run(client, pid, "What does est_cost measure?")
    assert (insufficient["outcome"], partial["outcome"], supported["outcome"]) == ("insufficient", "partial", "supported")


def test_N13_out_of_scope_source_is_not_called(client, mode):
    mode("out_of_scope")
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["out_of_scope_not_called"] == ["SOCRATA-LIVE-API"]


def test_N13_use_source_outside_allowlist_blocks_and_keeps_question_open(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "use_source", "value": "https://example.org/x"}).json()
    assert a["state"] == "blocked_pending_authorization" and a["cq_status"] == "pending"


# --- N17 / N18 decisions ------------------------------------------------------------------------------------------
def test_N17_confirmed_reason_is_preserved_and_linked(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"})
    reason = "Job Start Date is the first permit issuance date"
    d = client.post(f"/projects/{pid}/decisions", json={"question_id": q["question_id"], "verdict": "defer", "reason": reason}).json()
    assert d["reason"] == reason
    b = client.get(f"/projects/{pid}/briefing").json()
    assert reason in b["parts"]["where_you_left_off"][0]["text"]


def test_N18_missing_reason_reads_reason_not_recorded(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    d = client.post(f"/projects/{pid}/decisions", json={"question_id": q["question_id"], "verdict": "defer"}).json()
    assert d["reason"] is None and d["reason_display"] == "Reason not recorded"
    assert "Reason not recorded" in client.get(f"/projects/{pid}/briefing").json()["parts"]["where_you_left_off"][0]["text"]


def test_decision_idempotent_on_client_action_id(client):
    pid = project(client)
    q, _ = ask_run(client, pid, DEMO)
    body = {"question_id": q["question_id"], "verdict": "defer", "reason": "x", "client_action_id": "act-1"}
    a = client.post(f"/projects/{pid}/decisions", json=body).json(); b = client.post(f"/projects/{pid}/decisions", json=body).json()
    assert a["decision_id"] == b["decision_id"]


# --- N20 / N33 briefing ----------------------------------------------------------------------------------------------
def test_N33_briefing_five_parts_read_only(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    before_e, before_u = len(events(client, pid)), len(usage(client, pid))
    b = client.get(f"/projects/{pid}/briefing").json()
    assert set(b["parts"]) == {"where_you_left_off", "what_changed_since", "ready_now", "waiting_on_you", "suggested_next_action"}
    assert len(b["top_items"]) <= 3 and b["model_calls"] == 0 and b["verified_at"]
    assert any(i["saved_at"] for i in b["parts"]["waiting_on_you"])
    assert (len(events(client, pid)), len(usage(client, pid))) == (before_e, before_u)   # reading writes nothing


def test_N20_restart_briefing_is_deterministic_and_model_free(client, tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    client.post(f"/projects/{pid}/decisions", json={"question_id": q["question_id"], "verdict": "defer", "reason": "restart check"})
    first = client.get(f"/projects/{pid}/briefing").json()
    with TestClient(create_app(client.db_path, client.data_dir)) as again:      # new app instance, same database
        second = again.get(f"/projects/{pid}/briefing").json()
        assert second["parts"]["where_you_left_off"] == first["parts"]["where_you_left_off"]
        assert len(again.get(f"/projects/{pid}/usage").json()["usage"]) == len(usage(client, pid))


def test_briefing_reports_changed_input(client):
    pid = project(client, "fx")
    (client.data_dir / "fx" / "permits_2023.csv").write_text("permit_id\nP999\n")
    b = client.get(f"/projects/{pid}/briefing").json()
    assert "changed" in b["parts"]["what_changed_since"][0]["text"]


# --- N22 / E17 / cancel: stale responses ---------------------------------------------------------------------------------
def test_N22_answered_or_cross_project_response_rejected(client):
    pid = project(client)
    other = project(client, "fx")
    q, r = ask_run(client, pid, DEMO)
    assert client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "answer", "value": "no", "project_id": other}).status_code == 409
    client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "answer", "value": "no"})
    assert client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "answer", "value": "no"}).status_code == 409


def test_E17_input_version_change_rejects_late_answer(client):
    pid = project(client, "fx")
    q, r = ask_run(client, pid, "Can I study time to final approval?")
    with open(client.data_dir / "fx" / "permits_2023.csv", "a") as f:
        f.write("P021,2023-01-10,2023-02-10,A,2023-03-01,residential,15250,D1\n")
    resp = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"})
    assert resp.status_code == 409 and "input version changed" in resp.json()["message"]


def test_cancel_closes_pending_clarification(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    assert client.post(f"/tasks/{r['task_id']}/cancel").json()["clarifications_closed"] == 1
    assert client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).status_code == 409


# --- N24 / N25 deletion ------------------------------------------------------------------------------------------------------
def test_N24_deletion_leaves_no_text_anywhere(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"})
    client.post(f"/projects/{pid}/decisions", json={"question_id": q["question_id"], "verdict": "defer",
                                                    "reason": "PRIVATE REASON ZZZ", "client_action_id": "act-z"})
    client.post(f"/questions/{q['question_id']}/shadow")
    out = client.delete(f"/questions/{q['question_id']}").json()
    assert out["verified"] and out["remaining_hits"] == []
    import sqlite3
    db = sqlite3.connect(client.db_path)
    dump = "\n".join(db.iterdump())
    assert "PRIVATE REASON ZZZ" not in dump and DEMO not in dump        # independent check over the whole database dump


def test_N25_delete_during_pending_clarification_blocks_late_answer(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    client.delete(f"/questions/{q['question_id']}")
    assert client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).status_code == 409
    assert DEMO not in json.dumps(client.get(f"/projects/{pid}/briefing").json())


# --- N45 / N31 / H21 shadow, comparison, usage --------------------------------------------------------------------------------
def test_N45_shadow_is_separate_from_evidence_and_briefing(client):
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    s = client.post(f"/questions/{q['question_id']}/shadow").json()
    assert s["adapter"] == "mock" and s["flag_origin"] == "deterministic checks"
    assert s["basic_answer"] not in json.dumps(client.get(f"/projects/{pid}/briefing").json())
    import sqlite3
    db = sqlite3.connect(client.db_path)
    assert db.execute("SELECT count(*) FROM evidence WHERE question_id=?", (q["question_id"],)).fetchone()[0] == 0
    assert usage(client, pid)[-1]["stage"] == "shadow_basic"


def test_H21_cost_is_null_with_reason_never_zero(client):
    pid = project(client)
    ask_run(client, pid, DEMO)
    for u in usage(client, pid):
        assert u["cost_usd"] is None and "null, not zero" in u["cost_note"] and u["input_tokens"] > 0


def test_N31_comparison_runner_records_both_arms(fake, tmp_path):
    import asyncio
    from app.compare import run_all
    out = asyncio.run(run_all(str(tmp_path / "cmp.db")))
    assert out["total"] == 8 and all(r["basic_requests"] == 1 and r["cost_usd"] is None for r in out["cases"])
    stages_expected = {"E07": 2, "E13": 1, "E05": 3}     # preference: refine+first_step; refine only; insufficient stops before assess
    for r in out["cases"]:
        if r["case"] in stages_expected:
            assert r["harness_requests"] == stages_expected[r["case"]], r
    assert out["harness_matches"] == out["total"], [r for r in out["cases"] if not r["match"]]


# --- session-only memory (hackathon definition, lite §7) ------------------------------------------------
SECRET = "Session-only question about final approval QQ17?"


def test_session_only_is_kept_out_of_search_and_briefing(client):
    pid = project(client, "fx")
    q = client.post(f"/projects/{pid}/questions", json={"text": SECRET, "memory": "session_only"}).json()
    client.post(f"/questions/{q['question_id']}/run")
    assert SECRET not in json.dumps(client.get(f"/projects/{pid}/briefing").json())
    import sqlite3
    db = sqlite3.connect(client.db_path)
    assert db.execute("SELECT count(*) FROM questions_fts WHERE questions_fts MATCH 'QQ17'").fetchone()[0] == 0
    assert SECRET not in json.dumps(events(client, pid))


def test_session_end_deletes_session_only_questions(client):
    pid = project(client, "fx")
    keep = ask(client, pid, "What does est_cost measure?")
    gone = client.post(f"/projects/{pid}/questions", json={"text": SECRET, "memory": "session_only"}).json()
    out = client.post("/session/end").json()
    assert out["deleted"] == 1 and out["all_verified"]
    import sqlite3
    dump = "\n".join(sqlite3.connect(client.db_path).iterdump())
    assert SECRET not in dump and keep["original_text"] in dump


def test_unclean_exit_cleanup_on_next_start(client):
    from fastapi.testclient import TestClient
    from app.api import create_app
    pid = project(client, "fx")
    client.post(f"/projects/{pid}/questions", json={"text": SECRET, "memory": "session_only"})
    with TestClient(create_app(client.db_path, client.data_dir)):       # simulated restart without /session/end
        pass
    import sqlite3
    assert SECRET not in "\n".join(sqlite3.connect(client.db_path).iterdump())


def test_schema_rejects_confirmed_precedent(client, mode):
    mode("confirm_precedent")
    pid = project(client)
    q, r = ask_run(client, pid, DEMO)
    a = client.post(f"/clarifications/{r['cq_id']}/responses", json={"kind": "unknown"}).json()
    assert a["outcome"] == "invalid_output"
