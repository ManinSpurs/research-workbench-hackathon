"""Secretary coaching S1 (pure logic): recommendation contract, coaching templates, radar rule.

Implements the 56 cases SC11-SC66 of `current/SECRETARY_COACHING_S1_TESTSPEC_EN.md` (slice S1 of
`current/SECRETARY_COACHING_PROJECT_RADAR_EN.md`). Conventions follow the style reference
`tests/test_secretary_guidance.py`: module docstring, ROOT + sys.path, hand-built dict rows only —
no DB, no TestClient, no subprocess model server, no harness import, no credits consumed.

[IMPLEMENTED 2026-10-27] `app/secretary_coach.py` now exists in the tree and this file runs green
against it; the per-case `[NOT_IMPLEMENTED]` markers in the case docstrings are the historical
state at authoring time, kept for provenance. What is verified here is pure logic with hand-built
dicts only: no DB, no TestClient, no subprocess model server, no harness import, no credits.
Live/model behaviour of the radar is still NOT_RUN and this file proves nothing about it.

Two declared differences from the guidance test, both from the spec (§1):
  * no `fake`/`client` fixtures and no `tmp_path` DB — input is literal dicts only;
  * namespaced function prefix. The spec's `test_s1_<a..z>_<slug>` scheme has 26 letters for 56
    cases, so the SC ID occupies the scheme's letter slot: `test_s1_sc<ID>_<slug>`. The namespace
    prefix and the readable collect order the scheme exists for are preserved; the case ID is also
    repeated in every docstring.

Deviations resolved before writing (see spec §5):
  * M10 — the no-import rule wins: nothing here imports `harness._short`; SC54 asserts the observable
    <=80-char/ellipsis behaviour through the flag text instead (spec: "expect a local <=80-char
    helper behaviour").
  * M7 — the max-3 prominence cap is computed in S1 (spec's S1 assignment), so SC53 asserts it here.
  * Named-but-undetermined S1 symbols (M4) and the D1 record field (M9) are documented per case as
    provisional; the tests assert behaviour, never copy text (M3).

No wall clock is consulted: every age is measured from the literal `VERIFIED` instant below.
"""
import builtins
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import secretary_coach  # noqa: E402  [NOT_IMPLEMENTED] absent at HEAD -> collection error

VERIFIED = "2026-03-01T00:00:00+00:00"

# The seven DESIGN §1.1 recommendation fields, literal here on purpose: the test owns the contract.
FIELDS = ("proposal_wording", "basis", "uncertainty_limits", "alternatives",
          "suggested_next_action", "evidence_record_refs", "decision_status")
MANDATORY_TEXT_FIELDS = ("proposal_wording", "basis", "uncertainty_limits",
                         "suggested_next_action", "decision_status")
DECISION_STATUS_LITERAL = "proposal — not decided"
# DESIGN §1.1 names exactly these researcher-action families for `suggested_next_action`.
ACTION_FAMILIES = ("Review the drafted narrow scope and adopt, defer, or reject it.",
                   "Answer the recorded clarification gap about the construction start date.",
                   "Adopt the narrow scope, or defer or reject it, as recorded in ev-1.",
                   "Mark 'Prepare this for me' for the narrow scope draft.")

# Declared radar surface (DESIGN §4.5, declared 2026-10-27 as the schema authority for SC55;
# spec §4.2 inputs + S1 outputs); SC55 asserts nothing else appears. Input keys stay pinned to
# spec §4.2; the declared output sets below pin the allowlist to DESIGN §4.5, not to this comment.
# (RADAR_ROW_KEYS includes the conditional `flag` key; non-flagged rows carry the rest.)
RADAR_ROW_KEYS = {"project_id", "project", "has_waiting", "waiting_count", "waiting_items",
                  "has_decision", "decision_count", "input_changed", "last_activity",
                  "stale", "stale_prominent", "flag", "basis"}
RADAR_PAYLOAD_KEYS = {"rows", "project_count", "ordering_rule", "model_calls", "verified_at",
                      "limitation", "level", "basis"}

# Closed forbidden vocabularies (spec M1/M2: pinned here because DESIGN names categories only).
_DEADLINE_WORDS = ("deadline", "overdue", "no later than", "due date", "due by", "past due")
_DEADLINE_RES = (
    re.compile(r"\bdue\b(?!\s+to)", re.I),
    re.compile(r"\bby\s+(\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
               r"mon|tue|wed|thu|fri|sat|sun|the end of|end of)", re.I),
)
_SCORE_WORDS = ("score", "health", "productivity", "rank", "ranking", "grade", "kpi")
_SCORE_RES = (re.compile(r"\d+(?:\.\d+)?\s*%"), re.compile(r"\bper\s?cent\b", re.I))
_MORAL_WORDS = ("ashamed", "guilt", "guilty", "lazy", "disappoint", "shame", "punish",
                "neglect", "you must", "you should have")
_PRAISE_WORDS = ("well done", "great job", "good job", "congrat", "proud", "amazing",
                 "keep it up", "you've made progress", "you have made progress", "nice work")


# --------------------------------------------------------------------------- shared S1 helpers

def _rec(**overrides):
    """One complete, valid recommendation dict (all seven §1.1 fields), valid by construction."""
    rec = {
        "proposal_wording": "Proposed: adopt the narrow scope for the filed-to-construction question.",
        "basis": ("Recorded in ev-1: the narrow scope was drafted but not adopted. "
                  "No alternative found in the records."),
        "uncertainty_limits": ("ev-1 is a draft; whether 'construction start' means filing or "
                               "permit issuance is not yet verified."),
        "alternatives": [],
        "suggested_next_action": ACTION_FAMILIES[0],
        "evidence_record_refs": ["ev-1"],
        "decision_status": DECISION_STATUS_LITERAL,
    }
    rec.update(overrides)
    return rec


def _wi(text, saved_at):
    """One waiting item dict (`text`/`saved_at` only, per §4.2 inputs)."""
    return {"text": text, "saved_at": saved_at}


