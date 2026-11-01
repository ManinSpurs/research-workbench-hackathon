"""The conversation a chip carries as ``conversation.txt``: talk, decisions and evidence.

A chip is the researcher's notebook for one topic, and the notebook was missing the part
that explains it: what was asked of the secretary, what the secretary answered, which
decision each question reached and what evidence stood behind it. ``record.json`` holds
the captured objects; this module writes the readable log beside them, so a person who
opens the ``.chip`` file in any editor can follow the reasoning without the app.

Nothing here is a chat transcript fed back into a model. ``compose`` only reads what the
app already stored (``secretary_turns``, ``questions``, ``decisions``, ``evidence``) and
lays it out as text. It makes no model call, opens no socket, writes no file and invents
no entry: a question with no recorded decision is absent from the decisions section, and a
decision whose reason was never recorded says exactly ``Reason not recorded``.

The output is deterministic, so the same records always produce the same bytes and two
exports of one chip cannot drift apart. It is capped at ``chips.MAX_CONVERSATION_BYTES``:
rather than hand back text the chip reader would refuse, the log is cut at a line
boundary and the final line says so and names the cap.
"""
from __future__ import annotations

import json

from . import chips, store

HEADER_RULE = "=" * 72
SECTION_RULE = "-" * 72
NOTHING_RECORDED = "Nothing has been recorded for this chip yet."
NO_REASON = "Reason not recorded"


# ------------------------------------------------------------------ reading
def _turns(h, project_id: str, pack_id: str | None) -> list:
    """The turns this log prints, oldest first, plus the count it left out.

    Read straight from the table rather than through ``list_turns``: that reader is a
    review list (newest first, one page of at most ``MAX_LIMIT`` turns), and a log that
    silently dropped its earliest exchanges would misrepresent the record.

    A turn the person set aside (``discarded``) is not printed. Discarding is the person's
    own statement that the turn is not worth keeping, so printing it into the chip would
    overrule them; the count is reported instead, and the turn stays in the review list
    until it is deleted.
    """
    clauses = ["project_id=?"]
    args: list = [project_id]
    if pack_id is not None:
        clauses.append("(pack_id=? OR pack_id IS NULL)")
        args.append(pack_id)
    rows = h.db.execute("SELECT * FROM secretary_turns WHERE " + " AND ".join(clauses) +
                        " ORDER BY created_at, rowid", args).fetchall()
    kept, set_aside = [], 0
    for row in rows:
        if row["status"] == "discarded":
            set_aside += 1
            continue
        kept.append({"id": row["id"], "role": row["role"], "created_at": row["created_at"],
                     "question_id": row["question_id"], "status": row["status"],
                     "body": json.loads(row["body_json"])})
    return kept, set_aside


