"""SQLite store (WAL + FTS5). Search is schema-driven: text_columns() discovers every TEXT column, so no
hand-written column list can silently miss one. find_strings() is the generic whole-DB scan; it knows nothing
about what a deletion purges, so deletion verification uses find_strings_in_scope(), which searches only the
rows and cells the caller's deletion scope declares purged (Harness._question_purge_scope)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS question_budgets(question_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
 limits_json TEXT NOT NULL, reserved_requests INTEGER NOT NULL DEFAULT 0,
 tool_calls INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  files_json TEXT NOT NULL, input_version TEXT NOT NULL, deletion_generation INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, original_text TEXT NOT NULL,
  refined_text TEXT, working_text TEXT NOT NULL, refinement_status TEXT NOT NULL, memory TEXT NOT NULL,
  state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(text, question_id UNINDEXED);
CREATE TABLE IF NOT EXISTS research_canvases(question_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body_json TEXT NOT NULL, previous_json TEXT);
CREATE TABLE IF NOT EXISTS question_contexts(question_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL,
 pack_version TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, capability_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS question_datasets(question_id TEXT PRIMARY KEY, datasets_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_runs(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, project_id TEXT NOT NULL,
  status TEXT NOT NULL, input_version TEXT NOT NULL, deletion_generation INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, question_id TEXT NOT NULL,
  stage TEXT NOT NULL, messages_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS designs(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, project_id TEXT NOT NULL,
  body_json TEXT NOT NULL, harness_flags TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clarifications(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, question_id TEXT NOT NULL,
  project_id TEXT NOT NULL, body_json TEXT NOT NULL, status TEXT NOT NULL, input_version TEXT NOT NULL,
  deletion_generation INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, cq_id TEXT NOT NULL, question_id TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, project_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL, source_id TEXT NOT NULL, excerpt TEXT NOT NULL, location TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assessments(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, project_id TEXT NOT NULL,
  body_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question_id TEXT NOT NULL,
  verdict TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_actions(action_id TEXT PRIMARY KEY, question_id TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clarification_responses(cq_id TEXT PRIMARY KEY, question_id TEXT NOT NULL,
  client_action_id TEXT UNIQUE, kind TEXT NOT NULL, value TEXT, status TEXT NOT NULL, result_json TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, question_id TEXT,
  kind TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shadow_runs(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, project_id TEXT NOT NULL,
  record_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, question_id TEXT, task_id TEXT,
  stage TEXT NOT NULL, adapter TEXT NOT NULL, requests INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
   cost_usd REAL, cost_note TEXT NOT NULL, created_at TEXT NOT NULL, elapsed_seconds REAL);
CREATE TABLE IF NOT EXISTS background_scopes(project_id TEXT PRIMARY KEY, allowed_sources_json TEXT NOT NULL,
  bundle_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preparation_jobs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question_id TEXT NOT NULL,
  object_type TEXT NOT NULL, object_id TEXT NOT NULL, scope_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prepared_cards(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, project_id TEXT NOT NULL,
  question_id TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL, evidence_json TEXT NOT NULL,
  limitations_text TEXT NOT NULL, unresolved_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS latex_drafts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question_id TEXT,
  title TEXT NOT NULL, engine TEXT NOT NULL DEFAULT 'auto', main_file TEXT NOT NULL DEFAULT 'main.tex',
  body_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, previous_json TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS latex_draft_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL,
  revision INTEGER NOT NULL, body_json TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS latex_citations(draft_id TEXT NOT NULL, cite_key TEXT NOT NULL,
  ref_kind TEXT NOT NULL, ref_id TEXT NOT NULL, project_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(draft_id, cite_key));
CREATE TABLE IF NOT EXISTS latex_compiles(id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, project_id TEXT NOT NULL,
  status TEXT NOT NULL, engine TEXT, revision INTEGER NOT NULL, pdf_path TEXT, pdf_bytes INTEGER,
  log_text TEXT, diagnostics_json TEXT NOT NULL, elapsed_ms INTEGER,
  citations_resolved INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS briefing_snapshots(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
  summary_json TEXT NOT NULL, decision_ids_json TEXT NOT NULL, waiting_ids_json TEXT NOT NULL,
  input_version TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS overlay_versions(project_id TEXT NOT NULL, pack_id TEXT NOT NULL,
  overlay_version TEXT NOT NULL, status TEXT NOT NULL, body_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0, active_reason TEXT, activated_at TEXT,
  prev_active_version TEXT, deactivated_at TEXT, deactivation_reason TEXT,
  created_at TEXT NOT NULL, PRIMARY KEY(project_id, pack_id, overlay_version));
CREATE TABLE IF NOT EXISTS revisit_conditions(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  condition_type TEXT NOT NULL DEFAULT 'explicit_date',
  recorded_date TEXT NOT NULL,
  recorded_by TEXT NOT NULL,
  source TEXT NOT NULL,
  note TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS library_folders(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT,
  name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS library_items(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, folder_id TEXT,
  kind TEXT NOT NULL, title TEXT NOT NULL, day TEXT NOT NULL, body_text TEXT, pdf_path TEXT, pages INTEGER,
  chars INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS list_order(list_kind TEXT NOT NULL, scope_id TEXT NOT NULL, target_id TEXT NOT NULL,
  position INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(list_kind, scope_id, target_id));
CREATE TABLE IF NOT EXISTS secretary_turns(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, pack_id TEXT,
  question_id TEXT, role TEXT NOT NULL, body_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secretary_nudge_settings(
  project_id TEXT PRIMARY KEY NOT NULL,
  mode TEXT NOT NULL DEFAULT 'standard',
  snoozed_until TEXT,
  snooze_set_at TEXT,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_checks(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question_id TEXT, draft_id TEXT,
  claim_text TEXT NOT NULL, claim_sha256 TEXT NOT NULL, claim_location_json TEXT,
  source_items_json TEXT NOT NULL, table_item_id TEXT, table_sha256 TEXT,
  record_json TEXT NOT NULL, preview_hash TEXT NOT NULL, reservation REAL NOT NULL,
  status TEXT NOT NULL, job_id TEXT, result_json TEXT, result_digest TEXT,
  decision TEXT, decision_note TEXT, decided_at TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS claim_checks_project ON claim_checks(project_id, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if "elapsed_seconds" not in {r["name"] for r in db.execute("PRAGMA table_info(usage_ledger)")}:
        db.execute("ALTER TABLE usage_ledger ADD COLUMN elapsed_seconds REAL")
        db.commit()
    # A project belongs to the chip it was started under, so two chips never share
    # answers, results or code. Existing projects predate chips: they keep NULL and
    # are shown as belonging to no chip rather than being guessed into one.
    project_cols = {r["name"] for r in db.execute("PRAGMA table_info(projects)")}
    if "pack_id" not in project_cols:
        db.execute("ALTER TABLE projects ADD COLUMN pack_id TEXT")
        db.commit()
    return db


def log_event(db, project_id, question_id, kind, payload: dict | None):
    if question_id:
        q = db.execute("SELECT memory FROM questions WHERE id=?", (question_id,)).fetchone()
        if q is not None and q["memory"] == "session_only":
            payload = {"memory": "session_only"}  # centrally suppress all derived/private content
    db.execute("INSERT INTO events(project_id, question_id, kind, payload, created_at) VALUES(?,?,?,?,?)",
               (project_id, question_id, kind, json.dumps(payload) if payload is not None else None, now()))


def log_usage(db, project_id, question_id, task_id, stage, adapter, u, elapsed_seconds=None):
    db.execute("INSERT INTO usage_ledger(project_id, question_id, task_id, stage, adapter, requests, input_tokens, output_tokens,"
               " cost_usd, cost_note, created_at, elapsed_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (project_id, question_id, task_id, stage, adapter, u.requests, u.input_tokens, u.output_tokens, None,
                ("cost not returned by the API; null, not zero" +
                  ("; token/attempt accounting incomplete" if not getattr(u, "usage_complete", True) else "")), now(), elapsed_seconds))


def text_columns(db) -> list[tuple[str, str]]:
    cols = []
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                              " AND name NOT LIKE 'questions_fts_%'"):
        for c in db.execute(f"PRAGMA table_info('{name}')"):
            if (c["type"] or "").upper() in ("TEXT", ""):
                cols.append((name, c["name"]))
    return cols


def find_strings(db, needles: list[str]) -> list[dict]:
    hits = []
    for table, col in text_columns(db):
        for n in needles:
            if not n or len(n) < 4:
                continue
            row = db.execute(f'SELECT count(*) c FROM "{table}" WHERE instr("{col}", ?) > 0', (n,)).fetchone()
            if row["c"]:
                hits.append({"table": table, "column": col, "needle": n[:40], "rows": row["c"]})
    return hits


NEEDLE_MIN_CHARS = 4
NEEDLE_DISPLAY_CHARS = 40


def find_strings_in_scope(db, needles: list[str], targets: list[dict]) -> list[dict]:
    """Text still stored inside the caller's declared scope: one entry per ROW.

    targets: [{"table", "column", "where", "params"}] -- `where` is a SQL
    predicate (carrying its own positional params) that selects the rows of
    that table+column belonging to the caller's scope. This is how a deletion
    states its own honesty: it names the cells it purges and, separately, the
    cells it keeps, instead of scanning every table and subtracting a list of
    tables afterwards.

    Unlike find_strings (one entry per needle per table+column), one stored row
    is one hit here: two needles matching the same row, or one needle matching
    two of its text columns, collapse into a single entry

        {"table": str, "column": str, "needle": str, "rows": 1}

    where `column` names every matching text column of that row and `needle` is
    the matching string cut to NEEDLE_DISPLAY_CHARS for display. Needles
    shorter than NEEDLE_MIN_CHARS are ignored, exactly as find_strings ignores
    them.
    """
    usable = [n for n in dict.fromkeys(needles) if n and len(n) >= NEEDLE_MIN_CHARS]
    if not usable:
        return []
    found: dict[tuple[str, int], dict] = {}
    for target in targets:
        table, column = target["table"], target["column"]
        where = target["where"]
        for n in usable:
            rows = db.execute(f'SELECT rowid AS rid FROM "{table}" WHERE ({where}) AND instr("{column}", ?) > 0',
                              tuple(target["params"]) + (n,))
            for r in rows:
                entry = found.setdefault((table, r["rid"]),
                                         {"table": table, "columns": [],
                                          "needle": n[:NEEDLE_DISPLAY_CHARS]})
                if column not in entry["columns"]:
                    entry["columns"].append(column)
    return [{"table": e["table"], "column": ", ".join(sorted(e["columns"])),
             "needle": e["needle"], "rows": 1} for e in found.values()]
