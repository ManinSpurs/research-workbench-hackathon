"""Spend settlement decisions for AI jobs (pure, no I/O).

Settles the local allowance commitment for one job from its conservative
reservation and the per-stage accounting returned so far. It never contacts
a provider, never reads secrets, and never invents a provider billed cost:
``actual_cost`` is only the sum of numeric ``cost_usd`` values computed from
returned usage, or ``None`` when the total charge is not fully known.
Provider billed cost (``provider_billed_cost_usd``) stays ``None``/UNKNOWN;
that verification is out of scope here.

Intended wiring (not done here; ``service.py`` is owned elsewhere): in
``AIService.execute`` replace the single failure branch with
``decision = settle(row_reservation, stages, failure_kind)`` followed by
``UPDATE ai_spend SET status, actual_cost, reservation`` from the decision,
and count ``failed_known_charge`` by ``actual_cost`` in ``settings()``.
See the worker report for the exact diff.

Exactness: every validated dollar amount is converted to integer
micro-dollars (1 USD = 1,000,000 micro-USD, matching money6 display) via
``Decimal(str(x)) * 1_000_000`` rounded half-up and summed as integers.
The back-conversion ``micro_usd_string`` splits that integer into whole
dollars plus a zero-padded 6-digit fraction, so the dollar amount is
exact for every micro value and float division is never part of the
conversion.

Representability bound: ``actual_cost``/``reserved_after``/``releasable``
stay floats because the ``ai_spend`` REAL column, the result digest JSON,
and the budget sum all consume floats. A float's spacing is <= 1
micro-USD only up to 2**52 micro-USD (about $4.5036B); at the cap
(2**53 micro-USD, about $9.0075B) it reaches 2 micro-USD, so no float can
hold every micro value there -- e.g. 8589934592000001 micro-USD
($8589934592.000001) lies strictly between two floats and rounds to
8589934592.000002. Each float is the nearest float of the exact decimal
(never a division remainder), and the exact decimal is carried alongside
it as the ``*_exact`` Settlement strings, so no precision is lost -- it
is only projected. Known-cost totals must stay below
``MAX_SAFE_MICRO_USD``; below 2**52 micro-USD the float projection is
exact too (0.1 + 0.2 settles to exactly 0.3). Anything beyond the cap --
e.g. [1e16, 1.0, 1.0], whose
micro sum could not survive a float round-trip with the +2 intact,
[1e308, 1e308], or a 10**400 cost -- is treated as not-known (unknown
path), never as an infinite or rounded cost, and never raises.

Context: ``settle(..., has_context=False)`` mirrors receipt
``build_receipt(..., mode="process_only")``: with no trustworthy stage
ledger there is no basis for any settlement claim, so it returns
``unknown_no_context`` immediately without evaluating stages or sums,
keeping a valid reservation in full. An invalid reservation raises
``ValueError``, exactly as on the context path (see ``settle``).

Fail-fast: an unlisted ``failure_kind`` raises ``ValueError``; it never
maps to a charge status. The ``transport_unknown`` kind is what yields
``failed_unknown_charge`` with ``actual_cost=None``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from typing import Literal

FailureKind = Literal["none", "validation_only", "transport_unknown"]
SettlementStatus = Literal[
    "completed_known",
    "completed_unknown_charge",
    "failed_known_charge",
    "failed_unknown_charge",
    "unknown_no_context",
]

RECEIVED = "response_received"

KNOWN_STATUSES = ("completed_known", "failed_known_charge")

#: Micro-dollars per USD (money6 display precision).
MICRO_USD = 1_000_000
#: Sanity cap: a single known amount, and any known-cost integer total,
#: must not exceed 2**53 micro-USD (~$9.007B, far above any real budget).
#: Every integer micro total up to 2**53 is exactly representable as a
#: float, so amounts within the cap convert back with no binary rounding
#: loss (0.1 + 0.2 settles to exactly 0.3). A single amount beyond the
#: cap -- e.g. 1e16, 1e308, or a 10**400 int cost -- is itself not-known
#: (never stored, never summed as a float); a summed total beyond the cap
#: takes the unknown path, never an infinite or rounded known cost.
MAX_SAFE_MICRO_USD = 2**53

__all__ = [
    "FailureKind",
    "SettlementStatus",
    "Settlement",
    "RECEIVED",
    "KNOWN_STATUSES",
    "MICRO_USD",
    "MAX_SAFE_MICRO_USD",
    "settle",
    "known_sum",
    "is_fully_costed",
    "micro_usd_string",
]


@dataclass(frozen=True)
class Settlement:
    """Pure settlement decision for one ``ai_spend`` row.

    ``status`` is the ``ai_spend`` status to store. ``actual_cost`` is the
    summed known cost for fully-costed outcomes, else ``None`` (unknown
    total; never a guess). ``reserved_after`` is the commitment to keep
    stored (and counted by the budget) after settlement; it is always
    ``>=`` the known partial sum. ``releasable`` is
    ``max(0.0, reservation - reserved_after)``.

    The ``*_exact`` fields carry the same three amounts as exact
    decimal-dollar strings (``micro_usd_string`` of the settled integer
    micro total), so the 6-digit money precision survives even where a
    float cannot hold it (above $4.5036B the float spacing exceeds 1
    micro-USD). They are ordinary ``str`` values: ``json.dumps`` accepts
    them unchanged. Each float field is the nearest float of its exact
    string; ``actual_cost_exact`` is ``None`` exactly when
    ``actual_cost`` is.
    """

    status: str
    actual_cost: float | None
    reserved_after: float
    releasable: float
    actual_cost_exact: str | None = None
    reserved_after_exact: str | None = None
    releasable_exact: str | None = None


def _to_micro_usd(value) -> int | None:
    """Convert a validated dollar amount to integer micro-USD, or None.

    Rejects bools, non-numeric types, non-finite floats, negatives, and
    anything beyond the safe cap: a single amount over ``MAX_SAFE_MICRO_USD``
    is itself not-known (e.g. 1e16, 1e308, or a 10**400 int). ``Decimal``
    on a huge int returns ``Infinity`` rather than raising, so the check
    must follow the conversion. Quantizes to whole micro-dollars with
    ROUND_HALF_UP.
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0:
        return None
    try:
        micro = (Decimal(str(value)) * MICRO_USD).to_integral_value(rounding=ROUND_HALF_UP)
    except (InvalidOperation, OverflowError, ValueError):
        return None
    if not micro.is_finite() or micro > MAX_SAFE_MICRO_USD:
        return None
    return int(micro)