def _row(pid, **overrides):
    """One plain-dict briefing row carrying only the §4.2 inputs."""
    row = {"project_id": pid, "project": f"Project {pid}", "has_waiting": False,
           "waiting_count": 0, "waiting_items": [], "has_decision": False,
           "decision_count": 0, "input_changed": False, "last_activity": None}
    row.update(overrides)
    return row


def _days_ago(days):
    """ISO timestamp `days` before the literal VERIFIED instant."""
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return (base - timedelta(days=days)).isoformat(timespec="seconds")


def _waiting_row(pid, ages, texts=None, **overrides):
    """A waiting row whose items are `ages` days old (None marks an unparseable timestamp)."""
    items = []
    for i, age in enumerate(ages):
        text = (texts or [])[i] if texts and i < len(texts) else f"Waiting item {i + 1} for {pid}"
        stamp = None if age is None else _days_ago(age)
        items.append(_wi(text, stamp))
    return _row(pid, has_waiting=True, waiting_count=len(items), waiting_items=items, **overrides)


def _invalid():
    """S1's rejection surface: a plain ValueError, optionally a module-specific subclass."""
    error = getattr(secretary_coach, "RecommendationError", None)
    return (ValueError,) if error is None else (ValueError, error)


def _validate(rec):
    """Acceptance helper: the S1 validator returns a truthy result and raises on rejection."""
    return secretary_coach.validate_recommendation(rec)


def _coach(level="calm", waiting_items=(), left_off=(), verified_at=VERIFIED, basis=None):
    """Drive the S1 coaching template with hand-built dict items only."""
    return secretary_coach.coach(level=level, waiting_items=list(waiting_items),
                                 left_off=list(left_off), verified_at=verified_at, basis=basis)


def _radar(rows, verified_at=VERIFIED):
    return secretary_coach.radar(list(rows), verified_at=verified_at)


def _radar_row(payload, pid):
    return next(r for r in payload["rows"] if r["project_id"] == pid)


def _flag(row):
    """The stale flag object a flagged row carries."""
    return row["flag"]


def _blob(*values):
    """Everything a rendered object shows, flattened for forbidden-token scans."""
    return json.dumps(values, ensure_ascii=False, default=str).lower()


def assert_no_deadline_wording(blob):
    """No deadline/due/overdue/`by <date>` token in any rendered string (DESIGN §3, §7 SC03)."""
    text = str(blob).lower()
    for word in _DEADLINE_WORDS:
        assert word not in text, f"deadline wording {word!r} in: {text}"
    for pattern in _DEADLINE_RES:
        match = pattern.search(str(blob))
        assert match is None, f"deadline wording {match.group(0)!r} in: {text}"


def assert_no_score_or_moralizing(blob):
    """No score/health/productivity/guilt token, no percent, no second-person moral judgment."""
    text = str(blob).lower()
    for word in _SCORE_WORDS:
        assert word not in text, f"score token {word!r} in: {text}"
    for word in _MORAL_WORDS:
        assert word not in text, f"moralizing token {word!r} in: {text}"
    for pattern in _SCORE_RES:
        match = pattern.search(str(blob))
        assert match is None, f"numeric score {match.group(0)!r} in: {text}"


def _assert_no_unearned_praise(blob):
    """SC32/SC33 extension of the spec's helpers: no encouragement citing unrecorded progress."""
    text = str(blob).lower()
    for word in _PRAISE_WORDS:
        assert word not in text, f"unearned praise {word!r} in: {text}"
    for claim in ("you finished", "you completed", "you have finished", "you've finished"):
        assert claim not in text, f"unrecorded accomplishment {claim!r} in: {text}"


def assert_rec_shape(rec):
    """Mirror of `assert_guidance_shape`: seven fields, mandatory ones non-blank, literal status."""
    assert isinstance(rec, dict)
    assert set(rec) >= set(FIELDS)
    for field in MANDATORY_TEXT_FIELDS:
        assert isinstance(rec[field], str) and rec[field].strip(), field
    assert rec["decision_status"] == DECISION_STATUS_LITERAL
    assert isinstance(rec["alternatives"], list) and len(rec["alternatives"]) <= 2
    assert isinstance(rec["evidence_record_refs"], list)
    assert rec.get("model_calls") in (None, 0)


def _assert_coach_shape(note):
    """Coach output: level/text/basis/model_calls only, zero model calls, no deadline wording."""
    assert set(note) >= {"level", "text", "basis", "model_calls"}
    assert note["level"] in ("calm", "firm", "pointed")
    assert isinstance(note["text"], str) and note["text"].strip()
    assert isinstance(note["basis"], str) and note["basis"].strip()
    assert note["model_calls"] == 0
    assert_no_deadline_wording(note["text"] + " " + note["basis"])


# --------------------------------------------------- 2.1 Recommendation object — contract (§1)

def test_s1_sc11_complete_recommendation_accepted():
    """SC11 [NOT_IMPLEMENTED]: a complete one-ref object is accepted; shape holds."""
    rec = _rec()
    assert _validate(rec)
    assert_rec_shape(rec)
    assert set(rec) >= set(FIELDS)


def test_s1_sc12_each_missing_field_rejected():
    """SC12 [NOT_IMPLEMENTED]: seven single-field-removal variants, seven rejections naming it."""
    rejections = 0
    for field in FIELDS:
        rec = _rec()
        rec.pop(field)
        with pytest.raises(_invalid()) as err:
            _validate(rec)
        assert field in str(err.value), f"error must name the missing field {field!r}"
        rejections += 1
    assert rejections == 7


def test_s1_sc13_blank_mandatory_field_rejected():
    """SC13 [NOT_IMPLEMENTED]: blank/whitespace is never 'not applicable'."""
    for field in MANDATORY_TEXT_FIELDS:
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(_invalid()):
                _validate(_rec(**{field: blank}))


def test_s1_sc14_imperative_mandatory_wording_rejected():
    """SC14 [NOT_IMPLEMENTED]: 'You must …' is not a proposal and is rejected."""
    for wording in ("You must adopt the narrow scope.",
                    "Adopt the narrow scope now — this is required.",
                    "The researcher has to adopt the narrow scope."):
        with pytest.raises(_invalid()) as err:
            _validate(_rec(proposal_wording=wording))
        assert "proposal" in str(err.value).lower() or "imperative" in str(err.value).lower()


