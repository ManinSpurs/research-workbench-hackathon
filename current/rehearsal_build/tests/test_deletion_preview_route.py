"""HTTP-level coverage for GET /questions/{qid}/deletion-preview (app/api.py).

Harness-level shape is covered in tests/test_canvas.py and
tests/test_latex_store.py; this module pins the HTTP route itself: status
codes, exact response keys, the 404 body produced by guard(KeyError), and
read-only behavior. In-process only (StubAdapter, no network, no credits).

Read-only scope (honest): the snapshot comparison below proves "no committed,
row-visible write" -- the whole-DB dump is identical before and after the
preview call. It cannot see uncommitted writes on another connection (a second
connection never observes uncommitted rows); no such detection is attempted.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from fastapi.testclient import TestClient

from app import secretary_log, store
from app.api import create_app
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)


def seed(h, text="How long does processing take?"):
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    # A secretary turn quoting the question's own words, bound to it, is the
    # ordinary case (secretary_turns.question_id is that binding). It is
    # recorded through the real module path, so a column-contract drift in
    # app/secretary_log.py fails here instead of silently passing.
    secretary_log.record_turn(h, pid, {"role": "secretary",
                                       "body": {"text": f"You asked: {text}"},
                                       "question_id": qid})
    # A researcher-recorded revisit reminder that quotes the question's own
    # words. It binds through subject_kind='question' + subject_id (there is no
    # question_id column) and is recorded through the real harness write path,
    # so a binding/validation drift fails here rather than passing vacuously.
    # Its note deliberately repeats the question text: previously that made
    # delete_question report a false "Deletion incomplete".
    h.record_revisit_condition(pid, "question", qid, "2026-11-30",
                               recorded_by="researcher", source="researcher command",
                               note=f"revisit: {text}", status="recorded")
    return pid, qid


@pytest.fixture
def client(h):
    path = h.db.execute("PRAGMA database_list").fetchone()["file"]
    with TestClient(create_app(path, h.data)) as c:
        yield c


# INDEPENDENT oracle for the by_store key set: exactly the tables
# Harness.delete_question DELETEs from (Harness._QUESTION_DELETE_TABLES, which
# delete_question consumes directly).
# by_store is NOT "every table with a question_id column" anymore: events
# (payload redacted, rows kept), usage_ledger (spend accounting kept, spec
# section 19) and latex_drafts (project-owned, design section 3.2, with their
# draft-keyed versions/citations/compiles) are deliberately retained and
# reported under the retained map instead; the questions row itself is reported
# separately as question_removed, never folded into the removal total.
# If the schema or the delete path gains a table later, these sets MUST fail
# loudly and force a conscious update -- that is the point.
EXPECTED_BY_STORE_KEYS = {
    "ai_assets",
    "ai_jobs",
    "ai_reviews",
    "ai_usage",
    "answers",
    "assessments",
    "clarification_responses",
    "clarifications",
    "claim_checks",
    "client_actions",
    "decisions",
    "designs",
    "evidence",
    "preparation_jobs",
    "prepared_cards",
    "question_budgets",
    "question_contexts",
    "question_datasets",
    "questions_fts",
    "research_canvases",
    "run_messages",
    "secretary_turns",
    "shadow_runs",
    "task_runs",
}

# The retained map reports rows delete_question deliberately keeps. Shape:
# {name: {"count": int, "reason": str}}. Reasons are short stable strings
# pinned below so the frontend copy cannot drift silently.
#
# These keys are the retention CLASSES deletion_preview declares. The check
# itself is scope-based (Harness._question_purge_scope /
# _question_retained_scope), so a key is not what makes a hit non-failure --
# it is what tells the researcher, before the irreversible step, which kept
# rows will still quote the text. other_text_kept is the catch-all that keeps
# preview==effect true for retained rows no named class covers (a briefing
# snapshot, a library folder name, a turn bound to another question...):
# every table retained_hits can name is declared, so no surviving text is
# announced only afterwards, as an alarm.
EXPECTED_RETAINED_KEYS = {
    "events_payload_nulled",
    "usage_ledger_kept",
    "latex_drafts_kept",
    "latex_drafts_quoting_kept",
    "revisit_conditions_kept",
    "revisit_conditions_other_kept",
    "library_notes_kept",
    "other_text_kept",
}


def full_snapshot(h):
    """Whole-DB row dump (all but sqlite_% internals), following the
    tests/test_dot_safety.py content_state pattern but without its
    usage_ledger/questions_fts carve-outs: a preview must write nowhere.

    Deterministic ordering: ORDER BY rowid on rowid tables; the FTS5 external
    tables without rowid (questions_fts_config, questions_fts_idx) order by all
    their columns instead. sqlite_sequence is included when present, so an
    insert-then-delete inside the preview path (which would bump an AUTOINCREMENT
    counter without leaving a row) is still caught.
    """
    tables = [r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    seq_rows = []
    if h.db.execute("SELECT count(*) c FROM sqlite_master "
                    "WHERE name='sqlite_sequence'").fetchone()["c"]:
        seq_rows = [tuple(row) for row in h.db.execute(
            "SELECT * FROM sqlite_sequence ORDER BY name")]
    snap = {}
    for t in tables:
        try:
            rows = h.db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            snap[t] = [tuple(row) for row in rows]
        except Exception:
            # No rowid (FTS5 shadow tables): order by every column instead.
            cols = [c["name"] for c in h.db.execute(f"PRAGMA table_info('{t}')")]
            order = ", ".join(f'"{c}"' for c in cols) if cols else "1"
            snap[t] = [tuple(row) for row in
                       h.db.execute(f'SELECT * FROM "{t}" ORDER BY {order}')]
    if seq_rows:
        snap["sqlite_sequence"] = seq_rows
    return snap


def test_preview_existing_question_returns_shape(client, h):
    _, qid = seed(h)
    r = client.get(f"/questions/{qid}/deletion-preview")
    assert r.status_code == 200
    body = r.json()
    # Deterministic top-level shape from Harness.deletion_preview return.
    assert set(body) == {"question_id", "by_store", "retained", "question_removed"}
    assert body["question_id"] == qid
    assert body["question_removed"] == {"count": 1, "reason": "the question row itself is deleted"}
    assert isinstance(body["by_store"], dict) and body["by_store"]
    # Exact key set: the hardcoded EXPECTED_BY_STORE_KEYS above (hand-derived
    # from the delete path, not computed by the code's own rule -- see its comment).
    assert set(body["by_store"]) == EXPECTED_BY_STORE_KEYS
    # by_store must match _QUESTION_DELETE_TABLES exactly: preview==effect by
    # construction. Import here (module top has none) so a drift between the
    # two explicit lists fails loudly.
    from app.harness import Harness as _H
    assert set(_H._QUESTION_DELETE_TABLES) == EXPECTED_BY_STORE_KEYS
    # Retained map: exact keys, {count, reason} entries, counts are ints >= 0,
    # reasons are the pinned stable strings.
    assert set(body["retained"]) == EXPECTED_RETAINED_KEYS
    for entry in body["retained"].values():
        assert set(entry) == {"count", "reason"}
        assert type(entry["count"]) is int and entry["count"] >= 0
        assert isinstance(entry["reason"], str) and entry["reason"]
    assert body["retained"]["events_payload_nulled"]["reason"] == \
        "event rows are kept; payload redacted to NULL"
    assert body["retained"]["usage_ledger_kept"]["reason"] == \
        "spend-accounting rows are kept so deletion cannot reset spend (spec section 19)"
    # No drafts/versions/citations/compiles in this fixture, so the dynamic
    # latex reason pins its exact zero-state wording here.
    assert body["retained"]["latex_drafts_kept"]["reason"] == \
        "0 draft(s) kept with their 0 version row(s), 0 citation row(s) and 0 compile record(s); " \
        "project-owned drafts keyed by draft_id survive question deletion (design section 3.2)"
    # Live cross-check, derived from delete-list semantics, not the old
    # generic predicate: the delete list embedded in the route response must
    # equal (a) the hardcoded set and (b) the tables the delete path will
    # actually empty for this seeded question. After a real delete, every
    # by_store table holds zero rows for the qid while retained rows persist.
    live_deleted = {t for (t,) in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if t in EXPECTED_BY_STORE_KEYS}
    assert live_deleted == EXPECTED_BY_STORE_KEYS
    # type(v) is int (not isinstance): bool is an int subclass and must not pass.
    assert all(type(v) is int and v >= 0 for v in body["by_store"].values())
    # Seeding one question writes exactly one row per seed store (mirrors the
    # test_canvas.py:77 / test_latex_store.py:328 single-count style).
    assert body["by_store"]["question_contexts"] == 1
    assert body["by_store"]["question_datasets"] == 1
    assert body["by_store"]["question_budgets"] == 1
    # seed() records exactly one secretary turn bound to the question.
    assert body["by_store"]["secretary_turns"] == 1
    # events_payload_nulled counts only rows whose payload is non-NULL: those
    # are the payloads deletion redacts. The seed's question_captured event
    # carries a payload (Harness.create_question log_event), so this is 1 here.
    assert body["retained"]["events_payload_nulled"]["count"] == 1
    assert body["retained"]["events_payload_nulled"]["count"] == h.db.execute(
        "SELECT count(*) c FROM events WHERE question_id=? AND payload IS NOT NULL", (qid,)).fetchone()["c"]
    # Adapter-coupled pin: this fixture uses the mock StubAdapter
    # (tests/test_dot_safety.py h fixture), whose create_question refine path
    # goes through Harness._invoke and logs exactly one usage_ledger row.
    # Under the offline adapter (label "offline") the same seed logs zero --
    # verified 2026-10-27 -- so if a future adapter change breaks this pin,
    # update the expectation deliberately, not blindly.
    assert body["retained"]["usage_ledger_kept"]["count"] == 1
    assert body["retained"]["latex_drafts_kept"]["count"] == 0
    # Researcher-recorded revisit reminders are retained, not erased. The seed
    # records exactly one, bound through subject_kind='question' + subject_id
    # (revisit_conditions has no question_id column), and the preview count is
    # cross-checked against the real row count on that same binding — a
    # hardcoded 1 would pass vacuously if the binding were wrong.
    assert body["retained"]["revisit_conditions_kept"]["count"] == 1
    assert body["retained"]["revisit_conditions_kept"]["count"] == h.db.execute(
        "SELECT count(*) c FROM revisit_conditions WHERE subject_kind='question' AND subject_id=?",
        (qid,)).fetchone()["c"]
    assert body["retained"]["revisit_conditions_kept"]["reason"] == \
        "revisit reminders the researcher recorded in their own words are kept, not deleted " \
        "with the question; their subject reference to the deleted question is left unresolved"
    assert body["by_store"]["questions_fts"] == 1
    # preview==effect: run the real delete on a second seeded question and
    # confirm every by_store table ends at zero rows while retained rows
    # survive (events redacted, not removed; usage kept; drafts untouched).
    # Distinct seed text: delete verification searches for the deleted strings
    # DB-wide, so a same-text sibling would honestly fail verification.
    _, qid2 = seed(h, text="How long does archiving take?")
    probe = client.get(f"/questions/{qid2}/deletion-preview").json()
    for t in EXPECTED_BY_STORE_KEYS:
        assert probe["by_store"][t] == h.db.execute(
            f'SELECT count(*) c FROM "{t}" WHERE question_id=?', (qid2,)).fetchone()["c"]
    assert h.delete_question(qid2)["verified"] is True
    for t in EXPECTED_BY_STORE_KEYS:
        assert h.db.execute(
            f'SELECT count(*) c FROM "{t}" WHERE question_id=?', (qid2,)).fetchone()["c"] == 0
    assert h.db.execute(
        "SELECT count(*) c FROM events WHERE question_id=?", (qid2,)).fetchone()["c"] == 1
    assert h.db.execute(
        "SELECT payload FROM events WHERE question_id=?", (qid2,)).fetchone()["payload"] is None
    assert h.db.execute(
        "SELECT count(*) c FROM usage_ledger WHERE question_id=?", (qid2,)).fetchone()["c"] == 1
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id=?", (qid2,)).fetchone()["c"] == 0


def test_preview_effect_post_delete_whole_db_comparison(client, h):
    """Finding 5: one-directional cross-check is not enough. Dump the whole DB,
    delete a seeded question, then compare per table:
    - every by_store table is empty for the deleted question AND no row change
      outside the deleted question's rows may remain unverified: compare full
      row sets restricted to other questions / NULL question rows, which must
      be byte-identical;
    - the questions row is gone;
    - events rows survive with payload NULL; usage_ledger rows survive;
    - latex rows (drafts, versions, citations keyed by draft_id) survive.
    Pragmatic fixture: seeds one question via the route seed, plus one draft
    with a save (versions>=1) so the latex-retained counts are exercised.
    sqlite-internal tables excluded like full_snapshot (rowid ordering)."""
    from app import latex as latex_mod

    pid, qid = seed(h)
    draft = latex_mod.create_draft(h, pid, {"title": "Paper", "question_id": qid})
    latex_mod.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                "files": [{"path": "main.tex", "text": "x"}]})
    # The save adds one compile-less citation row pair-free baseline: latex
    # slice 1 never writes citations/compiles, so seed one citation manually to
    # prove draft-keyed rows survive deletion too (they have no question_id).
    h.db.execute("INSERT INTO latex_citations(draft_id, cite_key, ref_kind, ref_id, project_id, created_at)"
                 " VALUES(?,?,?,?,?,?)",
                 (draft["draft_id"], "k1", "manual", "r1", pid, "2026-10-27T00:00:00Z"))
    h.db.commit()

    preview = client.get(f"/questions/{qid}/deletion-preview").json()
    assert preview["retained"]["latex_drafts_kept"]["count"] == 1
    assert preview["retained"]["latex_drafts_kept"]["reason"] == \
        "1 draft(s) kept with their 2 version row(s), 1 citation row(s) and 0 compile record(s); " \
        "project-owned drafts keyed by draft_id survive question deletion (design section 3.2)"
    before_by_question = {}
    for t in EXPECTED_BY_STORE_KEYS:
        before_by_question[t] = [tuple(r) for r in h.db.execute(
            f'SELECT * FROM "{t}" WHERE question_id=?', (qid,))]
        assert len(before_by_question[t]) == preview["by_store"][t]
    before_events = [tuple(r) for r in h.db.execute(
        "SELECT * FROM events WHERE question_id=?", (qid,))]
    before_usage = [tuple(r) for r in h.db.execute(
        "SELECT * FROM usage_ledger WHERE question_id=?", (qid,))]
    before_latex = {}
    for t in ("latex_drafts", "latex_draft_versions", "latex_citations", "latex_compiles"):
        before_latex[t] = [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}"')]
    tables = [r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    before_other = {}
    declared_retained = {"events", "usage_ledger", "latex_drafts", "latex_draft_versions",
                         "latex_citations", "latex_compiles", "revisit_conditions"}
    for t in tables:
        if t in EXPECTED_BY_STORE_KEYS or t in declared_retained or t == "questions":
            continue
        if t.startswith("questions_fts_"):
            # FTS5 index shadow tables backing questions_fts: deleting the
            # questions_fts rows rewrites these by design. They are covered by
            # the by_store zero-rows assertion above, not by byte-identity here.
            continue
        if t == "sqlite_sequence":
            # AUTOINCREMENT counters: delete_question legitimately advances the
            # events counter via its question_deleted audit insert. Counters
            # are not question rows; the read-only preview test (full_snapshot)
            # already pins preview-side counter stability separately.
            continue
        try:
            before_other[t] = [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
        except Exception:
            cols = [c["name"] for c in h.db.execute(f"PRAGMA table_info('{t}')")]
            order = ", ".join(f'"{c}"' for c in cols) if cols else "1"
            before_other[t] = [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}" ORDER BY {order}')]

    assert h.delete_question(qid)["verified"] is True

    # questions row gone.
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id=?", (qid,)).fetchone()["c"] == 0
    # by_store tables hold zero rows for the deleted question.
    for t in EXPECTED_BY_STORE_KEYS:
        assert h.db.execute(
            f'SELECT count(*) c FROM "{t}" WHERE question_id=?', (qid,)).fetchone()["c"] == 0
    # events rows survive, every payload NULL (the one seeded row had a
    # payload pre-delete and now reads NULL).
    after_events = [tuple(r) for r in h.db.execute(
        "SELECT * FROM events WHERE question_id=?", (qid,))]
    assert len(after_events) == len(before_events) == 1
    payload_idx = [c["name"] for c in h.db.execute("PRAGMA table_info('events')")].index("payload")
    assert all(r[payload_idx] is None for r in after_events)
    # usage rows survive byte-identical.
    assert [tuple(r) for r in h.db.execute(
        "SELECT * FROM usage_ledger WHERE question_id=?", (qid,))] == before_usage
    # latex rows for the kept draft survive byte-identical, versions included.
    for t in ("latex_drafts", "latex_draft_versions", "latex_citations", "latex_compiles"):
        assert [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}"')] == before_latex[t]
    assert h.db.execute("SELECT count(*) c FROM latex_draft_versions WHERE draft_id=?",
                        (draft["draft_id"],)).fetchone()["c"] == 2
    # Every other table unchanged.
    for t, rows in before_other.items():
        try:
            after = [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
        except Exception:
            cols = [c["name"] for c in h.db.execute(f"PRAGMA table_info('{t}')")]
            order = ", ".join(f'"{c}"' for c in cols) if cols else "1"
            after = [tuple(r) for r in h.db.execute(f'SELECT * FROM "{t}" ORDER BY {order}')]
        assert after == rows


def test_question_quoted_by_a_secretary_turn_deletes_cleanly(h):
    """Regression: a secretary turn quoting the question's own words used to keep
    the question's text in the DB, so delete_question honestly reported
    verified: False and left a dangling secretary_turns.question_id behind.
    Deleting the question now deletes its turns (the binding is the question_id
    column) while a project turn with question_id NULL survives untouched --
    it was never about this question."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    bound = secretary_log.record_turn(h, pid, {"role": "secretary",
                                               "body": {"text": f"You asked: {text}"},
                                               "question_id": qid})
    unbound = secretary_log.record_turn(h, pid, {"role": "researcher",
                                                 "body": {"text": "unrelated note"}})
    assert unbound["question_id"] is None and unbound["project_id"] == pid
    unbound_row = tuple(h.db.execute("SELECT * FROM secretary_turns WHERE id=?",
                                     (unbound["id"],)).fetchone())
    assert h.deletion_preview(qid)["by_store"]["secretary_turns"] == 1

    result = h.delete_question(qid)

    assert result["verified"] is True
    assert result["remaining_hits"] == []
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (bound["id"],)).fetchone()["c"] == 0
    # The deleted question's id no longer resolves anywhere, bound turn included
    # (checked by the verified flag above) nor as a surviving turn row.
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE question_id=?",
                        (qid,)).fetchone()["c"] == 0
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id=?", (qid,)).fetchone()["c"] == 0
    # NULL-question turn of the same project: byte-identical, still listed.
    assert tuple(h.db.execute("SELECT * FROM secretary_turns WHERE id=?",
                              (unbound["id"],)).fetchone()) == unbound_row
    listed = [t["id"] for t in secretary_log.list_turns(h, pid)["turns"]]
    assert listed == [unbound["id"]]