def _valid_cost(value) -> bool:
    return _to_micro_usd(value) is not None


def _stage_cost_micro(stage: dict) -> int | None:
    """Micro-USD cost of one received stage, or None when not costed."""
    if not isinstance(stage, dict):
        return None
    if stage.get("status") != RECEIVED:
        return None
    return _to_micro_usd(stage.get("cost_usd"))


def micro_usd_string(micro: int) -> str:
    """Exact decimal-dollar string for an integer micro-USD amount.

    Splits the integer into whole dollars and a zero-padded 6-digit micro
    fraction with integer division only -- no float ever participates, so
    the conversion is exact for every value, including the ones a float
    cannot hold (e.g. ``8589934592000001`` -> ``"8589934592.000001"``,
    which has no float representation). The result is the money6 form
    (1 USD = 1,000,000 micro-USD) with a fixed 6 decimals, so two
    amounts compare exactly as strings.
    """
    sign = "-" if micro < 0 else ""
    whole, frac = divmod(abs(micro), MICRO_USD)
    return f"{sign}{whole}.{frac:06d}"


def _micro_to_float(micro: int) -> float:
    """Nearest float of the exact decimal dollars ``micro_usd_string(micro)``.

    The float is a projection, not the source of truth: above 2**52
    micro-USD (~$4.5036B) the float spacing exceeds 1 micro-USD, so no
    float can represent every micro value near the cap. Deriving it from
    the exact string (correctly rounded once) keeps it the nearest float
    of the exact amount rather than a division remainder; the exact
    amount is carried by the ``*_exact`` Settlement fields.
    """
    return float(micro_usd_string(micro))


def _micro_to_float_floor(micro: int) -> float:
    """Back-convert, rounding up so the stored float never drops below the micro total.

    A nearest-float projection can land below the exact value (e.g.
    300000 micro -> 0.3 sits 1/90071992547409920 below 3/10), and
    re-checking with a float multiply (``value * MICRO_USD``) can mask
    that error through its own rounding. So the exact dollar value is
    ``Fraction(micro, MICRO_USD)``, compared exactly against
    ``Fraction(value)``, stepping up with ``math.nextafter`` until the
    float is ``>=`` the exact total. Bounded loop: convergence takes at
    most a couple of ulps in practice.
    """
    exact = Fraction(micro, MICRO_USD)
    value = _micro_to_float(micro)
    for _ in range(1000):
        if Fraction(value) >= exact:
            return value
        value = math.nextafter(value, math.inf)
    return value  # unreachable: the loop converges within a few ulps


