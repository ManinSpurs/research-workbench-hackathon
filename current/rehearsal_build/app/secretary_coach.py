"""Secretary coaching S1: proposal-only recommendations, coaching templates, radar rule.

Pure, deterministic, read-only logic over caller-supplied rows/dicts. No model
calls, no wall-clock reads, no store access, no harness/api/store imports.
Every recommendation is a proposal (``proposal — not decided``); encouragement
derives only from recorded progress; deadline wording is allowed only for a
researcher-recorded explicit date (D1 gate). Nothing here auto-activates,
decides, or writes.
"""

DECISION_STATUS_LITERAL = "proposal \u2014 not decided"

FIELDS = ("proposal_wording", "basis", "uncertainty_limits", "alternatives",
          "suggested_next_action", "evidence_record_refs", "decision_status")

ACTION_FAMILIES = (
    "Review the drafted narrow scope and adopt, defer, or reject it.",
    "Answer the recorded clarification gap about the construction start date.",
    "Adopt the narrow scope, or defer or reject it, as recorded in ev-1.",
    "Mark 'Prepare this for me' for the narrow scope draft.",
)

_LAUNCH_ORDERING_RULE = ("projects with waiting items first, then projects with a "
                         "latest decision, then project name ascending "
                         "(case-insensitive), then project id")

_STALE_MIN_AGE_DAYS = 14
_PROMINENT_CAP = 3


class RecommendationError(ValueError):
    """Rejection of a recommendation that violates the S1 contract."""


def _parse_ts(value):
    """Parse an ISO timestamp to an aware UTC datetime; None when unparseable."""
    from datetime import datetime, timezone
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


def _age_days(saved_at, verified_at):
    saved = _parse_ts(saved_at)
    verified = _parse_ts(verified_at)
    if saved is None or verified is None:
        return None
    age = int((verified - saved).total_seconds() // 86400)
    return max(age, 0)


def _truncate_text(text, limit=80):
    flat = " ".join(str(text or "").split())
    if len(flat) > limit:
        return flat[:limit - 1].rstrip() + "\u2026"
    return flat


def _record_ids(text):
    import re
    return set(re.findall(r"[A-Za-z]+-\d+", str(text or "")))


_NO_RECORD_MARKERS = ("no record", "missing record", "not recorded", "no recorded",
                      "missing adoption record", "gap is the missing")


def _states_no_record(text):
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _NO_RECORD_MARKERS)


def _is_supply_or_investigate_action(text):
    lowered = str(text or "").lower()
    return ("investigat" in lowered or "suppl" in lowered or "provide" in lowered
            or "record the missing" in lowered or "adoption record exists" in lowered)


def _has_certainty_language(text):
    import re
    lowered = str(text or "").lower()
    if "right answer" in lowered:
        return True
    if "certain" in lowered:
        return True
    if "confirmed" in lowered:
        return True
    if re.search(r"\d+\s*%", lowered):
        return True
    return False


_IMPERATIVE_MARKERS = ("you must", "has to adopt", "this is required",
                       "adopt the narrow scope now")


def _is_imperative(wording):
    lowered = str(wording or "").lower()
    if lowered.startswith("adopt ") and "proposed" not in lowered:
        return True
    return any(marker in lowered for marker in _IMPERATIVE_MARKERS)


def _conflict_pair(text):
    """Return the (ref_a, ref_b) pair when text names an ev-N/ev-M conflict, else None."""
    import re
    ids = re.findall(r"[A-Za-z]+-\d+", str(text or ""))
    if len(ids) < 2 or "conflict" not in str(text or "").lower():
        return None
    return (ids[0], ids[1])


