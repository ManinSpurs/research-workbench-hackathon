"""Persisted display order for project cards and for a project's library PDFs.

One SQLite authority, migrated additively (store.SCHEMA creates the table on
every connect), so no preference JSON file and no browser-side copy can
disagree with the rows the API returns. Two lists share one table:

    list_kind='project'      scope_id=''          target_id=projects.id
    list_kind='library_pdf'  scope_id=<project>   target_id=library_items.id

A list is always saved whole: the payload names every current member, so the
writer replaces this list's rows in one transaction and a row for a member that
no longer exists cannot survive a save. A list that was never saved has no rows
at all, and the readers then keep the pre-existing deterministic order
(projects by creation, PDFs newest first), with members created after the save
appended after the ones the researcher placed. Notes and folders are not part
of any saved list: their listing order is untouched.
"""
from __future__ import annotations

from . import store
from .harness import Conflict

PROJECT_LIST = "project"
PDF_LIST = "library_pdf"
GLOBAL_SCOPE = ""

# Public project row shape. Unchanged from the pre-existing /projects answer.
PROJECT_COLUMNS = "id, name, kind, pack_id"


def checked_ids(body, key: str, label: str) -> list[str]:
    """The id list under `key`: strings, none empty, no repeats. Key set is the
    caller's contract (it also allows ids such as project_id)."""
    ids = body.get(key)
    if not isinstance(ids, list):
        raise Conflict(f"{key} must be a list")
    if any(not isinstance(i, str) or not i for i in ids):
        raise Conflict(f"{key} must contain {label} ids")
    if len(set(ids)) != len(ids):
        raise Conflict(f"{key} must not repeat an id")
    return ids


def require_complete(given: list[str], current: list[str], key: str, label: str) -> None:
    """Reject a payload that does not name exactly the current membership.

    Checked before anything is written, so a rejection leaves every stored
    order exactly as it was. A partial payload is refused rather than merged:
    merging would silently decide where the omitted rows belong.
    """
    wanted, offered = set(given), set(current)
    missing = [i for i in current if i not in wanted]
    unknown = [i for i in given if i not in offered]
    if missing:
        raise Conflict(f"{key} omits {len(missing)} current {label}; send the complete list")
    if unknown:
        raise Conflict(f"{key} names {len(unknown)} id(s) that are not a current {label}")


def positions(db, kind: str, scope: str) -> dict[str, int]:
    """Saved position per member of this list (empty when it was never saved)."""
    return {r["target_id"]: r["position"] for r in db.execute(
        "SELECT target_id, position FROM list_order WHERE list_kind=? AND scope_id=?", (kind, scope))}


def save(db, kind: str, scope: str, ids: list[str]) -> None:
    """Replace this list's rows with `ids` in order. Caller commits."""
    db.execute("DELETE FROM list_order WHERE list_kind=? AND scope_id=?", (kind, scope))
    stamp = store.now()
    db.executemany(
        "INSERT INTO list_order(list_kind, scope_id, target_id, position, created_at) VALUES(?,?,?,?,?)",
        [(kind, scope, target_id, position, stamp) for position, target_id in enumerate(ids)])


def ordered(rows: list[dict], saved: dict[str, int]) -> list[dict]:
    """Rows in saved order first, then the unsaved ones in the order given.

    The sort is stable, so when nothing was saved every row keeps its input
    position: an unsaved list reads exactly as it did before this feature.
    """
    return sorted(rows, key=lambda r: (0, saved[r["id"]]) if r["id"] in saved else (1, 0))


def projects(h) -> list[dict]:
    """The public project rows, in the researcher's saved order."""
    rows = [dict(r) for r in h.db.execute(
        f"SELECT {PROJECT_COLUMNS} FROM projects ORDER BY created_at, rowid")]
    return ordered(rows, positions(h.db, PROJECT_LIST, GLOBAL_SCOPE))


def save_projects(h, body) -> dict:
    """Persist the project card order. The payload names every current project
    exactly once; anything else is refused before a row is written."""
    if not isinstance(body, dict) or set(body) != {"project_ids"}:
        raise Conflict("body accepts only project_ids")
    ids = checked_ids(body, "project_ids", "project")
    current = [r["id"] for r in h.db.execute("SELECT id FROM projects ORDER BY created_at, rowid")]
    require_complete(ids, current, "project_ids", "projects")
    try:
        save(h.db, PROJECT_LIST, GLOBAL_SCOPE, ids)
        h.db.commit()
    except BaseException:
        h.db.rollback()
        raise
    return {"projects": projects(h)}