def _kept_float(kept_micro: int, reserve_micro: int, reservation) -> float:
    """Float to store for a kept commitment of ``kept_micro`` micro-USD.

    When the kept amount *is* the reservation (``kept_micro ==
    reserve_micro``: nothing was charged and nothing is released) the
    stored value must be the reservation itself, unchanged. It was
    produced outside this module (``AIService.prepare`` computes
    ``round(per_call * calls, 8)``), the caller passed that exact float,
    and ``_to_micro_usd`` has already proved it corresponds to exactly
    ``reserve_micro`` micro. Re-deriving it through
    ``_micro_to_float_floor`` steps the value up by one ulp whenever the
    nearest float of the exact decimal sits just below it --
    ``settle(1.319424, ..., "none")`` stored ``1.3194240000000002``
    instead of ``1.319424``, so the committed total no longer equalled
    the ``maximum_reserved_usd`` the budget was checked against, and
    nothing was gained: the exact decimal of both is ``1.319424``, which
    the reservation float already holds. So: no conversion at all when
    the amount to keep is the reservation.

    Only a kept amount that differs from the reservation (a known partial
    sum above it, which this module derived from stage costs rather than
    receiving) is converted, with the conservative floor that never drops
    below the exact micro total.
    """
    if kept_micro == reserve_micro:
        return float(reservation)
    return _micro_to_float_floor(kept_micro)


def is_fully_costed(stages: list[dict]) -> bool:
    """True when every attempted stage has a received, numerically costed response."""
    if not isinstance(stages, list) or not stages:
        return False
    for stage in stages:
        if not isinstance(stage, dict):
            return False
        if stage.get("status") != RECEIVED:
            return False
        if _to_micro_usd(stage.get("cost_usd")) is None:
            return False
    return True


def known_sum(stages: list[dict]) -> float:
    """Sum of present numeric ``cost_usd`` values computed in micro integers.

    Used for the legacy float partial-sum view; sums that would exceed
    ``MAX_SAFE_MICRO_USD`` saturate at that cap rather than overflowing.
    """
    micro = _known_sum_micro(stages)
    if micro > MAX_SAFE_MICRO_USD:
        micro = MAX_SAFE_MICRO_USD
    return _micro_to_float(micro)


def _known_sum_micro(stages: list[dict]) -> int:
    """Integer micro-USD partial sum of all costed stages (no cap)."""
    total = 0
    if not isinstance(stages, list):
        return total
    for stage in stages:
        cost = _stage_cost_micro(stage) if isinstance(stage, dict) else None
        if cost is not None:
            total += cost
    return total


def _total_micro_or_none(stages: list[dict]) -> int | None:
    """Full micro-USD sum when every stage is received+costed and under the cap."""
    if not is_fully_costed(stages):
        return None
    total = _known_sum_micro(stages)
    if total > MAX_SAFE_MICRO_USD:
        return None
    return total


def _valid_reservation_micro(reservation) -> int | None:
    """Reservation in micro-USD, or None when invalid or over the cap."""
    micro = _to_micro_usd(reservation)
    if micro is None or micro > MAX_SAFE_MICRO_USD:
        return None
    return micro


