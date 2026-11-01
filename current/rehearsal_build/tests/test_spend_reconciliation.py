"""Spend reconciliation: pure settlement decisions plus sqlite-level persistence.

No network, no provider calls, no API keys. The sqlite tests reuse the real
``ai_spend`` table definition from ``app.ai.service.SCHEMA`` (not a copy),
combined with the real core ``app.store.SCHEMA`` for the deletion-isolation
check.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import spend
from app.ai.service import SCHEMA as AI_SCHEMA
from app.store import SCHEMA as CORE_SCHEMA
from app.ai.spend import is_fully_costed, known_sum, settle


def received(cost):
    return {"stage": "basic", "status": "response_received", "cost_usd": cost}


def attempted_unknown():
    return {"stage": "frame", "status": "attempted_charge_unknown",
            "usage": {}, "cost_usd": None}


# ---- pure decision rules ----

def test_validation_only_settles_known_charge_and_releases_remainder():
    stages = [received(0.010), received(0.005)]
    d = settle(0.05, stages, "validation_only")
    assert d.status == "failed_known_charge"
    assert d.actual_cost == pytest.approx(0.015)
    assert d.reserved_after == pytest.approx(0.015)
    assert d.releasable == pytest.approx(0.035)


def test_transport_unknown_retains_full_reservation():
    stages = [received(0.010), attempted_unknown()]
    d = settle(0.05, stages, "transport_unknown")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None
    assert d.reserved_after == pytest.approx(0.05)
    assert d.releasable == pytest.approx(0.0)


def test_validation_claim_with_uncosted_stage_keeps_unknown_charge():
    # A 'validation_only' claim contradicted by an unreceived/uncosted
    # stage must not release anything.
    d = settle(0.05, [received(0.010), attempted_unknown()], "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None
    assert d.reserved_after == pytest.approx(0.05)


@pytest.mark.parametrize("bad", [None, "0.01", float("nan"), float("inf"), -0.01, True])
def test_non_numeric_costs_are_not_known(bad):
    assert not is_fully_costed([received(bad)])
    d = settle(0.05, [received(bad)], "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None


def test_empty_stages_never_settle_known():
    for kind in ("none", "validation_only", "transport_unknown"):
        d = settle(0.05, [], kind)
        assert d.actual_cost is None
        assert d.status in ("completed_unknown_charge", "failed_unknown_charge")
        assert d.reserved_after == pytest.approx(0.05)


def test_reserved_after_never_below_known_partial_sum():
    # Reservation smaller than already-known charges: keep the known sum.
    d = settle(0.001, [received(0.010), attempted_unknown()], "transport_unknown")
    assert d.reserved_after == pytest.approx(0.010)
    assert d.reserved_after >= known_sum([received(0.010)])
    assert d.releasable == pytest.approx(0.0)


def test_success_paths_representable():
    done = settle(0.05, [received(0.010)], "none")
    assert (done.status, done.actual_cost) == ("completed_known", pytest.approx(0.010))
    unknown = settle(0.05, [received(0.010), attempted_unknown()], "none")
    assert unknown.status == "completed_unknown_charge"
    assert unknown.actual_cost is None
    assert unknown.reserved_after == pytest.approx(0.05)


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        settle(-1.0, [], "none")
    with pytest.raises(ValueError):
        settle(float("nan"), [], "none")
    with pytest.raises(ValueError):
        settle(0.05, [], "bogus_kind")
    with pytest.raises(ValueError):
        settle(0.05, "not-a-list", "none")


def test_no_reset_or_billed_cost_invention():
    assert not any(n.startswith(("reset", "clear", "delete", "wipe"))
                   for n in dir(spend))
    # Unknown outcomes never guess a zero/derived billed cost.
    d = settle(0.05, [attempted_unknown()], "transport_unknown")
    assert d.actual_cost is None


# ---- has_context=False: no ledger, no claim ----

@pytest.mark.parametrize("kind", ["none", "validation_only", "transport_unknown"])
def test_no_context_returns_unknown_no_context_for_every_kind(kind):
    d = settle(0.05, [received(0.010), received(0.005)], kind, has_context=False)
    assert d.status == "unknown_no_context"
    assert d.actual_cost is None
    assert d.reserved_after == pytest.approx(0.05)
    assert d.releasable == pytest.approx(0.0)


def test_no_context_ignores_garbage_stages():
    garbage = ["not-a-stage", None, 42, {"status": "response_received", "cost_usd": "junk"}]
    d = settle(0.05, garbage, "transport_unknown", has_context=False)
    assert d.status == "unknown_no_context"
    assert d.actual_cost is None
    assert d.reserved_after == pytest.approx(0.05)
    assert d.releasable == pytest.approx(0.0)


def test_no_context_keeps_full_reservation_despite_known_looking_stages():
    d = settle(0.05, [received(0.010)], "validation_only", has_context=False)
    assert d.status == "unknown_no_context"
    assert d.reserved_after == pytest.approx(0.05)
    assert d.releasable == pytest.approx(0.0)


def test_no_context_does_not_validate_failure_kind():
    # With no ledger there is no basis for any claim, including a
    # fail-fast on the kind; every kind maps to no-context.
    d = settle(0.05, [], "bogus_kind", has_context=False)
    assert d.status == "unknown_no_context"
    assert d.actual_cost is None


def test_no_context_invalid_reservation_still_raises():
    for bad in (float("nan"), float("inf"), -1.0, "0.05", True, None):
        with pytest.raises(ValueError):
            settle(bad, [received(0.010)], "transport_unknown", has_context=False)
        with pytest.raises(ValueError):
            settle(bad, [], "bogus_kind", has_context=False)


def test_summed_total_over_cap_takes_unknown_path():
    # Each $1B stage is individually valid (1e15 micro < 2**53 micro), but
    # the integer micro total ($10B = 1e16 micro) exceeds 2**53 micro
    # (~$9.007B): no fabricated known cost. Integer arithmetic only -- a
    # float check would round away the very excess being tested.
    from app.ai.spend import MAX_SAFE_MICRO_USD, MICRO_USD
    stages = [received(1_000_000_000.0)] * 10
    assert all(is_fully_costed([s]) for s in stages)
    assert 10 * 1_000_000_000 * MICRO_USD > MAX_SAFE_MICRO_USD
    for kind, status in (("validation_only", "failed_unknown_charge"),
                         ("none", "completed_unknown_charge"),
                         ("transport_unknown", "failed_unknown_charge")):
        d = settle(0.05, stages, kind)
        assert d.status == status
        assert d.actual_cost is None
        assert d.reserved_after >= 0.05


# ---- exact micro-dollar arithmetic ----

def test_decimal_exactness_0_1_plus_0_2():
    d = settle(0.50, [received(0.1), received(0.2)], "validation_only")
    assert d.status == "failed_known_charge"
    assert d.actual_cost == 0.3
    assert d.reserved_after == 0.3
    assert d.releasable == 0.2


def test_large_values_beyond_cap_take_unknown_path():
    # [1e16, 1.0, 1.0]: each single amount exceeds MAX_SAFE_MICRO_USD, so
    # no stage is known-costed; nothing is fabricated or rounded.
    stages = [received(1e16), received(1.0), received(1.0)]
    for kind in ("none", "validation_only", "transport_unknown"):
        d = settle(0.05, stages, kind)
        assert d.actual_cost is None
        assert d.status in ("completed_unknown_charge", "failed_unknown_charge")


def test_overflow_pair_never_yields_infinite_known_cost():
    stages = [received(1e308), received(1e308)]
    d = settle(0.05, stages, "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None
    assert d.reserved_after != float("inf") and d.reserved_after == d.reserved_after
    d2 = settle(0.05, stages, "none")
    assert d2.status == "completed_unknown_charge"
    assert d2.actual_cost is None


def test_huge_int_cost_raises_no_exception_and_is_not_known():
    stages = [received(10**400)]
    assert not is_fully_costed(stages)
    d = settle(0.05, stages, "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None
    d2 = settle(0.05, stages, "transport_unknown")
    assert d2.actual_cost is None


def test_unknown_path_floor_uses_micro_integers():
    # Partial known sum above the reservation keeps the known sum exactly.
    d = settle(0.001, [received(0.010), attempted_unknown()], "transport_unknown")
    assert d.reserved_after == 0.01
    assert d.releasable == 0.0


def test_unknown_path_floor_never_below_exact_micro_total():
    # Sol counterexample: [0.1, 0.2] with reservation 0 must keep a
    # reserved_after >= exactly 300000 micro; the old float-multiply
    # re-check masked a 1-ulp downward error (1/90071992547409920 below
    # 3/10). Verified with exact Fraction comparison, not floats.
    from fractions import Fraction
    from app.ai.spend import MICRO_USD
    d = settle(0, [received(0.1), received(0.2), attempted_unknown()],
               "transport_unknown")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None
    assert Fraction(d.reserved_after) >= Fraction(300000, MICRO_USD)
    assert d.releasable == 0.0
    # Awkward micro totals, exact at 1µ / 299999µ / 300000µ.
    for cost, micro in ((0.000001, 1), (0.299999, 299999), (0.3, 300000)):
        e = settle(0, [received(cost), attempted_unknown()], "transport_unknown")
        assert Fraction(e.reserved_after) >= Fraction(micro, MICRO_USD), (cost, micro)
        assert e.releasable == 0.0


def test_no_context_unrepresentable_reservation_raises():
    # 9007199254740993 cannot survive a float round-trip ($1 silently
    # lost); 10**400 cannot become a float at all (raw OverflowError).
    # Both raise ValueError -- on the no-context path and on the context
    # path alike (OverflowError is not a ValueError subclass, so
    # pytest.raises(ValueError) genuinely distinguishes them).
    with pytest.raises(ValueError):
        settle(9007199254740993, [received(0.010)], "transport_unknown", has_context=False)
    with pytest.raises(ValueError):
        settle(10**400, [received(0.010)], "transport_unknown", has_context=False)
    with pytest.raises(ValueError):
        settle(9007199254740993, [received(0.010)], "transport_unknown")
    with pytest.raises(ValueError):
        settle(10**400, [received(0.010)], "transport_unknown")


# ---- sqlite-level persistence (real schema) ----

def make_db(path=":memory:"):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(AI_SCHEMA)
    return db


def apply_settlement(db, job_id, decision):
    db.execute(
        "UPDATE ai_spend SET status=?, actual_cost=?, reservation=? WHERE job_id=?",
        (decision.status, decision.actual_cost, decision.reserved_after, job_id),
    )
    db.commit()


def test_sqlite_validation_only_releases_remainder():
    db = make_db()
    db.execute("INSERT INTO ai_spend VALUES(?,?,'running',NULL)", ("job-v", 0.05))
    db.commit()
    d = settle(0.05, [received(0.010), received(0.005)], "validation_only")
    apply_settlement(db, "job-v", d)
    row = db.execute("SELECT * FROM ai_spend WHERE job_id='job-v'").fetchone()
    assert row["status"] == "failed_known_charge"
    assert row["actual_cost"] == pytest.approx(0.015)
    assert row["reservation"] == pytest.approx(0.015)
    db.close()


def test_sqlite_transport_unknown_retains_reservation():
    db = make_db()
    db.execute("INSERT INTO ai_spend VALUES(?,?,'running',NULL)", ("job-t", 0.05))
    db.commit()
    d = settle(0.05, [received(0.010), attempted_unknown()], "transport_unknown")
    apply_settlement(db, "job-t", d)
    row = db.execute("SELECT * FROM ai_spend WHERE job_id='job-t'").fetchone()
    assert row["status"] == "failed_unknown_charge"
    assert row["actual_cost"] is None
    assert row["reservation"] == pytest.approx(0.05)
    db.close()


def test_sqlite_question_deletion_does_not_delete_spend():
    # ai_spend is content-free and keyed by job_id (no question FK, no
    # cascade): deleting a question's rows elsewhere must leave it intact.
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(CORE_SCHEMA)
    db.executescript(AI_SCHEMA)
    assert db.execute("PRAGMA foreign_key_list(ai_spend)").fetchall() == []
    cols = {r["name"] for r in db.execute("PRAGMA table_info(ai_spend)")}
    assert cols == {"job_id", "reservation", "status", "actual_cost"}
    db.execute(
        "INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?)",
        ("q-1", "p-1", "text", None, "working", "ok", "remember",
         "open", "2026-01-01T00:00:00+00:00"),
    )
    db.execute("INSERT INTO ai_spend VALUES(?,?,?,?)", ("job-q", 0.015, "failed_known_charge", 0.015))
    db.commit()
    db.execute("DELETE FROM questions WHERE id=?", ("q-1",))
    db.commit()
    rows = db.execute("SELECT * FROM ai_spend").fetchall()
    assert len(rows) == 1 and rows[0]["job_id"] == "job-q"
    assert rows[0]["reservation"] == pytest.approx(0.015)
    db.close()


def test_sqlite_restart_sees_settled_state(tmp_path):
    path = str(tmp_path / "spend.db")
    db = make_db(path)
    db.execute("INSERT INTO ai_spend VALUES(?,?,'running',NULL)", ("job-r", 0.05))
    db.commit()
    d = settle(0.05, [received(0.010), received(0.005)], "validation_only")
    apply_settlement(db, "job-r", d)
    db.close()
    # Fresh connection == restart: settled state survives.
    again = sqlite3.connect(path)
    again.row_factory = sqlite3.Row
    row = again.execute("SELECT * FROM ai_spend WHERE job_id='job-r'").fetchone()
    assert row["status"] == "failed_known_charge"
    assert row["actual_cost"] == pytest.approx(0.015)
    assert row["reservation"] == pytest.approx(0.015)
    again.close()
