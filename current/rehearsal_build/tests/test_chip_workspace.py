"""Offline topic notebook lifecycle, using only an isolated WB_DB and data."""
import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from test_chip_projects import ROOT, ask, chip, project
from app import packs
from app.api import create_app
from app.chips import ChipStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WB_DB", str(tmp_path / "workspace.db"))
    monkeypatch.delenv("FAKE_BASE_URL", raising=False)
    monkeypatch.delenv("WB_ALLOW_REAL_MUSE", raising=False)
    monkeypatch.setattr(packs, "INSTALLED_PACKS", {})
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    with TestClient(create_app(data_dir=data)) as client:
        yield client, tmp_path / "workspace.db", data


def test_create_reopen_empty_and_restart(workspace):
    c, db, data = workspace
    chip(c, "topic_a")
    legacy = project(c)
    first = c.post("/chips/topic_a/workspace").json()
    assert first["created"] is True and first["pack_id"] == "topic_a"
    assert first["kind"].startswith("custom-")
    again = c.post("/chips/topic_a/workspace").json()
    assert again == {**first, "created": False}
    with sqlite3.connect(db) as conn:
        assert json.loads(conn.execute("SELECT files_json FROM projects WHERE id=?",
                                     (first["project_id"],)).fetchone()[0]) == []
        assert conn.execute("SELECT pack_id FROM projects WHERE id=?", (legacy,)).fetchone()[0] is None
    qid = ask(c, first["project_id"], "What should we study?")
    assert c.get(f"/questions/{qid}/context").json()["pack_id"] == "topic_a"
    briefing = c.get(f"/projects/{first['project_id']}/briefing")
    assert briefing.status_code == 200
    with TestClient(create_app(data_dir=data)) as restarted:
        assert restarted.post("/chips/topic_a/workspace").json() == again
        assert restarted.get(f"/projects/{first['project_id']}/profile").status_code == 200
        assert restarted.get(f"/questions/{qid}/context").json()["pack_id"] == "topic_a"
        # Rechecking after restart may advance verified_at. Saved content and
        # saved_at must survive; a fresh verification timestamp is not drift.
        def saved_content(value):
            if isinstance(value, dict):
                return {key: saved_content(item) for key, item in value.items()
                        if key != "verified_at"}
            if isinstance(value, list):
                return [saved_content(item) for item in value]
            return value
        assert saved_content(restarted.get(f"/projects/{first['project_id']}/briefing").json()) == saved_content(briefing.json())


def test_attach_and_refuse_overwrite_or_incompatible_pins(workspace):
    c, db, _ = workspace
    chip(c, "topic_a")
    chip(c, "topic_b")
    pid = project(c)
    assert c.post(f"/projects/{pid}/chip", json={"pack_id": "topic_a"}).status_code == 200
    assert c.post("/chips/topic_a/workspace").json()["project_id"] == pid
    qid = ask(c, pid, "What is the rate?")
    pin = c.get(f"/questions/{qid}/context").json()
    assert c.post(f"/projects/{pid}/chip", json={"pack_id": "topic_b"}).status_code == 409
    other = project(c)
    assert c.post(f"/projects/{other}/chip", json={"pack_id": "topic_a"}).status_code == 409
    assert c.get(f"/questions/{qid}/context").json() == pin
    assert c.get(f"/projects/{pid}/pack-memory?pack_id=topic_b").status_code == 409


def test_compile_scope_ambiguity_and_deleted_chip_no_writes(workspace):
    c, db, _ = workspace
    chip(c, "topic_a")
    chip(c, "topic_b")
    a = c.post("/chips/topic_a/workspace").json()["project_id"]
    b = c.post("/chips/topic_b/workspace").json()["project_id"]
    assert c.post("/chips/topic_a/conversation/compile", json={"project_id": b}).status_code == 409
    assert c.get("/chips/topic_a/conversation").json()["text"] == ""
    project(c, "topic_a")
    assert c.post("/chips/topic_a/workspace").status_code == 409
    assert c.post("/chips/topic_a/conversation/compile", json={"project_id": a}).status_code == 409
    assert c.post("/chips/missing/workspace").status_code == 404
    assert c.delete("/chips/topic_b").status_code == 200
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
    assert c.post("/chips/topic_b/workspace").status_code == 404
    assert c.post("/chips/topic_b/conversation/compile", json={"project_id": b}).status_code == 404
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == before