def test_s1_sc15_certainty_language_rejected():
    """SC15 [NOT_IMPLEMENTED] [M1: closed token list pinned in this file]: no certainty claims."""
    # The three forms DESIGN §1.2 bullet 3 names explicitly, in `proposal_wording` and in `basis`.
    for value in ("Proposed: this is the right answer — adopt the narrow scope.",
                  "Proposed: adopt the narrow scope (95% certain).",
                  "Proposed: the adoption is confirmed — adopt the narrow scope."):
        with pytest.raises(_invalid()):
            _validate(_rec(proposal_wording=value))
    for value in ("Recorded in ev-1. This is the right answer.",
                  "Recorded in ev-1; certainty 100%.",
                  "Recorded in ev-1 and confirmed by the Secretary (100% correct)."):
        with pytest.raises(_invalid()):
            _validate(_rec(basis=value))


def test_s1_sc16_unframed_proposal_rejected():
    """SC16 [NOT_IMPLEMENTED]: `proposal_wording` must carry the proposal framing."""
    rec = _rec(proposal_wording="The narrow scope is the better route for the question.")
    with pytest.raises(_invalid()) as err:
        _validate(rec)
    assert "proposal" in str(err.value).lower()


def test_s1_sc17_zero_actions_rejected():
    """SC17 [NOT_IMPLEMENTED]: exactly one action — never zero, never a silent briefing fallback."""
    for empty in ("", None, []):
        with pytest.raises(_invalid()) as err:
            _validate(_rec(suggested_next_action=empty))
        assert "action" in str(err.value).lower()


def test_s1_sc18_two_actions_rejected():
    """SC18 [NOT_IMPLEMENTED]: never a list, never 'the first of a list'."""
    two = "Review the drafted narrow scope. Answer the recorded clarification gap."
    for value in (two, ["Review the drafted narrow scope.", "Answer the clarification gap."],
                  ["Review the drafted narrow scope.", "Answer the clarification gap.",
                   "Defer the adoption."]):
        with pytest.raises(_invalid()) as err:
            _validate(_rec(suggested_next_action=value))
        assert "action" in str(err.value).lower()


def test_s1_sc19_non_researcher_action_rejected():
    """SC19 [NOT_IMPLEMENTED]: only the four DESIGN-named researcher-action families pass."""
    for family in ACTION_FAMILIES:
        assert _validate(_rec(suggested_next_action=family)), family
    for value in ("I will send the query myself.",
                  "The system will resolve the gap automatically.",
                  "The gap may resolve itself over time."):
        with pytest.raises(_invalid()):
            _validate(_rec(suggested_next_action=value))


def test_s1_sc20_decision_status_literal_only():
    """SC20 [NOT_IMPLEMENTED] [M5: whitespace/em-dash normalisation NOT asserted — undetermined]."""
    assert _validate(_rec(decision_status=DECISION_STATUS_LITERAL))
    for value in ("decided", "adopted", "confirmed", "pending", ""):
        with pytest.raises(_invalid()):
            _validate(_rec(decision_status=value))


def test_s1_sc21_no_builder_path_yields_a_decision():
    """SC21 [NOT_IMPLEMENTED]: no event promotes a proposal; silence/timeout/display never decide."""
    for event in ("timeout", "silence", "display", "revisit_satisfied", "preparation_complete"):
        with pytest.raises(_invalid()):
            secretary_coach.build_recommendation(event=event,
                                                 **_rec(decision_status="adopted"))
    built = secretary_coach.build_recommendation(**_rec())
    assert built["decision_status"] == DECISION_STATUS_LITERAL


def test_s1_sc22_claims_map_to_cited_refs():
    """SC22 [NOT_IMPLEMENTED]: every record ID a claim names must be cited."""
    assert _validate(_rec())
    with pytest.raises(_invalid()):
        _validate(_rec(basis="Recorded in ev-9: the narrow scope was drafted but not adopted."))


def test_s1_sc23_no_record_proposal_requires_missing_record_and_supply_action():
    """SC23 [NOT_IMPLEMENTED]: empty refs are legal only as a labelled no-record proposal."""
    no_record = _rec(
        evidence_record_refs=[],
        basis=("No record of an adopted scope exists yet: the gap is the missing adoption record. "
               "No alternative found in the records."),
        suggested_next_action="Investigate whether an adoption record exists for the narrow scope.")
    assert _validate(no_record)
    assert "no record" in json.dumps(no_record, ensure_ascii=False).lower()
    # A substantive conclusion over empty refs is refused.
    with pytest.raises(_invalid()):
        _validate(_rec(evidence_record_refs=[],
                       basis=("The narrow scope is the recorded direction. "
                              "No alternative found in the records.")))
    # Emptiness without a supply/investigate action is refused.
    with pytest.raises(_invalid()):
        _validate(_rec(evidence_record_refs=[],
                       basis=("No record of an adopted scope exists yet. "
                              "No alternative found in the records."),
                       suggested_next_action=ACTION_FAMILIES[0]))


def test_s1_sc24_absent_or_non_list_refs_are_a_validation_error():
    """SC24 [NOT_IMPLEMENTED]: absent/None/non-list refs differ from the legitimate empty list."""
    absent = _rec()
    absent.pop("evidence_record_refs")
    for value in (absent, _rec(evidence_record_refs=None),
                  _rec(evidence_record_refs="ev-1"), _rec(evidence_record_refs={"ev-1": True})):
        with pytest.raises(_invalid()):
            _validate(value)
    # Distinct from SC23: the empty list itself is not the error.
    assert _validate(_rec(
        evidence_record_refs=[],
        basis=("No record of an adopted scope exists yet. No alternative found in the records."),
        suggested_next_action="Investigate whether an adoption record exists."))