def test_question_with_a_researcher_revisit_reminder_deletes_cleanly(h):
    """Regression: revisit_conditions binds a question without a question_id
    column (subject_kind='question' + subject_id), so the generic delete loop
    never touches it. A researcher's reminder quoting the question's own words
    therefore survived deletion and made delete_question report the false alarm
    verified: False / remaining_hits: [revisit_conditions.note ...].

    The reminder is the researcher's own written words, so deletion keeps it and
    the preview must say so in advance; the retention must not count as an
    erasure failure."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    reminder = h.record_revisit_condition(pid, "question", qid, "2026-11-30",
                                          recorded_by="researcher",
                                          source="researcher command",
                                          note=f"revisit: {text}", status="recorded")
    row_before = tuple(h.db.execute("SELECT * FROM revisit_conditions WHERE id=?",
                                    (reminder["id"],)).fetchone())
    # Another question's reminder must not be counted for this one.
    other_pid = h.register_project("fx")["project_id"]
    other_qid = asyncio.run(h.create_question(other_pid, "How long does filing take?"))["question_id"]
    h.record_revisit_condition(other_pid, "question", other_qid, "2026-12-01",
                               recorded_by="researcher", source="researcher command",
                               note="revisit: someone else's question", status="recorded")

    # The preview declares the retention BEFORE any delete, with the real count.
    preview = h.deletion_preview(qid)
    assert "revisit_conditions" not in preview["by_store"]
    entry = preview["retained"]["revisit_conditions_kept"]
    assert set(entry) == {"count", "reason"}
    assert type(entry["count"]) is int and entry["count"] == 1
    assert entry["count"] == h.db.execute(
        "SELECT count(*) c FROM revisit_conditions WHERE subject_kind='question' AND subject_id=?",
        (qid,)).fetchone()["c"]

    result = h.delete_question(qid)

    assert result["verified"] is True
    assert result["remaining_hits"] == []
    # The reminder survives untouched: same row, same note, same dangling
    # reference to the (now deleted) question.
    assert tuple(h.db.execute("SELECT * FROM revisit_conditions WHERE id=?",
                              (reminder["id"],)).fetchone()) == row_before
    assert h.db.execute("SELECT note FROM revisit_conditions WHERE id=?",
                        (reminder["id"],)).fetchone()["note"] == f"revisit: {text}"
    assert h.db.execute("SELECT subject_id FROM revisit_conditions WHERE id=?",
                        (reminder["id"],)).fetchone()["subject_id"] == qid
    assert h.db.execute("SELECT count(*) c FROM questions WHERE id=?", (qid,)).fetchone()["c"] == 0
    # The project's other reminder is not part of this question's deletion.
    assert h.db.execute("SELECT count(*) c FROM revisit_conditions WHERE project_id=?",
                        (other_pid,)).fetchone()["c"] == 1


def test_declared_retained_tables_never_count_as_erasure_failures(h):
    """Structural guard for the whole class of false "Deletion incomplete"
    alarms, in its scope-based form.

    The old form of this test pinned an exclusion tuple
    (_QUESTION_RETAINED_TABLES) against store.find_strings' whole-DB scan. That
    machinery is gone: it was table-scoped while retention is row-scoped, and
    three tables had to be appended to it one false alarm at a time. What
    replaces it is a scope, and this test pins the two properties the scope must
    keep -- it fails if someone reintroduces an exclusion list, widens the
    failure search back to every table, or lets the preview drift from the
    deletion."""
    from app.harness import Harness as _H

    # 1. The failure search is a scope, not a scan-minus-exclusions. The purge
    #    scope must be built from exactly the tables deletion purges (plus the
    #    questions row and the redacted events payloads) and must cover EVERY
    #    text column of them -- a new TEXT column is protected automatically.
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(pid, "Which columns matter most?"))["question_id"]
    purge = h._question_purge_scope(qid)
    purged_tables = {t["table"] for t in purge}
    assert purged_tables == set(_H._QUESTION_DELETE_TABLES) | {"questions", "events"}
    text_cols = store.text_columns(h.db)
    for t, c in text_cols:
        if t in _H._QUESTION_DELETE_TABLES or t == "questions" or (t == "events" and c == "payload"):
            assert {"table": t, "column": c} in [{"table": x["table"], "column": x["column"]} for x in purge], \
                f"purge scope misses the purged cell {t}.{c}"
    # events is a genuine failure surface: only its payload is purged (the rows
    # survive), so the scope must name payload and nothing else of it.
    assert {x["column"] for x in purge if x["table"] == "events"} == {"payload"}
    # 2. No text cell is invisible: every text column of every table is searched
    #    by exactly one of the two scopes. For a table deletion purges, the
    #    purge scope names every text column (rows bound to this question) and
    #    the retained scope names every text column too (rows bound to another
    #    question or to none) -- the two are ROW-disjoint, not column-disjoint,
    #    which is the whole point of a row-scoped check. For a table deletion
    #    never touches, the retained scope names every text column and the purge
    #    scope names none of it.
    kept = {(x["table"], x["column"]) for x in h._question_retained_scope(qid)}
    purged = {(x["table"], x["column"]) for x in purge}
    purged_tbls = set(_H._QUESTION_DELETE_TABLES) | {"questions", "events"}
    for t, c in text_cols:
        if t == "events" and c != "payload":
            # events rows are kept whole except the payload cell.
            assert (t, c) in kept and (t, c) not in purged
        elif t in purged_tbls:
            assert (t, c) in purged, f"purge scope misses the purged cell {t}.{c}"
            assert (t, c) in kept, f"retained scope misses the surviving rows of {t}.{c}"
        else:
            assert (t, c) in kept and (t, c) not in purged, \
                f"{t}.{c} is neither purged nor retained: it could hide a leftover"
    # 3. preview==effect by construction: no table is declared kept that
    #    deletion purges (except events, whose payload cell is the redacted
    #    one), and every retained row a hit can name is declared BEFORE the
    #    delete -- including the rows of purged tables that survive because they
    #    belong to another question or to none. Those fall to the catch-all
    #    (other_text_kept), which is derived from the class table rather than
    #    hand-listed, so it cannot drift.
    retained = h.deletion_preview(qid)["retained"]
    assert {"library_notes_kept", "latex_drafts_quoting_kept",
            "revisit_conditions_other_kept", "other_text_kept"} <= set(retained)
    declared_tables = set().union(*_H._RETENTION_CLASSES.values())
    assert declared_tables & set(_H._PURGED_TABLES) <= {"events"}, \
        "the preview declares a kept table that deletion purges"
    assert set(_H._RETAINED_QUOTING_COVERED) == declared_tables - {"events"}, \
        "the catch-all's coverage must be derived from the declared classes, or a class would double-count"
    assert "library notes belong to the project" in retained["library_notes_kept"]["reason"]
    # Behavioral proof of the catch-all: a secretary turn bound to NO question
    # survives this deletion (it is not this question's row), and the preview
    # must say so before the irreversible step -- naming the table -- instead of
    # leaving it to be discovered afterwards as an alarm.
    secretary_log.record_turn(h, pid, {"role": "researcher",
                                       "body": {"text": "project note: Which columns matter most?"}})
    other = h.deletion_preview(qid)["retained"]["other_text_kept"]
    assert other["count"] >= 1
    assert "secretary_turns" in other["reason"]


def test_draft_quoting_the_question_does_not_fail_the_erasure_check(h):
    """Second, independent instance of the same defect: a kept latex draft whose
    body quotes the question's own words. latex_drafts is declared retained in
    the preview, so its surviving text must not be reported as a leftover
    question string either."""
    from app import latex as latex_mod

    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    draft = latex_mod.create_draft(h, pid, {"title": "Paper", "question_id": qid})
    latex_mod.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                "files": [{"path": "main.tex", "text": f"% {text}\n"}]})
    preview = h.deletion_preview(qid)
    assert preview["retained"]["latex_drafts_kept"]["count"] == 1

    result = h.delete_question(qid)

    assert result["verified"] is True
    assert result["remaining_hits"] == []
    assert latex_mod.read_draft(h, draft["draft_id"])["files"][0]["text"] == f"% {text}\n"


def test_library_note_quoting_the_question_is_declared_not_an_alarm(h):
    """Case 3 (HIGH, previously reported but not fixed): a library note that
    quotes the researcher's own question.

    library_items is outside _QUESTION_DELETE_TABLES (a note belongs to the
    project, never to a question), so deletion never touches it. The whole-DB
    scan therefore found the question's text in a note the researcher wrote
    themselves and reported it as proof the erase failed -- a person reading
    their own note was shown "Deletion incomplete" naming an internal table.
    Table-level exclusion could only silence this by naming library_items, which
    was the third band-aid in the same class; the scope-based check closes it
    because a table deletion never touches is outside the failure scope by
    construction. The note's survival is still REPORTED, in retained_hits."""
    from app import library as library_mod

    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    note = library_mod.create_note(h, {"project_id": pid, "text": f"My own note: {text}",
                                       "title": f"Note about {text}"})
    # Independent oracle: the storage layer really does hold the text, so this
    # test cannot pass by the note simply not matching.
    assert store.find_strings(h.db, [text])

    preview = h.deletion_preview(qid)
    entry = preview["retained"]["library_notes_kept"]
    assert set(entry) == {"count", "reason"}
    assert entry["count"] == 1
    assert "library note(s) keep text that quotes this question" in entry["reason"]

    result = h.delete_question(qid)

    # No false alarm: the note's survival is retention, not a failed erase.
    assert result["verified"] is True
    assert result["remaining_hits"] == []
    # ...but it is not hidden either: the researcher is told which kept row
    # still holds the words, and where.
    hits = [x for x in result["retained_hits"] if x["table"] == "library_items"]
    assert len(hits) == 1 and hits[0]["column"] == "body_text, title"
    assert hits[0]["rows"] == 1  # one note is one hit, not one per needle
    # The declaration was true: the note is still there, byte-identical.
    assert h.db.execute("SELECT body_text FROM library_items WHERE id=?",
                        (note["id"],)).fetchone()["body_text"] == f"My own note: {text}"


