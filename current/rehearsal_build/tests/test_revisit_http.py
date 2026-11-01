"""Isolated HTTP contracts for the saved-decision revisit workflow."""
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import harness, packs, store
from app.api import create_app
from test_ready_now_briefing import StubAdapter, complete_card

ROOT = Path(__file__).resolve().parents[1]
NOW = "2031-05-10T12:00:00+00:00"
PAST = "2031-05-09T12:00:00Z"
FUTURE = "2032-05-10T12:00:00Z"


@pytest.fixture
def api(tmp_path, monkeypatch):
    db = tmp_path / "revisit.db"
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    monkeypatch.setenv("WB_DB", str(db))
    monkeypatch.setattr(harness, "ModelAdapter", StubAdapter)
    monkeypatch.setattr(packs, "INSTALLED_PACKS", {})
    monkeypatch.setattr(store, "now", lambda: NOW)
    with TestClient(create_app(data_dir=data)) as client:
        client.db_path, client.data_dir = str(db), data
        yield client, db, data


def project(client):
    response = client.post("/projects", json={"kind": "fx"})
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def decision(client, pid, pack="social_science", reason="Exact retained reason."):
    response = client.post(f"/projects/{pid}/questions", json={
        "text": "What does est_cost measure?", "pack_id": pack,
    })
    assert response.status_code == 200, response.text
    qid = response.json()["question_id"]
    response = client.post(f"/projects/{pid}/decisions", json={
        "question_id": qid, "verdict": "defer", "reason": reason,
    })
    assert response.status_code == 200, response.text
    return qid, response.json()["decision_id"]


def payload(did, date=PAST):
    return {
        "subject_kind": "decision", "subject_id": did, "recorded_date": date,
        "recorded_by": "researcher", "source": "researcher_revisit_form",
        "status": "recorded", "condition_type": "explicit_date", "note": "My saved note.",
    }


def snapshot(db):
    with sqlite3.connect(db) as connection:
        return tuple(connection.iterdump())


def test_http_restart_read_only_done_and_reschedule(api):
    client, db, data = api
    pid = project(client)
    qid, did = decision(client, pid)
    url = f"/projects/{pid}/revisit-conditions"
    response = client.post(url + "?pack_id=social_science", json=payload(did))
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["display_status"] == "due"
    assert row["decision_reason"] == "Exact retained reason."
    before = snapshot(db)
    for path in (url, url + "?pack_id=social_science",
                 f"/projects/{pid}/briefing", f"/projects/{pid}/briefing?pack_id=social_science",
                 "/briefing"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert "Exact retained reason." in response.text
        assert response.json()["model_calls"] == 0
    assert snapshot(db) == before
    with TestClient(create_app(data_dir=data)) as restarted:
        restored = restarted.get(url).json()["conditions"][0]
        assert restored["id"] == row["id"]
        assert restored["decision_reason"] == "Exact retained reason."
        assert restored["display_status"] == "due"
    action = url + f"/{row['id']}/actions?pack_id=social_science"
    response = client.post(action, json={
        "kind": "defer", "recorded_date": FUTURE, "recorded_by": "researcher",
        "source": "researcher_revisit_form", "note": None,
    })
    assert response.status_code == 200, response.text
    assert response.json()["display_status"] == "recorded"
    assert response.json()["note"] == "My saved note."
    assert all(item.get("id") != row["id"] for item in
               client.get(f"/projects/{pid}/briefing").json()["parts"]["ready_now"])
    response = client.post(action, json={
        "kind": "done", "recorded_by": "researcher", "source": "researcher_revisit_form",
    })
    assert response.status_code == 200
    assert response.json()["display_status"] == "done"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT verdict,reason FROM decisions WHERE id=?", (did,)).fetchone() == (
            "defer", "Exact retained reason.")
        assert connection.execute("SELECT id FROM questions WHERE id=?", (qid,)).fetchone()


