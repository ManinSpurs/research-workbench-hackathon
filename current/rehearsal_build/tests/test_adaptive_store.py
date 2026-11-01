"""Slice D3 PART 1 tests -- persistent overlay version store.

Real SQLite DB via ``app.store.connect`` (which applies SCHEMA, including the
new ``overlay_versions`` table) on a tmp file; all project/pack/version data
is hand-built mock content. No harness, no API, no model calls, no credits.

Covers the PART 1 contract: write/read round-trip, AD03 gate refusal at write
time, immutability refusal, explicit activate/rollback sequence, isolation
(project A never sees project B; SOCIAL never sees ENGINEERING), and history
preserved after rollback.
"""
import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import adaptive_store as ads
from app import store

SOCIAL = "social_science"
ENGINEERING = "engineering_measurement"


@pytest.fixture
def db(tmp_path):
    conn = store.connect(str(tmp_path / "overlay_store.db"))
    try:
        yield conn
    finally:
        conn.close()


def valid_entry(**overrides):
    entry = {"kind": "checklist_item", "text": "verify units against the release note",
             "record_ids": ["D-1", "D-2"]}
    entry.update(overrides)
    return entry


def approved_v(label, **overrides):
    version = {"status": "approved", "overlay_version": label,
               "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": ["bm-1"],
               "entries": [valid_entry()]}
    version.update(overrides)
    return version


def deprecated_v(label, **overrides):
    version = {"status": "deprecated", "overlay_version": label,
               "evidence_refs": ["dec-1"], "reason": "benchmark showed no effect"}
    version.update(overrides)
    return version


# ---- schema: the additive table exists ---------------------------------------

def test_overlay_versions_table_exists_additive(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "overlay_versions" in tables
    cols = {c["name"] for c in db.execute("PRAGMA table_info('overlay_versions')")}
    assert {"project_id", "pack_id", "overlay_version", "status", "body_json",
            "active", "active_reason", "activated_at", "prev_active_version",
            "deactivated_at", "deactivation_reason", "created_at"} <= cols
    # Existing tables untouched: questions still has its original columns.
    qcols = {c["name"] for c in db.execute("PRAGMA table_info('questions')")}
    assert {"id", "project_id", "memory", "state"} <= qcols


# ---- write/read round-trip; persist never auto-activates ----------------------

def test_write_read_round_trip(db):
    ok, note = ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))
    assert ok, note
    history = ads.version_history(db, "proj-A", SOCIAL)
    assert [row["overlay_version"] for row in history] == ["v1"]
    row = history[0]
    assert row["status"] == "approved"
    assert row["body"] == approved_v("v1")
    assert row["active"] is False
    # Persisting is not activating: the scope stays cold until explicit activate.
    assert ads.active_version(db, "proj-A", SOCIAL) is None


def test_cold_scope_reads_empty(db):
    assert ads.active_version(db, "proj-new", SOCIAL) is None
    assert ads.version_history(db, "proj-new", SOCIAL) == []


# ---- AD03 gate at write time ---------------------------------------------------

def test_gate_refuses_approved_without_benchmark_refs(db):
    ok, reason = ads.persist_overlay_version(
        db, "proj-A", SOCIAL, approved_v("v1", benchmark_refs=[]))
    assert not ok and "enchmark" in reason
    assert ads.version_history(db, "proj-A", SOCIAL) == []
    assert ads.active_version(db, "proj-A", SOCIAL) is None


@pytest.mark.parametrize("bad_version", [
    "not-a-dict",
    None,
    {},
    {"status": "approved", "evidence_refs": ["d"], "benchmark_refs": ["b"]},
    {"status": "approved", "overlay_version": "v1", "evidence_refs": [],
     "benchmark_refs": ["b"]},
    {"status": "approved", "overlay_version": "  ", "evidence_refs": ["d"],
     "benchmark_refs": ["b"]},
    {"status": "deprecated", "overlay_version": "v1", "evidence_refs": ["d"]},
    {"status": "approved", "overlay_version": "v1", "evidence_refs": ["d"],
     "benchmark_refs": ["b"], "entries": "not-a-list"},
    {"status": "approved", "overlay_version": "v1", "evidence_refs": ["d"],
     "benchmark_refs": ["b"], "entries": [
         {"kind": "checklist_item", "text": "guaranteed improvement",
          "record_ids": ["D-1"]}]},
    {"status": "approved", "overlay_version": "v1", "evidence_refs": ["d"],
     "benchmark_refs": ["b"], "entries": [
         {"kind": "checklist_item", "text": "verify units", "record_ids": []}]},
])
def test_gate_refuses_invalid_shapes(db, bad_version):
    ok, reason = ads.persist_overlay_version(db, "proj-A", SOCIAL, bad_version)
    assert not ok and isinstance(reason, str) and reason.strip()
    assert ads.version_history(db, "proj-A", SOCIAL) == []


