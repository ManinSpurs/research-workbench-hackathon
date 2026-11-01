"""Persisted display order: project cards (PUT /projects/order) and a project's
library PDFs (PUT /library/order).

Storage and API only. Every test works on a WB_DB temp database (the shared `h`
fixture from test_dot_safety.py) and its own tmp data dir; PDFs are generated in
the test, so no fixture file travels with the repo and no model or network call
is made. The payload/status contract and the reject-and-leave-unchanged rule are
pinned here because the frontend relies on both.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient

from app import list_order, store
from app.api import create_app
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)
from test_library import TWO_PAGE_TEXT, pdf_b64


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(str(path), h.data)) as c:
        yield c


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def projects(client):
    r = client.get("/projects")
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()["projects"]]


def tree(client, pid):
    r = client.get("/library/tree", params={"project_id": pid})
    assert r.status_code == 200, r.text
    return r.json()


def item_ids(client, pid):
    return [i["id"] for i in tree(client, pid)["items"]]


def pdf(client, pid, name="paper"):
    r = client.post("/library/docs", json={"project_id": pid, "filename": f"{name}.pdf",
                                           "pdf_base64": pdf_b64(TWO_PAGE_TEXT)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def note(client, pid, text):
    r = client.post("/library/notes", json={"project_id": pid, "text": text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def put_order(client, pid, ids):
    return client.put("/library/order", json={"project_id": pid, "item_ids": ids})


# ------------------------------------------------------------------ schema
def test_list_order_table_is_additive(h):
    tables = {r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"library_items", "library_folders", "projects", "list_order"} <= tables
    cols = {c["name"] for c in h.db.execute("PRAGMA table_info('list_order')")}
    assert cols == {"list_kind", "scope_id", "target_id", "position", "created_at"}
    # No pre-existing table gained a column: the migration is one new table.
    assert {c["name"] for c in h.db.execute("PRAGMA table_info('library_items')")} == {
        "id", "project_id", "folder_id", "kind", "title", "day", "body_text", "pdf_path",
        "pages", "chars", "created_at"}


def test_existing_data_is_preserved_by_the_migration(h, tmp_path):
    """An older database (no list_order) opens, keeps every row, gets the table."""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,"
            " files_json TEXT NOT NULL, input_version TEXT NOT NULL,"
            " deletion_generation INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);"
            "INSERT INTO projects VALUES('p-old','Old study','fx','{}','v1',0,'2026-01-01T00:00:00+00:00');"
            "CREATE TABLE library_items(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, folder_id TEXT,"
            " kind TEXT NOT NULL, title TEXT NOT NULL, day TEXT NOT NULL, body_text TEXT, pdf_path TEXT,"
            " pages INTEGER, chars INTEGER, created_at TEXT NOT NULL);"
            "INSERT INTO library_items VALUES('li-old','p-old',NULL,'pdf','Legacy','2026-01-01',NULL,"
            "'library/li-old/a.pdf',1,3,'2026-01-01T00:00:00+00:00');")
    db = store.connect(str(path))
    try:
        assert db.execute("SELECT id FROM projects").fetchone()["id"] == "p-old"
        assert db.execute("SELECT id FROM library_items").fetchone()["id"] == "li-old"
        assert db.execute("SELECT count(*) c FROM list_order").fetchone()["c"] == 0
    finally:
        db.close()


# ------------------------------------------------------------------ projects
def test_projects_keep_creation_order_until_saved(client, h):
    first, second, third = seed(h), seed(h), seed(h)
    assert projects(client) == [first, second, third]
    rows = client.get("/projects").json()["projects"]
    assert set(rows[0]) == {"id", "name", "kind", "pack_id"}


def test_project_order_round_trip_and_reopen(client, h):
    first, second, third = seed(h), seed(h), seed(h)
    wanted = [third, first, second]

    r = client.put("/projects/order", json={"project_ids": wanted})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"projects"}
    # Same public row shape, in the saved order.
    assert [p["id"] for p in body["projects"]] == wanted
    assert set(body["projects"][0]) == {"id", "name", "kind", "pack_id"}
    assert projects(client) == wanted

    # The rows are in SQLite, so a fresh connection (a reopen) reads them too.
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(path)
    try:
        saved = list_order.positions(other, list_order.PROJECT_LIST, list_order.GLOBAL_SCOPE)
        assert saved == {third: 0, first: 1, second: 2}
    finally:
        other.close()

    with TestClient(create_app(path, h.data)) as again:
        assert [p["id"] for p in again.get("/projects").json()["projects"]] == wanted


def test_new_project_appends_after_ordered_ones(client, h):
    first, second = seed(h), seed(h)
    assert client.put("/projects/order", json={"project_ids": [second, first]}).status_code == 200
    late = seed(h)
    assert projects(client) == [second, first, late]
    # A second save may place the newcomer anywhere; the payload is complete.
    assert client.put("/projects/order", json={"project_ids": [late, second, first]}).status_code == 200
    assert projects(client) == [late, second, first]


@pytest.mark.parametrize("payload", [
    {"project_ids": ["p-missing"]},                        # unknown id
    {"project_ids": "not-a-list"},
    {"project_ids": [1, 2]},
    {"project_ids": []},                                   # omits every current project
    {"ids": []},                                           # wrong key
    {"project_ids": [], "extra": 1},                       # extra key
])
def test_project_order_rejects_bad_payloads_and_changes_nothing(client, h, payload):
    first, second = seed(h), seed(h)
    assert client.put("/projects/order", json={"project_ids": [second, first]}).status_code == 200
    r = client.put("/projects/order", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "conflict"
    # Atomically refused: the saved order is exactly what it was.
    assert projects(client) == [second, first]
    assert list_order.positions(h.db, list_order.PROJECT_LIST, list_order.GLOBAL_SCOPE) == \
        {second: 0, first: 1}


def test_project_order_rejects_partial_duplicate_and_stale_membership(client, h):
    first, second, third = seed(h), seed(h), seed(h)
    partial = client.put("/projects/order", json={"project_ids": [second]})
    assert partial.status_code == 409 and "omits" in partial.json()["message"]
    repeated = client.put("/projects/order", json={"project_ids": [second, second, first, third]})
    assert repeated.status_code == 409 and "repeat" in repeated.json()["message"]
    stale = client.put("/projects/order", json={"project_ids": [second, first, third, "p-gone"]})
    assert stale.status_code == 409 and "not a current" in stale.json()["message"]
    assert projects(client) == [first, second, third]
    assert h.db.execute("SELECT count(*) c FROM list_order").fetchone()["c"] == 0


# ------------------------------------------------------------------ library PDFs
def test_pdfs_keep_newest_first_until_saved(client, h):
    pid = seed(h)
    oldest, middle, newest = pdf(client, pid, "a"), pdf(client, pid, "b"), pdf(client, pid, "c")
    assert item_ids(client, pid) == [newest, middle, oldest]


def test_library_order_round_trip_returns_the_tree_and_persists(client, h):
    pid = seed(h)
    oldest, middle, newest = pdf(client, pid, "a"), pdf(client, pid, "b"), pdf(client, pid, "c")
    wanted = [oldest, newest, middle]

    r = put_order(client, pid, wanted)
    assert r.status_code == 200, r.text
    body = r.json()
    # Exactly the GET /library/tree contract.
    assert set(body) == {"folders", "items"}
    assert item_ids(client, pid) == wanted
    assert path_ids(body) == wanted
    assert set(body["items"][0]) >= {"id", "folder_id", "kind", "title", "day", "pages", "chars",
                                     "created_at", "text_status"}

    db_path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(db_path)
    try:
        assert list_order.positions(other, list_order.PDF_LIST, pid) == \
            {oldest: 0, newest: 1, middle: 2}
    finally:
        other.close()

    with TestClient(create_app(db_path, h.data)) as again:
        assert item_ids(again, pid) == wanted


def path_ids(body):
    return [i["id"] for i in body["items"]]


def test_notes_keep_their_order_and_slots_when_pdfs_move(client, h):
    pid = seed(h)
    first_pdf = pdf(client, pid, "a")
    first_note = note(client, pid, "meeting notes")
    second_pdf = pdf(client, pid, "b")
    second_note = note(client, pid, "reading notes")
    before = tree(client, pid)["items"]
    note_ids = [i["id"] for i in before if i["kind"] == "note"]
    assert note_ids == [second_note, first_note]

    assert put_order(client, pid, [first_pdf, second_pdf]).status_code == 200
    after = tree(client, pid)["items"]
    # Only the PDF slots changed; the notes hold their places and relative order.
    assert [i["id"] for i in after if i["kind"] == "note"] == note_ids
    assert [index for index, i in enumerate(after) if i["kind"] == "note"] == \
        [index for index, i in enumerate(before) if i["kind"] == "note"]
    assert [i["id"] for i in after if i["kind"] == "pdf"] == [first_pdf, second_pdf]


def test_pdf_uploaded_after_a_save_appends_after_placed_ones(client, h):
    pid = seed(h)
    oldest, newest = pdf(client, pid, "a"), pdf(client, pid, "b")
    assert put_order(client, pid, [oldest, newest]).status_code == 200
    late = pdf(client, pid, "late")
    assert [i for i in item_ids(client, pid) if i in (oldest, newest, late)] == [oldest, newest, late]


def test_empty_pdf_list_is_safe(client, h):
    pid = seed(h)
    note(client, pid, "a note but no PDF")
    r = put_order(client, pid, [])
    assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["items"]] == ["note"]
    # A PDF added later is the whole list, so it can be saved alone.
    only = pdf(client, pid, "only")
    assert put_order(client, pid, [only]).status_code == 200
    assert [i["id"] for i in tree(client, pid)["items"] if i["kind"] == "pdf"] == [only]


@pytest.mark.parametrize("case", ["partial", "duplicate", "note", "cross_project", "unknown",
                                  "missing_key", "extra_key", "not_a_list", "bad_id"])
def test_library_order_rejects_and_leaves_the_tree_unchanged(client, h, case):
    here, there = seed(h), seed(h)
    first, second = pdf(client, here, "a"), pdf(client, here, "b")
    elsewhere = pdf(client, there, "other")
    here_note = note(client, here, "must never be reordered")
    assert put_order(client, here, [first, second]).status_code == 200
    before = tree(client, here)
    there_before = tree(client, there)

    payload = {
        "partial": {"project_id": here, "item_ids": [second]},
        "duplicate": {"project_id": here, "item_ids": [first, second, first]},
        "note": {"project_id": here, "item_ids": [first, second, here_note]},
        "cross_project": {"project_id": here, "item_ids": [first, second, elsewhere]},
        "unknown": {"project_id": here, "item_ids": [first, second, "li-gone"]},
        "missing_key": {"project_id": here},
        "extra_key": {"project_id": here, "item_ids": [first, second], "folder_id": None},
        "not_a_list": {"project_id": here, "item_ids": "nope"},
        "bad_id": {"project_id": here, "item_ids": [first, second, ""]},
    }[case]
    r = client.put("/library/order", json=payload)
    assert r.status_code == 409, f"{case}: {r.text}"
    assert r.json()["error"] == "conflict"
    # Rejected atomically: neither this project's tree nor another project's moved.
    assert tree(client, here) == before
    assert tree(client, there) == there_before
    assert list_order.positions(h.db, list_order.PDF_LIST, here) == {first: 0, second: 1}


def test_library_order_unknown_project_is_404(client, h):
    assert client.put("/library/order", json={"project_id": "p-missing", "item_ids": []}).status_code == 404


def test_one_projects_order_does_not_touch_anothers(client, h):
    here, there = seed(h), seed(h)
    a1, a2 = pdf(client, here, "a1"), pdf(client, here, "a2")
    b1, b2 = pdf(client, there, "b1"), pdf(client, there, "b2")
    assert put_order(client, here, [a1, a2]).status_code == 200
    assert put_order(client, there, [b2, b1]).status_code == 200
    assert [i for i in item_ids(client, here) if i in (a1, a2)] == [a1, a2]
    assert [i for i in item_ids(client, there) if i in (b1, b2)] == [b2, b1]


def test_folder_delete_keeps_the_saved_pdf_order(client, h):
    pid = seed(h)
    r = client.post("/library/folders", json={"project_id": pid, "name": "Papers"})
    assert r.status_code == 200
    fid = r.json()["id"]
    first, second = pdf(client, pid, "a"), pdf(client, pid, "b")
    assert client.patch(f"/library/items/{first}", json={"folder_id": fid}).status_code == 200
    assert put_order(client, pid, [first, second]).status_code == 200
    assert client.delete(f"/library/folders/{fid}").status_code == 200
    # Items fall back to the root; the researcher's PDF order is untouched.
    assert [i for i in item_ids(client, pid) if i in (first, second)] == [first, second]


def test_stale_rows_for_vanished_members_are_ignored_and_dropped(client, h):
    """A member that no longer exists cannot pull a list out of shape, and the
    next save rewrites the whole list (deletion cleanup without a delete path)."""
    pid = seed(h)
    first, second = pdf(client, pid, "a"), pdf(client, pid, "b")
    h.db.execute("INSERT INTO list_order VALUES('library_pdf', ?, 'li-vanished', 0, ?)",
                 (pid, store.now()))
    h.db.commit()
    assert [i for i in item_ids(client, pid) if i in (first, second)] == [second, first]
    assert put_order(client, pid, [first, second]).status_code == 200
    stored = {r["target_id"] for r in h.db.execute(
        "SELECT target_id FROM list_order WHERE list_kind='library_pdf' AND scope_id=?", (pid,))}
    assert stored == {first, second}
