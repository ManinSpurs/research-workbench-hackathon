"""Slice D1 -- adaptive domain-pack overlay proposal pure logic.

Design source: ``current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md`` section 7.2,
Slice D1. Pure logic over hand-built dicts only: no DB, no model calls,
no network, and no imports from ``harness.py``, ``api.py``, ``frontend``
or ``store.py`` (only the Python standard library is used).

Public API (see ``__all__``):
  predicates   -- ``check_source_eligibility``
  validators   -- ``validate_content_kind``, ``validate_overlay_entry``,
                  ``validate_proposal``, ``contains_forbidden_claims``
  composers    -- ``compose_layers``, ``render_maturity``
  constants    -- ``ALLOWLIST_SOURCES``, ``DENYLIST_SOURCES``,
                  ``ALLOWLIST_KINDS``, ``MATURITY_STATES``,
                  ``PSEUDO_CONFIRMATIONS``, ``PROPOSAL_STAMP``

This module proposes only. It exposes no activation/adopt/apply path:
an approved overlay state can only be *rendered* from an explicit
version dict passed in by the caller -- silence, timeouts and nudges
can never produce it.
"""

from __future__ import annotations

import re

__all__ = [
    "check_source_eligibility",
    "validate_content_kind",
    "validate_overlay_entry",
    "validate_proposal",
    "compose_layers",
    "render_maturity",
    "contains_forbidden_claims",
    "ALLOWLIST_SOURCES",
    "DENYLIST_SOURCES",
    "ALLOWLIST_KINDS",
    "MATURITY_STATES",
    "PSEUDO_CONFIRMATIONS",
    "PROPOSAL_STAMP",
]

#: Literal stamp every adaptation proposal must carry verbatim.
PROPOSAL_STAMP = "proposal \u2014 not decided"

#: A1-A6 allowlist record kinds (design section 2.1).
ALLOWLIST_SOURCES = (
    "confirmed_decision",       # A1: confirmed decisions + their real reasons
    "evidence_card",            # A2: confirmed evidence cards (inspected + assessed)
    "recommendation_outcome",   # A3: accepted/rejected recommendations, outcome recorded
    "user_correction",          # A4: user corrections to wording, mappings or scope
    "registered_source",        # A5: registered sources (dictionaries, release notes, excerpts)
    "repeated_gap",             # A6: repeated unresolved gaps (counts + classes only)
)

#: Denylist record kinds (design section 2.2). Never eligible.
DENYLIST_SOURCES = (
    "ai_draft",
    "unconfirmed_proposal",
    "shadow_output",
    "mock_output",
    "session_only",
    "cross_project",
    "uncited",
    "pseudo_confirmation",
)

_DENY_REASONS = {
    "ai_draft": "rejected: AI draft -- draft cards before confirmation are never eligible",
    "unconfirmed_proposal": "rejected: unconfirmed proposal -- explicit confirmation required",
    "shadow_output": "rejected: shadow-run output is illustration only, never eligible",
    "mock_output": "rejected: mock/fake transport text is plumbing only, never eligible",
    "session_only": "rejected: session-only content is excluded from durable memory",
    "cross_project": "rejected: cross-project records must never leak into another project",
    "uncited": "rejected: content without resolvable cited record IDs",
    "pseudo_confirmation": "rejected: timeout/silence/nudge is not confirmation",
}

#: Closed allowlist of overlay content kinds (design section 1.2).
ALLOWLIST_KINDS = (
    "terminology_mapping",
    "variable_mapping",
    "evidence_pattern",
    "clarification_priority",
    "checklist_item",
    "tool_preference",
    "routing_preference",
    "methodological_caution",
)

#: Maturity states (design section 6.1).
MATURITY_STATES = (
    "cold_start",
    "accumulating",
    "proposed",
    "approved",
    "deprecated",
)

#: Confirmation channels that never count as confirmation (design 2.2/3).
PSEUDO_CONFIRMATIONS = (
    "timeout",
    "silence",
    "nudge",
    "revisit",
    "preparation",
)

#: Wording that must never appear in proposal or overlay text:
#: certainty claims, fabricated scores, and deadline language.
_FORBIDDEN_PATTERNS = (
    (re.compile(r"100\s*%"), "certainty claim '100%'"),
    (re.compile(r"\bguarantee[sd]?\b", re.IGNORECASE), "certainty claim 'guaranteed'"),
    (re.compile(r"\bconfirmed-correct\b", re.IGNORECASE), "certainty claim 'confirmed-correct'"),
    (re.compile(r"\bmust\b", re.IGNORECASE), "certainty wording 'must'"),
    (re.compile(r"\bscores?\b", re.IGNORECASE), "score wording"),
    (re.compile(r"\bratings?\b", re.IGNORECASE), "score wording"),
    (re.compile(r"\b\d+\s*%", re.IGNORECASE), "percentage score wording"),
    (re.compile(r"\bpercent\b", re.IGNORECASE), "percentage wording"),
    (re.compile(r"\bdeadline\b", re.IGNORECASE), "deadline wording"),
    (re.compile(r"\bdue\s+(by|date)\b", re.IGNORECASE), "deadline wording"),
    (re.compile(r"\basap\b", re.IGNORECASE), "deadline wording"),
)

