"""Pure read-time evaluation of researcher-recorded revisit instants."""
from datetime import datetime, timezone


def utc_instant(value):
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def display_status(row, verified_at):
    date, verified = utc_instant(row.get("recorded_date")), utc_instant(verified_at)
    if (date is None or verified is None or row.get("recorded_by") != "researcher"
            or not isinstance(row.get("source"), str) or not row["source"].strip()
            or row.get("condition_type") != "explicit_date"
            or row.get("subject_kind") != "decision" or not row.get("subject_id")):
        return "unknown"
    status = row.get("status")
    if status in ("done", "acknowledged"):
        return status
    if status != "recorded":
        return "unknown"
    return "due" if date <= verified else "recorded"
