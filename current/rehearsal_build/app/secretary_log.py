"""Secretary review log: the past secretary exchanges, gathered in one place.

The product owner's request: the home menu offers a review list of past secretary
exchanges, where the person keeps what matters and deletes the rest.

Secretary chat reads a bounded window of non-discarded project turns as context.
The recorded text is immutable — the only
mutable field is `status` (`kept` / `discarded`) — because a log you can rewrite is
not a record of what the secretary actually said.

Deletion is real deletion of exactly the rows asked for: `delete_turn` removes one
row by its ID, and `clear_turns` removes only the turns whose status was chosen.
"""
from __future__ import annotations

import json

from . import store
from .harness import Conflict

ROLES = ("researcher", "secretary")
STATUSES = ("recorded", "kept", "discarded")
UPDATE_STATUSES = ("kept", "discarded")
CLEARABLE_STATUSES = ("discarded",)
MAX_BODY_CHARS = 200_000
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
RECORD_FIELDS = {"role", "body", "pack_id", "question_id"}
UPDATE_FIELDS = {"status"}


class Invalid(Exception):
    """A request that must be rejected with HTTP 400 and a readable sentence."""


# ------------------------------------------------------------------ validation helpers
def _body(payload) -> dict:
    if not isinstance(payload, dict):
        raise Invalid("The request body must be a JSON object")
    return payload


def _no_extra_fields(body: dict, allowed: set, action: str) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        raise Invalid(f"{action} does not accept the field(s): {', '.join(extra)}")


def _strings(value) -> list:
    """Every string reachable one level into the recorded body."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _recorded_body(body: dict) -> dict:
    """The body as submitted, refused when it carries no text.

    A secretary exchange is text: a body that is null, a bare string, or a container
    of nothing but empty strings is not a turn worth keeping in a review list.
    """
    value = body.get("body")
    if value is None:
        raise Invalid("body is required: a turn must carry the recorded text")
    if not isinstance(value, dict):
        raise Invalid("body must be a JSON object that carries the recorded text")
    if not any(s.strip() for s in _strings(value)):
        raise Invalid("body must carry the recorded text")
    if len(json.dumps(value, ensure_ascii=False)) > MAX_BODY_CHARS:
        raise Invalid(f"body must be at most {MAX_BODY_CHARS} characters")
    return value


def _optional_id(body: dict, field: str) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{field} must be nonempty text or null")
    return value


def _limit(value) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Invalid("limit must be a positive integer")
    return min(value, MAX_LIMIT)


# ------------------------------------------------------------------ payload
def _payload(row) -> dict:
    return {"id": row["id"], "project_id": row["project_id"], "pack_id": row["pack_id"],
            "question_id": row["question_id"], "role": row["role"],
            "body": json.loads(row["body_json"]), "status": row["status"],
            "created_at": row["created_at"], "model_calls": 0}


def _row(h, tid: str):
    row = h.db.execute("SELECT * FROM secretary_turns WHERE id=?", (tid,)).fetchone()
    if row is None:
        raise KeyError("secretary turn not found")
    return row


# ------------------------------------------------------------------ record
def record_turn(h, project_id: str, payload) -> dict:
    h.project(project_id)  # KeyError -> 404 for unknown projects
    body = _body(payload)
    _no_extra_fields(body, RECORD_FIELDS, "Recording a secretary turn")
    role = body.get("role")
    if role not in ROLES:
        raise Invalid("role must be researcher or secretary")
    recorded = _recorded_body(body)
    pack_id = _optional_id(body, "pack_id")
    question_id = _optional_id(body, "question_id")
    if question_id is not None:
        question = h.question(question_id)  # KeyError -> 404
        if question["project_id"] != project_id:
            raise Conflict("The question belongs to another project")
    tid = store.new_id("turn")
    h.db.execute("INSERT INTO secretary_turns(id, project_id, pack_id, question_id, role, body_json,"
                 " status, created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (tid, project_id, pack_id, question_id, role,
                  json.dumps(recorded, ensure_ascii=False), "recorded", store.now()))
    h.db.commit()
    return _payload(_row(h, tid))


# ------------------------------------------------------------------ read
def list_turns(h, project_id: str, pack_id: str | None = None, status: str | None = None,
               limit: int | None = None) -> dict:
    h.project(project_id)  # KeyError -> 404 for unknown projects
    if status is not None and status not in STATUSES:
        raise Invalid("status must be recorded, kept or discarded")
    clauses = ["project_id=?"]
    args: list = [project_id]
    if pack_id is not None:
        clauses.append("pack_id=?")
        args.append(pack_id)
    if status is not None:
        clauses.append("status=?")
        args.append(status)
    args.append(_limit(limit))
    rows = h.db.execute("SELECT * FROM secretary_turns WHERE " + " AND ".join(clauses) +
                        " ORDER BY created_at DESC, rowid DESC LIMIT ?", args).fetchall()
    return {"project_id": project_id, "turns": [_payload(row) for row in rows], "model_calls": 0}


# ------------------------------------------------------------------ triage
def update_turn(h, turn_id: str, payload) -> dict:
    row = _row(h, turn_id)
    body = _body(payload)
    _no_extra_fields(body, UPDATE_FIELDS, "Updating a secretary turn")
    if "status" not in body:
        raise Invalid("status is required: the recorded text cannot be edited, only triaged")
    status = body["status"]
    if status not in UPDATE_STATUSES:
        raise Invalid("status must be kept or discarded")
    h.db.execute("UPDATE secretary_turns SET status=? WHERE id=?", (status, row["id"]))
    h.db.commit()
    return _payload(_row(h, turn_id))


# ------------------------------------------------------------------ delete
def delete_turn(h, turn_id: str) -> dict:
    row = _row(h, turn_id)
    h.db.execute("DELETE FROM secretary_turns WHERE id=?", (row["id"],))
    h.db.commit()
    return {"deleted": True, "turn_id": row["id"], "model_calls": 0}


def clear_turns(h, project_id: str, status: str = "discarded") -> dict:
    """Delete exactly the turns whose status was chosen; kept turns are never touched."""
    h.project(project_id)  # KeyError -> 404 for unknown projects
    if status not in CLEARABLE_STATUSES:
        raise Invalid("only discarded turns can be cleared in one call; delete a kept turn one at a time")
    ids = [r["id"] for r in h.db.execute("SELECT id FROM secretary_turns WHERE project_id=? AND status=?",
                                        (project_id, status))]
    if ids:
        h.db.execute("DELETE FROM secretary_turns WHERE project_id=? AND status=?", (project_id, status))
        h.db.commit()
    return {"deleted": len(ids), "project_id": project_id, "status": status, "model_calls": 0}