def test_deprecated_persists_without_benchmark_refs(db):
    ok, note = ads.persist_overlay_version(db, "proj-A", SOCIAL, deprecated_v("v0"))
    assert ok, note
    assert [r["overlay_version"] for r in ads.version_history(db, "proj-A", SOCIAL)] == ["v0"]


# ---- immutability ---------------------------------------------------------------

def test_immutability_refuses_rewrite(db):
    ok, _ = ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))
    assert ok
    # Same content again: refused.
    ok, reason = ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))
    assert not ok and "immutable" in reason
    # Changed content under the same label: refused too, never repaired.
    ok, reason = ads.persist_overlay_version(
        db, "proj-A", SOCIAL, deprecated_v("v1", reason="changed my mind"))
    assert not ok and "immutable" in reason
    history = ads.version_history(db, "proj-A", SOCIAL)
    assert len(history) == 1
    assert history[0]["body"] == approved_v("v1")


# ---- explicit activation only ------------------------------------------------------

@pytest.mark.parametrize("bad_reason", ["", "   ", None, 42, ["explicit"]])
def test_activate_requires_explicit_reason(db, bad_reason):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    ok, reason = ads.activate(db, "proj-A", SOCIAL, "v1", bad_reason)
    assert not ok and "reason" in reason
    assert ads.active_version(db, "proj-A", SOCIAL) is None


def test_activate_unknown_version_refused(db):
    ok, reason = ads.activate(db, "proj-A", SOCIAL, "v9", "explicit adopt")
    assert not ok and "never persisted" in reason
    assert ads.active_version(db, "proj-A", SOCIAL) is None


def test_activate_sets_active_with_recorded_reason(db):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    ok, note = ads.activate(db, "proj-A", SOCIAL, "v1", "researcher adopt at review")
    assert ok, note
    current = ads.active_version(db, "proj-A", SOCIAL)
    assert current["overlay_version"] == "v1"
    assert current["active"] is True
    assert current["active_reason"] == "researcher adopt at review"
    assert isinstance(current["activated_at"], str) and current["activated_at"].strip()
    # Re-activating the already-active version is refused.
    ok, reason = ads.activate(db, "proj-A", SOCIAL, "v1", "again")
    assert not ok and "already active" in reason


# ---- activate/rollback sequence; history preserved ----------------------------------

