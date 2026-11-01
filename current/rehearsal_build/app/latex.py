"""Project-scoped saved LaTeX sources; local compilation lives in latex_compile."""
import json
import re
import shutil
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from . import store

MAX_IMPORT_BYTES = 512 * 1024
IMPORT_EXTENSIONS = (".tex", ".bib", ".latex")
ENGINES = ("auto", "pdflatex", "xelatex", "lualatex")
TOOLCHAIN = ("pdflatex", "xelatex", "lualatex", "latexmk", "tectonic")
KEEP_VERSIONS = 50
SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_SOURCE = r"""\documentclass{article}
\usepackage{lmodern}
\begin{document}
\section{Introduction}
% Write here, or insert syntax using the buttons above.

\end{document}
"""

NOTICE = ("Drafts are stored on this machine only and belong to the project. "
          "The newest 50 autosave steps are kept; older steps are discarded. "
          "New papers start with an app-authored LaTeX template; imported text is preserved.")


class DraftFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: Annotated[str, Field(min_length=1, max_length=200)]
    text: Annotated[str, Field(max_length=MAX_IMPORT_BYTES)]


class DraftImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    name: Annotated[str, Field(min_length=1, max_length=120)]
    text: Annotated[str, Field(max_length=MAX_IMPORT_BYTES)]


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    title: Annotated[str, Field(max_length=200)] | None = None
    question_id: Annotated[str, Field(max_length=200)] | None = None
    import_: DraftImport | None = Field(default=None, alias="import")


class DraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: Annotated[int, Field(ge=0)]
    files: Annotated[list[DraftFile], Field(min_length=1, max_length=10)]
    title: Annotated[str, Field(max_length=200)] | None = None
    engine: Annotated[str, Field(max_length=20)] | None = None
    source: Annotated[str, Field(max_length=20)] = "manual"


def sanitize_rel_path(path: str) -> str:
    """Relative draft-internal path only: no absolute paths, no `..`, no empty segments."""
    from .harness import Conflict
    if not isinstance(path, str) or not path or path != path.strip():
        raise Conflict("A file path must be nonempty text without surrounding spaces")
    if path.startswith("/") or "\\" in path or ":" in path:
        raise Conflict("A file path must be relative to the draft, not an absolute or device path")
    segments = path.split("/")
    if len(segments) > 4:
        raise Conflict("A file path may have at most 4 segments")
    for segment in segments:
        if segment in ("", ".", "..") or not SAFE_SEGMENT.fullmatch(segment):
            raise Conflict("A file path may contain only letters, digits, dot, dash and underscore")
    return path


def _body(files, sections=None) -> str:
    return json.dumps({"files": [{"path": f["path"], "text": f["text"]} for f in files],
                       "sections": list(sections or [])}, ensure_ascii=False)


def _parse_body(raw: str) -> dict:
    body = json.loads(raw)
    return {"files": body.get("files") or [], "sections": body.get("sections") or []}


def _draft_payload(h, row, previous) -> dict:
    body = _parse_body(row["body_json"])
    # The question link is optional metadata. If the question was deleted, say so instead of
    # silently presenting a dangling reference (the draft itself is project-scoped, §3.2/§6.2).
    question_missing = False
    if row["question_id"] is not None:
        question_missing = h.db.execute("SELECT 1 FROM questions WHERE id=?",
                                       (row["question_id"],)).fetchone() is None
    return {"draft_id": row["id"], "project_id": row["project_id"], "question_id": row["question_id"],
            "question_missing": question_missing,
            "title": row["title"], "engine": row["engine"], "main_file": row["main_file"],
            "revision": row["revision"], "files": body["files"], "sections": body["sections"],
            "previous": previous, "created_at": row["created_at"], "updated_at": row["updated_at"],
            "model_calls": 0, "notice": NOTICE}