@pytest.mark.parametrize("invalid", ["foreign_question", "unpinned_question", "foreign_turn"])
def test_compile_refuses_incompatible_contents_without_storage_writes(workspace, invalid):
    c, db, data = workspace
    chip(c, "topic_a")
    chip(c, "topic_b")
    pid = c.post("/chips/topic_a/workspace").json()["project_id"]
    qid = ask(c, pid, "Keep this question in its original scope")
    assert c.put("/chips/topic_a/conversation", json={"text": "Original notebook\n"}).status_code == 200
    target = ChipStore(data)._target("topic_a")
    before = target.read_bytes()
    with sqlite3.connect(db) as conn:
        if invalid == "foreign_question":
            conn.execute("UPDATE question_contexts SET pack_id='topic_b' WHERE question_id=?", (qid,))
        elif invalid == "unpinned_question":
            conn.execute("DELETE FROM question_contexts WHERE question_id=?", (qid,))
        else:
            conn.execute("INSERT INTO secretary_turns VALUES(?,?,?,?,?,?,?,?)",
                         ("foreign", pid, "topic_b", None, "user", '{"text":"Foreign history"}',
                          "kept", "2026-01-01T00:00:00Z"))
    response = c.post("/chips/topic_a/conversation/compile", json={"project_id": pid})
    assert response.status_code == 409, response.json()
    assert target.read_bytes() == before
    # Automatic export must not silently filter the same incompatible contents.
    response = c.post("/chips/export/topic_a")
    assert response.status_code == 409, response.json()
    assert target.read_bytes() == before


def test_attach_compatible_pins_preserves_history_and_compiles_null_turns(workspace):
    c, db, _ = workspace
    chip(c, "topic_a")
    pid = project(c)
    response = c.post(f"/projects/{pid}/questions",
                      json={"text": "Original topic question", "pack_id": "topic_a"})
    assert response.status_code == 200, response.json()
    qid = response.json()["question_id"]
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO secretary_turns VALUES(?,?,?,?,?,?,?,?)",
                     ("pre-chip", pid, None, qid, "user", '{"text":"My pre-chip history"}',
                      "kept", "2026-01-01T00:00:00Z"))
        question = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        context = conn.execute("SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone()
        turn = conn.execute("SELECT * FROM secretary_turns WHERE id='pre-chip'").fetchone()
    assert c.post(f"/projects/{pid}/chip", json={"pack_id": "topic_a"}).status_code == 200
    assert c.post("/chips/topic_a/conversation/compile", json={"project_id": pid}).status_code == 200
    text = c.get("/chips/topic_a/conversation").json()["text"]
    assert "My pre-chip history" in text and "Original topic question" in text
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone() == question
        assert conn.execute("SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone() == context
        assert conn.execute("SELECT * FROM secretary_turns WHERE id='pre-chip'").fetchone() == turn


def test_attach_refuses_legacy_unpinned_question(workspace):
    c, db, _ = workspace
    chip(c, "topic_a")
    pid = project(c)
    qid = ask(c, pid, "Legacy question without a pin")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM question_contexts WHERE question_id=?", (qid,))
    assert c.post(f"/projects/{pid}/chip", json={"pack_id": "topic_a"}).status_code == 409
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT pack_id FROM projects WHERE id=?", (pid,)).fetchone()[0] is None
        assert conn.execute("SELECT original_text FROM questions WHERE id=?", (qid,)).fetchone()[0] == "Legacy question without a pin"
        assert conn.execute("SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone() is None