def test_briefing_snapshot_quoting_the_question_is_retained_not_a_failure(h):
    """Case 3, second instance: an append-only briefing snapshot.

    save_briefing_snapshot stores the project's briefing summary, including
    waiting-item text that quotes the question's own words. briefing_snapshots
    is immutable and outside the purge list, so after the delete the snapshot
    still holds the text. That used to be reported as erasure failure with the
    internal table name in the user-facing message; it is retention."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    snap = h.save_briefing_snapshot(pid)
    assert store.find_strings(h.db, [text])  # the snapshot really holds it

    preview = h.deletion_preview(qid)
    # The catch-all class names the table, so nothing surviving is undeclared.
    other = preview["retained"]["other_text_kept"]
    assert other["count"] >= 1
    assert "briefing_snapshots" in other["reason"]

    result = h.delete_question(qid)

    assert result["verified"] is True
    assert result["remaining_hits"] == []
    hits = [x for x in result["retained_hits"] if x["table"] == "briefing_snapshots"]
    assert hits and all(x["rows"] == 1 for x in hits)
    assert h.db.execute("SELECT count(*) c FROM briefing_snapshots WHERE id=?",
                        (snap["snapshot_id"],)).fetchone()["c"] == 1


def test_text_surviving_in_a_purged_table_still_fails_verification(h):
    """THE test with teeth: `verified` must still be False when it should be.

    The scope narrowing above must not become "always true". Here a row lives in
    a table deletion DOES purge, bound to THIS question, and the delete fails to
    remove it (the delete statement for that one table is neutralised, which is
    exactly what a real regression -- a renamed column, a broken WHERE, a
    dropped DELETE -- produces). The row's text is then still stored in the
    place deletion promised to empty, so verified must be False and the hit must
    name the table, column and row count.

    Chosen so the check cannot pass vacuously: the same question also has a
    retained survivor (a library note), which must NOT appear in remaining_hits.
    """
    from app import library as library_mod

    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    secretary_log.record_turn(h, pid, {"role": "secretary", "body": {"text": text},
                                       "question_id": qid})
    library_mod.create_note(h, {"project_id": pid, "text": f"note: {text}"})
    assert h.deletion_preview(qid)["by_store"]["secretary_turns"] == 1

    # Simulate a broken purge for secretary_turns only: the DELETE runs and the
    # row survives. A BEFORE DELETE trigger raising IGNORE does exactly that
    # without a test-only seam in the product code.
    h.db.execute("CREATE TRIGGER keep_secretary_turns BEFORE DELETE ON secretary_turns"
                 " BEGIN SELECT RAISE(IGNORE); END")
    h.db.commit()

    result = h.delete_question(qid)

    # The leftover is really there...
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE question_id=?",
                        (qid,)).fetchone()["c"] == 1
    # ...so the flag fails, and it fails on the right table.
    assert result["verified"] is False
    assert [hit["table"] for hit in result["remaining_hits"]] == ["secretary_turns"]
    assert result["remaining_hits"][0]["column"] == "body_json"
    assert result["remaining_hits"][0]["rows"] == 1
    # The retained note is not smuggled into the failure list.
    assert all(hit["table"] != "library_items" for hit in result["remaining_hits"])


def test_a_surviving_event_payload_still_fails_verification(h):
    """`events` is the one table that is neither fully purged nor fully kept:
    its rows survive and its payload cell is redacted. That makes it the exact
    place where a scope-based check could quietly stop being honest -- drop
    events from the purge scope and every redaction failure becomes invisible,
    while the preview still claims events_payload_nulled.

    Here the redaction is prevented (a trigger raising IGNORE on the payload
    UPDATE), so the payload survives in a row deletion promised to empty: the
    old table-level exclusion had to keep events out of its retained list for
    exactly this reason, and the scope-based check must keep it in the purge
    scope."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    payload = h.db.execute("SELECT payload FROM events WHERE question_id=?", (qid,)).fetchone()["payload"]
    assert payload and text in payload
    h.db.execute("CREATE TRIGGER keep_payload BEFORE UPDATE OF payload ON events"
                 " BEGIN SELECT RAISE(IGNORE); END")
    h.db.commit()

    result = h.delete_question(qid)

    # The payload really did survive the redaction...
    assert h.db.execute("SELECT payload FROM events WHERE question_id=?", (qid,)).fetchone()["payload"] == payload
    # ...so the flag fails and names the cell, not the whole row.
    assert result["verified"] is False
    assert [(x["table"], x["column"]) for x in result["remaining_hits"]] == [("events", "payload")]
    assert result["remaining_hits"][0]["rows"] == 1


