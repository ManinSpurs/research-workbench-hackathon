"""Secretary radar S2 (read-only composer): stale flags over launch-shaped rows.

Drives ``app/radar.py`` with hand-built ``launch_briefing()``-shaped dicts only — no DB,
no harness import, no network, no credits consumed. Mock/fake data throughout.

Shared vocabulary with ``tests/test_secretary_coach_s1.py`` (read for vocabulary only;
``secretary_coach`` is never imported): at most 3 prominent stale flags, D1-only
deadline wording (this composer receives no D1 field, so no deadline wording appears),
and the 14-day threshold reused from ``harness.POINTED_MIN_AGE_DAYS``.

No wall clock is consulted: every age is measured from the literal ``VERIFIED`` instant
below, injected as the ``verified_at`` parameter.
"""
import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import radar  # noqa: E402

VERIFIED = "2026-03-01T00:00:00+00:00"

_DEADLINE_WORDS = ("deadline", "overdue", "no later than", "due date", "due by", "past due")
_DEADLINE_RES = (
    re.compile(r"\bdue\b(?!\s+to)", re.I),
    re.compile(r"\bby\s+(\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
               r"mon|tue|wed|thu|fri|sat|sun|the end of|end of)", re.I),
)
_SCORE_WORDS = ("score", "health", "productivity", "ranking", "percent")
_MORAL_WORDS = ("ashamed", "guilt", "guilty", "lazy", "disappoint", "shame", "punish",
                "neglect", "you must", "you should have")


