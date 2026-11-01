"""Result kinds (lite §5.3). Schema v0 based on the second pre-event spike, adapted for strict JSON-schema output:
every field is required in the wire schema, so optional values are nullable rather than defaulted."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Adapter = Literal["mock", "muse"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefinedQuestion(Strict):
    refined_text: str = Field(min_length=1)
    kept_constraints: list[str]          # model's own list; the harness checks meaning independently


class Branch(Strict):
    condition: str = Field(min_length=1)
    outcome: str = Field(min_length=1)


class VariableRef(Strict):
    name: str = Field(min_length=1)
    dataset_id: str | None
    kind: Literal["actual", "derived", "additional", "unverified"]
    role: str = Field(min_length=1)


class DesignProposal(Strict):
    question: str = Field(min_length=1)
    variables: list[VariableRef] = Field(min_length=1)
    assumptions: list[str]
    risks: list[str]
    verification_plan: str = Field(min_length=1)


class ClarificationNeeded(Strict):
    gap: str = Field(min_length=1)
    affected: str = Field(min_length=1)
    why_matters: str = Field(min_length=1)
    branches: list[Branch] = Field(max_length=3)
    independent_work: str = Field(min_length=1)
    # Q3a (schema-only): first-class gap taxonomy from the product spec §6 step 4
    # ("a fact, a preference, private context, permission or a methodological
    # uncertainty"). Snake-case values reuse the already-enacted Frame.gap_type
    # literal `private_context`. Optional so pre-Q3a stored rows still validate;
    # harness respond-path presentation lives in `present_clarification` (Q3b).
    gap_kind: Literal["fact", "preference", "private_context", "permission", "methodological"] | None = None

    @field_validator("branches")
    @classmethod
    def _max_three(cls, v):
        if len(v) > 3:
            raise ValueError("at most three branches")
        return v

    @model_validator(mode="after")
    def _private_context_carries_no_fabricated_paths(self):
        # A private-context gap is a blocked explanation (user provides, defers,
        # or declines); conditional branches would fabricate model-planned paths
        # over undisclosed content, so the schema rejects them.
        if self.gap_kind == "private_context" and self.branches:
            raise ValueError("a private-context gap carries no conditional branches over undisclosed content")
        return self


# Q3b (harness presentation + vocabulary seam): the frame pass (`Frame.gap_type`
# in `app/ai/service.py`, FRAME_CONTRACT vocabulary) and the clarification
# schema (`ClarificationNeeded.gap_kind`, this module) share the snake-case
# literals `fact`, `preference`, `private_context` and `permission` by design;
# neither vocabulary is renamed. `methodological` has no frame counterpart (the
# frame pass reports it as `none`), and a frame `none` carries no gap to
# clarify, so both map to `None`.
FRAME_GAP_TYPE_TO_GAP_KIND = {
    "fact": "fact",
    "preference": "preference",
    "private_context": "private_context",
    "permission": "permission",
    "none": None,
}


def gap_kind_from_frame_gap_type(gap_type: str | None) -> str | None:
    """Map a frame-pass `gap_type` to the clarification `gap_kind`.

    Unknown or absent values map to `None` (unclassified) rather than raising,
    so a future frame vocabulary addition can never break clarification
    construction; such cards still surface exactly as before.
    """
    return FRAME_GAP_TYPE_TO_GAP_KIND.get(gap_type)


# Quiet, non-technical card copy for a private-context gap (spec §7c: the
# correct response is to explain what remains blocked and let the researcher
# provide, defer or decline). The researcher supplies the context; the system
# never guesses, infers or fetches it.
PRIVATE_CONTEXT_NOTE = (
    "This question needs context only you hold. "
    "Please share what you can — the system will not guess, infer, or fetch it."
)


def present_clarification(body: dict) -> dict:
    """Surface presentation of a stored or returned clarification dict.

    A `private_context` gap renders branchless with the supply-your-own note,
    even if the stored body predates Q3a validation or was forced past it:
    conditional branches would fabricate model-planned paths over undisclosed
    content. Every other gap kind is returned unchanged.
    """
    if not isinstance(body, dict) or body.get("gap_kind") != "private_context":
        return body
    view = dict(body)
    view["branches"] = []
    view["researcher_note"] = PRIVATE_CONTEXT_NOTE
    return view


class FirstStep(Strict):
    """Envelope for the first harness step. The demo needs a design and its consequential
    clarification together; both are still separate result kinds (spec gap reported in REPORT.md)."""
    route: Literal["clarify", "direct_answer", "preference", "mock_unsupported"]
    design: DesignProposal | None
    clarification: ClarificationNeeded | None
    direct_answer: str | None
    alternatives: list[str]

    @model_validator(mode="after")
    def _consistent(self):
        if self.route == "clarify" and self.clarification is None:
            raise ValueError("route=clarify needs a clarification")
        if self.route == "direct_answer" and not self.direct_answer:
            raise ValueError("route=direct_answer needs direct_answer")
        if self.route == "preference" and len(self.alternatives) < 2:
            raise ValueError("route=preference needs at least two alternatives")
        return self


class InvestigationPlan(Strict):
    target_gap: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)   # must be registered/allowlisted source IDs
    fields: list[str]                          # dictionary fields to read
    stop_condition: str = Field(min_length=1)


class EvidenceAssessment(Strict):
    claim: str = Field(min_length=1)
    evidence_ids: list[str]
    applicability: str = Field(min_length=1)
    outcome: Literal["supported", "partial", "conflicting", "insufficient", "blocked"]
    confirmation_level: Literal["confirmed", "partial", "conflicting", "unconfirmed"]
    unresolved: list[str]
    precedent_label: str | None
    precedent_source_ids: list[str]
    suggested_next_action: str = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self):
        if self.precedent_source_ids and self.confirmation_level == "confirmed":
            raise ValueError("a precedent is never a confirmed definition")
        if self.confirmation_level == "confirmed" and self.outcome != "supported":
            raise ValueError("confirmed requires outcome=supported")
        if self.outcome in ("supported", "partial", "conflicting") and not self.evidence_ids:
            raise ValueError("an evidence-based outcome needs evidence_ids")
        return self