def test_activate_rollback_sequence(db):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v2"))[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v1", "adopt v1")[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v2", "adopt v2")[0]
    assert ads.active_version(db, "proj-A", SOCIAL)["overlay_version"] == "v2"

    ok, note = ads.rollback(db, "proj-A", SOCIAL, "v2 shows no effect")
    assert ok, note
    current = ads.active_version(db, "proj-A", SOCIAL)
    assert current["overlay_version"] == "v1"
    assert current["active_reason"] == "rollback: v2 shows no effect"

    ok, note = ads.rollback(db, "proj-A", SOCIAL, "back to base guidance")
    assert ok, note
    assert ads.active_version(db, "proj-A", SOCIAL) is None  # cold/none restored

    # Rollback never deletes history: both rows intact, deterministic order.
    history = ads.version_history(db, "proj-A", SOCIAL)
    assert [row["overlay_version"] for row in history] == ["v1", "v2"]
    by_version = {row["overlay_version"]: row for row in history}
    assert by_version["v1"]["body"] == approved_v("v1")
    assert by_version["v2"]["body"] == approved_v("v2")
    # A rollback reason belongs to the row that was ACTIVE when it was rolled back
    # from: step 2 rolled back from v2, step 3 rolled back from v1 to cold/none.
    assert by_version["v2"]["deactivation_reason"] == "rollback: v2 shows no effect"
    assert by_version["v1"]["deactivation_reason"] == "rollback: back to base guidance"
    assert by_version["v1"]["active"] is False
    assert by_version["v2"]["active"] is False


def test_rollback_nothing_active_refused(db):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    ok, reason = ads.rollback(db, "proj-A", SOCIAL, "a recorded reason")
    assert not ok and "nothing to restore" in reason
    assert ads.version_history(db, "proj-A", SOCIAL)[0]["active"] is False


@pytest.mark.parametrize("bad_reason", ["", "  ", None])
def test_rollback_requires_reason(db, bad_reason):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v1", "adopt")[0]
    ok, reason = ads.rollback(db, "proj-A", SOCIAL, bad_reason)
    assert not ok and "reason" in reason
    assert ads.active_version(db, "proj-A", SOCIAL)["overlay_version"] == "v1"


# ---- isolation -----------------------------------------------------------------------

def test_isolation_across_projects(db):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v1", "adopt in A")[0]
    assert ads.version_history(db, "proj-B", SOCIAL) == []
    assert ads.active_version(db, "proj-B", SOCIAL) is None
    # B's own write does not disturb A.
    assert ads.persist_overlay_version(db, "proj-B", SOCIAL, approved_v("v9"))[0]
    assert ads.active_version(db, "proj-A", SOCIAL)["overlay_version"] == "v1"
    assert ads.active_version(db, "proj-B", SOCIAL) is None
    assert [r["overlay_version"] for r in ads.version_history(db, "proj-A", SOCIAL)] == ["v1"]
    assert [r["overlay_version"] for r in ads.version_history(db, "proj-B", SOCIAL)] == ["v9"]


def test_isolation_across_packs(db):
    assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v("v1"))[0]
    assert ads.persist_overlay_version(db, "proj-A", ENGINEERING, approved_v("vE"))[0]
    assert ads.activate(db, "proj-A", ENGINEERING, "vE", "adopt engineering")[0]
    assert ads.active_version(db, "proj-A", SOCIAL) is None
    assert ads.active_version(db, "proj-A", ENGINEERING)["overlay_version"] == "vE"
    assert [r["overlay_version"] for r in ads.version_history(db, "proj-A", SOCIAL)] == ["v1"]
    assert [r["overlay_version"] for r in ads.version_history(db, "proj-A", ENGINEERING)] == ["vE"]


# ---- deterministic order + history preserved after rollback -----------------------------

def test_history_deterministic_and_preserved(db):
    for label in ("v1", "v2", "v3"):
        assert ads.persist_overlay_version(db, "proj-A", SOCIAL, approved_v(label))[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v2", "adopt")[0]
    assert ads.activate(db, "proj-A", SOCIAL, "v3", "adopt")[0]
    assert ads.rollback(db, "proj-A", SOCIAL, "no effect")[0]
    history = ads.version_history(db, "proj-A", SOCIAL)
    assert [row["overlay_version"] for row in history] == ["v1", "v2", "v3"]
    assert ads.active_version(db, "proj-A", SOCIAL)["overlay_version"] == "v2"
    assert all(row["body"]["benchmark_refs"] == ["bm-1"] for row in history)


# ---- no auto/timer/training path ---------------------------------------------------------

def test_module_has_no_auto_timer_training_path():
    assert set(ads.__all__) == {"persist_overlay_version", "activate", "rollback",
                                "active_version", "version_history"}
    for name in dir(ads):
        lowered = name.lower()
        assert "auto" not in lowered, name
        assert "train" not in lowered, name
        assert "weight" not in lowered, name
        assert "gradient" not in lowered, name
    source = inspect.getsource(ads)
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0].lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(p.lower() for p in node.module.split("."))
    for forbidden in ("harness", "api", "store", "sqlite3", "threading",
                       "frontend", "adaptive_pack_gaps"):
        assert forbidden not in imported, forbidden
    for token in ("Timer(", "time.sleep", "auto_activate", "activation_loop",
                  "DELETE FROM"):
        assert token not in source, token
    # No silent activation path: the only writers of active=1 are activate/rollback.
    assert source.count("active=1") == 3
