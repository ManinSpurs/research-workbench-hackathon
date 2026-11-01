"""Q3a: schema-only private-context gap kind (audit gap #6, spec §6 step 4 / §7c).

Pure schema tests: no harness, no store, no network, no model calls.
Covers only the additive `gap_kind` on `ClarificationNeeded` in
`app/results.py`. Q3b (harness respond-path branch + clarification-card copy)
is explicitly out of scope and is not exercised here.

Identifier: `private_context` — the snake-case literal already enacted in
`Frame.gap_type` (`app/ai/service.py:59,64`), shared with the product-spec
wording "a fact, a preference, private context, permission or a
methodological uncertainty" (`RESEARCH_SECRETARY_PRODUCT_SPEC_EN.md:87`).
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.results import Branch, ClarificationNeeded


def _base(**overrides):
    fields = {
        "gap": "GAP-UNDER-TEST: undisclosed household detail needed for interpretation",
        "affected": "Interpretation of the estimate",
        "why_matters": "The answer changes which definition applies",
        "branches": [],
        "independent_work": "Read the registered definition while the researcher decides",
    }
    fields.update(overrides)
    return ClarificationNeeded(**fields)


def test_legacy_row_without_gap_kind_still_validates():
    # Pre-Q3a stored rows carry no gap_kind; the field is optional so they
    # keep validating exactly as before.
    c = _base()
    assert c.gap_kind is None
    assert c.branches == []


@pytest.mark.parametrize("kind", ["fact", "preference", "private_context", "permission", "methodological"])
def test_each_gap_kind_validates_as_its_own_kind(kind):
    c = _base(gap_kind=kind)
    assert c.gap_kind == kind


def test_non_private_kinds_keep_conditional_branches():
    c = _base(gap_kind="fact", branches=[Branch(condition="If X", outcome="Use definition A")])
    assert len(c.branches) == 1


def test_private_context_gap_carries_no_branches_over_undisclosed_content():
    # A private-context gap is a blocked explanation (provide/defer/decline);
    # conditional branches would fabricate model-planned paths over content
    # the researcher has not disclosed, so the schema rejects them.
    with pytest.raises(ValidationError):
        _base(gap_kind="private_context",
              branches=[Branch(condition="If the household is large", outcome="Assume definition B")])


def test_branch_cap_unchanged_for_all_kinds():
    many = [Branch(condition=f"C{i}", outcome=f"O{i}") for i in range(4)]
    with pytest.raises(ValidationError):
        _base(branches=many)
    with pytest.raises(ValidationError):
        _base(gap_kind="fact", branches=many)


def test_unknown_gap_kind_rejected():
    with pytest.raises(ValidationError):
        _base(gap_kind="personal_secret")
