"""Secretary nudge modes + per-project snooze: storage foundation slice (C8/C9).

Implements the DESIGN's (current/SECRETARY_NUDGE_MODES_DESIGN_EN.md §4)
smallest first slice only: per-project mode + snooze storage
(secretary_nudge_settings table) and harness get/set methods with the
design's validation (unknown mode rejected; snooze requires a recorded
basis — never silent defaults that change behaviour).

Storage + get/set only: no UI, no scheduling, no actual nudging /
notifications, no briefing wiring, no auto-switching (modes are explicit
researcher settings per the design). Zero model calls. Direct-harness
fixture (no sockets, no subprocess, no model calls) following
tests/test_dot_safety.py.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import harness as module  # noqa: E402
from app import store  # noqa: E402
from app.harness import Conflict  # noqa: E402
from test_dot_safety import StubAdapter  # noqa: E402  (shared loopback-guarded harness fixture)

MODES = ("quiet", "standard", "nag", "top")


@pytest.fixture
def h(tmp_path, monkeypatch):
    stub = StubAdapter()
    monkeypatch.setattr(module, "ModelAdapter", lambda: stub)
    data = tmp_path / "data"
    import shutil
    shutil.copytree(ROOT / "data", data)
    harness = module.Harness(str(tmp_path / "nudge.db"), data)
    yield harness
    harness.db.close()


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def count(h):
    return h.db.execute("SELECT count(*) c FROM secretary_nudge_settings").fetchone()["c"]


# ------------------------------------------------------------------ schema (§4, design lines 173-197)
def test_schema_is_additive_and_exact(h):
    tables = {r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "secretary_nudge_settings" in tables
    # Parallel slices' tables land by their own owners; this slice adds one only.
    info = {c["name"]: c for c in h.db.execute("PRAGMA table_info('secretary_nudge_settings')")}
    assert set(info) == {"project_id", "mode", "snoozed_until", "snooze_set_at", "updated_at"}
    assert info["mode"]["dflt_value"] == "'standard'"
    assert info["snoozed_until"]["notnull"] == 0 and info["snooze_set_at"]["notnull"] == 0
    assert info["updated_at"]["notnull"] == 1 and info["project_id"]["notnull"] == 1
    assert "secretary_nudge_settings" in {t for t, _ in store.text_columns(h.db)}


def test_existing_projects_default_honestly(h):
    """Absent row = no mode set (rendered as such), never a silent default (brief §5)."""
    pid = seed(h)
    before = count(h)
    got = h.get_nudge_settings(pid)
    assert got == {"project_id": pid, "mode": None, "snoozed_until": None,
                   "snooze_set_at": None, "updated_at": None, "model_calls": 0}
    assert count(h) == before, "a read must not write"


# ------------------------------------------------------------------ set/get round-trip
@pytest.mark.parametrize("mode", MODES)
def test_set_get_round_trip_all_modes(h, mode):
    pid = seed(h)
    out = h.set_nudge_mode(pid, mode)
    assert out["project_id"] == pid and out["mode"] == mode
    assert out["snoozed_until"] is None and out["updated_at"] and out["model_calls"] == 0
    assert h.get_nudge_settings(pid)["mode"] == mode


def test_mode_overwrite_keeps_snooze(h):
    pid = seed(h)
    h.set_nudge_snooze(pid, "2026-11-03", "2026-10-27T00:00:00+00:00")
    h.set_nudge_mode(pid, "quiet")
    got = h.get_nudge_settings(pid)
    assert got["mode"] == "quiet" and got["snoozed_until"] == "2026-11-03"
    h.set_nudge_mode(pid, "top")
    assert h.get_nudge_settings(pid)["mode"] == "top"
    assert h.get_nudge_settings(pid)["snoozed_until"] == "2026-11-03"


def test_snooze_set_lift_carry_over_mode(h):
    pid = seed(h)
    h.set_nudge_mode(pid, "nag")
    out = h.set_nudge_snooze(pid, "2026-11-03", "2026-10-27T00:00:00+00:00")
    assert out["snoozed_until"] == "2026-11-03" and out["snooze_set_at"]
    assert out["mode"] == "nag" and out["model_calls"] == 0
    assert h.get_nudge_settings(pid)["snoozed_until"] == "2026-11-03"
    lifted = h.clear_nudge_snooze(pid)
    assert lifted["snoozed_until"] is None and lifted["mode"] == "nag"
    assert h.get_nudge_settings(pid)["snoozed_until"] is None


# ------------------------------------------------------------------ validation: unknown mode rejected
@pytest.mark.parametrize("bad_mode", [None, "", "   ", "Angry", "ANGRY", "loud",
                                      "remind-often", "direct", "standard ", 123])
def test_unknown_mode_rejected_nothing_stored(h, bad_mode):
    pid = seed(h)
    before = count(h)
    with pytest.raises(Conflict):
        h.set_nudge_mode(pid, bad_mode)
    assert count(h) == before, "rejected mode set must store nothing"
    assert h.get_nudge_settings(pid)["mode"] is None, "no silent default after rejection"


# ------------------------------------------------------------------ validation: snooze recorded basis
@pytest.mark.parametrize("bad_until", [None, "", "   ", 123])
def test_snooze_without_until_rejected(h, bad_until):
    pid = seed(h)
    before = count(h)
    with pytest.raises(Conflict):
        h.set_nudge_snooze(pid, bad_until, "2026-10-27T00:00:00+00:00")
    assert count(h) == before, "rejected snooze must store nothing"


@pytest.mark.parametrize("bad_basis", [None, "", "   ", 123])
def test_snooze_without_recorded_basis_rejected(h, bad_basis):
    """snooze_set_at omission/blank/non-string rejected: no silent defaults (brief)."""
    pid = seed(h)
    before = count(h)
    with pytest.raises((Conflict, TypeError)):
        h.set_nudge_snooze(pid, "2026-11-03", bad_basis)
    assert count(h) == before, "rejected snooze must store nothing; no default fills the omission"


def test_snooze_without_any_basis_rejected(h):
    pid = seed(h)
    before = count(h)
    with pytest.raises((Conflict, TypeError)):
        h.set_nudge_snooze(pid, "2026-11-03")
    assert count(h) == before


# ------------------------------------------------------------------ isolation
def test_cross_project_isolation(h):
    pid_a, pid_b = seed(h), seed(h)
    h.set_nudge_mode(pid_a, "quiet")
    h.set_nudge_snooze(pid_b, "2026-11-03", "2026-10-27T00:00:00+00:00")
    assert h.get_nudge_settings(pid_a)["mode"] == "quiet"
    assert h.get_nudge_settings(pid_a)["snoozed_until"] is None
    assert h.get_nudge_settings(pid_b)["mode"] == "standard"
    assert h.get_nudge_settings(pid_b)["snoozed_until"] == "2026-11-03"


# ------------------------------------------------------------------ error paths
def test_unknown_project_surfaces_keyerror(h):
    with pytest.raises(KeyError):
        h.get_nudge_settings("proj-missing")
    with pytest.raises(KeyError):
        h.set_nudge_mode("proj-missing", "quiet")
    with pytest.raises(KeyError):
        h.set_nudge_snooze("proj-missing", "2026-11-03", "basis")
    with pytest.raises(KeyError):
        h.clear_nudge_snooze("proj-missing")


def test_model_free_and_read_path_writes_nothing(h):
    pid = seed(h)
    h.set_nudge_mode(pid, "quiet")
    before = "\n".join(h.db.iterdump())
    got = h.get_nudge_settings(pid)
    assert got["model_calls"] == 0
    assert "\n".join(h.db.iterdump()) == before, "get path must be read-only"
    assert h.set_nudge_mode(pid, "standard")["model_calls"] == 0


def test_only_settings_table_writes(h):
    """Evidence/decision/event rows untouched: dump-identical apart from the new table."""
    pid = seed(h)
    before = {(t, tuple(r)) for t in
              [r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                         " AND name NOT LIKE 'sqlite_%' AND name != 'secretary_nudge_settings'")]
              for r in h.db.execute(f'SELECT * FROM "{t}"')}
    h.set_nudge_mode(pid, "quiet")
    h.set_nudge_snooze(pid, "2026-11-03", "2026-10-27T00:00:00+00:00")
    after = {(t, tuple(r)) for t in
             [r[0] for r in h.db.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                        " AND name NOT LIKE 'sqlite_%' AND name != 'secretary_nudge_settings'")]
             for r in h.db.execute(f'SELECT * FROM "{t}"')}
    assert after == before