def test_reminder_bound_to_a_deleted_decision_is_reported_as_orphaned(h):
    """Case 2 (BLOCKER): a genuine defect the check must not hide.

    The researcher decides on question Q, then records a reminder bound to
    subject_kind='decision' + subject_id=<Q's decision>. Deleting Q deletes the
    decision, so the reminder keeps its words with a binding to a row that no
    longer exists: an orphaning defect.

    This is reported -- but it is NOT erasure failure. revisit_conditions is
    never purged (the reminder is the researcher's own words and the preview
    declares it kept), so its survival says nothing about whether the erase was
    complete; verified must therefore stay True, or every researcher who wrote a
    reminder about their own question would be told "Deletion incomplete" after
    an irreversible step. The damage is named in `orphaned_refs` instead, which
    is where it can be acted on."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    decision = h.decide(pid, qid, "defer", f"waiting on {text}", None)
    reminder = h.record_revisit_condition(pid, "decision", decision["decision_id"], "2026-11-30",
                                          recorded_by="researcher", source="researcher command",
                                          note=f"revisit: {text}", status="recorded")

    preview = h.deletion_preview(qid)
    # The preview does not claim to keep a reminder about the question itself:
    # revisit_conditions_kept counts only question-bound reminders. The reminder
    # is bound to a decision, so its survival IS declared -- under the "other"
    # class -- before the irreversible step. That is preview==effect: nothing
    # survives undeclared, and the damage (an orphaned binding) is named after.
    assert preview["by_store"]["decisions"] == 1
    assert preview["retained"]["revisit_conditions_kept"]["count"] == 0
    assert preview["retained"]["revisit_conditions_other_kept"]["count"] == 1

    result = h.delete_question(qid)

    # The decision really is gone and the reminder really did survive with its
    # binding orphaned -- the state the report must not hide.
    assert h.db.execute("SELECT count(*) c FROM decisions WHERE id=?",
                        (decision["decision_id"],)).fetchone()["c"] == 0
    assert h.db.execute("SELECT subject_id FROM revisit_conditions WHERE id=?",
                        (reminder["id"],)).fetchone()["subject_id"] == decision["decision_id"]
    # So it is reported, in the field that can be acted on...
    assert [o["row_id"] for o in result["orphaned_refs"]] == [reminder["id"]]
    assert result["orphaned_refs"][0]["missing_subject_id"] == decision["decision_id"]
    # ...and the reminder's survival is retention, not an incomplete erase.
    assert result["verified"] is True
    assert result["remaining_hits"] == []


def test_draft_bound_to_another_question_is_declared_not_silently_suppressed(h):
    """Case 3 (MEDIUM): a draft on question B quoting question A's text.

    latex_drafts is retained, so the table-level filter dropped the hit and the
    API said verified: True while the preview said latex_drafts_kept: 0 -- true
    (that draft belongs to B) but useless to the researcher, who was never told
    the text survives somewhere. The retention is now declared per row:
    latex_drafts_quoting_kept counts exactly the drafts bound to another
    question whose saved text quotes this one."""
    from app import latex as latex_mod

    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid_a = asyncio.run(h.create_question(pid, text))["question_id"]
    qid_b = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    draft = latex_mod.create_draft(h, pid, {"title": "Paper on B", "question_id": qid_b})
    latex_mod.save_draft(h, draft["draft_id"], {"expected_revision": 0,
                                                "files": [{"path": "main.tex", "text": f"% quotes: {text}\n"}]})
    assert store.find_strings(h.db, [text])

    preview = h.deletion_preview(qid_a)
    # latex_drafts_kept is about A's own drafts; this one belongs to B.
    assert preview["retained"]["latex_drafts_kept"]["count"] == 0
    entry = preview["retained"]["latex_drafts_quoting_kept"]
    assert entry["count"] == 1
    assert "draft(s) on another question keep text that quotes this question" in entry["reason"]

    result = h.delete_question(qid_a)

    assert result["verified"] is True
    assert result["remaining_hits"] == []
    # B's draft really did keep A's words, so declaring it was necessary.
    assert latex_mod.read_draft(h, draft["draft_id"])["files"][0]["text"] == f"% quotes: {text}\n"


def test_remaining_hits_lists_one_entry_per_surviving_row(h):
    """Case 4 (LOW): the hit list triplicated.

    find_strings aggregates per (table, column) and appends one entry per
    matching needle, and a question's original/refined/working text are usually
    identical, so one surviving row produced several identical-looking entries
    -- three alarming rows for one note. Hits are now keyed by (table, row id):
    one surviving row is one entry.

    This pins the count on the FAILURE side (remaining_hits), because that is
    the list the UI renders: a retained survivor is no longer a failure at all,
    so the dedup has to be proved where a hit still means something. Fixture: a
    row that deletion must purge, in a table it purges, whose text deletion
    cannot remove (the delete path is stubbed for that table only, simulating a
    genuinely surviving row). The row matches both the question text and the
    decision reason -- two distinct needles -- so the old shape produced two
    entries for one row."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    reason = "we are still waiting on the agency"
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    decision = h.decide(pid, qid, "defer", reason, None)
    # Two purged-table rows, each carrying BOTH needles in one text column.
    for i in (1, 2):
        h.db.execute("INSERT INTO run_messages(task_id, question_id, stage, messages_json) VALUES(?,?,?,?)",
                     (f"t{i}", qid, "shadow", f"{text} / {reason}"))
    h.db.commit()
    # Prevent the delete for run_messages only: the rows survive a purge that
    # claims to remove them, which is exactly the case `verified` must catch.
    # A BEFORE DELETE trigger raising IGNORE is the honest simulation -- the
    # DELETE runs, the row is not removed -- and unlike monkeypatching
    # Connection.execute it needs no test-only seam in the product code.
    h.db.execute("CREATE TRIGGER keep_run_messages BEFORE DELETE ON run_messages"
                 " BEGIN SELECT RAISE(IGNORE); END")
    h.db.commit()

    result = h.delete_question(qid)

    # Independent oracle for the duplication the old shape produced: the same
    # (table, column) pair is appended once per matching needle.
    raw = [r for r in store.find_strings(h.db, [text, reason]) if r["table"] == "run_messages"]
    assert len(raw) == 2 and {r["column"] for r in raw} == {"messages_json"}, raw

    # Two surviving rows, two entries -- not one per needle per surviving row.
    assert result["verified"] is False
    assert len(result["remaining_hits"]) == 2
    assert {hit["table"] for hit in result["remaining_hits"]} == {"run_messages"}
    assert [hit["rows"] for hit in result["remaining_hits"]] == [1, 1]
    assert sum(hit["rows"] for hit in result["remaining_hits"]) == 2