def test_project_and_pack_isolation_supersession_and_deletion(api):
    client, db, _ = api
    pid, other = project(client), project(client)
    qa, da = decision(client, pid, reason="SOCIAL-ONLY-REASON")
    qb, dbid = decision(client, pid, "engineering_measurement", "ENGINEERING-ONLY-REASON")
    urls = f"/projects/{pid}/revisit-conditions"
    for pack, did in (("social_science", da), ("engineering_measurement", dbid)):
        response = client.post(urls + f"?pack_id={pack}", json=payload(did))
        assert response.status_code == 200, response.text
    assert len(client.get(urls).json()["conditions"]) == 2
    for pack, wanted, forbidden in (
        ("social_science", "SOCIAL-ONLY-REASON", "ENGINEERING-ONLY-REASON"),
        ("engineering_measurement", "ENGINEERING-ONLY-REASON", "SOCIAL-ONLY-REASON"),
    ):
        response = client.get(f"/projects/{pid}/briefing?pack_id={pack}")
        assert response.status_code == 200, response.text
        assert wanted in response.text and forbidden not in response.text
    overview = client.get(f"/projects/{pid}/briefing").json()
    assert len([item for item in overview["parts"]["ready_now"] if item.get("display_status") == "due"]) == 2
    assert "Ready to revisit" in overview["parts"]["suggested_next_action"][0]["text"]
    assert client.get(f"/projects/{other}/revisit-conditions").json()["conditions"] == []
    before = snapshot(db)
    assert client.post(f"/projects/{other}/revisit-conditions", json=payload(da)).status_code == 409
    assert client.post(urls + "?pack_id=engineering_measurement", json=payload(da)).status_code == 409
    assert snapshot(db) == before
    assert client.post(f"/projects/{pid}/decisions", json={
        "question_id": qa, "verdict": "reject", "reason": "A later explicit decision.",
    }).status_code == 200
    assert all(row["subject_id"] != da for row in client.get(urls).json()["conditions"])
    before_delete = client.get(urls).json()["conditions"][0]
    assert client.delete(f"/questions/{qb}").status_code == 200
    assert client.get(urls).json()["conditions"] == []
    assert "ENGINEERING-ONLY-REASON" not in client.get(f"/projects/{pid}/briefing").text
    assert client.post(urls + f"/{before_delete['id']}/actions", json={
        "kind": "done", "recorded_by": "researcher", "source": "researcher_revisit_form",
    }).status_code == 409


@pytest.mark.parametrize("patch", [
    {"subject_id": {}}, {"subject_id": []}, {"subject_id": None}, {"subject_id": ""},
    {"condition_type": None}, {"recorded_by": "secretary"}, {"source": ""},
    {"recorded_date": "0001-01-01T00:00:00+23:59"},
    {"recorded_date": "9999-12-31T23:59:59-23:59"},
    {"recorded_date": "2031-05-09T12:00:00"}, {"status": "due"},
])
def test_bad_http_fields_refuse_without_writes(api, patch):
    client, db, _ = api
    pid = project(client)
    _, did = decision(client, pid)
    before = snapshot(db)
    response = client.post(f"/projects/{pid}/revisit-conditions", json={**payload(did), **patch})
    assert response.status_code == 409, response.text
    assert snapshot(db) == before


def test_due_reminder_preserves_real_prepared_card_in_both_briefings(api):
    client, _, _ = api
    pid = project(client)
    _, decision_row, mark, _ = complete_card(client, pid)
    response = client.post(f"/projects/{pid}/revisit-conditions", json=payload(decision_row["decision_id"]))
    assert response.status_code == 200, response.text
    for suffix in ("", "?pack_id=social_science"):
        brief = client.get(f"/projects/{pid}/briefing{suffix}").json()
        assert any(item.get("job_id") == mark["job_id"] for item in brief["parts"]["ready_now"])
        assert any(item.get("display_status") == "due" for item in brief["parts"]["ready_now"])
