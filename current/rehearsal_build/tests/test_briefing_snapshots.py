"""C13 briefing snapshots: 'what changed since you last saw it' (model-free, read-model only).

Harness-level tests using the OfflineAdapter: zero model calls, no fake_meta
server. Fixture pattern mirrors tests/test_tier1.py client fixture but builds
Harness directly. New question content uses texts containing a year (>= 2000)
so refine_check passes 'ok' and create_question is fully local.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.harness import Harness  # noqa: E402
from app.offline import OfflineAdapter  # noqa: E402
from app import store  # noqa: E402


@pytest.fixture
def h(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    return Harness(str(tmp_path / "wb.db"), data, adapter=OfflineAdapter())


def make_question(h, pid, text):
    return asyncio.run(h.create_question(pid, text))


def test_save_then_no_change_diff_is_empty(h):
    pid = h.register_project("nyc")["project_id"]
    out = h.save_briefing_snapshot(pid)
    assert out["project_id"] == pid and out["summary"]["project_id"] == pid
    assert out["recorded_at"]
    rows = h.db.execute("SELECT * FROM briefing_snapshots WHERE project_id=?", (pid,)).fetchall()
    assert len(rows) == 1 and rows[0]["id"] == out["snapshot_id"]
    # Stored clock convention: created_at is store.now() ISO-8601 UTC with seconds.
    assert rows[0]["created_at"] == out["recorded_at"] and "T" in rows[0]["created_at"]
    diff = h.briefing_changes_since_last_snapshot(pid)
    assert diff["has_snapshot"] is True and diff["snapshot_id"] == out["snapshot_id"]
    assert diff["new_decision_count"] == 0 and diff["new_waiting_count"] == 0
    assert diff["new_decisions"] == [] and diff["new_waiting"] == []
    assert diff["input_changed_since_snapshot"] is False
    # Diff is read-only: no extra snapshot row, events/usage untouched.
    assert h.db.execute("SELECT count(*) c FROM briefing_snapshots").fetchone()["c"] == 1


def test_change_after_snapshot_appears_in_diff(h):
    pid = h.register_project("nyc")["project_id"]
    q = make_question(h, pid, "Can I study 2023 residential permits and later?")
    h.save_briefing_snapshot(pid)
    h.decide(pid, q["question_id"], "defer", "snapshot diff reason", None)
    diff = h.briefing_changes_since_last_snapshot(pid)
    # Diff compares the post-decision briefing against the pre-decision snapshot,
    # so the new decision and the resolved waiting item both surface.
    assert diff["has_snapshot"] is True
    assert diff["new_decision_count"] == 1
    assert "snapshot diff reason" in diff["new_decisions"][0]["text"]
    assert diff["resolved_waiting"] and q["working_text"] in diff["resolved_waiting"][0]


def test_new_question_after_snapshot_appears_as_new_waiting(h):
    pid = h.register_project("nyc")["project_id"]
    h.save_briefing_snapshot(pid)
    q2 = make_question(h, pid, "Can I study 2024 commercial permits and later?")
    diff = h.briefing_changes_since_last_snapshot(pid)
    assert diff["new_waiting_count"] >= 1
    assert any(q2["working_text"] in w["text"] for w in diff["new_waiting"])


def test_no_snapshot_state_is_honest(h):
    pid = h.register_project("nyc")["project_id"]
    diff = h.briefing_changes_since_last_snapshot(pid)
    assert diff["has_snapshot"] is False and diff["state"] == "no snapshot yet"
    assert diff["snapshot_id"] is None and diff["recorded_at"] is None
    assert diff["new_decision_count"] == 0 and diff["new_waiting_count"] == 0
    assert diff["input_changed_since_snapshot"] is None
    assert h.db.execute("SELECT count(*) c FROM briefing_snapshots").fetchone()["c"] == 0


def test_cross_project_isolation(h):
    pid_a = h.register_project("nyc")["project_id"]
    pid_b = h.register_project("nyc")["project_id"]
    h.save_briefing_snapshot(pid_b)
    qa = make_question(h, pid_a, "Can I study 2023 residential permits and later?")
    h.decide(pid_a, qa["question_id"], "adopt", "isolation reason A", None)
    diff_b = h.briefing_changes_since_last_snapshot(pid_b)
    assert diff_b["has_snapshot"] is True
    assert diff_b["new_decision_count"] == 0 and diff_b["new_waiting_count"] == 0
    assert "isolation reason A" not in json.dumps(diff_b)
    diff_a = h.briefing_changes_since_last_snapshot(pid_a)
    assert diff_a["has_snapshot"] is False and diff_a["state"] == "no snapshot yet"


def test_snapshots_are_immutable_second_save_appends(h):
    pid = h.register_project("nyc")["project_id"]
    first = h.save_briefing_snapshot(pid)
    q = make_question(h, pid, "Can I study 2023 residential permits and later?")
    second = h.save_briefing_snapshot(pid)
    assert second["snapshot_id"] != first["snapshot_id"]
    rows = h.db.execute("SELECT * FROM briefing_snapshots WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()
    assert [r["id"] for r in rows] == [first["snapshot_id"], second["snapshot_id"]]
    # The first snapshot row is untouched by the second save.
    assert json.loads(rows[0]["summary_json"]) == first["summary"]
    assert json.loads(rows[1]["summary_json"]) == second["summary"]
    assert q["working_text"] in json.dumps(rows[1]["summary_json"])
    assert q["working_text"] not in json.dumps(rows[0]["summary_json"])


def test_unknown_project_raises(h):
    with pytest.raises(KeyError):
        h.save_briefing_snapshot("proj-missing")
    with pytest.raises(KeyError):
        h.briefing_changes_since_last_snapshot("proj-missing")


def test_store_clock_convention(h):
    pid = h.register_project("nyc")["project_id"]
    before = store.now()
    out = h.save_briefing_snapshot(pid)
    assert before <= out["recorded_at"] <= store.now()