_OVERLAY_NO_GRANT_LINE = "Overlay grants no tools, scope, or budget; guidance text only."


def contains_forbidden_claims(text):
    """Return (found, description) for certainty/score/deadline wording."""
    if not isinstance(text, str):
        return (False, "")
    for pattern, description in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            return (True, description)
    return (False, "")


def _valid_record_ids(record_ids):
    return (
        isinstance(record_ids, list)
        and len(record_ids) > 0
        and all(isinstance(i, str) and i.strip() for i in record_ids)
    )


def check_source_eligibility(record):
    """Predicate: is this hand-built record row eligible (A1-A6)?

    Returns (True, reason) for eligible rows, (False, reason) otherwise.
    Every denylist category is rejected with its own reason; every
    accepted row must carry resolvable cited record IDs.
    """
    if not isinstance(record, dict):
        return (False, "rejected: record must be a dict row")
    kind = record.get("record_type")
    if kind in _DENY_REASONS:
        return (False, _DENY_REASONS[kind])
    if kind not in ALLOWLIST_SOURCES:
        return (False, "rejected: unknown source kind %r -- not in the A1-A6 allowlist" % (kind,))
    if record.get("confirmed_via") in PSEUDO_CONFIRMATIONS:
        return (
            False,
            "rejected: %s is pseudo-confirmation, never confirmation"
            % (record.get("confirmed_via"),),
        )
    if not record.get("confirmed", False):
        return (False, "rejected: %s requires explicit confirmation" % (kind,))
    if record.get("session_only", False):
        return (False, _DENY_REASONS["session_only"])
    if record.get("cross_project", False):
        return (False, _DENY_REASONS["cross_project"])
    if not _valid_record_ids(record.get("record_ids")):
        return (False, _DENY_REASONS["uncited"])
    if kind == "recommendation_outcome":
        if record.get("outcome") not in ("accepted", "rejected"):
            return (False, "rejected: recommendation outcome requires a recorded accepted/rejected verdict")
    if kind == "repeated_gap":
        scope_ids = record.get("scope_ids")
        if (
            not isinstance(scope_ids, list)
            or len({i for i in scope_ids if isinstance(i, str) and i.strip()}) < 2
        ):
            return (
                False,
                "rejected: a repeated gap needs >=2 distinct question/task IDs -- "
                "a single occurrence never qualifies",
            )
    return (True, "eligible: %s with cited record IDs" % (kind,))


def validate_content_kind(kind):
    """Closed allowlist check for overlay content kinds (design 1.2)."""
    if kind in ALLOWLIST_KINDS:
        return (True, "allowed content kind: %s" % (kind,))
    return (False, "rejected: content kind %r is outside the closed allowlist" % (kind,))


def validate_overlay_entry(entry):
    """Validate one overlay entry dict.

    Requires an allowlisted kind, non-empty text free of certainty/score/
    deadline wording, and non-empty cited record IDs. Entries making a
    pattern claim additionally require >=2 independent record IDs
    (single-anecdote patterns are rejected as insufficient repetition).
    """
    if not isinstance(entry, dict):
        return (False, "rejected: overlay entry must be a dict")
    ok, reason = validate_content_kind(entry.get("kind"))
    if not ok:
        return (False, reason)
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        return (False, "rejected: overlay entry text must be non-empty")
    found, description = contains_forbidden_claims(text)
    if found:
        return (False, "rejected: overlay entry carries forbidden %s" % (description,))
    record_ids = entry.get("record_ids")
    if not _valid_record_ids(record_ids):
        return (False, _DENY_REASONS["uncited"])
    if entry.get("is_pattern_claim", False):
        if len({i.strip() for i in record_ids}) < 2:
            return (
                False,
                "rejected: pattern claim has insufficient repetition -- "
                "a pattern requires >=2 independent record IDs",
            )
    return (True, "valid overlay entry: %s" % (entry.get("kind"),))


def validate_proposal(proposal):
    """Validate an adaptation-proposal dict (design section 3, step 2).

    Requires exactly one suggested next action, non-empty
    uncertainty/limitations, non-empty evidence refs, the literal
    ``proposal -- not decided`` stamp in the wording, and no
    certainty/score/deadline wording.
    """
    if not isinstance(proposal, dict):
        return (False, "rejected: proposal must be a dict")
    actions = proposal.get("suggested_next_actions")
    if not isinstance(actions, list) or len(actions) == 0:
        return (False, "rejected: proposal must carry exactly one suggested next action (got zero)")
    if len(actions) != 1:
        return (
            False,
            "rejected: proposal must carry exactly one suggested next action (got %d)"
            % (len(actions),),
        )
    if not isinstance(actions[0], str) or not actions[0].strip():
        return (False, "rejected: the suggested next action must be a non-empty string")
    uncertainty = proposal.get("uncertainty_limits")
    if not isinstance(uncertainty, str) or not uncertainty.strip():
        return (False, "rejected: proposal uncertainty/limitations must be non-empty")
    refs = proposal.get("evidence_refs")
    if not _valid_record_ids(refs):
        return (False, "rejected: proposal must cite non-empty evidence record refs")
    wording = proposal.get("wording", "")
    if not isinstance(wording, str) or PROPOSAL_STAMP not in wording:
        return (
            False,
            "rejected: proposal wording must carry the literal stamp %r" % (PROPOSAL_STAMP,),
        )
    for field_name, field_value in (("wording", wording), ("suggested_next_actions[0]", actions[0])):
        found, description = contains_forbidden_claims(field_value)
        if found:
            return (
                False,
                "rejected: proposal %s carries forbidden %s" % (field_name, description),
            )
    return (True, "valid proposal: one action, refs cited, stamp present")


