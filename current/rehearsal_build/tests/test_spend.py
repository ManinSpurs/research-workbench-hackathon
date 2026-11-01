"""Exact micro-USD -> decimal-dollar conversion in ``app.ai.spend``.

No network, no provider calls, no API keys. Every assertion here is an
exact decimal-string comparison or exact ``Fraction`` comparison; no
assertion accepts a float approximation of a settled amount.

Regression context: ``_micro_to_float`` used to be
``micro / MICRO_USD``, whose float division loses the last micro when
the float spacing is wider than 1 micro-USD (above $4.5036B). At the
documented cap (2**53 micro-USD, ~$9.007B) the spacing is 2 micro-USD,
so a total of 8589934592000001 micro-USD ($8589934592.000001) rendered
as ``8589934592.000002``. The conversion is now integer-only
(``micro_usd_string``) and the exact decimal is carried in the
``*_exact`` Settlement fields, which ``json.dumps`` accepts as strings.
"""
import dataclasses
import json
import random
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai.spend import (
    MAX_SAFE_MICRO_USD,
    MICRO_USD,
    is_fully_costed,
    known_sum,
    micro_usd_string,
    settle,
)

MONEY6 = re.compile(r"^-?\d+\.\d{6}$")

#: The cap as an exact decimal-dollar string and as the float callers pass.
CAP_STRING = "9007199254.740992"
CAP_DOLLARS = float(CAP_STRING)


def received(cost):
    return {"stage": "basic", "status": "response_received", "cost_usd": cost}


def attempt_unknown():
    return {"stage": "frame", "status": "attempted_charge_unknown",
            "usage": {}, "cost_usd": None}


def micro_of(dollars) -> int:
    """Independent reference conversion (Decimal, half-up) -- not spend.py's."""
    return int((Decimal(str(dollars)) * MICRO_USD).to_integral_value(rounding=ROUND_HALF_UP))


# ---- the conversion function itself ----

@pytest.mark.parametrize("micro, expected", [
    (0, "0.000000"),
    (1, "0.000001"),
    (999999, "0.999999"),
    (1000000, "1.000000"),
    (1000001, "1.000001"),
    (15000, "0.015000"),
    (300000, "0.300000"),
    (8589934592000001, "8589934592.000001"),          # the reported value
    (8589934592000002, "8589934592.000002"),
    (2**52, "4503599627.370496"),                     # last exactly-spaced cap
    (MAX_SAFE_MICRO_USD, CAP_STRING),                 # documented cap
])
def test_micro_usd_string_is_exact(micro, expected):
    assert micro_usd_string(micro) == expected


@pytest.mark.parametrize("micro", [0, 1, 999999, 1000000, 8589934592000001,
                                   2**52, MAX_SAFE_MICRO_USD])
def test_micro_usd_string_round_trips_through_decimal(micro):
    # Exact string -> Decimal -> micro integer is lossless for every value,
    # including the ones no float can hold.
    assert micro_of(micro_usd_string(micro)) == micro
    assert MONEY6.match(micro_usd_string(micro))
    assert len(micro_usd_string(micro).split(".")[1]) == 6


def test_conversion_is_integer_only_not_float_division():
    # Float division collapses .000001 and .000002 here; the string does not.
    micro = 8589934592000001
    assert micro / MICRO_USD == 8589934592.000002
    assert micro_usd_string(micro) == "8589934592.000001"
    # No float exists between the two neighbours: the exact value lies
    # strictly inside the bracket, which is why a float cannot carry it.
    lo = 8589934592.0
    hi = float(micro_usd_string(8589934592000002))
    assert Fraction(lo) < Fraction(micro, MICRO_USD) < Fraction(hi)


def test_projection_is_nearest_float_and_exact_below_2_52():
    # Below 2**52 micro-USD the float projection equals exact division; at
    # and above it, the projection is the nearest float (<= half a micro
    # away) and the exact decimal stays in the string.
    for micro in (0, 1, 999999, 1000000, 300000, 15000, 2**52 - 1, 2**52):
        assert float(micro_usd_string(micro)) == micro / MICRO_USD, micro
    rnd = random.Random(0)
    for _ in range(500):
        micro = rnd.randrange(0, MAX_SAFE_MICRO_USD + 1)
        exact = Fraction(micro, MICRO_USD)
        assert micro_of(micro_usd_string(micro)) == micro
        assert abs(Fraction(float(micro_usd_string(micro))) - exact) <= Fraction(1, MICRO_USD)


