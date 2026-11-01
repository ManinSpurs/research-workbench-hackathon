"""One release-scope rule shared by the legacy harness and the AI grounding check.

A registered source note may declare the release it is scoped to, e.g.
``applies to release 2025 only``. Two paths used to read that note differently:
``harness.p_release_mismatch`` required the trailing ``only`` and matched
case-sensitively, while ``ai.grounding.validate`` ignored ``only`` and matched
case-insensitively. The same note could therefore be judged "another release"
by one path and applicable by the other. Both paths now ask this module.

Scope discipline:

* The only thing read from a note is a declared 4-digit release year stated as
  ``applies to release <year>`` with an optional trailing ``only``, matched
  case-insensitively. No other note wording is interpreted: there is no
  attempt to decide whether a source supports a claim.
* A declared year that differs from the question's release means exactly one
  thing: the note scopes the source to another release. It is a scope
  warning, never proof that a claim is semantically wrong or false.
* When the question's release is absent or not a 4-digit year, year
  applicability is uncheckable and is reported as ``NOT_CHECKED``. It is never
  reported as a pass.
"""
from __future__ import annotations

import re

#: A note's declared scope: "applies to release 2025", "applies to release 2025
#: only", "Applies To Release 2025". The year must be exactly four digits (not
#: part of a longer run of digits); anything else is left uninterpreted.
_RELEASE_NOTE = re.compile(r"applies to release ([0-9]{4})(?!\d)(?: only)?", re.IGNORECASE)
_YEAR = re.compile(r"[0-9]{4}")

#: The note declares the same release as the question.
MATCH = "MATCH"
#: The note declares a different release than the question.
MISMATCH = "MISMATCH"
#: The note declares no release scope, so no year comparison is possible.
UNDECLARED = "UNDECLARED"
#: The question's release is absent or not a 4-digit year: nothing is checkable.
NOT_CHECKED = "NOT_CHECKED"


def declared_release(note) -> str | None:
    """Return the 4-digit release year a note declares, or None.

    ``None`` covers a missing note, a non-string note, and a note that states no
    ``applies to release <year>`` scope. The trailing ``only`` is optional and
    case is ignored.
    """
    if not isinstance(note, str):
        return None
    found = _RELEASE_NOTE.search(note)
    return found.group(1) if found else None


def question_release(release) -> str | None:
    """Normalize a question/project release to its 4-digit year string, or None.

    Accepts an integer or a string year (surrounding whitespace ignored) and
    nothing else: booleans, other widths, empty strings and None normalize to
    None, which makes applicability uncheckable rather than silently passing.
    """
    if isinstance(release, bool):
        return None
    if isinstance(release, int):
        text = str(release)
    elif isinstance(release, str):
        text = release.strip()
    else:
        return None
    return text if _YEAR.fullmatch(text) else None


def year_applicability(note, release) -> dict:
    """Compare one note's declared release with the question's release.

    Returns a JSON-safe dict:

    * ``status``: ``MATCH``, ``MISMATCH``, ``UNDECLARED`` or ``NOT_CHECKED``.
    * ``declared_release``: the year the note declares, or None.
    * ``question_release``: the normalized question year, or None.

    ``NOT_CHECKED`` wins over every other state: without a usable question
    release no comparison is made, so nothing can be reported as matching.
    """
    declared = declared_release(note)
    question = question_release(release)
    if question is None:
        status = NOT_CHECKED
    elif declared is None:
        status = UNDECLARED
    elif declared == question:
        status = MATCH
    else:
        status = MISMATCH
    return {"status": status, "declared_release": declared, "question_release": question}


def release_mismatch(note, release) -> bool:
    """True only when the note declares a release different from ``release``.

    A missing question release and a note with no declared scope are both
    "no mismatch", matching the legacy harness boolean for those inputs. Use
    ``year_applicability`` when the uncheckable case must be visible.
    """
    return year_applicability(note, release)["status"] == MISMATCH