def settle(
    reservation: float,
    stages: list[dict],
    failure_kind: str,
    *,
    has_context: bool = True,
) -> Settlement:
    """Decide the settlement for one job.

    Args:
        reservation: conservative reservation in USD (finite, ``>= 0``).
        stages: per-stage attempt dicts with ``status`` and ``cost_usd``
            (mirrors the ``stages`` list built in ``AIService.execute``).
        failure_kind: ``"none"`` (success path), ``"validation_only"``
            (failure after all responses were received and costed, e.g.
            typed-output validation rejected them), or
            ``"transport_unknown"`` (any transport/provider/scope failure
            where the total charge is unknown). An unlisted kind raises
            ``ValueError`` (fail-fast); the ``transport_unknown`` kind is
            what yields ``failed_unknown_charge`` with ``actual_cost=None``.
        has_context: ``False`` when there is no trustworthy stage ledger
            (mirrors receipt ``build_receipt(..., mode="process_only")``,
            where no evidence context means no settlement claim). With
            ``False``, no stages or sums are evaluated and ``failure_kind``
            is not validated either: the reservation is kept in full as
            ``unknown_no_context`` with ``actual_cost=None``
            and ``releasable=0.0`` for every failure kind, because with
            no ledger there is no basis for any settlement claim at all.
            Reservation validation is identical on both paths: an
            over-cap or otherwise unrepresentable reservation (non-numeric,
            non-finite, negative, or beyond ``MAX_SAFE_MICRO_USD``, e.g.
            9007199254740993 or 10**400) raises ``ValueError`` whether or
            not there is context -- wiring (Lead's later service.py step)
            can rely on never receiving a silently truncated reservation.

    Returns:
        Settlement with the status to store, the actual known cost (or
        ``None``), the commitment to keep, and the releasable remainder.

    Rules:
        - ``validation_only`` with all stages received+costed and the
          exact integer-micro total under the cap ->
          ``failed_known_charge``, ``actual_cost`` = sum, remainder released.
        - ``transport_unknown``, or any attempted stage without a
          received/costed response (regardless of the claimed kind), or a
          total beyond the safe cap ->
          ``failed_unknown_charge``, ``actual_cost`` = ``None``,
          ``reserved_after`` = ``max(reservation, known partial sum)``
          computed in micro integers.
        - ``none`` with all stages received+costed (total under the cap)
          -> ``completed_known``; otherwise ->
          ``completed_unknown_charge`` with the same
          ``max(reservation, known sum)`` floor.
    """
    if has_context is False:
        # No trustworthy stage ledger: no settlement claim is possible.
        # Stages, sums, and failure_kind are deliberately not evaluated.
        # The reservation is still validated exactly like on the context
        # path: anything _to_micro_usd rejects -- non-numeric, non-finite,
        # negative, or over-cap/unrepresentable (e.g. 9007199254740993,
        # 10**400) -- raises ValueError, so no silent $1 float loss and
        # no raw OverflowError ever escape on either path.
        if _to_micro_usd(reservation) is None:
            raise ValueError("reservation must be a finite number >= 0 within the safe cap")
        return Settlement(
            status="unknown_no_context",
            actual_cost=None,
            reserved_after=float(reservation),
            releasable=0.0,
        )

    if failure_kind not in ("none", "validation_only", "transport_unknown"):
        raise ValueError("failure_kind must be one of 'none', 'validation_only', 'transport_unknown'")

    if not isinstance(stages, list):
        raise ValueError("stages must be a list")
    reserve_micro = _valid_reservation_micro(reservation)
    if reserve_micro is None:
        raise ValueError("reservation must be finite and >= 0")
    partial_micro = _known_sum_micro(stages)
    total_micro = _total_micro_or_none(stages)

    if failure_kind == "none":
        if total_micro is not None:
            total = _micro_to_float(total_micro)
            releasable_micro = max(0, reserve_micro - total_micro)
            return Settlement(
                status="completed_known",
                actual_cost=total,
                reserved_after=total,  # known total may exceed reservation; never store below it
                releasable=_micro_to_float(releasable_micro),
                actual_cost_exact=micro_usd_string(total_micro),
                reserved_after_exact=micro_usd_string(total_micro),
                releasable_exact=micro_usd_string(releasable_micro),
            )
        kept_micro = max(reserve_micro, partial_micro)
        return Settlement(
            status="completed_unknown_charge",
            actual_cost=None,
            reserved_after=_kept_float(kept_micro, reserve_micro, reservation),
            releasable=_micro_to_float(max(0, reserve_micro - kept_micro)),
            reserved_after_exact=micro_usd_string(kept_micro),
            releasable_exact=micro_usd_string(max(0, reserve_micro - kept_micro)),
        )

    if failure_kind == "validation_only" and total_micro is not None:
        total = _micro_to_float(total_micro)
        return Settlement(
            status="failed_known_charge",
            actual_cost=total,
            reserved_after=total,
            releasable=_micro_to_float(max(0, reserve_micro - total_micro)),
            actual_cost_exact=micro_usd_string(total_micro),
            reserved_after_exact=micro_usd_string(total_micro),
            releasable_exact=micro_usd_string(max(0, reserve_micro - total_micro)),
        )

    # Unknown-charge path: claimed transport failure, a validation_only
    # claim contradicted by an unreceived/uncosted stage, or a known sum
    # that exceeds the safe cap. A kept amount above the reservation uses
    # exact micro comparison via _micro_to_float_floor so reserved_after
    # never drops below the known partial sum, even past float resolution;
    # keeping the reservation itself stores it unchanged (see _kept_float).
    kept_micro = max(reserve_micro, partial_micro)
    return Settlement(
        status="failed_unknown_charge",
        actual_cost=None,
        reserved_after=_kept_float(kept_micro, reserve_micro, reservation),
        releasable=_micro_to_float(max(0, reserve_micro - kept_micro)),
        reserved_after_exact=micro_usd_string(kept_micro),
        releasable_exact=micro_usd_string(max(0, reserve_micro - kept_micro)),
    )