# ---- settle: exact settled amounts, string equality ----

@pytest.mark.parametrize("cost, micro, expected", [
    (0.0, 0, "0.000000"),                       # zero charge
    (0.000001, 1, "0.000001"),                  # 1 micro
    (0.999999, 999999, "0.999999"),             # 999999 micro
    (1.0, 1000000, "1.000000"),                 # 1000000 micro
    (4294967296.000001, 4294967296000001, "4294967296.000001"),
    (CAP_DOLLARS, MAX_SAFE_MICRO_USD, CAP_STRING),   # documented cap boundary
])
def test_settled_known_cost_is_exact(cost, micro, expected):
    assert micro_of(cost) == micro
    assert is_fully_costed([received(cost)])
    for kind, status in (("validation_only", "failed_known_charge"),
                         ("none", "completed_known")):
        d = settle(CAP_DOLLARS, [received(cost)], kind)
        assert d.status == status
        assert d.actual_cost_exact == expected
        assert d.reserved_after_exact == expected
        assert d.releasable_exact == micro_usd_string(MAX_SAFE_MICRO_USD - micro)
        # The float is the nearest float of that same exact string.
        assert d.actual_cost == float(expected)
        assert d.reserved_after == float(expected)


def test_reported_defect_value_8589934592000001_micros():
    # Two in-cap amounts whose integer micro total is the reported value
    # (both float literals are exact at micro scale: $2**32 + 1µ and $2**32).
    stages = [received(4294967296.000001), received(4294967296.0)]
    assert [micro_of(s["cost_usd"]) for s in stages] == [4294967296000001,
                                                         4294967296000000]
    total_micro = 8589934592000001
    assert total_micro < MAX_SAFE_MICRO_USD

    for kind, status in (("validation_only", "failed_known_charge"),
                         ("none", "completed_known")):
        d = settle(CAP_DOLLARS, stages, kind)
        assert d.status == status
        # Exact decimal string: ...000001, never the old $...000002 render.
        assert d.actual_cost_exact == "8589934592.000001"
        assert d.reserved_after_exact == "8589934592.000001"
        assert d.releasable_exact == micro_usd_string(MAX_SAFE_MICRO_USD - total_micro)
        assert d.releasable_exact == "417264662.740991"
        # The float cannot hold ...000001 (spacing is 2 micro-USD here);
        # it is the nearest float of the exact string, never a division
        # remainder that silently claims a whole extra micro.
        assert d.actual_cost == float("8589934592.000001") == 8589934592.000002
        assert Fraction(d.actual_cost) - Fraction(total_micro, MICRO_USD) < Fraction(1, MICRO_USD)


def test_reported_defect_releasable_stays_exact():
    # Reservation exactly one micro above the total: the released remainder
    # is 1 micro, still exact as a string. ($8589934592.000002 is the float
    # nearest that decimal, so it round-trips to micro ...000002 exactly.)
    reservation = 8589934592.000002
    assert micro_of(reservation) == 8589934592000002
    stages = [received(4294967296.000001), received(4294967296.0)]
    d = settle(reservation, stages, "validation_only")
    assert d.status == "failed_known_charge"
    assert d.actual_cost_exact == "8589934592.000001"
    assert d.releasable_exact == "0.000001"
    # The float is the nearest-float projection of that exact string (1e-06
    # has no exact binary form, so the string, not the float, is the truth).
    assert d.releasable == float(micro_usd_string(1)) == float("0.000001")
    assert abs(Fraction(d.releasable) - Fraction(1, MICRO_USD)) < Fraction(1, MICRO_USD * 1000)


