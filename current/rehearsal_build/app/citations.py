"""Citation Composer (design §13 "Citation Composer" row): a proposal the researcher decides.

Selected sentence -> registered evidence -> support/limitations -> proposed replacement ->
explicit approve or reject under a revision check. This module only records the researcher's
own words: it never edits a draft, never calls a model, never touches the network, and never
invents an evidence ID, timestamp or reason. Applying an approved proposal to the manuscript
is a separate, later step and is deliberately absent here.

Evidence references must already be registered for the project (rows in the `evidence` store,
written only when a registered source was actually read and assessed). An ID that does not
resolve is refused at creation, so a stored citation never points at evidence the project
does not have. A citation is project-owned, like a LaTeX draft: deleting the linked question
keeps the citation and its payload reports `question_missing` instead of failing.
"""
from __future__ import annotations

import json

from . import store
from .harness import Conflict

STATUSES = ("proposed", "approved", "rejected")
DECISIONS = {"approve": "approved", "reject": "rejected"}
MAX_TEXT = 20000
CREATE_FIELDS = {"selected_sentence", "evidence_ids", "support", "limitations",
                 "proposed_text", "question_id", "draft_id"}
DECIDE_FIELDS = {"decision", "reason", "expected_revision"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS citation_proposals(
  citation_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  question_id TEXT,
  draft_id TEXT,
  selected_sentence TEXT NOT NULL,
  evidence_ids_json TEXT NOT NULL,
  support TEXT,
  limitations TEXT,
  proposed_text TEXT NOT NULL,
  status TEXT NOT NULL,
  decision_reason TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revision INTEGER NOT NULL);
"""


class Invalid(Exception):
    """A request that must be rejected with HTTP 400 and a readable sentence."""


def ensure_schema(db) -> None:
    """Create this module's one table the first time it is needed.

    The store's SCHEMA is fixed elsewhere, so this slice adds its table from its own
    module (the same way Harness adds the ai_* tables). The probe is a plain read, so a
    read-only route stays read-only once the table exists.
    """
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='citation_proposals'").fetchone() is None:
        db.execute(SCHEMA)
        db.commit()


# ------------------------------------------------------------------ validation helpers
def _body(payload) -> dict:
    if not isinstance(payload, dict):
        raise Invalid("The request body must be a JSON object")
    return payload


def _no_extra_fields(body: dict, allowed: set, action: str) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        raise Invalid(f"{action} does not accept the field(s): {', '.join(extra)}")


def _text(body: dict, field: str, required: bool) -> str | None:
    """Returns the value exactly as submitted (TeX commands, braces and spacing included)."""
    value = body.get(field)
    if value is None:
        if required:
            raise Invalid(f"{field} is required and must contain the researcher's own text")
        return None
    if not isinstance(value, str):
        raise Invalid(f"{field} must be text")
    if len(value) > MAX_TEXT:
        raise Invalid(f"{field} must be at most {MAX_TEXT} characters")
    if required and not value.strip():
        raise Invalid(f"{field} must contain the researcher's own text")
    return value


def _evidence_ids(body: dict) -> list:
    value = body.get("evidence_ids")
    if value is None:
        raise Invalid("evidence_ids is required: attach at least one registered evidence ID")
    if not isinstance(value, list) or not value:
        raise Invalid("evidence_ids must be a nonempty list of registered evidence IDs")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise Invalid("evidence_ids must contain only nonempty evidence ID strings")
    return list(value)


def _registered(db, pid: str, evidence_id: str):
    """The project's registered evidence row for this ID, or None."""
    return db.execute("SELECT evidence_id, source_id, excerpt, location FROM evidence"
                      " WHERE project_id=? AND evidence_id=? ORDER BY rowid LIMIT 1",
                      (pid, evidence_id)).fetchone()


# ------------------------------------------------------------------ payload
def _payload(h, row) -> dict:
    evidence = []
    for evidence_id in json.loads(row["evidence_ids_json"]):
        detail = _registered(h.db, row["project_id"], evidence_id)
        evidence.append({"evidence_id": evidence_id, "registered": detail is not None,
                         "source_id": detail["source_id"] if detail is not None else None,
                         "excerpt": detail["excerpt"] if detail is not None else None,
                         "location": detail["location"] if detail is not None else None})
    # Optional metadata: if the linked question was deleted, say so rather than fail.
    question_missing = False
    if row["question_id"] is not None:
        question_missing = h.db.execute("SELECT 1 FROM questions WHERE id=?",
                                        (row["question_id"],)).fetchone() is None
    return {"citation_id": row["citation_id"], "project_id": row["project_id"],
            "question_id": row["question_id"], "question_missing": question_missing,
            "draft_id": row["draft_id"],
            "selected_sentence": row["selected_sentence"], "evidence_ids": json.loads(row["evidence_ids_json"]),
            "evidence": evidence, "support": row["support"], "limitations": row["limitations"],
            "proposed_text": row["proposed_text"], "status": row["status"],
            "decision_reason": row["decision_reason"], "decided_at": row["decided_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "revision": row["revision"], "model_calls": 0}


def _row(h, cid: str):
    ensure_schema(h.db)
    row = h.db.execute("SELECT * FROM citation_proposals WHERE citation_id=?", (cid,)).fetchone()
    if row is None:
        raise KeyError("citation not found")
    return row


# ------------------------------------------------------------------ read
def read_citation(h, cid: str) -> dict:
    return _payload(h, _row(h, cid))


def list_citations(h, pid: str) -> dict:
    h.project(pid)  # KeyError -> 404 for unknown projects
    ensure_schema(h.db)
    rows = h.db.execute("SELECT * FROM citation_proposals WHERE project_id=?"
                        " ORDER BY created_at, citation_id", (pid,)).fetchall()
    return {"project_id": pid, "citations": [_payload(h, row) for row in rows], "model_calls": 0}


# ------------------------------------------------------------------ create
def create_citation(h, pid: str, payload) -> dict:
    h.project(pid)  # KeyError -> 404 for unknown projects
    ensure_schema(h.db)
    body = _body(payload)
    _no_extra_fields(body, CREATE_FIELDS, "Creating a citation")
    selected_sentence = _text(body, "selected_sentence", required=True)
    proposed_text = _text(body, "proposed_text", required=True)
    support = _text(body, "support", required=False)
    limitations = _text(body, "limitations", required=False)
    evidence_ids = _evidence_ids(body)
    # Every ID must already be registered in this project; a citation never stores a dangling ref.
    unresolved = [eid for eid in evidence_ids if _registered(h.db, pid, eid) is None]
    if unresolved:
        raise Invalid("evidence_ids is not registered in this project: " + ", ".join(unresolved))
    question_id = body.get("question_id")
    if question_id is not None:
        if not isinstance(question_id, str) or not question_id.strip():
            raise Invalid("question_id must be nonempty text or null")
        question = h.question(question_id)  # KeyError -> 404
        if question["project_id"] != pid:
            raise Conflict("The question belongs to another project")
    draft_id = body.get("draft_id")
    if draft_id is not None:
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise Invalid("draft_id must be nonempty text or null")
        draft = h.db.execute("SELECT project_id FROM latex_drafts WHERE id=?", (draft_id,)).fetchone()
        if draft is None:
            raise KeyError("draft not found")
        if draft["project_id"] != pid:
            raise Conflict("The draft belongs to another project")
    now = store.now()
    cid = store.new_id("citation")
    h.db.execute("INSERT INTO citation_proposals(citation_id, project_id, question_id, draft_id,"
                 " selected_sentence, evidence_ids_json, support, limitations, proposed_text,"
                 " status, decision_reason, decided_at, created_at, updated_at, revision)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, pid, question_id, draft_id, selected_sentence,
                  json.dumps(evidence_ids, ensure_ascii=False), support, limitations, proposed_text,
                  "proposed", None, None, now, now, 1))
    h.db.commit()
    return read_citation(h, cid)


# ------------------------------------------------------------------ decide
def decide_citation(h, cid: str, payload) -> dict:
    row = _row(h, cid)
    body = _body(payload)
    _no_extra_fields(body, DECIDE_FIELDS, "Deciding a citation")
    decision = body.get("decision")
    if decision not in DECISIONS:
        raise Invalid("decision must be approve or reject")
    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        raise Invalid("expected_revision must be the integer revision the screen showed")
    reason = _text(body, "reason", required=False)
    if decision == "reject" and (reason is None or not reason.strip()):
        raise Invalid("reason is required when rejecting a citation proposal")
    if row["revision"] != expected_revision:
        raise Conflict(f"This citation is at revision {row['revision']}; "
                       f"reload before deciding so a newer decision is not overwritten")
    now = store.now()
    h.db.execute("UPDATE citation_proposals SET status=?, decision_reason=?, decided_at=?,"
                 " updated_at=?, revision=? WHERE citation_id=?",
                 (DECISIONS[decision], reason, now, now, row["revision"] + 1, cid))
    h.db.commit()
    return read_citation(h, cid)


# ------------------------------------------------------------------ delete
def delete_citation(h, cid: str) -> dict:
    _row(h, cid)
    h.db.execute("DELETE FROM citation_proposals WHERE citation_id=?", (cid,))
    h.db.commit()
    return {"deleted": True, "citation_id": cid, "model_calls": 0}
