"""Deleting a chip must never produce a 500 — and never break the launch briefing.

A chip is a research field, and a project's pack_id is its membership record. Deleting
a chip is supported (DELETE /chips/{pack_id}), so a project can end up naming a field
that is no longer installed. That is a real, expected state: reads of that project are
refused with a readable 409 instead of a bare ValueError, and the cross-project launch
briefing keeps working for every other project while saying honestly that this one's
field is gone.

Fixture pattern follows tests/test_chip_projects.py: chips are built through the
authoring path, the offline adapter is used (no model endpoint), and the process-wide
INSTALLED_PACKS registry is reset per test.
"""
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import packs  # noqa: E402
from app.api import create_app  # noqa: E402
from test_chips import install  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_BASE_URL", raising=False)
    monkeypatch.delenv("WB_ALLOW_REAL_MUSE", raising=False)
    monkeypatch.setattr(packs, "INSTALLED_PACKS", {})
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    # raise_server_exceptions=False on purpose: this file is about a crash that used to
    # surface as HTTP 500, so the status code must be observable rather than re-raised.
    with TestClient(create_app(str(tmp_path / "wb.db"), data), raise_server_exceptions=False) as c:
        c.data_dir = data
        yield c


def chip(client, pack_id, title=None):
    response = install(client, pack_id=pack_id, title=title or pack_id.replace("_", " ").title())
    assert response.status_code == 200, response.json()
    return pack_id


def project(client, pack_id=None, kind="fx"):
    body = {"kind": kind}
    if pack_id is not None:
        body["pack_id"] = pack_id
    response = client.post("/projects", json=body)
    assert response.status_code == 200, response.json()
    return response.json()["project_id"]


def ask(client, pid, text):
    response = client.post(f"/projects/{pid}/questions", json={"text": text})
    assert response.status_code == 200, response.json()
    return response.json()["question_id"]


def rows_by_id(payload):
    return {row["project_id"]: row for row in payload["rows"]}


def assert_conflict_envelope(response):
    """The app's one error envelope, with the fields every client already reads."""
    assert response.status_code == 409, (response.status_code, response.text)
    body = response.json()
    assert set(body) == {"error", "message", "details"}
    assert body["error"] == "conflict"
    assert isinstance(body["message"], str) and body["message"]
    assert body["details"] == {}
    return body


def scene(client):
    """A doomed chipped project, a healthy chipped project and a project with no chip.

    Returns (doomed_pid, doomed_chip, healthy_pid, healthy_chip, legacy_pid, doomed_qid)
    with the doomed chip already deleted through the supported route, and one question
    asked in each project beforehand (so each holds real records to report)."""
    doomed_chip = chip(client, "doomed_field", "Doomed Field")
    healthy_chip = chip(client, "healthy_field", "Healthy Field")
    doomed = project(client, doomed_chip)
    healthy = project(client, healthy_chip)
    legacy = project(client)  # created with no chip, as every older project was
    doomed_qid = ask(client, doomed, "What does the rate column measure?")
    ask(client, healthy, "Which agency issued this rule?")
    ask(client, legacy, "What does est_cost measure?")
    removed = client.delete(f"/chips/{doomed_chip}")
    assert removed.status_code == 200, removed.json()
    assert removed.json() == {"deleted": doomed_chip, "source": "file"}
    assert client.get(f"/chips/{doomed_chip}").status_code == 404
    return doomed, doomed_chip, healthy, healthy_chip, legacy, doomed_qid


# ------------------------------------------------------------------ a deleted chip is a 409, never a 500
def test_pack_memory_of_a_project_whose_chip_was_deleted_is_a_409_not_a_500(client):
    doomed, doomed_chip, *_ = scene(client)
    response = client.get(f"/projects/{doomed}/pack-memory")
    body = assert_conflict_envelope(response)
    # Plain wording: the field is gone, the id is named, nothing was read.
    assert doomed_chip in body["message"]
    assert "no longer installed" in body["message"]
    assert "nothing was read" in body["message"].lower()


