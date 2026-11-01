"""Q3b: harness clarification branch for private-context gaps (audit gap #6).

Drives ``Harness`` with a scripted in-process stub adapter (label "mock"):
no API, no UI, no network, no model calls, no credits.

Covers the respond/clarification path only:
  * a private_context clarification surfaces branchless with the
    supply-your-own note (run return and pending/resume surface);
  * the researcher's supplied answer records through the existing
    clarification-answer path (answers + clarification_responses, no new
    storage);
  * fact/preference clarifications keep current behaviour exactly
    (branches intact, no added copy);
  * a private_context clarification with branches forced past construction
    still cannot surface branches;
  * frame-pass gap_type -> clarification gap_kind mapping (no vocabulary
    renamed; methodological/none carry no clarifiable gap).

Identifier: `private_context` — the snake-case literal already enacted in
`Frame.gap_type` (`app/ai/service.py`) and `ClarificationNeeded.gap_kind`
(`app/results.py`), per spec §6 step 4 / §7c.
"""
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import store
from app.adapters import Usage
from app.harness import Harness
from app.results import (Branch, ClarificationNeeded, DesignProposal,
                         EvidenceAssessment, FirstStep, InvestigationPlan,
                         RefinedQuestion, VariableRef,
                         gap_kind_from_frame_gap_type, present_clarification,
                         PRIVATE_CONTEXT_NOTE)

QUESTION = "Q3B-UNDER-TEST: which undisclosed household detail determines the applicable definition?"
GAP = "Q3B-GAP-UNDER-TEST: undisclosed household detail needed for interpretation"
ANSWER = "Q3B-ANSWER-UNDER-TEST: researcher-supplied household context"
TARGET = "DD-2023"


class StubAdapter:
    """Scripted in-process adapter: label 'mock', no transport. Tunable gap."""

    label = "mock"

    def __init__(self, gap_kind=None, branches=()):
        self.calls = []
        self.gap_kind = gap_kind
        self.branches = list(branches)

    async def refine(self, question):
        self.calls.append("refine")
        return RefinedQuestion(refined_text=question, kept_constraints=[]), Usage(1, 1, 1)

    async def first_step(self, question, context):
        self.calls.append("first_step")
        return FirstStep(
            route="clarify",
            design=DesignProposal(question=question,
                variables=[VariableRef(name="approval_date", dataset_id="fx/permits_2023.csv",
                                       kind="unverified", role="end (meaning unverified)")],
                assumptions=["approval_date marks final approval (unverified)"],
                risks=["approval_date may mark plan-review completion instead"],
                verification_plan="Read DD-2023 and the registered official pages"),
            clarification=ClarificationNeeded(gap=GAP, affected="approval_date (processing-time design)",
                why_matters="If the household context applies, definition A holds; otherwise check status codes.",
                branches=list(self.branches), independent_work="status_code / issue_date check can continue",
                gap_kind=self.gap_kind),
            direct_answer=None, alternatives=[]), "HISTORY", Usage(1, 1, 1)

    async def plan(self, history, prompt):
        self.calls.append("plan")
        return InvestigationPlan(target_gap="Meaning of approval_date", sources=[TARGET, "OFF-FAQ-2023"],
                                 fields=["approval_date"], stop_condition="Stop after the listed sources"), "HISTORY", Usage(1, 1, 1)

    async def assess(self, history, observations, observed_ids):
        self.calls.append("assess")
        assert "DD-2023:approval_date" in observed_ids
        return EvidenceAssessment(claim="approval_date records plan-review completion",
            evidence_ids=["DD-2023:approval_date"], applicability="DD-2023 applies to this extract.",
            outcome="partial", confirmation_level="partial",
            unresolved=["Final approval is not established; check status_code."],
            precedent_label=None, precedent_source_ids=[],
            suggested_next_action="Check status codes before using approval_date as final approval."), "HISTORY", Usage(1, 1, 1)


def make_harness(tmp_path, **stub_kwargs):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    return Harness(str(tmp_path / "wb-q3b.db"), data, adapter=StubAdapter(**stub_kwargs))


@pytest.fixture
def private_harness(tmp_path):
    h = make_harness(tmp_path, gap_kind="private_context", branches=[])
    yield h
    h.db.close()


async def _ask(h, text=QUESTION):
    pid = h.register_project("fx")["project_id"]
    qid = (await h.create_question(pid, text))["question_id"]
    return pid, qid


