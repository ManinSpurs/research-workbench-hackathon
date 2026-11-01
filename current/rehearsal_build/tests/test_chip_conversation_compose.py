"""conversation.txt content: the secretary talk, the decisions and the evidence as text.

A chip's ``conversation.txt`` is meant to be readable in any editor, so these tests pin the
shape of what ``app/chip_conversation.py`` composes from the app's own records: the turn
texts, each question's latest decision and its real reason, the evidence excerpts and their
locations, a fixed order, and the byte cap the chip reader enforces. Nothing here is a model
call, and a reason that was never recorded must read ``Reason not recorded`` instead of a
plausible invention.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest

from app import chip_conversation, chips, secretary_log, store
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)

MINE = "Which cost field should I use for the 2023 estimate?"
HER_ANSWER = "Use DD-2023 est_cost; it is the applicant-reported figure."
DECIDED = "Does est_cost include the applicant's own labour?"
REASONED = "Adopt est_cost: the definition names the applicant-reported figure."
EVIDENCE_EXCERPT = "est_cost means the cost reported by the applicant at filing."
EVIDENCE_LOCATION = "data dictionary v2023, table 4, p.12"
EVIDENCE_SOURCE = "dd-2023"


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def add_question(h, pid, text):
    qid = store.new_id("q")
    h.db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?)",
                 (qid, pid, text, None, text, "done", "remember", "idle", store.now()))
    h.db.commit()
    return qid


def record(h, pid, role, text, **over):
    body = {"role": role, "body": {"text": text}}
    body.update(over)
    return secretary_log.record_turn(h, pid, body)


def full_project(h):
    """One project holding a turn pair, a reasoned decision and one evidence row."""
    pid = seed(h)
    record(h, pid, "researcher", MINE)
    record(h, pid, "secretary", HER_ANSWER)
    qid = add_question(h, pid, DECIDED)
    h.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                 (store.new_id("d"), pid, qid, "adopt", REASONED, store.now()))
    h.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",
                 (store.new_id("e"), qid, pid, "ev-1", EVIDENCE_SOURCE,
                  EVIDENCE_EXCERPT, EVIDENCE_LOCATION, store.now()))
    h.db.commit()
    return pid, qid


# ------------------------------------------------------------------ content
def test_composed_log_carries_the_talk_the_decision_and_the_evidence(h):
    pid, _qid = full_project(h)
    text = chip_conversation.compose(h, pid)
    for part in (MINE, HER_ANSWER, "researcher", "secretary", "adopt", REASONED,
                 EVIDENCE_EXCERPT, EVIDENCE_LOCATION, EVIDENCE_SOURCE):
        assert part in text, part
    # The three sections are present and in one fixed order.
    assert text.index("CONVERSATION") < text.index("DECISIONS") < text.index("EVIDENCE")
    assert "Chip:" in text and "Project:" in text


def test_a_decision_without_a_reason_says_so_and_never_invents_one(h):
    pid = seed(h)
    qid = add_question(h, pid, "Can permit issuance stand in for construction start?")
    h.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                 (store.new_id("d"), pid, qid, "defer", None, store.now()))
    h.db.commit()
    text = chip_conversation.compose(h, pid)
    assert "reason: Reason not recorded" in text
    assert "verdict: defer" in text


def test_a_question_without_a_decision_is_absent_from_the_decisions_section(h):
    pid = seed(h)
    add_question(h, pid, "An open question with no verdict")
    text = chip_conversation.compose(h, pid)
    assert "No decision has been recorded" in text
    assert "verdict:" not in text


def test_composing_is_deterministic(h):
    pid, _qid = full_project(h)
    assert chip_conversation.compose(h, pid) == chip_conversation.compose(h, pid)


def test_an_empty_project_still_says_what_it_is(h):
    pid = seed(h)
    text = chip_conversation.compose(h, pid)
    assert chip_conversation.NOTHING_RECORDED in text
    assert "Chip:" in text and "Project:" in text


def test_an_unknown_project_is_a_key_error(h):
    with pytest.raises(KeyError):
        chip_conversation.compose(h, "proj-missing")


def test_discarded_turns_are_counted_not_printed_and_go_when_deleted(h):
    """Setting a turn aside is the person's own choice, so the log obeys it and says so."""
    pid = seed(h)
    turn = record(h, pid, "secretary", "A turn the person set aside")
    secretary_log.update_turn(h, turn["id"], {"status": "discarded"})
    text = chip_conversation.compose(h, pid)
    assert "A turn the person set aside" not in text
    assert "1 turn(s) were set aside" in text
    secretary_log.delete_turn(h, turn["id"])
    assert "set aside" not in chip_conversation.compose(h, pid)


# ------------------------------------------------------------------ the cap
def test_a_long_log_fits_the_chip_cap_and_says_it_was_truncated(h, monkeypatch):
    pid = seed(h)
    # Many turns at the per-turn ceiling: the log has to be cut, not the records refused.
    roomy = secretary_log.MAX_BODY_CHARS - 200
    for index in range(6):
        record(h, pid, "researcher", f"turn {index} " + "x" * roomy)
    # The shipped conversation cap is 100 MB, far above what six turns produce, so the
    # truncation rule is pinned against a lowered cap instead of being left untested.
    monkeypatch.setattr(chips, "MAX_CONVERSATION_BYTES", 4096)
    text = chip_conversation.compose(h, pid)
    raw = text.encode("utf-8")
    assert len(raw) <= chips.MAX_CONVERSATION_BYTES
    assert "truncated" in text
    assert f"{chips.size_label(chips.MAX_CONVERSATION_BYTES)}" in text