def validate_recommendation(rec):
    """Accept a contract-valid proposal; raise RecommendationError otherwise."""
    if not isinstance(rec, dict):
        raise RecommendationError("recommendation must be a dict")
    for field in FIELDS:
        if field not in rec:
            raise RecommendationError("missing field: %s" % field)
    for field in ("proposal_wording", "basis", "uncertainty_limits",
                  "suggested_next_action", "decision_status"):
        value = rec[field]
        if not isinstance(value, str) or not value.strip():
            raise RecommendationError("blank mandatory field: %s" % field)
    wording = rec["proposal_wording"]
    basis = rec["basis"]
    limits = rec["uncertainty_limits"]
    action = rec["suggested_next_action"]
    refs = rec["evidence_record_refs"]
    alternatives = rec["alternatives"]

    if _is_imperative(wording):
        raise RecommendationError("proposal wording must frame a proposal, not an imperative")
    if _has_certainty_language(wording) or _has_certainty_language(basis):
        raise RecommendationError("certainty language is not allowed in a proposal")
    if "propos" not in wording.lower():
        raise RecommendationError("proposal wording must carry the proposal framing")

    if isinstance(action, list):
        raise RecommendationError("suggested_next_action must be a single action, not a list")
    if not isinstance(action, str) or not action.strip():
        raise RecommendationError("suggested_next_action must name exactly one action")
    if action not in ACTION_FAMILIES and not _is_supply_or_investigate_action(action):
        raise RecommendationError("suggested_next_action must be a researcher action")
    lowered_action = action.lower()
    if ". " in lowered_action.strip().rstrip(".") or lowered_action.count("review") > 1:
        raise RecommendationError("suggested_next_action must be exactly one action")

    if rec["decision_status"] != DECISION_STATUS_LITERAL:
        raise RecommendationError("decision_status must be %r" % DECISION_STATUS_LITERAL)

    if not isinstance(refs, list):
        raise RecommendationError("evidence_record_refs must be a list")
    for ref in refs:
        if isinstance(ref, str) and ref.startswith("prepared_card:"):
            raise RecommendationError("prepared-card refs are not citable at HEAD")

    named = _record_ids(basis)
    cited = set(r for r in refs if isinstance(r, str))
    if not named <= cited:
        missing = sorted(named - cited)
        raise RecommendationError("basis names uncited record(s): %s" % ", ".join(missing))

    if not refs:
        if not _states_no_record(basis):
            raise RecommendationError("empty refs require a stated missing record in basis")
        if not _is_supply_or_investigate_action(action):
            raise RecommendationError(
                "empty refs require a supply/investigate suggested_next_action")

    if not limits.strip():
        raise RecommendationError("uncertainty_limits must state a limit")
    pair = _conflict_pair(basis)
    if pair is not None:
        a, b = pair
        blob = limits.lower()
        if a.lower() not in blob or b.lower() not in blob or "conflict" not in blob:
            raise RecommendationError(
                "uncertainty_limits must carry the same conflict named in basis")
    lowered_basis = basis.lower()
    if "release 2023" in lowered_basis and "release" not in limits.lower():
        raise RecommendationError(
            "uncertainty_limits must carry the release-2023 scope of the claim")
    if "column" in lowered_basis and "populated" in lowered_basis:
        if "column" not in limits.lower() and "unverified" not in limits.lower():
            raise RecommendationError(
                "uncertainty_limits must note the unverified column claim")

    if pair is not None and "open" not in limits.lower() and "unresolved" not in limits.lower():
        raise RecommendationError("a named conflict must stay open in uncertainty_limits")

    if not isinstance(alternatives, list) or len(alternatives) > 2:
        raise RecommendationError("alternatives must be a list of at most two")

    if alternatives == [] and "no alternative found in the records" not in lowered_basis:
        raise RecommendationError(
            "empty alternatives require the stated 'no alternative found' form")

    if "reason not recorded" in lowered_basis:
        after = lowered_basis.split("reason not recorded", 1)[1]
        segment = after.split(".", 1)[0].strip().lstrip(":—- ").strip()
        if segment:
            raise RecommendationError(
                "basis cannot declare the reason missing and then supply one")
    return True


def build_recommendation(event=None, **fields):
    """Validate and return a proposal; no event ever promotes it to a decision."""
    if event is not None and fields.get("decision_status") != DECISION_STATUS_LITERAL:
        raise RecommendationError(
            "no event promotes a proposal to a decision: %r" % (event,))
    rec = dict(fields)
    rec["decision_status"] = DECISION_STATUS_LITERAL
    validate_recommendation(rec)
    return rec