def _tables(h):
    return sorted(r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def test_private_context_run_renders_branchless_with_supply_note(private_harness):
    h = private_harness
    pid, qid = asyncio.run(_ask(h))
    run = asyncio.run(h.run(qid))
    assert run["result_kind"] == "ClarificationNeeded", run
    card = run["clarification"]
    assert card["gap_kind"] == "private_context"
    assert card["branches"] == []
    assert card["researcher_note"] == PRIVATE_CONTEXT_NOTE
    assert "will not guess" in card["researcher_note"]
    # Canonical storage is unchanged: the note is presentation-only.
    stored = json.loads(h.db.execute(
        "SELECT body_json FROM clarifications WHERE id=?", (run["cq_id"],)).fetchone()[0])
    assert stored["gap_kind"] == "private_context"
    assert stored["branches"] == []
    assert "researcher_note" not in stored


def test_private_context_pending_surface_renders_branchless_with_supply_note(private_harness):
    h = private_harness
    pid, qid = asyncio.run(_ask(h))
    run = asyncio.run(h.run(qid))
    pending = h.pending_clarifications(pid)["clarifications"]
    assert len(pending) == 1
    card = pending[0]["clarification"]
    assert card["gap_kind"] == "private_context"
    assert card["branches"] == []
    assert card["researcher_note"] == PRIVATE_CONTEXT_NOTE


def test_private_context_answer_records_through_existing_path(private_harness):
    h = private_harness
    pid, qid = asyncio.run(_ask(h))
    run = asyncio.run(h.run(qid))
    cq_id = run["cq_id"]
    before = _tables(h)
    out = asyncio.run(h.respond(cq_id, "answer", ANSWER))
    # No new storage: the existing clarification-answer path carries it.
    assert _tables(h) == before
    ans = h.db.execute("SELECT kind, value FROM answers WHERE cq_id=?", (cq_id,)).fetchone()
    assert tuple(ans) == ("answer", ANSWER)
    resp = h.db.execute(
        "SELECT kind, value, status FROM clarification_responses WHERE cq_id=?", (cq_id,)).fetchone()
    assert tuple(resp) == ("answer", ANSWER, "completed")
    assert h.db.execute("SELECT status FROM clarifications WHERE id=?", (cq_id,)).fetchone()[0] == "answered"
    # The supplied statement is stored as the researcher's own words, not verified evidence.
    assert out["state"] == "decision_pending"
    assert "User statement stored" in out["note"]


@pytest.mark.parametrize("gap_kind", ["fact", "preference"])
def test_non_private_clarifications_keep_branches_and_no_added_copy(tmp_path, gap_kind):
    branches = [Branch(condition="If yes", outcome="Use design A"),
                Branch(condition="If no", outcome="Check status codes first")]
    h = make_harness(tmp_path, gap_kind=gap_kind, branches=branches)
    try:
        pid, qid = asyncio.run(_ask(h))
        run = asyncio.run(h.run(qid))
        card = run["clarification"]
        assert card["gap_kind"] == gap_kind
        assert card["branches"] == [{"condition": "If yes", "outcome": "Use design A"},
                                    {"condition": "If no", "outcome": "Check status codes first"}]
        assert "researcher_note" not in card
        pending = h.pending_clarifications(pid)["clarifications"][0]["clarification"]
        assert pending["branches"] == card["branches"]
        assert "researcher_note" not in pending
    finally:
        h.db.close()


def test_forced_branches_on_private_context_cannot_surface(private_harness):
    # A body forced past construction-time validation (legacy row, raw insert,
    # or model_construct) still renders branchless at every harness surface.
    h = private_harness
    pid, qid = asyncio.run(_ask(h))
    run = asyncio.run(h.run(qid))
    forced = ClarificationNeeded.model_construct(
        gap=GAP, affected="approval_date (processing-time design)",
        why_matters="Forced branches must never reach the researcher.",
        branches=[{"condition": "If the household is large", "outcome": "Assume definition B"}],
        independent_work="status_code check can continue",
        gap_kind="private_context")
    assert forced.branches != []
    assert present_clarification(dict(forced))["branches"] == []
    assert present_clarification(dict(forced))["researcher_note"] == PRIVATE_CONTEXT_NOTE
    # A raw stored body carrying branches + private_context (legacy row or
    # hand-written insert) renders branchless at every harness surface.
    forged_body = json.dumps({"gap": GAP, "affected": "approval_date (processing-time design)",
                              "why_matters": "Forced branches must never reach the researcher.",
                              "branches": [{"condition": "If the household is large",
                                            "outcome": "Assume definition B"}],
                              "independent_work": "status_code check can continue",
                              "gap_kind": "private_context"})
    assert len(json.loads(forged_body)["branches"]) == 1
    p = h.project(pid)
    forged_id = store.new_id("cq")
    h.db.execute("INSERT INTO clarifications VALUES(?,?,?,?,?,?,?,?,?)",
                 (forged_id, run["task_id"], qid, pid, forged_body, "pending",
                  p["input_version"], p["deletion_generation"], store.now()))
    h.db.commit()
    cards = {c["cq_id"]: c["clarification"]
             for c in h.pending_clarifications(pid)["clarifications"]}
    assert cards[forged_id]["branches"] == []
    assert cards[forged_id]["researcher_note"] == PRIVATE_CONTEXT_NOTE


def test_present_clarification_leaves_other_kinds_untouched():
    body = {"gap": GAP, "gap_kind": "fact",
            "branches": [{"condition": "If yes", "outcome": "Use design A"}]}
    assert present_clarification(body) is body
    legacy = {"gap": GAP, "branches": []}
    assert present_clarification(legacy) is legacy


@pytest.mark.parametrize("gap_type, expected", [
    ("fact", "fact"),
    ("preference", "preference"),
    ("private_context", "private_context"),
    ("permission", "permission"),
    ("none", None),
    ("methodological", None),
    ("unknown_future_value", None),
    (None, None),
])
def test_frame_gap_type_maps_to_clarification_gap_kind(gap_type, expected):
    # Neither vocabulary is renamed: shared snake-case literals pass through,
    # `none` (no gap to clarify) and anything unrecognised map to None so an
    # unclassified card still surfaces exactly as before.
    assert gap_kind_from_frame_gap_type(gap_type) == expected