def test_s1_sc25_empty_uncertainty_limits_rejected():
    """SC25 [NOT_IMPLEMENTED]: a recommendation is never rendered without a stated limit."""
    for blank in ("", "   ", "\n"):
        with pytest.raises(_invalid()):
            _validate(_rec(uncertainty_limits=blank))


def test_s1_sc26_uncertainty_limits_must_cover_applicable_conditions():
    """SC26 [NOT_IMPLEMENTED] [M6 PARTIAL]: only the condition the record itself declares is asserted."""
    conflict = "ev-4 and ev-5 give conflicting definitions of 'construction start'."
    with pytest.raises(_invalid()):
        _validate(_rec(basis=conflict + " No alternative found in the records.",
                       uncertainty_limits="ev-1 is a draft.",
                       evidence_record_refs=["ev-4", "ev-5"]))
    assert _validate(_rec(basis=conflict + " No alternative found in the records.",
                          uncertainty_limits="The conflict between ev-4 and ev-5 is unresolved.",
                          evidence_record_refs=["ev-4", "ev-5"]))
    # A release-2023-scoped statement may not travel outside that release.
    with pytest.raises(_invalid()):
        _validate(_rec(basis=("Recorded in ev-1 (release 2023): the narrow scope was adopted. "
                              "No alternative found in the records."),
                       uncertainty_limits="ev-1 is a draft."))
    # An unverified field may not be presented as an actual column.
    with pytest.raises(_invalid()):
        _validate(_rec(basis=("Recorded in ev-1: the cost column is populated. "
                              "No alternative found in the records."),
                       uncertainty_limits="ev-1 is a draft."))


def test_s1_sc27_more_than_two_alternatives_rejected():
    """SC27 [NOT_IMPLEMENTED]: at most two alternatives."""
    with pytest.raises(_invalid()):
        _validate(_rec(alternatives=["Narrow scope", "Broad scope", "Defer entirely"]))
    assert _validate(_rec(alternatives=["Narrow scope", "Broad scope"]))
    assert_rec_shape(_rec(alternatives=["Narrow scope", "Broad scope"]))


def test_s1_sc28_empty_alternatives_require_the_stated_form():
    """SC28 [NOT_IMPLEMENTED] [M8: fabricated-alternative detection is out of S1's reach]."""
    typed = _rec(alternatives=[], basis=("Recorded in ev-1: drafted, not adopted. "
                                         "No alternative found in the records."))
    assert _validate(typed)
    with pytest.raises(_invalid()):
        _validate(_rec(alternatives=[], basis="Recorded in ev-1: drafted, not adopted."))


def test_s1_sc29_recorded_reason_missing_form():
    """SC29 [NOT_IMPLEMENTED] [M8-adjacent PARTIAL]: the recorded-reason-missing form is accepted;
    the contradictory form (declares no reason, then supplies one) is rejected. Whether a *fabricated*
    reason can be detected in general needs a record view S1 does not have."""
    accepted = _rec(basis=("Recorded in dec-1: deferred. Reason not recorded. "
                           "No alternative found in the records."),
                    evidence_record_refs=["dec-1"],
                    proposal_wording=("Proposed: revisit dec-1 with a written reason before "
                                      "adopting the narrow scope."))
    assert _validate(accepted)
    with pytest.raises(_invalid()):
        _validate(_rec(basis=("Recorded in dec-1: deferred. Reason not recorded: funding unclear. "
                              "No alternative found in the records."),
                       evidence_record_refs=["dec-1"],
                       proposal_wording=("Proposed: revisit dec-1 with a written reason before "
                                         "adopting the narrow scope.")))


def test_s1_sc30_prepared_card_refs_not_citable():
    """SC30 [NOT_IMPLEMENTED]: no prepared cards exist at HEAD, so no ref may name one."""
    for name in ("prepared_cards", "preparation_jobs", "background_scopes"):
        assert not hasattr(secretary_coach, name), f"{name} belongs to a later slice"
    with pytest.raises(_invalid()):
        _validate(_rec(evidence_record_refs=["prepared_card:pc-1"],
                       basis=("Recorded in prepared_card:pc-1: the card is ready. "
                              "No alternative found in the records.")))


def test_s1_sc31_conflict_named_in_basis_requires_the_same_conflict_in_limits():
    """SC31 [NOT_IMPLEMENTED] [M6 PARTIAL]: a conflict is never reported as settled on one side."""
    basis = "ev-4 and ev-5 conflict on what 'construction start' means. No alternative found in the records."
    with pytest.raises(_invalid()):
        _validate(_rec(basis=basis, uncertainty_limits="ev-4 is the newer draft.",
                       evidence_record_refs=["ev-4", "ev-5"]))
    assert _validate(_rec(basis=basis,
                          uncertainty_limits="The ev-4/ev-5 conflict on 'construction start' is open.",
                          evidence_record_refs=["ev-4", "ev-5"]))


# ----------------------------------------------------------------- 2.2 Coaching templates (§2)

def test_s1_sc32_calm_with_no_records_claims_nothing():
    """SC32 [NOT_IMPLEMENTED] [M3: property asserted, not a fixed sentence]."""
    note = _coach("calm", waiting_items=(), left_off=())
    _assert_coach_shape(note)
    assert note["level"] == "calm"
    assert "record" in (note["text"] + " " + note["basis"]).lower()
    assert_no_score_or_moralizing(note["text"] + " " + note["basis"])
    _assert_no_unearned_praise(note["text"] + " " + note["basis"])


def test_s1_sc33_calm_with_one_recorded_decision_cites_only_that():
    """SC33 [NOT_IMPLEMENTED] [M3]: the calm note references the recorded decision only."""
    note = _coach("calm", waiting_items=(),
                  left_off=[{"text": "Deferred the narrow scope.", "reason": "Funding unclear."}])
    _assert_coach_shape(note)
    assert note["level"] == "calm"
    assert "decision" in (note["text"] + " " + note["basis"]).lower()
    _assert_no_unearned_praise(note["text"] + " " + note["basis"])
    assert_no_score_or_moralizing(note["text"] + " " + note["basis"])
    assert set(re.findall(r"\d+", note["text"] + " " + note["basis"])) <= {"1"}


