"""Slice D3 tests -- adaptive readiness wired into the briefing path (inventory item D1).

Wires ``_project_briefing`` (the function the design names: §5 "``launch_briefing``
builds each row from that project's ``_project_briefing`` only"; ``launch_briefing``
inherits the same row, including its new ``lines`` key). No live/API calls:
``Harness`` falls back to the offline adapter, so capture/decide need no model
call; mock transports only, zero credits. Read-only wiring: ``SELECT``s only,
no DB writes anywhere in the new path (``compose_readiness`` is pure).

What this pins (design ``current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md``):
  1. ``_project_briefing`` gains a read-only adaptation section for the project's
     active pack: composed readiness ``lines`` from ``compose_readiness``,
     including the approved-overlay version + benchmark refs when approved
     (§6.1; AD03 "benchmark refs filed before approval" gate in
     ``adaptive_readiness.py``).
  2. Pinned past decisions/history are never reinterpreted: the wiring makes no
     DB writes and decision rows render byte-identically before/after (AD04).
  3. Absent overlay/projects without adaptation data -> section absent or empty,
     never fabricated maturity claims; cold state reads as cold (§6.1).
  4. Read-only: identical DB dump before/after (assert via dump compare).
     Deterministic output: repeat reads are equal.

Owned-file note: the harness edit region is strictly the new ``_adaptation_lines``
helper + the additive ``lines`` key on the ``_project_briefing``/``launch_briefing``
returns; the briefing-ready-now worker owns the ``ready_now`` lines elsewhere in
the same file and is untouched. ``api.py`` needed NO route change: the existing
``GET /projects/{pid}/briefing`` and ``GET /briefing`` routes serialize the dicts
as-is, so the new ``lines`` key flows through with zero code change.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import adaptive_readiness as ar
from app.harness import Harness
from app.packs import DEFAULT_PACK

SOCIAL = "social_science"


def _harness(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    h = Harness(str(tmp_path / "wb.db"), data)
    yield h
    h.db.close()


harness = pytest.fixture(_harness)


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def capture(h, pid, text, pack_id=SOCIAL):
    return h.capture_evaluation_input(pid, text, None, pack_id)


def decide(h, pid, qid, verdict, reason):
    return h.decide(pid, qid, verdict, reason, None)


def dump(h):
    with sqlite3.connect(h.db.execute("PRAGMA database_list").fetchone()[2]) as db:
        return "\n".join(db.iterdump())


def approved_adaptation(overlay_version="v2"):
    return {
        "version": {"status": "approved", "overlay_version": overlay_version,
                    "evidence_refs": ["dec-1", "bm-1"], "benchmark_refs": ["bm-1"]},
        "benchmark_report": {"cases": [{"case_ref": "F1", "outcome": "supported"},
                                       {"case_ref": "F2", "outcome": "partial"}]},
    }


# ---- cold project: section present as cold, never fabricated maturity --------

def test_cold_project_reads_as_cold(harness):
    h = harness
    pid = seed(h)
    b = h._project_briefing(pid)
    assert b["model_calls"] == 0
    assert isinstance(b["lines"], list) and b["lines"]
    assert b["lines"][0] == (
        "Readiness for project %s + pack %s: cold." % (pid, DEFAULT_PACK))
    joined = "\n".join(b["lines"])
    assert "overlay" not in joined.lower() or "no eligible" in joined.lower()
    for forbidden in ("approved", "active", "score", "maturity", "progress",
                      "deadline", "overdue", "%"):
        assert forbidden not in joined.lower(), forbidden
    # launch row inherits the same section verbatim
    row = next(r for r in h.launch_briefing()["rows"] if r["project_id"] == pid)
    assert row["lines"] == b["lines"]
    assert row["model_calls"] == 0


# ---- active-pack section counts this project's own confirmed decisions -------

def test_active_pack_section_counts_confirmed_decisions(harness):
    h = harness
    pid = seed(h)
    qid = capture(h, pid, "Can I study filing-to-start time?")
    decide(h, pid, qid, "adopt", "filing date establishes the start")
    b = h._project_briefing(pid)
    joined = "\n".join(b["lines"])
    assert "accumulating" in joined
    assert "1 eligible records" in joined
    assert "confirmed decisions 1" in joined
    assert "Guidance text only; grants no tools, scope, or budget." in joined


# ---- approved adaptation dict: version + benchmark refs rendered -------------

def test_approved_adaptation_renders_version_and_benchmark_refs(harness):
    h = harness
    pid = seed(h)
    qid = capture(h, pid, "Can I study filing-to-start time?")
    decide(h, pid, qid, "adopt", "filing date establishes the start")
    adapt = approved_adaptation()
    b = h._project_briefing(pid, adaptation=adapt)
    joined = "\n".join(b["lines"])
    assert "Readiness for project %s + pack %s: approved." % (pid, DEFAULT_PACK) in joined
    assert "Overlay v2 active for new work only; old work unchanged." in joined
    assert "Evidence refs: dec-1, bm-1." in joined
    assert "Benchmark refs: bm-1." in joined
    assert "Benchmark report: 2 cases with refs and outcomes, no scores." in joined
    assert ar.PROPOSAL_BADGE not in joined  # approved is not a proposal
    row = next(r for r in h.launch_briefing({pid: adapt})["rows"]
               if r["project_id"] == pid)
    assert row["lines"] == b["lines"]


# ---- AD04: decision history byte-identical before/after -----------------------

def test_decision_history_byte_identical_before_after(harness):
    h = harness
    pid = seed(h)
    qid = capture(h, pid, "Can I study filing-to-start time?")
    decide(h, pid, qid, "adopt", "filing date establishes the start")
    pin_before = dict(h.db.execute(
        "SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone())
    decisions_before = [dict(r) for r in h.db.execute(
        "SELECT * FROM decisions ORDER BY rowid")]
    briefing_before = json.dumps(h.briefing(pid), sort_keys=True)
    project_before = json.dumps(h._project_briefing(pid), sort_keys=True)
    proto_row_before = {k: v for k, v in json.loads(project_before).items()
                        if k != "lines"}

    adapt = approved_adaptation()
    b = h._project_briefing(pid, adaptation=adapt)
    assert "approved" in "\n".join(b["lines"])
    row = next(r for r in h.launch_briefing({pid: adapt})["rows"]
               if r["project_id"] == pid)
    assert row["lines"] == b["lines"]

    # Old work untouched: same pin, same decision rows, same pack briefing.
    assert dict(h.db.execute(
        "SELECT * FROM question_contexts WHERE question_id=?", (qid,)).fetchone()) == pin_before
    assert [dict(r) for r in h.db.execute(
        "SELECT * FROM decisions ORDER BY rowid")] == decisions_before
    assert json.dumps(h.briefing(pid), sort_keys=True) == briefing_before
    after = json.loads(json.dumps(h._project_briefing(pid), sort_keys=True))
    assert {k: v for k, v in after.items() if k != "lines"} == proto_row_before
    assert after["parts"]["where_you_left_off"] == json.loads(project_before)["parts"]["where_you_left_off"]
    assert "filing date establishes the start" in json.dumps(
        h._project_briefing(pid)["parts"]["where_you_left_off"])


# ---- read-only: identical DB dump before/after --------------------------------

def test_wiring_writes_nothing(harness):
    h = harness
    pid_a, pid_b = seed(h), seed(h)
    qa = capture(h, pid_a, "Can I study filing-to-start time?")
    decide(h, pid_a, qa, "adopt", "a recorded reason")
    before = dump(h)
    adapt = approved_adaptation()
    h._project_briefing(pid_a, adaptation=adapt)
    h._project_briefing(pid_b)
    h.launch_briefing({pid_a: adapt})
    h.launch_briefing()
    assert dump(h) == before


# ---- deterministic output -----------------------------------------------------

def test_wiring_output_deterministic(harness):
    h = harness
    pid = seed(h)
    qid = capture(h, pid, "Can I study filing-to-start time?")
    decide(h, pid, qid, "adopt", "a recorded reason")
    adapt = approved_adaptation()
    assert h._project_briefing(pid, adaptation=adapt) == h._project_briefing(pid, adaptation=adapt)
    assert h.launch_briefing({pid: adapt}) == h.launch_briefing({pid: adapt})
    assert h._project_briefing(pid) == h._project_briefing(pid)


# ---- per-project isolation: adaptation dicts never cross projects -------------

def test_adaptation_scoped_to_single_project(harness):
    h = harness
    pid_p, pid_q = seed(h), seed(h)
    qp = capture(h, pid_p, "Can I study filing-to-start time?")
    decide(h, pid_p, qp, "adopt", "reason alpha")
    qq = capture(h, pid_q, "Can I study time to final approval?")
    decide(h, pid_q, qq, "reject", "reason beta")
    adapt = approved_adaptation()
    payload = h.launch_briefing({pid_p: adapt})
    row_p = next(r for r in payload["rows"] if r["project_id"] == pid_p)
    row_q = next(r for r in payload["rows"] if r["project_id"] == pid_q)
    assert "approved" in "\n".join(row_p["lines"])
    assert "v2" in "\n".join(row_p["lines"])
    assert "approved" not in "\n".join(row_q["lines"])
    assert "v2" not in "\n".join(row_q["lines"])
    assert "cold" not in "\n".join(row_q["lines"]).lower() or "accumulating" in "\n".join(row_q["lines"])
    before = dump(h)
    h.launch_briefing({pid_p: adapt})
    assert dump(h) == before