def test_briefing_of_a_project_whose_chip_was_deleted_is_a_409_not_a_500(client):
    doomed, doomed_chip, *_ = scene(client)
    body = assert_conflict_envelope(client.get(f"/projects/{doomed}/briefing"))
    assert doomed_chip in body["message"]


def test_naming_the_pack_of_a_deleted_chip_is_still_a_readable_409(client):
    # The same guard is reached through an explicit pack_id, so both reachable forms of
    # the ROM routes answer as one refusal instead of one 409 and one 500.
    doomed, doomed_chip, *_ = scene(client)
    body = assert_conflict_envelope(client.get(f"/projects/{doomed}/pack-memory?pack_id={doomed_chip}"))
    assert doomed_chip in body["message"]
    # Pending clarifications scopes through the same guard.
    assert_conflict_envelope(client.get(f"/projects/{doomed}/clarifications?pack_id={doomed_chip}"))


# ------------------------------------------------------------------ the launch briefing survives
def test_launch_briefing_still_works_and_states_the_missing_field(client):
    doomed, doomed_chip, healthy, healthy_chip, legacy, _doomed_qid = scene(client)
    response = client.get("/briefing")
    assert response.status_code != 500, response.text
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = rows_by_id(payload)
    # Nobody is dropped: the affected project is still listed, next to the healthy ones.
    assert set(rows) == {doomed, healthy, legacy}
    assert payload["project_count"] == len(payload["rows"]) == 3
    assert payload["model_calls"] == 0

    broken = rows[doomed]
    # The row says the field is gone instead of pretending the project is empty.
    assert broken["field_missing"] is True
    assert broken["field_missing_pack_id"] == doomed_chip
    text = " ".join(item["text"] for part in broken["parts"].values() for item in part)
    assert doomed_chip in text
    assert "no longer installed" in text
    # The row is honest about consequence and remedy, not just about the fact.
    assert "cannot be read" in text
    assert "Reinstall" in text and "nothing here can be read" in text
    assert broken["guidance"]["level"] in ("calm", "firm", "pointed")
    # It is not passed off as an ordinary empty briefing.
    assert broken["parts"]["waiting_on_you"][0]["text"] != "Nothing waiting."
    assert broken["parts"]["where_you_left_off"][0]["text"] != "No decisions yet."
    # Every existing key is still there.
    assert set(broken) >= {"project_id", "project", "parts", "top_items", "verified_at",
                           "model_calls", "guidance", "job_summary", "lines", "last_activity",
                           "has_waiting", "waiting_count", "has_decision", "decision_count",
                           "input_changed"}

    # The other projects still report normally.
    for pid, pack_id in ((healthy, healthy_chip), (legacy, None)):
        row = rows[pid]
        assert row["field_missing"] is False and row["field_missing_pack_id"] is None
        assert row["has_waiting"] is True
        assert any("issued this rule" in i["text"] or "est_cost" in i["text"]
                   for i in row["parts"]["waiting_on_you"])
        assert set(row["parts"]) == {"where_you_left_off", "what_changed_since", "ready_now",
                                     "waiting_on_you", "suggested_next_action"}


def test_the_other_projects_still_read_their_own_records_after_the_delete(client):
    doomed, _doomed_chip, healthy, healthy_chip, legacy, _doomed_qid = scene(client)
    assert client.get(f"/projects/{healthy}/pack-memory").status_code == 200
    healthy_memory = client.get(f"/projects/{healthy}/pack-memory").json()
    assert healthy_memory["pack_id"] == healthy_chip and healthy_memory["questions"]
    assert client.get(f"/projects/{healthy}/briefing").status_code == 200
    # A project with no chip keeps the documented historical default.
    legacy_memory = client.get(f"/projects/{legacy}/pack-memory").json()
    assert legacy_memory["pack_id"] == "social_science" and legacy_memory["questions"]
    assert client.get(f"/projects/{legacy}/briefing").status_code == 200
    # And the deleted chip leaves no residual name behind: it is not a usable pack again.
    refuse = client.post("/projects", json={"kind": "fx", "pack_id": _doomed_chip})
    assert refuse.status_code == 409
    assert client.get("/projects").json()["projects"]


