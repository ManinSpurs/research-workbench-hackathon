"""Citation Composer backend (design §13, "Citation Composer" row).

The composer records the researcher's selected sentence, the registered evidence that
supports or limits it, the proposed replacement, and an explicit approve/reject decision
under a revision check. It only PROPOSES: nothing here rewrites a draft. Evidence must be
already registered (an `evidence` row for this project); an unresolvable ID is refused and
nothing is stored. Local only: no model call, no socket, no process.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient

from app import citations, store
from app.api import create_app
from app.harness import Conflict
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)

SENTENCE = "The project's estimated cost rose in 2023."
PROPOSED = ("The project's estimated cost rose in 2023 \\citep{doe2023} "
            "(\\textbf{applicant-reported}, $+12.5\\%$; see Eq.~\\ref{eq:cost}).")
REGISTERED = "DD-2023:est_cost"
COUNT = "SELECT count(*) c FROM citation_proposals"


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


def seed(h, text="What does est_cost measure?"):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    return pid, qid


def register_evidence(h, pid, evidence_id=REGISTERED, qid=None):
    """The `evidence` store is the registration record (written when a source is read)."""
    h.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",
                 (store.new_id("ev"), qid or "q-registration", pid, evidence_id, "DD-2023",
                  "Applicant-reported estimated cost in USD.", "DD-2023 \u203a est_cost", store.now()))
    h.db.commit()
    return evidence_id


def create(h, pid, **over):
    body = {"selected_sentence": SENTENCE, "evidence_ids": [REGISTERED], "proposed_text": PROPOSED}
    body.update(over)
    return citations.create_citation(h, pid, body)


def dump(h):
    return "\n".join(h.db.iterdump())


# ------------------------------------------------------------------ schema
def test_schema_is_one_new_additive_table(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    create(h, pid)
    assert citations.SCHEMA.count("CREATE TABLE") == 1
    cols = {c["name"] for c in h.db.execute("PRAGMA table_info('citation_proposals')")}
    assert cols == {"citation_id", "project_id", "question_id", "draft_id", "selected_sentence",
                    "evidence_ids_json", "support", "limitations", "proposed_text", "status",
                    "decision_reason", "decided_at", "created_at", "updated_at", "revision"}
    # Existing tables are untouched: a fresh connection needs no ALTER.
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(path)
    try:
        assert {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= \
            {"citation_proposals", "questions", "latex_drafts"}
    finally:
        other.close()


def test_listing_an_empty_project_is_a_pure_read(h, client):
    pid, _ = seed(h)
    citations.list_citations(h, pid)  # first use creates the one new table
    before = dump(h)
    assert client.get(f"/projects/{pid}/citations").json() == \
        {"project_id": pid, "citations": [], "model_calls": 0}
    assert h.db.execute(COUNT).fetchone()["c"] == 0
    assert dump(h) == before


# ------------------------------------------------------------------ create
def test_create_persists_submitted_text_verbatim_and_starts_proposed(h):
    pid, qid = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid, question_id=qid, support="The dictionary defines the field.",
                 limitations="Self-reported; not audited.")
    assert rec["status"] == "proposed" and rec["revision"] == 1
    assert rec["selected_sentence"] == SENTENCE
    assert rec["proposed_text"] == PROPOSED, "TeX must survive byte-for-byte"
    assert "\\citep{doe2023}" in rec["proposed_text"] and "$+12.5\\%$" in rec["proposed_text"]
    assert rec["evidence_ids"] == [REGISTERED]
    assert rec["support"] == "The dictionary defines the field."
    assert rec["limitations"] == "Self-reported; not audited."
    assert rec["decision_reason"] is None and rec["decided_at"] is None
    assert rec["question_id"] == qid and rec["question_missing"] is False
    assert rec["draft_id"] is None and rec["model_calls"] == 0
    assert rec["created_at"] and rec["created_at"] == rec["updated_at"]
    stored = h.db.execute("SELECT selected_sentence, proposed_text, status, revision, support, limitations"
                          " FROM citation_proposals WHERE citation_id=?", (rec["citation_id"],)).fetchone()
    assert tuple(stored) == (SENTENCE, PROPOSED, "proposed", 1, "The dictionary defines the field.",
                             "Self-reported; not audited.")
    assert citations.read_citation(h, rec["citation_id"]) == rec


def test_create_registers_evidence_detail_from_the_project_store(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    assert rec["evidence"] == [{"evidence_id": REGISTERED, "registered": True, "source_id": "DD-2023",
                                "excerpt": "Applicant-reported estimated cost in USD.",
                                "location": "DD-2023 \u203a est_cost"}]


def test_create_with_unresolvable_evidence_is_refused_and_stores_nothing(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    citations.ensure_schema(h.db)  # the one new table is created on first use, not by a refusal
    before = dump(h)
    with pytest.raises(citations.Invalid, match="not registered in this project: DD-2023:invented"):
        create(h, pid, evidence_ids=[REGISTERED, "DD-2023:invented"])
    assert dump(h) == before, "a refused create must write nothing"
    assert h.db.execute(COUNT).fetchone()["c"] == 0


def test_evidence_registered_in_another_project_does_not_resolve(h):
    pid, _ = seed(h)
    other = h.register_project("fx")["project_id"]
    register_evidence(h, other)
    with pytest.raises(citations.Invalid):
        create(h, pid)
    assert h.db.execute(COUNT).fetchone()["c"] == 0


@pytest.mark.parametrize("field, value, message", [
    ("selected_sentence", "   ", "selected_sentence must contain the researcher's own text"),
    ("proposed_text", "", "proposed_text must contain the researcher's own text"),
    ("proposed_text", None, "proposed_text is required"),
    ("evidence_ids", [], "evidence_ids must be a nonempty list of registered evidence IDs"),
    ("evidence_ids", None, "evidence_ids is required"),
    ("evidence_ids", ["  "], "evidence_ids must contain only nonempty evidence ID strings"),
])
def test_create_rejects_named_field_before_writing(h, field, value, message):
    pid, _ = seed(h)
    register_evidence(h, pid)
    with pytest.raises(citations.Invalid, match=message):
        create(h, pid, **{field: value})
    assert h.db.execute(COUNT).fetchone()["c"] == 0


def test_create_rejects_unknown_and_other_project_question(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    with pytest.raises(KeyError):
        create(h, pid, question_id="q-missing")
    other_pid, other_qid = seed(h, "A different project's question")
    with pytest.raises(Conflict, match="belongs to another project"):
        create(h, pid, question_id=other_qid)
    assert other_pid != pid
    assert h.db.execute(COUNT).fetchone()["c"] == 0


# ------------------------------------------------------------------ decide
def test_approve_requires_matching_revision_and_a_stale_one_changes_nothing(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    approved = citations.decide_citation(h, rec["citation_id"],
                                        {"decision": "approve", "reason": "Matches the dictionary.",
                                         "expected_revision": 1})
    assert approved["status"] == "approved" and approved["revision"] == 2
    assert approved["decision_reason"] == "Matches the dictionary." and approved["decided_at"]
    # A stale screen (still at revision 1) must not overwrite the newer decision.
    before = dump(h)
    with pytest.raises(Conflict, match="at revision 2"):
        citations.decide_citation(h, rec["citation_id"],
                                  {"decision": "reject", "reason": "Changed my mind", "expected_revision": 1})
    assert dump(h) == before
    assert citations.read_citation(h, rec["citation_id"]) == approved


def test_reject_without_reason_is_refused(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    before = dump(h)
    with pytest.raises(citations.Invalid, match="reason is required when rejecting"):
        citations.decide_citation(h, rec["citation_id"], {"decision": "reject", "expected_revision": 1})
    with pytest.raises(citations.Invalid, match="reason is required when rejecting"):
        citations.decide_citation(h, rec["citation_id"],
                                  {"decision": "reject", "reason": "   ", "expected_revision": 1})
    assert dump(h) == before
    assert citations.read_citation(h, rec["citation_id"])["status"] == "proposed"


def test_reject_with_reason_records_it(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    rejected = citations.decide_citation(h, rec["citation_id"],
                                        {"decision": "reject", "reason": "The citation key is not in the bib.",
                                         "expected_revision": 1})
    assert rejected["status"] == "rejected" and rejected["revision"] == 2
    assert rejected["decision_reason"] == "The citation key is not in the bib."


@pytest.mark.parametrize("body, message", [
    ({"decision": "maybe", "expected_revision": 1}, "decision must be approve or reject"),
    ({"decision": "approve"}, "expected_revision must be the integer revision"),
    ({"decision": "approve", "expected_revision": "1"}, "expected_revision must be the integer revision"),
    ({"decision": "approve", "expected_revision": True}, "expected_revision must be the integer revision"),
])
def test_decide_rejects_bad_bodies(h, body, message):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    with pytest.raises(citations.Invalid, match=message):
        citations.decide_citation(h, rec["citation_id"], body)
    assert citations.read_citation(h, rec["citation_id"])["revision"] == 1


def test_decide_never_applies_the_proposal_to_a_draft(h):
    pid, _ = seed(h)
    register_evidence(h, pid)
    draft = h.db.execute("SELECT count(*) c FROM latex_drafts").fetchone()["c"]
    rec = create(h, pid)
    citations.decide_citation(h, rec["citation_id"], {"decision": "approve", "expected_revision": 1})
    assert h.db.execute("SELECT count(*) c FROM latex_drafts").fetchone()["c"] == draft
    assert h.db.execute("SELECT count(*) c FROM latex_draft_versions").fetchone()["c"] == 0
    assert citations.read_citation(h, rec["citation_id"])["proposed_text"] == PROPOSED


# ------------------------------------------------------------------ list / delete / 404
def test_listing_returns_only_its_own_project(h):
    pid, _ = seed(h)
    other = h.register_project("fx")["project_id"]
    register_evidence(h, pid)
    register_evidence(h, other)
    mine = create(h, pid, selected_sentence="Only mine.")
    theirs = create(h, other, selected_sentence="Only theirs.")
    listed = citations.list_citations(h, pid)
    assert [c["citation_id"] for c in listed["citations"]] == [mine["citation_id"]]
    assert listed["project_id"] == pid and listed["model_calls"] == 0
    assert [c["citation_id"] for c in citations.list_citations(h, other)["citations"]] == [theirs["citation_id"]]


def test_delete_removes_it_from_every_read(h, client):
    pid, _ = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid)
    assert client.delete(f"/citations/{rec['citation_id']}").json() == \
        {"deleted": True, "citation_id": rec["citation_id"], "model_calls": 0}
    assert h.db.execute(COUNT).fetchone()["c"] == 0
    assert client.get(f"/citations/{rec['citation_id']}").status_code == 404
    assert client.get(f"/projects/{pid}/citations").json()["citations"] == []
    with pytest.raises(KeyError):
        citations.read_citation(h, rec["citation_id"])
    assert client.delete(f"/citations/{rec['citation_id']}").status_code == 404


def test_unknown_project_and_citation_are_404(client):
    assert client.get("/projects/proj-missing/citations").status_code == 404
    assert client.get("/projects/proj-missing/citations").json() == \
        {"error": "not_found", "message": "project not found", "details": {}}
    assert client.post("/projects/proj-missing/citations",
                       json={"selected_sentence": "x", "evidence_ids": ["e"], "proposed_text": "y"}).status_code == 404
    assert client.get("/citations/citation-missing").status_code == 404
    assert client.get("/citations/citation-missing").json() == \
        {"error": "not_found", "message": "citation not found", "details": {}}
    assert client.post("/citations/citation-missing/decide",
                       json={"decision": "approve", "expected_revision": 1}).status_code == 404
    assert client.delete("/citations/citation-missing").status_code == 404


# ------------------------------------------------------------------ routes
def test_route_roundtrip(h, client):
    pid, qid = seed(h)
    register_evidence(h, pid)
    created = client.post(f"/projects/{pid}/citations",
                          json={"selected_sentence": SENTENCE, "evidence_ids": [REGISTERED],
                                "support": "Dictionary", "limitations": None,
                                "proposed_text": PROPOSED, "question_id": qid})
    assert created.status_code == 200
    rec = created.json()
    assert rec["status"] == "proposed" and rec["proposed_text"] == PROPOSED
    assert client.get(f"/citations/{rec['citation_id']}").json() == rec
    assert client.get(f"/projects/{pid}/citations").json()["citations"] == [rec]
    decided = client.post(f"/citations/{rec['citation_id']}/decide",
                          json={"decision": "approve", "reason": "Fine.", "expected_revision": 1})
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    stale = client.post(f"/citations/{rec['citation_id']}/decide",
                        json={"decision": "reject", "reason": "no", "expected_revision": 1})
    assert stale.status_code == 409
    assert stale.json()["error"] == "conflict" and "revision 2" in stale.json()["message"]
    assert client.get(f"/citations/{rec['citation_id']}").json() == decided.json()


def test_route_bad_request_is_400_with_a_readable_sentence(h, client):
    pid, _ = seed(h)
    register_evidence(h, pid)
    bad = client.post(f"/projects/{pid}/citations",
                      json={"selected_sentence": "", "evidence_ids": [REGISTERED], "proposed_text": PROPOSED})
    assert bad.status_code == 400
    assert bad.json() == {"error": "invalid_request",
                          "message": "selected_sentence must contain the researcher's own text",
                          "details": {}}
    unregistered = client.post(f"/projects/{pid}/citations",
                              json={"selected_sentence": SENTENCE, "evidence_ids": ["invented"],
                                    "proposed_text": PROPOSED})
    assert unregistered.status_code == 400
    assert unregistered.json()["message"] == "evidence_ids is not registered in this project: invented"
    assert h.db.execute(COUNT).fetchone()["c"] == 0


def test_unknown_body_fields_are_refused(h, client):
    pid, _ = seed(h)
    register_evidence(h, pid)
    r = client.post(f"/projects/{pid}/citations",
                    json={"selected_sentence": SENTENCE, "evidence_ids": [REGISTERED],
                          "proposed_text": PROPOSED, "confidence": 0.9})
    assert r.status_code == 400
    assert "confidence" in r.json()["message"]


def test_question_deleted_after_the_fact_is_reported_not_failed(h):
    pid, qid = seed(h)
    register_evidence(h, pid)
    rec = create(h, pid, question_id=qid)
    h.db.execute("DELETE FROM questions WHERE id=?", (qid,))
    h.db.commit()
    read = citations.read_citation(h, rec["citation_id"])
    assert read["question_missing"] is True and read["question_id"] == qid
    assert read["proposed_text"] == PROPOSED


def test_module_is_local_and_model_free():
    source = Path(citations.__file__).read_text()
    for banned in ("requests", "httpx", "urllib", "socket", "subprocess", "openai", "pydantic_ai"):
        assert banned not in source
    assert "confidence" not in source and "score" not in source