def _body_text(payload) -> str:
    """Every string in a recorded body, in a fixed order, one per line."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        pieces = [value for value in payload.values() if isinstance(value, str)]
        return "\n".join(pieces)
    if isinstance(payload, list):
        return "\n".join(value for value in payload if isinstance(value, str))
    return ""


def _questions(h, project_id: str, pack_id: str | None) -> list:
    """The questions asked in this project, oldest first, narrowed to one chip when named."""
    if pack_id is None:
        rows = h.db.execute("SELECT * FROM questions WHERE project_id=? ORDER BY rowid",
                            (project_id,)).fetchall()
    else:
        rows = h.db.execute(
            "SELECT q.* FROM questions q JOIN question_contexts c ON c.question_id=q.id"
            " WHERE q.project_id=? AND c.pack_id=? ORDER BY q.rowid",
            (project_id, pack_id)).fetchall()
    return [dict(row) for row in rows]


def _latest_decision(h, qid: str):
    return h.db.execute("SELECT * FROM decisions WHERE question_id=? ORDER BY rowid DESC LIMIT 1",
                        (qid,)).fetchone()


def _evidence(h, qid: str) -> list:
    return [dict(row) for row in h.db.execute(
        "SELECT * FROM evidence WHERE question_id=? ORDER BY rowid", (qid,)).fetchall()]


def _question_text(question: dict) -> str:
    return (question.get("working_text") or question.get("original_text") or "").strip()


def _project_name(h, project_id: str) -> str:
    row = h.db.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    return row["name"] if row is not None else project_id


# ------------------------------------------------------------------ writing
def _header(h, project_id: str, pack_id: str | None) -> list:
    chip = pack_id or "no chip named (the project's own)"
    return [HEADER_RULE,
            "Secretary conversation record",
            f"Chip: {chip}",
            f"Project: {_project_name(h, project_id)} ({project_id})",
            "Compiled from the app's own records: the recorded secretary turns, the decision",
            "each question reached and the evidence attached to it. Nothing here is inferred;",
            "the log states only what was stored, and the time of each turn is the time it was",
            "recorded.",
            HEADER_RULE, ""]


def _conversation_section(turns: list, questions: dict, set_aside: int) -> list:
    lines = ["CONVERSATION", SECTION_RULE]
    if not turns:
        lines.append("No secretary turn has been recorded for this chip.")
    for turn in turns:
        lines.append(f"[{turn['created_at']}] {turn['role']}")
        question_id = turn.get("question_id")
        if question_id and question_id in questions:
            lines.append(f"question: {questions[question_id]}")
        text = _body_text(turn.get("body") or {}).strip()
        lines.append(text if text else "(empty text)")
        lines.append("")
    if set_aside:
        # Named, not hidden: a reader comparing this log with the review list must know
        # why a turn they remember is missing here.
        lines.append(f"{set_aside} turn(s) were set aside and are not printed in this record.")
        lines.append("")
    return lines


def _decisions_section(h, ordered: list) -> list:
    lines = ["DECISIONS", SECTION_RULE]
    written = False
    for question in ordered:
        decision = _latest_decision(h, question["id"])
        if decision is None:
            continue
        written = True
        reason = (decision["reason"] or "").strip()
        lines.append(f"question: {_question_text(question)}")
        lines.append(f"verdict: {decision['verdict']}")
        lines.append(f"reason: {reason if reason else NO_REASON}")
        lines.append(f"decided_at: {decision['created_at']}")
        lines.append("")
    if not written:
        lines.append("No decision has been recorded for a question in this chip.")
    return lines + [""]


def _evidence_section(h, ordered: list) -> list:
    lines = ["EVIDENCE", SECTION_RULE]
    written = False
    for question in ordered:
        rows = _evidence(h, question["id"])
        if not rows:
            continue
        written = True
        lines.append(f"question: {_question_text(question)}")
        for row in rows:
            location = (row.get("location") or "").strip()
            source = (row.get("source_id") or "").strip()
            lines.append(f"- source: {source}" if source else "- source: not recorded")
            if location:
                lines.append(f"  location: {location}")
            excerpt = (row.get("excerpt") or "").strip()
            lines.append(f"  excerpt: {excerpt}" if excerpt else "  excerpt: not recorded")
        lines.append("")
    if not written:
        lines.append("No evidence has been attached to a question in this chip.")
    return lines + [""]


def _fit(lines: list, cap: int) -> str:
    """The composed text, cut at a line boundary when it would pass ``cap`` bytes.

    The cap is applied to the encoded bytes, which is exactly what the chip reader caps.
    The truncation note names the limit, so a reader knows the end of the log is missing
    rather than running out of content.
    """
    text = "\n".join(lines)
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text
    note = (f"\n\n[This log was truncated at the {chips.size_label(cap)} chip conversation limit;"
            " the records in the app still hold the full history.]")
    budget = cap - len(note.encode("utf-8"))
    if budget < 0:
        raise chips.Invalid("The chip has too little room for an honest conversation truncation notice")
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line.encode("utf-8")) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept).rstrip() + note


def compose(h, project_id: str, pack_id: str | None = None, *, cap: int | None = None) -> str:
    """The whole conversation log for one chip and project, as plain UTF-8 text.

    ``pack_id`` narrows the log to one chip. With no chip named the project's own records
    are written, which is what a project that predates chips needs. An unknown project is
    a ``KeyError``, exactly like every other route that names one.
    """
    h.project(project_id)
    cap = chips.MAX_CONVERSATION_BYTES if cap is None else min(cap, chips.MAX_CONVERSATION_BYTES)
    turns, set_aside = _turns(h, project_id, pack_id)
    ordered = _questions(h, project_id, pack_id)
    by_id = {question["id"]: _question_text(question) for question in ordered}
    lines = _header(h, project_id, pack_id)
    lines += _conversation_section(turns, by_id, set_aside)
    if not turns and not ordered:
        lines.append(NOTHING_RECORDED)
        return _fit(lines, cap)
    lines += _decisions_section(h, ordered)
    lines += _evidence_section(h, ordered)
    return _fit(lines, cap)


def compose_and_store(h, project_id: str, pack_id: str, chip_store=None) -> dict:
    """Compose the log for one installed chip and write it into that chip's file.

    This is the one call that makes the txt exist inside the ``.chip``: it reads what the
    app stored, composes the text and hands it to ``ChipStore.write_conversation``, which
    validates the cap and rewrites the archive keeping its manifest, art and record. A
    built-in chip is refused by the store, so composing never rewrites a shipped pack.
    """
    store_ = chip_store if chip_store is not None else chips.ChipStore(h.data)
    text = compose(h, project_id, pack_id, cap=store_.conversation_budget(pack_id))
    written = store_.write_conversation(pack_id, text)
    return {"pack_id": pack_id, "project_id": project_id, "text": text,
            "bytes": written["bytes"], "model_calls": 0, "now": store.now(),
            "stored": True}


def store_on_export(h, pack_id: str, chip_store=None) -> list[dict]:
    """Write this chip's log into it, for every project that is working under it.

    Taking a chip out is the moment its notebook has to be complete: the file leaves the
    app and whatever it does not carry is not recoverable from inside it. So the export
    path calls this first, and the bytes it hands out already hold the talk.

    One chip can be the pack of more than one project, and a chip holds one
    ``conversation.txt``. Writing it once per project would leave only the last project's
    log, silently discarding the others, so this returns one entry per project and writes
    only when there is exactly one: with several the caller must say which project's
    notebook the file is, rather than have one picked for them. A built-in chip is
    refused by the store, which is why a shipped pack is never rewritten.
    """
    store_ = chip_store if chip_store is not None else chips.ChipStore(h.data)
    projects = [row["id"] for row in h.db.execute(
        "SELECT id FROM projects WHERE pack_id=? ORDER BY created_at", (pack_id,))]
    if len(projects) == 1:
        return [compose_and_store(h, projects[0], pack_id, store_)]
    return [{"pack_id": pack_id, "project_id": project_id, "text": None,
             "bytes": None, "model_calls": 0, "now": store.now(),
             "stored": False, "projects": len(projects)} for project_id in projects]