def test_a_project_with_no_chip_is_unaffected_by_a_chip_delete(client):
    doomed_chip = chip(client, "doomed_field", "Doomed Field")
    doomed = project(client, doomed_chip)
    legacy = project(client)
    ask(client, legacy, "What does est_cost measure?")
    assert client.delete(f"/chips/{doomed_chip}").status_code == 200

    assert client.get(f"/projects/{legacy}/pack-memory").json()["pack_id"] == "social_science"
    brief = client.get(f"/projects/{legacy}/briefing")
    assert brief.status_code == 200
    assert brief.json()["project_id"] == legacy
    # Still asks questions under the default pack, and is never adopted by a chip.
    ask(client, legacy, "How is the filing date recorded?")
    row = rows_by_id(client.get("/briefing").json())[legacy]
    assert row["field_missing"] is False
    assert row["has_waiting"] is True
    # The doomed project still exists and is still refused, never silently moved.
    assert client.get(f"/projects/{doomed}/pack-memory").status_code == 409
    assert {p["id"]: p["pack_id"] for p in client.get("/projects").json()["projects"]}[doomed] == doomed_chip


def test_running_a_question_pinned_to_a_deleted_chip_refuses_rather_than_crashing(client):
    # A question asked inside the chip is pinned to that chip. After the delete the pin
    # names nothing, and the run path resolves the pinned pack: it must refuse with the
    # same readable 409, not escape as a ValueError (HTTP 500).
    doomed, doomed_chip, _healthy, _healthy_chip, _legacy, doomed_qid = scene(client)
    for method, path in (("post", f"/questions/{doomed_qid}/run"),
                         ("post", f"/questions/{doomed_qid}/shadow")):
        response = getattr(client, method)(path)
        assert response.status_code != 500, (path, response.text)
        assert response.status_code == 409, (path, response.status_code, response.text)
        body = response.json()
        assert body["error"] == "conflict" and doomed_chip in body["message"]


def test_a_project_with_no_pinned_question_can_still_be_moved_after_the_delete(client):
    # Nothing is bricked: a project with no question pinned to the deleted chip can be
    # moved to an installed chip through the existing explicit route and reads again.
    doomed_chip = chip(client, "doomed_field", "Doomed Field")
    healthy_chip = chip(client, "healthy_field", "Healthy Field")
    pid = project(client, doomed_chip)
    assert client.delete(f"/chips/{doomed_chip}").status_code == 200
    assert client.get(f"/projects/{pid}/pack-memory").status_code == 409

    moved = client.post(f"/projects/{pid}/chip", json={"pack_id": healthy_chip})
    assert moved.status_code == 200, moved.json()
    assert client.get(f"/projects/{pid}/pack-memory").json()["pack_id"] == healthy_chip
    row = rows_by_id(client.get("/briefing").json())[pid]
    assert row["field_missing"] is False


