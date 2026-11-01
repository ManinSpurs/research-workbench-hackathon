"""Secretary review log: past secretary exchanges kept or deleted by the person.

The log is a record, not a transcript: nothing here is read back into a prompt or
briefing. A recorded turn's text is immutable; only `status` (kept / discarded) can
change, so keeping and deleting are honest operations on the person's own list.
Local only: no model call, no socket, no process.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient

from app import secretary_log, store
from app.api import create_app
from app.harness import Conflict
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)

COUNT = "SELECT count(*) c FROM secretary_turns"
MINE = "Which cost field should I use for the 2023 estimate?"
HER_ANSWER = "Use DD-2023 est_cost; it is the applicant-reported figure."


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def record(h, pid, role="researcher", text=MINE, **over):
    body = {"role": role, "body": {"text": text}}
    body.update(over)
    return secretary_log.record_turn(h, pid, body)


def dump(h):
    return "\n".join(h.db.iterdump())


# ------------------------------------------------------------------ schema
def test_schema_is_one_additive_table_with_the_log_columns(h):
    pid = seed(h)
    record(h, pid)
    cols = {c["name"]: c for c in h.db.execute("PRAGMA table_info('secretary_turns')")}
    assert set(cols) == {"id", "project_id", "pack_id", "question_id", "role", "body_json",
                         "status", "created_at"}
    for name in ("project_id", "role", "body_json", "status", "created_at"):
        assert cols[name]["notnull"] == 1, name
    assert cols["id"]["pk"] == 1, "the turn ID is the primary key"
    for name in ("pack_id", "question_id"):
        assert cols[name]["notnull"] == 0, name
    # Existing tables are untouched: a fresh connection needs no ALTER.
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(path)
    try:
        assert {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= \
            {"secretary_turns", "questions", "projects", "clarifications"}
    finally:
        other.close()


# ------------------------------------------------------------------ record / list
def test_record_persists_the_text_and_lists_newest_first(h):
    pid = seed(h)
    first = record(h, pid, role="researcher")
    second = record(h, pid, role="secretary", text=HER_ANSWER)
    assert first["role"] == "researcher" and second["role"] == "secretary"
    assert first["status"] == "recorded" and second["status"] == "recorded"
    assert first["model_calls"] == 0
    assert first["body"] == {"text": MINE}, "the recorded text survives verbatim"
    assert second["body"] == {"text": HER_ANSWER}
    listed = secretary_log.list_turns(h, pid)
    assert listed["project_id"] == pid and listed["model_calls"] == 0
    assert [t["id"] for t in listed["turns"]] == [second["id"], first["id"]], "newest first"
    assert listed["turns"][0]["created_at"] == second["created_at"]


def answer_question(h, pid, text="What does est_cost measure?"):
    """A question row for this project: the turn may name a question, like everything else."""
    qid = store.new_id("q")
    h.db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?)",
                 (qid, pid, text, None, text, "done", "remember", "idle", store.now()))
    h.db.commit()
    return qid


def test_a_turn_can_be_attributed_to_a_chip_and_a_question(h):
    pid = seed(h)
    qid = answer_question(h, pid)
    turn = record(h, pid, pack_id="chip-fx", question_id=qid)
    assert turn["pack_id"] == "chip-fx" and turn["question_id"] == qid
    assert turn["body"] == {"text": MINE}


def test_chip_filter_isolates_two_chips_from_each_other(h):
    pid = seed(h)
    a1 = record(h, pid, pack_id="chip-a", text="Chip A first")
    a2 = record(h, pid, pack_id="chip-a", text="Chip A second")
    b1 = record(h, pid, pack_id="chip-b", text="Chip B only")
    loose = record(h, pid, text="No chip")
    only_a = secretary_log.list_turns(h, pid, pack_id="chip-a")
    only_b = secretary_log.list_turns(h, pid, pack_id="chip-b")
    assert [t["id"] for t in only_a["turns"]] == [a2["id"], a1["id"]]
    assert [t["id"] for t in only_b["turns"]] == [b1["id"]]
    everything = secretary_log.list_turns(h, pid)
    assert {t["id"] for t in everything["turns"]} == {a1["id"], a2["id"], b1["id"], loose["id"]}


def test_status_filter_and_bounded_limit(h):
    pid = seed(h)
    turns = [record(h, pid, text=f"Turn {n}") for n in range(5)]
    assert len(secretary_log.list_turns(h, pid, limit=2)["turns"]) == 2
    assert len(secretary_log.list_turns(h, pid, limit=10_000)["turns"]) == 5
    kept = secretary_log.update_turn(h, turns[0]["id"], {"status": "kept"})
    assert kept["status"] == "kept"
    only_kept = secretary_log.list_turns(h, pid, status="kept")
    assert [t["id"] for t in only_kept["turns"]] == [turns[0]["id"]]
    assert len(secretary_log.list_turns(h, pid, status="recorded")["turns"]) == 4


def test_listing_another_projects_turns_is_isolated(h):
    pid = seed(h)
    other = seed(h)
    mine = record(h, pid)
    record(h, other)
    assert [t["id"] for t in secretary_log.list_turns(h, pid)["turns"]] == [mine["id"]]


# ------------------------------------------------------------------ triage
def test_discarding_then_clearing_reports_the_count_and_leaves_kept_turns(h):
    pid = seed(h)
    turns = [record(h, pid, text=f"Turn {n}") for n in range(4)]
    for turn in turns[:3]:
        secretary_log.update_turn(h, turn["id"], {"status": "discarded"})
    secretary_log.update_turn(h, turns[3]["id"], {"status": "kept"})
    cleared = secretary_log.clear_turns(h, pid)
    assert cleared == {"deleted": 3, "project_id": pid, "status": "discarded", "model_calls": 0}
    assert h.db.execute(COUNT).fetchone()["c"] == 1
    remaining = secretary_log.list_turns(h, pid)["turns"]
    assert [t["id"] for t in remaining] == [turns[3]["id"]]
    assert remaining[0]["status"] == "kept" and remaining[0]["body"] == {"text": "Turn 3"}
    # A second clear has nothing left to delete, and never touches a kept turn.
    assert secretary_log.clear_turns(h, pid)["deleted"] == 0
    assert h.db.execute(COUNT).fetchone()["c"] == 1


def test_clear_never_reaches_another_project(h):
    pid = seed(h)
    other = seed(h)
    mine = record(h, pid)
    theirs = record(h, other)
    secretary_log.update_turn(h, mine["id"], {"status": "discarded"})
    secretary_log.update_turn(h, theirs["id"], {"status": "discarded"})
    assert secretary_log.clear_turns(h, pid)["deleted"] == 1
    assert [t["id"] for t in secretary_log.list_turns(h, other)["turns"]] == [theirs["id"]]


def test_editing_the_recorded_text_is_refused_while_a_status_change_is_accepted(h, client):
    pid = seed(h)
    turn = record(h, pid, text=MINE)
    before = dump(h)
    for payload, message in [
        ({"body": {"text": "Rewritten history"}}, "does not accept the field"),
        ({"role": "secretary"}, "does not accept the field"),
        ({}, "status is required"),
        ({"status": "recorded"}, "status must be kept or discarded"),
        ({"status": "approved"}, "status must be kept or discarded"),
    ]:
        with pytest.raises(secretary_log.Invalid, match=message):
            secretary_log.update_turn(h, turn["id"], payload)
    assert dump(h) == before, "a refused update must write nothing"
    accepted = client.patch(f"/secretary-turns/{turn['id']}", json={"status": "kept"})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "kept"
    assert accepted.json()["body"] == {"text": MINE}, "the log keeps what was said"
    assert secretary_log.list_turns(h, pid)["turns"][0]["body"] == {"text": MINE}


# ------------------------------------------------------------------ delete
def test_delete_removes_exactly_one_turn_for_real(h, client):
    pid = seed(h)
    doomed = record(h, pid, text="Not needed")
    survivor = record(h, pid, text="Keep this one")
    assert client.delete(f"/secretary-turns/{doomed['id']}").json() == \
        {"deleted": True, "turn_id": doomed["id"], "model_calls": 0}
    assert h.db.execute(COUNT).fetchone()["c"] == 1
    assert [t["id"] for t in secretary_log.list_turns(h, pid)["turns"]] == [survivor["id"]]
    assert client.delete(f"/secretary-turns/{doomed['id']}").status_code == 404


# ------------------------------------------------------------------ interaction with question deletion
def test_a_surviving_turn_on_another_question_is_retained_not_a_deletion_failure(h):
    """Deleting a question must not be reported as failed because a turn that
    belongs to a DIFFERENT question still quotes the deleted words.

    Retention here is row-scoped: secretary_turns is purged, but only for the
    deleted question's own question_id. A turn bound to another question is
    never touched, so its text surviving is the promise being kept, not broken.
    The old check filtered by TABLE, could not express that, and surfaced
    "Deletion incomplete" -- the user-visible alarm this test pins shut."""
    pid = seed(h)
    text = "How long does processing take?"
    doomed = answer_question(h, pid, text)
    other = answer_question(h, pid, "What does est_cost measure?")
    quotes = record(h, pid, text=f"About the other question, quoting: {text}", question_id=other)

    result = h.delete_question(doomed)

    # The turn really did survive with the deleted text in it...
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (quotes["id"],)).fetchone()["c"] == 1
    assert text in h.db.execute("SELECT body_json FROM secretary_turns WHERE id=?",
                                (quotes["id"],)).fetchone()["body_json"]
    # ...so it must not be counted as a leftover, and must not be hidden either.
    assert result["verified"] is True
    assert result["remaining_hits"] == []
    kept = [x for x in result["retained_hits"] if x["table"] == "secretary_turns"]
    assert len(kept) == 1 and kept[0]["column"] == "body_json"
    assert kept[0]["rows"] == 1  # one turn is one hit, not one per needle


def test_an_unbound_turn_quoting_the_question_survives_verification(h):
    """The other half of the same row-scoped case: a turn with question_id NULL.

    It is a project-level note, not part of any question, so no deletion owns it
    -- `question_id IS NOT ?` in the purge scope is what makes that true (a `<>`
    would silently drop NULL rows). It quotes the deleted text, and the delete
    must still verify."""
    pid = seed(h)
    text = "How long does processing take?"
    doomed = answer_question(h, pid, text)
    note = record(h, pid, text=f"Project note quoting: {text}")
    assert note["question_id"] is None

    result = h.delete_question(doomed)

    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (note["id"],)).fetchone()["c"] == 1
    assert result["verified"] is True
    assert result["remaining_hits"] == []
    assert [x["table"] for x in result["retained_hits"]] == ["secretary_turns"]


def test_a_turn_bound_to_the_deleted_question_is_purged_and_a_leftover_fails(h):
    """The flag still has teeth for this table.

    A turn bound to the deleted question IS in the purge scope. Normally it is
    deleted; here a BEFORE DELETE trigger raising IGNORE keeps it, which is what
    a real regression (wrong WHERE, renamed column, skipped DELETE) would look
    like. The leftover is genuine, so verified must be False and the hit must
    name this row -- while the project's unbound turn stays retained, not
    accused."""
    pid = seed(h)
    text = "How long does processing take?"
    doomed = answer_question(h, pid, text)
    bound = record(h, pid, text=f"You asked: {text}", question_id=doomed)
    unbound = record(h, pid, text=f"Project note: {text}")
    h.db.execute("CREATE TRIGGER keep_turns BEFORE DELETE ON secretary_turns"
                 " BEGIN SELECT RAISE(IGNORE); END")
    h.db.commit()

    result = h.delete_question(doomed)

    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (bound["id"],)).fetchone()["c"] == 1
    assert result["verified"] is False
    assert [x["table"] for x in result["remaining_hits"]] == ["secretary_turns"]
    assert result["remaining_hits"][0]["rows"] == 1
    # The unbound turn is retention, not a second "failure".
    assert [x["table"] for x in result["retained_hits"]] == ["secretary_turns"]


# ------------------------------------------------------------------ refusals
def test_an_unknown_project_is_404(client):
    expected = {"error": "not_found", "message": "project not found", "details": {}}
    assert client.get("/projects/proj-missing/secretary-turns").status_code == 404
    assert client.get("/projects/proj-missing/secretary-turns").json() == expected
    assert client.post("/projects/proj-missing/secretary-turns",
                       json={"role": "researcher", "body": {"text": "x"}}).status_code == 404
    assert client.post("/projects/proj-missing/secretary-turns/clear-discarded").status_code == 404


def test_an_unknown_turn_is_404(h, client):
    expected = {"error": "not_found", "message": "secretary turn not found", "details": {}}
    assert client.patch("/secretary-turns/turn-missing", json={"status": "kept"}).status_code == 404
    assert client.patch("/secretary-turns/turn-missing", json={"status": "kept"}).json() == expected
    assert client.delete("/secretary-turns/turn-missing").status_code == 404
    with pytest.raises(KeyError):
        secretary_log.update_turn(h, "turn-missing", {"status": "kept"})


@pytest.mark.parametrize("role", ["assistant", "system", "Secretary", "", None])
def test_a_role_other_than_the_two_allowed_is_refused(h, client, role):
    pid = seed(h)
    with pytest.raises(secretary_log.Invalid, match="role must be researcher or secretary"):
        secretary_log.record_turn(h, pid, {"role": role, "body": {"text": MINE}})
    assert h.db.execute(COUNT).fetchone()["c"] == 0
    r = client.post(f"/projects/{pid}/secretary-turns", json={"role": role, "body": {"text": MINE}})
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request", "message": "role must be researcher or secretary",
                        "details": {}}


@pytest.mark.parametrize("body, message", [
    ("x", "body must be a JSON object that carries the recorded text"),
    ({"text": "   "}, "body must carry the recorded text"),
    ({"steps": []}, "body must carry the recorded text"),
    (None, "body is required"),
])
def test_a_turn_without_text_is_refused(h, client, body, message):
    pid = seed(h)
    before = dump(h)
    with pytest.raises(secretary_log.Invalid, match=message):
        secretary_log.record_turn(h, pid, {"role": "researcher", "body": body})
    assert dump(h) == before, "a refused record must write nothing"
    assert client.post(f"/projects/{pid}/secretary-turns",
                       json={"role": "researcher", "body": body}).status_code == 400


def test_unknown_fields_and_manual_status_at_record_time_are_refused(h):
    pid = seed(h)
    with pytest.raises(secretary_log.Invalid, match="does not accept the field"):
        secretary_log.record_turn(h, pid, {"role": "researcher", "body": {"text": MINE}, "status": "kept"})
    with pytest.raises(secretary_log.Invalid, match="does not accept the field"):
        secretary_log.record_turn(h, pid, {"role": "researcher", "body": {"text": MINE}, "confidence": 0.9})
    assert h.db.execute(COUNT).fetchone()["c"] == 0


def test_a_question_from_another_project_is_a_conflict(h):
    pid = seed(h)
    other = seed(h)
    qid = answer_question(h, other)
    with pytest.raises(Conflict, match="belongs to another project"):
        record(h, pid, question_id=qid)
    assert h.db.execute(COUNT).fetchone()["c"] == 0


# ------------------------------------------------------------------ routes
def test_route_roundtrip(h, client):
    pid = seed(h)
    created = client.post(f"/projects/{pid}/secretary-turns",
                          json={"role": "researcher", "body": {"text": MINE}, "pack_id": "chip-fx"})
    assert created.status_code == 200
    turn = created.json()
    assert turn["status"] == "recorded" and turn["pack_id"] == "chip-fx"
    assert client.get(f"/projects/{pid}/secretary-turns").json()["turns"] == [turn]
    assert client.get(f"/projects/{pid}/secretary-turns",
                      params={"pack_id": "chip-fx"}).json()["turns"] == [turn]
    assert client.get(f"/projects/{pid}/secretary-turns", params={"pack_id": "chip-other"}).json()["turns"] == []
    assert client.patch(f"/secretary-turns/{turn['id']}", json={"status": "discarded"}).json()["status"] == "discarded"
    assert client.get(f"/projects/{pid}/secretary-turns",
                      params={"status": "discarded"}).json()["turns"][0]["id"] == turn["id"]
    assert client.post(f"/projects/{pid}/secretary-turns/clear-discarded").json()["deleted"] == 1
    assert client.get(f"/projects/{pid}/secretary-turns").json()["turns"] == []


def test_route_clear_refuses_a_status_it_may_not_bulk_delete(h, client):
    pid = seed(h)
    assert client.post(f"/projects/{pid}/secretary-turns/clear-discarded",
                       params={"status": "kept"}).status_code == 400
    with pytest.raises(secretary_log.Invalid, match="only discarded turns can be cleared"):
        secretary_log.clear_turns(h, pid, "kept")


def test_the_log_is_never_read_by_the_model_or_briefing_path():
    """A record, not a transcript: no model input imports or reads this module."""
    source = Path(secretary_log.__file__).read_text()
    for banned in ("requests", "httpx", "urllib", "socket", "subprocess", "openai", "pydantic_ai"):
        assert banned not in source
    assert "import" in source
    ai_dir = ROOT / "app" / "ai"
    for path in ai_dir.rglob("*.py"):
        assert "secretary_log" not in path.read_text(), path
    assert "secretary_log" not in (ROOT / "app" / "harness.py").read_text()
