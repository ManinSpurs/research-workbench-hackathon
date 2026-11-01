"""Slice D2 -- read-only adaptation-readiness composer.

Design source: ``current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md`` section 7.2,
slice D2. A read-only composer over plain dicts shaped like the public
outputs of ``pack_memory``, ``pending_clarifications``, ``briefing`` and
``launch_briefing``, plus an optional caller-supplied proposal/version dict.

Standard library only: no DB, no network, no model calls, and no imports
from ``harness.py``, ``api.py``, ``store.py`` or the parallel slice-D1
module ``adaptive_pack.py`` (the small eligibility helpers below are local,
to avoid coupling).

Guarantees (design sections 2, 3, 6):

- count-only rows: record text is never copied into a row, so content from
  one (project, pack) scope can never appear in another scope's row;
- proposed/approved/deprecated states come ONLY from an explicitly
  passed-in proposal/version dict; without one only cold/accumulating occur;
- no invented versions, scores, percentages, accomplishments, or dates;
- missing/unparseable timestamps degrade to count-only with the literal
  age ``unknown``;
- a passed-in proposal renders with the ``proposal -- not decided`` badge,
  never as a decision;
- rows grant no tools, scope, or budget, and carry no deadline wording
  unless an explicit recorded date value is passed in and quoted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

__all__ = [
    "ALLOWLIST_RECORD_TYPES",
    "DENYLIST_RECORD_TYPES",
    "READINESS_STATES",
    "PROPOSAL_BADGE",
    "NO_GRANT_NOTE",
    "UNKNOWN",
    "GAP_PATTERN_MIN",
    "is_eligible_record",
    "count_records",
    "briefing_decision_count",
    "tally_repeated_gaps",
    "oldest_age_label",
    "validate_proposal_shape",
    "validate_version_shape",
    "readiness_from",
    "check_benchmark_report",
    "render_row_lines",
    "compose_row",
    "compose_readiness",
]

#: Literal badge every rendered proposal must carry verbatim.
PROPOSAL_BADGE = "proposal \u2014 not decided"

#: Fixed no-grant disclaimer attached to every composed row.
NO_GRANT_NOTE = "Guidance text only; grants no tools, scope, or budget."

#: Literal age used whenever timestamps are missing or unparseable.
UNKNOWN = "unknown"

#: A repeated gap class becomes a pattern only at >= 2 occurrences.
GAP_PATTERN_MIN = 2

#: A1-A6 eligible accumulation categories (design section 2.1).
ALLOWLIST_RECORD_TYPES = (
    "confirmed_decision",      # A1: confirmed decisions + their real reasons
    "evidence_card",            # A2: confirmed evidence cards (inspected + assessed)
    "recommendation_outcome",  # A3: accepted/rejected recommendations, outcome recorded
    "user_correction",         # A4: user corrections to wording, mappings, or scope
    "registered_source",       # A5: registered sources (dictionaries, release notes)
    "repeated_gap",            # A6: repeated unresolved gaps (counts + classes only)
)

#: Never-eligible input kinds (design section 2.2). Rejected with a reason.
DENYLIST_RECORD_TYPES = (
    "ai_draft",
    "unconfirmed_proposal",
    "shadow_output",
    "mock_output",
    "session_only",
    "cross_project",
    "uncited",
    "pseudo_confirmation",
)

#: Readiness labels (design section 6.1), lowercase slice-D2 spelling.
READINESS_STATES = (
    "cold",
    "accumulating",
    "proposed",
    "approved",
    "deprecated",
)

#: Placeholder briefing texts that mean "no decision recorded".
_EMPTY_DECISION_TEXTS = frozenset({"No decisions yet.", "No decisions in this pack."})

#: Clarification statuses that still count as unresolved gaps (mirrors the
#: ``pending_clarifications`` SELECT filter in ``harness.py``).
_UNRESOLVED_STATUSES = frozenset({"pending", "deferred", "retryable"})

#: Confirmation channels that never count as confirmation.
_PSEUDO_CONFIRMATIONS = frozenset({"timeout", "silence", "nudge", "revisit", "preparation"})

#: Recorded outcomes accepted for recommendation_outcome rows.
_RECOMMENDATION_OUTCOMES = frozenset({"accepted", "rejected"})

#: Wording that must never appear in rendered proposal text.
_FORBIDDEN_TEXT_PATTERNS = (
    (re.compile(r"100\s*%"), "certainty claim '100%'"),
    (re.compile(r"\bguarantee[sd]?\b", re.IGNORECASE), "certainty claim 'guaranteed'"),
    (re.compile(r"\bconfirmed-correct\b", re.IGNORECASE), "certainty claim 'confirmed-correct'"),
    (re.compile(r"\bmust\b", re.IGNORECASE), "certainty wording 'must'"),
    (re.compile(r"\bscores?\b", re.IGNORECASE), "score wording"),
    (re.compile(r"\bratings?\b", re.IGNORECASE), "score wording"),
    (re.compile(r"\b\d+\s*%"), "percentage wording"),
    (re.compile(r"\bpercent\b", re.IGNORECASE), "percentage wording"),
    (re.compile(r"\bcertainty\b", re.IGNORECASE), "certainty wording"),
    (re.compile(r"\bdeadline\b", re.IGNORECASE), "deadline wording"),
    (re.compile(r"\bdue\s+(by|date)\b", re.IGNORECASE), "deadline wording"),
    (re.compile(r"\boverdue\b", re.IGNORECASE), "deadline wording"),
    (re.compile(r"\basap\b", re.IGNORECASE), "deadline wording"),
)

#: Deadline wording scan applied to composed row text in tests.
_DEADLINE_OUTPUT_PATTERNS = (
    re.compile(r"\bdeadline\b", re.IGNORECASE),
    re.compile(r"\boverdue\b", re.IGNORECASE),
    re.compile(r"\bdue\b", re.IGNORECASE),
)

#: Key-name scan for the benchmark-report shape check.
_SCORE_KEY_PATTERN = re.compile(r"score|percent|accuracy|certainty|confidence", re.IGNORECASE)
_PERCENT_VALUE_PATTERN = re.compile(r"\d\s*%|\bpercent\b", re.IGNORECASE)


def _parse_ts(value):
    """Parse an ISO timestamp to an aware UTC datetime; None when unparseable."""
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


def _norm_gap(text):
    """Normalize a gap class for tallying: lowercase, whitespace-collapsed."""
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _valid_ids(value):
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _contains_forbidden(text):
    if not isinstance(text, str):
        return (False, "")
    for pattern, description in _FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            return (True, description)
    return (False, "")


def contains_deadline_wording(text):
    """True when rendered text carries deadline/due/overdue wording."""
    if not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _DEADLINE_OUTPUT_PATTERNS)


def is_eligible_record(record, project_id, pack_id):
    """Local A1-A6 eligibility predicate over one plain record dict.

    Returns (True, reason) for eligible rows, (False, reason) otherwise.
    Denylist kinds, unconfirmed rows, session-only rows, cross-project or
    cross-pack rows, pseudo-confirmations, and uncited rows are rejected.
    """
    if not isinstance(record, dict):
        return (False, "ignored: record must be a dict row")
    kind = record.get("record_type")
    if kind in DENYLIST_RECORD_TYPES:
        return (False, "ignored: %s is never eligible" % (kind,))
    if kind not in ALLOWLIST_RECORD_TYPES:
        return (False, "ignored: unknown record kind %r" % (kind,))
    if record.get("confirmed_via") in _PSEUDO_CONFIRMATIONS:
        return (
            False,
            "ignored: %s is pseudo-confirmation, never confirmation"
            % (record.get("confirmed_via"),),
        )
    if not record.get("confirmed", False):
        return (False, "ignored: %s lacks explicit confirmation" % (kind,))
    if record.get("session_only", False):
        return (False, "ignored: session_only content is excluded from durable memory")
    if record.get("cross_project", False):
        return (False, "ignored: cross-project records never leave their project")
    if "project_id" in record and record.get("project_id") != project_id:
        return (False, "ignored: record belongs to another project")
    if "pack_id" in record and record.get("pack_id") != pack_id:
        return (False, "ignored: record belongs to another pack")
    if not _valid_ids(record.get("record_ids")):
        return (False, "ignored: record carries no resolvable cited record IDs")
    if kind == "recommendation_outcome":
        if record.get("outcome") not in _RECOMMENDATION_OUTCOMES:
            return (False, "ignored: recommendation outcome needs a recorded accepted/rejected verdict")
    if kind == "repeated_gap":
        gap_class = record.get("gap_class")
        if not isinstance(gap_class, str) or not gap_class.strip():
            return (False, "ignored: repeated gap needs a non-empty gap class")
        scope_ids = record.get("scope_ids")
        if (
            not isinstance(scope_ids, list)
            or len({item for item in scope_ids if isinstance(item, str) and item.strip()}) < 2
        ):
            return (False, "ignored: repeated gap needs >=2 distinct question/task IDs -- "
                            "a single occurrence never qualifies")
    return (True, "eligible: %s" % (kind,))


def count_records(records, project_id, pack_id):
    """Count eligible explicit records by category (never copies record text).

    The ``repeated_gap`` category is tallied separately by
    :func:`tally_repeated_gaps`; it is excluded here so occurrences are
    never double-counted. Returns (counts, eligible_total, ineligible).
    """
    counts = {
        "confirmed_decisions": 0,
        "evidence_cards": 0,
        "recommendation_outcomes": 0,
        "user_corrections": 0,
        "registered_sources": 0,
        "repeated_gaps": 0,
    }
    ineligible = 0
    for record in records or []:
        if isinstance(record, dict) and record.get("record_type") == "repeated_gap":
            continue
        ok, _ = is_eligible_record(record, project_id, pack_id)
        if not ok:
            ineligible += 1
            continue
        kind = record.get("record_type")
        if kind == "confirmed_decision":
            counts["confirmed_decisions"] += 1
        elif kind == "evidence_card":
            counts["evidence_cards"] += 1
        elif kind == "recommendation_outcome":
            counts["recommendation_outcomes"] += 1
        elif kind == "user_correction":
            counts["user_corrections"] += 1
        elif kind == "registered_source":
            counts["registered_sources"] += 1
    eligible_total = sum(counts.values())
    return (counts, eligible_total, ineligible)


def briefing_decision_count(briefing):
    """Count confirmed decisions visible in a briefing-shaped dict.

    Counts only ``where_you_left_off`` items that are not the empty
    placeholders; the item text itself is never copied anywhere.
    """
    if not isinstance(briefing, dict):
        return 0
    parts = briefing.get("parts")
    if not isinstance(parts, dict):
        return 0
    items = parts.get("where_you_left_off")
    if not isinstance(items, list):
        return 0
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip() and text not in _EMPTY_DECISION_TEXTS:
            total += 1
    return total


def _pending_gap_classes(pending, pack_id):
    """Extract normalized gap classes from a pending-clarifications dict.

    Only unresolved statuses count; items pinned to another pack are
    skipped so pack rows never mix. Returns (occurrences, scopes,
    skipped) where scopes maps each gap class to its set of distinct
    ``question_id``/``task_id`` scope tokens -- a class repeated on one
    question/task alone never qualifies as a pattern (mirrors the D1
    >=2 distinct scope_ids rule in ``adaptive_pack.py``).
    """
    occurrences = {}
    scopes = {}
    skipped = 0
    if not isinstance(pending, dict):
        return (occurrences, scopes, skipped)
    items = pending.get("clarifications")
    if not isinstance(items, list):
        return (occurrences, scopes, skipped)
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        if item.get("status") not in _UNRESOLVED_STATUSES:
            continue
        context = item.get("context")
        if isinstance(context, dict) and context.get("pack_id") and context.get("pack_id") != pack_id:
            skipped += 1
            continue
        clarification = item.get("clarification")
        gap = clarification.get("gap") if isinstance(clarification, dict) else None
        if gap is None:
            gap = item.get("gap")
        gap_class = _norm_gap(gap)
        if not gap_class:
            skipped += 1
            continue
        occurrences[gap_class] = occurrences.get(gap_class, 0) + 1
        token = "q:%s|t:%s" % (item.get("question_id") or "", item.get("task_id") or "")
        scopes.setdefault(gap_class, set()).add(token)
    return (occurrences, scopes, skipped)


def tally_repeated_gaps(pending, records, project_id, pack_id):
    """Tally unresolved gap classes within one scope (count-only).

    A class is a pattern only at >= 2 occurrences across >= 2 distinct
    questions/tasks (mirrors the D1 >=2 distinct scope_ids rule); below
    threshold -- or repeated on one question/task alone -- it is reported
    as single occurrences, never as a pattern. Returns
    (tally, gap_total, ineligible_gaps) where tally holds ``patterns``
    ([{"gap_class", "occurrences"}]), ``single_occurrences``, and
    ``single_classes``.
    """
    occurrences, scopes, _ = _pending_gap_classes(pending, pack_id)
    ineligible_gaps = 0
    for record in records or []:
        if not isinstance(record, dict) or record.get("record_type") != "repeated_gap":
            continue
        ok, _ = is_eligible_record(record, project_id, pack_id)
        if not ok:
            ineligible_gaps += 1
            continue
        gap_class = _norm_gap(record.get("gap_class"))
        occurrences[gap_class] = occurrences.get(gap_class, 0) + 1
        scopes.setdefault(gap_class, set()).update(
            item.strip()
            for item in record.get("scope_ids", [])
            if isinstance(item, str) and item.strip()
        )
    pattern_classes = {
        gap_class
        for gap_class, count in occurrences.items()
        if count >= GAP_PATTERN_MIN and len(scopes.get(gap_class, ())) >= 2
    }
    patterns = [
        {"gap_class": gap_class, "occurrences": occurrences[gap_class]}
        for gap_class in sorted(pattern_classes)
    ]
    singles = sorted(gap_class for gap_class in occurrences if gap_class not in pattern_classes)
    tally = {
        "patterns": patterns,
        "single_occurrences": sum(occurrences[g] for g in singles),
        "single_classes": singles,
    }
    return (tally, sum(occurrences.values()), ineligible_gaps)


def oldest_age_label(briefing, pending, verified_at):
    """Oldest waiting age in days, or the literal ``unknown``.

    Missing or unparseable timestamps degrade to count-only with
    ``unknown``; no age is ever guessed.
    """
    verified = _parse_ts(verified_at)
    if verified is None:
        return UNKNOWN
    stamps = []
    if isinstance(briefing, dict) and isinstance(briefing.get("parts"), dict):
        waiting = briefing["parts"].get("waiting_on_you")
        if isinstance(waiting, list):
            stamps += [item.get("saved_at") for item in waiting if isinstance(item, dict)]
    if isinstance(pending, dict) and isinstance(pending.get("clarifications"), list):
        stamps += [item.get("created_at") for item in pending["clarifications"] if isinstance(item, dict)]
    oldest = None
    for stamp in stamps:
        parsed = _parse_ts(stamp)
        if parsed is None:
            continue
        age = int((verified - parsed).total_seconds() // 86400)
        if age < 0:
            age = 0
        if oldest is None or age > oldest:
            oldest = age
    if oldest is None:
        return UNKNOWN
    return "%d day%s" % (oldest, "" if oldest == 1 else "s")


def validate_proposal_shape(proposal):
    """Validate a caller-supplied adaptation proposal (never invented here)."""
    if not isinstance(proposal, dict):
        return (False, "invalid proposal: must be a dict")
    actions = proposal.get("suggested_next_actions")
    if not isinstance(actions, list) or len(actions) != 1:
        got = len(actions) if isinstance(actions, list) else 0
        return (False, "invalid proposal: exactly one suggested next action required (got %d)" % (got,))
    if not isinstance(actions[0], str) or not actions[0].strip():
        return (False, "invalid proposal: the suggested next action must be non-empty text")
    uncertainty = proposal.get("uncertainty_limits")
    if not isinstance(uncertainty, str) or not uncertainty.strip():
        return (False, "invalid proposal: uncertainty/limitations must be non-empty")
    if not _valid_ids(proposal.get("evidence_refs")):
        return (False, "invalid proposal: non-empty evidence record refs required")
    wording = proposal.get("wording", "")
    if not isinstance(wording, str) or PROPOSAL_BADGE not in wording:
        return (False, "invalid proposal: wording must carry the literal badge %r" % (PROPOSAL_BADGE,))
    for field_name, field_value in (("wording", wording), ("suggested_next_action", actions[0])):
        found, description = _contains_forbidden(field_value)
        if found:
            return (False, "invalid proposal: %s carries forbidden %s" % (field_name, description))
    return (True, "valid proposal: one action, refs cited, badge present")


def validate_version_shape(version):
    """Validate a caller-supplied overlay version dict (never invented here).

    Design AD03 + section 3 step 6 + section 6.1: an approved overlay
    carries evidence refs AND benchmark report refs -- approval without
    filed benchmark refs is refused. Deprecated versions require only the
    version label, evidence refs, and a reason (benchmark refs are an
    approval precondition, not a deprecation one). (Section 7.1 marks the
    overlay store -- including the step-6 approver/timestamp fields --
    CONCEPTUAL-ONLY, so this read-only validator does not invent storage
    for them.)
    """
    if not isinstance(version, dict):
        return (False, "invalid version: must be a dict", None)
    status = version.get("status")
    if status not in ("approved", "deprecated"):
        return (False, "invalid version: explicit status 'approved' or 'deprecated' required", None)
    overlay_version = version.get("overlay_version")
    if not isinstance(overlay_version, str) or not overlay_version.strip():
        return (False, "invalid version: explicit overlay_version required, never invented", None)
    if not _valid_ids(version.get("evidence_refs")):
        return (False, "invalid version: explicit evidence refs required, never invented", None)
    if status == "approved" and not _valid_ids(version.get("benchmark_refs")):
        return (False, "invalid version: explicit benchmark report refs required "
                       "-- AD03 requires benchmark refs filed before approval", None)
    if status == "deprecated":
        reason = version.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return (False, "invalid version: deprecated state needs an explicit reason", None)
        found, description = _contains_forbidden(reason)
        if found:
            return (False, "invalid version: reason carries forbidden %s" % (description,), None)
    return (True, "valid version: %s" % (status,), status)


def readiness_from(eligible_total, proposal=None, version=None):
    """Decide the readiness label without inventing state.

    proposed/approved/deprecated come ONLY from an explicitly passed-in,
    valid proposal/version dict; otherwise only cold (zero records) or
    accumulating is possible. Returns (label, note, detail) where detail
    carries the badge/action for proposed or the version line otherwise.
    """
    if version is not None:
        ok, note, status = validate_version_shape(version)
        if ok and status == "approved":
            refs = ", ".join(version["evidence_refs"])
            brefs = ", ".join(version["benchmark_refs"])
            line = ("Overlay %s active for new work only; old work unchanged. "
                    "Evidence refs: %s. Benchmark refs: %s." % (
                        version["overlay_version"].strip(), refs, brefs))
            return ("approved", note, line)
        if ok and status == "deprecated":
            line = "Overlay %s rolled back for new work. Reason: %s." % (
                version["overlay_version"].strip(), version["reason"].strip())
            return ("deprecated", note, line)
        if eligible_total == 0:
            return ("cold", "version ignored (%s); no eligible records, base guidance only." % (note,), None)
        return ("accumulating",
                "version ignored (%s); %d eligible records, counts only." % (note, eligible_total), None)
    if proposal is not None:
        ok, note = validate_proposal_shape(proposal)
        if ok:
            return ("proposed", note, None)
        if eligible_total == 0:
            return ("cold", "proposal ignored (%s); no eligible records, base guidance only." % (note,), None)
        return ("accumulating",
                "proposal ignored (%s); %d eligible records, counts only." % (note, eligible_total), None)
    if eligible_total == 0:
        return ("cold", "no eligible accumulation records yet; base guidance only.", None)
    return ("accumulating", "%d eligible records; counts only, no scores." % (eligible_total,), None)


def _scan_report(node, path, problems):
    if isinstance(node, dict):
        for key, value in node.items():
            here = "%s.%s" % (path, key) if path else str(key)
            if isinstance(key, str) and _SCORE_KEY_PATTERN.search(key):
                problems.append("invalid: field %r carries score/percentage/certainty wording" % (here,))
            _scan_report(value, here, problems)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_report(value, "%s[%d]" % (path, index), problems)
    elif isinstance(node, str):
        if _PERCENT_VALUE_PATTERN.search(node):
            problems.append("invalid: field %r carries a percentage value" % (path,))
    elif isinstance(node, bool):
        pass  # booleans are verdict flags, never score-like values
    elif isinstance(node, (int, float)):
        problems.append("invalid: field %r carries a score-like numeric value" % (path,))


def check_benchmark_report(report):
    """Shape-check a frozen-benchmark report: refs + outcomes, no scores.

    A report carrying any score/percentage/certainty field is flagged
    invalid; its values are never rendered.
    """
    if not isinstance(report, dict):
        return {"valid": False, "case_count": 0,
                "reasons": ["invalid: benchmark report must be a dict"]}
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        return {"valid": False, "case_count": 0,
                "reasons": ["invalid: benchmark report needs a non-empty 'cases' list"]}
    reasons = []
    case_count = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            reasons.append("invalid: cases[%d] must be a dict" % (index,))
            continue
        ref = case.get("case_ref", case.get("case_id"))
        if not isinstance(ref, str) or not ref.strip():
            reasons.append("invalid: cases[%d] needs a non-empty case ref" % (index,))
            continue
        outcome = case.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            reasons.append("invalid: cases[%d] needs a non-empty per-case outcome" % (index,))
            continue
        case_count += 1
    problems = []
    _scan_report(report, "", problems)
    reasons.extend(problems)
    if reasons:
        return {"valid": False, "case_count": case_count, "reasons": reasons}
    return {"valid": True, "case_count": case_count,
            "reasons": ["ok: %d cases with refs and outcomes only; no scores rendered" % (case_count,)]}


def render_row_lines(row, recorded_date=None):
    """Render a composed row as labeled text lines (count-only).

    Never renders a proposal as a decision, grants nothing, and carries
    no deadline wording unless ``recorded_date`` is passed in, in which
    case the value is quoted verbatim.
    """
    counts = row.get("counts", {})
    lines = []
    lines.append("Readiness for project %s + pack %s: %s." % (
        row.get("project_id"), row.get("pack_id"), row.get("readiness")))
    lines.append("Counts: %d eligible records "
                 "(confirmed decisions %d, evidence cards %d, "
                 "recommendation outcomes %d, user corrections %d, "
                 "registered sources %d, repeated gap occurrences %d); "
                 "%d ineligible inputs ignored." % (
                     row.get("eligible_total", 0),
                     counts.get("confirmed_decisions", 0),
                     counts.get("evidence_cards", 0),
                     counts.get("recommendation_outcomes", 0),
                     counts.get("user_corrections", 0),
                     counts.get("registered_sources", 0),
                     counts.get("repeated_gaps", 0),
                     row.get("ineligible_ignored", 0)))
    lines.append("Oldest waiting age: %s." % (row.get("oldest_age", UNKNOWN),))
    tally = row.get("repeated_gaps_tally", {})
    for pattern in tally.get("patterns", []):
        lines.append("Pattern gap %r seen %d times (count only; never a quality claim)." % (
            pattern.get("gap_class"), pattern.get("occurrences")))
    if tally.get("single_occurrences"):
        lines.append("%d single gap occurrence(s) below pattern threshold; not a pattern." % (
            tally.get("single_occurrences"),))
    if row.get("readiness") == "proposed":
        lines.append(PROPOSAL_BADGE + ".")
        if row.get("proposal_action"):
            lines.append("Suggested next action: %s" % (row["proposal_action"],))
        if row.get("proposal_refs"):
            lines.append("Evidence refs: %s" % (", ".join(row["proposal_refs"]),))
    if row.get("version_line"):
        lines.append(row["version_line"])
    benchmark = row.get("benchmark")
    if isinstance(benchmark, dict):
        if benchmark.get("valid"):
            lines.append("Benchmark report: %d cases with refs and outcomes, no scores." % (
                benchmark.get("case_count", 0),))
        else:
            lines.append("Benchmark report invalid: %s" % ("; ".join(benchmark.get("reasons", [])),))
    lines.append(row.get("readiness_note", ""))
    lines.append(NO_GRANT_NOTE)
    if isinstance(recorded_date, str) and recorded_date.strip():
        lines.append("Quoted recorded date: %r" % (recorded_date.strip(),))
    return [line for line in lines if isinstance(line, str) and line.strip()]


def compose_row(project_id, pack_id, pack_version=None, manifest_sha256=None,
                briefing=None, pack_memory=None, pending=None, records=None,
                proposal=None, version=None, benchmark_report=None,
                verified_at=None, recorded_date=None):
    """Compose one read-only readiness row for a single (project, pack) scope.

    Every count and tally comes only from this scope's own dicts; nothing
    is read from or written to any database, and no record text is echoed,
    so scopes cannot leak into each other.
    """
    counts, eligible_total, ineligible = count_records(records, project_id, pack_id)
    briefing_n = briefing_decision_count(briefing)
    counts["confirmed_decisions"] += briefing_n
    eligible_total += briefing_n
    tally, gap_total, ineligible_gaps = tally_repeated_gaps(pending, records, project_id, pack_id)
    counts["repeated_gaps"] = gap_total
    eligible_total += gap_total
    ineligible += ineligible_gaps
    if verified_at is None and isinstance(briefing, dict):
        verified_at = briefing.get("verified_at")
    age = oldest_age_label(briefing, pending, verified_at)
    label, note, detail = readiness_from(eligible_total, proposal=proposal, version=version)
    benchmark = check_benchmark_report(benchmark_report) if benchmark_report is not None else None
    row = {
        "project_id": project_id,
        "pack_id": pack_id,
        "pack_version": pack_version,
        "manifest_sha256": manifest_sha256,
        "counts": counts,
        "eligible_total": eligible_total,
        "ineligible_ignored": ineligible,
        "repeated_gaps_tally": tally,
        "oldest_age": age,
        "readiness": label,
        "readiness_note": note,
        "proposal_badge": PROPOSAL_BADGE if label == "proposed" else None,
        "proposal_action": None,
        "proposal_refs": [],
        "version_line": None,
        "benchmark": benchmark,
        "model_calls": 0,
    }
    if label == "proposed" and isinstance(proposal, dict):
        actions = proposal.get("suggested_next_actions") or []
        row["proposal_action"] = actions[0] if actions else None
        row["proposal_refs"] = list(proposal.get("evidence_refs") or [])
    if label in ("approved", "deprecated"):
        row["version_line"] = detail
    if isinstance(pack_memory, dict):
        identity = {key: pack_memory.get(key) for key in ("pack_id", "pack_version", "manifest_sha256")}
        row["pack_identity_seen"] = identity
    row["lines"] = render_row_lines(row, recorded_date=recorded_date)
    if isinstance(recorded_date, str) and recorded_date.strip():
        row["quoted_recorded_date"] = recorded_date.strip()
    row["no_grant_note"] = NO_GRANT_NOTE
    return row


def compose_readiness(entries, proposals=None, versions=None,
                      benchmark_reports=None, recorded_dates=None, verified_at=None):
    """Compose readiness rows for several (project, pack) scopes.

    Each row is built only from its own entry dict (briefing-like public
    outputs plus optional explicit records); proposals, versions,
    benchmark reports, and recorded dates are matched by the
    (project_id, pack_id) key and never shared across rows.
    """
    proposals = proposals or {}
    versions = versions or {}
    benchmark_reports = benchmark_reports or {}
    recorded_dates = recorded_dates or {}
    rows = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        project_id = entry.get("project_id")
        pack_id = entry.get("pack_id")
        if not project_id or not pack_id:
            continue
        key = (project_id, pack_id)
        rows.append(compose_row(
            project_id, pack_id,
            pack_version=entry.get("pack_version"),
            manifest_sha256=entry.get("manifest_sha256"),
            briefing=entry.get("briefing"),
            pack_memory=entry.get("pack_memory"),
            pending=entry.get("pending"),
            records=entry.get("records"),
            proposal=entry.get("proposal", proposals.get(key)),
            version=entry.get("version", versions.get(key)),
            benchmark_report=entry.get("benchmark_report", benchmark_reports.get(key)),
            verified_at=entry.get("verified_at", verified_at),
            recorded_date=entry.get("recorded_date", recorded_dates.get(key)),
        ))
    return {"rows": rows, "row_count": len(rows), "model_calls": 0}
