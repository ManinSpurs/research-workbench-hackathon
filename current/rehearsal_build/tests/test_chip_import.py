import asyncio
import json
import shutil
from pathlib import Path

import pytest

from app import chips
from app.chip_import import import_notebook
from app.demo_chip import demo_bytes
from app.harness import Harness
from app.offline import OfflineAdapter


def test_failed_import_cannot_rollback_concurrent_native_write(restored, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app import library
    h = restored
    importing = threading.Event()
    native_attempted = threading.Event()
    original = library.create_note
    db = h.db
    def trace(sql):
        if "INSERT INTO projects" in sql and "FX-PERMIT" in sql:
            native_attempted.set()
    db.set_trace_callback(trace)
    def fail_after_note(*args, **kwargs):
        original(*args, **kwargs)
        importing.set()
        assert native_attempted.wait(5), "native writer did not attempt its insert"
        raise RuntimeError("injected import failure")
    monkeypatch.setattr(library, "create_note", fail_after_note)
    specs = h.specs.copy()
    def native_write():
        assert importing.wait(5), "import did not reach the injected failure"
        return h.register_project("fx")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            failed = pool.submit(asyncio.run, import_notebook(
                h, "sample_permit_notebook", {"memory": "remember"}))
            native = pool.submit(native_write)
            with pytest.raises(RuntimeError, match="injected import failure"):
                failed.result(timeout=10)
            result = native.result(timeout=10)
        assert h.db is db
        assert h.project(result["project_id"])["kind"] == "fx"
        assert h.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
        assert h.specs == specs
    finally:
        db.set_trace_callback(None)


def test_clarification_structured_content_is_only_displayed_gap(restored):
    h = restored
    pid = h.register_project("fx")["project_id"]
    qid = asyncio.run(h.create_question(
        pid, "Private working text not displayed in this briefing item.", capture_only=True))["question_id"]
    cq = h.ask_clarification(qid, None, {
        "gap": "Choose the denominator.", "affected": "Private detail",
        "why_matters": "Private rationale", "branches": [],
        "independent_work": "Private work", "gap_kind": "preference"})
    for brief in [h._project_briefing(pid), h.briefing(pid),
                  next(r for r in h.launch_briefing()["rows"] if r["project_id"] == pid)]:
        item = next(w for w in brief["parts"]["waiting_on_you"] if w.get("cq_id") == cq)
        assert item["question"] == "Choose the denominator."
        assert item["question_id"] == qid
        for key in ("question", "reason"):
            assert item[key] is None or item[key] in item["text"]
        assert "Private" not in json.dumps(item)


def test_imported_checklist_is_next_action_not_waiting_question(restored):
    h = restored
    pack = "sample_permit_notebook"
    result = asyncio.run(import_notebook(h, pack, {"memory": "remember"}))
    pid = result["project_id"]
    assert result["checklist_count"] == 1
    for brief in [h.briefing(pid, pack), h._project_briefing(pid),
                  next(r for r in h.launch_briefing()["rows"] if r["project_id"] == pid)]:
        assert "Checklist:" not in json.dumps(brief["parts"]["waiting_on_you"])
        next_item = next(i for i in brief["parts"]["suggested_next_action"]
                         if "Confirm the denominator" in i["text"])
        assert next_item["kind"] == "next"
    assert len(h.pending_clarifications(pid, pack)["clarifications"]) == 1


@pytest.fixture
def restored(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", data)
    h = Harness(str(tmp_path / "test.db"), data, adapter=OfflineAdapter())
    chips.ChipStore(data).install_bytes(demo_bytes())
    yield h
    h.db.close()


@pytest.mark.parametrize("stage", ["project", "note", "pin", "question", "decision", "row_event", "event"])
def test_import_failure_is_atomic_and_retry_is_complete(restored, monkeypatch, stage):
    from app import library, store
    h = restored
    targets = {"project": (Harness, "register_project"), "note": (library, "create_note"),
               "pin": (Harness, "_pin_context"), "question": (Harness, "create_question"),
               "decision": (Harness, "decide"), "event": (store, "log_event"),
               "row_event": (store, "log_event")}
    obj, name = targets[stage]
    original = getattr(obj, name)
    specs = h.specs.copy()
    files = {p.relative_to(h.data) for p in h.data.rglob("*")}
    tables = [r[0] for r in h.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before = {t: h.db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}

    def fail(*args, **kwargs):
        result = original(*args, **kwargs)
        # Fail at the final marker, after all questions, decisions and notes exist.
        if ((stage == "event" and args[3] == "chip_import_completed") or
                (stage == "row_event" and args[3] == "chip_row_imported") or
                stage not in ("event", "row_event")):
            raise RuntimeError("injected import failure")
        return result

    async def fail_async(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected import failure")

    run = lambda: asyncio.run(import_notebook(h, "sample_permit_notebook", {"memory": "remember"}))
    with monkeypatch.context() as patch:
        patch.setattr(obj, name, fail_async if stage == "question" else fail)
        with pytest.raises(RuntimeError, match="injected import failure"):
            run()
    assert h.specs == specs
    assert {p.relative_to(h.data) for p in h.data.rglob("*")} == files
    assert {t: h.db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables} == before
    result = run()
    assert result["created"]
    assert h.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert len(h.pack_memory(result["project_id"], "sample_permit_notebook")["questions"]) == 3
    assert h.db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 2
    assert h.db.execute("SELECT count(*) FROM library_items").fetchone()[0] == 1


def test_record_identity_and_deleted_project(restored):
    h = restored
    cs = chips.ChipStore(h.data)
    pack = "sample_permit_notebook"
    run = lambda: asyncio.run(import_notebook(h, pack, {"memory": "remember"}))
    first = run()
    cs.write_conversation(pack, "Changed conversation, unchanged research record.")
    same = run()
    assert same["project_id"] == first["project_id"]
    assert same["sha256"] == first["sha256"]  # Provenance remains the imported archive.
    record = cs.read_record(pack)
    record["questions"][0]["text"] += " Updated population."
    cs.write_record(pack, record)
    changed = run()
    assert changed["created"] and changed["newer_record"]
    assert changed["project_id"] != first["project_id"]
    # No project-deletion API exists; exercise removal of the authoritative project
    # row, including a stale event, to ensure an import never reopens a deleted project.
    h.db.execute("DELETE FROM projects WHERE id=?", (changed["project_id"],))
    h.db.commit()
    fresh = run()
    assert fresh["created"] and fresh["project_id"] != changed["project_id"]
    assert len(h.pack_memory(fresh["project_id"], pack)["questions"]) == 3


@pytest.mark.parametrize("separate_connection", [False, True])
def test_concurrent_imports_use_one_project(restored, separate_connection):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    h = restored
    other = (Harness(h.db.execute("PRAGMA database_list").fetchone()[2], h.data,
                     adapter=OfflineAdapter()) if separate_connection else h)
    barrier = threading.Barrier(2)
    def run(owner):
        barrier.wait(timeout=10)
        return asyncio.run(import_notebook(owner, "sample_permit_notebook", {"memory": "remember"}))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [h, other]))
    finally:
        if separate_connection:
            other.db.close()
    assert len({r["project_id"] for r in results}) == 1
    assert sorted(r["created"] for r in results) == [False, True]
    assert h.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_import_briefing_structures_match_native_reply_target(restored):
    h = restored
    pack = "sample_permit_notebook"
    pid = asyncio.run(import_notebook(h, pack, {"memory": "remember"}))["project_id"]
    pending = h.pending_clarifications(pid, pack)["clarifications"]
    assert len(pending) == 1
    target = pending[0]
    briefs = [h.briefing(pid, pack), h._project_briefing(pid),
              next(row for row in h.launch_briefing()["rows"] if row["project_id"] == pid)]
    for brief in briefs:
        parts = brief["parts"]
        waiting = next(w for w in parts["waiting_on_you"]
                       if w["question_id"] == target["question_id"])
        assert waiting["kind"] == "waiting"
        assert waiting["state"] == "clarification_pending"
        assert waiting["question"] == target["clarification"]["gap"]
        assert parts["suggested_next_action"][0]["kind"] == "next"
        assert parts["suggested_next_action"][0]["question_id"] == target["question_id"]
        for decision in parts["where_you_left_off"]:
            assert decision["kind"] == "decision" and decision["verdict"] == "adopt"
            assert decision["reason"]
            assert "?." not in decision["text"] and '?".' not in decision["text"]
    response = asyncio.run(h.respond(target["cq_id"], "later", project_id=pid))
    assert response


def test_import_notebook(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", data)
    h = Harness(str(tmp_path / "test.db"), data, adapter=OfflineAdapter())
    try:
        cs = chips.ChipStore(data)
        cs.install_bytes(demo_bytes())
        pack = "sample_permit_notebook"
        own = h.register_project("fx", pack_id=pack)["project_id"]
        run = lambda: asyncio.run(import_notebook(h, pack, {"memory": "remember"}))
        result = run()
        assert result["created"] is True
        pid = result["project_id"]
        assert pid != own
        assert not h.pack_memory(own, pack)["questions"]
        assert run()["project_id"] == pid
        assert run()["created"] is False
        assert len(h.pack_memory(pid, pack)["questions"]) == 3
        briefing = h.briefing(pid, pack)["parts"]
        assert "Residential permits" in json.dumps(briefing["where_you_left_off"])
        assert "withdrawn permits" in json.dumps(briefing["waiting_on_you"])
        assert "withdrawn permits" in json.dumps(briefing["suggested_next_action"])
        assert "imported from chip" in h.db.execute(
            "SELECT body_text FROM library_items WHERE project_id=?", (pid,)).fetchone()[0]
        assert "sha256" in result["provenance"]
        qid = h.pack_memory(pid, pack)["questions"][0]["id"]
        h.delete_question(qid)
        assert len(h.pack_memory(pid, pack)["questions"]) == 2
        assert run()["created"] is False  # Never resurrect a deleted question.
        cs.delete(pack)
        from app.harness import Conflict
        with pytest.raises(Conflict):
            h.pack_memory(pid, pack)
        cs.install_bytes(demo_bytes())
        assert len(h.pack_memory(pid, pack)["questions"]) == 2
        with pytest.raises(chips.Invalid):
            asyncio.run(import_notebook(h, pack, {}))
        bad = cs.read_record(pack)
        bad["questions"][0]["text"] = "x" * 20001
        cs.write_record(pack, bad)
        with pytest.raises(chips.Invalid):
            run()
        bad["questions"][0]["text"] = "ordinary text"
        bad["questions"][0]["manifest_sha256"] = "hostile mismatch"
        cs.write_record(pack, bad)
        with pytest.raises(chips.Invalid):
            run()
    finally:
        h.db.close()


def test_demo_route_and_packaging(tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    from tools.build_review_dmg import stage_app_sources
    data = tmp_path / "data"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", data)
    with TestClient(create_app(str(tmp_path / "route.db"), data)) as c:
        import base64
        sample = c.get("/chip-samples").json()["samples"][0]
        raw = base64.b64decode(sample["base64"])
        assert chips.read_chip_record(raw).questions
        assert "SAMPLE DATA" in chips.read_chip_conversation(raw)
        chips.ChipStore(data).install_bytes(raw)
        # install through the public endpoint registers the pack for this process too.
        response = c.post("/chips/sample_permit_notebook/import", json={"memory": "remember"})
        assert response.status_code == 200, response.text
        assert response.json()["model_calls"] == 0
    stage = tmp_path / "stage"
    stage.mkdir()
    stage_app_sources(stage)
    demo = stage / "chips/builtin/sample_permit_notebook.chip"
    assert demo.read_bytes() == raw