def test_s1_sc34_firm_reuses_supplied_basis_verbatim():
    """SC34 [NOT_IMPLEMENTED]: level never diverges from the displayed basis, no invented urgency."""
    basis = "Oldest waiting item is 9 days old; 2 items waiting."
    note = _coach("firm", waiting_items=(_wi("Scope question", _days_ago(9)),
                                         _wi("Gap question", _days_ago(4))), basis=basis)
    _assert_coach_shape(note)
    assert note["level"] == "firm"
    assert note["basis"] == basis
    supplied_digits = set(re.findall(r"\d+", basis))
    assert set(re.findall(r"\d+", note["text"])) <= supplied_digits


def test_s1_sc35_pointed_asks_for_a_spoken_next_step_or_reasoned_deferral():
    """SC35 [NOT_IMPLEMENTED] [M2: forbidden vocabulary pinned in this file]."""
    note = _coach("pointed", waiting_items=(_wi("Scope question", _days_ago(20)),
                                            _wi("Gap question", _days_ago(15))))
    _assert_coach_shape(note)
    assert note["level"] == "pointed"
    text = note["text"].lower()
    assert "next step" in text
    assert "defer" in text and "reason" in text
    assert_no_score_or_moralizing(note["text"] + " " + note["basis"])
    for punishing in ("punish", "because of you", "until you learn", "consequence"):
        assert punishing not in text


def test_s1_sc36_prepared_card_citation_never_raises_the_level():
    """SC36 [NOT_IMPLEMENTED]: prepared material never raises calm -> firm -> pointed."""
    basis = "No items are waiting; 1 recorded decision(s) on file."
    note = _coach("calm", waiting_items=(), left_off=[{"text": "Deferred the narrow scope."}],
                  basis=basis)
    _assert_coach_shape(note)
    assert note["level"] == "calm"
    assert note["basis"] == basis


def test_s1_sc37_all_levels_carry_no_deadline_score_or_moralizing_tokens():
    """SC37 [NOT_IMPLEMENTED] [M2]: every template's text and basis pass both forbidden scans."""
    cases = (("calm", (), ()),
             ("firm", (_wi("Scope question", _days_ago(9)),), ()),
             ("pointed", (_wi("Scope question", _days_ago(20)), _wi("Gap question", _days_ago(15))), ()))
    for level, items, left in cases:
        note = _coach(level, waiting_items=items, left_off=left)
        _assert_coach_shape(note)
        assert note["level"] == level
        for blob in (note["text"], note["basis"]):
            assert_no_deadline_wording(blob)
            assert_no_score_or_moralizing(blob)
            for word in ("insult", "idiot", "useless"):
                assert word not in blob.lower()
        assert note["model_calls"] == 0


def test_s1_sc38_all_unparseable_timestamps_state_unknown_age_and_count():
    """SC38 [NOT_IMPLEMENTED]: no interpolated, estimated or rounded age anywhere."""
    note = _coach("calm", waiting_items=(_wi("Scope question", None),
                                         _wi("Gap question", "not-a-timestamp !!!")))
    _assert_coach_shape(note)
    blob = (note["text"] + " " + note["basis"]).lower()
    assert "oldest waiting age is unknown" in blob
    assert "2 items waiting" in blob
    assert not re.search(r"\b\d+\s+days?\b", blob), blob


