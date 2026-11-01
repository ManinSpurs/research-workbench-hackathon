"""A chip holds projects: one chip may hold several, and two chips never share records.

Chips are built through the authoring path (app.chips via the /chips routes, using the
helpers in tests/test_chips.py), so every id here is a chip a person could really make.
The offline adapter is used on purpose: these tests count and isolate records, they do
not exercise a model.
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
    # No model endpoint: the offline adapter refines nothing and calls nothing, so
    # what these tests observe is the chip membership record and nothing else.
    monkeypatch.delenv("FAKE_BASE_URL", raising=False)
    monkeypatch.delenv("WB_ALLOW_REAL_MUSE", raising=False)
    # Installed chips are process-wide by design; each test starts from a clean registry
    # so one test's chip cannot make another test's made-up id resolvable.
    monkeypatch.setattr(packs, "INSTALLED_PACKS", {})
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    with TestClient(create_app(str(tmp_path / "wb.db"), data)) as c:
        c.data_dir = data
        yield c


def chip(client, pack_id, title=None):
    """Installs one chip through the authoring path and returns its pack id."""
    response = install(client, pack_id=pack_id, title=title or pack_id.replace("_", " ").title())
    assert response.status_code == 200, response.json()
    assert response.json()["pack"]["pack_id"] == pack_id
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


UPLOAD = {
    "name": "Chip fixture data",
    "csv_text": "rate\n12\n13\n",
    "dictionary": {"entries": [{"source_id": "SRC-1", "evidence_id": "EV-1", "field": "rate",
                                "definition": "The filed rate, in the register's own unit.",
                                "location": "fixture dictionary:rate"}]},
}


# ------------------------------------------------------------------ a chip's portfolio
def test_one_chip_holds_two_projects_and_lists_both_with_counts(client):
    chip(client, "urban_policy", "Urban Policy")
    tax = project(client, "urban_policy")
    regulation = project(client, "urban_policy")
    ask(client, tax, "What does the rate column measure?")
    ask(client, tax, "How is the filing date recorded?")
    ask(client, regulation, "Which agency issued this rule?")

    body = client.get("/chips/urban_policy/projects").json()
    assert body["pack_id"] == "urban_policy"
    # Newest first: one chip holding several projects is the big-chip case.
    assert [row["id"] for row in body["projects"]] == [regulation, tax]
    by_id = {row["id"]: row for row in body["projects"]}
    assert set(by_id[tax]) == {"id", "name", "kind", "pack_id", "question_count", "created_at"}
    assert by_id[tax]["question_count"] == 2
    assert by_id[regulation]["question_count"] == 1
    assert by_id[tax]["pack_id"] == "urban_policy"


def test_another_chips_projects_never_appear(client):
    chip(client, "urban_policy", "Urban Policy")
    chip(client, "remote_sensing", "Remote Sensing")
    tax = project(client, "urban_policy")
    bands = project(client, "remote_sensing")

    urban = [row["id"] for row in client.get("/chips/urban_policy/projects").json()["projects"]]
    remote = [row["id"] for row in client.get("/chips/remote_sensing/projects").json()["projects"]]
    assert urban == [tax]
    assert remote == [bands]
    assert tax not in remote and bands not in urban


def test_unknown_chip_is_the_same_404_as_the_chip_route(client):
    response = client.get("/chips/no_such_chip/projects")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "no_such_chip", "details": {}}
    assert response.json() == client.get("/chips/no_such_chip").json()


# ------------------------------------------------------------------ projects that predate chips
def test_project_without_a_chip_keeps_working_and_is_never_assigned_one(client):
    chip(client, "urban_policy", "Urban Policy")
    legacy = project(client)  # created with no chip, as every older project was
    ask(client, legacy, "What does est_cost measure?")

    assert client.get("/chips/urban_policy/projects").json()["projects"] == []
    assert client.get(f"/projects/{legacy}/profile").status_code == 200
    assert client.get(f"/projects/{legacy}/clarifications").status_code == 200
    # Reading this project's memory keeps the historical default and never invents a chip.
    assert client.get(f"/projects/{legacy}/pack-memory").json()["pack_id"] == "social_science"
    listed = {row["id"]: row for row in client.get("/projects").json()["projects"]}
    assert listed[legacy]["pack_id"] is None
    # Listing a chip is read-only: it did not adopt the project.
    assert client.get("/chips/urban_policy/projects").json()["projects"] == []


# ------------------------------------------------------------------ unknown chip ids are refused
def test_made_up_chip_id_is_refused_and_a_real_chip_is_accepted(client):
    chip(client, "urban_policy", "Urban Policy")
    refused = client.post("/projects", json={"kind": "fx", "pack_id": "made_up_chip"})
    assert refused.status_code == 409
    assert refused.json()["error"] == "conflict"
    assert "made_up_chip" in refused.json()["message"]
    accepted = client.post("/projects", json={"kind": "fx", "pack_id": "urban_policy"})
    assert accepted.status_code == 200
    assert accepted.json()["pack_id"] == "urban_policy"


def test_upload_refuses_a_made_up_chip_and_accepts_a_real_one(client):
    chip(client, "urban_policy", "Urban Policy")
    refused = client.post("/projects/upload", json={**UPLOAD, "pack_id": "made_up_chip"})
    assert refused.status_code == 409
    assert "made_up_chip" in refused.json()["message"]
    accepted = client.post("/projects/upload", json={**UPLOAD, "pack_id": "urban_policy"})
    assert accepted.status_code == 200
    assert accepted.json()["pack_id"] == "urban_policy"
    listed = client.get("/chips/urban_policy/projects").json()["projects"]
    assert [row["id"] for row in listed] == [accepted.json()["project_id"]]


# ------------------------------------------------------------------ isolation is enforced
def test_one_chip_may_not_read_another_chips_project_records(client):
    chip(client, "urban_policy", "Urban Policy")
    chip(client, "remote_sensing", "Remote Sensing")
    tax = project(client, "urban_policy")
    ask(client, tax, "What does the rate column measure?")

    # Pack memory scopes by pack, so naming the other chip is refused rather than served.
    refused = client.get(f"/projects/{tax}/pack-memory?pack_id=remote_sensing")
    assert refused.status_code == 409
    assert "urban_policy" in refused.json()["message"]
    assert client.get(f"/projects/{tax}/pack-memory").json()["questions"]
    # Question listing (pending clarifications) scopes by the same pack, so it agrees.
    assert client.get(f"/projects/{tax}/clarifications?pack_id=remote_sensing").status_code == 409
    # A question cannot be pinned into a chip the project does not belong to.
    pinned = client.post(f"/projects/{tax}/questions",
                         json={"text": "Which band was this measured in?", "pack_id": "remote_sensing"})
    assert pinned.status_code == 409
    assert "remote_sensing" in pinned.json()["message"]
    assert client.get("/chips/remote_sensing/projects").json()["projects"] == []


# ------------------------------------------------------------------ moving between chips
def test_moving_a_project_with_pinned_questions_is_refused(client):
    chip(client, "urban_policy", "Urban Policy")
    chip(client, "remote_sensing", "Remote Sensing")
    pid = project(client, "urban_policy")
    ask(client, pid, "What does the rate column measure?")

    response = client.post(f"/projects/{pid}/chip", json={"pack_id": "remote_sensing"})
    assert response.status_code == 409
    message = response.json()["message"]
    assert "urban_policy" in message and "remote_sensing" in message
    assert "pinned" in message
    # Nothing moved: the pin and the membership are both unchanged.
    assert [row["id"] for row in client.get("/chips/urban_policy/projects").json()["projects"]] == [pid]
    assert client.get("/chips/remote_sensing/projects").json()["projects"] == []
    assert client.get(f"/projects/{pid}/pack-memory").json()["questions"][0]["id"]


def test_moving_a_project_without_pinned_questions_succeeds(client):
    chip(client, "urban_policy", "Urban Policy")
    chip(client, "remote_sensing", "Remote Sensing")
    pid = project(client, "urban_policy")

    response = client.post(f"/projects/{pid}/chip", json={"pack_id": "remote_sensing"})
    assert response.status_code == 200
    assert response.json() == {"project_id": pid, "pack_id": "remote_sensing",
                               "previous_pack_id": "urban_policy"}
    assert client.get("/chips/urban_policy/projects").json()["projects"] == []
    assert [row["id"] for row in client.get("/chips/remote_sensing/projects").json()["projects"]] == [pid]
    # The next question is asked under the new chip, without naming it.
    ask(client, pid, "Which band was this measured in?")
    assert client.get(f"/projects/{pid}/pack-memory").json()["questions"][0]["id"]


def test_move_to_a_made_up_chip_is_refused(client):
    chip(client, "urban_policy", "Urban Policy")
    pid = project(client, "urban_policy")
    response = client.post(f"/projects/{pid}/chip", json={"pack_id": "made_up_chip"})
    assert response.status_code == 409
    assert "made_up_chip" in response.json()["message"]
