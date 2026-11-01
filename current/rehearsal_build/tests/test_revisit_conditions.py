"""Secretary revisit-condition storage slice (C6/C7 foundation).

Implements the SECRETARY_REVISIT_DESIGN_EN §5 cases that belong to this slice:
RV01 (creation + omission/wrong-status rejection), RV02 (blank source), RV03
(recorded_by omission/blank/non-researcher). Storage + record + list only:
no due derivation, no readiness scan, no briefing/radar wiring, no
transitions, no routes. Direct-harness fixture (no sockets, no subprocess,
no model calls) following tests/test_dot_safety.py.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import harness as module  # noqa: E402
from app import store  # noqa: E402
from app.harness import Conflict  # noqa: E402
from test_dot_safety import StubAdapter  # noqa: E402  (shared loopback-guarded harness fixture)


@pytest.fixture
def h(tmp_path, monkeypatch):
    stub = StubAdapter()
    monkeypatch.setattr(module, "ModelAdapter", lambda: stub)
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    harness = module.Harness(str(tmp_path / "revisit.db"), data)
    yield harness
    harness.db.close()


def seed(h, kind="fx", text="What does est_cost measure?"):
    import asyncio
    pid = h.register_project(kind)["project_id"]
    qid = asyncio.run(h.create_question(pid, text))["question_id"]
    return pid, qid


def valid(**kw):
    args = {"subject_kind": "question", "subject_id": "q-seed",
            "recorded_date": "2026-11-30", "recorded_by": "researcher",
            "source": "researcher command", "note": "recheck after release"}
    args.update(kw)
    return args


def count(h):
    return h.db.execute("SELECT count(*) c FROM revisit_conditions").fetchone()["c"]


# ------------------------------------------------------------------ schema (§1, RV14-adjacent coverage check)
def test_schema_is_additive_and_exact(h):
    tables = {r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "revisit_conditions" in tables
    assert "briefing_snapshots" in tables  # parallel C13 slice untouched
    info = {c["name"]: c for c in h.db.execute("PRAGMA table_info('revisit_conditions')")}
    assert set(info) == {"id", "project_id", "subject_kind", "subject_id", "condition_type",
                         "recorded_date", "recorded_by", "source", "note",
                         "status", "created_at", "updated_at"}
    assert info["condition_type"]["dflt_value"] == "'explicit_date'"
    assert info["recorded_by"]["dflt_value"] is None
    assert info["status"]["dflt_value"] is None
    assert info["recorded_by"]["notnull"] == 1 and info["status"]["notnull"] == 1
    assert info["note"]["notnull"] == 0
    text_cols = {(t, c) for t, c in store.text_columns(h.db) if t == "revisit_conditions"}
    assert {c for _, c in text_cols} >= {"id", "project_id", "subject_kind", "subject_id",
                                         "condition_type", "recorded_date", "recorded_by",
                                         "source", "status"}
    assert "due" not in {c["name"] for c in h.db.execute("PRAGMA table_info('revisit_conditions')")}


# ------------------------------------------------------------------ RV01
def test_rv01_valid_create_stores_recorded(h):
    pid, qid = seed(h)
    before = count(h)
    row = h.record_revisit_condition(pid, subject_id=qid, **{k: v for k, v in valid().items()
                                                             if k not in ("subject_kind", "subject_id")},
                                     subject_kind="question", status="recorded")
    assert row["project_id"] == pid and row["subject_id"] == qid
    assert row["status"] == "recorded" and row["recorded_by"] == "researcher"
    assert row["condition_type"] == "explicit_date"  # DDL default applied
    assert row["created_at"] and row["updated_at"] and row["model_calls"] == 0
    assert count(h) == before + 1
    listed = h.list_revisit_conditions(pid)
    assert listed["count"] == 1 and listed["conditions"][0]["id"] == row["id"]
    assert listed["model_calls"] == 0


@pytest.mark.parametrize("bad_status", ["due", "acknowledged", "done", "deferred", "recorded ", ""])
def test_rv01_wrong_status_rejected_nothing_stored(h, bad_status):
    pid, qid = seed(h)
    before = count(h)
    with pytest.raises(Conflict):
        h.record_revisit_condition(pid, **valid(subject_id=qid, status=bad_status))
    assert count(h) == before, "rejected creation must store nothing"


@pytest.mark.parametrize("omit", ["status", "recorded_by", "source", "recorded_date", "subject_id"])
def test_rv01_omission_rejected_nothing_stored(h, omit):
    pid, qid = seed(h)
    args = valid(subject_id=qid, status="recorded")
    del args[omit]
    before = count(h)
    with pytest.raises((Conflict, TypeError)):
        h.record_revisit_condition(pid, **args)
    assert count(h) == before, "rejected creation must store nothing"


# ------------------------------------------------------------------ RV02
@pytest.mark.parametrize("bad_source", [None, "", "   ", 123])
def test_rv02_missing_or_blank_source_rejected(h, bad_source):
    pid, qid = seed(h)
    before = count(h)
    with pytest.raises(Conflict):
        h.record_revisit_condition(pid, **valid(subject_id=qid, status="recorded", source=bad_source))
    assert count(h) == before, "rejected creation must store nothing"


# ------------------------------------------------------------------ RV03
@pytest.mark.parametrize("bad_author", [None, "", "   ", "Secretary", "secretary", "model",
                                        "nudge", "import", "system", 123])
def test_rv03_non_researcher_recorded_by_rejected(h, bad_author):
    pid, qid = seed(h)
    before = count(h)
    with pytest.raises((Conflict, TypeError)):
        h.record_revisit_condition(pid, **valid(subject_id=qid, status="recorded", recorded_by=bad_author))
    assert count(h) == before, "rejected creation must store nothing; no default fills the omission"


# ------------------------------------------------------------------ slice guards (non-goals)
def test_no_other_condition_type_or_transition(h):
    pid, qid = seed(h)
    before = count(h)
    with pytest.raises(Conflict):
        h.record_revisit_condition(pid, **valid(subject_id=qid, status="recorded", condition_type="inferred_age"))
    assert count(h) == before
    with pytest.raises(Conflict):
        h.record_revisit_condition(pid, **valid(subject_id=qid, status="recorded", subject_kind="project"))
    assert count(h) == before


def test_list_isolation_per_project(h):
    pid_a, qid_a = seed(h)
    pid_b, _ = seed(h, text="Can I study time to final approval?")
    row = h.record_revisit_condition(pid_a, **valid(subject_id=qid_a, status="recorded"))
    assert h.list_revisit_conditions(pid_a)["count"] == 1
    assert h.list_revisit_conditions(pid_b)["count"] == 0  # RV12-adjacent: no cross-project leak at read
    by_subject = h.list_revisit_conditions(pid_a, subject_kind="question", subject_id=qid_a)
    assert by_subject["count"] == 1 and by_subject["conditions"][0]["id"] == row["id"]
    assert h.list_revisit_conditions(pid_a, subject_kind="question", subject_id="q-other")["count"] == 0
    with pytest.raises(KeyError):
        h.list_revisit_conditions("proj-unknown")


def test_unknown_project_create_raises_keyerror(h):
    with pytest.raises(KeyError):
        h.record_revisit_condition("proj-unknown", **valid(status="recorded"))
    assert count(h) == 0