def test_s1_sc39_missing_level_is_an_error():
    """SC39 [NOT_IMPLEMENTED]: S1 never computes the level, and never imports harness thresholds."""
    with pytest.raises((TypeError, ValueError, KeyError)):
        secretary_coach.coach(waiting_items=[], left_off=[], verified_at=VERIFIED)
    source = Path(secretary_coach.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("import harness", "from app.harness", "from .harness", "app import harness",
                      "firm_min_age_days", "pointed_min_age_days"):
        assert forbidden not in source, forbidden


def test_s1_sc40_output_level_equals_input_level_with_zero_model_calls():
    """SC40 [NOT_IMPLEMENTED]: the tone is passed through; nothing calls a model."""
    for level in ("calm", "firm", "pointed"):
        items = () if level == "calm" else (_wi("Scope question", _days_ago(20)),)
        note = _coach(level, waiting_items=items)
        _assert_coach_shape(note)
        assert note["level"] == level
        assert note["model_calls"] == 0


def test_s1_sc41_text_is_one_string_and_basis_is_one_string():
    """SC41 [NOT_IMPLEMENTED]: an action list is the recommendation object's job, not the text's."""
    for level in ("calm", "firm", "pointed"):
        note = _coach(level, waiting_items=(_wi("Scope question", _days_ago(20)),
                                            _wi("Gap question", _days_ago(2))))
        _assert_coach_shape(note)
        text = note["text"]
        assert isinstance(text, str) and text.strip()
        assert "\n" not in text
        for marker in ("•", "- ", "* ", "1.", "2."):
            assert marker not in text


# ------------------------------------------------------- 2.3 Radar stale rule/ordering (§4)

def test_s1_effective_has_waiting_emitted_when_items_present_but_flag_false():
    """DESIGN §4.5 Option A (2026-10-27): emitted row carries the effective staleness inputs —
    an item-list row flagged stale emits has_waiting True even when the input flag is False."""
    row = _row("p1", has_waiting=False, waiting_count=0,
               waiting_items=[_wi("Old waiting item", _days_ago(40))])
    payload = _radar([row])
    out = _radar_row(payload, "p1")
    assert out["stale"] is True
    assert out["has_waiting"] is True
    assert out["waiting_count"] >= len(out["waiting_items"]) == 1


def test_s1_effective_waiting_count_never_below_item_list():
    """DESIGN §4.5 Option A: max(input count, item-list length) — display never undercounts."""
    row = _row("p1", has_waiting=True, waiting_count=1,
               waiting_items=[_wi("One", _days_ago(2)), _wi("Two", _days_ago(3)),
                              _wi("Three", _days_ago(4))])
    payload = _radar([row])
    out = _radar_row(payload, "p1")
    assert out["waiting_count"] == 3
    assert out["waiting_count"] >= len(out["waiting_items"])
    assert out["has_waiting"] is True


def test_s1_effective_values_deterministic_across_repeat_calls():
    """Repeat calls over contradictory inputs produce identical effective rows and order."""
    rows = [_row("p1", has_waiting=False, waiting_count=0,
                 waiting_items=[_wi("Old item", _days_ago(40))]),
            _row("p2", has_waiting=False, waiting_count=1,
                 waiting_items=[_wi("A", _days_ago(2)), _wi("B", _days_ago(3))]),
            _row("p3", last_activity=_days_ago(400))]
    first = _radar(rows)
    second = _radar(list(rows))
    assert first == second
    assert [r["project_id"] for r in first["rows"]] == ["p1", "p2", "p3"]
    for r in first["rows"]:
        assert r["waiting_count"] >= len(r["waiting_items"])
        if r["stale"]:
            assert r["has_waiting"] is True

def test_s1_sc42_boundary_14_days_and_30_days_are_stale():
    """SC42 [NOT_IMPLEMENTED]: rule (a) flags at the exact 14-day boundary."""
    for age in (14, 30):
        payload = _radar([_waiting_row("p1", [age])])
        row = _radar_row(payload, "p1")
        assert row["stale"] is True, age
        flag = _flag(row)
        assert flag["project"] == "Project p1"
        assert flag["age_days"] == age
        assert flag["waiting_count"] == 1


def test_s1_sc43_thirteen_days_with_recent_activity_is_not_stale():
    """SC43 [NOT_IMPLEMENTED]: no rounding, no 'nearly stale'."""
    payload = _radar([_waiting_row("p1", [13], last_activity=_days_ago(0))])
    row = _radar_row(payload, "p1")
    assert row["stale"] is False
    assert not row.get("flag")
    assert row["waiting_count"] == 1


def test_s1_sc44_inactivity_with_waiting_items_is_stale():
    """SC44 [NOT_IMPLEMENTED]: rule (b) flags 14+ days of inactivity when items are waiting."""
    for age in (14, 21):
        payload = _radar([_waiting_row("p1", [1], last_activity=_days_ago(age))])
        assert _radar_row(payload, "p1")["stale"] is True, age


def test_s1_sc45_no_waiting_project_is_never_stale():
    """SC45 [NOT_IMPLEMENTED]: dormant != neglected; 400 days of silence is not a flag."""
    rows = [_row("p1", last_activity=_days_ago(400)), _waiting_row("p2", [2])]
    payload = _radar(rows)
    assert _radar_row(payload, "p1")["stale"] is False
    assert not _radar_row(payload, "p1").get("flag")
    blob = _blob(_radar_row(payload, "p1"))
    assert_no_score_or_moralizing(blob)
    assert "neglected" not in blob
    # Ordered among the non-waiting rows only: the waiting project comes first.
    assert [r["project_id"] for r in payload["rows"]] == ["p2", "p1"]


def test_s1_sc46_zero_projects_is_an_empty_row_set():
    """SC46 [NOT_IMPLEMENTED]: no error, no placeholder project, no fabricated flag."""
    payload = _radar([])
    assert payload["rows"] == []
    assert payload["project_count"] == 0
    assert payload["model_calls"] == 0
    assert_no_deadline_wording(_blob(payload))


def test_s1_sc47_all_waiting_timestamps_unparseable_is_count_only():
    """SC47 [NOT_IMPLEMENTED]: unparseable ages never escalate; the row is kept and counted."""
    rows = [_waiting_row("p1", [None, None]),
            _waiting_row("p2", [1]),
            _row("p3", last_activity=_days_ago(400))]
    payload = _radar(rows)
    row = _radar_row(payload, "p1")
    assert row["stale"] is False
    assert row["waiting_count"] == 2
    assert "unknown" in _blob(row)
    order = [r["project_id"] for r in payload["rows"]]
    # Waiting projects precede the non-waiting one; LAUNCH_ORDERING_RULE keys do not include counts.
    assert order[:2] == ["p1", "p2"] and order[2] == "p3"
    assert payload["ordering_rule"] is not None


def test_s1_sc48_age_comes_from_oldest_parseable_item_but_all_items_count():
    """SC48 [NOT_IMPLEMENTED]: never the newest item, never a dropped unparseable item."""
    payload = _radar([_waiting_row("p1", [20, None], texts=["Aged item", "Broken stamp item"])])
    row = _radar_row(payload, "p1")
    assert row["waiting_count"] == 2
    assert row["stale"] is True
    assert _flag(row)["age_days"] == 20
    assert row["stale_prominent"] in (True, False)


def test_s1_sc49_unparseable_verified_at_renders_counts_and_a_limitation_only():
    """SC49 [NOT_IMPLEMENTED]: no age flag and no level without a parseable `verified_at`."""
    rows = [_waiting_row("p1", [40])]
    for verified_at in ("not-a-timestamp !!!", ""):
        payload = _radar(rows, verified_at=verified_at)
        assert payload["rows"], "rows must still be present"
        assert payload["limitation"], "a limitation line is required"
        assert all(r["stale"] is False for r in payload["rows"])
        assert payload.get("level") in (None, "")
        assert_no_deadline_wording(_blob(payload))
    payload = _radar(rows, verified_at=None)
    assert payload["limitation"] and all(r["stale"] is False for r in payload["rows"])


def test_s1_sc50_stale_projects_order_by_oldest_waiting_age_descending():
    """SC50 [NOT_IMPLEMENTED]: 40 d before 20 d, then the waiting-only project."""
    rows = [_waiting_row("p20", [20]), _waiting_row("p40", [40]), _waiting_row("p2", [2])]
    payload = _radar(rows)
    order = [r["project_id"] for r in payload["rows"]]
    assert order == ["p40", "p20", "p2"]
    assert [r["stale"] for r in payload["rows"]] == [True, True, False]


def test_s1_sc51_remainder_keeps_the_declared_ordering_rule():
    """SC51 [NOT_IMPLEMENTED]: waiting, then latest decision, then name (ci), then project id."""
    rows = [_row("plain-b", project="Same"), _row("plain-a", project="Same"),
            _row("decision-only", has_decision=True, decision_count=1),
            _row("waiting-decision", has_waiting=True, waiting_count=1,
                 waiting_items=[_wi("Scope question", _days_ago(1))], has_decision=True,
                 decision_count=1),
            _waiting_row("stale", [40])]
    payload = _radar(rows)
    assert [r["project_id"] for r in payload["rows"]] == [
        "stale", "waiting-decision", "decision-only", "plain-a", "plain-b"]
    for key in ("importance", "weight", "model_importance"):
        assert key not in _blob([set(r) for r in payload["rows"]])


def test_s1_sc52_case_insensitive_name_then_project_id_tiebreak():
    """SC52 [NOT_IMPLEMENTED]: identical names differing by case tie-break on project id."""
    rows = [_row("pid-z", project="alpha"), _row("pid-a", project="Alpha")]
    payload = _radar(rows)
    assert [r["project_id"] for r in payload["rows"]] == ["pid-a", "pid-z"]
    assert [r["project_id"] for r in _radar(list(reversed(rows)))["rows"]] == ["pid-a", "pid-z"]


def test_s1_sc53_prominence_cap_is_three_while_all_stale_rows_survive():
    """SC53 [NOT_IMPLEMENTED] [M7: cap computed in S1 per the spec's assignment]."""
    rows = [_waiting_row(f"p{i}", [15 + i]) for i in range(1, 6)]
    payload = _radar(rows)
    assert len(payload["rows"]) == 5
    prominent = [r for r in payload["rows"] if r["stale_prominent"]]
    assert len(prominent) == 3
    assert all(r["stale"] is True for r in payload["rows"])
    assert [r["project_id"] for r in prominent] == ["p5", "p4", "p3"]


def test_s1_sc54_flag_names_project_short_text_age_and_count():
    """SC54 [NOT_IMPLEMENTED] [M10: local <=80-char helper behaviour, no harness `_short` import]."""
    long_text = ("  Scope\tquestion about the filed-to-construction definition that runs far past "
                 "eighty characters so it has to be cut with an ellipsis\n  ")
    payload = _radar([_waiting_row("p1", [21, 3], texts=[long_text, "Second waiting item"])])
    row = _radar_row(payload, "p1")
    flag = _flag(row)
    assert flag["project"] == "Project p1"
    assert flag["age_days"] == 21
    assert flag["waiting_count"] == 2
    short = flag["oldest_text"]
    assert isinstance(short, str) and len(short) <= 80
    assert short.endswith("…")
    assert "\n" not in short and "\t" not in short and "  " not in short
    assert short.startswith("Scope question")
    # The short text is derived from this project's own oldest item only.
    assert "Second waiting item" not in short
    # M10 decision: the <=80-char/ellipsis behaviour is local, never `harness._short` imported.
    assert "_short" not in vars(secretary_coach)


def test_s1_sc55_only_declared_fields_and_no_scores():
    """SC55 [IMPLEMENTED 2026-10-27]: exactly the DESIGN §4.5-declared fields, no fewer and
    no more; the raw basis travels with the level. `flag` is conditional in both directions
    (present exactly for stale rows with a parseable oldest item); no score/health fields."""
    # Flagged case: 20 idle days with a parseable oldest item -> stale, flag present.
    payload = _radar([_waiting_row("p1", [20]), _waiting_row("p2", [1])])
    assert set(payload) == RADAR_PAYLOAD_KEYS
    flagged, plain = _radar_row(payload, "p1"), _radar_row(payload, "p2")
    assert set(flagged) == RADAR_ROW_KEYS
    assert set(plain) == RADAR_ROW_KEYS - {"flag"}
    assert flagged["stale"] is True and "flag" in flagged
    assert plain["stale"] is False and "flag" not in plain
    for row in payload["rows"]:
        assert set(row) >= RADAR_ROW_KEYS - {"flag"}
    blob = _blob(payload)
    assert_no_score_or_moralizing(blob)
    assert_no_deadline_wording(blob)
    for token in ("productivity", "health", "importance", "ranking", "percent"):
        assert token not in blob


def test_s1_sc56_no_cross_project_leakage():
    """SC56 [NOT_IMPLEMENTED]: one project's text/reason/excerpt never reaches another's row."""
    sentinel = "SENTINEL-P-9931"
    p_row = _waiting_row("p", [40], texts=[f"{sentinel} waiting text"],
                         decision_reason=sentinel, excerpt=sentinel)
    q_row = _waiting_row("q", [2], texts=["Q own waiting text"])
    payload = _radar([p_row, q_row])
    q_blob = _blob(_radar_row(payload, "q"))
    assert sentinel not in q_blob
    p_flag = _flag(_radar_row(payload, "p"))
    assert sentinel in p_flag["oldest_text"]
    assert "Q own waiting text" not in _blob(_radar_row(payload, "p"))
    # Q's own flag, if any, names only Q.
    q_row_out = _radar_row(payload, "q")
    if q_row_out.get("flag"):
        assert q_row_out["flag"]["project"] == "Project q"


# ------------------------------------------------------- 2.4 Deadline-wording gate (§3)

def test_s1_sc57_gate_defaults_to_false():
    """SC57 [NOT_IMPLEMENTED]: no date fields -> False; unrelated truthiness never turns it on."""
    for record in ({}, {"text": "waiting 9 days"}, {"waiting_count": 3},
                   {"waiting_days": 9, "last_activity_days": 5, "waiting_count": 3},
                   {"input_changed": True, "has_decision": True, "decision_count": 2},
                   None):
        assert secretary_coach.deadline_wording_allowed(record) is False, record


def test_s1_sc58_gate_true_only_for_a_recorded_explicit_date():
    """SC58 [NOT_IMPLEMENTED] [M9: D1 field name provisional — no D1 store at HEAD]."""
    record = {"recorded_date": "2026-03-14", "recording_researcher": "researcher"}
    assert secretary_coach.deadline_wording_allowed(record) is True
    # No rendering path quotes a computed or inferred date at HEAD (see SC62).
    note = _coach("calm", waiting_items=())
    assert "2026-03-14" not in _blob(note)


def test_s1_sc59_d2_age_alone_is_not_deadline_wording():
    """SC59 [NOT_IMPLEMENTED]: 'waiting N days' renders; the gate stays False."""
    assert secretary_coach.deadline_wording_allowed({"waiting_days": 9}) is False
    note = _coach("firm", waiting_items=(_wi("Scope question", _days_ago(9)),))
    _assert_coach_shape(note)
    assert_no_deadline_wording(note["text"] + " " + note["basis"])
    assert "waiting" in (note["text"] + " " + note["basis"]).lower()


def test_s1_sc60_d3_inactivity_alone_is_not_deadline_wording():
    """SC60 [NOT_IMPLEMENTED]: inactivity renders as 'last activity N days ago' / 'untouched'."""
    assert secretary_coach.deadline_wording_allowed({"last_activity_days": 5}) is False
    note = _coach("firm", waiting_items=(_wi("Scope question", _days_ago(9)),),
                  basis="Untouched for 5 days; 1 item waiting.")
    _assert_coach_shape(note)
    assert "untouched for 5 days" in note["basis"].lower()


def test_s1_sc61_d4_missing_or_unparseable_date_is_false():
    """SC61 [NOT_IMPLEMENTED]: nothing guesses, interpolates, or silently omits a value."""
    for record in ({"recorded_date": None}, {"recorded_date": ""},
                   {"recorded_date": "not-a-date !!!"}, {"recorded_date": "2026-13-45"},
                   {"recorded_date": 20260314}):
        assert secretary_coach.deadline_wording_allowed(record) is False, record
    note = _coach("calm", waiting_items=(_wi("Scope question", "not-a-date !!!"),))
    assert "unknown" in (note["text"] + " " + note["basis"]).lower()


def test_s1_sc62_no_deadline_wording_reachable_at_head():
    """SC62 (caveat: samples records without the M9 D1 carrier fields only; not universal over a
    future D1 schema — see testspec §5A(b)): with no D1 store, the sampled gate results are False
    and no line may say it."""
    for record in ({}, {"recorded_date": None}, {"waiting_days": 9},
                   {"last_activity_days": 30}, {"waiting_count": 4, "has_waiting": True}):
        assert secretary_coach.deadline_wording_allowed(record) is False
    for level in ("calm", "firm", "pointed"):
        note = _coach(level, waiting_items=(_wi("Scope question", _days_ago(40)),
                                            _wi("Gap question", _days_ago(20))))
        assert_no_deadline_wording(note["text"] + " " + note["basis"])
    payload = _radar([_waiting_row("p1", [40]), _row("p2", last_activity=_days_ago(400))])
    assert_no_deadline_wording(_blob(payload))


def test_s1_sc63_date_satisfaction_is_conceptual_only_at_head():
    """SC63 [NOT_IMPLEMENTED] [CONCEPTUAL-ONLY]: no revisit machinery, so nothing auto-adopts."""
    assert not hasattr(secretary_coach, "revisit_conditions")
    source = Path(secretary_coach.__file__).read_text(encoding="utf-8").lower()
    assert "ready to revisit" not in source
    rec = _rec()
    assert rec["decision_status"] == DECISION_STATUS_LITERAL
    assert secretary_coach.deadline_wording_allowed({"recorded_date": None}) is False


# --------------------------------------------------- 2.5 S1 purity and boundary (§6)

_IMPORT_PROBE = r'''
import sys

FORBIDDEN = {"harness", "api", "store", "sqlite3", "fastapi"}


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in FORBIDDEN:
            raise ImportError("blocked in S1: " + name)
        return None


sys.meta_path.insert(0, Blocker())
from app import secretary_coach as m

bad = sorted(n for n in dir(m)
             if any(k in n.lower() for k in ("harness", "api", "store", "sqlite", "fastapi")))
print(bad)
'''


def test_s1_sc64_imports_with_harness_api_store_and_sqlite3_poisoned():
    """SC64 [NOT_IMPLEMENTED]: import and public calls survive poisoned imports."""
    proc = subprocess.run([sys.executable, "-c", _IMPORT_PROBE], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", proc.stdout


def test_s1_sc65_no_writes_sockets_or_model_calls(monkeypatch):
    """SC65 [NOT_IMPLEMENTED]: filesystem-write and socket tripwires stay silent."""
    real_open = builtins.open

    def guard(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"filesystem write attempted: {file} mode={mode}")
        return real_open(file, mode, *args, **kwargs)

    def no_socket(*args, **kwargs):
        raise AssertionError("socket construction attempted")

    monkeypatch.setattr(builtins, "open", guard)
    monkeypatch.setattr(socket, "socket", no_socket)

    rec = _rec()
    assert _validate(rec)
    built = secretary_coach.build_recommendation(**_rec())
    assert built.get("model_calls") in (None, 0)
    for level in ("calm", "firm", "pointed"):
        assert _coach(level, waiting_items=(_wi("Scope question", _days_ago(20)),))["model_calls"] == 0
    payload = _radar([_waiting_row("p1", [40]), _row("p2", last_activity=_days_ago(400))])
    assert payload["model_calls"] == 0
    assert secretary_coach.deadline_wording_allowed({}) is False


def test_s1_sc66_only_the_two_owned_files_are_touched():
    """SC66 [NOT_IMPLEMENTED]: harness.py/api.py/frontend/app.js/store.py stay untouched.

    A working-tree check needs a repository; `current/rehearsal_build` is not inside one at HEAD,
    so this case reports [NOT_RUN] as a skip rather than passing vacuously.
    """
    probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=60)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("SC66 [NOT_RUN]: no git repository at %s, working-tree check unavailable" % ROOT)
    status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=60)
    assert status.returncode == 0, status.stderr
    shared = ("app/harness.py", "app/api.py", "frontend/app.js", "app/store.py")
    touched = [line for line in status.stdout.splitlines()
               if any(line.endswith(f" {path}") for path in shared)]
    assert touched == [], touched