def test_cap_boundary_accepted_and_one_micro_over_is_unknown():
    # Exactly at the cap: known, carried as the cap's exact string.
    at_cap = settle(CAP_DOLLARS, [received(CAP_DOLLARS)], "validation_only")
    assert at_cap.status == "failed_known_charge"
    assert at_cap.actual_cost_exact == CAP_STRING
    # One micro over the cap: unknown path, no fabricated cost.
    over = [received(CAP_DOLLARS), received(0.000001)]
    assert all(is_fully_costed([s]) for s in over)
    total = micro_of(CAP_DOLLARS) + micro_of(0.000001)
    assert total == MAX_SAFE_MICRO_USD + 1
    d = settle(CAP_DOLLARS, over, "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None and d.actual_cost_exact is None
    # The floor keeps the known partial sum, which here exceeds the
    # reservation by that one micro -- still exact as a string.
    assert d.reserved_after_exact == "9007199254.740993"
    assert d.releasable_exact == "0.000000"
    assert d.reserved_after == float("9007199254.740993")


def test_exact_fields_are_json_serializable_and_consistent():
    d = settle(CAP_DOLLARS, [received(4294967296.000001), received(4294967296.0)],
               "validation_only")
    payload = dataclasses.asdict(d)
    # The receipt/digest path dumps these with allow_nan=False: str is safe.
    text = json.dumps(payload, sort_keys=True, allow_nan=False)
    assert '"actual_cost_exact": "8589934592.000001"' in text
    for name in ("actual_cost", "reserved_after", "releasable"):
        assert isinstance(payload[name], float)
        assert float(payload[name + "_exact"]) == payload[name]
    # Unknown cost never invents an exact amount either.
    unknown = settle(0.05, [attempt_unknown()], "transport_unknown")
    assert unknown.actual_cost is None and unknown.actual_cost_exact is None
    json.dumps(dataclasses.asdict(unknown), sort_keys=True, allow_nan=False)


def test_unknown_path_floor_never_below_exact_micro_total():
    # The floor still steps up past float resolution: reserved_after_exact
    # carries the exact total, the float is >= the exact fraction. Both
    # costs below are exact at micro scale as float literals.
    for cost, micro in ((0.000001, 1), (0.299999, 299999), (0.3, 300000),
                        (4294967296.000001, 4294967296000001),
                        (8589934592.000002, 8589934592000002)):
        assert micro_of(cost) == micro
        e = settle(0, [received(cost), attempt_unknown()], "transport_unknown")
        assert e.status == "failed_unknown_charge"
        assert e.actual_cost is None
        assert e.reserved_after_exact == micro_usd_string(micro)
        assert Fraction(e.reserved_after) >= Fraction(micro, MICRO_USD)
        assert e.releasable == 0.0 and e.releasable_exact == "0.000000"


# ---- guards stay intact ----

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.01, -1, "0.01", None, True])
def test_invalid_costs_are_not_known(bad):
    assert not is_fully_costed([received(bad)])
    d = settle(0.05, [received(bad)], "validation_only")
    assert d.status == "failed_unknown_charge"
    assert d.actual_cost is None and d.actual_cost_exact is None


def test_invalid_reservations_rejected():
    for bad in (float("nan"), float("inf"), -1.0, "0.05", True, None,
                MAX_SAFE_MICRO_USD + 1, 10**400):
        with pytest.raises(ValueError):
            settle(bad, [received(0.010)], "validation_only")
        with pytest.raises(ValueError):
            settle(bad, [received(0.010)], "transport_unknown", has_context=False)


def test_overflow_beyond_cap_never_fabricates_a_cost():
    for stages in ([received(1e16), received(1.0), received(1.0)],
                   [received(1e308), received(1e308)],
                   [received(10**400)]):
        d = settle(0.05, stages, "validation_only")
        assert d.status == "failed_unknown_charge"
        assert d.actual_cost is None and d.actual_cost_exact is None
        assert d.reserved_after != float("inf") and d.releasable >= 0.0


def test_double_settle_is_deterministic_and_double_release_is_zero():
    stages = [received(0.010), received(0.005)]
    first = settle(0.05, stages, "validation_only")
    assert first.actual_cost_exact == "0.015000"
    assert first.releasable_exact == "0.035000"
    # Re-settling the same row yields the identical decision: no drift and
    # no second release (settle is pure and reservation-independent here).
    assert settle(0.05, stages, "validation_only") == first
    # Re-settling against the already-settled reservation releases nothing.
    again = settle(first.reserved_after, stages, "validation_only")
    assert again.actual_cost_exact == "0.015000"
    assert again.reserved_after_exact == "0.015000"
    assert again.releasable == 0.0 and again.releasable_exact == "0.000000"