def coach(level, waiting_items=(), left_off=(), verified_at=None, basis=None):
    """Render a calm/firm/pointed note from this project's own recorded items.

    The level is passed through (S1 never computes it); text and basis derive
    only from the supplied items. Zero model calls. No deadline, score, or
    moralizing tokens; no unearned praise.
    """
    if level is None:
        raise ValueError("level is required")
    if level not in ("calm", "firm", "pointed"):
        raise ValueError("unknown level: %r" % (level,))
    waiting = [w for w in (waiting_items or [])
               if isinstance(w, dict) and w.get("text") != "Nothing waiting."]
    left = [d for d in (left_off or [])
            if isinstance(d, dict) and d.get("text") != "No decisions yet."]
    count = len(waiting)
    verified = _parse_ts(verified_at)
    ages = []
    for item in waiting:
        age = _age_days(item.get("saved_at"), verified_at)
        if age is not None and verified is not None:
            ages.append(age)
    oldest_age = max(ages) if ages else None
    noun = "item" if count == 1 else "items"

    if basis is None:
        if count == 0:
            if left:
                basis = ("No items are waiting; %d recorded decision(s) on file."
                         % len(left))
            else:
                basis = "No items are waiting and no decisions are recorded."
        elif oldest_age is None:
            basis = "Oldest waiting age is unknown; %d %s waiting." % (count, noun)
        else:
            day_noun = "day" if oldest_age == 1 else "days"
            basis = ("Oldest waiting item is %d %s old; %d %s waiting."
                     % (oldest_age, day_noun, count, noun))

    if count == 0:
        if left:
            text = ("No pressure \u2014 the recorded decision is on file, so the "
                    "next question can start whenever you are ready.")
        else:
            text = ("No pressure \u2014 nothing is recorded as waiting, so starting a "
                    "fresh question is a fine next step whenever you are ready.")
    elif level == "pointed":
        if oldest_age is None:
            text = ("The waiting items need a stated next step \u2014 please say in "
                    "words what the next step is, or defer one explicitly with a reason.")
        else:
            text = ("The oldest waiting item has been open %d days with %d %s waiting "
                    "\u2014 please state the next step in words, or defer one "
                    "explicitly with a reason." % (oldest_age, count, noun))
    elif level == "firm":
        if oldest_age is None:
            text = ("There are %d items waiting \u2014 please act on one or defer it "
                    "explicitly so nothing sits open by accident." % count)
        else:
            text = ("The oldest waiting item has been open %d days \u2014 please act on "
                    "it or defer it explicitly so it stays a conscious choice."
                    % oldest_age)
    else:
        if oldest_age is None and count:
            text = ("No pressure \u2014 the %d waiting %s are recorded, so picking one "
                    "up whenever you are ready is a fine next step." % (count, noun))
        elif count:
            text = ("No pressure \u2014 the recorded waiting items can be picked up "
                    "whenever you are ready.")
        elif left:
            text = ("No pressure \u2014 the recorded decision is on file, so the next "
                    "question can start whenever you are ready.")
        else:
            text = ("No pressure \u2014 nothing is recorded as waiting, so starting a "
                    "fresh question is a fine next step whenever you are ready.")
    return {"level": level, "text": text, "basis": basis, "model_calls": 0}


def _waiting_ages(row, verified_at):
    items = row.get("waiting_items") or []
    oldest_age = None
    oldest_item = None
    for item in items:
        if not isinstance(item, dict):
            continue
        age = _age_days(item.get("saved_at"), verified_at)
        if age is not None:
            if oldest_age is None or age > oldest_age:
                oldest_age = age
                oldest_item = item
    return oldest_age, oldest_item


def _last_activity_age(row, verified_at):
    return _age_days(row.get("last_activity"), verified_at)


