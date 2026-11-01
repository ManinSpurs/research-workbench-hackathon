"""Research library: folders, notes, PDFs, literal search and the on-device screen.

Storage and API only. The Apple on-device bridge is always mocked here
(`apple_ai._run`), so the suite makes no model call and no network call; the
`loopback_network_only` fixture in conftest.py enforces that independently.
PDFs are generated in the test itself, so no fixture file travels with the repo.
"""
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient

from app import apple_ai, library, store
from app.api import create_app
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)


# ------------------------------------------------------------------ PDF builder
def build_pdf(pages, compress=False):
    """Smallest well-formed PDF with one Helvetica page per string.

    An empty string produces a page whose content stream carries no text
    operators, which is exactly the scanned-page shape `NO_TEXT_LAYER` describes.
    """
    import zlib

    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = []
    for text in pages:
        ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for line in (text.splitlines() if text else []):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            ops.append(f"({escaped}) Tj T*")
        ops.append("ET")
        raw = "\n".join(ops).encode("latin-1")
        if compress:
            body = zlib.compress(raw)
            contents.append(add(b"<< /Length " + str(len(body)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                                + body + b"\nendstream"))
        else:
            contents.append(add(b"<< /Length " + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream"))
    pages_id = len(objects) + len(pages) + 1
    page_ids = []
    for index in range(len(pages)):
        page_ids.append(add(b"<< /Type /Page /Parent " + str(pages_id).encode() + b" 0 R"
                            b" /MediaBox [0 0 612 792] /Resources << /Font << /F1 " + str(font).encode() + b" 0 R >> >>"
                            b" /Contents " + str(contents[index]).encode() + b" 0 R >>"))
    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    add(b"<< /Type /Pages /Count " + str(len(pages)).encode() + b" /Kids [" + kids + b"] >>")
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root " + str(catalog).encode()
            + b" 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


TWO_PAGE_TEXT = ["Cover page of the filed permit study.", "The replacement cost estimate of 2.4 million dollars appears here."]


def pdf_b64(pages, compress=False):
    return base64.b64encode(build_pdf(pages, compress=compress)).decode("ascii")


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def folder(client, pid, name, parent_id=None):
    r = client.post("/library/folders", json={"project_id": pid, "name": name, "parent_id": parent_id})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ schema
def test_schema_is_additive_only(h):
    tables = {r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"library_folders", "library_items"} <= tables
    folder_cols = {c["name"] for c in h.db.execute("PRAGMA table_info('library_folders')")}
    assert folder_cols == {"id", "project_id", "parent_id", "name", "created_at"}
    item_cols = {c["name"] for c in h.db.execute("PRAGMA table_info('library_items')")}
    assert item_cols == {"id", "project_id", "folder_id", "kind", "title", "day", "body_text",
                         "pdf_path", "pages", "chars", "created_at"}


def test_existing_tables_survive_a_fresh_connection(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(path)
    try:
        tables = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"questions", "projects", "latex_drafts", "library_items"} <= tables
    finally:
        other.close()


# ------------------------------------------------------------------ folders
def test_folder_lifecycle(client, h):
    pid = seed(h)
    parent = folder(client, pid, "  Papers  ")
    assert parent["name"] == "Papers" and parent["parent_id"] is None and parent["created_at"]

    child = folder(client, pid, "2024", parent["id"])
    assert child["parent_id"] == parent["id"]

    renamed = client.patch(f"/library/folders/{child['id']}", json={"name": "2024 filings"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "2024 filings"

    tree = client.get("/library/tree", params={"project_id": pid}).json()
    assert [f["name"] for f in tree["folders"]] == ["Papers", "2024 filings"]
    assert tree["items"] == []


def test_folder_rejects_cycle(client, h):
    pid = seed(h)
    a = folder(client, pid, "a")
    b = folder(client, pid, "b", a["id"])
    c = folder(client, pid, "c", b["id"])

    own = client.patch(f"/library/folders/{a['id']}", json={"parent_id": a["id"]})
    assert own.status_code == 409, own.text
    assert "descendant" in own.json()["message"]

    down = client.patch(f"/library/folders/{a['id']}", json={"parent_id": c["id"]})
    assert down.status_code == 409

    # A legal re-parent still works after the rejection (no half-applied state).
    moved = client.patch(f"/library/folders/{c['id']}", json={"parent_id": None})
    assert moved.status_code == 200 and moved.json()["parent_id"] is None


def test_delete_folder_moves_items_and_reparents_children(client, h):
    pid = seed(h)
    top = folder(client, pid, "top")
    middle = folder(client, pid, "middle", top["id"])
    leaf = folder(client, pid, "leaf", middle["id"])
    note = client.post("/library/notes", json={"project_id": pid, "folder_id": middle["id"],
                                               "text": "Reading note that must survive"}).json()

    response = client.delete(f"/library/folders/{middle['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": middle["id"], "items_moved_to_root": 1, "folders_reparented": 1}

    tree = client.get("/library/tree", params={"project_id": pid}).json()
    folders = {f["id"]: f for f in tree["folders"]}
    assert middle["id"] not in folders
    assert folders[leaf["id"]]["parent_id"] == top["id"]
    # Never silently deletes: the note is still there, just at the root.
    assert [i["id"] for i in tree["items"]] == [note["id"]]
    assert tree["items"][0]["folder_id"] is None


def test_folder_rejects_bad_input(client, h):
    pid = seed(h)
    assert client.post("/library/folders", json={"project_id": pid, "name": "  "}).status_code == 409
    assert client.patch("/library/folders/lf-missing", json={"name": "x"}).status_code == 404
    assert client.patch(f"/library/folders/{folder(client, pid, 'k')['id']}", json={"other": 1}).status_code == 409
    assert client.get("/library/tree", params={"project_id": "p-missing"}).status_code == 404


# ------------------------------------------------------------------ notes
def test_note_day_grouping_and_default_title(client, h):
    pid = seed(h)
    note = client.post("/library/notes", json={"project_id": pid,
                                               "text": "  First line becomes the title\nsecond line"}).json()
    assert note["title"] == "First line becomes the title"
    assert note["day"] == datetime.now().strftime("%Y-%m-%d")
    assert note["kind"] == "note" and note["folder_id"] is None
    assert note["pages"] is None and note["chars"] == len("  First line becomes the title\nsecond line")

    titled = client.post("/library/notes", json={"project_id": pid, "text": "body", "title": "Given"}).json()
    assert titled["title"] == "Given"

    tree = client.get("/library/tree", params={"project_id": pid}).json()
    by_day = {}
    for item in tree["items"]:
        by_day.setdefault(item["day"], []).append(item["id"])
    assert sorted(by_day) == [note["day"]]
    assert set(by_day[note["day"]]) == {note["id"], titled["id"]}


def test_note_rejections(client, h):
    pid = seed(h)
    assert client.post("/library/notes", json={"project_id": pid, "text": ""}).status_code == 409
    assert client.post("/library/notes", json={"project_id": "p-missing", "text": "x"}).status_code == 404
    assert client.post("/library/notes", json={"project_id": pid, "text": "x", "folder_id": "lf-missing"}).status_code == 404
    assert client.post("/library/notes", json={"project_id": pid, "text": "x", "extra": 1}).status_code == 409


def test_item_move_and_rename(client, h):
    pid = seed(h)
    target = folder(client, pid, "target")
    note = client.post("/library/notes", json={"project_id": pid, "text": "movable"}).json()
    moved = client.patch(f"/library/items/{note['id']}", json={"folder_id": target["id"]}).json()
    assert moved["folder_id"] == target["id"]
    renamed = client.patch(f"/library/items/{note['id']}", json={"title": "Renamed"}).json()
    assert renamed["title"] == "Renamed" and renamed["folder_id"] == target["id"]
    assert client.patch(f"/library/items/{note['id']}", json={"folder_id": None}).json()["folder_id"] is None
    assert client.patch("/library/items/li-missing", json={"title": "x"}).status_code == 404


# ------------------------------------------------------------------ PDFs
def test_pdf_upload_stores_a_real_file_and_reports_text(client, h):
    pid = seed(h)
    doc = client.post("/library/docs", json={"project_id": pid, "filename": "permit study.pdf",
                                             "pdf_base64": pdf_b64(TWO_PAGE_TEXT)}).json()
    assert doc["kind"] == "pdf" and doc["text_status"] == "OK"
    assert doc["title"] == "permit study" and doc["pages"] == 2
    assert doc["chars"] > 0
    row = h.db.execute("SELECT pdf_path FROM library_items WHERE id=?", (doc["id"],)).fetchone()
    stored = h.data / row["pdf_path"]
    assert stored.is_file() and stored.read_bytes().startswith(b"%PDF-")
    assert row["pdf_path"].startswith("library/"), "PDF bytes live in the data dir, not in SQLite"

    served = client.get(f"/library/docs/{doc['id']}/file")
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == build_pdf(TWO_PAGE_TEXT)
    # A note has no PDF, and a missing item is a 404 — never an empty "PDF".
    note = client.post("/library/notes", json={"project_id": pid, "text": "no file here"}).json()
    assert client.get(f"/library/docs/{note['id']}/file").status_code == 404
    assert client.get("/library/docs/li-missing/file").status_code == 404
    # With a project named, the file must belong to that project.
    assert client.get(f"/library/docs/{doc['id']}/file", params={"project_id": pid}).status_code == 200
    assert client.get(f"/library/docs/{doc['id']}/file", params={"project_id": "p-missing"}).status_code == 404


def test_pdf_upload_rejects_a_non_pdf_header(client, h):
    pid = seed(h)
    bad = base64.b64encode(b"PK\x03\x04 not a pdf at all").decode()
    r = client.post("/library/docs", json={"project_id": pid, "filename": "x.pdf", "pdf_base64": bad})
    assert r.status_code == 409 and "%PDF-" in r.json()["message"]
    assert h.db.execute("SELECT count(*) c FROM library_items").fetchone()["c"] == 0


def test_pdf_upload_rejects_oversize(client, h, monkeypatch):
    pid = seed(h)
    monkeypatch.setattr(library, "MAX_PDF_BYTES", 512)
    payload = base64.b64encode(b"%PDF-1.4" + b"x" * 600).decode()
    r = client.post("/library/docs", json={"project_id": pid, "filename": "big.pdf", "pdf_base64": payload})
    assert r.status_code == 409 and "40 MiB" in r.json()["message"]
    assert client.post("/library/docs", json={"project_id": pid, "filename": "x.pdf",
                                              "pdf_base64": "%%%"}).status_code == 409


def test_text_free_pdf_reports_no_text_layer(client, h):
    pid = seed(h)
    doc = client.post("/library/docs", json={"project_id": pid, "filename": "scan.pdf",
                                             "pdf_base64": pdf_b64(["", ""])}).json()
    assert doc["text_status"] == "NO_TEXT_LAYER"
    assert doc["chars"] == 0 and doc["pages"] == 2
    note = client.post("/library/notes", json={"project_id": pid, "text": "a readable note"}).json()
    listed = {i["id"]: i for i in client.get("/library/tree", params={"project_id": pid}).json()["items"]}
    # The tree keeps saying what the viewer needs: the scan cannot be searched or screened.
    assert listed[doc["id"]]["text_status"] == "NO_TEXT_LAYER"
    assert "text_status" not in listed[note["id"]], "a note has no text layer to report"
    # The viewer shows a note's body from the tree, so the body travels with it.
    assert listed[note["id"]]["text"] == "a readable note"
    assert "text" not in listed[doc["id"]], "a PDF's page text is not shipped in the tree"


def test_builtin_reader_keeps_pages_apart_when_pdftotext_is_absent(h, monkeypatch):
    """The fallback extractor is held to the same page contract, Flate included."""
    monkeypatch.setattr(library.shutil, "which", lambda name: None)
    pages, count = library.extract_pages(build_pdf(TWO_PAGE_TEXT, compress=True))
    assert count == 2
    assert "Cover page" in pages[0] and "Cover page" not in pages[1]
    assert "2.4 million" in pages[1]
    blank, blank_count = library.extract_pages(build_pdf(["", ""]))
    assert blank_count == 2 and not any(p.strip() for p in blank)


# ------------------------------------------------------------------ search
def test_search_reports_the_real_page_number(client, h):
    pid = seed(h)
    doc = client.post("/library/docs", json={"project_id": pid, "filename": "study.pdf",
                                             "pdf_base64": pdf_b64(TWO_PAGE_TEXT)}).json()
    hits = client.get("/library/search", params={"project_id": pid, "q": "Replacement COST"}).json()
    assert len(hits) == 1
    hit = hits[0]
    assert hit["item_id"] == doc["id"] and hit["page"] == 2
    assert "replacement cost estimate" in hit["snippet"].lower()

    first = client.get("/library/search", params={"project_id": pid, "q": "cover page"}).json()[0]
    assert first["page"] == 1

    note = client.post("/library/notes", json={"project_id": pid, "text": "a note about the cost"}).json()
    both = client.get("/library/search", params={"project_id": pid, "q": "cost"}).json()
    assert {h_["item_id"] for h_ in both} == {doc["id"], note["id"]}
    assert [x for x in both if x["item_id"] == note["id"]][0]["page"] is None
    assert client.get("/library/search", params={"project_id": pid, "q": "  "}).status_code == 409


# ------------------------------------------------------------------ screening (mocked)
class FakeBridge:
    """Stands in for `apple_ai._run`: no subprocess, no model, no network."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, system):
        self.calls.append((prompt, system))
        if isinstance(self.reply, str):
            return {"ok": True, "text": self.reply}
        return self.reply


def test_screen_drops_a_fabricated_quote(client, h, monkeypatch):
    pid = seed(h)
    note = client.post("/library/notes", json={"project_id": pid,
                                               "text": "The 2024 permit listed an estimated cost of 480000 dollars."}).json()
    fake = FakeBridge(json.dumps({
        "summary": "One relevant item.",
        "items": [
            {"item_id": note["id"], "relevance": "high",
             "quote": "estimated cost of 480000 dollars", "why": "States the cost."},
            {"item_id": note["id"], "relevance": "high",
             "quote": "the permit was approved by the mayor in 1987", "why": "Fabricated."},
        ]}))
    monkeypatch.setattr(apple_ai, "_run", fake)

    r = client.post("/library/screen", json={"project_id": pid, "item_ids": [note["id"]]})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["unverified_dropped"] == 1
    assert [x["quote"] for x in body["results"]] == ["estimated cost of 480000 dollars"]
    assert "480000" in fake.calls[0][0], "the prompt must carry the stored text"
    assert "verbatim" in fake.calls[0][1].lower()
    assert body["results"][0]["page"] is None, "a note has no page number to report"


def test_screen_reports_page_for_a_pdf_quote(client, h, monkeypatch):
    pid = seed(h)
    doc = client.post("/library/docs", json={"project_id": pid, "filename": "study.pdf",
                                             "pdf_base64": pdf_b64(TWO_PAGE_TEXT)}).json()
    monkeypatch.setattr(apple_ai, "_run", FakeBridge(json.dumps({"summary": "", "items": [
        {"item_id": doc["id"], "relevance": "medium",
         "quote": "replacement cost estimate of 2.4 million dollars", "why": "Cost figure."}]})))
    body = client.post("/library/screen", json={"project_id": pid, "item_ids": [doc["id"]]}).json()
    assert body["unverified_dropped"] == 0
    assert body["results"][0]["page"] == 2


def test_screen_unavailable_is_200_not_an_error(client, h, monkeypatch):
    pid = seed(h)
    note = client.post("/library/notes", json={"project_id": pid, "text": "some text"}).json()
    monkeypatch.setattr(apple_ai, "_run", FakeBridge({"ok": False, "error": "Apple on-device model: model not ready"}))
    r = client.post("/library/screen", json={"project_id": pid, "item_ids": [note["id"]]})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and "not ready" in body["reason"]
    assert body["results"] == []


def test_screen_of_a_scan_never_pretends_to_read_it(client, h, monkeypatch):
    pid = seed(h)
    doc = client.post("/library/docs", json={"project_id": pid, "filename": "scan.pdf",
                                             "pdf_base64": pdf_b64(["", ""])}).json()
    fake = FakeBridge({"ok": True, "text": '{"summary":"","items":[]}'})
    monkeypatch.setattr(apple_ai, "_run", fake)
    body = client.post("/library/screen", json={"project_id": pid, "item_ids": [doc["id"]]}).json()
    assert body["available"] is False and "NO_TEXT_LAYER" in body["reason"]
    assert fake.calls == [], "a text-free item must not be sent to the model at all"


def test_screen_drops_a_quote_attributed_to_the_wrong_item(client, h, monkeypatch):
    pid = seed(h)
    a = client.post("/library/notes", json={"project_id": pid, "text": "Alpha says the cost is 10."}).json()
    b = client.post("/library/notes", json={"project_id": pid, "text": "Beta says the cost is 20."}).json()
    monkeypatch.setattr(apple_ai, "_run", FakeBridge(json.dumps({"summary": "", "items": [
        {"item_id": a["id"], "quote": "Beta says the cost is 20.", "why": "cross-item"},
        {"item_id": "li-unknown", "quote": "Alpha says the cost is 10.", "why": "unknown item"}]})))
    body = client.post("/library/screen", json={"project_id": pid, "item_ids": [a["id"], b["id"]]}).json()
    assert body["results"] == []
    assert body["unverified_dropped"] == 1 and body["rejected_entries"] == 1


def test_items_stay_inside_their_project(client, h):
    here = seed(h, "fx")
    there = seed(h, "nyc")
    note = client.post("/library/notes", json={"project_id": here, "text": "private to fx"}).json()
    assert client.get("/library/tree", params={"project_id": there}).json()["items"] == []
    assert client.get("/library/search", params={"project_id": there, "q": "private"}).json() == []
    other_folder = folder(client, there, "nyc folder")
    moved = client.patch(f"/library/items/{note['id']}", json={"folder_id": other_folder["id"]})
    assert moved.status_code == 404, "a folder from another project cannot receive this item"
    assert client.post("/library/screen", json={"project_id": there, "item_ids": [note["id"]]}).status_code == 404


def test_available_reports_absence_without_raising(monkeypatch):
    """`available()` is the honest probe: no swiftc means no on-device model."""
    monkeypatch.setattr(apple_ai, "_available_cache", None)
    monkeypatch.setattr(apple_ai.shutil, "which", lambda name: None)
    assert apple_ai.available() is False


def test_screen_counts_items_above_the_batch_limit(client, h, monkeypatch):
    pid = seed(h)
    ids = [client.post("/library/notes", json={"project_id": pid, "text": f"note {n}"}).json()["id"]
           for n in range(3)]
    monkeypatch.setattr(apple_ai, "MAX_SCREEN_ITEMS", 2)
    fake = FakeBridge('{"summary":"","items":[]}')
    monkeypatch.setattr(apple_ai, "_run", fake)
    body = client.post("/library/screen", json={"project_id": pid, "item_ids": ids + [ids[0]]}).json()
    assert body["not_screened"] == [ids[2]]
    assert ids[2] not in fake.calls[0][0]


# ------------------------------------------------------------------ no cloud, ever
def test_library_modules_import_no_network_client():
    """Zero egress is structural: these modules have no HTTP/provider import at all."""
    for module in (library, apple_ai):
        source = Path(module.__file__).read_text()
        for forbidden in ("requests", "httpx", "urllib.request", "openai", "anthropic", "http.client"):
            assert forbidden not in source, f"{module.__name__} must not reference {forbidden}"


def test_screen_follows_the_existing_ai_origin_rule(client, h, monkeypatch):
    """The one library route that runs a model inherits the local-origin gate."""
    pid = seed(h)
    note = client.post("/library/notes", json={"project_id": pid, "text": "some text"}).json()
    monkeypatch.setattr(apple_ai, "_run", FakeBridge({"ok": False, "error": "unavailable"}))
    body = {"project_id": pid, "item_ids": [note["id"]]}
    assert client.post("/library/screen", json=body, headers={"origin": "http://localhost:5173"}).status_code == 200
    rejected = client.post("/library/screen", json=body, headers={"origin": "http://evil.example"})
    assert rejected.status_code == 403 and rejected.json()["error"] == "origin_rejected"
    # Read-only library routes stay open to any local page, as the existing routes are.
    assert client.get("/library/tree", params={"project_id": pid}).status_code == 200