# ------------------------------------------------------------------ isolation is unchanged
def test_no_route_reachable_for_this_project_answers_500_after_the_delete(client):
    # Sweep every read route that resolves the project's field, so a bare ValueError
    # anywhere in the chain would show up here as a 500 rather than as a refusal.
    doomed, doomed_chip, healthy, healthy_chip, legacy, doomed_qid = scene(client)
    # A new question in a field that no longer exists cannot be pinned either.
    refused_question = client.post(f"/projects/{doomed}/questions",
                                   json={"text": "A new question after the delete"})
    assert refused_question.status_code == 409
    assert doomed_chip in refused_question.json()["message"]

    paths = [f"/projects/{doomed}/pack-memory",
             f"/projects/{doomed}/pack-memory?pack_id={doomed_chip}",
             f"/projects/{doomed}/pack-memory?pack_id=social_science",
             f"/projects/{doomed}/briefing",
             f"/projects/{doomed}/briefing?pack_id={doomed_chip}",
             f"/projects/{doomed}/clarifications",
             f"/projects/{doomed}/clarifications?pack_id={doomed_chip}",
             f"/projects/{doomed}/profile",
             f"/projects/{doomed}/secretary-turns",
             "/briefing",
             "/projects",
             "/domain-packs",
             f"/projects/{healthy}/pack-memory",
             f"/projects/{healthy}/briefing",
             f"/projects/{healthy}/briefing?pack_id={healthy_chip}",
             f"/projects/{healthy}/clarifications?pack_id={healthy_chip}",
             f"/projects/{legacy}/pack-memory",
             f"/projects/{legacy}/briefing",
             # A question pinned to the deleted chip must refuse, not crash.
             f"/questions/{doomed_qid}/context",
             f"/questions/{doomed_qid}/deletion-preview",
             f"/questions/{doomed_qid}/canvas",
             f"/questions/{doomed_qid}/ai/jobs"]
    for path in paths:
        response = client.get(path)
        assert response.status_code != 500, (path, response.text)
    # Question-scoped reads of a question pinned to the deleted chip already refused
    # readably before this fix (question_context wraps the same ValueError), and they
    # still do; deletion-preview never resolves a pack, so it stays readable.
    for path in (f"/questions/{doomed_qid}/context", f"/questions/{doomed_qid}/canvas"):
        body = assert_conflict_envelope(client.get(path))
        assert doomed_chip in body["message"]
    assert client.get(f"/questions/{doomed_qid}/ai/jobs").status_code == 200
    assert client.get(f"/questions/{doomed_qid}/deletion-preview").status_code == 200
    # Moving the project is still gated by its pinned questions, and refuses readably.
    moved = client.post(f"/projects/{doomed}/chip", json={"pack_id": healthy_chip})
    assert moved.status_code != 500, moved.text
    assert moved.status_code == 409 and doomed_chip in moved.json()["message"]

    # Only the field-dependent reads of the affected project are refused, and they are
    # refused as a readable 409 — the project itself is still there and still readable.
    assert client.get(f"/projects/{doomed}/profile").status_code == 200
    assert client.get(f"/projects/{doomed}/pack-memory").status_code == 409
    assert client.get(f"/projects/{doomed}/briefing").status_code == 409
    assert client.get(f"/projects/{doomed}/briefing?pack_id={doomed_chip}").status_code == 409
    assert client.get("/briefing").status_code == 200


def test_naming_another_chip_is_still_refused_for_both_kinds_of_project(client):
    doomed, doomed_chip, healthy, healthy_chip, legacy, _doomed_qid = scene(client)
    other = chip(client, "other_field", "Other Field")

    healthy_refusal = assert_conflict_envelope(
        client.get(f"/projects/{healthy}/pack-memory?pack_id={other}"))
    assert healthy_chip in healthy_refusal["message"] and other in healthy_refusal["message"]
    assert "belongs to chip" in healthy_refusal["message"]
    assert client.get(f"/projects/{healthy}/pack-memory").status_code == 200

    # A project whose chip is gone still refuses another chip by the isolation rule: the
    # deletion did not weaken the boundary or turn the project into a chip-less one.
    doomed_refusal = assert_conflict_envelope(
        client.get(f"/projects/{doomed}/pack-memory?pack_id={other}"))
    assert doomed_chip in doomed_refusal["message"] and other in doomed_refusal["message"]
    assert "belongs to chip" in doomed_refusal["message"]

    # A chip-less project may still read a named chip's memory, as before.
    assert client.get(f"/projects/{legacy}/pack-memory?pack_id={other}").status_code == 200
    assert client.get(f"/projects/{legacy}/pack-memory").json()["pack_id"] == "social_science"
