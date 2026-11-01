"""Slice D3 PART 1 -- persistent overlay version store (explicit activation/rollback).

Design source: ``current/ADAPTIVE_DOMAIN_PACK_HARNESS_EN.md`` sections 3
(steps 5-7), 6.1 and 7.1. PART 1 ONLY: the persistent store half. Nothing
here wires into briefing, harness, api, frontend or docs (parallel scope).

Storage: the ``overlay_versions`` table declared in ``app/store.py``
(additive DDL; the connection passed in must come from ``store.connect``,
which applies it, so rows are readable by the schema-driven deletion
verification in ``store.find_strings`` like every other TEXT column).
One row per (project_id, pack_id, overlay_version).

Rules enforced here -- refused, never silently repaired:
- every persisted version passes
  ``adaptive_readiness.validate_version_shape``, so an ``approved`` version
  without filed benchmark refs is REFUSED at write time (the AD03 gate);
  approver/timestamp fields from design section 3 step 6 are CONCEPTUAL-ONLY
  and are not modeled as real identity;
- ``entries``, when present, must be a list whose every item passes
  ``adaptive_pack.validate_overlay_entry``;
- versions are immutable once written: persisting an already-stored
  (project_id, pack_id, overlay_version) key is refused, identical or not;
- activation is explicit only: ``activate`` requires the exact stored
  version label plus a non-empty recorded reason string. Silence, timeouts
  and nudges can never activate -- there is no timer, thread, training or
  auto path anywhere in this module;
- ``rollback`` restores the previously active version (or cold/none) with a
  recorded reason and never deletes history rows.

Standard library plus the D1/D2 pure-logic validators only: no model calls,
no network, no timers, no threads, and no imports from harness, api,
frontend or store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import adaptive_pack as _pack
from . import adaptive_readiness as _readiness

__all__ = [
    "persist_overlay_version",
    "activate",
    "rollback",
    "active_version",
    "version_history",
]

_COLUMNS = ("project_id, pack_id, overlay_version, status, body_json,"
            " active, active_reason, activated_at, prev_active_version,"
            " deactivated_at, deactivation_reason, created_at")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_scope(project_id, pack_id):
    if not isinstance(project_id, str) or not project_id.strip():
        return (False, "refused: project_id must be a non-empty string")
    if not isinstance(pack_id, str) or not pack_id.strip():
        return (False, "refused: pack_id must be a non-empty string")
    return (True, "")


def _row_to_dict(row):
    return {
        "project_id": row["project_id"],
        "pack_id": row["pack_id"],
        "overlay_version": row["overlay_version"],
        "status": row["status"],
        "body": json.loads(row["body_json"]),
        "active": bool(row["active"]),
        "active_reason": row["active_reason"],
        "activated_at": row["activated_at"],
        "prev_active_version": row["prev_active_version"],
        "deactivated_at": row["deactivated_at"],
        "deactivation_reason": row["deactivation_reason"],
        "created_at": row["created_at"],
    }


def _fetch(db, project_id, pack_id, overlay_version):
    return db.execute(
        "SELECT %s FROM overlay_versions"
        " WHERE project_id=? AND pack_id=? AND overlay_version=?" % _COLUMNS,
        (project_id, pack_id, overlay_version)).fetchone()


def _active(db, project_id, pack_id):
    return db.execute(
        "SELECT %s FROM overlay_versions"
        " WHERE project_id=? AND pack_id=? AND active=1" % _COLUMNS,
        (project_id, pack_id)).fetchone()


def persist_overlay_version(db, project_id, pack_id, version_dict):
    """Persist one overlay version row; never auto-activates (stays inactive).

    Returns (True, note) on success, (False, reason) on any refusal.
    """
    ok, note = _valid_scope(project_id, pack_id)
    if not ok:
        return (False, note)
    if not isinstance(version_dict, dict):
        return (False, "refused: version must be a dict; invalid shapes are refused, never repaired")
    ok, gate_note, _status = _readiness.validate_version_shape(version_dict)
    if not ok:
        return (False, "refused: " + gate_note)
    if "entries" in version_dict:
        entries = version_dict["entries"]
        if not isinstance(entries, list):
            return (False, "refused: version 'entries' must be a list of overlay entry dicts")
        for index, entry in enumerate(entries):
            entry_ok, entry_note = _pack.validate_overlay_entry(entry)
            if not entry_ok:
                return (False, "refused: entries[%d] invalid: %s" % (index, entry_note))
    label = version_dict["overlay_version"].strip()
    if _fetch(db, project_id, pack_id, label) is not None:
        return (False, "refused: overlay version %r is already persisted for project %r + pack %r; "
                       "versions are immutable once written" % (label, project_id, pack_id))
    try:
        body_json = json.dumps(version_dict, sort_keys=True)
    except (TypeError, ValueError):
        return (False, "refused: version is not JSON-serializable; invalid shapes are refused, never repaired")
    db.execute("INSERT INTO overlay_versions(project_id, pack_id, overlay_version, status, body_json,"
               " active, active_reason, activated_at, prev_active_version,"
               " deactivated_at, deactivation_reason, created_at)"
               " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (project_id, pack_id, label, version_dict["status"], body_json,
                0, None, None, None, None, None, _now()))
    db.commit()
    return (True, "persisted overlay %s for project %s + pack %s (inactive until explicit activate)"
            % (label, project_id, pack_id))


def activate(db, project_id, pack_id, overlay_version, reason):
    """Explicitly activate a persisted version; reason string is required.

    There is no auto-activate, no training call and no timer path: only this
    function, called with an explicit label and reason, changes what is
    active. Returns (True, note) on success, (False, reason) on refusal.
    """
    ok, note = _valid_scope(project_id, pack_id)
    if not ok:
        return (False, note)
    if not isinstance(overlay_version, str) or not overlay_version.strip():
        return (False, "refused: activate needs the exact stored overlay_version label; nothing is invented")
    if not isinstance(reason, str) or not reason.strip():
        return (False, "refused: activation requires an explicit recorded reason string; "
                       "silence, timeouts and nudges can never activate")
    label = overlay_version.strip()
    target = _fetch(db, project_id, pack_id, label)
    if target is None:
        return (False, "refused: overlay version %r was never persisted for project %r + pack %r; "
                       "activation of an unknown version is refused" % (label, project_id, pack_id))
    current = _active(db, project_id, pack_id)
    if current is not None and current["overlay_version"] == label:
        return (False, "refused: overlay version %r is already active; re-activation is refused" % (label,))
    stamped = _now()
    if current is not None:
        db.execute("UPDATE overlay_versions SET active=0, deactivated_at=?, deactivation_reason=? "
                   "WHERE project_id=? AND pack_id=? AND overlay_version=?",
                   (stamped, "superseded by explicit activation of %s" % label,
                    project_id, pack_id, current["overlay_version"]))
        prev_label = current["overlay_version"]
    else:
        prev_label = None
    db.execute("UPDATE overlay_versions SET active=1, active_reason=?, activated_at=?, prev_active_version=? "
               "WHERE project_id=? AND pack_id=? AND overlay_version=?",
               (reason.strip(), stamped, prev_label, project_id, pack_id, label))
    db.commit()
    return (True, "activated overlay %s for project %s + pack %s (new work only). Reason recorded: %s"
            % (label, project_id, pack_id, reason.strip()))


def rollback(db, project_id, pack_id, reason):
    """Restore the previously active version (or cold/none) with a reason.

    History rows are never deleted. Returns (True, note) on success,
    (False, reason) when there is nothing active to roll back.
    """
    ok, note = _valid_scope(project_id, pack_id)
    if not ok:
        return (False, note)
    if not isinstance(reason, str) or not reason.strip():
        return (False, "refused: rollback requires an explicit recorded reason string")
    current = _active(db, project_id, pack_id)
    if current is None:
        return (False, "refused: no active overlay version for project %r + pack %r; "
                       "there is nothing to restore and none is invented" % (project_id, pack_id))
    stamped = _now()
    db.execute("UPDATE overlay_versions SET active=0, deactivated_at=?, deactivation_reason=? "
               "WHERE project_id=? AND pack_id=? AND overlay_version=?",
               (stamped, "rollback: " + reason.strip(),
                project_id, pack_id, current["overlay_version"]))
    prev_label = current["prev_active_version"]
    if prev_label is None:
        db.commit()
        return (True, "rolled back to cold/none for project %s + pack %s (no earlier version). "
                      "Reason recorded: %s" % (project_id, pack_id, reason.strip()))
    previous = _fetch(db, project_id, pack_id, prev_label)
    if previous is None:  # unreachable: history rows are never deleted; fail closed.
        db.commit()
        return (True, "rolled back to cold/none for project %s + pack %s (previous row missing). "
                      "Reason recorded: %s" % (project_id, pack_id, reason.strip()))
    db.execute("UPDATE overlay_versions SET active=1, active_reason=?, activated_at=? "
               "WHERE project_id=? AND pack_id=? AND overlay_version=?",
               ("rollback: " + reason.strip(), stamped, project_id, pack_id, prev_label))
    db.commit()
    return (True, "rolled back: overlay %s restored for project %s + pack %s. Reason recorded: %s"
            % (prev_label, project_id, pack_id, reason.strip()))


def active_version(db, project_id, pack_id):
    """Return the active version row dict for one scope, or None when cold."""
    ok, _ = _valid_scope(project_id, pack_id)
    if not ok:
        return None
    row = _active(db, project_id, pack_id)
    return _row_to_dict(row) if row is not None else None


def version_history(db, project_id, pack_id):
    """Return every persisted version row for one scope, in write order."""
    ok, _ = _valid_scope(project_id, pack_id)
    if not ok:
        return []
    rows = db.execute(
        "SELECT %s FROM overlay_versions"
        " WHERE project_id=? AND pack_id=? ORDER BY rowid" % _COLUMNS,
        (project_id, pack_id)).fetchall()
    return [_row_to_dict(row) for row in rows]