def _ts(days_ago):
    """ISO timestamp ``days_ago`` before the literal VERIFIED instant."""
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return (base - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _launch_row(pid, name=None, waiting_ages=(), waiting_texts=None, decisions=0,
                input_changed=False, verified_at=VERIFIED, last_activity_days_ago=None):
    """One launch_briefing()-shaped row (real row keys, fake content)."""
    name = name or f"Project {pid}"
    items = []
    for i, age in enumerate(waiting_ages):
        if waiting_texts and i < len(waiting_texts):
            text = waiting_texts[i]
        else:
            text = f"Waiting item {i + 1} for {pid}"
        items.append({"text": text, "saved_at": None if age is None else _ts(age)})
    waiting_view = items if items else [{"text": "Nothing waiting.", "saved_at": None}]
    left_real = [{"text": f"Adopt: \"Scope {pid} {i}\". Reason: recorded reason {i}.",
                  "saved_at": _ts(400)} for i in range(decisions)]
    left_view = left_real if left_real else [{"text": "No decisions yet.", "saved_at": None}]
    if input_changed:
        changed = [{"text": "Registered data changed since registration; dependent work needs review.",
                    "saved_at": _ts(400), "verified_at": verified_at}]
    else:
        changed = [{"text": "No changes detected in registered data.", "saved_at": None,
                    "verified_at": verified_at}]
    row = {
        "project_id": pid, "project": name,
        "parts": {
            "where_you_left_off": left_view,
            "what_changed_since": changed,
            "ready_now": [{"text": "Nothing ready.", "saved_at": None}],
            "waiting_on_you": waiting_view,
            "suggested_next_action": [
                {"text": items[0]["text"] if items else "No pending actions.",
                 "saved_at": None}],
        },
        "top_items": (left_real + items)[:3],
        "verified_at": verified_at, "model_calls": 0,
        "guidance": {"level": "calm", "text": "No pressure — nothing is waiting.",
                     "basis": "No items are waiting.", "model_calls": 0},
        "has_waiting": bool(items),
        "waiting_count": len(items),
        "has_decision": bool(left_real),
        "decision_count": len(left_real),
        "input_changed": input_changed,
    }
    if last_activity_days_ago is not None:
        # Optional passthrough only: plain launch_briefing() rows carry no such field.
        row["last_activity"] = _ts(last_activity_days_ago)
    return row


def _compose(rows, verified_at=VERIFIED):
    return radar.compose_radar(rows, verified_at=verified_at)


def _by_id(payload, pid):
    return next(r for r in payload["rows"] if r["project_id"] == pid)


def _blob(*values):
    return json.dumps(values, ensure_ascii=False, default=str).lower()


def assert_no_deadline_wording(blob):
    text = str(blob).lower()
    for word in _DEADLINE_WORDS:
        assert word not in text, f"deadline wording {word!r} in: {text}"
    for pattern in _DEADLINE_RES:
        match = pattern.search(str(blob))
        assert match is None, f"deadline wording {match.group(0)!r} in: {text}"


def assert_no_score_or_moralizing(blob):
    text = str(blob).lower()
    for word in _SCORE_WORDS + _MORAL_WORDS:
        assert word not in text, f"forbidden token {word!r} in: {text}"


# ------------------------------------------------ multi-project stale flagging

def test_multi_project_stale_flagging_names_project_text_age_and_count():
    rows = [
        _launch_row("p-old", waiting_ages=[30, 5],
                    waiting_texts=["Oldest waiting question for p-old",
                                   "Second waiting item for p-old"]),
        _launch_row("p-fresh", waiting_ages=[2]),
        _launch_row("p-empty"),
    ]
    payload = _compose(rows)
    assert payload["project_count"] == 3
    assert payload["stale_count"] == 1
    assert payload["model_calls"] == 0

    stale = _by_id(payload, "p-old")
    assert stale["stale"] is True
    assert stale["stale_prominent"] is True
    flag = stale["flag"]
    assert flag["project"] == "Project p-old"
    assert flag["age_days"] == 30
    assert flag["waiting_count"] == 2
    assert "Oldest waiting question for p-old" in flag["oldest_text"]
    assert "Second waiting item" not in flag["oldest_text"]
    assert len(flag["oldest_text"]) <= 80

    for pid in ("p-fresh", "p-empty"):
        row = _by_id(payload, pid)
        assert row["stale"] is False
        assert row["stale_prominent"] is False
        assert row["flag"] is None

    assert_no_deadline_wording(_blob(payload))
    assert_no_score_or_moralizing(_blob(payload))


def test_stale_boundary_flags_at_exactly_14_days():
    payload = _compose([_launch_row("p1", waiting_ages=[14])])
    row = _by_id(payload, "p1")
    assert row["stale"] is True
    assert row["flag"]["age_days"] == 14


def test_thirteen_days_with_recent_activity_is_not_stale():
    payload = _compose([_launch_row("p1", waiting_ages=[13],
                                    last_activity_days_ago=0)])
    row = _by_id(payload, "p1")
    assert row["stale"] is False
    assert row["flag"] is None


# ------------------------------------------------------- prominence cap (max 3)

def test_prominence_cap_is_three_while_all_stale_rows_survive():
    rows = [_launch_row(f"p{i}", waiting_ages=[15 + i]) for i in range(1, 6)]
    payload = _compose(rows)
    assert payload["project_count"] == 5
    assert payload["stale_count"] == 5
    # Full list survival: no stale row is dropped or demoted to non-stale.
    assert len(payload["rows"]) == 5
    assert all(r["stale"] is True for r in payload["rows"])
    # Every stale row keeps its flag (expandable); only the top 3 are prominent.
    assert all(r["flag"] is not None for r in payload["rows"])
    prominent = [r for r in payload["rows"] if r["stale_prominent"]]
    assert len(prominent) == 3
    assert payload["prominent_count"] == 3
    assert [r["project_id"] for r in prominent] == ["p5", "p4", "p3"]
    rest = [r for r in payload["rows"] if not r["stale_prominent"]]
    assert all(r["stale"] is True for r in rest)


# ------------------------------------------------------------------ non-stale

def test_non_stale_projects_unflagged_including_dormant_ones():
    rows = [
        _launch_row("p-fresh", waiting_ages=[2]),
        _launch_row("p-dormant", decisions=2),  # 400-day-old decisions, no waiting
        _launch_row("p-empty"),
    ]
    payload = _compose(rows)
    assert payload["stale_count"] == 0
    assert payload["prominent_count"] == 0
    for pid in ("p-fresh", "p-dormant", "p-empty"):
        row = _by_id(payload, pid)
        assert row["stale"] is False
        assert row["flag"] is None
        assert "needs attention" not in _blob(row)
    dormant = _by_id(payload, "p-dormant")
    assert dormant["decision_count"] == 2
    assert_no_score_or_moralizing(_blob(payload))


def test_inactivity_branch_requires_waiting_items_and_last_activity():
    # Rule (b): waiting items + 14+ days of inactivity -> stale even when items are fresh.
    payload = _compose([_launch_row("p1", waiting_ages=[1], last_activity_days_ago=21)])
    assert _by_id(payload, "p1")["stale"] is True
    # Recent activity -> not stale.
    payload = _compose([_launch_row("p1", waiting_ages=[1], last_activity_days_ago=0)])
    assert _by_id(payload, "p1")["stale"] is False
    # No last_activity field at all (plain launch shape) -> branch (b) unavailable.
    payload = _compose([_launch_row("p1", waiting_ages=[1])])
    row = _by_id(payload, "p1")
    assert row["stale"] is False
    assert row["inactivity_days"] is None
    # Inactivity without waiting items is dormancy, never stale.
    payload = _compose([_launch_row("p1", decisions=1, last_activity_days_ago=400)])
    assert _by_id(payload, "p1")["stale"] is False


# ------------------------------------------------------------- edge inputs

def test_empty_input_returns_empty_rows_with_no_flags():
    payload = _compose([])
    assert payload["rows"] == []
    assert payload["project_count"] == 0
    assert payload["stale_count"] == 0
    assert payload["prominent_count"] == 0
    assert payload["model_calls"] == 0
    assert_no_deadline_wording(_blob(payload))


def test_single_stale_project_is_prominent_and_single_fresh_is_not():
    payload = _compose([_launch_row("solo", waiting_ages=[40])])
    assert payload["project_count"] == 1
    row = _by_id(payload, "solo")
    assert row["stale"] is True
    assert row["stale_prominent"] is True

    payload = _compose([_launch_row("solo", waiting_ages=[1])])
    row = _by_id(payload, "solo")
    assert row["stale"] is False
    assert row["stale_prominent"] is False
    assert row["flag"] is None


def test_all_unparseable_waiting_timestamps_are_counted_never_stale():
    payload = _compose([
        _launch_row("p1", waiting_ages=[None, None]),
        _launch_row("p2", waiting_ages=[1]),
    ])
    row = _by_id(payload, "p1")
    assert row["stale"] is False
    assert row["waiting_count"] == 2
    assert row["age_known"] is False
    assert "unknown" in row["basis"]
    # Waiting projects still order ahead of non-waiting ones.
    payload = _compose([_launch_row("p3"), _launch_row("p1", waiting_ages=[None])])
    assert [r["project_id"] for r in payload["rows"]] == ["p1", "p3"]


def test_unparseable_verified_at_renders_counts_only_with_limitation():
    rows = [_launch_row("p1", waiting_ages=[40], verified_at="not-a-timestamp !!!")]
    payload = radar.compose_radar(rows, verified_at="also unparseable")
    assert payload["rows"], "rows must survive"
    assert payload["limitation"], "a limitation line is required"
    assert all(r["stale"] is False for r in payload["rows"])
    assert all(r["stale_prominent"] is False for r in payload["rows"])
    assert _by_id(payload, "p1")["waiting_count"] == 1
    assert_no_deadline_wording(_blob(payload))


def test_explicit_verified_at_overrides_row_values():
    rows = [_launch_row("p1", waiting_ages=[30], verified_at="not-a-timestamp !!!")]
    payload = radar.compose_radar(rows, verified_at=VERIFIED)
    assert _by_id(payload, "p1")["stale"] is True
    assert payload["limitation"] == ""


# ------------------------------------------------------------------ ordering

def test_ordering_stale_first_then_launch_rule():
    rows = [
        _launch_row("plain-b", name="Same"),
        _launch_row("plain-a", name="Same"),
        _launch_row("decision-only", decisions=1),
        _launch_row("waiting-fresh", waiting_ages=[1], decisions=1),
        _launch_row("stale-20", waiting_ages=[20]),
        _launch_row("stale-40", waiting_ages=[40]),
    ]
    payload = _compose(rows)
    assert [r["project_id"] for r in payload["rows"]] == [
        "stale-40", "stale-20", "waiting-fresh", "decision-only", "plain-a", "plain-b"]
    assert [r["stale"] for r in payload["rows"]] == [True, True, False, False, False, False]


# ------------------------------------------------- determinism and purity

def test_deterministic_repeat_call_equality():
    rows = [
        _launch_row("p1", waiting_ages=[30, 2], decisions=1, input_changed=True),
        _launch_row("p2", waiting_ages=[20]),
        _launch_row("p3", waiting_ages=[2]),
        _launch_row("p4", decisions=1),
    ]
    first = _compose(rows)
    second = _compose(rows)
    assert first == second
    # Tie on every ordering key resolves identically regardless of input order.
    tied = [_launch_row("pid-z", name="alpha"), _launch_row("pid-a", name="Alpha")]
    assert ([r["project_id"] for r in _compose(tied)["rows"]] ==
            [r["project_id"] for r in _compose(list(reversed(tied)))["rows"]] ==
            ["pid-a", "pid-z"])


def test_read_only_input_rows_are_never_mutated():
    rows = [
        _launch_row("p1", waiting_ages=[30, None], decisions=1, input_changed=True),
        _launch_row("p2", waiting_ages=[2]),
        _launch_row("p3"),
    ]
    snapshot = copy.deepcopy(rows)
    payload = _compose(rows)
    assert rows == snapshot, "compose_radar must not mutate its input rows"
    # Outputs are independent copies: mutating them leaves the inputs untouched.
    payload["rows"][0]["top_items"].append({"text": "injected", "saved_at": None})
    payload["rows"][0]["flag"] = None
    assert rows == snapshot


def test_counts_and_top_items_pass_through_unchanged():
    row = _launch_row("p1", waiting_ages=[30, 5], decisions=2, input_changed=True)
    payload = _compose([row])
    out = _by_id(payload, "p1")
    assert out["waiting_count"] == 2
    assert out["decision_count"] == 2
    assert out["input_changed"] is True
    assert out["top_items"] == row["top_items"]
    assert out["top_item"] == row["top_items"][0]
    assert out["model_calls"] == 0


def test_no_cross_project_leakage():
    sentinel = "SENTINEL-P-9931"
    rows = [
        _launch_row("p", waiting_ages=[40], waiting_texts=[f"{sentinel} waiting text"]),
        _launch_row("q", waiting_ages=[2], waiting_texts=["Q own waiting text"]),
    ]
    payload = _compose(rows)
    assert sentinel not in _blob(_by_id(payload, "q"))
    assert "Q own waiting text" not in _blob(_by_id(payload, "p"))
    assert sentinel in _by_id(payload, "p")["flag"]["oldest_text"]


def test_no_deadline_wording_anywhere_including_limitation_paths():
    payload = _compose([
        _launch_row("p1", waiting_ages=[40]),
        _launch_row("p2", waiting_ages=[None]),
        _launch_row("p3", decisions=1, last_activity_days_ago=400),
        _launch_row("p4"),
    ])
    assert_no_deadline_wording(_blob(payload))
    counts_only = radar.compose_radar(
        [_launch_row("p1", waiting_ages=[40], verified_at="bad")], verified_at="bad")
    assert_no_deadline_wording(_blob(counts_only))
    assert_no_score_or_moralizing(_blob(payload))


def test_module_stays_free_of_io_clocks_and_heavy_imports():
    import io
    import tokenize
    raw = Path(radar.__file__).read_text(encoding="utf-8")
    code_only = []
    for tok in tokenize.generate_tokens(io.StringIO(raw).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        code_only.append(tok.string)
    source = " ".join(code_only)
    for token in ("datetime.now", "time.time", "socket", "sqlite3", "harness",
                  "secretary_coach", "from app import", "import app",
                  "open(", "requests", "urllib"):
        assert token not in source, f"purity violation: {token!r} in app/radar.py"