def compose_layers(base_guidance, overlay_entries, session_pin):
    """Compose the three layers as visibly distinct labeled text blocks.

    The composer only labels and orders: base block first, overlay block
    second, session block third. Overlay text grants nothing -- a fixed
    no-grant disclaimer is part of the overlay block.
    """
    lines = []
    lines.append("### BLOCK 1/3 -- BASE PACK GUIDANCE (immutable, versioned)")
    if isinstance(base_guidance, str) and base_guidance.strip():
        lines.append(base_guidance)
    else:
        lines.append("(no base guidance provided)")
    lines.append("")
    lines.append("### BLOCK 2/3 -- OVERLAY GUIDANCE (project-confirmed deltas; proposals only)")
    lines.append(_OVERLAY_NO_GRANT_LINE)
    if overlay_entries:
        for entry in overlay_entries:
            kind = entry.get("kind", "?") if isinstance(entry, dict) else "?"
            text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
            refs = entry.get("record_ids", []) if isinstance(entry, dict) else []
            ref_text = ", ".join(refs) if isinstance(refs, list) else str(refs)
            lines.append("- [%s] %s (refs: %s)" % (kind, text, ref_text))
    else:
        lines.append("(no overlay entries -- base guidance only)")
    lines.append("")
    lines.append("### BLOCK 3/3 -- SESSION CONTEXT (ephemeral; this session only)")
    if isinstance(session_pin, dict) and session_pin:
        for key in sorted(session_pin):
            lines.append("%s: %s" % (key, session_pin[key]))
    else:
        lines.append("(no active session)")
    return "\n".join(lines) + "\n"


def render_maturity(state, counts=None, proposal=None, version=None, reason=None):
    """Render one maturity state honestly (design section 6.1).

    Cold start invents no accomplishment; accumulating renders counts
    only; proposed shows the proposal stamp; approved and deprecated
    require explicit caller-supplied dicts and are never invented here.
    """
    if state not in MATURITY_STATES:
        raise ValueError("unknown maturity state %r" % (state,))
    if state == "cold_start":
        return (
            "No confirmed project history for this pack yet -- using base guidance.\n"
        )
    if state == "accumulating":
        counts = counts or {}
        decisions = counts.get("confirmed_decisions", 0)
        cards = counts.get("evidence_cards", 0)
        return (
            "%d confirmed decisions, %d evidence cards. "
            "Counts only -- no scores, no deadlines.\n" % (decisions, cards)
        )
    if state == "proposed":
        if proposal is None:
            raise ValueError("proposed state requires an explicit proposal dict -- never invented")
        ok, why = validate_proposal(proposal)
        if not ok:
            raise ValueError("invalid proposal for proposed state: %s" % (why,))
        action = proposal["suggested_next_actions"][0]
        refs = ", ".join(proposal["evidence_refs"])
        return (
            "%s\nSuggested next action: %s\nEvidence refs: %s\n"
            % (PROPOSAL_STAMP, action, refs)
        )
    if state == "approved":
        if version is None:
            raise ValueError("approved state requires an explicit version+refs dict -- never invented")
        if not isinstance(version, dict) or not version.get("overlay_version"):
            raise ValueError("approved state requires an explicit overlay_version -- never invented")
        if not _valid_record_ids(version.get("evidence_refs")):
            raise ValueError("approved state requires explicit evidence refs -- never invented")
        if not _valid_record_ids(version.get("benchmark_refs")):
            raise ValueError("approved state requires explicit benchmark report refs -- "
                             "AD03 requires benchmark refs filed before approval -- never invented")
        refs = ", ".join(version["evidence_refs"])
        brefs = ", ".join(version["benchmark_refs"])
        return (
            "overlay %s active (new work only; old work unchanged). Evidence refs: %s. "
            "Benchmark refs: %s.\n"
            % (version["overlay_version"], refs, brefs)
        )
    # state == "deprecated"
    if version is None or not isinstance(version, dict) or not version.get("overlay_version"):
        raise ValueError("deprecated state requires an explicit prior version label -- never invented")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("deprecated state requires an explicit reason -- never invented")
    return (
        "overlay rolled back: %s restored for new work. Reason: %s.\n"
        % (version["overlay_version"], reason.strip())
    )
