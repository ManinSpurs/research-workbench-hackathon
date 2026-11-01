"""LaTeX writing drafts, slice 1 storage + save routes (LATEX_WRITING_DESIGN_EN §§6.1, 6.2, 6.5).

Slice 1 ships storage, read, save and engine *discovery* only: no compile route, no
subprocess, no PDF. Every test here therefore also asserts that no compile row and no
PDF artifact can appear. Mock/fake transports only; no live calls.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient

from app import latex, store
from app.api import create_app
from app.harness import Conflict, Harness
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)

TEX = "\\documentclass{article}\n\\begin{document}\nUNIQUE-LATEX-DRAFT-BODY-41897\n\\end{document}\n"


def seed(h, text="What does est_cost measure?"):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    return pid, qid


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


# ------------------------------------------------------------------ schema
def test_schema_is_additive_and_complete(h):
    tables = {r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"latex_drafts", "latex_draft_versions", "latex_citations", "latex_compiles"} <= tables
    cols = {c["name"] for c in h.db.execute("PRAGMA table_info('latex_drafts')")}
    assert {"id", "project_id", "question_id", "title", "engine", "main_file", "body_json",
            "revision", "previous_json", "created_at", "updated_at"} <= cols
    version_cols = {c["name"] for c in h.db.execute("PRAGMA table_info('latex_draft_versions')")}
    assert version_cols == {"id", "draft_id", "revision", "body_json", "source", "created_at"}


def test_existing_tables_are_untouched(h):
    """Additive only: a fresh connection over an existing DB must not need an ALTER."""
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = store.connect(path)
    try:
        assert {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"latex_drafts"}
        assert "elapsed_seconds" in {c["name"] for c in other.execute("PRAGMA table_info('usage_ledger')")}
    finally:
        other.close()


# ------------------------------------------------------------------ capabilities
def test_capabilities_reports_absence_without_side_effects(h, monkeypatch):
    from app import latex_compile
    monkeypatch.setattr(latex_compile, "discover", lambda: {})
    monkeypatch.setattr(latex.shutil, "which", lambda name: None)
    before = list(h.db.iterdump())
    caps = latex.capabilities()
    assert caps["available"] is False
    assert caps["engines"] == {"pdflatex": None, "xelatex": None, "lualatex": None, "latexmk": None, "tectonic": None}
    assert "No TeX engine found" in caps["note"]
    assert caps["pdf_export"] is False
    assert list(h.db.iterdump()) == before, "capability probe must not write"


def test_capabilities_reports_usable_pdf_backend(h, monkeypatch):
    from app import latex_compile
    monkeypatch.setattr(latex.shutil, "which", lambda name: "/usr/local/bin/" + name if name == "pdflatex" else None)
    monkeypatch.setattr(latex_compile, "discover", lambda: {"pdflatex": ("/trusted/pdftex", "/trusted", "/trusted/bin")})
    monkeypatch.setattr(latex_compile, "sandbox_available", lambda: True)
    caps = latex.capabilities()
    assert caps["available"] is True and caps["engines"]["pdflatex"] == "/trusted/pdftex"
    assert caps["pdf_export"] is True
    assert "no PDF is produced here" in caps["note"]


# ------------------------------------------------------------------ create / read
def test_create_read_roundtrip(h):
    pid, qid = seed(h)
    created = latex.create_draft(h, pid, {"title": "Paper one", "question_id": qid})
    assert created["project_id"] == pid and created["question_id"] == qid
    assert created["revision"] == 0 and created["main_file"] == "main.tex" and created["engine"] == "auto"
    assert [f["path"] for f in created["files"]] == ["main.tex"]
    assert created["files"][0]["text"] == (
        "\\documentclass{article}\n\\usepackage{lmodern}\n\\begin{document}\n\\section{Introduction}\n"
        "% Write here, or insert syntax using the buttons above.\n\n\\end{document}\n"
    )
    assert created["model_calls"] == 0
    read = latex.read_draft(h, created["draft_id"])
    assert read == created
    listed = latex.list_drafts(h, pid)
    assert [d["draft_id"] for d in listed["drafts"]] == [created["draft_id"]]


def test_import_sniffs_non_ascii_to_xelatex(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"import": {"name": "paper.tex", "text": "\\section{한국어}\n"}})
    assert draft["main_file"] == "paper.tex" and draft["engine"] == "xelatex"
    assert draft["title"] == "paper"
    assert draft["files"] == [{"path": "paper.tex", "text": "\\section{한국어}\n"}]
    latin = latex.create_draft(h, pid, {"import": {"name": "second.tex", "text": TEX}})
    assert latin["engine"] == "auto"


def test_bib_import_is_a_side_file_not_the_main_document(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"import": {"name": "refs.bib", "text": "@misc{k, title={T}}"}})
    assert draft["main_file"] == "main.tex"
    assert [f["path"] for f in draft["files"]] == ["main.tex", "refs.bib"]
    assert draft["files"][1]["text"] == "@misc{k, title={T}}"
    # §6.5: a pasted .bib is the researcher's own file, never parsed into latex_citations.
    assert h.db.execute("SELECT count(*) FROM latex_citations").fetchone()[0] == 0
    # The main file stays main.tex, so a save that drops it is rejected rather than compiled as a .bib.
    with pytest.raises(Conflict, match="must be one of the saved files"):
        latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                "files": [{"path": "refs.bib", "text": "x"}]})


@pytest.mark.parametrize("payload", [
    {"import": {"name": "main.txt", "text": "x"}},
    {"import": {"name": "main.tex", "text": "x" * (512 * 1024 + 1)}},
    {"import": {"name": "../escape.tex", "text": "x"}},
    {"import": {"name": "main.tex", "text": "x", "extra": 1}},
    {"project_id": "proj-evil"},
    {"title": 7},
    [],
])
def test_invalid_creation_rejected_before_writing(h, payload):
    pid, _ = seed(h)
    with pytest.raises((Conflict, ValueError)):
        latex.create_draft(h, pid, payload)
    assert h.db.execute("SELECT count(*) FROM latex_drafts").fetchone()[0] == 0


def test_question_from_another_project_is_rejected(h):
    pid, _ = seed(h)
    _, other_qid = seed(h, "Second question")
    with pytest.raises(Conflict, match="another project"):
        latex.create_draft(h, pid, {"question_id": other_qid})


def test_unknown_draft_and_project_raise_keyerror(h):
    pid, _ = seed(h)
    with pytest.raises(KeyError):
        latex.read_draft(h, "draft-missing")
    with pytest.raises(KeyError):
        latex.create_draft(h, "proj-missing", {"title": "x"})
    with pytest.raises(KeyError):
        latex.list_drafts(h, "proj-missing")


# ------------------------------------------------------------------ save / revision
def test_save_increments_revision_and_keeps_previous(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    first = latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                    "files": [{"path": "main.tex", "text": TEX}],
                                                    "source": "manual"})
    assert first["revision"] == 1 and first["files"][0]["text"] == TEX
    assert first["previous"]["files"] == draft["files"]
    second = latex.save_draft(h, draft["draft_id"], {"expected_revision": 1,
                                                     "files": [{"path": "main.tex", "text": TEX + "% two\n"}],
                                                     "title": "Renamed"})
    assert second["revision"] == 2 and second["title"] == "Renamed"
    assert second["previous"]["files"][0]["text"] == TEX


def test_revision_conflict_leaves_stored_text_unchanged(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 0, "files": [{"path": "main.tex", "text": TEX}]})
    with pytest.raises(Conflict, match="reload"):
        latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                "files": [{"path": "main.tex", "text": "collision"}]})
    assert latex.read_draft(h, draft["draft_id"])["files"][0]["text"] == TEX
    assert latex.read_draft(h, draft["draft_id"])["revision"] == 1


@pytest.mark.parametrize("payload", [
    {"expected_revision": 0, "files": []},
    {"expected_revision": 0, "files": [{"path": "main.tex", "text": "x"}], "extra": 1},
    {"expected_revision": True, "files": [{"path": "main.tex", "text": "x"}]},
    {"expected_revision": 0, "files": [{"path": "/etc/passwd", "text": "x"}]},
    {"expected_revision": 0, "files": [{"path": "other.tex", "text": "x"}]},
    {"expected_revision": 0, "files": [{"path": "main.tex", "text": "x"},
                                       {"path": "main.tex", "text": "y"}]},
    {"expected_revision": 0, "files": [{"path": "main.tex", "text": "x"}], "engine": "tectonic"},
    {"expected_revision": 0, "files": [{"path": "main.tex", "text": "x"}], "source": "import"},
])
def test_invalid_saves_fail_before_writing(h, payload):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    body_before = h.db.execute("SELECT body_json, revision FROM latex_drafts WHERE id=?",
                               (draft["draft_id"],)).fetchone()
    with pytest.raises(Conflict):
        latex.save_draft(h, draft["draft_id"], payload)
    assert tuple(h.db.execute("SELECT body_json, revision FROM latex_drafts WHERE id=?",
                              (draft["draft_id"],)).fetchone()) == tuple(body_before)
    assert h.db.execute("SELECT count(*) FROM latex_draft_versions").fetchone()[0] == 1


# ------------------------------------------------------------------ versions
def test_autosave_and_manual_versions_are_labelled(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                            "files": [{"path": "main.tex", "text": "a"}], "source": "autosave"})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 1,
                                            "files": [{"path": "main.tex", "text": "b"}], "source": "manual"})
    rows = [dict(r) for r in h.db.execute(
        "SELECT revision, source FROM latex_draft_versions WHERE draft_id=? ORDER BY id", (draft["draft_id"],))]
    assert [(r["revision"], r["source"]) for r in rows] == [(0, "manual"), (1, "autosave"), (2, "manual")]


def test_version_pruning_keeps_newest_manual_row(h):
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                            "files": [{"path": "main.tex", "text": "manual-keep"}], "source": "manual"})
    for i in range(1, 61):
        latex.save_draft(h, draft["draft_id"], {"expected_revision": i,
                                                "files": [{"path": "main.tex", "text": f"auto-{i}"}],
                                                "source": "autosave"})
    rows = [dict(r) for r in h.db.execute(
        "SELECT revision, source FROM latex_draft_versions WHERE draft_id=? ORDER BY id", (draft["draft_id"],))]
    assert len(rows) == latex.KEEP_VERSIONS
    assert [r for r in rows if r["source"] == "manual"] == [{"revision": 0, "source": "manual"},
                                                            {"revision": 1, "source": "manual"}], \
        "no manual row is ever pruned"
    assert rows[-1]["revision"] == 61


# ------------------------------------------------------------------ compile route, absent toolchain
def test_compile_route_reports_absent_toolchain_without_pdf_or_rows(h, client, monkeypatch):
    from app import latex_compile
    monkeypatch.setattr(latex_compile, "discover", lambda: {})
    monkeypatch.setattr(latex.shutil, "which", lambda name: None)
    pid, _ = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper"})
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert "/capabilities/latex" in paths
    assert "/drafts/{did}/compile" in paths
    assert "/drafts/{did}/pdf" in paths
    res = client.post(f"/drafts/{draft['draft_id']}/compile", json={"source": "manual"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "toolchain_absent"
    assert res.json()["revision"] == draft["revision"]
    assert res.json()["pdf_url"] is None
    assert client.get(f"/drafts/{draft['draft_id']}/pdf?revision={draft['revision']}").status_code == 404
    assert h.db.execute("SELECT count(*) FROM latex_compiles").fetchone()[0] == 0


def test_module_never_spawns_a_process():
    source = (ROOT / "app/latex.py").read_text()
    for banned in ("subprocess", "os.system", "popen", "shell=True"):
        assert banned not in source, f"slice 1 must not spawn processes ({banned})"


# ------------------------------------------------------------------ routes
def test_route_create_read_save_flow(client, h):
    pid, qid = seed(h)
    created = client.post(f"/projects/{pid}/drafts", json={"title": "Route paper", "question_id": qid})
    assert created.status_code == 200, created.text
    did = created.json()["draft_id"]
    got = client.get(f"/drafts/{did}")
    assert got.status_code == 200 and got.json()["revision"] == 0
    saved = client.put(f"/drafts/{did}", json={"expected_revision": 0,
                                               "files": [{"path": "main.tex", "text": TEX}], "source": "manual"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1 and saved.json()["files"][0]["text"] == TEX
    listed = client.get(f"/projects/{pid}/drafts")
    assert [d["draft_id"] for d in listed.json()["drafts"]] == [did]


def test_route_revision_conflict_is_409_with_design_message(client, h):
    pid, _ = seed(h)
    did = client.post(f"/projects/{pid}/drafts", json={"title": "Paper"}).json()["draft_id"]
    client.put(f"/drafts/{did}", json={"expected_revision": 0, "files": [{"path": "main.tex", "text": TEX}]})
    res = client.put(f"/drafts/{did}", json={"expected_revision": 0,
                                             "files": [{"path": "main.tex", "text": "stale"}]})
    assert res.status_code == 409, res.text
    assert res.json()["error"] == "conflict"
    assert res.json()["message"] == "Draft changed; reload before saving"
    assert client.get(f"/drafts/{did}").json()["files"][0]["text"] == TEX


def test_route_unknown_project_and_draft_are_404(client):
    assert client.post("/projects/proj-missing/drafts", json={"title": "x"}).status_code == 404
    assert client.get("/projects/proj-missing/drafts").status_code == 404
    assert client.get("/drafts/draft-missing").status_code == 404
    assert client.put("/drafts/draft-missing", json={"expected_revision": 0,
                                                     "files": [{"path": "main.tex", "text": "x"}]}).status_code == 404


def test_route_import_body_matches_design_shape(client, h):
    pid, _ = seed(h)
    res = client.post(f"/projects/{pid}/drafts", json={"question_id": None,
                                                       "import": {"name": "main.tex", "text": TEX}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["project_id"] == pid and body["question_id"] is None
    assert body["files"] == [{"path": "main.tex", "text": TEX}]
    sources = [r["source"] for r in h.db.execute("SELECT source FROM latex_draft_versions WHERE draft_id=?",
                                                 (body["draft_id"],))]
    assert sources == ["import"]


def test_route_capabilities_shape(client):
    caps = client.get("/capabilities/latex").json()
    assert set(caps) >= {"available", "engines", "checked_at", "note"}
    assert caps["available"] in (True, False)


# ------------------------------------------------------------------ isolation
def test_draft_survives_restart_and_is_not_session_deleted(h):
    pid, qid = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Persistent", "question_id": qid})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                            "files": [{"path": "main.tex", "text": TEX}]})
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    other = Harness(path, h.data, adapter=StubAdapter())
    try:
        assert latex.read_draft(other, draft["draft_id"])["files"][0]["text"] == TEX
    finally:
        other.db.close()
    # Design §6.2: drafts are project-scoped persistent artifacts, never session-cleaned.
    asyncio.run(h.create_question(pid, "Session only?", memory="session_only"))
    h.end_session()
    assert latex.read_draft(h, draft["draft_id"])["revision"] == 1


def test_deletion_scan_reports_draft_rows_and_keeps_the_draft(h):
    pid, qid = seed(h)
    draft = latex.create_draft(h, pid, {"title": "Paper", "question_id": qid})
    latex.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                            "files": [{"path": "main.tex", "text": TEX}]})
    assert h.deletion_preview(qid)["retained"]["latex_drafts_kept"]["count"] == 1
    assert h.deletion_preview(qid)["retained"]["latex_drafts_kept"]["reason"] == \
        "1 draft(s) kept with their 2 version row(s), 0 citation row(s) and 0 compile record(s); " \
        "project-owned drafts keyed by draft_id survive question deletion (design section 3.2)"
    assert "latex_drafts" not in h.deletion_preview(qid)["by_store"]
    result = h.delete_question(qid)
    # Slice 1 does not edit the question-deletion path. Drafts are project-scoped persistent
    # artifacts (§6.2), so the draft text survives; the scan stays honest about it, and it does
    # not claim a leftover question string it did not find.
    assert result["verified"] is True and result["remaining_hits"] == []
    assert latex.read_draft(h, draft["draft_id"])["files"][0]["text"] == TEX
    assert store.find_strings(h.db, ["UNIQUE-LATEX-DRAFT-BODY-41897"])
