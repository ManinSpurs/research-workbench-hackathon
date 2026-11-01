"""Cross-project radar composer (Secretary slice S2).

Pure, read-only composer over ``launch_briefing()``-shaped rows. The caller passes the
rows in (this module never calls the network, never touches a store, and never reads the
wall clock); every age is measured against an explicitly injected ``verified_at``
instant, falling back to the rows' own recorded ``verified_at`` strings. All output rows
are freshly built copies: input rows are never mutated.

Scope (design: ``current/SECRETARY_COACHING_PROJECT_RADAR_EN.md``):
  * per-project progress rollup — the launch row's own counts (``waiting_count``,
    ``decision_count``) and its already-present ``top_items`` pass through unchanged;
    nothing is recomputed or invented;
  * deterministic stale-project detection across projects with at most
    ``MAX_PROMINENT_STALE`` (3) prominent flags while every stale row survives in the
    full list (design §4.2).

Staleness rule (design §4.2, thresholds reuse ``harness.POINTED_MIN_AGE_DAYS`` = 14 so
per-project coaching and cross-project radar agree): a project is flagged
``stale — needs attention`` iff it has waiting items AND either

  (a) its oldest parseable waiting age is >= ``STALE_MIN_AGE_DAYS`` (14) days, or
  (b) the row carries a parseable ``last_activity`` timestamp whose inactivity age is
      >= ``STALE_MIN_AGE_DAYS`` (14) days;

a project with no waiting items is never flagged stale (dormant is not neglected).

Current state (C15): every ``launch_briefing()`` row carries ``last_activity``
(``app/harness.py`` ``launch_briefing`` region): max ``created_at`` over
question-scoped events (``question_id IS NOT NULL``) ∪ decisions for that
project, ``None`` when no recorded activity (registration alone — a
``project_registered`` event with NULL ``question_id`` — does not count).
Branch (b) reads it tolerantly via ``row.get("last_activity")``, so rows without
the field (or with an unparseable value) still fall back to branch (a) alone,
and ``input_changed`` (a boolean about registered-data drift, not an
age) is carried through but never treated as a staleness signal. No timestamp is ever
fabricated or interpolated; unparseable ages degrade to count-only with an
``"age unknown"`` basis (design §§2.4, 4.4).

Deadline wording (design §3): only an explicitly recorded D1 date may ever be rendered
with deadline wording, and this composer receives no D1 field, so it never emits
deadline/due/overdue wording. Waiting age renders only as ``"waiting N days"`` and
inactivity only as ``"no recorded activity for N days"``. No scores, percentages,
rankings, or moral judgments appear anywhere in the output.

Ordering (design §4.2): stale-flagged projects first by oldest waiting age descending,
then projects with waiting items, then projects with a latest decision, then project
name ascending (case-insensitive), then project id. Python's sort is stable, so every
tie resolves by the documented keys only — no model calls, no randomness.

S1 boundary: recommendation objects, coaching templates, and the D1 deadline-wording
gate belong to slice S1 (``app/secretary_coach.py``) and are deliberately not
duplicated here; this module is import-independent of ``harness``, ``api``,
``store``, ``secretary_coach``, and ``sqlite3``. Wiring this output into the briefing
is slice S3 work and is not attempted here.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

__all__ = [
    "compose_radar",
    "STALE_MIN_AGE_DAYS",
    "MAX_PROMINENT_STALE",
    "ORDERING_RULE",
    "STALENESS_RULE",
]

#: Age threshold for the stale flag. Mirrors ``harness.POINTED_MIN_AGE_DAYS`` (= 14) by
#: value (not by import — this module must stay import-independent of ``harness``) so
#: per-project coaching and cross-project radar agree (design §§2.3, 4.2).
STALE_MIN_AGE_DAYS = 14

#: At most this many stale flags are marked prominent; every other stale row keeps its
#: flag in the full list (design §4.2; mirrors the top-3 briefing cap).
MAX_PROMINENT_STALE = 3

NOTHING_WAITING_TEXT = "Nothing waiting."
FLAG_LABEL = "stale — needs attention"

ORDERING_RULE = (
    "stale projects first (oldest waiting age descending), then projects with waiting "
    "items, then projects with a latest decision, then project name ascending "
    "(case-insensitive), then project id"
)

STALENESS_RULE = (
    "A project is flagged 'stale — needs attention' iff it has waiting items and "
    "either (a) its oldest parseable waiting age is 14 days or more, or (b) the row "
    "carries a parseable last_activity timestamp with inactivity of 14 days or more; "
    "a project with no waiting items is never flagged stale."
)


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp string to UTC; None when missing/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _whole_days(verified: datetime, saved: datetime) -> int:
    """Whole days from ``saved`` to ``verified``; future stamps clamp to 0."""
    age = int((verified - saved).total_seconds() // 86400)
    return age if age >= 0 else 0


def _short(text: str, limit: int = 80) -> str:
    """Local short-text helper: collapse whitespace, cap at ``limit`` chars."""
    flat = " ".join(str(text or "").split())
    if len(flat) > limit:
        return flat[:limit - 1].rstrip() + "…"
    return flat


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"


def _order_key(row: dict) -> tuple:
    name = str(row.get("project") or "").lower()
    pid = str(row.get("project_id") or "")
    if row.get("stale"):
        age = row.get("age_days")
        age = age if isinstance(age, int) else 0
        return (0, -age, 0, 0, name, pid)
    return (1, 0, 0 if row.get("has_waiting") else 1,
            0 if row.get("has_decision") else 1, name, pid)


def _compose_row(row: dict, verified: datetime | None) -> dict:
    """Build one radar row from a single launch-shaped row (read-only)."""
    pid = row.get("project_id")
    name = row.get("project") if isinstance(row.get("project"), str) else ""

    parts = row.get("parts")
    parts = parts if isinstance(parts, dict) else {}
    waiting_view = parts.get("waiting_on_you")
    if isinstance(waiting_view, list):
        # The waiting records themselves are authoritative when present: drop only the
        # "Nothing waiting." placeholder, never a real item.
        items = [w for w in waiting_view
                 if isinstance(w, dict) and w.get("text") != NOTHING_WAITING_TEXT]
        has_waiting = bool(items)
        waiting_count = len(items)
    else:
        # Row shape without parts (e.g. a compact rollup): trust the row's own flags.
        has_waiting = bool(row.get("has_waiting"))
        try:
            waiting_count = int(row.get("waiting_count") or 0) if has_waiting else 0
        except (TypeError, ValueError):
            waiting_count = 0
        items = []

    has_decision = bool(row.get("has_decision"))
    try:
        decision_count = int(row.get("decision_count") or 0) if has_decision else 0
    except (TypeError, ValueError):
        decision_count = 0

    oldest_age: int | None = None
    oldest_item: dict | None = None
    if verified is not None:
        for item in items:
            saved = _parse_ts(item.get("saved_at"))
            if saved is None:
                continue
            age = _whole_days(verified, saved)
            if oldest_age is None or age > oldest_age:
                oldest_age = age
                oldest_item = item

    inactivity: int | None = None
    if verified is not None:
        last_activity = _parse_ts(row.get("last_activity"))
        if last_activity is not None:
            inactivity = _whole_days(verified, last_activity)

    stale = False
    if verified is not None and has_waiting:
        if oldest_age is not None and oldest_age >= STALE_MIN_AGE_DAYS:
            stale = True
        elif inactivity is not None and inactivity >= STALE_MIN_AGE_DAYS:
            stale = True

    if oldest_age is not None:
        age_days: int | None = oldest_age
        age_basis = "waiting"
    elif stale and inactivity is not None:
        age_days = inactivity
        age_basis = "inactivity"
    else:
        age_days = None
        age_basis = "unknown"

    count_noun = _plural(waiting_count, "item")
    if not has_waiting:
        if has_decision:
            basis = (f"No items are waiting; {decision_count} "
                     f"recorded {_plural(decision_count, 'decision')} on file.")
        else:
            basis = "No items are waiting and no decisions are recorded."
        flag = None
    elif verified is None:
        basis = f"{waiting_count} {count_noun} waiting; age not evaluated."
        flag = None
    elif oldest_age is not None:
        day_noun = _plural(oldest_age, "day")
        basis = (f"Oldest waiting item is {oldest_age} {day_noun} old; "
                 f"{waiting_count} {count_noun} waiting.")
        flag = None
        if stale:
            basis += " Project flagged stale — needs attention."
            flag = {
                "label": FLAG_LABEL,
                "project": name,
                "oldest_text": _short((oldest_item or items[0]).get("text", "")),
                "age_days": oldest_age,
                "waiting_count": waiting_count,
                "detail": basis,
            }
    elif stale and inactivity is not None:
        day_noun = _plural(inactivity, "day")
        basis = (f"No recorded activity for {inactivity} {day_noun}; "
                 f"{waiting_count} {count_noun} waiting. "
                 f"Oldest waiting age is unknown. "
                 f"Project flagged stale — needs attention.")
        flag = {
            "label": FLAG_LABEL,
            "project": name,
            "oldest_text": _short(items[0].get("text", "")) if items else "",
            "age_days": inactivity,
            "waiting_count": waiting_count,
            "detail": basis,
        }
    else:
        basis = (f"Oldest waiting age is unknown; {waiting_count} {count_noun} waiting.")
        flag = None

    top_items = copy.deepcopy(row.get("top_items") or [])
    if not isinstance(top_items, list):
        top_items = []
    row_verified = row.get("verified_at")
    return {
        "project_id": pid,
        "project": name,
        "has_waiting": has_waiting,
        "waiting_count": waiting_count,
        "oldest_waiting_age_days": oldest_age,
        "age_known": oldest_age is not None,
        "age_days": age_days,
        "age_basis": age_basis,
        "has_decision": has_decision,
        "decision_count": decision_count,
        "input_changed": bool(row.get("input_changed")),
        "inactivity_days": inactivity,
        "top_items": top_items,
        "top_item": top_items[0] if top_items else None,
        "stale": stale,
        "stale_prominent": False,
        "flag": flag,
        "basis": basis,
        "verified_at": row_verified if isinstance(row_verified, str) else None,
        "model_calls": 0,
    }


def compose_radar(rows, verified_at=None) -> dict:
    """Compose the cross-project radar over launch-shaped rows.

    ``rows`` is a list of ``launch_briefing()``-shaped dicts (accepted as input, never
    mutated, never re-fetched). ``verified_at`` is an optional ISO-8601 instant used
    for every age computation; when absent or unparseable, the first parseable row
    ``verified_at`` is used; when none parses, the radar renders counts only with a
    limitation line and no age-based flags (design §4.4). No wall clock is read.
    """
    if not isinstance(rows, (list, tuple)):
        raise TypeError("rows must be a list of launch_briefing()-shaped dicts")
    incoming = list(rows)
    for entry in incoming:
        if not isinstance(entry, dict):
            raise TypeError("rows must be a list of launch_briefing()-shaped dicts")

    effective: str | None = None
    if isinstance(verified_at, str) and _parse_ts(verified_at) is not None:
        effective = verified_at
    if effective is None:
        for entry in incoming:
            candidate = entry.get("verified_at")
            if isinstance(candidate, str) and _parse_ts(candidate) is not None:
                effective = candidate
                break

    verified = _parse_ts(effective) if effective is not None else None
    composed = [_compose_row(entry, verified) for entry in incoming]

    if verified is None:
        limitation = ("verified_at is missing or unparseable; "
                      "showing counts only with no age-based flags.")
        ordered = composed  # preserve the launch_briefing input order
    else:
        limitation = ""
        ordered = sorted(composed, key=_order_key)
        prominent = 0
        for out in ordered:
            if out["stale"] and prominent < MAX_PROMINENT_STALE:
                out["stale_prominent"] = True
                prominent += 1
            else:
                out["stale_prominent"] = False

    return {
        "rows": ordered,
        "project_count": len(ordered),
        "stale_count": sum(1 for out in ordered if out["stale"]),
        "prominent_count": sum(1 for out in ordered if out["stale_prominent"]),
        "ordering_rule": ORDERING_RULE,
        "staleness_rule": STALENESS_RULE,
        "model_calls": 0,
        "verified_at": effective,
        "limitation": limitation,
    }
