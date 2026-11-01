"""Explicit, offline notebook restoration. Chip text is never an instruction."""
import base64
import hashlib
import json
import copy
import sqlite3

from . import chips, library, store
from .harness import Conflict
from .packs import get_pack


class _ImportConnection:
    """Keep native helper commits inside the import owner's transaction."""
    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    def commit(self):
        pass


async def import_notebook(h, pack_id, body):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    if not path:
        raise Conflict("Chip import requires a file-backed project database")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    # Skip Harness startup/session recovery: this is a private write scope, not
    # an app restart. No connection or mutable registration is shared with callers.
    worker = copy.copy(h)
    worker.db = _ImportConnection(db)
    worker.specs = copy.deepcopy(h.specs)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            result = await _import_notebook(worker, pack_id, body)
            project = worker.project(result["project_id"])
            db.commit()
        except BaseException:
            db.rollback()
            raise
        h.specs[project["kind"]] = {
            "name": project["name"], "files": json.loads(project["files_json"]),
            "release": None, "literature": []}
        return result
    finally:
        db.close()


async def _import_notebook(h, pack_id, body):
    if body != {"memory": "remember"}:
        raise chips.Invalid('Import requires exactly {"memory":"remember"}; it saves local project memory only.')
    cs = chips.ChipStore(h.data)
    raw = base64.b64decode(cs.export(pack_id)["base64"])
    record = chips.read_chip_record(raw)
    conversation = chips.read_chip_conversation(raw)
    identity = get_pack(pack_id).identity()
    digest = hashlib.sha256(raw).hexdigest()
    record_digest = hashlib.sha256(chips.record_bytes(record)).hexdigest()
    provenance = f"imported from chip {pack_id}, sha256 {digest}"
    # The marker lives in project-owned events, so native project deletion removes it.
    newer_record = False
    for row in h.db.execute(
            "SELECT e.project_id, e.payload FROM events e JOIN projects p ON p.id=e.project_id"
            " WHERE e.kind='chip_import_completed'"):
        payload = json.loads(row["payload"])
        if payload.get("pack_id") != pack_id:
            continue
        newer_record = True
        if payload.get("record_sha256") == record_digest:
            h.require_chip_scope(row["project_id"], pack_id)
            return {**payload, "project_id": row["project_id"], "created": False, "model_calls": 0}
    ids = [q.question_id for q in record.questions]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        raise chips.Invalid("Imported questions need unique nonempty question_id values")
    texts = []
    for q in record.questions:
        if ((q.pack_version and q.pack_version != identity["pack_version"]) or
                (q.manifest_sha256 and q.manifest_sha256 != identity["manifest_sha256"])):
            raise chips.Invalid("Record question has a different pinned pack identity")
        text = q.working_text or q.text
        if not text.strip() or len(text) > 20000:
            raise chips.Invalid("Imported question must be nonempty and at most 20000 characters")
        texts.append(text)
    for answer in record.answers:
        if answer.question_id not in ids:
            raise chips.Invalid("Record answer names an unknown question")
    checklist = [f"Checklist: {x.key}: {x.value}" for x in record.harness]
    if any(len(x) > 20000 for x in checklist):
        raise chips.Invalid("Imported checklist entry exceeds question limit")
    note = provenance + "\n\n" + json.dumps(record.model_dump(), ensure_ascii=False) + "\n\n" + conversation
    if len(note) > library.MAX_NOTE_CHARS:
        raise chips.Invalid("Imported notebook exceeds project note limit")
    # No await here except capture-only writes (which never invoke an adapter).
    # All validation precedes creation; no pre-existing project is a write target.
    # A notebook has no observational dataset. Avoid manufacturing upload files.
    kind = store.new_id("custom")
    h.specs[kind] = {"name": f"Imported: {get_pack(pack_id).title}"[:120],
                     "files": [], "release": None, "literature": []}
    p = h.register_project(kind, pack_id=pack_id)
    pid = p["project_id"]
    library.create_note(h, {"project_id": pid, "title": "Imported chip notebook", "text": note})
    for index, text in enumerate(texts):
        q = await h.create_question(pid, text, pack_id=pack_id, capture_only=True)
        qid = q["question_id"]
        store.log_event(h.db, pid, qid, "chip_row_imported", {"provenance": provenance})
        decided = False
        for a in record.answers:
            if a.question_id == ids[index] and a.kind in ("adopt", "defer", "reject"):
                h.decide(pid, qid, a.kind, a.value, None)
                decided = True
        if not decided:
            h.ask_clarification(qid, None, {
                "gap": text, "affected": "Continuation of the imported research question",
                "why_matters": "Researcher input is required before continuing this question.",
                "branches": [], "independent_work": "Review the saved notebook while waiting.",
                "gap_kind": "preference"})
    if checklist:
        store.log_event(h.db, pid, None, "project_checklist_recorded",
                        {"context": identity, "items": checklist})
    result = {"pack_id": pack_id, "sha256": digest, "provenance": provenance,
              "record_sha256": record_digest, "newer_record": newer_record,
              "question_count": len(texts), "checklist_count": len(checklist)}
    store.log_event(h.db, pid, None, "chip_import_completed", result)
    h.db.commit()
    return {**result, "project_id": pid, "created": True, "model_calls": 0}