def test_allocated_remainder_is_consistent():
    # releasable + reserved_after == reservation, exactly, at micro scale.
    # All three amounts are exact float literals at micro precision.
    stages = [received(2000000000.000001), received(1000000000.000003)]
    assert [micro_of(s["cost_usd"]) for s in stages] == [2000000000000001,
                                                         1000000000000003]
    d = settle(4000000000.000005, stages, "validation_only")
    assert d.status == "failed_known_charge"
    assert d.actual_cost_exact == "3000000000.000004"
    assert d.releasable_exact == "1000000000.000001"
    total = micro_of(d.reserved_after_exact) + micro_of(d.releasable_exact)
    assert total == micro_of("4000000000.000005") == 4000000000000005


def test_known_sum_saturates_at_cap_without_float_overflow():
    stages = [received(1_000_000_000.0)] * 10
    assert known_sum(stages) == CAP_DOLLARS
    assert micro_usd_string(MAX_SAFE_MICRO_USD) == CAP_STRING


# ---- keeping an unspent reservation must not move it ----

def attempt_unknown_stage():
    return {"stage": "frame", "status": "attempted_charge_unknown",
            "usage": {}, "cost_usd": None}


@pytest.mark.parametrize("reserve", [
    1.319424,        # muse-wonder-1.0 x3 calls; the reported ledger value
    0.1050624,       # muse-spark-1.3-contributor x1: sub-micro residue
    0.2113536,       # ... x2: also NOT a whole number of micros
    0.319424,        # muse-spark-1.3 x2
    14.548992,       # muse-wonder-1.0 x3 at max_output_tokens=32768
    0.0025,          # segmentation flat reservation
    0.18,            # transcription per-hour unit
])
def test_unspent_reservation_is_kept_bit_identical(reserve):
    """Keeping the whole reservation must store that exact float back.

    Regression (``maximum_reserved_usd`` vs the ``ai_spend`` ledger): the
    unknown-charge paths converted the kept amount with
    ``_micro_to_float_floor``, which steps up one ulp whenever the nearest
    float of the exact decimal sits just below it. For 1.319424 (exactly
    1319424 micro) that stored 1.3194240000000002, so
    ``settings()['local_committed_or_reserved_usd']`` no longer equalled
    the ``prepare`` report the budget was checked against.

    Asserting bit equality (``==``, not ``approx``) is the honest check:
    nothing was charged, so nothing may move. Each parametrised value is
    what ``AIService.prepare`` produces via ``round(per_call * calls, 8)``;
    0.1050624 and 0.2113536 are deliberately *not* whole micro amounts
    (105062.4 and 211353.6 micro), which is why canonicalising the
    reservation through a micro round-trip would corrupt it instead.
    """
    for kind in ("none", "validation_only", "transport_unknown"):
        d = settle(reserve, [attempt_unknown_stage()], kind)
        assert d.actual_cost is None
        assert d.reserved_after == reserve, (kind, d.reserved_after)
        assert d.releasable == 0.0


def test_kept_reservations_sum_to_the_prepare_total():
    """Many unspent rows: the ledger total still equals what prepare reported.

    ``service.settings()`` sums the stored floats, so every kept reservation
    must come back bit-identical for the sum to match. Uses a mix of whole-
    and sub-micro reservations -- the shapes that disagreed before the fix.
    """
    reserves = [0.1050624, 0.2113536, 1.319424, 0.319424, 0.0025, 0.18]
    kept = [settle(r, [attempt_unknown_stage()], "none").reserved_after
            for r in reserves]
    assert kept == reserves
    assert sum(kept) == sum(reserves)


def test_kept_partial_above_reservation_still_never_rounds_down():
    """The conservative floor must survive: a known partial above the
    reservation is still projected *up* (never below the exact micro total),
    so the fix above cannot have silently disabled the floor.
    """
    d = settle(0.001, [received(0.0025), attempt_unknown_stage()],
               "transport_unknown")
    assert d.status == "failed_unknown_charge"
    assert d.reserved_after_exact == "0.002500"
    assert Fraction(d.reserved_after) >= Fraction(2500, MICRO_USD)
    # Same guarantee past float resolution, where the floor is load-bearing.
    e = settle(0, [received(4294967296.000001), attempt_unknown_stage()],
               "transport_unknown")
    assert e.reserved_after_exact == "4294967296.000001"
    assert Fraction(e.reserved_after) >= Fraction(4294967296000001, MICRO_USD)
