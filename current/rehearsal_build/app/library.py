"""Local research library: folders, notes, PDFs, literal search.

Everything here is local: no network module is imported, no provider is contacted.
PDF text comes from a real extractor (`pdftotext` when present, otherwise a
built-in stream reader); when a file has no text layer the honest answer is
`NO_TEXT_LAYER`, never a guessed reading.

Storage layout: bytes live on disk under `<data_dir>/library/<item_id>/<name>`;
SQLite holds metadata only. `body_text` keeps the pages separated by a form feed
(`\f`), so a page number in search results is the page the match is really on.

A results table (`kind='table'`) reuses that same row: `body_text` holds the
CSV exactly as it was submitted, `pages` holds its data row count and `chars`
its length. No schema change was needed. A table is deliberately not sent to
literal search or to the on-device screen: its cells are a numeric results
table (read through `load_table` and compared by `app/numeric_check.py`), not
prose, and a screen would ship whole CSV rows to a model.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import re
import shutil
import subprocess
import zlib
from datetime import datetime
from pathlib import Path

from . import list_order
from . import numeric_check
from . import store
from .harness import Conflict

MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_NOTE_CHARS = 200_000
MAX_TITLE_CHARS = 200
MAX_SEARCH_HITS = 200
SNIPPET_PAD = 60
PAGE_SEPARATOR = "\f"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
PDF_MAGIC = b"%PDF-"
TEXT_OK = "OK"
TEXT_ABSENT = "NO_TEXT_LAYER"

#: Bounds of a stored results table. They are checked before anything is
#: written, so an oversized upload never becomes a library item at all.
MAX_TABLE_BYTES = 200_000
MAX_TABLE_ROWS = 2_000
MAX_TABLE_COLUMNS = 50
TABLE_KIND = "table"
BOM = "\ufeff"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def today() -> str:
    """Local calendar day, used for the note day grouping."""
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- PDF text
def _simple_extract(data: bytes) -> list[str]:
    """Minimal fallback reader: the file's `/Contents` streams, decoded (Flate or
    raw), with the text operands of `Tj`/`TJ`/`'`/`"` collected per stream.

    Used only when `pdftotext` is not installed. It is deliberately narrow: it
    returns the text it can actually see, and an empty list when the page
    content carries no text operators (a scanned page).
    """
    content_refs = re.findall(rb"/Contents\s*(\[(?:[^\]]*)\]|\d+\s+\d+\s+R)", data)
    texts = []
    for ref in content_refs:
        chunks = []
        for number in re.findall(rb"(\d+)\s+\d+\s+R", ref):
            stream = _stream_of(data, int(number))
            if stream is not None:
                chunks.append(_text_of(stream.decode("latin-1")))
        texts.append("".join(chunks))
    return texts


def _stream_of(raw: bytes, number: int) -> bytes | None:
    found = re.search(rb"(?:^|[\s>])" + str(number).encode() + rb"\s+\d+\s+obj\b", raw)
    if found is None:
        return None
    start = raw.find(b"stream", found.end())
    if start < 0:
        return None
    start += len(b"stream")
    if raw[start:start + 2] == b"\r\n":
        start += 2
    elif raw[start:start + 1] in (b"\n", b"\r"):
        start += 1
    end = raw.find(b"endstream", start)
    if end < 0:
        return None
    body = raw[start:end]
    header = raw[found.end():start]
    if b"FlateDecode" in header:
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompressobj().decompress(body)
            except zlib.error:
                return None
    return body


def _text_of(content: str) -> str:
    out = []
    for token in re.finditer(r"\((?:\\.|[^\\()])*\)\s*(?:Tj|TJ|'|\")|\[(?:\((?:\\.|[^\\()])*\)|[^\]])*\]\s*TJ|\bT\*|\bTd|\bTD|\bET\b", content):
        piece = token.group(0)
        if piece.endswith("T*") or piece.endswith("Td") or piece.endswith("TD") or piece == "ET":
            out.append("\n")
            continue
        for s in re.finditer(r"\((?:\\.|[^\\()])*\)", piece):
            out.append(_unescape(s.group(0)[1:-1]))
    return "".join(out)


def _unescape(s: str) -> str:
    mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(s):
            break
        nxt = s[i]
        if nxt in mapping:
            out.append(mapping[nxt])
            i += 1
        elif nxt.isdigit():
            digits = s[i:i + 3]
            take = 0
            while take < len(digits) and digits[take].isdigit():
                take += 1
            out.append(chr(int(digits[:take], 8)))
            i += take
        else:
            out.append(nxt)
            i += 1
    return "".join(out)


def extract_pages(data: bytes) -> tuple[list[str], int]:
    """Text per page, plus the real page count.

    `[""]`-shaped results are legitimate: a page that exists but carries no text
    operators. The caller turns "all pages empty" into `NO_TEXT_LAYER`; the page
    count stays the file's own count so a viewer can still show it.
    """
    tool = shutil.which("pdftotext")
    if tool:
        try:
            done = subprocess.run([tool, "-q", "-enc", "UTF-8", "-", "-"], input=data,
                                  capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0:
            text = done.stdout.decode("utf-8", "replace")
            pages = text.split(PAGE_SEPARATOR)
            if len(pages) > 1 and not pages[-1].strip():
                pages.pop()  # pdftotext ends the last page with a separator
            return pages, len(pages)
    streams = _simple_extract(data)
    if streams:
        return streams, len(streams)
    # No extractor output at all: the page objects are still the honest count.
    count = len(re.findall(rb"/Type\s*/Page(?![s\w])", data)) or 0
    return [""] * count, count


# ---------------------------------------------------------------- storage helpers
def _project(h, project_id) -> str:
    if not isinstance(project_id, str) or not project_id:
        raise Conflict("project_id is required")
    if h.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
        raise KeyError("project not found")
    return project_id


def _folder(h, fid, project_id=None) -> dict:
    row = h.db.execute("SELECT * FROM library_folders WHERE id=?", (fid,)).fetchone()
    if row is None or (project_id is not None and row["project_id"] != project_id):
        raise KeyError("folder not found")
    return dict(row)


def _item(h, iid, project_id=None) -> dict:
    row = h.db.execute("SELECT * FROM library_items WHERE id=?", (iid,)).fetchone()
    if row is None or (project_id is not None and row["project_id"] != project_id):
        raise KeyError("item not found")
    return dict(row)


def _folder_public(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "parent_id": row["parent_id"],
            "created_at": row["created_at"]}


def _item_public(row: dict) -> dict:
    """List/item shape. Two additions on top of the bare metadata, both read by
    the existing library view: a PDF reports `text_status` (the same value the
    upload returned, so the tree can still say "no text layer"), and a note
    reports its own `text` — the note viewer has no other route to the body it
    must show. PDF page text is never included here; it comes from search.
    A results table reports neither: its `pages` is its data row count and its
    body is the submitted CSV, which the tree never ships."""
    item = {"id": row["id"], "folder_id": row["folder_id"], "kind": row["kind"], "title": row["title"],
            "day": row["day"], "pages": row["pages"], "chars": row["chars"],
            "created_at": row["created_at"]}
    if row["kind"] == "pdf":
        item["text_status"] = TEXT_OK if (row["chars"] or 0) > 0 else TEXT_ABSENT
    elif row["kind"] != TABLE_KIND:
        item["text"] = row["body_text"] or ""
    return item


def _clean_name(name, label="name") -> str:
    if not isinstance(name, str):
        raise Conflict(f"{label} must be text")
    value = name.strip()
    if not 1 <= len(value) <= MAX_TITLE_CHARS:
        raise Conflict(f"{label} must be 1 to {MAX_TITLE_CHARS} characters")
    return value


def _safe_filename(filename: str) -> str:
    base = Path(filename.replace("\\", "/")).name
    cleaned = SAFE_NAME.sub("_", base).strip("._-")
    if not cleaned:
        cleaned = "document"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned[:120]


# ---------------------------------------------------------------- folders
def tree(h, project_id: str) -> dict:
    pid = _project(h, project_id)
    folders = h.db.execute(
        "SELECT * FROM library_folders WHERE project_id=? ORDER BY created_at, rowid", (pid,)).fetchall()
    items = h.db.execute(
        "SELECT * FROM library_items WHERE project_id=? ORDER BY created_at DESC, rowid DESC", (pid,)).fetchall()
    return {"folders": [_folder_public(dict(r)) for r in folders],
            "items": _ordered_items(h, pid, [dict(r) for r in items])}


def _ordered_items(h, pid: str, rows: list[dict]) -> list[dict]:
    """PDFs in the researcher's saved order, filling the slots they already hold.

    Only the PDFs move: the listing keeps one created_at-ordered list, and the
    positions where it holds a PDF are refilled with the PDFs in saved order.
    A project whose PDF order was never saved keeps exactly today's newest-first
    listing, because the saved order of an unsaved list is empty; PDFs uploaded
    after a save follow the placed ones, and notes never change place or order.
    """
    saved = list_order.positions(h.db, list_order.PDF_LIST, pid)
    slots = [index for index, row in enumerate(rows) if row["kind"] == "pdf"]
    if not slots:
        return [_item_public(row) for row in rows]
    pdfs = list_order.ordered([rows[i] for i in slots], saved)
    ordered_rows = list(rows)
    for index, row in zip(slots, pdfs):
        ordered_rows[index] = row
    return [_item_public(row) for row in ordered_rows]


def save_order(h, body) -> dict:
    """Persist this project's PDF display order, then answer with the same tree
    GET /library/tree returns. The payload must name every current PDF of the
    project and nothing else: a note id, another project's item or a partial
    list is refused before a row is written, so no reorder is ever partial."""
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    if set(body) - {"project_id", "item_ids"}:
        raise Conflict("body accepts only project_id and item_ids")
    pid = _project(h, body.get("project_id"))
    ids = list_order.checked_ids(body, "item_ids", "item")
    # The saved order places PDFs. A results table is displayed among them, but
    # the frontend that builds this payload reads any non-note kind as a PDF, so
    # a table id may arrive here. It is tolerated and ignored: tables keep their
    # created_at position and the PDF-only completeness check still applies, so
    # the existing ordering semantics are unchanged.
    current = [r["id"] for r in h.db.execute(
        "SELECT id FROM library_items WHERE project_id=? AND kind='pdf' ORDER BY created_at DESC, rowid DESC",
        (pid,))]
    known = {r["id"] for r in h.db.execute(
        "SELECT id FROM library_items WHERE project_id=? AND kind=?", (pid, TABLE_KIND))}
    ids = [iid for iid in ids if iid not in known]
    list_order.require_complete(ids, current, "item_ids", "PDFs of this project")
    try:
        list_order.save(h.db, list_order.PDF_LIST, pid, ids)
        h.db.commit()
    except BaseException:
        h.db.rollback()
        raise
    return tree(h, pid)



def create_folder(h, project_id, name, parent_id=None) -> dict:
    pid = _project(h, project_id)
    label = _clean_name(name)
    if parent_id is not None:
        parent = _folder(h, parent_id, pid)
        parent_id = parent["id"]
    fid = store.new_id("lf")
    h.db.execute("INSERT INTO library_folders(id, project_id, parent_id, name, created_at) VALUES(?,?,?,?,?)",
                 (fid, pid, parent_id, label, store.now()))
    h.db.commit()
    return _folder_public(_folder(h, fid))


def _is_descendant(h, candidate_id: str, ancestor_id: str) -> bool:
    """True when `candidate_id` sits below `ancestor_id` in the folder tree."""
    seen = set()
    current = candidate_id
    while current is not None:
        if current == ancestor_id:
            return True
        if current in seen:
            return True  # an existing cycle would still be a rejection
        seen.add(current)
        row = h.db.execute("SELECT parent_id FROM library_folders WHERE id=?", (current,)).fetchone()
        if row is None:
            return False
        current = row["parent_id"]
    return False


def update_folder(h, fid: str, body) -> dict:
    folder = _folder(h, fid)
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"name", "parent_id"}
    if unknown:
        raise Conflict("body accepts only name and parent_id")
    if not body:
        raise Conflict("body must carry name or parent_id")
    name = folder["name"]
    parent_id = folder["parent_id"]
    if "name" in body:
        name = _clean_name(body["name"])
    if "parent_id" in body:
        new_parent = body["parent_id"]
        if new_parent is not None:
            if not isinstance(new_parent, str) or not new_parent:
                raise Conflict("parent_id must be a folder id or null")
            parent = _folder(h, new_parent, folder["project_id"])
            if _is_descendant(h, parent["id"], folder["id"]):
                raise Conflict("a folder cannot become its own descendant")
            new_parent = parent["id"]
        parent_id = new_parent
    h.db.execute("UPDATE library_folders SET name=?, parent_id=? WHERE id=?", (name, parent_id, fid))
    h.db.commit()
    return _folder_public(_folder(h, fid))


def delete_folder(h, fid: str) -> dict:
    """Removes the folder only: items fall back to the root, children move up."""
    folder = _folder(h, fid)
    items = h.db.execute("SELECT count(*) c FROM library_items WHERE folder_id=?", (fid,)).fetchone()["c"]
    children = h.db.execute("SELECT count(*) c FROM library_folders WHERE parent_id=?", (fid,)).fetchone()["c"]
    h.db.execute("UPDATE library_items SET folder_id=NULL WHERE folder_id=?", (fid,))
    h.db.execute("UPDATE library_folders SET parent_id=? WHERE parent_id=?", (folder["parent_id"], fid))
    h.db.execute("DELETE FROM library_folders WHERE id=?", (fid,))
    h.db.commit()
    return {"deleted": fid, "items_moved_to_root": items, "folders_reparented": children}


# ---------------------------------------------------------------- items
def create_note(h, body) -> dict:
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"project_id", "folder_id", "text", "title"}
    if unknown:
        raise Conflict("body accepts only project_id, folder_id, text and title")
    pid = _project(h, body.get("project_id"))
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise Conflict("text must be nonempty text")
    if len(text) > MAX_NOTE_CHARS:
        raise Conflict(f"text must be at most {MAX_NOTE_CHARS} characters")
    folder_id = body.get("folder_id")
    if folder_id is not None:
        folder_id = _folder(h, folder_id, pid)["id"]
    title = body.get("title")
    if title is None:
        first = text.strip().splitlines()[0].strip()
        title = first[:MAX_TITLE_CHARS] or "(untitled note)"
    else:
        title = _clean_name(title, "title")
    iid = store.new_id("li")
    day = today()
    h.db.execute(
        "INSERT INTO library_items(id, project_id, folder_id, kind, title, day, body_text, pdf_path, pages,"
        " chars, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (iid, pid, folder_id, "note", title, day, text, None, None, len(text), store.now()))
    h.db.commit()
    return _item_public(_item(h, iid))


def create_doc(h, body) -> dict:
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"project_id", "folder_id", "filename", "pdf_base64"}
    if unknown:
        raise Conflict("body accepts only project_id, folder_id, filename and pdf_base64")
    pid = _project(h, body.get("project_id"))
    folder_id = body.get("folder_id")
    if folder_id is not None:
        folder_id = _folder(h, folder_id, pid)["id"]
    filename = body.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise Conflict("filename is required")
    encoded = body.get("pdf_base64")
    if not isinstance(encoded, str) or not encoded:
        raise Conflict("pdf_base64 is required")
    if len(encoded) > (MAX_PDF_BYTES * 4) // 3 + 8:
        raise Conflict("PDF must be at most 40 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Conflict("pdf_base64 is not valid base64") from exc
    if len(data) > MAX_PDF_BYTES:
        raise Conflict("PDF must be at most 40 MiB")
    if not data.startswith(PDF_MAGIC):
        raise Conflict("file is not a PDF (%PDF- header missing)")
    pages, page_count = extract_pages(data)
    page_texts = [p.strip("\n") for p in pages]
    body_text = PAGE_SEPARATOR.join(page_texts)
    chars = sum(len(p) for p in page_texts)
    status = TEXT_OK if chars else TEXT_ABSENT
    iid = store.new_id("li")
    safe = _safe_filename(filename)
    folder = h.data / "library" / iid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe).write_bytes(data)
    stem = Path(filename.replace("\\", "/")).name
    stem = stem[:-4] if stem.lower().endswith(".pdf") else stem
    title = (stem.strip() or Path(safe).stem or safe)[:MAX_TITLE_CHARS]
    h.db.execute(
        "INSERT INTO library_items(id, project_id, folder_id, kind, title, day, body_text, pdf_path, pages,"
        " chars, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (iid, pid, folder_id, "pdf", title, today(), body_text if chars else "",
         str(Path("library") / iid / safe), len(page_texts), chars, store.now()))
    h.db.commit()
    item = _item(h, iid)
    return {**_item_public(item), "text_status": status}


# ---------------------------------------------------------------- results tables
def _table_shape(csv_text: str) -> dict:
    """Parse and validate a submitted CSV results table, or raise Conflict.

    `csv` from the standard library is the only parser, and it is given the
    text exactly as submitted (BOM stripped, CRLF and lone CR already folded to
    LF by the reader) so a quoted comma stays inside its cell. Every limit is
    checked here, before a row is written, and any cell a spreadsheet would
    evaluate (a leading `=` `+` `-` `@` that is not a plain number) rejects the
    whole table rather than being silently stored or silently stripped.
    """
    if not isinstance(csv_text, str):
        raise Conflict("csv_text must be text")
    text = csv_text.lstrip(BOM)
    if not text.strip():
        raise Conflict("csv_text is empty")
    if len(text.encode("utf-8")) > MAX_TABLE_BYTES:
        raise Conflict(f"csv_text must be at most {MAX_TABLE_BYTES // 1000} KB")
    if CONTROL.search(text):
        raise Conflict("csv_text carries binary control characters, not CSV")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        parsed = list(reader)
    except csv.Error as exc:
        raise Conflict(f"csv_text is not valid CSV: {exc}") from exc
    if not parsed:
        raise Conflict("csv_text has no header row")
    columns = [name.strip() for name in parsed[0]]
    if not all(columns):
        raise Conflict("every column needs a header")
    if len(columns) > MAX_TABLE_COLUMNS:
        raise Conflict(f"a table has at most {MAX_TABLE_COLUMNS} columns")
    if len(set(columns)) != len(columns):
        raise Conflict("two columns share a header")
    rows = []
    for number, raw in enumerate(parsed[1:], start=2):
        if not any(cell.strip() for cell in raw):
            continue  # a blank line is not a data row
        if len(raw) != len(columns):
            raise Conflict(f"data row {number} has {len(raw)} cells, the header has {len(columns)}")
        for column, cell in zip(columns, raw):
            if numeric_check.looks_like_formula(cell):
                raise Conflict(f"data row {number}, column {column}: formula cells are not accepted")
        rows.append(raw)
        # The row bound counts data rows only, so trailing blank lines in a
        # spreadsheet export cannot make a table too large. The byte bound above
        # is still the hard cap on raw input size.
        if len(rows) > MAX_TABLE_ROWS:
            raise Conflict(f"a table has at most {MAX_TABLE_ROWS} data rows")
    if not rows:
        raise Conflict("csv_text has a header but no data rows")
    if any(numeric_check.looks_like_formula(name) for name in columns):
        raise Conflict("a column header is a formula")
    return {"columns": columns, "rows": rows}


def create_table(h, project_id, body) -> dict:
    """Store a CSV results table as a library item of kind `table`.

    The row is an ordinary `library_items` row, so it lists, orders, moves and
    deletes with every other item; `body_text` carries the submitted CSV, which
    `load_table` turns back into columns and rows for the numeric check.
    """
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"title", "csv_text", "folder_id", "project_id"}
    if unknown:
        raise Conflict("body accepts only title, csv_text, folder_id and project_id")
    requested = body.get("project_id")
    if project_id is None:
        project_id = requested
    elif requested is not None and requested != project_id:
        raise Conflict("body names a different project_id than the route")
    pid = _project(h, project_id)
    title = _clean_name(body.get("title"), "title")
    folder_id = body.get("folder_id")
    if folder_id is not None:
        folder_id = _folder(h, folder_id, pid)["id"]
    csv_text = body.get("csv_text")
    shape = _table_shape(csv_text)
    iid = store.new_id("li")
    h.db.execute(
        "INSERT INTO library_items(id, project_id, folder_id, kind, title, day, body_text, pdf_path, pages,"
        " chars, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (iid, pid, folder_id, TABLE_KIND, title, today(), csv_text, None, len(shape["rows"]),
         len(csv_text), store.now()))
    h.db.commit()
    return _item_public(_item(h, iid))


def load_table(h, item_id, project_id=None) -> dict:
    """The stored table as `{item_id, title, columns, rows}`.

    Scoped to its own project when one is named: another project's item is a
    missing item, never a readable table. A note or a PDF is refused with a
    Conflict, because it holds no columns to compare a claim against.
    """
    pid = _project(h, project_id) if project_id is not None else None
    item = _item(h, item_id, pid)
    if item["kind"] != TABLE_KIND:
        raise Conflict("item is not a results table")
    shape = _table_shape(item["body_text"] or "")
    return {"item_id": item["id"], "title": item["title"],
            "columns": shape["columns"], "rows": shape["rows"]}


def update_item(h, iid: str, body) -> dict:
    item = _item(h, iid)
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"folder_id", "title"}
    if unknown:
        raise Conflict("body accepts only folder_id and title")
    if not body:
        raise Conflict("body must carry folder_id or title")
    folder_id = item["folder_id"]
    title = item["title"]
    if "folder_id" in body:
        new_folder = body["folder_id"]
        if new_folder is not None:
            if not isinstance(new_folder, str) or not new_folder:
                raise Conflict("folder_id must be a folder id or null")
            new_folder = _folder(h, new_folder, item["project_id"])["id"]
        folder_id = new_folder
    if "title" in body:
        title = _clean_name(body["title"], "title")
    h.db.execute("UPDATE library_items SET folder_id=?, title=? WHERE id=?", (folder_id, title, iid))
    h.db.commit()
    return _item_public(_item(h, iid))


def doc_file(h, iid: str, project_id=None) -> tuple[bytes, str]:
    item = _item(h, iid, _project(h, project_id) if project_id is not None else None)
    if item["kind"] != "pdf" or not item["pdf_path"]:
        raise KeyError("item has no PDF file")
    path = h.data / item["pdf_path"]
    if not path.is_file():
        raise KeyError("stored PDF is missing")
    return path.read_bytes(), path.name


# ---------------------------------------------------------------- search
def _pages_of(item: dict) -> list[str]:
    body = item["body_text"] or ""
    if item["kind"] != "pdf":
        return [body]
    return body.split(PAGE_SEPARATOR) if body else [""]


def _snippet(page_text: str, at: int, length: int) -> str:
    start = max(0, at - SNIPPET_PAD)
    end = min(len(page_text), at + length + SNIPPET_PAD)
    fragment = " ".join(page_text[start:end].split())
    return ("…" if start > 0 else "") + fragment + ("…" if end < len(page_text) else "")


def search(h, project_id, q) -> list[dict]:
    """Literal search over prose items only.

    A results table is skipped: its cells are read by `load_table` for the
    numeric check, and a prose snippet cut out of a CSV cell would tell a
    researcher nothing they could not read in the table itself.
    """
    pid = _project(h, project_id)
    if not isinstance(q, str) or not q.strip():
        raise Conflict("q must be nonempty text")
    needle = q.strip().lower()
    rows = h.db.execute(
        "SELECT * FROM library_items WHERE project_id=? AND body_text IS NOT NULL AND kind<>?"
        " ORDER BY created_at DESC, id", (pid, TABLE_KIND)).fetchall()
    hits = []
    for row in rows:
        item = dict(row)
        for index, page_text in enumerate(_pages_of(item)):
            lowered = page_text.lower()
            start = lowered.find(needle)
            while start >= 0:
                hits.append({"item_id": item["id"], "title": item["title"],
                             "page": index + 1 if item["kind"] == "pdf" else None,
                             "snippet": _snippet(page_text, start, len(needle))})
                if len(hits) >= MAX_SEARCH_HITS:
                    return hits
                start = lowered.find(needle, start + len(needle))
    return hits


# ---------------------------------------------------------------- screening
def screen(h, body) -> dict:
    """First-pass screen of prose items. A results table is refused up front:
    it is not prose, and screening it would send whole CSV rows to a model."""
    from . import apple_ai
    if not isinstance(body, dict):
        raise Conflict("body must be an object")
    unknown = set(body) - {"project_id", "item_ids"}
    if unknown:
        raise Conflict("body accepts only project_id and item_ids")
    pid = _project(h, body.get("project_id"))
    ids = body.get("item_ids")
    if not isinstance(ids, list) or not ids:
        raise Conflict("item_ids must be a nonempty list")
    if any(not isinstance(i, str) or not i for i in ids):
        raise Conflict("item_ids must be item ids")
    items = []
    for iid in ids:
        item = _item(h, iid, pid)
        if item["kind"] == TABLE_KIND:
            raise Conflict("a results table cannot be screened; it is compared, not read as prose")
        items.append({"item_id": item["id"], "title": item["title"], "kind": item["kind"],
                      "text": item["body_text"] or ""})
    return apple_ai.screen(items)