def radar(rows, verified_at=None):
    """Cross-project stale view over plain-dict briefing rows.

    Stale iff (a) oldest parseable waiting age >= 14 days, or (b) waiting items
    present and no recorded activity for >= 14 days. Never stale without waiting
    items. Stale rows order first by oldest age descending; the remainder keeps
    the launch ordering rule. At most 3 stale flags are prominent; all stale
    rows survive. Unparseable verified_at renders counts with a limitation line
    and no flags or level.
    """
    rows = list(rows or [])
    verified_ok = _parse_ts(verified_at) is not None
    out_rows = []
    stale_entries = []
    for row in rows:
        pid = row.get("project_id")
        project = row.get("project", "Project %s" % pid)
        items = row.get("waiting_items") or []
        item_list = [i for i in items if isinstance(i, dict)]
        # Shared effective computation (DESIGN §4.5 ruling, Option A): one
        # computation feeds the staleness decision, the emitted row, the
        # remainder ordering (via the emitted row), and the row basis alike.
        raw_count = row.get("waiting_count", 0)
        if isinstance(raw_count, bool):
            input_count = int(raw_count)
        elif isinstance(raw_count, int):
            input_count = raw_count
        elif isinstance(raw_count, float) and raw_count == raw_count:
            input_count = int(raw_count)
        else:
            input_count = 0
        waiting_count = max(input_count, len(item_list))
        has_waiting = bool(row.get("has_waiting") or waiting_count or item_list)
        oldest_age, oldest_item = _waiting_ages(row, verified_at)
        activity_age = _last_activity_age(row, verified_at)
        stale = False
        if verified_ok and has_waiting:
            if oldest_age is not None and oldest_age >= _STALE_MIN_AGE_DAYS:
                stale = True
            elif activity_age is not None and activity_age >= _STALE_MIN_AGE_DAYS:
                stale = True
        flag = None
        if stale and oldest_item is not None and oldest_age is not None:
            flag = {"project": project,
                    "oldest_text": _truncate_text(oldest_item.get("text", "")),
                    "age_days": oldest_age,
                    "waiting_count": waiting_count}
        if verified_ok and not has_waiting:
            basis = "No items are waiting; the project is dormant, not stale."
        elif not verified_ok and has_waiting:
            basis = ("Ages cannot be verified from the records; "
                     "counts are shown with no age flag.")
        elif oldest_age is None and has_waiting:
            basis = ("Oldest waiting age is unknown; "
                     "%d items waiting." % waiting_count)
        else:
            basis = ("Oldest waiting item is %d days old; %d items waiting."
                     % (oldest_age, waiting_count))
        out = {"project_id": pid,
               "project": project,
               "has_waiting": has_waiting,
               "waiting_count": waiting_count,
               "waiting_items": item_list,
               "has_decision": bool(row.get("has_decision")),
               "decision_count": row.get("decision_count", 0),
               "input_changed": bool(row.get("input_changed")),
               "last_activity": row.get("last_activity"),
               "stale": bool(stale),
               "stale_prominent": False,
               "basis": basis}
        if flag is not None:
            out["flag"] = flag
        out_rows.append(out)
        if stale:
            stale_entries.append((oldest_age if oldest_age is not None else -1, out))

    stale_entries.sort(key=lambda e: e[0], reverse=True)
    for _, entry in stale_entries[:_PROMINENT_CAP]:
        entry["stale_prominent"] = True

    def remainder_key(entry):
        r = entry
        return ((0 if r["has_waiting"] else 1),
                (0 if r["has_decision"] else 1),
                (r["project"] or "").lower(),
                r["project_id"])

    stale_first = [e for _, e in stale_entries]
    rest = [r for r in out_rows if not r["stale"]]
    rest.sort(key=remainder_key)
    ordered = stale_first + rest

    if verified_ok:
        limitation = ""
        level = ""
    else:
        limitation = ("verified_at is missing or unparseable; counts only, "
                      "no age flags or level.")
        level = ""
        for r in ordered:
            r["stale"] = False
            r["stale_prominent"] = False
            r.pop("flag", None)
    return {"rows": ordered,
            "project_count": len(ordered),
            "ordering_rule": _LAUNCH_ORDERING_RULE,
            "model_calls": 0,
            "verified_at": verified_at,
            "limitation": limitation,
            "level": level,
            "basis": ("Stale means oldest waiting age or inactivity of 14 days or more; "
                      "at most 3 stale flags are prominent.")}


def deadline_wording_allowed(record):
    """D1 gate: True only for a researcher-recorded explicit date.

    The provisional D1 carrier is ``recorded_date`` plus ``recording_researcher``.
    Waiting age (D2), inactivity (D3), and missing/unparseable dates (D4) never
    allow deadline wording.
    """
    if not isinstance(record, dict):
        return False
    date = record.get("recorded_date")
    researcher = record.get("recording_researcher")
    if not isinstance(date, str) or not date.strip():
        return False
    if not isinstance(researcher, str) or not researcher.strip():
        return False
    if _parse_ts(date) is None:
        return False
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}", date.strip()):
        return False
    return True