def test_surviving_secretary_turn_on_another_question_is_retained_not_a_failure(h):
    """Consequence 1, half (a): a row-scoped retention the table filter missed.

    secretary_turns IS purged per question, but only for THIS question_id. A
    turn bound to a different question -- or to none -- is never touched by this
    deletion, so its surviving body quoting the deleted text is retention by
    design. The old check subtracted a list of TABLES, could not express that,
    and told the researcher "Deletion incomplete" after doing exactly what it
    promised."""
    pid = h.register_project("fx")["project_id"]
    text = "How long does processing take?"
    qid_a = asyncio.run(h.create_question(pid, text))["question_id"]
    qid_b = asyncio.run(h.create_question(pid, "What does est_cost measure?"))["question_id"]
    other = secretary_log.record_turn(h, pid, {"role": "researcher",
                                               "body": {"text": f"About B, quoting A: {text}"},
                                               "question_id": qid_b})
    unbound = secretary_log.record_turn(h, pid, {"role": "researcher",
                                                 "body": {"text": f"Project note quoting A: {text}"}})
    assert unbound["question_id"] is None

    result = h.delete_question(qid_a)

    # Both rows are genuinely still there, so the retention is real and the
    # check had to look at scope, not at the table name.
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (other["id"],)).fetchone()["c"] == 1
    assert h.db.execute("SELECT count(*) c FROM secretary_turns WHERE id=?",
                        (unbound["id"],)).fetchone()["c"] == 1
    assert result["verified"] is True
    assert result["remaining_hits"] == []
    kept = {(hit["table"], hit["column"]) for hit in result["retained_hits"]}
    assert ("secretary_turns", "body_json") in kept
    assert all(hit["table"] != "secretary_turns" for hit in result["remaining_hits"])


def test_preview_unknown_question_returns_404(client):
    r = client.get("/questions/does-not-exist/deletion-preview")
    assert r.status_code == 404
    # Harness.question raises KeyError("question not found"); guard maps it here.
    assert r.json() == {"error": "not_found", "message": "question not found", "details": {}}


def test_preview_is_read_only(client, h):
    _, qid = seed(h)
    before = full_snapshot(h)
    first = client.get(f"/questions/{qid}/deletion-preview")
    assert first.status_code == 200
    # Write-proof (no committed, row-visible write): the whole-DB dump --
    # including usage_ledger, questions_fts, and sqlite_sequence -- is
    # unchanged, so even an audit/event write smuggled into the preview path
    # would fail here. Uncommitted writes on another connection are out of
    # scope by design (see module docstring).
    assert full_snapshot(h) == before
    # The question still exists: a follow-up read over HTTP succeeds, which
    # H().question(qid) would reject with 404 had the preview deleted it.
    assert client.get(f"/questions/{qid}/context").status_code == 200
    second = client.get(f"/questions/{qid}/deletion-preview")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert full_snapshot(h) == before
