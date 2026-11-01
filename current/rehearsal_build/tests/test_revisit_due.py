from test_revisit_conditions import h, seed
from app.revisit import display_status
from app.harness import Conflict
import pytest


def body(did):
    return dict(subject_kind="decision", subject_id=did,
                recorded_date="2026-11-01T12:00:00Z", recorded_by="researcher",
                source="researcher_revisit_form", status="recorded",
                condition_type="explicit_date", note="Exact note")


def test_boundaries():
    row = body("d")
    assert display_status(row, "2026-11-01T11:59:59Z") == "recorded"
    assert display_status(row, "2026-11-01T12:00:00Z") == "due"
    assert display_status(row, "2026-11-02T12:00:00+00:00") == "due"
    for key, value in [("recorded_date", None), ("recorded_date", "broken"),
                       ("recorded_date", "2026-11-01T12:00:00"),
                       ("source", ""), ("recorded_by", "model"), ("subject_id", "")]:
        assert display_status({**row, key: value}, "2026-11-02T12:00:00Z") == "unknown"


def test_reads_and_actions(h, monkeypatch):
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", "Exact reason", None)["decision_id"]
    c = h.write_revisit(pid, body(did))
    monkeypatch.setattr("app.store.now", lambda: "2026-11-02T12:00:00Z")
    before = h.db.total_changes
    listed = h.revisit_conditions(pid)
    assert listed["conditions"][0]["display_status"] == "due"
    for brief in (h.briefing(pid), h.briefing(pid, h.require_chip_scope(pid))):
        item = next(x for x in brief["parts"]["ready_now"] if x.get("id") == c["id"])
        assert item["reason"] == "Exact reason"
        assert item["note"] == "Exact note"
        assert item["decision_status"] == "defer"
        assert item["question"] in item["text"]
        assert item["recorded_date"] in item["text"]
        assert brief["parts"]["suggested_next_action"][0]["id"] == c["id"]
        assert brief["model_calls"] == 0
    assert h.db.total_changes == before
    action = dict(kind="defer", recorded_date="2027-01-01T00:00:00Z",
                  recorded_by="researcher", source="researcher_revisit_form", note=None)
    assert h.write_revisit(pid, action, c["id"])["display_status"] == "recorded"
    assert h.write_revisit(pid, dict(kind="done", recorded_by="researcher",
                                   source="researcher_revisit_form"), c["id"])["display_status"] == "done"
    with pytest.raises(Conflict):
        h.write_revisit(pid, action, c["id"])


def test_subject_refusals(h):
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", "Reason", None)["decision_id"]
    c = h.write_revisit(pid, body(did))
    h.db.execute("UPDATE questions SET memory='session_only' WHERE id=?", (qid,))
    h.db.commit()
    assert h.revisit_conditions(pid)["conditions"] == []
    with pytest.raises(Conflict):
        h.write_revisit(pid, body(did))
    with pytest.raises(Conflict):
        h.write_revisit(pid, dict(kind="done", recorded_by="researcher",
                                 source="researcher_revisit_form"), c["id"])


@pytest.mark.parametrize("reason, expected", [
    ("Researcher's exact saved reason.", "Researcher's exact saved reason."),
    (None, "Reason not recorded"),
])
def test_ui_condition_response_contract(h, reason, expected):
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", reason, None)["decision_id"]
    created = h.write_revisit(pid, body(did))
    envelope = h.revisit_conditions(pid)
    assert set(envelope) == {"conditions", "verified_at", "model_calls"}
    assert envelope["model_calls"] == 0
    deferred = h.write_revisit(pid, dict(
        kind="defer", recorded_date="2028-01-01T00:00:00Z",
        recorded_by="researcher", source="researcher_revisit_form", note=None),
        created["id"])
    done = h.write_revisit(pid, dict(
        kind="done", recorded_by="researcher", source="researcher_revisit_form"),
        created["id"])
    for condition in (created, envelope["conditions"][0], deferred, done):
        assert condition["decision_reason"] == expected
        assert condition["reason"] == reason
        assert condition["decision_id"] == did
        assert condition["question_id"] == qid
        assert condition["decision_status"] == "defer"
        assert condition["subject_id"] == did
        assert condition["subject_kind"] == "decision"
    assert deferred["note"] == "Exact note"
    assert done["display_status"] == "done"


@pytest.mark.parametrize("subject_id", ["", " ", None, {}, [], 123, False])
def test_invalid_subject_id_refused(h, subject_id):
    pid, _ = seed(h)
    before = h.db.total_changes
    with pytest.raises(Conflict):
        h.write_revisit(pid, body(subject_id))
    assert h.db.total_changes == before


@pytest.mark.parametrize("note", [None, "", "   ", "New exact note"])
def test_reschedule_preserves_note_unless_replaced(h, note):
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", "Reason", None)["decision_id"]
    c = h.write_revisit(pid, body(did))
    changed = h.write_revisit(pid, dict(
        kind="defer", recorded_date="2028-01-01T00:00:00Z",
        recorded_by="researcher", source="researcher_revisit_form", note=note),
        c["id"])
    assert changed["note"] == ("New exact note" if note == "New exact note" else "Exact note")


def test_null_condition_type_refused(h):
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", "Reason", None)["decision_id"]
    payload = {**body(did), "condition_type": None}
    before = h.db.total_changes
    with pytest.raises(Conflict):
        h.write_revisit(pid, payload)
    assert h.db.total_changes == before


@pytest.mark.parametrize("timestamp", [
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:59:59-01:00",
])
def test_overflow_timestamp_unknown_and_refused(h, timestamp):
    row = body("decision")
    assert display_status({**row, "recorded_date": timestamp},
                          "2026-11-01T12:00:00Z") == "unknown"
    assert display_status(row, timestamp) == "unknown"
    pid, qid = seed(h)
    did = h.decide(pid, qid, "defer", "Reason", None)["decision_id"]
    before = h.db.total_changes
    with pytest.raises(Conflict):
        h.write_revisit(pid, {**body(did), "recorded_date": timestamp})
    assert h.db.total_changes == before