def _row(h, did):
    row = h.db.execute("SELECT * FROM latex_drafts WHERE id=?", (did,)).fetchone()
    if row is None:
        raise KeyError("draft not found")
    return row


def read_draft(h, did: str) -> dict:
    row = _row(h, did)
    previous = _parse_body(row["previous_json"]) if row["previous_json"] else None
    return _draft_payload(h, row, previous)


def list_drafts(h, pid: str) -> dict:
    h.project(pid)  # KeyError -> 404 for unknown projects
    rows = h.db.execute("SELECT * FROM latex_drafts WHERE project_id=? ORDER BY created_at, id", (pid,)).fetchall()
    return {"project_id": pid, "drafts": [{"draft_id": r["id"], "question_id": r["question_id"], "title": r["title"],
                                          "engine": r["engine"], "revision": r["revision"],
                                          "updated_at": r["updated_at"]} for r in rows], "model_calls": 0}


def create_draft(h, pid: str, payload) -> dict:
    from .harness import Conflict
    h.project(pid)  # KeyError -> 404 for unknown projects
    try:
        create = DraftCreate.model_validate(payload)
    except ValueError as exc:
        raise Conflict("Invalid draft creation: expected optional title, question_id and import{name,text}") from exc
    if create.question_id is not None:
        q = h.question(create.question_id)  # KeyError -> 404
        if q["project_id"] != pid:
            raise Conflict("The question belongs to another project")
    title = (create.title or "").strip()
    engine = "auto"
    main_file = "main.tex"
    files = [{"path": main_file, "text": DEFAULT_SOURCE}]
    source = "manual"
    if create.import_ is not None:
        name = create.import_.name.strip()
        if not name.lower().endswith(IMPORT_EXTENSIONS):
            raise Conflict("An imported file must end in .tex, .latex or .bib")
        if len(create.import_.text.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise Conflict("An imported file must be at most 512 KiB")
        imported = sanitize_rel_path(name)
        source = "import"
        if imported.lower().endswith(".bib"):
            # §6.5: a pasted .bib becomes a file of the draft and is never parsed into
            # latex_citations. It cannot be the main file, because it is not a document.
            if imported == main_file:
                raise Conflict("A .bib file cannot be the main document")
            files = [files[0], {"path": imported, "text": create.import_.text}]
            title = title or imported.rsplit(".", 1)[0]
        else:
            main_file = imported
            files = [{"path": main_file, "text": create.import_.text}]
            # Design §6.5: non-ASCII content cannot be set by pdflatex, so the engine defaults to xelatex.
            engine = "xelatex" if any(ord(c) > 127 for c in create.import_.text) else "auto"
            title = title or main_file.rsplit(".", 1)[0]
    title = title or "Untitled paper"
    now = store.now()
    did = store.new_id("draft")
    body = _body(files)
    h.db.execute("INSERT INTO latex_drafts(id, project_id, question_id, title, engine, main_file, body_json,"
                 " revision, previous_json, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (did, pid, create.question_id, title, engine, main_file, body, 0, None, now, now))
    h.db.execute("INSERT INTO latex_draft_versions(draft_id, revision, body_json, source, created_at)"
                 " VALUES(?,?,?,?,?)", (did, 0, body, source, now))
    h.db.commit()
    return read_draft(h, did)


def _prune(versions: list, drop: int) -> list:
    """Oldest-first autosaves are pruned first; the newest manual and import rows are never pruned."""
    newest_manual = max((v["id"] for v in versions if v["source"] == "manual"), default=None)
    newest_import = max((v["id"] for v in versions if v["source"] == "import"), default=None)
    kept = []
    for v in versions:  # oldest first
        if drop <= 0:
            kept.append(v)
        elif v["source"] == "autosave":
            drop -= 1
        else:
            kept.append(v)
    if drop > 0:  # not enough autosaves; drop the oldest non-protected rows
        survivors = []
        for v in kept:
            if drop > 0 and v["id"] not in (newest_manual, newest_import):
                drop -= 1
            else:
                survivors.append(v)
        kept = survivors
    return kept


def save_draft(h, did: str, payload) -> dict:
    from .harness import Conflict
    try:
        edit = DraftSave.model_validate(payload)
    except ValueError as exc:
        raise Conflict("Invalid draft save: provide expected_revision, files[{path,text}] and optional title/engine") from exc
    if edit.source not in ("autosave", "manual"):
        raise Conflict("source must be autosave or manual")
    if edit.engine is not None and edit.engine not in ENGINES:
        raise Conflict("engine must be auto, pdflatex, xelatex or lualatex")
    row = _row(h, did)
    if row["revision"] != edit.expected_revision:
        raise Conflict("Draft changed; reload before saving")
    paths = [sanitize_rel_path(f.path) for f in edit.files]
    if len(set(paths)) != len(paths):
        raise Conflict("A draft cannot hold two files with the same path")
    if row["main_file"] not in paths:
        raise Conflict(f"The main file {row['main_file']} must be one of the saved files")
    # NOTE: no .bib main-file check here. main_file is set structurally -- always
    # "main.tex" (:131) unless a non-.bib import renames it (:150); the .bib import
    # branch never reassigns main_file, so a .bib main_file is unreachable in
    # save_draft. The former guard here was dead code; API behaviour is unchanged.
    # (The `imported == main_file` check in create_draft is itself unfireable for
    # the same reason; left in place deliberately -- see review report.)
    for f in edit.files:
        if len(f.text.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise Conflict("A draft file must be at most 512 KiB")
    title = (edit.title.strip() if isinstance(edit.title, str) else "") or row["title"]
    engine = edit.engine or row["engine"]
    body = _body([{"path": f.path, "text": f.text} for f in edit.files],
                 _parse_body(row["body_json"])["sections"])
    now = store.now()
    revision = row["revision"] + 1
    # No await between the revision check and the write. One backend worker owns this DB.
    h.db.execute("UPDATE latex_drafts SET revision=?, body_json=?, previous_json=?, title=?, engine=?, updated_at=?"
                 " WHERE id=?", (revision, body, row["body_json"], title, engine, now, did))
    h.db.execute("INSERT INTO latex_draft_versions(draft_id, revision, body_json, source, created_at)"
                 " VALUES(?,?,?,?,?)", (did, revision, body, edit.source, now))
    versions = [dict(v) for v in h.db.execute(
        "SELECT id, source FROM latex_draft_versions WHERE draft_id=? ORDER BY id", (did,))]
    stale = _prune(versions, len(versions) - KEEP_VERSIONS)
    keep = {v["id"] for v in stale}
    for v in versions:
        if v["id"] not in keep:
            h.db.execute("DELETE FROM latex_draft_versions WHERE id=?", (v["id"],))
    h.db.commit()
    return read_draft(h, did)


def capabilities() -> dict:
    """Read-only discovery on every request; no installation, process or writes."""
    found = {name: shutil.which(name) for name in TOOLCHAIN}
    from .latex_compile import discover, sandbox_available, ENGINES as COMPILE_ENGINES
    trusted = discover()
    for name in COMPILE_ENGINES:
        if name in trusted:
            found[name] = trusted[name][0]
    available = any(name in trusted for name in COMPILE_ENGINES)
    note = ("No TeX engine found in an enabled trusted root. PATH discovery alone does not enable compilation."
            if not available else
            "A supported TeX engine is present. Compilation requires the macOS OS sandbox; "
            "no PDF is produced here by this capability probe.")
    return {"available": available,
            # A path is what was observed; the version line is unknown until an engine runs.
            "engines": {name: (path if path else None) for name, path in found.items()},
            "checked_at": store.now(), "note": note,
            "pdf_export": available and sandbox_available(),
            "cjk_note": "Korean compilation is not enabled: XeLaTeX helpers are blocked and kotex is not bundled."}