def test_a_short_log_is_never_truncated(h):
    pid, _qid = full_project(h)
    text = chip_conversation.compose(h, pid)
    assert "truncated" not in text


# ------------------------------------------------------------------ into the chip
def test_composing_then_storing_lands_in_the_chip_byte_for_byte(h, tmp_path):
    pid, _qid = full_project(h)
    payload = {"pack_id": "conv_live", "version": "1.0.0", "title": "Conversation Live",
               "instructions": "Probe chip.", "required_context": ["research_question"],
               "checks": ["estimand_preserved"], "allowed_tools": []}
    chip_store = chips.ChipStore(tmp_path / "data")
    chip_store.install_bytes(chips.build_chip(payload))

    written = chip_conversation.compose_and_store(h, pid, "conv_live", chip_store)
    # The composed text names the chip it was written into, so compare against that.
    text = chip_conversation.compose(h, pid, "conv_live")
    assert written["text"] == text
    assert written["bytes"] == len(text.encode("utf-8"))
    assert written["model_calls"] == 0
    # The file on disk holds exactly the composed text, not a re-encoded copy of it.
    assert chip_store.read_conversation("conv_live")["text"] == text
    assert chips.read_chip_conversation(chip_store._target("conv_live").read_bytes()) == text


def test_composing_into_a_builtin_chip_is_refused(h, tmp_path):
    pid = seed(h)
    chip_store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(chips.Invalid, match="built-in chip"):
        chip_conversation.compose_and_store(h, pid, "social_science", chip_store)


# ------------------------------------------------------- taking the chip out
def _install(tmp_path, pack_id="conv_out"):
    chip_store = chips.ChipStore(tmp_path / "data")
    chip_store.install_bytes(chips.build_chip(
        {"pack_id": pack_id, "version": "1.0.0", "title": "Conversation Out",
         "instructions": "Probe chip.", "required_context": ["research_question"]}))
    return chip_store


def test_taking_a_chip_out_writes_its_log_first(h, tmp_path):
    """The file leaves the app, so its notebook has to be complete before it goes."""
    chip_store = _install(tmp_path)
    pid = seed(h, kind="fx")
    h.db.execute("UPDATE projects SET pack_id='conv_out' WHERE id=?", (pid,))
    h.db.commit()
    record(h, pid, "researcher", "A line that must be inside the exported file")
    # Nothing was written yet: the chip is still without a conversation.
    assert chip_store.read_conversation("conv_out")["text"] == ""

    written = chip_conversation.store_on_export(h, "conv_out", chip_store)

    assert len(written) == 1
    assert written[0]["stored"] is True
    assert written[0]["model_calls"] == 0
    assert "A line that must be inside the exported file" in \
        chip_store.read_conversation("conv_out")["text"]


def test_two_projects_under_one_chip_are_not_written_over_each_other(h, tmp_path):
    """One chip holds one conversation.txt: picking a project for the person would lose one."""
    chip_store = _install(tmp_path)
    ids = []
    for index in range(2):
        pid = seed(h, kind="fx")
        h.db.execute("UPDATE projects SET pack_id='conv_out' WHERE id=?", (pid,))
        record(h, pid, "researcher", f"project {index} line")
        ids.append(pid)
    h.db.commit()

    written = chip_conversation.store_on_export(h, "conv_out", chip_store)

    assert [w["project_id"] for w in written] == ids
    assert all(w["stored"] is False for w in written)
    # Nothing was written: the file holds no log rather than one project's log.
    assert chip_store.read_conversation("conv_out")["text"] == ""


def test_a_chip_no_project_works_under_exports_without_a_log(h, tmp_path):
    chip_store = _install(tmp_path)
    assert chip_conversation.store_on_export(h, "conv_out", chip_store) == []


def test_export_fits_remaining_total_and_preserves_other_members(h, tmp_path, monkeypatch):
    import io
    import zipfile

    chip_store = _install(tmp_path)
    chip_store.write_record("conv_out", {"harness": [{"key": "keep", "value": "record"}]})
    target = chip_store._target("conv_out")
    with zipfile.ZipFile(io.BytesIO(target.read_bytes())) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    used = sum(map(len, original.values()))
    monkeypatch.setattr(chips, "MAX_TOTAL_BYTES", used + 512)
    pid = seed(h)
    h.db.execute("UPDATE projects SET pack_id='conv_out' WHERE id=?", (pid,))
    h.db.commit()
    record(h, pid, "researcher", "long history " * 500)
    result = chip_conversation.store_on_export(h, "conv_out", chip_store)[0]
    assert result["bytes"] <= 512
    assert "truncated" in result["text"]
    with zipfile.ZipFile(io.BytesIO(target.read_bytes())) as archive:
        assert sum(info.file_size for info in archive.infolist()) <= used + 512
        for name, raw in original.items():
            assert archive.read(name) == raw
    before = target.read_bytes()
    chip_store.write_conversation("conv_out", "")
    before = target.read_bytes()
    monkeypatch.setattr(chips, "MAX_TOTAL_BYTES", used + 10)
    with pytest.raises(chips.Invalid, match="honest"):
        chip_conversation.compose_and_store(h, pid, "conv_out", chip_store)
    assert target.read_bytes() == before
